"""黄白线金叉后B1策略

策略逻辑：
1. 周线多头空间（周线MA30>MA60>MA120>MA240）
2. 黄白线金叉（近N日内发生）
3. B1买入信号（7个子条件OR）
4. 止损：白线上方买入→买入日最低价；白线黄线之间→黄线价
5. T+3没涨清仓
6. 止盈：中阳卖1/3，涨停卖1/2，仓位半仓后持股至跌破白线
"""

import os
import re
import time
import warnings

import numpy as np
import pandas as pd
import backtrader as bt

warnings.filterwarnings("ignore", category=RuntimeWarning)
from MyTT import (
    EMA, MA, SMA, HHV, LLV, REF, COUNT, EVERY, EXIST,
    CROSS, MAX, ABS, IF, BARSLAST, HHVBARS, BBI as MyTT_BBI,
)
from mootdx.reader import Reader
from src.indicators.kdj_indicator import KDJIndicator
from src.strategies.base_strategy import BaseStrategy

from config import TDX_DIR, TDX_MARKET, SCAN_MAX_WORKERS


# ---------- helper ----------

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


def _weekly_ma(daily_close, dates, period):
    """将日线数据重采样为周线，计算MA，映射回日线频率"""
    s = pd.Series(daily_close, index=pd.to_datetime(dates))
    weekly = s.resample('W-FRI').last().dropna()
    wma = weekly.rolling(period).mean()
    return wma.reindex(s.index, method='ffill').values


# ---------- strategy ----------

