"""#3516 §B — the capture-lane marker (``hook`` | ``store_sync``).

The lane is the ONLY discriminator that makes the hook-liveness check
falsifiable (#3515 piece 8): the two producers POST the *same* payload except
this field, so without it a run whose hook is stubbed no-op greens the check.

Every test here asserts an OBSERVABLE artifact — the property read back off the
stored ``:Session``, or the key actually put on the wire — never that a helper
was called. The load-bearing pair is
``test_store_sync_lane_is_persisted_and_read_back`` (a lane really lands in the
graph) and ``test_claude_hook_path_stamps_hook_lane_on_the_wire`` (the shipped
Claude CLI leg really claims the lane).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from tests.test_hosted_api import TEST_ORG_ID
from tests.test_hosted_api import client as client
from tortoise import hosted_api as ha_mod


@pytest.fixture(autouse=True)
def _offline_llm(monkeypatch):
    """Offline MockModel extraction seam — no provider/network (mirrors
    tests/test_write_session_and_turns_3551.py)."""
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")


@pytest.fixture(autouse=True)
def _warm_embedded_graph():
    """#3834/#4098: the hosted lane's 10s transport wait bound measures the FIRST
    request that OPENS the embedded graph. On a loaded box that open alone can
    exceed it and an unrelated request is refused with 504 — an environment
    flake, not a product failure (the convention
    tests/test_hosted_api.py::_warm_data_graph documents). Warming it here keeps
    the lane assertions about the LANE rather than about box load."""
    ha_mod._make_sdk(namespace=TEST_ORG_ID)._get_proj().g.query("RETURN 1")


def _session_lane(sdk, sid: str) -> str | None:
    """The lane as STORED on the :Session — the artifact the liveness check reads."""
    rows = sdk._get_proj().g.query(
        "MATCH (s:Session {id:$sid}) RETURN s.capture_lane",
        params={"sid": sid}).result_set
    assert rows, f"no :Session {sid!r} — the capture did not land"
    return rows[0][0]


def _post_capture(client, payload: dict):
    """POST /v1/sessions, tolerating the embedded lane's #3834/#4098 504.

    The hosted lane's 10s transport wait bound measures the request that OPENS
    the embedded graph; on a loaded box that open alone exceeds it and the
    request is refused with a 504 whose own body says the work may still have
    completed and that a retry is safe when repeating the operation is safe.
    Capture IS idempotent by ``session_id`` and the lane is first-writer-wins,
    so a single retry is sound — it makes these assertions about the LANE
    rather than about box load.
    """
    r = client.post("/v1/sessions", json=payload)
    if r.status_code == 504:
        r = client.post("/v1/sessions", json=payload)
    return r


# ── the boundary contract (no DB) ──────────────────────────────────────────

@pytest.mark.parametrize("lane", ["hook", "store_sync", None])
def test_model_accepts_the_lane_vocabulary_including_absent(lane):
    """The two real lanes, and ABSENT — a pre-installed hook / SDK / backfill
    producer POSTs without the field and must never 422."""
    m = ha_mod.SessionRequest(
        session_id="s-lane-model", conversation=[{"role": "user", "content": "x"}],
        capture_lane=lane)
    assert m.capture_lane == lane


def test_model_rejects_an_unknown_lane():
    """A typo'd lane 422s at the boundary — it must never be stored as a lane
    the liveness check would then misread."""
    with pytest.raises(ValidationError):
        ha_mod.SessionRequest(
            session_id="s-lane-bad",
            conversation=[{"role": "user", "content": "x"}],
            capture_lane="recorder")


# ── the lane really lands on the stored Session ────────────────────────────

def test_writer_persists_the_lane_and_absence_never_erases_it():
    """The shared writer stores the lane, and a lane-less re-capture does NOT
    erase it (set-only-when-present — the same rule as harness). This is the
    direct-writer proof; the HTTP round-trip below proves it end-to-end."""
    from tortoise.sdk import _write_session_and_turns

    sdk = ha_mod._make_sdk(namespace=TEST_ORG_ID)
    proj = sdk._get_proj()
    sid = "3516-writer-lane"
    turns = [{"role": "user", "content": "hi"}]
    _write_session_and_turns(proj, sdk, sid, turns,
                             now="2026-10-04T00:00:00Z", harness="pi",
                             capture_lane="store_sync")
    assert _session_lane(sdk, sid) == "store_sync"
    _write_session_and_turns(proj, sdk, sid, turns,
                             now="2026-10-04T00:00:01Z", harness="pi")
    assert _session_lane(sdk, sid) == "store_sync", (
        "a lane-less re-capture erased the stored lane")


def test_first_writer_wins_so_store_sync_cannot_relabel_a_hook_session():
    """#3515 piece 7: the hook and the store-sync backstop ship the SAME session,
    and the backstop ships AFTER. Storing the lane last-writer-wins would
    RELABEL a live hook session 'store_sync', and the hook-liveness read
    (piece 8) would then report a WORKING hook as NOT live. First writer wins —
    the same effective rule harness uses via `_observed_capture_harness`."""
    from tortoise.sdk import _write_session_and_turns

    sdk = ha_mod._make_sdk(namespace=TEST_ORG_ID)
    proj = sdk._get_proj()
    turns = [{"role": "user", "content": "hi"}]
    _write_session_and_turns(proj, sdk, "3516-first-hook", turns,
                             now="2026-10-04T00:00:00Z", harness="pi",
                             capture_lane="hook")
    assert _session_lane(sdk, "3516-first-hook") == "hook"
    _write_session_and_turns(proj, sdk, "3516-first-hook", turns,
                             now="2026-10-04T00:00:01Z", harness="pi",
                             capture_lane="store_sync")
    assert _session_lane(sdk, "3516-first-hook") == "hook", (
        "a later store_sync capture RELABELLED a live hook session")
    # negative control: a store-sync-ONLY session still reads store_sync.
    _write_session_and_turns(proj, sdk, "3516-first-sync", turns,
                             now="2026-10-04T00:00:02Z", harness="pi",
                             capture_lane="store_sync")
    assert _session_lane(sdk, "3516-first-sync") == "store_sync"


def test_store_sync_lane_is_persisted_and_read_back(client):
    """CAPTURE HAPPENED WITH A LANE: a store-sync producer's POST is stored and
    the lane reads back off the :Session."""
    sid = "3516-lane-store-sync"
    sdk = ha_mod._make_sdk(namespace=TEST_ORG_ID)
    r = _post_capture(client, {
        "conversation": [{"role": "user", "content": "hello from store-sync"}],
        "session_id": sid,
        "harness": "pi",
        "capture_lane": "store_sync",
    })
    assert r.status_code == 200, r.text[:400]
    assert _session_lane(sdk, sid) == "store_sync"


def test_lane_marker_distinguishes_hook_from_store_sync(client):
    """The falsifiability property: two otherwise-identical captures are
    distinguishable by the persisted lane alone."""
    sdk = ha_mod._make_sdk(namespace=TEST_ORG_ID)
    for sid, lane in (("3516-lane-hook", "hook"),
                      ("3516-lane-sync", "store_sync")):
        r = _post_capture(client, {
            "conversation": [{"role": "user", "content": "same payload"}],
            "session_id": sid,
            "harness": "pi",
            "capture_lane": lane,
        })
        assert r.status_code == 200, r.text[:400]
    assert _session_lane(sdk, "3516-lane-hook") == "hook"
    assert _session_lane(sdk, "3516-lane-sync") == "store_sync"


def test_absent_lane_is_not_fabricated_and_never_422s(client):
    """A lane-less producer (backfill/import, or a pre-#3516 hook) POSTs
    successfully and the stored lane is ABSENT — not a fabricated default."""
    # Guard the guard: if the field were removed, the negative assertion below
    # would pass VACUOUSLY (pydantic ignores an unknown key by default) — this
    # reads the model surface, which goes red the moment it is reverted.
    assert "capture_lane" in ha_mod.SessionRequest.model_fields
    sid = "3516-lane-absent"
    sdk = ha_mod._make_sdk(namespace=TEST_ORG_ID)
    r = _post_capture(client, {
        "conversation": [{"role": "user", "content": "no lane here"}],
        "session_id": sid,
        "harness": "claude",
    })
    assert r.status_code == 200, r.text[:400]
    assert _session_lane(sdk, sid) is None


def test_invalid_lane_is_rejected_at_the_http_boundary(client):
    # The content is NON-BLANK and the detail must name `capture_lane`: with
    # blank content the request 422s for an unrelated reason ("no extractable
    # content"), so a bare `status_code == 422` here passed even with the lane
    # validator disabled — the test claimed a boundary guarantee it could not
    # detect (#3516 §B review).
    r = client.post("/v1/sessions", json={
        "conversation": [{"role": "user", "content": "a real turn"}],
        "session_id": "3516-lane-invalid",
        "harness": "pi",
        "capture_lane": "recorder",
    })
    assert r.status_code == 422, r.text[:400]
    detail = str(r.json().get("detail"))
    assert "capture_lane" in detail, detail
    assert "loc" not in detail or "recorder" in detail, detail


def test_a_lane_less_filing_omits_the_key_so_both_legs_read_it_alike(tmp_path, monkeypatch):
    """The lane-less filing must NOT write ``"filed_lane": null``.

    The two legs share one spool directory and read each other's meta: JSON has
    no ``undefined``, so a Python-written ``null`` read by the TS leg (a raw
    ``JSON.parse``) is ``null`` where its own writer leaves the key ABSENT — and
    the TS skip's strict ``===`` then reads ``null !== undefined``, bypasses the
    skip, and re-POSTs an entry Python already filed. Asserting the key is
    ABSENT (not merely falsy) is the point: ``.get(...) is None`` passes on
    ``null`` and would not catch the defect.
    """
    import tortoise.capture_spool as spool

    root = tmp_path / "spool"
    monkeypatch.setattr(spool, "spool_dir", lambda: root)
    sid = "3516-lane-less-filing"
    spool.write_spool_entry(root, spool.Snapshot(
        session_id=sid, turns=[{"role": "user", "content": "lane-less"}],
        source="t", machine_id="m"))  # no capture_lane
    posted: list[dict] = []
    spool.flush_spool(root, lambda p: (posted.append(p),
                                       spool.PostOutcome(ok=True, status=200))[1],
                      only_session_id=sid)
    assert posted, "the lane-less entry was not filed at all"
    assert "capture_lane" not in posted[0], posted[0]
    on_disk = spool.read_spool_meta(root, sid)
    assert on_disk and on_disk.get("filed_key"), on_disk
    assert "filed_lane" not in on_disk, (
        "a lane-less filing must OMIT filed_lane, not write it as null — a TS "
        f"reader sees null where its own writer leaves the key absent: {on_disk}"
    )
    # And it must be SKIPPED on the next drain in THIS leg — the negative
    # assertion above cannot distinguish "omitted" from "skipped forever".
    again: list[dict] = []
    summary = spool.flush_spool(
        root, lambda p: (again.append(p),
                         spool.PostOutcome(ok=True, status=200))[1],
        only_session_id=sid)
    assert not again and summary.skipped == 1, (again, summary)


# ── the shipped Claude hook leg claims 'hook' ──────────────────────────────

def _claude_transcript(tmp_path):
    # `_spool_transcript` parses SPEAKER-PREFIXED text (User:/Assistant:), the
    # shape `session capture` consumes — not Claude Code's raw JSONL.
    p = tmp_path / "transcript.txt"
    p.write_text("User: hello\nAssistant: hi\n", encoding="utf-8")
    return p


def test_claude_hook_path_stamps_hook_lane_on_the_wire(tmp_path, monkeypatch):
    """The shipped Claude CLI leg (``session capture``) claims 'hook', and the
    lane reaches the WIRE — the exact key the server would receive.

    Without this the claude path is permanently lane-less, so a WORKING claude
    install reads as hook-not-live (#3515 piece 8)."""
    import tortoise.capture_spool as spool
    from tortoise import __main__ as cli

    root = tmp_path / "spool"
    monkeypatch.setattr(spool, "spool_dir", lambda: root)
    transcript = _claude_transcript(tmp_path)
    args = SimpleNamespace(file=str(transcript), harness="claude",
                           session_id="3516-hook-wire", model=None)

    prep = cli._spool_transcript(args)
    assert prep["rc"] == 0, "the hook leg failed to spool the transcript"

    # (1) the DURABLE local record carries the lane …
    meta = spool.read_spool_meta(prep["root"], "3516-hook-wire")
    assert meta is not None and meta.get("capture_lane") == "hook"

    # (2) … and it survives to the POSTED payload.
    posted: list[dict] = []

    def _post(payload):
        posted.append(payload)
        return spool.PostOutcome(ok=True, status=200)

    spool.flush_spool(prep["root"], _post, only_session_id="3516-hook-wire")
    assert posted, "the spooled hook capture was never posted"
    assert posted[0].get("capture_lane") == "hook"


def test_lane_less_entry_posts_without_the_key(tmp_path, monkeypatch):
    """A pre-#3516 / backfill spool entry has no lane and must POST WITHOUT the
    key rather than inventing one (set-only-when-present)."""
    import tortoise.capture_spool as spool

    root = tmp_path / "spool"
    monkeypatch.setattr(spool, "spool_dir", lambda: root)
    spool.write_spool_entry(root, spool.Snapshot(
        session_id="3516-lane-less", turns=[{"role": "user", "content": "x"}],
        source="t", machine_id="m"))  # no capture_lane
    posted: list[dict] = []
    spool.flush_spool(root, lambda p: (posted.append(p),
                                       spool.PostOutcome(ok=True, status=200))[1],
                      only_session_id="3516-lane-less")
    assert posted
    assert "capture_lane" not in posted[0], (
        "the lane-less entry fabricated a lane on the wire")
    # Positive control: the SAME path DOES put the key on the wire when the
    # entry has a lane — so the assertion above cannot pass on a revert.
    spool.write_spool_entry(root, spool.Snapshot(
        session_id="3516-lane-ful", turns=[{"role": "user", "content": "x"}],
        source="t", machine_id="m", capture_lane="hook"))
    posted2: list[dict] = []
    spool.flush_spool(root, lambda p: (posted2.append(p),
                                       spool.PostOutcome(ok=True, status=200))[1],
                      only_session_id="3516-lane-ful")
    assert posted2 and posted2[0].get("capture_lane") == "hook"


def test_spool_lane_survives_a_lane_less_resnapshot(tmp_path, monkeypatch):
    """The spool is the durability path for a capture whose FIRST POST failed.
    A lane-less re-snapshot (import/backfill) must not ERASE the lane a hook
    already claimed, or the eventual POST drops it and a live hook reads as not
    confirmed (review F3)."""
    import tortoise.capture_spool as spool

    root = tmp_path / "spool"
    monkeypatch.setattr(spool, "spool_dir", lambda: root)
    sid = "3516-spool-carry"
    spool.write_spool_entry(root, spool.Snapshot(
        session_id=sid, turns=[{"role": "user", "content": "x"}],
        source="t", machine_id="m", capture_lane="hook"))
    assert spool.read_spool_meta(root, sid).get("capture_lane") == "hook"
    spool.write_spool_entry(root, spool.Snapshot(
        session_id=sid,
        turns=[{"role": "user", "content": "x"},
               {"role": "assistant", "content": "y"}],
        source="t", machine_id="m"))  # lane-less, grew
    assert spool.read_spool_meta(root, sid).get("capture_lane") == "hook", (
        "a lane-less re-snapshot ERASED the stored lane")
    # The resolution is TRUTHY, not nullish: an empty-string lane must carry
    # the stored lane forward too, exactly as the TypeScript leg does.
    spool.write_spool_entry(root, spool.Snapshot(
        session_id=sid,
        turns=[{"role": "user", "content": "x"},
               {"role": "assistant", "content": "y"},
               {"role": "user", "content": "z"}],
        source="t", machine_id="m", capture_lane=""))
    assert spool.read_spool_meta(root, sid).get("capture_lane") == "hook", (
        "an empty-string lane erased the stored lane (nullish vs truthy)")


def test_spool_lane_is_first_writer_wins_so_store_sync_cannot_downgrade_hook(
        tmp_path, monkeypatch):
    """The pinned contract (docs/plans/2026-08-25-1714-memory-capture-onboarding.md):
    "the delivery lane is monotone (a `store_sync` write never downgrades
    `hook`)". The spool's lane is what gets DELIVERED, so `prior` must win when
    both are set — a later `store_sync` snapshot cannot relabel a stored `hook`.
    This is the same first-writer-wins rule the server applies via coalesce
    (review P2)."""
    import tortoise.capture_spool as spool

    root = tmp_path / "spool"
    monkeypatch.setattr(spool, "spool_dir", lambda: root)
    sid = "3516-spool-monotone"
    spool.write_spool_entry(root, spool.Snapshot(
        session_id=sid, turns=[{"role": "user", "content": "a"}],
        source="t", machine_id="m", capture_lane="hook"))
    # A LATER `store_sync` snapshot of the SAME (grown) entry must not downgrade.
    spool.write_spool_entry(root, spool.Snapshot(
        session_id=sid,
        turns=[{"role": "user", "content": "a"},
               {"role": "assistant", "content": "b"}],
        source="t", machine_id="m", capture_lane="store_sync"))
    assert spool.read_spool_meta(root, sid).get("capture_lane") == "hook", (
        "a store_sync snapshot DOWNGRADED a stored hook lane")
    posts: list[dict] = []
    spool.flush_spool(
        root, lambda p: (posts.append(dict(p)),
                         spool.PostOutcome(ok=True, status=200))[1],
        only_session_id=sid)
    assert posts and posts[0].get("capture_lane") == "hook", (
        "the downgrade reached the wire")


def test_a_lane_upgrade_during_the_post_window_is_not_swallowed(tmp_path, monkeypatch):
    """The drain's compare-and-swap stamped `filed_key` on a CONTENT digest,
    which excludes the lane. A lane upgrade landing while the POST was in flight
    was therefore CANCELLED: the payload went out lane-less, the on-disk meta
    got the lane, and the CAS re-filed it — so `filed_key == capture_key` and
    every later drain skipped it. The lane was stranded forever and a working
    hook read as not-live. Reachable in the shipped topology:
    claude-hooks/session-start.sh backgrounds the drain."""
    import tortoise.capture_spool as spool

    root = tmp_path / "spool"
    monkeypatch.setattr(spool, "spool_dir", lambda: root)
    sid = "3516-cas-lane"
    turns = [{"role": "user", "content": "same"}]
    spool.write_spool_entry(root, spool.Snapshot(
        session_id=sid, turns=turns, source="t", machine_id="m"))  # lane-less

    def post_and_upgrade(payload):
        # The hook re-snapshots the SAME entry while this POST is in flight.
        spool.write_spool_entry(root, spool.Snapshot(
            session_id=sid, turns=turns, source="t", machine_id="m",
            capture_lane="hook"))
        assert "capture_lane" not in payload, (
            "this test needs the POST to go out BEFORE the upgrade")
        return spool.PostOutcome(ok=True, status=200)

    spool.flush_spool(root, post_and_upgrade, only_session_id=sid)
    meta = spool.read_spool_meta(root, sid)
    assert meta.get("capture_lane") == "hook"
    assert not (meta.get("filed_key") and meta.get("filed_key") == meta.get("capture_key")), (
        "the CAS re-filed a lane-upgraded entry — the lane can never be re-POSTed")

    posted: list[dict] = []
    spool.flush_spool(
        root, lambda p: (posted.append(dict(p)),
                         spool.PostOutcome(ok=True, status=200))[1],
        only_session_id=sid)
    assert posted and posted[0].get("capture_lane") == "hook", (
        "the lane was STRANDED — the drain skipped the upgraded entry forever")


def test_a_lane_that_appears_after_filing_is_never_skipped(tmp_path, monkeypatch):
    """`filed_key` is content-derived and says nothing about the LANE, so on its
    own it lets a lane-less filing mask a lane that appears afterwards — the
    lane is then stranded and a working hook reads as not-live. `filed_lane`
    records what the 2xx actually carried, and the skip requires it to match.
    The state is written directly here because the CAS's read→write instant can
    produce exactly it."""
    import tortoise.capture_spool as spool

    root = tmp_path / "spool"
    monkeypatch.setattr(spool, "spool_dir", lambda: root)
    sid = "3516-filed-lane"
    turns = [{"role": "user", "content": "same"}]
    spool.write_spool_entry(root, spool.Snapshot(
        session_id=sid, turns=turns, source="t", machine_id="m"))

    first: list[dict] = []
    spool.flush_spool(
        root, lambda p: (first.append(dict(p)),
                         spool.PostOutcome(ok=True, status=200))[1],
        only_session_id=sid)
    assert first and "capture_lane" not in first[0]
    meta = spool.read_spool_meta(root, sid)
    assert meta.get("filed_key") == meta.get("capture_key")
    assert "filed_lane" not in meta, (
        "the lane-less filing recorded a delivered lane it never put on the "
        "wire (and must OMIT the key, not write null — the TS leg reads this "
        f"same directory): {meta}")

    meta["capture_lane"] = "hook"  # the lane appears after the filing
    spool._write_meta(root, meta)

    second: list[dict] = []
    spool.flush_spool(
        root, lambda p: (second.append(dict(p)),
                         spool.PostOutcome(ok=True, status=200))[1],
        only_session_id=sid)
    assert second and second[0].get("capture_lane") == "hook", (
        "a lane arriving after the filing was skipped — it is stranded")
    assert spool.read_spool_meta(root, sid).get("filed_lane") == "hook"


def test_an_empty_stored_lane_is_filled_in_by_a_later_snapshot(tmp_path, monkeypatch):
    """The ONLY input that distinguishes truthy `or` from a nullish form: a
    stored EMPTY lane with a truthy new one (`"" or "hook"` -> `"hook"`; the
    nullish form would omit the key). Without this the `or`/`??` choice is
    unpinned — an empty lane is only reachable from a crafted/corrupt meta,
    which is exactly what this writes (review F2)."""
    import tortoise.capture_spool as spool

    root = tmp_path / "spool"
    monkeypatch.setattr(spool, "spool_dir", lambda: root)
    sid = "3516-empty-lane"
    turns = [{"role": "user", "content": "a"}]
    spool.write_spool_entry(root, spool.Snapshot(
        session_id=sid, turns=turns, source="t", machine_id="m"))
    meta = spool.read_spool_meta(root, sid)
    meta["capture_lane"] = ""  # crafted: the public API never writes a falsy lane
    spool._write_meta(root, meta)
    assert spool.read_spool_meta(root, sid).get("capture_lane") == ""

    spool.write_spool_entry(root, spool.Snapshot(
        session_id=sid,
        turns=[{"role": "user", "content": "a"},
               {"role": "assistant", "content": "b"}],
        source="t", machine_id="m", capture_lane="hook"))
    assert spool.read_spool_meta(root, sid).get("capture_lane") == "hook", (
        "an empty stored lane was not filled in — nullish semantics, not truthy")


def test_spool_lane_ful_identical_resnapshot_is_a_no_op(tmp_path, monkeypatch):
    """Pins the `not prior.capture_lane` half of `lane_upgrade`. Without it
    EVERY identical re-snapshot would rewrite the meta and clear `filed_key`,
    re-POSTing the same conversation at turn cadence — the amplification the
    dedup guard exists to prevent (review P2)."""
    import tortoise.capture_spool as spool

    root = tmp_path / "spool"
    monkeypatch.setattr(spool, "spool_dir", lambda: root)
    sid = "3516-spool-noop"
    turns = [{"role": "user", "content": "same"}]
    spool.write_spool_entry(root, spool.Snapshot(
        session_id=sid, turns=turns, source="t", machine_id="m",
        capture_lane="hook"))
    res = spool.write_spool_entry(root, spool.Snapshot(
        session_id=sid, turns=turns, source="t", machine_id="m",
        capture_lane="hook"))
    assert res["written"] is False, "a lane-ful identical re-snapshot must be a no-op"
    assert spool.read_spool_meta(root, sid).get("capture_lane") == "hook"


def test_the_journal_carrier_carries_the_lane_and_replay_recovers_it():
    """Pins the PRODUCER half of the journal guard. `_write_session_and_turns`
    must put the lane into the record it hands `on_session_merged`, or a
    journal-only replay of a lane-carrying capture cannot recover it — and the
    fold pinned by `test_journal_fold_keeps_the_first_lane` would be permanently
    UNREACHABLE (review P2). Drives both ends directly, because no caller passes
    a lane and `on_session_merged` together today."""
    from tortoise.sdk import _write_session_and_turns

    sdk = ha_mod._make_sdk(namespace=TEST_ORG_ID)
    proj = sdk._get_proj()
    sid = "3516-journal-lane"
    emitted: list[dict] = []
    _write_session_and_turns(
        proj, sdk, sid, [{"role": "user", "content": "hi"}],
        now="2026-10-04T00:00:00Z", harness="pi", capture_lane="hook",
        on_session_merged=emitted.append)
    assert emitted, "the writer emitted no journal carrier at all"
    assert any(ev.get("capture_lane") == "hook" for ev in emitted), (
        "the writer dropped the lane from its journal carrier")

    # Simulate a journal-only REPLAY: wipe the live node, then fold the journal.
    proj.g.query("MATCH (s:Session {id:$sid}) DELETE s", params={"sid": sid})
    for ev in emitted:
        proj._fold_session_recorded({"type": "SessionRecorded", **ev})
    assert _session_lane(sdk, sid) == "hook", (
        "the lane did not survive a journal-only replay")


def test_spool_lane_upgrade_refiles_an_already_filed_entry(tmp_path, monkeypatch):
    """A lane upgrade must also INVALIDATE the filing marker. `filed_key` is
    content-derived (the lane is not part of it), so an entry already filed
    LANE-LESS would keep its marker, be skipped by the drain, and stay
    lane-less forever — the same outcome the dedup guard exists to prevent
    (review P2)."""
    import tortoise.capture_spool as spool

    root = tmp_path / "spool"
    monkeypatch.setattr(spool, "spool_dir", lambda: root)
    sid = "3516-spool-refile"
    turns = [{"role": "user", "content": "same"}]
    spool.write_spool_entry(root, spool.Snapshot(
        session_id=sid, turns=turns, source="t", machine_id="m"))

    posts: list[dict] = []
    spool.flush_spool(root, lambda p: (posts.append(dict(p)),
                                       spool.PostOutcome(ok=True, status=200))[1],
                      only_session_id=sid)
    assert posts and "capture_lane" not in posts[0]
    assert spool.read_spool_meta(root, sid).get("filed_key"), (
        "the first filing did not stamp a marker — the test cannot prove anything")

    # The hook now re-snapshots the SAME content, claiming its lane.
    spool.write_spool_entry(root, spool.Snapshot(
        session_id=sid, turns=turns, source="t", machine_id="m",
        capture_lane="hook"))
    posts2: list[dict] = []
    spool.flush_spool(root, lambda p: (posts2.append(dict(p)),
                                       spool.PostOutcome(ok=True, status=200))[1],
                      only_session_id=sid)
    assert posts2, "the lane upgrade was never re-POSTed — it stranded in the spool"
    assert posts2[0].get("capture_lane") == "hook"


def test_journal_fold_keeps_the_first_lane():
    """A journal-only replay must converge on the SAME lane as the live write.
    Two SessionRecorded events for one session, the later claiming
    `store_sync` — the fold is first-writer-wins, matching the live coalesce
    (review P2; without this a revert of the fold hunk is invisible)."""
    sdk = ha_mod._make_sdk(namespace=TEST_ORG_ID)
    proj = sdk._get_proj()
    assert proj._fold_session_recorded({
        "type": "SessionRecorded", "id": "3516-fold", "capture_lane": "hook"}) == 1
    assert proj._fold_session_recorded({
        "type": "SessionRecorded", "id": "3516-fold",
        "capture_lane": "store_sync"}) == 1
    assert _session_lane(sdk, "3516-fold") == "hook", (
        "replay diverged from live: the fold relabelled the lane")


def test_spool_lane_upgrade_survives_identical_content_dedup(tmp_path, monkeypatch):
    """A hook re-snapshot of byte-IDENTICAL turns must still stamp the lane: the
    content-dedup early-return would otherwise swallow it and the entry would
    stay lane-less forever (review F6)."""
    import tortoise.capture_spool as spool

    root = tmp_path / "spool"
    monkeypatch.setattr(spool, "spool_dir", lambda: root)
    sid = "3516-spool-upgrade"
    turns = [{"role": "user", "content": "same"}]
    spool.write_spool_entry(root, spool.Snapshot(
        session_id=sid, turns=turns, source="t", machine_id="m"))  # lane-less
    assert spool.read_spool_meta(root, sid).get("capture_lane") is None
    res = spool.write_spool_entry(root, spool.Snapshot(
        session_id=sid, turns=turns, source="t", machine_id="m",
        capture_lane="hook"))
    assert res["written"] is True, (
        "the content-dedup early-return swallowed the lane upgrade")
    assert spool.read_spool_meta(root, sid).get("capture_lane") == "hook"


# ── #7708 — an unrecognised / falsy lane can never reach the wire or delete ──
#
# The defect: the drain forwarded the stored lane VERBATIM, the server answered
# 422 for an unrecognised one, the drain classified 422 PERMANENT, and unlinked
# the entry's turn log — the only copy of the conversation. The same shape made
# a FALSY stored lane re-POST forever (the payload dropped it while the skip
# clause and the CAS compared the raw value against `None`).
#
# Site map — one discriminating test each; REMOVING the normaliser at a site
# reds that site's test:
#   1 WRITE         test_spool_writer_drops_an_unrecognised_lane
#                   test_spool_writer_heals_a_stored_foreign_lane
#   2 DRAIN payload test_the_drain_never_puts_an_unrecognised_lane_on_the_wire
#   3 SKIP clause   test_a_falsy_lane_that_was_filed_is_skipped_not_re_posted
#   4 SUCCESS CAS   test_a_falsy_stored_lane_is_stamped_filed_on_the_first_drain
#   6 TERMINAL      test_a_422_is_terminal_and_never_unlinks_the_transcript
# Plus the vocabulary mirror and a cross-language set pin.


def test_the_spool_lane_vocabulary_mirrors_the_server_set():
    """The closed set lives at the SERVER (`hosted_api._SESSION_CAPTURE_LANE_VALUES`)
    — it is what produces the 422. The spool mirrors it (it cannot import
    hosted_api's FastAPI stack); this pin makes that mirror drift-proof."""
    import tortoise.capture_spool as spool

    assert spool.CAPTURE_LANES == ha_mod._SESSION_CAPTURE_LANE_VALUES
    assert set(spool.CAPTURE_LANES) == {"hook", "store_sync"}
    # And the normaliser really is the membership test, not a truthiness gate.
    for good in ("hook", "store_sync"):
        assert spool._normalise_lane(good) == good
    for bad in ("bogus", "recorder", "", "Hook", 0, False, None, [], ["hook"]):
        assert spool._normalise_lane(bad) is None, bad


def test_the_spool_harness_vocabulary_mirrors_the_server_set():
    """The HARNESS mirror (#7708 follow-up): the spool normalises `harness` at
    the write and at the wire, so its set must equal the server's closed set —
    a drift would either coerce a legal harness to the default or let an illegal
    one through."""
    import tortoise.capture_spool as spool

    assert spool.SESSION_HARNESSES == ha_mod._SESSION_HARNESS_VALUES
    for good in ("claude", "codex", "pi", "openclaw", "hermes", "claude-desktop"):
        assert spool._normalise_harness(good) == good
    # The CLI alias resolves; everything else (incl. non-strings) is the default.
    assert spool._normalise_harness("desktop") == "claude-desktop"
    for bad in ("bogus", "Claude", 0, False, None, [], ["codex"]):
        assert spool._normalise_harness(bad) == "claude", bad


def test_the_ts_lane_set_matches_the_server_and_python_legs(tmp_path):
    """The TS leg is a separate language, so its mirror has no import to pin it.
    Execute it and compare the SET — an over-wide TS set (e.g. one that also
    accepts "harness") would otherwise let an unrecognised lane reach the wire
    with no test red."""
    import json as _json
    from pathlib import Path

    import tortoise.capture_spool as spool
    from tests.test_capture_spool import _node_that_can_strip_ts

    node = _node_that_can_strip_ts()
    ts_uri = (Path(__file__).resolve().parents[1]
              / "tortoise" / "pi-hooks" / "tortoise-capture.ts").as_uri()
    driver = tmp_path / "lane-set.mjs"
    driver.write_text(
        f'import {{ CAPTURE_LANES }} from "{ts_uri}";\n'
        "console.log(JSON.stringify([...CAPTURE_LANES].sort()));\n",
        encoding="utf-8")
    import subprocess
    proc = subprocess.run(
        [node, "--experimental-strip-types", str(driver)],
        capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"
    ts_set = set(_json.loads(proc.stdout.strip().splitlines()[-1]))
    assert ts_set == set(ha_mod._SESSION_CAPTURE_LANE_VALUES) == set(spool.CAPTURE_LANES)


def test_spool_writer_drops_an_unrecognised_lane(tmp_path):
    """Site 1. MUTATION THAT REDS THIS: remove `_normalise_lane` from the writer
    → the foreign token is stored and later posted verbatim."""
    import tortoise.capture_spool as spool

    root = tmp_path / "spool"
    spool.write_spool_entry(root, spool.Snapshot(
        session_id="7708-writer-drop", turns=[{"role": "user", "content": "x"}],
        source="t", machine_id="m", capture_lane="bogus"))
    meta = spool.read_spool_meta(root, "7708-writer-drop")
    assert "capture_lane" not in meta, meta
    # positive control: a RECOGNISED lane IS stored, so the assertion above
    # cannot pass on a writer that stopped storing lanes entirely.
    spool.write_spool_entry(root, spool.Snapshot(
        session_id="7708-writer-keep", turns=[{"role": "user", "content": "x"}],
        source="t", machine_id="m", capture_lane="hook"))
    assert spool.read_spool_meta(root, "7708-writer-keep").get("capture_lane") == "hook"


def test_spool_writer_heals_a_stored_foreign_lane(tmp_path):
    """Site 1 (`lane_upgrade`). Raw resolution would let a stored foreign lane
    win the truthy `or` AND keep `lane_upgrade` False, so the byte-identical
    dedup early-return would strand the valid snapshot lane forever."""
    import tortoise.capture_spool as spool

    root = tmp_path / "spool"
    sid = "7708-writer-heal"
    turns = [{"role": "user", "content": "same"}]
    spool.write_spool_entry(root, spool.Snapshot(
        session_id=sid, turns=turns, source="t", machine_id="m"))
    meta = spool.read_spool_meta(root, sid)
    meta["capture_lane"] = "bogus"  # crafted: the API never writes a foreign lane
    spool._write_meta(root, meta)
    res = spool.write_spool_entry(root, spool.Snapshot(
        session_id=sid, turns=turns, source="t", machine_id="m", capture_lane="hook"))
    assert res["written"] is True, "the dedup early-return swallowed the upgrade"
    assert spool.read_spool_meta(root, sid).get("capture_lane") == "hook"


def test_the_drain_never_puts_an_unrecognised_lane_on_the_wire(tmp_path):
    """Site 2. MUTATION THAT REDS THIS: remove `_normalise_lane` from the payload
    gate → the wire carries the foreign token and the server 422s."""
    import tortoise.capture_spool as spool

    root = tmp_path / "spool"
    sid = "7708-drain-gate"
    spool.write_spool_entry(root, spool.Snapshot(
        session_id=sid, turns=[{"role": "user", "content": "x"}],
        source="t", machine_id="m"))
    meta = spool.read_spool_meta(root, sid)
    meta["capture_lane"] = "bogus"
    spool._write_meta(root, meta)
    posted: list[dict] = []
    spool.flush_spool(
        root, lambda p: (posted.append(dict(p)),
                         spool.PostOutcome(ok=True, status=200))[1],
        only_session_id=sid)
    assert posted, "the entry was not filed"
    assert "capture_lane" not in posted[0], posted[0]
    assert spool.read_spool_meta(root, sid).get("filed_key"), (
        "the entry was not marked filed — the drain could not skip it later")


def test_a_falsy_lane_that_was_filed_is_skipped_not_re_posted(tmp_path):
    """Site 3 (skip clause). MUTATION THAT REDS THIS: compare the RAW lanes →
    `None == ""` is False and the entry re-POSTs its whole transcript on EVERY
    drain (#4714 amplification)."""
    import tortoise.capture_spool as spool

    root = tmp_path / "spool"
    sid = "7708-skip"
    spool.write_spool_entry(root, spool.Snapshot(
        session_id=sid, turns=[{"role": "user", "content": "x"}],
        source="t", machine_id="m"))
    posts: list[dict] = []
    spool.flush_spool(
        root, lambda p: (posts.append(dict(p)),
                         spool.PostOutcome(ok=True, status=200))[1],
        only_session_id=sid)
    assert posts and "capture_lane" not in posts[0]
    meta = spool.read_spool_meta(root, sid)
    assert meta.get("filed_key") and "filed_lane" not in meta, meta
    meta["capture_lane"] = ""  # crafted later: filed_lane stays ABSENT
    spool._write_meta(root, meta)
    again: list[dict] = []
    summary = spool.flush_spool(
        root, lambda p: (again.append(dict(p)),
                         spool.PostOutcome(ok=True, status=200))[1],
        only_session_id=sid)
    assert again == [], "an already-filed entry with a falsy lane was re-POSTed"
    assert summary.skipped == 1


def test_a_falsy_stored_lane_is_stamped_filed_on_the_first_drain(tmp_path):
    """Site 4 (success CAS). MUTATION THAT REDS THIS: compare the raw disk lane
    → `"" == None` is False, `filed_key` is never stamped, and the entry
    re-POSTs on every drain."""
    import tortoise.capture_spool as spool

    root = tmp_path / "spool"
    sid = "7708-cas-falsy"
    spool.write_spool_entry(root, spool.Snapshot(
        session_id=sid, turns=[{"role": "user", "content": "x"}],
        source="t", machine_id="m"))
    meta = spool.read_spool_meta(root, sid)
    meta["capture_lane"] = ""
    spool._write_meta(root, meta)
    posts: list[dict] = []
    spool.flush_spool(
        root, lambda p: (posts.append(dict(p)),
                         spool.PostOutcome(ok=True, status=200))[1],
        only_session_id=sid)
    assert posts and "capture_lane" not in posts[0]
    assert spool.read_spool_meta(root, sid).get("filed_key"), (
        "the CAS did not stamp filed_key for a falsy lane (re-POST loop)")
    again: list[dict] = []
    summary = spool.flush_spool(
        root, lambda p: (again.append(dict(p)),
                         spool.PostOutcome(ok=True, status=200))[1],
        only_session_id=sid)
    assert again == [] and summary.skipped == 1, (again, summary)


def test_a_422_is_terminal_and_never_unlinks_the_transcript(tmp_path):
    """Site 6. MUTATION THAT REDS THIS: route the permanent verdict back to
    `_discard_entry` → the entry (meta AND turn log) is UNLINKED, the only copy
    of the conversation is gone, and `read_spool_meta` returns None."""
    import tortoise.capture_spool as spool

    root = tmp_path / "spool"
    sid = "7708-terminal"
    turns = [{"role": "user", "content": "the only copy"}]
    spool.write_spool_entry(root, spool.Snapshot(
        session_id=sid, turns=turns, source="t", machine_id="m"))
    posts: list[dict] = []

    def refuse(payload):
        posts.append(dict(payload))
        return spool.PostOutcome(
            ok=False, status=422, detail='invalid capture_lane "bogus"')

    first = spool.flush_spool(root, refuse, only_session_id=sid)
    assert len(posts) == 1
    assert [d["reason"] for d in first.discarded] == [spool.DISCARD_HTTP_REJECTED]
    assert first.lost == [], (
        "a retained terminal refusal must not read as a loss (#3971 class)")
    meta = spool.read_spool_meta(root, sid)
    assert meta is not None, "a 422 UNLINKED the only copy of the transcript"
    assert meta.get("terminal_failure"), meta
    assert spool.read_spool_turns(root, sid) == turns, "the turn log was lost"
    # A byte-IDENTICAL re-snapshot must not resurrect the retry: the dedup
    # early-return does not rebuild the meta, so the terminal marker survives.
    spool.write_spool_entry(root, spool.Snapshot(
        session_id=sid, turns=turns, source="t", machine_id="m"))
    assert spool.read_spool_meta(root, sid).get("terminal_failure"), (
        "a byte-identical re-snapshot cleared the terminal marker")
    second = spool.flush_spool(root, refuse, only_session_id=sid)
    assert second.attempted == 0 and second.terminal == 1, second
    assert len(posts) == 1, "a terminal entry was re-POSTed"
