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
from backtester.strategies.bollinger_mean_reversion import BollingerMeanReversionStrategy
from backtester.strategies.bollinger_vwap_breakout import BollingerVwapBreakoutStrategy
from backtester.strategies.donchian_breakout import DonchianBreakoutStrategy
from backtester.strategies.keltner_vwap_breakout import KeltnerVwapBreakoutStrategy
from backtester.strategies.ma_crossover import MaCrossoverStrategy
from backtester.strategies.macd_mean_reversion import MacdMeanReversionStrategy
from backtester.strategies.opening_range_breakout import OpeningRangeBreakoutStrategy
from backtester.strategies.pairs_mean_reversion import PairsMeanReversionStrategy
from backtester.strategies.pullback_to_ma import PullbackToMaStrategy
from backtester.strategies.regime_gated import RegimeGatedStrategy
from backtester.strategies.sr_psych_level_breakout import SupportResistanceBreakoutStrategy
from backtester.strategies.supertrend_flip import SuperTrendFlipStrategy
from backtester.strategies.vwap_mean_reversion import VwapMeanReversionStrategy

__all__ = [
    "Allocation",
    "AllocationOrder",
    "AllocationPosition",
    "BollingerMeanReversionStrategy",
    "BollingerVwapBreakoutStrategy",
    "CloseReason",
    "DonchianBreakoutStrategy",
    "KeltnerVwapBreakoutStrategy",
    "MaCrossoverStrategy",
    "MacdMeanReversionStrategy",
    "OpeningRangeBreakoutStrategy",
    "PairsMeanReversionStrategy",
    "PullbackToMaStrategy",
    "RegimeGatedStrategy",
    "Strategy",
    "StrategyDirection",
    "SupportResistanceBreakoutStrategy",
    "SuperTrendFlipStrategy",
    "Trade",
    "TradeResult",
    "VwapMeanReversionStrategy",
]
