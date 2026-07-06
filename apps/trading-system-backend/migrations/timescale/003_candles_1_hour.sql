CREATE TABLE IF NOT EXISTS candles__1_hour (
  ts TIMESTAMPTZ NOT NULL,
  instrument_id TEXT NOT NULL,
  open DOUBLE PRECISION,
  high DOUBLE PRECISION,
  low DOUBLE PRECISION,
  close DOUBLE PRECISION,
  volume DOUBLE PRECISION,
  source TEXT NOT NULL,
  validated BOOLEAN DEFAULT FALSE,
  UNIQUE (ts, instrument_id)
);

SELECT create_hypertable(
  'candles__1_hour', 'ts',
  partitioning_column => 'instrument_id',
  number_partitions => 1,
  chunk_time_interval => INTERVAL '1 month',
  if_not_exists => TRUE
);

CREATE INDEX IF NOT EXISTS idx_candles_1_hour_instrument_id ON candles__1_hour (instrument_id);
