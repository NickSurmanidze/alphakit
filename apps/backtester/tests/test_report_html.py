"""Unit tests for backtester.performance.report_html -- band functions asserted at
their documented thresholds, plus smoke tests that render_summary_html() produces the
expected description text and color for known-good/known-bad inputs."""

import pandas as pd
import pytest

from backtester.performance import report_html


class TestBandFunctions:
    def test_sharpe_bands(self):
        assert report_html._band_sharpe(-0.1) == "bad"
        assert report_html._band_sharpe(1.0) is None
        assert report_html._band_sharpe(2.0) == "good"
        assert report_html._band_sharpe(3.0) == "great"

    def test_calmar_bands(self):
        assert report_html._band_calmar(0.4) == "bad"
        assert report_html._band_calmar(0.9) is None
        assert report_html._band_calmar(1.0) == "good"
        assert report_html._band_calmar(3.0) == "great"

    def test_max_drawdown_percent_bands(self):
        assert report_html._band_max_drawdown_percent(-25) == "bad"
        assert report_html._band_max_drawdown_percent(-10) is None
        assert report_html._band_max_drawdown_percent(-3) == "good"

    def test_profit_factor_bands(self):
        assert report_html._band_profit_factor(0.8) == "bad"
        assert report_html._band_profit_factor(1.2) is None
        assert report_html._band_profit_factor(1.6) == "good"
        assert report_html._band_profit_factor(2.5) == "great"

    def test_r_multiple_expectancy_thin_positive_is_uncolored(self):
        assert report_html._band_r_multiple_expectancy(-0.01) == "bad"
        assert report_html._band_r_multiple_expectancy(0.007) is None
        assert report_html._band_r_multiple_expectancy(0.2) == "good"

    def test_closed_trades_bands(self):
        assert report_html._band_closed_trades(10) == "caution"
        assert report_html._band_closed_trades(66) is None
        assert report_html._band_closed_trades(150) == "good"


class TestBaseMetricId:
    def test_strips_vs_symbol_suffix(self):
        assert report_html._base_metric_id("beta_vs_MES/USD") == "beta"
        assert report_html._base_metric_id("alpha_percent_vs_MES/USD") == "alpha_percent"
        assert report_html._base_metric_id("sharpe_ratio") == "sharpe_ratio"


class TestRenderSummaryHtml:
    def _summary_df(self) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "algo": {
                    "sharpe_ratio": 3.5,
                    "annualized_volatility_percent": 5.0,
                    "returns_skewness": -0.2,
                    "closed_trades": 66.0,
                },
                "MES/USD": {
                    "sharpe_ratio": -1.0,
                    "annualized_volatility_percent": 15.0,
                    "returns_skewness": -0.1,
                    "closed_trades": float("nan"),
                },
            }
        )

    def test_includes_description_text(self):
        html = report_html.render_summary_html(self._summary_df())
        assert "return per unit of total volatility" in html.lower()

    def test_great_sharpe_colored_and_bad_sharpe_colored(self):
        html = report_html.render_summary_html(self._summary_df())
        assert report_html._GREAT in html
        assert report_html._BAD in html

    def test_lower_volatility_column_is_highlighted_without_absolute_band(self):
        html = report_html.render_summary_html(self._summary_df())
        # algo has the lower (better) volatility and no fixed band applies to it --
        # it should still get the comparative "best in row" treatment.
        assert report_html._GOOD in html

    def test_neutral_metric_has_no_color_at_all(self):
        # returns_skewness has no MetricSpec.direction/band -- neither algo's nor
        # MES/USD's cell should carry any band/highlight color.
        only_skew = pd.DataFrame({"algo": {"returns_skewness": -0.2}, "MES/USD": {"returns_skewness": -0.1}})
        html = report_html.render_summary_html(only_skew)
        for color in (report_html._GOOD, report_html._GREAT, report_html._BAD, report_html._CAUTION):
            assert color not in html

    def test_nan_renders_as_em_dash(self):
        html = report_html.render_summary_html(self._summary_df())
        assert "—" in html

    def test_integer_metric_has_no_decimal_point(self):
        html = report_html.render_summary_html(self._summary_df())
        assert ">66<" in html

    def test_empty_dataframe_does_not_raise(self):
        assert "<table" in report_html.render_summary_html(pd.DataFrame())
