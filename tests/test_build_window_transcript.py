"""Tests for tools/build_window_transcript.py (epic #909 gate, #946).

Locks the code-review round-1 fixes: secret-pattern hard-fail (SEC-1) and the
duplicate-session_id guard (bug-scan P2). Zero network — synthetic fixtures.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
from build_window_transcript import _SECRET_PATTERNS, _check_secrets, load_session, sanitize  # noqa: E402, I001, RUF100


def _write_events(tmp_path: Path, records: list[dict]) -> Path:
    p = tmp_path / "events.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    return p


def _record(session_id: str, turns: int = 2) -> dict:
    return {
        "session_id": session_id,
        "conversation": [{"role": "user", "content": f"u{i}"} for i in range(turns)],
        "metadata": {},
    }


# ── load_session: duplicate guard (bug-scan P2) ─────────────────────────────

def test_load_session_duplicate_hard_fails(tmp_path):
    path = _write_events(
        tmp_path,
        [
            _record("s-dup"),
            {"session_id": "other", "conversation": [], "metadata": {}},
            _record("s-dup"),
        ],
    )
    with pytest.raises(SystemExit, match="found 2 times .*lines 1, 3.*ambiguous"):  # noqa: RUF043
        load_session(path, "s-dup")


def test_load_session_not_found(tmp_path):
    path = _write_events(tmp_path, [_record("a")])
    with pytest.raises(SystemExit, match="not found"):
        load_session(path, "missing")


def test_load_session_single_match(tmp_path):
    path = _write_events(tmp_path, [_record("target", turns=3)])
    rec = load_session(path, "target")
    assert len(rec["conversation"]) == 3


def test_load_session_prefix_not_confused(tmp_path):
    # A partial-prefix lookalike must not match an exact session_id.
    path = _write_events(tmp_path, [_record("019ff63b-1036-7da7"), _record("019ff63b-9eef-7f89")])
    rec = load_session(path, "019ff63b-9eef-7f89")
    assert rec["session_id"] == "019ff63b-9eef-7f89"


# ── _check_secrets: secret guard (SEC-1) ────────────────────────────────────

def test_secret_guard_hard_fails_on_keys():
    # Fixtures are built via concatenation so no literal secret-shaped token exists
    # in the source (GitHub push protection flags even test fixtures). The runtime
    # strings still exercise the _SECRET_PATTERNS regexes.
    sk = "sk-" + "proj-" + "A" * 40
    stripe = "sk_live_" + "1" * 24
    fine = "github_pat_" + "A" * 40
    classic = "ghp_" + "A" * 24
    aws = "AKIA" + "I" * 16
    jwt = "Bearer " + "e" * 24 + "." + "y" * 24 + "." + "z" * 24
    rsa = "-----BEGIN RSA PRIVATE KEY-----"
    for sample in (
        "using " + sk + "ABCDEFGHIJ",
        "stripe secret " + stripe + "abcdef",
        "github " + fine + "0123456",
        "token " + classic + "1234",
        "aws " + aws + "EXAMPLE",
        "auth: " + jwt + "_j9J",
        rsa,
    ):
        with pytest.raises(SystemExit, match="possible secret pattern"):
            _check_secrets(sample, 0)


def test_secret_guard_no_false_positive_on_prose():
    for sample in (
        "sk-workflow-standard is a skill identifier",
        "the bearer of bad news arrived",
        "AKIA is an acronym in the docs",
        "let me check the git commit sha 8960db77afb139f95c3e5f89",
        "pushed the release to prod",
    ):
        _check_secrets(sample, 0)  # must not raise


# ── sanitize ────────────────────────────────────────────────────────────────

def test_sanitize_collapses_newlines():
    assert sanitize("a\nb\t  c\n\nd") == "a b c d"


def test_sanitize_strips():
    assert sanitize("  padded  ") == "padded"


# ── patterns are all compiled (import sanity) ───────────────────────────────

def test_secret_patterns_compiled():
    assert len(_SECRET_PATTERNS) >= 8
    for pat, _ in _SECRET_PATTERNS:
        assert pat.pattern
