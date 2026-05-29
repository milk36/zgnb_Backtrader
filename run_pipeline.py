"""管道脚本：ZStock B1 选股 → AI 评分 → 组合级回测

参考 StockTradebyZ 的管道架构，串联完整选股+回测流程:

Phase 1: B1 选股 — preload_all_signals() 预加载全市场信号
Phase 2: AI 评分 — batch_score() 量化评分过滤
Phase 3: 组合级回测 — PortfolioSimulator 执行模拟

用法:
    python run_pipeline.py                    # 完整管道
    python run_pipeline.py --no-ai-score      # 跳过AI评分
    python run_pipeline.py --start-from score # 从评分阶段开始
    python run_pipeline.py --chart            # 生成K线图
"""

import argparse
import json
import os
import pickle
import sys
import time

import numpy as np
import pandas as pd

from config import (
    TDX_DIR, TDX_MARKET, COMMISSION,
    ZSTOCK_INITIAL_CASH, ZSTOCK_MAX_POSITIONS, ZSTOCK_PER_POSITION,
    ZSTOCK_AI_SCORE_WATCH, ZSTOCK_AI_SCORE_PASS,
    DEFAULT_START_DATE, DEFAULT_END_DATE, LOG_DIR,
)


def _ensure_dir(path):
    os.makedirs(path, exist_ok=True)
    return path


def phase1_scan(args, output_dir):
    """Phase 1: B1 选股"""
    from src.strategies.zstock_b1_strategy import preload_all_signals

    print(f"\n{'=' * 60}")
    print("  Phase 1/3: ZStock B1 选股（4-Filter）")
    print(f"  区间: {args.start_date} ~ {args.end_date}")
    print(f"{'=' * 60}")

    t0 = time.time()
    all_signals, trading_days, market_macd_bullish = preload_all_signals(
        start=args.start_date, end=args.end_date,
        stock_type=args.stock_type,
        max_workers=args.max_workers,
        tdxdir=TDX_DIR, market=TDX_MARKET)

    elapsed = time.time() - t0
    print(f"  Phase 1 完成: {len(all_signals)} 只股票, {len(trading_days)} 个交易日, 耗时 {elapsed:.1f}s")

    # 统计 B1 信号
    b1_count = sum(1 for sig in all_signals.values() if np.any(sig.get('b1', np.zeros(1, dtype=bool))))
    print(f"  含 B1 信号的股票: {b1_count} 只")

    if not all_signals or len(trading_days) == 0:
        print("\n无有效数据，管道终止。")
        return None, None, None

    # 保存中间结果
    signals_path = os.path.join(output_dir, 'signals.pkl')
    with open(signals_path, 'wb') as f:
        pickle.dump({
            'all_signals': all_signals,
            'trading_days': trading_days,
            'market_macd_bullish': market_macd_bullish,
        }, f)
    print(f"  信号已保存: {signals_path}")

    return all_signals, trading_days, market_macd_bullish


def phase2_score(all_signals, args, output_dir):
    """Phase 2: AI 量化评分"""
    from src.ai_scorer import batch_score

    print(f"\n{'=' * 60}")
    print("  Phase 2/3: 量化评分")
    print(f"  阈值: {args.score_threshold}")
    print(f"{'=' * 60}")

    t0 = time.time()
    filtered_signals, scores_dict = batch_score(
        all_signals,
        threshold=args.score_threshold)

    elapsed = time.time() - t0
    print(f"  Phase 2 完成: 保留 {len(filtered_signals)} 只, 耗时 {elapsed:.1f}s")

    # 保存评分结果
    scores_path = os.path.join(output_dir, 'scores.json')
    serializable = {}
    for code, result in scores_dict.items():
        serializable[code] = {
            "total_score": result["total_score"],
            "verdict": result["verdict"],
            "signal_type": result["signal_type"],
            "comment": result["comment"],
            "scores": result["scores"],
        }
    with open(scores_path, 'w', encoding='utf-8') as f:
        json.dump(serializable, f, ensure_ascii=False, indent=2)
    print(f"  评分已保存: {scores_path}")

    # 打印评分排名
    ranked = sorted(scores_dict.items(), key=lambda x: x[1]['total_score'], reverse=True)
    if ranked:
        print(f"\n  {'代码':>8} {'总分':>6} {'判定':>6} {'信号':>12} 点评")
        print("  " + "-" * 70)
        for code, r in ranked[:30]:
            print(f"  {code:>8} {r['total_score']:>6.2f} {r['verdict']:>6} "
                  f"{r['signal_type']:>12} {r['comment']}")

    return filtered_signals


