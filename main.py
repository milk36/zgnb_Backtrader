"""回测系统 CLI 入口

Usage:
    python main.py                                          # 默认：600036，KDJ策略
    python main.py --strategy huangbai                      # 黄白线B1策略
    python main.py --symbol 000001 600036                   # 多只股票
    python main.py --start 2022-01-01 --end 2024-01-01
    python main.py --no-plot                                # 不绘图
"""

import argparse

from config import (
    INITIAL_CASH,
    COMMISSION,
    DEFAULT_STOCKS,
    DEFAULT_START_DATE,
    DEFAULT_END_DATE,
    STOCK_TYPE,
    PLOT_ENABLED,
)
from src.data.tdx_feed import TdxDataFeed
from src.engine.backtester import Backtester
from src.scanner import scan_all
from src.strategies.kdj_cross_strategy import KDJCrossStrategy
from src.strategies.huangbai_b1_strategy import HuangBaiB1Strategy

STRATEGIES = {
    "kdj": KDJCrossStrategy,
    "huangbai": HuangBaiB1Strategy,
}


def parse_args():
    parser = argparse.ArgumentParser(description="Backtrader 回测运行器")
    parser.add_argument("--symbol", nargs="+", default=DEFAULT_STOCKS, help="股票代码列表")
    parser.add_argument("--start", default=DEFAULT_START_DATE, help="起始日期")
    parser.add_argument("--end", default=DEFAULT_END_DATE, help="结束日期")
    parser.add_argument("--cash", type=float, default=INITIAL_CASH, help="初始资金")
    parser.add_argument("--strategy", choices=STRATEGIES.keys(), default="kdj", help="策略选择")
    parser.add_argument("--stock-type", choices=["main", "tech"], default=STOCK_TYPE, help="板块类型")
    parser.add_argument("--scan", action="store_true", help="全市场选股扫描模式")
    parser.add_argument("--no-plot", action="store_true", help="禁用绘图")
    return parser.parse_args()


def main():
    args = parse_args()

    # 全市场扫描模式
    if args.scan:
        scan_all(stock_type=args.stock_type)
        return

    # 单股/多股回测模式

    # 数据层
    feed_provider = TdxDataFeed()

    # 引擎
    backtester = Backtester(cash=args.cash, commission=COMMISSION)

    # 加载数据
    for symbol in args.symbol:
        feed = feed_provider.get_feed(symbol, start=args.start, end=args.end)
        backtester.add_feed(feed, name=symbol)

    # 策略
    strategy_cls = STRATEGIES[args.strategy]
    if strategy_cls == HuangBaiB1Strategy:
        backtester.add_strategy(strategy_cls, stock_type=args.stock_type)
    else:
        backtester.add_strategy(strategy_cls)

    # 运行
    report = backtester.run()
    Backtester.print_report(report)

    # 绘图
    if PLOT_ENABLED and not args.no_plot:
        backtester.plot()


if __name__ == "__main__":
    main()
