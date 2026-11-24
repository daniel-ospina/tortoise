"""#10465 — the A arms of the DoD instrument: what `tools/dod_5090_probe.py` may
never do to the product, and the product invariants its subjects must keep.

⛔ This file was 14,426 lines. It was a `core` surface row, i.e. it ran on
essentially every PR, and most of that length asserted that documents, status
words, census classes and property lists agree with each other (`'PARTIAL' ==
'MET'`, "§4.8 must name every declared property", "unclassified by the #10541
census", "written labels: [...]"). That is bookkeeping: per AGENTS.md §Product
Over Process it is category B, and it was a gate named as the reason CI stopped
being usable.

What survives is category A only — arms whose failure mode is a product
consequence, not an editorial one:

| arm | the failure it prevents |
|---|---|
| `test_every_embedding_write_path_reaches_the_gate_seam` | an embedding write that stops calling `encode_for_row` — a write that never meets the gate |
| `test_every_embedded_label_has_a_query_path` | a label that is embedded but unqueryable — data written and silently unreachable |
| `test_a_backtick_in_a_graph_label_cannot_re_purpose_the_census_query` | a graph-controlled label interpolated into Cypher — injection from stored data |
| `test_the_census_reads_through_the_READ_ONLY_verb` | the read-only instrument gaining a write verb and mutating the graph it measures |
| `test_the_census_never_touches_a_graph_that_does_not_exist` | a census answering a question about a graph it never opened |
| `test_the_census_refuses_an_embedded_engine` | the census measuring an embedded/throwaway engine and reporting it as the deployment |
| `test_the_census_refuses_to_report_zero_for_an_unreadable_store` | an unreadable store reported as `0` — the 2026-09-24 silent-zero error |
| `test_measure_restores_the_gate_flags`, `test_measure_pins_the_gate_flags_to_an_explicit_off` | the measurement leaving an ambient gate flag flipped — a bypass created by an instrument |
| `test_the_fallback_probe_restores_the_circuit_breakers`, `test_the_fallback_probe_leaves_no_breaker_it_CREATED_behind` | the probe leaving a breaker it opened (or one it created) behind, changing runtime behaviour |
| `test_the_instrument_is_selected_by_its_own_subjects` | a guard registered nowhere — an inert enforcer |
| `test_the_embed_decision_has_exactly_one_predicate`, `test_the_seam_module_is_scanned_for_its_own_patterns`, `test_a_pattern_list_in_the_seam_module_reads_as_drift`, `test_a_second_pattern_list_in_the_embed_gate_reads_as_drift` | the §12.2a predicate count becoming a number nobody asserts on — a second rule set on the embed path reported as `MET` (a false PASS: `measure()` still prints `§12.2a predicates: N — verdict`) |
| `test_an_unparseable_module_is_a_hole_in_the_predicate_count`, `test_a_class_level_pattern_still_counts_as_an_owner`, `test_a_function_local_pattern_is_not_a_second_predicate_owner` | the same count reading a member it could not parse as `MET`, or its module-scope prune swinging into a false `MET` (class body) / a false `DRIFT` on a helper's local pattern |

⛔ WHERE THESE ARMS ACTUALLY RUN — and the trigger this cut COST, stated rather
than papered over. `tortoise/embeddings.py` selects `eval`, so these arms run on a
seam diff. `tortoise/value_gate.py`, `tortoise/embed_gate.py`,
`tortoise/search_engine.py` and `tortoise/projection/entities.py` do NOT select
this file: they reached it through the `core` fallback, whose row this change
removed (taking a document/status-word scorecard off nearly every PR). A
regression in one of those modules therefore reaches the gate-flag, breaker and
census arms on the push-to-main full sweep AFTER merge, not on the PR that
introduces it. That is a disclosed consequence of the `core` removal, not
coverage that exists.

⛔ The §12.2a predicate arms are here because their failure mode is the
INVERSE of bookkeeping: they are the falsifiers for a number the instrument
PRINTS as a verdict (`§12.2a predicates: N (target M) — MET`). Restored after
review F1 on this PR measured that the count, its owner set and its population
were asserted on by nothing once they were cut — a false `MET` is exactly the
class `AGENTS.md` §Product Over Process calls **A**.

⛔ These tests never write a graph and never flip a gate: the instrument is
read-only and its pin must be too.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")
)

import dod_5090_probe as probe


@pytest.fixture(autouse=True)
def _no_ambient_gate_flags():
    """A lane's own env must not be able to make a default-off gate read as
    enforced — `measure()` clears these, and so must the fixture."""
    before = {k: os.environ.get(k) for k in ("TORTOISE_EMBED_GATE", "TORTOISE_VALUE_GATE")}
    for k in before:
        os.environ.pop(k, None)
    try:
        yield
    finally:
        for k, v in before.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_every_embedding_write_path_reaches_the_gate_seam():
    """#5008's seam is reached by the entity writers — not by the raw primitive."""
    seams = probe._write_seams_that_embed()
    for writer in (
        "tortoise.projection.entities::_upsert_point_props",
        "tortoise.projection.entities::_upsert_subject",
        "tortoise.projection.entities::_upsert_object",
        "tortoise.projection.entities::_upsert_document",
        "tortoise.projection.entities::_upsert_event",
    ):
        assert writer in seams, (
            f"{writer} embeds a written row without reaching encode_for_row — "
            f"the ungated-primitive state #5008 closed. Measured seams: {sorted(seams)}"
        )


