"""量化评分模块

基于 prompt.md 的四维评分体系，对股票进行纯量化评分：
- 趋势结构 (Trend Structure)       权重 0.20
- 价格位置 (Price Position)         权重 0.20
- 量价行为 (Volume-Price Behavior)  权重 0.30
- 前期异动 (Previous Abnormal Move) 权重 0.30

判定规则:
- PASS:  total_score >= 4.0
- WATCH: 3.2 <= total_score < 4.0
- FAIL:  total_score < 3.2 或 volume_behavior == 1
"""

import numpy as np
import pandas as pd
from MyTT import MA, HHV, LLV, EMA


class QuantitativeScorer:
    """纯量化实现 prompt.md 评分体系"""

    def __init__(self, C, H, L, O, V, dates, white=None, yellow=None):
        self.C = np.asarray(C, dtype=float)
        self.H = np.asarray(H, dtype=float)
        self.L = np.asarray(L, dtype=float)
        self.O = np.asarray(O, dtype=float)
        self.V = np.asarray(V, dtype=float)
        self.dates = dates
        self.white = np.asarray(white, dtype=float) if white is not None else None
        self.yellow = np.asarray(yellow, dtype=float) if yellow is not None else None
        self.n = len(C)

        # 预计算常用均线
        self._ma5 = MA(self.C, 5)
        self._ma10 = MA(self.C, 10)
        self._ma20 = MA(self.C, 20)
        self._ma60 = MA(self.C, 60)

    # ----------------------------------------------------------------
    # 趋势结构 (1-5 分)
    # ----------------------------------------------------------------
    def score_trend_structure(self, ref_idx):
        """评分均线多头排列与斜率"""
        if ref_idx < 60:
            return 2, "数据不足，趋势判断受限"

        ma5 = self._ma5[ref_idx]
        ma10 = self._ma10[ref_idx]
        ma20 = self._ma20[ref_idx]
        ma60 = self._ma60[ref_idx]

        # 多头排列
        bullish = ma5 > ma10 > ma20 > ma60
        # 斜率（5 日变化率）
        ma20_slope = ((ma20 - self._ma20[ref_idx - 5])
                       / max(abs(self._ma20[ref_idx - 5]), 0.01) * 100)
        ma60_slope = ((ma60 - self._ma60[ref_idx - 10])
                       / max(abs(self._ma60[ref_idx - 10]), 0.01) * 100)

        # 空头排列
        bearish = ma5 < ma10 < ma20 < ma60

        # 间距合理性（MA5 / MA60 不要过度发散）
        spread_ok = (ma5 / max(ma60, 0.01)) < 1.15 if ma60 > 0.01 else False

        reasoning_parts = []

        if bullish and ma20_slope > 0.3 and ma60_slope > 0 and spread_ok:
            score = 5
            reasoning_parts.append("均线刚进入多头，短均线上拐，间距合理")
        elif bullish and (ma20_slope > 0 or ma60_slope > 0):
            score = 4
            reasoning_parts.append("均线多头排列形成，整体向上")
        elif bullish:
            score = 3
            reasoning_parts.append("均线偏多但运行不够流畅")
        elif not bullish and not bearish:
            # 检查接近多头
            partial = (ma5 > ma10 > ma20) or (ma10 > ma20 > ma60)
            if partial and ma20_slope > 0:
                score = 3
                reasoning_parts.append("趋势改善中，均线开始上行")
            else:
                score = 2
                reasoning_parts.append("均线频繁交叉，趋势不清晰")
        else:
            score = 1
            reasoning_parts.append("空头排列，均线向下")

        if ma60_slope < -0.2:
            if score > 2:
                score -= 1
            reasoning_parts.append("MA60仍下弯")

        reasoning = "；".join(reasoning_parts) if reasoning_parts else "趋势中性"
        return score, reasoning

    # ----------------------------------------------------------------
    # 价格位置 (1-5 分)
    # ----------------------------------------------------------------
    def score_price_position(self, ref_idx):
        """评分当前价格在近期区间中的相对位置"""
        lookback = min(120, ref_idx)
        if lookback < 30:
            return 3, "数据不足，位置判断受限"

        high_n = np.max(self.H[ref_idx - lookback: ref_idx + 1])
        low_n = np.min(self.L[ref_idx - lookback: ref_idx + 1])
        price_range = high_n - low_n

        if price_range < 0.01:
            return 3, "价格区间过窄"

        position_pct = (self.C[ref_idx] - low_n) / price_range * 100

        # 突破平台检测
        recent_high_20 = np.max(self.H[max(0, ref_idx - 20): ref_idx])
        breakout = self.C[ref_idx] >= recent_high_20 * 0.98

        reasoning_parts = []

        if position_pct < 30:
            score = 5
            reasoning_parts.append("中低位刚突破，上方空间大")
            if breakout:
                reasoning_parts.append("突破近20日高点")
        elif position_pct < 50:
            score = 4
            reasoning_parts.append("中位突破区，脱离整理平台")
        elif position_pct < 70:
            score = 3
            reasoning_parts.append("接近前高区，可能突破也可能受阻")
        elif position_pct < 85:
            score = 2
            reasoning_parts.append("高位区，上方空间有限")
        else:
            score = 1
            reasoning_parts.append("明显高位或过热区")

        reasoning = "；".join(reasoning_parts)
        return score, reasoning

    # ----------------------------------------------------------------
    # 量价行为 (1-5 分)
    # ----------------------------------------------------------------
    def score_volume_behavior(self, ref_idx):
        """评分最近上涨-回调波段的量价配合"""
        lookback = min(30, ref_idx)
        if lookback < 10:
            return 3, "数据不足"

        seg_C = self.C[ref_idx - lookback: ref_idx + 1]
        seg_O = self.O[ref_idx - lookback: ref_idx + 1]
        seg_V = self.V[ref_idx - lookback: ref_idx + 1]
        m = len(seg_C)

        # 找最高点和最低点（分段判断涨跌）
        high_idx = np.argmax(seg_C)

        # 上涨段: 从起始到最高点
        rise_end = high_idx + 1
        # 回调段: 从最高点到末尾
        fall_start = high_idx

        # 上涨段阳线均量 vs 回调段阴线均量
        rise_yang = (seg_C[:rise_end] >= seg_O[:rise_end]) & (seg_V[:rise_end] > 0)
        fall_yin = (seg_C[fall_start:] < seg_O[fall_start:]) & (seg_V[fall_start:] > 0)

        rise_avg = np.mean(seg_V[:rise_end][rise_yang]) if np.any(rise_yang) else 0
        fall_avg = np.mean(seg_V[fall_start:][fall_yin]) if np.any(fall_yin) else 0

        vol_ratio = fall_avg / max(rise_avg, 1)

        # 最大量K线是阳线还是阴线
        max_vol_local = np.argmax(seg_V)
        max_vol_is_yang = seg_C[max_vol_local] >= seg_O[max_vol_local]

        # 检查是否存在放量大阴线（量 > 均量 * 2 且为阴线）
        avg_vol = np.mean(seg_V)
        big_bearish = np.any(
            (seg_V > avg_vol * 2) & (seg_C < seg_O)
        )

        reasoning_parts = []

        if not max_vol_is_yang:
            score = 1
            reasoning_parts.append("最大量出现在下跌K线，量价结构被破坏")
        elif big_bearish:
            score = 1
            reasoning_parts.append("存在放量大阴线，有出货迹象")
        elif vol_ratio < 0.5:
            score = 5
            reasoning_parts.append("回调缩量至上涨量能一半以下，量价配合极佳")
        elif vol_ratio < 0.7:
            score = 4
            reasoning_parts.append("上涨放量回调缩量，量价关系健康")
        elif vol_ratio < 1.0:
            score = 3
            reasoning_parts.append("量价中性，涨跌量能差异不大")
        elif vol_ratio < 1.5:
            score = 2
            reasoning_parts.append("回调不缩量，量价偏弱")
        else:
            score = 2
            reasoning_parts.append("上涨缩量下跌放量，量价恶化")

        if max_vol_is_yang and score >= 4:
            reasoning_parts.append("最大量出现在上涨阶段")

        reasoning = "；".join(reasoning_parts)
        return score, reasoning

    # ----------------------------------------------------------------
    # 前期异动 (1-5 分)
    # ----------------------------------------------------------------
    def score_previous_abnormal_move(self, ref_idx):
        """评分前期是否有主力建仓痕迹"""
        lookback = min(60, ref_idx)
        if lookback < 20:
            return 3, "数据不足"

        seg_C = self.C[ref_idx - lookback: ref_idx]
        seg_O = self.O[ref_idx - lookback: ref_idx]
        seg_V = self.V[ref_idx - lookback: ref_idx]
        seg_H = self.H[ref_idx - lookback: ref_idx]

        avg_vol = np.mean(seg_V)
        yang_pct = (seg_C - seg_O) / np.maximum(seg_O, 0.01) * 100

        # 异常放量阳线: 量 > 2倍均量 + 涨幅 > 3% + 阳线
        abnormal_mask = (
            (seg_V > avg_vol * 2)
            & (seg_C > seg_O)
            & (yang_pct > 3)
        )
        has_abnormal = np.any(abnormal_mask)

        # 突破平台: 突破前期30日高点
        if lookback > 30:
            prev_high = np.max(seg_H[:lookback - 10])
            breakout = self.C[ref_idx] > prev_high * 0.98
        else:
            breakout = False

        # 异动阶段涨幅
        abnormal_indices = np.where(abnormal_mask)[0]
        if len(abnormal_indices) > 0:
            first_ab = abnormal_indices[0]
            last_ab = abnormal_indices[-1]
            abnormal_gain = (seg_C[last_ab] - seg_C[first_ab]) / max(seg_C[first_ab], 0.01) * 100
        else:
            abnormal_gain = 0

        # 整体区间涨幅
        total_gain = (seg_C[-1] - seg_C[0]) / max(seg_C[0], 0.01) * 100

        # 放量大阴线检测（出货迹象）
        big_bearish_mask = (
            (seg_V > avg_vol * 2)
            & (seg_C < seg_O)
            & (yang_pct < -3)
        )
        has_bearish = np.any(big_bearish_mask)

        reasoning_parts = []

        if has_bearish and abnormal_gain > 50:
            score = 1
            reasoning_parts.append("存在放量大阴线，有出货迹象")
        elif total_gain > 100:
            score = 1
            reasoning_parts.append(f"区间涨幅{total_gain:.0f}%，已远离建仓区")
        elif not has_abnormal:
            if total_gain > 50:
                score = 2
                reasoning_parts.append("无明显放量建仓痕迹，且涨幅偏大")
            else:
                score = 2
                reasoning_parts.append("只有普通上涨，无异常放量")
        elif has_abnormal and breakout and abnormal_gain < 50:
            score = 5
            reasoning_parts.append("异常放量阳线突破平台，建仓痕迹明确")
        elif has_abnormal and abnormal_gain < 50:
            score = 4
            reasoning_parts.append("明显放量阳线，突破结构尚可")
        elif has_abnormal:
            score = 3
            reasoning_parts.append("有一定放量上涨，但不突出")
        else:
            score = 2

        if abnormal_gain > 0 and abnormal_gain < 100:
            reasoning_parts.append(f"异动阶段涨幅{abnormal_gain:.0f}%")

        reasoning = "；".join(reasoning_parts)
        return score, reasoning

    # ----------------------------------------------------------------
    # 综合评分
    # ----------------------------------------------------------------
    def score_all(self, ref_idx):
        """对指定日期进行四维综合评分

        Returns:
            dict: 完整评分结果（符合 prompt.md 输出格式）
        """
        trend_score, trend_reasoning = self.score_trend_structure(ref_idx)
        position_score, position_reasoning = self.score_price_position(ref_idx)
        volume_score, volume_reasoning = self.score_volume_behavior(ref_idx)
        abnormal_score, abnormal_reasoning = self.score_previous_abnormal_move(ref_idx)

        total_score = (
            trend_score * 0.20
            + position_score * 0.20
            + volume_score * 0.30
            + abnormal_score * 0.30
        )

        # 信号类型
        if volume_score == 1:
            signal_type = "distribution_risk"
        elif trend_score >= 4 and abnormal_score >= 4:
            signal_type = "trend_start"
        else:
            signal_type = "rebound"

        # 判定
        if volume_score == 1:
            verdict = "FAIL"
        elif total_score >= 4.0:
            verdict = "PASS"
        elif total_score >= 3.2:
            verdict = "WATCH"
        else:
            verdict = "FAIL"

        # 交易员点评
        comment = _generate_comment(
            trend_score, position_score, volume_score, abnormal_score,
            signal_type)

        return {
            "trend_reasoning": trend_reasoning,
            "position_reasoning": position_reasoning,
            "volume_reasoning": volume_reasoning,
            "abnormal_move_reasoning": abnormal_reasoning,
            "scores": {
                "trend_structure": trend_score,
                "price_position": position_score,
                "volume_behavior": volume_score,
                "previous_abnormal_move": abnormal_score,
            },
            "total_score": round(total_score, 2),
            "signal_type": signal_type,
            "verdict": verdict,
            "comment": comment,
        }


