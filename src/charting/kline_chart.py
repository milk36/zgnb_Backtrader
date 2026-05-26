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
COLOR_WHITE = "#666666"   # 白线（深灰，适配白色背景）
COLOR_YELLOW = "#FFD700"  # 黄线
COLOR_BBI = "#9966cc"     # BBI紫
COLOR_BUY = "#00ff00"     # 买入标记
COLOR_SELL = "#ff0000"    # 卖出标记
COLOR_SELL_PARTIAL = "#ff8800"  # 部分卖出标记
COLOR_STOP_LOSS = "#ff6600"  # 止损线橙
COLOR_COST = "#00ccff"    # 成本线蓝
COLOR_NXING_HIGH = "#ff0000"    # N型高点标记红
COLOR_NXING_LOW = "#00aa00"     # N型低点标记绿
COLOR_B1 = "#ff00ff"       # B1信号标记（洋红）
COLOR_NXING_RISE = "#00cc00"    # N型拉升阶段背景
COLOR_NXING_PULLBACK = "#ff8800"  # N型回调阶段背景
COLOR_WAVE_HIGH = "#cc00cc"   # 盈亏比高点标记（紫红）
DPI = 150
PADDING = 30              # 买卖前后额外显示的bar数
FIG_WIDTH = 16
FIG_HEIGHT = 9


def generate_charts(trade_list, all_signals, output_dir="charts", sub_chart="volume"):
    """为所有交易过的股票生成K线图

    Args:
        trade_list: list[dict] - 来自 report()["trade_list"]
            dict 包含: code, buy_date, sell_date, buy_price, sell_price,
                       size, pnl_pct, pnl_amount, reason
        all_signals: dict[str, dict] - 预加载信号数据 (key=股票代码)
        output_dir: str - 输出目录
        sub_chart: str - 副图类型 "volume"(成交量) 或 "brick"(砖型图)
    """
    if not trade_list:
        print("  无交易记录，跳过图表生成")
        return

    os.makedirs(output_dir, exist_ok=True)

    # 清空旧图表文件
    for f in os.listdir(output_dir):
        if f.lower().endswith(".png"):
            os.remove(os.path.join(output_dir, f))

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
            _plot_single_stock(code, sig, trades, output_dir, sub_chart)
            generated += 1
        except Exception as e:
            print(f"  警告: 绘制 {code} 失败: {e}")
            skipped += 1

    print(f"\n  图表生成完成: {generated} 只  跳过 {skipped} 只  目录: {output_dir}/")


