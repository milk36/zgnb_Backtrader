"""回测评测 HTML 报告生成器

暗黑主题自包含 HTML，无外部依赖。
"""

GRADE_COLORS = {
    "A": "#22c55e",
    "B": "#3b82f6",
    "C": "#f59e0b",
    "D": "#f97316",
    "F": "#ef4444",
}

VERDICT_COLORS = {
    "PASS": "#22c55e",
    "WATCH": "#f59e0b",
    "FAIL": "#ef4444",
}

VERDICT_LABELS = {
    "PASS": "✓ 通过",
    "WATCH": "⚠ 观察",
    "FAIL": "✗ 不通过",
}


def _css():
    return """
:root {
    --bg-primary: #0f1117;
    --bg-card: #1a1d2e;
    --bg-card-alt: #1e2235;
    --border: #2a2d3e;
    --text-primary: #e2e8f0;
    --text-secondary: #94a3b8;
    --text-muted: #64748b;
    --green: #22c55e;
    --red: #ef4444;
    --blue: #3b82f6;
    --orange: #f97316;
    --yellow: #f59e0b;
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
    background: var(--bg-primary);
    color: var(--text-primary);
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
    line-height: 1.6;
    padding: 2rem;
    max-width: 1100px;
    margin: 0 auto;
}
h1 { font-size: 1.5rem; margin-bottom: 0.25rem; }
h2 { font-size: 1.15rem; margin: 2rem 0 0.75rem; color: var(--text-secondary);
     border-bottom: 1px solid var(--border); padding-bottom: 0.5rem; }
.meta { color: var(--text-muted); font-size: 0.85rem; margin-bottom: 1.5rem; }
.card {
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 1.25rem;
    margin-bottom: 1rem;
}
.card-alt { background: var(--bg-card-alt); }

/* 总评分 */
.score-hero {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 2rem;
    padding: 2rem;
}
.score-number {
    font-size: 4rem;
    font-weight: 800;
    line-height: 1;
}
.grade-badge, .verdict-badge {
    display: inline-block;
    padding: 0.3rem 0.9rem;
    border-radius: 999px;
    font-weight: 700;
    font-size: 1rem;
    letter-spacing: 0.05em;
}
.verdict-badge { font-size: 1.1rem; }

/* 维度概览 */
.dim-grid {
    display: grid;
    grid-template-columns: repeat(5, 1fr);
    gap: 0.75rem;
    margin-bottom: 1.5rem;
}
.dim-chip {
    text-align: center;
    padding: 0.75rem 0.5rem;
    border-radius: 8px;
    background: var(--bg-card);
    border: 1px solid var(--border);
}
.dim-chip .dim-label { font-size: 0.75rem; color: var(--text-muted); margin-bottom: 0.3rem; }
.dim-chip .dim-grade { font-size: 1.5rem; font-weight: 700; }

/* 表格 */
table {
    width: 100%;
    border-collapse: collapse;
    margin-bottom: 1rem;
    font-size: 0.88rem;
}
th {
    text-align: left;
    padding: 0.6rem 0.75rem;
    background: var(--bg-card-alt);
    color: var(--text-secondary);
    font-weight: 600;
    border-bottom: 1px solid var(--border);
}
td {
    padding: 0.5rem 0.75rem;
    border-bottom: 1px solid var(--border);
}
tr:hover td { background: rgba(255,255,255,0.02); }
.text-green { color: var(--green); }
.text-red { color: var(--red); }
.text-right { text-align: right; }
.text-center { text-align: center; }
.mono { font-family: 'JetBrains Mono', 'Fira Code', monospace; }

/* 详情卡 */
.dim-detail {
    display: flex;
    gap: 1rem;
    align-items: flex-start;
    margin-bottom: 0.75rem;
}
.dim-detail .dim-badge {
    flex-shrink: 0;
    width: 3rem;
    height: 3rem;
    border-radius: 8px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 1.5rem;
    font-weight: 800;
}
.dim-detail .dim-info { flex: 1; }
.dim-detail .dim-title { font-weight: 600; margin-bottom: 0.2rem; }
.dim-detail .dim-desc { color: var(--text-secondary); font-size: 0.88rem; }
.dim-detail .dim-metrics {
    display: flex;
    gap: 1.5rem;
    margin-top: 0.4rem;
    font-size: 0.82rem;
    color: var(--text-muted);
}
.dim-detail .dim-metrics span { white-space: nowrap; }

/* 阈值条 */
.threshold-bar {
    display: flex;
    gap: 2px;
    margin-top: 0.5rem;
    border-radius: 4px;
    overflow: hidden;
    height: 6px;
}
.threshold-bar .seg {
    flex: 1;
    position: relative;
}
.threshold-bar .marker {
    position: absolute;
    top: -4px;
    width: 2px;
    height: 14px;
    background: #fff;
    border-radius: 1px;
}

/* 建议 */
.finding {
    background: var(--bg-card);
    border-left: 4px solid var(--blue);
    padding: 1rem 1.25rem;
    border-radius: 0 8px 8px 0;
    margin-top: 1rem;
}
.finding ul { margin-top: 0.5rem; padding-left: 1.2rem; }
.finding li { color: var(--text-secondary); font-size: 0.88rem; margin-bottom: 0.3rem; }

/* 月度柱状图 */
.monthly-chart {
    display: flex;
    align-items: flex-end;
    gap: 2px;
    height: 80px;
    margin-bottom: 0.75rem;
    padding: 0 0.25rem;
}
.monthly-chart .bar {
    flex: 1;
    min-width: 8px;
    max-width: 24px;
    border-radius: 2px 2px 0 0;
    position: relative;
    transition: opacity 0.2s;
}
.monthly-chart .bar:hover { opacity: 0.8; }
.monthly-chart .bar-label {
    position: absolute;
    bottom: -18px;
    left: 50%;
    transform: translateX(-50%);
    font-size: 0.6rem;
    color: var(--text-muted);
    white-space: nowrap;
}
"""


