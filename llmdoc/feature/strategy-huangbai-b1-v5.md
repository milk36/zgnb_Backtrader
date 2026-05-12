# 策略层：黄白线B1策略 V5（战法退出逻辑）

## 1. Purpose

V2 策略的退出逻辑重写版本，买入条件与 V2 完全相同（周线多头 + 大盘MACD + 黄白线金叉 + B1七子条件 + 放量过滤），但出场逻辑替换为基于文章战法的六级退出体系。引入关键K、缩量保护、加速检测等新概念，移除 T+N没涨清仓、盈利100%连跌清仓、动量持股、半仓持股模式等 V2 出场机制。

## 2. How it Works

### 与 V2 的核心差异

| 对比项 | V2 | V5 |
|--------|----|----|
| 买入逻辑 | 五重 AND | 完全相同 |
| 出场逻辑 | 六级止盈止损 + 动量持股 + 半仓持股 | **六级战法退出（全部重写）** |
| T+N没涨清仓 | 有 | **移除** |
| 盈利100%连跌清仓 | 有 | **移除** |
| 动量持股 | 有 | **移除** |
| 半仓持股模式 | 有 | **移除** |
| 关键K概念 | 无 | **新增** |
| 缩量不卖保护 | 无 | **新增** |
| 加速检测 | 无 | **新增** |

### 六级战法退出（L1~L6 优先级递减）

| 级别 | 名称 | 条件 | 动作 | 例外 |
|------|------|------|------|------|
| L1 | 硬止损 | 价格 <= 止损价 | 全部清仓 | 无（无条件执行） |
| L2 | 放量跌停 | 量能 > MA(V,20)*1.5 且 收盘 <= 跌停价 | 全部清仓 | 无 |
| L3 | S1信号卖出 | 已加速 + 放量阴线(量>HHV(V,20)*2或>MA(V,60)*3, 收跌, 实体>3%) | 全部清仓 | 缩量+在关键K内 |
| L4 | 两根平行中阴线 | 连续两日收阴(实体>2.5%) + 局部高位(>=HHV(H,20)*0.97) | 全部清仓 | 无 |
| L5 | 白线次日确认 | 前一日跌破白线 + 当日未收回(仍<白线) | 全部清仓 | 缩量+关键K内 / 未加速+缩量 |
| L6a | 涨停减仓 | 最高价 >= 涨停价 | 卖出1/3 | - |
| L6b | 大涨减仓 | 盈利>10% + 当日上涨（仅一次） | 卖出1/3 | `surge_reduction_done` 标记防重复 |

### 核心概念

- **关键K**：买入时向后扫描30根K线，找到最显著的放量阳线（显著性 = 成交量 * 涨幅百分比），取其开盘价~最高价作为支撑区间。持有期间出现更显著的放量阳线则替换
- **缩量不卖**：当日成交量 < MA(V,20)*0.618 或 < HHV(V,50)/3 时视为缩量，L3/L5 在缩量时可豁免卖出
- **加速检测**：买入后逐bar增量扫描，5日涨幅 > 15% 标记为加速。加速后对 S1/白线破位更严格

### V5 策略状态变量（Backtrader 策略类 + Position 类共用）

| 字段 | 类型 | 说明 |
|------|------|------|
| `key_k_high` / `key_k_low` / `key_k_bar` | float/int | 关键K的最高价、最低价（支撑下沿=开盘价）、bar索引 |
| `white_break_pending` | bool | 白线破位待确认标记 |
| `white_break_bar` | int | 白线破位发生bar |
| `has_accelerated` | bool | 是否已检测到加速 |
| `max_price_since_buy` | float | 买入后最高价 |
| `_surge_reduction_done` | bool | 大涨减仓是否已执行 |

### 函数签名

| 函数 | 返回值 |
|------|--------|
| `scan_all()` | `(results, market_macd_ok)` -- 与 V2 相同 |
| `preload_all_signals()` | `(all_signals, trading_days, market_macd_bullish)` -- 三元组 |

`_compute_all_bar_signals()` 返回字典在 V2 基础上新增字段：`ma_v20`、`hhv_v20`、`hhv_v50`、`ma_v60`、`hhv_h20`，供 PortfolioSimulator 的 V5 退出逻辑直接使用。

### PortfolioSimulator 集成

- `strategy_tag="[B1V5]"` 触发 V5 专用路径
- Position 类新增 V5 字段（`key_k_*`、`white_break_*`、`has_accelerated`、`max_price_since_buy`）
- `_identify_key_k_at_buy()`：买入时识别关键K
- `_check_exits_v5()`：替代默认的 `_check_exits()`，实现六级战法退出
- 板块识别从股票代码自动推断（`code[:2] in ("30","68")` 为科创板/创业板）

## 3. Relevant Code Modules

- `src/strategies/huangbai_b1_v5_strategy.py` - V5 策略主文件（策略类、大盘 MACD 函数、扫描函数、预加载函数）
- `src/strategies/huangbai_b1_v2_strategy.py` - V2 策略（V5 买入逻辑复制自 V2）
- `src/engine/portfolio_simulator.py` - 组合模拟器（Position 类 V5 字段、`_check_exits_v5`、`_identify_key_k_at_buy`、`_update_key_k_for_position`）
- `config.py` - MACD 参数、HUANGBAI_* 系列参数
- `main.py` - `huangbai_v5` 策略注册与三种运行模式分发

## 4. Attention

- B1 逻辑变更需同步三个位置：`HuangBaiB1V5Strategy.indicators()`、`_compute_signals()`、`_compute_all_bar_signals()`（与 V1/V2 相同的三处同步问题）
- V5 代码独立于 V2 文件，B1/买入逻辑变更不会自动同步，需手动维护
- S1/大风车/长上影线排除（三处同步：`indicators()`、`_compute_signals()`、`_compute_all_bar_signals()`）：新增 `_long_upper_shadow` 检测（C>=HHV(C,20)*0.97 + 上影线>3% + 上影线>实体*2 + 量>前日*1.3），纳入 `no_s1_dafengche` 的 OR 关系
- S1 天量判定新增涨停后替代条件（同V1/V2）：近3日有涨停时量能只需 > 前日量*1.5，解决连续涨停拉高HHV基线问题
- V5 退出逻辑仅在 Backtrader 策略类 `_check_exit()` 和 PortfolioSimulator `_check_exits_v5()` 两处实现，两者必须保持一致
- 关键K的支撑下沿取的是开盘价（`key_k_low = O[best_idx]`），不是最低价
- `_compute_all_bar_signals()` 返回的预计算量能指标（`ma_v20` 等）是 V5 新增，其他版本策略不使用这些字段
- L4 两根中阴线判断中的"局部高位"阈值为 `HHV(H,20) * 0.97`，即接近20日新高
- L6b 大涨减仓与 V2 的中阳卖出不同：V5 只看盈利>10%+当日上涨，不看中阳形态
