"""#4197 — the SAVED BACKLOG: offer the choice, price it, honour it.

The owner decision (recorded verbatim on #4197, 2026-09-19) is that a user whose
sessions were captured store-only (no provider key, #3892; or extraction turned
off, #4258) is OFFERED the choice to extract the saved backlog — with that
option RECOMMENDED and its cost visible before the spend — or to keep only
future conversations, with the choice re-offerable.

What made the choice impossible before this change is the property these tests
pin FIRST: a store-only 2xx was the only capture outcome that left no local
trace at all. A successful extraction writes a receipt; a failure writes a
breadcrumb; a store-only deferral writes neither (deliberately — a receipt
would make every later re-import skip the POST on a stale receipt, and the
session could then never gain its memory points). So the machine could not
enumerate its own backlog, could not price it, could not remember the user's
choice, and could not report which of the three states it was in.

MUTATIONS THAT RED THESE:
  * drop `record_not_processed` from the store-only branch of
    `sessions import` → the ledger stays empty and the report/offer vanish;
  * drop `mark_processed` from the receipt-write success path → a processed
    session stays offered forever (the report never flips);
  * let `record_not_processed` overwrite a `processed` record → a processed
    session is re-offered and re-processed;
  * make `set_policy` store an unknown choice, or make `read_policy` default to
    `declined` for an absent policy → the report claims a choice the user never
    made;
  * gate `--process` on the CLIENT's provider env → the hosted lane's
    recommended action is refused with a remedy its user cannot apply (the P1
    this suite pins in both directions).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest import mock

import pytest

from tortoise import __main__ as main
from tortoise import capture_backlog as backlog


@pytest.fixture
def places(monkeypatch, tmp_path):
    """A HOME-scoped local state: receipts + ledger derived from ONE base.

    `TORTOISE_IMPORT_RECEIPT_DIR` is the shared override — the ledger derives
    from its PARENT through `hook_install.local_state_dir`, the same derivation
    the receipt writer and the breadcrumb use. That is the property that keeps
    the three local records in one tree (#4373's class), so the fixture uses
    the real derivation rather than pointing the ledger somewhere else.
    """
    home = tmp_path / "home"
    receipts = tmp_path / "receipts"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("TORTOISE_IMPORT_RECEIPT_DIR", str(receipts))
    monkeypatch.setenv("TORTOISE_TEST_MODE", "1")
    monkeypatch.setenv("TORTOISE_CAPTURE", "1")
    (home / ".tortoise").mkdir(parents=True, exist_ok=True)
    (home / ".tortoise" / "credentials.json").write_text(json.dumps(
        {"api_key": "tt_test", "api_url": "https://legit.example.com"}),
        encoding="utf-8")
    for k in ("OPENROUTER_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY",
              "GEMINI_API_KEY", "TORTOISE_SESSION_LLM_MOCK",
              # The upload identity must come from the fixture's own
              # `credentials.json`, never from an ambient shell — otherwise the
              # hosted/self-hosted direction a test pins would be decided by
              # the developer's environment.
              "TORTOISE_API_KEY", "TORTOISE_API_URL"):
        monkeypatch.delenv(k, raising=False)
    return home, receipts


def _transcript(tmp_path: Path, name: str = "session.jsonl", turns: int = 4):
    """A parses-as-pi transcript with `turns` real turns."""
    lines = [
        json.dumps({"type": "message",
                    "message": {"role": "user" if i % 2 == 0 else "assistant",
                                "content": f"saved turn {i} with content"}})
        for i in range(turns)
    ]
    path = tmp_path / name
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _claude_transcript(tmp_path: Path, name: str = "project.jsonl",
                       turns: int = 4):
    """A transcript in the `claude-desktop` shape — the shape the Claude Code
    hook's DURABLE source (`~/.claude/projects/*/*.jsonl`) carries, and the one
    the saved-backlog ledger records for a hook-captured deferral."""
    lines = [
        json.dumps({"type": "assistant" if i % 2 else "user",
                    "message": {"role": "assistant" if i % 2 else "user",
                                "content": f"saved turn {i} with content"}})
        for i in range(turns)
    ]
    path = tmp_path / name
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _text_transcript(tmp_path: Path, name: str = "hook-tmp.jsonl",
                     turns: int = 4):
    """A transcript in the `User:`/`Assistant:` text form `session capture`
    parses (`_parse_transcript`) — what the Claude Code hook's `mktemp`
    conversion holds, and what that hook passes as `--file`."""
    lines = [f"{'User' if i % 2 == 0 else 'Assistant'}: saved turn {i} with content"
             for i in range(turns)]
    path = tmp_path / name
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


class _FakeResponse:
    def __init__(self, body: dict):
        self._body = json.dumps(body).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _store_only_body(session_id: str, turns: int = 4) -> dict:
    return {
        "session_id": session_id, "turns": turns, "extracted": 0, "points": [],
        "extraction_mode": "no-provider",
        "warnings": ["no LLM provider key configured (set e.g. "
                     "OPENROUTER_API_KEY or DEEPSEEK_API_KEY) — the session's "
                     "turns were STORED and remain searchable, but LLM "
                     "extraction into memory points was skipped"],
        "errors": [], "ok": False,
    }


def _extracted_body(session_id: str, turns: int = 4) -> dict:
    body = _store_only_body(session_id, turns)
    body.update({"extraction_mode": "llm:mock", "extracted": 3, "ok": True,
                 "warnings": []})
    return body


def _posting(body_for):
    """A `urllib.request.urlopen` fake that records posted payloads."""
    posted: list[dict] = []

    def _urlopen(req, timeout=None):
        posted.append(json.loads(req.data.decode("utf-8")))
        return _FakeResponse(body_for(posted[-1]))

    return _urlopen, posted


# ── The ledger: three states, and the property none of them may violate ─────


def test_store_only_import_records_the_backlog_and_writes_no_receipt(places,
                                                                     tmp_path):
    """The core defect: the deferral used to leave NO local trace."""
    _, receipts = places
    transcript = _transcript(tmp_path)
    with mock.patch("urllib.request.urlopen",
                    _posting(lambda payload: _store_only_body("imp_store1"))[0]):
        rc = main._cmd_sessions_import(main.argparse.Namespace(
            file=str(transcript), harness="pi", session_id="imp_store1"))

    assert rc == 0, "a store-only 2xx is a deferral, not a failure"
    assert not receipts.exists() or not list(receipts.glob("*.json")), \
        "a deferred store-only capture must write NO receipt (a receipt would " \
        "make every later re-import skip the POST, #4188)"

    records = backlog.sessions()
    assert [r["session_id"] for r in records] == ["imp_store1"]
    record = records[0]
    assert record["status"] == backlog.STATUS_NOT_PROCESSED
    assert record["file"] == str(transcript)
    assert record["harness"] == "pi"
    assert record["turns"] == 4
    assert record["estimate"] > 0, "the offer must be priceable from the record"
    assert "no LLM provider key" in record["reason"]


def test_summary_reports_all_three_states_with_cost(places):
    """not-processed / processed / declined — and the cost only counts what a
    run would still spend."""
    backlog.record_not_processed(session_id="s_pending", turns=3, estimate=9)
    backlog.record_not_processed(session_id="s_done", turns=5, estimate=15)
    backlog.mark_processed("s_done", processed_at="2026-10-07T00:00:00Z")

    state = backlog.summary()
    counts = state["counts"]
    assert counts[backlog.STATUS_NOT_PROCESSED] == 1
    assert counts[backlog.STATUS_PROCESSED] == 1
    assert state["declined"] is False, "an UNANSWERED choice is not a decline"
    assert state["cost_units"] == 9, \
        "an already-processed entry's spend already happened"
    assert state["turns"] == 3

    backlog.set_policy(backlog.CHOICE_FUTURE_ONLY, decided_at="2026-10-07T00:00:00Z")
    state = backlog.summary()
    assert state["declined"] is True
    assert state["counts"][backlog.STATUS_NOT_PROCESSED] == 1, \
        "declining is a POLICY: the saved sessions stay listed, not erased"


def test_policy_refuses_an_unknown_choice(places):
    """A typo'd policy would read as asked-and-answered."""
    assert backlog.set_policy("maybe") is False
    assert backlog.read_policy() == {}
    assert backlog.summary()["declined"] is False


def test_record_never_downgrades_a_processed_session(places):
    """A later store-only re-capture must not re-list an extracted session."""
    backlog.record_not_processed(session_id="s_x", turns=2, estimate=6)
    assert backlog.mark_processed("s_x", processed_at="t") is True
    assert backlog.record_not_processed(session_id="s_x", turns=2, estimate=6) \
        is False
    assert backlog.summary()["counts"][backlog.STATUS_PROCESSED] == 1


def test_mark_processed_is_a_noop_for_a_session_never_deferred(places):
    """Inventing a `processed` record would report a backlog that never was."""
    assert backlog.mark_processed("never_seen") is False
    assert backlog.sessions() == []


def test_session_id_cannot_escape_the_ledger_directory(places):
    """The id arrives from a CLI argument and a server response."""
    assert backlog.record_not_processed(session_id="../../escape",
                                        turns=1, estimate=1) is True
    written = list(backlog.backlog_dir().glob("*.json"))
    assert len(written) == 1
    assert written[0].parent == backlog.backlog_dir()
    assert backlog.summary()["counts"][backlog.STATUS_NOT_PROCESSED] == 1


def test_corrupt_records_read_as_nothing_recorded(places):
    """A malformed/unreadable record must not become a traceback in a report."""
    target = backlog.backlog_dir() / "s_broken.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("{not json", encoding="utf-8")
    assert backlog.sessions() == []
    assert backlog.summary()["counts"][backlog.STATUS_NOT_PROCESSED] == 0


def test_no_recommendation_over_an_empty_backlog(places):
    """A recommendation over nothing would turn every capture into an ad."""
    assert backlog.recommendation(backlog.summary()) is None
    assert main._print_backlog_offer() == 0


# ── The CLI: report, decline, process ──────────────────────────────────────


def test_backlog_report_shows_the_cost_before_the_choice(places, capsys):
    home, _ = places
    backlog.record_not_processed(session_id="s_1", harness="pi", turns=6,
                                 estimate=18, reason="no LLM provider key",
                                 file=str(home / "a.jsonl"))
    rc = main.main(["sessions", "backlog"])
    out = capsys.readouterr().out

    assert rc == 0
    assert "1 not-processed" in out
    assert "~18 estimated extraction unit(s)" in out
    assert "6 saved turn(s)" in out
    assert "--process" in out and "Recommended" in out, \
        "the backlog option must be the visibly recommended one"


def test_backlog_json_emits_the_machine_readable_state(places, capsys):
    """`--json` is the machine-readable half of the same state the report
    renders — so the three states are consumable without parsing prose."""
    home, _ = places
    backlog.record_not_processed(session_id="s_1", harness="pi", turns=4,
                                 estimate=12, reason="no LLM provider key",
                                 file=str(home / "a.jsonl"))
    rc = main.main(["sessions", "backlog", "--json"])
    out = capsys.readouterr().out

    assert rc == 0
    # JSON ONLY on stdout — a consumer doing `json.loads(out)` must not have to
    # strip a human report first (mirrors `_cmd_fail` / `_emit_json`).
    state = json.loads(out)
    assert state["counts"][backlog.STATUS_NOT_PROCESSED] == 1
    assert state["cost_units"] == 12
    assert state["declined"] is False
    assert state["statuses"] == [backlog.STATUS_NOT_PROCESSED,
                                 backlog.STATUS_PROCESSED]


def test_backlog_json_emits_the_state_for_an_empty_backlog(places, capsys):
    """An empty backlog is a STATE, not an absence: the machine-readable
    document must exist for it too, rather than an early-return sentence."""
    rc = main.main(["sessions", "backlog", "--json"])
    out = capsys.readouterr().out

    assert rc == 0
    state = json.loads(out)
    assert state["counts"][backlog.STATUS_NOT_PROCESSED] == 0
    assert state["counts"][backlog.STATUS_PROCESSED] == 0
    assert state["declined"] is False


def _set_api_url(home, url: str) -> None:
    (home / ".tortoise" / "credentials.json").write_text(
        json.dumps({"api_key": "tt_test", "api_url": url}), encoding="utf-8")


def test_backlog_process_is_not_refused_on_a_hosted_target(places, capsys):
    """P1: on the HOSTED lane extraction is decided SERVER-side (the
    deployment sets the provider key), so a client with no local key must still
    be able to run the RECOMMENDED action. The old gate read the CLIENT's env,
    refused, and named a remedy a hosted user could not apply.

    MUTATION THAT REDS THIS: restore the unconditional
    `_llm_provider_available()` refusal in `_process_saved_backlog`.
    """
    home, _ = places
    _set_api_url(home, "https://api.premiselabs.co")
    transcript = _transcript(home, "a.jsonl")
    backlog.record_not_processed(session_id="s_1", harness="pi", turns=4,
                                 estimate=12, reason="no LLM provider key",
                                 file=str(transcript))
    urlopen, posted = _posting(lambda payload: _extracted_body("s_1"))
    with mock.patch("urllib.request.urlopen", urlopen):
        rc = main.main(["sessions", "backlog", "--process"])
    out = capsys.readouterr().out

    assert rc == 0, out
    assert [p["session_id"] for p in posted] == ["s_1"], \
        "the hosted run must reach the POST the user asked for"
    assert "Cannot process" not in out
    assert "no LLM provider key is configured on this machine" not in out, \
        "the hosted lane gets no client-side key note"
    assert backlog.summary()["counts"][backlog.STATUS_PROCESSED] == 1


def test_backlog_process_notes_a_missing_key_only_for_a_self_hosted_target(
        places, capsys):
    """The genuinely-local direction: the note names the SELF-HOSTED target
    and a remedy its user can apply. It is a NOTE, not a refusal — the run's
    per-entry verdict comes from the LEDGER, so it is safe to attempt, and a
    refusal would still assert a server-side fact the client cannot have.

    MUTATION THAT REDS THIS: drop `_backlog_provider_warning()` → the local
    user gets no signal at all.
    """
    home, _ = places
    _set_api_url(home, "https://selfhosted.example.com")
    transcript = _transcript(home, "a.jsonl")
    backlog.record_not_processed(session_id="s_1", harness="pi", turns=4,
                                 estimate=12, reason="no LLM provider key",
                                 file=str(transcript))
    urlopen, posted = _posting(lambda payload: _store_only_body("s_1"))
    with mock.patch("urllib.request.urlopen", urlopen):
        rc = main.main(["sessions", "backlog", "--process"])
    out = capsys.readouterr().out

    assert "self-hosted (selfhosted.example.com)" in out, out
    assert "OPENROUTER_API_KEY" in out and "DEEPSEEK_API_KEY" in out
    assert [p["session_id"] for p in posted] == ["s_1"], \
        "a note is not a refusal — the run still attempts the re-capture"
    # The verdict is read from the ledger, so a still-store-only run is honest.
    assert rc == 1
    assert "still not extracted" in out
    assert backlog.summary()["counts"][backlog.STATUS_NOT_PROCESSED] == 1


def test_backlog_process_re_runs_the_capture_and_marks_the_entry_processed(
        places, capsys, monkeypatch):
    """The backlog run drives the EXISTING re-capture path (the #2335 gate) —
    the same POST the keyed `sessions import` makes, so the #4194 turn write
    (and its embedding) lands for the stored turns too."""
    home, receipts = places
    transcript = _transcript(home, "a.jsonl")
    backlog.record_not_processed(session_id="s_1", harness="pi", turns=4,
                                 estimate=12, reason="no LLM provider key",
                                 file=str(transcript))
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")

    urlopen, posted = _posting(lambda payload: _extracted_body("s_1"))
    with mock.patch("urllib.request.urlopen", urlopen):
        rc = main.main(["sessions", "backlog", "--process"])
    out = capsys.readouterr().out

    assert rc == 0, out
    assert [p["session_id"] for p in posted] == ["s_1"]
    assert "re-capturing s_1" in out, "progress is visible (#4197 AC6)"
    assert "extracted ✓" in out
    assert "1 processed, 0 still not-processed" in out, "completion is visible"
    assert (receipts / "s_1.json").exists(), \
        "the re-capture took the normal receipt path"
    state = backlog.summary()
    assert state["counts"][backlog.STATUS_PROCESSED] == 1
    assert state["counts"][backlog.STATUS_NOT_PROCESSED] == 0
    assert state["cost_units"] == 0


def test_a_multi_entry_run_prints_one_cost_report_and_one_completion(
        places, capsys, monkeypatch):
    """The cost is shown ONCE before the spend, then progress, then a
    completion — never N copies of the report burying the verdict."""
    home, _ = places
    first = _transcript(home, "a.jsonl")
    second = _transcript(home, "b.jsonl")
    for sid, path in (("s_1", first), ("s_2", second)):
        backlog.record_not_processed(session_id=sid, harness="pi", turns=4,
                                     estimate=12, reason="no LLM provider key",
                                     file=str(path))
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    urlopen, _ = _posting(lambda payload: _extracted_body("mock"))
    with mock.patch("urllib.request.urlopen", urlopen):
        rc = main.main(["sessions", "backlog", "--process"])
    out = capsys.readouterr().out

    assert rc == 0, out
    assert out.count("Saved backlog:") == 1, out
    assert out.count("Saved backlog run complete") == 1, out
    assert "[1/2]" in out and "[2/2]" in out
    assert "2 processed, 0 still not-processed" in out
    assert backlog.summary()["counts"][backlog.STATUS_PROCESSED] == 2


def test_backlog_process_keeps_a_failed_entry_and_reports_it(places, capsys,
                                                             monkeypatch):
    """A verdict read from the LEDGER, never from the import's exit code: a
    still-store-only re-import exits 0 while extracting nothing."""
    home, _ = places
    transcript = _transcript(home, "a.jsonl")
    backlog.record_not_processed(session_id="s_1", harness="pi", turns=4,
                                 estimate=12, reason="no LLM provider key",
                                 file=str(transcript))
    # A key is "available" but the server still stores-only (extraction
    # disabled org / provider rejected the work): the run must NOT claim it.
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    with mock.patch("urllib.request.urlopen",
                    _posting(lambda payload: _store_only_body("s_1"))[0]):
        rc = main.main(["sessions", "backlog", "--process"])
    out = capsys.readouterr().out

    assert rc == 1
    assert "still not extracted" in out
    assert "0 processed, 1 still not-processed" in out
    assert backlog.summary()["counts"][backlog.STATUS_NOT_PROCESSED] == 1


def test_backlog_process_reports_a_missing_transcript(places, capsys,
                                                      monkeypatch):
    home, _ = places
    backlog.record_not_processed(session_id="s_gone", harness="pi", turns=4,
                                 estimate=12, reason="no LLM provider key",
                                 file=str(home / "vanished.jsonl"))
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    rc = main.main(["sessions", "backlog", "--process"])
    out = capsys.readouterr().out

    assert rc == 1
    assert "still not extracted" in out
    assert backlog.summary()["counts"][backlog.STATUS_NOT_PROCESSED] == 1


def test_future_only_records_the_choice_and_leaves_the_backlog_alone(
        places, capsys):
    home, _ = places
    backlog.record_not_processed(session_id="s_1", harness="pi", turns=4,
                                 estimate=12, reason="no LLM provider key",
                                 file=str(home / "a.jsonl"))
    with mock.patch("urllib.request.urlopen") as urlopen:
        rc = main.main(["sessions", "backlog", "--future-only"])
    out = capsys.readouterr().out

    assert rc == 0
    assert not urlopen.called, "declining must not extract anything"
    assert "Only future conversations" in out or "only future conversations" in out
    state = backlog.summary()
    assert state["declined"] is True
    assert state["counts"][backlog.STATUS_NOT_PROCESSED] == 1

    # The declined state is distinguishable from "processed" and the choice is
    # re-offerable: the report names the way back.
    following = main.main(["sessions", "backlog"])
    out = capsys.readouterr().out
    assert following == 0
    assert "declined" in out
    assert "--process" in out


def test_future_only_is_overridden_by_a_later_explicit_process(places,
                                                               monkeypatch,
                                                               capsys):
    home, _ = places
    transcript = _transcript(home, "a.jsonl")
    backlog.record_not_processed(session_id="s_1", harness="pi", turns=4,
                                 estimate=12, reason="no LLM provider key",
                                 file=str(transcript))
    assert backlog.set_policy(backlog.CHOICE_FUTURE_ONLY, decided_at="t") is True
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    with mock.patch("urllib.request.urlopen",
                    _posting(lambda payload: _extracted_body("s_1"))[0]):
        rc = main.main(["sessions", "backlog", "--process"])
    capsys.readouterr()

    assert rc == 0
    assert backlog.read_policy()["choice"] == backlog.CHOICE_BACKLOG
    assert backlog.summary()["declined"] is False


def test_a_keyed_import_retires_the_entry_and_offers_what_remains(
        places, capsys, monkeypatch):
    """The offer appears at the point the key just proved it works (#4197 AC1),
    and only while a backlog remains."""
    home, _ = places
    first = _transcript(home, "a.jsonl")
    second = _transcript(home, "b.jsonl")
    backlog.record_not_processed(session_id="s_1", harness="pi", turns=4,
                                 estimate=12, reason="no LLM provider key",
                                 file=str(first))
    backlog.record_not_processed(session_id="s_2", harness="pi", turns=4,
                                 estimate=12, reason="no LLM provider key",
                                 file=str(second))
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    with mock.patch("urllib.request.urlopen",
                    _posting(lambda payload: _extracted_body("s_1"))[0]):
        rc = main._cmd_sessions_import(main.argparse.Namespace(
            file=str(first), harness="pi", session_id="s_1"))
    out = capsys.readouterr().out

    assert rc == 0
    state = backlog.summary()
    assert state["counts"][backlog.STATUS_PROCESSED] == 1
    assert state["counts"][backlog.STATUS_NOT_PROCESSED] == 1
    assert "Saved backlog: 1 not-processed" in out
    assert "--process" in out

    # With the backlog exhausted the offer goes silent — an ordinary import
    # must not become an advertisement.
    with mock.patch("urllib.request.urlopen",
                    _posting(lambda payload: _extracted_body("s_2"))[0]):
        rc = main._cmd_sessions_import(main.argparse.Namespace(
            file=str(second), harness="pi", session_id="s_2"))
    out = capsys.readouterr().out
    assert rc == 0
    assert "Saved backlog" not in out
    assert backlog.summary()["counts"][backlog.STATUS_NOT_PROCESSED] == 0


def test_doctor_reports_the_backlog_and_the_recommended_action(places, capsys,
                                                               tmp_path):
    """`tortoise doctor` is where the provider key's state is reported, so it is
    where the owner's choice belongs."""
    home, _ = places
    backlog.record_not_processed(session_id="s_1", harness="pi", turns=10,
                                 estimate=30, reason="no LLM provider key",
                                 file=str(home / "a.jsonl"))
    main.main(["doctor", "--path", str(tmp_path / "doctor.db")])
    out = capsys.readouterr().out

    line = next(ln for ln in out.splitlines() if "Saved backlog" in ln)
    assert "1 saved session(s)" in line
    assert "~30 estimated extraction unit(s)" in line
    assert "--process" in line and "Recommended" in line


def test_doctor_is_silent_about_a_backlog_that_does_not_exist(places, capsys,
                                                              tmp_path):
    main.main(["doctor", "--path", str(tmp_path / "doctor.db")])
    out = capsys.readouterr().out
    assert "Saved backlog" not in out


def test_the_stored_backlog_survives_an_unwritable_ledger(places, capsys):
    """Every ledger write is best-effort: losing the offer must never cost the
    capture (the hook path's fail-open contract)."""
    with mock.patch.object(backlog, "_write_json", return_value=False):
        assert backlog.record_not_processed(session_id="s_1", turns=1,
                                            estimate=3) is False
    assert backlog.summary()["counts"][backlog.STATUS_NOT_PROCESSED] == 0


# ── The contracts the read path and the run must not violate ───────────────


def test_a_degraded_extraction_does_not_retire_the_entry(places, tmp_path,
                                                        monkeypatch, capsys):
    """A receipt is NOT proof that extraction ran. A DEGRADED 2xx
    (`extraction_mode` error/empty, or a non-empty `errors` list) stored the
    turns and extracted nothing, so it DEFERS like a store-only response:
    retiring the entry would mark a session `processed` that gained no memory
    points, and writing a receipt would make every later re-import
    short-circuit BEFORE the POST, so the entry could never be retired either.
    This runs the whole loop twice, so it also reds the livelock."""
    _, receipts = places
    transcript = _transcript(tmp_path)
    backlog.record_not_processed(session_id="s_1", harness="pi", turns=4,
                                 file=str(transcript), estimate=12,
                                 reason="no LLM provider key")
    degraded = _store_only_body("s_1")
    degraded.update({"extraction_mode": "error", "extracted": 0, "ok": False,
                     "errors": ["upstream provider 500"], "warnings": []})
    with mock.patch("urllib.request.urlopen",
                    _posting(lambda payload: degraded)[0]):
        rc = main._cmd_sessions_import(main.argparse.Namespace(
            file=str(transcript), harness="pi", session_id="s_1"))

    assert rc == 0, "a degraded 2xx is a deferral, not a hard failure"
    assert not (receipts / "s_1.json").exists(), \
        "a receipt would make every later re-import skip the POST, so the " \
        "session could never gain its memory points"
    state = backlog.summary()
    assert state["counts"][backlog.STATUS_NOT_PROCESSED] == 1, \
        "the entry must stay pending: no extraction ever ran for it"
    assert state["counts"][backlog.STATUS_PROCESSED] == 0

    # …and the pending entry must still be ACTIONABLE: the recommended run has
    # to be able to re-POST it, or every read surface offers it forever.
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    urlopen, posted = _posting(lambda payload: _extracted_body("s_1"))
    with mock.patch("urllib.request.urlopen", urlopen):
        rc = main.main(["sessions", "backlog", "--process"])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert [p["session_id"] for p in posted] == ["s_1"], \
        "the re-offer must actually re-POST, not be skipped by a stale receipt"
    assert backlog.summary()["counts"][backlog.STATUS_PROCESSED] == 1


def test_a_session_id_cannot_shadow_the_policy_record(places):
    """The policy record lives in its OWN subdirectory, so no session id can
    address it — including the spellings a naive reserved-name rule misses.
    A relocating rule (`policy` -> `_policy`) is also not injective, and a
    case-insensitive filesystem collapses `POLICY` onto `policy`; both left
    the user's recorded choice destroyed and the session record invisible,
    since the scan skipped that name."""
    assert backlog.set_policy(backlog.CHOICE_FUTURE_ONLY, decided_at="t") is True
    for sid in ("policy", "POLICY", "Policy", "_policy", "s-policy"):
        assert backlog.record_not_processed(session_id=sid, turns=2,
                                            estimate=6) is True

    assert backlog.read_policy()["choice"] == backlog.CHOICE_FUTURE_ONLY, \
        "no session id may overwrite the recorded policy"
    ids = {r["session_id"] for r in backlog.sessions()}
    assert {"policy", "POLICY", "Policy"} & ids, \
        "a session id spelled like the old reserved name must be VISIBLE"
    # The separation is injective: `_policy` is its own record, not the
    # policy's and not `policy`'s (which the relocating rule this replaced
    # would have collided).
    assert {"_policy", "s-policy"} <= ids
    # …and the policy record sits in its own namespace, so it can never be one
    # of the session records.
    policy_dir = backlog.backlog_dir() / "policy"
    assert (policy_dir / "choice.json").is_file()
    assert not any(p.parent == policy_dir for p in backlog.backlog_dir().glob("*.json"))
    assert backlog.summary()["declined"] is True
    assert backlog.summary()["counts"][backlog.STATUS_NOT_PROCESSED] >= 3


def test_the_ledger_never_reaches_the_developers_real_home(monkeypatch):
    """A test (or a PROCESS a test spawns) must never write into the
    developer machine's real ledger (#3721's trap — the same rule
    `capture_spool.spool_dir` and `hook_install`'s src-dir base follow).
    Without an explicit override the path is derived from the test id."""
    monkeypatch.delenv("TORTOISE_IMPORT_RECEIPT_DIR", raising=False)
    monkeypatch.setenv("HOME", os.path.expanduser("~"))
    assert "tortoise-capture-backlog-tests" in backlog.backlog_dir().parts


def test_an_unresolvable_home_cannot_break_the_read_path(monkeypatch):
    """`Path.home()` is fallible (`$HOME` as a literal `~`) and these run on a
    capture's SUCCESS path — a local-bookkeeping failure must degrade to the
    neutral state, never to a traceback after the POST has committed.
    Precedent: `test_hook_run_observation.py`'s resolvable-home guard.

    `PYTEST_CURRENT_TEST` is cleared so the derivation really is reached (the
    test-id fail-safe would otherwise mask it)."""
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("TORTOISE_IMPORT_RECEIPT_DIR", raising=False)
    monkeypatch.setenv("HOME", "~")
    assert backlog.sessions() == []
    assert backlog.read_policy() == {}
    assert backlog.set_policy(backlog.CHOICE_BACKLOG) is False
    state = backlog.summary()
    assert state["counts"][backlog.STATUS_NOT_PROCESSED] == 0
    assert state["declined"] is False
    assert backlog.recommendation(state) is None


def test_a_malformed_record_reads_as_unpriced_not_as_a_traceback(places):
    """The read path runs after a successful capture: a record a human (or an
    older build) edited must read as unpriced, never as a crash and never as a
    free run."""
    target = backlog.backlog_dir() / "s_bad.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({
        "session_id": "s_bad", "status": backlog.STATUS_NOT_PROCESSED,
        "turns": "not-a-number", "estimate": "not-a-number",
    }), encoding="utf-8")

    state = backlog.summary()
    assert state["counts"][backlog.STATUS_NOT_PROCESSED] == 1
    assert state["cost_units"] == 0
    assert state["cost_unknown"] == 1
    report = backlog.format_report(state)
    assert "could not be priced" in report
    assert "cost unknown" in report


