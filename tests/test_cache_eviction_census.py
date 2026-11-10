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

import math
import re
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise.cache_eviction import (
    CACHE_CEILING_ENV,
    DEFAULT_PARITY_REPORT_MAX_AGE_S,
    DERIVED_LABELS,
    PARITY_INSTRUMENTS,
    PARITY_REPORT_MAX_AGE_ENV,
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
    parse_measured_at,
    resident_counts,
    resolve_max_report_age,
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

    def __init__(self, counts: dict[str, int], *, unlabelled: int = 0) -> None:
        self._counts = dict(counts)
        self._unlabelled = unlabelled
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
        if "db.labels" in statement:
            return self._Result([[label] for label in self._counts])
        if "size(labels(n)) = 0" in statement:
            # Label-less nodes, MEASURED — never a residual (multi-label nodes make
            # `total - sum(counts)` go negative; see `resident_counts`).
            return self._Result([[self._unlabelled]])
        if statement.startswith("MATCH (n:") and "count(n)" in statement:
            label = statement[len("MATCH (n:"):].split(")")[0]
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
    g = _FakeGraph({"Source": 50, "GraphEvent": 5_000, "Point": 10_000, "Object": 900})
    c = _FakeClient(used=900, maxmemory=1_000)
    # A CLEAN turn census — which must license the TURN population and NOTHING else.
    report = ParityReport(instrument="tools/turn_parity_census.py", verdict="CLEAN",
                          measured_nodes=2_000, total_nodes=2_000,
                          graph_name="tortoise", measured_at=time.time())

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
                           measured_nodes=40, total_nodes=100, graph_name="tortoise",
                           measured_at=time.time())

    result = assess(c, g, "tortoise", ceiling_bytes=100,
                    parity_reports={"tools/turn_parity_census.py": partial})

    assert result.verdict == "REFUSED"
    assert result.eligible_classes == ()
    assert "40/100" in " ".join(result.problems)


def test_a_verdict_with_no_population_is_not_evidence_about_a_population():
    g = _FakeGraph({"Point": 100})
    c = _FakeClient(used=900, maxmemory=1_000)
    no_population = ParityReport(instrument="tools/turn_parity_census.py", verdict="CLEAN",
                                 graph_name="tortoise", measured_at=time.time())

    result = assess(c, g, "tortoise", ceiling_bytes=100,
                    parity_reports={"tools/turn_parity_census.py": no_population})

    assert result.verdict == "REFUSED"


@pytest.mark.parametrize("verdict", ["DIRTY", "MISMATCH", "", "problems"])
def test_a_non_clean_verdict_never_proves(verdict):
    g = _FakeGraph({"Point": 100})
    c = _FakeClient(used=900, maxmemory=1_000)
    report = ParityReport(instrument="tools/turn_parity_census.py", verdict=verdict,
                          measured_nodes=100, total_nodes=100, graph_name="tortoise",
                          measured_at=time.time())

    result = assess(c, g, "tortoise", ceiling_bytes=100,
                    parity_reports={"tools/turn_parity_census.py": report})

    assert result.verdict == "REFUSED"


def test_a_clean_byte_verdict_still_publishes_the_licence():
    """⛔ The negative case: `CLEAN` with a PROVEN population must carry the licence.

    This is the state that made the entry bound unenforceable — under the byte ceiling,
    so `CLEAN`, but the entry budget a consumer enforces may be exceeded by orders of
    magnitude (many small turn nodes, the shape #10976 describes).
    """
    g = _FakeGraph({"Point": 10})
    c = _FakeClient(used=500, maxmemory=10_000)
    report = ParityReport(
        instrument=PARITY_INSTRUMENTS["turn"], verdict="CLEAN",
        measured_nodes=10, total_nodes=10, graph_name="tortoise",
        measured_at=time.time(),
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
                          graph_name="tortoise", measured_at=time.time())

    assert report.proves() is expected


