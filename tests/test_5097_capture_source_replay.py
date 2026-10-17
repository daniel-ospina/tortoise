"""Lane L5 (#5097) gate item 2 — the capture path's session `:Source` replays.

`derived = replay(journal)` is the lane's integration check, because
*individually-correct edits to a shared writer* can still leave a replay
non-faithful. `SDKTortoise._materialize_session_source` is exactly such a
writer: a RAW Cypher MERGE on the capture path that stores the session
`:Source`'s derived metadata (``summary``/``topics``/``contentHash``/
``_searchText``/``sessionId``/``capturedAt``/``eventId``) — every one of them
computed from the #4911-REDACTED transcript, i.e. this lane's own output.

Before the fix it had NO journal carrier, so a rebuild silently reverted the
whole block:

    capture_session(...) -> rebuild()
    summary  "We decided ..."           -> None
    topics   ['decided', 'weekly', ...] -> None
    contentHash <sha of transcript>     -> ''
    sessionId / capturedAt / eventId    -> None
    _searchText                         -> None

(measured on the embedded lane, one 3-turn capture). The lane's own rule for
this was already recorded a few hundred lines up in ``sdk`` — a raw graph SET
on the capture path must emit a trailing record "under the same gate as the
write" — and ``_write_capture_turns``'s ``capture_redactions`` note is that
precedent. This suite pins the carrier and its payload:

  * the record EXISTS for ``session:<id>`` (no carrier => nothing replays);
  * it carries the REDACTED metadata, never the raw turn text (#4911 runs
    BEFORE durability — a credential that reaches the journal is not
    retractable);
  * a rebuild reproduces the stored metadata.

NOT pinned here, deliberately (recorded rather than implied): ``ingestedAt``
on a rebuilt session Source is still the replay clock — the stub this write
completes is created by the M2 ``_link_source`` projection write and
``_upsert_source``'s ON MATCH arm has no ``ingestedAt`` clause. Same
unjournaled-writer class, one writer upstream. Also outside this slice: the
``references`` (Source→Event) and ``CONTAINS`` (Session→derived Point) edges,
which have no journal carrier at all (the "no carrier for edges" class the
unjournaled-mutation suite cites and does not close).

Offline: ``TORTOISE_SESSION_LLM_MOCK=1`` installs the deterministic MockModel
extractor — zero network, zero provider spend.
"""
from __future__ import annotations

import json

import pytest

from tortoise.log import EventLog
from tortoise.sdk import TortoiseSDK

SESSION_ID = "sess_5097_source_replay"
SECRET = "ghp_" + "A" * 36
CONV = [
    # The credential is in the FIRST turn on purpose: `summary` is the first
    # substantive utterance and `topics` are the most frequent content words,
    # so a carrier built from RAW text would put the secret in both.
    {"role": "user", "content": f"Use this key {SECRET} for the weekly rebuild."},
    {"role": "assistant", "content": "Agreed, the weekly job is fine."},
    {"role": "user", "content": "Also rotate the staging key tomorrow."},
]

#: The metadata the capture write derives from the REDACTED transcript.
DERIVED_KEYS = (
    "summary", "topics", "contentHash", "_searchText",
    "sessionId", "capturedAt", "eventId", "sourceKind",
)
SOURCE_URL = f"session:{SESSION_ID}"


@pytest.fixture(autouse=True)
def _offline_extractor(monkeypatch):
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")


@pytest.fixture()
def captured(tmp_path):
    log_path = str(tmp_path / "events" / "sdk.jsonl")
    sdk = TortoiseSDK(str(tmp_path / "tortoise.db"), event_log_path=log_path)
    res = sdk.capture_session(CONV, session_id=SESSION_ID)
    assert res["ok"] is True and res["turns"] == 3
    yield sdk, log_path
    sdk.close()


def _source_props(proj, url):
    rows = proj.g.query(
        "MATCH (s:Source {url: $url}) RETURN properties(s)",
        params={"url": url},
    ).result_set
    assert rows, f"no :Source at {url}"
    return rows[0][0] or {}


def _source_records(log_path):
    with open(log_path, encoding="utf-8") as fh:
        return [r for r in map(json.loads, (ln for ln in fh if ln.strip()))
                if r.get("type") == "SourceCreated"]


def _carrier(log_path):
    recs = [r for r in _source_records(log_path) if r.get("url") == SOURCE_URL]
    assert recs, (
        "the session :Source materialization emitted no journal record — a raw "
        "capture-path SET with no carrier is silently reverted by a rebuild")
    return recs[-1]


def test_the_capture_source_write_has_a_journal_carrier(captured):
    """No carrier => `rebuild()` reverts the block (the pre-fix behaviour)."""
    _sdk, log_path = captured
    record = _carrier(log_path)
    missing = [k for k in DERIVED_KEYS if record.get(k) is None]
    assert not missing, (
        f"the carrier omits {missing}, so a replay cannot restore them")
    # `title` rides the record as an explicit null so the fold cannot default it
    # to the MERGE key (a value live never sets) — see `_upsert_source`.
    assert "title" in record and record["title"] is None


def test_the_carrier_holds_the_redacted_text_not_the_raw_turn(captured):
    """#4911 runs BEFORE durability: the journal must not hold the credential."""
    sdk, log_path = captured
    record = _carrier(log_path)
    for key in ("summary", "topics", "_searchText"):
        value = record.get(key)
        text = " ".join(map(str, value)) if isinstance(value, list) else str(value)
        assert SECRET not in text, f"{key!r} carried the credential into the journal"
        assert "ghp_" not in text, f"{key!r} carried a credential-shaped run"
    # Positive half: the carrier is the REDACTED derivation, not a copy of the
    # raw input — the redaction marker is visible where the secret was.
    assert "[REDACTED:github_token]" in (record.get("summary") or ""), (
        "the carrier's summary is not the redacted transcript's first utterance")
    # And it describes the SAME bytes the node stores (the #4911 rule: the hash
    # is taken over the redacted transcript).
    stored = _source_props(sdk._get_proj(), SOURCE_URL)
    assert record["contentHash"] == stored["contentHash"]
    assert SECRET not in str(stored.get("_searchText") or "")


def test_rebuild_restores_the_session_source_metadata(captured):
    """Gate item 2: `derived == replay(journal)` for the capture `:Source`."""
    sdk, log_path = captured
    proj = sdk._get_proj()
    before = _source_props(proj, SOURCE_URL)
    for key in DERIVED_KEYS:
        assert before.get(key) not in (None, "", [], {}), (
            f"precondition: live {key!r} must be populated")

    proj.rebuild(EventLog(log_path), confirm_destructive=True)

    after = _source_props(proj, SOURCE_URL)
    lost = {k: (before.get(k), after.get(k))
            for k in DERIVED_KEYS if before.get(k) != after.get(k)}
    assert not lost, f"a rebuild reverted the session :Source metadata: {lost}"
    # The redaction survives the round trip too — a rebuild must not resurrect
    # the credential from the journal.
    assert SECRET not in str(after.get("summary") or "")
