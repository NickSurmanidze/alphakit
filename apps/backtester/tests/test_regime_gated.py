"""Unit tests for RegimeGatedStrategy -- verifies entries are suppressed while the
current regime isn't in the allowed set, exits on an already-open position always
proceed regardless of regime, and allocation/trade_history/current_trade correctly
forward to the wrapped inner strategy."""

import pandas as pd
import pytest

from backtester.exchange import PositionSide
from backtester.market import Market
from backtester.strategies import CloseReason, RegimeGatedStrategy
from backtester.strategies.base import Allocation, AllocationPosition, Strategy

SYMBOL = "MES/USD"
INTERVAL = 30
UNIT_OF_TIME = "minute"
REGIME_KEY = "regime"


class _CountingStrategy(Strategy):
    """Test double: opens a long position at the bar's close the first time
    refresh() is called while flat; otherwise no-ops (exits are driven manually by
    the test via close_trade()). Tracks how many times refresh() actually ran, so
    a test can tell whether the wrapper let a given bar's call through."""

    def __init__(self, key: str, market: Market, symbol: str):
        super().__init__(key=key, market=market, symbol=symbol)
        self.refresh_calls = 0

    def refresh(self) -> None:
        self.refresh_calls += 1
        if not self.allocation.positions:
            price = self.market.current[self.symbol]["close"]
            self.allocation = Allocation()
            self.allocation.positions = [
                AllocationPosition(side=PositionSide.long, symbol=self.symbol, percent=1, average_open_price=price)
            ]
            self._mark_allocation_changed()
            self.open_trade(side=PositionSide.long, open_price=price)


def _market(rows: list[dict]) -> Market:
    """Each row: close, regime (a string label, or None for NaN-like warmup)."""
    index = pd.date_range("2024-01-01", periods=len(rows), freq="30min")
    ohlc = pd.DataFrame(
        {
            "time_open": index,
            "time_close": index,
            "open": [r["close"] for r in rows],
            "high": [r["close"] for r in rows],
            "low": [r["close"] for r in rows],
            "close": [r["close"] for r in rows],
            "volume": 1,
        },
        index=index,
    )

    market = Market()
    market.add_market(symbol=SYMBOL, df=ohlc)
    market.add_indicator(
        symbol=SYMBOL, interval=INTERVAL, unit_of_time=UNIT_OF_TIME,
        indicator_name=REGIME_KEY, df=pd.Series([r["regime"] for r in rows], index=index),
    )
    market.compile()
    return market


def _row(close=100.0, regime="Choppy"):
    return {"close": close, "regime": regime}


class TestRegimeGatedEntries:
    def test_entry_suppressed_when_regime_not_allowed(self):
        rows = [_row(close=100.0, regime="Trending")]
        market = _market(rows)
        inner = _CountingStrategy(key="inner", market=market, symbol=SYMBOL)
        gated = RegimeGatedStrategy(
            key="gated", market=market, symbol=SYMBOL, inner=inner,
            regime_key=REGIME_KEY, allowed_regimes={"Choppy"},
        )

        market.set_current_market_from_num_index(0)
        gated.refresh()

        assert inner.refresh_calls == 0
        assert gated.allocation.positions == []

    def test_entry_proceeds_when_regime_allowed(self):
        rows = [_row(close=100.0, regime="Choppy")]
        market = _market(rows)
        inner = _CountingStrategy(key="inner", market=market, symbol=SYMBOL)
        gated = RegimeGatedStrategy(
            key="gated", market=market, symbol=SYMBOL, inner=inner,
            regime_key=REGIME_KEY, allowed_regimes={"Choppy"},
        )

        market.set_current_market_from_num_index(0)
        gated.refresh()

        assert inner.refresh_calls == 1
        assert len(gated.allocation.positions) == 1
        assert gated.allocation.positions[0].side == PositionSide.long

    def test_no_entry_when_regime_indicator_missing(self):
        rows = [_row(close=100.0, regime=None)]
        market = _market(rows)
        inner = _CountingStrategy(key="inner", market=market, symbol=SYMBOL)
        gated = RegimeGatedStrategy(
            key="gated", market=market, symbol=SYMBOL, inner=inner,
            regime_key=REGIME_KEY, allowed_regimes={"Choppy", "Trending"},
        )

        market.set_current_market_from_num_index(0)
        gated.refresh()

        assert inner.refresh_calls == 0
        assert gated.allocation.positions == []


class TestRegimeGatedExits:
    def test_exit_management_proceeds_even_when_regime_not_allowed(self):
        rows = [_row(close=100.0, regime="Choppy"), _row(close=105.0, regime="Trending")]
        market = _market(rows)
        inner = _CountingStrategy(key="inner", market=market, symbol=SYMBOL)
        gated = RegimeGatedStrategy(
            key="gated", market=market, symbol=SYMBOL, inner=inner,
            regime_key=REGIME_KEY, allowed_regimes={"Choppy"},
        )

        # Bar 0: regime allowed, inner opens a position.
        market.set_current_market_from_num_index(0)
        gated.refresh()
        assert inner.refresh_calls == 1
        assert len(gated.allocation.positions) == 1

        # Bar 1: regime is no longer allowed, but a position is already open --
        # inner.refresh() must still run so it can manage/close it.
        market.set_current_market_from_num_index(1)
        gated.refresh()
        assert inner.refresh_calls == 2


class TestRegimeGatedStateForwarding:
    def test_allocation_and_trade_history_forward_to_inner(self):
        rows = [_row(close=100.0, regime="Choppy")]
        market = _market(rows)
        inner = _CountingStrategy(key="inner", market=market, symbol=SYMBOL)
        gated = RegimeGatedStrategy(
            key="gated", market=market, symbol=SYMBOL, inner=inner,
            regime_key=REGIME_KEY, allowed_regimes={"Choppy"},
        )

        market.set_current_market_from_num_index(0)
        gated.refresh()

        assert gated.allocation is inner.allocation
        # Manually close the trade on the inner strategy and confirm the wrapper
        # sees it show up in trade_history without any extra plumbing.
        inner.close_trade(close_price=110.0, reason=CloseReason.tp)
        assert gated.trade_history is inner.trade_history
        assert len(gated.trade_history) == 1
        assert gated.trade_history[0].close_price == pytest.approx(110.0)
        assert gated.current_trade is inner.current_trade
        assert gated.allocation_change_time == inner.allocation_change_time
