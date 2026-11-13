"""#12379 — a capture whose extraction outcome was never RECORDED must leave a
durable, NON-PRICED trace.

#12362 removed a false claim: a capture confirmed after a transport 504 used to
report "no memory points were extracted" and write a *priced* saved-backlog
deferral for a session whose extraction outcome the server had not recorded yet
(``extraction.outcome_known: false``). Removing the claim left the state silent —
measured on the #12362 branch over a real spool entry: ``session drain`` printed
``filed 1, deferred 0, skipped 0 …`` with ``backlog records: []`` and
``receipts: []``, so nothing anywhere named the session that can never gain
memory points.

This file pins the SECOND half (option 2 on the issue: a counted, NON-PRICED
trace rather than a decision about re-attempting):

* the drain summaries count the state (``session drain`` stderr,
  ``format_progress`` for the day-file leg), and
* the spool entry KEEPS the marker, so ``session health`` reports it later and
  a human can find the session id — the hook that produces most captures throws
  stderr away, which is why an unrecorded state must not live only in a console
  line.

⛔ The trace keys on the wire marker #12362 introduces
(``body["extraction_outcome"] == "unknown"``, set by
``_session_post._refused`` for its ``PENDING`` verdict). On a tree that does not
carry #12362 the marker is ABSENT, so this trace simply never fires — inert, not
wrong. It never prices anything: a deferral is an offer to re-extract, and the
server has not said that extraction did not run.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tortoise import capture_backlog
from tortoise.capture_spool import (
    PostOutcome,
    Snapshot,
    flush_spool,
    read_spool_meta,
    unrecorded_captures,
    write_spool_entry,
)
from tortoise.sdk import _CAPTURE_NO_PROVIDER_MODE
from tortoise.session_events_drain import (
    drain_session_events,
    format_progress,
)

SESSION = "sess-unrecorded"
TURNS = [
    {"role": "user", "content": "Ship the capture spool."},
    {"role": "assistant", "content": "On it."},
]

#: What `_session_post._refused` synthesises for a confirmed-but-UNRECORDED
#: capture (#12362's `PENDING`): durable turns, outcome not recorded.
UNRECORDED_BODY = {"confirmed": "pending", "extraction_outcome": "unknown"}
#: The KNOWN store-only shape (#4188 keyless) — a real, priced deferral.
STORE_ONLY_BODY = {"confirmed": "UNEXTRACTED",
                   "extraction_mode": _CAPTURE_NO_PROVIDER_MODE}


def _snapshot(session_id: str = SESSION) -> Snapshot:
    return Snapshot(
        session_id=session_id,
        turns=list(TURNS),
        source="t.pi.jsonl",
        machine_id="m" * 64,
        harness="pi",
    )


def _post(body: dict):
    def post(payload: dict) -> PostOutcome:
        return PostOutcome(ok=True, status=200, body=dict(body))

    return post


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """Never touch the developer machine's spool / breadcrumbs / ledger."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("TORTOISE_IMPORT_RECEIPT_DIR",
                       str(home / ".tortoise" / "import-receipts"))
    # `session health` resolves the spool itself; pin it to the same directory the
    # test writes, so the surface is exercised on the entry the drain filed.
    monkeypatch.setenv("TORTOISE_CAPTURE_SPOOL_DIR", str(tmp_path))
    monkeypatch.setenv("TORTOISE_CAPTURE", "1")
    for key in ("TORTOISE_API_KEY", "TORTOISE_API_URL"):
        monkeypatch.delenv(key, raising=False)


# ── The spool leg (`session drain`) ─────────────────────────────────────────


def test_the_spool_drain_counts_an_unrecorded_outcome(tmp_path):
    """MUTATIONS THAT RED THIS: drop the `unrecorded` counter from
    `FlushSummary`/`_flush_one` (the measured silence returns)."""
    write_spool_entry(tmp_path, _snapshot())
    summary = flush_spool(tmp_path, _post(UNRECORDED_BODY), now=1000.0)

    assert summary.filed == 1, "the turns ARE durable — the entry must still file"
    assert summary.unrecorded == 1
    assert summary.unrecorded_sessions == [SESSION]


def test_the_unrecorded_marker_survives_on_the_spool_entry(tmp_path):
    """A count that lives only in one run's console line is not findable later.

    MUTATIONS THAT RED THIS: stop stamping the marker inside the filing CAS
    (`session health` then has nothing to count, and the session id the user
    needs is gone once the drain's output scrolls away)."""
    write_spool_entry(tmp_path, _snapshot())
    flush_spool(tmp_path, _post(UNRECORDED_BODY), now=1000.0)

    meta = read_spool_meta(tmp_path, SESSION)
    assert meta is not None and meta.get("filed_key") is not None
    assert meta.get("extraction_outcome") == "unknown"
    assert unrecorded_captures(tmp_path) == [SESSION]


def test_a_known_store_only_outcome_is_not_counted_as_unrecorded(tmp_path):
    """The #4188 / #4258 direction survives: a keyless capture is a KNOWN
    negative, it keeps its deferral, and it must not be relabelled `unrecorded`.

    MUTATIONS THAT RED THIS: broaden the predicate to "extracted nothing"
    (every keyless capture then reads as unrecorded, and the priced offer that
    genuinely applies is never made)."""
    write_spool_entry(tmp_path, _snapshot())
    summary = flush_spool(tmp_path, _post(STORE_ONLY_BODY), now=1000.0)

    assert summary.filed == 1
    assert summary.unrecorded == 0
    assert summary.unrecorded_sessions == []
    assert "extraction_outcome" not in (read_spool_meta(tmp_path, SESSION) or {})
    assert unrecorded_captures(tmp_path) == []


