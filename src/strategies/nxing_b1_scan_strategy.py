"""N型B1选股策略

基于N型结构筛选出符合B1条件的股票（纯选股，不做买卖操作）

筛选条件：
1. 60日内至少出现两次B1信号，两次B1信号间隔超过30天
2. 每次B1信号价格比前一次高（N型低点抬高结构）
3. 股票流通市值50亿以上
4. 前期放量上涨支撑（排除缩量上涨、放量下跌、阶梯出货、长上影线、S1/大风车）
5. 统计选股后T+3涨幅超过10%的概率
"""

import os
import re
import shutil
import time
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore", category=RuntimeWarning)
from MyTT import EMA, MA, SMA, HHV, LLV, REF, COUNT, EVERY, EXIST, \
    CROSS, MAX, ABS, BARSLAST, HHVBARS
from mootdx.reader import Reader

from config import (
    TDX_DIR, TDX_MARKET, SCAN_MAX_WORKERS, STOCK_TYPE,
    HUANGBAI_M1, HUANGBAI_M2, HUANGBAI_M3, HUANGBAI_M4,
    HUANGBAI_N, HUANGBAI_M, HUANGBAI_N1, HUANGBAI_N2,
    HUANGBAI_VOL_EXPAND_PERIOD, HUANGBAI_VOL_EXPAND_MIN,
    HUANGBAI_SURGE_PRICE_PCT, HUANGBAI_SURGE_VOL_RATIO,
    HUANGBAI_S1_PERIOD,
    HUANGBAI_S1_HIGH_PERIOD, HUANGBAI_S1_HIGH_RATIO,
    HUANGBAI_S1_ACCEL_PCT, HUANGBAI_S1_ACCEL_LOOKBACK,
    HUANGBAI_STEPPED_DROP_PCT, HUANGBAI_STEPPED_DROP_LOOKBACK,
    DNZH_MIN_MARKET_CAP,
    CHART_OUTPUT_DIR,
)

from src.strategies.dongneng_zhuan_strategy import _load_capital_data

# ---------- N型B1参数 ----------
NX_B1_LOOKBACK = 60        # 60日内寻找B1信号
NX_B1_MIN_COUNT = 2        # 至少2次B1信号
NX_B1_MIN_GAP = 30         # 两次B1间隔最少30个交易日
NX_T3_DAYS = 3             # T+3统计天数
NX_T3_TARGET_PCT = 10.0    # T+3涨幅目标(%)


# ---------- 复用 V4 的 helper ----------

def _weekly_ma(daily_close, dates, period):
    s = pd.Series(daily_close, index=pd.to_datetime(dates))
    weekly = s.resample('W-FRI').last().dropna()
    wma = weekly.rolling(period).mean()
    return wma.reindex(s.index, method='ffill').values


def _ref_at(S, offsets):
    S = np.asarray(S, dtype=float)
    offsets = np.asarray(offsets, dtype=float)
    result = np.full(len(S), np.nan)
    for i in range(len(S)):
        off = offsets[i]
        if np.isnan(off):
            continue
        idx = i - int(off)
        if 0 <= idx < len(S):
            result[i] = S[idx]
    return result


# ================================================================== #
#  全bar B1 + 过滤计算（复用V4 B1七子条件 + vol_expand_ok过滤链）      #
# ================================================================== #