def test_a_zero_population_report_cannot_flip_the_gate_open():
    """0/0 CLEAN would flip REFUSED→PLAN (exit 3→2) with nothing behind it."""
    g = _FakeGraph({"Point": 100})
    c = _FakeClient(used=900, maxmemory=1_000)
    empty = ParityReport(instrument="tools/turn_parity_census.py", verdict="CLEAN",
                         measured_nodes=0, total_nodes=0, graph_name="tortoise",
                         measured_at=time.time())

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
                           measured_nodes=1_000, total_nodes=1_000,
                           measured_at=time.time())

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
    g = _FakeGraph({"Point": 5})
    c = _FakeClient(used=900, maxmemory=1_000)
    stale = ParityReport(instrument="tools/turn_parity_census.py", verdict="CLEAN",
                         measured_nodes=120, total_nodes=120, graph_name="tortoise",
                         measured_at=time.time())

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
    g = _FakeGraph({"Point": 10_000, "Object": 900})
    c = _FakeClient(used=900, maxmemory=1_000)
    report = ParityReport(instrument="tools/turn_parity_census.py", verdict="CLEAN",
                          measured_nodes=2_000, total_nodes=2_000,
                          graph_name="tortoise", measured_at=time.time())

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
                             graph_name="some_other_graph", measured_at=time.time())

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
    g = _FakeGraph({"Point": 10_000, "Object": 2_000})
    g.true_total = 10_000
    c = _FakeClient(used=900, maxmemory=1_000)
    inflated = ParityReport(instrument="tools/turn_parity_census.py", verdict="CLEAN",
                            measured_nodes=11_000, total_nodes=11_000,
                            graph_name="tortoise", measured_at=time.time())

    result = assess(c, g, "tortoise", ceiling_bytes=100,
                    parity_reports={"tools/turn_parity_census.py": inflated})

    assert result.verdict == "REFUSED"
    assert "only 10000" in " ".join(result.problems)


# --------------------------------------------------------------------------- #
# STALENESS — the one hole the name binding and the size cross-check both leave open.
# --------------------------------------------------------------------------- #
#
# The defect this closes, recorded on #10976 (review of the egress executor, #11167):
# the ONLY staleness guard on the licence compared the report's population against the
# WHOLE graph, so a stale `turn` report claiming 20,000 nodes passed on a graph holding
# 25,000 nodes total but only 500 real turns -- and that claim IS the executor's runaway
# cap (`cache_evictor` gate 2, `licence_remaining`), i.e. 40x the population the cap
# exists to bound. Nothing unserved was deleted (gate 3 asks the record per node), so
# this was not the data-loss class: it weakened exactly the property the cap exists for.
# Each test below fails if the staleness demotion is removed, and each fails for its own
# reason -- an undated report, an aged one, and a future-stamped one are three different
# findings and must not collapse into one.


def test_a_report_with_no_measurement_time_is_not_evidence():
    """An artifact that cannot say WHEN it looked cannot be shown to describe this pass."""
    g = _FakeGraph({"Point": 25_000})
    c = _FakeClient(used=900, maxmemory=1_000)
    undated = ParityReport(instrument="tools/turn_parity_census.py", verdict="CLEAN",
                           measured_nodes=500, total_nodes=500, graph_name="tortoise")

    result = assess(c, g, "tortoise", ceiling_bytes=100,
                    parity_reports={"tools/turn_parity_census.py": undated})

    assert result.verdict == "REFUSED"
    assert result.eligible_classes == ()
    assert result.plan["evictable"] == []
    assert "NO measurement time" in " ".join(result.problems), result.problems
    assert g.mutated == []


def test_a_stale_report_larger_than_the_population_but_smaller_than_the_graph_is_refused(
    monkeypatch,
):
    """The measured 40x shape: 20,000 claimed, 25,000 resident, 500 real turns.

    ⛔ THIS IS THE RESIDUAL ITSELF. The over-count cross-check passes it
    (`500 < 20,000 < 25,000`), and before the age bound the plan offered 20,000 nodes
    — the number the executor would have capped its sweep at.

    `monkeypatch.delenv` makes the bound the DEFAULT: the fixture is aged 7200 s, so an
    operator lever exported at, say, 7200 s would otherwise read this refusal as the
    default's and the test would flip on an environment it never set.
    """
    monkeypatch.delenv(PARITY_REPORT_MAX_AGE_ENV, raising=False)
    g = _FakeGraph({"Point": 25_000})
    c = _FakeClient(used=900, maxmemory=1_000)
    aged = ParityReport(instrument="tools/turn_parity_census.py", verdict="CLEAN",
                        measured_nodes=20_000, total_nodes=20_000,
                        graph_name="tortoise",
                        measured_at=time.time() - 2 * 60 * 60)

    result = assess(c, g, "tortoise", ceiling_bytes=100,
                    parity_reports={"tools/turn_parity_census.py": aged})

    assert result.verdict == "REFUSED", result.verdict
    assert result.plan["evictable"] == []
    assert "older than this pass's" in " ".join(result.problems), result.problems


