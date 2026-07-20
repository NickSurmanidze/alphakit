import { getConnector } from '../src/connectors/registry.js';
import { closeMongoClient } from '../src/db/mongo.js';
import { closeRedis } from '../src/db/redis.js';
import { createInstrument, instrumentExists, updateResolutionCoverage } from '../src/modules/instruments/instruments.repository.js';
import { BaseResolution } from '../src/modules/instruments/instruments.types.js';
import { BACKFILL_JOB_OPTS } from '../src/queue/queue-utils.js';
import { addQueueJob, closeQueues } from '../src/queue/queues.js';
import { historicalQueueFor, QueueJobNames } from '../src/queue/types.js';

const SOURCE = 'databento' as const;
const SOURCE_SYMBOL = 'MES.c.0'; // Front-month continuous contract, ranked by calendar roll.
const DISPLAY_SYMBOL = 'MES';
const RESOLUTIONS: BaseResolution[] = ['5_minute', '1_hour', '1_day'];
const DEFAULT_BACKFILL_DAYS = 30;

// Mirrors instruments.router.ts's `register` mutation (create instrument doc, then kick off one
// backfill job per collected resolution) since a script can't call a protected tRPC procedure
// directly. Deliberately does NOT default to full history the way the UI's "backfill full
// history" checkbox can: Databento's Historical API bills per byte scanned, and MES's continuous
// contract goes back to GLBX.MDP3's dataset start (~2010) -- a full-history 5-minute (and
// underlying 1-minute, since 5-minute is derived from it) backfill over 15+ years is a real,
// avoidable cost. Defaults to the same trailing 30-day window the router itself defaults new
// registrations to; pass --backfill-days to widen it deliberately.
const parseArgs = () => {
  const args = process.argv.slice(2);
  const index = args.indexOf('--backfill-days');
  const backfillDays = index !== -1 ? Number(args[index + 1]) : DEFAULT_BACKFILL_DAYS;
  if (!Number.isInteger(backfillDays) || backfillDays <= 0) {
    console.error('Usage: pnpm seed:mes -- [--backfill-days <positive integer>]');
    process.exit(1);
  }
  return { backfillDays };
};

const main = async () => {
  const { backfillDays } = parseArgs();

  if (await instrumentExists(SOURCE, SOURCE_SYMBOL)) {
    console.log(`Instrument ${SOURCE}:${SOURCE_SYMBOL} already exists, nothing to do.`);
    return;
  }

  const connector = getConnector(SOURCE);
  const earliestDates = new Map<BaseResolution, Date | null>();
  for (const resolution of RESOLUTIONS) {
    const earliestAvailableDate = await connector.getEarliestAvailableDate(SOURCE_SYMBOL, resolution).catch(err => {
      console.warn(`Could not determine earliest available date for ${resolution}:`, err);
      return null;
    });
    earliestDates.set(resolution, earliestAvailableDate);
  }

  const instrument = await createInstrument({
    source: SOURCE,
    assetClass: 'future',
    sourceSymbol: SOURCE_SYMBOL,
    displaySymbol: DISPLAY_SYMBOL,
    description: 'Micro E-mini S&P 500 Futures (continuous front month)',
    calendarVenue: 'FUTURES_NEAR_CONTINUOUS',
    cacheHistoricalPrices: true,
    cacheLivePrices: true,
    resolutions: Object.fromEntries(
      RESOLUTIONS.map(resolution => [resolution, { earliestAvailableDate: earliestDates.get(resolution) ?? null }])
    )
  });
  const instrumentId = instrument._id.toHexString();

  const to = new Date();
  const from = new Date(to.getTime() - backfillDays * 24 * 60 * 60_000);

  for (const resolution of RESOLUTIONS) {
    await updateResolutionCoverage(instrumentId, resolution, { status: 'inProgress' });
    await addQueueJob({
      queueName: historicalQueueFor(SOURCE),
      job: {
        name: QueueJobNames.backfillHistoricalCandles,
        data: { instrumentId, resolution, from: from.toISOString(), to: to.toISOString() },
        opts: BACKFILL_JOB_OPTS
      }
    });
  }

  console.log(
    `Registered ${DISPLAY_SYMBOL} (${instrumentId}) collecting ${RESOLUTIONS.join(', ')} from Databento, ` +
      `backfilling the last ${backfillDays} days.`
  );
};

main()
  .catch(err => {
    console.error(err);
    process.exitCode = 1;
  })
  .finally(async () => {
    await closeQueues();
    await Promise.all([closeRedis(), closeMongoClient()]);
  });
