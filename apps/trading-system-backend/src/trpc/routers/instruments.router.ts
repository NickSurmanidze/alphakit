import { TRPCError } from '@trpc/server';
import { z } from 'zod';

import { getConnector } from '../../connectors/registry.js';
import { deleteAllCandles, deleteCandlesForResolution, getLatestCloses } from '../../modules/candles/candleStore.js';
import {
  addInstrumentResolution,
  createInstrument,
  deleteInstrument,
  getInstrumentById,
  instrumentExists,
  listInstruments,
  resetInstrumentResolution,
  updateInstrumentDescription,
  updateInstrumentFlags,
  updateResolutionCoverage
} from '../../modules/instruments/instruments.repository.js';
import {
  BaseResolution,
  finestResolution,
  InstrumentSource,
  toPublicInstrument
} from '../../modules/instruments/instruments.types.js';
import { BACKFILL_JOB_OPTS } from '../../queue/queue-utils.js';
import { addQueueJob } from '../../queue/queues.js';
import { historicalQueueFor, liveQueueFor, QueueJobNames } from '../../queue/types.js';
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

/** Marks one resolution `inProgress` and enqueues its historical backfill job -- shared by fresh
 * registration, `resetResolution`, and `addResolution` so all three compute `from` the same way:
 * full history since the source's earliest known date if requested and known, else a fixed
 * trailing window. */
const startBackfill = async (params: {
  instrumentId: string;
  source: InstrumentSource;
  resolution: BaseResolution;
  earliestAvailableDate: Date | null;
  backfillDays: number;
  backfillFullHistory: boolean;
}): Promise<void> => {
  const { instrumentId, source, resolution, earliestAvailableDate, backfillDays, backfillFullHistory } = params;
  const to = new Date();
  const from =
    backfillFullHistory && earliestAvailableDate
      ? earliestAvailableDate
      : new Date(to.getTime() - backfillDays * 24 * 60 * 60_000);

  await updateResolutionCoverage(instrumentId, resolution, { status: 'inProgress' });
  await addQueueJob({
    queueName: historicalQueueFor(source),
    job: {
      name: QueueJobNames.backfillHistoricalCandles,
      data: { instrumentId, resolution, from: from.toISOString(), to: to.toISOString() },
      opts: BACKFILL_JOB_OPTS
    }
  });
};

const DEFAULT_BACKFILL_DAYS = 30;

const sourceSchema = z.enum(['binance', 'yahoo', 'ib']);
const assetClassSchema = z.enum(['spot', 'perpetual', 'equity', 'future', 'index', 'forex']);
const baseResolutionSchema = z.enum(['1_minute', '5_minute', '1_hour', '1_day']);
const DEFAULT_RESOLUTIONS: BaseResolution[] = ['1_hour'];