def _generate_comment(trend, position, volume, abnormal, signal_type):
    """生成一句中文交易员点评"""
    parts = []

    # 趋势描述
    if trend >= 4:
        parts.append("均线多头排列运行中")
    elif trend >= 3:
        parts.append("趋势偏多但不够流畅")
    else:
        parts.append("趋势偏弱")

    # 量价描述
    if volume >= 4:
        parts.append("量价配合健康")
    elif volume >= 3:
        parts.append("量价中性")
    else:
        parts.append("量价恶化")

    # 异动描述
    if abnormal >= 4:
        parts.append("有明确建仓痕迹")
    elif abnormal >= 3:
        parts.append("有一定放量迹象")

    # 信号类型
    signal_map = {
        "trend_start": "主升启动信号",
        "rebound": "反弹信号",
        "distribution_risk": "出货风险",
    }
    parts.append(signal_map.get(signal_type, ""))

    return "，".join(p for p in parts if p)


# ============================================================================
# 批量评分接口
# ============================================================================

def batch_score(all_signals, ref_date=None, threshold=3.2):
    """对 B1 选股结果批量评分

    Args:
        all_signals: {code: signal_dict} 来自 preload_all_signals
        ref_date: 参考日期（默认最后交易日）
        threshold: 最低评分阈值（低于此值的股票被过滤）

    Returns:
        (filtered_signals, scores_dict)
        - filtered_signals: 过滤后的信号字典
        - scores_dict: {code: score_result}
    """
    if not all_signals:
        return {}, {}

    # 确定参考日期
    if ref_date is None:
        # 取所有信号数据最后交易日的最大值
        all_last_dates = []
        for sig in all_signals.values():
            if len(sig['dates']) > 0:
                all_last_dates.append(sig['dates'][-1])
        if not all_last_dates:
            return {}, {}
        ref_date = max(all_last_dates)
    else:
        ref_date = pd.Timestamp(ref_date)

    scores_dict = {}
    filtered = {}
    pass_count = watch_count = fail_count = 0

    for code, sig in all_signals.items():
        # 检查 B1 信号是否有任何一个 True
        if not np.any(sig.get('b1', np.zeros(1, dtype=bool))):
            continue

        # 找 ref_date 对应的 bar index
        dates = sig['dates']
        idx_arr = np.where(dates <= ref_date)[0]
        if len(idx_arr) == 0:
            continue
        ref_idx = idx_arr[-1]

        scorer = QuantitativeScorer(
            sig['close'], sig['high'], sig['low'],
            sig['open'], sig['volume'], dates,
            sig.get('white'), sig.get('yellow'))

        result = scorer.score_all(ref_idx)
        scores_dict[code] = result

        if result['verdict'] == 'PASS':
            pass_count += 1
        elif result['verdict'] == 'WATCH':
            watch_count += 1
        else:
            fail_count += 1

        # 过滤: 保留 threshold 以上
        if result['total_score'] >= threshold:
            filtered[code] = sig

    print(f"\n  评分统计: PASS={pass_count}  WATCH={watch_count}  FAIL={fail_count}")
    print(f"  阈值 {threshold}: 保留 {len(filtered)} 只, 过滤 {len(scores_dict) - len(filtered)} 只")

    return filtered, scores_dict


