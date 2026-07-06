import { describe, expect, it } from 'vitest';

import {
  candlesTableName,
  coarserResolutionsThan,
  floorToBucketStart,
  RESOLUTION_MS,
  RESOLUTION_ORDER
} from './resolutions.js';

describe('candlesTableName', () => {
  it('prefixes the resolution with the table name', () => {
    expect(candlesTableName('1_minute')).toBe('candles__1_minute');
    expect(candlesTableName('1_day')).toBe('candles__1_day');
  });
});

describe('coarserResolutionsThan', () => {
  it('returns every resolution strictly coarser than the given one, in order', () => {
    expect(coarserResolutionsThan('1_minute')).toEqual(['15_minute', '1_hour', '1_day']);
    expect(coarserResolutionsThan('15_minute')).toEqual(['1_hour', '1_day']);
    expect(coarserResolutionsThan('1_day')).toEqual([]);
  });
});

describe('floorToBucketStart', () => {
  it('floors to the start of the hour', () => {
    const date = new Date('2026-01-01T10:45:30.000Z');
    expect(floorToBucketStart(date, '1_hour').toISOString()).toBe('2026-01-01T10:00:00.000Z');
  });

  it('floors to the start of the UTC day', () => {
    const date = new Date('2026-01-01T10:45:30.000Z');
    expect(floorToBucketStart(date, '1_day').toISOString()).toBe('2026-01-01T00:00:00.000Z');
  });

  it('is a no-op for a timestamp already on a bucket boundary', () => {
    const date = new Date('2026-01-01T10:00:00.000Z');
    expect(floorToBucketStart(date, '1_hour').getTime()).toBe(date.getTime());
  });

  it('agrees with RESOLUTION_MS for every resolution', () => {
    const date = new Date('2026-03-05T13:37:42.123Z');
    for (const resolution of RESOLUTION_ORDER) {
      const floored = floorToBucketStart(date, resolution);
      expect(floored.getTime() % RESOLUTION_MS[resolution]).toBe(0);
      expect(floored.getTime()).toBeLessThanOrEqual(date.getTime());
    }
  });
});
