"""动能+砖 策略

策略逻辑：
1. 基于动能评分选出股票池（KDJ/RSI动量、Z-Score、筹码流量、综合天命打分）
2. 在动能股票池中基于金砖进一步筛选（砖型图、绿转强红共振、黄柱动能）
3. 按金砖排名"下大上小"排序取前N只
4. T+1 开盘买入，止损/2日不拉升/脱离成本5%持仓4-6天

选股公式来源：thinking/动能砖.md
"""

import os
import re
import time
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore", category=RuntimeWarning)
from MyTT import (
    EMA, MA, SMA, HHV, LLV, REF, COUNT, EVERY, EXIST,
    CROSS, MAX, ABS, IF, BARSLAST, HHVBARS,
    BBI as MyTT_BBI,
)
from mootdx.reader import Reader

from config import (
    TDX_DIR, TDX_MARKET, SCAN_MAX_WORKERS, STOCK_TYPE,
    HUANGBAI_M1, HUANGBAI_M2, HUANGBAI_M3, HUANGBAI_M4,
    HUANGBAI_N, HUANGBAI_M, HUANGBAI_N1, HUANGBAI_N2,
)


# ---------- helpers ----------

def _ref_at(S, offsets):
    """REF with variable offset: 取 S[i - offsets[i]]"""
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


def _rolling_sum(X, N):
    """SUM(X, N) - N周期累加"""
    return pd.Series(X).rolling(N, min_periods=1).sum().values


def _rolling_std(X, N):
    """STD(X, N) - N周期标准差 (population)"""
    return pd.Series(X).rolling(N, min_periods=1).std(ddof=0).values


# ================================================================== #
#  全量每bar信号计算                                                    #
# ================================================================== #

