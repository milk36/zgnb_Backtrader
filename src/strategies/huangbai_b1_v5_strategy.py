"""黄白线B1策略 V5 — 战法退出逻辑

买入逻辑基于 V2（周线多头 + 大盘MACD + B1七子条件 + 放量过滤），移除金叉条件

退出逻辑基于文章战法，六级优先级：
L0. 如果没有盈利之前则优先L1条件止损,有止盈放飞操作后再考虑L2-L6
L1. 硬止损（止损价，无条件）
L2. 放量跌停（量能放大 + 跌停价）
L3. S1信号持有期卖出（加速后放量阴线，缩量+关键K例外）
L4. 两根平行中阴线（局部高位连续两根中阴）
L5. 参考线次日确认（跌破参考线后次日未收回，缩量+关键K例外）
    - 参考线选择：白黄线差值≤10% → 黄线；止损基于黄线下 -1% → 黄线；否则 → 白线下 -1%
L6. 放飞减仓1/3（涨停或大涨减仓，保留核心仓位）

核心概念：
- 关键K：买入后识别最显著放量阳线，其范围作为宽松支撑
- 缩量不卖：缩量下跌时除硬止损外不触发卖出
- 加速检测：5日涨幅>15%视为加速，加速后对S1/白线破位更严格
"""

import os
import re
import time
import warnings

import numpy as np
import pandas as pd
import backtrader as bt

warnings.filterwarnings("ignore", category=RuntimeWarning)
from MyTT import EMA, MA, SMA, HHV, LLV, REF, COUNT, EVERY, EXIST, \
    CROSS, MAX, ABS, IF, BARSLAST, HHVBARS, BBI as MyTT_BBI
from mootdx.reader import Reader
from src.indicators.kdj_indicator import KDJIndicator
from src.strategies.base_strategy import BaseStrategy

from config import (
    TDX_DIR, TDX_MARKET, SCAN_MAX_WORKERS, STOCK_TYPE,
    HUANGBAI_M1, HUANGBAI_M2, HUANGBAI_M3, HUANGBAI_M4,
    HUANGBAI_N, HUANGBAI_M, HUANGBAI_N1, HUANGBAI_N2,
    HUANGBAI_T_PLUS_N,
    HUANGBAI_VOL_EXPAND_PERIOD, HUANGBAI_VOL_EXPAND_MIN,
    HUANGBAI_SURGE_PRICE_PCT, HUANGBAI_SURGE_VOL_RATIO,
    HUANGBAI_S1_PERIOD,
    MARKET_INDEX_CODE, MARKET_MACD_FAST, MARKET_MACD_SLOW, MARKET_MACD_SIGNAL,
)


# ---------- helpers（复用 V1 逻辑） ----------

def _ref_at(S, offsets):
    """REF with variable offset"""
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


# ---------- 大盘MACD ----------

def load_market_index(tdxdir=TDX_DIR, market=TDX_MARKET):
    """加载上证指数日线数据，返回 DataFrame 或 None"""
    try:
        reader = Reader.factory(market=market, tdxdir=tdxdir)
        df = reader.daily(symbol=MARKET_INDEX_CODE)
        if df is not None and len(df) > 0:
            return df.sort_index()
    except Exception as e:
        print(f"  警告: 加载大盘指数失败: {e}")
    return None


def compute_market_macd(close, fast=MARKET_MACD_FAST,
                        slow=MARKET_MACD_SLOW,
                        signal=MARKET_MACD_SIGNAL):
    """计算大盘 MACD 指标

    Returns:
        dif: MACD 快线 (DIF)
        dea: MACD 慢线 (DEA/Signal)
        bullish: bool array - DIF > DEA 为多头
    """
    dif = EMA(close, fast) - EMA(close, slow)
    dea = EMA(dif, signal)
    bullish = np.where(np.isnan(dif) | np.isnan(dea), False, dif > dea)
    return dif, dea, bullish


def compute_market_macd_for_trading_days(trading_days, tdxdir=TDX_DIR,
                                         market=TDX_MARKET):
    """预计算大盘在每个交易日的MACD多头状态

    Args:
        trading_days: DatetimeIndex 交易日历

    Returns:
        market_macd_bullish: np.ndarray[bool] 或 None
    """
    df = load_market_index(tdxdir, market)
    if df is None:
        print("  警告: 无法加载大盘指数数据，大盘MACD过滤将被跳过")
        return None

    close = df["close"].values.astype(float)
    _, _, bullish = compute_market_macd(close)

    macd_series = pd.Series(bullish, index=df.index)
    aligned = macd_series.reindex(trading_days, method='ffill').fillna(False)
    return aligned.values.astype(bool)


# ---------- 策略类 ----------

