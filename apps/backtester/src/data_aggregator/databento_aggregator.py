"""
Databento Data Aggregator

Fetches 1-minute OHLCV bars for a CME Globex (GLBX.MDP3) continuous future from
Databento's Historical API and saves them to a local Parquet file.

Unlike the free aggregators alongside this module (binance_aggregator.py,
kraken_aggregator.py), Databento bills per query. estimate_cost() below hits the same
metadata.get_cost endpoint Databento's own billing is based on, and
confirm_and_download() always prints that estimate and requires interactive
confirmation before spending it.

Run directly for the default use case (MES, full available history):
    DATABENTO_API_KEY=... python -m data_aggregator.databento_aggregator
"""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import TypeVar

import databento as db
import pandas as pd
from databento.common.error import BentoClientError

# This module only ever targets one dataset/schema/symbology combination (CME Globex
# continuous futures at 1-minute resolution) -- hardcoded rather than threaded through
# every function as override parameters nobody needs yet.
DATASET = "GLBX.MDP3"
SCHEMA = "ohlcv-1m"
STYPE_IN = "continuous"
DEFAULT_SYMBOL = "MES.c.0"  # Micro E-mini S&P 500, front-month continuous contract.

# Matches this repo's existing convention: apps/backtester/.gitignore ignores /datasets,
# so downloaded market data never gets committed.
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parents[2] / "datasets" / "databento"

# Observed in practice against the real API: Databento rejects (400-series) a request
# whose `end` is past what's actually available, under at least two different error
# `case` values -- plain ingestion lag, and a subscription/license tier that only allows
# delayed CME access (tighter than the dataset's own advertised range from
# get_dataset_range). Both carry the same `detail.payload.available_end` recovery hint.
# Bounded rather than a single retry since those two boundaries are independent and
# clamping past one can still land past the other; each iteration can only shrink `end`,
# so this converges quickly regardless of how many such boundaries exist.
MAX_AVAILABLE_END_ADJUSTMENTS = 5

T = TypeVar("T")


def get_client() -> db.Historical:
    """Lazily builds the Databento client from DATABENTO_API_KEY -- no default, unlike
    the free-data aggregators alongside this module, since there's no anonymous access
    to a bill-metered API."""
    api_key = os.environ.get("DATABENTO_API_KEY")
    if not api_key:
        raise RuntimeError(
            "DATABENTO_API_KEY is not set. Export it before running this script -- the "
            "same key already configured for apps/trading-system-backend/.env works here too."
        )
    return db.Historical(api_key)


def get_available_range(client: db.Historical) -> tuple[datetime, datetime]:
    """The dataset's overall available range under the caller's own entitlements. NOT
    necessarily a safe upper bound to request as-is: Databento's ingestion has real lag,
    and a lower subscription tier can impose an *additional* licensing delay tighter
    than this -- _with_available_end_clamp is what actually recovers from that at
    request time."""
    r = client.metadata.get_dataset_range(DATASET)
    return pd.Timestamp(r["start"]).to_pydatetime(), pd.Timestamp(r["end"]).to_pydatetime()


def _available_end_from_error(exc: Exception) -> datetime | None:
    """Pure so both the cost-check and the download can share one clamp-and-retry loop,
    and so it's unit-testable without a live account."""
    if not isinstance(exc, BentoClientError):
        return None
    payload = (exc.json_body or {}).get("detail", {}).get("payload", {})
    available_end = payload.get("available_end")
    return pd.Timestamp(available_end).to_pydatetime() if available_end else None


def _with_available_end_clamp(
    request_fn: Callable[[datetime], T], *, start: datetime, end: datetime
) -> tuple[T, datetime]:
    """Calls `request_fn(end)`, clamping `end` down via _available_end_from_error and
    retrying whenever the account can't actually serve up to `end` yet, converging
    within MAX_AVAILABLE_END_ADJUSTMENTS attempts. A clamped end at or before `start`
    means there's genuinely nothing in range yet -- a real error to raise, not one to
    retry past. Returns (result, the end actually used)."""
    current_end = end
    for _ in range(MAX_AVAILABLE_END_ADJUSTMENTS):
        try:
            return request_fn(current_end), current_end
        except BentoClientError as exc:
            clamped_end = _available_end_from_error(exc)
            if not clamped_end or clamped_end >= current_end or clamped_end <= start:
                raise
            current_end = clamped_end
    raise RuntimeError(
        f"Could not converge on an available `end` after {MAX_AVAILABLE_END_ADJUSTMENTS} tries"
    )


def estimate_cost(
    client: db.Historical, *, symbol: str, start: datetime, end: datetime
) -> tuple[float, datetime]:
    """Cost in USD for [start, end) at SCHEMA, from the same metadata.get_cost endpoint
    Databento's own billing is based on. Returns (cost, the end actually used) -- the
    latter may be earlier than requested if it had to be clamped."""

    def _request(end_: datetime) -> float:
        return client.metadata.get_cost(
            dataset=DATASET, symbols=symbol, schema=SCHEMA, stype_in=STYPE_IN, start=start, end=end_
        )

    return _with_available_end_clamp(_request, start=start, end=end)


