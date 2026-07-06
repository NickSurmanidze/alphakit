"""MA-crossover strategy: enters long/short when a fast MA crosses a slow MA."""

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
    ):
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

    # ------------------------------------------------------------------
    # Strategy interface
    # ------------------------------------------------------------------

    def refresh(self) -> None:
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
            self.tp_price = price * (1 + self.tp_percent)
            self.sl_price = price * (1 - self.sl_percent)
            self._enter(PositionSide.long, price)

    def _handle_cross_down(self, price: float) -> None:
        """Fast MA crossed below slow MA."""
        self._exit_current(price, CloseReason.signal)
        if self.direction in {StrategyDirection.both, StrategyDirection.short}:
            self.tp_price = price * (1 - self.tp_percent)
            self.sl_price = price * (1 + self.sl_percent)
            self._enter(PositionSide.short, price)

    # ------------------------------------------------------------------
    # SL / TP checks
    # ------------------------------------------------------------------

    def _check_long_sl_tp(self, price: float) -> None:  # noqa: ARG002
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
        if not self.allocation.positions:
            return
        self.allocation = Allocation()
        self._mark_allocation_changed()
        self.close_trade(close_price=price, reason=reason)

    def _enter(self, side: PositionSide, price: float) -> None:
        tp_order = self._make_tp_order(side, price)
        sl_order = self._make_sl_order(side, price)

        self.allocation = Allocation()
        self.allocation.positions = [
            AllocationPosition(
                side=side, symbol=self.symbol, percent=1, average_open_price=price
            )
        ]
        if tp_order:
            self.allocation.orders.append(tp_order)
        if sl_order:
            self.allocation.orders.append(sl_order)
        self._mark_allocation_changed()
        self.open_trade(side=side, open_price=price)

    def _make_tp_order(self, side: PositionSide, price: float) -> AllocationOrder | None:
        if not self.tp_enabled:
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
        if not self.sl_enabled:
            return None
        order_side = OrderSide.sell if side == PositionSide.long else OrderSide.buy
        return AllocationOrder(
            side=order_side,
            symbol=self.symbol,
            price=self.sl_price,
            percent=1,
            execution_type=OrderExecutionType.stoplossLimit,
        )