class HuangBaiB1Strategy(BaseStrategy):
    """
    周线多头空间 + 黄白线金叉 + B1 买入信号
    入场: 周线多头 AND 近期黄白线金叉 AND B1条件满足
    出场: 止损 / T+3 / 止盈 / 跌破白线
    """

    params = (
        ("print_log", True),
        ("position_pct", 0.1),       # 10万/100万
        ("stock_type", "main"),       # "main" / "tech"

        # 黄白线参数
        ("m1", 14), ("m2", 28), ("m3", 57), ("m4", 114),

        # B1 振幅参数
        ("n", 20), ("m", 50), ("n1", 3), ("n2", 21),

        # 止损止盈
        ("t_plus_n", 3),

        # 周线MA周期（实际周线，非日线近似）
        ("wma30", 30), ("wma60", 60), ("wma120", 120), ("wma240", 240),

        # 金叉回溯
        ("gc_lookback", 20),

        # 调试：跳过周线/金叉过滤，仅测试B1信号
        ("skip_weekly", False),
        ("skip_gc", False),
    )

    def __init__(self):
        self.order = None
        self.buy_info = None
        self.stop_loss_price = None
        self.hold_until_below_white = False
        self.initial_size = 0
        self.indicators()

    # ------------------------------------------------------------------ #
    #  indicators — MyTT 批量计算所有数组                                   #
    # ------------------------------------------------------------------ #
    def indicators(self):
        C = np.array(self.data.close.array, dtype=float)
        H = np.array(self.data.high.array, dtype=float)
        L = np.array(self.data.low.array, dtype=float)
        O = np.array(self.data.open.array, dtype=float)
        V = np.array(self.data.volume.array, dtype=float)
        n_bars = len(C)

        # ---- 核心指标 ----
        self._white = EMA(EMA(C, 10), 10)
        self._yellow = (MA(C, self.p.m1) + MA(C, self.p.m2)
                        + MA(C, self.p.m3) + MA(C, self.p.m4)) / 4
        self._bbi = (MA(C, 3) + MA(C, 6) + MA(C, 12) + MA(C, 24)) / 4

        # RSI (通达信标准)
        LC = REF(C, 1)
        self._rsi = SMA(MAX(C - LC, 0), 3, 1) / SMA(ABS(C - LC), 3, 1) * 100

        # KDJ
        self.kdj = KDJIndicator(self.data)

        # SHORT / LONG
        s_denom = HHV(C, self.p.n1) - LLV(L, self.p.n1)
        self._short = np.where(s_denom != 0,
                               100 * (C - LLV(L, self.p.n1)) / s_denom, 50.0)
        l_denom = HHV(C, self.p.n2) - LLV(L, self.p.n2)
        self._long = np.where(l_denom != 0,
                              100 * (C - LLV(L, self.p.n2)) / l_denom, 50.0)

        J = self.kdj._j
        K_val = self.kdj._k

        # ---- 周线多头过滤（周线重采样） ----
        dates = [bt.num2date(d) for d in self.data.datetime.array]
        ma30w = _weekly_ma(C, dates, self.p.wma30)
        ma60w = _weekly_ma(C, dates, self.p.wma60)
        ma120w = _weekly_ma(C, dates, self.p.wma120)
        ma240w = _weekly_ma(C, dates, self.p.wma240)
        valid = (ma30w > 0.01) & (ma60w > 0.01) & (ma120w > 0.01) & (ma240w > 0.01)
        self._weekly_bull = valid & (ma30w > ma60w) & (ma60w > ma120w) & (ma120w > ma240w)
        self._above_ma30w = C > ma30w

        # ---- 黄白线金叉 ----
        gc_arr = CROSS(self._white, self._yellow)
        bars_since_gc = BARSLAST(gc_arr)
        self._recent_gc = bars_since_gc <= self.p.gc_lookback

        # ---- 振幅 / 异动 ----
        is_tech = self.p.stock_type == "tech"
        pct_change = np.where(LC > 0, C / LC - 1, 0.0)
        volatile = EXIST(pct_change > 0.15, 200)
        is_volatile = volatile | is_tech
        amp_range = np.where(is_volatile, 8.0, 5.0)
        relax = np.where(is_volatile, 0.9, 1.0)

        daily_amp = (H - L) / L * 100
        daily_pct = ABS(C - LC) / LC * 100 * relax
        up_doji = (C > LC) & (ABS(C - O) / O * 100 * relax < 1.8)

        needle_20 = ((self._short <= 20) & (self._long >= 75)) | ((self._long - self._short) >= 70)
        treasure = (COUNT(self._long >= 75, 8) >= 6) & (COUNT(self._short <= 70, 7) >= 4) & (COUNT(self._short <= 50, 8) >= 1)
        dbl_fork = EVERY(self._long >= 75, 8) & (COUNT(self._short <= 50, 6) >= 2) & (COUNT(self._short <= 20, 7) >= 1)
        red_green = (COUNT(C >= O, 15) > 7) | (COUNT(C > LC, 11) > 5)

        near_amp = (HHV(H, self.p.n) - LLV(L, self.p.n)) / LLV(L, self.p.n) * 100
        far_amp = (HHV(H, self.p.m) - LLV(L, self.p.m)) / LLV(L, self.p.m) * 100
        near_ano = (near_amp >= 15) | ((HHV(H, 12) - LLV(L, 14)) / LLV(L, 14) * 100 >= 11)
        far_ano = far_amp >= 30
        super_ano = near_amp >= 60
        wash_ano = (COUNT(needle_20, 10) >= 2) | treasure | dbl_fork

        anomaly = near_ano | far_ano | wash_ano

        # ---- 成交量 ----
        vday = HHVBARS(V, 40)
        c_vd = _ref_at(C, vday)
        c_vd1 = _ref_at(C, vday + 1)
        o_vd = _ref_at(O, vday)
        not_big_green = np.where(np.isnan(c_vd), True,
                                 (c_vd >= c_vd1) | (c_vd >= o_vd))
        big_green = ~not_big_green
        big_green_far = (vday >= 15) & big_green
        ok_green = not_big_green | big_green_far

        shrink = (V < HHV(V, 20) * 0.416) | (V < HHV(V, 50) / 3)
        pb_shrink = (V < HHV(V, 20) * 0.45) | (V < HHV(V, 50) / 3)
        mod_shrink = (V < HHV(V, 20) * 0.618) | (V < HHV(V, 50) / 3)
        sup_shrink = (V < HHV(V, 30) / 4) | (V < HHV(V, 50) / 6)

        # ---- 趋势状态 ----
        uptrend = ((self._white >= self._yellow * 0.999)
                   & ((C >= self._yellow) | ((C > self._yellow * 0.975) & (C > O))))

        strong_trend = (EVERY(self._yellow >= REF(self._yellow, 1) * 0.999, 13)
                        & (self._white >= REF(self._white, 1))
                        & EVERY(self._white > self._yellow, 20)
                        & EVERY(self._white >= REF(self._white, 1), 11)
                        & red_green)

        cross_c_y = CROSS(C, self._yellow)
        bars_cross_cy = BARSLAST(cross_c_y)
        super_bull = ((EVERY(self._bbi >= REF(self._bbi, 1) * 0.999, 20)
                       | (COUNT(self._bbi >= REF(self._bbi, 1), 25) >= 23))
                      & ((near_amp >= 30) | (far_amp > 80))
                      & (bars_cross_cy > 12))

        # ---- 回踩距离 ----
        dist_w = ABS(C - self._white) / C * 100
        dist_wL = ABS(L - self._white) / self._white * 100
        dist_bbi = ABS(C - self._bbi) / C * 100
        dist_bbiL = ABS(L - self._bbi) / self._bbi * 100
        dist_y = ABS(C - self._yellow) / self._yellow * 100

        pb_white = ((C >= self._white) & (dist_w <= 2)) \
            | ((C < self._white) & (dist_w < 0.8)) \
            | ((C >= self._bbi) & (dist_bbi < 2.5) & (dist_bbiL < 1)
               & (dist_w <= 3) & (daily_pct < 1) & (C > LC))

        white_sup = (C >= self._white) & (dist_w < 1.5)

        strong_pb_hold = ((dist_wL < 1) | (dist_bbiL < 0.5)) & (C > self._white) & (dist_w <= 3.5)

        pb_yellow = ((C >= self._yellow) & ((dist_y <= 1.5) | ((dist_y <= 2) & (daily_pct < 1)))) \
            | ((C < self._yellow) & (dist_y <= 0.8))

        # ---- B1 子条件 ----
        rsi_j = self._rsi + J

        # 1. 超卖缩量拐头B
        b_oversold_turn = (uptrend
                           & (self._rsi - 15 >= REF(self._rsi, 1))
                           & ((REF(self._rsi, 1) < 20) | (REF(J, 1) < 14))
                           & (daily_amp < amp_range + 0.5)
                           & ((daily_pct < 2.3) | (up_doji & (daily_pct < 4)))
                           & ok_green & anomaly & (C >= self._yellow))

        # 2. 超卖缩量B
        b_oversold_shrink = (uptrend
                             & ((J < 14) | (self._rsi < 23))
                             & ((rsi_j < 55) | (J == LLV(J, 20)))
                             & (daily_amp < amp_range)
                             & ((daily_pct < 2.5) | up_doji)
                             & ok_green
                             & (shrink | (mod_shrink & (daily_pct < 1)))
                             & anomaly)

        # 3. 原始B1
        b_raw = ((self._white > self._yellow)
                 & (C >= self._yellow * 0.99)
                 & (self._yellow >= REF(self._yellow, 1))
                 & ((J < 13) | (self._rsi < 21))
                 & (rsi_j < LLV(rsi_j, 15) * 1.5)
                 & mod_shrink & ok_green
                 & ((ABS(C - O) * 100 / O < 1.5)
                    | (sup_shrink | (mod_shrink & (V < LLV(V, 20) * 1.1) & (J == LLV(J, 20))))
                    | (mod_shrink & ((dist_w < 1.8) | (dist_bbi < 1.5) | (dist_y < 2.8))))
                 & anomaly)

        # 4. 超卖超缩量B
        b_oversold_super = (uptrend
                            & ((J < 14) | (self._rsi < 23))
                            & (rsi_j < 60) & (far_amp >= 45)
                            & ((daily_amp < amp_range)
                               | (super_ano & (daily_amp < amp_range + 3.2) & (C > O) & (C > self._white)))
                            & (((C < O) & (V < REF(V, 1)) & (C >= self._yellow)) | (C >= O))
                            & ((daily_pct < 2) | up_doji)
                            & ok_green & sup_shrink & anomaly)

        # 5. 回踩白线B
        b_pb_white = (strong_trend
                      & ((J < 30) | (self._rsi < 40) | wash_ano)
                      & (rsi_j < 70)
                      & ((daily_amp < amp_range + 0.5) | (dist_w < 1) | (dist_bbi < 1))
                      & pb_white
                      & ((daily_pct < 2) | ((daily_pct < 5) & white_sup))
                      & ok_green & pb_shrink & anomaly & (L <= LC))

        # 6. 回踩超级B
        b_pb_super = (super_bull
                      & ((J < 35) | (self._rsi < 45) | wash_ano)
                      & (rsi_j < 80) & (rsi_j == LLV(rsi_j, 25))
                      & (daily_amp < amp_range + 1)
                      & ((daily_pct < 2.5) | (dist_w < 2))
                      & strong_pb_hold & ok_green & anomaly & mod_shrink)

        # 7. 回踩黄线B
        b_pb_yellow = ((self._white >= self._yellow)
                       & (C >= self._yellow * 0.975)
                       & ((J < 13) | (self._rsi < 18))
                       & pb_yellow & ok_green
                       & (shrink | (mod_shrink & ((J == LLV(J, 20)) | (self._rsi == LLV(self._rsi, 14)))))
                       & (self._yellow >= REF(self._yellow, 1) * 0.997)
                       & (MA(C, 60) >= REF(MA(C, 60), 1))
                       & (near_amp >= 11.9) & (far_amp >= 19.5))

        # ---- 总 B1 信号 ----
        self._b1 = (b_oversold_turn | b_oversold_shrink | b_raw
                    | b_oversold_super | b_pb_white | b_pb_super | b_pb_yellow)

    # ------------------------------------------------------------------ #
    #  next — 逐 bar 交易逻辑                                              #
    # ------------------------------------------------------------------ #
    def next(self):
        if self.order:
            return
        if self.is_suspended():
            return

        if not self.position:
            self._check_entry()
        else:
            self._check_exit()

    def _check_entry(self):
        idx = len(self) - 1
        dt = self.data.datetime.date(0)

        weekly_ok = self.p.skip_weekly or (self._weekly_bull[idx] and self._above_ma30w[idx])
        gc_ok = self.p.skip_gc or self._recent_gc[idx]
        b1_ok = self._b1[idx]

        if self.p.print_log:
            self._print_filter_result(dt, weekly_ok, gc_ok, b1_ok)

        if not weekly_ok or not gc_ok or not b1_ok:
            return
        if self.is_limit_up():
            return

        self.order = self.order_target_percent(target=self.p.position_pct)

        # 止损价
        if self.data.close[0] >= self._white[idx]:
            sl = self.data.low[0]
        else:
            sl = self._yellow[idx]

        self.buy_info = {
            "price": self.data.close[0],
            "low": self.data.low[0],
            "white": self._white[idx],
            "yellow": self._yellow[idx],
            "bar": len(self),
        }
        self.stop_loss_price = sl
        self.hold_until_below_white = False
        self.initial_size = 0

        self.log(f"买入  @ {self.data.close[0]:.2f}  止损={sl:.2f}")

    def _check_exit(self):
        idx = len(self) - 1
        price = self.data.close[0]
        bars_held = len(self) - self.buy_info["bar"]

        # 记录初始仓位（成交后首次进入时记录）
        if self.initial_size == 0:
            self.initial_size = self.position.size

        # --- 止损 ---
        if price <= self.stop_loss_price:
            self.order = self.order_target_percent(target=0.0)
            self.log(f"止损 @ {price:.2f}")
            self._reset_position_state()
            return

        # --- T+3 没涨清仓 ---
        if bars_held >= self.p.t_plus_n and price <= self.buy_info["price"]:
            self.order = self.order_target_percent(target=0.0)
            self.log(f"T+{bars_held} 清仓 @ {price:.2f}")
            self._reset_position_state()
            return

        # --- 持股至跌破白线 ---
        if self.hold_until_below_white:
            if price < self._white[idx]:
                self.order = self.order_target_percent(target=0.0)
                self.log(f"跌破白线 @ {price:.2f}")
                self._reset_position_state()
            return

        # --- 涨停卖 1/2 ---
        if price >= self.data.high[0] * 0.995:
            sell_size = max(1, int(self.position.size / 2))
            if sell_size < self.position.size:
                self.order = self.sell(size=sell_size)
                self.log(f"涨停卖半 @ {price:.2f}")
                if self.position.size - sell_size <= self.initial_size / 2:
                    self.hold_until_below_white = True
            return

        # --- 中阳卖 1/3 ---
        pct_gain = (price - self.buy_info["price"]) / self.buy_info["price"] * 100
        mid_yang = 10 if self.p.stock_type == "tech" else 5
        if pct_gain >= mid_yang:
            sell_size = max(1, int(self.position.size / 3))
            if sell_size < self.position.size:
                self.order = self.sell(size=sell_size)
                self.log(f"中阳卖1/3 @ {price:.2f}")
                if self.position.size - sell_size <= self.initial_size / 2:
                    self.hold_until_below_white = True
            return

    def _reset_position_state(self):
        self.buy_info = None
        self.stop_loss_price = None
        self.hold_until_below_white = False
        self.initial_size = 0

    def log(self, txt: str, dt=None):
        if self.p.print_log:
            dt = dt or self.data.datetime.date(0)
            sym = self.data._name or "?"
            print(f"[{dt.isoformat()}] {sym}  {txt}")

    def _print_filter_result(self, dt, weekly_ok, gc_ok, b1_ok):
        sym = self.data._name or "?"
        idx = len(self) - 1
        w = "Y" if weekly_ok else "N"
        g = "Y" if gc_ok else "N"
        b = "Y" if b1_ok else "N"
        all_pass = weekly_ok and gc_ok and b1_ok

        # 仅 B1 信号触发 或 全部通过 时输出
        if not b1_ok and not all_pass:
            return

        tag = " <<< SELECT" if all_pass else ""
        print(f"[{dt.isoformat()}] {sym}  周线={w}  金叉={g}  B1={b}  "
              f"C={self.data.close[0]:.2f}  "
              f"J={self.kdj._j[idx]:.1f}  RSI={self._rsi[idx]:.1f}"
              f"{tag}")
    def buy_signal(self) -> bool:
        return False

    def sell_signal(self) -> bool:
        return False


