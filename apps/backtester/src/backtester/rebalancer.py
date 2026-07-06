import copy

from backtester.exchange import (
    Exchange,
    Order,
    OrderExecutionType,
    OrderSide,
    PositionSide,
)
from backtester.market import Market
from backtester.portfolio import Portfolio


class Rebalancer:
    def __init__(self, market: Market, exchange: Exchange, portfolio: Portfolio):
        self.rebalancing_plan: list[Order] = []

        self.market: Market = market
        self.exchange: Exchange = exchange
        self.portfolio: Portfolio = portfolio
        # tracks the last-seen allocation hash to detect changes
        self.signal_allocation_change_time_hash = (
            self.portfolio.signal_allocation_change_time_hash
        )

    def get_allocation(self):
        return self.portfolio.merged_allocation

    def refresh(self):
        if len(self.portfolio.weighted_strategies) == 0:
            return

        if (
            self.portfolio.signal_allocation_change_time_hash
            != self.signal_allocation_change_time_hash
        ):
            self.rebalance()
            self.signal_allocation_change_time_hash = (
                self.portfolio.signal_allocation_change_time_hash
            )

    def rebalance(self):
        self.exchange.orders.cancel_open_orders()

        total_balance = self.exchange.get_asset_total_in_usd()
        allocation = self.get_allocation()
        position_dict = dict()

        for position in allocation.positions:
            price = self.exchange.get_market_price(symbol=position.symbol)
            position.volume = (total_balance * position.percent) / price
            position_dict[position.symbol] = position

        for order in allocation.orders:
            price = self.exchange.get_market_price(symbol=order.symbol)
            order.volume = (total_balance * order.percent) / price

        current_open_positions = copy.deepcopy(self.exchange.positions.open_positions)

        for open_position in current_open_positions.values():
            if open_position.symbol not in position_dict:
                self.exchange.positions.close_position(position=open_position)

        current_open_positions = copy.deepcopy(self.exchange.positions.open_positions)

        for position in position_dict.values():
            if position.symbol not in current_open_positions:
                side = OrderSide.buy if position.side == PositionSide.long else OrderSide.sell
                self.exchange.orders.create_order(
                    symbol=position.symbol,
                    side=side,
                    volume=position.volume,
                    execution_type=OrderExecutionType.market,
                    reason="open position",
                )
            elif current_open_positions[position.symbol].side == position.side:
                if current_open_positions[position.symbol].volume > position.volume:
                    side = OrderSide.sell if position.side == PositionSide.long else OrderSide.buy
                    self.exchange.orders.create_order(
                        symbol=position.symbol,
                        side=side,
                        volume=position.volume,
                        execution_type=OrderExecutionType.market,
                        reason="reduce position",
                    )
                else:
                    side = OrderSide.buy if position.side == PositionSide.long else OrderSide.sell
                    volume_to_increase = (
                        position.volume - current_open_positions[position.symbol].volume
                    )
                    self.exchange.orders.create_order(
                        symbol=position.symbol,
                        side=side,
                        volume=volume_to_increase,
                        execution_type=OrderExecutionType.market,
                        reason="increase position",
                    )
            else:
                self.exchange.positions.close_position(
                    position=current_open_positions[position.symbol]
                )
                side = OrderSide.buy if position.side == PositionSide.long else OrderSide.sell
                self.exchange.orders.create_order(
                    symbol=position.symbol,
                    side=side,
                    volume=position.volume,
                    execution_type=OrderExecutionType.market,
                    reason="open position",
                )

        for order in allocation.orders:
            self.exchange.orders.create_order(
                symbol=order.symbol,
                side=order.side,
                volume=order.volume,
                execution_type=order.execution_type,
                price=order.price,
                reason="sl / tp",
            )

        exchange_exposure = self.exchange.get_exposure()
        portfolio_exposure = self.portfolio.exposure
        gross_p = portfolio_exposure["gross"]
        gross_e = exchange_exposure["gross"]
        self.exchange.add_log(
            f"Rebalanced: Portfolio Exposure {gross_p} | Exchange Exposure {gross_e}"
        )
