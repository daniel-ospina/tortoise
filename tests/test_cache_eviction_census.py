"""Tests for the #10976 egress census — `tortoise/cache_eviction.py` + its CLI.

THE INVARIANT EVERY TEST HERE DEFENDS
-------------------------------------
> *A pass that cannot prove the record serves a class must REFUSE, never sweep.*

The discriminating tests therefore do not merely check a verdict word; they check
that the refusal path **issued no mutating statement**, because the failure this
issue exists to prevent is not "a wrong verdict was printed", it is "a node whose
only copy is the graph was deleted". A census that printed REFUSED and swept anyway
would pass a verdict-only assertion.

`_FakeGraph` records EVERY statement it is asked to run, so a `DELETE`/`DETACH
DELETE`/`DROP` anywhere on the path fails the run rather than being argued about.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise.cache_eviction import (
    CACHE_CEILING_ENV,
    DERIVED_LABELS,
    PARITY_INSTRUMENTS,
    POPULATION_RESIDENT_COUNT,
    TRUTH_LABELS,
    Assessment,
    CacheReadError,
    CacheState,
    ParityReport,
    assess,
    classify,
    config_labels,
    load_parity_report,
    measure_state,
    node_total,
    population_resident_count,
    resident_counts,
)

#: Anything that would change the graph. The census is READ-ONLY by construction;
#: this list is what "by construction" is asserted against.
MUTATING = ("DELETE", "DETACH", "DROP", "CREATE", "MERGE", "SET ", "REMOVE",
            "FLUSHALL", "GRAPH.DELETE", "UNLINK")


class _FakeGraph:
    """A graph handle that answers label/count reads and RECORDS every statement.

    ⛔ ``query`` is GRAPH.QUERY — the verb that MINTS a missing graph — so it is not a
    recording stub: it RAISES. Any module path that reads through it fails the run
    rather than being argued about (measured in this repo: a probe against a missing
    name created it and it appeared in `GRAPH.LIST`, `tools/engine_probe.py`).
    """

    def __init__(self, counts: dict[str, int], *, unlabelled: int = 0,
                 populations: dict[str, int] | None = None) -> None:
        self._counts = dict(counts)
        self._unlabelled = unlabelled
        #: population -> how many nodes its OWN predicate answers for, in THIS graph.
        #: ⛔ Explicit, never derived from the label counts: the turn population is a
        #: SUBSET of `:Point` (`is_episodic = true`), which is the whole reason the
        #: licence bound cannot use the label/whole-graph total. A population the fixture
        #: does not name answers 0 — so a report for it is DEMOTED, and a test that
        #: expected a licence has to state the population's real size rather than
        #: inherit a number from a label that is not the population.
        self._populations = dict(populations or {})
        #: (verb, statement) for EVERY statement, so a test can assert the VERB.
        self.queries: list[tuple[str, str]] = []
        # The TRUE node count. Defaults to the label sum so ordinary fixtures stay
        # unsurprising; a multi-label fixture overrides it (the label sum then
        # over-states, exactly as it does on a real graph).
        self.true_total: int | None = None

    class _Result:
        def __init__(self, rows):
            self.result_set = rows

    def ro_query(self, statement: str, params=None):
        """GRAPH.RO_QUERY — the read-only verb, which cannot create a graph."""
        self.queries.append(("ro_query", statement))
        return self._answer(statement)

    def query(self, statement: str, params=None):
        """GRAPH.QUERY — refuses, because a census must never issue it."""
        self.queries.append(("query", statement))
        raise AssertionError(
            f"the census issued GRAPH.QUERY ({statement!r}) — GRAPH.QUERY CREATES the "
            f"graph when the name does not exist, so every read must go through "
            f"GRAPH.RO_QUERY (`ro_query`)"
        )

    def _answer(self, statement: str):
        # ⛔ THE POPULATION READ IS RECOGNISED BY IDENTITY, not by a literal in the text
        # (#11709 review, P3). The old form keyed on `"is_episodic = true" in
        # statement`, so a SECOND population whose statement carries another predicate
        # — anything starting `MATCH (n:` and containing `count(n)` — fell through to
        # the LABEL branch below and answered that label's count (0 for a label the
        # fixture does not declare) instead of raising `unexpected statement`. A future
        # test could then assert a demotion production would never produce. Matching the
        # registered statement exactly answers every registered population from its own
        # declared size, and an UNREGISTERED one cannot reach this method at all
        # (`population_resident_count` refuses before issuing any read).
        squashed = " ".join(statement.split())
        for name, registered in POPULATION_RESIDENT_COUNT.items():
            if " ".join(registered.split()) == squashed:
                return self._Result([[self._populations.get(name, 0)]])
        if "db.labels" in statement:
            return self._Result([[label] for label in self._counts])
        if "size(labels(n)) = 0" in statement:
            # Label-less nodes, MEASURED — never a residual (multi-label nodes make
            # `total - sum(counts)` go negative; see `resident_counts`).
            return self._Result([[self._unlabelled]])
        if re.fullmatch(r"MATCH \(n:(.+)\) RETURN count\(n\)", statement):
            # ⛔ AN EXACT per-label count, the one form `resident_counts` issues. A
            # statement that merely STARTS `MATCH (n:` and carries `count(n)` — a
            # predicate-carrying count, i.e. a population read — must NOT be answered
            # here: it would silently get a LABEL's count (0 for an undeclared label),
            # and a future test could then assert a demotion production never produces.
            label = statement[len("MATCH (n:"):-len(") RETURN count(n)")]
            return self._Result([[self._counts.get(label.strip("`"), 0)]])
        if statement.startswith("MATCH (n) RETURN count(n)"):
            if self.true_total is not None:
                return self._Result([[self.true_total]])
            return self._Result([[sum(self._counts.values()) + self._unlabelled]])
        raise AssertionError(f"unexpected statement: {statement}")

    @property
    def statements(self) -> list[str]:
        return [statement for _verb, statement in self.queries]

    @property
    def mutated(self) -> list[str]:
        """Statements that would CHANGE the graph.

        Backtick-quoted spans are stripped first: in Cypher an identifier inside
        backticks is DATA, so a hostile label that spells `DETACH DELETE` inside one
        is exactly the case that must NOT read as a mutation — that is the P0 this
        detector exists to catch, in both directions.
        """
        out = []
        for statement in self.statements:
            bare = re.sub(r"`(?:[^`]|``)*`", "``", statement)
            if any(token in bare.upper() for token in MUTATING):
                out.append(statement)
        return out


class _FakeClient:
    """A redis client that answers INFO/GRAPH.LIST/keyspace probes and nothing else."""

    def __init__(self, *, used=1_000, maxmemory=10_000, policy="noeviction",
                 keys=("tortoise",), ttl=-1, graphs=("tortoise",)) -> None:
        self._info = {
            "used_memory": used,
            "maxmemory": maxmemory,
            "maxmemory_policy": policy,
        }
        self._keys = list(keys)
        self._ttl = ttl
        #: What `GRAPH.LIST` reports. Listing creates nothing, so this is the
        #: side-effect-free existence check the census makes before it reads.
        self._graphs = list(graphs)
        self.calls: list[tuple[str, tuple]] = []

    def info(self, section=None):
        self.calls.append(("info", (section,)))
        return dict(self._info)

    def keys(self, pattern):
        # ⛔ `KEYS *` is an O(N) full-keyspace scan on a single-threaded server — it
        # would stall the cache this census exists to protect. Its presence here fails
        # the run rather than being argued about.
        raise AssertionError(
            f"the census must not scan the keyspace (KEYS {pattern!r}); probe by name"
        )

    def exists(self, name):
        self.calls.append(("exists", (name,)))
        return name in self._keys

    def ttl(self, name):
        self.calls.append(("ttl", (name,)))
        return self._ttl

    def execute_command(self, *args, **kwargs):
        # `GRAPH.MEMORY USAGE` — the path `measure_state` must reach through the
        # CLIENT, not a graph handle. The reply is the real shape (a FLAT key/value
        # list, see `tortoise/graph_storage.py::parse_memory_usage`) so the reading can
        # be asserted as a VALUE: a fake that merely refuses told the pre-fix shape
        # (a `Graph` handle, which has no `execute_command`) and the post-fix one apart
        # only by the wording of an error message, which is what made the old pin
        # vacuous.
        self.calls.append(("execute_command", args))
        if args and str(args[0]).upper() == "GRAPH.LIST":
            return list(self._graphs)
        return ["total_graph_sz_mb", 12.5]

    # Deliberately absent: set/delete/flushall. A census reaching for them is a
    # bug and an AttributeError here names the bug at the point of the call.
    def __getattr__(self, name):
        raise AssertionError(f"the census must not touch client.{name}()")


@pytest.fixture(autouse=True)
def _no_ambient_ceiling(monkeypatch):
    monkeypatch.delenv(CACHE_CEILING_ENV, raising=False)


# --------------------------------------------------------------------------- #
# The refusal: over the bound, nothing proven, and NOTHING touched.
# --------------------------------------------------------------------------- #

def test_over_ceiling_with_no_instrument_REFUSES_and_never_mutates():
    """⛔ THE RED-FIRST TEST. Fails before the fix: there was no such assessment.

    A graph over its bound, holding §3's truth labels and derived labels, with no
    parity instrument run. The answer must be REFUSED — and `_FakeGraph.mutated`
    proves it did not sweep.

    Before this change the *behaviour* existed but nowhere was it a property: the
    cache simply grew to `maxmemory` and was refused by the engine, and any sweep
    anyone wrote had nothing that told it which nodes were record-served. This test
    is the executable form of "nothing may be evicted unproven".
    """
    g = _FakeGraph({"Point": 10_000, "GraphEvent": 4_000, "Source": 200, "Object": 900})
    c = _FakeClient(used=195_000_000, maxmemory=200_000_000)

    result = assess(c, g, "tortoise", ceiling_bytes=100_000_000)

    assert isinstance(result, Assessment)
    assert result.verdict == "REFUSED"
    assert result.state.over_ceiling is True
    assert result.eligible_classes == ()
    # Every resident label is named as unproven, with a reason a reader can act on.
    reasons = " ".join(result.problems)
    for label in ("Point", "GraphEvent", "Source", "Object"):
        assert label in reasons, f"{label} must be named in the refusal, got: {reasons}"
    assert "cannot-answer" in reasons
    # ⛔ THE ASSERTION THAT MATTERS: a refusal wrote nothing.
    assert g.mutated == [], f"refusal issued mutating statement(s): {g.mutated}"


def test_the_truth_labels_are_never_silently_treated_as_evictable():
    """§3's TRUTH set must not become eligible just because a derived class was proven."""
    # ⛔ THE FIXTURE MUST CARRY A DERIVED LABEL. Turns ARE `:Point` nodes, so a test
    # with no `:Point` row cannot catch the regression it is named for — the semantic
    # form of "a CLEAN turn census licenses evicting every `:Point`". Verified by
    # mutation: marking every derived-authority row eligible once ANY population is
    # proven survived the earlier, Point-less fixture green.
    g = _FakeGraph({"Source": 50, "GraphEvent": 5_000, "Point": 10_000, "Object": 900},
                   populations={"turn": 2_000})
    c = _FakeClient(used=900, maxmemory=1_000)
    # A CLEAN turn census — which must license the TURN population and NOTHING else.
    report = ParityReport(instrument="tools/turn_parity_census.py", verdict="CLEAN",
                          measured_nodes=2_000, total_nodes=2_000,
                          graph_name="tortoise")

    result = assess(c, g, "tortoise", ceiling_bytes=100,
                    parity_reports={"tools/turn_parity_census.py": report})

    assert [e.label for e in result.eligible_classes] == ["turn (population)"], (
        "a proven POPULATION must never make a LABEL eligible: turns are `:Point` "
        "nodes, so this is the licence-to-delete-every-Point regression"
    )
    # ⛔ The LICENCE names the BARE population ("turn"), not the display label
    # ("turn (population)"): the executor's population gate compares for
    # equality, so a licence naming the display string refused the sweep it
    # had just licensed. `label` still carries the display form.
    assert [e["population"] for e in result.plan["evictable"]] == ["turn"]
    assert "Point" in " ".join(result.problems)
    assert "Object" in " ".join(result.problems)
    assert g.mutated == []


