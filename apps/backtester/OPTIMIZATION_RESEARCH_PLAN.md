# Optimization Research Plan — trade count vs. risk-adjusted performance

Research roadmap for the MES/Tradovate MA-crossover work in
`notebooks/vectorized_backtest_mes_tradovate.ipynb` (parameter grid search) and
`notebooks/test_backtester_mes_tradovate.ipynb` (event-driven validation run), from
indicator-level tuning up to whole-algo optimization, plus prop-firm-specific
directions for a Tradeify evaluation. Companion to `PROP_FIRM_PLAN.md` (engine
plumbing); this doc is about *what to optimize and how to select parameters honestly*.

**State of the world this plan starts from (verified 2026-07-10):**

- Real MES 1h data in Mongo/Timescale spans only **~1 year** (2025-07 → now), even
  though the notebooks' `DATE_FROM` says 2019. Everything below is starved by this.
- The vectorized grid ranks combos by **raw total return** with a manual
  `MIN_TRADES >= 100` filter. No risk adjustment of any kind in the selection loop.
- The event-driven `PerformanceAnalyzer` already computes Sharpe, Sortino, Calmar,
  Ulcer, VaR/CVaR, profit factor, win rate, R-multiple expectancy, max-DD duration
  (`src/backtester/performance/analyzer.py::generate_report`) — but none of that
  feeds back into parameter selection. The metric machinery exists; only the
  vectorized grid needs (cheap, vectorized) equivalents.
- Costs are currently zero (tick_size unresearched, commission placeholder 0.0 — see
  PROP_FIRM_PLAN.md Phase 1 TODOs). Every grid result is frictionless.
- **Audit finding:** `MaCrossoverStrategy.sl_percent`/`tp_percent` are *fractions of
  entry price* (`sl_price = price * (1 - sl_percent)`,
  `strategies/ma_crossover.py:90`). The notebook passes `0.3`/`0.6` = a **30% stop /
  60% target** — on MES hourly these never fire (every logged trade closes with
  `close_reason="signal"`; −2.9% losers sail through). SL/TP are effectively
  disabled, and `risk_percent`-based metrics (R-multiple expectancy) are computed
  against a fictional 30% risk unit.
- Current picks (KAMA fast len 26, EMA consts 2/45, SMA slow 42 → +18.0% / 136
  trades, frictionless) are the product of **two stacked grid searches on the same
  ~1 year** — in-sample hypotheses, not results.

**The core tension this plan resolves:** more trades = more statistical confidence
and smoother equity (good for a 5% EOD-trailing-drawdown evaluation), but more
trades = more cost drag once real commissions/slippage land, and naive
return-maximization always prefers the lucky low-N outlier. The fix is not a
hand-tuned `MIN_TRADES` — it's a selection objective where sample size enters the
math (§1) and costs enter the returns (§4), evaluated out-of-sample (§5), against
the metric that actually matters for Tradeify: probability of passing the eval (§7).

Priorities: **P0** = prerequisite for trusting anything else; **P1** = core value;
**P2** = worthwhile once P0/P1 exist.

---

## Executed 2026-07-10: first pass through §1.1/1.2/1.4, §4.1/4.2, §5.1, §3.1 (partial)

What actually ran, and what it found — see `notebooks/vectorized_backtest_mes_tradovate.ipynb`
(pipeline + 4 experiments) and `notebooks/test_backtester_mes_tradovate.ipynb`
(implementation) for the full detail; this is the summary.

