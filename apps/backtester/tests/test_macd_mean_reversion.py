"""Unit tests for MacdMeanReversionStrategy -- directly drives Strategy.refresh()
against a controlled Market with hand-picked histogram/upper/lower series, so
extreme-histogram entries and fixed percent SL/TP exits are asserted exactly."""

import pandas as pd
import pytest

from backtester.exchange import PositionSide
from backtester.market import Market
from backtester.strategies import CloseReason, MacdMeanReversionStrategy, StrategyDirection

SYMBOL = "MES/USD"
INTERVAL = 15
UNIT_OF_TIME = "minute"
HIST_KEY = "macd_hist"
UPPER_KEY = "hist_upper"
LOWER_KEY = "hist_lower"

NAN = float("nan")


def _market(rows: list[dict]) -> Market:
    """Each row: high, low, close, macd_hist, hist_upper, hist_lower."""
    index = pd.date_range("2024-01-01", periods=len(rows), freq="15min")
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
    for key, field in [(HIST_KEY, "macd_hist"), (UPPER_KEY, "hist_upper"), (LOWER_KEY, "hist_lower")]:
        market.add_indicator(
            symbol=SYMBOL, interval=INTERVAL, unit_of_time=UNIT_OF_TIME,
            indicator_name=key, df=pd.Series([r.get(field, NAN) for r in rows], index=index),
        )
    market.compile()
    return market


def _row(high=100.0, low=100.0, close=100.0, macd_hist=0.0, hist_upper=1.0, hist_lower=-1.0):
    return {
        "high": high, "low": low, "close": close,
        "macd_hist": macd_hist, "hist_upper": hist_upper, "hist_lower": hist_lower,
    }


def _strategy(market: Market, direction: StrategyDirection = StrategyDirection.both, **kwargs) -> MacdMeanReversionStrategy:
    return MacdMeanReversionStrategy(
        key="test", market=market, symbol=SYMBOL, direction=direction,
        histogram_key=HIST_KEY, upper_key=UPPER_KEY, lower_key=LOWER_KEY, **kwargs,
    )


def _run(market: Market, strategy: MacdMeanReversionStrategy, n: int) -> None:
    for i in range(n):
        market.set_current_market_from_num_index(i)
        strategy.refresh()


class TestMacdMeanReversionEntries:
    def test_long_entry_when_histogram_at_or_below_lower_band(self):
        rows = [_row(close=100.0, macd_hist=-1.5, hist_upper=1.0, hist_lower=-1.0)]
        market = _market(rows)
        strategy = _strategy(market, sl_percent=0.01, tp_percent=0.01)
        _run(market, strategy, len(rows))

        assert len(strategy.allocation.positions) == 1
        position = strategy.allocation.positions[0]
        assert position.side == PositionSide.long
        assert position.average_open_price == pytest.approx(100.0)
        assert strategy.sl_price == pytest.approx(100.0 * 0.99)
        assert strategy.tp_price == pytest.approx(100.0 * 1.01)

    def test_short_entry_when_histogram_at_or_above_upper_band(self):
        rows = [_row(close=100.0, macd_hist=1.5, hist_upper=1.0, hist_lower=-1.0)]
        market = _market(rows)
        strategy = _strategy(market, sl_percent=0.01, tp_percent=0.01)
        _run(market, strategy, len(rows))

        assert len(strategy.allocation.positions) == 1
        position = strategy.allocation.positions[0]
        assert position.side == PositionSide.short
        assert position.average_open_price == pytest.approx(100.0)
        assert strategy.sl_price == pytest.approx(100.0 * 1.01)
        assert strategy.tp_price == pytest.approx(100.0 * 0.99)

    def test_no_entry_when_histogram_inside_band(self):
        rows = [_row(close=100.0, macd_hist=0.5, hist_upper=1.0, hist_lower=-1.0)]
        market = _market(rows)
        strategy = _strategy(market, sl_percent=0.01, tp_percent=0.01)
        _run(market, strategy, len(rows))

        assert strategy.allocation.positions == []

    def test_no_long_entry_when_direction_is_short_only(self):
        rows = [_row(close=100.0, macd_hist=-1.5, hist_upper=1.0, hist_lower=-1.0)]
        market = _market(rows)
        strategy = _strategy(market, direction=StrategyDirection.short, sl_percent=0.01, tp_percent=0.01)
        _run(market, strategy, len(rows))

        assert strategy.allocation.positions == []

    def test_no_entry_when_indicators_are_nan(self):
        rows = [_row(close=100.0, macd_hist=NAN, hist_upper=NAN, hist_lower=NAN)]
        market = _market(rows)
        strategy = _strategy(market, sl_percent=0.01, tp_percent=0.01)
        _run(market, strategy, len(rows))

        assert strategy.allocation.positions == []


class TestMacdMeanReversionExits:
    def test_exits_at_take_profit(self):
        entry_bar = _row(close=100.0, macd_hist=-1.5, hist_upper=1.0, hist_lower=-1.0)
        exit_bar = _row(high=101.5, low=100.0, close=101.0, macd_hist=-0.5, hist_upper=1.0, hist_lower=-1.0)
        rows = [entry_bar, exit_bar]
        market = _market(rows)
        strategy = _strategy(market, sl_percent=0.01, tp_percent=0.01)
        _run(market, strategy, len(rows))

        assert strategy.allocation.positions == []
        trade = strategy.trade_history[-1]
        assert trade.close_reason == CloseReason.tp
        assert trade.close_price == pytest.approx(100.0 * 1.01)

    def test_exits_at_stop_loss(self):
        entry_bar = _row(close=100.0, macd_hist=-1.5, hist_upper=1.0, hist_lower=-1.0)
        exit_bar = _row(high=100.0, low=95.0, close=96.0, macd_hist=-2.0, hist_upper=1.0, hist_lower=-1.0)
        rows = [entry_bar, exit_bar]
        market = _market(rows)
        strategy = _strategy(market, sl_percent=0.01, tp_percent=0.01)
        _run(market, strategy, len(rows))

        assert strategy.allocation.positions == []
        trade = strategy.trade_history[-1]
        assert trade.close_reason == CloseReason.sl
        assert trade.close_price == pytest.approx(100.0 * 0.99)
