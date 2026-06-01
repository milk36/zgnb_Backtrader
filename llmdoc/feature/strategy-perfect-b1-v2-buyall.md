# 策略层：完美B1 V2全仓策略

## 1. Purpose

完美B1 V2 的全仓变体，信号计算完全复用完美B1 V2，仅调整组合模拟参数：不限制总资金和最大持仓数量，每日买入所有符合条件的候选股票。用于验证"无限资金、无限持仓、全量买入"对完美B1 V2 策略的理论收益上限。

## 2. How it Works

### 与完美B1 V2 的核心差异

| 对比项 | 完美B1 V2 | 完美B1 V2全仓 |
|--------|-----------|---------------|
| 信号计算 | 完美B1 V2 原生 | **完全复用完美B1 V2**（代理调用） |
| initial_cash | 100万 | **10亿**（不限制） |
| max_positions | 10 | **9999**（不限制） |
| max_daily_buys | 1（默认） | **9999**（买入所有候选） |
| per_position_cash | 10万 | 10万（相同） |
| 退出逻辑 | 标准六级退出 | 标准六级退出（**完全相同**） |
| strategy_tag | `[完美B1V2]` | `[完美B1V2全仓]` |

### 架构：纯代理模式

策略文件 `perfect_b1_v2_buyall_strategy.py` 仅包含两个代理函数，直接导入完美B1 V2 的 `preload_all_signals` 和 `scan_all`，参数透传，无额外计算逻辑。

### PortfolioSimulator 集成

- `initial_cash=10亿`：模拟无限资金，现金不再是买入瓶颈
- `max_positions=9999`：不限制持仓数量
- `max_daily_buys=9999`：`_check_entries()` 买入排序后的全部候选股票
- `strategy_tag="[完美B1V2全仓]"`：不含 `B1V4` 子串，命中标准六级退出路径（止损 -> T+N没涨 -> 盈利100%清仓 -> 半仓持股 -> 涨停卖1/2 -> 中阳卖1/3）

### 配置参数

| 参数 | 值 | 说明 |
|------|----|------|
| `PB1V2_BUYALL_INITIAL_CASH` | 1,000,000,000 | 10亿初始资金 |
| `PB1V2_BUYALL_PER_POSITION` | 100,000 | 每只10万 |
| `PB1V2_BUYALL_MAX_DAILY_BUYS` | 9999 | 不限制每日买入数量 |

### 运行命令

- 组合模拟 + K线图：`python main.py --strategy perfect_b1_v2_buyall --portfolio --chart`
- 仅扫描：`python main.py --strategy perfect_b1_v2_buyall --scan-only`
- 不支持 `--symbol` 单股回测

## 3. Relevant Code Modules

- `src/strategies/perfect_b1_v2_buyall_strategy.py` — 全仓策略文件（代理函数）
- `src/strategies/perfect_b1_v2_strategy.py` — 完美B1 V2 策略主文件（信号计算实际来源）
- `src/engine/portfolio_simulator.py` — PortfolioSimulator（`max_daily_buys` 参数 + `_check_entries` 循环买入）
- `config.py` — `PB1V2_BUYALL_INITIAL_CASH`、`PB1V2_BUYALL_PER_POSITION`、`PB1V2_BUYALL_MAX_DAILY_BUYS`
- `main.py` — `perfect_b1_v2_buyall` 策略注册及 `--portfolio` 模式分发

## 4. Attention

- 不支持 `--symbol` 单股回测（STRATEGIES 字典中值为 None），仅支持 `--portfolio` / `--scan` / `--scan-only`
- B1 逻辑变更只需修改完美B1 V2 策略文件，全仓版自动同步（纯代理调用）
- strategy_tag 为 `[完美B1V2全仓]`，不含 `B1V4` 子串，故 V4 专属退出逻辑（大盘转空卖1/2、盈利跌破黄线清仓）不生效
- 10亿初始资金仅为模拟用，实际仅受 `per_position_cash`（每只10万）约束单只仓位
