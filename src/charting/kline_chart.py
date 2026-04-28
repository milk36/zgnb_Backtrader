"""组合模拟K线图生成器

为每只交易过的股票生成K线蜡烛图，叠加白线/黄线/BBI指标线和买卖标记。
用纯 matplotlib 实现，不依赖 mplfinance。
"""

import os
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.collections import PatchCollection
import numpy as np

plt.rcParams["font.sans-serif"] = ["SimHei", "Microsoft YaHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False


# ---- 常量 ----
COLOR_YANG = "#ff4444"    # 阳线红
COLOR_YIN = "#00aa00"     # 阴线绿
COLOR_WHITE = "#e0e0e0"   # 白线
COLOR_YELLOW = "#FFD700"  # 黄线
COLOR_BBI = "#9966cc"     # BBI紫
COLOR_BUY = "#00ff00"     # 买入标记
COLOR_SELL = "#ff0000"    # 卖出标记
COLOR_STOP_LOSS = "#ff6600"  # 止损线橙
COLOR_COST = "#00ccff"    # 成本线蓝
DPI = 150
PADDING = 30              # 买卖前后额外显示的bar数
FIG_WIDTH = 16
FIG_HEIGHT = 9


def generate_charts(trade_list, all_signals, output_dir="charts"):
    """为所有交易过的股票生成K线图

    Args:
        trade_list: list[dict] - 来自 report()["trade_list"]
            dict 包含: code, buy_date, sell_date, buy_price, sell_price,
                       size, pnl_pct, pnl_amount, reason
        all_signals: dict[str, dict] - 预加载信号数据 (key=股票代码)
        output_dir: str - 输出目录
    """
    if not trade_list:
        print("  无交易记录，跳过图表生成")
        return

    os.makedirs(output_dir, exist_ok=True)

    # 按 code 分组交易
    trades_by_code = defaultdict(list)
    for t in trade_list:
        trades_by_code[t["code"]].append(t)

    generated = 0
    skipped = 0
    for code, trades in trades_by_code.items():
        sig = all_signals.get(code)
        if sig is None:
            skipped += 1
            continue
        try:
            _plot_single_stock(code, sig, trades, output_dir)
            generated += 1
        except Exception as e:
            print(f"  警告: 绘制 {code} 失败: {e}")
            skipped += 1

    print(f"\n  图表生成完成: {generated} 只  跳过 {skipped} 只  目录: {output_dir}/")


def _plot_single_stock(code, sig, trades, output_dir):
    """为单只股票绘制完整K线图"""
    dates = sig["dates"]
    C = sig["close"]
    H = sig["high"]
    L = sig["low"]
    O = sig.get("open", C)
    V = sig.get("volume", np.zeros(len(C)))
    white = sig.get("white")
    yellow = sig.get("yellow")
    bbi = sig.get("bbi")

    # 确定绘图范围
    all_dates = []
    for t in trades:
        all_dates.append(t["buy_date"])
        if t.get("sell_date"):
            all_dates.append(t["sell_date"])

    first_date = min(all_dates)
    last_date = max(all_dates)
    i_start, i_end = _find_range(dates, first_date, last_date, PADDING)

    # 截取数据
    s = slice(i_start, i_end + 1)
    dates_s = dates[s]
    C_s, H_s, L_s, O_s, V_s = C[s], H[s], L[s], O[s], V[s]
    white_s = white[s] if white is not None else None
    yellow_s = yellow[s] if yellow is not None else None
    bbi_s = bbi[s] if bbi is not None else None

    # 创建图表
    fig, (ax_price, ax_vol) = plt.subplots(
        2, 1, figsize=(FIG_WIDTH, FIG_HEIGHT),
        gridspec_kw={"height_ratios": [3, 1]},
        sharex=True,
    )
    fig.subplots_adjust(hspace=0.05)

    # 绘制K线
    n = len(C_s)
    x = np.arange(n)
    _draw_candlestick(ax_price, O_s, C_s, H_s, L_s, n)

    # 绘制指标线
    _draw_indicators(ax_price, x, white_s, yellow_s, bbi_s)

    # 绘制买卖标记
    _draw_trade_markers(ax_price, x, dates_s, trades)

    # 绘制成交量
    _draw_volume(ax_vol, x, V_s, O_s, C_s, n)

    # 格式化X轴日期
    step = max(1, n // 15)
    ax_vol.set_xticks(x[::step])
    ax_vol.set_xticklabels(
        [pd_timestamp_to_str(d) for d in dates_s[::step]],
        rotation=45, fontsize=8,
    )

    # Y轴标签
    ax_price.set_ylabel("价格", fontsize=10)
    ax_vol.set_ylabel("成交量", fontsize=10)

    # 标题
    total_pnl = sum(t.get("pnl_pct", 0) for t in trades)
    first_str = _fmt_date(first_date)
    last_str = _fmt_date(last_date)
    ax_price.set_title(
        f"{code} | {first_str} ~ {last_str} | 共{len(trades)}笔 | "
        f"总PnL: {total_pnl:+.2f}%",
        fontsize=13, fontweight="bold",
    )

    # 图例
    legend_items = []
    if white_s is not None:
        legend_items.append(plt.Line2D([0], [0], color=COLOR_WHITE, lw=1.2, label="白线"))
    if yellow_s is not None:
        legend_items.append(plt.Line2D([0], [0], color=COLOR_YELLOW, lw=1.2, label="黄线"))
    if bbi_s is not None:
        legend_items.append(plt.Line2D([0], [0], color=COLOR_BBI, lw=0.8, label="BBI"))
    legend_items.append(plt.Line2D([0], [0], marker="^", color=COLOR_BUY,
                                    linestyle="None", markersize=8, label="买入"))
    legend_items.append(plt.Line2D([0], [0], marker="v", color=COLOR_SELL,
                                    linestyle="None", markersize=8, label="卖出"))
    legend_items.append(plt.Line2D([0], [0], color=COLOR_STOP_LOSS,
                                    linestyle="--", lw=1.0, label="止损线"))
    legend_items.append(plt.Line2D([0], [0], color=COLOR_COST,
                                    linestyle=":", lw=0.8, label="成本线"))
    ax_price.legend(handles=legend_items, loc="upper left", fontsize=8,
                    framealpha=0.8, facecolor="#1a1a2e")

    ax_price.set_facecolor("#1a1a2e")
    ax_vol.set_facecolor("#1a1a2e")
    ax_price.grid(True, alpha=0.2)
    ax_vol.grid(True, alpha=0.2)

    # 保存
    filename = f"{code}_{first_str}_{last_str}.png".replace("-", "")
    filepath = os.path.join(output_dir, filename)
    fig.savefig(filepath, dpi=DPI, bbox_inches="tight", facecolor="#0f0f1a")
    plt.close(fig)


def _draw_candlestick(ax, opens, closes, highs, lows, n):
    """绘制K线蜡烛图"""
    x = np.arange(n)

    # 影线
    ax.vlines(x, lows, highs, colors="k", linewidths=0.5)

    # 实体
    rects = []
    colors = []
    for i in range(n):
        o, c = float(opens[i]), float(closes[i])
        if np.isnan(o) or np.isnan(c):
            continue
        body_bottom = min(o, c)
        body_height = abs(c - o)
        if body_height < 0.001:
            body_height = closes[i] * 0.002
        rect = mpatches.Rectangle((x[i] - 0.35, body_bottom), 0.7, body_height)
        rects.append(rect)
        colors.append(COLOR_YANG if c >= o else COLOR_YIN)

    if rects:
        collection = PatchCollection(rects, facecolors=colors,
                                      edgecolors=colors, linewidths=0.5)
        ax.add_collection(collection)

    ax.set_xlim(-1, n)
    # 留出上下空间
    valid_mask = ~np.isnan(highs) & ~np.isnan(lows)
    if valid_mask.any():
        y_min = np.nanmin(lows[valid_mask])
        y_max = np.nanmax(highs[valid_mask])
        margin = (y_max - y_min) * 0.08
        ax.set_ylim(y_min - margin, y_max + margin)


def _draw_volume(ax, x, volumes, opens, closes, n):
    """绘制成交量柱"""
    colors = []
    for i in range(n):
        c, o = float(closes[i]), float(opens[i])
        if np.isnan(c) or np.isnan(o):
            colors.append(COLOR_YIN)
        else:
            colors.append(COLOR_YANG if c >= o else COLOR_YIN)

    ax.bar(x, volumes, width=0.7, color=colors, alpha=0.7)
    ax.set_xlim(-1, n)


def _draw_indicators(ax, x, white, yellow, bbi):
    """叠加指标线"""
    if white is not None:
        valid = ~np.isnan(white)
        ax.plot(x[valid], white[valid], color=COLOR_WHITE, linewidth=1.2, alpha=0.9)
    if yellow is not None:
        valid = ~np.isnan(yellow)
        ax.plot(x[valid], yellow[valid], color=COLOR_YELLOW, linewidth=1.2, alpha=0.9)
    if bbi is not None:
        valid = ~np.isnan(bbi)
        ax.plot(x[valid], bbi[valid], color=COLOR_BBI, linewidth=0.8, alpha=0.8)


def _draw_trade_markers(ax, x, dates_s, trades):
    """标注买卖点、止损线和成本线"""
    dates_list = list(dates_s)
    n = len(dates_list)

    for t in trades:
        # 买入标记
        buy_idx = _find_date_index(dates_list, t["buy_date"])
        sell_idx = _find_date_index(dates_list, t["sell_date"]) if t.get("sell_date") else n - 1
        end_idx = sell_idx if sell_idx is not None else n - 1

        if buy_idx is not None:
            ax.plot(buy_idx, t["buy_price"], marker="^", color=COLOR_BUY,
                    markersize=14, markeredgecolor="white", markeredgewidth=1,
                    zorder=5)
            ax.annotate(
                f"买 {t['buy_price']:.2f}",
                xy=(buy_idx, t["buy_price"]),
                xytext=(0, -18), textcoords="offset points",
                fontsize=7, color=COLOR_BUY, ha="center",
            )

            # 止损线（橙色虚线，从买入到卖出）
            sl = t.get("stop_loss")
            if sl:
                ax.hlines(sl, buy_idx, end_idx, colors=COLOR_STOP_LOSS,
                          linestyles="dashed", linewidths=1.0, alpha=0.8, zorder=3)
                ax.annotate(f"止损 {sl:.2f}", xy=(buy_idx, sl),
                            xytext=(-5, -12), textcoords="offset points",
                            fontsize=6, color=COLOR_STOP_LOSS, ha="right")

            # 成本线（蓝色点线）
            ax.hlines(t["buy_price"], buy_idx, end_idx, colors=COLOR_COST,
                      linestyles="dotted", linewidths=0.8, alpha=0.6, zorder=3)

        # 卖出标记
        sell_date = t.get("sell_date")
        if sell_date:
            sell_idx_s = _find_date_index(dates_list, sell_date)
            if sell_idx_s is not None:
                sell_price = t["sell_price"]
                reason = t.get("reason", "")
                ax.plot(sell_idx_s, sell_price, marker="v", color=COLOR_SELL,
                        markersize=14, markeredgecolor="white", markeredgewidth=1,
                        zorder=5)
                label = f"卖 {sell_price:.2f}"
                if reason:
                    label += f" ({reason})"
                ax.annotate(
                    label,
                    xy=(sell_idx_s, sell_price),
                    xytext=(0, 12), textcoords="offset points",
                    fontsize=7, color=COLOR_SELL, ha="center",
                )


def _find_range(dates, first_date, last_date, padding):
    """确定绘图数据范围索引"""
    dates_list = list(dates)
    n = len(dates_list)

    i_start = 0
    i_end = n - 1

    for i, d in enumerate(dates_list):
        if d >= first_date:
            i_start = max(0, i - padding)
            break

    for i in range(n - 1, -1, -1):
        if dates_list[i] <= last_date:
            i_end = min(n - 1, i + padding)
            break

    return i_start, i_end


def _find_date_index(dates_list, target_date):
    """在日期列表中找到目标日期的索引"""
    target = _to_timestamp(target_date)
    for i, d in enumerate(dates_list):
        if _to_timestamp(d) == target:
            return i
    # 精确匹配失败，找最近的
    best_i, best_dist = None, float("inf")
    for i, d in enumerate(dates_list):
        dist = abs((_to_timestamp(d) - target).days)
        if dist < best_dist:
            best_dist = dist
            best_i = i
    return best_i if best_dist <= 3 else None


def _to_timestamp(d):
    """统一转为 pd.Timestamp"""
    import pandas as pd
    if isinstance(d, pd.Timestamp):
        return d
    return pd.Timestamp(d)


def _fmt_date(d):
    """格式化日期为 YYYY-MM-DD"""
    return str(d)[:10]


def pd_timestamp_to_str(d):
    """日期转短字符串用于X轴标签"""
    s = str(d)
    if len(s) >= 10:
        return s[5:10]  # MM-DD
    return s
