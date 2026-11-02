"""#6388 (part 2): a seam that DIVERGES from the shipped artifact must be
reported by a READ-side surface, not only by the next `tortoise install <harness>`.

The state under test is the one measured on the reference box and recorded in
``docs/architecture/PI-CAPTURE-ARCHITECTURE.md`` §7: the installed seam is a
snapshot taken by an OLDER CLI (there: generation 14, 149,736 B, against a
shipped generation 15, 152,752 B), and **no install ran on that machine since**.
Install-time verification cannot observe that state by construction — it grades
a write THIS process just performed — so before this file the divergence was
reported only by an EXPLICIT read-side check (`hooks status`, `doctor`) and
never at session start.

Mutation (the fix removed), measured: returning from
``__main__._print_seam_divergences`` before its divergences loop reds the THREE
divergence-reporting surface tests; dropping its THREE call sites (two in ``_print_capture_health`` — the
``except`` branch and the normal one — and one in ``_cmd_session_health``) ALSO reds
``test_a_failed_seam_inspection_is_NAMED_and_never_blocks`` (the 'seam not
inspected' sentence). The predicate-level tests exercise
``startup_seam_report`` directly and stay green either way, which is why both
layers are pinned.  Mutation (a second predicate instead of the one instrument): stop
delegating to ``capture_install.assert_seam_target`` in
``capture_install.startup_seam_report`` — ``test_the_startup_report_asks_the_one_instrument``
goes RED.
"""
from __future__ import annotations

import contextlib
import hashlib
import io

import pytest

