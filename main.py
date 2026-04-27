"""回测系统 CLI 入口

Usage:
    python main.py                                  # 默认：600036，2020-2024
    python main.py --symbol 000001 600036           # 多只股票
    python main.py --start 2022-01-01 --end 2024-01-01
    python main.py --no-plot                        # 不绘图
"""

import argparse

from config import (
    INITIAL_CASH,
    COMMISSION,
    DEFAULT_STOCKS,
    DEFAULT_START_DATE,
    DEFAULT_END_DATE,
    PLOT_ENABLED,
)
from src.data.tdx_feed import TdxDataFeed
from src.engine.backtester import Backtester
from src.strategies.kdj_cross_strategy import KDJCrossStrategy


def parse_args():
    parser = argparse.ArgumentParser(description="Backtrader 回测运行器")
    parser.add_argument("--symbol", nargs="+", default=DEFAULT_STOCKS, help="股票代码列表")
    parser.add_argument("--start", default=DEFAULT_START_DATE, help="起始日期")
    parser.add_argument("--end", default=DEFAULT_END_DATE, help="结束日期")
    parser.add_argument("--cash", type=float, default=INITIAL_CASH, help="初始资金")
    parser.add_argument("--no-plot", action="store_true", help="禁用绘图")
    return parser.parse_args()


def main():
    args = parse_args()

    # 数据层
    feed_provider = TdxDataFeed()

    # 引擎
    backtester = Backtester(cash=args.cash, commission=COMMISSION)

    # 加载数据
    for symbol in args.symbol:
        feed = feed_provider.get_feed(symbol, start=args.start, end=args.end)
        backtester.add_feed(feed, name=symbol)

    # 策略
    backtester.add_strategy(KDJCrossStrategy)

    # 运行
    report = backtester.run()
    Backtester.print_report(report)

    # 绘图
    if PLOT_ENABLED and not args.no_plot:
        backtester.plot()


if __name__ == "__main__":
    main()
