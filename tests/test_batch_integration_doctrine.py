"""The batch-integration doctrine's commands must execute against THIS repo.

WHY THIS FILE EXISTS (#10399 review P1-1/P1-2/P1-4/P1-5, P2-1, P2-3)
-------------------------------------------------------------------
``docs/ops/batch-integration.md`` is a *doctrine*: an operator follows it
top-to-bottom and executes its command blocks. Two of those commands failed as
written, and two stated facts were false against the tree they describe:

* the suite blocks carried no ``TORTOISE_DB_URI`` and no marker filter, so the
  documented baseline dies at session start (``tests/conftest.py``'s
  ``_p4_uri_required`` fixture calls ``pytest.fail``) — and a bare
  ``pytest tests/`` would additionally run ``live`` / ``integration`` tests that
  no CI lane runs;
* every merge in a batch is a merge commit by construction, so the documented
  ``git revert <sha>`` exits 128 (``is a merge but no -m option was given``) and
  drops nothing;
* the invariant ("``main`` only ever receives a tree that has already passed the
  full suite") was unenforceable, because nothing re-checked that ``main`` was
  still the pinned base at landing time — the property bors and GitHub's queue
  get for free by owning the merge, and the live ``.mergify.yml`` queue keeps
  moving ``main`` while a batch runs;
* the carve-out file count was quoted from memory and was stale.

Prose is not pinned here — only the executable claims. A wording edit that keeps
the commands honest does not red this file; a command edit that would not run
does.

Non-vacuity: measured on the pre-fix head ``a17576c43`` — checks 1, 2, 3, 4 and 5
all red on it (``5 failed, 1 passed``; the one pass is the parser sanity check,
which must hold on both trees).

Static, no network / DB / graph.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "ops" / "batch-integration.md"

#: The filter every CI lane applies to the deterministic suite
#: (``.github/workflows/python-ci.yml``; ``pyproject.toml`` defines the markers).
MARKER_FILTER = "-m 'not track_b and not live and not integration'"

#: A full-suite invocation — ``pytest tests/`` optionally followed by flags.
#: ``pytest <some file>`` (the bisect/blame commands) deliberately does NOT match.
_FULL_SUITE = re.compile(r"\bpytest\s+tests/\s*(?:$|-)")

#: The landing-time base check: compare ``origin/main`` to the pinned sha.
_PINNED_MAIN_ASSERTION = re.compile(
    r'\[\s*"\$\(git\s+rev-parse\s+origin/main\)"\s*=\s*"\$PINNED"\s*\]'
)


def _text() -> str:
    assert DOC.is_file(), f"{DOC.relative_to(ROOT)} is missing — re-point this guard"
    return DOC.read_text(encoding="utf-8")


def _fenced_commands() -> list[str]:
    """Every non-blank, non-comment line inside a fenced code block."""
    commands: list[str] = []
    in_fence = False
    for line in _text().splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence and stripped and not stripped.startswith("#"):
            commands.append(stripped)
    return commands


def test_this_guard_parses_the_document() -> None:
    """A parser that finds nothing would make every check below vacuous."""
    commands = _fenced_commands()
    assert len(commands) >= 10, f"parsed only {len(commands)} fenced commands — re-point this guard"
    assert any(_FULL_SUITE.search(cmd) for cmd in commands)


def test_every_full_suite_command_carries_the_ci_marker_filter() -> None:
    """#10399 review P1-5: the documented suite is the DETERMINISTIC suite.

    Bare ``pytest tests/`` runs the ``live`` and ``integration`` markers, which
    need live credentials and production access; no CI lane runs them
    (``-m 'not track_b and not live and not integration'``). Every such failure
    the doc manufactures becomes a ``BASELINE(<ids>)`` id or convicts an
    innocent PR — the exact noise the Baseline rule exists to remove.
    """
    full_suite = [cmd for cmd in _fenced_commands() if _FULL_SUITE.search(cmd)]
    assert full_suite, "the doctrine no longer documents a full-suite run — re-point this guard"
    missing = [cmd for cmd in full_suite if MARKER_FILTER not in cmd]
    assert not missing, (
        "every full-suite command in the doctrine must carry the CI marker "
        f"{MARKER_FILTER!r}; these do not: {missing}"
    )


def test_the_lane_is_selected_and_exported_before_the_first_suite_run() -> None:
    """#10399 review P1-4: the suite is URI-required, so the lane comes first.

    ``tests/conftest.py::_p4_uri_required`` fails the session closed when
    ``TORTOISE_DB_URI`` is unset and the carve-out is not opted in, so a
    documented baseline that precedes the lane selection cannot run at all.
    """
    lines = _text().splitlines()
    uri_lines = [i for i, line in enumerate(lines) if "TORTOISE_DB_URI" in line]
    suite_lines = [i for i, line in enumerate(lines) if _FULL_SUITE.search(line)]
    assert uri_lines, "the doctrine no longer names TORTOISE_DB_URI — re-point this guard"
    assert suite_lines, "the doctrine no longer documents a full-suite run — re-point this guard"
    assert min(uri_lines) < min(suite_lines), (
        "the lane (TORTOISE_DB_URI) is first named at line "
        f"{min(uri_lines) + 1} but the first full-suite command is at line "
        f"{min(suite_lines) + 1}: the baseline would die at session start "
        "(tests/_embedded.py::_assert_p4_uri_required)"
    )


def test_reverting_a_batch_culprit_passes_a_mainline() -> None:
    """#10399 review P1-2: every batch merge is a merge commit by construction.

    ``git revert <merge sha>`` without ``-m`` exits 128 and drops nothing, so
    the one step the doctrine calls non-negotiable silently does not happen.
    """
    reverts = [cmd for cmd in _fenced_commands() if "git revert" in cmd]
    assert reverts, "the doctrine no longer documents dropping a culprit — re-point this guard"
    missing = [cmd for cmd in reverts if "-m 1" not in cmd]
    assert not missing, (
        "a batch merge is a merge commit, so its revert needs a mainline "
        f"(-m 1) or git exits 128; these do not: {missing}"
    )


def test_landing_asserts_main_is_still_the_pinned_base() -> None:
    """#10399 review P1-3: the tested tree must be the landed tree.

    bors fast-forwards ``main`` to the staging branch it tested and GitHub's
    queue validates against "the latest version of the target branch" — both get
    the property by OWNING the merge. Here ``.mergify.yml`` keeps merging to
    ``main`` while a batch runs, so the base must be re-checked mechanically at
    landing time, or the invariant is a sentence rather than a property.
    """
    fenced = "\n".join(_fenced_commands())
    assert _PINNED_MAIN_ASSERTION.search(fenced), (
        "the doctrine must carry the landing-time base assertion "
        '`[ "$(git rev-parse origin/main)" = "$PINNED" ]` as an executable '
        "command; without it a batch can land a tree no run has seen"
    )


def test_the_live_queue_and_the_carve_out_registry_are_named() -> None:
    """#10399 review P2-1 / P2-3: name the machinery instead of paraphrasing it.

    ``.mergify.yml`` is a LIVE merge queue in this repo (``merge_conditions:
    check-success=python-ci-gate``, ``batch_size: 4``) that already validates
    composition, so a doctrine claiming per-PR CI is the only gate
    mis-describes the repo it governs. And the carve-out population is a registry
    (``config/ci-surfaces.yml`` -> ``carve_out:``), so a quoted count of it goes
    stale on the next reconciliation — name the registry.
    """
    text = _text()
    for needle in (".mergify.yml", "python-ci-gate", "config/ci-surfaces.yml", "carve_out"):
        assert needle in text, (
            f"the doctrine must name {needle!r} — it is the authoritative source "
            "for a claim the document makes"
        )
