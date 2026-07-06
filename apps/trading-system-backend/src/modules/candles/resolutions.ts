import { BaseResolution } from '../instruments/instruments.types.js';

// All resolutions we store, finest to coarsest. BaseResolution ('1_minute' | '1_hour' | '1_day')
// is the set a connector can hand us raw data at; the full set below is what we store/serve.
export type Resolution = '1_minute' | '15_minute' | '1_hour' | '1_day';

export const RESOLUTION_ORDER: Resolution[] = ['1_minute', '15_minute', '1_hour', '1_day'];

export const RESOLUTION_MS: Record<Resolution, number> = {
  '1_minute': 60_000,
  '15_minute': 15 * 60_000,
  '1_hour': 60 * 60_000,
  '1_day': 24 * 60 * 60_000
};

export const RESOLUTION_TO_PG_INTERVAL: Record<Resolution, string> = {
  '1_minute': '1 minute',
  '15_minute': '15 minutes',
  '1_hour': '1 hour',
  '1_day': '1 day'
};

export const candlesTableName = (resolution: Resolution): string => `candles__${resolution}`;

/**
 * How far back gap-checking is allowed to look, per base resolution. Every source has a real,
 * finite depth limit for intraday data (Yahoo's 1-hour bars go back ~2 years at most; Binance's
 * 1-minute is effectively unbounded but still not worth scanning decades of it every 5 minutes).
 * Checking gaps beyond a resolution's realistic depth is not just wasteful -- if a historical
 * backfill already fell back to a coarser resolution for an old range (see
 * MarketDataConnector.fetchHistoricalCandles), that range's target-resolution table will *never*
 * have data, so unbounded gap-checking re-flags the same unfillable gap forever and floods the
 * source with doomed requests every single tick.
 */
export const GAP_CHECK_LOOKBACK_DAYS: Record<BaseResolution, number> = {
  '1_minute': 7,
  '1_hour': 90,
  '1_day': 3650
};

/** Resolutions strictly coarser than `from`, in order -- what to derive after writing raw data. */
export const coarserResolutionsThan = (from: BaseResolution | Resolution): Resolution[] => {
  const idx = RESOLUTION_ORDER.indexOf(from as Resolution);
  return idx === -1 ? [] : RESOLUTION_ORDER.slice(idx + 1);
};

/** Floors `date` down to the start of its bucket at `resolution` (top of the hour, start of the
 * UTC day, etc). Epoch-aligned integer division, matching Postgres's default time_bucket
 * origin for these regular interval sizes -- so this always agrees with the derived tables. */
export const floorToBucketStart = (date: Date, resolution: Resolution): Date => {
  const stepMs = RESOLUTION_MS[resolution];
  return new Date(Math.floor(date.getTime() / stepMs) * stepMs);
};