def test_a_clean_verdict_over_a_partial_population_does_not_prove_anything():
    """A CLEAN verdict is not a licence when the instrument measured part of the class."""
    g = _FakeGraph({"Point": 100})
    c = _FakeClient(used=900, maxmemory=1_000)
    partial = ParityReport(instrument="tools/turn_parity_census.py", verdict="CLEAN",
                           measured_nodes=40, total_nodes=100, graph_name="tortoise")

    result = assess(c, g, "tortoise", ceiling_bytes=100,
                    parity_reports={"tools/turn_parity_census.py": partial})

    assert result.verdict == "REFUSED"
    assert result.eligible_classes == ()
    assert "40/100" in " ".join(result.problems)


def test_a_verdict_with_no_population_is_not_evidence_about_a_population():
    g = _FakeGraph({"Point": 100})
    c = _FakeClient(used=900, maxmemory=1_000)
    no_population = ParityReport(instrument="tools/turn_parity_census.py", verdict="CLEAN",
                                 graph_name="tortoise")

    result = assess(c, g, "tortoise", ceiling_bytes=100,
                    parity_reports={"tools/turn_parity_census.py": no_population})

    assert result.verdict == "REFUSED"


@pytest.mark.parametrize("verdict", ["DIRTY", "MISMATCH", "", "problems"])
def test_a_non_clean_verdict_never_proves(verdict):
    g = _FakeGraph({"Point": 100})
    c = _FakeClient(used=900, maxmemory=1_000)
    report = ParityReport(instrument="tools/turn_parity_census.py", verdict=verdict,
                          measured_nodes=100, total_nodes=100, graph_name="tortoise")

    result = assess(c, g, "tortoise", ceiling_bytes=100,
                    parity_reports={"tools/turn_parity_census.py": report})

    assert result.verdict == "REFUSED"


def test_a_clean_byte_verdict_still_publishes_the_licence():
    """⛔ The negative case: `CLEAN` with a PROVEN population must carry the licence.

    This is the state that made the entry bound unenforceable — under the byte ceiling,
    so `CLEAN`, but the entry budget a consumer enforces may be exceeded by orders of
    magnitude (many small turn nodes, the shape #10976 describes).
    """
    g = _FakeGraph({"Point": 10}, populations={"turn": 10})
    c = _FakeClient(used=500, maxmemory=10_000)
    report = ParityReport(
        instrument=PARITY_INSTRUMENTS["turn"], verdict="CLEAN",
        measured_nodes=10, total_nodes=10, graph_name="tortoise",
    )

    result = assess(c, g, "tortoise", ceiling_bytes=1_000,
                    parity_reports={PARITY_INSTRUMENTS["turn"]: report})

    assert result.verdict == "CLEAN", result.problems
    assert [e["population"] for e in result.plan["evictable"]] == ["turn"]
    assert result.plan["over_by_bytes"] == 0
    assert g.mutated == []


def test_under_the_bound_is_clean_and_proposes_nothing():
    g = _FakeGraph({"Point": 10})
    c = _FakeClient(used=500, maxmemory=10_000)

    result = assess(c, g, "tortoise", ceiling_bytes=1_000)

    assert result.verdict == "CLEAN"
    # ⛔ `CLEAN` IS THE BYTE STATE, NOT "NOTHING TO DO", and the plan now says so on
    # every verdict. A consumer enforcing an ENTRY budget cannot be gated by the byte
    # verdict: a cache far over its node budget but under its byte ceiling answered
    # CLEAN with `plan == {}`, so the entry bound was unenforceable (measured: 1,000,000
    # nodes against a 20,000-node budget -> REFUSED, 0 deleted). This fake has no proven
    # population, so the licence is EMPTY and the refusal is as sharp as it was.
    assert result.plan["evictable"] == []
    assert result.plan["over_by_bytes"] == 0
    assert "not_evictable" in result.plan
    assert g.mutated == []


# --------------------------------------------------------------------------- #
# The measurement: the bound, the policy, the TTL, the eviction unit.
# --------------------------------------------------------------------------- #

def test_an_undeclared_ceiling_names_the_provider_cap_as_the_bound_it_used():
    """No ceiling is the finding, not an error — and the source is stated.

    #10976's Target is *"resident graph bytes bounded by a declared ceiling"*. Today
    nothing declares one, so the census measures against `maxmemory` and SAYS it did:
    a provider cap is a write REFUSAL, not a policy choice, and reading one as the
    other is how "we have a bound" gets written down when we do not.
    """
    c = _FakeClient(used=500, maxmemory=10_000)

    state = measure_state(c, "tortoise")

    assert state.ceiling_bytes is None
    assert state.bound_bytes == 10_000
    assert "NOT a declared ceiling" in state.ceiling_source
    assert state.maxmemory_policy == "noeviction"


def test_the_state_carries_the_eviction_unit_evidence_ttl_and_key_count():
    """`noeviction` + `ttl=-1` + N keys per graph is WHY the server arm is refused."""
    c = _FakeClient(used=500, maxmemory=10_000, policy="noeviction",
                    keys=("tortoise", "telemetry{tortoise}"), ttl=-1)

    state = measure_state(c, "tortoise")

    assert state.maxmemory_policy == "noeviction"
    assert state.graph_key_ttl_seconds == -1
    assert state.keys_per_graph == 2, (
        "a graph is a graphdata key plus a telemetry stream; the key count IS the "
        "evidence that a server-side policy can only evict the whole graph"
    )


def test_an_unreadable_info_is_a_refusal_not_a_zero_byte_cache():
    class _Broken(_FakeClient):
        def info(self, section=None):
            raise OSError("connection reset")

    with pytest.raises(CacheReadError):
        measure_state(_Broken(), "tortoise")


def test_a_bad_ceiling_env_is_refused_rather_than_ignored(monkeypatch):
    monkeypatch.setenv(CACHE_CEILING_ENV, "12 gigabytes")
    with pytest.raises(CacheReadError):
        measure_state(_FakeClient(), "tortoise")


def test_resident_counts_include_what_it_could_not_label():
    """A census that silently dropped unlabelled nodes would understate the cache."""
    g = _FakeGraph({"Point": 10}, unlabelled=3)

    counts = resident_counts(g)

    assert counts["<unlabelled>"] == 3
    assert g.mutated == []


# --------------------------------------------------------------------------- #
# §3's split: the classification is the doc's, not a second opinion.
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("label", sorted(TRUTH_LABELS))
def test_truth_labels_classify_as_truth(label):
    assert classify(label, config=()) == "truth"


@pytest.mark.parametrize("label", sorted(DERIVED_LABELS))
def test_derived_labels_classify_as_derived(label):
    assert classify(label, config=()) == "derived"


def test_config_labels_come_from_projections_registry_not_a_copy():
    """§3's invariant is `derived = replay(journal) ∪ preserved_config`.

    The config term has ONE owner, `projection._config_classes()`. If this module
    kept its own list the two would drift the first time a pack is added, and the
    drift would show up as a config node counted as derived.

    ⛔ This goes THROUGH `config_labels()`, and then through `assess()` — its only
    caller — because a test named for that function must fail when the function
    does. An earlier revision built the label set HERE and handed it to
    `classify(config=...)` directly, so `return frozenset()` inside `config_labels()`
    survived the entire suite: the empty set is the safe direction today and the
    WRONG one the day a class is proven, which is exactly what that function's own
    docstring is about.
    """
    from tortoise.projection import _config_classes

    registry = frozenset(cls.label for cls in _config_classes())
    assert registry, "the config registry must not be empty"
    assert config_labels() == registry, (
        "`config_labels()` must BE projection's registry, not a second copy: a "
        "hand-kept list drifts the first time a pack is added"
    )
    for label in sorted(registry):
        assert classify(label, config=config_labels()) == "config"

    # And through the only consumer: a registry class reaches `assess` as `config`,
    # is never eligible, and is never offered as evictable. With `frozenset()` here
    # the authority below is `unclassified` — a different CLASS, not just a
    # different word, so this cannot pass on the mutation.
    config_label = sorted(registry)[0]
    g = _FakeGraph({config_label: 3})
    c = _FakeClient(used=900, maxmemory=0)

    result = assess(c, g, "tortoise")

    reading = {cls.label: cls for cls in result.classes}[config_label]
    assert reading.authority == "config"
    assert reading.eligible is False
    assert reading.proven is False
    assert config_label in result.plan["not_evictable"]
    assert g.mutated == []


def test_an_unknown_label_is_unclassified_and_refused_not_defaulted():
    assert classify("SomethingNobodyTaughtUs", config=()) == "unclassified"


# --------------------------------------------------------------------------- #
# Reading a parity instrument's report.
# --------------------------------------------------------------------------- #

def test_a_report_that_is_not_a_json_object_is_refused(tmp_path):
    path = tmp_path / "turns.json"
    path.write_text("[]", encoding="utf-8")

    with pytest.raises(CacheReadError):
        load_parity_report(str(path), graph_name="tortoise")


def test_a_missing_report_file_is_a_usage_error_not_a_refusal(tmp_path):
    with pytest.raises(OSError):
        load_parity_report(str(tmp_path / "absent.json"), graph_name="tortoise")


# --------------------------------------------------------------------------- #
# The CLI: it must refuse its own destructive flags by name.
# --------------------------------------------------------------------------- #

def _cli():
    tools = Path(__file__).resolve().parent.parent / "tools"
    sys.path.insert(0, str(tools))
    import cache_eviction_census

    return cache_eviction_census


@pytest.mark.parametrize("flag", ["--execute", "--sweep", "--purge", "--evict"])
def test_the_cli_refuses_a_destructive_flag_by_name(capsys, flag):
    """A missing flag reads as a typo; a REFUSED flag reads as the gate it is."""
    cli = _cli()

    code = cli.main(["--graph-uri", "docker://h:1/g", "--graph", "g", flag])

    assert code == 3
    err = capsys.readouterr().err
    assert "REFUSED" in err
    assert "#10593" in err and "#7879" in err


def test_the_cli_refuses_a_graph_that_disagrees_with_its_uri(capsys):
    code = _cli().main(["--graph-uri", "docker://h:1/one", "--graph", "other"])

    assert code == 1
    assert "disagrees" in capsys.readouterr().err


@pytest.mark.parametrize("uri", ["", "localhost", "localhost:6379", "docker://:6379/g"])
def test_the_cli_refuses_a_uri_it_cannot_read(uri, capsys):
    code = _cli().main(["--graph-uri", uri, "--graph", "g"])

    assert code == 1


# --------------------------------------------------------------------------- #
# ⛔ THE P0: the label is interpolated into Cypher, and it comes from the graph.
# --------------------------------------------------------------------------- #

def test_a_label_that_tries_to_delete_is_quoted_not_executed():
    """A label reaches Cypher by interpolation and comes from the graph's schema.

    `tortoise/graph_key_restore.py` names this shape verbatim and ships the one
    quoting rule; `tools/dod_5090_probe.py` (a sibling census) imports it. Without
    it, a label named ``X) DETACH DELETE (n) //`` turns a COUNT into a delete — on
    the tool whose entire contract is "nothing here deletes anything".
    """
    hostile = "X) DETACH DELETE (n) //"
    g = _FakeGraph({hostile: 0})

    counts = resident_counts(g)

    assert counts[hostile] == 0
    assert g.mutated == [], f"hostile label reached Cypher unquoted: {g.statements}"
    # The identifier is backtick-quoted, with the interior quote escaped — so the
    # statement stays ONE MATCH and the injected clause is data.
    assert any("`X) DETACH DELETE (n) //`" in s for s in g.statements), g.statements


def test_a_benign_label_is_not_needlessly_quoted():
    """"Bare when bare" — the quoting rule must not rewrite every label."""
    g = _FakeGraph({"Point": 3})

    resident_counts(g)

    assert "MATCH (n:Point) RETURN count(n)" in g.statements


# --------------------------------------------------------------------------- #
# `proves()` — every relaxed form is a licence to sweep unmeasured nodes.
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("measured,total,expected", [
    (0, 0, False),      # a census of nothing is not a proof
    (0, -5, False),     # nonsense must not prove
    (500, 100, False),  # MORE than the population is a DIFFERENT population
    (100, 100, True),   # equal and non-empty is the only proof
    (0, 100, False),    # a clean verdict over nothing still proves nothing
])
def test_proves_requires_equality_over_a_non_empty_population(measured, total, expected):
    report = ParityReport(instrument="tools/turn_parity_census.py", verdict="CLEAN",
                          measured_nodes=measured, total_nodes=total,
                          graph_name="tortoise")

    assert report.proves() is expected


def test_a_zero_population_report_cannot_flip_the_gate_open():
    """0/0 CLEAN would flip REFUSED→PLAN (exit 3→2) with nothing behind it."""
    g = _FakeGraph({"Point": 100})
    c = _FakeClient(used=900, maxmemory=1_000)
    empty = ParityReport(instrument="tools/turn_parity_census.py", verdict="CLEAN",
                         measured_nodes=0, total_nodes=0, graph_name="tortoise")

    result = assess(c, g, "tortoise", ceiling_bytes=100,
                    parity_reports={"tools/turn_parity_census.py": empty})

    assert result.verdict == "REFUSED"


