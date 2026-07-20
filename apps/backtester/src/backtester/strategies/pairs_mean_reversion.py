"""Two-leg cointegrated-pair mean-reversion strategy: enters/exits both legs of a
price spread together, driven by a precomputed rolling z-score indicator."""

import pandas as pd

from backtester.exchange import PositionSide
from backtester.market import Market
from backtester.strategies.base import (
    Allocation,
    AllocationPosition,
    CloseReason,
    Strategy,
    StrategyDirection,
    Trade,
    TradeResult,
)


class PairsMeanReversionStrategy(Strategy):
    """Trades the spread between `symbol` and `symbol_b` off a precomputed rolling
    z-score indicator (`zscore_indicator_key`, registered against `symbol` via
    Market.add_indicator -- this strategy doesn't compute the spread/z-score itself,
    same convention as VwapMeanReversionStrategy consuming a precomputed indicator
    rather than owning the statistics).

    "Long the spread" = long `symbol`, short `symbol_b` (entered when z <= -entry_z,
    betting the spread reverts up). "Short the spread" = short `symbol`, long
    `symbol_b` (entered when z >= entry_z). Exits when z reverts inside +/-exit_z of
    zero, or a hard stop_z blowout. Both legs are sized at `percent=1` (full
    notional each, same convention MaCrossoverStrategy/VwapMeanReversionStrategy use
    for a single-symbol position) -- i.e. dollar-neutral equal notional per leg, not
    a hedge-ratio-scaled notional split. The rolling regression used to build the
    z-score indicator already captures the statistical hedge ratio for *signal*
    purposes; this keeps *position sizing* simple and doesn't additionally scale
    contract counts by that same beta.

    The stop is expressed in z-score units, not a price percent: unlike a
    single-symbol strategy's sl_percent, a symmetric price-percent stop doesn't mean
    anything across two different-priced, different-point-value legs, and it's the
    spread's own divergence -- not either leg's price move alone -- that this
    strategy is actually betting against. Checked once per candle against the
    z-score at that candle's close (not a resting order), so an intra-bar spike
    beyond stop_z that reverts before the candle closes won't trigger it.
    """

    def __init__(  # noqa: PLR0913
        self,
        key: str,
        market: Market,
        symbol: str,
        symbol_b: str,
        zscore_indicator_key: str,
        entry_z: float,
        exit_z: float,
        stop_z: float,
        direction: StrategyDirection = StrategyDirection.both,
    ):
        super().__init__(key=key, market=market, symbol=symbol)
        self.symbol_b: str = symbol_b
        self.zscore_indicator_key: str = zscore_indicator_key
        self.entry_z: float = entry_z
        self.exit_z: float = exit_z
        self.stop_z: float = stop_z
        self.direction: StrategyDirection = direction

        # "long"/"short" (the spread's side) or None -- distinct from PositionSide,
        # since a spread position is two legs on opposite sides, not one.
        self._spread_side: str | None = None
        # Two concurrent per-leg trades -- the base class's single current_trade slot
        # can't hold both legs open at once.
        self._trade_a: Trade | None = None
        self._trade_b: Trade | None = None

    # ------------------------------------------------------------------
    # Strategy interface
    # ------------------------------------------------------------------

    def refresh(self) -> None:
        z = self._zscore()
        if z is None or pd.isna(z):
            return

        if self._spread_side is None:
            self._check_entry(z)
        else:
            self._check_exit(z)

    # ------------------------------------------------------------------
    # Entry / exit checks
    # ------------------------------------------------------------------

    def _check_entry(self, z: float) -> None:
        can_short_spread = self.direction in {StrategyDirection.both, StrategyDirection.short}
        can_long_spread = self.direction in {StrategyDirection.both, StrategyDirection.long}

        if z >= self.entry_z and can_short_spread:
            self._enter("short")
        elif z <= -self.entry_z and can_long_spread:
            self._enter("long")

    def _check_exit(self, z: float) -> None:
        blown_stop = abs(z) >= self.stop_z
        reverted = (self._spread_side == "short" and z <= self.exit_z) or (
            self._spread_side == "long" and z >= -self.exit_z
        )
        if blown_stop:
            self._exit(CloseReason.sl)
        elif reverted:
            self._exit(CloseReason.signal)

    # ------------------------------------------------------------------
    # Allocation / trade-tracking helpers
    # ------------------------------------------------------------------

    def _zscore(self) -> float | None:
        return self.market.current[self.symbol]["indicators"].get(self.zscore_indicator_key)

    def _price(self, symbol: str) -> float:
        return self.market.current[symbol]["close"]

    def _enter(self, spread_side: str) -> None:
        side_a, side_b = (
            (PositionSide.long, PositionSide.short)
            if spread_side == "long"
            else (PositionSide.short, PositionSide.long)
        )
        self._spread_side = spread_side
        self._trade_a = self._open_leg(self.symbol, side_a)
        self._trade_b = self._open_leg(self.symbol_b, side_b)

        self.allocation = Allocation()
        self.allocation.positions = [
            AllocationPosition(
                side=side_a, symbol=self.symbol, percent=1, average_open_price=self._price(self.symbol)
            ),
            AllocationPosition(
                side=side_b, symbol=self.symbol_b, percent=1, average_open_price=self._price(self.symbol_b)
            ),
        ]
        self._mark_allocation_changed()

    def _exit(self, reason: CloseReason) -> None:
        self._close_leg(self._trade_a, reason)
        self._close_leg(self._trade_b, reason)
        self._trade_a = None
        self._trade_b = None
        self._spread_side = None

        self.allocation = Allocation()
        self._mark_allocation_changed()

    def _open_leg(self, symbol: str, side: PositionSide) -> Trade:
        trade = Trade()
        trade.symbol = symbol
        trade.side = side
        trade.time_open = self.market.current["time_close"]
        trade.open_price = self._price(symbol)
        return trade

    def _close_leg(self, trade: Trade, reason: CloseReason) -> None:
        trade.time_close = self.market.current["time_close"]
        trade.close_price = self._price(trade.symbol)
        trade.close_reason = reason
        trade.holding_period = trade.time_close - trade.time_open

        if trade.side == PositionSide.long:
            current, previous = trade.close_price, trade.open_price
        else:
            current, previous = trade.open_price, trade.close_price
        trade.pnl = current / previous - 1 if current > 0 and previous > 0 else 0.0
        trade.result = TradeResult.winner if trade.pnl > 0 else TradeResult.loser

        self.trade_history.append(trade)
