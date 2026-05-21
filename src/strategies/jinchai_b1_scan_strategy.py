"""金叉B1选股策略

白线刚刚金叉黄线后出现B1信号的股票筛选（纯选股，不做买卖操作）

筛选条件：
1. 白线刚刚金叉黄线上来，金叉之后出现B1信号
2. 股票流通市值50亿以上
3. 前期放量上涨支撑（排除缩量上涨、放量下跌、阶梯出货、长上影线、S1/大风车）
4. 8项假案例排除过滤
5. 统计选股后T+5涨幅超过10%的概率
6. 支持指定日期区间筛选
"""

import os
import re
import shutil
import time
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore", category=RuntimeWarning)
from mootdx.reader import Reader

from config import (
    TDX_DIR, TDX_MARKET, SCAN_MAX_WORKERS, STOCK_TYPE,
    HUANGBAI_M1, HUANGBAI_M2, HUANGBAI_M3, HUANGBAI_M4,
    HUANGBAI_N, HUANGBAI_M, HUANGBAI_N1, HUANGBAI_N2,
    HUANGBAI_VOL_EXPAND_PERIOD, HUANGBAI_VOL_EXPAND_MIN,
    HUANGBAI_SURGE_PRICE_PCT, HUANGBAI_SURGE_VOL_RATIO,
    HUANGBAI_S1_PERIOD,
    HUANGBAI_S1_HIGH_PERIOD, HUANGBAI_S1_HIGH_RATIO,
    HUANGBAI_S1_ACCEL_PCT, HUANGBAI_S1_ACCEL_LOOKBACK,
    HUANGBAI_STEPPED_DROP_PCT, HUANGBAI_STEPPED_DROP_LOOKBACK,
    DNZH_MIN_MARKET_CAP,
    CHART_OUTPUT_DIR,
)

from src.strategies.dongneng_zhuan_strategy import _load_capital_data
from src.strategies.nxing_b1_scan_strategy import (
    _compute_all_bar_b1_and_filters,
    _find_gc_b1_pattern,
    _compute_t3_stats,
    _exclude_rapid_rise_flat_dist,
    _exclude_irregular_rise,
    _exclude_b1_death_cross,
    _exclude_gap_up_sideways_dump,
    _exclude_s1_top_volume,
    _exclude_discontinuous_rise,
    _exclude_stepped_volume_dist,
    _exclude_pre_b1_limit_down,
    _get_all_codes,
)

# ---------- 金叉B1参数 ----------
JCB1_GC_MAX_BARS = 30       # 金叉距今最大bar数（"刚刚金叉"）
JCB1_LOOKBACK = 60          # 回看天数
JCB1_T5_DAYS = 5            # T+5统计天数
JCB1_T5_TARGET_PCT = 10.0   # T+5涨幅目标(%)

# ---------- 进程池全局变量 ----------
_jc_reader = None
_jc_capital = None


def _jc_init_process(tdxdir, market, capital_data):
    global _jc_reader, _jc_capital
    _jc_reader = Reader.factory(market=market, tdxdir=tdxdir)
    _jc_capital = capital_data
    from src.data.adjustment import preload_disk_cache
    preload_disk_cache()


