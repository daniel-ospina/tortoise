"""#3615 — hosted session capture requires EXplicit consent.

The defect this pins: `TORTOISE_API_KEY` carried two meanings — the MCP Bearer
credential (its only meaning now) and an implicit opt-in to ship session
transcripts to the hosted vendor. A credential is an authentication artifact;
capture is data-sharing. This module tests the CLI-side gate (the primitive):
a resolvable credential must never be sufficient to upload a transcript.

The bash twin of the predicate (session-end.sh) is pinned for parity in
tests/test_session_capture_e2e.py.
"""
from __future__ import annotations

import json
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tortoise.__main__ import main
from tortoise.capture_consent import (
    CAPTURE_OPT_IN_ENV,
    capture_consent_enabled,
    capture_notice_path,
    capture_notice_shown_path,
    pending_capture_notice,
)

GLOBAL_CFG = {
    "api_key": "tt_global", "api_url": "https://api.premiselabs.co",
    "org_id": "team-g", "org_name": "Global", "device_id": "anon-g",
}


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    """Never read the developer's real ~/.tortoise credentials, cwd config, or
    an ambient capture opt-in (a stray TORTOISE_CAPTURE in the shell would
    silently flip the negative tests)."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
    monkeypatch.delenv("TORTOISE_API_URL", raising=False)
    monkeypatch.delenv(CAPTURE_OPT_IN_ENV, raising=False)
    monkeypatch.chdir(tmp_path)


def _seed_global(tmp_path):
    d = tmp_path / ".tortoise"
    d.mkdir(parents=True, exist_ok=True)
    (d / "credentials.json").write_text(json.dumps(GLOBAL_CFG), encoding="utf-8")


def _ok(body: dict) -> mock.MagicMock:
    resp = mock.MagicMock()
    resp.read.return_value = json.dumps(body).encode()
    resp.__enter__.return_value = resp
    return resp


@pytest.fixture()
def transcript(tmp_path):
    p = tmp_path / "conv.txt"
    p.write_text("User: hello\nAssistant: hi there\n", encoding="utf-8")
    return p


# ── the predicate ─────────────────────────────────────────────────────────

def test_opt_in_defaults_off_when_unset():
    assert capture_consent_enabled({}) is False


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "True", "yes", "YES",
                                   "on", "On", " 1 ", "\ttrue\n"])
def test_opt_in_truthy_values(value):
    assert capture_consent_enabled({CAPTURE_OPT_IN_ENV: value}) is True


@pytest.mark.parametrize("value", ["", " ", "0", "false", "False", "no", "off",
                                   "2", "y", "enable", "capture"])
def test_opt_in_falsy_values(value):
    assert capture_consent_enabled({CAPTURE_OPT_IN_ENV: value}) is False


@pytest.mark.parametrize("value", ["1\x1c", "\x1c1", "\x1d1", "\x1f1", "1\x1f"])
def test_c1_controls_are_not_whitespace(value):
    """Security review P2: Python's str.strip() trims C1 controls that bash's
    [[:space:]] leaves in place, so the old predicate AUTHORIZED on values the
    shipped hook read as off. Trimming is ASCII-only now, so both refuse."""
    assert capture_consent_enabled({CAPTURE_OPT_IN_ENV: value}) is False


def test_opt_in_is_not_inferred_from_the_credential():
    """The whole point of #3615: a credential is not consent."""
    assert capture_consent_enabled({"TORTOISE_API_KEY": "tt_key"}) is False
    assert capture_consent_enabled({"TORTOISE_API_URL": "https://x"}) is False


# ── the CLI primitive: `session capture` ──────────────────────────────────

