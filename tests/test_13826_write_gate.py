"""#13626 — every writer that sets an entity ``embedding`` asks the write gate.

The strip's writer census (`tools/strip_graph_entity_vectors.py`) is the instrument that
PRODUCED the list of ungated writers; this file is its BEHAVIOURAL half. For each writer it
drives BOTH arms of `record_vector.mirror_required` through the writer's own entry point:

* ``True`` (the record has not proved it holds the label's vectors) — the vector lands, and
  that is the arm every deployment is on until a coverage count verifies;
* ``False`` (the record HAS proved it) — the vector does NOT land, while the ROW still does,
  in the same call. That is the half that makes a strip stick: a writer that skipped the
  row would be a hole, and a writer that skipped neither would undo the strip on its next
  call (`#13610` option 2).

⛔ THE AST HALF IS A DIFFERENT TEST, ON PURPOSE. Whether the gate ask can be SEEN by the
census (an ``if`` whose test asks about THIS label and whose arm stores/removes) is pinned by
`tests/test_13629_entity_vector_retire.py::test_every_declared_writer_of_a_retired_label_asks_the_gate`;
a writer could pass that and still write the vector on the stop arm, so the two halves are
not substitutes for one another.

⛔ ``restore_graph`` IS THE ONE WRITER WHOSE COMPLETENESS IS AT STAKE, so its falsifier also
asserts the negative that matters: a restore with NO tenant (the DR posture, where the gate
answers "keep") re-materialises the vector verbatim, and the strip it may perform is
REPORTED rather than silent.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile

import pytest

from tortoise import record_vector
from tortoise.hosted_backup import DUMP_FORMAT, restore_graph
from tortoise.projection import FalkorProjection
from tortoise.sdk import TortoiseSDK
from tortoise.vector_write import clear_then_set_prefix, set_embedding_clause

#: ⛔ THE STORE'S OWN WIDTH (384), not an arbitrary one: `_update_entity` runs the `#5004`
#: width guard on a CALLER-supplied vector and CLEARS a wrong-width one, so a narrow fixture
#: vector would make the mirror arm assert nothing while looking green.
VEC = [0.05] * 384
VEC2 = [0.07] * 384


# ── fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def proj():
    tmpdir = tempfile.mkdtemp(prefix="writegate13826_")
    p = FalkorProjection(os.path.join(tmpdir, "writegate.db"), graph_name="tortoise")
    try:
        yield p
    finally:
        p.close()
        shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.fixture
def sdk(tmp_path):
    s = TortoiseSDK(str(tmp_path / "sdk13826.db"))
    try:
        yield s
    finally:
        s.close()


def _gate(monkeypatch, required: bool) -> None:
    """Force the ONE predicate. `_derived_mirror_required` imports it per call, so this
    reaches every writer — folds, sdk doors and the restore alike.

    ⛔ ``**_kw`` for the same reason as :func:`_gate_map` (`#14560`): the DR restore asks
    with ``require_substantive=True``, and a positional-only stub would raise into the
    caller's fail-closed `except` — turning every "stop" into "keep" and greening these
    arms over a fold that never actually stopped. The keyword is ignored deliberately: the
    triviality rule is pinned separately.
    """
    monkeypatch.setattr(record_vector, "mirror_required",
                        lambda entity_type, **_kw: required, raising=False)


def _gate_map(monkeypatch, keep: set[str]) -> None:
    """Force the predicate PER LABEL: `keep` answers "required", everything else "not".

    ⛔ This is what makes the per-label claim FALSIFIABLE (#13828 review, P2): an
    `all-True`/`all-False` patch is satisfied by one boolean hoisted to the top of the
    method, by any permutation of the asks, and by a pop taken on the dict the five
    labels SHARE — measured, all three were green under `_gate` alone.
    """
    monkeypatch.setattr(record_vector, "mirror_required",
                        lambda entity_type, **_kw: entity_type in keep, raising=False)
    # ⛔ ``**_kw`` IS NOT TIDINESS (#14560): the DR restore asks with
    # ``require_substantive=True`` so a TRIVIAL verdict cannot license a drop, and a
    # one-positional-arg stub would raise into the caller's fail-closed `except` and turn
    # every "stop" answer into "keep" — the per-label AND below would then be green over
    # a door that never opened. The keyword is IGNORED here on purpose: what these arms
    # measure is the per-label conjunction, and the triviality rule has its own pin
    # (`tests/test_14560_operator_vectors.py::test_a_TRIVIAL_verdict_cannot_license_the_restore_drop`).


def _prop(g, match: str, prop: str):
    rows = g.query(f"MATCH {match} RETURN n.{prop}").result_set
    if not rows:
        return ("ABSENT-NODE",)
    return rows[0][0]


def _node_exists(g, match: str) -> bool:
    rows = g.query(f"MATCH {match} RETURN count(n)").result_set
    return bool(rows) and bool(rows[0][0])


# ── 1. the `:Event` folds ───────────────────────────────────────────────


def _count_session_embedding(monkeypatch):
    """Stub the session embedder AND count its calls.

    ⛔ Owning the count is what separates a WRITER from its DOOR (#13854 review, P3): the
    raw `:Event` door now gates too, so a writer whose own ask was deleted still stores no
    vector — the store cannot tell the two apart. Call this AFTER
    `_stub_session_metadata` (the later patch wins).
    """
    import tortoise.session_indexer as indexer

    calls: list[str] = []

    def _fake(*a, **k):
        calls.append("encode")
        return list(VEC)

    monkeypatch.setattr(indexer, "compute_session_embedding", _fake, raising=True)
    return calls


def _never_encode(monkeypatch):
    """A stub embedder that records the texts it was asked to encode."""
    from tortoise import derived_embedding

    calls: list[str] = []
    monkeypatch.setattr(derived_embedding, "encode",
                        lambda text, dim, **kw: (calls.append(text), VEC)[1],
                        raising=True)
    return calls


def test_the_event_fold_asks_the_gate(proj, monkeypatch):
    """`_upsert_event` gates the DERIVE; `_event_plain_merge` gates the STORE.

    Both are declared writers of `:Event`, and the carried vector (the journal's replay
    carrier) reaches the store through `_event_plain_merge`, so the two arms below cover
    one fold and one merge — including the case where the caller/journal SUPPLIES a vector
    and the fold never encodes one.
    """
    calls = _never_encode(monkeypatch)

    _gate(monkeypatch, True)
    proj._upsert_event({"eventId": "ev-13826-keep", "eventKind": "thing",
                        "subject": "alice", "object": "widget"})
    assert _prop(proj.g, "(n:Event {eventId:'ev-13826-keep'})", "embedding") is not None
    assert calls, "the mirror arm did not encode — the fixture never exercised the derive"

    _gate(monkeypatch, False)
    calls.clear()
    # (a) the DERIVED half: no encode at all, row written
    proj._upsert_event({"eventId": "ev-13826-drop", "eventKind": "thing",
                        "subject": "bob", "object": "gadget"})
    assert _prop(proj.g, "(n:Event {eventId:'ev-13826-drop'})", "embedding") is None, (
        "the fold stored a vector the record already owns")
    assert calls == [], f"the stop arm still spent an encode: {calls}"
    assert _prop(proj.g, "(n:Event {eventId:'ev-13826-drop'})", "eventKind") == "thing", (
        "the ROW was not written on the stop arm")
    # (b) the CARRIED half: a caller/journal vector must not be stored either
    proj._upsert_event({"eventId": "ev-13826-carried", "eventKind": "thing",
                        "embedding": list(VEC2)})
    assert _prop(proj.g, "(n:Event {eventId:'ev-13826-carried'})", "embedding") is None, (
        "`_event_plain_merge` stored the carried vector on the stop arm")
    assert _node_exists(proj.g, "(n:Event {eventId:'ev-13826-carried'})")

    # …and the mirror arm stores the CARRIED vector too (the same path, other answer).
    _gate(monkeypatch, True)
    proj._upsert_event({"eventId": "ev-13826-carried-keep", "eventKind": "thing",
                        "embedding": list(VEC2)})
    assert _prop(proj.g, "(n:Event {eventId:'ev-13826-carried-keep'})",
                 "embedding") is not None


# ── 2. the document fold ────────────────────────────────────────────────


def test_the_document_fold_asks_the_gate(proj, monkeypatch):
    """`_upsert_document` asks BOTH halves of the `:Source` node it writes.

    ⛔ ONE `:Source` VECTOR SERVES BOTH LABELS (#13854 review, the `:Source` acceptance
    gap). `LABEL_SERVED` splits the two populations on `document_kind`, so a node this
    fold writes need not belong to the `document` half at all — and the two writers that
    can SEE the shared node (`_update_entity`'s `:Source` arm and
    `hosted_backup.restore_graph`) both retire the vector only when BOTH `document` and
    `source` answer stop. Asking `document` alone left the mirror of that rule open in the
    one fold that ENCODES: with the `document` half covered and the `source` half not, the
    fold wrote NO vector — the "vector in neither store" state (#11395) the conservative
    arm forbids. That is the arm below at `keep={"source"}`, and it is RED on the pre-fix
    code (measured: `embedding is None` there).
    """
    calls = _never_encode(monkeypatch)

    _gate(monkeypatch, True)
    proj._upsert_document({"id": "https://example.com/doc-keep", "title": "My doc",
                           "summary": "a summary", "topics": ["x"]})
    assert _prop(proj.g, "(n:Source {url:'https://example.com/doc-keep'})",
                 "embedding") is not None
    assert calls, "the mirror arm did not encode"

    # ⛔ PER LABEL, through the SAME entry point (#13828 review's rule, applied here):
    # `document` covered + `source` uncovered must STILL write the vector, and the
    # reverse must too — only BOTH answering stop retires it.
    for _keep, _url in (("document", "doc-half"), ("source", "source-half")):
        calls.clear()
        _gate_map(monkeypatch, {_keep})
        _url = f"https://example.com/{_url}"
        proj._upsert_document({"id": _url, "title": "Half kept",
                               "summary": "one half still wants it"})
        assert _prop(proj.g, f"(n:Source {{url:'{_url}'}})", "embedding") is not None, (
            f"the document fold dropped the `:Source` vector while `{_keep}` answered "
            "KEEP — one vector serves both halves, so only BOTH stopping retires it")
        assert calls, f"the {_keep}-keep arm did not encode"

    _gate(monkeypatch, False)
    calls.clear()
    proj._upsert_document({"id": "https://example.com/doc-drop", "title": "Other doc",
                           "summary": "another summary", "topics": ["y"]})
    assert _prop(proj.g, "(n:Source {url:'https://example.com/doc-drop'})",
                 "embedding") is None, "the document fold stored a retired vector"
    assert calls == [], f"the stop arm still encoded: {calls}"
    assert _prop(proj.g, "(n:Source {url:'https://example.com/doc-drop'})",
                 "title") == "Other doc", "the ROW was not written on the stop arm"


def test_the_document_fold_is_credited_for_BOTH_halves_by_the_census():
    """⛔ THE CENSUS'S `any()` OVER A LABEL'S TYPES CANNOT SEE A LOST HALF (#13854 review C1).

    `:Source` is ONE label serving TWO `entity_type`s, so
    `test_every_declared_writer_of_a_retired_label_asks_the_gate` accepts a writer when
    **any** of the label's types answers gated. That union is right for the population
    question it asks — but it is silent about a fold that writes the one shared node and
    answers for only one of the two halves, which is exactly the `:Source` acceptance gap
    this PR closes. Measured at `9550359714`: with the fold spelled `if …("document"): …
    elif …("source"): …`, `_writer_consults_gate(…, ("source",))` answered True and
    `("document",)` answered **False** — the `elif` is the first `if`'s `orelse`, that arm
    stores, and the stop-arm rule then skips the outer `if` altogether — and the census test
    stayed GREEN on it, because `source` alone satisfied the union.

    So the union needs a per-half companion for the labels that HAVE two halves. Both
    answers are asserted: a spelling that loses the OTHER half is the same defect mirrored.
    """
    from tools import strip_graph_entity_vectors as strip

    for half in ("document", "source"):
        gated, why = strip._writer_consults_gate(
            "tortoise.projection.entities", "_upsert_document", (half,))
        assert gated is True, (
            f"the document fold writes the ONE `:Source` node but the census cannot see "
            f"it ask the gate about `{half}`: {why}")


# ── 3. the source fold (the caller passthrough) ─────────────────────────


def test_the_source_fold_asks_the_gate(proj, monkeypatch):
    """`_upsert_source` stores a CALLER vector through the passthrough.

    Its own `SET` list names no vector — `_persist_extra_props` is the store, and the
    closed `_SOURCE_EXTRA_PROPS` surface declares `embedding`. That is why the census
    keeps the fold in `:Source`'s population, and why the stop is a `pop` on a copy of the
    event rather than a skipped encode.
    """
    _gate(monkeypatch, True)
    proj._upsert_source({"url": "https://example.com/src-keep", "title": "Kept",
                         "embedding": list(VEC)})
    assert _prop(proj.g, "(n:Source {url:'https://example.com/src-keep'})",
                 "embedding") is not None

    _gate(monkeypatch, False)
    proj._upsert_source({"url": "https://example.com/src-drop", "title": "Dropped",
                         "embedding": list(VEC)})
    assert _prop(proj.g, "(n:Source {url:'https://example.com/src-drop'})",
                 "embedding") is None, (
        "the caller passthrough still stored the vector on the stop arm")
    assert _prop(proj.g, "(n:Source {url:'https://example.com/src-drop'})",
                 "title") == "Dropped", "the ROW was not written on the stop arm"
    assert _node_exists(proj.g, "(n:Source {url:'https://example.com/src-drop'})")

    # ⛔ THE MIXED ARMS (#13854 review): this fold MERGEs the same `:Source {url}` key space
    # `_upsert_document` writes, so one counter's answer is evidence about half the node and
    # a single STOP must not retire the caller's vector. Before this arm existed the fold
    # asked `source` alone and BOTH mixed states dropped the vector silently.
    for keep, tag in (("document", "src-mixed-d"), ("source", "src-mixed-s")):
        _gate_map(monkeypatch, {keep})
        url = f"https://example.com/{tag}"
        proj._upsert_source({"url": url, "title": tag, "embedding": list(VEC)})
        assert _prop(proj.g, f"(n:Source {{url:'{url}'}})", "embedding") is not None, (
            f"the caller vector was retired although `{keep}` answered KEEP — one `:Source` "
            "vector serves both halves of the node")
        assert _prop(proj.g, f"(n:Source {{url:'{url}'}})", "title") == tag


# ── 3b. `:Point` — the fold the writer REGISTER cannot see (#13971) ─────


def test_the_point_journal_fold_deliberately_keeps_the_mirror(proj, monkeypatch):
    """`:Point`'s four declared writers: two ask the gate, two deliberately must not — and
    the journal fold does not ask either, on purpose.

    ⛔ THIS TEST PINS AN EXCEPTION, and the exception is a RECORDED DECISION rather than a
    gap. `_upsert_point_props` is the journal fold: its vector is the JOURNAL's own record
    truth (`#5004`), so it must restore what the journal holds or `derived == replay(journal)`
    goes false the moment the record is the only copy. Two shipped pins state the product
    decision, and BOTH fail if this fold starts asking:

      * `record_vector.MIRROR_RETIRED_LABELS` must NOT contain `point`
        (`test_13610_graph_vector_copy.py::
        test_a_verified_label_whose_fold_still_writes_the_graph_keeps_the_fallback`), so the
        dense leg keeps a named fallback on `point`, the primary retrieval label; and
      * that set must equal the labels the folds stop writing — a fold that asked would
        break the equality from the other side.

    So the graph copy of `:Point` is retired by the STRIP plus the two writers that place a
    redundant copy (the SDK update door and `hosted_backup.restore_graph`), and the
    consequence is stated rather than hidden: a `rebuild_all` re-materialises the journal's
    vectors, so the strip is an operator action to re-run after a rebuild.
    `tools/strip_graph_entity_vectors.py` names that consequence in its own output and
    leaves `:Point` out of `FOLD_WRITERS`. ⛔ AND TWO MORE `:Point` WRITERS DELIBERATELY DO
    NOT ASK (`#13971` review, P1): `sdk::_write_capture_turns` (the graph batch runs exactly
    where the record cannot hold the turn, so the copy there is the SOLE store) and
    `projection::_rebuild_all_replay` (pass-1b restores a snapshot vector only for ids the
    journal wrote none for). The census therefore REFUSES `:Point`, naming both once the
    coverage verdict verifies (today the coverage arm answers first and names the two
    counts) — the
    strip stays unavailable until the capture path can answer per-turn, which is cheaper
    than a capture that writes a turn's vector to neither store.
    """
    from tortoise import embeddings

    encoded: list[str] = []
    monkeypatch.setattr(embeddings, "encode_for_row",
                        lambda text, dim, **kw: (encoded.append(text), VEC)[1],
                        raising=True)

    # (a) the JOURNAL RESTORE arm — reached by a live create and by a replay. The vector
    # survives on BOTH gate answers: this fold does not consult it.
    for flag, pid in ((True, "pt-13826-keep"), (False, "pt-13826-journalled")):
        _gate(monkeypatch, flag)
        proj._upsert_point_props({"id": pid, "content": "a claim",
                                  "embedding": list(VEC)})
        assert _prop(proj.g, f"(n:Point {{id:'{pid}'}})", "embedding") is not None, (
            f"the journal fold dropped the journal's own vector (gate={flag}) — "
            "`derived == replay(journal)` is now false")

    # (b) the RECOMPUTE arm — a pre-`#5004` journal record carries no vector, so the fold
    # encodes one. It also runs regardless of the gate, for the same reason.
    encoded.clear()
    _gate(monkeypatch, False)
    proj._upsert_point_props({"id": "pt-13826-recompute",
                              "content": "no journalled vector"})
    assert _prop(proj.g, "(n:Point {id:'pt-13826-recompute'})",
                 "embedding") is not None
    assert encoded, "the recompute arm did not encode at all"


# ── 4. `sdk::_update_entity` — the caller-prop door, per label ──────────


def test_update_entity_asks_the_gate_per_label(sdk, monkeypatch):
    """The door reaches FIVE labels, so `embedding` is gated against the label it is on.

    ⛔ THE `:Point` ARM IS GATED SINCE `#13971`, ON BOTH OF ITS TYPES, and this is the
    reason the ask is per-label rather than one answer for the whole method. ⛔ EVERY ARM
    BELOW IS PER-LABEL (#13828 review, P2): one answer for all five is
    satisfied by a single hoisted boolean, by any permutation of the asks, and by a pop
    taken on the dict the five labels share — all three measured green before this arm
    used `_gate_map`. The `object`-KEEP arm is the one that catches the shared-dict pop:
    an `:Object` write kept its vector only if the Subject/Source/Event asks did not
    strip it on the way past.
    """
    g = sdk._get_proj().g
    g.query("CREATE (o:Object {id: 'obj-13826', name: 'widget'})")
    g.query("CREATE (s:Subject {id: 'sub-13826', name: 'widgetness'})")
    g.query("CREATE (s:Source {id: 'src-13826', name: 'a page'})")

    # (1) `object` KEEP, every other label STOP: the Object write KEEPS its vector, and
    # the later asks in the same call must not take it away.
    _gate_map(monkeypatch, {"object"})
    sdk.update_knowledge("obj-13826", embedding=list(VEC))
    assert _prop(g, "(n:Object {id:'obj-13826'})", "embedding") is not None, (
        "an `:Object` write lost its vector because an EARLIER label's gate answer was "
        "applied to the dict all five labels share")

    # (2) `object` STOP: the row still lands, the vector does not.
    _gate_map(monkeypatch, {"subject", "event", "source", "document"})
    sdk.update_knowledge("obj-13826", embedding=None)
    g.query("MATCH (n:Object {id:'obj-13826'}) REMOVE n.embedding")
    sdk.update_knowledge("obj-13826", embedding=list(VEC))
    assert _prop(g, "(n:Object {id:'obj-13826'})", "embedding") is None, (
        "`update_entity` re-added the graph vector the strip removed")
    assert _prop(g, "(n:Object {id:'obj-13826'})", "name") == "widget", (
        "the ROW write did not land on the stop arm")

    # (3) the OTHER direction on a different label: `subject` STOP while `object` KEEPs.
    # A single boolean cannot tell (1) from (3).
    _gate_map(monkeypatch, {"object"})
    sdk.update_knowledge("sub-13826", embedding=list(VEC))
    assert _prop(g, "(n:Subject {id:'sub-13826'})", "embedding") is None, (
        "a `:Subject` write kept a vector its own label's gate said was retired")
    assert _prop(g, "(n:Subject {id:'sub-13826'})", "name") == "widgetness"

    # (4) `:Source` asks BOTH halves and retires only when BOTH answer stop — one
    # `:Source` vector serves the document and the provenance source, and `document`
    # coverage is the narrower population, so `source` alone is evidence about half.
    _gate_map(monkeypatch, {"source"})
    sdk.update_knowledge("src-13826", embedding=list(VEC))
    assert _prop(g, "(n:Source {id:'src-13826'})", "embedding") is not None, (
        "a `:Source` write was retired on ONE half's answer (`source` verified) while "
        "`document` was unverified — the rule `restore_graph` already applies")
    _gate_map(monkeypatch, set())
    # A FRESH `:Source` node: `SET n += $props` PRESERVES a property the props dict omits,
    # so the both-stop arm has to be proved on a row a strip would have left vectorless.
    g.query("CREATE (s:Source {id: 'src-13826-b', name: 'another page'})")
    sdk.update_knowledge("src-13826-b", embedding=list(VEC))
    assert _prop(g, "(n:Source {id:'src-13826-b'})", "embedding") is None, (
        ":Source kept its vector with BOTH halves answering stop")

    # (5) `:Event` is the FIFTH label and the one NO other arm reaches: its identity is
    # `eventId`, so every arm above misses it — deleting only the `label == "Event"`
    # branch left all four green (#13854 review, P2-1).
    g.query("CREATE (e:Event {eventId: 'ev-13826-ue', eventKind: 'thing'})")
    _gate_map(monkeypatch, {"object", "subject", "source", "document"})
    sdk.update_knowledge("ev-13826-ue", embedding=list(VEC))
    assert _prop(g, "(n:Event {eventId:'ev-13826-ue'})", "embedding") is None, (
        "an `:Event` write kept a vector its own label's gate said was retired")
    assert _prop(g, "(n:Event {eventId:'ev-13826-ue'})", "eventKind") == "thing", (
        "the ROW write did not land on the stop arm")
    g.query("CREATE (e:Event {eventId: 'ev-13826-ue-b', eventKind: 'thing'})")
    _gate_map(monkeypatch, {"event"})
    sdk.update_knowledge("ev-13826-ue-b", embedding=list(VEC))
    assert _prop(g, "(n:Event {eventId:'ev-13826-ue-b'})", "embedding") is not None, (
        "an `:Event` write lost its vector on its OWN label's KEEP answer — the ask is "
        "per label, not per method")

    # …and a `:Point` asks BOTH of its types (#13971) — the `:Source` rule, one label over.
    # ⛔ THIS ARM ASSERTED THE OPPOSITE BEFORE `#13971`, and the reversal is deliberate: the
    # graph copy was left ungated while coverage for `point`/`operator` was unproven and
    # every writer mirrored it unconditionally. `#13330` retired the in-graph `:Point`
    # index (so nothing READS the property) and `#13971` gated the writers on the same
    # measured predicate, so `#5004`'s "a Point vector is RECORD TRUTH" is now the reason
    # the RECORD holds it — never a reason to re-write the mirror after a strip.
    # ⛔ THE WHOLE `:Point` BLOCK RUNS WITH THE HOLD LIFTED (`#14560` review, P2). `:Point`'s
    # drop is HELD (`hosted_backup._RESTORE_DROP_HELD`, `#13971`'s per-ID ask is outstanding) on
    # BOTH doors now, so with the real set NO `:Point` write can pop at all — and the two
    # one-half arms below would then pass under a mutant that drops EITHER conjunct (or the
    # whole gate block), which is exactly what they exist to catch. Lifting the hold for the
    # block restores their discrimination; the hold itself is re-asserted at the end, on the
    # REAL set, so it is graded rather than assumed.
    import tortoise.hosted_backup as _hb
    _hold = _hb._RESTORE_DROP_HELD
    monkeypatch.setattr(_hb, "_RESTORE_DROP_HELD", frozenset())
    g.query("CREATE (p:Point {id: 'pt-13826', content: 'a claim'})")
    _gate_map(monkeypatch, {"point"})
    sdk.update_knowledge("pt-13826", embedding=list(VEC))
    assert _prop(g, "(n:Point {id:'pt-13826'})", "embedding") is not None, (
        "a `:Point` write was retired on ONE half's answer (`point` verified) while "
        "`operator` was unverified — one label, two populations, the `:Source` rule")
    # ⛔ THE DISCRIMINATING ARM (#13971 review, P3 on the first cut): the SAME question from
    # the OTHER half. `keep={"point"}` above proves the code asks `point`; on its own it is
    # also satisfied by a mutant that asks ONLY `point` and dropped the `operator` conjunct
    # (`point` True -> keep), so the pair of arms is what pins the CONJUNCTION.
    g.query("MATCH (n:Point {id:'pt-13826'}) REMOVE n.embedding")
    _gate_map(monkeypatch, {"operator"})
    sdk.update_knowledge("pt-13826", embedding=list(VEC))
    assert _prop(g, "(n:Point {id:'pt-13826'})", "embedding") is not None, (
        "a `:Point` write was retired with `operator` KEEPING — the ask is not `point` "
        "alone, exactly as for `:Source`'s two halves")
    g.query("MATCH (n:Point {id:'pt-13826'}) REMOVE n.embedding")
    _gate_map(monkeypatch, set())
    sdk.update_knowledge("pt-13826", embedding=list(VEC))
    assert _prop(g, "(n:Point {id:'pt-13826'})", "embedding") is None, (
        "a `:Point` write kept the graph vector with BOTH halves answering stop and the "
        "hold LIFTED — the conjunction is not being asked")
    assert _prop(g, "(n:Point {id:'pt-13826'})", "content") == "a claim", (
        "the ROW write did not land on the stop arm")
    # ⛔ AND THE HOLD, ON THE REAL SET (`#14560` review, P1). This door journals NO embedding
    # record (`#4094`), so a pop here does not "let the record hold it": the caller's vector
    # reaches NEITHER store and the call still returns success. The same both-stop answer
    # therefore KEEPS the vector until `#13971` can answer about the row being written.
    g.query("MATCH (n:Point {id:'pt-13826'}) REMOVE n.embedding")
    monkeypatch.setattr(_hb, "_RESTORE_DROP_HELD", _hold)
    sdk.update_knowledge("pt-13826", embedding=list(VEC))
    assert _prop(g, "(n:Point {id:'pt-13826'})", "embedding") is not None, (
        "the hold is not in force on the SDK door: a caller-supplied `:Point` vector was "
        "dropped from the props, and this door journals no embedding record — the write "
        "landed in neither store")


def test_update_entity_journals_the_gated_state(sdk, monkeypatch):
    """The strip must SURVIVE a rebuild, so the journal cannot carry the vector.

    ⛔ THE HALF THE STORE CANNOT SEE (#13854 review, P2-2). `_journal_entity_mutation`'s
    `state` is what `rebuild_all` replays with `SET n += $state`, so a gated write that
    journaled the CALLER's dict would re-add the vector on the next rebuild while every
    store assertion in this file stayed green. Mutating the `state=` argument to
    `_props_base` is exactly that mutant, and it reddened nothing before this case.
    """
    g = sdk._get_proj().g
    g.query("CREATE (o:Object {id: 'obj-13826-j', name: 'widget'})")
    captured: list[dict] = []
    real = TortoiseSDK._journal_entity_mutation

    def _capture(self, label, id_val, op, **kw):
        captured.append({"label": label, "id": id_val, "op": op,
                         "state": kw.get("state")})
        return real(self, label, id_val, op, **kw)

    monkeypatch.setattr(TortoiseSDK, "_journal_entity_mutation", _capture)
    _gate_map(monkeypatch, {"subject", "event", "source", "document"})
    sdk.update_knowledge("obj-13826-j", embedding=list(VEC), name="gadget")

    rows = [r for r in captured if r["id"] == "obj-13826-j"]
    assert rows, f"the update journaled nothing at all: {captured}"
    state = rows[-1]["state"] or {}
    assert "embedding" not in state, (
        "the EntityMutated journal carried the RETIRED vector, so a rebuild re-adds it "
        f"through `SET n += $state` — the strip does not survive: {state}")
    assert state.get("name") == "gadget", (
        f"the journal did not carry the key that DID land, so it replays nothing: {state}")


# ── 5. the raw `:Event` write door ─────────────────────────────────────


def test_the_raw_event_write_door_asks_the_gate(sdk, monkeypatch):
    """The door's statement is the CALLER's; its only vector is the `$embedding` param."""
    proj = sdk._get_proj()
    statement = ("MERGE (e:Event {eventId:$eid}) " + clear_then_set_prefix("e")
                 + "SET e += $props, " + set_embedding_clause("e"))

    _gate(monkeypatch, True)
    assert sdk._event_raw_write_door(
        proj, statement, event_id="ev-13826-door", props={"eventKind": "thing"},
        event_kind="thing", embedding=list(VEC), lane="test-13826") is True
    assert _prop(proj.g, "(n:Event {eventId:'ev-13826-door'})", "embedding") is not None

    _gate(monkeypatch, False)
    assert sdk._event_raw_write_door(
        proj, statement, event_id="ev-13826-door-drop", props={"eventKind": "thing"},
        event_kind="thing", embedding=list(VEC), lane="test-13826") is True
    assert _prop(proj.g, "(n:Event {eventId:'ev-13826-door-drop'})", "embedding") is None
    assert _prop(proj.g, "(n:Event {eventId:'ev-13826-door-drop'})",
                 "eventKind") == "thing", "the ROW was not written on the stop arm"


# ── 6. the session Event writer ────────────────────────────────────────


def _stub_session_metadata(monkeypatch, *, summary="a session"):
    import tortoise.session_indexer as indexer

    monkeypatch.setattr(indexer, "compute_session_embedding",
                        lambda *a, **k: list(VEC), raising=True)
    monkeypatch.setattr(
        indexer, "extract_keywords_from_frontmatter",
        lambda *a, **k: {"summary": summary, "keywords": ["k"], "topics": ["t"],
                         "narrative_arc": [], "issues": [], "prs": [],
                         "critical_decisions": []},
        raising=True)
    monkeypatch.setattr(
        indexer, "extract_metadata",
        lambda *a, **k: {"summary": summary, "keywords": ["k"], "topics": ["t"],
                         "narrative_arc": [], "issues": [], "prs": [],
                         "critical_decisions": []},
        raising=True)


def test_session_event_write_asks_the_gate(sdk, monkeypatch):
    """The writer still reports the vector it COMPUTED, while storing none.

    ⛔ That distinction is load-bearing, not cosmetic: its caller reads `None` as "the
    embedder failed" and records a repair marker, so folding the gate into the return
    value would make a gated deployment retry every session file for ever.
    """
    _stub_session_metadata(monkeypatch)
    g = sdk._get_proj().g
    kwargs = dict(event_id="ev-13826-sess", session_id="sess-13826",
                  content_hash="hash-1", title="A Session", extract_metadata=True,
                  llm_model=None, sf_rel="sess.md", rel="sess.md")

    _gate(monkeypatch, True)
    assert sdk._session_event_write({"title": "A Session"}, "body", "sess.md",
                                    **kwargs) == list(VEC)
    assert _prop(g, "(n:Event {eventId:'ev-13826-sess'})", "embedding") is not None

    _gate(monkeypatch, False)
    returned = sdk._session_event_write({"title": "B Session"}, "body", "sess.md",
                                        **{**kwargs, "event_id": "ev-13826-sess-drop"})
    assert returned == list(VEC), (
        "the gate leaked into the RETURN value, so the caller would read a healthy "
        "session as an embedder outage and record a repair marker for ever")
    assert _prop(g, "(n:Event {eventId:'ev-13826-sess-drop'})", "embedding") is None
    assert _prop(g, "(n:Event {eventId:'ev-13826-sess-drop'})",
                 "eventStatus") == "indexed", "the ROW was not written on the stop arm"


# ── 7. the embedding repair path ───────────────────────────────────────


def test_the_embedding_repair_asks_the_gate(sdk, monkeypatch):
    """A repair is a write; with the mirror retired there is nothing to repair.

    It must not be reported as a FAILURE either — the caller treats "failed" as retryable,
    and a gated deployment would re-walk every such file on every sweep. And it must not be
    reported as WORK: `healed` tells the caller a write happened, and a retirement writes
    neither the Event nor a vector (#13826 review).
    """
    _stub_session_metadata(monkeypatch)
    monkeypatch.setattr(TortoiseSDK, "_session_embedding",
                        lambda self, *a, **k: list(VEC), raising=True)
    g = sdk._get_proj().g

    _gate(monkeypatch, True)
    g.query("CREATE (e:Event {eventId: 'ev-13826-repair', eventKind: 'AgentSession'})")
    assert sdk._embedding_repair_attempt(
        "ev-13826-repair", {"title": "A Session"}, "body", "A Session", 24, None)[0] == "healed"
    assert _prop(g, "(n:Event {eventId:'ev-13826-repair'})", "embedding") is not None

    _gate(monkeypatch, False)
    g.query("CREATE (e:Event {eventId: 'ev-13826-repair-drop', eventKind: 'AgentSession'})")
    attempt = sdk._embedding_repair_attempt(
        "ev-13826-repair-drop", {"title": "B Session"}, "body", "B Session", 24, None)
    assert attempt[0] == "retired", (
        "the retired mirror was reported as a FAILED repair (which the sweep retries for "
        "ever) or as `healed`, which the caller reports as `updated` for a run that wrote "
        f"neither the Event nor a vector: {attempt}")
    assert _prop(g, "(n:Event {eventId:'ev-13826-repair-drop'})", "embedding") is None
    assert _prop(g, "(n:Event {eventId:'ev-13826-repair-drop'})",
                 "embeddingRepairFailedAt") is None, (
        "a retired mirror recorded a repair-failure marker")
    assert _node_exists(g, "(n:Event {eventId:'ev-13826-repair-drop'})")


# ── 8. the frozen legacy corpus ingest ─────────────────────────────────


def test_ingest_corpus_asks_the_gate(sdk, monkeypatch, tmp_path):
    """The frozen legacy branch is a declared `:Event` writer, so it asks too.

    The public spelling `TortoiseSDK.ingest_corpus` was retired by `#4282` (owner ruling
    `#3836` (c)), and this branch's base already carries the private spelling; the pin is
    stated here because the writer that asks is `_ingest_corpus`, which is also the key
    `tortoise.embed_writer_register` declares. A test that cannot reach its subject is a
    red gate, not a passing one.
    """
    _stub_session_metadata(monkeypatch)
    calls = _count_session_embedding(monkeypatch)
    corpus = tmp_path / "corpus-13826"
    corpus.mkdir()
    (corpus / "sess.md").write_text(
        "---\ntitle: A Session\nsessionId: sess-13826\n---\nuser: hi\n", encoding="utf-8")
    g = sdk._get_proj().g

    _gate(monkeypatch, True)
    # ⛔ `eventKind` DISPATCHES this frozen branch — it does not classify the frontmatter
    # (that is the new `index_sources_from_directory` path), so the session arm is only
    # reached by naming it.
    out = sdk._ingest_corpus(str(corpus), eventKind="AgentSession", extract_metadata=True)
    assert out["ingested"] == 1, out
    rows = g.query("MATCH (e:Event {session_id:'sess-13826'}) RETURN e.embedding"
                   ).result_set
    assert rows and rows[0][0] is not None, (
        f"the mirror arm of the legacy corpus ingest stored no vector: {rows}")
    assert calls, "the mirror arm spent no encode — the fixture never exercised it"
    calls.clear()

    g.query("MATCH (e:Event) DETACH DELETE e")
    _gate(monkeypatch, False)
    out = sdk._ingest_corpus(str(corpus), eventKind="AgentSession", extract_metadata=True)
    assert out["ingested"] == 1, out
    rows = g.query("MATCH (e:Event {session_id:'sess-13826'}) "
                   "RETURN e.embedding, e.eventStatus").result_set
    assert rows, "the ROW was not written on the stop arm"
    assert rows[0][0] is None, "the legacy corpus ingest stored a retired vector"
    assert rows[0][1] == "indexed", rows
    # ⛔ THE STORE ALONE DOES NOT DISCRIMINATE (#13854 review, P3): the raw `:Event` door
    # gates too, so a writer whose OWN gate was deleted would compute a vector, hand it to
    # the door, and still store none — green. The COUNT is what pins the writer.
    assert calls == [], (
        f"the legacy corpus ingest computed a vector the record already owns: {calls}")

    # ⛔ THE UPDATE ARM IS A SEPARATE WRITER (#13828 review, P2): the two runs above both
    # took the CREATE arm because the node was absent (the first run created it, the
    # second started from a wiped graph), so deleting the gate from the update arm left
    # this test green. A `:Event` carries its vector as "preserve on null", so the
    # discriminating state is a row with NO stored vector — which is exactly the state a
    # strip leaves — and an update that must not re-mint one. Same `sessionId`, different
    # bytes, so the hash differs and the update arm is taken.
    g.query("MATCH (e:Event {session_id:'sess-13826'}) REMOVE e.embedding")
    (corpus / "sess.md").write_text(
        "---\ntitle: A Session\nsessionId: sess-13826\n---\nuser: hi again\n",
        encoding="utf-8")
    _gate(monkeypatch, False)
    out = sdk._ingest_corpus(str(corpus), eventKind="AgentSession", extract_metadata=True)
    assert out["updated"] == 1, out
    rows = g.query("MATCH (e:Event {session_id:'sess-13826'}) RETURN e.embedding"
                   ).result_set
    assert rows, "the row vanished on the update arm's stop arm"
    assert rows[0][0] is None, (
        "the legacy corpus ingest's UPDATE arm re-minted a retired vector — it is a "
        "separate writer from the create arm and was ungated here")
    assert calls == [], (
        "the legacy corpus ingest's UPDATE arm still computed a vector on the stop "
        f"arm: {calls}")

    (corpus / "sess.md").write_text(
        "---\ntitle: A Session\nsessionId: sess-13826\n---\nuser: hi a third time\n",
        encoding="utf-8")
    _gate(monkeypatch, True)
    out = sdk._ingest_corpus(str(corpus), eventKind="AgentSession", extract_metadata=True)
    assert out["updated"] == 1, out
    rows = g.query("MATCH (e:Event {session_id:'sess-13826'}) RETURN e.embedding"
                   ).result_set
    assert rows and rows[0][0] is not None, (
        "the mirror arm of the legacy corpus UPDATE stored no vector")



# ── 9. the session indexer CLI ─────────────────────────────────────────


def test_the_session_indexer_cli_asks_the_gate(sdk, monkeypatch, capsys, tmp_path):
    """`python -m tortoise.session_indexer` is a declared `:Event` writer as well."""
    import tortoise.sdk as sdk_module
    from tortoise import session_indexer

    _stub_session_metadata(monkeypatch)
    calls = _count_session_embedding(monkeypatch)
    monkeypatch.setattr(sdk_module, "TortoiseSDK", lambda *a, **k: sdk, raising=True)
    g = sdk._get_proj().g
    keep_calls: list[int] = []
    stop_calls: list[int] = []
    for _ in range(2):
        session = tmp_path / f"sess-{_}.md"
        session.write_text("---\ntitle: A Session\n---\nuser: hi\n", encoding="utf-8")
        # `main()` writes `TORTOISE_DB_URI` itself; record the current value so the
        # teardown restores it (the same care `tests/test_9079_event_writers_journal.py`
        # takes — a leaked endpoint outlives the module).
        monkeypatch.setenv("TORTOISE_DB_URI", os.environ.get("TORTOISE_DB_URI", ""))
        monkeypatch.setattr(sys, "argv", [
            "session_indexer", str(session), "--db", "docker://:x@localhost:1/g",
            "--no-llm",
        ])
        _gate(monkeypatch, _ == 0)
        calls.clear()
        session_indexer.main()
        (keep_calls if _ == 0 else stop_calls).append(len(calls))

    rows = g.query("MATCH (e:Event {eventKind:'AgentSession'}) "
                   "RETURN e.embedding ORDER BY e.source_file").result_set
    assert len(rows) == 2, rows
    assert rows[0][0] is not None, "the mirror arm of the CLI stored no vector"
    assert rows[1][0] is None, "the CLI stored a retired vector"
    # ⛔ The STORE cannot tell the CLI's own ask from the door's (#13854 review, P3): the
    # vector is handed to `_event_raw_write_door`, which gates `:Event` as well, so a CLI
    # whose gate was deleted would still store none. What the CLI owns is the COMPUTE.
    assert keep_calls == [1], f"the mirror arm did not encode exactly once: {keep_calls}"
    assert stop_calls == [0], (
        f"the CLI computed a vector the record already owns: {stop_calls}")
    capsys.readouterr()


# ── 10. the restore ───────────────────────────────────────────────────


def _dump(*nodes) -> dict:
    return {"format": DUMP_FORMAT, "nodes": list(nodes), "edges": []}


def test_restore_graph_asks_the_gate(proj, monkeypatch):
    """A restore re-materialises a dump, so it is a writer of every label it carries.

    ⛔ THREE ARMS, because the RESTORE is the one writer where the gate's conservatism is
    the completeness argument: an unknown (no tenant, unarmed) keeps the vector, and only a
    proven answer drops it — and then only the VECTOR, with the node, its labels and its
    other properties written, and the drop REPORTED.
    """
    dump = _dump({"dump_id": 1, "labels": ["Event"],
                  "props": {"eventId": "ev-13826-restore", "eventKind": "thing",
                            "embedding": list(VEC)}})

    _gate(monkeypatch, True)
    result = restore_graph(proj.g, dump)
    assert _prop(proj.g, "(n:Event {eventId:'ev-13826-restore'})",
                 "embedding") is not None, (
        "the restore dropped a vector it was told to keep — a restored graph left short")
    assert "retired_vectors" not in result

    # `_query` is the guarded handle's own delegate (`#14548`'s ratchet counts a NEW
    # untyped `.query(` receiver as growth); `query()`/`ro_query()` both route here.
    proj.g._query("MATCH (n) DETACH DELETE n")
    _gate(monkeypatch, False)
    result = restore_graph(proj.g, dump)
    assert _prop(proj.g, "(n:Event {eventId:'ev-13826-restore'})",
                 "embedding") is None, "the restore re-created a retired vector"
    assert _prop(proj.g, "(n:Event {eventId:'ev-13826-restore'})",
                 "eventKind") == "thing", "the node's other properties were not restored"
    assert result["nodes"] == 1, result
    # ⛔ REPORTED, never silent: the count and the labels are part of the contract.
    assert result["retired_vectors"] == 1, result
    assert result["retired_vector_labels"] == ["Event"], result

    # ⛔ ONE DUMP NODE PER GATED LABEL (#13854 review, P2-3/P2-4). The single `:Event`
    # dump left three arms unfalsifiable: deleting the `Object` branch, deleting the
    # `Subject` branch, or turning the `:Source` both-ask's `and not` into `or not`
    # (retire on ONE half's answer) each reddened nothing.
    _gate_map(monkeypatch, set())
    proj.g.query("MATCH (n) DETACH DELETE n")
    kept = _dump(
        {"dump_id": 2, "labels": ["Object"],
         "props": {"id": "obj-13826-restore", "name": "w",
                   "embedding": list(VEC)}},
        {"dump_id": 3, "labels": ["Subject"],
         "props": {"id": "sub-13826-restore", "name": "s",
                   "embedding": list(VEC)}},
    )
    result = restore_graph(proj.g, kept)
    assert _prop(proj.g, "(n:Object {id:'obj-13826-restore'})",
                 "embedding") is None, "the restore re-created an `:Object`'s vector"
    assert _prop(proj.g, "(n:Subject {id:'sub-13826-restore'})",
                 "embedding") is None, "the restore re-created a `:Subject`'s vector"
    assert _prop(proj.g, "(n:Object {id:'obj-13826-restore'})", "name") == "w", (
        "the `:Object` node's other properties were not restored")
    assert result["retired_vectors"] == 2, result
    assert result["retired_vector_labels"] == ["Object", "Subject"], result

    # (b) the OTHER direction per label: a label the gate KEEPS keeps its vector while
    # its siblings are dropped — a mutant that pops unconditionally reddens here.
    _gate_map(monkeypatch, {"object"})
    proj.g.query("MATCH (n) DETACH DELETE n")
    mixed = _dump(
        {"dump_id": 4, "labels": ["Object"],
         "props": {"id": "obj-13826-keep", "name": "w2",
                   "embedding": list(VEC)}},
        {"dump_id": 5, "labels": ["Subject"],
         "props": {"id": "sub-13826-drop", "name": "s2",
                   "embedding": list(VEC)}},
    )
    result = restore_graph(proj.g, mixed)
    assert _prop(proj.g, "(n:Object {id:'obj-13826-keep'})",
                 "embedding") is not None, (
        "the restore dropped an `:Object` vector the gate answered KEEP for")
    assert _prop(proj.g, "(n:Subject {id:'sub-13826-drop'})",
                 "embedding") is None, (
        "the restore kept a `:Subject` vector the gate answered STOP for")
    assert result["retired_vector_labels"] == ["Subject"], result

    # (c) `:Source` drops only when BOTH halves answer stop. `document` KEEPs and
    # `source` STOPs: an `or` would retire the node and lose the vector the record may
    # not hold — the #11395 direction the both-ask exists to prevent.
    _gate_map(monkeypatch, {"document"})
    proj.g.query("MATCH (n) DETACH DELETE n")
    both = _dump({"dump_id": 6, "labels": ["Source"],
                  "props": {"url": "https://example.com/restore-both-13826",
                            "title": "both-ask", "embedding": list(VEC)}})
    result = restore_graph(proj.g, both)
    assert _prop(proj.g, "(n:Source {url:'https://example.com/restore-both-13826'})",
                 "embedding") is not None, (
        "the restore retired a `:Source` vector on ONE half's answer (`source` stop, "
        "`document` keep) — the both-ask rule is what forbids that")
    assert "retired_vectors" not in result, result
    _gate_map(monkeypatch, {"source"})
    proj.g.query("MATCH (n) DETACH DELETE n")
    both2 = _dump({"dump_id": 7, "labels": ["Source"],
                   "props": {"url": "https://example.com/restore-both2-13826",
                             "title": "both-ask-2", "embedding": list(VEC)}})
    result = restore_graph(proj.g, both2)
    assert _prop(proj.g, "(n:Source {url:'https://example.com/restore-both2-13826'})",
                 "embedding") is not None, (
        "the restore retired a `:Source` vector on the `source` half alone while "
        "`document` was unverified")
    _gate_map(monkeypatch, set())
    proj.g.query("MATCH (n) DETACH DELETE n")
    both3 = _dump({"dump_id": 8, "labels": ["Source"],
                   "props": {"url": "https://example.com/restore-both3-13826",
                             "title": "both-ask-3", "embedding": list(VEC)}})
    result = restore_graph(proj.g, both3)
    assert _prop(proj.g, "(n:Source {url:'https://example.com/restore-both3-13826'})",
                 "embedding") is None, (
        "the restore kept a `:Source` vector with BOTH halves answering stop")
    assert result["retired_vector_labels"] == ["Source"], result


def test_restore_graph_with_no_tenant_keeps_every_vector(proj, monkeypatch):
    """The DR posture: no tenant, so the gate answers `keep` and the dump is verbatim.

    Driven through the REAL predicate (no forcing) — `mirror_required` is conservative in
    every unknown, which is what makes gating a restore safe at all.
    """
    from tortoise.record_writer import record_tenant_effective

    assert record_tenant_effective() is None, (
        "fixture: this test is about the no-tenant answer")
    dump = _dump({"dump_id": 1, "labels": ["Source"],
                  "props": {"url": "https://example.com/restore-dr",
                            "title": "DR doc", "embedding": list(VEC)}})
    result = restore_graph(proj.g, dump)
    assert _prop(proj.g, "(n:Source {url:'https://example.com/restore-dr'})",
                 "embedding") is not None, (
        "a tenant-less restore dropped a vector, so a DR artifact came back short")
    assert "retired_vectors" not in result


def test_restore_graph_retires_only_when_every_gated_label_stops(proj, monkeypatch):
    """A co-labelled node has ONE vector, so the drop is `all`, not `any` (#13854 review, F4).

    `:Source` is the shape this rule was written for (its vector serves `document` AND
    `source`) and it already asked both. A `:Subject` that also carries `:Object` is the
    same node with the same property — `sdk.py` models it (`_sibling`) and `_update_entity`
    drops per label on a per-label copy for exactly this reason. The four pops used to be
    four INDEPENDENT `if`s, i.e. OR across the labels: an `object` STOP with a `subject`
    KEEP stripped the vector off a node the subject read still needs, which is the
    "vector in neither store" state the gate's own conservatism forbids.

    Mutation (arm 1): turn the guard into `any(...)`/restore the independent pops → the
    vector is gone and this reds. Mutation (arm 3): report every label on the node instead
    of the ones that answered stop → the label list reds.
    """
    dump = _dump({"dump_id": 1, "labels": ["Object", "Subject"],
                  "props": {"id": "obj-13826-co", "name": "co-13826",
                            "embedding": list(VEC)}})

    # ── arm 1: object STOP, subject KEEP → the vector SURVIVES ──────────────
    _gate_map(monkeypatch, keep={"subject"})
    result = restore_graph(proj.g, dump)
    assert _prop(proj.g, "(n:Object {id:'obj-13826-co'})", "embedding") is not None, (
        "the restore dropped a co-labelled node's vector because ONE of its labels said "
        "stop — the other label's read now has no graph vector and the record may not "
        "hold one (the #11395 outcome)")
    assert "retired_vectors" not in result, (
        f"a half-stopped node was reported as retired: {result}")

    # ── arm 2: BOTH stop → dropped, and only the STOPPING label is reported ──
    proj.g.query("MATCH (n) DETACH DELETE n")
    _gate_map(monkeypatch, keep={""})
    result = restore_graph(proj.g, dump)
    assert _prop(proj.g, "(n:Object {id:'obj-13826-co'})", "embedding") is None, (
        "both gated labels said stop and the restore kept the vector")
    assert result["retired_vectors"] == 1, result
    assert _prop(proj.g, "(n:Object {id:'obj-13826-co'})", "name") == "co-13826", (
        "the ROW was not restored — a retirement drops the vector, never the node")
    assert result["retired_vector_labels"] == ["Object", "Subject"], (
        f"the reported labels must be the gated ones the node carries: {result}")

    # ── arm 3: a NON-gated label is never retired, and never reported ────────
    # ⛔ `:Point` USED TO STAND IN FOR THIS ARM and no longer can (#13971): it IS gated
    # now, so a `:Point:Object` node's vector is decided by BOTH counters (arm 4). What
    # this arm still needs is a label NO gate answers for — `:Session` is one — because a
    # restored node carrying it may carry a vector that is not a mirror at all, and
    # retiring on the gated labels' answer would drop a copy the record need not hold.
    proj.g.query("MATCH (n) DETACH DELETE n")
    mixed = _dump({"dump_id": 1, "labels": ["Object", "Session"],
                   "props": {"id": "obj-13826-mixed", "name": "mixed-13826",
                             "embedding": list(VEC)}})
    result = restore_graph(proj.g, mixed)
    assert _prop(proj.g, "(n:Object {id:'obj-13826-mixed'})", "embedding") is not None, (
        "the restore retired the vector of a node carrying a label no gate covers")
    assert "retired_vectors" not in result, (
        "a node carrying a label no gate covers was reported as a retirement: "
        f"{result}")

    # ── arm 4: `:Point` IS gated now, on BOTH its types (#13971) ────────────
    # The shape is real (`tests/test_capture_session.py` mints `:Point:Object`), so the
    # question `#13854` raised is not moot — it CHANGED: a Point's vector may be retired
    # only when the `point`/`operator` counters stop as well as the Object one, and a
    # half-verified `:Point` keeps it. The record is where a stripped vector still lives.
    proj.g.query("MATCH (n) DETACH DELETE n")
    both = _dump({"dump_id": 1, "labels": ["Object", "Point"],
                  "props": {"id": "pt-13826-co", "content": "x",
                            "embedding": list(VEC)}})
    # ⛔ THE HOLD IS LIFTED *HERE ONLY*, so the two-counter conjunction stays GRADED
    # (`#14560` review F5). `:Point`'s restore drop is HELD (`hosted_backup._RESTORE_DROP_HELD`,
    # `#13971`'s per-ID ask is outstanding), so with the real set every arm below would keep
    # the vector for the WRONG reason and the two `keep=` arms would stop discriminating at
    # all. The hold itself is asserted at the end of this arm, on the real set.
    import tortoise.hosted_backup as _hb
    monkeypatch.setattr(_hb, "_RESTORE_DROP_HELD", frozenset())
    _gate_map(monkeypatch, keep={"point"})
    result = restore_graph(proj.g, both)
    assert _prop(proj.g, "(n:Point {id:'pt-13826-co'})", "embedding") is not None, (
        "the restore retired a `:Point` vector while `operator` was unverified")
    assert "retired_vectors" not in result, result
    # ⛔ THE DISCRIMINATING ARM (#13971 review, P3 on the first cut): the same question from
    # the OTHER half, and it is what makes the pair an arm pair rather than one arm. With
    # only `keep={"point"}`, a mutant that asks `point` ALONE and dropped the `operator`
    # conjunct still passes (`point` True -> keep); `keep={"operator"}` is satisfied by no
    # such mutant, because `point` answers stop.
    proj.g.query("MATCH (n) DETACH DELETE n")
    _gate_map(monkeypatch, keep={"operator"})
    result = restore_graph(proj.g, both)
    assert _prop(proj.g, "(n:Point {id:'pt-13826-co'})", "embedding") is not None, (
        "the restore retired a `:Point` vector while `point` answered STOP but `operator` "
        "said keep — one label, two populations (`#13854`'s rule)")
    assert "retired_vectors" not in result, result
    proj.g.query("MATCH (n) DETACH DELETE n")
    _gate_map(monkeypatch, keep={""})
    result = restore_graph(proj.g, both)
    assert _prop(proj.g, "(n:Point {id:'pt-13826-co'})", "embedding") is None, (
        "both `:Point` halves said stop and the restore kept the graph vector")
    assert result["retired_vectors"] == 1, result
    assert result["retired_vector_labels"] == ["Object", "Point"], result

    # ⛔ THE HOLD ON THE REAL SET is its OWN test (`test_the_Point_restore_drop_is_HELD`):
    # grading it here needs `monkeypatch.undo()` and a fourth graph wipe, and a test that
    # reaches for a raw `proj.g.query` to set up its state is an untyped receiver the
    # `sdk_privatise` RATCHET charges — a required leg (`surface-guard`) reds and there is
    # no pin to re-cut. A fresh graph per test is the cheaper setup AND the cleaner arm.


def test_the_Point_restore_drop_is_HELD(proj, monkeypatch):
    """`:Point`'s restored vector is KEPT even when BOTH counters answer stop (`#14560` F5).

    `#13971`'s per-ID ask is outstanding, so `_RESTORE_DROP_HELD` holds this label: an
    aggregate `record_vectors >= graph_vectors` is not evidence about the individual row a
    restore would delete, and `#5004` (a dropped restored vector cannot be re-encoded) makes
    a wrong drop unrecoverable. This arm REDS the moment the hold is lifted — it is the
    counterweight to the conjunction arms above, which lift the hold only to grade
    themselves, and it runs on the REAL set with nothing lifted.
    """
    dump = _dump({"dump_id": 1, "labels": ["Object", "Point"],
                  "props": {"id": "pt-13826-held", "content": "x",
                            "embedding": list(VEC)}})
    _gate_map(monkeypatch, keep={""})
    result = restore_graph(proj.g, dump)
    assert _prop(proj.g, "(n:Point {id:'pt-13826-held'})", "embedding") is not None, (
        "the `:Point` restore drop is HELD until `#13971`'s per-ID ask lands — an aggregate "
        "`record_vectors >= graph_vectors` is not evidence about the row being deleted, and "
        "`#5004` forbids re-encoding what is dropped")
    assert "retired_vectors" not in result, result


def test_the_completeness_clause_asks_the_gate(sdk, monkeypatch, tmp_path):
    """A gated deployment's session unit is COMPLETE — never a permanent repair candidate.

    `embedding_incomplete` is the clause that routes a session unit onto the REPAIR path on
    every sweep (`sdk.py`, `#13626`). It is load-bearing in the other direction too: while
    the record serves `event`, a missing graph vector is the TARGET design, so leaving the
    clause ungated makes every session file re-enter the repair path for ever — the
    "skipped/embedding-unavailable, retryable=True for ever" shape the repair's own gate
    exists to prevent.

    ⛔ THE DISCRIMINATOR IS THE REPAIR PATH ITSELF, not the reported status (#13854 review,
    F6): with the repair correctly a write-stop, the ungated clause ends in the same
    `skipped/unchanged` verdict, so a status-only assertion is satisfied by the mutant. The
    spy counts ENTRIES into the repair path — the gated unit must never take one.
    """
    _stub_session_metadata(monkeypatch)
    monkeypatch.setattr(TortoiseSDK, "_session_embedding",
                        lambda self, *a, **k: list(VEC), raising=True)
    attempts: list[str] = []
    _real = TortoiseSDK._embedding_repair_attempt

    def _counting(self, event_id, *a, **k):
        attempts.append(event_id)
        return _real(self, event_id, *a, **k)

    monkeypatch.setattr(TortoiseSDK, "_embedding_repair_attempt", _counting,
                        raising=True)

    corpus = tmp_path / "corpus-13826-complete"
    corpus.mkdir()
    (corpus / "sess.md").write_text(
        "---\nsessionId: sess-13826-complete\ntitle: A Session\n---\nuser: hi\n",
        encoding="utf-8")
    g = sdk._get_proj().g

    _gate(monkeypatch, True)
    r1 = sdk.index_sources_from_directory(str(corpus), extract_metadata=True,
                                          llm_model=None)
    assert r1["indexed"] == 1, r1
    assert g.query("MATCH (e:Event) WHERE e.embedding IS NOT NULL RETURN count(e)"
                   ).result_set[0][0] == 1

    # the strip this PR makes possible: the row keeps its Event, the graph keeps no vector
    g.query("MATCH (e:Event) SET e.embedding = NULL")
    attempts.clear()
    _gate(monkeypatch, False)
    r2 = sdk.index_sources_from_directory(str(corpus), extract_metadata=True,
                                         llm_model=None)
    assert attempts == [], (
        "a gated deployment entered the EMBEDDING REPAIR path for a session unit whose "
        "vector the record serves — the completeness clause still treats a retired "
        f"mirror as an outage, so every sweep retries it for ever: {attempts}")
    assert r2["skipped"] == 1 and r2["updated"] == 0, r2
    assert not [e for e in r2.get("errors", []) if e.get("retryable")], (
        f"the gated unit was reported as a retryable failure: {r2}")
    assert g.query("MATCH (e:Event) WHERE e.embeddingRepairFailedAt IS NOT NULL "
                   "RETURN count(e)").result_set[0][0] == 0, (
        "a repair marker was written for a unit that must never be repaired")


# ── 9. the FAIL-SAFE arm of the predicate itself ────────────────────────


def test_an_unreadable_predicate_keeps_the_graph_copy(proj, monkeypatch):
    """`_derived_mirror_required`'s `except` arm answers KEEP — pinned, because it is the
    one answer a caller CANNOT verify and the arm that must never be reachable as proof.

    ⛔ DIRECTION MATTERS MORE THAN COVERAGE. `True` means "the record has not proved it
    holds this label's vectors", so the writer stores and the graph copy survives — the
    safe direction when `record_vector` cannot be read at all. `False` would mean "the
    record HAS proved it", i.e. a strip, decided by an `ImportError`. Measured: inverting
    the arm (`except: return False`) leaves this whole file and
    `tests/test_13610_graph_vector_copy.py` / `tests/test_13629_entity_vector_retire.py`
    green (173 passed), which is why the arm needs an assertion of its own.
    """

    def _unreadable(entity_type: str) -> bool:
        raise RuntimeError("the record's arming predicate could not be read")

    monkeypatch.setattr(record_vector, "mirror_required", _unreadable, raising=False)

    proj._upsert_source({"url": "https://example.com/unreadable-gate",
                         "title": "Unreadable", "embedding": list(VEC)})
    assert _prop(proj.g, "(n:Source {url:'https://example.com/unreadable-gate'})",
                 "embedding") is not None, (
        "an unreadable gate dropped the graph copy — the fail-safe arm must answer KEEP "
        "(an `ImportError` is not evidence that the record holds the vector)")
