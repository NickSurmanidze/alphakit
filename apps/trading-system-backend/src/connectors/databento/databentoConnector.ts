import { env } from '../../env.js';
import { floorToBucketStart } from '../../modules/candles/resolutions.js';
import { BaseResolution } from '../../modules/instruments/instruments.types.js';
import { KNOWN_POINT_VALUES } from '../../modules/instruments/pointValues.js';
import { Candle, MarketDataConnector, SymbolSearchResult } from '../types.js';
import { HttpError, withRetry } from '../withRetry.js';

const BASE_URL = 'https://hist.databento.com/v0';

// CME Globex MDP 3.0 -- the only Databento dataset this connector talks to. Covers every CME
// Group futures product (equity index, FX, rates, metals, energy, ag/livestock), so the curated
// root list below (reused from pointValues.ts's known CME contracts) is a fair proxy for "things
// this dataset actually has", not a random guess.
const DATASET = 'GLBX.MDP3';

// Front-month continuous contract, ranked by calendar (nearest expiration), e.g. "MES.c.0".
// Databento resolves the roll itself server-side -- no in-house roll/stitching logic needed here,
// unlike ibConnector's CONTFUT handling.
const CONTINUOUS_FRONT_MONTH_SUFFIX = '.c.0';
const STYPE_IN = 'continuous';

// Databento has no native 5-minute OHLCV schema (only 1s/1m/1h/1d) -- 5-minute bars are built here
// by aggregating 1-minute bars client-side, bucketed the same epoch-aligned way the rest of the
// system derives coarser resolutions in SQL (see floorToBucketStart/candleStore.ts), rather than
// requesting a resolution Databento can't actually serve.
const RESOLUTION_TO_SCHEMA: Partial<Record<BaseResolution, string>> = {
  '1_minute': 'ohlcv-1m',
  '1_hour': 'ohlcv-1h',
  '1_day': 'ohlcv-1d'
};

// How far back to look when fetching "the last couple of bars" for fetchLatestCandle -- wide
// enough to comfortably span a weekend/holiday close even though CME futures trade near-
// continuously, since a fixed bar-count lookback isn't available (Databento's `limit` truncates
// from the *start* of a range, not the end).
const LATEST_LOOKBACK_MS: Partial<Record<BaseResolution, number>> = {
  '1_minute': 10 * 60_000,
  '1_hour': 6 * 60 * 60_000,
  '1_day': 10 * 24 * 60 * 60_000
};
const LATEST_LOOKBACK_MS_FOR_FIVE_MINUTE = 20 * 60_000;

// Fixed-point price scale used throughout Databento Binary Encoding (DBN): every integer price
// unit is 1e-9 of the real price. See https://databento.com/docs/standards-and-conventions.
const PRICE_SCALE = 1_000_000_000;

// The dataset's own overall available range almost never changes within a process's lifetime, so
// this can afford a much longer TTL than the per-registration caches other connectors use for a
// genuinely per-symbol lookup (Yahoo's firstTradeDate, IB's head timestamp) -- this call is the
// same regardless of which symbol/resolution is being registered.
const DATASET_RANGE_CACHE_TTL_MS = 5 * 60_000;
let datasetRangeCache: { start: Date; expiresAt: number } | null = null;

const encodeContinuousSymbol = (root: string): string => `${root}${CONTINUOUS_FRONT_MONTH_SUFFIX}`;

const getApiKey = (): string => {
  if (!env.DATABENTO_API_KEY) {
    throw new Error('DATABENTO_API_KEY is not set -- required to use the databento connector');
  }
  return env.DATABENTO_API_KEY;
};

const basicAuthHeader = (apiKey: string): string => `Basic ${Buffer.from(`${apiKey}:`).toString('base64')}`;

interface DatabentoErrorDetail {
  case?: string;
  message: string;
  payload?: { available_end?: string };
}

/** Databento's own JSON error shape (`{"detail": {"case", "message", "payload", ...}}`) carries
 * structured info plain HttpError can't -- notably `payload.available_end` on a
 * "data_end_after_available_end" error, which fetchOhlcvRange below needs to recover from. Still
 * extends HttpError so withRetry's existing 429/5xx retry check keeps working unmodified. */
class DatabentoApiError extends HttpError {
  constructor(
    status: number,
    public detail: DatabentoErrorDetail
  ) {
    super(detail.message, status);
    this.name = 'DatabentoApiError';
  }
}

/** Databento's HTTP API takes a POST with a form-encoded body for timeseries.get_range and a GET
 * with query params for metadata endpoints -- both authenticated with HTTP Basic auth, API key as
 * the username and an empty password (matching the official Python/Rust clients, there is no
 * official Node client to depend on instead). Returns the raw response body: get_range's
 * encoding=json response is newline-delimited JSON (JSON Lines), not a single JSON document, so
 * callers parse it themselves rather than this helper assuming one shape. */