def _compute_all_bar_b1_and_filters(C, H, L, O, V, dates, params):
    """计算全部bar的B1信号数组和过滤条件

    Returns:
        dict with keys: b1(bool array), vol_expand_ok(bool array),
        white, yellow, bbi, shrink_score, J, rsi
        或 None（数据不足）
    """
    n = len(C)
    if n < 300:
        return None

    LC = REF(C, 1)
    white = EMA(EMA(C, 10), 10)
    yellow = (MA(C, params["m1"]) + MA(C, params["m2"])
              + MA(C, params["m3"]) + MA(C, params["m4"])) / 4
    bbi = (MA(C, 3) + MA(C, 6) + MA(C, 12) + MA(C, 24)) / 4
    rsi = SMA(MAX(C - LC, 0), 3, 1) / SMA(ABS(C - LC), 3, 1) * 100

    llv9, hhv9 = LLV(L, 9), HHV(H, 9)
    denom9 = hhv9 - llv9
    rsv = np.where(denom9 != 0, (C - llv9) / denom9 * 100, 50.0)
    K = SMA(rsv, 3, 1)
    D = SMA(K, 3, 1)
    J = 3 * K - 2 * D

    s_denom = HHV(C, params["n1"]) - LLV(L, params["n1"])
    SHORT = np.where(s_denom != 0, 100 * (C - LLV(L, params["n1"])) / s_denom, 50.0)
    l_denom = HHV(C, params["n2"]) - LLV(L, params["n2"])
    LONG = np.where(l_denom != 0, 100 * (C - LLV(L, params["n2"])) / l_denom, 50.0)

    # 振幅/异动
    is_tech = params["stock_type"] == "tech"
    pct_change = np.where(LC > 0, C / LC - 1, 0.0)
    volatile = EXIST(pct_change > 0.15, 200)
    is_volatile = volatile | is_tech
    amp_range = np.where(is_volatile, 8.0, 5.0)
    relax = np.where(is_volatile, 0.9, 1.0)

    daily_amp = (H - L) / L * 100
    daily_pct = ABS(C - LC) / LC * 100 * relax
    up_doji = (C > LC) & (ABS(C - O) / O * 100 * relax < 1.8)

    needle_20 = ((SHORT <= 20) & (LONG >= 75)) | ((LONG - SHORT) >= 70)
    treasure = (COUNT(LONG >= 75, 8) >= 6) & (COUNT(SHORT <= 70, 7) >= 4) & (COUNT(SHORT <= 50, 8) >= 1)
    dbl_fork = EVERY(LONG >= 75, 8) & (COUNT(SHORT <= 50, 6) >= 2) & (COUNT(SHORT <= 20, 7) >= 1)
    red_green = (COUNT(C >= O, 15) > 7) | (COUNT(C > REF(C, 1), 11) > 5)

    near_amp = (HHV(H, params["n"]) - LLV(L, params["n"])) / LLV(L, params["n"]) * 100
    far_amp = (HHV(H, params["m"]) - LLV(L, params["m"])) / LLV(L, params["m"]) * 100
    near_ano = (near_amp >= 15) | ((HHV(H, 12) - LLV(L, 14)) / LLV(L, 14) * 100 >= 11)
    far_ano = far_amp >= 30
    super_ano = near_amp >= 60
    wash_ano = (COUNT(needle_20, 10) >= 2) | treasure | dbl_fork
    anomaly = near_ano | far_ano | wash_ano

    vday = HHVBARS(V, 40)
    c_vd = _ref_at(C, vday)
    c_vd1 = _ref_at(C, vday + 1)
    o_vd = _ref_at(O, vday)
    not_big_green = np.where(np.isnan(c_vd), True,
                             (c_vd >= c_vd1) | (c_vd >= o_vd))
    big_green = ~not_big_green
    big_green_far = (vday >= 15) & big_green
    ok_green = not_big_green | big_green_far

    hhv_v20 = HHV(V, 20)
    hhv_v50 = HHV(V, 50)
    shrink = (V < hhv_v20 * 0.416) | (V < hhv_v50 / 3)
    pb_shrink = (V < hhv_v20 * 0.45) | (V < hhv_v50 / 3)
    mod_shrink = (V < hhv_v20 * 0.618) | (V < hhv_v50 / 3)
    sup_shrink = (V < HHV(V, 30) / 4) | (V < hhv_v50 / 6)
    shrink_score = np.where(hhv_v20 > 0, V / hhv_v20, 1.0)

    uptrend = ((white >= yellow * 0.999)
               & ((C >= yellow) | ((C > yellow * 0.975) & (C > O))))

    strong_trend = (EVERY(yellow >= REF(yellow, 1) * 0.999, 13)
                    & (white >= REF(white, 1))
                    & EVERY(white > yellow, 20)
                    & EVERY(white >= REF(white, 1), 11)
                    & red_green)

    cross_c_y = CROSS(C, yellow)
    bars_cross_cy = BARSLAST(cross_c_y)
    super_bull = ((EVERY(bbi >= REF(bbi, 1) * 0.999, 20)
                   | (COUNT(bbi >= REF(bbi, 1), 25) >= 23))
                  & ((near_amp >= 30) | (far_amp > 80))
                  & (bars_cross_cy > 12))

    dist_w = ABS(C - white) / C * 100
    dist_wL = ABS(L - white) / white * 100
    dist_bbi = ABS(C - bbi) / C * 100
    dist_bbiL = ABS(L - bbi) / bbi * 100
    dist_y = ABS(C - yellow) / yellow * 100

    pb_white = ((C >= white) & (dist_w <= 2)) \
        | ((C < white) & (dist_w < 0.8)) \
        | ((C >= bbi) & (dist_bbi < 2.5) & (dist_bbiL < 1)
           & (dist_w <= 3) & (daily_pct < 1) & (C > LC))

    white_sup = (C >= white) & (dist_w < 1.5)
    strong_pb_hold = ((dist_wL < 1) | (dist_bbiL < 0.5)) & (C > white) & (dist_w <= 3.5)
    pb_yellow = ((C >= yellow) & ((dist_y <= 1.5) | ((dist_y <= 2) & (daily_pct < 1)))) \
        | ((C < yellow) & (dist_y <= 0.8))

    rsi_j = rsi + J

    # B1 七子条件
    b_oversold_turn = (uptrend
                       & (rsi - 15 >= REF(rsi, 1))
                       & ((REF(rsi, 1) < 20) | (REF(J, 1) < 14))
                       & (daily_amp < amp_range + 0.5)
                       & ((daily_pct < 2.3) | (up_doji & (daily_pct < 4)))
                       & ok_green & anomaly & (C >= yellow))

    b_oversold_shrink = (uptrend
                         & ((J < 14) | (rsi < 23))
                         & ((rsi_j < 55) | (J == LLV(J, 20)))
                         & (daily_amp < amp_range)
                         & ((daily_pct < 2.5) | up_doji)
                         & ok_green
                         & (shrink | (mod_shrink & (daily_pct < 1)))
                         & anomaly)

    b_raw = ((white > yellow)
             & (C >= yellow * 0.99)
             & (yellow >= REF(yellow, 1))
             & ((J < 13) | (rsi < 21))
             & (rsi_j < LLV(rsi_j, 15) * 1.5)
             & mod_shrink & ok_green
             & ((ABS(C - O) * 100 / O < 1.5)
                | (sup_shrink | (mod_shrink & (V < LLV(V, 20) * 1.1) & (J == LLV(J, 20))))
                | (mod_shrink & ((dist_w < 1.8) | (dist_bbi < 1.5) | (dist_y < 2.8))))
             & anomaly)

    b_oversold_super = (uptrend
                        & ((J < 14) | (rsi < 23))
                        & (rsi_j < 60) & (far_amp >= 45)
                        & ((daily_amp < amp_range)
                           | (super_ano & (daily_amp < amp_range + 3.2) & (C > O) & (C > white)))
                        & (((C < O) & (V < REF(V, 1)) & (C >= yellow)) | (C >= O))
                        & ((daily_pct < 2) | up_doji)
                        & ok_green & sup_shrink & anomaly)

    b_pb_white = (strong_trend
                  & ((J < 30) | (rsi < 40) | wash_ano)
                  & (rsi_j < 70)
                  & ((daily_amp < amp_range + 0.5) | (dist_w < 1) | (dist_bbi < 1))
                  & pb_white
                  & ((daily_pct < 2) | ((daily_pct < 5) & white_sup))
                  & ok_green & pb_shrink & anomaly & (L <= LC))

    b_pb_super = (super_bull
                  & ((J < 35) | (rsi < 45) | wash_ano)
                  & (rsi_j < 80) & (rsi_j == LLV(rsi_j, 25))
                  & (daily_amp < amp_range + 1)
                  & ((daily_pct < 2.5) | (dist_w < 2))
                  & strong_pb_hold & ok_green & anomaly & mod_shrink)

    b_pb_yellow = ((white >= yellow)
                   & (C >= yellow * 0.975)
                   & ((J < 13) | (rsi < 18))
                   & pb_yellow & ok_green
                   & (shrink | (mod_shrink & ((J == LLV(J, 20)) | (rsi == LLV(rsi, 14)))))
                   & (yellow >= REF(yellow, 1) * 0.997)
                   & (MA(C, 60) >= REF(MA(C, 60), 1))
                   & (near_amp >= 11.9) & (far_amp >= 19.5))

    b1 = (b_oversold_turn | b_oversold_shrink | b_raw
          | b_oversold_super | b_pb_white | b_pb_super | b_pb_yellow)

    # vol_expand_ok 过滤链
    vol_expand = (V > REF(V, 1) * 1.8) & (C > O) & (C > LC)
    _vep, _vem = HUANGBAI_VOL_EXPAND_PERIOD, HUANGBAI_VOL_EXPAND_MIN
    has_vol_expand = COUNT(vol_expand, _vep) >= _vem
    _ref_c = REF(C, _vep)
    _price_rise = np.where(np.abs(_ref_c) > 0.001,
                           (C - _ref_c) / np.abs(_ref_c) * 100, 0)
    _vol_ratio = MA(V, _vep) / np.maximum(MA(V, 60), 1)
    no_shrinkage_surge = ~((_price_rise > HUANGBAI_SURGE_PRICE_PCT)
                           & (_vol_ratio < HUANGBAI_SURGE_VOL_RATIO))
    _lp = 1.20 if params.get("stock_type") == "tech" else 1.10
    _limit_up = C >= np.round(REF(C, 1) * _lp, 2)
    _limit_shrink = _limit_up & (V < REF(V, 1))
    no_consec_limit_shrink = COUNT(_limit_shrink.astype(float), _vep) < 1
    _rise_v = np.where(C > REF(C, 1), V, 0)
    _decline_v = np.where(C < REF(C, 1), V, 0)
    _rvs = pd.Series(_rise_v).rolling(_vep, min_periods=1).sum().values
    _dvs = pd.Series(_decline_v).rolling(_vep, min_periods=1).sum().values
    no_heavy_decline = ~(_dvs > _rvs)

    _s1p = HUANGBAI_S1_PERIOD
    _accel_raw = (C - REF(C, 5)) / np.maximum(REF(C, 5), 0.001) * 100 > HUANGBAI_S1_ACCEL_PCT
    _accel = EXIST(_accel_raw, HUANGBAI_S1_ACCEL_LOOKBACK)
    _recent_limit = EXIST(_limit_up, 3)
    _big_vol = (V > HHV(V, 20) * 2) | (V > MA(V, 60) * 3) | (_recent_limit & (V > REF(V, 1) * 1.5))
    _big_yin = (C < O) & ((O - C) / np.maximum(REF(C, 1), 0.001) * 100 > 3)
    _at_high = C >= HHV(C, HUANGBAI_S1_HIGH_PERIOD) * HUANGBAI_S1_HIGH_RATIO
    _s1 = _accel & _big_vol & _big_yin & _at_high

    _upper_shadow = H - np.maximum(O, C)
    _lower_shadow = np.minimum(O, C) - L
    _body = ABS(C - O)
    _long_shadow_yin = (C < O) & ((_upper_shadow + _lower_shadow) > _body * 2)
    _hist_vol = V == HHV(V, 120)
    _dafengche = _accel & _hist_vol & _long_shadow_yin & (V > REF(V, 1))

    _near_high = C >= HHV(C, 20) * 0.97
    _upper_pct = _upper_shadow / np.maximum(C, 0.001) * 100
    _long_upper_shadow = _near_high & (_upper_pct > 3) & (_upper_shadow > _body * 2) & (V > REF(V, 1) * 1.3)

    _from_high = (C - HHV(H, HUANGBAI_STEPPED_DROP_LOOKBACK)) / HHV(H, HUANGBAI_STEPPED_DROP_LOOKBACK) * 100
    _stepped_selloff = _accel & (_from_high < HUANGBAI_STEPPED_DROP_PCT)

    no_s1_dafengche = ~EXIST(_s1 | _dafengche | _long_upper_shadow | _stepped_selloff, _s1p)
    vol_expand_ok = (has_vol_expand & no_shrinkage_surge
                     & no_consec_limit_shrink & no_heavy_decline
                     & no_s1_dafengche)

    return {
        "b1": b1,
        "vol_expand_ok": vol_expand_ok,
        "white": white,
        "yellow": yellow,
        "bbi": bbi,
        "shrink_score": shrink_score,
        "J": J,
        "rsi": rsi,
    }


