import { collection } from '../../db/mongo.js';
import { TradingCalendarDoc } from './tradingCalendars.types.js';

const calendars = () => collection<TradingCalendarDoc>('tradingCalendars');

export const getCalendar = async (venue: string): Promise<TradingCalendarDoc | null> => {
  return (await calendars()).findOne({ venue });
};

export const upsertCalendar = async (doc: TradingCalendarDoc): Promise<void> => {
  await (await calendars()).updateOne({ venue: doc.venue }, { $set: doc }, { upsert: true });
};
