# 策略层：黄白线B1策略 V4 ML增强版

## 1. Purpose

V4 的机器学习增强变体，信号计算完全复用 V4，在 B1 触发点上叠加 LightGBM 三分类模型评分（大亏损/小幅波动/大盈利），用大盈利概率作为 `ml_score` 影响买入排序或直接过滤低质量信号。支持两种模式：soft（默认，仅排序）和 hard（低于阈值拒绝买入）。

## 2. How it Works

### 与 V4 的核心差异

| 对比项 | V4 | V4 ML |
|--------|----|----|
| 信号计算 | V4 原生 | **完全复用 V4**（`_v4_compute_all_bar_signals`） |
| 排序键 | `shrink_score` 升序 | **`ml_score` 降序**（`b2_sort_primary = -ml_score`） |
| ML 过滤 | 无 | **有**（hard 模式下 `b1 &= ml_score >= threshold`） |
| 退出逻辑 | 六级 + V4 专属 | 六级 + V4 专属（**完全相同**） |
| strategy_tag | `[B1V4]` | `[B1V4-ML]` |

### 架构：纯代理 + ML 评分注入

策略文件 `huangbai_b1_v4_ml_strategy.py` 采用纯代理模式：
1. 调用 `_v4_compute_all_bar_signals()` 获取基础信号
2. 加载 ML 模型（无模型时退化为 V4，`ml_score` 全 0.5）
3. 调用 `compute_feature_arrays()` 提取 31 个特征
4. 调用 `predictor.predict_batch()` 在 B1 bar 上预测 `ml_score`
5. hard 模式：`b1 &= ml_score >= threshold`；soft 模式：仅排序
6. 设置 `b2_sort_primary = -ml_score` 供 PortfolioSimulator 排序

### ML 模块（`src/ml/`）

| 模块 | 职责 |
|------|------|
| `feature_extractor.py` | 31 个特征的向量化计算（动量 8 + 距离/趋势 8 + 振幅/量能 8 + B1 子条件 7），从 V4 信号字典 + OHLCV 提取 |
| `training_data_builder.py` | 三步构建训练数据：运行 V4 基准回测 → 从清仓交易提取特征+标签 → 时间序列分割（train/val/test），标签阈值由 `ML_LABEL_WIN`/`ML_LABEL_LOSS` 控制 |
| `trainer.py` | LightGBM 三分类训练（`MLTrainer`），含 early stopping(50)、评估（accuracy + 每类 precision/recall）、特征重要性、模型保存 |
| `predictor.py` | `MLPredictor` 加载 `.txt` 模型文件 + `_metadata.json`；`load_or_default_predictor()` 自动搜索 `models/b1v4_ml_v*.txt` 最新版本 |

### 特征分组（31 个）

- **动量指标 (8)**: `rsi3`, `J`, `K`, `D`, `SHORT`, `LONG`, `j_turn`, `rsi_turn`
- **距离/趋势 (8)**: `dist_w`, `dist_y`, `dist_bbi`, `pct_w`, `pct_y`, `wy_gap_pct`, `white_slope`, `yellow_slope`
- **振幅/量能 (8)**: `near_amp`, `far_amp`, `daily_amp`, `shrink_score`, `vol_ratio_60`, `vol_vs_hhv50`, `chip_spread`, `rr_reward_risk`
- **B1 子条件 (7)**: `b_oversold_turn`, `b_oversold_shrink`, `b_raw`, `b_oversold_super`, `b_pb_white`, `b_pb_super`, `b_pb_yellow`

### 训练数据标签（三分类）

| 标签 | 含义 | 条件 |
|------|------|------|
| 0 | 大亏损 | `pnl_pct < ML_LABEL_LOSS`（默认 -5%） |
| 1 | 小幅波动 | 中间区域 |
| 2 | 大盈利 | `pnl_pct > ML_LABEL_WIN`（默认 +5%） |

### 时间序列分割

`ML_TRAIN_TEST_SPLIT`（默认 0.15）控制验证集和测试集各占比例，按 `buy_date` 排序后顺序切分（train → val → test），避免未来数据泄露。

### ML 过滤模式

| 模式 | 行为 | 配置 |
|------|------|------|
| **soft**（默认） | 保留所有 B1 信号，`ml_score` 仅影响排序优先级 | `ML_FILTER_MODE = "soft"` |
| **hard** | `ml_score < threshold` 的 B1 信号被拒绝 | `--ml-filter-mode hard --ml-threshold 0.5` |

### PortfolioSimulator 集成

- 复用标准 `PortfolioSimulator`，`strategy_tag="[B1V4-ML]"`
- V4 专属退出逻辑通过 `"B1V4" in tag` 匹配（`[B1V4-ML]` 包含 `B1V4` 子串）
- `b2_sort_primary = -ml_score`：排序时优先买入 ML 评分高的候选（升序取最小，即 ml_score 最大）

### 函数签名

| 函数 | 返回值 |
|------|--------|
| `scan_all()` | `(results, market_macd_ok)` |
| `preload_all_signals()` | `(all_signals, trading_days, market_macd_bullish)` 三元组 |

## 3. Relevant Code Modules

- `src/strategies/huangbai_b1_v4_ml_strategy.py` - V4 ML 增强策略（代理函数 + ML 评分注入）
- `src/strategies/huangbai_b1_v4_strategy.py` - V4 策略主文件（信号计算实际来源）
- `src/ml/__init__.py` - ML 模块包
- `src/ml/feature_extractor.py` - 31 特征提取器（`compute_feature_arrays`、`extract_features_at_bar`、`FEATURE_NAMES`）
- `src/ml/training_data_builder.py` - 训练数据构建器（`TrainingDataBuilder`：V4 回测 → 特征+标签 → 时间分割）
- `src/ml/trainer.py` - LightGBM 三分类训练器（`MLTrainer`：训练、评估、保存）
- `src/ml/predictor.py` - 模型预测器（`MLPredictor`、`load_or_default_predictor`：批量/单条预测）
- `train_b1v4_ml.py` - 独立训练 CLI 脚本（`--train` / `--build-data` / `--eval` / `--feature-importance`）
- `src/engine/portfolio_simulator.py` - 组合模拟器（`b2_sort_primary` 排序、V4 专属退出）
- `config.py` - `ML_MODEL_DIR`、`ML_FILTER_MODE`、`ML_SCORE_THRESHOLD`、`ML_LABEL_WIN`、`ML_LABEL_LOSS`、`ML_TRAIN_TEST_SPLIT`
- `main.py` - `huangbai_v4_ml` 策略注册 + `--ml-model` / `--ml-filter-mode` / `--ml-threshold` CLI 参数

## 4. Attention

- 不支持 `--symbol` 单股回测（STRATEGIES 字典中值为 None），仅支持 `--portfolio` / `--scan` / `--scan-only`
- 无 ML 模型时自动退化为 V4（`ml_score` 全 0.5），不会报错中断
- 模型文件自动版本命名：`models/b1v4_ml_v{N}.txt` + `b1v4_ml_v{N}_metadata.json`
- 训练数据来自 V4 基准回测的**清仓交易**（排除部分卖出），标签基于实际 `pnl_pct`
- B1 子条件特征（7 个）从 V4 信号字典中获取，V4 信号字典不含这些键时默认为 0
- `chip_spread` 特征从 V4 信号字典的 `chip_spread` 字段获取，非 ML 模块独立计算
- `--ml-filter-mode` 和 `--ml-threshold` CLI 参数会透传到 `preload_all_signals` 和策略函数的 `params` 字典
