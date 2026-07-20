"""Unit tests for SupportResistanceBreakoutStrategy -- directly drives
Strategy.refresh() against a controlled Market with hand-picked resistance/support
indicators, so entries, fills, stop-buffer sizing, and exits are asserted exactly."""

import pandas as pd
import pytest

from backtester.exchange import PositionSide
from backtester.market import Market
from backtester.strategies import CloseReason, StrategyDirection, SupportResistanceBreakoutStrategy

SYMBOL = "MES/USD"
INTERVAL = 60
UNIT_OF_TIME = "minute"
RESISTANCE_KEY = "resistance"
SUPPORT_KEY = "support"
TREND_KEY = "trend"

NAN = float("nan")


def _market(rows: list[dict]) -> Market:
    """Each row: open, high, low, close, resistance, support, and optionally trend
    (defaults to NaN -- harmless unless a test's strategy actually sets
    trend_indicator_key)."""
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
    for key, field in [(RESISTANCE_KEY, "resistance"), (SUPPORT_KEY, "support")]:
        market.add_indicator(
            symbol=SYMBOL, interval=INTERVAL, unit_of_time=UNIT_OF_TIME,
            indicator_name=key, df=pd.Series([r[field] for r in rows], index=index),
        )
    market.add_indicator(
        symbol=SYMBOL, interval=INTERVAL, unit_of_time=UNIT_OF_TIME,
        indicator_name=TREND_KEY, df=pd.Series([r.get("trend", NAN) for r in rows], index=index),
    )
    market.compile()
    return market


def _row(  # noqa: PLR0913
    open_=100.0, high=100.0, low=100.0, close=100.0, resistance=110.0, support=90.0, trend=NAN,
):
    return {
        "open": open_, "high": high, "low": low, "close": close,
        "resistance": resistance, "support": support, "trend": trend,
    }


def _strategy(market: Market, **kwargs) -> SupportResistanceBreakoutStrategy:
    return SupportResistanceBreakoutStrategy(
        key="test", market=market, symbol=SYMBOL,
        resistance_key=RESISTANCE_KEY, support_key=SUPPORT_KEY, stop_buffer=2.0, **kwargs,
    )


def _run(market: Market, strategy: SupportResistanceBreakoutStrategy, n: int) -> None:
    for i in range(n):
        market.set_current_market_from_num_index(i)
        strategy.refresh()