export const instrumentsRouter = router({
  list: protectedProcedure.query(async () => {
    const instruments = await listInstruments();
    // Skip instruments with no collected resolutions yet (finestResolution throws on that) --
    // shouldn't happen for anything registered through this router, but a list endpoint
    // shouldn't fail entirely over one bad row's missing price either.
    const latestCloses = await getLatestCloses(
      instruments
        .filter(doc => Object.keys(doc.collectedResolutions).length > 0)
        .map(doc => ({ instrumentId: doc._id.toHexString(), resolution: finestResolution(doc) }))
    );
    return instruments.map(doc => ({
      ...toPublicInstrument(doc),
      latestPrice: latestCloses.get(doc._id.toHexString()) ?? null
    }));
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
        description: z.string().optional(),
        baseCurrency: z.string().optional(),
        quoteCurrency: z.string().optional(),
        // Every resolution to explicitly collect for this instrument, e.g. ['5_minute', '1_day']
        // for an IB future where daily depth vastly exceeds intraday depth and deriving one from
        // the other would be wrong in either direction.
        resolutions: z.array(baseResolutionSchema).min(1).default(DEFAULT_RESOLUTIONS),
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

      const { resolutions, backfillDays, backfillFullHistory, ...instrumentInput } = input;

      const earliestDates = new Map<BaseResolution, Date | null>();
      for (const resolution of resolutions) {
        earliestDates.set(resolution, await lookupEarliestAvailableDate(input.source, input.sourceSymbol, resolution));
      }

      const instrument = await createInstrument({
        ...instrumentInput,
        resolutions: Object.fromEntries(
          resolutions.map(resolution => [resolution, { earliestAvailableDate: earliestDates.get(resolution) ?? null }])
        )
      });
      const instrumentId = instrument._id.toHexString();

      for (const resolution of resolutions) {
        await startBackfill({
          instrumentId,
          source: input.source,
          resolution,
          earliestAvailableDate: earliestDates.get(resolution) ?? null,
          backfillDays,
          backfillFullHistory
        });
      }

      const created = await getInstrumentById(instrumentId);
      return created ? toPublicInstrument(created) : toPublicInstrument(instrument);
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

  updateDescription: protectedProcedure
    .input(z.object({ id: z.string(), description: z.string() }))
    .mutation(async ({ input }) => {
      await updateInstrumentDescription(input.id, input.description);
      const instrument = await getInstrumentById(input.id);
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

  /** Wipes and re-backfills just one of an instrument's already-collected resolutions -- e.g.
   * after fixing a connector bug, or to force a clean re-download. Leaves every other collected
   * resolution (and anything derived from them) untouched. Replaces the old whole-instrument
   * `updateResolution`, which used to wipe *all* candle data because there was only ever one
   * resolution to wipe. */
  resetResolution: protectedProcedure
    .input(z.object({ id: z.string(), resolution: baseResolutionSchema }))
    .mutation(async ({ input }) => {
      const instrument = await getInstrumentById(input.id);
      if (!instrument) {
        throw new TRPCError({ code: 'NOT_FOUND', message: 'Instrument not found' });
      }
      const coverage = instrument.collectedResolutions[input.resolution];
      if (!coverage) {
        throw new TRPCError({
          code: 'BAD_REQUEST',
          message: `Instrument does not collect ${input.resolution}`
        });
      }

      await deleteCandlesForResolution(input.id, input.resolution);
      await resetInstrumentResolution(input.id, input.resolution);
      await startBackfill({
        instrumentId: input.id,
        source: instrument.source,
        resolution: input.resolution,
        earliestAvailableDate: coverage.earliestAvailableDate,
        backfillDays: DEFAULT_BACKFILL_DAYS,
        backfillFullHistory: coverage.earliestAvailableDate !== null
      });

      const updated = await getInstrumentById(input.id);
      return updated ? toPublicInstrument(updated) : null;
    }),

  /** Starts collecting a new resolution on an already-registered instrument (e.g. adding
   * '5_minute' to an IB future that started out daily-only), without touching its existing
   * collected resolutions. */
  addResolution: protectedProcedure
    .input(
      z.object({ id: z.string(), resolution: baseResolutionSchema, backfillFullHistory: z.boolean().default(false) })
    )
    .mutation(async ({ input }) => {
      const instrument = await getInstrumentById(input.id);
      if (!instrument) {
        throw new TRPCError({ code: 'NOT_FOUND', message: 'Instrument not found' });
      }
      if (instrument.collectedResolutions[input.resolution]) {
        return toPublicInstrument(instrument);
      }

      const earliestAvailableDate = await lookupEarliestAvailableDate(
        instrument.source,
        instrument.sourceSymbol,
        input.resolution
      );

      await addInstrumentResolution(input.id, input.resolution, earliestAvailableDate);
      await startBackfill({
        instrumentId: input.id,
        source: instrument.source,
        resolution: input.resolution,
        earliestAvailableDate,
        backfillDays: DEFAULT_BACKFILL_DAYS,
        backfillFullHistory: input.backfillFullHistory
      });

      const updated = await getInstrumentById(input.id);
      return updated ? toPublicInstrument(updated) : null;
    }),

  refresh: protectedProcedure.input(z.object({ id: z.string() })).mutation(async ({ input }) => {
    const instrument = await getInstrumentById(input.id);
    if (!instrument) {
      throw new TRPCError({ code: 'NOT_FOUND', message: 'Instrument not found' });
    }

    const queueName = liveQueueFor(instrument.source);
    for (const resolution of Object.keys(instrument.collectedResolutions) as BaseResolution[]) {
      await addQueueJob({
        queueName,
        job: { name: QueueJobNames.refreshLatestCandles, data: { instrumentId: input.id, resolution } }
      });
    }
    return { success: true };
  })
});