const databentoRequest = async (
  path: string,
  params: Record<string, string>,
  method: 'GET' | 'POST'
): Promise<string> => {
  const apiKey = getApiKey();

  return withRetry(async () => {
    const init: RequestInit = {
      method,
      headers: { Authorization: basicAuthHeader(apiKey) }
    };

    let url = `${BASE_URL}${path}`;
    if (method === 'GET') {
      url += `?${new URLSearchParams(params).toString()}`;
    } else {
      init.headers = { ...init.headers, 'Content-Type': 'application/x-www-form-urlencoded' };
      init.body = new URLSearchParams(params).toString();
    }

    const res = await fetch(url, init);
    if (!res.ok) {
      const body = await res.text();
      // Databento's own errors are JSON (`{"detail": {...}}`); an infra-level failure in front of
      // it (a proxy's plain-text 502, etc.) won't parse -- fall back to a plain HttpError instead
      // of letting that throw mask the real (retryable, per isRetryable) HTTP status.
      const detail = (() => {
        try {
          return (JSON.parse(body) as { detail?: DatabentoErrorDetail }).detail;
        } catch {
          return undefined;
        }
      })();
      throw detail
        ? new DatabentoApiError(res.status, detail)
        : new HttpError(`${method} ${path} -> ${res.status}: ${body}`, res.status);
    }
    return res.text();
  });
};

interface DatabentoOhlcvRecord {
  hd: { ts_event: string };
  open: string;
  high: string;
  low: string;
  close: string;
  volume: string;
}

const parseNdjson = (text: string): DatabentoOhlcvRecord[] =>
  text
    .split('\n')
    .filter(line => line.trim().length > 0)
    .map(line => JSON.parse(line) as DatabentoOhlcvRecord);

// ts_event is nanoseconds since the Unix epoch, serialized as a string specifically to avoid the
// float precision loss a plain JSON number would suffer at that magnitude (it exceeds
// Number.MAX_SAFE_INTEGER) -- convert via BigInt, only dropping to Number once we're down to
// millisecond precision, which comfortably fits.
const parseTsEventToDate = (tsEventNs: string): Date => new Date(Number(BigInt(tsEventNs) / 1_000_000n));

const toCandle = (record: DatabentoOhlcvRecord): Candle => ({
  timeOpen: parseTsEventToDate(record.hd.ts_event),
  open: Number(record.open) / PRICE_SCALE,
  high: Number(record.high) / PRICE_SCALE,
  low: Number(record.low) / PRICE_SCALE,
  close: Number(record.close) / PRICE_SCALE,
  volume: Number(record.volume)
});

const requestOhlcvRange = (symbol: string, schema: string, from: Date, to: Date): Promise<string> =>
  databentoRequest(
    '/timeseries.get_range',
    {
      dataset: DATASET,
      symbols: symbol,
      schema,
      stype_in: STYPE_IN,
      start: from.toISOString(),
      end: to.toISOString(),
      encoding: 'json'
    },
    'POST'
  );

/** Databento's Historical API has real ingestion lag (observed: same-day data can be entirely
 * unavailable yet) -- unlike Yahoo/Binance/IB, which just return whatever's actually available up
 * to "now", it rejects the whole request with a 422 if `end` is past what's actually available --
 * observed in practice under at least two different `case` values (`data_end_after_available_end`
 * for plain ingestion lag, and `dataset_unavailable_range` when the account's subscription tier
 * only licenses CME data up to some delay behind real time), both carrying the same
 * `payload.available_end` recovery hint. That would otherwise hard-fail every "fetch up to now"
 * call (fetchLatestCandle, and any backfill whose `to` is "now"). Recovers by matching on that
 * shared hint rather than one specific `case` string -- clamping to it and retrying once; a
 * `clampedEnd` at or before `from` means there's genuinely nothing in range yet (a real "no new
 * data" answer, not an error to retry past). */
const availableEndFromError = (e: unknown): Date | null => {
  if (!(e instanceof DatabentoApiError)) {
    return null;
  }
  const availableEnd = e.detail.payload?.available_end;
  return typeof availableEnd === 'string' ? new Date(availableEnd) : null;
};

// Bounded rather than a single retry: observed in practice that ingestion lag and the account's
// subscription/licensing delay are two independent, progressively earlier boundaries -- clamping
// past the first can still land past the second. Each iteration can only shrink `end`, so this
// converges quickly regardless of how many such boundaries exist; the cap is just a backstop
// against an unexpected server response that never stops shrinking it.
const MAX_AVAILABLE_END_ADJUSTMENTS = 5;

