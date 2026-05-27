# 策略层：黄白线B1策略 V4 多仓版

## 1. Purpose

V4 策略的多仓变体，信号计算完全复用 V4，仅调整组合模拟参数：不限制最大持仓数量，每日最多买入前 N 只候选股票。用于验证"放宽持仓上限、分散买入"对 V4 策略收益的影响。

## 2. How it Works

### 与 V4 的核心差异

| 对比项 | V4 | V4 多仓 |
|--------|----|----|
| 信号计算 | V4 原生 | **完全复用 V4** (`preload_all_signals` / `scan_all` 代理调用) |
| max_positions | 10 | **999**（不限制） |
| max_daily_buys | 1（默认） | **2**（每日最多买入前 2 只） |
| 退出逻辑 | 六级 + V4 专属 | 六级 + V4 专属（**完全相同**） |
| strategy_tag | `[B1V4]` | `[B1V4多仓]` |

### 架构：纯代理模式

策略文件 `huangbai_b1_v4_multi_strategy.py` 仅包含两个代理函数，直接导入 V4 的 `preload_all_signals` 和 `scan_all`，参数透传，无额外计算逻辑。

### PortfolioSimulator 集成

- `max_positions=999`：实际不限制持仓数量（受现金约束自然受限）
- `max_daily_buys=2`：`_check_entries()` 从排序后的候选列表中循环取前 2 只买入
- V4 专属退出逻辑（`"B1V4" in self._strategy_tag`）通过 `strategy_tag="[B1V4多仓]"` 命中（包含 "B1V4" 子串），生效：
  - 大盘 MACD 转空头 + 浮盈>=20% → 卖 1/2
  - 曾经浮盈>=20% + 跌破黄线 → 清仓

### 配置参数

| 参数 | 值 | 说明 |
|------|----|------|
| `V4_MULTI_INITIAL_CASH` | 1,000,000 | 100 万初始资金 |
| `V4_MULTI_PER_POSITION` | 100,000 | 每只 10 万 |
| `V4_MULTI_MAX_DAILY_BUYS` | 2 | 每日最多买入 2 只 |

## 3. Relevant Code Modules

- `src/strategies/huangbai_b1_v4_multi_strategy.py` - V4 多仓策略文件（代理函数）
- `src/strategies/huangbai_b1_v4_strategy.py` - V4 策略主文件（信号计算实际来源）
- `src/engine/portfolio_simulator.py` - PortfolioSimulator（`max_daily_buys` 参数 + `_check_entries` 循环买入）
- `config.py` - `V4_MULTI_INITIAL_CASH`、`V4_MULTI_PER_POSITION`、`V4_MULTI_MAX_DAILY_BUYS`
- `main.py` - `huangbai_v4_multi` 策略注册（STRATEGIES 字典值为 None）及 `--portfolio` 模式分发

## 4. Attention

- 不支持 `--symbol` 单股回测（STRATEGIES 字典中值为 None），仅支持 `--portfolio` / `--scan` / `--scan-only`
- V4 多仓的 `strategy_tag` 为 `[B1V4多仓]`，V4 专属退出逻辑通过 `"B1V4" in tag` 匹配，需确保 tag 字符串包含 `B1V4` 子串
- B1 逻辑变更只需修改 V4 策略文件，V4 多仓自动同步（纯代理调用）
- `max_daily_buys` 参数为 PortfolioSimulator 通用参数（默认值为 1），其他策略如需多笔买入可复用
