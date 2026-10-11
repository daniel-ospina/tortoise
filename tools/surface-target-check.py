#!/usr/bin/env python3
"""The surface TARGET compliance check (#4185).

OVERRIDES: the frozen-snapshot-as-approval-baseline practice — a snapshot of the implementation cannot be the approved target; the target is read from docs/product/canonical-mcp-tools.md (owner, 26/40, re-confirmed 2026-10-10).

WHY THIS IS NOT tools/surface-guard.py
    `surface-guard.py` answers "does the live declaration MATCH THE FROZEN BASELINE?"
    Its baseline is a snapshot of the implementation, so a 3x-over-target surface can
    pass it with every check green — measured: the gate printed *"matches the approved
    baseline: 82 MCP tools, 150 public SDK methods"* while the owner's approved target
    was roughly a third of it. That is the defect (#4185): the gate called a snapshot of the
    implementation "the approved" surface, and nothing compared the live surface to the
    TARGET.

    This tool answers the OTHER question, and it is a separate exit code on purpose:
    "does the live surface MATCH THE OWNER-APPROVED TARGET?" The gap is real today, so
    folding this into the required `surface-guard` job would red `main` and block every
    unrelated merge while the migration is in flight. It is deliberately a sibling until
    the migration lands; then it can be promoted into the required gate.

THE TARGET IS READ, NEVER TYPED HERE
    A target number USED AS THE TARGET in this file would be the SAME drift class that caused
    #4185: a number maintained beside the document that owns it. Both numbers are parsed out
    of the two product docs at run time. (The mandated `OVERRIDES:` marker above quotes the
    owner's `26/40` as the historical approval record — it is prose, never read as the
    target.) If a doc's wording changes so the numbers can no
    longer be read, this tool REFUSES (non-zero) rather than guessing — a compliance
    check that cannot read the target must not report success.

    The owner's approval itself is READ from `docs/product/canonical-mcp-tools.md`; a
    target doc that is not `approved` is a refusal, not a target.

    MCP target: docs/product/canonical-mcp-tools.md (and cross-checked against
                docs/product/beta-sdk-surface.md's "MCP: N tools").
    SDK target: docs/product/beta-sdk-surface.md ("**N methods** against ... today").

Exit 0 = the live surface equals the approved target.
Exit 1 = they differ (the migration is not done), or the target/approval cannot be read.

Usage:
    uv run python tools/surface-target-check.py
"""

from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below — a module-level
# 3.11+-only import would fail first (D9 shape). Must be the FIRST statement after
# the docstring and `__future__` so an old interpreter never reaches another import.
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/surface-target-check.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/surface-target-check.py`"
    )

import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CANONICAL_DOC = ROOT / "docs" / "product" / "canonical-mcp-tools.md"
BETA_DOC = ROOT / "docs" / "product" / "beta-sdk-surface.md"


class TargetUnreadable(Exception):
    """The approved target could not be read — a refusal, never a skip."""


def _read(path: Path) -> str:
    path = Path(path)
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise TargetUnreadable(f"could not read {path}: {exc}") from exc


def _frontmatter(path: Path) -> dict:
    """The YAML frontmatter of a product doc, or raises when it carries none
    (missing / unclosed / non-mapping / unparseable).

    Deliberately NOT a `{}` fallback: a gate that cannot read its evidence must
    not report success, and an empty mapping reads as "nothing to check".
    """
    import yaml

    text = _read(path)
    if not text.startswith("---"):
        raise TargetUnreadable(
            f"{path} carries no YAML frontmatter — the owner's approval is recorded there, "
            "and a target that cannot be read must not be guessed."
        )
    end = text.find("\n---", 3)
    if end < 0:
        raise TargetUnreadable(f"{path} opens a frontmatter block that is never closed")
    try:
        data = yaml.safe_load(text[3:end])
    except yaml.YAMLError as exc:
        raise TargetUnreadable(f"could not parse the frontmatter of {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise TargetUnreadable(f"{path}'s frontmatter is not a mapping")
    return data


# The MCP target appears in BOTH docs, so each doc must yield its own number and the two
# must agree. The patterns are SCOPED TO THE DOCUMENT THAT OWNS THE PHRASE (#4185 review
# P3): `beta target (is|becomes) N` is the beta list's own count when it appears in the
# canonical doc, NOT the MCP target, so running the whole set over both docs made a future
# amendment that moved only the canonical `MCP target count` look ambiguous
# (`len(mcp_numbers) != 1`) and refuse a correctly-readable target. `[\s*]*` lets a phrase
# span a markdown bold boundary and a line wrap.
# The canonical doc states it as `MCP target count is unchanged at 26` AND as `MCP target
# is 26`; the single optional-chain pattern matches both. It must NEVER be `beta target`,
# which names the beta list's OWN count rather than the MCP target's — the two can move
# independently, so reading the wrong phrase is a wrong number waiting to happen.
_CANONICAL_MCP_PATTERNS = (
    re.compile(
        r"MCP[\s*]*target[\s*]*(?:count[\s*]*)?is[\s*]*(?:unchanged[\s*]*at[\s*]*)?(\d+)",
        re.I,
    ),
)
# The beta doc states the same number in its own voice.
_BETA_MCP_PATTERNS = (re.compile(r"\*\*MCP:\s*(\d+)\s*tools\*\*", re.I),)
# The SDK target: beta-sdk-surface.md's "**N methods** against **M** today".
_SDK_PATTERNS = (re.compile(r"\*\*(\d+)\s*methods\*\*\s*against\s*\*\*\d+\*\*", re.I),)


