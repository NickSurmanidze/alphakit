"""Hand-verified tests for PerformanceAnalyzer: the weekend/gap resample fix, the
periods_per_year annualization override, dollar-PnL/R-multiple/beta-correlation-alpha
summary metrics sourced from the EventLog and Trade.risk_percent, and merge_reports()
propagating both correctly."""

import numpy as np
import pandas as pd
import pytest
from conftest import build_market, make_exchange

from backtester.exchange import MarginAllocationType, OrderExecutionType, OrderSide, PositionSide
from backtester.market import Market
from backtester.performance import PerformanceAnalyzer, merge_reports
from backtester.performance.analyzer import _returns_from_series
from backtester.strategies import Trade, TradeResult


class TestReturnsFromSeriesGapFix:
    def test_gap_is_forward_filled_before_computing_returns(self):
        # Fri -> Sat (gap) -> Sun (gap) -> Mon, a real +10% move over the weekend.
        idx = pd.to_datetime(["2024-01-05", "2024-01-06", "2024-01-07", "2024-01-08"])
        series = pd.Series([1000.0, np.nan, np.nan, 1100.0], index=idx)

        simple_returns, log_returns = _returns_from_series(series)

        assert simple_returns.iloc[0] != simple_returns.iloc[0]  # first row NaN (no prior)
        assert simple_returns.iloc[1] == pytest.approx(0.0)  # Sat: flat (padded)
        assert simple_returns.iloc[2] == pytest.approx(0.0)  # Sun: flat (padded)
        # The critical assertion: Monday's return reflects the full weekend move
        # against Friday's real value, not NaN (which the naive pre-fix pct_change()
        # would have produced once fill_method=None becomes the pandas default).
        assert simple_returns.iloc[3] == pytest.approx(0.10)
        assert not np.isnan(log_returns.iloc[3])


class TestBuildAlgoDfHandlesGaps:
    def test_daily_resample_with_a_weekend_gap_preserves_the_real_return(self):
        analyzer = PerformanceAnalyzer(market=Market(), benchmark_symbols=[])
        base_snapshot = {
            "exchange_long_exposure": 0.0,
            "exchange_short_exposure": 0.0,
            "exchange_net_exposure": 0.0,
            "exchange_gross_exposure": 0.0,
            "portfolio_long_exposure": 0.0,
            "portfolio_short_exposure": 0.0,
            "portfolio_net_exposure": 0.0,
            "portfolio_gross_exposure": 0.0,
            "transactions": 0.0,
        }
        analyzer.raw_snapshots = {
            pd.Timestamp("2024-01-05"): {
                **base_snapshot,
                "ts": pd.Timestamp("2024-01-05"),
                "balance": 1000.0,
                "net_balance": 1000.0,
            },
            pd.Timestamp("2024-01-08"): {
                **base_snapshot,
                "ts": pd.Timestamp("2024-01-08"),
                "balance": 1100.0,
                "net_balance": 1100.0,
            },
        }

        algo_df = analyzer._build_algo_df()

        assert algo_df.loc[pd.Timestamp("2024-01-08"), "simple_returns"] == pytest.approx(0.10)
        assert algo_df.loc[pd.Timestamp("2024-01-08"), "cumulative_returns"] == pytest.approx(1.10)