# ================================================================== #
#  全市场选股扫描 — 周线多头筛选时使用                                    #
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


def _compute_signals(C, H, L, O, V, dates, params):
    """计算最新 bar 的三级过滤结果（直接用 MyTT，不依赖 Backtrader）"""
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
    denom = hhv9 - llv9
    rsv = np.where(denom != 0, (C - llv9) / denom * 100, 50.0)
    K = SMA(rsv, 3, 1)
    D = SMA(K, 3, 1)
    J = 3 * K - 2 * D

    s_denom = HHV(C, params["n1"]) - LLV(L, params["n1"])
    SHORT = np.where(s_denom != 0, 100 * (C - LLV(L, params["n1"])) / s_denom, 50.0)
    l_denom = HHV(C, params["n2"]) - LLV(L, params["n2"])
    LONG = np.where(l_denom != 0, 100 * (C - LLV(L, params["n2"])) / l_denom, 50.0)

    i = n - 1

    # 周线多头
    ma30w = _weekly_ma(C, dates, params["wma30"])
    ma60w = _weekly_ma(C, dates, params["wma60"])
    ma120w = _weekly_ma(C, dates, params["wma120"])
    ma240w = _weekly_ma(C, dates, params["wma240"])
    valid = all(v > 0.01 for v in [ma30w[i], ma60w[i], ma120w[i], ma240w[i]])
    weekly_ok = valid and ma30w[i] > ma60w[i] > ma120w[i] > ma240w[i]
    above_ma30w = C[i] > ma30w[i]

    # 黄白线金叉
    gc_arr = CROSS(white, yellow)
    bars_gc = np.asarray(BARSLAST(gc_arr), dtype=float)
    gc_ok = bars_gc[i] <= params["gc_lookback"]

    if not (weekly_ok and above_ma30w and gc_ok):
        return {"weekly": weekly_ok and above_ma30w, "gc": gc_ok,
                "b1": False, "close": C[i], "J": J[i], "RSI": rsi[i],
                "shrink_score": 0}

    # 振幅 / 异动
    is_tech = params["stock_type"] == "tech"
    pct_change = np.where(LC > 0, C / LC - 1, 0.0)
    volatile = EXIST(pct_change > 0.15, 200)
    is_volatile = volatile[i] or is_tech
    amp_range = 8.0 if is_volatile else 5.0
    relax = 0.9 if is_volatile else 1.0

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
    idx_vd, idx_vd1 = i - vday, i - vday - 1
    not_big_green = ((C[idx_vd] >= C[idx_vd1]) or (C[idx_vd] >= O[idx_vd])) if idx_vd >= 0 and idx_vd1 >= 0 else True
    ok_green = not_big_green or (vday >= 15 and not not_big_green)

    hhv_v20, hhv_v50 = HHV(V, 20), HHV(V, 50)
    llv_v20 = LLV(V, 20)
    shrink = (V[i] < hhv_v20[i] * 0.416) or (V[i] < hhv_v50[i] / 3)
    pb_shrink = (V[i] < hhv_v20[i] * 0.45) or (V[i] < hhv_v50[i] / 3)
    mod_shrink = (V[i] < hhv_v20[i] * 0.618) or (V[i] < hhv_v50[i] / 3)
    sup_shrink = (V[i] < HHV(V, 30)[i] / 4) or (V[i] < hhv_v50[i] / 6)
    shrink_score = V[i] / hhv_v20[i] if hhv_v20[i] > 0 else 1.0

    # 趋势状态
    uptrend = (white[i] >= yellow[i] * 0.999) and (
        (C[i] >= yellow[i]) or ((C[i] > yellow[i] * 0.975) and (C[i] > O[i])))
    strong_trend = (EVERY(yellow >= REF(yellow, 1) * 0.999, 13)[i]
                    and (white[i] >= REF(white, 1)[i])
                    and EVERY(white > yellow, 20)[i]
                    and EVERY(white >= REF(white, 1), 11)[i] and red_green[i])
    cross_c_y = CROSS(C, yellow)
    super_bull = ((EVERY(bbi >= REF(bbi, 1) * 0.999, 20)[i]
                   or COUNT(bbi >= REF(bbi, 1), 25)[i] >= 23)
                  and (near_amp[i] >= 30 or far_amp[i] > 80)
                  and BARSLAST(cross_c_y)[i] > 12)

    # 回踩距离
    dist_w = abs(C[i] - white[i]) / C[i] * 100
    dist_wL = abs(L[i] - white[i]) / white[i] * 100
    dist_bbi = abs(C[i] - bbi[i]) / C[i] * 100
    dist_bbiL = abs(L[i] - bbi[i]) / bbi[i] * 100
    dist_y = abs(C[i] - yellow[i]) / yellow[i] * 100

    pb_white = ((C[i] >= white[i] and dist_w <= 2) or (C[i] < white[i] and dist_w < 0.8)
                or (C[i] >= bbi[i] and dist_bbi < 2.5 and dist_bbiL < 1
                    and dist_w <= 3 and daily_pct < 1 and C[i] > C[i - 1]))
    white_sup = C[i] >= white[i] and dist_w < 1.5
    strong_pb_hold = (dist_wL < 1 or dist_bbiL < 0.5) and C[i] > white[i] and dist_w <= 3.5
    pb_yellow = ((C[i] >= yellow[i] and (dist_y <= 1.5 or (dist_y <= 2 and daily_pct < 1)))
                 or (C[i] < yellow[i] and dist_y <= 0.8))

    rsi_j = rsi + J
    b1 = False

    # 1.超卖缩量拐头B
    if (uptrend and (rsi[i] - 15 >= rsi[i - 1]) and (rsi[i - 1] < 20 or J[i - 1] < 14)
            and daily_amp < amp_range + 0.5 and (daily_pct < 2.3 or (up_doji and daily_pct < 4))
            and ok_green and anomaly and C[i] >= yellow[i]):
        b1 = True
    # 2.超卖缩量B
    if not b1 and (uptrend and (J[i] < 14 or rsi[i] < 23)
                   and (rsi_j[i] < 55 or J[i] == LLV(J, 20)[i])
                   and daily_amp < amp_range and (daily_pct < 2.5 or up_doji)
                   and ok_green and (shrink or (mod_shrink and daily_pct < 1)) and anomaly):
        b1 = True
    # 3.原始B1
    if not b1 and (white[i] > yellow[i] and C[i] >= yellow[i] * 0.99 and yellow[i] >= yellow[i - 1]
                   and (J[i] < 13 or rsi[i] < 21) and rsi_j[i] < LLV(rsi_j, 15)[i] * 1.5
                   and mod_shrink and ok_green
                   and (abs(C[i] - O[i]) * 100 / O[i] < 1.5
                        or (sup_shrink or (mod_shrink and V[i] < llv_v20[i] * 1.1 and J[i] == LLV(J, 20)[i]))
                        or (mod_shrink and (dist_w < 1.8 or dist_bbi < 1.5 or dist_y < 2.8)))
                   and anomaly):
        b1 = True
    # 4.超卖超缩量B
    if not b1 and (uptrend and (J[i] < 14 or rsi[i] < 23) and rsi_j[i] < 60 and far_amp[i] >= 45
                   and (daily_amp < amp_range or (super_ano and daily_amp < amp_range + 3.2 and C[i] > O[i] and C[i] > white[i]))
                   and ((C[i] < O[i] and V[i] < V[i - 1] and C[i] >= yellow[i]) or C[i] >= O[i])
                   and (daily_pct < 2 or up_doji) and ok_green and sup_shrink and anomaly):
        b1 = True
    # 5.回踩白线B
    if not b1 and (strong_trend and (J[i] < 30 or rsi[i] < 40 or wash_ano) and rsi_j[i] < 70
                   and (daily_amp < amp_range + 0.5 or dist_w < 1 or dist_bbi < 1) and pb_white
                   and (daily_pct < 2 or (daily_pct < 5 and white_sup))
                   and ok_green and pb_shrink and anomaly and L[i] <= C[i - 1]):
        b1 = True
    # 6.回踩超级B
    if not b1 and (super_bull and (J[i] < 35 or rsi[i] < 45 or wash_ano)
                   and rsi_j[i] < 80 and rsi_j[i] == LLV(rsi_j, 25)[i]
                   and daily_amp < amp_range + 1 and (daily_pct < 2.5 or dist_w < 2)
                   and strong_pb_hold and ok_green and anomaly and mod_shrink):
        b1 = True
    # 7.回踩黄线B
    if not b1 and (white[i] >= yellow[i] and C[i] >= yellow[i] * 0.975
                   and (J[i] < 13 or rsi[i] < 18) and pb_yellow and ok_green
                   and (shrink or (mod_shrink and (J[i] == LLV(J, 20)[i] or rsi[i] == LLV(rsi, 14)[i])))
                   and yellow[i] >= yellow[i - 1] * 0.997
                   and MA(C, 60)[i] >= REF(MA(C, 60), 1)[i]
                   and near_amp[i] >= 11.9 and far_amp[i] >= 19.5):
        b1 = True

    return {"weekly": True, "gc": True, "b1": b1,
            "close": C[i], "J": J[i], "RSI": rsi[i], "shrink_score": shrink_score}


