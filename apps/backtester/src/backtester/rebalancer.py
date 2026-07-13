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
    """Reconciles the exchange's actual open positions/orders against the portfolio's
    merged target allocation whenever that allocation changes."""

    def __init__(self, market: Market, exchange: Exchange, portfolio: Portfolio):
        """Snapshots the portfolio's current allocation hash so the first refresh() only
        rebalances if the allocation has changed since construction."""
        self.rebalancing_plan: list[Order] = []

        self.market: Market = market
        self.exchange: Exchange = exchange
        self.portfolio: Portfolio = portfolio
        # tracks the last-seen allocation hash to detect changes
        self.signal_allocation_change_time_hash = self.portfolio.signal_allocation_change_time_hash

    def get_allocation(self):
        """Returns the portfolio's current merged target allocation."""
        return self.portfolio.merged_allocation

    def refresh(self):
        """Triggers rebalance() only if the portfolio's allocation hash has changed
        since the last refresh (no-op otherwise, or if there are no strategies at all)."""
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

    def rebalance(self):  # noqa: PLR0912
        """Cancels all open orders, sizes the target allocation's positions/orders in
        volume terms, closes positions no longer in the target, opens/increases/reduces
        positions to match target sizes (in `allocation.positions` order -- see
        Portfolio._symbol_priority for how that order is set, highest-priority first),
        re-creates TP/SL orders for whatever actually opened, and logs the resulting
        exposure.

        Sizing is leveraged (`* self.exchange.max_leverage`) only when
        `self.exchange.leverage_aware_sizing` is set -- see Exchange.__init__'s
        docstring for why that's opt-in rather than always-on."""
        self.exchange.orders.cancel_open_orders()

        total_balance = self.exchange.get_asset_total_in_usd()
        allocation = self.get_allocation()
        leverage = self.exchange.max_leverage if self.exchange.leverage_aware_sizing else 1
        position_dict = dict()

        for position in allocation.positions:
            price = self.exchange.get_market_price(symbol=position.symbol)
            point_value = self.exchange.get_point_value(position.symbol)
            raw_volume = (total_balance * position.percent * leverage) / (price * point_value)
            position.volume = self.exchange.round_position_size(position.symbol, raw_volume)
            if position.volume > 0:
                position_dict[position.symbol] = position
            # else: floored to 0 contracts -- skip opening entirely. Since it's omitted
            # from position_dict, the "close positions not in position_dict" loop below
            # correctly flattens it if one was already open.

        for order in allocation.orders:
            price = self.exchange.get_market_price(symbol=order.symbol)
            point_value = self.exchange.get_point_value(order.symbol)
            raw_volume = (total_balance * order.percent * leverage) / (price * point_value)
            order.volume = self.exchange.round_position_size(order.symbol, raw_volume)

        current_open_positions = copy.deepcopy(self.exchange.positions.open_positions)

        for open_position in current_open_positions.values():
            if open_position.symbol not in position_dict:
                self.exchange.positions.close_position(position=open_position)

        current_open_positions = copy.deepcopy(self.exchange.positions.open_positions)

        # Symbols actually opened/resized this tick (as opposed to merely targeted in
        # position_dict) -- only these get their SL/TP orders re-created below. A
        # symbol can be targeted but still end up here without succeeding: leveraged
        # sizing can ask for more than whatever free margin remains once
        # higher-priority positions (processed first, in list order) have already
        # claimed their share.
        opened_symbols: set[str] = set()

        for position in position_dict.values():
            try:
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
                        side = (
                            OrderSide.sell if position.side == PositionSide.long else OrderSide.buy
                        )
                        self.exchange.orders.create_order(
                            symbol=position.symbol,
                            side=side,
                            volume=position.volume,
                            execution_type=OrderExecutionType.market,
                            reason="reduce position",
                        )
                    else:
                        side = (
                            OrderSide.buy if position.side == PositionSide.long else OrderSide.sell
                        )
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
            except ValueError as exc:
                # Insufficient margin for this (lower-priority) position -- skip it
                # rather than letting the whole backtest crash. Reducing an existing
                # position only ever frees margin, so this can only happen on an
                # open/increase/re-open.
                self.exchange.add_log(
                    f"Skipped {position.symbol} ({position.side.value}, "
                    f"{position.volume} contracts) -- insufficient margin: {exc}"
                )
                continue

            opened_symbols.add(position.symbol)

        for order in allocation.orders:
            if order.volume <= 0 or order.symbol not in opened_symbols:
                continue
            try:
                self.exchange.orders.create_order(
                    symbol=order.symbol,
                    side=order.side,
                    volume=order.volume,
                    execution_type=order.execution_type,
                    price=order.price,
                    reason="sl / tp",
                )
            except ValueError as exc:
                self.exchange.add_log(f"Skipped SL/TP order for {order.symbol}: {exc}")

        exchange_exposure = self.exchange.get_exposure()
        portfolio_exposure = self.portfolio.exposure
        gross_p = portfolio_exposure["gross"]
        gross_e = exchange_exposure["gross"]
        self.exchange.add_log(
            f"Rebalanced: Portfolio Exposure {gross_p} | Exchange Exposure {gross_e}"
        )
