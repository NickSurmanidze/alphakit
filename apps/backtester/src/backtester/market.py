import pandas as pd


class MarketDataFromCSV:
    """Loads OHLCV data from a CSV file, resamples it to the requested interval, and
    filters it to a date range -- the standard way this package's tests/notebooks feed
    real historical data into a Market."""

    def __init__(  # noqa: PLR0913
        self, symbol: str, date_from: str, date_to: str, interval: int, unit_of_time: str, path: str
    ) -> None:
        """Eagerly loads and processes the CSV at `path` via fetch_ohlc()."""
        self.data = self.fetch_ohlc(symbol, date_from, date_to, interval, unit_of_time, path)

    @staticmethod
    def fetch_ohlc(  # noqa: PLR0913
        symbol: str, date_from: str, date_to: str, interval: int, unit_of_time: str, path: str
    ):
        """Reads the CSV, resamples to `interval`/`unit_of_time` buckets (open=first,
        high=max, low=min, close=last, volume=sum), and returns only rows within
        [date_from, date_to), indexed by bucket close time."""
        df = pd.read_csv(path)

        # check if Volume column exists - TradingView csv has it with capital V
        if "Volume" in df.columns:
            df["volume"] = df["Volume"]

        timedelta = str(interval)

        if unit_of_time == "minute":
            timedelta = timedelta + "T"
        if unit_of_time == "hour":
            timedelta = timedelta + "h"
        if unit_of_time == "day":
            timedelta = timedelta + "D"

        # close time would be open time + time delta - 1 millisecond

        if "timestamp" in df.columns:
            # 1️⃣ Convert the string column to datetime
            df["time"] = pd.to_datetime(df["timestamp"], errors="coerce", utc=True)
            # 2️⃣ Convert to UNIX timestamp in seconds
            df["time"] = df["time"].view("int64") // 10**9

        df["time_open"] = pd.to_datetime(df["time"], unit="s")
        df["time_close"] = df["time_open"] + pd.Timedelta(timedelta) - pd.Timedelta("1ms")
        df["ts"] = df["time_open"]
        df.set_index("ts", inplace=True)

        df = df.resample(timedelta).agg(
            {
                "time_open": "first",
                "time_close": "last",
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum",
            }
        )

        df["time_open"] = df.index.to_series()
        df["time_close"] = df["time_open"] + pd.Timedelta(timedelta) - pd.Timedelta("1ms")
        df["ts"] = df["time_close"]
        df.set_index("ts", inplace=True)

        # filter data by date
        mask = (df["time_open"] >= date_from) & (df["time_close"] < date_to)
        df = df.loc[mask]

        return df

    def get_df(self):
        """Returns the loaded/resampled OHLCV DataFrame."""
        return self.data


