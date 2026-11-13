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
from tortoise import sdk as sdk_mod  # noqa: E402, RUF100
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
    assert any("attachment link(s) present" in w for w in meta["warnings"]), meta["warnings"]
    # …including WHICH slots it filled: "no evidence" and "copied quote" are
    # different receipts, and without this assertion the `copied` clause could
    # be deleted from `sdk.py` with the suite still green (measured: the round-5
    # review found this clause unpinned).
    assert any("copied quote, span_end, span_start" in w
               for w in meta["warnings"]), meta["warnings"]


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
    assert any("no attachments" in w for w in meta["warnings"]), meta["warnings"]
    said = [w for w in meta["warnings"] if "no per-occurrence evidence" in w]
    assert said, meta["warnings"]
    # …and the clause says what DID go with the node: the row's write history.
    # Unqualified, it asserted an absence over a node whose `createdAt` died.
    assert "beyond its write history" in said[0], said[0]
    assert "createdAt" in said[0], said[0]
    # …and the OTHER class the delete takes without carrying it: the duplicate's
    # outbound `extractedFrom` edge may die (the canonical's equivalent survives)
    # and can carry an attribute this fold does not read. The clause names the
    # declared bound instead of asserting an absence over it.
    assert "#12146" in said[0], said[0]


def test_a_pre_existing_link_is_reported_present_but_not_written(sdk, monkeypatch):
    """`carried` counts links PRESENT; the receipt must also say how many THIS
    fold wrote — the clause a reader needs to tell "the carry added support"
    from "the support was already there".

    RED if the `({created} written by this fold)` clause is deleted from
    `sdk.py`: the link pre-exists on the canonical, so `link_entity`
    short-circuits it and `created` is 0 while `carried` is 1 — the only shape
    that prints the clause.
    """
    sdk.create_object("Acme")
    extracted, meta = _extract(
        sdk, monkeypatch,
        canon_evidence={"aboutEntities": ["Acme"]},
        evidence={"aboutEntities": ["Acme"]})
    assert meta["errors"] == [], meta["errors"]
    assert [e["dedup"] for e in extracted] == ["new", "rephrase_linked"], extracted
    assert _about_targets(sdk._get_proj(), extracted[0]["id"]) == \
        {"aboutObject:Acme"}, _about_targets(sdk._get_proj(), extracted[0]["id"])
    assert any(
        "1 attachment link(s) present (0 written by this fold)" in w
        for w in meta["warnings"]), meta["warnings"]
    # ⚠️ The NEGATIVE half: `carried` alone must suppress the "no per-occurrence
    # evidence" clause. Deleting `not carried` from that conjunction left this
    # test green while the receipt printed both clauses in one sentence.
    assert not any("no per-occurrence evidence of its own" in w
                   for w in meta["warnings"]), meta["warnings"]


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
    assert any("did not carry" in w for w in meta["warnings"]), meta["warnings"]
    for key in ("quote", "span_start", "span_end"):
        assert any(key in w for w in meta["warnings"]), (key, meta["warnings"])
    # ⛔ …and the receipt must NOT also claim there was nothing to carry: a
    # duplicate whose evidence DIFFERS has evidence. RED on the previous head,
    # which printed both clauses in one sentence.
    assert not any("no per-occurrence evidence of its own" in w
                   for w in meta["warnings"]), meta["warnings"]


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

#: The columns the evidence read selects, in query order: extractedFrom,
#: extractedFromAll, quote, span_start, span_end, source_turn_id, search_keys,
#: when, validFrom, confidence, pointKind.
_NO_EVIDENCE = ("session:s", [], None, None, None, None, None, None, None,
                None, None, None)
_DUP_ROW = _NO_EVIDENCE
_DUP_ROW_QUOTED = ("session:s", [], "q dup", None, None, None, None, None,
                   None, None, None, None)
_CANON_ROW = _NO_EVIDENCE


class _FakeGraph:
    """Answers the four reads the carry issues, by query SHAPE.

    Deliberately not a graph stub that pretends everything worked: the point of
    most of these cases is that the fake CANNOT answer, so the carry has to
    refuse. A stub that always succeeds would pin nothing.
    """

    #: Column order of `_EVIDENCE_SELECT` + the two reported fields, so a test
    #: can "write" a slot and have the read-back see it.
    _COLUMNS = ("extractedFrom", "extractedFromAll", "quote", "span_start",
                "span_end", "source_turn_id", "search_keys", "when",
                "validFrom", "eventId", "confidence", "pointKind")

    def __init__(self, *, dup_row=_DUP_ROW, edges=(), carried=(0, None),
                 canon_row=_CANON_ROW, raise_on=frozenset(),
                 dup_props=None, inbound=(), canon_rows=None,
                 dup_status=None, dup_is_operator=None, dup_labels=("Point",),
                 dup_prov_source=None):
        self.dup_row = dup_row
        self.edges = list(edges)
        self.carried = carried
        self.canon_row = canon_row
        #: The duplicate's INBOUND edges — a separate fixture because the two
        #: reads are distinguished by their pattern, not by the `-[r]->` shape
        #: they share.
        self.inbound = list(inbound)
        #: Two-or-more rows for the canonical id (`_canonical_view` refuses).
        self.canon_rows = canon_rows
        #: The duplicate's `status` / `is_operator` — the two fields the read
        #: gained when they stopped being silently structural.
        self.dup_status = dup_status
        self.dup_is_operator = dup_is_operator
        #: The duplicate's own LABELS — the one class `properties(d)` cannot see.
        self.dup_labels = tuple(dup_labels)
        #: The duplicate's own `provenanceSource` — the OTHER provenance pointer,
        #: read as a REF, so a differing one must refuse.
        self.dup_prov_source = dup_prov_source
        #: The canonical's own `extractedFrom` targets — a provenance edge on the
        #: duplicate may only die when the canonical holds the same one.
        self.canon_prov_targets = []
        self.raise_on = set(raise_on)
        #: The DUPLICATE's whole property map — the `properties(d)` column the
        #: open-set report reads. Kept OUT of the shared row tuples so every
        #: fixed-width fixture above stays fixed-width; `None` means "no map
        #: came back", which must read as an empty open set, not as a loss.
        self.dup_props = dup_props

    @staticmethod
    def _to_query_row(row, status, is_operator, prov_source=None):
        """The fixture's 12-tuple → the QUERY's column order.

        `_COLUMNS` is the fixture order (evidence×10, confidence, pointKind);
        the Cypher selects evidence×10, `status`, `is_operator`, confidence,
        pointKind (and `properties(d)` for the duplicate). Keeping the fixtures
        at the shorter shape means a test can write `_CANON_ROW` without
        knowing the query's column count — but the fake must translate, or a
        column added to the SELECT silently shifts every field one place (which
        is what `internal_error: not enough values to unpack` measured).
        """
        base = tuple(row)
        assert len(base) == 12, f"fixture row is {len(base)} wide, expected 12"
        # query order: evidence×10, status, is_operator, provenanceSource,
        # confidence, pointKind
        return (*base[:10], status, is_operator, prov_source, *base[10:])

    def apply_slot(self, props: dict) -> None:
        """Simulate `update_point` landing: the read-back must SEE it."""
        row = list(self.canon_row)
        for key, value in props.items():
            if key in self._COLUMNS:
                idx = self._COLUMNS.index(key)
                row[idx + 2 if idx >= 10 else idx] = value
        self.canon_row = tuple(row)

    def query(self, q, **kw):
        if any(marker in q for marker in self.raise_on):
            raise RuntimeError("graph refused the read")
        # ⚠️ Order matters: the EDGES read also starts `MATCH (d:Point`, so it
        # is keyed on its own shape and checked before the property read.
        # ⚠️ The reads are UNFILTERED since round 8 (the rel partition moved into
        # Python), so the fake answers with EVERY edge it was given — including a
        # rel the module does not know, which is how `unexpected_edge` is
        # reachable here. The two reads are told apart by their pattern.
        if "(d:Point {id:$dup})-[r]->(t)" in q:
            if "WHERE type(r) IN" in q:
                # A re-introduced rel FILTER must be honoured, or this fake
                # would answer for the unknown edge anyway and
                # `test_an_edge_the_fold_does_not_know_refuses_...` would pin
                # nothing (measured by mutation: it stayed green).
                wanted = {rel for rel in capture_fold._FOLD_ATTACHMENT_RELS
                          if rel in q}
                return SimpleNamespace(
                    result_set=[e for e in self.edges if e[0] in wanted])
            return SimpleNamespace(result_set=list(self.edges))
        if "-[r]->(d:Point" in q:
            return SimpleNamespace(result_set=list(self.inbound))
        if "d.extractedFrom" in q:
            # `properties(d)` is the row's LAST column; the canonical read does
            # not select it, so the two branches differ by exactly that.
            return SimpleNamespace(
                result_set=[(*self._to_query_row(
                    self.dup_row, self.dup_status, self.dup_is_operator,
                    self.dup_prov_source),
                    self.dup_props, list(self.dup_labels))])
        if "c.extractedFrom" in q:
            # `canon_row=None` is the "canonical cannot be read" arm;
            # `canon_rows` covers the id-carried-by-two-nodes arm.
            if self.canon_rows is not None:
                return SimpleNamespace(result_set=[
                    self._to_query_row(r, None, None) for r in self.canon_rows])
            return SimpleNamespace(
                result_set=[] if self.canon_row is None else [
                    self._to_query_row(self.canon_row, None, None)])
        if "-[:extractedFrom]->(s)" in q:
            return SimpleNamespace(
                result_set=[[t] for t in self.canon_prov_targets])
        if "head(collect(r.confidence))" in q:
            target = (kw.get("params") or {}).get("target")
            if isinstance(self.carried, dict):
                return SimpleNamespace(
                    result_set=[list(self.carried.get(target, (0, None)))])
            return SimpleNamespace(result_set=[list(self.carried)])
        return SimpleNamespace(result_set=[])


class _FakeProj:
    def __init__(self, graph):
        self.g = graph


class _FakeSDK:
    """A write door that records the call and does NOT write the graph —
    exactly the silent-success `update_point` gives for an absent id."""

    def __init__(self, *, raises: Exception | None = None, on_update=None):
        self.raises = raises
        self.on_update = on_update
        self.calls: list[dict] = []

    def update_point(self, point_id, **props):
        self.calls.append({"id": point_id, **props})
        if self.raises:
            raise self.raises
        if self.on_update is not None:
            self.on_update(props)
        return {}


