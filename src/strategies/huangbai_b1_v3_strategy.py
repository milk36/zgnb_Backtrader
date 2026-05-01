"""黄白线金叉后B1策略 V3

相比 V2:
- B1买入信号改为通达信原始B1选股公式（单一复合条件，非7个子条件）
- 止盈逻辑调整：中阳优先于涨停检查，涨停在中阳触发后不再触发

策略逻辑：
0. 选股范围：沪深A股
1. 周线多头空间
2. 大盘MACD处于多头区间
3. 黄白线金叉
4. B1买入信号（通达信原始公式）
5. 分层止损/止盈
"""

import os
import re
import time
import warnings

import numpy as np
import pandas as pd
import backtrader as bt

warnings.filterwarnings("ignore", category=RuntimeWarning)
from MyTT import EMA, MA, SMA, HHV, LLV, REF, COUNT, CROSS, MAX, ABS, \
    BARSLAST, HHVBARS
from mootdx.reader import Reader
from src.indicators.kdj_indicator import KDJIndicator
from src.strategies.base_strategy import BaseStrategy

from config import (
    TDX_DIR, TDX_MARKET, SCAN_MAX_WORKERS, STOCK_TYPE,
    HUANGBAI_M1, HUANGBAI_M2, HUANGBAI_M3, HUANGBAI_M4,
    HUANGBAI_T_PLUS_N, HUANGBAI_GC_LOOKBACK,
    MARKET_INDEX_CODE, MARKET_MACD_FAST, MARKET_MACD_SLOW, MARKET_MACD_SIGNAL,
)


# ---------- helpers ----------

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


def _rolling_sum(arr, n):
    """通达信 SUM(X, N) 等价：N周期滚动求和"""
    return pd.Series(np.asarray(arr, dtype=float)).rolling(
        n, min_periods=1).sum().values


# ---------- V3 B1 核心（共享） ----------

