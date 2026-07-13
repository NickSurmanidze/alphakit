"""Hand-verified unit tests for the pure metric functions in performance/metrics.py --
each one computed by hand and asserted exactly (or via pytest.approx for float math),
not just "didn't crash"."""

import numpy as np
import pandas as pd
import pytest

from backtester.performance import metrics
from backtester.strategies import Trade, TradeResult


def _trade(pnl: float, result: TradeResult, risk_percent: float | None = None) -> Trade:
    t = Trade()
    t.pnl = pnl
    t.result = result
    t.risk_percent = risk_percent
    return t


class TestRMultipleExpectancy:
    def test_averages_pnl_over_risk_percent(self):
        # +10% on a 5% risk -> 2R; -4% on a 2% risk -> -2R. Average = 0R.
        trades = [
            _trade(0.10, TradeResult.winner, risk_percent=0.05),
            _trade(-0.04, TradeResult.loser, risk_percent=0.02),
        ]
        assert metrics.r_multiple_expectancy(trades) == pytest.approx(0.0)

    def test_ignores_trades_without_risk_percent(self):
        trades = [
            _trade(0.10, TradeResult.winner, risk_percent=0.05),  # 2R
            _trade(0.50, TradeResult.winner, risk_percent=None),  # excluded
        ]
        assert metrics.r_multiple_expectancy(trades) == pytest.approx(2.0)

    def test_empty_or_all_missing_risk_percent_returns_zero(self):
        assert metrics.r_multiple_expectancy([]) == 0.0
        assert metrics.r_multiple_expectancy([_trade(0.1, TradeResult.winner)]) == 0.0


class TestDollarProfitFactorAndExpectancy:
    def test_profit_factor_ratio(self):
        assert metrics.dollar_profit_factor([100.0, 50.0, -75.0]) == pytest.approx(150 / 75)

    def test_profit_factor_no_losses_is_practically_infinite(self):
        assert metrics.dollar_profit_factor([100.0, 50.0]) == 9999.0

    def test_profit_factor_no_data_or_no_gains_and_no_losses(self):
        assert metrics.dollar_profit_factor([]) == 0.0
        assert metrics.dollar_profit_factor([0.0, 0.0]) == 0.0

    def test_expectancy_is_mean(self):
        assert metrics.dollar_expectancy([100.0, -50.0, 25.0]) == pytest.approx(25.0)
        assert metrics.dollar_expectancy([]) == 0.0


class TestSharpeLowerBound:
    def test_lower_bound_is_below_point_estimate_sharpe(self):
        rng = np.random.default_rng(7)
        returns = pd.Series(rng.normal(0.001, 0.01, 250))
        sr = metrics.sharpe_ratio(returns, periods_per_year=250)
        sr_lb = metrics.sharpe_lower_bound(returns, periods_per_year=250)
        assert sr_lb < sr

    def test_smaller_sample_gives_wider_penalty_at_equal_sharpe(self):
        # Same mean/std (so identical point-estimate Sharpe), but one series is 4x
        # longer -- its lower bound should sit closer to the point estimate.
        short = pd.Series([0.01, -0.005] * 15)  # n=30
        long = pd.Series([0.01, -0.005] * 60)  # n=120
        sr_short = metrics.sharpe_ratio(short, periods_per_year=252)
        sr_long = metrics.sharpe_ratio(long, periods_per_year=252)
        # not bit-identical -- pandas' ddof=1 sample std carries a slightly different
        # small-sample bias at n=30 vs n=120 -- but close enough that the comparison
        # below isolates the lower-bound penalty, not a point-estimate difference.
        assert sr_short == pytest.approx(sr_long, rel=0.02)

        gap_short = sr_short - metrics.sharpe_lower_bound(short, periods_per_year=252)
        gap_long = sr_long - metrics.sharpe_lower_bound(long, periods_per_year=252)
        assert gap_short > gap_long

    def test_fewer_than_two_observations_returns_zero(self):
        assert metrics.sharpe_lower_bound(pd.Series([], dtype=float)) == 0.0
        assert metrics.sharpe_lower_bound(pd.Series([0.01])) == 0.0


class TestTimeInMarketPercent:
    def test_fraction_of_nonzero_exposure_periods(self):
        exposure = pd.Series([0.0, 0.5, 0.5, 0.0, 1.0])
        assert metrics.time_in_market_percent(exposure) == pytest.approx(60.0)

    def test_empty_series_returns_zero(self):
        assert metrics.time_in_market_percent(pd.Series([], dtype=float)) == 0.0


class TestValueAtRiskAndCVaR:
    def test_var_is_the_percentile_threshold(self):
        # 100 evenly spaced returns from -0.99 to 0.0 in steps of 0.01 -- 5th
        # percentile (lowest 5%) sits at index 4 (0-indexed) -> -0.95.
        returns = pd.Series(np.linspace(-0.99, 0.0, 100))
        var = metrics.value_at_risk(returns, confidence=0.95)
        assert var == pytest.approx(-0.95, abs=0.01)

    def test_cvar_is_more_negative_than_or_equal_to_var(self):
        returns = pd.Series(np.linspace(-0.99, 0.0, 100))
        var = metrics.value_at_risk(returns, confidence=0.95)
        cvar = metrics.conditional_value_at_risk(returns, confidence=0.95)
        assert cvar <= var

    def test_empty_series_returns_zero(self):
        empty = pd.Series([], dtype=float)
        assert metrics.value_at_risk(empty) == 0.0
        assert metrics.conditional_value_at_risk(empty) == 0.0


