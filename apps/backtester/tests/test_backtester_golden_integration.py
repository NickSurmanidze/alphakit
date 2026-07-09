"""Golden integration tests: middleware, the event log, and merge_reports exercised
together against the real multi-year ETH/USD CSV (not tiny synthetic fixtures), on top
of the same canonical SMA-90/150 crossover scenario the other golden tests use. Numbers
below were captured by actually running each scenario once and confirming the reasoning
in each comment -- same "golden" philosophy as test_backtester_golden.py: protects
against behavior drift, not a hand-derived proof (infeasible at this data scale).
"""

import os

import pytest

from backtester.backtest_runner import Backtester
from backtester.exchange import (
    Exchange,
    MarginAllocationType,
    MarketType,
    PositionClosed,
    PositionOpened,
)
from backtester.indicators import Indicators
from backtester.market import Market, MarketDataFromCSV
from backtester.middleware import MaxDailyLossMiddleware
from backtester.performance import PerformanceAnalyzer, merge_reports
from backtester.portfolio import Portfolio, WeightedStrategy
from backtester.strategies import MaCrossoverStrategy, StrategyDirection

DATA_FILE = os.path.join(os.path.dirname(__file__), "data", "BINANCE_ETHUSDT_1H.csv")
SYMBOL = "ETH/USD"
DATE_FROM = "2017-01-01"
DATE_TO = "2025-01-01"
FAST_MA = 90
SLOW_MA = 150


def _build_backtest(direction: StrategyDirection, key: str, middlewares=None) -> Backtester:
    market = Market()
    ohlc = MarketDataFromCSV(
        symbol=SYMBOL,
        date_from=DATE_FROM,
        date_to=DATE_TO,
        interval=1,
        unit_of_time="hour",
        path=DATA_FILE,
    ).get_df()
    market.add_market(symbol=SYMBOL, df=ohlc)
    market.add_indicator(
        symbol=SYMBOL,
        interval=1,
        unit_of_time="hour",
        indicator_name="sma_90",
        df=Indicators.sma(ohlc, FAST_MA),
    )
    market.add_indicator(
        symbol=SYMBOL,
        interval=1,
        unit_of_time="hour",
        indicator_name="sma_150",
        df=Indicators.sma(ohlc, SLOW_MA),
    )
    market.compile()

    portfolio = Portfolio(
        weighted_strategies=[
            WeightedStrategy(
                weight=1,
                strategy=MaCrossoverStrategy(
                    key=key,
                    market=market,
                    symbol=SYMBOL,
                    direction=direction,
                    fast_indicator_key="sma_90",
                    slow_indicator_key="sma_150",
                    sl_percent=0.3,
                    tp_percent=0.6,
                    sl_enabled=True,
                    tp_enabled=True,
                ),
            )
        ],
        output_scale=1,
    )
    exchange = Exchange(
        market=market,
        slippage=0.002,
        maker_fee=0.001,
        taker_fee=0.00075,
        market_type=MarketType.future,
        max_leverage=10,
        margin_allocation_type=MarginAllocationType.cross,
    )
    reporter = PerformanceAnalyzer(
        market=market, exchange=exchange, portfolio=portfolio, benchmark_symbols=[SYMBOL], key=key
    )

    market.reset()
    backtest = Backtester(
        market=market,
        portfolio=portfolio,
        exchange=exchange,
        reporter=reporter,
        middlewares=middlewares,
    )
    backtest.exchange.transactions.add_deposit(asset="USD", volume=1000)
    return backtest


class _CountingMaxDailyLossMiddleware(MaxDailyLossMiddleware):
    """Same behavior as MaxDailyLossMiddleware, plus a counter of how many times it
    actually transitioned into a halt -- for asserting on real-data trigger frequency."""

    def __init__(self, max_loss_percent: float) -> None:
        super().__init__(max_loss_percent)
        self.trigger_count = 0

    def before_tick(self, bt: Backtester) -> None:
        was_halted = self._halted_today
        super().before_tick(bt)
        if self._halted_today and not was_halted:
            self.trigger_count += 1


