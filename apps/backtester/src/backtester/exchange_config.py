"""Tradovate/CME futures per-symbol contract config: point value, tick size (where
verified), min contract size/step, and fee/slippage defaults.

Consumed by TradovateSymbolConfigProvider (backtester/exchange/symbol_config.py), which
Exchange/Orders use in place of the crypto percentage-of-price fee/slippage model when
set -- see Orders.add_order_processing_logic() for exactly where the two modes fork.

Point values are ported from a separately-verified CME-contract-spec reference table
built in a different codebase (trading-system-backend's pointValues.ts) -- re-flagged
here as needing independent re-verification, tracked in PROP_FIRM_PLAN.md. Tick sizes
are NOT yet researched for any symbol below except MES/MNQ/M2K/M6E/6J/M6A (also
tracked there) -- left as None rather than fabricated.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class SymbolConfig:
    """One futures product's contract spec: how much a 1.0 price-point move is worth
    per contract, its minimum tradeable/incrementable size, and its fee/slippage
    defaults."""

    point_value: float
    tick_size: float | None = None  # None = not yet researched
    min_position_size: float = 1.0  # contracts
    min_order_step: float = 1.0  # contracts
    fee_per_contract_override: float | None = None  # None = use provider default
    slippage_ticks: float = 1.0
    # Hard ceiling on contracts per position, independent of whatever the percent-of-
    # equity sizing formula computes. None = no ceiling (the default -- percent-based
    # sizing alone decides). Needed because Rebalancer sizes off *current* (compounding)
    # equity: a weight tuned to floor to exactly 1 contract at the account's starting
    # balance can size larger once equity has grown, or price has fallen, enough -- this
    # catches that case rather than relying on the weight tuning to hold forever.
    max_position_size: float | None = None


TRADOVATE_FUTURES: dict[str, SymbolConfig] = {
    "ES": SymbolConfig(point_value=50),
    # tick_size=0.25 index points ($1.25/tick) -- CME contract spec, verified 2026-07-10
    # (https://www.cmegroup.com/markets/equities/sp/micro-e-mini-sandp-500.contractSpecs.html).
    # fee_per_contract_override=0.91 -- half of Tradeify's $1.82 MES round-turn
    # commission (per PROP_FIRM_PLAN.md/OPTIMIZATION_RESEARCH_PLAN.md research), since
    # get_fee() here is charged once per fill (one side), not once per round trip.
    "MES": SymbolConfig(point_value=5, tick_size=0.25, fee_per_contract_override=0.91),
    "NQ": SymbolConfig(point_value=20),
    # tick_size=0.25 index points ($0.50/tick) -- CME contract spec, verified 2026-07-10
    # (https://www.cmegroup.com/markets/equities/nasdaq/micro-e-mini-nasdaq-100.contractSpecs.html).
    # fee_per_contract_override=0.91 -- half of Tradeify's confirmed $1.82 MNQ
    # round-turn commission (same source as MES's, explicitly names MNQ).
    "MNQ": SymbolConfig(point_value=2, tick_size=0.25, fee_per_contract_override=0.91),
    "RTY": SymbolConfig(point_value=50),
    # tick_size=0.10 index points ($0.50/tick) -- CME contract spec, verified 2026-07-10
    # (https://www.cmegroup.com/markets/equities/russell/micro-e-mini-russell-2000.html).
    # fee_per_contract_override=0.91 -- Tradeify's fee source confirms $1.82 round-turn
    # for "micro contracts like MES and MNQ" but doesn't separately name M2K; this
    # assumes the same standard-micro rate applies rather than citing a M2K-specific
    # number -- flagged in PROP_FIRM_PLAN.md as an assumption, not a confirmed fact.
    "M2K": SymbolConfig(point_value=5, tick_size=0.10, fee_per_contract_override=0.91),
    "NKD": SymbolConfig(point_value=5),
    "6E": SymbolConfig(point_value=125_000),
    # tick_size=0.0001 EUR ($1.25/tick) -- CME contract spec, verified 2026-07-10
    # (https://www.cmegroup.com/markets/fx/g10/e-micro-euro.contractSpecs.html).
    # fee_per_contract_override=0.91 -- assumed standard-micro rate (same as MES/MNQ),
    # not separately confirmed for M6E specifically -- same honesty caveat as M2K's.
    "M6E": SymbolConfig(point_value=12_500, tick_size=0.0001, fee_per_contract_override=0.91),
    "6B": SymbolConfig(point_value=62_500),
    # tick_size=0.0000005 JPY/USD (0.5 pip, $6.25/tick) -- CME contract spec, verified
    # 2026-07-10 (https://www.cmegroup.com/markets/fx/g10/japanese-yen.contractSpecs.html).
    # fee_per_contract_override=1.54 -- 6J is a full-size (not micro) contract;
    # Tradeify's fee source gives standard-contract round-turn as ~$3.04-3.12, this
    # uses the ~$3.08 midpoint -> $1.54/side. Approximate, not a confirmed exact
    # number -- flagged the same way as M2K's/M6E's/M6A's assumed rates.
    "6J": SymbolConfig(point_value=12_500_000, tick_size=0.0000005, fee_per_contract_override=1.54),
    "6C": SymbolConfig(point_value=100_000),
    "6S": SymbolConfig(point_value=125_000),
    "6A": SymbolConfig(point_value=100_000),
    # tick_size=0.0001 AUD ($1.00/tick) -- CME contract spec, verified 2026-07-10
    # (https://www.cmegroup.com/markets/fx/g10/australian-dollar.contractSpecs.html --
    # M6A is 1/10th-size vs. 6A, tick size/value are per-contract not per-unit).
    # fee_per_contract_override=0.91 -- assumed standard-micro rate, same caveat as M6E.
    "M6A": SymbolConfig(point_value=10_000, tick_size=0.0001, fee_per_contract_override=0.91),
    "HE": SymbolConfig(point_value=400),
    "LE": SymbolConfig(point_value=400),
    "GF": SymbolConfig(point_value=500),
    # EMD intentionally excluded: registered in Mongo (source="yahoo") but has no
    # usable daily history (filtered out upstream by ib_portfolio_test.ipynb's
    # MIN_DAILY_ROWS check). See PROP_FIRM_PLAN.md.
}
