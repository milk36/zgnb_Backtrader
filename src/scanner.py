"""全市场选股扫描器 — 不依赖 Backtrader，直接用 MyTT 计算"""

import os
import re
import time
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore", category=RuntimeWarning)
from mootdx.reader import Reader
from MyTT import (
    EMA, MA, SMA, HHV, LLV, REF, COUNT, EVERY, EXIST,
    CROSS, MAX, ABS, BARSLAST, HHVBARS,
)

from config import TDX_DIR, TDX_MARKET


# ---------- 工具函数 ----------

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


def _weekly_ma(daily_close, dates, period):
    s = pd.Series(daily_close, index=pd.to_datetime(dates))
    weekly = s.resample("W-FRI").last().dropna()
    wma = weekly.rolling(period).mean()
    return wma.reindex(s.index, method="ffill").values


def _get_all_codes(tdxdir=TDX_DIR, market=TDX_MARKET):
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
            # SZ A股: 000/001/002/003/300/301
            if prefix == "sz" and code[:3] in ("000", "001", "002", "003", "300", "301"):
                codes.add(code)
            # SH A股: 600/601/603/605/688/689
            elif prefix == "sh" and code[:3] in ("600", "601", "603", "605", "688", "689"):
                codes.add(code)
    return sorted(codes)


# ---------- 核心信号计算 ----------

