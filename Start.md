# Backtrader + MyTT + mootdx 组合使用详细指南

> **对话来源**: DeepSeek Chat
> **对话角色**: Expert (AI助手)
> **原始链接**: https://chat.deepseek.com/share/cahqs1nyfimrnggo1u
> **备注**: 此对话内容由AI生成，仅供参考

---

## 用户提问

> 详细说说 Backtrader + MyTT + mootdx组合应该如何使用才能达到效果

---

## 概述

基于 **Backtrader + MyTT + mootdx** 三个工具，构建一个"通达信选股公式 → Python策略 → 本地回测"的完整流水线，需要做好三件事：

1. **数据通路**：用 **mootdx** 直读通达信本地数据文件，喂给Backtrader
2. **逻辑转译**：用 **MyTT** 将通达信公式函数（如MA、CROSS）1:1还原成Python代码
3. **回测执行**：在 **Backtrader** 中组装策略、设置资金/手续费，跑出结果并分析

为了避免内容臃肿，我把每个步骤拆解成**关键代码段 + 核心注意事项**，让您看完就能直接上手。

---

## 一、环境安装与文件准备

在本地Python环境（推荐3.8以上）中执行：

```bash
pip install backtrader mytt mootdx pandas
```

然后，将 **MyTT.py** 文件（可从官方仓库下载）放到你的项目目录下，方便导入。

---

## 二、数据获取与处理（mootdx 读取通达信本地数据）

假设你的通达信安装在 `C:\\new_tdx`，日线数据在 `vipdoc\sh\lday` 下。

```python
from mootdx.reader import Reader

reader = Reader.factory(market='std', tdxdir='C:\\new_tdx')

# 自动识市场
# 读取单只股票（如上证指数 600000）
df = reader.daily(symbol='600000')
print(df.head())
```

输出会包含：`date, open, high, low, close, volume, amount`

### 数据馈送 Backtrader 的关键格式转换

Backtrader 的 `PandasData` 要求列名规范，必须重命名并设置日期索引：

```python
import pandas as pd
import backtrader as bt

def get_feed(symbol, start='2020-01-01', end='2023-12-31'):
    df = reader.daily(symbol=symbol)

    # 列名映射：mootdx -> Backtrader规范
    df.rename(columns={'date': 'datetime', 'vol': 'volume'}, inplace=True)

    # 设置日期为索引
    df['datetime'] = pd.to_datetime(df['datetime'])
    df.set_index('datetime', inplace=True)

    # 按时间截取
    df = df.loc[start:end]

    # 还需添加 openinterest 列（可为0）
    df['openinterest'] = 0

    return bt.feeds.PandasData(dataname=df)

data = get_feed('600000')
```

---

## 三、MyTT 转译通达信公式（核心）

MyTT 提供了与通达信几乎同名的函数，`SMA`、`REF`、`HHV`、`CROSS` 等。我们以 **"KDJ金叉 + 5日线过滤"** 为例，展示如何在Backtrader策略中直接使用。

### 初步尝试：直接计算方式

```python
from MyTT import SMA, CROSS, MA, REF, HHV, LLV  # 关键函数

class KDJ_Cross_Strategy(bt.Strategy):
    params = (('n', 9),)

    def __init__(self):
        # 定义指标：直接用MyTT计算，作为内部数组
        self.K = []
        self.D = []
        self.ma5 = MA(self.data.close, 5)
        self.signal = None

    def next(self):
        # 这里需在每次next中手动计算，或预先用lines计算
        # 获取当前及之前的价格窗口
        close = self.data.close.array
        high = self.data.high.array
        low = self.data.low.array

        # 通达信RSV公式
        n = self.params.n
        rsv = (close[-1] - min(low[-n:])) / (max(high[-n:]) - min(low[-n:])) * 100

        # 中国式SMA：使用MyTT的SMA函数
        # 需要传入序列，我们可以用列表累积，但更简洁的方法是使用MyTT函数直接计算整个序列
```

### 更优雅的做法：在 `__init__` 中构建自定义指标

Backtrader允许用 `bt.Indicator` 封装。我们可以将MyTT函数嵌入：

```python
from MyTT import SMA, CROSS, MA, REF, HHV, LLV

class MyTT_Indicator(bt.Indicator):
    lines = ('K', 'D', 'J')
    params = (('n', 9),)

    def __init__(self):
        # 使用MyTT函数计算整个序列
        C, H, L = self.data.close.array, self.data.high.array, self.data.low.array
        rsv = (C - LLV(L, self.p.n)) / (HHV(H, self.p.n) - LLV(L, self.p.n)) * 100
        K = SMA(rsv, 3, 1)
        D = SMA(K, 3, 1)
        J = 3 * K - 2 * D
        # 将数值转为lines
        self.lines.K = K
        self.lines.D = D
        self.lines.J = J

class MyStrategy(bt.Strategy):
    def __init__(self):
        self.kdj = MyTT_Indicator(self.data)  # 使用自定义指标
        self.ma5 = bt.ind.SMA(self.data.close, period=5)
        self.cross = bt.ind.CrossOver(self.kdj.K, self.kdj.D)

    def next(self):
        if self.cross > 0 and self.data.close[0] > self.ma5[0]:
            self.buy()
        elif self.cross < 0:
            self.sell()
```

