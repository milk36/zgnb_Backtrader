"""组合级回测模拟器

按正确的时间序列模拟完整交易流程：
- 每周一：全市场周线多头扫描 → 更新观察池
- 每日：观察池内黄白线金叉 + B1 选股 → 按缩量排序取最优
- 组合级仓位管理：100万总资金，最多10只，每只10万
"""

import os
from datetime import datetime

import numpy as np
import pandas as pd


class Position:
    """跟踪单只股票持仓"""

    __slots__ = (
        "code", "buy_date", "buy_price", "buy_low",
        "white_at_buy", "yellow_at_buy", "stop_loss",
        "size", "initial_size", "hold_until_below_white",
        "mid_yang_triggered", "partial_proceeds",
        "partial_sold",
        "momentum_hold", "consecutive_tp_days", "consecutive_down_days",
        "profit_100pct", "profit_100pct_down_days",
        # V5 战法退出字段
        "key_k_high", "key_k_low", "key_k_bar",
        "white_break_pending", "white_break_bar",
        "has_accelerated", "max_price_since_buy",
        "surge_reduction_done", "sl_based_on_yellow",
    )

    def __init__(self, code, buy_date, buy_price, buy_low,
                 white_at_buy, yellow_at_buy, stop_loss, size):
        self.code = code
        self.buy_date = buy_date
        self.buy_price = buy_price
        self.buy_low = buy_low
        self.white_at_buy = white_at_buy
        self.yellow_at_buy = yellow_at_buy
        self.stop_loss = stop_loss
        self.size = size
        self.initial_size = size
        self.hold_until_below_white = False
        self.mid_yang_triggered = False
        self.partial_proceeds = 0.0  # 部分卖出累计回款
        self.partial_sold = False   # 是否发生过部分卖出
        self.momentum_hold = False
        self.consecutive_tp_days = 0
        self.consecutive_down_days = 0
        self.profit_100pct = False
        self.profit_100pct_down_days = 0
        # V5 战法退出字段
        self.key_k_high = None
        self.key_k_low = None
        self.key_k_bar = None
        self.white_break_pending = False
        self.white_break_bar = None
        self.has_accelerated = False
        self.max_price_since_buy = 0.0
        self.surge_reduction_done = False
        self.sl_based_on_yellow = False


