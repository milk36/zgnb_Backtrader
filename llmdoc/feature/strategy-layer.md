# 策略层：基类与 KDJ 金叉策略

## 1. Purpose

提供策略开发模板基类 `BaseStrategy`（停牌/涨跌停过滤、订单管理、日志）和具体实现 `KDJCrossStrategy`（KDJ 金叉/死叉信号）。

## 2. How it Works

### BaseStrategy 模板模式

子类需实现三个方法：

| 方法 | 说明 |
|---|---|
| `indicators()` | 在 `__init__` 中自动调用，用于设置指标 |
| `buy_signal() -> bool` | 返回当前 bar 是否满足买入条件 |
| `sell_signal() -> bool` | 返回当前 bar 是否满足卖出条件 |

### 交易逻辑 (`next`)

1. 有未完成订单则跳过
2. 停牌（`volume == 0`）则跳过
3. 无持仓 + 买入信号 + 非涨停 -> `order_target_percent(target=position_pct)`
4. 有持仓 + 卖出信号 + 非跌停 -> `order_target_percent(target=0.0)`

### 涨跌停判断

- 涨停：`close >= high * 0.995`（无法买入）
- 跌停：`close <= low * 1.005`（无法卖出）

### KDJCrossStrategy

- **买入信号**：`CrossOver(K, D) > 0` 且 `close > SMA(close, 5)`
- **卖出信号**：`CrossOver(K, D) < 0`
- `position_pct` 默认 0.9（90% 仓位）

## 3. Relevant Code Modules

- `src/strategies/base_strategy.py` - BaseStrategy 基类
- `src/strategies/kdj_cross_strategy.py` - KDJCrossStrategy 实现
- `src/indicators/kdj_indicator.py` - KDJ 指标
- `config.py` - `POSITION_PCT`, `MA_PERIOD`

## 4. Attention

- 新策略只需继承 `BaseStrategy` 并实现三个抽象方法
- `notify_order` 仅在 Completed/Canceled/Margin 时清除 `self.order`，Rejected 状态未处理
- `order_target_percent` 按目标百分比下单，会自动计算所需买卖数量
