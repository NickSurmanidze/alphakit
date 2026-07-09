import { Bar, BarSizeSetting, Contract, ErrorCode, IBApiNextError, SecType, WhatToShow } from '@stoqey/ib';

import { AssetClass, BaseResolution } from '../../modules/instruments/instruments.types.js';
import { Candle, MarketDataConnector, SymbolSearchResult } from '../types.js';
import { getIbClient } from './client.js';
import { ibRateLimited } from './rateLimiter.js';

const DEFAULT_FUTURES_EXCHANGE = 'CME';

// IB quirk specific to legacy full-size CME FX futures: their popular ticker (6E, 6B, 6J, 6C,
// 6S, 6A) is the *localSymbol*, not the `symbol` field reqContractDetails/reqHeadTimestamp/
// reqHistoricalData actually key on -- that field wants the ISO currency code instead (EUR, GBP,
// JPY, CAD, CHF, AUD). Querying with the ticker fails outright with "No security definition has
// been found for the request" (IB error 200), for every one of those calls, not just historical
// data. Micro FX futures (M6A, M6E, ...) don't have this quirk -- IB assigned the popular ticker
// directly as their `symbol`. Applied to the continuous-future search probe below so searching
// "6E" finds the Euro FX future instead of silently coming up empty.
const CME_FX_FUTURES_SYMBOL_ALIASES: Record<string, string> = {
  '6A': 'AUD',
  '6B': 'GBP',
  '6C': 'CAD',
  '6E': 'EUR',
  '6J': 'JPY',
  '6S': 'CHF'
};

const HEAD_TIMESTAMP_CACHE_TTL_MS = 30_000;
const headTimestampCache = new Map<string, { date: Date | null; expiresAt: number }>();

const DEFAULT_EXCHANGE_BY_SEC_TYPE: Partial<Record<SecType, string>> = {
  [SecType.CONTFUT]: DEFAULT_FUTURES_EXCHANGE,
  [SecType.FUT]: DEFAULT_FUTURES_EXCHANGE,
  [SecType.STK]: 'SMART',
  [SecType.IND]: DEFAULT_FUTURES_EXCHANGE,
  [SecType.CASH]: 'IDEALPRO',
  [SecType.CRYPTO]: 'PAXOS'
};

const ASSET_CLASS_BY_SEC_TYPE: Partial<Record<SecType, AssetClass>> = {
  [SecType.CONTFUT]: 'future',
  [SecType.FUT]: 'future',
  [SecType.STK]: 'equity',
  [SecType.IND]: 'index',
  [SecType.CASH]: 'forex',
  [SecType.CRYPTO]: 'spot'
};

const RESOLUTION_TO_BAR_SIZE: Record<BaseResolution, BarSizeSetting> = {
  '1_minute': BarSizeSetting.MINUTES_ONE,
  '5_minute': BarSizeSetting.MINUTES_FIVE,
  '1_hour': BarSizeSetting.HOURS_ONE,
  '1_day': BarSizeSetting.DAYS_ONE
};

// Conservative per-request duration caps -- IB's actual max duration per bar size is an
// undocumented-in-detail, account/server-version-dependent limit. Rather than guess exactly and
// risk a rejected request, this caps well under the commonly-observed ceilings; a single request
// against these caps is unlikely to be everything a "full history" backfill wants (especially for
// dated, non-continuous contracts we may add later), but is safe to call without a live account
// to verify against, and getEarliestAvailableDate/GAP_CHECK_LOOKBACK_DAYS make sure the rest of
// the system reports whatever depth we actually got, honestly, rather than assuming more.
const MAX_DURATION_DAYS: Record<BaseResolution, number> = {
  '1_minute': 7,
  '5_minute': 30,
  '1_hour': 365,
  '1_day': 365 * 20
};

