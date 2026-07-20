"""Unit tests for PairsMeanReversionStrategy -- directly drives Strategy.refresh()
against a controlled two-symbol Market and a hand-picked z-score series, so entry/
exit/stop transitions are asserted exactly rather than inferred from a full backtest."""

import pandas as pd
import pytest

from backtester.exchange import PositionSide
from backtester.market import Market
from backtester.strategies import CloseReason, PairsMeanReversionStrategy, StrategyDirection

SYMBOL_A = "MES/USD"
SYMBOL_B = "MNQ/USD"
INTERVAL = 1
UNIT_OF_TIME = "minute"
ZSCORE_KEY = "spread_zscore"


def _market_with_zscores(zscores: list[float]) -> Market:
    index = pd.date_range("2024-01-01", periods=len(zscores), freq="min")
    ohlc_a = pd.DataFrame(
        {
            "time_open": index,
            "time_close": index,
            "open": 100.0,
            "high": 100.0,
            "low": 100.0,
            "close": [100.0 + i for i in range(len(zscores))],
            "volume": 1,
        },
        index=index,
    )
    ohlc_b = pd.DataFrame(
        {
            "time_open": index,
            "time_close": index,
            "open": 200.0,
            "high": 200.0,
            "low": 200.0,
            "close": [200.0 + 2 * i for i in range(len(zscores))],
            "volume": 1,
        },
        index=index,
    )

    market = Market()
    market.add_market(symbol=SYMBOL_A, df=ohlc_a)
    market.add_market(symbol=SYMBOL_B, df=ohlc_b)
    market.add_indicator(
        symbol=SYMBOL_A,
        interval=INTERVAL,
        unit_of_time=UNIT_OF_TIME,
        indicator_name=ZSCORE_KEY,
        df=pd.Series(zscores, index=index),
    )
    market.compile()
    return market


def _run(market: Market, strategy: PairsMeanReversionStrategy, n: int) -> None:
    for i in range(n):
        market.set_current_market_from_num_index(i)
        strategy.refresh()


class TestPairsMeanReversionStrategy:
    def test_enters_short_spread_when_zscore_crosses_entry_threshold(self):
        # Bars 0-1 flat (|z| < 2), bar 2 crosses entry_z=2 -> short spread:
        # short SYMBOL_A, long SYMBOL_B.
        market = _market_with_zscores([0.5, 1.0, 2.5])
        strategy = PairsMeanReversionStrategy(
            key="test", market=market, symbol=SYMBOL_A, symbol_b=SYMBOL_B,
            zscore_indicator_key=ZSCORE_KEY, entry_z=2.0, exit_z=0.5, stop_z=4.0,
        )
        _run(market, strategy, 3)

        assert len(strategy.allocation.positions) == 2
        pos_a = next(p for p in strategy.allocation.positions if p.symbol == SYMBOL_A)
        pos_b = next(p for p in strategy.allocation.positions if p.symbol == SYMBOL_B)
        assert pos_a.side == PositionSide.short
        assert pos_b.side == PositionSide.long
        assert pos_a.percent == 1
        assert pos_b.percent == 1

    def test_enters_long_spread_when_zscore_crosses_negative_entry_threshold(self):
        market = _market_with_zscores([0.0, -1.0, -2.5])
        strategy = PairsMeanReversionStrategy(
            key="test", market=market, symbol=SYMBOL_A, symbol_b=SYMBOL_B,
            zscore_indicator_key=ZSCORE_KEY, entry_z=2.0, exit_z=0.5, stop_z=4.0,
        )
        _run(market, strategy, 3)

        pos_a = next(p for p in strategy.allocation.positions if p.symbol == SYMBOL_A)
        pos_b = next(p for p in strategy.allocation.positions if p.symbol == SYMBOL_B)
        assert pos_a.side == PositionSide.long
        assert pos_b.side == PositionSide.short

    def test_exits_on_reversion_and_records_both_leg_trades(self):
        # Enter short-spread at bar 2 (z=2.5), revert to z=0.3 (<= exit_z=0.5) at bar 4.
        market = _market_with_zscores([0.5, 1.0, 2.5, 1.5, 0.3])
        strategy = PairsMeanReversionStrategy(
            key="test", market=market, symbol=SYMBOL_A, symbol_b=SYMBOL_B,
            zscore_indicator_key=ZSCORE_KEY, entry_z=2.0, exit_z=0.5, stop_z=4.0,
        )
        _run(market, strategy, 5)

        assert strategy.allocation.positions == []
        assert len(strategy.trade_history) == 2
        trade_a = next(t for t in strategy.trade_history if t.symbol == SYMBOL_A)
        trade_b = next(t for t in strategy.trade_history if t.symbol == SYMBOL_B)
        assert trade_a.side == PositionSide.short
        assert trade_b.side == PositionSide.long
        assert trade_a.close_reason == CloseReason.signal
        assert trade_b.close_reason == CloseReason.signal
        # Entered at bar 2 (close=102.0), closed at bar 4 (close=104.0).
        assert trade_a.open_price == pytest.approx(102.0)
        assert trade_a.close_price == pytest.approx(104.0)

    def test_hard_stop_fires_when_zscore_blows_through_stop_threshold(self):
        # Enter short-spread at bar 1 (z=2.5), spread keeps diverging past stop_z=4.0
        # at bar 2 instead of reverting.
        market = _market_with_zscores([0.0, 2.5, 4.5])
        strategy = PairsMeanReversionStrategy(
            key="test", market=market, symbol=SYMBOL_A, symbol_b=SYMBOL_B,
            zscore_indicator_key=ZSCORE_KEY, entry_z=2.0, exit_z=0.5, stop_z=4.0,
        )
        _run(market, strategy, 3)

        assert strategy.allocation.positions == []
        assert len(strategy.trade_history) == 2
        assert all(t.close_reason == CloseReason.sl for t in strategy.trade_history)

    def test_direction_long_only_ignores_short_spread_entries(self):
        market = _market_with_zscores([0.5, 1.0, 2.5])
        strategy = PairsMeanReversionStrategy(
            key="test", market=market, symbol=SYMBOL_A, symbol_b=SYMBOL_B,
            zscore_indicator_key=ZSCORE_KEY, entry_z=2.0, exit_z=0.5, stop_z=4.0,
            direction=StrategyDirection.long,
        )
        _run(market, strategy, 3)

        assert strategy.allocation.positions == []
        assert strategy.trade_history == []

    def test_stays_flat_while_zscore_is_nan(self):
        market = _market_with_zscores([float("nan"), float("nan")])
        strategy = PairsMeanReversionStrategy(
            key="test", market=market, symbol=SYMBOL_A, symbol_b=SYMBOL_B,
            zscore_indicator_key=ZSCORE_KEY, entry_z=2.0, exit_z=0.5, stop_z=4.0,
        )
        _run(market, strategy, 2)

        assert strategy.allocation.positions == []
        assert strategy.trade_history == []
