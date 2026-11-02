"""
CUSUM engine — pure stateless functions, no I/O.

Tabular CUSUM formulas:
    s_high_new = max(0, s_high + (value − mu_ref) − k)
    s_low_new  = max(0, s_low  − (value − mu_ref) − k)
    alarm      = s_high >= h  or  s_low >= h
"""

from __future__ import annotations


def update(
    s_high: float,
    s_low: float,
    value: float,
    mu_ref: float,
    k: float,
) -> tuple[float, float]:
    """Advance accumulators by one observation. Returns (s_high, s_low)."""
    dev = value - mu_ref
    return max(0.0, s_high + dev - k), max(0.0, s_low - dev - k)


def is_alarm(s_high: float, s_low: float, h: float) -> bool:
    """True if either accumulator has crossed the decision threshold h."""
    return s_high >= h or s_low >= h


def replay(
    observations: list[float],
    mu_ref: float,
    k: float,
) -> tuple[float, float]:
    """Recompute accumulators from scratch (cold-start recovery)."""
    s_high, s_low = 0.0, 0.0
    for v in observations:
        s_high, s_low = update(s_high, s_low, v, mu_ref, k)
    return s_high, s_low
