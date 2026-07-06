import { ObjectId } from 'mongodb';

export type InstrumentSource = 'binance' | 'yahoo';
export type AssetClass = 'spot' | 'perpetual' | 'equity' | 'future' | 'index' | 'forex';
export type BaseResolution = '1_minute' | '1_hour' | '1_day';
export type InstrumentStatus = 'pending' | 'inProgress' | 'finished';

// Finest first -- used wherever we need to iterate/query "every resolution a connector can
// natively serve" (as opposed to `Resolution` in modules/candles, which also includes derived
// ones like 15_minute).
export const BASE_RESOLUTIONS: BaseResolution[] = ['1_minute', '1_hour', '1_day'];

export interface FutureDetails {
  expiry: Date;
  underlying: string;
  contractMonth: string;
}

export interface InstrumentDoc {
  _id: ObjectId;
  source: InstrumentSource;
  assetClass: AssetClass;
  sourceSymbol: string;
  displaySymbol: string;
  baseCurrency?: string;
  quoteCurrency?: string;
  baseResolution: BaseResolution;
  calendarVenue: string;
  cacheHistoricalPrices: boolean;
  cacheLivePrices: boolean;
  cacheFrom: Date | null;
  cacheTo: Date | null;
  status: InstrumentStatus;
  // Earliest date the source actually has data for, per resolution -- intraday depth (1-minute,
  // often 1-hour too) is frequently much shallower than daily, so a single instrument-level date
  // isn't enough to know how far back a backfill at a *specific* resolution can reach. Populated
  // at registration time and refreshed whenever baseResolution changes; keyed only by the
  // resolutions we've actually looked up (not guaranteed to have all three).
  earliestAvailableDates?: Partial<Record<BaseResolution, Date>>;
  // Reserved for Tradovate/IB dated futures -- unused by the Yahoo/Binance connectors today.
  futureDetails?: FutureDetails;
  createdAt: Date;
  updatedAt: Date;
}

export interface PublicInstrument {
  id: string;
  source: InstrumentSource;
  assetClass: AssetClass;
  sourceSymbol: string;
  displaySymbol: string;
  baseCurrency?: string;
  quoteCurrency?: string;
  baseResolution: BaseResolution;
  calendarVenue: string;
  cacheHistoricalPrices: boolean;
  cacheLivePrices: boolean;
  cacheFrom: string | null;
  cacheTo: string | null;
  status: InstrumentStatus;
  earliestAvailableDates: Partial<Record<BaseResolution, string>>;
}

export const toPublicInstrument = (doc: InstrumentDoc): PublicInstrument => ({
  id: doc._id.toHexString(),
  source: doc.source,
  assetClass: doc.assetClass,
  sourceSymbol: doc.sourceSymbol,
  displaySymbol: doc.displaySymbol,
  baseCurrency: doc.baseCurrency,
  quoteCurrency: doc.quoteCurrency,
  baseResolution: doc.baseResolution,
  calendarVenue: doc.calendarVenue,
  cacheHistoricalPrices: doc.cacheHistoricalPrices,
  cacheLivePrices: doc.cacheLivePrices,
  cacheFrom: doc.cacheFrom ? doc.cacheFrom.toISOString() : null,
  cacheTo: doc.cacheTo ? doc.cacheTo.toISOString() : null,
  status: doc.status,
  earliestAvailableDates: Object.fromEntries(
    Object.entries(doc.earliestAvailableDates ?? {}).map(([resolution, date]) => [resolution, date.toISOString()])
  ) as Partial<Record<BaseResolution, string>>
});
