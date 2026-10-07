#!/usr/bin/env python3
"""The `docs` job's baseline differ — snapshot the debt that is already there.

WHY THIS EXISTS (#7435, owner ruling 2026-10-06)

The REQUIRED `docs` check lints each changed `.md` file **whole**. On the day the
snapshot was taken, 11,238 markdownlint findings sat in 650 of the 838 tracked
markdown files, plus the link findings (read them from the snapshot's own
``counts`` — that half checks REMOTE links and varies between generations).
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
  * lychee       — ``path|link_target`` (status text is NOT part of the key;
                   for lychee's ``error:`` placeholder — where there is no URL
                   to key on — the offending line's own text is used instead)

For a finding with a REAL target, the lychee key deliberately carries no status
text. lychee reports a URL it has
already seen in a run as ``Error (cached)``, so the status is a property of the
RUN'S FILE POPULATION — the same untouched link keys differently when the run
covers the whole repo (what ``update`` does) and when it covers only a PR's
changed files (what CI does). A status in the key therefore reds an unrelated PR
on an inherited finding, which is the #7475 failure this exists to remove. The
status IS still shown in the report; it is just not part of the identity. Two
findings on one target are ONE finding — the target is broken either way — and
the lychee comparison is therefore a key SET; see "THE SNAPSHOT…" below for why
an occurrence count cannot be compared across the populations the two paths run
over.

PLACEHOLDER TARGETS ARE THE EXCEPTION, and they are the opposite failure. When
lychee cannot extract a URL it records the target ``error:``, and ``path|error:``
has no distinguishing content: two different broken links in one file collapse to
one key, so editing one to the other keeps the key and absorbs a new dead link.
For those findings only the offending line's own text is the identity — it is
source content, not run state, and it is exactly what the paragraph above
promises changes when the line is edited. A bare ``Error (cached)`` marker is
still excluded even there, so no key anywhere varies with the run's population.

``normalized_detail`` keeps the rule's message and its ``[Context: …]`` (the
offending line's text), and ``column`` is horizontal, so both survive a shift of
the file's lines. Editing the offending line itself DOES change the key — that is
deliberate and fail-closed: a finding you touched is a finding you own.

THE SNAPSHOT IS OCCURRENCE-COUNTED WHERE THE TREE DETERMINES THE COUNT

The two linters need different comparisons, and the difference is forced by the
data, not chosen for convenience.

  * **markdownlint is a multiset.** Several findings can share one key (the same
    rule, column and offending line text on two different lines). A set would
    silently absorb a change that ADDED a third — the key was already present —
    so the snapshot stores every occurrence and the check compares counts: a
    change fails whenever it produces MORE occurrences of a key than the
    snapshot recorded. This is safe because a file's markdownlint findings are a
    property of the file ALONE: every file is linted independently, so the count
    is identical whether the run covers one file or all 838. Fixing one of them
    is never a new finding.

  * **lychee is a SET of ``(path, target)`` keys.** A link's occurrence count is
    a property of the RUN, not the tree: ``update`` lints all tracked markdown
    while CI lints only the changed files, and remote-link outcomes (429/403/…)
    plus lychee's run cache move the count for an unchanged link between runs.
    Measured at the snapshot's own base_sha: ``docs/license-notes.md`` carries
    the hashicorp URL TWICE, the all-file ``update`` recorded it ONCE (while
    recording the couchbase URL on the same file TWICE), and a changed-file run
    reports the hashicorp URL twice — so a count comparison classified an
    INHERITED finding as new and redded an unrelated PR, the #7475 failure this
    snapshot exists to remove. Membership still fails a genuinely new dead link;
    it stops re-failing a *repeat* of a link already on the list, which the
    owner's ruling ("reject a change only for problems not on that list") does
    not ask for — the problem is already on the list. There is no reproducible
    count to compare against, so the baseline stores and compares a key set, and
    ``update`` deduplicates this half.

END STATE — THIS IS A SNAPSHOT, NOT AN AMNESTY (#7534)

This file records debt; it does not repair any of it. It is a **ceiling, never a
floor**: every finding removed from the codebase must be removed from the
snapshot (run ``update``), and the entry count must never grow — a new entry is a
new failure, not a snapshot edit. The ceiling is ENFORCED (a pinned count in
``tests/test_docs_lint_baseline.py``), because the snapshot sits in a PR's own
diff and nothing else stops a change from appending the very findings it
introduces. **#7534 owns the burn-down** — see its comment for the population
gap: this snapshot covers ALL tracked markdown, while #7534 was scoped by a
``docs/``-subtree measurement, so it empties only when the non-``docs/`` remainder
is drained too. When the snapshot is empty this program and its baseline file are
deleted.

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
import hashlib
import json
import re
import subprocess
import tempfile
import tomllib
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

# Pinned to match `.github/workflows/ci.yml` — the snapshot must be produced by
# the same tools the required check runs, or it describes a different check.
MARKDOWNLINT_PIN = "markdownlint-cli2@0.23.3"
MARKDOWNLINT_VERSION = "markdownlint-cli2@0.23.3 / markdownlint 0.41.1"
LYCHEE_PIN = "0.24.2"

# The linter POLICY file BASENAMES, at any depth. cli2 reads its
# `.markdownlint*` config from ANY directory on the path to a linted file and a
# more specific config OVERRIDES the repo one, so pinning only the root file
# left a same-PR `docs/.markdownlint-cli2.jsonc` (or a root `.markdownlint.json`)
# free to turn a rule off. A path ignored in `.lycheeignore`/`lychee.toml` has the
# same effect on the link half. Every TRACKED file with one of these basenames is
# digested, so ADDING one is a policy change too.
LINTER_CONFIG_NAMES = frozenset({
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

# lychee 0.24.2 does NOT take its policy from `lychee.toml` alone: it also
# auto-loads a section out of `Cargo.toml` (`[package.metadata.lychee]`),
# `pyproject.toml` (`[tool.lychee]`) and `package.json` (`"lychee"`) in its
# WORKING DIRECTORY — which is the repo root, because `_run_lychee` runs there.
# Digesting those files WHOLE would red the required check on every dependency
# bump, which is not a policy change, so only the lychee section is digested —
# and a MISSING section digests to a fixed marker, so ADDING one is a policy
# change too (measured: `[tool.lychee] exclude = ["..."]` in the tracked
# `pyproject.toml` emptied the link half while every digested file stayed
# byte-identical, and the differ then reported `0 new`).
LYCHEE_CARRIERS: dict[str, tuple[str, ...]] = {
    "Cargo.toml": ("package", "metadata", "lychee"),
    "pyproject.toml": ("tool", "lychee"),
    "package.json": ("lychee",),
}
_NO_LYCHEE_SECTION = hashlib.sha256(b"<no lychee section>").hexdigest()

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
# A bare cache marker in lychee's status. It is the ONE status value that
# describes the RUN's file population rather than the finding, so it is never
# allowed into a key (see `lychee_key`).
_CACHE_MARKER = re.compile(r"^\s*Error \(cached\)\s*$", re.I)

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
    repo-relative form is what the link actually points at — FRAGMENT INCLUDED,
    because two dead anchors in one file are two different dead links, and
    dropping the fragment would let a swap of one for the other keep the same
    occurrence count and pass.
    """
    if not url.startswith("file://"):
        return _collapse(url)
    parsed = urlparse(url)
    raw = Path(unquote(parsed.path))
    try:
        relative = raw.relative_to(repo_root).as_posix()
    except ValueError:
        # A path outside the checkout has NO portable repo-relative spelling: any
        # form embeds this machine's layout, so committing it makes the snapshot
        # unreproducible on the next runner. Refuse rather than write a
        # machine-specific key — this branch used to return exactly the absolute
        # path the docstring above promises to avoid.
        raise FailClosed(
            f"link target {url!r} resolves outside the repository — it has no "
            "portable repo-relative form, so it cannot be recorded in a baseline"
        ) from None
    fragment = f"#{parsed.fragment}" if parsed.fragment else ""
    return _collapse(relative + fragment)