def test_a_report_stamped_far_in_the_FUTURE_is_a_clock_disagreement_not_freshness():
    """A stamp that predates nothing cannot place the reading in this pass.

    Bounded, not absolute: two hosts may disagree by seconds (see
    `PARITY_REPORT_FUTURE_SKEW_S`), so this asserts the TOLERANCE rather than "any
    future stamp refuses" — the looser rule would false-block on NTP skew.
    """
    g = _FakeGraph({"Point": 25_000})
    c = _FakeClient(used=900, maxmemory=1_000)
    ahead = ParityReport(instrument="tools/turn_parity_census.py", verdict="CLEAN",
                         measured_nodes=500, total_nodes=500, graph_name="tortoise",
                         measured_at=time.time() + 60 * 60)

    result = assess(c, g, "tortoise", ceiling_bytes=100,
                    parity_reports={"tools/turn_parity_census.py": ahead})

    assert result.verdict == "REFUSED", result.verdict
    assert "clock disagreement" in " ".join(result.problems), result.problems


def test_a_second_or_two_of_clock_skew_does_not_refuse_a_fresh_report():
    """The other side of the tolerance: a small future stamp is SKEW, not a defect."""
    g = _FakeGraph({"Point": 25_000})
    c = _FakeClient(used=900, maxmemory=1_000)
    skewed = ParityReport(instrument="tools/turn_parity_census.py", verdict="CLEAN",
                          measured_nodes=500, total_nodes=500, graph_name="tortoise",
                          measured_at=time.time() + 2)

    result = assess(c, g, "tortoise", ceiling_bytes=100,
                    parity_reports={"tools/turn_parity_census.py": skewed})

    assert result.verdict == "PLAN", result.problems
    assert [e["population"] for e in result.plan["evictable"]] == ["turn"]


def test_the_age_bound_is_read_from_the_environment(monkeypatch):
    """The operator's lever, and it is a BOUND: the aged fixture flips with it."""
    g = _FakeGraph({"Point": 25_000})
    c = _FakeClient(used=900, maxmemory=1_000)
    aged = ParityReport(instrument="tools/turn_parity_census.py", verdict="CLEAN",
                        measured_nodes=500, total_nodes=500, graph_name="tortoise",
                        measured_at=time.time() - 120)

    monkeypatch.setenv(PARITY_REPORT_MAX_AGE_ENV, "60")
    refused = assess(c, g, "tortoise", ceiling_bytes=100,
                     parity_reports={"tools/turn_parity_census.py": aged})
    assert refused.verdict == "REFUSED", refused.problems
    assert refused.plan["parity_evidence"]["bound_source"] == PARITY_REPORT_MAX_AGE_ENV

    monkeypatch.setenv(PARITY_REPORT_MAX_AGE_ENV, "600")
    accepted = assess(c, g, "tortoise", ceiling_bytes=100,
                      parity_reports={"tools/turn_parity_census.py": aged})
    assert accepted.verdict == "PLAN", accepted.problems


def test_the_default_bound_is_applied_when_nothing_is_configured(monkeypatch):
    monkeypatch.delenv(PARITY_REPORT_MAX_AGE_ENV, raising=False)
    assert resolve_max_report_age() == DEFAULT_PARITY_REPORT_MAX_AGE_S
    assert resolve_max_report_age(42) == 42
    assert resolve_max_report_age(0.5) == 0.5


def test_a_non_positive_or_malformed_bound_is_REFUSED_not_clamped(monkeypatch):
    """A zero bound would refuse every report while LOOKING configured.

    Same rule as `measure_state`'s non-positive ceiling: a bound must be greater than
    zero. Reading 0 or -1 as "no bound" would be the fail-open form; reading them as a
    live bound would be an inert enforcer. Both are refused instead.
    """
    for bad in (0, -1, -0.5):
        with pytest.raises(CacheReadError):
            resolve_max_report_age(bad)
    for bad in ("nan", "inf", "-inf", "soon"):
        monkeypatch.setenv(PARITY_REPORT_MAX_AGE_ENV, bad)
        with pytest.raises(CacheReadError):
            resolve_max_report_age()


