"""#3516 §B — the client-timestamp floor (``client_captured_at`` + its source).

#3515 piece 12 pins the contract: the store-proven check pairs with a floor that
compares the matched record's **client-recorded** capture instant against the
**server-recorded** install time, ±5 minutes, and a timestamp whose source is
``unknown`` can NEVER be counted as a pass. The client clock is the floor's
input because the server's ``capturedAt`` is the *ingest* transaction time — a
stale spool drained after an install would otherwise read as freshly captured
and the floor could only ever pass.

Every test asserts an OBSERVABLE artifact — the property read back off the
stored ``:Session``, the key really stored in the spool entry, or the floor's
own verdict — never that a helper was called.
"""
from __future__ import annotations

from datetime import datetime

import pytest
from pydantic import ValidationError

from tests.test_hosted_api import TEST_ORG_ID
from tortoise import hosted_api as ha_mod
from tortoise.capture_install import (
    FLOOR_SKEW_TOLERANCE_S,
    VERDICT_DISABLED,
    VERDICT_FAILED,
    VERDICT_PASSED,
    client_capture_floor_verdict,
)

#: One fixed install instant. Both clocks are set independently around it —
#: without that, the floor test could not fail.
INSTALL = 1_700_000_000.0


@pytest.fixture(autouse=True)
def _offline_llm(monkeypatch):
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")


@pytest.fixture(autouse=True)
def _warm_embedded_graph():
    """#3834/#4098: warm the graph the same way tests/test_capture_lane_3516.py
    does, so these assertions are about the stamp and not about box load."""
    ha_mod._make_sdk(namespace=TEST_ORG_ID)._get_proj().g.query("RETURN 1")


def _stamp(sdk, sid: str, key: str):
    """The property as STORED on the :Session — the artifact the floor reads."""
    rows = sdk._get_proj().g.query(
        "MATCH (s:Session {id:$sid}) RETURN s." + key,
        params={"sid": sid}).result_set
    assert rows, f"no :Session {sid!r} — the capture did not land"
    return rows[0][0]


# ── the contract: on the writer, and round-tripping through the graph ─────


def test_client_stamp_is_persisted_and_read_back():
    from tortoise.sdk import _write_session_and_turns

    sdk = ha_mod._make_sdk(namespace=TEST_ORG_ID)
    sid = "3516-stamp-roundtrip"
    _write_session_and_turns(
        sdk._get_proj(), sdk, sid, [{"role": "user", "content": "hi"}],
        now="2026-10-08T00:00:00Z", harness="claude", capture_lane="hook",
        client_captured_at=1_700_000_000.5,
        client_captured_at_source="cli_observed")
    assert _stamp(sdk, sid, "client_captured_at") == pytest.approx(1_700_000_000.5)
    assert _stamp(sdk, sid, "client_captured_at_source") == "cli_observed"


def test_absent_stamp_is_never_fabricated():
    """A pre-#3516 producer POSTs without it: absence must stay absence, never a
    fabricated instant — a fabricated floor input would pass on nothing."""
    from tortoise.sdk import _write_session_and_turns

    sdk = ha_mod._make_sdk(namespace=TEST_ORG_ID)
    sid = "3516-stamp-absent"
    _write_session_and_turns(
        sdk._get_proj(), sdk, sid, [{"role": "user", "content": "hi"}],
        now="2026-10-08T00:00:00Z", harness="claude", capture_lane="hook")
    assert _stamp(sdk, sid, "client_captured_at") is None
    assert _stamp(sdk, sid, "client_captured_at_source") is None


def test_store_sync_cannot_overwrite_the_hooks_stamp():
    """First writer wins, exactly like the lane. The store-sync backstop ships
    the SAME session AFTER the hook, so last-writer-wins would replace the
    hook's own observation with a weaker file mtime — and the floor would then
    be comparing an mtime against an install instant it was never about."""
    from tortoise.sdk import _write_session_and_turns

    sdk = ha_mod._make_sdk(namespace=TEST_ORG_ID)
    sid = "3516-stamp-firstwriter"
    turns = [{"role": "user", "content": "hi"}]
    _write_session_and_turns(sdk._get_proj(), sdk, sid, turns,
                             now="2026-10-08T00:00:00Z", harness="claude",
                             capture_lane="hook", client_captured_at=1000.0,
                             client_captured_at_source="cli_observed")
    _write_session_and_turns(sdk._get_proj(), sdk, sid, turns,
                             now="2026-10-08T00:00:01Z", harness="claude",
                             capture_lane="store_sync", client_captured_at=2000.0,
                             client_captured_at_source="file_mtime")
    assert _stamp(sdk, sid, "client_captured_at") == pytest.approx(1000.0)
    assert _stamp(sdk, sid, "client_captured_at_source") == "cli_observed"


def test_boundary_admits_unknown_but_refuses_an_unrecognised_source():
    """The source set is CLOSED: the floor's falsifiability depends on 'unknown'
    being an excludable value, which requires every stored value to be one the
    floor can interpret. 'unknown' is legal; anything else is a 422."""
    from tortoise.hosted_api import SessionRequest

    ok = SessionRequest(conversation=[{"role": "user", "content": "x"}],
                        client_captured_at_source="unknown")
    assert ok.client_captured_at_source == "unknown"
    with pytest.raises(ValidationError):
        SessionRequest(conversation=[{"role": "user", "content": "x"}],
                       client_captured_at_source="wall_clock")


# ── the floor: two clocks, one tolerance, and it must be falsifiable ──────


