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
| `dongneng_zhuan` | 动能+砖 | 组合模拟（默认）/ 仅扫描 |

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

### 完整参数表

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `--strategy` | str | `huangbai` | 策略选择：`kdj` / `huangbai` / `huangbai_v2` / `huangbai_v3` / `dongneng_zhuan` |
| `--symbol` | str[] | 无 | 股票代码，可多只（空格分隔）。不指定且无 `--scan` 时默认使用 `config.py` 中的 `DEFAULT_STOCKS` |
| `--start` | str | `2023-01-01` | 回测/模拟起始日期 |
| `--end` | str | `2025-12-31` | 回测/模拟结束日期 |
| `--cash` | float | `100000` | 初始资金（仅单股回测模式生效） |
| `--stock-type` | str | `main` | 板块类型：`main`（主板）/ `tech`（科创板/创业板）。对 `dongneng_zhuan` 无效（板块类型从股票代码自动推断） |
| `--scan` | flag | - | 强制全市场扫描（忽略 `--symbol`） |
| `--scan-only` | flag | - | 仅扫描选股，不回测 |
| `--portfolio` | flag | - | 组合级模拟模式（仅 `huangbai` 系列需要，`dongneng_zhuan` 默认即为组合模拟） |
| `--no-plot` | flag | - | 禁用回测结果绘图（仅指定股票回测模式有图） |
| `--chart` | flag | - | 组合模拟完成后生成交易 K 线图（保存到 `charts/` 目录）。仅 V1/V2/V3 `--portfolio` 模式有效 |

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