def _compute_v3_b1(C, H, L, O, V, skip_mvok=True):
    """基于通达信原始B1公式计算信号

    Returns:
        b1: np.ndarray[bool]  每根bar的B1信号
        shrink_score: np.ndarray[float]  缩量评分（用于候选排序）
        J: np.ndarray[float]  KDJ-J值（用于日志）
    """
    LC = REF(C, 1)

    # 真阳真阴
    REAL_YANG = (C > O) & ~(C < LC)
    REAL_YIN = (C < O) & ~(C > LC)

    # KDJ
    llv9, hhv9 = LLV(L, 9), HHV(H, 9)
    denom = hhv9 - llv9
    rsv = np.where(denom != 0, (C - llv9) / denom * 100, 50.0)
    K = SMA(rsv, 3, 1)
    D = SMA(K, 3, 1)
    J = 3 * K - 2 * D
    J_OK = J <= 13

    # 阳量/阴量分析
    vol_yang1 = _rolling_sum(V * REAL_YANG, 57)
    vol_yin1 = _rolling_sum(V * REAL_YIN, 57)
    vol_yang2 = _rolling_sum(V * REAL_YANG, 14)
    vol_yin2 = _rolling_sum(V * REAL_YIN, 14)
    yangyin_ok1 = vol_yang1 > 1.25 * vol_yin1
    yangyin_ok2 = vol_yang2 > 2.25 * vol_yin2

    # 流通市值过滤（默认跳过，周线多头已包含此过滤）
    MVOK = np.ones(len(C), dtype=bool)

    # 高位放量跌过滤
    O85 = LLV(O, 21) + 0.95 * (HHV(O, 21) - LLV(O, 21))
    TOP15O = O >= O85
    FD15 = (C < LC) & (C <= O) & (V >= 1.2 * REF(V, 1))
    CNT28 = COUNT(TOP15O & FD15, 21)
    GOOD28 = CNT28 <= 0

    # 均量
    AVG40 = MA(V, 40)

    # 放量阳
    PLRY = (V > 1.95 * REF(V, 1)) & (C > O) & (V > AVG40)
    PLRY_CNT = (COUNT(PLRY, 14) >= 2) | (COUNT(PLRY, 57) >= 4)

    # 放量阳细分
    PLRY_FIRST = PLRY & ~REF(PLRY, 1)
    PLRY_CONT = PLRY & REF(PLRY, 1)

    # 缩量下跌
    PRE_NOT_REALYIN = ~REF(REAL_YIN, 1)
    HALF_DOWN = PRE_NOT_REALYIN & (C < LC) & (V <= 0.5 * REF(V, 1))

    # 三项统计
    CNT_FIRST = COUNT(PLRY_FIRST, 57)
    CNT_CONT = COUNT(PLRY_CONT, 57)
    CNT_HALF = COUNT(HALF_DOWN, 57)
    THREE_SUM_OK = (CNT_FIRST + CNT_CONT + CNT_HALF) >= 4

    # 28日最大量非阴线
    MAXVOL28 = HHV(V, 28)
    MAX28_BAD = (V == MAXVOL28) & REAL_YIN
    MAX28_OK = COUNT(MAX28_BAD, 28) == 0

    # A1 复合条件
    A1 = ((PLRY_CNT & yangyin_ok1 & J_OK & MVOK & GOOD28 & THREE_SUM_OK & MAX28_OK)
          | (PLRY_CNT & yangyin_ok2 & J_OK & MVOK & GOOD28 & MAX28_OK))

    # 短期/长期均线
    HMSHORTWL = SMA(SMA(C, 40, 4), 100, 50)
    HMLONGYL = (0.5 * (0.2 * MA(C, 12) + 0.3 * MA(C, 24)
                        + 0.3 * MA(C, 52) + 0.2 * MA(C, 108))
                + 0.5 * (0.4 * MA(C, 20) + 0.25 * MA(C, 40)
                         + 0.25 * MA(C, 80) + 0.1 * MA(C, 160)))

    # B1 最终条件
    b1 = (HMSHORTWL >= HMLONGYL * 0.985) & (C >= HMLONGYL * 0.985) & A1

    # 缩量评分（候选排序用）
    hhv_v20 = HHV(V, 20)
    shrink_score = np.where(hhv_v20 > 0, V / hhv_v20, 1.0)

    return b1, shrink_score, J


# ---------- 大盘MACD（同V2） ----------

def load_market_index(tdxdir=TDX_DIR, market=TDX_MARKET):
    """加载上证指数日线数据"""
    try:
        reader = Reader.factory(market=market, tdxdir=tdxdir)
        df = reader.daily(symbol=MARKET_INDEX_CODE)
        if df is not None and len(df) > 0:
            return df.sort_index()
    except Exception:
        pass
    return None


def compute_market_macd(close, fast=MARKET_MACD_FAST,
                        slow=MARKET_MACD_SLOW,
                        signal=MARKET_MACD_SIGNAL):
    dif = EMA(close, fast) - EMA(close, slow)
    dea = EMA(dif, signal)
    bullish = np.where(np.isnan(dif) | np.isnan(dea), False, dif > dea)
    return dif, dea, bullish


def compute_market_macd_for_trading_days(trading_days, tdxdir=TDX_DIR,
                                         market=TDX_MARKET):
    df = load_market_index(tdxdir, market)
    if df is None:
        print("  警告: 无法加载大盘指数数据，大盘MACD过滤将被跳过")
        return None

    close = df["close"].values.astype(float)
    _, _, bullish = compute_market_macd(close)

    result = {}
    for i, dt in enumerate(df.index):
        if i < len(bullish):
            result[pd.Timestamp(dt)] = bool(bullish[i])

    macd_arr = np.zeros(len(trading_days), dtype=bool)
    for j, td in enumerate(trading_days):
        ts = pd.Timestamp(td)
        if ts in result:
            macd_arr[j] = result[ts]
        else:
            mask = df.index <= ts
            if mask.any():
                last_idx = np.where(mask)[0][-1]
                if last_idx < len(bullish):
                    macd_arr[j] = bool(bullish[last_idx])
    return macd_arr


