"""Opening Range Breakout (ORB) strategy: trades the break of the US cash session's
opening range, with the opposite side of the range as the stop, an optional
R-multiple take-profit, and a forced flat before the session close."""

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


class OpeningRangeBreakoutStrategy(Strategy):
    """Trades at most ONE breakout of the session's opening range per session.

    All session logic (which bars form the opening range, when trading is allowed,
    when to force flat, which session a bar belongs to) is precomputed in indicator
    series -- this strategy is a dumb consumer, same convention as the other
    strategies in this package. Expected indicators:

    - `or_high_key` / `or_low_key`: the completed opening range's high/low, NaN on
      every bar before the range is complete (NaN also naturally covers holidays/
      missing sessions -- no trade can trigger without a valid range).
    - `tradeable_key`: boolean -- True only in the window where new entries are
      allowed (after the range completes, before the force-flat cutoff).
    - `force_flat_key`: boolean -- True from the force-flat cutoff onward; any open
      position is closed at that bar's close price (time exit, CloseReason.signal).
    - `session_id_key`: numeric id identifying the trading session (e.g. the NY
      calendar date as an ordinal) -- used both for the one-trade-per-session rule
      and as a safety net: if a position is somehow still open when the session id
      changes (early-close days where the force-flat bar never printed), it's
      closed on the first bar of the new session.

    Entry is an intrabar stop-order fill: the moment a tradeable bar's high touches
    the range high -> long at max(or_high, bar open) (gap-through opens fill at the
    open, not at a price the market never traded); low touches the range low ->
    short, symmetrically. A bar that touches BOTH sides is skipped (bar-interior
    ordering is unknowable from OHLC -- the conservative choice is no trade, not a
    coin flip). Stop-loss sits at the opposite side of the range by default, or --
    if `stop_atr_key` is given -- at `stop_atr_mult` x that indicator's value away
    from entry (a lagged daily ATR, precomputed in the notebook; entries are
    skipped while it's NaN/non-positive). The ATR mode exists because the
    range-derived stop is dangerously tight when volatility is extreme relative to
    the opening range (2020's losses in the first pass came almost entirely from
    that). Take-profit (if `risk_reward_ratio` is not None) at entry +
    risk_reward_ratio * risk, where risk is the entry-to-stop distance in whichever
    mode is active. With `risk_reward_ratio=None` there is no TP -- winners run
    until the stop or the end-of-session flat.
    """

    def __init__(  # noqa: PLR0913
        self,
        key: str,
        market: Market,
        symbol: str,
        or_high_key: str,
        or_low_key: str,
        tradeable_key: str,
        force_flat_key: str,
        session_id_key: str,
        risk_reward_ratio: float | None = 2.0,
        direction: StrategyDirection = StrategyDirection.both,
        stop_atr_key: str | None = None,
        stop_atr_mult: float = 1.0,
    ):
        super().__init__(key=key, market=market, symbol=symbol)
        self.or_high_key: str = or_high_key
        self.or_low_key: str = or_low_key
        self.tradeable_key: str = tradeable_key
        self.force_flat_key: str = force_flat_key
        self.session_id_key: str = session_id_key
        self.risk_reward_ratio: float | None = risk_reward_ratio
        self.direction: StrategyDirection = direction
        self.stop_atr_key: str | None = stop_atr_key
        self.stop_atr_mult: float = stop_atr_mult

        self.sl_price: float = 0.0
        self.tp_price: float = 0.0
        self._last_traded_session: float | None = None
        self._position_session: float | None = None

    # ------------------------------------------------------------------
    # Strategy interface
    # ------------------------------------------------------------------

    def refresh(self) -> None:
        indicators = self.market.current[self.symbol]["indicators"]
        session_id = indicators.get(self.session_id_key)

        if self.allocation.positions:
            self._check_exit(indicators, session_id)
            return

        or_high = indicators.get(self.or_high_key)
        or_low = indicators.get(self.or_low_key)
        if or_high is None or or_low is None or pd.isna(or_high) or pd.isna(or_low):
            return
        # `tradeable` is only trusted when the range values themselves are valid --
        # the NaN check above already covers warmup/holiday bars.
        if not indicators.get(self.tradeable_key):
            return
        if session_id is not None and session_id == self._last_traded_session:
            return

        self._check_entry(or_high, or_low, session_id)

    # ------------------------------------------------------------------
    # Entry / exit checks
    # ------------------------------------------------------------------

    def _check_entry(self, or_high: float, or_low: float, session_id: float | None) -> None:
        candle = self.market.current[self.symbol]
        touched_high = candle["high"] >= or_high
        touched_low = candle["low"] <= or_low

        # A bar touching both sides is unknowable from OHLC -- skip rather than guess.
        if touched_high and touched_low:
            return

        can_long = self.direction in {StrategyDirection.both, StrategyDirection.long}
        can_short = self.direction in {StrategyDirection.both, StrategyDirection.short}

        if touched_high and can_long:
            entry = max(or_high, candle["open"])
            sl_price = self._stop_price(PositionSide.long, entry, range_stop=or_low)
            if sl_price is not None:
                self._enter(PositionSide.long, entry, sl_price=sl_price, session_id=session_id)
        elif touched_low and can_short:
            entry = min(or_low, candle["open"])
            sl_price = self._stop_price(PositionSide.short, entry, range_stop=or_high)
            if sl_price is not None:
                self._enter(PositionSide.short, entry, sl_price=sl_price, session_id=session_id)

    def _stop_price(self, side: PositionSide, entry: float, range_stop: float) -> float | None:
        """The stop level for a new position: the opposite range bound by default, or
        entry -/+ stop_atr_mult * ATR in ATR mode. None (skip the entry entirely)
        when ATR mode is on but the ATR value isn't usable yet."""
        if self.stop_atr_key is None:
            return range_stop
        atr = self.market.current[self.symbol]["indicators"].get(self.stop_atr_key)
        if atr is None or pd.isna(atr) or atr <= 0:
            return None
        offset = self.stop_atr_mult * atr
        return entry - offset if side == PositionSide.long else entry + offset

    def _check_exit(self, indicators: dict, session_id: float | None) -> None:
        candle = self.market.current[self.symbol]
        side = self.allocation.positions[0].side

        # Safety net first: session rolled over without the force-flat bar ever
        # printing (early-close days) -- flatten immediately at this bar's close.
        session_changed = (
            session_id is not None
            and self._position_session is not None
            and session_id != self._position_session
        )
        if indicators.get(self.force_flat_key) or session_changed:
            self._exit_current(candle["close"], CloseReason.signal)
            return

        if side == PositionSide.long:
            if candle["low"] <= self.sl_price:
                self._exit_current(self.sl_price, CloseReason.sl)
            elif self.risk_reward_ratio is not None and candle["high"] >= self.tp_price:
                self._exit_current(self.tp_price, CloseReason.tp)
        else:
            if candle["high"] >= self.sl_price:
                self._exit_current(self.sl_price, CloseReason.sl)
            elif self.risk_reward_ratio is not None and candle["low"] <= self.tp_price:
                self._exit_current(self.tp_price, CloseReason.tp)

    # ------------------------------------------------------------------
    # Allocation helpers
    # ------------------------------------------------------------------

    def _enter(
        self, side: PositionSide, price: float, sl_price: float, session_id: float | None
    ) -> None:
        risk = (price - sl_price) if side == PositionSide.long else (sl_price - price)
        if risk <= 0:
            return

        self.sl_price = sl_price
        if self.risk_reward_ratio is not None:
            self.tp_price = (
                price + self.risk_reward_ratio * risk
                if side == PositionSide.long
                else price - self.risk_reward_ratio * risk
            )

        self._last_traded_session = session_id
        self._position_session = session_id

        self.allocation = Allocation()
        self.allocation.positions = [
            AllocationPosition(side=side, symbol=self.symbol, percent=1, average_open_price=price)
        ]
        self.allocation.orders.append(self._make_sl_order(side))
        if self.risk_reward_ratio is not None:
            self.allocation.orders.append(self._make_tp_order(side))
        self._mark_allocation_changed()

        self.open_trade(side=side, open_price=price, risk_percent=risk / price)

    def _exit_current(self, price: float, reason: CloseReason) -> None:
        self._position_session = None
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