/** Our own encoding of "which IB contract does this instrument mean" into the single `symbol`
 * string the MarketDataConnector interface gives us (it has no separate assetClass/exchange
 * fields) -- `searchSymbols` below always produces symbols in this form, so registration round-
 * trips it back through `fetchHistoricalCandles`/etc. without needing interface changes. */
const encodeSourceSymbol = (symbol: string, exchange: string, secType: SecType): string =>
  `${symbol}@${exchange}@${secType}`;

const parseSourceSymbol = (sourceSymbol: string): { symbol: string; exchange: string; secType: SecType } => {
  const [symbol, exchange, secType] = sourceSymbol.split('@');
  return {
    symbol: symbol.toUpperCase(),
    exchange: exchange || DEFAULT_FUTURES_EXCHANGE,
    secType: (secType as SecType) || SecType.CONTFUT
  };
};

const resolveContract = (sourceSymbol: string): Contract => {
  const { symbol, exchange, secType } = parseSourceSymbol(sourceSymbol);
  return { symbol, secType, exchange, currency: 'USD' };
};

/** IB's historical/head-timestamp bar time can come back as: pure "yyyyMMdd" (daily+ bars,
 * regardless of the formatDate argument -- a known TWS API quirk), epoch seconds (intraday bars
 * with formatDate=2, what this connector always requests), or "yyyyMMdd HH:mm:ss[ TZ]"
 * (formatDate=1 style, kept as a defensive fallback in case a server version ever ignores our
 * formatDate request). */
const parseIbBarTime = (raw: string): Date => {
  if (/^\d{8}$/.test(raw)) {
    const year = Number(raw.slice(0, 4));
    const month = Number(raw.slice(4, 6)) - 1;
    const day = Number(raw.slice(6, 8));
    return new Date(Date.UTC(year, month, day));
  }
  if (/^\d+$/.test(raw)) {
    return new Date(Number(raw) * 1000);
  }
  const match = raw.match(/^(\d{4})(\d{2})(\d{2})[ -](\d{2}):(\d{2}):(\d{2})/);
  if (match) {
    const [, year, month, day, hour, minute, second] = match.map(Number);
    return new Date(Date.UTC(year, month - 1, day, hour, minute, second));
  }
  return new Date(raw);
};

const formatIbDateTime = (date: Date): string => {
  const iso = date.toISOString();
  return `${iso.slice(0, 10).replace(/-/g, '')} ${iso.slice(11, 19)} UTC`;
};

// IB rejects any "D" (days) duration string over 365 with "Historical data requests for
// durations longer than 365 days must be made in years" -- switches unit once the capped span
// crosses that line rather than always using days, which is what MAX_DURATION_DAYS['1_day']
// (20 years) needs in practice for a real full-history daily backfill.
const computeDurationStr = (fromMs: number, toMs: number, resolution: BaseResolution): string => {
  const spanDays = Math.ceil((toMs - fromMs) / (24 * 60 * 60 * 1000));
  const cappedDays = Math.min(Math.max(spanDays, 1), MAX_DURATION_DAYS[resolution]);
  if (cappedDays > 365) {
    return `${Math.ceil(cappedDays / 365)} Y`;
  }
  return `${cappedDays} D`;
};

// Only genuinely transient connectivity errors -- NOT e.g. IB's code 200 "No security definition
// has been found for the request" (not in this library's ErrorCode enum, but still a real code
// IB sends), a definitive "this contract doesn't exist" answer that a search box hits constantly
// while the user is still mid-word. Retrying that 3x with backoff only makes the box feel broken.
const RETRYABLE_IB_ERROR_CODES = new Set<ErrorCode>([
  ErrorCode.CONNECT_FAIL,
  ErrorCode.NOT_CONNECTED,
  ErrorCode.FAIL_CONNECTION_LOST_BETWEEN_SERVER_AND_TWS
]);

const isRetryableIbError = (e: unknown): boolean =>
  e instanceof IBApiNextError && RETRYABLE_IB_ERROR_CODES.has(e.code);

