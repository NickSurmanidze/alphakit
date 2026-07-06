"""Order creation, validation, and execution."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from backtester.exchange.types import (
    MarketType,
    OrderExecutionType,
    OrderSide,
    OrderStatus,
    PositionSide,
)

if TYPE_CHECKING:
    from backtester.exchange.core import Exchange


# price is used as both fill price and trigger price for stoplossLimit orders
class Order:
    def __init__(  # noqa: PLR0913
        self,
        id: str,
        side: OrderSide,
        execution_type: OrderExecutionType,
        symbol: str,
        status: OrderStatus,
        volume: float,
        open_time: datetime,
        price: float | None = None,
        close_time: datetime | None = None,
        is_reduce_only: bool = False,
        reason: str = "",
    ):
        self.id: str = id
        self.side: OrderSide = side
        self.execution_type: OrderExecutionType = execution_type
        self.symbol: str = symbol
        self.status: OrderStatus = status
        self.volume = volume
        self.price = price
        self.open_time = open_time
        self.close_time = close_time
        self.is_reduce_only = is_reduce_only

        # for OCO only one order locks balance; this flag marks which one
        self.has_locked_balance: bool = False
        self.is_oco: bool = False
        self.oco_reference: str | None = None

        self.error_message: str = ""
        self.reason: str = reason

        self.sell_asset: str | None = None
        self.sell_volume: float = 0.0
        self.buy_asset: str | None = None
        self.buy_volume: float = 0.0
        self.fees_asset: str | None = None
        self.fees_volume: float = 0.0
        self.price_with_slippage: float = 0.0


class Orders:
    def __init__(self, exchange: Exchange) -> None:
        self.exchange = exchange
        self.orders: dict[str, Order] = {}

    def get_orders(self, status: OrderStatus | None = None) -> list[Order]:
        if status:
            return [o for o in self.orders.values() if o.status == status]
        return list(self.orders.values())

    def get_order_by_id(self, id: str) -> Order:
        if id not in self.orders:
            raise ValueError(f"Order {id} not found!")
        return self.orders[id]

    def create_oco_order(  # noqa: PLR0913
        self,
        symbol: str,
        volume: float,
        stop_loss_price: float,
        take_profit_price: float,
        is_reduce_only: bool = False,
        reason: str = "",
    ):
        sl_order: Order = Order(
            id=str(uuid.uuid4()),
            side=OrderSide.sell,
            execution_type=OrderExecutionType.stoplossLimit,
            symbol=symbol,
            status=OrderStatus.open,
            volume=volume,
            price=stop_loss_price,
            open_time=self.exchange.market.current["time_close"].to_pydatetime(),
            close_time=None,
            is_reduce_only=is_reduce_only,
            reason=reason,
        )

        tp_order: Order = Order(
            id=str(uuid.uuid4()),
            side=OrderSide.sell,
            execution_type=OrderExecutionType.limit,
            symbol=symbol,
            status=OrderStatus.open,
            volume=volume,
            price=take_profit_price,
            open_time=self.exchange.market.current["time_close"].to_pydatetime(),
            close_time=None,
            is_reduce_only=is_reduce_only,
            reason=reason,
        )

        sl_order.is_oco = True
        tp_order.is_oco = True
        sl_order.has_locked_balance = True
        tp_order.has_locked_balance = False
        sl_order.oco_reference = tp_order.id
        tp_order.oco_reference = sl_order.id

        sl_order = self.add_order_processing_logic(order=sl_order)
        tp_order = self.add_order_processing_logic(order=tp_order)

        self.validate_order_before_creating(order=sl_order)
        self.validate_order_before_creating(order=tp_order)

        if sl_order.id in self.orders.keys():
            raise ValueError("Failed to create order. ID is already in use.")
        self.orders[sl_order.id] = sl_order

        if tp_order.id in self.orders.keys():
            raise ValueError("Failed to create order. ID is already in use.")
        self.orders[tp_order.id] = tp_order

        self.lock_balance(order=sl_order)

        return sl_order, tp_order

    def create_order(  # noqa: PLR0913
        self,
        symbol: str,
        side: OrderSide,
        execution_type: OrderExecutionType,
        volume: float,
        price: float | None = None,
        is_reduce_only: bool = False,
        reason: str = "",
    ):
        order: Order = Order(
            id=str(uuid.uuid4()),
            side=side,
            execution_type=execution_type,
            symbol=symbol,
            status=OrderStatus.open,
            volume=volume,
            price=price,
            open_time=self.exchange.market.current["time_close"].to_pydatetime(),
            close_time=None,
            is_reduce_only=is_reduce_only,
            reason=reason,
        )

        if order.execution_type == OrderExecutionType.market:
            order.price = self.exchange.get_market_price(symbol=order.symbol)

        order = self.add_order_processing_logic(order=order)
        self.validate_order_before_creating(order=order)

        if order.id in self.orders.keys():
            raise ValueError("Failed to create order. ID is already in use.")

        order.has_locked_balance = True
        self.lock_balance(order=order)
        self.orders[order.id] = order

        if order.execution_type == OrderExecutionType.market:
            order = self.refresh_order(order=order)

        return order

    def update_order(self, order: Order) -> Order:
        self.orders[order.id] = order
        return order

    def lock_balance(self, order: Order) -> None:
        if self.exchange.market_type == MarketType.spot and order.sell_asset is not None:
            self.exchange.balance.lock_balance(asset=order.sell_asset, volume=order.sell_volume)

    def unlock_balance(self, order: Order) -> None:
        if (
            self.exchange.market_type == MarketType.spot
            and order.has_locked_balance
            and order.sell_asset is not None
        ):
            self.exchange.balance.unlock_balance(
                asset=order.sell_asset, volume=order.sell_volume
            )

    def cancel_order(self, order: Order) -> Order:
        if order.is_oco and order.oco_reference is not None:
            oco_order = self.get_order_by_id(id=order.oco_reference)
            if oco_order.status == OrderStatus.open:
                oco_order.status = OrderStatus.canceled
                self.unlock_balance(order=oco_order)
                oco_order = self.update_order(order=oco_order)

        if order.status == OrderStatus.open:
            order.status = OrderStatus.canceled
            order = self.update_order(order=order)
            self.unlock_balance(order=order)

        return order

    def add_order_processing_logic(self, order: Order) -> Order:
        asset, quote = order.symbol.split("/")

        if order.execution_type == OrderExecutionType.market:
            fee = self.exchange.taker_fee
            slippage = self.exchange.slippage
        else:
            fee = self.exchange.maker_fee
            slippage = 0.0

        price_with_slippage = order.price or 0.0

        if order.price is None:
            raise ValueError("Price cannot be None at this stage")

        if order.side == OrderSide.buy:
            order.price_with_slippage = order.price + order.price * slippage
        else:
            order.price_with_slippage = order.price - order.price * slippage

        if order.side == OrderSide.buy:
            reduce_asset = quote
            reduce_volume = order.volume * price_with_slippage
            increase_asset = asset
            increase_volume = order.volume
        else:
            increase_asset = quote
            increase_volume = order.volume * price_with_slippage
            reduce_asset = asset
            reduce_volume = order.volume

        order.sell_asset = reduce_asset
        order.sell_volume = reduce_volume
        order.buy_asset = increase_asset
        order.buy_volume = increase_volume
        order.fees_asset = quote

        if order.side == OrderSide.buy:
            order.fees_volume = reduce_volume * fee
            order.sell_volume = order.sell_volume + order.fees_volume
        else:
            order.fees_volume = increase_volume * fee
            order.buy_volume = order.buy_volume - order.fees_volume

        return order

    def validate_order_before_creating(self, order: Order):  # noqa: PLR0912
        if order.symbol not in self.exchange.market.current:
            raise ValueError(
                f"Symbol {order.symbol} is not currently available. Cannot create an order!"
            )

        if order.execution_type in {
            OrderExecutionType.limit,
            OrderExecutionType.stoplossLimit,
        }:
            if order.price is None:
                raise ValueError("Limit order needs to have price")

            market_price = self.exchange.get_market_price(symbol=order.symbol)
            if order.execution_type == OrderExecutionType.limit:
                if order.side == OrderSide.sell and order.price <= market_price:
                    raise ValueError(
                        "Limit sell order price should be more than current market price"
                    )
                if order.side == OrderSide.buy and order.price >= market_price:
                    raise ValueError(
                        "Limit buy order price should be less than current market price"
                    )

            if order.execution_type == OrderExecutionType.stoplossLimit:
                if order.side == OrderSide.sell and order.price >= market_price:
                    raise ValueError(
                        "StoplossLimit sell order price should be less than current market price"
                    )
                if order.side == OrderSide.buy and order.price <= market_price:
                    raise ValueError(
                        "StoplossLimit buy order price should be more than current market price"
                    )

        if self.exchange.market_type == MarketType.spot:
            balance = self.exchange.balance.get_balance()
            if order.sell_asset not in balance:
                raise ValueError(f"Balance does not contain {order.sell_asset}")
            elif balance[order.sell_asset]["free"]["volume"] < order.sell_volume:
                avail = balance[order.sell_asset]["free"]["volume"]
                raise ValueError(
                    f"Not enough {order.sell_asset}: need {order.sell_volume}, have {avail}"
                )

        if self.exchange.market_type == MarketType.future:
            if order.is_reduce_only:
                existing_position = self.exchange.positions.get_open_position_by_symbol(
                    symbol=order.symbol
                )
                required_side = (
                    PositionSide.long
                    if order.side == OrderSide.sell
                    else PositionSide.short
                )
                if existing_position is None:
                    raise ValueError(
                        "Cannot create a reduce_only order when position does not exist!"
                    )
                elif existing_position.side != required_side:
                    raise ValueError(
                        f"reduce_only side mismatch: "
                        f"got {existing_position.side}, expected {required_side}"
                    )
                elif existing_position.volume < order.volume:
                    raise ValueError(
                        "reduce_only order volume exceeds existing position volume"
                    )

            required_margin = self.exchange.positions.calculate_required_margin(
                symbol=order.symbol,
                price=order.price_with_slippage,
                volume=order.volume,
                side=PositionSide.long,
            )
            total_required_balance = required_margin + order.fees_volume
            balance = self.exchange.balance.get_balance()

            if required_margin > 0:
                if order.fees_asset not in balance:
                    raise ValueError(f"Balance does not contain {order.fees_asset}")
                elif balance[order.fees_asset]["free"]["volume"] < total_required_balance:
                    avail = balance[order.fees_asset]["free"]["volume"]
                    raise ValueError(
                        f"Not enough {order.fees_asset}: "
                        f"need {total_required_balance}, have {avail}"
                    )

    def refresh_order(self, order: Order) -> Order:  # noqa: PLR0912, PLR0915
        if order.status == OrderStatus.open:
            should_execute = False

            if order.execution_type == OrderExecutionType.market:
                should_execute = True

            if order.execution_type == OrderExecutionType.limit:
                ohlc = self.exchange.get_market_candle(symbol=order.symbol)
                if order.side == OrderSide.buy and ohlc["low"] < order.price:
                    should_execute = True
                if order.side == OrderSide.sell and ohlc["high"] > order.price:
                    should_execute = True

            if order.execution_type == OrderExecutionType.stoplossLimit:
                ohlc = self.exchange.get_market_candle(symbol=order.symbol)
                if order.side == OrderSide.buy and ohlc["high"] > order.price:
                    should_execute = True
                if order.side == OrderSide.sell and ohlc["low"] < order.price:
                    should_execute = True

            if should_execute:
                self.unlock_balance(order=order)

                if self.exchange.market_type == MarketType.spot:
                    assert order.sell_asset is not None and order.buy_asset is not None
                    self.exchange.balance.reduce_asset_balance(
                        asset=order.sell_asset, volume=order.sell_volume
                    )
                    self.exchange.balance.increase_asset_balance(
                        asset=order.buy_asset, volume=order.buy_volume
                    )

                if self.exchange.market_type == MarketType.future:
                    if order.is_reduce_only:
                        existing_position = self.exchange.positions.get_open_position_by_symbol(
                            symbol=order.symbol
                        )
                        required_side = (
                            PositionSide.long
                            if order.side == OrderSide.sell
                            else PositionSide.short
                        )
                        if existing_position is None:
                            raise ValueError(
                                "Cannot create a reduce_only order when position does not exist!"
                            )
                        elif existing_position.side != required_side:
                            raise ValueError(
                                f"reduce_only side mismatch: "
                                f"got {existing_position.side}, expected {required_side}"
                            )
                        elif existing_position.volume < order.volume:
                            raise ValueError(
                                "reduce_only order volume exceeds existing position volume"
                            )

                    required_margin = self.exchange.positions.calculate_required_margin(
                        symbol=order.symbol,
                        price=order.price_with_slippage,
                        volume=order.volume,
                        side=PositionSide.long,
                    )
                    total_required_balance = required_margin + order.fees_volume
                    balance = self.exchange.balance.get_balance()

                    if required_margin > 0:
                        if order.fees_asset not in balance:
                            raise ValueError(f"Balance does not contain {order.fees_asset}")
                        elif balance[order.fees_asset]["free"]["volume"] < total_required_balance:
                            avail = balance[order.fees_asset]["free"]["volume"]
                            raise ValueError(
                                f"Not enough {order.fees_asset}: "
                                f"need {total_required_balance}, have {avail}"
                            )
                        self.exchange.balance.reduce_asset_balance(
                            asset=order.fees_asset, volume=order.fees_volume
                        )

                    if order.side == OrderSide.buy:
                        self.exchange.positions.add_position(
                            symbol=order.symbol,
                            volume=order.volume,
                            price=order.price_with_slippage,
                            side=PositionSide.long,
                            reduce_only=order.is_reduce_only,
                        )
                    else:
                        self.exchange.positions.add_position(
                            symbol=order.symbol,
                            volume=order.volume,
                            price=order.price_with_slippage,
                            side=PositionSide.short,
                            reduce_only=order.is_reduce_only,
                        )

                    if required_margin == 0:
                        if order.fees_asset not in balance:
                            raise ValueError(f"Balance does not contain {order.fees_asset}")
                        elif balance[order.fees_asset]["free"]["volume"] < total_required_balance:
                            avail = balance[order.fees_asset]["free"]["volume"]
                            raise ValueError(
                                f"Not enough {order.fees_asset}: "
                                f"need {total_required_balance}, have {avail}"
                            )
                        self.exchange.balance.reduce_asset_balance(
                            asset=order.fees_asset, volume=order.fees_volume
                        )

                order.status = OrderStatus.closed
                order.close_time = self.exchange.market.current["time_close"].to_pydatetime()
                order = self.update_order(order=order)

                if order.is_oco and order.oco_reference is not None:
                    oco_order = self.get_order_by_id(id=order.oco_reference)
                    self.cancel_order(order=oco_order)

        return order

    def refresh_open_orders(self) -> None:
        # process stoploss first to avoid optimistic results
        open_orders = self.get_orders(status=OrderStatus.open)
        for order in open_orders:
            if order.execution_type == OrderExecutionType.stoplossLimit:
                self.refresh_order(order=order)
        for order in open_orders:
            if order.execution_type == OrderExecutionType.limit:
                self.refresh_order(order=order)

    def cancel_open_orders(self) -> None:
        for order in self.get_orders(status=OrderStatus.open):
            self.cancel_order(order=order)
