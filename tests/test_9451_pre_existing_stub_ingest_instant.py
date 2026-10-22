"""#9451 — a capture onto a PRE-EXISTING ``session:<id>`` Source stub must leave
``ingestedAt`` equal to what ``rebuild_all()`` re-folds from the journal.

The defect this pins: ``_materialize_session_source`` writes its ingest instant
as ``s.ingestedAt = coalesce(s.ingestedAt, $now)``, i.e. it keeps whatever the
node already holds when the stub already exists — while the ``SourceCreated``
record it emits carries THIS capture's instant. ``_upsert_source``'s ON CREATE
arm reads the recorded value (#5024) but its ON MATCH arm had no ``ingestedAt``
clause at all, so a rebuild that folds the record onto an existing node left
whichever instant the earlier writer stamped. ``derived == replay(journal)``
(#5024 / #5097 / #8501) was therefore FALSE for ``ingestedAt`` on exactly the
arm where the stub pre-exists.

The stub pre-exists whenever another door minted the ref first — here the
shortest reproduction from the issue: ``create_point(..., session_id="S1")``
infers ``extractedFrom="session:S1"`` → ``_link_source`` → ``_mint_source_stub``
with the clock at ITS write, which is never journaled as an instant.

Both assertions compare the WHOLE property bag, not only ``ingestedAt``: a
neighbouring property cannot hide a second divergence behind the field pin.

FAILS BEFORE THE FIX (measured: live ``...423698`` vs rebuilt ``...437614`` on
the issue's lane; this test reproduces the same shape with a deterministic
clock), and FAILS IF the live writer stops applying the capture instant while
the fold applies it — the two halves must move together.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise.sdk import TortoiseSDK

CONV = [
    {"role": "user", "content": "I think the auth dead-end is the top issue. "
                                "We decided to ship serve --http first."},
    {"role": "assistant", "content": "Agreed. Evidence suggests the website "
                                     "config is the root cause."},
    {"role": "user", "content": "ok"},
]


@pytest.fixture()
def prov(tmp_path, monkeypatch):
    """(sdk, events_dir) — one journal-backed embedded SDK, offline extractor."""
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    monkeypatch.delenv("TORTOISE_SESSION_EXTRACTOR", raising=False)
    db = tmp_path / "t.db"
    events = tmp_path / "events"
    events.mkdir()
    sdk = TortoiseSDK(str(db), event_log_path=str(events / "events.jsonl"))
    yield sdk, events
    sdk.close()


def _bag(sdk, url):
    rows = sdk._get_proj().g.query(
        "MATCH (s:Source {url:$u}) RETURN properties(s)",
        params={"u": url}).result_set
    assert rows, f"no session Source {url}"
    assert len(rows) == 1, f"{len(rows)} :Source nodes for {url} — duplicate"
    # `dict(...)`: `properties(s)` comes back as an OrderedDict, whose `==`
    # is ORDER-sensitive — a replay can rebuild the same bag in another order.
    return dict(rows[0][0])


def _mint_stub_via_create_point(sdk, session_id):
    """Mint the ``session:<id>`` stub through a NON-capture door.

    ``create_point`` with ``session_id`` infers ``extractedFrom="session:<id>"``,
    which is enough to run ``_link_source`` → ``_mint_source_stub`` — the
    writer whose ``ingestedAt`` choice is never journaled.
    """
    sdk.create_point("observation", "the earlier point, linked to the session",
                     session_id=session_id)
    url = f"session:{session_id}"
    rows = sdk._get_proj().g.query(
        "MATCH (s:Source {url:$u}) RETURN s.ingestedAt, s.contentHash",
        params={"u": url}).result_set
    assert rows, f"create_point(session_id=...) minted no stub at {url}"
    return url, rows[0]


def test_a_capture_over_a_pre_existing_stub_replays_its_ingest_instant(prov):
    """The issue's shortest reproduction.

    FAILS BEFORE: live kept the create_point stub's instant while the rebuild
    folded the capture's recorded instant onto it (whole-bag inequality).
    """
    sdk, events = prov
    session_id = "S1"
    url, (stub_ingested_at, _hash) = _mint_stub_via_create_point(sdk, session_id)

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
        f"live != rebuilt. Diverging keys: "
        f"{ {k for k in set(live) | set(rebuilt) if live.get(k) != rebuilt.get(k)} }"
        f"\n  live    ingestedAt={live.get('ingestedAt')!r}"
        f"\n  rebuilt ingestedAt={rebuilt.get('ingestedAt')!r}")


def test_the_capture_instant_wins_over_the_stub_writers_instant(prov):
    """...and it is the CAPTURE's instant that both sides hold — not merely
    "some value that happens to agree".

    FAILS IF the divergence is closed by making the fold keep the earlier
    instant instead (i.e. ``ON MATCH SET s.ingestedAt = s.ingestedAt``): the
    capture is the write that actually ingested the transcript, and the
    ``SourceCreated`` record it emits carries its own ``now``.
    """
    sdk, events = prov
    session_id = "S2"
    url, (stub_ingested_at, _hash) = _mint_stub_via_create_point(sdk, session_id)

    res = sdk.capture_session(CONV, session_id=session_id)
    assert res["ok"] is True, res

    live = _bag(sdk, url)
    assert live["ingestedAt"] == live["capturedAt"], (
        "the session Source's ingest instant is not the capture's own: "
        f"ingestedAt={live['ingestedAt']!r} capturedAt={live['capturedAt']!r}"
    )
    assert live["ingestedAt"] > stub_ingested_at, (
        "the capture's own instant must supersede the stub mint's clock read: "
        f"stub={stub_ingested_at!r} ingestedAt={live['ingestedAt']!r}")

    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
    assert _bag(sdk, url)["ingestedAt"] == live["ingestedAt"], (
        "the rebuilt node did not land the capture's recorded instant: live="
        f"{live['ingestedAt']!r} rebuilt={_bag(sdk, url)['ingestedAt']!r}")
