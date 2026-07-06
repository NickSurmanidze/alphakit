"""Strategies package.

Public API — import from here, not from sub-modules:

    from backtester.strategies import Strategy, MaCrossoverStrategy, Allocation, ...
"""

from backtester.strategies.base import (
    Allocation,
    AllocationOrder,
    AllocationPosition,
    CloseReason,
    Strategy,
    StrategyDirection,
    Trade,
    TradeResult,
)
from backtester.strategies.ma_crossover import MaCrossoverStrategy

__all__ = [
    "Allocation",
    "AllocationOrder",
    "AllocationPosition",
    "CloseReason",
    "MaCrossoverStrategy",
    "Strategy",
    "StrategyDirection",
    "Trade",
    "TradeResult",
]