def test_clock_skew_tolerance():
    """Two clocks, independently set. Inside ±5 min passes either side of the
    install; beyond it is REJECTED WITH A REASON, never silently accepted."""
    assert FLOOR_SKEW_TOLERANCE_S == 300.0
    assert client_capture_floor_verdict(
        INSTALL + 1, "cli_observed", INSTALL)[0] == VERDICT_PASSED
    assert client_capture_floor_verdict(
        INSTALL - 299, "cli_observed", INSTALL)[0] == VERDICT_PASSED
    verdict, why = client_capture_floor_verdict(
        INSTALL - 301, "cli_observed", INSTALL)
    assert verdict == VERDICT_FAILED
    assert why, "a rejection must always carry a reason"
    # the boundary is exclusive: exactly AT the floor does not pass.
    assert client_capture_floor_verdict(
        INSTALL - 300, "cli_observed", INSTALL)[0] == VERDICT_FAILED


def test_store_proven_timestamp_floor_rejects_a_pre_install_row():
    """The pre-install NEGATIVE FIXTURE (piece 12): a row captured an hour
    before the install must not satisfy the floor. This is the case the whole
    client-clock decision exists for — the drain ships exactly such rows AFTER
    an install, and their server ``capturedAt`` is now."""
    verdict, why = client_capture_floor_verdict(
        INSTALL - 3600, "file_mtime", INSTALL)
    assert verdict == VERDICT_FAILED
    assert "behind" in why


def test_floor_mutation_control():
    """Remove the floor and the verdict MUST change. A check whose verdict does
    not move when the floor is deleted is not testing the floor."""
    pre_install = INSTALL - 3600
    assert client_capture_floor_verdict(
        pre_install, "file_mtime", INSTALL)[0] == VERDICT_FAILED
    # tolerance=inf is the floor deleted: every instant clears it.
    assert client_capture_floor_verdict(
        pre_install, "file_mtime", INSTALL,
        tolerance=float("inf"))[0] == VERDICT_PASSED


def test_unknown_source_can_never_pass_the_floor():
    """The falsifiability property itself: an admitted backfill gap is DISABLED,
    never a pass — even when its instant would satisfy the comparison."""
    verdict, why = client_capture_floor_verdict(INSTALL + 1, "unknown", INSTALL)
    assert verdict == VERDICT_DISABLED
    assert verdict != VERDICT_PASSED
    assert "unknown" in why
    # and an un-evaluable floor is DISABLED, never a pass.
    assert client_capture_floor_verdict(None, None, INSTALL)[0] == VERDICT_DISABLED
    assert client_capture_floor_verdict(
        INSTALL + 1, "cli_observed", None)[0] == VERDICT_DISABLED


# ── the producer leg: the stamp survives the spool, absent stays absent ───


def test_spool_entry_keeps_the_stamp_and_a_restamp_free_re_snapshot(tmp_path):
    """The spool is what actually gets delivered, so the stamp must survive it:
    stored on the entry, and NOT erased by a later re-snapshot that carries no
    stamp (the byte-identical-turns path)."""
    from tortoise.capture_spool import Snapshot, read_spool_meta, write_spool_entry

    root = tmp_path / "spool"
    sid = "3516-spool-stamp"
    write_spool_entry(root, Snapshot(
        session_id=sid, turns=[{"role": "user", "content": "hi"}],
        source="t", machine_id="m", harness="claude", capture_lane="hook",
        client_captured_at=1234.5,
        client_captured_at_source="cli_observed"))
    meta = read_spool_meta(root, sid)
    assert meta["client_captured_at"] == pytest.approx(1234.5)
    assert meta["client_captured_at_source"] == "cli_observed"

    # An EXTRA turn, deliberately: a byte-identical re-snapshot takes the dedup
    # early-return ABOVE the merge, so it would exercise nothing here — proved by
    # mutation, nulling the first-writer-wins rule left this file green. The grow
    # path is what actually runs the merge.
    write_spool_entry(root, Snapshot(
        session_id=sid,
        turns=[{"role": "user", "content": "hi"},
               {"role": "assistant", "content": "hello"}],
        source="t", machine_id="m", harness="claude", capture_lane="hook"))
    assert read_spool_meta(root, sid)["client_captured_at"] == pytest.approx(1234.5), (
        "a stamp-less re-snapshot erased the client stamp")


def test_spool_entry_without_a_stamp_has_none(tmp_path):
    """Set-only-when-present on the producer leg too — a pre-#3516 entry must
    not acquire an instant it never observed."""
    from tortoise.capture_spool import Snapshot, read_spool_meta, write_spool_entry

    root = tmp_path / "spool"
    sid = "3516-spool-nostamp"
    write_spool_entry(root, Snapshot(
        session_id=sid, turns=[{"role": "user", "content": "hi"}],
        source="t", machine_id="m", harness="claude", capture_lane="hook"))
    meta = read_spool_meta(root, sid)
    assert "client_captured_at" not in meta
    assert "client_captured_at_source" not in meta


