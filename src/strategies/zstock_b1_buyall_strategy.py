"""ZStock B1 全仓版

与 ZStock B1 信号完全相同，仅组合模拟参数不同：
- 不限制总资金（10亿模拟无限资金）
- 不限制最大持仓数量
- 每日买入所有符合条件的候选股票
- 退出逻辑与 ZStock B1 完全相同

信号计算直接复用 ZStock B1 的 preload_all_signals。
"""

from src.strategies.zstock_b1_strategy import (
    preload_all_signals as _preload_zstock_b1,
    scan_all as _scan_zstock_b1,
)


def preload_all_signals(start, end, stock_type="main", max_workers=None,
                        tdxdir=None, market=None):
    """预加载全市场信号（复用 ZStock B1）"""
    from config import TDX_DIR, TDX_MARKET, SCAN_MAX_WORKERS
    return _preload_zstock_b1(
        start=start, end=end,
        stock_type=stock_type,
        max_workers=max_workers or SCAN_MAX_WORKERS,
        tdxdir=tdxdir or TDX_DIR,
        market=market or TDX_MARKET,
    )


def scan_all(stock_type="main", tdxdir=None, market=None, max_workers=None):
    """全市场扫描（复用 ZStock B1）"""
    from config import TDX_DIR, TDX_MARKET, SCAN_MAX_WORKERS
    return _scan_zstock_b1(
        stock_type=stock_type,
        tdxdir=tdxdir or TDX_DIR,
        market=market or TDX_MARKET,
        max_workers=max_workers or SCAN_MAX_WORKERS,
    )
