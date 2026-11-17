"""#12184 — every evidence cite this branch's own inserts moved must land on the line it names.

THE DEFECT THIS PINS
--------------------
Two inserts on this branch shift every `file.py:NNNN` citation BELOW them:

* `tortoise/search_engine.py` — `run_fts_query`'s `_searchText`-per-door docstring, **+3** at `:2842`
  (`git diff origin/main...HEAD --unified=0` -> `@@ -2842 +2842,4 @@`);
* `tortoise/cache_projection.py` — the `_searchText` comment rewrite, **+2** at `:2128` (`@@ -2128,3
  +2128,5 @@`).

The documents below are EVIDENCE: a reader re-verifies a passage by opening the line it cites, so a
cite that is one line off reads exactly like a correct one and silently discounts the passage.

⚠️ The failure this file was written for was not a stale number, it was a **FALSE CORRECTION**: the
re-derivation pass at this branch's tip re-pointed
`docs/research/2026-11-13-12298-m3-fts-leg/research-brief.md`'s `for rank, (pid, _score) in
enumerate(ranked)` cite from `:4549` to `:4552` — applying the `+3` a SECOND time to a number that
already carried it. The loop read `:4546` on `origin/main` (where the doc's cite was right), moved to
`:4549` with the `+3` above, was corrected `:4546` -> `:4549` at `18d18b3565` — and `:4552` is the
`#7704` note UNDER that loop. A `⚠️ CORRECTED` marker records that the text changed, never that the
change was right, so each cite is pinned to its CONTENT here, never to a delta someone added.

WHAT MAKES EACH ROW FAIL, AND WHY THAT IS REACHABLE
---------------------------------------------------
A row is `(document, cite token, target file, 1-based line the document names, verbatim fragment)`.
It reds in two reachable states: the document no longer carries the cite token (the row was re-pointed
or dropped), or the named line does not contain the fragment the document quotes for it — which is the
shipped pre-fix state of FOUR rows:

* `research-brief.md` `:4552` (x2 rows — the table row and the prose cite) — the fix is `:4549`;
* `SUBSTRATE-REBUILD-MAP.md` `:3392` and its block-open `:3388` — the fix is `:3393` / `:3391`.

⚠️ Two conventions keep a row honest, so neither is a copy of the document's rendering:

* a RANGE cite (`4425-4429`, `4353-4355`, `2650-2657`, …) is anchored on the line inside the range
  that carries the fragment the document actually quotes for it, not blindly on the range's first line;
* where the document renders a template (`MATCH (n:{label})`) the fragment is the LITERAL on the line
  (`MATCH (n:{label_str})`), because the rendered form appears in no file.

The negative-control tests below are the other direction: they mutate a COPY of a shipped row (a
spurious `+3` with the cite token left intact, and a dropped cite) and assert the resolver reports it. A
guard whose negative case cannot be reached proves the code ran, not that the tree agrees with the
document.
"""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

_AUDIT = "docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md"
_SUBSTRATE = "docs/architecture/SUBSTRATE-REBUILD-MAP.md"
_M3_BRIEF = "docs/research/2026-11-13-12298-m3-fts-leg/research-brief.md"
_CENSUS = "docs/evidence/13083-capability-census/README.md"
_EVIDENCE = "docs/architecture/ENTITY-VERDICT-BANDS.md"
_SE = "tortoise/search_engine.py"
_CP = "tortoise/cache_projection.py"

