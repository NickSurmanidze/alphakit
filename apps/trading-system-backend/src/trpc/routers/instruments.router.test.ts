import { ObjectId } from 'mongodb';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { InstrumentDoc } from '../../modules/instruments/instruments.types.js';
import type { Context } from '../context.js';

const getConnector = vi.fn();
const deleteAllCandles = vi.fn();
const createInstrument = vi.fn();
const deleteInstrument = vi.fn();
const getInstrumentById = vi.fn();
const instrumentExists = vi.fn();
const listInstruments = vi.fn();
const updateInstrumentBaseResolution = vi.fn();
const updateInstrumentCoverage = vi.fn();
const updateInstrumentFlags = vi.fn();
const addQueueJob = vi.fn();

vi.mock('../../connectors/registry.js', () => ({ getConnector }));
vi.mock('../../modules/candles/candleStore.js', () => ({ deleteAllCandles }));
vi.mock('../../modules/instruments/instruments.repository.js', () => ({
  createInstrument,
  deleteInstrument,
  getInstrumentById,
  instrumentExists,
  listInstruments,
  updateInstrumentBaseResolution,
  updateInstrumentCoverage,
  updateInstrumentFlags
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
  baseResolution: '1_hour',
  calendarVenue: 'CRYPTO_24_7',
  cacheHistoricalPrices: true,
  cacheLivePrices: true,
  cacheFrom: null,
  cacheTo: null,
  status: 'finished',
  createdAt: new Date(),
  updatedAt: new Date(),
  ...overrides
});

beforeEach(() => {
  // resetAllMocks (not clearAllMocks) so a leftover queued mockResolvedValueOnce from one test
  // can never leak into the next test's first call to the same mock.
  vi.resetAllMocks();
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

  it('looks up the earliest-available date only for the chosen resolution, not every resolution', async () => {
    instrumentExists.mockResolvedValue(false);
    const earliest = new Date('2017-08-17T00:00:00.000Z');
    const connector = { getEarliestAvailableDate: vi.fn().mockResolvedValue(earliest) };
    getConnector.mockReturnValue(connector);
    createInstrument.mockResolvedValue(makeInstrument());

    await caller.register({
      source: 'binance',
      assetClass: 'spot',
      sourceSymbol: 'BTCUSDT',
      displaySymbol: 'BTC/USDT',
      baseResolution: '1_day',
      calendarVenue: 'CRYPTO_24_7'
    });

    // Regression test: this used to loop over 1_minute/1_hour/1_day unconditionally, so
    // registering at "1 day" for an old Yahoo symbol still probed "1 hour" behind the scenes and
    // could fail the whole registration over a resolution nobody asked for.
    expect(connector.getEarliestAvailableDate).toHaveBeenCalledTimes(1);
    expect(connector.getEarliestAvailableDate).toHaveBeenCalledWith('BTCUSDT', '1_day');
    expect(createInstrument).toHaveBeenCalledWith(
      expect.objectContaining({ earliestAvailableDates: { '1_day': earliest } })
    );
    expect(addQueueJob).toHaveBeenCalledTimes(1);
  });

  it('does not fail registration when the earliest-date lookup throws', async () => {
    instrumentExists.mockResolvedValue(false);
    const connector = { getEarliestAvailableDate: vi.fn().mockRejectedValue(new Error('range too large')) };
    getConnector.mockReturnValue(connector);
    createInstrument.mockResolvedValue(makeInstrument());

    await expect(
      caller.register({
        source: 'yahoo',
        assetClass: 'equity',
        sourceSymbol: 'VOO',
        displaySymbol: 'VOO',
        baseResolution: '1_hour',
        calendarVenue: 'NYSE'
      })
    ).resolves.toBeTruthy();

    expect(createInstrument).toHaveBeenCalledWith(expect.objectContaining({ earliestAvailableDates: {} }));
    expect(addQueueJob).toHaveBeenCalledTimes(1);
  });

  it('backfills from the earliest available date at the chosen resolution when requested', async () => {
    instrumentExists.mockResolvedValue(false);
    const earliestHourly = new Date('2020-01-01T00:00:00.000Z');
    const connector = { getEarliestAvailableDate: vi.fn().mockResolvedValue(earliestHourly) };
    getConnector.mockReturnValue(connector);
    createInstrument.mockResolvedValue(makeInstrument({ baseResolution: '1_hour' }));

    await caller.register({
      source: 'binance',
      assetClass: 'spot',
      sourceSymbol: 'BTCUSDT',
      displaySymbol: 'BTC/USDT',
      baseResolution: '1_hour',
      calendarVenue: 'CRYPTO_24_7',
      backfillFullHistory: true
    });

    const enqueuedJob = addQueueJob.mock.calls[0][0];
    expect(enqueuedJob.job.data.from).toBe(earliestHourly.toISOString());
  });
});

describe('instrumentsRouter.updateResolution', () => {
  it('throws NOT_FOUND for a non-existent instrument', async () => {
    getInstrumentById.mockResolvedValue(null);

    await expect(caller.updateResolution({ id: 'missing', baseResolution: '1_day' })).rejects.toMatchObject({
      code: 'NOT_FOUND'
    });
  });

  it('is a no-op when the resolution is unchanged', async () => {
    const instrument = makeInstrument({ baseResolution: '1_hour' });
    getInstrumentById.mockResolvedValue(instrument);

    await caller.updateResolution({ id: instrument._id.toHexString(), baseResolution: '1_hour' });

    expect(deleteAllCandles).not.toHaveBeenCalled();
    expect(updateInstrumentBaseResolution).not.toHaveBeenCalled();
    expect(addQueueJob).not.toHaveBeenCalled();
  });

  it('falls back to a default backfill window when the earliest-date lookup fails, instead of aborting', async () => {
    const instrument = makeInstrument({ baseResolution: '1_hour', earliestAvailableDates: {} });
    getInstrumentById.mockResolvedValueOnce(instrument).mockResolvedValueOnce(instrument);
    const connector = { getEarliestAvailableDate: vi.fn().mockRejectedValue(new Error('range too large')) };
    getConnector.mockReturnValue(connector);

    await caller.updateResolution({ id: instrument._id.toHexString(), baseResolution: '1_day' });

    expect(deleteAllCandles).toHaveBeenCalledWith(instrument._id.toHexString());
    expect(updateInstrumentBaseResolution).toHaveBeenCalledWith(instrument._id.toHexString(), '1_day', null);
    expect(addQueueJob).toHaveBeenCalledTimes(1);
  });

  it('uses the cached earliest-available date instead of calling the connector when present', async () => {
    const cachedEarliest = new Date('2018-01-01T00:00:00.000Z');
    const instrument = makeInstrument({
      baseResolution: '1_hour',
      earliestAvailableDates: { '1_day': cachedEarliest }
    });
    getInstrumentById.mockResolvedValue(instrument);
    const connector = { getEarliestAvailableDate: vi.fn() };
    getConnector.mockReturnValue(connector);

    await caller.updateResolution({ id: instrument._id.toHexString(), baseResolution: '1_day' });

    expect(connector.getEarliestAvailableDate).not.toHaveBeenCalled();
    expect(deleteAllCandles).toHaveBeenCalledWith(instrument._id.toHexString());
    expect(updateInstrumentBaseResolution).toHaveBeenCalledWith(
      instrument._id.toHexString(),
      '1_day',
      cachedEarliest
    );
    expect(addQueueJob.mock.calls[0][0].job.data.from).toBe(cachedEarliest.toISOString());
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
    expect(addQueueJob.mock.calls[0][0].job.data).toEqual({ instrumentId: instrument._id.toHexString() });
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
