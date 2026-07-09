import { ObjectId } from 'mongodb';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { InstrumentDoc } from '../../modules/instruments/instruments.types.js';
import type { Context } from '../context.js';

const getConnector = vi.fn();
const deleteAllCandles = vi.fn();
const deleteCandlesForResolution = vi.fn();
const getLatestCloses = vi.fn();
const addInstrumentResolution = vi.fn();
const createInstrument = vi.fn();
const deleteInstrument = vi.fn();
const getInstrumentById = vi.fn();
const instrumentExists = vi.fn();
const listInstruments = vi.fn();
const resetInstrumentResolution = vi.fn();
const updateInstrumentDescription = vi.fn();
const updateInstrumentFlags = vi.fn();
const updateResolutionCoverage = vi.fn();
const addQueueJob = vi.fn();

vi.mock('../../connectors/registry.js', () => ({ getConnector }));
vi.mock('../../modules/candles/candleStore.js', () => ({ deleteAllCandles, deleteCandlesForResolution, getLatestCloses }));
vi.mock('../../modules/instruments/instruments.repository.js', () => ({
  addInstrumentResolution,
  createInstrument,
  deleteInstrument,
  getInstrumentById,
  instrumentExists,
  listInstruments,
  resetInstrumentResolution,
  updateInstrumentDescription,
  updateInstrumentFlags,
  updateResolutionCoverage
}));
vi.mock('../../queue/queues.js', () => ({ addQueueJob }));

const { instrumentsRouter } = await import('./instruments.router.js');

const ctx = { req: {}, res: {}, user: { id: 'user1', email: 'test@example.com' } } as unknown as Context;
const caller = instrumentsRouter.createCaller(ctx);

const makeInstrument = (overrides: Partial<InstrumentDoc> = {}): InstrumentDoc => ({
  _id: new ObjectId(),
  source: 'binance',
  assetClass: 'spot',
  sourceSymbol: 'BTCUSDT',
  displaySymbol: 'BTC/USDT',
  collectedResolutions: {
    '1_hour': { cacheFrom: null, cacheTo: null, status: 'finished', earliestAvailableDate: null }
  },
  calendarVenue: 'CRYPTO_24_7',
  cacheHistoricalPrices: true,
  cacheLivePrices: true,
  pointValue: 1,
  createdAt: new Date(),
  updatedAt: new Date(),
  ...overrides
});

beforeEach(() => {
  // resetAllMocks (not clearAllMocks) so a leftover queued mockResolvedValueOnce from one test
  // can never leak into the next test's first call to the same mock.
  vi.resetAllMocks();
});

describe('instrumentsRouter.list', () => {
  it('merges each instrument\'s latest close, keyed by its own finest collected resolution', async () => {
    const withPrice = makeInstrument({
      _id: new ObjectId('000000000000000000000001'),
      collectedResolutions: {
        '5_minute': { cacheFrom: null, cacheTo: null, status: 'finished', earliestAvailableDate: null },
        '1_hour': { cacheFrom: null, cacheTo: null, status: 'finished', earliestAvailableDate: null }
      }
    });
    const stillBackfilling = makeInstrument({ _id: new ObjectId('000000000000000000000002') });
    listInstruments.mockResolvedValue([withPrice, stillBackfilling]);
    getLatestCloses.mockResolvedValue(new Map([[withPrice._id.toHexString(), 123.45]]));

    const result = await caller.list();

    expect(getLatestCloses).toHaveBeenCalledWith([
      { instrumentId: withPrice._id.toHexString(), resolution: '5_minute' },
      { instrumentId: stillBackfilling._id.toHexString(), resolution: '1_hour' }
    ]);
    expect(result.find(i => i.id === withPrice._id.toHexString())?.latestPrice).toBe(123.45);
    expect(result.find(i => i.id === stillBackfilling._id.toHexString())?.latestPrice).toBeNull();
  });
});

