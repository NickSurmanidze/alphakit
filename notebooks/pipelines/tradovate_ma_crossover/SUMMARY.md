# Tradovate MA-Crossover Pipeline — Summary

## What this pipeline does

A long-only MA-crossover strategy (fast indicator vs. a plain SMA slow line) tested
across 6 CME micro/full-size futures, on real 1h OHLC data (~1 year, `source="ib"`),
through a full event-driven backtester: real whole-contract sizing/margin, tick-based
slippage, per-contract commissions, and a Tradeify $50k-Growth-account risk model
(4% EOD-trailing drawdown with a lock-once-reached floor, plus a $1,250/day soft
daily-loss pause).

**Steps, per instrument**: `step_1` grid-searches 4 indicator families (KAMA, EMA,
HMA, DEMA, each vs. SMA) and picks a winner by trade-level Sharpe lower bound (not
raw return); `step_2` runs that winner through the full event-driven engine solo.
Then `combined.ipynb` builds a multi-instrument portfolio on one shared $50k account,
and `monte_carlo.ipynb` stress-tests that portfolio against 1000 block-bootstrapped
alternate histories (real bars resampled in contiguous blocks, preserving
autocorrelation and cross-instrument correlation).

All Sharpe/Sortino/volatility figures below use `periods_per_year=252` (real futures
trading days), not the engine's crypto-default 365 — an earlier pass in this session
used the wrong default, inflating those figures ~20%; it's fixed everywhere now.

## Per-symbol solo results

| Symbol | Winning strategy | Sharpe | Sortino | Calmar | Net return | Max DD | Trades | Win rate | Breached? |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| MES | KAMA(26)/SMA(42) | 1.35 | 2.01 | 2.94 | +8.6% | -3.3% | 66 | 54.5% | No |
| M2K | EMA(2)/SMA(12) | 1.34 | 2.42 | 2.91 | +21.0% | -8.1% | 306 | 33.0% | No |
| MNQ | DEMA(10)/SMA(32) | 0.82 | 1.30 | 1.22 | +9.3% | -8.6% | 107 | 43.9% | No |
| M6E | HMA(58)/SMA(62) | 0.37 | 0.60 | 0.72 | +1.4% | -2.2% | 46 | 43.5% | No |
| 6J | DEMA(14)/SMA(22) | 0.01 | 0.03 | -0.05 | -0.3% | -5.9% | 13 | 23.1% | **Yes** |
| M6A | KAMA(58)/SMA(102) | 1.35 | 2.18 | 3.47 | +8.9% | -2.9% | 26 | 61.5% | No |

MNQ underperforms its own buy-and-hold (negative alpha). M6E and 6J have no real
standalone edge — 6J actually breached the drawdown limit on its own once leverage
made it tradeable (its ~$77-84k/contract notional requires leverage just to trade a
$50k account at all). MES, M2K, and M6A are the 3 instruments with genuine
standalone edges, and are the ones carried forward into the combined portfolio.

## Combined portfolio — MES + M2K + M6A, ~1 contract each, one $50k account

Hand-picked (not the algorithmic 20-combination search's winner, which turned out to
be MNQ+6J+M6A and didn't hold up under stress — see below). Each instrument's weight
is tuned so it trades ~1 contract at a time at `max_leverage=2`, hard-capped at 1
contract regardless of how the account's equity compounds.

| Metric | Value |
|---|---:|
| Sharpe | 1.61 |
| Sortino | 2.59 |
| Calmar | 3.77 |
| Net return | +15.1% |
| Max drawdown | -4.49% |
| Total trades | 398 |
| Account breached? | No |
| Drawdown floor locked (profit buffer reached)? | Yes |

Combined Sharpe (1.61) beats every individual solo Sharpe (1.35 / 1.34 / 1.35) — a
genuine diversification benefit, confirmed via exposure tracking (contract counts
never exceeded 1 per instrument throughout the backtest).

## Monte Carlo stress test — 1000 block-bootstrapped alternate histories

Same combined portfolio (fixed strategies, fixed ~1-contract sizing), re-run against
1000 synthetic histories built by resampling real historical price blocks.

| Metric | Value |
|---|---:|
| P(account breaches the 4% drawdown limit) | 26.7% |
| P(net profitable) | 76.8% |
| P(reaches the profit-lock buffer) | 79.1% |
| Sharpe: mean / median / 5th–95th pct | 1.04 / 1.25 / -0.96 – 2.55 |
| Net return: mean / median / 5th–95th pct | +11.0% / +11.7% / -3.5% – +27.0% |
| Max drawdown: mean / 5th–95th pct | -4.3% / -6.4% – -2.7% |

Real history's own result (Sharpe 1.61, +15.1%) sits comfortably in the upper-middle
of this distribution, not as a lucky outlier. For comparison, the earlier
algorithmically-selected MNQ+6J+M6A combination (5x leverage) looked excellent on the
one real historical run (Sharpe 2.34, no breach) but had a **60% breach rate** under
this same stress test — it was a lucky draw, not a robust strategy, mainly because
6J had no standalone edge and only got picked for its correlation properties. The
hand-picked, more conservative combination tested here is meaningfully more robust,
though **26.7% is still not "safe"** — roughly 1 in 4 alternate histories still blow
the account.

## Caveats

- Bounded by ~1 year of real history throughout — no walk-forward or out-of-sample
  validation beyond each instrument's own single train/test holdout in `step_1`.
- Stop-loss/take-profit (30%/60% of entry price) never actually fires on this data —
  every exit is a MA-crossover signal exit, left as-is by design for now.
- `max_leverage=2` is a stand-in, not Tradovate's real per-symbol margin schedule.
- See `tradovate_ma_crossover.md` for the full session-by-session history, including
  the bugs found and fixed along the way (leverage-aware sizing, margin-side bug,
  `periods_per_year` mismatch, compounding-equity sizing risk, daily-loss modeling).
