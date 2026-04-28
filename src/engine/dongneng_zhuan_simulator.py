"""动能+砖 组合级回测模拟器

交易规则：
- 总资金10万，单只最多5万，最多持仓2只
- 信号日T检测 → T+1开盘价买入
- 四级退出（优先级从高到低）：
  1. 止损：买入K线最低价
  2. 涨停清仓：当日最高触及涨停价即清仓
  3. 2日不拉升（价格<=买入价）清仓
  4. 脱离成本5%以上，持仓最多4-6天
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
        "stop_loss", "size", "initial_size",
    )

    def __init__(self, code, buy_date, buy_price, buy_low, stop_loss, size):
        self.code = code
        self.buy_date = buy_date
        self.buy_price = buy_price
        self.buy_low = buy_low
        self.stop_loss = stop_loss
        self.size = size
        self.initial_size = size


class DongnengZhuanSimulator:
    """动能+砖组合级日频模拟引擎"""

    def __init__(self, all_signals, trading_days,
                 initial_cash=100_000, max_positions=2,
                 per_position_cash=50_000, commission=0.0003,
                 t_plus_n=2, max_hold_days=5, profit_pct=5.0,
                 log_dir="logs"):
        self._all_signals = all_signals
        self._trading_days = trading_days
        self._initial_cash = initial_cash
        self._max_positions = max_positions
        self._per_position_cash = per_position_cash
        self._commission = commission
        self._t_plus_n = t_plus_n
        self._max_hold_days = max_hold_days
        self._profit_pct = profit_pct

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
                      f"T+{self._t_plus_n}  最大持仓{self._max_hold_days}天")

        for td in self._trading_days:
            # 1. 执行T+1待买入（用今日开盘价）
            self._execute_pending_buys(td)

            # 2. 卖出检查
            self._check_exits(td)

            # 3. 扫描信号 → 加入明日待买入队列
            self._scan_signals(td)

            # 4. 记录权益
            equity = self._calc_equity(td)
            self._equity_curve.append(equity)

    def _execute_pending_buys(self, date):
        """执行前一日信号对应的T+1开盘买入"""
        if not self._pending_buys:
            return

        # 按排名分数降序排列
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
            # 止损冷却
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

            # 使用今日开盘价
            price = sig["open"][idx]
            if price <= 0:
                continue

            # 计算可买股数
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

            low_val = sig["low"][idx]
            pos = Position(
                code=code, buy_date=date, buy_price=price,
                buy_low=low_val, stop_loss=low_val, size=shares)
            self._positions[code] = pos
            self._cash -= total_cost

            self._log(f"  [{date.strftime('%Y-%m-%d')}] [动能砖] 买入 {code}  "
                      f"价格={price:.2f}(开盘)  数量={shares}  止损={low_val:.2f}  "
                      f"排名={rank_score:.2f}  "
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
        """检查所有持仓的卖出条件"""
        to_remove = []
        cur_idx = self._td_index.get(date, 0)

        for code, pos in list(self._positions.items()):
            sig = self._all_signals.get(code)
            if sig is None:
                continue
            idx = self._find_bar_index(code, date)
            if idx is None:
                continue
            try:
                price = sig["close"][idx]
            except (IndexError, TypeError):
                continue
            if price <= 0:
                continue

            # 持仓交易日数
            buy_idx = self._td_index.get(pos.buy_date)
            if buy_idx is not None and cur_idx is not None:
                days_held = cur_idx - buy_idx
            else:
                days_held = (date - pos.buy_date).days

            pct_gain = (price - pos.buy_price) / pos.buy_price * 100

            # 1. 止损：买入K线最低价
            if price <= pos.stop_loss:
                self._cooldown[code] = cur_idx
                self._sell_position(code, pos, price, date, "止损")
                to_remove.append(code)
                continue

            # 2. 涨停立即清仓
            if idx >= 1:
                prev_close = sig["close"][idx - 1]
                if prev_close > 0:
                    is_tech = code[:2] in ("30", "68")
                    limit_pct = 1.20 if is_tech else 1.10
                    limit_up_price = round(prev_close * limit_pct, 2)
                    if sig["high"][idx] >= limit_up_price:
                        self._cooldown[code] = cur_idx
                        self._sell_position(code, pos, price, date,
                                            f"涨停清仓(盈利{pct_gain:.1f}%)")
                        to_remove.append(code)
                        continue

            # 3. T+N不拉升清仓
            if days_held >= self._t_plus_n and price <= pos.buy_price:
                self._cooldown[code] = cur_idx
                self._sell_position(code, pos, price, date,
                                    f"T+{days_held}未拉升清仓")
                to_remove.append(code)
                continue

            # 4. 脱离成本 profit_pct% 以上，持仓最多 max_hold_days 天
            if pct_gain >= self._profit_pct and days_held >= self._max_hold_days:
                self._cooldown[code] = cur_idx
                self._sell_position(code, pos, price, date,
                                    f"盈利{pct_gain:.1f}%持仓{days_held}天止盈")
                to_remove.append(code)
                continue

        for code in to_remove:
            del self._positions[code]

    def _sell_position(self, code, pos, price, date, reason):
        """全部卖出"""
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
            "size": pos.initial_size,
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

        _out(f"  总交易次数:  {report['total_trades']:>12}")
        _out(f"  盈利次数:    {report['won']:>12}")
        _out(f"  亏损次数:    {report['lost']:>12}")
        won = report["won"]
        total = won + report["lost"]
        if total > 0:
            _out(f"  胜率:        {won / total * 100:>11.2f}%")

        trades = report["trade_list"]
        if trades:
            _out(f"\n  --- 交易明细 (共 {len(trades)} 笔) ---")
            for t in trades:
                bd = t["buy_date"].strftime("%Y-%m-%d") if hasattr(t["buy_date"], "strftime") else str(t["buy_date"])
                sd = t["sell_date"].strftime("%Y-%m-%d") if hasattr(t["sell_date"], "strftime") else str(t["sell_date"])
                _out(f"  {t['code']}  {bd}→{sd}  "
                     f"{t['buy_price']:.2f}→{t['sell_price']:.2f}  "
                     f"{t['pnl_pct']:+.2f}%  {t['reason']}")

        _out(f"{'=' * 55}")

        if log_file and not log_file.closed:
            log_file.close()
