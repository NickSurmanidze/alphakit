"""SymbolConfigProvider: lets Exchange/Orders swap the crypto percentage-of-price
fee/slippage model for an absolute, per-contract one (Tradovate/CME futures) without
touching the existing percentage code path.

A Protocol (not ABC) to match middleware.py's established convention in this codebase
of not forcing an inheritance relationship where a structural one suffices.
"""

from __future__ import annotations

from typing import Protocol

from backtester.exchange_config import TRADOVATE_FUTURES, SymbolConfig


class SymbolConfigProvider(Protocol):
    """Per-symbol contract config + fee/slippage calculation, keyed by bare asset
    symbol (e.g. "ES", not "ES/USD") -- callers split the quote currency off first."""

    def get_point_value(self, asset: str) -> float:
        """Dollars a single 1.0 price-point move is worth for one contract of `asset`."""
        ...

    def get_tick_size(self, asset: str) -> float | None:
        """Minimum price increment for `asset`, or None if not yet researched."""
        ...

    def get_min_position_size(self, asset: str) -> float:
        """Smallest tradeable position size (in contracts) for `asset`."""
        ...

    def get_min_order_step(self, asset: str) -> float:
        """Smallest position-size increment (in contracts) for `asset`."""
        ...

    def get_max_position_size(self, asset: str) -> float | None:
        """Hard ceiling on contracts per position for `asset`, or None if uncapped."""
        ...

    def get_fee(self, asset: str, position_size: float) -> tuple[float, str]:
        """Total commission for a `position_size`-contract fill of `asset`, as
        (amount, currency) -- e.g. (1.0, "USD")."""
        ...

    def get_slippage(self, asset: str, position_size: float) -> tuple[float, str]:
        """Expected fill slippage for a `position_size`-contract fill of `asset`, as an
        ABSOLUTE PRICE-UNIT OFFSET (not a dollar amount, not a percentage) -- e.g.
        (0.25, "points") meaning the fill price moves 0.25 points against the order.
        The second element is a unit label kept for API symmetry with get_fee, not a
        currency the amount is denominated in."""
        ...


class TradovateSymbolConfigProvider:
    """Concrete SymbolConfigProvider backed by TRADOVATE_FUTURES: a general per-contract
    commission every symbol uses unless its SymbolConfig sets fee_per_contract_override,
    and tick-based (not percentage-based) slippage."""

    def __init__(
        self,
        symbols: dict[str, SymbolConfig] | None = None,
        default_fee_per_contract: float = 0.0,
    ) -> None:
        """`default_fee_per_contract` is a placeholder until Tradovate's real commission
        schedule is sourced -- see PROP_FIRM_PLAN.md."""
        self._symbols = symbols if symbols is not None else TRADOVATE_FUTURES
        self.default_fee_per_contract = default_fee_per_contract

    def _lookup(self, asset: str) -> SymbolConfig:
        if asset not in self._symbols:
            raise ValueError(f"No Tradovate symbol config registered for {asset!r}")
        return self._symbols[asset]

    def get_point_value(self, asset: str) -> float:
        return self._lookup(asset).point_value

    def get_tick_size(self, asset: str) -> float | None:
        return self._lookup(asset).tick_size

    def get_min_position_size(self, asset: str) -> float:
        return self._lookup(asset).min_position_size

    def get_min_order_step(self, asset: str) -> float:
        return self._lookup(asset).min_order_step

    def get_max_position_size(self, asset: str) -> float | None:
        return self._lookup(asset).max_position_size

    def get_fee(self, asset: str, position_size: float) -> tuple[float, str]:
        cfg = self._lookup(asset)
        fee_per_contract = cfg.fee_per_contract_override
        if fee_per_contract is None:
            fee_per_contract = self.default_fee_per_contract
        return fee_per_contract * position_size, "USD"

    def get_slippage(self, asset: str, position_size: float) -> tuple[float, str]:  # noqa: ARG002
        cfg = self._lookup(asset)
        if cfg.slippage_ticks == 0:
            return 0.0, "points"
        if cfg.tick_size is None:
            raise ValueError(
                f"tick_size not yet researched for {asset!r} -- see PROP_FIRM_PLAN.md. "
                "Set slippage_ticks=0 on its SymbolConfig as a temporary workaround."
            )
        return cfg.slippage_ticks * cfg.tick_size, "points"
