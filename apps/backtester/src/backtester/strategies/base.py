"""Base types and abstract Strategy interface for the backtester."""

import copy
from abc import ABC, abstractmethod
from datetime import datetime
from enum import Enum

import pandas as pd

from backtester.exchange import OrderExecutionType, OrderSide, PositionSide
from backtester.market import Market


class CloseReason(Enum):
    signal = "signal"
    tp = "tp"
    sl = "sl"
    convergence = "convergence"
    end_of_test = "end_of_test"


class TradeResult(Enum):
    winner = "winner"
    loser = "loser"


class AllocationPosition:
    def __init__(
        self,
        side: PositionSide,
        symbol: str,
        percent: float,
        average_open_price: float,
    ):
        self.side: PositionSide = side
        self.symbol: str = symbol
        self.percent: float = percent
        self.volume: float = 0
        self.average_open_price: float = average_open_price


class AllocationOrder:
    def __init__(
        self,
        side: OrderSide,
        symbol: str,
        percent: float,
        price: float,
        execution_type: OrderExecutionType,
    ):
        self.side: OrderSide = side
        self.symbol: str = symbol
        self.percent: float = percent
        self.price: float = price
        self.volume: float = 0
        self.execution_type: OrderExecutionType = execution_type


class Allocation:
    def __init__(self):
        self.positions: list[AllocationPosition] = []
        self.orders: list[AllocationOrder] = []


class StrategyDirection(Enum):
    long = "long"
    short = "short"
    both = "both"


class Trade:
    def __init__(self):
        self.symbol: str = ""
        self.side: PositionSide = PositionSide.long
        self.time_open: pd.Timestamp | None = None
        self.time_close: pd.Timestamp | None = None
        self.open_price: float = 0
        self.close_price: float = 0
        self.close_reason: CloseReason | None = None
        self.pnl: float = 0
        self.result: TradeResult | None = None
        self.holding_period: pd.Timedelta | None = None


class Strategy(ABC):
    """Abstract base class for all trading strategies.

    Subclasses must implement :meth:`refresh`, which is called once per candle
    and should update :attr:`allocation` to reflect the desired portfolio state.
    """

    def __init__(self, key: str, market: Market, symbol: str):
        self.key: str = key
        self.allocation: Allocation = Allocation()
        self.market: Market = market
        self.symbol: str = symbol

        self.trade_history: list[Trade] = []
        self.current_trade: Trade = Trade()
        self.allocation_change_time: datetime | None = None

    @abstractmethod
    def refresh(self) -> None:
        """Evaluate current market conditions and update self.allocation."""

    # ------------------------------------------------------------------
    # Trade tracking helpers (concrete — shared by all subclasses)
    # ------------------------------------------------------------------

    def open_trade(self, side: PositionSide, open_price: float) -> None:
        self.current_trade.side = side
        self.current_trade.symbol = self.symbol
        self.current_trade.time_open = self.market.current["time_close"]
        self.current_trade.open_price = open_price

    def close_trade(self, close_price: float, reason: CloseReason) -> None:
        self.current_trade.time_close = self.market.current["time_close"]
        self.current_trade.close_price = close_price
        self.current_trade.close_reason = reason

        if self.current_trade.time_open is not None and self.current_trade.time_close is not None:
            self.current_trade.holding_period = (
                self.current_trade.time_close - self.current_trade.time_open
            )

        if self.current_trade.side == PositionSide.long:
            current, previous = close_price, self.current_trade.open_price
        else:
            current, previous = self.current_trade.open_price, close_price

        pnl = current / previous - 1 if current > 0 and previous > 0 else 0.0
        self.current_trade.pnl = pnl
        self.current_trade.result = TradeResult.winner if pnl > 0 else TradeResult.loser

        self.trade_history.append(copy.deepcopy(self.current_trade))
        self.current_trade = Trade()

    def _mark_allocation_changed(self) -> None:
        self.allocation_change_time = self.market.current["time_close"].to_pydatetime()