# ---------- 策略类 ----------

class HuangBaiB1V3Strategy(BaseStrategy):
    """V3: 周线多头 + 大盘MACD多头 + 黄白线金叉 + 通达信B1"""

    params = (
        ("print_log", True),
        ("position_pct", 0.1),
        ("stock_type", STOCK_TYPE),

        ("m1", HUANGBAI_M1), ("m2", HUANGBAI_M2),
        ("m3", HUANGBAI_M3), ("m4", HUANGBAI_M4),

        ("t_plus_n", HUANGBAI_T_PLUS_N),

        ("wma30", 30), ("wma60", 60), ("wma120", 120), ("wma240", 240),

        ("gc_lookback", HUANGBAI_GC_LOOKBACK),

        ("skip_weekly", False),
        ("skip_gc", False),
        ("skip_market_macd", False),
    )

    def __init__(self):
        self.order = None
        self.buy_info = None
        self.stop_loss_price = None
        self.hold_until_below_white = False
        self.initial_size = 0
        self._last_sl_bar = None
        self._mid_yang_triggered = False

        self.indicators()

        self._market_macd_bullish = None
        if len(self.datas) > 1:
            market_close = np.array(self.data1.close.array, dtype=float)
            _, _, self._market_macd_bullish = compute_market_macd(market_close)

    def indicators(self):
        C = np.array(self.data.close.array, dtype=float)
        H = np.array(self.data.high.array, dtype=float)
        L = np.array(self.data.low.array, dtype=float)
        O = np.array(self.data.open.array, dtype=float)
        V = np.array(self.data.volume.array, dtype=float)

        # 黄白线
        self._white = EMA(EMA(C, 10), 10)
        self._yellow = (MA(C, self.p.m1) + MA(C, self.p.m2)
                        + MA(C, self.p.m3) + MA(C, self.p.m4)) / 4

        # KDJ（用于日志）
        self.kdj = KDJIndicator(self.data)

        # 周线多头
        dates = [bt.num2date(d) for d in self.data.datetime.array]
        ma30w = _weekly_ma(C, dates, self.p.wma30)
        ma60w = _weekly_ma(C, dates, self.p.wma60)
        ma120w = _weekly_ma(C, dates, self.p.wma120)
        ma240w = _weekly_ma(C, dates, self.p.wma240)
        valid = (ma30w > 0.01) & (ma60w > 0.01) & (ma120w > 0.01) & (ma240w > 0.01)
        self._weekly_bull = valid & (ma30w > ma60w) & (ma60w > ma120w) & (ma120w > ma240w)
        self._above_ma30w = C > ma30w

        # 黄白线金叉
        gc_arr = CROSS(self._white, self._yellow)
        bars_since_gc = BARSLAST(gc_arr)
        self._recent_gc = bars_since_gc <= self.p.gc_lookback

        # B1（V3通达信公式）
        self._b1, _, _ = _compute_v3_b1(C, H, L, O, V)

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

        market_macd_ok = self.p.skip_market_macd
        if not market_macd_ok and self._market_macd_bullish is not None:
            market_macd_ok = self._market_macd_bullish[idx]

        weekly_ok = self.p.skip_weekly or (self._weekly_bull[idx] and self._above_ma30w[idx])
        gc_ok = self.p.skip_gc or self._recent_gc[idx]
        b1_ok = self._b1[idx]

        if self.p.print_log:
            self._print_filter_result(dt, weekly_ok, gc_ok, b1_ok, market_macd_ok)

        if not market_macd_ok:
            return
        if not weekly_ok or not gc_ok or not b1_ok:
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
        self.hold_until_below_white = False
        self.initial_size = 0
        self._mid_yang_triggered = False

        self.log(f"买入  @ {self.data.close[0]:.2f}  止损={sl:.2f}")

    def _check_exit(self):
        """出场逻辑（与V1/V2相同）"""
        idx = len(self) - 1
        price = self.data.close[0]
        high = self.data.high[0]
        white_val = self._white[idx]
        bars_held = len(self) - self.buy_info["bar"]

        if self.initial_size == 0:
            self.initial_size = self.position.size

        pct_gain = (price - self.buy_info["price"]) / self.buy_info["price"] * 100

        # 1. 止损
        if price <= self.stop_loss_price:
            self.order = self.order_target_percent(target=0.0)
            self._last_sl_bar = len(self)
            self.log(f"止损 @ {price:.2f}  亏损={pct_gain:+.2f}%")
            self._reset_position_state()
            return

        # 2. T+3 没涨清仓
        if bars_held >= self.p.t_plus_n and price <= self.buy_info["price"]:
            self.order = self.order_target_percent(target=0.0)
            self._last_sl_bar = len(self)
            self.log(f"T+{bars_held} 清仓 @ {price:.2f}  盈亏={pct_gain:+.2f}%")
            self._reset_position_state()
            return

        # 3. 盈利100%清仓
        if pct_gain >= 100:
            self.order = self.order_target_percent(target=0.0)
            self._last_sl_bar = len(self)
            self.log(f"盈利100%清仓 @ {price:.2f}  盈亏={pct_gain:+.2f}%")
            self._reset_position_state()
            return

        # 4. 半仓持股模式（仅涨停可卖1/2，中阳不再触发）
        if self.hold_until_below_white:
            if pct_gain <= 20:
                if price <= self.buy_info["price"]:
                    self.order = self.order_target_percent(target=0.0)
                    self._last_sl_bar = len(self)
                    self.log(f"半仓盈转亏清仓 @ {price:.2f}  盈亏={pct_gain:+.2f}%")
                    self._reset_position_state()
                    return
            else:
                if price < white_val:
                    self.order = self.order_target_percent(target=0.0)
                    self._last_sl_bar = len(self)
                    self.log(f"半仓跌破白线 @ {price:.2f}  盈亏={pct_gain:+.2f}%")
                    self._reset_position_state()
                    return
            # 未清仓：继续检查涨停卖1/2

        # 5. 涨停卖1/2（半仓模式下仍可触发，不受中阳标记限制）
        limit_pct = 1.20 if self.p.stock_type == "tech" else 1.10
        prev_close = self.data.close[-1]
        limit_up_price = round(prev_close * limit_pct, 2)
        if high >= limit_up_price:
            sell_size = max(1, int(self.position.size / 2))
            if sell_size < self.position.size:
                self.order = self.sell(size=sell_size)
                self.log(f"涨停卖半 @ {price:.2f}  盈亏={pct_gain:+.2f}%")
                if self.position.size - sell_size <= self.initial_size / 2:
                    self.hold_until_below_white = True
            return

        # 6. 中阳卖1/3（半仓模式下不触发）
        if not self.hold_until_below_white:
            mid_yang = 10 if self.p.stock_type == "tech" else 5
            if pct_gain >= mid_yang:
                sell_size = max(1, int(self.position.size / 3))
                if sell_size < self.position.size:
                    self.order = self.sell(size=sell_size)
                    self._mid_yang_triggered = True
                    self.log(f"中阳卖1/3 @ {price:.2f}  盈亏={pct_gain:+.2f}%")
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
            print(f"[{dt.isoformat()}] {sym}  [B1V3] {txt}")

    def _print_filter_result(self, dt, weekly_ok, gc_ok, b1_ok, market_macd_ok=True):
        sym = self.data._name or "?"
        idx = len(self) - 1
        w = "Y" if weekly_ok else "N"
        g = "Y" if gc_ok else "N"
        b = "Y" if b1_ok else "N"
        m = "Y" if market_macd_ok else "N"
        all_pass = market_macd_ok and weekly_ok and gc_ok and b1_ok

        if not b1_ok and not all_pass:
            return

        tag = " <<< SELECT" if all_pass else ""
        print(f"[{dt.isoformat()}] {sym}  [B1V3] 大盘={m}  周线={w}  金叉={g}  B1={b}  "
              f"C={self.data.close[0]:.2f}  "
              f"J={self.kdj._j[idx]:.1f}"
              f"{tag}")

    def buy_signal(self) -> bool:
        return False

    def sell_signal(self) -> bool:
        return False


