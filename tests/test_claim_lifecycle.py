"""Claim lifecycle tests — #432 Task 1 (state vocabulary + transition guards).

State vocabulary since #11824 S0 and ONTOLOGY v3.36/v3.37: **four** words,
draft → live → deprecated/superseded (`superseded` names its successor; if
nothing replaced it the word is `deprecated`). `retracted`/`outdated`/`archived`
are NOT states — they are read-side legacy over stored rows, and the
`retracted` one is still produced by `retract_point`'s own CAS until #11824 S2
migrates that verb (the §5 note discloses exactly that residual). challenged is
DERIVED (NAND-operator edge on a live point), NOT a state. update_point only
promotes draft→live; retract/supersede are the lifecycle-transition paths and
are terminal.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from tortoise.live import is_terminal_status
from tortoise.sdk import POINT_STATUS_VALUES


def test_status_vocabulary():
    # ⚠️ #11824 S0 NARROWED this deliberately, and the narrowing is the slice's
    # deliverable: every knowledge kind's `status` is the same closed FOUR words,
    # because the rule that separates the two endings is one rule — `superseded`
    # NAMES ITS SUCCESSOR, and if nothing replaced it the word is `deprecated`.
    # The previous six (`retracted`, `outdated`, `archived` extra) were the #432
    # set plus two words that were never lifecycle states: `outdated` (meaning
    # already carried by the pair, and ambiguous between them) and `archived` (a
    # collision of "this ended" with "do not show me this").
    #
    # ⛔ The READ vocabulary did NOT narrow (`live.TERMINAL_EXCLUDED_STATUSES`
    # still names every legacy word) — stored rows keep what they hold.
    assert POINT_STATUS_VALUES == frozenset(  # noqa: SIM300
        {"draft", "live", "deprecated", "superseded"}
    )
    assert "challenged" not in POINT_STATUS_VALUES
    # the retired words are refused at the door, not merely absent from the set
    for retired in ("outdated", "archived", "retracted"):
        assert retired not in POINT_STATUS_VALUES


def test_transition_guards_live_to_draft(sdk_factory, tmp_path):
    sdk = sdk_factory(tmp_path)
    # Draft is explicit since #1088 (a no-status create is LIVE) — the guard
    # under test is the draft→live promote, so start from a real draft.
    p = sdk.create_point("statement", "guarded", status="draft")
    assert p["status"] == "draft"
    sdk.update_point(p["id"], status="live")  # draft→live promote still allowed
    with pytest.raises(ValueError, match="live"):
        sdk.update_point(p["id"], status="draft")  # any non-promote status rejected


def test_update_point_rejects_status_changes_except_promote(sdk_factory, tmp_path):
    # plan-review P1: update_point is non-status except draft→live promote
    sdk = sdk_factory(tmp_path)
    p = sdk.create_point("statement", "status-guard")
    for bad in ("retracted", "superseded", "outdated", "archived"):
        with pytest.raises(ValueError, match="retract_point|supersede_point"):  # noqa: RUF043
            sdk.update_point(p["id"], status=bad)


def test_retract_tombstone_status(sdk_factory, tmp_path):
    sdk = sdk_factory(tmp_path)
    p = sdk.create_point("statement", "tombstone-me")
    r = sdk.retract_point(p["id"])
    assert r["status"] == "retracted"


def test_retracted_is_terminal(sdk_factory, tmp_path):
    sdk = sdk_factory(tmp_path)
    p = sdk.create_point("statement", "terminal")
    sdk.retract_point(p["id"])
    with pytest.raises(ValueError):
        sdk.retract_point(p["id"])  # already retracted
    with pytest.raises(ValueError):
        sdk.update_point(p["id"], status="live")  # terminal → no promote


def test_retract_archived_is_terminal(sdk_factory, tmp_path):
    # plan-review P2 boundary: archived is terminal. v1 has no SDK path to
    # archived (reserved), so set it via the graph directly to exercise the guard.
    sdk = sdk_factory(tmp_path)
    p = sdk.create_point("statement", "archived")
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) SET n.status='archived'", params={"id": p["id"]})
    with pytest.raises(ValueError, match="already"):
        sdk.retract_point(p["id"])


def test_retract_point_missing_and_operator_guards(sdk_factory, tmp_path):
    sdk = sdk_factory(tmp_path)
    with pytest.raises(ValueError, match="No point"):
        sdk.retract_point("does-not-exist")
    s = sdk.create_point("statement", "src")
    t = sdk.create_point("statement", "tgt")
    op = sdk.create_operator("IMPL", s["id"], [t["id"]])
    with pytest.raises(ValueError, match="operator"):
        sdk.retract_point(op["id"])  # operators are not retractable


def test_supersede_sets_status_and_keeps_flag(sdk_factory, tmp_path):
    sdk = sdk_factory(tmp_path)
    old = sdk.create_point("statement", "old")
    new = sdk.create_point("statement", "new")
    sdk.supersede_point(old["id"], new["id"])
    got = sdk.get_point(old["id"])
    assert got["status"] == "superseded"
    assert got.get("outdated") is True


# ── Task 2: retraction-as-absence consumer audit ────────────────────────

def test_query_excludes_retracted_by_default(sdk_factory, tmp_path):
    # #432 Task 2: query() omits retracted points by default; the
    # include_retracted flag surfaces them (tombstone contract).
    sdk = sdk_factory(tmp_path)
    p = sdk.create_point("statement", "gone")
    sdk.retract_point(p["id"])
    assert p["id"] not in [q["id"] for q in sdk.query()]
    assert p["id"] in [q["id"] for q in sdk.query(include_retracted=True)]


def test_paginated_query_excludes_retracted_by_default(sdk_factory, tmp_path):
    sdk = sdk_factory(tmp_path)
    live = sdk.create_point("statement", "stay")
    gone = sdk.create_point("statement", "gone")
    sdk.retract_point(gone["id"])
    page = sdk.paginated_query(kind="statement")
    assert [r["id"] for r in page["results"]] == [live["id"]]
    assert page["total"] == 1
    page_all = sdk.paginated_query(kind="statement", include_retracted=True)
    assert {r["id"] for r in page_all["results"]} == {live["id"], gone["id"]}
    assert page_all["total"] == 2


def test_query_explicit_retracted_status_filter(sdk_factory, tmp_path):
    # tombstone contract: an explicit status='retracted' filter is the
    # queryable-with-filter path (no include_retracted needed).
    sdk = sdk_factory(tmp_path)
    p = sdk.create_point("statement", "gone")
    sdk.retract_point(p["id"])
    hits = sdk.query(status="retracted")
    assert [q["id"] for q in hits] == [p["id"]]
    assert p["id"] not in [q["id"] for q in sdk.query(status="live")]


def test_retracted_point_retrievable_by_id(sdk_factory, tmp_path):
    # tombstone contract: get_point on a retracted point returns it with
    # status='retracted' (not {} / 404).
    sdk = sdk_factory(tmp_path)
    p = sdk.create_point("statement", "gone")
    sdk.retract_point(p["id"])
    got = sdk.get_point(p["id"])
    assert got["id"] == p["id"]
    assert got["status"] == "retracted"


# ── #690 parity tests: vocabulary ↔ code consistency ──────────────────

def test_code_writes_only_valid_statuses(sdk_factory, tmp_path):
    """Every status the code writes is in POINT_STATUS_VALUES."""
    from tortoise.sdk import POINT_STATUS_VALUES
    sdk = sdk_factory(tmp_path)

    # create_point: no-status is LIVE since #1088; an explicit draft is
    # honoured and never auto-promoted (see the p3 update below)
    p = sdk.create_point("statement", "parity-1")
    assert p["status"] in POINT_STATUS_VALUES

    # create_operator: promotes a NEVER-SET source to live. Since #1088
    # create_point always sets a status, so this pins the resulting value —
    # the never-set promotion path is only reachable from legacy graphs.
    p2 = sdk.create_point("statement", "parity-2")
    op = sdk.create_operator("IMPL", p2["id"], [p["id"]])  # noqa: F841
    assert sdk.get_point(p2["id"])["status"] == "live"
    assert "live" in POINT_STATUS_VALUES

    # retract_point: sets the LEGACY word `retracted`
    r = sdk.retract_point(p["id"])
    # ⚠️ #11824 S0 states a RESIDUAL here rather than work around it: the verb
    # still writes `retracted` through its own CAS, while the write DOOR's
    # vocabulary is the four words. Migrating the verb to `deprecated` is plan
    # slice S2 (`retract_point` targets `deprecated`), and it is not done in S0 —
    # so the assertion is inverted to pin the truth: the value the ENGINE produces
    # is a READ-side legacy word, not a word a caller may mint.
    assert r["status"] == "retracted"
    assert "retracted" not in POINT_STATUS_VALUES
    # …and it is terminal to every reader, which is why the residual is safe.
    assert is_terminal_status("retracted")

    # supersede_point: sets superseded
    old = sdk.create_point("statement", "old-parity")
    new = sdk.create_point("statement", "new-parity")
    sdk.supersede_point(old["id"], new["id"])
    assert sdk.get_point(old["id"])["status"] == "superseded"
    assert "superseded" in POINT_STATUS_VALUES

    # update_point: can promote draft→live (status='live' set)
    p3 = sdk.create_point("statement", "parity-3", status="draft")
    sdk.update_point(p3["id"], status="live")
    assert sdk.get_point(p3["id"])["status"] == "live"


def test_all_documented_transitions_allowed(sdk_factory, tmp_path):
    """Every transition the MODEL declares is allowed by _ALLOWED_TRANSITIONS."""
    from tortoise.sdk import _ALLOWED_TRANSITIONS

    # #11824 S0: the LIVE rows now offer `deprecated` — it is a declared word of
    # the four-word vocabulary, and before S0 it was absent from
    # POINT_STATUS_VALUES, so the vocabulary gate raised before this table was
    # ever read.
    #
    # draft → live, deprecated, superseded      (#432: a draft can end)
    # live  → deprecated, superseded
    #
    # ⛔ The legacy spellings keep TERMINAL rows rather than being deleted, and
    # that is a READ delta, not a write path: `retracted` is still written by
    # `retract_point`'s own CAS (S2 migrates it), `outdated`/`archived` are words
    # stored rows hold. A stored word with NO row would DEFER — i.e. silently
    # become transitionable — which is the opposite of terminal.
    #
    # ONTOLOGY §5 now states the same four words (owner-approved as v3.37), so
    # this map's legacy rows are the CODE side of a disclosed delta, not a delta
    # the canonical table contradicts. `tests/test_claim_lifecycle.py`'s
    # `test_ontology_status_table_matches_the_code_vocabulary` below keeps the
    # two from drifting apart again in either direction.

    assert "live" in _ALLOWED_TRANSITIONS["draft"], \
        "draft → live must be allowed"
    assert "deprecated" in _ALLOWED_TRANSITIONS["draft"], \
        "draft → deprecated must be allowed (#11824 S0)"
    assert "superseded" in _ALLOWED_TRANSITIONS["draft"], \
        "draft → superseded must be allowed (#432: draft can go terminal)"
    assert "deprecated" in _ALLOWED_TRANSITIONS["live"], \
        "live → deprecated must be allowed (#11824 S0)"
    assert "superseded" in _ALLOWED_TRANSITIONS["live"], \
        "live → superseded must be allowed"
    # …and NO live row still offers a retired spelling (the door refuses it, so
    # a transition entry would name a target no write can reach).
    for retired in ("retracted", "outdated", "archived"):
        assert retired not in _ALLOWED_TRANSITIONS["draft"], retired
        assert retired not in _ALLOWED_TRANSITIONS["live"], retired
    assert _ALLOWED_TRANSITIONS["outdated"] == frozenset(), \
        "outdated is terminal — no outgoing transitions (#2498)"
    assert _ALLOWED_TRANSITIONS["retracted"] == frozenset(), \
        "retracted is terminal — no outgoing transitions"
    assert _ALLOWED_TRANSITIONS["superseded"] == frozenset(), \
        "superseded is terminal — no outgoing transitions"
    assert _ALLOWED_TRANSITIONS["deprecated"] == frozenset(), \
        "deprecated is terminal — no outgoing transitions (#11824 S0)"
    assert _ALLOWED_TRANSITIONS["archived"] == frozenset(), \
        "archived is terminal — no outgoing transitions"


#: ONTOLOGY §5's model table — the heading, and the next top-level section that ends it.
_ONTOLOGY = Path(__file__).resolve().parent.parent / "docs" / "ONTOLOGY.md"
_STATUS_HEADING = "### Point Status Vocabulary"


def _ontology_status_section() -> str:
    """§5's *Point Status Vocabulary* section, from its heading to the next ``## ``."""
    text = _ONTOLOGY.read_text(encoding="utf-8")
    try:
        tail = text[text.index(_STATUS_HEADING):]
    except ValueError as e:  # a renamed heading must FAIL, never silently skip
        raise AssertionError(
            f"ONTOLOGY.md no longer contains the §5 heading {_STATUS_HEADING!r}; "
            "update this guard's anchor rather than dropping the check") from e
    end = re.search(r"\n## ", tail)
    return tail if end is None else tail[: end.start()]