# ================================================================== #
#  N型结构检测 + T+3统计                                               #
# ================================================================== #

def _find_nx_b1_pattern(b1, C, dates, lookback=NX_B1_LOOKBACK,
                        min_count=NX_B1_MIN_COUNT, min_gap=NX_B1_MIN_GAP):
    """在最近lookback天内寻找N型B1结构

    N型条件：
    1. >=min_count 次B1信号
    2. 任意相邻两次间隔 >= min_gap 天
    3. 每次B1价格 > 前一次B1价格（低点抬高）

    Returns:
        list[dict] - B1信号列表 [{"idx": int, "date": str, "price": float}]
        或 None（不符合N型）
    """
    n = len(b1)
    start = max(0, n - lookback)
    recent_b1 = b1[start:]
    indices = np.where(recent_b1)[0] + start

    if len(indices) < min_count:
        return None

    b1_list = []
    for idx in indices:
        b1_list.append({
            "idx": int(idx),
            "date": str(dates[idx])[:10],
            "price": float(C[idx]),
        })

    # 找到满足N型条件的B1组合：
    # 从最新B1向前搜索，找到间隔>min_gap且价格递增的序列
    # 简化：检查所有相邻B1对，找到最近的满足条件的对
    for i in range(len(b1_list) - 1, 0, -1):
        for j in range(i - 1, -1, -1):
            gap = b1_list[i]["idx"] - b1_list[j]["idx"]
            if gap < min_gap:
                continue
            # 检查从j到i之间所有B1价格是否递增
            sub = b1_list[j:i + 1]
            prices = [b["price"] for b in sub]
            if all(prices[k] < prices[k + 1] for k in range(len(prices) - 1)):
                return sub
    return None