def parse_lychee(document: dict, repo_root: Path) -> list[tuple[str, str, str]]:
    """(path, link_target, kind) for every entry in lychee's JSON error_map."""
    findings: list[tuple[str, str, str]] = []
    for path, entries in (document.get("error_map") or {}).items():
        for entry in entries or []:
            status = entry.get("status")
            if not isinstance(status, dict):
                # A producer that emits a non-object status must fail CLOSED (its
                # key reds), not crash with an AttributeError and a traceback.
                status = {}
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
    """`path|link_target` — the status is observed, but NOT part of the identity.

    See the module docstring: lychee's status text carries the run's cache state
    (``Error (cached)``), so keying on it makes an untouched link look new
    whenever the file population changes between ``update`` and CI. The status is
    still reported; it just cannot decide whether a finding is new.

    ONE EXCEPTION, and it is the opposite failure: when lychee could not extract
    a URL at all it reports the placeholder target ``error:``, and then
    `path|target` has NO distinguishing content — two different broken links in
    one file collapse to one key, a swap keeps the occurrence count, and a real
    new dead link is absorbed (measured: two `fdir/README.md` entries, one for
    `/BENCHMARKS.md` and one for `/documentation.md`, both keyed the same). For
    those findings only, the status text IS the identity: it is the offending
    line's own content, not the run's cache state, and it is exactly what the
    docstring promises changes when the offending line is edited. The cache
    marker is excluded even here, so no key anywhere varies with the population.
    """
    path, target = finding[0], finding[1]
    status = finding[2] if len(finding) > 2 else ""
    if target and target != "error:":
        return f"{path}|{target}"
    if _CACHE_MARKER.match(status):
        # No target AND only a cache marker: nothing portable to key on.
        return f"{path}|{target}"
    return f"{path}|{target}|{status}"


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


