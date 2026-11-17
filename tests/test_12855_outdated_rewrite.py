"""#12855 — the stored-rows rewrite: dry-run counts, journaled durability, idempotence.

The owner's decision on #12855 (2026-11-16) — *"Rewrite the stored rows once."* —
authorises dropping `outdated` from `assembly._RECALL_OBJECT_EXCLUDED_STATUSES`,
and the drop is safe only once no stored `:Object` carries the legacy word. This
module measures the three properties that make
`tools/rewrite_object_status.py` a MIGRATION rather than a tidying:

    AC1  a PREVIEW reports the real population, split by word, and writes nothing
    AC2  the rewrite is JOURNALED — a `rebuild_all` on the scratch instance
         reproduces the post-rewrite state (the acceptance criterion, and the
         reason a raw `SET` is not good enough: `test_a_raw_set_is_reverted_by
         _the_same_rebuild` is the negative control that makes AC2 discriminating)
    AC3  the rewrite is IDEMPOTENT — a second run finds nothing and appends
         nothing to the journal

The legacy rows are reproduced the way production holds them — a stored word
plus the journal record it came from — because the lifecycle write door no
longer mints `outdated` and stores `retracted` as `deprecated` (#11824 S0 /
#13143). See :func:`_mint_legacy`; that is what makes this module measure the
migration rather than a pre-door tree.

Everything runs against a temp DB and a temp JSONL journal: the tool is built
for the owner to run against production, and nothing here addresses a
production graph.

    uv run python tools/test_lane.py run -- tests/test_12855_outdated_rewrite.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import rewrite_object_status as tool

from tortoise.log import EventLog
from tortoise.sdk import TortoiseSDK


@pytest.fixture
def env(tmp_path):
    """``(sdk, events)`` — a real graph and a real JSONL journal on disk.

    Same shape as ``tests/test_3817_tags_rebuild_durability.py``: the journal is
    a FILE, because the distinction AC2 exists to enforce is "the record holds
    this" versus "the graph happened to hold it".
    """
    events = tmp_path / "events"
    events.mkdir()
    sdk = TortoiseSDK(str(tmp_path / "outdated12855.db"),
                      event_log_path=str(events / "events.jsonl"))
    yield sdk, events
    sdk.close()


def _journal(events) -> list[dict]:
    return EventLog(str(events / "events.jsonl")).read_all()


def _status(sdk, oid: str):
    return sdk.get_entity(oid).get("status")


def _legacy_rows(sdk) -> list:
    """The graph's own answer to "which rows still carry the word"."""
    return sdk._get_proj().g.query(
        "MATCH (n:Object) WHERE n.status IN ['outdated', 'retracted'] "
        "RETURN n.id ORDER BY n.id").result_set


def _mint_legacy(sdk, oid: str, word: str) -> None:
    """Reproduce a HISTORICAL legacy status word on `oid`.

    ⛔ The word can no longer be MINTED through the public write, and the
    fixture must not pretend otherwise. The lifecycle write door (#11824 S0 /
    #13143) accepts exactly `draft`/`live`/`deprecated`/`superseded`, stores
    `archived`/`retracted` AS `deprecated`, and REFUSES `outdated` — so on a
    current head `update_knowledge(obj, status='outdated')` raises and
    `status='retracted'` would land as `deprecated`. Writing either word the
    old way would therefore make this module pass only against a pre-door tree.

    The population this migration retires was written BEFORE the door narrowed,
    and production keeps those stored rows **verbatim — there is no backfill**
    (`lifecycle_vocabulary`: "Stored rows keep whatever they hold (no backfill,
    #11824 §1), which is why the families' READ sets still name the legacy words
    as deltas"). Reproducing that state therefore means reproducing the
    historical write as its two halves, which is exactly what the historical
    writer did:

      * the journal record it emitted — `EntityMutated{op: restatus,
        state: {status: <word>}}`, built by the SDK's ONE record builder, so the
        rebuild replays it and the row survives a rebuild as legacy; and
      * the live state that record left behind.

    This also keeps AC2 discriminating: the negative control writes its status
    as a raw `SET` with NO record, so it is still reverted by the rebuild while
    these rows are not.
    """
    journal = sdk._get_event_log()
    emitted = sdk._journal_entity_mutation(
        "Object", oid, "restatus", state={"status": word})
    if journal is not None:
        assert emitted is True, (
            "fixture precondition: the historical record must reach the journal")
    sdk._get_proj().g.query(
        "MATCH (n:Object {id:$id}) SET n.status = $status",
        params={"id": oid, "status": word})
    assert _status(sdk, oid) == word, "fixture precondition: the row is legacy"


def _seed(sdk) -> dict:
    """Two `outdated` Objects, one `retracted`, one live.

    The live one is created through the ordinary public path; the three legacy
    ones go through :func:`_mint_legacy`, because their word is no longer
    writable (see its docstring).
    """
    ids = {}
    for name, word in (("old-1", "outdated"), ("old-2", "outdated"),
                       ("gone-1", "retracted"), ("live-1", None)):
        oid = sdk.create_object(name, objectKind="test:widget")["id"]
        if word is not None:
            _mint_legacy(sdk, oid, word)
        ids[name] = oid
    return ids


