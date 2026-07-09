"""Hand-verified unit tests for Positions -- open/increase/reduce/close for both long and
short, liquidation (isolated + cross margin), and two regression tests for bugs fixed in
Phase 2 (see exchange/position.py):

1. A closed position's `pnl_in_usd` is now finalized to the realized PnL before it's
   copied into `closed_positions`, instead of carrying whatever `refresh_position()` last
   set (unrealized, possibly stale, 0 if never refreshed).
2. `close_all_open_positions()` snapshots `open_positions` before iterating, since
   `close_position()` mutates that same dict mid-loop -- iterating it live used to raise
   `RuntimeError: dictionary changed size during iteration` once 2+ positions were open.
"""

import pytest
from conftest import build_market, make_exchange

from backtester.exchange import (
    MarginAllocationType,
    OrderExecutionType,
    OrderSide,
    PositionSide,
    PositionStatus,
)


class TestOpenIncreaseReduce:
    def test_create_position_long(self):
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

        position = exchange.positions.get_open_position_by_symbol("BTC/USD")
        assert position.side == PositionSide.long
        assert position.volume == 10
        assert position.status == PositionStatus.open

    def test_create_position_short(self):
        market = build_market(
            {"BTC/USD": [{"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0}] * 2}
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

    def test_increase_position_volume_recomputes_weighted_average_entry(self):
        # Buy 10 @ 100, then 10 more @ 120 -> weighted avg = (100*10 + 120*10) / 20 = 110.
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

        position = exchange.positions.get_open_position_by_symbol("BTC/USD")
        assert position.volume == 20
        assert position.average_entry_price == pytest.approx(110.0)
        assert position.margin_used_in_usd == pytest.approx(2200.0)  # 1000 + 1200

    def test_partial_reduce_keeps_position_open(self):
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

        position = exchange.positions.get_open_position_by_symbol("BTC/USD")
        assert position.status == PositionStatus.open
        assert position.volume == 6
        assert position.margin_used_in_usd == pytest.approx(600.0)  # 1000 * 6/10

    def test_full_reduce_closes_position(self):
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
        position_id = exchange.positions.get_open_position_by_symbol("BTC/USD").id
        market.set_next_candle_as_current_market()

        exchange.orders.create_order(
            symbol="BTC/USD",
            side=OrderSide.sell,
            execution_type=OrderExecutionType.market,
            volume=10,
            is_reduce_only=True,
        )

        assert exchange.positions.get_open_position_by_symbol("BTC/USD") is None
        closed = exchange.positions.closed_positions[position_id]
        assert closed.status == PositionStatus.closed
        assert closed.close_time is not None


class TestBugFixRegressions:
    def test_closed_position_pnl_in_usd_reflects_realized_pnl(self):
        # Buy 10 @ 100, price rises to 120, close via a plain market order (never goes
        # through refresh_position() first) -- realized PnL = 10*(120-100) = 200. Before
        # the fix this stayed at the Position.__init__ default of 0.
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
        position = exchange.positions.get_open_position_by_symbol("BTC/USD")
        position_id = position.id
        market.set_next_candle_as_current_market()

        exchange.positions.close_position(position)

        closed = exchange.positions.closed_positions[position_id]
        assert closed.pnl_in_usd == pytest.approx(200.0)
        # Free balance: 10000 - 1000 margin (locked) + 1000 margin (returned) + 200 profit.
        assert exchange.balance.get_balance()["USD"]["free"]["volume"] == pytest.approx(10200.0)

    def test_close_all_open_positions_handles_two_or_more_positions(self):
        market = build_market(
            {
                "BTC/USD": [{"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0}] * 2,
                "ETH/USD": [{"open": 50.0, "high": 50.0, "low": 50.0, "close": 50.0}] * 2,
            }
        )
        exchange = make_exchange(market, max_leverage=1)
        exchange.transactions.add_deposit(asset="USD", volume=10000)
        exchange.orders.create_order(
            symbol="BTC/USD", side=OrderSide.buy, execution_type=OrderExecutionType.market, volume=1
        )
        exchange.orders.create_order(
            symbol="ETH/USD", side=OrderSide.buy, execution_type=OrderExecutionType.market, volume=1
        )

        exchange.positions.close_all_open_positions()

        assert exchange.positions.get_open_positions() == {}
        assert len(exchange.positions.closed_positions) == 2


class TestCloseErrors:
    def test_close_position_raises_when_symbol_not_open(self):
        market = build_market(
            {"BTC/USD": [{"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0}] * 2}
        )
        exchange = make_exchange(market, max_leverage=1)
        exchange.transactions.add_deposit(asset="USD", volume=10000)
        exchange.orders.create_order(
            symbol="BTC/USD", side=OrderSide.buy, execution_type=OrderExecutionType.market, volume=1
        )
        position = exchange.positions.get_open_position_by_symbol("BTC/USD")
        exchange.positions.close_position(position)  # already closed once

        with pytest.raises(ValueError, match="Position does not seem to exist"):
            exchange.positions.close_position(position)


class TestLiquidation:
    def test_isolated_margin_liquidates_when_loss_exceeds_position_margin(self):
        # 10x leverage, price crashes 100 -> 10 (90% drop). margin = 10*100/5 = 200 with 5x
        # leverage; unrealized loss at 10 = 10*(100-10) = 900, far exceeding the 200 margin.
        market = build_market(
            {
                "BTC/USD": [
                    {"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0},
                    {"open": 100.0, "high": 100.0, "low": 10.0, "close": 10.0},
                ]
            }
        )
        exchange = make_exchange(
            market, max_leverage=5, margin_allocation_type=MarginAllocationType.isolated
        )
        exchange.transactions.add_deposit(asset="USD", volume=10000)
        exchange.orders.create_order(
            symbol="BTC/USD",
            side=OrderSide.buy,
            execution_type=OrderExecutionType.market,
            volume=10,
        )

        market.set_next_candle_as_current_market()
        exchange.positions.refresh_open_positions()

        assert exchange.positions.get_open_position_by_symbol("BTC/USD") is None
        closed = [p for p in exchange.positions.closed_positions.values() if p.symbol == "BTC/USD"]
        assert len(closed) == 1
        assert closed[0].liquidated is True

    def test_cross_margin_liquidates_all_positions_when_total_loss_exceeds_balance(self):
        # $1000 deposit, two 10x-notional-ish positions (BTC + ETH, 2x leverage) whose
        # combined unrealized loss after both crash exceeds the entire $1000 cash balance.
        market = build_market(
            {
                "BTC/USD": [
                    {"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0},
                    {"open": 100.0, "high": 100.0, "low": 10.0, "close": 10.0},
                ],
                "ETH/USD": [
                    {"open": 50.0, "high": 50.0, "low": 50.0, "close": 50.0},
                    {"open": 50.0, "high": 50.0, "low": 5.0, "close": 5.0},
                ],
            }
        )
        exchange = make_exchange(
            market, max_leverage=2, margin_allocation_type=MarginAllocationType.cross
        )
        exchange.transactions.add_deposit(asset="USD", volume=1000)
        exchange.orders.create_order(
            symbol="BTC/USD",
            side=OrderSide.buy,
            execution_type=OrderExecutionType.market,
            volume=10,
        )
        exchange.orders.create_order(
            symbol="ETH/USD",
            side=OrderSide.buy,
            execution_type=OrderExecutionType.market,
            volume=10,
        )

        market.set_next_candle_as_current_market()
        exchange.positions.refresh_open_positions()

        assert exchange.positions.get_open_positions() == {}
        assert len(exchange.positions.closed_positions) == 2
        assert all(p.liquidated for p in exchange.positions.closed_positions.values())
