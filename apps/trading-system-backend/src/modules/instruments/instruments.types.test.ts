import { ObjectId } from 'mongodb';
import { describe, expect, it } from 'vitest';

import { InstrumentDoc, ResolutionCoverage, toPublicInstrument } from './instruments.types.js';

const baseDoc = (overrides: Partial<InstrumentDoc> = {}): InstrumentDoc => ({
  _id: new ObjectId(),
  source: 'binance',
  assetClass: 'spot',
  sourceSymbol: 'BTCUSDT',
  displaySymbol: 'BTC/USDT',
  collectedResolutions: {
    '1_hour': { cacheFrom: null, cacheTo: null, status: 'pending', earliestAvailableDate: null }
  },
  calendarVenue: 'CRYPTO_24_7',
  cacheHistoricalPrices: true,
  cacheLivePrices: true,
  pointValue: 1,
  createdAt: new Date('2026-01-01T00:00:00.000Z'),
  updatedAt: new Date('2026-01-01T00:00:00.000Z'),
  ...overrides
});

const coverage = (overrides: Partial<ResolutionCoverage> = {}): ResolutionCoverage => ({
  cacheFrom: null,
  cacheTo: null,
  status: 'pending',
  earliestAvailableDate: null,
  ...overrides
});

describe('toPublicInstrument', () => {
  it('maps dates to ISO strings and nulls to null', () => {
    const doc = baseDoc({
      collectedResolutions: {
        '1_hour': coverage({
          cacheFrom: new Date('2026-01-01T00:00:00.000Z'),
          cacheTo: new Date('2026-01-02T00:00:00.000Z')
        })
      }
    });

    const result = toPublicInstrument(doc);

    expect(result.id).toBe(doc._id.toHexString());
    expect(result.collectedResolutions['1_hour']?.cacheFrom).toBe('2026-01-01T00:00:00.000Z');
    expect(result.collectedResolutions['1_hour']?.cacheTo).toBe('2026-01-02T00:00:00.000Z');
  });

  it('defaults description to null when absent', () => {
    const doc = baseDoc();
    expect(toPublicInstrument(doc).description).toBeNull();
  });

  it('serializes only the resolutions actually collected, as ISO strings', () => {
    const doc = baseDoc({
      collectedResolutions: {
        '1_hour': coverage({ earliestAvailableDate: new Date('2020-01-01T00:00:00.000Z') }),
        '1_day': coverage({ earliestAvailableDate: new Date('2018-01-01T00:00:00.000Z') })
        // '1_minute' deliberately omitted, as a source that couldn't answer for it would leave it
      }
    });

    const result = toPublicInstrument(doc);

    expect(result.collectedResolutions['1_hour']?.earliestAvailableDate).toBe('2020-01-01T00:00:00.000Z');
    expect(result.collectedResolutions['1_day']?.earliestAvailableDate).toBe('2018-01-01T00:00:00.000Z');
    expect(result.collectedResolutions['1_minute']).toBeUndefined();
  });
});
