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

import json
import os
import shutil
import sys
import tempfile
import types

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
    reaches every writer — folds, sdk doors and the restore alike."""
    monkeypatch.setattr(record_vector, "mirror_required",
                        lambda entity_type: required, raising=False)


def _gate_map(monkeypatch, keep: set[str]) -> None:
    """Force the predicate PER LABEL: `keep` answers "required", everything else "not".

    ⛔ This is what makes the per-label claim FALSIFIABLE (#13828 review, P2): an
    `all-True`/`all-False` patch is satisfied by one boolean hoisted to the top of the
    method, by any permutation of the asks, and by a pop taken on the dict the five
    labels SHARE — measured, all three were green under `_gate` alone.
    """
    monkeypatch.setattr(record_vector, "mirror_required",
                        lambda entity_type: entity_type in keep, raising=False)


def _prop(g, match: str, prop: str):
    rows = g.query(f"MATCH {match} RETURN n.{prop}").result_set
    if not rows:
        return ("ABSENT-NODE",)
    return rows[0][0]


def _node_exists(g, match: str) -> bool:
    rows = g.query(f"MATCH {match} RETURN count(n)").result_set
    return bool(rows) and bool(rows[0][0])


# ── 1. the `:Event` folds ───────────────────────────────────────────────


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
    """`_upsert_document` asks about `document` — the half of `:Source` it writes."""
    calls = _never_encode(monkeypatch)

    _gate(monkeypatch, True)
    proj._upsert_document({"id": "https://example.com/doc-keep", "title": "My doc",
                           "summary": "a summary", "topics": ["x"]})
    assert _prop(proj.g, "(n:Source {url:'https://example.com/doc-keep'})",
                 "embedding") is not None
    assert calls, "the mirror arm did not encode"

    _gate(monkeypatch, False)
    calls.clear()
    proj._upsert_document({"id": "https://example.com/doc-drop", "title": "Other doc",
                           "summary": "another summary", "topics": ["y"]})
    assert _prop(proj.g, "(n:Source {url:'https://example.com/doc-drop'})",
                 "embedding") is None, "the document fold stored a retired vector"
    assert calls == [], f"the stop arm still encoded: {calls}"
    assert _prop(proj.g, "(n:Source {url:'https://example.com/doc-drop'})",
                 "title") == "Other doc", "the ROW was not written on the stop arm"


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


# ── 4. `sdk::_update_entity` — the caller-prop door, per label ──────────


def test_update_entity_asks_the_gate_per_label(sdk, monkeypatch):
    """The door reaches FIVE labels, so `embedding` is gated against the label it is on.

    ⛔ The `:Point` arm is NOT gated (a Point's vector is record TRUTH, `#5004`), which is
    the reason this is a per-label ask rather than one answer for the whole method.
    ⛔ EVERY ARM BELOW IS PER-LABEL (#13828 review, P2): one answer for all five is
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

    # …and a `:Point` is untouched in either direction (record truth, never derived).
    g.query("CREATE (p:Point {id: 'pt-13826', content: 'a claim'})")
    _gate(monkeypatch, False)
    sdk.update_knowledge("pt-13826", embedding=list(VEC))
    assert _prop(g, "(n:Point {id:'pt-13826'})", "embedding") is not None, (
        "the gate was applied to a `:Point`, whose vector is record truth (#5004)")


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
    and a gated deployment would re-walk every such file on every sweep.
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
    assert attempt[0] == "healed", (
        "the retired mirror was reported as a FAILED repair, which the sweep retries for "
        f"ever: {attempt}")
    assert _prop(g, "(n:Event {eventId:'ev-13826-repair-drop'})", "embedding") is None
    assert _prop(g, "(n:Event {eventId:'ev-13826-repair-drop'})",
                 "embeddingRepairFailedAt") is None, (
        "a retired mirror recorded a repair-failure marker")
    assert _node_exists(g, "(n:Event {eventId:'ev-13826-repair-drop'})")


# ── 8. the frozen legacy corpus ingest ─────────────────────────────────


def test_ingest_corpus_asks_the_gate(sdk, monkeypatch, tmp_path):
    """The frozen legacy branch is a declared `:Event` writer, so it asks too."""
    _stub_session_metadata(monkeypatch)
    corpus = tmp_path / "corpus-13826"
    corpus.mkdir()
    (corpus / "sess.md").write_text(
        "---\ntitle: A Session\nsessionId: sess-13826\n---\nuser: hi\n", encoding="utf-8")
    g = sdk._get_proj().g

    _gate(monkeypatch, True)
    # ⛔ `eventKind` DISPATCHES this frozen branch — it does not classify the frontmatter
    # (that is the new `index_sources_from_directory` path), so the session arm is only
    # reached by naming it.
    out = sdk.ingest_corpus(str(corpus), eventKind="AgentSession", extract_metadata=True)
    assert out["ingested"] == 1, out
    rows = g.query("MATCH (e:Event {session_id:'sess-13826'}) RETURN e.embedding"
                   ).result_set
    assert rows and rows[0][0] is not None, (
        f"the mirror arm of the legacy corpus ingest stored no vector: {rows}")

    g.query("MATCH (e:Event) DETACH DELETE e")
    _gate(monkeypatch, False)
    out = sdk.ingest_corpus(str(corpus), eventKind="AgentSession", extract_metadata=True)
    assert out["ingested"] == 1, out
    rows = g.query("MATCH (e:Event {session_id:'sess-13826'}) "
                   "RETURN e.embedding, e.eventStatus").result_set
    assert rows, "the ROW was not written on the stop arm"
    assert rows[0][0] is None, "the legacy corpus ingest stored a retired vector"
    assert rows[0][1] == "indexed", rows

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
    out = sdk.ingest_corpus(str(corpus), eventKind="AgentSession", extract_metadata=True)
    assert out["updated"] == 1, out
    rows = g.query("MATCH (e:Event {session_id:'sess-13826'}) RETURN e.embedding"
                   ).result_set
    assert rows, "the row vanished on the update arm's stop arm"
    assert rows[0][0] is None, (
        "the legacy corpus ingest's UPDATE arm re-minted a retired vector — it is a "
        "separate writer from the create arm and was ungated here")

    (corpus / "sess.md").write_text(
        "---\ntitle: A Session\nsessionId: sess-13826\n---\nuser: hi a third time\n",
        encoding="utf-8")
    _gate(monkeypatch, True)
    out = sdk.ingest_corpus(str(corpus), eventKind="AgentSession", extract_metadata=True)
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
    monkeypatch.setattr(sdk_module, "TortoiseSDK", lambda *a, **k: sdk, raising=True)
    g = sdk._get_proj().g
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
        session_indexer.main()

    rows = g.query("MATCH (e:Event {eventKind:'AgentSession'}) "
                   "RETURN e.embedding ORDER BY e.source_file").result_set
    assert len(rows) == 2, rows
    assert rows[0][0] is not None, "the mirror arm of the CLI stored no vector"
    assert rows[1][0] is None, "the CLI stored a retired vector"
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

    proj.g.query("MATCH (n) DETACH DELETE n")
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
