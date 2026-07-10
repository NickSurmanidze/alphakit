"""Hand-verified unit tests for point-value-aware margin/fee/slippage/PnL math (the
Tradovate/futures path through Exchange/Orders/Positions), plus a regression check that
the crypto path (symbol_config_provider=None) is a pure pass-through, unaffected by any
of this.

Scenario used throughout: a fake MES/USD contract, point_value=5 ($/point), tick_size=
0.25, slippage_ticks=1 (so slippage = 0.25 points), fee=$1/contract, no leverage.
"""

import pytest
from conftest import build_market, make_exchange

from backtester.exchange import OrderExecutionType, OrderSide
from backtester.exchange.symbol_config import TradovateSymbolConfigProvider
from backtester.exchange_config import SymbolConfig


def _mes_provider() -> TradovateSymbolConfigProvider:
    return TradovateSymbolConfigProvider(
        symbols={
            "MES": SymbolConfig(
                point_value=5.0,
                tick_size=0.25,
                slippage_ticks=1.0,
                fee_per_contract_override=1.0,
            )
        }
    )


class TestOrderFillMath:
    def test_price_with_slippage_is_absolute_tick_offset_not_percentage(self):
        market = build_market(
            {"MES/USD": [{"open": 5000.0, "high": 5000.0, "low": 5000.0, "close": 5000.0}] * 2}
        )
        exchange = make_exchange(market, max_leverage=1, symbol_config_provider=_mes_provider())
        exchange.transactions.add_deposit(asset="USD", volume=100_000)

        order = exchange.orders.create_order(
            symbol="MES/USD",
            side=OrderSide.buy,
            execution_type=OrderExecutionType.market,
            volume=2,
        )

        assert order.price_with_slippage == pytest.approx(5000.25)  # 5000 + 1 tick * 0.25
        assert order.fees_volume == pytest.approx(2.0)  # $1/contract * 2 contracts
        assert order.fees_asset == "USD"

    def test_sell_side_slippage_subtracts(self):
        market = build_market(
            {"MES/USD": [{"open": 5000.0, "high": 5000.0, "low": 5000.0, "close": 5000.0}] * 2}
        )
        exchange = make_exchange(market, max_leverage=1, symbol_config_provider=_mes_provider())
        exchange.transactions.add_deposit(asset="USD", volume=100_000)

        order = exchange.orders.create_order(
            symbol="MES/USD",
            side=OrderSide.sell,
            execution_type=OrderExecutionType.market,
            volume=1,
        )

        assert order.price_with_slippage == pytest.approx(4999.75)  # 5000 - 1 tick * 0.25


class TestPositionMargin:
    def test_margin_and_value_use_point_value_multiplier(self):
        # margin = volume * price_with_slippage * point_value / max_leverage
        #        = 2 * 5000.25 * 5 / 1 = 50002.5
        market = build_market(
            {"MES/USD": [{"open": 5000.0, "high": 5000.0, "low": 5000.0, "close": 5000.0}] * 2}
        )
        exchange = make_exchange(market, max_leverage=1, symbol_config_provider=_mes_provider())
        exchange.transactions.add_deposit(asset="USD", volume=100_000)

        exchange.orders.create_order(
            symbol="MES/USD",
            side=OrderSide.buy,
            execution_type=OrderExecutionType.market,
            volume=2,
        )
        position = exchange.positions.get_open_position_by_symbol("MES/USD")

        assert position.margin_used_in_usd == pytest.approx(50002.5)
        # value_in_usd marks to the current candle's close (5000.0), not the fill price:
        # 2 * 5000.0 * 5 = 50000.0 -- matches the pre-existing (crypto) convert_asset_
        # volume-based quirk this path deliberately preserves.
        assert position.value_in_usd == pytest.approx(50000.0)

    def test_fee_deducted_from_balance_on_fill(self):
        market = build_market(
            {"MES/USD": [{"open": 5000.0, "high": 5000.0, "low": 5000.0, "close": 5000.0}] * 2}
        )
        exchange = make_exchange(market, max_leverage=1, symbol_config_provider=_mes_provider())
        exchange.transactions.add_deposit(asset="USD", volume=100_000)

        exchange.orders.create_order(
            symbol="MES/USD",
            side=OrderSide.buy,
            execution_type=OrderExecutionType.market,
            volume=2,
        )

        # 100000 - 50002.5 margin - 2.0 fee = 49995.5 free.
        assert exchange.balance.get_balance()["USD"]["free"]["volume"] == pytest.approx(49995.5)


class TestRefreshPositionPnl:
    def test_pnl_and_value_scale_by_point_value_on_price_move(self):
        # Long 2 @ fill 5000.25, price rises to 5010 -> pnl = 2 * 5 * (5010 - 5000.25) = 97.5
        market = build_market(
            {
                "MES/USD": [
                    {"open": 5000.0, "high": 5000.0, "low": 5000.0, "close": 5000.0},
                    {"open": 5010.0, "high": 5010.0, "low": 5010.0, "close": 5010.0},
                ]
            }
        )
        exchange = make_exchange(market, max_leverage=1, symbol_config_provider=_mes_provider())
        exchange.transactions.add_deposit(asset="USD", volume=100_000)
        exchange.orders.create_order(
            symbol="MES/USD",
            side=OrderSide.buy,
            execution_type=OrderExecutionType.market,
            volume=2,
        )

        market.set_next_candle_as_current_market()
        exchange.positions.refresh_open_positions()

        position = exchange.positions.get_open_position_by_symbol("MES/USD")
        assert position.pnl_in_usd == pytest.approx(97.5)
        assert position.value_in_usd == pytest.approx(50100.0)  # 2 * 5010 * 5


class TestNoProviderIsPassThrough:
    def test_get_point_value_defaults_to_one(self):
        market = build_market(
            {"BTC/USD": [{"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0}]}
        )
        exchange = make_exchange(market, max_leverage=1)
        assert exchange.get_point_value("BTC/USD") == 1.0

    def test_round_position_size_is_unchanged_without_provider(self):
        market = build_market(
            {"BTC/USD": [{"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0}]}
        )
        exchange = make_exchange(market, max_leverage=1)
        assert exchange.round_position_size("BTC/USD", 3.7) == pytest.approx(3.7)

    def test_crypto_fractional_position_math_is_unaffected(self):
        # No provider -> percentage slippage/fee model, no point-value multiplier, no
        # whole-contract rounding -- exact same numbers as before this feature existed.
        market = build_market(
            {"BTC/USD": [{"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0}] * 2}
        )
        exchange = make_exchange(market, max_leverage=1, slippage=0.01, taker_fee=0.001)
        exchange.transactions.add_deposit(asset="USD", volume=10000)

        order = exchange.orders.create_order(
            symbol="BTC/USD",
            side=OrderSide.buy,
            execution_type=OrderExecutionType.market,
            volume=3.3,
        )

        assert order.price_with_slippage == pytest.approx(101.0)  # 100 * 1.01
        position = exchange.positions.get_open_position_by_symbol("BTC/USD")
        assert position.volume == pytest.approx(3.3)
