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
import stat
import sys
import threading
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tests._signal_hygiene import harness_safe_sigalrm
from tortoise.__main__ import main
from tortoise.capture_consent import (
    CAPTURE_DECLINED_HINT,
    CAPTURE_OPT_IN_ENV,
    capture_consent_enabled,
    capture_declined_reason,
    capture_notice_path,
    capture_notice_shown_path,
    pending_capture_notice,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

GLOBAL_CFG = {
    "api_key": "tt_global", "api_url": "https://api.premiselabs.co",
    "org_id": "team-g", "org_name": "Global", "device_id": "anon-g",
}


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path, _capture_consent_default_on):
    """Never read the developer's real ~/.tortoise credentials, cwd config, or
    an ambient capture opt-in (a stray TORTOISE_CAPTURE in the shell would
    silently flip the negative tests).

    #4276: the suite-wide default (``tests/conftest.py``) now GRANTS capture
    consent, so this whole file — the decline/parity matrix — must opt back out
    explicitly. ``_capture_consent_default_on`` is declared as a DEPENDENCY so
    the opt-out is ordered AFTER the grant regardless of pytest's same-scope
    autouse ordering; a decline test that inherited the grant would pass
    vacuously (the exact fail-open shape #4276 must not introduce).
    """
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


# ── #3684: the notice write must not FOLLOW a symlink ────────────────────

def test_a_dangling_symlink_at_the_notice_path_is_not_followed(tmp_path):
    """#3684: `Path.exists()` follows symlinks and so did `write_text`, so a
    DANGLING link defeated the first-write guard — `exists()` saw nothing, the
    guard passed, and the write then CREATED an attacker-chosen target. The
    write must refuse the link rather than follow it."""
    from tortoise.capture_consent import record_capture_declined
    victim = tmp_path / "some-other-file"
    notice = capture_notice_path(tmp_path)
    notice.parent.mkdir(parents=True, exist_ok=True)
    notice.symlink_to(victim)

    assert notice.exists() is False, "a dangling link is invisible to exists()"
    assert record_capture_declined(tmp_path) is False
    assert not victim.exists(), "the notice was written THROUGH the symlink"
    assert notice.is_symlink(), "the link is left alone, not replaced"


def test_a_symlink_to_an_existing_file_is_not_followed(tmp_path):
    """The non-dangling variant: following it would TRUNCATE the target with
    the notice body."""
    from tortoise.capture_consent import record_capture_declined
    victim = tmp_path / "existing"
    victim.write_text("do not touch\n")
    notice = capture_notice_path(tmp_path)
    notice.parent.mkdir(parents=True, exist_ok=True)
    notice.symlink_to(victim)

    assert record_capture_declined(tmp_path) is False
    assert victim.read_text() == "do not touch\n", "the target was truncated"


def test_the_shown_stamp_does_not_follow_a_symlink(tmp_path):
    """The stamp is rewritten by design, so it cannot use O_EXCL — but O_NOFOLLOW
    still applies: a symlink at the stamp is not a stamp."""
    from tortoise.capture_consent import mark_capture_notice_shown
    victim = tmp_path / "victim"
    stamp = capture_notice_shown_path(tmp_path)
    stamp.parent.mkdir(parents=True, exist_ok=True)
    stamp.symlink_to(victim)

    mark_capture_notice_shown(tmp_path)
    assert not victim.exists(), "the stamp was written THROUGH the symlink"
    assert stamp.is_symlink()


def test_the_notice_is_written_at_mode_0600(tmp_path):
    """It names the capture policy, so it is not world-readable — the old
    `write_text` path left it at the umask default (0o644)."""
    from tortoise.capture_consent import record_capture_declined
    assert record_capture_declined(tmp_path) is True
    mode = stat.S_IMODE(capture_notice_path(tmp_path).stat().st_mode)
    assert mode == 0o600, f"expected 0o600, got {oct(mode)}"


def test_the_notice_mode_is_pinned_against_a_restrictive_umask(tmp_path):
    """0o600 must be PINNED, not left at `0o600 & ~umask`. Under `umask 0o400`
    the mode lands at 0o200 — which the owner cannot read — so the notice would
    be written but never deliverable."""
    from tortoise.capture_consent import record_capture_declined
    previous = os.umask(0o400)
    try:
        assert record_capture_declined(tmp_path) is True
    finally:
        os.umask(previous)
    mode = stat.S_IMODE(capture_notice_path(tmp_path).stat().st_mode)
    assert mode == 0o600, f"expected a pinned 0o600, got {oct(mode)}"


