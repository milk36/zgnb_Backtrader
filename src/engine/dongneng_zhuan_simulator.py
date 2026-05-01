"""动能+砖 组合级回测模拟器

交易规则：
- 总资金10万，单只最多5万，最多持仓2只
- 信号日T检测 → T+1分钟线确认买入（无分钟线时降级为开盘价）
- 五级退出（优先级从高到低）：
  1. 止损：分钟级监控跌破买入价2%
  2. 涨停清仓 / 累计盈利≥10%：全部清仓
  3. 涨幅2%：卖出1/4仓位（每日最多2次，涨停则清仓）
  4. 2日不拉升（涨幅<2%）清仓
  5. 脱离成本5%以上，持仓最多4-6天
- 按"下大上小"排名取前N只买入
"""

import os
from datetime import datetime

import numpy as np
import pandas as pd


class Position:
    """跟踪单只股票持仓"""

    __slots__ = (
        "code", "buy_date", "buy_price", "buy_low",
        "stop_loss", "size", "initial_size", "confirmed_minute",
    )

    def __init__(self, code, buy_date, buy_price, buy_low, stop_loss, size):
        self.code = code
        self.buy_date = buy_date
        self.buy_price = buy_price
        self.buy_low = buy_low
        self.stop_loss = stop_loss
        self.size = size
        self.initial_size = size
        self.confirmed_minute = False


