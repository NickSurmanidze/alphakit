"""Unit tests for OpeningRangeBreakoutStrategy -- directly drives Strategy.refresh()
against a controlled Market with hand-picked opening-range/tradeable/force-flat/
session indicators, so entries, fills, exits, and the one-trade-per-session rule are
asserted exactly."""

import pandas as pd
import pytest

from backtester.exchange import PositionSide
from backtester.market import Market
from backtester.strategies import CloseReason, OpeningRangeBreakoutStrategy, StrategyDirection

SYMBOL = "MES/USD"
INTERVAL = 5
UNIT_OF_TIME = "minute"
OR_HIGH_KEY = "or_high"
OR_LOW_KEY = "or_low"
TRADEABLE_KEY = "tradeable"
FORCE_FLAT_KEY = "force_flat"
SESSION_ID_KEY = "session_id"
ATR_KEY = "atr"

NAN = float("nan")


def _market(rows: list[dict]) -> Market:
    """Each row: open, high, low, close, or_high, or_low, tradeable, force_flat,
    session_id, and optionally atr (defaults to NaN -- harmless unless a test's
    strategy actually sets stop_atr_key)."""
    index = pd.date_range("2024-01-01", periods=len(rows), freq="5min")
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
    for key, field in [
        (OR_HIGH_KEY, "or_high"),
        (OR_LOW_KEY, "or_low"),
        (TRADEABLE_KEY, "tradeable"),
        (FORCE_FLAT_KEY, "force_flat"),
        (SESSION_ID_KEY, "session_id"),
    ]:
        market.add_indicator(
            symbol=SYMBOL, interval=INTERVAL, unit_of_time=UNIT_OF_TIME,
            indicator_name=key, df=pd.Series([r[field] for r in rows], index=index),
        )
    market.add_indicator(
        symbol=SYMBOL, interval=INTERVAL, unit_of_time=UNIT_OF_TIME,
        indicator_name=ATR_KEY,
        df=pd.Series([r.get("atr", NAN) for r in rows], index=index),
    )
    market.compile()
    return market


def _row(  # noqa: PLR0913
    open_=100.0, high=100.0, low=100.0, close=100.0,
    or_high=105.0, or_low=95.0, tradeable=True, force_flat=False, session_id=1.0,
):
    return {
        "open": open_, "high": high, "low": low, "close": close,
        "or_high": or_high, "or_low": or_low,
        "tradeable": tradeable, "force_flat": force_flat, "session_id": session_id,
    }


def _row_atr(atr, **kwargs):
    row = _row(**kwargs)
    row["atr"] = atr
    return row


def _strategy(market: Market, **kwargs) -> OpeningRangeBreakoutStrategy:
    return OpeningRangeBreakoutStrategy(
        key="test", market=market, symbol=SYMBOL,
        or_high_key=OR_HIGH_KEY, or_low_key=OR_LOW_KEY, tradeable_key=TRADEABLE_KEY,
        force_flat_key=FORCE_FLAT_KEY, session_id_key=SESSION_ID_KEY, **kwargs,
    )


def _run(market: Market, strategy: OpeningRangeBreakoutStrategy, n: int) -> None:
    for i in range(n):
        market.set_current_market_from_num_index(i)
        strategy.refresh()