#: The markdown BLOCK STARTS that end a lazy paragraph continuation (CommonMark).
#: Prose that merely begins with a word — or with a code span — keeps continuing.
_BLOCK_START_RE = re.compile(r"^(?:[#>|]|[-*+]\s|\d+[.)]\s|=+\s*$)")


def _unwrap(text: str) -> str:
    """A blockquote paragraph as COMPARABLE text: `>` markers off, whitespace flat.

    Joining the raw lines made a cosmetic rewrap a RED pin (see the docstring below),
    so the anchor search and every word assertion read this form. Markdown line
    wrapping carries no meaning; the sentence does.
    """
    lines = [re.sub(r"^\s*(?:>\s?)+", "", line) for line in text.splitlines()]
    return " ".join(" ".join(lines).split())


def _read_side_legacy_note(section: str | None = None) -> str:
    """§5's read-side legacy note: the ONE blockquote PARAGRAPH that says it.

    Scoped to a paragraph, not to §5, because the three retired words appear in
    text that is NOT the disclosure: the pre-v3.37 table carried them as status
    ROWS, and §5's tombstone contract names `status='retracted'` in the SAME
    blockquote. A section-wide search was satisfied by both, so it was green on
    the buggy table this note exists to correct.

    ⛔ A BLANK-LINE split is not a paragraph split here. MD028 keeps §5's notes as
    ONE blockquote, so its paragraphs are separated by bare `>` lines and never by
    a blank line — splitting on `\\n\\s*\\n` returns the whole quote group, which
    admits the tombstone contract (and any word a later edit puts there) as if it
    were the disclosure. A bare `>` is therefore the break.

    Reads the paragraph's `>` continuation lines with it — and a CommonMark LAZY
    continuation too (a wrapped line may omit the `>` marker; the softbreak renders
    as a space, so the sentence is unchanged). ⛔ WHICH IS WHY THE ANCHOR AND THE
    WORDS ARE MATCHED UNWRAPPED: the comparison text is the paragraph with its `>`
    markers stripped and its whitespace collapsed (`_unwrap`). Matching the RAW lines
    made the promise above false and reddened a pure rewrap with a message claiming
    the disclosure was MISSING (v3.37 review, rounds 2-4).

    ⛔ AND TWO NOTES ARE REFUSED, NOT JOINED. The paragraph break above replaced a
    blank-line split whose uniqueness assert (``§5 has 2 blockquote paragraph(s)
    naming 'read-side legacy'``) was the only thing stopping a GUTTED disclosure
    from borrowing another paragraph's words: joining them makes the disclosure
    assertions pass on a note that names none of the retired words (measured —
    deleting the words from the real note and adding them to a second paragraph
    beside it left the pin green). Exactly one paragraph must say it.

    Returns ``""`` when no such paragraph exists, so the caller's assertion fails
    with the reason rather than this helper raising on a doc that lost the note.
    ``section`` is injectable so the non-vacuity pin can drive it with the pre-v3.37
    shape (words in the table, no note) without touching the real file.
    """
    paragraphs: list[list[str]] = []
    current: list[str] = []
    for line in (_ontology_status_section() if section is None else section).splitlines():
        stripped = line.lstrip()
        if not stripped.startswith(">"):
            # ⛔ A LAZY CONTINUATION STILL BELONGS TO THE PARAGRAPH (CommonMark): an
            # unquoted line that does not START a new block continues the open
            # paragraph, and its softbreak renders as a space. Only a blank line, a
            # bare `>`, or a real block start ends it — otherwise a rewrap that drops
            # the marker would red as "the disclosure is missing".
            if current and stripped and not _BLOCK_START_RE.match(stripped):
                current.append(stripped)
                continue
            if current:
                paragraphs.append(current)
                current = []
            continue
        if not stripped[1:].strip():  # a bare `>` is the MD028 paragraph break
            if current:
                paragraphs.append(current)
                current = []
            continue
        current.append(stripped)
    if current:
        paragraphs.append(current)
    matches = [
        paragraph for paragraph in (_unwrap("\n".join(p)) for p in paragraphs)
        if "read-side legacy" in paragraph
    ]
    if not matches:  # the CALLER fails, and says which words went missing
        return ""
    assert len(matches) == 1, (
        f"§5 has {len(matches)} blockquote paragraph(s) naming 'read-side legacy'; "
        "exactly one note must disclose the retired words — joining them lets a "
        "gutted disclosure borrow the words of another paragraph in the same section")
    return matches[0]


