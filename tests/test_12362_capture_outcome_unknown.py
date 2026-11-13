"""#12362 — a capture confirmed after a transport 504 must not be reported as
"no memory points were extracted".

Measured on the hosted deployment (2026-11-13): `POST /v1/sessions` answers 504 at
the 10 s transport wait bound while the abandoned handler keeps extracting. The
confirmation that follows proves the posted turns are durable, but
``GET /v1/sessions/<id>`` in that window serves ``extracted: 0`` **beside**
``extraction.outcome_known: false`` — the server's own word for "not recorded
yet". Reading the count alone made `session capture` print:

    Extraction: no-provider — the turns were STORED but no memory points were extracted

for sessions that were read back moments later holding 5-7 memory points, and it
persisted a *priced* saved-backlog deferral offering to re-extract work that was
already done.

The unit verdict is pinned in ``tests/test_session_confirm.py``. This file pins
the two user-visible consequences: the sentence, and the ledger entry.
"""
from __future__ import annotations

import io
import json
import urllib.error
from pathlib import Path
from unittest import mock

import pytest

from tests._provider_env import pin_provider_routing_env
from tortoise import __main__ as main
from tortoise import capture_backlog as backlog
from tortoise.session_confirm import expected_turns

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


def _text_transcript(tmp_path: Path, turns: int = 4) -> Path:
    lines = [f"{'User' if i % 2 == 0 else 'Assistant'}: saved turn {i} with content"
             for i in range(turns)]
    path = tmp_path / "hook-tmp.jsonl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _wait_bound_refusal(url: str = "https://api.example/v1/sessions"):
    """A 504 whose body is the hosted wait-bound refusal."""
    body = json.dumps({"detail": "The server's wait budget for this request was "
                                 "exceeded before a response was ready."}).encode()
    return urllib.error.HTTPError(
        url, 504, "Gateway Timeout",
        {"Content-Type": "application/json"}, io.BytesIO(body))


def _served_detail(turns: list[dict], *, extracted: int,
                   outcome_known: bool) -> dict:
    """A `GET /v1/sessions/<id>` payload: role and body served as own fields."""
    rows = [{"id": tid, "role": role, "content": body}
            for tid, (role, body) in expected_turns(SESSION, turns).items()]
    return {"id": SESSION, "turns": len(turns), "extracted": extracted,
            "turn_points": rows,
            "extraction": {"status": "stored", "extractor": None,
                           "outcome_known": outcome_known}}


def _run_capture(places, transcript: Path, detail_for):
    """Drive `session capture` through a 504 and a fake read-back.

    `detail_for(turns)` returns the served detail the confirmation reads.
    """
    captured: dict[str, list[dict]] = {}

    def _urlopen(req, timeout=None):
        raise _wait_bound_refusal(getattr(req, "full_url", "https://api.example"))

    def _detail(api_url, api_key, session_id, *, timeout=None):
        # The turns the CLIENT actually posted are the ones the confirmation
        # compares against — recover them from the parsed transcript so this
        # fake is derived, not hand-written (a hand-written echo is the
        # format-mismatch blind spot `_detail` in test_session_confirm guards).
        return detail_for(captured["turns"])

    with mock.patch("urllib.request.urlopen", _urlopen), \
         mock.patch("tortoise.session_verify.session_detail", _detail):
        # `_spool_transcript` parses the file before the POST; capture the same
        # turn list the poster will send by parsing it here.
        captured["turns"] = main._parse_transcript(transcript.read_text())
        return main._cmd_session_capture(
            main.argparse.Namespace(file=str(transcript), harness="pi",
                                    session_id=SESSION, transcript_path=None,
                                    model=None),
            "tt_test_key", "https://api.example")


def test_an_unrecorded_outcome_is_not_reported_as_extraction_failure(
        places, tmp_path, capsys):
    """The sentence the beta user saw must be gone, and no deferral written."""
    transcript = _text_transcript(tmp_path)
    rc = _run_capture(
        places, transcript,
        lambda turns: _served_detail(turns, extracted=0, outcome_known=False))

    err = capsys.readouterr().err
    assert rc == 0
    assert "no memory points were extracted" not in err, (
        "the server said the outcome is NOT RECORDED; asserting an absence from "
        "it is the measured false negative (#12362)")
    assert "outcome not recorded yet" in err, \
        "the honest state must still be surfaced, not silently dropped"
    assert "no-provider" not in err, (
        "the capture extracted on the server; `no-provider` names a cause that "
        "was never measured")
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

    assert rc == 0
    records = backlog.sessions()
    assert [r["session_id"] for r in records] == [SESSION]
    assert records[0]["status"] == backlog.STATUS_NOT_PROCESSED, (
        "an UNRECORDED extraction outcome is not proof that extraction ran; "
        "retiring the deferral here marks a possibly-unprocessed session "
        "`processed` and cannot be undone")
    assert "outcome not recorded yet" in capsys.readouterr().err
