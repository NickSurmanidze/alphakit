import { beforeEach, describe, expect, it, vi } from 'vitest';

const chartMock = vi.fn();
const searchMock = vi.fn();

vi.mock('yahoo-finance2', () => ({
  default: class {
    chart = chartMock;
    search = searchMock;
  }
}));

// Bypass retry/backoff and rate-limiting so tests exercise only yahooConnector's own branching.
vi.mock('../withRetry.js', () => ({
  withRetry: (fn: () => unknown) => fn()
}));
vi.mock('./rateLimiter.js', () => ({
  yahooRateLimited: (fn: () => unknown) => fn()
}));

const { yahooConnector } = await import('./yahooConnector.js');

const FIRST_TRADE_DATE = new Date('2018-01-01T00:00:00.000Z');

const metaChartResponse = (firstTradeDate: Date | null) => ({
  meta: { firstTradeDate },
  quotes: []
});

const quotesChartResponse = (dates: Date[]) => ({
  meta: {},
  quotes: dates.map(date => ({ date, open: 1, high: 2, low: 0.5, close: 1.5, volume: 100 }))
});

describe('yahooConnector.getEarliestAvailableDate', () => {
  // Each case uses its own symbol -- the module-level firstTradeDate cache (keyed by symbol) is
  // deliberately shared across the whole batch of getEarliestAvailableDate calls a single
  // registration makes, so it persists across these tests too unless they don't collide.
  beforeEach(() => {
    chartMock.mockReset();
    searchMock.mockReset();
  });

  it('returns firstTradeDate directly for 1_day', async () => {
    chartMock.mockResolvedValueOnce(metaChartResponse(FIRST_TRADE_DATE));

    const result = await yahooConnector.getEarliestAvailableDate('DAILY1', '1_day');

    expect(result).toEqual(FIRST_TRADE_DATE);
    expect(chartMock).toHaveBeenCalledTimes(1);
    expect(chartMock.mock.calls[0][1]).toMatchObject({ interval: '1d' });
  });

  it('returns null when Yahoo has no firstTradeDate for the symbol', async () => {
    chartMock.mockResolvedValueOnce(metaChartResponse(null));

    const result = await yahooConnector.getEarliestAvailableDate('UNKNOWN1', '1_hour');

    expect(result).toBeNull();
    // No point trying an intraday range if there's no ceiling date at all.
    expect(chartMock).toHaveBeenCalledTimes(1);
  });

  it('for intraday resolutions, tries the full range first and uses what Yahoo actually returns', async () => {
    const earliestHourly = new Date('2024-06-01T00:00:00.000Z');
    chartMock
      .mockResolvedValueOnce(metaChartResponse(FIRST_TRADE_DATE)) // meta lookup
      .mockResolvedValueOnce(quotesChartResponse([earliestHourly])); // full-range 1h attempt succeeds

    const result = await yahooConnector.getEarliestAvailableDate('HOURLY1', '1_hour');

    expect(result).toEqual(earliestHourly);
    expect(chartMock).toHaveBeenCalledTimes(2);
    expect(chartMock.mock.calls[1][1]).toMatchObject({ period1: FIRST_TRADE_DATE, interval: '1h' });
  });

  it('falls back to the known-safe lookback window when the full-range request is rejected', async () => {
    const earliestWithinFallbackWindow = new Date('2026-06-30T00:00:00.000Z');
    chartMock
      .mockResolvedValueOnce(metaChartResponse(FIRST_TRADE_DATE)) // meta lookup
      .mockRejectedValueOnce(new Error('range too large')) // full-range 1h attempt rejected
      .mockResolvedValueOnce(quotesChartResponse([earliestWithinFallbackWindow])); // fallback window

    const result = await yahooConnector.getEarliestAvailableDate('HOURLY2', '1_hour');

    expect(result).toEqual(earliestWithinFallbackWindow);
    expect(chartMock).toHaveBeenCalledTimes(3);
    // Fallback window should NOT start from firstTradeDate -- that's exactly what got rejected.
    expect(chartMock.mock.calls[2][1].period1).not.toEqual(FIRST_TRADE_DATE);
  });

  it('regression: the 1-hour fallback window stays strictly under 730 days, not exactly 730', async () => {
    // Yahoo rejected a request spanning *exactly* 730 days ("must be within the last 730 days"),
    // even though that's the documented cap -- this pins the fallback to a real margin under it
    // so we never again build a request landing right on that rejected boundary.
    chartMock
      .mockResolvedValueOnce(metaChartResponse(FIRST_TRADE_DATE))
      .mockRejectedValueOnce(new Error('range too large'))
      .mockResolvedValueOnce(quotesChartResponse([new Date('2026-01-01T00:00:00.000Z')]));

    await yahooConnector.getEarliestAvailableDate('HOURLYBOUNDARY', '1_hour');
    const after = Date.now();

    const fallbackCall = chartMock.mock.calls[2][1];
    const spanMs = after - (fallbackCall.period1 as Date).getTime();
    const days = spanMs / (24 * 60 * 60 * 1000);
    expect(days).toBeLessThan(730);
    expect(days).toBeGreaterThan(700); // still a real multi-year-ish window, not accidentally tiny
  });

  it('caches firstTradeDate across resolutions for the same symbol within one lookup batch', async () => {
    chartMock
      .mockResolvedValueOnce(metaChartResponse(FIRST_TRADE_DATE)) // meta lookup (1_minute call)
      .mockResolvedValueOnce(quotesChartResponse([new Date('2026-01-01T00:00:00.000Z')])) // 1_minute range
      .mockResolvedValueOnce(quotesChartResponse([new Date('2024-01-01T00:00:00.000Z')])); // 1_hour range

    await yahooConnector.getEarliestAvailableDate('CACHETEST', '1_minute');
    await yahooConnector.getEarliestAvailableDate('CACHETEST', '1_hour');

    // 2 calls per resolution (meta + range) would be 4 total; caching the meta lookup brings it to 3.
    expect(chartMock).toHaveBeenCalledTimes(3);
  });
});