def _header(meta, result):
    tag = meta.get("strategy_tag", "")
    start = meta.get("start_date", "-")
    end = meta.get("end_date", "-")
    days = meta.get("trading_days", 0)
    eval_time = meta.get("eval_time", "")
    return f"""
<h1>策略评测报告 {tag}</h1>
<p class="meta">
    回测区间: {start} ~ {end} &nbsp;|&nbsp;
    交易日: {days} &nbsp;|&nbsp;
    生成时间: {eval_time}
</p>"""


def _score_hero(result):
    score = result.overall_score
    grade = result.overall_grade
    verdict = result.overall_verdict
    gc = GRADE_COLORS.get(grade, "#94a3b8")
    vc = VERDICT_COLORS.get(verdict, "#94a3b8")
    vl = VERDICT_LABELS.get(verdict, verdict)
    return f"""
<div class="card">
    <div class="score-hero">
        <div class="score-number" style="color:{gc}">{score:.0f}</div>
        <div>
            <span class="grade-badge" style="background:{gc}22;color:{gc};border:1px solid {gc}44">
                {grade}
            </span>
            <span class="verdict-badge" style="background:{vc}22;color:{vc};border:1px solid {vc}44;margin-left:0.5rem">
                {vl}
            </span>
        </div>
    </div>
</div>"""


def _dim_overview(result):
    chips = []
    for key in ["drawdown", "risk_return", "monthly", "robustness", "commission"]:
        dim = result.dimensions[key]
        c = GRADE_COLORS.get(dim.grade, "#94a3b8")
        chips.append(f"""
        <div class="dim-chip">
            <div class="dim-label">{dim.label.split(' ', 1)[1] if ' ' in dim.label else dim.label}</div>
            <div class="dim-grade" style="color:{c}">{dim.grade}</div>
        </div>""")
    return '<div class="dim-grid">' + ''.join(chips) + '</div>'


