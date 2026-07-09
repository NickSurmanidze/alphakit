"""Shared builders for small, fully hand-traceable test scenarios.

Unlike the golden test's multi-year real CSV (good for "did behavior drift", not for
"is this individual number correct"), these build tiny synthetic OHLC series so
expected fills/fees/PnL can be computed by hand and asserted exactly.
"""

import pandas as pd
import pytest

from backtester.exchange import Exchange, MarginAllocationType, MarketType
from backtester.market import Market

INTERVAL = pd.Timedelta("1h")
START = pd.Timestamp("2024-01-01T00:00:00")


def make_ohlc_df(candles: list[dict]) -> pd.DataFrame:
    """One row per hour starting 2024-01-01T00:00, matching the column shape
    MarketDataFromCSV produces (time_open/time_close columns, indexed by time_close)."""
    rows = []
    for i, c in enumerate(candles):
        time_open = START + i * INTERVAL
        time_close = time_open + INTERVAL - pd.Timedelta("1ms")
        rows.append(
            {
                "time_open": time_open,
                "time_close": time_close,
                "open": c["open"],
                "high": c["high"],
                "low": c["low"],
                "close": c["close"],
                "volume": c.get("volume", 0.0),
            }
        )
    df = pd.DataFrame(rows)
    df.index = df["time_close"]
    df.index.name = "ts"
    return df


def build_market(markets: dict[str, list[dict]]) -> Market:
    """markets: {symbol: [{"open":.., "high":.., "low":.., "close":..}, ...]}.
    All symbols must have the same candle count -- they share one timeline."""
    market = Market()
    for symbol, candles in markets.items():
        market.add_market(symbol=symbol, df=make_ohlc_df(candles))
    market.compile()
    return market


def make_exchange(  # noqa: PLR0913
    market: Market,
    market_type: MarketType = MarketType.future,
    max_leverage: int = 1,
    margin_allocation_type: MarginAllocationType = MarginAllocationType.isolated,
    slippage: float = 0.0,
    maker_fee: float = 0.0,
    taker_fee: float = 0.0,
    event_log_enabled: bool = True,
) -> Exchange:
    return Exchange(
        market=market,
        slippage=slippage,
        maker_fee=maker_fee,
        taker_fee=taker_fee,
        market_type=market_type,
        max_leverage=max_leverage,
        margin_allocation_type=margin_allocation_type,
        event_log_enabled=event_log_enabled,
    )


@pytest.fixture
def flat_market() -> Market:
    """20 flat $100 candles on BTC/USD -- a neutral canvas for balance/order-lifecycle tests
    that don't care about price movement."""
    return build_market(
        {"BTC/USD": [{"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0}] * 20}
    )
