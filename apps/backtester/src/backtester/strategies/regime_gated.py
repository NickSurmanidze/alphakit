"""Regime-gated strategy: wraps another Strategy instance and only allows it to
open *new* entries while the market is currently in one of a precomputed regime
indicator's allowed values -- management of an already-open position is never
gated, only the decision to open a new one."""

from backtester.market import Market
from backtester.strategies.base import Strategy


class RegimeGatedStrategy(Strategy):
    """Delegates every decision to `inner` (any other Strategy instance, already
    fully configured), except that `inner.refresh()` is only called while flat if
    the current bar's `regime_key` indicator value is a member of
    `allowed_regimes`. While `inner` already has an open position, `inner.refresh()`
    always runs regardless of regime -- a regime filter should change when a
    strategy is allowed to open a new position, not strand it in an existing one
    the moment the regime label changes mid-trade (which would also make backtest
    results depend on lookahead-free but still awkward "force-close on regime
    change" logic this class deliberately doesn't add).

    All real state (allocation, trade_history, current_trade) lives on `inner`,
    not on this wrapper -- the properties below simply forward reads through, so
    `Portfolio`/`Backtester` (which read `.allocation`, `.symbol`,
    `.allocation_change_time`, and `.trade_history` directly off whatever
    `WeightedStrategy.strategy` they were given) see exactly what `inner` would
    have reported on its own. Does not call `Strategy.__init__` -- that would set
    `self.allocation`/`self.trade_history` as plain instance attributes, which
    would shadow (and conflict with) the read-only properties below.
    """

    def __init__(
        self,
        key: str,
        market: Market,
        symbol: str,
        inner: Strategy,
        regime_key: str,
        allowed_regimes: set[str],
    ):
        self.key: str = key
        self.market: Market = market
        self.symbol: str = symbol
        self.inner: Strategy = inner
        self.regime_key: str = regime_key
        self.allowed_regimes: set[str] = allowed_regimes

    # ------------------------------------------------------------------
    # State: forwarded from `inner`, read-only
    # ------------------------------------------------------------------

    @property
    def allocation(self):
        return self.inner.allocation

    @property
    def trade_history(self):
        return self.inner.trade_history

    @property
    def current_trade(self):
        return self.inner.current_trade

    @property
    def allocation_change_time(self):
        return self.inner.allocation_change_time

    # ------------------------------------------------------------------
    # Strategy interface
    # ------------------------------------------------------------------

    def refresh(self) -> None:
        if self.inner.allocation.positions:
            self.inner.refresh()
            return

        regime = self.market.current[self.symbol]["indicators"].get(self.regime_key)
        if regime in self.allowed_regimes:
            self.inner.refresh()