def test_a_symlink_at_the_notice_is_not_read(tmp_path):
    """The READ half of #3684: `read_text` follows links, so a symlink at the
    notice made `pending_capture_notice` return an ARBITRARY readable file's
    content — which a terminal stderr then printed."""
    from tortoise.capture_consent import record_capture_declined
    secret = tmp_path / "secret"
    secret.write_text("SECRET-CONTENT\n", encoding="utf-8")
    notice = capture_notice_path(tmp_path)
    notice.parent.mkdir(parents=True, exist_ok=True)
    notice.symlink_to(secret)

    assert pending_capture_notice(tmp_path) is None, "read THROUGH the symlink"
    assert record_capture_declined(tmp_path) is False


def test_a_symlink_at_the_stamp_does_not_suppress_the_notice(tmp_path):
    """`exists()` follows links, so a link at `….shown` pointing at ANY readable
    file read as "already shown" and the migration notice was silently never
    delivered."""
    from tortoise.capture_consent import record_capture_declined
    assert record_capture_declined(tmp_path) is True
    target = tmp_path / "unrelated-existing-file"
    target.write_text("x\n", encoding="utf-8")
    capture_notice_shown_path(tmp_path).symlink_to(target)

    assert pending_capture_notice(tmp_path) is not None, "the notice was suppressed"


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="mkfifo is not POSIX")
def test_a_fifo_at_the_notice_path_is_refused(tmp_path):
    """`write_text` would have blocked on it. On the parent the STAMP write does
    exactly that — it opens the fifo `O_WRONLY` and waits forever for a reader,
    which is why the fix opens `O_NONBLOCK` and refuses anything that is not a
    regular file. The stamp half therefore runs under an alarm, so a regression
    FAILS here instead of wedging the suite (observed: the parent hung until a
    30-minute tool timeout killed it)."""
    from tortoise.capture_consent import mark_capture_notice_shown, record_capture_declined
    notice = capture_notice_path(tmp_path)
    notice.parent.mkdir(parents=True, exist_ok=True)
    os.mkfifo(notice)

    assert record_capture_declined(tmp_path) is False
    assert stat.S_ISFIFO(notice.lstat().st_mode), "the fifo is left alone"

    stamp = capture_notice_shown_path(tmp_path)
    os.mkfifo(stamp)

    def _stuck(signum, frame):
        raise AssertionError("the stamp write blocked on the fifo (no O_NONBLOCK)")

    # #7655: write the alarm through `harness_safe_sigalrm`, not a bare
    # `signal.alarm` — pytest-timeout's `signal` method shares `ITIMER_REAL`
    # with the code under test, so `signal.alarm(0)` in a `finally` silently
    # disarms the per-test guard for the rest of the test. The helper saves and
    # restores the harness handler AND timer.
    with harness_safe_sigalrm(10, _stuck):
        mark_capture_notice_shown(tmp_path)
    assert stat.S_ISFIFO(stamp.lstat().st_mode)


