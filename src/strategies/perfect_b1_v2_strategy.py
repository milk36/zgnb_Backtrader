"""完美B1 V2策略 — 必要条件门控 + 四通道OR识别

基于 thinking/完美B1.md 中11个历史案例的前期量价关系和趋势特征，
识别有效B1买入信号。核心架构：建仓波必要条件 + 四通道OR + 预警过滤。

必要条件（11/11案例共有）：
  近期存在带量拉升的建仓波（放量阳线密度 + 区间涨幅 + 倍量柱 OR检测）

四通道OR（通过任一即可）：
  通道A 缩量极致型: shrink<30% & (超卖 OR 贴近均线)
    覆盖: 华纳药厂/宁波韵升/微芯生物/方正科技/国轩高科/野马电池/光电股份/新瀚新材/昂利康
  通道B 白线不死叉型: 白线30天>=黄线 & J<20 & vs黄线>-3% & 洗盘充分(>=3天)
    覆盖: 澄天伟业/国轩高科
  通道C 极端超卖型: J<0 或 RSI<15 & shrink<40%
    覆盖: 微芯生物/野马电池/光电股份
  通道D 大牛市型: 40日涨幅>80% & shrink<30% & 贴近白线<8% & J<15
    覆盖: 昂利康

预警条件（通道B豁免）：
  shrink>35% & (回调深度<8% 或 5日振幅<2.5%) → 过滤赢时胜

排序：通道优先级(A=1 > C=2 > D=3 > B=4) × 10000 - final_score
5维评分保留用于排序和日志，不再作为门控条件。

架构：包装 V4 的 _compute_all_bar_signals()，叠加通道检测，
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
#  通道名称与常量                                                      #
# ================================================================== #

CHANNEL_NAMES = {
    0: "不匹配",
    1: "缩量极致型",      # 通道A
    2: "白线不死叉型",    # 通道B
    3: "极端超卖型",      # 通道C
    4: "大牛市型",        # 通道D
}


# ================================================================== #
#  四通道OR检测 + 建仓波必要条件                                        #
# ================================================================== #

def _compute_channel_signals(C, H, L, O, V, white, yellow, shrink_score, J, rsi3):
    """四通道OR检测 + 建仓波必要条件

    Returns:
        dict 包含各通道通过标记、建仓波存在性、预警条件、通道编号
    """
    # ===== 必要条件：建仓波存在 =====
    v_ma20 = MA(V, 20)
    big_vol_yang = (C > O) & (V > v_ma20 * 1.5)
    cnt_bvy_20 = COUNT(big_vol_yang.astype(float), 20)
    cnt_bvy_40 = COUNT(big_vol_yang.astype(float), 40)
    cnt_bvy_60 = COUNT(big_vol_yang.astype(float), 60)

    rise_10 = (C - REF(C, 10)) / np.maximum(REF(C, 10), 0.001) * 100
    rise_20 = (C - REF(C, 20)) / np.maximum(REF(C, 20), 0.001) * 100
    rise_40 = (C - REF(C, 40)) / np.maximum(REF(C, 40), 0.001) * 100
    rise_60 = (C - REF(C, 60)) / np.maximum(REF(C, 60), 0.001) * 100

    vol_ratio_prev = V / np.maximum(REF(V, 1), 1)
    cnt_dv_20 = COUNT((vol_ratio_prev >= 2.0).astype(float), 20)
    cnt_dv_40 = COUNT((vol_ratio_prev >= 2.0).astype(float), 40)

    # 短波建仓: 20日内>=3根放量阳线 + 10日涨幅>10%
    has_short_wave = (cnt_bvy_20 >= 3) & (rise_10 > 10)
    # 中波建仓: 20日内有倍量柱 + 20日涨幅>15%
    has_mid_wave = (cnt_dv_20 >= 1) & (rise_20 > 15)
    # 长波建仓: 40日内>=3根放量阳线 + 40日涨幅>15%
    has_long_wave = (cnt_bvy_40 >= 3) & (rise_40 > 15)
    # 超长波建仓: 60日内>=5根放量阳线 + 60日涨幅>20% (澄天伟业32天建仓)
    has_very_long_wave = (cnt_bvy_60 >= 5) & (rise_60 > 20)
    # 倍量柱加成: 40日内>=2根倍量柱
    has_double_vol = cnt_dv_40 >= 2

    accumulation_exists = (has_short_wave | has_mid_wave | has_long_wave
                           | has_very_long_wave | has_double_vol)

    # ===== 通道A: 缩量极致型 =====
    shrink_ok = shrink_score < 0.30
    j_oversold = J < 14
    rsi_oversold = rsi3 < 25
    near_yellow = ABS(C - yellow) / np.maximum(yellow, 0.001) * 100 < 3.0
    near_white = ABS(C - white) / np.maximum(white, 0.001) * 100 < 3.0
    channel_a = shrink_ok & (j_oversold | rsi_oversold | near_yellow | near_white)

    # ===== 通道B: 白线不死叉型 =====
    white_no_death = EVERY(white >= yellow, 30)
    j_low = J < 20
    above_yellow_support = (C - yellow) / np.maximum(yellow, 0.001) * 100 > -3.0
    below_white_cnt = COUNT((C < white).astype(float), 10)
    sufficient_washout = below_white_cnt >= 3
    channel_b = white_no_death & j_low & above_yellow_support & sufficient_washout

    # ===== 通道C: 极端超卖型 =====
    # shrink<35%: 比通道A宽松但不让赢时胜(37.1%)通过
    channel_c = ((J < 0) | (rsi3 < 15)) & (shrink_score < 0.35)

    # ===== 通道D: 大牛市型 =====
    channel_d = ((rise_40 > 80) & (shrink_score < 0.30)
                 & (ABS(C - white) / np.maximum(white, 0.001) * 100 < 8.0)
                 & (J < 15))

    # ===== 通道汇总 =====
    channel_pass = channel_a | channel_b | channel_c | channel_d

    # ===== 预警条件（通道B豁免）=====
    warning_shrink = shrink_score > 0.35
    pullback_depth = (HHV(H, 20) - LLV(L, 5)) / np.maximum(HHV(H, 20), 0.001) * 100
    daily_amp = (H - L) / np.maximum(L, 0.001) * 100
    is_warning = warning_shrink & ((pullback_depth < 8.0) | (MA(daily_amp, 5) < 2.5))
    is_warning_effective = is_warning & ~channel_b

    # ===== 通道编号 =====
    channel_type = np.where(channel_a, 1,
                   np.where(channel_c, 3,
                   np.where(channel_d, 4,
                   np.where(channel_b, 2, 0))))

    return {
        "accumulation_exists": accumulation_exists,
        "channel_a": channel_a,
        "channel_b": channel_b,
        "channel_c": channel_c,
        "channel_d": channel_d,
        "channel_pass": channel_pass,
        "channel_type": channel_type,
        "is_warning": is_warning,
        "is_warning_effective": is_warning_effective,
        "rise_40": rise_40,
    }


# ================================================================== #
#  5维评分（排序用，不作为门控条件）                                     #
# ================================================================== #

def _calc_accumulation_score(C, O, V):
    """维度1: 建仓强度评分 (0-100)"""
    v_ma20 = MA(V, 20)
    big_vol_yang = ((C > O) & (V > v_ma20 * 1.5)).astype(float)
    cnt_bvy_20 = COUNT(big_vol_yang, 20)
    cnt_bvy_40 = COUNT(big_vol_yang, 40)
    cnt_bvy_60 = COUNT(big_vol_yang, 60)

    rise_10 = (C - REF(C, 10)) / np.maximum(REF(C, 10), 0.001) * 100
    rise_20 = (C - REF(C, 20)) / np.maximum(REF(C, 20), 0.001) * 100
    rise_40 = (C - REF(C, 40)) / np.maximum(REF(C, 40), 0.001) * 100

    vol_ratio_prev = V / np.maximum(REF(V, 1), 1)
    double_vol = (vol_ratio_prev >= 2.0).astype(float)
    cnt_dv_20 = COUNT(double_vol, 20)

    vol_price_surge = ((V > v_ma20 * 2) & (C > O)).astype(float)
    cnt_vps_20 = COUNT(vol_price_surge, 20)

    short_wave = (np.clip(cnt_bvy_20 / 3.0, 0, 1)
                 * np.clip(rise_10 / 15.0, 0, 1) * 30)
    mid_wave = (np.clip(cnt_bvy_40 / 5.0, 0, 1)
               * np.clip(rise_20 / 20.0, 0, 1) * 30)
    long_wave = (np.clip(cnt_bvy_60 / 7.0, 0, 1)
                * np.clip(rise_40 / 30.0, 0, 1) * 20)
    double_vol_bonus = np.clip(cnt_dv_20 / 3.0, 0, 1) * 10
    vps_bonus = np.clip(cnt_vps_20 / 2.0, 0, 1) * 10

    return np.clip(short_wave + mid_wave + long_wave
                   + double_vol_bonus + vps_bonus, 0, 100)


def _calc_washout_score(V, shrink_score):
    """维度2: 缩量洗盘评分 (0-100)

    阈值已放宽：shrink 零分阈值从 0.40→0.70，让澄天伟业也能得分。
    """
    hhv_v_50 = HHV(V, 50)
    v_ma60 = MA(V, 60)

    # (a) shrink极致度: shrink<18%→满分, >70%→0分 (50分)
    _s = np.nan_to_num(shrink_score, nan=1.0)
    s1 = np.clip((0.70 - _s) / (0.70 - 0.18), 0, 1) * 50

    # (b) V/MA(V,60) 极致度: <40%→满分, >80%→0分 (25分)
    v_ratio_60 = V / np.maximum(v_ma60, 1)
    s2 = np.clip((0.80 - v_ratio_60) / (0.80 - 0.40), 0, 1) * 25

    # (c) sup_shrink 满足: V < HHV(V,50)/3 (15分)
    s3 = np.where(V < hhv_v_50 / 3, 15.0, 0.0)

    # (d) 量能递减趋势: MA(V,5)/MA(V,10) < 1 说明近期缩量 (10分)
    v_ma5 = MA(V, 5)
    v_ma10 = MA(V, 10)
    vol_decline = v_ma5 / np.maximum(v_ma10, 1)
    s4 = np.clip((1.0 - vol_decline) / 0.5, 0, 1) * 10

    return np.clip(s1 + s2 + s3 + s4, 0, 100)


def _calc_oversold_score(J, rsi3):
    """维度3: 超卖拐点评分 (0-100)

    阈值已放宽：J 零分阈值从 15→30，让方正科技①也能得分。
    """
    _j = np.nan_to_num(J, nan=50.0)
    _r = np.nan_to_num(rsi3, nan=50.0)

    # (a) J值超卖深度: J<-10→满分, J>30→0分 (40分)
    j_score = np.clip((30.0 - _j) / (30.0 + 10.0), 0, 1) * 40

    # (b) RSI超卖深度: RSI<12→满分, RSI>30→0分 (30分)
    rsi_score = np.clip((30.0 - _r) / (30.0 - 12.0), 0, 1) * 30

    # (c) J值拐头: 当日J > 前日J (15分)
    j_turn = (J > REF(J, 1)).astype(float) * 15

    # (d) RSI拐头: 当日RSI > 前日RSI (10分)
    rsi_turn = (rsi3 > REF(rsi3, 1)).astype(float) * 10

    # (e) J值位于20日最低附近 (5分)
    llv_j_20 = LLV(J, 20)
    j_low_score = np.where(np.abs(J - llv_j_20) < 1.0, 5.0, 0.0)

    return np.clip(j_score + rsi_score + j_turn + rsi_turn + j_low_score, 0, 100)


def _calc_support_score(C, white, yellow):
    """维度4: 支撑结构评分 (0-100)

    阈值已放宽：贴近黄线零分阈值从 5%→50%，让昂利康也能得分。
    """
    pct_w = (C - white) / np.maximum(white, 0.001) * 100
    pct_y = (C - yellow) / np.maximum(yellow, 0.001) * 100

    # (a) 白线30天不死叉黄线 (30分)
    white_above_yellow_30 = EVERY(white >= yellow, 30)
    s_no_death = white_above_yellow_30.astype(float) * 30

    # (b) 贴近黄线: |pct_y|<5%→满分, >50%→0分 (25分)
    s_yellow_near = np.clip((50.0 - np.abs(pct_y)) / 45.0, 0, 1) * 25

    # (c) 贴近白线: |pct_w|<1%→满分, >3%→0分 (20分)
    s_white_near = np.clip((3.0 - np.abs(pct_w)) / 2.0, 0, 1) * 20

    # (d) 黄线趋势向上: 30日内上涨天数>=25 (15分)
    yellow_up = (yellow >= REF(yellow, 1)).astype(float)
    yellow_up_cnt = COUNT(yellow_up, 30)
    s_yellow_trend = np.clip(yellow_up_cnt / 25.0, 0, 1) * 15

    # (e) MA(C,60) 仍在上升 (10分)
    ma60 = MA(C, 60)
    s_ma60_up = (ma60 >= REF(ma60, 1)).astype(float) * 10

    return np.clip(s_no_death + s_yellow_near + s_white_near
                   + s_yellow_trend + s_ma60_up, 0, 100)


def _calc_multiwave_score(C, H, V, yellow):
    """维度5: 多波递进评分 (0-100)"""
    cross_up = CROSS(C, yellow)
    wave_cnt_60 = COUNT(cross_up, 60)
    wave_cnt_90 = COUNT(cross_up, 90)
    s_waves = np.where(wave_cnt_60 >= 3, 40.0,
              np.where(wave_cnt_60 >= 2, 30.0,
              np.where(wave_cnt_60 >= 1, 15.0, 0.0)))

    s_long_waves = np.where(wave_cnt_90 >= 2, 15.0, 0.0)

    hhv_v_30 = HHV(V, 30)
    v_ratio_to_peak = V / np.maximum(hhv_v_30, 1)
    s_shrink_vs_peak = np.clip((0.6 - v_ratio_to_peak) / 0.4, 0, 1) * 25

    hhv_h_20 = HHV(H, 20)
    hhv_h_60 = HHV(H, 60)
    s_high_rising = (hhv_h_20 >= hhv_h_60 * 0.95).astype(float) * 20

    return np.clip(s_waves + s_long_waves + s_shrink_vs_peak + s_high_rising, 0, 100)


def _compute_dynamic_process_scores(C, H, L, O, V, white, yellow,
                                    shrink_score, J, rsi3):
    """计算5维综合评分（排序用，不作为门控条件）"""
    accumulation = _calc_accumulation_score(C, O, V)
    washout = _calc_washout_score(V, shrink_score)
    oversold = _calc_oversold_score(J, rsi3)
    support = _calc_support_score(C, white, yellow)
    multiwave = _calc_multiwave_score(C, H, V, yellow)

    final_score = (accumulation * 0.20
                   + washout * 0.30
                   + oversold * 0.20
                   + support * 0.15
                   + multiwave * 0.15)

    return {
        "accumulation_score": accumulation,
        "washout_score": washout,
        "oversold_score": oversold,
        "support_score": support,
        "multiwave_score": multiwave,
        "final_score": final_score,
    }


# ================================================================== #
#  信号计算                                                            #
# ================================================================== #


def _compute_all_bar_signals(C, H, L, O, V, dates, params, capital_shares=None):
    """完美B1 V2: V4 B1 + 建仓波必要条件 + 四通道OR + 预警过滤"""
    signals = _v4_compute_all_bar_signals(C, H, L, O, V, dates, params,
                                          capital_shares)
    if signals is None:
        return None

    b1_original = signals["b1"].copy()
    white = signals["white"]
    yellow = signals["yellow"]
    shrink_score = signals["shrink_score"]

    # ---- KDJ-J ----
    llv9 = LLV(L, 9)
    hhv9 = HHV(H, 9)
    denom9 = hhv9 - llv9
    rsv = np.where(denom9 != 0, (C - llv9) / denom9 * 100, 50.0)
    K = SMA(rsv, 3, 1)
    D = SMA(K, 3, 1)
    J = 3 * K - 2 * D

    # ---- RSI(3) ----
    diff = C - REF(C, 1)
    diff = np.nan_to_num(diff, nan=0.0)
    up = SMA(np.maximum(diff, 0), 3, 1)
    down = SMA(np.abs(diff), 3, 1)
    rsi3 = np.where(down != 0, up / down * 100, 50.0)

    # ---- 签百分比（vs白线、vs黄线） ----
    pct_w = (C - white) / np.maximum(white, 0.001) * 100
    pct_y = (C - yellow) / np.maximum(yellow, 0.001) * 100

    # ---- V/MA(V,60) ----
    v_ma60 = MA(V, 60)
    v_ratio_60 = V / np.maximum(v_ma60, 1)

    # ---- 四通道OR检测 ----
    channels = _compute_channel_signals(
        C, H, L, O, V, white, yellow, shrink_score, J, rsi3)

    # ---- 5维评分（排序用） ----
    scores = _compute_dynamic_process_scores(
        C, H, L, O, V, white, yellow, shrink_score, J, rsi3)

    # 移除 vol_expand_ok：完美B1核心特征是极致缩量，与前期放量要求互斥
    signals["vol_expand_ok"] = np.ones(len(C), dtype=bool)

    # 完美B1 = V4_B1 & 建仓波存在 & 通道通过 & 非预警
    perfect_b1 = (b1_original
                  & channels["accumulation_exists"]
                  & channels["channel_pass"]
                  & ~channels["is_warning_effective"])
    signals["b1"] = perfect_b1

    # 通道优先级排序（A=1 > C=2 > D=3 > B=4），同通道按评分降序
    channel_priority = np.where(channels["channel_a"], 1.0,
                      np.where(channels["channel_c"], 2.0,
                      np.where(channels["channel_d"], 3.0,
                      np.where(channels["channel_b"], 4.0, 9.0))))
    signals["b2_sort_primary"] = channel_priority * 10000 - scores["final_score"]

    # 保存辅助字段
    signals["b1_original"] = b1_original
    signals["final_score"] = scores["final_score"]
    signals["accumulation_score"] = scores["accumulation_score"]
    signals["washout_score"] = scores["washout_score"]
    signals["oversold_score"] = scores["oversold_score"]
    signals["support_score"] = scores["support_score"]
    signals["multiwave_score"] = scores["multiwave_score"]

    # 通道相关字段
    signals["accumulation_exists"] = channels["accumulation_exists"]
    signals["channel_a"] = channels["channel_a"]
    signals["channel_b"] = channels["channel_b"]
    signals["channel_c"] = channels["channel_c"]
    signals["channel_d"] = channels["channel_d"]
    signals["channel_pass"] = channels["channel_pass"]
    signals["channel_type"] = channels["channel_type"]
    signals["is_warning"] = channels["is_warning"]
    signals["is_warning_effective"] = channels["is_warning_effective"]

    # 指标字段
    signals["J"] = J
    signals["RSI"] = rsi3
    signals["pct_w"] = pct_w
    signals["pct_y"] = pct_y
    signals["v_ratio_60"] = v_ratio_60
    signals["dist_w"] = ABS(C - white) / np.maximum(C, 0.001) * 100
    signals["dist_y"] = ABS(C - yellow) / np.maximum(C, 0.001) * 100

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
        "vol_expand": True,
        "no_huge_vol_bearish": bool(all_bars["no_huge_vol_bearish"][i]),
        "close": float(C[i]),
        "J": float(all_bars["J"][i]),
        "RSI": float(all_bars["RSI"][i]),
        "shrink_score": float(all_bars["shrink_score"][i]),
        "final_score": float(all_bars["final_score"][i]),
        "accumulation_score": float(all_bars["accumulation_score"][i]),
        "washout_score": float(all_bars["washout_score"][i]),
        "oversold_score": float(all_bars["oversold_score"][i]),
        "support_score": float(all_bars["support_score"][i]),
        "multiwave_score": float(all_bars["multiwave_score"][i]),
        "channel_type": int(all_bars["channel_type"][i]),
        "is_warning": bool(all_bars["is_warning"][i]),
        "is_warning_effective": bool(all_bars["is_warning_effective"][i]),
        "pct_w": float(all_bars["pct_w"][i]),
        "pct_y": float(all_bars["pct_y"][i]),
        "v_ratio_60": float(all_bars["v_ratio_60"][i]),
        "b1_original": bool(all_bars["b1_original"][i]),
        "accumulation_exists": bool(all_bars["accumulation_exists"][i]),
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
    """完美B1 V2全市场扫描（不检查大盘MACD，完美B1不受大盘约束）"""
    from concurrent.futures import ProcessPoolExecutor, as_completed

    # 完美B1不受大盘MACD约束
    market_macd_ok = True

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
            pool.submit(_scan_one, code, params, skip_weekly, market_macd_ok): code
            for code in codes
        }
        for future in as_completed(futures):
            code, sig, err = future.result()
            done += 1
            if err:
                errors += 1
            elif sig is not None:
                results.append(sig)
                ct = sig.get("channel_type", 0)
                cname = CHANNEL_NAMES.get(ct, "?")
                fs = sig.get('final_score', 0)
                print(f"  {code}  C={sig['close']:.2f}  "
                      f"评分={fs:.1f}  J={sig['J']:.1f}  "
                      f"缩量={sig['shrink_score']:.3f}  "
                      f"通道={cname}")
            if done % 500 == 0:
                print(f"  ... 已扫描 {done}/{total} ({done/total*100:.0f}%)  "
                      f"命中 {len(results)}  耗时 {time.time()-t0:.1f}s")

    elapsed = time.time() - t0
    results.sort(key=lambda x: x.get("final_score", 0), reverse=True)

    print(f"\n{'=' * 60}")
    print(f"  完美B1 V2扫描完成: {total} 只  命中 {len(results)} 只  "
          f"错误 {errors}  耗时 {elapsed:.1f}s")
    print(f"{'=' * 60}")

    if results:
        print("\n  选股结果（按评分降序）")
        print(f"{'=' * 60}")
        for r in results:
            tag = " <<< TOP" if r == results[0] else ""
            ct = r.get("channel_type", 0)
            cname = CHANNEL_NAMES.get(ct, "?")
            fs = r.get('final_score', 0)
            print(f"  {r['code']}  C={r['close']:.2f}  "
                  f"评分={fs:.1f}  缩量={r['shrink_score']:.3f}  "
                  f"通道={cname}{tag}")

    return results, market_macd_ok


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
    """完美B1 V2预加载（不检查大盘MACD，完美B1不受大盘约束）

    Returns:
        (all_signals, trading_days, None) —
        第三个元素固定为 None，表示不使用大盘MACD过滤
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

    # 完美B1不受大盘MACD约束，固定返回None
    return all_signals, trading_days, None
