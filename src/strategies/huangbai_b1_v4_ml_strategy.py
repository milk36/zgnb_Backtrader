"""黄白线B1策略 V4 ML增强版

基于 V4 B1 信号 + LightGBM 三分类评分（大盈利/小幅波动/大亏损）。
支持两种模式:
  - soft（默认）: 保留所有 V4 B1 信号，用 ML 分数排序优先买入高分候选
  - hard: ML 预测大盈利概率低于阈值时拒绝信号

架构: 纯代理包装 V4 的 _compute_all_bar_signals()，添加 ml_score 字段。
"""

import os
import time
import warnings

import numpy as np
import pandas as pd

from config import (
    TDX_DIR, TDX_MARKET, SCAN_MAX_WORKERS, STOCK_TYPE,
    HUANGBAI_M1, HUANGBAI_M2, HUANGBAI_M3, HUANGBAI_M4,
    HUANGBAI_N, HUANGBAI_M, HUANGBAI_N1, HUANGBAI_N2,
    DNZH_MIN_MARKET_CAP,
    ML_MODEL_DIR, ML_FILTER_MODE, ML_SCORE_THRESHOLD,
)
from src.strategies.huangbai_b1_v4_strategy import (
    _compute_all_bar_signals as _v4_compute_all_bar_signals,
    _compute_signals as _v4_compute_signals,
    _get_all_codes,
    _init_process,
    load_market_index,
    compute_market_macd,
    compute_market_macd_for_trading_days,
)
from src.strategies.dongneng_zhuan_strategy import _load_capital_data
from src.ml.feature_extractor import compute_feature_arrays
from src.ml.predictor import load_or_default_predictor

warnings.filterwarnings("ignore")


# ================================================================== #
#  进程级变量                                                          #
# ================================================================== #

_process_reader = None


def _init_ml_process(tdxdir, market):
    """子进程初始化：同时设置 V4 和本模块的 _process_reader"""
    global _process_reader
    from mootdx.reader import Reader
    _process_reader = Reader.factory(market=market, tdxdir=tdxdir)
    from src.data.adjustment import preload_disk_cache
    preload_disk_cache()


# ================================================================== #
#  信号计算                                                            #
# ================================================================== #

def _compute_all_bar_signals(C, H, L, O, V, dates, params, capital_shares=None):
    """ML增强版 V4 B1 信号计算

    步骤:
      1. 调用 V4 的 _v4_compute_all_bar_signals() 获取基础信号
      2. 加载 ML 模型（如果没有模型就返回 V4 原始信号）
      3. 用 compute_feature_arrays() 提取特征
      4. 用 predictor.predict_batch() 预测 ml_score
      5. 根据 ml_filter_mode 决定是否修改 b1 信号
      6. 设置 b2_sort_primary 排序键（PortfolioSimulator 已支持）
    """
    # 步骤1: 获取基础V4信号
    signals = _v4_compute_all_bar_signals(C, H, L, O, V, dates, params,
                                          capital_shares)
    if signals is None:
        return None

    # 步骤2: 加载ML模型
    model_path = params.get("ml_model_path")
    ml_filter_mode = params.get("ml_filter_mode", ML_FILTER_MODE)
    ml_threshold = params.get("ml_threshold", ML_SCORE_THRESHOLD)

    predictor = load_or_default_predictor(model_path)
    if predictor is None:
        # 无模型时退化为V4
        n = len(C)
        signals["ml_score"] = np.ones(n, dtype=float) * 0.5
        return signals

    # 步骤3: 提取特征
    feature_arrays = compute_feature_arrays(signals, C, H, L, O, V, dates,
                                            params)

    # 步骤4: 在所有B1 bar上预测
    n = len(C)
    b1_mask = signals["b1"]
    if np.any(b1_mask):
        ml_score = predictor.predict_batch(feature_arrays, b1_mask)
    else:
        ml_score = np.zeros(n, dtype=float)

    signals["ml_score"] = ml_score

    # 步骤5: 应用过滤模式
    if ml_filter_mode == "hard":
        signals["b1"] = signals["b1"] & (ml_score >= ml_threshold)

    # 步骤6: 排序键（ml_score降序，利用PortfolioSimulator的b2_sort_primary机制）
    signals["b2_sort_primary"] = np.where(
        signals["b1"], -ml_score, float('inf')
    )

    return signals


def _compute_signals(C, H, L, O, V, dates, params):
    """计算最新 bar 的 ML 增强 V4 信号"""
    all_bars = _compute_all_bar_signals(C, H, L, O, V, dates, params)
    if all_bars is None:
        return None

    i = len(C) - 1
    if i < 1:
        return None

    ml_filter_mode = params.get("ml_filter_mode", ML_FILTER_MODE)
    ml_threshold = params.get("ml_threshold", ML_SCORE_THRESHOLD)
    ml_score = float(all_bars["ml_score"][i])
    b1_raw = bool(all_bars["b1"][i])

    # hard 模式下，低于阈值的信号已在 _compute_all_bar_signals 中过滤
    # 这里只需报告结果
    if ml_filter_mode == "hard" and ml_score < ml_threshold:
        b1_final = False
    else:
        b1_final = b1_raw

    return {
        "weekly": bool(all_bars["weekly_bull"][i] and all_bars["above_ma30w"][i]),
        "gc": True,
        "market_macd": True,
        "b1": b1_final,
        "dongneng_recent": bool(all_bars["dongneng_recent"][i]),
        "vol_expand": bool(all_bars["vol_expand_ok"][i]),
        "no_huge_vol_bearish": bool(all_bars["no_huge_vol_bearish"][i]),
        "close": float(C[i]),
        "shrink_score": float(all_bars["shrink_score"][i]),
        "ml_score": ml_score,
        "ml_filter_mode": ml_filter_mode,
        "ml_threshold": ml_threshold,
    }


