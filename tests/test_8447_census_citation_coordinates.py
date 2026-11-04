"""#8447 C1 — the census's citations must NAME something, and must not be coordinates.

THE INVARIANT, and why it is two halves. `docs/architecture/BOUNDED-OPERATION-CONTRACT.md`
carries an evidence column that points at the code each row describes. It used line
numbers until #10622 replaced every one of them with a `<module>::<name>` symbol, because a
line number is exact when written and wrong after the next unrelated merge (that document
recorded three re-derivations before the advice was taken). A symbol does not drift with a
merge — but it drifts with a **rename**, and nothing asserted the other half: that each
symbol names something that still exists in the module it names.

WHAT THIS FILE PINS THAT `tests/test_8447_truncation_marker.py` DOES NOT, MEASURED.
That file's `TestTheCensusNamesItsEvidenceHome::test_the_census_cites_symbols_not_line_numbers`
asserts the ABSENCE of line numbers. It is registered on the `api` and `core` surfaces — and
a docs-only edit of the census document selects **no surface at all**:

    >>> ci_selection.select(['docs/architecture/BOUNDED-OPERATION-CONTRACT.md'],
    ...                      'pull_request', manifest)['surfaces']
    []

so the diff that re-introduces a coordinate is exactly the diff that does not run that pin.
This file is registered on `tier1`, which `select()` seeds every selection from and returns
wholesale for a docs-only diff — that is the arm that actually runs. It re-asserts the
absence (cheaply, and on the path where it matters) and adds the half nothing else asserts:
every `<module>::<name>` resolves to a definition in that module.

⚠️ This file does NOT replace main's pin and must not be read as a second opinion on it: the
absence rule is one rule, and the two files must agree on it. The resolution rule below is
new; the absence rule is repeated here only because a rule enforced on a diff that never
runs is not enforced.

Cost: text-only. One document read, seven `ast.parse` calls, no DB, no graph, no network, no
model, no fixture, no import of the package under test.
"""
from __future__ import annotations

import ast
import re
from functools import cache
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DOC = REPO / "docs" / "architecture" / "BOUNDED-OPERATION-CONTRACT.md"

#: A symbol citation: a module path and a name, joined by `::`. The name may carry dots
#: (`response_fields.tortoise_search.truncated`) because the manifest's own cells name keys
#: that way; a `.py` cite is resolved against the AST, so an unknown dotted tail fails there.
_SYMBOL_CITE = re.compile(r"([\w./\-]+\.(?:py|ya?ml))::([\w.]+)")

#: A line-number citation, in either of the two shapes the document has used: an explicit
#: `file.py:NNN` and the bare continuation `:NNN` (meaning "the file under discussion").
#: Both are retired — #10622 — so either reappearing is the defect.
_LINE_CITE = re.compile(
    r"\b[\w./\-]+\.(?:py|ya?ml):\d+(?:-\d+)?|`:\d+(?:-\d+)?`"
)

#: A floor, not a count: the census carries 40 symbol citations at this head. The floor is
#: deliberately far below that — it exists to fail a parser that matched nothing, not to be
#: updated every time a row is added (a count pinned here is the "a past state quoted as a
#: current one" defect this whole document is about).
_MIN_CITATIONS = 20


