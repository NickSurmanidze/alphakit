"""Hand-verified unit tests for TradovateSymbolConfigProvider: fee/slippage lookup,
override precedence, and failure modes (missing tick size, unknown symbol)."""

import pytest

from backtester.exchange.symbol_config import TradovateSymbolConfigProvider
from backtester.exchange_config import SymbolConfig


class TestGetFee:
    def test_uses_default_fee_per_contract_when_no_override(self):
        provider = TradovateSymbolConfigProvider(
            symbols={"ES": SymbolConfig(point_value=50)}, default_fee_per_contract=2.5
        )
        amount, currency = provider.get_fee("ES", position_size=3)
        assert amount == pytest.approx(7.5)  # 2.5 * 3
        assert currency == "USD"

    def test_symbol_override_takes_precedence_over_default(self):
        provider = TradovateSymbolConfigProvider(
            symbols={"MES": SymbolConfig(point_value=5, fee_per_contract_override=0.5)},
            default_fee_per_contract=2.5,
        )
        amount, _currency = provider.get_fee("MES", position_size=4)
        assert amount == pytest.approx(2.0)  # 0.5 * 4, not 2.5 * 4

    def test_unknown_symbol_raises(self):
        provider = TradovateSymbolConfigProvider(symbols={})
        with pytest.raises(ValueError, match="No Tradovate symbol config registered"):
            provider.get_fee("XX", position_size=1)


class TestGetSlippage:
    def test_ticks_times_tick_size(self):
        provider = TradovateSymbolConfigProvider(
            symbols={"ES": SymbolConfig(point_value=50, tick_size=0.25, slippage_ticks=1.0)}
        )
        amount, unit = provider.get_slippage("ES", position_size=1)
        assert amount == pytest.approx(0.25)
        assert unit == "points"

    def test_zero_slippage_ticks_short_circuits_without_needing_tick_size(self):
        provider = TradovateSymbolConfigProvider(
            symbols={"MES": SymbolConfig(point_value=5, tick_size=None, slippage_ticks=0)}
        )
        amount, unit = provider.get_slippage("MES", position_size=1)
        assert amount == 0.0
        assert unit == "points"

    def test_missing_tick_size_raises_when_slippage_ticks_nonzero(self):
        provider = TradovateSymbolConfigProvider(
            symbols={"MES": SymbolConfig(point_value=5, tick_size=None, slippage_ticks=1.0)}
        )
        with pytest.raises(ValueError, match="tick_size not yet researched"):
            provider.get_slippage("MES", position_size=1)

    def test_unknown_symbol_raises(self):
        provider = TradovateSymbolConfigProvider(symbols={})
        with pytest.raises(ValueError, match="No Tradovate symbol config registered"):
            provider.get_slippage("XX", position_size=1)


class TestOtherLookups:
    def test_point_value_min_size_min_step(self):
        provider = TradovateSymbolConfigProvider(
            symbols={"ES": SymbolConfig(point_value=50, min_position_size=1.0, min_order_step=1.0)}
        )
        assert provider.get_point_value("ES") == 50
        assert provider.get_min_position_size("ES") == 1.0
        assert provider.get_min_order_step("ES") == 1.0

    def test_default_symbols_table_is_tradovate_futures(self):
        # Constructed with no `symbols` override -> falls back to the real
        # TRADOVATE_FUTURES table (18 CME futures, EMD deliberately excluded).
        provider = TradovateSymbolConfigProvider()
        assert provider.get_point_value("ES") == 50
        assert provider.get_point_value("MES") == 5
        with pytest.raises(ValueError):
            provider.get_point_value("EMD")