def test_the_offer_price_is_the_product_estimator(places):
    """The number the offer shows and the number the hosted 402 gate charges
    are the SAME unit (`sdk._session_extraction_estimate`), so a regression
    that re-priced the offer locally would show up here. An estimator FAILURE
    is `None` (unpriced), never `0` — a free-looking run that spends budget is
    the inverse of the cost-visible-before-the-spend promise."""
    from tortoise.sdk import _session_extraction_estimate

    conversation = [{"role": "user", "content": "a" * 400},
                    {"role": "assistant", "content": "b" * 400}]
    assert backlog.estimate_units(conversation) == \
        int(_session_extraction_estimate(conversation))
    assert backlog.estimate_units(conversation) > 0
    assert backlog.estimate_units(None) == 0
    assert backlog.estimate_units([]) == 0

    with mock.patch("tortoise.sdk._session_extraction_estimate",
                    side_effect=RuntimeError("estimator unavailable")):
        assert backlog.estimate_units(conversation) is None, \
            "a failed price is UNKNOWN, not free"

    # …and an unpriced entry must read as unpriced end to end, rather than
    # being summed as a zero-cost run.
    assert backlog.record_not_processed(session_id="s_unpriced", turns=3,
                                        estimate=None) is True
    state = backlog.summary()
    assert state["cost_units"] == 0
    assert state["cost_unknown"] == 1
    assert "could not be priced" in backlog.format_report(state)