def _ontology_status_table_words() -> frozenset[str]:
    """The status words §5's canonical model table lists, one per row."""
    section = _ontology_status_section()
    rows = [ln for ln in section.splitlines() if ln.startswith("|")]
    assert len(rows) >= 3, (
        f"§5's status table parsed to {len(rows)} table line(s) — no header + rows; "
        f"got {rows!r}")
    words: set[str] = set()
    for row in rows[1:]:  # rows[0] is the header
        first = row.split("|")[1].strip()
        if set(first) <= set("-: "):  # the |---|---| separator
            continue
        m = re.fullmatch(r"`([a-z_]+)`", first)
        assert m is not None, (
            f"§5's status table has a Status cell this guard cannot read: {first!r}")
        words.add(m.group(1))
    assert words, "§5's status table parsed to zero status words"
    return frozenset(words)


def test_ontology_status_table_matches_the_code_vocabulary():
    """The canonical §5 table states exactly the four words the code accepts.

    This is the leg that would have caught the drift ONTOLOGY v3.37 fixed: §5's
    table called itself canonical while listing **six** statuses and offering
    `live → retracted`, contradicting §4.1's approved closed set of four. Nothing
    parsed the table, so the two canonical statements disagreed for months and
    the code map was the only side anyone checked.

    NON-VACUOUS on the pre-v3.37 doc: it listed draft/live/retracted/superseded/
    outdated/archived, so this assertion fails on it in BOTH directions
    (`retracted`/`outdated`/`archived` declared but not accepted; `deprecated`
    accepted but undeclared).
    """
    declared = _ontology_status_table_words()
    assert declared == POINT_STATUS_VALUES, (
        "§5's canonical status table has drifted from the code's write vocabulary "
        f"(#11824).\n  in the table but not accepted: "
        f"{sorted(declared - POINT_STATUS_VALUES)}\n  accepted but not in the "
        f"table: {sorted(POINT_STATUS_VALUES - declared)}")


