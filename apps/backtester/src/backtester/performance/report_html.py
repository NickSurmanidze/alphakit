"""HTML rendering for PerformanceAnalyzer.summary_dataframe().

Adds a plain-English description column and color-codes cells so a summary table
reads at a glance instead of requiring every metric name to be looked up elsewhere:
absolute quality bands (e.g. Sharpe >3 = green) where a fixed threshold is actually
meaningful, and a bold/bordered "best in row" highlight across columns (e.g. algo vs.
a benchmark) for metrics with a known better-direction but no universal fixed band
(e.g. lower volatility is better, but there's no fixed number that makes a given
volatility "good"). Metrics with neither a band nor a direction (skew, kurtosis, beta,
correlation, ...) render as plain, uncolored cells -- intentionally, since no
universal good/bad judgment applies to them (see each MetricSpec.description).

Kept separate from analyzer.py: this is a notebook-display concern, not a metrics
concern, and `backtester` has no hard IPython dependency outside the notebook/dev
environment, so this module returns a plain HTML string rather than an
IPython.display.HTML object -- wrap the return value yourself in a notebook.
"""

from __future__ import annotations

import html
import re
from collections.abc import Callable
from dataclasses import dataclass

import pandas as pd

_GOOD = "#c6f6c6"  # soft green
_GREAT = "#4caf50"  # deeper green -- paired with white text
_BAD = "#f4a3a3"  # soft red
_CAUTION = "#ffe4a3"  # soft amber
_BAND_COLORS = {"good": _GOOD, "great": _GREAT, "bad": _BAD, "caution": _CAUTION}
_WINNER_BORDER = "border:2px solid #2e7d32;font-weight:600;"
_CELL_STYLE = "border:1px solid #ddd;padding:4px 10px;"

# metric ids formatted as whole numbers (trade/day counts) rather than 4-decimal floats
_INTEGER_METRICS = {
    "max_drawdown_duration_days",
    "max_consecutive_losses",
    "closed_trades",
    "winner_trades",
    "loser_trades",
}

_VS_SYMBOL_RE = re.compile(r"^(beta|correlation|alpha_percent)_vs_.+$")


def _band_sharpe_like(value: float, great_at: float, good_at: float) -> str | None:
    if value < 0:
        return "bad"
    if value >= great_at:
        return "great"
    if value >= good_at:
        return "good"
    return None


def _band_sharpe(value: float) -> str | None:
    return _band_sharpe_like(value, great_at=3, good_at=2)


def _band_sortino(value: float) -> str | None:
    return _band_sharpe_like(value, great_at=3, good_at=2)


def _band_calmar(value: float) -> str | None:
    if value < 0.5:
        return "bad"
    if value >= 3:
        return "great"
    if value >= 1:
        return "good"
    return None


def _band_recovery_factor(value: float) -> str | None:
    if value < 1:
        return "bad"
    return "good" if value >= 2 else None


def _band_max_drawdown_percent(value: float) -> str | None:
    if value < -20:
        return "bad"
    return "good" if value >= -5 else None


def _band_profit_factor(value: float) -> str | None:
    if value < 1:
        return "bad"
    if value >= 2:
        return "great"
    return "good" if value >= 1.5 else None


def _band_positive_is_good(value: float) -> str | None:
    return "good" if value > 0 else "bad"


def _band_win_loss_ratio(value: float) -> str | None:
    return "good" if value >= 1 else "bad"


def _band_r_multiple_expectancy(value: float) -> str | None:
    if value <= 0:
        return "bad"
    return "good" if value >= 0.1 else None  # positive but thin, per its own description


def _band_closed_trades(value: float) -> str | None:
    if value < 30:
        return "caution"
    return "good" if value >= 100 else None


