"""`#10988` — A4's CI CONTRACT TWIN: per-layer label coverage, `NULL` kept as a reported share.

**Test tier: `CI`** (offline, deterministic — every arm below drives a pure function or a fake
projection; no graph, no model, no network, no `gh`, with ONE exception: the SDK-registration arm
spawns a single child interpreter because it needs a vocabulary built before `tortoise.sdk` is
imported. That spawn is the file's ONLY child process, and every other arm is sub-second.) ⛔ The file's
WALL cost is deliberately NOT quoted as a fixed figure here. It is a SHARD-LAYOUT input, a hand-written
local reading re-stales on every host it is taken on (`#2818`/`#3419`), and this same tree measured
**11.5-12 s at load average 30** on the host that wrote this line (import and load, not assertions, are
most of it) — so the number lives in `config/ci-surfaces.yml`'s `durations` row, refreshed from CI junit,
never in a sentence a reader has to re-measure to trust. A4's own measurement is **Tier E** (a read-only
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
   per-writer split are reported BESIDE the criterion (plan §7 E2E-8). ⛔ What must not be built is
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
    SNAPSHOT synthesis writes for a kindless operator (`projection/__init__.py:13804-13806`), and
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
    assert cov["by_kind"] == {"statement": 10}, (
        "`by_kind` is the present kinds WITHOUT the absent state — an empty dict here would "
        "satisfy a `'NULL' not in by_kind` test vacuously, so the CONTENT is asserted")
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


def test_the_two_histograms_are_pinned_by_INVARIANT_not_by_key_presence():
    """FAILS IF `labelled_by_declared_kind` or `by_kind` is emptied, re-keyed or made inconsistent.

    ⛔ MEASURED (#11837 review round 12, P2): both fields could be replaced with `{}` and the whole
    17-case pin set stayed GREEN — `by_kind` was asserted only as `"NULL" not in by_kind`, which an
    empty dict satisfies vacuously, and the render arm only checked that the STRING
    ` labelled_by_declared_kind=` appeared on the line. `capstone_measure.py`'s own render is what a
    scoring reader consumes, so an emptied histogram printed beside `labelled=3` is a
    self-contradicting artifact with nothing red.

    The invariants are the falsifier, so a future edit that drops a bucket reds HERE:
    the declared-kind histogram sums to `labelled`, `by_kind` holds every PRESENT kind (so it sums to
    `population` minus the absent share), and `by_kind`'s keys are exactly the declared kinds present
    plus the undeclared ones.
    """
    probe = _probe()
    cov = probe.label_coverage(
        {"statement": 7, "plan": 3, "constraint": 2, "NULL": 1},
        frozenset({"statement", "plan"}), layer="claims")
    assert sum(cov["labelled_by_declared_kind"].values()) == cov["labelled"] == 10, (
        f"the declared-kind histogram must sum to `labelled`: {cov['labelled_by_declared_kind']}")
    assert cov["labelled_by_declared_kind"] == {"statement": 7, "plan": 3}
    assert sum(cov["by_kind"].values()) == cov["population"] - cov["unlabelled_null"] == 12, (
        "`by_kind` carries every PRESENT kind — the absent state is reported as `unlabelled_null`, "
        f"so the histogram sums to population minus that share: {cov['by_kind']}")
    assert cov["by_kind"] == {"constraint": 2, "plan": 3, "statement": 7}
    assert set(cov["by_kind"]) == set(cov["labelled_by_declared_kind"]) | set(cov["undeclared"]), (
        "a kind present on the graph is in exactly one of the two histograms' key sets")
    # An EMPTY layer is where an unpinned histogram hides: every bucket is empty and every
    # key-presence assertion passes. Pin the zero shape too.
    zero = probe.label_coverage({}, frozenset({"statement"}), layer="claims")
    assert zero["labelled_by_declared_kind"] == {} and zero["by_kind"] == {}
    assert zero["labelled"] == 0 and zero["coverage"] is None


# ── 2. per layer, never blended ──────────────────────────────────────────


#: The five point kinds `tortoise.sdk` registers through `domain_loader.register_kind` at IMPORT time
#: (`sdk.py:229-232`, `:239`). No `packs/*/manifest.yaml` declares them and none is in
#: `CANONICAL_POINT_KINDS`, so the SDK import is the ONLY path that makes them declared.
_SDK_REGISTERED_KINDS = ("diary", "checkpoint-item", "option", "evidence", "criterion")


def test_the_declared_vocabulary_includes_the_kinds_the_SDK_itself_registers():
    """FAILS IF `_a4_declared_kinds` imports only `domain_loader` and misses the SDK's own kinds.

    ⛔ MEASURED ON THE LIVE GRAPH 2026-11-11, which is why this arm exists: with only
    `domain_loader` imported, `evidence` (121 nodes), `option` (2) and `diary` (2) read
    UNDECLARED — THREE of the fleet's six declared typed kinds on that graph, since
    `tortoise.sdk` registers them through `register_kind` at import time.
    `decision`/`jobToBeDone`/`useCase` are in `_BASE_KINDS` and stay declared, so they are not
    among the movers. ⛔ `finding` is NOT a fourth *declared* kind — it is declared by no
    vocabulary at all, i.e. the fourth undeclared *reading* and the one genuine gap, while the
    three above are the import artefact (#11837 round-6 P3-7). That is a FALSE failing run, and
    a refusal that names a false
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
        # the OTHER direction: the probe must not ADD a kind the product does not declare
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
    # three sorted str lists this test's own child printed
    bare, declared, extra = eval(lines[0]), eval(lines[1]), eval(lines[2])
    assert bare == [], (
        f"the product's BARE vocabulary already declares {bare} without the SDK import, so this arm "
        f"would be vacuous — find the real registration path before trusting the assertion below")
    assert declared == sorted(_SDK_REGISTERED_KINDS), (
        f"`_a4_declared_kinds` must re-supply the SDK-registered kinds (got {declared}) — without "
        f"its in-function `import tortoise.sdk` a standalone census reports the fleet's own declared "
        f"typed kinds as inventions")
    # ⛔ THE OTHER DIRECTION, which a round-2 rewrite of this arm lost (#11837 review round 3,
    # P2): `_a4_declared_kinds` must return the product's vocabulary and NOTHING MORE. Without
    # this, a `| {"finding", "totallyMadeUpKind"}` on its return passes every other case here,
    # i.e. a census could widen the vocabulary it measures itself against and report an
    # invention as coverage.
    assert extra == [], (
        f"`_a4_declared_kinds` declares kinds the product does not: {extra} — a census may not "
        f"widen the vocabulary it measures against, or an invention reads as coverage")


def test_the_three_layers_are_reported_SEPARATELY_and_never_blended():
    """FAILS IF `_a4_label_coverage` collapses things/events/claims into one percentage.

    A single blended rate is the failure mode `04-plan.md` §2 W2c lists **second** (the first is the
    graph head, which this artifact §5 says the run cannot supply): a failing thing or
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

    Turn rows are written `pointKind='event'` (`sdk.py:2541` `_TURN_WRITE_CYPHER`, whose
    `t.pointKind=turn.k` is fed by the payload's own `"k": "event"` at `sdk.py:3074`) and operator
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
    """FAILS IF `label_coverage` judges a layer against a SIBLING layer's vocabulary — or if the
    census WIRES one vocabulary into all three layers.

    ⛔ CORRECTED at review round 4: this docstring used to claim it saw a shared vocabulary
    ANYWHERE, but the calls below supply their own `frozenset`s, so they can only see the
    FUNCTION's rule — a mutant wiring `_a4_declared_kinds("pointKind")` into all three layers left
    this arm GREEN (measured). The WIRING half is the `_a4_label_coverage` assertion at the end.

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
    # ⛔ THE WIRING, which the direct calls above cannot reach: `_a4_label_coverage` chooses each
    # layer's vocabulary itself (`declared = _a4_declared_kinds(kind_prop)`). On an empty graph
    # every declared kind is absent, so `declared_absent` IS the vocabulary the layer was judged
    # against — three layers, three vocabularies, or the census blended them.
    wired = probe._a4_label_coverage(_FakeProjection())
    vocabularies = {layer: tuple(wired[layer]["declared_absent"])
                    for layer in ("claims", "things", "events")}
    assert len(set(vocabularies.values())) == 3, (
        "the three layers were judged against the same declared vocabulary — a shared set credits "
        f"a point kind on a thing as coverage: {vocabularies}")


def test_each_layer_QUERIES_its_own_node_label_and_only_claims_carries_the_exclusion():
    """FAILS IF `A4_LAYERS` names the wrong node label, or the exclusion moves off `claims`.

    ⛔ The fake answers on a CYPHER SUBSTRING that carries the kind PROPERTY but never the LABEL,
    so `("claims", "Point", "pointKind")` could become `("claims", "Object", "pointKind")` — a
    layer measuring the WRONG POPULATION, and (because the predicate is gated on `if label ==
    "Point"`) silently losing the turn-row/operator exclusion — with every suite GREEN (measured,
    `#11837` review round 4, P2). The label is pinned as QUERY TEXT, the way the predicate already
    is; the file's own header says this hazard is known, and it was only half-closed.

    ⛔ AND THE HOP, not just the row (`#11837` round-5 P2). The loop below hands
    `_a4_layer_counts` each label itself, so a mutant one frame up — hardcoding `"Point"` in
    `_a4_label_coverage`'s `for layer, label, kind_prop in A4_LAYERS` loop — left this whole
    test **GREEN** while every layer measured the CLAIM population (measured: 16 passed). The
    doc claimed the pin made "a layer cannot measure a sibling's population" true; it did not,
    because nothing observed the queries the census entry point emits. The second block drives
    `_a4_label_coverage` and asserts on THOSE queries, so that hop is closed rather than the
    sentence weakened.
    """
    probe = _probe()
    assert [label for _layer, label, _prop in probe.A4_LAYERS] == ["Point", "Object", "Event"], (
        "the three layers must read the three ontology node labels, in this order")
    projection = _FakeProjection()
    for layer, label, prop in probe.A4_LAYERS:
        projection.g.queries.clear()
        probe._a4_layer_counts(projection, label, prop)
        assert len(projection.g.queries) == 1, projection.g.queries
        (query,) = projection.g.queries
        assert f"MATCH (n:{label})" in query, (layer, query)
        assert f"coalesce(n.{prop}, 'NULL')" in query, (layer, query)
        if label == "Point":
            assert "is_episodic" in query and "is_operator" in query, (
                f"the {layer} layer lost the turn-row/operator exclusion: {query}")
        else:
            assert "is_episodic" not in query and "is_operator" not in query, (
                f"the {layer} layer carries an exclusion it must not — a `:{label}` is not a "
                f"claim: {query}")

    # ⛔ THE HOP (`#11837` round-5 P2). Drive the census entry point the production code actually
    # calls and assert on the queries IT emits: a hop that hardcodes one label for every layer
    # reds here and only here.
    census = _FakeProjection({
        **_CLAIM_ROWS,
        "RETURN coalesce(n.objectKind, 'NULL') AS k": [["Project", 4], ["NULL", 1]],
        "RETURN coalesce(n.eventKind, 'NULL') AS k": [["meeting", 2], ["turn", 6]],
    })
    probe._a4_label_coverage(census)
    emitted = list(census.g.queries)
    for layer, label, prop in probe.A4_LAYERS:
        # The per-layer histogram is the only query the census emits that carries
        # `coalesce(n.<prop>, 'NULL')`: the writer split's nearest neighbour is
        # `coalesce(n.pointKind, '')` (probe:3242), a DIFFERENT literal, so the filter cannot pick
        # up a sibling's query.
        own = [q for q in emitted if f"coalesce(n.{prop}, 'NULL')" in q]
        assert own, (f"the census issued no histogram query for the {layer} layer", emitted)
        for query in own:
            assert f"MATCH (n:{label})" in query, (
                f"the census emitted the {layer} layer's histogram against a sibling's label — "
                f"a hop that hardcodes one label measures every layer's population as that "
                f"layer's: {query}")


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
    # ⛔ BOTH layers, each in its own arm: this comment named point AND event while only `claims`
    # was exercised, so adding `events` to `_A4_CASEFOLDED_LAYERS` credited an `eventKind="Meeting"`
    # against a declared `meeting` — a FALSE coverage credit — and left the file GREEN (#11837
    # review round 3, P2, measured).
    exact = probe.label_coverage({"Statement": 1}, frozenset({"statement"}), layer="claims")
    assert exact["undeclared"] == {"Statement": 1}
    exact_event = probe.label_coverage({"Meeting": 1}, frozenset({"meeting"}), layer="events")
    assert exact_event["undeclared"] == {"Meeting": 1}, (
        "the EVENT layer's compare must stay exact-case — case-folding it credits an undeclared "
        "spelling as coverage, which is the fail-open direction the object layer's fold must not "
        "be generalised into")


def test_the_declared_vocabulary_is_IMPORTED_from_the_product_never_re_spelled():
    """FAILS IF a lane hardcodes a kind list instead of asking the product's own vocabulary.

    `domain_loader.known_kinds(bucket)` is the DECLARED vocabulary, imported — not the write
    validator for every bucket (see `capstone_measure.py::_a4_declared_kinds`) — so it is the
    product's answer; a second list here would drift in one direction (blessing undeclared kinds)
    or the other (calling a pack's declared kind an invention).
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

    # ⛔ MEMBERSHIP IS NOT IMPORT. Every assertion above is satisfied by a LITERAL snapshot of
    # today's vocabularies — measured GREEN with `known_kinds` swapped for a frozen copy (#11837
    # review round 3, P2). The import is proven only by changing the PRODUCT's registry and
    # watching the answer follow: `_a4_declared_kinds` re-imports `known_kinds` on every call, so
    # patching the module attribute is what a real registry change looks like from here.
    import tortoise.domain_loader as _dl
    sentinel = "zz-sentinel-kind-11837"
    real_known = _dl.known_kinds
    try:
        _dl.known_kinds = lambda bucket=None: (
            (real_known(bucket) | {sentinel}) if bucket == "eventKind" else real_known(bucket))
        followed = probe._a4_declared_kinds("eventKind")
    finally:
        _dl.known_kinds = real_known
    assert sentinel in followed, (
        "`_a4_declared_kinds` did not follow the product's registry when that registry changed — "
        "a re-spelled list or a frozen snapshot passes every assertion above and then rots "
        "silently the moment a pack declares its next kind")


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

    Plan §7 E2E-8: the per-writer split and the `typed`/`statement`/`NULL` shares are reported BESIDE
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


def test_the_claims_histogram_is_read_ONCE_and_both_published_numbers_share_it():
    """FAILS IF `_a4_claim_diagnostics` issues its OWN claims query beside the criterion's.

    ⛔ A second read is a SECOND POPULATION, and it is the one defect here that no other arm can
    see: on the live fleet graph a write landing between the two reads publishes one layer's
    population twice — `a4 claims population=<X>` beside `a4 diagnostics claim_points=<Y>` — with
    every rendering, fold and vocabulary arm still GREEN (`#11837` review; the guards are the
    `counts=` parameter of `_a4_claim_diagnostics` and its `if counts is None` default). ⛔ THOSE
    GUARDS ARE NAMED BY SYMBOL, NOT BY LINE: this docstring used to quote
    `capstone_measure.py:3287`, and a two-line insertion above it re-staled the number onto a
    comment in the same PR — the rot the bundle's own rule ("the symbol is authoritative and the
    number is a convenience") exists to stop.

    Pinned as QUERY TEXT, the way the predicate and the node label are: `_FakeGraph` records every
    cypher it answers, and the claims histogram is uniquely identifiable by its `RETURN`.
    """
    probe = _probe()
    projection = _FakeProjection(_CLAIM_ROWS)
    a4 = probe._a4_label_coverage(projection)
    histograms = [q for q in projection.g.queries
                  if "RETURN coalesce(n.pointKind, 'NULL') AS k" in q]
    assert len(histograms) == 1, (
        "the claims histogram was read more than once — a second read is a second population, so "
        f"the criterion and the diagnostic can disagree on one layer: {histograms}")
    assert a4["claim_points"] == a4["claims"]["population"], (
        "the criterion and the diagnostic report two populations for the SAME layer",
        a4["claims"]["population"], a4["claim_points"])


def test_the_per_writer_split_attributes_by_REPRODUCTION_and_says_it_is_not_a_stamp():
    """FAILS IF attribution is presented as a stored fact, or as "some other lane".

    No Point property is USED as a producer stamp here: the one that exists, `authoredBy`
    (`projection/entities.py`'s `_POINT_HANDLED` does declare it and the write path persists it from
    the payload), records the CALLER's own claim, so the split is a REPRODUCTION test: the rule
    classifier (`extractor.py::_classify_point_kind`) re-derived over each node's content. And
    `extractor_v2` cannot be the source of a typed claim at all — its write gate repairs every
    non-master point kind to `statement` (`extractor_v2.py:10419-10422`, the non-master arm;
    `:10413` is the EMPTY-kind default and repairs nothing else).
    """
    probe = _probe()
    projection = _FakeProjection({
        **_CLAIM_ROWS,
        "RETURN n.content, n.pointKind": [
            # the rule classifier derives `decision` from "we decided …" -> reproduces
            ["We decided to adopt option A.", "decision"],
            # stored `plan`, but the classifier derives `statement` from this content -> supplied
            ["The cat sat on the mat.", "plan"],
            # the classifier RAISES on this one -> the NAMED `unattributed` bucket
            ["SENTINEL content the classifier cannot read", "decision"],
        ],
    })
    # ⛔ The `except` arm is EXERCISED, not merely present: with every fixture row attributed,
    # `"attributed": total` passed this arm (#11837 review round 5, P2), so a census claiming
    # EVERYTHING was attributed could not be caught — the honesty cell was unfalsifiable. The
    # classifier is imported INSIDE the probe function, so the patch point is the SOURCE module.
    import tortoise.extractor as _extractor
    real_classify = _extractor._classify_point_kind

    def _sometimes_unreadable(content):
        if "SENTINEL" in content:
            raise RuntimeError("the classifier cannot read this content")
        return real_classify(content)

    _extractor._classify_point_kind = _sometimes_unreadable
    try:
        split = probe._a4_writer_attribution(projection)
    finally:
        _extractor._classify_point_kind = real_classify
    assert split["typed_claims"] == 3
    assert split["by_writer"]["classifier-reproduces"]["total"] == 1
    assert split["by_writer"]["caller-supplied"]["total"] == 1
    assert split["by_writer"]["classifier-reproduces"]["by_kind"] == {"decision": 1}
    assert split["by_writer"]["caller-supplied"]["by_kind"] == {"plan": 1}
    assert split["by_writer"]["unattributed"]["by_kind"] == {"decision": 1}, (
        "an unreadable row must land in the NAMED `unattributed` bucket, not vanish or be guessed")
    assert split["attributed"] == 2, (
        f"`attributed` must EXCLUDE the unattributed bucket, not restate typed_claims "
        f"({split['typed_claims']})")
    assert split["attributed"] < split["typed_claims"]
    assert str(split["provenance"]).startswith("UNMEASURED("), (
        "attribution must not be presented as a stored fact — no producer stamp is read")
    assert "extractor_v2" in split["v2_lane_excluded_by_construction"]


# ── 6. the rendered artefact a scorer reads ─────────────────────────────


def _full_census(probe) -> dict:
    """A complete census block, as `a_row_census` would return it.

    ⛔ The claims histogram carries an UNDECLARED kind (`constraint`) beside the `NULL`, so BOTH
    halves of the absent share are non-zero and a renderer that drops either one is visible: the
    absent total is 3 = 1 `NULL` + 2 undeclared, and only the `NULL` half may reach `null=`.
    ⏔ The three layers carry DIFFERENT numbers ON PURPOSE: identical blocks would let a renderer
    that emits ONE layer under all three keys pass every per-layer assertion below, which is the
    exact blend A4 exists to red (#11837 review round 2, P2). Populations are 6 / 3 / 2.
    """
    hist = {"claims": {"statement": 3, "constraint": 2, "NULL": 1},
            "things": {"project": 1, "NULL": 2},
            "events": {"NULL": 2}}
    layer = lambda name: probe.label_coverage(  # noqa: E731
        hist[name],
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

    ⛔ AND the absent share is rendered as its DECOMPOSITION, not only as a total: `label_coverage`
    computes `unlabelled_null` / `unlabelled_undeclared` so a reader never has to subtract the
    `undeclared` dict to recover the `NULL` denominator, which is the derivation
    `capstone_measure.py`'s own comment refuses to impose. Before this arm listed them, the
    docstring's "or the diagnostic loses its NULL share" clause was UNFALSIFIABLE for the
    thing/event layers — deleting either key from the renderer left it GREEN (PR #11837 round-7 P2).
    """
    probe = _probe()
    rendered = "\n".join(probe.a_row_census_lines(_full_census(probe)))
    #: `(population, labelled)` as `_full_census`'s fixture builds them. ⛔ A per-layer *key* pin is
    #: not enough: a renderer reading ONE layer and printing it under all three keys satisfies key
    #: presence, the halves-sum check and the claims-only value assertion below — measured, GREEN,
    #: with `block = a4.get("claims")` (#11837 review round 2, P2). The numbers are the pin.
    layer_shape = {"claims": (6, 3), "things": (3, 1), "events": (2, 0)}
    assert len({pop for pop, _ in layer_shape.values()}) == len(layer_shape), (
        "the fixture no longer gives the three layers distinct populations, so this arm cannot "
        "tell a per-layer render from one that repeats a single layer")
    for layer in ("claims", "things", "events"):
        line = [ln for ln in rendered.splitlines() if ln.startswith(f"a4 {layer} ")]
        assert line, f"the {layer} layer is not rendered:\n{rendered}"
        want_pop, want_lab = layer_shape[layer]
        assert f"population={want_pop} labelled={want_lab} " in line[0], (
            f"the {layer} line does not carry THAT layer's numbers — a renderer emitting one "
            f"layer under three keys reads as three measured layers:\n{line[0]}")
        # ⛔ A LEADING SPACE on every name: `unlabelled_undeclared=` is printed EARLIER on the same
        # line, so a bare `undeclared=` substring is satisfied by it and the renderer could drop
        # the key that reports the UNDECLARED kinds with this loop still GREEN (measured, #11837
        # review round 5, P2).
        for part in ("population", "labelled", "unlabelled", "unlabelled_null",
                     "unlabelled_undeclared", "coverage", "non_vacuity",
                     "declared_present", "declared_absent", "expected_absent",
                     "undeclared", "labelled_by_declared_kind"):
            assert f" {part}=" in line[0], (layer, part, line[0])
        # The rendered halves are NUMBERS that add up to the rendered total — a placeholder
        # (`unlabelled_null=None`) or a re-worded key is not the decomposition the epic accepts.
        fields = dict(part.split("=", 1) for part in line[0].split() if "=" in part)
        assert (int(fields["unlabelled_null"]) + int(fields["unlabelled_undeclared"])
                == int(fields["unlabelled"])), (layer, line[0])
        # ⛔ The declared-kind HISTOGRAM must not render EMPTY beside a non-zero `labelled`: the
        # renderer falls back to `{}` (`capstone_measure.py:3611`), so an emptied dict printed
        # `labelled=<n> labelled_by_declared_kind={}` — a self-contradicting line that every
        # key-presence assertion above still passes (measured, #11837 review round 12, P2).
        if int(fields["labelled"]) > 0:
            assert fields["labelled_by_declared_kind"] != "{}", (
                f"the {layer} layer renders labelled={fields['labelled']} with an EMPTY "
                f"declared-kind histogram — a scoring reader sees a self-contradicting line:\n"
                f"{line[0]}")
    claims = next(ln for ln in rendered.splitlines() if ln.startswith("a4 claims "))
    assert "unlabelled_null=1 unlabelled_undeclared=2 " in claims, (
        "the claims layer's absent share is 1 NULL + 2 undeclared; the log must carry both halves "
        f"as their own numbers, not only the total:\n{claims}")
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
    reason = a4["error"]
    assert str(reason).startswith("UNMEASURED("), a4["error"]
    assert a4["things"]["population"] == reason, a4["things"]
    assert a4["things"]["population"] != 0
    assert a4["claims"]["population"] == reason, (
        "the whole A4 row is the failure unit: a broken layer query must not publish the other "
        "layers' numbers under the same row")
    # ⛔ `non_vacuity` is the row's ONLY boolean, and a boolean cannot take the reason-string
    # substitution the numerics take: the string is TRUTHY, so `non_vacuity` read as PASS on a
    # broken query — the inverse of the rule this arm exists to enforce (measured, #11837 review
    # round 12, P2). It ships as `None`, which answers NEITHER reading (a three-valued field is read
    # `is True` / `is False` / `is None`, exactly as `coverage` is); ⛔ pinned as `is None` rather
    # than truthiness, because `not False` is `True` and a truthiness assert passes the ZERO SKELETON
    # the row's own comment says must never be what a broken query returns (#11837 review round 13,
    # P2).
    for layer in ("claims", "things", "events"):
        block = a4[layer]
        assert block["non_vacuity"] is None, (
            f"the {layer} layer reports non_vacuity={block['non_vacuity']!r} on a BROKEN query — "
            f"`True` scores an infrastructure failure as non-vacuous (false PASS) and `False` is the "
            f"zero skeleton (a MEASURED ABSENCE): {block}")
        # ⛔ EVERY OTHER FIELD MUST CARRY **THE** REASON — compared BY IDENTITY-OF-VALUE, not by a
        # `startswith("UNMEASURED(")` prefix. A prefix pin is satisfied by the skeleton's OWN reason
        # (`UNMEASURED(a claim-point population)`, which reads as a ZERO POPULATION) and it cannot see
        # a field the loop simply does not name: `typed_by_kind={}` and `coverage=None` both read as
        # MEASURED results (`None` is exactly "zero population" on this row) and both stayed green
        # under every arm until they were named (#11837 review round 14, P2/P3).
        for key, value in block.items():
            if key == "non_vacuity":
                continue
            assert value == reason, (
                f"the {layer} layer's `{key}` reads {value!r} on a BROKEN query instead of THE "
                f"row's reason {reason!r} — a skeleton value under a key a real measurement also "
                f"occupies reads as a MEASURED result: {block}")
    # ⛔ The DIAGNOSTICS are the other half of "the whole A4 row is the failure unit" asserted just
    # above: a mutant that keeps the skeleton's numeric `0` — or substitutes the skeleton's OWN
    # reason, or leaves an empty `{}` — renders `claim_points=0 … error=UNMEASURED(…)`, a
    # self-contradicting line scoring the row RED on a broken query, with every arm still green
    # (#11837 review rounds 13 and 14, P2).
    for key in ("claim_points", "excluded_capture_rows", "statement", "null", "typed",
                "typed_share", "typed_by_kind"):
        assert a4[key] == reason, (
            f"the A4 diagnostic `{key}` reads {a4[key]!r} on a BROKEN query instead of THE row's "
            f"reason {reason!r} — an empty container or the skeleton's own reason both read as a "
            f"MEASURED result: {a4[key]!r}")
    for key in ("typed_claims", "attributed", "by_writer", "v2_lane_excluded_by_construction",
                "provenance"):
        assert a4["per_writer"][key] == reason, (
            f"`per_writer.{key}` reads {a4['per_writer'][key]!r} on a BROKEN query instead of THE "
            f"row's reason: {a4['per_writer']}")
    # ⛔ And the ARM'S NAME is a claim: "renders a NAMED gap". The previous revision never called the
    # renderer at all, so `population=0` / `non_vacuity=False` on a broken query was unpinned in the
    # very arm whose docstring forbids it (#11837 review round 14, P2). Rendered, not just computed.
    rendered = probe.a_row_census_lines(census)
    a4_lines = [ln for ln in rendered if ln.startswith("a4 ")]
    assert a4_lines, rendered
    for layer in ("claims", "things", "events"):
        line = next((ln for ln in a4_lines if ln.startswith(f"a4 {layer} ")), None)
        assert line is not None, (layer, a4_lines)
        assert "population=0 " not in line, (
            f"the {layer} line RENDERS `population=0` on a broken query — a MEASURED ABSENCE: {line}")
        for bad in ("non_vacuity=False", "non_vacuity=True", "coverage=None"):
            assert bad not in line, (
                f"the {layer} line renders `{bad}` on a broken query — a MEASURED result rather "
                f"than a named gap: {line}")
        assert f"population={reason}" in line, (layer, line)
    diagnostics = next(ln for ln in a4_lines if ln.startswith("a4 diagnostics "))
    assert f"error={reason}" in diagnostics, (
        "the diagnostics line must render the row's reason beside the fields it explains — the "
        f"`row()` comment says so: {diagnostics}")