def test_ontology_transitions_column_matches_the_guard_map():
    """§5's `Transitions to` cells state the transitions the guards actually allow.

    The sibling test above pins the status WORDS; this one pins the EDGES between
    them, which is the other half of the same table and went stale the same way.
    The INTERMEDIATE v3.37 table on this branch (`17c81841e8`, before
    `cd38f7a74b`) stated `draft` → `live` alone while `_ALLOWED_TRANSITIONS`
    allows a draft point to END before it ever went live (`tortoise/sdk.py`:
    `"draft": frozenset({"live", "deprecated", "superseded"})`, with the
    guard-side comment saying so) — and this very file asserts
    `"deprecated" in _ALLOWED_TRANSITIONS["draft"]` twenty lines up, so the model
    and its own guard contradicted each other. v3.37 has never SHIPPED: the last
    released artifact, `origin/main`, is v3.36, and its own table reds this pin by
    the other path below.

    NON-VACUOUS on both shapes, by different paths (both measured at the refs, not
    reasoned). On that intermediate 4-column table `draft` parses to `{live}` and
    reds on the two missing edges. On `origin/main`'s §5 the table has FIVE columns
    (`Status | Kind | Write path | Transitions to | Notes`), so `cells[2]` is the
    *Write path* column and the parse yields `frozenset()` — the pin still reds,
    but on `set()` against `{live, deprecated, superseded}`, not on a missing edge.

    Both shapes are driven as fixtures in
    ``test_transitions_pin_reds_on_the_tables_it_was_written_for``, so the
    claim is checkable rather than merely confident.
    """
    _transition_edges_by_status(_ontology_status_section())


