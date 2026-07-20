# Planning

The goal of this pipeline is to establish a trend following trading algo with n instruments. Each instrument has sub pipeline in dedicated folder e.g. MES folder has MES related files for finding best crossings and best indicators to use for fast and slow moving averages.

Then once we find best parameters for each instrument.

The next step is to construct combined portfolio in combined.ipynb and see the benchmarks.

Last step is stress testing the portfolio with monte carlo simulation.

## Status (2026-07-10): first full pass complete

All three instrument pipelines + combined portfolio + Monte Carlo stress test have
been built and actually executed against the live Mongo/Timescale DB. Real per-instrument
winners (each found independently via its own 4-indicator-family comparison in
`step_1_find_best_indicators_and_parameters.ipynb` -- KAMA/EMA/HMA/DEMA, each vs. a
plain SMA slow line, ranked by trade-level Sharpe lower bound with real Tradeify
costs):

| Instrument | Winning pair | Solo Sharpe (event-driven) | vs. buy-and-hold Sharpe |
|---|---|---|---|
| MES | KAMA(fast=26)/SMA(slow=42) | 1.62 | 1.44 |
| M2K | EMA(fast=2)/SMA(slow=12) | 1.61 | 1.32 |
| MNQ | DEMA(fast=10)/SMA(slow=32) | 0.99 | 1.65 (algo underperforms here -- negative alpha) |

Notably, **a different indicator family won for each instrument** -- this is why the
4-way comparison runs per-instrument rather than being assumed from MES's result.

**Combined portfolio** (`combined.ipynb`, weight=1/3 per instrument -- required since
`MaCrossoverStrategy` always requests 100% of account per its own symbol and
`Portfolio` doesn't normalize weights): Sharpe 1.56, annualized vol 3.64% (vs. 5.8-22%
for any instrument alone -- the real diversification win), max drawdown -2.32% (better
than any solo run), never breached the real $2,000/4% Tradeify trail, and the trail
locked (reached the profit buffer) during the run. Net return (5.15%) is much lower
than any single instrument's solo return, though -- an expected consequence of the
conservative 1/3-weight sizing needed to avoid ~300% worst-case combined exposure, not
a sign the diversification failed.

**Monte Carlo** (`monte_carlo.ipynb`, N=100, block-bootstrap of real historical bars,
correlated across all 3 instruments, fixed strategies/parameters): P(breach) = 3%,
P(net profitable) = 94%, P(reached the drawdown lock) = 71%. Real history's own
result (5.15% return, Sharpe 1.56) sits comfortably inside the simulated distribution,
not as an outlier -- a good sign the Monte Carlo is behaving sensibly.

**Also fixed along the way** (see `PROP_FIRM_PLAN.md`/`OPTIMIZATION_RESEARCH_PLAN.md`
for detail): researched MNQ/M2K tick size + commission (previously MES-only),
implemented `TradeifyDrawdownMiddleware`'s real lock-once-reached mechanic (was
missing, trailed forever before), and added a formatted/color-coded HTML summary
table (`PerformanceAnalyzer.summary_html_table()`) used throughout.

**Known limitations, carried into every notebook's own caveats section**: all of this
is still bounded by ~1 year of real MES/M2K/MNQ history (real data doesn't go back
further despite `DATE_FROM` implying otherwise) -- every "winning" pick is *this pass's
best defensible answer given that constraint*, not a validated edge (each step_1's
own out-of-sample holdout check shows this directly). No walk-forward, CSCV/PBO
overfitting-probability scoring, or the full Tradeify pass-probability simulator from
`OPTIMIZATION_RESEARCH_PLAN.md` §7.2 yet -- next steps if this gets taken further.

## Status (2026-07-10, later same day): real leverage, 3 more instruments, honest Monte Carlo correction

