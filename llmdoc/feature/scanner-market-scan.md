# 全市场选股扫描器

## 1. Purpose

独立于 Backtrader 引擎的全市场选股扫描模块。直接使用 MyTT 计算指标（不经过 Backtrader 逐 bar 机制），对全部 A 股执行黄白线 B1 策略的三级过滤，命中结果按缩量评分排序输出。

## 2. How it Works

### 架构定位

扫描器与回测引擎并行，不依赖 `Backtester` / `BaseStrategy`。核心计算函数 `compute_signals()` 将 B1 策略的指标逻辑完整复刻为纯 NumPy/MyTT 版本，跳过 Backtrader 的逐 bar 开销。

### 数据流

1. `_get_all_codes()` 扫描通达信 `vipdoc/sz/lday` 和 `vipdoc/sh/lday` 目录，提取全部 A 股代码（去重、去指数），约 5202 只
2. `scan_all()` 遍历每只股票：通过 mootdx `Reader` 读取日线数据 -> `compute_signals()` 计算最新 bar 的三级过滤结果
3. 命中结果按 `shrink_score`（当日成交量 / 20日最高量）升序排列

### 三级过滤（AND 关系）

| 级别 | 条件 | 说明 |
|------|------|------|
| 1 | 周线多头 | `MA30 > MA60 > MA120 > MA240` 且收盘价站上周线 MA30 |
| 2 | 黄白线金叉 | 近 `gc_lookback`（默认20）日内白线上穿黄线 |
| 3 | B1 信号 | 7 个子条件任一满足（OR），与 HuangBaiB1Strategy 一致 |

### 关键函数

| 函数 | 说明 |
|------|------|
| `compute_signals(C, H, L, O, V, dates, params)` | 核心信号计算，返回三级过滤结果 dict 或 None |
| `scan_all(stock_type, skip_weekly, skip_gc, tdxdir, market)` | 全市场扫描入口，返回命中列表 |
| `_get_all_codes(tdxdir, market)` | 从通达信目录提取 A 股代码 |
| `_weekly_ma(daily_close, dates, period)` | 日线转周线 MA（与策略中同名函数一致） |
| `_ref_at(S, offsets)` | 可变偏移 REF（与策略中同名函数一致） |

### 性能

扫描 5202 只股票约 3.5 分钟（单线程，逐只读取 + 计算）。

### CLI 入口

```bash
python main.py --scan                    # 全市场扫描
python main.py --scan --stock-type tech   # 仅创业板风格参数
```

`--scan` 模式下不进入回测流程，不需要 `--symbol` 参数。

## 3. Relevant Code Modules

- `src/scanner.py` - 扫描器主文件（compute_signals / scan_all / _get_all_codes）
- `main.py` - `--scan` CLI 参数解析与 scan_all 调用
- `src/strategies/huangbai_b1_strategy.py` - B1 策略原始实现（扫描器复刻其指标逻辑）
- `config.py` - TDX_DIR / TDX_MARKET 配置

## 4. Attention

- `compute_signals()` 与 `HuangBaiB1Strategy` 的 B1 逻辑是手动同步的，策略变更时需同步更新扫描器
- 数据不足 300 条的股票会被跳过（`compute_signals` 返回 None）
- `skip_weekly` / `skip_gc` 参数可在 `scan_all()` 中跳过对应过滤级，便于调试
- 缩量评分 `shrink_score = V[i] / HHV(V,20)[i]`，越小代表相对缩量越明显