# ================================================================== #
#  全市场选股扫描 V3                                                   #
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


def _compute_signals(C, H, L, O, V, dates, params):
    """计算最新bar的四级过滤结果"""
    n = len(C)
    if n < 300:
        return None

    white = EMA(EMA(C, 10), 10)
    yellow = (MA(C, params["m1"]) + MA(C, params["m2"])
              + MA(C, params["m3"]) + MA(C, params["m4"])) / 4

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

    # B1
    b1_arr, shrink_arr, J_arr = _compute_v3_b1(C, H, L, O, V)
    b1_ok = bool(b1_arr[i])
    shrink_score = float(shrink_arr[i])

    if not (weekly_ok and above_ma30w and gc_ok and b1_ok):
        return {"weekly": weekly_ok and above_ma30w, "gc": gc_ok,
                "market_macd": True, "b1": b1_ok,
                "close": C[i], "J": J_arr[i],
                "shrink_score": shrink_score}

    return {"weekly": True, "gc": True, "market_macd": True, "b1": b1_ok,
            "close": C[i], "J": J_arr[i], "shrink_score": shrink_score}


_process_reader = None


def _init_process(tdxdir, market):
    global _process_reader
    _process_reader = Reader.factory(market=market, tdxdir=tdxdir)


def _scan_one(code, params, skip_weekly, skip_gc, market_macd_ok=True):
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
        if sig["b1"] and weekly_ok and gc_ok and market_macd_ok:
            sig["code"] = code
            return code, sig, False
        return code, None, False
    except Exception:
        return code, None, True


