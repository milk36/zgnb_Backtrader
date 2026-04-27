"""回测系统集中配置"""

# 通达信
TDX_DIR = r"D:\Tools\tdx_64"
TDX_MARKET = "std"

# 资金与手续费
INITIAL_CASH = 100000.0
COMMISSION = 0.0003  # 万三

# 默认回测区间
DEFAULT_START_DATE = "2020-01-01"
DEFAULT_END_DATE = "2025-12-31"

# 默认股票
DEFAULT_STOCKS = ["600036"]

# KDJ 策略参数
KDJ_N = 9
KDJ_M1 = 3
KDJ_M2 = 3
MA_PERIOD = 5
POSITION_PCT = 0.9

# 绘图
PLOT_ENABLED = True
