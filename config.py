"""回测系统集中配置"""

# 通达信
TDX_DIR = r"D:\Tools\tdx_64"
TDX_MARKET = "std"

# 资金与手续费
INITIAL_CASH = 100000.0
COMMISSION = 0.0003  # 万三

# 默认回测区间
DEFAULT_START_DATE = "2023-01-01"
DEFAULT_END_DATE = "2025-12-31"

# 默认股票
DEFAULT_STOCKS = ["600036"]

# KDJ 策略参数
KDJ_N = 9
KDJ_M1 = 3
KDJ_M2 = 3
MA_PERIOD = 5
POSITION_PCT = 0.9

# 大盘指数配置（V2策略）
MARKET_INDEX_CODE = "000001"  # 上证指数代码
MARKET_MACD_FAST = 12
MARKET_MACD_SLOW = 26
MARKET_MACD_SIGNAL = 9

# 黄白线B1策略参数
HUANGBAI_M1 = 14
HUANGBAI_M2 = 28
HUANGBAI_M3 = 57
HUANGBAI_M4 = 114
HUANGBAI_N = 20       # 近期振幅周期
HUANGBAI_M = 50       # 远期振幅周期
HUANGBAI_N1 = 3       # SHORT 周期
HUANGBAI_N2 = 21      # LONG 周期
HUANGBAI_T_PLUS_N = 3 # T+N 天
HUANGBAI_GC_LOOKBACK = 20  # 金叉回溯天数
STOCK_TYPE = "main"   # "main" 或 "tech"
SCAN_MAX_WORKERS = 10  # 全市场扫描线程数，None=自动(CPU核心数)，1=单线程

# 组合模拟参数（黄白线B1）
PORTFOLIO_INITIAL_CASH = 1_000_000   # 100万
PORTFOLIO_MAX_POSITIONS = 10         # 最多10只
PORTFOLIO_PER_POSITION = 100_000     # 每只10万

# 动能砖组合模拟参数
DNZH_INITIAL_CASH = 100_000          # 10万
DNZH_MAX_POSITIONS = 2               # 最多2只
DNZH_PER_POSITION = 50_000           # 每只5万
DNZH_T_PLUS_N = 2                    # 2日不拉升清仓
DNZH_MAX_HOLD_DAYS = 5               # 脱离成本5%以上持仓最多5天
DNZH_PROFIT_PCT = 5.0                # 脱离成本区百分比

# 日志
LOG_DIR = "logs"

# 绘图
PLOT_ENABLED = True
CHART_OUTPUT_DIR = "charts"
