"""#5361 — ``audit_graph`` must report Points with an INVERTED validity window.

A persisted window whose END precedes its START (``validTo < validFrom``)
covers no instant: ``restore_point_at``'s ``_covers`` requires
``validFrom <= t <= validTo``, so such a Point is silently invisible to every
temporal query while surfaces report honest absence. The audit must surface how
many Points carry one.

The check measures with the READ PATH's own primitives — ``_created_sort_key``
plus ``is not None`` presence — so a naive ``validTo < validFrom`` string
compare is NOT the implementation: that compare misreports open-ended and
unparseable bounds (and raises on ``None``). Unparseable bounds are a
different concern (#5360); this check must not report them.

Test doctrine (Class B): every docstring states (1) the value whose presence
makes the test fail and (2) that the fixture reaches that value.

Run with:
  TORTOISE_DB_URI='docker://:falkordb@localhost:16730/tortoise_test_matrix' \\
    uv run pytest tests/test_5361_inverted_window_audit.py -q -p no:randomly
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
        tempfile.mkdtemp(prefix="tortoise_inv5361_test_"), "test.db"
    )
    sdk = TortoiseSDK(db_path)
    yield sdk
    sdk.close()
    shutil.rmtree(os.path.dirname(db_path), ignore_errors=True)


def _q(sdk, query: str, params: dict | None = None) -> list:
    r = sdk._get_proj().g.query(query, params=params or {})
    return r.result_set if r else []


def _kind() -> str:
    """A pointKind unique to one test — isolates a shared server graph."""
    return f"k5361_{uuid.uuid4().hex[:12]}"


def _plant(sdk, kind: str, valid_from=None, valid_to=None) -> str:
    """Create ONE Point under ``kind`` with the given raw window bounds.

    Bounds are written verbatim (including unparseable text); a ``None`` bound
    is left unset so the read path sees honest absence.
    """
    pid = f"p5361_{uuid.uuid4().hex[:12]}"
    _q(
        sdk,
        "CREATE (n:Point {id:$id, pointKind:$kind, content:'w', "
        "is_operator:false, status:'live'})",
        params={"id": pid, "kind": kind},
    )
    if valid_from is not None:
        _q(sdk, "MATCH (n:Point {id:$id}) SET n.validFrom = $v",
           params={"id": pid, "v": valid_from})
    if valid_to is not None:
        _q(sdk, "MATCH (n:Point {id:$id}) SET n.validTo = $v",
           params={"id": pid, "v": valid_to})
    return pid


def _check(report: dict, cid: str) -> dict | None:
    return next((c for c in report["checks"] if c["id"] == cid), None)


# ── The falsifier ─────────────────────────────────────────────────

def test_planted_inverted_window_is_reported(sdk):
    """FALSIFIER: a Point with validFrom AFTER validTo must be reported.

    Value that makes it fail: the planted pair (validFrom '2030-06-01',
    validTo '2020-06-01'), where ``_created_sort_key(validTo)`` is strictly
    less than ``_created_sort_key(validFrom)``. The fixture reaches it — both
    bounds are written on this Point. An ``audit_graph`` without the window
    check has no ``inverted_validity_window`` entry at all, so this assertion
    cannot pass by accident.
    """
    kind = _kind()
    pid = _plant(sdk, kind, valid_from="2030-06-01", valid_to="2020-06-01")
    report = sdk.audit(point_kinds=[kind])
    ch = _check(report, "inverted_validity_window")
    assert ch is not None, report
    assert ch["count"] == 1
    assert ch["severity"] == "high"
    assert ch["capped"] is False
    assert ch["legacy"] is False
    assert ch["samples"][0]["node_id"] == pid


# ── Selectivity: what must NOT be reported ────────────────────────

def test_absent_bounds_are_not_reported(sdk):
    """An open-ended window must NOT be reported, in BOTH directions.

    Value that makes it fail: a missing bound (``None``) on either side. The
    fixture reaches it — one Point carries validFrom only, another validTo
    only (the property is never written, so the read path sees ``None``). A
    naive ``validTo < validFrom`` Python compare raises TypeError on ``None``,
    and a truthiness gate would treat a falsey bound as absent — neither is
    the read path's ``is not None`` presence predicate.
    """
    kind = _kind()
    _plant(sdk, kind, valid_from="2026-01-01")   # open END
    _plant(sdk, kind, valid_to="2026-01-01")     # open START
    report = sdk.audit(point_kinds=[kind])
    ch = _check(report, "inverted_validity_window")
    assert ch is None or ch["count"] == 0, ch


def test_unparseable_bound_is_not_reported(sdk):
    """An UNPARSEABLE bound must NOT be reported here — owned by #5360.

    Value that makes it fail: validFrom='zzz' with validTo='2026-01-01'.
    ``_created_sort_key('zzz')`` buckets as ``(1, 'zzz')`` while the ISO side
    parses to ``(0, ...)``, so the string compare ``'2026-01-01' < 'zzz'``
    reads as inverted — but the read path cannot order the pair at all. The
    fixture reaches it — both bounds are written, the start as text.

    The mirrored Point carries an unparseable END instead (``'1zzz'``, chosen
    so a naive raw compare WOULD flag it: ``'1zzz' < '2026-01-01'``) which
    makes the other half of the skip decisive — on that side it is bucket 1
    versus bucket 0, not the text, that suppresses the report.
    """
    kind = _kind()
    _plant(sdk, kind, valid_from="zzz", valid_to="2026-01-01")
    _plant(sdk, kind, valid_from="2026-01-01", valid_to="1zzz")
    report = sdk.audit(point_kinds=[kind])
    ch = _check(report, "inverted_validity_window")
    assert ch is None or ch["count"] == 0, ch


def test_zero_length_window_is_not_reported(sdk):
    """A zero-length window (validFrom == validTo) is well-formed → NOT flagged.

    Value that makes it fail: equality ('2026-06-15' on both sides). The
    fixture reaches it — both bounds are written with the same string, so
    ``k_to == k_from`` and the strict ``<`` never fires.
    """
    kind = _kind()
    _plant(sdk, kind, valid_from="2026-06-15", valid_to="2026-06-15")
    report = sdk.audit(point_kinds=[kind])
    ch = _check(report, "inverted_validity_window")
    assert ch is None or ch["count"] == 0, ch


def test_forward_window_is_not_reported(sdk):
    """A normal forward window must NOT be reported.

    Value that makes it fail: validFrom '2020-06-01' < validTo '2030-06-01'.
    The fixture reaches it — both bounds are written, the start strictly
    earlier, so a check that reports every bounded window would fail here.
    """
    kind = _kind()
    _plant(sdk, kind, valid_from="2020-06-01", valid_to="2030-06-01")
    report = sdk.audit(point_kinds=[kind])
    ch = _check(report, "inverted_validity_window")
    assert ch is None or ch["count"] == 0, ch


# ── Count vs sample contract, and scope ───────────────────────────

def test_count_is_uncapped_while_samples_are_capped(sdk):
    """60 inverted windows → count 60 (exact), samples capped at 50.

    Value that makes it fail: a planted population (60) larger than the
    50-sample cap, with a count asserted as the UNCAPPED total. The fixture
    reaches it — 60 distinct Points each carry an inverted window, so a
    LIMIT-capped count would read 50 and an over-reporting count would exceed
    60.
    """
    kind = _kind()
    pids = [_plant(sdk, kind, valid_from="2030-06-01", valid_to="2020-06-01")
            for _ in range(60)]
    report = sdk.audit(point_kinds=[kind])
    ch = _check(report, "inverted_validity_window")
    assert ch is not None
    assert ch["count"] == 60
    assert ch["capped"] is False
    assert len(ch["samples"]) == 50
    assert ch["count"] > len(ch["samples"])
    assert {s["node_id"] for s in ch["samples"]} <= set(pids)


def test_point_kinds_scope_applies(sdk):
    """The window check honours the audit's ``point_kinds`` scope.

    Value that makes it fail: an inverted window planted under an OUT-OF-SCOPE
    kind. The fixture reaches it — one inverted Point in ``kind_a`` and one in
    ``kind_b``; scoping to ``kind_a`` must count exactly its own.
    """
    ka, kb = _kind(), _kind()
    pa = _plant(sdk, ka, valid_from="2030-06-01", valid_to="2020-06-01")
    _plant(sdk, kb, valid_from="2030-06-01", valid_to="2020-06-01")
    report = sdk.audit(point_kinds=[ka])
    ch = _check(report, "inverted_validity_window")
    assert ch is not None and ch["count"] == 1
    assert ch["samples"][0]["node_id"] == pa


# ── The documented fix ────────────────────────────────────────

def test_documented_fix_repairs_the_window(sdk):
    """The reported ``fix`` string must NAME the tool and the Point to repair.

    Value that makes it fail: a ``fix`` string that does not carry the repair
    tool's name or the node id, or a ``validTo`` that still precedes the start.
    The fixture reaches BOTH — the window is planted inverted, so the audit
    emits a sample whose ``fix`` must name the tool and the id, and the repair
    is then applied through that tool's SDK method; a ``fix`` naming anything
    else fails the first pair of assertions, and a repair that does not move the
    END past the START fails the last.

    The first pair is a literal substring check against the emitted text, so it
    pins WHAT THE STRING ADVERTISES — not that the name resolves to a live tool
    (that is the surface's concern, and a rename would fail here loudly rather
    than silently). Asserting only that the window goes quiet would prove that
    *some* repair works, not that the advertised one is the repair performed.
    """
    kind = _kind()
    pid = _plant(sdk, kind, valid_from="2030-06-01", valid_to="2020-06-01")
    ch = _check(sdk.audit(point_kinds=[kind]), "inverted_validity_window")
    assert ch is not None and ch["count"] == 1, ch
    advertised = ch["samples"][0]["fix"]
    assert "tortoise_update_point" in advertised, advertised
    assert pid in advertised, advertised

    sdk.update_point(pid, validTo="2031-06-01")
    ch = _check(sdk.audit(point_kinds=[kind]), "inverted_validity_window")
    assert ch is None or ch["count"] == 0, ch


# ── Rendering ─────────────────────────────────────────────────────

def test_print_audit_renders_the_check(sdk, capsys):
    """``print_audit`` must render the new check like its siblings.

    Value that makes it fail: the check id and its HIGH block text in stdout.
    The fixture reaches it — an inverted window is planted, so the result's
    issues list is non-empty and the HIGH section prints the check id.
    """
    from tortoise.audit import audit_graph, print_audit

    kind = _kind()
    _plant(sdk, kind, valid_from="2030-06-01", valid_to="2020-06-01")
    print_audit(audit_graph(sdk._get_proj(), point_kinds=[kind]))
    out = capsys.readouterr().out
    assert "[inverted_validity_window]" in out
    assert "HIGH" in out


def test_paged_scan_counts_every_row_exactly_once(sdk, monkeypatch):
    """The scan is PAGED; every inverted row must still be counted exactly once.

    Value that makes it fail: an off-by-one in the page loop — a dropped or
    double-counted row at a page boundary yields 4 or 6 instead of 5.

    The fixture reaches it because the page size is monkeypatched DOWN to 2 and
    five inverted windows are planted, so the scan crosses TWO full boundaries
    and a short final page (2, 2, 1). With the un-paged scan this passes
    trivially, so the test is only meaningful once the loop exists — which is
    exactly the regression it guards.
    """
    import tortoise.audit as audit_mod

    monkeypatch.setattr(audit_mod, "CHECK8_PAGE", 2)
    kind = _kind()
    for _ in range(5):
        _plant(sdk, kind, valid_from="2030-06-01", valid_to="2020-06-01")
    # A well-formed row lands in the same pages and must not be counted.
    _plant(sdk, kind, valid_from="2020-06-01", valid_to="2030-06-01")

    report = sdk.audit(point_kinds=[kind])
    ch = _check(report, "inverted_validity_window")
    assert ch is not None, report
    assert ch["count"] == 5, ch
    assert len(ch["samples"]) == 5, ch
