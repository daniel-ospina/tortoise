"""#7950 capstone — the e2e chain probe's RECONCILIATION READERS are non-vacuous.

The probe (``docs/evidence/7950-capstone/probes/e2e_chain_probe.py``) feeds
``reconciliation.reconcile`` a record reader and a cache reader. Both originally
returned ``{}`` for every key, which made ``field_mismatches`` STRUCTURALLY
zero: ``reconcile_views`` compares two per-key MAPPINGS field by field, and two
empty mappings always agree. The committed log's

    RECONCILE (after a record-sourced rebuild): record vs cache: 0 divergent of
    7 checked (missing_in_cache=0, extra_in_cache=0, field_mismatches=0)

therefore proved **key-set agreement only** — a second writer that MODIFIED an
existing node's property (rather than adding a node) was invisible, so the
evidence doc's *"the end-to-end run proves it on real data"* overstated what was
measured (PR #8609 review, finding 3).

The probe now maps the REAL property sets. These tests pin that contract
DB-free: if either reader regresses to placeholder maps, the field-mutation
assertion below goes red — which is exactly the failure the pre-fix code had.

Why a test for a probe: the probe is the ONLY artifact that runs the shipped
reconcile path against the shipped readers, and it needs live Postgres +
FalkorDB. The reader mapping itself is pure, so the non-vacuity half is pinned
here rather than left to a live run that CI cannot perform.
"""
from __future__ import annotations

import ast
import importlib.util as _ilu
import re
from pathlib import Path

import pytest

from tortoise.cache_projection import RecordNode, RecordView
from tortoise.reconciliation import reconcile_views

_REPO_ROOT = Path(__file__).resolve().parent.parent
_PROBE = (_REPO_ROOT / "docs" / "evidence" / "7950-capstone" / "probes"
          / "e2e_chain_probe.py")


def _load_probe():
    """Load the probe module by path.

    The ``docs/evidence/7950-capstone/`` directory is hyphenated, so the module
    is unreachable with an ``import`` statement — the same spec-loader pattern
    ``tests/test_e2e_live_reconcile_scope_parity.py`` uses for
    ``graph-scripts/``. The probe keeps ``psycopg2`` (the optional ``postgres``
    extra) out of module scope on purpose, so importing it here needs no DB.
    """
    spec = _ilu.spec_from_file_location("capstone_e2e_chain_probe", str(_PROBE))
    assert spec and spec.loader
    module = _ilu.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_PROBE_MODULE = _load_probe()


def _view() -> RecordView:
    return RecordView(nodes=(
        RecordNode(label="Point", key="p1",
                   props={"id": "p1", "text": "hello", "version": 3}),
        RecordNode(label="Source", key="https://x.example",
                   props={"url": "https://x.example"}),
    ))


# ── the record reader carries real property maps ──────────────────────────


def test_record_key_map_carries_the_actual_property_maps():
    mapped = _PROBE_MODULE.record_key_map(_view())
    assert mapped == {
        "Point:p1": {"id": "p1", "text": "hello", "version": 3},
        "Source:https://x.example": {"url": "https://x.example"},
    }


def test_record_key_map_values_are_never_placeholders():
    # the pre-fix defect, asserted directly: every value was `{}`
    assert all(_PROBE_MODULE.record_key_map(_view()).values())


# ── the cache reader carries real property maps ───────────────────────────


def test_cache_key_map_carries_the_actual_property_maps():
    mapped = _PROBE_MODULE.cache_key_map({
        "Point": [{"id": "p1", "text": "hello", "version": 3}],
        "Source": [{"url": "https://x.example"}],
        "Session": [],  # a label with no rows contributes nothing
    })
    assert mapped == {
        "Point:p1": {"id": "p1", "text": "hello", "version": 3},
        "Source:https://x.example": {"url": "https://x.example"},
    }


def test_cache_key_map_skips_a_row_without_its_contract_key():
    # an unkeyed row cannot be addressed by `(label, key)` and is dropped,
    # rather than entering the comparison under a fabricated key
    assert _PROBE_MODULE.cache_key_map({"Point": [{"text": "no id"}]}) == {}


# ── the field comparison is therefore MEASURABLE ──────────────────────────


def test_a_property_value_change_is_a_field_mismatch():
    """The signature the pre-fix readers could not produce.

    A second writer that sets an EXISTING node's property (rather than adding a
    node) is a `field_mismatch`, not an `extra_in_cache` — with `{}`-valued maps
    both sides compare equal and this reads as agreement.
    """
    truth = _PROBE_MODULE.record_key_map(_view())
    cache = _PROBE_MODULE.cache_key_map({
        "Point": [{"id": "p1", "text": "MUTATED", "version": 3}],
        "Source": [{"url": "https://x.example"}],
    })
    report = reconcile_views(truth, cache)
    assert report.field_mismatches == 1
    assert report.divergent == 1
    assert report.missing_in_cache == 0
    assert report.extra_in_cache == 0
    assert report.divergent_keys == ("Point:p1",)


def test_a_foreign_property_on_an_existing_node_is_a_field_mismatch():
    """The exact mutation the probe's step 5c performs, without a live store."""
    truth = _PROBE_MODULE.record_key_map(_view())
    cache = _PROBE_MODULE.cache_key_map({
        "Point": [{"id": "p1", "text": "hello", "version": 3,
                   "second_writer_marker": "direct-cache-write"}],
        "Source": [{"url": "https://x.example"}],
    })
    report = reconcile_views(truth, cache)
    assert report.field_mismatches == 1
    assert report.extra_in_cache == 0  # the KEY is on both sides — value-only


# ── the probes' hardcoded record-migration lists stay in step ─────────────

_PROBE_MIGRATION_LISTS = (
    # (file, constant) — the two LIVE lists (both are applied to a throwaway
    # Postgres) plus the chain probe's dead duplicate.
    (_REPO_ROOT / "docs" / "evidence" / "7950-capstone" / "probes"
     / "e2e_postgres_bootstrap.py", "MIGRATIONS"),
    (_REPO_ROOT / "docs" / "evidence" / "7983-cost-magnitudes" / "probes"
     / "record_read_cost.py", "MIGRATIONS"),
    (_PROBE, "RECORD_MIGRATIONS"),
)


