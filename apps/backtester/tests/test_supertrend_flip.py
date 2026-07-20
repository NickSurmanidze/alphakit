"""Unit tests for SuperTrendFlipStrategy -- directly drives Strategy.refresh()
against a controlled Market with hand-picked line/direction indicators, so flip
entries, stop-and-reverse, signal exits, and the optional take-profit are
asserted exactly."""

import pandas as pd
import pytest

from backtester.exchange import PositionSide
from backtester.market import Market
from backtester.strategies import CloseReason, StrategyDirection, SuperTrendFlipStrategy

SYMBOL = "MES/USD"
INTERVAL = 60
UNIT_OF_TIME = "minute"
LINE_KEY = "line"
DIRECTION_KEY = "trend_direction"

NAN = float("nan")


def _market(rows: list[dict]) -> Market:
    """Each row: open, high, low, close, line, trend_direction."""
    index = pd.date_range("2024-01-01", periods=len(rows), freq="1h")
    ohlc = pd.DataFrame(
        {
            "time_open": index,
            "time_close": index,
            "open": [r["open"] for r in rows],
            "high": [r["high"] for r in rows],
            "low": [r["low"] for r in rows],
            "close": [r["close"] for r in rows],
            "volume": 1,
        },
        index=index,
    )

    market = Market()
    market.add_market(symbol=SYMBOL, df=ohlc)
    for key, field in [(LINE_KEY, "line"), (DIRECTION_KEY, "trend_direction")]:
        market.add_indicator(
            symbol=SYMBOL, interval=INTERVAL, unit_of_time=UNIT_OF_TIME,
            indicator_name=key, df=pd.Series([r[field] for r in rows], index=index),
        )
    market.compile()
    return market


def _row(  # noqa: PLR0913
    open_=100.0, high=100.0, low=100.0, close=100.0, line=95.0, trend_direction=1.0,
):
    return {
        "open": open_, "high": high, "low": low, "close": close,
        "line": line, "trend_direction": trend_direction,
    }


def _strategy(market: Market, **kwargs) -> SuperTrendFlipStrategy:
    return SuperTrendFlipStrategy(
        key="test", market=market, symbol=SYMBOL, line_key=LINE_KEY, direction_key=DIRECTION_KEY, **kwargs,
    )


def _run(market: Market, strategy: SuperTrendFlipStrategy, n: int) -> None:
    for i in range(n):
        market.set_current_market_from_num_index(i)
        strategy.refresh()


