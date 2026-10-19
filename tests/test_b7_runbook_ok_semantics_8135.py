"""Hermetic doc↔code pin: the B7 operator runbook's meaning of `status == "ok"`.

`docs/runbook/b7-activation-scorecard.md` is the live home of the claim an
operator acts on when they read `recall_attempted` — `mcp_tool_call.status ==
"ok"`. It was maintained in lockstep with that metric as recently as #3675
(commit f16e088da on this exact file), so it is not a historical record.

#8135 changed what an `ok` row EXCLUDES: a failure `_safe` swallowed is recorded
`status: "exec_error"` / `error_kind: "safe_error"` instead of `ok`. The runbook
kept teaching the pre-#8135 meaning ("only that the tool did not raise"), which
is false for that class. It has now drifted twice — the #8135 fix corrected it
once — so the correction is pinned by a test rather than by a comment.

⛔ WHY THIS FILE IS SMALL AND SEPARATE (review P1, two reviews). The pin used to
live in `tests/test_activation_scorecard.py`, which is registered on `api` and
`core` only. `docs/` is in `NON_PYTHON_PREFIXES` and
`docs/runbook/b7-activation-scorecard.md` matches no `SOURCE_PATTERNS` entry, so
a runbook-only diff filters to `changed == []` and takes `select()`'s docs-only
early return — tier1 ONLY. Registered there but not in `tier1`, the pin was
asleep on the ONE change that can break it: the pre-#8135 sentence could be
re-introduced in a docs-only PR and merge green. Same shape and same remedy as
#8614's `test_fly_secret_drift.py` (`docs/infra-runbook.md`) and #8701's
`test_cross_subdomain_cookie_sync.py` — a fast, no-DB file scan, so `tier1` is
the correct lane.

The selection half is pinned by
`tests/test_ci_selection.py::test_b7_runbook_ok_semantics_gate_runs_on_a_runbook_only_diff`
and by the `test_docs_scanning_gates_stay_in_tier1` name list; this file holds
only the prose assertion, so a docs-only PR pays ~0.1s for it.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RUNBOOK = REPO_ROOT / "docs" / "runbook" / "b7-activation-scorecard.md"

# The pre-#8135 wording. Anchored on the two halves that make it false: the
# "only" reduction and the sole "did not raise" definition. Asserted ABSENT.
STALE_CLAIM = 'means only that the tool did not raise'

# The corrected definition, both halves. Asserted PRESENT.
CORRECTED_CLAIM = 'means only that the dispatch neither raised nor returned a failure `_safe` swallowed'

# The #9084 boundary, asserted as the CLAUSE rather than as a bare "#9084"
# substring: a `#9084` mention anywhere in the file would satisfy a substring
# check while the plain-dict clause it stands for was deleted (review P3).
PLAIN_DICT_CLAUSE = 'a tool-level refusal returned as a **plain dict**'


def _flat_runbook() -> str:
    """The runbook with prose whitespace collapsed.

    The file is hard-wrapped, so a reflow must neither fail the pin nor let a
    stale clause hide inside a line break.
    """
    return " ".join(RUNBOOK.read_text().split())


def test_the_runbook_states_the_meaning_of_ok_the_emitter_has() -> None:
    """The runbook teaches the post-#8135 meaning of `ok`, not the old one.

    Fails on the pre-fix runbook (the stale sentence is still there) and on a
    partial correction (either half of the corrected definition missing, or the
    #9084 boundary dropped — an operator who filters on `status` would then read
    the filter as complete).
    """
    flat = _flat_runbook()

    assert STALE_CLAIM not in flat, (
        "the runbook still teaches the PRE-#8135 meaning of `status == ok` "
        "(\"means only that the tool did not raise\"); since #8135 an `ok` row "
        "also excludes a failure `_safe` swallowed (#8135)"
    )
    assert CORRECTED_CLAIM in flat, (
        "the runbook must state that `ok` means the dispatch NEITHER raised NOR "
        "returned a failure `_safe` swallowed (#8135)"
    )
    assert PLAIN_DICT_CLAUSE in flat, (
        "the runbook must name the class that still reads `ok` — a tool-level "
        "refusal returned as a plain dict (#9084) — or an operator will read a "
        "`status` filter as complete"
    )
    assert "#9084" in flat, (
        "the plain-dict clause must carry the #9084 pointer that tracks it"
    )


def test_the_pin_reads_the_runbook_the_scorecard_names() -> None:
    """The pinned path is the file `activation_scorecard` LIMITATIONS cites.

    A silent rename (runbook moved or deleted) would otherwise turn the pin
    above into a `FileNotFoundError`-on-demand rather than a statement about a
    document nobody reads any more. Kept separate from the prose assertion so a
    missing file reports WHY it matters.
    """
    assert RUNBOOK.is_file(), (
        "docs/runbook/b7-activation-scorecard.md is gone; the B7 operator "
        "runbook is the live home of the `status == ok` semantics the "
        "activation scorecard measures (#8135)"
    )
