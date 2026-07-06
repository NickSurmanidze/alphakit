"""
Golden test for the backtester.

Runs the canonical ETH/USD MA-crossover strategy against a fixed dataset and
asserts the exact output metrics.  If any of these numbers change after a
refactor you know the logic drifted.

Strategy: SMA-90 / SMA-150 crossover on 1-hour ETH/USD Binance data
           2017-01-01 → 2025-01-01, long-only, futures, $1 000 initial deposit
"""

import os

import pytest

from backtester.backtest_runner import Backtester
from backtester.exchange import Exchange, MarginAllocationType, MarketType, PositionSide
from backtester.indicators import Indicators
from backtester.market import Market, MarketDataFromCSV
from backtester.performance import PerformanceAnalyzer
from backtester.portfolio import Portfolio, WeightedStrategy
from backtester.strategies import CloseReason, MaCrossoverStrategy, StrategyDirection, TradeResult

DATA_FILE = os.path.join(os.path.dirname(__file__), "data", "BINANCE_ETHUSDT_1H.csv")

SYMBOL = "ETH/USD"
INTERVAL = 1
UNIT_OF_TIME = "hour"
DATE_FROM = "2017-01-01"
DATE_TO = "2025-01-01"
FAST_MA = 90
SLOW_MA = 150


@pytest.fixture(scope="module")
def backtest_result():
    market = Market()

    ohlc = MarketDataFromCSV(
        symbol=SYMBOL,
        date_from=DATE_FROM,
        date_to=DATE_TO,
        interval=INTERVAL,
        unit_of_time=UNIT_OF_TIME,
        path=DATA_FILE,
    ).get_df()

    market.add_market(symbol=SYMBOL, df=ohlc)
    market.add_indicator(
        symbol=SYMBOL,
        interval=INTERVAL,
        unit_of_time=UNIT_OF_TIME,
        indicator_name="sma_90",
        df=Indicators.sma(ohlc, FAST_MA),
    )
    market.add_indicator(
        symbol=SYMBOL,
        interval=INTERVAL,
        unit_of_time=UNIT_OF_TIME,
        indicator_name="sma_150",
        df=Indicators.sma(ohlc, SLOW_MA),
    )
    market.compile()

    portfolio = Portfolio(
        weighted_strategies=[
            WeightedStrategy(
                weight=1,
                strategy=MaCrossoverStrategy(
                    key="SMA_62_446",
                    market=market,
                    symbol=SYMBOL,
                    direction=StrategyDirection.long,
                    fast_indicator_key="sma_90",
                    slow_indicator_key="sma_150",
                    sl_percent=0.3,
                    tp_percent=0.6,
                    sl_enabled=True,
                    tp_enabled=True,
                ),
            )
        ],
        output_scale=1,
    )

    exchange = Exchange(
        market=market,
        slippage=0.002,
        maker_fee=0.001,
        taker_fee=0.00075,
        market_type=MarketType.future,
        max_leverage=10,
        margin_allocation_type=MarginAllocationType.cross,
    )

    reporter = PerformanceAnalyzer(
        market=market,
        exchange=exchange,
        portfolio=portfolio,
        benchmark_symbols=[SYMBOL],
    )

    market.reset()
    backtest = Backtester(
        market=market, portfolio=portfolio, exchange=exchange, reporter=reporter
    )
    backtest.exchange.transactions.add_deposit(asset="USD", volume=1000)
    backtest.run_all()

    return backtest


class TestGoldenMetrics:
    def test_candles_processed(self, backtest_result):
        assert backtest_result.market.current["num"] == 64651

    def test_total_trades(self, backtest_result):
        assert len(backtest_result.reporter.trades) == 224

    def test_total_rebalances(self, backtest_result):
        assert len(backtest_result.exchange.get_logs()) == 449

    # --- algo summary ---

    def test_algo_closed_trades(self, backtest_result):
        assert backtest_result.reporter.summary["algo"]["closed_trades"] == 224

    def test_algo_winner_trades(self, backtest_result):
        assert backtest_result.reporter.summary["algo"]["winner_trades"] == 97

    def test_algo_loser_trades(self, backtest_result):
        assert backtest_result.reporter.summary["algo"]["loser_trades"] == 127

    def test_algo_gross_return(self, backtest_result):
        assert backtest_result.reporter.summary["algo"]["gross_return_percent"] == pytest.approx(
            2436.115671739031, rel=1e-6
        )

    def test_algo_net_return(self, backtest_result):
        assert backtest_result.reporter.summary["algo"]["net_return_percent"] == pytest.approx(
            2336.115671739031, rel=1e-6
        )

    def test_algo_max_drawdown(self, backtest_result):
        assert backtest_result.reporter.summary["algo"]["max_drawdown_percent"] == pytest.approx(
            -66.2153, rel=1e-4
        )

    def test_algo_profit_factor(self, backtest_result):
        assert backtest_result.reporter.summary["algo"]["profit_factor"] == pytest.approx(
            2.016663917104035, rel=1e-6
        )

    def test_algo_sharpe_ratio(self, backtest_result):
        assert backtest_result.reporter.summary["algo"]["sharpe_ratio"] == pytest.approx(
            1.0233240145287723, rel=1e-6
        )

    # --- benchmark (buy-and-hold ETH/USD) ---

    def test_benchmark_gross_return(self, backtest_result):
        assert backtest_result.reporter.summary[SYMBOL]["gross_return_percent"] == pytest.approx(
            1105.2251655629107, rel=1e-6
        )

    def test_benchmark_net_return(self, backtest_result):
        assert backtest_result.reporter.summary[SYMBOL]["net_return_percent"] == pytest.approx(
            1005.2251655629107, rel=1e-6
        )

    def test_benchmark_max_drawdown(self, backtest_result):
        assert backtest_result.reporter.summary[SYMBOL]["max_drawdown_percent"] == pytest.approx(
            -93.9655, rel=1e-4
        )

    def test_benchmark_sharpe_ratio(self, backtest_result):
        assert backtest_result.reporter.summary[SYMBOL]["sharpe_ratio"] == pytest.approx(
            0.8190021481833262, rel=1e-6
        )


class TestGoldenTrades:
    def test_first_trade(self, backtest_result):
        t = backtest_result.reporter.trades[0]
        assert t.symbol == SYMBOL
        assert t.side == PositionSide.long
        assert t.open_price == pytest.approx(289.97)
        assert t.close_price == pytest.approx(268.74)
        assert t.close_reason == CloseReason.signal
        assert t.pnl == pytest.approx(-0.07321447046246166, rel=1e-6)
        assert t.result == TradeResult.loser

    def test_last_trade(self, backtest_result):
        t = backtest_result.reporter.trades[-1]
        assert t.symbol == SYMBOL
        assert t.side == PositionSide.long
        assert t.open_price == pytest.approx(3475.6)
        assert t.close_price == pytest.approx(3378.57)
        assert t.close_reason == CloseReason.signal
        assert t.pnl == pytest.approx(-0.0279174818736333, rel=1e-6)
        assert t.result == TradeResult.loser

    def test_first_tp_trade(self, backtest_result):
        """Verify at least one take-profit close exists and has expected 0.6% gain."""
        tp_trades = [t for t in backtest_result.reporter.trades if t.close_reason == CloseReason.tp]
        assert len(tp_trades) > 0
        for t in tp_trades:
            assert t.pnl == pytest.approx(0.6, rel=1e-6)
