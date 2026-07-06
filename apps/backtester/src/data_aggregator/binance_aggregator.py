"""
Binance Data Aggregator

This module provides functionality to aggregate OHLC data from Binance's public API.
"""

import time
from datetime import datetime

import pandas as pd
import requests


def aggregate_ohlc_data(
    symbol: str,
    unit_of_time: str,
    interval: int,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
) -> pd.DataFrame:
    """
    Aggregate OHLC data for a symbol from Binance using their public API.

    Args:
        symbol: Trading symbol (e.g., 'BTCUSDT', 'ETHUSDT')
        unit_of_time: Time unit - 'minute', 'hour', or 'day'
        interval: Interval value (e.g., 1, 5, 15, 30 for minutes;
                 1, 2, 4, 6, 8, 12 for hours; 1, 3 for days)
        start_date: Optional start date for data collection
        end_date: Optional end date for data collection

    Returns:
        pandas.DataFrame: OHLC data with columns ['timestamp', 'open', 'high',
                         'low', 'close', 'volume']

    Raises:
        ValueError: If invalid parameters are provided
        requests.RequestException: If API request fails
    """
    # Validate and construct interval string
    interval_str = _construct_interval_string(unit_of_time, interval)

    # Set default date range if not provided
    if end_date is None:
        end_date = datetime.now()
    if start_date is None:
        # Find the earliest available date for this symbol
        start_date = find_earliest_available_date(symbol)

    # Convert to milliseconds for Binance API
    start_ts = int(start_date.timestamp() * 1000)
    end_ts = int(end_date.timestamp() * 1000)

    # Binance API base URL
    base_url = "https://api.binance.com/api/v3/klines"

    # API limits: 1000 klines per request, 1200 requests per minute
    limit_per_request = 1000
    all_data = []

    current_start = start_ts

    while current_start < end_ts:
        # Calculate interval duration in milliseconds
        interval_ms = _get_interval_milliseconds(interval_str)

        # Calculate end time for this batch (don't exceed the requested end time)
        current_end = min(current_start + (limit_per_request * interval_ms), end_ts)

        # Prepare API parameters
        params = {
            "symbol": symbol.upper(),
            "interval": interval_str,
            "startTime": str(current_start),
            "endTime": str(current_end),
            "limit": str(limit_per_request),
        }

        try:
            # Make API request
            response = requests.get(base_url, params=params, timeout=30)
            response.raise_for_status()

            data = response.json()

            if not data:
                break

            # Process the data
            for kline in data:
                all_data.append(
                    {
                        "timestamp": pd.to_datetime(kline[0], unit="ms"),
                        "open": float(kline[1]),
                        "high": float(kline[2]),
                        "low": float(kline[3]),
                        "close": float(kline[4]),
                        "volume": float(kline[5]),
                        "close_time": pd.to_datetime(kline[6], unit="ms"),
                        "quote_volume": float(kline[7]),
                        "trades_count": int(kline[8]),
                        "taker_buy_base_volume": float(kline[9]),
                        "taker_buy_quote_volume": float(kline[10]),
                    }
                )

            # Update start time for next iteration
            if data:
                # Use the close time of the last kline + 1ms to avoid overlap
                current_start = data[-1][6] + 1
            else:
                break

            # Rate limiting: sleep to avoid hitting API limits
            time.sleep(0.1)  # 100ms delay between requests

        except requests.RequestException as e:
            raise requests.RequestException(f"Failed to fetch data from Binance API: {e}") from e

    if not all_data:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

    # Create DataFrame
    df = pd.DataFrame(all_data)

    # Set timestamp as index
    df.set_index("timestamp", inplace=True)

    # Sort by timestamp to ensure chronological order
    df.sort_index(inplace=True)

    # Remove duplicates if any
    df = df[~df.index.duplicated(keep="first")]

    # Filter to exact date range requested
    if start_date is not None and end_date is not None:
        df = df[(df.index >= start_date) & (df.index <= end_date)]

    return df


def _construct_interval_string(unit_of_time: str, interval: int) -> str:
    """
    Construct Binance API interval string from unit and interval.

    Args:
        unit_of_time: 'minute', 'hour', or 'day'
        interval: Integer interval value

    Returns:
        str: Binance API interval string (e.g., '1m', '4h', '1d')

    Raises:
        ValueError: If invalid parameters are provided
    """
    # Map unit names to Binance API suffixes
    unit_mapping = {"minute": "m", "hour": "h", "day": "d"}

    if unit_of_time not in unit_mapping:
        raise ValueError(
            f"Invalid unit_of_time: {unit_of_time}. Must be one of {list(unit_mapping.keys())}"
        )

    # Validate interval values based on Binance API constraints
    valid_intervals = {"minute": [1, 3, 5, 15, 30], "hour": [1, 2, 4, 6, 8, 12], "day": [1, 3]}

    if interval not in valid_intervals[unit_of_time]:
        raise ValueError(
            f"Invalid interval {interval} for {unit_of_time}. "
            f"Valid intervals: {valid_intervals[unit_of_time]}"
        )

    return f"{interval}{unit_mapping[unit_of_time]}"


