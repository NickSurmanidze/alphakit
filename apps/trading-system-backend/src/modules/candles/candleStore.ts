import { timescale } from '../../db/timescale.js';
import { Candle } from '../../connectors/types.js';
import {
  candlesTableName,
  RESOLUTION_ORDER,
  RESOLUTION_TO_PG_INTERVAL,
  Resolution,
  resolutionsToDerive
} from './resolutions.js';

const UPSERT_BATCH_SIZE = 500;
const COLUMNS_PER_ROW = 8;

export const upsertCandles = async (params: {
  instrumentId: string;
  resolution: Resolution;
  source: string;
  candles: Candle[];
}): Promise<void> => {
  const { instrumentId, resolution, source, candles } = params;
  if (candles.length === 0) return;

  const table = candlesTableName(resolution);

  for (let offset = 0; offset < candles.length; offset += UPSERT_BATCH_SIZE) {
    const batch = candles.slice(offset, offset + UPSERT_BATCH_SIZE);
    const values: unknown[] = [];
    const placeholders = batch.map((c, i) => {
      const base = i * COLUMNS_PER_ROW;
      values.push(c.timeOpen, instrumentId, c.open, c.high, c.low, c.close, c.volume, source);
      return `($${base + 1},$${base + 2},$${base + 3},$${base + 4},$${base + 5},$${base + 6},$${base + 7},$${base + 8})`;
    });

    await timescale().query(
      `
      INSERT INTO ${table} (ts, instrument_id, open, high, low, close, volume, source)
      VALUES ${placeholders.join(',')}
      ON CONFLICT (ts, instrument_id) DO UPDATE SET
        open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low, close = EXCLUDED.close,
        volume = EXCLUDED.volume, source = EXCLUDED.source
      `,
      values
    );
  }
};

/**
 * Merges a new slice of data into the *current, still-open* bucket at `resolution`, rather
 * than overwriting it: high/low widen (GREATEST/LEAST), volume accumulates, close takes the
 * latest value -- and open is left untouched on conflict, so the bucket's true opening price
 * survives every subsequent call. Used to keep e.g. the current hour candle live-updating
 * every minute when the instrument's base resolution is coarser than what's being polled.
 */
export const mergeCandleIncremental = async (params: {
  instrumentId: string;
  resolution: Resolution;
  source: string;
  bucketStart: Date;
  candle: { open: number; high: number; low: number; close: number; volume: number };
}): Promise<void> => {
  const { instrumentId, resolution, source, bucketStart, candle } = params;
  const table = candlesTableName(resolution);

  await timescale().query(
    `
    INSERT INTO ${table} (ts, instrument_id, open, high, low, close, volume, source)
    VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
    ON CONFLICT (ts, instrument_id) DO UPDATE SET
      high = GREATEST(${table}.high, EXCLUDED.high),
      low = LEAST(${table}.low, EXCLUDED.low),
      close = EXCLUDED.close,
      volume = ${table}.volume + EXCLUDED.volume,
      source = EXCLUDED.source
    `,
    [bucketStart, instrumentId, candle.open, candle.high, candle.low, candle.close, candle.volume, source]
  );
};

export const getCandles = async (params: {
  instrumentId: string;
  resolution: Resolution;
  from: Date;
  to: Date;
  limit?: number;
}): Promise<Candle[]> => {
  const table = candlesTableName(params.resolution);
  const result = await timescale().query(
    `SELECT ts, open, high, low, close, volume FROM ${table}
     WHERE instrument_id = $1 AND ts >= $2 AND ts < $3
     ORDER BY ts ASC
     LIMIT $4`,
    [params.instrumentId, params.from, params.to, params.limit ?? 5000]
  );

  return result.rows.map(row => ({
    timeOpen: row.ts,
    open: Number(row.open),
    high: Number(row.high),
    low: Number(row.low),
    close: Number(row.close),
    volume: Number(row.volume)
  }));
};

/** Latest close per instrument, at each instrument's own resolution -- batched by resolution (one
 * query per distinct resolution requested, not one per instrument) via `DISTINCT ON`, for the
 * instrument list view where showing a price for dozens of instruments as N individual queries
 * would be wasteful. Instruments with no candles yet (still backfilling) are simply absent from
 * the returned map rather than erroring. */