import tortoise.__main__ as cli
from tortoise import capture_install


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A hermetic ``~`` (the harness's own resolver reads ``$HOME``)."""
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("TORTOISE_CAPTURE_SPOOL_DIR", str(tmp_path / "spool"))
    # The registry is iterated, so EVERY contract root must be hermetic, not just
    # `$HOME`: OpenClaw's resolves through its own override (#6388 review).
    monkeypatch.delenv("OPENCLAW_STATE_DIR", raising=False)
    return h


def _installed(home):
    return home / ".pi" / "agent" / "extensions" / "tortoise-capture.ts"


def _install(home):
    result = capture_install.install_capture("pi", home=home)
    assert result.error == "", result.error
    assert _installed(home).is_file()
    return _installed(home)


def _make_divergent(home):
    """The measured state: an OLDER copy on disk (installed by an older CLI)."""
    path = _install(home)
    path.write_text(path.read_text() + "\n// generation 14 — older CLI\n")
    assert capture_install.assert_seam_target("pi", home=home) != ""
    return path


def _run(fn, *args, **kwargs):
    """Run a read-side surface, returning ``(rc, stdout, stderr)``."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = fn(*args, **kwargs)
    return rc, out.getvalue(), err.getvalue()


# ── the predicate: the read side of the ONE instrument ────────────────────


def test_a_clean_install_reports_nothing_at_startup(home):
    """A current seam must stay SILENT — a line on every session for a
    healthy box is the alert-fatigue pattern this module already records."""
    _install(home)
    assert capture_install.startup_seam_report() == []


def test_a_divergent_seam_is_reported_at_startup(home):
    """Both paths and BOTH digests, so the operator can tell which way it
    differs without opening either file."""
    path = _make_divergent(home)
    sentences = capture_install.startup_seam_report()
    assert len(sentences) == 1, sentences
    sentence = sentences[0]
    assert str(path) in sentence
    shipped = capture_install.hook_install.ARTIFACT_CONTRACTS["pi"].source
    assert str(shipped) in sentence
    for digest_source in (path, shipped):
        assert hashlib.sha256(digest_source.read_bytes()).hexdigest() in sentence
    # the remedy the installer actually accepts is named, never re-derived
    assert "tortoise install pi" in sentence


def test_the_startup_report_asks_the_one_instrument(home, monkeypatch):
    """#1178: no second predicate beside the existing one — the read side
    calls ``assert_seam_target`` and cannot drift from the install half."""
    seen = []

    def _fake(harness, *, home=None, require_present=False):
        seen.append((harness, require_present))
        return f"sentinel divergence for {harness}"

    monkeypatch.setattr(capture_install, "assert_seam_target", _fake)
    sentences = capture_install.startup_seam_report()
    assert "sentinel divergence for pi" in sentences
    # the READ side: an absent seam stays `missing-artifact`'s answer
    assert ("pi", False) in seen


# ── the read-side surfaces themselves ──────────────────────────────────────


def test_session_health_reports_the_divergence_and_never_blocks(home):
    """The one operator line a session start can print must not go silent on
    the box that captures with the wrong bytes — and must not become a gate."""
    _make_divergent(home)
    rc, out, err = _run(cli._cmd_session_health)
    assert rc == 0, "a stale seam is REPORTED, never a failing startup"
    assert "capture: the file pi LOADS is not the file this repo SHIPS" in err
    assert "tortoise-capture.ts" in err
    assert "tortoise-capture.ts" not in out, (
        "the ONE pinned health line on stdout must stay byte-identical")


def test_the_drain_health_line_reports_the_divergence(home):
    """The SessionStart drain's line is the second read-side surface; it used to
    filter on SPOOL state alone, so a stale seam on an otherwise healthy box
    printed nothing at all."""
    _make_divergent(home)
    _, _, err = _run(cli._print_capture_health)
    assert "tortoise-capture.ts" in err


def test_a_clean_box_startup_surfaces_stay_quiet(home):
    """Pins the other side: the new sentences appear only for a divergence, so
    the health surfaces do not gain a line on every healthy session."""
    _install(home)
    _, out, err = _run(cli._cmd_session_health)
    assert "tortoise-capture.ts" not in out + err
    _, _, err2 = _run(cli._print_capture_health)
    assert "tortoise-capture.ts" not in err2


def test_an_unreadable_spool_cannot_mask_the_divergence(home, monkeypatch):
    """The seam is a DIFFERENT substrate from the spool: a health read that could
    not look must not also swallow the seam report (cycle-1 review), and the two
    surfaces must not disagree on that path."""
    def _boom(*a, **k):
        raise OSError("spool unreadable")

    _make_divergent(home)
    monkeypatch.setattr("tortoise.capture_spool.capture_health_line", _boom)
    _, out, err = _run(cli._print_capture_health)
    assert out == "", "a failed health read must not print a health line"
    assert "capture: the file pi LOADS is not the file this repo SHIPS" in err
    # …and the explicit surface agrees on the same state.
    _, _, err2 = _run(cli._cmd_session_health)
    assert "capture: the file pi LOADS is not the file this repo SHIPS" in err2


def test_a_failed_seam_inspection_is_NAMED_and_never_blocks(home, monkeypatch):
    """A seam inspection that could not run is a sentence, never a failed session
    start — the exit-0 contract the drain and `session health` both promise."""
    def _boom(**k):
        raise OSError("registry unreadable")

    monkeypatch.setattr(capture_install, "startup_seam_report", _boom)
    rc, out, err = _run(cli._cmd_session_health)
    assert rc == 0, "a failed inspection must not fail the command"
    assert "capture: seam not inspected" in err and "registry unreadable" in err
    assert out.startswith("capture: "), "stdout stays the ONE pinned health line"


def test_the_explicit_seams_are_askable(tmp_path):
    """`harnesses=`/`home=` are the seams a caller (or a test) pins the question
    with; exercised here so the exported surface is not dead code (#1178)."""
    other_home = tmp_path / "other-home"
    other_home.mkdir()
    assert capture_install.install_capture("pi", home=other_home).error == ""
    path = other_home / ".pi" / "agent" / "extensions" / "tortoise-capture.ts"
    path.write_text(path.read_text() + "\n// older CLI\n")
    sentences = capture_install.startup_seam_report(home=other_home,
                                                   harnesses=("pi",))
    assert len(sentences) == 1 and str(path) in sentences[0]
