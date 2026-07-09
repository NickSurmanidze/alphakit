import { InstrumentSource } from '../modules/instruments/instruments.types.js';
import { binanceConnector } from './binance/binanceConnector.js';
import { ibConnector } from './ib/ibConnector.js';
import { MarketDataConnector } from './types.js';
import { yahooConnector } from './yahoo/yahooConnector.js';

const registry: Record<InstrumentSource, MarketDataConnector> = {
  binance: binanceConnector,
  yahoo: yahooConnector,
  ib: ibConnector
};

export const getConnector = (source: InstrumentSource): MarketDataConnector => registry[source];
