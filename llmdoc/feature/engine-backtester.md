# 引擎层：回测引擎 (Backtester) 与组合模拟器 (PortfolioSimulator)

## 1. Purpose

两种回测引擎：
- **Backtester**：单股 Backtrader Cerebro 封装，适用于指定股票的策略回测
- **PortfolioSimulator**：组合级日频模拟引擎，在回测区间内每周一扫描全市场、每日选股交易，适用于组合级策略验证

## 2. How it Works

### Backtester（单股回测）

封装 Backtrader `Cerebro` 的完整生命周期：初始化配置、数据注入、策略绑定、分析器挂载、执行回测、格式化报告输出与绘图。

#### 核心方法

| 方法 | 说明 |
|---|---|
| `add_feed(feed, name)` | 添加数据源（`cerebro.adddata`） |
| `add_strategy(cls, **kwargs)` | 绑定策略类 |
| `run() -> dict` | 挂载分析器、执行回测、返回报告字典 |
| `plot(**kwargs)` | 调用 `cerebro.plot()` |
| `print_report(report)` | 静态方法，格式化打印报告 |

#### 分析器配置

- `bt.analyzers.SharpeRatio` -> `report["sharpe"]`
- `bt.analyzers.DrawDown` -> `report["drawdown"]`
- `bt.analyzers.Returns` -> `report["returns"]`
- `bt.analyzers.TradeAnalyzer` -> `report["trades"]`

### PortfolioSimulator（组合级模拟）

自定义日频模拟引擎，不依赖 Backtrader。模拟真实交易流程：每周一更新周线多头观察池，每日检查金叉+B1买入条件，按缩量排序取最优，组合级仓位管理。

#### 核心流程

1. `run()` 逐日遍历交易日历：
   - 每月首个交易日更新观察池（遍历全市场找 `weekly_bull=True` 的股票）
   - 每日：大盘MACD空头时跳过买入（V2 模式）
   - 每日：从观察池筛选 `gc_ok + b1` 候选，按 `shrink_score` 升序取第1名买入
   - 每日：检查持仓卖出条件（止损→T+N→盈利100%清仓→半仓持股模式→涨停卖半→中阳卖1/3）
   - 记录每日权益曲线

#### 仓位管理

- 初始资金 100 万，最多 10 只，每只 10 万
- 买入股数按 100 股整手计算：`int(per_position_cash / (price * (1+commission)) / 100) * 100`
- T+N 按交易日计算（通过 trading_days 索引差值）
- Position 维护 `partial_proceeds` 累计部分卖出回款，用于摊薄成本价计算

#### 大盘MACD过滤（V2模式）

- `__init__` 接受可选参数 `market_macd_bullish: np.array[bool]`，长度须与 `trading_days` 一致
- `_check_entries()` 在大盘MACD空头时（`market_macd_bullish[td_idx] == False`）直接返回，不执行买入
- V1 策略传入 `None` 时跳过此过滤，行为不变

#### 止损止盈逻辑（优先级从高到低）

1. **止损**：白线之上买入→买入日最低价止损；白线黄线之间→黄线价止损；黄线之下→买入日最低价止损
2. **T+N没涨清仓**：持仓>=N日且价格<=买入价
3. **盈利100%清仓**：持仓盈利>=100%全仓卖出
4. **半仓持股模式**（`hold_until_below_white=True`）：连续止盈后仓位<=initial_size/2进入此模式
   - 盈利<=20%：盈转亏清仓
   - 盈利>20%：跌破白线清仓
5. **涨停卖1/2**：仅中阳未触发时；剩余<=半仓进入条件4
6. **中阳卖1/3**：涨幅>=主板5%/科创板10%；触发后标记`mid_yang_triggered=True`；剩余<=半仓进入条件4

#### 报告输出

总收益率、最大回撤、夏普比率（日收益率年化）、交易统计（笔数/胜率）、全部交易明细。`print_report()` 支持 `strategy_tag` 参数区分策略标签（如 `[B1]` / `[B1V2]`）。

#### 交易日志格式

所有日志行带策略标签前缀（`[B1]` 或 `[B1V2]`），关键信息如下：

| 操作 | 日志内容 |
|------|---------|
| 买入 | 价格、数量、止损价、缩量评分 |
| 部分卖出 | 卖出/剩余股数、价格、**成本价变化**（买入价→摊薄成本价）、盈亏% |
| 清仓 | 价格、**成本价变化**、总成本、总回款、**个股收益%(金额)**、卖出原因 |

**成本价摊薄计算**：部分卖出后，剩余持仓成本价 = (总买入成本 - 累计部分卖出回款) / 剩余股数。每次部分卖出都会降低成本价。

**清仓收益计算**：总收益 = (总回款 - 总成本) / 总成本 × 100%，其中总回款包含所有部分卖出的累计回款 + 最终清仓回款。

## 3. Relevant Code Modules

- `src/engine/backtester.py` - Backtester 单股回测类
- `src/engine/portfolio_simulator.py` - PortfolioSimulator 组合模拟器（Position 数据类）
- `config.py` - `INITIAL_CASH`, `COMMISSION`, `PORTFOLIO_INITIAL_CASH`, `PORTFOLIO_MAX_POSITIONS`, `PORTFOLIO_PER_POSITION`

## 4. Attention

- Backtester 的 `run()` 每次调用都会重新添加分析器，不应重复调用
- Backtester 的 `plot()` 依赖 matplotlib，在无 GUI 环境中需配合 `Agg` 后端
- PortfolioSimulator 的卖出逻辑与 `HuangBaiB1Strategy._check_exit()` 保持同步，含止损/T+N/盈利100%/半仓持股/涨停卖半/中阳卖1/3 六级优先级
- PortfolioSimulator 的 `_find_bar_index()` 使用 `searchsorted` 查找最近的前一个交易日，处理停牌场景
- 清仓收益使用总回款（含部分卖出累计）vs 总成本计算，非最终卖出价 vs 买入价
- `market_macd_bullish` 长度必须与 `trading_days` 一致，否则抛出 `ValueError`