# ══ AC1 — the preview is the measurement, and it writes nothing ═══════════


def test_the_preview_splits_the_population_by_word(env):
    """The two words are DIFFERENT questions and are counted separately.

    A single total would let a graph whose `retracted` rows are zero and whose
    `outdated` rows are many read the same as its reverse — and the owner's
    decision turns on zero, not on "some".
    """
    sdk, _events = env
    _seed(sdk)

    rep = tool.run(sdk, execute=False)

    assert rep.by_word["outdated"] == 2, rep.by_word
    assert rep.by_word["retracted"] == 1, rep.by_word
    assert rep.total == 3, rep.total
    assert rep.executed is False


def test_the_preview_does_not_write_anything(env):
    """A preview that mutates is not a preview — asserted by effect, on the
    graph AND on the journal (the two stores the execute arm touches)."""
    sdk, events = env
    ids = _seed(sdk)
    before_records = len(_journal(events))
    before_status = {name: _status(sdk, oid) for name, oid in ids.items()}

    tool.run(sdk, execute=False)

    assert {name: _status(sdk, oid) for name, oid in ids.items()} == before_status
    assert len(_journal(events)) == before_records, (
        "the preview appended a journal record — previews must be read-only")


# ══ AC2 — the rewrite is journaled, proven by a rebuild ═══════════════════


def test_each_rewrite_is_journaled_as_a_status_mutation(env):
    """The MECHANISM, asserted on the record: one `EntityMutated` carrying
    `state.status='deprecated'` per rewritten row.

    Without this, an outcome-only assertion could pass on a raw `SET` for as
    long as nobody rebuilt — which is exactly the failure mode the owner's step
    4 names.
    """
    sdk, events = env
    _seed(sdk)

    rep = tool.run(sdk, execute=True)
    assert len(rep.rewritten) == 3, rep.failures

    records = [e for e in _journal(events) if e.get("type") == "EntityMutated"]
    restatus = [e for e in records
                if (e.get("state") or {}).get("status") == tool.REWRITE_TO]
    assert {e["id"] for e in restatus} == set(rep.rewritten), (
        "every rewritten row must carry its own journaled status mutation")
    assert all(e.get("label") == "Object" for e in restatus), (
        "the record must be scoped to the label the write landed on")


def test_a_truncated_POST_read_is_reported_not_certified(env, monkeypatch):
    """The POST-condition must be judged on the POST read.

    A truncated post-read can show `remaining == 0` while rows remain; if the
    post-read's own truncation flag is dropped, `--execute` reports success on a
    graph it could not see. (Mutation-tested: deleting the fold left this suite
    green before this test existed.)
    """
    sdk, _events = env
    _seed(sdk)

    real_collect = tool.collect
    calls = {"n": 0}

    def second_read_truncated(proj, *, sample=20):
        rep = real_collect(proj, sample=sample)
        calls["n"] += 1
        if calls["n"] >= 2:            # the post-write read
            rep.truncated = True
        return rep

    monkeypatch.setattr(tool, "collect", second_read_truncated)

    rep = tool.run(sdk, execute=True)

    assert rep.truncated is True, "a truncated post-read must not be reported clean"
    assert tool.main(["--db-path", str(env[0]._db_path), "--execute"]) in (1, 2)


def test_a_fanout_that_clears_before_the_write_is_written_not_refused(env):
    """The fan-out report must follow the WRITE, not the preview's snapshot.

    A sibling deleted between the preview and `--execute` means the address is
    unambiguous by the time of the write: the row IS rewritten, and the report
    must not keep telling the operator it was "refused, never written" (nor keep
    `main` at exit 1 on a clean run — the sequencing step depends on that 0).
    """
    sdk, _events = env
    oid = sdk.create_object("cleared", objectKind="test:widget")["id"]
    _mint_legacy(sdk, oid, "outdated")
    sdk.create_point("stmt", "temporary sibling", id=oid)

    preview = tool.run(sdk, execute=False)
    assert [r["id"] for r in preview.fanout] == [oid], preview.fanout

    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) DETACH DELETE n", params={"id": oid})

    rep = tool.run(sdk, execute=True, report=preview)

    assert rep.rewritten == [oid], rep.failures
    assert rep.fanout == [], "a refused row that WAS written must not still be listed"
    assert rep.pending == [], rep.pending


def test_a_post_write_status_that_did_not_move_is_a_failure(env, monkeypatch):
    """The post-write guard is measured: a write that did not reach the row fails.

    `update_knowledge` returns a dict whatever happened, and it resolves by label
    precedence — so the guard must be the `:Object` node's own stored status.
    """
    sdk, _events = env
    ids = _seed(sdk)

    monkeypatch.setattr(tool, "_object_status", lambda proj, rid: "outdated")

    rep = tool.run(sdk, execute=True)

    assert rep.rewritten == [], rep.rewritten
    assert len(rep.failures) == len(ids) - 1, rep.failures   # `_seed` has one live row
    assert all("left the :Object" in f["error"] for f in rep.failures), rep.failures