def _compute_t3_stats(C, b1_list, t3_days=NX_T3_DAYS, target_pct=NX_T3_TARGET_PCT):
    """计算每个B1信号之后T+3涨幅

    Returns:
        list[dict] - 每个B1的T+3结果
    """
    results = []
    for b in b1_list:
        idx = b["idx"]
        buy_price = b["price"]
        target_idx = min(idx + t3_days, len(C) - 1)
        t3_price = float(C[target_idx])
        t3_pct = (t3_price - buy_price) / buy_price * 100
        results.append({
            "date": b["date"],
            "buy_price": buy_price,
            "t3_price": t3_price,
            "t3_pct": round(t3_pct, 2),
            "hit": t3_pct >= target_pct,
        })
    return results


# ================================================================== #
#  全市场选股扫描                                                       #
# ================================================================== #

def _get_all_codes(tdxdir=TDX_DIR):
    codes = set()
    for prefix in ("sz", "sh"):
        path = os.path.join(tdxdir, "vipdoc", prefix, "lday")
        if not os.path.isdir(path):
            continue
        for f in os.listdir(path):
            m = re.match(r"[a-z]{2}(\d{6})\.day", f)
            if not m:
                continue
            code = m.group(1)
            if prefix == "sz" and code[:3] in ("000", "001", "002", "003", "300", "301"):
                codes.add(code)
            elif prefix == "sh" and code[:3] in ("600", "601", "603", "605", "688", "689"):
                codes.add(code)
    return sorted(codes)


