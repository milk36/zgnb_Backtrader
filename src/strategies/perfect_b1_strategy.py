"""完美B1策略

基于 thinking/完美B1.md 中6种量价模式，在V4 B1信号基础上增加模式质量过滤。

核心逻辑：
1. V4 B1信号作为基础（七子条件OR + 盈亏比 + vol_expand_ok）
2. 叠加5种模式匹配（OR），仅保留匹配强势模式的B1
3. 过滤掉短期B1（缩量评分>35%且无其他强势模式）
4. 按缩量评分升序排序（缩量越极致优先级越高）

五种模式：
- 模式一：典型单波B1 — shrink<30% & J<14 & 贴近白/黄线
- 模式二：白线不死叉B1 — 回调30天白线>黄线 & J<14
- 模式三：多波N型B1 — 60日内>=2次上穿黄线 & shrink<26%
- 模式四：跌破反转B1 — 收盘<黄线 & shrink<28% & J<14
- 模式五：大牛市B1 — 收盘>黄线*1.30 & shrink<25%

架构：包装 V4 的 _compute_all_bar_signals()，叠加模式过滤，
复用 PortfolioSimulator 的标准六级退出，100万/10只。
"""

import time
import warnings

import numpy as np
import pandas as pd
from MyTT import ABS, COUNT, CROSS, EVERY, HHV, MA, REF

from config import (
    TDX_DIR, TDX_MARKET, SCAN_MAX_WORKERS,
    HUANGBAI_M1, HUANGBAI_M2, HUANGBAI_M3, HUANGBAI_M4,
    HUANGBAI_N, HUANGBAI_M, HUANGBAI_N1, HUANGBAI_N2,
    DNZH_MIN_MARKET_CAP,
)
from src.strategies.huangbai_b1_v4_strategy import (
    _compute_all_bar_signals as _v4_compute_all_bar_signals,
    _compute_signals as _v4_compute_signals,
    _get_all_codes,
)
from src.strategies.dongneng_zhuan_strategy import _load_capital_data

warnings.filterwarnings("ignore")


# ================================================================== #
#  进程级变量                                                          #
# ================================================================== #

_process_reader = None


def _init_process(tdxdir, market):
    global _process_reader
    from mootdx.reader import Reader
    _process_reader = Reader.factory(market=market, tdxdir=tdxdir)
    from src.data.adjustment import preload_disk_cache
    preload_disk_cache()


# ================================================================== #
#  模式匹配                                                            #
# ================================================================== #

PATTERN_NAMES = {
    0: "不匹配",
    1: "典型单波",
    2: "白线不死叉",
    3: "多波N型",
    4: "跌破反转",
    5: "大牛市",
}


def _compute_pattern_matches(C, white, yellow, shrink_score, J,
                              dist_w, dist_y):
    """计算5种模式匹配结果

    Args:
        C: 收盘价数组
        white: 白线数组
        yellow: 黄线数组
        shrink_score: 缩量评分数组 (V/HHV(V,20))
        J: KDJ-J值数组
        dist_w: |C - white| / C * 100
        dist_y: |C - yellow| / yellow * 100

    Returns:
        (pattern_1..5, pattern_type) — 5个布尔数组 + 模式编号数组
    """
    n = len(C)

    # 模式一：典型单波B1
    # shrink<30% & J<14 & (贴近白线≤2.5% 或 贴近黄线≤10%)
    p1 = (shrink_score < 0.30) & (J < 14) & ((dist_w <= 2.5) | (dist_y <= 10.0))

    # 模式二：白线不死叉B1
    # 30天内白线始终>=黄线 & J<14
    white_above_yellow = EVERY(white >= yellow, 30)
    p2 = white_above_yellow & (J < 14)

    # 模式三：多波N型B1
    # 60日内>=2次上穿黄线 & shrink<26%
    cross_up = CROSS(C, yellow)
    wave_count = COUNT(cross_up, 60)
    p3 = (wave_count >= 2) & (shrink_score < 0.26)

    # 模式四：跌破反转B1
    # C<yellow & shrink<28% & J<14
    p4 = (C < yellow) & (shrink_score < 0.28) & (J < 14)

    # 模式五：大牛市B1
    # C > yellow*1.30 & shrink<25%
    above_yellow_pct = (C - yellow) / np.maximum(yellow, 0.001) * 100
    p5 = (above_yellow_pct > 30) & (shrink_score < 0.25)

    # 模式编号（优先级：3>4>1>2>5，多波和跌破反转最可靠）
    pattern_type = np.zeros(n, dtype=int)
    # 逆序赋值，优先级高的后写覆盖
    for pval, parr in [(5, p5), (2, p2), (1, p1), (4, p4), (3, p3)]:
        pattern_type[parr] = pval

    pattern_matched = p1 | p2 | p3 | p4 | p5
    return p1, p2, p3, p4, p5, pattern_matched, pattern_type


