# CLI 使用指南

## 1. Purpose

说明 `main.py` 的所有命令行参数、运行模式及其与策略行为的关系。

## 2. How it Works

### 策略总览

| 策略 key | 策略名称 | 支持模式 |
|----------|---------|---------|
| `kdj` | KDJ 金叉死叉 | 单股回测 |
| `huangbai` | 黄白线 B1（默认） | 组合模拟 / 扫描+回测 / 仅扫描 / 单股回测 |
| `huangbai_v2` | 黄白线 B1 + 大盘MACD | 组合模拟 / 扫描+回测 / 仅扫描 / 单股回测 |
| `huangbai_v3` | 黄白线 B1 V3 | 组合模拟 / 扫描+回测 / 仅扫描 / 单股回测 |
| `huangbai_v4` | 黄白线 B1 V4（无金叉+动能过滤） | 组合模拟 / 扫描+回测 / 仅扫描 / 单股回测 |
| `huangbai_v4_multi` | 黄白线 B1 V4 多仓（不限仓位+每日买2只） | 组合模拟 / 扫描+回测 / 仅扫描 |
| `huangbai_v5` | 黄白线 B1 V5（战法退出） | 组合模拟 / 扫描+回测 / 仅扫描 / 单股回测 |
| `dongneng_zhuan` | 动能+砖 | 组合模拟（默认）/ 仅扫描 |
| `nxing_zhuan` | N型+砖 | 组合模拟（默认）/ 仅扫描 |
| `huangbai_b2` | B2 倍量柱 | 组合模拟（默认）/ 仅扫描 |
| `huangbai_b2_v2` | B2 V2 倍量柱（30日B1频次） | 组合模拟（默认）/ 仅扫描 |
| `perfect_b1` | 完美B1（V4+5种模式过滤） | 组合模拟（默认）/ 仅扫描 |
| `perfect_b1_v2` | 完美B1 V2（V4+11种个股模式过滤） | 组合模拟（默认）/ 仅扫描 |
| `perfect_b1_v2_multi` | 完美B1 V2多仓（不限仓位+每日买2只） | 组合模拟（默认）/ 仅扫描 |
| `perfect_b1_v2_buyall` | 完美B1 V2全仓（10亿资金+不限仓位+全量买入） | 组合模拟（默认）/ 仅扫描 |
| `nxing_b1` | N型B1选股 | 组合模拟（默认）/ 仅扫描 |
| `jinchai_b1` | 金叉B1选股 | 仅扫描（纯选股+K线图） |
| `huangbai_v4_ml` | V4 ML增强（V4 B1 + LightGBM三分类评分） | 组合模拟（默认）/ 仅扫描 |

### 运行模式

#### 黄白线系列（`huangbai` / `huangbai_v2` / `huangbai_v3`）

`--strategy huangbai`（默认策略，可省略）支持 4 种运行模式，通过参数组合选择：

| 模式 | 触发参数 | 说明 |
|------|----------|------|
| 组合级模拟 | `--portfolio` | 每周/月更新观察池，每日检查买卖信号，组合级仓位管理（100万/10只/每只10万）。推荐模式 |
| 全市场扫描+回测 | `--scan` 或省略 `--symbol` | 先扫描全市场选股，再对结果逐只独立回测（旧模式，不考虑组合约束） |
| 仅扫描选股 | `--scan-only` | 仅输出当前符合条件的选股结果，不进入回测 |
| 指定股票回测 | `--symbol <代码>` | 对指定股票运行 Backtrader 逐 bar 回测，可指定多只（空格分隔） |

#### 动能+砖（`dongneng_zhuan`）

默认直接进入组合级模拟（无需 `--portfolio`），支持 2 种运行模式：

| 模式 | 触发参数 | 说明 |
|------|----------|------|
| 组合级模拟 | 无需额外参数（默认） | 每日全市场扫描信号，T+1开盘买入，三级退出（10万/2只/每只5万） |
| 仅扫描选股 | `--scan` 或 `--scan-only` | 全市场扫描动能+金砖选股，按排名分数排序输出 |

