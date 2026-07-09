"""Tests for merge_reports -- combining N independently-run backtests' PerformanceAnalyzer
results into one, mirroring the legacy notebook's Reporter.merge_external_snapshots
pattern (see notebooks/22_signals_merged_report_original.ipynb) but as a single call
instead of a hand-rolled snapshot-merge + trades-concat + generate_report dance.
"""

import pytest
from conftest import build_market, make_exchange

from backtester.backtest_runner import Backtester
from backtester.exchange import PositionSide
from backtester.performance import PerformanceAnalyzer, merge_reports
from backtester.portfolio import Portfolio, WeightedStrategy
from backtester.strategies import CloseReason
from backtester.strategies.base import Allocation, AllocationPosition, Strategy


class _OpenThenCloseStrategy(Strategy):
    """Opens 100% long at tick 2, closes at tick 4 -- a single deterministic trade,
    enough to exercise merge_reports without needing indicator warmup."""

    def refresh(self) -> None:
        num = self.market.current["num"]
        price = self.market.current[self.symbol]["close"]

        if num == 2 and not self.allocation.positions:
            self.allocation = Allocation()
            self.allocation.positions = [
                AllocationPosition(
                    side=PositionSide.long,
                    symbol=self.symbol,
                    percent=1.0,
                    average_open_price=price,
                )
            ]
            self._mark_allocation_changed()
            self.open_trade(side=PositionSide.long, open_price=price)
        elif num == 4 and self.allocation.positions:
            self.allocation = Allocation()
            self._mark_allocation_changed()
            self.close_trade(close_price=price, reason=CloseReason.signal)


class _NoOpStrategy(Strategy):
    def refresh(self) -> None:
        pass


def _run_open_close_backtest(
    symbol: str, prices: list[float], deposit: float
) -> PerformanceAnalyzer:
    candles = [{"open": p, "high": p, "low": p, "close": p} for p in prices]
    market = build_market({symbol: candles})
    # max_leverage=10 (not 1): closing a fully-margined 1x position hits a separate,
    # pre-existing margin-validation quirk unrelated to merge_reports -- see
    # test_middleware.py's module docstring for the same workaround/reasoning.
    exchange = make_exchange(market, max_leverage=10)
    exchange.transactions.add_deposit(asset="USD", volume=deposit)
    strategy = _OpenThenCloseStrategy(key="s", market=market, symbol=symbol)
    portfolio = Portfolio(
        weighted_strategies=[WeightedStrategy(weight=1, strategy=strategy)], output_scale=1
    )
    reporter = PerformanceAnalyzer(
        market=market, exchange=exchange, portfolio=portfolio, benchmark_symbols=[]
    )
    market.reset()
    Backtester(market=market, portfolio=portfolio, exchange=exchange, reporter=reporter).run_all()
    return reporter


def _run_noop_backtest(symbol: str, n_candles: int, deposit: float) -> PerformanceAnalyzer:
    candles = [{"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0}] * n_candles
    market = build_market({symbol: candles})
    exchange = make_exchange(market, max_leverage=1)
    exchange.transactions.add_deposit(asset="USD", volume=deposit)
    strategy = _NoOpStrategy(key="s", market=market, symbol=symbol)
    portfolio = Portfolio(
        weighted_strategies=[WeightedStrategy(weight=1, strategy=strategy)], output_scale=1
    )
    reporter = PerformanceAnalyzer(
        market=market, exchange=exchange, portfolio=portfolio, benchmark_symbols=[]
    )
    market.reset()
    Backtester(market=market, portfolio=portfolio, exchange=exchange, reporter=reporter).run_all()
    return reporter


class TestMergeReports:
    def test_merges_trades_from_every_source_analyzer(self):
        r1 = _run_open_close_backtest("BTC/USD", [100.0] * 3 + [110.0] * 3, deposit=1000)
        r2 = _run_open_close_backtest("ETH/USD", [50.0] * 3 + [55.0] * 3, deposit=2000)
        assert len(r1.trades) == 1
        assert len(r2.trades) == 1

        merged = merge_reports([r1, r2], key="combined")

        assert len(merged.trades) == 2
        assert merged.key == "combined"

    def test_sums_balance_at_each_shared_timestamp(self):
        # r1: $1000 -> $1100 (10% gain on a fully-allocated long, 100 -> 110).
        # r2: $2000 -> $2200 (same 10% gain, 50 -> 55).
        # Merged balance at each timestamp should be the exact sum of both.
        r1 = _run_open_close_backtest("BTC/USD", [100.0] * 3 + [110.0] * 3, deposit=1000)
        r2 = _run_open_close_backtest("ETH/USD", [50.0] * 3 + [55.0] * 3, deposit=2000)

        merged = merge_reports([r1, r2], key="combined")

        for ts, snapshot in merged.raw_snapshots.items():
            expected = r1.raw_snapshots[ts]["balance"] + r2.raw_snapshots[ts]["balance"]
            assert snapshot["balance"] == expected
        # Spot-check the actual numbers, not just internal consistency.
        last_ts = max(merged.raw_snapshots)
        assert merged.raw_snapshots[last_ts]["balance"] == 3300.0

    def test_no_skipped_timestamps_when_timelines_match(self):
        r1 = _run_open_close_backtest("BTC/USD", [100.0] * 3 + [110.0] * 3, deposit=1000)
        r2 = _run_open_close_backtest("ETH/USD", [50.0] * 3 + [55.0] * 3, deposit=2000)

        merged = merge_reports([r1, r2], key="combined")

        assert merged.skipped_timestamps == []

    def test_reports_skipped_timestamps_when_timelines_mismatch(self):
        # r2's timeline is shorter than r1's -- merge_external_snapshots silently skips
        # summing at any timestamp not present in every source; skipped_timestamps
        # surfaces exactly which ones so a caller can decide if that's acceptable.
        r1 = _run_noop_backtest("BTC/USD", n_candles=5, deposit=1000)
        r2 = _run_noop_backtest("ETH/USD", n_candles=3, deposit=1000)

        merged = merge_reports([r1, r2], key="combined")

        r1_timestamps = set(r1.raw_snapshots.keys())
        r2_timestamps = set(r2.raw_snapshots.keys())
        assert set(merged.skipped_timestamps) == r1_timestamps - r2_timestamps
        assert len(merged.skipped_timestamps) == 2

    def test_raises_on_empty_analyzer_list(self):
        with pytest.raises(ValueError, match="at least one analyzer"):
            merge_reports([])