def _dim_details(result):
    items = []
    for key in ["drawdown", "risk_return", "monthly", "robustness", "commission"]:
        dim = result.dimensions[key]
        c = GRADE_COLORS.get(dim.grade, "#94a3b8")
        details = dim.details
        metrics_html = ""

        if key == "drawdown":
            metrics_html = (f'<span>最大回撤: <b>{details.get("max_drawdown_pct", 0):.1f}%</b></span>'
                            f'<span>预估恢复: {details.get("recovery_estimate_days", -1)}天</span>')
        elif key == "risk_return":
            sh = details.get("sharpe")
            ca = details.get("calmar")
            ar = details.get("annualized_return", 0)
            metrics_html = (f'<span>Sharpe: <b>{f"{sh:.2f}" if sh else "N/A"}</b></span>'
                            f'<span>Calmar: <b>{f"{ca:.2f}" if ca else "N/A"}</b></span>'
                            f'<span>年化收益: <b>{ar:.1f}%</b></span>')
        elif key == "monthly":
            con = details
            metrics_html = (f'<span>交易月数: <b>{con.get("total_months", 0)}</b></span>'
                            f'<span>正月率: <b>{con.get("positive_ratio", 0):.0%}</b></span>'
                            f'<span>前3月占比: <b>{con.get("top3_months_pct_of_total", 0):.0%}</b></span>')
        elif key == "robustness":
            metrics_html = (f'<span>交易数: <b>{details.get("trade_count", 0)}</b></span>'
                            f'<span>胜率: <b>{details.get("win_rate", 0):.0%}</b></span>'
                            f'<span>PF: <b>{details.get("profit_factor", 0):.2f}</b></span>'
                            f'<span>前3只占比: <b>{details.get("top3_stock_concentration", 0):.0%}</b></span>')
        elif key == "commission":
            metrics_html = (f'<span>毛收益: <b>{details.get("gross_return", 0):.2f}%</b></span>'
                            f'<span>净收益: <b>{details.get("net_return", 0):.2f}%</b></span>'
                            f'<span>佣金: <b>{details.get("commission_total", 0):,.0f}</b></span>'
                            f'<span>佣金占比: <b>{details.get("commission_pct_of_gross", 0):.0%}</b></span>')

        items.append(f"""
    <div class="card">
        <div class="dim-detail">
            <div class="dim-badge" style="background:{c}18;color:{c}">{dim.grade}</div>
            <div class="dim-info">
                <div class="dim-title">{dim.label}</div>
                <div class="dim-desc">{dim.description}</div>
                <div class="dim-metrics">{metrics_html}</div>
            </div>
        </div>
    </div>""")
    return '<h2>五维度详情</h2>' + ''.join(items)


def _monthly_section(result):
    md = result.monthly_data
    if not md:
        return '<h2>月度收益分布</h2><div class="card"><p class="meta">无交易数据</p></div>'

    # 柱状图
    max_abs = max((abs(m["pnl_amount"]) for m in md), default=1) or 1
    bars = []
    for m in md:
        h = abs(m["pnl_amount"]) / max_abs * 70
        color = "var(--green)" if m["pnl_amount"] >= 0 else "var(--red)"
        lbl = f"{m['month']}月"
        bars.append(f'<div class="bar" style="height:{h}px;background:{color}" '
                    f'title="{m["label"]}: {m["pnl_amount"]:,.0f}">'
                    f'<span class="bar-label">{lbl}</span></div>')
    chart_html = '<div class="monthly-chart">' + ''.join(bars) + '</div>'

    # 表格
    rows = []
    for m in md:
        cls = "text-green" if m["pnl_amount"] >= 0 else "text-red"
        rows.append(f"""
        <tr>
            <td>{m['label']}</td>
            <td class="text-right mono {cls}">{m['pnl_amount']:>10,.0f}</td>
            <td class="text-center">{m['trade_count']}</td>
            <td class="text-center">{m['won']}</td>
            <td class="text-center">{m['lost']}</td>
        </tr>""")

    con = result.concentration
    summary = (f"共 {con['total_months']} 个月，"
               f"正收益 {con['positive_months']} 个月，"
               f"负收益 {con['negative_months']} 个月，"
               f"正月率 {con['positive_ratio']:.0%}，"
               f"最佳月 {con['best_month_label']}（{con['best_month_pnl']:,.0f}）")

    return f"""
<h2>月度收益分布</h2>
<div class="card">
    {chart_html}
    <table>
        <tr><th>月份</th><th class="text-right">PnL</th>
            <th class="text-center">交易数</th><th class="text-center">盈</th>
            <th class="text-center">亏</th></tr>
        {''.join(rows)}
    </table>
    <p class="meta">{summary}</p>
</div>"""