def _numbers(patterns, text: str) -> set[int]:
    found: set[int] = set()
    for pattern in patterns:
        found |= {int(m.group(1)) for m in pattern.finditer(text)}
    return found


def read_targets(canonical: Path | None = None, beta: Path | None = None) -> dict:
    """The owner-approved target, READ from the two product docs.

    Returns `{"mcp": int, "sdk": int, "approval": dict, "sources": dict}`. Refuses
    (raises `TargetUnreadable`) when the docs do not carry a single unambiguous
    number for each leg, or when the canonical doc does not record an approval.
    """
    canonical = Path(canonical or CANONICAL_DOC)
    beta = Path(beta or BETA_DOC)

    approval_fm = _frontmatter(canonical)
    if not approval_fm.get("approval_status"):
        raise TargetUnreadable(
            f"{canonical} records no `approval_status`, so it is not an approval record."
        )
    approval = {
        "status": str(approval_fm.get("approval_status") or "pending-owner-approval"),
        "principal": str(approval_fm["approvedBy"]) if approval_fm.get("approvedBy") else None,
        "pr": str(approval_fm["approval_pr"]) if approval_fm.get("approval_pr") else None,
        "date": str(approval_fm["approved"]) if approval_fm.get("approved") is not None else None,
    }
    if approval["status"] != "approved":
        raise TargetUnreadable(
            f"{canonical} records approval_status={approval['status']!r}, so there is no "
            "owner-approved target to comply with."
        )
    # An `approved` with no principal and no PR is not an approval record (the guard applies
    # the same completeness test). Accepting it here would let the COMPLIANCE instrument
    # honour an approval the gate itself would reject.
    if not approval["principal"] or not approval["pr"]:
        raise TargetUnreadable(
            f"{canonical} records approval_status=approved but "
            f"approvedBy={approval['principal']!r} / approval_pr={approval['pr']!r} — an "
            "approval record must carry a principal and a PR."
        )
    # The BETA doc is the sole source of the SDK target, so it must be an approved record too.
    # It records a principal and a date but no PR (unlike the canonical doc), so the test is
    # `approved` + a named principal — NOT `approval_pr`.
    beta_approval = _frontmatter(beta)
    if (
        str(beta_approval.get("approval_status") or "") != "approved"
        or not beta_approval.get("approvedBy")
    ):
        raise TargetUnreadable(
            f"{beta} is not an approved record "
            f"(approval_status={beta_approval.get('approval_status')!r}, "
            f"approvedBy={beta_approval.get('approvedBy')!r}) — the SDK target cannot be "
            "taken from an unapproved document."
        )

    # THE MCP REDUNDANCY IS THE CONTROL. The MCP target is stated in BOTH docs on purpose, so
    # a one-doc edit cannot move it silently. A UNION would tolerate a doc that yields NOTHING
    # and the "cross-checked" claim would be decorative, so require EACH doc to carry the
    # number and the two per-doc sets to agree.
    canonical_mcp = _numbers(_CANONICAL_MCP_PATTERNS, _read(canonical))
    beta_mcp = _numbers(_BETA_MCP_PATTERNS, _read(beta))
    if not canonical_mcp:
        raise TargetUnreadable(
            f"{canonical} yields no MCP target — the target cannot be read from it alone."
        )
    if not beta_mcp:
        raise TargetUnreadable(
            f"{beta} yields no MCP target — the cross-check cannot be performed."
        )
    if canonical_mcp != beta_mcp:
        raise TargetUnreadable(
            "the two product docs disagree on the MCP target: "
            f"{sorted(canonical_mcp)} in {canonical.name} vs {sorted(beta_mcp)} in "
            f"{beta.name}."
        )
    mcp_numbers = canonical_mcp
    if len(mcp_numbers) != 1:
        raise TargetUnreadable(
            "the two product docs do not yield exactly one MCP target "
            f"(found {sorted(mcp_numbers)}). A target this tool cannot read "
            "unambiguously must not be guessed."
        )
    sdk_numbers = _numbers(_SDK_PATTERNS, _read(beta))
    if len(sdk_numbers) != 1:
        raise TargetUnreadable(
            f"{beta} does not yield exactly one SDK target (found {sorted(sdk_numbers)})."
        )

    return {
        "mcp": next(iter(mcp_numbers)),
        "sdk": next(iter(sdk_numbers)),
        "approval": approval,
        "sources": {"mcp": canonical, "sdk": beta},
    }