def test_a_stamp_appearing_after_a_filing_is_re_delivered(tmp_path):
    """The stamp is METADATA, not content — exactly like the lane — so an entry
    already filed must be RE-POSTED when a stamp appears. Without this the
    stamp is stranded on the spool forever and the floor reads DISABLED for
    precisely the session the floor exists for. This is the same defect the
    LANE field forced this codebase to fix (`fix(capture): the drain CAS must
    include the lane`), and it is asserted on the WIRE — the payload actually
    posted — not on the helper."""
    from tortoise.capture_spool import (
        PostOutcome,
        Snapshot,
        flush_spool,
        write_spool_entry,
    )

    root = tmp_path / "spool"
    sid = "3516-late-stamp"
    turns = [{"role": "user", "content": "hi"}]
    posted: list[dict] = []

    def post(payload):
        posted.append(payload)
        return PostOutcome(ok=True, status=200, body={"session_id": sid})

    # 1. filed with a lane but NO stamp.
    write_spool_entry(root, Snapshot(
        session_id=sid, turns=turns, source="t", machine_id="m",
        harness="claude", capture_lane="hook"))
    flush_spool(root, post)
    assert len(posted) == 1, posted
    assert "client_captured_at" not in posted[0]

    # 2. a byte-identical re-snapshot that DOES carry a stamp.
    write_spool_entry(root, Snapshot(
        session_id=sid, turns=turns, source="t", machine_id="m",
        harness="claude", capture_lane="hook",
        client_captured_at=1234.5, client_captured_at_source="cli_observed"))
    flush_spool(root, post)
    assert len(posted) == 2, (
        "the stamped entry was SKIPPED — the stamp is stranded on the spool")
    assert posted[1]["client_captured_at"] == pytest.approx(1234.5)
    assert posted[1]["client_captured_at_source"] == "cli_observed"

    # 3. and now the filing marker covers the stamp: a third flush is a no-op.
    flush_spool(root, post)
    assert len(posted) == 2, (
        "the stamped entry was re-posted forever — the marker never captured it")


def test_non_finite_instant_is_refused_at_the_boundary():
    """An inf/nan instant makes the floor PASS on a value that is not a clock
    reading, and `1e400` parses to `inf`. Refused at the boundary."""
    from tortoise.hosted_api import SessionRequest

    for bad in (float("inf"), float("-inf"), float("nan")):
        with pytest.raises(ValidationError):
            SessionRequest(conversation=[{"role": "user", "content": "x"}],
                           client_captured_at=bad,
                           client_captured_at_source="cli_observed")


def test_absent_source_passes_where_unknown_does_not():
    """Deliberate, not an oversight. piece 12's row 1 gives the in-process
    recorder 'none — the recorder always has a clock', so an ABSENT source is a
    pass while the explicit 'unknown' is not. Pinned here so the distinction is
    a recorded decision rather than an accident of the fall-through."""
    assert client_capture_floor_verdict(
        INSTALL + 1, None, INSTALL)[0] == VERDICT_PASSED
    assert client_capture_floor_verdict(
        INSTALL + 1, "unknown", INSTALL)[0] == VERDICT_DISABLED


def test_install_probe_iso_string_is_coerced_not_passed_raw():
    """The probe endpoint records an ISO-8601 STRING, so passing it straight in
    as `install_at` would raise on the subtraction. The conversion is named and
    an unparseable value degrades to DISABLED, never to a failure or a pass."""
    from tortoise.capture_install import install_at_unix

    iso = "2026-10-08T00:00:00+00:00"
    assert install_at_unix(iso) == pytest.approx(
        datetime.fromisoformat(iso).timestamp())
    assert install_at_unix("1970-01-01T00:00:00Z") == pytest.approx(0.0)
    assert install_at_unix(None) is None
    assert install_at_unix("not-a-date") is None
    # the coerced value is usable by the floor; the raw string is not.
    assert client_capture_floor_verdict(
        INSTALL + 1, "cli_observed", install_at_unix(iso))[0] in (
            VERDICT_PASSED, VERDICT_FAILED)


# ── the stamp is a PAIR: an instant WITH its own clock ────────────────────


def test_spool_never_relabels_an_instant_with_another_writers_clock(tmp_path):
    """The instant and its source resolve TOGETHER, never independently.

    A source names the clock that PRODUCED the instant. Resolved separately, a
    later writer attaches its own clock to an earlier writer's instant — and
    because `unknown` DISABLES the floor while an absent source PASSES it, that
    relabelling flips a verifiable session to unverifiable. Reproduced case: the
    Pi recorder's own clock (no source — piece 12 row 1) followed by a backfill
    that admits `unknown`.

    MUTATION THAT REDS THIS: resolve the source with its own `or`/`||`.
    """
    from tortoise.capture_spool import Snapshot, read_spool_meta, write_spool_entry

    root = tmp_path / "spool"
    sid = "3516-spool-pair"
    # (1) the Pi recorder: its own clock, NO source (piece 12 row 1).
    write_spool_entry(root, Snapshot(
        session_id=sid, turns=[{"role": "user", "content": "hi"}],
        source="t", machine_id="m", harness="pi", capture_lane="hook",
        client_captured_at=INSTALL + 10.0))
    # (2) a later backfill ships the SAME session, admitting `unknown`.
    write_spool_entry(root, Snapshot(
        session_id=sid,
        turns=[{"role": "user", "content": "hi"},
               {"role": "assistant", "content": "hello"}],
        source="t", machine_id="m", harness="pi", capture_lane="store_sync",
        client_captured_at=INSTALL + 20.0, client_captured_at_source="unknown"))

    meta = read_spool_meta(root, sid)
    assert meta["client_captured_at"] == pytest.approx(INSTALL + 10.0), (
        "the hook's instant was replaced by the later writer's")
    assert meta.get("client_captured_at_source") is None, (
        "the backfill's clock was attached to the hook's instant")
    verdict, reason = client_capture_floor_verdict(
        meta["client_captured_at"], meta.get("client_captured_at_source"), INSTALL)
    assert verdict == VERDICT_PASSED, (
        f"a real hook observation became unverifiable: {verdict} / {reason}")


