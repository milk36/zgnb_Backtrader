"""回测引擎 - 封装 Cerebro 的配置、执行、分析和绘图"""

import backtrader as bt

from config import INITIAL_CASH, COMMISSION


class Backtester:
    """回测引擎，封装 Cerebro 生命周期"""

    def __init__(self, cash: float = INITIAL_CASH, commission: float = COMMISSION):
        self._cerebro = bt.Cerebro()
        self._cerebro.broker.setcash(cash)
        self._cerebro.broker.setcommission(commission=commission)

    def add_feed(self, feed: bt.feeds.PandasData, name: str = None):
        self._cerebro.adddata(feed, name=name)

    def add_strategy(self, strategy_cls: type, **kwargs):
        self._cerebro.addstrategy(strategy_cls, **kwargs)

    def _add_analyzers(self):
        self._cerebro.addanalyzer(bt.analyzers.SharpeRatio, _name="sharpe")
        self._cerebro.addanalyzer(bt.analyzers.DrawDown, _name="drawdown")
        self._cerebro.addanalyzer(bt.analyzers.Returns, _name="returns")
        self._cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name="trades")

    def run(self) -> dict:
        self._add_analyzers()
        initial = self._cerebro.broker.getvalue()
        results = self._cerebro.run()
        strat = results[0]
        final = self._cerebro.broker.getvalue()

        report = {
            "initial_cash": initial,
            "final_value": final,
            "total_return": (final - initial) / initial * 100,
            "sharpe": strat.analyzers.sharpe.get_analysis(),
            "drawdown": strat.analyzers.drawdown.get_analysis(),
            "returns": strat.analyzers.returns.get_analysis(),
            "trades": strat.analyzers.trades.get_analysis(),
        }
        return report

    def plot(self, **kwargs):
        self._cerebro.plot(**kwargs)

    @staticmethod
    def print_report(report: dict):
        print("=" * 50)
        print("          回 测 报 告")
        print("=" * 50)
        print(f"  初始资金:    {report['initial_cash']:>12,.2f}")
        print(f"  最终资金:    {report['final_value']:>12,.2f}")
        print(f"  总收益率:    {report['total_return']:>11.2f}%")

        dd = report["drawdown"]
        print(f"  最大回撤:    {dd.max.drawdown:>11.2f}%")

        sharpe = report["sharpe"].get("sharperatio")
        print(f"  夏普比率:    {sharpe:>11.4f}" if sharpe else "  夏普比率:        N/A")

        ta = report["trades"]
        total_closed = ta.get("total", {}).get("closed", 0)
        won = ta.get("won", {}).get("total", 0)
        lost = ta.get("lost", {}).get("total", 0)
        print(f"  总交易次数:  {total_closed:>12}")
        print(f"  盈利次数:    {won:>12}")
        print(f"  亏损次数:    {lost:>12}")
        if won + lost > 0:
            print(f"  胜率:        {won / (won + lost) * 100:>11.2f}%")
        print("=" * 50)
