"""5分钟K线数据加载器

为动能砖模拟器提供分钟级数据，支持懒加载和日级缓存。
无分钟数据时返回 None，模拟器自动降级为日线逻辑。
"""

import numpy as np
import pandas as pd
from mootdx.reader import Reader

from config import TDX_DIR, TDX_MARKET


class MinuteFeed:
    """5分钟K线数据加载器，带日级缓存"""

    def __init__(self, tdxdir=TDX_DIR, market=TDX_MARKET):
        self._reader = Reader.factory(market=market, tdxdir=tdxdir)
        # cache: (code, date_str) -> DataFrame or None（失败也缓存）
        self._cache = {}

    def get_minute_bars(self, code, date):
        """获取单只股票某日的5分钟K线

        Args:
            code: 6位股票代码
            date: pd.Timestamp 或日期字符串

        Returns:
            DataFrame（约48行）包含 open/high/low/close/volume，按时间升序；
            无数据时返回 None。
        """
        date_str = str(date)[:10]
        cache_key = (code, date_str)

        if cache_key in self._cache:
            return self._cache[cache_key]

        result = self._load(code, date_str)
        self._cache[cache_key] = result
        return result

    def _load(self, code, date_str):
        """从 mootdx 加载并按日过滤"""
        try:
            df = self._reader.minute(symbol=code, suffix=5)
            if df is None or df.empty:
                return None

            day_mask = df.index.strftime("%Y-%m-%d") == date_str
            day_df = df.loc[day_mask]
            if day_df.empty:
                return None

            cols = [c for c in ["open", "high", "low", "close", "volume"]
                    if c in day_df.columns]
            return day_df[cols].sort_index()
        except Exception:
            return None

    def clear_cache(self):
        """清空缓存（每日结束时调用）"""
        self._cache.clear()
