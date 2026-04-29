# 图表层：组合模拟 K 线图生成器

## 1. Purpose

为组合模拟（`--portfolio` 模式）的交易结果生成 K 线蜡烛图。对每只交易过的股票生成独立的 PNG 图片，叠加白线/黄线/BBI 指标线、买卖标记、止损线和成本线。

## 2. How it Works

### 触发方式

所有黄白线策略（V1/V2/V3）的组合模拟模式均支持 `--chart` 参数：

```bash
python main.py --portfolio --chart
python main.py --strategy huangbai_v2 --portfolio --chart
python main.py --strategy huangbai_v3 --portfolio --chart
```

在 `main.py` 中，组合模拟完成后若 `args.chart` 为 True，调用 `generate_charts(report["trade_list"], sim._all_signals)`。

### 核心函数

| 函数 | 说明 |
|------|------|
| `generate_charts(trade_list, all_signals, output_dir)` | 按 code 分组交易，为每只股票调用 `_plot_single_stock()` |
| `_plot_single_stock(code, sig, trades, output_dir)` | 绘制单只股票的完整 K 线图（价格轴 + 成交量轴） |

### 图表结构

双面板布局（gridspec `height_ratios=[3,1]`）：

- **上方价格面板**：K 线蜡烛图 + 白线/黄线/BBI 指标线 + 买卖标记 + 止损线（橙色虚线）+ 成本线（蓝色点线）
- **下方成交量面板**：成交量柱状图，阳线红/阴线绿

### 数据依赖

`generate_charts()` 从 `all_signals[code]` 字典中读取以下字段：

| 字段 | 必需 | 说明 |
|------|------|------|
| `close`, `high`, `low` | 是 | 价格数据 |
| `dates` | 是 | 交易日序列 |
| `open` | 否（fallback close） | 开盘价，用于绘制蜡烛实体 |
| `volume` | 否（fallback zeros） | 成交量，用于绘制成交量面板 |
| `white`, `yellow`, `bbi` | 否 | 指标线，缺失则不绘制 |

`open` 和 `volume` 字段由 `_compute_all_bar_signals()` 在返回字典中提供。V1/V3 策略已新增这两个字段，V2 策略原本就包含。

### 样式

- 图表背景：白色（`ax.set_facecolor("white")`）
- 影线颜色：灰色（`#888888`）
- 图例背景：白色（`facecolor="white"`）
- 输出目录：`charts/`，文件名格式 `{code}_{start}_{end}.png`

### 输出

控制台打印生成统计：`图表生成完成: N 只  跳过 M 只  目录: charts/`

## 3. Relevant Code Modules

- `src/charting/__init__.py` - 模块入口，导出 `generate_charts`
- `src/charting/kline_chart.py` - K 线图绘制实现
- `main.py` - `--chart` 参数解析及各策略组合模拟完成后的调用点

## 4. Attention

- `generate_charts` 仅在 `--portfolio` 组合模拟模式下可用，单股回测和扫描模式不支持
- 图表依赖 matplotlib，使用 `Agg` 后端（无头模式），无需 GUI 环境
- `open` 字段缺失时 fallback 为 `close`，`volume` 缺失时 fallback 为零数组
- 绘图范围自动扩展：以首末交易日期为基准，前后各加 30 根 bar 的上下文
