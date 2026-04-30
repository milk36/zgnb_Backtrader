# 策略层：黄白线金叉后B1策略 V3

## 1. Purpose

V2 的升级版本，核心变更：B1 买入信号替换为通达信原始选股公式（单一复合条件取代 V1/V2 的 7 子条件 OR 关系）。止盈止损逻辑与 V1/V2 完全相同。B1 计算通过 `_compute_v3_b1()` 共享函数统一策略类、扫描函数、预加载函数三处调用，消除 V1/V2 的手动同步问题。

## 2. How it Works

### 与 V2 的核心差异

V3 继承 V2 的全部策略框架（周线多头 + 大盘 MACD + 黄白线金叉 + 止盈止损），变更点：

| 维度 | V2 | V3 |
|------|----|----|
| B1 公式 | 7 子条件 OR 关系 | 通达信原始复合公式 |
| B1 代码组织 | 三处各自计算，需手动同步 | `_compute_v3_b1()` 共享函数 |
| 止盈止损 | 涨停优先于中阳 | 与 V1/V2 完全相同 |

### V3 B1 公式组件（`_compute_v3_b1`）

单一复合条件，由以下组件通过 AND/OR 组合：

1. **真阳真阴**：`REAL_YANG = C>O & ~(C<LC)`，`REAL_YIN = C<O & ~(C>LC)`
2. **KDJ 超卖**：`J = 3K - 2D`（SMA 平滑），`J_OK = J <= 13`
3. **阳量/阴量比值**：57日 `yangyin_ok1 = vol_yang > 1.25 * vol_yin`；14日 `yangyin_ok2 = vol_yang > 2.25 * vol_yin`
4. **高位放量跌过滤**：`GOOD28 = COUNT(TOP15O & FD15, 21) == 0`
5. **放量阳统计**：`PLRY = V>1.95*REF(V,1) & C>O & V>AVG40`；14日>=2 或 57日>=4
6. **放量阳细分 + 缩量下跌**：`THREE_SUM_OK = (CNT_FIRST + CNT_CONT + CNT_HALF) >= 4`
7. **28日最大量非阴线**：`MAX28_OK = COUNT(MAX28_BAD, 28) == 0`
8. **A1 组合**：`(PLRY_CNT & yangyin_ok1 & J_OK & MVOK & GOOD28 & THREE_SUM_OK & MAX28_OK) | (PLRY_CNT & yangyin_ok2 & J_OK & MVOK & GOOD28 & MAX28_OK)`
9. **B1 最终**：`HMSHORTWL >= HMLONGYL*0.985 AND C >= HMLONGYL*0.985 AND A1`

其中短期均线 `HMSHORTWL = SMA(SMA(C,40,4),100,50)`，长期均线 `HMLONGYL` 为两组加权 MA 的均值。

### 共享函数设计

`_compute_v3_b1(C, H, L, O, V, skip_mvok=True)` 返回：
- `b1: np.ndarray[bool]` — 每根 bar 的 B1 信号
- `shrink_score: np.ndarray[float]` — 缩量评分（候选排序用）
- `J: np.ndarray[float]` — KDJ-J 值（日志用）

三处调用点统一使用此函数：`HuangBaiB1V3Strategy.indicators()`、`_compute_signals()`、`_compute_all_bar_signals()`。

`_compute_all_bar_signals()` 返回字典包含 `open`、`volume` 和 `avg_amount_20`（20日成交额均值，流通市值代理指标）字段，分别供 K 线图模块绘制和 PortfolioSimulator 选股排序使用。

### PortfolioSimulator 适配

- V3 通过 `strategy_tag="[B1V3]"` 参数标识日志标签
- 止盈止损逻辑与 V1/V2 完全相同，PortfolioSimulator 无需额外参数

### CLI 入口

| 命令 | 说明 |
|------|------|
| `--strategy huangbai_v3 --portfolio` | 组合级模拟（预加载 + Simulator） |
| `--strategy huangbai_v3 --portfolio --chart` | 组合级模拟 + 生成交易K线图 |
| `--strategy huangbai_v3 --scan` | 全市场扫描 + 回测 |
| `--strategy huangbai_v3 --scan-only` | 仅扫描 |
| `--strategy huangbai_v3 --symbol 002475` | 指定股票回测（自动加载大盘指数 data1） |

## 3. Relevant Code Modules

- `src/strategies/huangbai_b1_v3_strategy.py` — V3 策略主文件（策略类、B1 共享函数、大盘 MACD 函数、扫描函数、预加载函数）
- `src/strategies/huangbai_b1_v2_strategy.py` — V2 策略（V3 复用其大盘 MACD 函数的设计模式）
- `src/strategies/huangbai_b1_strategy.py` — V1 策略（B1 七子条件的原始实现，V3 不再使用）
- `src/engine/portfolio_simulator.py` — 组合模拟器（通过 `strategy_tag` 参数标识策略版本）
- `main.py` — `huangbai_v3` 策略注册与四种运行模式分发
- `config.py` — 策略参数配置

## 4. Attention

- `_compute_v3_b1()` 是 V3 的核心共享函数，B1 逻辑变更只需修改此函数一处
- `scan_all()` 和 `preload_all_signals()` 返回格式与 V2 相同（三元组），大盘 MACD 逻辑完全复用 V2
- V3 指定股票回测时自动加载大盘指数作为 `data1`，加载失败跳过大盘过滤并打印警告
- V3 流通市值过滤默认跳过（`skip_mvok=True`），因周线多头已包含此过滤
- 缩量评分（`shrink_score = V / HHV(V,20)`）用于扫描结果排序，值越小代表缩量越明显
- 止盈止损逻辑与 V1/V2 完全一致：涨停优先于中阳，涨停不受中阳标记限制
