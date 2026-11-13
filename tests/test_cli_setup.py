"""`tortoise setup` must never die on a non-TTY stdin (#12404).

`setup` is the step BOTH `tortoise init` and `tortoise onboard` advertise as the
next action, and the product's onboarding is agent-driven (epic #1976) — so the
*normal* caller is a shell with no TTY stdin (an agent's bash tool, CI,
`docker run` without `-t`, a pipe). On `origin/main` that caller got a raw
traceback:

    File ".../tortoise/__main__.py", line 12271, in _cmd_setup
        choice = input("\n> ").strip()
    EOFError: EOF when reading a line
    (exit 1)

⚠️ The fix is NOT a blanket `isatty` refusal, and these tests say so: a pipe
that SUPPLIES every answer is a legitimate non-TTY caller that used to work
(`printf '6\\ndeveloper\\neng\\n…' | tortoise setup` → wrote the config), and
`--role` cannot replace it — `--role` emits the canned default memory_filter,
not scripted custom answers. So the boundary is closed only where the input
ACTUALLY ends, by the `except EOFError` backstop at the dispatch site. The
capability test below (`test_setup_accepts_answers_piped_on_a_non_tty_stdin`)
is the pin that a future "just check isatty" simplification would break.
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import tortoise.__main__ as main

# The interactive prompt sequence, shortest complete route: skip harness, name
# role + team, decline the three optional blocks, accept the save. One answer
# per `input()`, so a pipe of these runs the whole flow to completion.
ANSWERS = "6\ndeveloper\neng\nn\nn\ny\nn\nn\nn\ny\n"


class _PipeStdin(io.StringIO):
    """A pipe: not a TTY, EOF once its buffered answers are consumed."""

    def isatty(self) -> bool:
        return False


class _TtyStdin(io.StringIO):
    """A TTY-shaped stdin carrying scripted answers."""

    def isatty(self) -> bool:
        return True


def _run_setup(monkeypatch, stdin, capsys, argv: list[str] | None = None):
    monkeypatch.setattr(sys, "stdin", stdin)
    rc = main.main(argv or ["setup"])
    captured = capsys.readouterr()
    return rc, captured.out, captured.err


def test_setup_at_eof_on_a_non_tty_refuses_cleanly_instead_of_raising(monkeypatch, capsys):
    """The #12404 regression pin: clean exit code, no traceback, no EOFError."""
    rc, out, err = _run_setup(monkeypatch, _PipeStdin(""), capsys)

    assert "Traceback" not in out + err, (
        "a non-TTY stdin at EOF must not produce a traceback; got:\n" + out + err
    )
    assert "EOFError" not in out + err, (
        "the EOF must be handled, not surfaced as an exception name; got:\n" + out + err
    )
    assert rc == 2, (
        "a missing EFFECTIVELY-REQUIRED input is a usage error — argparse's own "
        f"convention across this CLI — expected 2, got {rc}"
    )


def test_setup_refusal_names_the_non_interactive_flag(monkeypatch, capsys):
    """A refusal a user cannot act on is only a quieter trapdoor.

    The remedy must name the flag that DOES work, because the whole defect is
    that `--role` is undiscoverable from a traceback.
    """
    _rc, _out, err = _run_setup(monkeypatch, _PipeStdin(""), capsys)

    assert "--role" in err, (
        f"the refusal must name `--role` (the non-interactive mode); got:\n{err}"
    )


def test_setup_accepts_answers_piped_on_a_non_tty_stdin(monkeypatch, capsys, tmp_path):
    """A pipe that supplies every answer must still complete and save.

    This is the capability a blanket `isatty` gate would remove (first review of
    #12404 caught exactly that): it worked before the fix, so it must work
    after it. `--role` is not a substitute — it emits the canned defaults, not
    scripted answers.
    """
    monkeypatch.chdir(tmp_path)
    rc, out, err = _run_setup(monkeypatch, _PipeStdin(ANSWERS), capsys)

    assert rc == 0, f"a fully-answered pipe must complete; got rc={rc}\n{out}\n{err}"
    saved = Path("tortoise-setup.yaml")
    assert saved.exists(), f"the config was not written; stdout:\n{out}\nstderr:\n{err}"
    text = saved.read_text()
    assert "role: developer" in text and "team: eng" in text, text


def test_setup_on_a_tty_still_reaches_the_interactive_flow(monkeypatch, capsys):
    """The backstop must not swallow the interactive path it was added beside.

    `6` is the documented "Skip — just configure memory" answer; reaching the
    role question proves the first prompt ran.
    """
    _rc, out, err = _run_setup(monkeypatch, _TtyStdin("6\ndeveloper\neng\n"), capsys)

    assert "Configure what each role remembers" in out, (
        "the interactive flow must still run on a TTY; got:\n" + out + err
    )
    assert "Traceback" not in out + err, out + err


def test_setup_ends_cleanly_when_a_tty_users_input_runs_out(monkeypatch, capsys):
    """Ctrl-D on a real TTY raises the same EOFError — it must not traceback."""
    rc, out, err = _run_setup(monkeypatch, _TtyStdin("6\ndeveloper\neng\n"), capsys)

    assert "Traceback" not in out + err, out + err
    assert rc == 2, f"a Ctrl-D must end cleanly with exit 2, got {rc}"
    assert "--role" in err, err


def test_setup_role_mode_is_unchanged(monkeypatch, capsys):
    """`--role` is the remedy the refusal points at — it must keep working."""
    monkeypatch.setattr(sys, "stdin", _PipeStdin(""))
    rc = main.main(["setup", "--role", "developer", "--team", "eng"])
    captured = capsys.readouterr()

    assert rc == 0, captured.err
    assert "role: developer" in captured.out, captured.out
