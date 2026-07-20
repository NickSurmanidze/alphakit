"""Unit tests for VwapMeanReversionStrategy -- directly drives Strategy.refresh()
against a controlled Market with hand-picked vwap/std series, so band-touch
entries, reversion exits, stop-losses, and the session-warmup skip are asserted
exactly."""

import pandas as pd
import pytest

from backtester.exchange import PositionSide
from backtester.market import Market
from backtester.strategies import CloseReason, StrategyDirection, VwapMeanReversionStrategy

SYMBOL = "MES/USD"
INTERVAL = 5
UNIT_OF_TIME = "minute"
VWAP_KEY = "vwap"
STD_KEY = "vwap_std"

NAN = float("nan")


def _market(rows: list[dict], freq: str = "5min") -> Market:
    """Each row: open, high, low, close, vwap, std."""
    index = pd.date_range("2024-01-01", periods=len(rows), freq=freq)
    ohlc = pd.DataFrame(
        {
            "time_open": index,
            "time_close": index,
            "open": [r.get("open", r["close"]) for r in rows],
            "high": [r["high"] for r in rows],
            "low": [r["low"] for r in rows],
            "close": [r["close"] for r in rows],
            "volume": 1,
        },
        index=index,
    )

    market = Market()
    market.add_market(symbol=SYMBOL, df=ohlc)
    for key, field in [(VWAP_KEY, "vwap"), (STD_KEY, "std")]:
        market.add_indicator(
            symbol=SYMBOL, interval=INTERVAL, unit_of_time=UNIT_OF_TIME,
            indicator_name=key, df=pd.Series([r.get(field, NAN) for r in rows], index=index),
        )
    market.compile()
    return market


def _row(high=100.0, low=100.0, close=100.0, vwap=100.0, std=1.0):
    return {"high": high, "low": low, "close": close, "vwap": vwap, "std": std}


def _strategy(market: Market, direction: StrategyDirection = StrategyDirection.both, **kwargs) -> VwapMeanReversionStrategy:
    return VwapMeanReversionStrategy(
        key="test", market=market, symbol=SYMBOL, direction=direction,
        vwap_indicator_key=VWAP_KEY, vwap_std_indicator_key=STD_KEY, **kwargs,
    )


def _run(market: Market, strategy: VwapMeanReversionStrategy, n: int) -> None:
    for i in range(n):
        market.set_current_market_from_num_index(i)
        strategy.refresh()


# min_bars_since_session_start=3 (default) means the first 3 bars of a session
# are warmup-skipped -- pad every scenario with 3 harmless bars before the bar
# meant to trigger anything.
WARMUP = [_row() for _ in range(3)]


class TestVwapMeanReversionEntries:
    def test_long_entry_on_lower_band_touch(self):
        # vwap=100, std=1, entry_std=2 -> lower_entry=98. Bar low touches 98.
        rows = [*WARMUP, _row(high=100.0, low=97.5, close=98.0, vwap=100.0, std=1.0)]
        market = _market(rows)
        strategy = _strategy(market, entry_std=2.0)
        _run(market, strategy, len(rows))

        assert len(strategy.allocation.positions) == 1
        position = strategy.allocation.positions[0]
        assert position.side == PositionSide.long
        assert position.average_open_price == pytest.approx(98.0)

    def test_short_entry_on_upper_band_touch(self):
        # vwap=100, std=1, entry_std=2 -> upper_entry=102. Bar high touches 102.
        rows = [*WARMUP, _row(high=102.5, low=100.0, close=102.0, vwap=100.0, std=1.0)]
        market = _market(rows)
        strategy = _strategy(market, entry_std=2.0)
        _run(market, strategy, len(rows))

        assert len(strategy.allocation.positions) == 1
        position = strategy.allocation.positions[0]
        assert position.side == PositionSide.short
        assert position.average_open_price == pytest.approx(102.0)

    def test_no_entry_before_min_bars_since_session_start(self):
        # Band touch on bar 0 of the session -- should be skipped as warmup.
        rows = [_row(high=100.0, low=97.5, close=98.0, vwap=100.0, std=1.0)]
        market = _market(rows)
        strategy = _strategy(market, entry_std=2.0)
        _run(market, strategy, len(rows))

        assert strategy.allocation.positions == []

    def test_no_entry_when_direction_is_long_only_and_band_is_upper(self):
        rows = [*WARMUP, _row(high=102.5, low=100.0, close=102.0, vwap=100.0, std=1.0)]
        market = _market(rows)
        strategy = _strategy(market, entry_std=2.0, direction=StrategyDirection.long)
        _run(market, strategy, len(rows))

        assert strategy.allocation.positions == []

    def test_no_entry_when_std_is_non_positive(self):
        rows = [*WARMUP, _row(high=100.0, low=97.5, close=98.0, vwap=100.0, std=0.0)]
        market = _market(rows)
        strategy = _strategy(market, entry_std=2.0)
        _run(market, strategy, len(rows))

        assert strategy.allocation.positions == []


