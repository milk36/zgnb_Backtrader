"""StockTradebyZ B1 选股策略

参考 https://github.com/SebastienZh/StockTradebyZ 的 4-Filter B1 选股逻辑，
使用本地通达信数据实现。

4 个独立 Filter（AND 关系）:
1. KDJQuantileFilter   — J 值低位过滤
2. ZXConditionFilter   — 知行线条件（C > 黄线 AND 白线 > 黄线）
3. WeeklyMABullFilter  — 周线多头排列（MA20 > MA60 > MA120）
4. MaxVolNotBearishFilter — 过去 N 日最大成交量日非阴线

支持两种运行模式:
- scan_all()      全市场选股扫描（仅最新 bar）
- preload_all_signals()  预加载全市场每 bar 信号（供 PortfolioSimulator）
"""

import os
import time
import logging
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd
from MyTT import EMA, MA, SMA, HHV, LLV

from config import (
    TDX_DIR, TDX_MARKET, SCAN_MAX_WORKERS,
    ZSTOCK_KDJ_J_THRESHOLD, ZSTOCK_KDJ_J_Q_THRESHOLD,
    ZSTOCK_ZX_EMA_PERIOD, ZSTOCK_ZX_MA_PERIODS,
    ZSTOCK_WEEKLY_MA_PERIODS, ZSTOCK_MAXVOL_LOOKBACK,
    COMMISSION,
)
from src.data.adjustment import apply_qfq
from src.strategies.dongneng_zhuan_strategy import _load_capital_data
from src.strategies.nxing_b1_scan_strategy import _get_all_codes

logger = logging.getLogger(__name__)

# ============================================================================
# 进程级全局变量（子进程初始化时赋值）
# ============================================================================
_process_reader = None
_process_capital = None


def _init_process(tdxdir, market):
    """子进程初始化：创建 mootdx Reader + 前复权缓存"""
    global _process_reader, _process_capital
    from mootdx.reader import Reader
    _process_reader = Reader.factory(market=market, tdxdir=tdxdir)
    _process_capital = _load_capital_data(tdxdir)


# ============================================================================
# 4 个 Filter 的向量化计算
# ============================================================================

def _compute_kdj_filter(C, H, L, j_threshold, j_q_threshold):
    """Filter 1: KDJQuantileFilter

    条件: J < j_threshold OR J <= expanding_quantile(J, j_q_threshold)

    Returns:
        (passed, J, K, D)
    """
    n = len(C)
    llv9 = LLV(L, 9)
    hhv9 = HHV(H, 9)
    denom = hhv9 - llv9
    rsv = np.where(denom > 0, (C - llv9) / denom * 100, 50.0)
    K = SMA(rsv, 3, 1)
    D = SMA(K, 3, 1)
    J = 3 * K - 2 * D

    # 绝对阈值
    abs_passed = J < j_threshold

    # expanding 分位数（截至当前位置的 J 在历史序列中的排名百分位）
    j_series = pd.Series(J)
    j_rank = j_series.expanding().rank(pct=True).values
    quantile_passed = j_rank <= j_q_threshold

    passed = abs_passed | quantile_passed
    return passed, J, K, D


def _compute_zx_filter(C, ema_period, ma_periods):
    """Filter 2: ZXConditionFilter

    条件: close > 黄线 AND 白线 > 黄线

    Returns:
        (passed, white, yellow)
    """
    m1, m2, m3, m4 = sorted(ma_periods)
    white = EMA(EMA(C, ema_period), ema_period)
    yellow = (MA(C, m1) + MA(C, m2) + MA(C, m3) + MA(C, m4)) / 4
    passed = (C > yellow) & (white > yellow)
    return passed, white, yellow


def _weekly_ma(daily_close, dates, period):
    """将日线重采样为周线（W-FRI），计算 MA，ffill 回日线"""
    s = pd.Series(daily_close, index=pd.to_datetime(dates))
    weekly = s.resample('W-FRI').last().dropna()
    wma = weekly.rolling(period).mean()
    return wma.reindex(s.index, method='ffill').values


