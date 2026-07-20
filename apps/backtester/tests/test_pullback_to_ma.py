"""Unit tests for PullbackToMaStrategy -- directly drives Strategy.refresh()
against a controlled Market with hand-picked trend/pullback-MA/ATR indicators, so
regime filtering, reclaim entries, and ATR-offset stop/take-profit are asserted
exactly."""

import pandas as pd
import pytest

from backtester.exchange import PositionSide
from backtester.market import Market
from backtester.strategies import CloseReason, PullbackToMaStrategy, StrategyDirection

SYMBOL = "MES/USD"
INTERVAL = 15
UNIT_OF_TIME = "minute"
TREND_MA_KEY = "trend_ma"
PULLBACK_MA_KEY = "pullback_ma"
ATR_KEY = "atr"

NAN = float("nan")


def _market(rows: list[dict]) -> Market:
    """Each row: open, high, low, close, trend_ma, pullback_ma, atr."""
    index = pd.date_range("2024-01-01", periods=len(rows), freq="15min")
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
    for key, field in [(TREND_MA_KEY, "trend_ma"), (PULLBACK_MA_KEY, "pullback_ma"), (ATR_KEY, "atr")]:
        market.add_indicator(
            symbol=SYMBOL, interval=INTERVAL, unit_of_time=UNIT_OF_TIME,
            indicator_name=key, df=pd.Series([r.get(field, NAN) for r in rows], index=index),
        )
    market.compile()
    return market


def _row(  # noqa: PLR0913
    open_=100.0, high=100.0, low=100.0, close=100.0, trend_ma=90.0, pullback_ma=99.0, atr=2.0,
):
    return {
        "open": open_, "high": high, "low": low, "close": close,
        "trend_ma": trend_ma, "pullback_ma": pullback_ma, "atr": atr,
    }


def _strategy(market: Market, **kwargs) -> PullbackToMaStrategy:
    return PullbackToMaStrategy(
        key="test", market=market, symbol=SYMBOL,
        trend_ma_key=TREND_MA_KEY, pullback_ma_key=PULLBACK_MA_KEY, atr_key=ATR_KEY, **kwargs,
    )


def _run(market: Market, strategy: PullbackToMaStrategy, n: int) -> None:
    for i in range(n):
        market.set_current_market_from_num_index(i)
        strategy.refresh()


# Uptrend regime (trend_ma=90) throughout. Bar 0: close=98 (at/below pullback_ma=99).
# Bar 1: close reclaims above pullback_ma (100 > 99) -> long entry at close=100.
RECLAIM_LONG = [
    _row(close=98.0, high=98.0, low=98.0, trend_ma=90.0, pullback_ma=99.0, atr=2.0),
    _row(close=100.0, high=100.0, low=100.0, trend_ma=90.0, pullback_ma=99.0, atr=2.0),
]

# Downtrend regime (trend_ma=110) throughout. Bar 0: close=102 (at/above pullback_ma=101).
# Bar 1: close reclaims below pullback_ma (100 < 101) -> short entry at close=100.
RECLAIM_SHORT = [
    _row(close=102.0, high=102.0, low=102.0, trend_ma=110.0, pullback_ma=101.0, atr=2.0),
    _row(close=100.0, high=100.0, low=100.0, trend_ma=110.0, pullback_ma=101.0, atr=2.0),
]


