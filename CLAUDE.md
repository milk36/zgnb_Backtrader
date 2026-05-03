# CLAUDE.md - 量化回测系统开发指南

## 变更记录 (Changelog)

| 日期 | 操作 | 说明 |
|------|------|------|
| 2026-04-29 | 初始创建 | 基于项目全面扫描生成 |

## 项目愿景

基于 Backtrader + MyTT + mootdx 的本地量化回测系统，从通达信本地数据读取行情，用通达信标准公式计算指标，支持全市场选股扫描与组合级日频模拟回测。

## 常用命令

```bash
# 黄白线B1组合级模拟（推荐模式）
python main.py --portfolio

# 组合级模拟 + K线图
python main.py --portfolio --chart

# V2策略（含大盘MACD过滤）
python main.py --strategy huangbai_v2 --portfolio --chart

# V3策略（通达信原始B1公式）
python main.py --strategy huangbai_v3 --portfolio --chart

# 全市场选股扫描（仅看结果）
python main.py --scan-only

# 指定股票回测
python main.py --symbol 002475

# 动能+砖策略组合模拟
python main.py --strategy dongneng_zhuan

# 动能+砖策略扫描
python main.py --strategy dongneng_zhuan --scan-only
```

## 架构总览

```
main.py (CLI入口, argparse参数解析, 4种运行模式分发)
  config.py (集中配置, 非YAML)
  src/
    data/
      tdx_feed.py        -- mootdx Reader封装, 日线数据标准化
      minute_feed.py     -- 5分钟K线数据(动能砖策略专用)
    indicators/
      kdj_indicator.py   -- MyTT批量计算模式的指标范例
    strategies/
      base_strategy.py   -- 模板基类(3个抽象方法)
      kdj_cross_strategy.py
      huangbai_b1_strategy.py      -- V1: 含scan_all/preload_all_signals
      huangbai_b1_v2_strategy.py   -- V2: +大盘MACD过滤
      huangbai_b1_v3_strategy.py   -- V3: +共享B1函数消除同步问题
      dongneng_zhuan_strategy.py   -- 动能+砖: 双引擎串行过滤
    engine/
      backtester.py              -- 单股Backtrader Cerebro封装
      portfolio_simulator.py     -- 黄白线系列组合模拟器(100万/10只)
      dongneng_zhuan_simulator.py -- 动能砖组合模拟器(10万/2只, T+1分钟确认)
    charting/
      kline_chart.py     -- 组合模拟交易K线图生成(matplotlib)
```

## 核心架构要点

### 指标计算模式：MyTT批量 vs Backtrader逐bar

所有指标在策略 `__init__` 中用 MyTT 一次性计算完整数组，`next()` 中按索引取值。信号判断使用 Backtrader 原生 `CrossOver` 保持逐bar语义。这意味着自定义指标不应使用 Backtrader 的逐bar计算机制。

### 策略的双轨运行模式

每个黄白线策略文件同时包含 Backtrader 策略类和独立于 Backtrader 的模块级函数：

- **Backtrader策略类**：用于 `--symbol` 单股回测，继承 `BaseStrategy`
- **`_compute_signals()`**：最新bar信号计算（纯NumPy/MyTT），用于 `--scan` 扫描
- **`_compute_all_bar_signals()`**：向量版信号计算（每bar数组），用于 `--portfolio` 组合模拟

B1逻辑变更时，V1/V2需同步三处（`indicators()`、`_compute_signals()`、`_compute_all_bar_signals()`）。V3通过 `_compute_v3_b1()` 共享函数解决了此问题。

### 两种组合模拟器

| 模拟器 | 策略 | 资金/仓位 | 特点 |
|--------|------|-----------|------|
| `PortfolioSimulator` | 黄白线V1/V2/V3 | 100万/10只/每只10万 | 月更新观察池, 六级退出 |
| `DongnengZhuanSimulator` | 动能+砖 | 10万/2只/每只5万 | T+1分钟确认买入, 五级退出 |