# market contains all symbols and technical indicators
class Market:
    """Holds every symbol's OHLC DataFrame and indicator Series, then compiles them into
    a flat dict (`self.data`, keyed by close timestamp) for O(1) per-candle lookup during
    the backtest loop. `self.current` is the live candle dict every other module reads
    each step."""

    def __init__(self):
        """Starts with no markets/indicators loaded and no current candle set."""
        # main storage
        self.markets = dict()
        self.indicators = dict()

        # index and invested index for querying by timestamp or by
        self.index_num_ts = dict()
        self.index_ts_num = dict()

        # all markets and signals merged into one big fat dataframe
        self.merged: pd.DataFrame | None = None

        self.data = dict()

        # current candle
        self.current = dict()

    # will add a dataframe with market ohlc
    def add_market(self, symbol: str, df: pd.DataFrame):
        """Registers `df` as the OHLC data for `symbol`. Call compile() afterwards to
        rebuild the flat per-candle lookup."""
        self.markets[symbol] = df
        return self.markets

    def get_market(self, symbol: str) -> pd.DataFrame:
        """Returns the raw OHLC DataFrame for `symbol` (raises if not registered)."""
        if symbol not in self.markets:
            raise ValueError(f"Market with symbol {symbol} not found")

        market = self.markets[symbol]

        if not isinstance(market, pd.DataFrame):
            raise ValueError(f"Market with symbol {symbol} is not a DataFrame")

        return market

    # will add a technical indicator to the marker
    def add_indicator(
        self,
        symbol: str,
        interval: int,
        unit_of_time: str,
        indicator_name: str,
        df: pd.DataFrame | pd.Series,
    ):
        """Registers an indicator Series/DataFrame under a composite key so it can be
        merged alongside the matching symbol's OHLC data in compile()."""
        key = f"{symbol}__{interval}__{unit_of_time}__{indicator_name}"
        self.indicators[key] = df
        return self.indicators

    # adds technical indicator to the store
    def get_indicator(self, symbol: str, interval: int, unit_of_time: str, indicator_name: str):
        """Looks up a previously-registered indicator by its composite key."""
        key = f"{symbol}__{interval}__{unit_of_time}__{indicator_name}"
        return self.indicators[key]

    def set_current_market_from_num_index(self, num: int):
        """Sets `self.current` to the candle at sequential position `num`."""
        self.current = self.data[self.index_num_ts[num]]
        return self.current

    def set_current_market_from_ts_index(self, ts: pd.Timestamp):
        """Sets `self.current` to the candle whose close timestamp is `ts`."""
        self.current = self.data[ts]
        return self.current

    def get_market_by_ts(self, ts: pd.Timestamp):
        """Returns the candle dict at close timestamp `ts` without changing `self.current`."""
        return self.data[ts]

    def get_market_by_num(self, num: int):
        """Returns the candle dict at sequential position `num` without changing
        `self.current`."""
        return self.data[self.index_num_ts[num]]

    def set_next_candle_as_current_market(self):
        """Advances `self.current` to the next candle in sequence -- the core "tick
        forward" operation the backtest loop calls every step."""
        self.set_current_market_from_num_index(num=self.current["num"] + 1)
        return self.current

    def reset(self):
        """Rewinds `self.current` back to the first compiled candle."""
        self.set_current_market_from_num_index(num=0)

    # merges all market OHLC dataframes and indicators into self.merged
    def merge(self) -> pd.DataFrame:
        """Combines every registered symbol's OHLC columns and every registered
        indicator into one wide DataFrame (`self.merged`), forward-filled and numbered
        sequentially -- the intermediate step before compile() flattens it into
        per-candle dicts."""
        # Step 1: first we merge all markets
        # ohlc columns will have symbol as a prefix e.g. BTC/USD_open etc.
        symbols = list(self.markets.keys())
        first_symbol = symbols[0]

        df_merged = pd.DataFrame()

        df_merged["time_close"] = self.markets[first_symbol]["time_close"]
        df_merged["time_open"] = self.markets[first_symbol]["time_open"]

        for symbol in symbols:
            df_merged["m__" + symbol + "__open"] = self.markets[symbol]["open"]
            df_merged["m__" + symbol + "__high"] = self.markets[symbol]["high"]
            df_merged["m__" + symbol + "__low"] = self.markets[symbol]["low"]
            df_merged["m__" + symbol + "__close"] = self.markets[symbol]["close"]
            df_merged["m__" + symbol + "__volume"] = self.markets[symbol]["volume"]

        # Step 2: Now we add technical indicators
        for indicator in list(self.indicators.keys()):
            # check if the indicator is a dataframe or series.
            # some TA indicators create a DF, e.g. bollinger bands generate several columns.
            # others, such as RSI, generate just one serie of RSI values
            if isinstance(self.indicators[indicator], pd.DataFrame):
                for column in self.indicators[indicator].columns:
                    df_merged["i__" + indicator + "__" + column] = self.indicators[indicator][
                        column
                    ]
            else:
                df_merged["i__" + indicator] = self.indicators[indicator]

        df_merged.ffill(inplace=True)
        self.merged = df_merged
        self.merged["num"] = range(0, len(df_merged))
        return self.merged

    # compile raw markets into the consolidated full markets dict
    def compile(self):
        """Builds `self.data`, a dict keyed by close timestamp of plain-Python candle
        dicts (one per symbol, each with its OHLC fields and nested indicator values),
        by flattening `self.merged` (calling merge() first if needed). This is what
        makes each backtest step an O(1) dict lookup instead of a DataFrame slice.
        Also resets `self.current` to the first candle."""
        if self.merged is None:
            self.merge()

        if self.merged is None:
            return None

        _M_PARTS = 3  # m__symbol__title
        _IND_SIMPLE = 5  # i__symbol__interval__unit__name
        _IND_SUBFIELD = 6  # i__symbol__interval__unit__name__subfield

        # Pre-classify columns once so the inner loop does no string parsing.
        # Each tuple is (column_name, symbol, field_key).
        m_cols: list[tuple[str, str, str]] = []
        i_cols: list[tuple[str, str, str]] = []

        for col in self.merged.columns:
            parts = col.split("__")
            prefix = parts[0]
            if prefix == "m" and len(parts) == _M_PARTS:
                m_cols.append((col, parts[1], parts[2]))
            elif prefix == "i" and len(parts) == _IND_SIMPLE:
                i_cols.append((col, parts[1], parts[4]))
            elif prefix == "i" and len(parts) == _IND_SUBFIELD:
                i_cols.append((col, parts[1], parts[4] + "__" + parts[5]))

        # to_dict("records") converts the whole frame to plain Python dicts in one
        # vectorised pass — far cheaper than creating a pd.Series per row via iterrows().
        for index, row in zip(self.merged.index, self.merged.to_dict("records"), strict=True):
            market_dict: dict = {
                "num": row["num"],
                "time_open": row["time_open"],
                "time_close": row["time_close"],
            }

            self.index_ts_num[row["time_close"]] = row["num"]
            self.index_num_ts[row["num"]] = row["time_close"]

            for col, symbol, title in m_cols:
                if symbol not in market_dict:
                    market_dict[symbol] = {}
                market_dict[symbol][title] = row[col]

            for col, symbol, indicator_key in i_cols:
                if symbol not in market_dict:
                    market_dict[symbol] = {"indicators": {}}
                elif "indicators" not in market_dict[symbol]:
                    market_dict[symbol]["indicators"] = {}
                market_dict[symbol]["indicators"][indicator_key] = row[col]

            self.data[index] = market_dict

        self.set_current_market_from_num_index(num=0)
        return self.data
