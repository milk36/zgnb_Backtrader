"""前复权数据调整模块

通过 akshare 获取前复权收盘价，计算复权比例并应用到 mootdx 原始数据上。
支持本地 parquet 缓存 + 内存缓存，避免重复网络请求。
"""

import os
import time
import numpy as np
import pandas as pd

from config import QFQ_CACHE_ENABLED, QFQ_CACHE_DIR

# 内存缓存：code -> pd.Series(qfq_close, index=DatetimeIndex)
_memory_cache: dict[str, pd.Series] = {}

# 指数代码前缀（不需要复权）
_INDEX_PREFIXES = ("399", "899")


def is_index(code: str) -> bool:
    return code == "000001" or code[:3] in _INDEX_PREFIXES


def _cache_path(code: str) -> str:
    return os.path.join(QFQ_CACHE_DIR, f"{code}.parquet")


def _load_disk_cache(code: str) -> pd.Series | None:
    path = _cache_path(code)
    if not os.path.isfile(path):
        return None
    try:
        df = pd.read_parquet(path)
        dates = pd.to_datetime(df["date"])
        return pd.Series(df["qfq_close"].values, index=dates, name=code)
    except Exception:
        return None


def _save_disk_cache(code: str, qfq_close: pd.Series) -> None:
    os.makedirs(QFQ_CACHE_DIR, exist_ok=True)
    df = pd.DataFrame({"date": qfq_close.index, "qfq_close": qfq_close.values})
    df.to_parquet(_cache_path(code), index=False)


def _fetch_qfq_from_akshare(code: str) -> pd.Series | None:
    import akshare as ak

    try:
        qfq_df = ak.stock_zh_a_hist(
            symbol=code, period="daily",
            start_date="19900101", end_date="20991231",
            adjust="qfq"
        )
        if qfq_df is None or qfq_df.empty:
            return None
        # akshare 列名按位置：col[0]=日期, col[3]=收盘
        dates = pd.to_datetime(qfq_df.iloc[:, 0])
        closes = qfq_df.iloc[:, 3].astype(float)
        return pd.Series(closes.values, index=dates, name=code)
    except Exception as e:
        print(f"  [QFQ] 获取 {code} 失败: {e}")
        return None


def get_qfq_close(code: str) -> pd.Series | None:
    if not QFQ_CACHE_ENABLED or is_index(code):
        return None

    # 内存缓存
    if code in _memory_cache:
        return _memory_cache[code]

    # 磁盘缓存
    cached = _load_disk_cache(code)
    if cached is not None:
        _memory_cache[code] = cached
        return cached

    # 网络获取
    qfq_close = _fetch_qfq_from_akshare(code)
    if qfq_close is not None:
        _save_disk_cache(code, qfq_close)
        _memory_cache[code] = qfq_close
    return qfq_close


def apply_qfq(df: pd.DataFrame, code: str) -> pd.DataFrame:
    """对 mootdx DataFrame 应用前复权。

    ratio = qfq_close / raw_close, 乘以 OHLC 列。volume 不调整。
    """
    if not QFQ_CACHE_ENABLED or df is None or df.empty:
        return df

    qfq_close = get_qfq_close(code)
    if qfq_close is None:
        return df

    # 统一日期索引为 DatetimeIndex
    if isinstance(df.index, pd.DatetimeIndex):
        df_dates = df.index
    else:
        df_dates = pd.to_datetime(df.index)

    # 向量化匹配：只对两个数据源共有的日期计算比例
    common = df_dates.intersection(qfq_close.index)
    if len(common) == 0:
        return df

    raw_close_common = df["close"].reindex(common).astype(float)
    ratio_common = qfq_close.reindex(common) / raw_close_common

    # 构建 ratio 数组（缺失日期填 1.0）
    ratio_full = pd.Series(1.0, index=df_dates)
    ratio_full.loc[common] = ratio_common.values
    ratio_arr = ratio_full.values.astype(float)

    # 全为 1.0 说明无除权事件
    if np.allclose(ratio_arr, 1.0):
        return df

    for col in ("open", "high", "low", "close"):
        if col in df.columns:
            df[col] = df[col].astype(float) * ratio_arr

    return df


def preload_disk_cache() -> int:
    """批量加载磁盘缓存到内存（子进程初始化时调用）"""
    global _memory_cache
    if not os.path.isdir(QFQ_CACHE_DIR):
        return 0

    count = 0
    for f in os.listdir(QFQ_CACHE_DIR):
        if not f.endswith(".parquet"):
            continue
        code = f.replace(".parquet", "")
        s = _load_disk_cache(code)
        if s is not None:
            _memory_cache[code] = s
            count += 1
    return count


def update_cache_batch(codes: list[str]) -> None:
    """批量更新前复权缓存（--update-qfq-cache 调用）"""
    total = len(codes)
    success = failed = skipped = 0
    t0 = time.time()

    print(f"开始更新前复权缓存: {total} 只股票")
    print(f"  缓存目录: {os.path.abspath(QFQ_CACHE_DIR)}")

    for i, code in enumerate(codes):
        if is_index(code):
            skipped += 1
            continue
        try:
            qfq_close = _fetch_qfq_from_akshare(code)
            if qfq_close is not None and len(qfq_close) > 0:
                _save_disk_cache(code, qfq_close)
                _memory_cache[code] = qfq_close
                success += 1
            else:
                failed += 1
        except Exception:
            failed += 1

        if (i + 1) % 100 == 0:
            elapsed = time.time() - t0
            print(f"  [{i+1}/{total}] 成功={success} 失败={failed} "
                  f"跳过={skipped} 耗时={elapsed:.0f}s")

    elapsed = time.time() - t0
    print(f"\n缓存更新完成: 成功={success} 失败={failed} "
          f"跳过={skipped} 总计={total} 耗时={elapsed:.0f}s")
