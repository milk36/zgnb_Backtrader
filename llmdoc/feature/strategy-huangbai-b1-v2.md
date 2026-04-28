# 策略层：黄白线金叉后B1策略 V2

## 1. Purpose

V1 策略的增强版本，新增大盘（上证指数）MACD 多头/空头过滤。大盘 MACD 多头时正常选股买入，空头时只卖不买，避免在系统性下跌中开仓。

## 2. How it Works

### 与 V1 的核心差异

V2 继承 V1 的全部策略逻辑（周线多头 + 黄白线金叉 + B1 七子条件 + 分批止盈止损），仅在入场判断中新增一级过滤：

**入场四重过滤（AND关系）**：
1. 周线多头空间（同 V1）
2. **大盘 MACD 多头**（V2 新增）：上证指数 DIF > DEA
3. 黄白线金叉（同 V1）
4. B1 买入信号（同 V1）

出场逻辑与 V1 完全相同，不受大盘 MACD 影响（空头时仍正常卖出）。

### 大盘 MACD 计算

| 函数 | 说明 |
|------|------|
| `load_market_index()` | 通过 mootdx Reader 加载上证指数(`000001`)日线数据 |
| `compute_market_macd(close)` | 计算 MACD：`DIF = EMA(close,12) - EMA(close,26)`，`DEA = EMA(DIF,9)`，`bullish = DIF > DEA` |
| `compute_market_macd_for_trading_days(trading_days)` | 预计算每个交易日的 MACD 多头状态，返回 `np.array[bool]`，缺失日期回溯最近的前一交易日 |

MACD 参数由 `config.py` 配置：`MARKET_MACD_FAST=12`, `MARKET_MACD_SLOW=26`, `MARKET_MACD_SIGNAL=9`。

### Backtrader 策略类（`HuangBaiB1V2Strategy`）

- 继承 `BaseStrategy`，覆写 `next()` 实现逐 bar 交易
- 支持双数据源：`data0` 为个股数据，`data1` 为大盘指数数据
- `__init__` 中检测 `len(self.datas) > 1`，若存在第二数据源则计算 `_market_macd_bullish` 数组
- `_check_entry()` 先判断 `market_macd_ok`，不通过直接 return
- 新增调试参数 `skip_market_macd`（默认 False）
- 过滤日志新增 `大盘=Y/N` 字段

### 全市场扫描（`scan_all` V2）

- `scan_all()` 返回 `(results, market_macd_ok)`
- 调用时先获取上证指数最新 bar 的 MACD 状态
- 大盘空头时仍扫描但返回 `market_macd_ok=False`，调用方可据此跳过买入
- `_compute_signals()` 返回结构新增 `market_macd` 字段

### 组合级预加载（`preload_all_signals` V2）

- 返回 `(all_signals, trading_days, market_macd_bullish)`
- `market_macd_bullish` 为 `np.array[bool]`，长度等于 `trading_days`
- `PortfolioSimulator` 构造时接收 `market_macd_bullish`，在 `_check_entries()` 中每日过滤

### PortfolioSimulator 变更

- `__init__` 新增可选参数 `market_macd_bullish`，传入时校验长度与 `trading_days` 一致
- `_check_entries()` 开头新增判断：大盘 MACD 空头日直接 return，不执行买入

## 3. Relevant Code Modules

- `src/strategies/huangbai_b1_v2_strategy.py` - V2 策略主文件（策略类、大盘 MACD 函数、扫描函数、预加载函数）
- `src/strategies/huangbai_b1_strategy.py` - V1 策略（V2 复用其指标计算逻辑，B1 变更需同步两处）
- `src/engine/portfolio_simulator.py` - 组合模拟器（新增 `market_macd_bullish` 过滤）
- `config.py` - `MARKET_INDEX_CODE`、`MARKET_MACD_FAST`、`MARKET_MACD_SLOW`、`MARKET_MACD_SIGNAL`
- `main.py` - `huangbai_v2` 策略注册与三种运行模式分发

## 4. Attention

- B1 逻辑变更需同步三个位置：`HuangBaiB1V2Strategy.indicators()`、`_compute_signals()`、`_compute_all_bar_signals()`
- 单股回测时 V2 自动加载大盘指数作为第二数据源（`data1`），加载失败则跳过大盘过滤并打印警告
- `scan_all()` 返回元组 `(results, market_macd_ok)`，与 V1 的 `results` 不同，调用方需注意解包
- `preload_all_signals()` 返回三元组，V1 返回二元组
- `_compute_signals()` 与 `_compute_all_bar_signals()` 中的个股指标逻辑与 V1 相同，大盘 MACD 在调用方或模拟器层面过滤
- V1 文档中关于 B1 七子条件、出场逻辑、调试参数的说明完全适用于 V2
