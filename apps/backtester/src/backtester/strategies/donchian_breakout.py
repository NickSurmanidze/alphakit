"""Donchian channel breakout strategy: enters in the direction price closes
beyond the trailing N-bar high/low channel, stopped at the opposite side of the
channel, with a fixed risk:reward take-profit."""

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


class DonchianBreakoutStrategy(Strategy):
    """Classic Turtle-style N-bar channel breakout. All channel computation
    (`Indicators.donchian_channels` -- the trailing N-bar high/low, excluding the
    current bar) happens upstream, precomputed into `upper_key`/`lower_key`
    indicator series -- this strategy is a dumb consumer, same convention as this
    package's other strategies.

    Entry requires the bar to *close* beyond the channel (long if close > upper,
    short if close < lower), filled at that close price -- not an intrabar wick
    touch. This repo's own S/R-breakout research found intrabar-touch entries
    dominated by same-bar fakeouts (win rate 11-25% vs. a ~33% breakeven for a 1:2
    payout) that a close-confirmation requirement mostly fixed, so this strategy
    starts from that lesson rather than re-discovering it from scratch.

    Stop-loss sits at the *opposite* side of the channel by default (lower for a
    long, upper for a short) -- during a genuine breakout the channel is usually
    wide enough for this to be a meaningful (not near-zero) risk distance, unlike
    a bare S/R level which can sit arbitrarily close to price. If `stop_atr_key`
    is given, the stop is instead `stop_atr_mult` x that indicator's value away
    from entry -- a lagged ATR, precomputed in the notebook; entries are skipped
    while it's NaN/non-positive. This decouples "how wide is the stop" from
    "how long is the entry channel," the same reasoning
    OpeningRangeBreakoutStrategy's own ATR mode was added for: the two don't have
    to move together, and searching them independently is a strictly larger
    (and potentially more revealing) parameter space than the channel-width-only
    version. Take-profit is `risk_reward_ratio` x whichever stop distance is
    active, from entry.
    """

    def __init__(  # noqa: PLR0913
        self,
        key: str,
        market: Market,
        symbol: str,
        upper_key: str,
        lower_key: str,
        risk_reward_ratio: float = 2.0,
        direction: StrategyDirection = StrategyDirection.both,
        stop_atr_key: str | None = None,
        stop_atr_mult: float = 1.0,
    ):
        super().__init__(key=key, market=market, symbol=symbol)
        self.upper_key: str = upper_key
        self.lower_key: str = lower_key
        self.risk_reward_ratio: float = risk_reward_ratio
        self.direction: StrategyDirection = direction
        self.stop_atr_key: str | None = stop_atr_key
        self.stop_atr_mult: float = stop_atr_mult

        self.sl_price: float = 0.0
        self.tp_price: float = 0.0

    # ------------------------------------------------------------------
    # Strategy interface
    # ------------------------------------------------------------------

    def refresh(self) -> None:
        if self.allocation.positions:
            self._check_exit()
            return

        indicators = self.market.current[self.symbol]["indicators"]
        upper = indicators.get(self.upper_key)
        lower = indicators.get(self.lower_key)
        if upper is None or lower is None or pd.isna(upper) or pd.isna(lower):
            return

        self._check_entry(upper, lower)

    # ------------------------------------------------------------------
    # Entry / exit checks
    # ------------------------------------------------------------------

    def _check_entry(self, upper: float, lower: float) -> None:
        can_long = self.direction in {StrategyDirection.both, StrategyDirection.long}
        can_short = self.direction in {StrategyDirection.both, StrategyDirection.short}

        close = self.market.current[self.symbol]["close"]
        if close > upper and can_long:
            sl_price = self._stop_price(PositionSide.long, close, channel_stop=lower)
            if sl_price is not None:
                self._enter(PositionSide.long, close, sl_price=sl_price)
        elif close < lower and can_short:
            sl_price = self._stop_price(PositionSide.short, close, channel_stop=upper)
            if sl_price is not None:
                self._enter(PositionSide.short, close, sl_price=sl_price)

    def _stop_price(self, side: PositionSide, entry: float, channel_stop: float) -> float | None:
        """The stop level for a new position: the opposite channel side by
        default, or entry -/+ stop_atr_mult * ATR in ATR mode. None (skip the
        entry entirely) when ATR mode is on but the ATR value isn't usable yet."""
        if self.stop_atr_key is None:
            return channel_stop
        atr = self.market.current[self.symbol]["indicators"].get(self.stop_atr_key)
        if atr is None or pd.isna(atr) or atr <= 0:
            return None
        offset = self.stop_atr_mult * atr
        return entry - offset if side == PositionSide.long else entry + offset

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

    def _enter(self, side: PositionSide, price: float, sl_price: float) -> None:
        risk = (price - sl_price) if side == PositionSide.long else (sl_price - price)
        if risk <= 0:
            return

        self.sl_price = sl_price
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