def test_a_verify_probe_is_never_recorded_in_the_backlog(places, tmp_path):
    """A `session verify` probe's transcript is synthetic and lives in a
    tempdir removed at exit; recording it would leave a ledger entry that no
    read surface could ever act on."""
    from tortoise.capture_spool import is_probe_session_id

    probe_id = "verify-claude-20261011T000000Z-abcdef"
    assert is_probe_session_id(probe_id)
    transcript = _transcript(tmp_path)
    with mock.patch("urllib.request.urlopen",
                    _posting(lambda payload: _store_only_body(probe_id))[0]):
        rc = main._cmd_sessions_import(main.argparse.Namespace(
            file=str(transcript), harness="pi", session_id=probe_id))

    assert rc == 0
    assert backlog.sessions() == []


def test_future_only_on_an_empty_backlog_prints_no_recommendation(places,
                                                                 capsys):
    """`recommendation()` is `None` when nothing is pending; printing it
    unguarded emitted a literal `None`."""
    rc = main.main(["sessions", "backlog", "--future-only"])
    out = capsys.readouterr().out

    assert rc == 0
    assert "None" not in out
    assert backlog.read_policy()["choice"] == backlog.CHOICE_FUTURE_ONLY


def test_the_process_report_matches_the_action_it_starts(places, capsys,
                                                        monkeypatch):
    """Overriding a prior `--future-only` must READ as an override: rendering
    the PRE-run state printed "the saved backlog is NOT extracted" immediately
    before extracting it."""
    home, _ = places
    transcript = _transcript(home, "a.jsonl")
    backlog.record_not_processed(session_id="s_1", harness="pi", turns=4,
                                 estimate=12, file=str(transcript))
    assert backlog.set_policy(backlog.CHOICE_FUTURE_ONLY, decided_at="t") is True
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    with mock.patch("urllib.request.urlopen",
                    _posting(lambda payload: _extracted_body("s_1"))[0]):
        rc = main.main(["sessions", "backlog", "--process"])
    out = capsys.readouterr().out

    assert rc == 0, out
    run_report = out.split("[1/1]")[0]
    assert "NOT extracted" not in run_report
    # The report describes the run being STARTED: the policy override is
    # recorded first, so the state reads `not declined`, not `declined`.
    assert "not declined" in run_report


