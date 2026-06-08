"""ML 特征提取模块

从 V4 _compute_all_bar_signals() 返回的信号字典及 OHLCV 数据中，
提取约 30 个特征用于 LightGBM 三分类模型训练。

特征分四组：
  - 动量指标 (8个): rsi3, J, K, D, SHORT, LONG, j_turn, rsi_turn
  - 距离/趋势 (8个): dist_w, dist_y, dist_bbi, pct_w, pct_y,
                     wy_gap_pct, white_slope, yellow_slope
  - 振幅/量能 (8个): near_amp, far_amp, daily_amp, shrink_score,
                     vol_ratio_60, vol_vs_hhv50, chip_spread, rr_reward_risk
  - B1 子条件 (7个): b_oversold_turn, b_oversold_shrink, b_raw,
                     b_oversold_super, b_pb_white, b_pb_super, b_pb_yellow
"""

import numpy as np
import pandas as pd
from MyTT import EMA, MA, SMA, HHV, LLV, REF, ABS, MAX, COUNT


# ---------------------------------------------------------------------------
# 特征名列表（与 compute_feature_arrays 返回键完全一致）
# ---------------------------------------------------------------------------
FEATURE_NAMES: list[str] = [
    # 动量指标
    "rsi3", "J", "K", "D", "SHORT", "LONG", "j_turn", "rsi_turn",
    # 距离/趋势
    "dist_w", "dist_y", "dist_bbi", "pct_w", "pct_y",
    "wy_gap_pct", "white_slope", "yellow_slope",
    # 振幅/量能
    "near_amp", "far_amp", "daily_amp", "shrink_score",
    "vol_ratio_60", "vol_vs_hhv50", "chip_spread", "rr_reward_risk",
    # B1 子条件
    "b_oversold_turn", "b_oversold_shrink", "b_raw",
    "b_oversold_super", "b_pb_white", "b_pb_super", "b_pb_yellow",
]