注意：`dongneng_zhuan` 不支持 `--symbol` 单股回测、不需要 `--portfolio` 标志、不支持 `--stock-type` 参数。

#### N型+砖（`nxing_zhuan`）

默认直接进入组合级模拟（无需 `--portfolio`），支持 2 种运行模式：

| 模式 | 触发参数 | 说明 |
|------|----------|------|
| 组合级模拟 | 无需额外参数（默认） | 每日全市场扫描信号，T+1开盘买入（无分钟确认），五级退出（10万/2只/每只5万） |
| 仅扫描选股 | `--scan` 或 `--scan-only` | 全市场扫描N型+金砖选股，按排名分数排序输出 |

注意：`nxing_zhuan` 不支持 `--symbol` 单股回测、不需要 `--portfolio` 标志。

#### B2 倍量柱（`huangbai_b2`）

默认直接进入组合级模拟（无需 `--portfolio`），支持 2 种运行模式：

| 模式 | 触发参数 | 说明 |
|------|----------|------|
| 组合级模拟 | 无需额外参数（默认） | 前日B1 + 当日倍量柱入场，大盘MACD过滤，标准六级退出（100万/10只/每只10万） |
| 仅扫描选股 | `--scan` 或 `--scan-only` | 全市场扫描B2倍量柱选股，按缩量排序输出 |

注意：`huangbai_b2` 不支持 `--symbol` 单股回测，不需要 `--portfolio` 标志。

#### N型B1选股（`nxing_b1`）

默认直接进入组合级模拟（无需 `--portfolio`），支持 2 种运行模式：

| 模式 | 触发参数 | 说明 |
|------|----------|------|
| 组合级模拟 | 无需额外参数（默认） | 60日N型B1结构筛选 + T+1开盘买入 + 六级退出（100万/10只/每只10万） |
| 仅扫描选股 | `--scan-only` | 全市场扫描N型B1选股 + K线图 + T+3胜率统计 |

注意：`nxing_b1` 不支持 `--symbol` 单股回测，不需要 `--portfolio` 标志。

#### 金叉B1选股（`jinchai_b1`）

仅支持选股扫描模式（纯选股，不做买卖操作）：

| 模式 | 触发参数 | 说明 |
|------|----------|------|
| 仅扫描选股 | 无需额外参数（默认） | 全市场扫描金叉+B1选股 + K线图 + T+5胜率统计 |

注意：`jinchai_b1` 不支持 `--symbol` 单股回测、不支持 `--portfolio` 组合模拟。无需 `--scan-only` 标志（默认即扫描）。`--start / --end` 限定B1信号日期范围。

#### V4 ML增强（`huangbai_v4_ml`）

默认直接进入组合级模拟（无需 `--portfolio`），支持 2 种运行模式：

| 模式 | 触发参数 | 说明 |
|------|----------|------|
| 组合级模拟 | 无需额外参数（默认） | V4 B1信号 + LightGBM评分，soft模式排序优先买入高分候选，hard模式拒绝低分信号（100万/10只/每只10万） |
| 仅扫描选股 | `--scan` 或 `--scan-only` | 全市场V4 B1扫描 + ML评分排序输出 |

注意：`huangbai_v4_ml` 不支持 `--symbol` 单股回测。首次使用前需训练模型：`python train_b1v4_ml.py --train`。无模型文件时自动退化为纯V4策略。

