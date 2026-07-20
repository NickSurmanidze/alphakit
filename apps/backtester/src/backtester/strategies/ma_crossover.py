"""MA-crossover strategy: enters long/short when a fast MA crosses a slow MA."""

import pandas as pd

from backtester.exchange import OrderExecutionType, OrderSide, PositionSide
from backtester.market import Market
from backtester.strategies.base import (
    Allocation,
    AllocationOrder,
    AllocationPosition,
    CloseReason,
    Strategy,
    StrategyDirection,
)


class MaCrossoverStrategy(Strategy):
    """Enters long/short when a fast moving-average indicator crosses a slow one, and
    exits on the reverse cross, a take-profit fill, or a stop-loss fill.

    Bracket sizing has two modes:
    - **Fixed-percent (default)**: tp_percent/sl_percent are fractional distances
      from entry price (0.02 = 2%), the original behavior.
    - **ATR-scaled**: if `bracket_atr_key` is given, SL/TP sit at
      `sl_atr_mult`/`tp_atr_mult` x that indicator's value (a lagged daily ATR,
      precomputed by the caller) away from entry -- the bracket's *meaning* stays
      constant across volatility regimes instead of a fixed percent meaning
      something different in 2020 vol vs 2024 vol. If the ATR value is NaN/
      non-positive at entry time (indicator warmup), the trade is entered
      *without* a bracket rather than skipped -- the crossover signal itself is
      unchanged; only the risk overlay is unavailable for that trade.
    """

    def __init__(  # noqa: PLR0913
        self,
        key: str,
        market: Market,
        symbol: str,
        direction: StrategyDirection,
        slow_indicator_key: str,
        fast_indicator_key: str,
        tp_percent: float = 0.1,
        sl_percent: float = 0.1,
        sl_enabled: bool = True,
        tp_enabled: bool = True,
        bracket_atr_key: str | None = None,
        sl_atr_mult: float = 1.0,
        tp_atr_mult: float = 1.5,
    ):
        """direction controls which crosses are actually traded (long-only,
        short-only, or both); tp_percent/sl_percent are fractional distances from entry
        price (ignored when bracket_atr_key puts the bracket in ATR mode)."""
        super().__init__(key, market, symbol)
        self.direction: StrategyDirection = direction
        self.slow_indicator_key: str = slow_indicator_key
        self.fast_indicator_key: str = fast_indicator_key
        self.tp_percent: float = tp_percent
        self.sl_percent: float = sl_percent
        self.tp_price: float = 0
        self.sl_price: float = 0
        self.sl_enabled: bool = sl_enabled
        self.tp_enabled: bool = tp_enabled
        self.bracket_atr_key: str | None = bracket_atr_key
        self.sl_atr_mult: float = sl_atr_mult
        self.tp_atr_mult: float = tp_atr_mult
        # Per-trade: False when ATR mode couldn't price a bracket at entry time
        # (warmup NaN) -- gates order creation and SL/TP checks for that trade.
        self._bracket_active: bool = True

    # ------------------------------------------------------------------
    # Strategy interface
    # ------------------------------------------------------------------

    def refresh(self) -> None:
        """Compares this candle's fast/slow indicator values against the previous
        candle's to detect a cross; on a cross, exits any current position and enters
        the new direction (if enabled). Otherwise checks SL/TP against the current
        candle's high/low. No-ops on the first candle or while indicators are still
        warming up (falsy values)."""
        candle_num = self.market.current["num"]
        if candle_num == 0:
            return

        indicators = self.market.current[self.symbol]["indicators"]
        prev_indicators = self.market.get_market_by_num(candle_num - 1)[self.symbol]["indicators"]

        cur_slow = indicators[self.slow_indicator_key]
        cur_fast = indicators[self.fast_indicator_key]
        prev_slow = prev_indicators[self.slow_indicator_key]
        prev_fast = prev_indicators[self.fast_indicator_key]

        if not (cur_slow and cur_fast and prev_slow and prev_fast):
            return

        price = self.market.current[self.symbol]["close"]

        if cur_slow < cur_fast and prev_slow > prev_fast:
            self._handle_cross_up(price)
        elif cur_slow > cur_fast and prev_slow < prev_fast:
            self._handle_cross_down(price)
        else:
            self._check_long_sl_tp(price)
            self._check_short_sl_tp(price)

    # ------------------------------------------------------------------
    # Cross-event handlers
    # ------------------------------------------------------------------

    def _handle_cross_up(self, price: float) -> None:
        """Fast MA crossed above slow MA."""
        self._exit_current(price, CloseReason.signal)
        if self.direction in {StrategyDirection.both, StrategyDirection.long}:
            self._set_bracket_prices(PositionSide.long, price)
            self._enter(PositionSide.long, price)

    def _handle_cross_down(self, price: float) -> None:
        """Fast MA crossed below slow MA."""
        self._exit_current(price, CloseReason.signal)
        if self.direction in {StrategyDirection.both, StrategyDirection.short}:
            self._set_bracket_prices(PositionSide.short, price)
            self._enter(PositionSide.short, price)

    def _set_bracket_prices(self, side: PositionSide, price: float) -> None:
        """Prices this trade's SL/TP levels in whichever bracket mode is active, and
        sets _bracket_active accordingly (see class docstring for the ATR-mode NaN
        behavior)."""
        if self.bracket_atr_key is None:
            self._bracket_active = True
            if side == PositionSide.long:
                self.tp_price = price * (1 + self.tp_percent)
                self.sl_price = price * (1 - self.sl_percent)
            else:
                self.tp_price = price * (1 - self.tp_percent)
                self.sl_price = price * (1 + self.sl_percent)
            return

        atr = self.market.current[self.symbol]["indicators"].get(self.bracket_atr_key)
        if atr is None or pd.isna(atr) or atr <= 0:
            self._bracket_active = False
            return
        self._bracket_active = True
        if side == PositionSide.long:
            self.sl_price = price - self.sl_atr_mult * atr
            self.tp_price = price + self.tp_atr_mult * atr
        else:
            self.sl_price = price + self.sl_atr_mult * atr
            self.tp_price = price - self.tp_atr_mult * atr

    # ------------------------------------------------------------------
    # SL / TP checks
    # ------------------------------------------------------------------

    def _check_long_sl_tp(self, price: float) -> None:  # noqa: ARG002
        """Exits the current long position if this candle's low hit the stop-loss or
        its high hit the take-profit (no-op if not currently long)."""
        if not self._bracket_active:
            return
        if not self.allocation.positions:
            return
        if self.allocation.positions[0].side != PositionSide.long:
            return
        candle = self.market.current[self.symbol]
        if self.sl_enabled and candle["low"] <= self.sl_price:
            self._exit_current(self.sl_price, CloseReason.sl)
        elif self.tp_enabled and candle["high"] >= self.tp_price:
            self._exit_current(self.tp_price, CloseReason.tp)

    def _check_short_sl_tp(self, price: float) -> None:  # noqa: ARG002
        """Exits the current short position if this candle's high hit the stop-loss or
        its low hit the take-profit (no-op if not currently short)."""
        if not self._bracket_active:
            return
        if not self.allocation.positions:
            return
        if self.allocation.positions[0].side != PositionSide.short:
            return
        candle = self.market.current[self.symbol]
        if self.sl_enabled and candle["high"] >= self.sl_price:
            self._exit_current(self.sl_price, CloseReason.sl)
        elif self.tp_enabled and candle["low"] <= self.tp_price:
            self._exit_current(self.tp_price, CloseReason.tp)

    # ------------------------------------------------------------------
    # Allocation helpers
    # ------------------------------------------------------------------

    def _exit_current(self, price: float, reason: CloseReason) -> None:
        """Flattens the allocation (no-op if already flat) and records the close on the
        current trade."""
        if not self.allocation.positions:
            return
        self.allocation = Allocation()
        self._mark_allocation_changed()
        self.close_trade(close_price=price, reason=reason)

    def _enter(self, side: PositionSide, price: float) -> None:
        """Sets the allocation to a full-size position on `side` plus its TP/SL orders
        (if enabled and priceable this trade), and starts tracking a new trade."""
        tp_order = self._make_tp_order(side, price)
        sl_order = self._make_sl_order(side, price)

        self.allocation = Allocation()
        self.allocation.positions = [
            AllocationPosition(side=side, symbol=self.symbol, percent=1, average_open_price=price)
        ]
        if tp_order:
            self.allocation.orders.append(tp_order)
        if sl_order:
            self.allocation.orders.append(sl_order)
        self._mark_allocation_changed()
        if self.sl_enabled and self._bracket_active:
            # Fixed mode: sl_percent IS the fractional stop distance. ATR mode:
            # derive it from the actual priced stop so R-multiples stay meaningful.
            if self.bracket_atr_key is None:
                risk_percent = self.sl_percent
            else:
                risk_percent = abs(price - self.sl_price) / price
        else:
            risk_percent = None
        self.open_trade(side=side, open_price=price, risk_percent=risk_percent)

    def _make_tp_order(self, side: PositionSide, price: float) -> AllocationOrder | None:
        """Builds the take-profit limit order for a new `side` position at `price`, or
        None if take-profit is disabled or this trade's bracket couldn't be priced."""
        if not self.tp_enabled or not self._bracket_active:
            return None
        order_side = OrderSide.sell if side == PositionSide.long else OrderSide.buy
        return AllocationOrder(
            side=order_side,
            symbol=self.symbol,
            price=self.tp_price,
            percent=1,
            execution_type=OrderExecutionType.limit,
        )

    def _make_sl_order(self, side: PositionSide, price: float) -> AllocationOrder | None:  # noqa: ARG002
        """Builds the stop-loss stoploss-limit order for a new `side` position at
        `price`, or None if stop-loss is disabled or this trade's bracket couldn't
        be priced."""
        if not self.sl_enabled or not self._bracket_active:
            return None
        order_side = OrderSide.sell if side == PositionSide.long else OrderSide.buy
        return AllocationOrder(
            side=order_side,
            symbol=self.symbol,
            price=self.sl_price,
            percent=1,
            execution_type=OrderExecutionType.stoplossLimit,
        )
