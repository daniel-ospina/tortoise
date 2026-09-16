"""Shared entity-identity vectors for #3590 (Slice 0) — the #3589 mechanism.

One vector file (``tests/fixtures/entity_identity_vectors.json``) consumed by
``tests/test_rebuild_live_invariant.py`` here and by the S1/S2 resolver suites
later, so the shapes are shared rather than re-inlined per slice. In-repo
precedent for the shape: ``tests/fixtures/org_naming_vectors.json`` (#2779).

This module is a LOADER, not a test module — pytest's default ``python_files``
(``test_*.py``) does not collect it, so a ``test_`` function here would never
run (a vacuity trap). The schema is validated by ``validate_fixture()``, which
``test_rebuild_live_invariant.py::test_fixture_shape`` actually calls.

Every vector carries ``verdict``, ``owner`` and ``harness``. #3589's rule — no
permanently skipped test without an owner — is enforced here: an
``xfail``/``pending`` vector with no ``owner`` fails validation.
"""
from __future__ import annotations

import json
from pathlib import Path

FIXTURE = Path(__file__).with_name("fixtures") / "entity_identity_vectors.json"

# pass    — must be green now (S0)
# xfail   — known-bad today; strict xfail carrying the owner issue
# pending — executable only once a later slice lands the mechanism (owner required)
VERDICTS = frozenset({"pass", "xfail", "pending"})
REQUIRED_FIELDS = (
    "id", "description", "steps", "expect_live", "expect_replay",
    "verdict", "owner", "harness",
)


def load_vectors() -> list[dict]:
    """All vectors, in fixture order."""
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["vectors"]


def vector(id_: str) -> dict:
    """One vector by id — KeyError names the missing id, never returns None."""
    for v in load_vectors():
        if v["id"] == id_:
            return v
    raise KeyError(f"no entity-identity vector {id_!r}")


def validate_fixture() -> None:
    """Assert the fixture's schema — distinctly identified, well-formed,
    owned if skipped, with an honest ``harness`` flag."""
    vectors = load_vectors()
    ids = [v["id"] for v in vectors]
    assert len(ids) == len(set(ids)), f"duplicate vector ids: {ids}"
    for v in vectors:
        missing = [f for f in REQUIRED_FIELDS if f not in v]
        assert not missing, f"{v.get('id')!r}: missing fields {missing}"
        assert v["verdict"] in VERDICTS, \
            f"{v['id']!r}: unknown verdict {v['verdict']!r}"
        assert isinstance(v["steps"], list) and v["steps"], \
            f"{v['id']!r}: steps must be a non-empty list"
        assert isinstance(v["harness"], bool), \
            f"{v['id']!r}: harness must be a bool"
        if v["verdict"] in ("xfail", "pending"):
            assert v["owner"], (
                f"{v['id']!r}: verdict={v['verdict']!r} without an owner — "
                "#3589 forbids a permanently-skipped test with no owner")
