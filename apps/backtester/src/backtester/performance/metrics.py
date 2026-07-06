"""Pure metric functions for backtesting performance analysis.

Each function is stateless and takes only primitive inputs (pd.Series of returns,
list[Trade]) so they can be used and tested independently of PerformanceAnalyzer.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from backtester.strategies import Trade, TradeResult

_PERIODS_PER_YEAR = 365  # crypto trades 365 days/year


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
