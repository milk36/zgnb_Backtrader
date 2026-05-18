# 策略层：黄白线B2倍量柱策略

## 1. Purpose

基于V4策略的包装策略，将V4的B1买入信号替换为"前日B1 + 当日倍量柱"组合条件，移除动能过滤。倍量柱表示放量突破的确认信号，与B1形成时序上的两日联动入场。退出逻辑复用PortfolioSimulator标准六级退出。

## 2. How it Works

### 架构：包装模式

B2不是独立策略，而是对V4 `_compute_all_bar_signals()` 的薄包装层：

1. 调用V4的 `_compute_all_bar_signals()` 获取完整信号字典
2. 对返回字典中的 `b1` 和 `dongneng_recent` 进行覆盖
3. 附加倍量柱辅助字段

### 入场条件（五重 AND）

1. 大盘 MACD 多头（复用V4，通过V4函数链传递）
2. 周线多头空间（复用V4）
3. **前日B1信号**（`np.roll(b1_original, 1)`）
4. **当日倍量柱信号**（`_compute_beiliangzhu()`）
5. vol_expand_ok 过滤链（复用V4）

其中条件3+4合并覆盖V4的 `b1` 字段，条件"动能过滤"通过 `dongneng_recent` 设为全True而跳过。

### 倍量柱定义：`_compute_beiliangzhu(V, C, O)`

```
PLRY := V > ratio * REF(V,1) AND C > O AND V > MA(V, avg_period)
PLRY_FIRST := PLRY AND NOT(REF(PLRY,1))
```

- `ratio`：`config.B2_VOL_RATIO`（默认1.8）
- `avg_period`：`config.B2_VOL_AVG_PERIOD`（默认40）
- 首次出现：前一日不满足倍量柱条件

### 信号覆盖逻辑

| 字段 | V4原值 | B2覆盖值 |
|------|--------|----------|
| `b1` | V4七子条件 | `prev_b1 & beiliangzhu` |
| `dongneng_recent` | 60日动能窗口 | `np.ones(len(C), dtype=bool)` |

附加字段：
- `b1_original`：V4原始B1信号（供诊断）
- `beiliangzhu`：当日倍量柱布尔数组

### 函数签名

| 函数 | 返回值 |
|------|--------|
| `scan_all()` | `(results, market_macd_ok)` -- 二元组 |
| `preload_all_signals()` | `(all_signals, trading_days, market_macd_bullish)` -- 三元组 |

### 组合模拟集成

- 复用 `PortfolioSimulator`，`strategy_tag="[B2]"`
- 资金配置：100万/10只/每只10万（复用 `PORTFOLIO_*` 参数）
- 退出逻辑：标准六级（止损 -> T+N -> 盈利100% -> 半仓持股 -> 涨停卖半 -> 中阳卖1/3）

### 运行模式

- 仅扫描：`python main.py --strategy huangbai_b2 --scan-only`
- 组合模拟：`python main.py --strategy huangbai_b2 --chart`
- 不支持单股Backtrader回测（`STRATEGIES["huangbai_b2"] = None`）

### 选股排序

扫描结果按 `shrink_score` 升序排列（缩量越明显排名越靠前）。组合模拟中排序规则为 `(shrink_score升序, avg_amount_20降序, chip_spread升序)` 三键排序。

## 3. Relevant Code Modules

- `src/strategies/huangbai_b2_strategy.py` - B2策略主文件（`_compute_beiliangzhu()`、`_compute_all_bar_signals()` 包装、`scan_all()`、`preload_all_signals()`）
- `src/strategies/huangbai_b1_v4_strategy.py` - V4策略（被包装的底层信号计算）
- `src/engine/portfolio_simulator.py` - 组合模拟器（六级退出）
- `config.py` - `B2_VOL_RATIO`, `B2_VOL_AVG_PERIOD`, `B2_MIN_MARKET_CAP`
- `main.py` - `huangbai_b2` 策略注册与扫描/组合模拟入口

## 4. Attention

- B2依赖V4的 `_compute_all_bar_signals`，V4的B1逻辑变更会直接影响B2的前日B1判定
- 倍量柱参数（`B2_VOL_RATIO=1.8`, `B2_VOL_AVG_PERIOD=40`）集中配置在 `config.py`
- B2无Backtrader策略类，不存在三处同步问题
- `preload_all_signals()` 返回三元组（含 `market_macd_bullish`），与V2/V3/V4/V5一致
- 流通市值过滤通过 `B2_MIN_MARKET_CAP` 控制（默认50亿），复用 `_load_capital_data()` 加载数据
