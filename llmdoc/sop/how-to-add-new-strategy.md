# SOP：新增回测策略

## 1. Purpose

说明如何基于现有 `BaseStrategy` 模板新增一个自定义回测策略。

## 2. Step-by-Step Guide

1. **创建策略文件**：在 `src/strategies/` 下新建 `.py` 文件，如 `src/strategies/macd_strategy.py`

2. **继承 BaseStrategy 并实现三个方法**：

```python
from src.strategies.base_strategy import BaseStrategy

class MACDStrategy(BaseStrategy):
    params = (("position_pct", 0.9), ("print_log", True))

    def indicators(self):
        # 在此设置指标，如 self.macd = ...
        pass

    def buy_signal(self) -> bool:
        # 返回买入条件
        return False

    def sell_signal(self) -> bool:
        # 返回卖出条件
        return False
```

3. **（可选）新增自定义指标**：若需要 MyTT 中没有的指标，在 `src/indicators/` 下参照 `kdj_indicator.py` 的模式创建，`__init__` 批量计算 + `next` 按索引取值

4. **在 `config.py` 中添加策略相关参数**

5. **修改 `main.py`**：将 `KDJCrossStrategy` 替换为新策略类，或通过 CLI 参数动态选择

6. **运行回测**：`python main.py --symbol <代码> --start <日期> --end <日期>`

## 3. Relevant Code Modules

- `src/strategies/base_strategy.py` - 基类模板
- `src/strategies/kdj_cross_strategy.py` - 参考实现
- `src/indicators/kdj_indicator.py` - 自定义指标参考
- `main.py` - 入口文件
- `config.py` - 配置文件

## 4. Attention

- 确保策略参数通过 `params` 元组声明，而非硬编码
- 自定义指标中 `self.data.xxx.array` 仅在 `__init__` 阶段使用
- 新增的 `.py` 文件需要确保目录下有 `__init__.py`