**Major correction to the first pass above**: direct instrumentation
(`Rebalancer.round_position_size()`) proved the "combined portfolio diversification"
result above was an illusion -- MES and MNQ silently floored to **0 contracts** every
single rebalance at 1/3 weight (their own per-contract notional exceeded what an
unleveraged 1/3 equity-share could buy), so that whole result was actually just M2K
trading alone at reduced size. Root cause: `Exchange.max_leverage` was never actually
used to scale position sizing, only as a margin-call-buffer divisor.

**Fixed** (opt-in, `leverage_aware_sizing: bool = False` on `Exchange` -- default
preserves every existing test/notebook's exact behavior, since the 4 golden tests use
`max_leverage=10` purely as margin headroom and would otherwise silently 10x):
`Rebalancer` now multiplies sizing by `max_leverage` when opted in; `Portfolio`
orders positions deterministically by `weighted_strategies` list priority (was
Python `set()` hash order before); `Rebalancer` pre-checks margin per position in
that priority order and skips (logs, doesn't crash) whichever lower-priority
position doesn't fit once higher-priority ones are sized. Also found and fixed a
real pre-existing bug this exposed: `Orders.validate_order_before_creating()`
hardcoded `side=PositionSide.long` when computing required margin for *every* order,
so closing a long was miscounted as opening a fresh one (demanding margin instead of
freeing it) -- only ever surfaced once real leverage made margin tight enough to hit
it. 187 tests now passing (183 -> 187), including new regression coverage and all 4
golden tests unchanged.

**3 more instrument pipelines built and run** (M6E, 6J, M6A -- micro/full-size FX,
chosen for genuinely low/negative correlation with the MES/M2K/MNQ equity book, see
`ib_portfolio_test.ipynb`'s correlation matrix): M6A was the *strongest* standalone
result of all 6 instruments (Sharpe 1.63, Calmar 3.47, profit factor 3.74); M6E and
6J both showed *negative* `trade_sharpe_lb` in every indicator family (no standalone
edge), and 6J specifically needed leverage just to trade at all (its own per-contract
notional, ~$77-84k, exceeds the entire $50k account unleveraged) -- and then promptly
breached the drawdown limit solo once it *could* trade.

**`combined.ipynb` rebuilt**: brute-forced all C(6,3)=20 three-instrument
combinations (leveraged, 5x) ranked by combined Sharpe. Winner: **MNQ + 6J + M6A**,
Sharpe 2.34, Sortino 4.29, Calmar 5.66, net return +55.4%, max drawdown -11.2%, no
breach -- far exceeding any solo instrument's number, and confirmed via the new
exposure/leverage display that real (non-phantom) contracts were traded throughout
(max effective leverage ~4.7x, mean ~2.3x while positions open). Every combo
containing 6J alongside MNQ or M6E scored well; combos built entirely from
MES/M2K/MNQ (all mutually correlated 0.77-0.93) mostly scored negatively -- a real
diversification signal this time, not an artifact.

**`monte_carlo.ipynb` rebuilt to match, and this is the important part**: stress-testing
the winning MNQ+6J+M6A combo against 100 block-bootstrapped alternate histories gives
**P(breach) = 60%** -- the real-history result above is a lucky draw, not a
representative one. The distribution is bimodal (median Sharpe ~0.01, but 75th-95th
percentile 1.8-3.1), consistent with an early unlucky stretch blowing through the 4%
floor before the strategy has a chance to reach the profit-locking buffer. This is a
direct, expected consequence of real ~2.3-4.7x leverage now actually being applied,
not a bug -- and the headline reason not to take `combined.ipynb`'s real-history
Sharpe at face value for sizing an actual eval attempt.

## Status (2026-07-10, third pass): hand-picked instruments replace the algorithmic 20-combo winner

**User course-correction, and the reason for this pass**: after running the
MNQ+6J+M6A Monte Carlo 1000 times themselves, the user rejected that combination --
not because the Monte Carlo technique was wrong, but because **6J had no standalone
edge in the first place** (negative `trade_sharpe_lb` across all 4 indicator
families in its own step_1, and it breached the drawdown limit solo once leverage
made it tradeable). It only made the C(6,3)=20 brute-force Sharpe search because of
its negative correlation with the other instruments, not because it was individually
a sound strategy. **Correlation/diversification value alone is not sufficient
justification to include an instrument -- it must also have an individually-verified
standalone edge.** M6A, by contrast, was confirmed as the strongest standalone result
of all 6 instruments tested (Sharpe 1.63 solo) and stays in. M6E stays out (no
standalone edge either). The user's explicit instruction: hand-pick **MES + M2K +
M6A**, sized so each trades ~1 contract at a time (not a percent-of-equity split that
can round to 0 or fluctuate).

**`combined.ipynb` rebuilt (v3)** around this hand-picked trio instead of the
algorithmic search: `max_leverage=2` (down from 5x, since this is a deliberately more
conservative starting point), and a new `one_contract_weight()` derivation --
`weight_i = (max_observed_price_i * 1.05_headroom * point_value_i) / (account_balance
* max_leverage)` -- verified both analytically (floors to exactly 1 contract across
each instrument's full observed price range) and empirically via the
`ExposureRecorder` middleware (contract-count distribution never exceeds 1, across
the whole ~1 year backtest). Real-history result: **Sharpe 1.97, Sortino 3.16, Calmar
3.82, net return 15.2%, max drawdown -4.48%**, no breach, floor locked, 396 trades
(matches the sum of each instrument's own solo trade count almost exactly -- 66 MES +
306 M2K + 26 M6A in the last full run -- confirming genuine, non-phantom sizing, not
another instance of last pass's zero-contract bug). Combined Sharpe (1.97) genuinely
beats all three solo Sharpes (1.62/1.61/1.63).

**`monte_carlo.ipynb` rebuilt to match (N=1000, same hand-picked trio, same
`one_contract_weight()`/`max_leverage=2`)**: **P(breach) = 26.6%**, P(net profitable)
= 76.8%, P(reached the profit-locking buffer) = 79.2%. This is a large improvement
over the rejected MNQ+6J+M6A combo's 60% breach rate, and directly confirms the
user's methodology critique was right -- a more conservative, individually-vetted
combination is meaningfully more robust under resampled histories, not just prettier
on the one real historical path. **Still, 26.6% is not low** -- roughly 1 in 4
resampled histories still breach the $2,000/4% Tradeify trail, so this is not "safe"
in an absolute sense, just substantially safer than the alternative that was tried
and rejected. The distribution's 5th percentile net return is -3.5% (vs. -5.1% at
the min), median Sharpe 1.52 (vs. bimodal ~0.01 for the rejected combo) -- a
unimodal, much healthier-looking distribution overall. Real history's own result
(Sharpe 1.97) sits inside the upper-middle of the simulated distribution (75th
percentile is 2.23), not as an outlier.

**Open question for any future pass**: whether the 26.6% breach rate can be reduced
further by dropping leverage below 2x, tightening stop-losses, or reconsidering
whether 3 instruments is the right count at all -- none of that was attempted this
pass since the user's instruction was specifically to hand-pick this trio at
1-contract sizing, not to further optimize it.

## Status (2026-07-10, fourth pass): code review found 3 real issues, all fixed and re-verified

A direct code read (not just re-running notebooks) turned up three things worth
fixing, at the user's request (explicitly excluding the wide 30%/60% SL/TP, which
stays as-is for now):

1. **`periods_per_year` bug, confirmed**: `PerformanceAnalyzer` defaults to 365
   ("crypto trades 365 days/year" -- its own docstring says pass ~252 for futures),
   and grepping every notebook in this pipeline showed **none of them ever did** --
   every Sharpe/Sortino/annualized-volatility number reported across this whole
   session was inflated by sqrt(365/252) =~ 1.20 (~20%). CAGR/Calmar/max_drawdown/
   net_return never depended on this constant, so those numbers were always correct.
   **Fixed**: `periods_per_year=252` now passed explicitly in all 6 step_2 notebooks,
   `combined.ipynb`, and `monte_carlo.ipynb`.
2. **Compounding-equity sizing risk**: `Rebalancer` sizes off *current* (compounding)
   equity, but `combined.ipynb`/`monte_carlo.ipynb`'s "1 contract each" weight was
   only calibrated against the account's *starting* $50k -- a run that grows the
   account enough (some Monte Carlo paths grow it +50%) could in principle silently
   push a position to 2 contracts. **Fixed** with a new engine feature:
   `SymbolConfig.max_position_size` (a hard per-symbol contract ceiling, enforced in
   `Exchange.round_position_size()`, independent of whatever the percent-of-equity
   formula computes) -- `combined.ipynb`/`monte_carlo.ipynb` now set
   `max_position_size=1.0` for MES/M2K/M6A specifically (every other symbol
   untouched). 3 new tests added (`test_symbol_config.py`,
   `test_futures_point_value.py`). **Re-running the Monte Carlo confirmed this was
   never actually triggered in practice** -- P(breach) moved from 26.6% to 26.7%,
   i.e. the ceiling was a no-op safety net, not a fix to an active bug. Good to have
   locked in regardless, since it was previously unverified for anything but the one
   real-history path.
3. **Tradeify's real $1,250/day soft daily-loss pause was never modeled** anywhere in
   this pipeline, despite already being documented in `PROP_FIRM_PLAN.md` and having
   working code (`MaxDailyLossMiddleware`, previously unused). **Fixed**: now wired
   into all 6 step_2 notebooks, `combined.ipynb`, and `monte_carlo.ipynb` at
   `max_loss_percent=0.025` (2.5% of a $50k account = $1,250), alongside the existing
   permanent drawdown-breach middleware.

**Deliberately not fixed, per explicit instruction**: `sl_percent=0.3`/`tp_percent=0.6`
(30%/60%) is wide enough that it never actually fires for these instruments on
hourly bars in this data -- every exit in this whole pipeline is a MA-crossover
signal exit, not a stop-loss fill. Left as-is for now.

**Corrected real-history numbers** (`combined.ipynb`, re-executed against the live DB
after all 3 fixes): Sharpe **1.61** (was 1.97), Sortino **2.59** (was 3.16), Calmar
3.77 (was 3.82, negligible change), net return 15.1% (was 15.2%), max drawdown -4.49%
(unchanged), 398 trades. The ~20% Sharpe/Sortino drop is exactly the
`periods_per_year` correction; the tiny remaining differences elsewhere are the daily
-loss middleware occasionally pausing a day that previously traded through.

**Corrected Monte Carlo (N=1000, same hand-picked MES+M2K+M6A combo)**: **P(breach) =
26.7%** (was 26.6% -- confirms the max_position_size ceiling wasn't masking anything),
P(net profitable) = 76.8% (unchanged), P(locked) = 79.1% (unchanged). Sharpe
distribution shifted down ~20% as expected: mean 1.04 (was 1.25), median 1.25 (was
1.52), real-history Sharpe 1.63 (was 1.97) -- comfortably inside the corrected
distribution's upper-middle (75th percentile 1.85), same "not an outlier" conclusion
as before. **The headline risk number (P(breach)) and every dollar-based number are
unchanged** -- this pass corrected how good the strategy *looks* on a risk-adjusted
basis, not the actual odds of blowing the account or the actual dollars it makes.

**Verification**: 192/192 backtester tests pass (187 -> 192, +5 for the new
`max_position_size` feature). All 8 changed notebooks (6 step_2 + combined +
monte_carlo) re-executed end-to-end against the live Mongo/Timescale DB with zero
errors.

## Status (2026-07-13): MES moves off Mongo/Timescale onto Databento's full history, new winner found (KAMA/session-VWAP)

**Data source switch, MES only so far**: the Mongo/Timescale IB feed only ever had
~1 year of real MES history despite `DATE_FROM`/`DATE_TO` implying ~6.7 years -- every
MES result above is *this pass's best defensible answer given that ~1-year
shortfall*, not a validated edge. Fixed by adding a whole new data source,
Databento (`GLBX.MDP3`, `MES.c.0` continuous front-month contract):

- **New app-level integration** (`apps/trading-system-backend`): `databento`
  connector implementing `MarketDataConnector` (registry/queues/env wiring, HTTP
  Basic auth against Databento's Historical REST API since there's no official
  Node client), handles the API's own ingestion-lag/licensing-delay 422s by
  clamping `end` to the server-reported `available_end` and retrying.
- **New backtester tooling** (`apps/backtester/src/data_aggregator/databento_aggregator.py`):
  downloads full-history 1-minute OHLCV to a local Parquet
  (`apps/backtester/datasets/databento/MES_c_0_1m.parquet`, gitignored), with a
  mandatory cost estimate (`metadata.get_cost`, the same endpoint Databento's own
  billing uses) and interactive confirmation before spending anything -- full MES
  1-minute history cost **$9.16**, one-time. `load_1m_parquet_resampled()` resamples
  to any bucket size (dropping non-trading buckets, not leaving them as NaN/zero-
  volume rows a plain calendar-grid resample would).
  **To reproduce**: `cd apps/backtester && DATABENTO_API_KEY=... uv run python -m
  data_aggregator.databento_aggregator` (prints the cost, waits for `y` before
  downloading).
- Real MES history is now **2019-05-05 onward** (the contract's actual launch date)
  through whenever the Parquet was last downloaded -- genuinely ~7.2 years, not ~1.

**`step_1_find_best_indicators_and_parameters.ipynb` re-run against the full
history** (1h bars resampled from the 1-minute Parquet): winner changed from
KAMA(26)/SMA(42) (found on ~1 year of data) to **KAMA(fast=42)/SMA(slow=132)**,
`trade_sharpe_lb=+0.554` (was +0.04 on the ~1-year window -- a much stronger,
more defensible number). Two further passes added to this same notebook (not new
notebooks):

1. **Experiment 5 (KAMA/KAMA, both lines adaptive) + a heatmap over KAMA's own
   `fast`/`slow` EMA smoothing constants + a fine independent re-scan of KAMA's
   `length`**: all confirmatory, not corrective -- KAMA/KAMA came close
   (`trade_sharpe_lb=+0.506`) but didn't beat KAMA/SMA; Kaufman's own textbook
   defaults (`fast=2, slow=30`) turned out to already be the best of 41 tested
   combinations; the fine length re-scan found nothing better than `42` in a
   71-point scan. **An ADX trend-strength filter made every tested combination
   worse** (down to negative `trade_sharpe_lb` at the strictest thresholds) --
   traced directly to the filter forcing exit/re-entry on every choppy ADX
   reading (158 -> 320-558 trades), not a lack of edge in the underlying signal.
2. **Experiments 6-7: KAMA vs. session VWAP (new indicator, `Indicators.vwap_session`
   -- originally built for the separate mean-reversion pipeline below, reused
   here), both orderings**: **KAMA(fast=34)/session-VWAP(slow) beat the incumbent**,
   `trade_sharpe_lb=+0.575` -- the best number found anywhere in this notebook.
   VWAP has no `length` of its own (session-anchored, not a rolling window), so
   only KAMA's length varies; the reverse ordering (VWAP as the fast line) was
   clearly worse (`+0.421` best case). Out-of-sample check (75/25 train/test) came
   back genuinely strong, not just a good full-period number: **test-slice Sharpe
   (1.564) higher than train (0.874)**, test drawdown (-12.2%) less than half of
   train's (-25.6%) -- the opposite shape of a curve-fit result.

**`step_2_run_full_backtest.ipynb` updated to match, and re-verified through the
real event-driven engine (not just the vectorized numbers above)**:
KAMA(34)/session-VWAP beat KAMA(42)/SMA(132) for real, not just on paper --
Sharpe **0.84** (vs ~0.71-0.73), net return **106.5%** (vs ~80-83%), max drawdown
**-11.4%** (similar, and notably *better* than its own -25.6% frictionless
prediction), win rate 72.1% (vs ~50%), no drawdown-middleware breach, floor
locked. Trades far more often (821 vs 158 over the same period, ~114/year vs
~22/year) -- the turnover risk that raised was a legitimate thing to check, and
the real engine (real Tradovate fees, real position sizing/margin) confirms it
holds up rather than being quietly eaten by costs. **New engine feature enabling
this check**: `PerformanceAnalyzer.summary_html_table(split="Y"|"Q")` -- year-
by-year (or quarter-by-quarter) performance columns alongside the usual
whole-period one, which showed no single disastrous year (worst is 2022, roughly
flat at CAGR -1.7%) rather than one blow-up year hiding inside a good aggregate
number.

**Current MES parameters** (`step_2_run_full_backtest.ipynb`):
`KAMA_FAST_LEN=34`, `KAMA_EMA_FAST=2`, `KAMA_EMA_SLOW=30`, slow line = session VWAP
(no length), `sl_percent=0.025`, `tp_percent=0.07` (carried over unchanged from the
old KAMA/SMA pick -- **not yet re-derived for this candidate's much shorter real
holding period**, a known gap, not an oversight). Tradeify drawdown/daily-loss
middleware deliberately loosened to 50%/50% in this notebook (real limits are
4%/2.5%) since it tests one strategy sleeve in isolation ahead of eventual
multi-strategy combination -- re-tighten once combined with others, same as the
existing convention documented in that notebook's own cells.

**Separate mean-reversion pipeline exploration** (`notebooks/pipelines/mean-reversion/`,
not this pipeline, but the new `Indicators.vwap_session`/`VwapMeanReversionStrategy`
backtester additions came from here first): VWAP deviation-band mean reversion on
MES, entry_std/exit_std grid up to a 21-combo search -- **did not find a profitable
corner**. First pass (tight 1.5-2.5 std bands) lost badly to fee drag from
overtrading (5,479 trades, $16,851 in real commissions against a +$1,232 raw
edge); widening bands fixed the fee problem but exposed that the raw pre-fee edge
itself goes negative once bands are wide enough to control turnover -- a
structural finding, not a tuning problem. Documented for completeness since the
indicator/strategy code it produced ended up valuable for *this* pipeline instead.

**Verification**: 212/212 backtester tests pass (192 -> 212, +20 across the
Databento connector/aggregator, `Indicators.vwap_session`/`adx`,
`VwapMeanReversionStrategy`, and `PerformanceAnalyzer`'s period-split feature).
Both notebooks re-executed end-to-end against the real Databento Parquet with zero
errors.

## Status (2026-07-13, later same day): 1-minute candle attempt -- negative finding, transaction costs make genuine intraday MA-crossover unviable on MES

Goal was to switch `step_1`/`step_2` to 1-minute bars specifically to find a
crossover pair whose real holding period is minutes-to-a-few-hours (the KAMA/VWAP
winner above, while a real improvement, still only trades ~114/year -- multi-day
holds, not intraday). Result: **a real, structural negative finding, not a bug**.

**What was tried**: `step_1`'s data cell switched to load the Databento Parquet at
native 1-minute resolution (`UNIT_OF_TIME="minute"`, same numeric
`long_len_range`/`short_len_range` left unchanged on purpose -- at 1-minute
granularity `range(2, 300, 10)`/`range(2, 60, 4)` mean 2-300 *minutes* / 2-60
*minutes*, i.e. exactly the few-minutes-to-a-few-hours zone being targeted). First
two full re-executions **crashed** (`IndexError`/`KeyError`) because every single
experiment -- KAMA/SMA, EMA/SMA, HMA/SMA, DEMA/SMA, KAMA/KAMA, KAMA/VWAP both
orderings -- came back with **zero** combinations clearing the
`total_return > 0 and trades >= MIN_TRADES` bar at 1-minute resolution, and the
"pick the winner" cells did `.iloc[0]` on the resulting empty frames. Added a
shared `pick_best_or_fallback()` helper (falls back to the unfiltered argmax with
a loud printed warning instead of crashing) so a single all-unprofitable
experiment can't block the rest of the notebook from running -- kept, since it's
a real robustness improvement independent of this finding.

**Root cause, confirmed with a standalone diagnostic script** (same cost model as
`evaluate_signal()`: 1 tick slippage + $0.91/side commission, ~$4.32/round-trip
on MES):

| Setup | Gross return | Net return | Trades/yr |
|---|---|---|---|
| KAMA(34)/SMA(132), lengths in minutes | +107% | **-77%** | 1,536 |
| KAMA(34)/VWAP, lengths in minutes | +109% | **-46%** | 933 |
| KAMA(120m=2h)/SMA(480m=8h) | +75% | **-8%** | 443 |
| KAMA(2040m=34h)/SMA(7920m=132h) -- same *time* as the existing hourly winner | +89% | **+77%** | 47 |

The underlying trend edge is real (gross return strongly positive at every
lookback tested) but MES's fixed per-trade cost is large relative to the price
move a crossover captures within an intraday window, so every genuinely-intraday
parameterization loses money net of costs -- only stretching the lookback back
out to multi-day territory (reproducing the existing hourly winner almost
exactly, ~1 trade/week) survives. **Also tried layering the ADX trend filter
(already built for the hourly notebook) on top of intraday lengths, hoping it
would cut whipsaw trades**: it made things dramatically worse instead (trade
count exploded to 3,000-30,000/year across every length/threshold combination
tested) -- ADX itself whipsaws across its threshold at 1-minute granularity,
forcing extra forced-flat/re-entry round-trips on top of the crossover's own
signal changes, the opposite of the intended effect.

This is the same fee-drag mechanism already found in the separate mean-reversion
pipeline above (overtrading eating a real raw edge), now confirmed for the
crossover family too: **simple MA-crossover signals do not survive realistic
Tradeify/Tradovate costs at intraday MES trade frequencies**, regardless of
which indicator pair or trend filter is layered on top. Genuinely intraday
(minutes-to-hours) MES trading would need either materially lower per-trade
costs, or a fundamentally different signal mechanic (e.g. a minimum-holding-period
lockout, a wider dead-zone/hysteresis band before re-entry, or a breakout/target-
based approach rather than continuous crossover-driven position flips) --
deliberately not attempted this pass; decided to stop and document rather than
keep guessing at mitigations.

**Current state of the notebooks**: `step_1_find_best_indicators_and_parameters.ipynb`
still has its data cell set to `UNIT_OF_TIME="minute"` and the new
`pick_best_or_fallback()` guard, but was **not** re-executed to a clean completion
after this finding -- most of its cell outputs are stale artifacts left over from
the last successful **hourly** run before the switch, not real 1-minute results
(only the standalone diagnostic script's numbers above are trustworthy for the
1-minute case). `step_2_run_full_backtest.ipynb` was **not touched** and still
reflects the hourly KAMA(34)/session-VWAP winner documented in the entry above --
that remains the current best validated candidate. Anyone picking this back up
should either revert `step_1`'s `UNIT_OF_TIME` to `"hour"` to get back to a clean,
fully-consistent notebook, or deliberately re-run it at 1-minute resolution
knowing the likely outcome, before trying one of the mitigations above.
