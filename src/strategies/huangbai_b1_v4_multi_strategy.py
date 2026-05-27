"""黄白线B1策略 V4 多仓版

与 V4 信号完全相同，仅组合模拟参数不同：
- 不限制最大持仓数量
- 每日最多买入前2只最符合条件的股票
- 退出逻辑与V4完全相同

信号计算直接复用 V4 的 preload_all_signals。
"""

from src.strategies.huangbai_b1_v4_strategy import (
    preload_all_signals as _preload_v4,
    scan_all as _scan_v4,
)


def preload_all_signals(start="2024-01-01", end="2025-12-31",
                        stock_type="main", max_workers=None,
                        tdxdir=None, market=None):
    """预加载全市场信号（复用V4）"""
    from config import TDX_DIR, TDX_MARKET, SCAN_MAX_WORKERS
    return _preload_v4(
        start=start, end=end,
        stock_type=stock_type,
        max_workers=max_workers or SCAN_MAX_WORKERS,
        tdxdir=tdxdir or TDX_DIR,
        market=market or TDX_MARKET,
    )


def scan_all(stock_type="main", skip_weekly=False,
             tdxdir=None, market=None, max_workers=None,
             skip_on_bear=False):
    """全市场扫描（复用V4）"""
    from config import TDX_DIR, TDX_MARKET, SCAN_MAX_WORKERS
    return _scan_v4(
        stock_type=stock_type,
        skip_weekly=skip_weekly,
        tdxdir=tdxdir or TDX_DIR,
        market=market or TDX_MARKET,
        max_workers=max_workers or SCAN_MAX_WORKERS,
        skip_on_bear=skip_on_bear,
    )
