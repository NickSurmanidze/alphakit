# Backtester Architecture

A candle-by-candle simulation engine. Each module owns one concern; they are wired together by `Backtester` and stepped in lock-step through historical OHLC data.

---

## Package layout

```
src/backtester/
├── backtest_runner.py       # Orchestrator (Backtester)
├── market.py                # Market data store
├── portfolio.py             # Strategy aggregation
├── rebalancer.py            # Order execution
├── indicators.py            # Technical indicator wrappers
├── exchange_config.py       # Exchange preset constants
├── exchange/                # Exchange simulation sub-package
│   ├── core.py              #   Exchange — wires balance/orders/positions
│   ├── balance.py           #   Balance, Transactions
│   ├── order.py             #   Order, Orders
│   ├── position.py          #   Position, Positions
│   └── types.py             #   Enums and TypedDicts
├── strategies/              # Strategy sub-package
│   ├── base.py              #   Strategy (ABC), Trade, Allocation, enums
│   └── ma_crossover.py      #   MaCrossoverStrategy
└── performance/             # Performance reporting sub-package
    ├── analyzer.py          #   PerformanceAnalyzer
    ├── metrics.py           #   Pure metric functions
    └── charts.py            #   Plotly figure builder
```

---

## Data flow

```
CSV / DB
  └─ MarketDataFromCSV ──► Market.add_market()
                               Market.add_indicator()  ◄── indicators.Indicators
                               Market.compile()
                                   │
                                   ▼
                        ┌─── Backtester.run_all() ───────────────────────┐
                        │  loop: Market.set_next_candle_as_current()      │
                        │    1. Exchange.run_step()                        │
                        │    2. Portfolio.refresh()                        │
                        │    3. Rebalancer.refresh()                       │
                        │    4. PerformanceAnalyzer.take_snapshot()        │
                        └────────────────────────────────────────────────-┘
                                   │
                                   ▼
                        PerformanceAnalyzer.generate_report()
                            → summary dict  (returns, drawdown, Sharpe …)
                            → trades list   (per-trade PnL, holding period)
                            → merged df     (daily time-series)
                                   │
                         ┌─────────┴──────────┐
                         ▼                    ▼
                  show_plot()           save_plot(filename)
```

---

## Modules

### `market.py` — Market data store

Holds OHLC DataFrames and indicator Series for every symbol. `compile()` converts them into a flat dict (`market.data`) keyed by `close_timestamp → candle_dict`, enabling O(1) lookup at each step. Column parsing is done once outside the loop (O(cols)) using `to_dict("records")` for fast row conversion.

```python
market = Market()
ohlc = MarketDataFromCSV(symbol="ETH/USD", ..., path="BINANCE_ETHUSDT_1H.csv").get_df()
market.add_market(symbol="ETH/USD", df=ohlc)
market.add_indicator("ETH/USD", 1, "hour", "sma_90", Indicators.sma(ohlc, 90))
market.compile()
# market.current now holds the candle dict for the current step
```

Key attribute: `market.current` — the live candle dict consumed by every other module each step.

---

### `exchange/` — Exchange simulation

Simulates order matching, position management, and balance accounting. Import from the package root:

```python
from backtester.exchange import Exchange, MarketType, MarginAllocationType, PositionSide, ...
```

Four internal sub-objects wired together by `Exchange`:

| Sub-object | File | Responsibility |
|---|---|---|
| `Transactions` | `balance.py` | Deposits / withdrawals ledger |
| `Balance` | `balance.py` | Free / used / total per asset, USD conversion |
| `Orders` | `order.py` | Create, match, and cancel limit / market / stop-loss orders |
| `Positions` | `position.py` | Open / increase / reduce / close / liquidate futures positions |

`Exchange.run_step()` processes the current candle: triggers limit orders whose price was hit, updates position PnL, checks liquidations.

```python
exchange = Exchange(
    market=market,
    slippage=0.002, maker_fee=0.001, taker_fee=0.00075,
    market_type=MarketType.future,
    max_leverage=10,
    margin_allocation_type=MarginAllocationType.cross,
)
exchange.transactions.add_deposit(asset="USD", volume=1000)
```

---

### `strategies/` — Trading strategies

`Strategy` (abstract base class in `base.py`) describes *when* to be in the market and at what size. Subclasses implement `refresh()`, which updates `self.allocation` to reflect the desired portfolio state.

Each strategy maintains an `Allocation` — the **desired target state** — which the rebalancer reconciles against the live exchange state:

```
Allocation
 ├── positions: list[AllocationPosition]   # symbol, side (long/short), % of portfolio
 └── orders:    list[AllocationOrder]      # TP limit / SL stop-loss orders
```

`Strategy` also provides concrete helpers `open_trade()` / `close_trade()` which append `Trade` objects to `trade_history`. Each `Trade` records symbol, side, open/close price, PnL, result, close reason, and `holding_period` (`pd.Timedelta`).

**Included strategy: `MaCrossoverStrategy`** — enters long or short when a fast MA crosses a slow MA; exits on the reverse cross, a TP limit fill, or an SL stop-loss fill.

