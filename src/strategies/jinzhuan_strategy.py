"""金砖选股策略

策略逻辑：
1. 基于金砖共振信号选股（砖型图、绿转强红、黄柱动能、上影线、趋势条件）
2. 剔除60日内有S1/大风车信号（极端放量长上影出货形态）的股票
3. 剔除60日内有跳空涨停的股票
4. 前一个交易日相对缩量
5. 流通市值 > 50亿
6. 按"下大上小"排序取前2只
7. T+1 分钟线买入，止损-2% / 红砖变绿砖清仓 / 2日不拉升 / 涨停清仓 / 涨幅2%卖1/4 / 脱离成本5%持仓4-6天

选股公式来源：thinking/砖.md
"""

import time

import numpy as np
import pandas as pd

from MyTT import REF, MA, HHV, LLV, EXIST

from config import (
    TDX_DIR, TDX_MARKET, SCAN_MAX_WORKERS,
    HUANGBAI_M1, HUANGBAI_M2, HUANGBAI_M3, HUANGBAI_M4,
    HUANGBAI_N, HUANGBAI_M, HUANGBAI_N1, HUANGBAI_N2,
    JZH_MIN_MARKET_CAP, JZH_S1_PERIOD, JZH_GAP_LIMIT_PERIOD,
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
#  S1/大风车 + 跳空涨停 检测                                          #
# ================================================================== #


def _compute_s1_dafengche(C, H, L, O, V):
    """S1/大风车信号检测（极端放量长上影出货形态）

    S1: 放量(>2倍均量) + 长上影(>40%) + 收盘下跌
    大风车: 极端放量(>3倍均量) + 长上影(>50%) + 阴线
    """
    REFC = REF(C, 1)

    body_high = np.maximum(O, C)
    day_range = np.maximum(H - L, 0.001)
    upper_shadow_ratio = (H - body_high) / day_range

    vol_ma20 = MA(V, 20)
    vol_ratio = V / np.maximum(vol_ma20, 1)

    s1 = (vol_ratio > 2.0) & (upper_shadow_ratio > 0.4) & (C < REFC)
    dafengche = (vol_ratio > 3.0) & (upper_shadow_ratio > 0.5) & (C < O)

    return s1 | dafengche


def _compute_gap_limit_up(C, H, L, O, code):
    """跳空涨停检测（最低价跳空高开 + 涨停）"""
    REFC = REF(C, 1)
    REFH = REF(H, 1)

    is_tech = code[:2] in ("30", "68")
    limit_pct = 1.195 if is_tech else 1.095

    gap_up = L > REFH
    limit_up = np.where(REFC > 0, C / REFC, 1) > limit_pct

    return gap_up & limit_up


# ================================================================== #
#  信号计算                                                            #
# ================================================================== #

def _compute_all_bar_signals(C, H, L, O, V, dates, code, params):
    """金砖信号：金砖共振 + S1/大风车排除 + 跳空涨停排除 + 前一日缩量 + 流通市值过滤"""
    signals = _dnzh_compute(C, H, L, O, V, dates, code, params)
    if signals is None:
        return None

    jinzhuan_ok = signals["jinzhuan_ok"]
    n = len(C)

    # S1/大风车排除
    s1_signal = _compute_s1_dafengche(C, H, L, O, V)
    no_s1_recent = ~EXIST(s1_signal, params.get("s1_period", 60))

    # 跳空涨停排除
    gap_limit = _compute_gap_limit_up(C, H, L, O, code)
    no_gap_recent = ~EXIST(gap_limit, params.get("gap_limit_period", 60))

    # 前一日缩量
    hhv_v20 = HHV(V, 20)
    hhv_v50 = HHV(V, 50)
    prev_v = REF(V, 1)
    prev_hhv20 = REF(hhv_v20, 1)
    prev_hhv50 = REF(hhv_v50, 1)
    prev_shrink = (prev_v < prev_hhv20 * 0.618) | (prev_v < prev_hhv50 / 3)

    # 流通市值过滤
    capital_shares = params.get("capital_shares")
    min_market_cap = params.get("min_market_cap", 0)
    if capital_shares and capital_shares > 0 and min_market_cap > 0:
        market_cap = capital_shares * C / 10000
        liutong_mask = market_cap > min_market_cap
    else:
        liutong_mask = np.ones(n, dtype=bool)

    final_ok = jinzhuan_ok & no_s1_recent & no_gap_recent & prev_shrink & liutong_mask

    # 突然放巨量阴线检测
    _hvb_vr = V / np.maximum(REF(V, 1), 1)
    _hvb_body = np.where(O > 0, (O - C) / O, 0)
    huge_vol_bearish = (_hvb_vr > 3) & (V > MA(V, 20) * 3) & (C < O) & (_hvb_body > 0.03)
    no_huge_vol_bearish = ~EXIST(huge_vol_bearish, 60)
    final_ok = final_ok & no_huge_vol_bearish

    signals["any_ok"] = final_ok
    pct_chg = signals["pct_change"]
    brick = signals["brick_value"]
    signals["rank_score"] = np.where(final_ok, brick / np.maximum(pct_chg, 0.01), 0.0)
    signals["huge_vol_bearish"] = huge_vol_bearish
    signals["no_huge_vol_bearish"] = no_huge_vol_bearish

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
    """全市场扫描金砖选股"""
    from concurrent.futures import ProcessPoolExecutor, as_completed

    codes = _get_all_codes(tdxdir)
    total = len(codes)
    print(f"扫描 {total} 只A股... (workers={max_workers or 'auto'})")

    capital_data = _load_capital_data(tdxdir)
    if capital_data:
        print(f"  流通市值过滤: 已加载 {len(capital_data)} 只股票流通股本"
              f" (>{JZH_MIN_MARKET_CAP}亿)")
    else:
        print(f"  流通市值过滤: base.dbf 不可用，跳过")

    params = {
        "m1": HUANGBAI_M1, "m2": HUANGBAI_M2,
        "m3": HUANGBAI_M3, "m4": HUANGBAI_M4,
        "n": HUANGBAI_N, "m": HUANGBAI_M,
        "n1": HUANGBAI_N1, "n2": HUANGBAI_N2,
        "min_market_cap": JZH_MIN_MARKET_CAP,
        "capital_data": capital_data,
        "s1_period": JZH_S1_PERIOD,
        "gap_limit_period": JZH_GAP_LIMIT_PERIOD,
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
                print(f"  {code}  [金砖]  C={sig['close']:.2f}  "
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
            print(f"  {r['code']}  [金砖]  C={r['close']:.2f}  "
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
    """并行预计算全部 A 股的每bar信号数据（金砖）

    Returns:
        (all_signals, trading_days)
    """
    from concurrent.futures import ProcessPoolExecutor, as_completed

    codes = _get_all_codes(tdxdir)
    total = len(codes)
    print(f"预加载 {total} 只A股信号(金砖)... (workers={max_workers or 'auto'})")

    capital_data = _load_capital_data(tdxdir)
    if capital_data:
        print(f"  流通市值过滤: 已加载 {len(capital_data)} 只股票流通股本"
              f" (>{JZH_MIN_MARKET_CAP}亿)")
    else:
        print(f"  流通市值过滤: base.dbf 不可用，跳过")

    params = {
        "m1": HUANGBAI_M1, "m2": HUANGBAI_M2,
        "m3": HUANGBAI_M3, "m4": HUANGBAI_M4,
        "n": HUANGBAI_N, "m": HUANGBAI_M,
        "n1": HUANGBAI_N1, "n2": HUANGBAI_N2,
        "min_market_cap": JZH_MIN_MARKET_CAP,
        "capital_data": capital_data,
        "s1_period": JZH_S1_PERIOD,
        "gap_limit_period": JZH_GAP_LIMIT_PERIOD,
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
