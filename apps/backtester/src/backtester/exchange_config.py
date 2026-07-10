"""Tradovate/CME futures per-symbol contract config: point value, tick size (where
verified), min contract size/step, and fee/slippage defaults.

Consumed by TradovateSymbolConfigProvider (backtester/exchange/symbol_config.py), which
Exchange/Orders use in place of the crypto percentage-of-price fee/slippage model when
set -- see Orders.add_order_processing_logic() for exactly where the two modes fork.

Point values are ported from a separately-verified CME-contract-spec reference table
built in a different codebase (trading-system-backend's pointValues.ts) -- re-flagged
here as needing independent re-verification, tracked in PROP_FIRM_PLAN.md. Tick sizes
are NOT yet researched for any symbol below (also tracked there) -- left as None rather
than fabricated.
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


TRADOVATE_FUTURES: dict[str, SymbolConfig] = {
    "ES": SymbolConfig(point_value=50),
    "MES": SymbolConfig(point_value=5),
    "NQ": SymbolConfig(point_value=20),
    "MNQ": SymbolConfig(point_value=2),
    "RTY": SymbolConfig(point_value=50),
    "M2K": SymbolConfig(point_value=5),
    "NKD": SymbolConfig(point_value=5),
    "6E": SymbolConfig(point_value=125_000),
    "M6E": SymbolConfig(point_value=12_500),
    "6B": SymbolConfig(point_value=62_500),
    "6J": SymbolConfig(point_value=12_500_000),
    "6C": SymbolConfig(point_value=100_000),
    "6S": SymbolConfig(point_value=125_000),
    "6A": SymbolConfig(point_value=100_000),
    "M6A": SymbolConfig(point_value=10_000),
    "HE": SymbolConfig(point_value=400),
    "LE": SymbolConfig(point_value=400),
    "GF": SymbolConfig(point_value=500),
    # EMD intentionally excluded: registered in Mongo (source="yahoo") but has no
    # usable daily history (filtered out upstream by ib_portfolio_test.ipynb's
    # MIN_DAILY_ROWS check). See PROP_FIRM_PLAN.md.
}