_process_reader = None
_process_capital = None


def _init_process(tdxdir, market, capital_data):
    global _process_reader, _process_capital
    _process_reader = Reader.factory(market=market, tdxdir=tdxdir)
    _process_capital = capital_data
    from src.data.adjustment import preload_disk_cache
    preload_disk_cache()


def _scan_one_stock(code, params):
    """扫描单只股票：计算全bar B1 + N型结构 + T+3"""
    assert _process_reader is not None, "_process_reader 未初始化"
    try:
        df = _process_reader.daily(symbol=code)
        if df is None or len(df) < 300:
            return code, None
        df = df.sort_index()
        from src.data.adjustment import apply_qfq
        df = apply_qfq(df, code)

        C = df["close"].values.astype(float)
        H = df["high"].values.astype(float)
        L = df["low"].values.astype(float)
        O = df["open"].values.astype(float)
        V = df["vol"].values.astype(float) if "vol" in df.columns else df["volume"].values.astype(float)
        dates = df.index

        # 流通市值过滤
        capital_shares = _process_capital.get(code, 0) if _process_capital else 0
        if capital_shares > 0:
            latest_cap = capital_shares * C[-1] / 10000  # 亿元
            if latest_cap < DNZH_MIN_MARKET_CAP:
                return code, None
        else:
            return code, None

        # 全bar B1 + 过滤计算
        result = _compute_all_bar_b1_and_filters(C, H, L, O, V, dates, params)
        if result is None:
            return code, None

        b1 = result["b1"]
        vol_expand_ok = result["vol_expand_ok"]
        n = len(b1)

        # 最新bar的vol_expand_ok检查
        if not vol_expand_ok[n - 1]:
            return code, None

        # N型结构检测
        nx_b1 = _find_nx_b1_pattern(b1, C, dates)
        if nx_b1 is None:
            return code, None

        # T+3统计（对所有B1信号点计算，不仅限于N型中的）
        all_b1_indices = np.where(b1[max(0, n - NX_B1_LOOKBACK):])[0] + max(0, n - NX_B1_LOOKBACK)
        all_b1_list = [{"idx": int(i), "date": str(dates[i])[:10], "price": float(C[i])}
                       for i in all_b1_indices]
        t3_stats = _compute_t3_stats(C, all_b1_list)
        hit_count = sum(1 for t in t3_stats if t["hit"])
        hit_rate = hit_count / len(t3_stats) * 100 if t3_stats else 0

        return code, {
            "code": code,
            "close": float(C[-1]),
            "market_cap": round(latest_cap, 1),
            "shrink_score": float(result["shrink_score"][-1]),
            "J": float(result["J"][-1]),
            "RSI": float(result["rsi"][-1]),
            "nx_b1_count": len(nx_b1),
            "nx_b1_list": nx_b1,
            "all_b1_count": len(all_b1_list),
            "t3_stats": t3_stats,
            "t3_hit_rate": round(hit_rate, 1),
            # 图表数据
            "chart_data": {
                "close": C,
                "high": H,
                "low": L,
                "open": O,
                "volume": V,
                "dates": dates,
                "white": result["white"],
                "yellow": result["yellow"],
                "bbi": result["bbi"],
                "b1": b1,
            },
        }
    except Exception as e:
        return code, {"error": str(e)}