### 完整参数表

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `--strategy` | str | `huangbai` | 策略选择：`kdj` / `huangbai` / `huangbai_v2` / `huangbai_v3` / `huangbai_v4` / `huangbai_v4_multi` / `huangbai_v5` / `dongneng_zhuan` / `nxing_zhuan` / `jinzhuan` / `huangbai_b2` / `huangbai_b2_v2` / `perfect_b1` / `perfect_b1_v2` / `perfect_b1_v2_multi` / `perfect_b1_v2_buyall` / `nxing_b1` / `jinchai_b1` / `zstock_b1` / `zstock_b1_buyall` / `huangbai_v4_ml` |
| `--symbol` | str[] | 无 | 股票代码，可多只（空格分隔）。不指定且无 `--scan` 时默认使用 `config.py` 中的 `DEFAULT_STOCKS` |
| `--start` | str | `2023-01-01` | 回测/模拟起始日期 |
| `--end` | str | `2025-12-31` | 回测/模拟结束日期 |
| `--cash` | float | `100000` | 初始资金（仅单股回测模式生效） |
| `--stock-type` | str | `main` | 板块类型：`main`（主板）/ `tech`（科创板/创业板）。对 `dongneng_zhuan` 无效（板块类型从股票代码自动推断） |
| `--scan` | flag | - | 强制全市场扫描（忽略 `--symbol`） |
| `--scan-only` | flag | - | 仅扫描选股，不回测 |
| `--portfolio` | flag | - | 组合级模拟模式（仅 `huangbai` 系列需要，`dongneng_zhuan` 默认即为组合模拟） |
| `--no-plot` | flag | - | 禁用回测结果绘图（仅指定股票回测模式有图） |
| `--chart` | flag | - | 组合模拟完成后生成交易 K 线图（保存到 `charts/` 目录）。支持 V1/V2/V3 `--portfolio`、`dongneng_zhuan`、`nxing_zhuan`、`huangbai_b2`、`perfect_b1`、`nxing_b1` 等组合模拟模式 |
| `--eval` | flag | - | 组合级模拟完成后生成 5 维度评测 HTML 报告（D1 最大回撤/D2 夏普卡玛/D3 月度分布/D4 参数鲁棒/D5 佣金后利润，A-F 等级制）。报告保存到 `logs/eval_{tag}_{timestamp}.html`。详见 [评测系统文档](evaluator-backtest.md) |
| `--ml-model` | str | 无 | ML 模型文件路径（仅 `huangbai_v4_ml` 策略生效）。不指定时自动加载 `models/` 下最新模型 |
| `--ml-filter-mode` | str | `soft` | ML 过滤模式（仅 `huangbai_v4_ml`）：`soft` 仅排序优先买入高分候选，`hard` 拒绝低分信号 |
| `--ml-threshold` | float | `0.4` | ML 硬过滤阈值（仅 `--ml-filter-mode hard` 时生效），大盈利概率低于此值的信号被拒绝 |
| `--update-qfq-cache` | flag | - | 批量更新前复权缓存（需要网络）。不指定 `--update-qfq-codes` 时自动扫描通达信目录获取全市场股票列表 |
| `--update-qfq-codes` | str[] | 无 | 指定更新前复权缓存的股票代码，需配合 `--update-qfq-cache` 使用 |

### 常用命令示例

#### 黄白线 B1 策略（默认）

```bash
# 组合级模拟（推荐）
python main.py --portfolio

# 组合级模拟 + 生成交易K线图
python main.py --portfolio --chart

# 组合级模拟 + 完整参数
python main.py --strategy huangbai --portfolio --start 2023-01-01 --end 2025-12-31

# 组合级模拟 + 科创板参数
python main.py --portfolio --start 2024-06-01 --end 2025-12-31 --stock-type tech

# 全市场扫描，仅看选股结果
python main.py --scan-only

# 全市场扫描后逐只回测（旧模式）
python main.py --scan

# 指定股票回测
python main.py --symbol 002475

# 多只股票回测
python main.py --symbol 002475 600036 000001

# 指定股票回测 + 科创板参数 + 自定义区间
python main.py --symbol 688981 --stock-type tech --start 2024-01-01 --end 2025-06-30

# V2 策略：组合级模拟（含大盘MACD过滤）
python main.py --strategy huangbai_v2 --portfolio

# V2 策略：组合级模拟 + K线图
python main.py --strategy huangbai_v2 --portfolio --chart

# V2 策略：指定股票回测（自动加载大盘指数数据）
python main.py --strategy huangbai_v2 --symbol 002475

# 自定义资金回测
python main.py --symbol 600036 --cash 500000
```

#### 动能+砖策略

```bash
# 组合级模拟（默认模式，无需 --portfolio）
python main.py --strategy dongneng_zhuan

# 自定义回测区间
python main.py --strategy dongneng_zhuan --start 2024-06-01 --end 2025-12-31

# 全市场扫描选股
python main.py --strategy dongneng_zhuan --scan

# 仅扫描选股
python main.py --strategy dongneng_zhuan --scan-only

# 组合级模拟 + K线图
python main.py --strategy dongneng_zhuan --chart
```