```python
from backtester.strategies import MaCrossoverStrategy, StrategyDirection

strategy = MaCrossoverStrategy(
    key="my_strategy", market=market, symbol="ETH/USD",
    direction=StrategyDirection.long,
    fast_indicator_key="sma_20", slow_indicator_key="sma_50",
    tp_percent=0.006, sl_percent=0.003,
)
```

---

### `portfolio.py` — Strategy aggregation

`Portfolio` holds a list of `WeightedStrategy` pairs and merges their individual `Allocation` objects into a single `merged_allocation`.  
Net-out logic: if strategy A wants 80 % long ETH and strategy B wants 50 % short ETH, the merged result is 30 % long ETH.

```python
from backtester.portfolio import Portfolio, WeightedStrategy

portfolio = Portfolio(
    weighted_strategies=[
        WeightedStrategy(weight=0.6, strategy=strategy_a),
        WeightedStrategy(weight=0.4, strategy=strategy_b),
    ],
    output_scale=1,
)
```

`Portfolio.refresh()` calls every strategy's `refresh()`, then re-merges if any allocation changed.

---

### `rebalancer.py` — Order execution

Watches `portfolio.signal_allocation_change_time_hash`. When the hash changes (any strategy updated its allocation), `Rebalancer.rebalance()`:

1. Cancels all open orders.
2. Closes positions absent from the new allocation.
3. Opens / increases / reduces positions to match target sizes.
4. Re-creates TP / SL orders.

---

### `performance/` — Performance analysis

Takes one snapshot per candle during the run (balance, exposure). After `run_all()` completes, `generate_report()`:

- Resamples snapshots to daily frequency.
- Computes cumulative returns, drawdown, and a full set of risk metrics for the algo using pure functions from `metrics.py`.
- Computes the same for each `benchmark_symbols` (buy-and-hold).
- Populates `summary` dict and `merged` DataFrame.

Chart output is **explicit opt-in** — `generate_report()` has no side effects on display:

```python
reporter.generate_report()      # compute only
reporter.show_plot()            # open in browser
reporter.save_plot("out.html")  # write to file
```

See [performance/README.md](performance/README.md) for the full API and summary key reference.

```python
from backtester.performance import PerformanceAnalyzer

reporter = PerformanceAnalyzer(
    market=market,
    benchmark_symbols=["ETH/USD"],
    exchange=exchange,
    portfolio=portfolio,
    risk_free_rate=0.0,   # annual, decimal
    key="algo",
)
```

---

### `indicators.py` — Technical indicators

Thin wrappers around `pandas_ta`: `Indicators.sma`, `.ema`, `.rsi`, `.macd`, `.stoch`, `.bollinger_bands`. Each returns a Series or DataFrame aligned to the OHLC index, ready to pass directly to `Market.add_indicator()`.

---

### `backtest_runner.py` — Orchestrator (`Backtester`)

Wires market + exchange + portfolio + rebalancer + reporter and drives the main loop.

```python
bt = Backtester(market=market, exchange=exchange, portfolio=portfolio, reporter=reporter)
bt.exchange.transactions.add_deposit(asset="USD", volume=1000)
bt.run_all()
print(bt.reporter.summary)
```

`run_all()` steps through every compiled candle in chronological order, then collects trade history from all strategies and calls `generate_report()`.

---

## Minimal end-to-end example

```python
from backtester.backtest_runner import Backtester
from backtester.exchange import Exchange, MarginAllocationType, MarketType
from backtester.indicators import Indicators
from backtester.market import Market, MarketDataFromCSV
from backtester.performance import PerformanceAnalyzer
from backtester.portfolio import Portfolio, WeightedStrategy
from backtester.strategies import MaCrossoverStrategy, StrategyDirection

market = Market()
ohlc = MarketDataFromCSV(
    symbol="ETH/USD", date_from="2020-01-01", date_to="2024-01-01",
    interval=1, unit_of_time="hour", path="datasets/binance/BINANCE_ETHUSDT_1H.csv",
).get_df()
market.add_market(symbol="ETH/USD", df=ohlc)
market.add_indicator("ETH/USD", 1, "hour", "sma_20", Indicators.sma(ohlc, 20))
market.add_indicator("ETH/USD", 1, "hour", "sma_50", Indicators.sma(ohlc, 50))
market.compile()

strategy = MaCrossoverStrategy(
    key="demo", market=market, symbol="ETH/USD",
    direction=StrategyDirection.long,
    fast_indicator_key="sma_20", slow_indicator_key="sma_50",
    tp_percent=0.01, sl_percent=0.005,
)
portfolio = Portfolio([WeightedStrategy(weight=1, strategy=strategy)], output_scale=1)
exchange  = Exchange(market=market, slippage=0.001, maker_fee=0.001, taker_fee=0.00075,
                     market_type=MarketType.future, max_leverage=5)
reporter  = PerformanceAnalyzer(market=market, exchange=exchange,
                                portfolio=portfolio, benchmark_symbols=["ETH/USD"])

market.reset()
bt = Backtester(market=market, portfolio=portfolio, exchange=exchange, reporter=reporter)
bt.exchange.transactions.add_deposit(asset="USD", volume=1000)
bt.run_all()

print(bt.reporter.summary)
reporter.show_plot()
```
