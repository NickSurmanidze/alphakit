import { getConnector } from '../connectors/registry.js';
import { Candle } from '../connectors/types.js';
import { publishRedis } from '../db/redis.js';
import { deriveCoarserResolutions, getCandles, mergeCandleIncremental, upsertCandles } from '../modules/candles/candleStore.js';
import { coarserResolutionsThan, floorToBucketStart, RESOLUTION_MS } from '../modules/candles/resolutions.js';
import { BaseResolution } from '../modules/instruments/instruments.types.js';
import { getInstrumentById, updateResolutionCoverage } from '../modules/instruments/instruments.repository.js';

// Self-heal window: catches up on a few periods' worth of history each tick (not just the very
// latest one), so a transient error or worker restart that skips a tick doesn't leave a
// permanent hole waiting for fillCandleGaps' much slower cycle to notice and backfill it.
const LOOKBACK_PERIODS = 5;

const publishLatestCandle = async (instrumentId: string, resolution: string, candle: Candle): Promise<void> => {
  await publishRedis({
    channel: `candles:${instrumentId}:${resolution}`,
    message: JSON.stringify({
      timeOpen: candle.timeOpen.toISOString(),
      open: candle.open,
      high: candle.high,
      low: candle.low,
      close: candle.close,
      volume: candle.volume
    })
  });
};

/**
 * One tick for one collected resolution -- cron fires this at that resolution's own native
 * period (every 5 minutes for '5_minute', every hour for '1_hour', ...), not on a single global
 * "every minute" schedule regardless of what each instrument actually collects (see cron.ts).
 *
 * Each tick:
 *  1. Re-fetches and upserts the last few real candles at `resolution` itself, including
 *     whatever bucket is still open -- this both catches up on anything missed since the last
 *     tick and keeps `resolution`'s own current candle genuinely live, not just a coarser merge
 *     estimate of it.
 *  2. Cascades that same data into the *current, still-open* bucket of every other collected
 *     resolution coarser than this one -- so e.g. a '5_minute' tick alone keeps '1_hour' and
 *     '1_day' visibly live between their own (less frequent) ticks, with no separate job needed
 *     just for that. Each coarser resolution's own tick still runs on its own schedule to
 *     properly finalize/correct its stored data from a real fetch, not just this merge estimate.
 */
export const refreshLatestCandles = async ({
  instrumentId,
  resolution
}: {
  instrumentId: string;
  resolution: BaseResolution;
}): Promise<void> => {
  const instrument = await getInstrumentById(instrumentId);
  if (!instrument || !instrument.cacheLivePrices) {
    return;
  }

  const coverage = instrument.collectedResolutions[resolution];
  if (!coverage) {
    return;
  }

  const now = new Date();
  const bucketStart = floorToBucketStart(now, resolution);
  const lookbackFrom = new Date(bucketStart.getTime() - LOOKBACK_PERIODS * RESOLUTION_MS[resolution]);
  const fetchFrom = coverage.cacheTo && coverage.cacheTo > lookbackFrom ? coverage.cacheTo : lookbackFrom;
  // Defensive: also guards a `cacheTo` stuck ahead of real time (bad upstream bar, clock skew)
  // from requesting an inverted range every tick forever.
  if (fetchFrom.getTime() + RESOLUTION_MS[resolution] > now.getTime()) {
    return;
  }

  const connector = getConnector(instrument.source);
  // The connector's returned `resolution` may differ from what was requested (e.g. Yahoo falling
  // back to a coarser one) -- upsert/cascade off that, but track this resolution's own coverage
  // against what it was actually assigned, matching backfillHistoricalCandles.ts.
  const { resolution: fetchedResolution, candles } = await connector.fetchHistoricalCandles({
    symbol: instrument.sourceSymbol,
    resolution,
    from: fetchFrom,
    to: now
  });
  if (candles.length === 0) {
    return;
  }

  await upsertCandles({ instrumentId, resolution: fetchedResolution, source: instrument.source, candles });
  await updateResolutionCoverage(instrumentId, resolution, { cacheTo: candles[candles.length - 1].timeOpen });
  await publishLatestCandle(instrumentId, resolution, candles[candles.length - 1]);

  // Re-derive any purely-derived resolutions between `fetchedResolution` and the next
  // explicitly-collected one (e.g. '15_minute' between '5_minute' and '1_hour') from the full
  // fetched window, same rule backfill uses -- these never get their own tick.
  await deriveCoarserResolutions({
    instrumentId,
    sourceResolution: fetchedResolution,
    collectedResolutions: Object.keys(instrument.collectedResolutions) as BaseResolution[],
    from: candles[0].timeOpen,
    to: new Date(candles[candles.length - 1].timeOpen.getTime() + 1)
  });

  const collectedResolutions = Object.keys(instrument.collectedResolutions) as BaseResolution[];
  const collectedCoarser = coarserResolutionsThan(fetchedResolution).filter(r =>
    collectedResolutions.includes(r as BaseResolution)
  );

  for (const coarser of collectedCoarser) {
    const coarserBucketStart = floorToBucketStart(now, coarser);
    const relevant = candles.filter(c => c.timeOpen >= coarserBucketStart);
    if (relevant.length === 0) {
      continue;
    }

    const aggregated = {
      open: relevant[0].open,
      high: Math.max(...relevant.map(c => c.high)),
      low: Math.min(...relevant.map(c => c.low)),
      close: relevant[relevant.length - 1].close,
      volume: relevant.reduce((sum, c) => sum + c.volume, 0)
    };

    await mergeCandleIncremental({
      instrumentId,
      resolution: coarser,
      source: instrument.source,
      bucketStart: coarserBucketStart,
      candle: aggregated
    });

    // Read back the merged row (not the transient aggregated slice) so subscribers see the
    // bucket's true running open/high/low/volume, not just this tick's incremental contribution.
    const [liveCandle] = await getCandles({
      instrumentId,
      resolution: coarser,
      from: coarserBucketStart,
      to: new Date(coarserBucketStart.getTime() + RESOLUTION_MS[coarser]),
      limit: 1
    });
    if (liveCandle) {
      await publishLatestCandle(instrumentId, coarser, liveCandle);
    }
  }
};
