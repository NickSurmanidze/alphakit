"""Pure-function tests for databento_aggregator.py's error-recovery, prompt-formatting,
and resampling logic. Network-touching functions (get_client, get_available_range,
estimate_cost, download_ohlcv_1m) are intentionally left untested here -- matches
existing precedent (binance_aggregator.py/kraken_aggregator.py/mongo_timescale_aggregator.py
also have no live-API test coverage), verified manually against the real Databento API
instead (including load_1m_parquet_resampled against the actual downloaded MES file)."""

from datetime import datetime

import pandas as pd
import pytest
from databento.common.error import BentoClientError

from data_aggregator.databento_aggregator import (
    _available_end_from_error,
    _format_confirmation_prompt,
    _with_available_end_clamp,
    load_1m_parquet_resampled,
)


def _bento_error(case: str, available_end: str | None) -> BentoClientError:
    payload = {"available_end": available_end} if available_end else {}
    detail = {"case": case, "message": f"{case} error", "payload": payload, "docs": None}
    return BentoClientError(http_status=422, json_body={"detail": detail})


def test_available_end_from_error_extracts_the_recovery_hint():
    exc = _bento_error("data_end_after_available_end", "2024-01-01T12:00:00Z")
    assert _available_end_from_error(exc) == datetime.fromisoformat("2024-01-01T12:00:00+00:00")


def test_available_end_from_error_matches_any_case_with_the_hint():
    # Observed in practice under a different `case` (subscription/licensing delay) that
    # carries the same payload shape -- matching on the hint itself, not one specific
    # `case` string, is deliberate (see the module docstring on MAX_AVAILABLE_END_ADJUSTMENTS).
    exc = _bento_error("dataset_unavailable_range", "2024-01-01T12:00:00Z")
    assert _available_end_from_error(exc) is not None


def test_available_end_from_error_returns_none_for_other_exceptions():
    assert _available_end_from_error(ValueError("not a bento error")) is None


def test_available_end_from_error_returns_none_without_a_hint():
    exc = _bento_error("some_other_case", None)
    assert _available_end_from_error(exc) is None


def test_with_available_end_clamp_returns_immediately_on_success():
    calls = []

    def request_fn(end):
        calls.append(end)
        return "ok"

    start = datetime(2024, 1, 1)
    end = datetime(2024, 1, 2)
    result, effective_end = _with_available_end_clamp(request_fn, start=start, end=end)

    assert result == "ok"
    assert effective_end == end
    assert calls == [end]


def test_with_available_end_clamp_retries_with_the_clamped_end():
    start = datetime(2024, 1, 1)
    end = datetime(2024, 1, 10)
    clamped = datetime(2024, 1, 5)
    calls = []

    def request_fn(end_):
        calls.append(end_)
        if end_ == end:
            raise _bento_error("data_end_after_available_end", clamped.isoformat())
        return "ok"

    result, effective_end = _with_available_end_clamp(request_fn, start=start, end=end)

    assert result == "ok"
    assert effective_end == clamped
    assert calls == [end, clamped]


def test_with_available_end_clamp_raises_when_clamped_end_is_before_start():
    # No data in range at all yet -- a real error, not one to retry past.
    start = datetime(2024, 1, 1)
    end = datetime(2024, 1, 10)

    def request_fn(end_):
        raise _bento_error("dataset_unavailable_range", datetime(2023, 12, 31).isoformat())

    with pytest.raises(BentoClientError):
        _with_available_end_clamp(request_fn, start=start, end=end)


