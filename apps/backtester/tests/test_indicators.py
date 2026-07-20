"""Hand-verified unit tests for Indicators.vwap_session -- each value computed by
hand (cumulative volume-weighted mean/variance of typical price) and asserted via
pytest.approx, not just "didn't crash"."""

import pandas as pd
import pytest

from backtester.indicators import Indicators


def _ohlcv(index, high, low, close, volume) -> pd.DataFrame:
    return pd.DataFrame(
        {"high": high, "low": low, "close": close, "volume": volume},
        index=pd.to_datetime(index),
    )


class TestVwapSession:
    def test_accumulates_within_a_session(self):
        # Typical price ((h+l+c)/3) per bar: 100, 104, 110. cum_vol: 10, 30, 40.
        # vwap = cum(typical*volume)/cum(volume).
        df = _ohlcv(
            ["2024-01-01 00:00", "2024-01-01 01:00", "2024-01-01 02:00"],
            high=[102, 106, 112],
            low=[98, 102, 108],
            close=[100, 104, 110],
            volume=[10, 20, 10],
        )

        vwap, std = Indicators.vwap_session(df)

        assert vwap.tolist() == pytest.approx([100.0, 102.666667, 104.5])
        # A single data point has zero variance -- the session's first bar is
        # always exactly 0, not just "small".
        assert std.iloc[0] == pytest.approx(0.0, abs=1e-9)
        assert std.tolist() == pytest.approx([0.0, 1.885618, 3.570714])

    def test_resets_at_the_next_utc_calendar_day(self):
        # Second session's first bar must depend only on its own typical
        # price/volume, not carry over cumulative sums from the first session.
        df = _ohlcv(
            ["2024-01-01 00:00", "2024-01-01 01:00", "2024-01-02 00:00", "2024-01-02 01:00"],
            high=[102, 106, 50, 54],
            low=[98, 102, 46, 50],
            close=[100, 104, 48, 52],
            volume=[10, 20, 5, 15],
        )

        vwap, std = Indicators.vwap_session(df)

        day2 = vwap.loc["2024-01-02"]
        day2_std = std.loc["2024-01-02"]
        assert day2.iloc[0] == pytest.approx(48.0)  # day 2's own first typical price
        assert day2_std.iloc[0] == pytest.approx(0.0, abs=1e-9)
        assert day2.iloc[1] == pytest.approx(51.0)
        assert day2_std.iloc[1] == pytest.approx(1.732051)

    def test_returned_series_share_the_input_index(self):
        df = _ohlcv(
            ["2024-01-01 00:00", "2024-01-01 01:00"],
            high=[102, 106],
            low=[98, 102],
            close=[100, 104],
            volume=[10, 20],
        )

        vwap, std = Indicators.vwap_session(df)

        assert vwap.index.equals(df.index)
        assert std.index.equals(df.index)


class TestDonchianChannels:
    def test_channel_excludes_the_current_bar(self):
        # length=3: bar 3's channel comes from bars 0-2 only (high=[10,12,11],
        # low=[8,9,7]) -- NOT bar 3's own high=20/low=1, which would trivially
        # make every bar "break out" of a channel containing itself.
        df = pd.DataFrame({
            "high": [10, 12, 11, 20, 13],
            "low": [8, 9, 7, 1, 9],
        }, index=pd.date_range("2024-01-01", periods=5, freq="1h"))

        lower, upper = Indicators.donchian_channels(df, length=3)

        assert upper.iloc[3] == pytest.approx(12)
        assert lower.iloc[3] == pytest.approx(7)

    def test_warmup_bars_are_nan(self):
        df = pd.DataFrame({
            "high": [10, 12, 11, 13],
            "low": [8, 9, 7, 9],
        }, index=pd.date_range("2024-01-01", periods=4, freq="1h"))

        lower, upper = Indicators.donchian_channels(df, length=3)

        assert lower.iloc[:3].isna().all()
        assert upper.iloc[:3].isna().all()

    def test_returned_series_share_the_input_index(self):
        df = pd.DataFrame({
            "high": [10, 12, 11, 13],
            "low": [8, 9, 7, 9],
        }, index=pd.date_range("2024-01-01", periods=4, freq="1h"))

        lower, upper = Indicators.donchian_channels(df, length=2)

        assert lower.index.equals(df.index)
        assert upper.index.equals(df.index)


class TestAtr:
    def test_wider_range_bars_increase_atr(self):
        df_narrow = _ohlcv(
            pd.date_range("2024-01-01", periods=30, freq="1h"),
            high=[101] * 30, low=[99] * 30, close=[100] * 30, volume=1,
        )
        df_wide = _ohlcv(
            pd.date_range("2024-01-01", periods=30, freq="1h"),
            high=[110] * 30, low=[90] * 30, close=[100] * 30, volume=1,
        )

        atr_narrow = Indicators.atr(df_narrow, length=14)
        atr_wide = Indicators.atr(df_wide, length=14)

        assert atr_wide.iloc[-1] > atr_narrow.iloc[-1]

    def test_returned_series_shares_the_input_index(self):
        df = _ohlcv(
            pd.date_range("2024-01-01", periods=20, freq="1h"),
            high=[101] * 20, low=[99] * 20, close=[100] * 20, volume=1,
        )

        atr = Indicators.atr(df, length=5)

        assert atr.index.equals(df.index)


