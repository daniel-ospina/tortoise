"""#12362 — a capture confirmed after a transport 504 must not be reported as
"no memory points were extracted".

Measured on the hosted deployment (2026-11-13): `POST /v1/sessions` answers 504 at
the 10 s transport wait bound while the abandoned handler keeps extracting. The
confirmation that follows proves the posted turns are durable, but
``GET /v1/sessions/<id>`` in that window serves ``extracted: 0`` **beside**
``extraction.outcome_known: false`` — the server's own word for "not recorded
yet". Reading the count alone made `session capture` print:

    Extraction: no-provider — the turns were STORED but no memory points were extracted

for sessions that were read back moments later holding 2-7 memory points, and it
persisted a *priced* saved-backlog deferral offering to re-extract work that was
already done.

The unit verdict is pinned in ``tests/test_session_confirm.py``. This file pins
the two user-visible consequences: the sentence, and the ledger entry.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tests._capture_504 import run_capture as _run_capture_through_504
from tests._capture_504 import served_detail as _served_detail_for
from tests._capture_504 import text_transcript as _text_transcript
from tests._provider_env import pin_provider_routing_env
from tortoise import __main__ as main
from tortoise import capture_backlog as backlog

SESSION = "unknown-outcome-1"


@pytest.fixture()
def places(monkeypatch, tmp_path):
    """An isolated HOME + receipt dir, capture consent ON (the #4197 harness)."""
    home = tmp_path / "home"
    receipts = home / ".tortoise" / "import-receipts"
    home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("TORTOISE_IMPORT_RECEIPT_DIR", str(receipts))
    monkeypatch.setenv("TORTOISE_TEST_MODE", "1")
    monkeypatch.setenv("TORTOISE_CAPTURE", "1")
    for key in ("TORTOISE_API_KEY", "TORTOISE_API_URL",
                "TORTOISE_IMPORT_RECEIPT_DIR_OVERRIDE"):
        monkeypatch.delenv(key, raising=False)
    pin_provider_routing_env(monkeypatch)
    return home, receipts


#: The 504 → confirmation-read harness is SHARED with `#12379`'s composed test
#: (`tests/_capture_504.py`), which drives the same producer for its own surface.
#: The served detail is a SERVER contract that grows additive keys, so a copy
#: would be a second thing to forget — and the failure mode of
#: forgetting one is a suite that keeps passing against a body production no
#: longer serves. These two bindings keep this file's call sites readable while
#: the shape has ONE definition.
def _served_detail(turns: list[dict], *, extracted: int,
                   outcome_known: bool) -> dict:
    return _served_detail_for(SESSION, turns, extracted=extracted,
                              outcome_known=outcome_known)


def _run_capture(places, transcript: Path, detail_for):
    """Drive `session capture` through a 504 and a fake read-back.

    `detail_for(turns)` returns the served detail the confirmation reads. `places`
    is unused here and kept so every call site reads the same as before the
    harness moved out.
    """
    return _run_capture_through_504(transcript, detail_for, session_id=SESSION)


def test_an_unrecorded_outcome_is_not_reported_as_extraction_failure(
        places, tmp_path, capsys):
    """The sentence the beta user saw must be gone, and no deferral written."""
    transcript = _text_transcript(tmp_path)
    rc = _run_capture(
        places, transcript,
        lambda turns: _served_detail(turns, extracted=0, outcome_known=False))

    err = capsys.readouterr().err
    # `rc == 0` here is a SMOKE check, not this test's guard: the DEFERRED path
    # returns 0 as well, so it holds under a mutant that drops `PENDING` from the
    # terminal tuple. Measured (agent-infra#3442): that mutant prints "capture
    # deferred: … is spooled and will retry (504)" and this line still passed.
    # The discriminating rows are the retry-notice and sentence assertions below.
    assert rc == 0
    assert "will retry" not in err, (
        "a PENDING confirmation is terminal for TURN durability (#4675) — the "
        "turns ARE stored, so spooling it for retry is the deferral this verdict "
        "exists to stop re-asserting")
    # Folded into ONE assertion: both strings come from the same f-string in
    # `__main__.py`, so every `err` carrying `no-provider` also carries
    # "no memory points were extracted" — kept separate, the cause clause could
    # only ever fire behind the absence clause and the row would report the
    # absence (agent-infra#3442 rule 2: the catch must be ATTRIBUTABLE).
    assert ("no memory points were extracted" not in err
            and "no-provider" not in err), (
        "the server said the outcome is NOT RECORDED: asserting an absence, or "
        "naming the keyless cause, from it is the measured false negative (#12362)")
    assert "outcome not recorded yet" in err, \
        "the honest state must still be surfaced, not silently dropped"
    assert backlog.sessions() == [], (
        "a deferral is a PRICED offer to re-extract; offering it for a capture "
        "whose extraction is still running is the actionable half of the bug")


def test_a_known_store_only_outcome_still_records_the_deferral(places, tmp_path,
                                                               capsys):
    """The #4188 direction survives: a keyless capture IS a known negative."""
    transcript = _text_transcript(tmp_path)
    rc = _run_capture(
        places, transcript,
        lambda turns: _served_detail(turns, extracted=0, outcome_known=True))

    err = capsys.readouterr().err
    assert rc == 0
    assert "no memory points were extracted" in err
    records = backlog.sessions()
    assert [r["session_id"] for r in records] == [SESSION]
    assert records[0]["status"] == backlog.STATUS_NOT_PROCESSED


def test_an_unrecorded_outcome_never_retires_an_existing_deferral(places, tmp_path,
                                                                  capsys):
    """#12362 review: PENDING must not fall through to the RETIREMENT path.

    The branch after the store-only arm calls `_flush_backlog_offer`, whose own
    contract is "Call it ONLY where extraction provably ran" — it marks the
    record `processed` through `capture_backlog.mark_processed`, and nothing
    restores a retired record. An UNRECORDED outcome is the opposite of proof,
    so a session that was already deferred must still be deferred afterwards.

    Measured on the first cut of this fix: the pre-existing `not-processed`
    record became `processed`, i.e. the only recovery artifact for a
    stored-without-extraction session was destroyed by a capture whose
    extraction outcome the server had not recorded.
    """
    transcript = _text_transcript(tmp_path)
    assert backlog.record_not_processed(
        session_id=SESSION, harness="pi", file=str(transcript), turns=4,
        estimate=12, reason="no-provider") is True
    assert [r["status"] for r in backlog.sessions()] == [
        backlog.STATUS_NOT_PROCESSED]

    rc = _run_capture(
        places, transcript,
        lambda turns: _served_detail(turns, extracted=0, outcome_known=False))

    err = capsys.readouterr().err
    # Same smoke/guard split as the sibling above: `rc == 0` also holds on the
    # DEFERRED path, so it cannot witness `PENDING` terminalising.
    assert rc == 0
    assert "will retry" not in err, (
        "a PENDING confirmation must not be spooled for retry")
    records = backlog.sessions()
    # Id and status in ONE assertion (agent-infra#3442 rule 3): a retired record
    # keeps its session id, so an id-only assertion is dominated by the status
    # one and never faults — measured, retiring the deferral left it green.
    assert [(r["session_id"], r["status"]) for r in records] == [
        (SESSION, backlog.STATUS_NOT_PROCESSED)], (
        "an UNRECORDED extraction outcome is not proof that extraction ran; "
        "retiring the deferral here marks a possibly-unprocessed session "
        "`processed` and cannot be undone")
    assert "outcome not recorded yet" in err


def test_the_wire_spelling_is_defined_once_and_the_cli_does_not_retype_it():
    """#12374 review (P2): the producer and the drain must share ONE spelling.

    `capture_spool.outcome_unrecorded` calls itself "THE one definition of the
    state #12379 owns, so the three surfaces that see one wire body (the spool
    drain, the day-file drain and the spool entry's own durable marker) cannot
    drift apart about it". The CLI retyped that state as string literals in
    three places anyway — the producer that WRITES the body and the two arms
    that READ it — and the drift is silent in one direction: the CLI would keep
    printing the honest sentence (its literals agree with each other) while
    `_flush_one` / `session_events_drain` stopped counting and stopped stamping
    the durable marker, so the capture's only surviving trace disappears with
    every test still green.

    ⛔ The sibling tests in this file do NOT fake the body — they patch
    `urlopen`/`session_detail` and then drive the real `_session_post` →
    `_refused` producer (`_run_capture`, above) — so they DO exercise the
    producer/consumer pair. They stayed green over the literals for the duller
    reason that the literals EQUALLED the constants, which is exactly why a
    spelling test is needed on top: a divergence is what they cannot see, and
    only while it is a divergence.

    Discriminating by construction, and the pin is STRUCTURAL rather than a
    text scan, because the VALUE spelling (`"unknown"`) legitimately occurs
    elsewhere in `__main__` (six unrelated sites), so a blanket scan for it
    would be a false blocker. Each of the three sites is instead pinned to the
    shared constant, so reverting ANY of them to a literal — key OR value —
    reds one of the assertions below for the reason the state is defined once
    rather than for its wording.
    """
    src = Path(main.__file__).read_text(encoding="utf-8")
    assert '"extraction_outcome"' not in src and "'extraction_outcome'" not in src, (
        "`__main__` spells the #12362 wire key as a literal again — the state is "
        "defined once in `capture_spool.UNRECORDED_OUTCOME_KEY` and read back via "
        "`outcome_unrecorded`, so a second spelling here can drift from the "
        "consumer while every behavioural test stays green")
    # The producer: the ONE line that writes the wire body must take its key AND
    # its value from the shared constants. Pinned as a requirement on that line
    # rather than as the whole line's text, so re-indenting cannot break it —
    # while a reversion of either half reds it.
    _producer = [line for line in src.splitlines()
                 if "body[" in line and "UNRECORDED_OUTCOME_KEY" in line]
    assert len(_producer) == 1 and "UNRECORDED_OUTCOME_VALUE" in _producer[0], (
        "the producer no longer writes BOTH halves of the #12362 wire pair from "
        "`capture_spool` — reverting either the key or the VALUE to a literal "
        "re-creates the second definition this test exists to forbid")
    # The two reader arms, one line each: the predicate, never a literal.
    assert "if outcome_unrecorded(body):" in src, (
        "the CLI's first reader arm no longer asks `capture_spool.outcome_unrecorded`")
    assert "elif not outcome_unrecorded(body):" in src, (
        "the CLI's fall-through arm no longer asks `capture_spool.outcome_unrecorded`")
    # …and the round trip the coupled spelling buys: what the producer writes is
    # exactly what the consumer recognises.
    from tortoise import capture_spool
    produced = {capture_spool.UNRECORDED_OUTCOME_KEY:
                capture_spool.UNRECORDED_OUTCOME_VALUE}
    assert capture_spool.outcome_unrecorded(produced) is True, (
        "the producer's own key/value pair is not recognised by the consumer's "
        "predicate — the wire contract has two definitions")
    assert capture_spool.outcome_unrecorded(None) is False