def _scan_one_stock(code, params, start_date=None, end_date=None):
    """扫描单只股票：检测金叉+B1模式"""
    assert _jc_reader is not None, "_jc_reader 未初始化"
    try:
        df = _jc_reader.daily(symbol=code)
        if df is None or len(df) < 300:
            return code, None
        df = df.sort_index()
        from src.data.adjustment import apply_qfq
        df = apply_qfq(df, code)

        if end_date is not None:
            end_ts = pd.Timestamp(end_date)
            if df.index[0] > end_ts:
                return code, None
            df = df[df.index <= end_ts]
            if len(df) < 300:
                return code, None

        C = df["close"].values.astype(float)
        H = df["high"].values.astype(float)
        L = df["low"].values.astype(float)
        O = df["open"].values.astype(float)
        V = df["vol"].values.astype(float) if "vol" in df.columns else df["volume"].values.astype(float)
        dates = df.index

        n = len(C)
        end_idx = n - 1
        if end_date is not None:
            end_ts = pd.Timestamp(end_date)
            mask_end = dates <= end_ts
            if not mask_end.any():
                return code, None
            end_idx = np.max(np.where(mask_end))

        start_idx = 0
        if start_date is not None:
            start_ts = pd.Timestamp(start_date)
            mask_start = dates >= start_ts
            if not mask_start.any():
                return code, None
            start_idx = np.min(np.where(mask_start))

        # 流通市值过滤
        capital_shares = _jc_capital.get(code, 0) if _jc_capital else 0
        if capital_shares > 0:
            latest_cap = capital_shares * C[end_idx] / 10000
            if latest_cap < DNZH_MIN_MARKET_CAP:
                return code, None
        else:
            return code, None

        # 全bar B1 + 过滤计算
        result = _compute_all_bar_b1_and_filters(C, H, L, O, V, dates, params)
        if result is None:
            return code, None

        b1 = result["b1"]
        vol_expand_ok = result["vol_expand_ok"]

        # 在日期区间内的B1信号日检测金叉+B1模式
        b1_in_range = np.where(b1[start_idx:end_idx + 1])[0] + start_idx
        best_pattern = None
        best_ref_idx = None
        qualifying_dates = []

        for bi in b1_in_range:
            if not vol_expand_ok[bi]:
                continue
            pattern = _find_gc_b1_pattern(
                b1, C, dates, result["white"], result["yellow"],
                ref_idx=bi, lookback=JCB1_LOOKBACK, gc_max_bars=JCB1_GC_MAX_BARS)
            if pattern is not None:
                qualifying_dates.append(str(dates[bi])[:10])
                if best_pattern is None or result["shrink_score"][bi] < result["shrink_score"][best_ref_idx]:
                    best_pattern = pattern
                    best_ref_idx = bi

        if best_pattern is None:
            return code, None

        # 8项假案例排除过滤
        if _exclude_rapid_rise_flat_dist(C, V, best_pattern):
            return code, None
        if _exclude_irregular_rise(C, V, best_pattern):
            return code, None
        if _exclude_b1_death_cross(C, result["white"], result["yellow"], best_pattern):
            return code, None
        if _exclude_gap_up_sideways_dump(C, H, O, V, best_pattern):
            return code, None
        if _exclude_s1_top_volume(C, H, O, V, best_pattern):
            return code, None
        if _exclude_discontinuous_rise(C, V, best_pattern):
            return code, None
        if _exclude_stepped_volume_dist(C, V, O, best_pattern):
            return code, None
        if _exclude_pre_b1_limit_down(C, O, best_pattern, params["stock_type"]):
            return code, None

        ref_idx = best_ref_idx
        gc_idx = best_pattern[0].get("gc_idx")

        # T+5统计
        t5_stats = _compute_t3_stats(C, best_pattern,
                                      t3_days=JCB1_T5_DAYS,
                                      target_pct=JCB1_T5_TARGET_PCT)
        hit_count = sum(1 for t in t5_stats if t["hit"])
        hit_rate = hit_count / len(t5_stats) * 100 if t5_stats else 0

        return code, {
            "code": code,
            "close": float(C[end_idx]),
            "market_cap": round(latest_cap, 1),
            "shrink_score": float(result["shrink_score"][ref_idx]),
            "J": float(result["J"][ref_idx]),
            "RSI": float(result["rsi"][ref_idx]),
            "gc_b1_count": len(best_pattern),
            "gc_b1_list": best_pattern,
            "gc_idx": gc_idx,
            "all_b1_count": len(b1_in_range),
            "t5_stats": t5_stats,
            "t5_hit_rate": round(hit_rate, 1),
            "scan_date": str(dates[end_idx])[:10],
            "qualifying_dates": qualifying_dates,
            "chart_data": {
                "close": C,
                "high": H,
                "low": L,
                "open": O,
                "volume": V,
                "dates": dates,
                "white": result["white"],
                "yellow": result["yellow"],
                "bbi": result["bbi"],
                "b1": b1,
            },
        }
    except Exception as e:
        return code, {"error": str(e)}


# ================================================================== #
#  全市场选股扫描                                                       #
# ================================================================== #

