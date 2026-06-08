"""训练数据构建器

运行 V4 基准回测 → 从 B1 触发点的信号中提取特征 + 标签 → 构建 DataFrame
"""

import os
import time
import json
import numpy as np
import pandas as pd

from config import (
    TDX_DIR, TDX_MARKET, SCAN_MAX_WORKERS,
    PORTFOLIO_INITIAL_CASH, PORTFOLIO_MAX_POSITIONS,
    PORTFOLIO_PER_POSITION, COMMISSION,
    ML_LABEL_WIN, ML_LABEL_LOSS, ML_TRAIN_TEST_SPLIT,
    HUANGBAI_M1, HUANGBAI_M2, HUANGBAI_M3, HUANGBAI_M4,
    HUANGBAI_N, HUANGBAI_M, HUANGBAI_N1, HUANGBAI_N2,
    DNZH_MIN_MARKET_CAP,
)
from src.ml.feature_extractor import FEATURE_NAMES, compute_feature_arrays, extract_features_at_bar


class TrainingDataBuilder:
    """从V4回测交易记录中构建ML训练数据集"""

    def __init__(self, start="2023-01-01", end="2025-12-31",
                 stock_type="main"):
        self.start = start
        self.end = end
        self.stock_type = stock_type

    def build(self):
        """
        完整构建流程

        Returns:
            (df, metadata) 元组
            df: DataFrame，列包含 FEATURE_NAMES + label + split + code + buy_date + pnl_pct
            metadata: dict，包含构建信息
        """
        t0 = time.time()

        # 步骤1: 加载V4信号
        print("=" * 55)
        print("  步骤1: 预加载V4全市场信号")
        print("=" * 55)
        all_signals, trading_days, market_macd = self._load_signals()

        if not all_signals or len(trading_days) == 0:
            print("错误: 无有效信号数据")
            return None, None

        # 步骤2: 运行基准V4回测
        print(f"\n{'=' * 55}")
        print("  步骤2: 运行V4基准回测（生成交易记录）")
        print("=" * 55)
        trade_list = self._run_baseline_backtest(all_signals, trading_days, market_macd)

        if not trade_list:
            print("错误: 无交易记录")
            return None, None

        # 步骤3: 提取特征和标签
        print(f"\n{'=' * 55}")
        print("  步骤3: 提取特征和标签")
        print("=" * 55)
        df = self._extract_features_and_labels(all_signals, trading_days, trade_list)

        if df is None or len(df) == 0:
            print("错误: 无有效训练样本")
            return None, None

        # 步骤4: 时间序列分割
        df = self._add_temporal_split(df, trading_days)

        # 步骤5: 构建元数据
        elapsed = time.time() - t0
        metadata = {
            "start": self.start,
            "end": self.end,
            "stock_type": self.stock_type,
            "total_samples": len(df),
            "label_distribution": df["label"].value_counts().to_dict(),
            "split_distribution": df["split"].value_counts().to_dict(),
            "feature_count": len(FEATURE_NAMES),
            "build_time_sec": round(elapsed, 1),
        }

        # 打印统计
        print(f"\n  构建完成:")
        print(f"    总样本数: {len(df)}")
        label_dist = df["label"].value_counts().sort_index()
        label_names = {0: "大亏损", 1: "小幅波动", 2: "大盈利"}
        for label_val, count in label_dist.items():
            print(f"    label={label_val}({label_names.get(label_val, '?')}): {count} ({count/len(df)*100:.1f}%)")
        split_dist = df["split"].value_counts()
        for split_val, count in split_dist.items():
            print(f"    {split_val}: {count}")
        print(f"    耗时: {elapsed:.1f}s")

        return df, metadata

    def _load_signals(self):
        """加载V4全市场信号"""
        from src.strategies.huangbai_b1_v4_strategy import preload_all_signals
        return preload_all_signals(
            start=self.start, end=self.end,
            stock_type=self.stock_type,
            max_workers=SCAN_MAX_WORKERS,
            tdxdir=TDX_DIR, market=TDX_MARKET,
        )

    def _run_baseline_backtest(self, all_signals, trading_days, market_macd):
        """运行V4基准回测，返回trade_list"""
        from src.engine.portfolio_simulator import PortfolioSimulator

        sim = PortfolioSimulator(
            all_signals=all_signals,
            trading_days=trading_days,
            initial_cash=PORTFOLIO_INITIAL_CASH,
            max_positions=PORTFOLIO_MAX_POSITIONS,
            per_position_cash=PORTFOLIO_PER_POSITION,
            commission=COMMISSION,
            stock_type=self.stock_type,
            t_plus_n=3,
            log_dir="logs",
            market_macd_bullish=market_macd,
            strategy_tag="[B1V4-ML-TRAIN]",
        )
        sim.run()
        report = sim.report()
        trade_list = report.get("trade_list", [])

        # 只保留清仓记录（非部分卖出）
        closed_trades = [t for t in trade_list if not t.get("partial", False)]
        print(f"  交易记录: {len(trade_list)} 笔（清仓 {len(closed_trades)} 笔）")

        return closed_trades

    def _extract_features_and_labels(self, all_signals, trading_days, trade_list):
        """从交易记录中提取特征和标签"""
        # V4 参数
        params = {
            "m1": HUANGBAI_M1, "m2": HUANGBAI_M2,
            "m3": HUANGBAI_M3, "m4": HUANGBAI_M4,
            "n": HUANGBAI_N, "m": HUANGBAI_M,
            "n1": HUANGBAI_N1, "n2": HUANGBAI_N2,
            "stock_type": self.stock_type,
        }

        rows = []
        matched = 0
        unmatched = 0

        for trade in trade_list:
            code = trade["code"]
            buy_date = trade["buy_date"]
            pnl_pct = trade["pnl_pct"]

            if code not in all_signals:
                unmatched += 1
                continue

            sig = all_signals[code]

            # 找到 buy_date 对应的 bar 索引
            dates = sig["dates"]
            # 信号中 dates 是 pd.DatetimeIndex
            buy_date_ts = pd.Timestamp(buy_date)
            mask = dates == buy_date_ts
            if not mask.any():
                # 尝试最近的日期
                unmatched += 1
                continue

            idx = np.where(mask)[0][0]

            # 检查B1信号是否为True
            if not sig["b1"][idx]:
                unmatched += 1
                continue

            # 提取特征
            C = sig["close"]
            H = sig["high"]
            L = sig["low"]
            O = sig["open"]
            V = sig["volume"]

            feature_arrays = compute_feature_arrays(sig, C, H, L, O, V, dates, params)
            features = extract_features_at_bar(feature_arrays, idx)

            # 三分类标签
            if pnl_pct < ML_LABEL_LOSS:
                label = 0  # 大亏损
            elif pnl_pct > ML_LABEL_WIN:
                label = 2  # 大盈利
            else:
                label = 1  # 小幅波动

            row = {
                "code": code,
                "buy_date": buy_date,
                "pnl_pct": pnl_pct,
                "pnl_amount": trade["pnl_amount"],
                "sell_reason": trade["reason"],
                "label": label,
            }
            row.update(features)
            rows.append(row)
            matched += 1

        print(f"  匹配: {matched}  未匹配: {unmatched}")

        if not rows:
            return None

        df = pd.DataFrame(rows)
        return df

    def _add_temporal_split(self, df, trading_days):
        """添加时间序列分割列"""
        # 按日期排序
        df = df.sort_values("buy_date").reset_index(drop=True)

        n = len(df)
        val_size = int(n * ML_TRAIN_TEST_SPLIT)
        test_size = int(n * ML_TRAIN_TEST_SPLIT)
        train_size = n - val_size - test_size

        df["split"] = "train"
        df.iloc[train_size:train_size + val_size, df.columns.get_loc("split")] = "val"
        df.iloc[train_size + val_size:, df.columns.get_loc("split")] = "test"

        return df

    def save(self, df, metadata, output_dir="ml_output"):
        """保存训练数据到文件"""
        os.makedirs(output_dir, exist_ok=True)

        # 保存 DataFrame
        csv_path = os.path.join(output_dir, "training_data.csv")
        df.to_csv(csv_path, index=False, encoding="utf-8-sig")
        print(f"  训练数据已保存: {csv_path} ({len(df)} 行)")

        # 保存元数据
        meta_path = os.path.join(output_dir, "metadata.json")
        # 确保 metadata 中的值都是 JSON 可序列化的
        safe_meta = {}
        for k, v in metadata.items():
            if isinstance(v, (np.integer,)):
                safe_meta[k] = int(v)
            elif isinstance(v, (np.floating,)):
                safe_meta[k] = float(v)
            elif isinstance(v, dict):
                safe_meta[k] = {
                    str(kk): int(vv) if isinstance(vv, (np.integer,))
                    else float(vv) if isinstance(vv, (np.floating,))
                    else vv
                    for kk, vv in v.items()
                }
            else:
                safe_meta[k] = v

        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(safe_meta, f, ensure_ascii=False, indent=2)
        print(f"  元数据已保存: {meta_path}")