# ================================================================== #
#  信号计算                                                            #
# ================================================================== #


def _compute_all_bar_signals(C, H, L, O, V, dates, params, capital_shares=None):
    """完美B1: V4 B1 + 5种模式过滤"""
    signals = _v4_compute_all_bar_signals(C, H, L, O, V, dates, params, capital_shares)
    if signals is None:
        return None

    b1_original = signals["b1"].copy()
    white = signals["white"]
    yellow = signals["yellow"]
    shrink_score = signals["shrink_score"]

    # 计算KDJ-J（从V4的_compute_all_bar_signals没有直接返回J值，需重新计算）
    from MyTT import LLV, SMA
    llv9 = LLV(L, 9)
    hhv9 = HHV(H, 9)
    denom9 = hhv9 - llv9
    rsv = np.where(denom9 != 0, (C - llv9) / denom9 * 100, 50.0)
    K = SMA(rsv, 3, 1)
    D = SMA(K, 3, 1)
    J = 3 * K - 2 * D

    # 距离指标
    dist_w = ABS(C - white) / np.maximum(C, 0.001) * 100
    dist_y = ABS(C - yellow) / np.maximum(yellow, 0.001) * 100

    # 模式匹配
    p1, p2, p3, p4, p5, pattern_matched, pattern_type = _compute_pattern_matches(
        C, white, yellow, shrink_score, J, dist_w, dist_y)

    # 完美B1 = V4 B1 & 至少匹配一种强势模式
    signals["b1"] = b1_original & pattern_matched

    # 保存辅助字段
    signals["b1_original"] = b1_original
    signals["pattern_type"] = pattern_type
    signals["pattern_p1"] = p1
    signals["pattern_p2"] = p2
    signals["pattern_p3"] = p3
    signals["pattern_p4"] = p4
    signals["pattern_p5"] = p5
    signals["J"] = J
    signals["dist_w"] = dist_w
    signals["dist_y"] = dist_y

    # 不做B2涨幅排序，使用shrink_score排序（b2_sort_primary=inf）
    signals["b2_sort_primary"] = np.full(len(C), float('inf'))

    return signals


def _compute_signals(C, H, L, O, V, dates, params):
    """计算最新 bar 的完美B1信号"""
    all_bars = _compute_all_bar_signals(C, H, L, O, V, dates, params)
    if all_bars is None:
        return None

    i = len(C) - 1
    if i < 1:
        return None

    return {
        "weekly": bool(all_bars["weekly_bull"][i] and all_bars["above_ma30w"][i]),
        "gc": True,
        "market_macd": True,
        "b1": bool(all_bars["b1"][i]),
        "dongneng_recent": True,
        "vol_expand": bool(all_bars["vol_expand_ok"][i]),
        "no_huge_vol_bearish": bool(all_bars["no_huge_vol_bearish"][i]),
        "close": float(C[i]),
        "J": float(all_bars["J"][i]),
        "RSI": 0.0,
        "shrink_score": float(all_bars["shrink_score"][i]),
        "pattern_type": int(all_bars["pattern_type"][i]),
        "b1_original": bool(all_bars["b1_original"][i]),
    }


# ================================================================== #
#  全市场选股扫描                                                      #
# ================================================================== #


