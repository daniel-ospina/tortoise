"""#5361 — ``restore_point_at`` must not report an INVERTED window as absence.

The audit half of #5361 surfaces how many Points carry a window whose END
precedes its START. This half is the READ path: when such a Point sits on the
chain, no instant is covered, so ``found`` is correctly ``False`` — and without
a signal the reply is byte-identical to a Point that honestly has no window at
that date. A caller reads corruption as "nothing was true then".

The fix is additive: the honest-absence branch also carries ``malformed: true``
and ``malformed_ids``, and the affected ``nearest`` entry carries
``malformed: true``. Presence is measured with the SAME primitive the coverage
test orders with (``_created_sort_key``) plus ``is not None``, so the flag and
the coverage decision cannot disagree about what "inverted" means. Absent
bounds are legal open intervals; unparseable bounds are #5360's concern and are
not flagged here.

Scope is deliberate: the flag is emitted only in the honest-absence branch. A
covering window cannot be inverted, so where coverage succeeds there is nothing
to disambiguate — and adding a flag to the found path would change a reply that
is already correct.

Test doctrine (Class B): every docstring states (1) the value whose presence
makes the test fail and (2) that the fixture reaches that value.

Run with:
  TORTOISE_DB_URI='docker://:falkordb@localhost:16730/tortoise_test_matrix' \\
    uv run pytest tests/test_5361_inverted_window_read_path.py -q -p no:randomly
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: I001
from tortoise.sdk import TortoiseSDK


@pytest.fixture
def sdk():
    """SDK on a fresh embedded DB, one per test."""
    db_path = os.path.join(
        tempfile.mkdtemp(prefix="tortoise_inv5361r_test_"), "test.db"
    )
    sdk = TortoiseSDK(db_path)
    yield sdk
    sdk.close()
    shutil.rmtree(os.path.dirname(db_path), ignore_errors=True)


def _q(sdk, query: str, params: dict | None = None) -> list:
    r = sdk._get_proj().g.query(query, params=params or {})
    return r.result_set if r else []


def _plant(sdk, valid_from=None, valid_to=None) -> str:
    """Create ONE Point with the given raw window bounds, written verbatim.

    A ``None`` bound is left unset so the read path sees honest absence of that
    side (an open interval) rather than a stored null.
    """
    pid = f"pr5361_{uuid.uuid4().hex[:12]}"
    _q(sdk, "CREATE (n:Point {id:$id, pointKind:'k5361read', content:'w', "
            "is_operator:false, status:'live'})", params={"id": pid})
    if valid_from is not None:
        _q(sdk, "MATCH (n:Point {id:$id}) SET n.validFrom = $v",
           params={"id": pid, "v": valid_from})
    if valid_to is not None:
        _q(sdk, "MATCH (n:Point {id:$id}) SET n.validTo = $v",
           params={"id": pid, "v": valid_to})
    return pid


# ── The falsifier ─────────────────────────────────────────────────

def test_inverted_window_is_flagged_not_dressed_as_absence(sdk):
    """FALSIFIER: an inverted window must set ``malformed``, not just ``found: false``.

    Value that makes it fail: the planted pair (validFrom '2030-06-01',
    validTo '2020-06-01') queried at '2025-01-01', a date no instant of that
    window can cover. The fixture reaches it — both bounds are written on the
    chain's only Point. Without the flag the reply is exactly what an honest
    absence returns, so the assertion cannot pass by accident.

    It also pins the two sub-signals: the id is named, and the ``nearest``
    entry — which is this very window — is itself marked malformed rather than
    offered as innocent context.
    """
    pid = _plant(sdk, valid_from="2030-06-01", valid_to="2020-06-01")
    out = sdk.restore_point_at(pid, "2025-01-01")
    assert out["found"] is False
    assert out.get("malformed") is True, out
    assert out.get("malformed_ids") == [pid], out
    assert out["nearest"]["malformed"] is True, out["nearest"]


def test_forward_window_is_not_flagged(sdk):
    """A normal forward window that simply does not cover must NOT be flagged.

    Value that makes it fail: validFrom '2020-06-01' < validTo '2030-06-01'
    queried at '2035-01-01'. The fixture reaches it — both bounds are written
    and the date lies after the window, so this is honest absence and a check
    that flagged every non-covering window would fail here.
    """
    pid = _plant(sdk, valid_from="2020-06-01", valid_to="2030-06-01")
    out = sdk.restore_point_at(pid, "2035-01-01")
    assert out["found"] is False
    assert out.get("malformed") is not True, out
    assert out["nearest"]["malformed"] is False, out["nearest"]


def test_nearest_malformed_is_always_present_and_false(sdk):
    """``nearest.malformed`` is ALWAYS present and False when nothing is
    inverted — distinct from the top-level keys, which are ABSENT when clean.

    Value that makes it fail: DELETING the key from the nearest entry. The
    fixture reaches it — a well-formed forward window that does not cover, so
    the reply is honest absence WITH a nearest entry reported.

    ``is False`` rather than ``.get(...) is not True`` is the whole point:
    ``.get()`` also passes when the key is missing entirely, so it cannot pin
    the presence the manifest claims.
    """
    pid = _plant(sdk, valid_from="2020-06-01", valid_to="2030-06-01")
    out = sdk.restore_point_at(pid, "2035-01-01")
    assert out["found"] is False
    assert "nearest" in out and out["nearest"] is not None, out
    assert "malformed" in out["nearest"], (
        "nearest.malformed must be present on every nearest entry: "
        f"{out['nearest']}"
    )
    assert out["nearest"]["malformed"] is False, out["nearest"]


def test_open_ended_window_is_not_flagged(sdk):
    """An open-ended window must NOT be flagged, in BOTH directions.

    Value that makes it fail: a missing bound (``None``) on either side. The
    fixture reaches it — one Point carries validFrom only, another validTo
    only. A truthiness gate would call the ABSENT bound inverted; the read
    path's predicate is ``is not None``, so neither is flagged. (A
    PRESENT-but-falsey bound is a different case and IS flagged — see
    ``test_falsey_but_present_bound_is_epoch_not_absence``.)
    """
    only_start = _plant(sdk, valid_from="2020-06-01")
    out = sdk.restore_point_at(only_start, "2010-01-01")
    assert out["found"] is False and "malformed" not in out, out

    only_end = _plant(sdk, valid_to="2030-06-01")
    out = sdk.restore_point_at(only_end, "2035-01-01")
    assert out["found"] is False and "malformed" not in out, out


def test_falsey_but_present_bound_is_epoch_not_absence(sdk):
    """A bound of ``0`` is PRESENT-but-falsey — epoch, not absence (#3985).

    Value that makes it fail: ``validTo=0`` against
    ``validFrom='2030-01-01'``. ``0`` is present, so ``_created_sort_key(0)``
    is ``(0, 0.0)`` and the pair IS inverted — the reply must say so. The
    fixture reaches it: the bound is written as the integer ``0``, which
    round-trips as a present property, so a truthiness gate (``if not vt``)
    would read it as an open end and stay silent. That is the exact ``is not
    None`` vs truthiness distinction this pins.
    """
    pid = _plant(sdk, valid_from="2030-01-01", valid_to=0)
    out = sdk.restore_point_at(pid, "2020-01-01")
    assert out["found"] is False
    assert out.get("malformed") is True, out
    assert out.get("malformed_ids") == [pid], out


def test_unparseable_bound_is_not_flagged(sdk):
    """An UNPARSEABLE bound must NOT be flagged — that is #5360's concern.

    Value that makes it fail: validFrom 'zzz' with validTo '2026-01-01', a pair
    the read path cannot order at all. The fixture reaches it — both bounds are
    written, the start as text. ``_created_sort_key('zzz')`` buckets as
    ``(1, 'zzz')``, so a raw string compare would read this as inverted and a
    check that ignored parseability would flag it.
    """
    pid = _plant(sdk, valid_from="zzz", valid_to="2026-01-01")
    out = sdk.restore_point_at(pid, "2025-01-01")
    assert out["found"] is False
    assert "malformed" not in out, out


def test_zero_length_window_is_not_flagged(sdk):
    """A zero-length window is well-formed, so it must NOT be flagged.

    Value that makes it fail: equality ('2026-06-15' on both sides) queried a
    day later. The fixture reaches it — both bounds carry the same string, so
    ``k_to == k_from`` and the strict comparison never fires.
    """
    pid = _plant(sdk, valid_from="2026-06-15", valid_to="2026-06-15")
    out = sdk.restore_point_at(pid, "2026-06-16")
    assert out["found"] is False and "malformed" not in out, out


# ── The found path is untouched ───────────────────────────────────

def test_covering_window_reply_is_unchanged(sdk):
    """When a window covers, the reply must be exactly as before — no new key.

    Value that makes it fail: a date INSIDE the planted window
    ('2020-06-01'..'2030-06-01' at '2025-01-01'), where ``found`` is True and
    ``valid_point`` is returned. The fixture reaches it. The flag is scoped to
    the absence branch precisely so this reply does not change shape.
    """
    pid = _plant(sdk, valid_from="2020-06-01", valid_to="2030-06-01")
    out = sdk.restore_point_at(pid, "2025-01-01")
    assert out["found"] is True, out
    assert out["valid_point"]["id"] == pid
    assert "malformed" not in out, out
    assert "nearest" not in out, out


def test_malformed_window_beside_a_valid_one_names_only_the_bad_id(sdk):
    """With a mix on the chain, only the inverted window's id is named.

    Value that makes it fail: a chain carrying BOTH a well-formed window that
    does not cover the date and an inverted one. The fixture reaches it — the
    Point is superseded by a successor carrying the inverted window, so the
    walk visits both. A check that flagged the whole chain, or that missed the
    inverted entry behind the valid one, fails the id assertion.
    """
    old = _plant(sdk, valid_from="2020-06-01", valid_to="2020-12-31")
    new = _plant(sdk, valid_from="2030-06-01", valid_to="2020-06-01")
    _q(sdk, "MATCH (a:Point {id:$a}), (b:Point {id:$b}) MERGE (a)-[:CORRECTS]->(b)",
       params={"a": new, "b": old})

    out = sdk.restore_point_at(new, "2025-01-01")
    assert out["found"] is False, out
    assert out.get("malformed") is True, out
    assert out.get("malformed_ids") == [new], out
    assert {e["id"] for e in out["chain"]} == {new, old}, out["chain"]
