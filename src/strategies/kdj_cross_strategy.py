"""KDJ 金叉策略 - K/D 金叉 + MA5 过滤买入，K/D 死叉卖出"""

import backtrader as bt

from src.indicators.kdj_indicator import KDJIndicator
from src.strategies.base_strategy import BaseStrategy


class KDJCrossStrategy(BaseStrategy):
    """
    KDJ 金叉 + 5 日均线过滤策略

    买入: K 上穿 D（金叉） 且 收盘价 > MA5
    卖出: K 下穿 D（死叉）
    """

    params = (
        ("kdj_n", 9),
        ("kdj_m1", 3),
        ("kdj_m2", 3),
        ("ma_period", 5),
        ("position_pct", 0.9),
        ("print_log", True),
    )

    def indicators(self):
        self.kdj = KDJIndicator(
            self.data, n=self.p.kdj_n, m1=self.p.kdj_m1, m2=self.p.kdj_m2
        )
        self.ma5 = bt.ind.SMA(self.data.close, period=self.p.ma_period)
        self.kd_cross = bt.ind.CrossOver(self.kdj.K, self.kdj.D)

    def buy_signal(self) -> bool:
        return self.kd_cross > 0 and self.data.close[0] > self.ma5[0]

    def sell_signal(self) -> bool:
        return self.kd_cross < 0
