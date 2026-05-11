"""N型+砖 策略

策略逻辑：
1. N型拉升形态检测（近30日有量价齐升波段，当前处于回调阶段）
2. 基于金砖信号选股（砖型图、绿转强红共振、黄柱动能）
3. 无动能预过滤、无筹码密集过滤，外加流通市值 > 50亿过滤
4. 按"下大上小"排序取前2只
5. T+1 开盘买入，止损-2% / 红砖变绿砖清仓 / 2日不拉升 / 涨停清仓 / 涨幅2%卖1/4 / 脱离成本5%持仓4-6天

选股公式来源：thinking/N型砖.md
"""

import time

import numpy as np
import pandas as pd

from MyTT import REF, MA, HHV, EXIST

from config import (
    TDX_DIR, TDX_MARKET, SCAN_MAX_WORKERS,
    HUANGBAI_M1, HUANGBAI_M2, HUANGBAI_M3, HUANGBAI_M4,
    HUANGBAI_N, HUANGBAI_M, HUANGBAI_N1, HUANGBAI_N2,
    NXZH_MIN_MARKET_CAP,
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
#  N型拉升形态检测                                                      #
# ================================================================== #

def _compute_nxing_pattern(C, H, L, O, V, code):
    """N型拉升形态检测（向量化）

    条件：
    1. 近30日存在量价齐升的拉升波段（5日涨幅>=12% 且 成交量>5日均量×1.3）
    2. 当前处于拉升后的回调阶段（收盘价低于近20日最高价3%以上）
    3. 前期无S1/大风车（60日内无极端放量长上影：量>20日均量×3 且 上影>=3% 且 涨幅>=3%）
    4. 前期无跳空涨停（60日内无开盘跳空涨停）
    """
    n = len(C)
    if n < 65:
        return np.zeros(n, dtype=bool)

    REFC5 = REF(C, 5)
    REFC = REF(C, 1)

    # ---- 1. 拉升波段检测 ----
    pct_5d = np.where(REFC5 > 0, (C - REFC5) / REFC5 * 100, 0)
    vol_ma5 = MA(V, 5)
    vol_expand = V > vol_ma5 * 1.3
    rally = (pct_5d >= 12) & vol_expand
    rally_recent = EXIST(rally, 30)

    # ---- 2. 回调检测 ----
    hhv_20 = HHV(H, 20)
    pullback = np.where(hhv_20 > 0, (hhv_20 - C) / hhv_20 * 100, 0) >= 3

    # ---- 3. S1/大风车排除 ----
    # 极端放量长上影阳线（出货信号）：量>20日均量×3 且 上影>=3% 且 涨幅>=3%
    pct_chg = np.where(REFC > 0, (C - REFC) / REFC * 100, 0)
    upper_shadow_pct = (H - np.maximum(O, C)) / np.where(L > 0, L, 0.001) * 100
    vol_ma20 = MA(V, 20)
    s1_signal = (V > vol_ma20 * 3) & (upper_shadow_pct >= 3) & (pct_chg >= 3)
    no_s1 = ~EXIST(s1_signal, 60)

    # ---- 4. 跳空涨停排除 ----
    # 开盘>昨收×1.02（跳空）且 收盘>=昨收×涨停比例×0.995
    is_tech = code[:2] in ("30", "68")
    limit_pct = 1.195 if is_tech else 1.095
    gap_limit = (O > REFC * 1.02) & (C >= REFC * limit_pct)
    no_gap_limit = ~EXIST(gap_limit, 60)

    return rally_recent & pullback & no_s1 & no_gap_limit


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
    nxing_pattern = _compute_nxing_pattern(C, H, L, O, V, code)

    # 流通市值过滤
    capital_shares = params.get("capital_shares")
    min_market_cap = params.get("min_market_cap", 0)
    n = len(C)
    if capital_shares and capital_shares > 0 and min_market_cap > 0:
        market_cap = capital_shares * C / 10000  # 万股×元/股/10000 = 亿元
        liutong_mask = market_cap > min_market_cap
    else:
        liutong_mask = np.ones(n, dtype=bool)

    nxing_ok = jinzhuan_ok & nxing_pattern & liutong_mask

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
