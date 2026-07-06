import { getConnector } from '../connectors/registry.js';
import { deriveCoarserResolutions, upsertCandles } from '../modules/candles/candleStore.js';
import { getInstrumentById, updateInstrumentCoverage } from '../modules/instruments/instruments.repository.js';

export const backfillHistoricalCandles = async ({
  instrumentId,
  from,
  to
}: {
  instrumentId: string;
  from: string;
  to: string;
}): Promise<void> => {
  const instrument = await getInstrumentById(instrumentId);
  if (!instrument) {
    return;
  }

  const fromDate = new Date(from);
  const toDate = new Date(to);

  const connector = getConnector(instrument.source);
  const { resolution, candles } = await connector.fetchHistoricalCandles({
    symbol: instrument.sourceSymbol,
    resolution: instrument.baseResolution,
    from: fromDate,
    to: toDate
  });

  if (candles.length > 0) {
    await upsertCandles({ instrumentId, resolution, source: instrument.source, candles });
    await deriveCoarserResolutions({ instrumentId, sourceResolution: resolution, from: fromDate, to: toDate });
  }

  await updateInstrumentCoverage(instrumentId, {
    cacheFrom: !instrument.cacheFrom || fromDate < instrument.cacheFrom ? fromDate : instrument.cacheFrom,
    cacheTo: !instrument.cacheTo || toDate > instrument.cacheTo ? toDate : instrument.cacheTo,
    status: 'finished'
  });
};
