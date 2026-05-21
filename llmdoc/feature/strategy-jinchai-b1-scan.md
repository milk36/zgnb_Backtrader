# 策略层：金叉B1选股策略

## 1. Purpose

纯选股扫描策略，筛选"白线刚刚金叉黄线后出现B1信号"的股票。不做买卖操作，不支持组合模拟和单股回测。筛选后统计T+5涨幅超过10%的概率，自动生成K线图。

## 2. How it Works

### 整体流程

`scan_all_jc_b1()` 加载流通市值数据后，通过 `ProcessPoolExecutor` 并行扫描全市场A股。每只股票依次执行：前复权处理 -> 流通市值过滤(>50亿) -> 全bar B1计算+vol_expand_ok过滤 -> 金叉+B1模式检测 -> 8项假案例排除 -> T+5统计。

### 筛选条件（四重 AND）

1. **流通市值 > 50亿**（复用 `dongneng_zhuan_strategy._load_capital_data()`）
2. **B1七子条件 + vol_expand_ok 过滤链**：通过 `_compute_all_bar_b1_and_filters()` 一次性计算全bar信号（从 N型B1 策略导入）
3. **金叉+B1模式**（`_find_gc_b1_pattern()`）：白线在距今30bar内上穿黄线（金叉），金叉之后出现B1信号。回看窗口60天
4. **8项假案例排除**：快速上涨横盘派发、不规则上涨、B1死叉、跳空横盘出货、S1高位放量、不连续上涨、阶梯式量价背离、B1前跌停

### 金叉+B1模式检测

`_find_gc_b1_pattern()` 从指定B1位置向前搜索，检测白线是否在 `gc_max_bars=30` bar内上穿黄线。金叉后出现的B1即为有效信号。与N型结构不同，金叉+B1不要求多次B1、不要求间隔和价格递增。

### 日期区间筛选

支持 `--start` / `--end` 参数限定扫描的B1信号日期范围，只统计区间内出现的B1触发日。数据仍然加载完整历史（至少300bar用于指标计算）。

### T+5统计

复用 `_compute_t3_stats()`，参数调整为 `t3_days=5`、`target_pct=10.0`。对金叉B1模式中的所有B1信号点计算买入后第5个交易日的涨幅，统计涨幅 >= 10% 的概率。

### K线图生成

`_generate_charts()` 清空 `charts/` 目录后为每只入选股票生成PNG：
- 蜡烛图（以金叉日为中心前后约90天）+ 白线/黄线均线
- 所有B1信号用品红色星号标记
- 金叉点用蓝色菱形标记，附竖线
- 金叉到B1用蓝色虚线箭头连接
- 选股触发日用橙色虚线标注
- 标题含代码/价格/市值/T+5胜率

### 扫描输出

扫描结果按缩量得分（shrink_score）升序排列。每只股票输出：代码、收盘价、市值、缩量得分、J值、RSI值、金叉日、B1触发次数和日期、T+5胜率。末尾汇总所有B1信号的T+5整体胜率和平均涨幅。

## 3. Relevant Code Modules

- `src/strategies/jinchai_b1_scan_strategy.py` - 策略主文件（金叉B1扫描、T+5统计、K线图生成）
- `src/strategies/nxing_b1_scan_strategy.py` - 复用的核心函数（`_compute_all_bar_b1_and_filters`、`_find_gc_b1_pattern`、`_compute_t3_stats`、8项排除过滤、`_get_all_codes`）
- `src/strategies/dongneng_zhuan_strategy.py` - `_load_capital_data()` 流通市值数据加载
- `config.py` - `JCB1_*` 系列参数、`HUANGBAI_*` 系列参数、`DNZH_MIN_MARKET_CAP`、`CHART_OUTPUT_DIR`
- `main.py` - `jinchai_b1` 策略注册（STRATEGIES值为None）与扫描模式分发

## 4. Attention

- 无 Backtrader 策略类、无单股回测、无组合模拟，STRATEGIES 注册值为 None
- 仅支持 `--scan-only` 模式，不支持 `--portfolio`、`--symbol`
- 核心函数全部从 `nxing_b1_scan_strategy` 导入，B1逻辑和过滤链的变更点在 N型B1 策略文件中
- 每次扫描自动清空 `charts/` 目录并重建
- 选股结果中取缩量得分最小的B1作为代表，对其执行8项排除过滤