def test_every_embedded_label_has_a_query_path():
    """Bullet 2: nothing is STORED that `run_vector_query` cannot query.

    The assertions below are an IMPLICATION, never this head's classification:
    the row can only be `PARTIAL` while a write path's label is unresolved, and
    resolving them all (`#5090`) turns it `MET` without reddening this test.

    ⛔ `#11492`: read the map the row SCORES (`probe.scored_labels()`), never
    `written_labels()`. The latter is the pure MEASUREMENT — it does not read the
    product register — so on the tree this row is aiming at (every writer resolved,
    row `MET`) it would still hand back `UNKNOWN` for the register-declared writers
    and this pin would red on a CORRECT change. Resolving in two places is how a row
    and its own pin disagree; there is one combinator and this test comes through it.
    """
    row = probe.score_path_bullet()
    assert row.verdict in ("MET", "PARTIAL"), row.observed
    scored = probe.scored_labels()
    assert scored["labels"] <= probe.served_labels(), scored["labels"] - probe.served_labels()
    if scored["unresolved"]:
        assert row.verdict in ("PARTIAL", "NOT MET"), row.verdict


class _UnreadableGraph:
    """A store whose catalogue cannot be read — NOT an empty store."""

    def query(self, cypher, params=None, timeout=None):
        raise RuntimeError("Timeout reading from socket")

    # The census reads through the read-only verb (`GRAPH.RO_QUERY`); a fake that
    # implements only `query` would let the tool quietly keep the write-capable
    # one, so both verbs answer the same way here.
    def ro_query(self, cypher, params=None, timeout=None):
        raise RuntimeError("Timeout reading from socket")


def test_the_census_refuses_to_report_zero_for_an_unreadable_store():
    """`stored=0` and `could not read` are different facts (2026-09-24's error)."""
    census = probe.vector_census(
        _UnreadableGraph(), "some_graph", at="2026-11-01T00:00:00Z", graph_names=["some_graph"]
    )
    assert census["labels"] == {}
    assert census["catalogue"].startswith("UNMEASURED("), census["catalogue"]
    assert "Timeout reading from socket" in census["catalogue"]


class _MaterialisingGraph:
    """A handle that would CREATE the graph if anything queried it (measured:
    a catalogue read on a missing graph name materialises an empty graph)."""

    def query(self, cypher, params=None, timeout=None):
        raise AssertionError(
            "a catalogue read was issued for a graph that does not exist — that "
            "CREATES it server-side, and the census is read-only by doctrine"
        )

    def ro_query(self, cypher, params=None, timeout=None):
        raise AssertionError(
            "a catalogue read was issued for a graph that does not exist — that "
            "CREATES it server-side, and the census is read-only by doctrine"
        )


