#!/usr/bin/env python3
"""Fail-closed integrity for the multi-writer YAML registries (#5373).

WHY THIS FILE EXISTS
--------------------
`config/ci-surfaces.yml` and `config/surface-manifest.yml` are append-only
registries that many lanes edit at once. On a squash-merge trunk the two
appends land on the same anchor and git calls a conflict on lines that do not
disagree — measured 2026-09-26: 25 of 44 non-clean PRs conflicted on
`ci-surfaces.yml` alone. The fix is `merge=union` in `.gitattributes`.

`union` is a git BUILT-IN and needs no driver configuration in any clone. But
it is a **line-level** rule with no idea what YAML is. It keeps BOTH sides' lines
for the conflicting hunk, so it will just as happily emit a duplicate mapping key,
a duplicate list entry, or a structurally broken document — and
`yaml.safe_load` then **silently keeps the LAST duplicate key**. Nothing
downstream can tell that happened.

It is a line-SET union, so byte-identical additions dedupe: two lanes registering
the same test do NOT duplicate it. A duplicate appears when the same semantic
entry is written on DIFFERING lines (the same file plus a different trailing
comment), or when the same key gets DIFFERING values. Both were reproduced
against a real `merge=union` merge (see #5373).

This is not a belt-and-braces addition to an existing guard. The frozen
baseline's own duplicate-name refusal (`surface_manifest._read_manifest`) is keyed
on `name`, and a name-keyed check is BLIND to a duplicate key *inside* a row:
two lanes that each add a row named `zz_x` merge into ONE row carrying both
`served: true` and `served: false`, which `_read_manifest` accepts and this tool
refuses. Verified against a real union merge — the two checks are complementary,
not redundant.

So union is only safe when it is PAIRED WITH A VALIDATOR THAT FAILS CLOSED.
This is that validator. It is wired into the `manifest-integrity` job of
`.github/workflows/python-ci.yml`, which `python-ci-gate` (the required
aggregate check) lists in its `needs` — so a defect union produced reddens the
merge, rather than entering the tree as a fact.

WHAT IT REJECTS (all fail-closed)
---------------------------------
  * **malformed YAML** — an unparseable registry is never "fine, carry on";
  * **a duplicate mapping key** at ANY depth — the exact shape union emits when
    two lanes each add the same key, and the one `yaml.safe_load` hides;
  * **a duplicate scalar entry** in any sequence — two lanes appending the same
    value (the same test file, the same transform) to one list;
  * **a duplicate `name`** among sequence items that carry one (`rows:` /
    `retired:` in the frozen surface baseline) — a name-keyed comparison drops
    all but the last, so the duplicate is unverified content.

It reads the document with `yaml.compose`, which returns the raw node graph
BEFORE the constructor collapses duplicate keys. That is the whole point: a
loader that constructs the mapping cannot see the duplicate.

    uv run python tools/registry_integrity.py                 # both registries
    uv run python tools/registry_integrity.py <file> [...]    # explicit files
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent

# The registries that carry `merge=union` in .gitattributes. Kept here (and not
# only in the workflow) so the default invocation always covers the full set —
# adding a registry to .gitattributes without adding it here would silently
# leave it unvalidated.
REGISTRIES = (
    Path("config/ci-surfaces.yml"),
    Path("config/surface-manifest.yml"),
)


def _scalar(node: yaml.Node) -> str | None:
    """The text of a ScalarNode, or None for a key we cannot compare."""
    return node.value if isinstance(node, yaml.ScalarNode) else None


def _name_of(mapping: yaml.MappingNode) -> str | None:
    """The scalar value of a mapping's `name` key, if it has one."""
    for k_node, v_node in mapping.value:
        if _scalar(k_node) == "name":
            return _scalar(v_node)
    return None


def _walk(fname: str, node: yaml.Node, path: str, problems: list[str]) -> None:
    if isinstance(node, yaml.MappingNode):
        seen_keys: dict[str, int] = {}
        for k_node, v_node in node.value:
            key = _scalar(k_node)
            if key is not None:
                line = k_node.start_mark.line + 1
                if key in seen_keys:
                    problems.append(
                        f"{fname}: duplicate mapping key {key!r} at "
                        f"{path or '<root>'} (lines {seen_keys[key]}, {line}) — "
                        "a YAML loader keeps only the LAST, so this is silent data loss"
                    )
                else:
                    seen_keys[key] = line
            _walk(fname, v_node, f"{path}.{key}" if key is not None else path, problems)
    elif isinstance(node, yaml.SequenceNode):
        seen_scalars: dict[str, int] = {}
        seen_names: dict[str, int] = {}
        for item in node.value:
            line = item.start_mark.line + 1
            if isinstance(item, yaml.ScalarNode):
                if item.value in seen_scalars:
                    problems.append(
                        f"{fname}: duplicate entry {item.value!r} at "
                        f"{path or '<root>'}[] (lines {seen_scalars[item.value]}, {line})"
                    )
                else:
                    seen_scalars[item.value] = line
            elif isinstance(item, yaml.MappingNode):
                name = _name_of(item)
                if name is not None:
                    if name in seen_names:
                        problems.append(
                            f"{fname}: duplicate `name` {name!r} at "
                            f"{path or '<root>'}[] (lines {seen_names[name]}, {line}) — "
                            "a name-keyed comparison drops all but the last"
                        )
                    else:
                        seen_names[name] = line
            _walk(fname, item, f"{path}[]", problems)


def check_registry(path: Path | str) -> list[str]:
    """Every integrity problem in one registry file. Empty list means clean.

    A file that cannot be read or parsed returns a problem, never an empty
    list: a check that cannot read its evidence must not report success.
    """
    p = Path(path)
    shown = str(p)
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        return [f"{shown}: unreadable ({exc}) — a registry that cannot be read is not clean"]
    try:
        node = yaml.compose(text)
    except yaml.YAMLError as exc:
        return [f"{shown}: MALFORMED YAML ({exc})"]
    if node is None:
        return [f"{shown}: empty document"]
    if not isinstance(node, yaml.MappingNode):
        return [f"{shown}: top level is {type(node).__name__}, expected a mapping"]
    problems: list[str] = []
    _walk(shown, node, "", problems)
    return problems


def check(paths: list[Path] | None = None) -> list[str]:
    """Integrity problems across every registry file (defaults to REGISTRIES)."""
    problems: list[str] = []
    for rel in (paths if paths is not None else REGISTRIES):
        problems.extend(check_registry(rel))
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Fail-closed integrity for the merge=union YAML registries (#5373)."
    )
    ap.add_argument("files", nargs="*", type=Path,
                    help="registry files (default: config/ci-surfaces.yml, "
                         "config/surface-manifest.yml)")
    args = ap.parse_args(argv)
    files = args.files or list(REGISTRIES)
    problems = check(files)
    if problems:
        print("❌ registry integrity: union can emit a duplicate and YAML will not "
              "tell you (#5373):", file=sys.stderr)
        for problem in problems:
            print(f"  • {problem}", file=sys.stderr)
        return 1
    print(f"✅ registry integrity: {len(files)} registry file(s) clean — no duplicate "
          "key, no duplicate entry, well-formed YAML")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