def _compute_all_bar_signals(C, H, L, O, V, dates, code, params):
    """计算每根 bar 的动能+金砖信号（向量版本）

    Returns dict of numpy arrays, or None if data insufficient.
    """
    n = len(C)
    if n < 300:
        return None

    REFC = REF(C, 1)
    REFV = REF(V, 1)
    pct_chg = np.where(REFC > 0, (C - REFC) / REFC * 100, 0.0)
    is_yang = (C > O) & (C > REFC)

    max_oc = np.maximum(O, C)
    min_oc = np.minimum(O, C)
    day_range = np.maximum(H - L, 0.001)
    up_shadow = (H - max_oc) / day_range
    dn_shadow = (min_oc - L) / day_range

    # ---- RSI(3) ----
    diff_rsi = C - REFC
    sma1 = SMA(np.maximum(diff_rsi, 0), 3, 1)
    sma2 = SMA(np.abs(diff_rsi), 3, 1)
    rsi3 = sma1 / np.maximum(sma2, 0.001) * 100

    # ---- KDJ(9,3,3) ----
    llv9 = LLV(L, 9)
    hhv9 = HHV(H, 9)
    denom9 = hhv9 - llv9
    rsv = np.where(denom9 != 0, (C - llv9) / denom9 * 100, 50.0)
    K_val = SMA(rsv, 3, 1)
    D_val = SMA(K_val, 3, 1)
    J_val = 3 * K_val - 2 * D_val

    # ---- 动量增量（动能和金砖共用） ----
    N1 = J_val - REF(J_val, 1)
    N2 = rsi3 - REF(rsi3, 1)
    vol_ratio = V / np.maximum(REFV, 1)

    # ============================================================ #
    #  动能选股                                                      #
    # ============================================================ #

    # 影线系数（动能版）
    shadow_ratio_dn = (H - C) / np.maximum(H - np.minimum(O, REFC), 0.001)
    shadow_coef = np.where(is_yang, (0.70 - shadow_ratio_dn) * 1.3, 1.0)

    # 量价加成
    vb_raw = 1.0 + (1.2 - 1.0) / (6.0 - 2.5) * (vol_ratio - 2.5)
    vol_bonus = np.where(
        is_yang & (vol_ratio >= 2.5),
        np.where(vol_ratio >= 6.0, 1.2, vb_raw),
        1.0)

    # 基础动量
    base_mom = (N1 + N2) / 2 * shadow_coef * vol_bonus

    # X 动量
    x_diff = (N1 + N2) - (REF(N1, 1) + REF(N2, 1))
    x_mom = np.where(is_yang & (x_diff > 0),
                     x_diff / 2 * shadow_coef * vol_bonus, 0)

    # Z-Score (45日)
    ret_mean = MA(pct_chg, 45)
    ret_std = _rolling_std(pct_chg, 45)
    ret_z = np.where(ret_std > 0, (pct_chg - ret_mean) / ret_std, 0)

    # 套牢筹码流量 (20日)
    vol_mean = MA(V, 45)
    top_prox = (H - LLV(L, 20)) / np.maximum(HHV(H, 20) - LLV(L, 20), 0.001)
    is_true_green = (O > C) & (C < REFC)
    is_fake_green = (O > C) & (C >= REFC)
    flow1 = np.where(is_true_green,
                     top_prox * (V / np.maximum(vol_mean, 1)) * np.maximum(0, -ret_z), 0)
    flow2 = np.where(is_fake_green,
                     top_prox * (V / np.maximum(vol_mean, 1))
                     * ((O - C) / np.maximum(REFC, 0.001) * 100) * 0.5, 0)
    overhead_v20 = REF(_rolling_sum(flow1 + flow2, 20), 1)

    # 综合天命打分
    norm_j = np.clip((REF(J_val, 1) - 19.0) / 11.0, 0, 1)
    norm_rsi = np.clip((REF(rsi3, 1) - 29.0) / 11.0, 0, 1)
    norm_retz = np.clip((ret_z - 2.5) / 0.7, 0, 1)
    norm_v20 = np.clip((overhead_v20 - 2.0) / 8.0, 0, 1)
    norm_bonus = np.clip((vol_ratio - 5.0) / 5.0, 0, 1)

    visual_score = (base_mom + 15 * norm_bonus
                    - (20 * norm_j + 30 * norm_rsi + 35 * norm_v20 + 10 * norm_retz)
                    + 10)

    # 阵营过滤
    mask_a = (visual_score >= 35) & (base_mom >= 25)
    mask_b = (visual_score >= 20) & (visual_score < 35) & (base_mom >= 45)
    mask_c = (visual_score < 20) & (base_mom >= 65)
    mask_d = (x_mom >= 45) & (base_mom <= 20)

    # 硬性过滤
    hard_mask = ((up_shadow < 0.30) & (dn_shadow < 0.35)
                 & (pct_chg >= 3.0) & (ret_z >= 0.8))

    dongneng_ok = is_yang & hard_mask & (mask_a | mask_b | mask_c | mask_d)

    # ============================================================ #
    #  金砖选股                                                      #
    # ============================================================ #

    # 砖型图
    hhv4 = HHV(H, 4)
    llv4 = LLV(L, 4)
    var1a = (hhv4 - C) / np.maximum(hhv4 - llv4, 0.001) * 100 - 90
    var2a = SMA(var1a, 4, 1) + 100
    var3a = (C - llv4) / np.maximum(hhv4 - llv4, 0.001) * 100
    var4a = SMA(var3a, 6, 1)
    var5a = SMA(var4a, 6, 1) + 100
    var6a = var5a - var2a
    brick = np.where(var6a > 4, var6a - 4, 0)

    # 核心均线
    white = EMA(EMA(C, 10), 10)
    m1, m2, m3, m4 = params["m1"], params["m2"], params["m3"], params["m4"]
    yellow = (MA(C, m1) + MA(C, m2) + MA(C, m3) + MA(C, m4)) / 4
    bbi = (MA(C, 3) + MA(C, 6) + MA(C, 12) + MA(C, 24)) / 4

    # SHORT / LONG
    n1_p, n2_p = params["n1"], params["n2"]
    s_denom = HHV(C, n1_p) - LLV(L, n1_p)
    SHORT = np.where(s_denom != 0, 100 * (C - LLV(L, n1_p)) / s_denom, 50.0)
    l_denom = HHV(C, n2_p) - LLV(L, n2_p)
    LONG = np.where(l_denom != 0, 100 * (C - LLV(L, n2_p)) / l_denom, 50.0)

    # 板块 / 振幅
    is_tech = code[:2] in ("30", "68")
    pct_change_arr = np.where(REFC > 0, C / REFC - 1, 0.0)
    volatile = EXIST(pct_change_arr > 0.15, 200)
    is_volatile = volatile | is_tech
    amp_range = np.where(is_volatile, 8.0, 5.0)
    relax = np.where(is_volatile, 0.9, 1.0)

    daily_amp = (H - L) / np.where(L > 0, L, 0.001) * 100
    daily_pct = ABS(C - REFC) / np.where(REFC > 0, REFC, 0.001) * 100 * relax
    up_doji = (C > REFC) & (
        ABS(C - O) / np.where(O > 0, O, 0.001) * 100 * relax < 1.8)

    # 异动
    n_p, m_p = params["n"], params["m"]
    needle_20 = ((SHORT <= 20) & (LONG >= 75)) | ((LONG - SHORT) >= 70)
    treasure = ((COUNT(LONG >= 75, 8) >= 6) & (COUNT(SHORT <= 70, 7) >= 4)
                & (COUNT(SHORT <= 50, 8) >= 1))
    dbl_fork = (EVERY(LONG >= 75, 8) & (COUNT(SHORT <= 50, 6) >= 2)
                & (COUNT(SHORT <= 20, 7) >= 1))
    red_green = (COUNT(C >= O, 15) > 7) | (COUNT(C > REFC, 11) > 5)

    near_amp = ((HHV(H, n_p) - LLV(L, n_p))
                / np.where(LLV(L, n_p) > 0, LLV(L, n_p), 0.001) * 100)
    far_amp = ((HHV(H, m_p) - LLV(L, m_p))
               / np.where(LLV(L, m_p) > 0, LLV(L, m_p), 0.001) * 100)
    near_ano = (near_amp >= 15) | (
        (HHV(H, 12) - LLV(L, 14))
        / np.where(LLV(L, 14) > 0, LLV(L, 14), 0.001) * 100 >= 11)
    far_ano = far_amp >= 30
    super_ano = near_amp >= 60
    wash_ano = (COUNT(needle_20, 10) >= 2) | treasure | dbl_fork
    anomaly = near_ano | far_ano | wash_ano

    # 成交量
    vday = HHVBARS(V, 40)
    c_vd = _ref_at(C, vday)
    c_vd1 = _ref_at(C, vday + 1)
    o_vd = _ref_at(O, vday)
    not_big_green = np.where(np.isnan(c_vd), True, (c_vd >= c_vd1) | (c_vd >= o_vd))
    ok_green = not_big_green | ((vday >= 15) & ~not_big_green)

    hhv_v20 = HHV(V, 20)
    hhv_v50 = HHV(V, 50)
    shrink = (V < hhv_v20 * 0.416) | (V < hhv_v50 / 3)
    pb_shrink = (V < hhv_v20 * 0.45) | (V < hhv_v50 / 3)
    mod_shrink = (V < hhv_v20 * 0.618) | (V < hhv_v50 / 3)
    sup_shrink = (V < HHV(V, 30) / 4) | (V < hhv_v50 / 6)

    # 趋势状态
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

    # 回踩距离
    dist_w = ABS(C - white) / np.where(C > 0, C, 0.001) * 100
    dist_wL = ABS(L - white) / np.where(white > 0, white, 0.001) * 100
    dist_bbi = ABS(C - bbi) / np.where(C > 0, C, 0.001) * 100
    dist_bbiL = ABS(L - bbi) / np.where(bbi > 0, bbi, 0.001) * 100
    dist_y = ABS(C - yellow) / np.where(yellow > 0, yellow, 0.001) * 100

    pb_white = (((C >= white) & (dist_w <= 2))
                | ((C < white) & (dist_w < 0.8))
                | ((C >= bbi) & (dist_bbi < 2.5) & (dist_bbiL < 1)
                   & (dist_w <= 3) & (daily_pct < 1) & (C > REFC)))
    white_sup = (C >= white) & (dist_w < 1.5)
    strong_pb_hold = (((dist_wL < 1) | (dist_bbiL < 0.5))
                      & (C > white) & (dist_w <= 3.5))
    pb_yellow = (((C >= yellow) & ((dist_y <= 1.5) | ((dist_y <= 2) & (daily_pct < 1))))
                 | ((C < yellow) & (dist_y <= 0.8)))

    # B1 七个子条件（金砖"存在B"）
    rsi_j = rsi3 + J_val

    b_oversold_turn = (uptrend
                       & (rsi3 - 15 >= REF(rsi3, 1))
                       & ((REF(rsi3, 1) < 20) | (REF(J_val, 1) < 14))
                       & (daily_amp < amp_range + 0.5)
                       & ((daily_pct < 2.3) | (up_doji & (daily_pct < 4)))
                       & ok_green & anomaly & (C >= yellow))

    b_oversold_shrink = (uptrend
                         & ((J_val < 14) | (rsi3 < 23))
                         & ((rsi_j < 55) | (J_val == LLV(J_val, 20)))
                         & (daily_amp < amp_range)
                         & ((daily_pct < 2.5) | up_doji)
                         & ok_green
                         & (shrink | (mod_shrink & (daily_pct < 1)))
                         & anomaly)

    b_raw = ((white > yellow)
             & (C >= yellow * 0.99)
             & (yellow >= REF(yellow, 1))
             & ((J_val < 13) | (rsi3 < 21))
             & (rsi_j < LLV(rsi_j, 15) * 1.5)
             & mod_shrink & ok_green
             & ((ABS(C - O) * 100 / np.where(O > 0, O, 0.001) < 1.5)
                | (sup_shrink | (mod_shrink & (V < LLV(V, 20) * 1.1) & (J_val == LLV(J_val, 20))))
                | (mod_shrink & ((dist_w < 1.8) | (dist_bbi < 1.5) | (dist_y < 2.8))))
             & anomaly)

    b_oversold_super = (uptrend
                        & ((J_val < 14) | (rsi3 < 23))
                        & (rsi_j < 60) & (far_amp >= 45)
                        & ((daily_amp < amp_range)
                           | (super_ano & (daily_amp < amp_range + 3.2) & (C > O) & (C > white)))
                        & (((C < O) & (V < REF(V, 1)) & (C >= yellow)) | (C >= O))
                        & ((daily_pct < 2) | up_doji)
                        & ok_green & sup_shrink & anomaly)

    b_pb_white = (strong_trend
                  & ((J_val < 30) | (rsi3 < 40) | wash_ano)
                  & (rsi_j < 70)
                  & ((daily_amp < amp_range + 0.5) | (dist_w < 1) | (dist_bbi < 1))
                  & pb_white
                  & ((daily_pct < 2) | ((daily_pct < 5) & white_sup))
                  & ok_green & pb_shrink & anomaly & (L <= REFC))

    b_pb_super = (super_bull
                  & ((J_val < 35) | (rsi3 < 45) | wash_ano)
                  & (rsi_j < 80) & (rsi_j == LLV(rsi_j, 25))
                  & (daily_amp < amp_range + 1)
                  & ((daily_pct < 2.5) | (dist_w < 2))
                  & strong_pb_hold & ok_green & anomaly & mod_shrink)

    b_pb_yellow = ((white >= yellow)
                   & (C >= yellow * 0.975)
                   & ((J_val < 13) | (rsi3 < 18))
                   & pb_yellow & ok_green
                   & (shrink | (mod_shrink & ((J_val == LLV(J_val, 20)) | (rsi3 == LLV(rsi3, 14)))))
                   & (yellow >= REF(yellow, 1) * 0.997)
                   & (MA(C, 60) >= REF(MA(C, 60), 1))
                   & (near_amp >= 11.9) & (far_amp >= 19.5))

    exist_b = (b_oversold_turn | b_oversold_shrink | b_raw
               | b_oversold_super | b_pb_white | b_pb_super | b_pb_yellow)

    # ---- 金砖动量指标 ----
    vol_coef = np.where(
        V < REFV * 0.99,
        (1 - 5 * (REFV - V) / np.where(REFV > 0, REFV, 0.001)) * 0.8, 1.0)
    multi_coef = np.where(vol_ratio >= 4, 1.4, 0.1 * vol_ratio + 1)
    multi_bonus = np.where(is_yang & (V > REFV * 1.8), multi_coef, 1.0)
    shadow_coef_jz = np.where(
        (C > REFC) & (C > O),
        (0.75 - (H - C) / np.maximum(H - np.minimum(O, REFC), 0.001)) * 1.3,
        1.0)

    yellow_bar = (N1 + N2) / 2 * shadow_coef_jz * multi_bonus
    x_mom_jz = np.where(
        (C > O) & (C > REFC) & ((N1 + N2) > (REF(N1, 1) + REF(N2, 1))),
        ((N1 + N2) - (REF(N1, 1) + REF(N2, 1))) / 2 * shadow_coef_jz * vol_coef * multi_bonus,
        0)

    # ---- 红绿判定 ----
    jin_hong = brick > REF(brick, 1)
    jin_lv = brick <= REF(brick, 1)
    zuo_lv = REF(jin_lv.astype(float), 1) == 1

    red_len = np.where(jin_hong, brick - REF(brick, 1), 0)
    brick_len = brick - REF(brick, 1)
    brick_ref2 = REF(brick, 2)
    brick_ref1 = REF(brick, 1)
    zuo_lv_len = np.where(zuo_lv, brick_ref2 - brick_ref1, 0)
    ratio_jz = np.where(zuo_lv_len > 0, red_len / zuo_lv_len, 0)

    qiang_hong = jin_hong & zuo_lv & (ratio_jz > 0.666)

    # ---- 共振条件 ----
    trend_cond = ((white >= yellow * 0.995)
                  & (yellow >= REF(yellow, 1) * 0.997)
                  & (C >= yellow * 0.997))

    upper_shadow_cond = (
        ((C >= O) | (C > REFC))
        & (1 - (H - C) / np.maximum(H - np.minimum(L, REFC), 0.001) > 0.618))

    turnover_cond = V > 0

    cond1_inner = (qiang_hong
                   & ((yellow_bar >= 10) | (x_mom_jz >= 10))
                   & (EXIST(exist_b, 2)
                      | ((REF(LONG, 1) > 85) & (REF(SHORT, 1) < 30))))

    cond2_inner = (qiang_hong
                   & ((yellow_bar >= 10) | (x_mom_jz >= 10))
                   & (((EXIST(LONG - SHORT > 60, 4) & (LONG > 98) & (SHORT > 98))
                       | ((yellow_bar > 20) & (C > white))
                       | (yellow_bar > 30)
                       | ((yellow_bar + brick_len) > 50)
                       | (x_mom_jz > 40))))

    resonance = cond1_inner | cond2_inner

    # 非涨停
    limit_threshold = 1.139 if is_tech else 1.069
    non_limit_up = C / np.where(REFC > 0, REFC, 0.001) < limit_threshold

    jinzhuan_ok = (resonance & upper_shadow_cond & trend_cond
                   & turnover_cond & non_limit_up)

    # ============================================================ #
    #  最终信号：动能先筛 → 金砖再筛（串行过滤）                      #
    # ============================================================ #
    final_ok = dongneng_ok & jinzhuan_ok

    # 排名分数：金砖排名"下大上小"
    rank_score = np.where(final_ok, brick / pct_chg, 0.0)

    return {
        "dongneng_ok": dongneng_ok,
        "jinzhuan_ok": jinzhuan_ok,
        "any_ok": final_ok,
        "rank_score": rank_score,
        "close": C,
        "open": O,
        "high": H,
        "low": L,
        "dates": dates,
        "pct_change": pct_chg,
        "brick_value": brick,
        "base_mom": base_mom,
        "visual_score": visual_score,
        "yellow": yellow,
        "white": white,
    }