class TestMiddlewareAgainstRealData:
    def test_max_daily_loss_middleware_fires_and_changes_the_outcome(self):
        middleware = _CountingMaxDailyLossMiddleware(max_loss_percent=0.05)
        backtest = _build_backtest(StrategyDirection.long, key="s", middlewares=[middleware])

        backtest.run_all()

        # Real, deterministic trigger count for this exact dataset/threshold -- protects
        # against a change to the daily-loss calculation silently altering how often it
        # fires.
        assert middleware.trigger_count == 105

        # A 5% daily loss cap is much tighter than the strategy's own 30% stop-loss, so
        # blocking same-day re-entries after a bad day materially changes the outcome --
        # confirms the middleware causally affects the run, not just that it "ran".
        # Baseline (no middleware) net_return_percent is 2336.12% (test_backtester_golden.py).
        assert backtest.reporter.summary["s"]["net_return_percent"] == pytest.approx(
            481.96526418226335, rel=1e-6
        )

        # The strategy's own trade_history (reporter.trades) is untouched by
        # exchange-level middleware actions -- close_all_open_positions() operates
        # directly on Exchange, bypassing Strategy/Portfolio's allocation tracking
        # entirely. This is a real, worth-knowing interaction, not a bug: it's exactly
        # why skip_tick() exists (see middleware.py) -- to stop the rebalancer from
        # immediately re-opening what the middleware just closed, rather than trying to
        # keep the strategy layer "informed".
        assert len(backtest.reporter.trades) == 224


class TestEventLogAgainstRealData:
    def test_position_events_reconcile_with_strategy_trades(self):
        backtest = _build_backtest(StrategyDirection.long, key="s")

        backtest.run_all()

        # MaCrossoverStrategy only ever fully opens then fully closes (no partial
        # increase/reduce) -- so every strategy-level trade should correspond to exactly
        # one PositionOpened and one PositionClosed exchange-level event.
        trades = backtest.reporter.trades
        opened = backtest.exchange.event_log.get_events(PositionOpened)
        closed = backtest.exchange.event_log.get_events(PositionClosed)
        assert len(opened) == len(trades)
        assert len(closed) == len(trades)
        assert len(backtest.exchange.positions.closed_positions) == len(trades)

        # Every trade has a matching PositionClosed event at the same close time.
        closed_by_time = {e.time: e for e in closed}
        for trade in trades:
            assert trade.time_close.to_pydatetime() in closed_by_time

        # NOT asserted: that a trade's `pnl` sign (winner/loser) always matches its
        # PositionClosed event's `realized_pnl_in_usd` sign. It usually does, but not
        # always -- `trade.pnl` is a slippage/fee-free ratio of the strategy's own
        # signal prices (open_price/close_price), while `realized_pnl_in_usd` is computed
        # from the actual exchange fill prices, which include slippage on both entry and
        # exit (see Orders.add_order_processing_logic). For a trade with a small enough
        # nominal price move, round-trip slippage can flip the sign -- confirmed on this
        # exact dataset (e.g. a trade with trade.pnl=+0.03% closed at
        # realized_pnl_in_usd=-3.34 once slippage is accounted for). Not a bug in either
        # number, just two different things being measured.


class TestMergeReportsAgainstRealData:
    def test_merging_independent_long_and_short_runs(self):
        long_backtest = _build_backtest(StrategyDirection.long, key="long")
        short_backtest = _build_backtest(StrategyDirection.short, key="short")
        long_backtest.run_all()
        short_backtest.run_all()
        assert len(long_backtest.reporter.trades) == 224
        assert len(short_backtest.reporter.trades) == 224

        merged = merge_reports([long_backtest.reporter, short_backtest.reporter], key="combined")

        assert len(merged.trades) == 448
        assert merged.skipped_timestamps == []
        # Trade-list-derived stats (not balance/compounding-derived) match the
        # single-strategy StrategyDirection.both golden test's numbers exactly
        # (test_backtester_golden_mixed.py) -- two independently-run, then-merged
        # single-direction backtests produce the same underlying trade set as one
        # mixed-direction backtest, even though the balance/exposure dynamics of a
        # shared account vs. two summed separate accounts differ.
        assert merged.summary["combined"]["winner_trades"] == 187
        assert merged.summary["combined"]["loser_trades"] == 261
        assert merged.summary["combined"]["profit_factor"] == pytest.approx(
            1.7601165578480527, rel=1e-6
        )
