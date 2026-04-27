"""黄白线金叉后B1策略

策略逻辑：
1. 周线多头空间（周线MA30>MA60>MA120>MA240）
2. 黄白线金叉（近N日内发生）
3. B1买入信号（7个子条件OR）
4. 止损：白线上方买入→买入日最低价；白线黄线之间→黄线价
5. T+3没涨清仓
6. 止盈：中阳卖1/3，涨停卖1/2，仓位半仓后持股至跌破白线
"""

import numpy as np
import pandas as pd
import backtrader as bt
from MyTT import (
    EMA, MA, SMA, HHV, LLV, REF, COUNT, EVERY, EXIST,
    CROSS, MAX, ABS, IF, BARSLAST, HHVBARS, BBI as MyTT_BBI,
)
from src.indicators.kdj_indicator import KDJIndicator
from src.strategies.base_strategy import BaseStrategy


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

        self.log(f"BUY  @ {self.data.close[0]:.2f}  SL={sl:.2f}")

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
            self.log(f"STOP LOSS @ {price:.2f}")
            self._reset_position_state()
            return

        # --- T+3 没涨清仓 ---
        if bars_held >= self.p.t_plus_n and price <= self.buy_info["price"]:
            self.order = self.order_target_percent(target=0.0)
            self.log(f"T+{bars_held} EXIT @ {price:.2f}")
            self._reset_position_state()
            return

        # --- 持股至跌破白线 ---
        if self.hold_until_below_white:
            if price < self._white[idx]:
                self.order = self.order_target_percent(target=0.0)
                self.log(f"BELOW WHITE EXIT @ {price:.2f}")
                self._reset_position_state()
            return

        # --- 涨停卖 1/2 ---
        if price >= self.data.high[0] * 0.995:
            sell_size = max(1, int(self.position.size / 2))
            if sell_size < self.position.size:
                self.order = self.sell(size=sell_size)
                self.log(f"LIMIT UP SELL 1/2 @ {price:.2f}")
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
                self.log(f"MID YANG SELL 1/3 @ {price:.2f}")
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