def _migration_list(path: Path, name: str) -> list[str]:
    """A probe's hardcoded list, read with `ast` rather than by importing it.

    `e2e_postgres_bootstrap.py` and `record_read_cost.py` import `psycopg2` at
    module scope (the optional `postgres` extra), so importing them here would
    put a DB driver in a pin that must stay DB-free. `ast.literal_eval` reads the
    constant without executing a line of the module.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if any(getattr(target, "id", None) == name for target in node.targets):
            return list(ast.literal_eval(node.value))
    raise AssertionError(f"{path} no longer defines {name}")


def test_every_probe_applies_the_record_migrations_the_shipped_read_uses():
    """The probes cannot silently apply a schema the projection has moved past.

    Both live probes apply their hardcoded list to a throwaway Postgres and then
    drive the SHIPPED projection, whose `table_columns()` is read from
    `record_projection.RECORD_MIGRATIONS` **on disk**. A forward migration
    missing from a probe list therefore stands up a database older than the
    INSERT the projection emits: `public.point` without #2782's
    `amount_minor`/`currency`/… fails every money-bearing point INSERT with
    `UndefinedColumn`, and until it does the probe measures a record one
    migration behind — the "record poorer than the graph" failure the migration
    exists to close. #8976 review P3: the migration header enumerates the schema
    readers that must SEE these columns, and these copies were not carried
    through.

    Equality, not containment: a stale EXTRA entry is the same defect from the
    other side (a migration the shipped read does not use).
    """
    from tortoise.record_projection import RECORD_MIGRATIONS

    for path, name in _PROBE_MIGRATION_LISTS:
        assert _migration_list(path, name) == list(RECORD_MIGRATIONS), (
            f"{path.relative_to(_REPO_ROOT)}::{name} disagrees with "
            "record_projection.RECORD_MIGRATIONS — the probe would apply a "
            "different record schema than the projection writes against"
        )


def test_placeholder_maps_cannot_see_a_field_mutation():
    """The pre-fix shape, kept as the counter-example the fix is measured from.

    Two `{}`-valued maps agree field by field, so the same mutation that
    `test_a_property_value_change_is_a_field_mismatch` detects reads as zero
    divergence. This is why the probe's non-vacuity guard exists.
    """
    placeholder = {"Point:p1": {}, "Source:https://x.example": {}}
    report = reconcile_views(placeholder, dict(placeholder))
    assert report.field_mismatches == 0
    assert report.divergent == 0


# --------------------------------------------------------------------------
# #8615 review finding (P2): the evidence README quotes the log VERBATIM, so a
# re-measured log must drag the quote with it. The PR rewrote
# `e2e-chain-probe.log`'s reconcile numbers (field_mismatches 1/1/2 → 0/1/1) and
# left the README — the artifact S2's verdict is read from — asserting the old
# numbers and homing a decision it had just closed. Nothing pinned the two
# together, which is exactly why these tests exist.
# --------------------------------------------------------------------------

_README_7950 = _REPO_ROOT / "docs" / "evidence" / "7950-capstone" / "README.md"
_E2E_LOG = _REPO_ROOT / "docs" / "evidence" / "7950-capstone" / "e2e-chain-probe.log"


def _readme_text() -> str:
    return _README_7950.read_text(encoding="utf-8")


def _quoted_run_lines() -> list[str]:
    """The probe-OUTPUT lines the README quotes in its ``console`` block.

    The block opens with the invocation (``$ …`` plus its continued line) and
    the rest is the probe's stdout. Only stdout is quotable-from-the-log, so the
    invocation lines are dropped here rather than in the assertion.
    """
    text = _readme_text()
    first_output = text.index("journal records read: 39")
    block_start = text.rindex("```console", 0, first_output)
    block_end = text.index("```", first_output)
    lines = text[block_start:block_end].splitlines()[1:]
    return [ln for ln in lines if ln.strip() and not ln.lstrip().startswith("$")
            and not ln.startswith("    ")]


def test_the_evidence_readme_quotes_the_log_it_cites_verbatim():
    """Every quoted run line must exist in `e2e-chain-probe.log`, exactly.

    Pre-fix (#8615's own PR rewrote the log's reconcile lines) this fails with
    four missing lines: the README still carried `field_mismatches=1` /
    `=2` and `2 divergent of 8` where the log says `field_mismatches=0` / `=1`
    and `1 divergent of 8`. The README calls the block "verbatim", so a stale
    quote is a false claim about its own evidence, not a formatting nit.
    """
    log_lines = set(_E2E_LOG.read_text(encoding="utf-8").splitlines())
    quoted = _quoted_run_lines()
    assert quoted, "the README no longer quotes the end-to-end run at all"
    missing = [ln for ln in quoted if ln not in log_lines]
    assert not missing, (
        "docs/evidence/7950-capstone/README.md quotes probe output that does not "
        "exist in e2e-chain-probe.log — re-quote the block from the log after "
        "re-measuring:\n" + "\n".join(missing)
    )


def test_the_s2_verdict_reads_the_clean_rebuild_the_log_now_shows():
    """S2's deciding artifact must cite the measurement its log actually holds.

    The field half is what `#8615` closed: a clean record-sourced rebuild is
    `field_mismatches=0`, while a second-writer property mutation still reports
    `1`. Pre-fix the S2 row claimed `field_mismatches=1` "the `vecf32` narrowing"
    and homed `#8615` as OPEN — the opposite of the log in the same directory.
    """
    readme = _readme_text()
    verdict_rows = [ln for ln in readme.splitlines() if ln.startswith("| **S2** |")]
    assert len(verdict_rows) == 2, "expected exactly one S2 verdict row and one S2 blocker row"

    verdict = next(ln for ln in verdict_rows if "divergence is OBSERVED" in ln)
    assert "field_mismatches=0" in verdict, (
        "the S2 verdict row must cite the clean rebuild the log now measures: "
        + verdict
    )

    blocker = next(ln for ln in verdict_rows if "running reconciliation" in ln)
    # The field half is CLOSED; the row may record that, but must not re-home it
    # as the open blocker.
    assert "`#8615` (OPEN)" not in blocker and "#8615`** (OPEN)" not in blocker, (
        "the S2 blocker row still homes the closed comparison-rule decision as "
        "OPEN: " + blocker
    )


def test_the_readme_no_longer_asserts_the_field_half_is_an_open_s2_blocker():
    """The prose that explained `field_mismatches=1` as a deferral is now false.

    Pre-fix the README carried a section titled *"A second, independent reason S2
    cannot go green — the field half"* (and the "except the first line only"
    verbatim caveat, which the log's header block also invalidates). Both are
    claims about the pre-fix behaviour `#8615` removes.
    """
    readme = _readme_text()
    assert "second, independent reason S2 cannot go green" not in readme, (
        "the README still argues the field half is an independent reason S2 "
        "cannot go green; `#8615` closed it"
    )
    assert "except the first line only" not in readme, (
        "the README's verbatim caveat no longer describes the log (it carries a "
        "provenance header block)"
    )


# --------------------------------------------------------------------------
# #9189 review (finding 1): the census is S2's DECIDING ARTIFACT, and the PR
# that gave `read_record_view` its first production caller left it asserting
# "0 production call sites" for that function. A lane reading the README then
# re-derives wiring that has already landed — the stale-flag class the fleet
# rules name. These two pins keep the log and the prose it feeds together.
# --------------------------------------------------------------------------

_S2_CENSUS_LOG = _REPO_ROOT / "docs" / "evidence" / "7950-capstone" / "s2-wiring-census.log"


def _census_headline_lines() -> list[str]:
    """The `== <target>: N production call site(s) ==` lines from the log."""
    return [ln for ln in _S2_CENSUS_LOG.read_text(encoding="utf-8").splitlines()
            if ln.startswith("== ")]


def test_the_s2_census_records_the_callers_that_now_exist():
    """`0` for `read_record_view` was true before `#9189`, and false after it.

    Pre-fix the committed log (and the S2 row quoting it) reported **0** for
    `cache_projection.read_record_view` while `tortoise/__main__.py`'s
    `record-reconcile` verb already called it, so the evidence artifact
    contradicted the merged code it is the evidence for.
    """
    log = _S2_CENSUS_LOG.read_text(encoding="utf-8")
    assert "== cache_projection.read_record_view: 1 production call site(s) ==" in log, (
        "the census no longer records the production caller #9189 added — "
        "re-run docs/evidence/7950-capstone/probes/s2_wiring_census.py")
    verdict = next(ln for ln in _readme_text().splitlines()
                   if ln.startswith("| **S2** |") and "divergence is OBSERVED" in ln)
    assert "all have **0 production call sites**" not in verdict, (
        "the S2 verdict row still claims every module is unwired: " + verdict)


def test_the_s2_census_quote_matches_the_log():
    """Every headline the README quotes is the log's own, byte for byte.

    The README presents the block as the probe's output; a re-cut log that does
    not drag the quote with it is a false claim about its own evidence (the
    #8615 finding, one artifact over).
    """
    headlines = _census_headline_lines()
    assert len(headlines) == 4, f"expected four census headlines, got {headlines}"
    readme = _readme_text()
    missing = [ln for ln in headlines if ln not in readme]
    assert not missing, (
        "docs/evidence/7950-capstone/README.md quotes census numbers that are "
        "not in s2-wiring-census.log — re-quote the block after re-measuring:\n"
        + "\n".join(missing))


# --------------------------------------------------------------------------
# #9189 review (the re-cut census): the log is a MEASUREMENT, and the pattern
# must see every spelling a wired caller can use. Two ways the deciding
# artifact could lie, both pinned here.
# --------------------------------------------------------------------------

_CENSUS_PROBE = (_REPO_ROOT / "docs" / "evidence" / "7950-capstone"
                 / "probes" / "s2_wiring_census.py")


def _load_census_probe():
    """Load the S2 census probe by path (the directory is hyphenated)."""
    spec = _ilu.spec_from_file_location("capstone_s2_wiring_census",
                                       str(_CENSUS_PROBE))
    assert spec and spec.loader
    module = _ilu.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_CENSUS_MODULE = _load_census_probe()


def test_the_census_pattern_sees_every_call_spelling(tmp_path):
    """A wired `reconcile` must be COUNTED, however it is spelled.

    The re-cut pattern's bare-call alternative carries a `(?<![\\w.])`
    lookbehind, which rejects a dotted receiver — so without its own
    alternative the MODULE-QUALIFIED spelling
    (`reconciliation.reconcile(a, b)`, the natural one for a module-level
    function imported as a module) reads as **0 production call sites** while
    the reconciler IS wired. That is the stale-evidence failure this census
    exists to prevent, reproduced inside the census. Pre-fix the BARE-call case
    fails (only the dotted spelling had an alternative); the dotted case failed
    against an earlier cut, which is why both are kept in one test.
    """
    source = "\n".join([
        "from tortoise import reconciliation",
        "reconciliation.reconcile(record_reader, cache_reader)",
        "x = reconcile(record_reader, cache_reader)",
        "from tortoise.reconciliation import (",
        "    reconcile,",
        ")",
        "def reconcile(a, b):",          # a definition is NOT a call site
        "self.reconcile(a, b)",          # nor is a method of the same name
        "consistency.reconcile(a, b)",   # nor is another module's
        # ...and PROSE inside a multi-line f-string is not a call site either
        # (3.12 tokenizes it as FSTRING_START/MIDDLE/END, not one STRING).
        "note = f\"\"\"",
        "the reconciler is wired: reconciliation.reconcile(a, b)",
        "and reconcile(a, b) is the bare spelling",
        "\"\"\"",
    ])
    (tmp_path / "wired.py").write_text(source, encoding="utf-8")

    hits = _CENSUS_MODULE.census(
        [str(tmp_path)], _CENSUS_MODULE.TARGETS["reconciliation.reconcile"])
    lines = [h.split(": ", 1)[1] for h in hits]

    assert "reconciliation.reconcile(record_reader, cache_reader)" in lines
    assert "x = reconcile(record_reader, cache_reader)" in lines
    assert not any(ln.startswith("def reconcile") for ln in lines)
    assert not any("self.reconcile(" in ln for ln in lines)
    assert not any("consistency.reconcile(" in ln for ln in lines)
    # A multi-line f-string's prose is not a call site. `_prose_lines` claims
    # STRING *and* FSTRING spans; before that claim held, these two lines
    # inflated the count.
    assert not any("the reconciler is wired" in ln for ln in lines)
    assert not any("is the bare spelling" in ln for ln in lines)


def _production_section(text: str) -> list[str]:
    """The census output ABOVE the test population (the production counts).

    A leading `#` block is the artifact's PROVENANCE header — when the numbers
    were taken and by which command — and carries no measurement of its own, so
    it is skipped here. The pin that compares against this section compares
    measurements, not metadata (#9196 review P2: without a header a reader
    cannot tell which tree the numbers describe, which is how the informational
    half went stale unseen).
    """
    out: list[str] = []
    for line in text.splitlines():
        if line.startswith("#"):
            continue
        if line.startswith("test population:"):
            break
        out.append(line)
    return out


# A census hit line as the probe prints it: `  <path>:<ordinal>: <source text>`.
_HIT_LINE = re.compile(r"^(?P<path>[^:]+):(?P<ordinal>\d+):(?P<rest>.*)$")


def _without_line_ordinal(line: str) -> str:
    """`  tortoise/x.py:727: view = ...` -> `  tortoise/x.py: view = ...`.

    A line that is not a hit line (a `== target: N ... ==` headline, the
    `production population:` header) is returned unchanged.
    """
    m = _HIT_LINE.match(line)
    if not m:
        return line
    return f"{m.group('path')}:{m.group('rest')}"


def _normalise_census(printed: list[str], recorded: list[str]) -> tuple[list[str], list[str]]:
    """(live, committed) with each hit line's line ORDINAL dropped.

    What the census CLAIMS is a count per target plus the FILE each caller
    lives in. The ordinal is not part of that claim, and pinning it makes every
    unrelated edit above a call into a red `core`/`api` test whose remedy is
    re-cutting another issue's evidence log (#9196 review P2). Measured: the
    hits sit in `tortoise/__main__.py` and `tortoise/record_writer.py`, two of
    the most-edited files in the tree — 4 of the last 50 `origin/main` commits
    touched the former, and #8969 inserted 45 lines at 83/252/270, all above
    the `read_record_view` call. The `file:line` stays IN the log for the human
    reader; it is simply not what the pin compares.

    Dropping the WHOLE hit line would be fail-open, so the file is kept: a
    caller that moves file, appears, or disappears still reddens.
    """
    return (
        [_without_line_ordinal(ln) for ln in printed if ln],
        [_without_line_ordinal(ln) for ln in recorded if ln],
    )


_PROVENANCE_COMMIT = re.compile(r"Measured at:.*@[0-9a-f]{7,40}\b")
_PROVENANCE_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")


def _provenance_problems(header: list[str]) -> list[str]:
    """What the log's provenance header fails to state (#9196 review P2).

    The pin is on the SHAPE — when, which tree (by COMMIT), which command — and
    deliberately not on this PR's branch name. A branch is transient (GitHub
    deletes it on merge, and the re-cut of the log will be done on whatever
    branch carries it), so a literal `feat/9189-tm@` pin makes the artifact
    impossible to re-cut anywhere else: the re-cut writes its own branch, the
    assertion fails, and the only escape is to edit this literal — i.e. to
    edit the grader until its own artifact passes.
    """
    joined = "\n".join(header)
    problems: list[str] = []
    if not header:
        problems.append("the log carries no `#` provenance header")
    if "Measured at:" not in joined:
        problems.append("no `Measured at:` line names the tree measured")
    if not _PROVENANCE_COMMIT.search(joined):
        problems.append("`Measured at:` names no commit (`@<sha>`)")
    if not _PROVENANCE_DATE.search(joined):
        problems.append("no ISO date says when the numbers were taken")
    if "probes/s2_wiring_census.py" not in joined:
        problems.append("the command that produced the numbers is not named")
    return problems


def test_the_s2_census_log_carries_its_provenance():
    """The log must say WHICH tree its numbers describe (#9196 review P2).

    The production section is re-measured by the pin below, but the
    `test population:` counts are informational by design — nothing reds when
    they drift. Their remedy is provenance: a header naming the head, the date
    and the command, exactly as `e2e-chain-probe.log` carries, so a reader can
    tell a measurement from a memory. Without it the two stale counts found at
    #9196 (replay 1 vs 2, rebuild_from_record 6 vs 10) were invisible: nothing
    in the file said what tree they had been cut from.
    """
    text = _S2_CENSUS_LOG.read_text(encoding="utf-8")
    header = [ln for ln in text.splitlines() if ln.startswith("#")]
    assert not _provenance_problems(header), (
        "the census log's provenance header is incomplete: "
        f"{_provenance_problems(header)} — a log whose numbers cannot be dated "
        "re-stales silently (the #9196 review's finding)")
    assert header == text.splitlines()[:len(header)], (
        "the provenance header must LEAD the file — a header below a "
        "measurement reads as if it described the lines above it")


def test_the_provenance_pin_pins_the_SHAPE_not_this_branch():
    """A re-cut on ANY branch satisfies the pin; a commit-less header does not.

    Non-vacuity for the fix: the header below is what a re-cut performed on
    another branch (the normal case — this PR's branch is deleted on merge)
    writes. The PRE-FIX assertion required the literal `feat/9189-tm@`, so it
    rejected exactly this header; the shape assertion accepts it and still
    rejects every header that cannot date its numbers.
    """
    re_cut = [
        "# Measured at: fix/overlay-ordered@0123abc on 2031-02-03",
        "# Command: uv run python "
        "docs/evidence/7950-capstone/probes/s2_wiring_census.py",
    ]
    assert _provenance_problems(re_cut) == []
    # ...and the pre-fix literal is NOT what made it pass.
    assert "feat/9189-tm@" not in "\n".join(re_cut)

    assert _provenance_problems([]) == [
        "the log carries no `#` provenance header",
        "no `Measured at:` line names the tree measured",
        "`Measured at:` names no commit (`@<sha>`)",
        "no ISO date says when the numbers were taken",
        "the command that produced the numbers is not named",
    ]
    assert _provenance_problems(["# Measured at: fix/x on 2031-02-03"]), (
        "a header with no commit cannot say which TREE was measured")
    assert _provenance_problems(["# Measured at: fix/x@0123abc"]), (
        "a header with no date cannot say WHEN the numbers were taken")
    assert _provenance_problems(["# Measured at: fix/x@0123abc on 2031-02-03"]), (
        "a header that names no command cannot be reproduced")


def test_the_s2_census_log_is_a_LIVE_measurement(monkeypatch, capsys):
    """S2's deciding artifact is re-MEASURED, not compared to itself.

    `test_the_s2_census_quote_matches_the_log` pins the README to the log, and
    both are committed text — so together they cannot detect the TREE moving
    out from under the claim. That is what makes the log a stale flag rather
    than a measurement. Here the committed probe is re-run over the production
    population and the result is compared to the committed log: a new caller, a
    removed caller, or a line move all redden this instead of silently
    contradicting the artifact the S2 verdict rests on.

    Only the PRODUCTION population is pinned. The log's `test population:`
    counts are informational: they move whenever any test file mentions one of
    these names, which is not a claim about whether the product is wired.

    The hit lines are compared by COUNT and FILE, with each line's ordinal
    normalised away (`_normalise_census`) — see that helper for why: the
    ordinal is not part of the claim, and pinning it reddened this `core`/`api`
    test on every unrelated edit above a censused call (#9196 review P2). A new
    caller, a removed caller, or a caller that moves to another file still
    reddens, which is the wiring signal the S2 verdict rests on.

    `tools/ci_selection.py`'s `CORE_ALSO` keeps this evidence bundle selecting
    the pin, so a log-only edit cannot land with the pin unrun.
    """
    monkeypatch.chdir(_REPO_ROOT)
    measured = _CENSUS_MODULE.census_all(_CENSUS_MODULE.PROD_DIRS,
                                        _CENSUS_MODULE.TARGETS)
    printed = [f"production population: {_CENSUS_MODULE.PROD_DIRS}/**/*.py", ""]
    for name in _CENSUS_MODULE.TARGETS:
        hits = measured[name]
        printed.append(f"== {name}: {len(hits)} production call site(s) ==")
        printed.extend(f"  {h}" for h in hits)
        printed.append("")

    committed = _S2_CENSUS_LOG.read_text(encoding="utf-8")
    live, recorded = _normalise_census(printed, _production_section(committed))
    assert live == recorded, (
        "s2-wiring-census.log no longer matches a live run of "
        "docs/evidence/7950-capstone/probes/s2_wiring_census.py — re-run the "
        "probe and re-cut the log (and anything quoting it):\n"
        + "\n".join(printed))


def test_the_census_pin_catches_a_wiring_change_but_not_a_line_move():
    """Both directions of the normalisation, because each one alone is a bug.

    Pinning the ordinal (pre-fix) reddens on unrelated PRs; dropping the whole
    hit line would be FAIL-OPEN — a new or removed caller would then never
    redden the artifact the S2 verdict rests on. So: a line move agrees, and
    every wiring change still disagrees.
    """
    recorded = [
        "== cache_projection.read_record_view: 1 production call site(s) ==",
        "  tortoise/caller.py:375: view = read_record_view(conn)",
        "",
    ]

    def live(*hits: str, count: int | None = None) -> list[str]:
        n = len(hits) if count is None else count
        return [f"== cache_projection.read_record_view: {n} production "
                "call site(s) ==", *hits, ""]

    moved = live("  tortoise/caller.py:412: view = read_record_view(conn)")

    # PRE-FIX the ordinal was part of the comparison, so this unrelated line
    # move (4 of the last 50 commits on origin/main touch this file) reddened.
    assert ([ln for ln in moved if ln]
            != [ln for ln in recorded if ln]), "the fixture must move the line"
    # POST-FIX the same move agrees — the claim did not change.
    assert _normalise_census(moved, recorded)[0] == _normalise_census(moved, recorded)[1]

    # Every WIRING change still reddens.
    for wired in (
        live(),                                             # caller removed
        live("  tortoise/caller.py:375: view = read_record_view(conn)",
             "  tortoise/second.py:9: view = read_record_view(conn)"),  # added
        live("  tortoise/moved.py:375: view = read_record_view(conn)"),   # moved file
    ):
        a, b = _normalise_census(wired, recorded)
        assert a != b, f"a wiring change must redden: {wired}"

# ── §17.1's acceptance line is MEASURED, not hardcoded (#9079) ────────────
#
# The issue's acceptance command is
#
#     uv run python docs/evidence/7950-capstone/probes/e2e_chain_probe.py \
#         | grep "ARMED RAW HANDLE"
#
# and it must read `refused`. Until #9079 the line was a LITERAL printed one
# line after the write: it read `SUCCEEDED (direct cache write)` whether the
# statement landed, was refused, or never ran — and on an armed projection the
# refusal raised OUT of the probe, so the acceptance grep could never see
# `refused` at all. The S1 verdict rests on that line, so a hardcoded one is a
# false PASS in the epic's evidence.

_PINNED_REFUSED = "refused"
_PINNED_LANDED = "SUCCEEDED (direct cache write)"

#: The token the issue's acceptance command greps for. The probe emits exactly
#: ONE line carrying it, and that line is the measurement.
_GREP_TOKEN = "ARMED RAW HANDLE"

#: The S1 summary's marker — a verdict line that is not in the grep population.
_S1_TOKEN = "S1 ("


_REFUSED_STUB = object()


def _probe_tree() -> ast.Module:
    """The probe's AST — read from disk, never by importing `main` (needs a DB)."""
    return ast.parse(_PROBE.read_text(encoding="utf-8"))


def _report(monkeypatch, rows, *, guard_enforces, write_raises=None):
    """The probe's report for a stub projection with the guard flag SET.

    The mode is no longer an argument: `s1_report_text` reads
    `tortoise.projection._HANDLE_GUARD_ENFORCES` itself, so the test drives the
    same source the door consults (`monkeypatch`, so it is restored).
    """
    import tortoise.projection as projection

    monkeypatch.setattr(projection, "_HANDLE_GUARD_ENFORCES", guard_enforces)
    return _PROBE_MODULE.s1_report_text(
        _stub_projection(read_rows=rows, write_raises=write_raises))


def test_the_report_is_one_measured_text_in_both_modes(monkeypatch):
    """The pin is on the EMITTED TEXT, for all four mode × outcome cells.

    The report is one value, so there is no per-line seam for a hardcoded line
    to be inserted into (the predecessor pinned a line LIST and let `main` print
    it one line at a time — a hardcoded line after the loop did not disturb it).

    ⚠️ WHAT THIS PINS, PRECISELY. It asserts the report's STRUCTURE and that its
    acceptance line carries the MEASURED verdict — not an equality against an
    expected literal, which would turn every legitimate wording change into a
    test edit. What closes the remaining gap is the CELL COVERAGE: mode and
    outcome are independent, so all four combinations are driven, and a verdict
    read off `guard_enforces` instead of the measurement reds.
    """
    landed_refused, landed = _report(monkeypatch, [[1]], guard_enforces=False)
    refused_refused, refused = _report(
        monkeypatch, [[0]], guard_enforces=True, write_raises=_probe_refusal())
    # THE TWO OFF-DIAGONAL CELLS. Mode and outcome are INDEPENDENT — a scope in
    # flight or an unbound journal lets a write land while the switch is True
    # (`_refuse_unauthorized_cache_write` returns early on either) — so pinning
    # only `(False, landed)` and `(True, refused)` would let the verdict be read
    # off the MODE instead of the measurement and stay green.
    _, landed_enforcing = _report(monkeypatch, [[1]], guard_enforces=True)
    _, refused_measuring = _report(monkeypatch, [[0]], guard_enforces=False,
                                   write_raises=_probe_refusal())

    assert landed_refused is False and refused_refused is True, (
        "the branch signal must follow the measurement, not the guard mode")
    assert landed_enforcing.splitlines()[1].endswith(_PINNED_LANDED), (
        "a write that LANDED was reported as refused because the switch is on")
    assert refused_measuring.splitlines()[1].endswith(_PINNED_REFUSED), (
        "a write that was REFUSED was reported as landed because the switch is off")

    landed_lines = landed.splitlines()
    refused_lines = refused.splitlines()

    for lines, mode, s1 in ((landed_lines, "measures only", "OPEN"),
                            (landed_enforcing.splitlines(), "enforces", "OPEN"),
                            (refused_lines, "enforces", "CLOSED"),
                            (refused_measuring.splitlines(),
                             "measures only", "CLOSED")):
        # EXACTLY the three lines, in order — an extra line is a change to the
        # pin, i.e. a reviewed act rather than an invisible insertion.
        assert len(lines) == 3, lines
        assert lines[0].startswith("HANDLE GUARD MODE:"), lines
        assert mode in lines[0], lines
        assert lines[1].startswith(_GREP_TOKEN + " (proj.g.query):"), lines
        assert lines[2].startswith(_S1_TOKEN), lines
        assert s1 in lines[2], lines

    # the acceptance line carries the MEASURED verdict, both ways
    assert landed_lines[1].endswith(_PINNED_LANDED), landed_lines
    assert refused_lines[1].endswith(_PINNED_REFUSED), refused_lines
    # …and exactly one line is greppable, so the command is unambiguous
    assert sum(_GREP_TOKEN in ln for ln in landed_lines) == 1
    assert sum(_GREP_TOKEN in ln for ln in refused_lines) == 1
    # the two modes differ, so no constant can satisfy both
    assert landed != refused


def test_the_report_is_written_once_as_the_measured_text():
    """`main` writes the builder's text in ONE statement — no per-line seam."""
    tree = _probe_tree()
    main = next(node for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name == "main")
    calls = [node for node in ast.walk(main)
             if isinstance(node, ast.Call)
             and getattr(node.func, "id", None) == "s1_report_text"]
    assert len(calls) == 1, (
        f"`main` must call `s1_report_text` exactly once, found {len(calls)}")
    writes = [node for node in ast.walk(main)
              if isinstance(node, ast.Call)
              and getattr(node.func, "attr", None) == "write"
              and isinstance(node.func.value, ast.Attribute)
              and node.func.value.attr == "stdout"]
    assert len(writes) == 1, (
        "`main` must write the report with exactly one `sys.stdout.write`, "
        f"found {len(writes)}")


def test_the_mode_follows_the_guard_flag_and_no_caller_can_supply_it(monkeypatch):
    """The MODE is read from the guard, not handed in by the caller.

    Pre-fix `s1_report_text(proj, *, guard_enforces)` took the mode as an
    ARGUMENT and `main` passed `_HANDLE_GUARD_ENFORCES`. Correct at the one call
    site, pinned nowhere: `guard_enforces=True` there made the epic's deciding
    artifact announce an ARMED door while `_HANDLE_GUARD_ENFORCES` was still
    `False` — and no test reddened, because a `True` literal is not a value
    string and the TOTAL verdict rule sees only `str`/`bytes` (#9079 review, P3).

    Two independent halves, both of which the pre-fix code fails: the emitted
    MODE tracks the flag (so a stale or invented argument cannot outlive it),
    and there is NO parameter through which a mode could be asserted at all.
    """
    import tortoise.projection as projection

    # ── behaviour: the emitted MODE is the projection module's own switch ──
    monkeypatch.setattr(projection, "_HANDLE_GUARD_ENFORCES", False)
    _, measuring = _PROBE_MODULE.s1_report_text(_stub_projection(read_rows=[[1]]))
    monkeypatch.setattr(projection, "_HANDLE_GUARD_ENFORCES", True)
    _, enforcing = _PROBE_MODULE.s1_report_text(_stub_projection(read_rows=[[1]]))
    assert measuring.splitlines()[0].endswith("measures only (refusal NOT armed, #9124 part 2)"), (
        measuring.splitlines()[0])
    assert enforcing.splitlines()[0].endswith("enforces"), enforcing.splitlines()[0]
    # the flag moved NOTHING else: both cells are the landed outcome
    assert measuring.splitlines()[1:] == enforcing.splitlines()[1:], (
        "the MODE flag changed a line it does not own")

    # ── structure: there is no argument to falsify ────────────────────────
    tree = _probe_tree()
    builder = next(node for node in tree.body
                   if isinstance(node, ast.FunctionDef)
                   and node.name == "s1_report_text")
    params = [a.arg for a in builder.args.args + builder.args.kwonlyargs]
    assert params == ["proj"], (
        "`s1_report_text` must take only the projection: a supplied mode can "
        f"disagree with the guard's own flag, found params={params}")
    main = next(node for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name == "main")
    call = next(node for node in ast.walk(main)
                if isinstance(node, ast.Call)
                and getattr(node.func, "id", None) == "s1_report_text")
    assert not call.keywords, (
        "`main` must not pass a mode (or anything else) to the builder: "
        f"{[kw.arg for kw in call.keywords]}")


def test_the_fold_line_names_the_exception_that_was_caught():
    """The FOLD line keeps the exception name AND stays verdict-free.

    The informative literal `refused (DirectCacheWriteRefused)` was a false
    positive of the TOTAL verdict rule — `"refused"` is a substring of the
    CLASS name — so it had been flattened to "blocked (§17.1 refusal)", losing
    the one record of which exception the fold gate raised (#9079 review, P3).
    The name is now read off the caught exception, so it is both accurate and
    unspellable. Both halves are checked, and both red on the pre-fix code
    (which had no `armed_fold_line` at all).
    """
    from tortoise.projection import DirectCacheWriteRefused

    class _FoldRefusal(DirectCacheWriteRefused):
        """A subclass, so a hardcoded class name cannot satisfy this pin."""

    line = _PROBE_MODULE.armed_fold_line(
        _stub_projection(read_rows=[[1]],
                         apply_raises=_FoldRefusal("blocked")))
    assert type(_FoldRefusal("x")).__name__ in line, line
    assert DirectCacheWriteRefused.__name__ not in line, (
        "the line spells the base class instead of naming what was caught: " + line)

    # a fold that does NOT raise is reported as such, so the line is a verdict
    assert _PROBE_MODULE.armed_fold_line(
        _stub_projection(read_rows=[[1]])).endswith("NO RAISE")

    # …and the exception name cannot be spelled in the source either: a value
    # constant carrying it would re-arm the over-block the derivation removes.
    spelled = [node.value for node in _non_docstring_constants(_probe_tree())
               if DirectCacheWriteRefused.__name__ in _const_text(node)]
    assert not spelled, (
        "the probe spells the exception class in a literal, which the TOTAL "
        f"verdict rule refuses (it contains a verdict token): {spelled}")


def _non_docstring_constants(tree):
    """String/bytes constants that are VALUES — bare-`Expr` prose is skipped.

    `bytes` is in the population on purpose: `sys.stdout.write(b"…refused".decode())`
    is a literal verdict that a `str`-only rule does not see.
    """
    prose = {id(node.value) for node in ast.walk(tree)
             if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)}
    return [node for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, (str, bytes))
            and id(node) not in prose]


def _const_text(node) -> str:
    """The constant's text, whichever of the two shapes it arrived in."""
    value = node.value
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else value


def _verdict_constant_names() -> set[str]:
    return {"RAW_HANDLE_REFUSED", "RAW_HANDLE_LANDED"}


def _definition_constant_ids(tree) -> set[int]:
    """`id()` of the value in `RAW_HANDLE_REFUSED = "refused"` (plain or typed)."""
    out: set[int] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign):
            targets, value = [node.target], node.value
        else:
            continue
        if isinstance(value, ast.Constant) and any(
                getattr(t, "id", None) in _verdict_constant_names()
                for t in targets):
            out.add(id(value))
    return out


def test_no_string_literal_can_spell_a_verdict():
    """TOTAL rule over every VALUE-string constant in the probe.

    The predecessor of this pin was a POPULATION search ("does a printed line
    contain the token?"), and a population defined by a substring is defeated by
    splitting the token across a concatenation — measured twice in review. This
    rule has no population: ANY value constant spelling `refused` or `succeeded`
    fails except the two DEFINITION constants, so a verdict in the source has to
    come from one of those — i.e. from the measurement.

    ⚠️ Declared residual, not a claim: a fragment split into pieces that are not
    themselves a verdict spelling (`"refu" + "sed"`), or `chr()` assembly, is not
    caught here. That is a deliberately obfuscated edit, visible in review; the
    rule makes the ACCIDENTAL regression — the pre-fix line and every natural
    variant — impossible to reintroduce silently.
    """
    tree = _probe_tree()
    definitions = _definition_constant_ids(tree)
    offenders = []
    for node in _non_docstring_constants(tree):
        if id(node) in definitions:
            continue
        folded = _const_text(node).casefold()
        if "refused" in folded or "succeeded" in folded:
            offenders.append(node.value)
    assert not offenders, (
        "the probe spells a §17.1 verdict in literal text, so the acceptance "
        "line could report a verdict that was never measured — build it from "
        f"`armed_raw_handle_write(proj)` instead: {offenders}")


def test_only_the_report_builder_may_name_a_verdict():
    """The verdict LABELS and CONSTANTS live in one function and nowhere else.

    Total over the module, not a population: any value-string containing the
    acceptance token or the S1 marker, and any reference to `RAW_HANDLE_*`, must
    sit inside `s1_report_text` or `armed_raw_handle_write` (which RETURNS the
    verdict constants). Nowhere else — in particular not `main` — so a hardcoded
    S1 summary or acceptance line has no constant to reach for; the analogous
    obfuscation is the declared residual above.
    """
    tree = _probe_tree()
    # WHERE they may live: the function that MEASURES (it returns the constants)
    # and the function that REPORTS (it builds the lines). Nowhere else — in
    # particular not `main`.
    measured_in = {"armed_raw_handle_write", "s1_report_text"}
    allowed = {id(node) for node in ast.walk(tree)
               if isinstance(node, ast.FunctionDef) and node.name in measured_in
               for node in ast.walk(node)}
    offenders = []
    for node in _non_docstring_constants(tree):
        if id(node) in allowed:
            continue
        text = _const_text(node)
        if _GREP_TOKEN in text or _S1_TOKEN in text:
            offenders.append(text)
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in _verdict_constant_names() \
                and id(node) not in allowed \
                and not isinstance(node.ctx, ast.Store):
            offenders.append(f"name:{node.id}")
    assert not offenders, (
        "the §17.1 verdict labels/constants are used outside `s1_report_text`, "
        "so a verdict can be emitted without the measurement behind it: "
        f"{offenders}")


def test_the_verdict_constants_are_the_two_tokens_the_grep_reads():
    # `refused` is the token the issue's acceptance command greps for; if the
    # constant is renamed the command silently matches a different word.
    assert _PROBE_MODULE.RAW_HANDLE_REFUSED == _PINNED_REFUSED
    assert _PROBE_MODULE.RAW_HANDLE_LANDED == _PINNED_LANDED
    assert _PINNED_REFUSED != _PINNED_LANDED


def _probe_refusal():
    from tortoise.projection import DirectCacheWriteRefused

    return DirectCacheWriteRefused("the §17.1 handle refused the statement")


def _armed_projection(tmp_path):
    """A journal-backed projection — §17.1's "armed" precondition."""
    from tortoise.projection import FalkorProjection

    proj = FalkorProjection(str(tmp_path / "capstone-cache.db"))
    proj._bind_journal(str(tmp_path / "capstone-journal.jsonl"))
    assert proj._journal_backed
    return proj


def test_the_line_reads_refused_when_the_handle_refuses(tmp_path, monkeypatch):
    """The S1-GREEN path: the acceptance token is produced by the MEASUREMENT.

    This is the state the issue asks for, and the test does not depend on the
    default flipping: it arms the door the same way the guard's own suite does,
    so the day `_HANDLE_GUARD_ENFORCES` lands True the probe reads `refused`
    with no change here.
    """
    import tortoise.projection as projection
    from tortoise.projection import DirectCacheWriteRefused

    proj = _armed_projection(tmp_path)
    monkeypatch.setattr(projection, "_HANDLE_GUARD_ENFORCES", True)

    # the write is refused...
    with pytest.raises(DirectCacheWriteRefused):
        proj.g.query("CREATE (:Point {id:'p-direct-write-7950'})")
    # ...and the probe's line says so, instead of asserting the opposite.
    assert _PROBE_MODULE.armed_raw_handle_write(proj) == _PINNED_REFUSED

    # NON-VACUITY: the refusal is real — nothing reached the cache.
    created = proj.query("MATCH (n:Point {id:'p-direct-write-7950'}) RETURN count(n)").result_set
    assert created[0][0] == 0, "the armed handle let the second writer through"


def test_the_line_reads_succeeded_when_the_write_lands(tmp_path, monkeypatch):
    """The MEASURE-mode path, so the helper cannot rot into a constant.

    The MODE is pinned, not inherited: `_HANDLE_GUARD_ENFORCES` is False in the
    landed state (#9124 part 1), and this test must keep testing the
    measure-mode branch on the day it flips — otherwise the flip reds it for a
    probe that is right, which is a false red in the guard's own suite.
    """
    import tortoise.projection as projection
    from tortoise.projection import (
        _reset_undeclared_handle_writes,
        undeclared_handle_writes,
    )

    proj = _armed_projection(tmp_path)
    monkeypatch.setattr(projection, "_HANDLE_GUARD_ENFORCES", False)
    _reset_undeclared_handle_writes()
    try:
        assert _PROBE_MODULE.armed_raw_handle_write(proj) == _PINNED_LANDED
        # the write really landed — the token is not merely different
        created = proj.query(
            "MATCH (n:Point {id:'p-direct-write-7950'}) RETURN count(n)"
        ).result_set
        assert created[0][0] == 1
        # and measure mode RECORDED it: the census that a flip would refuse
        assert any("p-direct-write-7950" in key for key in undeclared_handle_writes()), (
            "measure mode did not record the undeclared write the probe made"
        )
    finally:
        _reset_undeclared_handle_writes()


class _StubResult:
    """The one thing the probe reads off a vendor result."""

    def __init__(self, rows):
        self.result_set = rows


def _stub_projection(*, read_rows, write_raises=None, apply_raises=None):
    """A projection stand-in: writes return a result, reads return `read_rows`."""
    import types

    def _query(statement):
        if "CREATE" in statement:
            if write_raises is not None:
                raise write_raises
            return _StubResult([])
        return _StubResult(read_rows)

    def _apply(_event):
        if apply_raises is not None:
            raise apply_raises
        return None

    return types.SimpleNamespace(g=types.SimpleNamespace(query=_query),
                                 query=_query, apply=_apply)


def test_a_write_that_neither_landed_nor_was_refused_is_not_a_landing():
    """The landed token is READ BACK, not inferred from "no exception".

    A dispatched statement that wrote nothing (a stale graph, a rolled-back
    transaction, a redirect) returns without raising, so "did not raise" answers
    a different question from "reached the cache". Deleting the read-back from
    `armed_raw_handle_write` leaves this red while its token still reads
    SUCCEEDED.
    """
    with pytest.raises(RuntimeError, match="neither blocked nor landed"):
        _PROBE_MODULE.armed_raw_handle_write(_stub_projection(read_rows=[[0]]))

    # …and the same stub WITH the node present is the landing it claims to be,
    # so the read-back is not simply refusing everything.
    assert _PROBE_MODULE.armed_raw_handle_write(
        _stub_projection(read_rows=[[1]])) == _PINNED_LANDED


def test_a_broken_probe_is_not_reported_as_a_refusal():
    """Only `DirectCacheWriteRefused` is a verdict; anything else propagates.

    A swallowed exception here would print `refused` for a probe that never
    reached the guard — the same false PASS this section exists to remove.
    """
    with pytest.raises(ConnectionError):
        _PROBE_MODULE.armed_raw_handle_write(
            _stub_projection(read_rows=[[1]],
                             write_raises=ConnectionError("store unreachable")))