def test_a_failed_run_leaves_a_marker_that_stops_the_zero_rows_conclusion(env):
    """A FAILED run must not hand the next preview a clean-looking "0 rows".

    The live write lands, the record does not, the run exits 1 — and the very
    next preview would otherwise print the sentence that authorises dropping the
    `assembly` member. The marker written BEFORE the first write is what makes
    that impossible (and covers a SIGTERM, which writes nothing at all).
    """
    sdk, events = env
    oid = sdk.create_object("unrecorded", objectKind="test:widget")["id"]
    _mint_legacy(sdk, oid, "outdated")

    real_append = EventLog.append

    def boom(self, event):
        raise OSError("journal is read-only")

    EventLog.append = boom
    try:
        rep = tool.run(sdk, execute=True)
    finally:
        EventLog.append = real_append

    assert rep.failures, "precondition: the run must have failed"
    assert rep.remaining == {w: 0 for w in tool.LEGACY_WORDS}, rep.remaining

    second = tool.run(sdk, execute=False)

    assert second.total == 0, "precondition: the live graph reads clean"
    assert second.pending == [oid], second.pending
    assert "dead weight" not in tool.render(second)
    assert tool.main(["--db-path", str(env[0]._db_path),
                      "--event-log-path", str(events / "events.jsonl")]) == 1


def test_the_rewrite_survives_a_rebuild_all(env):
    """ACCEPTANCE: the post-rewrite state reproduces from the journal.

    `rebuild_all` wipes the graph and replays the event stream, so this is the
    measurement that separates a durable rewrite from a live-only one.
    """
    sdk, events = env
    ids = _seed(sdk)

    tool.run(sdk, execute=True)
    assert _legacy_rows(sdk) == [], "precondition: the live graph is clean"

    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)

    assert _legacy_rows(sdk) == [], (
        "#12855: the rebuild resurrected a legacy status word — the rewrite was "
        "not journaled, so it is not a migration")
    for name in ("old-1", "old-2", "gone-1"):
        assert _status(sdk, ids[name]) == tool.REWRITE_TO, (
            f"{name} did not reproduce its rewritten status after the rebuild")


def test_a_raw_set_is_reverted_by_the_same_rebuild(env):
    """NEGATIVE CONTROL — the durability assertion above is not vacuous.

    The exact write `update_knowledge` replaces, performed as a raw `SET`,
    survives on the live graph and is UNDONE by the rebuild. If this test ever
    starts passing in the other direction, `test_the_rewrite_survives_a_rebuild
    _all` has stopped discriminating between the two mechanisms.
    """
    sdk, events = env
    oid = sdk.create_object("raw-set-victim", objectKind="test:widget")["id"]
    _mint_legacy(sdk, oid, "outdated")
    sdk._get_proj().g.query(
        "MATCH (n:Object {id:$id}) SET n.status = 'deprecated'",
        params={"id": oid})
    assert _status(sdk, oid) == "deprecated", "precondition: the raw SET landed live"

    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)

    assert _status(sdk, oid) == "outdated", (
        "control: a raw SET must be reverted by the rebuild — if it survives, "
        "the journal is not the reconstruction source this module assumes and "
        "the durability claim needs re-measuring")


def test_the_rewrite_reaches_zero_rows_including_the_dead_to_dead_move(env):
    """`retracted` -> `deprecated` is dead->dead, which the Object door admits
    by design; `outdated` -> `deprecated` is live->dead. Both must land, and the
    graph must end at zero."""
    sdk, _events = env
    ids = _seed(sdk)

    tool.run(sdk, execute=True)

    assert _legacy_rows(sdk) == []
    assert _status(sdk, ids["gone-1"]) == "deprecated"
    assert _status(sdk, ids["live-1"]) is None or _status(sdk, ids["live-1"]) == "live", (
        "the rewrite must not touch a row that never carried a legacy word")


# ══ AC3 — idempotence ═════════════════════════════════════════════════════


