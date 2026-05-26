"""完美B1 V2策略

基于 thinking/完美B1.md 中11种个股模式模板，在V4 B1信号基础上增加模式质量过滤。

核心逻辑：
1. V4 B1信号作为基础（七子条件OR + 盈亏比 + vol_expand_ok）
2. 叠加10种强势模式匹配（OR），仅保留匹配强势模式的B1
3. 标记赢时胜预警模式（缩量>35%）作为反向参考
4. 按缩量评分升序排序（缩量越极致优先级越高）

十种强势模式（每只个股独立模板）：
- 模式1:  华纳药厂 — SB1超卖缩量拐头
- 模式2:  宁波韵升 — 三波N型递进缩量
- 模式3:  微芯生物 — 倍量柱快速启动
- 模式4:  方正科技 — 双波递进B1
- 模式5:  澄天伟业 — 白线不死叉+倍量柱B2
- 模式6:  国轩高科 — 双波建仓+深度缩量回调
- 模式7:  野马电池 — 跌破黄线反转B1
- 模式8:  光电股份 — 长期多波极致缩量
- 模式9:  新瀚新材 — 双倍量柱+超级爆发
- 模式10: 昂利康 — 大牛市快速B1

预警模式：
- 模式11: 赢时胜 — 短期B1预警（缩量不极致，涨幅有限）

架构：包装 V4 的 _compute_all_bar_signals()，叠加模式过滤，
复用 PortfolioSimulator 的标准六级退出，100万/10只。
"""

import time
import warnings

import numpy as np
import pandas as pd
from MyTT import ABS, COUNT, CROSS, EVERY, HHV, LLV, MA, REF, SMA

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
    1: "华纳药厂",
    2: "宁波韵升",
    3: "微芯生物",
    4: "方正科技",
    5: "澄天伟业",
    6: "国轩高科",
    7: "野马电池",
    8: "光电股份",
    9: "新瀚新材",
    10: "昂利康",
    11: "赢时胜(预警)",
}


def _compute_pattern_matches(C, white, yellow, shrink_score, J,
                              rsi3, pct_w, pct_y, v_ratio_60,
                              vol_col_count, wave_count_60, wave_count_90,
                              white_above_yellow_30, above_yellow_pct,
                              recent_rise_30d, b1_count_30):
    """计算11种个股模式匹配结果

    每个模式基于一只具体股票的真实量化数据作为匹配模板。
    详情参考 thinking/完美B1.md "B1量价模式识别指南" 章节。
    """
    n = len(C)
    _s = np.nan_to_num(shrink_score, nan=1.0)
    _j = np.nan_to_num(J, nan=50.0)
    _r = np.nan_to_num(rsi3, nan=50.0)
    _pw = np.nan_to_num(pct_w, nan=0.0)
    _py = np.nan_to_num(pct_y, nan=0.0)
    _vr = np.nan_to_num(v_ratio_60, nan=1.0)
    _vc = np.nan_to_num(vol_col_count, nan=0.0)
    _w60 = np.nan_to_num(wave_count_60, nan=0.0)
    _w90 = np.nan_to_num(wave_count_90, nan=0.0)
    _ay = np.nan_to_num(above_yellow_pct, nan=0.0)
    _rr = np.nan_to_num(recent_rise_30d, nan=1.0)
    _bc = np.nan_to_num(b1_count_30, nan=0.0)

    # 模式1: 华纳药厂 — SB1超卖缩量拐头
    # 原型数据: shrink=24.5%, J=-4.5, RSI=22.9, vs黄线=-0.1%
    p1 = (_s < 0.25) & (_j < -4) & (_r < 23) & (np.abs(_py) <= 1.0)

    # 模式2: 宁波韵升 — 三波N型递进缩量
    # 原型数据: 3波N型, shrink=25.3%, J=9.9, vs白线=+0.5%
    p2 = (_w60 >= 3) & (_s < 0.26) & (_j < 13) & (np.abs(_pw) <= 2.0)

    # 模式3: 微芯生物 — 倍量柱快速启动
    # 原型数据: shrink=29.7%, RSI=11.5, J=-10.8, 白线破黄线不破
    p3 = (C < white) & (C > yellow) & (_r < 15) & (_j < -5) & (_s < 0.30)

    # 模式4: 方正科技 — 双波递进B1
    # 原型数据(第二波B1): shrink=24.8%, J=9.5, RSI=16.3, 30日内多次B1
    p4 = (_bc >= 2) & (_s < 0.30) & (_j < 13) & (C < white) & (C > yellow)

    # 模式5: 澄天伟业 — 白线不死叉+倍量柱B2
    # 原型数据: shrink=63.2%(偏高), J=6.0, 白线30天不死叉黄线
    p5 = white_above_yellow_30 & (_j < 13)

    # 模式6: 国轩高科 — 双波建仓+深度缩量回调
    # 原型数据: shrink=27.2%, V/MA60=40.2%, J=1.7, 白线不死叉
    p6 = (white_above_yellow_30 & (_s < 0.28) & (_vr < 0.45)
          & (_j < 5) & (C < white) & (C > yellow))

    # 模式7: 野马电池 — 跌破黄线反转B1
    # 原型数据: vs黄线=-6.4%, RSI=14.7, shrink=27.6%
    p7 = (C < yellow) & (_r < 15) & (_s < 0.28)

    # 模式8: 光电股份 — 长期多波极致缩量
    # 原型数据: shrink=17.9%(最低), J=-6.0, 90日多次上穿黄线
    p8 = (_s < 0.20) & (_j < 0) & (_w90 >= 2)

    # 模式9: 新瀚新材 — 双倍量柱+超级爆发
    # 原型数据: shrink=22.2%, vs黄线=-7.9%, 30日内2次倍量柱
    p9 = (C < yellow) & (_s < 0.25) & (_vc >= 2)

    # 模式10: 昂利康 — 大牛市快速B1
    # 原型数据: vs黄线=+37.7%, shrink=24.5%, 31天+217.5%
    p10 = (_ay > 30) & (_s < 0.25) & (_rr > 1.5)

    # 模式11(预警): 赢时胜 — 短期B1
    # 原型数据: shrink=37.1%(不极致), 后续仅+25%
    p11 = (_s > 0.35)

    strong_matched = p1 | p2 | p3 | p4 | p5 | p6 | p7 | p8 | p9 | p10

    # 模式编号（优先级 8>9>1>7>3>6>2>4>10>5，高优先级后写覆盖）
    pattern_type = np.zeros(n, dtype=int)
    for pval, parr in [(11, p11), (5, p5), (10, p10), (4, p4), (2, p2),
                        (6, p6), (3, p3), (7, p7), (1, p1), (9, p9), (8, p8)]:
        pattern_type[parr] = pval

    return (p1, p2, p3, p4, p5, p6, p7, p8, p9, p10, p11,
            strong_matched, pattern_type)


