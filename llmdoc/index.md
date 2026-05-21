# llmdoc Index - Backtrader + MyTT + mootdx 量化回测系统

## Feature Documents

- [项目总览](feature/project-overview.md): 系统整体架构、分层设计、核心数据流与关键设计决策。了解项目全貌时首先阅读此文档。
- [数据层：通达信数据馈送](feature/data-tdx-feed.md): TdxDataFeed 类的设计，mootdx Reader 封装，前复权调整模块（akshare + parquet 三级缓存），MinuteFeed 分钟线前复权处理，数据标准化流程。涉及数据获取、前复权配置与处理时参考。
- [指标层：KDJ 指标](feature/indicator-kdj.md): KDJIndicator 自定义指标的实现，MyTT 批量计算模式，通达信标准 KDJ 公式（SMA 而非 EMA）。开发新指标时参考此模式。
- [策略层：基类与 KDJ 金叉策略](feature/strategy-layer.md): BaseStrategy 模板基类的交易逻辑（停牌/涨跌停过滤、订单管理）与 KDJCrossStrategy 信号规则。开发新策略时必读。
- [策略层：黄白线金叉后B1策略](feature/strategy-huangbai-b1.md): HuangBaiB1Strategy 的完整设计——周线多头过滤、黄白线金叉检测、7种B1买入子条件、分层止盈止损与分批卖出逻辑。同时包含全市场选股扫描函数和组合级预加载函数（preload_all_signals）。理解该策略、开发类似复杂策略或涉及 --portfolio/--scan 模式时参考。
- [策略层：黄白线金叉后B1策略 V2](feature/strategy-huangbai-b1-v2.md): V1 的增强版本，新增大盘（上证指数）MACD 多头/空头过滤，空头时只卖不买。包含 V2 策略类、大盘 MACD 计算函数、扫描与预加载函数的变更说明，以及 PortfolioSimulator 适配逻辑。
- [策略层：黄白线金叉后B1策略 V3](feature/strategy-huangbai-b1-v3.md): B1 买入信号替换为通达信原始选股公式（单一复合条件取代7子条件），止盈止损逻辑与V1/V2完全相同。B1通过共享函数统一三处调用，消除手动同步问题。
- [策略层：黄白线B1策略 V4](feature/strategy-huangbai-b1-v4.md): V2 的变体版本，移除黄白线金叉条件，新增60日动能信号过滤（综合天命打分+阵营过滤+硬性过滤，复用动能砖策略核心逻辑，不含流通市值过滤）。保留大盘MACD过滤、周线多头、B1七子条件、vol_expand_ok过滤链（含S1/大风车/长上影线/阶梯出货四重排除）和动量持股逻辑。`recent_gc` 返回全True数组以兼容 PortfolioSimulator。
- [策略层：黄白线B1策略 V5（战法退出逻辑）](feature/strategy-huangbai-b1-v5.md): 买入逻辑与 V2 完全相同，退出逻辑全部重写为文章战法的六级退出体系（硬止损→放量跌停→S1信号→两根中阴线→白线次日确认→放飞减仓）。引入关键K支撑、缩量不卖保护、加速检测等新概念，移除T+N、盈利100%清仓、动量持股、半仓持股等V2出场机制。PortfolioSimulator 新增 `_check_exits_v5` 专用退出路径。
- [策略层：黄白线B2倍量柱策略](feature/strategy-huangbai-b2.md): 包装V4信号计算，将B1入场替换为"前日B1+当日倍量柱"时序联动条件，移除动能过滤。倍量柱定义：VOL>1.8*REF(VOL,1) AND C>O AND VOL>MA(VOL,40) 且首次出现。复用PortfolioSimulator标准六级退出，100万/10只。理解B2包装架构或倍量柱逻辑时参考。
- [策略层：黄白线B2_V2倍量柱策略](feature/strategy-huangbai-b2-v2.md): B2增强版，新增30日B1频次过滤（至少2次B1信号且间隔≥5天），排序改为缩量升序+流动市值降序并只取前1支。其余架构与B2一致（包装V4、倍量柱入场、复用六级退出）。
- [引擎层：回测引擎](feature/engine-backtester.md): Backtester 类对 Cerebro 的封装（单股回测）；PortfolioSimulator 组合级日频模拟引擎（100万/10只/每只10万）。理解回测执行流程时参考。
- [图表层：组合模拟K线图生成器](feature/charting-kline.md): 组合模拟交易结果的 K 线图生成模块，蜡烛图+指标线+B1信号标记+买卖标记+止损/成本线。涉及 `--chart` 参数或图表样式修改时参考。
- [CLI 使用指南](feature/cli-usage.md): main.py 的所有命令行参数、4 种运行模式、常用命令示例及参数与策略行为的对应关系。运行回测时首先阅读此文档。

- [策略层：动能+砖策略](feature/strategy-dongneng-zhuan.md): 动能评分先筛→金砖共振再筛→筹码密集过滤的串行过滤策略，包含综合天命打分、阵营过滤、流通市值过滤、砖型图强红共振（含前日缩量/大涨横盘放量/砖块质量三个附加条件）、60日VWAP筹码密集、T+1开盘买入及三级退出逻辑。
- [引擎层：动能砖组合模拟器](feature/engine-dnzh-simulator.md): DongnengZhuanSimulator 日频+分钟级模拟引擎，T+1分钟确认买入（可降级为日线）、T+1合规检查、五级退出（止损→涨停清仓→涨幅2%部分卖出→T+2不拉升→盈利止盈）、模拟结束强制清仓、MinuteFeed分钟数据支持。通过 strategy_tag 和参数差异化，N型砖策略复用同一模拟器。
- [引擎层：N型B1组合级模拟器](feature/engine-nxing-b1-simulator.md): NxingB1Simulator 日频模拟引擎，复用PortfolioSimulator六级退出逻辑，100万/10只/每只10万，T+1开盘价买入，按缩量评分升序取最优1只，冷却期10个交易日。涉及N型B1组合模拟回测时参考。
- [策略层：N型+砖策略](feature/strategy-nxing-zhuan.md): 动能砖的变体策略，仅使用金砖共振信号选股（跳过动能预过滤和筹码密集过滤），外加流通市值>50亿过滤。T+1日线开盘买入（无分钟确认），复用 DongnengZhuanSimulator 模拟器。
- [策略层：N型B1选股策略](feature/strategy-nxing-b1-scan.md): N型B1策略支持选股扫描和组合级模拟两种模式。选股扫描：60日内>=2次B1信号（间隔>=30天）且价格逐次抬高的N型结构筛选，统计T+3涨幅胜率。组合模拟：预加载全市场N型B1信号，100万/10只/每只10万，T+1开盘买入，六级退出。复用V4的B1七子条件和vol_expand_ok过滤链。

## SOP Documents

- [新增回测策略](sop/how-to-add-new-strategy.md): 从零新增一个自定义策略的完整步骤，包括创建文件、继承 BaseStrategy、添加指标、配置参数、修改入口。