@cache
def _names(module: Path) -> frozenset[str]:
    """Every name `module` binds at any scope: defs, classes, assignments, imports.

    Nested scopes are included because a cell legitimately cites a name that is not
    module-level (a route function defined at module level is the common case; a helper is
    not). Imports are included because a cell may cite a name the module re-exports, which is
    a real binding in that module even though its `def` lives elsewhere.
    """
    tree = ast.parse(module.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            found.add(node.name)
        elif isinstance(node, ast.Assign):
            found.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            found.add(node.target.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                found.add(alias.asname or alias.name.split(".")[0])
    return frozenset(found)


def _unresolved(text: str) -> list[tuple[str, str]]:
    """(module, name) pairs the document cites that `module` does not bind.

    A missing module file is reported as `<module>` with an empty name rather than raising:
    a cite into a file that does not exist is the loudest form of this defect, and a
    traceback here would report it as a test error instead of as the content finding it is.
    """
    cited: dict[str, set[str]] = {}
    for module, name in _SYMBOL_CITE.findall(text):
        cited.setdefault(module, set()).add(name)

    missing: list[tuple[str, str]] = []
    for module, names in sorted(cited.items()):
        path = REPO / module
        if not path.exists():
            missing.append((module, ""))
            continue
        if path.suffix in {".yml", ".yaml"}:
            # A manifest cite names a key (`response_fields[...]`); the YAML is read as text
            # rather than parsed so a cite that names a whole dotted path still resolves on
            # its leaf, which is how the document writes them.
            body = path.read_text(encoding="utf-8")
            missing.extend((module, n) for n in sorted(names) if n.split(".")[0] not in body)
            continue
        known = _names(path)
        missing.extend((module, n) for n in sorted(names) if n not in known)
    return missing


def test_the_census_cites_no_line_numbers() -> None:
    """The retired coordinate system must not come back (#10622's rule, on the docs path).

    Pre-#10622 every evidence cell was a coordinate and the document went stale three times;
    the numbers were also the reason a re-derivation had to be a READ rather than arithmetic.
    `tests/test_8447_truncation_marker.py` asserts this too — in a file that a docs-only edit
    of this document does not select (see the module docstring), which is why the rule is
    repeated here rather than merely relied on.
    """
    text = DOC.read_text(encoding="utf-8")
    offenders = sorted(set(_LINE_CITE.findall(text)))
    assert not offenders, (
        "the census carries line-number citations, which #10622 retired because a number is"
        f" exact when written and wrong after the next merge: {offenders}. Cite the"
        " construction as `<module>::<name>` instead."
    )


def test_every_cited_symbol_exists() -> None:
    """A symbol citation is only durable if it names something: a rename must fail here.

    This is the half of the invariant nothing else asserts. The document's own mitigation
    for a drifted citation — "search the quoted fragment" — cannot substitute: a cell whose
    symbol was renamed still READS correct, so no reader is warned.
    """
    text = DOC.read_text(encoding="utf-8")
    citations = _SYMBOL_CITE.findall(text)
    assert len(citations) >= _MIN_CITATIONS, (
        f"only {len(citations)} `<module>::<name>` citations were found in {DOC.name}; this"
        " head carries 40. A parser that matches nothing would pass the resolution check"
        " below, so the floor fails it here instead."
    )

    missing = _unresolved(text)
    assert not missing, (
        "the census cites names its own modules do not bind: "
        + "; ".join(f"`{m}::{n}`" if n else f"`{m}` (no such file)" for m, n in missing)
        + ". A renamed symbol leaves the cell reading correct with no reader warned —"
        " re-point the cite at the name that exists, or drop the cell."
    )


# ---------------------------------------------------------------------------
# Non-vacuity: both guards, driven with the mutation each names.
#
# A guard whose expectations are read out of the document can be defanged by editing the
# document, so each check above is handed the mutation a reviewer used to disprove its first
# version. They run in-process against `_unresolved` / `_LINE_CITE` rather than a `/tmp` copy,
# so they cannot rot.
# ---------------------------------------------------------------------------


def test_a_renamed_symbol_is_reported() -> None:
    """`test_every_cited_symbol_exists`, on a mutated citation.

    The real document's symbol is read first, so this mutation follows a rename instead of
    pinning a name that may legitimately change: it plants a `get_cross_lens_candidates`
    cite with a suffix no module binds.
    """
    text = DOC.read_text(encoding="utf-8")
    assert "tortoise/sdk.py::get_cross_lens_candidates" in text, (
        "the census no longer cites `tortoise/sdk.py::get_cross_lens_candidates` — update"
        " this mutation to a symbol the document does cite"
    )
    mutated = text.replace(
        "tortoise/sdk.py::get_cross_lens_candidates",
        "tortoise/sdk.py::get_cross_lens_candidates_RENAMED",
        1,
    )
    assert ("tortoise/sdk.py", "get_cross_lens_candidates_RENAMED") in _unresolved(mutated), (
        "a symbol the module does not bind was not reported — the resolution check is"
        " vacuous, and a rename would pass silently"
    )
    # An import-bound name must NOT be reported: a module that re-exports a name binds it.
    assert ("tortoise/sdk.py", "get_cross_lens_candidates") not in _unresolved(text)


def test_a_reintroduced_coordinate_is_reported() -> None:
    """`test_the_census_cites_no_line_numbers`, on a planted coordinate.

    Both spellings are driven, because the document used each: the explicit
    `hosted_api.py:10914` and the bare continuation `:10973` that means "the file under
    discussion". A check written for one spelling fails OPEN on the other.
    """
    text = DOC.read_text(encoding="utf-8")
    assert not _LINE_CITE.findall(text), "the real document already carries a coordinate"

    for planted in ("`tortoise/hosted_api.py:10914`", "(`:10973`)"):
        assert _LINE_CITE.findall(planted), (
            f"the coordinate scanner did not see {planted!r} — a retired number would slip"
            " through the check above"
        )