#### N型+砖策略

```bash
# 组合级模拟（默认模式，无需 --portfolio）
python main.py --strategy nxing_zhuan

# 全市场扫描选股
python main.py --strategy nxing_zhuan --scan-only

# 组合级模拟 + K线图
python main.py --strategy nxing_zhuan --chart
```

#### V4 多仓策略

```bash
# 组合级模拟（不限仓位，每日最多买入2只）
python main.py --strategy huangbai_v4_multi --portfolio --chart

# 自定义回测区间
python main.py --strategy huangbai_v4_multi --portfolio --start 2024-01-01 --end 2025-12-31

# 全市场扫描选股
python main.py --strategy huangbai_v4_multi --scan-only
```

#### B2 倍量柱策略

```bash
# 组合级模拟（默认模式，无需 --portfolio）
python main.py --strategy huangbai_b2

# 自定义回测区间
python main.py --strategy huangbai_b2 --start 2024-06-01 --end 2025-12-31

# 全市场扫描选股
python main.py --strategy huangbai_b2 --scan-only

# 组合级模拟 + K线图
python main.py --strategy huangbai_b2 --chart
```

#### 完美B1策略

```bash
# 组合级模拟（默认模式，无需 --portfolio）
python main.py --strategy perfect_b1

# 自定义回测区间
python main.py --strategy perfect_b1 --start 2024-06-01 --end 2025-12-31

# 全市场扫描选股
python main.py --strategy perfect_b1 --scan-only

# 组合级模拟 + K线图
python main.py --strategy perfect_b1 --chart
```

#### 完美B1 V2多仓策略

```bash
# 组合级模拟（不限仓位，每日最多买入2只）
python main.py --strategy perfect_b1_v2_multi --portfolio --chart

# 自定义回测区间
python main.py --strategy perfect_b1_v2_multi --portfolio --start 2024-01-01 --end 2025-12-31

# 全市场扫描选股
python main.py --strategy perfect_b1_v2_multi --scan-only
```

#### 完美B1 V2全仓策略

```bash
# 组合级模拟（10亿资金，不限仓位，买入所有候选）
python main.py --strategy perfect_b1_v2_buyall --portfolio --chart

# 自定义回测区间
python main.py --strategy perfect_b1_v2_buyall --portfolio --start 2024-01-01 --end 2025-12-31

# 全市场扫描选股
python main.py --strategy perfect_b1_v2_buyall --scan-only
```

#### N型B1选股

```bash
# 组合级模拟（默认模式，无需 --portfolio）
python main.py --strategy nxing_b1

# 自定义回测区间
python main.py --strategy nxing_b1 --start 2024-01-01 --end 2025-05-01

# 组合级模拟 + K线图
python main.py --strategy nxing_b1 --chart

# 仅扫描选股 + K线图
python main.py --strategy nxing_b1 --scan-only
```

#### 金叉B1选股

```bash
# 全市场扫描（默认即扫描，无需 --scan-only）
python main.py --strategy jinchai_b1 --scan-only

# 指定日期区间
python main.py --strategy jinchai_b1 --scan-only --start 2024-01-01 --end 2025-05-01
```

#### V4 ML增强策略

```bash
# 训练模型（首次使用前必须执行）
python train_b1v4_ml.py --train

# 仅构建训练数据（不训练）
python train_b1v4_ml.py --build-data

# 指定训练区间
python train_b1v4_ml.py --train --start 2023-01-01 --end 2025-12-31

# 查看特征重要性
python train_b1v4_ml.py --feature-importance

# 评估已有模型
python train_b1v4_ml.py --eval

# ML增强回测（默认soft模式，优先买入高分候选）
python main.py --strategy huangbai_v4_ml

# ML增强回测 + K线图 + 评测
python main.py --strategy huangbai_v4_ml --chart --eval

# 指定时间段回测
python main.py --strategy huangbai_v4_ml --start 2024-01-01 --end 2025-06-30

# 指定时间段 + K线图 + 评测
python main.py --strategy huangbai_v4_ml --start 2024-01-01 --end 2025-06-30 --chart --eval

# 硬过滤模式（拒绝低分信号）
python main.py --strategy huangbai_v4_ml --ml-filter-mode hard --ml-threshold 0.5

# 指定模型文件
python main.py --strategy huangbai_v4_ml --ml-model models/b1v4_ml_v1.txt

# 全市场扫描选股
python main.py --strategy huangbai_v4_ml --scan-only
```

