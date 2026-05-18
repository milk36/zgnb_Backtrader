"""B2 倍量柱策略

核心逻辑：
1. 大盘MACD多头时才买入（空头只卖不买）
2. 周线多头空间过滤
3. 前一天有B1信号 + 当天有倍量柱信号（B2核心入场条件）
4. vol_expand_ok 五重过滤链（缩量拉升排除/连续涨停缩量排除/放量下跌排除/S1大风车排除）
5. 组合级模拟：100万/10只/每只10万
6. 按缩量排序+流动市值排序，取前1支

倍量柱定义（通达信公式）：
  AVG40 := MA(VOL, 40);
  PLRY := VOL > 1.8 * REF(VOL, 1) AND C > O AND VOL > AVG40;
  PLRY_FIRST := PLRY AND NOT(REF(PLRY, 1));

架构：包装 V4 的 _compute_all_bar_signals()，在其返回字典上叠加倍量柱逻辑，
复用 PortfolioSimulator 的标准六级退出。
"""

import time
import warnings

import numpy as np
import pandas as pd
from MyTT import MA, REF

from config import (
    TDX_DIR, TDX_MARKET, SCAN_MAX_WORKERS,
    HUANGBAI_M1, HUANGBAI_M2, HUANGBAI_M3, HUANGBAI_M4,
    HUANGBAI_N, HUANGBAI_M, HUANGBAI_N1, HUANGBAI_N2,
    B2_VOL_RATIO, B2_VOL_AVG_PERIOD, B2_MIN_MARKET_CAP,
    MARKET_INDEX_CODE,
)
from src.strategies.huangbai_b1_v4_strategy import (
    _compute_all_bar_signals as _v4_compute_all_bar_signals,
    _compute_signals as _v4_compute_signals,
    _get_all_codes,
    load_market_index,
    compute_market_macd,
    compute_market_macd_for_trading_days,
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
#  倍量柱信号计算                                                      #
# ================================================================== #


def _compute_beiliangzhu(V, C, O):
    """倍量柱检测 (PLRY_FIRST)

    条件：
    1. VOL > ratio * REF(VOL, 1)  (当天量是前一天量的ratio倍以上)
    2. C > O                      (阳线)
    3. VOL > MA(VOL, avg_period)  (超过均量线)
    4. 前一天不是倍量柱             (首次出现)
    """
    ref_v = REF(V, 1)
    avg_vol = MA(V, B2_VOL_AVG_PERIOD)
    plry = (V > B2_VOL_RATIO * ref_v) & (C > O) & (V > avg_vol)
    plry_prev = np.roll(plry, 1)
    plry_prev[0] = False
    return plry & ~plry_prev


# ================================================================== #
#  信号计算                                                            #
# ================================================================== #


def _compute_all_bar_signals(C, H, L, O, V, dates, params, capital_shares=None):
    """B2: 前日B1 + 当日倍量柱

    包装V4的_compute_all_bar_signals，修改：
    1. b1 → 前一天b1 & 当天倍量柱 (B2入场条件)
    2. dongneng_recent → 全True (不做动能过滤)
    """
    signals = _v4_compute_all_bar_signals(C, H, L, O, V, dates, params, capital_shares)
    if signals is None:
        return None

    # 保留原始B1供参考
    b1_original = signals["b1"].copy()

    # 前一天B1
    prev_b1 = np.roll(b1_original, 1)
    prev_b1[0] = False

    # 当天倍量柱
    beiliangzhu = _compute_beiliangzhu(V, C, O)

    # B2入场条件：前一天B1 & 当天倍量柱
    signals["b1"] = prev_b1 & beiliangzhu

    # 不做动能过滤
    signals["dongneng_recent"] = np.ones(len(C), dtype=bool)

    # 保存辅助字段
    signals["b1_original"] = b1_original
    signals["beiliangzhu"] = beiliangzhu

    return signals


def _compute_signals(C, H, L, O, V, dates, params):
    """计算最新 bar 的 B2 信号结果（用于实时扫描）"""
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
        "vol_expand": bool(all_bars["vol_expand_ok"][i]),
        "close": float(C[i]),
        "J": float(all_bars.get("J", [0])[i]) if "J" in all_bars else 0.0,
        "RSI": float(all_bars.get("RSI", [0])[i]) if "RSI" in all_bars else 0.0,
        "shrink_score": float(all_bars["shrink_score"][i]),
        "beiliangzhu": bool(all_bars["beiliangzhu"][i]),
        "b1_yesterday": bool(all_bars["b1_original"][i - 1]),
    }


