import copy
import hashlib

from backtester.exchange import OrderSide, PositionSide
from backtester.strategies import (
    Allocation,
    AllocationOrder,
    AllocationPosition,
    Strategy,
)

_LongSides = dict[str, AllocationPosition]
_LongOrders = dict[str, list[AllocationOrder]]


class WeightedStrategy:
    """Pairs a Strategy with the weight its allocation should be scaled by when merged
    into a Portfolio."""

    def __init__(self, weight: float, strategy: Strategy):
        self.weight: float = weight
        self.strategy: Strategy = strategy


class Portfolio:
    """Aggregates one or more WeightedStrategy allocations into a single merged
    allocation, netting long vs. short exposure to the same symbol across strategies."""

    def __init__(self, weighted_strategies: list[WeightedStrategy], output_scale: float = 1):
        """output_scale multiplies every strategy's weighted allocation uniformly, e.g.
        to run the whole portfolio at half size."""
        self.weighted_strategies: list[WeightedStrategy] = weighted_strategies
        self.output_scale: float = output_scale

        self.merged_allocation: Allocation = Allocation()
        self.allocation_change_time = None
        self.signal_allocation_change_time_hash = ""

        self.exposure = {"long": 0.0, "short": 0.0, "gross": 0.0, "net": 0.0}

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def merge_allocation(self) -> None:
        """Recomputes `self.merged_allocation` from every weighted strategy's own
        allocation, netting opposite-side positions on the same symbol (see
        _net_positions). Positions/orders are ordered by each symbol's priority (see
        _symbol_priority) rather than dict/set iteration order, so a Rebalancer that
        processes `merged_allocation.positions` in list order (e.g. to size
        higher-priority signals first when leveraged margin can't fit everything) gets
        a deterministic, caller-controlled order -- not one that depends on incidental
        Python hashing."""
        long_pos, long_ord = self._collect_sides(PositionSide.long, OrderSide.sell)
        short_pos, short_ord = self._collect_sides(PositionSide.short, OrderSide.buy)
        merged_pos, merged_ord = self._net_positions(long_pos, long_ord, short_pos, short_ord)

        priority = self._symbol_priority()
        ordered_symbols = sorted(merged_pos, key=lambda symbol: priority.get(symbol, len(priority)))

        allocation = Allocation()
        for symbol in ordered_symbols:
            allocation.positions.append(merged_pos[symbol])
        for symbol in ordered_symbols:
            allocation.orders.extend(merged_ord.get(symbol, []))
        self.merged_allocation = allocation

    def refresh_exposures(self) -> None:
        """Recomputes `self.exposure` (long/short/gross/net, as fractions of the
        portfolio) from the current `merged_allocation`."""
        self.exposure = {"long": 0.0, "short": 0.0, "gross": 0.0, "net": 0.0}
        for position in self.merged_allocation.positions:
            if position.side == PositionSide.long:
                self.exposure["long"] += position.percent
            else:
                self.exposure["short"] += position.percent
        self.exposure["gross"] = self.exposure["short"] + self.exposure["long"]
        self.exposure["net"] = abs(self.exposure["short"] - self.exposure["long"])

    def refresh(self) -> None:
        """Refreshes every strategy, then re-merges the allocation and exposures only if
        any strategy's allocation actually changed this tick (cheap no-op otherwise)."""
        for ws in self.weighted_strategies:
            ws.strategy.refresh()

        new_hash = self._get_signal_allocation_change_time_hash()
        if self.signal_allocation_change_time_hash != new_hash:
            self.signal_allocation_change_time_hash = new_hash
            self.merge_allocation()
            self.refresh_exposures()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _collect_sides(
        self,
        position_side: PositionSide,
        order_side: OrderSide,
    ) -> tuple[_LongSides, _LongOrders]:
        """Aggregate positions and orders of one side across all weighted strategies."""
        positions: _LongSides = {}
        orders: _LongOrders = {}

        for ws in self.weighted_strategies:
            scale = ws.weight * self.output_scale
            for position in ws.strategy.allocation.positions:
                if position.side != position_side:
                    continue
                adj = copy.deepcopy(position)
                adj.percent *= scale
                if adj.symbol not in positions:
                    positions[adj.symbol] = adj
                else:
                    positions[adj.symbol].percent += adj.percent

                for order in ws.strategy.allocation.orders:
                    if order.symbol != adj.symbol or order.side != order_side:
                        continue
                    adj_order = copy.deepcopy(order)
                    adj_order.percent *= scale
                    orders.setdefault(order.symbol, []).append(adj_order)

        return positions, orders

    def _net_positions(
        self,
        long_pos: _LongSides,
        long_ord: _LongOrders,
        short_pos: _LongSides,
        short_ord: _LongOrders,
    ) -> tuple[_LongSides, _LongOrders]:
        """Net long and short positions into a single merged dict."""
        symbols = set(long_pos) | set(short_pos)
        merged_pos: _LongSides = {}
        merged_ord: _LongOrders = {}

        for symbol in symbols:
            has_long = symbol in long_pos
            has_short = symbol in short_pos

            if has_long and not has_short:
                merged_pos[symbol] = long_pos[symbol]
                merged_ord[symbol] = long_ord.get(symbol, [])

            elif has_short and not has_long:
                merged_pos[symbol] = short_pos[symbol]
                merged_ord[symbol] = short_ord.get(symbol, [])

            else:
                lp = long_pos[symbol].percent
                sp = short_pos[symbol].percent
                if lp > sp:
                    pos = long_pos[symbol]
                    pos.percent = lp - sp
                    merged_pos[symbol] = pos
                    merged_ord[symbol] = long_ord.get(symbol, []) + short_ord.get(symbol, [])
                elif sp > lp:
                    pos = short_pos[symbol]
                    pos.percent = sp - lp
                    merged_pos[symbol] = pos
                    merged_ord[symbol] = short_ord.get(symbol, []) + long_ord.get(symbol, [])
                # equal long/short → flat, no position

        return merged_pos, merged_ord

    def _symbol_priority(self) -> dict[str, int]:
        """Maps each symbol to the index of the first weighted_strategies entry that
        trades it -- index 0 is highest priority. This makes "top of the
        weighted_strategies list" a well-defined, caller-controlled priority order
        (reorder the list to change it) rather than an incidental one."""
        priority: dict[str, int] = {}
        for index, ws in enumerate(self.weighted_strategies):
            priority.setdefault(ws.strategy.symbol, index)
        return priority

    def _get_signal_allocation_change_time_hash(self) -> str:
        """Hashes every strategy's last allocation-change timestamp -- used by refresh()
        to detect "did anything actually change" without a deep comparison."""
        times = "".join(
            ws.strategy.allocation_change_time.isoformat()
            for ws in self.weighted_strategies
            if ws.strategy.allocation_change_time
        )
        return hashlib.md5(times.encode()).hexdigest()
