"""Refactored PerformanceAnalyzer using dedicated metric functions and chart helpers."""

from __future__ import annotations

import copy

import numpy as np
import pandas as pd

from backtester.exchange import Exchange
from backtester.exchange.event_log import EventLog
from backtester.market import Market
from backtester.performance import metrics
from backtester.performance.charts import make_report_figure
from backtester.portfolio import Portfolio
from backtester.strategies import Trade


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
    ):
        """`exchange`/`portfolio` are optional so a merge_reports() target can be built
        without either (it only needs pre-computed snapshots/trades from other
        analyzers)."""
        self.exchange = exchange
        self.portfolio = portfolio
        self.market = market
        self.trades: list[Trade] = []
        self.key = key
        self.benchmark_symbols = benchmark_symbols
        self.risk_free_rate = risk_free_rate

        self.raw_snapshots: dict[pd.Timestamp, dict] = {}
        self.merged: pd.DataFrame | None = None

        self.summary: dict[str, dict[str, float]] = {}
        # Populated only by merge_reports() -- timestamps present in one source
        # analyzer's snapshots but missing from at least one other, and therefore
        # silently skipped by merge_external_snapshots rather than summed in.
        self.skipped_timestamps: list[pd.Timestamp] = []

    @property
    def event_log(self) -> EventLog | None:
        """The exchange's structured audit trail, if this analyzer has a live exchange
        reference -- recording is owned by Exchange (it's the source of the state
        changes), this just gives reports/tests a natural place to reach it from."""
        return self.exchange.event_log if self.exchange else None

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
        return (
            algo_df.assign(
                simple_returns=lambda x: x["net_balance"].pct_change(),
                log_returns=lambda x: np.log1p(x["net_balance"].pct_change()),
            )
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
        return (
            symbol_df.assign(
                simple_returns=lambda x: x["close"].pct_change(),
                log_returns=lambda x: np.log1p(x["close"].pct_change()),
            )
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

    def generate_report(self) -> dict[str, dict[str, float]] | None:
        """Builds self.merged and populates self.summary with the full metric set
        (Sharpe, Sortino, CAGR, drawdown, profit factor, win rate, ...) for the algo and
        each benchmark symbol. Returns None (leaving summary unset) if there's no data
        to report on."""
        self._build_merged_df()

        if self.merged is None or self.merged.shape[0] == 0:
            return None

        algo_returns = self.merged[f"{self.key}__simple_returns"].dropna()
        algo_cum = self.merged[f"{self.key}__cumulative_returns"]
        n_days = (self.merged.index[-1] - self.merged.index[0]).days or 1
        gross_return = float(algo_cum.values[-1])
        max_dd = float(self.merged[f"{self.key}__drawdown"].min())
        ann_return = metrics.cagr(gross_return, n_days)

        self.summary[self.key] = {
            "sharpe_ratio": metrics.sharpe_ratio(algo_returns, self.risk_free_rate),
            "sortino_ratio": metrics.sortino_ratio(algo_returns, self.risk_free_rate),
            "annualized_volatility_percent": metrics.annualized_volatility(algo_returns) * 100,
            "cagr_percent": ann_return * 100,
            "calmar_ratio": metrics.calmar_ratio(ann_return, max_dd),
            "recovery_factor": metrics.recovery_factor(gross_return - 1, max_dd),
            "max_drawdown_percent": round(max_dd, 6) * 100,
            "gross_return_percent": gross_return * 100,
            "net_return_percent": (gross_return - 1) * 100,
            "profit_factor": metrics.profit_factor(self.trades),
            "win_rate_percent": metrics.win_rate(self.trades) * 100,
            "avg_win_loss_ratio": metrics.avg_win_loss_ratio(self.trades),
            "max_consecutive_losses": float(metrics.max_consecutive_losses(self.trades)),
            "avg_holding_period_min": metrics.avg_holding_period_minutes(self.trades),
            "closed_trades": float(len(self.trades)),
            "winner_trades": float(
                sum(1 for t in self.trades if t.result is not None and t.result.value == "winner")
            ),
            "loser_trades": float(
                sum(1 for t in self.trades if t.result is not None and t.result.value == "loser")
            ),
        }

        for symbol in self.benchmark_symbols:
            sym_returns = self.merged[f"{symbol}__simple_returns"].dropna()
            sym_cum = self.merged[f"{symbol}__cumulative_returns"]
            sym_gross = float(sym_cum.values[-1])
            sym_max_dd = float(self.merged[f"{symbol}__drawdown"].min())
            sym_ann = metrics.cagr(sym_gross, n_days)

            self.summary[symbol] = {
                "sharpe_ratio": metrics.sharpe_ratio(sym_returns, self.risk_free_rate),
                "sortino_ratio": metrics.sortino_ratio(sym_returns, self.risk_free_rate),
                "annualized_volatility_percent": metrics.annualized_volatility(sym_returns) * 100,
                "cagr_percent": sym_ann * 100,
                "calmar_ratio": metrics.calmar_ratio(sym_ann, sym_max_dd),
                "recovery_factor": metrics.recovery_factor(sym_gross - 1, sym_max_dd),
                "max_drawdown_percent": round(sym_max_dd, 6) * 100,
                "gross_return_percent": sym_gross * 100,
                "net_return_percent": (sym_gross - 1) * 100,
            }

        return self.summary

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
    )
    merged.merge_external_snapshots([a.raw_snapshots for a in analyzers])
    merged.trades = [trade for a in analyzers for trade in a.trades]
    merged.skipped_timestamps = _find_skipped_timestamps([a.raw_snapshots for a in analyzers])
    merged.generate_report()
    return merged