describe('instrumentsRouter.register', () => {
  it('rejects a duplicate before doing any earliest-date lookups', async () => {
    instrumentExists.mockResolvedValue(true);
    const connector = { getEarliestAvailableDate: vi.fn() };
    getConnector.mockReturnValue(connector);

    await expect(
      caller.register({
        source: 'binance',
        assetClass: 'spot',
        sourceSymbol: 'BTCUSDT',
        displaySymbol: 'BTC/USDT',
        calendarVenue: 'CRYPTO_24_7'
      })
    ).rejects.toMatchObject({ code: 'CONFLICT' });

    expect(connector.getEarliestAvailableDate).not.toHaveBeenCalled();
    expect(createInstrument).not.toHaveBeenCalled();
  });

  it('looks up the earliest-available date only for the chosen resolutions, not every resolution', async () => {
    instrumentExists.mockResolvedValue(false);
    const earliest = new Date('2017-08-17T00:00:00.000Z');
    const connector = { getEarliestAvailableDate: vi.fn().mockResolvedValue(earliest) };
    getConnector.mockReturnValue(connector);
    createInstrument.mockResolvedValue(makeInstrument());
    getInstrumentById.mockResolvedValue(makeInstrument());

    await caller.register({
      source: 'binance',
      assetClass: 'spot',
      sourceSymbol: 'BTCUSDT',
      displaySymbol: 'BTC/USDT',
      resolutions: ['1_day'],
      calendarVenue: 'CRYPTO_24_7'
    });

    // Regression test: this used to loop over 1_minute/1_hour/1_day unconditionally, so
    // registering at "1 day" for an old Yahoo symbol still probed "1 hour" behind the scenes and
    // could fail the whole registration over a resolution nobody asked for.
    expect(connector.getEarliestAvailableDate).toHaveBeenCalledTimes(1);
    expect(connector.getEarliestAvailableDate).toHaveBeenCalledWith('BTCUSDT', '1_day');
    expect(createInstrument).toHaveBeenCalledWith(
      expect.objectContaining({ resolutions: { '1_day': { earliestAvailableDate: earliest } } })
    );
    expect(addQueueJob).toHaveBeenCalledTimes(1);
  });

  it('does not fail registration when the earliest-date lookup throws', async () => {
    instrumentExists.mockResolvedValue(false);
    const connector = { getEarliestAvailableDate: vi.fn().mockRejectedValue(new Error('range too large')) };
    getConnector.mockReturnValue(connector);
    createInstrument.mockResolvedValue(makeInstrument());
    getInstrumentById.mockResolvedValue(makeInstrument());

    await expect(
      caller.register({
        source: 'yahoo',
        assetClass: 'equity',
        sourceSymbol: 'VOO',
        displaySymbol: 'VOO',
        resolutions: ['1_hour'],
        calendarVenue: 'NYSE'
      })
    ).resolves.toBeTruthy();

    expect(createInstrument).toHaveBeenCalledWith(
      expect.objectContaining({ resolutions: { '1_hour': { earliestAvailableDate: null } } })
    );
    expect(addQueueJob).toHaveBeenCalledTimes(1);
  });

  it('backfills from the earliest available date at each chosen resolution when requested', async () => {
    instrumentExists.mockResolvedValue(false);
    const earliestHourly = new Date('2020-01-01T00:00:00.000Z');
    const connector = { getEarliestAvailableDate: vi.fn().mockResolvedValue(earliestHourly) };
    getConnector.mockReturnValue(connector);
    createInstrument.mockResolvedValue(makeInstrument());
    getInstrumentById.mockResolvedValue(makeInstrument());

    await caller.register({
      source: 'binance',
      assetClass: 'spot',
      sourceSymbol: 'BTCUSDT',
      displaySymbol: 'BTC/USDT',
      resolutions: ['1_hour'],
      calendarVenue: 'CRYPTO_24_7',
      backfillFullHistory: true
    });

    const enqueuedJob = addQueueJob.mock.calls[0][0];
    expect(enqueuedJob.job.data.resolution).toBe('1_hour');
    expect(enqueuedJob.job.data.from).toBe(earliestHourly.toISOString());
  });

  it('enqueues one backfill job per requested resolution', async () => {
    instrumentExists.mockResolvedValue(false);
    const connector = { getEarliestAvailableDate: vi.fn().mockResolvedValue(null) };
    getConnector.mockReturnValue(connector);
    createInstrument.mockResolvedValue(makeInstrument());
    getInstrumentById.mockResolvedValue(makeInstrument());

    await caller.register({
      source: 'ib',
      assetClass: 'future',
      sourceSymbol: 'NQ',
      displaySymbol: 'NQ',
      resolutions: ['5_minute', '1_day'],
      calendarVenue: 'FUTURES_NEAR_CONTINUOUS'
    });

    expect(addQueueJob).toHaveBeenCalledTimes(2);
    const resolutions = addQueueJob.mock.calls.map(call => call[0].job.data.resolution);
    expect(resolutions).toEqual(['5_minute', '1_day']);
  });
});

