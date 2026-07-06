from backtester.exchange import Exchange
from backtester.market import Market
from backtester.performance import PerformanceAnalyzer
from backtester.portfolio import Portfolio
from backtester.rebalancer import Rebalancer


class Backtester:
    def __init__(
        self,
        market: Market,
        portfolio: Portfolio,
        reporter: PerformanceAnalyzer,
        exchange: Exchange,
    ) -> None:
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

    def run_step(self):
        # print("Running next step:", self.market.current["num"] + 1)
        # Step 1: exchange should process everything up to close price
        self.exchange.run_step()

        # Step 2: Process everything that should happen afterwards
        self.portfolio.refresh()

        # Step 3: Rebalance if needed
        self.rebalancer.refresh()

        # Step 4: take report snapshot
        self.reporter.take_snapshot()

        # clear output and re-print the results
        # clear_output(wait=True)
        # print("Position Pnl in USD: ", self.exchange.get_asset_total_in_usd())
        # check if anything has to be done at this step on backtester side,
        # e.g. open or close positions based on signals
        # finally take a snapshot

    def run_next_step(self):
        print("Running next step:", self.market.current["num"] + 1)
        self.market.set_next_candle_as_current_market()
        self.run_step()

    def run_all(self):
        while self.market.current["num"] < (len(self.market.index_num_ts) - 1):
            self.market.set_next_candle_as_current_market()
            self.run_step()

        # make report
        trades: list = []
        for ws in self.portfolio.weighted_strategies:
            trades.extend(ws.strategy.trade_history)
        self.reporter.trades = trades
        self.reporter.generate_report()
