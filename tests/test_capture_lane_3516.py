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


def _session_lane(sdk, sid: str) -> str | None:
    """The lane as STORED on the :Session — the artifact the liveness check reads."""
    rows = sdk._get_proj().g.query(
        "MATCH (s:Session {id:$sid}) RETURN s.capture_lane",
        params={"sid": sid}).result_set
    assert rows, f"no :Session {sid!r} — the capture did not land"
    return rows[0][0]


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
    r = client.post("/v1/sessions", json={
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
        r = client.post("/v1/sessions", json={
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
    r = client.post("/v1/sessions", json={
        "conversation": [{"role": "user", "content": "no lane here"}],
        "session_id": sid,
        "harness": "claude",
    })
    assert r.status_code == 200, r.text[:400]
    assert _session_lane(sdk, sid) is None


def test_invalid_lane_is_rejected_at_the_http_boundary(client):
    r = client.post("/v1/sessions", json={
        "conversation": [{"role": "user", "content": "x"}],
        "session_id": "3516-lane-invalid",
        "harness": "pi",
        "capture_lane": "recorder",
    })
    assert r.status_code == 422, r.text[:400]


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