class TestSupportResistanceBreakoutStrategy:
    def test_enters_long_when_high_touches_resistance(self):
        # resistance=110, stop_buffer=2 -> sl=108, risk=2, tp=110+2*2=114.
        market = _market([_row(open_=105, high=111, low=104, close=109)])
        strategy = _strategy(market)
        _run(market, strategy, 1)

        assert len(strategy.allocation.positions) == 1
        pos = strategy.allocation.positions[0]
        assert pos.side == PositionSide.long
        assert strategy.current_trade.open_price == pytest.approx(110)
        assert strategy.sl_price == pytest.approx(108)
        assert strategy.tp_price == pytest.approx(114)

    def test_enters_short_when_low_touches_support(self):
        # support=90, stop_buffer=2 -> sl=92, risk=2, tp=90-2*2=86.
        market = _market([_row(open_=95, high=96, low=89, close=91)])
        strategy = _strategy(market)
        _run(market, strategy, 1)

        pos = strategy.allocation.positions[0]
        assert pos.side == PositionSide.short
        assert strategy.current_trade.open_price == pytest.approx(90)
        assert strategy.sl_price == pytest.approx(92)
        assert strategy.tp_price == pytest.approx(86)

    def test_gap_through_open_fills_at_the_open_not_the_level(self):
        # Bar opens at 113, already above resistance=110 -- fills at the open, not
        # at a price the market never traded on the way up.
        market = _market([_row(open_=113, high=115, low=112, close=114)])
        strategy = _strategy(market)
        _run(market, strategy, 1)

        assert strategy.current_trade.open_price == pytest.approx(113)
        # sl is still level-derived (108), risk = 113-108=5, tp = 113 + 2*5 = 123.
        assert strategy.sl_price == pytest.approx(108)
        assert strategy.tp_price == pytest.approx(123)

    def test_skips_bar_that_touches_both_levels(self):
        market = _market([_row(open_=100, high=111, low=89, close=100)])
        strategy = _strategy(market)
        _run(market, strategy, 1)

        assert strategy.allocation.positions == []
        assert strategy.trade_history == []

    def test_no_entry_while_levels_are_nan(self):
        market = _market([_row(high=111, resistance=NAN, support=NAN)])
        strategy = _strategy(market)
        _run(market, strategy, 1)

        assert strategy.allocation.positions == []

    def test_respects_long_only_direction(self):
        market = _market([_row(open_=95, high=96, low=89, close=91)])  # would break support
        strategy = _strategy(market, direction=StrategyDirection.long)
        _run(market, strategy, 1)

        assert strategy.allocation.positions == []

    def test_exits_on_stop_loss(self):
        market = _market([
            _row(open_=105, high=111, low=104, close=109),  # long at 110, sl=108
            _row(open_=109, high=110, low=107, close=108),  # low hits 108
        ])
        strategy = _strategy(market)
        _run(market, strategy, 2)

        assert strategy.allocation.positions == []
        trade = strategy.trade_history[0]
        assert trade.close_reason == CloseReason.sl
        assert trade.close_price == pytest.approx(108)

    def test_exits_on_take_profit(self):
        market = _market([
            _row(open_=105, high=111, low=104, close=109),  # long at 110, tp=114
            _row(open_=112, high=115, low=111, close=114),  # high hits 114
        ])
        strategy = _strategy(market)
        _run(market, strategy, 2)

        trade = strategy.trade_history[0]
        assert trade.close_reason == CloseReason.tp
        assert trade.close_price == pytest.approx(114)

    def test_close_confirmed_mode_ignores_a_wick_that_does_not_close_past_the_level(self):
        # High touches resistance=110 but close falls back to 108 -- no confirmed
        # break, so no entry (unlike the default intrabar-touch mode).
        market = _market([_row(open_=105, high=111, low=104, close=108)])
        strategy = _strategy(market, confirm_on_close=True)
        _run(market, strategy, 1)

        assert strategy.allocation.positions == []
        assert strategy.trade_history == []

    def test_close_confirmed_mode_enters_at_the_close_when_it_clears_the_level(self):
        # close=112 > resistance=110 -> long at 112, sl=108, risk=4, tp=112+2*4=120.
        market = _market([_row(open_=105, high=113, low=104, close=112)])
        strategy = _strategy(market, confirm_on_close=True)
        _run(market, strategy, 1)

        pos = strategy.allocation.positions[0]
        assert pos.side == PositionSide.long
        assert strategy.current_trade.open_price == pytest.approx(112)
        assert strategy.sl_price == pytest.approx(108)
        assert strategy.tp_price == pytest.approx(120)

    def test_close_confirmed_mode_enters_short_when_close_clears_support(self):
        # close=88 < support=90 -> short at 88, sl=92, risk=4, tp=88-2*4=80.
        market = _market([_row(open_=95, high=96, low=87, close=88)])
        strategy = _strategy(market, confirm_on_close=True)
        _run(market, strategy, 1)

        pos = strategy.allocation.positions[0]
        assert pos.side == PositionSide.short
        assert strategy.current_trade.open_price == pytest.approx(88)
        assert strategy.sl_price == pytest.approx(92)
        assert strategy.tp_price == pytest.approx(80)

    def test_trend_filter_allows_long_when_price_is_above_trend(self):
        market = _market([_row(open_=105, high=111, low=104, close=109, trend=100.0)])
        strategy = _strategy(market, trend_indicator_key=TREND_KEY)
        _run(market, strategy, 1)

        assert len(strategy.allocation.positions) == 1
        assert strategy.allocation.positions[0].side == PositionSide.long

    def test_trend_filter_blocks_long_when_price_is_below_trend(self):
        # Would otherwise break resistance and go long, but close (109) is below
        # trend (120) -- the whipsaw case this filter exists for.
        market = _market([_row(open_=105, high=111, low=104, close=109, trend=120.0)])
        strategy = _strategy(market, trend_indicator_key=TREND_KEY)
        _run(market, strategy, 1)

        assert strategy.allocation.positions == []

    def test_trend_filter_blocks_short_when_price_is_above_trend(self):
        market = _market([_row(open_=95, high=96, low=89, close=91, trend=80.0)])
        strategy = _strategy(market, trend_indicator_key=TREND_KEY)
        _run(market, strategy, 1)

        assert strategy.allocation.positions == []

    def test_trend_filter_blocks_entry_while_trend_is_nan(self):
        # trend defaults to NaN -- warmup should block, not error.
        market = _market([_row(open_=105, high=111, low=104, close=109)])
        strategy = _strategy(market, trend_indicator_key=TREND_KEY)
        _run(market, strategy, 1)

        assert strategy.allocation.positions == []

    def test_no_trend_filter_reproduces_original_unfiltered_behavior(self):
        # Same setup as test_trend_filter_blocks_long_when_price_is_below_trend,
        # but without trend_indicator_key set -- should enter, filter is off.
        market = _market([_row(open_=105, high=111, low=104, close=109, trend=120.0)])
        strategy = _strategy(market)
        _run(market, strategy, 1)

        assert len(strategy.allocation.positions) == 1

    def test_stays_flat_after_a_zero_or_negative_risk_entry(self):
        # stop_buffer=0 -> sl equals the level itself -> risk=0 -> no trade taken.
        market = _market([_row(open_=105, high=111, low=104, close=109)])
        strategy = SupportResistanceBreakoutStrategy(
            key="test", market=market, symbol=SYMBOL,
            resistance_key=RESISTANCE_KEY, support_key=SUPPORT_KEY, stop_buffer=0.0,
        )
        _run(market, strategy, 1)

        assert strategy.allocation.positions == []
        assert strategy.trade_history == []