def compute_signals(C, H, L, O, V, dates, params):
    """
    计算最新 bar 的三级过滤结果
    返回 dict 或 None（数据不足时）
    """
    n = len(C)
    if n < 300:
        return None

    LC = REF(C, 1)

    # 白线 / 黄线
    white = EMA(EMA(C, 10), 10)
    yellow = (MA(C, params["m1"]) + MA(C, params["m2"])
              + MA(C, params["m3"]) + MA(C, params["m4"])) / 4
    bbi = (MA(C, 3) + MA(C, 6) + MA(C, 12) + MA(C, 24)) / 4

    # RSI
    rsi = SMA(MAX(C - LC, 0), 3, 1) / SMA(ABS(C - LC), 3, 1) * 100

    # KDJ
    llv9 = LLV(L, 9)
    hhv9 = HHV(H, 9)
    denom = hhv9 - llv9
    rsv = np.where(denom != 0, (C - llv9) / denom * 100, 50.0)
    K = SMA(rsv, 3, 1)
    D = SMA(K, 3, 1)
    J = 3 * K - 2 * D

    # SHORT / LONG
    s_denom = HHV(C, params["n1"]) - LLV(L, params["n1"])
    SHORT = np.where(s_denom != 0, 100 * (C - LLV(L, params["n1"])) / s_denom, 50.0)
    l_denom = HHV(C, params["n2"]) - LLV(L, params["n2"])
    LONG = np.where(l_denom != 0, 100 * (C - LLV(L, params["n2"])) / l_denom, 50.0)

    i = n - 1  # 最新 bar

    # --- 周线多头 ---
    ma30w = _weekly_ma(C, dates, params["wma30"])
    ma60w = _weekly_ma(C, dates, params["wma60"])
    ma120w = _weekly_ma(C, dates, params["wma120"])
    ma240w = _weekly_ma(C, dates, params["wma240"])
    valid = all(v > 0.01 for v in [ma30w[i], ma60w[i], ma120w[i], ma240w[i]])
    weekly_ok = valid and ma30w[i] > ma60w[i] > ma120w[i] > ma240w[i]
    above_ma30w = C[i] > ma30w[i]

    # --- 黄白线金叉 ---
    gc_arr = CROSS(white, yellow)
    bars_gc = np.asarray(BARSLAST(gc_arr), dtype=float)
    gc_ok = bars_gc[i] <= params["gc_lookback"]

    # --- B1 子条件（仅在前面通过时计算） ---
    if not (weekly_ok and above_ma30w and gc_ok):
        return {
            "weekly": weekly_ok and above_ma30w,
            "gc": gc_ok,
            "b1": False,
            "close": C[i], "J": J[i], "RSI": rsi[i],
            "shrink_score": 0,
        }

    # 振幅 / 异动
    is_tech = params["stock_type"] == "tech"
    pct_change = np.where(LC > 0, C / LC - 1, 0.0)
    volatile = EXIST(pct_change > 0.15, 200)
    is_volatile = volatile | is_tech
    amp_range = 8.0 if is_volatile[i] else 5.0
    relax = 0.9 if is_volatile[i] else 1.0

    daily_amp = (H[i] - L[i]) / L[i] * 100
    daily_pct = abs(C[i] - C[i - 1]) / C[i - 1] * 100 * relax
    up_doji = (C[i] > C[i - 1]) and (abs(C[i] - O[i]) / O[i] * 100 * relax < 1.8)

    needle_20 = ((SHORT <= 20) & (LONG >= 75)) | ((LONG - SHORT) >= 70)
    treasure = (COUNT(LONG >= 75, 8) >= 6) & (COUNT(SHORT <= 70, 7) >= 4) & (COUNT(SHORT <= 50, 8) >= 1)
    dbl_fork = EVERY(LONG >= 75, 8) & (COUNT(SHORT <= 50, 6) >= 2) & (COUNT(SHORT <= 20, 7) >= 1)
    red_green = (COUNT(C >= O, 15) > 7) | (COUNT(C > REF(C, 1), 11) > 5)

    near_amp = (HHV(H, params["n"]) - LLV(L, params["n"])) / LLV(L, params["n"]) * 100
    far_amp = (HHV(H, params["m"]) - LLV(L, params["m"])) / LLV(L, params["m"]) * 100
    near_ano = (near_amp[i] >= 15) or ((HHV(H, 12)[i] - LLV(L, 14)[i]) / LLV(L, 14)[i] * 100 >= 11)
    far_ano = far_amp[i] >= 30
    super_ano = near_amp[i] >= 60
    wash_ano = (COUNT(needle_20, 10)[i] >= 2) or treasure[i] or dbl_fork[i]
    anomaly = near_ano or far_ano or wash_ano

    # 成交量
    vday_arr = HHVBARS(V, 40)
    vday = int(vday_arr[i]) if not np.isnan(vday_arr[i]) else 0
    idx_vd = i - vday
    idx_vd1 = i - vday - 1
    if idx_vd >= 0 and idx_vd1 >= 0:
        not_big_green = (C[idx_vd] >= C[idx_vd1]) or (C[idx_vd] >= O[idx_vd])
    else:
        not_big_green = True
    big_green = not not_big_green
    big_green_far = vday >= 15 and big_green
    ok_green = not_big_green or big_green_far

    hhv_v20 = HHV(V, 20)
    hhv_v50 = HHV(V, 50)
    llv_v20 = LLV(V, 20)
    shrink = (V[i] < hhv_v20[i] * 0.416) or (V[i] < hhv_v50[i] / 3)
    pb_shrink = (V[i] < hhv_v20[i] * 0.45) or (V[i] < hhv_v50[i] / 3)
    mod_shrink = (V[i] < hhv_v20[i] * 0.618) or (V[i] < hhv_v50[i] / 3)
    sup_shrink = (V[i] < HHV(V, 30)[i] / 4) or (V[i] < hhv_v50[i] / 6)

    # 缩量评分（用于排序）
    shrink_score = V[i] / hhv_v20[i] if hhv_v20[i] > 0 else 1.0

    # 趋势状态
    uptrend = (white[i] >= yellow[i] * 0.999) and (
        (C[i] >= yellow[i]) or ((C[i] > yellow[i] * 0.975) and (C[i] > O[i]))
    )

    strong_trend = (
        EVERY(yellow >= REF(yellow, 1) * 0.999, 13)[i]
        and (white[i] >= REF(white, 1)[i])
        and EVERY(white > yellow, 20)[i]
        and EVERY(white >= REF(white, 1), 11)[i]
        and red_green[i]
    )

    cross_c_y = CROSS(C, yellow)
    bars_cross_cy = BARSLAST(cross_c_y)
    super_bull = (
        (EVERY(bbi >= REF(bbi, 1) * 0.999, 20)[i] or COUNT(bbi >= REF(bbi, 1), 25)[i] >= 23)
        and (near_amp[i] >= 30 or far_amp[i] > 80)
        and (bars_cross_cy[i] > 12)
    )

    # 回踩距离
    dist_w = abs(C[i] - white[i]) / C[i] * 100
    dist_wL = abs(L[i] - white[i]) / white[i] * 100
    dist_bbi = abs(C[i] - bbi[i]) / C[i] * 100
    dist_bbiL = abs(L[i] - bbi[i]) / bbi[i] * 100
    dist_y = abs(C[i] - yellow[i]) / yellow[i] * 100

    pb_white = (
        (C[i] >= white[i] and dist_w <= 2)
        or (C[i] < white[i] and dist_w < 0.8)
        or (C[i] >= bbi[i] and dist_bbi < 2.5 and dist_bbiL < 1
            and dist_w <= 3 and daily_pct < 1 and C[i] > C[i - 1])
    )
    white_sup = C[i] >= white[i] and dist_w < 1.5
    strong_pb_hold = (dist_wL < 1 or dist_bbiL < 0.5) and C[i] > white[i] and dist_w <= 3.5
    pb_yellow = (
        (C[i] >= yellow[i] and (dist_y <= 1.5 or (dist_y <= 2 and daily_pct < 1)))
        or (C[i] < yellow[i] and dist_y <= 0.8)
    )

    rsi_j = rsi + J

    # B1 子条件
    b1 = False

    # 1. 超卖缩量拐头B
    if (uptrend
            and (rsi[i] - 15 >= rsi[i - 1])
            and (rsi[i - 1] < 20 or J[i - 1] < 14)
            and daily_amp < amp_range + 0.5
            and (daily_pct < 2.3 or (up_doji and daily_pct < 4))
            and ok_green and anomaly and C[i] >= yellow[i]):
        b1 = True

    # 2. 超卖缩量B
    if not b1 and (
        uptrend
        and (J[i] < 14 or rsi[i] < 23)
        and (rsi_j[i] < 55 or J[i] == LLV(J, 20)[i])
        and daily_amp < amp_range
        and (daily_pct < 2.5 or up_doji)
        and ok_green
        and (shrink or (mod_shrink and daily_pct < 1))
        and anomaly
    ):
        b1 = True

    # 3. 原始B1
    if not b1 and (
        white[i] > yellow[i]
        and C[i] >= yellow[i] * 0.99
        and yellow[i] >= yellow[i - 1]
        and (J[i] < 13 or rsi[i] < 21)
        and rsi_j[i] < LLV(rsi_j, 15)[i] * 1.5
        and mod_shrink and ok_green
        and (
            abs(C[i] - O[i]) * 100 / O[i] < 1.5
            or (sup_shrink or (mod_shrink and V[i] < llv_v20[i] * 1.1 and J[i] == LLV(J, 20)[i]))
            or (mod_shrink and (dist_w < 1.8 or dist_bbi < 1.5 or dist_y < 2.8))
        )
        and anomaly
    ):
        b1 = True

    # 4. 超卖超缩量B
    if not b1 and (
        uptrend
        and (J[i] < 14 or rsi[i] < 23)
        and rsi_j[i] < 60 and far_amp[i] >= 45
        and (daily_amp < amp_range
             or (super_ano and daily_amp < amp_range + 3.2 and C[i] > O[i] and C[i] > white[i]))
        and ((C[i] < O[i] and V[i] < V[i - 1] and C[i] >= yellow[i]) or C[i] >= O[i])
        and (daily_pct < 2 or up_doji)
        and ok_green and sup_shrink and anomaly
    ):
        b1 = True

    # 5. 回踩白线B
    if not b1 and (
        strong_trend
        and (J[i] < 30 or rsi[i] < 40 or wash_ano)
        and rsi_j[i] < 70
        and (daily_amp < amp_range + 0.5 or dist_w < 1 or dist_bbi < 1)
        and pb_white
        and (daily_pct < 2 or (daily_pct < 5 and white_sup))
        and ok_green and pb_shrink and anomaly and L[i] <= C[i - 1]
    ):
        b1 = True

    # 6. 回踩超级B
    if not b1 and (
        super_bull
        and (J[i] < 35 or rsi[i] < 45 or wash_ano)
        and rsi_j[i] < 80 and rsi_j[i] == LLV(rsi_j, 25)[i]
        and daily_amp < amp_range + 1
        and (daily_pct < 2.5 or dist_w < 2)
        and strong_pb_hold and ok_green and anomaly and mod_shrink
    ):
        b1 = True

    # 7. 回踩黄线B
    if not b1 and (
        white[i] >= yellow[i]
        and C[i] >= yellow[i] * 0.975
        and (J[i] < 13 or rsi[i] < 18)
        and pb_yellow and ok_green
        and (shrink or (mod_shrink and (J[i] == LLV(J, 20)[i] or rsi[i] == LLV(rsi, 14)[i])))
        and yellow[i] >= yellow[i - 1] * 0.997
        and MA(C, 60)[i] >= REF(MA(C, 60), 1)[i]
        and near_amp[i] >= 11.9 and far_amp[i] >= 19.5
    ):
        b1 = True

    return {
        "weekly": True,
        "gc": True,
        "b1": b1,
        "close": C[i],
        "J": J[i],
        "RSI": rsi[i],
        "shrink_score": shrink_score,
    }


