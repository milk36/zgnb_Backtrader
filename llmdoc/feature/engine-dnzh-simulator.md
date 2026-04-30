# 引擎层：动能砖组合模拟器 (DongnengZhuanSimulator)

## 1. Purpose

动能+砖策略的专用组合级日频模拟引擎。支持分钟级入场确认和出场监控，五级退出机制，以及涨幅2%部分卖出。与 PortfolioSimulator 的区别：T+1分钟确认买入、五级退出（含部分卖出）、独立的资金/仓位配置。

## 2. How it Works

### 核心流程

1. 预加载全部A股信号数据（Phase 1，由 `preload_all_signals` 完成）
2. 逐日遍历交易日历：
   - 执行昨日信号的T+1分钟确认买入（降级：日线开盘价）
   - 检查持仓退出条件（分钟级优先，日线降级）
   - 扫描今日信号 → 加入明日待买入队列
   - 记录每日权益；清理分钟线缓存

### T+1分钟确认买入

信号在Day T检测，买入在Day T+1通过5分钟线确认执行：

- `_scan_signals(td)`: 扫描全市场信号，按排名分数降序取前N只加入 `_pending_buys` 队列
- `_execute_pending_buys(td)`: 分钟确认或日线降级买入

**分钟确认逻辑**（`minute_entry_enabled=True` 且有 MinuteFeed 时生效）：
1. 获取当日前N根5分钟bar（默认N=3，覆盖9:30-9:45）
2. 逐根检查：阳线（close > open）且 close >= 日线开盘价
3. 满足则以该bar收盘价买入，标记 `confirmed_minute=True`
4. 前N根均不满足则放弃本次买入

**降级逻辑**：无分钟数据或分钟确认禁用时，直接以日线开盘价买入。

### 五级退出（优先级从高到低）

每个级别均有分钟级监控（优先）和日线降级两条路径：

| # | 条件 | 级别 | 说明 |
|---|------|------|------|
| 1 | 止损 | 分钟/日线 | 价格 <= 买入价 × (1 - stop_loss_pct/100)，默认跌破4% |
| 2 | 涨停清仓 | 分钟/日线 | 最高价触及涨停价即全部清仓（主板10%/科创板20%） |
| 3 | 涨幅2%部分卖出 | 分钟/日线 | 卖出1/4仓位，仅触发一次（`pos.partial_sold` 标记） |
| 4 | T+N不拉升 | 日线 | 持仓>=N日 且 价格<=买入价，清仓 |
| 5 | 盈利止盈 | 日线 | 盈利>=X% 且 持仓>=M天，清仓 |

### 部分卖出（`_partial_sell`）

涨幅达2%时卖出1/4仓位：
- `sell_size = max(100, pos.size // 4 // 100 * 100)`（100股整手）
- 高价股 pos.size <= 100 时直接全部卖出
- 仅触发一次（`pos.partial_sold` 标记）
- 卖出后 pos.size 减小，后续全仓卖出以剩余股数为准

### Position 数据类

```python
__slots__ = ("code", "buy_date", "buy_price", "buy_low", "stop_loss",
             "size", "initial_size", "confirmed_minute", "partial_sold")
```

- `stop_loss = round(buy_price * (1 - stop_loss_pct / 100), 2)`
- `confirmed_minute`: 是否通过分钟确认入场
- `partial_sold`: 是否已执行过部分卖出

### 分钟级数据（MinuteFeed）

- `src/data/minute_feed.py` 提供5分钟K线数据，基于 mootdx Reader
- 懒加载 + 日级缓存（`(code, date_str)` 为key）
- 每日结束调用 `clear_cache()` 释放内存
- 无数据时返回 None，模拟器自动降级为日线逻辑
- `minute_entry_enabled` / `minute_exit_enabled` 可独立开关

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
| DNZH_STOP_LOSS_PCT | 4.0 | 止损百分比（买入价下跌4%） |
| DNZH_MINUTE_CONFIRM_BARS | 3 | 分钟确认bar数（前3根5分钟线） |
| DNZH_MINUTE_ENTRY_ENABLED | True | 启用分钟级入场确认 |
| DNZH_MINUTE_EXIT_ENABLED | True | 启用分钟级出场监控 |

### 报告输出

总收益率、最大回撤、夏普比率、交易统计（笔数/胜率）、全部交易明细（含部分卖出标记）。

## 3. Relevant Code Modules

- `src/engine/dongneng_zhuan_simulator.py` - DongnengZhuanSimulator 类、Position 数据类
- `src/data/minute_feed.py` - MinuteFeed 5分钟K线数据加载器
- `src/strategies/dongneng_zhuan_strategy.py` - preload_all_signals() 预加载函数
- `config.py` - DNZH_* 系列参数

## 4. Attention

- 已支持部分卖出（涨幅2%卖1/4），不再是纯全仓进出
- 不依赖 Backtrader，纯 numpy/pandas 日频+分钟级模拟
- `_pending_buys` 队列确保T+1延迟执行，避免未来信息泄露
- `_sell_position` 开头检查 `pos.size <= 0` 防止除零错误
- 分钟数据缺失时自动降级为日线逻辑，无需额外配置
- `market_macd_bullish` 等大盘过滤未实现，可后续扩展
