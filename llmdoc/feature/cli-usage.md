# CLI 使用指南

## 1. Purpose

说明 `main.py` 的所有命令行参数、运行模式及其与策略行为的关系。

## 2. How it Works

### 运行模式

`--strategy huangbai`（默认策略，可省略）支持 4 种运行模式，通过参数组合选择：

| 模式 | 触发参数 | 说明 |
|------|----------|------|
| 组合级模拟 | `--portfolio` | 每周一更新观察池，每日检查买卖信号，组合级仓位管理（100万/10只/每只10万）。推荐模式 |
| 全市场扫描+回测 | `--scan` 或省略 `--symbol` | 先扫描全市场选股，再对结果逐只独立回测（旧模式，不考虑组合约束） |
| 仅扫描选股 | `--scan-only` | 仅输出当前符合条件的选股结果，不进入回测 |
| 指定股票回测 | `--symbol <代码>` | 对指定股票运行 Backtrader 逐 bar 回测，可指定多只（空格分隔） |

### 完整参数表

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `--strategy` | str | `huangbai` | 策略选择：`kdj`（KDJ 金叉死叉）/ `huangbai`（黄白线 B1）/ `huangbai_v2`（黄白线 B1 + 大盘MACD过滤） |
| `--symbol` | str[] | 无 | 股票代码，可多只（空格分隔）。不指定且无 `--scan` 时默认使用 `config.py` 中的 `DEFAULT_STOCKS` |
| `--start` | str | `2023-01-01` | 回测/模拟起始日期 |
| `--end` | str | `2025-12-31` | 回测/模拟结束日期 |
| `--cash` | float | `100000` | 初始资金（仅单股回测模式生效） |
| `--stock-type` | str | `main` | 板块类型：`main`（主板）/ `tech`（科创板/创业板） |
| `--scan` | flag | - | 强制全市场扫描（忽略 `--symbol`） |
| `--scan-only` | flag | - | 仅扫描选股，不回测 |
| `--portfolio` | flag | - | 组合级模拟模式 |
| `--no-plot` | flag | - | 禁用回测结果绘图（仅指定股票回测模式有图） |

### 常用命令示例

```bash
# 组合级模拟（推荐）
python main.py --portfolio

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

# KDJ 策略回测
python main.py --strategy kdj --symbol 600036

# 自定义资金回测
python main.py --symbol 600036 --cash 500000

# V2 策略：组合级模拟（含大盘MACD过滤）
python main.py --strategy huangbai_v2 --portfolio

# V2 策略：全市场扫描
python main.py --strategy huangbai_v2 --scan

# V2 策略：仅扫描选股
python main.py --strategy huangbai_v2 --scan-only

# V2 策略：指定股票回测（自动加载大盘指数数据）
python main.py --strategy huangbai_v2 --symbol 002475
```

### 参数与策略行为的关系

- **`--stock-type`**：影响振幅阈值（主板 5% / 科创板 8%）、中阳判断（主板 5% / 科创板 10%）、涨停板计算（主板 10% / 科创板 20%）
- **`--portfolio`**：资金/仓位参数来自 `config.py`（`PORTFOLIO_INITIAL_CASH=100万`、`PORTFOLIO_MAX_POSITIONS=10`、`PORTFOLIO_PER_POSITION=10万`），不受 `--cash` 影响。对 `huangbai` 和 `huangbai_v2` 均生效
- **`huangbai_v2` 大盘MACD**：V2 策略自动加载上证指数数据计算 MACD。组合模拟模式下空头日只卖不买；单股回测中大盘数据作为第二数据源；扫描模式下大盘空头时返回空结果
- **`--cash`**：仅在非组合模式下控制单股回测初始资金
- **`--start / --end`**：所有模式通用。组合模拟模式下用于截取交易日历和信号数据范围；扫描模式下 `--start / --end` 不影响扫描（扫描始终取最新 bar），仅影响后续逐只回测区间
- **`--no-plot`**：仅指定股票回测模式有效，组合模拟和扫描模式无绘图

## 3. Relevant Code Modules

- `main.py` - CLI 入口，`parse_args()` 参数定义与模式分发逻辑
- `config.py` - 默认参数值（`DEFAULT_START_DATE`、`DEFAULT_END_DATE`、`DEFAULT_STOCKS`、`STOCK_TYPE` 等）

## 4. Attention

- `--strategy huangbai` 为默认策略，命令行中可省略
- `--scan` 与 `--symbol` 互斥：`--scan` 优先级更高，会忽略 `--symbol`
- `--scan-only` 隐含 `--scan`，不需要同时指定两者
- `--portfolio` 对 `huangbai` 和 `huangbai_v2` 均生效（`main.py` 分别判断 `HuangBaiB1Strategy` 和 `HuangBaiB1V2Strategy`）
- 组合模拟分两阶段执行：阶段 1 预加载全市场信号（约 55 秒），阶段 2 逐日模拟
