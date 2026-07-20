"""VWAP deviation-band mean-reversion strategy: enters against an extreme deviation
from session VWAP, exits on reversion toward VWAP (or a stop-loss backstop if the
deviation keeps extending instead of reverting)."""

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


class VwapMeanReversionStrategy(Strategy):
    """Enters long when price touches `entry_std` session-VWAP-standard-deviations
    below VWAP (expecting reversion up) or short when it touches `entry_std` above
    (expecting reversion down), sized off Indicators.vwap_session's (vwap, std)
    pair. Exits once price reverts to within `exit_std` deviations of VWAP, or a
    stop-loss fires first if the deviation keeps extending instead of reverting.

    Unlike MaCrossoverStrategy's fixed-price TP, there's no standing limit order for
    the reversion exit -- VWAP moves every bar, so there's no fixed price to place
    one against. It's checked directly each candle instead (against intrabar
    high/low, filling at the exit-target price actually touched, same convention as
    the SL/TP checks below). The stop-loss *is* a real standing order, same as
    MaCrossoverStrategy's, since it's a fixed fractional distance from entry.
    """

    def __init__(  # noqa: PLR0913
        self,
        key: str,
        market: Market,
        symbol: str,
        direction: StrategyDirection,
        vwap_indicator_key: str,
        vwap_std_indicator_key: str,
        entry_std: float = 2.0,
        exit_std: float = 0.0,
        sl_percent: float = 0.06,
        sl_enabled: bool = True,
        min_bars_since_session_start: int = 3,
    ):
        """entry_std/exit_std are session-VWAP-standard-deviation multiples --
        exit_std must be < entry_std (0.0 means "exit at VWAP itself"; a positive
        value means "exit at partial reversion, still exit_std deviations from
        VWAP"). sl_percent is a fractional distance from entry price, same
        convention as MaCrossoverStrategy's (0.06 means the stop sits 6% away from
        entry, not 0.06%). min_bars_since_session_start skips the first few bars
        after each session reset, when the volume-weighted std is still 0 or
        near-0 (mathematically exactly 0 on a session's very first bar) and would
        otherwise flag an ordinary small move as an extreme deviation.
        """
        super().__init__(key, market, symbol)
        self.direction: StrategyDirection = direction
        self.vwap_indicator_key: str = vwap_indicator_key
        self.vwap_std_indicator_key: str = vwap_std_indicator_key
        self.entry_std: float = entry_std
        self.exit_std: float = exit_std
        self.sl_percent: float = sl_percent
        self.sl_enabled: bool = sl_enabled
        self.min_bars_since_session_start: int = min_bars_since_session_start
        self.sl_price: float = 0.0

        self._current_session = None
        self._session_bar_count: int = 0

    # ------------------------------------------------------------------
    # Strategy interface
    # ------------------------------------------------------------------

    def refresh(self) -> None:
        """Tracks bars-since-session-start, then -- once past warmup and while
        vwap/std are usable -- checks for an exit if currently positioned, or an
        entry if flat. No-ops on the first candle, during a session's warmup
        window, or while std is 0/unavailable."""
        candle_num = self.market.current["num"]
        self._track_session_bar_count()

        if candle_num == 0 or self._session_bar_count < self.min_bars_since_session_start:
            return

        indicators = self.market.current[self.symbol]["indicators"]
        vwap = indicators[self.vwap_indicator_key]
        std = indicators[self.vwap_std_indicator_key]
        if not vwap or not std or std <= 0:
            return

        if self.allocation.positions:
            self._check_exit(vwap, std)
        else:
            self._check_entry(vwap, std)

    def _track_session_bar_count(self) -> None:
        """Resets the bars-since-session-start counter whenever the current
        candle's close falls on a new UTC calendar day -- same session boundary
        Indicators.vwap_session uses, so the two stay in sync."""
        session = self.market.current["time_close"].normalize()
        if session != self._current_session:
            self._current_session = session
            self._session_bar_count = 0
        self._session_bar_count += 1

    # ------------------------------------------------------------------
    # Entry / exit checks
    # ------------------------------------------------------------------

    def _check_entry(self, vwap: float, std: float) -> None:
        """Enters long/short the moment this candle's low/high touches the
        entry-band level, filling at the band price actually touched rather than
        the candle's close -- same convention as _check_exit/MaCrossoverStrategy's
        SL/TP fills below."""
        candle = self.market.current[self.symbol]
        lower_entry = vwap - self.entry_std * std
        upper_entry = vwap + self.entry_std * std
        can_long = self.direction in {StrategyDirection.both, StrategyDirection.long}
        can_short = self.direction in {StrategyDirection.both, StrategyDirection.short}

        if candle["low"] <= lower_entry and can_long:
            self._enter(PositionSide.long, lower_entry)
        elif candle["high"] >= upper_entry and can_short:
            self._enter(PositionSide.short, upper_entry)

    def _check_exit(self, vwap: float, std: float) -> None:
        """Exits the current position if this candle's low/high hit the stop-loss,
        or if it reached the reversion exit target -- stop-loss checked first
        (matching MaCrossoverStrategy's sl-before-tp precedence)."""
        candle = self.market.current[self.symbol]
        side = self.allocation.positions[0].side

        if side == PositionSide.long:
            exit_target = vwap - self.exit_std * std
            if self.sl_enabled and candle["low"] <= self.sl_price:
                self._exit_current(self.sl_price, CloseReason.sl)
            elif candle["high"] >= exit_target:
                self._exit_current(exit_target, CloseReason.signal)
        else:
            exit_target = vwap + self.exit_std * std
            if self.sl_enabled and candle["high"] >= self.sl_price:
                self._exit_current(self.sl_price, CloseReason.sl)
            elif candle["low"] <= exit_target:
                self._exit_current(exit_target, CloseReason.signal)

    # ------------------------------------------------------------------
    # Allocation helpers
    # ------------------------------------------------------------------

    def _exit_current(self, price: float, reason: CloseReason) -> None:
        """Flattens the allocation and records the close on the current trade."""
        self.allocation = Allocation()
        self._mark_allocation_changed()
        self.close_trade(close_price=price, reason=reason)

    def _enter(self, side: PositionSide, price: float) -> None:
        """Sets the allocation to a full-size position on `side` plus its stop-loss
        order (if enabled), and starts tracking a new trade."""
        if side == PositionSide.long:
            self.sl_price = price * (1 - self.sl_percent)
        else:
            self.sl_price = price * (1 + self.sl_percent)

        self.allocation = Allocation()
        self.allocation.positions = [
            AllocationPosition(side=side, symbol=self.symbol, percent=1, average_open_price=price)
        ]
        sl_order = self._make_sl_order(side)
        if sl_order:
            self.allocation.orders.append(sl_order)
        self._mark_allocation_changed()
        # sl_percent is exactly the fractional distance to the stop -- reuse it
        # directly as this trade's R-multiple risk unit, same convention as
        # MaCrossoverStrategy.
        risk_percent = self.sl_percent if self.sl_enabled else None
        self.open_trade(side=side, open_price=price, risk_percent=risk_percent)

    def _make_sl_order(self, side: PositionSide) -> AllocationOrder | None:
        """Builds the stop-loss stoploss-limit order for a new `side` position, or
        None if stop-loss is disabled."""
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