def test_an_epoch_instant_is_a_value_not_an_absence(tmp_path):
    """`install_at_unix` deliberately returns `0.0` for the epoch, so `0.0` is a
    real reading the floor must see. Truthiness (`or`) calls it absent, and a
    later stamp-less snapshot then ERASES it — flipping an evaluable FAILED into
    a DISABLED.

    MUTATION THAT REDS THIS: `prior.get(k) or snapshot.k`.
    """
    from tortoise.capture_spool import Snapshot, read_spool_meta, write_spool_entry

    root = tmp_path / "spool"
    sid = "3516-spool-epoch"
    write_spool_entry(root, Snapshot(
        session_id=sid, turns=[{"role": "user", "content": "hi"}],
        source="t", machine_id="m", harness="pi",
        client_captured_at=0.0, client_captured_at_source="cli_observed"))
    assert read_spool_meta(root, sid)["client_captured_at"] == pytest.approx(0.0)

    write_spool_entry(root, Snapshot(
        session_id=sid,
        turns=[{"role": "user", "content": "hi"},
               {"role": "assistant", "content": "hello"}],
        source="t", machine_id="m", harness="pi"))
    assert read_spool_meta(root, sid)["client_captured_at"] == pytest.approx(0.0), (
        "the epoch stamp was read as an absence and erased")


def test_a_non_finite_instant_is_never_written_to_the_spool(tmp_path):
    """`json.dumps` emits the NON-STANDARD tokens `NaN`/`Infinity`, and the
    TypeScript leg reads meta with a raw `JSON.parse` — which THROWS on them. So
    a non-finite instant written here makes the other leg classify this entry
    `corrupt_entry` and delete its turn log.

    MUTATION THAT REDS THIS: drop the `math.isfinite` guard in `_finite_instant`.
    """
    from tortoise.capture_spool import Snapshot, read_spool_meta, write_spool_entry

    root = tmp_path / "spool"
    for sid, value in (("3516-nan", float("nan")),
                       ("3516-inf", float("inf")),
                       ("3516-ninf", float("-inf"))):
        write_spool_entry(root, Snapshot(
            session_id=sid, turns=[{"role": "user", "content": "hi"}],
            source="t", machine_id="m", harness="pi",
            client_captured_at=value,
            client_captured_at_source="cli_observed"))
        assert read_spool_meta(root, sid).get("client_captured_at") is None, (
            f"{value} was stored as a clock reading")

    # The artifact the OTHER leg actually parses: no non-standard token on disk.
    raw = "\n".join(p.read_text() for p in root.rglob("*") if p.is_file())
    assert "NaN" not in raw and "Infinity" not in raw, (
        "a non-standard JSON token reached the shared spool directory")


def test_an_unrecognised_clock_is_normalised_not_forwarded(tmp_path):
    """A source the server refuses is a 422, and the drain classifies a 422 as
    PERMANENT — it then unlinks the entry's turn log, which is the only copy on
    this machine. So the spool normalises an unrecognised token to `unknown`
    (which DISABLES the floor) instead of forwarding it. Proved end to end: the
    capture still reaches the wire and the transcript survives.

    MUTATION THAT REDS THIS: forward `snapshot.client_captured_at_source`
    verbatim.
    """
    import tortoise.capture_spool as spool

    root = tmp_path / "spool"
    sid = "3516-spool-badclock"
    turns = [{"role": "user", "content": "hi"}]
    spool.write_spool_entry(root, spool.Snapshot(
        session_id=sid, turns=turns, source="t", machine_id="m", harness="pi",
        client_captured_at=INSTALL + 10.0,
        client_captured_at_source="wall_clock"))

    meta = spool.read_spool_meta(root, sid)
    assert meta["client_captured_at_source"] == "unknown", (
        "an unrecognised clock was forwarded — that is a 422, and a 422 makes "
        "the drain discard the user's transcript")

    posted: list[dict] = []
    spool.flush_spool(
        root,
        lambda p: (posted.append(dict(p)),
                   spool.PostOutcome(ok=True, status=200))[1],
        only_session_id=sid)
    assert posted and posted[0]["client_captured_at_source"] == "unknown"
    assert spool.read_spool_turns(root, sid) == turns, (
        "the drain deleted the user's transcript")


def test_the_delivered_stamp_survives_a_rewrite_of_a_filed_entry(tmp_path):
    """The filing marker is CONTENT-derived; the stamp is METADATA. If a REWRITE
    of an already-filed entry drops `filed_stamp`, the skip clause sees
    `filed_stamp != client_captured_at` and re-POSTs a byte-identical,
    already-filed entry — the upload amplification #4714 guards against.

    The rewrite is reached with a TORN log: the stored turns no longer match the
    digest the meta recorded, so the entry is rewritten from the same snapshot.

    MUTATION THAT REDS THIS: drop the `prior_stamp_delivered` carry.
    """
    import tortoise.capture_spool as spool

    root = tmp_path / "spool"
    sid = "3516-spool-carry"
    turns = [{"role": "user", "content": "hi"},
             {"role": "assistant", "content": "hello"}]
    spool.write_spool_entry(root, spool.Snapshot(
        session_id=sid, turns=turns, source="t", machine_id="m", harness="pi",
        client_captured_at=INSTALL + 10.0,
        client_captured_at_source="cli_observed"))

    posts: list[dict] = []
    spool.flush_spool(
        root,
        lambda p: (posts.append(dict(p)),
                   spool.PostOutcome(ok=True, status=200))[1],
        only_session_id=sid)
    assert posts and spool.read_spool_meta(root, sid).get("filed_stamp") is not None, (
        "the first filing did not record the delivered stamp — nothing to prove")

    # Tear the log mid-record, then re-snapshot the identical turns.
    log = spool._log_path(root, sid)
    text = log.read_text()
    log.write_text(text[: max(1, int(len(text) * 0.6))], encoding="utf-8")
    spool.write_spool_entry(root, spool.Snapshot(
        session_id=sid, turns=turns, source="t", machine_id="m", harness="pi",
        client_captured_at=INSTALL + 10.0,
        client_captured_at_source="cli_observed"))

    meta = spool.read_spool_meta(root, sid)
    assert meta.get("filed_stamp") == pytest.approx(INSTALL + 10.0), (
        "the rewrite dropped the delivered stamp, so the entry re-POSTs forever")
    second: list[dict] = []
    spool.flush_spool(
        root,
        lambda p: (second.append(dict(p)),
                   spool.PostOutcome(ok=True, status=200))[1],
        only_session_id=sid)
    assert not second, "an already-filed entry was re-POSTed"


