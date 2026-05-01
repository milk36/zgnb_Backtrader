# 策略层：动能+砖策略

## 1. Purpose

双引擎串行过滤策略：先由动能评分引擎（KDJ/RSI动量、Z-Score、筹码流量、综合天命打分）选出股票池，再在动能池中由金砖共振引擎（砖型图、绿转强红、黄柱动能）进一步筛选，最终通过筹码密集过滤。最终股票按金砖排名"下大上小"取前N只，T+1开盘买入，四级退出（止损→涨停清仓→T+N不拉升→盈利止盈）。

## 2. How it Works

### 选股流程（串行过滤）

先动能筛选，再在动能池中金砖筛选，最后筹码密集过滤（AND 逻辑，串行过滤）：

| 阶段 | 引擎 | 核心思路 | 关键指标 |
|------|------|---------|---------|
| 第1步 | 动能 | KDJ/RSI动量增量 × 影线/量价加成 → Z-Score + 筹码流量 → 综合打分 → 阵营过滤 | BASE_MOM, X_MOM, VISUAL_SCORE, RET_Z, OVERHEAD_V20 |
| 第2步 | 金砖 | 砖型图 → 强红判定(绿转红>2/3) → 共振条件(黄柱/X动能 + 存在B) → 趋势+上影线 | 砖型图, 强红, 黄柱, X动能(jz版), 存在B(7子条件) |
| 第3步 | 流通市值 | 从 base.dbf 读取 LTAG，配合 DNZH_MIN_MARKET_CAP 过滤低流通市值 | LTAG, DNZH_MIN_MARKET_CAP |
| 第4步 | 筹码密集 | 60日滚动VWAP近似筹码密集度，spread 近似 COST() | VWAP60, spread60, _conc_low60, _price_near_center |

### 动能选股流程

1. **动量增量**: N1=KDJ_J增量, N2=RSI3增量
2. **影线/量价加成**: SHADOW_COEF × VOL_BONUS
3. **BASE_MOM**: (N1+N2)/2 × 影线 × 量价
4. **X_MOM**: 动量增量的增量（二阶导）
5. **防守**: 45日Z-Score + 20日套牢筹码流量(OVERHEAD_V20)
6. **综合打分**: VISUAL_SCORE = BASE_MOM + 15×NORM_BONUS - (20×NORM_J + 30×NORM_RSI + 35×NORM_V20 + 10×NORM_RETZ) + 10
7. **阵营过滤**: MASK_A(高动能高评分) / MASK_B(中评分高动能) / MASK_C(低评分超高动能) / MASK_D(高X动量低BASE)
8. **硬性过滤**: 上影<30%, 下影<35%, 涨幅>=3%, Z>=0.8
9. **流通市值过滤**: `liutong_mask = market_cap > DNZH_MIN_MARKET_CAP`，`dongneng_ok` 需满足此条件

### 金砖选股流程

1. **砖型图**: VAR1A-VAR6A 自定义指标，值>4时取减4
2. **强红**: 今日红(砖>昨日) AND 昨日绿 AND 红柱长度/昨绿长度 > 0.666
3. **存在B**: 与黄白线B1相同的7个子条件（超卖缩量拐头/超卖缩量/原始B1/超卖超缩量/回踩白线/回踩超级/回踩黄线）
4. **金砖动量**: 黄柱=(N1+N2)/2×影线系数×倍量系数加成, X动能=二阶动量×影线×成交量系数×倍量
5. **共振条件**: 强红 AND (黄柱>=10 OR X动能>=10) AND (存在B近2日 OR LONG/SHORT极值)
6. **买入条件**: 共振 AND 趋势条件 AND 上影线条件 AND 换手 AND 非涨停

### 排名逻辑："下大上小"

- 统一使用金砖排名: `砖型图 / max(涨幅, 1)` — 砖大涨幅小优先
- 仅动能和金砖同时通过的股票才进入排名

### 筹码密集过滤（COST近似）

使用60日滚动VWAP近似通达信 COST() 函数：
1. **VWAP60**: `_sum_cv60 / _sum_v60`，即60日成交量加权平均价
2. **spread60**: `(HHV(C,60) - LLV(C,60)) / VWAP60 * 100`，衡量价格分散度
3. **_conc_low60**: spread60 处于60日内最低值（筹码最密集）
4. **_price_near_center**: 当前价距VWAP不超过10%
5. **chip_dense** = `_conc_low60 & _price_near_center`

最终信号：`final_ok = dongneng_ok & jinzhuan_ok & chip_dense`

### 流通市值过滤

- `_load_capital_data(tdxdir)`: 解析通达信 `T0002/hq_cache/base.dbf` 获取 LTAG（流通股本，万股）
- `capital_shares * C / 10000` 计算流通市值（亿元）
- `liutong_mask` 在 `dongneng_ok` 条件中作为必须条件
- 阈值由 `config.py` 的 `DNZH_MIN_MARKET_CAP` 控制（默认50亿元）

### 全市场扫描与预加载

| 函数 | 说明 |
|------|------|
| `_compute_all_bar_signals(C,H,L,O,V,dates,code,params)` | 向量版信号计算，返回 dongneng_ok/jinzhuan_ok/any_ok(=动能&金砖)/rank_score 等数组 |
| `_compute_signals(...)` | 最新bar信号（调用 _compute_all_bar_signals 取最后一个值） |
| `scan_all()` | 全市场扫描，多进程并行，按排名分数降序输出。加载 capital_data 传入 workers |
| `preload_all_signals(start, end)` | 并行预计算全部A股每bar信号，返回 (all_signals, trading_days)。加载 capital_data 传入 workers |

## 3. Relevant Code Modules

- `src/strategies/dongneng_zhuan_strategy.py` - 策略主文件（信号计算、扫描、预加载）
- `src/engine/dongneng_zhuan_simulator.py` - 组合模拟器
- `config.py` - DNZH_* 系列参数
- `thinking/动能砖.md` - 策略原始文档（通达信公式）

## 4. Attention

- 金砖的"存在B"七子条件与黄白线B1策略逻辑相同，但趋势/距离等指标独立计算
- 动能和金砖使用不同的影线系数和量价加成公式
- `code` 参数传入 `_compute_all_bar_signals` 用于判断板块类型（影响振幅/放宽系数/非涨停阈值）
- OVERHEAD_V20（套牢筹码流量）使用 REF(SUM(...),1) 即前一日的累计值
- 流通市值过滤已启用：通过 `_load_capital_data()` 解析 base.dbf 获取 LTAG，配合 `DNZH_MIN_MARKET_CAP=50.0`（亿元）过滤
- 筹码密集过滤已启用：使用60日滚动VWAP近似 COST()，`chip_dense` 作为 `final_ok` 的必要条件
- `_load_capital_data()` 在 base.dbf 不存在时返回 None，此时 `liutong_mask` 为全 True（不过滤）
