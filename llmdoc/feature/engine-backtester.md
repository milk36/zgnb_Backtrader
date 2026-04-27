# 引擎层：回测引擎 (Backtester)

## 1. Purpose

封装 Backtrader `Cerebro` 的完整生命周期：初始化配置、数据注入、策略绑定、分析器挂载、执行回测、格式化报告输出与绘图。

## 2. How it Works

### 初始化

构造时创建 `bt.Cerebro()` 实例，设置 broker 现金和佣金费率。

### 核心方法

| 方法 | 说明 |
|---|---|
| `add_feed(feed, name)` | 添加数据源（`cerebro.adddata`） |
| `add_strategy(cls, **kwargs)` | 绑定策略类 |
| `run() -> dict` | 挂载分析器、执行回测、返回报告字典 |
| `plot(**kwargs)` | 调用 `cerebro.plot()` |
| `print_report(report)` | 静态方法，格式化打印报告 |

### 分析器配置

- `bt.analyzers.SharpeRatio` -> `report["sharpe"]`
- `bt.analyzers.DrawDown` -> `report["drawdown"]`
- `bt.analyzers.Returns` -> `report["returns"]`
- `bt.analyzers.TradeAnalyzer` -> `report["trades"]`

### 报告输出内容

初始资金、最终资金、总收益率、最大回撤、夏普比率、总交易次数、盈利/亏损次数、胜率。

## 3. Relevant Code Modules

- `src/engine/backtester.py` - Backtester 类
- `config.py` - `INITIAL_CASH`, `COMMISSION`

## 4. Attention

- `run()` 每次调用都会重新添加分析器，不应重复调用
- `plot()` 依赖 matplotlib，在无 GUI 环境（如远程服务器）中需配合 `Agg` 后端使用
- 当前仅支持单策略，多策略需扩展 `add_strategy` 逻辑
