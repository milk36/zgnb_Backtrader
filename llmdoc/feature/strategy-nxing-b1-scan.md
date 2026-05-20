# 策略层：N型B1选股策略

## 1. Purpose

纯选股扫描策略（不做买卖操作），从全市场中筛选出60日内出现多次B1信号且价格逐次抬高的"N型低点抬高结构"股票，并统计T+3涨幅胜率。自动生成K线图（B1信号标记 + N型连接线）。

## 2. How it Works

### 整体流程

`scan_all()` 加载流通市值数据后，通过 `ProcessPoolExecutor` 并行扫描全市场A股，每只股票依次执行：前复权处理 -> 流通市值过滤(>50亿) -> 全bar B1计算+vol_expand_ok过滤 -> N型结构检测 -> T+3统计。

### 筛选条件（五重 AND）

1. **流通市值 > 50亿**（复用 `dongneng_zhuan_strategy._load_capital_data()`）
2. **B1七子条件**：复用 V4 的完整 B1 买入子条件（7 个 OR），通过 `_compute_all_bar_b1_and_filters()` 一次性计算全bar信号
3. **vol_expand_ok 过滤链**：放量上涨支撑 + 缩量上涨排除 + 放量下跌排除 + 阶梯出货排除 + 长上影线排除 + S1/大风车排除（与 V4 完全一致）
4. **N型结构**（`_find_nx_b1_pattern()`）：60日内 >= 2 次 B1信号，相邻两次间隔 >= 30 天，每次 B1 价格逐次抬高
5. **最新bar vol_expand_ok**：仅在最新交易日满足过滤时才入选

### N型结构检测算法

`_find_nx_b1_pattern()` 从最新B1向前搜索，找到间隔>30天且价格严格递增的B1序列。返回完整的B1列表（含idx、date、price），或 None。

### T+3统计

`_compute_t3_stats()` 对60日内所有B1信号点（不仅限于N型中的），计算买入后第3个交易日的涨幅，统计涨幅 >= 10% 的概率。输出每个B1点的命中/未命中详情及汇总胜率。

### K线图生成

`_generate_charts()` 清空 `charts/` 目录后为每只入选股票生成PNG：
- 蜡烛图（最近120天）+ 白线/黄线均线
- 所有B1信号用星号标记（品红色）
- N型B1点之间用蓝色虚线连接，标注价格
- 底部成交量柱状图
- 标题含代码/价格/市值/T+3胜率

### 参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `NX_B1_LOOKBACK` | 60 | N型检测回溯天数 |
| `NX_B1_MIN_COUNT` | 2 | N型最少B1次数 |
| `NX_B1_MIN_GAP` | 30 | 两次B1最小间隔（天） |
| `NX_T3_DAYS` | 3 | T+N统计天数 |
| `NX_T3_TARGET_PCT` | 10.0 | T+N涨幅目标(%) |

## 3. Relevant Code Modules

- `src/strategies/nxing_b1_scan_strategy.py` - 策略主文件（B1计算、N型检测、T+3统计、全市场扫描、K线图生成）
- `src/strategies/huangbai_b1_v4_strategy.py` - V4策略（B1七子条件 + vol_expand_ok过滤链的原始实现，N型B1策略复用其逻辑）
- `src/strategies/dongneng_zhuan_strategy.py` - 动能砖策略（`_load_capital_data()` 流通市值数据加载）
- `config.py` - HUANGBAI_* 系列参数、DNZH_MIN_MARKET_CAP(50亿)、CHART_OUTPUT_DIR
- `main.py` - `nxing_b1` 策略注册与扫描模式分发

## 4. Attention

- 纯选股策略，无 Backtrader 策略类、无组合模拟器、无单股回测，STRATEGIES 注册值为 None
- B1逻辑和 vol_expand_ok 过滤链从 V4 策略独立复制（非导入），修改 V4 的 B1 逻辑不会自动同步到此策略，需手动维护
- 流通市值数据通过 `_load_capital_data()` 从通达信本地目录加载，数据缺失时该股票被跳过
- `charts/` 目录每次扫描会被清空重建
- 扫描结果按缩量得分（shrink_score）升序排列