这种写法**信号由Backtrader管理**，彻底避免未来函数，且可自动画图。

---

## 四、回测设置与运行

```python
cerebro = bt.Cerebro()

# 添加数据
cerebro.adddata(get_feed('600000', start='2022-01-01', end='2024-01-01'))

# 添加策略
cerebro.addstrategy(MyStrategy)

# 设置初始资金、手续费
cerebro.broker.setcash(100000.0)
cerebro.broker.setcommission(commission=0.0003)  # 万三手续费

# 添加分析器
cerebro.addanalyzer(bt.analyzers.SharpeRatio, _name='sharpe')
cerebro.addanalyzer(bt.analyzers.DrawDown, _name='dd')
cerebro.addanalyzer(bt.analyzers.Returns, _name='returns')

# 运行并输出
result = cerebro.run()
strat = result[0]
print(f"夏普比率: {strat.analyzers.sharpe.get_analysis()['sharperatio']}")
print(f"最大回撤: {strat.analyzers.dd.get_analysis().max.drawdown:.2f}%")

# 图形回测曲线
cerebro.plot()
```

---

## 五、进阶：A股多股票选股回测

通达信选股公式的本质是在**全市场股票池中每日筛选**。Backtrader也能做，但需要技巧：

1. 准备多只股票的历史数据，循环 `adddata`
2. 在 `next` 中遍历 `self.datas`，对每个股票计算选股条件
3. 持仓管理需要手动处理：只买当日信号股，并控制仓位

```python
class StockSelectStrategy(bt.Strategy):
    def next(self):
        for d in self.datas:
            # d代表一只股票
            # 计算该股票的选股条件（复用之前的单股逻辑）
            cond1 = ...  # 你的公式条件
            pos = self.getposition(d)
            if cond1 and not pos:
                self.order_target_percent(d, target=0.1)  # 仓位10%
            elif 卖出条件 and pos:
                self.order_target_percent(d, target=0.0)
```

**注意**：多股回测对计算资源消耗大，建议先用10只股票调试。

---

## 六、确保效果的关键细节（避坑指南）

### 1. MyTT的SMA必须用中国式

- `SMA(X, N, M)` 代表 `M日加权`
- 通达信公式 `SMA(RSV,3,1)` 计算的是 `EMA` 还是 `SMA`？务必核对 MyTT 文档

### 2. 信号不使用未来数据

- 在自定义指标中使用 `self.data.close[0]` 是当前值
- `[0]` 是当下，`[-1]` 是昨天
- 千万避免在 `__init__` 中使用 `self.data.close.array` 时取到未发生的索引

### 3. 停牌、涨跌停处理

- 建议在 `next` 中加入 `if d.volume[0] == 0: continue` 过滤停牌
- 对于涨停无法买入，可用 `if d.close[0] >= d.high[0] * 0.995: return` 避过

### 4. 回测时间对齐

- 使用 `pandas` 导入后，确保所有数据日期一致
- 可用 `bt.feeds.PandasData` 的 `fromdate` 参数

---

## 七、这个组合的最终效果

您将获得一个**完全离线**、**无需付费**、**策略代码与通达信公式高度一致**的回测系统。其核心优势是：

- **数据由您掌控**（本地通达信文件，更新只需重读）
- **逻辑原汁原味**（MyTT保证了公式计算的精度）
- **回测速度快**（Backtrader的矢量化回测效率很高）

开始只需把您最熟悉的一个简单选股公式（如"均线金叉+成交量放大"）用此管道跑通，再逐步增加复杂度。

---

## 八、核心要点总结

| 步骤 | 工具 | 关键操作 |
|------|------|----------|
| 环境安装 | pip | `pip install backtrader mytt mootdx pandas` |
| 数据读取 | mootdx | `reader.daily(symbol='600000')` |
| 数据转换 | Pandas | 列名映射、日期索引设置 |
| 公式转译 | MyTT | SMA, REF, HHV, LLV, CROSS 等函数 |
| 策略编写 | Backtrader | bt.Strategy, bt.Indicator |
| 回测分析 | Analyzers | SharpeRatio, DrawDown, Returns |

---

**备注**: 此对话内容由AI生成，仅供参考。
