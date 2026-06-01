# 回测结果评测系统

## 1. Purpose

对组合级模拟回测结果进行 5 维度自动打分（A-F 等级制），生成暗黑主题自包含 HTML 评测报告。用于量化评估策略的回撤控制、风险收益比、收益分布均匀性、参数鲁棒性和佣金后利润，判断策略是否具备实盘条件。

## 2. How it Works

### 整体流程

```
main.py --eval
  → _run_eval(report, tag, args)
    → evaluate_report(report, strategy_tag, start_date, end_date)  → EvalResult
    → generate_eval_html(eval_result, output_path)                 → HTML文件
```

`--eval` 标志附加在任意组合级模拟命令后，在每个策略块的模拟完成后调用 `_run_eval()`，传入 `sim.report()` 字典和策略标签。

### 5 维度评分体系

| 维度 | 权重 | 核心指标 | 判定逻辑 |
|------|------|----------|----------|
| D1 最大回撤 | 0.25 | `report.max_drawdown`（绝对值） | A: ≤8%, B: ≤12%, C: ≤18%, D: ≤25%, F: >25% |
| D2 夏普/卡玛 | 0.20 | Calmar（年化收益/|最大回撤|）与 Sharpe 取较优者 | A: ≥2.0, B: ≥1.2, C: ≥0.6, D: ≥0.3, F: <0.3 |
| D3 月度收益分布 | 0.20 | 前3月占比 + 正月率双重判定 | A: <40%且≥60%, B: <55%且≥50%, C: <70%且≥40% |
| D4 逻辑与参数检验 | 0.20 | 交易数(25)+胜率(25)+盈亏因子(25)+集中度(25)=100 | 4子项复合打分，≥80=A, ≥65=B, ≥50=C, ≥35=D |
| D5 扣除佣金后利润 | 0.15 | 净收益率 + 佣金占毛利润比例 | A: ≥15%且<20%, B: ≥8%且<35%, C: ≥3% |

### 特殊规则

- **D1 硬伤一票否决**：D1 为 F 时，总评等级上限为 D（无论其他维度如何）
- **总评等级**：加权总分 ≥80=A, ≥65=B, ≥50=C, ≥35=D, <35=F
- **判定映射**：A/B → PASS, C → WATCH, D/F → FAIL

### 衍生指标计算

- **Calmar** = 年化收益率 / |最大回撤|，年化 = `total_return * 252 / trading_days`
- **月度收益** = trade_list 按 `sell_date` 月份分组求和 `pnl_amount`
- **Profit Factor** = 盈利股票总额 / |亏损股票总额|（按股票聚合，非按交易）
- **佣金总额** = `sum(size * buy_price * rate + size * sell_price * rate)`，rate 取自 `config.COMMISSION`

### HTML 报告结构

总评分卡片 → 5 维度概览（chip 网格） → 维度详情卡 → 月度收益柱状图 + 表格 → 交易质量 TOP5（最佳/最差） → 评测结论与建议。自包含 HTML，无外部依赖。

### 输出路径

报告保存到 `logs/eval_{tag}_{timestamp}.html`，tag 为策略标签去除方括号后的值（如 `B1V2`、`DNZH`、`N型砖`）。

### 支持的策略

所有 17 个策略块均已集成 `--eval` 调用。`--eval` 仅在组合级模拟模式生效（需配合 `--portfolio` 或默认组合模式的策略）。

## 3. Relevant Code Modules

- `src/evaluator/__init__.py` - 模块入口，导出 `evaluate_report` 和 `generate_eval_html`
- `src/evaluator/scorer.py` - 核心 5 维度打分逻辑，`EvalResult` / `DimensionScore` dataclass，月度收益与集中度计算
- `src/evaluator/html_report.py` - 暗黑主题 HTML 生成器，CSS/表格/柱状图/结论建议
- `main.py` - `--eval` 参数定义（L147）、`_run_eval()` 辅助函数（L153-164）、各策略块中的 eval 调用
- `config.py` - `COMMISSION` 常量，D5 佣金计算使用

## 4. Attention

- `--eval` 必须配合组合级模拟使用，单独 `--eval` 无意义（需要 report 字典作为输入）
- Profit Factor 按股票聚合 PnL（非按交易），盈利股票和亏损股票分别汇总
- D2 中 Calmar 和 Sharpe 取较优者判定等级，报告中同时展示两个值
- D4 的 4 个子项各 25 分（满分 100），交易数<10 时统计意义不足会在描述中标注
- `_run_eval()` 中 `tag_safe` 去除方括号和空格用于文件名安全