def scan_all(stock_type="main", tdxdir=TDX_DIR, market=TDX_MARKET,
             max_workers=SCAN_MAX_WORKERS):
    """N型B1全市场扫描"""
    from concurrent.futures import ProcessPoolExecutor, as_completed

    # 加载流通市值数据
    print("  加载流通市值数据...")
    capital_data = _load_capital_data(tdxdir)
    if capital_data is None:
        print("  警告: 无法加载流通市值数据，将跳过市值过滤")
    else:
        print(f"  已加载 {len(capital_data)} 只股票的流通市值")

    codes = _get_all_codes(tdxdir)
    total = len(codes)
    print(f"  扫描 {total} 只A股... (workers={max_workers or 'auto'})")

    params = {
        "m1": HUANGBAI_M1, "m2": HUANGBAI_M2, "m3": HUANGBAI_M3, "m4": HUANGBAI_M4,
        "n": HUANGBAI_N, "m": HUANGBAI_M, "n1": HUANGBAI_N1, "n2": HUANGBAI_N2,
        "stock_type": stock_type,
    }

    results = []
    errors = 0
    done = 0
    t0 = time.time()

    with ProcessPoolExecutor(
        max_workers=max_workers,
        initializer=_init_process,
        initargs=(tdxdir, market, capital_data),
    ) as pool:
        futures = {
            pool.submit(_scan_one_stock, code, params): code
            for code in codes
        }
        for future in as_completed(futures):
            code, sig = future.result()
            done += 1
            if sig is None:
                pass
            elif "error" in sig:
                errors += 1
            else:
                results.append(sig)
                nx_prices = " -> ".join(f"{b['price']:.2f}" for b in sig["nx_b1_list"])
                print(f"  {code}  市值={sig['market_cap']:.0f}亿  "
                      f"N型B1({sig['nx_b1_count']}次) 价格链={nx_prices}  "
                      f"T3胜率={sig['t3_hit_rate']:.0f}%")
            if done % 500 == 0:
                print(f"  ... 已扫描 {done}/{total} ({done/total*100:.0f}%)  "
                      f"命中 {len(results)}  耗时 {time.time()-t0:.1f}s")

    elapsed = time.time() - t0

    # 按缩量排序
    results.sort(key=lambda x: x["shrink_score"])

    print(f"\n{'=' * 65}")
    print(f"  N型B1扫描完成: {total} 只  命中 {len(results)} 只  "
          f"错误 {errors}  耗时 {elapsed:.1f}s")
    print(f"{'=' * 65}")

    if results:
        _print_results(results)
        _generate_charts(results)

    return results