class HuangBaiB1V5Strategy(BaseStrategy):
    """V5: 周线多头 + 大盘MACD多头 + B1 + 战法退出（无金叉条件）"""

    params = (
        ("print_log", True),
        ("position_pct", 0.1),
        ("stock_type", STOCK_TYPE),

        # 黄白线参数
        ("m1", HUANGBAI_M1), ("m2", HUANGBAI_M2),
        ("m3", HUANGBAI_M3), ("m4", HUANGBAI_M4),

        # B1 振幅参数
        ("n", HUANGBAI_N), ("m", HUANGBAI_M),
        ("n1", HUANGBAI_N1), ("n2", HUANGBAI_N2),

        # 止损止盈
        ("t_plus_n", HUANGBAI_T_PLUS_N),

        # 周线MA周期
        ("wma30", 30), ("wma60", 60), ("wma120", 120), ("wma240", 240),

        # 调试
        ("skip_weekly", False),
        ("skip_market_macd", False),
        ("skip_stock_macd", False),
        ("skip_vol_expand", False),
    )

    def __init__(self):
        self.order = None
        self.buy_info = None
        self.stop_loss_price = None
        self.initial_size = 0
        self._last_sl_bar = None
        self._partial_sold = False
        self._surge_reduction_done = False
        # V5 战法状态
        self.key_k_high = None
        self.key_k_low = None
        self.key_k_bar = None
        self._sl_based_on_yellow = False
        self.white_break_pending = False
        self.white_break_bar = None
        self.has_accelerated = False
        self.max_price_since_buy = 0.0
        self._accel_last_scanned = 0

        # 计算个股指标
        self.indicators()

        # 计算大盘MACD（如果存在第二数据源）
        self._market_macd_bullish = None
        if len(self.datas) > 1:
            market_close = np.array(self.data1.close.array, dtype=float)
            _, _, self._market_macd_bullish = compute_market_macd(market_close)

    def indicators(self):
        """计算个股全部指标（与V1相同）"""
        C = np.array(self.data.close.array, dtype=float)
        H = np.array(self.data.high.array, dtype=float)
        L = np.array(self.data.low.array, dtype=float)
        O = np.array(self.data.open.array, dtype=float)
        V = np.array(self.data.volume.array, dtype=float)

        # ---- 核心指标 ----
        self._white = EMA(EMA(C, 10), 10)
        self._yellow = (MA(C, self.p.m1) + MA(C, self.p.m2)
                        + MA(C, self.p.m3) + MA(C, self.p.m4)) / 4
        self._bbi = (MA(C, 3) + MA(C, 6) + MA(C, 12) + MA(C, 24)) / 4

        LC = REF(C, 1)
        self._rsi = SMA(MAX(C - LC, 0), 3, 1) / SMA(ABS(C - LC), 3, 1) * 100

        self.kdj = KDJIndicator(self.data)

        s_denom = HHV(C, self.p.n1) - LLV(L, self.p.n1)
        self._short = np.where(s_denom != 0,
                               100 * (C - LLV(L, self.p.n1)) / s_denom, 50.0)
        l_denom = HHV(C, self.p.n2) - LLV(L, self.p.n2)
        self._long = np.where(l_denom != 0,
                              100 * (C - LLV(L, self.p.n2)) / l_denom, 50.0)

        J = self.kdj._j

        # ---- 周线多头过滤 ----
        dates = [bt.num2date(d) for d in self.data.datetime.array]
        ma30w = _weekly_ma(C, dates, self.p.wma30)
        ma60w = _weekly_ma(C, dates, self.p.wma60)
        ma120w = _weekly_ma(C, dates, self.p.wma120)
        ma240w = _weekly_ma(C, dates, self.p.wma240)
        valid = (ma30w > 0.01) & (ma60w > 0.01) & (ma120w > 0.01) & (ma240w > 0.01)
        self._weekly_bull = valid & (ma30w > ma60w) & (ma60w > ma120w) & (ma120w > ma240w)
        self._above_ma30w = C > ma30w

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

        # ---- B1 七个子条件 ----
        rsi_j = self._rsi + J

        b_oversold_turn = (uptrend
                           & (self._rsi - 15 >= REF(self._rsi, 1))
                           & ((REF(self._rsi, 1) < 20) | (REF(J, 1) < 14))
                           & (daily_amp < amp_range + 0.5)
                           & ((daily_pct < 2.3) | (up_doji & (daily_pct < 4)))
                           & ok_green & anomaly & (C >= self._yellow))

        b_oversold_shrink = (uptrend
                             & ((J < 14) | (self._rsi < 23))
                             & ((rsi_j < 55) | (J == LLV(J, 20)))
                             & (daily_amp < amp_range)
                             & ((daily_pct < 2.5) | up_doji)
                             & ok_green
                             & (shrink | (mod_shrink & (daily_pct < 1)))
                             & anomaly)

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

        b_oversold_super = (uptrend
                            & ((J < 14) | (self._rsi < 23))
                            & (rsi_j < 60) & (far_amp >= 45)
                            & ((daily_amp < amp_range)
                               | (super_ano & (daily_amp < amp_range + 3.2) & (C > O) & (C > self._white)))
                            & (((C < O) & (V < REF(V, 1)) & (C >= self._yellow)) | (C >= O))
                            & ((daily_pct < 2) | up_doji)
                            & ok_green & sup_shrink & anomaly)

        b_pb_white = (strong_trend
                      & ((J < 30) | (self._rsi < 40) | wash_ano)
                      & (rsi_j < 70)
                      & ((daily_amp < amp_range + 0.5) | (dist_w < 1) | (dist_bbi < 1))
                      & pb_white
                      & ((daily_pct < 2) | ((daily_pct < 5) & white_sup))
                      & ok_green & pb_shrink & anomaly & (L <= LC))

        b_pb_super = (super_bull
                      & ((J < 35) | (self._rsi < 45) | wash_ano)
                      & (rsi_j < 80) & (rsi_j == LLV(rsi_j, 25))
                      & (daily_amp < amp_range + 1)
                      & ((daily_pct < 2.5) | (dist_w < 2))
                      & strong_pb_hold & ok_green & anomaly & mod_shrink)

        b_pb_yellow = ((self._white >= self._yellow)
                       & (C >= self._yellow * 0.975)
                       & ((J < 13) | (self._rsi < 18))
                       & pb_yellow & ok_green
                       & (shrink | (mod_shrink & ((J == LLV(J, 20)) | (self._rsi == LLV(self._rsi, 14)))))
                       & (self._yellow >= REF(self._yellow, 1) * 0.997)
                       & (MA(C, 60) >= REF(MA(C, 60), 1))
                       & (near_amp >= 11.9) & (far_amp >= 19.5))

        self._b1 = (b_oversold_turn | b_oversold_shrink | b_raw
                    | b_oversold_super | b_pb_white | b_pb_super | b_pb_yellow)

        # 个股MACD多头过滤（暂未启用，预留接口）
        self._stock_macd_bullish = np.ones(len(C), dtype=bool)

        # 前期放量上涨过滤：必须有放量支撑，排除缩量快速拉升
        vol_expand = (V > REF(V, 1) * 1.8) & (C > O) & (C > LC)
        _vep, _vem = HUANGBAI_VOL_EXPAND_PERIOD, HUANGBAI_VOL_EXPAND_MIN
        has_vol_expand = COUNT(vol_expand, _vep) >= _vem
        # 缩量快速拉升：近期涨幅大但量能萎缩
        _ref_c = REF(C, _vep)
        _price_rise = np.where(np.abs(_ref_c) > 0.001,
                               (C - _ref_c) / np.abs(_ref_c) * 100, 0)
        _vol_ratio = MA(V, _vep) / np.maximum(MA(V, 60), 1)
        no_shrinkage_surge = ~((_price_rise > HUANGBAI_SURGE_PRICE_PCT)
                               & (_vol_ratio < HUANGBAI_SURGE_VOL_RATIO))
        # 连续涨停缩量排除：前期有连续涨停且缩量则直接剔除
        _lp = 1.20 if self.p.stock_type == "tech" else 1.10
        _limit_up = C >= np.round(REF(C, 1) * _lp, 2)
        _limit_shrink = _limit_up & (V < REF(V, 1))
        no_consec_limit_shrink = COUNT(_limit_shrink.astype(float), _vep) < 1
        # 连续上涨后放量下跌排除：近N天下跌日总成交量 > 上涨日总成交量
        _rise_v = np.where(C > REF(C, 1), V, 0)
        _decline_v = np.where(C < REF(C, 1), V, 0)
        _rvs = pd.Series(_rise_v).rolling(_vep, min_periods=1).sum().values
        _dvs = pd.Series(_decline_v).rolling(_vep, min_periods=1).sum().values
        no_heavy_decline = ~(_dvs > _rvs)
        # S1/大风车排除：加速上涨后出现放天量大阴线或历史天量长上下影阴线
        _s1p = HUANGBAI_S1_PERIOD
        _accel = (C - REF(C, 5)) / np.maximum(REF(C, 5), 0.001) * 100 > 15
        _big_vol = (V > HHV(V, 20) * 2) | (V > MA(V, 60) * 3)
        _big_yin = (C < O) & ((O - C) / np.maximum(REF(C, 1), 0.001) * 100 > 3)
        _s1 = _accel & _big_vol & _big_yin
        _upper_shadow = H - np.maximum(O, C)
        _lower_shadow = np.minimum(O, C) - L
        _body = ABS(C - O)
        _long_shadow_yin = (C < O) & ((_upper_shadow + _lower_shadow) > _body * 2)
        _hist_vol = V == HHV(V, 120)
        _dafengche = _accel & _hist_vol & _long_shadow_yin & (V > REF(V, 1))
        no_s1_dafengche = ~EXIST(_s1 | _dafengche, _s1p)
        self._vol_expand_ok = (has_vol_expand & no_shrinkage_surge
                               & no_consec_limit_shrink & no_heavy_decline
                               & no_s1_dafengche)

    # ------------------------------------------------------------------ #
    #  next — 逐 bar 交易逻辑（V2: 增加大盘MACD过滤）                       #
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

        # V2新增：大盘MACD多头过滤
        market_macd_ok = self.p.skip_market_macd
        if not market_macd_ok and self._market_macd_bullish is not None:
            market_macd_ok = self._market_macd_bullish[idx]

        weekly_ok = self.p.skip_weekly or (self._weekly_bull[idx] and self._above_ma30w[idx])
        b1_ok = self._b1[idx]
        stock_macd_ok = self.p.skip_stock_macd or self._stock_macd_bullish[idx]
        vol_expand_ok = self.p.skip_vol_expand or self._vol_expand_ok[idx]

        if self.p.print_log:
            self._print_filter_result(dt, weekly_ok, b1_ok, market_macd_ok,
                                      stock_macd_ok, vol_expand_ok)

        if not market_macd_ok:
            return
        if not weekly_ok or not b1_ok or not stock_macd_ok or not vol_expand_ok:
            return

        if self._last_sl_bar is not None and (len(self) - self._last_sl_bar) < 10:
            return
        if self.is_limit_up():
            return

        self.order = self.order_target_percent(target=self.p.position_pct)

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
        self.initial_size = 0
        self._partial_sold = False
        self._surge_reduction_done = False
        self._sl_based_on_yellow = (self.data.close[0] < self._white[idx])
        self.white_break_pending = False
        self.white_break_bar = None
        self.has_accelerated = False
        self.max_price_since_buy = 0.0
        self._accel_last_scanned = 0

        # V5: 识别关键K
        self._identify_key_k_at_buy(idx)

        self.log(f"买入  @ {self.data.close[0]:.2f}  止损={sl:.2f}  "
                  f"关键K=[{self.key_k_low:.2f}~{self.key_k_high:.2f}]" if self.key_k_high else
                  f"买入  @ {self.data.close[0]:.2f}  止损={sl:.2f}")

    def _check_exit(self):
        """V5 战法六级退出逻辑"""
        idx = len(self) - 1
        price = self.data.close[0]
        high = self.data.high[0]
        low = self.data.low[0]
        open_price = self.data.open[0]
        white_val = self._white[idx]
        yellow_val = self._yellow[idx]

        if self.initial_size == 0:
            self.initial_size = self.position.size

        pct_gain = (price - self.buy_info["price"]) / self.buy_info["price"] * 100

        # 更新最高价和加速检测
        if price > self.max_price_since_buy:
            self.max_price_since_buy = price
        self._update_acceleration(idx)

        # 预计算缩量和关键K判断
        V = np.array(self.data.volume.array, dtype=float)
        C = np.array(self.data.close.array, dtype=float)
        O = np.array(self.data.open.array, dtype=float)
        H = np.array(self.data.high.array, dtype=float)

        ma_v20 = MA(V, 20)
        hhv_v50 = HHV(V, 50)
        is_shrinking = (V[idx] < ma_v20[idx] * 0.618) or (V[idx] < hhv_v50[idx] / 3)
        in_key_k = (self.key_k_high is not None
                    and self.key_k_low is not None
                    and self.key_k_low <= price <= self.key_k_high)

        _is_tech = self.p.stock_type == "tech"
        limit_pct = 1.20 if _is_tech else 1.10
        prev_close = self.data.close[-1] if idx >= 1 else price

        # ---- L1: 硬止损（无条件） ----
        if price <= self.stop_loss_price:
            self.order = self.order_target_percent(target=0.0)
            self._last_sl_bar = len(self)
            self.log(f"止损 @ {price:.2f}  亏损={pct_gain:+.2f}%")
            self._reset_position_state()
            return

        # ---- L0: 未放飞前仅L1+L6，放飞后L2-L5生效 ----
        if self._partial_sold:
            # ---- L2: 放量跌停 ----
            limit_down_price = round(prev_close * (2 - limit_pct), 2)
            vol_expanding = V[idx] > ma_v20[idx] * 1.5
            if vol_expanding and price <= limit_down_price:
                self.order = self.order_target_percent(target=0.0)
                self._last_sl_bar = len(self)
                self.log(f"放量跌停 @ {price:.2f}  盈亏={pct_gain:+.2f}%")
                self._reset_position_state()
                return

            # ---- L3: S1信号持有期卖出 ----
            if self.has_accelerated:
                hhv_v20 = HHV(V, 20)
                ma_v60 = MA(V, 60)
                big_vol = (V[idx] > hhv_v20[idx] * 2) or (V[idx] > ma_v60[idx] * 3)
                bearish = price < open_price
                body_pct = abs(open_price - price) / open_price * 100 if open_price > 0 else 0
                if big_vol and bearish and body_pct > 3:
                    if not (is_shrinking and in_key_k):
                        self.order = self.order_target_percent(target=0.0)
                        self._last_sl_bar = len(self)
                        self.log(f"S1信号清仓 @ {price:.2f}  盈亏={pct_gain:+.2f}%")
                        self._reset_position_state()
                        return

            # ---- L4: 两根平行中阴线 ----
            if idx >= 2:
                c1, o1 = C[idx - 1], O[idx - 1]
                bearish0 = price < open_price
                bearish1 = c1 < o1
                body0 = abs(open_price - price) / open_price * 100 if open_price > 0 else 0
                body1 = abs(o1 - c1) / o1 * 100 if o1 > 0 else 0
                hhv_h20 = HHV(H, 20)
                at_local_high = price >= hhv_h20[idx] * 0.97
                if bearish0 and bearish1 and body0 > 2.5 and body1 > 2.5 and at_local_high:
                    self.order = self.order_target_percent(target=0.0)
                    self._last_sl_bar = len(self)
                    self.log(f"两根中阴线清仓 @ {price:.2f}  盈亏={pct_gain:+.2f}%")
                    self._reset_position_state()
                    return

            # ---- L5: 参考线次日确认 ----
            wy_gap_pct = abs(white_val - yellow_val) / yellow_val * 100 if yellow_val > 0 else 100
            if wy_gap_pct <= 10:
                l5_ref = yellow_val
                l5_name = "黄线"
            elif self._sl_based_on_yellow:
                l5_ref = yellow_val * 0.99
                l5_name = "黄线-1%"
            else:
                l5_ref = white_val * 0.99
                l5_name = "白线-1%"

            if self.white_break_pending:
                if price < l5_ref:
                    # 例外1: 缩量 + 在关键K内 → 不卖
                    if is_shrinking and in_key_k:
                        self.white_break_pending = False
                    # 例外2: 未加速 + 缩量 → 不卖
                    elif not self.has_accelerated and is_shrinking:
                        self.white_break_pending = False
                    else:
                        self.order = self.order_target_percent(target=0.0)
                        self._last_sl_bar = len(self)
                        self.log(f"{l5_name}确认清仓 @ {price:.2f}  盈亏={pct_gain:+.2f}%")
                        self._reset_position_state()
                        return
                else:
                    self.white_break_pending = False
            else:
                if price < l5_ref:
                    self.white_break_pending = True
                    self.white_break_bar = idx

        # ---- L6: 放飞减仓 ----
        limit_up_price = round(prev_close * limit_pct, 2)
        daily_up = price > prev_close if prev_close > 0 else False

        # 6a: 涨停减仓1/3
        if high >= limit_up_price:
            sell_size = max(1, int(self.position.size / 3))
            if sell_size < self.position.size:
                self.order = self.sell(size=sell_size)
                self._partial_sold = True
                self.log(f"涨停放飞1/3 @ {price:.2f}  盈亏={pct_gain:+.2f}%")
            return

        # 6b: 大涨减仓1/3（盈利>10%+当日上涨，仅一次）
        if not self._surge_reduction_done and daily_up and pct_gain > 10:
            sell_size = max(1, int(self.position.size / 3))
            if sell_size < self.position.size:
                self.order = self.sell(size=sell_size)
                self._partial_sold = True
                self._surge_reduction_done = True
                self.log(f"大涨放飞1/3 @ {price:.2f}  盈亏={pct_gain:+.2f}%")
            return

        # ---- 更新关键K ----
        self._update_key_k(idx)

    def _update_acceleration(self, idx):
        """增量检测是否有加速（5日涨幅>15%）"""
        if self.has_accelerated:
            return
        C = np.array(self.data.close.array, dtype=float)
        buy_bar = self.buy_info["bar"]
        start = max(buy_bar, self._accel_last_scanned, 5)
        for b in range(start, idx + 1):
            if C[b - 5] > 0:
                gain = (C[b] - C[b - 5]) / C[b - 5] * 100
                if gain > 15:
                    self.has_accelerated = True
                    break
        self._accel_last_scanned = idx

    def _identify_key_k_at_buy(self, buy_idx):
        """买入时向后扫描30根K线，识别最显著的放量阳线作为关键K"""
        C = np.array(self.data.close.array, dtype=float)
        O = np.array(self.data.open.array, dtype=float)
        V = np.array(self.data.volume.array, dtype=float)
        H = np.array(self.data.high.array, dtype=float)

        start = max(0, buy_idx - 30)
        ma_v20 = MA(V, 20)
        best_sig = 0
        best_idx = None

        for i in range(start, buy_idx + 1):
            c, o, v = C[i], O[i], V[i]
            if c <= o:
                continue
            body_pct = (c - o) / c * 100
            if body_pct < 2:
                continue
            if v <= ma_v20[i]:
                continue
            sig_val = v * body_pct
            if sig_val > best_sig:
                best_sig = sig_val
                best_idx = i

        if best_idx is not None:
            self.key_k_high = H[best_idx]
            self.key_k_low = O[best_idx]
            self.key_k_bar = best_idx

    def _update_key_k(self, idx):
        """持有期更新关键K：出现更显著的放量阳线则替换"""
        C = np.array(self.data.close.array, dtype=float)
        O = np.array(self.data.open.array, dtype=float)
        V = np.array(self.data.volume.array, dtype=float)
        H = np.array(self.data.high.array, dtype=float)

        c, o, v, h = C[idx], O[idx], V[idx], H[idx]
        if c <= o:
            return
        body_pct = (c - o) / c * 100
        if body_pct < 2:
            return
        ma_v20 = MA(V, 20)
        if v <= ma_v20[idx]:
            return

        significance = v * body_pct

        if self.key_k_bar is not None:
            old_c, old_o = C[self.key_k_bar], O[self.key_k_bar]
            old_body = (old_c - old_o) / old_c * 100 if old_c > 0 else 0
            old_sig = V[self.key_k_bar] * old_body
            if significance <= old_sig:
                return

        self.key_k_high = h
        self.key_k_low = o
        self.key_k_bar = idx

    def _reset_position_state(self):
        self.buy_info = None
        self.stop_loss_price = None
        self.initial_size = 0
        self._partial_sold = False
        self._surge_reduction_done = False
        self.key_k_high = None
        self.key_k_low = None
        self.key_k_bar = None
        self._sl_based_on_yellow = False
        self.white_break_pending = False
        self.white_break_bar = None
        self.has_accelerated = False
        self.max_price_since_buy = 0.0
        self._accel_last_scanned = 0

    def log(self, txt: str, dt=None):
        if self.p.print_log:
            dt = dt or self.data.datetime.date(0)
            sym = self.data._name or "?"
            print(f"[{dt.isoformat()}] {sym}  [B1V5] {txt}")

    def _print_filter_result(self, dt, weekly_ok, b1_ok, market_macd_ok,
                             stock_macd_ok, vol_expand_ok=True):
        sym = self.data._name or "?"
        idx = len(self) - 1
        w = "Y" if weekly_ok else "N"
        b = "Y" if b1_ok else "N"
        m = "Y" if market_macd_ok else "N"
        s = "Y" if stock_macd_ok else "N"
        v = "Y" if vol_expand_ok else "N"
        all_pass = (market_macd_ok and weekly_ok and b1_ok
                    and stock_macd_ok and vol_expand_ok)

        if not b1_ok and not all_pass:
            return

        tag = " <<< SELECT" if all_pass else ""
        print(f"[{dt.isoformat()}] {sym}  [B1V5] 大盘={m}  周线={w}  "
              f"放量={v}  个股MACD={s}  B1={b}  "
              f"C={self.data.close[0]:.2f}  "
              f"J={self.kdj._j[idx]:.1f}  RSI={self._rsi[idx]:.1f}"
              f"{tag}")

    def buy_signal(self) -> bool:
        return False

    def sell_signal(self) -> bool:
        return False


