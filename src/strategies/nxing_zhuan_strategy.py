"""N型+砖 策略

策略逻辑：
1. N型缩量回调形态检测（近20日高点+涨幅15%-50%+量价齐升+缩量深回调15%+窄幅整理）
2. 基于金砖信号选股（砖型图、绿转强红共振、黄柱动能）
3. 无动能预过滤、无筹码密集过滤，外加流通市值 > 50亿过滤
4. 按"下大上小"排序取前2只
5. T+1 开盘买入，止损-2% / 红砖变绿砖清仓 / 2日不拉升 / 涨停清仓 / 涨幅2%卖1/4 / 脱离成本5%持仓4-6天

选股公式来源：thinking/N型砖.md
"""

import time

import numpy as np
import pandas as pd

from MyTT import REF, MA, HHV, LLV, EXIST

from config import (
    TDX_DIR, TDX_MARKET, SCAN_MAX_WORKERS,
    HUANGBAI_M1, HUANGBAI_M2, HUANGBAI_M3, HUANGBAI_M4,
    HUANGBAI_N, HUANGBAI_M, HUANGBAI_N1, HUANGBAI_N2,
    NXZH_MIN_MARKET_CAP,
    NXZH_N_LOOKBACK,
)
from src.strategies.dongneng_zhuan_strategy import (
    _compute_all_bar_signals as _dnzh_compute,
    _get_all_codes,
    _load_capital_data,
)


# ---------- helpers ----------

_process_reader = None


def _init_process(tdxdir, market):
    global _process_reader
    from mootdx.reader import Reader
    _process_reader = Reader.factory(market=market, tdxdir=tdxdir)
    from src.data.adjustment import preload_disk_cache
    preload_disk_cache()


# ================================================================== #
#  辅助函数                                                            #
# ================================================================== #


def _hhvbars(H, N):
    """HHVBARS: N周期内最高值距当前的bar数（TDX语义：并列取最近）"""
    n = len(H)
    result = np.zeros(n, dtype=int)
    for i in range(n):
        start = max(0, i - N + 1)
        window = H[start:i + 1]
        result[i] = np.argmax(window[::-1])
    return result


def _dyn_ref(arr, offsets):
    """动态REF: 对每个位置i, 返回 arr[i - int(offsets[i])]"""
    n = len(arr)
    indices = np.arange(n, dtype=int) - np.asarray(offsets, dtype=int)
    indices = np.clip(indices, 0, n - 1)
    return arr[indices]


def _every(cond, N):
    """EVERY: 最近N期cond全为True（向量化）"""
    n = len(cond)
    f = cond.astype(np.int8)
    cs = np.zeros(n + 1, dtype=int)
    cs[1:] = np.cumsum(f)
    window_len = np.minimum(np.arange(1, n + 1), N)
    starts = np.maximum(np.arange(n) - N + 1, 0)
    sums = cs[np.arange(n) + 1] - cs[starts]
    return sums == window_len


# ================================================================== #
#  N型拉升形态检测                                                      #
# ================================================================== #

