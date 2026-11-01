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
    assert summary["stopped"] == "refused"
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