def test_an_unwritable_ledger_fails_softly_for_real(places):
    """The "every write is best-effort and never raises" contract is only
    proven against a REAL failure — a regular file where the ledger directory
    must be — not against a mock that guarantees the result."""
    directory = backlog.backlog_dir()
    directory.parent.mkdir(parents=True, exist_ok=True)
    directory.write_text("not a directory", encoding="utf-8")

    assert backlog.record_not_processed(session_id="s_1", turns=1,
                                        estimate=3) is False
    assert backlog.mark_processed("s_1") is False
    assert backlog.set_policy(backlog.CHOICE_BACKLOG) is False
    assert backlog.sessions() == []
    assert backlog.summary()["counts"][backlog.STATUS_NOT_PROCESSED] == 0


# ── The hook path: recorded durably, and actually re-processable ───────────


def test_a_store_only_session_capture_records_the_durable_transcript(
        places, tmp_path):
    """The Claude Code hook's `--file` is its own `mktemp` conversion, deleted
    on exit — so `--transcript-path` (the durable Claude project transcript) is
    what the ledger must record, under the PARSER name `claude-desktop`, not
    the capture alias `claude` that `sessions import` has no parser for."""
    home, _ = places
    transient = _text_transcript(home, "hook-tmp.jsonl")
    durable = _claude_transcript(home, "project.jsonl")

    with mock.patch("urllib.request.urlopen",
                    _posting(lambda payload: _store_only_body("s_hook"))[0]):
        rc = main._cmd_session_capture(
            main.argparse.Namespace(file=str(transient), harness="claude",
                                    transcript_path=str(durable),
                                    session_id="s_hook", model=None),
            "tt_test", "https://legit.example.com")
    transient.unlink()  # the hook's `trap` — gone before the user ever asks

    assert rc == 0
    record = backlog.sessions()[0]
    assert record["status"] == backlog.STATUS_NOT_PROCESSED
    assert record["file"] == str(durable), \
        "a deleted temp path can never be re-processed"
    assert record["harness"] == "claude-desktop", \
        "the parser name for the durable file — `claude` has no parser"
    assert durable.exists()


