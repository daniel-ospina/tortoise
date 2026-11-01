"""#6388 — the session-events DRAIN: bounded, observable, and poison-proof.

The discriminating test is
``test_a_poison_record_is_dead_lettered_without_blocking_the_ones_behind_it``:
a naive in-order drain stalls at the record that always fails, and every record
after it stays unfiled forever — which is what a 61,997-record backlog with no
drain actually looks like from the operator's side.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tortoise.capture_spool import PostOutcome
from tortoise.session_events_drain import (
    CURSOR_FILE,
    DEAD_LETTER_FILE,
    MAX_RECORDS_PER_RUN,
    drain_session_events,
    format_progress,
    read_dead_letter_count,
)


def _record(session_id: str, turns: int = 2) -> dict:
    return {
        "session_id": session_id,
        "conversation": [
            {"role": "user" if i % 2 == 0 else "assistant",
             "content": f"turn {i} of {session_id}"}
            for i in range(turns)
        ],
        "metadata": {"source": "pi-session-quit"},
        "harness": "pi",
        "machine_id": "m" * 64,
        "model": "deepseek/deepseek-flash",
    }


def _write_day(root: Path, name: str, records: list[dict | str]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / name
    body = "".join((r if isinstance(r, str) else json.dumps(r)) + "\n"
                   for r in records)
    path.write_text(body, encoding="utf-8")
    return path


class Recorder:
    """A transport that records payloads and answers from a script."""

    def __init__(self, statuses: dict[str, PostOutcome | None] | None = None,
                 default: PostOutcome | None = None) -> None:
        self.posted: list[dict] = []
        self.statuses = statuses or {}
        self.default = default if default is not None else PostOutcome(ok=True, status=201)

    def __call__(self, payload: dict) -> PostOutcome:
        self.posted.append(payload)
        sid = payload.get("session_id") or ""
        if sid in self.statuses:
            return self.statuses[sid]  # type: ignore[return-value]
        return self.default

    @property
    def session_ids(self) -> list[str]:
        return [p.get("session_id") or "" for p in self.posted]


def _drain(root: Path, store: Path, post, **kw) -> dict:
    return drain_session_events(post, root=root, store=store, **kw)


def test_the_backlog_reaches_zero_filed_or_dead_lettered_over_bounded_runs(
        tmp_path: Path) -> None:
    """N=11 records across 2 day files drain to 0 queued in ceil(N/cap) runs."""
    root, store = tmp_path / "session-events", tmp_path / "store-sync"
    _write_day(root, "2026-10-01.jsonl", [_record(f"s{i}") for i in range(6)])
    _write_day(root, "2026-10-02.jsonl", [_record(f"s{i}") for i in range(6, 11)])
    post = Recorder()

    runs = []
    for _ in range(5):  # M bounded runs, each capped at 3 records
        runs.append(_drain(root, store, post, max_records=3))

    assert [r["filed"] for r in runs] == [3, 3, 3, 2, 0]
    assert [r["stopped"] for r in runs] == ["batch", "batch", "batch", None, None]
    assert sum(r["filed"] for r in runs) == 11
    assert runs[-1]["queued"] == 0
    # NOTHING is dropped to make the count fall: every record is on the wire,
    # once, and the source files are untouched (#3870 — no deletion).
    assert len(post.posted) == 11
    assert sorted(post.session_ids) == sorted(f"s{i}" for i in range(11))
    assert len(list(root.glob("*.jsonl"))) == 2
    assert (store / CURSOR_FILE).exists()
    assert runs[-1]["dead_lettered"] == 0


def test_a_poison_record_is_dead_lettered_without_blocking_the_ones_behind_it(
        tmp_path: Path) -> None:
    """THE discriminating test: the always-failing record must not gate the queue."""
    root, store = tmp_path / "session-events", tmp_path / "store-sync"
    records = [_record(f"s{i}") for i in range(6)]
    poison = "s2"
    _write_day(root, "2026-10-01.jsonl", records)
    # A PERMANENT refusal (a malformed payload the server will never accept).
    post = Recorder({poison: PostOutcome(ok=False, status=422, detail="bad lane")})

    summary = _drain(root, store, post)

    assert summary["dead_lettered"] == 1
    # Every record AFTER the poison one was still filed — a naive in-order drain
    # stops here and leaves s3..s5 unfiled forever.
    assert summary["filed"] == 5
    assert post.session_ids == ["s0", "s1", poison, "s3", "s4", "s5"]

    rows = [json.loads(line) for line in
            (store / DEAD_LETTER_FILE).read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(rows) == 1
    assert rows[0]["sessionId"] == poison
    assert rows[0]["reason"] == "http_rejected"
    assert rows[0]["status"] == 422
    assert rows[0]["path"] == "2026-10-01#2"  # keyed by DAY, not by path
    # The poison record is NOT deleted from the substrate — the dead letter is a
    # filing verdict, never a drop.
    assert len(_read_records(root / "2026-10-01.jsonl")) == 6

    # And it does not re-fail on the next pass: the cursor crossed it.
    second = _drain(root, store, post)
    assert second["dead_lettered"] == 0
    assert second["filed"] == 0
    assert second["queued"] == 0


def test_a_transient_refusal_stops_the_pass_and_loses_nothing(tmp_path: Path) -> None:
    """A rate-limited API ends the pass; the record is retried, never crossed."""
    root, store = tmp_path / "session-events", tmp_path / "store-sync"
    _write_day(root, "2026-10-01.jsonl", [_record(f"s{i}") for i in range(4)])
    refused = Recorder({f"s{i}": PostOutcome(ok=False, status=429, detail="slow down")
                        for i in range(4)})

    first = _drain(root, store, refused, max_records=4)
    assert first["stopped"] == "refused"
    assert first["filed"] == 0
    assert first["deferred"] == 1
    # 4 records, one attempted and deferred ⇒ 4 still queued (the deferred one
    # included: it was NOT consumed).
    assert first["queued"] == 4

    ok = Recorder()
    second = _drain(root, store, ok, max_records=4)
    assert second["filed"] == 4
    assert second["queued"] == 0


@pytest.mark.parametrize("bad_line,reason", [
    ("{not json", "unparseable_record"),
    (json.dumps({"conversation": [{"role": "user", "content": "x"}]}), "no_session_id"),
    (json.dumps({"session_id": "s-empty", "conversation": []}), "no_conversation"),
])
def test_an_unfileable_record_is_dead_lettered_and_crossed(
        tmp_path: Path, bad_line: str, reason: str) -> None:
    """Every non-fileable shape ends in the dead-letter surface, never in limbo."""
    root, store = tmp_path / "session-events", tmp_path / "store-sync"
    _write_day(root, "2026-10-01.jsonl", [bad_line, _record("s-good")])
    post = Recorder()

    summary = _drain(root, store, post)

    assert summary["dead_lettered"] == 1
    assert summary["filed"] == 1
    assert summary["queued"] == 0
    row = json.loads((store / DEAD_LETTER_FILE).read_text(encoding="utf-8").splitlines()[0])
    assert row["reason"] == reason
    # the same dead letter does not accumulate a row per run (fold by path)
    _drain(root, store, post)
    assert read_dead_letter_count(store) == 1


def test_a_sealed_archive_day_is_still_drained(tmp_path: Path) -> None:
    """A day sealed by tools/seal_session_events.py must not be skip-shaped.

    Reading only ``*.jsonl`` would leave every sealed day in a state that is
    neither filed nor dead-lettered — the exact state this drain forbids.
    """
    import gzip

    root, store = tmp_path / "session-events", tmp_path / "store-sync"
    archive = root / "archive"
    archive.mkdir(parents=True)
    with gzip.open(archive / "2026-09-30.jsonl.gz", "wt", encoding="utf-8") as fh:
        fh.write(json.dumps(_record("sealed-1")) + "\n")
    post = Recorder()

    summary = _drain(root, store, post)

    assert summary["files"] == 1
    assert summary["filed"] == 1
    assert post.session_ids == ["sealed-1"]


def test_a_plain_file_that_replaces_a_drained_archive_is_not_skipped(
        tmp_path: Path) -> None:
    """P1 (reproduced): the DUAL-SHAPE day silently skipped every new record.

    The cursor's ``consumed`` is an index into a DAY, but a day has two files.
    Seal the day, drain the archive, then let a later session re-create the
    plain file for the same day (``tools/seal_session_events.py`` seals on the
    LOCAL clock while the producer names its file in UTC, so a UTC day already
    behind local ``today`` stays sealable after its settle grace). PLAIN WINS
    then reads the new file against the ARCHIVE's index space, so its first
    ``consumed`` records are skipped — neither filed nor dead-lettered, and
    ``queued`` reports 0, which is the state this module exists to forbid.

    Pre-fix this file fails at the first assert: ``filed == 0``, the wire is
    empty, and the LATE records are lost silently and stably across passes.
    """
    import gzip

    root, store = tmp_path / "session-events", tmp_path / "store-sync"
    (root / "archive").mkdir(parents=True)
    archived = [f"archived-{i}" for i in range(4)]
    with gzip.open(root / "archive" / "2026-10-01.jsonl.gz", "wt",
                   encoding="utf-8") as fh:
        for sid in archived:
            fh.write(json.dumps(_record(sid)) + "\n")

    first = Recorder()
    assert _drain(root, store, first)["filed"] == 4
    assert first.session_ids == archived

    # A session ending later that UTC day re-creates the plain file.
    late = [f"LATE-{i}" for i in range(4)]
    _write_day(root, "2026-10-01.jsonl", [_record(sid) for sid in late])

    second = Recorder()
    summary = _drain(root, store, second)

    assert summary["filed"] == 4
    assert second.session_ids == late
    assert summary["queued"] == 0

    # …and the reset does not turn into a re-POST storm on the next pass: the
    # re-filed day is now the plain file's index space and stays consumed.
    third = Recorder()
    again = _drain(root, store, third)
    assert third.session_ids == []
    assert again["filed"] == 0


def test_the_seal_of_a_drained_plain_day_does_not_re_file_it(tmp_path: Path) -> None:
    """The OTHER direction of the shape binding: plain → gz is the seal.

    A fix for the dual-shape skip that reset on ANY shape change would turn the
    seal into a re-POST of the whole day, so this is the guard against
    over-correcting: the archive is a gzip of the plain file just drained, so
    the same indices are already consumed.
    """
    import gzip

    root, store = tmp_path / "session-events", tmp_path / "store-sync"
    plain = _write_day(root, "2026-10-01.jsonl",
                       [_record(f"s{i}") for i in range(3)])
    first = Recorder()
    assert _drain(root, store, first)["filed"] == 3

    # tools/seal_session_events.py: gzip the settled day, then remove the plain.
    (root / "archive").mkdir(parents=True, exist_ok=True)
    with gzip.open(root / "archive" / "2026-10-01.jsonl.gz", "wt",
                   encoding="utf-8") as fh:
        fh.write(plain.read_text(encoding="utf-8"))
    plain.unlink()

    second = Recorder()
    summary = _drain(root, store, second)
    assert second.session_ids == []  # idempotent across the seal
    assert summary["filed"] == 0
    assert summary["queued"] == 0


def test_the_pass_is_bounded_and_says_how_much_is_left(tmp_path: Path) -> None:
    """One pass can never be an unbounded hammer: cap, cadence, queue depth."""
    root, store = tmp_path / "session-events", tmp_path / "store-sync"
    _write_day(root, "2026-10-01.jsonl", [_record(f"s{i}") for i in range(260)])
    post = Recorder()

    summary = _drain(root, store, post)

    assert len(post.posted) == MAX_RECORDS_PER_RUN
    assert summary["filed"] == MAX_RECORDS_PER_RUN
    assert summary["stopped"] == "batch"
    assert summary["queued"] == 260 - MAX_RECORDS_PER_RUN
    line = format_progress(summary)
    assert f"filed {MAX_RECORDS_PER_RUN}" in line
    assert "queued 60" in line
    assert "stopped batch" in line


def test_dry_run_posts_nothing_and_moves_no_cursor(tmp_path: Path) -> None:
    root, store = tmp_path / "session-events", tmp_path / "store-sync"
    _write_day(root, "2026-10-01.jsonl", [_record("s0")])
    post = Recorder()

    summary = _drain(root, store, post, dry_run=True)

    assert summary["status"] == "dry_run"
    assert post.posted == []
    assert not (store / CURSOR_FILE).exists()


def test_a_conversation_is_windows_to_the_last_500_turns(tmp_path: Path) -> None:
    """An over-long record is windowed, not refused (the server's 400 is permanent)."""
    root, store = tmp_path / "session-events", tmp_path / "store-sync"
    record = _record("s-long", turns=502)
    _write_day(root, "2026-10-01.jsonl", [record])
    post = Recorder()

    summary = _drain(root, store, post)

    assert summary["filed"] == 1
    turns = post.posted[0]["conversation"]
    assert len(turns) == 500
    assert turns[-1]["content"] == "turn 501 of s-long"


def test_the_window_obeys_the_SERVERS_bound_and_not_a_local_copy(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """#4897 / review ISSUE (b): the turn bound is ``quota.MAX_SESSION_TURNS``.

    A local re-declaration is not a style problem: ``hosted_api`` 400s above the
    server's bound and ``classify_failure`` reads that 400 as PERMANENT, so a
    copy that drifts LOWER dead-letters every record in the band on its first
    pass AND crosses the cursor. The bound is therefore read from its owner at
    call time — patching the owner must move this leg's window, which a bare
    ``MAX_TURNS = 500`` literal here cannot do.
    """
    import tortoise.quota

    root, store = tmp_path / "session-events", tmp_path / "store-sync"
    _write_day(root, "2026-10-01.jsonl", [_record("s-five", turns=5)])
    post = Recorder()
    monkeypatch.setattr(tortoise.quota, "MAX_SESSION_TURNS", 3)

    _drain(root, store, post)

    turns = post.posted[0]["conversation"]
    # The MOST RECENT ``MAX_SESSION_TURNS`` turns — not the first ones, and not
    # five of them because this module remembered its own number.
    assert [t["content"] for t in turns] == [
        "turn 2 of s-five", "turn 3 of s-five", "turn 4 of s-five"]


def test_an_over_cap_turn_is_CLIPPED_WITH_A_MARKER_never_silently_cut(
        tmp_path: Path) -> None:
    """#4897 / review ISSUE (a): the cut must be observable to a reader.

    The bare slice this leg used to apply cut to EXACTLY the cap, so the server's
    own re-windowing saw a turn that is not *over* the window and added no marker
    either: the stored turn was truncated with nothing telling a reader it had
    been cut. The SDK clip reserves its marker INSIDE the cap, so the mark
    survives the server's re-application.
    """
    from tortoise.sdk import _CAPTURE_TURN_CAP, _capture_truncation_marker

    root, store = tmp_path / "session-events", tmp_path / "store-sync"
    total = _CAPTURE_TURN_CAP + 77
    raw = "z" * total
    record = _record("s-wide")
    record["conversation"][0] = {"role": "user", "content": raw}
    _write_day(root, "2026-10-01.jsonl", [record])
    post = Recorder()

    _drain(root, store, post)

    content = post.posted[0]["conversation"][0]["content"]
    # Within the window the server accepts...
    assert len(content) <= _CAPTURE_TURN_CAP
    # ...the SILENT slice's output (the pre-fix behaviour, and what a marker-less
    # re-window would produce) is distinguishable from this one.
    assert content != raw[:_CAPTURE_TURN_CAP]
    # ...and the marker carries the TRUE original length, which the cut destroys.
    assert content.endswith(_capture_truncation_marker(total))


def test_a_turn_exactly_at_the_cap_is_NOT_marked(tmp_path: Path) -> None:
    """The companion claim: only an ACTUAL cut marks — a complete turn is complete.

    Guards the over-marking half of #4897's P1: a turn at the boundary is
    returned verbatim, so the marker means "there is more", never "we reached
    the limit".
    """
    from tortoise.sdk import _CAPTURE_TURN_CAP

    root, store = tmp_path / "session-events", tmp_path / "store-sync"
    exact = "w" * _CAPTURE_TURN_CAP
    record = _record("s-exact")
    record["conversation"][0] = {"role": "user", "content": exact}
    _write_day(root, "2026-10-01.jsonl", [record])
    post = Recorder()

    _drain(root, store, post)

    assert post.posted[0]["conversation"][0]["content"] == exact


def _read_records(path: Path) -> list[str]:
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_the_default_substrate_follows_the_spools_pytest_guard() -> None:
    """#3721: a suite run must never drain the developer machine's real backlog.

    Under pytest the spool root is derived from the test id; reading ``$HOME``
    directly ignored that and posted this machine's own day files through a
    stubbed transport.
    """
    from tortoise.capture_spool import spool_dir
    from tortoise.session_events_drain import default_session_events_dir

    if "PYTEST_CURRENT_TEST" not in os.environ:  # pragma: no cover
        pytest.skip("not running under pytest's own session")
    assert default_session_events_dir() == spool_dir().parent / "session-events"
    assert Path.home() / ".tortoise" / "session-events" != default_session_events_dir()


def test_a_dead_letter_that_cannot_be_written_is_a_deferral_not_a_verdict(
        tmp_path: Path) -> None:
    """P0 from review: an unwritable substrate must NOT cross the record.

    Reporting `dead_lettered` for a row that never landed leaves the record in
    neither state — the one limbo this module forbids.
    """
    root, store = tmp_path / "session-events", tmp_path / "store-sync"
    _write_day(root, "2026-10-01.jsonl", ["{not json", _record("s-good")])
    # A DIRECTORY in the dead-letter file's place makes every write fail.
    store.mkdir(parents=True)
    (store / DEAD_LETTER_FILE).mkdir()
    post = Recorder()

    summary = _drain(root, store, post)

    assert summary["dead_lettered"] == 0
    assert summary["stopped"] == "dead_letter_unwritable"
    assert summary["deferred"] >= 1
    assert summary["consumed"] == 0
    # The pass DEFERRED at the unfileable line — it did not cross it, and the
    # captured record behind it was not attempted either (the pass stopped).
    assert post.session_ids == []

    # Once the substrate is writable the SAME records resolve: one dead letter,
    # one filed — nothing was lost while the write was failing.
    (store / DEAD_LETTER_FILE).rmdir()
    second = _drain(root, store, post)
    assert second["dead_lettered"] == 1
    assert post.session_ids == ["s-good"]


def test_a_record_that_always_draws_a_transient_refusal_stops_gating_the_queue(
        tmp_path: Path) -> None:
    """P1 from review: MAX_STRIKES is the escape from a permanent 402 on one record."""
    from tortoise.session_events_drain import MAX_STRIKES

    root, store = tmp_path / "session-events", tmp_path / "store-sync"
    _write_day(root, "2026-10-01.jsonl",
               [_record("s-blocked"), _record("s-behind-1"), _record("s-behind-2")])

    class RefusesOne(Recorder):
        def __call__(self, payload: dict):
            if payload.get("session_id") == "s-blocked":
                self.posted.append(payload)
                return PostOutcome(ok=False, status=402, detail="quota_exceeded")
            return super().__call__(payload)

    post = RefusesOne()
    for _ in range(MAX_STRIKES - 1):
        run = _drain(root, store, post)
        assert run["stopped"] == "refused"
        assert "s-behind-1" not in post.session_ids

    # The strike limit is now reached: the pass SKIPS the blocked record and
    # files the ones behind it — without dropping the blocked record.
    escape = _drain(root, store, post)
    assert escape["stuck"] == 1
    assert "s-behind-1" in post.session_ids
    assert "s-behind-2" in post.session_ids
    assert escape["filed"] == 2


def test_a_torn_tail_on_a_fresh_file_is_deferred_not_dead_lettered(
        tmp_path: Path) -> None:
    """A writer may be mid-append: the tail line is torn, not corrupt."""
    root, store = tmp_path / "session-events", tmp_path / "store-sync"
    _write_day(root, "2026-10-01.jsonl", [_record("s0"), '{"session_id": "s1", "conv'])
    post = Recorder()

    summary = _drain(root, store, post)

    assert summary["filed"] == 1
    assert summary["dead_lettered"] == 0
    assert summary["deferred"] == 1

    # Once the writer finishes the record, the NEXT pass files it — the cursor
    # stayed before the torn line.
    _write_day(root, "2026-10-01.jsonl", [_record("s0"), _record("s1")])
    second = _drain(root, store, post)
    assert second["filed"] == 1
    assert "s1" in post.session_ids


def test_a_stuck_record_is_not_crossed_by_the_record_behind_it(
        tmp_path: Path) -> None:
    """P0 from re-review: `consume(index+1)` must not silently cross a skipped record."""
    from tortoise.session_events_drain import MAX_STRIKES

    root, store = tmp_path / "session-events", tmp_path / "store-sync"
    _write_day(root, "2026-10-01.jsonl",
               [_record("s-blocked"), _record("s-behind-1"), _record("s-behind-2")])

    class RefusesOne(Recorder):
        def __call__(self, payload: dict):
            if payload.get("session_id") == "s-blocked":
                self.posted.append(payload)
                return PostOutcome(ok=False, status=402, detail="quota_exceeded")
            return super().__call__(payload)

    post = RefusesOne()
    for _ in range(MAX_STRIKES - 1):
        _drain(root, store, post)
    escape = _drain(root, store, post)
    assert escape["stuck"] == 1
    assert escape["filed"] == 2
    # The blocked record is STILL QUEUED — not crossed by its neighbours, never
    # dead-lettered, and retried on the next pass.
    assert escape["queued"] == 1
    before = post.session_ids.count("s-blocked")
    later = _drain(root, store, post)
    assert later["queued"] == 1
    assert post.session_ids.count("s-blocked") == before + 1  # retried, not lost
    assert later["dead_lettered"] == 0


def test_the_bound_caps_posts_against_a_transport_that_never_recovers(
        tmp_path: Path) -> None:
    """P1 from review: this case asserted nothing — 2 POSTs against a cap of 200.

    Every record answering ``503`` ends the pass on the FIRST refusal, so the
    counters never approached the cap: `assert run["posts"] <= 200` compared 2
    with 200 and was green for any implementation. Proven vacuous at the review
    head by two mutations — counting terminal verdicts instead of POSTs, and
    deleting the bound entirely — both of which left it green.

    So it now asserts the EXACT count of payloads the transport received, with a
    transport that cannot end the pass early: every record is PERMANENTLY
    rejected (422), which dead-letters and continues. `==` rather than `<=` is
    what makes deleting the bound a failure (300 POSTs), and 300 POSTs is what
    an outage would become without it.
    """
    root, store = tmp_path / "session-events", tmp_path / "store-sync"
    total = MAX_RECORDS_PER_RUN + 100
    _write_day(root, "2026-10-01.jsonl", [_record(f"s{i}") for i in range(total)])
    post = Recorder(default=PostOutcome(ok=False, status=422, detail="bad lane"))

    run = _drain(root, store, post)

    # The count the transport actually saw — not the summary field a mutation
    # would move along with it.
    assert len(post.posted) == MAX_RECORDS_PER_RUN
    assert run["posts"] == MAX_RECORDS_PER_RUN
    assert run["stopped"] == "batch"
    assert run["queued"] == total - MAX_RECORDS_PER_RUN  # the rest is still owed


def test_the_bound_counts_verdicts_that_never_touch_the_wire(
        tmp_path: Path) -> None:
    """P2 from review: a dead letter is not always a POST, so `posts` is not enough.

    ``no_session_id`` / ``no_conversation`` / ``unparseable_record`` reach a
    TERMINAL verdict without a request, so a day made entirely of them did one
    `fsync` per record into the shared `dead-letter.jsonl` in a single unbounded
    pass while `posts` stayed 0. One pass must be bounded in RECORDS, not only
    in requests.
    """
    root, store = tmp_path / "session-events", tmp_path / "store-sync"
    total = MAX_RECORDS_PER_RUN + 100
    # Every line is valid JSON with no usable session id: dead-lettered local.
    _write_day(root, "2026-10-01.jsonl",
               [json.dumps({"conversation": [{"role": "user", "content": "hi"}]})
                for _ in range(total)])
    post = Recorder()

    run = _drain(root, store, post)

    assert post.posted == []  # nothing reachable to POST
    assert run["dead_lettered"] == MAX_RECORDS_PER_RUN
    assert run["stopped"] == "batch"
    assert run["queued"] == total - MAX_RECORDS_PER_RUN
    assert read_dead_letter_count(store) == MAX_RECORDS_PER_RUN


def test_a_pass_of_stuck_records_never_spends_the_whole_budget_on_them(
        tmp_path: Path) -> None:
    """P1 from re-review: the cap must still REACH a healthy record.

    Probe that found this: 220 records, the first 200 permanently refused, 30
    passes — records 201..220 were never POSTed once, because every pass spent
    its whole budget re-attempting known-stuck records.
    """
    root, store = tmp_path / "session-events", tmp_path / "store-sync"
    healthy = [f"s{i}" for i in range(5, 8)]
    _write_day(root, "2026-10-01.jsonl",
               [_record(f"s{i}") for i in range(5)] + [_record(s) for s in healthy])
    post = Recorder({f"s{i}": PostOutcome(ok=False, status=402, detail="quota")
                     for i in range(5)})

    # Enough passes for every poison record to reach MAX_STRIKES; the point is
    # what happens AFTER that, when the budget would otherwise be consumed by
    # re-attempting them.
    for _ in range(40):
        run = _drain(root, store, post, max_records=5)

    # The healthy records behind the five poison ones WERE reached.
    for sid in healthy:
        assert sid in post.session_ids
    assert run["queued"] == 5  # the poison ones are still queued, never dropped


def test_an_unreadable_day_does_not_mislabel_a_bound_stop(tmp_path: Path) -> None:
    """P2 from re-review: `stopped` names the PASS, the list names the day."""
    root, store = tmp_path / "session-events", tmp_path / "store-sync"
    archive = root / "archive"
    archive.mkdir(parents=True)
    (archive / "2026-09-30.jsonl.gz").write_bytes(b"not a gzip stream")
    _write_day(root, "2026-10-01.jsonl", [_record(f"s{i}") for i in range(6)])

    run = _drain(root, store, Recorder(), max_records=2)

    assert run["unreadable"], "the broken day must be reported"
    assert run["stopped"] == "batch"

def test_the_probe_slot_rotates_so_a_recoverable_record_is_not_starved(
        tmp_path: Path) -> None:
    """P1 from the third re-review: one global probe slot starved every record
    except the first struck one, for ever."""
    from tortoise.session_events_drain import MAX_STRIKES

    root, store = tmp_path / "session-events", tmp_path / "store-sync"
    # `z-perm` refuses for ever and sorts FIRST, so a global slot would always be
    # spent on it; `a-recover` refuses MAX_STRIKES times and then succeeds.
    _write_day(root, "2026-10-01.jsonl", [_record("z-perm"), _record("a-recover")])

    class Recorder2(Recorder):
        def __init__(self) -> None:
            super().__init__()
            self.refusals = 0

        def __call__(self, payload: dict):
            sid = payload.get("session_id")
            if sid == "z-perm":
                self.posted.append(payload)
                return PostOutcome(ok=False, status=402, detail="quota")
            if sid == "a-recover" and self.refusals < MAX_STRIKES:
                self.refusals += 1
                self.posted.append(payload)
                return PostOutcome(ok=False, status=402, detail="quota")
            return super().__call__(payload)

    post = Recorder2()
    for _ in range(30):
        run = _drain(root, store, post)

    assert "a-recover" in post.session_ids
    assert run["queued"] == 1  # only the permanently-refused record remains


def test_a_deflate_corrupt_archive_day_is_reported_and_does_not_abort_the_pass(
        tmp_path: Path) -> None:
    """P1 from the final re-review: `zlib.error` escaped the whole drain.

    A flipped byte INSIDE a valid gzip stream raises `zlib.error`, which is
    neither an OSError nor a `BadGzipFile`. It aborted the pass BEFORE the cursor
    write, so a corrupt earliest day stranded every later day with no report.
    """
    import gzip

    root, store = tmp_path / "session-events", tmp_path / "store-sync"
    archive = root / "archive"
    archive.mkdir(parents=True)
    payload = (json.dumps(_record("sealed-1")) + "\n").encode()
    sealed = archive / "2026-09-30.jsonl.gz"
    sealed.write_bytes(gzip.compress(payload))
    blob = bytearray(sealed.read_bytes())
    blob[12] ^= 0xFF  # corrupt the DEFLATE stream, not the header
    sealed.write_bytes(bytes(blob))
    _write_day(root, "2026-10-01.jsonl", [_record("s-later")])
    post = Recorder()

    run = _drain(root, store, post)

    assert [str(sealed)] == run["unreadable"]
    assert run["filed"] == 1                      # the later day still drains
    assert post.session_ids == ["s-later"]
    assert (store / "session-events-cursor.json").exists()


def test_a_file_scoped_run_files_only_the_named_session(tmp_path: Path) -> None:
    """P2 from review: `--file <session>` must not drain the WHOLE backlog.

    `store_sync.run_sweep` derives `only_session_id` from `--file` and forwards it
    to the spool and log legs; the session-events leg took `exclude_session_id`
    but not this one, so a scoped run filed every other session's records too —
    the same "flag half-applied" class the spool leg was already fixed for
    (#3515 review). Anything not selected must stay QUEUED: a scoped run is
    non-destructive for every other session.
    """
    root, store = tmp_path / "session-events", tmp_path / "store-sync"
    _write_day(root, "2026-10-01.jsonl",
               [_record("target"), _record("other-1"), _record("other-2")])

    scoped = Recorder()
    summary = _drain(root, store, scoped, only_session_id="target")

    assert scoped.session_ids == ["target"]
    assert summary["filed"] == 1
    assert summary["unselected"] == 2
    # `queued` counts what THIS run still owes: the two out-of-scope records are
    # reported as `unselected` rather than folded into a queue depth the scoped
    # run was never asked about.
    assert summary["queued"] == 0

    # A later UNSCOPED pass files them: "not selected" is not "dropped".
    everything = Recorder()
    rest = _drain(root, store, everything)
    assert everything.session_ids == ["other-1", "other-2"]
    assert rest["filed"] == 2
    assert rest["queued"] == 0


def test_the_plan_answers_the_scoped_question_the_pass_will_ask(
        tmp_path: Path) -> None:
    """The `--file` plan must not report the whole day's backlog.

    A dry run that counted all three records while the real pass filed one would
    be two answers to "how much is left" — the same divergence `--dry-run`
    already had for `cursor_dir`.
    """
    root, store = tmp_path / "session-events", tmp_path / "store-sync"
    _write_day(root, "2026-10-01.jsonl",
               [_record("target"), _record("other-1"), _record("other-2")])

    plan = _drain(root, store, Recorder(), dry_run=True, only_session_id="target")
    assert plan["queued"] == 1

    plan_all = _drain(root, store, Recorder(), dry_run=True)
    assert plan_all["queued"] == 3


def test_a_2xx_that_stored_without_extracting_is_counted_not_just_filed(
        tmp_path: Path) -> None:
    """P2 from review: one wire event, two accountings.

    The spool leg reads a 2xx body and records the store-only / degraded shapes
    as DEFERRALS (#4197) — the turns were stored and nothing was extracted. This
    leg counted every 2xx as `filed` and never looked. The reason now comes from
    the ONE shared definition (`__main__._deferral_reason`), so a stored-but-
    not-extracted session is visible instead of reading as a clean filing.
    """
    from tortoise.session_events_drain import format_progress

    root, store = tmp_path / "session-events", tmp_path / "store-sync"
    _write_day(root, "2026-10-01.jsonl",
               [_record("stored-only"), _record("extracted")])
    post = Recorder({
        "stored-only": PostOutcome(ok=True, status=201,
                                   body={"extraction_mode": "empty"}),
        "extracted": PostOutcome(ok=True, status=201,
                                 body={"extraction_mode": "extracted"}),
    })

    run = _drain(root, store, post)

    # Both records were ACCEPTED — the filing policy is unchanged — but only one
    # of them produced memory points.
    assert run["filed"] == 2
    assert run["degraded"] == 1
    assert run["queued"] == 0
    assert post.session_ids == ["stored-only", "extracted"]
    assert "stored-not-extracted 1" in format_progress(run)


def test_a_file_scoped_run_is_non_destructive_for_lines_it_cannot_attribute(
        tmp_path: Path) -> None:
    """P3 from review: `--file X` dead-lettered lines it could not attribute to X.

    The scope check sat AFTER the parse, so an unparseable line was dead-lettered
    into the SHARED `dead-letter.jsonl` and its index crossed — for a record any
    other session may own, on a run that was never asked about it. The function's
    own comment says the opposite ("a null sid included: it cannot be attributed
    to the scope … not ours to dead-letter either"), and `_count_records` states
    the same reading for the plan, so plan and pass answered two questions.

    MUTATION THAT REDS THIS: move the `scoped` branch below the parse verdicts
    (i.e. restore the pre-fix order) → `dead_lettered == 1` and one row lands.
    """
    root, store = tmp_path / "session-events", tmp_path / "store-sync"
    _write_day(root, "2026-10-01.jsonl", ["{not json", _record("target")])

    scoped = Recorder()
    summary = _drain(root, store, scoped, only_session_id="target")

    assert scoped.session_ids == ["target"]
    assert summary["filed"] == 1
    assert summary["dead_lettered"] == 0, "a scoped run judged a line it cannot attribute"
    assert summary["unselected"] == 1
    assert read_dead_letter_count(store) == 0, (
        "a `--file` run appended a dead-letter row for another session's record"
    )

    # The line is still queued (unselected never consumes), so an UNSCOPED pass —
    # the one that IS entitled to judge it — still dead-letters it.
    everything = Recorder()
    other = _drain(root, store, everything)
    assert other["dead_lettered"] == 1
    assert read_dead_letter_count(store) == 1


def test_a_torn_tail_under_a_file_scope_is_unselected_not_a_strike(
        tmp_path: Path) -> None:
    """A torn tail outside the scope must not charge a strike and stop the pass.

    Under `--file X`, a torn tail in ANY day file returned `retry`, which charged
    a consecutive-refusal strike and ended the pass with `stopped="refused"` —
    before it ever reached the session it was scoped to, and for a record that
    cannot be attributed to that session at all.

    MUTATION THAT REDS THIS: return `retry` for the torn tail without consulting
    `scoped` → `stopped == "refused"` and `unselected == 0`.
    """
    root, store = tmp_path / "session-events", tmp_path / "store-sync"
    # The tail is on a file modified JUST NOW, so it reads as mid-append
    # (`SETTLE_GRACE_SECONDS`) rather than as a corrupt record.
    _write_day(root, "2026-10-01.jsonl", [_record("target"), '{"session_id": "tor'])

    summary = _drain(root, store, Recorder(), only_session_id="target")

    assert summary["filed"] == 1
    assert summary["unselected"] == 1
    assert summary["deferred"] == 0
    assert summary["stopped"] is None, "a line outside the scope ended the scoped pass"
    assert summary["dead_lettered"] == 0
