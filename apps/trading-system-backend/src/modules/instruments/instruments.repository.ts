import { ObjectId } from 'mongodb';

import { collection } from '../../db/mongo.js';
import {
  AssetClass,
  BaseResolution,
  InstrumentDoc,
  InstrumentSource,
  InstrumentStatus
} from './instruments.types.js';

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
  baseCurrency?: string;
  quoteCurrency?: string;
  baseResolution: BaseResolution;
  calendarVenue: string;
  cacheHistoricalPrices: boolean;
  cacheLivePrices: boolean;
  earliestAvailableDates?: Partial<Record<BaseResolution, Date>>;
}): Promise<InstrumentDoc> => {
  const existing = await (await instruments()).findOne({
    source: input.source,
    sourceSymbol: input.sourceSymbol
  });
  if (existing) {
    throw new Error(`Instrument ${input.source}:${input.sourceSymbol} already exists`);
  }

  const now = new Date();
  const doc: InstrumentDoc = {
    _id: new ObjectId(),
    ...input,
    cacheFrom: null,
    cacheTo: null,
    status: 'pending',
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

export const updateInstrumentCoverage = async (
  id: string,
  updates: { cacheFrom?: Date; cacheTo?: Date; status?: InstrumentStatus }
): Promise<void> => {
  await (await instruments()).updateOne(
    { _id: new ObjectId(id) },
    { $set: { ...updates, updatedAt: new Date() } }
  );
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

export const deleteInstrument = async (id: string): Promise<void> => {
  await (await instruments()).deleteOne({ _id: new ObjectId(id) });
};

/** Changes an instrument's base resolution and resets its coverage back to `pending` -- the
 * caller is responsible for deleting the old candle rows and enqueuing a fresh backfill, since
 * those touch TimescaleDB/BullMQ rather than Mongo. */
export const updateInstrumentBaseResolution = async (
  id: string,
  baseResolution: BaseResolution,
  earliestAvailableDate: Date | null
): Promise<void> => {
  await (await instruments()).updateOne(
    { _id: new ObjectId(id) },
    {
      $set: {
        baseResolution,
        cacheFrom: null,
        cacheTo: null,
        status: 'pending',
        updatedAt: new Date(),
        ...(earliestAvailableDate ? { [`earliestAvailableDates.${baseResolution}`]: earliestAvailableDate } : {})
      }
    }
  );
};
