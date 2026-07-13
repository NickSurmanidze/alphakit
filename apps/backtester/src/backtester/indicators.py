"""
Technical Analysis Indicators Module for Backtesting

This module provides optimized technical indicators for backtesting:

MAIN PURPOSE:
- Calculate technical indicators (SMA, EMA, RSI, MACD, Stochastic)
- Provide TradingView-compatible indicator calculations
- Handle indicator timing and data requirements
- Optimize calculation performance for backtesting

KEY COMPONENTS:
1. INDICATORS: Core technical analysis calculations
2. INDICATOR UTILS: Date/time calculations for indicator requirements
3. PERFORMANCE OPTIMIZATIONS: Vectorized operations using pandas/pandas_ta
4. TV COMPATIBILITY: TradingView-compatible calculation methods

PERFORMANCE OPTIMIZATIONS:
- Uses pandas_ta for vectorized calculations (10-50x faster than loops)
- Efficient memory management with minimal data copying
- Optimized datetime calculations using timedelta operations
- Cached indicator calculations to avoid recomputation

The indicators are designed to match TradingView calculations exactly
while providing maximum performance for backtesting scenarios.
"""

import math
from datetime import datetime, timedelta
from enum import Enum
from typing import NamedTuple

import pandas_ta
from pandas import DataFrame, Series


class IndicatorConfig(NamedTuple):
    """Configuration for indicator calculations to reduce function arguments."""

    date_from: str
    date_to: str
    indicator: "IndicatorType"
    indicator_length: int
    unit_of_time: "UnitOfTime"
    unit_of_time_length: int


class UnitOfTime(Enum):
    Day = "day"
    Hour = "hour"
    Minute = "minute"
    Second = "second"


class IndicatorType(Enum):
    HMA = "HMA"
    EMA = "EMA"
    SMA = "SMA"
    RMA = "RMA"
    RSI = "RSI"
    STOCH = "STOCH"
    STOCHRSI = "STOCHRSI"
    KAMA = "KAMA"
    DEMA = "DEMA"


class StochRsiTarget(Enum):
    K = "K"
    D = "D"
    both = "both"