# ================================================================== #
#  信号计算                                                            #
# ================================================================== #


def _compute_all_bar_signals(C, H, L, O, V, dates, params, capital_shares=None):
    """完美B1 V2: V4 B1 + 11种个股模式过滤"""
    signals = _v4_compute_all_bar_signals(C, H, L, O, V, dates, params,
                                          capital_shares)
    if signals is None:
        return None

    b1_original = signals["b1"].copy()
    white = signals["white"]
    yellow = signals["yellow"]
    shrink_score = signals["shrink_score"]

    # ---- KDJ-J（与v1相同） ----
    llv9 = LLV(L, 9)
    hhv9 = HHV(H, 9)
    denom9 = hhv9 - llv9
    rsv = np.where(denom9 != 0, (C - llv9) / denom9 * 100, 50.0)
    K = SMA(rsv, 3, 1)
    D = SMA(K, 3, 1)
    J = 3 * K - 2 * D

    # ---- V2新增指标 ----

    # RSI(3)
    diff = C - REF(C, 1)
    diff = np.nan_to_num(diff, nan=0.0)
    up = SMA(np.maximum(diff, 0), 3, 1)
    down = SMA(np.abs(diff), 3, 1)
    rsi3 = np.where(down != 0, up / down * 100, 50.0)

    # 签百分比（vs白线、vs黄线）
    pct_w = (C - white) / np.maximum(white, 0.001) * 100
    pct_y = (C - yellow) / np.maximum(yellow, 0.001) * 100

    # V/MA(V,60) 60日均量比值
    v_ma60 = MA(V, 60)
    v_ratio_60 = V / np.maximum(v_ma60, 1)

    # 倍量柱检测: V > 2 * REF(V,1)
    vol_ratio = V / np.maximum(REF(V, 1), 1)
    vol_col = (vol_ratio >= 2.0).astype(float)
    vol_col_count = COUNT(vol_col, 30)

    # 上穿黄线次数（60日/90日）
    cross_up = CROSS(C, yellow)
    wave_count_60 = COUNT(cross_up, 60)
    wave_count_90 = COUNT(cross_up, 90)

    # 白线30天不死叉黄线
    white_above_yellow_30 = EVERY(white >= yellow, 30)

    # vs黄线偏离百分比
    above_yellow_pct = (C - yellow) / np.maximum(yellow, 0.001) * 100

    # 30日涨幅比
    recent_rise_30d = C / np.maximum(REF(C, 30), 0.001)

    # 30日内B1信号次数（用于方正科技双波递进检测）
    b1_count_30 = COUNT(b1_original.astype(float), 30)

    # ---- 模式匹配 ----
    (p1, p2, p3, p4, p5, p6, p7, p8, p9, p10, p11,
     strong_matched, pattern_type) = _compute_pattern_matches(
        C, white, yellow, shrink_score, J,
        rsi3, pct_w, pct_y, v_ratio_60,
        vol_col_count, wave_count_60, wave_count_90,
        white_above_yellow_30, above_yellow_pct,
        recent_rise_30d, b1_count_30)

    # 完美B1 = V4 B1 & 至少匹配一种强势模式(1-10)
    signals["b1"] = b1_original & strong_matched

    # 保存辅助字段
    signals["b1_original"] = b1_original
    signals["pattern_type"] = pattern_type
    for i, p in enumerate([p1, p2, p3, p4, p5, p6, p7, p8, p9, p10, p11], 1):
        signals[f"pattern_p{i}"] = p
    signals["J"] = J
    signals["RSI"] = rsi3
    signals["pct_w"] = pct_w
    signals["pct_y"] = pct_y
    signals["v_ratio_60"] = v_ratio_60
    signals["vol_col_count"] = vol_col_count
    signals["wave_count_60"] = wave_count_60
    signals["wave_count_90"] = wave_count_90
    signals["b1_count_30"] = b1_count_30
    signals["dist_w"] = ABS(C - white) / np.maximum(C, 0.001) * 100
    signals["dist_y"] = ABS(C - yellow) / np.maximum(yellow, 0.001) * 100

    # 不做B2涨幅排序，使用shrink_score排序（b2_sort_primary=inf）
    signals["b2_sort_primary"] = np.full(len(C), float('inf'))

    return signals