def test_an_unaddressable_attachment_refuses():
    """An `about*` link whose target has NEITHER an id nor a url cannot be
    re-pointed — nothing can NAME it: the writer addresses its target by
    `{id:$id}`, and `link_entity` MINTS nothing — so the fold keeps the
    duplicate. Token: `unaddressable`."""
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
    at all. Token: `slot_write_failed`.

    The RAISE arm must also disclose the residuals it already computed: nothing
    landed, but a differing `pointKind` was FOUND, and the same rule that governs
    the two later refusal arms governs this one — a difference is never crossed in
    silence. RED when that arm returned without `_already_written`.
    """
    dup_row = list(_DUP_ROW_QUOTED)
    dup_row[_FakeGraph._COLUMNS.index("pointKind")] = "observation"
    proj = _FakeProj(_FakeGraph(dup_row=tuple(dup_row)))
    out = capture_fold.carry_fold_evidence(
        proj, "pt_dup", "pt_canon",
        sdk=_FakeSDK(raises=RuntimeError("no such point")))
    assert out["ok"] is False and out["reason"] == "slot_write_failed", out
    assert "no such point" in out["blocked"], out
    assert "did not carry pointKind" in out["blocked"], out["blocked"]
    assert "ALREADY added" not in out["blocked"], (
        f"nothing landed, so nothing was added: {out['blocked']}")

    out = capture_fold.carry_fold_evidence(proj, "pt_dup", "pt_canon", sdk=None)
    assert out["ok"] is False and out["reason"] == "slot_write_failed", out
    assert "no SDK write door" in out["blocked"], out


def test_a_duplicate_with_nothing_to_carry_issues_no_writes():
    """The cost the module declares: no attachments, no free slot ⇒ ZERO writes.

    Pinned at the WRITE DOOR (`sdk.calls`), not at the receipt — the claim is
    about what the fold does to the graph, and a receipt can describe writes that
    never happened (the shape `slot_write_failed`'s read-back exists for).
    """
    sdk = _FakeSDK()
    out = capture_fold.carry_fold_evidence(
        _FakeProj(_FakeGraph()), "pt_dup", "pt_canon", sdk=sdk)
    assert out["ok"] is True, out
    assert (out["carried"], out["created"], out["copied"], out["residual"]) == \
        (0, 0, [], []), out
    assert sdk.calls == [], f"nothing to carry, so nothing may be written: {sdk.calls}"


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


def test_an_internal_error_is_a_refusal_never_an_exception(monkeypatch):
    """The caller sits inside the capture's single per-loop `try`, so a
    propagated error would abandon every remaining statement's resolution.
    A raise the inner guards do not own becomes `internal_error` — a refused
    fold, the safe answer in front of a destructive act.

    ⚠️ The inner reads each have their OWN failure answer (`unreadable`,
    `link_not_carried`), so the wrapper is pinned by a raise from outside them
    (the vocabulary assertion) rather than by a graph read."""
    def boom():
        raise RuntimeError("vocabulary drifted")

    monkeypatch.setattr(capture_fold, "assert_evidence_vocabulary", boom)
    proj = _FakeProj(_FakeGraph())
    out = capture_fold.carry_fold_evidence(proj, "pt_dup", "pt_canon")
    assert out["ok"] is False and out["reason"] == "internal_error", out
    assert "vocabulary drifted" in out["blocked"], out
    assert out["carried"] == 0 and out["copied"] == [], out


# ── 8) the five-rel attachment cascade, and its two writer-less predicates ──


def test_every_attachment_predicate_the_capture_cascade_mints_is_READ():
    """`projection._create_about_edges` is the rel-IGNORING
    Subject→Object→Event→Source→Point cascade, so a capture Point can carry
    FIVE `about*` predicates, not two.

    Before this, `aboutEvent`/`aboutDocument`/`aboutPoint` were not read at all:
    a duplicate whose `aboutEntities` resolved to an `:Event` was folded with
    its link destroyed and a receipt saying it had "no per-occurrence evidence
    of its own to carry" (measured through the real seam).
    """
    from tortoise.projection.edges import _EdgeHandlers

    cascade = {rel for _label, rel in _EdgeHandlers._ABOUT_NAME_CASCADE}
    assert cascade <= set(capture_fold._FOLD_ATTACHMENT_RELS), (
        "the cascade can mint a predicate the fold does not read, so a fold "
        f"destroys it: {sorted(cascade - set(capture_fold._FOLD_ATTACHMENT_RELS))}"
    )
    # …and the SHARED WRITER's vocabulary is the other half of the population:
    # `link_entity` is the one `about*` writer that is not the cascade, and a
    # predicate added there (or an eighth `about*` rel) would be destroyed by
    # the delete exactly as silently. Guarding only the cascade leaves the
    # hand-copy free to drift against the writer that mints the rest.
    from tortoise.session_link import ENTITY_LINKED_RELS

    assert set(ENTITY_LINKED_RELS) <= set(capture_fold._FOLD_ATTACHMENT_RELS), (
        "`link_entity` can write an about* predicate the fold does not read, "
        "so a fold destroys it: "
        f"{sorted(set(ENTITY_LINKED_RELS) - set(capture_fold._FOLD_ATTACHMENT_RELS))}"
    )
    # …and the ones it CAN carry are the permitted Point-source triples.
    from tortoise.session_link import ENTITY_LINKED_TRIPLES

    for rel, label in capture_fold._CARRIABLE_ATTACHMENT_LABELS.items():
        assert (rel, "Point", label) in ENTITY_LINKED_TRIPLES, (
            f"the fold carries {rel} but the ontology permits no Point-source "
            f"triple for it, so `link_entity` would refuse at runtime")


def test_an_aboutEvent_link_is_carried():
    """The widened read is not decorative: an `aboutEvent` link is carried the
    same way an `aboutObject` one is (label `Event` is in the writer's §3.2
    triples)."""
    proj = _FakeProj(_FakeGraph(
        edges=[("aboutEvent", "Event", "ev_1", None)], carried=(1, None)))
    out = capture_fold.carry_fold_evidence(proj, "pt_dup", "pt_canon")
    assert out["ok"] is True and out["carried"] == 1, out


def test_a_carriable_link_takes_its_target_label_from_the_predicate():
    """A carriable link's target label is a function of the PREDICATE (§3.2's
    triple), not of whatever `head(labels(t))` answered — label order is the
    engine's business, and the writer addresses the target by the §3.2 label.

    The fake reports the edge with a label no §3.2 triple names (`Thing`, the
    shape a multi-labelled target produces when the unexpected label comes
    first). RED before the fix: that label was handed to `link_entity`, which
    refuses an unknown label, and the fold came back `link_not_carried` — a
    refusal over a link a Point DOES have a writer for, with the duplicate kept.
    """
    proj = _FakeProj(_FakeGraph(
        edges=[("aboutObject", "Thing", "obj_1", None)], carried=(1, None)))
    out = capture_fold.carry_fold_evidence(proj, "pt_dup", "pt_canon")
    assert out["ok"] is True and out["carried"] == 1, out


def test_a_writerless_predicate_refuses_rather_than_being_dropped():
    """`aboutDocument` (Point→Source) and `aboutPoint` (only Event→Point) are
    not permitted §3.2 triples for a Point source, so no writer can re-create
    them: the fold must refuse, not silently drop the link.

    Token: `uncarriable_attachment`. RED before the widened read: the link was
    not read at all, so the fold proceeded and the edge died with the node.
    """
    for rel, label in (("aboutDocument", "Source"), ("aboutPoint", "Point")):
        proj = _FakeProj(_FakeGraph(edges=[(rel, label, "x_1", None)]))
        out = capture_fold.carry_fold_evidence(proj, "pt_dup", "pt_canon")
        assert out["ok"] is False, (rel, out)
        assert out["reason"] == "uncarriable_attachment", (rel, out)
        assert rel in out["blocked"], out


def test_a_half_written_address_refuses_and_blames_the_duplicate():
    """A capture Point can hold `span_start` without `span_end` (the span
    validator guards the SDK doors, not this seam). Writing it would raise on
    the canonical and the receipt would blame the WRONG node, forever.

    Token: `malformed_address`; and it must refuse BEFORE any write, so the
    refused fold is a no-op.
    """
    dup_row = ("session:s", [], None, 30, None, None, None, None, None,
               None, None, None)
    sdk = _FakeSDK()
    proj = _FakeProj(_FakeGraph(dup_row=dup_row))
    out = capture_fold.carry_fold_evidence(proj, "pt_dup", "pt_canon", sdk=sdk)
    assert out["ok"] is False and out["reason"] == "malformed_address", out
    assert "half span is not an address" in out["blocked"], out
    assert sdk.calls == [], f"a refused fold must not write: {sdk.calls}"


@pytest.mark.parametrize("spans, reason", [
    # the duplicate holds BOTH ends — the half-pair check above cannot see it
    ({"span_start": 50, "span_end": 10}, "must be greater than"),
    # both ends, both wrong type (`validate_span` refuses `->` offsets)
    ({"span_start": "30", "span_end": "62"}, "integer character offset"),
])
def test_an_invalid_span_is_named_as_the_duplicates_own_evidence(
        sdk, monkeypatch, spans, reason):
    """`malformed_address` is the DUPLICATE's token, and "half a pair" is only
    one of the shapes that need it: an INVERTED or stringly-typed pair is just
    as unwritable, and the EventAPI→projection passthrough stores both verbatim
    (it does not run `validate_span` — the SDK's write doors do).

    RED before this: `validate_span` raised INSIDE `update_point`, so the
    refusal came back `slot_write_failed` / "its span_start, span_end evidence
    could not be written onto the canonical (ValueError: …)" — the receipt
    blaming the CANONICAL for evidence the DUPLICATE malformed, forever.
    """
    extracted, meta = _extract(sdk, monkeypatch, evidence=spans)
    assert meta["errors"] == [], meta["errors"]
    assert [e["dedup"] for e in extracted] == ["new", "new"], extracted
    assert len(_claim_rows(sdk._get_proj())) == 2, _claim_rows(sdk._get_proj())
    assert any("kept distinct" in w for w in meta["warnings"]), meta["warnings"]
    assert any("span evidence is not a valid address" in w
               for w in meta["warnings"]), meta["warnings"]
    assert any(reason in w for w in meta["warnings"]), (reason, meta["warnings"])
    assert not any("could not be written onto the canonical" in w
                   for w in meta["warnings"]), meta["warnings"]


def test_an_inbound_link_refuses_the_fold(sdk):
    """An `about*` edge pointing AT the duplicate cannot be moved (the carry
    writes FROM the canonical), so the delete would orphan it.

    Measured here on a real graph (`_FakeGraph`'s inbound fixture is synthetic;
    this proves the real inbound read sees a real `Event -[:aboutPoint]-> Point`). — RED without the guard: the fold proceeds and
    the `Event -[:aboutPoint]-> Point` edge dies with the duplicate.
    """
    proj = sdk._get_proj()
    for pid, content in (("pt_in_dup", "the duplicate claim"),
                         ("pt_in_canon", "the canonical claim")):
        sdk.create_point("statement", content, id=pid, dedup=False)
    proj.g.query(
        "MATCH (p:Point {id:'pt_in_dup'}) "
        "CREATE (e:Event {id:'ev_inbound_9437', name:'Release'}) "
        "-[:aboutPoint]->(p)")
    out = capture_fold.carry_fold_evidence(proj, "pt_in_dup", "pt_in_canon",
                                          sdk=sdk)
    assert out["ok"] is False and out["reason"] == "inbound_attachment", out
    assert "points AT it" in out["blocked"], out
    assert out["carried"] == 0 and out["copied"] == [], out


def test_a_duplicate_id_on_two_nodes_refuses_the_fold(sdk):
    """`DETACH DELETE n` by id takes EVERY row with that id, while the evidence
    read answers for ONE — so a fan-out duplicate would have a copy's evidence
    destroyed unread. Refuse rather than trust the read.

    `sdk`'s capture ids are ULIDs, but a point id is not unique in general
    (`_refuse_inverted_point_window` records the fan-out as a tested shape), so
    the carry must not assume it. RED without the guard: `ok=True, carried=1`
    over a delete that removes both rows.
    """
    proj = sdk._get_proj()
    sdk.create_point("statement", "canon", id="pt_fan_canon", dedup=False)
    proj.g.query(
        "CREATE (:Point {id:'pt_fan_dup', content:'copy one'}), "
        "(:Point {id:'pt_fan_dup', content:'copy two'})")
    out = capture_fold.carry_fold_evidence(proj, "pt_fan_dup", "pt_fan_canon",
                                          sdk=sdk)
    assert out["ok"] is False and out["reason"] == "ambiguous_duplicate", out
    assert "more than one node carries its id" in out["blocked"], out


def test_a_new_graded_link_carries_its_grading(sdk):
    """The other arm of the `confidence` residual: when the link is NEW on the
    canonical, the duplicate's grading rides it and there is NO residual.

    (Only the residual arm was pinned before, so a regression that dropped the
    grading on the create arm would have passed the suite.)
    """
    proj = sdk._get_proj()
    for pid in ("pt_dup_graded", "pt_canon_ungraded"):
        sdk.create_point("statement", f"claim for {pid}", id=pid, dedup=False)
    sdk.create_object("GradedCo")
    proj.g.query(
        "MATCH (p:Point {id:'pt_dup_graded'}), (o:Object {name:'GradedCo'}) "
        "MERGE (p)-[r:aboutObject]->(o) SET r.confidence=0.7")
    out = capture_fold.carry_fold_evidence(
        proj, "pt_dup_graded", "pt_canon_ungraded", sdk=sdk)
    assert out["ok"] is True, out
    assert out["carried"] == 1 and out["created"] == 1, out
    assert out["residual"] == [], out
    rows = proj.g.query(
        "MATCH (:Point {id:'pt_canon_ungraded'})-[r:aboutObject]->"
        "(:Object {name:'GradedCo'}) RETURN r.confidence").result_set
    assert rows and rows[0][0] == 0.7, rows


# ── 9) the evidence vocabulary cannot drift from the capture contract ──────


def test_the_fold_reads_every_field_the_capture_contract_declares(monkeypatch):
    """The slot list is a hand-copy of `sdk._CAPTURE_PASSTHROUGH_ORDER`. A copy
    with no guard drifts, and a drifted field is DESTROYED by the delete — the
    exact failure this module exists to stop. The guard is an assertion, not an
    import, because importing `sdk` here would be a cycle.

    The RAISE is pinned too: a guard whose failure arm has never been shown to
    fail is an inert enforcer. RED without the refusal branch (mutation: `if
    missing:` → `if False:`): the drift below would pass silently.
    """
    capture_fold.assert_evidence_vocabulary()      # raises on drift
    from tortoise.sdk import _CAPTURE_PASSTHROUGH_ORDER

    declared = (set(capture_fold._OCCURRENCE_ADDRESS)
                | {k for k, _ in capture_fold._SINGLE_SLOTS})
    assert set(_CAPTURE_PASSTHROUGH_ORDER) <= declared

    # …and the guard FIRES: drop one declared field and the read must refuse.
    monkeypatch.setattr(capture_fold, "_OCCURRENCE_ADDRESS",
                        tuple(k for k in capture_fold._OCCURRENCE_ADDRESS
                              if k != "quote"))
    with pytest.raises(AssertionError, match="#9437"):
        capture_fold.assert_evidence_vocabulary()


# ── 10) the disclosure pins (the shape two rounds of review found unpinned) ──


def test_a_pre_existing_link_is_not_reported_as_added_by_the_fold():
    """`carried` counts links PRESENT; only `created` were written by this fold.
    A late refusal must claim exactly what it added — and when every link it
    touched was already there, that is NOTHING.

    RED on the previous head: `_already_written` read `carried`, so it reported
    "it had ALREADY added 1 attachment link(s)" over a fold that wrote nothing.
    """
    proj = _FakeProj(_FakeGraph(
        edges=[("aboutObject", "Object", "obj_pre", None),
               ("aboutObject", "Object", "obj_missing", None)],
        carried={"obj_pre": (1, None), "obj_missing": (0, None)}))
    out = capture_fold.carry_fold_evidence(proj, "pt_dup", "pt_canon")
    assert out["ok"] is False and out["reason"] == "link_not_carried", out
    assert out["carried"] == 1 and out["created"] == 0, out
    assert "ALREADY added" not in out["blocked"], (
        f"the refusal claims an addition this fold did not make: {out['blocked']}"
    )


def test_a_late_refusal_discloses_the_slots_it_already_copied():
    """The scalar write lands, then a link cannot be carried: the refusal must
    say the canonical already gained the copied slots, or a reader treats
    "kept distinct" as "nothing changed" — the reading this module forbids.

    RED on the previous head: the raise from the post-write read escaped to the
    never-raise wrapper, whose fresh dict discarded `copied` entirely.

    Also pins the RESIDUAL half of the same disclosure: `pointKind` differs and
    is computed before the write, so a refusal that dropped it would cross a
    difference in silence — the one thing this module never does.
    """
    dup_row = list(_DUP_ROW_QUOTED)
    dup_row[_FakeGraph._COLUMNS.index("pointKind")] = "observation"
    graph = _FakeGraph(
        dup_row=tuple(dup_row),
        edges=[("aboutObject", "Object", "obj_missing", None)],
        carried={"obj_missing": (0, None)})
    sdk = _FakeSDK(on_update=graph.apply_slot)
    out = capture_fold.carry_fold_evidence(
        _FakeProj(graph), "pt_dup", "pt_canon", sdk=sdk)
    assert out["ok"] is False and out["reason"] == "link_not_carried", out
    assert out["copied"] == ["quote"], out
    assert "ALREADY added copied quote" in out["blocked"], out["blocked"]
    assert "did not carry pointKind" in out["blocked"], out["blocked"]


def test_a_raise_from_the_post_write_read_is_a_disclosed_refusal():
    """The post-write `_carried` read is INSIDE the link try on purpose: when it
    escaped to the never-raise wrapper, the wrapper's FRESH dict discarded
    `copied`/`created` and the receipt read as "nothing changed" over a canonical
    holding the copied slot.

    Pins the round-3 fix by MUTATION: deleting that inner `try` reds this test
    (the previous pin exercised `present == 0`, not a raise, and stayed green).
    """
    graph = _FakeGraph(
        dup_row=_DUP_ROW_QUOTED,
        edges=[("aboutObject", "Object", "obj_1", None)],
        raise_on={"head(collect(r.confidence))"})
    sdk = _FakeSDK(on_update=graph.apply_slot)
    out = capture_fold.carry_fold_evidence(
        _FakeProj(graph), "pt_dup", "pt_canon", sdk=sdk)
    assert out["ok"] is False and out["reason"] == "link_not_carried", out
    assert out["copied"] == ["quote"], out
    assert "ALREADY added copied quote" in out["blocked"], out["blocked"]


def test_a_differing_declared_support_field_is_reported_as_a_residual(
        sdk, monkeypatch):
    """`eventId` is DECLARED (`_REPORTED_FIELDS`), so a duplicate holding a
    different one is REPORTED as a residual — never crossed in silence while the
    receipt says it had no evidence.

    ⚠️ The distinction this pins is residual-vs-undeclared, and the earlier cut
    pinned neither: it claimed `eventId` was reached as `undeclared` (it is not —
    it is declared) and asserted only that the name appeared somewhere, which
    holds for both arms. So the arms are asserted NEGATIVELY too.
    """
    extracted, meta = _extract(
        sdk, monkeypatch, evidence={"eventId": "ev_dup_turn2"})
    assert [e["dedup"] for e in extracted] == ["new", "rephrase_linked"], extracted
    assert any("did not carry eventId" in w for w in meta["warnings"]), \
        meta["warnings"]
    assert not any("could not carry" in w for w in meta["warnings"]), \
        meta["warnings"]


# ── 9) the OPEN SET: a prop the fold does not know is REPORTED (#2795) ─────


def test_a_prop_outside_the_declared_vocabulary_is_reported_by_name():
    """`api.add_point(**fields)` persists undeclared props (#2795), so a
    duplicate can carry evidence this module has no slot for. The fold cannot
    carry it — and the delete takes it — so it must be NAMED.

    RED before round 7 — the receipt read "no per-occurrence evidence of its
    own to carry" over a node whose `author` was about to be destroyed.
    """
    graph = _FakeGraph(dup_props={"id": "pt_dup", "content": "c",
                                  "author": "second-producer"})
    out = capture_fold.carry_fold_evidence(
        _FakeProj(graph), "pt_dup", "pt_canon")
    assert out["undeclared"] == ["author"], out
    # …and the STRUCTURAL props are not noise: an id/content pair is the row's
    # own identity, not a loss.
    assert "id" not in out["undeclared"] and "content" not in out["undeclared"], out


def test_the_undeclared_report_does_not_depend_on_the_fold_succeeding():
    """A REFUSED fold also discloses the open set: the operator deciding whether
    to force the fold needs to know what the delete would take."""
    graph = _FakeGraph(dup_props={"author": "second-producer"},
                       edges=[("aboutObject", "Object", "obj_1", None)],
                       carried=(0, None))
    out = capture_fold.carry_fold_evidence(
        _FakeProj(graph), "pt_dup", "pt_canon")
    assert out["ok"] is False, out
    assert out["undeclared"] == ["author"], out


def test_a_point_the_real_write_door_mints_reports_no_undeclared_props(sdk):
    """The drift guard on a REAL producer, not a stub.

    ⚠️ The earlier arm ran a stub whose payload shape differs from the SDK's own
    write door, so it could not see the props the projection actually writes —
    and `create_point` writes THREE this module did not declare
    (`provenanceSource`, `sourceVersionTransit`, `ep_dirty`/`ep_dirty_at`).
    `provenanceSource` is a provenance POINTER (`graph_census
    .PROVENANCE_POINTER_PROPS` names `provenanceSource`/`extractedFrom`), so it is
    read as a REF; the rest are bookkeeping. This is the arm that reds when
    either fact drifts.
    """
    sdk.create_point("statement", "a claim", dedup=False)
    props = sdk._get_proj().g.query(
        "MATCH (p:Point) WHERE p.is_operator <> true RETURN properties(p)"
    ).result_set[0][0]
    assert not (set(props) - capture_fold._DECLARED_PROPS), (
        "a real write door mints props this fold does not declare (so every fold "
        f"of such a Point reports them as dropped): "
        f"{sorted(set(props) - capture_fold._DECLARED_PROPS)}"
    )


def test_a_clean_capture_point_reports_no_undeclared_props(sdk, monkeypatch):
    """NOISE CONTROL, and the drift guard for `_STRUCTURAL_PROPS`.

    A real capture Point must report an EMPTY open set: if the projection starts
    writing a prop this module does not declare, every fold's receipt grows a
    "dropped field(s)" clause — which is the loud direction — and this arm is
    what makes that drift visible instead of merely loud.
    """
    sdk.create_object("Acme")
    extracted, meta = _extract(
        sdk, monkeypatch, evidence={"aboutEntities": ["Acme"]})
    assert [e["dedup"] for e in extracted] == ["new", "rephrase_linked"], extracted
    assert not any("could not carry" in w for w in meta["warnings"]), meta["warnings"]
    props = sdk._get_proj().g.query(
        "MATCH (p:Point {id:$pid}) RETURN properties(p)",
        params={"pid": extracted[0]["id"]}).result_set[0][0]
    assert not (set(props) - capture_fold._DECLARED_PROPS), (
        "the projection writes props this fold does not declare (so every fold "
        f"would report them as dropped): {sorted(set(props) - capture_fold._DECLARED_PROPS)}"
    )


def test_a_derived_embedding_is_structural_so_a_fold_never_claims_it_dropped():
    """The drift guard that does NOT need an embedding model to be installed.

    ⚠️ Both `_extract`-based guards above can only see `embedding` on a box where
    `EmbeddingModel.get()` returns a model. On a keyword-only box (plain
    `uv sync`) the projection mints no vector, so the prop is absent and the
    guards are VACUOUS for the whole class — which is exactly how the red this
    case closes survived: measured #12322, `test (i)` red on every PR at
    `f33f5e2c32` with `['embedding']`, while the same suite passed **99/99**
    locally with no model provisioned. A guard that only fires where the model
    exists re-creates the bug for the next runner, so this case INJECTS the
    prop and pins the declaration on every box.

    ⚠️ The bucket is pinned too, not just membership: `embedding` must be
    STRUCTURAL rather than REPORTED. It is a DERIVED vector — the projection
    excludes it from content identity because a re-encode would otherwise read
    as an item change — so a differing one is recomputable from the wording the
    fold keeps, and reporting it would put a residual on every fold.
    """
    proj = _FakeProj(_FakeGraph(
        dup_props={"embedding": [0.125, -0.5, 0.75]},
        edges=[("aboutObject", "Object", "obj_1", 0.7)], carried=(1, None)))
    out = capture_fold.carry_fold_evidence(proj, "pt_dup", "pt_canon")
    assert out["ok"] is True, out
    assert out["undeclared"] == [], out
    assert "embedding" in capture_fold._STRUCTURAL_PROPS, (
        "`embedding` must stay STRUCTURAL, not move to `_REPORTED_FIELDS`: a "
        "derived vector is recomputable from the content the fold keeps, so "
        "reporting it as a residual puts noise on every capture receipt"
    )


def test_a_real_undeclared_prop_is_named_on_the_captures_receipt(sdk, monkeypatch):
    """End to end through the capture seam: the warning NAMES the prop."""
    extracted, meta = _extract(sdk, monkeypatch,
                               evidence={"author": "second-producer"})
    assert [e["dedup"] for e in extracted] == ["new", "rephrase_linked"], extracted
    assert any("could not carry: author" in w for w in meta["warnings"]), \
        meta["warnings"]
    # ⚠️ The NEGATIVE half: an undeclared prop must ALSO suppress the clause that
    # denies the node had any evidence of its own.
    assert not any("no per-occurrence evidence of its own" in w
                   for w in meta["warnings"]), meta["warnings"]


# ── 10) arms a reader could delete without any test going red ──────────────


def test_an_unreadable_canonical_refuses_naming_the_canonical():
    """`_canonical_view` answering None (absent id, failed read) must refuse —
    the occupancy checks compare against a view, and "no view" is not "empty".

    Unpinned before round 7: deleting the arm made the carry raise inside the
    never-raise wrapper, which reports `internal_error` — a token that blames
    the code for a graph state.
    """
    graph = _FakeGraph(edges=[("aboutObject", "Object", "obj_1", None)],
                       canon_row=None)
    out = capture_fold.carry_fold_evidence(
        _FakeProj(graph), "pt_dup", "pt_canon")
    assert out["ok"] is False and out["reason"] == "unreadable", out
    assert "canonical" in out["blocked"], out


def test_a_late_link_failure_discloses_the_link_it_already_wrote(monkeypatch):
    """The positive arm of `_already_written` on the LINK side: one of two links
    lands, the second does not, and the refusal says the canonical GAINED one.

    Unpinned before round 7 (only the slot-copy arm was pinned), so a reader
    could delete the `created` disclosure and the refusal would read "kept
    distinct" over a canonical that gained a link.
    """
    # `link_entity` reports CREATION; the fake graph cannot (it answers reads
    # only), so the writer is stubbed and the READ-BACK stays real — which is
    # what the disclosure keys on.
    from tortoise import session_link
    monkeypatch.setattr(session_link, "link_entity",
                        lambda *a, **kw: True)
    graph = _FakeGraph(
        edges=[("aboutObject", "Object", "obj_ok", None),
               ("aboutObject", "Object", "obj_bad", None)],
        carried={"obj_ok": (1, None), "obj_bad": (0, None)})
    out = capture_fold.carry_fold_evidence(
        _FakeProj(graph), "pt_dup", "pt_canon")
    assert out["ok"] is False and out["reason"] == "link_not_carried", out
    assert out["created"] == 1 and out["carried"] == 1, out
    assert "ALREADY added 1 attachment link(s)" in out["blocked"], out["blocked"]


def test_an_empty_string_slot_is_a_VALUE_and_is_carried():
    """#5004's presence rule: presence is ownership, so `""` is a value the
    producer wrote, not an absence to fill.

    Unpinned before round 7 — swapping `if value is not None:` for a truthiness
    test left every arm green, and the duplicate's empty-string slot would be
    dropped with the node.
    """
    graph = _FakeGraph(dup_row=("session:s", [], "", None, None, None, None,
                               None, None, None, None, None))
    sdk = _FakeSDK(on_update=graph.apply_slot)
    out = capture_fold.carry_fold_evidence(
        _FakeProj(graph), "pt_dup", "pt_canon", sdk=sdk)
    assert out["ok"] is True, out
    assert sdk.calls and sdk.calls[0].get("quote") == "", (sdk.calls, out)
    assert out["copied"] == ["quote"], out


# ── 11) round 8: the receipt's own contradictions, and the edge open set ──


def test_the_ontologys_date_spelling_is_declared_not_a_loss():
    """`validFrom` is the WRITE spelling of the `when` slot, so a producer that
    stores it is holding a value the fold CARRIES.

    RED before round 8: `_DECLARED_PROPS` derived the slot half from the READ
    keys only, so this duplicate was reported as
    `copied validFrom; … could not carry: validFrom` — one sentence claiming
    both, over a value that had landed.
    """
    row = ("session:s", [], None, None, None, None, None, None,
           "2026-08-01", None, None, None)          # position 8 = validFrom
    graph = _FakeGraph(dup_row=row, dup_props={"validFrom": "2026-08-01"})
    sdk = _FakeSDK(on_update=graph.apply_slot)
    out = capture_fold.carry_fold_evidence(
        _FakeProj(graph), "pt_dup", "pt_canon", sdk=sdk)
    assert out["ok"] is True, out
    assert out["undeclared"] == [], out
    assert out["copied"] == ["validFrom"], out
    assert sdk.calls == [{"id": "pt_canon", "validFrom": "2026-08-01"}], sdk.calls


def test_an_edge_the_fold_does_not_know_refuses_rather_than_orphaning():
    """The reads are UNFILTERED: the delete takes every edge, so an edge class
    outside `_FOLD_ATTACHMENT_RELS` is a refusal, not a silent orphan.

    RED before round 8: the outbound read carried `WHERE type(r) IN [...]`, so a
    `TAGGED`/session edge on the duplicate was invisible and died with it.
    """
    graph = _FakeGraph(edges=[("TAGGED", "Topic", "topic_1", None)])
    out = capture_fold.carry_fold_evidence(
        _FakeProj(graph), "pt_dup", "pt_canon")
    assert out["ok"] is False and out["reason"] == "unexpected_edge", out
    assert "TAGGED (outbound)" in out["blocked"], out


def test_an_unknown_inbound_edge_also_refuses():
    """The inbound read is unfiltered for the same reason — an inbound edge of an
    unknown type would be orphaned by the delete just as an outbound one would."""
    graph = _FakeGraph(inbound=[("MENTIONS", "Session")])
    out = capture_fold.carry_fold_evidence(
        _FakeProj(graph), "pt_dup", "pt_canon")
    assert out["ok"] is False and out["reason"] == "unexpected_edge", out
    assert "MENTIONS (inbound)" in out["blocked"], out


def test_a_canonical_id_carried_by_two_nodes_refuses():
    """The fan-out guard was ASYMMETRIC: the duplicate refused, the canonical did
    not. The writes address the ID, so occupancy would be read off one row while
    the slot write landed on both — a first-writer value overwritten on a row the
    fold never read.
    """
    graph = _FakeGraph(canon_rows=[_CANON_ROW, _CANON_ROW])
    out = capture_fold.carry_fold_evidence(
        _FakeProj(graph), "pt_dup", "pt_canon")
    assert out["ok"] is False and out["reason"] == "unreadable", out
    assert "more than one node" in out["blocked"], out


def test_a_malformed_ref_list_names_the_ref_not_its_characters():
    """A STRING `extractedFromAll` is a sequence, so the ref loop used to iterate
    it character-wise and refuse over the ref `':'` — fail-closed, but the token
    and the named ref were both wrong. The mis-attribution `malformed_address`
    exists to refuse, so it must not be reintroduced for provenance.
    """
    row = ("session:s", "session:other", None, None, None, None, None, None,
           None, None, None, None)
    graph = _FakeGraph(dup_row=row)
    out = capture_fold.carry_fold_evidence(
        _FakeProj(graph), "pt_dup", "pt_canon")
    assert out["ok"] is False and out["reason"] == "uncarried_source_ref", out
    assert "'session:other'" in out["blocked"], out["blocked"]
    assert "':'" not in out["blocked"], out["blocked"]


def test_every_arm_returns_the_same_key_set(monkeypatch):
    """The never-raise wrapper documented "EXACT key set" and then returned one
    MISSING `undeclared` — a `KeyError` waiting inside the capture loop it exists
    to protect. Both arms are pinned here.

    The `internal_error` arm is reached through the vocabulary assertion: a graph
    read that raises is `unreadable` (the reads own their failure answer), so
    reaching the WRAPPER needs a raise from outside them.
    """
    expected = {"ok", "reason", "blocked", "carried", "created", "copied",
                "residual", "undeclared"}
    ok_out = capture_fold.carry_fold_evidence(
        _FakeProj(_FakeGraph()), "pt_dup", "pt_canon")
    assert ok_out["ok"] is True, ok_out
    assert set(ok_out) == expected, sorted(set(ok_out) ^ expected)

    def boom():
        raise RuntimeError("vocabulary drifted")

    monkeypatch.setattr(capture_fold, "assert_evidence_vocabulary", boom)
    failing = capture_fold.carry_fold_evidence(
        _FakeProj(_FakeGraph()), "pt_dup", "pt_canon")
    assert failing["ok"] is False and failing["reason"] == "internal_error", failing
    assert set(failing) == expected, sorted(set(failing) ^ expected)


# ── 12) round 9: the provenance exemption, both date spellings, both reads ──


def test_a_provenance_edge_that_is_not_one_of_the_rows_refs_refuses():
    """The `extractedFrom` exemption is CONDITIONAL: it covers an outbound edge
    whose target IS one of the row's own refs — the case the field check
    (`uncarried_source_ref`) actually establishes.

    RED before round 9: the exemption was applied to every `extractedFrom` edge
    and `continue`d BEFORE the ref check could see it, so an edge pointing at a
    source the row's field does not name was destroyed on an `ok=True` fold.
    """
    graph = _FakeGraph(edges=[("extractedFrom", "Source", "session:STALE", None)])
    out = capture_fold.carry_fold_evidence(
        _FakeProj(graph), "pt_dup", "pt_canon")
    assert out["ok"] is False and out["reason"] == "unexpected_edge", out
    assert "provenance edge to 'session:STALE'" in out["blocked"], out


def test_a_provenance_edge_on_the_canonicals_own_ref_is_exempt():
    """…and the exemption still holds for the case it exists for: the edge IS the
    projection of a ref the canonical already carries."""
    graph = _FakeGraph(edges=[("extractedFrom", "Source", "session:s", None)])
    graph.canon_prov_targets = ["session:s"]      # the canonical holds that edge
    out = capture_fold.carry_fold_evidence(
        _FakeProj(graph), "pt_dup", "pt_canon")
    assert out["ok"] is True, out
    assert out["carried"] == 0, out


def test_an_inbound_provenance_edge_refuses():
    """The exemption was copied onto the INBOUND axis, where its ground cannot
    hold at all: an inbound edge is not the projection of any field on the
    duplicate, so nothing verified it and the delete orphaned it silently."""
    graph = _FakeGraph(inbound=[("extractedFrom", "Source")])
    out = capture_fold.carry_fold_evidence(
        _FakeProj(graph), "pt_dup", "pt_canon")
    assert out["ok"] is False and out["reason"] == "unexpected_edge", out
    assert "extractedFrom (inbound)" in out["blocked"], out


def test_a_duplicate_holding_both_date_spellings_reports_the_conflict():
    """`when` and `validFrom` are ONE slot — on the duplicate's side too. A row
    holding BOTH with different values used to collapse to `when` before any
    comparison, so the second value was neither carried, nor a residual, nor
    undeclared (both spellings are declared), and the receipt could say the node
    had no evidence of its own over a date that died with it.
    """
    row = ("session:s", [], None, None, None, None, None,
           "2026-08-01", "2026-08-02", None, None, None)   # when + validFrom
    graph = _FakeGraph(dup_row=row, dup_props={"when": "2026-08-01",
                                               "validFrom": "2026-08-02"})
    sdk = _FakeSDK(on_update=graph.apply_slot)
    out = capture_fold.carry_fold_evidence(
        _FakeProj(graph), "pt_dup", "pt_canon", sdk=sdk)
    assert out["ok"] is True, out
    assert any("validFrom (as when)" in r for r in out["residual"]), out
    # the value the field names still travels; the OTHER spelling is disclosed
    assert sdk.calls == [{"id": "pt_canon", "validFrom": "2026-08-01"}], sdk.calls


def test_the_canonical_read_normalises_a_string_ref_list_too():
    """`_canonical_view`'s ref loop was the un-fixed copy of the one hardened in
    `read_fold_evidence`: a string `extractedFromAll` was iterated
    CHARACTER-WISE, so the fold refused with the false claim that the canonical
    did not carry a ref that is exactly what it held."""
    graph = _FakeGraph()
    graph.canon_row = ("session:s", "session:s", None, None, None, None, None,
                       None, None, None, None, None)
    view = capture_fold._canonical_view(_FakeProj(graph), "pt_canon")
    assert view is not None
    assert view["refs"] == {"session:s"}, view["refs"]


def test_a_source_link_refuses_as_unaddressable_and_a_point_link_is_carried():
    """The two §3.2 triples no test constructed end to end: `aboutSource` refuses
    rather than claim a carry, and `aboutAction` (Point→Point) is carriable and
    counts as present.

    ⚠️ The FIXTURE's condition is a target with NEITHER an id NOR a url (the
    address column here is `""`), which is genuinely `unaddressable`. A REAL
    url-keyed `:Source` is a different shape: the outbound read coalesces
    `t.id, t.url`, so it IS addressable and refuses as `link_not_carried` — that
    is `test_a_real_source_link_refuses_as_link_not_carried`, and an earlier
    version of THIS docstring claimed the url-keyed case was unaddressable.
    """
    src = _FakeGraph(edges=[("aboutSource", "Source", "", None)])
    out = capture_fold.carry_fold_evidence(
        _FakeProj(src), "pt_dup", "pt_canon")
    assert out["ok"] is False and out["reason"] == "unaddressable", out
    assert "aboutSource" in out["blocked"], out

    act = _FakeGraph(edges=[("aboutAction", "Point", "pt_target", None)],
                     carried=(1, None))
    out = capture_fold.carry_fold_evidence(
        _FakeProj(act), "pt_dup", "pt_canon")
    assert out["ok"] is True and out["carried"] == 1, out


def test_a_refused_fold_still_names_the_open_set_on_the_captures_receipt(
        sdk, monkeypatch):
    """The module sets `undeclared` on every arm so a REFUSAL can disclose it,
    and the seam printed only `blocked` — so the open set went quiet in exactly
    the case its own comment cites."""
    extracted, meta = _extract(
        sdk, monkeypatch,
        evidence={"author": "second-producer"},
        dup_source="session:other")          # the canonical cannot carry it
    assert [e["dedup"] for e in extracted] == ["new", "new"], extracted
    assert any("kept distinct" in w for w in meta["warnings"]), meta["warnings"]
    assert any("cannot carry: author" in w for w in meta["warnings"]), \
        meta["warnings"]


# ── 13) round 10: what the writer counted, and what the bound is read by ──


def test_a_link_counted_before_the_read_back_still_counts_when_it_raises(
        monkeypatch):
    """`created` is what the WRITER reported, counted BEFORE the read-back that
    can raise.

    RED before round 10 — measured on a real graph with `_carried` patched to
    raise: the canonical gained the link, while the refusal reported
    `created: 0` and printed no "ALREADY added" clause, so "kept distinct" read
    as "nothing changed" over a node that had gained one.
    """
    from tortoise import session_link
    monkeypatch.setattr(session_link, "link_entity", lambda *a, **kw: True)

    def boom(*a, **kw):
        raise RuntimeError("read-back failed")

    monkeypatch.setattr(capture_fold, "_carried", boom)
    graph = _FakeGraph(edges=[("aboutObject", "Object", "obj_1", None)])
    out = capture_fold.carry_fold_evidence(
        _FakeProj(graph), "pt_dup", "pt_canon")
    assert out["ok"] is False and out["reason"] == "link_not_carried", out
    assert out["created"] == 1, out
    assert "ALREADY added 1 attachment link(s)" in out["blocked"], out["blocked"]


def test_a_provenance_edge_the_canonical_lacks_refuses_even_when_the_ref_matches():
    """The field is not the edge: a duplicate's provenance edge may only die when
    the CANONICAL holds the same edge. The earlier exemption granted it on the
    field alone and claimed "the canonical carries it" from a fact that does not
    imply it.
    """
    graph = _FakeGraph(edges=[("extractedFrom", "Source", "session:s", None)])
    out = capture_fold.carry_fold_evidence(
        _FakeProj(graph), "pt_dup", "pt_canon")     # no canonical edges
    assert out["ok"] is False and out["reason"] == "unexpected_edge", out
    assert "provenance edge to 'session:s'" in out["blocked"], out


def test_the_carried_edge_attr_bound_is_read_by_the_module_guard(monkeypatch):
    """`_CARRIED_EDGE_ATTRS` was declared and read by NOTHING — a "second
    constant nothing reads" — so a new attribute read, or a dropped one, left the
    declaration drifting in silence. `_assert_carried_edge_attrs` now asserts it
    against the query that reads it, called from `read_fold_evidence` (NOT from
    `assert_evidence_vocabulary`, which covers a different vocabulary — the
    sibling docstring at the wiring pin says so too).
    """
    capture_fold._assert_carried_edge_attrs(capture_fold._OUTBOUND_EDGES_QUERY)
    monkeypatch.setattr(capture_fold, "_CARRIED_EDGE_ATTRS",
                        ("confidence", "weight"))
    with pytest.raises(AssertionError, match="does not select"):
        capture_fold._assert_carried_edge_attrs(
            capture_fold._OUTBOUND_EDGES_QUERY)


def test_a_real_source_link_refuses_as_link_not_carried(sdk):
    """A `:Source` is `url`-keyed, so its address is the url; the writer matches
    `{id:$tid}`, finds nothing, and the fold refuses as `link_not_carried` — NOT
    `unaddressable`, which an earlier comment and test claimed.
    """
    proj = sdk._get_proj()
    for pid, content in (("pt_src_dup", "the duplicate claim with a source"),
                         ("pt_src_canon", "the canonical claim")):
        sdk.create_point("statement", content, id=pid, dedup=False)
    proj.g.query(
        "MATCH (p:Point {id:'pt_src_dup'}) "
        "CREATE (p)-[:aboutSource]->(:Source {url:'doc:policy-9437'})")
    out = capture_fold.carry_fold_evidence(proj, "pt_src_dup", "pt_src_canon",
                                          sdk=sdk)
    assert out["ok"] is False, out
    assert out["reason"] == "link_not_carried", out
    assert "did not land" in out["blocked"], out["blocked"]


def test_a_provenance_source_on_the_duplicate_is_declared_not_undeclared():
    """`provenanceSource` is a provenance POINTER, read as a ref — so it is
    DECLARED, and an equal value is silent.

    RED before round 11: it was read by `_refs_from` but missing from
    `_DECLARED_PROPS`, so it fell into the open set — every capture Point the m2
    path mints carried a receipt clause claiming the fold "could not carry:
    provenanceSource" over a value it had compared and allowed. Neither drift
    test saw it: `create_point` does not write the prop and the stub producer
    does not either.
    """
    graph = _FakeGraph(dup_props={"provenanceSource": "session:s",
                                  "sourceVersionTransit": "v1"})
    out = capture_fold.carry_fold_evidence(
        _FakeProj(graph), "pt_dup", "pt_canon")
    assert out["undeclared"] == [], out
    assert "provenanceSource" not in out["undeclared"], out
    assert "sourceVersionTransit" not in out["undeclared"], out


# ── 14) round 11: the residual on a refusal, the guard's WIRING, the sinks ──


def test_a_refused_fold_discloses_the_duplicates_own_slot_conflict(
        sdk, monkeypatch):
    """The module sets `residual` before every pre-write refusal so a refusal can
    disclose the duplicate's own one-slot-two-values conflict — and the seam
    printed only `blocked`/`undeclared`, so it went quiet in exactly the case an
    operator force-deciding the fold needs it.
    """
    extracted, meta = _extract(
        sdk, monkeypatch,
        evidence={"when": "2026-08-01", "validFrom": "2026-08-02"},
        dup_source="session:other")            # …and this refuses the fold
    assert [e["dedup"] for e in extracted] == ["new", "new"], extracted
    assert any("kept distinct" in w for w in meta["warnings"]), meta["warnings"]
    assert any("validFrom (as when)" in w for w in meta["warnings"]), \
        meta["warnings"]


def test_the_edge_attr_guard_is_WIRED_into_the_read(monkeypatch):
    """The guard's BODY was pinned; its CALL was not.

    Delete the call in `read_fold_evidence` and every test stayed green — the
    "second constant nothing reads" shape, restored. This arm pins the call.
    """
    monkeypatch.setattr(capture_fold, "_CARRIED_EDGE_ATTRS",
                        ("confidence", "weight"))
    out = capture_fold.carry_fold_evidence(
        _FakeProj(_FakeGraph()), "pt_dup", "pt_canon")
    assert out["ok"] is False and out["reason"] == "internal_error", out
    assert "does not select" in out["blocked"], out


def test_a_provenance_edge_with_no_address_refuses():
    """`if t` skipped an address-less provenance target, so that edge died on an
    `ok=True` fold: the ref check reads the FIELD, the edge check had nothing to
    compare, and the delete took the edge with no clause naming it."""
    graph = _FakeGraph(edges=[("extractedFrom", "Source", "", None)])
    out = capture_fold.carry_fold_evidence(
        _FakeProj(graph), "pt_dup", "pt_canon")
    assert out["ok"] is False and out["reason"] == "unexpected_edge", out


def test_the_writerless_predicates_are_the_CARRIABLE_complement():
    """ONE list decides which predicates a Point may write, and the refusal site
    tests that list directly. A second tuple of the same fact (hand-listed, then
    derived) was the "constant nothing reads" shape: its only reader was a test
    asserting it matched its own input, so editing it changed nothing."""
    writerless = (set(capture_fold._FOLD_ATTACHMENT_RELS)
                  - set(capture_fold._CARRIABLE_ATTACHMENT_RELS))
    assert writerless, "the partition must not be empty"
    for rel in sorted(writerless):
        out = capture_fold.carry_fold_evidence(
            _FakeProj(_FakeGraph(edges=[(rel, "Document", "doc_1", None)])),
            "pt_dup", "pt_canon")
        assert out["reason"] == "uncarriable_attachment", (rel, out)


def test_the_link_refusal_states_the_total_not_only_the_first_three():
    """Naming three of seven is a partial fact; the rest of this module counts
    what it cannot name."""
    edges = [("aboutObject", "Object", f"obj_{i}", None) for i in range(5)]
    graph = _FakeGraph(edges=edges,
                       carried={f"obj_{i}": (0, None) for i in range(5)})
    out = capture_fold.carry_fold_evidence(
        _FakeProj(graph), "pt_dup", "pt_canon")
    assert out["reason"] == "link_not_carried", out
    assert out["blocked"].startswith("its 5 attachment link(s)"), out["blocked"]
    assert "(and 2 more)" in out["blocked"], out["blocked"]


def test_an_address_part_the_canonical_does_not_hold_says_so():
    """The address is all-or-nothing: a canonical holding only `quote` drops the
    duplicate's `span_start`, whose canonical value does not exist. The receipt
    said "the canonical's value differs" over an absent value — the wording this
    same module fixed for the support fields."""
    graph = _FakeGraph(
        dup_row=("session:s", [], "q dup", 40, 72, "turn-2", None, None, None,
                 None, None, None),
        canon_row=("session:s", [], "q canon", None, None, "turn-1", None, None,
                   None, None, None, None))
    out = capture_fold.carry_fold_evidence(
        _FakeProj(graph), "pt_dup", "pt_canon")
    assert out["ok"] is True, out
    assert any("span_start — the canonical holds no value for this part of the "
               "address" in r for r in out["residual"]), out["residual"]
    assert not any("span_start — the canonical's value differs" in r
                   for r in out["residual"]), out["residual"]


# ── 15) round 12: the inbound malformation, the slot's VALUE, one fact once ──


def test_an_unlabelled_inbound_source_fails_closed():
    """`head(labels(s))` is None for a label-less source node, and the inbound arm
    used to test `isinstance(rel, str) and isinstance(source_label, str)` with NO
    else — so that row was neither `inbound` nor `unexpected`, and the edge died
    on an `ok=True` fold with no clause naming it. The OUTBOUND twin already
    failed closed on the same shape (`return None` → `unreadable`)."""
    graph = _FakeGraph(inbound=[("aboutObject", None)])
    out = capture_fold.carry_fold_evidence(
        _FakeProj(graph), "pt_dup", "pt_canon")
    assert out["ok"] is False and out["reason"] == "unreadable", out
    assert "could not be read" in out["blocked"], out


def test_the_slot_conflict_names_the_value_that_travelled():
    """Both spellings present, one slot: the write loop keeps the LAST spelling
    in `_SINGLE_SLOTS` order. Without naming it, the receipt said "copied
    validFrom" and "did not carry validFrom" about ONE slot in one sentence —
    both true of different values, neither named."""
    graph = _FakeGraph(
        dup_row=("session:s", [], "q", 10, 40, "turn-1", None,
                 "2026-08-01", "2026-08-02", None, None, None))
    sdk = _FakeSDK(on_update=graph.apply_slot)
    out = capture_fold.carry_fold_evidence(
        _FakeProj(graph), "pt_dup", "pt_canon", sdk=sdk)
    assert out["residual"], out
    item = next(r for r in out["residual"] if "held both" in r)
    assert "'2026-08-01' travelled" in item, item
    assert "'2026-08-02' went with the node" in item, item
    # And the clause names what was ACTUALLY written — not a guess: the evidence
    # read builds `scalar["when"] = when if when is not None else validFrom`, so
    # on a row holding BOTH the traveller is `when` (there is no per-spelling
    # ordering in the write loop, which is what an earlier comment claimed).
    assert "validFrom" in out["copied"], out
    assert sdk.calls and sdk.calls[0]["validFrom"] == "2026-08-01", sdk.calls


def test_a_fold_that_both_copied_and_disclosed_a_slot_names_both_values(
        sdk, monkeypatch):
    """The seam's whole sentence, pinned: the copy clause and the residual must
    not read as a contradiction about one slot."""
    _extracted, meta = _extract(
        sdk, monkeypatch,
        evidence={"when": "2026-08-01", "validFrom": "2026-08-02"})
    said = next(w for w in meta["warnings"] if "folded into canonical" in w)
    assert "copied validFrom" in said, said
    # The clause that follows names BOTH values and says which one travelled, so
    # "copied validFrom" and "did not carry validFrom" no longer contradict.
    assert "only '2026-08-01' travelled" in said, said
    assert "'2026-08-02' went with the node" in said, said


def test_one_fact_is_not_printed_twice_under_two_lead_ins():
    """`_already_written` prints the residual into `blocked`, and the seam then
    appended the whole residual again — "did not carry X; it also holds X" reads
    as TWO facts on the arm whose purpose is telling an operator what changed."""
    # The duplicate holds a support field the canonical does not, and the WRITE
    # door fails — the one arm where `_already_written` prints the residual.
    graph = _FakeGraph(dup_row=("session:s", [], "q", 10, 40, "turn-1", None,
                               None, None, None, 0.9, "observation"),
                       raise_on={"head(collect(r.confidence))"})
    out = capture_fold.carry_fold_evidence(
        _FakeProj(graph), "pt_dup", "pt_canon")
    assert out["ok"] is False, out
    assert out["blocked"].count("pointKind") == 1, out["blocked"]
    assert "it also holds pointKind" not in out["blocked"], out["blocked"]


def test_the_guard_reports_an_UNMEASURABLE_read_not_only_a_short_one():
    """The non-vacuity arm: a query selecting NO edge attribute must raise, or a
    declaration of `_CARRIED_EDGE_ATTRS` over such a read would be unmeasurable
    while the first check kept passing."""
    import pytest as _pytest
    monkeypatch = _pytest.MonkeyPatch()
    # ⚠️ The declaration must be EMPTY for this arm to be reachable at all: with
    # `confidence` declared, a query selecting nothing trips the FIRST check
    # ("does not select") and the non-vacuity arm is never exercised — which is
    # exactly why it was unpinned.
    monkeypatch.setattr(capture_fold, "_CARRIED_EDGE_ATTRS", ())
    try:
        with _pytest.raises(AssertionError, match="unmeasurable"):
            capture_fold._assert_carried_edge_attrs(
                "MATCH (d:Point {id:$dup})-[r]->(t) RETURN type(r)")
    finally:
        monkeypatch.undo()


# ── 16) round 13: a malformed REF element, a malformed GRADING, the clause ──


def test_a_non_string_ref_ELEMENT_refuses_rather_than_vanish():
    """`_refs_from` skipped any element that was not a `str`. The value is a
    DECLARED prop, so it never reached `undeclared`; it never entered the ref
    set, so it never reached a residual — and the duplicate was deleted with it.
    The CONTAINER malformation already refused; the element case is the same
    class with the opposite outcome."""
    row = ("session:s", [42], None, None, None, None, None, None,
           None, None, None, None)          # position 1 = extractedFromAll
    out = capture_fold.carry_fold_evidence(
        _FakeProj(_FakeGraph(dup_row=row)), "pt_dup", "pt_canon")
    assert out["ok"] is False and out["reason"] == "unreadable", out


def test_a_non_numeric_grading_is_disclosed_not_converted_to_ungraded():
    """`conf = confidence if isinstance(confidence, (int, float)) else None`
    erased the value, so `_carried`'s comparison was skipped and the duplicate's
    grading died on an `ok=True` fold with no clause naming it."""
    graph = _FakeGraph(edges=[("aboutObject", "Object", "obj_1", "high")],
                       carried={"obj_1": (1, None)})
    sdk = _FakeSDK()
    out = capture_fold.carry_fold_evidence(
        _FakeProj(graph), "pt_dup", "pt_canon", sdk=sdk)
    assert out["ok"] is True, out
    assert any("not a number: 'high'" in r for r in out["residual"]), out
    # …and the write door was never handed a string.
    assert sdk.calls == [], sdk.calls


def test_the_slot_conflict_does_not_claim_the_field_was_not_carried(sdk,
                                                                   monkeypatch):
    """The sinks prefix every residual with "did not carry ", so a residual whose
    SUBJECT is the field it just copied printed the contradiction one level up:
    "copied validFrom; did not carry validFrom (as when)"."""
    _extracted, meta = _extract(
        sdk, monkeypatch,
        evidence={"when": "2026-08-01", "validFrom": "2026-08-02"})
    said = next(w for w in meta["warnings"] if "folded into canonical" in w)
    assert "copied validFrom" in said, said
    assert "did not carry validFrom" not in said, said
    assert "did not carry the duplicate's second validFrom" in said, said


def test_the_vocabulary_guard_also_checks_the_READ_selects_the_declaration(
        monkeypatch):
    """`declared` keeps a field out of `undeclared`; only the SELECT puts it in
    front of the module. A field declared and never selected was neither carried
    nor reported — the drift the guard's docstring claims to stop."""
    import pytest as _pytest
    stripped = capture_fold._EVIDENCE_SELECT.replace(
        "{alias}.search_keys, ", "", 1)
    assert stripped != capture_fold._EVIDENCE_SELECT       # the strip LANDED
    monkeypatch.setattr(capture_fold, "_EVIDENCE_SELECT", stripped)
    with _pytest.raises(AssertionError, match="does not SELECT"):
        capture_fold.assert_evidence_vocabulary()


def test_the_seam_never_repeats_a_residual_the_module_already_printed():
    """The de-duplicator joined the blocked STRING character-wise
    (`" ".join("abc") == "a b c"`), so no item was ever found and one fact went
    out twice under two lead-ins. Pinned on the seam's own clause, not on
    `capture_fold`'s `blocked` (which is why the first pin stayed green)."""
    item = "pointKind (duplicate 'observation', canonical None)"
    assert sdk_mod._fold_residual_clause(
        {"blocked": f"…; did not carry {item}", "residual": [item]}) == ""
    assert sdk_mod._fold_residual_clause(
        {"blocked": "…", "residual": [item]}) == f"; it also holds {item}"
    assert sdk_mod._fold_residual_clause(
        {"blocked": None, "residual": []}) == ""


# ── 17) round 14: the two mechanisms the docstrings conflated, and the
#         "unproven" read-back arm ──


def test_the_provenance_exemption_is_grounded_on_the_CANONICAL_edge():
    """The exemption's ground is the canonical's own edge, NOT the row's field —
    the two are different mechanisms and the docstring used to describe the wrong
    one. Here the row's `extractedFrom` field is `session:s` while the duplicate's
    EDGE points at `src_other`: the edge proceeds only because the canonical
    holds an edge to `src_other` too, so the folded link's meaning survives."""
    row = ("session:s", [], None, None, None, None, None, None,
           None, None, None, None)                 # field names ONLY session:s
    graph = _FakeGraph(dup_row=row,
                       edges=[("extractedFrom", "Source", "src_other", None)])
    graph.canon_prov_targets = ["src_other"]       # the canonical holds the edge
    out = capture_fold.carry_fold_evidence(
        _FakeProj(graph), "pt_dup", "pt_canon")
    assert out["ok"] is True, out
    # …and with the canonical NOT holding it, the same edge refuses — so the two
    # mechanisms are distinguishable and neither is a formality.
    graph2 = _FakeGraph(dup_row=row,
                        edges=[("extractedFrom", "Source", "src_other", None)])
    out2 = capture_fold.carry_fold_evidence(
        _FakeProj(graph2), "pt_dup", "pt_canon")
    assert out2["ok"] is False and out2["reason"] == "unexpected_edge", out2


def test_an_unreadable_canonical_AFTER_the_write_says_unproven():
    """"we cannot say" and "the evidence is not there" are different facts, and
    this arm's whole purpose is the first one: the write raised nothing, the
    read-back cannot see the canonical (it vanished, or gained a second row), so
    the carry is UNPROVEN — and `copied` must stay empty rather than report a
    landing nothing verified."""
    # A free slot on the duplicate, so a write IS attempted (an empty
    # `to_write` never reaches the read-back this test is about).
    graph = _FakeGraph(dup_row=_DUP_ROW_QUOTED)

    def _vanish(props):
        graph.canon_rows = [list(graph.canon_row), list(graph.canon_row)]

    sdk = _FakeSDK(on_update=_vanish)
    out = capture_fold.carry_fold_evidence(
        _FakeProj(graph), "pt_dup", "pt_canon", sdk=sdk)
    assert out["ok"] is False and out["reason"] == "slot_write_failed", out
    assert "unproven" in out["blocked"], out["blocked"]
    assert out["copied"] == [], out


# ── 18) round 15: the class nothing read (LABELS), the wording that inverted
#         its own state, and the seam-level de-dup ──


def test_a_second_LABEL_on_the_duplicate_is_reported_not_lost():
    """A label is neither a prop nor an edge: `properties(d)` cannot see it, so
    `:Point:Object` was the ONE class carried by nothing, reported by nothing and
    refused by nothing — while `DETACH DELETE` removed the node and the label
    with it."""
    out = capture_fold.carry_fold_evidence(
        _FakeProj(_FakeGraph(dup_labels=("Point", "Object"))),
        "pt_dup", "pt_canon")
    assert any("label(s) Object" in r for r in out["residual"]), out
    # …and a plain `:Point` puts nothing on the receipt.
    plain = capture_fold.carry_fold_evidence(
        _FakeProj(_FakeGraph()), "pt_dup", "pt_canon")
    assert not any("label(s)" in r for r in plain["residual"]), plain


def test_the_grading_residual_names_the_TARGET_and_never_repeats():
    """`confidence of aboutObject→Object` was emitted twice for two links to
    DIFFERENT targets, so the receipt could not say which grading died."""
    edges = [("aboutObject", "Object", "obj_1", 0.4),
             ("aboutObject", "Object", "obj_2", 0.4)]
    graph = _FakeGraph(edges=edges,
                       carried={"obj_1": (1, 0.9), "obj_2": (1, 0.9)})
    out = capture_fold.carry_fold_evidence(
        _FakeProj(graph), "pt_dup", "pt_canon")
    assert out["ok"] is True, out
    graded = [r for r in out["residual"] if r.startswith("confidence of ")]
    assert len(graded) == 2, out["residual"]
    assert "obj_1" in graded[0] and "obj_2" in graded[1], graded
    assert len(set(out["residual"])) == len(out["residual"]), out["residual"]


def test_the_seam_prints_a_late_refusal_residual_ONCE(sdk, monkeypatch):
    """The fact-once rule pinned at the SEAM, not only in the helper's body: a
    post-write refusal whose `blocked` already carries the residual must reach
    the receipt once. (Mutating `_fold_residual_clause` back to the naive join
    used to leave every capture-level arm green — the helper's own unit test was
    the only red, which is the "body pinned, CALL not" shape.)"""
    item = "pointKind (duplicate 'observation', canonical None)"
    canned = {"ok": False, "reason": "link_not_carried",
              "blocked": ("its 1 attachment link(s) could not be carried onto "
                          f"the canonical: aboutObject→obj_1 (did not land); "
                          f"did not carry {item}"),
              "carried": 0, "created": 0, "copied": [], "residual": [item],
              "undeclared": []}
    monkeypatch.setattr(capture_fold, "carry_fold_evidence",
                        lambda *a, **k: dict(canned))
    _extracted, meta = _extract(sdk, monkeypatch)
    said = [w for w in meta["warnings"] if "kept distinct" in w]
    assert said, meta["warnings"]
    assert said[0].count(item) == 1, said[0]


# ── 19) round 16: the malformed shape that still vanished (a BLANK ref) ──


def test_a_BLANK_source_ref_is_reported_not_silently_dropped():
    """The malformed class was closed for a non-str CONTAINER and a non-str
    ELEMENT — but a whitespace-only element was skipped: `refs` stays empty, an
    empty set is a subset of the canonical's, the ref check passes, the fold
    succeeds, and a DECLARED prop dies with the node — neither carried, nor a
    residual, nor `undeclared`."""
    row = (" ", [], None, None, None, None, None, None,
           None, None, None, None)                 # extractedFrom = " "
    graph = _FakeGraph(dup_row=row)
    out = capture_fold.carry_fold_evidence(
        _FakeProj(graph), "pt_dup", "pt_canon")
    assert out["ok"] is True, out
    assert any("blank" in r for r in out["residual"]), out["residual"]
    # …and it does NOT become a refusal: a blank ref is not a MISSING ref, so the
    # duplicate's other evidence still folds.
    assert out["reason"] is None, out


# ── 20) round 17: the two disclosures that were mutation-deletable ──


def test_a_RETRACTED_duplicates_status_is_reported_not_swallowed():
    """`status` and `is_operator` were crossed in SILENCE while declared
    structural; the comment says `_REPORTED_FIELDS` now reports a difference —
    and nothing tested it. Measured by mutation: adding
    `and k not in ("status", "is_operator")` to the `support` comprehension left
    all 79 tests green, so re-silencing exactly these two fields was undetected.
    """
    out = capture_fold.carry_fold_evidence(
        _FakeProj(_FakeGraph(dup_status="retracted")), "pt_dup", "pt_canon")
    assert any("status (duplicate 'retracted', canonical None)" in r
               for r in out["residual"]), out


def test_an_OPERATOR_flag_is_reported_only_when_TRUTHY():
    """The second guard: `is_operator` False is the default, so reporting it would
    put a residual on every fold whose canonical lacks the flag. Only a truthy
    flag is a fact worth comparing."""
    op = capture_fold.carry_fold_evidence(
        _FakeProj(_FakeGraph(dup_is_operator=True)), "pt_dup", "pt_canon")
    assert any("is_operator (duplicate True, canonical None)" in r
               for r in op["residual"]), op
    plain = capture_fold.carry_fold_evidence(
        _FakeProj(_FakeGraph(dup_is_operator=False)), "pt_dup", "pt_canon")
    assert not any("is_operator" in r for r in plain["residual"]), plain


def test_a_DIFFERING_provenanceSource_refuses_the_fold():
    """`provenanceSource` is read as a REF, so a differing one must refuse like
    any other source reference. Only the "equal ⇒ silent" half was pinned:
    measured by mutation, passing `None` instead of `prov_source` into
    `_refs_from` left every test green — and with it a duplicate whose
    provenanceSource differs is DELETED with the value destroyed and no clause
    at all (it is declared, so not `undeclared`; it is not a reported field, so
    not a residual; and the ref set it should have joined is empty)."""
    out = capture_fold.carry_fold_evidence(
        _FakeProj(_FakeGraph(dup_prov_source="session:other")),
        "pt_dup", "pt_canon")
    assert out["ok"] is False and out["reason"] == "uncarried_source_ref", out
    assert "session:other" in out["blocked"], out
    # The equal case stays silent — that half is pinned by
    # `test_a_provenance_source_on_the_duplicate_is_declared_not_undeclared`.
    same = capture_fold.carry_fold_evidence(
        _FakeProj(_FakeGraph(dup_prov_source="session:s")),
        "pt_dup", "pt_canon")
    assert same["ok"] is True and not same["residual"], same


# ── 21) round 18: the OCCUPIED date slot — where "only X travelled" was false ──


def test_an_OCCUPIED_date_slot_says_NEITHER_spelling_travelled():
    """The dual-date residual is built by the READ, which cannot see the
    canonical's occupancy — and `when` only reaches the writer when the slot is
    FREE. On an occupied slot the receipt claimed "only '2026-08-01' travelled"
    one clause before "when (the canonical holds it as validFrom) — the
    canonical's value differs": the
    duplicate's date never travelled at all, and the canonical stored its own
    (measured through the real seam with a canonical `validFrom`)."""
    graph = _FakeGraph(
        dup_row=("session:s", [], "q", 10, 40, "turn-1", None,
                 "2026-08-01", "2026-08-02", None, None, None),
        canon_row=("session:s", [], "q", None, None, "turn-0", None,
                   None, "2026-09-09", None, None, None))
    sdk = _FakeSDK()
    out = capture_fold.carry_fold_evidence(
        _FakeProj(graph), "pt_dup", "pt_canon", sdk=sdk)
    assert out["ok"] is True, out
    item = next(r for r in out["residual"]
                if r.startswith("the duplicate's second validFrom"))
    assert "neither spelling travelled" in item, item
    assert "'2026-09-09'" in item, item               # the surviving date, named
    assert "travelled; " not in item, item            # the false clause is gone
    # …and the write door was never asked to hold a date.
    assert sdk.calls == [], sdk.calls
    # The other half — a FREE slot — still names the traveller (that pin already
    # existed; this asserts the two arms DISAGREE only where reality does).
    free_graph = _FakeGraph(
        dup_row=("session:s", [], "q", 10, 40, "turn-1", None,
                 "2026-08-01", "2026-08-02", None, None, None))
    free = capture_fold.carry_fold_evidence(
        _FakeProj(free_graph), "pt_dup", "pt_canon",
        sdk=_FakeSDK(on_update=free_graph.apply_slot))
    assert free["ok"] is True, free
    assert any("only '2026-08-01' travelled" in r for r in free["residual"]), free


# ── 22) round 19: the date verdict on the arms where NOTHING landed ──


def _dual_date_graph(**kw):
    return _FakeGraph(
        dup_row=("session:s", [], "q", 10, 40, "turn-1", None,
                 "2026-08-01", "2026-08-02", None, None, None), **kw)


def test_a_date_conflict_on_a_RAISED_write_claims_NO_travel():
    """The travel clause was completed as soon as the slot was FREE — before the
    write was issued — so the two write-failure arms printed "only '2026-08-01'
    travelled" one clause after "could not be written onto the canonical".
    Nothing travelled: the canonical gained neither value."""
    out = capture_fold.carry_fold_evidence(
        _FakeProj(_dual_date_graph()), "pt_dup", "pt_canon",
        sdk=_FakeSDK(raises=RuntimeError("no door")))
    assert out["ok"] is False and out["reason"] == "slot_write_failed", out
    item = next(r for r in out["residual"]
                if r.startswith("the duplicate's second validFrom"))
    assert "neither spelling travelled — the write did not land" in item, item
    assert "so only '2026-08-01' travelled" not in item, item


def test_a_date_conflict_on_an_UNREADABLE_read_back_claims_no_travel():
    """The "unproven" arm must not assert a landing either: the same verdict, but
    named for what it is (no travel CLAIMED, because nothing verified it)."""
    graph = _dual_date_graph()

    def _vanish(props):
        graph.canon_rows = [list(graph.canon_row), list(graph.canon_row)]

    out = capture_fold.carry_fold_evidence(
        _FakeProj(graph), "pt_dup", "pt_canon", sdk=_FakeSDK(on_update=_vanish))
    assert out["ok"] is False and out["reason"] == "slot_write_failed", out
    item = next(r for r in out["residual"]
                if r.startswith("the duplicate's second validFrom"))
    assert "no travel is claimed" in item, item
    assert "so only " not in item, item


def test_an_OUTBOUND_malformed_row_fails_closed_like_its_inbound_twin():
    """The outbound arm's `return None` on a null rel/label is the mirror of the
    inbound guard added in round 12 — and only the inbound side was pinned:
    replacing it with `continue` left all 83 tests green."""
    out = capture_fold.carry_fold_evidence(
        _FakeProj(_FakeGraph(edges=[(None, None, "x_1", None)])),
        "pt_dup", "pt_canon")
    assert out["ok"] is False and out["reason"] == "unreadable", out


def test_the_label_allowlist_backstop_still_fails_closed():
    """`_carried`'s allowlist check is INERT from the module's only call site
    (`read_fold_evidence` passes the dict's own value), so nothing exercised it:
    `if ... != label:` → `if False:` left all 83 tests green. It protects a
    future caller, so it is pinned directly rather than through the carry."""
    proj = _FakeProj(_FakeGraph(edges=[("aboutObject", "Object", "obj_1", 0.9)],
                                carried=(1, 0.9)))
    assert capture_fold._carried(proj, "pt_canon", "aboutObject", "Source",
                                 "obj_1") == (0, None)
    assert capture_fold._carried(proj, "pt_canon", "aboutObject", "Object",
                                 "obj_1") == (1, 0.9)


# ── 23) round 20: the presence tests, and the guard that only an OCCUPIED
#         date plus a free OTHER slot can reach ──


def test_the_dual_date_verdict_cannot_be_OVERWRITTEN_by_a_later_landing():
    """`_complete_slot_conflict`'s `"travelled" not in item` is the only thing
    stopping the LANDED verdict from appending a travel to an OCCUPIED slot's
    "neither spelling travelled" — and the existing occupied-slot pins never reach
    `_finish_date_conflict`, because they have nothing else to write. Here the
    canonical holds the date AND the duplicate has a free `search_keys` slot, so
    the write lands and the landed verdict is attempted on top."""
    graph = _FakeGraph(
        dup_row=("session:s", [], "q", 10, 40, "turn-1", "a b",
                 "2026-08-01", "2026-08-02", None, None, None),
        canon_row=("session:s", [], "q", None, None, "turn-0", None,
                   "2026-09-09", None, None, None, None))
    sdk = _FakeSDK(on_update=graph.apply_slot)
    out = capture_fold.carry_fold_evidence(
        _FakeProj(graph), "pt_dup", "pt_canon", sdk=sdk)
    assert out["ok"] is True, out
    assert out["copied"] == ["search_keys"], out       # the write DID land
    item = next(r for r in out["residual"]
                if r.startswith("the duplicate's second validFrom"))
    assert "neither spelling travelled" in item, item
    assert "travelled; " not in item, item


def test_a_FALSY_support_value_is_reported_like_the_pinned_slot_rule():
    """`support`'s presence test is `v is not None`, and its only pin used a
    truthy value: `v is not None` → `bool(v)` left every test green, and with it a
    duplicate whose `confidence` was 0.0 and whose `eventId` was "" lost BOTH
    silently — and the receipt then said "no per-occurrence evidence of its own to
    carry" over them."""
    graph = _FakeGraph(
        dup_row=("session:s", [], "q", 10, 40, "turn-1", None,
                 None, None, "", 0.0, None))
    sdk = _FakeSDK(on_update=graph.apply_slot)   # so the free slots DO land
    out = capture_fold.carry_fold_evidence(
        _FakeProj(graph), "pt_dup", "pt_canon", sdk=sdk)
    assert out["ok"] is True, out
    assert any("confidence (duplicate 0.0" in r for r in out["residual"]), out
    # `eventId` is reported by name; the exact parenthetical depends on the
    # fixture's canonical value, so assert the FIELD is named, not the form.
    assert any(r.startswith("eventId") for r in out["residual"]), out


def test_a_CANONICAL_EMPTY_STRING_part_of_the_address_is_OCCUPIED():
    """`address_held` tests `is not None`, and the projection really stores `''`.
    Mutating it to truthiness left every test green — and the fold then
    OVERWRITES the canonical's present (empty) text, so "the canonical's own text
    is untouched" breaks with no residual at all."""
    graph = _FakeGraph(
        dup_row=("session:s", [], "q dup", 10, 40, "turn-2", None,
                 None, None, None, None, None),
        # ONLY the empty `quote`: a canonical that also held a truthy part
        # (`source_turn_id`) would mask the mutation this pins.
        canon_row=("session:s", [], "", None, None, None, None,
                   None, None, None, None, None))
    sdk = _FakeSDK(on_update=graph.apply_slot)
    out = capture_fold.carry_fold_evidence(
        _FakeProj(graph), "pt_dup", "pt_canon", sdk=sdk)
    assert out["ok"] is True, out
    assert "quote" not in out["copied"], out
    assert sdk.calls == [], sdk.calls
    assert any("quote — the canonical's value differs" in r
               for r in out["residual"]), out


def test_a_CANONICAL_EMPTY_STRING_single_slot_is_OCCUPIED():
    """The same rule for the single slots: `held is not None`, not `held`."""
    graph = _FakeGraph(
        dup_row=("session:s", [], None, None, None, None, "a b",
                 None, None, None, None, None),
        canon_row=("session:s", [], None, None, None, None, "",
                   None, None, None, None, None))
    sdk = _FakeSDK(on_update=graph.apply_slot)
    out = capture_fold.carry_fold_evidence(
        _FakeProj(graph), "pt_dup", "pt_canon", sdk=sdk)
    assert out["ok"] is True, out
    assert "search_keys" not in out["copied"], out
    assert sdk.calls == [], sdk.calls


# ── 24) round 21: the residual that named the WRONG node, the de-dup line ──


def test_the_differing_date_names_what_EACH_node_holds__both_directions():
    """The residual was `f"{key} (as {write_key})"` — right for a duplicate
    holding `when`, INVERTED for one holding only `validFrom` against a canonical
    holding only `when`, where it printed "when (as validFrom)": a field neither
    node holds. Both directions are pinned here, and nothing pinned either
    before (mutating the spellings left every test green)."""
    dup_only_validfrom = _FakeGraph(
        dup_row=("session:s", [], None, None, None, None, None, None,
                 "2026-08-02", None, None, None),
        canon_row=("session:s", [], None, None, None, None, None,
                   "2026-08-01", None, None, None, None))
    out = capture_fold.carry_fold_evidence(
        _FakeProj(dup_only_validfrom), "pt_dup", "pt_canon")
    assert any("validFrom (the canonical holds it as when) — the canonical's "
               "value differs" in r for r in out["residual"]), out["residual"]
    assert not any("(as validFrom)" in r for r in out["residual"]), out

    dup_only_when = _FakeGraph(
        dup_row=("session:s", [], None, None, None, None, None,
                 "2026-08-01", None, None, None, None),
        canon_row=("session:s", [], None, None, None, None, None, None,
                   "2026-08-02", None, None, None))
    other = capture_fold.carry_fold_evidence(
        _FakeProj(dup_only_when), "pt_dup", "pt_canon")
    assert any("when (the canonical holds it as validFrom) — the canonical's "
               "value differs" in r for r in other["residual"]), other
    assert not any("(as when)" in r for r in other["residual"]), other


def test_the_residual_list_is_DEDUPLICATED_for_parallel_edges():
    """The wrapper's `dict.fromkeys` was presented as the "ONE place" fact-once is
    enforced and was pinned by nothing (replacing it with `list(...)` left every
    test green). The repeatable shape is two PARALLEL edges between the same pair:
    the read returns both, `_carried` reports each, and the two sentences are
    identical."""
    edges = [("aboutObject", "Object", "obj_1", 0.4),
             ("aboutObject", "Object", "obj_1", 0.4)]
    graph = _FakeGraph(edges=edges, carried={"obj_1": (1, 0.9)})
    out = capture_fold.carry_fold_evidence(
        _FakeProj(graph), "pt_dup", "pt_canon")
    assert out["ok"] is True, out
    graded = [r for r in out["residual"] if r.startswith("confidence of ")]
    assert graded == ["confidence of aboutObject→obj_1"], out["residual"]


def test_the_declared_bounds_are_named_on_the_arm_that_CARRIED_something(
        sdk, monkeypatch):
    """The write-history / edge-attribute disclosure was gated on
    `not carried and not copied` — exactly the state where nothing was carried —
    so the ORDINARY fold (one link carried) destroyed those classes with no
    clause naming them. This is that fold."""
    sdk.create_object("Acme")
    _extracted, meta = _extract(
        sdk, monkeypatch, evidence={"aboutEntities": ["Acme"]})
    said = next(w for w in meta["warnings"] if "folded into canonical" in w)
    assert "1 attachment link(s)" in said, said          # something WAS carried
    assert "#12146" in said, said                        # …and the bound is named
    assert "createdAt" in said, said


# ── 25) round 22: the verdict that must know whether the node is DELETED ──


def test_a_date_that_LANDED_but_a_LINK_that_refused_does_not_claim_a_deletion(
        sdk):
    """The travel verdict was completed when the free-slot write landed — BEFORE
    the link loop, which can still refuse. The refusal then printed "…so only X
    travelled; Y went with the node" right after "nothing removed from either
    node", over a duplicate the caller KEEPS."""
    proj = sdk._get_proj()
    for pid, content in (("pt_dd_dup", "the dual dated claim"),
                         ("pt_dd_canon", "the canonical claim")):
        sdk.create_point("statement", content, id=pid, dedup=False)
    proj.g.query(
        "MATCH (p:Point {id:'pt_dd_dup'}) "
        "SET p.when = '2026-08-01', p.validFrom = '2026-08-02' "
        "CREATE (p)-[:aboutSource]->(:Source {url:'doc:dual-9437'})")
    out = capture_fold.carry_fold_evidence(proj, "pt_dd_dup", "pt_dd_canon",
                                          sdk=sdk)
    assert out["ok"] is False and out["reason"] == "link_not_carried", out
    item = next(r for r in out["residual"]
                if r.startswith("the duplicate's second validFrom"))
    assert "the fold is REFUSED and the duplicate is kept" in item, item
    assert "went with the node" not in item, item


def test_an_UNLANDED_date_says_the_DATE_write_did_not_land(monkeypatch):
    """The partial-landing state (other slots landed, the date did not) read "the
    write did not land" over a `blocked` that reported the other slots as copied —
    and no test reached this outcome at all."""
    graph = _FakeGraph(
        dup_row=("session:s", [], "q", 10, 40, "turn-1", None,
                 "2026-08-01", "2026-08-02", None, None, None))

    def _skip_date(props):
        graph.apply_slot({k: v for k, v in props.items() if k != "validFrom"})

    out = capture_fold.carry_fold_evidence(
        _FakeProj(graph), "pt_dup", "pt_canon", sdk=_FakeSDK(on_update=_skip_date))
    assert out["ok"] is False and out["reason"] == "slot_write_failed", out
    item = next(r for r in out["residual"]
                if r.startswith("the duplicate's second validFrom"))
    assert "the date write did not land" in item, item
    assert "so only " not in item, item


def test_parallel_edges_count_as_ONE_link_present():
    """`carried` is "attachment links PRESENT after the write", and two PARALLEL
    edges to one target made it 2 over a graph holding one — the receipt then said
    "2 attachment link(s) present" for a single canonical edge."""
    edges = [("aboutObject", "Object", "obj_1", None),
             ("aboutObject", "Object", "obj_1", None)]
    out = capture_fold.carry_fold_evidence(
        _FakeProj(_FakeGraph(edges=edges, carried={"obj_1": (1, None)})),
        "pt_dup", "pt_canon")
    assert out["ok"] is True, out
    assert out["carried"] == 1, out


def test_the_bounds_are_disclosed_in_the_two_states_the_module_calls_NORMAL(
        sdk, monkeypatch):
    """The note was gated on `carried or copied`, so the states the module itself
    calls normal — every occurrence slot differs (residual-only), and a prop it
    cannot carry (undeclared-only) — disclosed NOTHING about the two classes the
    delete takes."""
    # residual-only: every address part differs from the canonical's
    _ex, meta = _extract(
        sdk, monkeypatch,
        canon_evidence={"quote": "canon words", "span_start": 0, "span_end": 11},
        evidence={"quote": "dup words", "span_start": 30, "span_end": 42})
    said = next(w for w in meta["warnings"] if "folded into canonical" in w)
    assert "did not carry quote" in said, said
    assert "#12146" in said and "createdAt" in said, said

    # undeclared-only: a prop this fold does not declare
    _ex2, meta2 = _extract(sdk, monkeypatch, evidence={"author": "someone"})
    said2 = next(w for w in meta2["warnings"] if "folded into canonical" in w)
    assert "author" in said2, said2
    assert "#12146" in said2 and "createdAt" in said2, said2


def test_a_COPIED_only_fold_says_what_was_copied_and_names_the_bounds(
        sdk, monkeypatch):
    """Pins the two arms a mutation could delete: `not copied` in the absence
    clause (deleting it prints "copied quote" AND "no per-occurrence evidence …
    to carry" in one sentence) and the `copied` half of the note's old guard."""
    _ex, meta = _extract(sdk, monkeypatch, evidence={"quote": "the dup words"})
    said = next(w for w in meta["warnings"] if "folded into canonical" in w)
    assert "copied quote" in said, said
    assert "no per-occurrence evidence" not in said, said
    assert "#12146" in said and "createdAt" in said, said
