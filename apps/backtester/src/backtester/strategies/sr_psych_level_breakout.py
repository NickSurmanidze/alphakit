"""Support/resistance breakout strategy: enters in the direction price breaks a
precomputed horizontal level (an empirical high-dwell price bin, or a
psychological round number), stopped a fixed buffer back inside the broken level,
with a fixed risk:reward take-profit."""

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


class SupportResistanceBreakoutStrategy(Strategy):
    """Trades the break of a horizontal support/resistance level. All level
    detection (time-at-price histogram + psychological round-number grid, see
    `Indicators.support_resistance_levels`) happens upstream, precomputed into
    `resistance_key`/`support_key` indicator series -- this strategy is a dumb
    consumer, same convention as this package's other strategies. Both indicators
    are the nearest active level as of the *start* of the current bar (no
    lookahead) -- NaN wherever no level set has been computed yet (warmup) or none
    qualified, which naturally blocks entries during that stretch.

    Entry has two modes, controlled by `confirm_on_close`:

    - False (default): an intrabar stop-order fill, same style as
      OpeningRangeBreakoutStrategy -- the bar's high touching the resistance level
      -> long at max(resistance, open); the bar's low touching the support level
      -> short at min(support, open). A bar touching both in the same period is
      skipped (bar-interior ordering is unknowable from OHLC -- no coin flip).
      Fast to trigger, but can't tell a real break from a wick that immediately
      reverses within the same bar.
    - True: waits for the bar to *close* past the level -- long if close >
      resistance, short if close < support -- and fills at that close price. A
      bar can't confirm both directions at once, so there's no touched-both
      ambiguity to skip. Slower to trigger (a level can be wicked through several
      times before a bar finally closes past it) but filters out intrabar-only
      fakeouts that never survive to the close.

    Stop-loss sits `stop_buffer` price units back inside the broken level
    (resistance - stop_buffer for a long, support + stop_buffer for a short) --
    the level itself isn't used directly as the stop, since a no-gap fill would
    make entry and stop the same price (zero risk to size the trade off). Take-
    profit is `risk_reward_ratio` x that stop distance from entry.

    `trend_indicator_key`, if given, is a precomputed longer-term moving-average
    indicator used as a directional-agreement filter: a long only fires if price
    is *also* above it, a short only if price is also below it -- same convention
    as BollingerVwapBreakoutStrategy's own `trend_indicator_key`. The point isn't
    trend *strength* (this strategy's own regime analysis found ADX magnitude
    alone doesn't predict edge -- a whipsaw resolving into a new trend can spike
    ADX too) but *persistence*: a level break that agrees with the prevailing
    longer-horizon direction is more likely a real continuation than a level break
    against it, which is more likely the kind of two-way whipsaw fakeout this
    strategy otherwise loses to. `None` (the default) disables the filter,
    reproducing the original unfiltered behavior.
    """

    def __init__(  # noqa: PLR0913
        self,
        key: str,
        market: Market,
        symbol: str,
        resistance_key: str,
        support_key: str,
        stop_buffer: float,
        risk_reward_ratio: float = 2.0,
        direction: StrategyDirection = StrategyDirection.both,
        confirm_on_close: bool = False,
        trend_indicator_key: str | None = None,
    ):
        super().__init__(key=key, market=market, symbol=symbol)
        self.resistance_key: str = resistance_key
        self.support_key: str = support_key
        self.stop_buffer: float = stop_buffer
        self.risk_reward_ratio: float = risk_reward_ratio
        self.direction: StrategyDirection = direction
        self.confirm_on_close: bool = confirm_on_close
        self.trend_indicator_key: str | None = trend_indicator_key

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
        resistance = indicators.get(self.resistance_key)
        support = indicators.get(self.support_key)
        if resistance is None or support is None or pd.isna(resistance) or pd.isna(support):
            return

        self._check_entry(resistance, support)

    # ------------------------------------------------------------------
    # Entry / exit checks
    # ------------------------------------------------------------------

    def _check_entry(self, resistance: float, support: float) -> None:
        can_long = self.direction in {StrategyDirection.both, StrategyDirection.long}
        can_short = self.direction in {StrategyDirection.both, StrategyDirection.short}

        trend = self._trend_value()
        # No trend filter configured -> always agrees (reproduces unfiltered
        # behavior). NaN trend (warmup) fails every comparison below, so it safely
        # blocks entries rather than erroring.
        if trend is not None:
            price = self.market.current[self.symbol]["close"]
            can_long = can_long and price > trend
            can_short = can_short and price < trend

        if self.confirm_on_close:
            self._check_entry_close_confirmed(resistance, support, can_long, can_short)
        else:
            self._check_entry_intrabar_touch(resistance, support, can_long, can_short)

    def _trend_value(self) -> float | None:
        if self.trend_indicator_key is None:
            return None
        return self.market.current[self.symbol]["indicators"].get(self.trend_indicator_key)

    def _check_entry_intrabar_touch(
        self, resistance: float, support: float, can_long: bool, can_short: bool
    ) -> None:
        candle = self.market.current[self.symbol]
        touched_resistance = candle["high"] >= resistance
        touched_support = candle["low"] <= support

        # A bar touching both levels is unknowable from OHLC -- skip rather than guess.
        if touched_resistance and touched_support:
            return

        if touched_resistance and can_long:
            entry = max(resistance, candle["open"])
            self._enter(PositionSide.long, entry, sl_price=resistance - self.stop_buffer)
        elif touched_support and can_short:
            entry = min(support, candle["open"])
            self._enter(PositionSide.short, entry, sl_price=support + self.stop_buffer)

    def _check_entry_close_confirmed(
        self, resistance: float, support: float, can_long: bool, can_short: bool
    ) -> None:
        close = self.market.current[self.symbol]["close"]
        if close > resistance and can_long:
            self._enter(PositionSide.long, close, sl_price=resistance - self.stop_buffer)
        elif close < support and can_short:
            self._enter(PositionSide.short, close, sl_price=support + self.stop_buffer)

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
