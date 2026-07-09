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
