"""回测系统 CLI 入口

Usage:
    python main.py --strategy huangbai --scan          # 全市场扫描 + 自动回测
    python main.py --strategy huangbai --symbol 002475 # 指定股票回测
    python main.py --strategy kdj --symbol 600036      # KDJ策略
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
    PORTFOLIO_INITIAL_CASH,
    PORTFOLIO_MAX_POSITIONS,
    PORTFOLIO_PER_POSITION,
    DNZH_INITIAL_CASH,
    DNZH_MAX_POSITIONS,
    DNZH_PER_POSITION,
    DNZH_T_PLUS_N,
    DNZH_MAX_HOLD_DAYS,
    DNZH_PROFIT_PCT,
    DNZH_MINUTE_CONFIRM_BARS,
    DNZH_MINUTE_ENTRY_ENABLED,
    DNZH_MINUTE_EXIT_ENABLED,
    LOG_DIR,
    MARKET_INDEX_CODE,
)
from src.data.tdx_feed import TdxDataFeed
from src.engine.backtester import Backtester
from src.strategies.kdj_cross_strategy import KDJCrossStrategy
from src.strategies.huangbai_b1_strategy import HuangBaiB1Strategy, scan_all
from src.strategies.huangbai_b1_v2_strategy import HuangBaiB1V2Strategy, scan_all as scan_all_v2
from src.strategies.huangbai_b1_v3_strategy import HuangBaiB1V3Strategy, scan_all as scan_all_v3
from src.strategies.dongneng_zhuan_strategy import scan_all as scan_all_dnzh

STRATEGIES = {
    "kdj": KDJCrossStrategy,
    "huangbai": HuangBaiB1Strategy,
    "huangbai_v2": HuangBaiB1V2Strategy,
    "huangbai_v3": HuangBaiB1V3Strategy,
    "dongneng_zhuan": None,  # 仅支持组合级模拟，不支持单股回测
}


def parse_args():
    parser = argparse.ArgumentParser(description="Backtrader 回测运行器")
    parser.add_argument("--symbol", nargs="+", default=None, help="股票代码（不指定则全市场扫描）")
    parser.add_argument("--start", default=DEFAULT_START_DATE, help="起始日期")
    parser.add_argument("--end", default=DEFAULT_END_DATE, help="结束日期")
    parser.add_argument("--cash", type=float, default=INITIAL_CASH, help="初始资金")
    parser.add_argument("--strategy", choices=STRATEGIES.keys(), default="huangbai", help="策略选择")
    parser.add_argument("--stock-type", choices=["main", "tech"], default=STOCK_TYPE, help="板块类型")
    parser.add_argument("--scan", action="store_true", help="强制全市场扫描（忽略 --symbol）")
    parser.add_argument("--scan-only", action="store_true", help="仅扫描选股，不回测")
    parser.add_argument("--no-plot", action="store_true", help="禁用绘图")
    parser.add_argument("--portfolio", action="store_true", help="组合级模拟（正确的时间序列模拟）")
    parser.add_argument("--chart", action="store_true", help="生成交易K线图（保存到charts/目录）")
    return parser.parse_args()


def _run_backtest(symbols, args, strategy_cls=None):
    """对指定股票列表运行回测，返回汇总报告"""
    if strategy_cls is None:
        strategy_cls = STRATEGIES[args.strategy]
    feed_provider = TdxDataFeed()
    total_return = 0
    total_trades = 0
    total_won = 0
    total_lost = 0
    n = len(symbols)

    for i, symbol in enumerate(symbols):
        print(f"\n--- [{i + 1}/{n}] {symbol} ---")
        try:
            backtester = Backtester(cash=args.cash, commission=COMMISSION)
            feed = feed_provider.get_feed(symbol, start=args.start, end=args.end)
            backtester.add_feed(feed, name=symbol)
            backtester.add_strategy(strategy_cls, stock_type=args.stock_type)
            report = backtester.run()
            Backtester.print_report(report)
            total_return += report["total_return"]
            ta = report["trades"].get("total", {})
            w = ta.get("won", 0) if isinstance(ta, dict) else 0
            l = ta.get("lost", 0) if isinstance(ta, dict) else 0
            total_trades += w + l
            total_won += w
            total_lost += l
        except Exception as e:
            print(f"  回测失败: {e}")

    if n > 1:
        print(f"\n{'=' * 55}")
        print(f"  汇总: {n} 只股票  平均收益={total_return / n:.2f}%  "
              f"总交易={total_trades}  胜={total_won}  负={total_lost}")
        print(f"{'=' * 55}")


def main():
    args = parse_args()
    strategy_cls = STRATEGIES[args.strategy]

    # ---- 动能砖策略 ----
    if args.strategy == "dongneng_zhuan":
        if args.scan or args.scan_only:
            print("=" * 55)
            print("  动能+砖 全市场选股扫描")
            print("=" * 55)
            scan_all_dnzh()
            return

        # 默认进入组合级模拟
        from src.engine.dongneng_zhuan_simulator import DongnengZhuanSimulator
        from src.strategies.dongneng_zhuan_strategy import preload_all_signals as preload_dnzh

        print("=" * 55)
        print("  动能+砖 策略")
        print("  阶段1: 预加载全市场信号数据")
        print("=" * 55)
        all_signals, trading_days = preload_dnzh(
            start=args.start, end=args.end)

        if not all_signals or len(trading_days) == 0:
            print("\n无有效数据，模拟终止。")
            return

        print(f"\n{'=' * 55}")
        print(f"  阶段2: 组合级模拟 ({len(trading_days)} 个交易日)")
        print(f"  区间: {args.start} ~ {args.end}")
        print(f"  资金: {DNZH_INITIAL_CASH:,.0f}  "
              f"最多 {DNZH_MAX_POSITIONS} 只  "
              f"每只 {DNZH_PER_POSITION:,.0f}")
        print(f"{'=' * 55}")

        from src.data.minute_feed import MinuteFeed
        if DNZH_MINUTE_ENTRY_ENABLED or DNZH_MINUTE_EXIT_ENABLED:
            minute_feed = MinuteFeed()
        else:
            minute_feed = None

        sim = DongnengZhuanSimulator(
            all_signals=all_signals,
            trading_days=trading_days,
            initial_cash=DNZH_INITIAL_CASH,
            max_positions=DNZH_MAX_POSITIONS,
            per_position_cash=DNZH_PER_POSITION,
            commission=COMMISSION,
            t_plus_n=DNZH_T_PLUS_N,
            max_hold_days=DNZH_MAX_HOLD_DAYS,
            profit_pct=DNZH_PROFIT_PCT,
            log_dir=LOG_DIR,
            minute_feed=minute_feed,
            minute_confirm_bars=DNZH_MINUTE_CONFIRM_BARS,
            minute_entry_enabled=DNZH_MINUTE_ENTRY_ENABLED,
            minute_exit_enabled=DNZH_MINUTE_EXIT_ENABLED)
        sim.run()
        report = sim.report()
        DongnengZhuanSimulator.print_report(report, log_file=sim._log_file)

        if args.chart:
            from src.charting import generate_charts
            generate_charts(report["trade_list"], sim._all_signals, sub_chart="brick")
        return

    # ---- huangbai 策略：组合级模拟 ----
    if strategy_cls == HuangBaiB1Strategy and args.portfolio:
        from src.engine.portfolio_simulator import PortfolioSimulator
        from src.strategies.huangbai_b1_strategy import preload_all_signals

        print("=" * 55)
        print("  阶段1: 预加载全市场信号数据")
        print("=" * 55)
        all_signals, trading_days = preload_all_signals(
            start=args.start, end=args.end,
            stock_type=args.stock_type)

        if not all_signals or len(trading_days) == 0:
            print("\n无有效数据，模拟终止。")
            return

        print(f"\n{'=' * 55}")
        print(f"  阶段2: 组合级模拟 ({len(trading_days)} 个交易日)")
        print(f"  区间: {args.start} ~ {args.end}")
        print(f"  资金: {PORTFOLIO_INITIAL_CASH:,.0f}  "
              f"最多 {PORTFOLIO_MAX_POSITIONS} 只  "
              f"每只 {PORTFOLIO_PER_POSITION:,.0f}")
        print(f"{'=' * 55}")

        sim = PortfolioSimulator(
            all_signals=all_signals,
            trading_days=trading_days,
            initial_cash=PORTFOLIO_INITIAL_CASH,
            max_positions=PORTFOLIO_MAX_POSITIONS,
            per_position_cash=PORTFOLIO_PER_POSITION,
            commission=COMMISSION,
            stock_type=args.stock_type,
            log_dir=LOG_DIR)
        sim.run()
        report = sim.report()
        PortfolioSimulator.print_report(report, log_file=sim._log_file, strategy_tag="[B1]")
        return

    # ---- huangbai_v2 策略：组合级模拟（含大盘MACD过滤） ----
    if strategy_cls == HuangBaiB1V2Strategy and args.portfolio:
        from src.engine.portfolio_simulator import PortfolioSimulator
        from src.strategies.huangbai_b1_v2_strategy import preload_all_signals as preload_v2

        print("=" * 55)
        print("  V2: 周线多头 + 大盘MACD + 黄白线金叉 + B1")
        print("  阶段1: 预加载全市场信号数据")
        print("=" * 55)
        all_signals, trading_days, market_macd_bullish = preload_v2(
            start=args.start, end=args.end,
            stock_type=args.stock_type)

        if not all_signals or len(trading_days) == 0:
            print("\n无有效数据，模拟终止。")
            return

        print(f"\n{'=' * 55}")
        print(f"  阶段2: V2组合级模拟 ({len(trading_days)} 个交易日)")
        print(f"  区间: {args.start} ~ {args.end}")
        print(f"  资金: {PORTFOLIO_INITIAL_CASH:,.0f}  "
              f"最多 {PORTFOLIO_MAX_POSITIONS} 只  "
              f"每只 {PORTFOLIO_PER_POSITION:,.0f}")
        macd_status = "已启用" if market_macd_bullish is not None else "不可用(跳过)"
        print(f"  大盘MACD过滤: {macd_status}")
        print(f"{'=' * 55}")

        sim = PortfolioSimulator(
            all_signals=all_signals,
            trading_days=trading_days,
            initial_cash=PORTFOLIO_INITIAL_CASH,
            max_positions=PORTFOLIO_MAX_POSITIONS,
            per_position_cash=PORTFOLIO_PER_POSITION,
            commission=COMMISSION,
            stock_type=args.stock_type,
            log_dir=LOG_DIR,
            market_macd_bullish=market_macd_bullish)
        sim.run()
        report = sim.report()
        PortfolioSimulator.print_report(report, log_file=sim._log_file, strategy_tag="[B1V2]")

        if args.chart:
            from src.charting import generate_charts
            generate_charts(report["trade_list"], sim._all_signals)
        return
    if strategy_cls == HuangBaiB1V3Strategy and args.portfolio:
        from src.engine.portfolio_simulator import PortfolioSimulator
        from src.strategies.huangbai_b1_v3_strategy import preload_all_signals as preload_v3

        print("=" * 55)
        print("  V3: 周线多头 + 大盘MACD + 黄白线金叉 + 通达信B1")
        print("  阶段1: 预加载全市场信号数据")
        print("=" * 55)
        all_signals, trading_days, market_macd_bullish = preload_v3(
            start=args.start, end=args.end,
            stock_type=args.stock_type)

        if not all_signals or len(trading_days) == 0:
            print("\n无有效数据，模拟终止。")
            return

        print(f"\n{'=' * 55}")
        print(f"  阶段2: V3组合级模拟 ({len(trading_days)} 个交易日)")
        print(f"  区间: {args.start} ~ {args.end}")
        print(f"  资金: {PORTFOLIO_INITIAL_CASH:,.0f}  "
              f"最多 {PORTFOLIO_MAX_POSITIONS} 只  "
              f"每只 {PORTFOLIO_PER_POSITION:,.0f}")
        macd_status = "已启用" if market_macd_bullish is not None else "不可用(跳过)"
        print(f"  大盘MACD过滤: {macd_status}")
        print(f"{'=' * 55}")

        sim = PortfolioSimulator(
            all_signals=all_signals,
            trading_days=trading_days,
            initial_cash=PORTFOLIO_INITIAL_CASH,
            max_positions=PORTFOLIO_MAX_POSITIONS,
            per_position_cash=PORTFOLIO_PER_POSITION,
            commission=COMMISSION,
            stock_type=args.stock_type,
            log_dir=LOG_DIR,
            market_macd_bullish=market_macd_bullish,
            strategy_tag="[B1V3]")
        sim.run()
        report = sim.report()
        PortfolioSimulator.print_report(report, log_file=sim._log_file, strategy_tag="[B1V3]")
        return

    # ---- huangbai 策略：全市场扫描 + 回测 ----
    if strategy_cls == HuangBaiB1Strategy and (args.scan or args.symbol is None):
        print("=" * 55)
        print("  阶段1: 全市场选股扫描")
        print("=" * 55)
        results = scan_all(stock_type=args.stock_type)

        if args.scan_only:
            return

        if not results:
            print("\n无符合条件的股票，回测终止。")
            return

        codes = [r["code"] for r in results]
        print(f"\n{'=' * 55}")
        print(f"  阶段2: 对 {len(codes)} 只选股结果执行回测")
        print(f"  区间: {args.start} ~ {args.end}")
        print(f"{'=' * 55}")
        _run_backtest(codes, args)
        return

    # ---- huangbai_v2 策略：全市场扫描 + 回测 ----
    if strategy_cls == HuangBaiB1V2Strategy and (args.scan or args.symbol is None):
        print("=" * 55)
        print("  V2: 全市场选股扫描（含大盘MACD过滤）")
        print("=" * 55)
        results, market_macd_ok = scan_all_v2(stock_type=args.stock_type)

        if args.scan_only:
            return

        if not results or not market_macd_ok:
            print("\n无符合条件的股票或大盘MACD空头，回测终止。")
            return

        codes = [r["code"] for r in results]
        print(f"\n{'=' * 55}")
        print(f"  阶段2: 对 {len(codes)} 只选股结果执行回测")
        print(f"  区间: {args.start} ~ {args.end}")
        print(f"{'=' * 55}")
        _run_backtest(codes, args)
        return

    # ---- huangbai_v3 策略：全市场扫描 + 回测 ----
    if strategy_cls == HuangBaiB1V3Strategy and (args.scan or args.symbol is None):
        print("=" * 55)
        print("  V3: 全市场选股扫描（通达信B1 + 大盘MACD过滤）")
        print("=" * 55)
        results, market_macd_ok = scan_all_v3(stock_type=args.stock_type)

        if args.scan_only:
            return

        if not results or not market_macd_ok:
            print("\n无符合条件的股票或大盘MACD空头，回测终止。")
            return

        codes = [r["code"] for r in results]
        print(f"\n{'=' * 55}")
        print(f"  阶段2: 对 {len(codes)} 只选股结果执行回测")
        print(f"  区间: {args.start} ~ {args.end}")
        print(f"{'=' * 55}")
        _run_backtest(codes, args)
        return

    # ---- 指定股票回测 ----
    symbols = args.symbol or DEFAULT_STOCKS
    feed_provider = TdxDataFeed()
    backtester = Backtester(cash=args.cash, commission=COMMISSION)

    for symbol in symbols:
        feed = feed_provider.get_feed(symbol, start=args.start, end=args.end)
        backtester.add_feed(feed, name=symbol)

    if strategy_cls == HuangBaiB1Strategy:
        backtester.add_strategy(strategy_cls, stock_type=args.stock_type)
    elif strategy_cls == HuangBaiB1V2Strategy:
        # V2: 加载大盘指数数据作为第二数据源
        try:
            market_feed = feed_provider.get_feed(
                MARKET_INDEX_CODE, start=args.start, end=args.end)
            if market_feed is not None:
                backtester.add_feed(market_feed, name=MARKET_INDEX_CODE)
            else:
                print(f"  警告: 无法加载大盘指数({MARKET_INDEX_CODE})数据，大盘MACD过滤将被跳过")
        except Exception as e:
            print(f"  警告: 加载大盘指数数据失败({e})，大盘MACD过滤将被跳过")
        backtester.add_strategy(strategy_cls, stock_type=args.stock_type)
    elif strategy_cls == HuangBaiB1V3Strategy:
        try:
            market_feed = feed_provider.get_feed(
                MARKET_INDEX_CODE, start=args.start, end=args.end)
            if market_feed is not None:
                backtester.add_feed(market_feed, name=MARKET_INDEX_CODE)
            else:
                print(f"  警告: 无法加载大盘指数({MARKET_INDEX_CODE})数据，大盘MACD过滤将被跳过")
        except Exception as e:
            print(f"  警告: 加载大盘指数数据失败({e})，大盘MACD过滤将被跳过")
        backtester.add_strategy(strategy_cls, stock_type=args.stock_type)
    else:
        backtester.add_strategy(strategy_cls)

    report = backtester.run()
    Backtester.print_report(report)

    if PLOT_ENABLED and not args.no_plot:
        backtester.plot()


if __name__ == "__main__":
    main()
