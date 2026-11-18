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


def test_ontology_discloses_the_legacy_words_it_no_longer_lists():
    """The three retired words stay NAMED in §5 as read-side legacy.

    Dropping them from the table without the note would be indistinguishable from
    "the engine never stored them" — and it does: `retract_point` still writes
    `retracted` (#11824 S2 migrates that verb), readers still fold `outdated`, and
    stored rows still hold `archived`. The note is what makes that residual
    DISCLOSED rather than discovered, so it is pinned here: a lane that tidies the
    three words out of §5 entirely fails this test instead of quietly deleting the
    record of a live divergence.
    """
    section = _ontology_status_section()
    for word in ("retracted", "outdated", "archived"):
        assert f"`{word}`" in section, (
            f"§5 no longer names {word!r} anywhere — the retired words are read-side "
            "legacy over stored rows and must stay disclosed (#12855)")
    assert "read-side legacy" in section, (
        "§5's note no longer calls the retired words read-side legacy — without it "
        "the table reads as 'the code never produced these'")
    assert "`retract_point`" in section and "still writes `retracted`" in section, (
        "§5 no longer discloses that `retract_point` still writes `retracted` — that "
        "is the one place the code diverges from this model (#11824 S2)")


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
