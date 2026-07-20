"""Unit tests for BollingerMeanReversionStrategy -- directly drives Strategy.refresh()
against a controlled Market with hand-picked bb_lower/bb_upper series, so band-touch
entries and fixed percent SL/TP exits are asserted exactly."""

import pandas as pd
import pytest

from backtester.exchange import PositionSide
from backtester.market import Market
from backtester.strategies import BollingerMeanReversionStrategy, CloseReason, StrategyDirection

SYMBOL = "MES/USD"
INTERVAL = 30
UNIT_OF_TIME = "minute"
BB_LOWER_KEY = "bb_lower"
BB_UPPER_KEY = "bb_upper"

NAN = float("nan")


def _market(rows: list[dict]) -> Market:
    """Each row: high, low, close, bb_lower, bb_upper."""
    index = pd.date_range("2024-01-01", periods=len(rows), freq="30min")
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
    for key, field in [(BB_LOWER_KEY, "bb_lower"), (BB_UPPER_KEY, "bb_upper")]:
        market.add_indicator(
            symbol=SYMBOL, interval=INTERVAL, unit_of_time=UNIT_OF_TIME,
            indicator_name=key, df=pd.Series([r.get(field, NAN) for r in rows], index=index),
        )
    market.compile()
    return market


def _row(high=100.0, low=100.0, close=100.0, bb_lower=95.0, bb_upper=105.0):
    return {"high": high, "low": low, "close": close, "bb_lower": bb_lower, "bb_upper": bb_upper}


def _strategy(market: Market, direction: StrategyDirection = StrategyDirection.both, **kwargs) -> BollingerMeanReversionStrategy:
    return BollingerMeanReversionStrategy(
        key="test", market=market, symbol=SYMBOL, direction=direction,
        bb_lower_key=BB_LOWER_KEY, bb_upper_key=BB_UPPER_KEY, **kwargs,
    )


def _run(market: Market, strategy: BollingerMeanReversionStrategy, n: int) -> None:
    for i in range(n):
        market.set_current_market_from_num_index(i)
        strategy.refresh()


class TestBollingerMeanReversionEntries:
    def test_long_entry_on_lower_band_touch(self):
        rows = [_row(high=100.0, low=94.5, close=95.0, bb_lower=95.0, bb_upper=105.0)]
        market = _market(rows)
        strategy = _strategy(market, sl_percent=0.01, tp_percent=0.01)
        _run(market, strategy, len(rows))

        assert len(strategy.allocation.positions) == 1
        position = strategy.allocation.positions[0]
        assert position.side == PositionSide.long
        assert position.average_open_price == pytest.approx(95.0)
        assert strategy.sl_price == pytest.approx(95.0 * 0.99)
        assert strategy.tp_price == pytest.approx(95.0 * 1.01)

    def test_short_entry_on_upper_band_touch(self):
        rows = [_row(high=105.5, low=100.0, close=105.0, bb_lower=95.0, bb_upper=105.0)]
        market = _market(rows)
        strategy = _strategy(market, sl_percent=0.01, tp_percent=0.01)
        _run(market, strategy, len(rows))

        assert len(strategy.allocation.positions) == 1
        position = strategy.allocation.positions[0]
        assert position.side == PositionSide.short
        assert position.average_open_price == pytest.approx(105.0)
        assert strategy.sl_price == pytest.approx(105.0 * 1.01)
        assert strategy.tp_price == pytest.approx(105.0 * 0.99)

    def test_no_long_entry_when_direction_is_short_only(self):
        rows = [_row(high=100.0, low=94.5, close=95.0, bb_lower=95.0, bb_upper=105.0)]
        market = _market(rows)
        strategy = _strategy(market, direction=StrategyDirection.short, sl_percent=0.01, tp_percent=0.01)
        _run(market, strategy, len(rows))

        assert strategy.allocation.positions == []

    def test_no_entry_when_indicators_are_nan(self):
        rows = [_row(high=100.0, low=94.5, close=95.0, bb_lower=NAN, bb_upper=NAN)]
        market = _market(rows)
        strategy = _strategy(market, sl_percent=0.01, tp_percent=0.01)
        _run(market, strategy, len(rows))

        assert strategy.allocation.positions == []


class TestBollingerMeanReversionExits:
    def test_exits_at_take_profit(self):
        entry_bar = _row(high=100.0, low=94.5, close=95.0, bb_lower=95.0, bb_upper=105.0)
        exit_bar = _row(high=96.5, low=95.0, close=96.0, bb_lower=95.0, bb_upper=105.0)
        rows = [entry_bar, exit_bar]
        market = _market(rows)
        strategy = _strategy(market, sl_percent=0.01, tp_percent=0.01)
        _run(market, strategy, len(rows))

        assert strategy.allocation.positions == []
        trade = strategy.trade_history[-1]
        assert trade.close_reason == CloseReason.tp
        assert trade.close_price == pytest.approx(95.0 * 1.01)

    def test_exits_at_stop_loss(self):
        entry_bar = _row(high=100.0, low=94.5, close=95.0, bb_lower=95.0, bb_upper=105.0)
        exit_bar = _row(high=95.0, low=90.0, close=91.0, bb_lower=95.0, bb_upper=105.0)
        rows = [entry_bar, exit_bar]
        market = _market(rows)
        strategy = _strategy(market, sl_percent=0.01, tp_percent=0.01)
        _run(market, strategy, len(rows))

        assert strategy.allocation.positions == []
        trade = strategy.trade_history[-1]
        assert trade.close_reason == CloseReason.sl
        assert trade.close_price == pytest.approx(95.0 * 0.99)