#: (document, cite token the document must still carry, target file, 1-based line, fragment at that line)
_PINS: tuple[tuple[str, str, str, int, str], ...] = (
    # --- docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md (search_engine.py +3, cache_projection.py +2)
    (_AUDIT, "cache_projection.py:3635", _CP, 3635, 'if table == "point"'),
    (_AUDIT, "search_engine.py:4528-4532", _SE, 4532, "MATCH path = (seed)-[:IMPL|NAND*1..{max_hops}]-(n:Point)"),
    (_AUDIT, "search_engine.py:6415", _SE, 6415, "(op)-[r2:IMPL|hasPart]-(other:Point)"),
    (_AUDIT, "search_engine.py:6774", _SE, 6774, "(n:Point)-[r:aboutSubject]->(:Subject)"),
    (_AUDIT, "search_engine.py:6776", _SE, 6776, "(m:Event)-[r2:aboutSubject]->(:Subject)"),
    # Row 149's TWO BARE range cites carry no `search_engine.py` prefix, so the token-level pins for
    # `6659`/`6661` above do not reach them — they are the only shifted mentions in this document the
    # first pin set missed. Both were already correct at the pre-fix head, so these two are
    # COMPLETENESS pins: they catch the re-point/double-shift that DID happen in this class (`:4549`
    # re-pointed to `:4552`), which is the failure the whole file exists for.
    (_AUDIT, "`:6773-6776`", _SE, 6773, "_SUBJECT_SOURCE_SCOPED_PROBE = ("),
    (_AUDIT, "`:6779-6792`", _SE, 6779, "def subject_binding_available"),
    (_AUDIT, "search_engine.py:3053", _SE, 3053, "CALL db.idx.fulltext.queryNodes"),
    (_AUDIT, "search_engine.py:4456-4458", _SE, 4456, "MATCH (n:{label_str})"),
    (_AUDIT, "search_engine.py:4044", _SE, 4044, "CALL db.idx.vector.queryNodes('{label}', 'embedding', $limit"),
    (_AUDIT, "search_engine.py:2958", _SE, 2958, 'label = "Source" if entity_type == "document"'),
    (_AUDIT, "`:3025`", _SE, 3025, "the fulltext index is on Source._searchText"),
    (_AUDIT, "cache_projection.py:2650-2657", _CP, 2650, "it is the ONLY field the Source FTS index"),
    (_AUDIT, "cache_projection.py:2660-2662", _CP, 2660, "prose_columns = _EPISODIC_SOURCE_PROSE_COLUMNS"),
    # Row 387's bare range, quoting "un-indexes a restored session". Its sibling pins (`2660-2662`,
    # `2650-2657`) are different ranges and do not reach this one. NON-VACUOUS: the pre-fix document
    # carried `:2648-2649` here, so the cite-token arm fires on that tree (measured).
    (_AUDIT, "`:2650-2651`", _CP, 2651, "un-indexes a restored session"),
    (_AUDIT, "cache_projection.py:2712", _CP, 2712, "tail_denied = forbidden | {"),
    # --- docs/architecture/SUBSTRATE-REBUILD-MAP.md §5's move list (the #11682 corpus-join block)
    (_SUBSTRATE, "tortoise/search_engine.py:3404", _SE, 3404, "``:Source``/``:Document`` row must not contribute"),
    (_SUBSTRATE, "`:3402`", _SE, 3402, "# #11682: the corpus join is PER FAMILY"),
    # --- docs/research/2026-11-13-12298-m3-fts-leg/research-brief.md
    (_M3_BRIEF, "tortoise/search_engine.py:2841", _SE, 2841, "def run_fts_query"),
    (_M3_BRIEF, "search_engine.py:2683", _SE, 2683, "def recency_tie_clause"),
    (_M3_BRIEF, "tortoise/search_engine.py:4652", _SE, 4652, "for rank, (pid, _score) in enumerate(ranked)"),
    (_M3_BRIEF, "`search_engine.py:4652`", _SE, 4652, "for rank, (pid, _score) in enumerate(ranked)"),
    # --- docs/architecture/ENTITY-VERDICT-BANDS.md — the D-1 row "the retrieval filter is status-only"
    # ⚠️ This row is a SECOND false correction, of the same class this file exists for. The document
    # cited `search_engine.py:2973-2997`; other PRs' inserts had already moved the status filter to
    # `:3020` on `origin/main`, so the cite was ~44 lines stale BEFORE this branch's `+3`. Adding the
    # `+3` (`2973-2997` -> `2976-3000`) therefore preserved the staleness while reading as a correction,
    # and `2976-3000` is the `build_or_query` byte-identity comment block, not the filter. The pin below
    # is anchored on the line that IS the filter, so the next offset-only edit reds instead of shipping.
    (_EVIDENCE, "search_engine.py:3034-3049", _SE, 3034, "if label in _STATUS_FILTERED_LABELS"),
    # --- docs/evidence/13083-capability-census/README.md
    (_CENSUS, "cache_projection.py:2712", _CP, 2712, "tail_denied = forbidden | {"),
)