def _lychee_section_digest(path: Path, rel: str) -> str:
    """Digest of the lychee config section inside a carrier file, or a marker.

    A carrier that cannot be parsed is a policy that cannot be attested, so it
    fails closed rather than reading as "unchanged".
    """
    try:
        text = path.read_text(encoding="utf-8")
        data: object = json.loads(text) if rel.endswith(".json") else tomllib.loads(text)
    except (OSError, ValueError) as exc:
        raise FailClosed(
            f"cannot read {rel} to attest the lychee policy it may carry: {exc}"
        ) from exc
    node: object = data
    for key in LYCHEE_CARRIERS[rel]:
        node = node.get(key) if isinstance(node, dict) else None
    if node is None:
        return _NO_LYCHEE_SECTION
    return hashlib.sha256(
        json.dumps(node, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def _config_digest(repo_root: Path) -> dict[str, str]:
    """Content digest of EVERY tracked linter-policy file, keyed by repo path.

    Tracked, not merely on-disk: CI checks out the PR commit, so a policy file
    the PR adds is exactly what must change this map — and an added file is a
    key that was not recorded, so it fails closed. A non-repo root (a test
    fixture) has no policy files.
    """
    if not (repo_root / ".git").exists():
        return {}
    proc = subprocess.run(
        ["git", "ls-files", "-z"], cwd=repo_root, capture_output=True, text=True
    )
    if proc.returncode != 0:
        raise FailClosed(
            f"`git ls-files` failed in {repo_root} (rc {proc.returncode}) — the linter "
            "policy cannot be attested, so the snapshot cannot be validated"
        )
    digest: dict[str, str] = {}
    for rel in proc.stdout.split("\0"):
        if not rel or not (repo_root / rel).is_file():
            continue
        if Path(rel).name in LINTER_CONFIG_NAMES:
            digest[rel] = hashlib.sha256((repo_root / rel).read_bytes()).hexdigest()
        elif rel in LYCHEE_CARRIERS:
            digest[rel] = _lychee_section_digest(repo_root / rel, rel)
    return digest


def _require_unchanged_linter_policy(baseline: dict, repo_root: Path) -> None:
    """Refuse a diff computed under a DIFFERENT linter policy than the snapshot's.

    The differ compares findings, so the policy that produced them is part of the
    comparison's validity: a same-PR edit to any `.markdownlint*` config (turn a
    rule off), or to `.lycheeignore`/`lychee.toml` (ignore a path), suppresses the
    finding instead of fixing it, and the differ then reports `0 new`. Pinning the
    policy makes that edit fail closed until the snapshot is regenerated to match
    it. The field is REQUIRED: a baseline that simply omits `linter_config` would
    otherwise disable this whole check.
    """
    recorded = baseline.get("linter_config")
    if not isinstance(recorded, dict):
        raise FailClosed(
            "the baseline carries no linter_config map, so the linter policy it was "
            "generated under cannot be attested — regenerate it with `update`"
        )
    current = _config_digest(repo_root)
    if current == recorded:
        return
    changed = sorted(
        name
        for name in set(current) | set(recorded)
        if current.get(name) != recorded.get(name)
    )
    raise FailClosed(
        "the linter policy changed since the snapshot was taken ("
        + ", ".join(changed)
        + ") — the pinned linters would report DIFFERENT findings, so the diff is "
        "meaningless. Regenerate the snapshot in this same change (`update`) and "
        "say why."
    )


class FailClosed(Exception):
    """The linter did not run (or did not run cleanly) — never a lint verdict."""


def _require_lychee_shape(document: object, where: str) -> dict:
    """Refuse a lychee document the differ cannot read, on BOTH code paths.

    `check` always required `total`/`error_map`, but `update` did not — so a
    lychee run that emitted a parseable object without `error_map` (an error
    envelope, a wrapper) wrote a 0-entry lychee snapshot and exited 0. The
    ceiling accepts 0, so that snapshot then reds every future inherited link
    finding: the generator must be exactly as fail-closed as the consumer.
    """
    if not isinstance(document, dict) or "error_map" not in document or "total" not in document:
        raise FailClosed(
            f"lychee JSON from {where} has not the expected shape (total/error_map "
            "missing) — failing closed"
        )
    return document


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
    document = _require_lychee_shape(document, f"report {output}")
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


def _describe(kind: str, key: str, observed: tuple | None = None) -> str:
    """A human line for a finding key, plus the OBSERVED status where one exists.

    The lychee status is printed from the run's own finding, not read back out of
    the key: it is not part of the identity (a cached vs fresh report of the same
    link is the same finding), but it is the most useful thing to show.
    """
    parts = key.split("|")
    if kind == "markdownlint":
        path, code, column, detail = parts[0], parts[1], parts[2], "|".join(parts[3:])
        location = f"{path} (column {column})"
        what = f"{code} {detail}"
    else:
        path, target = parts[0], parts[1]
        location = path
        status = observed[2] if observed and len(observed) > 2 else ""
        what = f"{target} — {status}" if status else target
    return f"  {location}\n      {what}"


def run_check(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo_root).resolve()
    baseline = load_baseline(Path(args.baseline))
    # Occurrence counts for markdownlint (a file's findings are a property of the
    # file alone); a SET for lychee (its occurrence count is a property of the run
    # — see the module docstring).
    known_markdownlint = Counter(baseline["markdownlint"])
    known_lychee = set(baseline["lychee"])

    try:
        _require_unchanged_linter_policy(baseline, repo_root)
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

    new: list[tuple[str, str, tuple]] = []
    known = 0
    generated = 0
    # Memoized so a file with hundreds of findings is read once, not per finding.
    generated_cache: dict[str, str | None] = {}

    def _generated(path: str) -> str | None:
        if path not in generated_cache:
            generated_cache[path] = generated_target(path, repo_root)
        return generated_cache[path]

    # markdownlint is a multiset: a THIRD identical finding is a genuinely new
    # failure (the count is tree-determined). Use a separate loop so each half's
    # comparison is explicit and a single typed expression.
    seen_markdownlint: Counter[str] = Counter()
    for key, finding in markdownlint:
        if _generated(finding[0]) is not None:
            generated += 1
        seen_markdownlint[key] += 1
        if seen_markdownlint[key] <= known_markdownlint[key]:
            known += 1
        else:
            new.append(("markdownlint", key, finding))

    # lychee is a membership test: its occurrence count is a run property (see the
    # module docstring), so counting it would re-fail inherited debt.
    for key, finding in lychee:
        if _generated(finding[0]) is not None:
            generated += 1
        if key in known_lychee:
            known += 1
        else:
            new.append(("lychee", key, finding))

    print(
        f"docs-lint baseline: {len(new)} new, {known} known (baseline), "
        f"{generated} in generated files"
    )

    if new:
        print("")
        print(f"NEW findings not in {args.baseline} — these fail the required `docs` check:")
        for kind, key, finding in new[:MAX_REPORTED]:
            print(_describe(kind, key, finding))
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
            "(uv run python tools/docs_lint_baseline.py update) in a separate change "
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
    # Returned BARE (repo-relative, no `./`). The `./` prefix a linter needs is
    # added at the invocation site (`_run_markdownlint` / `_run_lychee`), because
    # a filename is data and one starting with `-` must never be read as an
    # option (the CI's own contract, #4449) — but the baseline keys and the
    # report use the bare form, so the prefix must not leak into `_population`.
    return [normalize_path(p) for p in listed]


def _run_markdownlint(files: list[str], repo_root: Path) -> tuple[int, str]:
    proc = subprocess.run(
        ["npx", "--yes", MARKDOWNLINT_PIN, *[f"./{f}" for f in files]],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    return proc.returncode, proc.stdout + proc.stderr


def _require_lychee_binary(lychee_bin: str) -> None:
    """`update` stamps LYCHEE_PIN; verify the binary IS that version.

    The lychee key can carry the status text (for the `error:` placeholder), and
    status text is version-dependent, so a snapshot generated by a DIFFERENT
    lychee is not reproducible by the pinned one while its metadata claims it is.
    The markdownlint half is pinned by the `npx …@0.23.3` invocation; the lychee
    half was invoked as whatever `--lychee-bin` resolved to, unpinned.
    """
    try:
        proc = subprocess.run([lychee_bin, "--version"], capture_output=True, text=True)
    except OSError as exc:
        raise FailClosed(f"lychee binary {lychee_bin!r} could not be run: {exc}") from exc
    reported = f"{proc.stdout} {proc.stderr}".strip()
    match = re.search(r"(\d+\.\d+\.\d+)", reported)
    if proc.returncode != 0 or match is None or match.group(1) != LYCHEE_PIN:
        raise FailClosed(
            f"lychee binary {lychee_bin!r} reported {reported[:120]!r}, but the snapshot "
            f"is pinned to lychee {LYCHEE_PIN} — the key can carry version-dependent "
            "status text, so a different producer writes an unreproducible snapshot"
        )


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
        document = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise FailClosed(
            f"lychee produced unparseable JSON (exit {proc.returncode}): "
            f"{proc.stderr.strip()[:400]}"
        ) from exc
    return _require_lychee_shape(document, f"lychee stdout (exit {proc.returncode})")


def run_update(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo_root).resolve()
    files = _population(repo_root, Path(args.files_from) if args.files_from else None)
    if not files:
        raise FailClosed("no markdown files in the population")
    print(f"docs-lint baseline: sampling {len(files)} markdown file(s)")
    _require_lychee_binary(args.lychee_bin)

    rc, markdownlint_text = _run_markdownlint(files, repo_root)
    if rc not in (0, 1):
        raise FailClosed(f"markdownlint exited {rc} while generating the snapshot")
    # The same positive evidence the CHECK requires: `Linting: N` must equal the
    # population. Without it, a run that silently skipped files writes an
    # INCOMPLETE snapshot — and an incomplete snapshot reads as a complete one,
    # so every skipped finding stays "not on the list" and reds later PRs. The
    # generator must be exactly as fail-closed as the consumer.
    linting = MARKDOWNLINT_LINTING.search(markdownlint_text)
    if linting is None or int(linting.group("files")) != len(files):
        judged = linting.group("files") if linting is not None else "no"
        raise FailClosed(
            f"markdownlint judged {judged} of {len(files)} file(s) while generating the "
            "snapshot — refusing to write an incomplete baseline"
        )
    # Sorted WITH duplicates: the markdownlint half is a multiset (see the module
    # docstring), so two occurrences of one key are two entries.
    markdownlint = sorted(
        markdownlint_key(f) for f in parse_markdownlint_checked(markdownlint_text)
    )

    lychee_document = _run_lychee(files, repo_root, args.lychee_bin)
    # Deduplicated: the lychee half is a SET of `(path, target)` keys. Its
    # occurrence count is a property of the run, not the tree, so writing
    # duplicates would advertise a count the check deliberately does not compare.
    lychee = sorted({lychee_key(f) for f in parse_lychee(lychee_document, repo_root)})

    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo_root, capture_output=True, text=True
    ).stdout.strip()

    baseline = {
        "schema_version": BASELINE_SCHEMA,
        "linter_config": _config_digest(repo_root),
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
                "snapshot edit. #7534 owns the burn-down, but NOTE ITS POPULATION: this "
                "snapshot covers ALL tracked markdown, while #7534 was scoped by a "
                "`docs/`-subtree measurement, and the non-`docs/` remainder includes "
                "tracked `website/apps/dashboard/node_modules` files that a vendored "
                "re-install rewrites, so they cannot be fixed by hand-editing the .md. "
                "See the measured breakdown in the comment on #7534."
            ),
            "regenerate": "uv run python tools/docs_lint_baseline.py update",
        },
        "snapshot": {
            "base_sha": head,
            "generated_at_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "markdownlint": MARKDOWNLINT_VERSION,
            "lychee": f"lychee {LYCHEE_PIN}",
            "population": f"git ls-files '*.md' — {len(files)} files",
            "counts": {"markdownlint": len(markdownlint), "lychee": len(lychee)},
            "variance": (
                "markdownlint findings are deterministic and occurrence-counted. The "
                "lychee half also checks REMOTE links, whose occurrence count varies "
                "between RUNS for reasons no author controls (rate limits, transient "
                "network, TLS, run population), so it is a SET of `(path, target)` "
                "keys, not a count — its pinned ceiling in "
                "tests/test_docs_lint_baseline.py has headroom while the markdownlint "
                "one is exact. Regenerate with `update`; never hand-edit."
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
