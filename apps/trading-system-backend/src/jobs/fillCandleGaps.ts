import { findCandleGaps } from '../modules/candles/gapDetection.js';
import { GAP_CHECK_LOOKBACK_DAYS } from '../modules/candles/resolutions.js';
import { getInstrumentById } from '../modules/instruments/instruments.repository.js';
import { addQueueJob } from '../queue/queues.js';
import { QueueJobNames, Queues } from '../queue/types.js';

// Caps how many backfill jobs a single tick can enqueue for one instrument -- even within the
// resolution-bounded lookback window, a genuinely large gap (e.g. a brand new instrument, or a
// long source outage) shouldn't burst thousands of jobs at once. The remainder gets picked up
// on the next tick.
const MAX_GAP_CHUNKS_PER_RUN = 50;

export const fillCandleGaps = async ({ instrumentId }: { instrumentId: string }): Promise<void> => {
  const instrument = await getInstrumentById(instrumentId);
  if (!instrument || instrument.status !== 'finished' || !instrument.cacheFrom || !instrument.cacheTo) {
    return;
  }

  const lookbackMs = GAP_CHECK_LOOKBACK_DAYS[instrument.baseResolution] * 24 * 60 * 60_000;
  const earliestCheckable = new Date(Date.now() - lookbackMs);
  const from = instrument.cacheFrom > earliestCheckable ? instrument.cacheFrom : earliestCheckable;

  const gaps = await findCandleGaps({
    instrumentId,
    resolution: instrument.baseResolution,
    calendarVenue: instrument.calendarVenue,
    from,
    to: instrument.cacheTo
  });

  for (const gap of gaps.slice(0, MAX_GAP_CHUNKS_PER_RUN)) {
    await addQueueJob({
      queueName: Queues.MARKET_DATA_HISTORICAL,
      job: {
        name: QueueJobNames.backfillHistoricalCandles,
        data: { instrumentId, from: gap.from.toISOString(), to: gap.to.toISOString() }
      }
    });
  }
};