def test_transitions_pin_reds_on_the_tables_it_was_written_for():
    """Non-vacuity for ``test_ontology_transitions_column_matches_the_guard_map``.

    The pin above claims it would have caught two table shapes, and each claim is
    only worth the artifact behind it — so each shape is reproduced here in the
    shape the real doc carried, and the pin is driven with it. Nothing here
    touches ``docs/ONTOLOGY.md``; the section is injected.

    (a) the INTERMEDIATE v3.37 table (`17c81841e8` -> `cd38f7a74b`, never
        shipped): four columns, `draft` states `live` alone. The pin must red on
        the two MISSING EDGES and say ``['live']``.
    (b) the LAST SHIPPED artifact (`origin/main`, v3.36): FIVE columns, so the
        cell the pin reads is the `Write path` column — `create_point(status='draft')`
        and friends, which yield NO `` `word` `` run at all. The pin must still
        red (it is non-vacuous on main's real doc), but through an EMPTY stated
        set plus the six-row set assert — and its message must say ``[]``, which
        is the proof that main's shape is not the `{live}` shape.
    """
    intermediate_v337 = (
        "### Point Status Vocabulary (canonical, #432/#690)\n"
        "\n"
        "| Status | Kind | Transitions to | Notes |\n"
        "|--------|------|----------------|-------|\n"
        "| `draft` | initial (opt-in, #1088) | `live` | Inert for EP |\n"
        "| `live` | active | `deprecated`, `superseded` | Full EP |\n"
        "| `deprecated` | terminal | *(none)* | Ended (#11824) |\n"
        "| `superseded` | terminal | *(none)* | Replaced |\n"
    )
    with pytest.raises(AssertionError) as intermediate_red:
        _transition_edges_by_status(intermediate_v337)
    assert "states transitions ['live'] but the guards allow" in str(
        intermediate_red.value), (
        "the pin no longer reds on the intermediate v3.37 table's `draft` row "
        "through the two missing edges — that is the shape it was written for")

    # `origin/main`'s §5 (v3.36): the `Write path` column sits at cells[2].
    shipped_v336 = (
        "### Point Status Vocabulary (canonical, #432/#690)\n"
        "\n"
        "| Status | Kind | Write path | Transitions to | Notes |\n"
        "|--------|------|------------|----------------|-------|\n"
        "| `draft` | initial (opt-in, #1088) | `create_point(status='draft')`, "
        "`EventAPI._point` | `live` | Inert for EP |\n"
        "| `live` | active | `create_point`, `update_point` | `retracted`, "
        "`superseded` | Full EP |\n"
        "| `retracted` | terminal | `retract_point` | *(none)* | Tombstone |\n"
        "| `superseded` | terminal | `supersede_point` | *(none)* | Replaced |\n"
        "| `outdated` | terminal (legacy flag) | `invalidate_point` | *(none)* | Flag |\n"
        "| `archived` | terminal (reserved) | *(no v1 SDK write path)* | *(none)* | Reserved |\n"
    )
    with pytest.raises(AssertionError) as shipped_red:
        _transition_edges_by_status(shipped_v336)
    assert "states transitions [] but the guards allow" in str(shipped_red.value), (
        "the pin does not red on main's five-column table the way the docstring "
        "says it does (the Write path cell, parsed to an EMPTY set) — if it now "
        "reads `{live}` there, the column index moved and the docstring is wrong "
        "again")