def test_a_failed_journal_append_is_a_failure_and_exits_1(tmp_path, monkeypatch):
    """The RECORD is part of the write, not a side effect.

    The SDK's append is best-effort (it returns False when it raises) and
    `_update_entity` ignores that return, so a failed append leaves a LIVE-ONLY
    write that the next `rebuild_all` reverts — with no exception anywhere. This
    tool must not report that as a success: the row is a failure, it is not
    counted as rewritten, and the run exits 1.
    """
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)

    def fresh_legacy(name):
        """One live-only leg needs its OWN graph: the first run rewrites the
        only legacy row, so a second run over the same DB legitimately finds
        nothing to do (population 0, exit 0) and would measure nothing.

        Called BEFORE the append is broken — the historical record has to reach
        the journal (`_mint_legacy` asserts it), or the row is not the legacy
        shape this test is about.
        """
        db = tmp_path / name
        events = tmp_path / (name + "-ev")
        events.mkdir()
        journal = events / "events.jsonl"
        sdk = TortoiseSDK(str(db), event_log_path=str(journal))
        oid = sdk.create_object("unrecorded", objectKind="test:widget")["id"]
        _mint_legacy(sdk, oid, "outdated")
        sdk.close()
        return db, journal, oid

    run_db, run_journal, oid = fresh_legacy("unrecorded.db")
    cli_db, cli_journal, _cli_oid = fresh_legacy("unrecorded-cli.db")

    def boom(self, event):
        raise OSError("journal is read-only")

    monkeypatch.setattr(EventLog, "append", boom)

    # ⛔ Drive `run` and assert the PER-ROW verdict, not just the process exit.
    # The batch backstop (`journal_delta < len(rewritten)`) also returns 1, so an
    # exit-code-only assertion would stay green with the per-row probe deleted —
    # and the per-row probe is the half a batch delta CANNOT do: attribute the
    # missing record to a row.
    sdk = TortoiseSDK(str(run_db), event_log_path=str(run_journal))
    try:
        rep = tool.run(sdk, execute=True)
        assert rep.rewritten == [], "a live-only row must never count as rewritten"
        assert [f.get("id") for f in rep.failures] == [oid], rep.failures
        assert "LIVE-ONLY" in rep.failures[0]["error"], rep.failures[0]["error"]
        assert rep.journal_delta == 0, rep.journal_delta
    finally:
        sdk.close()
    sdk = TortoiseSDK(str(run_db), event_log_path=str(run_journal))
    try:
        # The write DID land live — that is exactly why the guard is needed.
        assert _status(sdk, oid) == "deprecated"
        assert _legacy_rows(sdk) == []
    finally:
        sdk.close()

    # …and the CLI arm answers the same state with exit 1.
    rc = tool.main(["--db-path", str(cli_db), "--event-log-path", str(cli_journal),
                    "--execute"])

    assert rc == 1, "a live-only rewrite must never exit 0"


def test_a_sibling_created_after_the_read_is_refused_at_write_time(env):
    """The fan-out guard is re-resolved AT THE WRITE, not trusted from the read.

    On `--execute` the Report is the PREVIEW's, so a sibling created between the
    preview and the write is invisible in its snapshot. Guarding only from the
    snapshot writes the sibling and leaves the `:Object` untouched — with no
    undo. Measured precondition: this exact sequence restamped the Point.
    """
    sdk, _events = env
    oid = sdk.create_object("late-sibling", objectKind="test:widget")["id"]
    _mint_legacy(sdk, oid, "outdated")

    preview = tool.run(sdk, execute=False)
    assert preview.fanout == [], "precondition: the snapshot shows no fan-out"

    # …the sibling appears AFTER the read, BEFORE the write.
    sdk.create_point("stmt", "late sibling claim", id=oid)

    rep = tool.run(sdk, execute=True, report=preview)

    assert rep.rewritten == [], "a raced row must never be counted as rewritten"
    assert [f.get("id") for f in rep.failures] == [oid], rep.failures
    both = sdk._get_proj().g.query(
        "MATCH (n) WHERE n.id=$id RETURN labels(n), n.status",
        params={"id": oid}).result_set
    statuses = {",".join(labs): st for labs, st in both}
    assert statuses["Point"] == "live", "the late sibling :Point must be untouched"
    assert statuses["Object"] == "outdated"


def test_a_sibling_deleted_after_the_read_stops_being_reported_as_refused(env):
    """The report must describe what the WRITE did, not what the read predicted.

    `rep.fanout` is the PRE-read snapshot, so a sibling deleted between the read
    and the write leaves a row in it that the write then rewrote — and the
    operator reads "refused, never written" about a row the run just changed.
    Deleting the reconciliation block leaves every other row green (measured
    2026-11-17), so without this test the block is deletable in silence.
    """
    sdk, _events = env
    oid = sdk.create_object("late-gone", objectKind="test:widget")["id"]
    _mint_legacy(sdk, oid, "outdated")
    # …a sibling present AT THE READ, so the snapshot really does refuse the row.
    sdk.create_point("stmt", "sibling claim", id=oid)

    preview = tool.run(sdk, execute=False)
    assert [r.get("id") for r in preview.fanout] == [oid], (
        f"precondition: the snapshot must refuse the row. got {preview.fanout!r}")

    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) DELETE n", params={"id": oid})

    rep = tool.run(sdk, execute=True, report=preview)

    assert rep.rewritten == [oid], (
        f"the sibling is gone, so the write must land. failures={rep.failures!r}")
    assert rep.fanout == [], (
        "a row this run REWROTE must not still be reported as refused: "
        f"{rep.fanout!r}")
    assert rep.journal_delta == 1, rep.journal_delta


