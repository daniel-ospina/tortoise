"""#5109 — the CLI must never print a literal API key to stdout.

WHY THIS GATE EXISTS (the mechanism, measured 2026-10-11)
=========================================================
``tortoise init`` prints per-harness MCP config. In this fleet that command is
run *by an agent*, so its stdout is a tool result — and a tool result is
persisted **verbatim** into the session transcript, the task-session log and
``audit.jsonl``. That is the channel by which the live key reached **326 files
under ``~/.pi``** (322 of them session records going back to 2026-09-05).

The functions below already *claim* the correct behaviour in their own prose —
"env expansion … no literal key on disk" — while emitting the literal value on
the adjacent line. The leak is not a missing feature; it is a printed string.

WHAT THE GATE IS
----------------
Print the *resolution*, never the *value*: an instruction that reads the key
from its 0600 config at use time. A user pasting it still gets a working
``$TORTOISE_API_KEY``; a process that merely captures stdout gets no credential.

The failure this prevents has already happened (326 files). Deleting this test
would harm the PRODUCT, not the process: it is the only check standing between a
one-line ``print(...)`` and another 300 leaked copies.
"""

from __future__ import annotations

import contextlib
import io
import json
import re
from pathlib import Path

import pytest

from tortoise.__main__ import _print_mcp_configs, _write_mcp_config_file

# uuid4().hex is 32 lowercase hex, so every minted key matches `tt_[0-9a-f]{32}`.
# A SENTINEL is used so this test can never itself leak a real credential.
KEY = "tt_" + "0123456789abcdef" * 2
URL = "https://api.premiselabs.co"
KEY_SHAPE = re.compile(r"tt_[0-9a-f]{32}")

HARNESSES = ("codex", "claude", "cursor", "pi")


def _capture(fn, *args, **kwargs) -> str:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        fn(*args, **kwargs)
    return buf.getvalue()


@pytest.mark.parametrize("harness", HARNESSES)
def test_print_mcp_configs_emits_no_literal_key(harness: str) -> None:
    """The onboarding print path must not echo the credential."""
    out = _capture(_print_mcp_configs, KEY, URL, harness)
    leaked = KEY_SHAPE.findall(out)
    assert not leaked, (
        f"_print_mcp_configs({harness!r}) printed the literal API key "
        f"{len(leaked)}x — stdout is captured verbatim into agent session "
        f"transcripts (#5109). Emit a use-time resolution instead."
    )
    assert KEY not in out, "the exact key value reached stdout"


@pytest.mark.parametrize("harness", HARNESSES)
def test_write_mcp_config_file_emits_no_literal_key(
    harness: str, tmp_path, monkeypatch
) -> None:
    """The --write-mcp-config path prints the same export line — same rule."""
    monkeypatch.chdir(tmp_path)
    out = _capture(_write_mcp_config_file, KEY, URL, harness, True)
    leaked = KEY_SHAPE.findall(out)
    assert not leaked, (
        f"_write_mcp_config_file({harness!r}) printed the literal API key "
        f"{len(leaked)}x — the same stdout channel as above (#5109)."
    )


def test_the_written_mcp_file_itself_stays_key_free(tmp_path, monkeypatch) -> None:
    """The file was never the leak — assert the gate did not regress that."""
    monkeypatch.chdir(tmp_path)
    _capture(_write_mcp_config_file, KEY, URL, "pi", True)
    raw = (tmp_path / ".mcp.json").read_text()
    assert KEY not in raw
    assert (
        json.loads(raw)["mcpServers"]["tortoise"]["headers"]["Authorization"]
        == "Bearer ${TORTOISE_API_KEY}"
    )


