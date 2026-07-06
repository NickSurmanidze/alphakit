"""
Kraken Data Aggregator

Fetches OHLC data from the Kraken public spot REST API.
"""

import time
from datetime import datetime

import pandas as pd
import requests

KRAKEN_SPOT_BASE = "https://api.kraken.com/0/public"
MAX_CANDLES_PER_REQUEST = 720
REQUEST_DELAY = 0.5  # seconds between requests — Kraken rate-limits at ~1 req/s

# Maps (unit_of_time, interval) -> Kraken interval in minutes
_INTERVAL_MINUTES: dict[tuple[str, int], int] = {
    ("minute", 1): 1,
    ("minute", 5): 5,
    ("minute", 15): 15,
    ("minute", 30): 30,
    ("hour", 1): 60,
    ("hour", 4): 240,
    ("hour", 12): 720,
    ("day", 1): 1440,
    ("week", 1): 10080,
}


def aggregate_ohlc_data(
    pair: str,
    unit_of_time: str,
    interval: int,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
) -> pd.DataFrame:
    """
    Fetch OHLC data for a Kraken spot pair.

    Args:
        pair: Kraken pair altname (e.g., 'XBTUSD', 'ETHUSD', 'SOLUSD')
        unit_of_time: 'minute', 'hour', 'day', or 'week'
        interval: Interval size (e.g., 1 for 1h with unit_of_time='hour')
        start_date: Start of the fetch window (defaults to 2017-01-01)
        end_date: End of the fetch window (defaults to now)

    Returns:
        DataFrame indexed by timestamp with columns:
        open, high, low, close, vwap, volume, trade_count
    """
    key = (unit_of_time, interval)
    if key not in _INTERVAL_MINUTES:
        raise ValueError(
            f"Unsupported interval: {interval} {unit_of_time}. "
            f"Valid options: {list(_INTERVAL_MINUTES.keys())}"
        )
    interval_minutes = _INTERVAL_MINUTES[key]

    if end_date is None:
        end_date = datetime.now()
    if start_date is None:
        start_date = datetime(2017, 1, 1)

    since_ts = int(start_date.timestamp())
    end_ts = int(end_date.timestamp())

    all_rows: list[dict] = []
    prev_last = None

    while since_ts < end_ts:
        params: dict = {
            "pair": pair,
            "interval": interval_minutes,
            "since": since_ts,
        }

        try:
            resp = requests.get(f"{KRAKEN_SPOT_BASE}/OHLC", params=params, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise requests.RequestException(
                f"Kraken OHLC request failed for {pair}: {exc}"
            ) from exc

        payload = resp.json()
        if payload.get("error"):
            raise ValueError(f"Kraken API error for {pair}: {payload['error']}")

        result = payload["result"]
        last_ts = int(result["last"])
        pair_key = next(k for k in result if k != "last")
        candles = result[pair_key]

        if not candles:
            break

        for c in candles:
            ts_secs = int(c[0])
            if ts_secs >= end_ts:
                break
            all_rows.append(
                {
                    "timestamp": pd.to_datetime(ts_secs, unit="s"),
                    "open": float(c[1]),
                    "high": float(c[2]),
                    "low": float(c[3]),
                    "close": float(c[4]),
                    "vwap": float(c[5]),
                    "volume": float(c[6]),
                    "trade_count": int(c[7]),
                }
            )

        # Kraken returns `last` = timestamp of the last candle; use it to paginate.
        if last_ts <= since_ts or last_ts == prev_last:
            break
        prev_last = last_ts
        since_ts = last_ts
        time.sleep(REQUEST_DELAY)

    if not all_rows:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "vwap", "volume", "trade_count"])

    df = pd.DataFrame(all_rows)
    df.set_index("timestamp", inplace=True)
    df.sort_index(inplace=True)
    df = df[~df.index.duplicated(keep="first")]
    df = df[df.index <= pd.to_datetime(end_date)]
    return df


