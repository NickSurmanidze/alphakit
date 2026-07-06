import { closeMongoClient } from '../src/db/mongo.js';
import { upsertCalendar } from '../src/modules/tradingCalendars/tradingCalendars.repository.js';

// NYSE full-day closures. Hardcoded for the next couple of years -- extend as needed rather
// than pulling in a holiday-calculation dependency for a two-row list.
const NYSE_HOLIDAYS_2026_2027 = [
  '2026-01-01', // New Year's Day
  '2026-01-19', // MLK Day
  '2026-02-16', // Presidents Day
  '2026-04-03', // Good Friday
  '2026-05-25', // Memorial Day
  '2026-06-19', // Juneteenth
  '2026-07-03', // Independence Day (observed, July 4 falls on a Saturday)
  '2026-09-07', // Labor Day
  '2026-11-26', // Thanksgiving
  '2026-12-25', // Christmas
  '2027-01-01', // New Year's Day
  '2027-01-18', // MLK Day
  '2027-02-15', // Presidents Day
  '2027-03-26', // Good Friday
  '2027-05-31', // Memorial Day
  '2027-06-18', // Juneteenth (observed, June 19 falls on a Saturday)
  '2027-07-05', // Independence Day (observed, July 4 falls on a Sunday)
  '2027-09-06', // Labor Day
  '2027-11-25', // Thanksgiving
  '2027-12-24' // Christmas (observed, Dec 25 falls on a Saturday)
];

const main = async () => {
  await upsertCalendar({
    venue: 'CRYPTO_24_7',
    timezone: 'UTC',
    is24x7: true,
    sessions: [],
    holidays: []
  });

  await upsertCalendar({
    venue: 'NYSE',
    timezone: 'America/New_York',
    is24x7: false,
    sessions: [1, 2, 3, 4, 5].map(dayOfWeek => ({ dayOfWeek, open: '09:30', close: '16:00' })),
    holidays: NYSE_HOLIDAYS_2026_2027
  });

  // Futures (CME Globex) and forex both trade close to continuously (Sun evening - Fri
  // evening, with only a brief daily halt) -- our session model doesn't support sessions that
  // wrap past midnight, so this approximates as fully continuous rather than encoding the exact
  // halt/weekend boundaries. That's deliberately the safe direction to be wrong in: treating a
  // brief real closure as "open" produces a few harmless empty-result gap-checks; treating a
  // near-continuous market as NYSE hours (6.5h/day) is what caused the original incident this
  // calendar exists to fix -- it undercounts real trading hours *and* endlessly re-flags the
  // "missing" 17h/day as gaps.
  await upsertCalendar({
    venue: 'FUTURES_NEAR_CONTINUOUS',
    timezone: 'UTC',
    is24x7: true,
    sessions: [],
    holidays: []
  });

  console.log('Seeded trading calendars: CRYPTO_24_7, NYSE, FUTURES_NEAR_CONTINUOUS');
};

main()
  .catch(err => {
    console.error(err);
    process.exitCode = 1;
  })
  .finally(() => closeMongoClient());
