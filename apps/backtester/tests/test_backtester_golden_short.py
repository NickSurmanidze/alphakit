"""Golden test: short-only variant of the canonical ETH/USD MA-crossover scenario.

Same CSV, same SMA-90/150 crossover, same fees/slippage/leverage as
test_backtester_golden.py -- only StrategyDirection.short differs. Existed to give the
short-side code path (Positions with PositionSide.short, short PnL sign, short SL/TP
trigger logic in _check_short_sl_tp) the same end-to-end regression coverage the
long-only test already had; before this file, nothing exercised short at this scale.
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
                    key="SMA_62_446_short",
                    market=market,
                    symbol=SYMBOL,
                    direction=StrategyDirection.short,
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
    backtest = Backtester(market=market, portfolio=portfolio, exchange=exchange, reporter=reporter)
    backtest.exchange.transactions.add_deposit(asset="USD", volume=1000)
    backtest.run_all()

    return backtest


class TestGoldenMetricsShort:
    def test_total_trades(self, backtest_result):
        assert len(backtest_result.reporter.trades) == 224

    def test_total_rebalances(self, backtest_result):
        assert len(backtest_result.exchange.get_logs()) == 450

    def test_algo_closed_trades(self, backtest_result):
        assert backtest_result.reporter.summary["algo"]["closed_trades"] == 224

    def test_algo_winner_trades(self, backtest_result):
        assert backtest_result.reporter.summary["algo"]["winner_trades"] == 90

    def test_algo_loser_trades(self, backtest_result):
        assert backtest_result.reporter.summary["algo"]["loser_trades"] == 134

    def test_algo_gross_return(self, backtest_result):
        assert backtest_result.reporter.summary["algo"]["gross_return_percent"] == pytest.approx(
            16.435588672433003, rel=1e-6
        )

    def test_algo_net_return(self, backtest_result):
        # Net-negative despite more winners than a coin flip: ETH was in a multi-year
        # uptrend overall, so a short-only strategy fighting that trend nets a loss even
        # though many individual signal-close trades book small gains.
        assert backtest_result.reporter.summary["algo"]["net_return_percent"] == pytest.approx(
            -83.564411327567, rel=1e-6
        )

    def test_algo_max_drawdown(self, backtest_result):
        assert backtest_result.reporter.summary["algo"]["max_drawdown_percent"] == pytest.approx(
            -92.2202, rel=1e-4
        )

    def test_algo_profit_factor(self, backtest_result):
        assert backtest_result.reporter.summary["algo"]["profit_factor"] == pytest.approx(
            1.4980817749468338, rel=1e-6
        )

    def test_algo_sharpe_ratio(self, backtest_result):
        assert backtest_result.reporter.summary["algo"]["sharpe_ratio"] == pytest.approx(
            -0.16098598096561348, rel=1e-6
        )


class TestGoldenTradesShort:
    def test_first_trade(self, backtest_result):
        t = backtest_result.reporter.trades[0]
        assert t.symbol == SYMBOL
        assert t.side == PositionSide.short
        assert t.open_price == pytest.approx(341.71)
        assert t.close_price == pytest.approx(289.97)
        assert t.close_reason == CloseReason.signal
        assert t.pnl == pytest.approx(0.17843225161223564, rel=1e-6)
        assert t.result == TradeResult.winner

    def test_last_trade(self, backtest_result):
        t = backtest_result.reporter.trades[-1]
        assert t.symbol == SYMBOL
        assert t.side == PositionSide.short
        assert t.open_price == pytest.approx(3705.62)
        assert t.close_price == pytest.approx(3475.6)
        assert t.close_reason == CloseReason.signal
        assert t.pnl == pytest.approx(0.06618137875474739, rel=1e-6)
        assert t.result == TradeResult.winner

    def test_no_tp_or_sl_trades(self, backtest_result):
        # Over this dataset every exit was a signal-close (fast/slow MA re-cross) -- the
        # 30%/60% SL/TP bands were simply never hit on hourly ETH. Asserting the *count*
        # here still matters: a wrong-signed short SL/TP (see _check_short_sl_tp) would
        # very likely change this to nonzero, since an inverted trigger would fire on
        # ordinary price noise almost immediately after every entry.
        reasons = {t.close_reason for t in backtest_result.reporter.trades}
        assert CloseReason.tp not in reasons
        assert CloseReason.sl not in reasons
        assert reasons == {CloseReason.signal}
