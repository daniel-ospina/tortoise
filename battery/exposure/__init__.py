"""#2284 Task 8 — two-part exposure study (mechanism-liveness + oracle).

Ownership note (plan Task 8): the exposure harness retires the 4 executor
risks on a measured basis BEFORE Task 9's real executor transport ships.
This package holds the hermetic legs (liveness, oracle, usage, cap) plus
the budget-guarded real smoke driver; nothing here is ever claimed as
product semantics — the A4 arm + SDK stay the single product surface.

Liveness + oracle live here so the smoke report (exposure-part1.md) can
cite the same callables the tests lock.
"""
from __future__ import annotations