#### 评测报告

```bash
# V2 策略组合模拟 + 评测报告
python main.py --strategy huangbai_v2 --portfolio --eval

# ZStock B1 全仓 + 评测 + K线图
python main.py --strategy zstock_b1_buyall --portfolio --chart --eval

# 动能砖策略 + 评测
python main.py --strategy dongneng_zhuan --eval

# 完美B1 V2 + 评测
python main.py --strategy perfect_b1_v2 --eval
```

#### 前复权缓存管理

```bash
# 全市场更新前复权缓存（首次运行，需要网络）
python main.py --update-qfq-cache

# 指定股票更新缓存
python main.py --update-qfq-cache --update-qfq-codes 300733 600036
```

#### KDJ 策略

```bash
# 指定股票回测
python main.py --strategy kdj --symbol 600036
```

### 参数与策略行为的关系

#### 通用参数

- **`--start / --end`**：所有模式通用。组合模拟模式下用于截取交易日历和信号数据范围；扫描模式下不影响扫描（扫描始终取最新 bar），仅影响后续逐只回测区间
- **`--cash`**：仅在非组合模式下控制单股回测初始资金
- **`--no-plot`**：仅指定股票回测模式有效，组合模拟和扫描模式无绘图
- **`--chart`**：仅黄白线系列 `--portfolio` 组合模拟模式有效，模拟完成后为每只交易过的股票生成 K 线图（含买卖标记、止损线、成本线），保存到 `charts/` 目录

#### 黄白线系列参数

- **`--stock-type`**：影响振幅阈值（主板 5% / 科创板 8%）、中阳判断（主板 5% / 科创板 10%）、涨停板计算（主板 10% / 科创板 20%）
- **`--portfolio`**：资金/仓位参数来自 `config.py`（`PORTFOLIO_INITIAL_CASH=100万`、`PORTFOLIO_MAX_POSITIONS=10`、`PORTFOLIO_PER_POSITION=10万`），不受 `--cash` 影响
- **`huangbai_v2` 大盘MACD**：V2 策略自动加载上证指数数据计算 MACD。组合模拟模式下空头日只卖不买；单股回测中大盘数据作为第二数据源；扫描模式下大盘空头时返回空结果

#### 动能+砖参数

- **无需 `--portfolio`**：默认即组合级模拟
- **不支持 `--symbol`**：仅支持全市场扫描模式
- **`--stock-type` 无效**：板块类型从股票代码自动推断（30xx/68xx → 创业板/科创板，其余 → 主板）
- **资金/仓位**：`DNZH_INITIAL_CASH=10万`、`DNZH_MAX_POSITIONS=2`、`DNZH_PER_POSITION=5万`，来自 `config.py`
- **退出参数**：`DNZH_T_PLUS_N=2`（不拉升天数）、`DNZH_MAX_HOLD_DAYS=5`（盈利后最大持仓）、`DNZH_PROFIT_PCT=5.0`（脱离成本区%），均可在 `config.py` 调整

#### N型+砖参数

- **无需 `--portfolio`**：默认即组合级模拟
- **不支持 `--symbol`**：仅支持全市场扫描模式
- **资金/仓位**：`NXZH_INITIAL_CASH=10万`、`NXZH_MAX_POSITIONS=2`、`NXZH_PER_POSITION=5万`，来自 `config.py`
- **退出参数**：`NXZH_T_PLUS_N=2`（不拉升天数）、`NXZH_MAX_HOLD_DAYS=6`（盈利后最大持仓）、`NXZH_PROFIT_PCT=5.0`（脱离成本区%），均可在 `config.py` 调整
- **入场模式**：`NXZH_MINUTE_ENTRY_ENABLED=False`，T+1日线开盘买入（无分钟确认），与动能砖的核心区别

