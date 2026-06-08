"""B1 V4 ML 模型训练脚本

用法:
    python train_b1v4_ml.py --train              # 完整训练流程
    python train_b1v4_ml.py --build-data         # 仅构建训练数据
    python train_b1v4_ml.py --eval               # 评估已有模型
    python train_b1v4_ml.py --feature-importance # 显示特征重要性

可选参数:
    --start 2023-01-01    训练数据起始日期
    --end 2025-12-31      训练数据结束日期
    --stock-type main     股票类型 (main/tech)
    --model-path          指定模型保存路径 (默认自动生成)
"""

import argparse
import json
import os
import time
import numpy as np
import pandas as pd


def parse_args():
    parser = argparse.ArgumentParser(description="B1 V4 ML 模型训练")
    parser.add_argument("--train", action="store_true", help="完整训练流程 (构建数据 + 训练 + 评估)")
    parser.add_argument("--build-data", action="store_true", help="仅构建训练数据")
    parser.add_argument("--eval", action="store_true", help="评估已有模型")
    parser.add_argument("--feature-importance", action="store_true", help="显示特征重要性")
    parser.add_argument("--start", default="2023-01-01", help="起始日期")
    parser.add_argument("--end", default="2025-12-31", help="结束日期")
    parser.add_argument("--stock-type", default="main", choices=["main", "tech"])
    parser.add_argument("--model-path", default=None, help="模型保存/加载路径")
    return parser.parse_args()


def build_data(args):
    """构建训练数据"""
    from src.ml.training_data_builder import TrainingDataBuilder

    print(f"\n{'=' * 60}")
    print(f"  构建训练数据: {args.start} ~ {args.end}  stock_type={args.stock_type}")
    print(f"{'=' * 60}")

    builder = TrainingDataBuilder(
        start=args.start, end=args.end,
        stock_type=args.stock_type,
    )
    df, metadata = builder.build()

    if df is None:
        print("构建失败")
        return None, None

    builder.save(df, metadata)
    return df, metadata


def train_model(df, metadata, args):
    """训练 LightGBM 模型"""
    from src.ml.trainer import MLTrainer
    from src.ml.feature_extractor import FEATURE_NAMES
    from config import ML_MODEL_DIR

    print(f"\n{'=' * 60}")
    print(f"  训练 LightGBM 三分类模型")
    print(f"  特征数: {len(FEATURE_NAMES)}")
    print(f"  样本数: {len(df)}")
    print(f"{'=' * 60}")

    trainer = MLTrainer()
    result = trainer.train(df, FEATURE_NAMES, label_col="label")

    # 打印结果
    print(f"\n  训练结果:")
    print(f"    最佳迭代: {result['best_iteration']}")
    for split in ["val", "test"]:
        acc = result.get(f"{split}_accuracy", 0)
        print(f"    {split} 准确率: {acc:.4f}")
        cm = result.get(f"{split}_class_metrics", {})
        for cls, metrics in cm.items():
            cls_name = {0: "大亏损", 1: "小幅波动", 2: "大盈利"}.get(cls, f"class_{cls}")
            print(f"      {cls_name}: precision={metrics['precision']:.3f}  recall={metrics['recall']:.3f}")

    # 保存模型
    model_dir = ML_MODEL_DIR
    os.makedirs(model_dir, exist_ok=True)

    # 自动版本号
    existing = [f for f in os.listdir(model_dir) if f.startswith("b1v4_ml_v") and f.endswith(".txt")]
    next_ver = len(existing) + 1
    model_name = f"b1v4_ml_v{next_ver}.txt"

    model_path = args.model_path or os.path.join(model_dir, model_name)

    save_metadata = {
        **(metadata or {}),
        **result,
        "model_path": model_path,
        "feature_names": FEATURE_NAMES,
    }
    # 移除非序列化字段
    for key in ["feature_importance"]:
        if key in save_metadata and isinstance(save_metadata[key], dict):
            pass  # 保留

    trainer.save(model_path, save_metadata)
    print(f"\n  模型已保存: {model_path}")

    # 打印 Top-10 特征重要性
    fi = trainer.get_feature_importance_df()
    if len(fi) > 0:
        print(f"\n  Top-10 特征重要性:")
        for _, row in fi.head(10).iterrows():
            print(f"    {row['feature']:20s}  {row['importance']:.1f}")

    return trainer, result


def eval_model(args):
    """评估已有模型"""
    from src.ml.predictor import load_or_default_predictor
    from config import ML_MODEL_DIR

    model_path = args.model_path
    predictor = load_or_default_predictor(model_path)
    if predictor is None:
        print("未找到可用模型")
        return

    print(f"\n{'=' * 60}")
    print(f"  模型评估: {predictor.model.model_file if hasattr(predictor.model, 'model_file') else 'loaded'}")
    print(f"  特征数: {len(predictor.feature_names)}")
    print(f"{'=' * 60}")

    meta = predictor.metadata
    if meta:
        print(f"\n  元数据:")
        for key in ["start", "end", "stock_type", "total_samples",
                     "val_accuracy", "test_accuracy", "best_iteration"]:
            if key in meta:
                print(f"    {key}: {meta[key]}")

        fi = meta.get("feature_importance", {})
        if fi:
            print(f"\n  Top-10 特征重要性:")
            for name, imp in list(fi.items())[:10]:
                print(f"    {name:20s}  {imp:.1f}")


def show_feature_importance(args):
    """显示特征重要性"""
    from src.ml.predictor import load_or_default_predictor

    predictor = load_or_default_predictor(args.model_path)
    if predictor is None:
        print("未找到可用模型")
        return

    fi = predictor.metadata.get("feature_importance", {})
    if not fi:
        print("无特征重要性数据")
        return

    print(f"\n{'=' * 60}")
    print(f"  特征重要性 (完整)")
    print(f"{'=' * 60}")
    for name, imp in fi.items():
        bar = "█" * int(imp / max(fi.values()) * 40)
        print(f"  {name:20s}  {imp:8.1f}  {bar}")


def main():
    args = parse_args()

    if not any([args.train, args.build_data, args.eval, args.feature_importance]):
        args.train = True  # 默认执行完整训练

    if args.build_data or args.train:
        df, metadata = build_data(args)
        if df is None:
            return

        if args.train:
            train_model(df, metadata, args)

    if args.eval:
        eval_model(args)

    if args.feature_importance:
        show_feature_importance(args)


if __name__ == "__main__":
    main()
