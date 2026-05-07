"""5分钟K线数据加载器

为动能砖模拟器提供分钟级数据，支持懒加载和日级缓存。
无分钟数据时返回 None，模拟器自动降级为日线逻辑。
分钟线自动应用前复权，与日线价格体系一致。
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
        # 原始日线收盘价缓存: code -> pd.Series (raw daily close)
        self._daily_close_cache = {}

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
        """从 mootdx 加载并按日过滤，应用前复权"""
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
            day_df = day_df[cols].sort_index()

            ratio = self._get_qfq_ratio(code, date_str)
            if ratio is not None and abs(ratio - 1.0) > 1e-8:
                for col in ("open", "high", "low", "close"):
                    if col in day_df.columns:
                        day_df[col] = day_df[col].astype(float) * ratio

            return day_df
        except Exception:
            return None

    def _get_qfq_ratio(self, code, date_str):
        """计算某日的前复权比例: ratio = qfq_close / raw_daily_close"""
        from src.data.adjustment import get_qfq_close, is_index, QFQ_CACHE_ENABLED

        if not QFQ_CACHE_ENABLED or is_index(code):
            return None

        qfq_close = get_qfq_close(code)
        if qfq_close is None:
            return None

        raw_close = self._get_raw_daily_close(code)
        if raw_close is None:
            return None

        d_ts = pd.Timestamp(date_str)
        if d_ts not in qfq_close.index or d_ts not in raw_close.index:
            return None

        rc = raw_close[d_ts]
        if rc <= 0:
            return None

        return float(qfq_close[d_ts] / rc)

    def _get_raw_daily_close(self, code):
        """获取原始日线收盘价（每只股票只加载一次）"""
        if code not in self._daily_close_cache:
            try:
                df = self._reader.daily(symbol=code)
                if df is not None and not df.empty:
                    df = df.sort_index()
                    self._daily_close_cache[code] = df["close"].astype(float)
                else:
                    self._daily_close_cache[code] = None
            except Exception:
                self._daily_close_cache[code] = None
        return self._daily_close_cache[code]

    def clear_cache(self):
        """清空缓存（每日结束时调用）"""
        self._cache.clear()
