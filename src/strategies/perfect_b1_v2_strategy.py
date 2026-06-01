"""完美B1 V2策略 — 基于量价动态过程的5维评分识别

不使用静态阈值模板匹配，而是从量价关系和趋势结构中动态评分：

5维评分体系：
  维度1 建仓强度 (20%): 近期是否存在放量拉升的建仓波
  维度2 缩量洗盘 (30%): 当前成交量相对近期高峰的缩减程度（最核心维度）
  维度3 超卖拐点 (20%): KDJ-J/RSI超卖深度 + 拐点回升确认
  维度4 支撑结构 (15%): 白/黄线位置关系、白线不死叉、均线趋势
  维度5 多波递进 (15%): 上穿黄线次数、量能递进、高低点抬高

核心逻辑：
1. V4 B1信号作为基础（七子条件OR + 盈亏比），移除 vol_expand_ok
2. 计算5维过程评分，加权综合得到 final_score (0-100)
3. 完美B1 = V4_B1 AND (final_score >= 45) AND NOT 预警条件
4. 按综合评分降序排序（评分越高优先级越高）

预警条件：缩量评分>35%（洗盘不充分）AND 5日振幅极小（回调不足）

架构：包装 V4 的 _compute_all_bar_signals()，叠加5维动态评分，
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
    MARKET_INDEX_CODE, MARKET_MACD_FAST, MARKET_MACD_SLOW, MARKET_MACD_SIGNAL,
)
from src.strategies.huangbai_b1_v4_strategy import (
    _compute_all_bar_signals as _v4_compute_all_bar_signals,
    _compute_signals as _v4_compute_signals,
    _get_all_codes,
    compute_market_macd,
    compute_market_macd_for_trading_days,
    load_market_index,
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
#  5维动态评分体系                                                      #
# ================================================================== #

PATTERN_NAMES = {
    0: "不匹配",
    1: "超卖缩量拐头",       # 原型: 华纳药厂
    2: "多波N型递进缩量",     # 原型: 宁波韵升
    3: "倍量柱快速启动",      # 原型: 微芯生物
    4: "双波递进B1",          # 原型: 方正科技
    5: "白线不死叉",          # 原型: 澄天伟业
    6: "深度缩量回调",        # 原型: 国轩高科
    7: "跌破黄线反转",        # 原型: 野马电池
    8: "长期多波极致缩量",    # 原型: 光电股份
    9: "双倍量柱爆发",        # 原型: 新瀚新材
    10: "大牛市快速B1",       # 原型: 昂利康
    11: "预警(缩量不极致)",   # 原型: 赢时胜
}

# 评分阈值
PASS_THRESHOLD = 45       # >= 45 判定为完美B1
WARNING_SHRINK = 0.35     # shrink > 35% 触发预警（洗盘不充分）


def _calc_accumulation_score(C, O, V):
    """维度1: 建仓强度评分 (0-100)

    用多窗口(10/20/40/60日)的放量阳线密度和区间涨幅，
    近似"是否存在带量拉升的建仓波"。
    """
    v_ma20 = MA(V, 20)

    # 放量阳线: C>O 且 V>MA(V,20)*1.5
    big_vol_yang = ((C > O) & (V > v_ma20 * 1.5)).astype(float)
    cnt_bvy_20 = COUNT(big_vol_yang, 20)
    cnt_bvy_40 = COUNT(big_vol_yang, 40)
    cnt_bvy_60 = COUNT(big_vol_yang, 60)

    # 区间涨幅
    rise_10 = (C - REF(C, 10)) / np.maximum(REF(C, 10), 0.001) * 100
    rise_20 = (C - REF(C, 20)) / np.maximum(REF(C, 20), 0.001) * 100
    rise_40 = (C - REF(C, 40)) / np.maximum(REF(C, 40), 0.001) * 100

    # 倍量柱计数 (20日内)
    vol_ratio_prev = V / np.maximum(REF(V, 1), 1)
    double_vol = (vol_ratio_prev >= 2.0).astype(float)
    cnt_dv_20 = COUNT(double_vol, 20)

    # 量价齐升: V > MA(V,20)*2 且 C>O
    vol_price_surge = ((V > v_ma20 * 2) & (C > O)).astype(float)
    cnt_vps_20 = COUNT(vol_price_surge, 20)

    # 短波建仓 (7-15天, +15%~30%)
    short_wave = (np.clip(cnt_bvy_20 / 3.0, 0, 1)
                 * np.clip(rise_10 / 15.0, 0, 1) * 30)

    # 中波建仓 (20-30天, +15%~40%)
    mid_wave = (np.clip(cnt_bvy_40 / 5.0, 0, 1)
               * np.clip(rise_20 / 20.0, 0, 1) * 30)

    # 长波建仓 (40-60天, +30%~80%)
    long_wave = (np.clip(cnt_bvy_60 / 7.0, 0, 1)
                * np.clip(rise_40 / 30.0, 0, 1) * 20)

    # 倍量柱加成
    double_vol_bonus = np.clip(cnt_dv_20 / 3.0, 0, 1) * 10

    # 量价齐升加成
    vps_bonus = np.clip(cnt_vps_20 / 2.0, 0, 1) * 10

    return np.clip(short_wave + mid_wave + long_wave
                   + double_vol_bonus + vps_bonus, 0, 100)


def _calc_washout_score(V, shrink_score):
    """维度2: 缩量洗盘评分 (0-100)

    当前成交量相对近期高峰的缩减程度，越高代表洗盘越极致。
    """
    hhv_v_20 = HHV(V, 20)
    hhv_v_50 = HHV(V, 50)
    v_ma60 = MA(V, 60)

    # (a) shrink极致度: shrink<18%→满分, >40%→0分 (50分)
    _s = np.nan_to_num(shrink_score, nan=1.0)
    s1 = np.clip((0.40 - _s) / (0.40 - 0.18), 0, 1) * 50

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

    J值/RSI超卖深度 + 是否出现拐点回升。
    """
    _j = np.nan_to_num(J, nan=50.0)
    _r = np.nan_to_num(rsi3, nan=50.0)

    # (a) J值超卖深度: J<-10→满分, J>15→0分 (40分)
    j_score = np.clip((15.0 - _j) / (15.0 + 10.0), 0, 1) * 40

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

    白/黄线位置关系、白线不死叉、均线趋势。
    """
    pct_w = (C - white) / np.maximum(white, 0.001) * 100
    pct_y = (C - yellow) / np.maximum(yellow, 0.001) * 100

    # (a) 白线30天不死叉黄线 (30分)
    white_above_yellow_30 = EVERY(white >= yellow, 30)
    s_no_death = white_above_yellow_30.astype(float) * 30

    # (b) 贴近黄线: |pct_y|<1%→满分, >5%→0分 (25分)
    s_yellow_near = np.clip((5.0 - np.abs(pct_y)) / 4.0, 0, 1) * 25

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
    """维度5: 多波递进评分 (0-100)

    上穿黄线次数 + 量能递进 + 高点抬高，识别多波段N型结构。
    """
    # (a) 60日内上穿黄线次数 (40分)
    cross_up = CROSS(C, yellow)
    wave_cnt_60 = COUNT(cross_up, 60)
    wave_cnt_90 = COUNT(cross_up, 90)
    s_waves = np.where(wave_cnt_60 >= 3, 40.0,
              np.where(wave_cnt_60 >= 2, 30.0,
              np.where(wave_cnt_60 >= 1, 15.0, 0.0)))

    # (b) 90日内多波 (15分)
    s_long_waves = np.where(wave_cnt_90 >= 2, 15.0, 0.0)

    # (c) 量能递进缩量: V/HHV(V,30) < 50% 说明缩到近期峰量一半以下 (25分)
    hhv_v_30 = HHV(V, 30)
    v_ratio_to_peak = V / np.maximum(hhv_v_30, 1)
    s_shrink_vs_peak = np.clip((0.6 - v_ratio_to_peak) / 0.4, 0, 1) * 25

    # (d) 高点抬高: 近20日高点接近近60日高点 (20分)
    hhv_h_20 = HHV(H, 20)
    hhv_h_60 = HHV(H, 60)
    s_high_rising = (hhv_h_20 >= hhv_h_60 * 0.95).astype(float) * 20

    return np.clip(s_waves + s_long_waves + s_shrink_vs_peak + s_high_rising, 0, 100)


def _classify_pattern(scores, C, white, yellow, shrink_score):
    """根据5维子分确定模式分类编号"""
    acc = scores["accumulation_score"]
    wash = scores["washout_score"]
    over = scores["oversold_score"]
    supp = scores["support_score"]
    multi = scores["multiwave_score"]

    _s = np.nan_to_num(shrink_score, nan=1.0)
    pct_y = (C - yellow) / np.maximum(yellow, 0.001) * 100

    pattern_type = np.zeros(len(C), dtype=int)

    # 按优先级从低到高赋值（高优先级后写覆盖）
    # P5 白线不死叉
    mask5 = (supp > 70) & (wash > 40)
    pattern_type[mask5] = 5

    # P10 大牛市快速B1
    mask10 = (pct_y > 30) & (wash > 50) & (acc > 50)
    pattern_type[mask10] = 10

    # P4 双波递进B1
    mask4 = (multi >= 30) & (multi < 50) & (wash > 50)
    pattern_type[mask4] = 4

    # P2 多波N型递进缩量
    mask2 = (multi > 50) & (wash > 50)
    pattern_type[mask2] = 2

    # P6 深度缩量回调
    mask6 = (wash > 70) & (supp > 50)
    pattern_type[mask6] = 6

    # P9 双倍量柱爆发
    mask9 = (acc > 50) & (multi > 30) & (wash > 60)
    pattern_type[mask9] = 9

    # P3 倍量柱快速启动
    mask3 = (acc > 60) & (over > 60)
    pattern_type[mask3] = 3

    # P7 跌破黄线反转
    mask7 = (C < yellow) & (over > 60) & (wash > 50)
    pattern_type[mask7] = 7

    # P1 超卖缩量拐头
    mask1 = (over > 60) & (wash > 50) & (np.abs(pct_y) <= 5)
    pattern_type[mask1] = 1

    # P8 长期多波极致缩量
    mask8 = (wash > 80) & (multi > 40)
    pattern_type[mask8] = 8

    # P11 预警
    mask11 = _s > WARNING_SHRINK
    pattern_type[mask11] = 11

    return pattern_type


def _compute_dynamic_process_scores(C, H, L, O, V, white, yellow,
                                    shrink_score, J, rsi3, b1_original):
    """计算5维动态过程评分，替代旧的 _compute_pattern_matches()"""
    # 5个维度评分
    accumulation = _calc_accumulation_score(C, O, V)
    washout = _calc_washout_score(V, shrink_score)
    oversold = _calc_oversold_score(J, rsi3)
    support = _calc_support_score(C, white, yellow)
    multiwave = _calc_multiwave_score(C, H, V, yellow)

    # 加权综合评分
    final_score = (accumulation * 0.20
                   + washout * 0.30
                   + oversold * 0.20
                   + support * 0.15
                   + multiwave * 0.15)

    # 模式分类
    pattern_type = _classify_pattern(
        {"accumulation_score": accumulation, "washout_score": washout,
         "oversold_score": oversold, "support_score": support,
         "multiwave_score": multiwave},
        C, white, yellow, shrink_score)

    # 完美B1判定: 评分达标
    is_perfect_b1 = final_score >= PASS_THRESHOLD

    # 预警: 缩量不极致 + 回调不充分
    _s = np.nan_to_num(shrink_score, nan=1.0)
    daily_amp = (H - L) / np.maximum(L, 0.001) * 100
    low_amp_5d = MA(daily_amp, 5)
    is_warning = (_s > WARNING_SHRINK) & (low_amp_5d < 3.0)

    return {
        "accumulation_score": accumulation,
        "washout_score": washout,
        "oversold_score": oversold,
        "support_score": support,
        "multiwave_score": multiwave,
        "final_score": final_score,
        "pattern_type": pattern_type,
        "is_perfect_b1": is_perfect_b1,
        "is_warning": is_warning,
    }


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
    # ---- 5维动态评分 ----
    scores = _compute_dynamic_process_scores(
        C, H, L, O, V, white, yellow, shrink_score, J, rsi3, b1_original)

    # 移除 vol_expand_ok：完美B1核心特征是极致缩量，与前期放量要求互斥
    signals["vol_expand_ok"] = np.ones(len(C), dtype=bool)

    # 完美B1 = V4 B1 & 评分达标 & 非预警
    is_perfect = scores["is_perfect_b1"] & ~scores["is_warning"]
    signals["b1"] = b1_original & is_perfect

    # 保存辅助字段
    signals["b1_original"] = b1_original
    signals["final_score"] = scores["final_score"]
    signals["accumulation_score"] = scores["accumulation_score"]
    signals["washout_score"] = scores["washout_score"]
    signals["oversold_score"] = scores["oversold_score"]
    signals["support_score"] = scores["support_score"]
    signals["multiwave_score"] = scores["multiwave_score"]
    signals["pattern_type"] = scores["pattern_type"]
    signals["is_warning"] = scores["is_warning"]
    signals["J"] = J
    signals["RSI"] = rsi3
    signals["pct_w"] = pct_w
    signals["pct_y"] = pct_y
    signals["v_ratio_60"] = v_ratio_60
    signals["dist_w"] = ABS(C - white) / np.maximum(C, 0.001) * 100
    signals["dist_y"] = ABS(C - yellow) / np.maximum(C, 0.001) * 100

    # 排序字段：使用 -final_score 实现评分降序（PortfolioSimulator 按升序取最优）
    signals["b2_sort_primary"] = -scores["final_score"]

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
        "vol_expand": True,  # 完美B1移除vol_expand_ok过滤
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
        "pattern_type": int(all_bars["pattern_type"][i]),
        "is_warning": bool(all_bars["is_warning"][i]),
        "pct_w": float(all_bars["pct_w"][i]),
        "pct_y": float(all_bars["pct_y"][i]),
        "v_ratio_60": float(all_bars["v_ratio_60"][i]),
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
    """完美B1 V2全市场扫描（含大盘MACD多头过滤）"""
    from concurrent.futures import ProcessPoolExecutor, as_completed

    # 计算大盘MACD状态
    market_macd_ok = True
    market_df = load_market_index(tdxdir, market)
    if market_df is not None:
        market_close = market_df["close"].values.astype(float)
        _, _, market_bullish = compute_market_macd(market_close)
        market_macd_ok = bool(market_bullish[-1])
        status = "多头" if market_macd_ok else "空头(只卖不买)"
        print(f"  大盘MACD状态: {status}")
    else:
        print("  警告: 无法加载大盘指数数据，大盘MACD过滤将被跳过")

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
                pt = sig.get("pattern_type", 0)
                pname = PATTERN_NAMES.get(pt, "?")
                fs = sig.get('final_score', 0)
                print(f"  {code}  C={sig['close']:.2f}  "
                      f"评分={fs:.1f}  J={sig['J']:.1f}  "
                      f"缩量={sig['shrink_score']:.3f}  "
                      f"模式={pname}")
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
            pt = r.get("pattern_type", 0)
            pname = PATTERN_NAMES.get(pt, "?")
            fs = r.get('final_score', 0)
            print(f"  {r['code']}  C={r['close']:.2f}  "
                  f"评分={fs:.1f}  缩量={r['shrink_score']:.3f}  "
                  f"模式={pname}{tag}")

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
    """完美B1 V2预加载（含大盘MACD多头过滤）

    Returns:
        (all_signals, trading_days, market_macd_bullish) —
        market_macd_bullish 为每日大盘MACD多头布尔数组
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

    # 计算大盘MACD多头状态
    market_macd_bullish = None
    if len(trading_days) > 0:
        print("  计算大盘MACD状态...")
        market_macd_bullish = compute_market_macd_for_trading_days(
            trading_days, tdxdir, market)
        if market_macd_bullish is not None:
            bull_count = np.sum(market_macd_bullish)
            total_days = len(market_macd_bullish)
            print(f"  大盘MACD多头天数: {bull_count}/{total_days} "
                  f"({bull_count/total_days*100:.1f}%)")

    return all_signals, trading_days, market_macd_bullish
