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
    """A desired position within a Strategy's Allocation: side, symbol, and target size
    as a percent of portfolio value (volume is filled in later by the rebalancer)."""

    def __init__(
        self,
        side: PositionSide,
        symbol: str,
        percent: float,
        average_open_price: float,
    ):
        """volume starts at 0 -- the rebalancer computes it from `percent` and the
        current price."""
        self.side: PositionSide = side
        self.symbol: str = symbol
        self.percent: float = percent
        self.volume: float = 0
        self.average_open_price: float = average_open_price


class AllocationOrder:
    """A desired standing order (typically TP/SL) within a Strategy's Allocation --
    volume is filled in later by the rebalancer, same as AllocationPosition."""

    def __init__(
        self,
        side: OrderSide,
        symbol: str,
        percent: float,
        price: float,
        execution_type: OrderExecutionType,
    ):
        """volume starts at 0 -- the rebalancer computes it from `percent` and the
        current price."""
        self.side: OrderSide = side
        self.symbol: str = symbol
        self.percent: float = percent
        self.price: float = price
        self.volume: float = 0
        self.execution_type: OrderExecutionType = execution_type


class Allocation:
    """A strategy's (or portfolio's merged) desired target state: which positions to
    hold and which standing orders (TP/SL) to have open."""

    def __init__(self):
        """Starts empty -- flat, no orders."""
        self.positions: list[AllocationPosition] = []
        self.orders: list[AllocationOrder] = []


class StrategyDirection(Enum):
    long = "long"
    short = "short"
    both = "both"


class Trade:
    """A completed round-trip trade record: open/close price and time, why it closed,
    and its percent PnL (slippage/fee-free -- see Positions.reduce_position_volume's
    realized_pnl_in_usd for the actual exchange-fill-based PnL). risk_percent, if the
    strategy provided one, is the fractional distance originally risked (e.g. its
    stop-loss distance) -- used to express pnl in R-multiples."""

    def __init__(self):
        """Starts as an empty/default trade -- Strategy.open_trade()/close_trade() fill
        it in."""
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
        self.risk_percent: float | None = None


class Strategy(ABC):
    """Abstract base class for all trading strategies.

    Subclasses must implement :meth:`refresh`, which is called once per candle
    and should update :attr:`allocation` to reflect the desired portfolio state.
    """

    def __init__(self, key: str, market: Market, symbol: str):
        """Starts flat (empty Allocation, no trade history)."""
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

    def open_trade(
        self, side: PositionSide, open_price: float, risk_percent: float | None = None
    ) -> None:
        """Starts tracking a new current_trade at the given side/price/time.
        risk_percent, if given, is the fractional distance being risked (e.g. the
        stop-loss distance as a percent of entry price) -- recorded so metrics can
        later express this trade's PnL in R-multiples."""
        self.current_trade.side = side
        self.current_trade.symbol = self.symbol
        self.current_trade.time_open = self.market.current["time_close"]
        self.current_trade.open_price = open_price
        self.current_trade.risk_percent = risk_percent

    def close_trade(self, close_price: float, reason: CloseReason) -> None:
        """Finalizes current_trade: records close price/time/reason/holding period,
        computes its percent PnL and winner/loser result, appends a copy to
        trade_history, and resets current_trade to a fresh empty Trade."""
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
        """Stamps allocation_change_time with the current candle's close time -- this is
        what Portfolio's change-detection hash (and thus Rebalancer.rebalance()) reacts
        to, so subclasses must call this whenever they mutate self.allocation."""
        self.allocation_change_time = self.market.current["time_close"].to_pydatetime()