def test_a_hook_recorded_entry_is_actually_processable(places, capsys,
                                                      monkeypatch):
    """The point of recording the DURABLE transcript: an entry written by the
    hook path must be re-capturable, not merely listed. Both halves of the old
    defect would red this — the deleted temp path AND the `claude` harness
    `parse_transcript` refuses."""
    home, _ = places
    transient = _text_transcript(home, "hook-tmp.jsonl")
    durable = _claude_transcript(home, "project.jsonl")
    with mock.patch("urllib.request.urlopen",
                    _posting(lambda payload: _store_only_body("s_hook"))[0]):
        assert main._cmd_session_capture(
            main.argparse.Namespace(file=str(transient), harness="claude",
                                    transcript_path=str(durable),
                                    session_id="s_hook", model=None),
            "tt_test", "https://legit.example.com") == 0
    transient.unlink()

    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    urlopen, posted = _posting(lambda payload: _extracted_body("s_hook"))
    with mock.patch("urllib.request.urlopen", urlopen):
        rc = main.main(["sessions", "backlog", "--process"])
    out = capsys.readouterr().out

    assert rc == 0, out
    assert [p["session_id"] for p in posted] == ["s_hook"]
    assert "1 processed, 0 still not-processed" in out
    assert backlog.summary()["counts"][backlog.STATUS_PROCESSED] == 1


def test_a_legacy_claude_harness_record_still_processes(places, capsys,
                                                       monkeypatch):
    """A record written before the harness alias existed (`claude`, the hook's
    capture name) must still process: `parse_transcript` knows
    `claude-desktop`, and the alias is resolved at the point of use."""
    home, _ = places
    transcript = _claude_transcript(home, "legacy.jsonl")
    backlog.record_not_processed(session_id="s_legacy", harness="claude",
                                 turns=4, estimate=12, file=str(transcript))
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    with mock.patch("urllib.request.urlopen",
                    _posting(lambda payload: _extracted_body("s_legacy"))[0]):
        rc = main.main(["sessions", "backlog", "--process"])
    out = capsys.readouterr().out

    assert rc == 0, out
    assert "1 processed, 0 still not-processed" in out
    assert backlog.summary()["counts"][backlog.STATUS_PROCESSED] == 1