def test_an_unrecorded_outcome_prices_nothing(tmp_path):
    """The trace is a TRACE: no saved-backlog record, priced or otherwise.

    MUTATIONS THAT RED THIS: record the state through
    `capture_backlog.record_not_processed` (the #12362 bug returns — a priced
    offer to re-extract a session that may already have extracted)."""
    write_spool_entry(tmp_path, _snapshot())
    flush_spool(tmp_path, _post(UNRECORDED_BODY), now=1000.0)

    assert capture_backlog.sessions() == []


def test_session_drain_names_the_unrecorded_count_and_the_session(tmp_path,
                                                                  monkeypatch,
                                                                  capsys):
    """The operator surface: `session drain`'s own summary line.

    A count with no session id is not actionable, and the SessionStart hook
    discards this stderr — which is why the durable half above exists. On a
    hand-run drain this line is what tells the operator to look.

    MUTATIONS THAT RED THIS: drop the counter from the drain's summary line (the
    measured silence), or drop it from the print's gate condition (a pass whose
    ONLY event is an unrecorded filing prints nothing at all)."""
    from tortoise import __main__ as main

    write_spool_entry(tmp_path, _snapshot())
    monkeypatch.setattr(main, "_session_post",
                        lambda api_key, api_url: _post(UNRECORDED_BODY))
    monkeypatch.setattr(main, "spool_dir", lambda: Path(tmp_path), raising=False)
    # `_cmd_session_drain` resolves the spool through `capture_spool.spool_dir`
    # imported inside the function; pin it there instead of on `main`.
    monkeypatch.setenv("TORTOISE_CAPTURE_SPOOL_DIR", str(tmp_path))

    assert main._cmd_session_drain("tt_test_key", "https://api.example") == 0

    err = capsys.readouterr().err
    assert "outcome unrecorded 1" in err, err
    assert SESSION in err, "the count must name the session it is about"


# ── `session health` — the surface that survives the hook's 2>/dev/null ─────


def test_session_health_reports_the_unrecorded_count(tmp_path, capsys):
    """MUTATIONS THAT RED THIS: keep the marker off the entry (health counts 0),
    or fold the count into the PINNED health line (the cross-leg parity test
    executes the real TypeScript against this module's line, so the pinned
    spelling cannot grow a Python-only term)."""
    from tortoise import __main__ as main

    write_spool_entry(tmp_path, _snapshot())
    flush_spool(tmp_path, _post(UNRECORDED_BODY), now=1000.0)

    assert main._cmd_session_health() == 0
    out = capsys.readouterr()
    # The pinned line stays byte-identical — ONE line on stdout, and no
    # Python-only term in it (`capture: OK (0 queued)` / `INACTIVE (...)`
    # depending on the box's credential, neither of which may mention this).
    assert len([ln for ln in out.out.splitlines() if ln.strip()]) == 1, out.out
    assert "UNRECORDED" not in out.out, out.out
    assert "UNRECORDED" in out.err, out.err
    assert SESSION in out.err, out.err


# ── The day-file leg (`session events`, counted by `format_progress`) ───────


def _record(session_id: str) -> dict:
    return {
        "session_id": session_id,
        "conversation": list(TURNS),
        "metadata": {"source": "pi-session-quit"},
        "harness": "pi",
        "machine_id": "m" * 64,
    }


def test_the_events_leg_counts_an_unrecorded_outcome(tmp_path):
    """The second drain writer must agree, or one leg keeps the silence.

    MUTATIONS THAT RED THIS: count only in `capture_spool` (the day-file leg
    then files the same wire body with no trace), or add the term to
    `format_progress` without counting it."""
    root, store = tmp_path / "session-events", tmp_path / "store-sync"
    root.mkdir(parents=True)
    (root / "2026-11-13.jsonl").write_text(
        __import__("json").dumps(_record("evt-unrecorded")) + "\n",
        encoding="utf-8")

    summary = drain_session_events(_post(UNRECORDED_BODY), root=root, store=store)

    assert summary["filed"] == 1
    assert summary["unrecorded"] == 1
    assert "outcome-unrecorded 1" in format_progress(summary)


def test_the_events_line_is_unchanged_when_nothing_is_unrecorded(tmp_path):
    """No new noise on the ordinary line: the term appears only when non-zero.

    MUTATIONS THAT RED THIS: append the term unconditionally (every drain line
    grows a `0`, and the operators who read it learn to ignore the field)."""
    root, store = tmp_path / "session-events", tmp_path / "store-sync"
    root.mkdir(parents=True)
    (root / "2026-11-13.jsonl").write_text(
        __import__("json").dumps(_record("evt-known")) + "\n", encoding="utf-8")

    summary = drain_session_events(_post({"session_id": "evt-known"}),
                                   root=root, store=store)

    assert summary["unrecorded"] == 0
    assert "unrecorded" not in format_progress(summary)