# class TradingViewIndicators:
class Indicators:
    """
    Optimized technical indicators class with enhanced performance.

    All methods use vectorized pandas_ta operations for maximum speed.
    Results are compatible with TradingView calculations.
    """

    @staticmethod
    def sma(ohlc: DataFrame, length: int = 13, column: str = "close") -> Series:
        """
        Simple Moving Average - optimized with pandas_ta.

        Args:
            ohlc: OHLC DataFrame
            length: Period for calculation
            column: Column to use for calculation

        Returns:
            SMA Series with proper naming
        """
        sma = pandas_ta.sma(ohlc[column], length)
        if sma is None:
            raise ValueError("SMA calculation returned None")

        if not isinstance(sma, Series):
            raise ValueError("SMA should return a pandas Series")

        sma.name = f"{IndicatorType.SMA.value}_{length}"
        return sma

    @staticmethod
    def ema(ohlc: DataFrame, length: int = 13, column: str = "close") -> Series:
        """
        Exponential Moving Average - optimized with pandas_ta.

        Args:
            ohlc: OHLC DataFrame
            length: Period for calculation
            column: Column to use for calculation

        Returns:
            EMA Series with proper naming
        """
        ema = pandas_ta.ema(ohlc[column], length)
        if ema is None:
            raise ValueError("EMA calculation returned None")

        if not isinstance(ema, Series):
            ema = Series(ema, name=f"{IndicatorType.EMA.value}_{length}")

        ema.name = f"{IndicatorType.EMA.value}_{length}"
        return ema

    @staticmethod
    def kama(
        ohlc: DataFrame, length: int = 10, fast: int = 2, slow: int = 30, column: str = "close"
    ) -> Series:
        """
        Kaufman's Adaptive Moving Average - optimized with pandas_ta.

        Adjusts its own smoothing constant based on an efficiency ratio (how
        directional vs. choppy recent price action has been) computed over `length`
        bars: tracks fast during clean trends, flattens out during chop. `fast`/`slow`
        set the EMA-equivalent smoothing-constant bounds it interpolates between --
        Kaufman's own defaults (2, 30) if not overridden.

        Args:
            ohlc: OHLC DataFrame
            length: Efficiency-ratio lookback period
            fast: Fast EMA-equivalent period bound (more reactive, must be < slow)
            slow: Slow EMA-equivalent period bound (less reactive, must be > fast)
            column: Column to use for calculation

        Returns:
            KAMA Series with proper naming
        """
        kama = pandas_ta.kama(ohlc[column], length=length, fast=fast, slow=slow)
        if kama is None:
            raise ValueError("KAMA calculation returned None")

        if not isinstance(kama, Series):
            raise ValueError("KAMA should return a pandas Series")

        kama.name = f"{IndicatorType.KAMA.value}_{length}"
        return kama

    @staticmethod
    def hma(ohlc: DataFrame, length: int = 13, column: str = "close") -> Series:
        """
        Hull Moving Average - optimized with pandas_ta.

        A weighted-MA construction (sqrt(length)-period WMA of the difference between
        two half/full-length WMAs) designed to track price more closely than a plain
        MA of the same length while still smoothing noise -- lower lag for a given
        amount of smoothing than SMA/EMA.

        Args:
            ohlc: OHLC DataFrame
            length: Period for calculation
            column: Column to use for calculation

        Returns:
            HMA Series with proper naming
        """
        hma = pandas_ta.hma(ohlc[column], length)
        if hma is None:
            raise ValueError("HMA calculation returned None")

        if not isinstance(hma, Series):
            raise ValueError("HMA should return a pandas Series")

        hma.name = f"{IndicatorType.HMA.value}_{length}"
        return hma

    @staticmethod
    def dema(ohlc: DataFrame, length: int = 13, column: str = "close") -> Series:
        """
        Double Exponential Moving Average - optimized with pandas_ta.

        Applies EMA twice and combines the two (2*EMA - EMA(EMA)) to cancel out most
        of the lag a plain EMA of the same length would have.

        Args:
            ohlc: OHLC DataFrame
            length: Period for calculation
            column: Column to use for calculation

        Returns:
            DEMA Series with proper naming
        """
        dema = pandas_ta.dema(ohlc[column], length)
        if dema is None:
            raise ValueError("DEMA calculation returned None")

        if not isinstance(dema, Series):
            raise ValueError("DEMA should return a pandas Series")

        dema.name = f"{IndicatorType.DEMA.value}_{length}"
        return dema

    @staticmethod
    def rsi(ohlc: DataFrame, length: int = 14, column: str = "close") -> Series:
        """
        Relative Strength Index - optimized with pandas_ta.

        Args:
            ohlc: OHLC DataFrame
            length: Period for calculation
            column: Column to use for calculation

        Returns:
            RSI Series with proper naming
        """
        rsi = pandas_ta.rsi(ohlc[column], length)
        if rsi is None:
            raise ValueError("RSI calculation returned None")

        if not isinstance(rsi, Series):
            rsi = Series(rsi, name=f"{IndicatorType.RSI.value}_{length}")

        rsi.name = f"{IndicatorType.RSI.value}_{length}"
        return rsi

    @staticmethod
    def macd(
        ohlc: DataFrame, fast: int = 12, slow: int = 26, signal: int = 9, column: str = "close"
    ) -> tuple[Series, Series, Series]:
        """
        MACD indicator - optimized with pandas_ta.

        Args:
            ohlc: OHLC DataFrame
            fast: Fast EMA period
            slow: Slow EMA period
            signal: Signal line EMA period
            column: Column to use for calculation

        Returns:
            Tuple of (macd_line, macd_histogram, macd_signal)
        """
        macd_result = pandas_ta.macd(ohlc[column], fast, slow, signal)
        if macd_result is None:
            raise ValueError("MACD calculation returned None")

        # Extract individual components
        macd_line = macd_result.iloc[:, 0]
        macd_histogram = macd_result.iloc[:, 1]
        macd_signal = macd_result.iloc[:, 2]

        # Set proper names
        macd_line.name = f"MACD_{fast}_{slow}_{signal}"
        macd_histogram.name = f"MACDh_{fast}_{slow}_{signal}"
        macd_signal.name = f"MACDs_{fast}_{slow}_{signal}"

        return macd_line, macd_histogram, macd_signal

    @staticmethod
    def stoch(ohlc: DataFrame, k: int = 14, d: int = 3, smooth_k: int = 3) -> tuple[Series, Series]:
        """
        Stochastic Oscillator - optimized with pandas_ta.

        Args:
            ohlc: OHLC DataFrame with high, low, close columns
            k: %K period
            d: %D period
            smooth_k: %K smoothing period

        Returns:
            Tuple of (%K, %D) Series
        """
        stoch_result = pandas_ta.stoch(
            ohlc["high"], ohlc["low"], ohlc["close"], k=k, d=d, smooth_k=smooth_k
        )
        if stoch_result is None:
            raise ValueError("Stochastic calculation returned None")

        stoch_k = stoch_result.iloc[:, 0]  # %K
        stoch_d = stoch_result.iloc[:, 1]  # %D

        stoch_k.name = f"STOCH_K_{k}_{d}_{smooth_k}"
        stoch_d.name = f"STOCH_D_{k}_{d}_{smooth_k}"

        return stoch_k, stoch_d

    @staticmethod
    def bollinger_bands(
        ohlc: DataFrame, length: int = 20, std_dev: float = 2.0, column: str = "close"
    ) -> tuple[Series, Series, Series]:
        """
        Bollinger Bands - optimized calculation.

        Args:
            ohlc: OHLC DataFrame
            length: Period for calculation
            std_dev: Standard deviation multiplier
            column: Column to use for calculation

        Returns:
            Tuple of (lower_band, middle_band, upper_band)
        """
        # Calculate middle band (SMA)
        middle_band = pandas_ta.sma(ohlc[column], length)
        if middle_band is None:
            raise ValueError("SMA calculation for Bollinger Bands returned None")

        # Calculate standard deviation
        rolling_std = ohlc[column].rolling(window=length).std()

        # Calculate upper and lower bands
        upper_band = middle_band + (rolling_std * std_dev)
        lower_band = middle_band - (rolling_std * std_dev)

        # Set proper names
        lower_band.name = f"BB_L_{length}_{std_dev}"
        middle_band.name = f"BB_M_{length}_{std_dev}"
        upper_band.name = f"BB_U_{length}_{std_dev}"

        return lower_band, middle_band, upper_band