def scan_all(stock_type="main", skip_weekly=False, skip_gc=False,
             tdxdir=TDX_DIR, market=TDX_MARKET, max_workers=SCAN_MAX_WORKERS):
    """V3全市场扫描：含大盘MACD过滤"""
    from concurrent.futures import ProcessPoolExecutor, as_completed

    market_macd_ok = True
    market_df = load_market_index(tdxdir, market)
    if market_df is not None and len(market_df) > 0:
        market_close = market_df["close"].values.astype(float)
        _, _, bullish = compute_market_macd(market_close)
        market_macd_ok = bool(bullish[-1])
        status = "多头" if market_macd_ok else "空头"
        print(f"  大盘MACD状态: {status} (最新收盘={market_close[-1]:.2f})")
        if not market_macd_ok:
            print("  大盘MACD处于空头区间，仅扫描不执行买入")
    else:
        print("  警告: 无法获取大盘MACD数据，跳过大盘过滤")

    codes = _get_all_codes(tdxdir)
    total = len(codes)
    print(f"扫描 {total} 只A股... (workers={max_workers or 'auto'})")

    params = {
        "m1": HUANGBAI_M1, "m2": HUANGBAI_M2,
        "m3": HUANGBAI_M3, "m4": HUANGBAI_M4,
        "wma30": 30, "wma60": 60, "wma120": 120, "wma240": 240,
        "gc_lookback": HUANGBAI_GC_LOOKBACK,
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
            pool.submit(_scan_one, code, params, skip_weekly, skip_gc, market_macd_ok): code
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
                      f"C={sig['close']:.2f}  J={sig['J']:.1f}  "
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
                  f"J={r['J']:.1f}  "
                  f"缩量={r['shrink_score']:.3f}{tag}")

    return results, market_macd_ok


