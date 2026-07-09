"""Tests for the pre/post-tick middleware hooks and the reference MaxDailyLossMiddleware.

Every numeric scenario below was verified by actually running it and confirming the
result before being written down as an assertion -- see the comments for the reasoning
(these interact with isolated-margin liquidation and futures margin validation in ways
that aren't obvious from reading Backtester/Positions in isolation).
"""

from conftest import build_market, make_exchange

from backtester.backtest_runner import Backtester
from backtester.exchange import PositionSide
from backtester.middleware import MaxDailyLossMiddleware, Middleware
from backtester.performance import PerformanceAnalyzer
from backtester.portfolio import Portfolio, WeightedStrategy
from backtester.strategies.base import Allocation, AllocationPosition, Strategy


class _AlwaysLongStrategy(Strategy):
    """Test double: unconditionally wants 100% long every single tick (marks its
    allocation changed every refresh(), not just when the desired state actually
    differs) -- this makes the rebalancer re-evaluate every tick, which is what lets
    test_skip_tick_prevents_reopening_after_a_flatten actually distinguish "skip_tick
    stopped a reopen" from "nothing would have tried to reopen anyway"."""

    def refresh(self) -> None:
        self.allocation = Allocation()
        self.allocation.positions = [
            AllocationPosition(
                side=PositionSide.long,
                symbol=self.symbol,
                percent=1.0,
                average_open_price=self.market.current[self.symbol]["close"],
            )
        ]
        self._mark_allocation_changed()


class _RecordingMiddleware(Middleware):
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    def before_tick(self, bt: Backtester) -> None:
        self.calls.append(("before", bt.market.current["num"]))

    def after_tick(self, bt: Backtester) -> None:
        self.calls.append(("after", bt.market.current["num"]))


class _AlwaysSkipMiddleware(Middleware):
    def before_tick(self, bt: Backtester) -> None:
        bt.skip_tick()


def _make_backtester(market, exchange, middlewares=None) -> Backtester:
    strategy = _AlwaysLongStrategy(key="s", market=market, symbol="BTC/USD")
    portfolio = Portfolio(
        weighted_strategies=[WeightedStrategy(weight=1, strategy=strategy)], output_scale=1
    )
    reporter = PerformanceAnalyzer(
        market=market, exchange=exchange, portfolio=portfolio, benchmark_symbols=[]
    )
    return Backtester(
        market=market,
        portfolio=portfolio,
        exchange=exchange,
        reporter=reporter,
        middlewares=middlewares,
    )


