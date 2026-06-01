"""回测结果 5 维度评测打分模块

维度:
    D1 最大回撤     — 硬伤检查，直接决定能否坚持
    D2 夏普/卡玛    — 风险收益互换效率
    D3 月度分布     — 判断盈利是否过度集中
    D4 参数鲁棒     — 防过拟合（交易数、胜率、盈亏因子、集中度）
    D5 佣金后利润   — 扣除滑点佣金后是否还有利可图

用法:
    eval_result = evaluate_report(report, strategy_tag="[B1V2]", ...)
    generate_eval_html(eval_result, output_path="eval.html")
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np

from config import COMMISSION


# ============================================================================
# 数据结构
# ============================================================================

@dataclass
class DimensionScore:
    """单维度评分"""
    grade: str           # "A" / "B" / "C" / "D" / "F"
    score: float         # 0-100
    label: str           # 维度名称
    description: str     # 一句话说明
    details: dict = field(default_factory=dict)


@dataclass
class EvalResult:
    """评测结果"""
    dimensions: dict                # dim_id -> DimensionScore
    overall_score: float            # 0-100 加权总分
    overall_grade: str              # A/B/C/D/F
    overall_verdict: str            # PASS / WATCH / FAIL
    monthly_data: list              # 月度收益列表
    concentration: dict             # 集中度分析
    commission_analysis: dict       # 佣金分析
    trade_quality: dict             # 交易质量（最佳/最差）
    report_meta: dict               # 元信息（策略标签、日期范围等）


# ============================================================================
# 常量
# ============================================================================

GRADE_SCORES = {"A": 90, "B": 75, "C": 60, "D": 45, "F": 25}

WEIGHTS = {
    "drawdown":     0.25,
    "risk_return":  0.20,
    "monthly":      0.20,
    "robustness":   0.20,
    "commission":   0.15,
}


# ============================================================================
# 月度收益分析
# ============================================================================

def _compute_monthly_returns(trade_list):
    """从 trade_list 按卖出月份聚合已实现 PnL"""
    monthly = defaultdict(lambda: {"pnl": 0.0, "trades": 0, "won": 0, "lost": 0})

    for t in trade_list:
        sell_date = t.get("sell_date")
        if sell_date is None:
            continue
        pnl = t.get("pnl_amount", 0.0)

        if isinstance(sell_date, str):
            dt = datetime.strptime(sell_date[:10], "%Y-%m-%d")
        else:
            dt = sell_date

        key = (dt.year, dt.month)
        monthly[key]["pnl"] += pnl
        monthly[key]["trades"] += 1
        if pnl >= 0:
            monthly[key]["won"] += 1
        else:
            monthly[key]["lost"] += 1

    result = []
    for (y, m), v in sorted(monthly.items()):
        result.append({
            "year": y, "month": m,
            "label": f"{y}-{m:02d}",
            "pnl_amount": round(v["pnl"], 2),
            "trade_count": v["trades"],
            "won": v["won"],
            "lost": v["lost"],
        })
    return result


def _compute_concentration(monthly_data, total_pnl):
    """分析收益集中度"""
    if not monthly_data or total_pnl <= 0:
        return {
            "best_month_label": "-",
            "best_month_pnl": 0.0,
            "best_month_pct_of_total": 0.0,
            "top3_months_pct_of_total": 0.0,
            "positive_months": 0,
            "negative_months": 0,
            "total_months": len(monthly_data),
            "positive_ratio": 0.0,
        }

    pnls = sorted([m["pnl_amount"] for m in monthly_data], reverse=True)
    positive = sum(1 for m in monthly_data if m["pnl_amount"] >= 0)
    negative = sum(1 for m in monthly_data if m["pnl_amount"] < 0)
    total_months = len(monthly_data)

    best = monthly_data[max(range(len(monthly_data)),
                            key=lambda i: monthly_data[i]["pnl_amount"])]
    best_pct = best["pnl_amount"] / total_pnl if total_pnl > 0 else 0.0

    top3_sum = sum(pnls[:min(3, len(pnls))])
    top3_pct = top3_sum / total_pnl if total_pnl > 0 else 0.0

    return {
        "best_month_label": best["label"],
        "best_month_pnl": round(best["pnl_amount"], 2),
        "best_month_pct_of_total": round(best_pct, 4),
        "top3_months_pct_of_total": round(top3_pct, 4),
        "positive_months": positive,
        "negative_months": negative,
        "total_months": total_months,
        "positive_ratio": round(positive / total_months, 4) if total_months > 0 else 0.0,
    }


# ============================================================================
# D1: 最大回撤
# ============================================================================

def _score_drawdown(report):
    dd = report.get("max_drawdown", 0.0)
    # max_drawdown 是负数（如 -15.3 表示回撤 15.3%）
    dd_pct = abs(dd)

    if dd_pct <= 8:
        grade, desc = "A", f"最大回撤 {dd_pct:.1f}%，心理压力极小，可安心持有"
    elif dd_pct <= 12:
        grade, desc = "B", f"最大回撤 {dd_pct:.1f}%，在可接受范围内"
    elif dd_pct <= 18:
        grade, desc = "C", f"最大回撤 {dd_pct:.1f}%，需要一定心理承受力"
    elif dd_pct <= 25:
        grade, desc = "D", f"最大回撤 {dd_pct:.1f}%，偏大，可能影响执行纪律"
    else:
        grade, desc = "F", f"最大回撤 {dd_pct:.1f}%，严重回撤，极难坚持执行"

    # 估算恢复天数
    trading_days = report.get("trading_days", 1)
    total_ret = report.get("total_return", 0.0)
    avg_daily = total_ret / max(trading_days, 1)
    recovery_days = int(dd_pct / max(avg_daily, 0.01)) if avg_daily > 0 else -1

    return DimensionScore(
        grade=grade,
        score=GRADE_SCORES[grade],
        label="D1 最大回撤（硬伤）",
        description=desc,
        details={
            "max_drawdown_pct": round(dd_pct, 2),
            "recovery_estimate_days": recovery_days,
        },
    )


# ============================================================================
# D2: 夏普 / 卡玛比率
# ============================================================================

def _score_risk_return(report):
    sharpe = report.get("sharpe")
    dd = abs(report.get("max_drawdown", 0.0))
    trading_days = report.get("trading_days", 1)
    total_ret = report.get("total_return", 0.0)

    # 年化收益率
    ann_ret = total_ret * (252 / max(trading_days, 1))

    # Calmar 比率
    calmar = None
    if dd > 0.01:
        calmar = ann_ret / dd
    elif dd <= 0.01 and total_ret > 0:
        calmar = float('inf')

    # 取两者中较好的
    best_indicator = max(
        calmar if calmar is not None and calmar != float('inf') else -1,
        sharpe if sharpe is not None else -1,
    )

    if total_ret <= 0:
        grade, desc = "F", f"策略亏损，年化收益 {ann_ret:.1f}%，无效率可言"
    elif calmar is not None and calmar == float('inf'):
        grade, desc = "A", f"零回撤盈利，Calmar 无穷大"
    elif best_indicator >= 2.0:
        grade = "A"
        desc = f"Calmar={calmar:.2f}，Sharpe={sharpe or 'N/A'}，风险收益比优异"
    elif best_indicator >= 1.2:
        grade = "B"
        desc = f"Calmar={calmar:.2f}，Sharpe={sharpe or 'N/A'}，风险收益比良好"
    elif best_indicator >= 0.6:
        grade = "C"
        desc = f"Calmar={calmar:.2f}，Sharpe={sharpe or 'N/A'}，风险收益比一般"
    elif best_indicator >= 0.3:
        grade = "D"
        desc = f"Calmar={calmar:.2f}，Sharpe={sharpe or 'N/A'}，风险补偿不足"
    else:
        grade = "F"
        desc = f"Calmar={calmar:.2f}，Sharpe={sharpe or 'N/A'}，收益远不足补偿风险"

    return DimensionScore(
        grade=grade,
        score=GRADE_SCORES[grade],
        label="D2 夏普/卡玛比率",
        description=desc,
        details={
            "sharpe": round(sharpe, 3) if sharpe is not None else None,
            "calmar": round(calmar, 3) if calmar is not None and calmar != float('inf') else None,
            "annualized_return": round(ann_ret, 2),
        },
    )


# ============================================================================
# D3: 月度收益分布
# ============================================================================

def _score_monthly(monthly_data, concentration):
    total_months = concentration["total_months"]
    top3_pct = concentration["top3_months_pct_of_total"]
    pos_ratio = concentration["positive_ratio"]

    if total_months < 3:
        return DimensionScore(
            grade="C", score=60,
            label="D3 月度收益分布",
            description=f"仅 {total_months} 个月数据，不足以分析分布",
            details=concentration,
        )

    total_pnl = sum(m["pnl_amount"] for m in monthly_data)
    if total_pnl <= 0:
        return DimensionScore(
            grade="F", score=25,
            label="D3 月度收益分布",
            description=f"总亏损，月度分布无意义",
            details=concentration,
        )

    if top3_pct < 0.40 and pos_ratio >= 0.60:
        grade = "A"
        desc = f"收益分布均匀，前3月占比 {top3_pct:.0%}，正月率 {pos_ratio:.0%}"
    elif top3_pct < 0.55 and pos_ratio >= 0.50:
        grade = "B"
        desc = f"收益分布较均匀，前3月占比 {top3_pct:.0%}，正月率 {pos_ratio:.0%}"
    elif top3_pct < 0.70 and pos_ratio >= 0.40:
        grade = "C"
        desc = f"收益有一定集中，前3月占比 {top3_pct:.0%}，正月率 {pos_ratio:.0%}"
    elif top3_pct < 0.85 or pos_ratio >= 0.30:
        grade = "D"
        desc = f"收益较集中，前3月占比 {top3_pct:.0%}，正月率 {pos_ratio:.0%}，警惕偶然性"
    else:
        grade = "F"
        desc = f"收益高度集中，前3月占比 {top3_pct:.0%}，正月率 {pos_ratio:.0%}，大概率靠运气"

    return DimensionScore(
        grade=grade,
        score=GRADE_SCORES[grade],
        label="D3 月度收益分布",
        description=desc,
        details=concentration,
    )


# ============================================================================
# D4: 参数鲁棒 / 防过拟合
# ============================================================================

def _score_robustness(report, trade_list):
    # --- 按股票聚合 PnL ---
    stock_pnl = defaultdict(float)
    for t in trade_list:
        code = t.get("code", "")
        stock_pnl[code] += t.get("pnl_amount", 0.0)

    trade_count = report.get("total_trades", 0)
    won = report.get("won", 0)
    lost = report.get("lost", 0)
    win_rate = won / max(trade_count, 1)

    # Profit Factor
    gain = sum(v for v in stock_pnl.values() if v > 0)
    loss = abs(sum(v for v in stock_pnl.values() if v < 0))
    profit_factor = gain / max(loss, 1.0)

    # 收益集中度（按股票）
    total_gain = gain
    if total_gain > 0 and len(stock_pnl) >= 3:
        sorted_gains = sorted([v for v in stock_pnl.values() if v > 0], reverse=True)
        top3_gain = sum(sorted_gains[:min(3, len(sorted_gains))])
        top3_concentration = top3_gain / total_gain
    else:
        top3_concentration = 1.0

    # --- 4 个子项打分 ---
    # 交易数
    if trade_count >= 30:
        s_count = 25
    elif trade_count >= 20:
        s_count = 20
    elif trade_count >= 10:
        s_count = 15
    elif trade_count >= 5:
        s_count = 8
    else:
        s_count = 3

    # 胜率
    if win_rate >= 0.50:
        s_win = 25
    elif win_rate >= 0.40:
        s_win = 20
    elif win_rate >= 0.30:
        s_win = 15
    elif win_rate >= 0.20:
        s_win = 10
    else:
        s_win = 5

    # 盈亏因子
    if profit_factor >= 2.0:
        s_pf = 25
    elif profit_factor >= 1.5:
        s_pf = 20
    elif profit_factor >= 1.2:
        s_pf = 15
    elif profit_factor >= 1.0:
        s_pf = 10
    else:
        s_pf = 3

    # 集中度
    if top3_concentration < 0.40:
        s_conc = 25
    elif top3_concentration < 0.60:
        s_conc = 20
    elif top3_concentration < 0.80:
        s_conc = 12
    else:
        s_conc = 5

    total_sub = s_count + s_win + s_pf + s_conc

    if total_sub >= 80:
        grade = "A"
    elif total_sub >= 65:
        grade = "B"
    elif total_sub >= 50:
        grade = "C"
    elif total_sub >= 35:
        grade = "D"
    else:
        grade = "F"

    desc_parts = []
    if trade_count < 10:
        desc_parts.append(f"交易数仅{trade_count}只（统计意义不足）")
    desc_parts.append(f"胜率{win_rate:.0%}，PF={profit_factor:.2f}")
    if top3_concentration >= 0.60:
        desc_parts.append(f"收益前3只占比{top3_concentration:.0%}（过度集中）")

    return DimensionScore(
        grade=grade,
        score=GRADE_SCORES[grade],
        label="D4 逻辑与参数检验",
        description="；".join(desc_parts),
        details={
            "trade_count": trade_count,
            "win_rate": round(win_rate, 4),
            "profit_factor": round(profit_factor, 3),
            "top3_stock_concentration": round(top3_concentration, 4),
            "sub_scores": {
                "count": s_count,
                "win_rate": s_win,
                "profit_factor": s_pf,
                "concentration": s_conc,
            },
            "total_sub_score": total_sub,
        },
    )


# ============================================================================
# D5: 佣金后利润
# ============================================================================

def _score_commission(report, trade_list):
    total_ret = report.get("total_return", 0.0)
    initial_cash = report.get("initial_cash", 1_000_000)
    commission_rate = report.get("_commission", COMMISSION)

    # 计算总佣金
    total_commission = 0.0
    for t in trade_list:
        size = t.get("size", 0)
        bp = t.get("buy_price", 0)
        sp = t.get("sell_price", 0)
        total_commission += size * bp * commission_rate  # 买入佣金
        total_commission += size * sp * commission_rate  # 卖出佣金

    # 估算毛收益 = 净收益 + 佣金
    net_pnl = initial_cash * total_ret / 100
    gross_pnl = net_pnl + total_commission

    # 佣金占毛收益比例
    if gross_pnl > 0:
        commission_pct = total_commission / gross_pnl
    else:
        commission_pct = 1.0

    # 毛收益率
    gross_ret = gross_pnl / initial_cash * 100

    if total_ret <= 0:
        grade, desc = "F", f"策略净亏损 {total_ret:.2f}%，扣除佣金后无利可图"
    elif total_ret >= 15 and commission_pct < 0.20:
        grade = "A"
        desc = f"净收益 {total_ret:.2f}%，佣金仅占毛利润的 {commission_pct:.0%}，盈利充裕"
    elif total_ret >= 8 and commission_pct < 0.35:
        grade = "B"
        desc = f"净收益 {total_ret:.2f}%，佣金占比 {commission_pct:.0%}，盈利可观"
    elif total_ret >= 3:
        grade = "C"
        desc = f"净收益 {total_ret:.2f}%，佣金占比 {commission_pct:.0%}，勉强可接受"
    elif total_ret > 0:
        grade = "D"
        desc = f"净收益仅 {total_ret:.2f}%，佣金占比 {commission_pct:.0%}，利润微薄"
    else:
        grade = "F"
        desc = f"策略净亏损 {total_ret:.2f}%"

    return DimensionScore(
        grade=grade,
        score=GRADE_SCORES[grade],
        label="D5 扣除佣金后利润",
        description=desc,
        details={
            "gross_return": round(gross_ret, 2),
            "net_return": round(total_ret, 2),
            "commission_total": round(total_commission, 2),
            "commission_pct_of_gross": round(commission_pct, 4),
            "gross_pnl": round(gross_pnl, 2),
            "net_pnl": round(net_pnl, 2),
        },
    )


# ============================================================================
# 交易质量分析
# ============================================================================

def _analyze_trade_quality(trade_list):
    """最佳/最差交易"""
    closed = [t for t in trade_list if not t.get("partial")]
    if not closed:
        return {"best5": [], "worst5": []}

    sorted_by_pnl = sorted(closed, key=lambda t: t.get("pnl_amount", 0), reverse=True)
    best5 = sorted_by_pnl[:min(5, len(sorted_by_pnl))]
    worst5 = sorted_by_pnl[-min(5, len(sorted_by_pnl)):]
    worst5.reverse()

    def _fmt(t):
        sell_date = t.get("sell_date", "")
        if hasattr(sell_date, 'strftime'):
            sell_date = sell_date.strftime("%Y-%m-%d")
        return {
            "code": t.get("code", ""),
            "sell_date": sell_date,
            "pnl_pct": round(t.get("pnl_pct", 0), 2),
            "pnl_amount": round(t.get("pnl_amount", 0), 2),
            "reason": t.get("reason", ""),
        }

    return {
        "best5": [_fmt(t) for t in best5],
        "worst5": [_fmt(t) for t in worst5],
    }


# ============================================================================
# 主入口
# ============================================================================

def evaluate_report(report, strategy_tag="", start_date="", end_date=""):
    """对回测 report 进行 5 维度评测打分

    Args:
        report: sim.report() 返回的字典
        strategy_tag: 策略标签
        start_date: 回测起始日期
        end_date: 回测结束日期

    Returns:
        EvalResult
    """
    trade_list = report.get("trade_list", [])

    # 月度分析
    monthly_data = _compute_monthly_returns(trade_list)
    total_pnl = sum(m["pnl_amount"] for m in monthly_data)
    concentration = _compute_concentration(monthly_data, total_pnl)

    # 5 维度打分
    d1 = _score_drawdown(report)
    d2 = _score_risk_return(report)
    d3 = _score_monthly(monthly_data, concentration)
    d4 = _score_robustness(report, trade_list)
    d5 = _score_commission(report, trade_list)

    dimensions = {
        "drawdown": d1,
        "risk_return": d2,
        "monthly": d3,
        "robustness": d4,
        "commission": d5,
    }

    # 加权总分
    overall_score = sum(dim.score * WEIGHTS[k] for k, dim in dimensions.items())

    # 等级映射
    if overall_score >= 80:
        overall_grade = "A"
    elif overall_score >= 65:
        overall_grade = "B"
    elif overall_score >= 50:
        overall_grade = "C"
    elif overall_score >= 35:
        overall_grade = "D"
    else:
        overall_grade = "F"

    # 硬伤一票否决：D1 为 F 时总评上限 D
    if d1.grade == "F" and overall_grade in ("A", "B", "C"):
        overall_grade = "D"
        overall_score = min(overall_score, 44.9)

    # 判定
    if overall_grade in ("A", "B"):
        verdict = "PASS"
    elif overall_grade == "C":
        verdict = "WATCH"
    else:
        verdict = "FAIL"

    # 佣金分析
    commission_analysis = d5.details.copy()

    # 交易质量
    trade_quality = _analyze_trade_quality(trade_list)

    # 元信息
    report_meta = {
        "strategy_tag": strategy_tag,
        "start_date": str(start_date) if start_date else "-",
        "end_date": str(end_date) if end_date else "-",
        "trading_days": report.get("trading_days", 0),
        "eval_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    return EvalResult(
        dimensions=dimensions,
        overall_score=round(overall_score, 1),
        overall_grade=overall_grade,
        overall_verdict=verdict,
        monthly_data=monthly_data,
        concentration=concentration,
        commission_analysis=commission_analysis,
        trade_quality=trade_quality,
        report_meta=report_meta,
    )
