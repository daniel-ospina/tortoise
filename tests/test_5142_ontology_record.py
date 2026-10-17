"""#5142 — the ONTOLOGY record of the D10 code half, pinned against drift.

Three recording gaps were found by the code review of PR #5127 (the D10 code
half) and closed by #5142. Each one is the same *class* of defect: a **claim in
the canonical ontology that nothing re-checks**, so the next lane reads it as
fact and either re-flips it, re-implements the opposite, or trusts a REQUIRED
marker the writer does not honour.

  1. **The `format` markers moved ⚠️ → ✅ with no changelog entry.** PR #5127
     (`294d5847e`) flipped §4.6's field row and §4.7's matrix row because
     `format` moved into `_SOURCE_HANDLED`, and touched no changelog line — so
     the doc asserted a landed code state with nothing recording when or why.
  2. **The deferred node collapse was unrecorded, and #5088 asserted the
     opposite.** A document keeps TWO `:Source` nodes; the collapse is
     deliberately deferred because merging them turns the `references` edge
     into a dropped self-loop and leaves every doc unit permanently incomplete
     for the index-completeness gate.
  3. **The document `:Source` scoping was unrecorded.** §4.6 marked
     `sourceKind`/`contentHash`/`version` REQUIRED ✅, while the document write
     path supplied a different set — `contentHash`/`version` (PR #5592) and
     `sourceKind` (#5082, closed on the CREATE-only write).

This module is the **anti-drift falsifier** for all three. It asserts the doc
and the code together, so neither half can move alone:

  * the `format` markers are ✅ **and** `format` really is in
    `_SOURCE_HANDLED` (a marker that overstates the code is the class here);
  * the changelog **contains** the `_SOURCE_HANDLED` / `format` / PR #5127
    record (so a re-attribution has to carry a changelog line);
  * §4.4 carries the deferral statement **and** its `OVERRIDES:` line naming
    the departed default;
  * §4.6 carries the document-`:Source` scoping note recording `sourceKind`
    as written since #5082 (CREATE-only) and `contentHash`/`version` as
    written since #5422;
  * `_upsert_document` writes `contentHash` **and** `version` **and**
    `sourceKind='document'` (CREATE-only) — so the note stays true in both
    directions;
  * **every** writer that can create a document `:Source`
    (`_upsert_document`, `_event_plain_merge`'s `objectType='Document'` branch,
    and `hosted_api._execute_commit_writes`' `/v1/sessions` transcript MERGE)
    stamps the same CREATE-only `sourceKind='document'` — the note's claim is
    universal, so one writer dropping it must not pass silently (#8525).

Hermetic: a file read plus one import of the handler class. No DB, no network,
no browser.
"""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ONTOLOGY = REPO / "docs" / "ONTOLOGY.md"
ONTOLOGY_CHANGELOG = REPO / "docs" / "ONTOLOGY-CHANGELOG.md"

# The two marker rows PR #5127 flipped, and the PR/commit that flipped them.
FLIP_PR = "#5127"
FLIP_COMMIT = "294d5847e"
# The landing that supplies `contentHash` + `version` on the document path.
ANCHOR_PR = "#5592"
# The open gap the §4.6 note must name.
KIND_ISSUE = "#5082"


def _doc() -> str:
    return ONTOLOGY.read_text(encoding="utf-8")


def _changelog_doc() -> str:
    """The ONTOLOGY changelog file — the home of the per-field build record.

    #8745 moved the `Impl` column and the changelog prose out of `ONTOLOGY.md`
    into this sibling (owner approval tracked there; #8542 authorised only the
    two architecture documents), so the change record and the per-field markers
    are read from here rather than from the model.
    """
    return ONTOLOGY_CHANGELOG.read_text(encoding="utf-8")


def _section(doc: str, heading: str) -> str:
    """The text of one `### …` section, up to the next `###`/`##` heading."""
    start = doc.index(heading)
    ends = [doc.find(marker, start + len(heading)) for marker in ("\n### ", "\n## ")]
    ends = [e for e in ends if e != -1]
    return doc[start:min(ends)] if ends else doc[start:]


def _changelog() -> str:
    """The contiguous blockquote run that holds the changelog entries.

    Every changelog line is a blockquote line (blank separators are a bare
    ``>``), so the run ends at the first line that is not prefixed with ``>``.
    The entries live in `docs/ONTOLOGY-CHANGELOG.md` since #8745 moved them out
    of the model.
    """
    doc = _changelog_doc()
    start = doc.index("> **Changelog")
    out: list[str] = []
    for line in doc[start:].splitlines():
        if not line.startswith(">"):
            break
        out.append(line)
    return "\n".join(out)


