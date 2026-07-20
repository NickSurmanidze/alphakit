"""Unit tests for MaCrossoverStrategy's ATR-scaled bracket mode -- drives refresh()
against a controlled Market with hand-picked fast/slow/ATR indicator series so
bracket prices, warmup behavior, and fixed-mode backward compatibility are asserted
exactly."""

import pandas as pd
import pytest

from backtester.exchange import PositionSide
from backtester.market import Market
from backtester.strategies import CloseReason, MaCrossoverStrategy, StrategyDirection

SYMBOL = "MES/USD"
INTERVAL = 1
UNIT_OF_TIME = "minute"
FAST_KEY = "fast"
SLOW_KEY = "slow"
ATR_KEY = "daily_atr"

NAN = float("nan")


def _market(rows: list[dict]) -> Market:
    """Each row: close (also used for OHLC), high, low, fast, slow, atr."""
    index = pd.date_range("2024-01-01", periods=len(rows), freq="min")
    ohlc = pd.DataFrame(
        {
            "time_open": index,
            "time_close": index,
            "open": [r["close"] for r in rows],
            "high": [r.get("high", r["close"]) for r in rows],
            "low": [r.get("low", r["close"]) for r in rows],
            "close": [r["close"] for r in rows],
            "volume": 1,
        },
        index=index,
    )

    market = Market()
    market.add_market(symbol=SYMBOL, df=ohlc)
    for key, field in [(FAST_KEY, "fast"), (SLOW_KEY, "slow"), (ATR_KEY, "atr")]:
        market.add_indicator(
            symbol=SYMBOL, interval=INTERVAL, unit_of_time=UNIT_OF_TIME,
            indicator_name=key,
            df=pd.Series([r.get(field, NAN) for r in rows], index=index),
        )
    market.compile()
    return market


def _strategy(market: Market, **kwargs) -> MaCrossoverStrategy:
    return MaCrossoverStrategy(
        key="test", market=market, symbol=SYMBOL, direction=StrategyDirection.both,
        fast_indicator_key=FAST_KEY, slow_indicator_key=SLOW_KEY, **kwargs,
    )


def _run(market: Market, strategy: MaCrossoverStrategy, n: int) -> None:
    for i in range(n):
        market.set_current_market_from_num_index(i)
        strategy.refresh()


# Bar 0: fast below slow; bar 1: fast crosses above -> long entry at close=100.
CROSS_UP = [
    {"close": 100, "fast": 99, "slow": 101, "atr": 8.0},
    {"close": 100, "fast": 102, "slow": 101, "atr": 8.0},
]


class TestMaCrossoverAtrBracket:
    def test_atr_mode_prices_bracket_from_atr(self):
        market = _market(CROSS_UP)
        strategy = _strategy(market, bracket_atr_key=ATR_KEY, sl_atr_mult=1.0, tp_atr_mult=1.5)
        _run(market, strategy, 2)

        assert len(strategy.allocation.positions) == 1
        assert strategy.allocation.positions[0].side == PositionSide.long
        assert strategy.sl_price == pytest.approx(100 - 1.0 * 8.0)   # 92
        assert strategy.tp_price == pytest.approx(100 + 1.5 * 8.0)   # 112
        # Both bracket orders placed alongside the position.
        assert len(strategy.allocation.orders) == 2
        # risk_percent derived from the actual priced stop distance.
        assert strategy.current_trade.risk_percent == pytest.approx(8.0 / 100)

    def test_atr_mode_short_is_symmetric(self):
        rows = [
            {"close": 100, "fast": 101, "slow": 99, "atr": 8.0},
            {"close": 100, "fast": 98, "slow": 99, "atr": 8.0},
        ]
        market = _market(rows)
        strategy = _strategy(market, bracket_atr_key=ATR_KEY, sl_atr_mult=0.5, tp_atr_mult=2.0)
        _run(market, strategy, 2)

        assert strategy.allocation.positions[0].side == PositionSide.short
        assert strategy.sl_price == pytest.approx(100 + 0.5 * 8.0)   # 104
        assert strategy.tp_price == pytest.approx(100 - 2.0 * 8.0)   # 84

    def test_atr_mode_nan_atr_enters_unbracketed(self):
        rows = [
            {"close": 100, "fast": 99, "slow": 101, "atr": NAN},
            {"close": 100, "fast": 102, "slow": 101, "atr": NAN},
        ]
        market = _market(rows)
        strategy = _strategy(market, bracket_atr_key=ATR_KEY, sl_atr_mult=1.0, tp_atr_mult=1.5)
        _run(market, strategy, 2)

        # Signal unchanged: position entered -- but no bracket orders, no risk unit.
        assert len(strategy.allocation.positions) == 1
        assert strategy.allocation.orders == []
        assert strategy.current_trade.risk_percent is None

    def test_atr_mode_unbracketed_trade_never_sl_tp_exits(self):
        # Enter unbracketed (NaN ATR); stale sl_price=0 from init must not trigger
        # a phantom SL/TP on later bars.
        rows = [
            {"close": 100, "fast": 99, "slow": 101, "atr": NAN},
            {"close": 100, "fast": 102, "slow": 101, "atr": NAN},
            {"close": 50, "high": 150, "low": 40, "fast": 103, "slow": 101, "atr": 8.0},
        ]
        market = _market(rows)
        strategy = _strategy(market, bracket_atr_key=ATR_KEY)
        _run(market, strategy, 3)

        assert len(strategy.allocation.positions) == 1  # still holding
        assert strategy.trade_history == []

    def test_atr_mode_sl_exit_fills_at_atr_stop(self):
        rows = [
            {"close": 100, "fast": 99, "slow": 101, "atr": 8.0},
            {"close": 100, "fast": 102, "slow": 101, "atr": 8.0},   # long, sl=92
            {"close": 93, "high": 100, "low": 91, "fast": 103, "slow": 101, "atr": 8.0},
        ]
        market = _market(rows)
        strategy = _strategy(market, bracket_atr_key=ATR_KEY, sl_atr_mult=1.0, tp_atr_mult=1.5)
        _run(market, strategy, 3)

        assert strategy.allocation.positions == []
        trade = strategy.trade_history[0]
        assert trade.close_reason == CloseReason.sl
        assert trade.close_price == pytest.approx(92)

    def test_fixed_mode_unchanged_by_default(self):
        # No bracket_atr_key -> original percent behavior, byte-for-byte.
        market = _market(CROSS_UP)
        strategy = _strategy(market, sl_percent=0.02, tp_percent=0.03)
        _run(market, strategy, 2)

        assert strategy.sl_price == pytest.approx(98.0)
        assert strategy.tp_price == pytest.approx(103.0)
        assert strategy.current_trade.risk_percent == pytest.approx(0.02)
        assert len(strategy.allocation.orders) == 2