def _transition_edges_by_status(section: str) -> dict[str, frozenset[str]]:
    """Each §5 status row's `Transitions to` cell, keyed by status word.

    Refuses rather than skipping on every shape it cannot read (a table of fewer
    than three lines, a Status cell that is not a bare `` `word` ``), so a table
    the pin cannot parse is a RED and never a silent pass. ``section`` is
    injectable so the non-vacuity pin can drive the two table shapes this guard
    was written for without touching the real file
    (``test_transitions_pin_reds_on_the_tables_it_was_written_for``).
    """
    from tortoise.sdk import _ALLOWED_TRANSITIONS

    rows = [ln for ln in section.splitlines() if ln.startswith("|")]
    assert len(rows) >= 3, f"§5's table parsed to {len(rows)} line(s); got {rows!r}"
    seen: dict[str, frozenset[str]] = {}
    for row in rows[1:]:
        cells = [c.strip() for c in row.split("|")[1:-1]]
        if len(cells) < 3 or set(cells[0]) <= set("-: "):
            continue
        status_match = re.fullmatch(r"`([a-z_]+)`", cells[0])
        assert status_match is not None, (
            f"§5's Transitions pin cannot read this Status cell: {cells[0]!r}")
        status = status_match.group(1)
        stated = frozenset(re.findall(r"`([a-z_]+)`", cells[2]))
        seen[status] = stated
        assert stated == _ALLOWED_TRANSITIONS[status], (
            f"§5's `{status}` row states transitions {sorted(stated)} but the guards "
            f"allow {sorted(_ALLOWED_TRANSITIONS[status])} (#11824). The table is "
            "canonical; either the row is stale or the guard moved without the doc")
    assert set(seen) == POINT_STATUS_VALUES, (
        f"this pin read the transitions of {sorted(seen)}, not the four model words "
        f"{sorted(POINT_STATUS_VALUES)} — a table row it cannot reach is unpinned")
    return seen