class PortfolioSimulator:
    """组合级日频模拟引擎"""

    def __init__(self, all_signals, trading_days,
                 initial_cash=1_000_000, max_positions=10,
                 per_position_cash=100_000, commission=0.0003,
                 stock_type="main", t_plus_n=3, log_dir="logs",
                 market_macd_bullish=None, strategy_tag=None):
        self._all_signals = all_signals
        self._trading_days = trading_days
        self._initial_cash = initial_cash
        self._max_positions = max_positions
        self._per_position_cash = per_position_cash
        self._commission = commission
        self._stock_type = stock_type
        self._t_plus_n = t_plus_n
        self._market_macd_bullish = market_macd_bullish  # np.array[bool] 或 None
        if strategy_tag:
            self._strategy_tag = strategy_tag
        elif market_macd_bullish is not None:
            self._strategy_tag = "[B1V2]"
        else:
            self._strategy_tag = "[B1]"
        if market_macd_bullish is not None and len(market_macd_bullish) != len(trading_days):
            raise ValueError(
                f"market_macd_bullish 长度({len(market_macd_bullish)})与 "
                f"trading_days 长度({len(trading_days)})不匹配")

        # 日志文件（追加模式）
        os.makedirs(log_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._log_path = os.path.join(log_dir, f"portfolio_{ts}.log")
        self._log_file = open(self._log_path, "a", encoding="utf-8")

        # 为每只股票预构建日期查找索引
        self._date_indices = {}
        for code, sig in all_signals.items():
            dates = sig["dates"]
            if isinstance(dates, pd.DatetimeIndex):
                arr = dates.values
            elif hasattr(dates, "values"):
                arr = pd.to_datetime(dates).values
            else:
                arr = pd.to_datetime(dates).values
            self._date_indices[code] = arr

    def _log(self, msg):
        """同时输出到控制台和日志文件"""
        print(msg)
        self._log_file.write(msg + "\n")
        self._log_file.flush()

    def _find_bar_index(self, code, target_date):
        """找到 target_date 在该股票数据中的 bar 索引

        如果当天停牌，返回最近的前一个交易日索引。
        返回 None 表示该日期之前没有数据。
        """
        arr = self._date_indices.get(code)
        if arr is None:
            return None
        target = np.datetime64(target_date)
        idx = np.searchsorted(arr, target, side="right") - 1
        if idx < 0:
            return None
        return idx

    def _log_macd_summary(self):
        """输出大盘MACD多头/空头日期区间摘要"""
        bullish = self._market_macd_bullish
        days = self._trading_days
        segments = []
        i = 0
        while i < len(bullish):
            if bullish[i]:
                start = days[i]
                while i < len(bullish) and bullish[i]:
                    i += 1
                end = days[i - 1]
                segments.append(("多头", start, end))
            else:
                start = days[i]
                while i < len(bullish) and not bullish[i]:
                    i += 1
                end = days[i - 1]
                segments.append(("空头", start, end))
        self._log(f"  {self._strategy_tag} 大盘MACD日期区间:")
        for label, s, e in segments:
            self._log(f"    {label}: {s.strftime('%Y-%m-%d')} ~ {e.strftime('%Y-%m-%d')}")

    def run(self):
        """执行组合级模拟"""
        self._cash = self._initial_cash
        self._positions = {}         # code -> Position
        self._watchlist = set()      # 周线多头股票代码集
        self._equity_curve = []      # 每日总权益
        self._trade_list = []        # 已完成交易记录
        self._last_month = (-1, -1)  # 上次更新观察池的 (year, month)
        self._cooldown = {}          # 止损冷却: code -> (stop_loss_date, td_index)

        # 诊断：打印交易日历范围
        if len(self._trading_days) > 0:
            first = self._trading_days[0]
            last = self._trading_days[-1]
            years = pd.Series(self._trading_days.year).value_counts().sort_index()
            year_info = "  ".join(f"{y}年:{c}天" for y, c in years.items())
            self._log(f"  {self._strategy_tag} 交易日历: {first.strftime('%Y-%m-%d')} ~ {last.strftime('%Y-%m-%d')}  "
                      f"共{len(self._trading_days)}天  [{year_info}]")
            self._log(f"  {self._strategy_tag} 日志文件: {self._log_path}")

        # 输出大盘MACD多头日期区间摘要
        if self._market_macd_bullish is not None:
            self._log_macd_summary()

        # 构建交易日 → 序号映射（用于计算持仓交易日数）
        self._td_index = {td: i for i, td in enumerate(self._trading_days)}

        for td in self._trading_days:
            # 每月首个交易日更新观察池
            year_month = (td.year, td.month)
            if year_month != self._last_month:
                self._update_watchlist(td)
                self._last_month = year_month
                self._log(f"  [{td.strftime('%Y-%m-%d')}] {self._strategy_tag} 月度更新观察池: {len(self._watchlist)} 只")

            # 每日卖出检查
            self._check_exits(td)

            # 每日买入检查（最后一天只卖不买，避免T+0）
            if td != self._trading_days[-1]:
                self._check_entries(td)

            # 记录每日权益
            equity = self._calc_equity(td)
            self._equity_curve.append(equity)

        # 模拟结束：强制清仓所有未平仓持仓
        if self._positions:
            last_date = self._trading_days[-1]
            for code in list(self._positions.keys()):
                pos = self._positions[code]
                idx = self._find_bar_index(code, last_date)
                sig = self._all_signals.get(code)
                price = float(sig["close"][idx]) if idx is not None and sig is not None else pos.buy_price
                self._sell_position(code, pos, price, last_date, "模拟结束清仓")

    def _update_watchlist(self, date):
        """遍历全部股票，更新周线多头观察池"""
        cur_idx = self._td_index.get(date, 0)
        self._watchlist = set()
        for code, sig in self._all_signals.items():
            if code in self._positions:
                continue
            # 止损冷却期：2周（10个交易日）内不再纳入观察池
            if code in self._cooldown:
                sl_idx = self._cooldown[code]
                if cur_idx - sl_idx < 10:
                    continue
                else:
                    del self._cooldown[code]
            idx = self._find_bar_index(code, date)
            if idx is None:
                continue
            try:
                if sig["weekly_bull"][idx] and sig["above_ma30w"][idx]:
                    self._watchlist.add(code)
            except (IndexError, TypeError):
                pass

    def _check_entries(self, date):
        """从观察池中筛选 gc_ok + b1 的候选，买入缩量最优的1只"""
        if len(self._positions) >= self._max_positions:
            return
        if self._cash < self._per_position_cash * 0.5:
            return

        # V2: 大盘MACD过滤 — 空头时只卖不买
        if self._market_macd_bullish is not None:
            td_idx = self._td_index.get(date)
            if td_idx is not None and not self._market_macd_bullish[td_idx]:
                return

        candidates = []
        cur_idx = self._td_index.get(date, 0)
        for code in self._watchlist:
            if code in self._positions:
                continue
            if code in self._cooldown:
                if cur_idx - self._cooldown[code] < 10:
                    continue
                else:
                    del self._cooldown[code]
            sig = self._all_signals[code]
            idx = self._find_bar_index(code, date)
            if idx is None or idx < 1:
                continue
            try:
                gc_ok = sig["recent_gc"][idx]
                b1_ok = sig["b1"][idx]
                stock_macd_ok = sig.get("stock_macd_bullish", np.ones(idx + 1, dtype=bool))[idx]
                vol_expand_ok = sig.get("vol_expand_ok", np.ones(idx + 1, dtype=bool))[idx]
                if gc_ok and b1_ok and stock_macd_ok and vol_expand_ok:
                    score = sig["shrink_score"][idx]
                    if np.isnan(score):
                        score = 1.0
                    avg_amt = sig.get("avg_amount_20", np.zeros(idx + 1))[idx]
                    if np.isnan(avg_amt):
                        avg_amt = 0.0
                    chip_sp = sig.get("chip_spread", None)
                    if chip_sp is not None:
                        cs = chip_sp[idx]
                        if np.isnan(cs):
                            cs = float('inf')
                    else:
                        cs = float('inf')
                    candidates.append((code, score, avg_amt, cs, idx, sig))
            except (IndexError, TypeError):
                continue

        if not candidates:
            return

        # 按 shrink_score 升序, avg_amount_20 降序, chip_spread 升序
        candidates.sort(key=lambda x: (x[1], -x[2], x[3]))
        code, score, avg_amt, cs, idx, sig = candidates[0]

        price = sig["close"][idx]
        if price <= 0:
            return

        # 计算可买股数（100股整手）
        buy_cost = price * (1 + self._commission)
        shares = int(self._per_position_cash / buy_cost / 100) * 100
        if shares <= 0:
            return

        total_cost = shares * price * (1 + self._commission)
        if total_cost > self._cash:
            shares = int(self._cash / buy_cost / 100) * 100
            if shares <= 0:
                return
            total_cost = shares * price * (1 + self._commission)

        # 止损价
        white_val = sig["white"][idx]
        yellow_val = sig["yellow"][idx]
        low_val = sig["low"][idx]
        if price >= white_val:
            # 白线之上买入 → 买入日最低价止损
            sl = low_val
        elif price >= yellow_val:
            # 白线和黄线之间 → 黄线价止损
            sl = yellow_val
        else:
            # 黄线之下 → 买入日最低价止损
            sl = low_val

        pos = Position(
            code=code, buy_date=date, buy_price=price,
            buy_low=low_val, white_at_buy=white_val,
            yellow_at_buy=yellow_val, stop_loss=sl, size=shares,
        )
        pos.sl_based_on_yellow = (price < white_val)
        self._positions[code] = pos
        self._cash -= total_cost

        # V5: 买入时识别关键K
        if "V5" in self._strategy_tag:
            self._identify_key_k_at_buy(pos, idx, sig)

        # 大盘MACD状态
        macd_tag = ""
        if self._market_macd_bullish is not None:
            td_idx = self._td_index.get(date)
            if td_idx is not None:
                macd_tag = f"  大盘={'多头' if self._market_macd_bullish[td_idx] else '空头'}"

        self._log(f"  [{date.strftime('%Y-%m-%d')}] {self._strategy_tag} 买入 {code}  "
                  f"价格={price:.2f}  数量={shares}  止损={sl:.2f}  "
                  f"缩量={score:.3f}{macd_tag}  "
                  f"持仓={len(self._positions)}/{self._max_positions}  "
                  f"现金={self._cash:,.0f}")

    def _check_exits(self, date):
        """检查所有持仓的卖出条件"""
        # V5: 使用战法退出逻辑
        if "V5" in self._strategy_tag:
            self._check_exits_v5(date)
            return

        to_remove = []
        for code, pos in list(self._positions.items()):
            sig = self._all_signals.get(code)
            if sig is None:
                continue
            idx = self._find_bar_index(code, date)
            if idx is None:
                continue
            try:
                price = sig["close"][idx]
                high = sig["high"][idx]
                low = sig["low"][idx]
                white_val = sig["white"][idx]
                yellow_val = sig["yellow"][idx]
            except (IndexError, TypeError):
                continue

            if price <= 0:
                continue

            if pos.initial_size == 0:
                pos.initial_size = pos.size

            # 计算持仓交易日数
            buy_idx = self._td_index.get(pos.buy_date)
            cur_idx = self._td_index.get(date)
            if buy_idx is not None and cur_idx is not None:
                bars_held = cur_idx - buy_idx
            else:
                bars_held = (date - pos.buy_date).days
            days_held = bars_held

            # 计算摊薄成本价（扣除部分卖出回款后的真实成本）
            total_cost = pos.initial_size * pos.buy_price * (1 + self._commission)
            remaining_cost = total_cost - pos.partial_proceeds
            avg_cost = remaining_cost / pos.size if pos.size > 0 else pos.buy_price

            pct_gain = (price - pos.buy_price) / pos.buy_price * 100
            real_gain = (price - avg_cost) / avg_cost * 100

            # 1. 止损
            if price <= pos.stop_loss:
                cur_idx = self._td_index.get(date)
                if cur_idx is not None:
                    self._cooldown[code] = cur_idx
                self._sell_position(code, pos, price, date, "止损")
                to_remove.append(code)
                continue

            # 2. T+N 没涨清仓（基于摊薄成本价判断）
            if days_held >= self._t_plus_n and price <= avg_cost:
                cur_idx = self._td_index.get(date)
                if cur_idx is not None:
                    self._cooldown[code] = cur_idx
                self._sell_position(code, pos, price, date, f"T+{days_held}清仓")
                to_remove.append(code)
                continue

            # 3. 盈利100%后连跌2天清仓（基于实际总收益，含部分卖出回款）
            total_cost = pos.initial_size * pos.buy_price * (1 + self._commission)
            current_value = pos.size * price * (1 - self._commission)
            total_proceeds = pos.partial_proceeds + current_value
            total_pnl_pct = (total_proceeds - total_cost) / total_cost * 100
            if pos.profit_100pct:
                prev_close = sig["close"][idx - 1] if idx >= 1 else 0
                if prev_close > 0 and price < prev_close:
                    pos.profit_100pct_down_days += 1
                else:
                    pos.profit_100pct_down_days = 0
                if pos.profit_100pct_down_days >= 2:
                    cur_idx = self._td_index.get(date)
                    if cur_idx is not None:
                        self._cooldown[code] = cur_idx
                    self._sell_position(code, pos, price, date, "盈利100%连跌清仓")
                    to_remove.append(code)
                    continue
                # 未连跌2天，继续持股，跳过后续卖出
                continue
            if total_pnl_pct >= 100:
                pos.profit_100pct = True
                continue

            # 3.5 部分卖出后白线/黄线跟踪止损
            if pos.partial_sold and not pos.hold_until_below_white:
                sold = False
                wy_gap_pct = abs(white_val - yellow_val) / yellow_val * 100 if yellow_val > 0 else 0
                if real_gain <= 20:
                    if wy_gap_pct <= 10:
                        if price <= avg_cost or price < yellow_val:
                            cur_idx = self._td_index.get(date)
                            if cur_idx is not None:
                                self._cooldown[code] = cur_idx
                            reason = "部分卖出后跌破黄线" if price < yellow_val else "部分卖出后跌破成本"
                            self._sell_position(code, pos, price, date, reason)
                            to_remove.append(code)
                            sold = True
                    else:
                        if price <= avg_cost or price < white_val:
                            cur_idx = self._td_index.get(date)
                            if cur_idx is not None:
                                self._cooldown[code] = cur_idx
                            reason = "部分卖出后跌破白线" if price < white_val else "部分卖出后跌破成本"
                            self._sell_position(code, pos, price, date, reason)
                            to_remove.append(code)
                            sold = True
                else:
                    if wy_gap_pct <= 10:
                        if price < yellow_val:
                            cur_idx = self._td_index.get(date)
                            if cur_idx is not None:
                                self._cooldown[code] = cur_idx
                            self._sell_position(code, pos, price, date, "部分卖出后跌破黄线")
                            to_remove.append(code)
                            sold = True
                    else:
                        if price < white_val:
                            cur_idx = self._td_index.get(date)
                            if cur_idx is not None:
                                self._cooldown[code] = cur_idx
                            self._sell_position(code, pos, price, date, "部分卖出后跌破白线")
                            to_remove.append(code)
                            sold = True
                if sold:
                    continue

            # 4. 半仓持股模式（跌破白线/摊薄成本价/黄线可清仓）
            if pos.hold_until_below_white:
                sold = False
                wy_gap_pct = abs(white_val - yellow_val) / yellow_val * 100 if yellow_val > 0 else 0
                if real_gain <= 20:
                    if wy_gap_pct <= 10:
                        if price <= avg_cost or price < yellow_val:
                            cur_idx = self._td_index.get(date)
                            if cur_idx is not None:
                                self._cooldown[code] = cur_idx
                            reason = "半仓跌破黄线" if price < yellow_val else "半仓盈转亏清仓"
                            self._sell_position(code, pos, price, date, reason)
                            to_remove.append(code)
                            sold = True
                    else:
                        if price <= avg_cost or price < white_val:
                            cur_idx = self._td_index.get(date)
                            if cur_idx is not None:
                                self._cooldown[code] = cur_idx
                            reason = "半仓跌破白线" if price < white_val else "半仓盈转亏清仓"
                            self._sell_position(code, pos, price, date, reason)
                            to_remove.append(code)
                            sold = True
                else:
                    # 盈利>20%：黄白线差值≤5%则跌破黄线清仓，否则跌破白线清仓
                    wy_gap_pct = abs(white_val - yellow_val) / yellow_val * 100 if yellow_val > 0 else 0
                    if wy_gap_pct <= 10:
                        if price < yellow_val:
                            cur_idx = self._td_index.get(date)
                            if cur_idx is not None:
                                self._cooldown[code] = cur_idx
                            self._sell_position(code, pos, price, date, "半仓跌破黄线")
                            to_remove.append(code)
                            sold = True
                    else:
                        if price < white_val:
                            cur_idx = self._td_index.get(date)
                            if cur_idx is not None:
                                self._cooldown[code] = cur_idx
                            self._sell_position(code, pos, price, date, "半仓跌破白线")
                            to_remove.append(code)
                            sold = True
                if sold:
                    continue
                # 未清仓：仍允许涨停卖1/2和中阳卖1/3

            # 5. 动量持股逻辑（连续止盈后持股待涨）
            if idx >= 1:
                prev_close = sig["close"][idx - 1]
                if prev_close > 0:
                    _is_tech = code[:2] in ("30", "68")
                    limit_pct = 1.20 if _is_tech else 1.10
                    limit_up_price = round(prev_close * limit_pct, 2)
                    daily_up = price > prev_close
                    if pos.hold_until_below_white:
                        mid_yang = 15 if _is_tech else 8
                    else:
                        mid_yang = 10 if _is_tech else 5
                    hit_limit_up = high >= limit_up_price
                    hit_mid_yang = daily_up and pct_gain >= mid_yang
                    tp_met = hit_limit_up or hit_mid_yang

                    if pos.momentum_hold:
                        # 动量持股模式：检测退出条件
                        if price < prev_close:
                            pos.consecutive_down_days += 1
                        else:
                            pos.consecutive_down_days = 0
                        drop_pct = (prev_close - price) / prev_close * 100
                        drop_threshold = 14.0 if _is_tech else 7.0
                        if drop_pct > drop_threshold:
                            cur_idx = self._td_index.get(date)
                            if cur_idx is not None:
                                self._cooldown[code] = cur_idx
                            self._sell_position(code, pos, price, date, "动量结束清仓")
                            to_remove.append(code)
                            continue
                        # 未退出动量持股，跳过后续涨停/中阳
                        continue

                    # 正常模式：累计连续止盈天数
                    if tp_met:
                        pos.consecutive_tp_days += 1
                    else:
                        pos.consecutive_tp_days = 0

                    if pos.consecutive_tp_days >= 3:
                        pos.momentum_hold = True
                        continue  # 第3天不卖，进入动量持股

                    # 6. 涨停卖1/2（半仓模式下仍可触发，不受中阳标记限制）
                    if high >= limit_up_price:
                        sell_size = max(1, pos.size // 2)
                        if sell_size < pos.size:
                            self._sell_partial(code, pos, sell_size, price, date, "涨停卖半")
                            if pos.size <= pos.initial_size // 2:
                                pos.hold_until_below_white = True
                        continue

                    # 7. 中阳卖1/3（当日上涨 + 累计盈利达标，仅触发一次）
                    if not pos.mid_yang_triggered and daily_up:
                        if pct_gain >= mid_yang:
                            sell_size = max(1, pos.size // 3)
                            if sell_size < pos.size:
                                self._sell_partial(code, pos, sell_size, price, date, "中阳卖1/3")
                                pos.mid_yang_triggered = True
                                if pos.size <= pos.initial_size // 2:
                                    pos.hold_until_below_white = True

        for code in to_remove:
            del self._positions[code]

    # ------------------------------------------------------------------ #
    #  V5 战法退出逻辑                                                    #
    # ------------------------------------------------------------------ #

    def _identify_key_k_at_buy(self, pos, buy_idx, sig):
        """买入时向后扫描30根K线，识别最显著的放量阳线作为关键K"""
        C, O, V, H = sig["close"], sig["open"], sig["volume"], sig["high"]
        start = max(0, buy_idx - 30)
        best_sig = 0
        best_idx = None
        for i in range(start, buy_idx + 1):
            c, o, v = C[i], O[i], V[i]
            if c <= o:
                continue
            body_pct = (c - o) / c * 100
            if body_pct < 2:
                continue
            ma_v = sig.get("ma_v20")
            if ma_v is not None:
                if v <= ma_v[i]:
                    continue
            sig_val = v * body_pct
            if sig_val > best_sig:
                best_sig = sig_val
                best_idx = i
        if best_idx is not None:
            pos.key_k_high = H[best_idx]
            pos.key_k_low = O[best_idx]
            pos.key_k_bar = best_idx

    def _update_key_k_for_position(self, pos, idx, sig):
        """持有期更新关键K"""
        C, O, V, H = sig["close"], sig["open"], sig["volume"], sig["high"]
        c, o, v, h = C[idx], O[idx], V[idx], H[idx]
        if c <= o:
            return
        body_pct = (c - o) / c * 100
        if body_pct < 2:
            return
        ma_v = sig.get("ma_v20")
        if ma_v is not None and v <= ma_v[idx]:
            return
        significance = v * body_pct
        if pos.key_k_bar is not None:
            old_c, old_o = C[pos.key_k_bar], O[pos.key_k_bar]
            old_body = (old_c - old_o) / old_c * 100 if old_c > 0 else 0
            old_sig = V[pos.key_k_bar] * old_body
            if significance <= old_sig:
                return
        pos.key_k_high = h
        pos.key_k_low = o
        pos.key_k_bar = idx

    def _check_exits_v5(self, date):
        """V5 战法六级退出逻辑"""
        to_remove = []
        for code, pos in list(self._positions.items()):
            sig = self._all_signals.get(code)
            if sig is None:
                continue
            idx = self._find_bar_index(code, date)
            if idx is None or idx < 2:
                continue
            try:
                price = sig["close"][idx]
                high = sig["high"][idx]
                low = sig["low"][idx]
                open_price = sig["open"][idx]
                volume = sig["volume"][idx]
                white_val = sig["white"][idx]
                yellow_val = sig["yellow"][idx]
                prev_close = sig["close"][idx - 1]
            except (IndexError, TypeError):
                continue

            if price <= 0 or prev_close <= 0:
                continue

            if pos.initial_size == 0:
                pos.initial_size = pos.size

            cur_td_idx = self._td_index.get(date)
            pct_gain = (price - pos.buy_price) / pos.buy_price * 100

            # 更新最高价
            if price > pos.max_price_since_buy:
                pos.max_price_since_buy = price

            # 加速检测（增量扫描）
            if not pos.has_accelerated:
                buy_bar_idx = self._find_bar_index(code, pos.buy_date)
                if buy_bar_idx is not None:
                    C = sig["close"]
                    for b in range(max(buy_bar_idx + 5, 5), idx + 1):
                        if C[b - 5] > 0:
                            gain = (C[b] - C[b - 5]) / C[b - 5] * 100
                            if gain > 15:
                                pos.has_accelerated = True
                                break

            # 预计算辅助判断
            ma_v20 = sig.get("ma_v20")
            hhv_v50 = sig.get("hhv_v50")
            hhv_v20 = sig.get("hhv_v20")
            ma_v60 = sig.get("ma_v60")
            hhv_h20 = sig.get("hhv_h20")

            is_shrinking = False
            if ma_v20 is not None and hhv_v50 is not None:
                is_shrinking = (volume < ma_v20[idx] * 0.618) or (volume < hhv_v50[idx] / 3)

            in_key_k = (pos.key_k_high is not None
                        and pos.key_k_low is not None
                        and pos.key_k_low <= price <= pos.key_k_high)

            _is_tech = code[:2] in ("30", "68")
            limit_pct = 1.20 if _is_tech else 1.10

            # ---- L1: 硬止损 ----
            if price <= pos.stop_loss:
                if cur_td_idx is not None:
                    self._cooldown[code] = cur_td_idx
                self._sell_position(code, pos, price, date, "止损")
                to_remove.append(code)
                continue

            # ---- L2: 放量跌停 ----
            limit_down_price = round(prev_close * (2 - limit_pct), 2)
            vol_expanding = ma_v20 is not None and volume > ma_v20[idx] * 1.5
            if vol_expanding and price <= limit_down_price:
                if cur_td_idx is not None:
                    self._cooldown[code] = cur_td_idx
                self._sell_position(code, pos, price, date, "放量跌停")
                to_remove.append(code)
                continue

            # ---- L3: S1信号持有期卖出 ----
            if pos.has_accelerated:
                big_vol = False
                if hhv_v20 is not None and ma_v60 is not None:
                    big_vol = (volume > hhv_v20[idx] * 2) or (volume > ma_v60[idx] * 3)
                bearish = price < open_price
                body_pct = abs(open_price - price) / open_price * 100 if open_price > 0 else 0
                if big_vol and bearish and body_pct > 3:
                    if not (is_shrinking and in_key_k):
                        if cur_td_idx is not None:
                            self._cooldown[code] = cur_td_idx
                        self._sell_position(code, pos, price, date, "S1信号清仓")
                        to_remove.append(code)
                        continue

            # ---- L4: 两根平行中阴线 ----
            C = sig["close"]
            O = sig["open"]
            c1, o1 = C[idx - 1], O[idx - 1]
            bearish0 = price < open_price
            bearish1 = c1 < o1
            body0 = abs(open_price - price) / open_price * 100 if open_price > 0 else 0
            body1 = abs(o1 - c1) / o1 * 100 if o1 > 0 else 0
            at_local_high = hhv_h20 is not None and price >= hhv_h20[idx] * 0.97
            if bearish0 and bearish1 and body0 > 2.5 and body1 > 2.5 and at_local_high:
                if cur_td_idx is not None:
                    self._cooldown[code] = cur_td_idx
                self._sell_position(code, pos, price, date, "两根中阴线清仓")
                to_remove.append(code)
                continue

            # ---- L5: 参考线次日确认 ----
            wy_gap_pct = abs(white_val - yellow_val) / yellow_val * 100 if yellow_val > 0 else 100
            if wy_gap_pct <= 10:
                l5_ref = yellow_val
                l5_name = "黄线"
            elif pos.sl_based_on_yellow:
                l5_ref = yellow_val * 1.01
                l5_name = "黄线+1%"
            else:
                l5_ref = white_val * 1.01
                l5_name = "白线+1%"

            if pos.white_break_pending:
                if price < l5_ref:
                    # 例外: 缩量+关键K内 或 未加速+缩量 → 不卖
                    if is_shrinking and in_key_k:
                        pos.white_break_pending = False
                    elif not pos.has_accelerated and is_shrinking:
                        pos.white_break_pending = False
                    else:
                        if cur_td_idx is not None:
                            self._cooldown[code] = cur_td_idx
                        self._sell_position(code, pos, price, date, f"{l5_name}确认清仓")
                        to_remove.append(code)
                        continue
                else:
                    pos.white_break_pending = False
            else:
                if price < l5_ref:
                    pos.white_break_pending = True
                    pos.white_break_bar = idx

            # ---- L6: 放飞减仓 ----
            limit_up_price = round(prev_close * limit_pct, 2)
            daily_up = price > prev_close

            # 6a: 涨停减仓1/3
            if high >= limit_up_price:
                sell_size = max(1, pos.size // 3)
                if sell_size < pos.size:
                    self._sell_partial(code, pos, sell_size, price, date, "涨停放飞1/3")
                    pos.partial_sold = True
                continue

            # 6b: 大涨减仓1/3（盈利>10%+当日上涨，仅一次）
            if not pos.surge_reduction_done and daily_up and pct_gain > 10:
                sell_size = max(1, pos.size // 3)
                if sell_size < pos.size:
                    self._sell_partial(code, pos, sell_size, price, date, "大涨放飞1/3")
                    pos.partial_sold = True
                    pos.surge_reduction_done = True
                continue

            # ---- 更新关键K ----
            self._update_key_k_for_position(pos, idx, sig)

        for code in to_remove:
            del self._positions[code]

    def _sell_position(self, code, pos, price, date, reason):
        """全部卖出"""
        proceeds = pos.size * price * (1 - self._commission)
        total_proceeds = pos.partial_proceeds + proceeds
        total_cost = pos.initial_size * pos.buy_price * (1 + self._commission)
        pnl = (total_proceeds - total_cost) / total_cost * 100
        pnl_amount = total_proceeds - total_cost
        # 剩余持仓的摊薄成本价
        remaining_cost = total_cost - pos.partial_proceeds
        avg_cost = remaining_cost / pos.size if pos.size > 0 else 0
        self._cash += proceeds
        self._trade_list.append({
            "code": code,
            "buy_date": pos.buy_date,
            "sell_date": date,
            "buy_price": pos.buy_price,
            "sell_price": price,
            "size": pos.initial_size,
            "pnl_pct": pnl,
            "pnl_amount": pnl_amount,
            "reason": reason,
            "stop_loss": pos.stop_loss,
            "white_at_buy": pos.white_at_buy,
            "yellow_at_buy": pos.yellow_at_buy,
        })
        self._log(f"  [{date.strftime('%Y-%m-%d')}] {self._strategy_tag} 清仓 {code}  "
                  f"价格={price:.2f}  "
                  f"成本价 {pos.buy_price:.2f}→{avg_cost:.2f}  "
                  f"总成本={total_cost:,.0f}  累计回款={total_proceeds:,.0f}  "
                  f"收益={pnl:+.2f}%({pnl_amount:+,.0f})  {reason}  "
                  f"持仓={len(self._positions)-1}/{self._max_positions}  "
                  f"现金={self._cash:,.0f}")

    def _sell_partial(self, code, pos, sell_size, price, date, reason):
        """部分卖出"""
        proceeds = sell_size * price * (1 - self._commission)
        total_cost = pos.initial_size * pos.buy_price * (1 + self._commission)
        pnl = (price - pos.buy_price) / pos.buy_price * 100
        self._cash += proceeds
        pos.partial_proceeds += proceeds
        pos.partial_sold = True
        pos.size -= sell_size
        self._trade_list.append({
            "code": code,
            "buy_date": pos.buy_date,
            "sell_date": date,
            "buy_price": pos.buy_price,
            "sell_price": price,
            "size": sell_size,
            "pnl_pct": pnl,
            "pnl_amount": proceeds - sell_size * pos.buy_price * (1 + self._commission),
            "reason": reason,
            "stop_loss": pos.stop_loss,
            "white_at_buy": pos.white_at_buy,
            "yellow_at_buy": pos.yellow_at_buy,
            "partial": True,
        })
        remaining_cost = total_cost - pos.partial_proceeds
        avg_cost = remaining_cost / pos.size if pos.size > 0 else 0
        self._log(f"  [{date.strftime('%Y-%m-%d')}] {self._strategy_tag} 卖出 {code}  "
                  f"{sell_size}股→剩余{pos.size}股  价格={price:.2f}  "
                  f"成本价 {pos.buy_price:.2f}→{avg_cost:.2f}  "
                  f"剩余成本={remaining_cost:,.0f}  本次回款={proceeds:,.0f}  "
                  f"盈亏={pnl:+.2f}%  {reason}  "
                  f"持仓={len(self._positions)}/{self._max_positions}  "
                  f"现金={self._cash:,.0f}")

    def _calc_equity(self, date):
        """计算当日总权益 = 现金 + 持仓市值"""
        market_value = 0.0
        for code, pos in self._positions.items():
            sig = self._all_signals.get(code)
            if sig is None:
                market_value += pos.size * pos.buy_price
                continue
            idx = self._find_bar_index(code, date)
            if idx is not None:
                try:
                    market_value += pos.size * sig["close"][idx]
                except (IndexError, TypeError):
                    market_value += pos.size * pos.buy_price
            else:
                market_value += pos.size * pos.buy_price
        return self._cash + market_value

    def report(self):
        """生成回测报告"""
        equity = np.array(self._equity_curve)
        initial = self._initial_cash
        final = equity[-1] if len(equity) > 0 else initial

        total_return = (final - initial) / initial * 100

        # 最大回撤
        peak = np.maximum.accumulate(equity)
        drawdown = (equity - peak) / peak * 100
        max_drawdown = float(np.min(drawdown)) if len(drawdown) > 0 else 0.0

        # 夏普比率（日收益率年化）
        if len(equity) > 1:
            daily_returns = np.diff(equity) / equity[:-1]
            if np.std(daily_returns) > 0:
                sharpe = float(np.mean(daily_returns) / np.std(daily_returns) * np.sqrt(252))
            else:
                sharpe = None
        else:
            sharpe = None

        # 交易统计（仅统计清仓交易）
        closed_trades = [t for t in self._trade_list if not t.get("partial")]
        total_trades = len(closed_trades)

        # 按股票汇总计算胜率
        stock_pnl = {}
        for t in closed_trades:
            c = t["code"]
            stock_pnl[c] = stock_pnl.get(c, 0.0) + t["pnl_amount"]
        won = sum(1 for v in stock_pnl.values() if v > 0)
        lost = sum(1 for v in stock_pnl.values() if v <= 0)

        return {
            "initial_cash": initial,
            "final_value": float(final),
            "total_return": total_return,
            "max_drawdown": max_drawdown,
            "sharpe": sharpe,
            "total_trades": len(stock_pnl),
            "won": won,
            "lost": lost,
            "trade_list": self._trade_list,
            "trading_days": len(self._trading_days),
            "_commission": self._commission,
        }

    @staticmethod
    def print_report(report, log_file=None, strategy_tag="[B1]"):
        """打印回测报告（同时写入日志文件）"""
        def _out(msg):
            print(msg)
            if log_file and not log_file.closed:
                log_file.write(msg + "\n")
                log_file.flush()

        _out(f"\n{'=' * 55}")
        _out(f"          {strategy_tag} 组合回测报告")
        _out(f"{'=' * 55}")
        _out(f"  回测区间:    {report['trading_days']} 个交易日")
        _out(f"  初始资金:    {report['initial_cash']:>12,.2f}")
        _out(f"  最终资金:    {report['final_value']:>12,.2f}")
        _out(f"  总收益率:    {report['total_return']:>11.2f}%")
        _out(f"  最大回撤:    {report['max_drawdown']:>11.2f}%")

        sharpe = report["sharpe"]
        if sharpe is not None:
            _out(f"  夏普比率:    {sharpe:>11.4f}")
        else:
            _out(f"  夏普比率:        N/A")

        _out(f"  交易股票数:  {report['total_trades']:>12}")
        _out(f"  盈利股票:    {report['won']:>12}")
        _out(f"  亏损股票:    {report['lost']:>12}")
        won = report["won"]
        total = won + report["lost"]
        if total > 0:
            _out(f"  胜率:        {won / total * 100:>11.2f}%")

        # 交易明细：按股票汇总，按盈利金额从高到低排序
        all_trades = report["trade_list"]
        if all_trades:
            from collections import OrderedDict
            stock_summary = OrderedDict()
            for t in all_trades:
                c = t["code"]
                if c not in stock_summary:
                    stock_summary[c] = {
                        "code": c,
                        "buy_date": t["buy_date"],
                        "sell_date": t["sell_date"],
                        "buy_price": t["buy_price"],
                        "sell_price": t["sell_price"],
                        "total_pnl_amount": 0.0,
                        "total_size": 0,
                        "total_cost": 0.0,
                        "reasons": [],
                    }
                s = stock_summary[c]
                s["sell_date"] = t["sell_date"]
                s["sell_price"] = t["sell_price"]
                s["total_pnl_amount"] += t["pnl_amount"]
                s["total_size"] += t["size"]
                s["total_cost"] += t["size"] * t["buy_price"] * (1 + report.get("_commission", 0.0003))
                if t["reason"] not in s["reasons"]:
                    s["reasons"].append(t["reason"])

            sorted_stocks = sorted(stock_summary.values(),
                                   key=lambda x: x["total_pnl_amount"], reverse=True)
            _out(f"\n  --- 交易明细 (共 {len(sorted_stocks)} 只股票) ---")
            for s in sorted_stocks:
                total_pct = (s["total_pnl_amount"] / s["total_cost"] * 100) if s["total_cost"] > 0 else 0
                bd = s["buy_date"].strftime("%Y-%m-%d") if hasattr(s["buy_date"], "strftime") else str(s["buy_date"])
                sd = s["sell_date"].strftime("%Y-%m-%d") if hasattr(s["sell_date"], "strftime") else str(s["sell_date"])
                _out(f"  {s['code']}  {bd}→{sd}  "
                     f"{s['buy_price']:.2f}→{s['sell_price']:.2f}  "
                     f"{total_pct:+.2f}%({s['total_pnl_amount']:+,.0f})  "
                     f"{'+'.join(s['reasons'])}")

        _out(f"{'=' * 55}")

        if log_file and not log_file.closed:
            log_file.close()