def get_available_pairs(quote_currencies: list[str] | None = None) -> pd.DataFrame:
    """
    Return Kraken spot pairs, optionally filtered by quote currency.

    Args:
        quote_currencies: List of quote asset codes to filter by (e.g., ['USD', 'USDT']).
                          Pass None to return all tradeable pairs.

    Returns:
        DataFrame with columns: altname, wsname, base, quote
    """
    try:
        resp = requests.get(f"{KRAKEN_SPOT_BASE}/AssetPairs", timeout=30)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise requests.RequestException(f"Kraken AssetPairs request failed: {exc}") from exc

    payload = resp.json()
    if payload.get("error"):
        raise ValueError(f"Kraken API error: {payload['error']}")

    rows: list[dict] = []
    for internal_name, info in payload["result"].items():
        # Skip dark-pool pairs (suffixed with .d) and leverage-only variants
        if info.get("status") != "online":
            continue
        rows.append(
            {
                "internal_name": internal_name,
                "altname": info.get("altname", ""),
                "wsname": info.get("wsname", ""),
                "base": info.get("base", ""),
                "quote": info.get("quote", ""),
            }
        )

    df = pd.DataFrame(rows)

    if quote_currencies:
        # Kraken quote assets use 4-char codes like ZUSD, ZUSDT → normalise
        normalised_quotes = {_normalise_asset(q) for q in quote_currencies}
        df = df[df["quote"].apply(_normalise_asset).isin(normalised_quotes)]

    return df.reset_index(drop=True)


def aggregate_futures_ohlcv_data(
    symbol: str,
    unit_of_time: str,
    interval: int,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
) -> pd.DataFrame:
    """
    Fetch OHLCV data for a Kraken Futures perpetual symbol.

    Args:
        symbol: Futures symbol (e.g., 'PF_XBTUSD', 'PI_XBTUSD')
        unit_of_time: 'minute', 'hour', 'day', or 'week'
        interval: Interval size (e.g., 1 for 1h with unit_of_time='hour')
        start_date: Start of the fetch window (defaults to 2017-01-01)
        end_date: End of the fetch window (defaults to now)

    Returns:
        DataFrame indexed by timestamp with columns:
        open, high, low, close, volume
    """
    interval_map: dict[tuple[str, int], str] = {
        ("minute", 1): "1m",
        ("minute", 5): "5m",
        ("minute", 15): "15m",
        ("minute", 30): "30m",
        ("hour", 1): "1h",
        ("hour", 4): "4h",
        ("day", 1): "1d",
        ("week", 1): "1w",
    }

    key = (unit_of_time, interval)
    if key not in interval_map:
        raise ValueError(
            f"Unsupported interval: {interval} {unit_of_time}. "
            f"Valid options: {list(interval_map.keys())}"
        )
    interval_str = interval_map[key]

    if end_date is None:
        end_date = datetime.now()
    if start_date is None:
        start_date = datetime(2017, 1, 1)

    since_ts = int(start_date.timestamp())
    end_ts = int(end_date.timestamp())

    all_rows: list[dict] = []

    while since_ts < end_ts:
        try:
            resp = requests.get(
                f"https://futures.kraken.com/api/charts/v1/spot/{symbol}/{interval_str}",
                params={"from": since_ts, "to": end_ts},
                timeout=30,
            )
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise requests.RequestException(
                f"Kraken Futures OHLCV request failed for {symbol}: {exc}"
            ) from exc

        payload = resp.json()
        if payload.get("error"):
            raise ValueError(f"Kraken Futures API error for {symbol}: {payload['error']}")

        candles = payload.get("candles", [])
        if not candles:
            break

        for c in candles:
            ts_ms = int(c["time"])
            ts_secs = ts_ms // 1000
            if ts_secs >= end_ts:
                break
            all_rows.append(
                {
                    "timestamp": pd.to_datetime(ts_ms, unit="ms"),
                    "open": float(c["open"]),
                    "high": float(c["high"]),
                    "low": float(c["low"]),
                    "close": float(c["close"]),
                    "volume": float(c.get("volume", 0)),
                }
            )

        if candles:
            last_ts = int(candles[-1]["time"]) // 1000
            if last_ts >= end_ts:
                break
            since_ts = last_ts
            time.sleep(REQUEST_DELAY)

    if not all_rows:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

    df = pd.DataFrame(all_rows)
    df.set_index("timestamp", inplace=True)
    df.sort_index(inplace=True)
    df = df[~df.index.duplicated(keep="first")]
    df = df[df.index < pd.to_datetime(end_date)]
    return df


def _normalise_asset(asset: str) -> str:
    """Strip leading X/Z that Kraken prepends to some asset codes."""
    if len(asset) == 4 and asset[0] in ("X", "Z"):
        return asset[1:]
    return asset