class TestSuperTrendFlipStrategy:
    def test_no_entry_on_the_first_valid_bar(self):
        # No prior direction to compare against yet -- can't detect a flip.
        market = _market([_row(trend_direction=1.0)])
        strategy = _strategy(market)
        _run(market, strategy, 1)

        assert strategy.allocation.positions == []

    def test_enters_long_on_flip_to_uptrend(self):
        market = _market([
            _row(close=100, line=105, trend_direction=-1.0),
            _row(close=110, line=100, trend_direction=1.0),
        ])
        strategy = _strategy(market)
        _run(market, strategy, 2)

        pos = strategy.allocation.positions[0]
        assert pos.side == PositionSide.long
        assert strategy.current_trade.open_price == pytest.approx(110)

    def test_enters_short_on_flip_to_downtrend(self):
        market = _market([
            _row(close=100, line=95, trend_direction=1.0),
            _row(close=90, line=100, trend_direction=-1.0),
        ])
        strategy = _strategy(market)
        _run(market, strategy, 2)

        pos = strategy.allocation.positions[0]
        assert pos.side == PositionSide.short
        assert strategy.current_trade.open_price == pytest.approx(90)

    def test_no_entry_without_a_flip(self):
        market = _market([
            _row(close=100, line=95, trend_direction=1.0),
            _row(close=101, line=96, trend_direction=1.0),
        ])
        strategy = _strategy(market)
        _run(market, strategy, 2)

        assert strategy.allocation.positions == []

    def test_exits_on_signal_when_direction_flips_back_above_the_frozen_stop(self):
        # long at 110, sl frozen at line=100. Bar 2: still uptrend, line ratchets
        # to 112 (no exit -- low=115 stays clear of the frozen sl=100). Bar 3:
        # flips down at close=105 -- above the frozen sl=100, so this is a clean
        # signal exit, not an sl exit (low=104 also stays clear of sl=100).
        market = _market([
            _row(close=100, line=105, trend_direction=-1.0),
            _row(open_=105, high=111, low=104, close=110, line=100, trend_direction=1.0),
            _row(open_=112, high=122, low=115, close=120, line=112, trend_direction=1.0),
            _row(open_=118, high=119, low=104, close=105, line=112, trend_direction=-1.0),
        ])
        strategy = SuperTrendFlipStrategy(
            key="test", market=market, symbol=SYMBOL, line_key=LINE_KEY, direction_key=DIRECTION_KEY,
            direction=StrategyDirection.long,  # isolate the exit, no stop-and-reverse short
        )
        _run(market, strategy, 4)

        assert strategy.allocation.positions == []
        trade = strategy.trade_history[0]
        assert trade.close_reason == CloseReason.signal
        assert trade.close_price == pytest.approx(105)

    def test_stop_and_reverse_closes_long_and_opens_short_in_the_same_bar(self):
        # long at 110, sl frozen at line=100. Bar 2's low (default 100.0) hits
        # the frozen sl exactly, closing the long -- and the same bar's flip to
        # a downtrend opens the short immediately (stop-and-reverse), regardless
        # of which exit path (sl vs. signal) closed the prior side.
        market = _market([
            _row(close=100, line=105, trend_direction=-1.0),
            _row(close=110, line=100, trend_direction=1.0),   # long at 110, sl=100
            _row(close=95, line=108, trend_direction=-1.0),   # sl hit -> reverse to short
        ])
        strategy = _strategy(market)
        _run(market, strategy, 3)

        assert len(strategy.trade_history) == 1
        closed = strategy.trade_history[0]
        assert closed.close_reason == CloseReason.sl
        assert closed.close_price == pytest.approx(100)

        assert len(strategy.allocation.positions) == 1
        pos = strategy.allocation.positions[0]
        assert pos.side == PositionSide.short
        assert strategy.current_trade.open_price == pytest.approx(95)

    def test_hard_stop_loss_fires_on_a_violent_move_even_without_a_flip(self):
        # long at 110, sl frozen at line=100. Bar 2's direction is still 1.0 (no
        # flip at all -- the indicator hasn't caught up yet), but a violent
        # single-bar move takes the low to 80, well past the frozen sl. Without
        # a hard stop this position would ride uncapped until some future flip;
        # with it, it's capped at the frozen sl distance.
        market = _market([
            _row(close=100, line=105, trend_direction=-1.0),
            _row(close=110, line=100, trend_direction=1.0),               # long at 110, sl=100
            _row(open_=105, high=106, low=80, close=85, trend_direction=1.0),  # violent drop, no flip
        ])
        strategy = _strategy(market)
        _run(market, strategy, 3)

        assert strategy.allocation.positions == []
        trade = strategy.trade_history[0]
        assert trade.close_reason == CloseReason.sl
        assert trade.close_price == pytest.approx(100)

    def test_take_profit_when_configured(self):
        # long at 110, line=100 -> risk=10, rr=2 -> tp=130.
        market = _market([
            _row(close=100, line=105, trend_direction=-1.0),
            _row(close=110, line=100, trend_direction=1.0),
            _row(open_=115, high=131, low=112, close=128, line=115, trend_direction=1.0),
        ])
        strategy = _strategy(market, risk_reward_ratio=2.0)
        _run(market, strategy, 3)

        assert strategy.allocation.positions == []
        trade = strategy.trade_history[0]
        assert trade.close_reason == CloseReason.tp
        assert trade.close_price == pytest.approx(130)

    def test_no_take_profit_by_default_holds_through_favorable_moves(self):
        market = _market([
            _row(close=100, line=105, trend_direction=-1.0),
            _row(close=110, line=100, trend_direction=1.0),
            _row(open_=115, high=500, low=112, close=128, line=115, trend_direction=1.0),
        ])
        strategy = _strategy(market)
        _run(market, strategy, 3)

        # No TP configured -- a big favorable wick doesn't close the position.
        assert len(strategy.allocation.positions) == 1
        assert strategy.trade_history == []

    def test_nan_bar_is_skipped_without_a_spurious_entry(self):
        # A NaN bar returns early (no crash), leaving _prev_trend_direction at
        # its last known value -- direction is unchanged (1.0) before and after
        # the gap, so no flip is ever detected and nothing should fire.
        market = _market([
            _row(close=100, line=95, trend_direction=1.0),
            _row(trend_direction=NAN, line=NAN),
            _row(close=101, line=96, trend_direction=1.0),
        ])
        strategy = _strategy(market)
        _run(market, strategy, 3)

        assert strategy.allocation.positions == []

    def test_respects_long_only_direction(self):
        market = _market([
            _row(close=100, line=95, trend_direction=1.0),
            _row(close=90, line=100, trend_direction=-1.0),  # would flip short
        ])
        strategy = _strategy(market, direction=StrategyDirection.long)
        _run(market, strategy, 2)

        assert strategy.allocation.positions == []

    def test_stays_flat_on_a_negative_risk_entry(self):
        # Defensive guard: line already on the wrong side of the flip close --
        # shouldn't happen from the real indicator's own construction, but the
        # shared _enter guard should still protect against it.
        market = _market([
            _row(close=100, line=105, trend_direction=-1.0),
            _row(close=110, line=120, trend_direction=1.0),  # line above close despite "uptrend"
        ])
        strategy = _strategy(market)
        _run(market, strategy, 2)

        assert strategy.allocation.positions == []
        assert strategy.trade_history == []
