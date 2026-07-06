import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { binanceConnector } from './binanceConnector.js';

const jsonResponse = (body: unknown) =>
  Promise.resolve({
    ok: true,
    json: () => Promise.resolve(body)
  } as Response);

describe('binanceConnector.getEarliestAvailableDate', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('queries the exact interval for the requested resolution, not always daily', async () => {
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock.mockReturnValue(jsonResponse([[1500000000000, '1', '2', '0.5', '1.5', '100', 0, '', 0, '', '', '']]));

    await binanceConnector.getEarliestAvailableDate('BTCUSDT', '1_hour');

    const calledUrl = new URL(fetchMock.mock.calls[0][0] as string);
    expect(calledUrl.searchParams.get('interval')).toBe('1h');
    expect(calledUrl.searchParams.get('startTime')).toBe('0');
    expect(calledUrl.searchParams.get('limit')).toBe('1');
  });

  it('returns the open time of the first kline as a Date', async () => {
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    const openTimeMs = 1502928000000; // 2017-08-17
    fetchMock.mockReturnValue(jsonResponse([[openTimeMs, '1', '2', '0.5', '1.5', '100', 0, '', 0, '', '', '']]));

    const result = await binanceConnector.getEarliestAvailableDate('BTCUSDT', '1_day');

    expect(result).toEqual(new Date(openTimeMs));
  });

  it('returns null when Binance has no data for the symbol at that interval', async () => {
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock.mockReturnValue(jsonResponse([]));

    const result = await binanceConnector.getEarliestAvailableDate('NOSUCHPAIR', '1_minute');

    expect(result).toBeNull();
  });
});