# ---------- 多进程扫描 ----------

_process_reader = None


def _init_process(tdxdir, market):
    """子进程初始化：每个进程创建自己的 Reader"""
    global _process_reader
    _process_reader = Reader.factory(market=market, tdxdir=tdxdir)


def _scan_one(code, params, skip_weekly, skip_gc):
    """扫描单只股票，返回 (code, sig|None, error)"""
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
            df.index, params)
        if sig is None:
            return code, None, False
        weekly_ok = skip_weekly or sig["weekly"]
        gc_ok = skip_gc or sig["gc"]
        if sig["b1"] and weekly_ok and gc_ok:
            sig["code"] = code
            return code, sig, False
        return code, None, False
    except Exception:
        return code, None, True


def scan_all(stock_type="main", skip_weekly=False, skip_gc=False,
             tdxdir=TDX_DIR, market=TDX_MARKET, max_workers=SCAN_MAX_WORKERS):
    """全市场周线多头筛选，返回符合条件的股票列表

    Args:
        max_workers: 进程池大小，None 表示默认（CPU核心数），1 为单进程模式
    """
    from concurrent.futures import ProcessPoolExecutor, as_completed

    codes = _get_all_codes(tdxdir)
    total = len(codes)
    print(f"扫描 {total} 只A股... (workers={max_workers or 'auto'})")

    params = {
        "m1": 14, "m2": 28, "m3": 57, "m4": 114,
        "n": 20, "m": 50, "n1": 3, "n2": 21,
        "wma30": 30, "wma60": 60, "wma120": 120, "wma240": 240,
        "gc_lookback": 20, "stock_type": stock_type,
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
            pool.submit(_scan_one, code, params, skip_weekly, skip_gc): code
            for code in codes
        }
        for future in as_completed(futures):
            code, sig, err = future.result()
            done += 1
            if err:
                errors += 1
            elif sig is not None:
                results.append(sig)
                print(f"  {code}  "
                      f"C={sig['close']:.2f}  J={sig['J']:.1f}  RSI={sig['RSI']:.1f}  "
                      f"缩量={sig['shrink_score']:.3f}")
            if done % 500 == 0:
                print(f"  ... 已扫描 {done}/{total} ({done/total*100:.0f}%)  "
                      f"命中 {len(results)}  耗时 {time.time()-t0:.1f}s")

    elapsed = time.time() - t0
    results.sort(key=lambda x: x["shrink_score"])

    print(f"\n{'=' * 55}")
    print(f"  扫描完成: {total} 只  命中 {len(results)} 只  "
          f"错误 {errors}  耗时 {elapsed:.1f}s")
    print(f"{'=' * 55}")

    if results:
        print(f"\n  选股结果（按缩量排序）")
        print(f"{'=' * 55}")
        for r in results:
            tag = " <<< TOP" if r == results[0] else ""
            print(f"  {r['code']}  C={r['close']:.2f}  "
                  f"J={r['J']:.1f}  RSI={r['RSI']:.1f}  "
                  f"缩量={r['shrink_score']:.3f}{tag}")

    return results