def _resolve(pins, repo: Path = REPO) -> list[str]:
    """Every reason the *pins* do not hold at *repo*'s tree (empty list = they all hold)."""
    errors: list[str] = []
    for doc_rel, token, target_rel, line, fragment in pins:
        doc_text = (repo / doc_rel).read_text(encoding="utf-8")
        if token not in doc_text:
            errors.append(
                f"{doc_rel}: the citation `{token}` is gone — the row was re-pointed or dropped. "
                "Re-derive it against the head file rather than deleting the pin."
            )
            continue
        target_lines = (repo / target_rel).read_text(encoding="utf-8").splitlines()
        if not 0 < line <= len(target_lines):
            errors.append(
                f"{doc_rel}: `{target_rel}:{line}` is out of range "
                f"({len(target_lines)} lines) — the file shrank under the cite."
            )
            continue
        actual = target_lines[line - 1]
        if fragment not in actual:
            errors.append(
                f"{doc_rel} cites `{target_rel}:{line}` for {fragment!r}, but that line reads "
                f"{actual.strip()[:100]!r} — the cite rotted, or an offset was applied twice. "
                f"RE-DERIVE BY CONTENT: grep -n {fragment!r} {target_rel}"
            )
    return errors


def test_every_pinned_cite_lands_on_the_content_the_document_quotes() -> None:
    """Fails on the pre-fix tree: the m3 brief names `:4552` and §5 names `:3392`/`:3388`."""
    errors = _resolve(_PINS)
    assert not errors, "\n".join(errors)


def test_the_resolver_reds_on_a_double_applied_offset() -> None:
    """Negative control — the exact defect: 4549 + 3 = 4552 is the `#7704` note, not the loop.

    Reachable: it is the shipped pre-fix state of the m3 brief, reintroduced here by mutating a COPY of
    the shipped row with the cite token LEFT INTACT, so the failure is reported by the CONTENT arm and
    cannot be satisfied by the token arm (which is what a re-pointed token would trip first).
    """
    row = (_M3_BRIEF, "tortoise/search_engine.py:4652", _SE, 4652, "for rank, (pid, _score) in enumerate(ranked)")
    doubled = (row[0], row[1], row[2], 4655, row[4])
    errors = _resolve((doubled,))
    assert errors, "a spurious +3 must red the resolver"
    assert ":4655" in errors[0] and "#7704" in errors[0]
    assert "is gone" not in errors[0], "the content arm must be the one that fires"


def test_the_resolver_reds_when_the_document_no_longer_carries_the_cite() -> None:
    """Negative control for the other arm: a dropped/re-pointed cite cannot pass by being absent."""
    row = (_M3_BRIEF, "search_engine.py:99999", _SE, 4549, "for rank, (pid, _score) in enumerate(ranked)")
    errors = _resolve((row,))
    assert errors, "a cite the document does not carry must red the resolver"
    assert "is gone" in errors[0]


def test_the_pinned_documents_are_the_ones_this_diff_shifted() -> None:
    """The pin set is bounded to the documents the review named — it must not silently shrink.

    Reachable: a row deleted to make the suite pass is the failure mode; `grep -c '^def test_'` and this
    count both move together only when a row is added on purpose.
    """
    docs = {row[0] for row in _PINS}
    assert docs == {_AUDIT, _SUBSTRATE, _M3_BRIEF, _CENSUS, _EVIDENCE}
    assert len(_PINS) == 24