@dataclass(frozen=True)
class MetricSpec:
    description: str
    # "higher_better"/"lower_better" enables the cross-column "best in row" highlight;
    # None means no direction is meaningful (e.g. skew, beta) -- no highlight at all.
    direction: str | None = None
    # value -> "good"/"great"/"bad"/"caution"/None; None means no absolute judgment
    # applies (e.g. volatility has no universally "good" number) -- comparative
    # highlighting (if `direction` is set) is still shown for these.
    band: Callable[[float], str | None] | None = None


METRIC_SPECS: dict[str, MetricSpec] = {
    "sharpe_ratio": MetricSpec(
        "Return per unit of total volatility. <0 bad, ~1 decent, >2 great, >3 excellent.",
        "higher_better",
        _band_sharpe,
    ),
    "sortino_ratio": MetricSpec(
        "Like Sharpe but only penalizes downside volatility (upside swings aren't "
        '"risk"). >2 good, >3 excellent.',
        "higher_better",
        _band_sortino,
    ),
    "calmar_ratio": MetricSpec(
        "CAGR ÷ max drawdown. >1 decent, >3 excellent, <0.5 weak.",
        "higher_better",
        _band_calmar,
    ),
    "recovery_factor": MetricSpec(
        "Total net profit ÷ max drawdown. >2 solid.",
        "higher_better",
        _band_recovery_factor,
    ),
    "annualized_volatility_percent": MetricSpec(
        "How much the equity curve swings per year. Lower = smoother.",
        "lower_better",
    ),
    "var_95_percent": MetricSpec(
        'The daily loss you shouldn\'t exceed on a "normal" bad day (worst of the '
        "best 95%). Closer to 0 is better.",
        "higher_better",
    ),
    "cvar_95_percent": MetricSpec(
        "Expected shortfall: average loss on the worst 5% of days. Closer to 0 is better.",
        "higher_better",
    ),
    "ulcer_index": MetricSpec(
        "Combines depth and duration of drawdowns into one number. Lower is better "
        '(less "ulcer-inducing").',
        "lower_better",
    ),
    "max_drawdown_percent": MetricSpec(
        "Largest peak-to-trough loss. Closer to 0 better; below -20% starts getting "
        "painful for most investors.",
        "higher_better",
        _band_max_drawdown_percent,
    ),
    "max_drawdown_duration_days": MetricSpec(
        "How long it took to recover the loss. Shorter is better.",
        "lower_better",
    ),
    "returns_skewness": MetricSpec(
        "Asymmetry of daily returns. Positive = occasional big wins; negative = "
        "occasional big losses (crash risk)."
    ),
    "returns_kurtosis": MetricSpec(
        '"Fat-tailedness" -- how often extreme days happen vs. a normal distribution. '
        "Higher = more extreme surprises (good or bad)."
    ),
    "cagr_percent": MetricSpec(
        "Compound annual growth rate, the smoothed annual growth rate. Higher is "
        "generally better, but always weigh against volatility/drawdown.",
        "higher_better",
    ),
    "gross_return_percent": MetricSpec(
        "Ending NAV as a percent of starting capital (100 + net_return_percent) -- "
        'not "return before fees".'
    ),
    "net_return_percent": MetricSpec(
        "Your actual gain over the backtest window.",
        "higher_better",
    ),
    "profit_factor": MetricSpec(
        "Gross wins ÷ gross losses. >1.5 decent, >2 great, <1 losing money.",
        "higher_better",
        _band_profit_factor,
    ),
    "dollar_profit_factor": MetricSpec(
        "Same as profit factor, in $ terms.",
        "higher_better",
        _band_profit_factor,
    ),
    "dollar_expectancy": MetricSpec(
        "Average $ profit per trade. Positive and comfortably above costs is what you want.",
        "higher_better",
        _band_positive_is_good,
    ),
    "win_rate_percent": MetricSpec(
        "Fraction of trades that were profitable. No universal \"good\" number -- a "
        "40% win rate can be very profitable with a strong win/loss ratio, and vice versa."
    ),
    "avg_win_loss_ratio": MetricSpec(
        "Average winning trade size ÷ average losing trade size.",
        "higher_better",
        _band_win_loss_ratio,
    ),
    "r_multiple_expectancy": MetricSpec(
        "Expected profit per unit of risk taken, per trade. Positive is good, but a "
        "small positive number means a thin edge that likely relies on volume/"
        "frequency rather than big per-trade wins.",
        "higher_better",
        _band_r_multiple_expectancy,
    ),
    "max_consecutive_losses": MetricSpec(
        "Longest losing streak. Lower is easier to stomach and reduces risk of ruin.",
        "lower_better",
    ),
    "avg_holding_period_min": MetricSpec(
        "Average minutes between trade open and close. Not good/bad, just describes "
        "trading style (swing vs. scalping)."
    ),
    "time_in_market_percent": MetricSpec(
        "How much of the time capital is actually deployed. Lower can mean better "
        "capital efficiency (same return, less exposure)."
    ),
    "closed_trades": MetricSpec(
        "Total closed trades -- sample-size context for every ratio above it. Fewer "
        "than ~30 makes those ratios much less trustworthy.",
        "higher_better",
        _band_closed_trades,
    ),
    "winner_trades": MetricSpec("Number of closed trades that were profitable."),
    "loser_trades": MetricSpec("Number of closed trades that were losses."),
    "beta": MetricSpec(
        "Fraction of the benchmark's moves the algo captures. Low beta is good if "
        "returns are also positive -- means the strategy isn't just repackaging "
        "market exposure."
    ),
    "correlation": MetricSpec(
        "Pearson correlation with the benchmark's returns, from -1 to 1."
    ),
    "alpha_percent": MetricSpec(
        "Annualized excess return not explained by beta exposure to the benchmark. "
        "Positive alpha means genuine outperformance -- the number that most directly "
        'answers "is this actually adding value."',
        "higher_better",
        _band_positive_is_good,
    ),
}


