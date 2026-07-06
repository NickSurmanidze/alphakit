# `backtester.performance`

Performance reporting sub-package for the backtester. Tracks portfolio equity through a backtest, computes standard financial metrics, and renders interactive Plotly reports.

## Package layout

| File | Purpose |
|---|---|
| `analyzer.py` | `PerformanceAnalyzer` — snapshot collection, DataFrame builders, report generation, chart output |
| `metrics.py` | Pure stateless metric functions (no side effects, easily unit-tested) |
| `charts.py` | `make_report_figure()` — builds the Plotly figure from pre-computed data |

---

## Quick start

```python
from backtester.performance import PerformanceAnalyzer

reporter = PerformanceAnalyzer(
    market=market,
    benchmark_symbols=["BTC/USD"],
    exchange=exchange,
    risk_free_rate=0.05,   # 5% annual, default 0.0
    key="my_strategy",     # prefix for summary dict keys, default "algo"
)

# called once per candle inside the backtest loop
reporter.take_snapshot()

# after the loop
reporter.trades = strategy.trade_history
reporter.generate_report()         # populates reporter.summary
reporter.show_plot()               # opens Plotly in browser
reporter.save_plot("report.html")  # saves to file
```

---

## `PerformanceAnalyzer` API

### Constructor

```python
PerformanceAnalyzer(
    market: Market,
    benchmark_symbols: list[str],
    exchange: Exchange | None = None,
    portfolio: Portfolio | None = None,
    key: str = "algo",
    risk_free_rate: float = 0.0,
)
```

| Parameter | Description |
|---|---|
| `market` | Compiled `Market` instance used during the backtest |
| `benchmark_symbols` | Buy-and-hold comparisons (must exist in `market`) |
| `exchange` | Exchange to pull balance snapshots from |
| `portfolio` | Optional — adds exposure metrics to snapshots |
| `key` | String prefix for this strategy's entries in `reporter.summary` |
| `risk_free_rate` | Annual risk-free rate as a decimal (e.g. `0.05` for 5%) |

### Methods

| Method | Description |
|---|---|
| `take_snapshot()` | Record current balance and exposure at the current candle. Call once per candle. |
| `generate_report()` | Build `reporter.summary` from accumulated snapshots and trades. Returns the summary dict. Does **not** produce any chart output. |
| `show_plot(additional_fields?)` | Render the Plotly report in the browser. |
| `save_plot(filename, additional_fields?)` | Write the Plotly report to an HTML file. |
| `merge_external_snapshots(snapshots)` | Combine snapshot dicts from multiple exchanges (multi-leg strategies). |

### `reporter.summary` keys

After `generate_report()`, `reporter.summary[key]` contains:

| Key | Unit | Description |
|---|---|---|
| `sharpe_ratio` | ratio | Mean excess return / std dev of returns, annualised. Risk-adjusted return including both upside and downside volatility. |
| `sortino_ratio` | ratio | Like Sharpe but uses only downside deviation. Preferable when the return distribution is positively skewed. |
| `annualized_volatility_percent` | % | Annualised standard deviation of daily returns. Measures equity-curve smoothness. |
| `cagr_percent` | % | Compound Annual Growth Rate — the steady annual return that produces the same end value. Allows fair comparison across different backtest lengths. |
| `calmar_ratio` | ratio | CAGR / abs(max drawdown). Return earned per unit of worst-case loss. |
| `recovery_factor` | ratio | Net return / abs(max drawdown). How many times the strategy "earned back" its worst loss. |
| `max_drawdown_percent` | % | Largest peak-to-trough decline of the equity curve. Negative value. |
| `gross_return_percent` | % | Terminal equity as a percentage of starting equity (e.g. 250 = final is 2.5× initial). |
| `net_return_percent` | % | Profit as a percentage of starting equity (gross − 100). |
| `profit_factor` | ratio | Gross profit / gross loss across all trades. Values > 1 mean the strategy earned more than it lost. |
| `win_rate_percent` | % | Percentage of closed trades that were profitable. |
| `avg_win_loss_ratio` | ratio | Average winning trade PnL / average losing trade PnL. |
| `max_consecutive_losses` | count | Longest unbroken streak of losing trades. |
| `avg_holding_period_min` | minutes | Average time a position was held, in minutes. |
| `closed_trades` | count | Total number of completed trades. |
| `winner_trades` | count | Number of profitable trades. |
| `loser_trades` | count | Number of unprofitable trades. |

Benchmark entries (`reporter.summary[symbol]`) contain the same keys except the trade-level stats (`profit_factor`, `win_rate_percent`, `avg_win_loss_ratio`, `max_consecutive_losses`, `avg_holding_period_min`, `closed_trades`, `winner_trades`, `loser_trades`).

---

## `metrics` module

All functions in `metrics.py` are pure (no state, no side effects) and can be imported and called directly:

```python
from backtester.performance import metrics

sharpe = metrics.sharpe_ratio(daily_returns, risk_free_rate=0.05)
sortino = metrics.sortino_ratio(daily_returns)
vol = metrics.annualized_volatility(daily_returns)          # decimal, multiply by 100 for %
annual_return = metrics.cagr(gross_cumulative_return=2.5, n_days=730)
calmar = metrics.calmar_ratio(annual_return, max_drawdown=-0.30)
rf = metrics.recovery_factor(net_return=1.50, max_drawdown=-0.30)
pf = metrics.profit_factor(trades)
wr = metrics.win_rate(trades)                               # 0.0–1.0
wl = metrics.avg_win_loss_ratio(trades)
streak = metrics.max_consecutive_losses(trades)
hold = metrics.avg_holding_period_minutes(trades)
```

See the docstring on each function in [metrics.py](metrics.py) for a precise definition of every argument and return value.
