"""Version vectors — Lamport clocks + cross-machine causality tracking.

REQ-EVT-005. ~30 LOC. Per-machine Lamport clock + vector clock for
distributed agent coordination. Composes with coordination.py for
Card lifecycle ordering.
"""
from __future__ import annotations


class VersionVector:
    """Per-machine Lamport clock + cross-machine vector clock."""

    def __init__(self, machine_id: str):
        self.machine_id = machine_id
        self._clock: dict[str, int] = {machine_id: 0}

    def increment(self, machine_id: str | None = None) -> int:
        """Increment the clock for a machine. Returns new value."""
        mid = machine_id or self.machine_id
        self._clock[mid] = self._clock.get(mid, 0) + 1
        return self._clock[mid]

    def get(self, machine_id: str) -> int:
        """Read clock value for a machine (0 if unseen)."""
        return self._clock.get(machine_id, 0)

    def merge(self, other: VersionVector) -> None:
        """Merge another vector clock into this one (max per dimension)."""
        for mid, count in other._clock.items():
            self._clock[mid] = max(self._clock.get(mid, 0), count)

    def happens_before(self, other: VersionVector) -> bool:
        """True if self strictly happens-before other.

        Self ≤ other on all machines AND strictly less on at least one.
        """
        all_keys = set(self._clock) | set(other._clock)
        any_less = False
        for k in all_keys:
            s = self._clock.get(k, 0)
            o = other._clock.get(k, 0)
            if s > o:
                return False
            if s < o:
                any_less = True
        return any_less

    def to_dict(self) -> dict[str, int]:
        return dict(self._clock)

    @classmethod
    def from_dict(cls, machine_id: str, data: dict[str, int]) -> VersionVector:
        vv = cls(machine_id)
        vv._clock = dict(data)
        if machine_id not in vv._clock:
            vv._clock[machine_id] = 0
        return vv


# ── self-check ────────────────────────────────────────────────

if __name__ == "__main__":
    a = VersionVector("A")
    b = VersionVector("B")

    a.increment("A")  # A:1
    assert a.get("A") == 1
    assert a.get("B") == 0

    b.increment("B")  # B:1
    b.increment("B")  # B:2

    # Concurrent — neither happens-before the other
    assert not a.happens_before(b)
    assert not b.happens_before(a)

    # After merge, A sees B's clock (A:1, B:2)
    a.merge(b)
    assert a.get("B") == 2

    # B updated again: B:3. a and b are concurrent (a ahead on A, b ahead on B)
    b.increment("B")  # B:3
    assert not a.happens_before(b)  # a is ahead on A
    assert not b.happens_before(a)  # b is ahead on B

    # Fresh vector C that only sees B:1 — should happen-before b
    c = VersionVector("C")
    c.increment("B")  # C sees B at 1
    assert c.happens_before(b)  # C < b on B (1 < 3)

    # Roundtrip
    d = b.to_dict()
    b2 = VersionVector.from_dict("B", d)
    assert b2.get("B") == 3
    assert b2.happens_before(b) is False  # equal

    print("✅ version_vector")
