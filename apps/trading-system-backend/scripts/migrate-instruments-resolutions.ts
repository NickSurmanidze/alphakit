import { closeMongoClient, collection } from '../src/db/mongo.js';

// One-time migration: collapses the old single baseResolution/cacheFrom/cacheTo/status/
// earliestAvailableDates fields into the new per-resolution `collectedResolutions` map (see
// modules/instruments/instruments.types.ts). Run once, then the old fields are gone -- this is
// not a permanent compatibility shim, just a one-shot data move.
interface LegacyInstrumentDoc {
  _id: unknown;
  baseResolution?: string;
  cacheFrom?: Date | null;
  cacheTo?: Date | null;
  status?: string;
  earliestAvailableDates?: Record<string, Date>;
  collectedResolutions?: unknown;
}

const main = async () => {
  const instruments = await collection<LegacyInstrumentDoc>('instruments');

  const legacyDocs = await instruments.find({ baseResolution: { $exists: true } }).toArray();
  console.log(`Found ${legacyDocs.length} instrument(s) on the legacy single-resolution schema.`);

  for (const doc of legacyDocs) {
    const resolution = doc.baseResolution!;
    const collectedResolutions = {
      [resolution]: {
        cacheFrom: doc.cacheFrom ?? null,
        cacheTo: doc.cacheTo ?? null,
        status: doc.status ?? 'pending',
        earliestAvailableDate: doc.earliestAvailableDates?.[resolution] ?? null
      }
    };

    await instruments.updateOne(
      { _id: doc._id },
      {
        $set: { collectedResolutions },
        $unset: { baseResolution: '', cacheFrom: '', cacheTo: '', status: '', earliestAvailableDates: '' }
      }
    );
    console.log(`migrated: ${doc._id} -> collectedResolutions.${resolution}`);
  }

  console.log('Instrument resolution migration complete.');
};

main()
  .catch(err => {
    console.error(err);
    process.exitCode = 1;
  })
  .finally(() => closeMongoClient());
