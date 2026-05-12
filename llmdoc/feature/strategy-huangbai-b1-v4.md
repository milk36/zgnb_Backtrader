# 策略层：黄白线B1策略 V4

## 1. Purpose

V2 策略的变体版本，**移除黄白线金叉条件**，新增**60日动能信号过滤**。保留大盘 MACD 过滤、周线多头、B1 七子条件、vol_expand_ok 过滤链和动量持股逻辑。适用于金叉信号过于严格导致选股范围过窄的场景。

## 2. How it Works

### 与 V2 的核心差异

| 对比项 | V2 | V4 |
|--------|----|----|
| 大盘 MACD 过滤 | 有 | 有（完全相同） |
| 周线多头空间 | 有 | 有（完全相同） |
| 黄白线金叉 | 有 | **移除** |
| 个股 MACD 多头 | 预留接口（未启用） | **替换为60日动能信号过滤** |
| B1 七子条件 | 有 | 有（完全相同） |
| vol_expand_ok 过滤链 | 有 | 有（完全相同） |
| 出场/止盈止损 | 六级+动量持股 | 六级+动量持股（完全相同） |

### 入场过滤（V4 为五重 AND）

1. 大盘 MACD 多头（上证指数 DIF > DEA）
2. 周线多头空间（MA30 > MA60 > MA120 > MA240，且收盘价站上 MA30）
3. **60日内有动能信号**（综合天命打分 + 阵营过滤 + 硬性过滤）
4. B1 买入信号（7 个子条件 OR）
5. vol_expand_ok 过滤链

### 动能信号过滤：`_compute_dongneng_ok()`

独立 helper 函数，复用 `dongneng_zhuan_strategy` 的核心动能计算逻辑，**不含流通市值过滤**（V4 无 capital_shares 数据）。

计算步骤：
1. **RSI(3)** + **KDJ(9,3,3)** 动量指标
2. **基础动量** = `(N1 + N2) / 2 * shadow_coef * vol_bonus`（影线系数+量价加成）
3. **X动量** = 动量增量差值（阳线且差值递增时计算）
4. **综合天命打分**（visual_score）= 基础动量 + 量比加成 - J/RSI/V20/RetZ 归一化惩罚
5. **阵营过滤**：四选一（A: score>=35 & mom>=25, B: 20<=score<35 & mom>=45, C: score<20 & mom>=65, D: x_mom>=45 & mom<=20）
6. **硬性过滤**：上影线<0.30 & 下影线<0.35 & 涨幅>=3.0 & Z-Score>=0.8
7. 最终：`is_yang & hard_mask & (mask_a | mask_b | mask_c | mask_d)`

信号通过 `EXIST(dongneng_ok, 60)` 扩展为60日窗口，即60日内任一天触发即视为满足。

### 金叉移除的实现方式

- **Backtrader 策略类**：`_check_entry()` 中不检查金叉条件，直接进入后续过滤
- **`_compute_signals()`**：返回字典中 `gc` 字段始终为 `True`（兼容调用方解包）
- **`_compute_all_bar_signals()`**：返回字典中 `recent_gc` 为 `np.ones(len(C), dtype=bool)`（全 True 数组，兼容 PortfolioSimulator 逻辑）

### 函数签名

| 函数 | 返回值 |
|------|--------|
| `scan_all()` | `(results, market_macd_ok)` -- 与 V2 相同 |
| `preload_all_signals()` | `(all_signals, trading_days, market_macd_bullish)` -- 三元组，与 V2 相同 |

### PortfolioSimulator 集成

使用与 V2 相同的 `PortfolioSimulator`，`strategy_tag="[B1V4]"`。买入条件中：
- `recent_gc` 过滤因 V4 返回全 True 数组而自动失效
- `dongneng_recent` 替代原 `stock_macd_bullish`，兼容写法：`sig.get("dongneng_recent", sig.get("stock_macd_bullish", ...))`

### 动量持股逻辑

与 V2 完全相同：连续 3 天触发止盈条件（涨停或中阳）后进入动量持股模式，当日跌幅超过阈值（主板 7%/创业板 14%）清仓。

## 3. Relevant Code Modules

- `src/strategies/huangbai_b1_v4_strategy.py` - V4 策略主文件（策略类、`_compute_dongneng_ok()` helper、大盘 MACD 函数、扫描/预加载函数）
- `src/strategies/dongneng_zhuan_strategy.py` - 动能砖策略（`_compute_dongneng_ok` 复用其核心逻辑，导入 `_rolling_std` / `_rolling_sum`）
- `src/strategies/huangbai_b1_v2_strategy.py` - V2 策略（V4 复制自 V2，B1 变更需同步两处）
- `src/engine/portfolio_simulator.py` - 组合模拟器（`dongneng_recent` 买入条件，兼容 `stock_macd_bullish` 回退）
- `config.py` - MACD 参数、HUANGBAI_* 系列参数
- `main.py` - `huangbai_v4` 策略注册与三种运行模式分发

## 4. Attention

- B1 逻辑变更需同步三个位置：`HuangBaiB1V4Strategy.indicators()`、`_compute_signals()`、`_compute_all_bar_signals()`（与 V1/V2 相同的三处同步问题）
- 动能信号逻辑变更需同步三处中的 `_compute_dongneng_ok()` 调用。`_compute_dongneng_ok()` 本身是独立函数，三处共用
- V4 代码独立于 V2 文件，不共享函数引用。V2 的 B1 逻辑变更不会自动同步到 V4，需手动维护
- S1/大风车/长上影线排除（三处同步：`indicators()`、`_compute_signals()`、`_compute_all_bar_signals()`）：新增 `_long_upper_shadow` 检测（C>=HHV(C,20)*0.97 + 上影线>3% + 上影线>实体*2 + 量>前日*1.3），纳入 `no_s1_dafengche` 的 OR 关系
- S1 天量判定新增涨停后替代条件（同V1/V2）：近3日有涨停时量能只需 > 前日量*1.5，解决连续涨停拉高HHV基线问题
- `_compute_dongneng_ok()` 不含流通市值过滤（`dongneng_zhuan_strategy` 中的 `liutong_mask` 在 V4 中省略）
- `preload_all_signals()` 返回三元组，调用方需注意解包
- V4 不支持 `skip_gc` 参数（金叉条件已移除，该参数无意义）
- 日志标签为 `[B1V4]`，区别于 V2 的 `[B1V2]`
- 返回字典中 `stock_macd_bullish` 已替换为 `dongneng_recent`，PortfolioSimulator 通过 `.get()` 兼容两者
