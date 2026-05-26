"""为 thinking/完美B1.md 中的11个历史案例生成K线图表

使用与组合回测相同的图表样式（白线/黄线/B1标记/买卖标记），
为每个案例生成图表到 b1_charts/ 目录。

用法:
    python gen_b1_charts.py
"""

import os
import sys

import numpy as np
import pandas as pd

# 预加载
from config import TDX_DIR, TDX_MARKET
from mootdx.reader import Reader
from src.data.adjustment import apply_qfq, preload_disk_cache
from src.charting.kline_chart import _plot_single_stock, PADDING

preload_disk_cache()
reader = Reader.factory(market=TDX_MARKET, tdxdir=TDX_DIR)

# 完美B1信号计算
from config import (
    HUANGBAI_M1, HUANGBAI_M2, HUANGBAI_M3, HUANGBAI_M4,
    HUANGBAI_N, HUANGBAI_M, HUANGBAI_N1, HUANGBAI_N2,
)
from src.strategies.perfect_b1_strategy import (
    _compute_all_bar_signals, PATTERN_NAMES,
)

PARAMS = {
    "m1": HUANGBAI_M1, "m2": HUANGBAI_M2, "m3": HUANGBAI_M3, "m4": HUANGBAI_M4,
    "n": HUANGBAI_N, "m": HUANGBAI_M, "n1": HUANGBAI_N1, "n2": HUANGBAI_N2,
    "wma30": 30, "wma60": 60, "wma120": 120, "wma240": 240,
    "stock_type": "main",
    "min_market_cap": 0,
    "_capital_data": {},
}

# 11个历史案例: (code, B1日期, 模式名称, 展示窗口额外天数)
CASES = [
    ("688799", "2025-05-12", "典型单波", 80),
    ("600366", "2025-08-06", "多波N型", 100),
    ("688321", "2025-06-20", "典型单波", 80),
    ("600601", "2025-06-24", "典型单波", 80),
    ("600601", "2025-07-23", "跌破反转", 100),
    ("300689", "2025-07-18", "白线不死叉", 60),
    ("002074", "2025-08-04", "白线不死叉", 80),
    ("605378", "2025-07-31", "跌破反转", 80),
    ("600184", "2025-07-11", "多波N型", 120),
    ("301076", "2025-08-01", "跌破反转", 80),
    ("002940", "2025-07-11", "大牛市", 60),
    ("300377", "2026-01-08", "短期B1", 40),
]


def load_signals(code):
    """加载单只股票日线数据并计算完美B1信号"""
    df = reader.daily(symbol=code)
    if df is None or len(df) < 300:
        return None, None
    df = df.sort_index()
    df = apply_qfq(df, code)
    signals = _compute_all_bar_signals(
        df["close"].values.astype(float),
        df["high"].values.astype(float),
        df["low"].values.astype(float),
        df["open"].values.astype(float),
        df["volume"].values.astype(float),
        df.index, PARAMS)
    if signals is None:
        return None, None
    # 补充 avg_amount_20（generate_charts 不需要但 _plot_single_stock 可能引用）
    amount = df["amount"].values.astype(float)
    signals["avg_amount_20"] = pd.Series(amount).rolling(20, min_periods=1).mean().values
    return df, signals


def build_trade_list(code, b1_date_str, buy_price, pattern_type, extra_days):
    """构造模拟 trade_list 条目

    通过设置较大的 sell_date 间距让 _find_range 展示完整的建仓-回调-拉升过程。
    """
    b1_ts = pd.Timestamp(b1_date_str)
    # sell_date 设为 B1 之后 extra_days 天，让图表展示后续走势
    return [{
        "code": code,
        "buy_date": b1_ts,
        "sell_date": b1_ts + pd.Timedelta(days=extra_days),
        "buy_price": buy_price,
        "sell_price": buy_price,
        "size": 100,
        "pnl_pct": 0,
        "pnl_amount": 0,
        "reason": "B1案例",
        "stop_loss": None,
        "white_at_buy": 0,
        "yellow_at_buy": 0,
        "pattern_type": pattern_type,
    }]


def main():
    output_dir = "b1_charts"
    os.makedirs(output_dir, exist_ok=True)

    # 清空旧图表
    for f in os.listdir(output_dir):
        if f.lower().endswith(".png"):
            os.remove(os.path.join(output_dir, f))

    # 缓存已加载的股票数据（方正科技有两个案例）
    cache = {}

    generated = 0
    for code, b1_date_str, mode_name, extra_days in CASES:
        print(f"  处理 {code} B1={b1_date_str} 模式={mode_name} ...")

        if code not in cache:
            df, sig = load_signals(code)
            if sig is None:
                print(f"    跳过: 无法加载 {code}")
                continue
            cache[code] = (df, sig)
        else:
            df, sig = cache[code]

        dates = sig["dates"]
        b1_ts = pd.Timestamp(b1_date_str)

        # 找到 B1 日在数组中的位置
        mask = dates == b1_ts
        if not mask.any():
            # 尝试找最近的交易日
            future = dates[dates >= b1_ts]
            if len(future) == 0:
                print(f"    跳过: {code} 无 {b1_date_str} 之后的数据")
                continue
            b1_ts = future[0]
            mask = dates == b1_ts

        idx = np.where(mask)[0][0]
        buy_price = float(sig["close"][idx])
        pattern_type = int(sig.get("pattern_type", np.zeros(len(dates)))[idx])

        # 使用文档中人工分析的模式分类作为标题
        _reverse = {"典型单波": 1, "白线不死叉": 2, "多波N型": 3,
                    "跌破反转": 4, "大牛市": 5, "短期B1": 0}
        pattern_type = _reverse.get(mode_name, 0)

        # 构造 trade_list
        trades = build_trade_list(code, b1_date_str, buy_price, pattern_type, extra_days)

        # 调用图表生成
        try:
            _plot_single_stock(code, sig, trades, output_dir, sub_chart="volume")
            pt_label = PATTERN_NAMES.get(pattern_type, "")
            print(f"    完成: {code} 模式={pt_label} 买入价={buy_price:.2f}")
            generated += 1
        except Exception as e:
            print(f"    失败: {e}")

    print(f"\n  图表生成完成: {generated}/{len(CASES)}  目录: {output_dir}/")


if __name__ == "__main__":
    main()