def test_ontology_discloses_the_legacy_words_it_no_longer_lists():
    """The three retired words stay NAMED in §5 as read-side legacy.

    Dropping them from the table without the note would be indistinguishable from
    "the engine never stored them" — and it does: `retract_point` still writes
    `retracted` (#11824 S2 migrates that verb), readers still fold `outdated`, and
    stored rows still hold `archived`. The note is what makes that residual
    DISCLOSED rather than discovered, so it is pinned here: a lane that tidies the
    three words out of §5 entirely fails this test instead of quietly deleting the
    record of a live divergence. NON-VACUOUS: the pre-v3.37 table satisfied a
    section-wide search for all three words, so the pin reads the note itself —
    ``test_ontology_legacy_pin_reads_the_note_not_the_section`` drives that shape
    and shows the scoped read refusing it.
    """
    # ⛔ SCOPED TO THE NOTE, not to §5 as a whole. The pre-v3.37 table listed all
    # three words as STATUS ROWS, so a section-wide `in section` was satisfied by
    # the very table this note exists to correct — the guard would have been green
    # on the buggy doc it was written for. The note is the blockquote paragraph
    # that follows the table; a word elsewhere in §5 is not a disclosure.
    note = _read_side_legacy_note()
    assert note, (
        "§5 carries no `read-side legacy` blockquote paragraph — the retired words "
        "must stay DISCLOSED in the note that says the model no longer produces "
        "them, not merely named somewhere in the section (#12855)")
    for word in ("retracted", "outdated", "archived"):
        assert f"`{word}`" in note, (
            f"§5's read-side legacy note no longer names {word!r} — the retired "
            "words are read-side legacy over stored rows and must stay disclosed "
            "(#12855); naming it elsewhere in §5 is not the note")
    # ⛔ The `assert "read-side legacy" in note` that stood here was DELETED, not
    # relocated (review P3). `_read_side_legacy_note` selects its paragraphs BY that
    # substring (`:322`), so a truthy `note` already implies it: no mutation of the
    # document makes this the assertion that reds — deleting the note reds at the
    # existence check above, never here. Relocating it to the raw section does not
    # buy a verdict either, since a non-empty `note` already implies the phrase is
    # in §5. The verdict is carried by the existence check above and the per-word
    # checks below, which DO vary on their own mutants.
    assert "`retract_point`" in note and "still writes `retracted`" in note, (
        "§5 no longer discloses that `retract_point` still writes `retracted` — that "
        "is the one place the code diverges from this model (#11824 S2)")