# ================================================================== #
#  全市场选股扫描                                                      #
# ================================================================== #

def _scan_one(code, params, skip_weekly, market_macd_ok=True):
    """扫描单只股票（ML增强 V4）"""
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
             skip_on_bear=False,
             ml_model_path=None, ml_filter_mode=None, ml_threshold=None):
    """ML增强 V4 全市场扫描

    Args:
        stock_type: 股票类型 ("main" / "tech")
        skip_weekly: 跳过周线过滤
        tdxdir: 通达信目录
        market: 市场类型
        max_workers: 最大进程数
        skip_on_bear: 大盘空头时跳过
        ml_model_path: ML模型路径（None则自动搜索最新）
        ml_filter_mode: "soft" 或 "hard"
        ml_threshold: hard模式阈值
    """
    from concurrent.futures import ProcessPoolExecutor, as_completed

    _ml_mode = ml_filter_mode or ML_FILTER_MODE
    _ml_thresh = ml_threshold or ML_SCORE_THRESHOLD

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

    # 检查ML模型可用性
    predictor = load_or_default_predictor(ml_model_path)
    model_status = "已加载" if predictor else "无模型(退化为V4)"
    print(f"  ML模型: {model_status}  模式={_ml_mode}  阈值={_ml_thresh}")

    codes = _get_all_codes(tdxdir)
    total = len(codes)
    print(f"扫描 {total} 只A股 [B1V4-ML]... (workers={max_workers or 'auto'})")

    params = {
        "m1": HUANGBAI_M1, "m2": HUANGBAI_M2, "m3": HUANGBAI_M3, "m4": HUANGBAI_M4,
        "n": HUANGBAI_N, "m": HUANGBAI_M, "n1": HUANGBAI_N1, "n2": HUANGBAI_N2,
        "wma30": 30, "wma60": 60, "wma120": 120, "wma240": 240,
        "stock_type": stock_type,
        "ml_model_path": ml_model_path,
        "ml_filter_mode": _ml_mode,
        "ml_threshold": _ml_thresh,
    }

    results = []
    errors = 0
    done = 0
    t0 = time.time()

    with ProcessPoolExecutor(
        max_workers=max_workers,
        initializer=_init_ml_process,
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
                      f"C={sig['close']:.2f}  ML={sig.get('ml_score', 0):.3f}  "
                      f"缩量={sig['shrink_score']:.3f}")
            if done % 500 == 0:
                print(f"  ... 已扫描 {done}/{total} ({done/total*100:.0f}%)  "
                      f"命中 {len(results)}  耗时 {time.time()-t0:.1f}s")

    elapsed = time.time() - t0
    results.sort(key=lambda x: x.get("ml_score", 0), reverse=True)

    print(f"\n{'=' * 60}")
    print(f"  [B1V4-ML] 扫描完成: {total} 只  命中 {len(results)} 只  "
          f"错误 {errors}  耗时 {elapsed:.1f}s")
    print(f"  模式={_ml_mode}  阈值={_ml_thresh}  模型={model_status}")
    if not market_macd_ok:
        print(f"  [注意] 大盘MACD空头，建议不执行买入")
    print(f"{'=' * 60}")

    if results:
        print(f"\n  选股结果（按ML评分降序）")
        print(f"{'=' * 60}")
        for r in results:
            tag = " <<< TOP" if r == results[0] else ""
            print(f"  {r['code']}  C={r['close']:.2f}  "
                  f"ML={r.get('ml_score', 0):.3f}  "
                  f"缩量={r['shrink_score']:.3f}{tag}")

    return results, market_macd_ok


# ================================================================== #
#  组合级模拟预加载                                                     #
# ================================================================== #

def _scan_one_all_bars(code, params):
    """加载单只股票数据并计算全量每bar ML增强信号"""
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
                        tdxdir=TDX_DIR, market=TDX_MARKET,
                        ml_model_path=None, ml_filter_mode=None,
                        ml_threshold=None):
    """ML增强 V4 预加载

    Returns:
        (all_signals, trading_days, market_macd_bullish)
    """
    from concurrent.futures import ProcessPoolExecutor, as_completed

    _ml_mode = ml_filter_mode or ML_FILTER_MODE
    _ml_thresh = ml_threshold or ML_SCORE_THRESHOLD

    codes = _get_all_codes(tdxdir)
    total = len(codes)
    print(f"预加载 {total} 只A股信号 [B1V4-ML]... (workers={max_workers or 'auto'})")
    print(f"  ML模式={_ml_mode}  阈值={_ml_thresh}")

    # 加载流通股本数据
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
        "ml_model_path": ml_model_path,
        "ml_filter_mode": _ml_mode,
        "ml_threshold": _ml_thresh,
    }

    all_signals = {}
    errors = 0
    error_details = []
    done = 0
    t0 = time.time()
    all_dates_index = pd.DatetimeIndex([])

    with ProcessPoolExecutor(
        max_workers=max_workers,
        initializer=_init_ml_process,
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

    print(f"\n  [B1V4-ML] 预加载完成: {len(all_signals)} 只  错误 {errors}  "
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