def _compute_weekly_bull_filter(C, dates, periods):
    """Filter 3: WeeklyMABullFilter

    条件: 周线 MA_short > MA_mid > MA_long
    """
    p1, p2, p3 = sorted(periods)
    wma_s = _weekly_ma(C, dates, p1)
    wma_m = _weekly_ma(C, dates, p2)
    wma_l = _weekly_ma(C, dates, p3)
    valid = np.isfinite(wma_s) & np.isfinite(wma_m) & np.isfinite(wma_l)
    valid &= (wma_s > 0.01) & (wma_m > 0.01) & (wma_l > 0.01)
    passed = valid & (wma_s > wma_m) & (wma_m > wma_l)
    return passed


def _compute_maxvol_filter(C, O, V, lookback):
    """Filter 4: MaxVolNotBearishFilter

    条件: 过去 lookback 日内成交量最大的那天不能是阴线
    """
    n = len(C)
    passed = np.ones(n, dtype=bool)
    passed[:lookback] = False

    for i in range(lookback, n):
        seg_v = V[i - lookback + 1: i + 1]
        max_idx = np.argmax(seg_v) + (i - lookback + 1)
        if C[max_idx] < O[max_idx]:  # 阴线
            passed[i] = False
    return passed


# ============================================================================
# 信号计算（每 bar 数组版）
# ============================================================================

def _compute_all_bar_signals(C, H, L, O, V, dates, params):
    """计算每根 bar 的全部信号数组

    Returns:
        dict[str, ndarray] — 信号字典，兼容 PortfolioSimulator
    """
    # 4 个 Filter
    kdj_j_low, J, K_arr, D_arr = _compute_kdj_filter(
        C, H, L,
        params['j_threshold'], params['j_q_threshold'])
    zx_ok, white, yellow = _compute_zx_filter(
        C, params['ema_period'], params['ma_periods'])
    weekly_bull = _compute_weekly_bull_filter(
        C, dates, params['weekly_periods'])
    maxvol_ok = _compute_maxvol_filter(
        C, O, V, params['maxvol_lookback'])

    # B1 = 4 Filter AND
    b1 = kdj_j_low & zx_ok & weekly_bull & maxvol_ok

    # 缩量评分（值越小缩量越明显，用于排序取最优）
    hhv_v20 = HHV(V, 20)
    shrink_score = np.where(hhv_v20 > 0, V / hhv_v20, 1.0)

    # 20 日均成交额
    amount = C * V
    avg_amount_20 = pd.Series(amount).rolling(20).mean().values
    avg_amount_20 = np.nan_to_num(avg_amount_20, nan=0.0)

    # 砖型图（简化版，ZStock B1 不依赖砖型图，填充 0）
    brick = np.zeros(len(C), dtype=float)

    # 流通市值过滤（占位，后续在 preload 中填充）
    liutong_mask = np.ones(len(C), dtype=bool)

    return {
        # PortfolioSimulator 必需字段
        # weekly_bull/above_ma30w 控制观察池入场，设为 True 让所有股票进入观察池
        # Filter 3 的周线多头条件已包含在 b1 信号中
        "weekly_bull": np.ones(len(C), dtype=bool),
        "above_ma30w": np.ones(len(C), dtype=bool),
        "recent_gc": np.ones(len(C), dtype=bool),  # 无金叉条件
        "b1": b1,
        "dongneng_recent": np.ones(len(C), dtype=bool),  # 无动能过滤
        "vol_expand_ok": np.ones(len(C), dtype=bool),     # ZStock 不用此过滤
        "no_huge_vol_bearish": maxvol_ok,  # 复用 Filter 4
        "liutong_mask": liutong_mask,
        "shrink_score": shrink_score,
        "white": white,
        "yellow": yellow,
        "bbi": np.full(len(C), np.nan),  # 无 BBI
        "close": C,
        "high": H,
        "low": L,
        "open": O,
        "volume": V,
        "dates": dates,
        "brick_value": brick,
        "avg_amount_20": avg_amount_20,
        # ZStock 专用
        "kdj_j_low": kdj_j_low,
        "zx_condition": zx_ok,
        "weekly_bull_zs": weekly_bull,
        "maxvol_ok": maxvol_ok,
        "J": J,
    }


