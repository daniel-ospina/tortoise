"""#7818 — the `get_historical_knowledge` reply contract + the §4.7 coverage boundary.

Two defects, one root, and two record gaps:

  1. **The `ambiguous` reply omitted `found`.** `get_historical_knowledge` set
     `ambiguous`/`candidates` and returned WITHOUT a `found` key, so a consumer
     reading ``res.get("found")`` read a genuine two-candidate ambiguity as
     *"nothing was true at that instant"* — the silent wrong answer the
     ambiguity signal exists to prevent. The reply is now uniform: every shape
     carries ``status`` (``"found"`` | ``"ambiguous"`` | ``"absent"``) and a
     boolean ``found``, and the ambiguous shape sets ``found=True`` (a
     candidate WAS found — just not a unique one).
  2. **The shared-boundary instant of a WELL-FORMED forward chain reads
     `ambiguous`.** ``_covers`` is closed at both ends, so for the shape every
     dated ``supersede_point`` writes — ``P[vP, vC] ← C[vC, ∞)`` — the instant
     ``t == vC`` is covered by BOTH windows. That is the closed-interval
     consequence, NOT corruption, and it is pinned here so a future change of
     representation has to update this file consciously. The instant is also
     entry-point-dependent: entering from ``P`` (the CORRECTS walk is
     backwards-only) resolves to ``P`` alone.
  3. **ONTOLOGY §4.7 stated no coverage rule at all**, so the closed reading
     the code implements and the half-open reading the ``validTo`` row's prose
     describes coexisted silently. §4.7 now states the boundary, its shared
     instant, and the OPEN decision between the two readings (#7818). The
     anti-drift pin for that record lives in ``tests/test_7818_ontology_record.py``
     — hermetic and registered in ``core`` + ``tier1``, so a docs-only ONTOLOGY
     edit runs it (this file is docker-lane and `sdk`-registered only).
  4. **The reply-shape change was not recorded in the surface manifest.**
     ``CONTRIBUTING.md`` requires every added response field in the
     ``response_fields`` block of ``config/surface-manifest.yml``; no gate can
     see an unrecorded field, so it is a reviewing obligation. The rows are
     added (and the two stale #5361 byte-identity claims corrected), and
     ``test_surface_manifest_records_the_status_and_found_keys`` pins them.

⚠️ **Scope.** This change does NOT pick a representation. Flipping ``_covers``
to half-open ``[vF, vT)`` is a global boundary-semantics change (it also makes a
zero-length window genuinely empty, colliding with #5361's well-formed-empty
question) and would fail the closed coverage sets pinned by
``tests/test_validity_window_contract_5374.py``. That trade is the owner's —
recorded as an open decision on #7818.

Class-B test doctrine — every test answers BOTH:
  (1) *What value makes this test fail?*  Named per test below. For the
      contract tests, the pre-fix code (no `found` key on the ambiguous reply,
      no `status` anywhere) reds them. For the boundary tests, the value is the
      shared instant itself: a representation that resolves the boundary to one
      candidate reds them, which is the point — the pin must be updated
      deliberately.
  (2) *Does the fixture reach it?*  Yes — points and windows are persisted
      through the public writers (``create_point`` / ``supersede_point``), and
      the reply is read back through ``get_historical_knowledge``.

Runnable (docker lane; URI-less it needs TORTOISE_TEST_CARVE_OUT=1):
  TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' \\
    pytest tests/test_7818_boundary_ambiguity_contract.py -v
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: I001
import yaml
from tortoise.sdk import TortoiseSDK

REPO = Path(__file__).resolve().parent.parent
MANIFEST = REPO / "config" / "surface-manifest.yml"

# The contiguous chain's shared boundary: P[06-01, 06-10] ← C[06-10, ∞).
P_START = "2026-06-01"
BOUNDARY = "2026-06-10"


@pytest.fixture
def sdk():
    """SDK with a temp database. Closed and reclaimed after the test."""
    db_path = os.path.join(
        tempfile.mkdtemp(prefix="tortoise_7818_test_"), "test.db"
    )
    sdk = TortoiseSDK(db_path)
    yield sdk
    sdk.close()
    shutil.rmtree(os.path.dirname(db_path), ignore_errors=True)


def _make_point(sdk: TortoiseSDK, content: str, **kw):
    return sdk.create_point("statement", content, **kw)


def _forward_chain(sdk: TortoiseSDK) -> tuple[str, str]:
    """A WELL-FORMED contiguous chain: returns (predecessor_id, successor_id).

    ``supersede_point`` with an explicit agreeing ``valid_from`` stamps
    ``P.validTo == C.validFrom == BOUNDARY`` (the contiguity D2 promises), and
    writes ``(C)-[:CORRECTS]->(P)``.
    """
    old = _make_point(sdk, "7818 claim v1", validFrom=P_START)
    new = _make_point(sdk, "7818 claim v2", validFrom=BOUNDARY)
    sdk.supersede_point(old["id"], new["id"], valid_from=BOUNDARY)
    return old["id"], new["id"]


# ── the reply contract: `found` + `status` on every shape ────────────────

def test_ambiguous_reply_carries_found_true_and_status(sdk):
    """A two-candidate ambiguity is NOT shaped like honest absence.

    (1) *What value makes this test fail?*  The pre-fix reply: it returned
        ``{ambiguous: true, candidates: [...]}`` with NO ``found`` key, so
        ``out["found"]`` raises ``KeyError`` and ``out.get("found")`` is
        ``None`` — a falsy read indistinguishable from absence. A fix that sets
        ``found=False`` here also reds: the whole point is that a candidate WAS
        found.
    (2) *Does the fixture reach it?*  Yes — two hand-planted overlapping
        windows (``P``'s window still covers a later date), the shape
        ``test_restore_ambiguous_overlapping_windows`` uses.
    """
    a = _make_point(sdk, "7818 overlap A", validFrom="2026-06-01",
                    validTo="2026-06-30")
    b = _make_point(sdk, "7818 overlap B", validFrom="2026-06-15")
    sdk._get_proj().g.query(
        "MATCH (a:Point {id:$aid}), (b:Point {id:$bid}) "
        "CREATE (b)-[:CORRECTS]->(a)",
        params={"aid": a["id"], "bid": b["id"]})

    out = sdk.get_historical_knowledge(b["id"], "2026-06-20")

    assert out.get("ambiguous") is True
    assert out["status"] == "ambiguous"
    assert out["found"] is True, (
        "an ambiguous reply must not read as absence — `found` is True "
        "(a candidate WAS found; `valid_point` is what is missing)")
    assert "valid_point" not in out
    assert {c["id"] for c in out["candidates"]} == {a["id"], b["id"]}


def test_found_and_absent_replies_carry_a_status(sdk):
    """The three shapes are distinguishable by `status`, and `found` is boolean.

    (1) *What value makes this test fail?*  The pre-fix replies carry no
        ``status`` at all (``out["status"]`` raises), so this reds on the
        contract gap rather than on coverage.
    (2) *Does the fixture reach it?*  Yes — one covering instant (status
        ``"found"``) and one instant before every window (status ``"absent"``),
        both read from a persisted chain.
    """
    old, new = _forward_chain(sdk)

    inside = sdk.get_historical_knowledge(new, "2026-06-05")
    assert inside["status"] == "found"
    assert inside["found"] is True
    assert inside["valid_point"]["id"] == old

    before = sdk.get_historical_knowledge(new, "2020-01-01")
    assert before["status"] == "absent"
    assert before["found"] is False
    assert "valid_point" not in before


def test_missing_point_and_no_coverage_are_absent(sdk):
    """The early-return and no-coverage paths also carry the uniform contract.

    (1) *What value makes this test fail?*  Both returned ``{found: false, ...}``
        with no ``status``; the assertion reds on ``out["status"]``.
    (2) *Does the fixture reach it?*  Yes — an id that was never written (the
        missing-point early return), and a real point read outside its own
        window with no CORRECTS chain (the no-coverage fall-through).
    """
    missing = sdk.get_historical_knowledge("pt_7818_does_not_exist", BOUNDARY)
    assert missing["found"] is False
    assert missing["status"] == "absent"

    lone = _make_point(sdk, "7818 lone claim", validFrom=P_START,
                       validTo=BOUNDARY)
    no_coverage = sdk.get_historical_knowledge(lone["id"], "2020-01-01")
    assert no_coverage["found"] is False
    assert no_coverage["status"] == "absent"


# ── the coverage boundary of a WELL-FORMED forward chain ────────────────

def test_forward_chain_shared_boundary_reads_ambiguous_from_the_successor(sdk):
    """The #7818 core: a contiguous chain's shared instant has TWO candidates.

    This is the closed-interval consequence, and it is deliberately pinned: a
    representation change (half-open ``[vF, vT)``, or a tie-break) must update
    THIS test, not slip past it.

    (1) *What value makes this test fail?*  The instant ``BOUNDARY``. Under the
        closed ``_covers`` both windows cover it, so the reply is ambiguous with
        two candidates. Pre-fix the reply also omitted ``found``, so the
        ``found is True`` assertion reds first — which is the contract half of
        this issue.
    (2) *Does the fixture reach it?*  Yes — the chain is written through
        ``supersede_point`` with an agreeing ``valid_from`` (contiguity), and
        the instant is exactly ``P.validTo == C.validFrom``, asserted below so
        the fixture cannot silently stop meeting at a point.
    """
    old, new = _forward_chain(sdk)

    props_old = sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) RETURN n.validTo",
        params={"id": old}).result_set
    assert props_old and props_old[0][0] == BOUNDARY, (
        "fixture no longer meets at a shared instant — contiguity changed")

    out = sdk.get_historical_knowledge(new, BOUNDARY)

    assert out["status"] == "ambiguous"
    assert out.get("ambiguous") is True
    assert out["found"] is True
    assert "valid_point" not in out
    assert {c["id"] for c in out["candidates"]} == {old, new}, (
        "the shared instant is covered by the predecessor AND the successor")


def test_forward_chain_shared_boundary_is_entry_point_dependent(sdk):
    """The same instant entered from the PREDECESSOR resolves to it alone.

    The CORRECTS walk is backwards-only, so ``P`` has no successor in its chain
    and the boundary resolves to ``P``. Pinned because it is the reason a
    chain-local tie-break ("prefer the successor") is not a route out of #7818:
    it would make the answer depend on the id you enter from.

    (1) *What value makes this test fail?*  The instant ``BOUNDARY`` read from
        ``P``. A representation whose boundary resolves to the successor from
        BOTH entries reds here (``valid_point`` would be ``C``); so does one
        that walks CORRECTS forwards.
    (2) *Does the fixture reach it?*  Yes — ``P`` is persisted with
        ``validTo == BOUNDARY`` and has no outgoing CORRECTS, so the walk stops
        after one entry.
    """
    old, _new = _forward_chain(sdk)

    out = sdk.get_historical_knowledge(old, BOUNDARY)

    assert out["status"] == "found"
    assert out["found"] is True
    assert out["valid_point"]["id"] == old
    assert out.get("ambiguous") is not True
    assert len(out["chain"]) == 1, (
        "the walk from the predecessor must not reach the successor")
    assert out["chain"][0]["id"] == old


# ── the surface-manifest record (§ P2 of the #8575 review) ───────────────

def test_surface_manifest_records_the_status_and_found_keys() -> None:
    """The reply-shape change is RECORDED, per ``CONTRIBUTING.md``.

    ``status`` (every shape) and ``found``-on-``ambiguous`` are response-field
    additions, and CONTRIBUTING requires each in the ``response_fields`` block
    of ``config/surface-manifest.yml`` — no gate compares response BODIES, so
    an unrecorded field is caught by review alone. The two pre-existing #5361
    rows also claimed reply behaviour that #7818 changed; those claims are
    pinned out here too.

    (1) *What value makes this test fail?*  The pre-fix manifest, which had no
        row for either key, and a #5361 row still claiming the found/ambiguous
        replies are byte-identical/untouched.
    (2) *Does the fixture reach it?*  Yes — the committed manifest is read and
        the exact (response, field) rows asserted.
    """
    doc = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    entries = {
        (e.get("response"), e.get("field")): e
        for e in (doc.get("response_fields") or [])
        if isinstance(e, dict)
    }
    status = entries.get(("sdk:get_historical_knowledge", "status"))
    assert status is not None, (
        "the `status` reply key is not recorded in response_fields — an "
        "unrecorded field has no approval behind it (CONTRIBUTING.md)")
    emitted = status["emitted_when"]
    for value in ("found", "ambiguous", "absent"):
        assert value in emitted, (
            f"the status row does not record the {value!r} member")
    assert "every" in emitted.lower(), (
        "status is emitted on EVERY shape, not one branch")

    found = entries.get(("sdk:get_historical_knowledge", "found"))
    assert found is not None, (
        "the `found`-on-the-ambiguous-shape change is not recorded")
    assert "ambiguous" in found["emitted_when"], (
        "the ambiguous-shape `found` change is the point of the row")

    stale_byte_identity = entries[
        ("sdk:get_historical_knowledge", "malformed")]["unchanged_when_off"]
    assert "found and ambiguous replies are byte-identical" not in stale_byte_identity, (
        "#7818 changes the found and ambiguous replies, so this #5361 claim is "
        "false — scope it to this key")
    stale_untouched = entries[
        ("sdk:get_historical_knowledge", "nearest.malformed")]["unchanged_when_off"]
    assert "found and ambiguous replies are untouched" not in stale_untouched, (
        "#7818 touches every shape, so this #5361 claim is false")

