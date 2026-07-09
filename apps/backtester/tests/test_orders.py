"""Hand-verified unit tests for Orders -- fill matching, fee/slippage math, and OCO
lifecycle, against tiny synthetic candle sequences small enough to trace by hand.

Every numeric assertion below was cross-checked by actually running the scenario and
confirming the result matches the hand computation documented in each test's comment --
not copied from an unverified run.
"""

import pytest
from conftest import build_market, make_exchange

from backtester.exchange import MarketType, OrderExecutionType, OrderSide, OrderStatus, PositionSide


class TestMarketOrders:
    def test_market_buy_opens_long_position_and_locks_margin(self):
        # cost = 10 * 100 (raw market price, no slippage on a fresh position's fee/margin
        # base) -- but the *fill* price used for the position itself IS slippage-adjusted:
        # price_with_slippage = 100 + 100*0.002 = 100.2. margin = 10*100.2/1 = 1002.
        # fee is charged on the *unadjusted* notional: 10*100*0.001 = 1.0 (see module note
        # below -- this is an existing quirk in add_order_processing_logic, not something
        # this test suite is fixing, just documenting as current behavior).
        market = build_market(
            {"BTC/USD": [{"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0}] * 3}
        )
        exchange = make_exchange(market, slippage=0.002, taker_fee=0.001, max_leverage=1)
        exchange.transactions.add_deposit(asset="USD", volume=10000)

        order = exchange.orders.create_order(
            symbol="BTC/USD",
            side=OrderSide.buy,
            execution_type=OrderExecutionType.market,
            volume=10,
        )

        assert order.status == OrderStatus.closed
        assert order.price_with_slippage == pytest.approx(100.2)
        assert order.fees_volume == pytest.approx(1.0)

        position = exchange.positions.get_open_position_by_symbol("BTC/USD")
        assert position.side == PositionSide.long
        assert position.average_entry_price == pytest.approx(100.2)
        assert position.margin_used_in_usd == pytest.approx(1002.0)

        balance = exchange.balance.get_balance()["USD"]
        # total = 10000 - 1.0 fee = 9999; margin (1002) moved from free to used.
        assert balance["total"]["volume"] == pytest.approx(9999.0)
        assert balance["used"]["volume"] == pytest.approx(1002.0)
        assert balance["free"]["volume"] == pytest.approx(8997.0)

    def test_market_sell_opens_short_position(self):
        market = build_market(
            {"BTC/USD": [{"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0}] * 3}
        )
        exchange = make_exchange(market, max_leverage=1)
        exchange.transactions.add_deposit(asset="USD", volume=10000)

        exchange.orders.create_order(
            symbol="BTC/USD",
            side=OrderSide.sell,
            execution_type=OrderExecutionType.market,
            volume=10,
        )

        position = exchange.positions.get_open_position_by_symbol("BTC/USD")
        assert position.side == PositionSide.short
        assert position.average_entry_price == pytest.approx(100.0)

    def test_covering_a_profitable_short_returns_margin_plus_profit(self):
        # Short 10 @ 100 (no fees/slippage in this fixture), price drops to 80, cover (buy
        # reduce_only). PnL = volume*(entry - exit) = 10*(100-80) = 200. Margin (1000)
        # unlocked back to free, plus the 200 realized profit: 9000 + 1000 + 200 = 10200.
        market = build_market(
            {
                "BTC/USD": [
                    {"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0},
                    {"open": 100.0, "high": 100.0, "low": 80.0, "close": 80.0},
                ]
            }
        )
        exchange = make_exchange(market, max_leverage=1)
        exchange.transactions.add_deposit(asset="USD", volume=10000)
        exchange.orders.create_order(
            symbol="BTC/USD",
            side=OrderSide.sell,
            execution_type=OrderExecutionType.market,
            volume=10,
        )

        market.set_next_candle_as_current_market()
        exchange.orders.create_order(
            symbol="BTC/USD",
            side=OrderSide.buy,
            execution_type=OrderExecutionType.market,
            volume=10,
            is_reduce_only=True,
        )

        assert exchange.balance.get_balance()["USD"]["free"]["volume"] == pytest.approx(10200.0)
        assert exchange.positions.get_open_position_by_symbol("BTC/USD") is None


class TestLimitOrders:
    def test_limit_buy_stays_open_until_low_crosses_price(self):
        market = build_market(
            {
                "BTC/USD": [
                    {"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0},
                    {"open": 100.0, "high": 102.0, "low": 94.0, "close": 98.0},
                ]
            }
        )
        exchange = make_exchange(market, max_leverage=1)
        exchange.transactions.add_deposit(asset="USD", volume=10000)

        order = exchange.orders.create_order(
            symbol="BTC/USD",
            side=OrderSide.buy,
            execution_type=OrderExecutionType.limit,
            volume=10,
            price=95.0,
        )
        assert order.status == OrderStatus.open  # doesn't fill on the candle it was created on

        market.set_next_candle_as_current_market()  # candle low=94, crosses below 95
        exchange.orders.refresh_open_orders()

        assert order.status == OrderStatus.closed
        position = exchange.positions.get_open_position_by_symbol("BTC/USD")
        assert position.average_entry_price == pytest.approx(95.0)  # limit fills have no slippage

    def test_limit_order_rejected_on_wrong_side_of_market_price(self):
        market = build_market(
            {"BTC/USD": [{"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0}] * 2}
        )
        exchange = make_exchange(market)
        exchange.transactions.add_deposit(asset="USD", volume=10000)

        with pytest.raises(ValueError, match="Limit buy order price should be less"):
            exchange.orders.create_order(
                symbol="BTC/USD",
                side=OrderSide.buy,
                execution_type=OrderExecutionType.limit,
                volume=1,
                price=105.0,
            )
        with pytest.raises(ValueError, match="Limit sell order price should be more"):
            exchange.orders.create_order(
                symbol="BTC/USD",
                side=OrderSide.sell,
                execution_type=OrderExecutionType.limit,
                volume=1,
                price=95.0,
            )


class TestOcoOrders:
    def test_take_profit_fill_cancels_the_paired_stop_loss(self):
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

        market.set_next_candle_as_current_market()  # high=110, crosses take-profit at 105
        exchange.orders.refresh_open_orders()

        assert tp_order.status == OrderStatus.closed
        assert sl_order.status == OrderStatus.canceled
        assert exchange.positions.get_open_position_by_symbol("BTC/USD") is None

    def test_cancel_order_cancels_both_oco_legs(self):
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
        sl_order, tp_order = exchange.orders.create_oco_order(
            symbol="BTC/USD",
            volume=10,
            stop_loss_price=90.0,
            take_profit_price=105.0,
            is_reduce_only=True,
        )

        exchange.orders.cancel_order(sl_order)

        assert sl_order.status == OrderStatus.canceled
        assert tp_order.status == OrderStatus.canceled


class TestReduceOnlyValidation:
    def test_reduce_only_rejected_when_no_position_exists(self):
        market = build_market(
            {"BTC/USD": [{"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0}] * 2}
        )
        exchange = make_exchange(market)
        exchange.transactions.add_deposit(asset="USD", volume=10000)

        with pytest.raises(
            ValueError, match="Cannot create a reduce_only order when position does not exist"
        ):
            exchange.orders.create_order(
                symbol="BTC/USD",
                side=OrderSide.sell,
                execution_type=OrderExecutionType.market,
                volume=1,
                is_reduce_only=True,
            )


class TestSpotOrders:
    def test_spot_buy_moves_quote_asset_to_base_asset(self):
        market = build_market(
            {"BTC/USD": [{"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0}] * 2}
        )
        exchange = make_exchange(market, market_type=MarketType.spot, taker_fee=0.0)
        exchange.transactions.add_deposit(asset="USD", volume=1000)

        exchange.orders.create_order(
            symbol="BTC/USD", side=OrderSide.buy, execution_type=OrderExecutionType.market, volume=2
        )

        balance = exchange.balance.get_balance()
        assert balance["USD"]["free"]["volume"] == pytest.approx(800.0)  # 1000 - 2*100
        assert balance["BTC"]["free"]["volume"] == pytest.approx(2.0)


class TestCancelOpenOrders:
    def test_cancel_open_orders_cancels_every_open_order(self):
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

        assert o1.status == OrderStatus.canceled
        assert o2.status == OrderStatus.canceled