def test_the_bound_source_is_derived_from_the_bound_that_applied(monkeypatch):
    """An artifact field a monitor keys on must name the bound that REALLY licensed.

    Reachable — and it WAS the shipped state: `bound_source` was chosen by truthiness
    of the raw variable while the resolution treats a whitespace-only value as unset,
    so `TORTOISE_PARITY_REPORT_MAX_AGE_S='   '` applied the DEFAULT bound (3600 s) and
    reported `bound_source: "TORTOISE_PARITY_REPORT_MAX_AGE_S"`. The field exists so a
    monitor can tell which bound licensed a DESTRUCTIVE cap, so naming the wrong one is
    the artifact lying about its own provenance.
    """
    g = _FakeGraph({"Point": 25_000})
    c = _FakeClient(used=900, maxmemory=1_000)
    fresh = ParityReport(instrument="tools/turn_parity_census.py", verdict="CLEAN",
                         measured_nodes=500, total_nodes=500, graph_name="tortoise",
                         measured_at=time.time() - 30)

    monkeypatch.setenv(PARITY_REPORT_MAX_AGE_ENV, "   ")
    evidence = assess(c, g, "tortoise", ceiling_bytes=100,
                      parity_reports={"tools/turn_parity_census.py": fresh}
                      ).plan["parity_evidence"]
    assert evidence["bound_source"] == "default", evidence["bound_source"]
    assert evidence["max_age_seconds"] == DEFAULT_PARITY_REPORT_MAX_AGE_S

    # The environment, when it DOES bind, and the argument, which always wins.
    monkeypatch.setenv(PARITY_REPORT_MAX_AGE_ENV, "600")
    evidence = assess(c, g, "tortoise", ceiling_bytes=100,
                      parity_reports={"tools/turn_parity_census.py": fresh}
                      ).plan["parity_evidence"]
    assert evidence["bound_source"] == PARITY_REPORT_MAX_AGE_ENV
    assert evidence["max_age_seconds"] == 600.0

    evidence = assess(c, g, "tortoise", ceiling_bytes=100,
                      parity_reports={"tools/turn_parity_census.py": fresh},
                      max_report_age_seconds=120).plan["parity_evidence"]
    assert evidence["bound_source"] == "argument"
    assert evidence["max_age_seconds"] == 120.0


def test_parse_measured_at_refuses_a_stamp_it_cannot_age():
    """A stamp the bound cannot EVALUATE is a refusal, not a fresh reading.

    Reachable — and it WAS the shipped state: the numeric arm returned `float(raw)`
    unchecked, so `json.load`'s documented `NaN`/`Infinity` extension reached `assess`
    as a stamp. `now - nan` is `nan`, every comparison in the staleness block is False
    for it, and the report therefore stayed PROVEN — licensed — with only a render
    helper raising two frames later. Same rule the bound already applied one function
    up: a non-finite operand is REFUSED, never clamped.

    The range arm is the same defect in its other shape: a millisecond epoch is 1000x
    too large and must be refused AS A UNIT ERROR, not crash the artifact renderer.
    """
    for bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(CacheReadError, match="not a finite instant"):
            parse_measured_at(bad)
    for bad in (1.75e12, 1e18):
        # A unit error is named as one, so the operator fixes the producer's stamp
        # rather than hunting a clock drift that does not exist.
        with pytest.raises(CacheReadError, match="MILLISECOND"):
            parse_measured_at(bad)
    # ...and the two edges of the representable window are REFUSED / ACCEPTED exactly
    # where `datetime`'s own year range puts them, so the bound is measured not guessed.
    with pytest.raises(CacheReadError):
        parse_measured_at(253_402_300_800.0)
    assert parse_measured_at(-62_135_596_800.0) == -62_135_596_800.0
    assert parse_measured_at(1_700_000_000.0) == 1_700_000_000.0

    # ⛔ A JSON INTEGER TOO LARGE FOR `float` IS A REFUSAL, NOT AN `OverflowError`.
    # `json.load` reads ANY digit string as a Python int, so this shape is reachable
    # from an operator artifact. Measured pre-fix: `parse_measured_at(10**309)` raised
    # `OverflowError: int too large to convert to float` — not this module's refusal
    # type, so it escaped the loader's documented contract and would have reached
    # `tools/cache_evict.py` as a traceback with exit 1 (its USAGE code, "retry your
    # flags") instead of 3 (REFUSED). The int arm range-checks BEFORE converting.
    for huge in (10**309, -(10**309)):
        with pytest.raises(CacheReadError, match="MILLISECOND"):
            parse_measured_at(huge)
    # …and an IN-RANGE int is still accepted, converted exactly (no range check drift).
    assert parse_measured_at(1_700_000_000) == 1_700_000_000.0
    assert parse_measured_at(-62_135_596_800) == -62_135_596_800.0