def test_ontology_legacy_pin_reads_the_note_not_the_section():
    """Non-vacuity for the scoping in ``test_ontology_discloses_the_legacy_words…``.

    §5 as it read BEFORE v3.37 satisfied a section-wide word check without carrying
    any disclosure at all: its six-row table named all three retired words in its
    Status/Transitions cells (measured against the real artifact — `origin/main`'s
    §5 — where the section-wide read is GREEN and this scoped read is RED). This
    pins that the scoped read refuses that shape, so the check above cannot drift
    back to a whole-section read and keep passing.

    ⛔ It also pins the paragraph BREAK, which a blank-line split gets wrong: §5's
    notes are one MD028 blockquote, so a blank-line split hands back the whole
    quote group — tombstone contract included — and a word added there would
    satisfy the disclosure assertions. The fixture carries the tombstone note with
    the disclosure paragraph deleted, which must read as NO note at all.
    """
    words = ("retracted", "outdated", "archived")
    pre_v337 = (
        "### Point Status Vocabulary (canonical, #432/#690)\n"
        "\n"
        "| Status | Kind | Transitions to | Notes |\n"
        "|--------|------|----------------|-------|\n"
        "| `draft` | initial | `live` | Inert for EP |\n"
        "| `live` | active | `retracted`, `outdated`, `archived` | Full EP |\n"
    )
    for word in words:
        assert f"`{word}`" in pre_v337, (
            "the fixture must reproduce the shape the whole-section read passed on")
    assert _read_side_legacy_note(pre_v337) == "", (
        "the legacy pin read the retired words out of the TABLE — that is the "
        "whole-section read this finding raised, and it proves nothing about the "
        "note")
    tombstone_only = (
        pre_v337
        + "\n"
        "> **Tombstone contract:** Retracted points stay in the graph "
        "(`get_point` returns them with `status='retracted'`).\n"
        ">\n"
        "> **Consequence:** a stored inverted window is reported, not repaired.\n"
    )
    assert _read_side_legacy_note(tombstone_only) == "", (
        "the note read escaped its paragraph — §5's tombstone contract is in the "
        "same blockquote, so a blank-line paragraph split would admit it as the "
        "disclosure")
    # The split is not cosmetic: with the note PRESENT and a retired word carried
    # only by the tombstone paragraph beside it, the scoped read must not see that
    # word — a blank-line split returns both paragraphs as ONE note and does.
    note_then_tombstone = (
        "### Point Status Vocabulary (canonical)\n"
        "\n"
        "> **`retracted` and `outdated` are read-side legacy**, not statuses.\n"
        ">\n"
        "> **Tombstone contract:** stored rows may still hold `archived`.\n"
    )
    scoped = _read_side_legacy_note(note_then_tombstone)
    assert "`retracted`" in scoped and "`outdated`" in scoped, scoped
    assert "`archived`" not in scoped, (
        "the scoped read picked up a word from the tombstone paragraph beside the "
        "note — the paragraph break is being read as a blank line, and §5's notes "
        "are one MD028 blockquote with bare `>` separators")
    # ⛔ AND A SECOND NOTE IS REFUSED, NOT JOINED. The blank-line split this pin
    # replaced asserted exactly ONE paragraph said 'read-side legacy' (measured on
    # this fixture: `§5 has 2 blockquote paragraph(s) naming 'read-side legacy'`).
    # Dropping that assert leaves a gutted disclosure able to borrow the retired
    # words from any other paragraph in §5 — the disclosure test passes while the
    # note discloses nothing. Driven here so the refusal cannot be deleted quietly.
    two_notes = (
        "### Point Status Vocabulary (canonical)\n"
        "\n"
        "> **`retracted` is read-side legacy** — and this paragraph names neither "
        "`outdated` nor `archived`.\n"
        "\n"
        "> **Unrelated note.** read-side legacy rows may still hold `outdated` and "
        "`archived` in storage.\n"
    )
    with pytest.raises(AssertionError, match="exactly one note"):
        _read_side_legacy_note(two_notes)


def test_every_status_in_values_has_transition_entry(sdk_factory, tmp_path):
    """Every status in POINT_STATUS_VALUES has an entry in _ALLOWED_TRANSITIONS."""
    from tortoise.sdk import POINT_STATUS_VALUES, _ALLOWED_TRANSITIONS  # noqa: I001

    for status in POINT_STATUS_VALUES:
        assert status in _ALLOWED_TRANSITIONS, \
            f"{status!r} in POINT_STATUS_VALUES but missing from _ALLOWED_TRANSITIONS"

    # ⚠️ The reverse direction is deliberately NOT an equality any more (#11824
    # S0): the table also carries the RETIRED spellings as terminal rows, because
    # a stored row may still hold one and a word with no row would DEFER (become
    # transitionable) instead of being terminal. So the pin is: every key is
    # either a writable word or a declared legacy word, and every legacy key is
    # terminal.
    legacy = {"retracted", "outdated", "archived"}
    for status in _ALLOWED_TRANSITIONS:
        assert status in POINT_STATUS_VALUES or status in legacy, \
            f"{status!r} in _ALLOWED_TRANSITIONS but is neither writable nor legacy"
        if status in legacy:
            assert _ALLOWED_TRANSITIONS[status] == frozenset(), \
                f"the legacy word {status!r} must have no outgoing transition"