def test_the_transcript_path_flag_reaches_the_ledger_through_the_parser(
        places, tmp_path):
    """The flag must be wired in the REAL parser, not only in a hand-built
    Namespace: the consumer reads it with `getattr(args, "transcript_path",
    None)`, which SILENTLY falls back to the transient `--file` if the flag
    ever leaves the parser — reintroducing the deleted-temp-path defect with
    the suite still green."""
    home, _ = places
    transient = _text_transcript(home, "hook-tmp.jsonl")
    durable = _claude_transcript(home, "project.jsonl")

    with mock.patch("urllib.request.urlopen",
                    _posting(lambda payload: _store_only_body("s_cli"))[0]):
        rc = main.main(["session", "capture", "--file", str(transient),
                        "--harness", "claude",
                        "--transcript-path", str(durable),
                        "--session-id", "s_cli"])
    transient.unlink()

    assert rc == 0
    record = backlog.sessions()[0]
    assert record["file"] == str(durable)
    assert record["harness"] == "claude-desktop"


def test_a_hook_capture_that_extracts_retires_a_deferred_entry(places,
                                                               tmp_path):
    """The hook path's OTHER half: once the hook captures a previously
    deferred session WITH extraction, its ledger entry must be retired. Only
    the `sessions import` half is otherwise pinned, so deleting this call left
    a session deferred once and forever re-offered."""
    home, _ = places
    first = _text_transcript(home, "hook-tmp.jsonl")
    args = main.argparse.Namespace(file=str(first), harness="claude",
                                   transcript_path=None,
                                   session_id="s_retire", model=None)
    with mock.patch("urllib.request.urlopen",
                    _posting(lambda payload: _store_only_body("s_retire"))[0]):
        assert main._cmd_session_capture(
            args, "tt_test", "https://legit.example.com") == 0
    assert backlog.summary()["counts"][backlog.STATUS_NOT_PROCESSED] == 1

    # The second capture carries MORE turns, so the spool sees new content and
    # actually re-files it (an unchanged entry is "already filed — nothing to
    # capture" and never reaches the extraction branch).
    args.file = str(_text_transcript(home, "hook-tmp.jsonl", turns=6))
    with mock.patch("urllib.request.urlopen",
                    _posting(lambda payload: _extracted_body("s_retire"))[0]):
        assert main._cmd_session_capture(
            args, "tt_test", "https://legit.example.com") == 0
    state = backlog.summary()
    assert state["counts"][backlog.STATUS_PROCESSED] == 1, \
        "a hook capture that extracted must retire the entry"
    assert state["counts"][backlog.STATUS_NOT_PROCESSED] == 0


def test_process_names_an_entry_with_no_recorded_transcript(places, capsys,
                                                            monkeypatch):
    """A record with no `file` (a hand-edited ledger, or an older build) must
    be NAMED as skipped rather than silently counted as processed."""
    home, _ = places
    good = _transcript(home, "a.jsonl")
    backlog.record_not_processed(session_id="s_file", harness="pi", turns=4,
                                 estimate=12, file=str(good))
    backlog.record_not_processed(session_id="s_nofile", harness="pi", turns=2,
                                 estimate=6, file=None)
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    with mock.patch("urllib.request.urlopen",
                    _posting(lambda payload: _extracted_body("s_file"))[0]):
        rc = main.main(["sessions", "backlog", "--process"])
    out = capsys.readouterr().out

    assert rc == 0, out
    assert "1 processed, 0 still not-processed" in out
    assert "skipped s_nofile" in out
    state = backlog.summary()
    assert state["counts"][backlog.STATUS_PROCESSED] == 1
    assert state["counts"][backlog.STATUS_NOT_PROCESSED] == 1
    # The skipped entry stays not-processed, so the run must not report a
    # fully-drained backlog.
    assert state["counts"][backlog.STATUS_NOT_PROCESSED] == 1


def test_process_with_nothing_to_do_does_not_blame_the_provider_key(
        places, capsys):
    """An empty backlog on a keyless machine is a no-op, not a missing-key
    error: the stated cause would be false."""
    rc = main.main(["sessions", "backlog", "--process"])
    out = capsys.readouterr().out

    assert rc == 0
    assert "No saved backlog to process." in out
    assert "no LLM provider key" not in out


def test_json_refuses_to_be_combined_with_an_action(places):
    """`--json` is the state document, not an action. Both action branches
    return before the report, so combining them used to print prose and NO
    document while the flag was silently ignored. argparse must refuse it."""
    with pytest.raises(SystemExit):
        main.main(["sessions", "backlog", "--future-only", "--json"])
    with pytest.raises(SystemExit):
        main.main(["sessions", "backlog", "--process", "--json"])


# ── `session drain`: the THIRD consent-gated transmission primitive ─────────


def test_a_drain_delivered_store_only_session_is_recorded(places, capsys,
                                                          tmp_path,
                                                          monkeypatch):
    """`session drain` files a session spooled by an interrupted capture, and a
    store-only server response there left NO local record at all — turns stored,
    no receipt (by design), the capture-failure breadcrumb cleared by the 2xx —
    so the offer missed exactly the case it exists for. The drain must record
    it.

    The spool keeps the turns but not the transcript PATH, so the entry is
    recorded without one; the read surface and `--process` say so instead of
    claiming the backlog is empty.

    MUTATION THAT REDS THIS: drop `_record_drain_deferrals` from
    `_cmd_session_drain`.
    """
    from tortoise.capture_spool import (
        PostOutcome,
        Snapshot,
        spool_dir,
        write_spool_entry,
    )

    home, _ = places
    _set_api_url(home, "https://legit.example.com")
    monkeypatch.setenv("TORTOISE_CAPTURE_SPOOL_DIR", str(tmp_path / "spool"))
    monkeypatch.setenv("TORTOISE_CAPTURE", "1")
    turns = [{"role": "user", "content": "stored turn"},
             {"role": "assistant", "content": "stored reply"}]
    write_spool_entry(spool_dir(), Snapshot(
        session_id="s_drained", turns=turns, source="t", machine_id="m",
        harness="claude"))

    monkeypatch.setattr(
        main, "_session_post",
        lambda api_key, api_url: (lambda payload: PostOutcome(
            ok=True, status=200, body=_store_only_body("s_drained"))))
    rc = main.main(["session", "drain"])
    out = capsys.readouterr().out

    assert rc == 0, out
    state = backlog.summary()
    assert state["counts"][backlog.STATUS_NOT_PROCESSED] == 1
    record = backlog.sessions()[0]
    assert record["session_id"] == "s_drained"
    assert record["harness"] == "claude"
    assert record["turns"] == 2
    assert record["reason"] == "no-provider"
    assert record["file"] is None, "the spool holds turns, not the path"


def test_a_drain_record_never_erases_a_known_transcript_path(places):
    """A record written WITHOUT a path must not erase one a previous writer
    recorded: `session capture` knows the durable transcript, the drain does
    not. Losing the path would leave an entry the run can no longer act on.

    MUTATION THAT REDS THIS: make `record_not_processed` rebuild the record
    from its own arguments alone (the pre-fix shape).
    """
    home, _ = places
    transcript = home / "a.jsonl"
    transcript.write_text("User: hi\nAssistant: yes\n", encoding="utf-8")
    assert backlog.record_not_processed(
        session_id="s_1", harness="claude-desktop", file=str(transcript),
        turns=2, estimate=6) is True
    assert backlog.record_not_processed(
        session_id="s_1", harness="claude", turns=2, estimate=6,
        reason="no-provider") is True

    assert backlog.sessions()[0]["file"] == str(transcript)