两者都是纯 numpy/pandas 日频模拟引擎，不依赖 Backtrader。

### 止盈止损优先级

**黄白线系列**（6级）：止损 -> T+N没涨 -> 盈利100%清仓 -> 半仓持股模式 -> 涨停卖1/2 -> 中阳卖1/3

**动能+砖**（5级）：止损 -> 涨停清仓 -> 涨幅2%部分卖出 -> T+N不拉升 -> 盈利止盈

### 数据依赖

系统从通达信本地目录读取数据（`config.py` 的 `TDX_DIR`），必须先下载对应股票的日线数据。扫描约5202只A股。

## 运行与开发

### 前置条件

- Python 3.8+
- 通达信客户端本地安装，修改 `config.py` 中 `TDX_DIR` 路径
- `pip install -r requirements.txt`

### 策略注册

在 `main.py` 的 `STRATEGIES` 字典中注册策略类。不支持单股回测的策略设为 `None`（如 `dongneng_zhuan`）。

### 新增策略

继承 `BaseStrategy` 并实现 `indicators()`、`buy_signal()`、`sell_signal()` 三个方法。复杂策略（分批卖出、状态管理）可直接覆写 `next()`。详见 `llmdoc/sop/how-to-add-new-strategy.md`。

### stock_type 参数

`--stock-type main/tech` 影响振幅阈值（主板5%/科创板8%）、中阳判断（主板5%/科创板10%）、涨停板计算（主板10%/科创板20%）。动能砖策略从股票代码自动推断。

## 编码规范

- 配置集中在 `config.py`，不用 YAML 避免额外依赖
- 策略参数通过 `params` 元组声明，不硬编码
- 缩量评分 `shrink_score = V / HHV(V,20)`，值越小代表缩量越明显
- 买入股数按100股整手计算
- 组合模拟分两阶段执行：Phase 1 预加载全市场信号（约45-55秒），Phase 2 逐日模拟

## AI 使用指引

### 必读文档

遇到具体模块问题时，优先查阅 `llmdoc/feature/` 下对应的文档，而非直接读源码：

- 运行命令不清楚 -> `llmdoc/feature/cli-usage.md`
- 数据获取问题 -> `llmdoc/feature/data-tdx-feed.md`
- 策略逻辑问题 -> `llmdoc/feature/strategy-huangbai-b1.md`（或对应版本）
- 回测引擎问题 -> `llmdoc/feature/engine-backtester.md`
- K线图问题 -> `llmdoc/feature/charting-kline.md`
- 新增策略SOP -> `llmdoc/sop/how-to-add-new-strategy.md`

### 关键注意事项

- 修改黄白线B1逻辑时，V1/V2需同步三处函数（见上文），V3只需改 `_compute_v3_b1()`
- 组合模拟器中的卖出逻辑必须与对应策略类保持一致
- `_compute_signals()` 和 `indicators()` 是同一逻辑的两种实现，修改一处必须同步另一处
- V2/V3的 `scan_all()` 和 `preload_all_signals()` 返回三元组（含 `market_macd_bullish`），V1返回二元组
- 周线计算使用 `resample('W-FRI')` 以周五为周结束日
- 所有策略最后一个交易日只卖不买,因为A股不支持T+0,避免组合模拟器中最后一天无交易记录
- 所有组合回测报告中只统计单支股票的盈亏情况来计算胜率,并且基于每支股票的盈利情况从高到底排序

### 术语

- S1: S1 是啥？就是个股走加速的时候，不管是建仓波、拉升波还是冲刺波，只要涨得越来越快（斜率变大，动不动就 7 个点、8 个点的长阳），然后出现放天量或巨量的大阴线;
- 大风车: 1.历史天量;2.连续流畅拉升加速之后的;3.长上下影的阴线+阴量; 通常也是S1的一种类型; 具体可以参考这些股当时的量价关系:港股小米-H01810 2025-02-27,A股恒生电子-600570 2025-02-17,A股中信证券-600030 2024-10-09