def test_a_url_only_source_stub_sharing_the_id_is_refused(env):
    """The resolution uses the WRITE DOOR's identity keys, not just `m.id`.

    `Source` declares a secondary identity key (`url`) because url-only stubs
    are minted with no `id`, so a `:Source {url: <object id>}` is bound by an
    id-addressed write while carrying no `id` at all. A `m.id = $rid`-only guard
    misses it, writes the Source, and still exits 0 (measured).
    """
    sdk, _events = env
    oid = sdk.create_object("with-stub", objectKind="test:widget")["id"]
    _mint_legacy(sdk, oid, "outdated")
    sdk._get_proj().g.query(
        "CREATE (s:Source {url:$url, title:'url-only stub'})",
        params={"url": oid})

    preview = tool.run(sdk, execute=False)
    assert [r["id"] for r in preview.fanout] == [oid], preview.fanout

    rep = tool.run(sdk, execute=True)

    assert rep.rewritten == [], rep.failures
    stub = sdk._get_proj().g.query(
        "MATCH (s:Source) WHERE s.url=$url RETURN s.status",
        params={"url": oid}).result_set
    assert stub == [[None]], f"the Source stub must be untouched, got {stub!r}"


def test_a_point_sharing_the_id_is_refused_and_never_written(env):
    """`update_knowledge` binds on the ID, not on the row.

    A `:Point` that merely SHARES an `:Object`'s id is invisible in that row's
    labels, so a labels-only guard lets the Point arm restamp the POINT (#12855
    keeps Points' words) while the `:Object` stays untouched — and the call
    still returns the target status, so a return-value guard would certify the
    rewrite. The id must be resolved, and the row refused.
    """
    sdk, _events = env
    oid = sdk.create_object("shared-id", objectKind="test:widget")["id"]
    _mint_legacy(sdk, oid, "outdated")
    sdk.create_point("stmt", "sibling claim", id=oid)

    preview = tool.run(sdk, execute=False)
    assert [r["id"] for r in preview.fanout] == [oid], preview.fanout

    rep = tool.run(sdk, execute=True)

    assert rep.rewritten == [], "a fan-out row must never be counted as rewritten"
    assert [f.get("id") for f in rep.failures] == [oid], rep.failures
    both = sdk._get_proj().g.query(
        "MATCH (n) WHERE n.id=$id RETURN labels(n), n.status",
        params={"id": oid}).result_set
    statuses = {",".join(labs): st for labs, st in both}
    assert statuses["Object"] == "outdated", "the :Object row must be untouched"
    assert statuses["Point"] == "live", "the sibling :Point must be untouched"


def test_a_truncated_read_cannot_print_the_zero_rows_conclusion():
    """The `0 rows / dead weight` sentence is what authorises the SEQUENCED drop.

    It must never appear beside (or without) the truncation warning: a read that
    fell short is not a measurement of zero.
    """
    rep = tool.Report(total=0, store_count=3, truncated=True)
    out = tool.render(rep)
    assert "dead weight" not in out
    assert "TRUNCATED" in out


def test_a_truncated_preview_exits_nonzero(tmp_path, monkeypatch):
    """A preview over an incomplete read is not a complete answer (exit 1)."""
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    db = tmp_path / "trunc.db"
    events = tmp_path / "ev"
    events.mkdir()
    journal = events / "events.jsonl"
    sdk = TortoiseSDK(str(db), event_log_path=str(journal))
    oid = sdk.create_object("trunc", objectKind="test:widget")["id"]
    _mint_legacy(sdk, oid, "outdated")
    sdk.close()

    real_collect = tool.collect

    def short_read(proj, *, sample=20):
        rep = real_collect(proj, sample=sample)
        rep.truncated = True
        return rep

    monkeypatch.setattr(tool, "collect", short_read)

    assert tool.main(["--db-path", str(db),
                      "--event-log-path", str(journal)]) == 1


def test_a_second_run_finds_nothing_and_appends_nothing(env):
    """Re-running is safe by measurement, not by assumption: the population is
    empty and the journal does not grow."""
    sdk, events = env
    _seed(sdk)

    first = tool.run(sdk, execute=True)
    assert len(first.rewritten) == 3
    records_after_first = len(_journal(events))

    second = tool.run(sdk, execute=True)

    assert second.total == 0, second.by_word
    assert second.rewritten == []
    assert len(_journal(events)) == records_after_first, (
        "a second run appended a record — it is not idempotent")


# ══ the boundaries the tool reports rather than crosses ═══════════════════


def test_a_row_carrying_a_supersession_carrier_is_reported(env):
    """A row with BOTH a legacy word and a successor carrier is a shape the tool
    must not decide about silently: it is counted in the preview, and the WRITE
    leaves the carrier alone.

    ⚠️ The carrier assertion is made on an EXECUTE run on purpose. Asserting
    `rewritten == []` on a PREVIEW measured nothing at all: `run` returns before
    `rewrite` whenever `execute` is false, so that assertion held for every
    preview ever taken (review finding, 2026-11-17).
    """
    sdk, _events = env
    oid = sdk.create_object("carried", objectKind="test:widget")["id"]
    _mint_legacy(sdk, oid, "outdated")
    sdk._get_proj().g.query(
        "MATCH (n:Object {id:$id}) SET n.supersededBy = 'succ-1' "
        "SET n.supersededAt = '2026-01-01T00:00:00Z'",
        params={"id": oid})

    preview = tool.run(sdk, execute=False)
    assert [r["id"] for r in preview.carriers] == [oid]
    assert preview.by_word["outdated"] == 1

    rep = tool.run(sdk, execute=True)

    assert rep.rewritten == [oid], rep.failures
    row = sdk.get_entity(oid)
    assert row.get("status") == tool.REWRITE_TO, "the status IS rewritten"
    assert row.get("supersededBy") == "succ-1", "the carrier is NOT touched"
    assert row.get("supersededAt") == "2026-01-01T00:00:00Z"


