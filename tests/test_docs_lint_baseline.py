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
  * **occurrence counts, not sets** — two findings can share one key, so a set
    would silently absorb a change that ADDED a third;
  * **fail-closed** — a linter that did not run must never read as "clean", and
    the markdownlint exit code cannot carry that evidence: GNU `xargs` rewrites a
    child's 1-125 to 123, so cli2's 1 (findings) and 2 (fatal) are the same byte.
    The differ reads the REPORT instead (`Linting: N` == the changed-set count,
    and parsed findings == the Summary count).

The workflow wiring is pinned too, because the script is only reachable through
it.
"""
from __future__ import annotations

import json
import sys
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


def _lychee_document(entries: dict[str, list[dict]]) -> dict:
    return {"total": 10, "errors": sum(len(v) for v in entries.values()), "error_map": entries}


def _file_entry(url: str, kind: str = "File not found. Check if file exists and path is correct"):
    return {"url": url, "status": {"text": kind, "details": kind}}


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
        "markdownlint": markdownlint,
        "lychee": lychee,
    }


def _md_keys(report: str) -> list[str]:
    return [dlb.markdownlint_key(f) for f in dlb.parse_markdownlint(report)]


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
    """`(path, rule, column, detail)` — collapsing columns would merge findings.

    If every MD032 in a file collapsed to one key, fixing one and adding another
    would net to zero and pass. The column plus the offending line's text keeps
    two findings of the same rule distinct.
    """
    report = (
        MARKDOWNLINT_REPORT
        + 'docs/x.md:200:5 error MD032/blanks-around-lists Lists should be surrounded by blank lines [Context: "- b"]\n'
    )
    keys = _md_keys(report)
    assert len(keys) == len(set(keys)) == 3


def test_lychee_target_is_portable_not_an_absolute_checkout_path(tmp_path: Path):
    """A committed snapshot may not carry the machine that generated it."""
    url = f"file://{tmp_path}/docs/04_platform/event-catalog.md"
    assert dlb.normalize_link_target(url, tmp_path) == "docs/04_platform/event-catalog.md"
    assert str(tmp_path) not in dlb.normalize_link_target(url, tmp_path)


# ── the check: known passes, new fails ───────────────────────────────────────


def test_known_findings_pass(tmp_path: Path, capsys):
    """The CI shape: a report + an expected-file count, and NO exit code.

    This is exactly what the workflow feeds the differ — the exit code is not a
    parameter at all, so a baselined finding in a touched file cannot red the
    required check.
    """
    lychee_document = _lychee_document(
        {"docs/x.md": [_file_entry("file:///nowhere/missing.md")]}
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
    lychee = _lychee_document({"docs/x.md": [_file_entry("file:///nowhere/missing.md")]})
    rc = _check(tmp_path, MARKDOWNLINT_REPORT, lychee, baseline)
    out = capsys.readouterr().out
    assert rc == 1, out
    assert "1 new, 2 known (baseline), 0 in generated files" in out


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
        assert Path(document).name in source, (
            f"{generator} never references {Path(document).name} — it does not render it"
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


# ── workflow wiring ──────────────────────────────────────────────────────────


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


def test_linters_capture_output_instead_of_deciding_the_verdict():
    """The steps must FEED the differ: the report to a file, link JSON to a file.

    If the linter still decided the verdict itself, every pre-existing finding
    would red the job and the snapshot would be dead weight — the exact churn
    #7435 exists to stop. No exit code is recorded: GNU xargs would hand the
    differ 123 for both "found issues" and "fatal error".
    """
    changed_lint = _by_name("Markdownlint (changed files)")["run"]
    assert "mdlint-pr.txt" in changed_lint
    main_lint = _by_name("Markdownlint (main health, changed files)")["run"]
    assert "mdlint-mh.txt" in main_lint
    for lint in (changed_lint, main_lint):
        assert ".rc" not in lint, "an exit-code file is meaningless under xargs (#7435)"

    for name in ("Link check (changed files)", "Link check (main health, changed files)"):
        with_block = _by_name(name)["with"]
        assert with_block.get("format") == "json", name
        assert with_block.get("fail") is False, name
        assert with_block.get("failIfEmpty") is False, name
        assert "lychee" in str(with_block.get("output", "")), name

    for name in ("Docs lint baseline (changed files)", "Docs lint baseline (main health)"):
        run = _by_name(name)["run"]
        assert "lychee-pr.json" in run or "lychee-mh.json" in run


def test_snapshot_exists_is_consistent_and_announces_its_end_state():
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    assert baseline["schema_version"] == dlb.BASELINE_SCHEMA
    counts = baseline["snapshot"]["counts"]
    assert counts["markdownlint"] == len(baseline["markdownlint"]) > 0
    assert counts["lychee"] == len(baseline["lychee"])
    assert baseline["snapshot"]["base_sha"]
    assert baseline["end_state"]["issue"] == 7534
    assert "NOT AN AMNESTY" in baseline["note"]


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
