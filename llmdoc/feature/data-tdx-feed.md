# 数据层：通达信数据馈送 (TdxDataFeed)

## 1. Purpose

封装 mootdx Reader，从通达信本地数据目录读取日线行情，经标准化处理后返回 Backtrader 兼容的 `bt.feeds.PandasData`。

## 2. How it Works

### 类结构

`TdxDataFeed` 在构造时创建 `Reader.factory(market, tdxdir)` 实例，后续通过 `get_feed()` / `get_feeds()` 获取数据。

### 数据标准化流程 (`_normalize`)

1. **日期处理**：`date` 列转 `DatetimeIndex` 并设为索引
2. **列名映射**：`vol` -> `volume`（Backtrader 要求）
3. **补充列**：添加 `openinterest = 0`
4. **列筛选**：仅保留 `open, high, low, close, volume, openinterest`
5. **排序与裁剪**：按日期排序后 `loc[start:end]` 截取区间

### 关键接口

| 方法 | 说明 |
|---|---|
| `get_feed(symbol, start, end)` | 单只股票，返回 `bt.feeds.PandasData` |
| `get_feeds(symbols, start, end)` | 多只股票，返回 `list[bt.feeds.PandasData]` |

## 3. Relevant Code Modules

- `src/data/tdx_feed.py` - TdxDataFeed 类
- `config.py` - `TDX_DIR`, `TDX_MARKET`, `DEFAULT_START_DATE`, `DEFAULT_END_DATE`

## 4. Attention

- 数据来源为通达信本地文件，必须先下载对应股票的日线数据
- `_normalize` 中 `loc[start:end]` 要求索引为 DatetimeIndex，否则会报错
- 找不到数据时抛出 `ValueError`，上层需捕获
