"""Golden test: mixed long/short (StrategyDirection.both) variant of the canonical
ETH/USD MA-crossover scenario. Same CSV/fees/slippage/leverage as
test_backtester_golden.py -- flips direction on every cross instead of only entering
long. Covers the code path where a single strategy flips between PositionSide.long and
.short over its lifetime, which neither the long-only nor the short-only golden test
exercises on its own.
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
                    key="SMA_62_446_both",
                    market=market,
                    symbol=SYMBOL,
                    direction=StrategyDirection.both,
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


class TestGoldenMetricsMixed:
    def test_total_trades(self, backtest_result):
        # Exactly double the long-only/short-only count (224 each): every crossover now
        # closes whichever side is open AND opens the other, instead of only ever
        # entering one direction.
        assert len(backtest_result.reporter.trades) == 448

    def test_total_rebalances(self, backtest_result):
        assert len(backtest_result.exchange.get_logs()) == 462

    def test_algo_closed_trades(self, backtest_result):
        assert backtest_result.reporter.summary["algo"]["closed_trades"] == 448

    def test_algo_winner_trades(self, backtest_result):
        assert backtest_result.reporter.summary["algo"]["winner_trades"] == 187

    def test_algo_loser_trades(self, backtest_result):
        assert backtest_result.reporter.summary["algo"]["loser_trades"] == 261

    def test_algo_gross_return(self, backtest_result):
        assert backtest_result.reporter.summary["algo"]["gross_return_percent"] == pytest.approx(
            399.53772056404335, rel=1e-6
        )

    def test_algo_net_return(self, backtest_result):
        assert backtest_result.reporter.summary["algo"]["net_return_percent"] == pytest.approx(
            299.53772056404335, rel=1e-6
        )

    def test_algo_max_drawdown(self, backtest_result):
        assert backtest_result.reporter.summary["algo"]["max_drawdown_percent"] == pytest.approx(
            -78.1926, rel=1e-4
        )

    def test_algo_profit_factor(self, backtest_result):
        assert backtest_result.reporter.summary["algo"]["profit_factor"] == pytest.approx(
            1.7601165578480527, rel=1e-6
        )

    def test_algo_sharpe_ratio(self, backtest_result):
        assert backtest_result.reporter.summary["algo"]["sharpe_ratio"] == pytest.approx(
            0.6339167600934363, rel=1e-6
        )


class TestGoldenTradesMixed:
    def test_first_trade_is_short(self, backtest_result):
        # The very first crossover in the dataset is a downward cross, so with both
        # directions enabled the strategy's first-ever position is short -- identical
        # open/close/pnl to the short-only golden test's first trade, confirming the
        # "both" direction doesn't alter which side gets entered on a given cross, only
        # whether the *other* side is also allowed.
        t = backtest_result.reporter.trades[0]
        assert t.symbol == SYMBOL
        assert t.side == PositionSide.short
        assert t.open_price == pytest.approx(341.71)
        assert t.close_price == pytest.approx(289.97)
        assert t.pnl == pytest.approx(0.17843225161223564, rel=1e-6)
        assert t.result == TradeResult.winner

    def test_last_trade_is_long(self, backtest_result):
        t = backtest_result.reporter.trades[-1]
        assert t.symbol == SYMBOL
        assert t.side == PositionSide.long
        assert t.open_price == pytest.approx(3475.6)
        assert t.close_price == pytest.approx(3378.57)
        assert t.close_reason == CloseReason.signal
        assert t.pnl == pytest.approx(-0.0279174818736333, rel=1e-6)
        assert t.result == TradeResult.loser

    def test_trades_alternate_side(self, backtest_result):
        # A new position can only ever open on a crossover event, and consecutive real
        # crossovers always flip direction (fast can't cross above, then above again,
        # without crossing below in between) -- so across the *entire* trade sequence,
        # side must strictly alternate. This only holds over the unfiltered list: a
        # signal-reason-only view can show two same-side trades in a row whenever a
        # TP/SL closed the opposite-side position in between (that close isn't reason
        # signal, so it's invisible to a signal-only filter, but it still consumed a
        # slot in the alternation) -- which is exactly what direction=both should do.
        trades = backtest_result.reporter.trades
        for prev, cur in zip(trades, trades[1:], strict=False):
            assert prev.side != cur.side

    def test_both_tp_and_sl_reasons_occur(self, backtest_result):
        tp_trades = [t for t in backtest_result.reporter.trades if t.close_reason == CloseReason.tp]
        assert len(tp_trades) == 11
        for t in tp_trades:
            assert t.pnl == pytest.approx(0.6, rel=1e-6)
