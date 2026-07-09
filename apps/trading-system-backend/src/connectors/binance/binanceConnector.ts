import { BaseResolution } from '../../modules/instruments/instruments.types.js';
import { Candle, MarketDataConnector, SymbolSearchResult } from '../types.js';
import { fetchJson } from '../withRetry.js';

const BASE_URL = 'https://api.binance.com';
const MAX_KLINES_PER_CALL = 1000;

const RESOLUTION_TO_INTERVAL: Record<BaseResolution, string> = {
  '1_minute': '1m',
  '5_minute': '5m',
  '1_hour': '1h',
  '1_day': '1d'
};

const RESOLUTION_TO_MS: Record<BaseResolution, number> = {
  '1_minute': 60_000,
  '5_minute': 5 * 60_000,
  '1_hour': 60 * 60_000,
  '1_day': 24 * 60 * 60_000
};

type BinanceKline = [
  number, // open time
  string, // open
  string, // high
  string, // low
  string, // close
  string, // volume
  number, // close time
  string,
  number,
  string,
  string,
  string
];

interface BinanceExchangeInfoSymbol {
  symbol: string;
  status: string;
  baseAsset: string;
  quoteAsset: string;
}

const toCandle = (kline: BinanceKline): Candle => ({
  timeOpen: new Date(kline[0]),
  open: Number(kline[1]),
  high: Number(kline[2]),
  low: Number(kline[3]),
  close: Number(kline[4]),
  volume: Number(kline[5])
});

const fetchKlines = async (params: {
  symbol: string;
  interval: string;
  startTime?: number;
  endTime?: number;
  limit?: number;
}): Promise<BinanceKline[]> => {
  const url = new URL('/api/v3/klines', BASE_URL);
  url.searchParams.set('symbol', params.symbol);
  url.searchParams.set('interval', params.interval);
  if (params.startTime !== undefined) url.searchParams.set('startTime', String(params.startTime));
  if (params.endTime !== undefined) url.searchParams.set('endTime', String(params.endTime));
  url.searchParams.set('limit', String(params.limit ?? MAX_KLINES_PER_CALL));

  return fetchJson<BinanceKline[]>(url.toString());
};

export const binanceConnector: MarketDataConnector = {
  source: 'binance',

  async searchSymbols(query: string): Promise<SymbolSearchResult[]> {
    const info = await fetchJson<{ symbols: BinanceExchangeInfoSymbol[] }>(
      `${BASE_URL}/api/v3/exchangeInfo`
    );

    const upperQuery = query.toUpperCase();
    return info.symbols
      .filter(s => s.status === 'TRADING' && s.symbol.includes(upperQuery))
      .slice(0, 25)
      .map(s => ({
        symbol: s.symbol,
        displaySymbol: `${s.baseAsset}/${s.quoteAsset}`,
        assetClass: 'spot' as const,
        baseCurrency: s.baseAsset,
        quoteCurrency: s.quoteAsset
      }));
  },

  async getEarliestAvailableDate(symbol: string, resolution: BaseResolution): Promise<Date | null> {
    // startTime=0 + limit=1 returns exactly the symbol's first-ever candle at this interval in
    // one cheap call. Binance serves every interval back to listing, so unlike Yahoo this is
    // exact per resolution, not an approximation -- no fallback-window logic needed.
    const klines = await fetchKlines({
      symbol,
      interval: RESOLUTION_TO_INTERVAL[resolution],
      startTime: 0,
      limit: 1
    });
    return klines.length > 0 ? new Date(klines[0][0]) : null;
  },

  async fetchHistoricalCandles({
    symbol,
    resolution,
    from,
    to
  }): Promise<{ resolution: BaseResolution; candles: Candle[] }> {
    const interval = RESOLUTION_TO_INTERVAL[resolution];
    const stepMs = RESOLUTION_TO_MS[resolution] * MAX_KLINES_PER_CALL;

    const candles: Candle[] = [];
    let cursor = from.getTime();
    const endTime = to.getTime();

    while (cursor < endTime) {
      const chunkEnd = Math.min(cursor + stepMs, endTime);
      const klines = await fetchKlines({
        symbol,
        interval,
        startTime: cursor,
        endTime: chunkEnd,
        limit: MAX_KLINES_PER_CALL
      });

      if (klines.length === 0) {
        cursor = chunkEnd;
        continue;
      }

      candles.push(...klines.map(toCandle));
      const lastOpenTime = klines[klines.length - 1][0];
      cursor = lastOpenTime + RESOLUTION_TO_MS[resolution];
    }

    return { resolution, candles };
  },

  async fetchLatestCandle({ symbol, resolution }): Promise<Candle | null> {
    const klines = await fetchKlines({ symbol, interval: RESOLUTION_TO_INTERVAL[resolution], limit: 2 });

    // The last element is the still-forming current-period candle; only the one before it
    // is guaranteed closed.
    if (klines.length < 2) {
      return null;
    }

    return toCandle(klines[klines.length - 2]);
  }
};