# --------------------------------------------------------------------------
# 1. The `format` markers agree with the code, and the move has a changelog
#    entry naming it (the D10 model version is NOT the code version).
# --------------------------------------------------------------------------


def test_format_is_in_source_handled() -> None:
    """The ✅ marker's only justification: `format` is a fixed-clause key."""
    from tortoise.projection.entities import _EntityHandlers

    assert "format" in _EntityHandlers._SOURCE_HANDLED, (
        "§4.6/§4.7 mark `format` ✅ ('In `_SOURCE_HANDLED`'), so the code must "
        "carry it — PR #5127 moved it there (#5142)"
    )


def test_format_marker_rows_are_checked() -> None:
    """Both flipped rows read ✅; a re-flip to ⚠️/❌ must fail here.

    The §4.6 `format` **build marker** now lives in `ONTOLOGY-CHANGELOG.md`'s
    per-field implementation table — #8745 moved the `Impl` column out of the
    model — so it is read from its new home. §4.7's matrix row is a design
    statement ("`format` belongs here"), not a per-field `Impl` cell, and stays
    in `ONTOLOGY.md`.
    """
    source_row = [
        ln for ln in _section(_changelog_doc(), "### §4.6 Source").splitlines()
        if ln.startswith("| `format` ")
    ]
    assert len(source_row) == 1, (
        "expected exactly one `format` row in the §4.6 build record "
        "(`ONTOLOGY-CHANGELOG.md`)"
    )
    assert "✅" in source_row[0], (
        "the §4.6 `format` build record must stay ✅ (PR #5127 moved it into "
        "`_SOURCE_HANDLED`) — #5142"
    )

    matrix_row = [
        ln for ln in _section(_doc(), "### §4.7 Temporal Model").splitlines()
        if ln.startswith("| format ")
    ]
    assert len(matrix_row) == 1, "expected exactly one `format` row in §4.7"
    assert "✅ `format` belongs here" in matrix_row[0], (
        "§4.7's matrix `format` row must stay ✅ — #5142"
    )


def test_changelog_records_the_format_move() -> None:
    """The flip must be recorded, not appear from nowhere in the rows."""
    changelog = _changelog()
    assert "_SOURCE_HANDLED" in changelog and "format" in changelog, (
        "the `format` markers moved ⚠️ → ✅ with no changelog entry (#5142): the "
        "changelog must record the `_SOURCE_HANDLED |= format` fact"
    )
    assert FLIP_PR in changelog or FLIP_COMMIT in changelog, (
        f"the changelog entry must name the code half that flipped the markers "
        f"({FLIP_PR} / {FLIP_COMMIT}) — the rows' '(v3.15)' is the MODEL version, "
        f"not the code's (#5142)"
    )


# --------------------------------------------------------------------------
# 2. The deferred node collapse is recorded, with its OVERRIDES line.
# --------------------------------------------------------------------------


def test_section_4_4_records_the_deferred_collapse() -> None:
    section = _section(_doc(), "### §4.4 Document")
    assert "DEFERRED" in section and "self-loop" in section, (
        "§4.4 must state that the document/corpus-source node collapse is "
        "deliberately deferred and why (the `references` self-loop) — without "
        "it, 'a document is a `:Source`' reads as an invitation to merge the "
        "two nodes and break the index-completeness gate (#5142)"
    )
    assert "references" in section and "index-completeness" in section, (
        "§4.4's deferral statement must name the `references` edge and the "
        "index-completeness gate's `edge` clause — the concrete consequence "
        "(#5142)"
    )


def test_section_4_4_carries_the_overrides_line() -> None:
    section = _section(_doc(), "### §4.4 Document")
    assert "OVERRIDES:" in section, (
        "the departure from the 'one node per document/source' default is a "
        "deliberate ruling, so it must carry an `OVERRIDES:` line (relayed from "
        "#5013) at §4.4 — the canonical home for the entity decision (#5142)"
    )
    overrides_row = [
        ln for ln in section.splitlines()
        if ln.lstrip("> ").strip().startswith("**OVERRIDES:**")
    ]
    assert len(overrides_row) == 1, "expected exactly one `OVERRIDES:` line in §4.4"
    assert "one node per" in overrides_row[0], (
        "the `OVERRIDES:` line must name the DEFAULT departed from — 'one node "
        "per document/source' / 'one node per source' — not merely state the "
        "choice (#5142)"
    )


# --------------------------------------------------------------------------
# 3. The document `:Source` scoping is recorded at §4.6, and is true of the
#    writer in BOTH directions.
# --------------------------------------------------------------------------