class TestSkewnessAndKurtosis:
    def test_symmetric_returns_have_near_zero_skew(self):
        returns = pd.Series([-0.02, -0.01, 0.0, 0.01, 0.02])
        assert metrics.returns_skewness(returns) == pytest.approx(0.0, abs=1e-9)

    def test_right_skewed_returns_have_positive_skew(self):
        # A few large positive outliers among small negative returns.
        returns = pd.Series([-0.01, -0.01, -0.01, -0.01, 0.10])
        assert metrics.returns_skewness(returns) > 0

    def test_too_few_observations_returns_zero(self):
        assert metrics.returns_skewness(pd.Series([0.01, 0.02])) == 0.0
        assert metrics.returns_kurtosis(pd.Series([0.01, 0.02, 0.03])) == 0.0

    def test_zero_variance_returns_zero(self):
        flat = pd.Series([0.01, 0.01, 0.01, 0.01])
        assert metrics.returns_skewness(flat) == 0.0
        assert metrics.returns_kurtosis(flat) == 0.0


class TestUlcerIndexAndDrawdownDuration:
    def test_ulcer_index_zero_when_always_at_high_water_mark(self):
        cum = pd.Series([1.0, 1.1, 1.2, 1.3])
        assert metrics.ulcer_index(cum) == pytest.approx(0.0)

    def test_ulcer_index_positive_when_underwater(self):
        cum = pd.Series([1.0, 1.2, 0.9, 1.1])
        assert metrics.ulcer_index(cum) > 0

    def test_drawdown_duration_counts_longest_underwater_streak(self):
        # New high at 1.2 (day 1), underwater for days 2-4 (3 days), new high at day 5.
        cum = pd.Series([1.0, 1.2, 1.1, 1.15, 1.19, 1.25])
        assert metrics.max_drawdown_duration_days(cum) == 3

    def test_empty_series(self):
        empty = pd.Series([], dtype=float)
        assert metrics.ulcer_index(empty) == 0.0
        assert metrics.max_drawdown_duration_days(empty) == 0


class TestBetaCorrelationAlpha:
    def test_beta_one_for_identical_series(self):
        idx = pd.date_range("2024-01-01", periods=5, freq="D")
        returns = pd.Series([0.01, -0.02, 0.03, 0.0, 0.01], index=idx)
        assert metrics.beta(returns, returns) == pytest.approx(1.0)
        assert metrics.correlation(returns, returns) == pytest.approx(1.0)

    def test_beta_zero_for_uncorrelated_constant_benchmark(self):
        idx = pd.date_range("2024-01-01", periods=5, freq="D")
        returns = pd.Series([0.01, -0.02, 0.03, 0.0, 0.01], index=idx)
        benchmark = pd.Series([0.0, 0.0, 0.0, 0.0, 0.0], index=idx)
        assert metrics.beta(returns, benchmark) == 0.0  # zero benchmark variance

    def test_alpha_zero_when_returns_equal_beta_times_benchmark(self):
        idx = pd.date_range("2024-01-01", periods=5, freq="D")
        benchmark = pd.Series([0.01, -0.02, 0.03, 0.0, 0.01], index=idx)
        returns = benchmark * 2  # beta should be ~2, alpha should be ~0
        b = metrics.beta(returns, benchmark)
        assert b == pytest.approx(2.0)
        a = metrics.alpha(returns, benchmark, b, periods_per_year=365)
        assert a == pytest.approx(0.0, abs=1e-9)

    def test_too_few_overlapping_observations_returns_zero(self):
        idx = pd.date_range("2024-01-01", periods=1, freq="D")
        returns = pd.Series([0.01], index=idx)
        assert metrics.beta(returns, returns) == 0.0
        assert metrics.correlation(returns, returns) == 0.0
        assert metrics.alpha(returns, returns, 1.0) == 0.0

    def test_misaligned_index_only_uses_shared_dates(self):
        returns = pd.Series(
            [0.01, 0.02, 0.03], index=pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03"])
        )
        benchmark = pd.Series(
            [0.01, 0.02, 0.03], index=pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"])
        )
        # Only 2024-01-02/03 overlap -- still enough for a defined beta.
        assert metrics.beta(returns, benchmark) == pytest.approx(1.0)


class TestPeriodsPerYearAffectsAnnualization:
    def test_sharpe_scales_with_sqrt_periods_per_year(self):
        returns = pd.Series([0.01, -0.005, 0.008, 0.002, -0.001])
        sharpe_365 = metrics.sharpe_ratio(returns, periods_per_year=365)
        sharpe_252 = metrics.sharpe_ratio(returns, periods_per_year=252)
        assert sharpe_365 / sharpe_252 == pytest.approx((365 / 252) ** 0.5)

    def test_volatility_scales_with_sqrt_periods_per_year(self):
        returns = pd.Series([0.01, -0.005, 0.008, 0.002, -0.001])
        vol_365 = metrics.annualized_volatility(returns, periods_per_year=365)
        vol_252 = metrics.annualized_volatility(returns, periods_per_year=252)
        assert vol_365 / vol_252 == pytest.approx((365 / 252) ** 0.5)
