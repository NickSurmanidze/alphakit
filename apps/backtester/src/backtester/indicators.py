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

import numpy as np
import pandas as pd
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
    def macd_kama(  # noqa: PLR0913
        ohlc: DataFrame,
        fast: int = 12,
        slow: int = 26,
        signal: int = 9,
        kama_fast: int = 2,
        kama_slow: int = 30,
        column: str = "close",
    ) -> tuple[Series, Series, Series]:
        """
        KAMA-based MACD -- same construction as Indicators.macd (fast MA minus
        slow MA, plus a signal-line MA of that difference) but using Kaufman's
        Adaptive Moving Average in place of the standard EMA at every stage,
        including the signal line. KAMA tracks fast during clean trends and
        flattens out during chop (see Indicators.kama), so this variant should
        react faster in trending stretches and whipsaw less during chop than
        the EMA version -- at the cost of being more path-dependent (KAMA is
        recursive/stateful, unlike EMA's closed-form weighting).

        Args:
            ohlc: OHLC DataFrame.
            fast: Fast KAMA length (on price).
            slow: Slow KAMA length (on price).
            signal: KAMA length for the signal line (applied to the MACD line itself).
            kama_fast: KAMA's own fast EMA-equivalent smoothing-constant bound, shared by all three KAMA calls.
            kama_slow: KAMA's own slow EMA-equivalent smoothing-constant bound, shared by all three KAMA calls.
            column: Column to use for calculation.

        Returns:
            Tuple of (macd_line, macd_histogram, macd_signal), same shape as Indicators.macd.
        """
        fast_kama = pandas_ta.kama(ohlc[column], length=fast, fast=kama_fast, slow=kama_slow)
        slow_kama = pandas_ta.kama(ohlc[column], length=slow, fast=kama_fast, slow=kama_slow)
        if fast_kama is None or slow_kama is None:
            raise ValueError("KAMA calculation for MACD returned None")

        macd_line = fast_kama - slow_kama
        # pandas_ta.kama returns all-NaN when its input starts with any NaN
        # (its recursive warmup seeds off index 0) -- macd_line always does,
        # for as many bars as the slower of fast/slow's own warmup takes, so
        # drop those leading NaNs before computing the signal KAMA and
        # reindex the result back onto macd_line's full index afterwards.
        macd_signal = pandas_ta.kama(macd_line.dropna(), length=signal, fast=kama_fast, slow=kama_slow)
        if macd_signal is None:
            raise ValueError("KAMA calculation for MACD signal line returned None")
        macd_signal = macd_signal.reindex(macd_line.index)
        macd_histogram = macd_line - macd_signal

        macd_line.name = f"MACD_KAMA_{fast}_{slow}_{signal}"
        macd_histogram.name = f"MACDh_KAMA_{fast}_{slow}_{signal}"
        macd_signal.name = f"MACDs_KAMA_{fast}_{slow}_{signal}"

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

    @staticmethod
    def adx(ohlc: DataFrame, length: int = 14) -> Series:
        """
        Average Directional Index - trend *strength* (0-100), not direction --
        optimized with pandas_ta. Commonly used as a chop filter: conventionally,
        below ~20-25 is non-trending/choppy, above is trending. Doesn't say which
        way price is trending (see DMP/DMN in pandas_ta's own output for that), just
        how strongly.

        Args:
            ohlc: OHLC DataFrame with high, low, close columns
            length: ADX/DI smoothing period

        Returns:
            ADX Series
        """
        adx_result = pandas_ta.adx(ohlc["high"], ohlc["low"], ohlc["close"], length=length)
        if adx_result is None:
            raise ValueError("ADX calculation returned None")

        adx = adx_result[f"ADX_{length}"]
        adx.name = f"ADX_{length}"
        return adx

    @staticmethod
    def vwap_session(ohlc: DataFrame) -> tuple[Series, Series]:
        """
        Session VWAP and its volume-weighted standard deviation -- the standard
        "VWAP with bands" construction: cumulative volume-weighted mean/variance of
        each bar's typical price ((high+low+close)/3), reset to zero at the first
        bar of each session so the bands start at the VWAP itself and widen through
        the session as more volume accrues (both are exactly 0 on a session's first
        bar -- a single data point has no variance -- so callers should treat an
        early-session std of 0 or near-0 as "not enough data yet", not "price is
        exactly at VWAP").

        Session boundary is UTC midnight (`ohlc.index.normalize()`), not CME's real
        ~17:00 Chicago-time Globex trading-day boundary -- this repo has no session-
        calendar infrastructure for that yet, so this is a known simplification, not
        an attempt at the institutionally exact session.

        Args:
            ohlc: OHLC DataFrame with high, low, close, volume columns, indexed by
                  timestamp (one row per bar, ascending).

        Returns:
            Tuple of (vwap, std) Series, same index as `ohlc`.
        """
        typical_price = (ohlc["high"] + ohlc["low"] + ohlc["close"]) / 3
        session_key = pd.DatetimeIndex(ohlc.index).normalize()

        pv = typical_price * ohlc["volume"]
        pv2 = typical_price.pow(2) * ohlc["volume"]

        cum_vol = ohlc["volume"].groupby(session_key).cumsum()
        cum_pv = pv.groupby(session_key).cumsum()
        cum_pv2 = pv2.groupby(session_key).cumsum()

        vwap = cum_pv / cum_vol
        # Clip before sqrt: floating-point rounding can push a true-zero variance
        # (e.g. a session's first bar, mathematically exactly 0) very slightly
        # negative, which would otherwise produce a NaN.
        variance = (cum_pv2 / cum_vol - vwap.pow(2)).clip(lower=0)
        std = variance.pow(0.5)

        vwap.name = "VWAP_SESSION"
        std.name = "VWAP_SESSION_STD"
        return vwap, std

    @staticmethod
    def donchian_channels(ohlc: DataFrame, length: int = 20) -> tuple[Series, Series]:
        """
        Donchian Channel: the highest high and lowest low over the trailing
        `length` bars, NOT including the current bar (shifted by one) -- a bar
        can't "break out" of a channel that already includes its own extreme.
        The classic Turtle-style breakout channel: no consolidation/dwell-time
        requirement, unlike `support_resistance_levels`, just a pure N-bar price
        extreme.

        Args:
            ohlc: OHLC DataFrame with high, low columns.
            length: lookback bar count.

        Returns:
            Tuple of (lower, upper) Series, same index as `ohlc`. NaN for the
            first `length` bars (not enough history yet).
        """
        lower = ohlc["low"].rolling(window=length).min().shift(1)
        upper = ohlc["high"].rolling(window=length).max().shift(1)
        lower.name = f"DONCHIAN_L_{length}"
        upper.name = f"DONCHIAN_U_{length}"
        return lower, upper

    @staticmethod
    def atr(ohlc: DataFrame, length: int = 14) -> Series:
        """
        Average True Range -- Wilder's smoothed average of the true range (the
        greatest of high-low, |high-prev_close|, |low-prev_close|), a volatility
        measure in price units rather than a percentage. Used throughout this
        package as a stop-sizing input wherever a level-based stop (a channel
        edge, a moving average) needs an extra buffer to avoid a near-zero risk
        distance.

        Args:
            ohlc: OHLC DataFrame with high, low, close columns.
            length: ATR smoothing period.

        Returns:
            ATR Series, same index as `ohlc`.
        """
        atr = pandas_ta.atr(ohlc["high"], ohlc["low"], ohlc["close"], length=length)
        if atr is None:
            raise ValueError("ATR calculation returned None")

        atr.name = f"ATR_{length}"
        return atr

    @staticmethod
    def keltner_channels(
        ohlc: DataFrame, length: int = 20, atr_length: int = 10, atr_mult: float = 2.0
    ) -> tuple[Series, Series, Series]:
        """
        Keltner Channels -- an EMA midline +/- an ATR-scaled band, the ATR
        analogue of Bollinger Bands' EMA +/- std-dev construction. ATR reacts to
        realized high-low range rather than the variance of closes, so Keltner
        bands tend to hold up better than std-dev bands specifically in trending
        markets (variance-based bands widen only after a move has already
        happened; ATR widens with the range of each new bar as it happens).

        Args:
            ohlc: OHLC DataFrame with high, low, close columns.
            length: EMA midline period.
            atr_length: ATR smoothing period.
            atr_mult: band half-width as a multiple of ATR.

        Returns:
            Tuple of (lower, middle, upper) Series, same index as `ohlc`.
        """
        middle = pandas_ta.ema(ohlc["close"], length)
        if middle is None:
            raise ValueError("EMA calculation for Keltner Channels returned None")

        atr = pandas_ta.atr(ohlc["high"], ohlc["low"], ohlc["close"], length=atr_length)
        if atr is None:
            raise ValueError("ATR calculation for Keltner Channels returned None")

        upper = middle + (atr * atr_mult)
        lower = middle - (atr * atr_mult)

        lower.name = f"KC_L_{length}_{atr_length}_{atr_mult}"
        middle.name = f"KC_M_{length}_{atr_length}_{atr_mult}"
        upper.name = f"KC_U_{length}_{atr_length}_{atr_mult}"
        return lower, middle, upper

    @staticmethod
    def supertrend(ohlc: DataFrame, length: int = 10, multiplier: float = 3.0) -> tuple[Series, Series]:
        """
        SuperTrend -- an ATR-based trend-following line that flips between
        acting as a trailing support (uptrend) and trailing resistance
        (downtrend). Unlike a plain moving average, the line only moves in the
        trend-following direction (ratchets up during an uptrend, down during a
        downtrend) until price closes through it, at which point it flips sides
        and resets -- a construction that makes it double as both a trend
        filter and a trailing stop level. Wraps `pandas_ta.supertrend`
        (recursive/stateful, not a plain rolling window).

        Args:
            ohlc: OHLC DataFrame with high, low, close columns.
            length: ATR/basis smoothing period.
            multiplier: band distance as a multiple of ATR.

        Returns:
            Tuple of (line, direction) Series, same index as `ohlc`. `line` is
            the active support/resistance level; `direction` is 1.0 during an
            uptrend (line below price, acting as support) and -1.0 during a
            downtrend (line above price, acting as resistance). Both NaN during
            warmup. Column names are matched by prefix rather than
            reconstructed from `length`/`multiplier` -- pandas_ta formats the
            multiplier into the column name using its own float repr, which
            doesn't always round-trip from the argument as passed (e.g. `3` vs
            `3.0`).
        """
        result = pandas_ta.supertrend(ohlc["high"], ohlc["low"], ohlc["close"], length=length, multiplier=multiplier)
        if result is None:
            raise ValueError("SuperTrend calculation returned None")

        line_col = next(c for c in result.columns if c.startswith("SUPERT_"))
        direction_col = next(c for c in result.columns if c.startswith("SUPERTd_"))
        line = result[line_col]
        direction = result[direction_col]
        line.name = f"SUPERT_{length}_{multiplier}"
        direction.name = f"SUPERTd_{length}_{multiplier}"
        return line, direction

    @staticmethod
    def support_resistance_levels(  # noqa: PLR0913
        ohlc: DataFrame,
        *,
        window: int,
        bin_size: float,
        min_touches: int,
        recompute_every: int,
        psych_grid_size: float | None,
        column: str = "close",
    ) -> tuple[Series, Series]:
        """Rolling horizontal support/resistance levels: an empirical "time at
        price" histogram unioned with a psychological round-number grid.

        Every `recompute_every` bars, the trailing `window` bars' `column` values
        are bucketed into `bin_size`-wide price bins. A bin becomes an empirical
        level ("high-dwell node") if its bar count is both >= min_touches and a
        local peak against its immediate neighbor bins -- ties are *not* collapsed
        to a single bin, so a plateau of equal-count adjacent bins each become a
        level; harmless since they're already within one bin_size of each other
        and the nearest-level lookup below treats the whole set independently.
        These are unioned with a static grid every `psych_grid_size` price units
        spanning the window's price range (skipped entirely if `psych_grid_size`
        is None) into one sorted active-level array that holds until the next
        recompute -- level sets don't need per-bar freshness, and recomputing less
        often is far cheaper over a large window.

        Every bar (not just recompute points) is then compared against whichever
        level set is currently active, using only bars *before* it (the window
        used at a recompute at index i is `[i - window, i)`, never including bar i
        itself -- no lookahead). Returns the nearest active level at-or-below
        `column` (support, inclusive of an exact match) and strictly above it
        (resistance) -- NaN for both during the initial `window`-bar warmup or any
        stretch where no level qualified.

        Args:
            ohlc: OHLC DataFrame, ascending timestamp index.
            window: trailing bar count the histogram/grid are computed over.
            bin_size: price-unit width of each histogram bin -- scale to the
                instrument's tick size (e.g. a few ticks wide, not one tick).
            min_touches: minimum bar count for a histogram bin to count as a level.
            recompute_every: bar stride between level-set recomputes. 1 recomputes
                every bar (most accurate, most expensive); a coarser stride (e.g.
                one trading day's worth of bars) is usually enough.
            psych_grid_size: spacing of the round-number grid, or None to disable
                it (empirical levels only).
            column: OHLC column the histogram/grid are built from.

        Returns:
            (nearest_support, nearest_resistance) Series, same index as `ohlc`.
        """
        price = ohlc[column].to_numpy(dtype=float)
        n = len(price)
        bin_idx = np.floor(price / bin_size).astype(np.int64)

        nearest_support = np.full(n, np.nan)
        nearest_resistance = np.full(n, np.nan)
        active_levels: np.ndarray = np.array([], dtype=float)

        for i in range(n):
            if i >= window and (i - window) % recompute_every == 0:
                active_levels = Indicators._price_levels_in_window(
                    price[i - window : i],
                    bin_idx[i - window : i],
                    bin_size=bin_size,
                    min_touches=min_touches,
                    psych_grid_size=psych_grid_size,
                )
            if active_levels.size:
                # side="right": an exact match lands support (inclusive) at that
                # level and resistance (strictly above) at the next one up.
                pos = np.searchsorted(active_levels, price[i], side="right")
                if pos > 0:
                    nearest_support[i] = active_levels[pos - 1]
                if pos < active_levels.size:
                    nearest_resistance[i] = active_levels[pos]

        support = Series(nearest_support, index=ohlc.index, name="SR_SUPPORT")
        resistance = Series(nearest_resistance, index=ohlc.index, name="SR_RESISTANCE")
        return support, resistance

    @staticmethod
    def _price_levels_in_window(
        window_price: np.ndarray,
        window_bin_idx: np.ndarray,
        *,
        bin_size: float,
        min_touches: int,
        psych_grid_size: float | None,
    ) -> np.ndarray:
        """One recompute's active-level array: empirical high-dwell bins unioned
        with the psychological grid. Split out of support_resistance_levels purely
        so that method's per-bar loop stays readable."""
        offset = window_bin_idx.min()
        counts = np.bincount(window_bin_idx - offset)
        left_neighbor = np.r_[counts[0], counts[:-1]]
        right_neighbor = np.r_[counts[1:], counts[-1]]
        is_peak = (counts >= min_touches) & (counts >= left_neighbor) & (counts >= right_neighbor)
        hvn_bins = np.nonzero(is_peak)[0] + offset
        hvn_levels = (hvn_bins + 0.5) * bin_size

        if psych_grid_size is not None:
            lo, hi = window_price.min(), window_price.max()
            first_level = np.ceil(lo / psych_grid_size) * psych_grid_size
            psych_levels = np.arange(first_level, hi + psych_grid_size, psych_grid_size)
        else:
            psych_levels = np.array([], dtype=float)

        return np.unique(np.concatenate([hvn_levels, psych_levels]))


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
