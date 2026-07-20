"""Bollinger Band mean-reversion strategy: enters against an extension to the
outer band, exits at a fixed take-profit or stop-loss distance from entry."""

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


class BollingerMeanReversionStrategy(Strategy):
    """Enters long the moment price touches the lower Bollinger Band (expecting
    reversion up) or short at the upper band (expecting reversion down), filled at
    the band price actually touched -- same intrabar-touch convention as
    `VwapMeanReversionStrategy`. Unlike that strategy's dynamic "revert to within
    exit_std of VWAP" exit, both the stop-loss and take-profit here are fixed
    fractional distances from entry (`sl_percent`/`tp_percent`), so a caller can
    tune them directly rather than tying the exit to the (still-moving) band.

    All band computation happens upstream, precomputed into `bb_lower_key`/
    `bb_upper_key` indicator series -- this strategy is a dumb consumer, same
    convention as this package's other strategies.
    """

    def __init__(  # noqa: PLR0913
        self,
        key: str,
        market: Market,
        symbol: str,
        bb_lower_key: str,
        bb_upper_key: str,
        sl_percent: float,
        tp_percent: float,
        direction: StrategyDirection = StrategyDirection.both,
    ):
        super().__init__(key=key, market=market, symbol=symbol)
        self.bb_lower_key: str = bb_lower_key
        self.bb_upper_key: str = bb_upper_key
        self.sl_percent: float = sl_percent
        self.tp_percent: float = tp_percent
        self.direction: StrategyDirection = direction

        self.sl_price: float = 0.0
        self.tp_price: float = 0.0

    # ------------------------------------------------------------------
    # Strategy interface
    # ------------------------------------------------------------------

    def refresh(self) -> None:
        indicators = self.market.current[self.symbol]["indicators"]
        lower_band = indicators.get(self.bb_lower_key)
        upper_band = indicators.get(self.bb_upper_key)
        if lower_band is None or upper_band is None:
            return

        if self.allocation.positions:
            self._check_exit()
        else:
            self._check_entry(lower_band, upper_band)

    # ------------------------------------------------------------------
    # Entry / exit checks
    # ------------------------------------------------------------------

    def _check_entry(self, lower_band: float, upper_band: float) -> None:
        candle = self.market.current[self.symbol]
        can_long = self.direction in {StrategyDirection.both, StrategyDirection.long}
        can_short = self.direction in {StrategyDirection.both, StrategyDirection.short}

        if candle["low"] <= lower_band and can_long:
            self._enter(PositionSide.long, lower_band)
        elif candle["high"] >= upper_band and can_short:
            self._enter(PositionSide.short, upper_band)

    def _check_exit(self) -> None:
        candle = self.market.current[self.symbol]
        side = self.allocation.positions[0].side

        if side == PositionSide.long:
            if candle["low"] <= self.sl_price:
                self._exit_current(self.sl_price, CloseReason.sl)
            elif candle["high"] >= self.tp_price:
                self._exit_current(self.tp_price, CloseReason.tp)
        else:
            if candle["high"] >= self.sl_price:
                self._exit_current(self.sl_price, CloseReason.sl)
            elif candle["low"] <= self.tp_price:
                self._exit_current(self.tp_price, CloseReason.tp)

    # ------------------------------------------------------------------
    # Allocation helpers
    # ------------------------------------------------------------------

    def _enter(self, side: PositionSide, price: float) -> None:
        if side == PositionSide.long:
            self.sl_price = price * (1 - self.sl_percent)
            self.tp_price = price * (1 + self.tp_percent)
        else:
            self.sl_price = price * (1 + self.sl_percent)
            self.tp_price = price * (1 - self.tp_percent)

        self.allocation = Allocation()
        self.allocation.positions = [
            AllocationPosition(side=side, symbol=self.symbol, percent=1, average_open_price=price)
        ]
        self.allocation.orders.append(self._make_sl_order(side))
        self.allocation.orders.append(self._make_tp_order(side))
        self._mark_allocation_changed()

        self.open_trade(side=side, open_price=price, risk_percent=self.sl_percent)

    def _exit_current(self, price: float, reason: CloseReason) -> None:
        self.allocation = Allocation()
        self._mark_allocation_changed()
        self.close_trade(close_price=price, reason=reason)

    def _make_sl_order(self, side: PositionSide) -> AllocationOrder:
        order_side = OrderSide.sell if side == PositionSide.long else OrderSide.buy
        return AllocationOrder(
            side=order_side,
            symbol=self.symbol,
            price=self.sl_price,
            percent=1,
            execution_type=OrderExecutionType.stoplossLimit,
        )

    def _make_tp_order(self, side: PositionSide) -> AllocationOrder:
        order_side = OrderSide.sell if side == PositionSide.long else OrderSide.buy
        return AllocationOrder(
            side=order_side,
            symbol=self.symbol,
            price=self.tp_price,
            percent=1,
            execution_type=OrderExecutionType.limit,
        )