class TestMacdKama:
    def test_histogram_equals_macd_line_minus_signal(self):
        df = _ohlcv(
            pd.date_range("2024-01-01", periods=60, freq="1h"),
            high=[100 + i * 0.3 + 1 for i in range(60)],
            low=[100 + i * 0.3 - 1 for i in range(60)],
            close=[100 + i * 0.3 for i in range(60)],
            volume=1,
        )

        macd_line, macd_histogram, macd_signal = Indicators.macd_kama(df, fast=5, slow=10, signal=5)

        valid = macd_line.notna() & macd_signal.notna() & macd_histogram.notna()
        assert valid.sum() > 0
        assert (macd_histogram[valid]).values == pytest.approx(
            (macd_line[valid] - macd_signal[valid]).values
        )

    def test_uptrend_produces_positive_macd_line(self):
        df = _ohlcv(
            pd.date_range("2024-01-01", periods=60, freq="1h"),
            high=[100 + i * 0.5 + 1 for i in range(60)],
            low=[100 + i * 0.5 - 1 for i in range(60)],
            close=[100 + i * 0.5 for i in range(60)],
            volume=1,
        )

        macd_line, _macd_histogram, _macd_signal = Indicators.macd_kama(df, fast=5, slow=10, signal=5)

        # A steady uptrend means the fast KAMA (closer to price) sits above
        # the slow KAMA (lagging further behind) by the end of the series.
        assert macd_line.dropna().iloc[-1] > 0

    def test_returned_series_share_the_input_index(self):
        df = _ohlcv(
            pd.date_range("2024-01-01", periods=40, freq="1h"),
            high=[101] * 40, low=[99] * 40, close=[100] * 40, volume=1,
        )

        macd_line, macd_histogram, macd_signal = Indicators.macd_kama(df, fast=5, slow=10, signal=5)

        assert macd_line.index.equals(df.index)
        assert macd_histogram.index.equals(df.index)
        assert macd_signal.index.equals(df.index)


class TestKeltnerChannels:
    def test_upper_and_lower_are_symmetric_around_the_middle(self):
        df = _ohlcv(
            pd.date_range("2024-01-01", periods=30, freq="1h"),
            high=[100 + i * 0.5 + 1 for i in range(30)],
            low=[100 + i * 0.5 - 1 for i in range(30)],
            close=[100 + i * 0.5 for i in range(30)],
            volume=1,
        )

        lower, middle, upper = Indicators.keltner_channels(df, length=10, atr_length=5, atr_mult=2.0)

        valid = middle.notna() & upper.notna() & lower.notna()
        assert (upper[valid] - middle[valid]).values == pytest.approx((middle[valid] - lower[valid]).values)

    def test_wider_atr_multiple_widens_the_bands(self):
        df = _ohlcv(
            pd.date_range("2024-01-01", periods=30, freq="1h"),
            high=[100 + (i % 5) + 1 for i in range(30)],
            low=[100 + (i % 5) - 1 for i in range(30)],
            close=[100 + (i % 5) for i in range(30)],
            volume=1,
        )

        lower_narrow, _, upper_narrow = Indicators.keltner_channels(df, length=10, atr_length=5, atr_mult=1.0)
        lower_wide, _, upper_wide = Indicators.keltner_channels(df, length=10, atr_length=5, atr_mult=3.0)

        last = -1
        assert (upper_wide.iloc[last] - lower_wide.iloc[last]) > (upper_narrow.iloc[last] - lower_narrow.iloc[last])

    def test_returned_series_share_the_input_index(self):
        df = _ohlcv(
            pd.date_range("2024-01-01", periods=15, freq="1h"),
            high=[101] * 15, low=[99] * 15, close=[100] * 15, volume=1,
        )

        lower, middle, upper = Indicators.keltner_channels(df, length=5, atr_length=5)

        assert lower.index.equals(df.index)
        assert middle.index.equals(df.index)
        assert upper.index.equals(df.index)


