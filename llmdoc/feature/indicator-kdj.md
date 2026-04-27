# 指标层：KDJ 指标 (KDJIndicator)

## 1. Purpose

基于 MyTT 函数库实现通达信标准 KDJ 指标，作为 Backtrader 自定义 Indicator 供策略使用。与 MyTT 内置 `KDJ()` 的区别在于使用 SMA（加权移动平均）而非 EMA。

## 2. How it Works

### 计算公式

```
RSV = (C - LLV(L, N)) / (HHV(H, N) - LLV(L, N)) * 100
K   = SMA(RSV, M1, 1)    # MyTT.SMA 即中国式加权移动平均
D   = SMA(K, M2, 1)
J   = 3*K - 2*D
```

默认参数：N=9, M1=3, M2=3。

### 实现方式

- `__init__` 中：从 `self.data.close/high/low.array` 取完整数组，用 MyTT 的 `HHV`, `LLV`, `SMA` 批量计算 `_k`, `_d`, `_j` 数组
- `next` 中：按当前 bar 索引 `len(self)-1` 从预计算数组取值赋给 `self.lines.K/D/J[0]`
- `denom == 0` 时 RSV 取 50.0（防除零）

### Backtrader 集成

继承 `bt.Indicator`，声明 `lines = ("K", "D", "J")`，策略中可通过 `self.kdj.K` / `self.kdj.D` / `self.kdj.J` 引用。

## 3. Relevant Code Modules

- `src/indicators/kdj_indicator.py` - KDJIndicator 类
- `config.py` - `KDJ_N`, `KDJ_M1`, `KDJ_M2`

## 4. Attention

- `.array` 属性在 `__init__` 阶段可用，但在 `next()` 中不应使用（此时用索引访问）
- 新增指标应遵循同样模式：`__init__` 批量算 + `next` 按索引取值