def test_the_legacy_outdated_FLAG_is_reported_and_not_rewritten(env):
    """`n.outdated = true` is a DIFFERENT axis (the Point lifecycle flag). The
    owner's decision excludes it; touching it here would corrupt the flag read
    every read surface performs.

    ⛔ Both halves are measured: the flag is REPORTED, and an `--execute` run
    leaves it exactly as it found it (a preview-only assertion would hold for
    any preview ever taken and measure nothing about the write).
    """
    sdk, _events = env
    oid = sdk.create_object("flagged", objectKind="test:widget")["id"]
    sdk._get_proj().g.query(
        "MATCH (n:Object {id:$id}) SET n.outdated = true", params={"id": oid})

    rep = tool.run(sdk, execute=False)

    assert rep.flag_count == 1, rep.flag_count
    assert [r["id"] for r in rep.flag_sample] == [oid]
    assert rep.by_word["outdated"] == 0, (
        "the flag is not a status word — it must not enter the status population")

    tool.run(sdk, execute=True)

    flag = sdk._get_proj().g.query(
        "MATCH (n:Object {id:$id}) RETURN n.outdated",
        params={"id": oid}).result_set
    assert flag == [[True]], f"the flag must survive the rewrite, got {flag!r}"


# ══ the refusals ═════════════════════════════════════════════════════════


def test_a_dual_labelled_point_row_is_reported_not_silently_skipped(env):
    """A row labelled BOTH `:Point` and `:Object` is REFUSED, and the run says so.

    `update_knowledge` dispatches on the label, so such a row takes the Point
    arm, where a terminal Point declares no outgoing transition (#2498: a dead
    claim is answered by minting a successor, never by re-stamping it). This
    tool must not work around that — overriding it would be a REOPEN of #2498 —
    but it also must not let the row vanish from the count: the operator has to
    see the id and the refusal, or a graph holding such a row would read clean.

    ⚠️ The arm does not RAISE on this head (#11824 S0): a stored `retracted`
    already MEANS `deprecated`, so the write is an ECHO — it lands, journals the
    word the row already holds, and leaves the row untouched. So the tool
    refuses the row from its own labels BEFORE the write, and the post-write
    status check is the second guard; either way the id is reported.
    """
    sdk, _events = env
    oid = sdk.create_object("dual", objectKind="test:widget")["id"]
    _mint_legacy(sdk, oid, "retracted")
    sdk._get_proj().g.query(
        "MATCH (n:Object {id:$id}) SET n:Point", params={"id": oid})

    rep = tool.run(sdk, execute=True)

    assert rep.rewritten == [], "a refused row must never be counted as rewritten"
    assert [f.get("id") for f in rep.failures] == [oid], rep.failures
    assert "dead claim" in rep.failures[0]["error"], rep.failures[0]["error"]
    assert _legacy_rows(sdk) == [[oid]], "the row is untouched, and still counted"
    # ⛔ THE ORDER, measured. Without the pre-write label refusal the call is the
    # documented ECHO and appends a record; with it, nothing is appended at all.
    # The post-state alone cannot tell the two apart — the row is unchanged
    # either way — so an assertion on status only would not discriminate the
    # refusal from the post-write guard that would otherwise have caught it
    # (review finding, 2026-11-17).
    assert (rep.journal_delta or 0) == 0, (
        "a pre-write refusal must append nothing to the journal — a non-zero "
        "delta means the write was attempted and only the post-write guard saved it")


def test_a_missing_journal_refuses_before_any_write(tmp_path, monkeypatch):
    """No journal ⇒ every write would be live-only and a rebuild would revert
    it, so the tool refuses by name instead of producing that. Asserted through
    `main` (the arm an operator runs), and asserted by EFFECT: exit 2 and the
    row unchanged."""
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    monkeypatch.delenv("TORTOISE_EVENT_LOG_BASE_DIR", raising=False)
    db = tmp_path / "journal-less.db"
    sdk = TortoiseSDK(str(db))
    oid = sdk.create_object("unjournaled", objectKind="test:widget")["id"]
    _mint_legacy(sdk, oid, "outdated")
    sdk.close()

    rc = tool.main(["--db-path", str(db)])

    assert rc == 2, "a journal-less target must be refused, not rewritten"
    sdk = TortoiseSDK(str(db))
    try:
        assert _status(sdk, oid) == "outdated", "the refusal must precede any write"
    finally:
        sdk.close()