#### V4 多仓参数

- **不支持 `--symbol`**：仅支持组合模拟或扫描模式
- **`--stock-type`**：同黄白线系列，影响振幅阈值、中阳判断、涨停板计算
- **资金/仓位**：`V4_MULTI_INITIAL_CASH=100万`、不限持仓数量（`max_positions=999`）、`V4_MULTI_PER_POSITION=10万`，来自 `config.py`
- **每日买入上限**：`V4_MULTI_MAX_DAILY_BUYS=2`，PortfolioSimulator 的 `max_daily_buys` 参数
- **退出逻辑**：与 V4 完全相同（六级退出 + 大盘转空卖1/2 + 盈利跌破黄线清仓）
- **大盘MACD过滤**：同V4，空头日只卖不买

#### B2 倍量柱参数

- **无需 `--portfolio`**：默认即组合级模拟
- **不支持 `--symbol`**：仅支持全市场扫描模式
- **`--stock-type`**：同黄白线系列，影响振幅阈值、中阳判断、涨停板计算
- **资金/仓位**：复用 `PORTFOLIO_INITIAL_CASH=100万`、`PORTFOLIO_MAX_POSITIONS=10`、`PORTFOLIO_PER_POSITION=10万`，来自 `config.py`
- **退出逻辑**：复用 PortfolioSimulator 标准六级退出（止损→T+N→盈利100%→半仓持股→涨停卖半→中阳卖1/3）
- **大盘MACD过滤**：同V2，空头日只卖不买

#### 完美B1参数

- **无需 `--portfolio`**：默认即组合级模拟
- **不支持 `--symbol`**：仅支持全市场扫描模式
- **`--stock-type`**：同黄白线系列，影响振幅阈值、中阳判断、涨停板计算
- **资金/仓位**：复用 `PORTFOLIO_INITIAL_CASH=100万`、`PORTFOLIO_MAX_POSITIONS=10`、`PORTFOLIO_PER_POSITION=10万`，来自 `config.py`
- **退出逻辑**：复用 PortfolioSimulator 标准六级退出（同V4）
- **大盘MACD过滤**：同V2，空头日只卖不买
- **核心差异**：在V4 B1基础上叠加5种模式质量过滤（典型单波/白线不死叉/多波N型/跌破反转/大牛市），过滤掉短期B1

#### 完美B1 V2多仓参数

- **不支持 `--symbol`**：仅支持组合模拟或扫描模式
- **`--stock-type`**：同黄白线系列，影响振幅阈值、中阳判断、涨停板计算
- **资金/仓位**：`PB1V2_MULTI_INITIAL_CASH=100万`、不限持仓数量（`max_positions=999`）、`PB1V2_MULTI_PER_POSITION=10万`，来自 `config.py`
- **每日买入上限**：`PB1V2_MULTI_MAX_DAILY_BUYS=2`，PortfolioSimulator 的 `max_daily_buys` 参数
- **退出逻辑**：与完美B1 V2完全相同（标准六级退出）
- **大盘MACD过滤**：同V2，空头日只卖不买

#### 完美B1 V2全仓参数

- **不支持 `--symbol`**：仅支持组合模拟或扫描模式
- **`--stock-type`**：同黄白线系列，影响振幅阈值、中阳判断、涨停板计算
- **资金/仓位**：`PB1V2_BUYALL_INITIAL_CASH=10亿`、不限持仓数量（`max_positions=9999`）、`PB1V2_BUYALL_PER_POSITION=10万`，来自 `config.py`
- **每日买入上限**：`PB1V2_BUYALL_MAX_DAILY_BUYS=9999`，买入所有符合条件的候选股票
- **退出逻辑**：与完美B1 V2完全相同（标准六级退出）
- **大盘MACD过滤**：同V2，空头日只卖不买

#### N型B1参数

