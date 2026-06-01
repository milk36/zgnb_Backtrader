# 策略层：完美B1策略

## 1. Purpose

基于 V4 B1 信号的包装策略，叠加5种历史案例总结的量价模式进行质量过滤，仅保留匹配强势模式的B1信号。核心思路：不是所有B1都值得买，通过模式匹配筛选出"完美B1"——缩量极致、主力控盘信号明确的入场点。

## 2. How it Works

### 架构：包装模式

与 B2 策略相同的薄包装架构，对 V4 `_compute_all_bar_signals()` 进行二次加工：

1. 调用 V4 的 `_compute_all_bar_signals()` 获取完整信号字典
2. 覆盖 `vol_expand_ok` 为全 True（完美B1核心特征是极致缩量，与前期放量要求互斥）
3. 独立计算 KDJ-J 值（V4 未返回 J 值）
4. 计算距离指标（`dist_w`、`dist_y`）
5. 执行5种模式匹配，覆盖 `b1` 字段

### 5种模式匹配逻辑：`_compute_pattern_matches()`

| 模式 | 名称 | 条件 | 来源案例 |
|------|------|------|----------|
| 模式一 | 典型单波 | `shrink<30%` & `J<14` & `dist_w<=2.5% or dist_y<=10%` | 华纳药厂、微芯生物 |
| 模式二 | 白线不死叉 | `EVERY(white>=yellow, 30)` & `J<14` | 澄天伟业、国轩高科 |
| 模式三 | 多波N型 | `COUNT(CROSS(C,yellow),60)>=2` & `shrink<26%` | 宁波韵升、光电股份 |
| 模式四 | 跌破反转 | `C<yellow` & `shrink<28%` & `J<14` | 野马电池、新瀚新材 |
| 模式五 | 大牛市 | `(C-yellow)/yellow*100 > 30` & `shrink<25%` | 昂利康 |

模式优先级（多匹配时覆盖顺序）：3 > 4 > 1 > 2 > 5

### 信号覆盖与附加字段

| 字段 | 覆盖值 |
|------|--------|
| `b1` | `b1_original & pattern_matched`（V4 B1 且匹配至少一种模式） |
| `b2_sort_primary` | `inf`（不做B2涨幅排序，使用 shrink_score 排序） |

附加字段：`b1_original`、`pattern_type`、`pattern_p1~p5`、`J`、`dist_w`、`dist_y`

### 函数签名

| 函数 | 返回值 |
|------|--------|
| `scan_all()` | `(results, market_macd_ok)` — 二元组 |
| `preload_all_signals()` | `(all_signals, trading_days, market_macd_bullish)` — 三元组 |

### 组合模拟集成

- 复用 `PortfolioSimulator`，`strategy_tag="[完美B1]"`
- 资金配置：100万/10只/每只10万
- 退出逻辑：标准六级退出（止损 -> T+N -> 盈利100% -> 半仓持股 -> 涨停卖半 -> 中阳卖1/3）

### 运行模式

- 仅扫描：`python main.py --strategy perfect_b1 --scan-only`
- 组合模拟：`python main.py --strategy perfect_b1 --portfolio --chart`
- 不支持单股 Backtrader 回测

### 选股排序

扫描和组合模拟均按 `shrink_score` 升序排列（缩量越极致优先级越高）。

## 3. Relevant Code Modules

- `src/strategies/perfect_b1_strategy.py` — 策略主文件（`_compute_pattern_matches()`、`_compute_all_bar_signals()` 包装、`scan_all()`、`preload_all_signals()`）
- `src/strategies/huangbai_b1_v4_strategy.py` — V4 策略（被包装的底层信号计算）
- `src/engine/portfolio_simulator.py` — 组合模拟器（六级退出）
- `config.py` — `HUANGBAI_*` 参数、`DNZH_MIN_MARKET_CAP`
- `main.py` — `perfect_b1` 策略注册与扫描/组合模拟入口
- `thinking/完美B1.md` — 5种模式的历史案例研究与量化标准来源

## 4. Attention

- 完美B1依赖 V4 的 `_compute_all_bar_signals`，V4 的 B1/巨量阴线过滤变更会直接传递，但 **vol_expand_ok 已被覆盖为全 True**（极致缩量与前期放量互斥）
- 突然放巨量阴线过滤通过 V4 信号字典的 `no_huge_vol_bearish` 字段传递，本策略无独立实现
- KDJ-J 值在本策略中独立计算（V4 信号字典未返回 J 值），公式：`J = 3*K - 2*D`（MyTT SMA）
- 无 Backtrader 策略类，不存在三处同步问题
- `preload_all_signals()` 返回三元组（含 `market_macd_bullish`），与 V2/V3/V4/V5/B2 一致
- 流通市值过滤通过 `DNZH_MIN_MARKET_CAP` 控制，复用 `_load_capital_data()` 加载数据