/** Retries only the small set of connectivity errors above; anything else (including a plain
 * "no such contract" response) fails on the first attempt. Keeps the cap low regardless, so a
 * connection that's genuinely down doesn't loop for long. */
const withIbRetry = async <T>(fn: () => Promise<T>, retries = 3): Promise<T> => {
  let attempt = 0;
  for (;;) {
    try {
      return await fn();
    } catch (e) {
      attempt++;
      if (!isRetryableIbError(e) || attempt >= retries) {
        throw e;
      }
      const delay = 1000 * 2 ** (attempt - 1);
      await new Promise(resolve => setTimeout(resolve, delay));
    }
  }
};

const toSearchResult = (contract: Contract, secType: SecType, longName?: string): SymbolSearchResult | null => {
  const assetClass = ASSET_CLASS_BY_SEC_TYPE[secType];
  if (!assetClass || !contract.symbol) {
    return null;
  }
  const exchange = contract.exchange || contract.primaryExch || DEFAULT_EXCHANGE_BY_SEC_TYPE[secType] || 'SMART';
  return {
    symbol: encodeSourceSymbol(contract.symbol, exchange, secType),
    displaySymbol: longName ? `${contract.symbol} — ${longName}` : contract.symbol,
    assetClass,
    quoteCurrency: contract.currency
  };
};

