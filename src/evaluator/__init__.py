"""回测结果评测系统 — 5 维度打分 + HTML 报告"""

from src.evaluator.scorer import evaluate_report
from src.evaluator.html_report import generate_eval_html

__all__ = ["evaluate_report", "generate_eval_html"]
