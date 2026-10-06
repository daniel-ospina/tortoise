#!/usr/bin/env python3
"""The `docs` job's baseline differ — snapshot the debt that is already there.

WHY THIS EXISTS (#7435, owner ruling 2026-10-06)

The REQUIRED `docs` check lints each changed `.md` file **whole**. On the day the
snapshot was taken, 11,238 markdownlint findings sat in 650 of the 837 tracked
markdown files, and 151 link findings (many of them inherited) across 44 files.
Because the file is read whole, every one of those files is a trap: the next
change to touch one is failed for findings it did not write. Two measured
instances:

  * **#5434** — its own new plan doc carried 9 markdownlint findings
    (MD001/MD007/MD032); reproduced at the PR head in ~24 s, but discovered only
    after a full CI cycle.
  * **#7475** — touching ``docs/product/mcp-sdk-surface.md`` failed the lychee
    step on a link that had been broken **on main for as long as the file
    existed**, in a GENERATED file whose fix target is the generator.

The owner ruled **Option 1**: *save today's list of findings, and reject a
proposed change only for problems that are not on that list.* Option 2 (repair
the 518+ files) was split out to **#7534**.

WHAT THIS PROGRAM DOES

  * ``check``  — CI. Parse the two linters' own output, diff the findings against
    the committed snapshot, and exit non-zero ONLY on findings that are not in
    the snapshot. New findings still fail the build; this narrows the SCOPE the
    check is responsible for, it does not weaken it.
  * ``update`` — regenerate the snapshot by running the CI's OWN pinned linters
    (``markdownlint-cli2@0.23.3`` and ``lychee 0.24.2``) over the population the
    job can lint (``git ls-files '*.md'``). The snapshot is reproducible, never
    hand-written.

WHY THE MARKDOWNLINT EXIT CODE IS NOT USED

The CI runs the linter through ``xargs -0``. **xargs rewrites its child's exit
status**: GNU (ubuntu-latest) maps any child exit of 1-125 to **123**, and BSD
maps it to **1**. So ``markdownlint-cli2``'s 1 (issues found) and 2 (fatal error)
both arrive as the same number and cannot be told apart — measured:
``printf 'x\0' | xargs -0 sh -c 'exit 1'`` -> 123, and ``exit 2`` -> 123 as well.
A discriminator built on that exit code is meaningless, and one that accepts only
{0, 1} would fail EVERY PR on Linux.

The evidence used instead is the report itself, and it is stronger:

  * ``Linting: N files`` must equal the number of files the changed set held
    (passed by the caller). A linter that judged fewer files than it was given,
    or none at all, has not done its job — that is the silent-green trap.
  * ``Summary: X issues in M files`` must be present, and the number of finding
    lines parsed must equal X. A finding line the parser cannot read is a finding
    the diff cannot see, i.e. a fail-OPEN hole.

KEYS ARE STABLE ACROSS UNRELATED LINE SHIFTS

A finding is keyed on ``path`` + the rule/target + its content, **never on a line
number** — a line-number key would invalidate the whole snapshot the moment any
file grew above the offending line, which is the churn this exists to stop.

  * markdownlint — ``path|RULE|column|normalized_detail``
  * lychee       — ``path|link_target|kind``

``normalized_detail`` keeps the rule's message and its ``[Context: …]`` (the
offending line's text), and ``column`` is horizontal, so both survive a shift of
the file's lines. Editing the offending line itself DOES change the key — that is
deliberate and fail-closed: a finding you touched is a finding you own.

THE SNAPSHOT IS OCCURRENCE-COUNTED, NOT A SET

Several findings can share one key (the same rule, column and offending line
text on two different lines). A set would silently absorb a change that ADDED a
third — the key was already present — so the snapshot stores every occurrence and
the check compares counts: a change fails whenever it produces MORE occurrences
of a key than the snapshot recorded. Fixing one of them is never a new finding.

END STATE — THIS IS A SNAPSHOT, NOT AN AMNESTY (#7534)

This file records debt; it does not repair any of it. It is a **ceiling, never a
floor**: every finding removed from the codebase must be removed from the
snapshot (run ``update``), and the entry count must never grow — a new entry is a
new failure, not a snapshot edit. **#7534 drains it to zero**, and when the
snapshot is empty this program and its baseline file are deleted.

GENERATED FILES

A finding inside a file that is rendered by a generator cannot be fixed in the
``.md``: a re-render rewrites it, and a hand edit turns a link failure into a
drift failure. When a finding's file carries a "generated" marker (or is in
``GENERATED_DOCS``), the report names the **generator** as the fix target.
"""
from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below — a module-level
# 3.11+-only import (`from datetime import UTC`) would fail first (D9 shape).
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/docs_lint_baseline.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/docs_lint_baseline.py`"
    )

import argparse
import json
import re
import subprocess
import tempfile
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

# Pinned to match `.github/workflows/ci.yml` — the snapshot must be produced by
# the same tools the required check runs, or it describes a different check.
MARKDOWNLINT_PIN = "markdownlint-cli2@0.23.3"
MARKDOWNLINT_VERSION = "markdownlint-cli2@0.23.3 / markdownlint 0.41.1"
LYCHEE_PIN = "0.24.2"

BASELINE_SCHEMA = 1
DEFAULT_BASELINE = "config/docs-lint-baseline.json"

# `:<line>` and `:<line>:<column>` are both emitted (cli2 omits the column when
# it is 1). `.+?` is non-greedy so a path containing a colon still anchors on the
# first `:<digits> ` that begins the diagnostic.
MARKDOWNLINT_FINDING = re.compile(
    r"^(?P<path>.+?):(?P<line>\d+)(?::(?P<column>\d+))? "
    r"(?P<severity>error|warning) (?P<code>MD\d+)/(?P<alias>[A-Za-z0-9_/-]+) "
    r"(?P<detail>.*)$"
)
MARKDOWNLINT_SUMMARY = re.compile(
    r"^Summary: (?P<issues>\d+) issues? in (?P<files>\d+) files?$", re.M
)
MARKDOWNLINT_LINTING = re.compile(r"^Linting: (?P<files>\d+) files?$", re.M)

# Files rendered by a generator, mapped to the generator that must be edited.
# Kept explicit (rather than sniffed) so the fix target is unambiguous; the
# marker sniff below is the safety net for a generator this map does not know.
#
# ⛔ A mapping is only real if the generator WRITES the file. `beta-sdk-surface.md`
# was once in this map pointed at `tools/bridge_table.py`, which only READS it
# (BETA_DOC.read_text) — a hand-authored, owner-approved doc. A finding there
# would then have printed "edit the generator, do NOT hand-edit the rendered
# markdown", which cannot fix it: the #7475 misdirection one level up. The test
# suite now asserts both halves (the doc declares a generated marker AND the
# named generator's source references the file it renders).
GENERATED_DOCS = {
    "docs/product/mcp-sdk-surface.md": "tools/surface_manifest.py",
    "docs/product/bridge-table.md": "tools/bridge_table.py",
    "docs/product/mcp-rename-table.md": "tools/mcp_rename_table.py",
    "docs/product/sdk-surface-declaration.md": "tools/sdk_surface.py",
}
GENERATED_HEAD_LINES = 25
# A generator marker must be LINE-ANCHORED. A substring search for "GENERATED"
# false-positived on ordinary docs that merely discuss a generator — measured on
# `MEMORY.md` ("... is GENERATED ON DEMAND ...") and `docs/00_index.md` (an
# index row quoting another doc's marker); the loose form reported both as
# generated and then blamed their generators for a finding inside them.
GENERATED_MARKERS = (
    re.compile(r"^\s*generated_from:", re.M),  # frontmatter key
    re.compile(r"^\s*\*\*\s*GENERATED", re.M),  # bolded marker at line start
)
GENERATOR_REFERENCE = re.compile(r"tools/[A-Za-z0-9_./-]+\.py")

MAX_REPORTED = 40


def _collapse(text: str) -> str:
    """Whitespace-collapse a detail so an unrelated reflow cannot change a key."""
    return " ".join(text.split())


def normalize_path(path: str) -> str:
    """Repo-relative POSIX path, without the `./` the CI list prefixes."""
    while path.startswith("./"):
        path = path[2:]
    return path


# ── parsing ──────────────────────────────────────────────────────────────────


def parse_markdownlint(text: str) -> list[tuple[str, str, str, str]]:
    """(path, code, column, detail) for every finding in cli2's default report."""
    findings: list[tuple[str, str, str, str]] = []
    for raw in text.splitlines():
        match = MARKDOWNLINT_FINDING.match(raw)
        if match is None:
            continue
        findings.append(
            (
                normalize_path(match.group("path")),
                match.group("code"),
                match.group("column") or "1",
                _collapse(match.group("detail")),
            )
        )
    return findings


def normalize_link_target(url: str, repo_root: Path) -> str:
    """A portable target: `file://` URLs become repo-relative, others verbatim.

    lychee resolves a local link to an ABSOLUTE ``file://`` URL, so the raw value
    embeds the checkout path and could never be committed as a baseline. The
    repo-relative form is what the link actually points at.
    """
    if not url.startswith("file://"):
        return _collapse(url)
    parsed = urlparse(url)
    raw = Path(unquote(parsed.path))
    try:
        return raw.relative_to(repo_root).as_posix()
    except ValueError:
        # Outside the checkout (or an odd platform path): keep the tail of the
        # URL rather than an absolute machine path.
        return raw.as_posix()


def parse_lychee(document: dict, repo_root: Path) -> list[tuple[str, str, str]]:
    """(path, link_target, kind) for every entry in lychee's JSON error_map."""
    findings: list[tuple[str, str, str]] = []
    for path, entries in (document.get("error_map") or {}).items():
        for entry in entries or []:
            status = entry.get("status") or {}
            kind = status.get("details") or status.get("text") or "error"
            findings.append(
                (
                    normalize_path(path),
                    normalize_link_target(str(entry.get("url", "")), repo_root),
                    _collapse(str(kind)),
                )
            )
    return findings


