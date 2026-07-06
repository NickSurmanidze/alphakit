import { AssetClass, BaseResolution, InstrumentSource } from '../modules/instruments/instruments.types.js';

export interface Candle {
  timeOpen: Date;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface SymbolSearchResult {
  symbol: string;
  displaySymbol: string;
  assetClass: AssetClass;
  baseCurrency?: string;
  quoteCurrency?: string;
}

export interface MarketDataConnector {
  readonly source: InstrumentSource;

  searchSymbols(query: string): Promise<SymbolSearchResult[]>;

  /** The earliest date this source actually has data for this symbol *at this resolution*, or
   * null if that can't be determined. Intraday depth is frequently much shallower than daily
   * (e.g. Yahoo's 1-minute bars only go back ~8 days vs. decades for daily), so this is looked
   * up per resolution rather than once per symbol. Used for "backfill full history" instead of
   * a fixed lookback window, and to bound a resolution change's re-backfill range. */
  getEarliestAvailableDate(symbol: string, resolution: BaseResolution): Promise<Date | null>;

  /**
   * Fetch OHLC candles for [from, to). Implementations handle their own pagination and,
   * where relevant (Yahoo), fall back to a coarser resolution for ranges the source can no
   * longer serve at `resolution` -- the returned `resolution` tells the caller which table
   * to actually upsert into, since it may differ from the one requested.
   */
  fetchHistoricalCandles(params: {
    symbol: string;
    resolution: BaseResolution;
    from: Date;
    to: Date;
  }): Promise<{ resolution: BaseResolution; candles: Candle[] }>;

  /** The most recent fully-closed candle at `resolution`, or null if none is available yet. */
  fetchLatestCandle(params: { symbol: string; resolution: BaseResolution }): Promise<Candle | null>;
}
