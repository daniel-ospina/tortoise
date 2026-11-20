#!/usr/bin/env python3
"""The owner-approved MCP tool NAMES, READ from the two documents that own them.

WHY THIS EXISTS (#13036 review F1)
    `tools/surface-target-check.py` reads the approved target COUNT (27) out of the two
    product docs and refuses when it cannot. Nothing read the approved NAMES:
    `tools/surface_capstone_probe.py` carried a hand-typed 27-tuple whose docstring
    claimed a test bound it to the document — and `git grep` found that test name in
    exactly one place, the docstring itself. So the DENOMINATOR of the capstone's two
    headline numbers ("22/27 tools") had no guard against drift, which is the same
    defect `#4185` was filed about, one level down: a hand-typed count beside the
    document that owns it.

    The probe's SDK half already refused at run time when its transcription and the
    declaration disagreed. This module is the MCP half of that symmetry.

WHERE THE NAMES COME FROM — two sources, each with its own reason
    1. `docs/product/beta-sdk-surface.md`, the **MCP twin** column of rows 1-40: the
       owner's beta surface. A row whose twin is `—` contributes NO name (the
       constructor, `close`, `write_knowledge_batch`, and the whole tenancy block); a
       row whose twin is a name contributes it, with a trailing parenthetical dropped
       (row 30's `graph_set_recording (recording only)` contributes
       `graph_set_recording`). That is 26 of the 27.
    2. `docs/product/canonical-mcp-tools.md`, the **#3540 amendment**:
       `tortoise_manage_sources` is the 27th, and it is deliberately NOT on the beta
       list — the amendment says the MCP target moved to 27 *because* that tool was
       added to the merged list. So the name is read from the amendment, not typed
       here.

    Both docs must record the SAME MCP target count, and the number of names read must
    equal it. Otherwise this REFUSES: a silent short read is how a denominator shrinks
    while every "N/27" figure still looks measured.

NOT AN APPROVAL
    Reading a name from an `approved` document does not approve anything, and this
    module cannot tell an approved addition from an unapproved one. The approval lives
    in `docs/product/canonical-mcp-tools.md` (and, for the SDK, in
    `tortoise/__all__.py`); this reader only makes the probe measure against
    the document instead of against a copy of it. See `AGENTS.md` — MCP/SDK surface
    approval.

WHY NOT `tools/surface-target-check.py`
    That tool lives in a file whose name is not importable (`surface-target-check.py`),
    answers a different question (a count, and a live-vs-target compliance verdict), and
    is deliberately outside required CI. This reader is imported by the probe and by the
    test that pins it, so it must be an ordinary module.

    OVERRIDES: the frozen-snapshot-as-approval-baseline practice — a snapshot of the
    implementation cannot be the approved target; the target is read from
    docs/product/canonical-mcp-tools.md (owner, 27 MCP tools) and from the declared SDK
    surface in tortoise/__all__.py (owner, 40 methods; cross-checked against
    docs/product/beta-sdk-surface.md) — never from a count beside them. Same ruling, same
    marker as `tools/surface-guard.py`, `tools/surface_manifest.py` and
    `tools/surface-target-check.py`; this is its NAMES half.

Usage:
    uv run python tools/mcp_surface_approval.py            # print the names, exit 0
    uv run python tools/mcp_surface_approval.py --count    # print the count both docs state
    uv run python tools/mcp_surface_approval.py --check <name>[,<name>...]
                                                          # exit 1 when the set disagrees
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

CANONICAL_DOC = ROOT / "docs" / "product" / "canonical-mcp-tools.md"
BETA_DOC = ROOT / "docs" / "product" / "beta-sdk-surface.md"


class ApprovalUnreadable(Exception):
    """The approved NAMES could not be read. A refusal, never a skip.

    Same contract as `tools/surface-target-check.py::TargetUnreadable`: a check that
    cannot read its evidence must not report success, and an empty tuple returned here
    would read as "the owner approved nothing".
    """


# The MCP target count is stated in BOTH docs on purpose, so a one-doc edit cannot move
# it silently — the same redundancy `tools/surface-target-check.py` relies on. These are
# the same two patterns, kept as its sibling copy (that file is not importable):
#
#   * canonical: `the MCP target count is 27` — PRESENT TENSE only (`is`, never `was`),
#     or the historical `was 26 at that ruling` sentence would be counted too, and
#     `beta target` names the beta list's own count rather than the MCP target's;
#   * beta: `**MCP: 27 tools**`.
_MCP_COUNT_PATTERNS = {
    "canonical": (
        re.compile(
            r"MCP[\s*]*target[\s*]*(?:count[\s*]*)?is[\s*]*(?:unchanged[\s*]*at[\s*]*)?(\d+)",
            re.I,
        ),
    ),
    "beta": (re.compile(r"\*\*MCP:\s*(\d+)\s*tools\*\*", re.I),),
}

#: A table row of `docs/product/beta-sdk-surface.md`, keyed on the leading row number so
#: prose tables elsewhere in the doc are not read as surface rows.
_ROW = re.compile(r"\|\s*(\d+)\s*\|")

#: The #3540 amendment's own sentence: *"the merged list gains `tortoise_manage_sources`"*.
_AMENDMENT_TOOL = re.compile(r"merged[\s*]+list[\s*]+gains[\s*]+`tortoise_([a-z0-9_]+)`", re.I)

#: A cell that carries no MCP twin (an em dash, an en dash or a hyphen).
_NO_TWIN = {"", "—", "–", "-"}

#: What a tool NAME may look like, before the `tortoise_` prefix is added back.
_NAME = re.compile(r"^[a-z][a-z0-9_]*$")


def _read(path: Path) -> str:
    path = Path(path)
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ApprovalUnreadable(f"could not read {path}: {exc}") from exc


def _frontmatter(path: Path, doc: str) -> dict:
    """The verified approval frontmatter of a product doc.

    FAIL CLOSED, exactly as the sibling readers do: a doc that carries no frontmatter,
    no `approval_status`, or an unapproved status is a REFUSAL — an approval record
    whose provenance was deleted must not be read as a target.
    """
    import yaml

    text = _read(path)
    if not text.startswith("---"):
        raise ApprovalUnreadable(
            f"{path} carries no YAML frontmatter — the owner's approval is recorded "
            "there, and names read from an unapproved document are not a target."
        )
    end = text.find("\n---", 3)
    if end < 0:
        raise ApprovalUnreadable(f"{path} opens a frontmatter block that is never closed")
    try:
        data = yaml.safe_load(text[3:end])
    except yaml.YAMLError as exc:
        raise ApprovalUnreadable(f"could not parse the frontmatter of {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ApprovalUnreadable(f"{path}'s frontmatter is not a mapping")
    if str(data.get("approval_status") or "") != "approved":
        raise ApprovalUnreadable(
            f"{path} records approval_status={data.get('approval_status')!r}, so there is "
            f"no owner-approved {doc} to read names from."
        )
    if not data.get("approvedBy"):
        raise ApprovalUnreadable(
            f"{path} records approval_status=approved but names no `approvedBy` principal "
            "— an approval record must carry one."
        )
    return data


def _cells(line: str) -> list[str]:
    """Split one markdown table row into cells, honouring `\\|` escapes.

    `str.split("|")` would break a row whose CELL text carries an escaped pipe (the
    canonical doc's tables do), silently shifting every later cell — and with it the
    MCP-twin column. Cheap to do right, so done right.
    """
    cells: list[str] = []
    current: list[str] = []
    i = 0
    while i < len(line):
        ch = line[i]
        if ch == "\\" and i + 1 < len(line) and line[i + 1] == "|":
            current.append("|")
            i += 2
            continue
        if ch == "|":
            cells.append("".join(current))
            current = []
            i += 1
            continue
        current.append(ch)
        i += 1
    cells.append("".join(current))
    return cells


def _twin_of(line: str, lineno: int) -> str | None:
    """The MCP-twin NAME of one beta-doc row, or `None` when the row has no twin."""
    cells = _cells(line)
    if len(cells) < 6:
        raise ApprovalUnreadable(
            f"row at line {lineno} has {len(cells)} cells; the surface table is "
            "`| # | Name | Does | MCP twin | Who needs it |` and the MCP-twin column "
            f"cannot be located: {line.strip()[:120]}"
        )
    twin = cells[4].strip()
    if twin in _NO_TWIN:
        return None
    # `graph_set_recording (recording only)` — the parenthetical qualifies the twin, it
    # is not part of the name.
    twin = re.sub(r"\s*\([^)]*\)\s*$", "", twin).strip()
    twin = twin.strip("`").strip()
    if not _NAME.match(twin):
        raise ApprovalUnreadable(
            f"row at line {lineno} names an MCP twin that is not a tool name: {twin!r}"
        )
    return twin


def _beta_twins(beta: Path) -> list[str]:
    """Every `MCP twin` name of the beta surface's 40 rows, in row order."""
    names: list[str] = []
    for lineno, line in enumerate(_read(beta).splitlines(), 1):
        if not _ROW.match(line):
            continue
        twin = _twin_of(line, lineno)
        if twin is not None:
            names.append(twin)
    return names


def _amendment_tools(canonical: Path) -> list[str]:
    """Tool names the canonical doc's amendments ADD to the merged list.

    Today that is the #3540 ruling's `tortoise_manage_sources`. Read rather than typed:
    the amendment is the record that moved the target from 26 to 27, so the name it
    introduces has to come from it.
    """
    return sorted({m.group(1) for m in _AMENDMENT_TOOL.finditer(_read(canonical))})


def mcp_target_count(beta: Path | None = None, canonical: Path | None = None) -> int:
    """The MCP target count, READ from both docs — which must state the same number."""
    beta = Path(beta or BETA_DOC)
    canonical = Path(canonical or CANONICAL_DOC)
    found: list[int] = []
    for label, path, patterns in (
        ("canonical", canonical, _MCP_COUNT_PATTERNS["canonical"]),
        ("beta", beta, _MCP_COUNT_PATTERNS["beta"]),
    ):
        numbers: set[int] = set()
        text = _read(path)
        for pattern in patterns:
            numbers |= {int(m.group(1)) for m in pattern.finditer(text)}
        if len(numbers) != 1:
            raise ApprovalUnreadable(
                f"{path} states {sorted(numbers) or 'no'} MCP target count(s); exactly one "
                f"is required in the {label} doc — a target that cannot be read is not a "
                "target."
            )
        found.append(numbers.pop())
    if len(set(found)) != 1:
        raise ApprovalUnreadable(
            f"the two documents disagree about the MCP target: canonical says {found[0]}, "
            f"beta says {found[1]}. One of them moved without the other."
        )
    return found[0]


def load_approved_tools(
    beta: Path | None = None, canonical: Path | None = None
) -> tuple[str, ...]:
    """The owner-approved MCP tool names, in approval-document order.

    Raises `ApprovalUnreadable` — never returns a short tuple — when a doc is
    unapproved/unreadable, when the two docs disagree about the count, or when the
    names read do not add up to the count they state.
    """
    beta = Path(beta or BETA_DOC)
    canonical = Path(canonical or CANONICAL_DOC)
    _frontmatter(canonical, "MCP surface")
    _frontmatter(beta, "beta surface")

    names = _beta_twins(beta)
    names.extend(_amendment_tools(canonical))

    if len(names) != len(set(names)):
        duplicated = sorted({n for n in names if names.count(n) > 1})
        raise ApprovalUnreadable(
            f"the approval documents yield a duplicated MCP tool name: {duplicated} — a "
            "duplicate would be probed twice and counted twice."
        )

    count = mcp_target_count(beta=beta, canonical=canonical)
    if len(names) != count:
        raise ApprovalUnreadable(
            f"the documents state an MCP target of {count} but yield {len(names)} approved "
            f"name(s): {list(names)}. A numerator measured against a denominator the "
            "documents do not state is the #4185 defect one level down — fix the doc or "
            "the reader, never the arithmetic."
        )
    return tuple(names)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", action="store_true", help="print the stated count only")
    parser.add_argument("--check", default=None,
                        help="comma-separated names that must equal the approved set")
    args = parser.parse_args(argv[1:] if argv else None)
    try:
        names = load_approved_tools()
        count = mcp_target_count()
    except ApprovalUnreadable as exc:
        print(f"REFUSED — {exc}")
        return 1
    if args.check is not None:
        given = tuple(n for n in args.check.split(",") if n)
        if given != names:
            print("REFUSED — the given list and the approval documents disagree:")
            print(f"  only in the approval documents: {sorted(set(names) - set(given))}")
            print(f"  only in the given list:        {sorted(set(given) - set(names))}")
            if set(given) == set(names):
                print("  (same set, different ORDER — the documents' order is the order)")
            return 1
        print(f"OK — {len(names)} names match the approval documents")
        return 0
    if args.count:
        print(count)
        return 0
    print(f"{len(names)} approved MCP tools (the target count both documents state: {count}):")
    for i, name in enumerate(names, 1):
        print(f"  {i:2d}. {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
