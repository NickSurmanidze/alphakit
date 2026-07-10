"""Position tracking and management."""

from __future__ import annotations

import copy
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from backtester.exchange.event_log import (
    PositionClosed,
    PositionIncreased,
    PositionLiquidated,
    PositionOpened,
    PositionReduced,
)
from backtester.exchange.types import (
    MarginAllocationType,
    OrderExecutionType,
    OrderSide,
    PositionSide,
    PositionStatus,
)

if TYPE_CHECKING:
    from backtester.exchange.core import Exchange


class Position:
    """One open or closed futures/margin position's full state, including its running
    (or, once closed, final realized) PnL and liquidation status."""

    def __init__(  # noqa: PLR0913
        self,
        id: str,
        side: PositionSide,
        symbol: str,
        status: PositionStatus,
        volume: float,
        value_in_usd: float,
        collateral_used_in_usd: float,
        open_time: datetime,
        average_entry_price: float,
        margin_used_in_usd: float,
        close_time: datetime | None = None,
    ):
        """Sets identity/sizing fields as given; pnl_in_usd/liquidation_price/liquidated
        all start at their "nothing has happened yet" defaults."""
        self.id: str = id
        self.side: PositionSide = side
        self.symbol: str = symbol
        self.status: PositionStatus = status
        self.volume: float = volume
        self.average_entry_price: float = average_entry_price
        self.value_in_usd: float = value_in_usd
        self.open_time: datetime = open_time
        self.close_time = close_time

        self.collateral_used_in_usd: float = collateral_used_in_usd
        self.margin_used_in_usd: float = margin_used_in_usd
        self.pnl_in_usd: float = 0
        self.liquidation_price: float = 0
        self.liquidated: bool = False


