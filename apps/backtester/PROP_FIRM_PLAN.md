# Tradovate Futures + Prop-Firm Drawdown — Development Plan

Tracks progress on adding a Tradovate futures exchange (18 CME futures) and an
EOD-trailing-drawdown risk middleware to the backtester. Check items off as completed;
mark skipped items explicitly rather than deleting them, so this stays an accurate
record across sessions.

## Phase 1 — Symbol config

- [x] `exchange_config.py` repurposed with `SymbolConfig` + `TRADOVATE_FUTURES` (18
      symbols; EMD explicitly skipped — registered in Mongo but has no usable data)
- [x] `exchange/symbol_config.py`: `SymbolConfigProvider` Protocol +
      `TradovateSymbolConfigProvider`
- [x] **MES tick_size researched** (2026-07-10): CME contract spec — 0.25 index
      points = $1.25/tick
      ([source](https://www.cmegroup.com/markets/equities/sp/micro-e-mini-sandp-500.contractSpecs.html)).
      Set in `exchange_config.py`.
- [x] **MNQ tick_size researched** (2026-07-10): CME contract spec — 0.25 index
      points = $0.50/tick
      ([source](https://www.cmegroup.com/markets/equities/nasdaq/micro-e-mini-nasdaq-100.contractSpecs.html)).
      Set in `exchange_config.py`.
- [x] **M2K tick_size researched** (2026-07-10): CME contract spec — 0.10 index
      points = $0.50/tick
      ([source](https://www.cmegroup.com/markets/equities/russell/micro-e-mini-russell-2000.html)).
      Set in `exchange_config.py`.
- [x] **M6E tick_size researched** (2026-07-10): CME contract spec — 0.0001 EUR =
      $1.25/tick
      ([source](https://www.cmegroup.com/markets/fx/g10/e-micro-euro.contractSpecs.html)).
      Set in `exchange_config.py`.
- [x] **6J tick_size researched** (2026-07-10): CME contract spec — 0.0000005
      JPY/USD (0.5 pip) = $6.25/tick
      ([source](https://www.cmegroup.com/markets/fx/g10/japanese-yen.contractSpecs.html)).
      Set in `exchange_config.py`.
- [x] **M6A tick_size researched** (2026-07-10): CME contract spec — 0.0001 AUD =
      $1.00/tick
      ([source](https://www.cmegroup.com/markets/fx/g10/australian-dollar.contractSpecs.html)).
      Set in `exchange_config.py`. Remaining 12 symbols still `None` — still TODO.
- [ ] **Research TODO**: verify tick_size for the other 12 symbols against CME spec
      sheets (`get_slippage()` raises for any symbol until its tick size is filled in,
      `slippage_ticks=0` is the documented temporary workaround)
- [ ] **Research TODO**: re-verify point values against CME specs directly (currently
      ported from a different codebase's `pointValues.ts`, not re-derived here)
- [x] **MES/MNQ per-contract commission researched** (2026-07-10): Tradeify charges
      $1.82 round-turn on both, explicitly named
      ([source](https://proptradingvibes.com/blog/tradeify-commission-fees)). Set as
      `fee_per_contract_override=0.91` (half the round-turn — `get_fee()` is charged
      once per fill/side, not once per round trip) in `exchange_config.py`.
- [x] **M2K/M6E/M6A per-contract commission assumed, not separately confirmed**
      (2026-07-10): the same source describes $1.82 round-turn for "micro contracts
      like MES and MNQ" but doesn't separately name these three. Set
      `fee_per_contract_override=0.91` on the assumption the standard micro rate
      applies — worth confirming against Tradeify's own pricing reference directly if
      any of these three's numbers ever look off.
- [x] **6J per-contract commission assumed, not separately confirmed** (2026-07-10):
      6J is a full-size (not micro) contract; Tradeify's fee source gives
      "standard contract" round-turn as ~$3.04-3.12 without naming 6J specifically.
      Set `fee_per_contract_override=1.54` (the ~$3.08 midpoint, halved) as an
      approximation — same caveat as above, worth confirming directly.
      Remaining 12 symbols still use the `0.0` provider default — still TODO.

## Phase 2 — Exchange/Orders/Positions/Rebalancer integration

- [x] `Exchange.symbol_config_provider` constructor param + `get_point_value()`/
      `round_position_size()`
- [x] `Orders.add_order_processing_logic()` provider branch (absolute fee/slippage,
      bypasses percentage math; sell/buy asset/volume left untouched — confirmed dead
      for `MarketType.future`, which Tradovate always uses)
- [x] `Positions.calculate_required_margin`/`create_position`/`increase_position_volume`/
      `reduce_position_volume`/`refresh_position` made point-value-aware
- [x] `Rebalancer.rebalance()` point-value-aware sizing + floor-to-whole-contract +
      skip-if-zero (never forces a minimum-1-contract trade)
- [x] Golden tests re-run clean (exact log count, exact PnL/Sharpe asserts) with no
      provider set — crypto path is a byte-for-byte no-op
- [x] New unit tests (symbol config, point-value math, rebalancer rounding) passing

## Phase 3 — Risk middleware

- [x] `TradeifyDrawdownMiddleware` (EOD trailing, configurable `drawdown_percent`
      (default 5%), flag-and-halt semantics, real balance/equity never mutated)
- [x] Unit tests: trailing-up on new EOD highs, breach detection, permanent halt across
      further day boundaries, day-1 seeding never itself breaches
- [x] **Rulebook researched** (2026-07-10, Tradeify Growth Evaluation, $50k account —
      [source](https://help.tradeify.co/en/articles/10495897-rules-trailing-max-drawdowns),
      [source](https://help.tradeify.co/en/articles/10495915-growth-evaluation-accounts)):
      drawdown **is** EOD-trailing as implemented, confirming the earlier assumption —
      but two mechanics aren't modeled yet:
      - Real number for $50k is **$2,000 (4%)**, not this middleware's 5% default —
        the notebook's own instantiation should pass `drawdown_percent=0.04`, though
        the class default is left at 0.05 as a generic value (not itself wrong, just
        not Tradeify-$50k-specific).
      - **Trail locks** once EOD balance reaches `initial + drawdown + $100` (e.g.
        $52,100 for a $50k/$2k account) — floor freezes there permanently
        (e.g. $50,100) rather than continuing to trail upward. Not implemented:
        `TradeifyDrawdownMiddleware` currently trails forever. See
        `OPTIMIZATION_RESEARCH_PLAN.md` §7.1/§7.4 for the follow-up.
      - Also confirmed: $3,000 profit target, $1,250 daily loss limit (soft
        pause, not a hard fail — also not modeled), no consistency rule on Growth
        specifically (Select uses a 40% rule instead — also not modeled).

## Phase 4 — Shared Mongo/Timescale fetch utility

- [x] `data_aggregator/mongo_timescale_aggregator.py`: `fetch_instruments`/
      `fetch_candles`/`market_dataframe_from_candles`/`fetch_market_data`,
      parameterized by `source`/`resolution`
- [x] Pure-transform test for `market_dataframe_from_candles()`
- [ ] `fetch_instruments`/`fetch_candles`/`fetch_market_data` are untested against a
      real DB (matches existing precedent — `binance_aggregator.py`/
      `kraken_aggregator.py` also have zero test coverage) — verify manually against a
      running Mongo/Timescale instance before relying on this in production
- [ ] `ib_portfolio_test.ipynb` optionally migrated to use the shared module instead of
      its own inline fetch functions (not required — its own intro cell already flags
      this as the eventual plan, but migrating it is out of scope for this phase)

## Phase 5 — New notebook

- [x] `notebooks/test_backtester_mes_tradovate.ipynb`: MES 1h, `source="ib"`,
      MA-crossover, `TradeifyDrawdownMiddleware`
- [ ] Run end-to-end against a live Mongo/Timescale instance to confirm the fetch →
      `Market.compile()` → backtest → drawdown-middleware wiring actually works
      together — not covered by any unit test

## Skipped / explicitly out of scope

- [x] EMD — registered in Mongo (`source="yahoo"`) but has no usable daily history
      (filtered out upstream by `ib_portfolio_test.ipynb`'s `MIN_DAILY_ROWS` check) —
      not included in `TRADOVATE_FUTURES`
- [ ] FX/livestock CME futures (6E, 6B, 6J, 6C, 6S, 6A, M6A, M6E, HE, LE, GF) are
      configured alongside the equity-index futures since the user asked for "all
      symbols" from the notebook, but it's not confirmed whether Tradeify's evaluation
      rules actually permit trading all of these — worth checking before using them in
      a real evaluation simulation
