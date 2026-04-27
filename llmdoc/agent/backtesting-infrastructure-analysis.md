# 回测基础设施分析：引擎、数据、策略、CLI 流程

## Code Sections

### 1. Backtester 类 (`src/engine/backtester.py`)

- `src/engine/backtester.py:11~14` (构造函数): 创建 `bt.Cerebro()` 实例，通过 `broker.setcash()` 和 `broker.setcommission()` 配置初始资金与佣金

  ```python
  def __init__(self, cash: float = INITIAL_CASH, commission: float = COMMISSION):
      self._cerebro = bt.Cerebro()
      self._cerebro.broker.setcash(cash)
      self._cerebro.broker.setcommission(commission=commission)
  ```

- `src/engine/backtester.py:16~17` (`add_feed`): 调用 `cerebro.adddata(feed, name=name)` 添加数据源。多数据馈送通过多次调用实现，每次传入不同 `name`

- `src/engine/backtester.py:22~26` (`_add_analyzers`): 在 `run()` 内部调用，挂载 4 个分析器

  ```python
  self._cerebro.addanalyzer(bt.analyzers.SharpeRatio, _name="sharpe")
  self._cerebro.addanalyzer(bt.analyzers.DrawDown, _name="drawdown")
  self._cerebro.addanalyzer(bt.analyzers.Returns, _name="returns")
  self._cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name="trades")
  ```

- `src/engine/backtester.py:28~44` (`run`): 每次调用先 `_add_analyzers()`，执行 `cerebro.run()`，取 `results[0]` 策略实例提取分析结果。报告字典含 `initial_cash`/`final_value`/`total_return`/`sharpe`/`drawdown`/`returns`/`trades`

- `src/engine/backtester.py:49~73` (`print_report`): 静态方法格式化输出：初始/最终资金、总收益率、最大回撤(`dd.max.drawdown`)、夏普比率、交易次数、盈利/亏损次数、胜率

### 2. TdxDataFeed (`src/data/tdx_feed.py`)

- `src/data/tdx_feed.py:13~14` (构造): `Reader.factory(market=market, tdxdir=tdxdir)` 创建 mootdx Reader，`market` 默认 `"std"`

- `src/data/tdx_feed.py:16~26` (`get_feed`): 调用 `self._reader.daily(symbol=symbol)` 获取 DataFrame -> `_normalize()` -> `bt.feeds.PandasData(dataname=df)`。数据为空时抛出 `ValueError`

- `src/data/tdx_feed.py:28~34` (`get_feeds`): 循环调用 `get_feed` 返回列表

- `src/data/tdx_feed.py:37~58` (`_normalize`): 静态方法执行标准化流程：
  1. `date` 列转 `DatetimeIndex` 并设索引
  2. `vol` -> `volume` 列名映射
  3. 添加 `openinterest = 0`
  4. 仅保留 `[open, high, low, close, volume, openinterest]`
  5. `sort_index()` 后 `loc[start:end]` 裁剪

### 3. BaseStrategy (`src/strategies/base_strategy.py`)

- `src/strategies/base_strategy.py:16~19` (params): `print_log=True`, `position_pct=0.9`

- `src/strategies/base_strategy.py:21~23` (`__init__`): 初始化 `self.order = None`，调用子类的 `indicators()`

- `src/strategies/base_strategy.py:28~34` (抽象方法): `buy_signal()` 和 `sell_signal()` 为 `@abstractmethod`

- `src/strategies/base_strategy.py:36~43` (涨跌停判断):
  - 停牌: `volume[0] == 0`
  - 涨停: `close >= high * 0.995`
  - 跌停: `close <= low * 1.005`

- `src/strategies/base_strategy.py:45~59` (`next`): 模板方法逻辑：
  1. 有未完成订单 -> 跳过
  2. 停牌 -> 跳过
  3. 无持仓 + `buy_signal()` + 非涨停 -> `order_target_percent(target=position_pct)`
  4. 有持仓 + `sell_signal()` + 非跌停 -> `order_target_percent(target=0.0)`

- `src/strategies/base_strategy.py:61~63` (`notify_order`): 订单状态为 Completed/Canceled/Margin 时清除 `self.order`。**Rejected 状态未处理**

### 4. HuangBaiB1Strategy 覆写模式 (`src/strategies/huangbai_b1_strategy.py`)

- `src/strategies/huangbai_b1_strategy.py:92~99` (`__init__`): 额外初始化 `buy_info`/`stop_loss_price`/`hold_until_below_white`/`initial_size` 状态变量

- `src/strategies/huangbai_b1_strategy.py:305~314` (`next` 覆写): **完全覆写** BaseStrategy.next()，不使用模板模式：
  - 无持仓 -> `_check_entry()`
  - 有持仓 -> `_check_exit()`

- `src/strategies/huangbai_b1_strategy.py:316~351` (`_check_entry`): 三级过滤 (周线多头 + 金叉 + B1信号)，通过后 `order_target_percent(target=0.1)`。止损价根据收盘价与白线关系确定

- `src/strategies/huangbai_b1_strategy.py:353~404` (`_check_exit`): 分层出场逻辑：
  1. 止损 (price <= stop_loss_price)
  2. T+N 没涨清仓 (bars_held >= t_plus_n 且 price <= 买入价)
  3. `hold_until_below_white` 标志下跌破白线清仓
  4. 涨停卖 1/2
  5. 中阳卖 1/3 (main:5%, tech:10%)