# --------------------------------------------------------------------------- #
# The report shape must be the instrument's REAL one, not a convenient invention.
# --------------------------------------------------------------------------- #

def _real_shape_report(**overrides):
    """A report in `tools/turn_parity_census.py`'s own shape (census()'s return).

    Keys copied from that function's return dict: `instrument` (REQUIRED by
    `load_parity_report`), `graph` (REQUIRED too — the graph the GRAPH leg read, which
    the consumer compares against the graph being measured), `population.episodic_graph_nodes`
    (the DELETE SET), `dry_run.would_delete_turn_nodes` / `held_back_turn_nodes`, and a
    top-level `verdict`. There is no `totals` and no `counts` — a loader reading those
    cannot ever open the gate, which is why the real shape is pinned here.
    """
    payload = {
        "instrument": PARITY_INSTRUMENTS["turn"],
        "graph": "tortoise",
        "verdict": "CLEAN",
        "population": {"episodic_graph_nodes": 120, "turn_shaped_nodes": 120},
        "dry_run": {"would_delete_turn_nodes": 120, "held_back_turn_nodes": 0},
    }
    payload.update(overrides)
    return payload


def test_the_real_instrument_shape_is_read_correctly(tmp_path):
    import json

    path = tmp_path / "turns.json"
    path.write_text(json.dumps(_real_shape_report()), encoding="utf-8")

    report = load_parity_report(str(path), graph_name="tortoise")

    assert (report.measured_nodes, report.total_nodes) == (120, 120)
    assert report.proves() is True


def test_a_real_report_with_held_back_nodes_does_not_prove_the_population(tmp_path):
    """`held_back` = turn nodes the census could NOT place. CLEAN does not cover them."""
    import json

    path = tmp_path / "turns.json"
    path.write_text(
        json.dumps(_real_shape_report(
            **{"dry_run": {"would_delete_turn_nodes": 110,
                           "held_back_turn_nodes": 10}})),
        encoding="utf-8",
    )

    report = load_parity_report(str(path), graph_name="tortoise")

    assert report.proves() is False


def test_a_report_from_an_unregistered_instrument_is_refused(tmp_path):
    import json

    path = tmp_path / "x.json"
    path.write_text(json.dumps({"instrument": "my-own-vibes", "verdict": "CLEAN"}),
                    encoding="utf-8")

    with pytest.raises(CacheReadError):
        load_parity_report(str(path), graph_name="tortoise")


def test_a_report_of_another_graph_is_refused(tmp_path):
    """A CLEAN census of graph A must not license evicting graph B."""
    import json

    path = tmp_path / "x.json"
    path.write_text(json.dumps(_real_shape_report(graph="other_graph")),
                    encoding="utf-8")

    with pytest.raises(CacheReadError):
        load_parity_report(str(path), graph_name="tortoise")


def test_a_report_carrying_no_graph_identity_is_REFUSED(tmp_path):
    """⛔ THE LOADER MUST NOT AUTHOR THE BINDING IT IS SUPPOSED TO CHECK.

    Measured before the fix (review P1 on PR #11004): a report with no `graph` field was
    ATTRIBUTED to the caller's own `graph_name`, so `mismatched` could never fire and
    `unbound` was unreachable from `main()` — the only path that produces a real report.
    The binding then proved exactly one thing: that the operator typed `--graph` and
    repeated it in `--parity-graph`. The instrument now emits `graph`
    (`turn_parity_census.census`), so a report without one is an artifact that cannot
    say what it measured.

    Mutation that goes RED here: deleting the `names no graph` refusal. NOT the
    `graph_name` alias (measured: this payload carries neither key, so the alias still
    yields None and the refusal fires — the ALIAS pin below owns that half), and NOT
    the return-expression fill-in (a no-op once the refusal has passed).
    """
    import json

    payload = _real_shape_report()
    payload.pop("graph")
    path = tmp_path / "x.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(CacheReadError) as excinfo:
        load_parity_report(str(path), graph_name="tortoise")

    assert "names no graph" in str(excinfo.value)


def test_a_null_graph_identity_is_refused_like_a_missing_one(tmp_path):
    """A field present-but-null is the same absence, and must not be filled in."""
    import json

    path = tmp_path / "x.json"
    path.write_text(json.dumps(_real_shape_report(graph=None)), encoding="utf-8")

    with pytest.raises(CacheReadError) as excinfo:
        load_parity_report(str(path), graph_name="tortoise")

    assert "names no graph" in str(excinfo.value)


def test_a_null_graph_is_not_rescued_by_the_graph_name_ALIAS(tmp_path):
    """⛔ The `graph_name` fallback re-opened the hole the refusal closes.

    Measured before the fix (review P2 on PR #11097): `payload.get("graph") or
    payload.get("graph_name")` resolved `{"graph": null, "graph_name": "tortoise"}` to
    `"tortoise"` BEFORE the refusal could see it, so an artifact with no identity was
    accepted on an operator-controlled field — and `proves()` returned True. Nothing
    emits `graph_name` (`census()` emits `graph`), so the alias was a dead pre-fix shim
    whose only live effect was keeping "the loader authors the identity" open.

    Mutation that goes RED here: restoring the `or payload.get("graph_name")` fallback.
    """
    import json

    path = tmp_path / "x.json"
    path.write_text(
        json.dumps(_real_shape_report(graph=None, graph_name="tortoise")),
        encoding="utf-8",
    )

    with pytest.raises(CacheReadError) as excinfo:
        load_parity_report(str(path), graph_name="tortoise")

    assert "names no graph" in str(excinfo.value)


def test_the_parity_graph_help_states_the_rule_that_is_enforced(capsys):
    """The flag's help is the only documentation read AT THE MOMENT OF CHOOSING it.

    Measured before the fix (review P2 on PR #11097): the help still said *"the
    instrument emits no graph identity"* and *"An operator ASSERTION"* — both false
    after the loader began refusing an unattributed report, and the two halves steering
    an operator toward the opposite of the new contract (one told them their report
    carries no identity, which since the fix is a REFUSAL, not a note).
    """
    with pytest.raises(SystemExit) as exc:
        _cli().main(["--help"])
    assert exc.value.code == 0
    text = capsys.readouterr().out
    at = text.index("  --parity-graph")
    rest = text[at + 1:]
    block = " ".join(rest[:rest.index("\n  --")].split())
    assert "emits no graph identity" not in block
    assert "ASSERTION" not in block
    assert "REFUSED" in block and "`graph`" in block
    # Both residuals must survive on this surface too (review P2, cycle 7): the
    # sibling tool stated the staleness clause and this one did not, so an operator
    # re-using a CLEAN census taken before a re-capture read a warning about foreign
    # instances only.
    assert "EARLIER" in block


def test_a_report_naming_NO_instrument_is_refused(tmp_path):
    """⛔ A nameless report was silently attributed to the `turn` population.

    Measured before the fix: `{"verdict": "CLEAN", "population":
    {"episodic_graph_nodes": 1000}, "dry_run": {"would_delete_turn_nodes": 1000}}`
    loaded with `proves() is True` and produced PLAN + `plan["evictable"]` naming 1000
    nodes — hand-written evidence opening the gate that a real report carrying an
    UNRECOGNISED instrument name is refused for. Provenance must not be a layer of
    interpretation on top of a claim.
    """
    import json

    path = tmp_path / "x.json"
    path.write_text(json.dumps({
        "verdict": "CLEAN",
        "population": {"episodic_graph_nodes": 1_000},
        "dry_run": {"would_delete_turn_nodes": 1_000},
    }), encoding="utf-8")

    with pytest.raises(CacheReadError) as excinfo:
        load_parity_report(str(path), graph_name="tortoise")

    assert "names no instrument" in str(excinfo.value)


def test_the_instrument_names_itself_so_the_honest_path_reopens(tmp_path):
    """The other half of refusing nameless reports: the REAL one carries the name.

    Without this, requiring `instrument` would be a gate that can never open — so the
    constant the instrument emits and the value the loader accepts are pinned to each
    other, not to a prose copy of the string.
    """
    from tools.turn_parity_census import INSTRUMENT

    assert INSTRUMENT in PARITY_INSTRUMENTS.values(), (INSTRUMENT, PARITY_INSTRUMENTS)

    import json

    path = tmp_path / "x.json"
    path.write_text(json.dumps(_real_shape_report()), encoding="utf-8")

    report = load_parity_report(str(path), graph_name="tortoise")

    assert report.instrument == INSTRUMENT
    assert report.proves() is True


def test_an_unbound_report_is_not_eligible_at_the_library_surface():
    """⛔ `graph_name=None` must demote exactly like a MISMATCH — it is the worse hole.

    `ParityReport`'s docstring says a binding is required *"because the alternative —
    no binding at all — lets a CLEAN census of graph A license evicting graph B"*, but
    `assess` only compared the binding WHEN ONE WAS PRESENT: a report built without one
    (which `load_parity_report`'s old default produced) was accepted and eligible
    without ever being compared to the graph being evicted. The CLI was safe only
    because `main()` requires `--parity-graph`; the library — where the module promises
    enforcement "not only at the CLI's flag surface" — was not.
    """
    g = _FakeGraph({"Point": 9_000, "GraphEvent": 1_000})
    c = _FakeClient(used=900, maxmemory=1_000)
    unbound = ParityReport(instrument=PARITY_INSTRUMENTS["turn"], verdict="CLEAN",
                           measured_nodes=1_000, total_nodes=1_000)

    result = assess(c, g, "tortoise", ceiling_bytes=100,
                    parity_reports={PARITY_INSTRUMENTS["turn"]: unbound})

    assert result.verdict == "REFUSED", result.verdict
    assert result.eligible_classes == ()
    assert "NO graph binding" in " ".join(result.problems), result.problems


def test_load_parity_report_REFUSES_to_produce_an_unbound_report(tmp_path):
    """The door itself: `graph_name` is required, so the library cannot mint one."""
    import json

    path = tmp_path / "x.json"
    path.write_text(json.dumps(_real_shape_report()), encoding="utf-8")

    with pytest.raises(CacheReadError) as excinfo:
        load_parity_report(str(path), graph_name="  ")

    assert "UNBOUND" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# The measurement's remaining edges.
# --------------------------------------------------------------------------- #

def test_maxmemory_zero_is_reported_as_unbounded_not_as_a_bound_of_zero():
    """`maxmemory 0` means UNLIMITED. A bound of 0 hides the one state worth naming."""
    c = _FakeClient(used=500, maxmemory=0)

    state = measure_state(c, "tortoise")

    assert state.bound_bytes is None
    assert state.over_ceiling is False
    assert "NO BOUND ANYWHERE" in state.ceiling_source


def test_the_meter_receives_the_client_not_a_graph_handle():
    """`GRAPH.MEMORY USAGE` needs `execute_command`; a Graph handle does not have it.

    Passing the handle made the field silently `None` forever — a documented reading
    that never exists is worse than an absent field, so the READING is asserted here,
    not the absence of one error word. ⛔ The old form asserted
    `not note.startswith("unavailable: AttributeError")`, which passed on BOTH shapes:
    the fake client refused `execute_command` (note: "the census must not touch"), and
    the pre-fix graph handle produced a different sentence ("'Graph' object has no
    attribute 'execute_command'") — neither starts with that prefix. A pin that cannot
    tell the two code shapes apart is decoration; this one asserts the value.
    """
    c = _FakeClient(used=500, maxmemory=10_000)

    state = measure_state(c, "tortoise")

    # Reverting `measure_state` to pass `graph` makes this `None` (a `_FakeGraph` has
    # no `execute_command`), so the test fails on the pre-fix code.
    assert state.graph_memory_usage_mb == 12.5, state.graph_memory_usage_note
    assert not (state.graph_memory_usage_note or "").startswith("unavailable"), (
        state.graph_memory_usage_note
    )
    assert ("execute_command", ("GRAPH.MEMORY", "USAGE", "tortoise",
                               "SAMPLES", 1000)) in c.calls


def test_the_keyspace_is_probed_by_name_never_scanned():
    """`_FakeClient.keys` raises; a scan here would fail the run (see the fake)."""
    c = _FakeClient(used=500, maxmemory=10_000,
                    keys=("tortoise", "telemetry{tortoise}"))

    state = measure_state(c, "tortoise")

    assert state.keys_per_graph == 2
    assert all(name != "keys" for name, _ in c.calls)


