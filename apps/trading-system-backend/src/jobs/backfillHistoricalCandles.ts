import { getConnector } from '../connectors/registry.js';
import { deriveCoarserResolutions, upsertCandles } from '../modules/candles/candleStore.js';
import { BaseResolution } from '../modules/instruments/instruments.types.js';
import { getInstrumentById, updateResolutionCoverage } from '../modules/instruments/instruments.repository.js';

export const backfillHistoricalCandles = async ({
  instrumentId,
  resolution,
  from,
  to
}: {
  instrumentId: string;
  resolution: BaseResolution;
  from: string;
  to: string;
}): Promise<void> => {
  const instrument = await getInstrumentById(instrumentId);
  if (!instrument) {
    return;
  }

  const coverage = instrument.collectedResolutions[resolution];
  if (!coverage) {
    return;
  }

  const fromDate = new Date(from);
  const toDate = new Date(to);

  const connector = getConnector(instrument.source);
  // The connector's returned `resolution` may differ from what was requested (e.g. Yahoo falling
  // back to a coarser one for old intraday ranges it can no longer serve) -- upsert/derive off
  // that, but track coverage against the resolution this job was actually assigned.
  const { resolution: fetchedResolution, candles } = await connector.fetchHistoricalCandles({
    symbol: instrument.sourceSymbol,
    resolution,
    from: fromDate,
    to: toDate
  });

  if (candles.length > 0) {
    await upsertCandles({ instrumentId, resolution: fetchedResolution, source: instrument.source, candles });
    await deriveCoarserResolutions({
      instrumentId,
      sourceResolution: fetchedResolution,
      collectedResolutions: Object.keys(instrument.collectedResolutions) as BaseResolution[],
      from: fromDate,
      to: toDate
    });
  }

  await updateResolutionCoverage(instrumentId, resolution, {
    cacheFrom: !coverage.cacheFrom || fromDate < coverage.cacheFrom ? fromDate : coverage.cacheFrom,
    cacheTo: !coverage.cacheTo || toDate > coverage.cacheTo ? toDate : coverage.cacheTo,
    status: 'finished'
  });
};