def test_the_real_resolution_constant_actually_resolves(tmp_path) -> None:
    """Guard the CONSTANT, not a lookalike.

    A previous version hand-built the expression, so it passed even when the real
    constant was emptied or renamed to the wrong JSON field — i.e. it guarded
    nothing. Import the production function and drive it through a real shell.
    """
    import subprocess

    from tortoise.__main__ import _key_resolution_expr

    cfg = tmp_path / "credentials.json"
    cfg.write_text(json.dumps({"api_key": KEY, "api_url": URL}))
    expr = _key_resolution_expr(cfg)
    assert KEY not in expr, "the expression itself must never contain the value"
    got = subprocess.run(
        ["/bin/sh", "-c", f'export TORTOISE_API_KEY={expr}; printf %s "$TORTOISE_API_KEY"'],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert got == KEY, (
        "the REAL _key_resolution_expr must resolve the key from the file the CLI "
        "wrote — an emptied constant or a wrong field name (apiKey vs api_key) "
        "silently exports nothing"
    )


def test_resolution_expr_points_at_the_path_it_was_given(tmp_path) -> None:
    """The path must be threaded, not hard-coded: `init --api-key` saves to
    `<cwd>/.tortoise`, so a hard-coded `~/.tortoise/credentials.json` would
    export a stale or absent key (review finding, #5109)."""
    from tortoise.__main__ import _key_resolution_expr

    a = tmp_path / "a" / "credentials.json"
    b = tmp_path / "b" / "credentials.json"
    assert str(a) in _key_resolution_expr(a)
    assert str(b) in _key_resolution_expr(b)
    assert _key_resolution_expr(a) != _key_resolution_expr(b)


#: Interpolations that put a CREDENTIAL VALUE on a print line. The #5109 rule
#: is "never interpolate the value" — the resolution EXPRESSION is fine — so
#: this deliberately does not match `_key_resolution_expr(...)`.
_LIVE_VALUE_PATTERNS = (
    "data['key']",
    "data.get('key')",
    "key['api_key']",
    "{new_token}",
    "{orphan_token}",
)


def _live_key_print_sites(src: str) -> list[tuple[int, str]]:
    """1-based line numbers of `print(...)` lines carrying a credential value."""
    sites: list[tuple[int, str]] = []
    for lineno, line in enumerate(src.splitlines(), start=1):
        if "print(" not in line:
            continue
        if any(pat in line for pat in _LIVE_VALUE_PATTERNS):
            sites.append((lineno, line.strip()))
    return sites


def test_every_live_key_print_carries_a_5109_note() -> None:
    """The invariant is not "no key is ever printed" — it is "every remaining
    value echo is a DELIBERATE, documented, owner-routed exception".

    The success paths carry the note; the ORPHAN echoes (mint/recover OK, save
    failed — the key exists nowhere else) were missing it, so the PR's
    deliberate/owner-routed coverage claim was false. Pin it: every live-key
    print must have a `#5109 NOTE` within the 5 lines above it. A new
    `print(f"... {data['key']}")` with no note reds this gate.
    """
    src = (
        Path(__file__).resolve().parent.parent / "tortoise" / "__main__.py"
    ).read_text(encoding="utf-8")
    lines = src.splitlines()
    missing = []
    for lineno, text in _live_key_print_sites(src):
        window = "\n".join(lines[max(0, lineno - 6):lineno - 1])
        if "#5109 NOTE" not in window:
            missing.append((lineno, text))
    assert not missing, (
        "every remaining live-key print must carry a `#5109 NOTE` (deliberate, "
        f"owner-routed) — these do not: {missing}"
    )


def test_signup_orphan_echo_is_stderr_only_never_stdout(
    monkeypatch, tmp_path
) -> None:
    """#5109 P2: on `_cmd_signup`'s orphan path (key minted, save failed) the
    key genuinely exists nowhere else, so the echo is DELIBERATE — but it must
    stay on STDERR. stdout is the verbatim-captured channel; a regression that
    moves (or duplicates) this echo onto stdout reds this gate."""
    import contextlib
    import io
    from unittest import mock

    import tortoise.__main__ as main

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)

    orphan_key = "tt_" + "ab" * 16  # matches KEY_SHAPE (32 lowercase hex)
    resp = mock.MagicMock()
    resp.read.return_value = json.dumps({
        "key": orphan_key,
        "org_id": "team-orphan",
        "org_name": "agent-orphan",
        "signup_token": "st_" + "cd" * 32,
    }).encode()
    resp.__enter__.return_value = resp

    out, err = io.StringIO(), io.StringIO()
    with mock.patch("urllib.request.urlopen", return_value=resp), \
         mock.patch("os.replace", side_effect=OSError("read-only fs")), \
         contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = main._cmd_signup(mock.Mock())

    assert rc == 1, "the orphan path must fail closed (never exit 0)"
    assert orphan_key in err.getvalue(), (
        "the orphan path must still echo the key — it exists nowhere else"
    )
    assert orphan_key not in out.getvalue(), (
        "the orphan key echo must never reach stdout (verbatim-captured, #5109)"
    )


def test_json_payload_uses_the_env_form_not_the_literal() -> None:
    """The `--json` payload is the AGENT-consumed path (#304).

    Asserted at the SOURCE, because that is where the regression lands: the
    payload is built inline in `_main`'s init flow, so the honest guard is that
    no `auth_header` is ever interpolated from a key variable again.
    """
    src = (
        Path(__file__).resolve().parent.parent / "tortoise" / "__main__.py"
    ).read_text(encoding="utf-8")
    offenders = [
        ln for ln in src.splitlines()
        if "auth_header" in ln and "api_key" in ln
    ]
    assert not offenders, (
        "the --json payload interpolates a live key — the agent-consumed path "
        f"must emit ${{TORTOISE_API_KEY}}: {offenders}"
    )
    assert '"auth_header": "Bearer ${TORTOISE_API_KEY}"' in src, (
        "the --json payload must name the env var explicitly"
    )
