"""Shared enums, TypedDicts, and type aliases for the exchange package."""

from datetime import datetime
from enum import Enum
from typing import Any, TypedDict

TransactionRecord = dict[str, Any]


class AssetBalance(TypedDict):
    volume: float
    value_in_usd: float


class AssetBalances(TypedDict):
    free: AssetBalance
    used: AssetBalance
    total: AssetBalance


ExchangeBalance = dict[str, AssetBalances]


class PositionSide(Enum):
    long = "long"
    short = "short"


# spot balances are modelled as positions too
class PositionMarketType(Enum):
    future = "future"
    margin = "margin"


class PositionStatus(Enum):
    open = "open"
    closed = "closed"


class OrderSide(Enum):
    buy = "buy"
    sell = "sell"


class OrderStatus(Enum):
    open = "open"
    closed = "closed"
    canceled = "canceled"
    failed = "failed"


class OrderExecutionType(Enum):
    market = "market"
    limit = "limit"
    stoplossLimit = "stoplossLimit"


class MarketType(Enum):
    future = "future"
    spot = "spot"
    margin = "margin"


class MarginAllocationType(Enum):
    cross = "cross"
    isolated = "isolated"


class Candle(TypedDict):
    time_open: datetime
    time_close: datetime
    open: float
    high: float
    low: float
    close: float


CurrentMarketData = dict[str, Candle]
Log = dict[str, str]