- **无需 `--portfolio`**：默认即组合级模拟
- **不支持 `--symbol`**：仅支持全市场扫描或组合模拟
- **`--stock-type`**：同黄白线系列，影响振幅阈值、中阳判断、涨停板计算
- **资金/仓位**：`NXB1_INITIAL_CASH=100万`、`NXB1_MAX_POSITIONS=10`、`NXB1_PER_POSITION=10万`，来自 `config.py`
- **退出逻辑**：复用 PortfolioSimulator 标准六级退出（止损→跌破黄线→T+N→盈利100%→半仓持股→动量持股）

#### V4 ML增强参数

- **无需 `--portfolio`**：默认即组合级模拟
- **不支持 `--symbol`**：仅支持全市场扫描或组合模拟
- **`--stock-type`**：同黄白线系列，影响振幅阈值、中阳判断、涨停板计算
- **资金/仓位**：复用 `PORTFOLIO_INITIAL_CASH=100万`、`PORTFOLIO_MAX_POSITIONS=10`、`PORTFOLIO_PER_POSITION=10万`，来自 `config.py`
- **退出逻辑**：复用 PortfolioSimulator 标准六级退出（同V4）
- **大盘MACD过滤**：同V4，空头日只卖不买
- **ML模型**：LightGBM 三分类（大亏损/小幅波动/大盈利），取大盈利概率作为 `ml_score`
- **ML过滤模式**：`--ml-filter-mode soft`（默认，保留所有V4 B1信号但优先买入高分候选）/ `hard`（拒绝 `ml_score < threshold` 的信号）
- **无模型退化**：`models/` 目录下无模型文件时，策略自动退化为纯V4（`ml_score=0.5`）
- **训练脚本**：独立的 `train_b1v4_ml.py`，不通过 `main.py` 调用
- **特征数**：31个（动量指标8 + 距离/趋势8 + 振幅/量能8 + B1子条件7）

## 3. Relevant Code Modules

- `main.py` - CLI 入口，`parse_args()` 参数定义与模式分发逻辑
- `config.py` - 默认参数值（`DEFAULT_START_DATE`、`DEFAULT_END_DATE`、`DEFAULT_STOCKS`、`STOCK_TYPE` 等）及各策略资金/仓位参数

## 4. Attention

- `--strategy huangbai` 为默认策略，命令行中可省略
- `--scan` 与 `--symbol` 互斥：`--scan` 优先级更高，会忽略 `--symbol`
- `--scan-only` 隐含 `--scan`，不需要同时指定两者
- `--portfolio` 对 `huangbai` 系列生效；`dongneng_zhuan` 默认即为组合模拟，无需此标志
- 组合模拟分两阶段执行：阶段 1 预加载全市场信号（约 45-55 秒），阶段 2 逐日模拟
- `dongneng_zhuan` 不支持单股 Backtrader 回测（STRATEGIES 字典中值为 None）
- `nxing_zhuan` 同样不支持单股回测（STRATEGIES 字典中值为 None）
- `huangbai_b2` 同样不支持单股回测（STRATEGIES 字典中值为 None）
- `huangbai_b2_v2` 同样不支持单股回测（STRATEGIES 字典中值为 None）
- `perfect_b1` 同样不支持单股回测（STRATEGIES 字典中值为 None）
- `nxing_b1` 同样不支持单股回测（STRATEGIES 字典中值为 None）
- `jinzhuan` 同样不支持单股回测（STRATEGIES 字典中值为 None）
- `jinchai_b1` 同样不支持单股回测（STRATEGIES 字典中值为 None，仅纯扫描+图表）
- `huangbai_v4_multi` 同样不支持单股回测（STRATEGIES 字典中值为 None），需配合 `--portfolio` 使用
- `perfect_b1_v2_multi` 同样不支持单股回测（STRATEGIES 字典中值为 None），需配合 `--portfolio` 使用
- `perfect_b1_v2_buyall` 同样不支持单股回测（STRATEGIES 字典中值为 None），需配合 `--portfolio` 使用
- `huangbai_v4_ml` 同样不支持单股回测（STRATEGIES 字典中值为 None）。首次使用前需运行 `python train_b1v4_ml.py --train` 训练模型，无模型时退化为纯V4策略