def _base_metric_id(metric_name: str) -> str:
    """Strips the "_vs_{symbol}" suffix off beta/correlation/alpha_percent metric
    names (the symbol varies per benchmark) so they resolve to one shared MetricSpec."""
    match = _VS_SYMBOL_RE.match(metric_name)
    return match.group(1) if match else metric_name


# Row-label-only (display concern, not a metrics concern) -- an emoji + plain-English
# name per metric id so the table reads at a glance without decoding snake_case.
# Keyed by the same base metric id as METRIC_SPECS; falls back to the raw metric_name
# for anything not yet mapped here (e.g. a newly added metric).
_DISPLAY_NAMES: dict[str, str] = {
    "gross_return_percent": "🏦 Gross Return %",
    "net_return_percent": "💰 Net Return %",
    "max_drawdown_percent": "📉 Max Drawdown %",
    "max_drawdown_duration_days": "⏱️ Max Drawdown Duration (days)",
    "sharpe_ratio": "📊 Sharpe Ratio",
    "sortino_ratio": "📊 Sortino Ratio",
    "annualized_volatility_percent": "🌪️ Annualized Volatility %",
    "cagr_percent": "📈 CAGR %",
    "calmar_ratio": "📐 Calmar Ratio",
    "recovery_factor": "🔁 Recovery Factor",
    "ulcer_index": "🤢 Ulcer Index",
    "var_95_percent": "⚠️ VaR (95%)",
    "cvar_95_percent": "⚠️ CVaR (95%)",
    "returns_skewness": "↔️ Returns Skewness",
    "returns_kurtosis": "🎯 Returns Kurtosis",
    "profit_factor": "⚖️ Profit Factor",
    "dollar_profit_factor": "💵 Dollar Profit Factor",
    "dollar_expectancy": "💵 Dollar Expectancy",
    "win_rate_percent": "🎯 Win Rate %",
    "avg_win_loss_ratio": "⚖️ Avg Win/Loss Ratio",
    "r_multiple_expectancy": "🎲 R-Multiple Expectancy",
    "max_consecutive_losses": "🔻 Max Consecutive Losses",
    "avg_holding_period_min": "⏳ Avg Holding Period (min)",
    "time_in_market_percent": "🕒 Time in Market %",
    "closed_trades": "🔢 Closed Trades",
    "winner_trades": "✅ Winner Trades",
    "loser_trades": "❌ Loser Trades",
    "beta": "📡 Beta",
    "correlation": "🔗 Correlation",
    "alpha_percent": "🚀 Alpha %",
}