def test_process_reports_unprocessable_entries_instead_of_claiming_no_backlog(
        places, capsys):
    """A drain-recorded entry has no transcript path. `--process` must not say
    "No saved backlog to process" — the backlog is not empty, it is
    unprocessable — and must give the remedy.

    MUTATION THAT REDS THIS: restore the `if not entries: print("No saved
    backlog to process.")` early return that skipped the skipped-entry lines.
    """
    backlog.record_not_processed(session_id="s_drained", harness="claude",
                                 turns=4, estimate=12, reason="no-provider")
    rc = main.main(["sessions", "backlog", "--process"])
    out = capsys.readouterr().out

    assert rc == 0, out
    assert "No saved backlog to process" not in out
    assert "no transcript path" in out
    assert "s_drained" in out
    assert "skipped" in out


# ── The read surface must not assert a state it cannot know ────────────────


def test_the_read_surface_reports_a_local_record_not_a_server_verdict(
        places, capsys):
    """The ledger is retired only by the capture/re-import paths on THIS
    machine, so a session extracted since by another path (the hosted MCP
    `tortoise_session_capture`, or `TortoiseSDK.capture_session`) is invisible
    here. The report and `doctor` must say the record is LOCAL rather than
    assert a state this surface cannot know — the server-side status read is
    #3520's, deliberately not absorbed here.

    MUTATION THAT REDS THIS: restore the unqualified "were captured without
    extraction" wording.
    """
    home, _ = places
    backlog.record_not_processed(session_id="s_1", harness="pi", turns=10,
                                 estimate=30, reason="no LLM provider key",
                                 file=str(home / "a.jsonl"))
    assert main.main(["sessions", "backlog"]) == 0
    out = capsys.readouterr().out
    assert "Local record only" in out
    assert "not visible here" in out

    main.main(["doctor", "--path", str(home / "doctor.db")])
    doctor_out = capsys.readouterr().out
    line = next(ln for ln in doctor_out.splitlines() if "Saved backlog" in ln)
    assert "LOCALLY RECORDED" in line
    assert "not visible here" in line


# ── The staged write must be atomic PER WRITER ─────────────────────────────


def test_each_ledger_writer_stages_through_its_own_temp_file(places,
                                                             monkeypatch):
    """The temp-then-`replace` is atomic only if every writer owns its temp
    file. A fixed `<name>.tmp` is shared by two concurrent writers of the SAME
    session (per-turn hooks fire concurrently): their writes interleave and one
    `replace` publishes a TORN record, which `_read_json` then reads as
    "nothing recorded" — the entry silently drops out of the offer.

    MUTATION THAT REDS THIS: restore `tmp = path.with_name(path.name + ".tmp")`.
    """
    import os as _os

    seen: list[str] = []
    real_replace = _os.replace

    def spy(src, dst):
        seen.append(str(src))
        return real_replace(src, dst)

    monkeypatch.setattr(_os, "replace", spy)
    assert backlog.record_not_processed(session_id="s1", turns=1, estimate=1)
    assert backlog.record_not_processed(session_id="s1", turns=2, estimate=2)

    assert len(seen) == 2
    assert seen[0] != seen[1], "each writer needs its OWN temp file"
    assert all(name.endswith(".tmp") for name in seen)
    assert backlog.summary()["counts"][backlog.STATUS_NOT_PROCESSED] == 1
    assert not list(backlog.backlog_dir().glob("*.tmp")), \
        "a consumed temp file must not be left behind"


# ── A DEGRADED 2xx defers at EVERY writer, not just at `sessions import` ────


def test_a_degraded_capture_records_the_deferral_and_still_fails(places,
                                                                 tmp_path,
                                                                 capsys):
    """`session capture` is the Claude Code SessionEnd hook — where most of the
    backlog comes from — and a DEGRADED 2xx there used to leave NO trace at all:
    the 2xx stamps `filed_key` in the spool (so no later drain re-POSTs it), it
    clears the capture-failure breadcrumb, no receipt is written by design, and
    the hook discards stderr (`2>/dev/null || exit 0`). The turns were stored
    with no memory points and the offer never saw them. Only the `sessions
    import` site was pinned, so the other two writers were free to disagree.

    MUTATION THAT REDS THIS: move the `_degraded_extraction` branch back to a
    bare `return 1` (the pre-fix shape, which ran BEFORE the store-only branch
    that records).
    """
    home, _ = places
    transient = _text_transcript(home, "hook-tmp.jsonl")
    durable = _claude_transcript(home, "project.jsonl")
    degraded = _store_only_body("s_deg")
    degraded.update({"extraction_mode": "error", "extracted": 0, "ok": False,
                     "errors": ["upstream provider 500"]})

    with mock.patch("urllib.request.urlopen",
                    _posting(lambda payload: degraded)[0]):
        rc = main.main(["session", "capture", "--file", str(transient),
                        "--harness", "claude",
                        "--transcript-path", str(durable),
                        "--session-id", "s_deg"])
    transient.unlink()  # the hook's own trap does this moments later

    assert rc == 1, "a degraded extraction is still a FAILED capture"
    assert "capture failed" in capsys.readouterr().err
    state = backlog.summary()
    assert state["counts"][backlog.STATUS_NOT_PROCESSED] == 1, \
        "the stored-but-un-extracted session must be offered for a re-capture"
    record = backlog.sessions()[0]
    # …and it must carry the transcript a later run can RE-READ: the hook's
    # `--file` is gone, so only the durable path is actionable.
    assert record["file"] == str(durable)
    assert record["harness"] == "claude-desktop", \
        "the parser-named harness, not the capture alias"
    assert record["reason"] == "extraction failed: error"


def test_a_degraded_drain_is_recorded(places, capsys, tmp_path, monkeypatch):
    """The third writer. `flush_spool` marks the entry filed on ANY 2xx, so a
    drain-delivered degraded extraction was unreachable by the offer forever.

    MUTATION THAT REDS THIS: narrow `_record_drain_deferrals` back to the two
    store-only modes.
    """
    from tortoise.capture_spool import PostOutcome, Snapshot, spool_dir, write_spool_entry

    home, _ = places
    (home / ".tortoise" / "credentials.json").write_text(
        json.dumps({"api_key": "tt_test",
                    "api_url": "https://legit.example.com"}), encoding="utf-8")
    monkeypatch.setenv("TORTOISE_CAPTURE_SPOOL_DIR", str(tmp_path / "spool"))
    write_spool_entry(spool_dir(), Snapshot(
        session_id="s_deg_drain",
        turns=[{"role": "user", "content": "stored turn"},
               {"role": "assistant", "content": "stored reply"}],
        source="t", machine_id="m", harness="claude"))
    degraded = _store_only_body("s_deg_drain")
    degraded.update({"extraction_mode": "empty", "extracted": 0, "ok": False,
                     "errors": []})

    monkeypatch.setattr(
        main, "_session_post",
        lambda api_key, api_url: (lambda payload: PostOutcome(
            ok=True, status=200, body=degraded)))
    rc = main.main(["session", "drain"])
    out = capsys.readouterr().out

    assert rc == 0, out
    state = backlog.summary()
    assert state["counts"][backlog.STATUS_NOT_PROCESSED] == 1
    record = backlog.sessions()[0]
    assert record["session_id"] == "s_deg_drain"
    assert record["reason"] == "extraction failed: empty"
    assert record["turns"] == 2
    assert record["file"] is None, "the spool holds turns, not the path"


# ── The id -> path map is INJECTIVE ────────────────────────────────────────