# ================================================================== #
#  全市场选股扫描 V2 — 增加大盘MACD过滤                               #
# ================================================================== #

def _get_all_codes(tdxdir=TDX_DIR):
    """从通达信本地目录提取全部A股代码"""
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
    """计算最新 bar 的四级过滤结果（V2: 增加大盘MACD）"""
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

    # 个股MACD多头过滤（暂未启用，预留接口）
    stock_macd_ok = True

    # 前期放量上涨过滤 + 排除缩量快速拉升 + 连续涨停缩量排除
    vol_expand = (V > REF(V, 1) * 1.8) & (C > O) & (C > LC)
    _vep, _vem = HUANGBAI_VOL_EXPAND_PERIOD, HUANGBAI_VOL_EXPAND_MIN
    has_vol_expand = COUNT(vol_expand, _vep)[i] >= _vem
    _ref_c = REF(C, _vep)[i]
    _price_rise = (C[i] - _ref_c) / abs(_ref_c) * 100 if abs(_ref_c) > 0.001 else 0
    _vol_ratio = MA(V, _vep)[i] / max(MA(V, 60)[i], 1)
    no_shrinkage_surge = not (_price_rise > HUANGBAI_SURGE_PRICE_PCT
                              and _vol_ratio < HUANGBAI_SURGE_VOL_RATIO)
    # 连续涨停缩量排除
    _lp = 1.20 if params.get("stock_type") == "tech" else 1.10
    _limit_up = C >= np.round(REF(C, 1) * _lp, 2)
    _limit_shrink = _limit_up & (V < REF(V, 1))
    no_consec_limit_shrink = COUNT(_limit_shrink.astype(float), _vep)[i] < 1
    # 连续上涨后放量下跌排除
    _rise_v = np.where(C > REF(C, 1), V, 0)
    _decline_v = np.where(C < REF(C, 1), V, 0)
    _rvs = pd.Series(_rise_v).rolling(_vep, min_periods=1).sum().values[i]
    _dvs = pd.Series(_decline_v).rolling(_vep, min_periods=1).sum().values[i]
    no_heavy_decline = not (_dvs > _rvs)
    # S1/大风车排除
    _s1p = HUANGBAI_S1_PERIOD
    _accel = (C - REF(C, 5)) / np.maximum(REF(C, 5), 0.001) * 100 > 15
    _big_vol = (V > HHV(V, 20) * 2) | (V > MA(V, 60) * 3)
    _big_yin = (C < O) & ((O - C) / np.maximum(REF(C, 1), 0.001) * 100 > 3)
    _s1 = _accel & _big_vol & _big_yin
    _upper_shadow = H - np.maximum(O, C)
    _lower_shadow = np.minimum(O, C) - L
    _body = ABS(C - O)
    _long_shadow_yin = (C < O) & ((_upper_shadow + _lower_shadow) > _body * 2)
    _hist_vol = V == HHV(V, 120)
    _dafengche = _accel & _hist_vol & _long_shadow_yin & (V > REF(V, 1))
    no_s1_dafengche = not EXIST(_s1 | _dafengche, _s1p)[i]
    vol_expand_ok = (has_vol_expand and no_shrinkage_surge
                     and no_consec_limit_shrink and no_heavy_decline
                     and no_s1_dafengche)

    if not (weekly_ok and above_ma30w and stock_macd_ok and vol_expand_ok):
        return {"weekly": weekly_ok and above_ma30w,
                "market_macd": True, "b1": False, "stock_macd": stock_macd_ok,
                "vol_expand": vol_expand_ok,
                "close": C[i], "J": J[i], "RSI": rsi[i],
                "shrink_score": 0}

    # 振幅 / 异动
    is_tech = params["stock_type"] == "tech"
    pct_change = np.where(LC > 0, C / LC - 1, 0.0)
    volatile = EXIST(pct_change > 0.15, 200)
    is_volatile = volatile[i] or is_tech
    amp_range = 8.0 if is_volatile else 5.0
    relax = 0.9 if is_volatile else 1.0

    daily_amp = (H[i] - L[i]) / L[i] * 100
    daily_pct = abs(C[i] - C[i - 1]) / C[i - 1] * 100 * relax if C[i - 1] > 0 else 0.0
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

    return {"weekly": True, "gc": True, "market_macd": True, "b1": b1,
            "stock_macd": stock_macd_ok, "vol_expand": vol_expand_ok,
            "close": C[i], "J": J[i], "RSI": rsi[i], "shrink_score": shrink_score}