def _display_label(metric_name: str) -> str:
    """Emoji + plain-English row label for `metric_name`. Preserves a "_vs_{symbol}"
    suffix (which benchmark a beta/correlation/alpha row is against) as " vs {symbol}"
    rather than dropping it -- that part is meaningful, unlike the base metric id."""
    base_id = _base_metric_id(metric_name)
    label = _DISPLAY_NAMES.get(base_id, metric_name)
    match = _VS_SYMBOL_RE.match(metric_name)
    if match:
        label += f" vs {match.group(0)[len(base_id) + len('_vs_'):]}"
    return label


def _format_value(metric_name: str, value: float) -> str:
    if pd.isna(value):
        return "—"
    if _base_metric_id(metric_name) in _INTEGER_METRICS:
        return f"{int(value)}"
    return f"{value:,.4f}"


def render_summary_html(df: pd.DataFrame) -> str:
    """Renders a PerformanceAnalyzer.summary_dataframe() (one row per metric, one
    column per key/benchmark symbol) as a self-contained HTML table: a Description
    column, absolute-quality background colors where a fixed band is meaningful, and
    a bold/bordered highlight on whichever column is the best value in a row
    (wherever a better-direction is known, even without a fixed band).

    Returns a raw HTML string -- wrap in `IPython.display.HTML(...)` to render.
    """
    value_columns = list(df.columns)
    body_rows = []

    for metric_name, row in df.iterrows():
        spec = METRIC_SPECS.get(_base_metric_id(str(metric_name)))
        description = html.escape(spec.description) if spec else ""
        direction = spec.direction if spec else None
        band_fn = spec.band if spec else None

        numeric_values = {col: row[col] for col in value_columns if pd.notna(row[col])}
        best_col = None
        if direction and len(numeric_values) >= 2:
            best_col = (max if direction == "higher_better" else min)(
                numeric_values, key=lambda c: numeric_values[c]
            )

        cells = []
        for col in value_columns:
            value = row[col]
            style = _CELL_STYLE + "text-align:right;"
            if pd.notna(value):
                band = band_fn(value) if band_fn else None
                if band:
                    style += f"background-color:{_BAND_COLORS[band]};"
                    # Pastel bands (good/bad/caution) need dark text forced -- an
                    # inherited color (e.g. a notebook's light-gray default) can be
                    # unreadable against them. "great" uses a deeper, saturated
                    # green, so it needs white instead.
                    style += "color:#ffffff;" if band == "great" else "color:#1a1a1a;"
                if col == best_col:
                    style += _WINNER_BORDER
                    if not band:
                        style += f"background-color:{_GOOD};color:#1a1a1a;"
            cells.append(f'<td style="{style}">{_format_value(str(metric_name), value)}</td>')

        cells.append(
            f'<td style="{_CELL_STYLE}text-align:left;color:#555;max-width:420px;">'
            f"{description}</td>"
        )
        body_rows.append(
            f'<tr><th style="{_CELL_STYLE}text-align:left;">{html.escape(_display_label(str(metric_name)))}</th>'
            f"{''.join(cells)}</tr>"
        )

    header_cells = "".join(
        f'<th style="{_CELL_STYLE}">{html.escape(str(col))}</th>' for col in value_columns
    )
    header = (
        f'<tr><th style="{_CELL_STYLE}text-align:left;">metric</th>{header_cells}'
        f'<th style="{_CELL_STYLE}text-align:left;">description</th></tr>'
    )

    return (
        '<table style="border-collapse:collapse;font-family:sans-serif;font-size:13px;">'
        f"<thead>{header}</thead><tbody>{''.join(body_rows)}</tbody></table>"
    )