def _scan_one(code, params, skip_weekly, market_macd_ok=True):
    """扫描单只股票"""
    assert _process_reader is not None, "_process_reader 未初始化"
    try:
        df = _process_reader.daily(symbol=code)
        if df is None or len(df) < 300:
            return code, None, False
        df = df.sort_index()
        from src.data.adjustment import apply_qfq
        df = apply_qfq(df, code)
        sig = _compute_signals(
            df["close"].values.astype(float),
            df["high"].values.astype(float),
            df["low"].values.astype(float),
            df["open"].values.astype(float),
            df["volume"].values.astype(float),
            df.index, params)
        if sig is None:
            return code, None, False
        vol_expand_ok = sig.get("vol_expand", True)
        no_huge_vol_bearish = sig.get("no_huge_vol_bearish", True)
        if sig["b1"] and vol_expand_ok and no_huge_vol_bearish and market_macd_ok:
            sig["code"] = code
            return code, sig, False
        return code, None, False
    except Exception as e:
        return code, {"error": str(e)}, True


def scan_all(stock_type="main", skip_weekly=False,
             tdxdir=TDX_DIR, market=TDX_MARKET, max_workers=SCAN_MAX_WORKERS,
             skip_on_bear=False):
    """完美B1全市场扫描（不做大盘MACD过滤）"""
    from concurrent.futures import ProcessPoolExecutor, as_completed

    codes = _get_all_codes(tdxdir)
    total = len(codes)
    print(f"扫描 {total} 只A股(完美B1)... (workers={max_workers or 'auto'})")

    params = {
        "m1": HUANGBAI_M1, "m2": HUANGBAI_M2, "m3": HUANGBAI_M3, "m4": HUANGBAI_M4,
        "n": HUANGBAI_N, "m": HUANGBAI_M, "n1": HUANGBAI_N1, "n2": HUANGBAI_N2,
        "wma30": 30, "wma60": 60, "wma120": 120, "wma240": 240,
        "stock_type": stock_type,
        "min_market_cap": DNZH_MIN_MARKET_CAP,
        "_capital_data": {},
    }

    results = []
    errors = 0
    done = 0
    t0 = time.time()

    with ProcessPoolExecutor(
        max_workers=max_workers,
        initializer=_init_process,
        initargs=(tdxdir, market),
    ) as pool:
        futures = {
            pool.submit(_scan_one, code, params, skip_weekly, True): code
            for code in codes
        }
        for future in as_completed(futures):
            code, sig, err = future.result()
            done += 1
            if err:
                errors += 1
            elif sig is not None:
                results.append(sig)
                pt = sig.get("pattern_type", 0)
                pname = PATTERN_NAMES.get(pt, "?")
                print(f"  {code}  C={sig['close']:.2f}  "
                      f"J={sig['J']:.1f}  缩量={sig['shrink_score']:.3f}  "
                      f"模式={pname}")
            if done % 500 == 0:
                print(f"  ... 已扫描 {done}/{total} ({done/total*100:.0f}%)  "
                      f"命中 {len(results)}  耗时 {time.time()-t0:.1f}s")

    elapsed = time.time() - t0
    results.sort(key=lambda x: x["shrink_score"])

    print(f"\n{'=' * 55}")
    print(f"  完美B1扫描完成: {total} 只  命中 {len(results)} 只  "
          f"错误 {errors}  耗时 {elapsed:.1f}s")
    print(f"{'=' * 55}")

    if results:
        print(f"\n  选股结果（按缩量排序）")
        print(f"{'=' * 55}")
        for r in results:
            tag = " <<< TOP" if r == results[0] else ""
            pt = r.get("pattern_type", 0)
            pname = PATTERN_NAMES.get(pt, "?")
            print(f"  {r['code']}  C={r['close']:.2f}  "
                  f"J={r['J']:.1f}  缩量={r['shrink_score']:.3f}  "
                  f"模式={pname}{tag}")

    return results, True


# ================================================================== #
#  组合级模拟预加载                                                     #
# ================================================================== #


