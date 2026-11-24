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
| `test_row_two_MEASURES_the_cache_projection_by_running_it` + `test_re_introducing_the_session_vector_flips_row_two_to_NOT_MET` | row 2 reporting `MET` over a derivation that measured NOTHING — every surviving row-2 implication is satisfied by the empty set (`∅ <= served`), so the same `stored-without-a-query-path: none` prints for a graph full of orphaned vectors |
| `test_the_census_has_no_DUPLICATE_dict_key_in_its_source`, `test_no_module_declares_the_same_dict_key_twice`, `test_the_duplicate_key_detector_catches_a_duplicate`, `test_the_scan_REPORTS_a_duplicate_it_finds`, `test_the_scan_root_is_the_REPO_not_the_process_cwd` | a duplicated dict-literal key in `WRITER_CENSUS` / `CENSUS_EVIDENCE` / `WRITER_LABEL_OVERRIDES` shadowing a `bypass` row — LAST-WINS, so a dead declaration merges green and row 2 reads a class that no longer exists |
| `test_a_receiver_that_only_CONTAINS_the_seam_name_is_not_a_reach`, `test_a_receiver_REBOUND_in_the_writers_scope_is_not_a_reach` | `_seam_module_calls` crediting a receiver that never reaches `encode_for_row` — its writer leaves `NEEDS_A_DECLARATION`, so the census reports the gate as complete over a write that never met it |
| `test_every_aliasable_name_has_a_canonical_order_entry` | `_alias_preference`'s `# pragma: no cover` ValueError arm going live, and a reordered tuple silently republishing a `callee` name a route claim is re-derived from |

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

The row-2, duplicate-key and receiver families were restored the same way, one
review round later: each one's failure mode is a printed verdict over an
artifact nobody measured (the empty set satisfying every row-2 implication, a
LAST-WINS duplicate declaration, a writer credited as gated that never reached
the gate). See the RESTORED AFTER REVIEW block at the end of this file.

