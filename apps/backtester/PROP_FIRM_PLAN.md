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
- [ ] **Research TODO**: verify tick_size for all 18 symbols against CME spec sheets
      (not yet done — every `SymbolConfig.tick_size` is `None`; `get_slippage()` raises
      for any symbol until its tick size is filled in, `slippage_ticks=0` is the
      documented temporary workaround)
- [ ] **Research TODO**: re-verify point values against CME specs directly (currently
      ported from a different codebase's `pointValues.ts`, not re-derived here)
- [ ] **Research TODO**: Tradovate's real per-contract commission — no real number
      sourced yet, `TradovateSymbolConfigProvider.default_fee_per_contract` defaults to
      `0.0` as an explicit placeholder

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
- [ ] **Open question, not yet resolved**: Tradeify's real drawdown mechanic may not be
      purely EOD-trailing (some prop firms use intraday trailing, or a static/locked
      threshold after reaching a profit target) — current implementation is the
      confirmed-with-user EOD-trailing model; revisit against Tradeify's actual current
      rulebook before relying on this for a real evaluation simulation

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