# --------------------------------------------------------------------------- #
# The CLI's refusal must hold on the command line a user actually types.
# --------------------------------------------------------------------------- #

def test_the_real_entry_point_refuses_a_destructive_flag():
    """⛔ The earlier test called `main([...])`; the shipped path is `main()`.

    The guard was gated on `argv is not None`, so on `__main__` it was DEAD: the flag
    reached argparse, printed `unrecognized arguments`, and exited 2 — this tool's own
    PLAN code. This test drives the real process.
    """
    import subprocess

    proc = subprocess.run(
        [sys.executable, "tools/cache_eviction_census.py",
         "--graph-uri", "docker://h:1/g", "--graph", "g", "--execute"],
        cwd=Path(__file__).resolve().parent.parent,
        capture_output=True, text=True, timeout=120,
    )

    assert proc.returncode == 3, (proc.returncode, proc.stdout, proc.stderr)
    assert "REFUSED --execute" in proc.stderr
    assert "unrecognized arguments" not in proc.stderr


def test_a_usage_error_exits_one_never_the_plan_code():
    """argparse's default exit 2 is this tool's PLAN code — a typo must not read as it."""
    import subprocess

    root = Path(__file__).resolve().parent.parent
    for argv in (
        ["--graph-uri", "docker://h:1/g"],                      # missing --graph
        ["--graph-uri", "docker://h:1/g", "--graph", "g", "--bogus"],
        ["--graph-uri", "docker://h:1/g", "--graph", "g",
         "--ceiling-bytes", "notanint"],
    ):
        proc = subprocess.run(
            [sys.executable, "tools/cache_eviction_census.py", *argv],
            cwd=root, capture_output=True, text=True, timeout=120,
        )
        assert proc.returncode == 1, (argv, proc.returncode, proc.stderr)


def test_a_parity_report_without_a_parity_graph_is_a_usage_error(tmp_path, capsys):
    path = tmp_path / "turns.json"
    path.write_text('{"verdict": "CLEAN"}', encoding="utf-8")

    code = _cli().main([
        "--graph-uri", "docker://h:1/g", "--graph", "g",
        "--parity-report", str(path),
    ])

    assert code == 1
    assert "--parity-graph" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# Round-2 findings — each one measured as a wrong outcome before it was fixed.
# --------------------------------------------------------------------------- #

def test_parity_graph_must_name_the_graph_being_measured(tmp_path, capsys):
    """A `--parity-graph` naming another graph made the whole binding vacuous.

    Measured before the fix: `--graph evict_probe --parity-report <CLEAN census of
    other_graph> --parity-graph other_graph` produced a PLAN for `evict_probe` on
    evidence about `other_graph`. Comparing the report against an operator-typed
    string only proves the operator typed the same string twice.

    This is the FLAG arm. The REPORT arm — the one that made the pre-fix outcome
    still reachable through the argv this pin requires — is
    `test_a_clean_census_of_another_graph_cannot_license_this_one`.
    """
    import json

    path = tmp_path / "turns.json"
    path.write_text(json.dumps(_real_shape_report(graph="other_graph")), encoding="utf-8")

    code = _cli().main([
        "--graph-uri", "docker://h:1/tortoise", "--graph", "tortoise",
        "--parity-report", str(path), "--parity-graph", "other_graph",
    ])

    assert code == 1
    assert "must be the graph being measured" in capsys.readouterr().err


def _turn_parity():
    tools = Path(__file__).resolve().parent.parent / "tools"
    sys.path.insert(0, str(tools))
    import turn_parity_census

    return turn_parity_census


def _clean_turn_census(graph_name: str) -> dict:
    """A CLEAN census produced by the REAL instrument, for *graph_name*.

    The two dictionaries are the shapes `read_graph_turns` / `read_record_turns` return
    (one turn, byte-identical on both sides), so `census()` reaches its CLEAN arm
    without a store — the artifact an operator would hand to `--parity-report`.
    """
    node = {
        "id": "s1_t0",
        "content": "[user] body of s1_t0",
        "speaker": "user",
        "status": "draft",
        "createdAt": "2026-10-25T00:00:00+00:00",
        "pointKind": "event",
        "embedding": None,
        "sessions": {"s1"},
    }
    row = {
        "node_key": "s1_t0",
        "content": "[user] body of s1_t0",
        "speaker": "user",
        "status": "draft",
        "created_at": "2026-10-25T00:00:00+00:00",
        "point_kind": "event",
        "is_episodic": True,
        "embedding": None,
    }
    return _turn_parity().census(
        {"nodes": {"s1_t0": node}},
        {"rows_by_key": {"s1_t0": row}, "non_turn_shaped": 0},
        sample=0, graph_name=graph_name)


def test_a_clean_census_of_another_graph_cannot_license_this_one(tmp_path, capsys):
    """⛔ THE HOLE THE ROUND-2 FIX LEFT OPEN (review P1 on PR #11004, measured).

    `--graph A --parity-report <CLEAN census of B> --parity-graph A` (the argv the
    round-2 fix REQUIRES — the flag must equal `--graph`) produced a PLAN for A on
    evidence about B, because `load_parity_report` filled A into a report carrying no
    graph identity. The two invocations need not even name the same instance: the census
    takes `--graph-uri`, and the report comes from a separate
    `turn_parity_census.py --uri …` run.

    The report here is built by the REAL instrument (`turn_parity_census.census`), not a
    hand-written dict, so the pin follows the artifact the operator actually hands over.

    Mutation that goes RED here: deleting the `reported_graph != graph_name` comparison
    (measured — the run then loads the FOREIGN report and refuses later for a different
    reason, so the error no longer names both graphs). NOT the fill-in: this report
    carries `graph: "other_graph"`, so a fill-in could not change its outcome.
    """
    import json

    foreign = _clean_turn_census("other_graph")
    assert foreign["graph"] == "other_graph"
    assert (foreign["verdict"],
            foreign["dry_run"]["would_delete_turn_nodes"],
            foreign["population"]["episodic_graph_nodes"]) == ("CLEAN", 1, 1), \
        foreign
    path = tmp_path / "turns.json"
    path.write_text(json.dumps(foreign), encoding="utf-8")

    code = _cli().main([
        "--graph-uri", "docker://h:1/tortoise", "--graph", "tortoise",
        "--parity-report", str(path), "--parity-graph", "tortoise",
    ])

    assert code == 3, (code, capsys.readouterr().err)
    err = capsys.readouterr().err
    assert "other_graph" in err and "tortoise" in err


def test_a_real_clean_census_of_THIS_graph_still_licenses_it(tmp_path):
    """The positive arm: the fix must not refuse the honest report.

    Without this, deleting the whole `graph` comparison would keep the refusal tests
    green — a refusal-only suite cannot tell "the gate works" from "the gate is shut".
    """
    import json

    path = tmp_path / "turns.json"
    path.write_text(json.dumps(_clean_turn_census("tortoise")), encoding="utf-8")

    report = load_parity_report(str(path), graph_name="tortoise")

    assert report.graph_name == "tortoise"
    assert report.proves() is True


def test_an_unbounded_cache_is_not_reported_as_clean():
    """CLEAN/0 means "within its bound" — the one state with NO bound must not share it.

    A monitor keyed on exit 0 concluded the cache was bounded when nothing bounded it.
    """
    g = _FakeGraph({"Point": 20_000})
    c = _FakeClient(used=8_000_000, maxmemory=0)

    result = assess(c, g, "tortoise")

    assert result.verdict == "UNBOUNDED"
    assert result.state.bound_bytes is None
    assert "NO BOUND ANYWHERE" in result.state.ceiling_source


def test_a_report_claiming_more_nodes_than_the_graph_holds_does_not_prove():
    """A stale report (or one about another graph) must not size a sweep.

    Measured before the fix: a graph holding 0 nodes emitted a PLAN offering 120 turn
    nodes on a 120/120 report — a licence for 120 nodes in an empty graph, which is
    the number a sweeper would size itself from.
    """
    # ⚠️ The fixture makes the POPULATION statement over-count past the whole graph
    # (`turn`: 120 on a 5-node graph), because that is the only shape this guard can
    # reach: the trigger is `resident_population >= claim > node_total`, i.e. the CLAIM is
    # above every node the graph holds. ⛔ A widened PREDICATE cannot produce it (a
    # subgraph count never exceeds the total) and a modest row-multiplication does not
    # either (the claim stays under the total) — that class is covered solely by
    # `test_the_population_count_mirrors_the_instrument`, and
    # `test_a_row_multiplying_count_statement_is_a_documented_residual` pins what this
    # guard does NOT catch.
    # ⛔ The fixture MODELS READ SKEW, not a graph shape. `assess()` reads each
    # population's count and only THEN the graph total (that order is what keeps
    # additions between the two reads from demoting an honest report — #11726), so
    # `population > total` is a state the two reads can DISAGREE about, never one a
    # quiescent graph produces. That is exactly the failure this guard exists for: an
    # operator's record describing a graph that no longer exists.
    g = _FakeGraph({"Point": 5}, populations={"turn": 120})
    c = _FakeClient(used=900, maxmemory=1_000)
    stale = ParityReport(instrument="tools/turn_parity_census.py", verdict="CLEAN",
                         measured_nodes=120, total_nodes=120, graph_name="tortoise")

    result = assess(c, g, "tortoise", ceiling_bytes=100,
                    parity_reports={"tools/turn_parity_census.py": stale})

    assert result.verdict == "REFUSED"
    assert result.eligible_classes == ()
    assert "only 5" in " ".join(result.problems)


def test_a_label_ending_in_a_newline_is_refused_rather_than_miscounted():
    """The shared quoting rule's regex matches before a trailing newline, so it goes BARE.

    A bare `Point` + newline parses as label `Point` (measured), so two distinct labels
    would both be counted against `Point` — a wrong census number. The census refuses
    the reading rather than guessing an escape: a second quoting rule is exactly what
    the shared one exists to prevent.
    """
    g = _FakeGraph({"Point": 3, "Point\n": 4})

    with pytest.raises(CacheReadError):
        resident_counts(g)

    assert g.mutated == []


def test_an_unreachable_cache_refuses_rather_than_exiting_like_a_typo():
    """An unreachable store is a REFUSAL (3), not a flag mistake (1) with a traceback."""
    import subprocess

    proc = subprocess.run(
        [sys.executable, "tools/cache_eviction_census.py",
         "--graph-uri", "docker://127.0.0.1:1/nope", "--graph", "nope"],
        cwd=Path(__file__).resolve().parent.parent,
        capture_output=True, text=True, timeout=180,
    )

    assert proc.returncode == 3, (proc.returncode, proc.stdout, proc.stderr)
    assert "could not be reached" in proc.stderr
    assert "Traceback" not in proc.stderr


def test_exit_two_licenses_only_the_listed_populations():
    """The plan must name what may NOT go, so exit 2 cannot read as a blanket licence."""
    g = _FakeGraph({"Point": 10_000, "Object": 900}, populations={"turn": 2_000})
    c = _FakeClient(used=900, maxmemory=1_000)
    report = ParityReport(instrument="tools/turn_parity_census.py", verdict="CLEAN",
                          measured_nodes=2_000, total_nodes=2_000,
                          graph_name="tortoise")

    result = assess(c, g, "tortoise", ceiling_bytes=100,
                    parity_reports={"tools/turn_parity_census.py": report})

    assert result.verdict == "PLAN"
    assert result.plan["not_evictable"] == ["Point", "Object"]
    # ⛔ The LICENCE names the BARE population ("turn"), not the display label
    # ("turn (population)"): the executor's population gate compares for
    # equality, so a licence naming the display string refused the sweep it
    # had just licensed. `label` still carries the display form.
    assert [e["population"] for e in result.plan["evictable"]] == ["turn"]


# --------------------------------------------------------------------------- #
# Round-3 findings.
# --------------------------------------------------------------------------- #

def test_a_report_bound_to_another_graph_is_demoted_by_the_library_too():
    """The CLI's flag check is not enough — a future sweeper consumes THIS API."""
    g = _FakeGraph({"Point": 10_000})
    c = _FakeClient(used=900, maxmemory=1_000)
    elsewhere = ParityReport(instrument="tools/turn_parity_census.py", verdict="CLEAN",
                             measured_nodes=2_000, total_nodes=2_000,
                             graph_name="some_other_graph")

    result = assess(c, g, "tortoise", ceiling_bytes=100,
                    parity_reports={"tools/turn_parity_census.py": elsewhere})

    assert result.verdict == "REFUSED"
    assert result.eligible_classes == ()
    assert "different graph" in " ".join(result.problems)