def download_ohlcv_1m(
    client: db.Historical, *, symbol: str, start: datetime, end: datetime, output_path: Path
) -> Path:
    """Downloads once -- the only step that spends money -- to a raw .dbn.zst file
    alongside `output_path`, then converts to Parquet from that local file. Keeping the
    raw download means a bug in the Parquet conversion never requires re-paying for the
    same data. Returns the Parquet path."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path = output_path.with_suffix(".dbn.zst")

    def _request(end_: datetime) -> None:
        client.timeseries.get_range(
            dataset=DATASET,
            symbols=symbol,
            schema=SCHEMA,
            stype_in=STYPE_IN,
            start=start,
            end=end_,
            path=raw_path,
        )

    _with_available_end_clamp(_request, start=start, end=end)

    store = db.DBNStore.from_file(raw_path)
    store.to_parquet(output_path)
    return output_path


def _format_confirmation_prompt(
    *, symbol: str, start: datetime, end: datetime, cost_usd: float
) -> str:
    """Pure so it's unit-testable without stdin/network."""
    return (
        f"About to download {symbol} 1-minute OHLCV from {start.date()} to {end.date()} "
        f"(dataset {DATASET}).\nEstimated cost: ${cost_usd:,.2f} USD.\nProceed? [y/N] "
    )


def confirm_and_download(
    client: db.Historical,
    *,
    symbol: str = DEFAULT_SYMBOL,
    date_range: tuple[datetime, datetime] | None = None,
    output_path: Path | None = None,
    auto_confirm: bool = False,
) -> Path | None:
    """The full flow this module exists for: resolve the range, quote the cost, require
    interactive confirmation (unless auto_confirm=True), then download. Returns the
    Parquet path if it downloaded, None if the user declined."""
    start, end = date_range if date_range else (None, None)
    if start is None or end is None:
        available_start, available_end = get_available_range(client)
        start = start or available_start
        end = end or available_end

    cost, effective_end = estimate_cost(client, symbol=symbol, start=start, end=end)
    if effective_end != end:
        print(
            f"Note: requested end clamped to {effective_end.isoformat()} "
            "(Databento's actual available range)."
        )

    prompt = _format_confirmation_prompt(
        symbol=symbol, start=start, end=effective_end, cost_usd=cost
    )
    if auto_confirm:
        print(prompt)
    else:
        answer = input(prompt).strip().lower()
        if answer != "y":
            print("Aborted -- no data downloaded, no cost incurred.")
            return None

    resolved_output_path = output_path or (
        DEFAULT_OUTPUT_DIR / f"{symbol.replace('.', '_')}_1m.parquet"
    )
    saved_path = download_ohlcv_1m(
        client, symbol=symbol, start=start, end=effective_end, output_path=resolved_output_path
    )
    print(f"Saved {saved_path}")
    return saved_path


_RESAMPLE_OFFSET_ALIAS = {"minute": "min", "hour": "h", "day": "D"}


def load_1m_parquet_resampled(path: Path, *, interval: int, unit_of_time: str) -> pd.DataFrame:
    """Loads a 1-minute OHLCV Parquet file saved by download_ohlcv_1m() and resamples it
    to the requested bucket size, in the exact shape Market.add_market() expects:
    columns time_open/time_close/open/high/low/close/volume, indexed by time_close
    (named "ts", tz-naive) -- same convention as MarketDataFromCSV.fetch_ohlc, just
    sourced from a Databento Parquet file instead of a CSV.

    Buckets with no trades (nights/weekends/holidays the future is closed) are dropped
    rather than left as NaN/zero-volume rows, which a plain pandas resample() over a
    continuous calendar grid would otherwise produce and which would corrupt moving-
    average indicators computed over this data -- MarketDataFromCSV's crypto-only CSVs
    never hit this case since spot crypto trades 24/7, but a real futures calendar does.
    """
    if unit_of_time not in _RESAMPLE_OFFSET_ALIAS:
        valid = list(_RESAMPLE_OFFSET_ALIAS)
        raise ValueError(f"Unknown unit_of_time: {unit_of_time}. Must be one of {valid}")
    offset = f"{interval}{_RESAMPLE_OFFSET_ALIAS[unit_of_time]}"

    df = pd.read_parquet(path, columns=["open", "high", "low", "close", "volume"])
    df.index = pd.DatetimeIndex(df.index).tz_localize(None)

    resampled = df.resample(offset).agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    )
    resampled = resampled.dropna(subset=["open"])

    resampled["time_open"] = resampled.index.to_series()
    resampled["time_close"] = resampled["time_open"] + pd.Timedelta(offset) - pd.Timedelta("1ms")
    resampled.index = resampled["time_close"]
    resampled.index.name = "ts"

    return resampled[["time_open", "time_close", "open", "high", "low", "close", "volume"]]


if __name__ == "__main__":
    confirm_and_download(get_client())
