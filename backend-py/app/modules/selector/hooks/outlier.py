"""Lightweight outlier detection for incoming metrics.

A metric value is "outlying" if it jumps by more than ``factor`` standard
deviations from the running mean of recent values for the same entity. The
running stats are kept in-process (single-instance is fine here — outlier
flagging is best-effort).

This is *advisory*: the selector still ingests the value. The function
returns True if the value looks suspicious so the caller can log / alert
without losing data.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class _RunningStats:
    n: int = 0
    mean: float = 0.0
    m2: float = 0.0  # sum of squared deviations from mean

    @property
    def stddev(self) -> float:
        if self.n < 2:
            return 0.0
        return math.sqrt(self.m2 / (self.n - 1))


class OutlierDetector:
    def __init__(self, *, factor: float = 5.0, warmup: int = 5):
        if factor <= 0:
            raise ValueError("factor must be > 0")
        self._factor = factor
        self._warmup = max(2, warmup)
        self._stats: dict[str, _RunningStats] = {}

    def observe(self, key: str, value: float) -> bool:
        """Update stats with ``value`` and return True if it looks outlying.

        During the warmup window we always return False — we don't have
        enough data to judge.
        """
        s = self._stats.setdefault(key, _RunningStats())
        outlier = False
        if s.n >= self._warmup and s.stddev > 0:
            z = abs(value - s.mean) / s.stddev
            if z > self._factor:
                outlier = True
        # Welford's online update — numerically stable mean and variance.
        s.n += 1
        delta = value - s.mean
        s.mean += delta / s.n
        delta2 = value - s.mean
        s.m2 += delta * delta2
        return outlier