def phase3_backtest(filtered_signals, trading_days, market_macd_bullish, args, output_dir):
    """Phase 3: 组合级回测"""
    from src.engine.portfolio_simulator import PortfolioSimulator

    print(f"\n{'=' * 60}")
    print("  Phase 3/3: 组合级回测")
    print(f"  区间: {args.start_date} ~ {args.end_date}")
    print(f"  资金: {args.initial_cash:,.0f}  最多 {args.max_positions} 只  "
          f"每只 {args.per_position:,.0f}")
    print(f"  参与回测股票: {len(filtered_signals)} 只")
    print(f"{'=' * 60}")

    sim = PortfolioSimulator(
        all_signals=filtered_signals,
        trading_days=trading_days,
        initial_cash=args.initial_cash,
        max_positions=args.max_positions,
        per_position_cash=args.per_position,
        commission=COMMISSION,
        stock_type=args.stock_type,
        log_dir=LOG_DIR,
        market_macd_bullish=market_macd_bullish,
        strategy_tag="[ZStockB1]",
    )
    sim.run()
    report = sim.report()
    PortfolioSimulator.print_report(report, log_file=sim._log_file,
                                    strategy_tag="[ZStockB1]")

    # 保存报告
    report_path = os.path.join(output_dir, 'report.txt')
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(f"ZStock B1 管道回测报告\n")
        f.write(f"区间: {args.start_date} ~ {args.end_date}\n")
        f.write(f"总收益率: {report['total_return']:.2f}%\n")
        f.write(f"最大回撤: {report['max_drawdown']:.2f}%\n")
        f.write(f"交易股票数: {report['total_trades']}\n")
        f.write(f"盈利: {report['won']}  亏损: {report['lost']}\n")
    print(f"\n  报告已保存: {report_path}")

    if args.chart:
        from src.charting import generate_charts
        chart_dir = os.path.join(output_dir, 'charts')
        print(f"  生成K线图到 {chart_dir}/ ...")
        generate_charts(report["trade_list"], sim._all_signals,
                        output_dir=chart_dir, sub_chart="volume")

    return report


def main():
    parser = argparse.ArgumentParser(
        description="ZStock B1 管道: 选股 → 评分 → 回测")

    # 选股参数
    parser.add_argument("--stock-type", choices=["main", "tech"], default="main",
                        help="板块类型 (默认: main)")
    parser.add_argument("--start-date", default=DEFAULT_START_DATE,
                        help="起始日期 (默认: 2023-01-01)")
    parser.add_argument("--end-date", default=DEFAULT_END_DATE,
                        help="结束日期 (默认: 2025-12-31)")

    # 管道控制
    parser.add_argument("--start-from", choices=["scan", "score", "backtest"],
                        default="scan",
                        help="从指定阶段开始 (默认: scan=完整管道)")
    parser.add_argument("--no-ai-score", action="store_true",
                        help="跳过AI评分筛选（所有B1信号直接进入回测）")
    parser.add_argument("--score-threshold", type=float,
                        default=ZSTOCK_AI_SCORE_WATCH,
                        help=f"AI评分阈值 (默认: {ZSTOCK_AI_SCORE_WATCH})")
    parser.add_argument("--max-workers", type=int, default=None,
                        help="并行工作进程数")

    # 回测参数
    parser.add_argument("--initial-cash", type=float,
                        default=ZSTOCK_INITIAL_CASH,
                        help=f"初始资金 (默认: {ZSTOCK_INITIAL_CASH})")
    parser.add_argument("--max-positions", type=int,
                        default=ZSTOCK_MAX_POSITIONS,
                        help=f"最大持仓数 (默认: {ZSTOCK_MAX_POSITIONS})")
    parser.add_argument("--per-position", type=float,
                        default=ZSTOCK_PER_POSITION,
                        help=f"每只股票资金 (默认: {ZSTOCK_PER_POSITION})")

    # 输出
    parser.add_argument("--chart", action="store_true",
                        help="生成K线图")
    parser.add_argument("--output-dir", default="pipeline_output",
                        help="输出目录 (默认: pipeline_output)")

    args = parser.parse_args()
    output_dir = _ensure_dir(args.output_dir)

    print(f"\n{'=' * 60}")
    print("  ZStock B1 管道脚本")
    print(f"  阶段: {args.start_from} → {'跳过评分' if args.no_ai_score else '含评分'} → 回测")
    print(f"  输出: {output_dir}")
    print(f"{'=' * 60}")

    # ── Phase 1: 选股 ──
    all_signals = trading_days = market_macd_bullish = None

    if args.start_from == "scan":
        all_signals, trading_days, market_macd_bullish = phase1_scan(args, output_dir)
        if all_signals is None:
            return
    else:
        # 从已有信号文件加载
        signals_path = os.path.join(output_dir, 'signals.pkl')
        if not os.path.exists(signals_path):
            print(f"\n[ERROR] 找不到信号文件: {signals_path}")
            print("  请先运行完整管道: python run_pipeline.py")
            return
        with open(signals_path, 'rb') as f:
            data = pickle.load(f)
        all_signals = data['all_signals']
        trading_days = data['trading_days']
        market_macd_bullish = data['market_macd_bullish']
        print(f"\n  已加载信号: {len(all_signals)} 只股票, {len(trading_days)} 个交易日")

    # ── Phase 2: 评分 ──
    filtered_signals = all_signals

    if not args.no_ai_score and args.start_from != "backtest":
        filtered_signals = phase2_score(all_signals, args, output_dir)
        if not filtered_signals:
            print("\n评分过滤后无符合条件的股票，管道终止。")
            return
    elif args.start_from == "backtest":
        # 尝试加载评分过滤后的信号
        scores_path = os.path.join(output_dir, 'scores.json')
        if os.path.exists(scores_path) and not args.no_ai_score:
            with open(scores_path, 'r', encoding='utf-8') as f:
                scores_dict = json.load(f)
            passed_codes = {code for code, r in scores_dict.items()
                           if r['total_score'] >= args.score_threshold}
            filtered_signals = {k: v for k, v in all_signals.items()
                               if k in passed_codes}
            print(f"  已加载评分过滤: 保留 {len(filtered_signals)} 只")

    # ── Phase 3: 回测 ──
    phase3_backtest(filtered_signals, trading_days, market_macd_bullish,
                    args, output_dir)

    print(f"\n{'=' * 60}")
    print("  管道执行完毕！")
    print(f"  输出目录: {output_dir}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
