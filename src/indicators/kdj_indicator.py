"""KDJ 指标 - 使用 MyTT 的 HHV/LLV/SMA 计算（通达信标准公式）"""

import numpy as np
import backtrader as bt
from MyTT import SMA, HHV, LLV


class KDJIndicator(bt.Indicator):
    """
    通达信标准 KDJ 指标

    公式:
      RSV = (C - LLV(L, N)) / (HHV(H, N) - LLV(L, N)) * 100
      K = SMA(RSV, M1, 1)   # 中国式加权移动平均
      D = SMA(K, M2, 1)
      J = 3*K - 2*D

    MyTT.SMA(S, N, M) 使用 ewm(alpha=M/N)，通达信标准用 SMA(RSV,3,1)
    """

    lines = ("K", "D", "J")
    params = (("n", 9), ("m1", 3), ("m2", 3))

    def __init__(self):
        C = self.data.close.array
        H = self.data.high.array
        L = self.data.low.array

        llv = LLV(L, self.p.n)
        hhv = HHV(H, self.p.n)

        denom = hhv - llv
        rsv = np.where(denom != 0, (C - llv) / denom * 100, 50.0)

        self._k = SMA(rsv, self.p.m1, 1)
        self._d = SMA(self._k, self.p.m2, 1)
        self._j = 3 * self._k - 2 * self._d

    def next(self):
        idx = len(self) - 1
        self.lines.K[0] = self._k[idx]
        self.lines.D[0] = self._d[idx]
        self.lines.J[0] = self._j[idx]