# ================================================================== #
#  全市场选股扫描                                                      #
# ================================================================== #

def _get_all_codes(tdxdir=TDX_DIR):
    """从通达信本地目录提取全部A股代码（去重、去指数）"""
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


def _compute_signals(C, H, L, O, V, dates, code, params):
    """计算最新 bar 的信号结果"""
    signals = _compute_all_bar_signals(C, H, L, O, V, dates, code, params)
    if signals is None:
        return None
    i = len(C) - 1
    return {
        "code": code,
        "dongneng": bool(signals["dongneng_ok"][i]),
        "jinzhuan": bool(signals["jinzhuan_ok"][i]),
        "any": bool(signals["any_ok"][i]),
        "close": float(signals["close"][i]),
        "pct_change": float(signals["pct_change"][i]),
        "brick": float(signals["brick_value"][i]),
        "base_mom": float(signals["base_mom"][i]),
        "visual_score": float(signals["visual_score"][i]),
        "rank_score": float(signals["rank_score"][i]),
    }


# ---------- 多进程扫描 ----------

_process_reader = None


def _init_process(tdxdir, market):
    global _process_reader
    _process_reader = Reader.factory(market=market, tdxdir=tdxdir)


def _scan_one(code, params):
    try:
        df = _process_reader.daily(symbol=code)
        if df is None or len(df) < 300:
            return code, None, False
        df = df.sort_index()
        sig = _compute_signals(
            df["close"].values.astype(float),
            df["high"].values.astype(float),
            df["low"].values.astype(float),
            df["open"].values.astype(float),
            df["volume"].values.astype(float),
            df.index, code, params)
        if sig is not None and sig["any"]:
            return code, sig, False
        return code, None, False
    except Exception:
        return code, None, True