def test_with_available_end_clamp_converges_across_multiple_independent_boundaries():
    # Ingestion lag and the account's licensing delay are two independent boundaries --
    # clamping past the first can still land past the second.
    start = datetime(2024, 1, 1)
    end = datetime(2024, 1, 10)
    ingestion_boundary = datetime(2024, 1, 8)
    license_boundary = datetime(2024, 1, 3)
    calls = []

    def request_fn(end_):
        calls.append(end_)
        if end_ > ingestion_boundary:
            raise _bento_error("data_end_after_available_end", ingestion_boundary.isoformat())
        if end_ > license_boundary:
            raise _bento_error("dataset_unavailable_range", license_boundary.isoformat())
        return "ok"

    result, effective_end = _with_available_end_clamp(request_fn, start=start, end=end)

    assert result == "ok"
    assert effective_end == license_boundary
    assert calls == [end, ingestion_boundary, license_boundary]


def test_format_confirmation_prompt_includes_symbol_range_and_cost():
    prompt = _format_confirmation_prompt(
        symbol="MES.c.0", start=datetime(2010, 6, 6), end=datetime(2026, 7, 12), cost_usd=1234.5
    )

    assert "MES.c.0" in prompt
    assert "2010-06-06" in prompt
    assert "2026-07-12" in prompt
    assert "$1,234.50" in prompt


def test_load_1m_parquet_resampled_aggregates_and_drops_untraded_buckets(tmp_path):
    # Two full hours of 1-minute bars (00:00-01:59), then a gap simulating the
    # future's overnight/weekend closure, then one more minute at 05:00 that starts a
    # third, mostly-empty hourly bucket -- resampling a continuous calendar grid over
    # this would otherwise produce NaN/zero-volume rows for every untraded hour.
    timestamps = [
        "2024-01-01 00:00",
        "2024-01-01 00:30",
        "2024-01-01 01:00",
        "2024-01-01 01:59",
        "2024-01-01 05:00",
    ]
    index = pd.to_datetime(timestamps, utc=True)
    df = pd.DataFrame(
        {
            "open": [100.0, 101.0, 102.0, 103.0, 200.0],
            "high": [105.0, 106.0, 107.0, 108.0, 205.0],
            "low": [95.0, 96.0, 97.0, 98.0, 195.0],
            "close": [101.0, 102.0, 103.0, 104.0, 201.0],
            "volume": [10, 20, 30, 40, 50],
        },
        index=index,
    )
    path = tmp_path / "sample_1m.parquet"
    df.to_parquet(path)

    result = load_1m_parquet_resampled(path, interval=1, unit_of_time="hour")

    expected_columns = ["time_open", "time_close", "open", "high", "low", "close", "volume"]
    assert list(result.columns) == expected_columns
    # 2 real hourly buckets (00:00, 01:00) + the 05:00 bucket -- the untraded 02:00-04:00
    # buckets are gone, not present as NaN rows.
    assert len(result) == 3
    assert pd.DatetimeIndex(result.index).tz is None

    first = result.iloc[0]
    assert first["open"] == 100.0  # first 1m bar's open
    assert first["high"] == 106.0  # max high across the 00:00/00:30 bars
    assert first["low"] == 95.0  # min low across the 00:00/00:30 bars
    assert first["close"] == 102.0  # last 1m bar's close (00:30)
    assert first["volume"] == 30  # 10 + 20
    assert first["time_open"] == pd.Timestamp("2024-01-01 00:00:00")
    assert first["time_close"] == pd.Timestamp("2024-01-01 00:59:59.999")
    assert result.index[0] == first["time_close"]

    last = result.iloc[-1]
    assert last["time_open"] == pd.Timestamp("2024-01-01 05:00:00")
    assert last["volume"] == 50


def test_load_1m_parquet_resampled_rejects_unknown_unit_of_time(tmp_path):
    df = pd.DataFrame(
        {"open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0], "volume": [1]},
        index=pd.to_datetime(["2024-01-01 00:00"], utc=True),
    )
    path = tmp_path / "sample_1m.parquet"
    df.to_parquet(path)

    with pytest.raises(ValueError, match="Unknown unit_of_time"):
        load_1m_parquet_resampled(path, interval=1, unit_of_time="fortnight")