def test_rewrite_refuses_a_journal_less_target_instead_of_writing_live_only(
        tmp_path, monkeypatch):
    """`rewrite` is a PUBLIC writer, and it used to write before it checked.

    `run` answers the journal precondition, but a direct `rewrite` call did not:
    measured 2026-11-17, it restamped the row (`outdated` → `deprecated`,
    LIVE-ONLY, revertable by the next `rebuild_all`) and then died on
    `None.position()` — the exact write this tool exists to prevent, on the one
    path the CLI's arm does not cover (review finding, 2026-11-17).
    """
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    monkeypatch.delenv("TORTOISE_EVENT_LOG_BASE_DIR", raising=False)
    db = tmp_path / "no-journal-writer.db"
    sdk = TortoiseSDK(str(db))
    try:
        oid = sdk.create_object("unjournaled", objectKind="test:widget")["id"]
        _mint_legacy(sdk, oid, "outdated")
        assert sdk._get_event_log() is None, "precondition: no journal on this target"
        rep = tool.collect(sdk._get_proj())
        assert [r["id"] for r in rep.rows] == [oid], "precondition: one legacy row"

        with pytest.raises(tool.Refused) as refused:
            tool.rewrite(sdk, rep)

        assert refused.value.reason == tool.REFUSE_NO_JOURNAL, refused.value.reason
        assert rep.executed is False, "a refused write must not read as executed"
        assert rep.rewritten == [], rep.rewritten
        assert _status(sdk, oid) == "outdated", "the refusal must precede the write"
    finally:
        sdk.close()


def test_an_empty_target_flag_is_refused_not_treated_as_absent(
        tmp_path, monkeypatch, capsys):
    """`--db-path ""` is a set-but-empty value: it would fall through to a
    different store while the operator believes they named one.

    ⛔ A REAL JOURNAL IS PASSED, deliberately (review finding, 2026-11-17). When
    no journal is configured the journal-less refusal ALSO exits 2, so the exit
    code alone let this row pass with the empty-flag guard disabled — it proved
    nothing. Supplying the journal leaves `2` reachable only from the guard, and
    the refusal is then asserted BY NAME.
    """
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    monkeypatch.delenv("TORTOISE_EVENT_LOG_BASE_DIR", raising=False)
    journal = tmp_path / "journal.jsonl"
    journal.write_text("")

    rc = tool.main(["--db-path", "", "--event-log-path", str(journal)])

    assert rc == 2, "an empty target flag must be refused, not treated as absent"
    err = capsys.readouterr().err
    assert tool.REFUSE_EMPTY_FLAG in err, err
    assert tool.REFUSE_NO_JOURNAL not in err, (
        "the wrong refusal answered: the journal WAS supplied, so rc=2 must come "
        f"from the empty-flag guard. stderr: {err!r}")
    assert "--db-path" in err, "the refusal must name the flag that was empty"


def test_two_targets_at_once_are_refused_not_silently_resolved(
        tmp_path, monkeypatch, capsys):
    """`--uri` AND `--db-path` name two stores, and the SDK prefers `db_path`.

    Resolving it silently writes the EMBEDDED graph while the operator believes
    the container was addressed — and the journal appended to is whichever
    `--event-log-path` names, so a production journal can receive a record for a
    write that never touched the production graph.

    ⛔ A REAL JOURNAL IS PASSED AND THE REFUSAL IS ASSERTED BY NAME (review
    finding, 2026-11-17): with no journal configured the journal-less refusal
    answers `2` as well, so `rc == 2` alone stayed green with the ambiguity
    clause deleted, and this row proved nothing about the guard it names.
    """
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    monkeypatch.delenv("TORTOISE_EVENT_LOG_BASE_DIR", raising=False)
    journal = tmp_path / "journal.jsonl"
    journal.write_text("")
    would_be_embedded = tmp_path / "should-never-exist.db"

    rc = tool.main(["--uri", "bolt://127.0.0.1:1/none",
                    "--db-path", str(would_be_embedded),
                    "--event-log-path", str(journal)])

    assert rc == 2, "two targets must be refused, not silently resolved"
    err = capsys.readouterr().err
    # The ambiguity has its OWN reason token: the defect is two stores named at
    # once, not an empty value, and `empty-target-flag` on this line read as the
    # wrong defect (review nit).
    assert tool.REFUSE_AMBIGUOUS_TARGET in err, err
    assert tool.REFUSE_EMPTY_FLAG not in err, err
    assert tool.REFUSE_NO_JOURNAL not in err, (
        "the wrong refusal answered: a journal WAS supplied, so rc=2 must be the "
        f"ambiguity guard's. stderr: {err!r}")
    assert "--uri and --db-path" in err, "the refusal must name both flags"
    assert not would_be_embedded.exists(), (
        "nothing may be opened on a refused target")


def test_run_refuses_a_journal_less_target_instead_of_crashing(tmp_path, monkeypatch):
    """The journal precondition is a REFUSAL at the `run` layer, not a crash.

    A refusal reachable only from `main` is one every other caller walks past —
    here it produced `AttributeError: 'NoneType' object has no attribute
    'position'` out of the write loop. It fails closed either way, but the
    contract (`Refused`) has to hold for a non-CLI caller too.
    """
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    monkeypatch.delenv("TORTOISE_EVENT_LOG_BASE_DIR", raising=False)
    sdk = TortoiseSDK(str(tmp_path / "no-journal.db"))
    try:
        with pytest.raises(tool.Refused):
            tool.run(sdk, execute=True)
    finally:
        sdk.close()


