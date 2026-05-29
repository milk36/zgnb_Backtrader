# 策略层：ZStock B1 选股策略 + AI 评分 + 管道脚本

## 1. Purpose

参考 StockTradebyZ 项目的 4-Filter B1 选股逻辑，实现独立于黄白线体系的全新选股策略，并配套量化评分模块和一键管道脚本，串联"选股 -> AI评分 -> 组合级回测"全流程。

## 2. How it Works

### 4-Filter 选股逻辑（AND 关系）

B1 信号由 4 个独立 Filter 取交集产生：

1. **KDJQuantileFilter**：J < 15（绝对阈值）OR J <= expanding_quantile(J, 0.10)（历史分位数）。KDJ 使用通达信标准 SMA 计算
2. **ZXConditionFilter**：close > 黄线 AND 白线 > 黄线。白线 = EMA(EMA(C, 10), 10)，黄线 = (MA14 + MA28 + MA57 + MA114) / 4
3. **WeeklyMABullFilter**：日线重采样为周线（W-FRI）后 MA20 > MA60 > MA120
4. **MaxVolNotBearishFilter**：过去 20 日最大成交量日非阴线（C >= O）

### 双轨运行模式

- **`scan_all()`**：全市场选股扫描（仅最新 bar），通过 `ProcessPoolExecutor` 并行，结果按 shrink_score 升序排列
- **`preload_all_signals()`**：预加载全市场每 bar 信号数组，返回 `(all_signals, trading_days, market_macd_bullish)` 三元组，兼容 PortfolioSimulator

### 信号字典字段

`_compute_all_bar_signals()` 返回的字典包含 PortfolioSimulator 必需字段（`weekly_bull`, `b1`, `recent_gc`, `vol_expand_ok`, `no_huge_vol_bearish`, `shrink_score`, `white`, `yellow` 等），其中 `recent_gc` 和 `vol_expand_ok` 恒为全 True（ZStock 无金叉条件和放量过滤），`no_huge_vol_bearish` 复用 Filter 4。

### AI 评分模块（QuantitativeScorer）

四维加权评分体系：

| 维度 | 权重 | 评分范围 | 核心逻辑 |
|------|------|----------|----------|
| 趋势结构 | 0.20 | 1-5 | 均线多头排列 + MA20/MA60 斜率 |
| 价格位置 | 0.20 | 1-5 | 120 日区间位置百分比 + 突破平台检测 |
| 量价行为 | 0.30 | 1-5 | 上涨段阳线均量 vs 回调段阴线均量比 + 最大量K线阴阳 |
| 前期异动 | 0.30 | 1-5 | 异常放量阳线(量>2x均量+涨幅>3%) + 突破平台 + 区间涨幅 |

判定规则：PASS(>=4.0), WATCH(3.2-4.0), FAIL(<3.2 或 volume_score==1)。

批量接口：
- `batch_score(all_signals, ref_date, threshold)` — 对固定日期评分并过滤
- `batch_score_on_b1_dates(all_signals, threshold)` — 对每只股票的每个 B1 日评分，注入 `ai_score` / `ai_verdict` 字段

### 管道脚本（run_pipeline.py）

三阶段串行管道：

1. **Phase 1 扫描**：`preload_all_signals()` 预加载全市场信号 -> 保存 `signals.pkl`
2. **Phase 2 评分**：`batch_score()` 量化评分过滤 -> 保存 `scores.json`
3. **Phase 3 回测**：`PortfolioSimulator` 组合模拟 -> 保存 `report.txt`

支持 `--start-from` 从任意阶段恢复（复用中间文件），`--no-ai-score` 跳过评分。

### CLI 命令

```bash
# 纯选股扫描
python main.py --strategy zstock_b1 --scan-only

# 组合级模拟
python main.py --strategy zstock_b1 --portfolio

# 管道脚本（含AI评分）
python run_pipeline.py
python run_pipeline.py --no-ai-score --chart
python run_pipeline.py --start-from score
```

## 3. Relevant Code Modules

- `src/strategies/zstock_b1_strategy.py` - ZStock B1 策略主文件（4个Filter、双轨扫描/预加载、大盘MACD计算）
- `src/ai_scorer.py` - 量化评分模块（QuantitativeScorer 类 + batch_score 批量接口）
- `run_pipeline.py` - 管道脚本（三阶段串联 + 中间结果持久化）
- `config.py` - `ZSTOCK_*` 前缀参数常量
- `main.py` - `zstock_b1` 策略注册（STRATEGIES 值为 None）
- `src/engine/portfolio_simulator.py` - 组合级模拟器（ZStock 复用标准六级退出）
- `src/strategies/nxing_b1_scan_strategy.py` - `_get_all_codes()` 全市场股票代码列表
- `src/strategies/dongneng_zhuan_strategy.py` - `_load_capital_data()` 流通市值数据加载

## 4. Attention

- 无 Backtrader 策略类、无单股回测，STRATEGIES 注册值为 None
- ZStock B1 的 B1 条件（4-Filter）与黄白线系列（7子条件）完全独立，无共享代码
- 信号字典中 `recent_gc` 恒为全 True（无金叉条件），`vol_expand_ok` 恒为全 True（无放量过滤）
- 流通市值过滤在 `preload_all_signals` 的子进程中执行（>= 50 亿），`scan_all` 不做市值过滤
- 周线计算使用 `resample('W-FRI')`，与其他策略一致
- AI 评分的 `volume_score == 1` 直接判 FAIL，无视总分
- 管道脚本中间结果用 pickle/json 保存到 `pipeline_output/`，支持断点续跑
- `ZSTOCK_AI_SCORE_PASS=4.0`, `ZSTOCK_AI_SCORE_WATCH=3.2` 为判定阈值
