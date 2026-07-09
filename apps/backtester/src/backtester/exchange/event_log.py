"""Structured audit trail: one immutable event per state-changing order/position action.

Separate from (and additive to) `Exchange.logs`, which is a coarse, rebalance-only text
log an existing golden test hard-asserts an exact count against -- this never touches
that. `EventLog` exists so a full run can be reconstructed and cross-checked event by
event (e.g. every closed trade has a matching `PositionClosed`), not just inspected via
final-state snapshots.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from backtester.exchange.types import OrderExecutionType, OrderSide, PositionSide


@dataclass(frozen=True)
class OrderCreated:
    """Emitted when a new order is registered (before it's necessarily filled)."""

    time: datetime
    order_id: str
    symbol: str
    side: OrderSide
    execution_type: OrderExecutionType
    volume: float
    price: float | None


@dataclass(frozen=True)
class OrderFilled:
    """Emitted when an order executes -- `price` is the slippage-adjusted fill price."""

    time: datetime
    order_id: str
    symbol: str
    side: OrderSide
    execution_type: OrderExecutionType
    volume: float
    price: float  # fill price, i.e. price_with_slippage
    fees_asset: str | None
    fees_volume: float


@dataclass(frozen=True)
class OrderCanceled:
    """Emitted when an order is canceled, directly or as an OCO side effect."""

    time: datetime
    order_id: str
    symbol: str
    side: OrderSide
    execution_type: OrderExecutionType


@dataclass(frozen=True)
class PositionOpened:
    """Emitted when a brand-new position is created."""

    time: datetime
    position_id: str
    symbol: str
    side: PositionSide
    volume: float
    price: float
    margin_used_in_usd: float


@dataclass(frozen=True)
class PositionIncreased:
    """Emitted when more volume is added to an existing same-side position."""

    time: datetime
    position_id: str
    symbol: str
    side: PositionSide
    added_volume: float
    price: float
    new_volume: float
    new_average_entry_price: float


@dataclass(frozen=True)
class PositionReduced:
    """Emitted when a position is partially reduced (some volume remains open)."""

    time: datetime
    position_id: str
    symbol: str
    side: PositionSide
    reduced_volume: float
    price: float
    remaining_volume: float
    realized_pnl_in_usd: float


@dataclass(frozen=True)
class PositionClosed:
    """Emitted when a position is fully closed (normally, not via liquidation)."""

    time: datetime
    position_id: str
    symbol: str
    side: PositionSide
    volume: float
    price: float
    realized_pnl_in_usd: float


@dataclass(frozen=True)
class PositionLiquidated:
    """Emitted when a position is force-closed for exhausting its margin."""

    time: datetime
    position_id: str
    symbol: str
    side: PositionSide
    volume: float
    margin_used_in_usd: float


EventUnion = (
    OrderCreated
    | OrderFilled
    | OrderCanceled
    | PositionOpened
    | PositionIncreased
    | PositionReduced
    | PositionClosed
    | PositionLiquidated
)


class EventLog:
    """Owned by `Exchange`, populated from `Orders`/`Positions` at each state-changing
    call site. `enabled=False` makes `emit()` a no-op -- for a large multi-year run where
    the append overhead isn't worth paying if nothing consumes the trail."""

    def __init__(self, enabled: bool = True) -> None:
        """Starts with an empty event list."""
        self.enabled = enabled
        self.events: list[EventUnion] = []

    def emit(self, event: EventUnion) -> None:
        """Appends `event` to the log (no-op if disabled)."""
        if self.enabled:
            self.events.append(event)

    def get_events(self, event_type: type | None = None) -> list[EventUnion]:
        """Returns every recorded event, optionally filtered to one event type."""
        if event_type is None:
            return self.events
        return [e for e in self.events if isinstance(e, event_type)]
