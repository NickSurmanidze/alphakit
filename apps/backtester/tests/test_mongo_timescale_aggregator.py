"""Pure-function test for market_dataframe_from_candles() -- the bucket-open-time ->
Market.add_market()-ready (time_open/time_close, indexed by time_close) conversion.
Doesn't touch Mongo/Timescale: fetch_instruments/fetch_candles/fetch_market_data need a
live DB and are intentionally left untested here (matches existing precedent --
binance_aggregator.py/kraken_aggregator.py also have no test coverage), verified
manually instead (see PROP_FIRM_PLAN.md)."""

import pandas as pd
import pytest

from data_aggregator.mongo_timescale_aggregator import market_dataframe_from_candles


def test_time_open_and_time_close_for_1_hour_resolution():
    # Timescale's `ts` is bucket-OPEN time -- two 1h candles starting at 00:00 and 01:00.
    candles = pd.DataFrame(
        {
            "open": [5000.0, 5010.0],
            "high": [5005.0, 5015.0],
            "low": [4995.0, 5005.0],
            "close": [5010.0, 5012.0],
            "volume": [100.0, 200.0],
        },
        index=pd.DatetimeIndex(
            [pd.Timestamp("2024-01-01T00:00:00"), pd.Timestamp("2024-01-01T01:00:00")], name="ts"
        ),
    )

    result = market_dataframe_from_candles(candles, resolution="1_hour")

    assert list(result.columns) == [
        "time_open",
        "time_close",
        "open",
        "high",
        "low",
        "close",
        "volume",
    ]
    assert result["time_open"].tolist() == [
        pd.Timestamp("2024-01-01T00:00:00"),
        pd.Timestamp("2024-01-01T01:00:00"),
    ]
    assert result["time_close"].tolist() == [
        pd.Timestamp("2024-01-01T00:59:59.999000"),
        pd.Timestamp("2024-01-01T01:59:59.999000"),
    ]
    # Indexed by time_close, matching MarketDataFromCSV.fetch_ohlc's convention exactly.
    assert result.index.tolist() == result["time_close"].tolist()
    assert result.index.name == "ts"


def test_unknown_resolution_raises():
    candles = pd.DataFrame(
        {"open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0], "volume": [1.0]},
        index=pd.DatetimeIndex([pd.Timestamp("2024-01-01T00:00:00")], name="ts"),
    )
    with pytest.raises(ValueError, match="Unknown resolution"):
        market_dataframe_from_candles(candles, resolution="17_minute")