def test_the_census_never_touches_a_graph_that_does_not_exist():
    """The read-only doctrine: a missing name is `UNMEASURED`, not a write and
    not a measured-looking `stored=0`."""
    census = probe.vector_census(
        _MaterialisingGraph(),
        "absent_graph",
        at="2026-11-01T00:00:00Z",
        graph_names=["dod5090_probe"],
    )
    assert census["labels"] == {}
    assert "does not exist" in census["catalogue"], census["catalogue"]
    assert census["command"].startswith("uv run python tools/dod_5090_probe.py")


def test_the_census_refuses_an_embedded_engine():
    """Embedded has no index API — a census there would read `indexed: False`
    for every label, which is not the same fact as "nothing is indexed"."""
    with pytest.raises(SystemExit) as raised:
        probe._connect("file:///tmp/whatever.db")
    # ⛔ The documented contract, not merely "it raised": a non-int SystemExit
    # argument exits 1, and this refusal MEANS "could not measure" (review P3).
    assert raised.value.code == 2, raised.value.code


# ── §12.2a — one predicate ─────────────────────────────────────────────────
# ⛔ RESTORED (review F1 on PR #14544): the count/owners/population half was cut as
# "bookkeeping", but its failure mode is a FALSE PASS — the instrument prints
# `§12.2a predicates: 1 (target 1) — MET` while a second rule set, a dropped seam
# module or an unparseable module goes unreported. `tools/dod_5090_probe.py:10346`
# still feeds `count_embed_predicates()` into `measure()`, so without these the value
# is computed and asserted on by nothing. Hermetic: tmp_path only, no DB, no graph, no
# network.


def test_the_embed_decision_has_exactly_one_predicate():
    """§12.2a: ONE rule set, ONE predicate, shared by identity."""
    counted = probe.count_embed_predicates()
    assert counted["count"] == 1, (
        "a second predicate that can answer NO-EMBED is the drift §12.2a "
        f"forbids — measured owners: {counted['owners']}"
    )
    assert counted["owners"] == [probe.PREDICATE_MODULE], counted
    assert counted["rule_set_is_shared_identity"] is True, (
        "embed_gate.RULE_SET must BE value_gate.RULES, not an equal copy"
    )
    assert counted["verdict"] == "MET", counted