- `src/strategies/huangbai_b1_strategy.py:435~439` (`buy_signal`/`sell_signal`): 返回 `False`，因该策略完全覆写 `next()` 不使用模板

### 5. CLI 流程 (`main.py`)

- `main.py:25~28` (`STRATEGIES`): 策略注册字典，`kdj` -> `KDJCrossStrategy`, `huangbai` -> `HuangBaiB1Strategy`

- `main.py:45~77` (`_run_backtest`): **逐只股票串行回测**，每只股票创建独立 Backtester 实例。固定使用 `HuangBaiB1Strategy`（硬编码，不使用 `args.strategy`）。累计收益/交易统计输出汇总

- `main.py:80~128` (`main`): 两段式流程：
  - **扫描模式** (`--scan` 或无 `--symbol`): `scan_all()` -> 可选 `_run_backtest(codes, args)`
  - **指定股票模式**: 单个 Backtester 实例，支持多 symbol 通过 `add_feed` 添加

### 6. 全市场扫描 (`src/strategies/huangbai_b1_strategy.py`)

- `src/strategies/huangbai_b1_strategy.py:670~735` (`scan_all`): 使用 `ProcessPoolExecutor` 多进程扫描，`_init_process` 为每个子进程创建独立 Reader，`_scan_one` 读取日线数据并调用 `_compute_signals`

- `src/strategies/huangbai_b1_strategy.py:446~462` (`_get_all_codes`): 从通达信本地 `vipdoc/sz|sh/lday` 目录解析 `.day` 文件名提取全部 A 股代码

## Report

### conclusions

- Backtester 是对 `bt.Cerebro` 的薄封装：构造时配置 broker，`add_feed` 映射到 `adddata`，`run()` 内部每次重新挂载 4 个分析器（SharpeRatio/DrawDown/Returns/TradeAnalyzer），取 `results[0]` 的第一个策略实例提取报告
- 多数据馈送支持通过多次 `add_feed(name=...)` 实现。在 `_run_backtest` 流程中，每只股票使用独立 Backtester 实例（而非多数据单 Cerebro）
- TdxDataFeed 通过 mootdx `Reader.factory` 读取通达信本地日线 `.day` 文件，输出 DataFrame 经 `_normalize` 标准化后包装为 `bt.feeds.PandasData`
- BaseStrategy 提供模板模式（`indicators`/`buy_signal`/`sell_signal`），含停牌/涨跌停过滤和 `order_target_percent` 仓位管理。`notify_order` 未处理 Rejected 状态
- HuangBaiB1Strategy 完全覆写 `next()`，不使用模板模式。`buy_signal()`/`sell_signal()` 返回 `False`。出场逻辑含止损/T+N清仓/分批卖出/跌破白线清仓四个层次
- `_run_backtest()` 函数硬编码使用 `HuangBaiB1Strategy`，不读取 `args.strategy` 参数，这与 `main()` 中指定股票模式下根据参数选择策略的行为不一致

### relations

- `main.py:60` -> `src/strategies/huangbai_b1_strategy.py:HuangBaiB1Strategy` (`_run_backtest` 硬编码策略类)
- `main.py:82` -> `main.py:25~28` (`STRATEGIES` 字典查找策略类)
- `main.py:115~118` -> `src/engine/backtester.py:19~20` (指定股票模式根据策略类传参)
- `src/engine/backtester.py:28~30` -> `src/engine/backtester.py:22~26` (`run()` 调用 `_add_analyzers()`)
- `src/data/tdx_feed.py:22` -> `mootdx.reader.Reader` (`_reader.daily(symbol=...)`)
- `src/strategies/base_strategy.py:45~59` (模板 `next`) 被 `src/strategies/kdj_cross_strategy.py` 使用
- `src/strategies/huangbai_b1_strategy.py:305~314` 完全覆写 `next()`，绕过 BaseStrategy 模板
- `config.py:4` (`TDX_DIR`) -> `src/data/tdx_feed.py:13`, `src/strategies/huangbai_b1_strategy.py:670` (共享通达信路径配置)

### result

四大模块协作流程：
1. `TdxDataFeed` 从 mootdx Reader 获取通达信本地日线数据，经 `_normalize` 标准化为 `PandasData`
2. `Backtester` 封装 Cerebro 生命周期，挂载 4 个分析器，`run()` 返回结构化报告字典
3. `BaseStrategy` 提供模板模式（三个抽象方法），但 `HuangBaiB1Strategy` 完全覆写 `next()` 实现复杂的多层出场逻辑
4. `main.py` CLI 支持两种模式：全市场扫描（`scan_all` 多进程）后批量回测，或指定股票单次回测

### attention

- `_run_backtest()` (main.py:60) 硬编码 `HuangBaiB1Strategy`，`--strategy kdj --scan` 组合下扫描后的回测仍使用 huangbai 策略
- `notify_order` 不处理 `Rejected` 状态，此时 `self.order` 不会被清除，可能导致后续所有 bar 被跳过
- `run()` 每次调用都 `_add_analyzers()`，若对同一 Backtester 实例多次调用 `run()` 会重复添加分析器
- `_run_backtest` 中每只股票创建独立 Backtester 实例，但多个实例共享同一 `feed_provider`（TdxDataFeed），而 TdxDataFeed 内部 Reader 是有状态的
