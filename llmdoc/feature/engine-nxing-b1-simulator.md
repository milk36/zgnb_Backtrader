# 引擎层：N型B1组合级模拟器 (NxingB1Simulator)

## 1. Purpose

N型B1策略的组合级日频模拟引擎，复用 PortfolioSimulator 的六级退出逻辑，在100万/10只/每只10万的资金框架下执行 T+1 开盘价买入，按缩量评分升序取最优1只。

## 2. How it Works

### 核心流程

`run()` 逐日遍历交易日历：

1. `_execute_pending_buys(td)` -- T+1开盘价执行待买入队列
2. `_check_exits(td)` -- 检查所有持仓的六级退出条件
3. `_scan_signals(td)` -- 扫描当日 N型B1 信号入队（最后一天跳过）
4. `_calc_equity(td)` -- 记录当日权益曲线

模拟结束后对剩余持仓强制清仓。

### 信号扫描与买入

`_scan_signals()` 遍历 `all_signals` 中所有非持仓股票，检查当日 bar 的 `b1` 和 `vol_expand_ok` 均为 True，收集候选后按 `shrink_score` 升序排序取第1只，加入 `_pending_buys` 队列。

`_execute_pending_buys()` 在次日以开盘价买入，股数按100股整手计算。止损价计算逻辑与 PortfolioSimulator 一致。

### 仓位管理

| 参数 | 默认值 | 配置常量 |
|------|--------|----------|
| 初始资金 | 1,000,000 | `NXB1_INITIAL_CASH` |
| 最大持仓数 | 10 | `NXB1_MAX_POSITIONS` |
| 每只资金 | 100,000 | `NXB1_PER_POSITION` |
| T+N天数 | 3 | `NXB1_T_PLUS_N` |
| 手续费 | 0.0003 | `COMMISSION` |

冷却期：清仓后10个交易日内不再买入同一股票。

### 六级退出逻辑（与 PortfolioSimulator 一致）

1. **止损** -- 跌破止损价
2. **巨量阴线清仓** -- V>REF(V,1)*3 & V>MA(V,20)*3 & C<O & (O-C)/O>3%（`huge_vol_bearish`），仅次于止损的最高优先级
3. **跌破黄线清仓** -- 买入价>=黄线时，价格跌破当前黄线
4. **T+N不涨清仓** -- 持仓>=N日且价格<=摊薄成本价
5. **盈利100%连跌2天清仓** -- 累计盈利>=100%后连续2天下跌
6. **部分卖出后跟踪** -- 白线/黄线止损 + 止盈放飞后放量阴线清仓
7. **动量持股** -- 连续3天止盈后进入动量模式；涨停卖1/2、中阳卖1/3

### 报告输出

`report()` 返回总收益率、最大回撤、夏普比率、交易统计。胜率基于单只股票的总盈亏判定（非单笔交易），按盈利从高到低排序。`print_report()` 静态方法支持 `strategy_tag` 参数。

## 3. Relevant Code Modules

- `src/engine/nxing_b1_simulator.py` - NxingB1Simulator 类 + Position 数据类
- `src/engine/portfolio_simulator.py` - PortfolioSimulator（六级退出逻辑的原始实现）
- `src/strategies/nxing_b1_scan_strategy.py` - `preload_all_signals()` 预加载函数
- `config.py` - `NXB1_INITIAL_CASH`, `NXB1_MAX_POSITIONS`, `NXB1_PER_POSITION`, `NXB1_T_PLUS_N`
- `main.py` - `nxing_b1` 分支：组合模拟入口 + `--chart` 图表生成

## 4. Attention

- 退出逻辑与 PortfolioSimulator 保持同步，但无观察池/周线多头过滤/大盘MACD过滤，直接使用全市场信号
- **巨量阴线清仓**通过信号字典的 `huge_vol_bearish` 字段判断，在止损之后检查
- `preload_all_signals()` 中的 `_scan_one_all_bars_nx()` 对每个B1日运行完整的 N型检测 + 8项排除过滤，预加载阶段耗时长
- Position 使用 `__slots__` 优化内存，字段与 PortfolioSimulator 的 Position 类一致
- 日志文件前缀 `nxb1_portfolio_`，输出到 `logs/` 目录