def scan_all_jc_b1(stock_type=STOCK_TYPE, tdxdir=TDX_DIR, market=TDX_MARKET,
                   max_workers=SCAN_MAX_WORKERS, start_date=None, end_date=None):
    """金叉B1全市场扫描"""
    from concurrent.futures import ProcessPoolExecutor, as_completed

    date_range = f"{start_date or '最早'} ~ {end_date or '最新'}"
    print(f"  筛选日期区间: {date_range}")

    print("  加载流通市值数据...")
    capital_data = _load_capital_data(tdxdir)
    if capital_data is None:
        print("  警告: 无法加载流通市值数据，将跳过市值过滤")
    else:
        print(f"  已加载 {len(capital_data)} 只股票的流通市值")

    codes = _get_all_codes(tdxdir)
    total = len(codes)
    print(f"  扫描 {total} 只A股... (workers={max_workers or 'auto'})")

    params = {
        "m1": HUANGBAI_M1, "m2": HUANGBAI_M2, "m3": HUANGBAI_M3, "m4": HUANGBAI_M4,
        "n": HUANGBAI_N, "m": HUANGBAI_M, "n1": HUANGBAI_N1, "n2": HUANGBAI_N2,
        "stock_type": stock_type,
    }

    results = []
    errors = 0
    done = 0
    t0 = time.time()

    with ProcessPoolExecutor(
        max_workers=max_workers,
        initializer=_jc_init_process,
        initargs=(tdxdir, market, capital_data),
    ) as pool:
        futures = {
            pool.submit(_scan_one_stock, code, params, start_date, end_date): code
            for code in codes
        }
        for future in as_completed(futures):
            code, sig = future.result()
            done += 1
            if sig is None:
                pass
            elif "error" in sig:
                errors += 1
            else:
                results.append(sig)
                gc_date = ""
                if sig.get("gc_idx") is not None:
                    cd = sig["chart_data"]["dates"]
                    gc_date = str(cd[sig["gc_idx"]])[:10]
                print(f"  {code}  市值={sig['market_cap']:.0f}亿  "
                      f"缩量={sig['shrink_score']:.3f}  "
                      f"金叉={gc_date} B1={sig['qualifying_dates'][-1] if sig['qualifying_dates'] else '?'}  "
                      f"T5胜率={sig['t5_hit_rate']:.0f}%")
            if done % 500 == 0:
                print(f"  ... 已扫描 {done}/{total} ({done/total*100:.0f}%)  "
                      f"命中 {len(results)}  耗时 {time.time()-t0:.1f}s")

    elapsed = time.time() - t0

    results.sort(key=lambda x: x["shrink_score"])

    print(f"\n{'=' * 65}")
    print(f"  金叉B1扫描完成: {total} 只  命中 {len(results)} 只  "
          f"错误 {errors}  耗时 {elapsed:.1f}s")
    print(f"{'=' * 65}")

    if results:
        _print_results(results)
        _generate_charts(results)

    return results


def _print_results(results):
    """打印选股结果"""
    print(f"\n  金叉B1选股结果（按缩量排序）")
    print(f"{'=' * 65}")
    for r in results:
        cd = r["chart_data"]["dates"]
        gc_date = ""
        if r.get("gc_idx") is not None:
            gc_date = str(cd[r["gc_idx"]])[:10]
        print(f"  {r['code']}  C={r['close']:.2f}  市值={r['market_cap']:.0f}亿  "
              f"缩量={r['shrink_score']:.3f}  J={r['J']:.1f}  RSI={r['RSI']:.1f}")
        print(f"    金叉日={gc_date}  B1触发({r['gc_b1_count']}次)")
        qd = r.get("qualifying_dates", [])
        if len(qd) > 1:
            print(f"    触发日期({len(qd)}次): {', '.join(qd[:5])}"
                  f"{'...' if len(qd) > 5 else ''}")
        elif qd:
            print(f"    触发日期: {qd[0]}")
        print(f"    T+5胜率: {r['t5_hit_rate']:.0f}% ({r['all_b1_count']}个B1信号)")
        for t in r["t5_stats"]:
            mark = "V" if t["hit"] else "X"
            print(f"      [{mark}] {t['date']} 买入={t['buy_price']:.2f} "
                  f"T+5={t['t3_price']:.2f}  涨幅={t['t3_pct']:+.2f}%")

    # 汇总T+5统计
    all_t5 = []
    for r in results:
        all_t5.extend(r["t5_stats"])
    if all_t5:
        total = len(all_t5)
        hits = sum(1 for t in all_t5 if t["hit"])
        avg_pct = sum(t["t3_pct"] for t in all_t5) / total
        print(f"\n{'=' * 65}")
        print(f"  T+5汇总: {total}个B1信号  "
              f"涨幅>={JCB1_T5_TARGET_PCT}%: {hits}/{total} ({hits/total*100:.1f}%)  "
              f"平均涨幅={avg_pct:+.2f}%")
        print(f"{'=' * 65}")


def _generate_charts(results):
    """为选中的股票生成K线图"""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    # 清空charts目录
    if os.path.exists(CHART_OUTPUT_DIR):
        shutil.rmtree(CHART_OUTPUT_DIR)
    os.makedirs(CHART_OUTPUT_DIR, exist_ok=True)

    print(f"\n  生成K线图到 {CHART_OUTPUT_DIR}/ ...")

    for r in results:
        try:
            _plot_jc_b1_chart(r, CHART_OUTPUT_DIR)
        except Exception as e:
            print(f"  {r['code']} 图表生成失败: {e}")

    print(f"  已生成 {len(results)} 张K线图")


