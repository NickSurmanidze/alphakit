"""Tests for the structured audit trail (EventLog) -- confirms every state-changing
order/position action emits the right event with the right fields, and that disabling
the log actually suppresses recording."""

from conftest import build_market, make_exchange

from backtester.exchange import (
    OrderCanceled,
    OrderCreated,
    OrderExecutionType,
    OrderFilled,
    OrderSide,
    PositionClosed,
    PositionIncreased,
    PositionLiquidated,
    PositionOpened,
    PositionReduced,
    PositionSide,
)


class TestOrderEvents:
    def test_market_buy_emits_created_then_filled(self):
        market = build_market(
            {"BTC/USD": [{"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0}] * 2}
        )
        exchange = make_exchange(market, max_leverage=1)
        exchange.transactions.add_deposit(asset="USD", volume=10000)

        order = exchange.orders.create_order(
            symbol="BTC/USD",
            side=OrderSide.buy,
            execution_type=OrderExecutionType.market,
            volume=10,
        )

        created = exchange.event_log.get_events(OrderCreated)
        filled = exchange.event_log.get_events(OrderFilled)
        assert len(created) == 1
        assert created[0].order_id == order.id
        assert created[0].symbol == "BTC/USD"
        assert created[0].side == OrderSide.buy
        assert created[0].volume == 10
        assert len(filled) == 1
        assert filled[0].order_id == order.id
        assert filled[0].price == order.price_with_slippage
        assert filled[0].fees_volume == order.fees_volume

    def test_limit_order_only_emits_created_until_it_fills(self):
        market = build_market(
            {"BTC/USD": [{"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0}] * 2}
        )
        exchange = make_exchange(market, max_leverage=1)
        exchange.transactions.add_deposit(asset="USD", volume=10000)

        exchange.orders.create_order(
            symbol="BTC/USD",
            side=OrderSide.buy,
            execution_type=OrderExecutionType.limit,
            volume=10,
            price=95.0,
        )

        assert len(exchange.event_log.get_events(OrderCreated)) == 1
        assert len(exchange.event_log.get_events(OrderFilled)) == 0

    def test_cancel_open_orders_emits_canceled_events(self):
        market = build_market(
            {"BTC/USD": [{"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0}] * 2}
        )
        exchange = make_exchange(market, max_leverage=1)
        exchange.transactions.add_deposit(asset="USD", volume=10000)
        o1 = exchange.orders.create_order(
            symbol="BTC/USD",
            side=OrderSide.buy,
            execution_type=OrderExecutionType.limit,
            volume=1,
            price=95.0,
        )
        o2 = exchange.orders.create_order(
            symbol="BTC/USD",
            side=OrderSide.sell,
            execution_type=OrderExecutionType.limit,
            volume=1,
            price=105.0,
        )

        exchange.orders.cancel_open_orders()

        canceled_ids = {e.order_id for e in exchange.event_log.get_events(OrderCanceled)}
        assert canceled_ids == {o1.id, o2.id}

    def test_take_profit_fill_emits_filled_for_tp_and_canceled_for_paired_sl(self):
        market = build_market(
            {
                "BTC/USD": [
                    {"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0},
                    {"open": 100.0, "high": 110.0, "low": 100.0, "close": 108.0},
                ]
            }
        )
        exchange = make_exchange(market, max_leverage=1)
        exchange.transactions.add_deposit(asset="USD", volume=10000)
        exchange.orders.create_order(
            symbol="BTC/USD",
            side=OrderSide.buy,
            execution_type=OrderExecutionType.market,
            volume=10,
        )
        sl_order, tp_order = exchange.orders.create_oco_order(
            symbol="BTC/USD",
            volume=10,
            stop_loss_price=90.0,
            take_profit_price=105.0,
            is_reduce_only=True,
        )
        exchange.event_log.events.clear()  # isolate this tick's events from the setup above

        market.set_next_candle_as_current_market()
        exchange.orders.refresh_open_orders()

        filled = exchange.event_log.get_events(OrderFilled)
        canceled = exchange.event_log.get_events(OrderCanceled)
        assert [e.order_id for e in filled] == [tp_order.id]
        assert [e.order_id for e in canceled] == [sl_order.id]


class TestPositionEvents:
    def test_create_position_emits_position_opened(self):
        market = build_market(
            {"BTC/USD": [{"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0}] * 2}
        )
        exchange = make_exchange(market, max_leverage=1)
        exchange.transactions.add_deposit(asset="USD", volume=10000)

        exchange.orders.create_order(
            symbol="BTC/USD",
            side=OrderSide.buy,
            execution_type=OrderExecutionType.market,
            volume=10,
        )

        opened = exchange.event_log.get_events(PositionOpened)
        assert len(opened) == 1
        assert opened[0].symbol == "BTC/USD"
        assert opened[0].side == PositionSide.long
        assert opened[0].volume == 10
        assert opened[0].margin_used_in_usd == 1000.0

    def test_increase_position_emits_position_increased(self):
        market = build_market(
            {
                "BTC/USD": [
                    {"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0},
                    {"open": 120.0, "high": 120.0, "low": 120.0, "close": 120.0},
                ]
            }
        )
        exchange = make_exchange(market, max_leverage=1)
        exchange.transactions.add_deposit(asset="USD", volume=100000)
        exchange.orders.create_order(
            symbol="BTC/USD",
            side=OrderSide.buy,
            execution_type=OrderExecutionType.market,
            volume=10,
        )
        market.set_next_candle_as_current_market()

        exchange.orders.create_order(
            symbol="BTC/USD",
            side=OrderSide.buy,
            execution_type=OrderExecutionType.market,
            volume=10,
        )

        increased = exchange.event_log.get_events(PositionIncreased)
        assert len(increased) == 1
        assert increased[0].added_volume == 10
        assert increased[0].new_volume == 20
        assert increased[0].new_average_entry_price == 110.0

    def test_partial_reduce_emits_position_reduced_not_closed(self):
        market = build_market(
            {"BTC/USD": [{"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0}] * 2}
        )
        exchange = make_exchange(market, max_leverage=1)
        exchange.transactions.add_deposit(asset="USD", volume=10000)
        exchange.orders.create_order(
            symbol="BTC/USD",
            side=OrderSide.buy,
            execution_type=OrderExecutionType.market,
            volume=10,
        )
        market.set_next_candle_as_current_market()

        exchange.orders.create_order(
            symbol="BTC/USD",
            side=OrderSide.sell,
            execution_type=OrderExecutionType.market,
            volume=4,
            is_reduce_only=True,
        )

        assert len(exchange.event_log.get_events(PositionReduced)) == 1
        assert len(exchange.event_log.get_events(PositionClosed)) == 0
        reduced = exchange.event_log.get_events(PositionReduced)[0]
        assert reduced.reduced_volume == 4
        assert reduced.remaining_volume == 6

    def test_full_reduce_emits_position_closed_with_realized_pnl(self):
        market = build_market(
            {
                "BTC/USD": [
                    {"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0},
                    {"open": 120.0, "high": 120.0, "low": 120.0, "close": 120.0},
                ]
            }
        )
        exchange = make_exchange(market, max_leverage=1)
        exchange.transactions.add_deposit(asset="USD", volume=10000)
        exchange.orders.create_order(
            symbol="BTC/USD",
            side=OrderSide.buy,
            execution_type=OrderExecutionType.market,
            volume=10,
        )
        market.set_next_candle_as_current_market()

        exchange.orders.create_order(
            symbol="BTC/USD",
            side=OrderSide.sell,
            execution_type=OrderExecutionType.market,
            volume=10,
            is_reduce_only=True,
        )

        closed = exchange.event_log.get_events(PositionClosed)
        assert len(closed) == 1
        assert closed[0].realized_pnl_in_usd == 200.0  # 10 * (120 - 100)
        assert len(exchange.event_log.get_events(PositionReduced)) == 0

    def test_isolated_liquidation_emits_position_liquidated(self):
        market = build_market(
            {
                "BTC/USD": [
                    {"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0},
                    {"open": 100.0, "high": 100.0, "low": 10.0, "close": 10.0},
                ]
            }
        )
        exchange = make_exchange(market, max_leverage=5)
        exchange.transactions.add_deposit(asset="USD", volume=10000)
        exchange.orders.create_order(
            symbol="BTC/USD",
            side=OrderSide.buy,
            execution_type=OrderExecutionType.market,
            volume=10,
        )

        market.set_next_candle_as_current_market()
        exchange.positions.refresh_open_positions()

        liquidated = exchange.event_log.get_events(PositionLiquidated)
        assert len(liquidated) == 1
        assert liquidated[0].symbol == "BTC/USD"


class TestEventLogToggle:
    def test_disabled_event_log_records_nothing(self):
        market = build_market(
            {"BTC/USD": [{"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0}] * 2}
        )
        exchange = make_exchange(market, max_leverage=1, event_log_enabled=False)
        exchange.transactions.add_deposit(asset="USD", volume=10000)

        exchange.orders.create_order(
            symbol="BTC/USD",
            side=OrderSide.buy,
            execution_type=OrderExecutionType.market,
            volume=10,
        )

        assert exchange.event_log.enabled is False
        assert exchange.event_log.events == []