def test_a_second_pattern_list_in_the_embed_gate_reads_as_drift(tmp_path, monkeypatch):
    """The mutation check, made executable: the count CAN fail.

    A fake tree whose embed gate owns its own patterns must read `DRIFT` with
    two owners. If the count were hard-wired to 1 (a probe that cannot come
    back red), this test fails — which is the point.
    """
    (tmp_path / "tortoise").mkdir()
    (tmp_path / "tortoise" / "value_gate.py").write_text(
        "import re\n_A = re.compile(r'#\\d+')\nRULES = ('value.identifier_only.reference',)\n"
        "def identifier_only(t):\n    return None\n",
        encoding="utf-8",
    )
    # A SECOND, independently-derived rule set — exactly the drift.
    (tmp_path / "tortoise" / "embed_gate.py").write_text(
        "import re\n_B = re.compile(r'\\b\\d{4}-\\d{2}-\\d{2}\\b')\n"
        "RULE_SET = ('value.identifier_only.reference',)\n"
        "def embedding_verdict(t):\n    return None\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(probe, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(probe, "PREDICATE_MODULE", "tortoise/value_gate.py")
    counted = probe.count_embed_predicates()
    assert counted["count"] == 2, counted
    assert counted["verdict"] == "DRIFT", counted


def test_the_seam_module_is_scanned_for_its_own_patterns():
    """§12.2a's population must include the module that DECIDES to consult the gate.

    ⛔ `tortoise/embeddings.py` imports `embed_gate`, so a walk of the GATE's own
    imports can never enter it: an import makes the IMPORTER depend on the
    IMPORTEE (Python Language Reference §5), and this edge runs the other way.
    Excluding the seam on the grounds that "it is the CALLER, and it imports half
    the tree" answers a question about its IMPORTS — not about its own
    module-scope assignments, which is what this count exists to find.

    Real tree, no monkeypatch: this is the pin that reds if `OWNERSHIP_ONLY` is
    ever "tidied" away, which would silently re-open the hole below.
    """
    population = probe.predicate_population()
    assert "tortoise/embeddings.py" in population, (
        "the seam that decides whether the gate is consulted is not scanned for "
        f"its own patterns — population: {population}"
    )
    # …and the widening must NOT drag in the imports it was excluded for:
    assert "tortoise/api.py" not in population, population


def test_a_function_local_pattern_is_not_a_second_predicate_owner(tmp_path, monkeypatch):
    """A helper's LOCAL `re.compile` is not a second rule set — module scope is.

    `count_embed_predicates`'s docstring has always said *`re.compile` at module
    scope*, while the code ran `ast.walk()` over each top-level statement — which
    descends into `FunctionDef.body`. One ordinary helper-local pattern in any
    module of the population therefore read `count: 2 / verdict: DRIFT` and
    reddened `test_the_embed_decision_has_exactly_one_predicate`, a required
    `core` leg, on a change that is NOT drift: a guard that refutes a correct
    tree, whose cheapest-looking repair is to delete it (review, P3).
    """
    (tmp_path / "tortoise").mkdir()
    (tmp_path / "tortoise" / "value_gate.py").write_text(
        "import re\n_A = re.compile(r'#\\d+')\nRULES = ('r',)\n"
        "def identifier_only(t):\n    return None\n",
        encoding="utf-8",
    )
    (tmp_path / "tortoise" / "embed_gate.py").write_text(
        "from . import value_gate\nRULE_SET = value_gate.RULES\n"
        "def embedding_verdict(t):\n    return None\n",
        encoding="utf-8",
    )
    # The shape a helper grows: compiled when CALLED, not when imported.
    (tmp_path / "tortoise" / "embeddings.py").write_text(
        "from . import embed_gate\n"
        "def _is_precomputed(text):\n"
        "    import re\n"
        "    pat = re.compile(r'^sha256:[0-9a-f]{64}$')\n"
        "    return bool(pat.match(text))\n"
        "def encode_for_row(t, d):\n    return embed_gate.embedding_verdict(t)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(probe, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(probe, "PREDICATE_MODULE", "tortoise/value_gate.py")
    counted = probe.count_embed_predicates()
    assert counted["count"] == 1, (
        f"a function-LOCAL pattern is not a module-scope predicate owner: {counted}")
    assert counted["verdict"] == "MET", counted


def test_a_class_level_pattern_still_counts_as_an_owner(tmp_path, monkeypatch):
    """The module-scope prune must not become the false `MET` it guards against.

    A class body executes at IMPORT time, so a pattern list there IS owned then
    and is a real second rule set. Pruning function bodies must not take the
    class with it — over-reporting drift is this file's stated safe direction,
    and a false `MET` is the failure the whole instrument exists to remove.
    """
    (tmp_path / "tortoise").mkdir()
    (tmp_path / "tortoise" / "value_gate.py").write_text(
        "import re\n_A = re.compile(r'#\\d+')\nRULES = ('r',)\n",
        encoding="utf-8",
    )
    (tmp_path / "tortoise" / "embed_gate.py").write_text(
        "from . import value_gate\nRULE_SET = value_gate.RULES\n",
        encoding="utf-8",
    )
    (tmp_path / "tortoise" / "embeddings.py").write_text(
        "from . import embed_gate\n"
        "class _Skip:\n"
        "    import re\n"
        "    _PATS = [re.compile(r'^sha256:')]\n"
        "def encode_for_row(t, d):\n    return embed_gate.embedding_verdict(t)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(probe, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(probe, "PREDICATE_MODULE", "tortoise/value_gate.py")
    counted = probe.count_embed_predicates()
    assert counted["count"] == 2, counted
    assert counted["verdict"] == "DRIFT", counted


def test_a_pattern_list_in_the_seam_module_reads_as_drift(tmp_path, monkeypatch):
    """A second rule set at the SEAM is the drift §12.2a forbids, and counts.

    ⛔ Non-vacuity: on the pre-fix tool this tree reads `{"count": 1,
    "verdict": "MET"}` — the seam is not in the population, so its pattern list
    is invisible AND the writer map skips the same module, leaving this the only
    detector for the shape. The module is written so it genuinely could not be
    reached by walking the gate's imports: `embeddings.py` imports `embed_gate`,
    never the reverse.
    """
    (tmp_path / "tortoise").mkdir()
    (tmp_path / "tortoise" / "value_gate.py").write_text(
        "import re\n_V = re.compile(r'#\\d+')\nRULES = ('r',)\n", encoding="utf-8")
    (tmp_path / "tortoise" / "embed_gate.py").write_text(
        "from . import value_gate\nRULE_SET = value_gate.RULES\n", encoding="utf-8")
    # The seam: it IMPORTS the gate (so no forward walk of the gate's imports can
    # ever enter it) and owns a second, independently-derived rule set.
    (tmp_path / "tortoise" / "embeddings.py").write_text(
        "import re\nfrom . import embed_gate\n"
        "_SKIP = re.compile(r'^sha256:[0-9a-f]{64}$')\n"
        "def encode_for_row(t, d):\n"
        "    if embed_gate.embed_gate_enabled():\n        return None\n"
        "    return _SKIP.match(t)\n",
        encoding="utf-8")
    monkeypatch.setattr(probe, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(probe, "PREDICATE_MODULE", "tortoise/value_gate.py")
    counted = probe.count_embed_predicates()
    assert counted["count"] == 2, counted
    assert "tortoise/embeddings.py" in counted["owners"], counted
    assert counted["verdict"] == "DRIFT", counted


def test_an_unparseable_module_is_a_hole_in_the_predicate_count(
        tmp_path, monkeypatch):
    """A module the count cannot parse is a HOLE, never a silent zero.

    The writer map already says so (`UNPARSEABLE:<path>` entries; the note "a
    module that will not parse is a HOLE in the measurement, not a zero"). The
    predicate count used to `continue` instead or report the hole, so a broken
    `embed_rules.py` carrying a second pattern list left `count: 1 / MET` —
    the one verdict this instrument exists to withhold.

    ⛔ Non-vacuity: on the pre-fix tool `verdict` reads `"MET"` here.
    """
    (tmp_path / "tortoise").mkdir()
    (tmp_path / "tortoise" / "value_gate.py").write_text(
        "import re\n_V = re.compile(r'#\\d+')\nRULES = ('r',)\n", encoding="utf-8")
    (tmp_path / "tortoise" / "embed_gate.py").write_text(
        "from . import value_gate\nfrom . import embed_rules\n"
        "RULE_SET = value_gate.RULES\n", encoding="utf-8")
    # Reachable ONLY because the PARSEABLE importer names it: `_embed_path_modules`
    # skips an unparseable file when building its edge map but keeps it in the
    # population, which is exactly the reachable shape.
    (tmp_path / "tortoise" / "embed_rules.py").write_text(
        "import re\n_SKIP = re.compile(r'^sha256:')\ndef oops(:\n",
        encoding="utf-8")
    monkeypatch.setattr(probe, "REPO_ROOT", tmp_path)
    counted = probe.count_embed_predicates()
    # The primary claim first, so the pre-fix failure is the VERDICT (it reads
    # `MET`), not merely the absent key: a hole cannot certify `MET`.
    assert counted["verdict"] != "MET", counted
    # `UNDETERMINED`, not `DRIFT`: a hole is not evidence of a second rule set
    # either, and claiming one would be the same unfounded verdict inverted.
    assert counted["verdict"] == "UNDETERMINED", counted
    assert counted["unparseable"] == ["tortoise/embed_rules.py"], counted


def test_the_instrument_is_selected_by_its_own_subjects():
    """A falsifier that never executes is not a falsifier (review P1).

    ⛔ The `core` row is GONE on purpose (see `config/ci-surfaces.yml`, where its
    comment used to be): `core` is the fallback surface, so that row ran this
    file's document/status-word scorecard on nearly every PR. What survives is the
    `eval` registration below, whose subject is the one this file's remaining
    arms guard — `tortoise/embeddings.py`, the `encode_for_row` seam.

    ⛔ A subject that only selected through the `core` fallback NO LONGER selects
    this file, and that is the intended consequence of taking it off the every-PR
    path. It is also this file's one real coverage loss: the gate-flag, breaker and
    census arms guard `tortoise/value_gate.py`, `tortoise/embed_gate.py`,
    `tortoise/search_engine.py` and `tortoise/projection/entities.py`, none of which
    select this file any more. The module docstring states where they DO run (the
    push-to-main full sweep, after merge). This pin asserts the registration that
    exists rather than pretending it covers those subjects.
    """
    import ci_selection

    manifest = ci_selection.load_manifest()
    for subject in ("tortoise/embeddings.py",):
        selected = ci_selection.select([subject], "pull_request", manifest)
        assert "test_dod_5090_probe.py" in selected["test_files"], (
            f"a {subject}-only diff does not select this file — its falsifiers "
            f"never run: surfaces={selected['surfaces']} full={selected['full']}"
        )


def test_measure_restores_the_gate_flags(monkeypatch):
    """A read-only instrument must not leave the caller's process un-gated."""
    monkeypatch.setenv("TORTOISE_EMBED_GATE", "1")
    monkeypatch.setenv("TORTOISE_VALUE_GATE", "1")
    probe.measure()
    assert os.environ["TORTOISE_EMBED_GATE"] == "1"
    assert os.environ["TORTOISE_VALUE_GATE"] == "1"


def test_measure_pins_the_gate_flags_to_an_explicit_off(monkeypatch):
    """Step 0 of the instrument — pinned by the state the ROWS are scored in.

    `#10996` made `TORTOISE_VALUE_GATE` default ON, so a `measure()` that merely
    POPPED the switches produced rows measured against a gate that was ARMED
    while the instrument declared it idle. `measure()` now writes `"0"`
    explicitly, and until this pin existed **nothing observed it**: the mutation
    the review ran (delete the explicit write, keep the pop) left the suite
    green, because `test_measure_restores_the_gate_flags` above only reads the
    environment AFTER the run, which the `finally` block restores either way.

    The assertion is on the RESOLVED predicate, not on the spelling: the
    instrument's claim is "the gates were off while these rows were measured",
    and only the resolver can say whether that was true. A future default flip
    reds this test instead of silently re-arming the instrument.
    """
    from tortoise import embed_gate, value_gate

    flags = ("TORTOISE_EMBED_GATE", "TORTOISE_VALUE_GATE")
    seen: dict = {}

    def _spy():
        # The state the rows are actually scored in — sampled INSIDE the run.
        seen["env"] = {var: os.environ.get(var) for var in flags}
        seen["value_gate"] = value_gate.value_gate_enabled()
        seen["embed_gate"] = embed_gate.embed_gate_enabled()
        return []

    # An ambient ON value must not survive into the measurement: a lane's own
    # shell is exactly what Step 0 exists to neutralise.
    monkeypatch.setenv("TORTOISE_EMBED_GATE", "1")
    monkeypatch.setenv("TORTOISE_VALUE_GATE", "1")
    monkeypatch.setattr(probe, "_measure_rows", _spy)
    probe.measure()

    assert seen["env"] == {var: "0" for var in flags}, (
        "measure() did not PIN the gate flags to an explicit OFF — a pop-only "
        "Step 0 reads as OFF only while the flag defaults OFF, and "
        "TORTOISE_VALUE_GATE now defaults ON (#10996)"
    )
    assert seen["value_gate"] is False, (
        "the rows were measured with the S2.2 mechanical predicate ARMED while "
        "the report declares it idle"
    )
    assert seen["embed_gate"] is False, (
        "the rows were measured with the embed gate ARMED while the report declares it idle"
    )


class _CensusGraph:
    """A fake engine: a catalogue, a label list, and per-label vector counts.

    Exists because every census test before it asserted a FAILURE path, which
    is how three false-`MET` shapes survived a review (the `unindexed` column,
    the population, and the payload arithmetic had no positive coverage).
    """

    def __init__(self, catalogue, labels, vectors, dim=None):
        self._catalogue = catalogue
        self._labels = labels
        self._vectors = vectors  # {label: (count, [1.0]*width | None)}
        self._dim = dim
        #: Every statement the census issued, in order. Recorded so a claim
        #: ABOUT the query (e.g. "the probe carries no `ORDER BY`") can be
        #: asserted against the statement itself instead of against prose that
        #: happens to agree with it (#11337 review).
        self.queries: list[str] = []

    # ⛔ BOTH verbs answer through `_answer`, because the census must issue its
    # reads through `ro_query` (the engine's read-only verb) and a fake that
    # implemented only the write-capable `query` would make a revert invisible.
    # Subclasses override `_answer`, never one verb, so neither can go untested.
    def query(self, cypher, params=None, timeout=None):
        return self._answer(cypher)

    def ro_query(self, cypher, params=None, timeout=None):
        return self._answer(cypher)

    def _answer(self, cypher):
        self.queries.append(cypher)
        if "db.indexes()" in cypher:
            rows = self._catalogue
        elif "db.labels()" in cypher:
            rows = [[lab] for lab in self._labels]
        elif "RETURN count(n)" in cypher:
            label = cypher.split("MATCH (n:")[1].split(")")[0].strip("`")
            rows = [[self._vectors.get(label, (0, None))[0]]]
        elif "RETURN n.embedding" in cypher:
            label = cypher.split("MATCH (n:")[1].split(")")[0].strip("`")
            samples = self._vectors.get(label, (0, None))[1]
            # A LIST OF VECTORS is several sampled rows (the width probe reads
            # more than one); a single vector is one row. Without this a test
            # could not model the mixed-width label `WIDTH_PROBE_SAMPLES`
            # exists to refuse (#11088 review round 7, P2).
            if samples and isinstance(samples[0], list):
                rows = [[v] for v in samples]
            else:
                rows = [[samples]]
            # ⛔ HONOUR THE `LIMIT` the production query issues. Returning every
            # sample regardless made the mixed-width test pass with
            # `WIDTH_PROBE_SAMPLES = 1` — i.e. with the exact single-row bug it
            # exists to catch — so the test could not fail on a regression
            # (review round 9, P2: a verification gate with no discriminating
            # power).
            limit = cypher.rsplit("LIMIT", 1)[-1].strip()
            if limit.isdigit():
                rows = rows[: int(limit)]
        else:
            rows = []
        return type("_R", (), {"result_set": rows})()


def _index_row(label, field, dim):
    return [
        label,
        [field],
        {field: ["VECTOR"]},
        {field: {"dimension": dim}},
        "english",
        [],
        "NODE",
        "OPERATIONAL",
        {},
    ]


def test_the_fallback_probe_restores_the_circuit_breakers():
    """A read-only probe must not un-trip a breaker a live process opened.

    Pinned because the FIRST attempt at this restore (`deepcopy` of
    `_BREAKERS`) raised `TypeError: cannot pickle '_thread.lock' object` the
    moment a breaker was registered — i.e. in exactly the state it existed to
    protect, and only in a process that had already run a real search. A fresh
    pytest process hid it.
    """
    from tortoise.search_engine import _breaker

    b = _breaker("vector")
    b.record_failure()
    before = (b._fails, b._open_until, b._probing)
    probe.fallback_probe()
    after = (b._fails, b._open_until, b._probing)
    assert after == before, (before, after)


def test_the_fallback_probe_leaves_no_breaker_it_CREATED_behind():
    """A FRESH process starts with an EMPTY `_BREAKERS` — leave it empty.

    The other direction is `…_restores_the_circuit_breakers` above, and it is the
    only state that direction can see: it PRE-CREATES the breaker, so the probe's
    snapshot is non-empty. A fresh process has no such entry — that is the CLI's
    state, `measure()`'s state, and this file's first test's — so the restore
    iterated an empty snapshot and the breaker `_probe_mechanisms` registers
    lazily (`_breaker_record("vector", …)` → `search_engine._breaker`) was never
    restored by anything.

    Measured at `37fa883bd` before the fix (review P3, reproduced verbatim):
    `before: {}` → `after: {'vector': (0, 0.0, False)}` — an entry this read-only
    instrument created and left in the caller's module. The slot values alone
    would not have caught it, which is why the assertion is over the MAP.
    """
    import tortoise.search_engine as se

    saved = dict(se._BREAKERS)
    se._BREAKERS.clear()
    try:
        probe.fallback_probe()
        after = {name: (b._fails, b._open_until, b._probing) for name, b in se._BREAKERS.items()}
        assert after == {}, after
    finally:
        se._BREAKERS.clear()
        se._BREAKERS.update(saved)


def test_a_backtick_in_a_graph_label_cannot_re_purpose_the_census_query():
    """A label is QUOTED by the repo's identifier rule, never concatenated raw.

    `label` comes from `CALL db.labels()` / the index catalogue — the graph's own
    schema, i.e. graph-controlled input reaching Cypher by interpolation (a label
    cannot be a query parameter). The pre-fix `f"MATCH (n:`{label}`) …"` let a
    label carrying a backtick close the quoted identifier and append Cypher of
    its own. The repo's one rule for this shape is
    `graph_key_restore._quote_identifier` (bare when bare, else backtick-quoted
    with interior backticks DOUBLED) — round 15 review, P3.

    The payload is EXECUTED through the census rather than read off a source
    line, so a later rewrite that stops quoting reds here instead of shipping.
    """
    hostile = "x`) DETACH DELETE n //"
    emitted: list[str] = []

    class _Hostile(_CensusGraph):
        def _answer(self, cypher):
            emitted.append(cypher)
            return super()._answer(cypher)

    probe.vector_census(_Hostile([], [hostile], {}), "g", at="t", graph_names=["g"])
    counts = [q for q in emitted if "RETURN count(n)" in q]
    target = [q for q in counts if "x" in q]
    assert target, counts
    quoted = "`" + hostile.replace("`", "``") + "`"
    assert quoted in target[0], (
        f"the label reached Cypher unquoted — the payload's backtick closes the "
        f"identifier: {target[0]}"
    )
    # ⛔ The RAW form is what closed the identifier, and it is emitted nowhere:
    # the payload's backtick exists only inside a doubled pair.
    assert f"`{hostile}`" not in " ".join(counts), counts
    # …and the payload's own backtick is the ONLY doubled one, so the doubling is
    # the escape and not an artefact of the payload happening to contain it.
    assert target[0].count("``") == 1, target[0]


def test_the_census_reads_through_the_READ_ONLY_verb():
    """The census reads through `GRAPH.RO_QUERY`, not the write-capable verb.

    "This module stores nothing" was a convention held only by writing
    read-shaped statements through `graph.query` — the verb that CAN write.
    `ro_query` is rejected server-side for a write, so the doctrine becomes the
    engine's to enforce (round 15 review, P3). A fake that implemented only
    `query` could never show this: the handle below REFUSES `query` and the
    census must still measure it.
    """

    class _WriteVerbForbidden(_CensusGraph):
        def query(self, cypher, params=None, timeout=None):
            raise AssertionError(f"the census read through the write-capable verb: {cypher}")

    row = probe.vector_census(
        _WriteVerbForbidden(
            catalogue=[_index_row("Point", "embedding", 384)],
            labels=["Point"],
            vectors={"Point": (3, [0.0] * 384)},
        ),
        "g",
        at="2026-11-01T00:00:00Z",
        graph_names=["g"],
    )["labels"]["Point"]
    assert row["stored"] == 3, row
    assert row["indexed"] is True, row
    assert row["payload"]["derived_payload_B"] == 3 * 384 * 4, row
