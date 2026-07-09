import { findCandleGaps } from '../modules/candles/gapDetection.js';
import { GAP_CHECK_LOOKBACK_DAYS } from '../modules/candles/resolutions.js';
import { BaseResolution } from '../modules/instruments/instruments.types.js';
import { getInstrumentById } from '../modules/instruments/instruments.repository.js';
import { BACKFILL_JOB_OPTS } from '../queue/queue-utils.js';
import { addQueueJob, queues } from '../queue/queues.js';
import { historicalQueueFor, QueueJobNames } from '../queue/types.js';

// Caps how many backfill jobs a single tick can enqueue for one instrument+resolution -- even
// within the resolution-bounded lookback window, a genuinely large gap (e.g. a brand new
// instrument, or a long source outage) shouldn't burst thousands of jobs at once. The remainder
// gets picked up on the next tick.
const MAX_GAP_CHUNKS_PER_RUN = 50;

// Backstop against gap-detection false positives piling up the queue faster than the workers
// (especially IB, serialized to ~1 call/10s -- see connectors/ib/rateLimiter.ts) can drain it --
// e.g. an expected-time grid that doesn't quite match a source's real bar alignment would
// otherwise "discover" the same not-actually-missing candles on every 5-minute tick forever,
// each time adding up to MAX_GAP_CHUNKS_PER_RUN more jobs on top of whatever's still queued from
// the last tick. This doesn't fix a bad detector, but it does mean the failure mode is "gap-fill
// pauses under a big backlog" instead of "the historical queue grows without bound." Checked
// against this instrument's own source-specific historical queue (see queue/types.ts), so a
// backed-up IB queue can't pause Yahoo/Binance gap-filling.
const MAX_HISTORICAL_BACKLOG_BEFORE_PAUSING_GAP_FILL = 300;

export const fillCandleGaps = async ({ instrumentId }: { instrumentId: string }): Promise<void> => {
  const instrument = await getInstrumentById(instrumentId);
  if (!instrument) {
    return;
  }

  const queueName = historicalQueueFor(instrument.source);
  const { waiting, active } = await queues[queueName].getJobCounts('waiting', 'active');
  if (waiting + active >= MAX_HISTORICAL_BACKLOG_BEFORE_PAUSING_GAP_FILL) {
    return;
  }

  for (const [resolution, coverage] of Object.entries(instrument.collectedResolutions) as [
    BaseResolution,
    (typeof instrument.collectedResolutions)[BaseResolution]
  ][]) {
    if (!coverage || coverage.status !== 'finished' || !coverage.cacheFrom || !coverage.cacheTo) {
      continue;
    }

    const lookbackMs = GAP_CHECK_LOOKBACK_DAYS[resolution] * 24 * 60 * 60_000;
    const earliestCheckable = new Date(Date.now() - lookbackMs);
    const from = coverage.cacheFrom > earliestCheckable ? coverage.cacheFrom : earliestCheckable;
    // Defensive clamp: a `cacheTo` ahead of real time (bad upstream bar, clock skew, manual DB
    // edit) would otherwise have gap-detection "discover" gaps in a range that hasn't happened
    // yet and enqueue backfill jobs doomed to fail with an inverted date range forever.
    const now = new Date();
    const to = coverage.cacheTo < now ? coverage.cacheTo : now;
    if (from >= to) {
      continue;
    }

    const gaps = await findCandleGaps({
      instrumentId,
      resolution,
      calendarVenue: instrument.calendarVenue,
      from,
      to
    });

    for (const gap of gaps.slice(0, MAX_GAP_CHUNKS_PER_RUN)) {
      await addQueueJob({
        queueName,
        job: {
          name: QueueJobNames.backfillHistoricalCandles,
          data: { instrumentId, resolution, from: gap.from.toISOString(), to: gap.to.toISOString() },
          opts: BACKFILL_JOB_OPTS
        }
      });
    }
  }
};
