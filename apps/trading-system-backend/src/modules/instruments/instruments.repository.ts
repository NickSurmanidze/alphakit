import { ObjectId } from 'mongodb';

import { collection } from '../../db/mongo.js';
import {
  AssetClass,
  BaseResolution,
  FutureDetails,
  InstrumentDoc,
  InstrumentSource,
  InstrumentStatus,
  ResolutionCoverage
} from './instruments.types.js';
import { pointValueFor } from './pointValues.js';

const instruments = () => collection<InstrumentDoc>('instruments');

export const instrumentExists = async (source: InstrumentSource, sourceSymbol: string): Promise<boolean> => {
  const existing = await (await instruments()).findOne(
    { source, sourceSymbol },
    { projection: { _id: 1 } }
  );
  return existing !== null;
};

export const createInstrument = async (input: {
  source: InstrumentSource;
  assetClass: AssetClass;
  sourceSymbol: string;
  displaySymbol: string;
  description?: string;
  baseCurrency?: string;
  quoteCurrency?: string;
  calendarVenue: string;
  cacheHistoricalPrices: boolean;
  cacheLivePrices: boolean;
  futureDetails?: FutureDetails;
  // One coverage entry per resolution the instrument should start collecting, each pre-populated
  // with whatever earliest-available lookup already ran (see instruments.router.ts) -- cache
  // coverage itself always starts empty regardless, since nothing's been fetched yet.
  resolutions: Partial<Record<BaseResolution, { earliestAvailableDate: Date | null }>>;
}): Promise<InstrumentDoc> => {
  const existing = await (await instruments()).findOne({
    source: input.source,
    sourceSymbol: input.sourceSymbol
  });
  if (existing) {
    throw new Error(`Instrument ${input.source}:${input.sourceSymbol} already exists`);
  }

  const { resolutions, ...rest } = input;
  const collectedResolutions: Partial<Record<BaseResolution, ResolutionCoverage>> = Object.fromEntries(
    Object.entries(resolutions).map(([resolution, { earliestAvailableDate }]) => [
      resolution,
      { cacheFrom: null, cacheTo: null, status: 'pending' as InstrumentStatus, earliestAvailableDate }
    ])
  );

  const now = new Date();
  const doc: InstrumentDoc = {
    _id: new ObjectId(),
    ...rest,
    collectedResolutions,
    pointValue: pointValueFor(input.displaySymbol),
    createdAt: now,
    updatedAt: now
  };

  await (await instruments()).insertOne(doc);
  return doc;
};

export const getInstrumentById = async (id: string): Promise<InstrumentDoc | null> => {
  return (await instruments()).findOne({ _id: new ObjectId(id) });
};

export const listInstruments = async (): Promise<InstrumentDoc[]> => {
  return (await instruments()).find({}).sort({ createdAt: -1 }).toArray();
};

export const listLiveInstruments = async (): Promise<InstrumentDoc[]> => {
  return (await instruments()).find({ cacheLivePrices: true }).toArray();
};

export const updateResolutionCoverage = async (
  id: string,
  resolution: BaseResolution,
  updates: { cacheFrom?: Date; cacheTo?: Date; status?: InstrumentStatus }
): Promise<void> => {
  const $set: Record<string, unknown> = { updatedAt: new Date() };
  for (const [key, value] of Object.entries(updates)) {
    $set[`collectedResolutions.${resolution}.${key}`] = value;
  }
  await (await instruments()).updateOne({ _id: new ObjectId(id) }, { $set });
};

export const updateInstrumentFlags = async (
  id: string,
  updates: { cacheHistoricalPrices?: boolean; cacheLivePrices?: boolean }
): Promise<void> => {
  await (await instruments()).updateOne(
    { _id: new ObjectId(id) },
    { $set: { ...updates, updatedAt: new Date() } }
  );
};

export const updateInstrumentDescription = async (id: string, description: string): Promise<void> => {
  await (await instruments()).updateOne(
    { _id: new ObjectId(id) },
    { $set: { description, updatedAt: new Date() } }
  );
};

export const deleteInstrument = async (id: string): Promise<void> => {
  await (await instruments()).deleteOne({ _id: new ObjectId(id) });
};

/** Resets one resolution's cache coverage back to `pending` -- the caller is responsible for
 * deleting that resolution's candle rows and enqueueing a fresh backfill, since those touch
 * TimescaleDB/BullMQ rather than Mongo. Keeps `earliestAvailableDate` (a source fact, not cache
 * state) unless a fresh lookup value is supplied. */
export const resetInstrumentResolution = async (
  id: string,
  resolution: BaseResolution,
  earliestAvailableDate?: Date | null
): Promise<void> => {
  const $set: Record<string, unknown> = {
    [`collectedResolutions.${resolution}.cacheFrom`]: null,
    [`collectedResolutions.${resolution}.cacheTo`]: null,
    [`collectedResolutions.${resolution}.status`]: 'pending',
    updatedAt: new Date()
  };
  if (earliestAvailableDate !== undefined) {
    $set[`collectedResolutions.${resolution}.earliestAvailableDate`] = earliestAvailableDate;
  }
  await (await instruments()).updateOne({ _id: new ObjectId(id) }, { $set });
};

/** Adds a new resolution to an instrument's set of explicitly-collected resolutions (e.g. adding
 * '5_minute' to an instrument that started out daily-only). No-op fields for cache coverage --
 * the caller enqueues the actual backfill job separately. */
export const addInstrumentResolution = async (
  id: string,
  resolution: BaseResolution,
  earliestAvailableDate: Date | null
): Promise<void> => {
  const coverage: ResolutionCoverage = { cacheFrom: null, cacheTo: null, status: 'pending', earliestAvailableDate };
  await (await instruments()).updateOne(
    { _id: new ObjectId(id) },
    { $set: { [`collectedResolutions.${resolution}`]: coverage, updatedAt: new Date() } }
  );
};
