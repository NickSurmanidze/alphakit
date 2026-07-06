// Mirrors modules/candles/resolutions.ts and modules/instruments/instruments.types.ts on the
// backend -- kept as plain local types rather than importing backend internals, same convention
// already used for Source/AssetClass elsewhere in this app.
export type BaseResolution = '1_minute' | '1_hour' | '1_day';
export type Resolution = '1_minute' | '15_minute' | '1_hour' | '1_day';

export const RESOLUTION_ORDER: Resolution[] = ['1_minute', '15_minute', '1_hour', '1_day'];

export const RESOLUTION_MS: Record<Resolution, number> = {
  '1_minute': 60_000,
  '15_minute': 15 * 60_000,
  '1_hour': 60 * 60_000,
  '1_day': 24 * 60 * 60_000
};

export const RESOLUTION_LABELS: Record<Resolution, string> = {
  '1_minute': '1 minute',
  '15_minute': '15 minutes',
  '1_hour': '1 hour',
  '1_day': '1 day'
};

// A resolution finer than the instrument's baseResolution was never fetched/derived, so it has
// no data -- only baseResolution itself and anything coarser than it are actually viewable.
export const viewableResolutions = (baseResolution: BaseResolution): Resolution[] => {
  const idx = RESOLUTION_ORDER.indexOf(baseResolution);
  return idx === -1 ? [baseResolution] : RESOLUTION_ORDER.slice(idx);
};