# ══ the refusals are not a CLI-only courtesy ═════════════════════════════


def test_run_execute_raises_the_refusal_instead_of_half_writing(env, monkeypatch):
    """`run(execute=True)` is the layer every writing test drives, and it is the
    layer a non-CLI caller reaches.

    With the gates only in `main`, that call rewrote the addressable rows and
    left the rest behind — the partial rewrite the refusal text names — and no
    suite could see it (review finding, 2026-11-17). Asserted by EFFECT: the
    refusal IS raised and the population is untouched.
    """
    sdk, _events = env
    _seed(sdk)
    real_collect = tool.collect

    def short_read(proj, *, sample=20):
        rep = real_collect(proj, sample=sample)
        rep.truncated = True
        return rep

    monkeypatch.setattr(tool, "collect", short_read)

    with pytest.raises(tool.Refused) as refused:
        tool.run(sdk, execute=True)

    assert refused.value.reason == tool.REFUSE_TRUNCATED
    monkeypatch.undo()
    assert len(_legacy_rows(sdk)) == 3, "nothing may be written before the refusal"


# ══ the CLI's SUCCESS arm — exit 0, which nothing used to exercise ═══════


def test_main_execute_rewrites_the_population_and_exits_zero(tmp_path, monkeypatch):
    """`--execute` on a rewritable graph exits 0 and leaves no legacy row.

    Before this test only the exit-2 arms of `main` were driven, so the arm an
    operator actually runs — and the exit code the documented contract promises
    for it — was never measured.
    """
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    monkeypatch.delenv("TORTOISE_EVENT_LOG_BASE_DIR", raising=False)
    db = tmp_path / "exec.db"
    events = tmp_path / "events"
    events.mkdir()
    journal = events / "events.jsonl"

    sdk = TortoiseSDK(str(db), event_log_path=str(journal))
    ids = _seed(sdk)
    sdk.close()

    argv = ["--db-path", str(db), "--event-log-path", str(journal)]
    assert tool.main(argv) == 0, "the preview is a success, population or not"
    assert tool.main([*argv, "--execute", "--json"]) == 0

    sdk = TortoiseSDK(str(db), event_log_path=str(journal))
    try:
        assert _legacy_rows(sdk) == []
        for name in ("old-1", "old-2", "gone-1"):
            assert _status(sdk, ids[name]) == tool.REWRITE_TO, name
    finally:
        sdk.close()


def test_an_unreadable_journal_probe_fails_the_row_instead_of_skipping(env, monkeypatch):
    """The per-row durability probe FAILS CLOSED, which is what its docstring says.

    The probe is IDENTITY-based (`cursor_at_end` + `read_after`: the records
    appended in this row's window must contain THIS row's own record), so an
    unreadable cursor answers `None`. A row must not be certified on a
    measurement that was never taken — the batch gate fails on a `None` delta for
    the same reason, and a skip would leave the two gates opposite, the weaker
    one guarding the finer-grained question (review finding, 2026-11-17).
    """
    sdk, _events = env
    _seed(sdk)
    monkeypatch.setattr(tool, "_journal_cursor", lambda log: None)

    rep = tool.run(sdk, execute=True)

    assert rep.rewritten == [], "nothing may be certified on an unread position"
    assert len(rep.failures) == 3, rep.failures
    assert all("UNPROVEN" in f["error"] for f in rep.failures), rep.failures


def test_a_dual_arm_row_needs_a_record_that_names_the_OBJECT(env, monkeypatch):
    """A `[Object, Source]` row appends TWO records; the OBJECT's is the one that counts.

    The door writes once per arm (measured 2026-11-17: this row appends two), so a
    gate that is satisfied by "the journal grew" can be satisfied by the WRONG
    arm's record. Reproduced by failing the FIRST append after arming (the
    `:Object` arm) and letting the second succeed: the journal still grows, and a
    rebuild reverts the Object to `outdated` because only the Object record folds
    onto it. The gate must demand the row's OWN record, by label and id.
    """
    sdk, _events = env
    oid = sdk.create_object("dual-arm", objectKind="test:widget")["id"]
    _mint_legacy(sdk, oid, "outdated")
    sdk._get_proj().g.query(
        "MATCH (n:Object {id:$id}) SET n:Source", params={"id": oid})

    real_append = EventLog.append
    calls = {"n": 0}

    def flaky(self, event):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("journal is read-only for the Object arm")
        return real_append(self, event)

    monkeypatch.setattr(EventLog, "append", flaky)

    rep = tool.run(sdk, execute=True)

    assert calls["n"] >= 2, "the fixture must reach both arms"
    assert rep.rewritten == [], (
        "the :Object arm's record never landed, so the row is not durable")
    assert [f.get("id") for f in rep.failures] == [oid], rep.failures
