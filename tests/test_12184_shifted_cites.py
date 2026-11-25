"""Content pins for the `file:line` cites the living architecture/research docs carry.

THE DEFECT THIS PINS
--------------------
A document tells its reader to open a line, and the line it names is not the line it
quotes. A stale number reads exactly like a correct one, so the passage is silently
discounted — and a `⚠️ CORRECTED` marker records that the text CHANGED, never that the
change was RIGHT. Each row here is therefore
`(document, cite token, target file, 1-based line, verbatim fragment)` and grades the
TARGET line's CONTENT, not a delta someone applied to a number:

* the document no longer carries the cite token (the row was re-pointed or dropped), or
* the named line does not contain the fragment the document quotes for it.

Both are reachable states, and both are why this file grades content: the shipped defect
the instrument was written for was a FALSE CORRECTION — a re-derivation pass that applied
its own `+3` twice to a cite that already carried it, and re-pointed a correct address
onto the unrelated note below it.

WHAT THIS FILE NO LONGER CARRIES (2026-11-23, the test-bureaucracy cut)
----------------------------------------------------------------------
The retired centre of this file was `docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md`:
101 of the 119 rows it held at the rebased base (`origin/main` `0fbfbb5b210c`) pinned
that one document — 88 naming the `_AUDIT` constant, thirteen spelling the path
literally — and ~3,100 lines of apparatus around them graded its prose (its header's own
enumeration, its re-measured figures, its section-by-section cite families, its
`main`-set census). That document is a CENSUS — the audit's own verdict
(`#11081`, comment 7107649723, row B2) is *"none — it is a census of where capabilities
sit"*, i.e. a documentation defect and never a product break. Its cost is the pin loop itself:
B2's own count is **208** line-pinned cites, so every lane that inserted above one had to
re-derive the table
(the audit's own figure for that loop is ~45 runner-hours/day), and at this head two OPEN
PRs still carry hunks into this document and this file (`#14296`, `#14140`, read with
`gh pr list --json files`). `#14038` is the third, and it is MERGED — its merge
resolution is what dropped the `sdk.py:22127` row this file now restores, so it is cited
here as history rather than as a live conflict. It is deleted rather than re-cut, and the
arms that existed only to grade it went with it.

⛔ Row B2 also names the arms grading SENTENCES in
`docs/epics/2026-11-04-7949-extractor-v4-capstone/05-verify.md`, so exactly one arm over a
surviving document is retired here with them:
`test_the_13102_addendum_states_the_consume_pair_at_this_head`. That document is the `#7949`
bundle's, and its sentence-pins are B4's work, not this file's — say it plainly rather than
pretending every retired arm graded a document that no longer exists.

⚠️ RETIRED ARMS THAT DO **NOT** GRADE THE DELETED DOCUMENT ARE NOT RETIRED BY B2, and this
file had to be told that by a fresh reviewer (round 4). Two families were restored after
being dropped with the apparatus, because their subjects survive:

- **The token-span arm and its four controls.** They grade `_PINS` — the table this cut keeps
  — not the deleted document's prose. `_rows_outside_their_token` refuses a row that pins one
  address while grading another, which is the shape that shipped green once
  (`":37534`"` grading 37547) and which the content arm alone cannot see.
- **The `#7949` bundle cross-file arm** (`test_the_epic_bundles_i7_prose_cites_the_range_...`).
  Its subjects are `04-plan.md` and `tests/test_7949_capstone_measure.py`, both surviving; it
  needs no `_AUDIT` row and no pin, and it was green at this head.

A cut is scoped by what it deletes, not by where the code happened to sit.

⛔ AND TWO `_PINS`-GRADING ARMS WERE DROPPED WITHOUT REPLACEMENT, which round 4 did not say
and round 6 caught. `test_a_def_range_pin_ends_on_the_functions_own_last_line` and
`test_a_row_that_claims_the_COUNT_leg_pins_a_line_that_aggregates` both scan `_PINS`, so their
SUBJECT survives — but their population does not: measured against this head's 21 rows, the
`def`-anchored range pins are **0** and the `COUNT`-claiming rows are **0** (every such row lived
in the deleted document), and both arms carry a non-vacuity floor (`assert checked`) that would
red on an empty population. So they could not survive verbatim, and re-pointing them at a
population that does not exist would be a gate vouching for a claim it never read. Recorded here
rather than dropped silently, because "an arm vanished and nobody said why" is the next
reviewer's whole problem.

⚠️ ONE CROSS-DOCUMENT ARM IS **NOT** RETIRED: it grades two SURVIVING documents
(`docs/00_index.md` and `docs/architecture/ENTITY-VERDICT-BANDS.md`), it is named by neither
B2 nor B4, and no `_PINS` row can replace it (see its docstring below).

What is left is the generic instrument plus the rows for the documents that remain.

A row is added ONLY for a site a real correction touched. An anti-shrink arm below keeps
the document set and the row count honest, so a row cannot be dropped to make a suite pass.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

_SUBSTRATE = "docs/architecture/SUBSTRATE-REBUILD-MAP.md"
_M3_BRIEF = "docs/research/2026-11-13-12298-m3-fts-leg/research-brief.md"
_CENSUS = "docs/evidence/13083-capability-census/README.md"
_EVIDENCE = "docs/architecture/ENTITY-VERDICT-BANDS.md"
_INDEX = "docs/00_index.md"
_SE = "tortoise/search_engine.py"
_CP = "tortoise/cache_projection.py"
_SDK = "tortoise/sdk.py"
#: `#14087` review round 7: `00_index.md`'s entity-verdict row points at this file's `:43` in the
#: PRESENT tense ("the bullet is `entity_verdict.py:43`"), so the address is head-relative.
_EV = "tortoise/entity_verdict.py"
_HB = "tortoise/hosted_backup.py"
#: `#13854`'s vector-index-parity plan. Its reader table cites `hosted_backup.py:2732` for
#: `_index_has_is_operator()`.
_PLAN_4997 = "docs/plans/2026-09-25-4997-vector-index-parity.md"

#: (document, cite token the document must still carry, target file, 1-based line, fragment at that line)
_PINS: tuple[tuple[str, str, str, int, str], ...] = (
    # --- docs/architecture/SUBSTRATE-REBUILD-MAP.md §5's move list (the #11682 corpus-join block)
    # ⛔ EVERY `sdk.py` ROW BELOW MOVED +1 AT #14592, together and for ONE cause: that
    # branch's `_get_entity` label-set hunk adds one line above these addresses, so the
    # whole rotation is +1 and a per-row re-point would be nine chances to leave one
    # behind. Re-derived by CONTENT at the #14592 head (not by adding 1 to the old
    # number): each pinned fragment was grepped in `tortoise/sdk.py` and the pin was
    # re-anchored on the line that carries it.
    (_SUBSTRATE, "tortoise/search_engine.py:3508", _SE, 3508, "``:Source``/``:Document`` row must not contribute"),
    (_SUBSTRATE, "`:3506`", _SE, 3506, "# #11682: the corpus join is PER FAMILY"),
    # ⛔ The two `:Document` addresses §5's #12915 move names had NO pin, and that is how BOTH shipped
    # one line high — `:37143`/`:37185` are the `:Source` lines of those two docstrings, so the cites
    # pointed a reader just OUTSIDE the set §5 censuses (§5 counts `git grep -n` lines carrying
    # `:Document`; `grep -nF 'D10: a document is a ``:Source``, not a'` returns the line ABOVE the
    # `:Document` one, which is how the +35-instead-of-+36 arithmetic was introduced and never caught).
    # Pinned by CONTENT on the line that carries `:Document`, so the next re-measure by that phrase reds.
    (_SUBSTRATE, 'tortoise/sdk.py:37958', _SDK, 37958, "``:Document`` node)."),
    (_SUBSTRATE, '`:38000` in `_file_human_approval`', _SDK, 38000, "``:Document`` node)."),
    # ⛔ Review cycle 2 (P1): these were RE-POINTED and left with no content pin, so a one-line
    # regression shipped green — MEASURED by mutating each destination by one line and watching the
    # whole suite stay at `31 passed`. A re-point nobody grades is the `#13854` class this file exists for.
    (_INDEX, 'sdk.py:22197', _SDK, 22197, '"stats": _capture_stats_with_step_witness('),
    (_SUBSTRATE, 'opening `:37938`', _SDK, 37938, "def record_decision(self, approver_id: str, artifact_id: str,"),
    # ⛔ ADOPTED at the `origin/main` rebase (`#14416`'s fold): `main` added this row for the `:Source`
    # line BETWEEN the two `opening` defs, after a merge resurrected the `+21`-era siblings
    # (`(opening :37548` = a kwarg line) while the two graded headlines stayed right — green pins over
    # a wrong sentence (`#14038` review, cycle 2). It grades a SURVIVING document, so this cut keeps
    # it: only the `_AUDIT` rows go. Re-verified by content at the rebased head, not carried.
    (_SUBSTRATE, '`:37957` is the `:Source` line', _SDK, 37957, "D10: a document is a ``:Source``, not a"),
    (_SUBSTRATE, 'opening `:37978`', _SDK, 37978, "def _file_human_approval(self, approver_id: str, artifact_id: str,"),
    # --- docs/research/2026-11-13-12298-m3-fts-leg/research-brief.md
    (_M3_BRIEF, "tortoise/search_engine.py:2945", _SE, 2945, "def run_fts_query"),
    (_M3_BRIEF, "search_engine.py:2787", _SE, 2787, "def recency_tie_clause"),
    (_M3_BRIEF, "tortoise/search_engine.py:5074", _SE, 5074, "for rank, (pid, _score) in enumerate(ranked)"),
    (_M3_BRIEF, "`search_engine.py:4993`", _SE, 4993, "def rrf_fusion("),
    # --- docs/architecture/ENTITY-VERDICT-BANDS.md — the D-1 row "the retrieval filter is status-only"
    # ⚠️ This row is a SECOND false correction, of the same class this file exists for. The document
    # cited `search_engine.py:2973-2997`; other PRs' inserts had already moved the status filter to
    # `:3020` on `origin/main`, so the cite was ~44 lines stale BEFORE the branch's own `+3`. Adding the
    # `+3` therefore preserved the staleness while reading as a correction, and `2976-3000` is the
    # `build_or_query` byte-identity comment block, not the filter. The pin below is anchored on the line
    # that IS the filter, so the next offset-only edit reds instead of shipping.
    (_EVIDENCE, "search_engine.py:3138-3153", _SE, 3138, "if label in _STATUS_FILTERED_LABELS"),
    # --- docs/evidence/13083-capability-census/README.md
    (_CENSUS, "cache_projection.py:2776", _CP, 2776, "tail_denied = forbidden | {"),
    # --- docs/00_index.md — its entity-verdict row carries a SECOND present-tense this-head address
    # (`entity_verdict.py:43`) besides the pinned `sdk.py:22127`; the neighbour was unpinned, so a
    # rotation of `entity_verdict.py` under that cite would have moved it silently.
    (_INDEX, "`entity_verdict.py:43`", _EV, 43, "writing `dedup_reviewed='link'` is worse than inert"),
    # ⛔ These three rows spell their document path LITERALLY, not through a constant — so a
    # mechanical re-cut keyed on the constants (how this file's audit rows were removed on
    # 2026-11-23) drops them from a handful of rows to `0` while the documents still carry the
    # tokens and still hold. They are the SAME `sdk.py` pair `00_index.md` and
    # `ENTITY-VERDICT-BANDS.md` each cite, plus the merge call below it; re-resolved at this head
    # before being restored, not assumed.
    ("docs/00_index.md", '`sdk.py:28705` at this head', _SDK, 28705, "WHERE n.dedup_candidate = true AND n.dedup_reviewed IS NULL"),
    ("docs/architecture/ENTITY-VERDICT-BANDS.md", '(`sdk.py:28705`), so any non-NULL', _SDK, 28705, "WHERE n.dedup_candidate = true AND n.dedup_reviewed IS NULL"),
    ("docs/architecture/ENTITY-VERDICT-BANDS.md", 'called at `sdk.py:29069`', _SDK, 29069, "result = apply_entity_merge("),
    # --- docs/plans/2026-09-25-4997-vector-index-parity.md — the reader table cites
    # `hosted_backup.py:2732` for `_index_has_is_operator()`, and that document had NO pin at all,
    # so nothing would have reddened the next rotation of it. ⛔ RE-DERIVED BY CONTENT at the
    # `#14560` head (`2683` on `main` → `2732` here, +11 from that PR's `hosted_backup.py` hunk and
    # +38 from the `#14560` review-F5 hold's docblock and prose): an offset-only edit would have
    # carried the cite TWO shifts' worth of staleness while reading as a correction.
    (_PLAN_4997, "tortoise/hosted_backup.py:2732", _HB, 2732, "def _index_has_is_operator() -> bool:"),
    # --- docs/evidence/13083-capability-census/README.md — the PROBE's two `file:line` cites.
    # ⛔ Added 2026-11-23: deleting the census document rewrote `probe_shape_bytes.py`'s docstring,
    # which was one line SHORTER than the rewrite, so both of the README's cites into that file
    # landed one line high (`:286`/`:195` -> `:287`/`:196`) and nothing red — the sweep in
    # `test_docs_citation_refs.py` is presence-only, so a shift onto a different non-blank line
    # stays green. That is this file's whole defect class, so the re-point carries a content pin.
    (_CENSUS, "`probe_shape_bytes.py:287`", "docs/evidence/13083-capability-census/probe_shape_bytes.py",
     287, 'sdk._create_point("statement", "the shape census claim"'),
    (_CENSUS, "`probe_shape_bytes.py:196`", "docs/evidence/13083-capability-census/probe_shape_bytes.py",
     196, "MATCH (n) DETACH DELETE n"),
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
    """The instrument: every row holds, on THIS tree."""
    errors = _resolve(_PINS)
    assert not errors, "\n".join(errors)


def test_the_resolver_reds_on_a_double_applied_offset() -> None:
    """Negative control — the exact defect: the loop's cite re-pointed at the `#7704` note below it.

    Reachable: it is the shipped pre-fix state of the m3 brief, reintroduced here by mutating a COPY of
    the shipped row with the cite token LEFT INTACT, so the failure is reported by the CONTENT arm and
    cannot be satisfied by the token arm (which is what a re-pointed token would trip first).

    The spurious line is DERIVED FROM CONTENT, not hardcoded: the note it must land on has moved twice
    since this control was written, and a hardcoded line number rotted into a line that no longer holds
    the note — the do-nothing control then failed for the wrong reason (the note check, not the
    content arm).
    """
    loop_line = 5074
    note_line = next(
        i + 1
        for i, line in enumerate((REPO / _SE).read_text(encoding="utf-8").splitlines())
        if "#7704 (MEASURED MECHANISM" in line
    )
    assert note_line > loop_line, "the `#7704` note must sit BELOW the loop it annotates"
    row = (_M3_BRIEF, "tortoise/search_engine.py:5074", _SE, loop_line,
           "for rank, (pid, _score) in enumerate(ranked)")
    doubled = (row[0], row[1], row[2], note_line, row[4])
    errors = _resolve((doubled,))
    assert errors, "a spurious +3 must red the resolver"
    assert f":{note_line}" in errors[0] and "#7704" in errors[0]
    assert "is gone" not in errors[0], "the content arm must be the one that fires"


def test_the_resolver_reds_when_the_document_no_longer_carries_the_cite() -> None:
    """Negative control for the other arm: a dropped/re-pointed cite cannot pass by being absent."""
    row = (_M3_BRIEF, "search_engine.py:99999", _SE, 5074,
           "for rank, (pid, _score) in enumerate(ranked)")
    errors = _resolve((row,))
    assert errors, "a cite the document does not carry must red the resolver"
    assert "is gone" in errors[0]


def test_the_pinned_documents_are_the_ones_this_diff_shifted() -> None:
    """The pin set is bounded to a named document list — it must not silently shrink.

    Reachable: a row deleted to make the suite pass is the failure mode; this count moves with a
    documented ADDITION only, never with a deletion.
    """
    docs = {row[0] for row in _PINS}
    assert docs == {_SUBSTRATE, _M3_BRIEF, _CENSUS, _EVIDENCE, _INDEX, _PLAN_4997}
    # 119 -> 21 at the test-bureaucracy cut: 101 of the 119 rows held at `origin/main`
    # `0fbfbb5b210c` pinned the deleted
    # `CAPABILITY-PLACEMENT-AUDIT.md` (counted BY DOCUMENT, which is why the number is larger than
    # the 88 that name the `_AUDIT` constant — thirteen rows spell that path literally), and the
    # audit-derived census/enumeration rows around them went with the apparatus that graded that
    # document's prose. Of the 18 rows that survive the cut, all 18 are for the six documents above
    # and are otherwise UNCHANGED: three of them were briefly dropped by this cut because a re-cut
    # keyed on the constants misses a row that spells its path literally, which is the anti-shrink
    # arm doing its job (#14428 review round 1, P1). The arithmetic, measured by AST against
    # `origin/main:tests/test_12184_shifted_cites.py` rather than counted by hand: the base holds 18
    # surviving rows and this head holds 21. The three that are not survivors are `_CENSUS`'s two
    # probe re-points (the cut's own docstring rewrite moved them one line, and this file's rule is
    # that every re-point carries a content pin) plus `_INDEX`'s `sdk.py:22127`, which the base
    # WRITES but does not PIN — its row was lost as a casualty of `#14038`'s merge resolution, and an
    # address a surviving document writes with no row is the uncovered-claim class this file refuses.
    # Two of the eighteen are re-worded at the rebase (`(opening `:37815`` -> `opening `:37815``, and
    # likewise `:37855`) with STRICTER fragments, so their coverage grew and none was dropped.
    # Add a row when a real correction touches an unpinned site, and update this count in the same commit.
    assert len(_PINS) == 21, (
        f"_PINS carries {len(_PINS)} rows, not 21 — a re-pointed cite was added or dropped without "
        "this count. Every re-point a branch writes must carry a content pin."
    )


#: ⛔ RESTORED (#13494, fresh-context review at `1038810281`), and kept through the 2026-11-23
#: cut. This arm exists on `origin/main` and was DROPPED once before by the `#13345` integration
#: merge (`66e5fe9293`) without a word in any commit message; every later forward merge re-dropped
#: it because the branch tip already lacked it. Deleting it bought nothing and cost the only arm
#: that DERIVES an address instead of comparing two hardcoded literals. `_PINS` cannot replace it:
#: a coordinate edit of the doc token, the pin line and the pin fragment satisfies every pin while
#: two documents still name two different lines. It reads ONLY documents that survive the cut, so
#: no part of row B2 reaches it — which is why it is here and not in the retired apparatus.

def test_the_dedup_filter_is_one_address_in_both_documents_that_promise_it() -> None:
    """The CROSS-DOCUMENT arm — `_PINS` cannot see it.

    `docs/00_index.md`'s D-1 row states the promise in as many words: *"the filter it names is at
    `sdk.py:<n>` at this head, and `ENTITY-VERDICT-BANDS.md` carries the same number"*. Every row in
    `_PINS` is satisfied by its OWN document, so two documents naming two DIFFERENT lines both pass every
    pin — which is the shipped `#13345` state, measured: `docs/00_index.md` `sdk.py:27943` (prose at the
    head, and the filter at no head) against `ENTITY-VERDICT-BANDS.md` `sdk.py:27938` (the value before
    `#13290` moved it). The row that claims agreement was the only thing that had to hold, and nothing
    checked it: the `+5` delta was applied to one document and not the other, and the promise broke
    while reading as a correction.

    NON-VACUOUS on that tree: the two addresses differ (`27866` vs `27861`), so the first assertion reds;
    and neither opens on `dedup_reviewed IS NULL`, so the second reds independently. The address is
    read out of each document by regex rather than hardcoded, so this arm survives a legitimate future
    re-derivation of BOTH rows together (which is the only edit that keeps the promise true).
    """
    def address_beside(doc_rel: str) -> str:
        lines = (REPO / doc_rel).read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            if "dedup_reviewed" not in line:
                continue
            # `ENTITY-VERDICT-BANDS.md` puts the address on the line AFTER the marker; `00_index.md`
            # carries both on one line. One line of slack covers both, and the regex is anchored on
            # the qualified `sdk.py:` form so a bare `:NNNN` elsewhere on the line cannot be picked up.
            window = " ".join(lines[i:i + 2])
            found = re.search(r"sdk\.py:(\d{4,5})", window)
            if found:
                return found.group(1)
        raise AssertionError(
            f"{doc_rel} carries no `sdk.py:NNNN` address beside its `dedup_reviewed` marker — the D-1 "
            "filter cite was dropped rather than re-derived."
        )

    index_addr = address_beside(_INDEX)
    bands_addr = address_beside(_EVIDENCE)
    assert index_addr == bands_addr, (
        f"the two documents disagree on the D-1 dedup filter: `docs/00_index.md` says `sdk.py:{index_addr}` "
        f"and `ENTITY-VERDICT-BANDS.md` says `sdk.py:{bands_addr}`, while `docs/00_index.md` promises the "
        "latter \"carries the same number\". Only one address can open at any head, so one of the two "
        "readers is sent to the wrong line. Re-derive BOTH from the statement "
        "(`grep -n 'dedup_reviewed IS NULL' tortoise/sdk.py`) rather than re-pointing one by a delta."
    )
    target = (REPO / _SDK).read_text(encoding="utf-8").splitlines()
    assert 0 < int(bands_addr) <= len(target), (
        f"`sdk.py:{bands_addr}` is out of range ({len(target)} lines) — the file shrank under the cite."
    )
    assert "dedup_reviewed IS NULL" in target[int(bands_addr) - 1], (
        f"both documents agree on `sdk.py:{bands_addr}`, but that line is "
        f"{target[int(bands_addr) - 1].strip()[:100]!r} — agreeing on the wrong line is still wrong, and "
        "it is the reason this arm checks content as well as agreement."
    )


#: Every address a TOKEN names, as `(first, last)` spans. One span per match, never the hull of
#: a token's numbers — see `_token_spans`.
_TOKEN_SPAN = re.compile(r":(\d+)(?:-(\d+))?")


def _token_spans(token: str) -> list[tuple[int, int]]:
    """EVERY address the *token* names, as `(first, last)` spans — empty when it names none.

    One span per address match, never the hull of a token's numbers: a token naming two
    unrelated addresses (`:58603`/`:58605`) names two lines, NOT the line between them. Taking
    `min..max` over every number turned such a token into a range, so the arm accepted the `58604`
    the token never mentions — the exact false PASS this arm exists to refuse, one indirection
    further out. Measured at this head: 0 of the surviving rows carries more than one address, so
    no row's verdict moves; only the door the next row could have walked through is closed.

    ⛔ RESTORED (round 4, P1) after the test-bureaucracy cut dropped it with the deleted
    document's apparatus. It grades `_PINS`, which SURVIVES: the subject is the pin table, not
    the census. Only its `_MAIN_SET_MISSED` leg is gone, because that table died with the
    document it described.
    """
    return [
        (int(match.group(1)), int(match.group(2) or match.group(1)))
        for match in _TOKEN_SPAN.finditer(token)
    ]


def _rows_outside_their_token(rows) -> list[str]:
    """Rows whose graded line is NOT inside the address the row's OWN token names.

    *rows* is `_PINS`' 5-tuples; only the token, the target and the graded line are read.
    """
    problems: list[str] = []
    for row in rows:
        token, target_rel, line = row[1], row[2], row[3]
        spans = _token_spans(token)
        if not spans:
            # A row whose token names NO address cannot be checked by this arm, so passing it
            # silently would be the arm vouching for a claim it never read (fail-open).
            problems.append(
                f"{target_rel}: the row pins `{token}`, which names NO line address, so this "
                f"arm cannot check the line it GRADES ({line}) — an unverifiable row passes by "
                "being unreadable, which is a fail-open in a gate. Give the token the address "
                "the fragment sits on."
            )
            continue
        if any(first <= line <= last for first, last in spans):
            continue
        named = " / ".join(
            f"{first}" if first == last else f"{first}..{last}" for first, last in spans
        )
        problems.append(
            f"{target_rel}: the row pins `{token}`, which names {named}, but it "
            f"GRADES line {line} — a graded line outside its own token is a DEFENDER: the "
            "content arm opens the line the fragment lives on while the token arm only asks "
            "whether the string occurs anywhere in the document, so a document that names a "
            "stale address for that line stays green. Re-point the DOCUMENT to the address the "
            "fragment actually sits on, then re-point this row to the same number."
        )
    return problems


def test_every_pinned_row_grades_a_line_inside_the_address_its_token_names() -> None:
    """The token IS the claim: a row that grades some other line defends a stale cite.

    MEASURED, and shipped in the reviewed tree: two rows pinned a STALE address while grading the
    CORRECT line — `(":31496", sdk.py, 31551)` for the §2.5 `:Tag` header entry and
    `(":37534` in `_file_human_approval`", sdk.py, 37547)` in the substrate map. Both arms were
    green: `_resolve`'s content arm opened `31551` / `37547` (where the fragment is), and its token
    arm only asks whether the token string occurs SOMEWHERE in the document (it did). So the
    document could name an address 55 lines above the statement it describes — the `#13290` class this
    file exists to stop — with nothing red.

    A RANGE is anchored on the line inside it that carries the fragment (the file's stated
    convention), so the arm accepts any line in `START..END`; a single-value token must grade that
    value exactly, and a token naming SEVERAL addresses accepts only a line one of them names —
    never the hull between them, and never nothing at all (the two fail-open doors the controls
    below re-open).

    Non-vacuity is the negative controls below, which re-introduce the shipped shapes.
    """
    problems = _rows_outside_their_token(_PINS)
    assert not problems, "\n".join(problems)


def test_every_pinned_token_occurs_exactly_once_in_its_document() -> None:
    """One pin, one SITE — a token occurring twice can have one site re-pointed with nothing red.

    This is the surviving half of `_TOKEN_OCCURRENCES` (round 4, P1). That table carried per-token
    counts AND a `declared == pinned` set equality; the set equality is subsumed by `_PINS`, but the
    COUNT was not, and the anti-shrink arm cannot express it: a row deleted and another added inside
    the same document set satisfies both `docs == {six}` and `len(_PINS) == 21`. Measured at this head:
    all 21 tokens occur exactly once, so the invariant is green and load-bearing rather than
    aspirational.

    ⛔ DERIVED, not transcribed: no count is written down here, because a transcribed count is the
    figure-rot class this file exists to catch (a table of 21 literals would be stale the moment a
    document legitimately mentions a site twice). The remedy for a genuine second mention is the
    file's own convention — carry enough TABLE context in the token to make it unique, and pin the
    second site as its own row.
    """
    repeated = []
    for doc_rel, token, _target, _line, _frag in _PINS:
        count = (REPO / doc_rel).read_text(encoding="utf-8").count(token)
        if count != 1:
            repeated.append(
                f"{doc_rel}: `{token}` occurs {count}x, not 1x — one site can now be re-pointed "
                "alone while this pin stays green. Make the token specific enough to name one site "
                "(add its table/sentence context), or pin each site as its own row."
            )
    assert not repeated, "\n".join(repeated)


def test_the_token_span_arm_reds_on_a_row_that_grades_another_line() -> None:
    """Negative control — the SHIPPED pre-fix shape: stale token, correct line, green.

    The synthetic rows name `_SUBSTRATE` rather than `_AUDIT` (the deleted document) because
    `_rows_outside_their_token` reads only the token, target and line; the doc field is carried for
    the error message. The `:37534` row below is the REAL shipped one, from the surviving
    `SUBSTRATE-REBUILD-MAP.md`; the document has since re-pointed the address to `:37877`, which
    is why the control's token is synthetic and reads no document.
    """
    stale = (_SUBSTRATE, "`:31496`", _SDK, 31551, "MATCH (p:Point)-[:TAGGED]->(t:Tag {name:$tag})")
    problems = _rows_outside_their_token((stale,))
    assert problems, "a token naming `:31496` must not be allowed to grade line 31551"
    assert "31496" in problems[0] and "31551" in problems[0], problems[0]
    # The substrate-map row the same review found, in its shipped form.
    shipped = (_SUBSTRATE, "`:37534` in `_file_human_approval`", _SDK, 37547, "``:Document`` node).")
    assert _rows_outside_their_token((shipped,)), "the `:37534` row graded 37547 must red"
    # A range token is accepted on any line INSIDE the range it names (the anchor convention).
    inside = (_SUBSTRATE, "`:7599-7602`", _SE, 7602, "MATCH path = (seed)-[:IMPL|NAND*1..{max_hops}]-(n:Point)")
    assert not _rows_outside_their_token((inside,)), "a line inside the named range must pass"


def test_a_token_naming_two_addresses_does_not_vouch_for_the_hull_between_them() -> None:
    """Negative control — the fail-open `min..max` left in this arm, re-opened here.

    On the PRE-FIX code `_token_span` returned `(min(numbers), max(numbers))` over EVERY number a
    token carries, so a token naming two unrelated addresses became a range spanning the gap: the
    arm accepted the `58604` neither address names. That is the same false PASS this arm exists to
    refuse — a row asserting an address the document does not carry — so it is refused here rather
    than recorded as a residual.
    """
    between = (_SUBSTRATE, "`:58603`/`:58605`", _SDK, 58604, "rows = proj.g.query(")
    assert _rows_outside_their_token((between,)), (
        "a token naming :58603 and :58605 must NOT vouch for the line between them"
    )
    # …and each address it DOES name is still accepted, so the tightening rejects only the hull.
    for named in (58603, 58605):
        row = (between[0], between[1], between[2], named, between[4])
        assert not _rows_outside_their_token((row,)), (
            f"{named} is an address the token names, so it must pass"
        )


def test_a_row_whose_token_names_no_address_is_reported_not_skipped() -> None:
    """Negative control — an unreadable row must not pass by being unreadable.

    On the PRE-FIX code `_token_span` returned `None` for a token with no digits and the loop
    `continue`d, so such a row was silently exempt from the arm — the gate vouching for a claim it
    never read.

    ⛔ NO ROW COUNT HERE ON PURPOSE: an earlier form said "0 of 112", then a peer lane added three
    `_PINS` rows and the sentence was silently two rows stale — the same unverified-literal class this
    file exists to catch, in prose. The arm scans whatever `_PINS` holds.
    """
    unaddressed = (_SUBSTRATE, "`_file_human_approval`", _SDK, 37547, "``:Document`` node).")
    assert _rows_outside_their_token((unaddressed,)), (
        "a row whose token names no address must be REPORTED, not skipped"
    )


def test_the_resolve_content_arm_is_unchanged_by_the_token_arm() -> None:
    """The two arms are independent: a row can be content-correct AND token-stale.

    `_resolve` answers two questions — does the token still occur, and does the GRADED line carry
    the fragment — and a row that pins one address while grading another satisfies BOTH (the token
    exists, and the graded line does carry the fragment). That is what the shipped rows
    demonstrated, and why a separate arm was needed instead of a stricter `_resolve`.

    Built from REAL surviving artifacts rather than a synthetic token: `SUBSTRATE-REBUILD-MAP.md`
    carries both `:3506` and `search_engine.py:3508`, and line 3508 is the line that carries the
    fragment, so this row is exactly the shape — content arm silent, token arm red.
    """
    correct_line_wrong_token = (
        _SUBSTRATE, "`:3506`", _SE, 3508, "``:Source``/``:Document`` row must not contribute",
    )
    assert _resolve((correct_line_wrong_token,)) == [], (
        "the content arm must stay silent here — that silence is why the token arm exists"
    )
    assert _rows_outside_their_token((correct_line_wrong_token,)), (
        "the token arm must red on the same row the content arm passes"
    )


# ⛔ CUT (B4 test-bureaucracy cut, 2026-11-23): `test_the_epic_bundles_i7_prose_cites_the_range_
# its_row_carries` lived here. It compared a `:START-END` label printed in
# `tests/test_7949_capstone_measure.py` against `04-plan.md`'s I7 row — progress-DOC prose, and
# both of its inputs are deleted by this batch (the label went with the org_info span pin). The
# arm cannot run without them, and the alternative — planting the label in a surviving comment —
# would leave it grading prose that nothing else verifies.

