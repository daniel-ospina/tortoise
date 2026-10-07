"""The `docs` job's baseline differ (#7435) — Option 1, with a defined end state.

The owner ruled on #7435: *save today's list of findings, and reject a proposed
change only for problems that are not on that list.* ``tools/docs_lint_baseline.py``
is that rule. What it must NOT become is a no-op: the whole point is that NEW
findings still fail the required `docs` check, so the tests below deliberately
assert the failing case as hard as the passing one.

Four properties are pinned here, each of them a failure this program can cause
rather than prevent:

  * **line-number independence** — a key that moves when an unrelated line is
    inserted above the finding re-introduces the churn the snapshot exists to
    stop;
  * **new findings fail** — a differ that returns 0 for everything is worse than
    no differ, because it looks like a check;
  * **occurrence counts where the TREE determines them, a set where it does not**
    — two markdownlint findings can share one key, so a set would silently absorb
    a change that ADDED a third; a lychee occurrence count is a property of the
    RUN (file population + run cache + remote status), so counting it re-fails
    inherited debt on an unchanged tree (#7542);
  * **fail-closed** — a linter that did not run must never read as "clean", and
    the markdownlint exit code cannot carry that evidence: GNU `xargs` rewrites a
    child's 1-125 to 123, so cli2's 1 (findings) and 2 (fatal) are the same byte.
    The differ reads the REPORT instead (`Linting: N` == the changed-set count,
    and parsed findings == the Summary count).

The workflow wiring is pinned too, because the script is only reachable through
it.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools import docs_lint_baseline as dlb  # noqa: E402

CI = ROOT / ".github" / "workflows" / "ci.yml"
BASELINE = ROOT / "config" / "docs-lint-baseline.json"

MARKDOWNLINT_REPORT = """markdownlint-cli2 v0.23.3 (markdownlint v0.41.1)
Finding: ./docs/x.md ./docs/y.md
Linting: 2 files
Summary: 2 issues in 2 files
docs/x.md:18:1 error MD001/heading-increment Heading levels should only increment by one level at a time [Context: "### Foo"]
docs/y.md:104 error MD032/blanks-around-lists Lists should be surrounded by blank lines [Context: "- a"]
"""

NO_FINDINGS_REPORT = "Linting: 1 file\nSummary: 0 issues in 0 files\n"


def _lychee_document(entries: dict[str, list[dict]], timeouts: dict[str, list[dict]] | None = None) -> dict:
    """A lychee JSON report. `timeouts` is its SECOND failure map.

    lychee 0.24.2 emits BOTH `error_map` and `timeout_map` on every run, and
    both are REQUIRED by `_require_lychee_shape` — a hard failure and a link it
    could not reach in time are two different maps in lychee's own JSON.
    """
    return {
        "total": 10,
        "errors": sum(len(v) for v in entries.values()),
        "error_map": entries,
        "timeout_map": timeouts or {},
    }


def _file_entry(url: str, kind: str = "File not found. Check if file exists and path is correct"):
    return {"url": url, "status": {"text": kind, "details": kind}}


def _timed_out_entry(url: str) -> dict:
    """A lychee `timeout_map` entry — what a link it could not reach looks like."""
    return {"url": url, "status": {"text": "Timeout", "details": "Request timed out"}}


def _write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def _linted(report: str) -> int:
    """The file count the report itself claims to have linted."""
    match = dlb.MARKDOWNLINT_LINTING.search(report)
    return int(match.group("files")) if match else 0


def _check(
    tmp_path: Path,
    markdownlint: str,
    lychee: dict,
    baseline: dict,
    *,
    expected_files: int | None = None,
    repo_root: Path | None = None,
) -> int:
    paths = [
        "--markdownlint-output",
        str(_write(tmp_path, "md.txt", markdownlint)),
        "--markdownlint-expected-files",
        str(expected_files if expected_files is not None else _linted(markdownlint)),
        "--lychee-output",
        str(_write(tmp_path, "lychee.json", json.dumps(lychee))),
        "--baseline",
        str(_write(tmp_path, "baseline.json", json.dumps(baseline))),
        "--repo-root",
        str(repo_root or tmp_path),
    ]
    return dlb.main(["check", *paths])


def _baseline(markdownlint: list[str], lychee: list[str]) -> dict:
    return {
        "schema_version": dlb.BASELINE_SCHEMA,
        # `_check` defaults `repo_root` to `tmp_path`, which is not a git repo, so
        # its policy map is empty. The field is REQUIRED (a snapshot that omits it
        # would silently disable the policy check).
        "linter_config": {},
        "markdownlint": markdownlint,
        "lychee": lychee,
    }


def _md_keys(report: str) -> list[str]:
    return [dlb.markdownlint_key(f) for f in dlb.parse_markdownlint(report)]


def _lychee_keys(document: dict, repo_root: Path = ROOT) -> list[str]:
    return [dlb.lychee_key(f) for f in dlb.parse_lychee(document, repo_root)]


# ── keys survive an unrelated line shift ─────────────────────────────────────


def test_markdownlint_key_is_line_number_independent():
    """Inserting 100 lines above a finding must not change or add a key."""
    shifted = MARKDOWNLINT_REPORT.replace("docs/x.md:18:1", "docs/x.md:118:1").replace(
        "docs/y.md:104", "docs/y.md:204"
    )
    assert set(dlb.parse_markdownlint(MARKDOWNLINT_REPORT)) == set(
        dlb.parse_markdownlint(shifted)
    )


def test_markdownlint_keys_still_distinguish_same_rule_at_different_columns():
    """`(path, rule, column, detail)` — collapsing ANY component merges findings.

    If every MD032 in a file collapsed to one key, fixing one and adding another
    would net to zero and pass. The column plus the offending line's text keeps
    two findings of the same rule distinct — so each case below holds the PATH and
    the RULE fixed and varies ONLY the component under test. An earlier version
    varied the path as well, and then `path|rule` alone kept every key distinct,
    so the column and the detail were pinned by NOTHING: dropping either one
    still passed (#7542 review round 7).
    """
    # Only the COLUMN differs.
    column_only = (
        'docs/x.md:200:5 error MD032/blanks-around-lists '
        'Lists should be surrounded by blank lines [Context: "- b"]\n'
        'docs/x.md:200:9 error MD032/blanks-around-lists '
        'Lists should be surrounded by blank lines [Context: "- b"]\n'
    )
    keys = _md_keys(MARKDOWNLINT_REPORT + column_only)
    assert len(keys) == len(set(keys)) == 4, keys
    # Only the DETAIL (the offending line's text) differs.
    detail_only = (
        'docs/x.md:200:5 error MD032/blanks-around-lists '
        'Lists should be surrounded by blank lines [Context: "- b"]\n'
        'docs/x.md:200:5 error MD032/blanks-around-lists '
        'Lists should be surrounded by blank lines [Context: "- c"]\n'
    )
    keys = _md_keys(MARKDOWNLINT_REPORT + detail_only)
    assert len(keys) == len(set(keys)) == 4, keys


def test_the_digested_policy_name_set_is_pinned_so_a_name_cannot_vanish():
    """A name REMOVED from the set is a suppression route, not a tidy-up.

    `_config_digest` digests a tracked file whose basename is in this set, so
    dropping a name silently stops digesting configs of that shape — and a PR that
    both drops the name and adds `docs/.markdownlint.yaml` with `MD001: false`
    suppresses findings while every digest stays byte-identical. `ci_selection`
    still selects the FULL matrix for that path, so the pin RUNS and is simply
    blind: no other test can see the removal (#7542 review round 7). The set is a
    hand-maintained tuple, so this literal is the only thing that makes a change
    to it visible in the diff and forces the decision to be made out loud.

    It is deliberately a SUPERSET — it includes names cli2 does not read (see the
    #7534 deferral), which is the fail-CLOSED direction: an inert file reds the
    pin, rather than a real one slipping through.
    """
    assert dlb.LINTER_CONFIG_NAMES == frozenset({
        ".markdownlint-cli2.jsonc",
        ".markdownlint-cli2.yaml",
        ".markdownlint-cli2.yml",
        ".markdownlint-cli2.cjs",
        ".markdownlint-cli2.mjs",
        ".markdownlint.jsonc",
        ".markdownlint.json",
        ".markdownlint.yaml",
        ".markdownlint.yml",
        ".markdownlint.cjs",
        ".markdownlint.mjs",
        ".markdownlintrc",
        ".markdownlintignore",
        ".lycheeignore",
        "lychee.toml",
    })


def test_run_lychee_refuses_an_unshaped_document(tmp_path: Path):
    """The generator's fail-closed shape check must be WIRED, not merely defined.

    `test_update_rejects_a_lychee_document_the_check_would_reject` calls
    `_require_lychee_shape` directly, and every `update` test monkeypatches
    `_run_lychee` — so deleting the one call that wires the two together left the
    whole suite green while `update` wrote a snapshot the CHECK refuses, which is
    the exact regression its docstring describes (#7542 review round 7). This
    drives `_run_lychee` itself against a stub binary, which is the call the
    generator actually makes.
    """
    stub = tmp_path / "lychee"
    stub.write_text('#!/bin/sh\nprintf \'{"total": 0}\\n\'\n', encoding="utf-8")
    stub.chmod(0o755)
    doc = tmp_path / "docs" / "x.md"
    doc.parent.mkdir(parents=True)
    doc.write_text("# x\n", encoding="utf-8")
    with pytest.raises(dlb.FailClosed):
        dlb._run_lychee(["docs/x.md"], tmp_path, str(stub))


def test_lychee_target_is_portable_not_an_absolute_checkout_path(tmp_path: Path):
    """A committed snapshot may not carry the machine that generated it."""
    url = f"file://{tmp_path}/docs/04_platform/event-catalog.md"
    assert dlb.normalize_link_target(url, tmp_path) == "docs/04_platform/event-catalog.md"
    assert str(tmp_path) not in dlb.normalize_link_target(url, tmp_path)


def test_lychee_target_keeps_its_fragment(tmp_path: Path):
    """Two dead ANCHORS in one file are two different dead links.

    Dropping the fragment collapses them to one key, so replacing one broken
    anchor with another keeps the occurrence count and passes — a real new dead
    link absorbed. Measured before the fix: both URLs normalised to `docs/b.md`.
    """
    first = dlb.normalize_link_target(f"file://{tmp_path}/docs/b.md#good-anchor", tmp_path)
    second = dlb.normalize_link_target(f"file://{tmp_path}/docs/b.md#bad-anchor", tmp_path)
    assert first == "docs/b.md#good-anchor", first
    assert second == "docs/b.md#bad-anchor", second
    assert first != second


def test_a_link_target_outside_the_checkout_fails_closed(tmp_path: Path):
    """No portable spelling exists, so it must not be written into a snapshot.

    The old fallback returned the ABSOLUTE path it promised to avoid, which
    commits this machine's layout and normalises differently on the next runner
    — so the same finding reads as NEW there and reds the required check.
    """
    with pytest.raises(dlb.FailClosed):
        dlb.normalize_link_target("file:///outside/the/checkout.md", tmp_path)


def test_lychee_key_is_stable_across_the_run_population():
    """The status text is observed, but NOT part of the identity.

    lychee reports a URL it has already seen in a run as `Error (cached)`, so a
    status in the key makes the SAME untouched link key differently when the run
    covers the whole repo (what `update` does) and when it covers only a PR's
    changed files (what CI does). Measured on the committed snapshot:
    `docs/license-notes.md` carries the hashicorp URL TWICE, but the all-file
    `update` recorded it ONCE (and the couchbase URL on that same file twice), so
    a changed-file run's second occurrence was counted as NEW and failed the
    required check — the #7475 failure this snapshot exists to remove.

    The KEY is only half of that fix. The other half is that the lychee compare is
    a SET, not a count: the count is a property of the run (see
    `test_lychee_occurrence_count_is_not_compared_across_populations`).
    """
    cached = ("docs/x.md", "https://example.invalid/a", "Error (cached)")
    fresh = ("docs/x.md", "https://example.invalid/a", "Rejected status code: 429 Too Many Requests")
    assert dlb.lychee_key(cached) == dlb.lychee_key(fresh) == "docs/x.md|https://example.invalid/a"
    # A DIFFERENT target is a different finding; identical targets are one
    # finding, however many times the target appears (the set compare below).
    other = ("docs/x.md", "https://example.invalid/b", "Error (cached)")
    assert dlb.lychee_key(other) != dlb.lychee_key(cached)


def test_lychee_placeholder_target_keeps_the_offending_line_as_its_identity():
    """With NO URL there is nothing else to key on, so the line must be kept.

    lychee reports the placeholder target `error:` when it cannot extract a URL
    at all. `path|target` then carries no distinguishing content, so two broken
    links in one file collapse to one key and editing one to the other keeps the
    occurrence count — a real new dead link absorbed. Measured on the committed
    snapshot: two `fdir/README.md` entries (`/BENCHMARKS.md`, `/documentation.md`)
    both keyed `...|error:`. The status here is the OFFENDING LINE's own text,
    not the run's cache state, so it is safe to key on.
    """
    a = ("docs/x.md", "error:", "Cannot resolve root-relative link '/BENCHMARKS.md'")
    b = ("docs/x.md", "error:", "Cannot resolve root-relative link '/documentation.md'")
    assert dlb.lychee_key(a) != dlb.lychee_key(b)
    assert dlb.lychee_key(a) == "docs/x.md|error:|Cannot resolve root-relative link '/BENCHMARKS.md'"
    # A cache marker is still excluded even on the placeholder path: no key
    # anywhere may vary with the run's file population.
    assert dlb.lychee_key(("docs/x.md", "error:", "Error (cached)")) == "docs/x.md|error:"
    # A real target keeps the status-free key regardless of its status text.
    real = ("docs/x.md", "https://example.invalid/a", "Cannot resolve root-relative link '/x'")
    assert dlb.lychee_key(real) == "docs/x.md|https://example.invalid/a"


# ── the check: known passes, new fails ───────────────────────────────────────


def test_known_findings_pass(tmp_path: Path, capsys):
    """The CI shape: a report + an expected-file count, and NO exit code.

    This is exactly what the workflow feeds the differ — the exit code is not a
    parameter at all, so a baselined finding in a touched file cannot red the
    required check.
    """
    lychee_document = _lychee_document(
        {"docs/x.md": [_file_entry(f"file://{tmp_path}/docs/missing.md")]}
    )
    lychee = [dlb.lychee_key(f) for f in dlb.parse_lychee(lychee_document, tmp_path)]
    assert len(lychee) == 1, "the fixture really produced a lychee key"
    rc = _check(tmp_path, MARKDOWNLINT_REPORT, lychee_document, _baseline(_md_keys(MARKDOWNLINT_REPORT), lychee))
    assert rc == 0, capsys.readouterr().out
    assert "0 new, 3 known (baseline)" in capsys.readouterr().out


def test_a_new_finding_fails_and_prints_the_summary_line(tmp_path: Path, capsys):
    """The property that must not be lost: NEW findings still red the check."""
    # Baseline records only the MD032 finding; the report also carries MD001.
    md_known = [key for key in _md_keys(MARKDOWNLINT_REPORT) if "|MD032|" in key]
    rc = _check(tmp_path, MARKDOWNLINT_REPORT, _lychee_document({}), _baseline(md_known, []))
    out = capsys.readouterr().out
    assert rc == 1, out
    assert "docs-lint baseline: 1 new, 1 known (baseline), 0 in generated files" in out
    assert "MD001" in out


def test_new_lychee_finding_fails(tmp_path: Path, capsys):
    """The link half is diffed too — a new dead link must not slip through."""
    baseline = _baseline(_md_keys(MARKDOWNLINT_REPORT), [])
    assert _check(tmp_path, MARKDOWNLINT_REPORT, _lychee_document({}), baseline) == 0
    lychee = _lychee_document({"docs/x.md": [_file_entry(f"file://{tmp_path}/docs/missing.md")]})
    rc = _check(tmp_path, MARKDOWNLINT_REPORT, lychee, baseline)
    out = capsys.readouterr().out
    assert rc == 1, out
    assert "1 new, 2 known (baseline), 0 in generated files" in out


# ── lychee's SECOND failure map: a timeout is a failure it counts ─────────────


def test_both_lychee_failure_maps_are_parsed():
    """`error_map` AND `timeout_map` — a timeout is not a flavour of no-finding.

    lychee 0.24.2 splits its failures across two maps; its own JSON fixture
    (`lychee-bin/src/formatters/stats/json.rs`) carries `"timeouts": 1` with the
    timeout entry under `timeout_map` and `error_map` holding only the 404. So a
    differ that reads one map cannot see the other map's findings at all.
    """
    document = _lychee_document(
        {"docs/x.md": [_file_entry("https://example.invalid/err")]},
        {"docs/y.md": [_timed_out_entry("https://example.invalid/slow")]},
    )
    assert sorted(_lychee_keys(document)) == [
        "docs/x.md|https://example.invalid/err",
        "docs/y.md|https://example.invalid/slow",
    ]


def test_a_new_timed_out_link_fails_the_check(tmp_path: Path, capsys):
    """The fail-open this closes: a NEW dead link that TIMES OUT must still red.

    lychee's verdict (`ResponseStats::is_success`, lychee-bin/src/formatters/
    stats/response.rs) is `error_map.is_empty() && timeout_map.is_empty()`, and
    `check.rs` maps a false verdict to `ExitCode::LinkCheckFailure` unless
    `--accept-timeouts` is passed — which the `docs` job does not pass. So before
    #7435 the lychee action, running with its default `fail: true`, FAILED the
    required check on a timeout. #7435 sets `fail: false` and makes the differ the
    sole verdict, so reading only `error_map` silently stopped failing on the
    timeout class — and because `update` uses the same parse, the target could
    never be recorded as known either. Measured on the revision before this test:
    `0 new, 0 known (baseline)` and exit 0 for exactly this document.
    """
    document = _lychee_document(
        {}, {"docs/x.md": [_timed_out_entry("https://example.invalid/slow")]}
    )
    rc = _check(tmp_path, NO_FINDINGS_REPORT, document, _baseline([], []))
    out = capsys.readouterr().out
    assert rc == 1, out
    assert "1 new, 0 known (baseline), 0 in generated files" in out
    assert "https://example.invalid/slow" in out


def test_a_timeout_on_a_recorded_target_is_known(tmp_path: Path, capsys):
    """The two maps share ONE identity, so the fix adds no false red of its own.

    A real target's key is `path|target` with no status, so a link the snapshot
    recorded as an ERROR and that reports as a TIMEOUT in this run is the SAME
    finding — the timeout class cannot re-red inherited debt for a link that is
    already on the list (the #7475 property this snapshot exists to keep).
    """
    key = "docs/x.md|https://example.invalid/a"
    document = _lychee_document({}, {"docs/x.md": [_timed_out_entry("https://example.invalid/a")]})
    rc = _check(tmp_path, NO_FINDINGS_REPORT, document, _baseline([], [key]))
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "0 new, 1 known (baseline)" in out


# ── lychee occurrence counts are a RUN property, so lychee is a set ───────────


def test_lychee_occurrence_count_is_not_compared_across_populations(tmp_path: Path, capsys):
    """The #7542 P1: a run's repeat of a recorded target must NOT read as NEW.

    `update` lints ALL tracked markdown while CI lints only the changed files, and
    a remote link's entry count moves with the run (cache + transient status), so
    the count a baseline records is NOT reproducible by the run that reads it.
    Measured on the committed snapshot: `docs/license-notes.md` carries the
    hashicorp URL twice, the all-file `update` recorded it once, and a
    changed-file run reports it twice — `1 new, 4 known` and a red REQUIRED `docs`
    on an unrelated PR. The lychee compare is a key SET, so the repeat is KNOWN.
    """
    key = "docs/x.md|https://example.invalid/a"
    twice = _lychee_document(
        {
            "docs/x.md": [
                _file_entry("https://example.invalid/a"),
                _file_entry("https://example.invalid/a"),
            ]
        }
    )
    assert _lychee_keys(twice) == [key, key], "the fixture really repeats one key"
    # Baseline records it ONCE (what the all-file update wrote); the run reports it
    # TWICE (what a changed-file run writes) — still zero new.
    rc = _check(tmp_path, NO_FINDINGS_REPORT, twice, _baseline([], [key]))
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "0 new, 2 known (baseline)" in out


def test_lychee_same_link_twice_with_a_doubled_baseline_is_clean(tmp_path: Path, capsys):
    """The same population on both sides: a doubled baseline over a doubled run.

    This is the direction that was already green on the couchbase URL, pinned so a
    future re-baseline cannot silently make it the ONLY green shape.
    """
    key = "docs/x.md|https://example.invalid/a"
    twice = _lychee_document(
        {"docs/x.md": [_file_entry("https://example.invalid/a")] * 2}
    )
    rc = _check(tmp_path, NO_FINDINGS_REPORT, twice, _baseline([], [key, key]))
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "0 new, 2 known (baseline)" in out


def test_a_genuinely_new_lychee_target_is_still_reported_exactly_once(tmp_path: Path, capsys):
    """Set membership is not a no-op: an unseen target still reds the check.

    The set compare narrows the check to targets not on the list; it must not stop
    reporting a target that IS new. The run repeats a KNOWN target and adds one
    NEW one, so exactly one finding is new — the repeat is suppressed, the new
    target is not.
    """
    known = "docs/x.md|https://example.invalid/known"
    document = _lychee_document(
        {
            "docs/x.md": [
                _file_entry("https://example.invalid/known"),
                _file_entry("https://example.invalid/known"),
                _file_entry("https://example.invalid/brand-new"),
            ]
        }
    )
    rc = _check(tmp_path, NO_FINDINGS_REPORT, document, _baseline([], [known]))
    out = capsys.readouterr().out
    assert rc == 1, out
    assert "1 new, 2 known (baseline)" in out
    assert "brand-new" in out
    assert "example.invalid/known" not in out.split("NEW findings")[1]


# ── generated files name the generator ───────────────────────────────────────


def test_generated_file_finding_reports_the_generator(tmp_path: Path, capsys):
    """#7475: the failure named the rendered `.md`; the fix target is the generator."""
    assert dlb.GENERATED_DOCS["docs/product/mcp-sdk-surface.md"] == "tools/surface_manifest.py"
    report = (
        "Linting: 1 file\nSummary: 1 issue in 1 file\n"
        "docs/product/mcp-sdk-surface.md:96 error MD032/blanks-around-lists Lists should be "
        'surrounded by blank lines [Context: "- x"]\n'
    )
    rc = _check(tmp_path, report, _lychee_document({}), _baseline([], []))
    out = capsys.readouterr().out
    assert rc == 1, out
    assert "1 new, 0 known (baseline), 1 in generated files" in out
    assert "FIX TARGET: tools/surface_manifest.py" in out
    assert "do NOT hand-edit" in out


def test_known_generated_finding_is_counted_as_generated_but_passes(tmp_path: Path, capsys):
    report = (
        "Linting: 1 file\nSummary: 1 issue in 1 file\n"
        'docs/product/mcp-sdk-surface.md:96 error MD032/blanks-around-lists Lists should be '
        'surrounded by blank lines [Context: "- x"]\n'
    )
    rc = _check(tmp_path, report, _lychee_document({}), _baseline(_md_keys(report), []))
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "0 new, 1 known (baseline), 1 in generated files" in out


@pytest.mark.parametrize(
    ("document", "generator"),
    [
        ("docs/product/mcp-sdk-surface.md", "tools/surface_manifest.py"),
        ("docs/product/bridge-table.md", "tools/bridge_table.py"),
        ("docs/product/mcp-rename-table.md", "tools/mcp_rename_table.py"),
        ("docs/product/sdk-surface-declaration.md", "tools/sdk_surface.py"),
    ],
)
def test_generated_detection_covers_the_rendered_docs(document: str, generator: str):
    assert dlb.generated_target(document, ROOT) == generator


def test_every_mapping_names_a_generator_that_actually_renders_the_doc():
    """A mapping is only real if the doc says so AND the generator writes it.

    `docs/product/beta-sdk-surface.md` was wrongly mapped to `tools/bridge_table.py`,
    which only READS it (it is a hand-authored, owner-approved doc) — so a finding
    there would have printed "edit the generator", which cannot fix it. Neither
    half of this check was asserted before, so a wrong mapping was invisible.
    """
    assert dlb.GENERATED_DOCS
    for document, generator in dlb.GENERATED_DOCS.items():
        head = "\n".join(
            (ROOT / document).read_text(encoding="utf-8").splitlines()[: dlb.GENERATED_HEAD_LINES]
        )
        assert any(marker.search(head) for marker in dlb.GENERATED_MARKERS), (
            f"{document} carries no generated marker, so it must not be in GENERATED_DOCS"
        )
        source = (ROOT / generator).read_text(encoding="utf-8")
        name = Path(document).name
        assert name in source, (
            f"{generator} never references {name} — it does not render it"
        )
        # The reference must be a WRITE, not a read. `name in source` alone also
        # matches a `read_text()` reference, which is exactly the #7475
        # misdirection: `docs/product/beta-sdk-surface.md` is read by
        # `bridge_table.py` and a finding there must NOT send the author to it.
        # Resolve the identifier the doc path is bound to, then require that
        # identifier to be written — directly, or through an argparse option
        # defaulted to it (`args.doc.write_text(...)`, default `DOC_FILE`).
        binding = re.search(
            rf"^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*[^\n]*[\"']{re.escape(name)}[\"']",
            source,
            re.M,
        )
        assert binding is not None, (
            f"{generator} references {name} but binds it to no identifier, so it "
            "cannot be shown to WRITE it"
        )
        ident = binding.group(1)
        written = re.search(rf"\b{re.escape(ident)}\.write_text\s*\(", source) is not None
        if not written:
            for opt in re.finditer(
                rf"add_argument\(\s*[\"']--([A-Za-z0-9-]+)[\"'][^)]*default\s*=\s*{re.escape(ident)}\b",
                source,
                re.S,
            ):
                dest = opt.group(1).replace("-", "_")
                if re.search(rf"\bargs\.{re.escape(dest)}\.write_text\s*\(", source):
                    written = True
                    break
        assert written, (
            f"{generator} binds {name} to `{ident}` but never writes it — a mapping "
            "to a generator that only READS the doc sends the author to a fix that "
            "cannot fix it (#7475)"
        )


@pytest.mark.parametrize("document", ["MEMORY.md", "docs/00_index.md"])
def test_marker_sniff_does_not_flag_ordinary_docs_that_mention_a_generator(document: str):
    """A substring sniff blamed a generator for a finding in an unrelated doc.

    `MEMORY.md` says something "is GENERATED ON DEMAND", and `docs/00_index.md`
    carries an index row quoting a generated file's marker. Neither file is
    generated, and the advisory `FIX TARGET:` line must not tell an author to
    edit a generator.
    """
    assert document not in dlb.GENERATED_DOCS
    assert dlb.generated_target(document, ROOT) is None


# ── fail closed: "the linter did not run" is never "clean" ───────────────────


def test_missing_markdownlint_report_fails_closed(tmp_path: Path, capsys):
    rc = dlb.main([
        "check",
        "--markdownlint-output", str(tmp_path / "absent.txt"),
        "--markdownlint-expected-files", "1",
        "--lychee-output", str(_write(tmp_path, "lychee.json", json.dumps(_lychee_document({})))),
        "--baseline", str(_write(tmp_path, "b.json", json.dumps(_baseline([], [])))),
        "--repo-root", str(tmp_path),
    ])
    assert rc == 2
    assert "did not run" in capsys.readouterr().err


@pytest.mark.parametrize(
    "report",
    [
        "",  # nothing at all
        "markdownlint-cli2 v0.23.3 (markdownlint v0.41.1)\n",  # banner only
        "Linting: 0 files\n",  # no Summary
    ],
)
def test_markdownlint_report_without_a_real_run_fails_closed(tmp_path: Path, report: str, capsys):
    rc = _check(tmp_path, report, _lychee_document({}), _baseline([], []))
    assert rc == 2
    assert "did not run" in capsys.readouterr().err


def test_report_that_linted_nothing_fails_closed(tmp_path: Path, capsys):
    """`Linting: 0 files` + a clean Summary is the silent-green trap.

    It parses as "no findings" — but the run judged nothing, so a baselined
    finding in the changed file would read as absent. The expected count catches
    it; the exit code cannot (xargs rewrites it, and a clean cli2 run exits 0).
    """
    report = "Linting: 0 files\nSummary: 0 issues in 0 files\n"
    rc = _check(tmp_path, report, _lychee_document({}), _baseline([], []), expected_files=2)
    assert rc == 2
    assert "judged 0 file(s) but the changed set holds 2" in capsys.readouterr().err


def test_short_linting_count_fails_closed(tmp_path: Path, capsys):
    """Fewer files judged than the changed set held is not a lint verdict."""
    rc = _check(
        tmp_path, NO_FINDINGS_REPORT, _lychee_document({}), _baseline([], []), expected_files=3
    )
    assert rc == 2
    assert "judged 1 file(s) but the changed set holds 3" in capsys.readouterr().err


def test_unparseable_lychee_report_fails_closed(tmp_path: Path, capsys):
    rc = dlb.main([
        "check",
        "--markdownlint-output", str(_write(tmp_path, "md.txt", MARKDOWNLINT_REPORT)),
        "--markdownlint-expected-files", "2",
        "--lychee-output", str(_write(tmp_path, "lychee.json", "{not json")),
        "--baseline", str(_write(tmp_path, "b.json", json.dumps(_baseline(_md_keys(MARKDOWNLINT_REPORT), [])))),
        "--repo-root", str(tmp_path),
    ])
    assert rc == 2
    assert "unparseable" in capsys.readouterr().err


def test_baseline_whose_counts_disagree_with_its_lists_fails_closed(tmp_path: Path, capsys):
    """A truncated or inflated snapshot mis-scopes every later diff."""
    baseline = _baseline([], [])
    baseline["snapshot"] = {"counts": {"markdownlint": 5, "lychee": 0}}
    rc = _check(tmp_path, MARKDOWNLINT_REPORT, _lychee_document({}), baseline)
    assert rc == 2
    assert "snapshot is corrupt" in capsys.readouterr().err


def test_malformed_baseline_fails_closed(tmp_path: Path, capsys):
    rc = dlb.main([
        "check",
        "--markdownlint-output", str(_write(tmp_path, "md.txt", MARKDOWNLINT_REPORT)),
        "--markdownlint-expected-files", "2",
        "--lychee-output", str(_write(tmp_path, "lychee.json", json.dumps(_lychee_document({})))),
        "--baseline", str(_write(tmp_path, "b.json", json.dumps({"schema_version": 99}))),
        "--repo-root", str(tmp_path),
    ])
    assert rc == 2
    assert "schema_version" in capsys.readouterr().err


# ── parsing edge cases that would otherwise fail open ────────────────────────


def test_a_duplicate_occurrence_of_a_recorded_key_is_still_new(tmp_path: Path, capsys):
    """Set semantics would absorb it; occurrence counts must not.

    MD032 can fire twice with an identical column and offending line text on two
    different lines. If the snapshot were a set, a change that ADDED a third
    identical finding would find its key already present and pass — the one case
    where "new findings still fail" would quietly stop being true.
    """
    line = (
        "docs/x.md:{line} error MD032/blanks-around-lists Lists should be surrounded "
        'by blank lines [Context: "- a"]\n'
    )
    report_one = "Linting: 1 file\nSummary: 1 issue in 1 file\n" + line.format(line=10)
    report_two = (
        "Linting: 1 file\nSummary: 2 issues in 1 file\n"
        + line.format(line=10)
        + line.format(line=50)
    )
    one_key = _md_keys(report_one)
    two_keys = _md_keys(report_two)
    assert set(one_key) == set(two_keys) and len(two_keys) == 2, (
        "the fixture's two findings share one key"
    )

    assert _check(tmp_path, report_one, _lychee_document({}), _baseline(one_key, [])) == 0
    rc = _check(tmp_path, report_two, _lychee_document({}), _baseline(one_key, []))
    out = capsys.readouterr().out
    assert rc == 1, out
    assert "1 new, 1 known (baseline)" in out


def test_multi_segment_rule_alias_parses(tmp_path: Path, capsys):
    """`MD025/single-title/single-h1` — a second `/` in the alias must parse.

    The first revision's alias regex stopped at the first `/`, so all 89 MD025
    findings were silently dropped — and a dropped finding is a finding the diff
    cannot see, i.e. a fail-OPEN hole.
    """
    report = (
        "Linting: 1 file\nSummary: 1 issue in 1 file\n"
        "docs/x.md:153 error MD025/single-title/single-h1 Multiple top-level headings "
        'in the same document [Context: "# Two"]\n'
    )
    findings = dlb.parse_markdownlint(report)
    assert len(findings) == 1
    assert findings[0][1] == "MD025"
    rc = _check(tmp_path, report, _lychee_document({}), _baseline([], []))
    assert rc == 1, capsys.readouterr().out


def test_parsed_count_must_match_the_reported_count(tmp_path: Path, capsys):
    """An unparsed finding line is a fail-OPEN hole; the count is the guard."""
    report = (
        "Linting: 1 file\nSummary: 2 issues in 1 file\n"
        "docs/x.md:10 error MD032/blanks-around-lists Lists should be surrounded "
        'by blank lines [Context: "- a"]\n'
    )
    rc = _check(tmp_path, report, _lychee_document({}), _baseline([], []))
    assert rc == 2
    assert "would be missed" in capsys.readouterr().err


# ── update writes a snapshot the check can consume ───────────────────────────


def test_update_writes_a_valid_snapshot(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(ROOT)
    monkeypatch.setattr(dlb, "_require_lychee_binary", lambda binary: None)
    monkeypatch.setattr(dlb, "_run_markdownlint", lambda files, root: (1, MARKDOWNLINT_REPORT))
    monkeypatch.setattr(
        dlb,
        "_run_lychee",
        lambda files, root, binary: _lychee_document(
            {"docs/x.md": [_file_entry(f"file://{root}/docs/missing.md")]}
        ),
    )
    files = _write(tmp_path, "files.txt", "docs/x.md\ndocs/y.md\n")
    out = tmp_path / "baseline.json"
    rc = dlb.main(["update", "--files-from", str(files), "--baseline", str(out)])
    assert rc == 0
    written = json.loads(out.read_text(encoding="utf-8"))
    assert written["schema_version"] == dlb.BASELINE_SCHEMA
    assert written["snapshot"]["counts"] == {
        "markdownlint": len(written["markdownlint"]),
        "lychee": len(written["lychee"]),
    }
    assert written["end_state"]["issue"] == 7534
    assert "CEILING, never a floor" in written["end_state"]["rule"]
    # The written snapshot is consumable: re-diff the same findings → clean.
    # `repo_root=ROOT` matches how `update` normalized the `file://` target.
    rc = _check(
        tmp_path,
        MARKDOWNLINT_REPORT,
        _lychee_document({"docs/x.md": [_file_entry(f"file://{ROOT}/docs/missing.md")]}),
        written,
        expected_files=_linted(MARKDOWNLINT_REPORT),
        repo_root=ROOT,
    )
    assert rc == 0


def test_update_deduplicates_the_lychee_half(tmp_path: Path, monkeypatch):
    """The producer writes a SET for lychee — a repeat is one entry, not two.

    The check compares membership (see
    `test_lychee_occurrence_count_is_not_compared_across_populations`), so the
    producer must not advertise a count. Deduplicating here is what makes the
    committed artifact and the live comparison one normalisation rather than two.
    """
    monkeypatch.chdir(ROOT)
    monkeypatch.setattr(dlb, "_require_lychee_binary", lambda binary: None)
    monkeypatch.setattr(dlb, "_run_markdownlint", lambda files, root: (0, NO_FINDINGS_REPORT))
    monkeypatch.setattr(
        dlb,
        "_run_lychee",
        lambda files, root, binary: _lychee_document(
            {
                "docs/x.md": [
                    _file_entry("https://example.invalid/a"),
                    _file_entry("https://example.invalid/a"),
                ]
            }
        ),
    )
    files = _write(tmp_path, "files.txt", "docs/x.md\n")
    out = tmp_path / "baseline.json"
    assert dlb.main(["update", "--files-from", str(files), "--baseline", str(out)]) == 0
    written = json.loads(out.read_text(encoding="utf-8"))
    assert written["lychee"] == ["docs/x.md|https://example.invalid/a"]
    assert written["snapshot"]["counts"]["lychee"] == 1


def _docs_steps() -> list[dict]:
    workflow = yaml.safe_load(CI.read_text(encoding="utf-8"))
    return workflow["jobs"]["docs"]["steps"]


def _by_name(name: str) -> dict:
    for step in _docs_steps():
        if step.get("name") == name:
            return step
    raise AssertionError(f"no docs-job step named {name!r} (#7435)")


@pytest.mark.parametrize(
    ("name", "gate"),
    [
        (
            "Docs lint baseline (changed files)",
            "${{ !inputs.main_health && steps.changed.outputs.count != '0' }}",
        ),
        (
            "Docs lint baseline (main health)",
            "${{ inputs.main_health && steps.changed_mh.outputs.count != '0' }}",
        ),
    ],
)
def test_differ_step_exists_and_is_gated_with_the_linters(name: str, gate: str):
    step = _by_name(name)
    assert "tools/docs_lint_baseline.py check" in step["run"]
    assert "--baseline" in step["run"]
    assert step["if"] == gate
    # The count is the fail-closed evidence, and it must arrive as an env VALUE,
    # never interpolated into the shell body (the #4449 discipline).
    assert "--markdownlint-expected-files" in step["run"]
    assert "${{" not in step["run"]
    assert "EXPECTED_MD_FILES" in step["run"]
    assert "${{" in step["env"]["EXPECTED_MD_FILES"]


@pytest.mark.parametrize(
    "name",
    ["Get changed markdown files", "Get changed markdown files (main health)"],
)
def test_glob_guard_actually_fails_closed(name: str):
    """EXECUTE the guard — a substring assertion let a missing `exit 1` pass.

    `assert "exit 1" in run` was satisfied by the step's unrelated BASE_SHA
    guard, so deleting the glob guard's `exit 1` kept every test green while the
    step exited 0 and linted the glob-expanded file (and dropping the regex's `$`
    anchor was similarly invisible). Both guards must be an ALLOWLIST, because
    globby/micromatch expand extglob (`@(README).md`), which contains none of
    `* ? [ ] { }`. So run the extracted guard against real inputs.
    """
    run = _by_name(name)["run"]
    match = re.search(
        r"(if grep -qvE '\^\\\./\[A-Za-z0-9\._@/-\]\+\$'[^\n]*\n(?:.*\n)*?\s*fi\n)", run
    )
    assert match is not None, "the allowlist guard is missing from this step"
    guard = match.group(1)
    listing = "pr-md.txt" if name == "Get changed markdown files" else "changed-md.txt"
    with tempfile.TemporaryDirectory() as td:
        target = Path(td) / listing

        def run_with(contents: str) -> int:
            target.write_text(contents, encoding="utf-8")
            script = f'set -euo pipefail\nRUNNER_TEMP="$1"\n{guard}'
            return subprocess.run(
                ["bash", "-c", script, "bash", td], capture_output=True, text=True
            ).returncode

        assert run_with("./docs/normal-file.md\n") == 0
        assert run_with("./node_modules/@babel/README.md\n") == 0
        for bad in (
            "./docs/@(README).md\n",
            "./docs/a[1].md\n",
            "./docs/a b.md\n",
            "./docs/a+b.md\n",
        ):
            assert run_with(bad) != 0, f"{bad!r} must fail the job"


def test_update_refuses_a_lychee_binary_that_is_not_the_pinned_version(tmp_path: Path):
    """The snapshot stamps `lychee <LYCHEE_PIN>`; verify the binary IS that version.

    The key can carry status text (the `error:` placeholder) and status text is
    version-dependent, so an UNPINNED producer writes an unreproducible snapshot
    while the metadata claims the pinned version.
    """
    good = tmp_path / "lychee-good"
    good.write_text(f"#!/bin/sh\necho 'lychee {dlb.LYCHEE_PIN}'\n", encoding="utf-8")
    bad = tmp_path / "lychee-bad"
    bad.write_text("#!/bin/sh\necho 'lychee 0.0.1'\n", encoding="utf-8")
    for script in (good, bad):
        script.chmod(0o755)
    dlb._require_lychee_binary(str(good))
    with pytest.raises(dlb.FailClosed):
        dlb._require_lychee_binary(str(bad))
    with pytest.raises(dlb.FailClosed):
        dlb._require_lychee_binary(str(tmp_path / "absent"))


def test_update_refuses_a_snapshot_that_did_not_lint_the_whole_population(
    tmp_path: Path, monkeypatch
):
    """`Linting: N` must equal the population, exactly as the CHECK requires.

    Without it a run that silently skipped files writes an INCOMPLETE snapshot,
    and an incomplete snapshot reads as a complete one — every skipped finding
    stays "not on the list" and reds later PRs.
    """
    monkeypatch.chdir(ROOT)
    monkeypatch.setattr(dlb, "_require_lychee_binary", lambda binary: None)
    monkeypatch.setattr(
        dlb,
        "_run_markdownlint",
        lambda files, root: (1, "Linting: 1 file\nSummary: 0 issues in 0 files\n"),
    )
    files = _write(tmp_path, "files.txt", "docs/x.md\ndocs/y.md\n")
    out = tmp_path / "baseline.json"
    rc = dlb.main(["update", "--files-from", str(files), "--baseline", str(out)])
    assert rc == 2
    assert not out.exists()


def test_lychee_non_dict_status_fails_closed_instead_of_crashing(tmp_path: Path):
    """A non-object `status` must red via an empty key, not raise AttributeError."""
    document = {
        "total": 1,
        "error_map": {"docs/x.md": [{"url": "https://example.invalid/a", "status": "boom"}]},
    }
    findings = dlb.parse_lychee(document, tmp_path)
    assert len(findings) == 1
    assert findings[0][2] == "error"


def test_check_fails_closed_when_the_linter_policy_changed(tmp_path: Path):
    """A rule turned off is a SUPPRESSED finding, not a fixed one.

    The differ compares findings, so the policy that produced them is part of the
    comparison's validity: a same-PR `.markdownlint*` edit ("MD001": false) or a
    `.lycheeignore` path makes the linter report FEWER findings, which the differ
    reads as `0 new`. `run_check` refuses when the current policy files differ
    from what the snapshot recorded — and refuses when the field is absent at all,
    since an omitted map would disable the check.
    """
    baseline = _baseline(_md_keys(MARKDOWNLINT_REPORT), [])
    baseline["linter_config"] = {".markdownlint-cli2.jsonc": "0" * 64}
    assert _check(tmp_path, MARKDOWNLINT_REPORT, _lychee_document({}), baseline) == 2
    # A policy that matches the checkout passes.
    baseline["linter_config"] = dlb._config_digest(tmp_path)
    assert _check(tmp_path, MARKDOWNLINT_REPORT, _lychee_document({}), baseline) == 0
    # The field is REQUIRED: deleting it must not silently disable the check.
    del baseline["linter_config"]
    assert _check(tmp_path, MARKDOWNLINT_REPORT, _lychee_document({}), baseline) == 2


def test_policy_digest_covers_configs_at_any_depth(tmp_path: Path):
    """cli2 reads `.markdownlint*` from ANY directory; a nested config overrides.

    Digesting only the repo-root file left a same-PR `docs/.markdownlint-cli2.jsonc`
    (or a root `.markdownlint.json`) free to turn a rule off while the pinned file
    stayed untouched. Every TRACKED policy basename is digested, so ADDING one is
    a policy change.
    """
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)

    git("init", "-q")
    git("config", "user.email", "t@example.invalid")
    git("config", "user.name", "t")
    (repo / ".markdownlint-cli2.jsonc").write_text('{"config": {}}\n', encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "base")
    root_only = dlb._config_digest(repo)
    assert set(root_only) == {".markdownlint-cli2.jsonc"}
    (repo / "docs" / ".markdownlint.json").write_text('{"MD001": false}\n', encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "nested override")
    nested = dlb._config_digest(repo)
    assert "docs/.markdownlint.json" in nested
    assert nested != root_only


def test_policy_digest_refuses_a_config_that_loads_policy_elsewhere(tmp_path: Path):
    """A content digest pins what it READS; `extends` reads a second file.

    Digesting the config's own bytes is not enough when the config names another
    file as the source of its rules: measured on `.markdownlint-cli2.jsonc` with
    `"config": {"extends": "./lintcfg/relaxed.json"}`, editing ONLY
    `relaxed.json` to `{"MD001": false}` left `_config_digest` byte-identical
    while cli2 reported `0 issues` — a rule turned off with the guard silent, in
    a LATER change than the one that added the reference.

    The keys are read from the PARSED config, because a text pattern is evaded by
    a spelling the parser still honours: a JSONC backslash-u escape and a YAML
    flow mapping each turn the rule off while an `extends` search finds nothing.
    A programmatic `.cjs`/`.mjs` config is the same hole one level down — it
    executes, so it can load an untracked module — and the keys that load a
    module are cli2's own (`markdownItPlugins`/`modulePaths`/`outputFormatters`)
    as well as markdownlint's (`extends`/`customRules`). All of them are refused
    rather than followed: an `extends` may name a package, not only a repo path.
    """
    repo = tmp_path / "repo"
    (repo / "lintcfg").mkdir(parents=True)

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)

    git("init", "-q")
    git("config", "user.email", "t@example.invalid")
    git("config", "user.name", "t")
    # A self-contained JSONC config — comments and all — is accepted whole.
    config = repo / ".markdownlint-cli2.jsonc"
    config.write_text(
        '{\n  // MD013 is off for prose documents\n  "config": {"MD013": false}\n}\n',
        encoding="utf-8",
    )
    git("add", "-A")
    git("commit", "-qm", "base")
    assert set(dlb._config_digest(repo)) == {".markdownlint-cli2.jsonc"}

    # Every form that DELEGATES its policy is refused, including the two spelled
    # so that a regex over the config's text would find nothing.
    backslash = chr(92)
    delegating = (
        '{"config": {"extends": "./lintcfg/relaxed.json"}}\n',
        '{"customRules": ["./rules/extra.mjs"]}\n',
        '{"markdownItPlugins": [["./plugins/x.mjs", {}]]}\n',
        '{"modulePaths": ["./rules/"]}\n',
        '{"outputFormatters": [["./fmt.mjs", {}]]}\n',
        '{"config": {"' + backslash + 'u0065xtends": "./lintcfg/relaxed.json"}}\n',
    )
    for body in delegating:
        config.write_text(body, encoding="utf-8")
        git("commit", "-qam", "delegates")
        with pytest.raises(dlb.FailClosed):
            dlb._config_digest(repo)

    # ...and so is the same delegation spelled as YAML.
    config.unlink()
    yaml_config = repo / ".markdownlint-cli2.yaml"
    yaml_config.write_text("config: {extends: ./lintcfg/relaxed.json}\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "yaml flow")
    with pytest.raises(dlb.FailClosed):
        dlb._config_digest(repo)

    # A programmatic config EXECUTES, so its bytes do not describe its policy.
    yaml_config.unlink()
    (repo / ".markdownlint-cli2.cjs").write_text(
        "module.exports = require('./lintcfg/relaxed.json');\n", encoding="utf-8"
    )
    git("add", "-A")
    git("commit", "-qm", "programmatic")
    with pytest.raises(dlb.FailClosed):
        dlb._config_digest(repo)

    # A config that cannot be parsed cannot be attested either — fail closed.
    (repo / ".markdownlint-cli2.cjs").unlink()
    (repo / ".markdownlint-cli2.jsonc").write_text('{"config": ', encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "unparseable")
    with pytest.raises(dlb.FailClosed):
        dlb._config_digest(repo)


def test_policy_digest_covers_the_lychee_carrier_files(tmp_path: Path):
    """lychee reads its policy from `Cargo.toml`, `pyproject.toml` and `package.json`.

    Only `lychee.toml` was digested, so a same-PR `[tool.lychee] exclude = [...]`
    (or the equivalent in `package.json`/`Cargo.toml`) changed the link half's
    policy while every digested file stayed byte-identical — measured as the
    differ reporting `0 new` against a genuinely new dead link. Digesting the
    whole carrier would red the check on every dependency bump, so only the
    section is digested; a MISSING section is a fixed marker, so adding one is a
    change too.
    """
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)

    git("init", "-q")
    git("config", "user.email", "t@example.invalid")
    git("config", "user.name", "t")
    pyproject = repo / "pyproject.toml"
    pyproject.write_text('[project]\nname = "demo"\nversion = "1.0"\n', encoding="utf-8")
    package = repo / "package.json"
    package.write_text('{"name": "demo"}\n', encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "base")
    before = dlb._config_digest(repo)
    assert set(before) == {"pyproject.toml", "package.json"}
    # An UNRELATED edit (a dependency bump) is NOT a policy change.
    pyproject.write_text('[project]\nname = "demo"\nversion = "2.0"\n', encoding="utf-8")
    git("commit", "-qam", "bump")
    assert dlb._config_digest(repo) == before
    # ADDING the lychee section IS a policy change.
    pyproject.write_text(
        '[project]\nname = "demo"\nversion = "2.0"\n\n'
        '[tool.lychee]\nexclude = ["brand-new-missing.md"]\n',
        encoding="utf-8",
    )
    git("commit", "-qam", "lychee policy")
    assert dlb._config_digest(repo)["pyproject.toml"] != before["pyproject.toml"]
    # `package.json` carries it too.
    package.write_text(
        '{"name": "demo", "lychee": {"exclude": ["^https://"]}}\n', encoding="utf-8"
    )
    git("commit", "-qam", "json lychee policy")
    assert dlb._config_digest(repo)["package.json"] != before["package.json"]
    # `Cargo.toml` carries TWO sections lychee reads: its loader prefers
    # `[package.metadata.lychee]` and FALLS BACK to `[workspace.metadata.lychee]`,
    # so digesting only the first would let a workspace-level `exclude` change the
    # link policy with the map unmoved (#7542 review round 6) — latent while no
    # root `Cargo.toml` is tracked, which is exactly why it needed a test.
    cargo = repo / "Cargo.toml"
    cargo.write_text(
        '[package]\nname = "demo"\nversion = "0.1.0"\n\n'
        '[workspace.metadata.lychee]\nexclude = ["^https://example.invalid"]\n',
        encoding="utf-8",
    )
    git("add", "-A")
    git("commit", "-qm", "cargo workspace lychee policy")
    with_ws = dlb._config_digest(repo)["Cargo.toml"]
    # An unrelated edit elsewhere in the carrier is NOT a policy change.
    cargo.write_text(
        '[package]\nname = "demo"\nversion = "0.2.0"\n\n'
        '[workspace.metadata.lychee]\nexclude = ["^https://example.invalid"]\n',
        encoding="utf-8",
    )
    git("commit", "-qam", "cargo bump")
    assert dlb._config_digest(repo)["Cargo.toml"] == with_ws
    # MOVING the policy into the OTHER section IS one, even though the section's
    # text is byte-identical — the two sections are digested separately.
    cargo.write_text(
        '[package]\nname = "demo"\nversion = "0.2.0"\n\n'
        '[package.metadata.lychee]\nexclude = ["^https://example.invalid"]\n',
        encoding="utf-8",
    )
    git("commit", "-qam", "cargo package lychee policy")
    assert dlb._config_digest(repo)["Cargo.toml"] != with_ws


def _extract_suppression_guard(run: str) -> str:
    """The suppression-directive guard from a step's `run` block.

    The guard may be ONE `if` (a pipe: `git diff … | grep -qE …; then … fi`) or
    TWO sibling `if`s (write the added-markdown diff to a file, then grep THAT
    file). The pipe form is what this PR first shipped, and it was BLIND on a
    large diff: `grep -q` exits at its first match, and under `set -o pipefail`
    the SIGPIPE that sends `git diff` made the whole pipeline non-zero, so the
    guard was skipped. Both shapes are extracted so the EXECUTED assertions
    below decide whether the guard works, not which idiom it is written in.
    """
    lines = run.splitlines(keepends=True)

    def indent(line: str) -> int:
        return len(line) - len(line.lstrip())

    start = next(
        (
            i
            for i, line in enumerate(lines)
            if re.match(
                r"\s*if (?:! )?git diff (?:--no-renames|--find-renames) -U0 .*-- '\*\.md'", line
            )
        ),
        None,
    )
    assert start is not None, "the suppression-directive guard is missing from this step"
    lead = indent(lines[start])
    block: list[str] = []
    for line in lines[start:]:
        block.append(line)
        if line.strip() == "fi" and indent(line) == lead:
            break
    # The file form is TWO sibling `if`s: after the first `fi`, a second `if`
    # greps the SAME temp file. Extend only when that sibling references the very
    # file the first block wrote — the step's later glob-allowlist guard also
    # greps a `$RUNNER_TEMP/...` file, and matching it would smuggle an unrelated
    # `exit 1` into the guard under test.
    marker = re.search(r"\$RUNNER_TEMP/(\w+)", "".join(block))
    if marker:
        sibling = f"$RUNNER_TEMP/{marker.group(1)}"
        nxt = next((raw for raw in lines[start + len(block) :] if raw.strip()), "")
        if re.match(r"\s*if grep ", nxt) and sibling in nxt:
            for line in lines[start + len(block) :]:
                block.append(line)
                if line.strip() == "fi" and indent(line) == lead:
                    break
    return "".join(block)


def test_an_added_suppression_directive_is_rejected(tmp_path: Path):
    """An ADDED `markdownlint-disable` must fail the job — it hides a new finding.

    cli2 reports 0 issues for a suppressed finding, so the differ sees `0 new` and
    the required check passes. A PRE-EXISTING directive is baselined debt; only an
    ADDED one is rejected. This EXECUTES the extracted guard against real git
    diffs, because a presence-only assertion cannot tell a wired guard from one
    whose `exit 1` was deleted.
    """
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)

    def git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args], cwd=repo, check=True, capture_output=True, text=True
        )

    git("init", "-q")
    git("config", "user.email", "t@example.invalid")
    git("config", "user.name", "t")
    doc = repo / "docs" / "a.md"
    doc.write_text("# a\n\n### b\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "base")
    base = git("rev-parse", "HEAD").stdout.strip()

    def run_guard(name: str, env: dict[str, str]) -> int:
        guard = _extract_suppression_guard(_by_name(name)["run"])
        # The guard writes the added-markdown diff to `$RUNNER_TEMP` and greps the
        # FILE (never a pipe — `grep -q` mid-pipe under `set -o pipefail` returned
        # non-zero and skipped the guard on a large diff).
        runner_temp = tmp_path / "runner"
        runner_temp.mkdir(exist_ok=True)
        return subprocess.run(
            ["bash", "-c", f"set -euo pipefail\n{guard}"],
            cwd=repo,
            capture_output=True,
            text=True,
            env={**os.environ, "RUNNER_TEMP": str(runner_temp), **env},
        ).returncode

    pr_step = "Get changed markdown files"
    mh_step = "Get changed markdown files (main health)"
    doc.write_text("# a\n\n### b\n\nordinary\n", encoding="utf-8")
    git("commit", "-qam", "ordinary")
    assert run_guard(pr_step, {"BASE_SHA": base}) == 0
    assert run_guard(mh_step, {}) == 0
    # Prose that merely NAMES the directive must not red the check (this PR's own
    # doc does exactly that); only a real HTML comment directive is rejected.
    doc.write_text("# a\n\nSee the `markdownlint-disable` directive.\n", encoding="utf-8")
    git("commit", "-qam", "prose")
    assert run_guard(pr_step, {"BASE_SHA": base}) == 0
    doc.write_text("# a\n\n<!-- markdownlint-disable MD001 -->\n### b\n", encoding="utf-8")
    git("commit", "-qam", "suppress")
    assert run_guard(pr_step, {"BASE_SHA": base}) != 0
    assert run_guard(mh_step, {}) != 0
    # cli2's directive parser is CASE-INSENSITIVE: `<!-- MARKDOWNLINT-DISABLE -->`
    # suppresses a finding exactly as the lowercase form does, so a case-sensitive
    # guard let an uppercase directive through while the differ saw `0 new`.
    case_base = git("rev-parse", "HEAD").stdout.strip()
    doc.write_text("# a\n\n<!-- MARKDOWNLINT-DISABLE MD001 -->\n### b\n", encoding="utf-8")
    git("commit", "-qam", "uppercase suppression")
    assert run_guard(pr_step, {"BASE_SHA": case_base}) != 0
    assert run_guard(mh_step, {}) != 0
    # cli2's matcher is a JS regex whose `\s` includes U+FEFF and the Unicode
    # space family, so ONE zero-width character before `markdownlint-` suppressed
    # a finding while an ASCII-only `[[:space:]]*` gap class missed it — and the
    # bypass is INVISIBLE in the rendered diff. Measured: cli2 0.23.3 honours
    # `<!--<U+FEFF>markdownlint-disable MD001 -->`, and grep missed it in every
    # locale (C included).
    bom_base = git("rev-parse", "HEAD").stdout.strip()
    doc.write_text(
        "# a\n\n<!--\ufeffmarkdownlint-disable MD001 -->\n### b\n", encoding="utf-8"
    )
    git("commit", "-qam", "zero-width suppression")
    assert run_guard(pr_step, {"BASE_SHA": bom_base}) != 0
    assert run_guard(mh_step, {}) != 0
    # A PURE RENAME of a file that ALREADY carries a directive must NOT fail: with
    # rename detection off a `git mv` reads as the whole file being added, so a
    # pre-existing directive looked new — a false failure on a no-op change.
    legacy = repo / "docs" / "c.md"
    legacy.write_text("# c\n\n<!-- markdownlint-disable MD001 -->\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "add a file with a pre-existing directive")
    rename_base = git("rev-parse", "HEAD").stdout.strip()
    git("mv", "docs/c.md", "docs/d.md")
    git("commit", "-qm", "pure rename")
    assert run_guard(pr_step, {"BASE_SHA": rename_base}) == 0
    assert run_guard(mh_step, {}) == 0
    # A `.md` containing a NUL byte is BINARY to git, which then emits `Binary
    # files ... differ` with NO `+` lines at all — while cli2 still lints the file
    # and honours a directive inside it. The guard passes `--text` for exactly
    # this, and this case is the only thing that makes that flag load-bearing:
    # measured both ways, the guard is SILENT on this file without it (#7542
    # review round 8 found the flag unpinned — `grep -rn -- '--text' tests/` had
    # no reference, so all 60 cases passed with it removed).
    nul_base = git("rev-parse", "HEAD").stdout.strip()
    nul = repo / "docs" / "nul.md"
    nul.write_bytes(b"# a\n\x00\n<!-- markdownlint-disable MD001 -->\n### b\n")
    git("add", "-A")
    git("commit", "-qm", "nul-bearing file with a suppression directive")
    assert run_guard(pr_step, {"BASE_SHA": nul_base}) != 0
    assert run_guard(mh_step, {}) != 0
    # cli2 matches `configure-file` against the WHOLE document joined with
    # newlines (its regex is `<!--\s*markdownlint-(…|configure-file)`), so a
    # MULTI-LINE comment suppresses with the opener on a different line and a
    # same-line `<!--` anchor never sees it. Measured end-to-end before the split:
    # guard exit 0, cli2 `0 issues`, differ `0 new` on a genuinely new MD001
    # (#7542 review round 9 — the one live fail-open the review found).
    cf_base = git("rev-parse", "HEAD").stdout.strip()
    doc.write_text(
        '# a\n\n<!--\nmarkdownlint-configure-file { "MD001": false }\n-->\n### b\n',
        encoding="utf-8",
    )
    git("commit", "-qam", "multi-line configure-file")
    assert run_guard(pr_step, {"BASE_SHA": cf_base}) != 0
    assert run_guard(mh_step, {}) != 0
    # The single-line form must keep failing too (the split must not trade one
    # for the other), and it is the reason the alternation is tested at all:
    # removing `|configure-file` from BOTH regexes left the whole suite green.
    single_base = git("rev-parse", "HEAD").stdout.strip()
    doc.write_text(
        '# a\n\n<!-- markdownlint-configure-file { "MD001": false } -->\n### b\n',
        encoding="utf-8",
    )
    git("commit", "-qam", "single-line configure-file")
    assert run_guard(pr_step, {"BASE_SHA": single_base}) != 0
    assert run_guard(mh_step, {}) != 0
    # `capture`/`restore` re-arm a captured DISABLED state, so an added `restore`
    # suppresses a finding that an `enable` had brought back — and it is in the
    # same cross-line class, hence the second grep. The base already carries the
    # disable/enable pair, so only `capture` and `restore` are added here.
    doc.write_text(
        "# a\n\n<!-- markdownlint-disable MD001 -->\n"
        "<!-- markdownlint-enable MD001 -->\n### b\n",
        encoding="utf-8",
    )
    git("commit", "-qam", "disable then enable")
    restore_base = git("rev-parse", "HEAD").stdout.strip()
    doc.write_text(
        "# a\n\n<!-- markdownlint-disable MD001 -->\n"
        "<!-- markdownlint-capture -->\n"
        "<!-- markdownlint-enable MD001 -->\n"
        "\n<!-- markdownlint-restore -->\n### b\n",
        encoding="utf-8",
    )
    git("commit", "-qam", "capture/restore re-arms a suppression")
    assert run_guard(pr_step, {"BASE_SHA": restore_base}) != 0
    assert run_guard(mh_step, {}) != 0
    # A LARGE diff must not skip the guard: `grep -q` in a PIPE exited at its first
    # match, and under `set -o pipefail` the SIGPIPE to `git diff` made the
    # pipeline non-zero, so a large markdown diff reported no directive at all.
    # The directive goes FIRST with ~140 KB after it so grep exits early mid-write.
    big = repo / "docs" / "big.md"
    big.write_text("<!-- markdownlint-disable MD001 -->\n" + "filler\n" * 20000, encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "large suppressed file")
    assert run_guard(pr_step, {"BASE_SHA": base}) != 0
    assert run_guard(mh_step, {}) != 0


def test_update_rejects_a_lychee_document_the_check_would_reject():
    """The generator must be as fail-closed as the consumer.

    `load_lychee` requires total/error_map, but `_run_lychee` only json.loads'd,
    so a parseable object without error_map wrote a 0-entry lychee snapshot with
    exit 0 — and the ceiling accepts 0, so that snapshot then reds every future
    inherited link finding.
    """
    with pytest.raises(dlb.FailClosed):
        dlb._require_lychee_shape({}, "test")
    with pytest.raises(dlb.FailClosed):
        dlb._require_lychee_shape({"total": 0}, "test")
    # The SECOND failure map is required too: a producer that stops emitting
    # `timeout_map` would otherwise silently drop the timeout class again — the
    # exact fail-open this change closes. lychee 0.24.2 emits it on every run.
    with pytest.raises(dlb.FailClosed):
        dlb._require_lychee_shape({"total": 0, "error_map": {}}, "test")
    assert dlb._require_lychee_shape(
        {"total": 0, "error_map": {}, "timeout_map": {}}, "test"
    ) == {"total": 0, "error_map": {}, "timeout_map": {}}


def test_linters_capture_output_instead_of_deciding_the_verdict():
    """The steps must FEED the differ: the report to a file, link JSON to a file.

    If the linter still decided the verdict itself, every pre-existing finding
    would red the job and the snapshot would be dead weight — the exact churn
    #7435 exists to stop. No exit code is recorded: GNU xargs would hand the
    differ 123 for both "found issues" and "fatal error".
    """
    changed_lint = _by_name("Markdownlint (changed files)")["run"]
    main_lint = _by_name("Markdownlint (main health, changed files)")["run"]
    for lint, report in (
        (changed_lint, "mdlint-pr.txt"),
        (main_lint, "mdlint-mh.txt"),
    ):
        # The REDIRECT is the load-bearing part: `cat "$RUNNER_TEMP/mdlint-*.txt"`
        # also names the file, so a substring assertion stays green with the whole
        # capture deleted. And the capture must be exempt from `-e` — GitHub's
        # default `run:` shell is `bash -e`, cli2 exits 123 under xargs when it
        # finds issues, so without the `set +e` wrapper the STEP fails and the
        # differ never runs, restoring the pre-#7435 behaviour where every
        # inherited finding reds the check. Both mutants survived the full suite
        # (#7542 review round 9).
        assert f'> "$RUNNER_TEMP/{report}" 2>&1' in lint, report
        assert "set +e" in lint and "set -e" in lint, report
        # ... and the wrapper must BRACKET the xargs, not merely appear somewhere.
        assert "xargs" in lint[lint.index("set +e") :], report
    # (No `assert ".rc" not in lint` here. No step writes a `.rc` file — the status
    # is captured in a shell variable — so that assert could not fail and only read
    # as if it pinned the property; a vacuous assert is worse than none. #7542
    # review round 6 called it and this removes it rather than re-wording it.)

    # Exact per-step pairing. Both loops here used to accept EITHER file on
    # EITHER differ (and only a substring for the action's `output`), so a
    # copy-paste that made the PR differ read the main-health report passed —
    # and each differ's expected-file count must come from ITS OWN detector, or
    # the cardinality check validates the wrong population (#7542 review round 8).
    # A mismatch does fail closed at runtime (a missing file is rc 2), so this is
    # a lost regression net rather than a hole.
    pairing = (
        (
            "Link check (changed files)",
            "Docs lint baseline (changed files)",
            "lychee-pr.json",
            "steps.changed.outputs.count",
        ),
        (
            "Link check (main health, changed files)",
            "Docs lint baseline (main health)",
            "lychee-mh.json",
            "steps.changed_mh.outputs.count",
        ),
    )
    for action_name, differ_name, report, count_expr in pairing:
        with_block = _by_name(action_name)["with"]
        assert with_block.get("format") == "json", action_name
        assert with_block.get("fail") is False, action_name
        assert with_block.get("failIfEmpty") is False, action_name
        assert str(with_block.get("output", "")).endswith(report), action_name
        step = _by_name(differ_name)
        assert f'--lychee-output "$RUNNER_TEMP/{report}"' in step["run"], differ_name
        assert step["env"]["EXPECTED_MD_FILES"] == "${{ " + count_expr + " }}", differ_name


def test_normalize_path_strips_the_ci_list_prefix():
    """The list the workflow hands the differ is `./`-prefixed (see the `docs` job).

    `parse_markdownlint` runs this on every finding path, so a no-op version
    re-keys everything: `./docs/x.md` then never equals the snapshot's
    `docs/x.md`, and every finding in the population reads as NEW. That is
    fail-CLOSED but a wholesale false red, and the 60-case suite did not see it
    (#7542 review round 8 — a no-op mutant survived).
    """
    assert dlb.normalize_path("./docs/x.md") == "docs/x.md"
    assert dlb.normalize_path("././docs/x.md") == "docs/x.md"
    assert dlb.normalize_path("docs/x.md") == "docs/x.md"


def test_collapse_makes_a_reflow_not_a_new_finding():
    """cli2's `[Context: ...]` detail is copied from the SOURCE line.

    Reflowing a paragraph changes the spacing inside that detail without changing
    what is wrong, so collapsing whitespace keeps the key stable and the differ
    does not charge a reflow as a new finding — the churn #7435 exists to stop. A
    no-op mutant survived all 60 cases (#7542 review round 8).
    """
    assert dlb._collapse("a  b") == "a b"
    assert dlb._collapse("a\n  b") == "a b"
    assert dlb._collapse(" a ") == "a"


def test_a_generated_marker_names_the_generator_without_a_map_entry(tmp_path: Path):
    """The FIX-TARGET contract is not only the `GENERATED_DOCS` map.

    A doc carrying a `generated_from:` marker names its generator as the fix
    target even when the path is not mapped, and falls back to a generic message
    when the head names no `tools/*.py`. Disabling that sniff still passed every
    case (#7542 review round 8), so the contract rested on nothing — and it is
    deliberately latent, which is exactly why it needs a test rather than a
    reader's trust.
    """
    doc = tmp_path / "docs" / "gen.md"
    doc.parent.mkdir(parents=True)
    doc.write_text(
        "---\ngenerated_from: tools/thing.py\n---\n\n# Gen\n", encoding="utf-8"
    )
    assert dlb.generated_target("docs/gen.md", tmp_path) == "tools/thing.py"
    # The marker without a `tools/*.py` reference still names a generator target.
    doc.write_text(
        "---\ngenerated_from: something else\n---\n\n# Gen\n", encoding="utf-8"
    )
    named = dlb.generated_target("docs/gen.md", tmp_path)
    assert named is not None and named.startswith("<a generator"), named
    # No marker at all is not generated, and a missing file is not generated.
    doc.write_text("# Gen\n\nhand written\n", encoding="utf-8")
    assert dlb.generated_target("docs/gen.md", tmp_path) is None
    assert dlb.generated_target("docs/nope.md", tmp_path) is None


def test_snapshot_exists_is_consistent_and_announces_its_end_state():
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    assert baseline["schema_version"] == dlb.BASELINE_SCHEMA
    counts = baseline["snapshot"]["counts"]
    assert counts["markdownlint"] == len(baseline["markdownlint"]) > 0
    assert counts["lychee"] == len(baseline["lychee"])
    assert baseline["snapshot"]["base_sha"]
    assert baseline["end_state"]["issue"] == 7534
    assert "NOT AN AMNESTY" in baseline["note"]
    assert baseline["end_state"]["regenerate"] == "uv run python tools/docs_lint_baseline.py update", (
        "the snapshot must name the invocation that WORKS — a bare `python3` is "
        "refused by the #5128 guard on this host's 3.9 interpreter"
    )


def test_committed_policy_map_matches_the_checkout():
    """A config-only change touches no `.md`, so no `docs` step ever checks it.

    `_require_unchanged_linter_policy` is called from `run_check`, and the
    workflow gates both differ steps on a NON-ZERO changed-markdown count — so a
    PR that only turns a rule off in `.markdownlint-cli2.jsonc` (or adds a
    `[tool.lychee]` section, or an ignore path) skips the differ entirely and the
    policy change lands with nothing recorded. Measured: such a diff produces an
    empty changed-markdown list, so the step's `count != '0'` gate is false.

    This test is the net that runs anyway — a policy file change selects the FULL
    suite, and `config/docs-lint-baseline.json` selects `core`, which includes this
    file. The committed map must match the checkout, so a policy change has to
    move the snapshot; and the snapshot's own digest is pinned above, so it has to
    be made out loud.
    """
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    assert baseline["linter_config"] == dlb._config_digest(ROOT), (
        "the committed linter-policy map no longer matches the checkout — a policy "
        "file changed without the snapshot moving. A rule turned off is a suppressed "
        "finding, not a fixed one: regenerate with `update` in the same change and "
        "say why. (#7534 drains the debt; the entry count must never grow.)"
    )


def test_snapshot_is_a_ceiling_never_a_floor():
    """The snapshot may SHRINK freely and may never GROW unnoticed.

    Nothing else stops a PR from appending the very findings it introduces and
    bumping `snapshot.counts` — the file is in the PR's own diff, and the
    consistency test above only checks that the counts describe the lists, which
    a grown file still satisfies. That is a self-service amnesty: the snapshot
    then ratifies new debt instead of blocking it. These ceilings are the
    enforcement, and the burn-down (#7534) lowers them as the debt is paid; a row
    that needs RAISING is a new failure, not a snapshot edit.
    """
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    counts = baseline["snapshot"]["counts"]
    # markdownlint is DETERMINISTIC, so its ceiling is EXACT: any growth is a
    # deliberate append, never noise. The lychee half also checks REMOTE links,
    # whose occurrence count drifts between RUNS for reasons no author controls,
    # so it is a SET (deduplicated) and its ceiling is the MAXIMUM OBSERVED set
    # size across generations (151-160, measured three times) — not a round
    # number: slack above the observed range is an amnesty window, so it is
    # bounded at 160 and any re-baseline above it must raise this row out loud.
    # The asymmetry is deliberate.
    ceilings = {"markdownlint": 11238, "lychee": 160}
    for kind, ceiling in ceilings.items():
        assert counts[kind] <= ceiling, (
            f"the {kind} snapshot grew to {counts[kind]} (ceiling {ceiling}). A snapshot is a "
            "CEILING, never a floor: a new finding is a new FAILURE to fix, not an entry to "
            "add. If a raise is genuinely required, raise this ceiling in the same change "
            "and say why."
        )


def _canonical_digest(entries: list[str]) -> str:
    return hashlib.sha256("\n".join(sorted(entries)).encode("utf-8")).hexdigest()


def test_snapshot_contents_are_pinned_so_an_entry_cannot_be_swapped():
    """The ceiling bounds the COUNT; a SWAP of one entry for another defeats it.

    A PR can delete a legitimate baseline entry and append the finding it
    introduced while keeping `snapshot.counts` constant: the count ceiling
    (11238 <= 11238) and the counts/lists consistency test both pass, and the
    differ classifies the new finding as KNOWN — it only inspects findings the
    run produces, so a removed entry is never re-checked. Measured end-to-end on
    the previous revision: the differ returned 0 new on a swapped baseline. These
    digests pin the CONTENTS, so append, delete and swap all require a digest
    raised in the same change — the only thing that makes "a new entry is a new
    failure" true for the deterministic half and the remote-varying one alike.

    The pinned linter POLICY is held to the same standard, for the same reason.
    The runtime guard (`_require_unchanged_linter_policy`) compares the snapshot's
    `linter_config` map against the checkout — but that map is itself part of the
    PR's own diff, so a change that turns a rule OFF (or adds a `.markdownlint*`
    config, or a `[tool.lychee]` section) and rewrites the map to match would pass
    the guard while the linter reported FEWER findings, and the differ would then
    call the change clean. Pinning the map's digest forces that edit out loud,
    exactly as the two list digests force an append, a delete or a swap out loud.
    """
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    assert _canonical_digest(baseline["markdownlint"]) == (
        "ba58e90af5357ad7f83eb6c4004b84b0860ce4408dbbb1b7977c704c766d5469"
    ), "the markdownlint snapshot contents changed — a swap is not a re-baseline"
    assert _canonical_digest(baseline["lychee"]) == (
        "568e42acb1a73bc4ff3b68a1cecf58b4233d9f8f3ebd68279f7c3e59cece8493"
    ), "the lychee snapshot contents changed — a swap is not a re-baseline"
    assert hashlib.sha256(
        json.dumps(baseline["linter_config"], sort_keys=True).encode("utf-8")
    ).hexdigest() == "6f63d7c4437ca88c69184fcde2d53d38beb649c64eadd21edad0b02887cce658", (
        "the pinned linter-policy map changed — turning a rule off in any "
        ".markdownlint* config (or adding one, or adding a [tool.lychee] section) "
        "suppresses the very findings the required `docs` check exists to catch, so "
        "the edit must raise this digest in the same change"
    )


def test_every_mapped_generator_exists_and_its_doc_is_tracked():
    """A generator path that drifts out from under the map would name nothing."""
    import subprocess

    tracked = set(
        subprocess.run(
            ["git", "ls-files", "docs/product/*.md"], cwd=ROOT, capture_output=True, text=True
        )
        .stdout.split()
    )
    for document, generator in dlb.GENERATED_DOCS.items():
        assert (ROOT / generator).is_file(), f"{generator} does not exist"
        assert document in tracked, f"{document} is not a tracked docs/product file"