class TestPeriodsPerYearAnnualization:
    def test_changing_periods_per_year_scales_sharpe_by_sqrt_ratio(self):
        # 3 days of hourly (flat-price -- balance is what moves, not price) candles,
        # since resample("D") needs data on multiple distinct calendar days to produce
        # more than one non-NaN daily return.
        candles = [{"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0}] * 72
        market = build_market({"BTC/USD": candles})
        exchange = make_exchange(market, max_leverage=1)
        exchange.transactions.add_deposit(asset="USD", volume=1000)

        analyzer_365 = PerformanceAnalyzer(
            market=market, exchange=exchange, benchmark_symbols=[], periods_per_year=365
        )
        analyzer_252 = PerformanceAnalyzer(
            market=market, exchange=exchange, benchmark_symbols=[], periods_per_year=252
        )
        analyzer_365.take_snapshot()
        analyzer_252.take_snapshot()
        for i in range(71):
            market.set_next_candle_as_current_market()
            exchange.run_step()
            # Bump balance once per day (hours 23, 47) so each day's real EOD balance
            # actually differs -- otherwise every return is flat 0% and Sharpe is
            # trivially 0 regardless of periods_per_year.
            if market.current["num"] % 24 == 0:
                exchange.balance.increase_asset_balance(asset="USD", volume=50.0 + i)
            analyzer_365.take_snapshot()
            analyzer_252.take_snapshot()

        analyzer_365.generate_report()
        analyzer_252.generate_report()

        sharpe_365 = analyzer_365.summary["algo"]["sharpe_ratio"]
        sharpe_252 = analyzer_252.summary["algo"]["sharpe_ratio"]
        assert sharpe_365 != 0
        assert sharpe_365 / sharpe_252 == pytest.approx((365 / 252) ** 0.5)

    def test_default_periods_per_year_is_365(self):
        market = build_market(
            {"BTC/USD": [{"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0}]}
        )
        analyzer = PerformanceAnalyzer(market=market, benchmark_symbols=[])
        assert analyzer.periods_per_year == 365


class TestDollarPnlFromEventLog:
    def test_realized_pnl_from_a_normal_close(self):
        market = build_market(
            {"BTC/USD": [{"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0}] * 2}
        )
        exchange = make_exchange(market, max_leverage=1)
        exchange.transactions.add_deposit(asset="USD", volume=10000)
        analyzer = PerformanceAnalyzer(market=market, exchange=exchange, benchmark_symbols=[])

        exchange.orders.create_order(
            symbol="BTC/USD",
            side=OrderSide.buy,
            execution_type=OrderExecutionType.market,
            volume=10,
        )
        position = exchange.positions.get_open_position_by_symbol("BTC/USD")
        market.set_next_candle_as_current_market()
        exchange.positions.close_position(position)

        pnls = analyzer._get_realized_pnls()
        assert pnls == pytest.approx([0.0])  # flat price, no PnL

    def test_liquidation_counts_as_a_loss_of_the_forfeited_margin(self):
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
        analyzer = PerformanceAnalyzer(market=market, exchange=exchange, benchmark_symbols=[])

        exchange.orders.create_order(
            symbol="BTC/USD",
            side=OrderSide.buy,
            execution_type=OrderExecutionType.market,
            volume=10,
        )
        market.set_next_candle_as_current_market()
        exchange.positions.refresh_open_positions()  # triggers liquidation

        pnls = analyzer._get_realized_pnls()
        assert pnls == pytest.approx([-200.0])  # -(10 * 100 / 5) margin forfeited

    def test_no_event_log_returns_empty(self):
        market = build_market(
            {"BTC/USD": [{"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0}]}
        )
        analyzer = PerformanceAnalyzer(market=market, benchmark_symbols=[])  # no exchange
        assert analyzer._get_realized_pnls() == []


class TestRMultipleAndDollarMetricsInSummary:
    def test_summary_includes_r_multiple_and_dollar_metrics(self):
        market = build_market(
            {"BTC/USD": [{"open": 100.0, "high": 100.0, "low": 100.0, "close": 120.0}] * 3}
        )
        exchange = make_exchange(market, max_leverage=1)
        exchange.transactions.add_deposit(asset="USD", volume=10000)
        analyzer = PerformanceAnalyzer(market=market, exchange=exchange, benchmark_symbols=[])
        analyzer.take_snapshot()
        market.set_next_candle_as_current_market()
        exchange.run_step()
        analyzer.take_snapshot()
        market.set_next_candle_as_current_market()
        exchange.run_step()
        analyzer.take_snapshot()

        trade = Trade()
        trade.symbol = "BTC/USD"
        trade.side = PositionSide.long
        trade.pnl = 0.2
        trade.result = TradeResult.winner
        trade.risk_percent = 0.1
        analyzer.trades = [trade]

        analyzer.generate_report()
        summary = analyzer.summary["algo"]

        assert summary["r_multiple_expectancy"] == pytest.approx(2.0)  # 0.2 / 0.1
        assert 0 <= summary["time_in_market_percent"] <= 100
        assert "ulcer_index" in summary
        assert "max_drawdown_duration_days" in summary
        assert "var_95_percent" in summary
        assert "cvar_95_percent" in summary
        assert "returns_skewness" in summary
        assert "returns_kurtosis" in summary
        assert "dollar_profit_factor" in summary
        assert "dollar_expectancy" in summary


class TestBetaCorrelationAlphaInSummary:
    def test_beta_correlation_alpha_keys_present_per_benchmark(self):
        candles = [
            {"open": 100.0 + i, "high": 100.0 + i, "low": 100.0 + i, "close": 100.0 + i}
            for i in range(5)
        ]
        market = build_market({"BTC/USD": candles})
        exchange = make_exchange(market, max_leverage=1)
        exchange.transactions.add_deposit(asset="USD", volume=1000)
        analyzer = PerformanceAnalyzer(
            market=market, exchange=exchange, benchmark_symbols=["BTC/USD"]
        )
        analyzer.take_snapshot()
        for _ in range(4):
            market.set_next_candle_as_current_market()
            exchange.run_step()
            analyzer.take_snapshot()

        analyzer.generate_report()
        summary = analyzer.summary["algo"]

        assert "beta_vs_BTC/USD" in summary
        assert "correlation_vs_BTC/USD" in summary
        assert "alpha_percent_vs_BTC/USD" in summary


class TestMergeReportsPropagatesNewFields:
    def test_periods_per_year_and_realized_pnls_are_combined(self):
        market1 = build_market(
            {"BTC/USD": [{"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0}] * 2}
        )
        market2 = build_market(
            {"BTC/USD": [{"open": 50.0, "high": 50.0, "low": 50.0, "close": 50.0}] * 2}
        )
        exchange1 = make_exchange(market1, max_leverage=1)
        exchange1.transactions.add_deposit(asset="USD", volume=10000)
        exchange2 = make_exchange(market2, max_leverage=1)
        exchange2.transactions.add_deposit(asset="USD", volume=10000)

        analyzer1 = PerformanceAnalyzer(
            market=market1, exchange=exchange1, benchmark_symbols=[], periods_per_year=252
        )
        analyzer2 = PerformanceAnalyzer(
            market=market2, exchange=exchange2, benchmark_symbols=[], periods_per_year=252
        )

        exchange1.orders.create_order(
            symbol="BTC/USD",
            side=OrderSide.buy,
            execution_type=OrderExecutionType.market,
            volume=10,
        )
        p1 = exchange1.positions.get_open_position_by_symbol("BTC/USD")
        market1.set_next_candle_as_current_market()
        exchange1.positions.close_position(p1)  # realized pnl 0 (flat price)
        analyzer1.take_snapshot()

        exchange2.orders.create_order(
            symbol="BTC/USD",
            side=OrderSide.buy,
            execution_type=OrderExecutionType.market,
            volume=10,
        )
        p2 = exchange2.positions.get_open_position_by_symbol("BTC/USD")
        market2.set_next_candle_as_current_market()
        exchange2.positions.close_position(p2)
        analyzer2.take_snapshot()

        merged = merge_reports([analyzer1, analyzer2])

        assert merged.periods_per_year == 252
        assert merged._get_realized_pnls() == pytest.approx([0.0, 0.0])
