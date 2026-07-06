import { ObjectId } from 'mongodb';
import { describe, expect, it } from 'vitest';

import { InstrumentDoc, toPublicInstrument } from './instruments.types.js';

const baseDoc = (overrides: Partial<InstrumentDoc> = {}): InstrumentDoc => ({
  _id: new ObjectId(),
  source: 'binance',
  assetClass: 'spot',
  sourceSymbol: 'BTCUSDT',
  displaySymbol: 'BTC/USDT',
  baseResolution: '1_hour',
  calendarVenue: 'CRYPTO_24_7',
  cacheHistoricalPrices: true,
  cacheLivePrices: true,
  cacheFrom: null,
  cacheTo: null,
  status: 'pending',
  createdAt: new Date('2026-01-01T00:00:00.000Z'),
  updatedAt: new Date('2026-01-01T00:00:00.000Z'),
  ...overrides
});

describe('toPublicInstrument', () => {
  it('maps dates to ISO strings and nulls to null', () => {
    const doc = baseDoc({
      cacheFrom: new Date('2026-01-01T00:00:00.000Z'),
      cacheTo: new Date('2026-01-02T00:00:00.000Z')
    });

    const result = toPublicInstrument(doc);

    expect(result.id).toBe(doc._id.toHexString());
    expect(result.cacheFrom).toBe('2026-01-01T00:00:00.000Z');
    expect(result.cacheTo).toBe('2026-01-02T00:00:00.000Z');
  });

  it('defaults earliestAvailableDates to an empty object when absent', () => {
    const doc = baseDoc();
    expect(toPublicInstrument(doc).earliestAvailableDates).toEqual({});
  });

  it('serializes only the resolutions actually present, as ISO strings', () => {
    const doc = baseDoc({
      earliestAvailableDates: {
        '1_hour': new Date('2020-01-01T00:00:00.000Z'),
        '1_day': new Date('2018-01-01T00:00:00.000Z')
        // '1_minute' deliberately omitted, as a source that couldn't answer for it would leave it
      }
    });

    const result = toPublicInstrument(doc);

    expect(result.earliestAvailableDates).toEqual({
      '1_hour': '2020-01-01T00:00:00.000Z',
      '1_day': '2018-01-01T00:00:00.000Z'
    });
    expect(result.earliestAvailableDates['1_minute']).toBeUndefined();
  });
});
