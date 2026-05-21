"""N型B1 组合级回测模拟器

交易规则：
- 总资金100万，单只最多10万，最多持仓10只
- 信号日T → T+1开盘价买入
- 六级退出（优先级从高到低）：
  1. 止损：跌破止损价
  2. 跌破黄线清仓
  3. T+N天不涨清仓
  4. 盈利100%后连跌2天清仓
  5. 半仓持股模式（跌破白线/黄线/成本清仓）
  6. 动量持股（连续3天止盈后跟踪下跌清仓）
- 按缩量评分升序取最优1只买入
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
        "mid_yang_triggered", "partial_proceeds", "partial_sold",
        "momentum_hold", "consecutive_tp_days", "consecutive_down_days",
        "profit_100pct", "profit_100pct_down_days",
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
        self.partial_proceeds = 0.0
        self.partial_sold = False
        self.momentum_hold = False
        self.consecutive_tp_days = 0
        self.consecutive_down_days = 0
        self.profit_100pct = False
        self.profit_100pct_down_days = 0


class NxingB1Simulator:
    """N型B1组合级日频模拟引擎"""

    def __init__(self, all_signals, trading_days,
                 initial_cash=1_000_000, max_positions=10,
                 per_position_cash=100_000, commission=0.0003,
                 t_plus_n=3, log_dir="logs",
                 strategy_tag="[NXB1]"):
        self._all_signals = all_signals
        self._trading_days = trading_days
        self._initial_cash = initial_cash
        self._max_positions = max_positions
        self._per_position_cash = per_position_cash
        self._commission = commission
        self._t_plus_n = t_plus_n
        self._tag = strategy_tag

        os.makedirs(log_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._log_path = os.path.join(log_dir, f"nxb1_portfolio_{ts}.log")
        self._log_file = open(self._log_path, "a", encoding="utf-8")

        # 为每只股票预构建日期查找索引
        self._date_indices = {}
        for code, sig in all_signals.items():
            dates = sig["dates"]
            if isinstance(dates, pd.DatetimeIndex):
                arr = dates.values
            else:
                arr = pd.to_datetime(dates).values
            self._date_indices[code] = arr

    def _log(self, msg):
        print(msg)
        self._log_file.write(msg + "\n")
        self._log_file.flush()

    def _find_bar_index(self, code, target_date):
        """找到 target_date 在该股票数据中的 bar 索引"""
        arr = self._date_indices.get(code)
        if arr is None:
            return None
        target = np.datetime64(target_date)
        idx = np.searchsorted(arr, target, side="right") - 1
        if idx < 0:
            return None
        return idx

    def run(self):
        """执行组合级模拟"""
        self._cash = self._initial_cash
        self._positions = {}
        self._equity_curve = []
        self._trade_list = []
        self._pending_buys = []
        self._cooldown = {}

        # 构建交易日 → 序号映射
        self._td_index = {td: i for i, td in enumerate(self._trading_days)}

        if len(self._trading_days) > 0:
            first = self._trading_days[0]
            last = self._trading_days[-1]
            years = pd.Series(self._trading_days.year).value_counts().sort_index()
            year_info = "  ".join(f"{y}年:{c}天" for y, c in years.items())
            self._log(f"  {self._tag} 交易日历: {first.strftime('%Y-%m-%d')} ~ {last.strftime('%Y-%m-%d')}  "
                      f"共{len(self._trading_days)}天  [{year_info}]")
            self._log(f"  {self._tag} 日志文件: {self._log_path}")

        for td in self._trading_days:
            # 1. T+1执行待买入（用当日开盘价买入）
            self._execute_pending_buys(td)
            # 2. 检查退出条件
            self._check_exits(td)
            # 3. 扫描新信号入队（最后一天只卖不买）
            if td != self._trading_days[-1]:
                self._scan_signals(td)
            # 4. 记录权益
            equity = self._calc_equity(td)
            self._equity_curve.append(equity)

        # 模拟结束：强制清仓
        if self._positions:
            last_date = self._trading_days[-1]
            for code in list(self._positions.keys()):
                pos = self._positions[code]
                idx = self._find_bar_index(code, last_date)
                sig = self._all_signals.get(code)
                price = float(sig["close"][idx]) if idx is not None and sig is not None else pos.buy_price
                self._sell_position(code, pos, price, last_date, "模拟结束清仓")

    def _scan_signals(self, date):
        """扫描当日N型B1信号，加入待买入队列"""
        if len(self._positions) >= self._max_positions:
            return
        if self._cash < self._per_position_cash * 0.5:
            return

        cur_idx = self._td_index.get(date, 0)
        candidates = []
        for code, sig in self._all_signals.items():
            if code in self._positions:
                continue
            # 冷却期检查
            if code in self._cooldown:
                if cur_idx - self._cooldown[code] < 10:
                    continue
                else:
                    del self._cooldown[code]
            idx = self._find_bar_index(code, date)
            if idx is None or idx < 1:
                continue
            try:
                b1_ok = sig["b1"][idx]
                veo = sig.get("vol_expand_ok", np.ones(idx + 1, dtype=bool))[idx]
                no_hvb = sig.get("no_huge_vol_bearish", np.ones(idx + 1, dtype=bool))[idx]
                if not (b1_ok and veo and no_hvb):
                    continue
                score = sig["shrink_score"][idx]
                if np.isnan(score):
                    score = 1.0
                candidates.append((code, score, idx, sig))
            except (IndexError, TypeError):
                continue

        if not candidates:
            return

        # 按缩量评分升序排序，取最优1只
        candidates.sort(key=lambda x: x[1])
        code, score, idx, sig = candidates[0]

        # 加入待买入队列（T+1开盘买入）
        self._pending_buys.append((code, score, date, idx, sig))

    def _execute_pending_buys(self, date):
        """T+1开盘价买入"""
        if not self._pending_buys:
            return

        pending = self._pending_buys
        self._pending_buys = []

        for code, score, signal_date, sig_idx, sig in pending:
            if code in self._positions:
                continue
            if len(self._positions) >= self._max_positions:
                continue
            if self._cash < self._per_position_cash * 0.5:
                continue

            idx = self._find_bar_index(code, date)
            if idx is None or idx < 1:
                continue
            try:
                price = sig["open"][idx]
            except (IndexError, TypeError):
                continue
            if price <= 0:
                continue

            # 计算可买股数（100股整手）
            buy_cost = price * (1 + self._commission)
            shares = int(self._per_position_cash / buy_cost / 100) * 100
            if shares <= 0:
                continue

            total_cost = shares * price * (1 + self._commission)
            if total_cost > self._cash:
                shares = int(self._cash / buy_cost / 100) * 100
                if shares <= 0:
                    continue
                total_cost = shares * price * (1 + self._commission)

            # 止损价（与PortfolioSimulator一致）
            white_val = sig["white"][idx]
            yellow_val = sig["yellow"][idx]
            low_val = sig["low"][idx]
            wy_diff = (white_val - yellow_val) / yellow_val
            if wy_diff < 0.05:
                sl = yellow_val * 0.99
            elif price >= white_val:
                sl = low_val * 0.99
            else:
                sl = yellow_val * 0.99
            if sl > price:
                sl = price * 0.95

            pos = Position(
                code=code, buy_date=date, buy_price=price,
                buy_low=low_val, white_at_buy=white_val,
                yellow_at_buy=yellow_val, stop_loss=sl, size=shares,
            )
            self._positions[code] = pos
            self._cash -= total_cost

            self._log(f"  [{date.strftime('%Y-%m-%d')}] {self._tag} 买入 {code}  "
                      f"价格={price:.2f}  数量={shares}  止损={sl:.2f}  "
                      f"缩量={score:.3f}  "
                      f"持仓={len(self._positions)}/{self._max_positions}  "
                      f"现金={self._cash:,.0f}")

    def _check_exits(self, date):
        """检查所有持仓的六级卖出条件"""
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

            # 持仓交易日数
            buy_idx = self._td_index.get(pos.buy_date)
            cur_idx = self._td_index.get(date)
            if buy_idx is not None and cur_idx is not None:
                bars_held = cur_idx - buy_idx
            else:
                bars_held = (date - pos.buy_date).days
            days_held = bars_held

            # 摊薄成本价
            total_cost = pos.initial_size * pos.buy_price * (1 + self._commission)
            remaining_cost = total_cost - pos.partial_proceeds
            avg_cost = remaining_cost / pos.size if pos.size > 0 else pos.buy_price

            pct_gain = (price - pos.buy_price) / pos.buy_price * 100
            real_gain = (price - avg_cost) / avg_cost * 100

            # 1. 止损
            if price <= pos.stop_loss:
                if cur_idx is not None:
                    self._cooldown[code] = cur_idx
                self._sell_position(code, pos, price, date, "止损")
                to_remove.append(code)
                continue

            # 巨量阴线清仓（最高优先级）
            hvb = sig.get("huge_vol_bearish", np.zeros(idx + 1, dtype=bool))
            if idx < len(hvb) and hvb[idx]:
                self._sell_position(code, pos, price, date, "巨量阴线清仓")
                to_remove.append(code)
                continue

            # 2. 跌破黄线清仓
            if price < yellow_val and not (pos.buy_price < pos.yellow_at_buy and not pos.partial_sold):
                if cur_idx is not None:
                    self._cooldown[code] = cur_idx
                self._sell_position(code, pos, price, date, "跌破黄线清仓")
                to_remove.append(code)
                continue

            # 3. T+N 不涨清仓
            if days_held >= self._t_plus_n and price <= avg_cost:
                if cur_idx is not None:
                    self._cooldown[code] = cur_idx
                self._sell_position(code, pos, price, date, f"T+{days_held}清仓")
                to_remove.append(code)
                continue

            # 4. 盈利100%后连跌2天清仓
            total_cost_full = pos.initial_size * pos.buy_price * (1 + self._commission)
            current_value = pos.size * price * (1 - self._commission)
            total_proceeds = pos.partial_proceeds + current_value
            total_pnl_pct = (total_proceeds - total_cost_full) / total_cost_full * 100
            if pos.profit_100pct:
                prev_close = sig["close"][idx - 1] if idx >= 1 else 0
                if prev_close > 0 and price < prev_close:
                    pos.profit_100pct_down_days += 1
                else:
                    pos.profit_100pct_down_days = 0
                if pos.profit_100pct_down_days >= 2:
                    if cur_idx is not None:
                        self._cooldown[code] = cur_idx
                    self._sell_position(code, pos, price, date, "盈利100%连跌清仓")
                    to_remove.append(code)
                    continue
                continue
            if total_pnl_pct >= 100:
                pos.profit_100pct = True
                continue

            # 4.5 止盈放飞后跟踪
            if pos.partial_sold:
                if price >= white_val and idx >= 5:
                    open_price = sig["open"][idx]
                    vol = sig["volume"][idx]
                    is_bearish = price < open_price
                    vol_ma20 = np.mean(sig["volume"][max(0, idx - 20):idx])
                    is_big_vol = vol > vol_ma20 * 1.5 if vol_ma20 > 0 else False
                    recent_gain = (sig["close"][idx] - sig["close"][idx - 5]) / sig["close"][idx - 5] * 100
                    has_accel = recent_gain > 15
                    if is_bearish and is_big_vol and has_accel:
                        self._sell_position(code, pos, price, date, "止盈放飞后放量阴线清仓")
                        to_remove.append(code)
                        continue
                continue

            # 4.8 部分卖出后白线/黄线跟踪止损
            if pos.partial_sold and not pos.hold_until_below_white:
                sold = False
                wy_gap_pct = abs(white_val - yellow_val) / yellow_val * 100 if yellow_val > 0 else 0
                if real_gain <= 20:
                    if wy_gap_pct <= 10:
                        if price <= avg_cost or price < yellow_val:
                            if cur_idx is not None:
                                self._cooldown[code] = cur_idx
                            reason = "部分卖出后跌破黄线" if price < yellow_val else "部分卖出后跌破成本"
                            self._sell_position(code, pos, price, date, reason)
                            to_remove.append(code)
                            sold = True
                    else:
                        if price <= avg_cost or price < white_val:
                            if cur_idx is not None:
                                self._cooldown[code] = cur_idx
                            reason = "部分卖出后跌破白线" if price < white_val else "部分卖出后跌破成本"
                            self._sell_position(code, pos, price, date, reason)
                            to_remove.append(code)
                            sold = True
                else:
                    if wy_gap_pct <= 10:
                        if price < yellow_val:
                            if cur_idx is not None:
                                self._cooldown[code] = cur_idx
                            self._sell_position(code, pos, price, date, "部分卖出后跌破黄线")
                            to_remove.append(code)
                            sold = True
                    else:
                        if price < white_val:
                            if cur_idx is not None:
                                self._cooldown[code] = cur_idx
                            self._sell_position(code, pos, price, date, "部分卖出后跌破白线")
                            to_remove.append(code)
                            sold = True
                if sold:
                    continue

            # 5. 半仓持股模式
            if pos.hold_until_below_white:
                sold = False
                wy_gap_pct = abs(white_val - yellow_val) / yellow_val * 100 if yellow_val > 0 else 0
                if real_gain <= 20:
                    if wy_gap_pct <= 10:
                        if price <= avg_cost or price < yellow_val:
                            if cur_idx is not None:
                                self._cooldown[code] = cur_idx
                            reason = "半仓跌破黄线" if price < yellow_val else "半仓盈转亏清仓"
                            self._sell_position(code, pos, price, date, reason)
                            to_remove.append(code)
                            sold = True
                    else:
                        if price <= avg_cost or price < white_val:
                            if cur_idx is not None:
                                self._cooldown[code] = cur_idx
                            reason = "半仓跌破白线" if price < white_val else "半仓盈转亏清仓"
                            self._sell_position(code, pos, price, date, reason)
                            to_remove.append(code)
                            sold = True
                else:
                    wy_gap_pct = abs(white_val - yellow_val) / yellow_val * 100 if yellow_val > 0 else 0
                    if wy_gap_pct <= 10:
                        if price < yellow_val:
                            if cur_idx is not None:
                                self._cooldown[code] = cur_idx
                            self._sell_position(code, pos, price, date, "半仓跌破黄线")
                            to_remove.append(code)
                            sold = True
                    else:
                        if price < white_val:
                            if cur_idx is not None:
                                self._cooldown[code] = cur_idx
                            self._sell_position(code, pos, price, date, "半仓跌破白线")
                            to_remove.append(code)
                            sold = True
                if sold:
                    continue

            # 6. 动量持股逻辑
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
                        if price < prev_close:
                            pos.consecutive_down_days += 1
                        else:
                            pos.consecutive_down_days = 0
                        drop_pct = (prev_close - price) / prev_close * 100
                        drop_threshold = 14.0 if _is_tech else 7.0
                        if drop_pct > drop_threshold:
                            if cur_idx is not None:
                                self._cooldown[code] = cur_idx
                            self._sell_position(code, pos, price, date, "动量结束清仓")
                            to_remove.append(code)
                            continue
                        continue

                    if tp_met:
                        pos.consecutive_tp_days += 1
                    else:
                        pos.consecutive_tp_days = 0

                    if pos.consecutive_tp_days >= 3:
                        pos.momentum_hold = True
                        continue

                    # 6.1 涨停卖1/2
                    if high >= limit_up_price:
                        sell_size = max(1, pos.size // 2)
                        if sell_size < pos.size:
                            self._sell_partial(code, pos, sell_size, price, date, "涨停卖半")
                            if pos.size <= pos.initial_size // 2:
                                pos.hold_until_below_white = True
                        continue

                    # 6.2 中阳卖1/3
                    if not pos.mid_yang_triggered:
                        if (daily_up and pct_gain >= mid_yang) or pct_gain >= 7:
                            sell_size = max(1, pos.size // 3)
                            if sell_size < pos.size:
                                self._sell_partial(code, pos, sell_size, price, date, "中阳卖1/3")
                                pos.mid_yang_triggered = True
                                if pos.size <= pos.initial_size // 2:
                                    pos.hold_until_below_white = True

        for code in to_remove:
            del self._positions[code]

    def _sell_position(self, code, pos, price, date, reason):
        """全部卖出"""
        proceeds = pos.size * price * (1 - self._commission)
        total_proceeds = pos.partial_proceeds + proceeds
        total_cost = pos.initial_size * pos.buy_price * (1 + self._commission)
        pnl = (total_proceeds - total_cost) / total_cost * 100
        pnl_amount = total_proceeds - total_cost
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
        self._log(f"  [{date.strftime('%Y-%m-%d')}] {self._tag} 清仓 {code}  "
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
        self._log(f"  [{date.strftime('%Y-%m-%d')}] {self._tag} 卖出 {code}  "
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

        peak = np.maximum.accumulate(equity)
        drawdown = (equity - peak) / peak * 100
        max_drawdown = float(np.min(drawdown)) if len(drawdown) > 0 else 0.0

        if len(equity) > 1:
            daily_returns = np.diff(equity) / equity[:-1]
            if np.std(daily_returns) > 0:
                sharpe = float(np.mean(daily_returns) / np.std(daily_returns) * np.sqrt(252))
            else:
                sharpe = None
        else:
            sharpe = None

        closed_trades = [t for t in self._trade_list if not t.get("partial")]
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
    def print_report(report, log_file=None, strategy_tag="[NXB1]"):
        """打印回测报告"""
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