def batch_score_on_b1_dates(all_signals, threshold=3.2):
    """对每只股票在 B1 信号日进行评分，返回评分标记

    在信号字典中添加 'ai_score' 和 'ai_verdict' 字段，
    供 PortfolioSimulator 在买入时参考。

    Args:
        all_signals: {code: signal_dict}
        threshold: 最低评分阈值

    Returns:
        (all_signals_with_scores, scores_summary)
    """
    pass_count = watch_count = fail_count = 0
    scores_summary = {}

    for code, sig in all_signals.items():
        b1 = sig.get('b1', np.zeros(1, dtype=bool))
        b1_indices = np.where(b1)[0]

        if len(b1_indices) == 0:
            sig['ai_score'] = np.full(len(sig['close']), 0.0)
            sig['ai_verdict'] = np.full(len(sig['close']), '', dtype=object)
            continue

        scorer = QuantitativeScorer(
            sig['close'], sig['high'], sig['low'],
            sig['open'], sig['volume'], sig['dates'],
            sig.get('white'), sig.get('yellow'))

        ai_score = np.full(len(sig['close']), 0.0)
        ai_verdict = np.full(len(sig['close']), '', dtype=object)

        for idx in b1_indices:
            result = scorer.score_all(idx)
            ai_score[idx] = result['total_score']
            ai_verdict[idx] = result['verdict']

            if idx == b1_indices[-1]:
                scores_summary[code] = result
                if result['verdict'] == 'PASS':
                    pass_count += 1
                elif result['verdict'] == 'WATCH':
                    watch_count += 1
                else:
                    fail_count += 1

        sig['ai_score'] = ai_score
        sig['ai_verdict'] = ai_verdict

    print(f"\n  评分统计: PASS={pass_count}  WATCH={watch_count}  FAIL={fail_count}")
    return all_signals, scores_summary
