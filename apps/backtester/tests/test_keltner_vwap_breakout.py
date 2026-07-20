"""Unit tests for KeltnerVwapBreakoutStrategy -- directly drives Strategy.refresh()
against a controlled single-symbol Market with hand-picked VWAP/Keltner-band/squeeze
series, so entry direction, SL/TP levels, and exits are asserted exactly. Mirrors
test_bollinger_vwap_breakout.py's cases 1:1, since the strategy is the same shape
with Keltner bands in place of Bollinger bands."""

import pandas as pd
import pytest

from backtester.exchange import PositionSide
from backtester.market import Market
from backtester.strategies import CloseReason, KeltnerVwapBreakoutStrategy, StrategyDirection

SYMBOL = "MES/USD"
INTERVAL = 5
UNIT_OF_TIME = "minute"
VWAP_KEY = "vwap"
KC_LOWER_KEY = "kc_lower"
KC_UPPER_KEY = "kc_upper"
SQUEEZE_KEY = "is_squeeze"
TREND_KEY = "trend"


def _market(rows: list[dict]) -> Market:
    """Each row: close, high, low, vwap, kc_lower, kc_upper, is_squeeze, and
    optionally trend (defaults to NaN -- harmless unless a test's strategy actually
    sets trend_indicator_key)."""
    index = pd.date_range("2024-01-01", periods=len(rows), freq="5min")
    ohlc = pd.DataFrame(
        {
            "time_open": index,
            "time_close": index,
            "open": [r["close"] for r in rows],
            "high": [r["high"] for r in rows],
            "low": [r["low"] for r in rows],
            "close": [r["close"] for r in rows],
            "volume": 1,
        },
        index=index,
    )

    market = Market()
    market.add_market(symbol=SYMBOL, df=ohlc)
    market.add_indicator(
        symbol=SYMBOL, interval=INTERVAL, unit_of_time=UNIT_OF_TIME,
        indicator_name=VWAP_KEY, df=pd.Series([r["vwap"] for r in rows], index=index),
    )
    market.add_indicator(
        symbol=SYMBOL, interval=INTERVAL, unit_of_time=UNIT_OF_TIME,
        indicator_name=KC_LOWER_KEY, df=pd.Series([r["kc_lower"] for r in rows], index=index),
    )
    market.add_indicator(
        symbol=SYMBOL, interval=INTERVAL, unit_of_time=UNIT_OF_TIME,
        indicator_name=KC_UPPER_KEY, df=pd.Series([r["kc_upper"] for r in rows], index=index),
    )
    market.add_indicator(
        symbol=SYMBOL, interval=INTERVAL, unit_of_time=UNIT_OF_TIME,
        indicator_name=SQUEEZE_KEY, df=pd.Series([r["is_squeeze"] for r in rows], index=index),
    )
    market.add_indicator(
        symbol=SYMBOL, interval=INTERVAL, unit_of_time=UNIT_OF_TIME,
        indicator_name=TREND_KEY,
        df=pd.Series([r.get("trend", float("nan")) for r in rows], index=index),
    )
    market.compile()
    return market


def _strategy(market: Market, **kwargs) -> KeltnerVwapBreakoutStrategy:
    return KeltnerVwapBreakoutStrategy(
        key="test", market=market, symbol=SYMBOL,
        vwap_indicator_key=VWAP_KEY, kc_lower_key=KC_LOWER_KEY, kc_upper_key=KC_UPPER_KEY,
        squeeze_indicator_key=SQUEEZE_KEY, **kwargs,
    )


def _run(market: Market, strategy: KeltnerVwapBreakoutStrategy, n: int) -> None:
    for i in range(n):
        market.set_current_market_from_num_index(i)
        strategy.refresh()


