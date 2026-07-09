import { getCalendar } from '../tradingCalendars/tradingCalendars.repository.js';
import { TradingCalendarDoc } from '../tradingCalendars/tradingCalendars.types.js';
import { getCandles } from './candleStore.js';
import { floorToBucketStart, RESOLUTION_MS, Resolution } from './resolutions.js';

const WEEKDAY_INDEX: Record<string, number> = { Sun: 0, Mon: 1, Tue: 2, Wed: 3, Thu: 4, Fri: 5, Sat: 6 };

const isWithinSession = (date: Date, calendar: TradingCalendarDoc): boolean => {
  if (calendar.is24x7) return true;

  const localDateStr = date.toLocaleDateString('en-CA', { timeZone: calendar.timezone });
  if (calendar.holidays.includes(localDateStr)) return false;

  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: calendar.timezone,
    weekday: 'short',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23'
  }).formatToParts(date);

  const dayOfWeek = WEEKDAY_INDEX[parts.find(p => p.type === 'weekday')!.value];
  const hour = Number(parts.find(p => p.type === 'hour')!.value);
  const minute = Number(parts.find(p => p.type === 'minute')!.value);
  const minutesSinceMidnight = hour * 60 + minute;

  const session = calendar.sessions.find(s => s.dayOfWeek === dayOfWeek);
  if (!session) return false;

  const [openH, openM] = session.open.split(':').map(Number);
  const [closeH, closeM] = session.close.split(':').map(Number);

  return minutesSinceMidnight >= openH * 60 + openM && minutesSinceMidnight < closeH * 60 + closeM;
};

export const computeExpectedTimes = (
  from: Date,
  to: Date,
  resolution: Resolution,
  calendar: TradingCalendarDoc
): Date[] => {
  const stepMs = RESOLUTION_MS[resolution];
  const times: Date[] = [];
  // `from` is frequently an arbitrary, sub-second-precision instant (e.g. `now - lookbackDays`,
  // recomputed fresh on every gap-check tick) rather than something already on a bucket boundary
  // -- stepping from it directly would build an expected-time grid that's offset from every real
  // candle's actual (epoch-aligned) timestamp by that same arbitrary amount, so almost nothing
  // would ever match and every real candle would misreport as "missing". Anchor the grid to the
  // same bucket boundaries the rest of the system uses (deriveCoarserResolutions,
  // refreshLatestCandles, ...) instead. Floors *down*, so the first bucket can start slightly
  // before `from` -- harmless, `getCandles`'s own `from`/`to` bounds still apply to what's fetched.
  const alignedFrom = floorToBucketStart(from, resolution).getTime();
  for (let t = alignedFrom; t < to.getTime(); t += stepMs) {
    const date = new Date(t);
    if (isWithinSession(date, calendar)) {
      times.push(date);
    }
  }
  return times;
};

export interface GapChunk {
  from: Date;
  to: Date;
}

const MAX_CANDLES_PER_CHUNK = 120;

/** Expected-vs-actual diff, same algorithm as legacy's candleGaps.ts, but calendar-aware:
 * expected-times outside the venue's trading sessions are excluded before diffing, so closed
 * markets never show up as gaps. */
export const findCandleGaps = async (params: {
  instrumentId: string;
  resolution: Resolution;
  calendarVenue: string;
  from: Date;
  to: Date;
}): Promise<GapChunk[]> => {
  const calendar = await getCalendar(params.calendarVenue);
  if (!calendar) {
    throw new Error(`Unknown trading calendar venue "${params.calendarVenue}"`);
  }

  const expected = computeExpectedTimes(params.from, params.to, params.resolution, calendar);
  if (expected.length === 0) {
    return [];
  }

  // Bound on total *raw* rows possibly in [from, to) at this resolution, ignoring calendar
  // filtering -- sources like Yahoo return pre/post-market candles our session model doesn't
  // know about, which would pad the actual row count well past `expected.length` and, with a
  // tighter ORDER BY ts ASC LIMIT, silently truncate before ever reaching the rows we need.
  const maxPossibleCandles = Math.ceil((params.to.getTime() - params.from.getTime()) / RESOLUTION_MS[params.resolution]) + 1;

  const actualCandles = await getCandles({
    instrumentId: params.instrumentId,
    resolution: params.resolution,
    from: params.from,
    to: params.to,
    limit: maxPossibleCandles
  });
  const actualTimes = new Set(actualCandles.map(c => c.timeOpen.getTime()));

  const missing = expected.filter(t => !actualTimes.has(t.getTime()));
  if (missing.length === 0) {
    return [];
  }

  const stepMs = RESOLUTION_MS[params.resolution];
  const chunks: GapChunk[] = [];
  let chunkStart = missing[0];
  let chunkCount = 1;
  let prev = missing[0];

  for (let i = 1; i < missing.length; i++) {
    const current = missing[i];
    const isConsecutive = current.getTime() - prev.getTime() === stepMs;

    if (isConsecutive && chunkCount < MAX_CANDLES_PER_CHUNK) {
      chunkCount++;
    } else {
      chunks.push({ from: chunkStart, to: new Date(prev.getTime() + stepMs) });
      chunkStart = current;
      chunkCount = 1;
    }
    prev = current;
  }
  chunks.push({ from: chunkStart, to: new Date(prev.getTime() + stepMs) });

  return chunks;
};