def test_a_nan_stamp_in_the_report_FILE_is_a_refusal_not_a_licence(tmp_path):
    """The artifact seam: `json.load` accepts `NaN`, so the READER must not."""
    path = tmp_path / "r.json"
    path.write_text(
        '{"instrument": "tools/turn_parity_census.py", "graph": "tortoise", '
        '"verdict": "CLEAN", "measured_at": NaN, '
        '"population": {"episodic_graph_nodes": 5}, '
        '"dry_run": {"would_delete_turn_nodes": 5}}',
        "utf-8",
    )
    with pytest.raises(CacheReadError):
        load_parity_report(str(path), graph_name="tortoise")


def test_an_int_stamp_too_large_for_the_clock_is_a_refusal_not_an_OverflowError(
    tmp_path,
):
    """The artifact seam, for the one number shape `float()` cannot take.

    Reachable and measured pre-fix: `json.load` reads a 309-digit literal as a Python
    int, `float()` on it raises `OverflowError: int too large to convert to float`, and
    that escaped `load_parity_report`'s documented `CacheReadError` contract. The
    consequence is the one this loader exists to prevent: `tools/cache_evict.py`
    classifies only `CacheReadError` as REFUSED, so this shape produced a traceback
    with exit 1 — its USAGE code, which tells the operator to retry FLAGS against
    evidence nobody could read.
    """
    path = tmp_path / "r.json"
    path.write_text(
        '{"instrument": "tools/turn_parity_census.py", "graph": "tortoise", '
        '"verdict": "CLEAN", "measured_at": ' + "1" + "0" * 309 + ', '
        '"population": {"episodic_graph_nodes": 5}, '
        '"dry_run": {"would_delete_turn_nodes": 5}}',
        "utf-8",
    )
    with pytest.raises(CacheReadError, match="MILLISECOND"):
        load_parity_report(str(path), graph_name="tortoise")


def test_an_un_ageable_stamp_is_REFUSED_by_assess_and_does_not_raise():
    """The LIBRARY surface: `assess` returns a refusal, never a diagnostic-free crash.

    Reachable — and it WAS the shipped state for a directly-constructed report: (1) a
    NaN stamp stayed PROVEN (every age comparison is False for NaN), licensing the
    sweep; (2) an out-of-range finite stamp was demoted correctly but then
    `parity_evidence`'s `_epoch_to_iso` raised `ValueError`/`OverflowError`/`OSError`
    out of `assess` — a non-`CacheReadError` escaping a function whose contract is
    "an `Assessment` or a `CacheReadError`". Both must now be refusals an operator can
    READ, with the artifact still emitted.
    """
    g = _FakeGraph({"Point": 25_000})
    c = _FakeClient(used=900, maxmemory=1_000)

    for stamp, expected in ((float("nan"), "not a finite instant"),
                            (float("inf"), "not a finite instant"),
                            (1.75e12, "clock disagreement")):
        report = ParityReport(instrument="tools/turn_parity_census.py", verdict="CLEAN",
                              measured_nodes=20_000, total_nodes=20_000,
                              graph_name="tortoise", measured_at=stamp)
        result = assess(c, g, "tortoise", ceiling_bytes=100,
                        parity_reports={"tools/turn_parity_census.py": report})
        assert result.verdict == "REFUSED", (stamp, result.verdict)
        assert result.plan["evictable"] == [], stamp
        assert expected in " ".join(result.problems), (stamp, result.problems)
        # The artifact is still emitted, and it says the stamp could not be rendered.
        row = result.plan["parity_evidence"]["reports"][0]
        assert row["measured_at"] is None, (stamp, row)
        if math.isfinite(stamp):
            # An out-of-range finite stamp still HAS an age, and it is the datum that
            # names the defect (a huge negative: decades in the future).
            assert row["age_seconds"] == pytest.approx(-(stamp - time.time()), rel=1e-6)
            assert row["age_seconds"] < 0, row
        else:
            # `now - nan` is `nan`, and every comparison against it is False — so an
            # un-ageable stamp must report NO age rather than a NaN that a JSON
            # consumer would read as a number.
            assert row["age_seconds"] is None, (stamp, row)
    assert g.mutated == []


