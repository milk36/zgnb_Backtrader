# llmdoc Index - Backtrader + MyTT + mootdx 量化回测系统

## Feature Documents

- [项目总览](feature/project-overview.md): 系统整体架构、分层设计、核心数据流与关键设计决策。了解项目全貌时首先阅读此文档。
- [数据层：通达信数据馈送](feature/data-tdx-feed.md): TdxDataFeed 类的设计，mootdx Reader 封装，数据标准化流程（列名映射、日期索引、裁剪）。涉及数据获取与处理时参考。
- [指标层：KDJ 指标](feature/indicator-kdj.md): KDJIndicator 自定义指标的实现，MyTT 批量计算模式，通达信标准 KDJ 公式（SMA 而非 EMA）。开发新指标时参考此模式。
- [策略层：基类与 KDJ 金叉策略](feature/strategy-layer.md): BaseStrategy 模板基类的交易逻辑（停牌/涨跌停过滤、订单管理）与 KDJCrossStrategy 信号规则。开发新策略时必读。
- [策略层：黄白线金叉后B1策略](feature/strategy-huangbai-b1.md): HuangBaiB1Strategy 的完整设计——周线多头过滤、黄白线金叉检测、7种B1买入子条件、分层止盈止损与分批卖出逻辑。同时包含全市场选股扫描函数和组合级预加载函数（preload_all_signals）。理解该策略、开发类似复杂策略或涉及 --portfolio/--scan 模式时参考。
- [策略层：黄白线金叉后B1策略 V2](feature/strategy-huangbai-b1-v2.md): V1 的增强版本，新增大盘（上证指数）MACD 多头/空头过滤，空头时只卖不买。包含 V2 策略类、大盘 MACD 计算函数、扫描与预加载函数的变更说明，以及 PortfolioSimulator 适配逻辑。
- [策略层：黄白线金叉后B1策略 V3](feature/strategy-huangbai-b1-v3.md): B1 买入信号替换为通达信原始选股公式（单一复合条件取代7子条件），止盈止损逻辑与V1/V2完全相同。B1通过共享函数统一三处调用，消除手动同步问题。
- [引擎层：回测引擎](feature/engine-backtester.md): Backtester 类对 Cerebro 的封装（单股回测）；PortfolioSimulator 组合级日频模拟引擎（100万/10只/每只10万）。理解回测执行流程时参考。
- [CLI 使用指南](feature/cli-usage.md): main.py 的所有命令行参数、4 种运行模式、常用命令示例及参数与策略行为的对应关系。运行回测时首先阅读此文档。

- [策略层：动能+砖策略](feature/strategy-dongneng-zhuan.md): 动能评分+金砖共振双引擎选股策略，包含综合天命打分、阵营过滤、砖型图强红共振、T+1开盘买入及三级退出逻辑。
- [引擎层：动能砖组合模拟器](feature/engine-dnzh-simulator.md): DongnengZhuanSimulator 日频模拟引擎，T+1开盘买入、买入K线最低价止损、T+2不拉升清仓、盈利5%持仓止盈。

## SOP Documents

- [新增回测策略](sop/how-to-add-new-strategy.md): 从零新增一个自定义策略的完整步骤，包括创建文件、继承 BaseStrategy、添加指标、配置参数、修改入口。