def _compute_signals(C, H, L, O, V, dates, params):
    """仅计算最新 bar 的信号（标量值），用于 scan_all"""
    n = len(C)
    if n < params['maxvol_lookback'] + 10:
        return None

    kdj_j_low, J, _, _ = _compute_kdj_filter(
        C, H, L, params['j_threshold'], params['j_q_threshold'])
    zx_ok, white, yellow = _compute_zx_filter(
        C, params['ema_period'], params['ma_periods'])
    weekly_bull = _compute_weekly_bull_filter(
        C, dates, params['weekly_periods'])
    maxvol_ok = _compute_maxvol_filter(
        C, O, V, params['maxvol_lookback'])

    b1 = kdj_j_low[-1] and zx_ok[-1] and weekly_bull[-1] and maxvol_ok[-1]
    if not b1:
        return None

    hhv_v20 = HHV(V, 20)
    shrink_score = V[-1] / hhv_v20[-1] if hhv_v20[-1] > 0 else 1.0

    return {
        "code": None,  # 调用方填充
        "close": float(C[-1]),
        "shrink_score": float(shrink_score),
        "J": float(J[-1]),
        "kdj_j_low": bool(kdj_j_low[-1]),
        "zx_ok": bool(zx_ok[-1]),
        "weekly_bull": bool(weekly_bull[-1]),
        "maxvol_ok": bool(maxvol_ok[-1]),
    }


# ============================================================================
# 单只股票处理
# ============================================================================

def _scan_one(code, params, tdxdir=None, market=None):
    """扫描单只股票（仅最新 bar），返回 (code, result_dict_or_None)"""
    global _process_reader
    try:
        reader = _process_reader
        if reader is None:
            from mootdx.reader import Reader
            reader = Reader.factory(market=market or TDX_MARKET,
                                    tdxdir=tdxdir or TDX_DIR)

        df = reader.daily(symbol=code)
        if df is None or len(df) < params['maxvol_lookback'] + 10:
            return code, None

        df = apply_qfq(df, code)
        if df is None or len(df) < params['maxvol_lookback'] + 10:
            return code, None

        C = df['close'].values.astype(float)
        H = df['high'].values.astype(float)
        L = df['low'].values.astype(float)
        O = df['open'].values.astype(float)
        V = df['volume'].values.astype(float)
        dates = df.index

        result = _compute_signals(C, H, L, O, V, dates, params)
        if result is not None:
            result['code'] = code
        return code, result
    except Exception as e:
        logger.debug("scan skip %s: %s", code, e)
        return code, None