class TestVwapMeanReversionExits:
    def test_exits_at_reversion_target(self):
        entry_bar = _row(high=100.0, low=97.5, close=98.0, vwap=100.0, std=1.0)
        # exit_std=0.0 -> exit target == vwap (100). Next bar's high reaches it.
        exit_bar = _row(high=100.5, low=98.0, close=100.0, vwap=100.0, std=1.0)
        rows = [*WARMUP, entry_bar, exit_bar]
        market = _market(rows)
        strategy = _strategy(market, entry_std=2.0, exit_std=0.0)
        _run(market, strategy, len(rows))

        assert strategy.allocation.positions == []
        trade = strategy.trade_history[-1]
        assert trade.close_reason == CloseReason.signal
        assert trade.close_price == pytest.approx(100.0)

    def test_exits_at_stop_loss(self):
        entry_bar = _row(high=100.0, low=97.5, close=98.0, vwap=100.0, std=1.0)
        # sl_percent=0.06 -> long stop at 98 * 0.94 = 92.12. Next bar's low breaches it.
        exit_bar = _row(high=98.0, low=90.0, close=91.0, vwap=100.0, std=1.0)
        rows = [*WARMUP, entry_bar, exit_bar]
        market = _market(rows)
        strategy = _strategy(market, entry_std=2.0, sl_percent=0.06, sl_enabled=True)
        _run(market, strategy, len(rows))

        assert strategy.allocation.positions == []
        trade = strategy.trade_history[-1]
        assert trade.close_reason == CloseReason.sl
        assert trade.close_price == pytest.approx(98.0 * 0.94)

    def test_no_stop_loss_order_when_sl_disabled(self):
        rows = [*WARMUP, _row(high=100.0, low=97.5, close=98.0, vwap=100.0, std=1.0)]
        market = _market(rows)
        strategy = _strategy(market, entry_std=2.0, sl_enabled=False)
        _run(market, strategy, len(rows))

        assert len(strategy.allocation.positions) == 1
        assert strategy.allocation.orders == []

    def test_session_reset_restarts_warmup_counter(self):
        """A new UTC calendar day resets the bars-since-session-start counter, so a
        band touch on the first bar of day 2 is still warmup-skipped even though
        the strategy has seen many bars in total."""
        day1 = [_row() for _ in range(5)]
        day2_first_bar = _row(high=100.0, low=97.5, close=98.0, vwap=100.0, std=1.0)
        rows = day1 + [day2_first_bar]
        # 5min bars within a day, but force a day boundary before the last row.
        index = pd.date_range("2024-01-01 00:00", periods=5, freq="5min").tolist()
        index += [pd.Timestamp("2024-01-02 00:00")]
        ohlc = pd.DataFrame(
            {
                "time_open": index, "time_close": index,
                "open": [r["close"] for r in rows], "high": [r["high"] for r in rows],
                "low": [r["low"] for r in rows], "close": [r["close"] for r in rows],
                "volume": 1,
            },
            index=pd.DatetimeIndex(index),
        )
        market = Market()
        market.add_market(symbol=SYMBOL, df=ohlc)
        for key, field in [(VWAP_KEY, "vwap"), (STD_KEY, "std")]:
            market.add_indicator(
                symbol=SYMBOL, interval=INTERVAL, unit_of_time=UNIT_OF_TIME,
                indicator_name=key, df=pd.Series([r["vwap" if field == "vwap" else "std"] for r in rows], index=index),
            )
        market.compile()

        strategy = _strategy(market, entry_std=2.0)
        _run(market, strategy, len(rows))

        assert strategy.allocation.positions == []
