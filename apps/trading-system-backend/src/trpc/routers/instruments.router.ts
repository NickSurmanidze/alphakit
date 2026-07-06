import { TRPCError } from '@trpc/server';
import { z } from 'zod';

import { getConnector } from '../../connectors/registry.js';
import { deleteAllCandles } from '../../modules/candles/candleStore.js';
import {
  createInstrument,
  deleteInstrument,
  getInstrumentById,
  instrumentExists,
  listInstruments,
  updateInstrumentBaseResolution,
  updateInstrumentCoverage,
  updateInstrumentFlags
} from '../../modules/instruments/instruments.repository.js';
import { BaseResolution, InstrumentSource, toPublicInstrument } from '../../modules/instruments/instruments.types.js';
import { addQueueJob } from '../../queue/queues.js';
import { QueueJobNames, Queues } from '../../queue/types.js';
import { protectedProcedure, router } from '../trpc.js';

/** Looks up the earliest date the source actually has data for a symbol *at one resolution* --
 * only the resolution actually being registered/switched to, never every resolution up front.
 * (This used to probe all three base resolutions on every registration, so e.g. picking "1 day"
 * for an old Yahoo equity would still probe "1 hour" behind the scenes, hit Yahoo's ~730-day
 * intraday limit, and fail the whole registration over a resolution nobody asked for.)
 * Best-effort: any failure is caught and treated as "unknown" rather than failing the caller. */
const lookupEarliestAvailableDate = async (
  source: InstrumentSource,
  sourceSymbol: string,
  resolution: BaseResolution
): Promise<Date | null> => {
  try {
    return await getConnector(source).getEarliestAvailableDate(sourceSymbol, resolution);
  } catch (err) {
    console.warn(`Could not determine earliest available date for ${source}:${sourceSymbol} @ ${resolution}:`, err);
    return null;
  }
};

const DEFAULT_BACKFILL_DAYS = 30;

const sourceSchema = z.enum(['binance', 'yahoo']);
const assetClassSchema = z.enum(['spot', 'perpetual', 'equity', 'future', 'index', 'forex']);
const baseResolutionSchema = z.enum(['1_minute', '1_hour', '1_day']);
const DEFAULT_BASE_RESOLUTION = '1_hour';

