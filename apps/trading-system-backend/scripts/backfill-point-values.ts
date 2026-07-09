import { closeMongoClient, collection } from '../src/db/mongo.js';
import { InstrumentDoc } from '../src/modules/instruments/instruments.types.js';
import { pointValueFor } from '../src/modules/instruments/pointValues.js';

// One-time backfill: every instrument registered before `pointValue` existed on the schema gets
// it set from the same lookup table new registrations now use at creation time (see
// createInstrument in instruments.repository.ts). Safe to re-run -- always recomputes from
// displaySymbol, never trusts a possibly-stale existing value.
const main = async () => {
  const instruments = await collection<InstrumentDoc>('instruments');
  const docs = await instruments.find({}).toArray();

  for (const doc of docs) {
    const pointValue = pointValueFor(doc.displaySymbol);
    await instruments.updateOne({ _id: doc._id }, { $set: { pointValue } });
    console.log(`${doc.source}:${doc.displaySymbol} -> pointValue=${pointValue}`);
  }

  console.log(`Backfilled pointValue for ${docs.length} instrument(s).`);
};

main()
  .catch(err => {
    console.error(err);
    process.exitCode = 1;
  })
  .finally(() => closeMongoClient());
