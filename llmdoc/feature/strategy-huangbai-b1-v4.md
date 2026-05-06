# 策略层：黄白线B1策略 V4

## 1. Purpose

V2 策略的变体版本，**移除黄白线金叉条件**，放宽入场限制。保留大盘 MACD 过滤、周线多头、B1 七子条件、vol_expand_ok 过滤链和动量持股逻辑。适用于金叉信号过于严格导致选股范围过窄的场景。

## 2. How it Works

### 与 V2 的核心差异

V4 复制 V2 的全部代码逻辑，仅做一处结构性变更：**移除黄白线金叉（recent_gc）过滤**。

| 对比项 | V2 | V4 |
|--------|----|----|
| 大盘 MACD 过滤 | 有 | 有（完全相同） |
| 周线多头空间 | 有 | 有（完全相同） |
| 黄白线金叉 | 有 | **移除** |
| B1 七子条件 | 有 | 有（完全相同） |
| vol_expand_ok 过滤链 | 有 | 有（完全相同） |
| 出场/止盈止损 | 六级+动量持股 | 六级+动量持股（完全相同） |

### 入场过滤（V4 为五重 AND）

1. 大盘 MACD 多头（上证指数 DIF > DEA）
2. 周线多头空间（MA30 > MA60 > MA120 > MA240，且收盘价站上 MA30）
3. 个股 MACD 多头（预留接口，暂未启用）
4. B1 买入信号（7 个子条件 OR）
5. vol_expand_ok 过滤链

### 金叉移除的实现方式

- **Backtrader 策略类**：`_check_entry()` 中不检查金叉条件，直接进入后续过滤
- **`_compute_signals()`**：返回字典中 `gc` 字段始终为 `True`（兼容调用方解包）
- **`_compute_all_bar_signals()`**：返回字典中 `recent_gc` 为 `np.ones(len(C), dtype=bool)`（全 True 数组，兼容 PortfolioSimulator 逻辑）

### 函数签名

| 函数 | 返回值 |
|------|--------|
| `scan_all()` | `(results, market_macd_ok)` -- 与 V2 相同 |
| `preload_all_signals()` | `(all_signals, trading_days, market_macd_bullish)` -- 三元组，与 V2 相同 |

### PortfolioSimulator 集成

使用与 V2 相同的 `PortfolioSimulator`，`strategy_tag="[B1V4]"`。PortfolioSimulator 中的 `recent_gc` 过滤因 V4 返回全 True 数组而自动失效，无需修改模拟器代码。

### 动量持股逻辑

与 V2 完全相同：连续 3 天触发止盈条件（涨停或中阳）后进入动量持股模式，当日跌幅超过阈值（主板 7%/创业板 14%）清仓。

## 3. Relevant Code Modules

- `src/strategies/huangbai_b1_v4_strategy.py` - V4 策略主文件（策略类、大盘 MACD 函数、扫描函数、预加载函数）
- `src/strategies/huangbai_b1_v2_strategy.py` - V2 策略（V4 复制自 V2，B1 变更需同步两处）
- `src/engine/portfolio_simulator.py` - 组合模拟器（V4 复用，`recent_gc` 全 True 自动跳过金叉过滤）
- `config.py` - MACD 参数、HUANGBAI_* 系列参数
- `main.py` - `huangbai_v4` 策略注册与三种运行模式分发

## 4. Attention

- B1 逻辑变更需同步三个位置：`HuangBaiB1V4Strategy.indicators()`、`_compute_signals()`、`_compute_all_bar_signals()`（与 V1/V2 相同的三处同步问题）
- V4 代码独立于 V2 文件，不共享函数引用。V2 的 B1 逻辑变更不会自动同步到 V4，需手动维护
- `preload_all_signals()` 返回三元组，调用方需注意解包
- V4 不支持 `skip_gc` 参数（金叉条件已移除，该参数无意义）
- 日志标签为 `[B1V4]`，区别于 V2 的 `[B1V2]`
- 除金叉外，V4 的指标计算、出场逻辑、大盘 MACD 过滤均与 V2 逐行一致
