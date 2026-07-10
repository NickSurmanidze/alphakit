"""
Mongo/Timescale Data Aggregator

Fetches instrument metadata (Mongo `instruments` collection) and OHLC candles
(TimescaleDB `candles__{resolution}` tables) from this repo's own market-data store.

Lifted out of notebooks/ib_portfolio_test.ipynb and generalized over `source` (e.g.
"yahoo", "ib") and `resolution` (e.g. "1_day", "1_hour") -- that notebook hardcoded
both to "yahoo" / "1_day".
"""

import os
from functools import lru_cache

import pandas as pd
from pymongo import MongoClient
from pymongo.collection import Collection
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

MONGO_URI = os.environ.get(
    "MONGO_URI", "mongodb://mongo:mongo@localhost:27018/mongo?authSource=mongo"
)
TIMESCALE_URL = os.environ.get(
    "TIMESCALE_URL", "postgres://timescale:timescale@localhost:5432/timescale"
)

_RESOLUTION_TIMEDELTA: dict[str, pd.Timedelta] = {
    "1_minute": pd.Timedelta("1min"),
    "5_minute": pd.Timedelta("5min"),
    "1_hour": pd.Timedelta("1h"),
    "1_day": pd.Timedelta("1D"),
}


@lru_cache(maxsize=1)
def _get_instruments_collection() -> Collection:
    """Lazily connects to Mongo and returns the `instruments` collection (cached, so
    repeated calls in the same process reuse one connection)."""
    return MongoClient(MONGO_URI).get_default_database()["instruments"]


@lru_cache(maxsize=1)
def _get_timescale_engine() -> Engine:
    """Lazily creates and caches the TimescaleDB SQLAlchemy engine (psycopg3 dialect)."""
    return create_engine(TIMESCALE_URL.replace("postgres://", "postgresql+psycopg://"))


def fetch_instruments(source: str) -> dict[str, str]:
    """Every registered instrument for `source` (e.g. "yahoo", "ib"), as
    {displaySymbol: Mongo _id (str)}.

    Raises:
        ValueError: if a displaySymbol is registered more than once for this source.
    """
    docs = list(
        _get_instruments_collection().find(
            {"source": source}, {"displaySymbol": 1, "source": 1, "description": 1}
        )
    )
    symbols = [d["displaySymbol"] for d in docs]
    dupes = {s for s in symbols if symbols.count(s) > 1}
    if dupes:
        raise ValueError(f"displaySymbol not unique for source={source!r}: {sorted(dupes)}")
    return {d["displaySymbol"]: str(d["_id"]) for d in docs}


def fetch_candles(instrument_id: str, resolution: str) -> pd.DataFrame:
    """One instrument's full `candles__{resolution}` history, indexed by `ts` -- the
    candle's UTC BUCKET-OPEN time in this Timescale schema, NOT close time. See
    market_dataframe_from_candles() for the open -> close conversion Market.add_market()
    expects."""
    table = f"candles__{resolution}"
    query = f"""
        SELECT ts, open, high, low, close, volume FROM {table}
        WHERE instrument_id = %(instrument_id)s ORDER BY ts ASC
    """
    df = pd.read_sql(
        query,
        _get_timescale_engine(),
        params={"instrument_id": instrument_id},
        index_col="ts",
        parse_dates=["ts"],
    )
    df.index = pd.to_datetime(df.index).tz_localize(None)
    df.index.name = "ts"
    return df


def market_dataframe_from_candles(candles: pd.DataFrame, resolution: str) -> pd.DataFrame:
    """Converts a fetch_candles() frame (indexed by bucket-OPEN ts) into the exact shape
    Market.add_market()/MarketDataFromCSV expect: time_open/time_close/OHLCV columns,
    indexed by time_close = time_open + resolution_interval - 1ms (matching
    MarketDataFromCSV.fetch_ohlc's convention exactly). Timescale tables are already
    pre-bucketed at `resolution`, so unlike the CSV path no resampling is needed here.

    Raises:
        ValueError: if `resolution` isn't a known bucket size.
    """
    if resolution not in _RESOLUTION_TIMEDELTA:
        raise ValueError(f"Unknown resolution {resolution!r}")
    interval = _RESOLUTION_TIMEDELTA[resolution]
    df = candles.copy()
    df["time_open"] = df.index
    df["time_close"] = df["time_open"] + interval - pd.Timedelta("1ms")
    df.index = df["time_close"]
    df.index.name = "ts"
    return df[["time_open", "time_close", "open", "high", "low", "close", "volume"]]


def fetch_market_data(source: str, display_symbol: str, resolution: str) -> pd.DataFrame:
    """One-call convenience: instrument lookup -> candles -> Market.add_market()-ready
    frame.

    Raises:
        KeyError: if `display_symbol` isn't registered for `source`.
    """
    instruments = fetch_instruments(source=source)
    if display_symbol not in instruments:
        raise KeyError(f"{display_symbol!r} not found for source={source!r}")
    candles = fetch_candles(instrument_id=instruments[display_symbol], resolution=resolution)
    return market_dataframe_from_candles(candles, resolution=resolution)