def test_the_licence_carries_when_it_was_measured_and_against_which_bound(monkeypatch):
    """`nodes` is a destructive cap: the operator must be able to audit its freshness."""
    monkeypatch.delenv(PARITY_REPORT_MAX_AGE_ENV, raising=False)
    g = _FakeGraph({"Point": 25_000})
    c = _FakeClient(used=900, maxmemory=1_000)
    stamp = time.time() - 30
    report = ParityReport(instrument="tools/turn_parity_census.py", verdict="CLEAN",
                          measured_nodes=500, total_nodes=500, graph_name="tortoise",
                          measured_at=stamp)

    result = assess(c, g, "tortoise", ceiling_bytes=100,
                    parity_reports={"tools/turn_parity_census.py": report})

    entry = result.plan["evictable"][0]
    assert entry["nodes"] == 500
    assert entry["max_age_seconds"] == DEFAULT_PARITY_REPORT_MAX_AGE_S
    assert entry["age_seconds"] >= 30
    assert entry["measured_at"].endswith("Z"), entry["measured_at"]
    assert result.plan["parity_evidence"]["reports"][0]["instrument"] == (
        "tools/turn_parity_census.py")


def test_parse_measured_at_reads_the_wire_shapes_and_refuses_nonsense():
    """Three accepted forms, one refusal — and `None` is NOT the refusal."""
    assert parse_measured_at(None) is None
    assert parse_measured_at("") is None
    assert parse_measured_at(1_700_000_000) == 1_700_000_000.0
    assert parse_measured_at("1970-01-01T00:00:01Z") == 1.0
    assert parse_measured_at("1970-01-01T00:00:01") == 1.0
    assert parse_measured_at("2026-11-10T05:03:00+00:00") == parse_measured_at(
        "2026-11-10T05:03:00Z")
    with pytest.raises(CacheReadError):
        parse_measured_at("yesterday-ish")
    with pytest.raises(CacheReadError):
        parse_measured_at(True)


def test_load_parity_report_reads_measured_at_and_refuses_an_unparseable_one(tmp_path):
    """The loader carries the datum, and a broken stamp is a REFUSAL, not `None`."""
    import json

    def _write(payload):
        path = tmp_path / "r.json"
        path.write_text(json.dumps(payload), "utf-8")
        return path

    ok = load_parity_report(str(_write({
        "instrument": "tools/turn_parity_census.py", "graph": "tortoise",
        "verdict": "CLEAN", "measured_at": "2026-11-10T05:03:00Z",
        "population": {"episodic_graph_nodes": 5},
        "dry_run": {"would_delete_turn_nodes": 5},
    })), graph_name="tortoise")
    assert ok.measured_at == parse_measured_at("2026-11-10T05:03:00Z")
    assert ok.proves() is True

    with pytest.raises(CacheReadError):
        load_parity_report(str(_write({
            "instrument": "tools/turn_parity_census.py", "graph": "tortoise",
            "verdict": "CLEAN", "measured_at": "last tuesday",
            "population": {"episodic_graph_nodes": 5},
            "dry_run": {"would_delete_turn_nodes": 5},
        })), graph_name="tortoise")


def test_the_real_instrument_emits_a_measured_at_the_loader_can_age():
    """END TO END across the seam: the producer's stamp survives the loader.

    The honest path is only open while these two agree, so it is pinned as one test
    rather than trusting either side's own suite.
    """
    from tools import turn_parity_census

    payload = turn_parity_census.census(
        {"nodes": {}, "unidentifiable": []},
        {"rows_by_key": {}, "declared_absent": [], "non_turn_shaped": 0},
        sample=0, graph_name="tortoise",
    )
    assert payload["measured_at"].endswith("Z")
    aged = parse_measured_at(payload["measured_at"])
    assert aged is not None and abs(time.time() - aged) < 60


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
                          graph_name="tortoise", measured_at=time.time())

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