- **§4.1/4.2 costs**: researched and set. MES `tick_size=0.25` ($1.25/tick, CME spec),
  `fee_per_contract_override=0.91` (half of Tradeify's $1.82 MES round-turn — charged
  per side, matching `get_fee()`'s billing) in `exchange_config.py`. Tradeify's real
  $50k-Growth-account drawdown (**4%/$2,000**, not this repo's generic 5% default) and
  its **lock-once-reached** mechanic were also researched — the lock isn't
  implemented yet (`TradeifyDrawdownMiddleware` still trails forever), see §7.1/§7.4.
- **§1.1/1.2 selection objective**: added `metrics.sharpe_lower_bound()` (Lo 2002
  asymptotic SE, tested in `test_metrics.py`) and used it as the ranking metric,
  computed on **per-trade** returns (n = round-trip trades) rather than per-day
  returns specifically so it's sensitive to trade count, not just calendar span — see
  that function's docstring for why the distinction matters.
- **§1.4 plateau selection**: 3x3-neighborhood-smoothed argmax reported alongside the
  raw best in every experiment's heatmap cell.
- **§3.1 alternative indicator families (partial)**: ran KAMA/SMA, EMA/SMA, HMA/SMA,
  DEMA/SMA (added `Indicators.hma()`/`Indicators.dema()`) — same length grid, same
  cost-aware scoring. **KAMA/SMA won outright** (`long_len=42`/`short_len=26`,
  `trade_sharpe_lb=+0.04` vs. next-best EMA/SMA at `-0.20`). Donchian/SuperTrend
  (structurally not a 2-line crossover) still deferred, per §3.1's original note.
- **§5.1 hold-out split**: run on the winner. Result tempers the pick rather than
  confirming it — performance concentrates in the untouched last-25% slice; the
  train-only slice's `trade_sharpe_lb` is clearly negative. Documented in the
  vectorized notebook's "Final pick" cell as the headline caveat: this is *this pass's
  best defensible answer given ~1 year of data*, not a validated edge.
- **Implemented and run end-to-end** in `test_backtester_mes_tradovate.ipynb` (single
  strategy, 2 indicators, real costs, real 4% drawdown middleware, live DB): **+8.65%
  net return, Sharpe 1.62, Sortino 2.42, Calmar 2.94, max drawdown -3.29% (no
  breach — well inside the real 4% trail), 66 trades, 54.5% win rate, profit factor
  1.73**, vs. buy-and-hold's +18.19% / Sharpe 1.44 / max drawdown -9.9% over the same
  window — lower return, meaningfully better risk-adjusted/drawdown profile, which is
  the right trade to want for a drawdown-limited eval account.
- **Not done this pass** (still open, unchanged from below): §2 data backfill, §1.3
  deflated Sharpe / trials correction, §5.2 walk-forward (needs more data than one
  train/test split to be meaningful), §5.3 CSCV/PBO, §5.4 formal vectorized↔event
  parity assertion (informally: 67 vs. 66 round-trip trades on the same window — close
  but not exact, worth a real test), §6 sizing, §7 Tradeify simulator, §8 ensembles.

---

## 1. Fix the selection objective (P0)

The grid's ranking function is the single highest-leverage change. All work here is
in the vectorized notebook's `execute_wrapped_backtest*` functions — every metric
below is computable from the `net_rs` (per-bar net log-return) series it already
builds, no event engine needed.

- [x] **1.1 Per-combo risk-adjusted metrics.** From `net_rs` add: annualized Sharpe
      and Sortino, max drawdown, Calmar, time-in-market (fraction of bars with
      `pos_exec != 0`), and trades/year. Annualization: infer bars-per-year from the
      data itself (`len(rs) / ((rs.index[-1] - rs.index[0]).days / 365.25)`) — MES
      trades ~23h/day, so hardcoding crypto's 24×365 or equities' 6.5×252 is wrong
      both ways. Sharpe = `mean(net_rs) / std(net_rs) * sqrt(bars_per_year)`.
- [x] **1.2 Confidence-bound selection instead of `MIN_TRADES`.** Rank combos by the
      lower bound of the Sharpe estimate, not the point estimate:
      `SR_lb = SR - z * SE(SR)` with `SE(SR) ≈ sqrt((1 + SR²/2) / N)` (Lo 2002,
      N = number of return observations; z = 1.0–1.645). This encodes the user's
      actual preference — a +14% strategy over 134 trades beats a +15.5% strategy
      over 33 trades — as math instead of a threshold. Keep `trades` as a reported
      column; drop it as a filter.
- [ ] **1.3 Deflated Sharpe Ratio (trials correction).** The grid tries ~400 combos,
      then ~100 more EMA-constant combos on the winner — effective trials are huge.
      Implement Bailey & López de Prado's DSR: compute the expected max Sharpe under
      the null given the number of trials and the cross-trial Sharpe variance, and
      report each candidate's probability of beating it. Cheap (closed-form, uses
      skew/kurtosis of `net_rs`), and it converts "best of 40,000" hand-waving in the
      notebook's own caveat into a number.
- [x] **1.4 Plateau selection.** On the (long_len × short_len) heatmap, select the
      parameter *neighborhood* with the best mean/worst-case metric (e.g. 3×3 window
      mean of `SR_lb`), not the argmax cell. A pick whose neighbors are also good is
      structurally less overfit; a spike surrounded by losers is noise. Trivial:
      `results2.rolling(3).mean()` on the pivot in both axes, or
      `scipy.ndimage.uniform_filter`.
- [ ] **1.5 Report per-combo max drawdown against the 5% Tradeify limit** even in
      the frictionless grid: a combo whose *frictionless, always-in* equity curve
      already draws down >5% peak-to-trough is dead on arrival for the eval
      regardless of its Sharpe (EOD version of this metric is §7's job; the per-bar
      version here is a cheap early filter).

## 2. Data expansion (P0 — the binding constraint)

One year of hourly data cannot support two stacked grid searches plus out-of-sample
validation. Nothing in §5 works without more history.

- [ ] **2.1 Backfill MES/ES 1h history.** Options to research, in order: (a) IB
      historical API depth for MES continuous (`CONTFUT`) 1h bars — typically much
      more than 1 year is available; extend the collector's backfill window; (b) use
      **ES** (same underlying, launched 1997) as a proxy for pre-MES-liquidity
      periods — MES launched May 2019 and tracks ES tick-for-tick at 1/10th size;
      (c) paid data (Databento, Portara/CQG) if IB depth disappoints.
- [ ] **2.2 Continuous-contract handling.** Verify how the `ib` source stitches
      quarterly rolls (IB `CONTFUT` is unadjusted). Unadjusted stitches inject phantom
      gap returns at roll dates (~4/year, can be 10+ points); research back-adjusted
      vs. ratio-adjusted series, and at minimum flag/neutralize roll-date bars in the
      log-return series before optimizing on multi-year data.
- [ ] **2.3 Regime coverage audit.** Whatever history lands, label it (realized-vol
      terciles, trend/chop via efficiency ratio, bull/bear) and report per-regime
      performance for any candidate — a strategy tuned on one melt-up year needs its
      chop/bear behavior known *before* the eval, not during.

## 3. Indicator-level optimization (P1)

All within the existing vectorized harness; each idea is a drop-in replacement for
the `ma_x = kama - sma` line plus a re-run of the (now §1-scored) grid.

- [x] **3.1 Alternative line pairs.** Same crossover skeleton, different lines: EMA,
      HMA (Hull), ZLEMA, TEMA, DEMA for the fast line; SMA/EMA/HMA for the slow. All
      exist in `pandas_ta`. Also structurally different signals with the same
      {-1,0,1} interface: Donchian-channel breakout, SuperTrend flip. Deliverable: a
      table comparing each family's *plateau* `SR_lb` (not its best cell) on
      identical data — decide whether KAMA/SMA actually earns its complexity.
- [ ] **3.2 Regime/entry filters.** Gate the crossover with a second condition and
      measure what it does to both trade count and `SR_lb`: (a) KAMA's own
      efficiency ratio above a threshold (only trade when trending — the ER is
      already computed inside KAMA, expose it); (b) ADX threshold; (c) realized-vol
      band (skip dead and panicked tape); (d) session filter — only enter during RTH
      (09:30–16:00 ET) vs. 23h, since overnight MES is thin and slippage-prone. Each
      filter deliberately *cuts* trades; the CI-based objective from §1.2 is what
      makes that tradeoff honest (fewer-but-better must overcome the wider CI).
- [ ] **3.3 Whipsaw control (hysteresis).** Instead of flipping on any sign change
      of `ma_x`, require `ma_x > +k·ATR` to enter and `ma_x < −k·ATR` to exit (a
      dead-band), and/or n-consecutive-bar confirmation. This is the direct
      trade-count-vs-quality dial: sweep k ∈ {0, 0.1, …, 0.5}·ATR and plot trades/yr
      vs. `SR_lb` — the knee of that curve is the answer to "how many trades do we
      actually want." Cheap to vectorize (`np.where` ladder with ffill).
- [ ] **3.4 Exits decoupled from entries.** Today exit = inverse crossover (and the
      nominal SL/TP never fire — see audit finding). Research separately: time-stop
      (exit after N bars flat-or-losing), ATR trailing stop, breakeven stop after
      +1R. Requires adding stop logic to the vectorized loop (a small numba/cython
      or plain-numpy state loop — position no longer a pure function of the
      indicator), or doing exit research directly in the event engine on a coarse
      grid. **First step regardless: fix the SL/TP units bug-in-practice** — decide
      whether `sl_percent=0.3` was *meant* as 0.3% (then pass 0.003 and re-verify
      MES's hourly noise doesn't stop everything out) or as disabled (then set
      `sl_enabled=False` honestly and stop reporting fictional R-multiples).

## 4. Real cost model (P0, interacts with everything)

Frictionless ranking systematically favors high-turnover combos; this plan
simultaneously *prefers* high trade counts for confidence (§1.2). Only real costs
can arbitrate. These are the PROP_FIRM_PLAN Phase-1 TODOs, now blocking:

- [x] **4.1 Fill in MES facts.** Tick size (CME lists MES at 0.25 index points =
      $1.25/tick; verify against the CME spec sheet and set it in
      `exchange_config.py`), Tradovate round-turn commission for micros (retail is
      roughly $0.35–$0.79/side + exchange & NFA fees; **Tradeify evals route through
      their own account types — source the actual number from Tradeify's spec**, not
      Tradovate retail pricing).
- [x] **4.2 Re-rank the grid with costs on.** The notebook's cost plumbing already
      works (`cost_percent` per side, applied on position changes) — it's just fed
      zeros. With ~1 tick slippage + commission, a 134-trade/yr combo pays ~270
      sides/yr. Compute and report the **break-even cost per side** for each top
      combo (the cost at which its return hits zero) — combos whose break-even is
      under ~1.5 ticks + commission are mirages.
- [ ] **4.3 Slippage realism per session.** 1 tick RTH vs. 2+ ticks overnight is a
      more honest model than a flat number; cheap to implement as a time-of-day
      lookup on the cost series. Feeds §3.2(d)'s session-filter decision.

## 5. Validation & overfitting control (P1 — gates everything before real money)

- [x] **5.1 Hold-out split now.** Even with today's single year: freeze the last ~3
      months as untouched test data, re-run the §1-scored grid on the first ~9
      months only, and report the chosen combo's test-window performance once. This
      is the minimum honesty bar and costs an afternoon.
- [ ] **5.2 Walk-forward optimization.** With §2's longer history: rolling
      (train 12m → trade 3m) windows, re-selecting parameters each step with the §1
      objective; the concatenated out-of-sample segments are the strategy's real
      track record. Report parameter *stability* across windows (do the chosen
      lengths wander wildly? — if yes, the edge is the re-fitting, i.e. probably
      nothing).
- [ ] **5.3 CSCV / PBO.** Combinatorially split the sample into S blocks, and for
      each train/test partition check whether the in-sample-best combo lands in the
      out-of-sample top half. The fraction of times it doesn't = Probability of
      Backtest Overfitting (Bailey et al.). Directly reuses the grid harness;
      deliverable is one number per grid ("this search procedure overfits with
      p=…") that should accompany every future heatmap.
- [ ] **5.4 Vectorized ↔ event-engine parity test.** Run the chosen parameters
      through both notebooks on the identical window with identical costs
      (SL/TP disabled) and assert trade-count and return agree within tolerance. Any
      unexplained gap is a bug in one of them; today the two have never been
      reconciled (different trade counts — 67 vs 136 — are currently explained away
      by sizing/margin differences without proof).

## 6. Signal→position layer (P1)

- [ ] **6.1 Volatility-targeted sizing.** Replace binary 0/1 exposure with
      `min(1, target_vol / realized_vol)` scaling so daily P&L vol is roughly
      constant — the single most effective generic improver of Sharpe/Calmar, and
      for Tradeify it's what keeps a vol spike from eating the 5% buffer in two
      days. Constraint to model honestly: with $50k and MES notional ≈ $35k
      (5 × ~7000 index points), whole-contract rounding gives only ~1–14 contracts
      of granularity depending on leverage — implement in the *event engine*
      (`Rebalancer` already floors to whole contracts) and check the rounding
      doesn't destroy the effect at this account size.
- [ ] **6.2 Signal-strength sizing.** Scale exposure by ER or normalized `ma_x`
      magnitude instead of sign only. Test with §1 objective; keep only if it beats
      binary after costs (it often doesn't at whole-contract granularity).
- [ ] **6.3 Fractional Kelly ceiling.** From the trade distribution (win rate, avg
      win/loss), compute Kelly and cap total exposure at ~¼ Kelly. Mostly a sanity
      bound for §7 sizing rather than a live signal.

## 7. Tradeify-specific research (P1 — this is the actual objective)

For a prop eval, Sharpe is a proxy. The real objective is **P(pass) within the
drawdown rule**, and its complement, expected cost of blown evals.

- [ ] **7.1 Rulebook verification** (open question carried from PROP_FIRM_PLAN
      Phase 3). Pin down from Tradeify's current published rules: EOD-trailing vs.
      intraday-trailing drawdown; whether the trail **locks** at initial balance +
      buffer once reached (common at funded stage); profit target and minimum
      trading days for the eval; **consistency rule** (max % of total profit from a
      single day — this *forces* a minimum trade count / uniformity and directly
      supports the user's many-trades preference); daily loss limit if any; max
      contracts and scaling plan; permitted symbols (FX/livestock question from
      PROP_FIRM_PLAN); news-event and holding-period restrictions. Update
      `TradeifyDrawdownMiddleware` to match reality; everything below assumes it.
- [ ] **7.2 Monte-Carlo eval simulator.** The deliverable that ties this whole plan
      together: block-bootstrap the strategy's daily net returns (or resample its
      trade sequence) thousands of times, run each path through the (verified)
      Tradeify rule set, and output **P(pass), median days-to-pass, P(breach)**.
      Then optimize *parameters and sizing jointly on P(pass)* rather than on
      return. Implementation: pure-pandas/numpy on the event engine's daily equity
      series; the middleware logic already exists to be reused as the rule oracle.
      This is where trade count vs. risk-adjusted return stops being a philosophical
      tradeoff — more, smaller trades usually raise P(pass) at equal expected
      return, and the simulator quantifies exactly how much.
- [ ] **7.3 Drawdown-buffer-aware dynamic sizing.** Size positions off *remaining
      buffer*, not account size: e.g. target daily 2σ ≤ ⅓ of remaining distance to
      the trailing threshold; derisk automatically as the buffer shrinks, re-risk as
      EOD highs rebuild it. Implement as a second middleware (sizing hint) next to
      the existing halt-only middleware; evaluate via §7.2's simulator (it should
      dominate static sizing on P(pass)).
- [ ] **7.4 Exploit the EOD-settlement mechanic.** If the trail truly updates only
      at 5pm ET settlement, intraday equity excursions don't move the threshold —
      which changes what "drawdown" means for intraday-flat vs. swing styles. Test
      both a close-all-by-settlement variant and the current hold-overnight style
      through §7.2; also model the settlement-time equity mark correctly in the
      middleware (currently it uses bar-close equity at day boundary — verify which
      bar that is in a 23h session).
- [ ] **7.5 Multi-symbol eval portfolio.** 18 futures are already configured. Two
      uncorrelated 0.7-Sharpe streams beat one 1.0-Sharpe stream on P(pass) because
      breach probability is driven by the *portfolio's* worst days. Start with
      MES + one rates (ZN-type) or metals micro; measure return-stream correlation
      of the fitted strategies, then re-run §7.2 on the combined equity. This is
      the whole-algo-level optimization the plan builds toward.

## 8. Whole-algo / ensemble level (P2)

- [ ] **8.1 Strategy ensemble done right.** The earlier SMA+KAMA 0.5/0.5 portfolio
      was assembled without checking whether the two return streams were actually
      decorrelated (both long-only trend on the same symbol/timeframe — likely
      >0.8 correlated, so the blend just averaged them). Rule: admit a second
      strategy only if pairwise return correlation < ~0.5 on the validation window;
      weight by inverse risk contribution, not equal split.
- [ ] **8.2 Regime-gating layer.** A cheap classifier (realized-vol tercile +
      ER-trendiness) that switches between strategy variants (or to flat) rather
      than one fixed parameterization. Only after §5's infrastructure exists —
      regime layers are overfitting machines without walk-forward discipline.
- [ ] **8.3 Meta-labeling.** Keep the crossover as the trade *trigger*, train a
      small classifier (features: ER, vol, time-of-day, distance-from-SMA, recent
      win/loss streak) to size-or-skip each signal. Preserves high trade candidacy
      while pruning the worst entries — the ML-flavored version of §3.2, and the
      right first use of `scikit-learn` (already a dependency) in this repo. Needs
      §5.2's walk-forward to be trainable honestly.
- [ ] **8.4 Bayesian/adaptive search instead of exhaustive grids.** Once the
      objective is §1's `SR_lb` (or §7.2's P(pass), which is expensive), swap the
      Cartesian grid for `optuna` TPE with the same multiprocessing harness —
      ~10× fewer evaluations for the same coverage, which matters once each
      evaluation is a Monte-Carlo simulation rather than one vectorized pass.

---

## Suggested execution order

| Step | Items | Why first | Rough size |
|------|-------|-----------|------------|
| 1 | §1.1–1.2, §4.1–4.2 | Re-rank existing grids with risk-adjusted, cost-aware objective — may immediately change the chosen parameters | 1–2 sessions |
| 2 | §3.4 SL/TP audit fix, §5.1 hold-out, §5.4 parity | Honesty fixes; cheap | 1 session |
| 3 | §2 data backfill | Unblocks everything statistical | external-dependent |
| 4 | §1.3–1.5, §5.2–5.3 | Full validation stack on the longer history | 2–3 sessions |
| 5 | §7.1–7.3 | The actual objective (P(pass)) + rule verification | 2–3 sessions |
| 6 | §3.1–3.3, §6 | Indicator & sizing search under the new objective | open-ended |
| 7 | §7.4–7.5, §8 | Whole-algo level | open-ended |

**Definition of done for any candidate strategy:** positive `SR_lb` after real
costs on walk-forward out-of-sample data, PBO < ~30%, and Monte-Carlo P(pass
Tradeify eval) meaningfully above the ~30–40% base rate that would make the eval
fee -EV — with a trade count high enough that all three of those numbers have
tight confidence intervals, which is the resolution of the trade-count question:
*trade count is not an objective, it's what makes every other number believable.*
