"""#9451 — a capture onto a PRE-EXISTING ``session:<id>`` Source stub must leave
``ingestedAt`` equal to what a rebuild re-folds from the journal.

The defect this pins: ``_materialize_session_source`` keeps its live SET
``s.ingestedAt = coalesce(s.ingestedAt, $now)`` (#398's never-overwrite contract
— the field means "when Tortoise FIRST saw this source"), so on a stub that
already existed it stores the EARLIER writer's instant, while the
``SourceCreated`` record it emitted carried ``now`` — a value the write never
wrote. ``_upsert_source`` reads the recorded instant on its ON CREATE arm
(#5024) and had **no ``ingestedAt`` clause at all on ON MATCH**, so a rebuild
landed the record's value on a node whose live value was the stub's:
``derived == replay(journal)`` (#5024/#5097/#8501) was FALSE for the field on
exactly the arm where the stub pre-exists.

The stub pre-exists whenever another door minted the ref first — here the
shortest reproduction from the issue: ``create_point(..., session_id="S1")``
infers ``extractedFrom="session:S1"`` → ``_link_source`` → ``_mint_source_stub``
with the clock at ITS write, which is never journaled as an instant.

BOTH replay engines are pinned, because they reach different arms of
``_upsert_source`` (see ``tests/test_5097_capture_source_replay.py``'s module
docstring): ``rebuild_all()`` folds ``SourceCreated`` before the stub mint, so
the record CREATES the node (ON CREATE); ``rebuild()`` replays record by record,
so the stub is re-minted first and the record lands on ON MATCH — the arm this
issue is about. A fix verified only on ``rebuild_all()`` is half a fix.

Every assertion compares the WHOLE property bag, not only ``ingestedAt``, so a
neighbouring property cannot hide a second divergence behind the field pin.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise.log import EventLog
from tortoise.sdk import TortoiseSDK

CONV = [
    {"role": "user", "content": "I think the auth dead-end is the top issue. "
                                "We decided to ship serve --http first."},
    {"role": "assistant", "content": "Agreed. Evidence suggests the website "
                                     "config is the root cause."},
    {"role": "user", "content": "ok"},
]
LATER_NOW = "2099-01-01T00:00:00+00:00"
CONV_2 = [
    {"role": "user", "content": "Follow-up: the website config fix shipped. "
                                "Next is the retry budget."},
    {"role": "assistant", "content": "Noted. The retry budget is the open item."},
]


@pytest.fixture()
def prov(tmp_path, monkeypatch):
    """(sdk, events_dir, log_path) — journal-backed embedded SDK, mock extractor."""
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    monkeypatch.delenv("TORTOISE_SESSION_EXTRACTOR", raising=False)
    db = tmp_path / "t.db"
    events = tmp_path / "events"
    events.mkdir()
    log_path = str(events / "events.jsonl")
    sdk = TortoiseSDK(str(db), event_log_path=log_path)
    yield sdk, events, log_path
    sdk.close()


def _bag(sdk, url):
    rows = sdk._get_proj().g.query(
        "MATCH (s:Source {url:$u}) RETURN properties(s)",
        params={"u": url}).result_set
    assert rows, f"no session Source {url}"
    assert len(rows) == 1, f"{len(rows)} :Source nodes for {url} — duplicate"
    # `dict(...)`: `properties(s)` comes back as an OrderedDict, whose `==` is
    # ORDER-sensitive — a replay can rebuild the same bag in another order.
    return dict(rows[0][0])


def _mint_stub_via_create_point(sdk, session_id):
    """Mint the ``session:<id>`` stub through a NON-capture door.

    ``create_point`` with ``session_id`` infers ``extractedFrom="session:<id>"``,
    which is enough to run ``_link_source`` → ``_mint_source_stub`` — the writer
    whose ``ingestedAt`` choice is never journaled.
    """
    sdk.create_point("observation", "the earlier point, linked to the session",
                     session_id=session_id)
    url = f"session:{session_id}"
    rows = sdk._get_proj().g.query(
        "MATCH (s:Source {url:$u}) RETURN s.ingestedAt",
        params={"u": url}).result_set
    assert rows, f"create_point(session_id=...) minted no stub at {url}"
    return url, rows[0][0]


def _capture_records(events):
    """Every ``SourceCreated`` payload this journal holds."""
    out = []
    for line in (events / "events.jsonl").read_text().splitlines():
        if not line.strip():
            continue
        ev = json.loads(line)
        if ev.get("type") == "SourceCreated":
            out.append(ev)
    return out


def _differs(live, rebuilt):
    return {k for k in set(live) | set(rebuilt) if live.get(k) != rebuilt.get(k)}


def test_a_capture_over_a_pre_existing_stub_replays_its_ingest_instant(prov):
    """The issue's shortest reproduction, on the ``rebuild_all()`` engine.

    FAILS BEFORE: the whole-bag comparison reported the single diverging key
    ``{'ingestedAt'}`` (live = the stub mint's instant, rebuilt = the record's).
    """
    sdk, events, _log = prov
    session_id = "S1"
    url, stub_ingested_at = _mint_stub_via_create_point(sdk, session_id)

    res = sdk.capture_session(CONV, session_id=session_id)
    assert res["ok"] is True, res

    live = _bag(sdk, url)
    assert live.get("capturedAt") and live["capturedAt"] > stub_ingested_at, (
        "guard: the capture did not reach this Source at all, so this test is "
        f"not exercising the divergent arm ({live!r})")

    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
    rebuilt = _bag(sdk, url)
    assert rebuilt == live, (
        "a capture over a PRE-EXISTING session Source stub does not replay: "
        f"live != rebuilt on {_differs(live, rebuilt)}"
        f"\n  live    ingestedAt={live.get('ingestedAt')!r}"
        f"\n  rebuilt ingestedAt={rebuilt.get('ingestedAt')!r}")


def test_the_record_states_the_instant_the_write_stored(prov):
    """...and the record states the value the write STORED — not ``now``.

    This is the other half of the fix, and the reason it is NOT closed by
    overwriting ``ingestedAt`` live: #398's never-overwrite contract (the field
    means "when Tortoise FIRST saw this source") is what makes the live node keep
    the stub mint's instant, so a record carrying the capture's ``now`` journals
    a value that was never written.

    FAILS BEFORE: the live node held the stub's instant while the ``SourceCreated``
    payload carried ``capturedAt``'s value.
    """
    sdk, events, _log = prov
    session_id = "S2"
    url, stub_ingested_at = _mint_stub_via_create_point(sdk, session_id)

    res = sdk.capture_session(CONV, session_id=session_id)
    assert res["ok"] is True, res
    live = _bag(sdk, url)

    # The live node keeps the FIRST instant (#398): the capture did not overwrite
    # it, and that is not the defect — the record already stating `now` was.
    assert live["ingestedAt"] == stub_ingested_at, (
        "the capture overwrote the recorded ingest instant, reversing #398's "
        f"never-overwrite contract: stub={stub_ingested_at!r} "
        f"live={live['ingestedAt']!r}")
    assert live["capturedAt"] > stub_ingested_at, (
        f"guard: this must be a LATER writer ({live!r})")

    records = [r for r in _capture_records(events)
               if r.get("url") == url and r.get("_stub_completion") is True]
    assert records, "the capture emitted no SourceCreated record — nothing to replay"
    assert records[-1]["ingestedAt"] == live["ingestedAt"], (
        "the record does not state the instant the write STORED: "
        f"record={records[-1]['ingestedAt']!r} live={live['ingestedAt']!r} "
        f"capturedAt={live['capturedAt']!r}")

    # ... and it survives the replay as that same value, on the engine whose fold
    # CREATES the node from the record.
    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
    assert _bag(sdk, url)["ingestedAt"] == stub_ingested_at, (
        "the rebuilt node did not hold the recorded instant: "
        f"rebuilt={_bag(sdk, url)['ingestedAt']!r} stub={stub_ingested_at!r}")


def test_a_replay_that_remints_the_stub_first_lands_the_recorded_instant(prov):
    """The ``rebuild()`` engine — record by record, so the stub exists BEFORE the
    ``SourceCreated`` folds and ``_upsert_source``'s **ON MATCH** arm is the one
    that must apply the recorded instant (``rebuild_all()`` never reaches it: it
    folds the record before pass 2 mints the stub).

    FAILS IF the ON MATCH arm has no ``ingestedAt`` clause (the pre-#9451 tree):
    the re-minted stub's instant is the REPLAY clock, so the rebuilt node carries
    that instead of the recorded one and the whole-bag comparison diverges.
    """
    sdk, _events, log_path = prov
    session_id = "S3"
    url, stub_ingested_at = _mint_stub_via_create_point(sdk, session_id)

    res = sdk.capture_session(CONV, session_id=session_id)
    assert res["ok"] is True, res
    live = _bag(sdk, url)

    sdk._get_proj().rebuild(EventLog(log_path), confirm_destructive=True)
    rebuilt = _bag(sdk, url)
    assert rebuilt == live, (
        "a record-by-record replay (the ON MATCH arm) does not reproduce the "
        f"capture over a pre-existing stub: live != rebuilt on "
        f"{_differs(live, rebuilt)}"
        f"\n  live    ingestedAt={live.get('ingestedAt')!r}"
        f"\n  rebuilt ingestedAt={rebuilt.get('ingestedAt')!r}")
    assert rebuilt["ingestedAt"] == stub_ingested_at, (
        "the replay re-minted the stub at the REPLAY clock and never applied the "
        f"recorded instant: rebuilt={rebuilt['ingestedAt']!r} "
        f"recorded={stub_ingested_at!r}")


def test_two_captures_over_one_stub_replay_in_journal_order(prov):
    """Two captures over the SAME pre-existing stub, then BOTH engines.

    Pins that the recorded instant is per-record and lands in journal order: the
    second record states the same stored instant (the first one, per #398) on the
    ``rebuild_all()`` engine, and the ON MATCH arm does not let the earlier fold
    win on ``rebuild()``.
    """
    sdk, events, log_path = prov
    session_id = "S4"
    url, stub_ingested_at = _mint_stub_via_create_point(sdk, session_id)

    res = sdk.capture_session(CONV, session_id=session_id)
    assert res["ok"] is True, res
    # A SECOND materialization of the same session (the re-materialisation door
    # `tests/test_5097_capture_source_replay.py` drives directly — a second
    # `capture_session` short-circuits on "already captured" and journals
    # nothing, so it could not put two records over one stub).
    sdk._materialize_session_source(session_id, None, LATER_NOW, CONV_2)
    live = _bag(sdk, url)
    assert len([r for r in _capture_records(events)
                if r.get("url") == url]) == 2, (
        "guard: both materializations must journal a SourceCreated record")

    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
    assert _bag(sdk, url) == live, (
        "two captures over one pre-existing stub do not replay: "
        f"{_differs(live, _bag(sdk, url))}")

    sdk._get_proj().rebuild(EventLog(log_path), confirm_destructive=True)
    assert _bag(sdk, url) == live, (
        "two captures over one pre-existing stub do not replay record-by-record: "
        f"{_differs(live, _bag(sdk, url))}")
    assert live["ingestedAt"] == stub_ingested_at


def test_a_blank_stored_instant_is_journaled_as_blank_not_as_the_capture_clock(prov):
    """Review round 2, P2: the read-back must distinguish FALSY from NULL.

    A ``:Source`` holding ``ingestedAt=''`` is falsy but NON-NULL, and the live
    SET's ``coalesce`` KEEPS it — so a read-back written as ``... or now`` would
    journal the capture clock for a value the write did not store, and
    ``rebuild_all()`` would create a node carrying that clock against a live node
    holding ``''``: the divergence class this issue closes, on one state.

    FAILS ON A `or now` READ-BACK (measured: rebuilt = the capture instant,
    live = ''), and FAILS IF a future change starts treating '' as absent on
    either side.
    """
    sdk, events, _log = prov
    session_id = "S5"
    url, _stub_ingested_at = _mint_stub_via_create_point(sdk, session_id)
    # The synthetic state: a legacy/foreign writer stamped a blank instant.
    sdk._get_proj().g.query(
        "MATCH (s:Source {url:$u}) SET s.ingestedAt = ''", params={"u": url})

    res = sdk.capture_session(CONV, session_id=session_id)
    assert res["ok"] is True, res
    live = _bag(sdk, url)
    assert live["ingestedAt"] == "", (
        "guard: the live coalesce must KEEP a non-null blank instant — "
        f"{live['ingestedAt']!r}")

    records = [r for r in _capture_records(events)
               if r.get("url") == url and r.get("_stub_completion") is True]
    assert records, "the capture emitted no SourceCreated record"
    assert records[-1]["ingestedAt"] == "", (
        "the record states an instant the write did not store: "
        f"record={records[-1]['ingestedAt']!r} live={live['ingestedAt']!r}")

    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
    rebuilt = _bag(sdk, url)
    assert rebuilt == live, (
        "a blank stored instant does not replay: "
        f"{_differs(live, rebuilt)}"
        f"\n  live    ingestedAt={live.get('ingestedAt')!r}"
        f"\n  rebuilt ingestedAt={rebuilt.get('ingestedAt')!r}")


def test_a_legacy_capture_record_does_not_move_a_journalled_instant(prov):
    """#9451 review round 3, ISSUE 2 — a PRE-#9451 capture line must stay the
    NO-OP it was, on both engines.

    The shape: the node's instant is **journaled** by an earlier writer
    (`create_source("session:<id>")`, the hosted commit path's own door — its
    `SourceCreated` carries T0), and a capture written BEFORE this fix then
    completed that node. That legacy line states `now` — the capture clock,
    which the live write never stored, because `coalesce(s.ingestedAt, $now)`
    keeps T0 — and it carries `_stub_completion=True` like every capture
    completion record.

    Pre-fix, both engines replayed it FAITHFULLY (the ON CREATE arm took T0 from
    the journaled creator, the ON MATCH arm had no `ingestedAt` clause at all),
    so live == replay held. A clause gated on `$stub_completion` ALONE would
    apply the never-stored `now` here and MANUFACTURE the divergence this issue
    exists to remove. That is why the fold is also gated on the writer's own
    ``_ingestedAtStored`` marker: a legacy line has no marker and stays inert.

    FAILS ON A `$stub_completion`-ONLY GATE (measured: both engines land the
    legacy record's `2099-01-01` against a live T0).

    ⚠️ Only `ingestedAt` is compared, not the whole bag: a legacy capture line
    carries other payload keys the live write of THIS version derives
    differently (`updatedAt`, `summary`, …), which is a separate, documented
    legacy residual — not the field this issue owns.
    """
    sdk, events, log_path = prov
    url = "session:S6"
    # 1. A JOURNALLED creator: T0 is in the journal, so replay can reproduce it.
    sdk.create_source(url, "agentSession")
    logged_t0 = _bag(sdk, url)["ingestedAt"]
    assert logged_t0, "guard: create_source journaled no ingest instant"

    # 2. A PRE-#9451 capture-completion line: `_stub_completion` set, NO
    #    `_ingestedAtStored` marker, `ingestedAt` = the capture's clock (a value
    #    the live coalesce never stored). Emitted through the real journal
    #    writer, which is how the pre-fix writer wrote it.
    legacy_now = LATER_NOW
    sdk._emit_event(
        "SourceCreated", id=url, url=url,
        sourceKind="agentSession", ingestedAt=legacy_now,
        updatedAt=legacy_now, capturedAt=legacy_now,
        _stub_completion=True,
    )
    legacy = [r for r in _capture_records(events)
              if r.get("url") == url and r.get("_stub_completion") is True]
    assert legacy, "guard: the legacy line did not reach the journal"
    assert "_ingestedAtStored" not in legacy[-1], (
        "guard: this line must look PRE-#9451 (no stored-instant marker)")

    live = _bag(sdk, url)
    assert live["ingestedAt"] == logged_t0, (
        "the harness is not modelling the legacy shape: appending a journal "
        f"record moved the live node ({live['ingestedAt']!r})")

    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
    assert _bag(sdk, url)["ingestedAt"] == logged_t0, (
        "a PRE-#9451 capture line MOVED the rebuilt node's ingest instant on "
        f"rebuild_all(): rebuilt={_bag(sdk, url)['ingestedAt']!r} "
        f"live={logged_t0!r} legacy_record={legacy_now!r}")

    sdk._get_proj().rebuild(EventLog(log_path), confirm_destructive=True)
    assert _bag(sdk, url)["ingestedAt"] == logged_t0, (
        "a PRE-#9451 capture line MOVED the rebuilt node's ingest instant on "
        f"rebuild(): rebuilt={_bag(sdk, url)['ingestedAt']!r} "
        f"live={logged_t0!r} legacy_record={legacy_now!r}")
