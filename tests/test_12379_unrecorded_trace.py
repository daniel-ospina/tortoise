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

⛔ The trace keys on the wire marker ``body["extraction_outcome"] == "unknown"``
(``#12362``). Its ONE producer is ``__main__._session_post._refused``, which
synthesises that body when ``session_confirm._extraction_verdict`` returns
``PENDING`` — the verdict that requires the confirmation READ to have matched the
posted turns AND the served ``extraction.outcome_known`` to be explicitly
``False``. That producer landed as #12374 (squash ``5e5845b141``,
2026-11-14T01:32:37Z), so at or after it the trace is LIVE, not inert: the last
section of this file drives the real 504 → real ``confirm_capture`` → real
``_flush_one`` path and asserts the marker lands on the entry, so the hand-built
bodies below are proven shape-compatible with the producer rather than
taken on faith. It never prices anything: a deferral is an offer to re-extract,
and the server has not said that extraction did not run.

⚠️ SCOPE OF THE TRACE — TWO producers, one per leg, and BOTH are now live.
This file exercises the Python-CLI one (``__main__._session_post._refused``). The
Pi leg posts to ``/v1/sessions`` itself (``tortoise-capture.ts``'s ``post()``), so
it never receives the synthesised body; since **#12603** it produces the same
marker from its own CONFIRM-ON-REFUSAL READ instead (a retryable refusal →
``GET /v1/sessions/<id>`` → turn match → explicit
``extraction.outcome_known: false``), pinned by the ``#12603`` family in
``tortoise/pi-hooks/tortoise-capture.test.ts``. Before #12603 that stamp branch
had no live input and ``session health`` on a Pi box reported nothing for a
session in exactly the measured state (filed, ``outcome_known: false``). The two
legs write ONE shared meta, so the marker key/value must stay identical —
``test_the_two_legs_share_one_marker_and_verdict_vocabulary`` pins that.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests._capture_504 import run_capture, served_detail, text_transcript
from tests._provider_env import pin_provider_routing_env
from tortoise import __main__ as main
from tortoise import capture_backlog
from tortoise.capture_spool import (
    RECORDED_OUTCOME_VERDICTS,
    UNRECORDED_OUTCOME_KEY,
    UNRECORDED_OUTCOME_VALUE,
    PostOutcome,
    Snapshot,
    capture_health_line,
    flush_spool,
    read_spool_meta,
    spool_dir,
    unrecorded_captures,
    write_spool_entry,
)
from tortoise.sdk import _CAPTURE_NO_PROVIDER_MODE
from tortoise.session_confirm import PENDING
from tortoise.session_events_drain import (
    drain_session_events,
    format_progress,
)

SESSION = "sess-unrecorded"
TURNS = [
    {"role": "user", "content": "Ship the capture spool."},
    {"role": "assistant", "content": "On it."},
]

#: The body `__main__._session_post._refused` synthesises for a `PENDING`
#: confirmation (durable turns, outcome not recorded) — shape and spelling bound
#: to the producer's own vocabulary (`session_confirm.PENDING` and the two
#: `capture_spool` constants), never retyped here: a rename on either side would
#: otherwise leave this fixture describing a body production no longer emits
#: while all sixteen tests that feed it stayed green. The composed section
#: at the end of the file asserts the real producer emits it and that it reaches
#: the spool entry.
UNRECORDED_BODY = {"confirmed": PENDING, UNRECORDED_OUTCOME_KEY: UNRECORDED_OUTCOME_VALUE}
#: The KNOWN store-only shape (#4188 keyless) — a real, priced deferral. The
#: verdict is deliberately typed in the SHOUTED spelling: `confirmed` is compared
#: case-insensitively (`outcome_recorded`'s `strip().lower()`), and that is pinned
#: by the two rows that clear on THIS fixture —
#: `test_an_unextracted_confirmation_still_clears_the_marker` and
#: `test_an_unextracted_verdict_clears_a_marker_a_real_flush_stamped` — each of
#: which reds if the lowercase fold is dropped (measured; a third row feeds this
#: fixture and is a non-verdict assertion). ⚠️ NOT by
#: `test_only_a_closed_verdict_vocabulary_clears_the_marker`, whose junk bodies are
#: non-verdicts whether folded or not: it stays GREEN on that mutant.
STORE_ONLY_BODY = {"confirmed": "UNEXTRACTED",
                   "extraction_mode": _CAPTURE_NO_PROVIDER_MODE}


def _snapshot(session_id: str = SESSION,
              turns: list[dict] | None = None) -> Snapshot:
    return Snapshot(
        session_id=session_id,
        turns=list(TURNS) if turns is None else turns,
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


def test_the_unrecorded_marker_is_STICKY_across_a_replay_filing(tmp_path):
    """A later re-capture cannot resolve the state, so it must not erase the trace.

    The routine `--resume`/`clear`/`compact` path re-captures a grown transcript;
    the server answers `replayed` ("already captured") because its re-capture
    gate requires a RECORDED failure (`prior_capture_ok is False`) and this
    session's outcome is NULL — so a re-POST replays rather than re-extracts and
    the condition PERSISTS. Measured in review: clearing the marker on a
    marker-absent body deleted the only trace while the session still had no
    recorded outcome.

    MUTATIONS THAT RED THIS: `else: fresh.pop(UNRECORDED_OUTCOME_KEY, None)`
    (the marker disappears on the second filing) — the pre-fix shape."""
    write_spool_entry(tmp_path, _snapshot())
    flush_spool(tmp_path, _post(UNRECORDED_BODY), now=1000.0)
    assert unrecorded_captures(tmp_path) == [SESSION]

    # The transcript grows, so the entry is unfiled by the new content and is
    # re-POSTed; the server answers `replayed` — no outcome evidence at all.
    write_spool_entry(tmp_path, _snapshot(
        turns=[*TURNS, {"role": "user", "content": "one more turn"}]))
    replay = flush_spool(tmp_path,
                         _post({"session_id": SESSION, "mode": "replayed"}),
                         now=2000.0)

    assert replay.filed == 1
    assert unrecorded_captures(tmp_path) == [SESSION], (
        "a replayed re-capture says the turns are already stored — it does NOT "
        "say the extraction outcome was recorded, and a NULL-outcome session can "
        "never be re-extracted by a re-POST")
    assert capture_backlog.sessions() == []


def test_a_later_recorded_verdict_CLEARS_the_marker(tmp_path):
    """A resolved session must stop being reported — else the trace is a
    permanent false alarm (#12379 review).

    `confirmed: filed` means the server imported the turns, so extraction RAN:
    the state "we do not know how it ended" is over. A marker left standing
    over that verdict makes `session health` cry wolf for every session the
    server later resolved, until the entry is evicted — the alert-fatigue shape
    the health line's own states are ordered to avoid. (What nothing on the
    client can resolve is a later filing that does NOT record the ending — a
    REPLAY, see `test_the_unrecorded_marker_is_STICKY_across_a_replay_filing` — which
    is why the health line states this historically, VERBATIM: "have NO
    recorded extraction outcome at the time". Do not paraphrase that qualifier
    away — it is the whole difference between a trace and a false alarm.
    Since #12603 the marker is also written by the Pi leg on a retryable
    REFUSAL, so the line may name a still-QUEUED capture, not only a filed one.)

    MUTATIONS THAT RED THIS: make the stamp unconditional (`if unrecorded:` /
    `elif outcome_recorded(...)` → a single-or-no branch that never pops).
    """
    write_spool_entry(tmp_path, _snapshot())
    flush_spool(tmp_path, _post(UNRECORDED_BODY), now=1000.0)
    assert unrecorded_captures(tmp_path) == [SESSION]

    # A later pass files new content and the server reports the outcome.
    write_spool_entry(tmp_path, _snapshot(
        turns=[*TURNS, {"role": "user", "content": "one more turn"}]))
    resolved = flush_spool(
        tmp_path, _post({"confirmed": "filed", "extracted": 5}), now=2000.0)

    assert resolved.filed == 1, "the turns are durable — the entry still files"
    assert resolved.unrecorded == 0
    assert unrecorded_captures(tmp_path) == [], (
        "the server recorded the outcome, so the trace is stale — it must not "
        "outlive the evidence that resolved it")
    assert capture_health_line(tmp_path, api_key="tt_k").unrecorded == ()
    assert (read_spool_meta(tmp_path, SESSION) or {}).get("filed_key") is not None


def test_an_unextracted_verdict_clears_a_marker_a_real_flush_stamped(tmp_path):
    """The ``unextracted`` verdict clears even when the marker was written by a
    flush (the full stamp → clear sequence on one entry) rather than by a direct
    ``stamp_unrecorded_marker`` call. "A real prior pass" means the REAL code
    path (``flush_spool``); the marker's live producer is #12374's PENDING
    branch, proved end-to-end at the end of this file.

    ⚠️ What the verdict proves, precisely: ``unextracted`` requires the
    confirmation READ to have matched the posted turns AND the served
    ``extraction.outcome_known`` NOT to be ``False`` (``_extraction_verdict``
    answers ``PENDING`` for an explicit ``False``, #12374). ⚠️ It is still not
    POSITIVE evidence of a recorded outcome: a detail with no ``extraction``
    block at all (a pre-#3520 server, a fake) reads as ``None`` and opens the
    same verdict. It stays in the allow-list because ``#12379``'s accepted
    decision is that a keyless session must stop being reported as unresolved
    forever.

    MUTATIONS THAT RED THIS: narrow `outcome_recorded` to `filed` alone.
    """
    write_spool_entry(tmp_path, _snapshot())
    flush_spool(tmp_path, _post(UNRECORDED_BODY), now=1000.0)
    assert unrecorded_captures(tmp_path) == [SESSION]

    write_spool_entry(tmp_path, _snapshot(
        turns=[*TURNS, {"role": "user", "content": "one more turn"}]))
    resolved = flush_spool(tmp_path, _post(STORE_ONLY_BODY), now=2000.0)

    assert resolved.filed == 1
    assert resolved.unrecorded == 0
    assert unrecorded_captures(tmp_path) == []


def test_only_a_closed_verdict_vocabulary_clears_the_marker(tmp_path):
    """The clear is a closed allow-list, never "anything that is not pending".

    `confirmed` is client-owned vocabulary (`filed` / `unextracted`, plus
    #12374's `pending`), but a body is still server input: a typo, a `null`, a
    future/unknown verdict, or a non-dict 2xx must leave the marker alone. A
    `!= "pending"` predicate would clear on every one of them, and the trace
    would disappear for exactly the sessions nobody resolved.

    MUTATIONS THAT RED THIS: `return verdict != "pending"` (or any predicate
    that treats an unknown value as evidence).
    """
    write_spool_entry(tmp_path, _snapshot())
    flush_spool(tmp_path, _post(UNRECORDED_BODY), now=1000.0)
    assert unrecorded_captures(tmp_path) == [SESSION]

    # Dict bodies only: `PostOutcome.body` is a parsed 2xx body, and a non-dict
    # one already fails the filing for its own (pre-existing) reasons.
    for junk in ({"confirmed": "FLIED"}, {"confirmed": None},
                 {"confirmed": 1}, {"confirmed": "pending"}, {},
                 {"mode": "replayed"}):
        write_spool_entry(tmp_path, _snapshot(
            turns=[*TURNS, {"role": "user", "content": f"{junk}"}]))
        summary = flush_spool(tmp_path, _post(junk), now=2000.0)
        assert summary.filed == 1
        assert unrecorded_captures(tmp_path) == [SESSION], (
            f"{junk!r} does not report how the capture ended, so it is not "
            "evidence — the marker must stand")


#: The shape the spool ACTUALLY receives on a routine 2xx, on both legs: the
#: hosted capture receipt (`hosted_api` POST `/v1/sessions`). It carries neither
#: `confirmed` nor `extraction_outcome` — that is the whole reason #12460 exists.
#: Server-shaped: `points` is `extracted` itself and `turns` is the turn count of
#: the snapshot these are posted against (`hosted_api`: `"turns":
#: len(body.conversation)`, `"points": extracted, "extracted": len(extracted)`),
#: so a fixture whose counts disagree with the request would not describe a body
#: the server can emit.
RECEIPT_EXTRACTED_BODY = {"session_id": SESSION, "turns": 3, "extracted": 4,
                          "points": ["p1", "p2", "p3", "p4"],
                          "extraction_mode": "llm:openai"}
RECEIPT_STORE_ONLY_BODY = {"session_id": SESSION, "turns": 3, "extracted": 0,
                           "points": [],
                           "extraction_mode": _CAPTURE_NO_PROVIDER_MODE}
RECEIPT_AMBIGUOUS_BODY = {"session_id": SESSION, "turns": 3, "extracted": 0,
                          "points": [], "extraction_mode": "llm:openai"}


def test_a_receipt_reporting_extracted_points_clears_the_marker(tmp_path):
    """⛔ THE PRODUCTION SHAPE — the clear on a ROUTINE 2xx.

    A predicate that reads only the synthesised ``confirmed`` vocabulary never
    fires on a success: the drain's own 2xx body is the capture receipt
    (``{session_id, turns, extracted, …}``), which has no ``confirmed``. Measuring
    that in #12460's verification is what turned a green, inert port into a real
    one — the fake-green class this suite exists to prevent.

    MUTATIONS THAT RED THIS: drop the ``extracted`` clause from
    ``outcome_recorded`` (the marker then stands over a session the server
    resolved, until eviction — the permanent false alarm #12460 describes).
    """
    write_spool_entry(tmp_path, _snapshot())
    flush_spool(tmp_path, _post(UNRECORDED_BODY), now=1000.0)
    assert unrecorded_captures(tmp_path) == [SESSION]

    write_spool_entry(tmp_path, _snapshot(
        turns=[*TURNS, {"role": "user", "content": "one more turn"}]))
    resolved = flush_spool(tmp_path, _post(RECEIPT_EXTRACTED_BODY), now=2000.0)

    assert resolved.filed == 1
    assert unrecorded_captures(tmp_path) == [], (
        "the receipt reports four extracted points — extraction RAN, so the "
        "marker is stale and must not outlive the evidence that resolved it")


def test_a_store_only_receipt_mode_does_NOT_clear_the_marker(tmp_path):
    """⛔ A store-only ``extraction_mode`` is NOT evidence the outcome was recorded.

    It says only that THIS REQUEST skipped extraction. The server gates its
    durable outcome record on ``not session_existed`` (``hosted_api``:
    ``_record_session_state = (not session_existed) if store_only``), so a keyless
    RE-capture of an existing session records NOTHING while still reporting
    ``extraction_mode: "no-provider"`` with ``extracted: 0``. Clearing on that
    destroys the only trace of a session whose outcome was never recorded — the
    false-PASS direction this trace exists to prevent (found in review of
    PR #12507).

    MUTATIONS THAT RED THIS: add a store-only ``extraction_mode`` clause to
    ``outcome_recorded`` (the shape PR #12507 first shipped, and the reason this
    test reads as a negative).
    """
    write_spool_entry(tmp_path, _snapshot())
    flush_spool(tmp_path, _post(UNRECORDED_BODY), now=1000.0)
    assert unrecorded_captures(tmp_path) == [SESSION]

    write_spool_entry(tmp_path, _snapshot(
        turns=[*TURNS, {"role": "user", "content": "one more turn"}]))
    received = flush_spool(tmp_path, _post(RECEIPT_STORE_ONLY_BODY), now=2000.0)
    # ⚠️ The body must actually have been received, or this pins nothing about a
    # store-only mode: an un-reposted entry also leaves the marker standing.
    assert received.filed == 1, "the receipt must have been POSTed and accepted"

    assert unrecorded_captures(tmp_path) == [SESSION], (
        "a keyless re-capture records no outcome, so the marker must stand — "
        "clearing here hides a session nobody resolved")


def test_an_unextracted_confirmation_still_clears_the_marker(tmp_path):
    """...but the CONFIRMATION vocabulary still does — that is #12379's decision.

    ``confirmed: "unextracted"`` is synthesised by ``_session_post`` from
    ``session_confirm.confirm_capture``, which read the session back and matched
    the posted turns. ⚠️ What that read proves, precisely: the turns are durable
    AND the served ``extraction.outcome_known`` is NOT ``False`` —
    ``_extraction_verdict`` answers ``PENDING`` for an explicit ``False``, so this
    verdict cannot be reached over a capture whose outcome the server has not
    recorded (#12374). ⚠️ It is still not POSITIVE evidence of a recorded
    outcome: a detail with no ``extraction`` block at all (a pre-#3520 server, a
    fake) reads ``None`` and opens the same verdict. It stays in the allow-list
    because #12379's accepted decision is that a keyless session must stop being
    reported as unresolved; removing it would make the unresolved-alarm
    permanent for every keyless session, which is the trade the issue already
    settled.

    MUTATIONS THAT RED THIS: narrow ``RECORDED_OUTCOME_VERDICTS`` to ``filed``
    alone (every keyless session then keeps a permanent false alarm).
    """
    write_spool_entry(tmp_path, _snapshot())
    flush_spool(tmp_path, _post(UNRECORDED_BODY), now=1000.0)
    assert unrecorded_captures(tmp_path) == [SESSION]

    write_spool_entry(tmp_path, _snapshot(
        turns=[*TURNS, {"role": "user", "content": "one more turn"}]))
    flush_spool(tmp_path, _post(STORE_ONLY_BODY), now=2000.0)

    assert unrecorded_captures(tmp_path) == []


def test_an_AMBIGUOUS_receipt_leaves_the_marker_standing(tmp_path):
    """⛔ The load-bearing negative: ``extracted: 0`` in a NORMAL mode.

    The capture handler the transport bound abandoned may still be extracting —
    the server's "no outcome yet" state (#12362's measurement: three captures
    printed "nothing extracted" and read back holding 7, 2 and 5 points seconds
    later). Reading a zero as a negative is the #12362 bug wearing the other mask.

    MUTATIONS THAT RED THIS: treat a zero extraction count as resolved (``>= 0``,
    or "anything that is not pending").
    """
    write_spool_entry(tmp_path, _snapshot())
    flush_spool(tmp_path, _post(UNRECORDED_BODY), now=1000.0)

    write_spool_entry(tmp_path, _snapshot(
        turns=[*TURNS, {"role": "user", "content": "one more turn"}]))
    received = flush_spool(tmp_path, _post(RECEIPT_AMBIGUOUS_BODY), now=2000.0)
    # ⚠️ Same guard as its store-only sibling: an un-reposted entry would satisfy
    # the final assertion for the wrong reason.
    assert received.filed == 1, "the receipt must have been POSTed and accepted"

    assert unrecorded_captures(tmp_path) == [SESSION], (
        "a zero extraction count in a normal mode does not say whether the "
        "outcome was ever recorded — it must never resolve the marker")


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
    offer to re-extract a session that may already have extracted).

    ⚠️ `_flush_one` never touches the ledger, so the PRICED writer for this leg
    is `__main__._record_drain_deferrals` — the assertion for THAT path lives in
    `test_session_drain_records_no_priced_deferral`, not here (a review finding:
    pinning the claim only on the path with no writer proved nothing)."""
    write_spool_entry(tmp_path, _snapshot())
    flush_spool(tmp_path, _post(UNRECORDED_BODY), now=1000.0)

    assert capture_backlog.sessions() == []


def test_session_drain_records_no_priced_deferral(tmp_path, monkeypatch):
    """#12362's decision, pinned on the path that actually PRICES (#12379 review).

    `_record_drain_deferrals` is the spool leg's ONE priced writer, and it runs
    INSIDE `_cmd_session_drain` — the `flush_spool`-only test above cannot see it.
    A PENDING body must record nothing there, or a session that may already have
    extracted is offered a re-extraction priced at its turn count.

    MUTATIONS THAT RED THIS: add an `outcome_unrecorded` arm to
    `_record_drain_deferrals` that calls `_record_backlog_deferral` (verified in
    review: it left the original suite green)."""
    from tortoise import __main__ as main

    write_spool_entry(tmp_path, _snapshot())
    monkeypatch.setattr(main, "_session_post",
                        lambda api_key, api_url: _post(UNRECORDED_BODY))

    assert main._cmd_session_drain("tt_test_key", "https://api.example") == 0
    assert capture_backlog.sessions() == [], (
        "an unrecorded outcome must never become a priced deferral")


def test_the_drain_line_bounds_the_session_ids(tmp_path, monkeypatch, capsys):
    """`flush_spool` has no per-pass attempt cap and the spool holds 20 000
    entries, so one unbounded join could render twenty thousand ids into a single
    stderr line.

    MUTATIONS THAT RED THIS: join the whole list (the line grows with the spool),
    or use `'..., '` as the separator (it reads as a truncation marker on an id
    that was not truncated, and disagrees with the health line's rendering)."""
    from tortoise import __main__ as main

    for i in range(4):
        write_spool_entry(tmp_path, _snapshot(session_id=f"sess-unrecorded-{i}"))
    monkeypatch.setattr(main, "_session_post",
                        lambda api_key, api_url: _post(UNRECORDED_BODY))

    assert main._cmd_session_drain("tt_test_key", "https://api.example") == 0

    err = capsys.readouterr().err
    line = next(ln for ln in err.splitlines()
                if ln.startswith("spool drain: outcome unrecorded"))
    assert "outcome unrecorded 4" in line, line
    assert "+1 more" in line, line
    assert "...," not in line, line


def test_session_drain_names_the_unrecorded_count_and_the_session(tmp_path,
                                                                  monkeypatch,
                                                                  capsys):
    """The operator surface: `session drain`'s own summary line.

    A count with no session id is not actionable, and the SessionStart hook
    discards this stderr — which is why the durable half above exists. On a
    hand-run drain this line is what tells the operator to look.

    MUTATIONS THAT RED THIS: drop the `outcome unrecorded` block from the drain
    (`filed 1 …` and silence return — the measured state). NOTE the summary line
    needs no term in its own gate: `summary.attempted` is incremented before the
    POST, so an unrecorded-only pass always passes the pre-existing gate (a
    review finding — the extra `or summary.unrecorded` term was dead and is
    gone)."""
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
    """MUTATIONS THAT RED THIS (measured): delete the CAS stamp (the health count
    stays 0); drop `_print_unrecorded_captures` from `_cmd_session_health`; or
    fold a Python-only term into the pinned stdout line at either edit site it
    can come from — the line `capture_health_line` builds for THIS box's own
    state (it takes exactly one of its literals), or `_cmd_session_health`'s
    `print`. The case-insensitive absence check below is what catches the
    lower-case spelling (#12427); it does not depend on the other leg."""
    from tortoise import __main__ as main

    write_spool_entry(tmp_path, _snapshot())
    flush_spool(tmp_path, _post(UNRECORDED_BODY), now=1000.0)

    assert main._cmd_session_health() == 0
    out = capsys.readouterr()
    # The pinned line stays byte-identical — ONE line on stdout, and no
    # Python-only term in it (`capture: OK (0 queued)` / `INACTIVE (...)`
    # depending on the box's credential, neither of which may mention this).
    assert len([ln for ln in out.out.splitlines() if ln.strip()]) == 1, out.out
    # Case-insensitive on purpose: the spelling a Python-only term would take
    # is lower-case, and an upper-case-only check would pass it (#12427).
    assert "recorded" not in out.out.lower(), out.out
    assert "NO recorded" in out.err, out.err
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


# ── Cross-leg parity: ONE marker, ONE verdict allow-list ────────────────────
#
# Both legs write the SAME spool meta (`capture_spool` the Python drain,
# `tortoise/pi-hooks/tortoise-capture.ts` the Pi filing path). #12460 gave the Pi
# leg the stamp/clear the Python leg already had, so the two predicates now act
# on one shared file in two languages — and a divergence would flip the marker
# against itself depending on which leg filed the growth (the Python leg reading
# `extraction_outcome`, the Pi leg writing something else; or one leg clearing on
# a verdict the other refuses). Neither language's behaviour suite can see the
# other's constants, so the pin lives here: read the TypeScript source and
# compare it to the Python definitions.
#
# These are SOURCE-level assertions. The Pi BEHAVIOUR is pinned by
# `tortoise/pi-hooks/tortoise-capture.test.ts`'s `#12460` tests, which pytest
# does not run (they need `node --test`).

PI_SEAM = (Path(__file__).resolve().parent.parent
           / "tortoise" / "pi-hooks" / "tortoise-capture.ts")

PY_SEAM = Path(__file__).resolve().parent.parent / "tortoise" / "capture_spool.py"


def _ts_source() -> str:
    return PI_SEAM.read_text(encoding="utf-8")


def _ts_source_name() -> str:
    return "tortoise/pi-hooks/tortoise-capture.ts"


_PY_SOURCE = PY_SEAM.read_text(encoding="utf-8")


def _ts_string_const(name: str) -> str:
    match = re.search(rf'const {name} = "([^"]+)";', _ts_source())
    assert match is not None, (
        f"`{name}` is not declared in {PI_SEAM.name} — the two legs no longer "
        "share the marker the #12379 trace is keyed on")
    return match.group(1)


def test_the_two_legs_share_one_marker_and_verdict_vocabulary():
    """The marker key/value and the recorded-verdict closed lists must be
    IDENTICAL on both legs, and that NEITHER leg infers the outcome from a
    receipt's ``extraction_mode``.

    MUTATIONS THAT RED THIS: rename the key on one leg only (the marker becomes
    invisible to the other — written by whoever stamped it, read by nobody),
    change ``unknown`` on one side, add a verdict to the Python allow-list
    without the Pi CAS clearing on it (then a Pi filing over a resolved session
    keeps reporting a permanent false alarm), or reintroduce a store-only
    ``extraction_mode`` clause on either leg (a keyless RE-capture records no
    outcome, so that clause HIDES a session nobody resolved — the false-PASS
    direction; the shape PR #12507 first shipped and review caught).
    """
    assert _ts_string_const("UNRECORDED_OUTCOME_KEY") == UNRECORDED_OUTCOME_KEY
    assert _ts_string_const("UNRECORDED_OUTCOME_VALUE") == UNRECORDED_OUTCOME_VALUE

    ts_list = re.search(r"const RECORDED_OUTCOME_VERDICTS = \[([^\]]*)\] as const;",
                        _ts_source())
    assert ts_list is not None, (
        "the Pi leg declares no `RECORDED_OUTCOME_VERDICTS` allow-list — a "
        "predicate without a closed vocabulary clears the marker on anything")
    ts_verdicts = tuple(re.findall(r'"([^"]+)"', ts_list.group(1)))

    assert ts_verdicts == tuple(RECORDED_OUTCOME_VERDICTS), (
        f"the two legs disagree about which verdicts resolve the state: "
        f"Pi={ts_verdicts!r} Python={tuple(RECORDED_OUTCOME_VERDICTS)!r}")

    # ⛔ Neither leg may read `extraction_mode` as evidence of a recorded outcome.
    # The server gates its durable outcome record on `not session_existed`, so a
    # keyless RE-capture records NOTHING while still reporting `no-provider` with
    # `extracted: 0` (`hosted_api._record_session_state`). The behavioural halves
    # are `test_a_store_only_receipt_mode_does_NOT_clear_the_marker` (here) and
    # its `#12460` namesake in `tortoise-capture.test.ts`; this is the source-level
    # guard so neither predicate can grow the clause back quietly. Matched on the
    # READ FORMS (both prose blocks legitimately discuss the mode), not the word.
    for source, name, reads in (
        (_ts_source(), _ts_source_name(),
         ("receipt.extraction_mode", "body.extraction_mode",
          'receipt["extraction_mode"]', "receipt['extraction_mode']",
          "{ extraction_mode }")),
        (_PY_SOURCE, "tortoise/capture_spool.py",
         ('.get("extraction_mode")', ".get('extraction_mode')",
          '["extraction_mode"]')),
    ):
        predicate = re.search(
            r"(?:function outcomeRecorded|def outcome_recorded)\b.*?\n(?:\}|(?=\S))",
            source, re.S)
        assert predicate is not None, f"{name}: outcomeRecorded not found"
        body = predicate.group(0)
        assert not any(read in body for read in reads), (
            f"{name}'s recorded-outcome predicate reads `extraction_mode` — a "
            "keyless RE-capture would then clear the marker while recording no "
            "outcome at all")


# ── The PRODUCER, end to end (the composed path) ───────────────────────────
#
# Everything above hands `flush_spool` a hand-built body. That pins the
# PREDICATES, but a fixture only ever agrees with itself: it cannot tell you
# whether the body a real capture produces is the shape these predicates read,
# whether the verdict that produces it can be reached, or whether the marker
# survives the filing to reach `session health`. Until #12374 landed there was
# no producer at all, so this half was provably inert and the section below
# would have been speculation. It has landed (`5e5845b141`), so the chain is
# pinned here against the REAL 504 → REAL confirmation read → REAL `_flush_one`
# path, and the qualifier — not the refusal — is shown to be what decides.

def _capture_through_a_504(tmp_path, monkeypatch, *, outcome_known: bool) -> int:
    """Drive the REAL `session capture` into the transport bound, then serve a
    REAL confirmation read carrying `outcome_known`.

    The 504 → confirmation-read harness itself lives in `tests/_capture_504.py`,
    shared with `tests/test_12362_capture_outcome_unknown.py`, which drives the
    same producer for the CLI sentence and the backlog ledger.
    """
    monkeypatch.setenv("TORTOISE_TEST_MODE", "1")
    pin_provider_routing_env(monkeypatch)
    return run_capture(
        text_transcript(tmp_path),
        lambda turns: served_detail(SESSION, turns, extracted=0,
                                    outcome_known=outcome_known),
        session_id=SESSION)


def test_the_PRODUCERS_pending_body_reaches_the_entry_and_the_health_line(
        tmp_path, monkeypatch, capsys):
    """`#12374`'s producer → `_flush_one` → `session health`, on the real path.

    The 504 is REAL (the `_session_post` wrapper synthesises the body), the
    confirmation read is REAL (`session_confirm._extraction_verdict` answers
    `PENDING` from the served qualifier), and the drain is REAL (`flush_spool`
    → `_flush_one`'s compare-and-swap). Nothing in this test writes the marker
    directly, so a broken link anywhere in that chain reds it.

    MUTATIONS THAT RED THIS: drop `_refused`'s `PENDING` arm (the body loses the
    marker); drop `_extraction_verdict`'s `PENDING` branch (the verdict becomes
    `unextracted`, which CLEARS); make `_flush_one` skip the stamp; or make
    `_unrecorded_from_metas` read a different key. The `outcome_known: True`
    twin below is the other half: same 504, same turns, qualifier flipped.
    """
    rc = _capture_through_a_504(tmp_path, monkeypatch, outcome_known=False)
    err = capsys.readouterr().err

    root = spool_dir()
    meta = read_spool_meta(root, SESSION) or {}
    assert rc == 0
    assert meta.get("filed_key") is not None, (
        "the marker only proves anything on a FILED entry — an unfiled one is "
        "re-stamped by the next pass, so a missing stamp there is not a defect")
    assert meta.get(UNRECORDED_OUTCOME_KEY) == UNRECORDED_OUTCOME_VALUE, (
        "the producer's own body did not reach the spool entry: the durable "
        "half of the trace (#12379) is inert on the real path")
    assert unrecorded_captures(root) == [SESSION], (
        "the durable surface must name the session, not just hold a key")
    health = capture_health_line(root)
    assert health.unrecorded == (SESSION,)
    main._print_unrecorded_captures(health)
    named = capsys.readouterr().err
    assert SESSION in named and "NO recorded extraction outcome" in named, (
        "the hook's 2>/dev/null discards the drain's line, so this stderr "
        "statement is the surface that must survive it")
    assert "will retry" not in err, (
        "a PENDING confirmation is terminal for TURN durability (#4675) — the "
        "turns ARE stored")
    assert capture_backlog.sessions() == [], (
        "the trace is counted, never PRICED: the server never said extraction "
        "did not run, so offering a re-extraction is the #12362 bug")


def test_the_same_refusal_with_a_KNOWN_outcome_stamps_nothing(
        tmp_path, monkeypatch):
    """The QUALIFIER decides, not the refusal — the twin of the test above.

    Same 504, same posted turns, same `extracted: 0`: flipping only
    `extraction.outcome_known` to `True` must leave no marker at all. Without
    this row a mutant that stamped on ANY refusal would pass the test above.
    """
    rc = _capture_through_a_504(tmp_path, monkeypatch, outcome_known=True)

    root = spool_dir()
    meta = read_spool_meta(root, SESSION) or {}
    assert rc == 0
    assert meta.get("filed_key") is not None
    assert UNRECORDED_OUTCOME_KEY not in meta, (
        "the server RECORDED the outcome (outcome_known: true) — stamping it "
        "would report a resolved session as unresolved forever")
    assert unrecorded_captures(root) == []