# ================================================================== #
#  组合级模拟：全量每bar信号计算                                       #
# ================================================================== #

def _compute_all_bar_signals(C, H, L, O, V, dates, params):
    """计算每根 bar 的三级过滤信号（向量版本，返回所有 bar 的信号数组）

    基于 HuangBaiB1Strategy.indicators() 的逻辑，直接用 MyTT 批量计算。
    返回 dict of numpy arrays，或 None（数据不足）。
    """
    n = len(C)
    if n < 300:
        return None

    LC = REF(C, 1)

    # ---- 核心指标 ----
    white = EMA(EMA(C, 10), 10)
    yellow = (MA(C, params["m1"]) + MA(C, params["m2"])
              + MA(C, params["m3"]) + MA(C, params["m4"])) / 4
    bbi = (MA(C, 3) + MA(C, 6) + MA(C, 12) + MA(C, 24)) / 4

    rsi = SMA(MAX(C - LC, 0), 3, 1) / SMA(ABS(C - LC), 3, 1) * 100

    # KDJ
    llv9, hhv9 = LLV(L, 9), HHV(H, 9)
    denom9 = hhv9 - llv9
    rsv = np.where(denom9 != 0, (C - llv9) / denom9 * 100, 50.0)
    K = SMA(rsv, 3, 1)
    D = SMA(K, 3, 1)
    J = 3 * K - 2 * D

    # SHORT / LONG
    s_denom = HHV(C, params["n1"]) - LLV(L, params["n1"])
    SHORT = np.where(s_denom != 0, 100 * (C - LLV(L, params["n1"])) / s_denom, 50.0)
    l_denom = HHV(C, params["n2"]) - LLV(L, params["n2"])
    LONG = np.where(l_denom != 0, 100 * (C - LLV(L, params["n2"])) / l_denom, 50.0)

    # ---- 周线多头过滤 ----
    ma30w = _weekly_ma(C, dates, params["wma30"])
    ma60w = _weekly_ma(C, dates, params["wma60"])
    ma120w = _weekly_ma(C, dates, params["wma120"])
    ma240w = _weekly_ma(C, dates, params["wma240"])
    valid = (ma30w > 0.01) & (ma60w > 0.01) & (ma120w > 0.01) & (ma240w > 0.01)
    weekly_bull = valid & (ma30w > ma60w) & (ma60w > ma120w) & (ma120w > ma240w)
    above_ma30w = C > ma30w

    # ---- 黄白线金叉 ----
    gc_arr = CROSS(white, yellow)
    bars_since_gc = BARSLAST(gc_arr)
    recent_gc = np.asarray(bars_since_gc, dtype=float) <= params["gc_lookback"]

    # ---- 振幅 / 异动 ----
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

    # ---- 成交量 ----
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

    # ---- 趋势状态 ----
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

    # ---- 回踩距离 ----
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

    # ---- B1 七个子条件（向量版） ----
    rsi_j = rsi + J

    # 1. 超卖缩量拐头B
    b_oversold_turn = (uptrend
                       & (rsi - 15 >= REF(rsi, 1))
                       & ((REF(rsi, 1) < 20) | (REF(J, 1) < 14))
                       & (daily_amp < amp_range + 0.5)
                       & ((daily_pct < 2.3) | (up_doji & (daily_pct < 4)))
                       & ok_green & anomaly & (C >= yellow))

    # 2. 超卖缩量B
    b_oversold_shrink = (uptrend
                         & ((J < 14) | (rsi < 23))
                         & ((rsi_j < 55) | (J == LLV(J, 20)))
                         & (daily_amp < amp_range)
                         & ((daily_pct < 2.5) | up_doji)
                         & ok_green
                         & (shrink | (mod_shrink & (daily_pct < 1)))
                         & anomaly)

    # 3. 原始B1
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

    # 4. 超卖超缩量B
    b_oversold_super = (uptrend
                        & ((J < 14) | (rsi < 23))
                        & (rsi_j < 60) & (far_amp >= 45)
                        & ((daily_amp < amp_range)
                           | (super_ano & (daily_amp < amp_range + 3.2) & (C > O) & (C > white)))
                        & (((C < O) & (V < REF(V, 1)) & (C >= yellow)) | (C >= O))
                        & ((daily_pct < 2) | up_doji)
                        & ok_green & sup_shrink & anomaly)

    # 5. 回踩白线B
    b_pb_white = (strong_trend
                  & ((J < 30) | (rsi < 40) | wash_ano)
                  & (rsi_j < 70)
                  & ((daily_amp < amp_range + 0.5) | (dist_w < 1) | (dist_bbi < 1))
                  & pb_white
                  & ((daily_pct < 2) | ((daily_pct < 5) & white_sup))
                  & ok_green & pb_shrink & anomaly & (L <= LC))

    # 6. 回踩超级B
    b_pb_super = (super_bull
                  & ((J < 35) | (rsi < 45) | wash_ano)
                  & (rsi_j < 80) & (rsi_j == LLV(rsi_j, 25))
                  & (daily_amp < amp_range + 1)
                  & ((daily_pct < 2.5) | (dist_w < 2))
                  & strong_pb_hold & ok_green & anomaly & mod_shrink)

    # 7. 回踩黄线B
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

    return {
        "weekly_bull": weekly_bull,
        "above_ma30w": above_ma30w,
        "recent_gc": recent_gc,
        "b1": b1,
        "shrink_score": shrink_score,
        "white": white,
        "yellow": yellow,
        "close": C,
        "high": H,
        "low": L,
        "dates": dates,
    }


