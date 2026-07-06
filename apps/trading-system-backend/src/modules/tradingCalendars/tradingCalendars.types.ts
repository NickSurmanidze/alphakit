export interface CalendarSession {
  dayOfWeek: number; // 0 = Sunday .. 6 = Saturday
  open: string; // 'HH:mm', in `timezone`
  close: string; // 'HH:mm', in `timezone`
}

export interface TradingCalendarDoc {
  venue: string; // 'CRYPTO_24_7' | 'NYSE' | ...
  timezone: string; // IANA name, e.g. 'America/New_York'
  is24x7: boolean;
  sessions: CalendarSession[]; // ignored when is24x7 is true
  holidays: string[]; // 'YYYY-MM-DD', venue-local calendar dates, full-day closures
}
