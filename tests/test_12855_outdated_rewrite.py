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
    must not decide about silently: it is counted in the preview, and the
    carrier is left alone."""
    sdk, _events = env
    oid = sdk.create_object("carried", objectKind="test:widget")["id"]
    _mint_legacy(sdk, oid, "outdated")
    sdk._get_proj().g.query(
        "MATCH (n:Object {id:$id}) SET n.supersededBy = 'succ-1' "
        "SET n.supersededAt = '2026-01-01T00:00:00Z'",
        params={"id": oid})

    rep = tool.run(sdk, execute=False)

    assert [r["id"] for r in rep.carriers] == [oid]
    assert rep.by_word["outdated"] == 1
    # The carrier is part of the report, never the write's business.
    assert rep.rewritten == []


def test_the_legacy_outdated_FLAG_is_reported_and_not_rewritten(env):
    """`n.outdated = true` is a DIFFERENT axis (the Point lifecycle flag). The
    owner's decision excludes it; touching it here would corrupt the flag read
    every read surface performs."""
    sdk, _events = env
    oid = sdk.create_object("flagged", objectKind="test:widget")["id"]
    sdk._get_proj().g.query(
        "MATCH (n:Object {id:$id}) SET n.outdated = true", params={"id": oid})

    rep = tool.run(sdk, execute=False)

    assert rep.flag_count == 1, rep.flag_count
    assert [r["id"] for r in rep.flag_sample] == [oid]
    assert rep.by_word["outdated"] == 0, (
        "the flag is not a status word — it must not enter the status population")


# ══ the refusals ═════════════════════════════════════════════════════════


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


def test_an_empty_target_flag_is_refused_not_treated_as_absent(monkeypatch):
    """`--db-path ""` is a set-but-empty value: it would fall through to a
    different store while the operator believes they named one."""
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    assert tool.main(["--db-path", ""]) == 2
