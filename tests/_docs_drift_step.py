"""The ONE source of truth for the `docs` job's generated-artifact drift step.

`ci.yml`'s `docs` job is a REQUIRED context with **no** `paths:` filter, so it is
the only gate that reaches a docs-only edit — and its drift step is where the
`--check` guards for generated artifacts actually run. Two pins assert that the
step's `run:` block is EXACTLY this list, in this order, and nothing else
(`tests/test_merge_throughput.py` and `tests/test_mcp_rename_table.py`): a
trailing `|| true`, `; true`, `| true`, an `echo` decoy or a commented-out
command must not satisfy them, because any of those makes a REAL failure report
green (#2656).

**Why the list lives here.** It was hand-typed twice, in two files, so #11084's
fifth invocation (`python3 tools/test_design_map_check.py`, the #5090 surface
map) reded BOTH `test (b)` and the `test (a)`/`(c)` shards on every PR in the
repository at once (#11412, root 2) — one workflow edit, two stale expectations,
two red required checks, no diff at fault. Both copies were repaired for that
red, byte-identical (measured), which leaves the next addition one edit away
from reding two shards again: one list means it is ONE edit.

**The count is deliberately not in the prose.** An expectation whose message
says "the four `--check` commands" cannot absorb a fifth invocation that takes
no `--check` flag (this one verifies by default), which is precisely how the
duplication went stale.
"""

# Every invocation the step's `run:` block must carry, in order. The `pip
# install` is first because the siblings after it need the package installed;
# the tool listed last is stdlib-only but is pinned INSIDE the exact list rather
# than exempted — the exactness is the point (#5086, #11084).
DOCS_DRIFT_STEP_COMMANDS = (
    "pip install -e . --quiet",
    "python3 tools/bridge_table.py --check",
    "python3 tools/mcp_rename_table.py --check",
    "python3 tools/sdk_surface.py --check",
    # #5086: the substrate map's doc↔tree guard, which also needs the
    # `fetch-depth: 0` history this job checks out (its `measured_at_commit`
    # ancestry claim cannot be answered on a grafted `fetch-depth: 1` graph).
    "python3 tools/substrate_map_check.py --check",
    # #2820 (landed on `main` while this de-duplication was in flight): the
    # problem map's doc↔tree guard joins the same step for the same `#4454`
    # reason, and it needs HISTORY for the same anchor-ancestry claim its #5086
    # sibling above does.
    "python3 tools/problem_map_check.py --check",
    # #11084: the #5090 vector-layer surface map's own guard. It takes no
    # `--check` — it verifies by default and exits 2 when the map cannot be read.
    "python3 tools/test_design_map_check.py",
    # #11084: the map guard's SECOND arm. `--self-test` proves the checker still
    # catches the mutations its "not trusted" claim rests on; an arm nothing runs
    # would let a quietly weakened rule pass green. `#11292` added it to the step
    # while this branch was de-duplicating the pin, so it is carried here rather
    # than dropped by that refactor.
    "python3 tools/test_design_map_check.py --self-test",
    # #2852: the findings ledger's doc↔tree guard. Same reason as its #5086/#2820
    # siblings — a docs-only edit of the ledger selects NO surface — plus one of
    # its own: the page's §3 is a CORRECTIONS log, and re-measuring it found seven
    # of its twelve rows no longer read as written (four of them overtaken by the
    # fixes they motivated). A verdict
    # outside the closed vocabulary, or a `path:line` that stopped resolving,
    # reads exactly like evidence to the next lane. It needs HISTORY for the
    # `measured_at_commit` ancestry claim, which the depth-1 `test` job cannot
    # answer at all.
    "python3 tools/findings_ledger_check.py --check",
    # #2852: the same guard's second arm. `--self-test` proves it still catches
    # its own mutations (invented verdict, dangling citation, out-of-range line,
    # deleted correction row, a §6 count that disagrees with its own list) and
    # that a cosmetic edit leaves the verdict unchanged. An arm nothing runs is
    # not a guard.
    "python3 tools/findings_ledger_check.py --self-test",
)

# The step's `run:` block, as `_code()`-filtered text, must equal this exactly.
EXPECTED_DOCS_DRIFT_STEP = "\n".join(DOCS_DRIFT_STEP_COMMANDS)
