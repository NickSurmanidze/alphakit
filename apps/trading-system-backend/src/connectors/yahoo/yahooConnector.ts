import YahooFinanceClient from 'yahoo-finance2';

import { AssetClass, BaseResolution } from '../../modules/instruments/instruments.types.js';
import { Candle, MarketDataConnector, SymbolSearchResult } from '../types.js';
import { withRetry } from '../withRetry.js';
import { yahooRateLimited } from './rateLimiter.js';

const yahooFinance = new YahooFinanceClient();

const RESOLUTION_TO_INTERVAL: Record<BaseResolution, '1m' | '1h' | '1d'> = {
  '1_minute': '1m',
  '1_hour': '1h',
  '1_day': '1d'
};

// Finest-first; fetchHistoricalCandles falls back one step coarser each time Yahoo rejects
// the range at the current resolution (its intraday history depth is limited and can change
// without notice -- better to react to an actual failure than hardcode a day-count threshold).
const FALLBACK_ORDER: BaseResolution[] = ['1_minute', '1_hour', '1_day'];
const nextCoarserResolution = (resolution: BaseResolution): BaseResolution | null => {
  const idx = FALLBACK_ORDER.indexOf(resolution);
  return idx < FALLBACK_ORDER.length - 1 ? FALLBACK_ORDER[idx + 1] : null;
};

// Yahoo's documented (and undocumented-but-observed) intraday depth limits, used only as a
// fallback window when a full-range request at that resolution is rejected outright -- we still
// prefer to read back whatever Yahoo actually returns over trusting these numbers exactly.
// Deliberately shaved a few days under the documented cap (730 for 1h, ~8 for 1m): a request for
// *exactly* 730 days was observed to still be rejected ("must be within the last 730 days"),
// likely because Yahoo's own clock/rounding puts the boundary a little earlier than ours -- the
// margin here is cheap insurance against that, not a real depth difference.
const FALLBACK_LOOKBACK_MS: Partial<Record<BaseResolution, number>> = {
  '1_minute': 7 * 24 * 60 * 60 * 1000,
  '1_hour': 725 * 24 * 60 * 60 * 1000
};

const QUOTE_TYPE_TO_ASSET_CLASS: Record<string, AssetClass | undefined> = {
  EQUITY: 'equity',
  ETF: 'equity',
  MUTUALFUND: 'equity',
  INDEX: 'index',
  CURRENCY: 'forex',
  FUTURE: 'future'
  // OPTION / CRYPTOCURRENCY intentionally excluded for this phase
};

// Registration looks up getEarliestAvailableDate for all three base resolutions back to back,
// and firstTradeDate is identical for all of them -- without this, that's the same "meta" chart
// request repeated three times (three real Yahoo round-trips, each 500ms rate-limited apart) for
// data that doesn't change within the life of a request. Short TTL: just long enough to cover
// one registration/resolution-change flow, not intended as a long-lived cache.
const FIRST_TRADE_DATE_CACHE_TTL_MS = 30_000;
const firstTradeDateCache = new Map<string, { date: Date | null; expiresAt: number }>();

const getFirstTradeDate = async (symbol: string): Promise<Date | null> => {
  const cached = firstTradeDateCache.get(symbol);
  if (cached && cached.expiresAt > Date.now()) {
    return cached.date;
  }

  const metaResult = await withRetry(() =>
    yahooRateLimited(() =>
      yahooFinance.chart(symbol, {
        period1: new Date(Date.now() - 24 * 60 * 60 * 1000),
        period2: new Date(),
        interval: '1d'
      })
    )
  );
  const date = metaResult.meta.firstTradeDate ?? null;
  firstTradeDateCache.set(symbol, { date, expiresAt: Date.now() + FIRST_TRADE_DATE_CACHE_TTL_MS });
  return date;
};

