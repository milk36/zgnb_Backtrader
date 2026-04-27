# 策略层：黄白线金叉后B1策略

## 1. Purpose

周线多头空间 + 黄白线金叉 + B1买入信号的中短线策略。通过周线级别趋势过滤、日线级别黄白线关系判定入场时机，以7种B1子条件捕捉超卖回踩买点，配合分层止盈止损管理仓位。

## 2. How it Works

### 入场三重过滤（AND关系）

1. **周线多头空间**：日线收盘价经 `resample('W-FRI')` 转为周线后，要求 `MA30 > MA60 > MA120 > MA240`，且收盘价站上周线MA30
2. **黄白线金叉**：近 `gc_lookback`（默认20）日内白线上穿黄线
   - 白线 = `EMA(EMA(C,10),10)`
   - 黄线 = `(MA14 + MA28 + MA57 + MA114) / 4`（BBI式均线）
3. **B1信号**：以下7个子条件任一满足（OR）

### B1 七个子条件

| # | 名称 | 核心逻辑 |
|---|------|---------|
| 1 | 超卖缩量拐头B | RSI/J拐头+超卖+缩量+异动+上升趋势 |
| 2 | 超卖缩量B | J<14或RSI<23 + 缩量 + 异动 |
| 3 | 原始B1 | 白线>黄线 + J<13或RSI<21 + 极度缩量 + 振幅异动 |
| 4 | 超卖超缩量B | 超卖 + 超缩量 + 远期振幅>=45% |
| 5 | 回踩白线B | 强趋势 + 回踩白线<=2% + 缩量 + 异动 |
| 6 | 回踩超级B | 超级牛市特征 + 回踩持仓 + 异动 |
| 7 | 回踩黄线B | 回踩黄线 + 超卖 + 缩量 + MA60上升 |

### 关键辅助指标

- **SHORT/LONG**：短期(3日)/长期(21日)位置百分位，用于超卖/高位判断
- **RSI**：`SMA(MAX(C-LC,0),3,1) / SMA(ABS(C-LC),3,1) * 100`（通达信标准）
- **异动(anomaly)**：近期振幅>=15% 或 远期振幅>=30% 或 洗盘异动（needle_20/treasure/dbl_fork）
- **缩量等级**：shrink(<41.6%) / pb_shrink(<45%) / mod_shrink(<61.8%) / sup_shrink(<25%或<1/6)

### 出场逻辑（优先级从高到低）

1. **止损**：白线上方买入 -> 买入日最低价止损；白线黄线之间 -> 黄线价止损
2. **T+3没涨清仓**：持仓>=3日且价格<=买入价，全仓清出
3. **持股至跌破白线**：仓位已减至半仓后，跌破白线全清
4. **涨停卖1/2**：涨停板时卖出半仓，触发条件3的持股模式
5. **中阳卖1/3**：涨幅达标（主板5%/创业板10%）卖1/3，若剩余<=半仓触发条件3

### 调试参数

- `skip_weekly=True`：跳过周线过滤，仅测试B1信号
- `skip_gc=True`：跳过金叉过滤

### 日志输出格式

所有交易日志和过滤结果均带股票代码前缀：

- 交易日志：`[日期] 代码 BUY @ 价格 SL=止损价`
- 过滤结果：`[日期] 代码 周线=Y/N 金叉=Y/N B1=Y/N C=价格 J=值 RSI=值 <<< SELECT`
- `_print_filter_result()` 仅在 B1 信号触发或三级全部通过时输出

## 3. Relevant Code Modules

- `src/strategies/huangbai_b1_strategy.py` - 策略主文件（HuangBaiB1Strategy类、_weekly_ma、_ref_at辅助函数）
- `src/strategies/base_strategy.py` - 基类（停牌/涨跌停过滤、订单管理）
- `src/indicators/kdj_indicator.py` - KDJ指标（提供J值用于B1条件）
- `config.py` - HUANGBAI_* 系列参数
- `main.py` - `--strategy huangbai --stock-type main/tech` 入口

## 4. Attention

- 策略覆写了 `next()` 而非使用 `buy_signal()/sell_signal()` 模板方法，因出场逻辑涉及分批卖出和状态管理
- `_weekly_ma()` 将日线重采样为周线计算MA再映射回日线，注意 `resample('W-FRI')` 以周五为周结束日
- `stock_type` 影响振幅阈值（主板5%/创业板8%）和中阳判断（主板5%/创业板10%）
- B1条件依赖大量MyTT函数（HHV/LLV/EVERY/EXIST/COUNT/BARSLAST/HHVBARS），确保MyTT版本兼容
- `position_pct` 默认0.1（10%仓位），与KDJ策略的0.9不同，设计上采用多笔小仓位
- `log()` 方法自动从 `self.data._name` 获取股票代码，所有日志行均包含代码标识
- B1 指标逻辑在 `src/scanner.py` 中有独立的纯 MyTT 复刻版本，策略变更时需同步