⛔ These tests never write a graph and never flip a gate: the instrument is
read-only and its pin must be too.
"""

from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

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

#: ══════════════════════════════════════════════════════════════════════════════════════════════
#: RESTORED AFTER REVIEW (PR #14544). The arms below were cut with the B inventory and the reviews
#: measured that each protects a FAIL-OPEN direction (AGENTS.md §Product Over Process → A), not an
#: editorial one, so they are back verbatim from `origin/main`:
#:
#:   * `#11747` — `test_row_two_MEASURES_the_cache_projection_by_running_it` +
#:     `test_re_introducing_the_session_vector_flips_row_two_to_NOT_MET`. Without them every
#:     surviving row-2 assertion is satisfied by the EMPTY set (`∅ <= served` is true, `∅` is
#:     falsy), so a derivation that measured nothing reported the row `MET`.
#:   * `#12182` / `#12265` — the duplicate-dict-key detector and its four scan arms. A duplicated
#:     dict-literal key is LAST-WINS, so one declaration silently shadows another and row 2 reads a
#:     `bypass` row that no longer exists; `ruff`'s `F601` is suppressible and is not the pytest
#:     lane's catcher.
#:   * `#13447` — the two receiver arms. `_seam_module_calls` decides which writers the census
#:     credits with a gated reach of `encode_for_row`; crediting a receiver that never reaches the
#:     gate takes its writer OUTSIDE `NEEDS_A_DECLARATION`, i.e. the census reports the gate as
#:     complete over a writer that never met it.
#:   * `#13447` — `test_every_aliasable_name_has_a_canonical_order_entry`, which keeps the
#:     `# pragma: no cover` on `_alias_preference`'s ValueError arm honest and a published `callee`
#:     from moving with a tuple reorder.
#: All of them are hermetic (`tmp_path` / AST only, no DB, no graph, no network).
#: ══════════════════════════════════════════════════════════════════════════════════════════════


#: ── `#11747`: the write path the DISCOVERY cannot see ─────────────────────────────────
#: `cache_projection.node_statement` stores its vector through an INTERPOLATED property name
#: (`f", n.{prop} = {form}"`), which is one of the shapes `_EMBED_WRITE` and the AST pass cannot
#: decide — so the writer was ABSENT from `embedding_writers()` and a `:Session` vector reached the
#: graph while this row printed `MET` / `stored-without-a-query-path: none`. The tests below are that
#: hole's closure: the first pins that the row MEASURES the projection at all (and that the
#: measurement is non-vacuous); the second that emptying the guard turns the row RED; the third that
#: the probe's THIRD (directly-built `RecordNode`) door is measured; the fourth that the PLANTED
#: property set is derived rather than the literal `embedding`; and the fifth that an unbuildable
#: label HOLDS the row instead of crashing it.


def test_row_two_MEASURES_the_cache_projection_by_running_it():
    """`#11747` — measured by EXECUTING the writer, because reading it is the blind spot.

    ⚠️ Non-vacuity is the load-bearing half. A derivation that simply returned nothing (or that
    the import broke) would satisfy `labels <= served` on the live tree while seeing no store at
    all — the fail-open shape every row in this file refuses. So the assertion is that the
    projection DOES store vectors, that every label it stores on is served, and that `Session`
    is not among them.
    """
    measured = probe.cache_projection_store_labels()
    assert measured["error"] is None, measured["error"]
    assert measured["unmeasured"] == {}, measured["unmeasured"]
    assert measured["labels"], (
        "the projection reports NO vector store at all — the probe is measuring nothing, so a "
        "`labels <= served` assertion below would pass vacuously")
    assert measured["labels"] <= probe.served_labels(), (
        sorted(measured["labels"] - probe.served_labels()))
    assert "Session" not in measured["labels"]
    assert probe.score_path_bullet().verdict == "MET"


def test_re_introducing_the_session_vector_flips_row_two_to_NOT_MET(monkeypatch):
    """The falsifier: delete the guard and this row must go RED by itself.

    ⛔ Mutating the GATE (not the probe's inputs) is the point: it exercises the real derivation
    path end to end — `node_from_row` → `node_statement` → `stmt.params` → served-set comparison
    — so the test reds on the failure a future lane would actually introduce, rather than on a
    mock the probe was told to believe.
    """
    import tortoise.cache_projection as cp

    monkeypatch.setattr(cp, "UNSERVED_VECTOR_LABELS", frozenset())
    assert "Session" in probe.cache_projection_store_labels()["labels"]
    row = probe.score_path_bullet()
    assert row.verdict == "NOT MET", row.observed
    assert "stored-without-a-query-path: ['Session']" in row.observed


# ── the census half's duplicate-key detector (`#12182` review cycle 7, P2) ────────────────────────
def test_the_census_has_no_DUPLICATE_dict_key_in_its_source() -> None:
    """`WRITER_CENSUS` / `CENSUS_EVIDENCE` / `WRITER_LABEL_OVERRIDES` must not declare a key twice.

    ⛔ WHY THIS EXISTS, NOT AS ADVICE BUT AS A DETECTOR (`#12182` review cycle 7, P2). The register
    half catches a duplicate itself: `load()` raises `DuplicateWriterDeclaration`, and because
    `EMBED_WRITER_CLASSES = load(_DECLARATIONS)` runs at import, a merged tree carrying two entries
    for one key cannot even be imported. Measured on PR #12182: `main`'s `#12016` and that branch each
    added a `tortoise.sdk::create_source` declaration, both branches were green, and the forward merge
    produced a duplicate that the guard rejected at import — the failure was CAUGHT only there.

    The census half has no such guard. A dict literal accepts a repeated key silently and keeps the
    LAST entry, so the same merge shape here does not fail — it quietly selects one of two
    contradicting reasons, and `WRITER_CENSUS`'s class is what row-2 scoring reads (`bypass` /
    `real_bypasses`). Measured: appending a second `create_source` row still imports, still has 23
    keys (re-read at this tree, `#12182` cycle 9 — the figure read 22 on the tree that authored the
    sentence, and `main`'s merge has added rows since), and leaves the census↔evidence key-set
    equality green, with the appended — false — prose as the survivor.

    Read from SOURCE, not from the imported dicts: the import has already collapsed a duplicate, so
    the evidence it could report is gone by the time this test could look at it.
    """
    import ast

    src = Path(probe.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    wanted = {"WRITER_CENSUS", "CENSUS_EVIDENCE", "WRITER_LABEL_OVERRIDES"}
    seen: dict[str, list[str]] = {}
    # ⛔ MODULE TOP LEVEL ONLY (`tree.body`), NOT `ast.walk`. These three tables are module-level
    # declarations, so a top-level scan is both sufficient and the only scope where "the table" is a
    # single object: a walk also visits a same-named literal inside a function, and that literal is
    # not the table.
    # ⚠️ What this scope is NOT: a duplicate fail-open fix. `assert not dupes` below fires PER
    # VISITED NODE, so a duplicate in the real table reds whether or not a decoy literal exists —
    # re-measured on the pre-`tree.body` version: real duplicate plus a 23-key decoy → RED, not
    # clean. `seen` is last-wins, so a decoy CAN shadow the TRAILING checks that read it — the
    # key-count comparison against the runtime dict AND the per-literal key floors below — while a
    # decoy naming a table ABSENT from the top level also satisfies the vacuity `set(seen) == wanted`.
    # Narrowing the scan removes that window; it does not close a duplicate fail-open, because there
    # was none. What a walk DOES get wrong in the other direction: a 5-key function-local literal
    # bearing the same name FALSE-BLOCKS on the floors below (measured), so the narrowing is not
    # cosmetic either.
    # ⚠️ And this is a DIRECTIVE, not a pin: restoring `ast.walk` leaves this file GREEN (measured),
    # so nothing reds if a later lane widens it back. Re-derive the reason before changing the scope.
    for node in tree.body:
        if not isinstance(node, ast.AnnAssign | ast.Assign):
            continue
        targets = [node.target] if isinstance(node, ast.AnnAssign) else list(node.targets)
        names = [t.id for t in targets if isinstance(t, ast.Name) and t.id in wanted]
        if not names or not isinstance(node.value, ast.Dict):
            continue
        # ⛔ A key that is NOT a string literal (an alias like `_ALIAS_KEY = "…"` used as a key, a
        # splat, any computed expression) must FAIL here rather than be filtered out. Filtering made
        # the same name declared twice under one literal and once behind the alias collapse to "no
        # duplicate" in BOTH checks below — the alias is skipped, and the literal-vs-runtime count
        # still agrees, because the runtime dict collapses identically. Measured on a copy of this
        # tree (`#12182` cycle 10): `_ALIAS_KEY = "tortoise.api::_emit"` plus `_ALIAS_KEY: (…)` in
        # `WRITER_CENSUS` stayed GREEN here while `main`'s dropped `#9314` pin, which called
        # `ast.literal_eval`, raised on the same file. That is the one shape where this detector was
        # NARROWER than the pin it replaced: fail on it, never re-filter it.
        keys: list[str] = []
        for k in node.value.keys:
            assert isinstance(k, ast.Constant) and isinstance(k.value, str), (
                f"{names[0]} carries a key that is not a string LITERAL ({type(k).__name__}). This "
                "pin reads literal keys, so an aliased or computed key is invisible to it — and a "
                "duplicate hidden behind one is exactly the fail-open this detector exists to stop "
                "(#9314). Spell the key as a literal, or re-derive the detector")
            keys.append(k.value)
        dupes = sorted({k for k in keys if keys.count(k) > 1})
        assert not dupes, (
            f"{names[0]} declares the same key more than once: {dupes}. A dict literal keeps the "
            "LAST silently, so the earlier entry's reason is discarded with nothing red — which is "
            "how a forward merge picks the wrong copy. Delete the stale row and say in a comment "
            "which one survived and why")
        seen[names[0]] = keys
    # A vacuous pass is the failure mode of a source-reading test: if none of the three literals was
    # found AT TOP LEVEL, nothing was checked. All three exist there today, so a MISS means a table
    # was moved into a scope this detector no longer reads — re-derive it deliberately, do not widen
    # it back to `ast.walk` (a decoy literal would then also feed `seen`, masking the vacuity check
    # below and the floor/count assertions that read it, and a smaller one would false-block on the
    # floors).
    assert set(seen) == wanted, (
        f"these census dict literals were not found in {probe.__file__}: "
        f"{sorted(wanted - set(seen))} — the detector checked nothing for them")
    # …and a literal found with a HANDFUL of keys is the same vacuity one level down: the walk above
    # would happily report "no duplicates" over a population that is not the real table. The floors
    # are well under the measured sizes (23 / 24 / 1 at this tree) on purpose — this is a
    # still-the-real-table check, not a pin on a count that moves with every merge.
    floors = {"WRITER_CENSUS": 10, "CENSUS_EVIDENCE": 10, "WRITER_LABEL_OVERRIDES": 1}
    thin = {n: len(keys) for n, keys in seen.items() if len(keys) < floors[n]}
    assert not thin, (
        f"these census literals parsed to fewer literal keys than the real tables carry: {thin} "
        f"(floors {floors}) — the detector would be checking a fragment, not the table")
    # …and the collapsed dict the rest of the probe actually uses must still agree on size, so a
    # delete-one-of-two edit cannot silently shrink the population instead of de-duplicating it.
    for name in sorted(wanted):
        assert len(seen[name]) == len(getattr(probe, name)), (
            f"the {name} literal and the imported dict disagree on their key count — a literal key "
            "is being shadowed by something other than a duplicate")
# ── #12265 (root: #9314) — a duplicated declaration key is a SILENT loser ────
#
# `#11996` landed two duplicate `"tortoise.sdk::create_source"` rows in
# `tools/dod_5090_probe.py`'s census dicts and a third in
# `tortoise/embed_writer_register.py`. The two DICT rows were silently resolved by
# Python's last-wins semantics — the earlier entry, the one a review had pinned,
# simply stopped existing, and no test went red FOR THEM (the register's third copy
# is what red the tree, at import — below). ⛔ The duplicate was born AT THE MERGE,
# not inside either PR: each PR's own tree held ONE row (`#12016` landed at 20:43Z;
# `#11996`'s base predated it), so no run of either PR could see two.
# `main-lint.yml`'s ruff gate (`#2174`, `on: push: branches: [main]`) then red the
# pushed tree — `F601` twice, 16 minutes after the merge — and that lint job was the
# whole detection this class had. The register's own copy raised
# `DuplicateWriterDeclaration` at IMPORT, which is loud but is a different shape (a
# tuple of pairs, checked by `load()` — see
# `tests/test_embed_writer_register.py::test_a_duplicate_key_raises`).
#
# So this is the DICT half: one scan, run wherever a diff SELECTS this module. ⛔ Not
# on every tree that holds a duplicate — selection is the real bound, and it is
# NARROWER than "every file under the scanned roots". Measured per file on the tree that
# measured them, with `select([p], "pull_request")` over every module the scan walks:
#
#   * `tools/` — 140 of 195 DO reach it; 55 do not (measured:
#     `select(["tools/min_signal.py"], "pull_request")` → 91 test files, this one
#     absent — a `tools/`-only diff matching NO pattern is swallowed by
#     NON_PYTHON_PREFIXES before the `core` fallback runs, and
#     `tools/merge_throughput.py` measures the same 91; `tools/ask_spotcheck.py`
#     misses by the OTHER mechanism (`tools/ci_selection.py:1147` makes it an `sdk`
#     named surface, which REPLACES the fallback → 198 test files, this one absent)).
#     ⛔ Do NOT name `tools/who_is_on.py` here — this bullet did, and it is the
#     OPPOSITE of a miss: an UNCLASSIFIED path selects in FULL, so this test DOES run
#     on that diff (measured: `full=True`, `test_files == "ALL"`). `tools/claim_lanes.py`
#     is not a file in this repo (agent-infra's is `tools/fleet/claim_lanes.py`), so it
#     could not have been the one measured. `tools/dod_5090_probe.py`
#     itself does — it is a `CORE_ALSO` entry in `tools/ci_selection.py` (the
#     instrument has to be selected by its own subject; 833 test files, this one
#     PRESENT) — which is the file `#11996`'s duplicate lived in, and why a
#     duplicate born there at a merge IS caught. It is pinned by
#     `test_the_instrument_is_selected_by_its_own_subjects` in this module.
#   * `tortoise/` — 31 of 287 do NOT reach it (`tortoise/ranking.py` → `ep`,
#     `tortoise/hosted_api.py` → `api`+`onboarding`, `tortoise/reader.py` → `sdk`, …).
#     The mechanism is the one this module already documents: a named-surface match
#     REPLACES the `core` fallback that would otherwise sweep this file in.
#   * `tests/` — 1,493 of 1,494 reach it; the exception is `tests/_html_links.py`
#     (`onboarding`).
#
# So the honest bound is per root and NOT a rule you can generalise: any full-lane run
# (push and schedule select in FULL, which is what makes the born-at-merge case
# catchable), plus a per-file `pull_request` selection that covers every `tests/`
# module but one, 140 of 195 `tools/` modules (`tools/dod_5090_probe.py` — the file
# `#11996`'s duplicate lived in — among them), and 256 of 287 `tortoise/` modules.
# Anything outside that is lint's alone.

#: Roots the declaration dicts live under today. ⛔ NOT the whole repo: `battery/`,
#: `graph-scripts/`, `docs/`, `eval/` and several smaller roots sit outside these three,
#: so this scan does not reach a duplicate declared there. Stated rather than called
#: "repo-wide" because the difference is what a reader would rely on.
#:
#: ⛔ Outside these roots a duplicate is not reliably caught by LINT either: `ruff`'s
#: `F601` lands on the repeated (surviving) key, and a `noqa` marker naming `F601` on
#: that line suppresses it — which is exactly the live shape this change found and
#: removed in `tests/test_commit_schema.py`. So "lint would still catch it there" is
#: false for the one shape we have actually observed.
#:
#: ⛔ THE CANONICAL ALTERNATIVE, stated so this scan is not read as the only catcher
#: and not read as a substitute for the linter: the standard answer to a suppressible
#: rule is to run the rule UNSUPPRESSED — `ruff check --select F601 --ignore-noqa`
#: (measured on this tree: `All checks passed`, and no `noqa` directive naming F601
#: survives to un-suppress, so it costs nothing). What this scan adds is what lint
#: cannot: a red that no marker can defuse, one that runs in the pytest shard without
#: depending on the lint step's selection, and one whose RED arm is provable by a test
#: — so it is a COMPLEMENT to lint, never a replacement. ⛔ That step must stay
#: rule-SCOPED: a repo-wide `--ignore-noqa` would un-baseline the WHOLE suppression
#: baseline — measured on the tree this note was written on, **4,452** suppressed
#: findings (the 242 markers `#1685` added on 2026-08-25 are where it started, not the
#: size it reached) — i.e. it would reverse that decision. Do not propose it.
#:
#: (⛔ Neither sentence above spells the suppressive marker prefix itself: a comment
#: whose text contains that prefix followed by the word `noqa` is PARSED by ruff as a
#: directive, so prose about suppression emits "invalid directive" warnings on exactly
#: the cache-cold lint run that is supposed to report findings — measured: this paragraph
#: cost 2 spurious warnings in its first draft.)
#:
#: ⛔ NO FILE COUNT IS ASSERTED, here or for the roots outside. A count is a
#: measurement of the tree it was taken on: a draft of this comment said "254", and by
#: the time the text landed the tree held 255 outside these roots, in directories the
#: enumeration had also missed — the drift class this very comment is about. The
#: figures elsewhere in this comment are bound to the tree that measured them. The
#: population an assertion should pin is the one it ENFORCES (`_MIN_MODULES` below),
#: not a figure in prose.
#:
#: It is one scan over ALL three roots on purpose: the class is not specific to the
#: probe, and naming files here would re-open the hole the moment a declaration moves
#: into one not named.
#:
#: ⛔ IT IS NOT FREE, measured on the tree that measured it: 1,976 modules (`tortoise/` 287,
#: `tools/` 195, `tests/` 1,494), **19.1 s** of `ast.parse`+walk, inside a file whose
#: own run is ~2 min. Recorded so the next reader does not assume it is cheap — and so
#: a future budget discussion has a figure. There is no sound cheap pre-filter:
#: narrowing the roots narrows the guard, which is the hole the scan exists to close.
_DUP_KEY_ROOTS = ("tortoise", "tools", "tests")

#: Per-root FLOOR for the scan's own non-vacuity check, deliberately per root and not
#: as an aggregate: measured 287 / 195 / 1,494 on the tree above, so an aggregate floor
#: (>1,000) still passes if the `tortoise/` AND `tools/` walks silently yield nothing —
#: and `tools/` is exactly where this class's original instance lived. Set well under
#: what each root holds so ordinary churn does not red it, but non-zero, so a renamed or
#: vanished root fails instead of quietly narrowing the guard.
_MIN_MODULES = {"tortoise": 100, "tools": 50, "tests": 500}


class _JoinedKey:
    """An F-STRING dict key that CARRIES a placeholder, compared by its source.

    Two byte-identical f-string keys are ONE key at runtime for any referentially
    transparent expression (shown by `ruff`'s `F601`, measured:
    `{f"{x}": 1, f"{x}": 2}`), and `ast.unparse` normalises quoting, so `f"{x}"` and
    `f'{x}'` are one key here as they are at runtime.
    ⛔ The transparency qualifier is MEASURED, not caution: `c = iter(["a", "b"]);
    D = {f"{next(c)}": 1, f"{next(c)}": 2}` builds `{'a': 1, 'b': 2}` — two DISTINCT
    keys — while this arm reports a duplicate. That is a false RED, never a false
    PASS, and `ruff 0.16.4 --select F601` flags the same shape (verified), so the
    guard is not weaker than the lint it backs; skipping impure JoinedStrs would
    diverge from the oracle, so the residual is stated instead.

    ⛔ THIS CLASS IS REACHED ONLY BY A PLACEHOLDER-BEARING F-STRING. A placeholder-free
    one (`f"a"`, `f""`) is FOLDED to the plain `str` key CPython compiles it to,
    inside `_constant_key_value` — and that distinction is the difference between a
    FALSE PASS and a caught duplicate. `{f"a": 1, "a": 2}` IS one key at runtime
    (`{'a': 2}`), it needs NO evaluation to prove (CPython has already dropped the
    `FormattedValue`s, so the key is the `str` constants joined in order), and `ruff
    0.16.4` does not flag that CROSS-FORM pair (measured; it flags only the
    same-source `{f"a": 1, f"a": 2}`) — so an arm that refused to compare it caught
    nothing on the one shape whose only other catcher can be a `noqa` on the
    survivor, while reading as a deliberate limit. The earlier text here said proving
    it "needs evaluation"; that was FALSE, and it is why the false PASS survived a
    review round. Only a PLACEHOLDER-BEARING f-string genuinely needs evaluation to
    be equated with a `Constant`, and that is the comparison refused here.
    """
    __slots__ = ("text",)

    def __init__(self, text: str) -> None:
        self.text = text

    def __hash__(self) -> int:
        return hash((_JoinedKey, self.text))

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _JoinedKey) and other.text == self.text

    def __repr__(self) -> str:
        return f"f-string key {self.text}"


def _constant_key_value(node: ast.expr) -> tuple[bool, object]:
    """`(True, value)` when the key is a literal Python can hash — else `(False, None)`.

    Recurses through a TUPLE literal, because `{(1, 2): …}` is a real declaration
    shape (`ruff`'s `F601` catches it; measured, 390 tuple-literal dict keys under
    these roots) and a `Constant`-only test would silently skip it — a hole in a
    guard that sells itself as closing holes. Only the 308 of those whose elements
    are ALL literal are compared; the other 82 carry a computed element and are
    skipped like any other non-literal key. Element values are compared by Python's
    own `==`/hash, so `(1, 2)` and `(True, 2)` are ONE key, exactly as a dict treats
    them.

    Recurses through a SIGNED numeric literal too (`{-1: …}`, `{+1: …}`, and
    `{(-1, 2): …}` via the tuple arm), because `ruff`'s `F601` flags all three and
    `{+1: 'a', 1: 'b'}` is a real duplicate — Python collapses it to one key. A
    `Constant`-only reading would skip the class, and a skipped duplicate is one that
    no lane catches at all when the survivor carries a `noqa` marker.

    An F-STRING key is read too, in the two arms its own AST separates:

      * PLACEHOLDER-FREE (`f"a"`, `f""`) is folded to the `str` key CPython compiles it
        to, so `{f"a": 1, "a": 2}` — one key at runtime, `{'a': 2}` — is caught. This
        FOLD is the repaired arm: the scan used to answer `[]` for it, justified by a
        claim that proving the equality "needs evaluation". That claim was FALSE (the
        `ast` has no `FormattedValue` left to evaluate) and `ruff 0.16.4 --select F601`
        does not flag the cross-form pair either, so the guard was WEAKER than nothing
        on it: a FALSE PASS nobody else caught.
      * PLACEHOLDER-BEARING (`f"{x}"`) is compared by SOURCE (`_JoinedKey`), because
        equating that with a `Constant` genuinely does need evaluation. The same-source
        duplicate was MEASURED missing by a fresh review — the scan reported `[]` where
        `ruff --select F601` reported a duplicate.
    """
    if isinstance(node, ast.Constant):
        return True, node.value
    if isinstance(node, ast.JoinedStr):
        # A placeholder-free f-string is a CONSTANT FOLD, not a computation: `ast` has
        # already removed every `FormattedValue`, so the key is the `str` constants
        # joined in order. Reading it is the difference between catching
        # `{f"a": 1, "a": 2}` (`{'a': 2}` at runtime, and NOT flagged by `ruff`) and
        # missing it.
        if all(isinstance(v, ast.Constant) and isinstance(v.value, str)
               for v in node.values):
            return True, "".join(v.value for v in node.values)
        # A placeholder-bearing f-string cannot be equated with a `Constant` without
        # evaluating it (see `_JoinedKey`), so it is compared by SOURCE — and two
        # byte-identical sources are one key for a referentially transparent
        # expression, which is exactly what `ruff` reports.
        return True, _JoinedKey(ast.unparse(node))
    if isinstance(node, ast.Tuple):
        values = []
        for element in node.elts:
            ok, value = _constant_key_value(element)
            if not ok:
                return False, None
            values.append(value)
        return True, tuple(values)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        ok, operand = _constant_key_value(node.operand)
        # Numeric only: `-`/`+` on anything else is a TypeError at runtime, so there
        # is no key to compare. `bool` is an `int` here on purpose — `+True` IS `1`,
        # so `{+True: 'a', True: 'b'}` is a real duplicate.
        if ok and isinstance(operand, (int, float, complex)):
            return True, (-operand if isinstance(node.op, ast.USub) else +operand)
    return False, None


def _duplicate_constant_key_sites(
        source: str | bytes, filename: str = "<string>") -> list[tuple[int, int, str]]:
    """`(first_lineno, duplicate_lineno, key)` for every duplicated dict-literal key.

    The `seen` map is keyed on the VALUE, not on a type-prefixed spelling of it, so
    the comparison IS Python's own: `1`, `1.0` and `True` collapse to ONE key (they
    hash equal), `None` is a key like any other, and `b"a"` stays distinct from
    `"a"`. A type-prefixed marker would read `{1: 'a', 1.0: 'b'}` as two keys and
    miss a real shadow — the exact class this guard exists for (found in this
    guard's own first draft by a fresh review).

    Only LITERAL keys can be compared without evaluating anything: a computed key
    (`SOME_NAME`) is not a declaration this can read, and guessing would report a
    duplicate that is not one. An F-STRING key is read in its two arms, and neither is
    a guess: a PLACEHOLDER-FREE one (`f"a"`, `f""`) is the `str` constant CPython
    folds it to, and a PLACEHOLDER-BEARING one is compared by SOURCE — two byte-identical
    sources are ONE key for a referentially transparent expression (`_JoinedKey`; an
    impure one is a stated false-RED residual there, and `ruff` shares it), which is
    also exactly what `ruff`'s `F601` reports.
    `**splat` in a
    literal parses as a `None` entry in
    `node.keys`, not a Constant, so it is skipped rather than mistaken for the key
    `None`; `_constant_key_value` recurses through tuple and signed-numeric literals
    so a fully-literal `{(1, 2): …}` is compared too (390 tuple-literal keys under
    these roots, 308 of them fully literal).

    ⚠️ Still outside this population, stated so it is not mistaken for a total, AND
    stated as a HOLE rather than as parity with the lint: a COMPUTED key (`SOME_NAME`),
    a PLACEHOLDER-BEARING f-string compared against a DIFFERENT expression or against a
    plain literal — that comparison needs the evaluation this refuses — any tuple
    containing one, and a key built by an operator outside `USub`/`UAdd`. ⛔ The
    placeholder-bearing cross-form pair is NOT harmless: `{f"{'a'}": 1, "a": 2}` is
    `{'a': 2}` at runtime, its first entry is DEAD, and BOTH this scan and
    `ruff 0.16.4 --select F601` report nothing for it (measured). The earlier text here
    supported the exclusion with `{f"{x}": 1, "{x}": 2}`, which is a near-non-example
    (`x` would have to evaluate to the literal string `"{x}"`), so a reader testing the
    boundary that way concluded the residual was unreachable. It is reachable; closing
    it needs evaluating the placeholder, which is the one step this function does not take.
    ⛔ The PLACEHOLDER-FREE cross-form pair, `{f"a": 1, "a": 2}`, USED to be listed
    here as "needs evaluation" — that was FALSE and it hid a duplicate no other
    instrument reported (see `_constant_key_value`); it is IN this population and is
    pinned by its own case below.

    ⚠️ `filename` is threaded into `ast.parse` so a `SyntaxError` carries the module's
    REAL path rather than `<string>` — it does NOT affect any message. The
    UNPARSEABLE text is built by `_scan_declaration_roots` from `rel` and `exc.msg`,
    so dropping this argument leaves every message byte-identical (measured by
    mutation: removing `filename=filename` keeps all three tests green, and the
    `{rel}` PREFIX is what makes an unparseable module name ITSELF instead of
    `<unknown>` — the "a hole is a hole, not a zero" rule this file's
    predicate-count test states). Stated so the next reader does not mistake an
    inert argument for the mechanism.

    ⛔ `source` may be BYTES on purpose: the scan hands it the file's raw bytes, so
    CPython's own source decoding applies (a PEP 263 coding cookie and a UTF-8 BOM are
    legal source). Decoding as UTF-8 first would report a file the interpreter reads
    fine as a hole — measured: a `latin-1` cookie read `UNREADABLE (UnicodeDecodeError)`
    and a BOM read `UNPARSEABLE (U+FEFF at line 1)`, both of which `ast.parse(bytes)`
    accepts.
    """
    sites: list[tuple[int, int, str]] = []
    for node in ast.walk(ast.parse(source, filename=filename)):
        if not isinstance(node, ast.Dict):
            continue
        seen: dict[object, int] = {}
        for key in node.keys:
            if key is None:  # `**splat`, not a key literal
                continue
            ok, value = _constant_key_value(key)
            if not ok:
                continue
            if value in seen:
                sites.append((seen[value], key.lineno, repr(value)))
            else:
                seen[value] = key.lineno
    return sites


def test_the_duplicate_key_detector_catches_a_duplicate():
    """The detector must be able to come back RED, or the scan below proves nothing.

    This module's own contract (see the file docstring): an instrument that can
    only print `MET` is machinery. A guard whose scan silently returned `[]` for
    every input would pass the assertion below forever.
    """
    source = (
        "DECLARATIONS = {\n"
        "    'a::b': ('first', 1),\n"
        "    'c::d': ('other', 2),\n"
        "    'a::b': ('second', 3),\n"
        "}\n"
    )
    assert _duplicate_constant_key_sites(source) == [(2, 4, "'a::b'")]
    # …and a clean map reports nothing, so the positive case above is the ONLY
    # reason a red is possible.
    assert _duplicate_constant_key_sites("D = {'a': 1, 'b': 2}\n") == []
    # A computed key is not a declaration this reads: no false positive.
    assert _duplicate_constant_key_sites("D = {K: 1, K: 2}\n") == []
    # The comparison must BE Python's own key equality, in the MISS direction too:
    # `1`, `1.0` and `True` are ONE key, so a numeric/alias shadow must be caught
    # (a type-prefixed marker misses it — this guard's own first draft did).
    assert _duplicate_constant_key_sites("D = {1: 'a', 1.0: 'b'}\n") == [(1, 1, "1.0")]
    assert _duplicate_constant_key_sites("D = {1: 'a', True: 'b'}\n") == [(1, 1, "True")]
    assert _duplicate_constant_key_sites("D = {None: 1, None: 2}\n") == [(1, 1, "None")]
    # …while genuinely distinct keys stay silent: bytes are not text, and a
    # `**splat` is not the key `None`.
    assert _duplicate_constant_key_sites("D = {b'a': 1, 'a': 2}\n") == []
    assert _duplicate_constant_key_sites("D = {None: 1, **x}\n") == []
    # A TUPLE-literal key is compared too (390 under the scanned roots, 308 fully
    # literal), and
    # element values follow dict equality: `(1, 2)` and `(True, 2)` are ONE key.
    assert _duplicate_constant_key_sites("D = {(1, 2): 'a', (1, 2): 'b'}\n") == [
        (1, 1, "(1, 2)")]
    assert _duplicate_constant_key_sites("D = {(1, 2): 'a', (True, 2): 'b'}\n") == [
        (1, 1, "(True, 2)")]
    # …while a 1-tuple is NOT the bare key, and a non-literal element is skipped.
    assert _duplicate_constant_key_sites("D = {(1,): 'a', 1: 'b'}\n") == []
    assert _duplicate_constant_key_sites("D = {(K, 2): 'a', (K, 2): 'b'}\n") == []
    # A SIGNED numeric key is still a key, and `ruff`'s `F601` flags all three of
    # these shapes — so a guard that skipped them would be WEAKER than the lint it
    # exists to backstop, on the one shape that has no other catcher when the
    # survivor carries a noqa marker.
    assert _duplicate_constant_key_sites("D = {-1: 'a', -1: 'b'}\n") == [(1, 1, "-1")]
    assert _duplicate_constant_key_sites("D = {+1: 'a', 1: 'b'}\n") == [(1, 1, "1")]
    assert _duplicate_constant_key_sites("D = {(-1, 2): 'a', (-1, 2): 'b'}\n") == [
        (1, 1, "(-1, 2)")]
    # ⛔ `bool` is an `int` in this arm ON PURPOSE (`+True` IS `1`), and that is what
    # makes `{+True: 'a', True: 'b'}` a real duplicate — `ruff F601` flags it too. Nothing
    # pinned it until a review MEASURED the narrowing: `isinstance(operand, (int, float,
    # complex))` → `type(operand) in (int, float, complex)` reads like a tidy-up, makes the
    # scan return `[]`, and leaves the whole suite GREEN — a false PASS on a shape lint
    # catches, i.e. exactly the weakness this arm exists to avoid.
    assert _duplicate_constant_key_sites("D = {+True: 'a', True: 'b'}\n") == [(1, 1, "True")]
    # …and the sign is compared, not stripped: `-1` and `1` are DIFFERENT keys.
    assert _duplicate_constant_key_sites("D = {-1: 'a', 1: 'b'}\n") == []
    # An F-STRING key is a gap a fresh review MEASURED in this guard: the scan returned
    # `[]` where `ruff --select F601` reported a duplicate, so the guard was WEAKER than
    # the lint it backstops — a false PASS on the one shape whose only other catcher can
    # be a `noqa` marker on the survivor.
    # A PLACEHOLDER-FREE f-string FOLDS to the plain `str` key CPython compiles, so `f"a"`
    # and `f'a'` are ONE key and render as the `str` they are rather than as a
    # `_JoinedKey`. This arm used to refuse the fold on a FALSE claim that proving the
    # equality "needs evaluation" — `ast` has no `FormattedValue` left to evaluate, and
    # `ruff 0.16.4` does not flag the cross-form pair, so nothing else caught it.
    assert _duplicate_constant_key_sites('D = {f"a": 1, f"a": 2}\n') == [
        (1, 1, "'a'")]
    assert _duplicate_constant_key_sites("D = {f\"a\": 1, f'a': 2}\n") == [
        (1, 1, "'a'")]
    assert _duplicate_constant_key_sites('D = {f"": 1, "": 2}\n') == [(1, 1, "''")]
    # ⛔ …and the CROSS-FORM pair specifically, which is the shape with NO other catcher
    # at all: `{f"a": 1, "a": 2}` IS `{'a': 2}` at runtime and `ruff --select F601` does
    # NOT flag it (measured on 0.16.4), so refusing it was a false PASS reading as a
    # deliberate limit. Pinned so the wording cannot drift back.
    assert _duplicate_constant_key_sites('D = {f"a": 1, "a": 2}\n') == [(1, 1, "'a'")]
    # A PLACEHOLDER-BEARING f-string is compared by SOURCE instead, because equating it
    # with a `Constant` genuinely needs evaluation: two byte-identical sources are one
    # key for a referentially transparent expression, and
    # `ast.unparse` normalises the quoting (`f"{x}"` and `f'{x}'` are one key, as at runtime).
    assert _duplicate_constant_key_sites('D = {f"{x}": 1, f"{x}": 2}\n') == [
        (1, 1, "f-string key f'{x}'")]
    # …while a DIFFERENT placeholder expression is a different key. ⛔ A
    # PLACEHOLDER-BEARING f-string is NOT compared with a plain literal, and that is a
    # RESIDUAL HOLE IN BOTH INSTRUMENTS — not parity with the lint, which the earlier
    # wording here claimed: `{f"{'a'}": 1, "a": 2}` is `{'a': 2}` at runtime (the first
    # entry is DEAD) and NEITHER this scan NOR `ruff 0.16.4 --select F601` reports it
    # (both measured). Equating it needs the placeholder EVALUATED, which is the one step
    # this function refuses; the hole is pinned here so the next lane inherits the truth
    # rather than a claim that nothing is missed.
    assert _duplicate_constant_key_sites('D = {f"{x}": 1, f"{y}": 2}\n') == []
    # the residual HOLE itself, pinned so its absence cannot be read as coverage:
    assert _duplicate_constant_key_sites("D = {f\"{'a'}\": 1, \"a\": 2}\n") == []

    # ⛔ The one direction this arm gets WRONG, pinned so the wording cannot drift back
    # to "whatever the expression is": an IMPURE expression is re-evaluated per key, so
    # two byte-identical sources build two DISTINCT keys — and this reports a duplicate.
    # It is a false RED, not a false PASS, and `ruff --select F601` flags the same shape
    # (verified), so the guard stays no weaker than the oracle it backstops.
    assert _duplicate_constant_key_sites(
        'D = {f"{next(c)}": 1, f"{next(c)}": 2}\n') == [
        (1, 1, "f-string key f'{next(c)}'")]


def _duplicate_key_report(violations: list[str]) -> str:
    """The guard's failure message — separated so a test can DRIVE it.

    It used to be composed inline in the guard's `assert`, where it is evaluated only
    when that assert FAILS — so no run ever built it, and changing the prefix or the
    newline-and-indent separator would have left every test green. A message nobody renders is
    not a guard's evidence.

    ⛔ The prose covers BOTH arms this prefixes, because `_scan_declaration_roots` routes
    three kinds of violation through it: a duplicated key, an UNREADABLE module and an
    UNPARSEABLE one. A prefix that named only the duplicate would send a reader holding a
    parse failure looking for a duplicate key that is not there — the misattribution this
    guard exists to stop, one layer up (review of `#12265`).
    """
    return (
        "a duplicate declaration is a SILENT loser: a dict literal that declares the same "
        "key twice keeps only the LAST binding, so the entry that loses the last-wins race "
        "is DEAD while every test stays green (#12265, root #9314) — and a module this scan "
        "could not READ or PARSE is a HOLE in the same guard, not a pass. Delete the stale "
        "row (or correct the reason) rather than adding `# noqa: F601`, and make an "
        "UNREADABLE/UNPARSEABLE module readable:\n  "
        + "\n  ".join(violations)
    )


def _guard_root() -> Path:
    """The repo root the duplicate-key scan walks, resolved from THIS FILE.

    ⛔ NOT `Path.cwd()`. `Path(root).rglob(...)` resolves against the invocation
    directory, so a CWD-derived root scans 0 modules when pytest is launched from
    `tests/` and reds this guard on a CORRECT tree. Hoisted out of the test body
    only so `test_the_scan_root_is_the_REPO_not_the_process_cwd` can be the
    FALSIFIER for it: a `chdir` is the one thing that tells a file-derived root
    apart from a CWD-derived one, and comparing without it passes under the repo
    root, which is how CI and `tools/test_lane.py` both invoke pytest.
    """
    return Path(probe.__file__).resolve().parent.parent


def _scan_declaration_roots(root: Path) -> tuple[list[str], dict[str, int]]:
    """`(violation messages, modules FOUND per root)` over `_DUP_KEY_ROOTS` under `root`.

    ⚠️ "found", not "read": the per-root counter is incremented BEFORE the read, so a
    module that cannot be READ is counted here while its own message says
    `UNREADABLE (…), not scanned`. Stated because these counts are what satisfy the
    per-root floors — i.e. the guard's non-vacuity claim. A module that cannot be
    DECODED is a different case now that the read hands `ast.parse` raw bytes: it
    surfaces as a `SyntaxError` and says `UNPARSEABLE`, never `UNREADABLE`.

    ⛔ KNOWN SILENT HOLE in the WALK (measured, filed as `#12385`): `rglob` swallows a
    `PermissionError` from `scandir` and does not descend a symlinked directory, so an
    unlistable (`chmod 000`) subtree or one reachable only through a symlink is a
    directory the scan never ENUMERATES — no violation, and the per-root floors are too
    coarse to notice a partial loss (losing 20 of `tortoise/`'s 287 still clears the
    floor of 100). Neither shape exists under these roots today (no tracked symlinks;
    every file git-readable), which is why the hole is stated rather than repaired in a
    review lane: the repair replaces this walk with `os.walk(..., onerror=…)` plus a
    symlinked-directory violation, and that belongs with the follow-up, not here.

    Hoisted out of the test so the REPORTING path can be driven: on a real tree
    `violations` is empty, so the message formatting, the `relative_to` naming and the
    `"\n  ".join(...)` would otherwise be code no run ever executes. A guard whose
    only exercised arm is the green one is not evidence the red one works.
    """
    violations: list[str] = []
    per_root: dict[str, int] = {}
    for name in _DUP_KEY_ROOTS:
        scanned_here = 0
        for path in sorted((root / name).rglob("*.py")):
            rel = path.relative_to(root)
            scanned_here += 1
            try:
                # ⛔ BYTES, not text: `read_text(encoding="utf-8")` is STRICTER than
                # CPython — a PEP 263 coding cookie and a UTF-8 BOM are legal source, and
                # decoding it ourselves reported both as holes (measured). Handing the
                # bytes to `ast.parse` makes the interpreter the decoder, so an
                # UNREADABLE verdict now means what its name says: the file could not be
                # READ (permissions, a directory), not that it is unusual source.
                source = path.read_bytes()
            except OSError as exc:  # a hole is a hole, not a zero
                violations.append(f"{rel}: UNREADABLE ({exc.__class__.__name__}), not scanned")
                continue
            try:
                for first, duplicate, key in _duplicate_constant_key_sites(source, str(rel)):
                    violations.append(
                        f"{rel}:{duplicate} re-declares key {key} (first at :{first})")
            except SyntaxError as exc:  # name the file, never `<unknown>`
                # `lineno` is `None` for a NUL byte and `0` for a codec/cookie failure
                # (`# coding: unknown-xyz`), so a `is not None` test still renders
                # "at line 0" — a located-LOOKING defect pointing at a line that cannot
                # exist, which is the misattribution this message exists to prevent, one
                # field down. Both mean "no line", so test the VALUE, not `is None`.
                where = exc.lineno if exc.lineno else "?"
                violations.append(
                    f"{rel}: UNPARSEABLE ({exc.msg} at line {where}), not scanned")
        per_root[name] = scanned_here
    return violations, per_root


def test_no_module_declares_the_same_dict_key_twice():
    """#12265: a duplicated literal key silently disables the entry that loses.

    Measured at `origin/main` `9828250732`: three sites — two in
    `tools/dod_5090_probe.py` (`WRITER_CENSUS`, `CENSUS_EVIDENCE`) and one in
    `tests/test_commit_schema.py` (a fixture `op_type` that last-wins discarded,
    silenced with `# noqa: F601`). This assertion is the lane-independent catch
    `#9314` asks for: it reds in the pytest shard, not only where lint runs.

    ⛔ Its coverage is the three roots `_DUP_KEY_ROOTS` names, not the whole repo —
    see that constant: `battery/`, `graph-scripts/`, `docs/`, `eval/` and the smaller
    roots sit outside them.
    """
    # ⛔ A root with no floor is a walk nothing checks: adding a fourth entry to
    # `_DUP_KEY_ROOTS` without adding a `_MIN_MODULES` floor would leave it counted
    # and unchecked, so it could yield zero and the guard would silently narrow —
    # exactly what the floors exist to prevent.
    assert set(_MIN_MODULES) == set(_DUP_KEY_ROOTS), (
        "every scanned root needs a non-vacuity floor — "
        f"roots={sorted(_DUP_KEY_ROOTS)}, floors={sorted(_MIN_MODULES)}")
    # ⛔ …and a floor of ZERO is the same hole: `per_root[name] >= 0` is true for a walk
    # that yielded nothing, so `{n: 0 for n in _DUP_KEY_ROOTS}` satisfies the loop below
    # while making the non-vacuity claim vacuous — measured GREEN before this assert
    # existed. The comment says "non-zero"; this is the line that enforces it. ⛔ And an
    # EMPTY population is the same vacuity one level up: `all(...)` over no values is True
    # and the key-set equality is satisfied by two empty containers, so the scan walks
    # nothing and passes — measured GREEN with `_DUP_KEY_ROOTS = ()`.
    assert _MIN_MODULES and all(floor > 0 for floor in _MIN_MODULES.values()), (
        f"every floor must be non-zero, or a root that yields nothing passes — {_MIN_MODULES}")
    # ⛔ THE REPO ROOT COMES FROM THE FILE, NOT THE CWD (#12265 review):
    # `Path(root).rglob` resolves against the invocation directory, so a run from
    # `tests/` scanned 0 modules and red this guard on a CORRECT tree. This is the
    # same derivation the module's other repo-root reads use
    # (`Path(probe.__file__).resolve().parent.parent`); `probe.REPO_ROOT` is that
    # same value, but several reads here already spell it out for the same reason.
    violations, per_root = _scan_declaration_roots(_guard_root())
    # ⛔ THE VIOLATION IS ASSERTED FIRST, deliberately: a floor failure means the
    # guard is narrower than it claims, but a live duplicate in the same run is the
    # finding the reader actually needs, and asserting floors first would hide it.
    # ⚠️ The ORDER itself is not separately asserted — both asserts fire on their own
    # (each is proven RED by its own mutation), but no test builds a tree holding a
    # duplicate AND a broken walk, so swapping these two lines leaves the suite green
    # (measured by mutation). The sentence above is the RATIONALE for the order, not a
    # claim that the order is pinned.
    assert not violations, _duplicate_key_report(violations)
    # A scan that read nothing would satisfy the assertion above vacuously — and it
    # must be checked PER ROOT: rglob returns zero silently on a root that was
    # renamed or removed, and an aggregate floor lets two empty roots hide behind
    # the third. `tools/` is where this class's original instance lived, so losing
    # that walk while still counting `tests/` is the regression that matters most.
    for name, floor in _MIN_MODULES.items():
        assert per_root[name] >= floor, (
            f"the duplicate-key scan found {per_root[name]} module(s) under {name}/, below "
            f"its floor of {floor} — the root was renamed, removed, or its walk broke, "
            "so the guard is now narrower than it claims. Re-derive the floor (or fix "
            f"the root) rather than lowering it. Full counts: {per_root}")


def test_the_scan_root_is_the_REPO_not_the_process_cwd(tmp_path, monkeypatch):
    """The FALSIFIER for `_guard_root` — a `chdir` is the only thing that tells them apart.

    ⛔ WHY THIS EXISTS (#12265 review cycle 3). `_guard_root` is a fix for a measured
    defect (a run from `tests/` scanned 0 modules and red this guard on a CORRECT
    tree), and until this test the fix had NO driving case: mutating the return to
    `Path.cwd()` kept the whole guard GREEN when pytest runs from the repo root —
    which is exactly how CI and `tools/test_lane.py` invoke it — so the defect could
    return with every assertion here still passing.

    ⛔ The `chdir` is the discriminating half, not decoration. Comparing
    `_guard_root()` against `probe.REPO_ROOT` WITHOUT moving the CWD passes under
    either implementation, because at the repo root the two derivations agree. Only
    after `monkeypatch.chdir(tmp_path)` do they diverge — a CWD-derived root answers
    `tmp_path` and reds here.
    """
    before = _guard_root()
    assert (before / "tortoise").is_dir() and (before / "tools").is_dir(), (
        f"_guard_root() returned {before}, which is not this checkout — the scan would "
        "walk roots that do not exist and satisfy its floors vacuously")
    monkeypatch.chdir(tmp_path)
    after = _guard_root()
    assert after == before, (
        f"_guard_root() moved with the CWD ({before} -> {after}): it is derived from "
        "`Path.cwd()` (or another process-relative source) instead of this file, so "
        "every invocation from a directory other than the repo root scans 0 modules "
        "and the duplicate-key guard silently narrows to nothing")


def test_the_scan_REPORTS_a_duplicate_it_finds(tmp_path):
    """The scan's RED arm, driven — the reporting path no real tree exercises.

    `test_no_module_declares_the_same_dict_key_twice` passes on every healthy tree
    with `violations == []`, so its message formatting, its `relative_to` naming and
    its `"\n  ".join(...)` never run. A guard whose only demonstrated arm is the green
    one is not evidence the red one works, so this drives the same helper over a
    planted tree and asserts the report names the file, the duplicate line and the
    first line.
    """
    (tmp_path / "tortoise").mkdir()
    (tmp_path / "tools").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "tools" / "clean.py").write_text("D = {'a': 1}\n", encoding="utf-8")
    (tmp_path / "tortoise" / "dup.py").write_text(
        "DECLARATIONS = {\n"
        "    'x::y': 1,\n"
        "    'x::y': 2,\n"
        "}\n", encoding="utf-8")
    # A module that cannot be parsed must be NAMED, never skip silently to a green.
    (tmp_path / "tests" / "broken.py").write_text("def (:\n", encoding="utf-8")
    # …and neither may a module that cannot be READ: the UNREADABLE arm is the one whose
    # own comment states the doctrine ("a hole is a hole, not a zero"), so leaving it
    # undriven would make that comment the only thing behind it — and its failure mode
    # is a FALSE GREEN (the file is counted as scanned, contributes no violation, and
    # satisfies the per-root floor). A DIRECTORY named `*.py` is the readable-shape case
    # that survives the bytes fix: `read_bytes` raises `IsADirectoryError`, an OSError.
    (tmp_path / "tools" / "adir.py").mkdir()
    # …and non-UTF-8 BYTES are legal source shapes, not holes: CPython decodes them, so
    # this one must land in UNPARSEABLE (its own decode error), never UNREADABLE.
    (tmp_path / "tools" / "binary.py").write_bytes(b"\xff\xfe\n")
    # …and a PARSE failure that carries no line number must read as UNKNOWN, never as a
    # located defect: a NUL byte raises a `SyntaxError` whose `lineno` is None, and a
    # codec failure raises one whose `lineno` is 0. Both mean "no line", and rendering
    # either prints a location that is not there (`at line None` / `at line 0`).
    (tmp_path / "tools" / "nul.py").write_bytes(b"D = {}\x00\n")
    (tmp_path / "tools" / "codec.py").write_bytes(b"# coding: unknown-xyz\nD = {}\n")

    violations, per_root = _scan_declaration_roots(tmp_path)

    # `binary.py` still counts as SCANNED (the counter runs before the read), which is
    # exactly why the report — not the count — is what has to name it.
    assert per_root == {"tortoise": 1, "tools": 5, "tests": 1}, per_root
    # The key is rendered with `repr`, so `1` and `'1'` stay distinguishable in the
    # report — the whole reason that spelling was chosen over `str(value)`.
    assert any(
        v.startswith("tortoise/dup.py:3 re-declares key 'x::y' (first at :2)") for v in violations
    ), violations
    # ⛔ The TRUTHY branch — a real line number — must still render it: without this pin
    # `where = "?"` unconditionally is GREEN on every other assertion here (measured), so
    # the one branch that prints a usable location would be the one the suite cannot see.
    # `def (:` yields `lineno == 1`; so does `binary.py`'s UTF-8 decode failure below
    # (the COOKIE failure in `codec.py` yields 0 — a different fixture, a different case).
    assert any(
        v.startswith("tests/broken.py: UNPARSEABLE") and "at line 1" in v for v in violations
    ), violations
    assert any(
        v.startswith("tools/adir.py: UNREADABLE (IsADirectoryError)") for v in violations
    ), violations
    # ⛔ A file CPython CAN decode is never reported as unreadable: the bytes arm above
    # means a non-UTF-8 file is a PARSE failure with its own message, not a hole.
    assert any(v.startswith("tools/binary.py: UNPARSEABLE") for v in violations), violations
    assert not any("binary.py: UNREADABLE" in v for v in violations), violations
    # ⛔ A parse failure with no line number must say UNKNOWN, never `None`: a NUL byte's
    # `SyntaxError.lineno` is None, and "at line None" reads as a location that is not
    # there — the same misattribution, one field down (#12265 round 4).
    assert any(
        v.startswith("tools/nul.py: UNPARSEABLE") and "at line ?" in v for v in violations
    ), violations
    assert any(
        v.startswith("tools/codec.py: UNPARSEABLE") and "at line ?" in v for v in violations
    ), violations
    assert not any("at line None" in v for v in violations), violations
    assert not any("at line 0" in v for v in violations), violations
    # …and the clean module contributes nothing: the report is specific, not a roll-up.
    assert not any("clean.py" in v for v in violations), violations
    # ⛔ And the guard's OWN message — the one `test_no_module_declares_the_same_dict_key_twice`
    # asserts with — must carry both sites. It used to be composed inline in that
    # `assert`, so it was evaluated only on FAILURE and no run ever built it; a
    # change to the prefix or the `"\n  "` separator left every test green. This
    # drives the real builder, not a local copy of it.
    report = _duplicate_key_report(violations)
    assert "tortoise/dup.py:3 re-declares key 'x::y' (first at :2)" in report, report
    assert "tests/broken.py: UNPARSEABLE" in report, report
    assert report.count("\n  ") >= 2, report
    # ⛔ The wrapper's PROSE must describe EVERY arm it prefixes, not just the duplicate
    # one: `broken.py`/`binary.py` are not duplicate keys, and a red run whose first line
    # says "a dict literal declares the same key twice" sends the reader hunting for a
    # duplicate that does not exist. Asserted on a PARSE-ONLY set, so the arm that has no
    # duplicate in it is the one the prose has to hold for.
    parse_only = _duplicate_key_report([v for v in violations if "broken.py" in v])
    assert "could not READ or PARSE" in parse_only.split("\n  ")[0], parse_only


def test_a_receiver_that_only_CONTAINS_the_seam_name_is_not_a_reach() -> None:
    """#13447 review cycle 3, issue 7 — the seam reach is resolved by BINDING, not by NAME.

    `_seam_module_calls` decides which writers the census credits with a gated
    reach of `encode_for_row`, and its own docstring promises that
    `client.encode(...)` — *"same attribute, a different object"* — is **not** a
    reach. Substring containment broke that promise: a receiver called
    `derived_embedding_client` contains `derived_embedding`, so `.encode(...)` on it
    was credited and the writer stopped owing a declaration. That is the FAIL-OPEN
    direction on the instrument — the false-MET side, not the false-block one.

    Non-vacuity: on the pre-fix code (`SEAM_MODULE in name`) the negative tree
    returns `{"encode"}`, so the `== set()` assertion is the row that reds. And on the
    cycle-3-fix spelling (`name.endswith("_" + SEAM_MODULE)`) the negative tree returns
    `{"encode", "derive_row"}` — from the two `my_derived_embedding.*` rows added at
    review cycle 6, issue 1 — so the suffix arm cannot come back unnoticed either.

    ⛔ This arm is the SPELLING half only. The BINDING half — a receiver that resolves
    to the module's name but is bound to something else — is
    `test_a_receiver_REBOUND_in_the_writers_scope_is_not_a_reach`.

    ⛔ And the last arm here is the DIRECT-CALL half (`#13447` review cycle 8, P3):
    `_seam_module_calls`'s docstring justifies its own shadow check as belt AND
    braces, "because the caller already drops shadowed aliases through
    `_keep_shadowed_aliases` and this function is also called directly (its unit test,
    and any future caller) with a hand-fed map" — and nothing pinned that clause. The
    mutant that DELETED the check survived the whole file (measured: `278 passed, 2
    skipped` either way), so the docstring named a witness its own test did not supply.
    The shape below is the only one the check can save: a node whose own `def` binds the
    receiver, plus a map that still RESOLVES it — exactly what a caller passing its own
    hand-fed map does. Deleting the check reds it (`{'encode'}` vs `set()`).
    """
    positive = ast.parse(
        "derived_embedding.encode('Acme')\n"
        "_derived_embedding.derive_row('Object', {})\n"
    )
    # ⛔ The map is REQUIRED even for the bare spelling (`#13447` review cycle 7, P2): a receiver
    # is credited only when the writer's own import map resolves it to the module, so a name the
    # writer never imported is not a reach. Both spellings here are what `_import_aliases` really
    # produces for `from tortoise import derived_embedding[ as _derived_embedding]`.
    assert probe._seam_module_calls(
        positive, {probe.SEAM_MODULE: probe.SEAM_MODULE,
                   "_" + probe.SEAM_MODULE: probe.SEAM_MODULE}) == {"encode", "derive_row"}

    # A SHORT alias resolved through the caller's own import map is still a reach — this is the
    # `from tortoise import derived_embedding as de` shape, which `_import_aliases` really emits
    # (`{"de": "derived_embedding"}`). ⛔ Before cycle 7 this arm hand-fed
    # `{"de": "tortoise.derived_embedding"}`, a key no production caller can build, so it pinned a
    # branch `_embedding_writers` could never take.
    aliased = ast.parse("de.encode('Acme')\n")
    assert probe._seam_module_calls(
        aliased, {"de": probe.SEAM_MODULE}) == {"encode"}

    # …and these are NOT: each merely CONTAINS the module name, or is a
    # different object carrying the same attribute. The two `my_derived_embedding`
    # rows are the cycle-6, issue-1 witnesses: a `"_" + SEAM_MODULE` SUFFIX rule
    # credits them, so the arm has to be membership in the two real spellings.
    negative = ast.parse(
        "derived_embedding_client.encode('Acme')\n"
        "embeds.derived_embedding_client.derive_row('Object', {})\n"
        "client.encode('Acme')\n"
        "my_derived_embedding.encode('Acme')\n"
        "my_derived_embedding.derive_row('Object', {})\n"
    )
    assert probe._seam_module_calls(negative, {
        # ⛔ The map is what makes this arm SENSITIVE to the spelling rules it names (`#13447`
        # review cycle 10, P3): with `{}` the function returns `set()` for every receiver as soon
        # as `aliases.get(name) is None`, so re-adding containment (`SEAM_MODULE in name`) or the
        # `"_" + SEAM_MODULE` suffix BEFORE the map lookup left this assertion green — it only
        # discriminated the pre-cycle-7 code that ignored the map entirely. These two entries
        # RESOLVE (so the map lookup passes and the binding test is reached) but to a module that
        # is not the seam, which is exactly the state a spelling rule cannot survive.
        "derived_embedding_client": "client_lib",
        "my_derived_embedding": "client_lib",
    }) == set(), (
        "a receiver that only CONTAINS the seam module's name was credited as a "
        "gated reach of `encode_for_row` — the census then reports the writer as "
        "covered and it owes no declaration (the fail-open direction)")

    # …and the DIRECT-CALL arm: the docstring's belt-and-braces clause, made falsifiable.
    # ⛔ The shadow check is unreachable THROUGH `_embedding_writers` (that caller drops the
    # alias first), so the only witness is a direct call with a map that still resolves the
    # name while the NODE binds it. Delete the check and this returns `{"encode"}`.
    locally_bound = ast.parse(
        "def _w(derived_embedding):\n"
        "    return derived_embedding.encode('Acme')\n"
    ).body[0]
    assert probe._seam_module_calls(
        locally_bound, {probe.SEAM_MODULE: probe.SEAM_MODULE}) == set(), (
        "the direct-call shadow check did not refuse a receiver the NODE binds "
        "itself — a hand-fed map would then credit a local parameter as the module, which "
        "is the fail-open direction this docstring's belt-and-braces clause promises to "
        "close for every caller")


#: The three shapes `#13447` review cycle 7 (P2) measured as credited by SPELLING, each paired with
#: the write clause that makes its writer DISCOVERABLE — so the assertion is about the KIND the
#: census assigns (owed vs not owed), not about whether the writer is in the population at all.
_REBOUND_RECEIVER_SHAPES: dict[str, str] = {
    # a PARAMETER of the writer's own scope
    "parameter": (
        "def _w(proj, derived_embedding):\n"
        "    return proj.g.query('MATCH (n:Point) SET n.embedding = $e', "
        "{'e': derived_embedding.encode('x')})\n"
    ),
    # an ASSIGNMENT in the writer's own scope
    "assignment": (
        "def _w(proj):\n"
        "    _derived_embedding = make()\n"
        "    return proj.g.query('MATCH (n:Point) SET n.embedding = $e', "
        "{'e': _derived_embedding.encode('x')})\n"
    ),
    # a FOREIGN import (`from other.pkg import …`) — same name, a different object
    "foreign_import": (
        "from other.pkg import derived_embedding\n"
        "def _w(proj):\n"
        "    return proj.g.query('MATCH (n:Point) SET n.embedding = $e', "
        "{'e': derived_embedding.encode('x')})\n"
    ),
}


@pytest.mark.parametrize("shape", sorted(_REBOUND_RECEIVER_SHAPES))
def test_a_receiver_REBOUND_in_the_writers_scope_is_not_a_reach(shape, tmp_path) -> None:
    """#13447 review cycle 7, P2 — a receiver is the module only while the BINDING says so.

    `_seam_module_calls` credited `.encode`/`.derive_row` on any receiver whose NAME was
    `derived_embedding`/`_derived_embedding`, with no binding check. A parameter, an assignment, and
    a foreign import all bind that name to something else, and all three measured as a credited
    reach — which sets `kind="gated"`, and `gated` is OUTSIDE `NEEDS_A_DECLARATION`. So the writer
    stopped OWING a declaration while never reaching the gate: `#12020` round 3's fail-open class
    (`a writer-local _enc = lambda p: p still credited encode_for_row`), one seam over, in the file
    whose whole purpose is that guard.

    Drives `_embedding_writers` over a scratch tree, not `_seam_module_calls` directly, because two
    of the three shapes are caught one layer UP: the caller drops a shadowed alias through
    `_keep_shadowed_aliases`. This test is about the writer's KIND, so it has to see the caller.

    Non-vacuity: on the pre-fix code each shape measures `gated` (measured at `e9017e1e95` —
    `_locally_bound` already saw the names, but the receiver test only read the spelling), so the
    `unresolved_write` assertion is the row that reds. The healthy control in the same file pins
    the other direction, so the fix cannot be "credit nothing".
    """
    (tmp_path / "tortoise").mkdir()
    (tmp_path / "tortoise" / "embeddings.py").write_text("", encoding="utf-8")
    (tmp_path / "tortoise" / "shadow.py").write_text(
        _REBOUND_RECEIVER_SHAPES[shape], encoding="utf-8")
    # ⛔ The healthy control lives in its OWN module, not beside the shape: `_scope_bound_names`
    # shadows a module alias for the WHOLE module ON PURPOSE (`_keep_shadowed_aliases`: "a
    # rebinding anywhere in the module makes a bare name ambiguous for a GATE spelling"), so the
    # foreign-import row would legitimately shadow the control and this arm would red a correct
    # verdict. The two are therefore separate files, which is also what the production tree looks
    # like.
    (tmp_path / "tortoise" / "healthy.py").write_text(
        "from tortoise import derived_embedding\n"
        "def _healthy(proj):\n"
        "    return proj.g.query('MATCH (n:Point) SET n.embedding = $e', "
        "{'e': derived_embedding.encode('x')})\n",
        encoding="utf-8")

    # ⛔ NESTED-SCOPE rows (`#13447` review cycle 10, P2). The writer's OWN scope is not the only
    # place a receiver is rebound: `_seam_module_calls` walks the WHOLE subtree, so a nested `def`'s
    # parameter — or an assignment inside a nested body — binds the name only INSIDE that subtree.
    # With the shadow read as "writer's own scope only" these two measured `gated` (outside
    # `NEEDS_A_DECLARATION`: owing no declaration while never reaching the gate), so the assertions
    # below are the rows that red; the shadow is threaded DOWNWARD instead, which is why the
    # healthy control above stays `gated` (a legitimate closure that reaches the module).
    nested_sources = {
        "nested_parameter": (
            "from tortoise import derived_embedding\n"
            "def _w_np(proj):\n"
            "    def helper(derived_embedding):\n"
            "        return derived_embedding.encode('x')\n"
            "    return proj.g.query('MATCH (n:Point) SET n.embedding = $e', "
            "{'e': helper(None)})\n"
        ),
        "nested_assignment": (
            "from tortoise import derived_embedding\n"
            "def _w_na(proj):\n"
            "    def helper():\n"
            "        derived_embedding = make()\n"
            "        return derived_embedding.encode('x')\n"
            "    return proj.g.query('MATCH (n:Point) SET n.embedding = $e', "
            "{'e': helper()})\n"
        ),
    }
    for module_name, source in nested_sources.items():
        (tmp_path / "tortoise" / f"{module_name}.py").write_text(source, encoding="utf-8")

    writers = probe._embedding_writers(tmp_path)
    # ⛔ The healthy control: the module import IS a reach, so the row below is a binding verdict
    # and not a resolver that credits nothing.
    assert writers["tortoise.healthy::_healthy"]["kind"] == "gated", (
        "the honest `from tortoise import derived_embedding` reach stopped being credited — the "
        "fix over-corrected into the false-block direction")
    kind = writers["tortoise.shadow::_w"]["kind"]
    assert kind in ("unresolved_write", "ungated"), (
        f"a receiver rebound in the writer's own scope ({shape}) was credited as a gated reach of "
        f"`encode_for_row` (measured {kind!r}) — `gated` is outside NEEDS_A_DECLARATION, so the "
        "writer owes no declaration while never reaching the gate (the fail-open direction)")
    assert writers["tortoise.shadow::_w"]["callee"] != probe.SEAM_CALL, (
        "the shadowed receiver was credited to the seam anyway — the binding was not consulted")

    # ⛔ The nested rows: the SAME rebinding, one scope down. Each module is its own file (see the
    # healthy-control note above), so the module-wide alias pass cannot muddy the verdict.
    for module_name, writer_name in (("nested_parameter", "_w_np"),
                                     ("nested_assignment", "_w_na")):
        nested_kind = writers[f"tortoise.{module_name}::{writer_name}"]["kind"]
        assert nested_kind in ("unresolved_write", "ungated"), (
            f"a receiver rebound in a NESTED scope ({module_name}) was credited as a gated reach "
            f"of `encode_for_row` (measured {nested_kind!r}) — the shadow is threaded DOWNWARD, so "
            f"a nested parameter/assignment must not credit the writer, which otherwise stops "
            f"owing a declaration while never reaching the gate")
        assert writers[f"tortoise.{module_name}::{writer_name}"]["callee"] != probe.SEAM_CALL, (
            f"the {module_name} receiver was credited to the seam anyway — the nested scope's "
            f"binding was not consulted")


def test_every_aliasable_name_has_a_canonical_order_entry() -> None:
    """`#13447` review cycle 7, P3 — `_alias_preference`'s premise, pinned instead of assumed.

    `_ALIAS_CANONICAL_ORDER`'s readers are `.index()` lookups inside `_alias_preference`, and the
    `except ValueError` arm there is marked `# pragma: no cover - every aliasable name is in the
    tuple`. `SEAM_MODULE` was enrolled in `_ALIASABLE_CALL_NAMES` (the binding fix above) WITHOUT
    being added here, so that sentence was FALSE and live code sat behind a "no cover" pragma — the
    resolution still happened to be deterministic (the fallback returns a lower key than every real
    entry, so `SEAM_CALL` wins a collision and both are rank 0 anyway), but by an accident nothing
    declared.

    Non-vacuity: measured at `f2f48bae33` (the commit that enrolled `SEAM_MODULE`), `missing` is
    `['derived_embedding']` and the second loop raises `ValueError` out of `order.index` — so both
    rows red there and green at the fix.

    ⛔ The ORDER is pinned too, not merely membership: `SEAM_MODULE` is last so the ordering that was
    measured before the enrolment is unchanged, which is what keeps a published `callee` from moving
    (`#11444`). A lane that reorders this tuple is republishing census output.
    """
    order = probe._ALIAS_CANONICAL_ORDER
    missing = sorted(set(probe._ALIASABLE_CALL_NAMES) - set(order))
    assert not missing, (
        f"{missing} can be credited by `_import_aliases` but has no `_ALIAS_CANONICAL_ORDER` entry, "
        "so `_alias_preference` resolves it through its `except ValueError` arm — which is marked "
        "`# pragma: no cover` on the premise this assertion is checking")
    assert len(order) == len(set(order)), (
        f"`_ALIAS_CANONICAL_ORDER` carries a duplicate ({order}) — the tie-break is then not a "
        "total order and `#12182`'s source-order independence no longer holds")
    # …and the fallback arm stays unreachable, which is exactly what its pragma claims.
    for name in probe._ALIASABLE_CALL_NAMES:
        assert probe._alias_preference(name)[1] == -order.index(name), (
            f"{name!r} took `_alias_preference`'s ValueError fallback, so its tie-break key is "
            "not the canonical position — the `# pragma: no cover` on that arm is a lie and a "
            "reordering of this tuple would no longer be detectable")
    assert order[-1] == probe.SEAM_MODULE, (
        "`SEAM_MODULE` is no longer LAST — both it and `SEAM_CALL` are rank 0, so moving it ahead "
        "silently changes which `callee` name the census PUBLISHES (no obligation moves, but "
        "`recognised_producer` route claims are re-derived from that name — `#11444`)")
