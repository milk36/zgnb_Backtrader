"""通达信数据馈送模块 - 通过 mootdx 读取本地数据并转为 Backtrader 格式"""

import pandas as pd
import backtrader as bt
from mootdx.reader import Reader

from config import TDX_DIR, TDX_MARKET, DEFAULT_START_DATE, DEFAULT_END_DATE


class TdxDataFeed:
    """封装 mootdx Reader，产出 Backtrader 兼容的 PandasData"""

    def __init__(self, tdxdir: str = TDX_DIR, market: str = TDX_MARKET):
        self._reader = Reader.factory(market=market, tdxdir=tdxdir)

    def get_feed(
        self,
        symbol: str,
        start: str = DEFAULT_START_DATE,
        end: str = DEFAULT_END_DATE,
    ) -> bt.feeds.PandasData:
        df = self._reader.daily(symbol=symbol)
        if df is None or df.empty:
            raise ValueError(f"无法读取股票 {symbol} 的数据，请检查代码和通达信路径")
        from src.data.adjustment import apply_qfq
        df = apply_qfq(df, symbol)
        df = self._normalize(df, start, end)
        return bt.feeds.PandasData(dataname=df)

    def get_feeds(
        self,
        symbols: list[str],
        start: str = DEFAULT_START_DATE,
        end: str = DEFAULT_END_DATE,
    ) -> list[bt.feeds.PandasData]:
        return [self.get_feed(s, start, end) for s in symbols]

    @staticmethod
    def _normalize(df: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
        # 日期索引
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"])
            df.set_index("date", inplace=True)
        elif not isinstance(df.index, pd.DatetimeIndex):
            df.index = pd.to_datetime(df.index)

        # 列名映射
        if "vol" in df.columns and "volume" not in df.columns:
            df.rename(columns={"vol": "volume"}, inplace=True)

        # 添加 openinterest
        df["openinterest"] = 0

        # 保留必要列
        cols = ["open", "high", "low", "close", "volume", "openinterest"]
        df = df[[c for c in cols if c in df.columns]]

        df.sort_index(inplace=True)
        df = df.loc[start:end]
        return df