class Positions:
    """Opens, increases, reduces, closes, and liquidates futures/margin positions for
    one Exchange, keeping open and closed positions in separate dicts."""

    def __init__(self, exchange: Exchange) -> None:
        """Starts with no positions."""
        self.exchange = exchange
        self.open_positions: dict[str, Position] = {}
        self.closed_positions: dict[str, Position] = {}

    def get_positions(self) -> list[Position]:
        """Returns every position, open and closed."""
        return list(self.open_positions.values()) + list(self.closed_positions.values())

    def get_open_positions(self) -> dict[str, Position]:
        """Returns the open-positions dict, keyed by symbol."""
        return self.open_positions

    def get_open_position_by_symbol(self, symbol: str) -> Position | None:
        """Returns the open position for `symbol`, or None if flat."""
        return self.open_positions.get(symbol)

    def add_position(
        self,
        side: PositionSide,
        price: float,
        symbol: str,
        volume: float,
        reduce_only: bool = False,
    ):
        """Routes a fill to the right position operation: opens a new position if flat,
        increases if adding to the same side, or reduces/flips if the fill is on the
        opposite side of an existing position (closing it first, then opening the
        remainder in the new direction if the fill volume exceeds the existing
        position's)."""
        existing_position = self.get_open_position_by_symbol(symbol=symbol)

        if existing_position is None:
            self.create_position(side=side, price=price, symbol=symbol, volume=volume)
        elif existing_position.side == side:
            self.increase_position_volume(position=existing_position, price=price, volume=volume)
        elif existing_position.volume > volume:
            self.reduce_position_volume(position=existing_position, price=price, volume=volume)
        else:
            remaining_volume = volume - existing_position.volume
            self.reduce_position_volume(
                position=existing_position, price=price, volume=existing_position.volume
            )
            if remaining_volume > 0:
                self.create_position(side=side, price=price, symbol=symbol, volume=remaining_volume)

        return True

    def create_position(
        self, side: PositionSide, price: float, symbol: str, volume: float
    ) -> Position:
        """Opens a brand-new position at `price`/`volume`, locks its required margin,
        and records a PositionOpened event."""
        asset, quote = symbol.split("/")
        point_value = self.exchange.get_point_value(symbol)
        value_in_usd = (
            self.exchange.convert_asset_volume(volume, from_asset=asset, to_asset="USD")
            * point_value
        )
        cost_in_usd = price * volume * point_value

        position = Position(
            id=str(uuid.uuid4()),
            side=side,
            symbol=symbol,
            status=PositionStatus.open,
            volume=volume,
            open_time=self.exchange.market.current["time_close"].to_pydatetime(),
            average_entry_price=price,
            margin_used_in_usd=cost_in_usd / self.exchange.max_leverage,
            collateral_used_in_usd=cost_in_usd,
            value_in_usd=value_in_usd,
        )

        self.open_positions[position.symbol] = position
        self.exchange.balance.lock_balance(asset=quote, volume=position.margin_used_in_usd)

        self.exchange.event_log.emit(
            PositionOpened(
                time=position.open_time,
                position_id=position.id,
                symbol=position.symbol,
                side=position.side,
                volume=position.volume,
                price=position.average_entry_price,
                margin_used_in_usd=position.margin_used_in_usd,
            )
        )

        return position

    def increase_position_volume(self, position: Position, price: float, volume: float) -> Position:
        """Adds `volume` more to an existing same-side position at `price`, recomputing
        its weighted-average entry price and margin, and records a PositionIncreased
        event."""
        asset, quote = position.symbol.split("/")
        point_value = self.exchange.get_point_value(position.symbol)
        cost_in_usd = price * volume * point_value

        new_margin_used = position.margin_used_in_usd + cost_in_usd / self.exchange.max_leverage
        self.exchange.balance.unlock_balance(asset=quote, volume=position.margin_used_in_usd)
        self.exchange.balance.lock_balance(asset=quote, volume=new_margin_used)

        position.margin_used_in_usd = new_margin_used
        position.average_entry_price = (
            position.average_entry_price * position.volume + price * volume
        ) / (position.volume + volume)
        position.volume = position.volume + volume
        position.value_in_usd = (
            self.exchange.convert_asset_volume(
                volume=position.volume, from_asset=asset, to_asset="USD"
            )
            * point_value
        )
        position.collateral_used_in_usd = position.collateral_used_in_usd + cost_in_usd

        self.open_positions[position.symbol] = position

        self.exchange.event_log.emit(
            PositionIncreased(
                time=self.exchange.market.current["time_close"].to_pydatetime(),
                position_id=position.id,
                symbol=position.symbol,
                side=position.side,
                added_volume=volume,
                price=price,
                new_volume=position.volume,
                new_average_entry_price=position.average_entry_price,
            )
        )

        return position

    def reduce_position_volume(self, position: Position, price: float, volume: float):
        """Reduces `position` by `volume` at `price` (or fully closes it if `volume`
        covers the whole position), realizing PnL to the cash balance and unlocking a
        proportional share of margin. Records a PositionClosed or PositionReduced event
        depending on whether the position ended up closed."""
        should_close = position.volume <= volume

        if should_close:
            volume = position.volume

        asset, quote = position.symbol.split("/")
        point_value = self.exchange.get_point_value(position.symbol)

        old_volume = position.volume
        new_volume = position.volume - volume

        old_margin_used = position.margin_used_in_usd
        new_margin_used = position.margin_used_in_usd * (new_volume / old_volume)

        self.exchange.balance.unlock_balance(asset=quote, volume=old_margin_used)
        if new_margin_used > 0:
            self.exchange.balance.lock_balance(asset=quote, volume=new_margin_used)

        position.margin_used_in_usd = new_margin_used
        position.volume = new_volume
        position.value_in_usd = (
            self.exchange.convert_asset_volume(volume, from_asset=asset, to_asset="USD")
            * point_value
        )
        position.collateral_used_in_usd = position.collateral_used_in_usd * (
            new_volume / old_volume
        )

        pnl_in_usd: float = 0
        if position.side == PositionSide.long:
            pnl_in_usd = point_value * (volume * price - volume * position.average_entry_price)
        else:
            pnl_in_usd = point_value * (volume * position.average_entry_price - volume * price)

        if pnl_in_usd < 0:
            self.exchange.balance.reduce_asset_balance(asset=quote, volume=abs(pnl_in_usd))
        else:
            self.exchange.balance.increase_asset_balance(asset=quote, volume=pnl_in_usd)

        if should_close:
            position.status = PositionStatus.closed
            position.close_time = self.exchange.market.current["time_close"].to_pydatetime()
            # Finalize the realized PnL of this close onto the position before it's copied
            # into closed_positions -- without this it carries whatever pnl_in_usd was last
            # set by refresh_position() (unrealized, possibly stale), not this close's
            # actual realized amount.
            position.pnl_in_usd = pnl_in_usd
            self.closed_positions[position.id] = copy.deepcopy(position)
            del self.open_positions[position.symbol]
            self.exchange.event_log.emit(
                PositionClosed(
                    time=position.close_time,
                    position_id=position.id,
                    symbol=position.symbol,
                    side=position.side,
                    volume=volume,
                    price=price,
                    realized_pnl_in_usd=pnl_in_usd,
                )
            )
        else:
            self.open_positions[position.symbol] = position
            self.exchange.event_log.emit(
                PositionReduced(
                    time=self.exchange.market.current["time_close"].to_pydatetime(),
                    position_id=position.id,
                    symbol=position.symbol,
                    side=position.side,
                    reduced_volume=volume,
                    price=price,
                    remaining_volume=position.volume,
                    realized_pnl_in_usd=pnl_in_usd,
                )
            )

        return True

    def calculate_required_margin(
        self, side: PositionSide, symbol: str, volume: float, price: float
    ) -> float:
        """Estimates the margin a new fill of `volume`/`price` would require: zero (or
        reduced) if it would only reduce an existing opposite-side position, otherwise
        the full (or net-additional) notional divided by max leverage."""
        added_volume = volume
        position = self.get_open_position_by_symbol(symbol=symbol)
        if position is not None:
            if position.side != side:
                if position.volume > volume:
                    added_volume = 0
                else:
                    added_volume = volume - position.volume

        point_value = self.exchange.get_point_value(symbol)
        return (
            added_volume * price * point_value / self.exchange.max_leverage if added_volume else 0
        )

    def liquidate_position(self, position: Position):
        """Force-closes `position` for a total loss of its margin (not just the realized
        PnL loss up to that point): the margin is forfeited entirely, not just debited by
        the loss amount, then the position moves to closed_positions with liquidated=True
        and a PositionLiquidated event is recorded."""
        position.status = PositionStatus.closed
        position.liquidated = True
        position.close_time = self.exchange.market.current["time_close"].to_pydatetime()

        asset, quote = position.symbol.split("/")
        self.exchange.balance.unlock_balance(asset=quote, volume=abs(position.margin_used_in_usd))
        self.exchange.balance.reduce_asset_balance(
            asset=quote, volume=abs(position.margin_used_in_usd)
        )

        self.closed_positions[position.id] = copy.deepcopy(position)
        del self.open_positions[position.symbol]
        self.exchange.event_log.emit(
            PositionLiquidated(
                time=position.close_time,
                position_id=position.id,
                symbol=position.symbol,
                side=position.side,
                volume=position.volume,
                margin_used_in_usd=position.margin_used_in_usd,
            )
        )

    def get_total_unrealized_pnl(self) -> float:
        """Sums pnl_in_usd across every currently-open position."""
        return sum(p.pnl_in_usd for p in self.open_positions.values())

    def liquidate_all_positions(self):
        """Liquidates every open position (e.g. a cross-margin account-wide margin
        call)."""
        for position in copy.deepcopy(self.open_positions).values():
            self.liquidate_position(position=position)

    def refresh_position(self, position: Position):
        """Marks `position` to the current market price, recomputing its unrealized
        pnl_in_usd, and liquidates it if isolated margin has been exhausted (loss
        exceeds the position's own margin)."""
        point_value = self.exchange.get_point_value(position.symbol)

        if point_value == 1.0:
            # Crypto path, byte-for-byte unchanged: kept as its own branch (rather than
            # folded into the point_value-aware math below via a "* 1.0") so the crypto
            # path never depends on (a*b)/b round-tripping back to exactly `a`.
            asset, quote = position.symbol.split("/")
            position.value_in_usd = self.exchange.convert_asset_volume(
                volume=position.volume, from_asset=asset, to_asset="USD"
            )
            price = position.value_in_usd / position.volume

            if position.side == PositionSide.long:
                position.pnl_in_usd = (
                    position.volume * price - position.volume * position.average_entry_price
                )
            else:
                position.pnl_in_usd = (
                    position.volume * position.average_entry_price - position.volume * price
                )
        else:
            market_price = self.exchange.get_market_price(position.symbol)
            position.value_in_usd = position.volume * market_price * point_value

            if position.side == PositionSide.long:
                position.pnl_in_usd = (
                    position.volume * point_value * (market_price - position.average_entry_price)
                )
            else:
                position.pnl_in_usd = (
                    position.volume * point_value * (position.average_entry_price - market_price)
                )

        self.open_positions[position.symbol] = position

        if self.exchange.margin_allocation_type == MarginAllocationType.isolated:
            if (
                position.pnl_in_usd < 0
                and (position.margin_used_in_usd - abs(position.pnl_in_usd)) <= 0
            ):
                self.liquidate_position(position=position)

    def refresh_open_positions(self):
        """Marks every open position to market, then (for cross margin) liquidates
        everything at once if the account's total unrealized loss exceeds its total
        balance."""
        for position in copy.deepcopy(self.open_positions).values():
            self.refresh_position(position=position)

        if self.exchange.margin_allocation_type == MarginAllocationType.cross:
            total_unrealized_pnl = self.get_total_unrealized_pnl()
            total_balance = self.exchange.balance.get_total_balance_in_usd()
            if total_unrealized_pnl < 0 and (total_balance - abs(total_unrealized_pnl)) <= 0:
                self.liquidate_all_positions()

    def close_position(self, position: Position):
        """Closes `position` by placing an opposite-side reduce_only market order for
        its full volume (raises if the symbol isn't actually open)."""
        if position.symbol in self.open_positions:
            if position.side == PositionSide.long:
                self.exchange.orders.create_order(
                    side=OrderSide.sell,
                    volume=position.volume,
                    symbol=position.symbol,
                    execution_type=OrderExecutionType.market,
                    is_reduce_only=True,
                    reason="close position",
                )
            elif position.side == PositionSide.short:
                self.exchange.orders.create_order(
                    side=OrderSide.buy,
                    volume=position.volume,
                    symbol=position.symbol,
                    execution_type=OrderExecutionType.market,
                    is_reduce_only=True,
                    reason="close position",
                )
            else:
                raise ValueError("Position side seems incorrect!!")
        else:
            raise ValueError("Position does not seem to exist!!")

    def close_all_open_positions(self):
        """Closes every currently-open position."""
        # list(...) snapshots the values before iterating -- close_position() synchronously
        # fills a market order for futures, which deletes from open_positions mid-loop (via
        # reduce_position_volume). Iterating the live dict directly raises "dictionary
        # changed size during iteration" the moment 2+ positions are open. Matches the
        # snapshot-before-iterating convention liquidate_all_positions() already uses.
        for position in list(self.open_positions.values()):
            self.close_position(position=position)
