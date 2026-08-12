#!/usr/bin/env python3
"""Stage harness onboarding variants for deployment (epic #529).

Single concat point: each ``variants/<harness>-header.md`` is concatenated
with the canonical ``AGENT_ONBOARDING.md`` body (drift-proof by construction
— headers never contain the question flow; the canonical body is embedded
verbatim as a suffix). Outputs mirror the website/ layout:

    <out>/onboarding-prompt.md            (canonical copy, unchanged path)
    <out>/onboarding/<harness>.md         (claude-code, codex, cursor, pi)

Used by ``.github/workflows/deploy-pages.yml`` and by
``tests/test_onboarding_variants.py``.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CANONICAL = HERE / "AGENT_ONBOARDING.md"
VARIANTS_DIR = HERE / "variants"
SEPARATOR = "\n\n---\n\n"
HARNESSES: tuple[str, ...] = ("claude-code", "codex", "cursor", "pi")


def stage(out_dir: Path) -> int:
    """Write staged artifacts into ``out_dir``. Returns a process exit code."""
    if not CANONICAL.exists():
        print(f"error: canonical prompt missing: {CANONICAL}", file=sys.stderr)
        return 1
    canonical = CANONICAL.read_text(encoding="utf-8")
    if not canonical.strip():
        print(f"error: canonical prompt empty: {CANONICAL}", file=sys.stderr)
        return 1

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "onboarding-prompt.md").write_text(canonical, encoding="utf-8")

    onboarding_dir = out_dir / "onboarding"
    onboarding_dir.mkdir(parents=True, exist_ok=True)
    for harness in HARNESSES:
        header_path = VARIANTS_DIR / f"{harness}-header.md"
        if not header_path.exists():
            print(f"error: missing variant header: {header_path}", file=sys.stderr)
            return 1
        header = header_path.read_text(encoding="utf-8")
        if not header.strip():
            print(f"error: empty variant header: {header_path}", file=sys.stderr)
            return 1
        staged = header.rstrip() + SEPARATOR + canonical.lstrip()
        (onboarding_dir / f"{harness}.md").write_text(staged, encoding="utf-8")
        print(f"staged onboarding/{harness}.md ({len(staged)} bytes)")
    print(f"staged onboarding-prompt.md ({len(canonical)} bytes)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    default_out = HERE.parent.parent / "website"
    parser.add_argument(
        "--out",
        type=Path,
        default=default_out,
        help=f"output directory mirroring the website layout (default: {default_out})",
    )
    args = parser.parse_args()
    return stage(args.out)


if __name__ == "__main__":
    sys.exit(main())