def _plot_single_stock(code, sig, trades, output_dir, sub_chart="volume"):
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
    has_brick = sub_chart == "brick" and sig.get("brick_value") is not None
    if has_brick:
        fig, (ax_price, ax_vol, ax_brick) = plt.subplots(
            3, 1, figsize=(FIG_WIDTH, FIG_HEIGHT),
            gridspec_kw={"height_ratios": [3, 1, 1]},
            sharex=True,
        )
    else:
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

    # 绘制N型阶段标注
    _draw_nxing_phases(ax_price, sig, i_start, i_end, code=code)

    # 绘制B1信号标记（B2策略会覆写b1为入场信号，原始B1保存在b1_original）
    b1_full = sig.get("b1_original") if sig.get("b1_original") is not None else sig.get("b1")
    b1_s = b1_full[s] if b1_full is not None else None
    _draw_b1_markers(ax_price, x, b1_s, L_s)

    # 计算盈亏比高点（前一波阳线高点，阴线跳过）
    wave_high_s = None
    if yellow is not None and yellow_s is not None:
        _wh = np.empty(n)
        _pk = H_s[0] if C_s[0] >= O_s[0] else 0.0
        _pin = C_s[0] >= yellow_s[0] if not np.isnan(yellow_s[0]) else False
        for _i in range(n):
            _ni = C_s[_i] >= yellow_s[_i] if not np.isnan(yellow_s[_i]) else False
            _is_yang = C_s[_i] >= O_s[_i]
            if _ni and not _pin:
                _pk = H_s[_i] if _is_yang else 0.0
            elif _ni and _is_yang:
                _pk = max(_pk, H_s[_i])
            _wh[_i] = _pk
            _pin = _ni
        wave_high_s = _wh

    # 绘制买卖标记
    _draw_trade_markers(ax_price, x, dates_s, trades, wave_high_s=wave_high_s)

    # 绘制副图
    if has_brick:
        brick_s = sig["brick_value"][s]
        _draw_volume(ax_vol, x, V_s, O_s, C_s, n)
        ax_vol.set_ylabel("成交量", fontsize=10)
        _draw_brick(ax_brick, x, brick_s, n)
        ax_brick.set_ylabel("砖型图", fontsize=10)
        last_ax = ax_brick
    else:
        _draw_volume(ax_vol, x, V_s, O_s, C_s, n)
        ax_vol.set_ylabel("成交量", fontsize=10)
        last_ax = ax_vol

    # 格式化X轴日期
    step = max(1, n // 15)
    last_ax.set_xticks(x[::step])
    last_ax.set_xticklabels(
        [pd_timestamp_to_str(d) for d in dates_s[::step]],
        rotation=45, fontsize=8,
    )

    # Y轴标签
    ax_price.set_ylabel("价格", fontsize=10)

    # 标题
    first_str = _fmt_date(first_date)
    last_str = _fmt_date(last_date)
    closed_trades = [t for t in trades if not t.get("partial")]
    partial_count = len(trades) - len(closed_trades)
    # 完美B1模式类型标注（取第一笔交易的pattern_type）
    pt = trades[0].get("pattern_type", 0) if trades else 0
    pt_prefix = ""
    if pt > 0:
        if pt <= 5:
            _PATTERN_NAMES = {1: "典型单波", 2: "白线不死叉", 3: "多波N型",
                              4: "跌破反转", 5: "大牛市"}
        else:
            _PATTERN_NAMES = {1: "华纳药厂", 2: "宁波韵升", 3: "微芯生物",
                              4: "方正科技", 5: "澄天伟业", 6: "国轩高科",
                              7: "野马电池", 8: "光电股份", 9: "新瀚新材",
                              10: "昂利康", 11: "赢时胜(预警)"}
        pt_prefix = f"[{_PATTERN_NAMES.get(pt, '')}] "
    title = f"{pt_prefix}{code} | {first_str} ~ {last_str}"
    if closed_trades:
        pnl_parts = " ".join(
            f"#{i+1}:{t['pnl_pct']:+.1f}%"
            for i, t in enumerate(closed_trades)
        )
        title += f" | {pnl_parts}"
    if partial_count > 0:
        title += f" | 部分卖出{partial_count}笔"
    ax_price.set_title(title, fontsize=13, fontweight="bold")

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
                                    linestyle="None", markersize=8, label="清仓卖出"))
    if b1_s is not None and np.any(b1_s):
        legend_items.append(plt.Line2D([0], [0], marker="*", color=COLOR_B1,
                                        linestyle="None", markersize=8, label="B1信号"))
    legend_items.append(plt.Line2D([0], [0], marker="v", color=COLOR_SELL_PARTIAL,
                                    linestyle="None", markersize=6, label="部分卖出"))
    legend_items.append(plt.Line2D([0], [0], color=COLOR_STOP_LOSS,
                                    linestyle="--", lw=1.0, label="止损线"))
    legend_items.append(plt.Line2D([0], [0], color=COLOR_COST,
                                    linestyle=":", lw=0.8, label="成本线"))
    if sig.get("nxing_pattern") is not None and np.any(sig["nxing_pattern"]):
        legend_items.append(plt.Line2D([0], [0], marker="D", color=COLOR_NXING_HIGH,
                                        linestyle="None", markersize=6, label="N型高点"))
        legend_items.append(plt.Line2D([0], [0], marker="D", color=COLOR_NXING_LOW,
                                        linestyle="None", markersize=5, label="N型低点"))
        legend_items.append(mpatches.Patch(facecolor=COLOR_NXING_RISE, alpha=0.2,
                                            label="N型拉升"))
        legend_items.append(mpatches.Patch(facecolor=COLOR_NXING_PULLBACK, alpha=0.2,
                                            label="N型回调"))
    ax_price.legend(handles=legend_items, loc="upper left", fontsize=8,
                    framealpha=0.9, facecolor="white")

    ax_price.set_facecolor("white")
    ax_vol.set_facecolor("white")
    ax_price.grid(True, alpha=0.3)
    ax_vol.grid(True, alpha=0.3)
    if has_brick:
        ax_brick.set_facecolor("white")
        ax_brick.grid(True, alpha=0.3)

    # 保存
    filename = f"{code}_{first_str}_{last_str}.png".replace("-", "")
    filepath = os.path.join(output_dir, filename)
    fig.savefig(filepath, dpi=DPI, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def _draw_candlestick(ax, opens, closes, highs, lows, n):
    """绘制K线蜡烛图"""
    x = np.arange(n)

    # 影线
    ax.vlines(x, lows, highs, colors="#888888", linewidths=0.5)

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


def _draw_brick(ax, x, brick, n):
    """绘制砖型图副图

    涨砖（brick > REF(brick,1)）画红色，跌砖画绿色。
    每根柱子从 min(brick[i], brick[i-1]) 到 max(brick[i], brick[i-1])。
    """
    for i in range(n):
        cur = float(brick[i])
        if i == 0:
            prev = cur
        else:
            prev = float(brick[i - 1])
        if np.isnan(cur) or np.isnan(prev):
            continue
        bar_bottom = min(cur, prev)
        bar_top = max(cur, prev)
        if bar_top - bar_bottom < 0.001:
            continue
        color = COLOR_YANG if cur >= prev else COLOR_YIN
        ax.bar(x[i], bar_top - bar_bottom, bottom=bar_bottom,
               width=0.7, color=color, alpha=0.85)

    ax.set_xlim(-1, n)
    valid = ~np.isnan(brick)
    if valid.any():
        y_min = np.nanmin(brick[valid])
        y_max = np.nanmax(brick[valid])
        margin = (y_max - y_min) * 0.08
        ax.set_ylim(max(0, y_min - margin), y_max + margin)


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


def _draw_b1_markers(ax, x, b1, lows):
    """在K线下方标注B1信号点（洋红色五角星）"""
    if b1 is None or not np.any(b1):
        return
    # 根据Y轴范围计算偏移量，确保标记在K线下方
    ylim = ax.get_ylim()
    offset = (ylim[1] - ylim[0]) * 0.03
    for i in range(len(b1)):
        if b1[i]:
            y = float(lows[i])
            if np.isnan(y):
                continue
            ax.plot(x[i], y - offset, marker="*", color=COLOR_B1,
                    markersize=10, markeredgecolor="white", markeredgewidth=0.5,
                    zorder=4)


def _draw_trade_markers(ax, x, dates_s, trades, wave_high_s=None):
    """标注买卖点、止损线、成本线和盈亏比高点"""
    dates_list = list(dates_s)
    n = len(dates_list)

    # 找到最后一个卖出日期，用于止损线/成本线的终点
    last_sell_idx = 0
    for t in trades:
        if t.get("sell_date"):
            idx = _find_date_index(dates_list, t["sell_date"])
            if idx is not None and idx > last_sell_idx:
                last_sell_idx = idx

    # 按 buy_date 分组，每组只画一次买入标记
    drawn_buy = set()
    for t in trades:
        buy_idx = _find_date_index(dates_list, t["buy_date"])
        sell_idx = _find_date_index(dates_list, t["sell_date"]) if t.get("sell_date") else None
        end_idx = sell_idx if sell_idx is not None else n - 1
        is_partial = t.get("partial", False)
        buy_key = (str(t["buy_date"]), t["buy_price"])

        if buy_idx is not None and buy_key not in drawn_buy:
            drawn_buy.add(buy_key)
            ax.plot(buy_idx, t["buy_price"], marker="^", color=COLOR_BUY,
                    markersize=14, markeredgecolor="white", markeredgewidth=1,
                    zorder=5)
            ax.annotate(
                f"买 {t['buy_price']:.2f}",
                xy=(buy_idx, t["buy_price"]),
                xytext=(0, -18), textcoords="offset points",
                fontsize=7, color=COLOR_BUY, ha="center",
            )

            # 止损线（橙色虚线，从买入到最后一个卖出点）
            sl = t.get("stop_loss")
            if sl:
                ax.hlines(sl, buy_idx, last_sell_idx, colors=COLOR_STOP_LOSS,
                          linestyles="dashed", linewidths=1.0, alpha=0.8, zorder=3)
                ax.annotate(f"止损 {sl:.2f}", xy=(buy_idx, sl),
                            xytext=(-5, -12), textcoords="offset points",
                            fontsize=6, color=COLOR_STOP_LOSS, ha="right")

            # 成本线（蓝色点线）
            ax.hlines(t["buy_price"], buy_idx, last_sell_idx, colors=COLOR_COST,
                      linestyles="dotted", linewidths=0.8, alpha=0.6, zorder=3)

            # 盈亏比高点（紫红点划线，从买入到最后一个卖出点）
            if wave_high_s is not None and buy_idx < len(wave_high_s):
                wh = wave_high_s[buy_idx]
                if wh > t["buy_price"] and not np.isnan(wh):
                    ax.hlines(wh, buy_idx, last_sell_idx, colors=COLOR_WAVE_HIGH,
                              linestyles="dashdot", linewidths=1.0, alpha=0.8, zorder=3)
                    ax.annotate(f"目标 {wh:.2f}", xy=(buy_idx, wh),
                                xytext=(-5, 10), textcoords="offset points",
                                fontsize=6, color=COLOR_WAVE_HIGH, ha="right")

        # 卖出标记（每笔都画）
        sell_date = t.get("sell_date")
        if sell_date:
            sell_idx_s = _find_date_index(dates_list, sell_date)
            if sell_idx_s is not None:
                sell_price = t["sell_price"]
                reason = t.get("reason", "")
                marker_color = COLOR_SELL_PARTIAL if is_partial else COLOR_SELL
                marker_size = 10 if is_partial else 14
                ax.plot(sell_idx_s, sell_price, marker="v", color=marker_color,
                        markersize=marker_size, markeredgecolor="white",
                        markeredgewidth=1, zorder=5)
                label = f"卖 {sell_price:.2f}"
                if reason:
                    label += f" ({reason})"
                ax.annotate(
                    label,
                    xy=(sell_idx_s, sell_price),
                    xytext=(0, 12), textcoords="offset points",
                    fontsize=7, color=marker_color, ha="center",
                )


def _draw_nxing_phases(ax, sig, i_start, i_end, code=None):
    """标注N型拉升和回调阶段"""
    if sig.get("nxing_pattern") is None:
        # 按需计算N型形态数据（huangbai等策略未预计算时）
        if code is None:
            return
        C = sig.get("close")
        H = sig.get("high")
        L = sig.get("low")
        O = sig.get("open")
        V = sig.get("volume")
        if any(x is None for x in [C, H, L, O, V]):
            return
        from src.strategies.nxing_zhuan_strategy import _compute_nxing_pattern
        nxing_pattern, nxing_hhvbars, nxing_hhv, nxing_rise_low = \
            _compute_nxing_pattern(C, H, L, O, V, code)
        sig["nxing_pattern"] = nxing_pattern
        sig["nxing_hhvbars"] = nxing_hhvbars
        sig["nxing_hhv"] = nxing_hhv
        sig["nxing_rise_low"] = nxing_rise_low

    pattern_full = sig["nxing_pattern"]
    hhvbars_full = sig["nxing_hhvbars"]
    hhv_full = sig["nxing_hhv"]
    rise_low_full = sig["nxing_rise_low"]
    L_full = sig.get("low")

    s = slice(i_start, i_end + 1)
    pattern_s = pattern_full[s]
    if not np.any(pattern_s):
        return

    hhvbars_s = hhvbars_full[s]
    hhv_s = hhv_full[s]
    rise_low_s = rise_low_full[s]
    L_s = L_full[s] if L_full is not None else None
    n_s = len(pattern_s)

    # 找连续的pattern区间
    groups = []
    in_group = False
    for i in range(n_s):
        if pattern_s[i]:
            if not in_group:
                group_start = i
                in_group = True
        else:
            if in_group:
                groups.append((group_start, i - 1))
                in_group = False
    if in_group:
        groups.append((group_start, n_s - 1))

    # 按高点位置去重，每个唯一高点只画一次
    seen_highs = set()
    for g_start, g_end in groups:
        ref_j = g_start
        high_offset = int(hhvbars_s[ref_j])
        high_x = ref_j - high_offset

        if high_x < 0 or high_x in seen_highs:
            continue
        seen_highs.add(high_x)

        high_price = float(hhv_s[ref_j])
        low_price = float(rise_low_s[ref_j])

        # 找拉升起点（高点前15根内的最低点）
        rise_x = max(0, high_x - 10)
        if L_s is not None and high_x > 2:
            search_start = max(0, high_x - 15)
            search_end = max(0, high_x)
            if search_end > search_start:
                window = L_s[search_start:search_end + 1]
                if len(window) > 0:
                    rise_x = search_start + int(np.argmin(window))

        pullback_end_x = g_end

        # 绘制拉升阶段背景（绿色）
        if high_x > rise_x:
            ax.axvspan(rise_x - 0.5, high_x + 0.5, alpha=0.12,
                       color=COLOR_NXING_RISE, zorder=0)

        # 绘制回调阶段背景（橙色）
        if pullback_end_x >= high_x:
            ax.axvspan(high_x - 0.5, pullback_end_x + 0.5, alpha=0.12,
                       color=COLOR_NXING_PULLBACK, zorder=0)

        # 标记高点
        if 0 <= high_x < n_s:
            ax.plot(high_x, high_price, marker="D", color=COLOR_NXING_HIGH,
                    markersize=8, markeredgecolor="white", markeredgewidth=1, zorder=6)
            ax.annotate(f"N高 {high_price:.2f}",
                        xy=(high_x, high_price),
                        xytext=(8, 10), textcoords="offset points",
                        fontsize=7, color=COLOR_NXING_HIGH, fontweight="bold",
                        bbox=dict(boxstyle="round,pad=0.2", fc="white",
                                  ec=COLOR_NXING_HIGH, alpha=0.85))

        # 标记低点
        if 0 <= rise_x < n_s:
            ax.plot(rise_x, low_price, marker="D", color=COLOR_NXING_LOW,
                    markersize=6, markeredgecolor="white", markeredgewidth=1, zorder=6)
            ax.annotate(f"N低 {low_price:.2f}",
                        xy=(rise_x, low_price),
                        xytext=(-8, -14), textcoords="offset points",
                        fontsize=7, color=COLOR_NXING_LOW, fontweight="bold",
                        bbox=dict(boxstyle="round,pad=0.2", fc="white",
                                  ec=COLOR_NXING_LOW, alpha=0.85))


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