class TestPullbackToMaEntries:
    def test_long_entry_on_reclaim_in_uptrend_regime(self):
        market = _market(RECLAIM_LONG)
        strategy = _strategy(market, direction=StrategyDirection.both, stop_atr_mult=1.0, risk_reward_ratio=2.0)
        _run(market, strategy, 2)

        assert len(strategy.allocation.positions) == 1
        position = strategy.allocation.positions[0]
        assert position.side == PositionSide.long
        assert position.average_open_price == pytest.approx(100.0)
        # sl = pullback_ma(99) - 1.0*atr(2.0) = 97; risk = 100-97 = 3; tp = 100 + 2*3 = 106
        assert strategy.sl_price == pytest.approx(97.0)
        assert strategy.tp_price == pytest.approx(106.0)

    def test_short_entry_on_reclaim_in_downtrend_regime(self):
        market = _market(RECLAIM_SHORT)
        strategy = _strategy(market, direction=StrategyDirection.both, stop_atr_mult=1.0, risk_reward_ratio=2.0)
        _run(market, strategy, 2)

        assert len(strategy.allocation.positions) == 1
        position = strategy.allocation.positions[0]
        assert position.side == PositionSide.short
        assert position.average_open_price == pytest.approx(100.0)
        # sl = pullback_ma(101) + 1.0*atr(2.0) = 103; risk = 103-100 = 3; tp = 100 - 2*3 = 94
        assert strategy.sl_price == pytest.approx(103.0)
        assert strategy.tp_price == pytest.approx(94.0)

    def test_no_long_entry_when_direction_is_short_only(self):
        market = _market(RECLAIM_LONG)
        strategy = _strategy(market, direction=StrategyDirection.short)
        _run(market, strategy, 2)

        assert strategy.allocation.positions == []

    def test_no_entry_without_prior_bar_below_pullback_ma(self):
        """Close is above pullback_ma on both bars -- never actually pulled back,
        so no reclaim signal fires even though it's above the fast MA."""
        rows = [
            _row(close=101.0, trend_ma=90.0, pullback_ma=99.0, atr=2.0),
            _row(close=102.0, trend_ma=90.0, pullback_ma=99.0, atr=2.0),
        ]
        market = _market(rows)
        strategy = _strategy(market)
        _run(market, strategy, 2)

        assert strategy.allocation.positions == []

    def test_no_long_entry_when_reclaim_happens_outside_uptrend_regime(self):
        """Same reclaim shape as RECLAIM_LONG, but trend_ma is above price (downtrend
        regime) -- longs are filtered out even though the fast-MA reclaim pattern
        is identical."""
        rows = [
            _row(close=98.0, trend_ma=105.0, pullback_ma=99.0, atr=2.0),
            _row(close=100.0, trend_ma=105.0, pullback_ma=99.0, atr=2.0),
        ]
        market = _market(rows)
        strategy = _strategy(market, direction=StrategyDirection.both)
        _run(market, strategy, 2)

        assert strategy.allocation.positions == []

    def test_no_entry_while_any_indicator_is_nan(self):
        rows = [
            _row(close=98.0, trend_ma=NAN, pullback_ma=99.0, atr=2.0),
            _row(close=100.0, trend_ma=90.0, pullback_ma=99.0, atr=2.0),
        ]
        market = _market(rows)
        strategy = _strategy(market)
        _run(market, strategy, 2)

        # bar 0 has NaN trend_ma so prev_close never gets set -- bar 1 has no
        # prior-bar context to detect a reclaim against.
        assert strategy.allocation.positions == []

    def test_no_entry_when_atr_is_non_positive(self):
        rows = [
            _row(close=98.0, trend_ma=90.0, pullback_ma=99.0, atr=2.0),
            _row(close=100.0, trend_ma=90.0, pullback_ma=99.0, atr=0.0),
        ]
        market = _market(rows)
        strategy = _strategy(market)
        _run(market, strategy, 2)

        assert strategy.allocation.positions == []


class TestPullbackToMaExits:
    def test_exits_at_stop_loss(self):
        rows = [
            *RECLAIM_LONG,
            _row(close=97.0, high=100.0, low=96.0, trend_ma=90.0, pullback_ma=99.0, atr=2.0),
        ]
        market = _market(rows)
        strategy = _strategy(market, stop_atr_mult=1.0, risk_reward_ratio=2.0)
        _run(market, strategy, 3)

        assert strategy.allocation.positions == []
        trade = strategy.trade_history[-1]
        assert trade.close_reason == CloseReason.sl
        assert trade.close_price == pytest.approx(97.0)

    def test_exits_at_take_profit(self):
        rows = [
            *RECLAIM_LONG,
            _row(close=106.0, high=107.0, low=100.0, trend_ma=90.0, pullback_ma=99.0, atr=2.0),
        ]
        market = _market(rows)
        strategy = _strategy(market, stop_atr_mult=1.0, risk_reward_ratio=2.0)
        _run(market, strategy, 3)

        assert strategy.allocation.positions == []
        trade = strategy.trade_history[-1]
        assert trade.close_reason == CloseReason.tp
        assert trade.close_price == pytest.approx(106.0)