def test_over_ceiling_is_null_not_false_when_nothing_bounds_the_cache():
    """A monitor reading the JSON must not read `false` as "bounded and fine"."""
    g = _FakeGraph({"Point": 5})
    c = _FakeClient(used=8_000_000, maxmemory=0)

    result = assess(c, g, "tortoise")

    assert result.as_dict()["state"]["over_ceiling"] is None


def test_unlabelled_nodes_are_measured_not_derived_from_a_residual():
    """Multi-label nodes make `total - sum(counts)` go NEGATIVE.

    Measured before the fix: a graph with 10,000 `Point` of which 1,000 were also
    `Object` produced `<unlabelled>: -1000` — a negative node count in the artifact an
    owner decides on. Labels do not partition the node set, so the residual is not a
    count of anything.
    """
    g = _FakeGraph({"Point": 10_000, "Object": 2_000}, unlabelled=4)

    counts = resident_counts(g)

    assert counts["<unlabelled>"] == 4
    assert all(v >= 0 for v in counts.values()), counts
    assert not any("RETURN count(n)" in s and s.strip() == "MATCH (n) RETURN"
                   for s in g.statements), (
        "the residual total query must not be used to derive an unlabelled count"
    )


def test_a_report_that_is_not_utf8_is_a_refusal_not_a_traceback(tmp_path):
    path = tmp_path / "turns.json"
    path.write_bytes(b'{"verdict": "CLEAN"}\xff')

    with pytest.raises(CacheReadError):
        load_parity_report(str(path), graph_name="tortoise")


# --------------------------------------------------------------------------- #
# Round-4 findings.
# --------------------------------------------------------------------------- #

def test_the_size_cross_check_uses_the_true_total_not_the_label_sum():
    """Multi-label nodes are counted once per label, so the label sum OVER-states.

    Measured before the fix: 10,000 nodes of which 2,000 carry a second label gave
    `resident_total = 12,000`, so an 11,000/11,000 CLEAN report was accepted and the
    plan offered 11,000 nodes for a 10,000-node graph — the exact over-offer the
    cross-check exists to prevent.
    """
    # 10,000 real nodes: 8,000 `Point`-only + 2,000 `Point:Object` (so the label sum is
    # 12,000 while the graph holds 10,000).
    # ⚠️ Population statement over-counts as well (11,000), so the label-sum regression
    # is measured HERE rather than caught by the population bound one gate earlier.
    g = _FakeGraph({"Point": 10_000, "Object": 2_000}, populations={"turn": 11_000})
    g.true_total = 10_000
    c = _FakeClient(used=900, maxmemory=1_000)
    inflated = ParityReport(instrument="tools/turn_parity_census.py", verdict="CLEAN",
                            measured_nodes=11_000, total_nodes=11_000,
                            graph_name="tortoise")

    result = assess(c, g, "tortoise", ceiling_bytes=100,
                    parity_reports={"tools/turn_parity_census.py": inflated})

    assert result.verdict == "REFUSED"
    assert "only 10000" in " ".join(result.problems)


def test_two_reports_for_one_instrument_are_a_usage_error_not_last_wins(tmp_path, capsys):
    """Last-wins made the verdict depend on argv order.

    Measured before the fix: `--parity-report refused.json --parity-report clean.json`
    exited 2 with a licence; the same two files reversed exited 3.
    """
    import json

    refused = tmp_path / "refused.json"
    refused.write_text(json.dumps(_real_shape_report(verdict="REFUSED")), encoding="utf-8")
    clean = tmp_path / "clean.json"
    clean.write_text(json.dumps(_real_shape_report()), encoding="utf-8")

    code = _cli().main([
        "--graph-uri", "docker://h:1/tortoise", "--graph", "tortoise",
        "--parity-report", str(refused), "--parity-report", str(clean),
        "--parity-graph", "tortoise",
    ])

    assert code == 1
    assert "argument order" in capsys.readouterr().err


def test_the_ceiling_source_names_the_flag_when_the_flag_set_it(monkeypatch):
    """The field exists to answer "where did this bound come from" — so it must."""
    monkeypatch.delenv(CACHE_CEILING_ENV, raising=False)

    state = measure_state(_FakeClient(used=500, maxmemory=10_000), "tortoise",
                          ceiling_bytes=100)

    assert state.ceiling_source == "declared (--ceiling-bytes)"
    assert CACHE_CEILING_ENV not in state.ceiling_source


@pytest.mark.parametrize("bad", [0, -1])
@pytest.mark.parametrize("via", ["flag", "env"])
def test_a_non_positive_ceiling_is_refused(bad, via, monkeypatch):
    """`--ceiling-bytes -1` emitted the LICENCE code on a mistyped flag."""
    monkeypatch.delenv(CACHE_CEILING_ENV, raising=False)
    kwargs = {"ceiling_bytes": bad} if via == "flag" else {}
    if via == "env":
        monkeypatch.setenv(CACHE_CEILING_ENV, str(bad))

    with pytest.raises(CacheReadError):
        measure_state(_FakeClient(), "tortoise", **kwargs)


def test_refused_covers_unbounded_and_an_unbounded_plan_offers_nothing():
    """`if not census.refused: execute(plan['evictable'])` must not sweep an unbounded cache."""
    g = _FakeGraph({"Point": 20_000})
    c = _FakeClient(used=8_000_000, maxmemory=0)
    report = ParityReport(instrument="tools/turn_parity_census.py", verdict="CLEAN",
                          measured_nodes=20_000, total_nodes=20_000,
                          graph_name="tortoise")

    result = assess(c, g, "tortoise",
                    parity_reports={"tools/turn_parity_census.py": report})

    assert result.verdict == "UNBOUNDED"
    assert result.refused is True
    assert result.plan["evictable"] == []


# --------------------------------------------------------------------------- #
# Round-6 findings — the documentation an operator actually types.
# --------------------------------------------------------------------------- #

ROOT = Path(__file__).resolve().parent.parent


def _docstring_of(relpath: str) -> str:
    import ast

    return ast.get_docstring(ast.parse((ROOT / relpath).read_text(encoding="utf-8"))) or ""


def _documented_commands(doc: str, tool: str) -> list[str]:
    """Every `uv run python <tool> …` block in `doc`, with `\\` continuations joined."""
    commands: list[str] = []
    current: str | None = None
    for raw in doc.splitlines():
        text = raw.strip()
        if current is None:
            if f"uv run python {tool}" not in text:
                continue
            current = text.split(f"uv run python {tool}", 1)[1].strip()
        elif text:
            current = f"{current} {text}"
        if current.endswith("\\"):
            current = current[:-1].rstrip()
            continue
        commands.append(current)
        current = None
    return commands


def _flags(command: str) -> set[str]:
    return set(re.findall(r"(?<![\w-])--[A-Za-z][\w-]*", command))


def _accepted_flags(relpath: str) -> set[str]:
    """The tool's OWN `add_argument` option strings — not its prose."""
    import ast

    tree = ast.parse((ROOT / relpath).read_text(encoding="utf-8"))
    flags: set[str] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_argument" and node.args):
            continue
        first = node.args[0]
        if (isinstance(first, ast.Constant) and isinstance(first.value, str)
                and first.value.startswith("--")):
            flags.add(first.value)
    return flags


def test_the_documented_way_to_produce_the_parity_report_uses_real_flags():
    """The docstring is this tool's ONLY operator manual, so its commands must run.

    It documented `tools/turn_parity_census.py --out /tmp/turns.json`, but that
    instrument has no `--out` — its report flag is `--json` (:1972), and `--uri` /
    `--db-path` is a required mutually-exclusive group (:1945). The documented
    invocation therefore exited 1 with `unrecognized arguments: --out` and never
    produced the report this tool consumes: the integration the census exists to
    enable was documented as impossible.
    """
    doc = _docstring_of("tools/cache_eviction_census.py")
    commands = _documented_commands(doc, "tools/turn_parity_census.py")

    assert commands, "the docstring must document how to produce the report it consumes"
    accepted = _accepted_flags("tools/turn_parity_census.py")
    assert "--json" in accepted, "the report comes from --json"

    unknown = sorted({f for c in commands for f in _flags(c)} - accepted)
    assert not unknown, (
        f"the documented invocation(s) {commands} name flag(s) the instrument does "
        f"not accept: {unknown} — accepted: {sorted(accepted)}"
    )


def test_the_documented_parity_example_supplies_the_graph_binding_the_code_requires(
        tmp_path, capsys, monkeypatch):
    """The documented example omitted `--parity-graph`, which the code REQUIRES.

    A report without the graph it was measured on is a vacuous binding (the whole
    round-2 P1), so the example taught an invocation the tool refuses. This runs the
    documented command instead of reading it: the report written here is one the
    loader MUST refuse (an unregistered instrument), so the run stops at its REFUSAL
    boundary — exit 3 out of `load_parity_report` — before any connection, and it
    never touches a cache.

    ⛔ THE REPORT MUST BE UNREADABLE FOR THAT TO BE TRUE, and `_connection` is
    poisoned below so this cannot regress silently. An earlier revision wrote a
    perfectly READABLE report and then relied on nothing listening on the documented
    `localhost:6379`, which made the asserted exit 3 an accident of the host: on the
    dev lane `AGENTS.md` documents (a FalkorDB on that port with that password) the
    run reaches `assess()` and returns the verdict of the MEASURED cache — UNBOUNDED
    (4) for a default `maxmemory 0`, or CLEAN (0) — so the pin went red on the
    documented setup and green only because the port was closed. It is the same
    "reads as verification while verifying nothing" class as the dangling pin
    citation fixed one commit earlier.
    """
    import shlex

    doc = _docstring_of("tools/cache_eviction_census.py")
    commands = [c for c in _documented_commands(doc, "tools/cache_eviction_census.py")
                if "--parity-report" in c]
    assert commands, "the docstring must document the parity-report invocation"

    report = tmp_path / "turns.json"
    report.write_text('{"instrument": "not-a-registered-instrument", "verdict": "CLEAN"}',
                      encoding="utf-8")

    cli = _cli()
    attempted: list[str] = []

    def _must_not_connect(*args, **kwargs):
        attempted.append("connection")
        raise AssertionError(
            "the documented command opened a connection before refusing — the report "
            "must stop the run at the REFUSAL boundary"
        )

    monkeypatch.setattr(cli, "_connection", _must_not_connect)

    for command in commands:
        argv = shlex.split(command)
        assert "--parity-graph" in argv, (
            f"the documented command {command!r} omits --parity-graph, which the CLI "
            f"refuses without (a CLEAN census of one graph would license evicting another)"
        )
        # Point the report at a real file so the run reaches the report LOADER, not an
        # IO error — the loader refuses it, and the connection is never attempted.
        argv[argv.index("--parity-report") + 1] = str(report)

        code = cli.main(argv)
        err = capsys.readouterr().err

        assert "requires --parity-graph" not in err, (command, err)
        assert "not a registered parity instrument" in err, (command, err)
        # A run that reached the cache would print the measurement and a verdict line;
        # this one must stop at the report, so neither may appear.
        assert "verdict:" not in err, (command, err)
        assert "resident" not in err, (command, err)
        assert not attempted, (command, attempted)
        assert code == 3, (command, code, err)


def test_the_documented_example_refuses_before_a_reachable_cache_is_measured(
        tmp_path, capsys, monkeypatch):
    """⛔ The pin must not depend on the HOST: a cache that ANSWERS cannot change it.

    Measured on the pre-fix test — it wrote a READABLE report and relied on nothing
    listening on the documented `localhost:6379`, so the same assertion gave a
    different answer wherever a FalkorDB answered: `UNBOUNDED` (exit 4) for the
    container default `maxmemory 0`, `CLEAN` (exit 0) below a declared bound. It was
    green in CI only because #6673 provisions the service on an EPHEMERAL port, i.e.
    the pin held for a reason unrelated to what it claims to prove, and red on the dev
    lane `AGENTS.md` documents. Here a cache IS reachable (the suite's own fakes) and
    the loader refusal must still come first: exit 3 naming the report, with no
    connection made and no statement issued against the graph.
    """
    import shlex

    doc = _docstring_of("tools/cache_eviction_census.py")
    command = next(c for c in _documented_commands(doc, "tools/cache_eviction_census.py")
                   if "--parity-report" in c)

    report = tmp_path / "turns.json"
    report.write_text('{"instrument": "not-a-registered-instrument", "verdict": "CLEAN"}',
                      encoding="utf-8")

    cli = _cli()
    graph = _FakeGraph({"Point": 20_000})
    reached: list[str] = []

    def _a_reachable_cache(*args, **kwargs):
        # A cache that answers — the state CI does not have and the dev lane does.
        reached.append("connected")
        return _FakeClient(used=8_000_000, maxmemory=0), graph

    monkeypatch.setattr(cli, "_connection", _a_reachable_cache)

    argv = shlex.split(command)
    argv[argv.index("--parity-report") + 1] = str(report)

    code = cli.main(argv)
    err = capsys.readouterr().err

    assert "not a registered parity instrument" in err, err
    assert code == 3, (code, err)
    assert not reached, (reached, "a refused report must refuse BEFORE the cache is read")
    assert not graph.statements, graph.statements