def test_the_first_write_is_atomic_under_concurrency(tmp_path):
    """The reason for `O_CREAT|O_EXCL` over the old `exists()` probe: racing
    writers must produce exactly ONE winner. A check-then-write probe has a
    window in which both callers see "not yet written" and both write."""
    from tortoise.capture_consent import record_capture_declined
    racers = 32
    barrier = threading.Barrier(racers)
    results: list[bool] = []
    guard = threading.Lock()

    def attempt() -> None:
        barrier.wait()
        got = record_capture_declined(tmp_path)
        with guard:
            results.append(got)

    threads = [threading.Thread(target=attempt) for _ in range(racers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert results.count(True) == 1, (
        f"exactly one writer must win the first write, got {results.count(True)}")


def test_a_hardlink_at_the_stamp_is_refused(tmp_path):
    """A hardlink planted at the stamp is a REGULAR file, and `O_NOFOLLOW` covers
    only symlinks — so an `O_TRUNC` open would truncate data OUTSIDE this
    directory. The `st_nlink == 1` gate is the same refusal `embedded_reaper.py`
    makes for its own marker file; the notice path needs no gate, because
    `O_EXCL` already refuses any pre-existing name."""
    from tortoise.capture_consent import mark_capture_notice_shown, record_capture_declined
    assert record_capture_declined(tmp_path) is True
    victim = tmp_path / "important-user-data"
    victim.write_text("IMPORTANT USER DATA\n", encoding="utf-8")
    stamp = capture_notice_shown_path(tmp_path)
    os.link(victim, stamp)

    mark_capture_notice_shown(tmp_path)

    assert victim.read_text(encoding="utf-8") == "IMPORTANT USER DATA\n", \
        "the hardlinked target was truncated"
    assert stamp.stat().st_nlink == 2, "the link is left alone"


def test_the_refusal_still_writes_nothing_readable_by_a_reader(tmp_path):
    """Regression guard for the fix's blast radius: an ordinary (non-symlink)
    first write still lands, is still delivered once, and a refused write leaves
    `pending_capture_notice` with nothing to say."""
    from tortoise.capture_consent import record_capture_declined
    assert pending_capture_notice(tmp_path) is None, "nothing written yet"
    assert record_capture_declined(tmp_path) is True
    assert pending_capture_notice(tmp_path) is not None, "the notice is delivered"
    assert record_capture_declined(tmp_path) is False, "still one-time"


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


# ── #3662: the SDK client TRANSMISSION path is consent-gated ──────────────
# The defect this pins: #3615's confirmed problem statement said "no in-repo
# path requires an explicit non-credential opt-in", but `TortoiseSDK`'s
# `_post_commit` (POST `/v1/sessions/commit`) shipped session-derived content
# with no consent check — the one in-repo, CLIENT-side transmission primitive
# the predicate did not reach. The hosted MCP tool
# (`mcp_server.tortoise_session_capture`) is deliberately NOT gated here: it
# executes server-side, so the client host's `TORTOISE_CAPTURE` is unreadable
# there (its gate is the server policy `session_recording`, #1927). See the
# consumer inventory in `tortoise/capture_consent.py`.


def test_capture_declined_reason_is_none_when_opted_in(monkeypatch):
    """The decline side of the predicate is inert once the host opts in."""
    monkeypatch.setenv(CAPTURE_OPT_IN_ENV, "1")
    assert capture_declined_reason() is None


def test_capture_declined_reason_records_the_durable_notice(tmp_path):
    """One call decides the refusal AND records the migration notice.

    Single-sourcing the decline is what stops a new transmitting surface from
    refusing without writing the `~/.tortoise/capture-consent-notice` channel
    the migration depends on (`_isolated` pins HOME to `tmp_path`)."""
    reason = capture_declined_reason()
    assert reason == CAPTURE_DECLINED_HINT
    assert capture_notice_path(tmp_path).exists(), (
        "the refusal must record the durable migration notice")


def test_sdk_post_commit_refuses_without_consent_and_never_posts(monkeypatch):
    """#3662: the client transmission primitive fails closed.

    FAIL-ON (before the fix): `_post_commit` POSTs the derived payload with no
    consent consulted — the test's `requests.post` recorder is hit.
    REACHABLE: the consent variable is genuinely UNSET (`_isolated` deletes it,
    ordered after the suite-wide grant in tests/conftest.py)."""
    import requests

    from tortoise.sdk import _post_commit

    posted: list = []
    monkeypatch.setattr(requests, "post", lambda *a, **k: posted.append(a))
    payload = {"session_id": "s1", "points": [], "entities": [],
               "operators": [], "summary": "", "story_arc": ""}
    with pytest.raises(PermissionError) as excinfo:
        _post_commit(payload, base_url="http://unused", api_key="tt_k")
    assert CAPTURE_OPT_IN_ENV in str(excinfo.value), excinfo.value
    assert posted == [], "an unconsented capture must not POST"


def test_sdk_post_commit_transmits_when_consented(monkeypatch):
    """Control for the gate: the opt-in takes the authorised branch.

    Without this, a gate that always refuses would pass the test above."""
    import requests

    from tortoise.sdk import _post_commit

    calls: list = []

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"duplicate": False}

    monkeypatch.setenv(CAPTURE_OPT_IN_ENV, "1")
    monkeypatch.setattr(requests, "post",
                        lambda url, **k: calls.append(url) or _Resp())
    payload = {"session_id": "s1", "points": [], "entities": [],
               "operators": [], "summary": "", "story_arc": ""}
    out = _post_commit(payload, base_url="http://unused.example", api_key="tt_k")
    assert out == {"duplicate": False}
    assert calls == ["http://unused.example/v1/sessions/commit"], calls


def test_quickstart_documents_both_consent_contracts():
    """#3662's minimum requirement: document the SDK gate next to #3615's
    opt-in AND name the surface that is deliberately NOT client-gated, so the
    two contracts cannot drift apart in the doc a user actually reads.

    The refusal hint points readers at this file by name
    (`CAPTURE_DECLINED_HINT`), so it is the surface the two claims must agree
    on."""
    doc = (REPO_ROOT / "docs" / "quickstart-cloud.md").read_text(encoding="utf-8")
    assert "TortoiseSDK.commit_session" in doc, (
        "the quickstart must name the SDK's consent-gated transmission path")
    assert "tortoise_session_capture" in doc, (
        "the quickstart must name the server-side MCP tool whose gate is the "
        "server policy, not the client opt-in")
    assert "#3662" in doc, "the deferred hosted-side decision must be traceable"
