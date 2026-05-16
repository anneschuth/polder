"""Gedeelde, thread-safe kostenteller voor parallelle backfill-runs.

Het `parallel <= 1`-pad kan de cumulatieve kosten na elke call checken,
maar in het `parallel > 1`-pad werken N workers op disjuncte chunks en
ziet de aggregatie de kosten pas *nadat* alle workers klaar zijn. Zonder
een gedeelde teller loopt een run ver voorbij `max_cost_usd`.

`CostBudget` is één lock-protected float die elke worker vóór elke
skill-call raadpleegt (`exceeded()`) en na elke call ophoogt (`add()`).
"""

from __future__ import annotations

import threading


class CostBudget:
    """Thread-safe cumulatieve USD-teller met optionele cap.

    `limit_usd is None` betekent geen cap; `exceeded()` is dan altijd False.
    """

    def __init__(self, limit_usd: float | None) -> None:
        self._limit = limit_usd
        self._spent = 0.0
        self._lock = threading.Lock()

    def add(self, cost_usd: float) -> None:
        with self._lock:
            self._spent += cost_usd

    def exceeded(self) -> bool:
        if self._limit is None:
            return False
        with self._lock:
            return self._spent >= self._limit

    @property
    def spent_usd(self) -> float:
        with self._lock:
            return self._spent

    @property
    def limit_usd(self) -> float | None:
        return self._limit