def test_colliding_session_ids_never_share_a_record(places):
    """`_session_path` sanitises by SUBSTITUTION, which merges `a/b`, `a..b` and
    `a_b` onto one path. That is not cosmetic: the writers PRESERVE a recorded
    transcript path, so the second id would inherit the first's `file` and
    `--process` would re-import one session's transcript under a DIFFERENT
    session id — memory points attributed to the wrong session. A digest of the
    RAW id disambiguates.

    MUTATION THAT REDS THIS: drop the `safe != raw` digest from `_session_path`.
    """
    first = _transcript(backlog.backlog_dir().parent, "first.jsonl")
    backlog.record_not_processed(session_id="a/b", harness="pi",
                                 file=str(first), turns=4, estimate=12)
    backlog.record_not_processed(session_id="a..b", harness="pi",
                                 turns=7, estimate=21)
    backlog.record_not_processed(session_id="a_b", harness="pi",
                                 turns=2, estimate=6)

    records = {r["session_id"]: r for r in backlog.sessions()}
    assert set(records) == {"a/b", "a..b", "a_b"}, \
        "three distinct ids must be three distinct records"
    assert records["a..b"].get("file") is None, \
        "a colliding id must never inherit another session's transcript"
    assert records["a/b"]["file"] == str(first)
    assert backlog.summary()["counts"][backlog.STATUS_NOT_PROCESSED] == 3


def test_a_session_id_spelled_like_another_records_name_does_not_collide(places):
    """Appending the digest ONLY when the sanitiser changed the id leaves the
    collision open in the other direction: a raw id spelled like another's
    suffixed name maps back onto it. The digest is therefore ALWAYS present, so
    no SPELLING can reproduce a name.

    MUTATION THAT REDS THIS: restore the `if safe != raw` guard around the
    digest.
    """
    doomed = backlog._session_path("a/b").name
    crafted = doomed[: -len(".json")]

    assert backlog.record_not_processed(session_id="a/b", turns=4) is True
    assert backlog.record_not_processed(session_id=crafted, turns=9) is True

    records = {r["session_id"]: r for r in backlog.sessions()}
    assert set(records) == {"a/b", crafted}, \
        "no id spelling may reproduce another record's name"
    assert records["a/b"]["turns"] == 4
    assert records[crafted]["turns"] == 9


def test_a_session_id_can_never_be_written_outside_the_ledger(places):
    """The injectivity digest must not weaken the traversal guard it joined: a
    `../..` id still lands INSIDE the ledger, and still lands somewhere the
    directory scan can see (a write the reader cannot see is a write-nothing)."""
    assert backlog.record_not_processed(session_id="../../etc/passwd",
                                        turns=1, estimate=3) is True
    directory = backlog.backlog_dir()
    written = list(directory.glob("*.json"))
    assert len(written) == 1
    assert written[0].parent == directory
    assert [r["session_id"] for r in backlog.sessions()] == ["../../etc/passwd"]


# ── One record per session id, and the LATEST one wins ─────────────────────


def test_two_records_for_one_id_report_the_latest(places):
    """An orphan that shares a session id (an earlier layout left one, or a path
    collision) must not pin the session's state: the `--process` verdict lookup
    takes the FIRST match by `recorded_at`, so an older `not-processed` record
    would report a session as never-extracted forever and keep the pending count
    and price inflated.

    MUTATION THAT REDS THIS: return the sorted list without the dedupe.
    """
    backlog.record_not_processed(session_id="s_dup", turns=4, estimate=12)
    orphan = backlog.backlog_dir() / "orphan.json"
    orphan.write_text(json.dumps({
        "session_id": "s_dup", "harness": "pi", "file": None,
        "turns": 4, "estimate": 12, "reason": "no LLM provider key",
        "recorded_at": "2000-01-01T00:00:00Z", "status": "processed",
    }), encoding="utf-8")

    records = backlog.sessions()
    assert [r["session_id"] for r in records] == ["s_dup"], \
        "one id must be one record"
    assert records[0]["status"] == backlog.STATUS_PROCESSED, \
        "the LATEST write wins, whatever the filename ordering"
    state = backlog.summary()
    assert state["counts"][backlog.STATUS_NOT_PROCESSED] == 0
    assert state["counts"][backlog.STATUS_PROCESSED] == 1


# ── The policy record's earlier, top-level layout is still READ ────────────


def test_a_legacy_top_level_policy_record_is_still_honoured(places):
    """The policy record moved into its own subdirectory. The shape never
    shipped (this module debuts in this change), but a development machine can
    still hold the old one, and reading it as "the user was never asked" would
    silently REVERSE an explicit "keep only future conversations" into "the
    choice is still open" — the conflation the module exists to keep apart.

    MUTATION THAT REDS THIS: drop the `_LEGACY_POLICY_FILE` fallback.
    """
    (backlog.backlog_dir()).mkdir(parents=True, exist_ok=True)
    (backlog.backlog_dir() / "policy.json").write_text(json.dumps(
        {"choice": backlog.CHOICE_FUTURE_ONLY,
         "decided_at": "2026-01-01T00:00:00Z"}), encoding="utf-8")

    assert backlog.read_policy()["choice"] == backlog.CHOICE_FUTURE_ONLY
    state = backlog.summary()
    assert state["declined"] is True
    assert state["choice"] == backlog.CHOICE_FUTURE_ONLY

    # …and the fallback is READ-ONLY: the next recorded choice uses the new
    # location, so the old file can never win again.
    assert backlog.set_policy(backlog.CHOICE_BACKLOG,
                              decided_at="2026-02-02T00:00:00Z") is True
    assert (backlog.backlog_dir() / "policy" / "choice.json").is_file()
    assert backlog.read_policy()["choice"] == backlog.CHOICE_BACKLOG

    # A record with no session id is NOT a session record: the legacy policy
    # file must not be enumerated as one.
    assert backlog.sessions() == []


# ── Both cost surfaces render an UNPRICED entry the same way ───────────────


def test_doctor_renders_an_unpriced_entry(places, capsys, tmp_path):
    """`cost_units` sums an unpriced entry as 0, so a doctor row that reads only
    `cost_units` prints "~0 estimated extraction unit(s)" for a backlog that is
    merely unknown-priced — the inverse of the cost-visible-before-the-spend
    promise, and a divergence from `format_report`, which says so.

    MUTATION THAT REDS THIS: drop the `cost_unknown` clause from the doctor row.
    """
    home, _ = places
    with mock.patch("tortoise.sdk._session_extraction_estimate",
                    side_effect=RuntimeError("estimator unavailable")):
        assert backlog.record_not_processed(
            session_id="s_unpriced", harness="pi", turns=3,
            estimate=backlog.estimate_units([{"role": "user", "content": "x"}]),
            file=str(home / "a.jsonl")) is True

    main.main(["doctor", "--path", str(tmp_path / "doctor.db")])
    line = next(ln for ln in capsys.readouterr().out.splitlines()
                if "Saved backlog" in ln)
    assert "~0 estimated extraction unit(s)" in line
    assert "could not be priced" in line, \
        "an unpriced backlog must not read as a free one"


def test_future_only_write_failure_goes_to_stderr(places, capsys):
    """This CLI's failure contract is human text on STDERR (#875 P2), asserted
    by `_cmd_fail` and followed by every sibling refusal. A caller that consumes
    stdout would otherwise see failure prose where the report belongs.

    MUTATION THAT REDS THIS: drop `file=sys.stderr` from that print.
    """
    with mock.patch.object(backlog, "set_policy", return_value=False):
        rc = main.main(["sessions", "backlog", "--future-only"])

    captured = capsys.readouterr()
    assert rc == 1
    assert "Could not record the choice" not in captured.out
    assert "Could not record the choice" in captured.err