# ── the producer legs: the stamp must be STAMPED, not merely carried ──────


def test_cli_hook_leg_stamps_its_own_clock_on_the_wire(tmp_path, monkeypatch):
    """The shipped Claude CLI leg (`session capture`) claims `cli_observed` and
    an instant of its OWN observation — piece 12's row, and the floor's input.
    The server's ingest time is NOT it: the pre-existing spool drains after an
    install, so an ingest-stamped row would read as freshly captured and the
    floor could only ever pass.

    MUTATION THAT REDS THIS: delete either keyword in `_spool_transcript`.
    """
    from types import SimpleNamespace

    import tortoise.capture_spool as spool
    from tortoise import __main__ as cli

    root = tmp_path / "spool"
    monkeypatch.setattr(spool, "spool_dir", lambda: root)
    transcript = tmp_path / "transcript.txt"
    transcript.write_text("User: hello\nAssistant: hi\n", encoding="utf-8")
    args = SimpleNamespace(file=str(transcript), harness="claude",
                           session_id="3516-cli-stamp", model=None)

    before = datetime.now().timestamp()
    prep = cli._spool_transcript(args)
    assert prep["rc"] == 0, "the hook leg failed to spool the transcript"

    meta = spool.read_spool_meta(prep["root"], "3516-cli-stamp") or {}
    assert meta.get("client_captured_at_source") == "cli_observed"
    assert meta.get("client_captured_at") == pytest.approx(before, abs=120.0)

    posted: list[dict] = []
    spool.flush_spool(
        prep["root"],
        lambda p: (posted.append(dict(p)),
                   spool.PostOutcome(ok=True, status=200))[1],
        only_session_id="3516-cli-stamp")
    assert posted, "the spooled hook capture was never posted"
    assert posted[0].get("client_captured_at_source") == "cli_observed"
    assert posted[0].get("client_captured_at") == pytest.approx(before, abs=120.0)


def test_journal_fold_keeps_the_stamp_and_a_replay_recovers_it():
    """The journal fold must carry the stamp, or a journal-only replay loses it
    and the floor reads DISABLED for a session that WAS stamped. Drives both
    ends directly, because the fold is otherwise unreachable in tests.

    MUTATION THAT REDS THIS: drop the coalesce arms in `_fold_session_recorded`.
    """
    from tortoise.sdk import _write_session_and_turns

    sdk = ha_mod._make_sdk(namespace=TEST_ORG_ID)
    proj = sdk._get_proj()
    sid = "3516-journal-stamp"
    emitted: list[dict] = []
    _write_session_and_turns(
        proj, sdk, sid, [{"role": "user", "content": "hi"}],
        now="2026-10-04T00:00:00Z", harness="pi",
        client_captured_at=INSTALL + 10.0,
        client_captured_at_source="cli_observed",
        on_session_merged=emitted.append)
    assert any(ev.get("client_captured_at") == pytest.approx(INSTALL + 10.0)
               for ev in emitted), (
        "the writer dropped the stamp from its journal carrier")

    # Simulate a journal-only REPLAY: wipe the live node, then fold the journal.
    proj.g.query("MATCH (s:Session {id:$sid}) DELETE s", params={"sid": sid})
    for ev in emitted:
        proj._fold_session_recorded({"type": "SessionRecorded", **ev})
    assert _stamp(sdk, sid, "client_captured_at") == pytest.approx(INSTALL + 10.0)
    assert _stamp(sdk, sid, "client_captured_at_source") == "cli_observed"


# ── values that are PRESENT but are not readings ──────────────────────────


def test_install_at_unix_refuses_a_value_that_is_not_a_reading():
    """The one coercion the floor's setup depends on. A value that is present
    but is NOT a reading must come back `None` (DISABLED), never a number the
    floor compares: `inf` PASSED, `-inf` passed any negative floor, and a NAIVE
    datetime was read as LOCAL time — 5 h here, 60x the 300 s tolerance.

    MUTATION THAT REDS THIS: return `float(value)` for a non-finite number, or
    skip the `tzinfo is None` refusal.
    """
    from tortoise.capture_install import install_at_unix

    assert install_at_unix(float("nan")) is None
    assert install_at_unix(float("inf")) is None
    assert install_at_unix(float("-inf")) is None
    assert install_at_unix("2026-10-08T00:00:00") is None, (
        "a naive datetime is read as LOCAL time, which shifts the floor past "
        "the tolerance and silently flips the verdict")
    assert install_at_unix(True) is None


