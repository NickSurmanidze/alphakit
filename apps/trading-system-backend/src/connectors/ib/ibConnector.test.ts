import { SecType } from '@stoqey/ib';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const getContractDetails = vi.fn();
const getMatchingSymbols = vi.fn();

vi.mock('./client.js', () => ({
  getIbClient: () => ({ getContractDetails, getMatchingSymbols })
}));
// Real rate limiter enforces a real 10.5s minimum spacing (see rateLimiter.ts) -- irrelevant to
// this file's concern (symbol resolution) and already covered by rateLimiter.test.ts on its own.
vi.mock('./rateLimiter.js', () => ({
  ibRateLimited: (fn: () => unknown) => fn()
}));

const { ibConnector } = await import('./ibConnector.js');

describe('ibConnector.searchSymbols', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    getMatchingSymbols.mockResolvedValue([]);
  });

  it('resolves full-size CME FX futures by currency code, not the popular ticker', async () => {
    // Regression test: IB's `symbol` field for legacy full-size FX futures is the ISO currency
    // code (EUR) -- the popular ticker (6E) is only the *localSymbol*. Querying reqContractDetails
    // with "6E" as `symbol` fails outright with IB error 200 "No security definition has been
    // found for the request". This broke registration + all historical/live data fetching for
    // every full-size CME FX future (6A/6B/6C/6E/6J/6S) until this alias was added.
    getContractDetails.mockResolvedValue([
      {
        contract: { symbol: 'EUR', exchange: 'CME', currency: 'USD', secType: SecType.CONTFUT },
        longName: 'European Monetary Union Euro'
      }
    ]);

    const results = await ibConnector.searchSymbols('6E');

    expect(getContractDetails).toHaveBeenCalledWith(
      expect.objectContaining({ symbol: 'EUR', secType: SecType.CONTFUT, exchange: 'CME' })
    );
    expect(results[0]).toMatchObject({
      symbol: 'EUR@CME@CONTFUT',
      displaySymbol: '6E — European Monetary Union Euro',
      assetClass: 'future'
    });
  });

  it('queries the symbol as-is for a product without a known alias', async () => {
    getContractDetails.mockResolvedValue([
      { contract: { symbol: 'ES', exchange: 'CME', currency: 'USD', secType: SecType.CONTFUT }, longName: 'E-mini S&P 500' }
    ]);

    await ibConnector.searchSymbols('ES');

    expect(getContractDetails).toHaveBeenCalledWith(expect.objectContaining({ symbol: 'ES' }));
  });

  it('does not alias micro FX futures, which use the popular ticker as their real symbol', async () => {
    getContractDetails.mockResolvedValue([
      { contract: { symbol: 'M6E', exchange: 'CME', currency: 'USD', secType: SecType.CONTFUT }, longName: 'Micro EUR/USD Futures' }
    ]);

    await ibConnector.searchSymbols('M6E');

    expect(getContractDetails).toHaveBeenCalledWith(expect.objectContaining({ symbol: 'M6E' }));
  });
});