def _compute_nxing_pattern(C, H, L, O, V, code):
    """N型缩量回调形态检测（向量化）

    基于通达信选股公式（N型上涨-缩量深回调+窄幅整理+市值筛选）:
    1. 近N日存在高点（距今日>=5天）
    2. 高点前10日内有起涨低点，涨幅15%-50%
    3. 上涨段量能放大（10日均量 > 前期10日均量 × 1.5）
    4. N日内无跳空涨停（排除无量一字板拉升）
    5. 高点当日未放量（量 < 上涨段最大量 × 0.9）
    6. 当前处于回调（收盘低于高点且高于起涨点）
    7. 回调缩量（5日均量 < 上涨段均量 × 0.8）
    8. 回调幅度 >= 15%
    9. 近5日窄幅整理（单日振幅 <= 10%）
    """
    N_LOOKBACK = NXZH_N_LOOKBACK
    MIN_RISE_PCT = 15
    MAX_RISE_PCT = 50
    VOL_RATIO = 1.5
    MIN_PULLBACK_PCT = 15
    MAX_AMPLITUDE_PCT = 10
    MIN_HIGH_BARS = 5

    n = len(C)
    if n < 65:
        return (np.zeros(n, dtype=bool), np.zeros(n, dtype=int),
                np.full(n, np.nan), np.full(n, np.nan))

    REFC = REF(C, 1)
    REFH = REF(H, 1)

    # ---- 1. 识别近期高点 ----
    hhv_20 = HHV(H, N_LOOKBACK)
    hhvbars_20 = _hhvbars(H, N_LOOKBACK)
    high_ok = (hhvbars_20 >= MIN_HIGH_BARS) & (hhvbars_20 < N_LOOKBACK)

    # ---- 2. 上涨起点与涨幅 ----
    llv_10 = LLV(L, 10)
    rise_low = _dyn_ref(llv_10, hhvbars_20 + 1)
    rise_pct = np.where(rise_low > 0, (hhv_20 - rise_low) / rise_low * 100, 0)
    rise_ok = (rise_pct >= MIN_RISE_PCT) & (rise_pct < MAX_RISE_PCT)

    # ---- 3. 上涨段量价齐升 ----
    vol_ma10 = MA(V, 10)
    rise_vol_ma = _dyn_ref(vol_ma10, hhvbars_20 + 5)
    pre_vol_ma = _dyn_ref(vol_ma10, hhvbars_20 + 15)
    vol_ok = rise_vol_ma > pre_vol_ma * VOL_RATIO

    # ---- 4. 非跳空拉涨 ----
    is_tech = code[:2] in ("30", "68")
    limit_pct = 1.195 if is_tech else 1.095
    gap_limit = (L > REFH) & (np.where(REFC > 0, C / REFC, 1) > limit_pct)
    no_gap_limit = ~EXIST(gap_limit, 20)

    # ---- 5. 高点未放量 ----
    hhv_vol_10 = HHV(V, 10)
    max_rise_vol = _dyn_ref(hhv_vol_10, hhvbars_20 + 1)
    high_bar_vol = _dyn_ref(V, hhvbars_20)
    high_no_vol = high_bar_vol < max_rise_vol * 0.9

    # ---- 6. 当前处于回调 ----
    in_pullback = (C < hhv_20) & (C > rise_low)

    # ---- 7. 回调缩量 ----
    vol_ma5 = MA(V, 5)
    pullback_shrink = vol_ma5 < rise_vol_ma * 0.8

    # ---- 8. 回调幅度 ----
    pullback_pct = np.where(hhv_20 > 0, (hhv_20 - C) / hhv_20 * 100, 0)
    deep_pullback = pullback_pct >= MIN_PULLBACK_PCT

    # ---- 9. 窄幅整理 ----
    amplitude = np.where(REFC > 0, (H - L) / REFC * 100, 0)
    narrow = _every(amplitude <= MAX_AMPLITUDE_PCT, 5)

    pattern = (high_ok & rise_ok & vol_ok & no_gap_limit & high_no_vol
               & in_pullback & pullback_shrink & deep_pullback & narrow)
    return pattern, hhvbars_20, hhv_20, rise_low


# ================================================================== #
#  信号计算                                                            #
# ================================================================== #

def _compute_all_bar_signals(C, H, L, O, V, dates, code, params):
    """N型砖信号：N型拉升形态 + 金砖信号 + 流通市值过滤

    复用动能砖的 _compute_all_bar_signals 计算，然后：
    - 新增 N型拉升形态过滤（近30日有拉升波段 + 当前回调）
    - 将 final_ok 设为 jinzhuan_ok & nxing_pattern & liutong_mask
    - 跳过动能预过滤(dongneng_recent)和筹码密集(chip_dense)
    """
    signals = _dnzh_compute(C, H, L, O, V, dates, code, params)
    if signals is None:
        return None

    jinzhuan_ok = signals["jinzhuan_ok"]

    # N型拉升形态过滤
    nxing_pattern, nxing_hhvbars, nxing_hhv, nxing_rise_low = _compute_nxing_pattern(C, H, L, O, V, code)
    # N型拉升形态过滤（已屏蔽）
    # nxing_pattern = np.ones(len(C), dtype=bool)

    # 存储N型图表数据
    signals["nxing_pattern"] = nxing_pattern
    signals["nxing_hhvbars"] = nxing_hhvbars
    signals["nxing_hhv"] = nxing_hhv
    signals["nxing_rise_low"] = nxing_rise_low

    # 流通市值过滤
    capital_shares = params.get("capital_shares")
    min_market_cap = params.get("min_market_cap", 0)
    n = len(C)
    if capital_shares and capital_shares > 0 and min_market_cap > 0:
        market_cap = capital_shares * C / 10000  # 万股×元/股/10000 = 亿元
        liutong_mask = market_cap > min_market_cap
    else:
        liutong_mask = np.ones(n, dtype=bool)

    nxing_ok = jinzhuan_ok & nxing_pattern & liutong_mask & signals["no_huge_vol_bearish"]

    # Override final signals
    signals["any_ok"] = nxing_ok
    pct_chg = signals["pct_change"]
    brick = signals["brick_value"]
    signals["rank_score"] = np.where(nxing_ok, brick / np.maximum(pct_chg, 0.01), 0.0)

    return signals