export const ibConnector: MarketDataConnector = {
  source: 'ib',

  async searchSymbols(query: string): Promise<SymbolSearchResult[]> {
    const client = getIbClient();
    const upperQuery = query.toUpperCase();
    const results: SymbolSearchResult[] = [];

    // CME continuous futures are the primary use case for this connector, and IB's generic
    // symbol-matching endpoint below doesn't reliably surface them -- try resolving the query
    // directly as a continuous-future root first (e.g. "NQ", "ES", "GC"), best-effort.
    const contfutSymbol = CME_FX_FUTURES_SYMBOL_ALIASES[upperQuery] ?? upperQuery;
    try {
      const details = await ibRateLimited(
        () =>
          withIbRetry(() =>
            client.getContractDetails({
              symbol: contfutSymbol,
              secType: SecType.CONTFUT,
              exchange: DEFAULT_FUTURES_EXCHANGE,
              currency: 'USD'
            })
          ),
        'high'
      );
      for (const d of details) {
        const result = toSearchResult(d.contract, SecType.CONTFUT, d.longName);
        if (!result) continue;
        // Prefer the popular ticker the user actually searched for (e.g. "6E") over IB's own
        // currency-code symbol ("EUR") in the display label -- the underlying `symbol`
        // (sourceSymbol) still correctly encodes what IB itself needs.
        if (contfutSymbol !== upperQuery) {
          result.displaySymbol = d.longName ? `${upperQuery} — ${d.longName}` : upperQuery;
        }
        results.push(result);
      }
    } catch {
      // Not a valid continuous-future root on CME -- fine, fall through to general matching.
    }

    try {
      const matches = await ibRateLimited(() => withIbRetry(() => client.getMatchingSymbols(query)), 'high');
      for (const desc of matches) {
        if (!desc.contract?.secType) continue;
        const result = toSearchResult(desc.contract, desc.contract.secType);
        if (result) results.push(result);
      }
    } catch {
      // Best-effort -- the continuous-future lookup above may already have what's needed.
    }

    const seen = new Set<string>();
    return results.filter(r => (seen.has(r.symbol) ? false : (seen.add(r.symbol), true))).slice(0, 25);
  },

  async getEarliestAvailableDate(symbol: string): Promise<Date | null> {
    // IB's head-timestamp is the earliest tick/trade time for the contract, not specific to a bar
    // size -- unlike Yahoo's per-resolution depth limits, there's a single ceiling here regardless
    // of which resolution is being registered. Registration calls this once per resolution back to
    // back (three times for a typical 5m/1h/1d instrument), so without caching that's the same
    // head-timestamp request repeated three times at ~10.5s/request each -- same rationale as
    // yahooConnector's firstTradeDateCache. Short TTL: just long enough to cover one registration
    // (or a batch of several back-to-back), not a long-lived cache.
    const cached = headTimestampCache.get(symbol);
    if (cached && cached.expiresAt > Date.now()) {
      return cached.date;
    }

    const contract = resolveContract(symbol);
    const client = getIbClient();
    let date: Date | null;
    try {
      const raw = await ibRateLimited(
        () => withIbRetry(() => client.getHeadTimestamp(contract, WhatToShow.TRADES, false, 2)),
        'high'
      );
      date = parseIbBarTime(raw);
    } catch {
      date = null;
    }
    headTimestampCache.set(symbol, { date, expiresAt: Date.now() + HEAD_TIMESTAMP_CACHE_TTL_MS });
    return date;
  },

  async fetchHistoricalCandles({
    symbol,
    resolution,
    from,
    to
  }): Promise<{ resolution: BaseResolution; candles: Candle[] }> {
    const contract = resolveContract(symbol);
    const barSize = RESOLUTION_TO_BAR_SIZE[resolution];
    const client = getIbClient();

    const isContinuousFuture = contract.secType === SecType.CONTFUT;
    // CONTFUT rejects an explicit historical end date (IB error 10339, "Setting end date/time for
    // continuous future security type is not allowed") -- only "N units of history ending now" is
    // possible, never "history ending on some past date". True backward pagination (like
    // binanceConnector/yahooConnector use for deep backfills) isn't available for it; instead we
    // fetch the largest single window IB will give us ending now and filter down to [from, to)
    // client-side. A genuinely old gap (`to` far from now) wastes part of that fetch on data past
    // `to`, but stays correct -- and matches the "report actual depth honestly" approach already
    // used for Yahoo's shallow intraday history rather than pretending full pagination is possible.
    const endDateTime = isContinuousFuture ? '' : formatIbDateTime(to);
    const durationAnchor = isContinuousFuture ? new Date() : to;
    const durationStr = computeDurationStr(from.getTime(), durationAnchor.getTime(), resolution);

    const bars = await ibRateLimited(() =>
      withIbRetry(() => client.getHistoricalData(contract, endDateTime, durationStr, barSize, WhatToShow.TRADES, false, 2))
    );

    const candles = barsToCandles(bars).filter(c => c.timeOpen >= from && c.timeOpen < to);
    return { resolution, candles };
  },

  async fetchLatestCandle({ symbol, resolution }): Promise<Candle | null> {
    const contract = resolveContract(symbol);
    const barSize = RESOLUTION_TO_BAR_SIZE[resolution];
    const client = getIbClient();

    const bars = await ibRateLimited(() =>
      withIbRetry(() => client.getHistoricalData(contract, '', '2 D', barSize, WhatToShow.TRADES, false, 2))
    );

    const closed = barsToCandles(bars);
    if (closed.length === 0) {
      return null;
    }

    // The last bar IB returns for an endDateTime='' (now) request can still be the in-progress
    // one; drop it unless it's the only bar available -- same convention as yahooConnector.
    return closed.length > 1 ? closed[closed.length - 2] : closed[closed.length - 1];
  }
};

const barsToCandles = (bars: Bar[]): Candle[] =>
  bars
    .filter(
      (b): b is Bar & { time: string; open: number; high: number; low: number; close: number } =>
        b.time !== undefined && b.open !== undefined && b.high !== undefined && b.low !== undefined && b.close !== undefined
    )
    .map(b => ({
      timeOpen: parseIbBarTime(b.time),
      open: b.open,
      high: b.high,
      low: b.low,
      close: b.close,
      volume: b.volume ?? 0
    }));

export const __testables = { parseIbBarTime, formatIbDateTime, computeDurationStr, parseSourceSymbol, encodeSourceSymbol };