def test_section_4_6_records_the_document_scoping() -> None:
    section = _section(_doc(), "### §4.6 Source")
    assert "Document `:Source` scoping" in section, (
        "§4.6 marks REQUIRED fields a document `:Source` supplies conditionally; "
        "the scoping note must state that at the row a reader trusts, instead of "
        "leaving it to the global 'code may lag' caveat (#5142)"
    )
    assert KIND_ISSUE in section, (
        f"the scoping note must name the issue that closed the missing "
        f"`sourceKind` write ({KIND_ISSUE}) — #5142/#5082"
    )
    assert "written since #5082" in section, (
        "the scoping note must now record that `sourceKind` IS written on the "
        "document path (#5082) — otherwise it overstates a closed gap the way "
        "the pre-#5142 rows understated a landed one"
    )
    assert "CREATE ONLY" in section or "create-only" in section.lower(), (
        "the note must record the `coalesce` create-only semantics — #398's "
        "never-overwrite contract is what makes the write safe on a node an "
        "authoritative writer already stamped (#5082)"
    )
    assert ANCHOR_PR in section or "#5422" in section, (
        f"the scoping note must record that `contentHash`/`version` ARE written "
        f"on the document path ({ANCHOR_PR} / #5422) — otherwise the note "
        f"overstates the gap the way the markers overstated the code (#5142)"
    )
    # #8525 review (P1/P2): state the downstream consequences of the write, not
    # just the field. A future lane that deletes either paragraph must fail here.
    assert "word-point un-freeze requires a" in section, (
        "the note must record the belief-path guard that keeps 'no credibility "
        "inheritance moves' TRUE: a document source resolves no tier, so the "
        "#398 word-point un-freeze requires a resolved evidence body — without "
        "it a word Point over a neutral document is re-labelled "
        "`set-by-author` -> `inherited-from-source` with nothing inherited "
        "(#8525 review P1)"
    )
    assert "cross-lens" in section, (
        "the note must name the intended D3 consequence of the write: a "
        "document `:Source` is a REGISTERED kind, so `_cross_lens_pool` now "
        "admits Points extracted from documents (the #8525 review's P2 — an "
        "otherwise unstated output change)"
    )


def test_upsert_document_writes_the_source_kind_and_the_anchor_pair() -> None:
    """Both halves of the §4.6 note, true in both directions.

    `sourceKind='document'` IS written on CREATE only (#5082, implementing §4.6's
    REQUIRED marking and #5013 Q2's *"a source is `sourceKind: document` and
    carries a genre"*), and so are `contentHash`/`version` (#5422). A change
    that removes either write must update the note and this test together — the
    note must never be silently right or silently wrong.
    """
    import inspect

    from tortoise.projection.entities import _EntityHandlers

    src = inspect.getsource(_EntityHandlers._upsert_document)
    assert "s.contentHash" in src, "the document path must write the version anchor (#5422)"
    assert "s.version" in src, "the anchor pair is never half-written (#5422)"
    assert "s.sourceKind" in src, (
        "`_upsert_document` must write `sourceKind='document'` — §4.6 marks it "
        "REQUIRED on every :Source and #5013 Q2 keeps the two axes (#5082)"
    )
    assert "coalesce(s.sourceKind, 'document')" in src, (
        "the `sourceKind` write must be CREATE-ONLY (`coalesce`) — #398's "
        "never-overwrite contract; a plain SET would clobber an authoritative "
        "kind on a node the document fold merely MERGEs onto (#5082)"
    )


def _string_expression(node, module_consts: dict[str, str]) -> str | None:
    """Best-effort static evaluation of a string expression.

    Recovers a plain string, an f-string's literal parts, a `+`-concatenation of
    strings (including a bare name bound to a module-level string constant), and
    a bare module-level constant name. Returns `None` when the node is not
    statically a string we can recover — a clause assembled at runtime is the
    documented coverage ceiling.
    """
    import ast

    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(
            part.value if isinstance(part, ast.Constant) and isinstance(part.value, str) else " "
            for part in node.values
        )
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _string_expression(node.left, module_consts)
        right = _string_expression(node.right, module_consts)
        if left is not None or right is not None:
            return (left or "") + (right or "")
        return None
    if isinstance(node, ast.Name):
        return module_consts.get(node.id)
    return None


def _module_string_consts(tree) -> dict[str, str]:
    """Every module-level `NAME = <string expression>` value, resolvable."""
    import ast

    consts: dict[str, str] = {}
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign):
            value = _string_expression(stmt.value, consts)
            if value is not None:
                for target in stmt.targets:
                    if isinstance(target, ast.Name):
                        consts[target.id] = value
        elif (
            isinstance(stmt, ast.AnnAssign)
            and isinstance(stmt.target, ast.Name)
            and stmt.value is not None
        ):
            value = _string_expression(stmt.value, consts)
            if value is not None:
                consts[stmt.target.id] = value
    return consts