def test_floor_is_disabled_when_install_at_is_not_an_observation():
    """A zero install time yields `floor = -tolerance`, which EVERY client
    clock passes — a floor-pass on the absence of a floor.

    MUTATION THAT REDS THIS: drop the `install_at <= 0` refusal.
    """
    for value in (0.0, -1.0):
        verdict, reason = client_capture_floor_verdict(
            INSTALL, "cli_observed", value)
        assert verdict == VERDICT_DISABLED, reason


def test_boundary_refuses_a_bool_instant():
    """Pydantic coerces `true` to `1.0` (a bool is an `int` subclass), storing a
    clock reading that is not one.

    MUTATION THAT REDS THIS: drop the `isinstance(v, bool)` guard.
    """
    from tortoise.hosted_api import SessionRequest

    with pytest.raises(ValidationError):
        SessionRequest(conversation=[{"role": "user", "content": "x"}],
                       client_captured_at=True)


# ── the pair must hold at the SINK too, not only in the spool ─────────────


def test_the_server_keeps_the_pair_when_two_writers_disagree():
    """The spool is only ONE path in; `POST /v1/sessions` is where every writer
    converges, so the pair has to hold there as well.

    Reproduced defect: a first write storing an instant with NO source (the Pi
    recorder's shape), then a second carrying its OWN instant plus 'unknown',
    left the FIRST instant labelled with the SECOND writer's clock — flipping
    the floor from PASSED to DISABLED for the same instant.

    MUTATION THAT REDS THIS: coalesce the two fields independently.
    """
    from tortoise.sdk import _write_session_and_turns

    sdk = ha_mod._make_sdk(namespace=TEST_ORG_ID)
    sid = "3516-server-pair"
    turns = [{"role": "user", "content": "hi"}]
    _write_session_and_turns(sdk._get_proj(), sdk, sid, turns,
                             now="2026-10-08T00:00:00Z", harness="pi",
                             client_captured_at=INSTALL + 10.0)
    _write_session_and_turns(sdk._get_proj(), sdk, sid, turns,
                             now="2026-10-08T00:00:01Z", harness="pi",
                             client_captured_at=INSTALL + 20.0,
                             client_captured_at_source="unknown")

    assert _stamp(sdk, sid, "client_captured_at") == pytest.approx(INSTALL + 10.0)
    assert _stamp(sdk, sid, "client_captured_at_source") is None, (
        "the second writer's clock was hung on the first writer's instant")
    verdict, reason = client_capture_floor_verdict(
        _stamp(sdk, sid, "client_captured_at"),
        _stamp(sdk, sid, "client_captured_at_source"), INSTALL)
    assert verdict == VERDICT_PASSED, f"{verdict} / {reason}"


def test_the_journal_fold_keeps_the_pair_when_events_disagree():
    """The replay path carries the same rule: a replayed event must not hang its
    own clock on an earlier event's instant.

    Built from REAL carriers (two writes, each self-consistent) so the test
    exercises the fold's own resolution rather than a hand-built event.

    MUTATION THAT REDS THIS: coalesce the two fields independently in
    `_fold_session_recorded`.
    """
    from tortoise.sdk import _write_session_and_turns

    sdk = ha_mod._make_sdk(namespace=TEST_ORG_ID)
    proj = sdk._get_proj()
    sid = "3516-fold-pair"
    turns = [{"role": "user", "content": "hi"}]
    emitted: list[dict] = []
    _write_session_and_turns(proj, sdk, sid, turns, now="2026-10-08T00:00:00Z",
                             harness="pi", client_captured_at=INSTALL + 10.0,
                             on_session_merged=emitted.append)
    _write_session_and_turns(proj, sdk, sid, turns, now="2026-10-08T00:00:01Z",
                             harness="pi", client_captured_at=INSTALL + 20.0,
                             client_captured_at_source="unknown",
                             on_session_merged=emitted.append)
    assert emitted, "the writer emitted no journal carrier at all"

    # Simulate a journal-only REPLAY: wipe the live node, then fold the journal.
    proj.g.query("MATCH (s:Session {id:$sid}) DELETE s", params={"sid": sid})
    for ev in emitted:
        proj._fold_session_recorded({"type": "SessionRecorded", **ev})

    assert _stamp(sdk, sid, "client_captured_at") == pytest.approx(INSTALL + 10.0)
    assert _stamp(sdk, sid, "client_captured_at_source") is None, (
        "a replayed event's clock was hung on an earlier event's instant")


# ── values that make the coercions RAISE instead of refusing ──────────────


def test_a_huge_integer_instant_is_refused_not_raised(tmp_path):
    """`float(10**400)` raises OverflowError, and a 400-digit JSON integer is a
    legal request body. Refusing it must not become a 500 at the boundary, must
    not abort a spool write, and must not break `install_at_unix`'s contract.

    MUTATION THAT REDS THIS: drop any of the three `OverflowError` guards.
    """
    from tortoise.capture_install import install_at_unix
    from tortoise.capture_spool import Snapshot, read_spool_meta, write_spool_entry
    from tortoise.hosted_api import SessionRequest

    huge = 10 ** 400
    with pytest.raises(ValidationError):
        SessionRequest(conversation=[{"role": "user", "content": "x"}],
                       client_captured_at=huge)
    assert install_at_unix(huge) is None

    root = tmp_path / "spool"
    write_spool_entry(root, Snapshot(
        session_id="3516-huge", turns=[{"role": "user", "content": "hi"}],
        source="t", machine_id="m", harness="pi", client_captured_at=huge))
    assert read_spool_meta(root, "3516-huge").get("client_captured_at") is None