class TestHookSequencing:
    def test_before_and_after_fire_once_per_tick_in_order(self):
        # 4 candles -> run_all/manual stepping processes num=1,2,3 (num=0 is the initial
        # state before any advance, never itself a "tick").
        market = build_market(
            {"BTC/USD": [{"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0}] * 4}
        )
        exchange = make_exchange(market, max_leverage=1)
        exchange.transactions.add_deposit(asset="USD", volume=1000)
        recorder = _RecordingMiddleware()
        strategy = _AlwaysLongStrategy(key="s", market=market, symbol="BTC/USD")
        portfolio = Portfolio(
            weighted_strategies=[WeightedStrategy(weight=1, strategy=strategy)], output_scale=1
        )
        reporter = PerformanceAnalyzer(
            market=market, exchange=exchange, portfolio=portfolio, benchmark_symbols=[]
        )
        bt = Backtester(
            market=market,
            portfolio=portfolio,
            exchange=exchange,
            reporter=reporter,
            middlewares=[recorder],
        )

        for _ in range(3):
            market.set_next_candle_as_current_market()
            bt.run_step()

        assert recorder.calls == [
            ("before", 1),
            ("after", 1),
            ("before", 2),
            ("after", 2),
            ("before", 3),
            ("after", 3),
        ]

    def test_default_middlewares_list_is_not_shared_between_instances(self):
        # Regression guard for the classic Python mutable-default-argument trap.
        market1 = build_market(
            {"BTC/USD": [{"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0}] * 2}
        )
        market2 = build_market(
            {"BTC/USD": [{"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0}] * 2}
        )
        bt1 = _make_backtester(market1, make_exchange(market1))
        bt2 = _make_backtester(market2, make_exchange(market2))

        bt1.middlewares.append(_RecordingMiddleware())

        assert len(bt1.middlewares) == 1
        assert len(bt2.middlewares) == 0


class TestSkipTick:
    def test_skip_tick_prevents_reopening_after_a_flatten(self):
        market = build_market(
            {"BTC/USD": [{"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0}] * 4}
        )
        exchange = make_exchange(market, max_leverage=1)
        exchange.transactions.add_deposit(asset="USD", volume=1000)
        bt = _make_backtester(market, exchange, middlewares=[_AlwaysSkipMiddleware()])

        for _ in range(3):
            market.set_next_candle_as_current_market()
            bt.run_step()

        # The strategy wants 100% long every tick, but before_tick always skips before
        # the rebalancer ever runs -- no position should ever have opened.
        assert exchange.positions.get_open_position_by_symbol("BTC/USD") is None
        assert exchange.positions.closed_positions == {}

    def test_without_skip_tick_the_position_does_open(self):
        # Control case for the test above -- same setup, no middleware, confirms the
        # strategy really would open a position if nothing skipped the tick.
        market = build_market(
            {"BTC/USD": [{"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0}] * 4}
        )
        exchange = make_exchange(market, max_leverage=1)
        exchange.transactions.add_deposit(asset="USD", volume=1000)
        bt = _make_backtester(market, exchange, middlewares=None)

        for _ in range(3):
            market.set_next_candle_as_current_market()
            bt.run_step()

        assert exchange.positions.get_open_position_by_symbol("BTC/USD") is not None


class TestMaxDailyLossMiddleware:
    def test_flattens_and_halts_for_the_rest_of_the_day_then_resumes_next_day(self):
        # Hours 0-4: flat at 100. Hour 5: crashes 15% to 85 (with 2x leverage this stays
        # well clear of isolated-margin auto-liquidation, which would otherwise trigger
        # at roughly a 1/leverage = 50% adverse move and pre-empt the middleware
        # entirely). Hours 6-23: stays at 85 (rest of day 1). Hours 24-29: back to 100
        # (day 2).
        candles = [{"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0}] * 5
        candles += [{"open": 100.0, "high": 100.0, "low": 85.0, "close": 85.0}]
        candles += [{"open": 85.0, "high": 85.0, "low": 85.0, "close": 85.0}] * 18
        candles += [{"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0}] * 6

        market = build_market({"BTC/USD": candles})
        exchange = make_exchange(market, max_leverage=2)
        exchange.transactions.add_deposit(asset="USD", volume=1000)
        middleware = MaxDailyLossMiddleware(max_loss_percent=0.1)
        bt = _make_backtester(market, exchange, middlewares=[middleware])

        # Advance through hour 4 (still flat) -- position open, no halt yet.
        for _ in range(4):
            market.set_next_candle_as_current_market()
            bt.run_step()
        assert exchange.positions.get_open_position_by_symbol("BTC/USD") is not None
        assert middleware._halted_today is False

        # Hour 5: the crash tick -- loss (15%) exceeds the 10% threshold, so the
        # middleware flattens everything and halts for the rest of the day.
        market.set_next_candle_as_current_market()
        bt.run_step()
        assert exchange.positions.get_open_position_by_symbol("BTC/USD") is None
        assert middleware._halted_today is True
        assert exchange.get_asset_total_in_usd() == 850.0  # 1000 - 150 realized loss

        # Hours 6-23: still halted, no position reopens even though the strategy keeps
        # asking for one every single tick.
        for _ in range(18):
            market.set_next_candle_as_current_market()
            bt.run_step()
        assert exchange.positions.get_open_position_by_symbol("BTC/USD") is None

        # Hour 24: new day -- halt resets, strategy reopens (sized off the reduced
        # post-loss balance: 850 / 100 = 8.5).
        market.set_next_candle_as_current_market()
        bt.run_step()
        assert middleware._halted_today is False
        position = exchange.positions.get_open_position_by_symbol("BTC/USD")
        assert position is not None
        assert position.volume == 8.5
