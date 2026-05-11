# 策略层：N型+砖策略

## 1. Purpose

N型砖策略是动能砖策略的变体，仅使用金砖共振信号（砖型图、绿转强红、黄柱动能）选股，跳过动能预过滤和筹码密集过滤，外加流通市值>50亿过滤。相比动能砖的三重过滤（动能+金砖+筹码），N型砖的选股范围更宽，意在捕捉更多金砖共振机会。

## 2. How it Works

### 与动能砖的差异

| 维度 | 动能砖 | N型砖 |
|------|--------|-------|
| 选股过滤 | dongneng_recent(5日动能) + jinzhuan_ok(金砖) + chip_dense(筹码密集) | jinzhuan_ok(金砖) + liutong_mask(流通市值>50亿) |
| 入场确认 | T+1 分钟线确认买入（前3根5分钟bar阳线确认） | T+1 日线开盘买入（无分钟确认） |
| 模拟器 | DongnengZhuanSimulator（strategy_tag="动能砖"） | DongnengZhuanSimulator（strategy_tag="N型砖"） |
| 退出逻辑 | 五级退出（相同） | 五级退出（相同） |

### 信号计算

`_compute_all_bar_signals()` 复用动能砖的 `_dnzh_compute()` 获取完整信号字典，然后覆盖 `any_ok` 和 `rank_score`：

1. 调用 `_dnzh_compute(C, H, L, O, V, dates, code, params)` 获取全部信号
2. 取 `jinzhuan_ok` 数组（金砖共振信号）
3. 计算流通市值过滤：`liutong_mask = market_cap > NXZH_MIN_MARKET_CAP`
4. 覆盖最终信号：`nxing_ok = jinzhuan_ok & liutong_mask`
5. 覆盖排名分数：`rank_score = brick / max(pct_chg, 0.01)`（仅 nxing_ok 时有效）

关键区别：`any_ok` 从 `dongneng_recent & jinzhuan_ok & chip_dense` 变为 `jinzhuan_ok & liutong_mask`。

### 配置参数（config.py NXZH_* 系列）

| 参数 | 默认值 | 说明 |
|------|--------|------|
| NXZH_INITIAL_CASH | 100,000 | 初始资金 |
| NXZH_MAX_POSITIONS | 2 | 最多持仓数 |
| NXZH_PER_POSITION | 50,000 | 单只最大买入金额 |
| NXZH_T_PLUS_N | 2 | 不拉升清仓天数 |
| NXZH_MAX_HOLD_DAYS | 6 | 盈利后最大持仓天数 |
| NXZH_PROFIT_PCT | 5.0 | 脱离成本区百分比 |
| NXZH_STOP_LOSS_PCT | 2.0 | 止损百分比（买入价下跌2%） |
| NXZH_MIN_MARKET_CAP | 50.0 | 流通市值最低阈值（亿元） |
| NXZH_MINUTE_ENTRY_ENABLED | False | **禁用**分钟级入场确认（T+1开盘买入） |
| NXZH_MINUTE_EXIT_ENABLED | True | 启用分钟级止损/涨停检查 |

### 模拟器复用

N型砖复用 `DongnengZhuanSimulator`，通过参数差异化：

- `strategy_tag="N型砖"`：日志和报告中的策略标签
- `minute_entry_enabled=False`：跳过分钟确认，直接以日线开盘价买入
- `stop_loss_pct=2.0`（动能砖为4.0）：更紧的止损
- `max_hold_days=6`（动能砖为5）：持仓略长

### 全市场扫描与预加载

| 函数 | 说明 |
|------|------|
| `_compute_all_bar_signals()` | 向量版信号计算，复用动能砖后覆盖 any_ok/rank_score |
| `_compute_signals()` | 最新bar信号（取 _compute_all_bar_signals 最后一个值） |
| `scan_all()` | 全市场扫描，多进程并行，输出 "N型砖" 标签结果 |
| `preload_all_signals(start, end)` | 并行预计算全部A股每bar信号，返回 (all_signals, trading_days) |

`scan_all` 和 `preload_all_signals` 均从 `dongneng_zhuan_strategy` 导入 `_get_all_codes` 和 `_load_capital_data`，流通市值阈值使用 `NXZH_MIN_MARKET_CAP`。

## 3. Relevant Code Modules

- `src/strategies/nxing_zhuan_strategy.py` - N型砖策略主文件（信号计算、扫描、预加载）
- `src/strategies/dongneng_zhuan_strategy.py` - 被复用的 `_compute_all_bar_signals`/`_get_all_codes`/`_load_capital_data`
- `src/engine/dongneng_zhuan_simulator.py` - 组合模拟器（通过 strategy_tag 和参数差异化复用）
- `config.py` - NXZH_* 系列参数
- `main.py` - 策略注册（`"nxing_zhuan": None`）及运行入口

## 4. Attention

- N型砖不支持单股 Backtrader 回测（STRATEGIES 字典中值为 None）
- 信号计算依赖动能砖的 `_compute_all_bar_signals`，金砖引擎的任何修改会同时影响两个策略
- `NXZH_MINUTE_ENTRY_ENABLED=False` 是与动能砖的核心差异，模拟器中 `_execute_pending_buys` 将直接以日线开盘价买入
- `NXZH_MIN_MARKET_CAP` 使用独立参数而非 `DNZH_MIN_MARKET_CAP`，两者可独立调整
- 退出逻辑完全由模拟器控制，五级退出优先级与动能砖相同（止损→涨停清仓→涨幅2%卖1/4→T+N不拉升→盈利止盈）