def test_a_readable_documented_report_DOES_reach_the_connection(tmp_path, capsys,
                                                                monkeypatch):
    """Non-vacuity for the poisoned `_connection` above: a READABLE report gets past it.

    The two tests above prove a refused report never connects. Neither can tell that
    apart from "this CLI never connects on ANY input", and that is exactly the shape
    the round-6 P2 was: an assertion that held for a reason unrelated to the claim.
    Same documented argv, a report the loader ACCEPTS, and the fakes behind
    `_connection` — so the run must reach the documented target (host, port, password,
    graph) and return a MEASURED verdict (CLEAN: 900 B resident against a 1000 B
    `maxmemory`) with exit 0, still without opening a socket.
    """
    import json
    import shlex

    doc = _docstring_of("tools/cache_eviction_census.py")
    command = next(c for c in _documented_commands(doc, "tools/cache_eviction_census.py")
                   if "--parity-report" in c)

    report = tmp_path / "turns.json"
    report.write_text(json.dumps(_real_shape_report()), encoding="utf-8")

    cli = _cli()
    seen: list[tuple] = []

    def _a_cache_that_answers(endpoint):
        seen.append(endpoint)
        return _FakeClient(used=900, maxmemory=1_000), _FakeGraph({"Point": 120})

    monkeypatch.setattr(cli, "_connection", _a_cache_that_answers)

    argv = shlex.split(command)
    argv[argv.index("--parity-report") + 1] = str(report)

    code = cli.main(argv)
    err = capsys.readouterr().err

    assert len(seen) == 1, (command, seen)
    assert (seen[0].host, seen[0].port, seen[0].password,
            seen[0].graph_name) == ("localhost", 6379, "falkordb", "tortoise"), \
        (command, seen)
    assert "verdict: CLEAN" in err, (command, err)
    assert code == 0, (command, code, err)


# --------------------------------------------------------------------------- #
# #8118 / #3065: the census's OWN clients carry the product's READ BOUND.
# --------------------------------------------------------------------------- #

def _capture_client_kwargs(monkeypatch, cli,
                           uri="docker://:falkordb@localhost:6379/tortoise") \
        -> dict[str, dict]:
    """Drive `_connection` with stand-in constructors and return their kwargs."""
    import falkordb
    import redis

    from tortoise.projection import resolve_db_endpoint

    captured: dict[str, dict] = {}

    class _RecordingRedis:
        def __init__(self, **kwargs):
            captured["redis"] = kwargs

    class _RecordingFalkorDB:
        def __init__(self, **kwargs):
            captured["falkordb"] = kwargs

        def select_graph(self, name):
            captured["graph"] = {"name": name}
            return object()

    monkeypatch.setattr(redis, "Redis", _RecordingRedis)
    monkeypatch.setattr(falkordb, "FalkorDB", _RecordingFalkorDB)

    endpoint = resolve_db_endpoint(uri)
    client, graph = cli._connection(endpoint)

    assert captured["graph"] == {"name": endpoint.graph_name}, (
        "the handle must name the graph")
    assert graph is not None
    assert client is not None
    return captured


def test_both_census_clients_carry_the_products_read_bound(monkeypatch):
    """An UNBOUNDED client parks this census in ``recv()`` forever — it does not read slowly.

    ``falkordb.FalkorDB`` defaults ``socket_timeout=None`` and hands that to
    redis-py EXPLICITLY, so the handle this tool measures through is unbounded
    unless it is given a bound. Against a stalled engine (the #2969 defrag loop,
    the #2838 MERGE lock) the process then stays alive at 0% CPU and prints
    nothing — which a caller deciding an EVICTION from this census cannot tell
    apart from "still measuring" (#3065, #8118). The pre-fix code passed NO
    bound to ``FalkorDB(...)`` at all, so the assertions below fail on it.
    """
    from tortoise.projection import _bounded_server_socket_kwargs

    monkeypatch.delenv("TORTOISE_DB_SOCKET_TIMEOUT", raising=False)
    monkeypatch.delenv("TORTOISE_DB_SOCKET_CONNECT_TIMEOUT", raising=False)

    captured = _capture_client_kwargs(monkeypatch, _cli())
    expected = _bounded_server_socket_kwargs()

    for name in ("redis", "falkordb"):
        kwargs = captured[name]
        read_to = kwargs["socket_timeout"]
        assert isinstance(read_to, (int, float)) and read_to > 0, (
            f"the {name} client was constructed with NO read bound "
            f"(socket_timeout={read_to!r}) — an unbounded recv() that a stalled "
            f"engine parks forever (#3065/#8118)")
        assert kwargs["socket_connect_timeout"] > 0, name
        # The bound comes from the ONE resolver, not from a copy of its defaults.
        assert read_to == expected["socket_timeout"], name
        assert kwargs["socket_connect_timeout"] == expected["socket_connect_timeout"], name


def test_the_operators_socket_knob_reaches_BOTH_census_clients(monkeypatch):
    """Bounded-but-knob-blind literals are the same defect one step milder (#3065).

    The pre-fix code spelled ``socket_connect_timeout=10, socket_timeout=60`` at
    the ``redis.Redis`` site, so an operator who set
    ``TORTOISE_DB_SOCKET_TIMEOUT`` for a loaded server got 60s anyway, and the
    ``FalkorDB`` site ignored the knob entirely. Both clients must resolve it.
    """
    monkeypatch.setenv("TORTOISE_DB_SOCKET_TIMEOUT", "3")
    monkeypatch.delenv("TORTOISE_DB_SOCKET_CONNECT_TIMEOUT", raising=False)

    captured = _capture_client_kwargs(monkeypatch, _cli())

    for name in ("redis", "falkordb"):
        assert captured[name]["socket_timeout"] == 3.0, (
            f"the operator's TORTOISE_DB_SOCKET_TIMEOUT did not reach the {name} "
            f"client: {captured[name].get('socket_timeout')!r}")


# --------------------------------------------------------------------------- #
# Round-7 findings — GRAPH.QUERY MINTS, so "read-only by construction" needs the
# VERB and not only the statement text, and the URI derivation is the canonical one.
# --------------------------------------------------------------------------- #

def test_the_read_only_verb_is_the_only_verb_the_census_issues():
    """⛔ `graph.query` is GRAPH.QUERY, which CREATES the graph when it is missing.

    The statement text of a count is innocent; the verb carrying it is not. Measured
    in this repo three times (`tools/engine_probe.py`, `tools/backfill_graph_turns.py`,
    `tools/edge_census.py` — the last two ship `_assert_graph_exists` for it), so a
    census whose contract is "changes nothing" must read through GRAPH.RO_QUERY.

    Non-vacuity: `_FakeGraph.query` RAISES, so the pre-fix code (all four counts through
    `query`) fails here on the first statement rather than passing silently.
    """
    g = _FakeGraph({"Point": 7, "GraphEvent": 2}, unlabelled=1)

    counts = resident_counts(g)
    total = node_total(g)

    assert counts == {"Point": 7, "GraphEvent": 2, "<unlabelled>": 1}
    assert total == 10
    assert {verb for verb, _statement in g.queries} == {"ro_query"}, g.queries
    # The four reads really were issued — a fake that recorded nothing would satisfy
    # a verb-only assertion vacuously.
    assert len(g.queries) == 5, g.queries  # labels + 2 label counts + unlabelled + total
    assert g.mutated == [], g.statements


def test_a_handle_with_no_read_only_verb_is_refused_never_measured_with_query():
    """⛔ NO FALLBACK. A handle that cannot read is a REFUSAL, not a reason to mint.

    A duck-typed stub (or a guard proxy that lost the verb) is the handle most likely
    to lack `ro_query`, and "fall back to `query` so we can still measure" is exactly
    the fail-open the read-only contract exists to close.
    """
    class _QueryOnly:
        def __init__(self):
            self.calls: list[str] = []

        def query(self, statement, params=None):
            self.calls.append(statement)
            raise AssertionError(
                "the census fell back to GRAPH.QUERY, which CREATES a missing graph"
            )

    handle = _QueryOnly()

    with pytest.raises(CacheReadError) as excinfo:
        resident_counts(handle)

    assert "ro_query" in str(excinfo.value), str(excinfo.value)
    assert handle.calls == [], handle.calls


def test_the_cli_refuses_a_graph_that_is_not_on_the_instance(capsys, monkeypatch):
    """⛔ A typo'd `--graph` must not be the thing that CREATES the graph.

    `--graph` / URI equality is not evidence the graph exists: both halves can carry
    the same typo, and a graph can have been dropped. Reading it would materialise an
    empty keyspace and then report a verdict indistinguishable from an empty cache, so
    the census lists first (`GRAPH.LIST` creates nothing) and refuses — exit 3, a
    statement about the store rather than a flag mistake, and NO statement at all
    against the graph.
    """
    cli = _cli()
    graph = _FakeGraph({"Point": 10})

    monkeypatch.setattr(
        cli, "_connection",
        lambda endpoint: (_FakeClient(used=900, maxmemory=1_000,
                                      graphs=("some_other_graph",)), graph),
    )

    code = cli.main(["--graph-uri", "docker://h:1/tortoise", "--graph", "tortoise"])
    err = capsys.readouterr().err

    assert code == 3, err
    assert "does not exist" in err, err
    assert "CREATES" in err, err
    assert graph.queries == [], graph.queries
    assert "verdict:" not in err, err


def test_an_unreadable_graph_list_is_a_refusal_not_a_licence_to_measure(capsys,
                                                                       monkeypatch):
    """⛔ FAIL CLOSED. "I could not list the graphs" is not "the graph is there".

    Proceeding on an unreadable list would measure through the one verb that can mint
    a missing graph, so an unreadable list is a refusal (3) and no statement is issued.
    """
    cli = _cli()
    graph = _FakeGraph({"Point": 10})

    class _BlindClient(_FakeClient):
        def execute_command(self, *args, **kwargs):
            if args and str(args[0]).upper() == "GRAPH.LIST":
                raise RuntimeError("NOPERM this user has no permissions to run GRAPH.LIST")
            return super().execute_command(*args, **kwargs)

    monkeypatch.setattr(
        cli, "_connection",
        lambda endpoint: (_BlindClient(used=900, maxmemory=1_000), graph),
    )

    code = cli.main(["--graph-uri", "docker://h:1/tortoise", "--graph", "tortoise"])
    err = capsys.readouterr().err

    assert code == 3, err
    assert "graph list could not be read" in err, err
    assert "NOPERM" in err, err
    assert graph.queries == [], graph.queries


def test_every_existing_cache_in_place_still_reaches_the_measurement(capsys,
                                                                    monkeypatch):
    """Non-vacuity for the two refusals above: an EXISTING graph still measures.

    Without this, "the CLI refuses" would be indistinguishable from "this CLI never
    gets past the existence check", which is the class of pin that holds for a reason
    unrelated to its claim.
    """
    cli = _cli()
    graph = _FakeGraph({"Point": 10})
    monkeypatch.setattr(
        cli, "_connection",
        lambda endpoint: (_FakeClient(used=900, maxmemory=1_000,
                                      graphs=("tortoise",)), graph),
    )

    code = cli.main(["--graph-uri", "docker://h:1/tortoise", "--graph", "tortoise"])
    err = capsys.readouterr().err

    assert code == 0, err
    assert "verdict: CLEAN" in err, err
    assert graph.queries, "the graph must actually be read once it exists"


def test_the_uri_is_resolved_by_the_repos_canonical_derivation():
    """⛔ NOT A SECOND PARSER: `username` and `ssl` were DROPPED by the hand-rolled one.

    Its consequences were a `rediss://` endpoint rejected as a usage error (`rediss`
    parsed as the host) and an ACL user silently discarded — on the one tool whose job
    is to name the right cache. `tortoise.projection.resolve_db_endpoint` is THE
    canonical derivation (`FalkorProjection.from_uri` and `backup._bgsave` call it), so
    the census must agree with it field for field.
    """
    from tortoise.projection import resolve_db_endpoint

    cli = _cli()
    uri = "rediss://alice:s3cret@cache.example.com:6380/tortoise"

    endpoint = cli._endpoint(uri)

    assert endpoint == resolve_db_endpoint(uri)
    assert endpoint.host == "cache.example.com"
    assert endpoint.port == 6380
    assert endpoint.username == "alice"
    assert endpoint.password == "s3cret"
    assert endpoint.ssl is True
    assert endpoint.graph_name == "tortoise"


