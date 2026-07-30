"""Tests for version_vector — Lamport clocks + happens-before.

Covers: increment, merge, happens_before, serialization, concurrency detection.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tortoise.version_vector import VersionVector


def test_increment():
    vv = VersionVector("A")
    assert vv.get("A") == 0
    vv.increment("A")
    assert vv.get("A") == 1
    vv.increment("A")
    assert vv.get("A") == 2


def test_other_machine():
    vv = VersionVector("A")
    vv.increment("B")
    assert vv.get("B") == 1
    assert vv.get("A") == 0


def test_increment_no_arg_defaults_to_own_machine():
    """increment() with no argument increments self.machine_id."""
    vv = VersionVector("X")
    assert vv.increment() == 1
    assert vv.get("X") == 1
    vv.increment()
    assert vv.get("X") == 2


def test_merge():
    a = VersionVector("A")
    b = VersionVector("B")
    a.increment("A")  # A:1
    b.increment("B")  # B:1
    b.increment("B")  # B:2

    a.merge(b)
    assert a.get("A") == 1  # preserved
    assert a.get("B") == 2  # took max from b

    # a doesn't lose its own clock on merge
    b_no_a = VersionVector("B")
    b_no_a.increment("B")
    a.merge(b_no_a)
    assert a.get("A") == 1  # still there


def test_happens_before_strictly_before():
    a = VersionVector("A")
    b = VersionVector("B")
    b.increment("B")  # B:1

    assert a.happens_before(b)  # a ≤ b on all, < on B
    assert not b.happens_before(a)


def test_happens_before_concurrent():
    a = VersionVector("A")
    b = VersionVector("B")
    a.increment("A")  # A:1
    b.increment("B")  # B:1

    assert not a.happens_before(b)
    assert not b.happens_before(a)


def test_happens_before_equal_not_strict():
    a = VersionVector("A")
    b = VersionVector("A")
    a.increment("A")  # A:1
    b.increment("A")  # A:1

    assert not a.happens_before(b)  # equal, not strictly before
    assert not b.happens_before(a)


def test_happens_before_same_machine_monotonic():
    vv = VersionVector("A")
    v1 = VersionVector.from_dict("A", vv.to_dict())
    vv.increment("A")  # A:1
    assert v1.happens_before(vv)
    v2 = VersionVector.from_dict("A", vv.to_dict())
    vv.increment("A")  # A:2
    assert v2.happens_before(vv)


def test_to_from_dict_roundtrip():
    vv = VersionVector("M")
    vv.increment("A")
    vv.increment("B")
    vv.increment("B")

    d = vv.to_dict()
    assert d == {"M": 0, "A": 1, "B": 2}

    vv2 = VersionVector.from_dict("M", d)
    assert vv2.get("A") == 1
    assert vv2.get("B") == 2
    assert vv2.machine_id == "M"


def test_default_get():
    vv = VersionVector("X")
    assert vv.get("nonexistent") == 0


def test_from_dict_missing_machine_id():
    """from_dict adds machine_id=0 when not present in data."""
    vv = VersionVector.from_dict("Z", {"A": 5})
    assert vv.machine_id == "Z"
    assert vv.get("A") == 5
    assert vv.get("Z") == 0


def test_merge_from_empty_self():
    """Merge when self has no entries beyond own machine."""
    a = VersionVector("A")
    b = VersionVector("B")
    b.increment("B")
    b.increment("B")
    b.increment("C")
    a.merge(b)
    assert a.get("B") == 2
    assert a.get("C") == 1
    assert a.get("A") == 0


def test_happens_before_self_after_merge():
    """After merge, a vector happens-before its prior self if no new ticks."""
    a = VersionVector("A")
    a.increment("A")
    snapshot = VersionVector.from_dict("A", a.to_dict())
    b = VersionVector("B")
    b.increment("B")
    a.merge(b)
    assert snapshot.happens_before(a)


def test_happens_before_max_values():
    """happens_before with large clock values."""
    a = VersionVector("A")
    b = VersionVector("B")
    for _ in range(100):
        a.increment("A")
    for _ in range(50):
        b.increment("B")
    assert a.happens_before(b) is False
    assert b.happens_before(a) is False


# ── Self-check runner ───────────────────────────────────────

def test_version_vector_self_check():
    """Run module as __main__ in-process to cover self-check assertions."""
    import runpy
    from pathlib import Path

    mod = Path(__file__).resolve().parents[1] / "tortoise" / "version_vector.py"
    runpy.run_path(str(mod), run_name="__main__")