class TestSessionCaptureConsentGate:
    def test_capture_refused_without_consent(self, tmp_path, transcript, capsys):
        """A credential resolves, but nothing is uploaded."""
        _seed_global(tmp_path)
        with mock.patch("urllib.request.urlopen") as urlopen:
            rc = main(["session", "capture", "--file", str(transcript)])
        assert rc != 0
        assert not urlopen.called, "no transcript may leave the machine"
        err = capsys.readouterr().err
        assert "explicit consent" in err
        assert CAPTURE_OPT_IN_ENV in err

    def test_capture_runs_with_consent(self, tmp_path, transcript, monkeypatch):
        _seed_global(tmp_path)
        monkeypatch.setenv(CAPTURE_OPT_IN_ENV, "1")
        with mock.patch("urllib.request.urlopen",
                        return_value=_ok({"session_id": "s1"})) as urlopen:
            rc = main(["session", "capture", "--file", str(transcript)])
        assert rc == 0
        req = urlopen.call_args.args[0]
        assert req.full_url == "https://api.premiselabs.co/v1/sessions"
        assert req.headers["Authorization"] == "Bearer tt_global"


# ── the CLI primitive: `sessions import` (the second upload path) ─────────

def test_sessions_import_refused_without_consent(tmp_path, capsys):
    """The second transcript-upload primitive must not be a consent bypass."""
    with mock.patch("urllib.request.urlopen") as urlopen:
        rc = main(["sessions", "import", "--harness", "codex",
                   "--file", str(tmp_path / "missing.jsonl")])
    assert rc != 0
    assert not urlopen.called
    assert "explicit consent" in capsys.readouterr().err


# ── the durable one-time migration notice ─────────────────────────────────

def test_declined_capture_writes_a_durable_one_time_notice(tmp_path, transcript):
    """A STALE copied hook runs `tortoise session capture … 2>/dev/null` and
    discards the CLI's stderr — so the migration notice must land on DISK, once.
    Without this the population the change targets migrates silently."""
    _seed_global(tmp_path)
    notice = capture_notice_path(tmp_path)
    assert not notice.exists()
    with mock.patch("urllib.request.urlopen"):
        assert main(["session", "capture", "--file", str(transcript)]) != 0
    assert notice.exists(), "declined capture must leave a discoverable trace"
    body = notice.read_text(encoding="utf-8")
    assert CAPTURE_OPT_IN_ENV in body and "explicit consent" in body

    first = notice.stat().st_mtime_ns
    with mock.patch("urllib.request.urlopen"):
        assert main(["session", "capture", "--file", str(transcript)]) != 0
    assert notice.stat().st_mtime_ns == first, "the notice is one-time, not per-run"


def test_record_capture_declined_is_first_write_only(tmp_path):
    from tortoise.capture_consent import record_capture_declined
    assert record_capture_declined(tmp_path) is True
    assert record_capture_declined(tmp_path) is False


# ── migration DELIVERY: the notice must reach a human, not just a file ────

def test_a_refusal_does_not_consume_the_notice(tmp_path, transcript):
    """Writing the file is not delivering the notice. If a refusal stamped the
    notice "shown", the person would never actually be told (a typo'd --file on
    a machine command is enough to burn it)."""
    _seed_global(tmp_path)
    with mock.patch("urllib.request.urlopen"):
        assert main(["session", "capture", "--file", str(transcript)]) != 0
    assert pending_capture_notice(tmp_path) is not None
    assert not capture_notice_shown_path(tmp_path).exists()


def test_machine_facing_commands_never_consume_the_notice(tmp_path, transcript):
    """Both transcript primitives are called BY hooks (stderr discarded), so
    they must never burn the human's one sighting of the notice."""
    _seed_global(tmp_path)
    with mock.patch("urllib.request.urlopen"):
        main(["session", "capture", "--file", str(transcript)])
        main(["sessions", "import", "--file", str(transcript), "--harness", "codex"])
    assert pending_capture_notice(tmp_path) is not None
    assert not capture_notice_shown_path(tmp_path).exists()


