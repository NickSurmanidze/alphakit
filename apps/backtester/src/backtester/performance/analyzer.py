"""Refactored PerformanceAnalyzer using dedicated metric functions and chart helpers."""

from __future__ import annotations

import copy

import numpy as np
import pandas as pd

from backtester.exchange import Exchange
from backtester.exchange.event_log import (
    EventLog,
    PositionClosed,
    PositionLiquidated,
    PositionReduced,
)
from backtester.market import Market
from backtester.performance import metrics
from backtester.performance.charts import make_report_figure
from backtester.performance.report_html import render_summary_html
from backtester.portfolio import Portfolio
from backtester.strategies import Trade


def _returns_from_series(series: pd.Series) -> tuple[pd.Series, pd.Series]:
    """Forward-fills gaps (e.g. non-trading weekends/holidays once a daily-resampled
    series has empty buckets for a non-24/7 market like futures) before computing
    simple/log returns, then returns (simple_returns, log_returns).

    Without the explicit ffill, a gap day silently poisons the very next real trading
    day's return into NaN too (a NaN denominator in pct_change), erasing exactly the
    move that happened across the gap -- e.g. every Monday's return for a weekday-only
    market would be lost. pandas' pct_change() used to paper over this via an implicit
    fill_method='pad' default, but that default is deprecated and not something this
    package should depend on implicitly (crypto data has no gaps so this was invisible
    until futures data started flowing through here). `fill_method=None` makes the
    ffill-then-diff behavior explicit and pandas-version-proof instead.
    """
    filled = series.ffill()
    simple_returns = filled.pct_change(fill_method=None)
    log_returns = pd.Series(np.log1p(simple_returns), index=simple_returns.index)
    return simple_returns, log_returns