export const yahooConnector: MarketDataConnector = {
  source: 'yahoo',

  async searchSymbols(query: string): Promise<SymbolSearchResult[]> {
    const result = await withRetry(() => yahooRateLimited(() => yahooFinance.search(query)));

    return result.quotes
      .filter((q): q is typeof q & { symbol: string; quoteType: string } => 'quoteType' in q && 'symbol' in q)
      .map(q => ({ ...q, assetClass: QUOTE_TYPE_TO_ASSET_CLASS[q.quoteType] }))
      .filter((q): q is typeof q & { assetClass: AssetClass } => q.assetClass !== undefined)
      .slice(0, 25)
      .map(q => ({
        symbol: q.symbol,
        displaySymbol: ('shortname' in q && q.shortname) || q.symbol,
        assetClass: q.assetClass
      }));
  },

  async getEarliestAvailableDate(symbol: string, resolution: BaseResolution): Promise<Date | null> {
    // firstTradeDate is present in chart() meta regardless of the requested period, so a cheap
    // recent-range request is enough -- no need to guess/scan backward. This is the true answer
    // for daily (Yahoo serves daily bars back to listing) and the ceiling for intraday.
    const firstTradeDate = await getFirstTradeDate(symbol);
    if (resolution === '1_day' || !firstTradeDate) {
      return firstTradeDate;
    }

    // Intraday: try the full range at the requested resolution first -- Yahoo's actual depth
    // limits are undocumented and can change, so read back what it really returns rather than
    // trusting a hardcoded number. Only fall back to a known-safe window if the full-range
    // request is rejected outright.
    const now = new Date();
    try {
      const result = await withRetry(() =>
        yahooRateLimited(() =>
          yahooFinance.chart(symbol, { period1: firstTradeDate, period2: now, interval: RESOLUTION_TO_INTERVAL[resolution] })
        )
      );
      return result.quotes[0]?.date ?? null;
    } catch {
      const lookbackMs = FALLBACK_LOOKBACK_MS[resolution];
      if (!lookbackMs) return null;

      const result = await withRetry(() =>
        yahooRateLimited(() =>
          yahooFinance.chart(symbol, {
            period1: new Date(now.getTime() - lookbackMs),
            period2: now,
            interval: RESOLUTION_TO_INTERVAL[resolution]
          })
        )
      );
      return result.quotes[0]?.date ?? null;
    }
  },

  async fetchHistoricalCandles({
    symbol,
    resolution,
    from,
    to
  }): Promise<{ resolution: BaseResolution; candles: Candle[] }> {
    let currentResolution: BaseResolution | null = resolution;
    let lastError: unknown;

    while (currentResolution) {
      try {
        const result = await withRetry(() =>
          yahooRateLimited(() =>
            yahooFinance.chart(symbol, {
              period1: from,
              period2: to,
              interval: RESOLUTION_TO_INTERVAL[currentResolution as BaseResolution]
            })
          )
        );

        const candles: Candle[] = result.quotes
          .filter(q => q.open !== null && q.high !== null && q.low !== null && q.close !== null)
          .map(q => ({
            timeOpen: q.date,
            open: q.open as number,
            high: q.high as number,
            low: q.low as number,
            close: q.close as number,
            volume: q.volume ?? 0
          }));

        return { resolution: currentResolution, candles };
      } catch (e) {
        lastError = e;
        currentResolution = nextCoarserResolution(currentResolution);
      }
    }

    throw lastError;
  },

  async fetchLatestCandle({ symbol, resolution }): Promise<Candle | null> {
    const now = new Date();
    const lookback = new Date(now.getTime() - 2 * 60 * 60 * 1000); // 2h is plenty at any resolution here

    const result = await withRetry(() =>
      yahooRateLimited(() =>
        yahooFinance.chart(symbol, {
          period1: lookback,
          period2: now,
          interval: RESOLUTION_TO_INTERVAL[resolution]
        })
      )
    );

    const closed = result.quotes.filter(
      q => q.open !== null && q.high !== null && q.low !== null && q.close !== null
    );
    if (closed.length === 0) {
      return null;
    }

    // The last bar Yahoo returns can still be the in-progress one; drop it unless it's the
    // only bar available.
    const candidate = closed.length > 1 ? closed[closed.length - 2] : closed[closed.length - 1];
    return {
      timeOpen: candidate.date,
      open: candidate.open as number,
      high: candidate.high as number,
      low: candidate.low as number,
      close: candidate.close as number,
      volume: candidate.volume ?? 0
    };
  }
};
