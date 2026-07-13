"""Hand-verified unit tests for Rebalancer.rebalance()'s position-sizing math: whole-
contract flooring (Tradovate/futures, via a fake SymbolConfigProvider), skip-if-floored-
to-zero, closing an existing position that floors to zero on a later rebalance, and a
regression check that fractional crypto sizing (no provider) is unaffected."""

import pytest
from conftest import build_market, make_exchange

from backtester.exchange import PositionSide
from backtester.exchange.symbol_config import TradovateSymbolConfigProvider
from backtester.exchange_config import SymbolConfig
from backtester.portfolio import Portfolio, WeightedStrategy
from backtester.rebalancer import Rebalancer
from backtester.strategies.base import Allocation, AllocationPosition, Strategy


class _TargetPercentStrategy(Strategy):
    """Test double: wants `target_percent` of the portfolio long in `symbol` (0 = flat).
    Always marks its allocation changed on refresh(), so every call to portfolio.refresh()
    re-triggers the rebalancer regardless of whether target_percent actually changed."""

    def __init__(self, key, market, symbol):
        super().__init__(key, market, symbol)
        self.target_percent: float = 0.0

    def refresh(self) -> None:
        self.allocation = Allocation()
        if self.target_percent > 0:
            self.allocation.positions = [
                AllocationPosition(
                    side=PositionSide.long,
                    symbol=self.symbol,
                    percent=self.target_percent,
                    average_open_price=self.market.current[self.symbol]["close"],
                )
            ]
        self._mark_allocation_changed()


def _mes_provider() -> TradovateSymbolConfigProvider:
    return TradovateSymbolConfigProvider(
        symbols={"MES": SymbolConfig(point_value=5.0, tick_size=0.25, slippage_ticks=0.0)}
    )


def _wire(market, exchange, symbol) -> tuple[_TargetPercentStrategy, Rebalancer]:
    strategy = _TargetPercentStrategy(key="s", market=market, symbol=symbol)
    portfolio = Portfolio(weighted_strategies=[WeightedStrategy(weight=1, strategy=strategy)])
    rebalancer = Rebalancer(market=market, exchange=exchange, portfolio=portfolio)
    return strategy, rebalancer


def _step(market, rebalancer) -> None:
    market.set_next_candle_as_current_market()
    rebalancer.portfolio.refresh()
    rebalancer.refresh()


class TestFuturesWholeContractRounding:
    def test_floors_down_to_whole_contract(self):
        # raw_volume = (100000 * 0.725) / (5000 * 5) = 2.9 -> floors to 2.
        market = build_market(
            {"MES/USD": [{"open": 5000.0, "high": 5000.0, "low": 5000.0, "close": 5000.0}] * 3}
        )
        exchange = make_exchange(market, max_leverage=1, symbol_config_provider=_mes_provider())
        exchange.transactions.add_deposit(asset="USD", volume=100_000)
        strategy, rebalancer = _wire(market, exchange, "MES/USD")
        strategy.target_percent = 0.725

        _step(market, rebalancer)

        position = exchange.positions.get_open_position_by_symbol("MES/USD")
        assert position is not None
        assert position.volume == 2

    def test_skips_opening_when_floored_to_zero(self):
        # raw_volume = (100000 * 0.1) / (5000 * 5) = 0.4 -> floors to 0, below min size 1.
        market = build_market(
            {"MES/USD": [{"open": 5000.0, "high": 5000.0, "low": 5000.0, "close": 5000.0}] * 3}
        )
        exchange = make_exchange(market, max_leverage=1, symbol_config_provider=_mes_provider())
        exchange.transactions.add_deposit(asset="USD", volume=100_000)
        strategy, rebalancer = _wire(market, exchange, "MES/USD")
        strategy.target_percent = 0.1

        _step(market, rebalancer)

        assert exchange.positions.get_open_position_by_symbol("MES/USD") is None
        assert exchange.orders.get_orders() == []

    def test_existing_position_closed_when_a_later_rebalance_floors_to_zero(self):
        market = build_market(
            {"MES/USD": [{"open": 5000.0, "high": 5000.0, "low": 5000.0, "close": 5000.0}] * 3}
        )
        exchange = make_exchange(market, max_leverage=1, symbol_config_provider=_mes_provider())
        exchange.transactions.add_deposit(asset="USD", volume=100_000)
        strategy, rebalancer = _wire(market, exchange, "MES/USD")
        strategy.target_percent = 0.725

        _step(market, rebalancer)
        assert exchange.positions.get_open_position_by_symbol("MES/USD") is not None

        strategy.target_percent = 0.1
        _step(market, rebalancer)

        assert exchange.positions.get_open_position_by_symbol("MES/USD") is None


def _multi_symbol_provider() -> TradovateSymbolConfigProvider:
    return TradovateSymbolConfigProvider(
        symbols={
            "MES": SymbolConfig(point_value=5.0, tick_size=0.25, slippage_ticks=0.0),
            "NQ": SymbolConfig(point_value=5.0, tick_size=0.25, slippage_ticks=0.0),
        }
    )