def _plot_jc_b1_chart(result, output_dir):
    """为单只股票绘制金叉B1 K线图"""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    from matplotlib.collections import PatchCollection

    plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'DejaVu Sans']
    plt.rcParams['axes.unicode_minus'] = False
    COLOR_YANG = "#ff4444"
    COLOR_YIN = "#00aa00"

    cd = result["chart_data"]
    C = cd["close"]
    H = cd["high"]
    L = cd["low"]
    O = cd["open"]
    V = cd["volume"]
    dates = cd["dates"]
    white = cd["white"]
    yellow = cd["yellow"]
    b1 = cd["b1"]

    # 以金叉B1信号区域为中心截取
    n = len(C)
    gc_idx = result.get("gc_idx")
    if gc_idx is not None:
        center_idx = gc_idx
    else:
        b1_list = result["gc_b1_list"]
        center_idx = b1_list[-1]["idx"] if b1_list else n - 1
    padding = 30
    start = max(0, center_idx - 60 - padding)
    end = min(n, center_idx + padding + 1)
    s = slice(start, end)
    C_s, H_s, L_s, O_s, V_s = C[s], H[s], L[s], O[s], V[s]
    white_s, yellow_s = white[s], yellow[s]
    b1_s = b1[s]
    dates_s = dates[s]
    n_s = len(C_s)

    # 双行布局：K线 + 成交量
    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(16, 9),
        gridspec_kw={'height_ratios': [3, 1]},
        sharex=True)
    fig.subplots_adjust(hspace=0.05)

    gc_date_str = ""
    if gc_idx is not None:
        gc_date_str = str(dates[gc_idx])[:10]
    fig.suptitle(f"金叉B1选股 {result['code']}  C={result['close']:.2f}  "
                 f"市值={result['market_cap']:.0f}亿  T+5胜率={result['t5_hit_rate']:.0f}%\n"
                 f"金叉日: {gc_date_str}  选股日期: {', '.join(result.get('qualifying_dates', []))}",
                 fontsize=13, fontweight='bold')

    x = np.arange(n_s)

    # ---- K线图 ----
    ax1.vlines(x, L_s, H_s, colors="#888888", linewidths=0.5)
    rects, rect_colors = [], []
    for i in range(n_s):
        o_v, c_v = float(O_s[i]), float(C_s[i])
        if np.isnan(o_v) or np.isnan(c_v):
            continue
        body_bottom = min(o_v, c_v)
        body_height = abs(c_v - o_v)
        if body_height < 0.001:
            body_height = c_v * 0.002
        rects.append(mpatches.Rectangle((x[i] - 0.35, body_bottom), 0.7, body_height))
        rect_colors.append(COLOR_YANG if c_v >= o_v else COLOR_YIN)
    if rects:
        ax1.add_collection(PatchCollection(rects, facecolors=rect_colors,
                                            edgecolors=rect_colors, linewidths=0.5))
    valid_mask = ~np.isnan(H_s) & ~np.isnan(L_s)
    if valid_mask.any():
        y_min, y_max = np.nanmin(L_s[valid_mask]), np.nanmax(H_s[valid_mask])
        margin = (y_max - y_min) * 0.08
        ax1.set_ylim(y_min - margin, y_max + margin)
    ax1.set_xlim(-1, n_s)

    # 均线
    valid_w = ~np.isnan(white_s)
    if valid_w.any():
        ax1.plot(x[valid_w], white_s[valid_w], color='#666666', linewidth=1.2, alpha=0.9, label='白线')
    valid_y = ~np.isnan(yellow_s)
    if valid_y.any():
        ax1.plot(x[valid_y], yellow_s[valid_y], color='#FFD700', linewidth=1.2, alpha=0.9, label='黄线')

    # ---- 金叉点标注 ----
    if gc_idx is not None:
        gc_chart_x = gc_idx - start
        if 0 <= gc_chart_x < n_s:
            gc_price = float(min(O_s[gc_chart_x], C_s[gc_chart_x]))
            ax1.plot(gc_chart_x, gc_price, marker='D', color='#0088FF',
                     markersize=10, markeredgecolor='white', markeredgewidth=1.5, zorder=6)
            ax1.annotate(f"金叉 {gc_date_str}",
                         xy=(gc_chart_x, gc_price),
                         xytext=(8, -18), textcoords='offset points',
                         fontsize=8, color='#0066CC', fontweight='bold',
                         bbox=dict(boxstyle='round,pad=0.2', fc='white',
                                   ec='#0088FF', alpha=0.9))
            # 金叉竖线
            ax1.axvline(gc_chart_x, color='#0088FF', linewidth=1, linestyle=':',
                        alpha=0.6, zorder=4)

    # ---- B1信号标记 ----
    b1_indices = np.where(b1_s)[0]
    if len(b1_indices) > 0:
        ylim = ax1.get_ylim()
        offset = (ylim[1] - ylim[0]) * 0.03
        b1_prices = [float(L_s[i]) - offset for i in b1_indices]
        ax1.scatter(b1_indices, b1_prices, marker='*', s=100,
                    c='#ff00ff', zorder=5, label='B1信号',
                    edgecolors='white', linewidths=0.5)

    # ---- 金叉→B1 连线 ----
    b1_list = result["gc_b1_list"]
    if gc_idx is not None and len(b1_list) > 0:
        gc_chart_x = gc_idx - start
        for nb in b1_list:
            bi_chart_x = nb["idx"] - start
            if 0 <= gc_chart_x < n_s and 0 <= bi_chart_x < n_s:
                gc_p = float(C_s[gc_chart_x])
                b1_p = float(C_s[bi_chart_x])
                ax1.annotate('', xy=(bi_chart_x, b1_p), xytext=(gc_chart_x, gc_p),
                             arrowprops=dict(arrowstyle='->', color='#0088FF',
                                             lw=1.5, linestyle='--'))
                ax1.annotate(f"B1 {nb['price']:.2f}",
                             xy=(bi_chart_x, float(L_s[bi_chart_x])),
                             xytext=(-8, -16), textcoords='offset points',
                             fontsize=7, color='#660066', fontweight='bold',
                             bbox=dict(boxstyle='round,pad=0.2', fc='white',
                                       ec='#ff00ff', alpha=0.85))

    # ---- 选股日期竖线标记 ----
    qd = result.get("qualifying_dates", [])
    if qd:
        ylim = ax1.get_ylim()
        y_top = ylim[1]
        for qd_str in qd:
            qd_x = None
            for di in range(len(dates_s)):
                if str(dates_s[di])[:10] == qd_str:
                    qd_x = di
                    break
            if qd_x is not None:
                ax1.axvline(qd_x, color='#FF6600', linewidth=1.2, linestyle='--',
                            alpha=0.8, zorder=4)
                ax1.annotate(qd_str, xy=(qd_x, y_top),
                             xytext=(0, -12), textcoords='offset points',
                             fontsize=7, color='#FF6600', fontweight='bold',
                             ha='center', rotation=45,
                             bbox=dict(boxstyle='round,pad=0.15', fc='white',
                                       ec='#FF6600', alpha=0.9))

    # ---- 图例 ----
    legend_items = []
    legend_items.append(plt.Line2D([0], [0], color='#666666', lw=1.2, label='白线'))
    legend_items.append(plt.Line2D([0], [0], color='#FFD700', lw=1.2, label='黄线'))
    if len(b1_indices) > 0:
        legend_items.append(plt.Line2D([0], [0], marker='*', color='#ff00ff',
                                        linestyle='None', markersize=8, label='B1信号'))
    if gc_idx is not None:
        legend_items.append(plt.Line2D([0], [0], marker='D', color='#0088FF',
                                        linestyle='None', markersize=8, label='金叉点'))
    if qd:
        legend_items.append(plt.Line2D([0], [0], color='#FF6600', lw=1.2,
                                        linestyle='--', label='选股日期'))
    ax1.legend(handles=legend_items, loc='upper left', fontsize=8, framealpha=0.9)
    ax1.set_ylabel('价格', fontsize=10)
    ax1.grid(True, alpha=0.3)
    ax1.set_facecolor('white')

    # ---- 成交量 ----
    v_colors = [COLOR_YANG if C_s[i] >= O_s[i] else COLOR_YIN for i in range(n_s)]
    ax2.bar(x, V_s, color=v_colors, width=0.7, alpha=0.7)
    ax2.set_ylabel('成交量', fontsize=10)
    ax2.grid(True, alpha=0.3)
    ax2.set_facecolor('white')

    # ---- X轴日期 ----
    step = max(1, n_s // 12)
    ax2.set_xticks(x[::step])
    ax2.set_xticklabels(
        [str(dates_s[i])[:10] for i in range(0, n_s, step)],
        rotation=45, fontsize=7)

    plt.tight_layout()
    filepath = os.path.join(output_dir, f"{result['code']}.png")
    fig.savefig(filepath, dpi=150, bbox_inches='tight', facecolor='white')
    plt.close(fig)
    print(f"    {result['code']}.png 已保存")