def _compute_signals(C, H, L, O, V, dates, params):
    """计算最新 bar 的完美B1 V2信号"""
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
        "RSI": float(all_bars["RSI"][i]),
        "shrink_score": float(all_bars["shrink_score"][i]),
        "pattern_type": int(all_bars["pattern_type"][i]),
        "pct_w": float(all_bars["pct_w"][i]),
        "pct_y": float(all_bars["pct_y"][i]),
        "v_ratio_60": float(all_bars["v_ratio_60"][i]),
        "vol_col_count": int(all_bars["vol_col_count"][i]),
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
    """完美B1 V2全市场扫描（不做大盘MACD过滤）"""
    from concurrent.futures import ProcessPoolExecutor, as_completed

    codes = _get_all_codes(tdxdir)
    total = len(codes)
    print(f"扫描 {total} 只A股(完美B1 V2)... (workers={max_workers or 'auto'})")

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
                      f"J={sig['J']:.1f}  RSI={sig['RSI']:.1f}  "
                      f"缩量={sig['shrink_score']:.3f}  "
                      f"模式={pname}")
            if done % 500 == 0:
                print(f"  ... 已扫描 {done}/{total} ({done/total*100:.0f}%)  "
                      f"命中 {len(results)}  耗时 {time.time()-t0:.1f}s")

    elapsed = time.time() - t0
    results.sort(key=lambda x: x["shrink_score"])

    print(f"\n{'=' * 60}")
    print(f"  完美B1 V2扫描完成: {total} 只  命中 {len(results)} 只  "
          f"错误 {errors}  耗时 {elapsed:.1f}s")
    print(f"{'=' * 60}")

    if results:
        print(f"\n  选股结果（按缩量排序）")
        print(f"{'=' * 60}")
        for r in results:
            tag = " <<< TOP" if r == results[0] else ""
            pt = r.get("pattern_type", 0)
            pname = PATTERN_NAMES.get(pt, "?")
            print(f"  {r['code']}  C={r['close']:.2f}  "
                  f"J={r['J']:.1f}  RSI={r.get('RSI',0):.1f}  "
                  f"缩量={r['shrink_score']:.3f}  "
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
    """完美B1 V2预加载（不做大盘MACD过滤）

    Returns:
        (all_signals, trading_days, None) — 第三项始终为None，兼容调用方解包
    """
    from concurrent.futures import ProcessPoolExecutor, as_completed

    codes = _get_all_codes(tdxdir)
    total = len(codes)
    print(f"预加载 {total} 只A股信号(完美B1 V2)... (workers={max_workers or 'auto'})")

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