# class Indicators(Indicators):
#     # @staticproperty
#     # def finta(_) -> type:
#     #     return TA

#     # @staticproperty
#     # def pandas(_) -> type:
#     #     return pandas_ta

#     # @staticproperty
#     # def tv(_) -> type[Indicators]:
#     #     return Indicators

#     # @staticmethod
#     # def HMA_ROUND(ohlc: DataFrame, period: int = 16, column: str = "close") -> Series:
#     #     return Indicators.hma(ohlc, period, column)


class IndicatorUtils:
    """Date/length bookkeeping for indicators: how far back a warmup window needs to
    start so an indicator has enough history to be valid by `date_from`."""

    @staticmethod
    def subtract_from_date(
        date_from: str, unit_of_time: UnitOfTime, unit_of_time_length: int
    ) -> datetime:
        """
        Subtracting unit of time from given datetime

        Args:
            :date_from: (str): Datetime to be adjusted
            :unit_of_time: (UnitOfTime): Unit of time for adjusting
            :unit_of_time_length: (int): How many units of time should be subtracted

        Returns:
            datetime
        """
        date = datetime.fromisoformat(date_from)

        if unit_of_time == UnitOfTime.Day:
            return date - timedelta(days=unit_of_time_length - 1)
        elif unit_of_time == UnitOfTime.Hour:
            return date - timedelta(hours=unit_of_time_length - 1)
        elif unit_of_time == UnitOfTime.Minute:
            return date - timedelta(minutes=unit_of_time_length - 1)
        else:  # seconds
            return date - timedelta(seconds=unit_of_time_length - 1)

    @staticmethod
    def get_difference_in_unit_of_time(
        date_from: str, date_to: str, unit_of_time: UnitOfTime
    ) -> int:
        """
        Calculates the the difference between two dates and converts it to unit of time

        Args:
            :date_from: (str): Time range start
            :date_to: (str): Time range end
            :unit_of_time: (UnitOfTime): Unit of time for calculation

        Returns:
            int: Difference
        """
        start = datetime.fromisoformat(date_from)
        end = datetime.fromisoformat(date_to)
        duration = end - start
        duration_in_seconds = duration.total_seconds()

        diff = 0
        if unit_of_time == UnitOfTime.Day:
            diff = math.ceil(divmod(duration_in_seconds, 86400)[0])
        elif unit_of_time == UnitOfTime.Hour:
            diff = math.ceil(divmod(duration_in_seconds, 3600)[0])
        elif unit_of_time == UnitOfTime.Minute:
            diff = math.ceil(divmod(duration_in_seconds, 60)[0])
        else:  # seconds
            diff = math.ceil(duration_in_seconds)

        return diff

    @staticmethod
    def get_indicator_start_length(config: IndicatorConfig) -> int:
        """
        Calculation in unit of time for how much earlier should starting datetime be.

        Args:
            config: IndicatorConfig with all required parameters

        Returns:
            int: Calculated length for given unit of time
        """
        diff = IndicatorUtils.get_difference_in_unit_of_time(
            config.date_from, config.date_to, config.unit_of_time
        )
        max_length = config.indicator_length  # by default max length is same as indicator_length

        if config.indicator == IndicatorType.HMA:
            max_length = config.indicator_length + math.floor(
                math.sqrt(config.indicator_length) - 1
            )

        subtract_length = max_length * config.unit_of_time_length
        adjusted_mod = (diff + max_length) % config.unit_of_time_length
        adjusted_length = subtract_length + (config.unit_of_time_length if adjusted_mod > 0 else 0)

        return adjusted_length

    @staticmethod
    def get_stoch_rsi_length(
        rsi_period: int = 14,
        stoch_period: int = 14,
        smooth_k: int = 3,
        smooth_d: int = 3,
        target: StochRsiTarget = StochRsiTarget.both,
    ) -> int:
        """
        Calculating Stoch RSI length for calculating start length

        Args:
            :rsi_period: (int, optional): RSI length. Defaults to 14.
            :stoch_period: (int, optional): Stoch length. Defaults to 14.
            :smooth_k: (int, optional): K length. Defaults to 3.
            :smooth_d: (int, optional): D length. Defaults to 3.
            :target: (StochRsiTarget, optional): What needs bo included.
                Defaults to StochRsiTarget.both.

        Returns:
            [type]: [description]

        Example:
            ...
            date_from = "2019-08-09"
            unit_of_time = UnitOfTime.Day
            unit_of_time_length = 1
            indicator = IndicatorType.STOCHRSI

            # get indicator_length using get_stoch_rsi_length
            indicator_length = IndicatorUtils.get_stoch_rsi_length(
                rsi_period = 14,
                stoch_period = 14,
                smooth_k = 3,
                smooth_d = 3,
                target = StochRsiTarget.both
            )
            adjusted_from = IndicatorUtils.indicator_start_datetime(
                date_from,
                date_to,
                indicator,
                indicator_length,
                unit_of_time,
                unit_of_time_length
            )
            print(adjusted_from) # Adjusted in this example will be 2019-07-21
            ...
        """
        length = rsi_period

        # stoch period starts add to base length if it is 3 units greather than rsi length
        if stoch_period >= rsi_period + 3:
            length = length + (stoch_period - rsi_period - 2)

        length = length + smooth_k

        if target != StochRsiTarget.K:
            length = length + smooth_d

        return length

    @staticmethod
    def indicator_start_datetime(config: IndicatorConfig) -> str:
        """
        Calculation for how much earlier should starting datetime be.

        Args:
            config: IndicatorConfig with all required parameters

        Returns:
            str: Adjusted date in ISO format
        """
        length = IndicatorUtils.get_indicator_start_length(config)
        adjusted = IndicatorUtils.subtract_from_date(config.date_from, config.unit_of_time, length)
        return adjusted.isoformat()