class PerformanceAnalyzer:
    """Collects a per-candle equity/exposure snapshot over the course of a backtest,
    then computes returns, drawdown, and a full set of risk/trade metrics against those
    snapshots and the strategy's trade history."""

    def __init__(  # noqa: PLR0913
        self,
        market: Market,
        benchmark_symbols: list[str],
        exchange: Exchange | None = None,
        portfolio: Portfolio | None = None,
        key: str = "algo",
        risk_free_rate: float = 0.0,
        periods_per_year: int = 365,  # matches metrics.py's own default (crypto, 24/7)
    ):
        """`exchange`/`portfolio` are optional so a merge_reports() target can be built
        without either (it only needs pre-computed snapshots/trades from other
        analyzers). `periods_per_year` controls Sharpe/Sortino/volatility/alpha
        annualization -- defaults to 365 (correct for crypto's 24/7 markets, and
        preserves this class's historical behavior); pass ~252 for futures/equities,
        where every calendar day still gets a return row (weekends show 0%, see
        _returns_from_series) but annualizing by the number of REAL trading days is
        the industry-standard convention and won't match 365-based figures."""
        self.exchange = exchange
        self.portfolio = portfolio
        self.market = market
        self.trades: list[Trade] = []
        self.key = key
        self.benchmark_symbols = benchmark_symbols
        self.risk_free_rate = risk_free_rate
        self.periods_per_year = periods_per_year

        self.raw_snapshots: dict[pd.Timestamp, dict] = {}
        self.merged: pd.DataFrame | None = None

        self.summary: dict[str, dict[str, float]] = {}
        # Populated only by merge_reports() -- timestamps present in one source
        # analyzer's snapshots but missing from at least one other, and therefore
        # silently skipped by merge_external_snapshots rather than summed in.
        self.skipped_timestamps: list[pd.Timestamp] = []
        # Populated only by merge_reports(), as the union of every source analyzer's
        # realized dollar PnLs -- a merged analyzer has no live exchange/event_log of
        # its own to derive this from (see _get_realized_pnls()).
        self._realized_pnls_override: list[float] | None = None

    @property
    def event_log(self) -> EventLog | None:
        """The exchange's structured audit trail, if this analyzer has a live exchange
        reference -- recording is owned by Exchange (it's the source of the state
        changes), this just gives reports/tests a natural place to reach it from."""
        return self.exchange.event_log if self.exchange else None

    def _get_realized_pnls(self) -> list[float]:
        """Realized dollar PnL for every position close/reduce/liquidation. Uses
        _realized_pnls_override instead if merge_reports() set one (a merged analyzer
        has no live exchange/event_log of its own). Empty if neither is available
        (e.g. event logging was disabled on the source exchange)."""
        if self._realized_pnls_override is not None:
            return self._realized_pnls_override
        return [pnl for _, pnl in self._get_realized_pnls_with_time()]

    def _get_realized_pnls_with_time(self) -> list[tuple[pd.Timestamp, float]]:
        """Same events as _get_realized_pnls(), paired with each event's own
        timestamp -- needed to slice realized PnL by period in
        generate_period_report() (the flat list _get_realized_pnls() itself returns
        has no timestamps to filter by). Sourced from the EventLog:
        PositionClosed/PositionReduced carry their own realized_pnl_in_usd;
        PositionLiquidated doesn't (it forfeits the position's entire margin, per
        Positions.liquidate_position), so that loss is reconstructed as
        -margin_used_in_usd. Ignores _realized_pnls_override (a merge_reports()
        target has no per-event timestamps of its own to give)."""
        if self.event_log is None:
            return []
        pairs: list[tuple[pd.Timestamp, float]] = []
        for event in self.event_log.get_events():
            if isinstance(event, (PositionClosed, PositionReduced)):
                pairs.append((pd.Timestamp(event.time), event.realized_pnl_in_usd))
            elif isinstance(event, PositionLiquidated):
                pairs.append((pd.Timestamp(event.time), -event.margin_used_in_usd))
        return pairs

    # ------------------------------------------------------------------
    # Snapshot collection
    # ------------------------------------------------------------------

    def take_snapshot(self) -> None:
        """Records one raw_snapshots entry for the current candle: total balance,
        net balance (excluding deposits/withdrawals that tick), and exchange/portfolio
        exposure. Called once per tick by Backtester.run_step(); no-ops without a live
        exchange/current candle."""
        if self.exchange is None or self.exchange.market.current is None:
            return

        ts = self.exchange.market.current["time_close"]
        balance_total = self.exchange.get_asset_total_in_usd()

        snapshot: dict = {
            "ts": ts,
            "balance": balance_total,
            "net_balance": 0.0,
            "transactions": 0.0,
            "exchange_long_exposure": 0.0,
            "exchange_short_exposure": 0.0,
            "exchange_net_exposure": 0.0,
            "exchange_gross_exposure": 0.0,
            "portfolio_long_exposure": 0.0,
            "portfolio_short_exposure": 0.0,
            "portfolio_net_exposure": 0.0,
            "portfolio_gross_exposure": 0.0,
        }

        if self.portfolio:
            snapshot.update(
                {
                    "portfolio_net_exposure": self.portfolio.exposure["net"],
                    "portfolio_gross_exposure": self.portfolio.exposure["gross"],
                    "portfolio_long_exposure": self.portfolio.exposure["long"],
                    "portfolio_short_exposure": self.portfolio.exposure["short"],
                }
            )

        exchange_exposure = self.exchange.get_exposure()
        snapshot.update(
            {
                "exchange_net_exposure": exchange_exposure["net"],
                "exchange_gross_exposure": exchange_exposure["gross"],
                "exchange_long_exposure": exchange_exposure["long"],
                "exchange_short_exposure": exchange_exposure["short"],
            }
        )

        if self.market.current is not None and self.market.current["num"] > 0:
            transactions = self.exchange.transactions.get_transactions_by_timestamp(timestamp=ts)
            if transactions:
                deposits = sum(t["value_in_usd"] for t in transactions if t["type"] == "deposit")
                withdrawals = sum(t["value_in_usd"] for t in transactions if t["type"] != "deposit")
                snapshot["transactions"] = deposits - withdrawals

        snapshot["net_balance"] = snapshot["balance"] - snapshot["transactions"]
        self.raw_snapshots[ts] = snapshot

    def merge_external_snapshots(self, snapshots: list[dict[pd.Timestamp, dict]]) -> None:
        """Sums balance/exposure across multiple independent backtests' raw_snapshots at
        each shared timestamp, replacing self.raw_snapshots with the combined result.
        Timestamps missing from any one source are silently skipped for that
        contribution (see merge_reports's _find_skipped_timestamps for surfacing which
        ones)."""
        if not snapshots:
            return
        snaps = copy.deepcopy(snapshots)
        raw = snaps.pop(0)
        for ts in raw:
            for snap in snaps:
                if ts not in snap:
                    continue
                raw[ts]["balance"] += snap[ts]["balance"]
                raw[ts]["transactions"] += snap[ts]["transactions"]
                raw[ts]["net_balance"] += snap[ts]["net_balance"]
                raw[ts]["exchange_long_exposure"] += snap[ts]["exchange_long_exposure"]
                raw[ts]["exchange_short_exposure"] += snap[ts]["exchange_short_exposure"]
                raw[ts]["exchange_gross_exposure"] = (
                    raw[ts]["exchange_long_exposure"] + raw[ts]["exchange_short_exposure"]
                )
                raw[ts]["exchange_net_exposure"] = abs(
                    raw[ts]["exchange_long_exposure"] - raw[ts]["exchange_short_exposure"]
                )
                raw[ts]["portfolio_long_exposure"] += snap[ts]["portfolio_long_exposure"]
                raw[ts]["portfolio_short_exposure"] += snap[ts]["portfolio_short_exposure"]
                raw[ts]["portfolio_gross_exposure"] = (
                    raw[ts]["portfolio_long_exposure"] + raw[ts]["portfolio_short_exposure"]
                )
                raw[ts]["portfolio_net_exposure"] = abs(
                    raw[ts]["portfolio_long_exposure"] - raw[ts]["portfolio_short_exposure"]
                )
        self.raw_snapshots = raw

    # ------------------------------------------------------------------
    # DataFrame builders
    # ------------------------------------------------------------------

    def _build_algo_df(self) -> pd.DataFrame:
        """Resamples raw_snapshots to daily frequency and derives simple/log/cumulative
        returns and drawdown from net_balance."""
        algo_df = (
            pd.DataFrame(list(self.raw_snapshots.values()))
            .assign(ts=lambda x: x["ts"])
            .set_index("ts")
            .resample("D")
            .agg(
                {
                    "balance": "last",
                    "net_balance": "last",
                    "exchange_long_exposure": "last",
                    "exchange_short_exposure": "last",
                    "exchange_net_exposure": "last",
                    "exchange_gross_exposure": "last",
                    "portfolio_long_exposure": "last",
                    "portfolio_short_exposure": "last",
                    "portfolio_net_exposure": "last",
                    "portfolio_gross_exposure": "last",
                }
            )
        )
        simple_returns, log_returns = _returns_from_series(algo_df["net_balance"])
        return (
            algo_df.assign(simple_returns=simple_returns, log_returns=log_returns)
            .assign(cumulative_returns=lambda x: np.exp(x["log_returns"].cumsum()))
            .assign(high_watermark=lambda x: x["cumulative_returns"].cummax())
            .assign(
                drawdown=lambda x: (
                    (x["high_watermark"] - x["cumulative_returns"]) / x["high_watermark"] * -1
                )
            )
        )

    def _build_symbol_df(self, symbol: str) -> pd.DataFrame:
        """Resamples `symbol`'s own OHLC to daily frequency and derives the same
        returns/drawdown series as _build_algo_df, for use as a buy-and-hold benchmark."""
        symbol_df = (
            self.market.get_market(symbol)
            .assign(ts=lambda x: x["time_close"])
            .set_index("ts")
            .resample("D")
            .agg({"close": "last", "time_open": "first", "time_close": "last"})
        )
        simple_returns, log_returns = _returns_from_series(symbol_df["close"])
        return (
            symbol_df.assign(simple_returns=simple_returns, log_returns=log_returns)
            .assign(cumulative_returns=lambda x: np.exp(x["log_returns"].cumsum()))
            .assign(high_watermark=lambda x: x["cumulative_returns"].cummax())
            .assign(
                drawdown=lambda x: (
                    (x["high_watermark"] - x["cumulative_returns"]) / x["high_watermark"] * -1
                )
            )
        )

    def _build_merged_df(self) -> pd.DataFrame | None:
        """Builds self.merged: a daily-frequency DataFrame combining the algo's own
        returns/exposure series (prefixed by `self.key`) with each benchmark symbol's
        buy-and-hold returns series (prefixed by the symbol)."""
        if self.market.merged is None:
            return None

        report_df = (
            pd.DataFrame(
                {
                    "time_open": self.market.merged["time_open"],
                    "time_close": self.market.merged["time_close"],
                    "ts": self.market.merged["time_close"],
                }
            )
            .set_index("ts")
            .resample("D")
            .agg({"time_open": "first", "time_close": "last"})
        )

        algo_df = self._build_algo_df()
        for col in ("simple_returns", "log_returns", "cumulative_returns", "drawdown"):
            report_df[f"{self.key}__{col}"] = algo_df[col]
        for col in (
            "exchange_long_exposure",
            "exchange_short_exposure",
            "exchange_net_exposure",
            "exchange_gross_exposure",
            "portfolio_long_exposure",
            "portfolio_short_exposure",
            "portfolio_net_exposure",
            "portfolio_gross_exposure",
        ):
            report_df[f"{self.key}__{col}"] = algo_df[col]

        for symbol in self.benchmark_symbols:
            sym_df = self._build_symbol_df(symbol)
            for col in ("simple_returns", "log_returns", "cumulative_returns", "drawdown"):
                report_df[f"{symbol}__{col}"] = sym_df[col]

        self.merged = report_df
        return self.merged

    # ------------------------------------------------------------------
    # Report generation (no side effects on chart output)
    # ------------------------------------------------------------------

    def _algo_metric_block(  # noqa: PLR0913
        self,
        returns: pd.Series,
        cum: pd.Series,
        trades: list[Trade],
        realized_pnls: list[float],
        exposure: pd.Series,
        n_days: int,
    ) -> dict[str, float]:
        """The full algo-side metric set (Sharpe, Sortino, CAGR, drawdown, Ulcer
        Index, VaR/CVaR, skew/kurtosis, profit factor, win rate, R-multiple
        expectancy, ...), computed from whatever `returns`/`cum`/`trades`/
        `realized_pnls`/`exposure`/`n_days` are handed to it. Shared by
        generate_report() (the whole-history period) and generate_period_report()
        (each year/quarter sub-period) so both stay numerically in sync from one
        place instead of two independently-maintained copies of the same ~25-metric
        block. Recomputes high-watermark/drawdown from `cum` rather than reading a
        pre-built column, since a sub-period's `cum` isn't backed by one."""
        gross_return = float(cum.iloc[-1]) if len(cum) else 1.0
        high_watermark = cum.cummax()
        drawdown = (high_watermark - cum) / high_watermark * -1
        max_dd = float(drawdown.min()) if len(drawdown) else 0.0
        ann_return = metrics.cagr(gross_return, n_days)

        return {
            "gross_return_percent": gross_return * 100,
            "net_return_percent": (gross_return - 1) * 100,
            "max_drawdown_percent": round(max_dd, 6) * 100,
            "max_drawdown_duration_days": float(metrics.max_drawdown_duration_days(cum)),
            "sharpe_ratio": metrics.sharpe_ratio(
                returns, self.risk_free_rate, self.periods_per_year
            ),
            "sortino_ratio": metrics.sortino_ratio(
                returns, self.risk_free_rate, self.periods_per_year
            ),
            "annualized_volatility_percent": (
                metrics.annualized_volatility(returns, self.periods_per_year) * 100
            ),
            "cagr_percent": ann_return * 100,
            "calmar_ratio": metrics.calmar_ratio(ann_return, max_dd),
            "recovery_factor": metrics.recovery_factor(gross_return - 1, max_dd),
            "ulcer_index": metrics.ulcer_index(cum),
            "var_95_percent": metrics.value_at_risk(returns, 0.95) * 100,
            "cvar_95_percent": metrics.conditional_value_at_risk(returns, 0.95) * 100,
            "returns_skewness": metrics.returns_skewness(returns),
            "returns_kurtosis": metrics.returns_kurtosis(returns),
            "profit_factor": metrics.profit_factor(trades),
            "dollar_profit_factor": metrics.dollar_profit_factor(realized_pnls),
            "dollar_expectancy": metrics.dollar_expectancy(realized_pnls),
            "win_rate_percent": metrics.win_rate(trades) * 100,
            "avg_win_loss_ratio": metrics.avg_win_loss_ratio(trades),
            "r_multiple_expectancy": metrics.r_multiple_expectancy(trades),
            "max_consecutive_losses": float(metrics.max_consecutive_losses(trades)),
            "avg_holding_period_min": metrics.avg_holding_period_minutes(trades),
            "time_in_market_percent": metrics.time_in_market_percent(exposure),
            "closed_trades": float(len(trades)),
            "winner_trades": float(
                sum(1 for t in trades if t.result is not None and t.result.value == "winner")
            ),
            "loser_trades": float(
                sum(1 for t in trades if t.result is not None and t.result.value == "loser")
            ),
        }

    def generate_report(self) -> dict[str, dict[str, float]] | None:
        """Builds self.merged and populates self.summary with the full metric set
        (Sharpe, Sortino, CAGR, drawdown, Ulcer Index, VaR/CVaR, skew/kurtosis, profit
        factor, win rate, R-multiple expectancy, beta/correlation/alpha vs. each
        benchmark, ...) for the algo and each benchmark symbol. Returns None (leaving
        summary unset) if there's no data to report on."""
        self._build_merged_df()

        if self.merged is None or self.merged.shape[0] == 0:
            return None

        algo_returns = self.merged[f"{self.key}__simple_returns"].dropna()
        algo_cum = self.merged[f"{self.key}__cumulative_returns"]
        n_days = (self.merged.index[-1] - self.merged.index[0]).days or 1
        realized_pnls = self._get_realized_pnls()

        self.summary[self.key] = self._algo_metric_block(
            returns=algo_returns,
            cum=algo_cum,
            trades=self.trades,
            realized_pnls=realized_pnls,
            exposure=self.merged[f"{self.key}__exchange_gross_exposure"],
            n_days=n_days,
        )

        for symbol in self.benchmark_symbols:
            sym_returns = self.merged[f"{symbol}__simple_returns"].dropna()
            sym_cum = self.merged[f"{symbol}__cumulative_returns"]
            sym_gross = float(sym_cum.values[-1])
            sym_max_dd = float(self.merged[f"{symbol}__drawdown"].min())
            sym_ann = metrics.cagr(sym_gross, n_days)
            sym_beta = metrics.beta(algo_returns, sym_returns)

            self.summary[symbol] = {
                "sharpe_ratio": metrics.sharpe_ratio(
                    sym_returns, self.risk_free_rate, self.periods_per_year
                ),
                "sortino_ratio": metrics.sortino_ratio(
                    sym_returns, self.risk_free_rate, self.periods_per_year
                ),
                "annualized_volatility_percent": (
                    metrics.annualized_volatility(sym_returns, self.periods_per_year) * 100
                ),
                "cagr_percent": sym_ann * 100,
                "calmar_ratio": metrics.calmar_ratio(sym_ann, sym_max_dd),
                "recovery_factor": metrics.recovery_factor(sym_gross - 1, sym_max_dd),
                "max_drawdown_percent": round(sym_max_dd, 6) * 100,
                "max_drawdown_duration_days": float(metrics.max_drawdown_duration_days(sym_cum)),
                "ulcer_index": metrics.ulcer_index(sym_cum),
                "var_95_percent": metrics.value_at_risk(sym_returns, 0.95) * 100,
                "cvar_95_percent": metrics.conditional_value_at_risk(sym_returns, 0.95) * 100,
                "returns_skewness": metrics.returns_skewness(sym_returns),
                "returns_kurtosis": metrics.returns_kurtosis(sym_returns),
                "gross_return_percent": sym_gross * 100,
                "net_return_percent": (sym_gross - 1) * 100,
            }

            self.summary[self.key][f"beta_vs_{symbol}"] = sym_beta
            self.summary[self.key][f"correlation_vs_{symbol}"] = metrics.correlation(
                algo_returns, sym_returns
            )
            self.summary[self.key][f"alpha_percent_vs_{symbol}"] = (
                metrics.alpha(algo_returns, sym_returns, sym_beta, self.periods_per_year) * 100
            )

        return self.summary

    def generate_period_report(self, freq: str) -> dict[str, dict[str, float]]:
        """Adds one extra self.summary entry per `freq`-period (e.g. `algo_2019`,
        `algo_2020` for `freq="Y"`; `algo_2019Q3` for `freq="Q"` -- any pandas period
        alias works) alongside the whole-history `self.summary[self.key]` entry
        generate_report() already builds. Same metric set, via the same
        _algo_metric_block() helper, computed from just that period's own slice of
        merged/trades/realized PnLs rather than the whole history.

        Cumulative return/drawdown are recomputed *within each period* (compounding
        restarts at 1.0 at the period's first bar) rather than sliced out of the
        whole-history cumulative series -- otherwise every later period would just
        look like "the whole history so far" instead of how that period did on its
        own.

        Requires generate_report() to have already run (needs self.merged/
        self.trades in their final state) -- no-ops, returning self.summary
        unchanged, if there's no merged data yet.
        """
        if self.merged is None or self.merged.shape[0] == 0:
            return self.summary

        returns_col = f"{self.key}__simple_returns"
        exposure_col = f"{self.key}__exchange_gross_exposure"
        realized_pnls_with_time = self._get_realized_pnls_with_time()
        period_index = pd.DatetimeIndex(self.merged.index).to_period(freq)

        for period in period_index.unique():
            period_merged = self.merged.loc[period_index == period]
            period_returns = period_merged[returns_col].dropna()
            if period_returns.empty:
                continue

            period_cum = (1 + period_returns).cumprod()
            period_n_days = (period_merged.index[-1] - period_merged.index[0]).days or 1
            period_trades = [
                t
                for t in self.trades
                if t.time_close is not None and t.time_close.to_period(freq) == period
            ]
            period_pnls = [
                pnl for ts, pnl in realized_pnls_with_time if ts.to_period(freq) == period
            ]

            self.summary[f"{self.key}_{period}"] = self._algo_metric_block(
                returns=period_returns,
                cum=period_cum,
                trades=period_trades,
                realized_pnls=period_pnls,
                exposure=period_merged[exposure_col],
                n_days=period_n_days,
            )

        return self.summary

    def summary_dataframe(self) -> pd.DataFrame:
        """Reshapes self.summary (a dict keyed by self.key/benchmark symbol, each a
        dict of metric name -> value) into a DataFrame -- one row per metric, one
        column per key/symbol -- for a readable table instead of a wall of JSON.
        Empty DataFrame if generate_report() hasn't run yet."""
        if not self.summary:
            return pd.DataFrame()
        df = pd.DataFrame(self.summary).round(4)
        df.index.name = "metric"
        return df

    def summary_html_table(self, split: str | None = None) -> str:
        """HTML rendering of summary_dataframe(): a plain-English description column
        plus color-coding (absolute quality bands where a fixed threshold is
        meaningful, a "best in row" highlight across columns otherwise) -- see
        backtester.performance.report_html for the per-metric rules. Wrap the
        returned string in IPython.display.HTML(...) to render in a notebook.

        split, if given ("Y" for calendar year, "Q" for calendar quarter, or any
        other pandas period alias), calls generate_period_report(split) and inserts
        one extra column per period right after the whole-history algo column --
        e.g. algo, algo_2019, algo_2020, ..., then the benchmark column(s) as usual.
        Omit (the default) for the original single-column-per-key table, unchanged.
        """
        if split is None:
            return render_summary_html(self.summary_dataframe())

        self.generate_period_report(split)
        df = self.summary_dataframe()

        period_prefix = f"{self.key}_"
        period_cols = sorted(
            (c for c in df.columns if str(c).startswith(period_prefix)),
            key=lambda c: str(c)[len(period_prefix):],
        )
        other_cols = [c for c in df.columns if c != self.key and c not in period_cols]
        ordered_cols = [self.key, *period_cols, *other_cols]
        return render_summary_html(df[ordered_cols])

    # ------------------------------------------------------------------
    # Chart output — explicit opt-in
    # ------------------------------------------------------------------

    def show_plot(self, additional_fields: pd.DataFrame | None = None) -> None:
        """Opens the report chart in a browser (no-op if generate_report() hasn't run)."""
        fig = self._make_figure(additional_fields)
        if fig is not None:
            fig.show()

    def save_plot(self, filename: str, additional_fields: pd.DataFrame | None = None) -> None:
        """Writes the report chart to `filename` as HTML (raises on an empty filename;
        no-op if generate_report() hasn't run)."""
        if not filename:
            raise ValueError("filename must not be empty")
        fig = self._make_figure(additional_fields)
        if fig is not None:
            fig.write_html(filename)

    def _make_figure(self, additional_fields: pd.DataFrame | None = None):
        """Builds the Plotly report figure from self.merged/summary/trades, or None if
        there's no merged data yet."""
        if self.merged is None:
            return None
        return make_report_figure(
            merged=self.merged,
            summary=self.summary,
            trades=self.trades,
            benchmark_symbols=self.benchmark_symbols,
            key=self.key,
            additional_fields=additional_fields,
        )

    # ------------------------------------------------------------------
    # Legacy aliases (keep callers that use old method names working)
    # ------------------------------------------------------------------

    def save_report_plot(
        self, filename: str, additional_fields: pd.DataFrame | None = None
    ) -> None:
        """Deprecated alias for save_plot()."""
        self.save_plot(filename, additional_fields)

    def show_report_plot(self, additional_fields: pd.DataFrame | None = None) -> None:
        """Deprecated alias for show_plot()."""
        self.show_plot(additional_fields)

    def generate_algo_report_dataframe(self) -> pd.DataFrame:
        """Public wrapper around _build_algo_df()."""
        return self._build_algo_df()

    def generate_symbol_report_dataframe(self, symbol: str) -> pd.DataFrame:
        """Public wrapper around _build_symbol_df()."""
        return self._build_symbol_df(symbol)

    def generate_merged_report_dataframe(self) -> pd.DataFrame | None:
        """Public wrapper around _build_merged_df()."""
        return self._build_merged_df()


