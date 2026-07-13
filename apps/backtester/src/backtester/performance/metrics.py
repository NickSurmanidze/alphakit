"""Pure metric functions for backtesting performance analysis.

Each function is stateless and takes only primitive inputs (pd.Series of returns,
list[Trade]) so they can be used and tested independently of PerformanceAnalyzer.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from backtester.strategies import Trade, TradeResult

_PERIODS_PER_YEAR = 365  # crypto trades 365 days/year
_MIN_OBSERVATIONS_FOR_ALIGNMENT = 2  # beta/correlation/alpha need >=2 shared data points
_MIN_OBSERVATIONS_FOR_SKEW = 3  # pandas' own minimum for a defined skew
_MIN_OBSERVATIONS_FOR_KURTOSIS = 4  # pandas' own minimum for a defined kurtosis


def sharpe_ratio(
    returns: pd.Series,
    risk_free_rate: float = 0.0,
    periods_per_year: int = _PERIODS_PER_YEAR,
) -> float:
    """Risk-adjusted return: mean excess return divided by its standard deviation.

    Annualised with sqrt(periods_per_year). Higher is better; values above 1.0
    are generally considered good, above 2.0 excellent. Returns 0.0 when there
    is no return or no variability (e.g. flat equity curve).

    Args:
        returns: Per-period simple returns (e.g. daily pct_change series).
        risk_free_rate: Annual risk-free rate as a decimal (e.g. 0.05 for 5%).
        periods_per_year: Trading periods in a year (365 for crypto, 252 for equities).
    """
    excess = returns - risk_free_rate / periods_per_year
    std = float(excess.std())
    mean = float(excess.mean())
    if std == 0 or mean == 0:
        return 0.0
    return float(np.sqrt(periods_per_year) * mean / std)


def sortino_ratio(
    returns: pd.Series,
    risk_free_rate: float = 0.0,
    periods_per_year: int = _PERIODS_PER_YEAR,
) -> float:
    """Like Sharpe but penalises only downside volatility, not upside.

    Uses the root-mean-square of returns that fall below the target (downside
    deviation) instead of total standard deviation. Preferable to Sharpe when
    the return distribution is positively skewed. Returns 0.0 when there is no
    downside or no excess return.

    Args:
        returns: Per-period simple returns.
        risk_free_rate: Annual risk-free rate as a decimal.
        periods_per_year: Trading periods in a year.
    """
    excess = returns - risk_free_rate / periods_per_year
    # Downside deviation: RMS of returns that fall below the target.
    downside = np.minimum(excess, 0.0)
    downside_std = float(np.sqrt(np.mean(downside**2)))
    mean = float(excess.mean())
    if downside_std == 0 or mean == 0:
        return 0.0
    return float(np.sqrt(periods_per_year) * mean / downside_std)


def sharpe_lower_bound(
    returns: pd.Series,
    periods_per_year: int = _PERIODS_PER_YEAR,
    risk_free_rate: float = 0.0,
    z: float = 1.0,
) -> float:
    """A conservative (lower-confidence-bound) Sharpe estimate that penalizes small
    sample sizes -- Lo (2002)'s asymptotic standard error under an iid-returns
    assumption (ignoring the skew/kurtosis correction terms of the fuller Mertens 2002
    version): SE(SR) ~= sqrt((1 + SR^2/2) / n), n = len(returns).

    Ranking candidates by this instead of the raw point-estimate Sharpe stops a lucky
    small sample from outranking a larger one with a slightly lower Sharpe -- e.g. two
    parameter sets with near-identical Sharpe where one is backed by 4x the
    observations should not be treated as equivalent. `returns` need not be one row
    per calendar period: pass one row per closed trade (with `periods_per_year` set to
    the strategy's trades/year) to get a confidence bound driven by trade count
    specifically, rather than by how long the backtest window happens to be.

    Returns 0.0 for fewer than 2 observations.
    """
    clean = returns.dropna()
    n = len(clean)
    if n < _MIN_OBSERVATIONS_FOR_ALIGNMENT:
        return 0.0
    sr = sharpe_ratio(clean, risk_free_rate, periods_per_year)
    se = np.sqrt((1 + sr**2 / 2) / n) * np.sqrt(periods_per_year)
    return float(sr - z * se)


def annualized_volatility(
    returns: pd.Series,
    periods_per_year: int = _PERIODS_PER_YEAR,
) -> float:
    """Annualised standard deviation of returns (as a decimal, not percent).

    Measures the dispersion of returns over a year. A value of 0.20 means the
    strategy's annual returns typically vary by ±20%. Lower values indicate a
    smoother equity curve.

    Args:
        returns: Per-period simple returns.
        periods_per_year: Trading periods in a year.
    """
    return float(returns.std() * np.sqrt(periods_per_year))


def cagr(gross_cumulative_return: float, n_days: int) -> float:
    """Compound Annual Growth Rate — the constant annual return that produces the same end value.

    Normalises returns across different backtest lengths so strategies tested
    over different time spans are comparable. Returns 0.0 for degenerate inputs.

    Args:
        gross_cumulative_return: Terminal value as a multiple of initial (e.g. 2.5 for +150%).
        n_days: Calendar days covered by the backtest.
    """
    if n_days <= 0 or gross_cumulative_return <= 0:
        return 0.0
    return float(gross_cumulative_return ** (365.0 / n_days) - 1)


def calmar_ratio(annualized_return: float, max_drawdown: float) -> float:
    """CAGR divided by the magnitude of the maximum drawdown.

    Measures return earned per unit of worst-case peak-to-trough loss. A ratio
    above 1.0 means the annualised return exceeds the worst drawdown experienced.
    Returns 0.0 when max_drawdown is zero (no drawdown occurred).

    Args:
        annualized_return: CAGR as a decimal (e.g. 0.25 for 25% p.a.).
        max_drawdown: Max drawdown as a negative decimal (e.g. -0.30 for -30%).
    """
    if max_drawdown == 0:
        return 0.0
    return annualized_return / abs(max_drawdown)


def recovery_factor(net_return: float, max_drawdown: float) -> float:
    """Total net return divided by the magnitude of the maximum drawdown.

    Indicates how many times the strategy "earned back" its worst loss over the
    full backtest period. Higher values mean faster recovery relative to drawdown.
    Returns 0.0 when max_drawdown is zero.

    Args:
        net_return: Total net return as a decimal (e.g. 0.80 for +80%).
        max_drawdown: Max drawdown as a negative decimal (e.g. -0.20 for -20%).
    """
    if max_drawdown == 0:
        return 0.0
    return net_return / abs(max_drawdown)


def profit_factor(trades: list[Trade]) -> float:
    """Gross profit divided by gross loss across all closed trades.

    Values above 1.0 mean the strategy made more than it lost in aggregate.
    Returns 9999.0 when there are no losing trades (practically infinite),
    0.0 when there are no winning trades or no trades at all.
    """
    if not trades:
        return 0.0
    gross_profit = sum(t.pnl for t in trades if t.result == TradeResult.winner)
    gross_loss = sum(abs(t.pnl) for t in trades if t.result == TradeResult.loser)
    if gross_profit == 0 and gross_loss == 0:
        return 0.0
    if gross_loss == 0:
        return 9999.0
    if gross_profit == 0:
        return 0.0
    return gross_profit / gross_loss


def win_rate(trades: list[Trade]) -> float:
    """Fraction of closed trades that were profitable (0.0–1.0).

    Does not account for the size of wins vs losses — a strategy can be
    profitable with a win rate below 0.5 if winners are larger than losers
    (see avg_win_loss_ratio). Returns 0.0 for an empty trade list.
    """
    if not trades:
        return 0.0
    return sum(1 for t in trades if t.result == TradeResult.winner) / len(trades)


def avg_win_loss_ratio(trades: list[Trade]) -> float:
    """Average winner PnL divided by average loser PnL (both as positive magnitudes).

    A value of 2.0 means winners were on average twice as large as losers.
    Combined with win_rate, this determines whether a strategy has a positive
    expected value per trade. Returns 0.0 when there are no losing trades.
    """
    winner_pnls = [t.pnl for t in trades if t.result == TradeResult.winner]
    loser_pnls = [abs(t.pnl) for t in trades if t.result == TradeResult.loser]
    avg_win = sum(winner_pnls) / len(winner_pnls) if winner_pnls else 0.0
    avg_loss = sum(loser_pnls) / len(loser_pnls) if loser_pnls else 0.0
    if avg_loss == 0:
        return 0.0
    return avg_win / avg_loss


def max_consecutive_losses(trades: list[Trade]) -> int:
    """Longest unbroken streak of losing trades.

    Stress-tests psychological and capital resilience. A high number indicates
    the strategy can go through long losing runs even if it is overall profitable.
    """
    max_streak = current = 0
    for t in trades:
        if t.result == TradeResult.loser:
            current += 1
            max_streak = max(max_streak, current)
        else:
            current = 0
    return max_streak


def avg_holding_period_minutes(trades: list[Trade]) -> float:
    """Average time between trade open and close, in minutes.

    Only counts trades where holding_period was recorded. Returns 0.0 when no
    trade has a holding period (e.g. strategies that do not use close_trade()).
    """
    periods = [t.holding_period for t in trades if t.holding_period is not None]
    if not periods:
        return 0.0
    return sum(p.total_seconds() for p in periods) / len(periods) / 60


def r_multiple_expectancy(trades: list[Trade]) -> float:
    """Average PnL expressed in units of R (the fractional distance originally risked
    per trade, e.g. its stop-loss distance as a percent of entry price) -- lets trades
    with different stop widths be compared on equal footing, unlike raw percent PnL.

    Only counts trades that recorded a risk_percent (e.g. SL-enabled strategies).
    Returns 0.0 if none did.
    """
    r_multiples = [
        t.pnl / t.risk_percent for t in trades if t.risk_percent is not None and t.risk_percent != 0
    ]
    if not r_multiples:
        return 0.0
    return sum(r_multiples) / len(r_multiples)


def dollar_profit_factor(realized_pnls_in_usd: list[float]) -> float:
    """Same as profit_factor but on realized dollar PnL (e.g. from EventLog's
    PositionClosed/PositionReduced.realized_pnl_in_usd, plus liquidations' forfeited
    margin) instead of Trade.pnl's slippage/fee-free percent return -- accounts for
    actual position sizing, unlike the percent-based version.

    Returns 9999.0 when there are no losses (practically infinite), 0.0 when there
    are no gains or no data at all.
    """
    if not realized_pnls_in_usd:
        return 0.0
    gross_profit = sum(p for p in realized_pnls_in_usd if p > 0)
    gross_loss = sum(abs(p) for p in realized_pnls_in_usd if p < 0)
    if gross_profit == 0 and gross_loss == 0:
        return 0.0
    if gross_loss == 0:
        return 9999.0
    if gross_profit == 0:
        return 0.0
    return gross_profit / gross_loss


def dollar_expectancy(realized_pnls_in_usd: list[float]) -> float:
    """Average realized dollar PnL per closed/reduced/liquidated position fill.

    Returns 0.0 for an empty list.
    """
    if not realized_pnls_in_usd:
        return 0.0
    return sum(realized_pnls_in_usd) / len(realized_pnls_in_usd)


def time_in_market_percent(exposure: pd.Series) -> float:
    """Fraction of periods where gross exposure was nonzero (i.e. at least one
    position was open), as a percent. 100% means always in the market, 0% means a
    position was never opened.
    """
    if exposure.empty:
        return 0.0
    return float((exposure.abs() > 0).mean() * 100)


def value_at_risk(returns: pd.Series, confidence: float = 0.95) -> float:
    """Value at Risk: the return threshold that losses exceed only (1 - confidence) of
    the time, as a negative decimal (e.g. -0.05 for a 5% VaR at 95% confidence).

    Returns 0.0 for an empty series.
    """
    clean = returns.dropna()
    if clean.empty:
        return 0.0
    return float(np.percentile(clean, (1 - confidence) * 100))


def conditional_value_at_risk(returns: pd.Series, confidence: float = 0.95) -> float:
    """Conditional VaR / Expected Shortfall: the average return in the tail beyond
    VaR -- the expected loss GIVEN that a VaR-level loss has already occurred. More
    informative than VaR alone since it captures tail severity, not just the
    threshold.

    Returns 0.0 for an empty series.
    """
    clean = returns.dropna()
    if clean.empty:
        return 0.0
    var = value_at_risk(clean, confidence)
    tail = clean[clean <= var]
    if tail.empty:
        return var
    return float(tail.mean())


def returns_skewness(returns: pd.Series) -> float:
    """Skewness of the return distribution: negative means a longer left tail (large
    losses more extreme than large gains), positive means the opposite. Sharpe/Sortino
    implicitly assume near-zero skew; a strongly skewed distribution makes those ratios
    less reliable on their own.

    Returns 0.0 for fewer than 3 observations or zero variance (pandas' own
    requirement/degenerate case for a defined skew).
    """
    clean = returns.dropna()
    if len(clean) < _MIN_OBSERVATIONS_FOR_SKEW or clean.std() == 0:
        return 0.0
    return float(clean.skew())  # type: ignore[arg-type]  # pandas-stubs return type too broad


def returns_kurtosis(returns: pd.Series) -> float:
    """Excess kurtosis of the return distribution (0 = normal distribution). Positive
    means fatter tails / more extreme outliers than a normal distribution, which is
    common for strategies with capped upside (e.g. take-profit orders) or uncapped
    downside.

    Returns 0.0 for fewer than 4 observations or zero variance (pandas' own
    requirement/degenerate case for a defined kurtosis).
    """
    clean = returns.dropna()
    if len(clean) < _MIN_OBSERVATIONS_FOR_KURTOSIS or clean.std() == 0:
        return 0.0
    return float(clean.kurtosis())  # type: ignore[arg-type]  # pandas-stubs return type too broad


def ulcer_index(cumulative_returns: pd.Series) -> float:
    """Root-mean-square of percentage drawdowns from the running high-water mark --
    unlike max drawdown, this penalizes both the DEPTH and DURATION of every
    drawdown, not just the single worst trough. Lower is better; 0 means the equity
    curve never fell below its high-water mark.

    Returns 0.0 for an empty series.
    """
    clean = cumulative_returns.dropna()
    if clean.empty:
        return 0.0
    high_watermark = clean.cummax()
    drawdown_percent = (clean - high_watermark) / high_watermark * 100
    return float(np.sqrt((drawdown_percent**2).mean()))


def max_drawdown_duration_days(cumulative_returns: pd.Series) -> int:
    """Longest unbroken stretch (in resampled periods -- days, given this package
    always resamples to daily) the equity curve spent below a prior high-water mark
    before reaching a new one, i.e. "time under water". A large max drawdown that
    recovers in a week reads very differently from one that takes a year, but
    max_drawdown_percent alone can't tell them apart.

    Returns 0 for an empty series.
    """
    clean = cumulative_returns.dropna()
    if clean.empty:
        return 0
    high_watermark = clean.cummax()
    underwater = clean < high_watermark
    max_streak = current = 0
    for is_underwater in underwater:
        if is_underwater:
            current += 1
            max_streak = max(max_streak, current)
        else:
            current = 0
    return max_streak


def _align(returns: pd.Series, benchmark_returns: pd.Series) -> pd.DataFrame:
    """Inner-joins two return series on their shared index and drops any row where
    either side is NaN -- the common alignment step beta/correlation/alpha all need
    before comparing two return series pointwise."""
    return pd.concat([returns, benchmark_returns], axis=1, join="inner").dropna()


def beta(returns: pd.Series, benchmark_returns: pd.Series) -> float:
    """OLS beta of `returns` against `benchmark_returns`: covariance divided by
    benchmark variance, on their shared (aligned) index. A beta of 1.0 means the
    strategy moves in lockstep with the benchmark; 0 means no linear relationship.

    Returns 0.0 with fewer than 2 overlapping observations or zero benchmark
    variance.
    """
    aligned = _align(returns, benchmark_returns)
    if len(aligned) < _MIN_OBSERVATIONS_FOR_ALIGNMENT:
        return 0.0
    r, b = aligned.iloc[:, 0], aligned.iloc[:, 1]
    benchmark_variance = float(b.var())
    if benchmark_variance == 0:
        return 0.0
    return float(r.cov(b) / benchmark_variance)


def correlation(returns: pd.Series, benchmark_returns: pd.Series) -> float:
    """Pearson correlation of `returns` against `benchmark_returns` on their shared
    (aligned) index, from -1 (perfectly inverse) to 1 (perfectly in sync).

    Returns 0.0 with fewer than 2 overlapping observations.
    """
    aligned = _align(returns, benchmark_returns)
    if len(aligned) < _MIN_OBSERVATIONS_FOR_ALIGNMENT:
        return 0.0
    return float(aligned.iloc[:, 0].corr(aligned.iloc[:, 1]))


def alpha(
    returns: pd.Series,
    benchmark_returns: pd.Series,
    beta_value: float,
    periods_per_year: int = _PERIODS_PER_YEAR,
) -> float:
    """Annualized excess return not explained by benchmark exposure (simple
    CAPM-style): mean(algo) - beta * mean(benchmark), on their shared (aligned)
    index, annualized by periods_per_year. Positive alpha means the strategy
    outperformed what its benchmark exposure alone would predict.

    Returns 0.0 with fewer than 2 overlapping observations.
    """
    aligned = _align(returns, benchmark_returns)
    if len(aligned) < _MIN_OBSERVATIONS_FOR_ALIGNMENT:
        return 0.0
    r, b = aligned.iloc[:, 0], aligned.iloc[:, 1]
    per_period_alpha = float(r.mean()) - beta_value * float(b.mean())
    return per_period_alpha * periods_per_year