def test_both_clients_receive_the_username_and_tls_the_uri_named(monkeypatch):
    """The endpoint's `username`/`ssl` must reach BOTH clients, not just the parse."""
    captured = _capture_client_kwargs(
        monkeypatch, _cli(),
        uri="rediss://alice:s3cret@cache.example.com:6380/tortoise",
    )

    for name in ("redis", "falkordb"):
        assert captured[name]["username"] == "alice", (name, captured[name])
        assert captured[name]["password"] == "s3cret", (name, captured[name])
        assert captured[name]["ssl"] is True, (name, captured[name])


@pytest.mark.parametrize("uri", ["", "localhost", "localhost:6379",
                                 "docker://:6379/g", "docker://h:not-a-port/g",
                                 "docker://h:0/g"])
def test_a_uri_that_names_no_host_and_port_is_refused(uri, capsys):
    """The ONE local rule kept beside the canonical derivation is a REFUSAL.

    `resolve_db_endpoint` fills defaults (`localhost` / `16379`) for a URI that names
    neither, which for this tool means measuring an instance the operator never named.
    """
    code = _cli().main(["--graph-uri", uri, "--graph", "g"])

    assert code == 1


def test_a_uri_that_names_port_zero_refuses_instead_of_dialling_16379():
    """`0` is a NAMED port, and the canonical rule substitutes its default for it.

    ⛔ THE DEFAULT IS APPLIED INSIDE `resolve_db_endpoint` (`port=parsed.port or
    16379`), so a "did the operator name a port?" test made BEFORE the call is the
    wrong test: `0` is falsy, so `docker://h:0/g` passed the `port is None` guard and
    then dialled **16379** — the embedded lane's default, where a graph server
    genuinely listens (`AGENTS.md` / `tools/test_lane.py`). The operator named a
    different instance and the census would have reported on that one, silently, with
    a verdict indistinguishable from a correct reading. So the rule has to be checked
    against the port that will ACTUALLY be dialled.
    """
    cli = _cli()

    with pytest.raises(ValueError, match="16379"):
        cli._endpoint("docker://h:0/g")

    # Non-vacuity: the refusal is about the SUBSTITUTION, not about small ports —
    # a port that is really written, including the default one itself, still resolves.
    assert cli._endpoint("docker://h:6379/g").port == 6379
    assert cli._endpoint("docker://h:16379/g").port == 16379


def test_a_uri_that_names_port_zero_is_a_usage_error_before_any_socket(capsys,
                                                                      monkeypatch):
    """End-to-end: the refusal is exit 1 and touches no connection."""
    cli = _cli()
    monkeypatch.setattr(
        cli, "_connection",
        lambda endpoint: pytest.fail(
            f"connected to {endpoint.host}:{endpoint.port} for a URI that named 0"),
    )

    code = cli.main(["--graph-uri", "docker://localhost:0/tortoise",
                     "--graph", "tortoise"])
    err = capsys.readouterr().err

    assert code == 1, err
    assert "16379" in err, err
    assert "Traceback" not in err, err


def test_an_unwritable_out_path_is_a_usage_error_not_a_traceback(tmp_path, capsys,
                                                                 monkeypatch):
    """A bad `--out` escaped `main()` as a traceback; the IO path must match the loader.

    `load_parity_report` already turns an unreadable report into exit 1 rather than a
    traceback, and "a flag or IO mistake" is the tool's own definition of exit 1 — it
    has to be true for the WRITE path too.
    """
    cli = _cli()
    graph = _FakeGraph({"Point": 10})
    monkeypatch.setattr(
        cli, "_connection",
        lambda endpoint: (_FakeClient(used=900, maxmemory=1_000), graph),
    )

    target = tmp_path / "no-such-directory" / "census.json"

    code = cli.main(["--graph-uri", "docker://h:1/tortoise", "--graph", "tortoise",
                     "--out", str(target)])
    err = capsys.readouterr().err

    assert code == 1, err
    assert "--out" in err, err
    assert "Traceback" not in err, err


def test_the_cli_PRINTS_the_licence_on_a_CLEAN_verdict(monkeypatch, capsys, tmp_path):
    """⛔ `CLEAN` is the BYTE state, not "nothing to evict".

    The CLI returned on CLEAN before printing the licence it had just published, so an
    operator following the documented two-step (census, then the executor) read "nothing
    to evict" from step 1 and then watched thousands of nodes go in step 2. Nothing
    consumed that print: deleting the whole `LICENSED (byte-clean)` loop left the suite at
    248 passed. This asserts the line an operator actually reads.
    """
    cli = _cli()

    # Drive the CLI to a CLEAN verdict with a PROVEN population, without a store: the
    # census's own `assess` is replaced, and everything upstream of it is stubbed.
    class _Endpoint:
        host, port, username, password, ssl = "h", 1, None, None, False
        graph_name = "tortoise"

    state = CacheState(
        graph_name="tortoise", resident_bytes=500, maxmemory_bytes=10_000,
        maxmemory_policy="noeviction", ceiling_bytes=1_000, ceiling_source="declared",
        graph_key_ttl_seconds=-1, keys_per_graph=1,
        graph_memory_usage_mb=None, graph_memory_usage_note=None)
    census = Assessment(
        state=state, classes=(), verdict="CLEAN",
        plan={"evictable": [{"population": "turn", "nodes": 7,
                             "instrument": PARITY_INSTRUMENTS["turn"]}]})

    monkeypatch.setattr(cli, "_endpoint", lambda uri: _Endpoint())
    monkeypatch.setattr(cli, "_connection", lambda ep: (object(), object()))
    monkeypatch.setattr(cli, "_absent_graph_refusal", lambda client, name: None)
    monkeypatch.setattr(cli, "assess", lambda *a, **k: census)

    code = cli.main(["--graph-uri", "docker://h:1/tortoise", "--graph", "tortoise"])

    err = capsys.readouterr().err
    assert code == 0
    assert "LICENSED (byte-clean)" in err, err
    assert "turn" in err and "7 nodes" in err, err
    assert "BYTE" in err, err


# --------------------------------------------------------------------------- #
# Round-5 findings (#10976 owner residual, comment 6689914601).
# --------------------------------------------------------------------------- #

#: The shape the defect was measured in: 25,000 nodes resident, only 500 of them in
#: the `turn` population, and a stale report claiming 20,000 turns.
_STALE_GRAPH = {"Point": 25_000}
_STALE_POPULATION = {"turn": 500}
_STALE_CLAIM = 20_000


def test_a_stale_report_cannot_size_a_sweep_beyond_its_own_population():
    """⛔ The licence's staleness guard was the WHOLE graph's node total.

    Measured before the fix (lane container, `docs/evidence/10976-licence/`): a graph
    holding 25,000 nodes with **500** `is_episodic = true` turns answered a 20,000/20,000
    CLEAN report with `verdict: PLAN` and `plan["evictable"] = [{population: turn, nodes:
    20000}]` — a cap **40x** the population it bounds, because 20,000 < 25,000. The
    executor spends that cap directly (`licence_remaining`), so the runaway guard the
    cap exists for was 40x too loose.

    Nothing unserved could be deleted (gate 3 asks the record per node) — but "evicting
    more nodes than the instrument measured is evicting nodes nothing proved" was false
    as a property, which is what this pins.
    """
    g = _FakeGraph(_STALE_GRAPH, populations=_STALE_POPULATION)
    c = _FakeClient(used=900, maxmemory=1_000)
    stale = ParityReport(instrument=PARITY_INSTRUMENTS["turn"], verdict="CLEAN",
                         measured_nodes=_STALE_CLAIM, total_nodes=_STALE_CLAIM,
                         graph_name="tortoise")

    result = assess(c, g, "tortoise", ceiling_bytes=100,
                    parity_reports={PARITY_INSTRUMENTS["turn"]: stale})

    assert result.verdict == "REFUSED", result.plan
    assert result.eligible_classes == ()
    assert result.plan["evictable"] == []
    reason = " ".join(result.problems)
    # Both numbers must be in the refusal: an operator has to see WHICH population is
    # short and by how much, or the next step is a re-investigation. ⛔ BIND THE PHRASE,
    # not the bare number — `str(500)` is satisfied by the label row's `25000`, so a
    # message reworded to drop the population size would keep a numeric assertion green.
    assert str(_STALE_CLAIM) in reason, reason
    assert f"holds only {_STALE_POPULATION['turn']}" in reason, reason
    assert g.mutated == []


def test_an_honest_report_of_the_same_population_still_licenses_it():
    """The positive arm — the fix must not shut a gate that is legitimately open.

    Without this, deleting the population bound entirely would keep the refusal test
    green: a refusal-only suite cannot tell "the bound works" from "the bound is shut".
    """
    g = _FakeGraph(_STALE_GRAPH, populations=_STALE_POPULATION)
    c = _FakeClient(used=900, maxmemory=1_000)
    honest = ParityReport(instrument=PARITY_INSTRUMENTS["turn"], verdict="CLEAN",
                          measured_nodes=_STALE_POPULATION["turn"],
                          total_nodes=_STALE_POPULATION["turn"],
                          graph_name="tortoise")

    result = assess(c, g, "tortoise", ceiling_bytes=100,
                    parity_reports={PARITY_INSTRUMENTS["turn"]: honest})

    assert result.verdict == "PLAN", result.problems
    assert [e["population"] for e in result.plan["evictable"]] == ["turn"]
    assert result.plan["evictable"][0]["nodes"] == _STALE_POPULATION["turn"]
    assert g.mutated == []


def test_a_report_claiming_fewer_nodes_than_the_population_still_licenses_that_smaller_cap():
    """A SMALLER claim is the safe direction and must survive: it caps the sweep LOWER.

    The bound is an upper bound (`>`), not equality. Requiring equality would refuse a
    report measured while the population was smaller — over-tightening a gate that is
    allowed to be conservative, and one a future "the numbers must match" simplification
    would silently make permanent.
    """
    g = _FakeGraph(_STALE_GRAPH, populations=_STALE_POPULATION)
    c = _FakeClient(used=900, maxmemory=1_000)
    smaller = ParityReport(instrument=PARITY_INSTRUMENTS["turn"], verdict="CLEAN",
                           measured_nodes=400, total_nodes=400, graph_name="tortoise")

    result = assess(c, g, "tortoise", ceiling_bytes=100,
                    parity_reports={PARITY_INSTRUMENTS["turn"]: smaller})

    assert result.verdict == "PLAN", result.problems
    assert result.plan["evictable"][0]["nodes"] == 400


def test_the_population_count_mirrors_the_instrument():
    """The population predicate has ONE owner — and a test that reds when it drifts.

    `tools/turn_parity_census.py` owns the turn population's shape (#3551). The census
    may not import a script at RUNTIME, so `POPULATION_RESIDENT_COUNT["turn"]` is a
    mirror; this is the drift pin, and it compares the EXECUTED constant rather than the
    instrument's source text. A mirror that silently narrowed or widened the predicate
    would change the ceiling every licence is measured against, with no error anywhere.
    ⚠️ FOUR other textual carriers of this predicate exist — `tools/purge_legacy_turns.py`,
    `tools/backfill_graph_turns.py`, `tools/backfill_copy_turns.py` and
    `tortoise/projection/__init__.py::_episodic_point_ids`. ONLY the instrument is pinned,
    deliberately: it is the one whose report sizes a licence, and pinning the others would
    red this bound whenever one of them reflows a statement nothing licenses.
    """
    # ⛔ IDENTITY, not source-text containment. A containment check is satisfied by ANY
    # occurrence of the string — and the instrument carries `is_episodic = true` in a
    # dozen docstrings, so the accidental change this pin exists for (edit the executed
    # constant, leave the old statement in prose) would keep it green. Bind the constant
    # itself: a rename, a removal or a changed predicate all red.
    from tools.turn_parity_census import _GRAPH_EPISODIC_COUNT_CYPHER

    def squash(text: str) -> str:
        # The instrument writes the statement as ADJACENT literals split across source
        # lines (`"... true " "RETURN count(t)"`), so compare on collapsed whitespace.
        return " ".join(text.split())

    assert squash(POPULATION_RESIDENT_COUNT["turn"]) == squash(
        _GRAPH_EPISODIC_COUNT_CYPHER
    ), (
        "POPULATION_RESIDENT_COUNT['turn'] and the instrument's own count statement "
        "have drifted — the census and the instrument now measure different "
        "populations (#3551 owns the predicate)"
    )
    # …and the predicate must keep the two halves that make it the POPULATION rather
    # than the label: `:Point` (turns live there) and `is_episodic` (the recorded
    # delete predicate, #10017). `is_episodic` is stamped on `:Source`/`:Object` nodes
    # too, so dropping `:Point` would INFLATE the ceiling.
    assert ":Point" in POPULATION_RESIDENT_COUNT["turn"]
    assert "is_episodic" in POPULATION_RESIDENT_COUNT["turn"]