def scan_all(tdxdir=TDX_DIR, market=TDX_MARKET, max_workers=SCAN_MAX_WORKERS):
    """全市场扫描动能+金砖选股"""
    from concurrent.futures import ProcessPoolExecutor, as_completed

    codes = _get_all_codes(tdxdir)
    total = len(codes)
    print(f"扫描 {total} 只A股... (workers={max_workers or 'auto'})")

    params = {
        "m1": HUANGBAI_M1, "m2": HUANGBAI_M2,
        "m3": HUANGBAI_M3, "m4": HUANGBAI_M4,
        "n": HUANGBAI_N, "m": HUANGBAI_M,
        "n1": HUANGBAI_N1, "n2": HUANGBAI_N2,
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
                tag = "双重"
                print(f"  {code}  [{tag}]  C={sig['close']:.2f}  "
                      f"涨幅={sig['pct_change']:.1f}%  砖={sig['brick']:.1f}  "
                      f"动能={sig['base_mom']:.1f}  评分={sig['visual_score']:.1f}")
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
            tag = "双重"
            print(f"  {r['code']}  [{tag}]  C={r['close']:.2f}  "
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
        signals = _compute_all_bar_signals(
            df["close"].values.astype(float),
            df["high"].values.astype(float),
            df["low"].values.astype(float),
            df["open"].values.astype(float),
            df["volume"].values.astype(float),
            df.index, code, params)
        return code, signals, False
    except Exception:
        return code, None, True


def preload_all_signals(start="2024-01-01", end="2025-12-31",
                        max_workers=SCAN_MAX_WORKERS,
                        tdxdir=TDX_DIR, market=TDX_MARKET):
    """并行预计算全部 A 股的每bar信号数据

    Returns:
        (all_signals, trading_days)
        - all_signals: dict[str, dict]  股票代码 -> 信号数组字典
        - trading_days: DatetimeIndex   回测区间内的交易日历
    """
    from concurrent.futures import ProcessPoolExecutor, as_completed

    codes = _get_all_codes(tdxdir)
    total = len(codes)
    print(f"预加载 {total} 只A股信号... (workers={max_workers or 'auto'})")

    params = {
        "m1": HUANGBAI_M1, "m2": HUANGBAI_M2,
        "m3": HUANGBAI_M3, "m4": HUANGBAI_M4,
        "n": HUANGBAI_N, "m": HUANGBAI_M,
        "n1": HUANGBAI_N1, "n2": HUANGBAI_N2,
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