def live_surface() -> dict:
    """The live surface, EXECUTED from the declaration — never read as text."""
    try:
        from tortoise.sdk import TortoiseSDK
        from tortoise.tool_registry import TOOL_REGISTRY
    except Exception as exc:
        raise TargetUnreadable(
            f"could not import the surface declaration: {type(exc).__name__}: {exc}"
        ) from exc
    tools = sorted(entry.name for entry in TOOL_REGISTRY)
    sdk = sorted(
        name
        for name in dir(TortoiseSDK)
        if not name.startswith("_") and callable(getattr(TortoiseSDK, name))
    )
    return {"mcp": len(tools), "sdk": len(sdk), "tools": tools, "sdk_methods": sdk}


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv if argv is None else argv
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--canonical-doc", default=str(CANONICAL_DOC))
    parser.add_argument("--beta-doc", default=str(BETA_DOC))
    args = parser.parse_args(argv[1:])

    try:
        targets = read_targets(args.canonical_doc, args.beta_doc)
        live = live_surface()
    except TargetUnreadable as exc:
        print(f"::error::{exc}")
        print(f"REFUSED {exc}")
        return 1

    approval = targets["approval"]
    print("surface target compliance (#4185) — live surface vs the owner-approved target")
    print(f"  approval:  READ from {targets['sources']['mcp']}")
    print(
        f"             status={approval['status']} principal={approval['principal']} "
        f"pr={approval['pr']} date={approval['date']}"
    )
    print(f"  target:    {targets['mcp']} MCP tools / {targets['sdk']} SDK methods")
    print(f"  live:      {live['mcp']} MCP tools / {live['sdk']} SDK methods")
    print("")

    problems: list[str] = []
    for label, live_n, target_n in (
        ("MCP tools", live["mcp"], targets["mcp"]),
        ("SDK methods", live["sdk"], targets["sdk"]),
    ):
        delta = live_n - target_n
        ratio = (live_n / target_n) if target_n else float("inf")
        verdict = "OK" if delta == 0 else ("OVER TARGET" if delta > 0 else "UNDER TARGET")
        print(
            f"  {label:12s} live {live_n:>4} → target {target_n:>4}   "
            f"(Δ {delta:+d}, {ratio:.1f}x) {verdict}"
        )
        if delta != 0:
            problems.append(
                f"{label}: live {live_n} != approved target {target_n} (Δ {delta:+d})"
            )

    print("")
    if problems:
        print("::error::the live agent-facing surface does not match the owner-approved target")
        for problem in problems:
            print(f"FAIL {problem}")
        print(
            f"\nThe target is the owner's ruling ({targets['mcp']} MCP tools / "
            f"{targets['sdk']} SDK methods), read from "
            f"{targets['sources']['mcp']} and {targets['sources']['sdk']}. This checks "
            "COMPLIANCE WITH THE TARGET; it does not perform the migration and it is "
            "deliberately not in the required CI gate while the gap is open."
        )
        return 1
    print("OK — the live surface matches the owner-approved target.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
