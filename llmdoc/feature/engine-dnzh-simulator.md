# 引擎层：动能砖组合模拟器 (DongnengZhuanSimulator)

## 1. Purpose

动能+砖策略的专用组合级日频模拟引擎。与 PortfolioSimulator 的区别：T+1开盘买入、简化的三级退出、不同的资金/仓位配置。

## 2. How it Works

### 核心流程

1. 预加载全部A股信号数据（Phase 1，由 `preload_all_signals` 完成）
2. 逐日遍历交易日历：
   - 执行昨日信号的T+1开盘买入
   - 检查持仓退出条件
   - 扫描今日信号 → 加入明日待买入队列
   - 记录每日权益

### T+1开盘买入

信号在Day T检测，买入在Day T+1的开盘价执行：
- `_scan_signals(td)`: 扫描全市场信号，按排名分数降序取前N只加入 `_pending_buys` 队列
- `_execute_pending_buys(td)`: 用今日开盘价执行队列中的买入

### 三级退出（优先级从高到低）

| # | 条件 | 说明 |
|---|------|------|
| 1 | 止损 | 价格 <= 买入K线最低价 |
| 2 | T+N不拉升 | 持仓>=N日 且 价格<=买入价 |
| 3 | 盈利持仓止盈 | 盈利>=X% 且 持仓>=M天 |

### 仓位管理

- 初始资金 10万，最多 2 只，每只 5万
- 买入股数按 100 股整手计算
- 止损冷却期：5个交易日内不再买入同一只

### 配置参数（config.py DNZH_* 系列）

| 参数 | 默认值 | 说明 |
|------|--------|------|
| DNZH_INITIAL_CASH | 100,000 | 初始资金 |
| DNZH_MAX_POSITIONS | 2 | 最多持仓数 |
| DNZH_PER_POSITION | 50,000 | 单只最大买入金额 |
| DNZH_T_PLUS_N | 2 | 不拉升清仓天数 |
| DNZH_MAX_HOLD_DAYS | 5 | 盈利后最大持仓天数 |
| DNZH_PROFIT_PCT | 5.0 | 脱离成本区百分比 |

### 报告输出

总收益率、最大回撤、夏普比率、交易统计（笔数/胜率）、全部交易明细。

## 3. Relevant Code Modules

- `src/engine/dongneng_zhuan_simulator.py` - DongnengZhuanSimulator 类、Position 数据类
- `src/strategies/dongneng_zhuan_strategy.py` - preload_all_signals() 预加载函数
- `config.py` - DNZH_* 系列参数

## 4. Attention

- 与 PortfolioSimulator 不同，本模拟器不支持分批卖出，每次都是全仓进出
- 不依赖 Backtrader，纯 numpy/pandas 日频模拟
- `_pending_buys` 队列确保T+1延迟执行，避免未来信息泄露
- `_scan_signals` 遍历全部股票（无观察池概念），因为没有周线多头预过滤
- `market_macd_bullish` 等大盘过滤未实现，可后续扩展
