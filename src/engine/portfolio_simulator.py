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
        "mid_yang_triggered",
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


class PortfolioSimulator:
    """组合级日频模拟引擎"""

    def __init__(self, all_signals, trading_days,
                 initial_cash=1_000_000, max_positions=10,
                 per_position_cash=100_000, commission=0.0003,
                 stock_type="main", t_plus_n=3, log_dir="logs"):
        self._all_signals = all_signals
        self._trading_days = trading_days
        self._initial_cash = initial_cash
        self._max_positions = max_positions
        self._per_position_cash = per_position_cash
        self._commission = commission
        self._stock_type = stock_type
        self._t_plus_n = t_plus_n

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
            self._log(f"  交易日历: {first.strftime('%Y-%m-%d')} ~ {last.strftime('%Y-%m-%d')}  "
                      f"共{len(self._trading_days)}天  [{year_info}]")
            self._log(f"  日志文件: {self._log_path}")

        # 构建交易日 → 序号映射（用于计算持仓交易日数）
        self._td_index = {td: i for i, td in enumerate(self._trading_days)}

        for td in self._trading_days:
            # 每月首个交易日更新观察池
            year_month = (td.year, td.month)
            if year_month != self._last_month:
                self._update_watchlist(td)
                self._last_month = year_month
                self._log(f"  [{td.strftime('%Y-%m-%d')}] 月度更新观察池: {len(self._watchlist)} 只")

            # 每日卖出检查
            self._check_exits(td)

            # 每日买入检查
            self._check_entries(td)

            # 记录每日权益
            equity = self._calc_equity(td)
            self._equity_curve.append(equity)

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

        candidates = []
        for code in self._watchlist:
            if code in self._positions:
                continue
            sig = self._all_signals[code]
            idx = self._find_bar_index(code, date)
            if idx is None or idx < 1:
                continue
            try:
                gc_ok = sig["recent_gc"][idx]
                b1_ok = sig["b1"][idx]
                if gc_ok and b1_ok:
                    score = sig["shrink_score"][idx]
                    if np.isnan(score):
                        score = 1.0
                    candidates.append((code, score, idx, sig))
            except (IndexError, TypeError):
                continue

        if not candidates:
            return

        # 按 shrink_score 升序（越小=越缩量）
        candidates.sort(key=lambda x: x[1])
        code, score, idx, sig = candidates[0]

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
        self._positions[code] = pos
        self._cash -= total_cost

        self._log(f"  [{date.strftime('%Y-%m-%d')}] 买入 {code}  "
                  f"价格={price:.2f}  数量={shares}  止损={sl:.2f}  "
                  f"缩量={score:.3f}")

    def _check_exits(self, date):
        """检查所有持仓的卖出条件"""
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

            pct_gain = (price - pos.buy_price) / pos.buy_price * 100

            # 1. 止损
            if price <= pos.stop_loss:
                cur_idx = self._td_index.get(date)
                if cur_idx is not None:
                    self._cooldown[code] = cur_idx
                self._sell_position(code, pos, price, date, "止损")
                to_remove.append(code)
                continue

            # 2. T+N 没涨清仓
            if days_held >= self._t_plus_n and price <= pos.buy_price:
                cur_idx = self._td_index.get(date)
                if cur_idx is not None:
                    self._cooldown[code] = cur_idx
                self._sell_position(code, pos, price, date, f"T+{days_held}清仓")
                to_remove.append(code)
                continue

            # 3. 盈利100%清仓
            if pct_gain >= 100:
                cur_idx = self._td_index.get(date)
                if cur_idx is not None:
                    self._cooldown[code] = cur_idx
                self._sell_position(code, pos, price, date, "盈利100%清仓")
                to_remove.append(code)
                continue

            # 4. 半仓持股模式
            if pos.hold_until_below_white:
                if pct_gain <= 20:
                    # 盈利20%以内：盈转亏清仓
                    if price <= pos.buy_price:
                        cur_idx = self._td_index.get(date)
                        if cur_idx is not None:
                            self._cooldown[code] = cur_idx
                        self._sell_position(code, pos, price, date, "半仓盈转亏清仓")
                        to_remove.append(code)
                else:
                    # 盈利>20%：跌破白线清仓
                    if price < white_val:
                        cur_idx = self._td_index.get(date)
                        if cur_idx is not None:
                            self._cooldown[code] = cur_idx
                        self._sell_position(code, pos, price, date, "半仓跌破白线")
                        to_remove.append(code)
                continue

            # 5. 涨停卖1/2（中阳未触发时才触发）
            if not pos.mid_yang_triggered and price >= high * 0.995:
                sell_size = max(1, pos.size // 2)
                if sell_size < pos.size:
                    self._sell_partial(code, pos, sell_size, price, date, "涨停卖半")
                    if pos.size <= pos.initial_size // 2:
                        pos.hold_until_below_white = True
                continue

            # 6. 中阳卖1/3
            mid_yang = 10 if self._stock_type == "tech" else 5
            if pct_gain >= mid_yang:
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
        pnl = (price - pos.buy_price) / pos.buy_price * 100
        self._cash += proceeds
        self._trade_list.append({
            "code": code,
            "buy_date": pos.buy_date,
            "sell_date": date,
            "buy_price": pos.buy_price,
            "sell_price": price,
            "size": pos.size + (pos.initial_size - pos.size),  # 记录原始买入量
            "pnl_pct": pnl,
            "reason": reason,
        })
        self._log(f"  [{date.strftime('%Y-%m-%d')}] 卖出 {code}  "
                  f"价格={price:.2f}  收益={pnl:+.2f}%  {reason}")

    def _sell_partial(self, code, pos, sell_size, price, date, reason):
        """部分卖出"""
        proceeds = sell_size * price * (1 - self._commission)
        self._cash += proceeds
        pos.size -= sell_size
        self._log(f"  [{date.strftime('%Y-%m-%d')}] 卖出 {code}  "
                  f"部分 {sell_size}股  价格={price:.2f}  {reason}")

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

        # 交易统计
        total_trades = len(self._trade_list)
        won = sum(1 for t in self._trade_list if t["pnl_pct"] > 0)
        lost = sum(1 for t in self._trade_list if t["pnl_pct"] <= 0)

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
        """打印回测报告（同时写入日志文件）"""
        def _out(msg):
            print(msg)
            if log_file and not log_file.closed:
                log_file.write(msg + "\n")
                log_file.flush()

        _out(f"\n{'=' * 55}")
        _out(f"          组合回测报告")
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

        _out(f"  总交易次数:  {report['total_trades']:>12}")
        _out(f"  盈利次数:    {report['won']:>12}")
        _out(f"  亏损次数:    {report['lost']:>12}")
        won = report["won"]
        total = won + report["lost"]
        if total > 0:
            _out(f"  胜率:        {won / total * 100:>11.2f}%")

        # 交易明细（最近20笔）
        trades = report["trade_list"]
        if trades:
            _out(f"\n  --- 交易明细 (共 {len(trades)} 笔) ---")
            for t in trades[-20:]:
                bd = t["buy_date"].strftime("%Y-%m-%d") if hasattr(t["buy_date"], "strftime") else str(t["buy_date"])
                sd = t["sell_date"].strftime("%Y-%m-%d") if hasattr(t["sell_date"], "strftime") else str(t["sell_date"])
                _out(f"  {t['code']}  {bd}→{sd}  "
                     f"{t['buy_price']:.2f}→{t['sell_price']:.2f}  "
                     f"{t['pnl_pct']:+.2f}%  {t['reason']}")

        _out(f"{'=' * 55}")

        if log_file and not log_file.closed:
            log_file.close()
