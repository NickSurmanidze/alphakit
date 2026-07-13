"""Composable pre/post-tick hooks -- lets code outside the core engine (a risk-control
layer, an audit sampler, ...) observe and react to every tick without Backtester knowing
anything about what it's for. See Backtester.run_step() for exactly where each hook sits
in the tick sequence.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backtester.backtest_runner import Backtester


class Middleware:
    """Base class for pre/post-tick hooks -- not an ABC, since both hooks are genuinely
    optional no-ops by default rather than a contract subclasses must fulfill; a
    middleware only needs to implement the one it cares about. Middlewares run in
    registration order for both hooks (not reversed on the way "out"): there's no
    wrapping/short-circuiting semantics here, just a flat list of independent observers,
    so the simplest order is the least surprising one."""

    def before_tick(self, bt: Backtester) -> None:
        """Runs after Exchange.run_step() has refreshed orders/positions for this candle
        (so fills/closes/liquidations triggered by this candle's price action are already
        reflected), before strategies evaluate new signals and the rebalancer acts on
        them. Call `bt.skip_tick()` here to skip strategy evaluation and rebalancing for
        the rest of this tick -- e.g. a risk-control middleware that just flattened
        everything and doesn't want a strategy signal to immediately reopen a position on
        the same candle. A middleware wanting to stay halted across multiple ticks (e.g.
        "no new trades for the rest of the trading day") tracks that state itself and
        calls `skip_tick()` again every tick during the halt window -- Backtester itself
        stays stateless about *why* a tick was skipped."""

    def after_tick(self, bt: Backtester) -> None:
        """Runs after strategies have evaluated and the rebalancer has acted (or after
        they were skipped via skip_tick()), before the performance snapshot is taken.
        Always runs, even on a skipped tick."""


class MaxDailyLossMiddleware(Middleware):
    """Flattens every open position and cancels every open order once a trading day's
    loss (realized + unrealized, vs. that day's starting balance) reaches
    `max_loss_percent`, then keeps skipping ticks for the remainder of that day so
    nothing reopens. Reference implementation for "risk control in middleware" -- not a
    fully-specified prop-trading rule set (no per-symbol limits, no trailing limits,
    no cooldown beyond end-of-day)."""

    def __init__(self, max_loss_percent: float) -> None:
        """max_loss_percent: fraction (e.g. 0.05 = 5%) of the day's starting balance
        that triggers a flatten-and-halt once realized+unrealized loss reaches it."""
        self.max_loss_percent = max_loss_percent
        self._day: date | None = None
        self._day_start_balance: float | None = None
        self._halted_today = False

    def before_tick(self, bt: Backtester) -> None:
        """Resets the day's starting balance on a new calendar day, then flattens and
        halts for the rest of the day if the loss threshold has just been breached (or
        keeps skipping ticks if already halted today)."""
        current_day = bt.exchange.market.current["time_close"].date()

        if current_day != self._day:
            self._day = current_day
            self._day_start_balance = bt.exchange.get_asset_total_in_usd()
            self._halted_today = False

        if self._halted_today:
            bt.skip_tick()
            return

        if not self._day_start_balance:
            return

        current_balance = bt.exchange.get_asset_total_in_usd()
        loss_percent = (self._day_start_balance - current_balance) / self._day_start_balance

        if loss_percent >= self.max_loss_percent:
            bt.exchange.positions.close_all_open_positions()
            bt.exchange.orders.cancel_open_orders()
            self._halted_today = True
            bt.skip_tick()


class TradeifyDrawdownMiddleware(Middleware):
    """Prop-firm-style EOD trailing drawdown, modeled on Tradeify's evaluation rule:
    once per calendar day (at the day boundary, using the *previous* day's final
    equity -- never checked intraday), compares that day's ending balance against a
    trailing threshold `highest_eod_balance_ever_reached * (1 - drawdown_percent)`. The
    threshold trails UP as new EOD highs are made, never down -- until it *locks*:
    Tradeify's real rule permanently freezes the floor at
    `initial_balance + lock_grace_dollars` the first time EOD balance reaches
    `initial_balance * (1 + drawdown_percent) + lock_grace_dollars` (confirmed for a
    $50k Growth account: $2,000/4% drawdown locks at $52,100 EOD balance, floor frozen
    at $50,100 forever after -- not verified across every account size/tier).

    On breach: flattens every open position, cancels every open order, sets
    account_failed=True, and halts permanently (skip_tick every tick from then on) --
    unlike MaxDailyLossMiddleware this never resumes. Real balance/equity numbers are
    never zeroed or altered, only trading is halted, so reporting/metrics stay
    accurate."""

    def __init__(self, drawdown_percent: float = 0.05, lock_grace_dollars: float = 100.0) -> None:
        """drawdown_percent: fraction (e.g. 0.05 = 5%) below the highest EOD balance
        ever reached that triggers a permanent flatten-and-halt, before the floor
        locks. lock_grace_dollars: the fixed dollar buffer above the initial balance
        the floor freezes at once locked (confirmed as $100 for Tradeify's $50k
        Growth tier; left overridable since it hasn't been verified elsewhere)."""
        self.drawdown_percent = drawdown_percent
        self.lock_grace_dollars = lock_grace_dollars
        self.account_failed: bool = False
        self.locked: bool = False
        self._current_day: date | None = None
        self._last_balance_seen: float | None = None
        self._initial_balance: float | None = None
        self._highest_eod_balance: float | None = None
        self._max_allowed_balance: float | None = None
        self._lock_threshold: float | None = None
        self._locked_floor: float | None = None

    def before_tick(self, bt: Backtester) -> None:
        """Once failed, keeps flattening/halting forever. Otherwise seeds the trailing
        high-water mark on the very first tick (so day 1 can never itself be a breach),
        then on every subsequent day boundary checks the previous day's final equity
        (captured by after_tick) against the trailing threshold: trailing it up on a
        new high (until it locks permanently), or failing the account on a breach."""
        if self.account_failed:
            bt.exchange.positions.close_all_open_positions()
            bt.exchange.orders.cancel_open_orders()
            bt.skip_tick()
            return

        current_day = bt.exchange.market.current["time_close"].date()

        if self._current_day is None:
            self._current_day = current_day
            self._initial_balance = bt.exchange.get_asset_total_in_usd()
            self._highest_eod_balance = self._initial_balance
            self._max_allowed_balance = self._highest_eod_balance * (1 - self.drawdown_percent)
            self._lock_threshold = (
                self._initial_balance * (1 + self.drawdown_percent) + self.lock_grace_dollars
            )
            self._locked_floor = self._initial_balance + self.lock_grace_dollars
            return

        if current_day != self._current_day:
            eod_balance = self._last_balance_seen
            assert eod_balance is not None
            assert self._max_allowed_balance is not None
            assert self._highest_eod_balance is not None
            assert self._lock_threshold is not None
            assert self._locked_floor is not None

            if eod_balance <= self._max_allowed_balance:
                self.account_failed = True
                bt.exchange.positions.close_all_open_positions()
                bt.exchange.orders.cancel_open_orders()
                bt.skip_tick()
            else:
                self._highest_eod_balance = max(self._highest_eod_balance, eod_balance)
                if not self.locked and self._highest_eod_balance >= self._lock_threshold:
                    self.locked = True
                self._max_allowed_balance = (
                    self._locked_floor
                    if self.locked
                    else self._highest_eod_balance * (1 - self.drawdown_percent)
                )

            self._current_day = current_day

    def after_tick(self, bt: Backtester) -> None:
        """Always runs (even on a skipped tick) -- records this tick's ending equity as
        the latest candidate EOD balance for whichever day is current, since
        before_tick needs the *previous* day's last balance at the moment the day
        changes."""
        self._last_balance_seen = bt.exchange.get_asset_total_in_usd()
