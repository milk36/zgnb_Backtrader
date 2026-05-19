# 策略层：黄白线B2_V2倍量柱策略

## 1. Purpose

基于B2策略的增强版本，在B2基础上新增"30日B1频次过滤"条件（最近30个交易日内至少有2个B1信号且间隔≥5个交易日），排序逻辑改为缩量升序+流动市值降序优先，扫描结果只取前1支。其余架构与B2一致（包装V4、倍量柱入场、复用六级退出）。

## 2. How it Works

### 架构：包装模式（与B2一致）

B2_V2是对V4 `_compute_all_bar_signals()` 的薄包装层，新增30日B1频次过滤函数。

### 入场条件（六重 AND）

1. 大盘 MACD 多头（复用V4）
2. 周线多头空间（复用V4）
3. **30日B1频次过滤**（新增：最近30个交易日内至少有2个B1信号，且间隔≥5天）
4. **前日B1信号**（`np.roll(b1_original, 1)`）
5. **当日倍量柱信号**（`_compute_beiliangzhu()`）
6. vol_expand_ok 过滤链（复用V4）

### 30日B1频次过滤：`_compute_b1_count_filter(b1_array, lookback, min_count, min_gap)`

对每个bar位置i，回看`lookback`个交易日（默认30）的窗口：
1. 统计窗口内B1信号出现的次数
2. 如果次数 >= `min_count`（默认2），检查任意两个相邻B1信号之间的间隔
3. 如果最大间隔 >= `min_gap`（默认5），则该bar通过过滤

参数集中配置在 `config.py`：
- `B2_V2_B1_LOOKBACK = 30`
- `B2_V2_B1_MIN_COUNT = 2`
- `B2_V2_B1_MIN_GAP = 5`

### 倍量柱定义（与B2一致）

```
PLRY := V > 1.8 * REF(V,1) AND C > O AND V > MA(V, 40)
PLRY_FIRST := PLRY AND NOT(REF(PLRY,1))
```

### 信号覆盖逻辑

| 字段 | V4原值 | B2_V2覆盖值 |
|------|--------|-------------|
| `b1` | V4七子条件 | `prev_b1 & beiliangzhu & b1_count_ok` |
| `dongneng_recent` | 60日动能窗口 | `np.ones(len(C), dtype=bool)` |

附加字段：
- `b1_original`：V4原始B1信号（供诊断）
- `beiliangzhu`：当日倍量柱布尔数组
- `b1_count_ok`：30日B1频次过滤布尔数组

### 排序规则（与B2不同）

- **扫描**：缩量升序（`shrink_score` ascending）优先，次按涨幅接近4.5%，取前1支
- **组合模拟**：不设 `b2_sort_primary`，使用PortfolioSimulator默认排序（`shrink_score`升序, `avg_amount_20`降序, `chip_spread`升序）

### 函数签名

| 函数 | 返回值 |
|------|--------|
| `scan_all()` | `(top1_results, market_macd_ok)` -- 二元组，top1最多1只 |
| `preload_all_signals()` | `(all_signals, trading_days, market_macd_bullish)` -- 三元组 |

### 组合模拟集成

- 复用 `PortfolioSimulator`，`strategy_tag="[B2_V2]"`
- 资金配置：100万/10只/每只10万（复用 `PORTFOLIO_*` 参数）
- 退出逻辑：标准六级（止损 -> T+N -> 盈利100% -> 半仓持股 -> 涨停卖半 -> 中阳卖1/3）
- T+1开盘买入（与B2一致，`"B2" in strategy_tag` 匹配）

### 运行模式

- 仅扫描：`python main.py --strategy huangbai_b2_v2 --scan-only`
- 组合模拟：`python main.py --strategy huangbai_b2_v2 --portfolio --chart`
- 不支持单股Backtrader回测（`STRATEGIES["huangbai_b2_v2"] = None`）

## 3. Relevant Code Modules

- `src/strategies/huangbai_b2_v2_strategy.py` - B2_V2策略主文件
- `src/strategies/huangbai_b2_strategy.py` - B2策略（对比参考）
- `src/strategies/huangbai_b1_v4_strategy.py` - V4策略（被包装的底层信号计算）
- `src/engine/portfolio_simulator.py` - 组合模拟器（六级退出）
- `config.py` - `B2_VOL_RATIO`, `B2_VOL_AVG_PERIOD`, `B2_MIN_MARKET_CAP`, `B2_V2_B1_*`
- `main.py` - `huangbai_b2_v2` 策略注册与扫描/组合模拟入口

## 4. Attention

- B2_V2与B2的差异仅在：新增30日B1频次过滤、不设b2_sort_primary（改用默认shrink_score排序）、扫描只取前1支
- 30日B1频次过滤使用循环实现（每bar检查30天窗口），对性能影响约5%
- `_scan_one_all_bars` 中 `b1_count_ok` 不随T+1前移（它描述的是当日的B1频次状态）
- `preload_all_signals()` 返回三元组（含 `market_macd_bullish`），与V2/V3/V4/V5/B2一致