def test_an_unhashable_source_is_normalised_not_crashed(tmp_path):
    """A non-string source (a corrupt or hand-edited meta) would RAISE on the
    membership test, aborting the capture; the TS twin's `Set.has` accepts any
    value, so crashing here is also a leg divergence.

    MUTATION THAT REDS THIS: `source in CLIENT_CAPTURED_AT_SOURCES` unguarded.
    """
    from tortoise.capture_spool import Snapshot, read_spool_meta, write_spool_entry

    root = tmp_path / "spool"
    for sid, source in (("3516-list", ["wall_clock"]), ("3516-dict", {"a": 1})):
        write_spool_entry(root, Snapshot(
            session_id=sid, turns=[{"role": "user", "content": "hi"}],
            source="t", machine_id="m", harness="pi",
            client_captured_at=INSTALL + 10.0,
            client_captured_at_source=source))
        assert read_spool_meta(root, sid)["client_captured_at_source"] == "unknown"


def test_the_drain_normalises_a_corrupt_stored_source(tmp_path):
    """The last line before the wire. A meta carrying an unrecognised token must
    not reach the server: the refusal is a 422, a 422 is PERMANENT, and the drain
    then unlinks the entry's turn log.

    MUTATION THAT REDS THIS: forward `meta[\"client_captured_at_source\"]`
    verbatim at the drain.
    """
    import json as _json

    import tortoise.capture_spool as spool

    root = tmp_path / "spool"
    sid = "3516-drain-corrupt"
    turns = [{"role": "user", "content": "hi"}]
    spool.write_spool_entry(root, spool.Snapshot(
        session_id=sid, turns=turns, source="t", machine_id="m", harness="pi",
        client_captured_at=INSTALL + 10.0,
        client_captured_at_source="cli_observed"))

    # Corrupt the stored token, the way a foreign or older writer could.
    path = spool._meta_path(root, sid)
    raw = _json.loads(path.read_text())
    raw["client_captured_at_source"] = "wall_clock"
    path.write_text(_json.dumps(raw), encoding="utf-8")

    posted: list[dict] = []
    spool.flush_spool(
        root,
        lambda p: (posted.append(dict(p)),
                   spool.PostOutcome(ok=True, status=200))[1],
        only_session_id=sid)
    assert posted and posted[0]["client_captured_at_source"] == "unknown"
    assert spool.read_spool_turns(root, sid) == turns, (
        "the drain deleted the user's transcript")


def test_a_refused_instant_does_not_repost_an_already_filed_entry(tmp_path):
    """A value the guards REFUSE must not count as 'newly stamped'. It would
    bypass the dedup early-return AND skip the filing-marker carry, so an
    already-filed, byte-identical entry is re-POSTed — while no stamp is written
    at all. The TS leg already judges this with its `finiteInstant`.

    MUTATION THAT REDS THIS: `snapshot.client_captured_at is not None`.
    """
    import tortoise.capture_spool as spool

    root = tmp_path / "spool"
    sid = "3516-refused-upgrade"
    turns = [{"role": "user", "content": "hi"}]
    spool.write_spool_entry(root, spool.Snapshot(
        session_id=sid, turns=turns, source="t", machine_id="m"))

    posts: list[dict] = []
    spool.flush_spool(
        root,
        lambda p: (posts.append(dict(p)),
                   spool.PostOutcome(ok=True, status=200))[1],
        only_session_id=sid)
    assert posts and spool.read_spool_meta(root, sid).get("filed_key"), (
        "the first filing did not stamp a marker — the test cannot prove anything")

    res = spool.write_spool_entry(root, spool.Snapshot(
        session_id=sid, turns=turns, source="t", machine_id="m",
        client_captured_at=float("nan")))
    assert res["written"] is False, (
        "a refused instant was treated as a stamp upgrade")
    assert spool.read_spool_meta(root, sid).get("filed_key"), (
        "a refused instant dropped the filing marker")

    second: list[dict] = []
    spool.flush_spool(
        root,
        lambda p: (second.append(dict(p)),
                   spool.PostOutcome(ok=True, status=200))[1],
        only_session_id=sid)
    assert not second, "an already-filed entry was re-POSTed"


def test_floor_refuses_a_non_finite_client_instant():
    """`inf` would PASS every finite floor and `nan` would fail every one, both
    on a value that is not a clock. The writers refuse these; a direct caller of
    the floor must not slip past — and must not CRASH either, because
    `math.isfinite(10**400)` raises.

    MUTATION THAT REDS THIS: drop the `math.isfinite` guard, or its
    `OverflowError` arm.
    """
    for value in (float("inf"), float("-inf"), float("nan"), 10 ** 400):
        verdict, reason = client_capture_floor_verdict(
            value, "cli_observed", INSTALL)
        assert verdict == VERDICT_DISABLED, f"{value!r}: {verdict} / {reason}"


def test_floor_refuses_a_non_numeric_or_bool_instant():
    """The guard must refuse — not crash — for every present-but-not-a-reading
    shape. A non-numeric string raises `TypeError` from `math.isfinite`, and a
    `bool` is an `int` subclass so `isfinite` ACCEPTS it and `True` would be
    compared as the instant `1.0`. Both must be DISABLED.

    MUTATION THAT REDS THIS: catch only `OverflowError`, or drop the bool guard.
    """
    for client in ("123", b"123", True, False):
        verdict, reason = client_capture_floor_verdict(
            client, "cli_observed", INSTALL)
        assert verdict == VERDICT_DISABLED, f"{client!r}: {verdict} / {reason}"
    for install in (True, float("inf")):
        verdict, reason = client_capture_floor_verdict(
            INSTALL + 1, "cli_observed", install)
        assert verdict == VERDICT_DISABLED, f"install={install!r}: {verdict} / {reason}"