def _print_results(results):
    """打印选股结果"""
    print(f"\n  N型B1选股结果（按缩量排序）")
    print(f"{'=' * 65}")
    for r in results:
        nx_prices = " -> ".join(f"{b['price']:.2f}" for b in r["nx_b1_list"])
        print(f"  {r['code']}  C={r['close']:.2f}  市值={r['market_cap']:.0f}亿  "
              f"缩量={r['shrink_score']:.3f}  J={r['J']:.1f}  RSI={r['RSI']:.1f}")
        print(f"    N型B1({r['nx_b1_count']}次): {nx_prices}")
        print(f"    T+3胜率: {r['t3_hit_rate']:.0f}% ({r['all_b1_count']}个B1信号)")
        for t in r["t3_stats"]:
            mark = "V" if t["hit"] else "X"
            print(f"      [{mark}] {t['date']} 买入={t['buy_price']:.2f} "
                  f"T+3={t['t3_price']:.2f}  涨幅={t['t3_pct']:+.2f}%")

    # 汇总T+3统计
    all_t3 = []
    for r in results:
        all_t3.extend(r["t3_stats"])
    if all_t3:
        total = len(all_t3)
        hits = sum(1 for t in all_t3 if t["hit"])
        avg_pct = sum(t["t3_pct"] for t in all_t3) / total
        print(f"\n{'=' * 65}")
        print(f"  T+3汇总: {total}个B1信号  "
              f"涨幅>={NX_T3_TARGET_PCT}%: {hits}/{total} ({hits/total*100:.1f}%)  "
              f"平均涨幅={avg_pct:+.2f}%")
        print(f"{'=' * 65}")


def _generate_charts(results):
    """为选中的股票生成K线图（含B1信号标记）"""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    from matplotlib.patches import FancyBboxPatch

    # 清空charts目录
    if os.path.exists(CHART_OUTPUT_DIR):
        shutil.rmtree(CHART_OUTPUT_DIR)
    os.makedirs(CHART_OUTPUT_DIR, exist_ok=True)

    print(f"\n  生成K线图到 {CHART_OUTPUT_DIR}/ ...")

    for r in results:
        try:
            _plot_nx_b1_chart(r, CHART_OUTPUT_DIR)
        except Exception as e:
            print(f"  {r['code']} 图表生成失败: {e}")

    print(f"  已生成 {len(results)} 张K线图")