def _find_skipped_timestamps(snapshots: list[dict[pd.Timestamp, dict]]) -> list[pd.Timestamp]:
    """Timestamps present in the first snapshot dict but missing from at least one of the
    others -- mirrors merge_external_snapshots's own `if ts not in snap: continue` skip
    condition, so callers can see exactly which timestamps didn't get summed into every
    source rather than being silently dropped from the combined result."""
    if not snapshots:
        return []
    base_timestamps = set(snapshots[0].keys())
    skipped: set[pd.Timestamp] = set()
    for snapshot in snapshots[1:]:
        skipped |= base_timestamps - set(snapshot.keys())
    return sorted(skipped)


def merge_reports(
    analyzers: list[PerformanceAnalyzer], key: str = "combined"
) -> PerformanceAnalyzer:
    """Runs N independent backtests' `PerformanceAnalyzer`s through the same
    fetch-snapshots / concat-trades / generate-report dance the legacy notebook's
    `Reporter.merge_external_snapshots()` pattern required doing by hand, in one call.
    Each `analyzer` should already have `generate_report()` called on it (i.e. its
    source backtest already completed) -- this only reads `raw_snapshots`/`trades` off
    each, it doesn't run anything.
    """
    if not analyzers:
        raise ValueError("merge_reports requires at least one analyzer")

    first = analyzers[0]
    merged = PerformanceAnalyzer(
        market=first.market,
        benchmark_symbols=first.benchmark_symbols,
        key=key,
        risk_free_rate=first.risk_free_rate,
        periods_per_year=first.periods_per_year,
    )
    merged.merge_external_snapshots([a.raw_snapshots for a in analyzers])
    merged.trades = [trade for a in analyzers for trade in a.trades]
    merged.skipped_timestamps = _find_skipped_timestamps([a.raw_snapshots for a in analyzers])
    merged._realized_pnls_override = [pnl for a in analyzers for pnl in a._get_realized_pnls()]
    merged.generate_report()
    return merged