class TestSupertrend:
    def test_flips_direction_when_a_sustained_move_reverses(self):
        # A clean uptrend (rising, tight range) followed by a clean downtrend of
        # the same magnitude should produce at least one direction flip from
        # +1 to -1 -- the whole point of the indicator.
        n = 60
        up = pd.Series(range(100, 100 + n))
        down = pd.Series(range(100 + n, 100, -1))
        close = pd.concat([up, down], ignore_index=True)
        index = pd.date_range("2024-01-01", periods=len(close), freq="1h")
        df = pd.DataFrame({
            "high": (close + 0.5).to_numpy(), "low": (close - 0.5).to_numpy(), "close": close.to_numpy(),
        }, index=index)

        line, direction = Indicators.supertrend(df, length=10, multiplier=3.0)

        valid_direction = direction.dropna()
        assert (valid_direction == 1.0).any()
        assert (valid_direction == -1.0).any()
        # Line and direction agree on sign of (close - line) after warmup: an
        # uptrend's line sits below price (support), a downtrend's sits above.
        aligned = pd.DataFrame({"close": df["close"], "line": line, "direction": direction}).dropna()
        uptrend = aligned[aligned["direction"] == 1.0]
        downtrend = aligned[aligned["direction"] == -1.0]
        assert (uptrend["close"] >= uptrend["line"]).all()
        assert (downtrend["close"] <= downtrend["line"]).all()

    def test_warmup_bars_are_nan(self):
        n = 30
        close = pd.Series(range(100, 100 + n))
        index = pd.date_range("2024-01-01", periods=n, freq="1h")
        df = pd.DataFrame({
            "high": (close + 0.5).to_numpy(), "low": (close - 0.5).to_numpy(), "close": close.to_numpy(),
        }, index=index)

        line, direction = Indicators.supertrend(df, length=10, multiplier=3.0)

        assert pd.isna(line.iloc[0])
        assert pd.isna(direction.iloc[0])

    def test_returned_series_share_the_input_index(self):
        n = 30
        close = pd.Series(range(100, 100 + n))
        index = pd.date_range("2024-01-01", periods=n, freq="1h")
        df = pd.DataFrame({
            "high": (close + 0.5).to_numpy(), "low": (close - 0.5).to_numpy(), "close": close.to_numpy(),
        }, index=index)

        line, direction = Indicators.supertrend(df, length=10, multiplier=3.0)

        assert line.index.equals(df.index)
        assert direction.index.equals(df.index)


def _closes(values: list[float]) -> pd.DataFrame:
    index = pd.date_range("2024-01-01", periods=len(values), freq="1h")
    return pd.DataFrame({"close": values}, index=index)


class TestSupportResistanceLevels:
    def test_warmup_bars_are_nan_before_the_first_recompute(self):
        # window=5 -> no level set exists yet for bars 0..4 (recompute only
        # happens once index 5 is reached), regardless of what their prices are.
        df = _closes([10.2, 10.3, 10.1, 10.4, 10.3, 10.5, 10.7, 10.3])

        support, resistance = Indicators.support_resistance_levels(
            df, window=5, bin_size=1.0, min_touches=3, recompute_every=5, psych_grid_size=None
        )

        assert support.iloc[:5].isna().all()
        assert resistance.iloc[:5].isna().all()

    def test_empirical_high_dwell_bin_is_support_inclusive_resistance_exclusive(self):
        # First 5 bars all floor to bin 10 (bin_size=1.0) -> 5 touches >= min_touches
        # of 3, so bin 10's midpoint (10.5) is the only active level from bar 5
        # onward (recompute_every=5 means no second recompute within this series).
        df = _closes([10.2, 10.3, 10.1, 10.4, 10.3, 10.5, 10.7, 10.3])

        support, resistance = Indicators.support_resistance_levels(
            df, window=5, bin_size=1.0, min_touches=3, recompute_every=5, psych_grid_size=None
        )

        # Exact match on the level: support is inclusive, resistance is not.
        assert support.iloc[5] == pytest.approx(10.5)
        assert pd.isna(resistance.iloc[5])
        # Above the level: support holds, resistance stays out of range (only one
        # level exists, and price is now past it).
        assert support.iloc[6] == pytest.approx(10.5)
        assert pd.isna(resistance.iloc[6])
        # Below the level: no support below it, resistance is the level above.
        assert pd.isna(support.iloc[7])
        assert resistance.iloc[7] == pytest.approx(10.5)

    def test_psychological_grid_levels_when_no_bin_qualifies(self):
        # min_touches=100 is unreachable with a 5-bar window -- no empirical level
        # can ever qualify, isolating the psychological grid's own behavior.
        df = _closes([101.0, 102.0, 103.0, 104.0, 106.0, 107.0])

        support, resistance = Indicators.support_resistance_levels(
            df, window=5, bin_size=1.0, min_touches=100, recompute_every=5, psych_grid_size=5.0
        )

        # Window range is [101, 106] -> grid levels are 105 and 110.
        assert support.iloc[5] == pytest.approx(105.0)
        assert resistance.iloc[5] == pytest.approx(110.0)

    def test_returned_series_share_the_input_index(self):
        df = _closes([10.0, 10.1, 10.2, 10.3, 10.4, 10.5])

        support, resistance = Indicators.support_resistance_levels(
            df, window=5, bin_size=1.0, min_touches=3, recompute_every=5, psych_grid_size=None
        )

        assert support.index.equals(df.index)
        assert resistance.index.equals(df.index)
