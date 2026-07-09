from backtester.exchange import Exchange
from backtester.market import Market
from backtester.middleware import Middleware
from backtester.performance import PerformanceAnalyzer
from backtester.portfolio import Portfolio
from backtester.rebalancer import Rebalancer


class Backtester:
    """Orchestrator: wires market, exchange, portfolio, rebalancer, reporter, and
    middleware together and drives the candle-by-candle simulation loop."""

    def __init__(
        self,
        market: Market,
        portfolio: Portfolio,
        reporter: PerformanceAnalyzer,
        exchange: Exchange,
        middlewares: list[Middleware] | None = None,
    ) -> None:
        """Builds the Rebalancer internally from the given market/exchange/portfolio;
        everything else is stored as-is."""
        # market we are trading
        self.market: Market = market

        # Portfolio holding merged allocation
        self.portfolio: Portfolio = portfolio

        # init an exchange
        self.exchange: Exchange = exchange

        # rebalancer
        self.rebalancer: Rebalancer = Rebalancer(
            market=market, exchange=self.exchange, portfolio=portfolio
        )

        # reporter
        self.reporter: PerformanceAnalyzer = reporter

        # pre/post-tick hooks, e.g. risk control -- see middleware.py
        self.middlewares: list[Middleware] = middlewares if middlewares is not None else []
        self._skip_tick: bool = False

    def skip_tick(self) -> None:
        """Callable from within a middleware's before_tick() to skip strategy evaluation
        and rebalancing for the current tick only. See Middleware.before_tick's
        docstring for how a middleware achieves a multi-tick halt with this."""
        self._skip_tick = True

    def run_step(self):
        """Processes the current candle: exchange fills/mark-to-market, pre-tick
        middleware, strategy/rebalancer (unless skipped), post-tick middleware, then
        takes a performance snapshot. See middleware.py for the hook semantics."""
        # Step 1: exchange processes everything up to this candle's close price -- order
        # fills, position mark-to-market, liquidations.
        self.exchange.run_step()

        # Step 2: pre-tick middleware sees that post-fill state and may call skip_tick()
        # to prevent step 3 from acting on it this candle.
        self._skip_tick = False
        for middleware in self.middlewares:
            middleware.before_tick(self)

        if not self._skip_tick:
            # Step 3: strategies evaluate signals against the close price, rebalancer
            # acts on any resulting allocation change.
            self.portfolio.refresh()
            self.rebalancer.refresh()

        # Step 4: post-tick middleware -- always runs, even if this tick was skipped.
        for middleware in self.middlewares:
            middleware.after_tick(self)

        # Step 5: take report snapshot.
        self.reporter.take_snapshot()

    def run_next_step(self):
        """Advances the market to the next candle and processes it. Manual single-step
        alternative to run_all(), useful for interactive/notebook stepping."""
        print("Running next step:", self.market.current["num"] + 1)
        self.market.set_next_candle_as_current_market()
        self.run_step()

    def run_all(self):
        """Steps through every remaining compiled candle in chronological order, then
        collects each strategy's trade history into the reporter and generates the
        final report."""
        while self.market.current["num"] < (len(self.market.index_num_ts) - 1):
            self.market.set_next_candle_as_current_market()
            self.run_step()

        # make report
        trades: list = []
        for ws in self.portfolio.weighted_strategies:
            trades.extend(ws.strategy.trade_history)
        self.reporter.trades = trades
        self.reporter.generate_report()