# ---------- 扫描入口 ----------

def scan_all(stock_type="main", skip_weekly=False, skip_gc=False,
             tdxdir=TDX_DIR, market=TDX_MARKET):
    """全市场扫描，返回符合条件的股票列表"""
    reader = Reader.factory(market=market, tdxdir=tdxdir)
    codes = _get_all_codes(tdxdir)
    print(f"扫描 {len(codes)} 只A股...")

    params = {
        "m1": 14, "m2": 28, "m3": 57, "m4": 114,
        "n": 20, "m": 50, "n1": 3, "n2": 21,
        "wma30": 30, "wma60": 60, "wma120": 120, "wma240": 240,
        "gc_lookback": 20,
        "stock_type": stock_type,
    }

    results = []
    t0 = time.time()
    errors = 0

    for idx, code in enumerate(codes):
        try:
            df = reader.daily(symbol=code)
            if df is None or len(df) < 300:
                continue
            df = df.sort_index()
            C = df["close"].values.astype(float)
            H = df["high"].values.astype(float)
            L = df["low"].values.astype(float)
            O = df["open"].values.astype(float)
            V = df["volume"].values.astype(float)
            dates = df.index

            sig = compute_signals(C, H, L, O, V, dates, params)
            if sig is None:
                continue

            # 模拟 skip 参数
            weekly_ok = skip_weekly or sig["weekly"]
            gc_ok = skip_gc or sig["gc"]
            b1_ok = sig["b1"]

            if b1_ok and weekly_ok and gc_ok:
                results.append({
                    "code": code,
                    "close": sig["close"],
                    "J": sig["J"],
                    "RSI": sig["RSI"],
                    "shrink_score": sig["shrink_score"],
                    "weekly": sig["weekly"],
                    "gc": sig["gc"],
                    "b1": sig["b1"],
                })
                print(f"  [{df.index[-1].strftime('%Y-%m-%d')}] {code}  "
                      f"C={sig['close']:.2f}  J={sig['J']:.1f}  RSI={sig['RSI']:.1f}  "
                      f"缩量={sig['shrink_score']:.3f}")

        except Exception:
            errors += 1
            continue

        # 进度
        if (idx + 1) % 500 == 0:
            elapsed = time.time() - t0
            pct = (idx + 1) / len(codes) * 100
            print(f"  ... 已扫描 {idx + 1}/{len(codes)} ({pct:.0f}%)  "
                  f"命中 {len(results)}  耗时 {elapsed:.1f}s")

    elapsed = time.time() - t0

    # 按缩量排序（越小越缩量）
    results.sort(key=lambda x: x["shrink_score"])

    print(f"\n{'=' * 55}")
    print(f"  扫描完成: {len(codes)} 只  命中 {len(results)} 只  "
          f"错误 {errors}  耗时 {elapsed:.1f}s")
    print(f"{'=' * 55}")

    if results:
        print(f"\n{'=' * 55}")
        print(f"  选股结果（按缩量排序，越小越优先）")
        print(f"{'=' * 55}")
        for r in results:
            tag = " <<< TOP" if r == results[0] else ""
            print(f"  {r['code']}  C={r['close']:.2f}  "
                  f"J={r['J']:.1f}  RSI={r['RSI']:.1f}  "
                  f"缩量={r['shrink_score']:.3f}{tag}")

    return results