def _scan_one_all_bars(code, params):
    """加载单只股票数据并计算全量每bar信号，供并行预加载使用"""
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
            df.index, params)
        return code, signals, False
    except Exception:
        return code, None, True


def preload_all_signals(start="2024-01-01", end="2025-12-31",
                        stock_type="main", max_workers=SCAN_MAX_WORKERS,
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
        "m1": 14, "m2": 28, "m3": 57, "m4": 114,
        "n": 20, "m": 50, "n1": 3, "n2": 21,
        "wma30": 30, "wma60": 60, "wma120": 120, "wma240": 240,
        "gc_lookback": 20, "stock_type": stock_type,
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
        futures = {
            pool.submit(_scan_one_all_bars, code, params): code
            for code in codes
        }
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

    # 构建交易日历
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)
    sorted_dates = sorted(d for d in all_dates if start_ts <= d <= end_ts)
    trading_days = pd.DatetimeIndex(sorted_dates)

    print(f"\n  预加载完成: {len(all_signals)} 只  错误 {errors}  "
          f"交易日 {len(trading_days)}  耗时 {elapsed:.1f}s")

    # 诊断：打印实际数据年份覆盖
    if len(trading_days) > 0:
        first = trading_days[0]
        last = trading_days[-1]
        years = pd.Series(trading_days.year).value_counts().sort_index()
        year_info = "  ".join(f"{y}年:{c}天" for y, c in years.items())
        print(f"  数据范围: {first.strftime('%Y-%m-%d')} ~ {last.strftime('%Y-%m-%d')}  [{year_info}]")

    return all_signals, trading_days
