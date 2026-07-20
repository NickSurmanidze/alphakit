"""MACD mean-reversion strategy: fades an extreme MACD histogram reading (a
rolling z-score band around the histogram's own recent mean), exits at a fixed
take-profit or stop-loss distance from entry."""

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


class MacdMeanReversionStrategy(Strategy):
    """Enters short the moment the MACD histogram reaches or exceeds
    `upper_key` (an unusually strong bullish reading -- momentum overbought,
    expecting reversion down) or long at `lower_key` (unusually strong
    bearish reading -- oversold, expecting reversion up), filled at that
    bar's close. Unlike a price-vs-band strategy (`BollingerMeanReversion
    Strategy`), the histogram is a derived indicator value known only once a
    bar closes, not a price level that can be touched intrabar -- so entries
    check the histogram at bar close rather than candle high/low, same
    convention as `SuperTrendFlipStrategy`/`BollingerVwapBreakoutStrategy`.

    `upper_key`/`lower_key` are expected to be a rolling mean of the
    histogram +/- some multiple of its own rolling standard deviation
    (computed upstream in the notebook, same "precomputed band, strategy is
    a dumb consumer" convention as this package's other strategies) --
    reused here as the histogram's own "how extreme is this reading right
    now" band, since the histogram isn't naturally bounded the way an RSI or
    stochastic value is.

    Stop-loss and take-profit are fixed fractional distances from entry
    (`sl_percent`/`tp_percent`), same convention as `BollingerMeanReversion
    Strategy` and `VwapMeanReversionStrategy`.
    """

    def __init__(  # noqa: PLR0913
        self,
        key: str,
        market: Market,
        symbol: str,
        histogram_key: str,
        upper_key: str,
        lower_key: str,
        sl_percent: float,
        tp_percent: float,
        direction: StrategyDirection = StrategyDirection.both,
    ):
        super().__init__(key=key, market=market, symbol=symbol)
        self.histogram_key: str = histogram_key
        self.upper_key: str = upper_key
        self.lower_key: str = lower_key
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
        histogram = indicators.get(self.histogram_key)
        upper = indicators.get(self.upper_key)
        lower = indicators.get(self.lower_key)
        if histogram is None or upper is None or lower is None:
            return

        if self.allocation.positions:
            self._check_exit()
        else:
            self._check_entry(histogram, upper, lower)

    # ------------------------------------------------------------------
    # Entry / exit checks
    # ------------------------------------------------------------------

    def _check_entry(self, histogram: float, upper: float, lower: float) -> None:
        close = self.market.current[self.symbol]["close"]
        can_long = self.direction in {StrategyDirection.both, StrategyDirection.long}
        can_short = self.direction in {StrategyDirection.both, StrategyDirection.short}

        if histogram <= lower and can_long:
            self._enter(PositionSide.long, close)
        elif histogram >= upper and can_short:
            self._enter(PositionSide.short, close)

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