# ================================================================== #
#  全市场选股扫描                                                      #
# ================================================================== #


def _scan_one(code, params, skip_weekly, market_macd_ok=True):
    """扫描单只股票（B2: 前日B1 + 当日倍量柱）"""
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
        vol_expand_ok = sig.get("vol_expand", True)
        if sig["b1"] and weekly_ok and vol_expand_ok and market_macd_ok:
            sig["code"] = code
            return code, sig, False
        return code, None, False
    except Exception as e:
        return code, {"error": str(e)}, True


def scan_all(stock_type="main", skip_weekly=False,
             tdxdir=TDX_DIR, market=TDX_MARKET, max_workers=SCAN_MAX_WORKERS,
             skip_on_bear=False):
    """B2全市场扫描：大盘MACD过滤 + 前日B1 + 当日倍量柱"""
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
        "min_market_cap": B2_MIN_MARKET_CAP,
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
                blz = "Y" if sig.get("beiliangzhu") else "N"
                b1y = "Y" if sig.get("b1_yesterday") else "N"
                print(f"  {code}  "
                      f"C={sig['close']:.2f}  缩量={sig['shrink_score']:.3f}  "
                      f"倍量柱={blz}  前日B1={b1y}")
            if done % 500 == 0:
                print(f"  ... 已扫描 {done}/{total} ({done/total*100:.0f}%)  "
                      f"命中 {len(results)}  耗时 {time.time()-t0:.1f}s")

    elapsed = time.time() - t0
    results.sort(key=lambda x: x["shrink_score"])

    print(f"\n{'=' * 55}")
    print(f"  B2扫描完成: {total} 只  命中 {len(results)} 只  "
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
                  f"缩量={r['shrink_score']:.3f}{tag}")

    return results, market_macd_ok


# ================================================================== #
#  组合级模拟：全量每bar信号预加载                                       #
# ================================================================== #


def _scan_one_all_bars(code, params):
    """加载单只股票数据并计算全量每bar信号（B2版本）"""
    assert _process_reader is not None, "_process_reader 未初始化，请在子进程中调用"
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
    """B2预加载：大盘MACD过滤 + 前日B1 + 当日倍量柱

    Returns:
        (all_signals, trading_days, market_macd_bullish)
    """
    from concurrent.futures import ProcessPoolExecutor, as_completed

    codes = _get_all_codes(tdxdir)
    total = len(codes)
    print(f"预加载 {total} 只A股信号(B2倍量柱)... (workers={max_workers or 'auto'})")

    capital_data = _load_capital_data(tdxdir)
    if capital_data:
        print(f"  已加载 {len(capital_data)} 只股票流通股本数据 (>{B2_MIN_MARKET_CAP}亿)")
    else:
        print("  警告: 无法加载流通股本数据，跳过流通市值过滤")

    params = {
        "m1": HUANGBAI_M1, "m2": HUANGBAI_M2, "m3": HUANGBAI_M3, "m4": HUANGBAI_M4,
        "n": HUANGBAI_N, "m": HUANGBAI_M, "n1": HUANGBAI_N1, "n2": HUANGBAI_N2,
        "wma30": 30, "wma60": 60, "wma120": 120, "wma240": 240,
        "stock_type": stock_type,
        "min_market_cap": B2_MIN_MARKET_CAP,
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
