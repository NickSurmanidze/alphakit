"""Bollinger squeeze breakout strategy: enters in the direction VWAP indicates while
Bollinger Bands are unusually narrow, with a band-derived stop-loss and a fixed
risk:reward take-profit."""

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


class BollingerVwapBreakoutStrategy(Strategy):
    """Enters a trade the moment Bollinger Bands are in a "squeeze" (a precomputed
    boolean indicator -- this strategy doesn't compute the squeeze/percentile logic
    itself, same convention as VwapMeanReversionStrategy/PairsMeanReversionStrategy
    consuming precomputed indicators rather than owning the statistics). Direction is
    decided by price's position relative to session VWAP at that moment: above VWAP
    -> long, below -> short -- betting the squeeze resolves in the direction the
    session's volume-weighted "fair value" already leans.

    Stop-loss sits at the *opposite* band (lower band for a long, upper band for a
    short) -- during a squeeze the bands are close together, so this is naturally a
    tight, volatility-scaled stop: the whole edge of trading a squeeze is that the
    invalidation point (price re-entering/crossing the recent tight range) is close
    by. Take-profit is `risk_reward_ratio` times that same risk distance (default
    2.0, i.e. risking 1 to make 2) -- not itself a band level, just a multiple of
    the stop distance.

    `trend_indicator_key`, if given, is a precomputed longer-term moving-average
    indicator used as a confirmation filter: a long only fires if price is *also*
    above it (not just above VWAP), a short only if price is also below it. Added
    after an initial run found this strategy's win rate sitting almost exactly at
    its 1:2 payout's breakeven point (33.3%) -- VWAP side alone wasn't predicting
    which way a squeeze resolves any better than chance, so this requires a second,
    longer-horizon signal to agree before taking the trade. `None` (the default)
    disables the filter, reproducing the original VWAP-only behavior.
    """

    def __init__(  # noqa: PLR0913
        self,
        key: str,
        market: Market,
        symbol: str,
        vwap_indicator_key: str,
        bb_lower_key: str,
        bb_upper_key: str,
        squeeze_indicator_key: str,
        risk_reward_ratio: float = 2.0,
        direction: StrategyDirection = StrategyDirection.both,
        trend_indicator_key: str | None = None,
    ):
        super().__init__(key=key, market=market, symbol=symbol)
        self.vwap_indicator_key: str = vwap_indicator_key
        self.bb_lower_key: str = bb_lower_key
        self.bb_upper_key: str = bb_upper_key
        self.squeeze_indicator_key: str = squeeze_indicator_key
        self.risk_reward_ratio: float = risk_reward_ratio
        self.direction: StrategyDirection = direction
        self.trend_indicator_key: str | None = trend_indicator_key

        self.sl_price: float = 0.0
        self.tp_price: float = 0.0

    # ------------------------------------------------------------------
    # Strategy interface
    # ------------------------------------------------------------------

    def refresh(self) -> None:
        indicators = self.market.current[self.symbol]["indicators"]
        is_squeeze = indicators[self.squeeze_indicator_key]
        vwap = indicators[self.vwap_indicator_key]
        lower_band = indicators[self.bb_lower_key]
        upper_band = indicators[self.bb_upper_key]

        if not vwap or not lower_band or not upper_band:
            return

        if self.allocation.positions:
            self._check_exit()
        elif is_squeeze:
            self._check_entry(vwap, lower_band, upper_band)

    # ------------------------------------------------------------------
    # Entry / exit checks
    # ------------------------------------------------------------------

    def _check_entry(self, vwap: float, lower_band: float, upper_band: float) -> None:
        price = self.market.current[self.symbol]["close"]
        can_long = self.direction in {StrategyDirection.both, StrategyDirection.long}
        can_short = self.direction in {StrategyDirection.both, StrategyDirection.short}
        trend = self._trend_value()
        # No trend filter configured -> always agrees (reproduces VWAP-only
        # behavior). NaN trend (warmup) fails every comparison below, so it safely
        # blocks entries rather than erroring.
        trend_agrees_long = trend is None or price > trend
        trend_agrees_short = trend is None or price < trend

        if price > vwap and can_long and trend_agrees_long:
            self._enter(PositionSide.long, price, sl_price=lower_band)
        elif price < vwap and can_short and trend_agrees_short:
            self._enter(PositionSide.short, price, sl_price=upper_band)

    def _trend_value(self) -> float | None:
        if self.trend_indicator_key is None:
            return None
        return self.market.current[self.symbol]["indicators"].get(self.trend_indicator_key)

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
        """No-ops if the band-derived stop distance isn't actually positive (e.g. a
        big enough move that price already sits past the opposite band by entry
        time) -- there's no valid risk unit to size the trade or its take-profit
        off in that case."""
        risk = (price - sl_price) if side == PositionSide.long else (sl_price - price)
        if risk <= 0:
            return

        tp_price = (
            price + self.risk_reward_ratio * risk
            if side == PositionSide.long
            else price - self.risk_reward_ratio * risk
        )
        self.sl_price = sl_price
        self.tp_price = tp_price

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
