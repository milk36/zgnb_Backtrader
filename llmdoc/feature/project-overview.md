# 项目总览：Backtrader + MyTT + mootdx 量化回测系统

## 1. Purpose

基于 Backtrader 框架的本地量化回测系统，使用 mootdx 读取通达信本地行情数据，使用 MyTT 计算技术指标（通达信标准公式），支持自定义策略开发与回测分析。

## 2. How it Works

### 架构分层

```
main.py (CLI 入口，参数解析)
  ├── config.py (集中配置)
  ├── src/data/tdx_feed.py (数据层：通达信数据读取)
  ├── src/indicators/ (指标层：MyTT 计算)
  ├── src/strategies/ (策略层：信号生成 + 交易逻辑)
  ├── src/engine/backtester.py (引擎层：Cerebro 封装)
  └── src/scanner.py (扫描层：全市场选股，不依赖 Backtrader)
```

### 核心数据流

1. `TdxDataFeed` 通过 mootdx Reader 从通达信本地目录读取日线数据
2. 数据经列名映射（`vol` -> `volume`）、日期索引、裁剪后封装为 `bt.feeds.PandasData`
3. `Backtester` 将数据注入 Cerebro，绑定策略与分析器
4. 策略中自定义指标在 `__init__` 阶段通过 MyTT 批量计算完整数组，`next()` 逐 bar 读取
5. Cerebro 运行回测，产出包含 SharpeRatio / DrawDown / Returns / TradeAnalyzer 的报告

### 运行模式

`main.py` 支持两种运行模式：

| 模式 | 触发参数 | 说明 |
|------|---------|------|
| 全市场扫描 | `--scan` | 调用 `src/scanner.py`，直接用 MyTT 计算指标，输出选股结果，不进入回测 |
| 单股/多股回测 | `--symbol`（默认） | 通过 Backtrader 引擎执行回测，可选 `--strategy` / `--stock-type` |

### 策略选择机制

`main.py` 通过 `--strategy` 参数选择策略，注册在 `STRATEGIES` 字典中：

| key | 策略类 | 说明 |
|-----|--------|------|
| `kdj` | KDJCrossStrategy | KDJ金叉/死叉策略（默认） |
| `huangbai` | HuangBaiB1Strategy | 黄白线金叉后B1策略 |

黄白线策略额外支持 `--stock-type main/tech` 参数（影响振幅/中阳阈值）。

### 关键设计决策

- **MyTT 批量计算 vs Backtrader 逐 bar 计算**：指标在 `__init__` 中用 MyTT 一次性算完，`next()` 中按索引取值。信号判断使用 Backtrader 原生 `CrossOver` 保持逐 bar 语义
- **KDJ 公式选择**：使用 MyTT 的 `SMA(RSV,3,1)` 而非 MyTT 内置 `KDJ()` 函数（后者用 EMA），以匹配通达信标准
- **配置方式**：`config.py` 集中配置而非 YAML，避免额外依赖
- **策略模板 vs 自定义next**：简单策略用 BaseStrategy 的 `buy_signal/sell_signal` 模板；复杂策略（如黄白线B1，含分批卖出和状态管理）直接覆写 `next()`

## 3. Relevant Code Modules

- `config.py` - 全局配置（TDX 路径、资金、手续费、策略参数）
- `main.py` - CLI 入口，argparse 参数解析，串联各模块
- `requirements.txt` - 依赖清单

## 4. Attention

- 通达信本地路径 `TDX_DIR` 需根据实际安装位置修改 `config.py`
- `TDX_MARKET` 默认为 `"std"`（标准市场），使用前需确认 mootdx Reader 的 market 参数
