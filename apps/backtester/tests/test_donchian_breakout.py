"""Unit tests for DonchianBreakoutStrategy -- directly drives Strategy.refresh()
against a controlled Market with hand-picked upper/lower channel indicators, so
entries, fills, opposite-channel stops, and exits are asserted exactly."""

import pandas as pd
import pytest

from backtester.exchange import PositionSide
from backtester.market import Market
from backtester.strategies import CloseReason, DonchianBreakoutStrategy, StrategyDirection

SYMBOL = "MES/USD"
INTERVAL = 60
UNIT_OF_TIME = "minute"
UPPER_KEY = "upper"
LOWER_KEY = "lower"
ATR_KEY = "atr"

NAN = float("nan")


def _market(rows: list[dict]) -> Market:
    """Each row: open, high, low, close, upper, lower, and optionally atr
    (defaults to NaN -- harmless unless a test's strategy actually sets
    stop_atr_key)."""
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
    for key, field in [(UPPER_KEY, "upper"), (LOWER_KEY, "lower")]:
        market.add_indicator(
            symbol=SYMBOL, interval=INTERVAL, unit_of_time=UNIT_OF_TIME,
            indicator_name=key, df=pd.Series([r[field] for r in rows], index=index),
        )
    market.add_indicator(
        symbol=SYMBOL, interval=INTERVAL, unit_of_time=UNIT_OF_TIME,
        indicator_name=ATR_KEY, df=pd.Series([r.get("atr", NAN) for r in rows], index=index),
    )
    market.compile()
    return market


def _row(open_=100.0, high=100.0, low=100.0, close=100.0, upper=110.0, lower=90.0, atr=NAN):  # noqa: PLR0913
    return {
        "open": open_, "high": high, "low": low, "close": close,
        "upper": upper, "lower": lower, "atr": atr,
    }


def _strategy(market: Market, **kwargs) -> DonchianBreakoutStrategy:
    return DonchianBreakoutStrategy(
        key="test", market=market, symbol=SYMBOL, upper_key=UPPER_KEY, lower_key=LOWER_KEY, **kwargs,
    )


def _run(market: Market, strategy: DonchianBreakoutStrategy, n: int) -> None:
    for i in range(n):
        market.set_current_market_from_num_index(i)
        strategy.refresh()


class TestDonchianBreakoutStrategy:
    def test_enters_long_when_close_clears_the_upper_channel(self):
        # upper=110, lower=90 -> long at close=112, sl=90 (opposite side), risk=22,
        # tp=112+2*22=156.
        market = _market([_row(open_=105, high=113, low=104, close=112)])
        strategy = _strategy(market)
        _run(market, strategy, 1)

        assert len(strategy.allocation.positions) == 1
        pos = strategy.allocation.positions[0]
        assert pos.side == PositionSide.long
        assert strategy.current_trade.open_price == pytest.approx(112)
        assert strategy.sl_price == pytest.approx(90)
        assert strategy.tp_price == pytest.approx(156)

    def test_enters_short_when_close_clears_the_lower_channel(self):
        # upper=110, lower=90 -> short at close=88, sl=110, risk=22, tp=88-2*22=44.
        market = _market([_row(open_=95, high=96, low=87, close=88)])
        strategy = _strategy(market)
        _run(market, strategy, 1)

        pos = strategy.allocation.positions[0]
        assert pos.side == PositionSide.short
        assert strategy.current_trade.open_price == pytest.approx(88)
        assert strategy.sl_price == pytest.approx(110)
        assert strategy.tp_price == pytest.approx(44)

    def test_no_entry_when_close_stays_inside_the_channel(self):
        # High wicks above upper=110, but close (105) never clears it -- no entry,
        # this strategy requires a close-confirmed break.
        market = _market([_row(open_=100, high=112, low=99, close=105)])
        strategy = _strategy(market)
        _run(market, strategy, 1)

        assert strategy.allocation.positions == []

    def test_no_entry_while_channel_is_nan(self):
        market = _market([_row(close=112, upper=NAN, lower=NAN)])
        strategy = _strategy(market)
        _run(market, strategy, 1)

        assert strategy.allocation.positions == []

    def test_respects_long_only_direction(self):
        market = _market([_row(open_=95, high=96, low=87, close=88)])  # would break lower channel
        strategy = _strategy(market, direction=StrategyDirection.long)
        _run(market, strategy, 1)

        assert strategy.allocation.positions == []

    def test_exits_on_stop_loss(self):
        market = _market([
            _row(open_=105, high=113, low=104, close=112),  # long at 112, sl=90
            _row(open_=100, high=101, low=89, close=95),    # low hits 90
        ])
        strategy = _strategy(market)
        _run(market, strategy, 2)

        assert strategy.allocation.positions == []
        trade = strategy.trade_history[0]
        assert trade.close_reason == CloseReason.sl
        assert trade.close_price == pytest.approx(90)

    def test_exits_on_take_profit(self):
        market = _market([
            _row(open_=105, high=113, low=104, close=112),  # long at 112, tp=156
            _row(open_=150, high=157, low=149, close=155),  # high hits 156
        ])
        strategy = _strategy(market)
        _run(market, strategy, 2)

        trade = strategy.trade_history[0]
        assert trade.close_reason == CloseReason.tp
        assert trade.close_price == pytest.approx(156)

    def test_atr_stop_mode_places_stop_at_atr_multiple_from_entry(self):
        # Long at close=112 (upper=110), ATR=8, mult=1.0 -> sl = 112-8 = 104
        # (NOT the channel's own lower=90), risk = 8, tp = 112 + 2*8 = 128.
        market = _market([_row(open_=105, high=113, low=104, close=112, atr=8.0)])
        strategy = _strategy(market, stop_atr_key=ATR_KEY, stop_atr_mult=1.0)
        _run(market, strategy, 1)

        assert strategy.current_trade.open_price == pytest.approx(112)
        assert strategy.sl_price == pytest.approx(104)
        assert strategy.tp_price == pytest.approx(128)

    def test_atr_stop_mode_short_is_symmetric(self):
        market = _market([_row(open_=95, high=96, low=87, close=88, atr=8.0)])
        strategy = _strategy(market, stop_atr_key=ATR_KEY, stop_atr_mult=0.5)
        _run(market, strategy, 1)

        # Short at 88, sl = 88 + 0.5*8 = 92, tp = 88 - 2*4 = 80.
        assert strategy.sl_price == pytest.approx(92)
        assert strategy.tp_price == pytest.approx(80)

    def test_atr_stop_mode_skips_entry_while_atr_is_nan(self):
        market = _market([_row(open_=105, high=113, low=104, close=112, atr=NAN)])
        strategy = _strategy(market, stop_atr_key=ATR_KEY, stop_atr_mult=1.0)
        _run(market, strategy, 1)

        assert strategy.allocation.positions == []
        assert strategy.trade_history == []

    def test_stays_flat_on_a_negative_risk_entry(self):
        # Defensive guard: an inverted channel (upper < lower -- not something the
        # real indicator produces, since upper is a rolling max and lower a
        # rolling min of the same window, but worth confirming the shared _enter
        # guard still protects against it) would price the opposite-side stop
        # past the entry itself -- no trade taken rather than a negative-risk one.
        market = _market([_row(open_=105, high=113, low=104, close=95, upper=90.0, lower=110.0)])
        strategy = _strategy(market)
        _run(market, strategy, 1)

        assert strategy.allocation.positions == []
        assert strategy.trade_history == []
