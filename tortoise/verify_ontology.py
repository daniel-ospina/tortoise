#!/usr/bin/env python3
"""Pre-flight ontology validator — catch Schema B drift before batch LLM operations.

Validates that _template.md matches AGENTS.md frontmatter contract before
expensive operations like enrich_frontmatter.py batch mode.

Usage:
  python3 operations/memory/verify_ontology.py                 # exit 0 = good
  python3 operations/memory/verify_ontology.py --quiet         # only exit code
  python3 operations/memory/verify_ontology.py --sample 10     # check 10 docs
  python3 operations/memory/verify_ontology.py --sample 0      # skip doc check

Exit codes: 0 = pass, 1 = contract mismatch, 2 = missing infrastructure, 3 = doc failures
"""
from __future__ import annotations
import argparse, re, sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
TEMPLATE = REPO / "docs" / "_template.md"
AGENTS = REPO / "AGENTS.md"
VOCAB = REPO / "docs" / "teams" / "organisation-design-team" / "data" / "controlled_vocabulary.md"
TEAMS_DIR = REPO / "docs" / "teams"

FRONTMATTER_RE = re.compile(r'^---\s*\n(.*?)\n---', re.DOTALL)
FIELD_LINE = re.compile(r'^(\S+):')


def parse_frontmatter_fields(text: str) -> set[str]:
    m = FRONTMATTER_RE.match(text)
    if not m:
        return set()
    fields = set()
    for line in m.group(1).split("\n"):
        fl = FIELD_LINE.match(line.strip())
        if fl:
            fields.add(fl.group(1))
    return fields


def extract_agents_contract() -> set[str]:
    """Extract required fields from AGENTS.md §Frontmatter Population."""
    text = AGENTS.read_text(encoding="utf-8")

    section = False
    fields = set()
    for line in text.split("\n"):
        if "## Frontmatter Population" in line:
            section = True
            continue
        if section:
            if line.startswith("## ") and "Frontmatter" not in line:
                break
            m = re.match(r'^- `([^`]+)`', line.strip())
            if m:
                fields.add(m.group(1))
    return fields


def verify_contract(schema_b_fields: set[str], agents_fields: set[str]) -> list[str]:
    """Check all AGENTS.md-mandated fields exist in _template.md Schema B."""
    failures = []
    for field in agents_fields:
        if field not in schema_b_fields:
            failures.append(
                f"MISMATCH: AGENTS.md requires '{field}' but _template.md Schema B is missing it"
            )
    return failures


def sample_docs(sample_n: int = 5) -> list[str]:
    """Sample docs for contract-required field presence."""
    if not TEAMS_DIR.exists():
        return ["FAIL: docs/teams/ directory not found"]

    docs = sorted(
        f for f in TEAMS_DIR.rglob("*.md")
        if "_templates" not in str(f) and ".git" not in str(f)
    )
    if not docs:
        return []

    import random
    random.seed(42)
    sample = random.sample(docs, min(sample_n, len(docs)))

    agents_fields = extract_agents_contract()
    failures = []
    for fp in sample:
        text = fp.read_text(encoding="utf-8")
        if len(text) < 50:
            continue
        fields = parse_frontmatter_fields(text)
        for field in agents_fields:
            if field not in fields:
                try:
                    rel = fp.relative_to(TEAMS_DIR)
                except ValueError:
                    rel = fp
                failures.append(f"DOC_MISSING: {rel} missing '{field}'")
    return failures


def main():
    p = argparse.ArgumentParser(
        description="Pre-flight ontology validator — catch Schema B drift before batch LLM ops"
    )
    p.add_argument("--quiet", action="store_true")
    p.add_argument("--sample", type=int, default=5, metavar="N")
    args = p.parse_args()

    # 1. Check infrastructure files exist
    for path, label in [(TEMPLATE, "_template.md"), (AGENTS, "AGENTS.md"),
                         (VOCAB, "controlled_vocabulary.md")]:
        if not path.exists():
            print(f"MISSING: {label} ({path})", file=sys.stderr)
            sys.exit(2)

    # 2. Parse Schema B fields from _template.md
    template_text = TEMPLATE.read_text(encoding="utf-8")
    schema_b_fields = parse_frontmatter_fields(template_text)

    # 3. Parse AGENTS.md contract
    agents_fields = extract_agents_contract()
    if not agents_fields:
        print("FAIL: could not parse AGENTS.md §Frontmatter Population", file=sys.stderr)
        sys.exit(1)

    # 4. Check _template.md has required AGENTS.md fields
    failures = verify_contract(schema_b_fields, agents_fields)

    # 5. Check controlled_vocabulary has team + domain sections
    vocab_text = VOCAB.read_text(encoding="utf-8")
    if "## Teams" not in vocab_text:
        failures.append("FAIL: controlled_vocabulary.md missing ## Teams section")
    if "## Domains" not in vocab_text:
        failures.append("FAIL: controlled_vocabulary.md missing ## Domains section")

    # 6. Sample docs
    if args.sample > 0:
        failures += sample_docs(args.sample)

    if failures:
        for f in failures:
            print(f"  {f}", file=sys.stderr)
        code = 3 if any(f.startswith("DOC_MISSING") for f in failures) else 1
        sys.exit(code)

    if not args.quiet:
        print("ontology verified — Schema B matches AGENTS.md contract")


if __name__ == "__main__":
    main()
