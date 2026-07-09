"""Exchange: top-level simulation class that wires balance, positions, and orders together."""

from backtester.exchange.balance import Balance, Transactions
from backtester.exchange.event_log import EventLog
from backtester.exchange.order import Orders
from backtester.exchange.position import Positions
from backtester.exchange.types import (
    Log,
    MarginAllocationType,
    MarketType,
    PositionSide,
)
from backtester.market import Market


class Exchange:
    """Top-level simulation surface: wires Balance/Transactions/Orders/Positions
    together and provides the shared pricing/exposure helpers they all call through."""

    def __init__(  # noqa: PLR0913
        self,
        market: Market,
        slippage: float,
        maker_fee: float,
        taker_fee: float,
        market_type: MarketType,
        max_leverage: int = 1,
        margin_allocation_type: MarginAllocationType = MarginAllocationType.isolated,
        event_log_enabled: bool = True,
    ):
        """Builds fresh Balance/Transactions/Orders/Positions/EventLog sub-objects bound
        to this exchange instance."""
        self.market: Market = market
        self.slippage: float = slippage
        self.maker_fee: float = maker_fee
        self.taker_fee: float = taker_fee
        self.market_type: MarketType = market_type
        self.max_leverage: int = max_leverage
        self.margin_allocation_type: MarginAllocationType = margin_allocation_type

        self.logs: list[Log] = []
        self.event_log = EventLog(enabled=event_log_enabled)

        self.balance = Balance(exchange=self)
        self.transactions = Transactions(exchange=self)
        self.orders = Orders(exchange=self)
        self.positions = Positions(exchange=self)

    def convert_asset_volume(self, volume: float, from_asset: str, to_asset: str) -> float:
        """Converts `volume` units of `from_asset` into `to_asset` using the current
        candle's close price for whichever of `from/to` or `to/from` is a registered
        market (identity if the assets are the same; raises if neither pair exists)."""
        symbol = f"{from_asset.upper()}/{to_asset.upper()}"
        reversed_symbol = f"{to_asset.upper()}/{from_asset.upper()}"
        if from_asset == to_asset:
            return volume

        if symbol in self.market.current:
            return float(self.market.current[symbol]["close"]) * volume
        elif reversed_symbol in self.market.current:
            return (1 / float(self.market.current[reversed_symbol]["close"])) * volume
        else:
            raise ValueError(f"{symbol} symbol does not exist in current market data dict.")

    def get_market_price(self, symbol: str) -> float:
        """Returns the current candle's close price for `symbol` (raises if not in the
        current market data)."""
        if symbol not in self.market.current:
            raise ValueError(f"{symbol} symbol does not exist in current market data dict.")
        return float(self.market.current[symbol]["close"])

    def get_market_candle(self, symbol: str):
        """Returns the full current candle dict (OHLC + indicators) for `symbol`."""
        if symbol not in self.market.current:
            raise ValueError(f"{symbol} symbol does not exist in current market data dict.")
        return self.market.current[symbol]

    def add_log(self, message: str) -> None:
        """Appends a coarse timestamped text log entry (currently only used by
        Rebalancer.rebalance() to record each rebalance event)."""
        self.logs.append({"time": self.market.current["time_close"], "message": message})

    def get_logs(self) -> list[Log]:
        """Returns every coarse text log entry recorded so far."""
        return self.logs

    def get_asset_total_in_usd(self) -> float:
        """Total account equity: cash balance plus every open position's unrealized PnL."""
        return self.balance.get_total_balance_in_usd() + self.positions.get_total_unrealized_pnl()

    def get_exposure(self) -> dict[str, float]:
        """Computes current long/short/gross/net exposure (as fractions of total
        balance, plus the raw USD amounts) from open positions."""
        exposures: dict[str, float] = {
            "long": 0.0,
            "short": 0.0,
            "gross": 0.0,
            "net": 0.0,
            "long_in_usd": 0.0,
            "short_in_usd": 0.0,
        }

        balance = self.balance.get_total_balance_in_usd()
        if not balance:
            return exposures

        for p in self.positions.open_positions.values():
            if p.side == PositionSide.long:
                exposures["long_in_usd"] += p.value_in_usd
            else:
                exposures["short_in_usd"] += p.value_in_usd

        exposures["long"] = exposures["long_in_usd"] / balance
        exposures["short"] = exposures["short_in_usd"] / balance
        exposures["gross"] = exposures["long"] + exposures["short"]
        exposures["net"] = abs(exposures["long"] - exposures["short"])

        return exposures

    def run_step(self) -> None:
        """Processes the current candle: refreshes USD-denominated balance values,
        matches/fills any open orders whose trigger was hit, and marks open positions to
        market (checking liquidations)."""
        self.balance.refresh_balance_usd_values()
        self.orders.refresh_open_orders()
        self.positions.refresh_open_positions()
