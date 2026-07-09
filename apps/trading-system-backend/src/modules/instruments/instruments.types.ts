import { ObjectId } from 'mongodb';

export type InstrumentSource = 'binance' | 'yahoo' | 'ib';
export type AssetClass = 'spot' | 'perpetual' | 'equity' | 'future' | 'index' | 'forex';
export type BaseResolution = '1_minute' | '5_minute' | '1_hour' | '1_day';
export type InstrumentStatus = 'pending' | 'inProgress' | 'finished';

// Finest first -- used wherever we need to iterate/query "every resolution a connector can
// natively serve" (as opposed to `Resolution` in modules/candles, which also includes derived
// ones like 15_minute).
export const BASE_RESOLUTIONS: BaseResolution[] = ['1_minute', '5_minute', '1_hour', '1_day'];

export interface FutureDetails {
  expiry: Date;
  underlying: string;
  contractMonth: string;
}

// Per-resolution cache-coverage tracking. An instrument can explicitly collect more than one
// resolution directly from its source (e.g. an IB future collecting both '5_minute' and '1_day'
// directly, since daily depth on IB reaches decades back while intraday depth is much shallower)
// -- each collected resolution needs its own independent cacheFrom/cacheTo/status, since deriving
// the coarser one from the finer one would truncate it to the finer resolution's shallower depth.
// Resolutions strictly between two collected ones are still derived in SQL (see
// resolutionsToDerive in modules/candles/resolutions.ts); this map only covers what's fetched
// directly from the source.
export interface ResolutionCoverage {
  cacheFrom: Date | null;
  cacheTo: Date | null;
  status: InstrumentStatus;
  // Earliest date the source actually has data for at this resolution, or null if unknown.
  // Looked up lazily (only for resolutions actually registered), refreshed on reset.
  earliestAvailableDate: Date | null;
}

export interface InstrumentDoc {
  _id: ObjectId;
  source: InstrumentSource;
  assetClass: AssetClass;
  sourceSymbol: string;
  displaySymbol: string;
  description?: string;
  baseCurrency?: string;
  quoteCurrency?: string;
  collectedResolutions: Partial<Record<BaseResolution, ResolutionCoverage>>;
  calendarVenue: string;
  cacheHistoricalPrices: boolean;
  cacheLivePrices: boolean;
  // Which dated contract is currently front month for a continuous future (display only -- the
  // IB connector fetches via the CONTFUT continuous contract itself, not this). Unused by
  // Binance/Yahoo.
  futureDetails?: FutureDetails;
  // Dollar value of a one-unit move in the raw quoted price -- 1 when the quote already *is* the
  // dollar price (equities, spot/perpetual crypto, most tickers), a real conversion factor for
  // futures that quote an abstract index level or a per-unit price (see pointValues.ts). Set at
  // registration from a static lookup table, not fetched per-instrument -- it's a property of the
  // real-world contract, identical for the same displaySymbol regardless of source.
  pointValue: number;
  createdAt: Date;
  updatedAt: Date;
}

export interface PublicResolutionCoverage {
  cacheFrom: string | null;
  cacheTo: string | null;
  status: InstrumentStatus;
  earliestAvailableDate: string | null;
}

export interface PublicInstrument {
  id: string;
  source: InstrumentSource;
  assetClass: AssetClass;
  sourceSymbol: string;
  displaySymbol: string;
  description: string | null;
  baseCurrency?: string;
  quoteCurrency?: string;
  collectedResolutions: Partial<Record<BaseResolution, PublicResolutionCoverage>>;
  calendarVenue: string;
  cacheHistoricalPrices: boolean;
  cacheLivePrices: boolean;
  futureDetails?: { expiry: string; underlying: string; contractMonth: string };
  pointValue: number;
}

/** The finest resolution an instrument explicitly collects, by BASE_RESOLUTIONS order -- used
 * wherever code needs a single representative resolution (e.g. an overall status badge, or the
 * source of truth for live incremental merges into every coarser collected resolution). Throws if
 * an instrument somehow has no collected resolutions, since that's not a valid state. */
export const finestResolution = (doc: Pick<InstrumentDoc, 'collectedResolutions'>): BaseResolution => {
  const finest = BASE_RESOLUTIONS.find(resolution => resolution in doc.collectedResolutions);
  if (!finest) {
    throw new Error('Instrument has no collected resolutions');
  }
  return finest;
};

const toPublicCoverage = (coverage: ResolutionCoverage): PublicResolutionCoverage => ({
  cacheFrom: coverage.cacheFrom ? coverage.cacheFrom.toISOString() : null,
  cacheTo: coverage.cacheTo ? coverage.cacheTo.toISOString() : null,
  status: coverage.status,
  earliestAvailableDate: coverage.earliestAvailableDate ? coverage.earliestAvailableDate.toISOString() : null
});

export const toPublicInstrument = (doc: InstrumentDoc): PublicInstrument => ({
  id: doc._id.toHexString(),
  source: doc.source,
  assetClass: doc.assetClass,
  sourceSymbol: doc.sourceSymbol,
  displaySymbol: doc.displaySymbol,
  description: doc.description ?? null,
  baseCurrency: doc.baseCurrency,
  quoteCurrency: doc.quoteCurrency,
  collectedResolutions: Object.fromEntries(
    Object.entries(doc.collectedResolutions).map(([resolution, coverage]) => [resolution, toPublicCoverage(coverage)])
  ) as Partial<Record<BaseResolution, PublicResolutionCoverage>>,
  calendarVenue: doc.calendarVenue,
  cacheHistoricalPrices: doc.cacheHistoricalPrices,
  cacheLivePrices: doc.cacheLivePrices,
  pointValue: doc.pointValue,
  futureDetails: doc.futureDetails
    ? {
        expiry: doc.futureDetails.expiry.toISOString(),
        underlying: doc.futureDetails.underlying,
        contractMonth: doc.futureDetails.contractMonth
      }
    : undefined
});
