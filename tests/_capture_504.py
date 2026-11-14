"""ONE definition of the `504 → confirmation-read` capture harness.

Two suites need a capture whose `POST /v1/sessions` crosses the transport bound and
whose confirm-on-refusal `GET /v1/sessions/<id>` serves the extraction qualifier:

* ``test_12362_capture_outcome_unknown.py`` — the CLI SENTENCE and the priced
  deferral (`#12362`);
* ``test_12379_unrecorded_trace.py`` — the durable spool marker and the health
  surface (`#12379`, end to end since `#12374` landed the producer).

`#12362` had grown this harness privately, for its own suite. `#12379`'s composed
section needs the same 504 → confirmation-read pair, so it lives here once rather
than being copied a second time. The served detail is a SERVER contract that
grows additive keys (``#3520`` added the ``extraction`` block this pair turns
on), so a copy is a thing to forget — and the way it degrades is a suite that
keeps passing against a body production no longer serves. A second copy would
also have to be kept in step about the *turn* shape, which is the subtler half:
`confirm_capture` compares stored turns against the posted ones and answers
`TURNS_MISSING` when they disagree, so a harness that serves rows built from a
different model makes a "composed" test assert nothing at all.

⛔ Everything here is parameterised by ``session_id``, and that is load-bearing
rather than tidiness: ``expected_turns`` derives the served turn IDs FROM the
session id, so a helper closing over one module's constant would post under one
id and serve rows under another.
"""
from __future__ import annotations

import io
import json
import urllib.error
from collections.abc import Callable
from pathlib import Path
from unittest import mock

from tortoise import __main__ as main
from tortoise.session_confirm import expected_turns


def text_transcript(tmp_path: Path, turns: int = 4) -> Path:
    """A transcript the poster can parse, of the requested length."""
    lines = [f"{'User' if i % 2 == 0 else 'Assistant'}: saved turn {i} with content"
             for i in range(turns)]
    path = tmp_path / "hook-tmp.jsonl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def wait_bound_refusal(url: str = "https://api.example/v1/sessions"):
    """A 504 whose body is the hosted wait-bound refusal (the measured #12362)."""
    body = json.dumps({"detail": "The server's wait budget for this request was "
                                 "exceeded before a response was ready."}).encode()
    return urllib.error.HTTPError(
        url, 504, "Gateway Timeout",
        {"Content-Type": "application/json"}, io.BytesIO(body))


def served_detail(session_id: str, turns: list[dict], *, extracted: int,
                  outcome_known: bool) -> dict:
    """A `GET /v1/sessions/<id>` payload for the turns the client just posted.

    The turn rows are DERIVED from the posted payload through the same
    `expected_turns` model the confirmation compares against, because a
    hand-written echo would agree with a broken client and hide exactly the
    mismatch `confirm_capture` exists to catch.
    """
    rows = [{"id": tid, "role": role, "content": body}
            for tid, (role, body) in expected_turns(session_id, turns).items()]
    return {"id": session_id, "turns": len(turns), "extracted": extracted,
            "turn_points": rows,
            "extraction": {"status": "stored", "extractor": None,
                           "outcome_known": outcome_known}}


def run_capture(transcript: Path, detail_for: Callable[[list[dict]], dict], *,
                session_id: str, harness: str = "pi") -> int:
    """Drive the REAL `session capture` into a transport bound, then serve a REAL
    confirmation read built by ``detail_for`` from the turns actually posted.

    `urlopen` is raised from, and `session_verify.session_detail` is replaced —
    both are the seams the production code binds at call time (the poster imports
    `urlopen` function-locally, the confirmation imports `session_detail`
    locally), so patching the module attribute is the whole fake. No network.
    """
    posted: dict[str, object] = {}

    def _urlopen(req, timeout=None):
        raise wait_bound_refusal(getattr(req, "full_url", "https://api.example"))

    def _detail(api_url, api_key, served_session_id, *, timeout=None):
        # RECORDED, not asserted here: `confirm_capture` wraps this reader in a
        # broad `except Exception: detail = None` (session_confirm), so an
        # AssertionError raised inside it is swallowed and reads downstream as
        # an `UNKNOWN` verdict — the check would report itself as the symptom it
        # guards against. The assertion lives after the call, below.
        posted["served_id"] = served_session_id
        return detail_for(posted["turns"])  # type: ignore[arg-type]

    with mock.patch("urllib.request.urlopen", _urlopen), \
         mock.patch("tortoise.session_verify.session_detail", _detail):
        posted["turns"] = main._parse_transcript(transcript.read_text())
        rc = main._cmd_session_capture(
            main.argparse.Namespace(file=str(transcript), harness=harness,
                                    session_id=session_id, transcript_path=None,
                                    model=None),
            "tt_test_key", "https://api.example")
    assert posted.get("served_id") == session_id, (
        "the confirmation read a different session than the one posted "
        f"({posted.get('served_id')!r} != {session_id!r}) — the real code would "
        "answer UNKNOWN and the test would be asserting on nothing")
    return rc