def test_every_registered_population_has_a_resident_count_statement():
    """A population licensed without a count statement could not be bounded at all.

    `population_resident_count` refuses rather than defaulting — this pins that the
    registry can never reach that refusal by construction, and that no count statement
    outlives its population.
    """
    assert set(POPULATION_RESIDENT_COUNT) == set(PARITY_INSTRUMENTS), (
        "every licensed population needs its OWN resident-count statement, and a "
        "statement for a population nothing licenses is dead code that reads as a "
        "bound"
    )


def _shared_count_statements(mapping: dict[str, str]) -> set[str]:
    """The count statements that two or more populations of `mapping` would SHARE.

    Squashed (`" ".join(value.split())`), because a Cypher statement's identity is
    semantic and the realistic copy-paste failure is the same one-liner REFLOWED across
    source lines — byte comparison would call that unique while FalkorDB sees one
    statement, bounding the new population by `turn`'s count (fail-open).

    A helper rather than an inline comprehension so the pin has DEMONSTRABLE
    discriminating power: `test_the_shared_statement_check_can_actually_fire` feeds it a
    duplicated mapping and asserts the collision is reported. An inline assertion over
    the live registry alone cannot fail while the registry holds one population.
    """
    seen: set[str] = set()
    shared: set[str] = set()
    for value in mapping.values():
        squashed = " ".join(value.split())
        if squashed in seen:
            shared.add(squashed)
        seen.add(squashed)
    return shared


def test_no_two_populations_share_a_resident_count_statement():
    """⛔ A COPY-PASTED STATEMENT IS A FAIL-OPEN BOUND, not a harmless duplicate.

    Registering a second population and copying `turn`'s count into its slot would bound
    that population by the TURN population's size — a stale report for it would then
    license up to the turn count, and the whole-graph backstop cannot see it (a subgraph
    count never exceeds the graph's total). The next population's registry entry is the
    one place this defect would be introduced, so pin it here rather than in a reviewer's
    head: each population must be counted by its OWN statement.

    ⚠️ SCOPE — THIS PIN IS TEXTUAL. It catches an exact copy and a reflowed copy (the
    realistic shapes); a SEMANTICALLY identical re-spelling (`WHERE true = t.is_episodic`,
    or a superset carrying an extra filter) is NOT detected. Whoever ADDS a population
    must therefore own that population's predicate and bound rather than inherit this
    assertion — the semantic-duplicate gap is filed as #11726.
    """
    # ⛔ SQUASHED, not verbatim. A Cypher statement's identity is semantic, and the
    # realistic copy-paste failure is the same one-liner REFLOWED across source lines —
    # byte comparison would call that unique while FalkorDB sees one statement, bounding
    # the new population by `turn`'s count (fail-open, and invisible to the whole-graph
    # backstop). The mirror pin above squashes for the same reason.
    shared = _shared_count_statements(POPULATION_RESIDENT_COUNT)
    assert not shared, (
        f"two populations share one count statement: {shared} of "
        f"{POPULATION_RESIDENT_COUNT} — each population must be counted by the "
        f"statement for ITS OWN predicate"
    )


def test_the_shared_statement_check_can_actually_fire():
    """⛔ PROOF THE PIN ABOVE IS NOT VACUOUS.

    `POPULATION_RESIDENT_COUNT` holds ONE population today, so the live assertion above
    is "no shared statement" over a single element and CAN NEVER FAIL: it is a guard with
    no demonstrated discriminating power. This test drives the SAME helper with the
    defect that guard exists to catch and asserts it reports the collision — otherwise
    nothing shows it would fire the day a second population is registered, which is the
    only day it matters (#11726).

    It also asserts the NEGATIVE direction: a genuinely distinct predicate must NOT be
    reported, or the guard would block every new population and get deleted.
    """
    reflowed_copy = POPULATION_RESIDENT_COUNT["turn"].replace(
        " RETURN count(t)", "\n    RETURN count(t)"
    )
    assert reflowed_copy != POPULATION_RESIDENT_COUNT["turn"], (
        "the fixture must differ BYTEWISE, or this test proves nothing about the squash"
    )
    duplicated = {"turn": POPULATION_RESIDENT_COUNT["turn"], "turn_v2": reflowed_copy}
    assert _shared_count_statements(duplicated) == {
        " ".join(POPULATION_RESIDENT_COUNT["turn"].split())
    }, (
        "a REFLOWED copy of another population's statement must be reported as shared — "
        "byte comparison would call it unique while FalkorDB sees one statement"
    )
    assert not _shared_count_statements(
        {
            "turn": POPULATION_RESIDENT_COUNT["turn"],
            "other": "MATCH (o:Object) RETURN count(o)",
        }
    ), "a genuinely distinct predicate must NOT be reported, or the guard is a false block"


def test_an_empty_population_count_read_is_a_refusal_not_zero():
    """`0` and "could not read" are opposite findings, and only one is safe to threshold.

    `RETURN count(...)` always yields one row, so an empty result means the reading did
    not happen. Softening it to `0` would demote every report for the population with
    the message "this graph holds only 0 …" — a fact about the graph nobody measured,
    and a refusal indistinguishable from the record not being ready.
    """

    class _NoRows:
        class _Result:
            def __init__(self) -> None:
                self.result_set: list = []

        def ro_query(self, statement, params=None):
            return _NoRows._Result()

    with pytest.raises(CacheReadError):
        population_resident_count(_NoRows(), "turn")


def test_a_population_with_no_count_statement_is_a_refusal_not_a_guess():
    """A population licensed without a bound must not be bounded by a default.

    Defaulting to `0` would silently demote it forever; defaulting to the whole-graph
    total would silently restore the defect this change closes. Both are guesses.
    """

    # ⛔ The stub must ANSWER a count, not raise (#11709 review, P1). `_read_statement`
    # wraps ANY exception as `CacheReadError("… unreadable …")`, so a raising stub is
    # converted into the very exception this test expects — from the WRONG branch — and
    # the whole-graph default (`.get(population, "MATCH (n) RETURN count(n)")`) then
    # reads a number successfully and the row stays GREEN. Measured at the reviewed
    # head: `CacheReadError: … unreadable … (got 'MATCH (n) RETURN count(n)')` — the
    # default WAS taken and the population WAS bounded by a guess. Answering a count
    # makes any defaulted statement succeed (it would return 999 and this test would
    # fail on the missing `pytest.raises`), and the `match` binds the refusal to the
    # MISSING-STATEMENT branch rather than accepting any `CacheReadError` at all.
    # The `0` half is covered by this same shape: a `0` default returns 0, and a
    # whole-graph default returns 999.
    class _Graph:
        class _Result:
            def __init__(self, rows):
                #: ANY read SUCCEEDS — so a defaulted statement would be USED, never
                #: refused, and this test would fail on its missing `pytest.raises`.
                self.result_set = rows

        def ro_query(self, statement, params=None):
            return _Graph._Result([[999]])

    with pytest.raises(CacheReadError, match="no resident-count statement"):
        population_resident_count(_Graph(), "no-such-population")


def test_the_graph_total_is_read_AFTER_every_population_count():
    """⛔ Pin the READ ORDER, because reverting it left every suite green (#11709).

    The total is read AFTER the per-population counts so that a graph which GAINS nodes
    between the two reads satisfies `resident_population <= total` by construction: an
    honest 500/500 report cannot be demoted by the whole-graph backstop. Read BEFORE the
    counts it could be — a legitimate 500/500 report measured REFUSED when the total read
    answered 5, the false block #11726 records.

    The property is the ORDER, so that is what is asserted. A behavioural fixture cannot
    see it: `node_total` is answered from graph metadata, and a fixture that answers
    differently on the two reads models a graph that does not exist. Statement order is
    the cheapest true statement of the invariant, and it reds on the pre-fix order.
    """
    g = _FakeGraph(_STALE_GRAPH, populations=_STALE_POPULATION)
    c = _FakeClient(used=900, maxmemory=1_000)
    honest = ParityReport(instrument=PARITY_INSTRUMENTS["turn"], verdict="CLEAN",
                          measured_nodes=_STALE_POPULATION["turn"],
                          total_nodes=_STALE_POPULATION["turn"],
                          graph_name="tortoise")

    result = assess(c, g, "tortoise", ceiling_bytes=100,
                    parity_reports={PARITY_INSTRUMENTS["turn"]: honest})

    # The honest arm still licenses (so this test cannot pass by refusing everything).
    assert result.verdict == "PLAN", result.problems
    assert [e["population"] for e in result.plan["evictable"]] == ["turn"]

    total_at = g.statements.index("MATCH (n) RETURN count(n)")
    population_at = [
        i for i, s in enumerate(g.statements)
        if s == POPULATION_RESIDENT_COUNT["turn"]
    ]
    assert population_at, (
        "the population was never counted, so this test measured nothing — a report "
        "that is not eligible never reaches the bound it is meant to exercise"
    )
    assert all(i < total_at for i in population_at), (
        "the graph total is read BEFORE the population counts: a graph that GAINS "
        "nodes between the two reads then demotes an honest report through the "
        "whole-graph backstop (#11726) — read it after them"
    )


def test_a_row_multiplying_count_statement_is_a_documented_residual():
    """⛔ WHAT THE WHOLE-GRAPH BACKSTOP DOES NOT CATCH — pinned so nobody believes it does.

    A count statement whose query shape multiplies rows (a join, a second `MATCH`, an
    `UNWIND`) inflates the population's ceiling. When the REPORT's claim then sits under
    the graph's whole node count, BOTH guards pass: the per-population bound validates
    the claim against the inflated ceiling, and the whole-graph check compares the CLAIM
    (never the mirror's count) against the total.

    ⛔ WHY THIS IS THE IRREDUCIBLE BAND, and not a checker someone forgot to write:
    refusing on the MIRROR's count vs the graph total (`resident_population >
    node_total`) is not sound — it cannot tell an inflated statement from a graph that
    LOST nodes between the population read and the total read (the total is read AFTER
    every population count, `cache_eviction.py`'s `resident_total = node_total(graph)`),
    so it would false-block. Only removals can make `resident_population > node_total`
    in that order; a cache that GAINED nodes raises the total with the population, so
    gains cannot produce the false block this docstring used to name — the direction is
    stated once, correctly, in `cache_eviction.py`'s backstop comment. Refusing on
    `resident_population > report.total_nodes` is worse: the
    report's total is frozen at measurement time, so it would refuse every legitimate
    growth. That is why the residual rests on the identity pin
    (`test_the_population_count_mirrors_the_instrument`), which binds the mirror to the
    instrument's executed constant.

    This test asserts the residual rather than the desired behaviour on purpose: the
    alternative is a suite that reads as though the backstop covered the class, which is
    how the mirror pin gets retired as "belt and braces".
    """
    # 25,000 nodes; the mirror answers 2,000 for a 500-node population (a row-multiplying
    # statement); the report claims 1,500 — under the graph total, so the backstop is
    # silent, and under the inflated ceiling, so the population bound is silent too.
    g = _FakeGraph({"Point": 25_000}, populations={"turn": 2_000})
    c = _FakeClient(used=900, maxmemory=1_000)
    report = ParityReport(instrument=PARITY_INSTRUMENTS["turn"], verdict="CLEAN",
                          measured_nodes=1_500, total_nodes=1_500,
                          graph_name="tortoise")

    result = assess(c, g, "tortoise", ceiling_bytes=100,
                    parity_reports={PARITY_INSTRUMENTS["turn"]: report})

    assert result.verdict == "PLAN", result.problems
    assert result.plan["evictable"][0]["nodes"] == 1_500, result.plan["evictable"]
    assert not any("claims 1500" in problem for problem in result.problems), (
        "the population bound must not be described as catching a claim that sits under "
        "the inflated ceiling"
    )
    assert g.mutated == []
