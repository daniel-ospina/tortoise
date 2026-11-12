"""#9437 — the in-capture near-duplicate fold may not destroy the duplicate's evidence.

THE DEFECT THIS PINS, IN ONE SENTENCE
-------------------------------------
The M2 capture seam (`sdk._extract_session_llm`) resolves a repeated claim to
an earlier claim of the SAME capture, then runs
``MATCH (n:Point {id:$id}) DETACH DELETE n`` behind ONE guard — the
operator-wiring probe. Nothing looks at the node's own attachments, so the
duplicate's ``aboutObject``/``aboutSubject`` edges go with the node: the
SURVIVING claim is left carrying less support than the two claims together
held, and the receipt reports a ``dedup`` verdict about the CLAIM, never about
what the delete took.

The sibling path already refuses this shape — ``extractor_v2.classify_consolidation``
is guarded by PR #9424 so a NOOP fold that would drop an entity the candidate
names is REFUSED — and that PR's own review recorded this seam as its
remaining scope ("the capture seam's ``DETACH DELETE`` … discards the folded
node's own ``aboutObject`` edges … filed as #9437"). These tests are that
scope, end to end through the real seam.

WHAT IS PINNED
--------------
1. the duplicate's attachments + a free scalar span slot ARE carried onto the
   canonical before the copy is removed (RED before #9437: the edges died with
   the node and the span was never stored);
2. a per-occurrence slot the canonical already fills keeps the canonical's
   value (first-writer) and the difference is REPORTED — never dropped in
   silence (RED before #9437: the receipt said nothing at all);
3. evidence that cannot be carried (a source reference the canonical does not
   hold) REFUSES the fold and keeps both claims distinct, with the reason on
   the receipt (RED before #9437: the fold happened and the reference died);
4. a plain duplicate still folds, and the operator-wired duplicate still stays
   distinct — the change must not weaken folding in general.

The extractor stubs below are the seam's own contract (`_build_session_llm_extractor`),
the same way `tests/test_capture_session.py`'s #3778 fold pin drives it. They
exist because the in-tree M2 producer emits cleaned content only, so the
evidence class has no in-tree producer yet — which is why the loss was nil in
practice and the hole was structural.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise import capture_fold  # noqa: E402, RUF100
from tortoise.sdk import TortoiseSDK  # noqa: E402, RUF100

#: The paraphrase pair the committed band already treats as one claim
#: (same content pair as `test_capture_phase_d_dedup`), so the ONLY thing
#: that varies across the cases below is the EVIDENCE the duplicate carries.
CANON = "We should ship the web server first to unblock the mobile team."
DUP = "We should ship the web server first and unblock the mobile team now."
CONV = [{"role": "user", "content": CANON},
        {"role": "assistant", "content": DUP}]
SESSION = "sess_9437_fold"


@pytest.fixture(autouse=True)
def llm_extraction_provider(monkeypatch):
    """The offline mock seam — no network, no provider key (#822)."""
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    monkeypatch.delenv("TORTOISE_SESSION_EXTRACTOR", raising=False)


@pytest.fixture()
def sdk(tmp_path):
    return TortoiseSDK(db_path=str(tmp_path / "t.db"))


class _EvidenceExtractor:
    """Two occurrences of ONE claim; the duplicate carries `evidence`."""

    version = "m2-evidence@0"

    def __init__(self, evidence: dict | None = None,
                 canon_evidence: dict | None = None,
                 dup_source: str | None = None):
        self._evidence = evidence or {}
        self._canon_evidence = canon_evidence or {}
        self._dup_source = dup_source

    def run(self, transcript, source_id, api):
        api.add_point(CANON, {"source": source_id}, extractedFrom=source_id,
                      **self._canon_evidence)
        api.add_point(DUP, {"source": source_id},
                      extractedFrom=self._dup_source or source_id,
                      **self._evidence)


def _extract(sdk, monkeypatch, *, evidence=None, canon_evidence=None,
             dup_source=None):
    monkeypatch.setattr(
        "tortoise.sdk._build_session_llm_extractor",
        lambda: _EvidenceExtractor(evidence, canon_evidence, dup_source))
    proj = sdk._get_proj()
    proj.g.query("MERGE (s:Session {id:$sid})", params={"sid": SESSION})
    return sdk._extract_session_llm(CONV, SESSION, "2026-11-12T00:00:00+00:00")


def _claim_rows(proj) -> list:
    """The capture's own claim nodes (not turns, not operators, not objects)."""
    return proj.g.query(
        "MATCH (p:Point) WHERE NOT p.id CONTAINS '_t' "
        "AND p.is_operator <> true RETURN p.id, p.content, p.quote, "
        "p.span_start, p.span_end, p.extractedFrom").result_set


def _about_targets(proj, point_id: str) -> set[str]:
    rows = proj.g.query(
        "MATCH (:Point {id:$pid})-[r:aboutObject|aboutSubject]->(t) "
        "RETURN type(r), t.name", params={"pid": point_id}).result_set
    return {f"{rel}:{name}" for rel, name in rows}


# ── 1) the evidence is CARRIED ─────────────────────────────────────────────


def test_a_fold_carries_the_duplicates_attachments_span_and_provenance(
        sdk, monkeypatch):
    """The duplicate's `aboutObject` link, its free-slot span evidence and its
    provenance are on the canonical AFTER the copy is removed.

    RED before #9437 — mutation: restore the bare `DETACH DELETE`. The
    duplicate's `aboutObject` edge dies with the node (`_about_targets` is
    empty) and the canonical stores no span at all.
    """
    sdk.create_object("Acme")          # so the cascade resolves an :Object
    extracted, meta = _extract(
        sdk, monkeypatch,
        evidence={"aboutEntities": ["Acme"], "quote": "the duplicate's words",
                  "span_start": 30, "span_end": 62})
    assert meta["errors"] == [], meta["errors"]
    assert [e["dedup"] for e in extracted] == ["new", "rephrase_linked"], extracted
    assert len({e["id"] for e in extracted}) == 1, extracted

    proj = sdk._get_proj()
    canonical = extracted[0]["id"]
    claims = _claim_rows(proj)
    assert len(claims) == 1, f"the copy was removed — expected 1 claim: {claims}"

    # (a) the claim's SUPPORT — the link the fold used to destroy.
    assert _about_targets(proj, canonical) == {"aboutObject:Acme"}, \
        "the duplicate's own attachment did not survive the fold"
    # (b) a FREE scalar slot is filled from the duplicate (no loss, no overwrite).
    _id, _content, quote, span_start, span_end, _src = claims[0]
    assert (quote, span_start, span_end) == ("the duplicate's words", 30, 62), \
        f"the duplicate's span evidence was not carried: {claims[0]}"
    # (c) provenance: the canonical keeps — and is shown to keep — its source.
    assert claims[0][5] == f"session:{SESSION}", claims[0]
    assert proj.g.query(
        "MATCH (:Point {id:$pid})-[:extractedFrom]->(s) RETURN count(s)",
        params={"pid": canonical}).result_set[0][0] == 1
    # (d) a fold is a destructive outcome and must be REPORTED as one.
    assert any("folded into canonical" in w for w in meta["warnings"]), meta["warnings"]
    assert any("attachment link" in w for w in meta["warnings"]), meta["warnings"]


def test_a_plain_duplicate_with_no_evidence_still_folds(sdk, monkeypatch):
    """Anti-regression: the fold still happens when there is nothing to carry.

    The guard must not become "never fold" — a duplicate with no attachments
    and no per-occurrence evidence is exactly the case the feature exists for.
    """
    extracted, meta = _extract(sdk, monkeypatch)
    assert meta["errors"] == [], meta["errors"]
    assert [e["dedup"] for e in extracted] == ["new", "rephrase_linked"], extracted
    assert len(_claim_rows(sdk._get_proj())) == 1, _claim_rows(sdk._get_proj())
    assert any("folded into canonical" in w for w in meta["warnings"]), meta["warnings"]
    assert any("no evidence of its own" in w for w in meta["warnings"]), meta["warnings"]


# ── 2) a slot the canonical owns is reported, never silently overwritten ──


def test_a_conflicting_slot_keeps_the_canonicals_and_reports_the_difference(
        sdk, monkeypatch):
    """First-writer: the canonical's own quote/span stay, and the duplicate's
    differing values are reported as a residual rather than dropped in silence.

    This is the shape `tests/test_capture_session.py`'s #3778 pin already
    drives (a duplicate carrying `q second`/`turn-2` against `q first`/
    `turn-1`) — there the fold is right and the receipt is what must state the
    difference. RED before #9437: the receipt said nothing, so a fold that
    kept one occurrence's span was indistinguishable from one that carried it.
    """
    extracted, meta = _extract(
        sdk, monkeypatch,
        canon_evidence={"quote": "first words", "span_start": 0, "span_end": 29},
        evidence={"quote": "second words", "span_start": 30, "span_end": 62})
    assert meta["errors"] == [], meta["errors"]
    assert [e["dedup"] for e in extracted] == ["new", "rephrase_linked"], extracted
    claims = _claim_rows(sdk._get_proj())
    assert len(claims) == 1, claims
    assert (claims[0][2], claims[0][3], claims[0][4]) == ("first words", 0, 29), \
        f"the canonical's own evidence must not be overwritten: {claims}"
    assert any("kept the canonical's own" in w for w in meta["warnings"]), meta["warnings"]
    for key in ("quote", "span_start", "span_end"):
        assert any(key in w for w in meta["warnings"]), (key, meta["warnings"])


# ── 3) evidence that cannot be carried REFUSES the fold ────────────────────


def test_a_fold_that_cannot_carry_a_source_reference_is_refused(
        sdk, monkeypatch):
    """A source reference the canonical does not hold cannot be MOVED (a
    Point's `extractedFrom` is a node field, and §4.3 forecloses writing one
    onto a committed row outside a lifecycle event), so the copy is not
    redundant: the fold is refused and both claims stay, each with its own
    evidence, with the reason on the receipt.

    RED before #9437 — the fold deleted the duplicate and its reference with
    it: one node, `rephrase_linked`, and the second source unreachable.
    """
    sdk.create_object("Acme")
    extracted, meta = _extract(
        sdk, monkeypatch,
        evidence={"aboutEntities": ["Acme"]},
        dup_source=f"session:{SESSION}#turn-2")
    assert meta["errors"] == [], meta["errors"]
    assert [e["dedup"] for e in extracted] == ["new", "new"], extracted
    claims = _claim_rows(sdk._get_proj())
    assert len(claims) == 2, f"the duplicate must survive a refused fold: {claims}"
    # each occurrence keeps ITS provenance, and the duplicate keeps its link
    assert {c[5] for c in claims} == {f"session:{SESSION}",
                                      f"session:{SESSION}#turn-2"}, claims
    assert _about_targets(sdk._get_proj(), extracted[1]["id"]) == \
        {"aboutObject:Acme"}, "the kept- distinct duplicate keeps its own link"
    assert any("kept distinct" in w for w in meta["warnings"]), meta["warnings"]
    assert any("source reference" in w for w in meta["warnings"]), meta["warnings"]


# ── 4) fail-closed: an unreadable node is not an empty one ────────────────


class _RaisingGraph:
    def query(self, *a, **kw):
        raise RuntimeError("graph refused the read")


class _RaisingProj:
    g = _RaisingGraph()


def test_an_unreadable_duplicate_refuses_the_fold():
    """`read_fold_evidence` answers None — not "nothing there" — when the read
    fails, and `carry_fold_evidence` refuses rather than authorising a delete
    over evidence it could not see."""
    assert capture_fold.read_fold_evidence(_RaisingProj(), "pt_x") is None
    out = capture_fold.carry_fold_evidence(_RaisingProj(), "pt_x", "pt_y")
    assert out["ok"] is False and out["reason"] == "unreadable", out
    assert "could not be read" in out["blocked"], out


# ── 5) the pre-existing guard is untouched ────────────────────────────────


def test_the_operator_wired_duplicate_still_stays_distinct(sdk, monkeypatch):
    """The `engaged` branch this change sits beside still refuses: an
    operator-wired duplicate would orphan a real relation of ITS occurrence."""
    monkeypatch.setenv("TORTOISE_SESSION_EXTRACTOR", "m2")
    wired = ("We should ship the HTTP server first because the mobile team "
             "is blocked.")
    res = sdk._capture_session([
        {"role": "user", "content": wired},
        {"role": "assistant", "content": wired},
        {"role": "user", "content": "ok"},
    ])
    assert [p["dedup"] for p in res["points"]] == ["new", "new"], res["points"]
    assert any("not deduped" in w for w in res["warnings"]), res["warnings"]
    assert len(_claim_rows(sdk._get_proj())) == 2, "operator-wired duplicate kept"


# ── 6) the two slot shapes that are not independent slots ─────────────────


def test_a_date_is_one_slot_and_the_fold_never_gives_it_two_spellings(
        sdk, monkeypatch):
    """`when` and `validFrom` are ONE date slot (ONTOLOGY §4.7), so the fold
    must not fill `validFrom` with the duplicate's date while the canonical
    already records its own under the raw `when` spelling.

    Measured before the fix (this exact fixture): the node held
    `when=2026-08-01` AND `validFrom=2026-08-02` — one slot, two values — and
    the receipt reported `copied validFrom` as a success.

    RED on the pre-fix carry: `canonical.get("validFrom", canonical.get("when"))`
    never evaluated its default (`_canonical_view` always returns both keys), so
    the canonical's own `when` was invisible to the occupancy check.
    """
    extracted, meta = _extract(
        sdk, monkeypatch,
        canon_evidence={"when": "2026-08-01"},
        evidence={"when": "2026-08-02"})
    assert meta["errors"] == [], meta["errors"]
    assert [e["dedup"] for e in extracted] == ["new", "rephrase_linked"], extracted
    proj = sdk._get_proj()
    row = proj.g.query(
        "MATCH (p:Point {id:$pid}) RETURN p.when, p.validFrom",
        params={"pid": extracted[0]["id"]}).result_set
    assert row and row[0][0] == "2026-08-01", row
    assert row[0][1] is None, f"one date, two spellings: {row}"
    assert any("when" in w for w in meta["warnings"]), meta["warnings"]


def test_the_occurrence_address_is_carried_whole_or_not_at_all(
        sdk, monkeypatch):
    """`quote` + `source_turn_id` + the span are ONE address. When the
    canonical holds any of it, the fold must not graft the duplicate's span
    onto the canonical's quote and turn — that composes offsets against text
    the survivor is not quoting, and `validate_span` cannot catch it.

    Measured before this shape: the survivor read `quote='q first'`,
    `source_turn_id='turn-1'`, `span_start=40, span_end=72` — turn-2's
    offsets under turn-1's quote.
    """
    extracted, meta = _extract(
        sdk, monkeypatch,
        canon_evidence={"quote": "q first", "source_turn_id": "turn-1"},
        evidence={"span_start": 40, "span_end": 72})
    assert [e["dedup"] for e in extracted] == ["new", "rephrase_linked"], extracted
    proj = sdk._get_proj()
    row = proj.g.query(
        "MATCH (p:Point {id:$pid}) RETURN p.quote, p.source_turn_id, "
        "p.span_start, p.span_end", params={"pid": extracted[0]["id"]}).result_set
    assert tuple(row[0]) == ("q first", "turn-1", None, None), \
        f"a composed address is not an address: {row}"
    for key in ("span_start", "span_end"):
        assert any(key in w for w in meta["warnings"]), (key, meta["warnings"])


# ── 7) the refusal tokens, one construction each ──────────────────────────

#: The nine columns the evidence read selects, in query order:
#: extractedFrom, extractedFromAll, quote, span_start, span_end,
#: source_turn_id, search_keys, when, validFrom.
_DUP_ROW = ("session:s", [], None, None, None, None, None, None, None)
_DUP_ROW_QUOTED = ("session:s", [], "q dup", None, None, None, None, None, None)
_CANON_ROW = ("session:s", [], None, None, None, None, None, None, None)


class _FakeGraph:
    """Answers the four reads the carry issues, by query SHAPE.

    Deliberately not a graph stub that pretends everything worked: the point of
    most of these cases is that the fake CANNOT answer, so the carry has to
    refuse. A stub that always succeeds would pin nothing.
    """

    def __init__(self, *, dup_row=_DUP_ROW, edges=(), carried=(0, None),
                 canon_row=_CANON_ROW, raise_on=frozenset()):
        self.dup_row = dup_row
        self.edges = list(edges)
        self.carried = carried
        self.canon_row = canon_row
        self.raise_on = set(raise_on)

    def query(self, q, **kw):
        if any(marker in q for marker in self.raise_on):
            raise RuntimeError("graph refused the read")
        # ⚠️ Order matters: the EDGES read also starts `MATCH (d:Point`, so it
        # is keyed on its own shape and checked before the property read.
        if "-[r]->" in q:
            return SimpleNamespace(result_set=self.edges)
        if "d.extractedFrom" in q:
            return SimpleNamespace(result_set=[self.dup_row])
        if "c.extractedFrom" in q:
            return SimpleNamespace(result_set=[self.canon_row])
        if "head(collect(r.confidence))" in q:
            return SimpleNamespace(result_set=[list(self.carried)])
        return SimpleNamespace(result_set=[])


class _FakeProj:
    def __init__(self, graph):
        self.g = graph


class _FakeSDK:
    """A write door that records the call and does NOT write the graph —
    exactly the silent-success `update_point` gives for an absent id."""

    def __init__(self, *, raises: Exception | None = None):
        self.raises = raises
        self.calls: list[dict] = []

    def update_point(self, point_id, **props):
        self.calls.append({"id": point_id, **props})
        if self.raises:
            raise self.raises
        return {}


def test_an_unaddressable_attachment_refuses():
    """An `about*` link whose target has no id cannot be re-pointed
    (`link_entity` MERGEs on `{id:$id}` and would mint a DIFFERENT node), so
    the fold must keep the duplicate. Token: `unaddressable`."""
    proj = _FakeProj(_FakeGraph(edges=[("aboutObject", "Object", "", None)]))
    out = capture_fold.carry_fold_evidence(proj, "pt_dup", "pt_canon")
    assert out["ok"] is False and out["reason"] == "unaddressable", out
    assert "cannot be carried" in out["blocked"], out


def test_a_link_that_does_not_land_refuses():
    """A link is counted, never assumed: `link_entity` MERGEs and creates
    nothing when an endpoint is absent. Token: `link_not_carried`."""
    proj = _FakeProj(_FakeGraph(
        edges=[("aboutObject", "Object", "obj_1", None)], carried=(0, None)))
    out = capture_fold.carry_fold_evidence(proj, "pt_dup", "pt_canon")
    assert out["ok"] is False and out["reason"] == "link_not_carried", out
    assert "did not land" in out["blocked"], out


def test_an_attachment_already_on_the_canonical_reports_the_grading():
    """`link_entity` leaves an existing edge's `confidence` alone (#1370), so
    the duplicate's grade cannot ride it — the carry reports that residual
    instead of claiming it carried the grading."""
    proj = _FakeProj(_FakeGraph(
        edges=[("aboutObject", "Object", "obj_1", 0.7)], carried=(1, None)))
    out = capture_fold.carry_fold_evidence(proj, "pt_dup", "pt_canon")
    assert out["ok"] is True, out
    assert out["carried"] == 1, out
    assert any("confidence of aboutObject" in r for r in out["residual"]), out


def test_a_slot_write_that_raises_or_has_no_door_refuses():
    """Both arms of `slot_write_failed`: the write door raising, and no door
    at all. Token: `slot_write_failed`."""
    proj = _FakeProj(_FakeGraph(dup_row=_DUP_ROW_QUOTED))
    out = capture_fold.carry_fold_evidence(
        proj, "pt_dup", "pt_canon",
        sdk=_FakeSDK(raises=RuntimeError("no such point")))
    assert out["ok"] is False and out["reason"] == "slot_write_failed", out
    assert "no such point" in out["blocked"], out

    out = capture_fold.carry_fold_evidence(proj, "pt_dup", "pt_canon", sdk=None)
    assert out["ok"] is False and out["reason"] == "slot_write_failed", out
    assert "no SDK write door" in out["blocked"], out


def test_a_silent_slot_write_is_detected_by_the_read_back():
    """The write returned cleanly and NOTHING landed — the absent-canonical
    shape `update_point` reports as success. The carry must refuse rather than
    let the caller delete a duplicate whose evidence never arrived."""
    sdk = _FakeSDK()
    proj = _FakeProj(_FakeGraph(dup_row=_DUP_ROW_QUOTED))  # canonical stays free
    out = capture_fold.carry_fold_evidence(proj, "pt_dup", "pt_canon", sdk=sdk)
    assert sdk.calls == [{"id": "pt_canon", "quote": "q dup"}], sdk.calls
    assert out["ok"] is False and out["reason"] == "slot_write_failed", out
    assert "did not land" in out["blocked"], out


def test_an_internal_error_is_a_refusal_never_an_exception():
    """The caller sits inside the capture's single per-loop `try`, so a
    propagated error would abandon every remaining statement's resolution.
    A raise anywhere inside the carry becomes `internal_error` — a refused
    fold, the safe answer in front of a destructive act."""
    proj = _FakeProj(_FakeGraph(
        edges=[("aboutObject", "Object", "obj_1", None)],
        raise_on={"head(collect(r.confidence))"}))
    out = capture_fold.carry_fold_evidence(proj, "pt_dup", "pt_canon")
    assert out["ok"] is False and out["reason"] == "internal_error", out
    assert "graph refused the read" in out["blocked"], out
