// Mirrors modules/candles/resolutions.ts and modules/instruments/instruments.types.ts on the
// backend -- kept as plain local types rather than importing backend internals, same convention
// already used for Source/AssetClass elsewhere in this app.
export type BaseResolution = '1_minute' | '5_minute' | '1_hour' | '1_day';
export type Resolution = '1_minute' | '5_minute' | '15_minute' | '1_hour' | '1_day';

export const RESOLUTION_ORDER: Resolution[] = ['1_minute', '5_minute', '15_minute', '1_hour', '1_day'];

export const RESOLUTION_MS: Record<Resolution, number> = {
  '1_minute': 60_000,
  '5_minute': 5 * 60_000,
  '15_minute': 15 * 60_000,
  '1_hour': 60 * 60_000,
  '1_day': 24 * 60 * 60_000
};

export const RESOLUTION_LABELS: Record<Resolution, string> = {
  '1_minute': '1 minute',
  '5_minute': '5 minutes',
  '15_minute': '15 minutes',
  '1_hour': '1 hour',
  '1_day': '1 day'
};

/** Resolutions viewable for an instrument that explicitly collects `collectedResolutions`: each
 * collected resolution itself, plus everything coarser than it up to (but not including) the
 * next collected one -- mirrors the backend's `resolutionsToDerive` bounding rule, since a
 * resolution strictly between two collected ones is exactly what gets derived and stored. A
 * resolution finer than every collected resolution was never fetched/derived, so it has no data. */
export const viewableResolutions = (collectedResolutions: BaseResolution[]): Resolution[] => {
  const collected = new Set(collectedResolutions);
  const sorted = RESOLUTION_ORDER.filter(r => collected.has(r as BaseResolution));
  if (sorted.length === 0) return [];

  const result = new Set<Resolution>();
  for (const source of sorted) {
    result.add(source);
    const startIdx = RESOLUTION_ORDER.indexOf(source) + 1;
    for (let i = startIdx; i < RESOLUTION_ORDER.length; i++) {
      const candidate = RESOLUTION_ORDER[i];
      if (collected.has(candidate as BaseResolution)) break;
      result.add(candidate);
    }
  }
  return RESOLUTION_ORDER.filter(r => result.has(r));
};

/** The finest resolution an instrument explicitly collects -- mirrors the backend's
 * `finestResolution` helper, used wherever the UI needs one representative resolution (default
 * chart view, live-subscription eligibility). */
export const finestResolution = (collectedResolutions: BaseResolution[]): BaseResolution | null => {
  const finest = RESOLUTION_ORDER.find(r => collectedResolutions.includes(r as BaseResolution));
  return (finest as BaseResolution | undefined) ?? null;
};

/** The explicitly-collected resolution that `resolution` actually gets its data from: itself if
 * it's collected directly, otherwise the nearest finer collected resolution it's derived from
 * (mirrors the backend's derivation rule in `resolutionsToDerive`). Used to look up the *real*
 * earliest-available-date bound for scroll-back loading -- using any other resolution's bound
 * (e.g. always the instrument's overall finest collected resolution, regardless of what's being
 * viewed) is wrong whenever that's not also the one backing the current view: an instrument
 * collecting both '5_minute' (shallow, e.g. only a couple months) and '1_day' (deep, years) would
 * have its '1_day' view's scroll-back cut off at '5_minute's much later start date instead of
 * '1_day's own real, much earlier one. */
export const sourceResolutionFor = (
  resolution: Resolution,
  collectedResolutions: BaseResolution[]
): BaseResolution | null => {
  if (collectedResolutions.includes(resolution as BaseResolution)) {
    return resolution as BaseResolution;
  }
  const idx = RESOLUTION_ORDER.indexOf(resolution);
  for (let i = idx - 1; i >= 0; i--) {
    const candidate = RESOLUTION_ORDER[i] as BaseResolution;
    if (collectedResolutions.includes(candidate)) {
      return candidate;
    }
  }
  return null;
};
