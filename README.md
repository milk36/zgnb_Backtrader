# Backtrader + MyTT + mootdx 量化回测系统

基于 Backtrader 框架的本地量化回测系统，使用 mootdx 读取通达信本地行情数据，使用 MyTT 计算技术指标（通达信标准公式），支持自定义策略开发、全市场选股扫描与组合级模拟回测。

## 功能特性

- **本地数据源**：通过 mootdx 直接读取通达信本地日线数据，无需联网
- **通达信标准指标**：基于 MyTT 库计算指标，公式与通达信一致（如 KDJ 使用 SMA 而非 EMA）
- **策略模板基类**：提供 `BaseStrategy` 模板，新策略只需实现 `indicators()`、`buy_signal()`、`sell_signal()` 三个方法
- **全市场选股扫描**：多线程并行扫描约 5200 只 A 股，按条件筛选并排序输出
- **组合级模拟**：100 万资金、最多 10 只、每只 10 万的组合日频模拟引擎，模拟真实交易流程
- **内置策略**：KDJ 金叉/死叉策略、黄白线金叉后 B1 策略（含 7 种买入子条件和分层止盈止损）
- **回测报告**：自动输出夏普比率、最大回撤、收益率、交易统计等分析指标

## 项目结构

```
├── main.py                          # CLI 入口，参数解析
├── config.py                        # 集中配置（通达信路径、资金、策略参数）
├── requirements.txt                 # 依赖清单
├── src/
│   ├── data/
│   │   └── tdx_feed.py              # 数据层：通达信数据读取与标准化
│   ├── indicators/
│   │   └── kdj_indicator.py         # 指标层：KDJ 自定义指标
│   ├── strategies/
│   │   ├── base_strategy.py         # 策略基类模板
│   │   ├── kdj_cross_strategy.py    # KDJ 金叉/死叉策略
│   │   └── huangbai_b1_strategy.py  # 黄白线 B1 策略 + 全市场扫描 + 信号预加载
│   └── engine/
│       ├── backtester.py            # 单股回测引擎（Backtrader Cerebro 封装）
│       └── portfolio_simulator.py   # 组合级日频模拟引擎
└── llmdoc/                          # 项目文档
```

## 安装

### 前置条件

- Python 3.8+
- 通达信客户端（需本地安装，用于提供行情数据）

### 安装依赖

```bash
pip install -r requirements.txt
```

### 配置

修改 `config.py` 中的通达信安装路径：

```python
TDX_DIR = r"D:\Tools\tdx_64"  # 改为你的通达信安装目录
```

## 使用方式

### 指定股票回测

```bash
# 黄白线 B1 策略回测指定股票
python main.py --strategy huangbai --symbol 002475

# KDJ 策略回测
python main.py --strategy kdj --symbol 600036

# 多只股票回测
python main.py --strategy huangbai --symbol 002475 600036 000001

# 指定回测区间
python main.py --strategy huangbai --symbol 002475 --start 2024-01-01 --end 2025-12-31
```

### 全市场选股扫描

```bash
# 扫描并自动对结果执行回测
python main.py --strategy huangbai --scan

# 仅扫描选股，不执行回测
python main.py --strategy huangbai --scan-only
```

### 组合级模拟

组合级模拟是最接近真实交易的回测方式：每周一更新观察池，每日检查买卖信号，组合级仓位管理。

```bash
python main.py --strategy huangbai --portfolio
```

### 其他参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--cash` | 初始资金 | 100000 |
| `--start` | 回测起始日期 | 2023-01-01 |
| `--end` | 回测结束日期 | 2025-12-31 |
| `--stock-type` | 板块类型（main/tech） | main |
| `--no-plot` | 禁用回测结果绘图 | - |

## 扩展新策略

### 1. 创建策略文件

在 `src/strategies/` 下新建 `.py` 文件，继承 `BaseStrategy`：

```python
from src.strategies.base_strategy import BaseStrategy

class MyStrategy(BaseStrategy):
    params = (("position_pct", 0.9), ("print_log", True))

    def indicators(self):
        # 设置技术指标（MyTT 批量计算）
        pass

    def buy_signal(self) -> bool:
        # 返回 True 时触发买入
        return False

    def sell_signal(self) -> bool:
        # 返回 True 时触发卖出
        return False
```

### 2. 注册策略

在 `main.py` 的 `STRATEGIES` 字典中添加：

```python
from src.strategies.my_strategy import MyStrategy

STRATEGIES = {
    "kdj": KDJCrossStrategy,
    "huangbai": HuangBaiB1Strategy,
    "my": MyStrategy,  # 新增
}
```

### 3. 运行

```bash
python main.py --strategy my --symbol 600036
```

### 复杂策略

如果策略涉及分批卖出、状态管理等复杂逻辑，可以直接覆写 `next()` 方法而非使用模板方法。参考 `huangbai_b1_strategy.py` 的实现。

### 自定义指标

如果 MyTT 内置指标不满足需求，可在 `src/indicators/` 下参照 `kdj_indicator.py` 的模式创建：`__init__` 中用 MyTT 批量计算完整数组，`next()` 中按索引取值。

## 内置策略说明

### KDJ 金叉/死叉策略

经典的 KDJ 指标策略，K 线上穿 D 线时买入，下穿时卖出。使用通达信标准的 `SMA(RSV,3,1)` 公式。

### 黄白线金叉后 B1 策略

中短线策略，通过三重过滤入场：

1. **周线多头空间**：周线 MA30 > MA60 > MA120 > MA240，且收盘价站上 MA30
2. **黄白线金叉**：白线 `EMA(EMA(C,10),10)` 上穿黄线 `BBI(MA14,MA28,MA57,MA114)`
3. **B1 买入信号**：7 个子条件任一满足（超卖缩量、回踩均线等）

出场采用 6 级优先级：止损 → T+N 没涨清仓 → 盈利 100% 清仓 → 半仓持股模式 → 涨停卖半 → 中阳卖 1/3。

## 依赖

| 库 | 用途 |
|----|------|
| backtrader | 回测框架 |
| mytt | 通达信标准技术指标计算 |
| mootdx | 通达信本地数据读取 |
| pandas / numpy | 数据处理 |
| matplotlib | 回测结果绘图 |

## License

MIT