export const instrumentsRouter = router({
  list: protectedProcedure.query(async () => {
    const instruments = await listInstruments();
    return instruments.map(toPublicInstrument);
  }),

  searchSymbols: protectedProcedure
    .input(z.object({ source: sourceSchema, query: z.string().min(1) }))
    .query(({ input }) => getConnector(input.source).searchSymbols(input.query)),

  register: protectedProcedure
    .input(
      z.object({
        source: sourceSchema,
        assetClass: assetClassSchema,
        sourceSymbol: z.string().min(1),
        displaySymbol: z.string().min(1),
        baseCurrency: z.string().optional(),
        quoteCurrency: z.string().optional(),
        baseResolution: baseResolutionSchema.default(DEFAULT_BASE_RESOLUTION),
        calendarVenue: z.string().min(1),
        cacheHistoricalPrices: z.boolean().default(true),
        cacheLivePrices: z.boolean().default(true),
        backfillDays: z.number().int().positive().max(3650).default(DEFAULT_BACKFILL_DAYS),
        backfillFullHistory: z.boolean().default(false)
      })
    )
    .mutation(async ({ input }) => {
      // Cheap existence check before the earliest-date lookups below, which cost up to a few
      // real requests per resolution to the source -- no point paying for those just to have
      // createInstrument's own check reject the registration as a duplicate afterwards.
      if (await instrumentExists(input.source, input.sourceSymbol)) {
        throw new TRPCError({
          code: 'CONFLICT',
          message: `Instrument ${input.source}:${input.sourceSymbol} already exists`
        });
      }

      const { backfillDays, backfillFullHistory, ...instrumentInput } = input;
      const earliest = await lookupEarliestAvailableDate(input.source, input.sourceSymbol, input.baseResolution);
      const earliestAvailableDates: Partial<Record<BaseResolution, Date>> = earliest
        ? { [input.baseResolution]: earliest }
        : {};
      const instrument = await createInstrument({ ...instrumentInput, earliestAvailableDates });
      const instrumentId = instrument._id.toHexString();

      const to = new Date();
      let from = new Date(to.getTime() - backfillDays * 24 * 60 * 60_000);

      if (backfillFullHistory && earliest) {
        from = earliest;
        // If the source couldn't tell us, silently fall back to the backfillDays window
        // computed above rather than failing the whole registration.
      }

      await updateInstrumentCoverage(instrumentId, { status: 'inProgress' });
      await addQueueJob({
        queueName: Queues.MARKET_DATA_HISTORICAL,
        job: {
          name: QueueJobNames.backfillHistoricalCandles,
          data: {
            instrumentId,
            from: from.toISOString(),
            to: to.toISOString()
          }
        }
      });

      return toPublicInstrument(instrument);
    }),

  updateFlags: protectedProcedure
    .input(
      z.object({
        id: z.string(),
        cacheHistoricalPrices: z.boolean().optional(),
        cacheLivePrices: z.boolean().optional()
      })
    )
    .mutation(async ({ input }) => {
      const { id, ...updates } = input;
      await updateInstrumentFlags(id, updates);
      const instrument = await getInstrumentById(id);
      return instrument ? toPublicInstrument(instrument) : null;
    }),

  delete: protectedProcedure.input(z.object({ id: z.string() })).mutation(async ({ input }) => {
    // Candle data first: if the instrument doc were deleted first and this failed partway
    // through, the rows would become permanently unreachable (no instrument to look up their
    // resolution/coverage from) rather than just retryable.
    await deleteAllCandles(input.id);
    await deleteInstrument(input.id);
    return { success: true };
  }),

  updateResolution: protectedProcedure
    .input(z.object({ id: z.string(), baseResolution: baseResolutionSchema }))
    .mutation(async ({ input }) => {
      const instrument = await getInstrumentById(input.id);
      if (!instrument) {
        throw new TRPCError({ code: 'NOT_FOUND', message: 'Instrument not found' });
      }
      if (instrument.baseResolution === input.baseResolution) {
        return toPublicInstrument(instrument);
      }

      // Resolve the new range *before* deleting anything: if the connector lookup throws (e.g.
      // a network blip), we bail out here with the instrument's existing data still intact,
      // instead of wiping it and only then discovering we can't say what to re-backfill.
      let earliest = instrument.earliestAvailableDates?.[input.baseResolution] ?? null;
      if (!earliest) {
        earliest = await lookupEarliestAvailableDate(instrument.source, instrument.sourceSymbol, input.baseResolution);
      }

      // A different base resolution has a completely different set of rows at every derived
      // resolution too (e.g. switching 1_day -> 1_minute means the old daily-derived rows were
      // built from daily source data, not minute data) -- wipe everything and re-derive from
      // scratch rather than trying to reconcile old and new.
      await deleteAllCandles(input.id);
      await updateInstrumentBaseResolution(input.id, input.baseResolution, earliest);

      const to = new Date();
      const from = earliest ?? new Date(to.getTime() - DEFAULT_BACKFILL_DAYS * 24 * 60 * 60_000);
      await addQueueJob({
        queueName: Queues.MARKET_DATA_HISTORICAL,
        job: {
          name: QueueJobNames.backfillHistoricalCandles,
          data: { instrumentId: input.id, from: from.toISOString(), to: to.toISOString() }
        }
      });

      const updated = await getInstrumentById(input.id);
      return updated ? toPublicInstrument(updated) : null;
    }),

  refresh: protectedProcedure.input(z.object({ id: z.string() })).mutation(async ({ input }) => {
    const instrument = await getInstrumentById(input.id);
    if (!instrument) {
      throw new TRPCError({ code: 'NOT_FOUND', message: 'Instrument not found' });
    }

    await addQueueJob({
      queueName: Queues.MARKET_DATA_LIVE,
      job: { name: QueueJobNames.refreshLatestCandles, data: { instrumentId: input.id } }
    });
    return { success: true };
  })
});