def _compute_signals(C, H, L, O, V, dates, code, params):
    """计算最新 bar 的信号结果"""
    signals = _compute_all_bar_signals(C, H, L, O, V, dates, code, params)
    if signals is None:
        return None
    i = len(C) - 1
    return {
        "code": code,
        "jinzhuan": bool(signals["jinzhuan_ok"][i]),
        "any": bool(signals["any_ok"][i]),
        "close": float(signals["close"][i]),
        "pct_change": float(signals["pct_change"][i]),
        "brick": float(signals["brick_value"][i]),
        "base_mom": float(signals["base_mom"][i]),
        "visual_score": float(signals["visual_score"][i]),
        "rank_score": float(signals["rank_score"][i]),
    }


# ================================================================== #
#  全市场选股扫描                                                      #
# ================================================================== #

def _scan_one(code, params):
    try:
        df = _process_reader.daily(symbol=code)
        if df is None or len(df) < 300:
            return code, None, False
        df = df.sort_index()
        from src.data.adjustment import apply_qfq
        df = apply_qfq(df, code)
        stock_params = dict(params)
        capital_data = params.get("capital_data")
        if capital_data:
            stock_params["capital_shares"] = capital_data.get(code)
        sig = _compute_signals(
            df["close"].values.astype(float),
            df["high"].values.astype(float),
            df["low"].values.astype(float),
            df["open"].values.astype(float),
            df["volume"].values.astype(float),
            df.index, code, stock_params)
        if sig is not None and sig["any"]:
            return code, sig, False
        return code, None, False
    except Exception:
        return code, None, True


