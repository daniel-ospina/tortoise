#!/usr/bin/env python3
"""S2 census — is truth<->cache divergence OBSERVED, or merely checkable?

Counts PRODUCTION (non-test) call sites of the three modules the two-store
chain needs wired together:

  * tortoise/reconciliation.py::reconcile      — compares the two stores
  * tortoise/record_projection.py::replay      — journal -> Postgres
  * tortoise/cache_projection.py::read_record_view — Postgres -> cache input
  * FalkorProjection.rebuild_from_record       — the cache rebuild

The census is a COUNT of call sites, so a line of prose that mentions a call
is not one: `record_projection.replay(` inside `record_writer.py`'s docstring
matched the original line filter and inflated the count (#9189 review). Lines
that fall inside a string literal (docstrings included) are therefore skipped,
read from `tokenize`'s STRING/FSTRING spans rather than guessed from indentation.

A second precision fix, same review: `reconciliation.reconcile` used to match
ANY `from .reconciliation import …`, so an import of `reconcile_views` — a
different function, and what the #9189 trigger imports — counted as a caller of
`reconcile` (the pattern `reconciliation import reconcile` is a PREFIX of
`…import reconcile_views`). The pattern now requires the NAME `reconcile`, and
counts a CALL `reconcile(` as well, because the multi-line import form
(`from tortoise.reconciliation import (… reconcile, …)`) carries the name on a
line of its own. A definition — `def reconcile(` / `async def reconcile(` — is
not a call site.

⚠️ The bare-call alternative's lookbehind (`(?<![\\w.])`) deliberately rejects a
DOTTED receiver, so the MODULE-QUALIFIED spelling
(`reconciliation.reconcile(a, b)`, the natural one for a module-level function
imported as a module) needs its OWN alternative. Dropping it was a measurement
blind spot, not a precision win: the census would have kept reporting `0` for a
`reconcile` wired as `reconciliation.reconcile(…)` — a stale-evidence artifact
of exactly the kind this file exists to prevent. The explicit `reconciliation.`
prefix cannot collide with `self.reconcile(` / `consistency.reconcile(`, which
is what the lookbehind protects against. Both spellings are pinned by
`tests/test_7950_capstone_probe.py::test_the_census_pattern_sees_every_call_
spelling`.

A checkable mechanism with zero production callers cannot observe anything.
Populations are stated (which directories were searched).
"""
from __future__ import annotations

import io
import pathlib
import re
import tokenize

PROD_DIRS = ["tortoise", "tools", "scripts"]
TESTS = pathlib.Path("tests")
TARGETS = {
    "reconciliation.reconcile": re.compile(
        r"(?<![\w.])reconcile\("
        r"|reconciliation\.reconcile\("
        r"|from [\w.]*reconciliation import[^\n]*\breconcile\b(?!_)"),
    "record_projection.replay": re.compile(r"record_projection\.replay\(|\brp\.replay\(|from tortoise.record_projection import[^\n]*\breplay\b"),
    "cache_projection.read_record_view": re.compile(r"read_record_view\("),
    "FalkorProjection.rebuild_from_record": re.compile(r"\.rebuild_from_record\("),
}


def _prose_lines(text: str) -> set[int]:
    """Lines lying INSIDE a multi-line string literal — docstrings and prose.

    Only continuation lines are excluded, never a line that merely CONTAINS a
    string: `view = cp.read_record_view(conn, org_id="o")` is a call site whose
    line also carries string literals, and a line-granularity exclusion would
    silently drop it (measured: it took the test population's
    `read_record_view` count from 2 to 0).
    """
    covered: set[int] = set()
    # Python 3.12 tokenizes an f-string into FSTRING_START/…/FSTRING_END rather
    # than one STRING token, so a multi-line f-string's prose lines have to be
    # collected from those spans — otherwise the docstring above would be
    # claiming a coverage the code does not have (`reconcile(` inside a
    # multi-line f-string counted as a call site). `FSTRING_*` does not exist
    # before 3.12, where the f-string arrives as a STRING and the first branch
    # covers it.
    fstring_start = getattr(tokenize, "FSTRING_START", None)
    fstring_end = getattr(tokenize, "FSTRING_END", None)
    open_fstrings: list[tuple[int, int]] = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.STRING and tok.end[0] > tok.start[0] + 1:
                covered.update(range(tok.start[0] + 1, tok.end[0] + 1))
            elif fstring_start is not None and tok.type == fstring_start:
                open_fstrings.append(tok.start)
            elif fstring_end is not None and tok.type == fstring_end:
                # Innermost first: nesting is a stack, not a single variable.
                start = open_fstrings.pop() if open_fstrings else tok.start
                if tok.end[0] > start[0] + 1:
                    covered.update(range(start[0] + 1, tok.end[0] + 1))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        # An unparseable file is not this census's subject; its lines simply
        # keep the conservative (over-counting) reading.
        return set()
    return covered


def census(roots, pattern):
    """The hit lines for ONE target pattern. Thin wrapper over :func:`census_all`."""
    return census_all(roots, {"": pattern})[""]


def census_all(roots, patterns):
    """`{target name: hit lines}` for every pattern, in ONE walk of `roots`.

    Every file is read and tokenized ONCE for all targets: the per-target
    implementation re-read and re-tokenized the whole tree per target (eight
    full passes for one run, measured 5m12s), which made re-measuring the log in
    a test unaffordable — and an artifact nobody can afford to re-measure is how
    a stale flag survives. Per-target hit ORDER is unchanged (roots, then files,
    then lines), so the output is byte-identical to the per-target walk's.
    """
    hits: dict[str, list[str]] = {name: [] for name in patterns}
    for root in roots:
        for f in pathlib.Path(root).rglob("*.py"):
            if "__pycache__" in str(f):
                continue
            text = f.read_text(errors="replace")
            prose = _prose_lines(text)
            lines = text.splitlines()
            for name, pattern in patterns.items():
                for i, line in enumerate(lines, 1):
                    if pattern.search(line) and not line.strip().startswith("#") \
                            and not re.match(r"\s*(?:async\s+)?def\s", line) \
                            and i not in prose:
                        hits[name].append(f"{f}:{i}: {line.strip()[:100]}")
    return hits


def main(roots=None):
    """Print the census. Importable, so a test can RE-MEASURE it.

    The committed log is a claim about the tree; a pin that compares the log to
    itself (or to prose that quotes it) can never detect the tree moving out
    from under it. `tests/test_7950_capstone_probe.py` imports this module and
    compares a live `census()` against the log for exactly that reason.
    """
    roots = PROD_DIRS if roots is None else roots
    print(f"production population: {roots}/**/*.py")
    production = census_all(roots, TARGETS)
    for name in TARGETS:
        hits = production[name]
        print(f"\n== {name}: {len(hits)} production call site(s) ==")
        for h in hits:
            print(f"  {h}")

    print(f"\ntest population: {TESTS}/*.py")
    in_tests = census_all([str(TESTS)], TARGETS)
    for name in TARGETS:
        print(f"  {name}: {len(in_tests[name])} test-file hit line(s)")


if __name__ == "__main__":
    main()
