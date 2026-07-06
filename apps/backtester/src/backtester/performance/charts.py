"""Chart generation for backtesting performance reports."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd
import plotly.graph_objects as go  # type: ignore[import-untyped]
from plotly.subplots import make_subplots  # type: ignore[import-untyped]

if TYPE_CHECKING:
    from backtester.strategies import Trade


def make_report_figure(  # noqa: PLR0913
    merged: pd.DataFrame,
    summary: dict,
    trades: list[Trade],
    benchmark_symbols: list[str],
    key: str,
    additional_fields: pd.DataFrame | None = None,
) -> go.Figure:
    """Build and return a Plotly figure for the performance report.

    Layout (top to bottom):
      1. Summary table
      2. Cumulative returns chart (algo + benchmarks)
      3. Drawdown chart (algo + benchmarks)
      4. Trade log table
      5. Optional additional fields table
    """
    fig = make_subplots(
        row_heights=[15, 15, 15, 50, 10],
        rows=5,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.03,
        specs=[
            [{"type": "table"}],
            [{"type": "scatter"}],
            [{"type": "scatter"}],
            [{"type": "table"}],
            [{"type": "table"}],
        ],
    )

    # --- summary table ---
    summary_df = pd.DataFrame(summary).round(4)
    summary_df.insert(0, "Measurement", summary_df.index)
    fig.add_trace(
        go.Table(
            header=dict(values=list(summary_df.columns)),
            cells=dict(values=[summary_df[c].values for c in summary_df.columns]),
        ),
        row=1,
        col=1,
    )

    # --- cumulative returns chart ---
    fig.add_trace(
        go.Scatter(
            name=f"{key} cumulative returns",
            x=merged.index,
            y=merged[f"{key}__cumulative_returns"],
        ),
        row=2,
        col=1,
    )
    for symbol in benchmark_symbols:
        fig.add_trace(
            go.Scatter(
                name=f"{symbol} HODL",
                x=merged.index,
                y=merged[f"{symbol}__cumulative_returns"],
            ),
            row=2,
            col=1,
        )

    # --- drawdown chart ---
    fig.add_trace(
        go.Scatter(
            name=f"{key} drawdown",
            x=merged.index,
            y=merged[f"{key}__drawdown"],
        ),
        row=3,
        col=1,
    )
    for symbol in benchmark_symbols:
        fig.add_trace(
            go.Scatter(
                name=f"{symbol} drawdown",
                x=merged.index,
                y=merged[f"{symbol}__drawdown"],
            ),
            row=3,
            col=1,
        )

    # --- trade log table ---
    trades_df = pd.DataFrame([t.__dict__ for t in trades])
    if not trades_df.empty:
        trades_df["side"] = trades_df["side"].apply(lambda x: x.value)
        trades_df["close_reason"] = trades_df["close_reason"].apply(
            lambda x: x.value if x is not None else ""
        )
        trades_df["result"] = trades_df["result"].apply(
            lambda x: x.value if x is not None else ""
        )
        trades_df["time_open"] = trades_df["time_open"].apply(
            lambda x: x.strftime("%Y-%m-%d %H:%M") if x is not None else ""
        )
        trades_df["time_close"] = trades_df["time_close"].apply(
            lambda x: x.strftime("%Y-%m-%d %H:%M") if x is not None else ""
        )
        if "holding_period" in trades_df.columns:
            trades_df["holding_period"] = trades_df["holding_period"].apply(
                lambda x: f"{x.total_seconds() / 60:.1f}m" if pd.notna(x) and x is not None else ""
            )
        trades_df["pnl"] = (trades_df["pnl"] * 100).round(4)
        trades_df = trades_df.round(4)

    fig.add_trace(
        go.Table(
            header=dict(values=list(trades_df.columns) if not trades_df.empty else []),
            cells=dict(
                values=(
                    [trades_df[c].values for c in trades_df.columns]
                    if not trades_df.empty
                    else []
                )
            ),
        ),
        row=4,
        col=1,
    )

    # --- optional extra table ---
    if additional_fields is not None:
        fig.add_trace(
            go.Table(
                header=dict(values=list(additional_fields.columns)),
                cells=dict(
                    values=[additional_fields[c].values for c in additional_fields.columns]
                ),
            ),
            row=5,
            col=1,
        )

    fig.update_layout(autosize=True, height=2000)
    return fig
