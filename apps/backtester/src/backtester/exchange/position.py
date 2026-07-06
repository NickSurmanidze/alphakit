"""Position tracking and management."""

from __future__ import annotations

import copy
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

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
    def __init__(self, exchange: Exchange) -> None:
        self.exchange = exchange
        self.open_positions: dict[str, Position] = {}
        self.closed_positions: dict[str, Position] = {}

    def get_positions(self) -> list[Position]:
        return list(self.open_positions.values()) + list(self.closed_positions.values())

    def get_open_positions(self) -> dict[str, Position]:
        return self.open_positions

    def get_open_position_by_symbol(self, symbol: str) -> Position | None:
        return self.open_positions.get(symbol)

    def add_position(
        self,
        side: PositionSide,
        price: float,
        symbol: str,
        volume: float,
        reduce_only: bool = False,
    ):
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
        asset, quote = symbol.split("/")
        value_in_usd = self.exchange.convert_asset_volume(volume, from_asset=asset, to_asset="USD")
        cost_in_usd = price * volume

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

        return position

    def increase_position_volume(self, position: Position, price: float, volume: float) -> Position:
        asset, quote = position.symbol.split("/")
        cost_in_usd = price * volume

        new_margin_used = position.margin_used_in_usd + cost_in_usd / self.exchange.max_leverage
        self.exchange.balance.unlock_balance(asset=quote, volume=position.margin_used_in_usd)
        self.exchange.balance.lock_balance(asset=quote, volume=new_margin_used)

        position.margin_used_in_usd = new_margin_used
        position.average_entry_price = (
            position.average_entry_price * position.volume + price * volume
        ) / (position.volume + volume)
        position.volume = position.volume + volume
        position.value_in_usd = self.exchange.convert_asset_volume(
            volume=position.volume, from_asset=asset, to_asset="USD"
        )
        position.collateral_used_in_usd = position.collateral_used_in_usd + cost_in_usd

        self.open_positions[position.symbol] = position
        return position

    def reduce_position_volume(self, position: Position, price: float, volume: float):
        should_close = position.volume <= volume

        if should_close:
            volume = position.volume

        asset, quote = position.symbol.split("/")

        old_volume = position.volume
        new_volume = position.volume - volume

        old_margin_used = position.margin_used_in_usd
        new_margin_used = position.margin_used_in_usd * (new_volume / old_volume)

        self.exchange.balance.unlock_balance(asset=quote, volume=old_margin_used)
        if new_margin_used > 0:
            self.exchange.balance.lock_balance(asset=quote, volume=new_margin_used)

        position.margin_used_in_usd = new_margin_used
        position.volume = new_volume
        position.value_in_usd = self.exchange.convert_asset_volume(
            volume, from_asset=asset, to_asset="USD"
        )
        position.collateral_used_in_usd = position.collateral_used_in_usd * (
            new_volume / old_volume
        )

        pnl_in_usd: float = 0
        if position.side == PositionSide.long:
            pnl_in_usd = volume * price - volume * position.average_entry_price
        else:
            pnl_in_usd = volume * position.average_entry_price - volume * price

        if pnl_in_usd < 0:
            self.exchange.balance.reduce_asset_balance(asset=quote, volume=abs(pnl_in_usd))
        else:
            self.exchange.balance.increase_asset_balance(asset=quote, volume=pnl_in_usd)

        if should_close:
            position.status = PositionStatus.closed
            position.close_time = self.exchange.market.current["time_close"].to_pydatetime()
            self.closed_positions[position.id] = copy.deepcopy(position)
            del self.open_positions[position.symbol]
        else:
            self.open_positions[position.symbol] = position

        return True

    def calculate_required_margin(
        self, side: PositionSide, symbol: str, volume: float, price: float
    ) -> float:
        added_volume = volume
        position = self.get_open_position_by_symbol(symbol=symbol)
        if position is not None:
            if position.side != side:
                if position.volume > volume:
                    added_volume = 0
                else:
                    added_volume = volume - position.volume

        return added_volume * price / self.exchange.max_leverage if added_volume else 0

    def liquidate_position(self, position: Position):
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
        print("****** Liquidation ********")

    def get_total_unrealized_pnl(self) -> float:
        return sum(p.pnl_in_usd for p in self.open_positions.values())

    def liquidate_all_positions(self):
        for position in copy.deepcopy(self.open_positions).values():
            self.liquidate_position(position=position)

    def refresh_position(self, position: Position):
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

        self.open_positions[position.symbol] = position

        if self.exchange.margin_allocation_type == MarginAllocationType.isolated:
            if (
                position.pnl_in_usd < 0
                and (position.margin_used_in_usd - abs(position.pnl_in_usd)) <= 0
            ):
                self.liquidate_position(position=position)

    def refresh_open_positions(self):
        for position in copy.deepcopy(self.open_positions).values():
            self.refresh_position(position=position)

        if self.exchange.margin_allocation_type == MarginAllocationType.cross:
            total_unrealized_pnl = self.get_total_unrealized_pnl()
            total_balance = self.exchange.balance.get_total_balance_in_usd()
            if total_unrealized_pnl < 0 and (total_balance - abs(total_unrealized_pnl)) <= 0:
                self.liquidate_all_positions()

    def close_position(self, position: Position):
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
        for position in self.open_positions.values():
            self.close_position(position=position)