def test_the_drain_and_the_writer_agree_on_a_falsy_stored_source(tmp_path):
    """One predicate, one normalisation, on both legs. The writer already turns a
    falsy source into `unknown`; a truthy guard at the drain then DROPPED it, so
    the same on-disk entry produced opposite verdicts depending on which leg
    drained it — Python omitted the key (absent PASSES) while the TS leg posted
    `unknown` (which DISABLES).

    MUTATION THAT REDS THIS: `if _drain_src:` at the drain.
    """
    import json as _json

    import tortoise.capture_spool as spool

    root = tmp_path / "spool"
    sid = "3516-falsy-src"
    turns = [{"role": "user", "content": "hi"}]
    spool.write_spool_entry(root, spool.Snapshot(
        session_id=sid, turns=turns, source="t", machine_id="m", harness="pi",
        client_captured_at=INSTALL + 10.0,
        client_captured_at_source="cli_observed"))

    posted: list[dict] = []

    def _post(payload):
        posted.append(dict(payload))
        return spool.PostOutcome(ok=True, status=200)

    for falsy in ("", 0, False):
        path = spool._meta_path(root, sid)
        raw = _json.loads(path.read_text())
        raw["client_captured_at_source"] = falsy
        raw.pop("filed_key", None)
        raw.pop("filed_stamp", None)
        path.write_text(_json.dumps(raw), encoding="utf-8")

        posted.clear()
        spool.flush_spool(root, _post, only_session_id=sid)
        assert posted, f"{falsy!r}: nothing was posted"
        assert posted[0].get("client_captured_at_source") == "unknown", (
            f"{falsy!r}: the drain dropped a source the writer normalises to "
            "'unknown' — the two legs would report opposite floor verdicts")


def test_the_journal_fold_matches_the_sink_on_a_falsy_source():
    """The fold must use the same predicate the sink does, or a rebuild disagrees
    with the thing it rebuilds: the sink stores NULL for `""`/`0`/`False`, so the
    fold must not keep them.

    MUTATION THAT REDS THIS: `_cap_src is not None` in `_fold_session_recorded`.
    """
    sdk = ha_mod._make_sdk(namespace=TEST_ORG_ID)
    proj = sdk._get_proj()
    sid = "3516-fold-falsy"
    proj._fold_session_recorded({
        "type": "SessionRecorded", "id": sid, "harness": "pi",
        "client_captured_at": INSTALL + 10.0,
        "client_captured_at_source": ""})
    assert _stamp(sdk, sid, "client_captured_at") == pytest.approx(INSTALL + 10.0)
    assert _stamp(sdk, sid, "client_captured_at_source") is None, (
        "the fold kept a falsy source the live sink stores as NULL")


def test_a_refused_stored_instant_does_not_repost_forever(tmp_path):
    """The skip clause and the success CAS must compare the SAME normalisation
    the payload POSTS. `filed_stamp` only ever holds a normalised value, so
    comparing it against the RAW disk value can never match — and the drain never
    rewrites the meta, so the entry would re-POST its full transcript on EVERY
    drain, forever (#4714 amplification).

    MUTATION THAT REDS THIS: compare `meta[\"client_captured_at\"]` raw in the
    skip clause / CAS.
    """
    import json as _json

    import tortoise.capture_spool as spool

    posts: list[dict] = []

    def _post(payload):
        posts.append(dict(payload))
        return spool.PostOutcome(ok=True, status=200)

    for sid, bad in (("3516-repost-str", "1700000000.5"),
                     ("3516-repost-huge", 10 ** 400)):
        root = tmp_path / sid
        turns = [{"role": "user", "content": "hi"}]
        spool.write_spool_entry(root, spool.Snapshot(
            session_id=sid, turns=turns, source="t", machine_id="m",
            harness="pi", client_captured_at=INSTALL + 10.0,
            client_captured_at_source="cli_observed"))

        # Corrupt the stored instant the way a foreign or older writer could.
        path = spool._meta_path(root, sid)
        raw = _json.loads(path.read_text())
        raw["client_captured_at"] = bad
        path.write_text(_json.dumps(raw), encoding="utf-8")

        before = len(posts)
        for _ in range(3):
            spool.flush_spool(root, _post, only_session_id=sid)
        assert len(posts) - before == 1, (
            f"{bad!r}: the entry re-POSTed {len(posts) - before} times — the "
            "normalised value never matches the raw one, so it is never marked "
            "filed")
        assert spool.read_spool_meta(root, sid).get("filed_key")


def test_floor_keeps_the_pass_for_a_small_positive_install_time():
    """Deliberately NOT a defect, and pinned so it is not "fixed" later: a small
    but POSITIVE install time means the capture genuinely happened after the
    install, so PASS is the correct answer. The guard is on the ABSENT encoding
    (epoch or earlier), not on the floor's sign — extending it would also destroy
    `tolerance=inf`, the only way to express "the floor is deleted"
    (`test_floor_mutation_control`)."""
    verdict, reason = client_capture_floor_verdict(
        0.0, "cli_observed", FLOOR_SKEW_TOLERANCE_S / 2)
    assert verdict == VERDICT_PASSED, reason
