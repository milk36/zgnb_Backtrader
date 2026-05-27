"""完美B1 V2 多仓版

与完美B1 V2 信号完全相同，仅组合模拟参数不同：
- 不限制最大持仓数量
- 每日最多买入前2只最符合条件的股票
- 退出逻辑与完美B1 V2完全相同

信号计算直接复用完美B1 V2 的 preload_all_signals。
"""

from src.strategies.perfect_b1_v2_strategy import (
    preload_all_signals as _preload_pb1v2,
    scan_all as _scan_pb1v2,
)


def preload_all_signals(start="2024-01-01", end="2025-12-31",
                        stock_type="main", max_workers=None,
                        tdxdir=None, market=None):
    """预加载全市场信号（复用完美B1 V2）"""
    from config import TDX_DIR, TDX_MARKET, SCAN_MAX_WORKERS
    return _preload_pb1v2(
        start=start, end=end,
        stock_type=stock_type,
        max_workers=max_workers or SCAN_MAX_WORKERS,
        tdxdir=tdxdir or TDX_DIR,
        market=market or TDX_MARKET,
    )


def scan_all(stock_type="main", skip_weekly=False,
             tdxdir=None, market=None, max_workers=None,
             skip_on_bear=False):
    """全市场扫描（复用完美B1 V2）"""
    from config import TDX_DIR, TDX_MARKET, SCAN_MAX_WORKERS
    return _scan_pb1v2(
        stock_type=stock_type,
        skip_weekly=skip_weekly,
        tdxdir=tdxdir or TDX_DIR,
        market=market or TDX_MARKET,
        max_workers=max_workers or SCAN_MAX_WORKERS,
        skip_on_bear=skip_on_bear,
    )
