"""The document-source GOLD fixture — and the proof the `documents` cap is not vacuous.

Why this test exists
--------------------
Production holds **zero** `:Source` rows with `sourceKind='document'` (all 2,208
live rows are `agentSession`). Every candidate predicate therefore measures 0
there and passes **vacuously** — the gate cannot be shown to work against live
data, only against a constructed fixture.

This test does two jobs:

1. **Fixture integrity** — the gold file is well-formed and its per-case
   `predicate_matrix` is internally consistent.
2. **Non-vacuity, on a real graph** — the fixture sources are written to an
   embedded FalkorDB and the three candidate predicates are run as REAL Cypher.
   The test asserts the choice is *consequential*: the candidates must DISAGREE
   on at least two cases (if they agreed everywhere, the predicate choice would
   not matter and the "leak" finding would be unfounded).

The ruling under test (Q-S2 protocol, #5013): the `documents` cap predicate must
be **`sourceKind = 'document'`**. The form
`documentKind IS NOT NULL AND documentKind <> 'transcript'` is REJECTED because
it leaks a frontmatter-less document (NULL genre → unmetered → /v1/index/docs
free for it → #1726 reopens). The form
`COALESCE(documentKind,'') <> 'transcript'` is REJECTED because it meters every
session source (measured: 2,208).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "document_source_gold.jsonl"

# The three candidates. `CYPHER` maps each candidate's LABEL (the key used in
# the fixture's predicate_matrix) to the Cypher predicate over a node bound as
# `src`. These are the EXACT strings under decision — kept as a table so a
# reviewer can see the whole choice at once.
CANON = "sourceKind='document'"
REJECTED_LEAK = "documentKind IS NOT NULL AND documentKind <> 'transcript'"
REJECTED_OVERCOUNT = "COALESCE(documentKind,'') <> 'transcript'"
CYPHER = {
    CANON: "src.sourceKind = 'document'",
    REJECTED_LEAK: "src.documentKind IS NOT NULL AND src.documentKind <> 'transcript'",
    REJECTED_OVERCOUNT: "COALESCE(src.documentKind,'') <> 'transcript'",
}


def _load() -> tuple[dict, list[dict]]:
    lines = [ln for ln in FIXTURE.read_text().splitlines() if ln.strip()]
    head = json.loads(lines[0])
    rows = [json.loads(ln) for ln in lines[1:]]
    return head, rows


def _source_rows(rows: list[dict]) -> list[dict]:
    """The cases that describe a DISTINCT :Source node to be loaded and metered.

    Two exclusions, both deliberate:

    * the edge/round-trip case has no ``input.url`` — it is about resolution,
      not about a node; and
    * the two *reingest* cases are STATE TRANSITIONS of an already-present
      source (and deliberately reuse ``doc-with-genre``'s url). Loading them as
      separate nodes would create duplicate url keys and inflate every count.

    The behavioural cases (the two reingests, and the ``aboutDocument``
    round-trip) are NOT exercised by any test here — the behaviour belongs to
    epic #5088's implementation. What this file does assert about them is that
    their declared invariants are internally consistent and complete
    (``test_behavioural_cases_declare_their_invariants``), so each row is a
    usable gold target rather than prose.

    The discriminator is therefore ``metered_by_documents_cap`` being declared:
    a case either states a cap verdict or it is not a cap case.
    """
    return [
        r for r in rows
        if r.get("input", {}).get("url")
        and "metered_by_documents_cap" in r.get("expected", {})
    ]


# ── Job 1: fixture integrity ────────────────────────────────────────────────

def test_fixture_is_well_formed() -> None:
    head, rows = _load()
    assert head["__dataset__"] == "document-source-gold"
    assert head["__round__"] == 2
    assert "SYNTHETIC" in head["__provenance__"], (
        "the fixture must declare that it is constructed, not sampled — the "
        "whole reason it exists is that production has no instances"
    )
    assert rows, "no cases"
    for r in rows:
        for key in ("case", "band", "input", "expected", "predicate_matrix",
                    "why_this_case", "label", "reason", "source"):
            assert key in r, f"{r.get('case')}: missing {key}"
        assert r["why_this_case"].strip(), f"{r['case']}: empty rationale"
        # a row's matrix is EITHER the full candidate set OR exactly {"n/a": …}
        # — never a mix — AND the n/a form is reserved to ONE named row. Pinning
        # the row BY NAME is what removes the opt-out: without it, any row could
        # replace its three declared verdicts with `{"n/a": …}` and escape every
        # check (a review pass demonstrated exactly that, 10/10 green).
        m = r["predicate_matrix"]
        if r["case"] == NON_CAP_CASE:
            assert set(m) == {"n/a"}, (
                f"{r['case']} is the declared non-cap row: it must carry exactly 'n/a', "
                f"got {sorted(m)}"
            )
            assert isinstance(m["n/a"], str) and m["n/a"].strip(), (
                f"{r['case']}: the 'n/a' marker must carry a non-empty explanation"
            )
        else:
            assert set(m) == set(CYPHER), (
                f"{r['case']}: a cap row must declare all {len(CYPHER)} candidates, "
                f"got {sorted(m)} — 'n/a' is reserved to {NON_CAP_CASE!r}"
            )


def _py_predicates(inp: dict) -> dict:
    """The three candidates evaluated in pure Python, from a row's own `input`.

    Pure Python rather than Cypher on purpose: these rows are excluded by
    `_source_rows()`, and loading them as nodes would collide with
    `doc-with-genre`'s url.
    """
    sk, dk = inp.get("sourceKind"), inp.get("documentKind")
    return {
        CANON: sk == "document",
        REJECTED_LEAK: dk is not None and dk != "transcript",
        REJECTED_OVERCOUNT: (dk or "") != "transcript",
    }


#: The ONE row that is not a cap predicate, pinned BY NAME. Reserving `n/a` to
#: a named row is what stops any other row from opting out of its declared
#: verdicts by relabelling them `n/a`.
NON_CAP_CASE = "doc-aboutDocument-roundtrip"


#: The band each NON-cap case must sit in, pinned BY NAME. The cap rows are
#: mapped from their `sourceKind` instead; these three cannot be. A SWAP between
#: two non-cap rows leaves the band SET unchanged, so set-equality cannot see it.
NON_CAP_BAND = {
    "doc-reingest-unchanged": "idempotency",
    "doc-reingest-changed": "versioning",
    "doc-aboutDocument-roundtrip": "replay-identity",
}


def test_rows_outside_source_rows_are_still_pinned() -> None:
    """The rows `_source_rows()` EXCLUDES must still have their declared values pinned.

    `_source_rows()` requires `input.url` AND `metered_by_documents_cap`, so the
    two re-ingest rows and the round-trip row sit outside every graph check. A
    review pass demonstrated that flipping their `predicate_matrix` verdicts to
    wrong values left the suite fully green. Their verdicts are still DECLARED
    values, so they are checked here — in pure Python, from each row's own input.
    """
    _, rows = _load()
    checked = 0
    for r in rows:
        m = r["predicate_matrix"]
        if r["case"] == NON_CAP_CASE:
            # the n/a marker's PRESENCE is pinned here, so it cannot be swapped
            # for a set of three verdicts (which `_py_predicates` would derive
            # from the top-level input and wrongly accept)
            assert set(m) == {"n/a"}, (
                f"{NON_CAP_CASE} must carry exactly 'n/a', got {sorted(m)}"
            )
            assert isinstance(m["n/a"], str) and m["n/a"].strip()
            continue
        derived = _py_predicates(r["input"])
        assert m == derived, (
            f"{r['case']}: declared matrix disagrees with the matrix derivable "
            f"from its own input\n  declared: {m}\n  derived:  {derived}"
        )
        checked += 1
    assert checked > 0, "vacuous: no row had a real matrix to check"

    # the non-cap rows' bands are pinned BY NAME — a swap between two of them
    # leaves the band set unchanged and would otherwise survive
    seen = 0
    for r in rows:
        if r["case"] in NON_CAP_BAND:
            assert r["band"] == NON_CAP_BAND[r["case"]], (
                f"{r['case']}: band must be {NON_CAP_BAND[r['case']]!r}, got {r['band']!r}"
            )
            seen += 1
    assert seen == len(NON_CAP_BAND), (
        f"non-cap band pin incomplete: saw {seen} of {len(NON_CAP_BAND)}"
    )


def test_the_candidates_actually_disagree() -> None:
    """If the candidates agreed everywhere, the token choice would not matter.

    This is the assertion that makes the 'leak' and 'over-count' findings
    falsifiable: at least two cases must split the candidates.
    """
    _, rows = _load()
    splits = []
    for r in _source_rows(rows):
        m = r["predicate_matrix"]
        verdicts = {m[p] for p in CYPHER}
        if len(verdicts) > 1:
            splits.append(r["case"])
    assert len(splits) >= 2, (
        f"candidates disagree on only {splits} — expected >= 2 cases "
        f"(the frontmatter-less doc and the session source)"
    )


def test_canonical_predicate_matches_every_declared_verdict() -> None:
    _, rows = _load()
    srcs = _source_rows(rows)
    assert srcs, "vacuous guard: no source rows to check"
    for r in srcs:
        want = r["expected"]["metered_by_documents_cap"]
        got = r["predicate_matrix"][CANON]
        assert got is want, f"{r['case']}: canonical predicate says {got}, expected {want}"


# ── Job 2: non-vacuity on a REAL graph ──────────────────────────────────────

@pytest.fixture
def gold_graph(shared_embedded_db):
    """Write the fixture's :Source rows into an embedded FalkorDB."""
    from tortoise.sdk import TortoiseSDK

    sdk = TortoiseSDK(shared_embedded_db, namespace="gold")
    g = sdk._get_proj().g
    _, rows = _load()
    g.query("MATCH (n) DETACH DELETE n")
    for r in _source_rows(rows):
        inp = r["input"]
        g.query(
            "CREATE (s:Source {url: $url, sourceKind: $sk, documentKind: $dk, "
            "               contentHash: $h, version: $v, title: $t})",
            {"url": inp["url"], "sk": inp.get("sourceKind"),
             "dk": inp.get("documentKind"), "h": inp.get("contentHash"),
             "v": inp.get("version"), "t": inp.get("title")},
        )
    return sdk, g, rows


def _count(g, predicate: str) -> int:
    return int(g.query(f"MATCH (src:Source) WHERE {predicate} RETURN count(src)").result_set[0][0])


def test_predicates_on_a_real_graph_are_non_vacuous(gold_graph) -> None:
    """The proof this fixture exists for.

    On the REAL live graph every candidate returns 0 (no documents exist), so a
    green gate there proves nothing. Here the fixture guarantees a non-zero
    population, so the predicates are actually exercised.
    """
    _, g, rows = gold_graph
    srcs = _source_rows(rows)
    total = _count(g, "true")
    assert total == len(srcs) > 0, "fixture did not load"

    canon = _count(g, CYPHER[CANON])
    leak = _count(g, CYPHER[REJECTED_LEAK])
    over = _count(g, CYPHER[REJECTED_OVERCOUNT])

    # The canonical predicate must count EXACTLY the rows whose expected verdict
    # is True — no more (over-count) and no fewer (leak).
    expected_true = sum(1 for r in srcs if r["expected"]["metered_by_documents_cap"])
    assert canon == expected_true, (
        f"sourceKind='document' counted {canon}, expected {expected_true}"
    )

    # The two rejected forms must be WRONG, each in its own way, on this
    # population — otherwise the whole Q-S2 finding is unfounded.
    assert over > canon, (
        f"the COALESCE form counted {over} vs canonical {canon} — expected it to "
        f"over-count by metering session sources"
    )
    assert leak < canon, (
        f"the documentKind IS NOT NULL form counted {leak} vs canonical {canon} — "
        f"expected it to under-count by missing a frontmatter-less document"
    )


def test_the_leak_case_is_real(gold_graph) -> None:
    """The single most consequential row: a document with a NULL genre.

    The epic's proposed predicate drops it; the canonical one keeps it. If this
    ever stops being true the Q-S2 correction is no longer needed.
    """
    _, g, rows = gold_graph
    leak_case = next(r for r in _source_rows(rows) if r["case"] == "doc-without-genre")
    assert leak_case["input"]["documentKind"] is None
    url = leak_case["input"]["url"]
    assert _count(g, f"{CYPHER[CANON]} AND src.url = '{url}'") == 1, (
        "canonical predicate must meter the frontmatter-less document"
    )
    assert _count(g, f"{CYPHER[REJECTED_LEAK]} AND src.url = '{url}'") == 0, (
        "the rejected form must miss it — that is the leak"
    )


def test_every_declared_matrix_cell_matches_the_graph(gold_graph) -> None:
    """Verify EVERY declared cell, not just the canonical column.

    ``test_predicates_on_a_real_graph_are_non_vacuous`` proves the choice is
    consequential via aggregate inequalities (``leak < canon``, ``over >
    canon``). That would NOT catch a single wrong cell in a rejected column.
    This asserts each candidate's verdict per case against the real graph, so
    the fixture's whole matrix is measured rather than asserted.
    """
    _, g, rows = gold_graph
    checked = 0
    for r in _source_rows(rows):
        url = r["input"]["url"]
        for label, cypher in CYPHER.items():
            want = r["predicate_matrix"][label]
            got = _count(g, f"{cypher} AND src.url = '{url}'") == 1
            assert got is want, (
                f"{r['case']}: declared {label!r}={want}, but the graph says {got}"
            )
            checked += 1
    assert checked > 0, "vacuous: no cells were checked"


def test_behavioural_cases_declare_their_invariants() -> None:
    """The non-metering rows export the invariants epic #5088 must honour.

    Nothing here EXERCISES the behaviour — that is the epic's to implement.
    What this asserts is that every declaration the fixture makes is one it can
    be held to, and that the set of asserted keys EQUALS the set of declared
    keys. The completeness guard at the end is the load-bearing part: without
    it, a newly added `expected` key is silently unasserted and a mutated value
    survives green (which is exactly what a prior review pass caught here).
    """
    _, rows = _load()
    by_case = {r["case"]: r for r in rows}
    checked: set[tuple[str, str]] = set()

    def declared(case: str, key: str):
        """Read a declared value, recording that it was actually asserted."""
        exp = by_case[case]["expected"]
        assert key in exp, f"{case}: declared expected key {key!r} is missing"
        checked.add((case, key))
        return exp[key]

    # ── re-ingest unchanged: a no-op ──────────────────────────────────────
    unc_in = by_case["doc-reingest-unchanged"]["input"]
    assert declared("doc-reingest-unchanged", "reingest_result") == "unchanged"
    assert declared("doc-reingest-unchanged", "version_bumped") is False
    assert declared("doc-reingest-unchanged", "version_after") == unc_in["prior_version"]
    assert declared("doc-reingest-unchanged", "node_count_for_this_url") == 1
    assert declared("doc-reingest-unchanged", "new_derived_facts") == 0
    assert declared("doc-reingest-unchanged", "contentHash_after") == unc_in["contentHash"], (
        "an unchanged re-ingest must leave the SAME hash"
    )
    assert unc_in["contentHash"] == unc_in["reingested_hash"], (
        "the unchanged case must actually re-ingest the same hash"
    )

    # ── re-ingest changed: version +1, stale, additive supersession ───────
    chg_in = by_case["doc-reingest-changed"]["input"]
    assert declared("doc-reingest-changed", "version_bumped") is True
    assert declared("doc-reingest-changed", "version_after") == chg_in["prior_version"] + 1, (
        "version bumps by exactly 1"
    )
    assert declared("doc-reingest-changed", "node_count_for_this_url") == 1, (
        "a change replaces nothing — still one node keyed by url"
    )
    assert declared("doc-reingest-changed", "updatedAt_changed") is True
    assert declared("doc-reingest-changed", "old_version_derived_entities") == (
        "stale (DERIVED predicate, never a stored flag)"
    ), "exact equality: a substring test is invertible ('not stale…' contains 'stale')"
    assert declared("doc-reingest-changed", "supersession") == (
        "additive via CORRECTS when re-inference produces successors — never a delete"
    ), "exact equality: 'never CORRECTS — always DELETE' would pass a substring test"
    assert declared("doc-reingest-changed", "history_location") == (
        "the journal, not the graph"
    ), "exact equality: 'in the graph; the journal is never consulted' would pass a substring test"
    assert chg_in["prior_hash"] != chg_in["reingested_hash"], "the changed case must differ"

    # ── the aboutDocument round-trip: label AND key move together ─────────
    rt_in = by_case["doc-aboutDocument-roundtrip"]["input"]
    assert declared("doc-aboutDocument-roundtrip", "resolved_target_label") == "Source"
    assert declared("doc-aboutDocument-roundtrip", "resolved_by") == "url"
    assert declared("doc-aboutDocument-roundtrip", "resolved_key_value") == rt_in["target"]["url"], (
        "the resolved key must BE the source url — this is the failure the row warns about"
    )
    assert declared("doc-aboutDocument-roundtrip", "pass2b_rebuild_resolves_same_node") is True
    assert declared("doc-aboutDocument-roundtrip", "mis_pointed") is False
    assert rt_in["old_key"] == "coalesce(title, name)", (
        "pin the EXACT old key — `startswith('coalesce')` would accept a different one"
    )
    assert rt_in["new_key"] == declared("doc-aboutDocument-roundtrip", "resolved_by"), (
        "CROSS-FIELD: the key the row says it moves TO must be the key it says it "
        "resolves BY — otherwise the row's headline invariant is unpinned"
    )
    assert rt_in["target"]["url"] == declared("doc-aboutDocument-roundtrip", "resolved_key_value"), (
        "CROSS-FIELD: the resolved key value must BE the target's url"
    )
    assert rt_in["old_key"] != rt_in["new_key"], (
        "the label and the replay key must MOVE TOGETHER — that is the whole step"
    )
    assert rt_in["live_edge_count_in_production"] == 0, (
        "the round-trip exists BECAUSE production has no edges to test against"
    )

    # ── the SOURCE cases' declarations ────────────────────────────────────
    # These carry the D10 model itself (label, identity key, resolution). They
    # are asserted here for the same reason as the behavioural block: an
    # unasserted declaration is prose, and a prior pass found 11 of 14 mutations
    # here surviving green.
    for r in _source_rows(rows):
        case = r["case"]
        assert declared(case, "node_label") == "Source", "D10: a document is a :Source"
        assert declared(case, "identity_key") == "url", "D10: identity is the url"
        assert declared(case, "key_value") == r["input"]["url"], (
            "the resolved key must BE the source's url"
        )
        assert declared(case, "replay_key_resolves") is True
        # already cross-checked against the graph, restated here so the
        # completeness guard covers it
        assert declared(case, "metered_by_documents_cap") == r["predicate_matrix"][CANON]
        assert declared(case, "metered_by_sessions_cap") == (
            r["input"].get("sourceKind") == "agentSession"
        ), "the sessions meter owns exactly the agentSession rows"

    # ── the completeness guard — over every row's declared `expected` ─────
    # Scope, stated precisely: this compares (case_name, key) pairs drawn from
    # `expected` blocks. It cannot see a duplicate case name, an empty
    # `expected`, or any field OUTSIDE `expected` — those are pinned separately
    # by `test_row_identities_and_the_band_taxonomy_are_complete`.
    declared_keys = {(r["case"], k) for r in rows for k in r["expected"]}
    unasserted = declared_keys - checked
    assert not unasserted, (
        f"declared but never asserted: {sorted(unasserted)} — either assert the key "
        f"or delete it from the fixture; an unasserted declaration is prose"
    )


def test_row_identities_and_the_band_taxonomy_are_complete() -> None:
    """Guard the fixture's STRUCTURE, which the (case,key) completeness guard misses.

    The `declared()` guard compares `(case_name, key)` pairs, so it cannot see a
    duplicate case name, an empty `expected` block, or any field outside
    `expected`. A prior pass demonstrated all three surviving green. This pins
    them.
    """
    head, rows = _load()
    cases = [r["case"] for r in rows]

    # unique row identity — otherwise the (case,key) guard is ambiguous
    assert len(cases) == len(set(cases)), f"duplicate case names: {sorted(cases)}"

    # and the case INVENTORY is pinned exactly. Uniqueness catches a duplicate
    # but NOT a deletion — and a deleted row takes its assertions with it,
    # leaving the suite green over a smaller fixture. A review pass demonstrated
    # both `transcript-source` and `doc-with-genre` deleted with 9/9 green.
    assert set(cases) == {
        "doc-with-genre", "doc-without-genre", "session-source", "transcript-source",
        "doc-reingest-unchanged", "doc-reingest-changed", "doc-aboutDocument-roundtrip",
    }, f"case inventory changed: {sorted(set(cases))}"

    # every row declares SOMETHING to be held to
    for r in rows:
        assert r["expected"], f"{r['case']}: empty expected block is unassertable"

    # the band taxonomy is closed, and every band is populated
    bands = {r["band"] for r in rows}
    known = {"document-must-count", "session-must-not-count", "idempotency",
             "versioning", "replay-identity"}
    assert bands <= known, f"unknown band(s): {sorted(bands - known)}"
    assert bands == known, f"band(s) with no case: {sorted(known - bands)}"

    # a row in a CAP band must declare a cap verdict — this is what stops a
    # source row from silently opting out of the metering assertions
    for r in rows:
        if r["band"] in ("document-must-count", "session-must-not-count"):
            assert "metered_by_documents_cap" in r["expected"], (
                f"{r['case']}: a cap-band row must declare metered_by_documents_cap"
            )

    # and the band must MATCH the row's sourceKind — otherwise the band set can
    # still be "complete" while a row is mislabelled (a document filed under
    # session-must-not-count). A set-equality check cannot see that.
    expected_band = {"document": "document-must-count",
                     "agentSession": "session-must-not-count"}
    for r in _source_rows(rows):
        sk = r["input"]["sourceKind"]
        if sk in expected_band:
            assert r["band"] == expected_band[sk], (
                f"{r['case']}: sourceKind={sk!r} must sit in band "
                f"{expected_band[sk]!r}, not {r['band']!r}"
            )

    # the header schema literal is pinned, and the declared vocabulary actually
    # covers the genre values the rows use
    assert head["__schema__"] == (
        "case/band/input/expected/predicate_matrix/why_this_case/label/reason/source"
    )
    used = {r["input"].get("documentKind") for r in rows} - {None}
    declared_kinds = set(head["__vocabulary_grounds__"]["documentKind"])
    assert used <= declared_kinds, (
        f"rows use documentKind(s) absent from the declared vocabulary: {sorted(used - declared_kinds)}"
    )


def test_session_sources_are_not_double_metered(gold_graph) -> None:
    """Sessions already have their own meter; the docs cap must not count them."""
    _, g, rows = gold_graph
    session_rows = [r for r in _source_rows(rows) if r["expected"]["metered_by_sessions_cap"]]
    assert session_rows, "vacuous guard: no row declares metered_by_sessions_cap=true"
    for r in session_rows:
        url = r["input"]["url"]
        assert _count(g, f"{CYPHER[CANON]} AND src.url = '{url}'") == 0, (
            f"{r['case']} must NOT be metered by the documents cap"
        )
