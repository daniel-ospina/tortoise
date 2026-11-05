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