def _document_writers_in_source(text: str, filename: str) -> dict[str, str]:
    """The document-`:Source` writers in one module's source.

    A writer is a function whose recovered Cypher mentions both a
    `MERGE (s:Source` and the `documentKind` key. Recovery deliberately spans
    more than the function's own string literals: bare references to
    module-level string constants are followed too, so a writer that composes
    its MERGE clause from a shared constant (`"MERGE (s:Source ..." + DOC_SET`)
    is still found — the #8525 review's P2 showed a function-locals-only scan
    missed exactly that shape.
    """
    import ast

    try:
        tree = ast.parse(text)
    except SyntaxError:  # pragma: no cover — a syntax error fails elsewhere
        return {}
    module_consts = _module_string_consts(tree)
    writers: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        parts: list[str] = [
            child.value
            for child in ast.walk(node)
            if isinstance(child, ast.Constant) and isinstance(child.value, str)
        ]
        parts += [
            module_consts[child.id]
            for child in ast.walk(node)
            if isinstance(child, ast.Name)
            and isinstance(child.ctx, ast.Load)
            and child.id in module_consts
        ]
        src = " ".join(parts)
        if "MERGE (s:Source" in src and "documentKind" in src:
            writers[f"{filename}::{node.name}"] = src
    return writers


def _document_source_writers() -> dict[str, str]:
    """Every function in `tortoise/` whose recovered strings build a `:Source`
    MERGE that sets `documentKind` — i.e. every writer that can create a
    document `:Source`. Derived MECHANICALLY: a hand-picked list is exactly how
    the two writers #8525's review found (and the doc named none of them) went
    unnamed.
    """
    writers: dict[str, str] = {}
    for path in sorted((REPO / "tortoise").rglob("*.py")):
        writers.update(
            _document_writers_in_source(path.read_text(encoding="utf-8"), path.name)
        )
    return writers


#: The writers `_document_source_writers()` must find — #4.6's claim is
#: UNIVERSAL, so a NEW writer (or a removed one) is a doc/claim change, not a
#: silent addition.
PINNED_DOCUMENT_SOURCE_WRITERS = frozenset({
    "entities.py::_upsert_document",
    "entities.py::_event_plain_merge",
    "hosted_api.py::_execute_commit_writes",
})


def test_every_document_source_writer_stamps_source_kind() -> None:
    """The §4.6 note and the four `_required_sweep` harnesses now assert
    `sourceKind` on **every** `:Source` — a universal. Every path that can create
    a document `:Source` must stamp it; the PR #8525 review found two of them
    still writing `sourceKind = NULL` while the doc and the sweeps claimed
    otherwise.

    The writer set is DERIVED, not hardcoded: a new document writer changes the
    derived set and fails the pin, so it cannot leave the doc's "every writer"
    claim false and the four sweeps a false green the way #8525's two unnamed
    writers did.
    """
    writers = _document_source_writers()
    assert set(writers) == set(PINNED_DOCUMENT_SOURCE_WRITERS), (
        "the set of document-`:Source` writers changed — stamp "
        "`coalesce(s.sourceKind, 'document')` on any new writer, update the "
        "§4.6 note, and re-pin here in the same change (#5082/#8525). "
        f"derived={sorted(writers)}"
    )
    for name, src in writers.items():
        assert "coalesce(s.sourceKind, 'document')" in src, (
            f"{name} must stamp `sourceKind='document'` on the document :Source "
            f"— §4.6 marks it REQUIRED on every :Source, the four "
            f"_required_sweep harnesses assert it on every :Source, and #5082 "
            f"is only closed if every writer agrees (#8525 review)"
        )


def test_writer_scanner_follows_module_level_constants() -> None:
    """The derivation must not miss a writer that composes its MERGE clause from
    a module-level constant (the #8525 review's P2).

    The pre-review scanner only gathered string literals *inside* the function,
    so `"MERGE (s:Source ..." + DOC_SET` was invisible — a future writer could
    leave the doc's universal claim false and still pass. The scanner now
    follows bare module-level string constants; this synthetic writer is
    detected, and the assertion fails if that resolution is removed.
    """
    text = '''\
DOC_SET = (
    "s.documentKind='note', "
    "s.sourceKind=coalesce(s.sourceKind, 'document')"
)


def _synthetic_writer():
    return "MERGE (s:Source {url:$id}) SET " + DOC_SET
'''
    writers = _document_writers_in_source(text, "synthetic.py")
    assert "synthetic.py::_synthetic_writer" in writers, (
        "a writer composing its MERGE clause from a module-level constant must "
        "still be derived — the pre-#8525-review function-locals-only scan "
        "missed exactly this shape"
    )
    assert "coalesce(s.sourceKind, 'document')" in writers["synthetic.py::_synthetic_writer"], (
        "the resolved module-level clause must be visible to the stamp check"
    )