# ---------- 多进程扫描 V2 ----------

_process_reader = None


def _init_process(tdxdir, market):
    global _process_reader
    _process_reader = Reader.factory(market=market, tdxdir=tdxdir)
    from src.data.adjustment import preload_disk_cache
    preload_disk_cache()


def _scan_one(code, params, skip_weekly, market_macd_ok=True):
    """扫描单只股票（大盘MACD在调用方层面已判断，此处直接使用 market_macd_ok）"""
    assert _process_reader is not None, "_process_reader 未初始化，请在子进程中调用"
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
        weekly_ok = skip_weekly or sig["weekly"]
        stock_macd_ok = sig.get("stock_macd", True)
        vol_expand_ok = sig.get("vol_expand", True)
        if sig["b1"] and weekly_ok and stock_macd_ok and vol_expand_ok and market_macd_ok:
            sig["code"] = code
            return code, sig, False
        return code, None, False
    except Exception as e:
        return code, {"error": str(e)}, True


def scan_all(stock_type="main", skip_weekly=False,
             tdxdir=TDX_DIR, market=TDX_MARKET, max_workers=SCAN_MAX_WORKERS,
             skip_on_bear=False):
    """V2全市场扫描：增加大盘MACD过滤

    Args:
        skip_on_bear: 大盘空头时跳过扫描（节省时间），默认 False（仍扫描但不执行买入）
    """
    from concurrent.futures import ProcessPoolExecutor, as_completed

    # 检查大盘MACD状态
    market_macd_ok = True
    market_df = load_market_index(tdxdir, market)
    if market_df is not None and len(market_df) > 0:
        market_close = market_df["close"].values.astype(float)
        _, _, bullish = compute_market_macd(market_close)
        market_macd_ok = bool(bullish[-1])
        status = "多头" if market_macd_ok else "空头"
        print(f"  大盘MACD状态: {status} (最新收盘={market_close[-1]:.2f})")
        if not market_macd_ok:
            if skip_on_bear:
                print("  大盘MACD处于空头区间，跳过扫描 (skip_on_bear=True)")
                return [], market_macd_ok
            print("  大盘MACD处于空头区间，仅扫描不执行买入")
    else:
        print("  警告: 无法获取大盘MACD数据，跳过大盘过滤")

    codes = _get_all_codes(tdxdir)
    total = len(codes)
    print(f"扫描 {total} 只A股... (workers={max_workers or 'auto'})")

    params = {
        "m1": HUANGBAI_M1, "m2": HUANGBAI_M2, "m3": HUANGBAI_M3, "m4": HUANGBAI_M4,
        "n": HUANGBAI_N, "m": HUANGBAI_M, "n1": HUANGBAI_N1, "n2": HUANGBAI_N2,
        "wma30": 30, "wma60": 60, "wma120": 120, "wma240": 240,
        "stock_type": stock_type,
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
    if not market_macd_ok:
        print(f"  [注意] 大盘MACD空头，建议不执行买入")
    print(f"{'=' * 55}")

    if results:
        print(f"\n  选股结果（按缩量排序）")
        print(f"{'=' * 55}")
        for r in results:
            tag = " <<< TOP" if r == results[0] else ""
            print(f"  {r['code']}  C={r['close']:.2f}  "
                  f"J={r['J']:.1f}  RSI={r['RSI']:.1f}  "
                  f"缩量={r['shrink_score']:.3f}{tag}")

    return results, market_macd_ok


# ================================================================== #
#  组合级模拟 V2：增加大盘MACD每bar过滤                                #
# ================================================================== #

def _compute_all_bar_signals(C, H, L, O, V, dates, params):
    """计算每根 bar 的信号数组（与V1相同，大盘MACD在模拟器层面过滤）"""
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

    ma30w = _weekly_ma(C, dates, params["wma30"])
    ma60w = _weekly_ma(C, dates, params["wma60"])
    ma120w = _weekly_ma(C, dates, params["wma120"])
    ma240w = _weekly_ma(C, dates, params["wma240"])
    valid = (ma30w > 0.01) & (ma60w > 0.01) & (ma120w > 0.01) & (ma240w > 0.01)
    weekly_bull = valid & (ma30w > ma60w) & (ma60w > ma120w) & (ma120w > ma240w)
    above_ma30w = C > ma30w

    recent_gc = np.ones(len(C), dtype=bool)  # V5无金叉条件，始终为True

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

    # 个股MACD多头过滤（暂未启用，预留接口）
    stock_macd_bullish = np.ones(len(C), dtype=bool)

    # 前期放量上涨过滤 + 排除缩量快速拉升 + 连续涨停缩量排除 + 放量下跌排除
    vol_expand = (V > REF(V, 1) * 1.8) & (C > O) & (C > LC)
    _vep, _vem = HUANGBAI_VOL_EXPAND_PERIOD, HUANGBAI_VOL_EXPAND_MIN
    has_vol_expand = COUNT(vol_expand, _vep) >= _vem
    _ref_c = REF(C, _vep)
    _price_rise = np.where(np.abs(_ref_c) > 0.001,
                           (C - _ref_c) / np.abs(_ref_c) * 100, 0)
    _vol_ratio = MA(V, _vep) / np.maximum(MA(V, 60), 1)
    no_shrinkage_surge = ~((_price_rise > HUANGBAI_SURGE_PRICE_PCT)
                           & (_vol_ratio < HUANGBAI_SURGE_VOL_RATIO))
    # 连续涨停缩量排除
    _lp = 1.20 if params.get("stock_type") == "tech" else 1.10
    _limit_up = C >= np.round(REF(C, 1) * _lp, 2)
    _limit_shrink = _limit_up & (V < REF(V, 1))
    no_consec_limit_shrink = COUNT(_limit_shrink.astype(float), _vep) < 1
    # 连续上涨后放量下跌排除：近N天下跌日总成交量 > 上涨日总成交量
    _rise_v = np.where(C > REF(C, 1), V, 0)
    _decline_v = np.where(C < REF(C, 1), V, 0)
    _rvs = pd.Series(_rise_v).rolling(_vep, min_periods=1).sum().values
    _dvs = pd.Series(_decline_v).rolling(_vep, min_periods=1).sum().values
    no_heavy_decline = ~(_dvs > _rvs)
    # S1/大风车排除
    _s1p = HUANGBAI_S1_PERIOD
    _accel = (C - REF(C, 5)) / np.maximum(REF(C, 5), 0.001) * 100 > 15
    _big_vol = (V > HHV(V, 20) * 2) | (V > MA(V, 60) * 3)
    _big_yin = (C < O) & ((O - C) / np.maximum(REF(C, 1), 0.001) * 100 > 3)
    _s1 = _accel & _big_vol & _big_yin
    _upper_shadow = H - np.maximum(O, C)
    _lower_shadow = np.minimum(O, C) - L
    _body = ABS(C - O)
    _long_shadow_yin = (C < O) & ((_upper_shadow + _lower_shadow) > _body * 2)
    _hist_vol = V == HHV(V, 120)
    _dafengche = _accel & _hist_vol & _long_shadow_yin & (V > REF(V, 1))
    no_s1_dafengche = ~EXIST(_s1 | _dafengche, _s1p)
    vol_expand_ok = (has_vol_expand & no_shrinkage_surge
                     & no_consec_limit_shrink & no_heavy_decline
                     & no_s1_dafengche)

    # 筹码密集度（COST近似）
    _chip_period = 60
    _sum_cv = pd.Series(C * V).rolling(_chip_period, min_periods=1).sum().values
    _sum_v = pd.Series(V).rolling(_chip_period, min_periods=1).sum().values
    _vwap = _sum_cv / np.maximum(_sum_v, 1)
    _chip_spread = (HHV(C, _chip_period) - LLV(C, _chip_period)) / np.maximum(_vwap, 0.001) * 100
    _conc_low = _chip_spread == LLV(_chip_spread, _chip_period)
    _price_near = ABS(C - _vwap) / np.maximum(_vwap, 0.001) <= 0.10
    chip_dense = _conc_low & _price_near

    # ---- 砖型图 ----
    hhv4 = HHV(H, 4)
    llv4 = LLV(L, 4)
    _br1 = (hhv4 - C) / np.maximum(hhv4 - llv4, 0.001) * 100 - 90
    _br2 = SMA(_br1, 4, 1) + 100
    _br3 = (C - llv4) / np.maximum(hhv4 - llv4, 0.001) * 100
    _br4 = SMA(_br3, 6, 1)
    _br5 = SMA(_br4, 6, 1) + 100
    _br6 = _br5 - _br2
    brick = np.where(_br6 > 4, _br6 - 4, 0)

    return {
        "weekly_bull": weekly_bull,
        "above_ma30w": above_ma30w,
        "recent_gc": recent_gc,
        "b1": b1,
        "shrink_score": shrink_score,
        "stock_macd_bullish": stock_macd_bullish,
        "vol_expand_ok": vol_expand_ok,
        "chip_dense": chip_dense,
        "chip_spread": _chip_spread,
        "white": white,
        "yellow": yellow,
        "bbi": bbi,
        "close": C,
        "high": H,
        "low": L,
        "open": O,
        "volume": V,
        "dates": dates,
        "brick_value": brick,
        # V5: 预计算量能和高价指标，供模拟器退出逻辑使用
        "ma_v20": MA(V, 20),
        "hhv_v20": HHV(V, 20),
        "hhv_v50": HHV(V, 50),
        "ma_v60": MA(V, 60),
        "hhv_h20": HHV(H, 20),
    }


def _scan_one_all_bars(code, params):
    assert _process_reader is not None, "_process_reader 未初始化，请在子进程中调用"
    try:
        df = _process_reader.daily(symbol=code)
        if df is None or len(df) < 300:
            return code, None, False
        df = df.sort_index()
        from src.data.adjustment import apply_qfq
        df = apply_qfq(df, code)
        signals = _compute_all_bar_signals(
            df["close"].values.astype(float),
            df["high"].values.astype(float),
            df["low"].values.astype(float),
            df["open"].values.astype(float),
            df["volume"].values.astype(float),
            df.index, params)
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
    """V2预加载：增加大盘MACD计算

    Returns:
        (all_signals, trading_days, market_macd_bullish)
    """
    from concurrent.futures import ProcessPoolExecutor, as_completed

    codes = _get_all_codes(tdxdir)
    total = len(codes)
    print(f"预加载 {total} 只A股信号... (workers={max_workers or 'auto'})")

    params = {
        "m1": HUANGBAI_M1, "m2": HUANGBAI_M2, "m3": HUANGBAI_M3, "m4": HUANGBAI_M4,
        "n": HUANGBAI_N, "m": HUANGBAI_M, "n1": HUANGBAI_N1, "n2": HUANGBAI_N2,
        "wma30": 30, "wma60": 60, "wma120": 120, "wma240": 240,
        "stock_type": stock_type,
    }
    t0 = time.time()
    all_dates_index = pd.DatetimeIndex([])
    all_signals = {}
    errors = 0
    error_details = []
    done = 0

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

    # V2新增：计算大盘MACD多头状态
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
