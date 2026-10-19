"""#9122 §17.1 — the two raw `:Event` writers keep a JOURNAL RECORD.

`TortoiseSDK.ingest_corpus` and the `python -m tortoise.session_indexer` CLI put
`:Event` nodes into the derived cache with **raw Cypher on the projection's write
handle** (`proj.g.query`), and neither one appends anything to the journal. That
is exactly the class §17.1 forbids — *"a derived cache that is also written to
directly is not derived"* — and it is invisible to the two doors `#7951` closed,
because those guard the **fold** (`FalkorProjection.apply`) and the **public
raw-query lane** (`FalkorProjection.query`), not the write handle.

It is also why `#9079`'s third door (`#9124`, the handle guard) cannot land
first: measured on that lane, arming the guard refused these two writers and the
§17.1 suites went to 7 failed / 25 passed. A guard that refuses the product's
own writes is a regression, not a fix — so the writers must declare a record
**before** the guard closes.

⛔ WHAT THIS SUITE PINS. Journal-first for both writers, so a rebuild from the
journal alone reproduces the `:Event` nodes — including `file_hash`, which is
what makes byte-identical re-ingest a `skipped` rather than a duplicate *after* a
rebuild. And the write itself runs inside a live-write scope, so the guard
`#9124` installs on the handle permits the sanctioned route instead of refusing
it.

⛔ THE RECORD TYPE IS `EventRecorded`, NOT `DocumentCreated` — deliberately, and
this is the trap `#9122` warned about. `DocumentCreated` folds through
`_upsert_document`, which MERGEs a **`:Source` keyed `url`** (D10, ONTOLOGY
§4.4); replaying one would materialise a Source and leave the `:Event` gone. A
`rebuild_all` over `DocumentCreated` records therefore passes a naive
"something was recreated" check while losing the node. `EventRecorded` folds
through `_upsert_event`, which MERGEs `:Event {eventId}` — the identity these
writers actually use.

⛔ WHAT THIS SUITE DOES *NOT* CLAIM:
* it does not claim every raw `:Event` writer is covered. These remain, and are
  recorded on `#9122` / `#9079`'s census rather than silently absorbed here:
  `_session_event_write` (the `#900` index path) journals but opens no
  live-write scope; `_create_entity`'s recorded apply-first deviation stands;
  `_record_embedding_marker` and `mining`'s `mined_hash` write props on the
  handle; and **`hosted_api._execute_commit_writes` writes `:Event {eventId}`
  nodes with raw Cypher, journals NOTHING and opens no scope** — the same defect
  class as this issue, on the hosted commit path — **and
  `TortoiseSDK._connect_issue_objects` (reached from BOTH session arms here and
  from `_session_event_write`) MERGEs `:Object` nodes and `aboutObject` edges on
  the raw handle with no record and no scope.** Measured with the guard probe
  armed and a session body containing `foo#123`: the `:Event` lands, then the
  `:Object` write is REFUSED and the sweep aborts. So `#9124` must census that
  writer before it arms; this suite's guard assertion is deliberately scoped to
  `:Event` statements and says so.
* it does not arm the standalone `session_indexer` CLI's journal.
  `main()` builds `TortoiseSDK()` with no `event_log_path`, and the product's
  only derivation (`config._resolve_event_log_path`) deliberately returns
  `None` for the unnamed DEFAULT graph — so run as documented that CLI is
  journal-less and this door is a no-op there. What this suite proves is the
  MECHANISM (a journal-backed SDK records and replays faithfully) and the
  configured/unconfigured switch, not that the CLI's default graph grew a
  journal. Disclosed on `#9122`.
* it does not claim replay is faithful for a vector the live node never had.
  When a record carries no `embedding`, the fold FALLBACK-ENCODES
  `subject + eventKind + object` and writes the result — so the two
  `DocumentCreated` arms (whose live statement has no vector clause) gain a
  vector on replay, and one embedder call per replayed Event on a lane with
  embeddings. That is `_upsert_event`'s own fallback, pre-existing, and it is
  disclosed here rather than changed: the harmful direction (overwriting a GOOD
  stored vector with that fallback) IS closed by the door, which carries the
  stored vector forward on both update arms.
* it does not claim a replayed record reproduces EVERY live property. The fold's
  extra-props walk drops its `_META_KEYS`, so a frontmatter `version` on an
  ingested document Event is live and journaled but not replayed. That is the
  fold's pre-existing skip set, not something this lane changed, and it is
  disclosed on `#9122` rather than fixed here.
* it does not claim the `github_indexer` SDK-less arm journals. That arm is a
  TEST SEAM whose fail-closed refusal is deliberate and pinned by
  `tests/test_7951_write_doors.py::test_indexer_sdk_less_fallback_fails_closed_
  on_a_journal_backed_projection`; this suite runs that pin unmodified (see the
  acceptance command in `#9122`) rather than adjusting it to fit.
* it does not arm `#9124`'s guard (that child is not on `main`). `_GuardProbe.install`
  reproduces the guard's *predicate* verbatim — journal-backed + no declaration
  in flight + a mutating statement ⇒ refused — so "these writers will not be
  refused" is executable now instead of blocked on a sibling, and the two
  cannot disagree about what a declaration is.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from tortoise.projection import (
    _CACHE_WRITE_AUTHORIZED,
    DirectCacheWriteRefused,
    _GuardedGraph,
    _statement_writes,
)

# ── fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def sdk_with_journal(tmp_path):
    """A real SDK with a configured JSONL journal.

    The same shape `tests/test_7951_enforce_atomic_write.py` uses: the explicit
    `db_path` makes the projection embedded when no URI is configured, and the
    SDK's own construction is redirected to the docker server in a URI-set
    session. `event_log_path` is what makes a journal record possible at all.
    """
    from tortoise.sdk import TortoiseSDK

    journal = tmp_path / "journal.jsonl"
    sdk = TortoiseSDK(str(tmp_path / "s9122.db"), event_log_path=str(journal))
    sdk._journal_path_for_test = journal  # convenience for the assertions
    try:
        yield sdk
    finally:
        sdk.close()


def _journal_records(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out: list[dict] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if raw:
            out.append(json.loads(raw))
    return out


def _event_records(path: Path) -> list[dict]:
    return [r for r in _journal_records(path) if r.get("type") == "EventRecorded"]


def _corpus(root: Path, files: dict[str, str]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for name, body in files.items():
        (root / name).write_text(body, encoding="utf-8")
    return root


def _doc(title: str, extra: str = "") -> str:
    return f"---\ntitle: {title}\n{extra}---\n# {title}\n\nbody of {title}\n"


def _event_ids(proj) -> set[str]:
    rows = proj.g.query(
        "MATCH (e:Event) WHERE e.eventId IS NOT NULL RETURN e.eventId"
    ).result_set
    return {r[0] for r in rows}


def _event_props(proj, event_id: str) -> dict:
    rows = proj.g.query(
        "MATCH (e:Event {eventId:$eid}) RETURN properties(e)",
        params={"eid": event_id},
    ).result_set
    return rows[0][0] if rows else {}


_DDL_PREFIXES = ("CREATE INDEX", "CREATE FULLTEXT INDEX", "DROP INDEX",
                 "CREATE CONSTRAINT", "DROP CONSTRAINT")


def _is_index_ddl(statement: str) -> bool:
    return statement.upper().startswith(_DDL_PREFIXES)


class _GuardProbe:
    """`#9124`'s predicate, reproduced on `_GuardedGraph.query`.

    Records every `(statement, scope-at-the-write)` so "this write ran inside a
    live-write scope" is OBSERVED rather than inferred from the source, and
    raises `DirectCacheWriteRefused` for exactly the statements the real guard
    will refuse. Nothing here is installed unless a test asks for it.
    """

    def __init__(self) -> None:
        self.seen: list[tuple[str, str | None, bool]] = []

    def install(self, monkeypatch) -> _GuardProbe:
        original = _GuardedGraph.query
        probe = self

        def instrumented(self, cypher, params=None, timeout=None):
            statement = " ".join((cypher or "").split())
            writes = _statement_writes(statement)
            scope = _CACHE_WRITE_AUTHORIZED.get()
            probe.seen.append((statement, scope, writes))
            if (getattr(self._proj, "_journal_backed", False)
                    and scope is None and writes):
                raise DirectCacheWriteRefused(
                    "§17.1: direct cache write through the projection write "
                    f"handle (test probe of #9124's third door): {statement[:120]}"
                )
            return original(self, cypher, params, timeout)

        monkeypatch.setattr(_GuardedGraph, "query", instrumented)
        return self

    def mutating(self) -> list[tuple[str, str | None]]:
        """Mutating statements that are NOT index/constraint DDL.

        `FalkorProjection.__init__` issues `CREATE INDEX …` on the graph it
        opens. That is schema setup, not a §17.1 cache write (it changes no
        node), and it runs before any lane exists to declare — including it
        would make every honest writer look undeclared and the assertion
        vacuous.
        """
        return [
            (s, sc) for s, sc, w in self.seen
            if w and not _is_index_ddl(s)
        ]


# ── the load-bearing pin: `ingest_corpus` journals every `:Event` write ───


def test_ingest_corpus_journals_every_document_event(sdk_with_journal, tmp_path):
    """RED before this lane: `ingest_corpus` wrote the cache with no record.

    Two documents in, two `EventRecorded` records out, each naming the id the
    cache write used — the record is what a rebuild replays.
    """
    sdk = sdk_with_journal
    docs = _corpus(tmp_path / "corpus", {
        "a.md": _doc("Alpha"),
        "b.md": _doc("Beta"),
    })

    result = sdk.ingest_corpus(str(docs))

    assert result["ingested"] == 2, result
    records = _event_records(sdk._journal_path_for_test)
    assert len(records) == 2, (
        "ingest_corpus wrote :Event nodes with no journal record — a rebuild "
        f"destroys them (§17.1): journal={[r.get('type') for r in _journal_records(sdk._journal_path_for_test)]}"
    )
    ids = {r.get("id") or r.get("eventId") for r in records}
    assert ids == {"a.md", "b.md"}, ids


def test_a_rebuild_reproduces_the_ingested_events(sdk_with_journal, tmp_path):
    """The record carries the payload, so the rebuild is not merely non-empty.

    `file_hash` is the assertion that matters: it is what makes a byte-identical
    re-ingest a `skipped` rather than a duplicate, and a rebuild that loses it
    silently degrades every later run into an `updated` (or a re-embed).
    """
    sdk = sdk_with_journal
    docs = _corpus(tmp_path / "corpus", {"a.md": _doc("Alpha")})
    assert sdk.ingest_corpus(str(docs))["ingested"] == 1
    live = _event_props(sdk._get_proj(), "a.md")
    assert live, "sanity: the Event exists live"
    assert live.get("file_hash"), "sanity: the live write carries file_hash"

    sdk._get_proj().rebuild_all(
        str(sdk._journal_path_for_test.parent), confirm_destructive=True)

    replayed = _event_props(sdk._get_proj(), "a.md")
    assert replayed, (
        "rebuild_all destroyed the ingested Event — the write reached the "
        "cache with no journal record to replay (#9122)")
    assert replayed.get("file_hash") == live.get("file_hash"), (
        "the rebuild lost file_hash, so byte-identical re-ingest stops being a "
        f"skip: live={live.get('file_hash')!r} "
        f"replayed={replayed.get('file_hash')!r}")
    assert replayed.get("eventKind") == "DocumentCreated", replayed.get("eventKind")
    assert replayed.get("title") == "Alpha", replayed.get("title")


def test_a_rebuilt_corpus_still_skips_a_byte_identical_reingest(
        sdk_with_journal, tmp_path):
    """The end the file_hash exists for: `skipped`, not `updated`, after a rebuild."""
    sdk = sdk_with_journal
    docs = _corpus(tmp_path / "corpus", {"a.md": _doc("Alpha")})
    assert sdk.ingest_corpus(str(docs))["ingested"] == 1
    sdk._get_proj().rebuild_all(
        str(sdk._journal_path_for_test.parent), confirm_destructive=True)

    second = sdk.ingest_corpus(str(docs))

    assert second["skipped"] == 1, (
        "byte-identical re-ingest after a rebuild no longer skips — the "
        f"replayed Event lost its file_hash: {second}")


def test_reingest_byte_identical_stays_a_skip_and_appends_nothing(
        sdk_with_journal, tmp_path):
    """A `skipped` file writes NOTHING, so it must append nothing.

    Emit-on-every-WRITE, not on-every-run: a no-op that journals would grow the
    log once per run on every unchanged corpus, which §9.6's cost bound forbids.
    """
    sdk = sdk_with_journal
    docs = _corpus(tmp_path / "corpus", {"a.md": _doc("Alpha")})
    assert sdk.ingest_corpus(str(docs))["ingested"] == 1
    before = len(_event_records(sdk._journal_path_for_test))

    second = sdk.ingest_corpus(str(docs))

    assert second["skipped"] == 1, second
    assert len(_event_records(sdk._journal_path_for_test)) == before, (
        "a skipped re-ingest appended a record — the journal now grows once "
        "per run on an unchanged corpus")


def test_a_changed_document_journals_the_update(sdk_with_journal, tmp_path):
    """The update arm is a write too, so it carries its own record."""
    sdk = sdk_with_journal
    docs = _corpus(tmp_path / "corpus", {"a.md": _doc("Alpha")})
    assert sdk.ingest_corpus(str(docs))["ingested"] == 1

    (docs / "a.md").write_text(_doc("Alpha revised"), encoding="utf-8")
    second = sdk.ingest_corpus(str(docs))

    assert second["updated"] == 1, second
    assert len(_event_records(sdk._journal_path_for_test)) == 2, (
        "the update reached the cache with no record of its own")


def test_ingest_corpus_agent_session_branch_journals(sdk_with_journal, tmp_path):
    """The `AgentSession` arm is the OTHER half of `ingest_corpus`.

    It has its own two write statements, so a fix applied only to the
    `DocumentCreated` branch would leave half this issue open.
    """
    sdk = sdk_with_journal
    docs = _corpus(tmp_path / "corpus", {
        "s1.md": "---\ntitle: Session One\nsessionId: s9122\n---\nuser: hi\nassistant: hello\n",
    })

    result = sdk.ingest_corpus(str(docs), eventKind="AgentSession")

    assert result["ingested"] == 1, result
    records = _event_records(sdk._journal_path_for_test)
    assert len(records) == 1, (
        "the AgentSession arm of ingest_corpus wrote the cache with no record: "
        f"{[r.get('type') for r in _journal_records(sdk._journal_path_for_test)]}")
    assert records[0].get("eventKind") == "AgentSession", records[0].get("eventKind")

    sdk._get_proj().rebuild_all(
        str(sdk._journal_path_for_test.parent), confirm_destructive=True)
    assert _event_ids(sdk._get_proj()) == {
        records[0].get("id") or records[0].get("eventId")}, (
        "the AgentSession Event did not survive the rebuild")


# ── the record type matters: EventRecorded, never DocumentCreated ─────────


def test_the_ingest_record_folds_back_to_an_event_not_a_source(
        sdk_with_journal, tmp_path):
    """RED if the record is re-typed `DocumentCreated`.

    `DocumentCreated` folds through `_upsert_document` and MERGEs a `:Source`
    keyed `url` — a rebuild over such records replays a Source and deletes the
    Event. Asserting the NODE LABEL, not "something exists", is what catches it.
    """
    sdk = sdk_with_journal
    docs = _corpus(tmp_path / "corpus", {"a.md": _doc("Alpha")})
    assert sdk.ingest_corpus(str(docs))["ingested"] == 1

    sdk._get_proj().rebuild_all(
        str(sdk._journal_path_for_test.parent), confirm_destructive=True)

    assert _event_props(sdk._get_proj(), "a.md"), (
        "the rebuilt node is not an :Event — the ingest record was typed "
        "DocumentCreated, which folds to a :Source {url} instead (#9122's "
        "document-event trap)")
    sources = sdk._get_proj().g.query(
        "MATCH (s:Source {url:$u}) RETURN count(s)",
        params={"u": "a.md"},
    ).result_set[0][0]
    assert sources == 0, "the ingest record materialised a :Source, not an :Event"


# ── guard-armed: the sanctioned route is not refused ─────────────────────


def test_ingest_corpus_writes_inside_a_live_write_scope(
        sdk_with_journal, tmp_path, monkeypatch):
    """`#9124`'s third door will refuse an undeclared handle write.

    `_GuardProbe.install` puts the same predicate on `_GuardedGraph.query`. FAILS IF
    a `:Event`-writing statement reaches the handle with no scope in flight —
    which is exactly the 7-failed state `#9124` measured when its guard was
    armed over this writer.

    SCOPE, stated because it is load-bearing: this asserts about `:Event`
    statements only. The same session ingest ALSO reaches
    `_connect_issue_objects`, which writes `:Object` nodes and `aboutObject`
    edges unscoped — refused under this probe once the body references an issue
    — and that writer is `#9124`'s census item, disclosed in the module
    docstring, not silently blessed here.
    """
    probe = _GuardProbe().install(monkeypatch)
    sdk = sdk_with_journal
    assert sdk._get_proj()._journal_backed, (
        "sanity: the probe only refuses on a journal-backed projection")
    docs = _corpus(tmp_path / "corpus", {"a.md": _doc("Alpha")})

    result = sdk.ingest_corpus(str(docs))

    assert result["ingested"] == 1, (
        f"the guard refused the sanctioned ingest write: {probe.mutating()}")
    unguarded = [
        (s, sc) for s, sc in probe.mutating()
        if sc is None and "Event" in s
    ]
    assert not unguarded, (
        "an :Event write reached the handle with no live-write scope, so "
        f"#9124's guard will refuse it: {unguarded}")


# ── the second writer: `python -m tortoise.session_indexer` ──────────────


@pytest.fixture
def indexer_sdk(sdk_with_journal, monkeypatch):
    """Drive the real CLI, but on a journal-backed SDK.

    `main()` builds `TortoiseSDK()` inside the branch (a function-level import),
    so patching the class is the seam: everything else — the walk, the
    exists/skip logic, the props, the printed status — is the shipped code.
    """
    import tortoise.sdk as _sdk

    def _factory(*_a, **_kw):
        return sdk_with_journal

    monkeypatch.setattr(_sdk, "TortoiseSDK", _factory)
    monkeypatch.setenv("NO_NETWORK", "1")
    monkeypatch.setenv("TORTOISE_NO_NETWORK", "1")
    # Build the projection BEFORE `main()` writes `TORTOISE_DB_URI` into the
    # process: `main()` sets it from `--db`, and in a test session the
    # embedded→server redirect (`FalkorProjection.__init__`) reads that var at
    # CONSTRUCTION time. Pinning the projection here keeps the SDK on the lane
    # the fixture opened, which is the only thing this suite is about.
    sdk_with_journal._get_proj()
    return sdk_with_journal


def _run_indexer(monkeypatch, capsys, path: Path) -> dict:
    from tortoise import session_indexer

    # `main()` assigns `os.environ['TORTOISE_DB_URI']` DIRECTLY, so monkeypatch
    # never records a value to restore and every test collected after this
    # module would inherit a dead endpoint (the test-mode redirect sends a
    # path-built projection to whatever this var names). Recording the CURRENT
    # value first is what makes the teardown restore it.
    monkeypatch.setenv("TORTOISE_DB_URI",
                       __import__("os").environ.get("TORTOISE_DB_URI", ""))
    monkeypatch.setattr(sys, "argv", [
        "session_indexer", str(path), "--db", "docker://:x@localhost:1/g",
        "--no-llm",
    ])
    session_indexer.main()
    out = capsys.readouterr().out.strip().splitlines()
    return json.loads(out[-1]) if out else {}


def test_session_indexer_main_journals_the_session_event(
        indexer_sdk, tmp_path, monkeypatch, capsys):
    """RED before this lane: the CLI wrote `:Event` with no journal record."""
    session = tmp_path / "sess.md"
    session.write_text(
        "---\ntitle: A Session\n---\nuser: hi\nassistant: hello\n", encoding="utf-8")

    status = _run_indexer(monkeypatch, capsys, session)

    assert status.get("status") == "ingested", status
    event_id = status.get("eventId")
    assert event_id, status
    records = _event_records(indexer_sdk._journal_path_for_test)
    assert records, (
        "`python -m tortoise.session_indexer` wrote an :Event with no journal "
        "record — a rebuild destroys it (§17.1)")
    assert {r.get("id") or r.get("eventId") for r in records} == {event_id}, records


def test_the_indexer_event_survives_a_rebuild(
        indexer_sdk, tmp_path, monkeypatch, capsys):
    session = tmp_path / "sess.md"
    session.write_text(
        "---\ntitle: A Session\n---\nuser: hi\nassistant: hello\n", encoding="utf-8")
    status = _run_indexer(monkeypatch, capsys, session)
    event_id = status["eventId"]

    indexer_sdk._get_proj().rebuild_all(
        str(indexer_sdk._journal_path_for_test.parent), confirm_destructive=True)

    props = _event_props(indexer_sdk._get_proj(), event_id)
    assert props, "the indexer's Event did not survive the rebuild (#9122)"
    assert props.get("eventKind") == "AgentSession", props.get("eventKind")
    assert props.get("file_hash"), (
        "the replayed session Event lost file_hash, so the CLI's unchanged-file "
        "skip stops working after a rebuild")


def test_the_indexer_writes_inside_a_live_write_scope(
        indexer_sdk, tmp_path, monkeypatch, capsys):
    probe = _GuardProbe().install(monkeypatch)
    session = tmp_path / "sess.md"
    session.write_text(
        "---\ntitle: A Session\n---\nuser: hi\nassistant: hello\n", encoding="utf-8")

    status = _run_indexer(monkeypatch, capsys, session)

    assert status.get("status") == "ingested", (
        f"the guard refused the sanctioned indexer write: {probe.mutating()}")
    unguarded = [(s, sc) for s, sc in probe.mutating() if sc is None]
    assert not unguarded, (
        "the indexer wrote the handle with no live-write scope, so #9124's "
        f"guard will refuse it: {unguarded}")


# ── the UPDATE record must be a full node view, not a patch ──────────────


def test_the_session_update_record_replays_a_faithful_node(
        sdk_with_journal, tmp_path):
    """The update arm is a partial `SET`, but its RECORD must not be.

    The replay fold writes its OWN fixed props with defaults for anything the
    record omits (`_upsert_event`), so a patch-only record REPLACES a good
    `startedAt` with `''` and a `markdown` `format` with `jsonl`. Measured
    before the fix: `startedAt` `'…T13:10:09…'` → `''` after a rebuild, which
    silently drops the key `search_sessions` orders by. RED if the record goes
    back to carrying `update_props` alone.
    """
    sdk = sdk_with_journal
    docs = _corpus(tmp_path / "corpus", {
        "s1.md": "---\ntitle: Session One\nsessionId: s9122u\n---\nuser: hi\nassistant: hello\n",
    })
    assert sdk.ingest_corpus(str(docs), eventKind="AgentSession")["ingested"] == 1
    event_id = "session_s9122u"
    live = _event_props(sdk._get_proj(), event_id)
    assert live.get("startedAt"), "sanity: the create arm stamps startedAt"
    assert live.get("format") == "markdown", live.get("format")

    # Change the content so the UPDATE arm runs (file_hash differs).
    (docs / "s1.md").write_text(
        "---\ntitle: Session One\nsessionId: s9122u\n---\n"
        "user: hi\nassistant: hello again\n", encoding="utf-8")
    assert sdk.ingest_corpus(str(docs), eventKind="AgentSession")["updated"] == 1

    sdk._get_proj().rebuild_all(
        str(sdk._journal_path_for_test.parent), confirm_destructive=True)

    replayed = _event_props(sdk._get_proj(), event_id)
    assert replayed, "the session Event did not survive the rebuild"
    assert replayed.get("startedAt") == live.get("startedAt"), (
        "the update record replayed a PATCH, so the fold's default overwrote "
        f"startedAt: live={live.get('startedAt')!r} "
        f"replayed={replayed.get('startedAt')!r} — search_sessions orders by it")
    assert replayed.get("format") == "markdown", (
        f"format degraded to {replayed.get('format')!r} on replay")


# ── the append is the commit point ───────────────────────────────────────


def test_a_failed_append_does_not_write_the_cache(
        sdk_with_journal, tmp_path, monkeypatch):
    """`#4240` makes the append non-raising, so the DOOR must read its outcome.

    A configured journal whose write FAILS (ENOSPC, IO error) must not leave the
    derived store ahead of truth — the window a replay cannot undo. The file is
    reported as a retryable failure and NOT marked complete, so the next run
    retries instead of silently losing the document. RED if the door folds
    anyway (the pre-review `BEST-EFFORT` shape).
    """
    sdk = sdk_with_journal
    docs = _corpus(tmp_path / "corpus", {"a.md": _doc("Alpha")})
    real = sdk._emit_event

    def failing_append(*a, **kw):
        sdk._journal_write_failures += 1   # what `_emit_event` does on a raise
        return False

    monkeypatch.setattr(sdk, "_emit_event", failing_append)
    try:
        result = sdk.ingest_corpus(str(docs))
    finally:
        monkeypatch.setattr(sdk, "_emit_event", real)

    assert result["ingested"] == 0, (
        f"the cache write ran after a FAILED append: {result}")
    assert result["failed"] == 1, result
    assert result["errors"] and result["errors"][0]["retryable"] is True, result["errors"]
    assert _event_props(sdk._get_proj(), "a.md") == {}, (
        "the derived store is now AHEAD of the journal — a replay cannot undo it")


# ── the record is a WRITE door: gated before the append, never re-typed ──


def test_the_record_is_gated_for_kind_spellings_before_the_append(
        sdk_with_journal, tmp_path, monkeypatch):
    """`#5475`: journalling frontmatter-controlled kinds needs the gate.

    `ingest_corpus` takes `document_kind` straight from `frontmatter["type"]`,
    and that name IS a pack-kind spelling (`pack_state._KIND_PROP_SPELLINGS`).
    Replay is ungated by design, so journalling an unapproved namespace would
    make the JOURNAL its durable carrier — a rebuild would re-install it after
    the node was deleted or the pack un-declared. RED if the door appends
    without asking (the pre-review shape, which gated only the `eventKind`
    argument).
    """
    sdk = sdk_with_journal
    docs = _corpus(tmp_path / "corpus", {
        "m.md": "---\ntitle: M\ntype: marketing:brief\n---\ncontent\n",
    })
    seen: list[dict] = []

    from tortoise.sdk import UnapprovedKindNamespace

    def refuse(record, graph=None):
        seen.append(dict(record))
        raise UnapprovedKindNamespace(
            "#5475 refusal (test double)", prop="document_kind",
            kind="marketing:brief", namespace="marketing", installed=frozenset())

    monkeypatch.setattr(sdk, "_refuse_unapproved_kinds_in_record", refuse)

    # A refusal must fail the ONE file, not take the batch down: `ingest_corpus`
    # is a sweep, and #9068's whole point is that the write is refused rather
    # than silently widening the pack approval set.
    result = sdk.ingest_corpus(str(docs))

    assert seen, "the door appended without consulting the #5475 record gate"
    assert seen[0].get("document_kind") == "marketing:brief", (
        "the gate was handed a record without the frontmatter kind spelling, "
        f"so it could not have judged it: {sorted(seen[0])}")
    assert result["failed"] == 1, result
    assert result["errors"][0]["retryable"] is False, (
        "a kind refusal was marked retryable — the sweep would never converge, "
        f"refusing the same file on every run: {result['errors']}")
    assert _event_records(sdk._journal_path_for_test) == [], (
        "the record was appended BEFORE the gate — a refusal must leave the "
        "journal untouched")
    assert _event_props(sdk._get_proj(), "m.md") == {}, (
        "the cache was written after a kind refusal")


def test_a_frontmatter_date_still_ingests(sdk_with_journal, tmp_path):
    """A bare YAML date must not turn the journal into a hard failure.

    `EventLog.append`'s `json.dumps` cannot encode a `datetime.date`, and the
    frontmatter `created:`/`updated:` fields are passed through as parsed. The
    NODE write already degrades an unrepresentable parameter to NULL (the
    projection's transport gate), so the record must degrade the same value to
    `None` — the faithful value. RED if the record lets it raise: `_emit_event`
    returns `False`, the door skips the write, and every document with a bare
    date — 470 tracked `.md` files in this repo — fails permanently and never
    converges.
    """
    sdk = sdk_with_journal
    docs = _corpus(tmp_path / "corpus", {
        "dated.md": (
            "---\ntitle: Dated\ncreated: 2026-10-19\n"
            "updated: 2026-10-20\n---\nbody\n"
        ),
    })

    result = sdk.ingest_corpus(str(docs))

    assert result["ingested"] == 1, (
        f"a bare frontmatter date broke the ingest: {result}")
    assert result["failed"] == 0, result
    assert len(_event_records(sdk._journal_path_for_test)) == 1


def test_the_update_record_carries_the_stored_vector(
        sdk_with_journal, tmp_path, monkeypatch):
    """The FOLD does not preserve a vector the way the live clause does.

    When the freshly computed embedding is `None`, the live `SET` keeps the
    stored `vecf32`, but a record with no `embedding` sends replay into the
    fold's fallback — it encodes `subject + eventKind + object`, i.e. the single
    token "AgentSession", and writes that OVER the good stored vector. So the
    door must carry the STORED vector forward. RED without it: the replay
    writes a vector the live node never had.
    """
    sdk = sdk_with_journal
    body = "---\ntitle: Session One\nsessionId: s9122v\n---\nuser: hi\n"
    docs = _corpus(tmp_path / "corpus", {"s1.md": body + "assistant: a\n"})
    vec = [0.01] * 8
    monkeypatch.setattr(sdk, "_session_embedding", lambda *a, **kw: list(vec))
    assert sdk.ingest_corpus(str(docs), eventKind="AgentSession")["ingested"] == 1

    # The embedder is unavailable for the update, so the live clause preserves
    # the stored vector; the record must carry that same vector.
    monkeypatch.setattr(sdk, "_session_embedding", lambda *a, **kw: None)
    (docs / "s1.md").write_text(body + "assistant: b\n", encoding="utf-8")
    assert sdk.ingest_corpus(str(docs), eventKind="AgentSession")["updated"] == 1

    def _vec():
        rows = sdk._get_proj().g.query(
            "MATCH (e:Event {eventId:$e}) RETURN e.embedding",
            params={"e": "session_s9122v"}).result_set
        return rows[0][0] if rows else None

    live = _vec()
    assert live is not None, "sanity: the live clause preserved the vector"
    assert [round(float(x), 6) for x in list(live)] == [
        round(float(x), 6) for x in vec], (
        "sanity: the live clause preserved the vector written on create")

    # ⛔ Assert the UPDATE RECORD, not just a post-rebuild read. The CREATE
    # record already carries the vector and the fold's ON MATCH preserve keeps
    # it, so a rebuild alone stays GREEN with the carry-forward deleted
    # (measured). The last record is the update's; only the carry-forward can
    # put the vector on it.
    records = _event_records(sdk._journal_path_for_test)
    assert len(records) >= 2, records
    update_record = records[-1]
    assert update_record.get("embedding") is not None, (
        "the UPDATE record carries no `embedding`, so replay falls back to the "
        "fold's one-token encode and overwrites the stored vector — the "
        "carry-forward (`_journal_vector`) is not on the path")
    assert [round(float(x), 6) for x in list(update_record["embedding"])] == [
        round(float(x), 6) for x in vec], (
        "the UPDATE record carries a DIFFERENT vector than the node kept")

    sdk._get_proj().rebuild_all(
        str(sdk._journal_path_for_test.parent), confirm_destructive=True)
    replayed = _vec()
    assert replayed is not None, (
        "the replayed Event lost the vector the live node kept")
    assert ([round(float(x), 6) for x in list(replayed)]
            == [round(float(x), 6) for x in list(live)]), (
        "the replayed vector differs from the live one")
