// Mirrors modules/instruments/instruments.types.ts on the backend -- plain local types rather
// than importing backend internals, matching the convention already used for Resolution.
export type Source = 'binance' | 'yahoo' | 'ib' | 'databento';
export type AssetClass = 'spot' | 'perpetual' | 'equity' | 'future' | 'index' | 'forex';

export const SOURCE_LABELS: Record<Source, string> = {
  binance: 'Binance',
  yahoo: 'Yahoo Finance',
  ib: 'Interactive Brokers',
  databento: 'Databento'
};
