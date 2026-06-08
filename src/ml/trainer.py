"""LightGBM 三分类训练器"""

import json
import os
import time
import numpy as np
import pandas as pd


class MLTrainer:
    """LightGBM 多分类训练器"""

    DEFAULT_PARAMS = {
        "objective": "multiclass",
        "num_class": 3,
        "metric": "multi_logloss",
        "boosting_type": "gbdt",
        "num_leaves": 31,
        "learning_rate": 0.05,
        "max_depth": 5,
        "min_child_samples": 20,
        "feature_fraction": 0.8,
        "bagging_fraction": 0.8,
        "bagging_freq": 5,
        "lambda_l1": 0.1,
        "lambda_l2": 1.0,
        "verbose": -1,
    }

    def __init__(self, params=None):
        self.params = {**self.DEFAULT_PARAMS, **(params or {})}
        self.model = None
        self.feature_importance_ = None

    def train(self, df, feature_names, label_col="label"):
        """
        训练模型。

        df 必须包含: feature_names 中的特征列 + label_col + 'split' 列
        split 列值: 'train' / 'val' / 'test'

        返回评估结果 dict:
        {
            "train_logloss": float,
            "val_logloss": float,
            "test_logloss": float,
            "val_accuracy": float,
            "test_accuracy": float,
            "val_class_metrics": dict,  # 每类的precision/recall
            "test_class_metrics": dict,
            "feature_importance": dict,
            "train_samples": int,
            "val_samples": int,
            "test_samples": int,
            "label_distribution": dict,
            "best_iteration": int,
        }
        """
        import lightgbm as lgb

        train_mask = df["split"] == "train"
        val_mask = df["split"] == "val"
        test_mask = df["split"] == "test"

        X_train = df.loc[train_mask, feature_names].values
        y_train = df.loc[train_mask, label_col].values.astype(int)
        X_val = df.loc[val_mask, feature_names].values
        y_val = df.loc[val_mask, label_col].values.astype(int)
        X_test = df.loc[test_mask, feature_names].values
        y_test = df.loc[test_mask, label_col].values.astype(int)

        train_data = lgb.Dataset(X_train, label=y_train, feature_name=feature_names)
        val_data = lgb.Dataset(X_val, label=y_val, feature_name=feature_names, reference=train_data)

        callbacks = [
            lgb.early_stopping(stopping_rounds=50),
            lgb.log_evaluation(period=50),
        ]

        self.model = lgb.train(
            self.params,
            train_data,
            num_boost_round=300,
            valid_sets=[train_data, val_data],
            valid_names=["train", "val"],
            callbacks=callbacks,
        )

        # 评估
        result = {}
        result["best_iteration"] = self.model.best_iteration
        result["train_samples"] = int(train_mask.sum())
        result["val_samples"] = int(val_mask.sum())
        result["test_samples"] = int(test_mask.sum())

        # 标签分布
        for split_name, mask in [("train", train_mask), ("val", val_mask), ("test", test_mask)]:
            labels = df.loc[mask, label_col].value_counts().to_dict()
            result[f"{split_name}_label_dist"] = {int(k): int(v) for k, v in labels.items()}

        # 预测和准确率
        for split_name, X, y in [("val", X_val, y_val), ("test", X_test, y_test)]:
            if len(y) > 0:
                proba = self.model.predict(X)
                pred = np.argmax(proba, axis=1)
                accuracy = np.mean(pred == y)
                result[f"{split_name}_accuracy"] = float(accuracy)
                # 每类 precision/recall
                class_metrics = {}
                for cls in range(3):
                    tp = np.sum((pred == cls) & (y == cls))
                    fp = np.sum((pred == cls) & (y != cls))
                    fn = np.sum((pred != cls) & (y == cls))
                    precision = tp / max(tp + fp, 1)
                    recall = tp / max(tp + fn, 1)
                    class_metrics[int(cls)] = {"precision": float(precision), "recall": float(recall)}
                result[f"{split_name}_class_metrics"] = class_metrics
            else:
                result[f"{split_name}_accuracy"] = 0.0
                result[f"{split_name}_class_metrics"] = {}

        # 特征重要性
        importance = self.model.feature_importance(importance_type="gain")
        fi_dict = {name: float(imp) for name, imp in zip(feature_names, importance)}
        result["feature_importance"] = dict(sorted(fi_dict.items(), key=lambda x: -x[1]))
        self.feature_importance_ = result["feature_importance"]

        return result

    def save(self, model_path, metadata=None):
        """保存模型和元数据"""
        if self.model is None:
            raise ValueError("模型未训练")
        os.makedirs(os.path.dirname(model_path) or ".", exist_ok=True)
        self.model.save_model(model_path)
        if metadata:
            meta_path = model_path.replace(".txt", "_metadata.json")
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(metadata, f, ensure_ascii=False, indent=2)

    def get_feature_importance_df(self):
        """返回特征重要性 DataFrame（按importance降序）"""
        if self.feature_importance_ is None:
            return pd.DataFrame()
        return pd.DataFrame([
            {"feature": k, "importance": v}
            for k, v in self.feature_importance_.items()
        ]).sort_values("importance", ascending=False).reset_index(drop=True)
