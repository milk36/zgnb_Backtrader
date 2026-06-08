# 策略层：完美B1 V2多仓策略

## 1. Purpose

完美B1 V2 的多仓变体，信号计算完全复用完美B1 V2，仅调整组合模拟参数：不限制最大持仓数量，每日最多买入前 N 只候选股票。用于验证"放宽持仓上限、分散买入"对完美B1 V2 策略收益的影响。

## 2. How it Works

### 与完美B1 V2 的核心差异

| 对比项 | 完美B1 V2 | 完美B1 V2多仓 |
|--------|-----------|---------------|
| 信号计算 | 完美B1 V2 原生 | **完全复用完美B1 V2**（代理调用） |
| max_positions | 10 | **999**（不限制） |
| max_daily_buys | 1（默认） | **2**（每日最多买入前 2 只） |
| 退出逻辑 | 标准六级退出 | 标准六级退出（**完全相同**） |
| strategy_tag | `[完美B1V2]` | `[完美B1V2多仓]` |

### 完美B1 V2 信号架构概述（被代理的核心逻辑）

完美B1 V2 采用 **必要条件门控 + 四通道OR识别** 架构：

```
完美B1 = V4_B1 & 建仓波存在 & (通道A|通道B|通道C|通道D) & ~预警生效
```

- **必要条件**：近期存在带量拉升的建仓波（放量阳线密度+区间涨幅+倍量柱，多窗口OR检测）
- **通道A 缩量极致型**：`shrink<30%` & (超卖 OR 贴近均线)
- **通道B 白线不死叉型**：`white>=yellow` 30天 & `J<20` & 洗盘充分(>=3天跌破白线)
- **通道C 极端超卖型**：`(J<0 | RSI<15)` & `shrink<35%`
- **通道D 大牛市型**：`40日涨幅>80%` & `shrink<30%` & 贴近白线<8% & `J<15`
- **预警过滤**：`shrink>35%` & (回调深度<8% | 5日振幅<2.5%)，通道B豁免
- **排序**：通道优先级(A=1 > C=2 > D=3 > B=4) x 10000 - final_score
- **5维评分**保留用于排序和日志，不再作为门控条件

### 架构：纯代理模式

策略文件 `perfect_b1_v2_multi_strategy.py` 仅包含两个代理函数，直接导入完美B1 V2 的 `preload_all_signals` 和 `scan_all`，参数透传，无额外计算逻辑。

### PortfolioSimulator 集成

- `max_positions=999`：实际不限制持仓数量（受现金约束自然受限）
- `max_daily_buys=2`：`_check_entries()` 从排序后的候选列表中循环取前 2 只买入
- `strategy_tag="[完美B1V2多仓]"`：不包含 `B1V4` 子串，命中标准六级退出路径（止损 -> T+N没涨 -> 盈利100%清仓 -> 半仓持股 -> 涨停卖1/2 -> 中阳卖1/3）

### 配置参数

| 参数 | 值 | 说明 |
|------|----|------|
| `PB1V2_MULTI_INITIAL_CASH` | 1,000,000 | 100万初始资金 |
| `PB1V2_MULTI_PER_POSITION` | 100,000 | 每只10万 |
| `PB1V2_MULTI_MAX_DAILY_BUYS` | 2 | 每日最多买入2只 |

### 运行命令

- 组合模拟 + K线图：`python main.py --strategy perfect_b1_v2_multi --portfolio --chart`
- 仅扫描：`python main.py --strategy perfect_b1_v2_multi --scan-only`
- 不支持 `--symbol` 单股回测

## 3. Relevant Code Modules

- `src/strategies/perfect_b1_v2_multi_strategy.py` — 多仓策略文件（代理函数）
- `src/strategies/perfect_b1_v2_strategy.py` — 完美B1 V2 策略主文件（`_compute_channel_signals()` 四通道检测 + `_compute_dynamic_process_scores()` 5维评分）
- `src/strategies/huangbai_b1_v4_strategy.py` — V4 策略（被完美B1 V2 包装的底层信号）
- `src/engine/portfolio_simulator.py` — PortfolioSimulator（`max_daily_buys` 参数 + `_check_entries` 循环买入）
- `config.py` — `PB1V2_MULTI_*` 参数、`HUANGBAI_*` 参数、`DNZH_MIN_MARKET_CAP`
- `main.py` — `perfect_b1_v2_multi` 策略注册及 `--portfolio` 模式分发

## 4. Attention

- 不支持 `--symbol` 单股回测（STRATEGIES 字典中值为 None），仅支持 `--portfolio` / `--scan` / `--scan-only`
- B1 逻辑变更只需修改完美B1 V2 策略文件，多仓版自动同步（纯代理调用）
- `max_daily_buys` 参数为 PortfolioSimulator 通用参数（默认值为 1），其他策略如需多笔买入可复用
- strategy_tag 为 `[完美B1V2多仓]`，不含 `B1V4` 子串，故 V4 专属退出逻辑（大盘转空卖1/2、盈利跌破黄线清仓）不生效
- 完美B1 V2 的 `vol_expand_ok` 被覆盖为全True（极致缩量与前期放量互斥），V4 原始的 vol_expand_ok 过滤不生效