def _trade_quality_section(result):
    tq = result.trade_quality
    best = tq.get("best5", [])
    worst = tq.get("worst5", [])
    if not best and not worst:
        return ""

    def _trade_rows(trades, color_class):
        rows = []
        for t in trades:
            rows.append(f"""
            <tr>
                <td class="mono">{t.get('code', '')}</td>
                <td>{t.get('sell_date', '')}</td>
                <td class="text-right mono {color_class}">{t.get('pnl_pct', 0):.1f}%</td>
                <td class="text-right mono {color_class}">{t.get('pnl_amount', 0):>10,.0f}</td>
                <td>{t.get('reason', '')}</td>
            </tr>""")
        return ''.join(rows)

    html = '<h2>交易质量</h2>'

    if best:
        html += f"""
<div class="card">
    <h3 style="font-size:0.95rem;margin-bottom:0.5rem;color:var(--green)">🏆 最佳交易 TOP 5</h3>
    <table>
        <tr><th>代码</th><th>卖出日</th><th class="text-right">收益率</th>
            <th class="text-right">盈亏</th><th>退出原因</th></tr>
        {_trade_rows(best, 'text-green')}
    </table>
</div>"""

    if worst:
        html += f"""
<div class="card">
    <h3 style="font-size:0.95rem;margin-bottom:0.5rem;color:var(--red)">💀 最差交易 TOP 5</h3>
    <table>
        <tr><th>代码</th><th>卖出日</th><th class="text-right">收益率</th>
            <th class="text-right">盈亏</th><th>退出原因</th></tr>
        {_trade_rows(worst, 'text-red')}
    </table>
</div>"""
    return html


def _recommendation(result):
    dims = result.dimensions
    tips = []

    if dims["drawdown"].grade in ("D", "F"):
        tips.append("回撤过大，建议收紧止损或增加过滤条件降低持仓风险")
    if dims["risk_return"].grade in ("D", "F"):
        tips.append("风险收益比不达标，需优化信号质量或仓位管理")
    if dims["monthly"].grade in ("D", "F"):
        tips.append("收益过度集中，盈利可能依赖少数事件，建议增加信号多样性")
    if dims["robustness"].grade in ("D", "F"):
        pf = dims["robustness"].details.get("profit_factor", 0)
        if pf < 1.0:
            tips.append("盈亏因子 < 1.0，策略逻辑本身需修改")
        tc = dims["robustness"].details.get("trade_count", 0)
        if tc < 10:
            tips.append(f"交易样本仅 {tc} 只，统计意义不足，需延长回测周期或放宽条件")
    if dims["commission"].grade in ("D", "F"):
        tips.append("扣除佣金后利润微薄或亏损，需减少交易频率或提高单笔收益")

    if result.overall_verdict == "PASS":
        summary = "策略通过评测，核心指标表现良好，可考虑实盘验证。"
    elif result.overall_verdict == "WATCH":
        summary = "策略表现尚可但存在隐患，建议针对性优化后再评估。"
    else:
        summary = "策略未通过评测，存在明显缺陷，不建议直接实盘。"

    tips_html = ''.join(f"<li>{t}</li>" for t in tips) if tips else "<li>无特别建议</li>"

    return f"""
<h2>评测结论</h2>
<div class="finding">
    <p><b>{summary}</b></p>
    <ul>{tips_html}</ul>
</div>"""


def generate_eval_html(eval_result, output_path="eval_report.html"):
    """生成暗黑主题 HTML 评测报告

    Args:
        eval_result: EvalResult 实例
        output_path: HTML 文件保存路径

    Returns:
        output_path
    """
    r = eval_result
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>策略评测报告 {r.report_meta.get('strategy_tag', '')}</title>
    <style>{_css()}</style>
</head>
<body>
{_header(r.report_meta, r)}
{_score_hero(r)}
{_dim_overview(r)}
{_dim_details(r)}
{_monthly_section(r)}
{_trade_quality_section(r)}
{_recommendation(r)}
</body>
</html>"""

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    return output_path
