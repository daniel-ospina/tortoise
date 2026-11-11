"""`#10988` — A4's CI CONTRACT TWIN: per-layer label coverage, `NULL` kept as a reported share.

**Test tier: `CI`** (offline, deterministic, sub-second — every arm below drives a pure function or
a fake projection; no graph, no model, no network). A4's own measurement is **Tier E** (a read-only
census over the fleet graph, `04-plan.md` §2 W2c), and this file is the contract half the epic
requires beside it: *"a `Tier E` case without its CI contract twin is not done"* (`#11454`).

WHY THIS FILE EXISTS, in the row's own words. A4 was **re-authored** at the epic's decompose gate
(2026-11-05, `04-plan.md` §4.1 / `AM7`): the criterion is *"things and events carry the labels the
ontology declares for them — reported **per layer**"*, on the bar **appears + labelled**, with the
percentage **reported, not chased**. It is NOT the retired *"typed-claim target"*: the CLAIM layer is
deliberately uniform (`statement`, `ONTOLOGY.md` §5 option B), so counting labels there could not
fail. The owner's 2026-11-09 ruling (home: `#4432` comment `6751932659`, relayed onto `#10988`)
cleared the D3 fork the old row was blocked on, so nothing here waits on it.

Five properties, each naming the mutation that makes it FAIL:

1. **`NULL` is a THIRD reported share, never folded into a declared kind** — a blank `pointKind` and
   a Cypher `null` are the ABSENT label, and `unlabelled = unlabelled_null + unlabelled_undeclared`
   EXACTLY, so no node can fall out of the denominator. (E2E-8's CI counterpart: *"the `NULL`
   denominator kept as a reported share"*.)
2. **the three layers are reported SEPARATELY** — a blended percentage is what lets a failing
   thing/event layer hide inside a green claim layer (`04-plan.md` §2 W2c lists it as a failure mode).
3. **each layer is judged against ITS OWN declared vocabulary** — the vocabularies overlap
   (`plan`/`goal`/`strategy`/`target` are point AND object kinds, `ONTOLOGY.md` §5 #7813;
   `meeting` is an Event kind the flat registry also accepts on the point bucket), so crediting a
   thing against the point set would report the wrong layer as labelled.
4. **`0 of 0` is not coverage** — A4's registered floor is non-vacuity (`a4_labelled_nodes: 1`, plan
   §4.1 / `AM7`), so an empty layer is RED rather than a vacuous 100 %/absent rate.
5. **the diagnostic survives as a diagnostic** — the typed/`statement`/`NULL` shares and the
   per-writer split are reported BESIDE the criterion (plan §3.1 A4). ⛔ What must not be built is
   the retired FRAMING (a typed-share bar); deleting the diagnostic instead is the opposite error,
   and would quote a share without the denominator the epic says it will not accept.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

PROBE = REPO_ROOT / "docs" / "evidence" / "7949-capstone" / "probes" / "capstone_measure.py"


def _probe():
    """The census probe, loaded by path (it is evidence, not an importable package)."""
    spec = importlib.util.spec_from_file_location("capstone_measure_10988", PROBE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ── fake graph, in the shape the probe actually issues ────────────────────
#
# ⛔ Matched on a substring of the CYPHER, so a query that loses its exclusion predicate still
# answers — which is why the arms that care about the predicate assert on the QUERY TEXT too (the
# `#10982` round-4 shape: "the guard exists, the mutant that reverts the fix does not reach it").

EMPTY_ROWS: dict = {
    "RETURN coalesce(n.pointKind, 'NULL') AS k": [],
    "RETURN coalesce(n.objectKind, 'NULL') AS k": [],
    "RETURN coalesce(n.eventKind, 'NULL') AS k": [],
    "coalesce(n.is_episodic, false) OR coalesce(n.is_operator, false)": [[0]],
    "RETURN n.content, n.pointKind": [],
    "MATCH (n:Subject) RETURN count(n)": [[0]],
    "MATCH (n:Point) RETURN count(n)": [[0]],
    "RETURN type(r) AS t, count(DISTINCT r) AS n": [],
    "WHERE n.pointKind = 'statement'": [[0]],
    "RETURN type(r) AS t, a.objectKind AS ak": [],
    "RETURN type(r) AS t, count(*) AS n": [],
}


class _FakeGraph:
    def __init__(self, rows: dict):
        self._rows = rows
        self.queries: list[str] = []

    def query(self, cypher: str):
        self.queries.append(cypher)
        for needle, payload in self._rows.items():
            if needle in cypher:
                return type("R", (), {"result_set": payload})()
        raise AssertionError(f"unexpected query: {cypher}")


class _FakeProjection:
    def __init__(self, rows: dict | None = None):
        self.g = _FakeGraph(dict(EMPTY_ROWS if rows is None else rows))


#: The `#10982` fixture, re-used because it is the population A4's diagnostic was reviewed against:
#: 193 capture rows excluded, `statement` 150, `NULL` 10 + blank 3 (ONE absent state), and 109 typed
#: claims (`constraint` 88 + `preference` 21) — "the 109" the issue's Scope stage names. The other
#: three queries are answered EMPTY so an arm exercising the claims layer still runs the whole row.
_CLAIM_ROWS = {
    **EMPTY_ROWS,
    "RETURN coalesce(n.pointKind, 'NULL') AS k": [
        ["statement", 150], ["NULL", 10], ["", 3], ["constraint", 88], ["preference", 21]],
    "coalesce(n.is_episodic, false) OR coalesce(n.is_operator, false)": [[193]],
}


# ── 1. `NULL` is its own reported share, and nothing escapes the denominator ──


def test_null_and_blank_are_ONE_absent_state_reported_as_its_own_share():
    """FAILS IF a blank kind is folded into a declared kind, or `NULL` is folded into `statement`.

    Both folds manufacture a typing/coverage win nobody made: `''` is what the `#548` rebuild
    SNAPSHOT synthesis writes for a kindless operator (`projection/__init__.py:13738-13740`), and
    `NULL` is the absent property. They are ONE state (absent) and neither is a label.
    """
    probe = _probe()
    cov = probe.label_coverage(
        {"statement": 10, "NULL": 4, "": 2}, frozenset({"statement"}), layer="claims")
    assert cov["population"] == 16, "the denominator is every node, absences included"
    assert cov["labelled"] == 10
    assert cov["unlabelled"] == 6, "a blank kind is ABSENT, not a declared label"
    assert cov["unlabelled_null"] == 6
    assert cov["unlabelled_undeclared"] == 0
    assert cov["coverage"] == 10 / 16
    assert "NULL" not in cov["by_kind"] and "" not in cov["by_kind"]
    assert cov["declared_present"] == ["statement"]


def test_unlabelled_is_EXACTLY_the_null_plus_undeclared_share_and_nothing_escapes():
    """FAILS IF a kind present on the graph is dropped out of the denominator.

    `unlabelled = unlabelled_null + unlabelled_undeclared`, and `population = labelled +
    unlabelled`: an undeclared kind is CARRIED, never silently excluded — the arithmetic is the
    falsifier, so a future fold that drops a bucket reds here rather than in a reader's head.
    """
    probe = _probe()
    cov = probe.label_coverage(
        {"statement": 7, "plan": 3, "constraint": 2, "NULL": 1},
        frozenset({"statement", "plan"}), layer="claims")
    assert cov["labelled"] == 10
    assert cov["undeclared"] == {"constraint": 2}
    assert cov["unlabelled_undeclared"] == 2
    assert cov["unlabelled_null"] == 1
    assert cov["unlabelled"] == 3
    assert cov["labelled"] + cov["unlabelled"] == cov["population"] == 13


# ── 2. per layer, never blended ──────────────────────────────────────────


#: The five point kinds `tortoise.sdk` registers through `domain_loader.register_kind` at IMPORT time
#: (`sdk.py:217-227`). No `packs/*/manifest.yaml` declares them and none is in
#: `CANONICAL_POINT_KINDS`, so the SDK import is the ONLY path that makes them declared.
_SDK_REGISTERED_KINDS = ("diary", "checkpoint-item", "option", "evidence", "criterion")


def test_the_declared_vocabulary_includes_the_kinds_the_SDK_itself_registers():
    """FAILS IF `_a4_declared_kinds` imports only `domain_loader` and misses the SDK's own kinds.

    ⛔ MEASURED ON THE LIVE GRAPH 2026-11-11, which is why this arm exists: with only
    `domain_loader` imported, `evidence` (121 nodes) and `option` (2) read UNDECLARED — FOUR of
    the fleet's six declared typed kinds, since `tortoise.sdk` registers them through
    `register_kind` at import time. That is a FALSE failing run, and a refusal that names a false
    reason is the one failure mode a refusal must not have (`tools/ax_write_eval.py` records the
    same defect as `#9695` review P1).

    ⛔ IN ITS OWN INTERPRETER, and that is not ceremony: `tests/conftest.py:594` imports
    `tortoise.sdk` at module level, so by the time ANY test body runs in this process the SDK's
    `register_kind` calls have already happened and the vocabulary is complete. Deleting the
    in-function `import tortoise.sdk` — the exact mutation this arm names — therefore left an
    IN-PROCESS assertion green: a pin that cannot see its own mutant is a false PASS (#11837 review
    round 1, P1). The child reads the product's bare vocabulary FIRST (no SDK import) and only then
    calls the probe, so both halves are measured in one run with no shared state.
    """
    import subprocess

    code = (
        "import sys, importlib.util\n"
        "from tortoise.domain_loader import known_kinds\n"
        f"want = set({_SDK_REGISTERED_KINDS!r})\n"
        "bare = sorted(want & set(known_kinds('pointKind')))\n"
        "spec = importlib.util.spec_from_file_location('cm_10988', sys.argv[1])\n"
        "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
        "declared = sorted(want & set(m._a4_declared_kinds('pointKind')))\n"
        # the OTHER direction: the probe must not ADD anything the product does not declare
        "extra = sorted(set(m._a4_declared_kinds('pointKind')) - set(known_kinds('pointKind')))\n"
        "print(bare)\n"
        "print(declared)\n"
        "print(extra)\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code, str(PROBE)],
        cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=300)
    assert out.returncode == 0, (out.returncode, out.stdout[-800:], out.stderr[-1500:])
    lines = out.stdout.strip().splitlines()
    assert len(lines) == 3, out.stdout
    # noqa: S307 — three sorted str lists this test's own child printed
    bare, declared, extra = eval(lines[0]), eval(lines[1]), eval(lines[2])
    assert bare == [], (
        f"the product's BARE vocabulary already declares {bare} without the SDK import, so this arm "
        f"would be vacuous — find the real registration path before trusting the assertion below")
    assert declared == sorted(_SDK_REGISTERED_KINDS), (
        f"`_a4_declared_kinds` must re-supply the SDK-registered kinds (got {declared}) — without "
        f"its in-function `import tortoise.sdk` a standalone census reports the fleet's own declared "
        f"typed kinds as inventions")
    # ⛔ THE OTHER DIRECTION, which a round-2 rewrite of this arm lost (#11837 review round 3):
    # `_a4_declared_kinds` must return the product's vocabulary and NOTHING MORE. Without this, a
    # `| {"finding", "totallyMadeUpKind"}` on the return passes every other case in this file.
    assert extra == [], (
        f"`_a4_declared_kinds` declares kinds the product does not: {extra} — a census may not "
        f"widen the vocabulary it measures against, or an invention reads as coverage")


def test_the_three_layers_are_reported_SEPARATELY_and_never_blended():
    """FAILS IF `_a4_label_coverage` collapses things/events/claims into one percentage.

    A single blended rate is the failure mode `04-plan.md` §2 W2c lists first: a failing thing or
    event layer would hide inside a green claim layer. The three populations below are deliberately
    DIFFERENT sizes, so any union changes at least one of the three `coverage` values.
    """
    probe = _probe()
    projection = _FakeProjection({
        **_CLAIM_ROWS,
        "RETURN coalesce(n.objectKind, 'NULL') AS k": [["Project", 4], ["NULL", 1]],
        "RETURN coalesce(n.eventKind, 'NULL') AS k": [["meeting", 2], ["turn", 6]],
    })
    a4 = probe._a4_label_coverage(projection)
    for layer in ("claims", "things", "events"):
        assert layer in a4, f"the {layer} layer is not reported separately"
        assert a4[layer]["layer"] == layer
    assert a4["claims"]["population"] == 150 + 3 + 10 + 88 + 21
    assert a4["things"]["population"] == 5
    assert a4["events"]["population"] == 8
    # Each layer's own coverage differs, which is the whole point of not averaging them.
    assert a4["things"]["coverage"] == 4 / 5
    assert a4["events"]["coverage"] == 8 / 8
    # ⛔ `constraint`/`preference` are NOT in the product's declared point vocabulary at this
    # head (they resolve through no loaded pack), so the claim layer's coverage is the `statement`
    # share ALONE — the census must not credit a kind the product does not declare.
    assert a4["claims"]["coverage"] == 150 / 272
    assert a4["claims"]["undeclared"] == {"constraint": 88, "preference": 21}


def test_the_claim_layer_keeps_its_EXCLUSION_predicate_so_capture_rows_are_not_claims():
    """FAILS IF the per-layer rewrite drops the `is_episodic`/`is_operator` filter.

    Turn rows are written `pointKind='event'` (`sdk.py:2528` `_TURN_WRITE_CYPHER`) and operator
    Points are operators, not claims. On the committed corpus 159 of 354 Points are turn rows, so
    counting them read ~45 % "typed" against the owner's 0.6 % reading (`#10982` review, finding 4).
    ⛔ Asserted on the QUERY TEXT: the fake answers the same rows with the clause deleted, so this
    is the only arm that can see the mutation.
    """
    probe = _probe()
    projection = _FakeProjection(_CLAIM_ROWS)
    probe._a4_layer_counts(projection, "Point", "pointKind")
    claims_query = [q for q in projection.g.queries if "MATCH (n:Point)" in q]
    assert len(claims_query) == 1
    assert "NOT coalesce(n.is_episodic, false)" in claims_query[0]
    assert "NOT coalesce(n.is_operator, false)" in claims_query[0]
    # …and the THING/EVENT layers carry no such filter: an `:Object` is a thing by label, and a
    # filter inherited from the claim layer would silently measure a subpopulation instead.
    things = _FakeProjection()
    probe._a4_layer_counts(things, "Object", "objectKind")
    assert "is_episodic" not in things.g.queries[0]


# ── 3. each layer judged against its OWN declared vocabulary ─────────────


def test_a_layer_is_judged_against_its_OWN_vocabulary_not_a_sibling_layer_s():
    """FAILS IF the declared set is a single vocabulary shared by all three layers.

    The sets overlap by owner ruling (`#7813`), which is exactly why a shared set is tempting and
    wrong: `statement` is a POINT kind and never an object label, and `meeting` is an EVENT kind the
    legacy flat registry ALSO accepts on the point bucket. A census crediting `statement` on an
    `:Object` would report a mislabelled thing as coverage — the failure A4 exists to catch.
    """
    probe = _probe()
    things = probe.label_coverage(
        {"statement": 3, "project": 2}, frozenset({"project"}), layer="things")
    assert things["labelled"] == 2, "a point kind on a thing is NOT coverage"
    assert things["undeclared"] == {"statement": 3}
    events = probe.label_coverage(
        {"meeting": 5, "statement": 1}, frozenset({"meeting"}), layer="events")
    assert events["labelled"] == 5 and events["undeclared"] == {"statement": 1}


def test_object_kinds_are_compared_CASE_INSENSITIVELY_because_the_ontology_mixes_case():
    """FAILS IF `Project`-vs-`project` is reported as an unlabelled thing.

    §5's object vocabulary deliberately mixes cases (`Project`, `WorkItem`, `Problem`, `tag`) and
    the write path stores what the caller passed, so an exact compare manufactures a FALSE failing
    run — the one direction a refusal must not fail in. Same rule as
    `tools/ax_write_eval.py::canonical_object_kinds`.
    """
    probe = _probe()
    cov = probe.label_coverage({"Project": 2}, frozenset({"project"}), layer="things")
    assert cov["labelled"] == 2 and cov["undeclared"] == {}
    # …and the POINT/EVENT layers stay exact: the write validator is exact for those buckets.
    exact = probe.label_coverage({"Statement": 1}, frozenset({"statement"}), layer="claims")
    assert exact["undeclared"] == {"Statement": 1}


def test_the_declared_vocabulary_is_IMPORTED_from_the_product_never_re_spelled():
    """FAILS IF a lane hardcodes a kind list instead of asking the product's own vocabulary.

    `domain_loader.known_kinds(bucket)` is the predicate the hosted write validator runs
    (`hosted_api.py::valid_kind`), so it is the product's answer; a second list here would drift in
    one direction (blessing undeclared kinds) or the other (calling a pack's declared kind an
    invention).
    """
    probe = _probe()
    points = probe._a4_declared_kinds("pointKind")
    objects = probe._a4_declared_kinds("objectKind")
    events = probe._a4_declared_kinds("eventKind")
    assert "statement" in points, "the claim layer's canonical kind is the core one"
    assert "statement" not in objects, "`statement` is never a declared OBJECT label"
    assert "meeting" in events, "`meeting` is a core EVENT kind (ONTOLOGY §5)"
    assert any(k.casefold() == "project" for k in objects), "`Project` is a core OBJECT kind"
    assert points != objects and objects != events, "the three layers are three vocabularies"


# ── 4. `0 of 0` is not coverage (the non-vacuity floor) ──────────────────


def test_an_EMPTY_layer_is_not_coverage_and_a_single_labelled_node_is():
    """FAILS IF an empty layer reports a rate, or a labelled node fails A4's floor.

    A4's registered floor is `a4_labelled_nodes: 1` (plan §4.1 / `AM7`): at least ONE thing or event
    carrying a declared label, because *"100 % of 0 is not attribution"*. The rate is `None` at a
    zero population rather than `0.0`/`1.0` — a number there is a measurement of nothing.
    """
    probe = _probe()
    empty = probe.label_coverage({}, frozenset({"project"}), layer="things")
    assert empty["population"] == 0
    assert empty["coverage"] is None, "a rate over an empty population is not a measurement"
    assert empty["non_vacuity"] is False, "0 of 0 labelled is not coverage"
    # A populated layer where NOTHING carries a declared label is the concrete failing run.
    all_absent = probe.label_coverage({"NULL": 4}, frozenset({"project"}), layer="things")
    assert all_absent["population"] == 4 and all_absent["labelled"] == 0
    assert all_absent["non_vacuity"] is False
    # …and one declared label clears the floor, which is what the floor says and nothing more.
    one = probe.label_coverage({"project": 1, "NULL": 4}, frozenset({"project"}), layer="things")
    assert one["non_vacuity"] is True and one["coverage"] == 1 / 5


def test_a_declared_kind_absent_from_the_graph_is_LISTED_and_the_verdict_is_not_invented():
    """FAILS IF the census decides WHICH declared kinds should have appeared.

    *"A declared kind absent"* is A4's failing leg, but which declared kinds a graph SHOULD produce
    is a corpus expectation, not a graph fact — a census that assumed it would red every graph for
    the ~30 kinds it never writes. So the list is reported in full and the row decides nothing from
    it on its own.
    """
    probe = _probe()
    declared = frozenset({"project", "workitem", "problem"})
    cov = probe.label_coverage({"project": 2}, declared, layer="things")
    assert cov["declared_present"] == ["project"]
    assert cov["declared_absent"] == ["problem", "workitem"], "the whole absent set is reported"
    # An UNDECLARED kind present is not a member of either list — it is its own bucket.
    other = probe.label_coverage({"project": 2, "gadget": 1}, declared, layer="things")
    assert "gadget" not in other["declared_present"]
    assert "gadget" not in other["declared_absent"]
    assert other["undeclared"] == {"gadget": 1}


def test_an_EXPECTED_set_is_the_corpus_fact_the_census_may_not_infer():
    """FAILS IF the "a declared kind absent" leg is either invented or unproducible.

    A4's second failing leg is *"a declared kind the graph should produce is absent"* — and WHICH
    kinds a layer should produce is a CORPUS fact, not a graph fact. So it arrives as an INPUT
    (`expected`) and the census never derives it: without one, `expected_absent` is `UNMEASURED` (so
    "nothing was expected of this layer" is distinguishable from "everything expected of it was
    present"), and with one it names exactly the expected kinds the graph does not show.
    """
    probe = _probe()
    no_expectation = probe.label_coverage(
        {"project": 2}, frozenset({"project", "problem"}), layer="things")
    assert str(no_expectation["expected_absent"]).startswith("UNMEASURED("), (
        "with no corpus expectation the absence list is reported and this leg decides nothing")
    expected = probe.label_coverage(
        {"project": 2}, frozenset({"project", "problem"}),
        layer="things", expected=frozenset({"project", "problem"}))
    assert expected["expected_absent"] == ["problem"], (
        "an expected kind with zero population IS the failing leg")
    satisfied = probe.label_coverage(
        {"project": 2}, frozenset({"project", "problem"}),
        layer="things", expected=frozenset({"project"}))
    assert satisfied["expected_absent"] == [], (
        "a kind nobody expected is not a finding — `declared_absent` still lists it, `expected_absent` "
        "does not")


# ── 5. the diagnostics survive, as diagnostics ───────────────────────────


def test_the_typed_statement_NULL_diagnostics_are_reported_beside_the_criterion():
    """FAILS IF the re-authoring DELETES the diagnostic (the opposite error to keeping the old bar).

    Plan §3.1 A4: the per-writer split and the `typed`/`statement`/`NULL` shares are reported BESIDE
    the criterion, because *"a share quoted without its `NULL` denominator is not a measurement this
    epic accepts"*. The retired thing is the FRAMING (a typed-share target), not the number.
    """
    probe = _probe()
    a4 = probe._a4_label_coverage(_FakeProjection(_CLAIM_ROWS))
    assert a4["excluded_capture_rows"] == 193
    assert a4["statement"] == 150
    assert a4["null"] == 13, "a blank kind is ABSENT, not typed"
    assert a4["typed"] == 109 and a4["typed_by_kind"] == {"constraint": 88, "preference": 21}
    assert a4["claim_points"] == 150 + 13 + 109
    assert a4["typed_share"] == 109 / 272


def test_the_per_writer_split_attributes_by_REPRODUCTION_and_says_it_is_not_a_stamp():
    """FAILS IF attribution is presented as a stored fact, or as "some other lane".

    No Point property records its producer (`projection/entities.py`'s `_POINT_HANDLED` names every
    persisted property and none is a writer stamp), so the split is a REPRODUCTION test: the rule
    classifier (`extractor.py::_classify_point_kind`) re-derived over each node's content. And
    `extractor_v2` cannot be the source of a typed claim at all — its write gate repairs every
    non-master point kind to `statement` (`extractor_v2.py:10413-10420`, `:11044`).
    """
    probe = _probe()
    projection = _FakeProjection({
        **_CLAIM_ROWS,
        "RETURN n.content, n.pointKind": [
            # the rule classifier derives `decision` from "we decided …" -> reproduces
            ["We decided to adopt option A.", "decision"],
            # stored `plan`, but the classifier derives `statement` from this content -> supplied
            ["The cat sat on the mat.", "plan"],
        ],
    })
    split = probe._a4_writer_attribution(projection)
    assert split["typed_claims"] == 2
    assert split["by_writer"]["classifier-reproduces"]["total"] == 1
    assert split["by_writer"]["caller-supplied"]["total"] == 1
    assert split["by_writer"]["classifier-reproduces"]["by_kind"] == {"decision": 1}
    assert split["by_writer"]["caller-supplied"]["by_kind"] == {"plan": 1}
    assert split["attributed"] == 2
    assert str(split["provenance"]).startswith("UNMEASURED("), (
        "attribution must not be presented as a stored fact — no writer stamp exists")
    assert "extractor_v2" in split["v2_lane_excluded_by_construction"]


# ── 6. the rendered artefact a scorer reads ─────────────────────────────


def _full_census(probe) -> dict:
    """A complete census block, as `a_row_census` would return it."""
    layer = lambda name: probe.label_coverage(  # noqa: E731
        {"statement": 3, "NULL": 1} if name == "claims" else {"NULL": 2},
        frozenset({"statement"}) if name == "claims" else frozenset({"project"}),
        layer=name)
    return {
        "A1": {"objects": 2, "edges_total": 0, "by_type": [], "by_endpoint_kind": [],
               "by_pinned_family": {}, "pinned_family_declared_spellings": {},
               "rollup_queries": probe.unmeasured("a rollup query")},
        "A2": {key: probe.unmeasured("a counter") for key in
               ("attach", "mint", "held", "candidates", "decided_candidates", "attach_share")},
        "A3": {"statements": 0, "statements_with_an_event_root": 0,
               "event_root_coverage": probe.unmeasured("a statement population"),
               "cheap_joined": 0, "cheap_rate": probe.unmeasured("a statement population"),
               "cross_batch_joined": probe.unmeasured("an eventId stamp"),
               "cross_batch_rate": probe.unmeasured("an eventId stamp"),
               "relations": list(probe.CHAIN_RELATIONS)},
        "A4": {**{name: layer(name) for name in ("claims", "things", "events")},
               "claim_points": 4, "excluded_capture_rows": 0, "statement": 3, "null": 1,
               "typed": 0, "typed_by_kind": {}, "typed_share": 0.0,
               "per_writer": {"typed_claims": 0, "attributed": 0, "by_writer": {},
                              "v2_lane_excluded_by_construction": "extractor_v2 is excluded",
                              "provenance": probe.unmeasured("a writer stamp")}},
        "A5": {"subjects": 0, "named_actors": probe.unmeasured("the corpus's actor list"),
               "point_population": 4, "families": {"aboutSubject": 0, "memberOf": 0,
                                                   "ownedBy": 0, "managedBy": 0},
               "statements_with_a_subject_edge": 0},
        "TAGGED": {"edges": 0, "note": probe.TAGGED_NOTE},
    }


def test_the_rendered_block_emits_EVERY_layer_with_its_own_denominator():
    """FAILS IF a layer is missing from the log, or the diagnostic loses its NULL share.

    The renderer IS the artifact a scoring child reads; a field in the dict the log never prints is
    a measurement nobody sees (the `#11331` round-8 shape).
    """
    probe = _probe()
    rendered = "\n".join(probe.a_row_census_lines(_full_census(probe)))
    for layer in ("claims", "things", "events"):
        line = [ln for ln in rendered.splitlines() if ln.startswith(f"a4 {layer} ")]
        assert line, f"the {layer} layer is not rendered:\n{rendered}"
        for part in ("population", "labelled", "unlabelled", "coverage", "non_vacuity",
                     "declared_present", "declared_absent", "undeclared"):
            assert f"{part}=" in line[0], (layer, part, line[0])
    diagnostics = [ln for ln in rendered.splitlines() if ln.startswith("a4 diagnostics")]
    assert diagnostics, rendered
    for part in ("claim_points", "statement", "null", "typed_share"):
        assert f"{part}=" in diagnostics[0], (part, diagnostics[0])
    assert "a4 per_writer" in rendered
    assert "REPORTED not chased" in rendered, (
        "the rendered row must state that the percentage is REPORTED and carries no bar")


def test_a_broken_layer_query_renders_a_NAMED_GAP_and_never_a_zero():
    """FAILS IF an infrastructure failure renders `population=0` — a MEASURED ABSENCE.

    A numeric zero under a key a real measurement occupies reads as "the graph has none of these",
    which scores the row RED on a broken query. `row()` replaces the skeleton with its
    `UNMEASURED(...)` reason instead.
    """

    class _Breaks(_FakeGraph):
        def query(self, cypher):
            if "RETURN coalesce(n.objectKind, 'NULL') AS k" in cypher:
                raise RuntimeError("boom")
            return super().query(cypher)

    class _Projection:
        def __init__(self, rows):
            self.g = _Breaks(rows)

    probe = _probe()
    from tortoise import projection as projection_module

    original = projection_module.FalkorProjection.from_uri
    projection_module.FalkorProjection.from_uri = classmethod(lambda cls, uri: _Projection(dict(_CLAIM_ROWS)))
    try:
        census = probe.a_row_census("docker://h:1/g")
    finally:
        projection_module.FalkorProjection.from_uri = original
    a4 = census["A4"]
    assert str(a4["things"]["population"]).startswith("UNMEASURED("), a4["things"]
    assert a4["things"]["population"] != 0
    assert str(a4["claims"]["population"]).startswith("UNMEASURED("), (
        "the whole A4 row is the failure unit: a broken layer query must not publish the other "
        "layers' numbers under the same row")