def _scan_one_all_bars(code, params):
    """加载单只股票数据并计算全量每bar信号"""
    assert _process_reader is not None, "_process_reader 未初始化"
    try:
        df = _process_reader.daily(symbol=code)
        if df is None or len(df) < 300:
            return code, None, False
        df = df.sort_index()
        from src.data.adjustment import apply_qfq
        df = apply_qfq(df, code)
        capital_shares = params.get("_capital_data", {}).get(code)
        signals = _compute_all_bar_signals(
            df["close"].values.astype(float),
            df["high"].values.astype(float),
            df["low"].values.astype(float),
            df["open"].values.astype(float),
            df["volume"].values.astype(float),
            df.index, params, capital_shares)
        if signals is not None:
            amount = df["amount"].values.astype(float)
            signals["avg_amount_20"] = pd.Series(amount).rolling(
                20, min_periods=1).mean().values
        return code, signals, False
    except Exception as e:
        return code, {"error": str(e)}, True


def preload_all_signals(start="2024-01-01", end="2025-12-31",
                        stock_type="main", max_workers=SCAN_MAX_WORKERS,
                        tdxdir=TDX_DIR, market=TDX_MARKET):
    """完美B1预加载（不做大盘MACD过滤）

    Returns:
        (all_signals, trading_days, None) — 第三项始终为None，兼容调用方解包
    """
    from concurrent.futures import ProcessPoolExecutor, as_completed

    codes = _get_all_codes(tdxdir)
    total = len(codes)
    print(f"预加载 {total} 只A股信号(完美B1)... (workers={max_workers or 'auto'})")

    capital_data = _load_capital_data(tdxdir)
    if capital_data:
        print(f"  已加载 {len(capital_data)} 只股票流通股本数据 (>{DNZH_MIN_MARKET_CAP}亿)")
    else:
        print("  警告: 无法加载流通股本数据，跳过流通市值过滤")

    params = {
        "m1": HUANGBAI_M1, "m2": HUANGBAI_M2, "m3": HUANGBAI_M3, "m4": HUANGBAI_M4,
        "n": HUANGBAI_N, "m": HUANGBAI_M, "n1": HUANGBAI_N1, "n2": HUANGBAI_N2,
        "wma30": 30, "wma60": 60, "wma120": 120, "wma240": 240,
        "stock_type": stock_type,
        "min_market_cap": DNZH_MIN_MARKET_CAP,
        "_capital_data": capital_data or {},
    }

    all_signals = {}
    errors = 0
    error_details = []
    done = 0
    t0 = time.time()
    all_dates_index = pd.DatetimeIndex([])

    with ProcessPoolExecutor(
        max_workers=max_workers,
        initializer=_init_process,
        initargs=(tdxdir, market),
    ) as pool:
        futures = {
            pool.submit(_scan_one_all_bars, code, params): code
            for code in codes
        }
        for future in as_completed(futures):
            code, signals, err = future.result()
            done += 1
            if err:
                errors += 1
                if isinstance(signals, dict) and "error" in signals:
                    error_details.append(f"{code}: {signals['error']}")
            elif signals is not None:
                all_signals[code] = signals
                all_dates_index = all_dates_index.union(signals["dates"])
            if done % 500 == 0:
                print(f"  ... 已处理 {done}/{total} ({done/total*100:.0f}%)  "
                      f"有效 {len(all_signals)}  耗时 {time.time()-t0:.1f}s")

    elapsed = time.time() - t0

    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)
    mask = (all_dates_index >= start_ts) & (all_dates_index <= end_ts)
    trading_days = all_dates_index[mask].sort_values().unique()
    trading_days = pd.DatetimeIndex(trading_days)

    print(f"\n  预加载完成: {len(all_signals)} 只  错误 {errors}  "
          f"交易日 {len(trading_days)}  耗时 {elapsed:.1f}s")

    if len(trading_days) > 0:
        first = trading_days[0]
        last = trading_days[-1]
        years = pd.Series(trading_days.year).value_counts().sort_index()
        year_info = "  ".join(f"{y}年:{c}天" for y, c in years.items())
        print(f"  数据范围: {first.strftime('%Y-%m-%d')} ~ {last.strftime('%Y-%m-%d')}  [{year_info}]")

    if error_details and len(error_details) <= 5:
        for ed in error_details:
            print(f"  错误: {ed}")

    return all_signals, trading_days, None