class TestKeltnerVwapBreakoutStrategy:
    def test_enters_long_when_squeezed_and_above_vwap(self):
        # price=105 > vwap=100, squeeze True -> long, sl=lower_band=95, risk=10,
        # tp = 105 + 2*10 = 125.
        market = _market([
            {"close": 105, "high": 105, "low": 105, "vwap": 100, "kc_lower": 95, "kc_upper": 115, "is_squeeze": True},
        ])
        strategy = _strategy(market)
        _run(market, strategy, 1)

        assert len(strategy.allocation.positions) == 1
        pos = strategy.allocation.positions[0]
        assert pos.side == PositionSide.long
        assert pos.percent == 1
        assert strategy.sl_price == pytest.approx(95)
        assert strategy.tp_price == pytest.approx(125)

    def test_enters_short_when_squeezed_and_below_vwap(self):
        # price=95 < vwap=100, squeeze True -> short, sl=upper_band=105, risk=10,
        # tp = 95 - 2*10 = 75.
        market = _market([
            {"close": 95, "high": 95, "low": 95, "vwap": 100, "kc_lower": 85, "kc_upper": 105, "is_squeeze": True},
        ])
        strategy = _strategy(market)
        _run(market, strategy, 1)

        pos = strategy.allocation.positions[0]
        assert pos.side == PositionSide.short
        assert strategy.sl_price == pytest.approx(105)
        assert strategy.tp_price == pytest.approx(75)

    def test_no_entry_without_a_squeeze(self):
        market = _market([
            {"close": 105, "high": 105, "low": 105, "vwap": 100, "kc_lower": 95, "kc_upper": 115, "is_squeeze": False},
        ])
        strategy = _strategy(market)
        _run(market, strategy, 1)

        assert strategy.allocation.positions == []

    def test_exits_on_stop_loss(self):
        # Enter long at bar 0 (price=105, sl=95); bar 1's low hits 95.
        market = _market([
            {"close": 105, "high": 105, "low": 105, "vwap": 100, "kc_lower": 95, "kc_upper": 115, "is_squeeze": True},
            {"close": 96, "high": 106, "low": 94, "vwap": 100, "kc_lower": 95, "kc_upper": 115, "is_squeeze": False},
        ])
        strategy = _strategy(market)
        _run(market, strategy, 2)

        assert strategy.allocation.positions == []
        assert len(strategy.trade_history) == 1
        trade = strategy.trade_history[0]
        assert trade.close_reason == CloseReason.sl
        assert trade.close_price == pytest.approx(95)

    def test_exits_on_take_profit(self):
        # Enter long at bar 0 (price=105, sl=95, tp=125); bar 1's high hits 125.
        market = _market([
            {"close": 105, "high": 105, "low": 105, "vwap": 100, "kc_lower": 95, "kc_upper": 115, "is_squeeze": True},
            {"close": 120, "high": 126, "low": 104, "vwap": 100, "kc_lower": 95, "kc_upper": 115, "is_squeeze": False},
        ])
        strategy = _strategy(market)
        _run(market, strategy, 2)

        assert strategy.allocation.positions == []
        trade = strategy.trade_history[0]
        assert trade.close_reason == CloseReason.tp
        assert trade.close_price == pytest.approx(125)

    def test_direction_long_only_ignores_short_signals(self):
        market = _market([
            {"close": 95, "high": 95, "low": 95, "vwap": 100, "kc_lower": 85, "kc_upper": 105, "is_squeeze": True},
        ])
        strategy = _strategy(market, direction=StrategyDirection.long)
        _run(market, strategy, 1)

        assert strategy.allocation.positions == []

    def test_skips_entry_when_risk_is_not_positive(self):
        # price=105 > vwap -> long side, but kc_lower=110 is *above* price -- a
        # degenerate/invalid band placement with no positive risk distance to size
        # the trade or its take-profit off.
        market = _market([
            {"close": 105, "high": 105, "low": 105, "vwap": 100, "kc_lower": 110, "kc_upper": 115, "is_squeeze": True},
        ])
        strategy = _strategy(market)
        _run(market, strategy, 1)

        assert strategy.allocation.positions == []
        assert strategy.trade_history == []

    def test_risk_reward_ratio_is_configurable(self):
        market = _market([
            {"close": 105, "high": 105, "low": 105, "vwap": 100, "kc_lower": 100, "kc_upper": 115, "is_squeeze": True},
        ])
        strategy = _strategy(market, risk_reward_ratio=3.0)
        _run(market, strategy, 1)

        # risk = 105 - 100 = 5, tp = 105 + 3*5 = 120.
        assert strategy.tp_price == pytest.approx(120)

    def test_trend_filter_blocks_long_when_price_is_below_trend(self):
        # price=105 > vwap=100 -> long side, but price is *below* trend=110 -- the
        # longer-term trend disagrees, so no trade.
        market = _market([
            {"close": 105, "high": 105, "low": 105, "vwap": 100, "kc_lower": 95, "kc_upper": 115,
             "is_squeeze": True, "trend": 110},
        ])
        strategy = _strategy(market, trend_indicator_key=TREND_KEY)
        _run(market, strategy, 1)

        assert strategy.allocation.positions == []

    def test_trend_filter_allows_long_when_price_is_above_trend(self):
        market = _market([
            {"close": 105, "high": 105, "low": 105, "vwap": 100, "kc_lower": 95, "kc_upper": 115,
             "is_squeeze": True, "trend": 102},
        ])
        strategy = _strategy(market, trend_indicator_key=TREND_KEY)
        _run(market, strategy, 1)

        assert len(strategy.allocation.positions) == 1
        assert strategy.allocation.positions[0].side == PositionSide.long

    def test_trend_filter_blocks_short_when_price_is_above_trend(self):
        market = _market([
            {"close": 95, "high": 95, "low": 95, "vwap": 100, "kc_lower": 85, "kc_upper": 105,
             "is_squeeze": True, "trend": 90},
        ])
        strategy = _strategy(market, trend_indicator_key=TREND_KEY)
        _run(market, strategy, 1)

        assert strategy.allocation.positions == []

    def test_no_trend_filter_reproduces_original_vwap_only_behavior(self):
        # Same setup as test_trend_filter_blocks_long_when_price_is_below_trend, but
        # without trend_indicator_key set -- should enter, since the filter is off.
        market = _market([
            {"close": 105, "high": 105, "low": 105, "vwap": 100, "kc_lower": 95, "kc_upper": 115,
             "is_squeeze": True, "trend": 110},
        ])
        strategy = _strategy(market)
        _run(market, strategy, 1)

        assert len(strategy.allocation.positions) == 1
        assert strategy.allocation.positions[0].side == PositionSide.long
