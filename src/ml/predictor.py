"""ML 模型预测器"""

import json
import os
import glob
import numpy as np


class MLPredictor:
    """加载训练好的 LightGBM 模型并进行预测"""

    def __init__(self, model_path):
        import lightgbm as lgb
        self.model = lgb.Booster(model_file=model_path)
        self.feature_names = self.model.feature_name()

        # 加载元数据
        meta_path = model_path.replace(".txt", "_metadata.json")
        self.metadata = {}
        if os.path.exists(meta_path):
            with open(meta_path, "r", encoding="utf-8") as f:
                self.metadata = json.load(f)

    def predict_proba(self, features_df):
        """
        预测三分类概率。

        Args:
            features_df: DataFrame，列名为特征名

        Returns:
            numpy array, shape (n_samples, 3)，每行为 [p_loss, p_neutral, p_win]
        """
        X = features_df[self.feature_names].values
        return self.model.predict(X)

    def predict_batch(self, feature_arrays, mask=None):
        """
        批量预测，返回大盈利概率(ml_score)。

        Args:
            feature_arrays: {name: array} 来自 feature_extractor.compute_feature_arrays()
            mask: 布尔数组，只对mask=True的bar预测（如 sig["b1"]）

        Returns:
            numpy array, 长度与mask相同，每个值为大盈利(label=2)的概率
        """
        n = len(next(iter(feature_arrays.values())))
        result = np.zeros(n, dtype=float)

        if mask is not None:
            indices = np.where(mask)[0]
        else:
            indices = np.arange(n)

        if len(indices) == 0:
            return result

        # 构建 DataFrame
        import pandas as pd
        data = {}
        for name in self.feature_names:
            arr = feature_arrays.get(name)
            if arr is not None:
                data[name] = arr[indices]
            else:
                data[name] = np.zeros(len(indices))

        df = pd.DataFrame(data)
        proba = self.predict_proba(df)
        result[indices] = proba[:, 2]  # label=2 的概率

        return result

    def predict_single(self, feature_dict):
        """预测单个样本，返回大盈利概率"""
        import pandas as pd
        df = pd.DataFrame([feature_dict])
        proba = self.predict_proba(df)
        return float(proba[0, 2])


def load_or_default_predictor(model_path=None):
    """
    加载指定模型或最新的可用模型。

    Args:
        model_path: 指定模型路径。None则自动搜索最新的。

    Returns:
        MLPredictor 实例，或 None（无可用模型时）
    """
    from config import ML_MODEL_DIR

    if model_path and os.path.exists(model_path):
        return MLPredictor(model_path)

    # 自动搜索最新的模型
    model_dir = ML_MODEL_DIR
    if not os.path.isdir(model_dir):
        return None

    pattern = os.path.join(model_dir, "b1v4_ml_v*.txt")
    model_files = glob.glob(pattern)
    if not model_files:
        return None

    # 按修改时间降序，取最新的
    latest = max(model_files, key=os.path.getmtime)
    print(f"  加载ML模型: {latest}")
    return MLPredictor(latest)