def markdownlint_key(finding: tuple[str, str, str, str]) -> str:
    return "|".join(finding)


def lychee_key(finding: tuple[str, str, str]) -> str:
    return "|".join(finding)


# ── generated-file detection ─────────────────────────────────────────────────


def generated_target(path: str, repo_root: Path) -> str | None:
    """The generator to edit for a finding in `path`, or None if not generated."""
    mapped = GENERATED_DOCS.get(path)
    if mapped is not None:
        return mapped
    document = repo_root / path
    if not document.is_file():
        return None
    try:
        head = document.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    head = "\n".join(head.splitlines()[:GENERATED_HEAD_LINES])
    if not any(marker.search(head) for marker in GENERATED_MARKERS):
        return None
    reference = GENERATOR_REFERENCE.search(head)
    if reference is not None:
        return reference.group(0)
    return "<a generator — see the file's 'generated' marker>"


# ── fail-closed loading of the linters' output ───────────────────────────────


class FailClosed(Exception):
    """The linter did not run (or did not run cleanly) — never a lint verdict."""


def parse_markdownlint_checked(text: str) -> list[tuple[str, str, str, str]]:
    """Parse a cli2 report, refusing one the parser cannot fully account for.

    A line the parser does not recognise is a finding the diff cannot see, which
    would read as "known" and silently fail OPEN — the class this program exists
    to avoid. Measured on the first revision: 89
    ``MD025/single-title/single-h1`` findings were dropped because the alias
    regex did not allow a second ``/`` segment.
    """
    summary = MARKDOWNLINT_SUMMARY.search(text)
    if summary is None:
        raise FailClosed("markdownlint report has no 'Summary:' line — it did not run")
    findings = parse_markdownlint(text)
    reported = int(summary.group("issues"))
    if len(findings) != reported:
        raise FailClosed(
            f"markdownlint reported {reported} issue(s) but {len(findings)} parsed — the "
            "report format changed and findings would be missed; failing closed"
        )
    return findings


