import { getConnector } from '../connectors/registry.js';
import { Candle } from '../connectors/types.js';
import { publishRedis } from '../db/redis.js';
import {
  deriveCoarserResolutions,
  getCandles,
  mergeCandleIncremental,
  upsertCandles
} from '../modules/candles/candleStore.js';
import { floorToBucketStart, RESOLUTION_MS } from '../modules/candles/resolutions.js';
import { getInstrumentById, updateInstrumentCoverage } from '../modules/instruments/instruments.repository.js';

const dayStartOf = (date: Date): Date =>
  new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate()));

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

export const refreshLatestCandles = async ({ instrumentId }: { instrumentId: string }): Promise<void> => {
  const instrument = await getInstrumentById(instrumentId);
  if (!instrument || !instrument.cacheLivePrices) {
    return;
  }

  const connector = getConnector(instrument.source);

  if (instrument.baseResolution === '1_minute') {
    const candle = await connector.fetchLatestCandle({ symbol: instrument.sourceSymbol, resolution: '1_minute' });
    if (!candle) {
      return;
    }

    await upsertCandles({ instrumentId, resolution: '1_minute', source: instrument.source, candles: [candle] });

    // Re-derive from the start of the UTC day containing this candle, not just this one row --
    // the running hour/day buckets need their full extent recomputed, not just the latest minute,
    // or high/low/volume for the in-progress bucket would be wrong.
    await deriveCoarserResolutions({
      instrumentId,
      sourceResolution: '1_minute',
      from: dayStartOf(candle.timeOpen),
      to: new Date(candle.timeOpen.getTime() + 1)
    });

    await updateInstrumentCoverage(instrumentId, { cacheTo: candle.timeOpen });
    await publishLatestCandle(instrumentId, '1_minute', candle);
    return;
  }

  // Coarser base resolution (1_hour / 1_day): keep the current, still-open bucket live-updating
  // every tick by incrementally merging in whatever new 1-minute data has arrived since the
  // last tick, rather than waiting for the whole bucket to close before it ever appears.
  const now = new Date();
  const bucketStart = floorToBucketStart(now, instrument.baseResolution);
  const fetchFrom = instrument.cacheTo && instrument.cacheTo > bucketStart ? instrument.cacheTo : bucketStart;

  const { candles } = await connector.fetchHistoricalCandles({
    symbol: instrument.sourceSymbol,
    resolution: '1_minute',
    from: fetchFrom,
    to: now
  });
  if (candles.length === 0) {
    return;
  }

  const aggregated = {
    open: candles[0].open,
    high: Math.max(...candles.map(c => c.high)),
    low: Math.min(...candles.map(c => c.low)),
    close: candles[candles.length - 1].close,
    volume: candles.reduce((sum, c) => sum + c.volume, 0)
  };

  await mergeCandleIncremental({
    instrumentId,
    resolution: instrument.baseResolution,
    source: instrument.source,
    bucketStart,
    candle: aggregated
  });

  const bucketEnd = new Date(bucketStart.getTime() + RESOLUTION_MS[instrument.baseResolution]);
  await deriveCoarserResolutions({
    instrumentId,
    sourceResolution: instrument.baseResolution,
    from: dayStartOf(bucketStart),
    to: bucketEnd
  });

  // Advance coverage to just past the last minute we actually counted, so the next tick fetches
  // only genuinely new minutes instead of re-summing (and double-counting volume for) old ones.
  const newCacheTo = new Date(candles[candles.length - 1].timeOpen.getTime() + RESOLUTION_MS['1_minute']);
  await updateInstrumentCoverage(instrumentId, { cacheTo: newCacheTo });

  // Read back the merged row (not the transient aggregated slice) so subscribers see the
  // bucket's true running open/high/low/volume, not just this tick's incremental contribution.
  const [liveCandle] = await getCandles({
    instrumentId,
    resolution: instrument.baseResolution,
    from: bucketStart,
    to: bucketEnd,
    limit: 1
  });
  if (liveCandle) {
    await publishLatestCandle(instrumentId, instrument.baseResolution, liveCandle);
  }
};
