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
_SDK = "tortoise/sdk.py"
_CACHE = "tortoise/cache_projection.py"
_ENT = "tortoise/projection/entities.py"
_HOSTED = "tortoise/hosted_api.py"
_INIT = "tortoise/projection/__init__.py"

#: (document, cite token the document must still carry, target file, 1-based line, fragment at that line)
_PINS: tuple[tuple[str, str, str, int, str], ...] = (
    # --- docs/architecture/SUBSTRATE-REBUILD-MAP.md §5's move list (the #11682 corpus-join block)
    (_SUBSTRATE, "tortoise/search_engine.py:3429", _SE, 3429, "``:Source``/``:Document`` row must not contribute"),
    (_SUBSTRATE, "`:3427`", _SE, 3427, "# #11682: the corpus join is PER FAMILY"),
    # --- docs/research/2026-11-13-12298-m3-fts-leg/research-brief.md
    (_M3_BRIEF, "tortoise/search_engine.py:2866", _SE, 2866, "def run_fts_query"),
    (_M3_BRIEF, "search_engine.py:2708", _SE, 2708, "def recency_tie_clause"),
    (_M3_BRIEF, "tortoise/search_engine.py:4707", _SE, 4707, "for rank, (pid, _score) in enumerate(ranked)"),
    (_M3_BRIEF, "`search_engine.py:4707`", _SE, 4707, "for rank, (pid, _score) in enumerate(ranked)"),
    # --- docs/architecture/ENTITY-VERDICT-BANDS.md — the D-1 row "the retrieval filter is status-only"
    # ⚠️ This row is a SECOND false correction, of the same class this file exists for. The document
    # cited `search_engine.py:2973-2997`; other PRs' inserts had already moved the status filter to
    # `:3020` on `origin/main`, so the cite was ~44 lines stale BEFORE this branch's `+3`. Adding the
    # `+3` (`2973-2997` -> `2976-3000`) therefore preserved the staleness while reading as a correction,
    # and `2976-3000` is the `build_or_query` byte-identity comment block, not the filter. The pin below
    # is anchored on the line that IS the filter, so the next offset-only edit reds instead of shipping.
    (_EVIDENCE, "search_engine.py:3059-3074", _SE, 3059, "if label in _STATUS_FILTERED_LABELS"),
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


# ⛔ ONE DOCUMENT IN THIS SET IS **PIN-BASED BY ITS OWN DECLARATION**, and pinning it to the live tree
# is a FALSE CORRECTION — not a completeness win. `CAPABILITY-PLACEMENT-AUDIT.md` states in its own
# header that every cite in it is opened at `ccb49741b2ec` (#13095's squash commit) and that on any
# later head those cites drift "**by construction**: a `main` integration that inserts a line above a
# cited site moves every cite below it, with no symbol having changed" — and it forbids the obvious
# wrong repair in as many words: "that is a fact about the drift, not a licence to re-derive from a
# symbol". Measured cost of getting this backwards (this branch, twice): the audit's §2.5 rows were
# re-pointed `4426 → 4587`, `3039 → 3078`, `6656 → 6829` and the row-13 cluster `19679 → 21620` to
# agree with THIS tree — each edit was `grep`-verified, each one broke the document, and the first one
# was then applied a second time. The numbers below open on the pin (`ccb49741b2ec`) and are the ones
# `#13274` re-derived there; a live-tree arm on this document can only re-create that
# error, because the document's cites are not claims about the tree this test runs in.
_PIN_HEAD = "ccb49741b2ec"

#: (document, cite token the document still carries, target file, line at `_PIN_HEAD`, fragment at that line)
_AUDIT_PINS: tuple[tuple[str, str, str, int, str], ...] = (
    # §2.5's six re-derived cites — the rows the doc says were re-pointed sixteen times.
    (_AUDIT, "tortoise/search_engine.py:4426", _SE, 4426, "MATCH path = (seed)-[:IMPL|NAND*1..{max_hops}]-(n:Point)"),
    (_AUDIT, "tortoise/search_engine.py:6297", _SE, 6297, "(op)-[r2:IMPL|hasPart]-(other:Point)"),
    (_AUDIT, "tortoise/search_engine.py:6351", _SE, 6351, "(op)-[r2:IMPL|NAND|hasPart]-(other:Point)"),
    (_AUDIT, "tortoise/search_engine.py:6656", _SE, 6656, "(n:Point)-[r:aboutSubject]->(:Subject)"),
    (_AUDIT, "tortoise/search_engine.py:3039", _SE, 3039, "CALL db.idx.fulltext.queryNodes"),
    (_AUDIT, "tortoise/search_engine.py:4350-4351", _SE, 4350, "MATCH (n:{label_str})"),
    (_AUDIT, "tortoise/search_engine.py:3938", _SE, 3938, "CALL db.idx.vector.queryNodes('{label}', 'embedding', $limit"),
    # Row 13's cluster — the FOUR writers (#13268/#13312's "four WRITERS" row) and the WITNESS read.
    (_AUDIT, "sdk.py:19679-19680", _SDK, 19679, "s.summary=CASE WHEN $sum <> '' THEN $sum "),
    (_AUDIT, "`:19802`", _SDK, 19802, "s.summary, s.topics"),
    (_AUDIT, "sdk.py:19637", _SDK, 19637, "_source_search_text(topics=topics)"),
    (_AUDIT, "projection/entities.py:5598", _ENT, 5598, "s.summary=coalesce($summary, s.summary, '')"),
    (_AUDIT, "`:5282`", _ENT, 5282, "def _upsert_document"),
    (_AUDIT, "`:6850`", _ENT, 6850, "def _upsert_source"),
    (_AUDIT, "hosted_api.py:22309", _HOSTED, 22309, "s.title=$title, s.summary=$summary"),
    (_AUDIT, "`:22113`", _HOSTED, 22113, "def _execute_commit_writes"),
    (_AUDIT, "cache_projection.py:2479-2485", _CP, 2485, 'return summary == f"{SESSION_SOURCE_SUMMARY_PREFIX}{joined}"'),
    (_AUDIT, "`:2659`", _CP, 2659, "_episodic_source_search_text_is_the_lexicon(row)"),
    # Row 13b's index declaration.
    (_AUDIT, "projection/__init__.py:6831", _INIT, 6831, '("Source", ["_searchText"])'),
)


def _resolve_at_head(pins, head: str = _PIN_HEAD, repo: Path = REPO) -> list[str]:
    """Every reason the *pins* do not hold at *head* — the PIN regime, `git show` not the worktree.

    Same two arms as `_resolve` (token still carried, content still on the named line) so a broken
    document reds identically; the file content simply comes from the pinned commit, which is the head
    the document itself declares. A missing commit is reported, never skipped: an unreadable pin is a
    citation nobody can open.
    """
    import subprocess

    errors: list[str] = []
    for doc_rel, token, target_rel, line, fragment in pins:
        doc_text = (repo / doc_rel).read_text(encoding="utf-8")
        if token not in doc_text:
            errors.append(
                f"{doc_rel}: the citation `{token}` is gone — the row was re-pointed or dropped. "
                f"This document is pin-based: re-derive it at `{head}`, never against the live tree."
            )
            continue
        out = subprocess.run(
            ["git", "show", f"{head}:{target_rel}"], cwd=repo, capture_output=True, text=True,
        )
        if out.returncode != 0:
            errors.append(f"{doc_rel}: cannot read `{target_rel}` at `{head}` — {out.stderr.strip()[:120]}")
            continue
        target_lines = out.stdout.splitlines()
        if not 0 < line <= len(target_lines):
            errors.append(f"{doc_rel}: `{target_rel}:{line}` is past EOF at `{head}` ({len(target_lines)} lines)")
            continue
        actual = target_lines[line - 1]
        if fragment not in actual:
            errors.append(
                f"{doc_rel} cites `{target_rel}:{line}` for {fragment!r}, but at `{head}` that line reads "
                f"{actual.strip()[:100]!r} — the cite is wrong AT THE PIN it declares."
            )
    return errors


def test_the_audit_cites_land_at_the_pin_the_document_declares() -> None:
    """The pin-based document is verified where IT says its cites are opened — `_PIN_HEAD`."""
    errors = _resolve_at_head(_AUDIT_PINS)
    assert not errors, "\n".join(errors)


def test_the_pin_arm_is_not_vacuous() -> None:
    """Negative control for the pin arm: the SAME row, the pin's line +1, must red with the pin named.

    Reachable by construction (no offset is ever applied to a pin row), and it is the arm that would
    have caught the live-tree rewrite: `sdk.py:19679` is the CASE keep-arm at `ccb49741b2ec` and is NOT
    the keep-arm 1,940 lines later, which is where the re-pointed audit put it.
    """
    row = (_AUDIT, "sdk.py:19679-19680", _SDK, 19680, "s.summary=CASE WHEN $sum <> '' THEN $sum ")
    errors = _resolve_at_head((row,))
    assert errors, "a pin row off by one line must red the pin arm"
    assert _PIN_HEAD in errors[0], errors[0]
    assert "AT THE PIN" in errors[0], "the content arm must be the one that fires"


def test_the_resolver_reds_on_a_double_applied_offset() -> None:
    """Negative control — the exact defect: 4549 + 3 = 4552 is the `#7704` note, not the loop.

    Reachable: it is the shipped pre-fix state of the m3 brief, reintroduced here by mutating a COPY of
    the shipped row with the cite token LEFT INTACT, so the failure is reported by the CONTENT arm and
    cannot be satisfied by the token arm (which is what a re-pointed token would trip first).
    """
    row = (_M3_BRIEF, "tortoise/search_engine.py:4707", _SE, 4707, "for rank, (pid, _score) in enumerate(ranked)")
    doubled = (row[0], row[1], row[2], 4710, row[4])
    errors = _resolve((doubled,))
    assert errors, "a spurious +3 must red the resolver"
    # ⛔ THE CONTROL MAY NOT PIN THE CONTENT OF LINE 4710. It used to assert
    # `"#7704" in errors[0]` — the note that happened to sit three lines below the
    # loop at the head this was written on. Any edit above it moves that line, so
    # the control reds for a reason that has nothing to do with the resolver and
    # can never pass again (#13248 round 5 measured it permanently red). What the
    # control must prove is the ARM that fires and the offsets it names.
    assert ":4710" in errors[0], errors[0]
    assert "the cite rotted" in errors[0], "the content arm must be the one that fires"
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
    assert docs == {_SUBSTRATE, _M3_BRIEF, _CENSUS, _EVIDENCE}
    assert len(_PINS) == 8   # substrate x2, m3 brief x4, census x1, verdict-bands x1
    # ⛔ The audit is NOT in this set on purpose — it is pin-based and has its own arm. A row deleted
    # to make the suite pass is the failure mode, so `_AUDIT_PINS` is counted too.
    assert {row[0] for row in _AUDIT_PINS} == {_AUDIT}
    assert len(_AUDIT_PINS) == 18   # §2.5 x7, row 13's four writers + WITNESS x10, row 13b x1