class DongnengZhuanSimulator:
    """动能+砖组合级日频模拟引擎"""

    def __init__(self, all_signals, trading_days,
                 initial_cash=100_000, max_positions=2,
                 per_position_cash=50_000, commission=0.0003,
                 t_plus_n=2, max_hold_days=5, profit_pct=5.0,
                 stop_loss_pct=4.0,
                 log_dir="logs",
                 minute_feed=None, minute_confirm_bars=3,
                 minute_entry_enabled=True, minute_exit_enabled=True):
        self._all_signals = all_signals
        self._trading_days = trading_days
        self._initial_cash = initial_cash
        self._max_positions = max_positions
        self._per_position_cash = per_position_cash
        self._commission = commission
        self._t_plus_n = t_plus_n
        self._max_hold_days = max_hold_days
        self._profit_pct = profit_pct
        self._stop_loss_pct = stop_loss_pct
        self._minute_feed = minute_feed
        self._confirm_bars = max(1, minute_confirm_bars)
        self._minute_entry = minute_entry_enabled and (minute_feed is not None)
        self._minute_exit = minute_exit_enabled and (minute_feed is not None)

        # 日志
        os.makedirs(log_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._log_path = os.path.join(log_dir, f"dnzh_portfolio_{ts}.log")
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
        self._pending_buys = []        # T+1 待买入队列: [(code, rank_score, signal_date)]
        self._equity_curve = []
        self._trade_list = []
        self._cooldown = {}             # code -> td_index

        # 交易日→序号映射
        self._td_index = {td: i for i, td in enumerate(self._trading_days)}

        if len(self._trading_days) > 0:
            first = self._trading_days[0]
            last = self._trading_days[-1]
            years = pd.Series(self._trading_days.year).value_counts().sort_index()
            year_info = "  ".join(f"{y}年:{c}天" for y, c in years.items())
            self._log(f"  [动能砖] 交易日历: {first.strftime('%Y-%m-%d')} ~ "
                      f"{last.strftime('%Y-%m-%d')}  共{len(self._trading_days)}天  [{year_info}]")
            self._log(f"  [动能砖] 日志文件: {self._log_path}")
            self._log(f"  [动能砖] 资金={self._initial_cash:,.0f}  "
                      f"每只={self._per_position_cash:,.0f}  "
                      f"最多{self._max_positions}只  "
                      f"T+{self._t_plus_n}  最大持仓{self._max_hold_days}天  "
                      f"止损-{self._stop_loss_pct}%")
            mode_entry = "分钟确认" if self._minute_entry else "日线开盘"
            mode_exit = "分钟监控" if self._minute_exit else "日线检查"
            self._log(f"  [动能砖] 入场={mode_entry}  出场={mode_exit}  "
                      f"确认bar数={self._confirm_bars}")

        last_td = self._trading_days[-1] if len(self._trading_days) > 0 else None
        for td in self._trading_days:
            # 1. 执行T+1待买入（最后一天不执行，避免买入后强制清仓产生T+0）
            if td != last_td:
                self._execute_pending_buys(td)

            # 2. 卖出检查
            self._check_exits(td)

            # 3. 扫描信号 → 加入明日待买入队列（最后一天跳过，无次日可执行）
            if td != last_td:
                self._scan_signals(td)

            # 4. 记录权益
            equity = self._calc_equity(td)
            self._equity_curve.append(equity)

            # 5. 清理分钟线缓存
            if self._minute_feed is not None:
                self._minute_feed.clear_cache()

        # 模拟结束：强制清仓所有未平仓持仓
        if self._positions and len(self._trading_days) > 0:
            last_date = self._trading_days[-1]
            for code in list(self._positions.keys()):
                pos = self._positions[code]
                sig = self._all_signals.get(code)
                idx = self._find_bar_index(code, last_date)
                if sig is not None and idx is not None:
                    try:
                        price = float(sig["close"][idx])
                    except (IndexError, TypeError):
                        price = pos.buy_price
                else:
                    price = pos.buy_price
                self._sell_position(code, pos, price, last_date, "模拟结束清仓")

    def _execute_pending_buys(self, date):
        """执行T+1买入，支持5分钟线确认入场"""
        if not self._pending_buys:
            return

        pending = sorted(self._pending_buys, key=lambda x: x[1], reverse=True)
        self._pending_buys = []

        cur_idx = self._td_index.get(date, 0)

        for code, rank_score, _sig_date in pending:
            if len(self._positions) >= self._max_positions:
                break
            if self._cash < self._per_position_cash * 0.5:
                break
            if code in self._positions:
                continue
            if code in self._cooldown:
                if cur_idx - self._cooldown[code] < 5:
                    continue
                else:
                    del self._cooldown[code]

            sig = self._all_signals.get(code)
            if sig is None:
                continue
            idx = self._find_bar_index(code, date)
            if idx is None:
                continue

            daily_open = sig["open"][idx]
            if daily_open <= 0:
                continue

            buy_price = None
            buy_reason = ""
            confirmed = False

            # --- 分钟线确认入场 ---
            if self._minute_entry:
                bars = self._minute_feed.get_minute_bars(code, date)
                if bars is not None and len(bars) >= self._confirm_bars:
                    first_n = bars.head(self._confirm_bars)

                    for i in range(len(first_n)):
                        bar = first_n.iloc[i]
                        bar_close = float(bar["close"])
                        bar_open = float(bar["open"])

                        # 确认条件：阳线 且 收盘>=日线开盘价
                        if bar_close > bar_open and bar_close >= daily_open:
                            buy_price = bar_close
                            buy_reason = f"分钟确认(bar{i+1})"
                            confirmed = True
                            break

                    if not confirmed:
                        self._log(f"  [{date.strftime('%Y-%m-%d')}] [动能砖] "
                                  f"放弃 {code}  开盘急跌未确认  "
                                  f"排名={rank_score:.2f}")
                        continue

            # --- 降级：日线开盘价 ---
            if buy_price is None:
                buy_price = daily_open
                buy_reason = "开盘"

            # 计算可买股数
            buy_cost = buy_price * (1 + self._commission)
            shares = int(self._per_position_cash / buy_cost / 100) * 100
            if shares <= 0:
                continue

            total_cost = shares * buy_price * (1 + self._commission)
            if total_cost > self._cash:
                shares = int(self._cash / buy_cost / 100) * 100
                if shares <= 0:
                    continue
                total_cost = shares * buy_price * (1 + self._commission)

            low_val = sig["low"][idx]
            stop_loss = round(buy_price * (1 - self._stop_loss_pct / 100), 2)
            pos = Position(
                code=code, buy_date=date, buy_price=buy_price,
                buy_low=low_val, stop_loss=stop_loss, size=shares)
            pos.confirmed_minute = confirmed
            self._positions[code] = pos
            self._cash -= total_cost

            self._log(f"  [{date.strftime('%Y-%m-%d')}] [动能砖] 买入 {code}  "
                      f"价格={buy_price:.2f}({buy_reason})  数量={shares}  "
                      f"止损={stop_loss:.2f}(-{self._stop_loss_pct}%)  排名={rank_score:.2f}  "
                      f"持仓={len(self._positions)}/{self._max_positions}  "
                      f"现金={self._cash:,.0f}")

    def _scan_signals(self, date):
        """扫描全市场信号，加入明日待买入队列"""
        if len(self._positions) >= self._max_positions:
            return
        if self._cash < self._per_position_cash * 0.5:
            return

        cur_idx = self._td_index.get(date, 0)
        candidates = []

        for code, sig in self._all_signals.items():
            if code in self._positions:
                continue
            if code in self._cooldown:
                if cur_idx - self._cooldown[code] < 5:
                    continue
                else:
                    del self._cooldown[code]
            # 检查是否已经在待买入队列中
            if any(c == code for c, _, _ in self._pending_buys):
                continue

            idx = self._find_bar_index(code, date)
            if idx is None or idx < 1:
                continue
            try:
                if not sig["any_ok"][idx]:
                    continue
                score = sig["rank_score"][idx]
                if np.isnan(score) or score <= 0:
                    continue
                candidates.append((code, score))
            except (IndexError, TypeError):
                continue

        # 按排名分数降序，取前 max_positions 个加入待买入
        candidates.sort(key=lambda x: x[1], reverse=True)
        available_slots = self._max_positions - len(self._positions)
        for code, score in candidates[:available_slots]:
            self._pending_buys.append((code, score, date))

    def _check_exits(self, date):
        """检查所有持仓的卖出条件

        优先级：止损 → 涨停清仓 → 2%涨幅卖1/4 → T+N不拉升 → 盈利止盈
        """
        to_remove = []
        cur_idx = self._td_index.get(date, 0)

        for code, pos in list(self._positions.items()):
            # T+1: 当日买入的股票不能当日卖出
            if pos.buy_date == date:
                continue

            partial_count_today = 0  # 每只股票每天最多2次部分卖出

            sig = self._all_signals.get(code)
            if sig is None:
                continue
            idx = self._find_bar_index(code, date)
            if idx is None:
                continue
            try:
                daily_close = sig["close"][idx]
                daily_high = sig["high"][idx]
                prev_close = sig["close"][idx - 1] if idx >= 1 else 0
            except (IndexError, TypeError):
                continue
            if daily_close <= 0:
                continue

            buy_idx = self._td_index.get(pos.buy_date)
            if buy_idx is not None and cur_idx is not None:
                days_held = cur_idx - buy_idx
            else:
                days_held = (date - pos.buy_date).days

            pct_gain = (daily_close - pos.buy_price) / pos.buy_price * 100

            is_tech = code[:2] in ("30", "68")
            limit_pct = 1.20 if is_tech else 1.10
            limit_up_price = round(prev_close * limit_pct, 2) if prev_close > 0 else 0

            # --- 分钟级监控 ---
            minute_exited = False
            if self._minute_exit:
                bars = self._minute_feed.get_minute_bars(code, date)
                if bars is not None and len(bars) > 0:
                    for i in range(len(bars)):
                        bar = bars.iloc[i]
                        bar_low = float(bar["low"])
                        bar_high = float(bar["high"])
                        bar_close = float(bar["close"])
                        bar_pct = (bar_close - pos.buy_price) / pos.buy_price * 100

                        # 1. 止损
                        if bar_low <= pos.stop_loss:
                            sell_price = pos.stop_loss
                            self._cooldown[code] = cur_idx
                            self._sell_position(code, pos, sell_price, date,
                                                "止损(分钟)")
                            to_remove.append(code)
                            minute_exited = True
                            break

                        # 2. 涨停清仓 / 累计盈利≥10%清仓
                        if (limit_up_price > 0 and bar_high >= limit_up_price) or bar_pct >= 10.0:
                            reason = "涨停清仓" if (limit_up_price > 0 and bar_high >= limit_up_price) else f"盈利{bar_pct:.1f}%清仓"
                            self._cooldown[code] = cur_idx
                            self._sell_position(code, pos, bar_close, date,
                                                f"{reason}(分钟)")
                            to_remove.append(code)
                            minute_exited = True
                            break

                        # 3. 涨幅2%卖1/4（每天最多2次）
                        if bar_pct >= 2.0 and partial_count_today < 2:
                            self._partial_sell(code, pos, bar_close, date)
                            partial_count_today += 1
                            if pos.size <= 0:
                                to_remove.append(code)
                                minute_exited = True
                                break

            if minute_exited:
                continue

            # --- 日线级别检查 ---

            # 1. 止损（日线降级）
            if daily_close <= pos.stop_loss:
                self._cooldown[code] = cur_idx
                self._sell_position(code, pos, daily_close, date, "止损")
                to_remove.append(code)
                continue

            # 2. 涨停清仓 / 累计盈利≥10%清仓（日线降级）
            if (limit_up_price > 0 and daily_high >= limit_up_price) or pct_gain >= 10.0:
                reason = "涨停清仓" if (limit_up_price > 0 and daily_high >= limit_up_price) else f"盈利{pct_gain:.1f}%清仓"
                self._cooldown[code] = cur_idx
                self._sell_position(code, pos, daily_close, date, reason)
                to_remove.append(code)
                continue

            # 3. 涨幅2%卖1/4（每天最多2次）
            if pct_gain >= 2.0 and partial_count_today < 2:
                self._partial_sell(code, pos, daily_close, date)
                partial_count_today += 1
                if pos.size <= 0:
                    to_remove.append(code)
                    continue

            # 4. T+N不拉升清仓（涨幅不足2%视为不拉升）
            if days_held >= self._t_plus_n and pct_gain < 2.0:
                self._cooldown[code] = cur_idx
                self._sell_position(code, pos, daily_close, date,
                                    f"T+{days_held}未拉升清仓")
                to_remove.append(code)
                continue

            # 5. 盈利止盈
            if pct_gain >= self._profit_pct and days_held >= self._max_hold_days:
                self._cooldown[code] = cur_idx
                self._sell_position(code, pos, daily_close, date,
                                    f"盈利{pct_gain:.1f}%持仓{days_held}天止盈")
                to_remove.append(code)
                continue

        for code in set(to_remove):
            if code in self._positions:
                del self._positions[code]

    def _partial_sell(self, code, pos, price, date):
        """卖出1/4仓位（剩余不足时清仓）"""
        sell_size = max(100, pos.size // 4 // 100 * 100)
        if sell_size >= pos.size:
            sell_size = pos.size
        is_clearance = (sell_size == pos.size)

        proceeds = sell_size * price * (1 - self._commission)
        cost_basis = sell_size * pos.buy_price * (1 + self._commission)
        pnl = (proceeds - cost_basis) / cost_basis * 100
        pnl_amount = proceeds - cost_basis
        self._cash += proceeds
        pos.size -= sell_size

        action = "清仓" if is_clearance else "卖1/4"
        self._trade_list.append({
            "code": code,
            "buy_date": pos.buy_date,
            "sell_date": date,
            "buy_price": pos.buy_price,
            "sell_price": price,
            "size": sell_size,
            "pnl_pct": pnl,
            "pnl_amount": pnl_amount,
            "reason": f"涨2%{action}",
            "partial": not is_clearance,
        })
        self._log(f"  [{date.strftime('%Y-%m-%d')}] [动能砖] {action} {code}  "
                  f"价格={price:.2f}  数量={sell_size}  "
                  f"收益={pnl:+.2f}%({pnl_amount:+,.0f})  "
                  f"剩余={pos.size}  "
                  f"现金={self._cash:,.0f}")

    def _sell_position(self, code, pos, price, date, reason):
        """全部卖出"""
        if pos.size <= 0:
            return
        proceeds = pos.size * price * (1 - self._commission)
        total_cost = pos.size * pos.buy_price * (1 + self._commission)
        pnl = (proceeds - total_cost) / total_cost * 100
        pnl_amount = proceeds - total_cost
        self._cash += proceeds
        self._trade_list.append({
            "code": code,
            "buy_date": pos.buy_date,
            "sell_date": date,
            "buy_price": pos.buy_price,
            "sell_price": price,
            "size": pos.size,
            "pnl_pct": pnl,
            "pnl_amount": pnl_amount,
            "reason": reason,
        })
        self._log(f"  [{date.strftime('%Y-%m-%d')}] [动能砖] 清仓 {code}  "
                  f"价格={price:.2f}  买入={pos.buy_price:.2f}  "
                  f"收益={pnl:+.2f}%({pnl_amount:+,.0f})  {reason}  "
                  f"持仓={len(self._positions)-1}/{self._max_positions}  "
                  f"现金={self._cash:,.0f}")

    def _calc_equity(self, date):
        """计算当日总权益"""
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

        # 夏普比率
        if len(equity) > 1:
            daily_returns = np.diff(equity) / equity[:-1]
            if np.std(daily_returns) > 0:
                sharpe = float(np.mean(daily_returns) / np.std(daily_returns) * np.sqrt(252))
            else:
                sharpe = None
        else:
            sharpe = None

        total_trades = len(self._trade_list)

        # 按股票汇总计算胜率
        stock_pnl = {}
        for t in self._trade_list:
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
            "total_trades": total_trades,
            "won": won,
            "lost": lost,
            "trade_list": self._trade_list,
            "trading_days": len(self._trading_days),
        }

    @staticmethod
    def print_report(report, log_file=None):
        """打印回测报告"""
        def _out(msg):
            print(msg)
            if log_file and not log_file.closed:
                log_file.write(msg + "\n")
                log_file.flush()

        _out(f"\n{'=' * 55}")
        _out(f"          [动能砖] 组合回测报告")
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

        _out(f"  总交易笔数:  {report['total_trades']:>12}")
        _out(f"  盈利股票:    {report['won']:>12}")
        _out(f"  亏损股票:    {report['lost']:>12}")
        won = report["won"]
        total = won + report["lost"]
        if total > 0:
            _out(f"  胜率:        {won / total * 100:>11.2f}%")

        trades = report["trade_list"]
        if trades:
            # 按股票汇总：买入价=首次买入价，卖出价=最后卖出价，总收益=各笔之和
            from collections import OrderedDict
            stock_summary = OrderedDict()
            for t in trades:
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
                        "reasons": [],
                    }
                s = stock_summary[c]
                s["sell_date"] = t["sell_date"]
                s["sell_price"] = t["sell_price"]
                s["total_pnl_amount"] += t["pnl_amount"]
                s["total_size"] += t["size"]
                if t["reason"] not in s["reasons"]:
                    s["reasons"].append(t["reason"])

            # 按盈利金额从高到低排序
            sorted_stocks = sorted(stock_summary.values(),
                                   key=lambda x: x["total_pnl_amount"], reverse=True)
            _out(f"\n  --- 交易明细 (共 {len(sorted_stocks)} 只股票) ---")
            for s in sorted_stocks:
                total_cost = s["total_size"] * s["buy_price"] * (1 + 0.0003)
                total_pct = (s["total_pnl_amount"] / total_cost * 100) if total_cost > 0 else 0
                bd = s["buy_date"].strftime("%Y-%m-%d") if hasattr(s["buy_date"], "strftime") else str(s["buy_date"])
                sd = s["sell_date"].strftime("%Y-%m-%d") if hasattr(s["sell_date"], "strftime") else str(s["sell_date"])
                _out(f"  {s['code']}  {bd}→{sd}  "
                     f"{s['buy_price']:.2f}→{s['sell_price']:.2f}  "
                     f"{total_pct:+.2f}%({s['total_pnl_amount']:+,.0f})  "
                     f"{'+'.join(s['reasons'])}")

        _out(f"{'=' * 55}")

        if log_file and not log_file.closed:
            log_file.close()