class TestLeverageAwareSizing:
    def test_disabled_by_default_matches_unleveraged_formula(self):
        # raw_volume = (100000 * 0.725) / (5000 * 5) = 2.9 -> floors to 2, identical to
        # TestFuturesWholeContractRounding.test_floors_down_to_whole_contract even
        # though max_leverage=5 here -- leverage_aware_sizing defaults False.
        market = build_market(
            {"MES/USD": [{"open": 5000.0, "high": 5000.0, "low": 5000.0, "close": 5000.0}] * 3}
        )
        exchange = make_exchange(market, max_leverage=5, symbol_config_provider=_mes_provider())
        exchange.transactions.add_deposit(asset="USD", volume=100_000)
        strategy, rebalancer = _wire(market, exchange, "MES/USD")
        strategy.target_percent = 0.725

        _step(market, rebalancer)

        position = exchange.positions.get_open_position_by_symbol("MES/USD")
        assert position is not None
        assert position.volume == 2

    def test_enabled_multiplies_size_by_max_leverage(self):
        # Same 0.725 target as above, but leverage_aware_sizing=True with
        # max_leverage=5: raw_volume = (100000 * 0.725 * 5) / (5000 * 5) = 14.5 -> 14.
        market = build_market(
            {"MES/USD": [{"open": 5000.0, "high": 5000.0, "low": 5000.0, "close": 5000.0}] * 3}
        )
        exchange = make_exchange(
            market,
            max_leverage=5,
            symbol_config_provider=_mes_provider(),
            leverage_aware_sizing=True,
        )
        exchange.transactions.add_deposit(asset="USD", volume=100_000)
        strategy, rebalancer = _wire(market, exchange, "MES/USD")
        strategy.target_percent = 0.725

        _step(market, rebalancer)

        position = exchange.positions.get_open_position_by_symbol("MES/USD")
        assert position is not None
        assert position.volume == 14


class TestPriorityOrderedMarginConflict:
    def test_higher_priority_position_sized_fully_lower_priority_skipped_not_crashed(self):
        # Two symbols, each wanting 90% of equity -- can't both fit (180% > 100%).
        # MES is listed first in weighted_strategies (highest priority): gets its
        # full 3-contract target. NQ, listed second, only has $25,000 of margin left
        # (needs $75,000) -- skipped, logged, and the backtest keeps running instead
        # of raising.
        market = build_market(
            {
                "MES/USD": [
                    {"open": 5000.0, "high": 5000.0, "low": 5000.0, "close": 5000.0}
                ]
                * 3,
                "NQ/USD": [
                    {"open": 5000.0, "high": 5000.0, "low": 5000.0, "close": 5000.0}
                ]
                * 3,
            }
        )
        exchange = make_exchange(
            market, max_leverage=1, symbol_config_provider=_multi_symbol_provider()
        )
        exchange.transactions.add_deposit(asset="USD", volume=100_000)

        mes_strategy = _TargetPercentStrategy(key="mes", market=market, symbol="MES/USD")
        nq_strategy = _TargetPercentStrategy(key="nq", market=market, symbol="NQ/USD")
        mes_strategy.target_percent = 0.9
        nq_strategy.target_percent = 0.9

        portfolio = Portfolio(
            weighted_strategies=[
                WeightedStrategy(weight=1, strategy=mes_strategy),  # priority 0 -- highest
                WeightedStrategy(weight=1, strategy=nq_strategy),  # priority 1
            ]
        )
        rebalancer = Rebalancer(market=market, exchange=exchange, portfolio=portfolio)

        market.set_next_candle_as_current_market()
        rebalancer.portfolio.refresh()
        rebalancer.refresh()  # must not raise

        mes_position = exchange.positions.get_open_position_by_symbol("MES/USD")
        assert mes_position is not None
        assert mes_position.volume == 3  # (100000*0.9)/(5000*5) = 3.6 -> 3

        assert exchange.positions.get_open_position_by_symbol("NQ/USD") is None
        assert any("insufficient margin" in log["message"] for log in exchange.get_logs())


class TestCryptoUnaffected:
    def test_fractional_sizing_unchanged_without_provider(self):
        # raw_volume = (1000 * 0.33) / 100 = 3.3 -- no provider, no flooring.
        market = build_market(
            {"BTC/USD": [{"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0}] * 3}
        )
        exchange = make_exchange(market, max_leverage=1)
        exchange.transactions.add_deposit(asset="USD", volume=1000)
        strategy, rebalancer = _wire(market, exchange, "BTC/USD")
        strategy.target_percent = 0.33

        _step(market, rebalancer)

        position = exchange.positions.get_open_position_by_symbol("BTC/USD")
        assert position is not None
        assert position.volume == pytest.approx(3.3)