def _scan_one_all_bars(code, params, start_date, end_date):
    """扫描单只股票（全部 bar），返回 (code, signal_dict_or_None)"""
    global _process_reader, _process_capital
    try:
        reader = _process_reader
        if reader is None:
            return code, None

        df = reader.daily(symbol=code)
        if df is None or len(df) < params['maxvol_lookback'] + 10:
            return code, None

        df = apply_qfq(df, code)
        if df is None or len(df) < params['maxvol_lookback'] + 10:
            return code, None

        # 日期过滤（warmup: 周线MA120需约120周≈840天，额外预留缓冲）
        if start_date:
            start_ts = pd.Timestamp(start_date)
            df = df[df.index >= start_ts - pd.Timedelta(days=1200)]
        if end_date:
            end_ts = pd.Timestamp(end_date)
            df = df[df.index <= end_ts]
        if len(df) < params['maxvol_lookback'] + 10:
            return code, None

        C = df['close'].values.astype(float)
        H = df['high'].values.astype(float)
        L = df['low'].values.astype(float)
        O = df['open'].values.astype(float)
        V = df['volume'].values.astype(float)
        dates = df.index

        signals = _compute_all_bar_signals(C, H, L, O, V, dates, params)

        # 流通市值过滤
        if _process_capital:
            shares = _process_capital.get(code)
            if shares and shares > 0:
                market_cap = C * shares * 10000 / 1e8  # 亿元
                signals['liutong_mask'] = market_cap >= 50.0
            else:
                signals['liutong_mask'] = np.zeros(len(C), dtype=bool)

        return code, signals
    except Exception as e:
        logger.debug("preload skip %s: %s", code, e)
        return code, None


# ============================================================================
# 大盘 MACD 计算
# ============================================================================

def compute_market_macd_for_trading_days(trading_days, tdxdir=TDX_DIR,
                                         market=TDX_MARKET):
    """预计算大盘在每个交易日的 MACD 多头状态"""
    from config import MARKET_INDEX_CODE, MARKET_MACD_FAST, MARKET_MACD_SLOW, MARKET_MACD_SIGNAL
    from mootdx.reader import Reader
    from MyTT import MACD

    try:
        reader = Reader.factory(market=market, tdxdir=tdxdir)
        df = reader.daily(symbol=MARKET_INDEX_CODE)
        if df is None or df.empty:
            return None
        df = apply_qfq(df, MARKET_INDEX_CODE)
        if df is None:
            return None

        C = df['close'].values.astype(float)
        _, _, macd_hist = MACD(C, MARKET_MACD_FAST, MARKET_MACD_SLOW,
                               MARKET_MACD_SIGNAL)

        # 构建日期 → bar index 映射
        date_to_idx = {}
        for i, d in enumerate(df.index):
            date_to_idx[d] = i

        # 对每个交易日获取 MACD 状态
        result = np.zeros(len(trading_days), dtype=bool)
        for ti, td in enumerate(trading_days):
            if td in date_to_idx:
                idx = date_to_idx[td]
                result[ti] = macd_hist[idx] > 0
        return result
    except Exception:
        return None


# ============================================================================
# 公共入口: scan_all
# ============================================================================

def scan_all(stock_type="main", tdxdir=None, market=None, max_workers=None):
    """全市场选股扫描

    Returns:
        list[dict]: 选中的股票列表，按 shrink_score 升序（缩量越明显排越前）
    """
    tdxdir = tdxdir or TDX_DIR
    market = market or TDX_MARKET
    max_workers = max_workers or SCAN_MAX_WORKERS

    codes = _get_all_codes(tdxdir)
    print(f"  全市场股票数量: {len(codes)}")

    params = {
        'j_threshold': ZSTOCK_KDJ_J_THRESHOLD,
        'j_q_threshold': ZSTOCK_KDJ_J_Q_THRESHOLD,
        'ema_period': ZSTOCK_ZX_EMA_PERIOD,
        'ma_periods': ZSTOCK_ZX_MA_PERIODS,
        'weekly_periods': ZSTOCK_WEEKLY_MA_PERIODS,
        'maxvol_lookback': ZSTOCK_MAXVOL_LOOKBACK,
    }

    results = []
    t0 = time.time()

    if max_workers == 1:
        _init_process(tdxdir, market)
        for code in codes:
            _, r = _scan_one(code, params, tdxdir, market)
            if r is not None:
                results.append(r)
    else:
        with ProcessPoolExecutor(
                max_workers=max_workers,
                initializer=_init_process,
                initargs=(tdxdir, market)) as executor:
            futures = {executor.submit(_scan_one, code, params): code
                       for code in codes}
            for fut in futures:
                try:
                    code, r = fut.result()
                    if r is not None:
                        results.append(r)
                except Exception:
                    pass

    elapsed = time.time() - t0
    results.sort(key=lambda x: x.get('shrink_score', 1.0))

    # 打印结果
    print(f"\n{'=' * 65}")
    print(f"  ZStock B1 选股完成  耗时 {elapsed:.1f}s  命中 {len(results)} 只")
    print(f"{'=' * 65}")
    if results:
        header = f"{'代码':>8} {'收盘':>8} {'缩量评分':>8} {'J值':>8} {'KDJ':>4} {'ZX':>4} {'周线':>4} {'量非阴':>4}"
        print(header)
        print("-" * len(header))
        for r in results:
            print(f"{r['code']:>8} {r['close']:>8.2f} {r['shrink_score']:>8.3f} "
                  f"{r['J']:>8.1f} "
                  f"{'✓' if r['kdj_j_low'] else '✗':>4} "
                  f"{'✓' if r['zx_ok'] else '✗':>4} "
                  f"{'✓' if r['weekly_bull'] else '✗':>4} "
                  f"{'✓' if r['maxvol_ok'] else '✗':>4}")
    print()
    return results