# ================================================================== #
#  组合级模拟 V3                                                       #
# ================================================================== #

def _compute_all_bar_signals(C, H, L, O, V, dates, params):
    """计算每根bar的信号数组"""
    n = len(C)
    if n < 300:
        return None

    white = EMA(EMA(C, 10), 10)
    yellow = (MA(C, params["m1"]) + MA(C, params["m2"])
              + MA(C, params["m3"]) + MA(C, params["m4"])) / 4

    # 周线多头
    ma30w = _weekly_ma(C, dates, params["wma30"])
    ma60w = _weekly_ma(C, dates, params["wma60"])
    ma120w = _weekly_ma(C, dates, params["wma120"])
    ma240w = _weekly_ma(C, dates, params["wma240"])
    valid = (ma30w > 0.01) & (ma60w > 0.01) & (ma120w > 0.01) & (ma240w > 0.01)
    weekly_bull = valid & (ma30w > ma60w) & (ma60w > ma120w) & (ma120w > ma240w)
    above_ma30w = C > ma30w

    # 黄白线金叉
    gc_arr = CROSS(white, yellow)
    bars_since_gc = BARSLAST(gc_arr)
    recent_gc = np.asarray(bars_since_gc, dtype=float) <= params["gc_lookback"]

    # B1
    b1, shrink_score, _ = _compute_v3_b1(C, H, L, O, V)

    # 筹码密集度（COST近似）
    _chip_period = 60
    _sum_cv = pd.Series(C * V).rolling(_chip_period, min_periods=1).sum().values
    _sum_v = pd.Series(V).rolling(_chip_period, min_periods=1).sum().values
    _vwap = _sum_cv / np.maximum(_sum_v, 1)
    _chip_spread = (HHV(C, _chip_period) - LLV(C, _chip_period)) / np.maximum(_vwap, 0.001) * 100
    _conc_low = _chip_spread == LLV(_chip_spread, _chip_period)
    _price_near = ABS(C - _vwap) / np.maximum(_vwap, 0.001) <= 0.10
    chip_dense = _conc_low & _price_near

    return {
        "weekly_bull": weekly_bull,
        "above_ma30w": above_ma30w,
        "recent_gc": recent_gc,
        "b1": b1,
        "shrink_score": shrink_score,
        "chip_dense": chip_dense,
        "chip_spread": _chip_spread,
        "white": white,
        "yellow": yellow,
        "close": C,
        "high": H,
        "low": L,
        "open": O,
        "volume": V,
        "dates": dates,
    }


def _scan_one_all_bars(code, params):
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
        if signals is not None:
            amount = df["amount"].values.astype(float)
            signals["avg_amount_20"] = pd.Series(amount).rolling(
                20, min_periods=1).mean().values
        return code, signals, False
    except Exception:
        return code, None, True


def preload_all_signals(start="2024-01-01", end="2025-12-31",
                        stock_type="main", max_workers=SCAN_MAX_WORKERS,
                        tdxdir=TDX_DIR, market=TDX_MARKET):
    """V3预加载

    Returns:
        (all_signals, trading_days, market_macd_bullish)
    """
    from concurrent.futures import ProcessPoolExecutor, as_completed

    codes = _get_all_codes(tdxdir)
    total = len(codes)
    print(f"预加载 {total} 只A股信号... (workers={max_workers or 'auto'})")

    params = {
        "m1": HUANGBAI_M1, "m2": HUANGBAI_M2,
        "m3": HUANGBAI_M3, "m4": HUANGBAI_M4,
        "wma30": 30, "wma60": 60, "wma120": 120, "wma240": 240,
        "gc_lookback": HUANGBAI_GC_LOOKBACK,
        "stock_type": stock_type,
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
