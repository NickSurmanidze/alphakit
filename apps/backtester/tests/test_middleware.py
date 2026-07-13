"""Tests for the pre/post-tick middleware hooks and the reference MaxDailyLossMiddleware.

Every numeric scenario below was verified by actually running it and confirming the
result before being written down as an assertion -- see the comments for the reasoning
(these interact with isolated-margin liquidation and futures margin validation in ways
that aren't obvious from reading Backtester/Positions in isolation).
"""

import pytest
from conftest import build_market, make_exchange

from backtester.backtest_runner import Backtester
from backtester.exchange import PositionSide
from backtester.middleware import MaxDailyLossMiddleware, Middleware, TradeifyDrawdownMiddleware
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


class _StubBacktester:
    """Minimal Backtester stand-in exposing just what TradeifyDrawdownMiddleware reads:
    .exchange and .skip_tick(). Driving the middleware directly (rather than through a
    full Backtester + strategy + rebalancer) keeps this purely a test of the drawdown
    timing/threshold logic -- P&L is injected directly via Balance methods instead of
    being earned through real trades."""

    def __init__(self, exchange) -> None:
        self.exchange = exchange
        self.skip_tick_calls = 0

    def skip_tick(self) -> None:
        self.skip_tick_calls += 1


class TestTradeifyDrawdownMiddleware:
    def test_eod_trailing_drawdown_full_scenario(self):
        # Hourly candles from 2024-01-01T00:00 -- date() changes at candle index 24
        # (2024-01-02 00:00), 48 (01-03), 72 (01-04). num=0 is the initial state and is
        # never processed (matches the established convention in this file's other
        # tests) -- ticks are driven via set_next_candle_as_current_market() + explicit
        # before_tick/after_tick calls, starting from num=1.
        market = build_market(
            {"BTC/USD": [{"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0}] * 100}
        )
        exchange = make_exchange(market, max_leverage=1)
        exchange.transactions.add_deposit(asset="USD", volume=10_000)
        middleware = TradeifyDrawdownMiddleware(drawdown_percent=0.05)
        stub = _StubBacktester(exchange)

        def advance(n: int) -> None:
            for _ in range(n):
                market.set_next_candle_as_current_market()
                middleware.before_tick(stub)
                middleware.after_tick(stub)

        # num=1: first tick ever -- seeds the trailing high-water mark from the current
        # balance. Day-1 seeding must never itself count as a breach.
        advance(1)
        assert middleware.account_failed is False
        assert middleware._highest_eod_balance == pytest.approx(10_000)
        assert middleware._max_allowed_balance == pytest.approx(9_500)

        # num=2..23: rest of day 1, balance unchanged.
        advance(22)

        # num=24: day boundary (day1 -> day2). EOD balance (10000) is above the 9500
        # threshold -- no breach, and the high-water mark stays at 10000 (no new high).
        advance(1)
        assert middleware.account_failed is False
        assert middleware._highest_eod_balance == pytest.approx(10_000)

        # Simulate a $2000 gain during day 2 (balance -> 12000).
        exchange.balance.increase_asset_balance(asset="USD", volume=2_000)
        advance(23)  # num=25..47: rest of day 2

        # num=48: day boundary (day2 -> day3). EOD balance (12000) is a new high, but it
        # also crosses the lock threshold (10_000 * 1.05 + 100 = 10_600) -- so instead
        # of trailing to 12000 * 0.95 = 11400, the floor LOCKS permanently at
        # initial_balance + lock_grace_dollars = 10_100.
        advance(1)
        assert middleware.account_failed is False
        assert middleware.locked is True
        assert middleware._highest_eod_balance == pytest.approx(12_000)
        assert middleware._max_allowed_balance == pytest.approx(10_100)

        # Simulate a $1950 loss during day 3 (balance -> 10050, below the locked 10100
        # floor but well above what the old trailing-only floor of 11400 would have
        # been -- this scenario specifically exercises the locked floor, not the trail).
        exchange.balance.reduce_asset_balance(asset="USD", volume=1_950)
        advance(23)  # num=49..71: rest of day 3

        # num=72: day boundary (day3 -> day4). EOD balance (10050) breaches the locked
        # 10100 floor -- flattens (no-op, no open positions), cancels orders (no-op),
        # fails the account, and halts this tick.
        advance(1)
        assert middleware.account_failed is True
        assert stub.skip_tick_calls == 1
        # Real balance/equity is never mutated by the middleware itself -- still exactly
        # what we set it to, not zeroed.
        assert exchange.get_asset_total_in_usd() == pytest.approx(10_050)

        # num=73: once failed, the halt is permanent (unlike MaxDailyLossMiddleware,
        # there's no next-day reset) -- keeps flattening/halting every subsequent tick.
        advance(1)
        assert middleware.account_failed is True
        assert stub.skip_tick_calls == 2
        assert exchange.get_asset_total_in_usd() == pytest.approx(10_050)

    def test_locked_floor_does_not_rise_further_as_balance_keeps_climbing(self):
        # Same $10k/5% setup as above, but instead of a loss after locking, balance
        # keeps climbing -- the floor must stay pinned at 10_100, not keep trailing.
        market = build_market(
            {"BTC/USD": [{"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0}] * 150}
        )
        exchange = make_exchange(market, max_leverage=1)
        exchange.transactions.add_deposit(asset="USD", volume=10_000)
        middleware = TradeifyDrawdownMiddleware(drawdown_percent=0.05)
        stub = _StubBacktester(exchange)

        def advance(n: int) -> None:
            for _ in range(n):
                market.set_next_candle_as_current_market()
                middleware.before_tick(stub)
                middleware.after_tick(stub)

        advance(1)  # num=1: seed at 10_000
        advance(22)  # rest of day 1

        # Gain past the lock threshold (10_600) during day 2 -> balance 12_000.
        exchange.balance.increase_asset_balance(asset="USD", volume=2_000)
        advance(24)  # num=24 (day1->day2 boundary, no new high yet) .. num=47

        advance(1)  # num=48: day2->day3 boundary -- locks at floor 10_100
        assert middleware.locked is True
        assert middleware._max_allowed_balance == pytest.approx(10_100)

        # Balance keeps climbing well past the lock trigger during day 3.
        exchange.balance.increase_asset_balance(asset="USD", volume=20_000)  # -> 32_000
        advance(23)  # rest of day 3

        advance(1)  # num=72: day3->day4 boundary
        assert middleware.account_failed is False
        assert middleware.locked is True
        # Floor stays pinned at 10_100 -- NOT 32_000 * 0.95 = 30_400, which is what
        # the pre-lock trailing formula would have produced.
        assert middleware._max_allowed_balance == pytest.approx(10_100)
        assert middleware._highest_eod_balance == pytest.approx(32_000)

    def test_account_can_still_fail_while_locked(self):
        # Confirms the locked floor is a real breach threshold, not just a reported
        # number -- a large-enough drop after locking still fails the account.
        market = build_market(
            {"BTC/USD": [{"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0}] * 100}
        )
        exchange = make_exchange(market, max_leverage=1)
        exchange.transactions.add_deposit(asset="USD", volume=10_000)
        middleware = TradeifyDrawdownMiddleware(drawdown_percent=0.05)
        stub = _StubBacktester(exchange)

        def advance(n: int) -> None:
            for _ in range(n):
                market.set_next_candle_as_current_market()
                middleware.before_tick(stub)
                middleware.after_tick(stub)

        advance(1)  # num=1: seed at 10_000
        advance(22)

        exchange.balance.increase_asset_balance(asset="USD", volume=2_000)  # -> 12_000
        advance(24)

        advance(1)  # num=48: locks, floor = 10_100
        assert middleware.locked is True

        # Crash straight through the locked floor (12_000 -> 5_000).
        exchange.balance.reduce_asset_balance(asset="USD", volume=7_000)
        advance(23)

        advance(1)  # num=72: breaches the locked floor
        assert middleware.account_failed is True
        assert middleware.locked is True  # stays True, doesn't get reset by failure
        assert stub.skip_tick_calls == 1

    def test_locked_stays_true_permanently_once_set(self):
        # Once locked, later days that make new (but sub-lock-threshold-irrelevant)
        # highs must not somehow flip locked back to False -- there's no unlock path.
        market = build_market(
            {"BTC/USD": [{"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0}] * 150}
        )
        exchange = make_exchange(market, max_leverage=1)
        exchange.transactions.add_deposit(asset="USD", volume=10_000)
        middleware = TradeifyDrawdownMiddleware(drawdown_percent=0.05)
        stub = _StubBacktester(exchange)

        def advance(n: int) -> None:
            for _ in range(n):
                market.set_next_candle_as_current_market()
                middleware.before_tick(stub)
                middleware.after_tick(stub)

        advance(1)
        advance(22)
        assert middleware.locked is False

        exchange.balance.increase_asset_balance(asset="USD", volume=2_000)  # -> 12_000
        advance(24)
        advance(1)  # num=48: locks
        assert middleware.locked is True

        # A further gain and several more day boundaries pass -- still locked.
        exchange.balance.increase_asset_balance(asset="USD", volume=5_000)  # -> 17_000
        advance(23)
        advance(1)  # num=72
        assert middleware.locked is True
        advance(23)
        advance(1)  # num=96
        assert middleware.locked is True
