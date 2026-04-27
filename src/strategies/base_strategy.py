"""策略模板基类 - 提供停牌/涨跌停过滤、订单管理、日志等通用逻辑"""

from abc import abstractmethod

import backtrader as bt


class BaseStrategy(bt.Strategy):
    """
    策略模板，子类需实现:
      - indicators()    : 设置指标（在 __init__ 中调用）
      - buy_signal()    : 返回是否满足买入条件
      - sell_signal()   : 返回是否满足卖出条件
    """

    params = (
        ("print_log", True),
        ("position_pct", 0.9),
    )

    def __init__(self):
        self.order = None
        self.indicators()

    def indicators(self):
        """子类覆写：在此设置指标"""

    @abstractmethod
    def buy_signal(self) -> bool:
        ...

    @abstractmethod
    def sell_signal(self) -> bool:
        ...

    def is_suspended(self) -> bool:
        return self.data.volume[0] == 0

    def is_limit_up(self) -> bool:
        return self.data.close[0] >= self.data.high[0] * 0.995

    def is_limit_down(self) -> bool:
        return self.data.close[0] <= self.data.low[0] * 1.005

    def next(self):
        if self.order:
            return

        if self.is_suspended():
            return

        if not self.position:
            if self.buy_signal() and not self.is_limit_up():
                self.order = self.order_target_percent(target=self.p.position_pct)
                self.log(f"BUY  @ {self.data.close[0]:.2f}")
        else:
            if self.sell_signal() and not self.is_limit_down():
                self.order = self.order_target_percent(target=0.0)
                self.log(f"SELL @ {self.data.close[0]:.2f}")

    def notify_order(self, order):
        if order.status in [order.Completed, order.Canceled, order.Margin]:
            self.order = None

    def log(self, txt: str, dt=None):
        if self.p.print_log:
            dt = dt or self.data.datetime.date(0)
            print(f"[{dt.isoformat()}] {txt}")

    def stop(self):
        self.log(f"策略结束，组合价值: {self.broker.getvalue():.2f}")