export const getLatestCloses = async (
  requests: { instrumentId: string; resolution: Resolution }[]
): Promise<Map<string, number>> => {
  const instrumentIdsByResolution = new Map<Resolution, string[]>();
  for (const { instrumentId, resolution } of requests) {
    const list = instrumentIdsByResolution.get(resolution) ?? [];
    list.push(instrumentId);
    instrumentIdsByResolution.set(resolution, list);
  }

  const result = new Map<string, number>();
  for (const [resolution, instrumentIds] of instrumentIdsByResolution) {
    const table = candlesTableName(resolution);
    const rows = await timescale().query(
      `SELECT DISTINCT ON (instrument_id) instrument_id, close
       FROM ${table}
       WHERE instrument_id = ANY($1)
       ORDER BY instrument_id, ts DESC`,
      [instrumentIds]
    );
    for (const row of rows.rows) {
      result.set(row.instrument_id, Number(row.close));
    }
  }
  return result;
};

/**
 * Aggregates `sourceResolution` data into every coarser resolution up to (but not including) the
 * instrument's next explicitly-collected resolution, directly in SQL (TimescaleDB's
 * first()/last() hyperfunctions), for the given window. App-level derivation, not a continuous
 * aggregate -- matches the legacy system's approach, which avoided continuous aggregates because
 * they fight the upsert-heavy gap-correction pattern used here too.
 *
 * `collectedResolutions` is the instrument's full set of directly-fetched resolutions (not just
 * `sourceResolution`) -- an instrument collecting both '5_minute' and '1_day' directly must never
 * have its directly-fetched '1_day' rows overwritten by a '5_minute'-derived bucket, since the
 * derived version would be truncated to '5_minute's much shallower depth.
 */
export const deriveCoarserResolutions = async (params: {
  instrumentId: string;
  sourceResolution: Resolution;
  collectedResolutions: Resolution[];
  from: Date;
  to: Date;
}): Promise<void> => {
  const { instrumentId, sourceResolution, collectedResolutions, from, to } = params;
  const sourceTable = candlesTableName(sourceResolution);

  for (const target of resolutionsToDerive(sourceResolution, collectedResolutions)) {
    const targetTable = candlesTableName(target);
    const interval = RESOLUTION_TO_PG_INTERVAL[target];

    await timescale().query(
      `
      INSERT INTO ${targetTable} (ts, instrument_id, open, high, low, close, volume, source, validated)
      SELECT
        time_bucket($1::interval, ts) AS bucket,
        instrument_id,
        first(open, ts),
        max(high),
        min(low),
        last(close, ts),
        sum(volume),
        'derived',
        false
      FROM ${sourceTable}
      WHERE instrument_id = $2 AND ts >= $3 AND ts < $4
      GROUP BY bucket, instrument_id
      ON CONFLICT (ts, instrument_id) DO UPDATE SET
        open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low, close = EXCLUDED.close,
        volume = EXCLUDED.volume, source = EXCLUDED.source
      `,
      [interval, instrumentId, from, to]
    );
  }
};

/** Deletes every candle row for `instrumentId` across all resolution tables. Used when an
 * instrument is deleted -- otherwise its historical data becomes permanently orphaned in
 * TimescaleDB (unreachable once the Mongo instrument doc is gone, but never cleaned up). */
export const deleteAllCandles = async (instrumentId: string): Promise<void> => {
  for (const resolution of RESOLUTION_ORDER) {
    await timescale().query(`DELETE FROM ${candlesTableName(resolution)} WHERE instrument_id = $1`, [
      instrumentId
    ]);
  }
};

/** Deletes candle rows for `instrumentId` at just one resolution. Used to reset a single
 * explicitly-collected resolution (see instruments.router.ts `resetResolution`) without touching
 * the instrument's other collected resolutions or their derived tables. */
export const deleteCandlesForResolution = async (instrumentId: string, resolution: Resolution): Promise<void> => {
  await timescale().query(`DELETE FROM ${candlesTableName(resolution)} WHERE instrument_id = $1`, [instrumentId]);
};
