# 数据层：通达信数据馈送 (TdxDataFeed)

## 1. Purpose

封装 mootdx Reader，从通达信本地数据目录读取日线行情，经前复权调整和标准化处理后返回 Backtrader 兼容的 `bt.feeds.PandasData`。

## 2. How it Works

### 数据流总览

```
mootdx Reader -> 原始 DataFrame -> apply_qfq() 前复权 -> _normalize() 标准化 -> bt.feeds.PandasData
```

`get_feed()` 在读取原始数据后立即调用 `apply_qfq(df, symbol)` 进行前复权，再执行 `_normalize`。

### 前复权模块 (`adjustment.py`)

通过 akshare 获取前复权收盘价，计算复权比例并乘以 OHLC 列，消除除权除息日价格断崖。

**核心数据流**: `ratio = akshare_qfq_close / mootdx_raw_close`，对 OHLC 四列乘以 ratio，volume 不调整。

**三级缓存机制**（`get_qfq_close()`）:
1. 内存缓存 (`_memory_cache` dict) -- 命中直接返回
2. 磁盘 parquet 缓存 (`cache/qfq/{code}.parquet`) -- 加载后写入内存缓存
3. akshare 网络请求 (`_fetch_qfq_from_akshare()`) -- 获取后写入磁盘和内存

**批量操作**:
- `preload_disk_cache()` -- 将全部磁盘缓存加载到内存，在组合模拟子进程初始化时调用
- `update_cache_batch(codes)` -- 批量从 akshare 获取并写入磁盘缓存（CLI `--update-qfq-cache` 调用）

**指数跳过**: `is_index()` 识别上证指数(000001)和深证指数(399xxx/899xxx)，不做复权。

### 类结构 (`TdxDataFeed`)

构造时创建 `Reader.factory(market, tdxdir)` 实例，后续通过 `get_feed()` / `get_feeds()` 获取数据。

### 数据标准化流程 (`_normalize`)

1. **日期处理**: `date` 列转 `DatetimeIndex` 并设为索引
2. **列名映射**: `vol` -> `volume`（Backtrader 要求）
3. **补充列**: 添加 `openinterest = 0`
4. **列筛选**: 仅保留 `open, high, low, close, volume, openinterest`
5. **排序与裁剪**: 按日期排序后 `loc[start:end]` 截取区间

### 关键接口

| 方法 | 说明 |
|---|---|
| `get_feed(symbol, start, end)` | 单只股票，返回 `bt.feeds.PandasData` |
| `get_feeds(symbols, start, end)` | 多只股票，返回 `list[bt.feeds.PandasData]` |
| `apply_qfq(df, code)` | 对 DataFrame 应用前复权（OHLC * ratio） |
| `get_qfq_close(code)` | 获取前复权收盘价 Series（三级缓存） |
| `preload_disk_cache()` | 批量加载磁盘缓存到内存 |
| `update_cache_batch(codes)` | 批量更新磁盘缓存 |

### 前复权在策略中的集成

所有策略的 `_init_process`（子进程初始化）中调用 `preload_disk_cache()`，`_scan_one` 和 `_scan_one_all_bars` 中对 mootdx 原始数据调用 `apply_qfq(df, code)`。涉及的策略文件:
- `huangbai_b1_strategy.py` -- 3 处
- `huangbai_b1_v2_strategy.py` -- 3 处
- `huangbai_b1_v3_strategy.py` -- 3 处
- `dongneng_zhuan_strategy.py` -- 3 处

## 3. Relevant Code Modules

- `src/data/tdx_feed.py` - TdxDataFeed 类
- `src/data/adjustment.py` - 前复权核心模块（apply_qfq / get_qfq_close / preload_disk_cache / update_cache_batch）
- `config.py` - `TDX_DIR`, `TDX_MARKET`, `QFQ_CACHE_ENABLED`, `QFQ_CACHE_DIR`
- `main.py` - `--update-qfq-cache`, `--update-qfq-codes` CLI 参数

## 4. Attention

- 数据来源为通达信本地文件，必须先下载对应股票的日线数据
- 前复权依赖 akshare 网络数据，首次运行需执行 `python main.py --update-qfq-cache` 全市场更新缓存
- `QFQ_CACHE_ENABLED = False` 可全局关闭前复权（config.py）
- 指数代码（000001 / 399xxx / 899xxx）自动跳过复权
- 缓存文件位于 `cache/qfq/{code}.parquet`，每只股票约 20-50KB
- `_normalize` 中 `loc[start:end]` 要求索引为 DatetimeIndex，否则会报错
- 找不到数据时抛出 `ValueError`，上层需捕获