def scan_all(tdxdir=TDX_DIR, market=TDX_MARKET, max_workers=SCAN_MAX_WORKERS):
    """全市场扫描N型砖选股"""
    from concurrent.futures import ProcessPoolExecutor, as_completed

    codes = _get_all_codes(tdxdir)
    total = len(codes)
    print(f"扫描 {total} 只A股... (workers={max_workers or 'auto'})")

    capital_data = _load_capital_data(tdxdir)
    if capital_data:
        print(f"  流通市值过滤: 已加载 {len(capital_data)} 只股票流通股本"
              f" (>{NXZH_MIN_MARKET_CAP}亿)")
    else:
        print(f"  流通市值过滤: base.dbf 不可用，跳过")

    params = {
        "m1": HUANGBAI_M1, "m2": HUANGBAI_M2,
        "m3": HUANGBAI_M3, "m4": HUANGBAI_M4,
        "n": HUANGBAI_N, "m": HUANGBAI_M,
        "n1": HUANGBAI_N1, "n2": HUANGBAI_N2,
        "min_market_cap": NXZH_MIN_MARKET_CAP,
        "capital_data": capital_data,
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
        futures = {pool.submit(_scan_one, code, params): code for code in codes}
        for future in as_completed(futures):
            code, sig, err = future.result()
            done += 1
            if err:
                errors += 1
            elif sig is not None:
                results.append(sig)
                print(f"  {code}  [N型砖]  C={sig['close']:.2f}  "
                      f"涨幅={sig['pct_change']:.1f}%  砖={sig['brick']:.1f}  "
                      f"排名={sig['rank_score']:.2f}")
            if done % 500 == 0:
                print(f"  ... 已扫描 {done}/{total} ({done/total*100:.0f}%)  "
                      f"命中 {len(results)}  耗时 {time.time()-t0:.1f}s")

    elapsed = time.time() - t0
    results.sort(key=lambda x: x["rank_score"], reverse=True)

    print(f"\n{'=' * 55}")
    print(f"  扫描完成: {total} 只  命中 {len(results)} 只  "
          f"错误 {errors}  耗时 {elapsed:.1f}s")
    print(f"{'=' * 55}")

    if results:
        print(f"\n  选股结果（按排名分数排序）")
        print(f"{'=' * 55}")
        for r in results:
            print(f"  {r['code']}  [N型砖]  C={r['close']:.2f}  "
                  f"涨幅={r['pct_change']:.1f}%  砖={r['brick']:.1f}  "
                  f"排名={r['rank_score']:.2f}")

    return results


# ================================================================== #
#  组合级模拟：全量每bar信号预加载                                       #
# ================================================================== #

def _scan_one_all_bars(code, params):
    """加载单只股票数据并计算全量每bar信号"""
    try:
        df = _process_reader.daily(symbol=code)
        if df is None or len(df) < 300:
            return code, None, False
        df = df.sort_index()
        from src.data.adjustment import apply_qfq
        df = apply_qfq(df, code)
        stock_params = dict(params)
        capital_data = params.get("capital_data")
        if capital_data:
            stock_params["capital_shares"] = capital_data.get(code)
        signals = _compute_all_bar_signals(
            df["close"].values.astype(float),
            df["high"].values.astype(float),
            df["low"].values.astype(float),
            df["open"].values.astype(float),
            df["volume"].values.astype(float),
            df.index, code, stock_params)
        return code, signals, False
    except Exception:
        return code, None, True


def preload_all_signals(start="2024-01-01", end="2025-12-31",
                        max_workers=SCAN_MAX_WORKERS,
                        tdxdir=TDX_DIR, market=TDX_MARKET):
    """并行预计算全部 A 股的每bar信号数据（N型砖）

    Returns:
        (all_signals, trading_days)
        - all_signals: dict[str, dict]  股票代码 -> 信号数组字典
        - trading_days: DatetimeIndex   回测区间内的交易日历
    """
    from concurrent.futures import ProcessPoolExecutor, as_completed

    codes = _get_all_codes(tdxdir)
    total = len(codes)
    print(f"预加载 {total} 只A股信号(N型砖)... (workers={max_workers or 'auto'})")

    capital_data = _load_capital_data(tdxdir)
    if capital_data:
        print(f"  流通市值过滤: 已加载 {len(capital_data)} 只股票流通股本"
              f" (>{NXZH_MIN_MARKET_CAP}亿)")
    else:
        print(f"  流通市值过滤: base.dbf 不可用，跳过")

    params = {
        "m1": HUANGBAI_M1, "m2": HUANGBAI_M2,
        "m3": HUANGBAI_M3, "m4": HUANGBAI_M4,
        "n": HUANGBAI_N, "m": HUANGBAI_M,
        "n1": HUANGBAI_N1, "n2": HUANGBAI_N2,
        "min_market_cap": NXZH_MIN_MARKET_CAP,
        "capital_data": capital_data,
    }

    all_signals = {}
    errors = 0
    done = 0
    t0 = time.time()
    all_dates = set()

    with ProcessPoolExecutor(
        max_workers=max_workers,
        initializer=_init_process,
        initargs=(tdxdir, market),
    ) as pool:
        futures = {pool.submit(_scan_one_all_bars, code, params): code for code in codes}
        for future in as_completed(futures):
            code, signals, err = future.result()
            done += 1
            if err:
                errors += 1
            elif signals is not None:
                all_signals[code] = signals
                if hasattr(signals["dates"], "to_list"):
                    all_dates.update(signals["dates"])
            if done % 500 == 0:
                print(f"  ... 已处理 {done}/{total} ({done/total*100:.0f}%)  "
                      f"有效 {len(all_signals)}  耗时 {time.time()-t0:.1f}s")

    elapsed = time.time() - t0

    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)
    sorted_dates = sorted(d for d in all_dates if start_ts <= d <= end_ts)
    trading_days = pd.DatetimeIndex(sorted_dates)

    print(f"\n  预加载完成: {len(all_signals)} 只  错误 {errors}  "
          f"交易日 {len(trading_days)}  耗时 {elapsed:.1f}s")

    if len(trading_days) > 0:
        first = trading_days[0]
        last = trading_days[-1]
        years = pd.Series(trading_days.year).value_counts().sort_index()
        year_info = "  ".join(f"{y}年:{c}天" for y, c in years.items())
        print(f"  数据范围: {first.strftime('%Y-%m-%d')} ~ {last.strftime('%Y-%m-%d')}  [{year_info}]")

    return all_signals, trading_days