def _plot_nx_b1_chart(result, output_dir):
    """为单只股票绘制N型B1 K线图"""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    from matplotlib import font_manager

    # 中文字体
    plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'DejaVu Sans']
    plt.rcParams['axes.unicode_minus'] = False

    cd = result["chart_data"]
    C = cd["close"]
    H = cd["high"]
    L = cd["low"]
    O = cd["open"]
    V = cd["volume"]
    dates = cd["dates"]
    white = cd["white"]
    yellow = cd["yellow"]
    b1 = cd["b1"]

    # 截取最近120天
    n = len(C)
    start = max(0, n - 120)
    s = slice(start, n)
    C_s, H_s, L_s, O_s, V_s = C[s], H[s], L[s], O[s], V[s]
    white_s, yellow_s = white[s], yellow[s]
    b1_s = b1[s]
    dates_s = dates[s]

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(16, 10),
                                    gridspec_kw={'height_ratios': [3, 1]},
                                    sharex=True)
    fig.suptitle(f"N型B1选股 {result['code']}  C={result['close']:.2f}  "
                 f"市值={result['market_cap']:.0f}亿  T+3胜率={result['t3_hit_rate']:.0f}%",
                 fontsize=14, fontweight='bold')

    # K线图
    x = np.arange(len(C_s))
    colors = ['#ef5350' if O_s[i] > C_s[i] else '#26a69a' for i in range(len(C_s))]

    for i in range(len(C_s)):
        ax1.plot([x[i], x[i]], [L_s[i], H_s[i]], color=colors[i], linewidth=0.6)
        ax1.plot([x[i], x[i]], [min(O_s[i], C_s[i]), max(O_s[i], C_s[i])],
                 color=colors[i], linewidth=2.5)

    # 均线
    ax1.plot(x, white_s, color='white', linewidth=0.8, alpha=0.7, label='白线')
    ax1.plot(x, yellow_s, color='#FFD54F', linewidth=0.8, alpha=0.7, label='黄线')

    # B1信号标记
    b1_indices = np.where(b1_s)[0]
    if len(b1_indices) > 0:
        b1_dates = [str(dates_s[i])[:10] for i in b1_indices]
        b1_prices = [L_s[i] * 0.98 for i in b1_indices]
        ax1.scatter(b1_indices, b1_prices, marker='*', s=120,
                    c='magenta', zorder=5, label='B1信号')

    # N型B1连接线
    nx_list = result["nx_b1_list"]
    nx_x = []
    nx_prices_plot = []
    for nb in nx_list:
        idx = nb["idx"] - start
        if 0 <= idx < len(C_s):
            nx_x.append(idx)
            nx_prices_plot.append(L_s[idx] * 0.96)
            ax1.annotate(f"{nb['price']:.2f}", (idx, L_s[idx] * 0.94),
                         fontsize=8, color='blue', ha='center', fontweight='bold')
    if len(nx_x) >= 2:
        ax1.plot(nx_x, nx_prices_plot, 'b--', linewidth=1.5, alpha=0.8, label='N型结构')

    ax1.legend(loc='upper left', fontsize=8)
    ax1.set_ylabel('价格')
    ax1.grid(True, alpha=0.3)

    # 成交量
    v_colors = ['#ef5350' if O_s[i] > C_s[i] else '#26a69a' for i in range(len(V_s))]
    ax2.bar(x, V_s, color=v_colors, width=0.8, alpha=0.7)
    ax2.set_ylabel('成交量')
    ax2.grid(True, alpha=0.3)

    # X轴日期
    tick_step = max(1, len(x) // 10)
    tick_pos = x[::tick_step]
    tick_labels = [str(dates_s[i])[:10] for i in range(0, len(dates_s), tick_step)]
    ax2.set_xticks(tick_pos)
    ax2.set_xticklabels(tick_labels, rotation=45, fontsize=7)

    plt.tight_layout()
    filepath = os.path.join(output_dir, f"{result['code']}.png")
    fig.savefig(filepath, dpi=120, bbox_inches='tight')
    plt.close(fig)
    print(f"    {result['code']}.png 已保存")
