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

Both REPLAY ENGINES are pinned, because they fold this record in different
orders and only one of them is the recovery path: ``rebuild()`` replays record
by record, so the M2 stub exists before the record folds (ON MATCH);
``rebuild_all()`` folds ``SourceCreated`` in pass 1b and mints the stub in
pass 2, so the record itself CREATES the node (ON CREATE). A carrier that is
faithful only on ``rebuild()`` is half a carrier — hence
``test_rebuild_all_restores_the_session_source_metadata``, which compares the
WHOLE property bag, not a chosen subset.

NOT pinned as faithful here, deliberately (recorded rather than implied):
``ingestedAt`` on a rebuilt session Source is still the replay clock — the stub
this write completes is created by the M2 ``_link_source`` projection write and
``_upsert_source``'s ON MATCH arm has no ``ingestedAt`` clause. Same
unjournaled-writer class, one writer upstream; it is the ONE key the whole-bag
comparison excludes (``REPLAY_RESIDUAL_KEYS``) and nothing else may join it
silently. Also outside this slice: the ``references`` (Source→Event) and
``CONTAINS`` (Session→derived Point) edges, which have no journal carrier at
all (the "no carrier for edges" class the unjournaled-mutation suite cites and
does not close).

The carrier is also a NO-OP for the two properties this writer must neither
move nor destroy: it carries the node's own ``validFrom``/``is_episodic`` (so
the fold's hash-diff ``s.validFrom = $vf`` cannot REMOVE a window) and its
record declares itself a STUB COMPLETION (``_stub_completion``), which
suppresses the ON CREATE creator-only defaults (``id``/``version``/
``externalId``) and gates the ON MATCH ``version + 1`` — the live write never
touches ``version`` on either arm, so neither may a replay. That discriminator
is server-managed: it is refused by ``create_source``'s ``**props`` guard, by
``_sanitize_props`` and by the MCP ``_SERVER_MANAGED_PROPS`` boundary, so the
capture writer is its only producer (a caller-settable one forged capture
provenance AND stripped a tenant node's identity props). It is dropped from the
RECORD layer too: ``record_projection._RECORD_ONLY_KEYS`` keeps it out of the
``source.props`` jsonb tail that ``cache_projection`` copies back onto the node
(pinned in ``tests/test_record_projection.py`` — the third home of a
``_META_KEYS`` transport key, next to ``contains_session``).

Offline: ``TORTOISE_SESSION_LLM_MOCK=1`` installs the deterministic MockModel
extractor — zero network, zero provider spend.
"""
from __future__ import annotations

import json
import os

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

#: The metadata the capture write derives from the REDACTED transcript, PLUS
#: every node property a replay must also not destroy (`title`/`is_episodic` are
#: the M2 stub's, `updatedAt` this writer's). `version`/`externalId`/`id` are
#: deliberately absent from this tuple but present in the WHOLE-BAG comparison
#: below: they are the ON CREATE arm's creator-only defaults, which the record
#: suppresses (`_stub_completion`) — asserting their absence in the bag is what
#: pins that, and adding them here would make the precondition assert a
#: `version` the live node does not have.
DERIVED_KEYS = (
    "summary", "topics", "contentHash", "_searchText",
    "sessionId", "capturedAt", "eventId", "sourceKind",
    "updatedAt", "title", "is_episodic",
)
#: The one key a rebuilt node is still allowed to differ on (see the module
#: docstring). Kept as a named constant so a second residual cannot slip into
#: the comparison unremarked.
REPLAY_RESIDUAL_KEYS = ("ingestedAt",)
SOURCE_URL = f"session:{SESSION_ID}"
SECOND_NOW = "2099-01-01T00:00:00+00:00"


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


def _bag_diff(before, after):
    """Every property that differs between the live and the rebuilt node.

    WHOLE-BAG, not a chosen subset: the earlier form of this suite compared an
    eight-key tuple, so a replay that ADDED `id`/`version=1`/`externalId` (the
    ON CREATE arm's creator-only defaults) or REMOVED `title`/`is_episodic`
    passed green over a real live != replay divergence (#5097 review P1).
    """
    keys = (set(before) | set(after)) - set(REPLAY_RESIDUAL_KEYS)
    return {k: (before.get(k), after.get(k)) for k in sorted(keys)
            if before.get(k) != after.get(k)}


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
    # `title` is excluded on purpose: the carrier sends it as an EXPLICIT null so
    # the fold can (a) keep the node's title on ON MATCH and (b) reproduce the
    # stub's `title=url` on ON CREATE — both asserted below.
    missing = [k for k in DERIVED_KEYS if k != "title" and record.get(k) is None]
    assert not missing, (
        f"the carrier omits {missing}, so a replay cannot restore them")
    # The two properties this writer must not DESTROY are carried from the node.
    assert record.get("is_episodic") is True, (
        "the carrier must carry the M2 stub's `is_episodic` — a rebuild that "
        "loses it re-opens the #1486 episodic backfill")
    assert "validFrom" in record, (
        "`validFrom` must be carried (null when the node has none): the fold's "
        "hash-diff arm writes `s.validFrom = $vf`, so an ABSENT key clears a "
        "window the node holds")
    # `title` rides the record as an explicit null so the fold cannot default it
    # to the MERGE key (a value live never sets) — see `_upsert_source`.
    assert "title" in record and record["title"] is None
    # And the record declares itself a STUB COMPLETION, which is what suppresses
    # the ON CREATE arm's creator-only defaults (`id`/`version`/`externalId`) and
    # admits this writer's own prop spellings.
    assert record.get("_stub_completion") is True


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
    lost = _bag_diff(before, after)
    assert not lost, (
        f"a rebuild diverged the session :Source from the live node: {lost}")
    # The redaction survives the round trip too — a rebuild must not resurrect
    # the credential from the journal.
    assert SECRET not in str(after.get("summary") or "")


def test_rebuild_all_restores_the_session_source_metadata(captured):
    """Gate item 2 on the RECOVERY engine, not only on `rebuild()`.

    `rebuild_all` folds `SourceCreated` in pass 1b and mints the M2 stub in
    pass 2, so `_upsert_source` takes its ON CREATE arm — where the creator-only
    defaults (`id`, `version=1`, `externalId=''`) live and where `title` /
    `is_episodic` / `updatedAt` had no clause at all. Measured before this was
    fixed: `title` ('session:sess_…', None), `is_episodic` (True, None),
    `updatedAt` (set, None), `id` (None, 'session:sess_…'), `version` (None, 1) —
    i.e. the recovery path LOST two properties it had right before the carrier
    existed and INVENTED two the live node never had.
    """
    sdk, log_path = captured
    proj = sdk._get_proj()
    before = _source_props(proj, SOURCE_URL)
    for key in DERIVED_KEYS:
        assert before.get(key) not in (None, "", [], {}), (
            f"precondition: live {key!r} must be populated")

    proj.rebuild_all(str(os.path.dirname(log_path)), confirm_destructive=True)

    after = _source_props(proj, SOURCE_URL)
    diverged = _bag_diff(before, after)
    assert not diverged, (
        "the CLI/recovery replay engine diverged the session :Source from the "
        f"live node: {diverged}")


def test_a_second_materialization_with_a_changed_transcript_moves_updatedAt(
        captured):
    """Review P2: live never moved `updatedAt`; the carrier always carried it.

    A second materialization of the same `session:<id>` whose transcript (and so
    `contentHash`) differs is reachable through a TRUE retry (#2335 WI-2b) and
    the documented #1727 concurrent-fresh race. Live used a bare `coalesce`
    (first stamp forever) while `_upsert_source`'s hash-diff arm takes the
    RECORD's instant — so a rebuild moved the node to t2 where live stayed on
    t1, and `memory_orchestrator` then sorted the session Source by a different
    capture.

    Fails on the pre-fix head twice over: `live["updatedAt"]` stays at the first
    capture's instant, and the rebuilt value disagrees with it.
    """
    sdk, log_path = captured
    proj = sdk._get_proj()
    first = _source_props(proj, SOURCE_URL)
    assert first["updatedAt"], "precondition: the first capture stamped it"

    second_conv = [*CONV, {"role": "user", "content": "One more turn."}]
    sdk._materialize_session_source(SESSION_ID, None, SECOND_NOW, second_conv)

    live = _source_props(proj, SOURCE_URL)
    assert live["contentHash"] != first["contentHash"], (
        "precondition: the second transcript must hash differently")
    assert live["updatedAt"] == SECOND_NOW, (
        "a hash-changing re-materialization is a version transition, and live "
        f"kept the FIRST stamp ({live['updatedAt']!r}) — the fold's hash-diff "
        "arm takes the record's, so the two halves disagree")

    proj.rebuild(EventLog(log_path), confirm_destructive=True)
    after = _source_props(proj, SOURCE_URL)
    assert after["updatedAt"] == live["updatedAt"], (
        f"replayed updatedAt {after['updatedAt']!r} != live {live['updatedAt']!r}")


def test_an_empty_transcript_capture_replays_the_stamp_live_set(tmp_path):
    """Review ISSUE 1 (head f16638e6b): the ELSE arms of `updatedAt` disagreed.

    The live clause stamps the update instant when the node has NONE
    (``ELSE coalesce(s.updatedAt, $now)``), while `_upsert_source`'s ON MATCH
    ELSE arm kept the absence (``ELSE s.updatedAt``). The two only part company
    when the incoming hash EQUALS the stored one AND the node carries no
    `updatedAt` — exactly the M2 stub (``contentHash=''``, no `updatedAt`, see
    `_mint_source_stub`) receiving a capture whose derived transcript is EMPTY
    (``content_hash`` is `''` precisely then), i.e. a session that already has
    derived points referencing it and is then captured with nothing to say.

    Fails on the head it was found on: live has an `updatedAt`, `rebuild()` has
    none — the recovery path LOSING a value the write stored, the class this
    lane exists to remove.
    """
    log_path = str(tmp_path / "events" / "sdk.jsonl")
    sdk = TortoiseSDK(str(tmp_path / "tortoise.db"), event_log_path=log_path)
    try:
        url = f"session:{SESSION_ID}"
        # The M2 stub: `contentHash=''` and NO `updatedAt`.
        sdk.create_point("statement", "hello", extractedFrom=url)
        stub = _source_props(sdk._get_proj(), url)
        assert stub.get("contentHash") == "", (
            "precondition: the stub carries the empty hash")
        assert stub.get("updatedAt") is None, (
            "precondition: the stub carries no update instant")

        # The writer itself, with an EMPTY conversation: `content_hash` is `''`
        # exactly when the derived transcript has no non-space text
        # (`sdk._materialize_session_source`), i.e. the same hash the stub
        # carries, so the live CASE takes its ELSE arm.
        sdk._materialize_session_source(SESSION_ID, None, SECOND_NOW, [])
        live = _source_props(sdk._get_proj(), url)
        assert live.get("contentHash") == "", (
            "precondition: an empty transcript hashes to '' — the SAME hash the "
            "stub carries, so the write takes the ELSE arm")
        assert live.get("updatedAt"), (
            "precondition: live stamps the instant when the node has none")

        sdk._get_proj().rebuild(EventLog(log_path), confirm_destructive=True)
        after = _source_props(sdk._get_proj(), url)
        diverged = _bag_diff(live, after)
        assert not diverged, (
            "an empty-transcript capture was not reproduced by the replay — the "
            "fold's ELSE arm kept an ABSENCE the live write filled: "
            f"{diverged}")
        assert after.get("updatedAt") == live["updatedAt"], (
            f"replayed updatedAt {after.get('updatedAt')!r} != live "
            f"{live['updatedAt']!r}")
    finally:
        sdk.close()


def test_the_carrier_does_not_clear_a_version_window_the_node_had(tmp_path):
    """Review P2 + review P1: the carrier must not CLEAR or ADVANCE a version.

    Two defects, one state (a session `:Source` that already carried a version
    window, i.e. one a caller created with `create_source` before capturing):

    * `_upsert_source`'s hash-diff arm writes `s.validFrom = $vf` — deliberately
      NOT `coalesce($vf, s.validFrom)`, so a record carrying no window REMOVES
      the property. The carrier therefore CLEARED a RECORDED window
      (`docs/durability-posture.md`) that a replay restored before it existed.
    * the same hash-diff arm also ran `s.version = s.version + 1`, while the
      capture writer's live raw MERGE never touches `s.version` — so a replay
      ADVANCED the ordinal (live 1, rebuilt 2), the exact class the ON CREATE
      suppression exists to stop, one arm over.

    The fix carries the node's own `validFrom` and gates the ON MATCH bump on
    `$stub_completion`, so the fold is a no-op on both fields. This compares the
    WHOLE property bag (`_bag_diff`), because the earlier form asserted two
    chosen keys and stepped over the `version` divergence green.
    """
    log_path = str(tmp_path / "events" / "sdk.jsonl")
    sdk = TortoiseSDK(str(tmp_path / "tortoise.db"), event_log_path=log_path)
    try:
        url = f"session:{SESSION_ID}"
        sdk.create_source(url, "agentSession", contentHash="h-1",
                          title="pinned window")
        proj = sdk._get_proj()
        before = _source_props(proj, url)
        assert before["validFrom"], "precondition: create_source minted a window"
        assert before.get("version") == 1, (
            "precondition: create_source mints the first version ordinal")

        sdk._materialize_session_source(SESSION_ID, None, SECOND_NOW, CONV)
        live = _source_props(proj, url)
        assert live["validFrom"] == before["validFrom"], (
            "the capture writer must not move the version window")
        assert live.get("version") == before.get("version"), (
            "the capture writer must not advance the version ordinal")

        proj.rebuild(EventLog(log_path), confirm_destructive=True)
        after = _source_props(proj, url)
        assert after["validFrom"] == before["validFrom"], (
            "a replay CLEARED the version window the node holds — the carrier "
            f"omitted `validFrom` (live {before['validFrom']!r}, rebuilt "
            f"{after.get('validFrom')!r})")
        assert after.get("title") == before["title"], (
            "the carrier's explicit null title must PRESERVE the node's title")
        # WHOLE bag, minus the ONE declared residual (`ingestedAt`, see the
        # module docstring): a replay that advances `version` is a live != replay
        # divergence on a declared property, not a detail.
        diverged = _bag_diff(live, after)
        assert not diverged, (
            "a replay diverged the versioned session :Source from the live node: "
            f"{diverged}")
    finally:
        sdk.close()


def test_the_capture_props_stay_unsettable_through_create_source(tmp_path):
    """Review ISSUE 3 / the P3 note: the widening was on the CALLER surface.

    `_SOURCE_EXTRA_PROPS` is not only the replay allowlist — `create_source(**
    props)` rides the same passthrough. Adding the capture writer's spellings
    there made `create_source(url, k, sessionId=…, capturedAt=…, eventId=…)`
    persist provenance that was DENIED with a #3998 warning before, and let one
    call carry BOTH spellings of one fact (`session_id` is caller-settable).
    They now live in `_SOURCE_CAPTURE_REPLAY_PROPS`, admitted only for a record
    that declares itself a capture completion.

    Non-vacuity on `main`: the camelCase half passes there too (those keys were
    never in `_SOURCE_EXTRA_PROPS` on main — the intermediate revision of THIS
    PR widened them); what main accepts and this suite catches in that half is
    `update_entity(sessionId=…)`, which `_SOURCE_SERVER_MANAGED_PROPS` now
    refuses.
    """
    sdk = TortoiseSDK(str(tmp_path / "tortoise.db"),
                      event_log_path=str(tmp_path / "events" / "sdk.jsonl"))
    try:
        url = "session:caller-5097"
        sdk.create_source(url, "agentSession", contentHash="h-1",
                          session_id="s-1", sessionId="forged",
                          capturedAt=SECOND_NOW, eventId="forged-event")
        props = _source_props(sdk._get_proj(), url)
        leaked = sorted(k for k in ("sessionId", "capturedAt", "eventId")
                        if k in props)
        assert not leaked, (
            f"create_source() persisted the capture-only props {leaked} — the "
            "replay allowlist is not the caller allowlist")
        assert props.get("session_id") == "s-1"
        # The OTHER caller door: the generic tenant route (`_update_entity`),
        # which uses the whole read declaration as its write allowlist.
        for key in ("sessionId", "capturedAt", "eventId"):
            with pytest.raises(ValueError, match="server-managed"):
                sdk.update_entity(url, **{key: "forged"})
    finally:
        sdk.close()


def test_the_stub_completion_discriminator_is_not_caller_settable(tmp_path):
    """Review P1: the widening's DISCRIMINATOR rode in through `**props`.

    `_upsert_source` reads `_stub_completion` as a parameter, and it selects
    write behaviour twice: it suppresses the ON CREATE creator-only defaults
    (`id`/`version`/`externalId`) and it admits `_SOURCE_CAPTURE_REPLAY_PROPS`.
    `create_source` builds `ev = {..., **props}` and bypasses
    `_sanitize_props` (`_skip_sanitize=True`), so an unlisted key reached it and
    one call could forge capture provenance AND strip a tenant node's identity
    props — then ride the journal, so a rebuild restored both.

    Fails on the review head in three places: `create_source` ACCEPTED the key
    (and persisted all three props), the generic props surfaces accepted it, and
    the MCP boundary forwarded it. The final assertion pins the consequence:
    a caller-created `:Source` still carries the creator-only defaults, so the
    suppression is unreachable from the tenant surface.
    """
    sdk = TortoiseSDK(str(tmp_path / "tortoise.db"),
                      event_log_path=str(tmp_path / "events" / "sdk.jsonl"))
    try:
        url = "session:forged-5097"
        forged = dict(sessionId="forged", capturedAt=SECOND_NOW,
                      eventId="forged-event")
        with pytest.raises(ValueError, match="_stub_completion"):
            sdk.create_source(url, "agentSession", contentHash="h-1",
                              _stub_completion=True, **forged)
        # The fail-closed backstop every OTHER props surface passes through.
        with pytest.raises(ValueError, match="_stub_completion"):
            sdk.create_point("statement", "a statement",
                             _stub_completion=True)
        # …and the MCP boundary, the third surface the docstring above names:
        # the tool splats caller `props` into `create_source(**props)`, so the
        # name must be refused BEFORE the write is attempted.
        from tortoise.mcp_server import _reject_server_managed_props
        assert _reject_server_managed_props({"_stub_completion": True}), (
            "the MCP boundary forwarded the capture discriminator")
        assert _reject_server_managed_props({"sourceKind": "document"}) is None
        # Nothing was written by the refused calls, and a legitimate
        # `create_source` still gets the creator-only defaults — i.e. the
        # ON CREATE suppression is not reachable from the caller surface.
        assert not sdk._get_proj().g.query(
            "MATCH (s:Source {url: $url}) RETURN properties(s)",
            params={"url": url}).result_set, (
            "the refused call still wrote a :Source")
        clean = "session:caller-5097-clean"
        sdk.create_source(clean, "agentSession", contentHash="h-1")
        props = _source_props(sdk._get_proj(), clean)
        assert props.get("sessionId") is None, (
            "a caller-created :Source gained the capture writer's provenance")
        assert props.get("id") == clean, (
            "a caller-created :Source must keep its `id` (the ON CREATE arm's "
            "creator-only default) — a caller-settable `_stub_completion` "
            "suppressed it")
        assert props.get("version") == 1
        assert "externalId" in props
    finally:
        sdk.close()