def _get_interval_milliseconds(interval_str: str) -> int:
    """
    Get the duration of an interval in milliseconds.

    Args:
        interval_str: Binance API interval string (e.g., '1m', '4h', '1d')

    Returns:
        int: Duration in milliseconds
    """
    # Extract number and unit
    unit = interval_str[-1]
    value = int(interval_str[:-1])

    # Convert to milliseconds
    if unit == "m":  # minutes
        return value * 60 * 1000
    elif unit == "h":  # hours
        return value * 60 * 60 * 1000
    elif unit == "d":  # days
        return value * 24 * 60 * 60 * 1000
    else:
        raise ValueError(f"Unknown interval unit: {unit}")


def find_earliest_available_date(symbol: str) -> datetime:
    """
    Find the earliest available date for a trading symbol on Binance.

    This function uses a two-step approach:
    1. First finds the earliest year with data using yearly intervals
    2. Then narrows down to find the exact earliest date using daily data

    Args:
        symbol: Trading symbol (e.g., 'BTCUSDT', 'ETHUSDT')

    Returns:
        datetime: The earliest available date for the symbol

    Raises:
        requests.RequestException: If API request fails
        ValueError: If no data is found for the symbol
    """
    symbol = symbol.upper()

    # Step 1: Find the earliest year with data
    earliest_year = _find_earliest_year_with_data(symbol)

    # Step 2: Find the exact earliest date within that year
    earliest_date = _find_earliest_date_in_year(symbol, earliest_year)

    return earliest_date


def _find_earliest_year_with_data(symbol: str) -> int:
    """Find the earliest year that has data for the given symbol."""
    base_url = "https://api.binance.com/api/v3/klines"
    current_year = datetime.now().year
    earliest_year = None

    # Look backwards from current year to find first year with data
    for year in range(current_year, 2016, -1):  # Binance started around 2017
        year_start = datetime(year, 1, 1)
        year_end = datetime(year, 12, 31)

        params = {
            "symbol": symbol,
            "interval": "1d",
            "startTime": str(int(year_start.timestamp() * 1000)),
            "endTime": str(int(year_end.timestamp() * 1000)),
            "limit": "1",
        }

        try:
            response = requests.get(base_url, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()

            if data:
                earliest_year = year
                time.sleep(0.1)  # Rate limiting
            else:
                break  # No data for this year, stop searching backwards

        except requests.RequestException as e:
            raise requests.RequestException(f"Failed to search for earliest year: {e}") from e

    if earliest_year is None:
        raise ValueError(f"No historical data found for symbol {symbol}")

    return earliest_year


def _find_earliest_date_in_year(symbol: str, year: int) -> datetime:
    """Find the exact earliest date with data in the given year."""
    base_url = "https://api.binance.com/api/v3/klines"

    # Try to get the first available data point in the year
    year_start = datetime(year, 1, 1)
    year_end = datetime(year, 12, 31)

    params = {
        "symbol": symbol,
        "interval": "1d",
        "startTime": str(int(year_start.timestamp() * 1000)),
        "endTime": str(int(year_end.timestamp() * 1000)),
        "limit": "1",
    }

    try:
        response = requests.get(base_url, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()

        if data:
            timestamp_ms = data[0][0]
            return datetime.fromtimestamp(timestamp_ms / 1000)
        else:
            raise ValueError(f"No data found for symbol {symbol} in year {year}")

    except requests.RequestException as e:
        raise requests.RequestException(f"Failed to get earliest date in year: {e}") from e


def get_available_symbols() -> list[str]:
    """
    Get list of available trading symbols from Binance.

    Returns:
        list[str]: List of available trading symbols

    Raises:
        requests.RequestException: If API request fails
    """
    url = "https://api.binance.com/api/v3/exchangeInfo"

    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()

        data = response.json()
        symbols = [
            symbol_info["symbol"]
            for symbol_info in data["symbols"]
            if symbol_info["status"] == "TRADING"
        ]

        return sorted(symbols)

    except requests.RequestException as e:
        raise requests.RequestException(f"Failed to fetch symbols from Binance API: {e}") from e


# Example usage
if __name__ == "__main__":
    # Example 1: Get 1-day BTCUSDT data from earliest available date
    print("Finding earliest available date for BTCUSDT...")
    earliest_date = find_earliest_available_date("BTCUSDT")
    print(f"Earliest available date: {earliest_date}")

    # Example 2: Get data from earliest date to now
    df = aggregate_ohlc_data(
        symbol="BTCUSDT",
        unit_of_time="day",
        interval=1,
        # start_date will be automatically set to earliest available
        end_date=datetime.now(),
    )

    print(f"Retrieved {len(df)} data points from {df.index.min()} to {df.index.max()}")
    print(df.head())
    print(f"\nColumns: {list(df.columns)}")