class TestOpeningRangeBreakoutStrategy:
    def test_enters_long_when_high_touches_range_high(self):
        # OR = [95, 105]; bar's high touches 105 -> long at 105, sl=95, risk=10,
        # tp = 105 + 2*10 = 125.
        market = _market([_row(open_=101, high=106, low=100, close=104)])
        strategy = _strategy(market)
        _run(market, strategy, 1)

        assert len(strategy.allocation.positions) == 1
        pos = strategy.allocation.positions[0]
        assert pos.side == PositionSide.long
        assert strategy.current_trade.open_price == pytest.approx(105)
        assert strategy.sl_price == pytest.approx(95)
        assert strategy.tp_price == pytest.approx(125)

    def test_enters_short_when_low_touches_range_low(self):
        market = _market([_row(open_=99, high=100, low=94, close=96)])
        strategy = _strategy(market)
        _run(market, strategy, 1)

        pos = strategy.allocation.positions[0]
        assert pos.side == PositionSide.short
        assert strategy.current_trade.open_price == pytest.approx(95)
        assert strategy.sl_price == pytest.approx(105)
        assert strategy.tp_price == pytest.approx(75)

    def test_gap_through_open_fills_at_the_open_not_the_range_level(self):
        # Bar opens at 108, already above or_high=105 -- a stop order would fill at
        # the open, not at 105 (a price the market never traded on the way up).
        market = _market([_row(open_=108, high=110, low=107, close=109)])
        strategy = _strategy(market)
        _run(market, strategy, 1)

        assert strategy.current_trade.open_price == pytest.approx(108)
        # tp scales off the actual entry: risk = 108-95 = 13, tp = 108 + 2*13 = 134.
        assert strategy.tp_price == pytest.approx(134)

    def test_skips_bar_that_touches_both_sides(self):
        market = _market([_row(open_=100, high=106, low=94, close=100)])
        strategy = _strategy(market)
        _run(market, strategy, 1)

        assert strategy.allocation.positions == []
        assert strategy.trade_history == []

    def test_no_entry_when_not_tradeable(self):
        market = _market([_row(high=106, tradeable=False)])
        strategy = _strategy(market)
        _run(market, strategy, 1)

        assert strategy.allocation.positions == []

    def test_no_entry_while_range_is_nan(self):
        market = _market([_row(high=106, or_high=NAN, or_low=NAN)])
        strategy = _strategy(market)
        _run(market, strategy, 1)

        assert strategy.allocation.positions == []

    def test_exits_on_stop_loss(self):
        market = _market([
            _row(open_=101, high=106, low=100, close=104),   # long at 105, sl=95
            _row(open_=100, high=101, low=94, close=96),     # low hits 95
        ])
        strategy = _strategy(market)
        _run(market, strategy, 2)

        assert strategy.allocation.positions == []
        trade = strategy.trade_history[0]
        assert trade.close_reason == CloseReason.sl
        assert trade.close_price == pytest.approx(95)

    def test_exits_on_take_profit(self):
        market = _market([
            _row(open_=101, high=106, low=100, close=104),   # long at 105, tp=125
            _row(open_=110, high=126, low=109, close=120),   # high hits 125
        ])
        strategy = _strategy(market)
        _run(market, strategy, 2)

        trade = strategy.trade_history[0]
        assert trade.close_reason == CloseReason.tp
        assert trade.close_price == pytest.approx(125)

    def test_force_flat_exits_at_close_price(self):
        market = _market([
            _row(open_=101, high=106, low=100, close=104),                  # long at 105
            _row(open_=104, high=107, low=103, close=106, force_flat=True), # time exit at 106
        ])
        strategy = _strategy(market)
        _run(market, strategy, 2)

        trade = strategy.trade_history[0]
        assert trade.close_reason == CloseReason.signal
        assert trade.close_price == pytest.approx(106)

    def test_only_one_trade_per_session(self):
        market = _market([
            _row(open_=101, high=106, low=100, close=104),   # long at 105
            _row(open_=100, high=101, low=94, close=96),     # stopped out at 95
            _row(open_=100, high=106, low=100, close=104),   # would break again -- same session
        ])
        strategy = _strategy(market)
        _run(market, strategy, 3)

        assert strategy.allocation.positions == []
        assert len(strategy.trade_history) == 1

    def test_new_session_allows_a_new_trade(self):
        market = _market([
            _row(open_=101, high=106, low=100, close=104, session_id=1.0),  # long, session 1
            _row(open_=100, high=101, low=94, close=96, session_id=1.0),    # stopped out
            _row(open_=101, high=106, low=100, close=104, session_id=2.0),  # session 2 -> new trade
        ])
        strategy = _strategy(market)
        _run(market, strategy, 3)

        # Session 1's trade is closed (in history); session 2's is newly open --
        # open trades live in current_trade, not trade_history, until they close.
        assert len(strategy.trade_history) == 1
        assert len(strategy.allocation.positions) == 1
        assert strategy.current_trade.open_price == pytest.approx(105)

    def test_session_rollover_with_open_position_forces_exit(self):
        # force_flat never fires (early-close day); position must still be flattened
        # on the first bar of the next session.
        market = _market([
            _row(open_=101, high=106, low=100, close=104, session_id=1.0),  # long at 105
            _row(open_=104, high=104.5, low=103, close=104, session_id=2.0),  # session change
        ])
        strategy = _strategy(market)
        _run(market, strategy, 2)

        assert strategy.allocation.positions == []
        trade = strategy.trade_history[0]
        assert trade.close_reason == CloseReason.signal
        assert trade.close_price == pytest.approx(104)

    def test_no_tp_when_risk_reward_ratio_is_none(self):
        market = _market([
            _row(open_=101, high=106, low=100, close=104),
            _row(open_=110, high=200, low=109, close=150),  # would smash any tp
            _row(open_=150, high=151, low=149, close=150, force_flat=True),
        ])
        strategy = _strategy(market, risk_reward_ratio=None)
        _run(market, strategy, 3)

        trade = strategy.trade_history[0]
        assert trade.close_reason == CloseReason.signal  # ran to the time exit
        assert trade.close_price == pytest.approx(150)
        # Only the SL order should have been placed alongside the position.
        assert len(strategy.trade_history) == 1

    def test_direction_long_only_ignores_short_breakouts(self):
        market = _market([_row(open_=99, high=100, low=94, close=96)])
        strategy = _strategy(market, direction=StrategyDirection.long)
        _run(market, strategy, 1)

        assert strategy.allocation.positions == []

    def test_atr_stop_mode_places_stop_at_atr_multiple_from_entry(self):
        # Long at 105 (range high), ATR=8, mult=1.0 -> sl = 105-8 = 97 (NOT the
        # range low 95), risk = 8, tp = 105 + 2*8 = 121.
        market = _market([_row_atr(open_=101, high=106, low=100, close=104, atr=8.0)])
        strategy = _strategy(market, stop_atr_key=ATR_KEY, stop_atr_mult=1.0)
        _run(market, strategy, 1)

        assert strategy.current_trade.open_price == pytest.approx(105)
        assert strategy.sl_price == pytest.approx(97)
        assert strategy.tp_price == pytest.approx(121)

    def test_atr_stop_mode_short_is_symmetric(self):
        market = _market([_row_atr(open_=99, high=100, low=94, close=96, atr=8.0)])
        strategy = _strategy(market, stop_atr_key=ATR_KEY, stop_atr_mult=0.5)
        _run(market, strategy, 1)

        # Short at 95, sl = 95 + 0.5*8 = 99, tp = 95 - 2*4 = 87.
        assert strategy.sl_price == pytest.approx(99)
        assert strategy.tp_price == pytest.approx(87)

    def test_atr_stop_mode_skips_entry_while_atr_is_nan(self):
        market = _market([_row_atr(open_=101, high=106, low=100, close=104, atr=NAN)])
        strategy = _strategy(market, stop_atr_key=ATR_KEY, stop_atr_mult=1.0)
        _run(market, strategy, 1)

        assert strategy.allocation.positions == []
        assert strategy.trade_history == []