def compute_feature_arrays(
    signals: dict,
    C: np.ndarray,
    H: np.ndarray,
    L: np.ndarray,
    O: np.ndarray,
    V: np.ndarray,
    dates,
    params: dict,
) -> dict:
    """为所有 bar 计算 ML 特征数组（向量化）。

    Args:
        signals: V4 ``_compute_all_bar_signals()`` 返回的信号字典。
        C, H, L, O, V: OHLCV numpy 数组。
        dates: 日期索引（当前特征计算未使用，保留接口一致性）。
        params: 策略参数字典，需要 ``n1``, ``n2``, ``n``, ``m`` 等键。

    Returns:
        ``{feature_name: numpy_array}`` 字典，每个数组长度为 ``len(C)``。
    """
    n = len(C)
    result = {}

    # ---- 从 signals 中直接获取的字段 ----
    result["shrink_score"] = signals.get("shrink_score", np.ones(n))
    result["chip_spread"] = signals.get("chip_spread", np.zeros(n))

    # ---- 从 signals 中获取均线 ----
    white = signals["white"]
    yellow = signals["yellow"]
    bbi = signals["bbi"]

    # ---- KDJ 指标 ----
    LC = REF(C, 1)
    rsi = SMA(MAX(C - LC, 0), 3, 1) / SMA(ABS(C - LC), 3, 1) * 100

    llv9, hhv9 = LLV(L, 9), HHV(H, 9)
    denom9 = hhv9 - llv9
    rsv = np.where(denom9 != 0, (C - llv9) / denom9 * 100, 50.0)
    K = SMA(rsv, 3, 1)
    D = SMA(K, 3, 1)
    J = 3 * K - 2 * D

    # ---- SHORT / LONG 位置百分位 ----
    n1 = params.get("n1", 3)
    n2 = params.get("n2", 21)
    s_denom = HHV(C, n1) - LLV(L, n1)
    SHORT = np.where(s_denom != 0, 100 * (C - LLV(L, n1)) / s_denom, 50.0)
    l_denom = HHV(C, n2) - LLV(L, n2)
    LONG = np.where(l_denom != 0, 100 * (C - LLV(L, n2)) / l_denom, 50.0)

    # ---- 动量指标 (8) ----
    result["rsi3"] = rsi
    result["J"] = J
    result["K"] = K
    result["D"] = D
    result["SHORT"] = SHORT
    result["LONG"] = LONG
    result["j_turn"] = J - REF(J, 1)
    result["rsi_turn"] = rsi - REF(rsi, 1)

    # ---- 距离 / 趋势 (8) ----
    result["dist_w"] = ABS(C - white) / np.maximum(C, 0.001) * 100
    result["dist_y"] = ABS(C - yellow) / np.maximum(C, 0.001) * 100
    result["dist_bbi"] = ABS(C - bbi) / np.maximum(C, 0.001) * 100
    result["pct_w"] = (C - white) / np.maximum(white, 0.001) * 100
    result["pct_y"] = (C - yellow) / np.maximum(yellow, 0.001) * 100
    result["wy_gap_pct"] = (white - yellow) / np.maximum(yellow, 0.001) * 100

    ref_white_5 = REF(white, 5)
    ref_yellow_5 = REF(yellow, 5)
    result["white_slope"] = np.where(
        ref_white_5 > 0, (white - ref_white_5) / ref_white_5 * 100, 0
    )
    result["yellow_slope"] = np.where(
        ref_yellow_5 > 0, (yellow - ref_yellow_5) / ref_yellow_5 * 100, 0
    )

    # ---- 振幅 (3) ----
    nn = params.get("n", 20)
    mm = params.get("m", 50)
    near_amp = (HHV(H, nn) - LLV(L, nn)) / np.maximum(LLV(L, nn), 0.001) * 100
    far_amp = (HHV(H, mm) - LLV(L, mm)) / np.maximum(LLV(L, mm), 0.001) * 100
    daily_amp = (H - L) / np.maximum(L, 0.001) * 100
    result["near_amp"] = near_amp
    result["far_amp"] = far_amp
    result["daily_amp"] = daily_amp

    # ---- 量能 (2) ----
    ma60 = MA(V, 60)
    result["vol_ratio_60"] = V / np.maximum(ma60, 1)
    result["vol_vs_hhv50"] = V / np.maximum(HHV(V, 50), 1)

    # ---- 盈亏比 (1) ----
    wave_high = _compute_wave_high(C, H, O, yellow)
    rr_risk = np.maximum(C - yellow * 0.99, 0.001)
    result["rr_reward_risk"] = (wave_high - C) / rr_risk

    # ---- B1 子条件 (7): 优先从 signals 获取，否则为 0 ----
    b1_sub_keys = [
        "b_oversold_turn",
        "b_oversold_shrink",
        "b_raw",
        "b_oversold_super",
        "b_pb_white",
        "b_pb_super",
        "b_pb_yellow",
    ]
    for key in b1_sub_keys:
        if key in signals:
            result[key] = signals[key].astype(float)
        else:
            result[key] = np.zeros(n, dtype=float)

    return result


def extract_features_at_bar(feature_arrays: dict, idx: int) -> dict:
    """提取单 bar 特征为扁平字典，NaN 替换为 0。

    Args:
        feature_arrays: ``compute_feature_arrays()`` 的返回值。
        idx: bar 索引。

    Returns:
        ``{feature_name: float_value}`` 字典，不含 NaN。
    """
    row = {}
    for name in FEATURE_NAMES:
        val = feature_arrays[name][idx]
        row[name] = 0.0 if np.isnan(val) else float(val)
    return row


def _compute_wave_high(
    C: np.ndarray,
    H: np.ndarray,
    O: np.ndarray,
    yellow: np.ndarray,
) -> np.ndarray:
    """计算前一波高点（C >= 黄线时跟踪阳线波峰）。

    与 V4 ``_compute_all_bar_signals()`` 中的 wave_high 逻辑一致：
    当收盘价在黄线之上且为阳线时更新峰值，否则保持前值。
    """
    n = len(C)
    wave_high = np.empty(n)
    peak = 0.0
    for i in range(n):
        if C[i] >= yellow[i] and C[i] >= O[i]:
            peak = max(peak, H[i])
        wave_high[i] = peak
    return wave_high
