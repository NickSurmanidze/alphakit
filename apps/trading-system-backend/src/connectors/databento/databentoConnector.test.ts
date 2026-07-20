import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// env.ts parses process.env eagerly at import time, so tests can't just vi.stubEnv() around it --
// mock the module directly instead (same approach ibConnector.test.ts uses for its own env-backed
// dependency, client.js) and vi.resetModules()+vi.doMock() between cases to vary the API key.
const mockEnv = (apiKey: string | undefined) => {
  vi.doMock('../../env.js', () => ({ env: { DATABENTO_API_KEY: apiKey } }));
};

const importConnector = async () => {
  const mod = await import('./databentoConnector.js');
  return mod.databentoConnector;
};

const textResponse = (body: string) => Promise.resolve({ ok: true, text: () => Promise.resolve(body) } as Response);

const ndjsonLine = (fields: {
  tsEventNs: string;
  open: string;
  high: string;
  low: string;
  close: string;
  volume: string;
}): string =>
  JSON.stringify({
    hd: { ts_event: fields.tsEventNs, rtype: 32, publisher_id: 1, instrument_id: 1 },
    open: fields.open,
    high: fields.high,
    low: fields.low,
    close: fields.close,
    volume: fields.volume
  });

describe('databentoConnector', () => {
  beforeEach(() => {
    vi.resetModules();
    vi.stubGlobal('fetch', vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.doUnmock('../../env.js');
  });

  it('sends HTTP Basic auth with the API key as username and an empty password', async () => {
    mockEnv('test-key');
    const databentoConnector = await importConnector();
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock.mockReturnValue(textResponse(''));

    await databentoConnector.fetchHistoricalCandles({
      symbol: 'MES.c.0',
      resolution: '1_hour',
      from: new Date('2024-01-01T00:00:00Z'),
      to: new Date('2024-01-02T00:00:00Z')
    });

    const [, init] = fetchMock.mock.calls[0];
    const expectedAuth = `Basic ${Buffer.from('test-key:').toString('base64')}`;
    expect((init.headers as Record<string, string>).Authorization).toBe(expectedAuth);
  });

  it('scales fixed-point prices by 1e-9 and converts nanosecond timestamps to Dates', async () => {
    mockEnv('test-key');
    const databentoConnector = await importConnector();
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock.mockReturnValue(
      textResponse(
        ndjsonLine({
          tsEventNs: '1704067200000000000', // 2024-01-01T00:00:00Z
          open: '5900250000000',
          high: '5905000000000',
          low: '5895000000000',
          close: '5901000000000',
          volume: '1234'
        })
      )
    );

    const result = await databentoConnector.fetchHistoricalCandles({
      symbol: 'MES.c.0',
      resolution: '1_hour',
      from: new Date('2024-01-01T00:00:00Z'),
      to: new Date('2024-01-02T00:00:00Z')
    });

    expect(result.resolution).toBe('1_hour');
    expect(result.candles).toEqual([
      {
        timeOpen: new Date('2024-01-01T00:00:00Z'),
        open: 5900.25,
        high: 5905,
        low: 5895,
        close: 5901,
        volume: 1234
      }
    ]);
  });

  it('requests ohlcv-1m and aggregates into 5-minute buckets when resolution is 5_minute', async () => {
    mockEnv('test-key');
    const databentoConnector = await importConnector();
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;

    const minuteBar = (minute: number, close: number) =>
      ndjsonLine({
        tsEventNs: `${(1704067200 + minute * 60).toString()}000000000`,
        open: `${(close - 1) * 1_000_000_000}`,
        high: `${(close + 1) * 1_000_000_000}`,
        low: `${(close - 2) * 1_000_000_000}`,
        close: `${close * 1_000_000_000}`,
        volume: '100'
      });

    // Five 1-minute bars spanning one 5-minute bucket (00:00-00:04).
    fetchMock.mockReturnValue(textResponse([0, 1, 2, 3, 4].map(m => minuteBar(m, 5900 + m)).join('\n')));

    const result = await databentoConnector.fetchHistoricalCandles({
      symbol: 'MES.c.0',
      resolution: '5_minute',
      from: new Date('2024-01-01T00:00:00Z'),
      to: new Date('2024-01-01T00:05:00Z')
    });

    const [, init] = fetchMock.mock.calls[0];
    const body = new URLSearchParams(init.body as string);
    expect(body.get('schema')).toBe('ohlcv-1m');

    expect(result.resolution).toBe('5_minute');
    expect(result.candles).toHaveLength(1);
    expect(result.candles[0]).toMatchObject({
      timeOpen: new Date('2024-01-01T00:00:00Z'),
      open: 5899, // first bar's open (minute 0, close 5900, open = close - 1)
      close: 5904, // last bar's close (minute 4, close = 5904)
      high: 5905, // max high across bars (minute 4 high = 5904 + 1)
      low: 5898, // min low across bars (minute 0 low = 5900 - 2)
      volume: 500
    });
  });

  it('drops the still-forming last bar in fetchLatestCandle, keeping it only if nothing else is available', async () => {
    mockEnv('test-key');
    const databentoConnector = await importConnector();
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;

    const hourBar = (hour: number) =>
      ndjsonLine({
        tsEventNs: `${(1704067200 + hour * 3600).toString()}000000000`,
        open: '5900000000000',
        high: '5901000000000',
        low: '5899000000000',
        close: '5900500000000',
        volume: '10'
      });

    fetchMock.mockReturnValue(textResponse([hourBar(0), hourBar(1)].join('\n')));

    const result = await databentoConnector.fetchLatestCandle({ symbol: 'MES.c.0', resolution: '1_hour' });

    expect(result?.timeOpen).toEqual(new Date(1704067200 * 1000));
  });

  it('clamps `end` to the server-reported available_end and retries once the boundary is past `from`', async () => {
    // Observed against the real API: Databento rejects (422) a get_range whose `end` is past what
    // it actually has (ingestion lag, or -- as here -- an account's licensing delay), but tells
    // you exactly how far back to move `end` via `payload.available_end`.
    mockEnv('test-key');
    const databentoConnector = await importConnector();
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;

    const errorResponse = (detail: Record<string, unknown>) =>
      Promise.resolve({ ok: false, status: 422, text: () => Promise.resolve(JSON.stringify({ detail })) } as Response);

    fetchMock
      .mockReturnValueOnce(
        errorResponse({
          case: 'dataset_unavailable_range',
          message: 'not licensed yet',
          status_code: 422,
          payload: { available_end: '2024-01-01T12:00:00Z' }
        })
      )
      .mockReturnValueOnce(
        textResponse(
          ndjsonLine({
            tsEventNs: '1704067200000000000',
            open: '5900000000000',
            high: '5901000000000',
            low: '5899000000000',
            close: '5900500000000',
            volume: '10'
          })
        )
      );

    const result = await databentoConnector.fetchHistoricalCandles({
      symbol: 'MES.c.0',
      resolution: '1_day',
      from: new Date('2024-01-01T00:00:00Z'),
      to: new Date('2024-01-02T00:00:00Z')
    });

    expect(fetchMock).toHaveBeenCalledTimes(2);
    const secondCallBody = new URLSearchParams(fetchMock.mock.calls[1][1].body as string);
    expect(secondCallBody.get('end')).toBe('2024-01-01T12:00:00.000Z');
    expect(result.candles).toHaveLength(1);
  });

  it('gives up and surfaces the real error when the clamped available_end is at or before `from` (no data in range yet)', async () => {
    mockEnv('test-key');
    const databentoConnector = await importConnector();
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;

    fetchMock.mockReturnValue(
      Promise.resolve({
        ok: false,
        status: 422,
        text: () =>
          Promise.resolve(
            JSON.stringify({
              detail: {
                case: 'dataset_unavailable_range',
                message: 'not licensed yet',
                status_code: 422,
                payload: { available_end: '2023-12-31T00:00:00Z' }
              }
            })
          )
      } as Response)
    );

    await expect(
      databentoConnector.fetchHistoricalCandles({
        symbol: 'MES.c.0',
        resolution: '1_hour',
        from: new Date('2024-01-01T00:00:00Z'),
        to: new Date('2024-01-02T00:00:00Z')
      })
    ).rejects.toThrow('not licensed yet');
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('throws a clear error instead of calling out when DATABENTO_API_KEY is unset', async () => {
    mockEnv(undefined);
    const databentoConnector = await importConnector();
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;

    await expect(
      databentoConnector.fetchHistoricalCandles({
        symbol: 'MES.c.0',
        resolution: '1_day',
        from: new Date('2024-01-01T00:00:00Z'),
        to: new Date('2024-01-02T00:00:00Z')
      })
    ).rejects.toThrow('DATABENTO_API_KEY is not set');
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
