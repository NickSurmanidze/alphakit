"""Pullback-to-MA strategy: trades *with* a slow-MA-defined trend regime, entering
on the reclaim of a faster MA after a pullback rather than on a new breakout
extreme -- a structurally different entry style from this package's breakout
strategies (Donchian, Keltner) and its trend-flip strategy (SuperTrend)."""

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


class PullbackToMaStrategy(Strategy):
    """Buys weakness within an established uptrend instead of chasing strength at
    a new extreme. All indicator computation happens upstream, precomputed into
    `trend_ma_key` (slow MA -- defines the trend regime), `pullback_ma_key` (fast
    MA -- the pullback level being defended), and `atr_key` (for stop sizing) --
    this strategy is a dumb consumer, same convention as this package's other
    strategies.

    Regime filter: close above `trend_ma_key` permits longs, close below permits
    shorts (mirrors the long-only bias this repo has repeatedly found necessary
    for its breakout/trend-flip strategies, applied here as a filter rather than
    a hard-coded direction).

    Entry (long): while in an uptrend regime, the bar where close *reclaims* the
    fast MA -- previous bar's close at or below `pullback_ma_key`, current bar's
    close above it -- fires the entry at that close. This is the "pullback found
    support, trend resuming" signal; short entries are the mirror image in a
    downtrend regime. Requires one bar of prior-close history, tracked the same
    way `SuperTrendFlipStrategy` tracks its previous flip direction.

    Stop-loss is the fast MA itself, offset by `stop_atr_mult` x the current ATR
    (below for longs, above for shorts) -- same ATR-offset-from-a-level
    convention as `DonchianBreakoutStrategy`'s optional ATR-stop mode, here used
    unconditionally since the fast MA (unlike a breakout channel edge) can sit
    arbitrarily close to price and needs the ATR buffer to avoid a near-zero risk
    distance. Take-profit is `risk_reward_ratio` x that stop distance from entry.
    """

    def __init__(  # noqa: PLR0913
        self,
        key: str,
        market: Market,
        symbol: str,
        trend_ma_key: str,
        pullback_ma_key: str,
        atr_key: str,
        stop_atr_mult: float = 1.0,
        risk_reward_ratio: float = 2.0,
        direction: StrategyDirection = StrategyDirection.both,
    ):
        super().__init__(key=key, market=market, symbol=symbol)
        self.trend_ma_key: str = trend_ma_key
        self.pullback_ma_key: str = pullback_ma_key
        self.atr_key: str = atr_key
        self.stop_atr_mult: float = stop_atr_mult
        self.risk_reward_ratio: float = risk_reward_ratio
        self.direction: StrategyDirection = direction

        self.sl_price: float = 0.0
        self.tp_price: float = 0.0
        self._prev_close: float | None = None

    # ------------------------------------------------------------------
    # Strategy interface
    # ------------------------------------------------------------------

    def refresh(self) -> None:
        indicators = self.market.current[self.symbol]["indicators"]
        trend_ma = indicators.get(self.trend_ma_key)
        pullback_ma = indicators.get(self.pullback_ma_key)
        atr = indicators.get(self.atr_key)
        close = self.market.current[self.symbol]["close"]

        if any(v is None or pd.isna(v) for v in (trend_ma, pullback_ma, atr)):
            self._prev_close = None
            return

        prev_close = self._prev_close
        self._prev_close = close

        if self.allocation.positions:
            self._check_exit()
            return

        if prev_close is not None:
            self._check_entry(prev_close, close, trend_ma, pullback_ma, atr)

    # ------------------------------------------------------------------
    # Entry / exit checks
    # ------------------------------------------------------------------

    def _check_entry(
        self, prev_close: float, close: float, trend_ma: float, pullback_ma: float, atr: float
    ) -> None:
        if atr <= 0:
            return
        can_long = self.direction in {StrategyDirection.both, StrategyDirection.long}
        can_short = self.direction in {StrategyDirection.both, StrategyDirection.short}

        if can_long and close > trend_ma and prev_close <= pullback_ma and close > pullback_ma:
            sl_price = pullback_ma - self.stop_atr_mult * atr
            self._enter(PositionSide.long, close, sl_price)
        elif can_short and close < trend_ma and prev_close >= pullback_ma and close < pullback_ma:
            sl_price = pullback_ma + self.stop_atr_mult * atr
            self._enter(PositionSide.short, close, sl_price)

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