describe('instrumentsRouter.resetResolution', () => {
  it('throws NOT_FOUND for a non-existent instrument', async () => {
    getInstrumentById.mockResolvedValue(null);

    await expect(caller.resetResolution({ id: 'missing', resolution: '1_day' })).rejects.toMatchObject({
      code: 'NOT_FOUND'
    });
  });

  it('throws BAD_REQUEST when the instrument does not collect that resolution', async () => {
    const instrument = makeInstrument();
    getInstrumentById.mockResolvedValue(instrument);

    await expect(
      caller.resetResolution({ id: instrument._id.toHexString(), resolution: '1_day' })
    ).rejects.toMatchObject({ code: 'BAD_REQUEST' });

    expect(deleteCandlesForResolution).not.toHaveBeenCalled();
  });

  it('wipes and re-backfills only the targeted resolution', async () => {
    const cachedEarliest = new Date('2018-01-01T00:00:00.000Z');
    const instrument = makeInstrument({
      collectedResolutions: {
        '1_hour': { cacheFrom: new Date(), cacheTo: new Date(), status: 'finished', earliestAvailableDate: cachedEarliest }
      }
    });
    getInstrumentById.mockResolvedValue(instrument);

    await caller.resetResolution({ id: instrument._id.toHexString(), resolution: '1_hour' });

    expect(deleteCandlesForResolution).toHaveBeenCalledWith(instrument._id.toHexString(), '1_hour');
    expect(resetInstrumentResolution).toHaveBeenCalledWith(instrument._id.toHexString(), '1_hour');
    expect(addQueueJob).toHaveBeenCalledTimes(1);
    expect(addQueueJob.mock.calls[0][0].job.data.resolution).toBe('1_hour');
    expect(addQueueJob.mock.calls[0][0].job.data.from).toBe(cachedEarliest.toISOString());
  });
});

describe('instrumentsRouter.addResolution', () => {
  it('throws NOT_FOUND for a non-existent instrument', async () => {
    getInstrumentById.mockResolvedValue(null);

    await expect(caller.addResolution({ id: 'missing', resolution: '1_day' })).rejects.toMatchObject({
      code: 'NOT_FOUND'
    });
  });

  it('is a no-op when the resolution is already collected', async () => {
    const instrument = makeInstrument();
    getInstrumentById.mockResolvedValue(instrument);

    await caller.addResolution({ id: instrument._id.toHexString(), resolution: '1_hour' });

    expect(addInstrumentResolution).not.toHaveBeenCalled();
    expect(addQueueJob).not.toHaveBeenCalled();
  });

  it('adds a new resolution and enqueues its backfill', async () => {
    const instrument = makeInstrument();
    getInstrumentById.mockResolvedValueOnce(instrument).mockResolvedValueOnce(instrument);
    const earliest = new Date('2015-01-01T00:00:00.000Z');
    const connector = { getEarliestAvailableDate: vi.fn().mockResolvedValue(earliest) };
    getConnector.mockReturnValue(connector);

    await caller.addResolution({ id: instrument._id.toHexString(), resolution: '1_day' });

    expect(addInstrumentResolution).toHaveBeenCalledWith(instrument._id.toHexString(), '1_day', earliest);
    expect(addQueueJob).toHaveBeenCalledTimes(1);
    expect(addQueueJob.mock.calls[0][0].job.data.resolution).toBe('1_day');
  });
});

describe('instrumentsRouter.refresh', () => {
  it('throws NOT_FOUND for a non-existent instrument and never enqueues a job', async () => {
    getInstrumentById.mockResolvedValue(null);

    await expect(caller.refresh({ id: 'missing' })).rejects.toMatchObject({ code: 'NOT_FOUND' });
    expect(addQueueJob).not.toHaveBeenCalled();
  });

  it('enqueues a refreshLatestCandles job for an existing instrument', async () => {
    const instrument = makeInstrument();
    getInstrumentById.mockResolvedValue(instrument);

    await caller.refresh({ id: instrument._id.toHexString() });

    expect(addQueueJob).toHaveBeenCalledTimes(1);
    expect(addQueueJob.mock.calls[0][0].job.data).toEqual({
      instrumentId: instrument._id.toHexString(),
      resolution: '1_hour'
    });
  });
});

describe('instrumentsRouter.delete', () => {
  it('deletes candle data before deleting the instrument document', async () => {
    const callOrder: string[] = [];
    deleteAllCandles.mockImplementation(async () => {
      callOrder.push('deleteAllCandles');
    });
    deleteInstrument.mockImplementation(async () => {
      callOrder.push('deleteInstrument');
    });

    await caller.delete({ id: 'instrument1' });

    expect(callOrder).toEqual(['deleteAllCandles', 'deleteInstrument']);
  });
});
