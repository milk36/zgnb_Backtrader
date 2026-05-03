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
5. **前期放量过滤 + 缩量拉升排除**：近N天内至少M天放量上涨，且排除近期涨幅大但量能萎缩的股票（详见 V1 文档）

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
- `config.py` - `MARKET_INDEX_CODE`、`MARKET_MACD_FAST`、`MARKET_MACD_SLOW`、`MARKET_MACD_SIGNAL`、`HUANGBAI_VOL_EXPAND_PERIOD`、`HUANGBAI_VOL_EXPAND_MIN`、`HUANGBAI_SURGE_PRICE_PCT`、`HUANGBAI_SURGE_VOL_RATIO`
- `main.py` - `huangbai_v2` 策略注册与三种运行模式分发

## 4. Attention

- B1 逻辑变更需同步三个位置：`HuangBaiB1V2Strategy.indicators()`、`_compute_signals()`、`_compute_all_bar_signals()`
- 前期放量过滤在三个位置均已实现：Backtrader 策略类 `_vol_expand_ok`、`_compute_signals()`、`_compute_all_bar_signals()`
- 缩量快速拉升检测逻辑：涨幅 > `HUANGBAI_SURGE_PRICE_PCT`(15%) 且 近期均量/长期均量 < `HUANGBAI_SURGE_VOL_RATIO`(0.7) 时排除
- 连续涨停缩量排除：前期连续2天涨停且成交量递减则直接剔除（主板10%/科创板20%涨停阈值）
- 连续上涨后放量下跌排除：近N天下跌日总成交量 > 上涨日总成交量则剔除（出货特征）
- 单股回测时 V2 自动加载大盘指数作为第二数据源（`data1`），加载失败则跳过大盘过滤并打印警告
- `scan_all()` 返回元组 `(results, market_macd_ok)`，与 V1 的 `results` 不同，调用方需注意解包
- `preload_all_signals()` 返回三元组，V1 返回二元组
- `_compute_signals()` 与 `_compute_all_bar_signals()` 中的个股指标逻辑与 V1 相同，大盘 MACD 在调用方或模拟器层面过滤
- `_scan_one_all_bars()` 返回的信号字典包含 `avg_amount_20` 字段（20日成交额均值），供 PortfolioSimulator 选股排序使用
- V1 文档中关于 B1 七子条件、出场逻辑、调试参数的说明完全适用于 V2

### 代码审查修复记录（2026-04）

- **`_mid_yang_triggered` 一次性触发保护**：中阳卖1/3 触发后标记 `_mid_yang_triggered=True`，确保每次持仓仅触发一次，避免中阳持续期间每天重复卖出。Backtrader 策略类中通过 `_reset_position_state()` 在清仓时重置；PortfolioSimulator 中 `Position` 对象新建时默认 `False`
- **中阳卖1/3 当日上涨前置条件**：除累计盈利达标外，还需当日收盘价 > 前一日收盘价（`daily_up = price > prev_close`），防止股票持续下跌时误触发中阳卖出
- **修复 `_compute_signals()` 除零风险**：`daily_pct` 计算添加 `C[i-1] > 0` 保护
- **优化 `compute_market_macd_for_trading_days`**：用 `pd.Series.reindex(method='ffill')` 替代 O(n*m) 嵌套循环
- **优化 `preload_all_signals` 日期收集**：用 `DatetimeIndex.union` 替代 set + sorted，移除无意义的 `hasattr` 检查
- **清理个股 MACD 注释**：三处注释块简化为 `个股MACD多头过滤（暂未启用，预留接口）`
- **移除 `_print_filter_result` 无用默认参数**：`market_macd_ok` 和 `stock_macd_ok` 不再有默认值
- **添加 `_process_reader` 非空检查**：`_scan_one` 和 `_scan_one_all_bars` 入口添加 assert
- **改善异常处理**：`except Exception` 不再静默吞错，返回错误详情
- **添加 `skip_on_bear` 参数**：`scan_all()` 支持大盘空头时跳过扫描以节省时间
- **添加 `position.size` 预测值注释**：澄清涨停卖半中 `position.size - sell_size` 是预测值而非已执行结果