# ============================================================================
# 公共入口: preload_all_signals
# ============================================================================

def preload_all_signals(start, end, stock_type="main", max_workers=None,
                        tdxdir=None, market=None):
    """预加载全市场每 bar 信号数据

    Returns:
        (all_signals, trading_days, market_macd_bullish)
        - all_signals: dict[str, dict]  股票代码 → 信号字典
        - trading_days: pd.DatetimeIndex  交易日历
        - market_macd_bullish: np.ndarray[bool] | None
    """
    tdxdir = tdxdir or TDX_DIR
    market = market or TDX_MARKET
    max_workers = max_workers or SCAN_MAX_WORKERS

    codes = _get_all_codes(tdxdir)
    print(f"  全市场股票数量: {len(codes)}")

    params = {
        'j_threshold': ZSTOCK_KDJ_J_THRESHOLD,
        'j_q_threshold': ZSTOCK_KDJ_J_Q_THRESHOLD,
        'ema_period': ZSTOCK_ZX_EMA_PERIOD,
        'ma_periods': ZSTOCK_ZX_MA_PERIODS,
        'weekly_periods': ZSTOCK_WEEKLY_MA_PERIODS,
        'maxvol_lookback': ZSTOCK_MAXVOL_LOOKBACK,
    }

    all_signals = {}
    t0 = time.time()

    if max_workers == 1:
        _init_process(tdxdir, market)
        for code in codes:
            _, sig = _scan_one_all_bars(code, params, start, end)
            if sig is not None:
                all_signals[code] = sig
    else:
        with ProcessPoolExecutor(
                max_workers=max_workers,
                initializer=_init_process,
                initargs=(tdxdir, market)) as executor:
            futures = {
                executor.submit(_scan_one_all_bars, code, params, start, end): code
                for code in codes
            }
            for fut in futures:
                try:
                    code, sig = fut.result()
                    if sig is not None:
                        all_signals[code] = sig
                except Exception:
                    pass

    elapsed = time.time() - t0
    print(f"  信号预加载完成: {len(all_signals)} 只, 耗时 {elapsed:.1f}s")

    if not all_signals:
        return {}, pd.DatetimeIndex([]), None

    # 构建统一交易日历
    all_dates = set()
    for sig in all_signals.values():
        all_dates.update(sig['dates'])
    trading_days = pd.DatetimeIndex(sorted(all_dates))
    # 按 start/end 截断
    if start:
        trading_days = trading_days[trading_days >= pd.Timestamp(start)]
    if end:
        trading_days = trading_days[trading_days <= pd.Timestamp(end)]

    # 大盘 MACD
    market_macd_bullish = compute_market_macd_for_trading_days(
        trading_days, tdxdir, market)

    return all_signals, trading_days, market_macd_bullish