const fetchOhlcvRange = async (symbol: string, schema: string, from: Date, to: Date): Promise<Candle[]> => {
  let end = to;
  for (let attempt = 0; ; attempt++) {
    try {
      return parseNdjson(await requestOhlcvRange(symbol, schema, from, end)).map(toCandle);
    } catch (e) {
      const clampedEnd = availableEndFromError(e);
      if (!clampedEnd || clampedEnd >= end || clampedEnd <= from || attempt >= MAX_AVAILABLE_END_ADJUSTMENTS) {
        throw e;
      }
      end = clampedEnd;
    }
  }
};

/** Builds 5-minute bars from 1-minute ones -- the input is assumed already time-ordered ascending
 * (true of every get_range response), which this relies on for `open`/`close` to land on the
 * bucket's actual first/last minute rather than requiring a separate sort. */
const aggregateToFiveMinute = (oneMinuteCandles: Candle[]): Candle[] => {
  const buckets = new Map<number, Candle[]>();
  for (const candle of oneMinuteCandles) {
    const bucketStart = floorToBucketStart(candle.timeOpen, '5_minute').getTime();
    const members = buckets.get(bucketStart);
    if (members) {
      members.push(candle);
    } else {
      buckets.set(bucketStart, [candle]);
    }
  }

  return [...buckets.entries()]
    .sort(([a], [b]) => a - b)
    .map(([bucketStart, members]) => ({
      timeOpen: new Date(bucketStart),
      open: members[0].open,
      high: Math.max(...members.map(c => c.high)),
      low: Math.min(...members.map(c => c.low)),
      close: members[members.length - 1].close,
      volume: members.reduce((sum, c) => sum + c.volume, 0)
    }));
};

const getDatasetStart = async (): Promise<Date> => {
  if (datasetRangeCache && datasetRangeCache.expiresAt > Date.now()) {
    return datasetRangeCache.start;
  }

  const text = await databentoRequest('/metadata.get_dataset_range', { dataset: DATASET }, 'GET');
  const { start } = JSON.parse(text) as { start: string; end: string };
  const startDate = new Date(start);
  datasetRangeCache = { start: startDate, expiresAt: Date.now() + DATASET_RANGE_CACHE_TTL_MS };
  return startDate;
};

export const databentoConnector: MarketDataConnector = {
  source: 'databento',

  async searchSymbols(query: string): Promise<SymbolSearchResult[]> {
    // Databento's historical API has no fuzzy "search the catalog" endpoint (symbology.resolve
    // only maps a *known* symbol you already have, it doesn't discover one) -- so this searches a
    // curated list of CME Group futures roots instead, reusing pointValues.ts's known-contracts
    // list since every one of those already trades on CME Globex (this connector's one dataset).
    const upperQuery = query.toUpperCase();
    return Object.keys(KNOWN_POINT_VALUES)
      .filter(root => root.includes(upperQuery))
      .slice(0, 25)
      .map(root => ({
        symbol: encodeContinuousSymbol(root),
        displaySymbol: root,
        assetClass: 'future' as const
      }));
  },

  // Neither param varies the answer: continuous-contract history spans the whole dataset
  // regardless of which resolution is being registered, unlike Yahoo's per-resolution depth
  // limits -- same rationale ibConnector uses for dropping its own unused `resolution` param.
  async getEarliestAvailableDate(): Promise<Date | null> {
    try {
      return await getDatasetStart();
    } catch {
      return null;
    }
  },

  async fetchHistoricalCandles({
    symbol,
    resolution,
    from,
    to
  }): Promise<{ resolution: BaseResolution; candles: Candle[] }> {
    if (resolution === '5_minute') {
      const oneMinuteCandles = await fetchOhlcvRange(symbol, 'ohlcv-1m', from, to);
      return { resolution, candles: aggregateToFiveMinute(oneMinuteCandles) };
    }

    const candles = await fetchOhlcvRange(symbol, RESOLUTION_TO_SCHEMA[resolution] as string, from, to);
    return { resolution, candles };
  },

  async fetchLatestCandle({ symbol, resolution }): Promise<Candle | null> {
    const now = new Date();

    const closed =
      resolution === '5_minute'
        ? aggregateToFiveMinute(
            await fetchOhlcvRange(symbol, 'ohlcv-1m', new Date(now.getTime() - LATEST_LOOKBACK_MS_FOR_FIVE_MINUTE), now)
          )
        : await fetchOhlcvRange(
            symbol,
            RESOLUTION_TO_SCHEMA[resolution] as string,
            new Date(now.getTime() - (LATEST_LOOKBACK_MS[resolution] as number)),
            now
          );

    if (closed.length === 0) {
      return null;
    }

    // The last bar in an end=now request can still be the in-progress one; drop it unless it's
    // the only bar available -- same convention as binanceConnector/yahooConnector/ibConnector.
    return closed.length > 1 ? closed[closed.length - 2] : closed[closed.length - 1];
  }
};
