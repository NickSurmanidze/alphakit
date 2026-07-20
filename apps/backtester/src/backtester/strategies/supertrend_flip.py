"""SuperTrend flip strategy: a stop-and-reverse trend follower built directly on
Indicators.supertrend's own flip logic -- always in the market (long or short),
switching sides the moment the indicator's trend direction flips."""

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


class SuperTrendFlipStrategy(Strategy):
    """Stop-and-reverse trend follower on `Indicators.supertrend`'s flip signal.
    All ATR/ratchet/flip computation happens upstream, precomputed into
    `line_key` (the active support/resistance level) and `direction_key` (1.0
    uptrend / -1.0 downtrend) indicator series -- this strategy is a dumb
    consumer, same convention as this package's other strategies.

    Entry: the bar where `direction_key` flips from downtrend to uptrend -> long
    at that bar's close (the earliest point the flip is known); flips the other
    way -> short, symmetrically. If a flip happens while already positioned the
    other way, the existing position is closed and the new one opened in the
    same bar (stop-and-reverse) rather than waiting a bar flat, since a
    SuperTrend flip is simultaneously an exit signal for the old side and an
    entry signal for the new one.

    The *typical* exit is the indicator's own construction: hold until
    `direction_key` flips back (`CloseReason.signal`, filled at that bar's
    close) -- `line_key` ratchets favorably every bar while direction is
    unchanged, so this is normally what actually closes a trade, well before any
    hard stop is reached. But relying on the flip alone means zero protection
    against a single violent adverse move that takes many bars to actually
    reverse the indicator -- a strategy backtest that tried that (no hard stop
    at all) blew through this repo's own drawdown middleware early on real MES
    data, more than once. So there is also a hard stop-loss, frozen at the
    `line_key` value *at entry* (not trailing with it) -- a fixed worst-case
    cap, same convention as every other strategy in this package, checked
    intrabar before the signal exit each bar. An optional `risk_reward_ratio`
    (None by default, matching OpeningRangeBreakoutStrategy's own no-TP
    convention) additionally takes profit early, at that R-multiple of the
    entry-time risk, if hit before either the flip or the hard stop.
    """

    def __init__(  # noqa: PLR0913
        self,
        key: str,
        market: Market,
        symbol: str,
        line_key: str,
        direction_key: str,
        risk_reward_ratio: float | None = None,
        direction: StrategyDirection = StrategyDirection.both,
    ):
        super().__init__(key=key, market=market, symbol=symbol)
        self.line_key: str = line_key
        self.direction_key: str = direction_key
        self.risk_reward_ratio: float | None = risk_reward_ratio
        self.direction: StrategyDirection = direction

        self.sl_price: float = 0.0
        self.tp_price: float | None = None
        self._prev_trend_direction: float | None = None

    # ------------------------------------------------------------------
    # Strategy interface
    # ------------------------------------------------------------------

    def refresh(self) -> None:
        indicators = self.market.current[self.symbol]["indicators"]
        trend_direction = indicators.get(self.direction_key)
        line = indicators.get(self.line_key)
        if trend_direction is None or line is None or pd.isna(trend_direction) or pd.isna(line):
            return

        prev_trend_direction = self._prev_trend_direction
        self._prev_trend_direction = trend_direction

        if self.allocation.positions:
            self._check_exit(trend_direction)

        if not self.allocation.positions and prev_trend_direction is not None:
            self._check_entry(prev_trend_direction, trend_direction, line)

    # ------------------------------------------------------------------
    # Entry / exit checks
    # ------------------------------------------------------------------

    def _check_entry(self, prev_trend_direction: float, trend_direction: float, line: float) -> None:
        can_long = self.direction in {StrategyDirection.both, StrategyDirection.long}
        can_short = self.direction in {StrategyDirection.both, StrategyDirection.short}
        close = self.market.current[self.symbol]["close"]

        if prev_trend_direction < 0 and trend_direction > 0 and can_long:
            self._enter(PositionSide.long, close, line)
        elif prev_trend_direction > 0 and trend_direction < 0 and can_short:
            self._enter(PositionSide.short, close, line)

    def _check_exit(self, trend_direction: float) -> None:
        candle = self.market.current[self.symbol]
        side = self.allocation.positions[0].side
        close = candle["close"]

        if side == PositionSide.long:
            if candle["low"] <= self.sl_price:
                self._exit_current(self.sl_price, CloseReason.sl)
            elif trend_direction < 0:
                self._exit_current(close, CloseReason.signal)
            elif self.tp_price is not None and candle["high"] >= self.tp_price:
                self._exit_current(self.tp_price, CloseReason.tp)
        else:
            if candle["high"] >= self.sl_price:
                self._exit_current(self.sl_price, CloseReason.sl)
            elif trend_direction > 0:
                self._exit_current(close, CloseReason.signal)
            elif self.tp_price is not None and candle["low"] <= self.tp_price:
                self._exit_current(self.tp_price, CloseReason.tp)

    # ------------------------------------------------------------------
    # Allocation helpers
    # ------------------------------------------------------------------

    def _enter(self, side: PositionSide, price: float, line: float) -> None:
        risk = (price - line) if side == PositionSide.long else (line - price)
        if risk <= 0:
            return

        self.sl_price = line
        self.tp_price = None
        if self.risk_reward_ratio is not None:
            self.tp_price = (
                price + self.risk_reward_ratio * risk
                if side == PositionSide.long
                else price - self.risk_reward_ratio * risk
            )

        self.allocation = Allocation()
        self.allocation.positions = [
            AllocationPosition(side=side, symbol=self.symbol, percent=1, average_open_price=price)
        ]
        self.allocation.orders.append(self._make_sl_order(side))
        if self.tp_price is not None:
            self.allocation.orders.append(self._make_tp_order(side))
        self._mark_allocation_changed()

        self.open_trade(side=side, open_price=price, risk_percent=risk / price)

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
