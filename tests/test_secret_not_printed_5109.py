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


def test_resolution_form_survives_a_shell_roundtrip(tmp_path) -> None:
    """The migration hint must still SET a usable key — not just stop leaking.

    Guards the obvious wrong fix: deleting the line entirely (which would leave
    the user with no way to populate ``$TORTOISE_API_KEY`` and would silently
    break every onboarding that relied on it).
    """
    import subprocess

    cfg = tmp_path / "tortoise-config.json"
    cfg.write_text(json.dumps({"apiKey": KEY, "apiUrl": URL}))
    expr = (
        "python3 -c 'import json,pathlib;"
        "print(json.loads(pathlib.Path(\"" + str(cfg) + "\").read_text())[\"apiKey\"])'"
    )
    got = subprocess.run(
        ["/bin/sh", "-c", f'export TORTOISE_API_KEY=$({expr}); printf %s "$TORTOISE_API_KEY"'],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert got == KEY, "the printed resolution expression must actually resolve the key"