def test_interactive_command_pushes_the_pending_notice_once(tmp_path, transcript,
                                                            capsys, monkeypatch):
    """Solution-verify cycle 2 P1: the targeted population runs a STALE copied
    hook that swallows the refusal, and has no reason to ever run `doctor`, so a
    file-only notice is evidence without notification. The next HUMAN-FACING
    command must deliver it — exactly once.
    """
    monkeypatch.setattr("tortoise.__main__._stderr_is_human_facing", lambda: True)
    _seed_global(tmp_path)
    with mock.patch("urllib.request.urlopen"):
        main(["session", "capture", "--file", str(transcript)])
    capsys.readouterr()

    main([])  # bare `tortoise` → help; any interactive command qualifies
    err = capsys.readouterr().err
    assert CAPTURE_OPT_IN_ENV in err, err
    assert "explicit consent" in err, err

    main([])
    assert CAPTURE_OPT_IN_ENV not in capsys.readouterr().err, "shown twice"


def test_non_terminal_run_never_consumes_the_notice(tmp_path, transcript, capsys):
    """Security review P1 regression: the delivery gate is the SURFACE, not a
    command list.

    The first cut exempted five commands by name and missed `context` — the
    command the SessionStart hook runs as `tortoise context 2>/dev/null`. On a
    standard install (both hooks) the end-of-session refusal wrote the durable
    notice, and the next session START printed it into /dev/null and stamped it
    shown: the human's single sighting was consumed on exactly the hosts the
    migration exists for. `volunteer` (whose hook relays only prefixed lines)
    had the same hole. A non-terminal stderr is what the DEFAULT unattended
    consumers share, so gating on it closes that path rather than extending the
    list — a pty-allocating non-human caller is the declared residual (see
    `tortoise.__main__._stderr_is_human_facing`).
    """
    from tortoise.__main__ import _flush_pending_capture_notice, _stderr_is_human_facing

    _seed_global(tmp_path)
    with mock.patch("urllib.request.urlopen"):
        main(["session", "capture", "--file", str(transcript)])
    capsys.readouterr()

    assert not _stderr_is_human_facing(), "capsys stderr is not a terminal"
    # One gate covers every command, the hook-invoked ones included.
    _flush_pending_capture_notice()
    assert "explicit consent" not in capsys.readouterr().err
    assert pending_capture_notice(tmp_path) is not None, (
        "a non-terminal run consumed the human's single sighting")
    assert not capture_notice_shown_path(tmp_path).exists()


def test_doctor_reports_consent_without_a_false_warning(capsys):
    """The doctor row is the migration's diagnostic surface; capture-off is the
    privacy-correct default, so it must NOT be painted as a warning (every
    healthy install would then never read 'All checks passing')."""
    assert main(["doctor"]) in (0, 1)
    out = capsys.readouterr().out
    row = next((ln for ln in out.splitlines() if "Session capture" in ln), None)
    assert row is not None, out[-2000:]
    assert "⚠️" not in row, row
    assert CAPTURE_OPT_IN_ENV in row, row


def test_capture_refused_for_a_self_hosted_endpoint_too(tmp_path, transcript,
                                                        monkeypatch):
    """The predicate is deliberate host-agnostic: a self-hosted daemon is still
    data leaving the machine, so the consent requirement does not depend on the
    endpoint the resolved config names."""
    monkeypatch.setenv("TORTOISE_API_KEY", "tt_selfhosted")
    monkeypatch.setenv("TORTOISE_API_URL", "http://localhost:8000")
    with mock.patch("urllib.request.urlopen") as urlopen:
        rc = main(["session", "capture", "--file", str(transcript)])
    assert rc != 0
    assert not urlopen.called


# ── boundary: the install probe is content-free telemetry, not capture ────

def test_install_probe_is_not_capture_gated(tmp_path):
    """`session probe` sends harness + timestamp only — it is install
    telemetry, explicitly out of #3615's consent boundary (the server does not
    gate it on session_recording either)."""
    _seed_global(tmp_path)
    with mock.patch("urllib.request.urlopen",
                    return_value=_ok({"harness": "claude"})) as urlopen:
        rc = main(["session", "probe", "--harness", "claude"])
    assert rc == 0
    assert urlopen.called