def load_markdownlint(output: Path, expected_files: int) -> list[tuple[str, str, str, str]]:
    if not output.is_file():
        raise FailClosed(f"markdownlint report {output} is missing — the linter did not run")
    text = output.read_text(encoding="utf-8", errors="replace")
    # `Linting: N files` is the positive evidence that cli2 actually judged the
    # files it was handed. The exit code cannot supply it (see the module
    # docstring: xargs rewrites it), and the banner cannot (it prints before a
    # config error aborts the run).
    linting = MARKDOWNLINT_LINTING.search(text)
    if linting is None:
        raise FailClosed(
            f"markdownlint report {output} has no 'Linting:' line — it did not run"
        )
    linted = int(linting.group("files"))
    if linted != expected_files:
        raise FailClosed(
            f"markdownlint judged {linted} file(s) but the changed set holds "
            f"{expected_files} — it did not lint what it was given; failing closed"
        )
    return parse_markdownlint_checked(text)


def load_lychee(output: Path, repo_root: Path) -> list[tuple[str, str, str]]:
    if not output.is_file():
        raise FailClosed(f"lychee JSON report {output} is missing — the link check did not run")
    try:
        document = json.loads(output.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FailClosed(f"lychee JSON report {output} is unparseable: {exc}") from exc
    if not isinstance(document, dict) or "error_map" not in document or "total" not in document:
        raise FailClosed(
            f"lychee JSON report {output} has not the expected shape (total/error_map missing) "
            "— failing closed"
        )
    return parse_lychee(document, repo_root)


# ── baseline ─────────────────────────────────────────────────────────────────


def load_baseline(path: Path) -> dict:
    try:
        baseline = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FailClosed(f"baseline {path} is missing or unparseable: {exc}") from exc
    if baseline.get("schema_version") != BASELINE_SCHEMA:
        raise FailClosed(
            f"baseline {path} has schema_version {baseline.get('schema_version')!r}, "
            f"expected {BASELINE_SCHEMA}"
        )
    for key in ("markdownlint", "lychee"):
        if not isinstance(baseline.get(key), list):
            raise FailClosed(f"baseline {path} has no {key!r} list")
    # The snapshot's own counts must describe its own lists. A silently truncated
    # or inflated file would mis-scope every later diff, and an inflated one could
    # mask a genuinely new occurrence.
    counts = (baseline.get("snapshot") or {}).get("counts") or {}
    for key in ("markdownlint", "lychee"):
        recorded = counts.get(key)
        if recorded is not None and recorded != len(baseline[key]):
            raise FailClosed(
                f"baseline {path} says {recorded} {key} entries but carries "
                f"{len(baseline[key])} — the snapshot is corrupt"
            )
    return baseline


# ── check ────────────────────────────────────────────────────────────────────


def _describe(kind: str, key: str) -> str:
    """A human line for a finding key, plus its fix target when generated."""
    parts = key.split("|")
    if kind == "markdownlint":
        path, code, column, detail = parts[0], parts[1], parts[2], "|".join(parts[3:])
        location = f"{path} (column {column})"
        what = f"{code} {detail}"
    else:
        path, target = parts[0], parts[1]
        location = path
        what = f"{target} — {'|'.join(parts[2:])}"
    return f"  {location}\n      {what}"


def run_check(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo_root).resolve()
    baseline = load_baseline(Path(args.baseline))
    # Occurrence counts, not sets: see the module docstring. Adding a second
    # occurrence of an already-recorded key is still a new finding.
    known_markdownlint = Counter(baseline["markdownlint"])
    known_lychee = Counter(baseline["lychee"])

    try:
        markdownlint = [
            (markdownlint_key(f), f)
            for f in load_markdownlint(
                Path(args.markdownlint_output), args.markdownlint_expected_files
            )
        ]
        lychee = [
            (lychee_key(f), f) for f in load_lychee(Path(args.lychee_output), repo_root)
        ]
    except FailClosed as exc:
        print(f"::error::docs-lint baseline: {exc}", file=sys.stderr)
        return 2

    new: list[tuple[str, str]] = []
    known = 0
    generated = 0
    # Memoized so a file with hundreds of findings is read once, not per finding.
    generated_cache: dict[str, str | None] = {}

    def _generated(path: str) -> str | None:
        if path not in generated_cache:
            generated_cache[path] = generated_target(path, repo_root)
        return generated_cache[path]

    for kind, observed in (("markdownlint", markdownlint), ("lychee", lychee)):
        recorded = known_markdownlint if kind == "markdownlint" else known_lychee
        seen: Counter[str] = Counter()
        for key, finding in observed:
            if _generated(finding[0]) is not None:
                generated += 1
            seen[key] += 1
            if seen[key] <= recorded[key]:
                known += 1
            else:
                new.append((kind, key))

    print(
        f"docs-lint baseline: {len(new)} new, {known} known (baseline), "
        f"{generated} in generated files"
    )

    if new:
        print("")
        print(f"NEW findings not in {args.baseline} — these fail the required `docs` check:")
        for kind, key in new[:MAX_REPORTED]:
            print(_describe(kind, key))
            target = _generated(key.split("|")[0])
            if target is not None:
                print(
                    f"      -> FIX TARGET: {target} (GENERATED file — edit the generator "
                    "and re-render; do NOT hand-edit the rendered markdown)"
                )
        if len(new) > MAX_REPORTED:
            print(f"  … and {len(new) - MAX_REPORTED} more new finding(s)")
        print("")
        print(
            "A new finding is a real failure. Fix it, or — if it is genuinely "
            "pre-existing debt this snapshot missed — regenerate the snapshot "
            "(python3 tools/docs_lint_baseline.py update) in a separate change "
            "that explains why (#7534 drains it; the entry count must never grow)."
        )
        return 1
    return 0


# ── update ───────────────────────────────────────────────────────────────────


def _population(repo_root: Path, files_from: Path | None) -> list[str]:
    if files_from is not None:
        listed = [
            line.strip()
            for line in files_from.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        ]
    else:
        proc = subprocess.run(
            ["git", "ls-files", "-z", "*.md"],
            cwd=repo_root,
            capture_output=True,
        )
        if proc.returncode != 0:
            raise FailClosed(f"git ls-files failed: {proc.stderr.decode(errors='replace')}")
        listed = [p for p in proc.stdout.decode().split("\0") if p]
    # `./`-prefix every entry: a filename is data, and one starting with `-` must
    # never be read as an option by a linter (the CI's own contract, #4449).
    return [normalize_path(p) for p in listed]


def _run_markdownlint(files: list[str], repo_root: Path) -> tuple[int, str]:
    proc = subprocess.run(
        ["npx", "--yes", MARKDOWNLINT_PIN, *[f"./{f}" for f in files]],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    return proc.returncode, proc.stdout + proc.stderr


def _run_lychee(files: list[str], repo_root: Path, lychee_bin: str) -> dict:
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as handle:
        list_path = Path(handle.name)
        handle.write("\n".join(f"./{f}" for f in files))
    try:
        proc = subprocess.run(
            [
                lychee_bin,
                "--mode",
                "task",
                "--no-progress",
                "--format",
                "json",
                "--files-from",
                str(list_path),
            ],
            cwd=repo_root,
            capture_output=True,
            text=True,
        )
    finally:
        list_path.unlink(missing_ok=True)
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise FailClosed(
            f"lychee produced unparseable JSON (exit {proc.returncode}): "
            f"{proc.stderr.strip()[:400]}"
        ) from exc


def run_update(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo_root).resolve()
    files = _population(repo_root, Path(args.files_from) if args.files_from else None)
    if not files:
        raise FailClosed("no markdown files in the population")
    print(f"docs-lint baseline: sampling {len(files)} markdown file(s)")

    rc, markdownlint_text = _run_markdownlint(files, repo_root)
    if rc not in (0, 1):
        raise FailClosed(f"markdownlint exited {rc} while generating the snapshot")
    # Sorted WITH duplicates: the snapshot is a multiset (see the module
    # docstring), so two occurrences of one key are two entries.
    markdownlint = sorted(
        markdownlint_key(f) for f in parse_markdownlint_checked(markdownlint_text)
    )

    lychee_document = _run_lychee(files, repo_root, args.lychee_bin)
    lychee = sorted(lychee_key(f) for f in parse_lychee(lychee_document, repo_root))

    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo_root, capture_output=True, text=True
    ).stdout.strip()

    baseline = {
        "schema_version": BASELINE_SCHEMA,
        "note": (
            "SNAPSHOT, NOT AN AMNESTY (#7435). This file records the findings that "
            "already existed when it was taken, so the REQUIRED `docs` check fails a "
            "change only on findings that are NOT here. It repairs nothing. Read "
            "'end_state' before editing it."
        ),
        "end_state": {
            "issue": 7534,
            "rule": (
                "This file is a CEILING, never a floor. Every finding removed from the "
                "codebase must be removed from this file (run `update`), and the entry "
                "count must never grow — a genuinely new entry is a new failure, not a "
                "snapshot edit. #7534 drains the list to zero; when it is empty, this "
                "file and tools/docs_lint_baseline.py are deleted."
            ),
            "regenerate": "python3 tools/docs_lint_baseline.py update",
        },
        "snapshot": {
            "base_sha": head,
            "generated_at_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "markdownlint": MARKDOWNLINT_VERSION,
            "lychee": f"lychee {LYCHEE_PIN}",
            "population": f"git ls-files '*.md' — {len(files)} files",
            "counts": {"markdownlint": len(markdownlint), "lychee": len(lychee)},
            "variance": (
                "markdownlint findings are deterministic. The lychee half also checks "
                "REMOTE links, so a few of its entries can differ between runs (rate "
                "limits, transient network, TLS) — measured 151-155 across three "
                "generations. Regenerate with `update`; never hand-edit."
            ),
        },
        "markdownlint": markdownlint,
        "lychee": lychee,
    }
    Path(args.baseline).write_text(
        json.dumps(baseline, indent=2, sort_keys=False) + "\n", encoding="utf-8"
    )
    print(
        f"wrote {args.baseline}: {len(markdownlint)} markdownlint, {len(lychee)} lychee "
        "entries"
    )
    return 0


# ── cli ──────────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--repo-root", default=".", help="repository root (default: cwd)")
    common.add_argument("--baseline", default=DEFAULT_BASELINE, help="snapshot JSON path")
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser(
        "check", parents=[common], help="diff the linters' output against the snapshot"
    )
    check.add_argument("--markdownlint-output", required=True)
    check.add_argument(
        "--markdownlint-expected-files",
        type=int,
        required=True,
        help=(
            "how many markdown files the changed set held; the report must show it "
            "linted exactly that many (the exit code cannot carry this — xargs rewrites it)"
        ),
    )
    check.add_argument("--lychee-output", required=True)
    check.set_defaults(func=run_check)

    update = sub.add_parser(
        "update",
        parents=[common],
        help="regenerate the snapshot with the pinned linters",
    )
    update.add_argument(
        "--files-from",
        default=None,
        help="file listing markdown paths (default: git ls-files '*.md')",
    )
    update.add_argument(
        "--lychee-bin",
        default="lychee",
        help=f"lychee {LYCHEE_PIN} binary (default: lychee on PATH)",
    )
    update.set_defaults(func=run_update)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except FailClosed as exc:
        print(f"::error::docs-lint baseline: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
