"""#10465 — the DoD instrument must be able to come back RED.

`#10465` scores `#5090`'s four `Definition of done` bullets at a named head, so
the instrument is the deliverable and its **falsifiers are the proof**. A probe
that can only ever print `MET` is machinery, not measurement — the same failure
`#4999`'s own test file calls out (*"a test asserting ``reason == 'ok'`` on a
healthy path PASSES on the pre-fix code"*).

Each test below is a falsifier for one of the instrument's two load-bearing
claims:

| test | claim it falsifies | it goes RED when … |
|---|---|---|
| `test_the_four_bullets_are_scored_verbatim` | nothing is silently dropped | a bullet stops being scored, or is re-worded so the table scores something the epic does not say |
| `test_the_embed_decision_has_exactly_one_predicate` | §12.2a's target of ONE predicate | a second pattern-owning module appears on the embed path, or the gate copies `value_gate.RULES` instead of sharing it |
| `test_a_second_pattern_list_in_the_embed_gate_reads_as_drift` | the predicate count can FAIL | the count is hard-wired to 1 (mutation: a fake tree WITH a second pattern list must read `DRIFT`) |
| `test_every_embedding_write_path_reaches_the_gate_seam` | bullet 1's `enforced` half, REMOVAL direction only | a named writer stops calling `encode_for_row` (the point of #5008) |
| `test_an_unresolved_writer_is_reported_and_holds_bullet_two_at_partial` | bullet 2's honesty, on an INJECTED map | an unresolved write path is dropped from the written set, or stops holding the row at `PARTIAL` |
| `test_every_embedded_label_has_a_query_path` | bullet 2 | an embedding write path serves a label `run_vector_query` cannot query |
| `test_all_three_mechanisms_are_visible_to_the_caller` | bullet 3 (`#4999`) | the fallback stops announcing itself, or two paths report the same mechanism |
| `test_the_census_refuses_to_report_zero_for_an_unreadable_store` | the 2026-09-24 error (`stored`-only / silent 0) | a store that could not be read is reported as `0` anything |
| `test_an_unmeasurable_run_exits_two` | the documented exit-code contract | a run that could not measure exits 0 or 1, so a caller reads "could not measure" as "a row was NOT MET" |
| `test_the_instrument_is_selected_by_its_own_subjects` | the pins above actually RUN in CI | a subject diff stops selecting this file — a falsifier that never executes |
| `test_two_package_inits_keep_distinct_writer_identities` | the writer map's exhaustiveness | two writers in one package collapse into one key, so a label bullet 2 compares vanishes |
| `test_a_duplicate_writer_identity_holds_bullet_two_at_partial` | bullet 2's honesty | a writer whose label could not be resolved stops holding the row, while bullet 1 still reports the same hole |
| `test_the_tool_parses_under_the_floor` | the `#5128` guard is PROTECTIVE, not documentary | 3.12-only syntax (PEP 701) creeps back in, so the file dies in the tokenizer before its own guard can refuse |
| `test_a_line_that_merely_mentions_the_flag_is_not_a_shipped_default` | bullet 1's `on_by_default` input | a line that *documents* the flag (prose, a workflow step name, a commented list item) is scored as a shipped ON |
| `test_every_real_assignment_shape_still_reads_as_shipped_ON` | the same input, in the other direction | tightening the test makes a REAL shipped ON (compose map/array, `.env`, Dockerfile, `export`) read as a false `False` |
| `test_searched_is_the_walk_not_the_list_of_mentions` | the documented `searched` contract | `searched` reports the mentions, so "what 'nowhere' covered" is `[]` for the case the function exists for |
| `test_an_unreadable_surface_is_named_not_silently_excluded` | the `UNMEASURED` rule, applied to the walk | a file the walk could not read is folded into a measured `False` |

⛔ **These tests never write a graph and never flip a gate.** `#10465` is
measurement-only; the instrument is read-only and its pin must be too.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "tools"))

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


# ── The four rows ──────────────────────────────────────────────────────────


def test_the_four_bullets_are_scored_verbatim():
    """Every bullet the epic states is scored, and none is invented."""
    report = probe.measure()
    rows = report["rows"]
    assert len(rows) == 4, rows
    assert [r["bullet"] for r in rows] == [1, 2, 3, 4], rows
    # A LITERAL, not `probe.BULLETS` on both sides: comparing the tool's
    # constant to itself could only detect a dropped row, never a re-worded
    # one (this file's own review, P3). These strings are `#5090`'s
    # `## Definition of done`, verbatim.
    assert [r["text"] for r in rows] == [
        "A stated, enforced rule for what earns an embedding.",
        "No vector is stored without a path to query it.",
        "A fallback query reports the fallback.",
        "V1 is decided and implemented.",
    ], "the scored text drifted from #5090's `## Definition of done`"
    for r in rows:
        assert r["verdict"] in probe.VERDICTS, r
        # A bullet without a command is UNSCORED, not met (#10465 target 1).
        assert r["command"].strip(), r
        assert r["observed"].strip(), r


def test_the_report_names_the_head_it_measured():
    """A verdict without a head is unquotable a week later — #5087's staleness."""
    report = probe.measure()
    assert report["head"] and not report["head"].startswith("UNMEASURED"), report["head"]
    assert len(report["head"]) >= 7, report["head"]


# ── §12.2a — one predicate ─────────────────────────────────────────────────


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


# ── Bullet 1 / 2 — the seam and the served labels ──────────────────────────


def test_every_embedding_write_path_reaches_the_gate_seam():
    """#5008's seam is reached by the entity writers — not by the raw primitive."""
    seams = probe._write_seams_that_embed()
    for writer in ("tortoise.projection.entities::_upsert_point_props",
                   "tortoise.projection.entities::_upsert_subject",
                   "tortoise.projection.entities::_upsert_object",
                   "tortoise.projection.entities::_upsert_document",
                   "tortoise.projection.entities::_upsert_event"):
        assert writer in seams, (
            f"{writer} embeds a written row without reaching encode_for_row — "
            f"the ungated-primitive state #5008 closed. Measured seams: {sorted(seams)}"
        )
    # ⛔ This test claims the REMOVAL direction only, and deliberately asserts
    # nothing about TODAY's unresolved set: `#5090`'s remaining work is to
    # resolve those writers, so `assert unresolved` would red on a CORRECT
    # change and its cheapest-looking repair would be to delete it (review, P2).
    # The BYPASS direction is bullet 1's own `OUTSIDE the gate` population
    # (`score_rule_bullet` counts it and holds bullet 1 at `PARTIAL` for it),
    # and the "an unresolved writer is REPORTED, never
    # dropped" property is pinned on an injected map in
    # `test_an_unresolved_writer_is_reported_and_holds_bullet_two_at_partial`
    # and `test_a_duplicate_writer_identity_holds_bullet_two_at_partial`.


def test_an_unresolved_writer_is_reported_and_holds_bullet_two_at_partial(monkeypatch):
    """Bullet 2 must never read `MET` over a write path whose label it lost.

    The map is INJECTED, so this is a durable property rather than a reading of
    this head: resolving every unresolved writer in the tree turns the real row
    `MET` without touching this test (review, P2 — the sibling assertion that
    read the live tree was exactly that trap).
    """
    writers = {
        "tortoise.z::_store_without_a_producer": {
            "callee": "unrecognised", "at": "tortoise/z.py:3",
            "kind": "unresolved_write",
            "label": "UNKNOWN(unresolved producer)",
        },
    }
    monkeypatch.setattr(probe, "embedding_writers", lambda: writers)
    written = probe.written_labels()
    assert written == {
        "tortoise.z::_store_without_a_producer": "UNKNOWN(unresolved producer)"
    }, f"an unresolved write path must be REPORTED, never dropped: {written}"
    row = probe.score_path_bullet()   # every resolved label IS served here
    assert row.verdict == "PARTIAL", row.observed
    assert any("unresolved writers" in n for n in row.notes), row.notes


def test_every_embedded_label_has_a_query_path():
    """Bullet 2: nothing is STORED that `run_vector_query` cannot query.

    The assertions below are an IMPLICATION, never this head's classification:
    the row can only be `PARTIAL` while a write path's label is unresolved, and
    resolving them all (`#5090`) turns it `MET` without reddening this test.
    """
    row = probe.score_path_bullet()
    assert row.verdict in ("MET", "PARTIAL"), row.observed
    resolved = {v for v in probe.written_labels().values()
                if not v.startswith("UNKNOWN")}
    assert resolved <= probe.served_labels(), resolved - probe.served_labels()
    if any(v.startswith("UNKNOWN") for v in probe.written_labels().values()):
        assert row.verdict == "PARTIAL", row.verdict


# ── Bullet 3 — the fallback announces itself ───────────────────────────────


def test_all_three_mechanisms_are_visible_to_the_caller():
    """`#4999`: an indexed read, a scan, and a scan_fallback are distinguishable."""
    observed = probe.fallback_probe()
    assert observed == {"index": "index", "scan": "scan",
                        "scan_fallback": "scan_fallback"}, observed
    assert probe.score_fallback_bullet().verdict == "MET"


# ── The census ─────────────────────────────────────────────────────────────


class _UnreadableGraph:
    """A store whose catalogue cannot be read — NOT an empty store."""

    def query(self, cypher, params=None, timeout=None):
        raise RuntimeError("Timeout reading from socket")


def test_the_census_refuses_to_report_zero_for_an_unreadable_store():
    """`stored=0` and `could not read` are different facts (2026-09-24's error)."""
    census = probe.vector_census(_UnreadableGraph(), "some_graph",
                                at="2026-11-01T00:00:00Z",
                                graph_names=["some_graph"])
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


def test_the_census_never_touches_a_graph_that_does_not_exist():
    """The read-only doctrine: a missing name is `UNMEASURED`, not a write and
    not a measured-looking `stored=0`."""
    census = probe.vector_census(_MaterialisingGraph(), "absent_graph",
                                at="2026-11-01T00:00:00Z",
                                graph_names=["dod5090_probe"])
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


# ── The exit contract the docstring promises ───────────────────────


def test_an_unmeasurable_run_exits_two(monkeypatch):
    """

    `0 = ran` (whatever the verdicts), `2 = could not measure`. The docstring
    promised `2` and two of the three routes returned `1`, which is the same
    class of defect as the epic body this PR repaired: a contract in prose the
    code never implements (review P3). All three routes are pinned here.
    """
    # (a) a gate module the instrument scores that will not import.
    def _no_gate():
        raise ImportError("no module named 'tortoise.embed_gate'")

    monkeypatch.setattr(probe, "score_rule_bullet", _no_gate)
    assert probe.main(["--json"]) == 2
    monkeypatch.undo()

    # (b) a census URI whose engine has no index API (the `_connect` refusal).
    with pytest.raises(SystemExit) as raised:
        probe.main(["--census", "--uri", "file:///tmp/whatever.db"])
    assert raised.value.code == 2, raised.value.code

    # (c) AND a run that DID measure is `0` even when a row is NOT MET or
    # PARTIAL — a caller must never read a red verdict as "the tool failed".
    assert probe.main(["--json"]) == 0


# ── The pins must actually RUN (review P1) ─────────────────────────


def test_the_instrument_is_selected_by_its_own_subjects():
    """

    A falsifier that never executes is not a falsifier (review P1).

    Registration alone is CLASSIFICATION (#4348): it satisfies
    `ci_selection.py --integrity` but does not make a diff SELECT this file. Each
    subject below must select it, or the pins this instrument exists to install
    never run on the change that can break them. Two named-surface matches
    REPLACE the `core` fallback (#2938), which is why the file is registered in
    `eval` as well as `core` (`tortoise/embeddings.py` — the seam — selects
    `eval`), and why `tools/dod_5090_probe.py` needs a `CORE_ALSO` entry (a
    `tools/`-only diff is swallowed by NON_PYTHON_PREFIXES before the fallback
    runs, so it selected 70 unrelated files and not this one).
    """
    import ci_selection

    manifest = ci_selection.load_manifest()
    for subject in ("tortoise/embed_gate.py", "tortoise/value_gate.py",
                    "tortoise/embeddings.py", "tortoise/search_engine.py",
                    "tortoise/security.py", "tools/dod_5090_probe.py"):
        selected = ci_selection.select([subject], "pull_request", manifest)
        assert "test_dod_5090_probe.py" in selected["test_files"], (
            f"a {subject}-only diff does not select this file — its falsifiers "
            f"never run: surfaces={selected['surfaces']} full={selected['full']}"
        )


# ── The falsifiers the review asked for ────────────────────────────────────


def test_a_write_path_outside_the_gate_is_counted(tmp_path, monkeypatch):
    """Bullet 1 must SEE an ungated writer (review P1 #4) — a map that only
    looks for the gated call cannot report the bypass it is meant to catch."""
    (tmp_path / "tortoise").mkdir()
    (tmp_path / "tortoise" / "embeddings.py").write_text(
        "def encode_for_row(t, d):\n    return None\n"
        "def compute_session_embedding(t):\n    return [0.0]\n", encoding="utf-8")
    (tmp_path / "tortoise" / "writer.py").write_text(
        "from .embeddings import compute_session_embedding\n"
        "def main(event):\n"
        "    emb = compute_session_embedding('x')\n"
        "    set_embedding_clause('e')\n"
        "    return emb\n", encoding="utf-8")
    monkeypatch.setattr(probe, "REPO_ROOT", tmp_path)
    writers = probe.embedding_writers()
    assert writers["tortoise.writer::main"]["kind"] == "ungated", writers


def test_the_predicate_count_searches_the_embed_path_not_a_whitelist(
        tmp_path, monkeypatch):
    """A THIRD pattern-owning module on the embed path is drift (review P1 #2).

    A two-file whitelist reports this tree as drift-free; the import-graph walk
    cannot.
    """
    (tmp_path / "tortoise").mkdir()
    (tmp_path / "tortoise" / "value_gate.py").write_text(
        "import re\n_A = re.compile(r'#\\d+')\nRULES = ('r',)\n", encoding="utf-8")
    (tmp_path / "tortoise" / "embed_gate.py").write_text(
        "from . import value_gate\nfrom . import embed_rules\n"
        "RULE_SET = value_gate.RULES\n", encoding="utf-8")
    (tmp_path / "tortoise" / "embed_rules.py").write_text(
        "import re\n_B = re.compile(r'\\d{4}')\n", encoding="utf-8")
    (tmp_path / "tortoise" / "embeddings.py").write_text("# seam\n", encoding="utf-8")
    monkeypatch.setattr(probe, "REPO_ROOT", tmp_path)
    counted = probe.count_embed_predicates()
    assert counted["count"] == 2, counted
    assert "tortoise/embed_rules.py" in counted["owners"], counted
    assert counted["verdict"] == "DRIFT", counted


def test_measure_restores_the_gate_flags(monkeypatch):
    """A read-only instrument must not leave the caller's process un-gated."""
    monkeypatch.setenv("TORTOISE_EMBED_GATE", "1")
    monkeypatch.setenv("TORTOISE_VALUE_GATE", "1")
    probe.measure()
    assert os.environ["TORTOISE_EMBED_GATE"] == "1"
    assert os.environ["TORTOISE_VALUE_GATE"] == "1"


# ── The census, on its POSITIVE path (all three tests above were negative) ──


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

    def query(self, cypher, params=None, timeout=None):
        if "db.indexes()" in cypher:
            rows = self._catalogue
        elif "db.labels()" in cypher:
            rows = [[lab] for lab in self._labels]
        elif "RETURN count(n)" in cypher:
            label = cypher.split("MATCH (n:")[1].split(")")[0].strip("`")
            rows = [[self._vectors.get(label, (0, None))[0]]]
        elif "RETURN n.embedding" in cypher:
            label = cypher.split("MATCH (n:")[1].split(")")[0].strip("`")
            rows = [[self._vectors.get(label, (0, None))[1]]]
        else:
            rows = []
        return type("_R", (), {"result_set": rows})()


def _index_row(label, field, dim):
    return [label, [field], {field: ["VECTOR"]},
            {field: {"dimension": dim}}, "english", [], "NODE", "OPERATIONAL", {}]


def test_the_census_reports_an_index_on_ANOTHER_field_as_unindexed():
    """An index on the LABEL is not an index on `embedding` (review P1-1).

    Measured failure this pins: a `(Source, vec)` index made a stored
    `Source.embedding` vector report `indexed=True, unindexed=0`.
    """
    graph = _CensusGraph(
        catalogue=[_index_row("Source", "vec", 384)],
        labels=["Source", "Chunk"],
        vectors={"Source": (1, [0.0] * 384), "Chunk": (2, [0.0] * 384)},
    )
    census = probe.vector_census(graph, "g", at="2026-11-01T00:00:00Z",
                                graph_names=["g"])
    assert census["labels"]["Source"]["indexed"] is False
    assert census["labels"]["Source"]["unindexed"] == 1
    # …and the population comes from the GRAPH's labels, so a stored-and-
    # neither-indexed-nor-served label is REPORTED, not omitted (review P1-2).
    assert "Chunk" in census["labels"], sorted(census["labels"])
    assert census["labels"]["Chunk"]["unindexed"] == 2


def test_the_census_payload_needs_the_probe_and_the_index_to_agree():
    """The byte figure is derived, so it must not multiply a different width."""
    graph = _CensusGraph(
        catalogue=[_index_row("Point", "embedding", 384)],
        labels=["Point"],
        vectors={"Point": (2, [0.0] * 4)},  # stored width 4, index dim 384
    )
    census = probe.vector_census(graph, "g", at="2026-11-01T00:00:00Z",
                                graph_names=["g"])
    payload = census["labels"]["Point"]["payload"]["derived_payload_B"]
    assert isinstance(payload, str) and payload.startswith("UNMEASURED"), payload
    # Agreeing width/dim DOES derive it.
    graph = _CensusGraph(
        catalogue=[_index_row("Point", "embedding", 384)],
        labels=["Point"],
        vectors={"Point": (2, [0.0] * 384)},
    )
    census = probe.vector_census(graph, "g", at="2026-11-01T00:00:00Z",
                                graph_names=["g"])
    assert census["labels"]["Point"]["payload"]["derived_payload_B"] == 2 * 384 * 4


def test_the_census_does_not_claim_coverage_it_could_not_measure():
    """`unindexed = 0` needs BOTH numbers; with one unreadable it is a claim.

    Measured failure this pins (review round 7, P2): a covering `embedding`
    index plus an unreadable stored width printed `unindexed=0` — full coverage,
    measured-looking — beside `payload=UNMEASURED(stored width not probeable)`
    in the same row. `0` is the one column this instrument quotes into `#5090`'s
    body as the epic's cost figure, so it is the last column allowed to say `0`
    without having measured anything.
    """
    # (a) the width probe returned nothing (a vecf32 read that yielded no row)
    graph = _CensusGraph(
        catalogue=[_index_row("Point", "embedding", 384)],
        labels=["Point"],
        vectors={"Point": (5, None)},
    )
    row = probe.vector_census(graph, "g", at="2026-11-01T00:00:00Z",
                              graph_names=["g"])["labels"]["Point"]
    assert row["indexed"] is True, row
    assert str(row["unindexed"]).startswith("UNMEASURED"), row
    assert "stored width" in row["unindexed"], row

    # (b) the width probe RAISED — the same branch via the swallowed exception
    class _RaisingWidth(_CensusGraph):
        def query(self, cypher, params=None, timeout=None):
            if "RETURN n.embedding" in cypher:
                raise RuntimeError("Timeout reading from socket")
            return super().query(cypher, params, timeout)

    graph = _RaisingWidth([_index_row("Point", "embedding", 384)], ["Point"],
                          {"Point": (5, [0.0] * 384)})
    row = probe.vector_census(graph, "g", at="2026-11-01T00:00:00Z",
                              graph_names=["g"])["labels"]["Point"]
    assert str(row["unindexed"]).startswith("UNMEASURED"), row

    # (c) the catalogue row carries no dimension — `dimension` is REQUIRED in a
    # vector index's OPTIONS, so its absence is an unreadable cell, not a label
    # with no dimension.
    graph = _CensusGraph(
        catalogue=[_index_row("Point", "embedding", None)],
        labels=["Point"],
        vectors={"Point": (5, [0.0] * 384)},
    )
    row = probe.vector_census(graph, "g", at="2026-11-01T00:00:00Z",
                              graph_names=["g"])["labels"]["Point"]
    assert str(row["unindexed"]).startswith("UNMEASURED"), row
    assert "index dimension" in row["unindexed"], row


def test_the_census_still_reports_zero_when_there_is_nothing_to_cover():
    """The refusal above must not over-refuse: 0 stored vectors needs no width.

    `stored == 0` is a measured zero (`MATCH … RETURN count(n)` answered), so
    `unindexed = 0` is provable without a probe — pinning it keeps the new
    `UNMEASURED` branch from turning an empty label into an unmeasured one.
    """
    graph = _CensusGraph(
        catalogue=[_index_row("Point", "embedding", 384)],
        labels=["Point"],
        vectors={"Point": (0, None)},
    )
    row = probe.vector_census(graph, "g", at="2026-11-01T00:00:00Z",
                              graph_names=["g"])["labels"]["Point"]
    assert row["stored"] == 0, row
    assert row["unindexed"] == 0, row
    assert row["payload"]["derived_payload_B"] == 0, row


def test_the_census_measures_the_graph_its_row_NAMES(monkeypatch):
    """`--graph B` with a `--uri …/A` must query B (review P1, no falsifier)."""
    selected: list[str] = []

    class _Db:
        def select_graph(self, name):
            selected.append(name)
            return _CensusGraph([], [name], {})

    monkeypatch.setattr(probe, "_connect", lambda uri: (_Db(), ["A", "B"]))
    rc = probe.main(["--census", "--json",
                     "--uri", "docker://:@127.0.0.1:1/A", "--graph", "B"])
    assert rc == 0, rc
    assert selected == ["B"], selected


def test_an_unparseable_module_holds_the_rows(tmp_path, monkeypatch):
    """A tree that will not fully parse must not read as clean (review P2-1),
    and more than one of them must not collapse into a single opaque entry
    (review round 5, P3)."""
    (tmp_path / "tortoise").mkdir()
    (tmp_path / "tortoise" / "embeddings.py").write_text(
        "def encode_for_row(t, d):\n    return None\n", encoding="utf-8")
    (tmp_path / "tortoise" / "writer.py").write_text(
        "from .embeddings import encode_for_row\n"
        "def _upsert_point(ev):\n"
        "    return 'MERGE (n:Point {id: 1}) SET n.embedding = $e'\n",
        encoding="utf-8")
    (tmp_path / "tortoise" / "broken.py").write_text(
        "def oops(:\n", encoding="utf-8")
    (tmp_path / "tortoise" / "broken_two.py").write_text(
        "def oops_again(:\n", encoding="utf-8")
    monkeypatch.setattr(probe, "REPO_ROOT", tmp_path)
    assert probe.unparsed_modules() == [
        "tortoise/broken.py", "tortoise/broken_two.py"
    ], probe.unparsed_modules()
    assert probe.score_path_bullet().verdict == "PARTIAL"


def test_the_predicate_walk_follows_subpackages_and_absolute_imports(
        tmp_path, monkeypatch):
    """The two import shapes that made a second predicate read MET (review P1-3)."""
    t = tmp_path / "tortoise"
    (t / "pkg").mkdir(parents=True)
    (t / "pkg" / "__init__.py").write_text(
        "import re\n_P = re.compile(r'x')\n", encoding="utf-8")
    (t / "abs_rules.py").write_text("import re\n_A = re.compile(r'y')\n",
                                    encoding="utf-8")
    (t / "value_gate.py").write_text(
        "import re\n_V = re.compile(r'#\\d+')\nRULES = ('r',)\n", encoding="utf-8")
    (t / "embed_gate.py").write_text(
        "from . import value_gate, pkg\n"
        "from tortoise.abs_rules import _A\n"
        "RULE_SET = value_gate.RULES\n", encoding="utf-8")
    monkeypatch.setattr(probe, "REPO_ROOT", tmp_path)
    counted = probe.count_embed_predicates()
    assert counted["count"] == 3, counted
    assert counted["verdict"] == "DRIFT", counted


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


def test_a_failed_population_read_is_unmeasured_not_a_narrowed_population():
    """`CALL db.labels()` failing must not fall back to `indexed | served`."""

    class _NoLabels(_CensusGraph):
        def query(self, cypher, params=None, timeout=None):
            if "db.labels()" in cypher:
                raise RuntimeError("Timeout reading from socket")
            return super().query(cypher, params, timeout)

    graph = _NoLabels([_index_row("Point", "embedding", 384)], ["Point"],
                      {"Point": (1, [0.0] * 384)})
    census = probe.vector_census(graph, "g", at="2026-11-01T00:00:00Z",
                                 graph_names=["g"])
    assert census["labels"] == {}
    assert census["catalogue"].startswith("UNMEASURED"), census["catalogue"]


def test_an_unreadable_catalogue_row_is_not_an_unindexed_label():
    """A row we could not read must not assert `indexed: False`."""
    graph = _CensusGraph(
        catalogue=[["Point", ["embedding"], "not-a-dict"]],
        labels=["Point"], vectors={"Point": (7, [0.0] * 384)},
    )
    census = probe.vector_census(graph, "g", at="2026-11-01T00:00:00Z",
                                 graph_names=["g"])
    row = census["labels"]["Point"]
    assert isinstance(row["indexed"], str) and row["indexed"].startswith("UNMEASURED"), row
    assert str(row["unindexed"]).startswith("UNMEASURED"), row


def test_the_predicate_walk_follows_plain_import_and_multiline_from(
        tmp_path, monkeypatch):
    """`import tortoise.X` and `from .pkg import (\n a,\n)` — both in this tree."""
    t = tmp_path / "tortoise"
    (t / "pkg").mkdir(parents=True)
    (t / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (t / "pkg" / "sub_rules.py").write_text("import re\n_S = re.compile(r'z')\n",
                                            encoding="utf-8")
    (t / "abs_rules.py").write_text("import re\n_A = re.compile(r'y')\n",
                                    encoding="utf-8")
    (t / "value_gate.py").write_text("import re\n_V = re.compile(r'#\\d+')\nRULES=('r',)\n",
                                     encoding="utf-8")
    (t / "embed_gate.py").write_text(
        "import tortoise.abs_rules as ar\n"
        "from .pkg import (\n    sub_rules,\n)\n"
        "from . import value_gate\nRULE_SET = value_gate.RULES\n", encoding="utf-8")
    monkeypatch.setattr(probe, "REPO_ROOT", tmp_path)
    counted = probe.count_embed_predicates()
    assert counted["count"] == 3, counted
    assert counted["verdict"] == "DRIFT", counted


def test_the_shipped_default_is_searched_not_assumed(monkeypatch):
    """The default is READ from the shipped surfaces, and the row reports it.

    ⛔ Deliberately NOT `assert shipped["on"] is False`. That pins TODAY's
    answer to a question `#5090` intends to change — row 1's recorded remainder
    is "turn the flag on and measure the rate on real rows" — so the day it is
    done this falsifier would red on a CORRECT change, and the cheapest-looking
    repair would be to weaken it: a pinned answer to a moving question is the
    stale-flag class `#10465` exists to end (review round 7, P3). What is pinned
    here is the real-tree MECHANISM the synthetic roots cannot cover: the walk
    reaches the repository's own shipped surfaces, an ON claim is sourced from a
    file the walk actually read, and `score_rule_bullet` reports the SAME read
    rather than only the ambient env — `measure()` clears the env, so an
    env-only read would report `False` forever and bullet 1 could never see a
    shipped default.
    """
    monkeypatch.delenv("TORTOISE_EMBED_GATE", raising=False)
    shipped = probe.flag_in_shipped_configs("TORTOISE_EMBED_GATE")
    assert isinstance(shipped["on"], bool), shipped
    # ⛔ `searched` is the WALK (every file this repo's shipped surfaces hold),
    # `mentions` is the subset that names the flag. The previous version of this
    # falsifier asserted `shipped["searched"]` was non-empty and justified it as
    # "the flag is mentioned in the tree, so an empty list means the walk missed
    # a path" — false, because `searched` then held only the mentions, so an
    # empty list meant only "nothing mentions it", which is the expected state
    # for an unset flag. It passed on a prose sentence in
    # `config/v4-mechanisms.yml`; reflow that sentence and it red with a message
    # sending the fixer after a non-existent walk bug.
    assert shipped["searched"], shipped
    assert set(shipped["mentions"]) <= set(shipped["searched"]), shipped
    assert not set(shipped["unreadable"]) & set(shipped["searched"]), shipped
    for hit in shipped["files"]:  # an ON claim is SOURCED, never asserted
        assert hit.split(":")[0] in shipped["searched"], hit
    row = probe.score_rule_bullet()
    note = next(n for n in row.notes if n.startswith("gate enabled by default:"))
    assert f"gate enabled by default: {shipped['on']}" in note, (note, shipped)


@pytest.mark.parametrize("text", [
    # Plain prose in a `note: >-` folded scalar — the natural documentation
    # form of the flag this repo's own `config/v4-mechanisms.yml` describes.
    "note: >-\n  Set TORTOISE_EMBED_GATE=1 to enable the gate.\n",
    # The same sentence with the flag in backticks (today's shape in
    # `config/v4-mechanisms.yml:431`).
    "note: >-\n  Set `TORTOISE_EMBED_GATE=1` to enable the gate.\n",
    # A GitHub-Actions step NAME. `stripped[0]` is `-`, so the pre-fix guard
    # did not skip it, `lstrip("-")` left `name: verify …`, and the value
    # token after the flag was `1`.
    "jobs:\n  t:\n    steps:\n      - name: verify TORTOISE_EMBED_GATE=1 takes effect\n",
    # A COMMENTED list item: the `#` test ran BEFORE the dash strip, so the
    # comment marker was the second character and this read ON.
    "environment:\n  - # TORTOISE_EMBED_GATE=1\n",
    # A bare mention with no separator at all.
    "note: >-\n  (`TORTOISE_EMBED_GATE`), off by default\n",
    # A compose ARRAY declaration with no value: the spec says the variable is
    # then unset, so it is not an ON.
    "environment:\n  - TORTOISE_EMBED_GATE\n",
    # An explicitly OFF value, including compose's own `false` spelling.
    "environment:\n  TORTOISE_EMBED_GATE: \"false\"\n",
])
def test_a_line_that_merely_mentions_the_flag_is_not_a_shipped_default(
        text, tmp_path, monkeypatch):
    """Only an ASSIGNMENT of the flag can read as shipped ON.

    Measured on the pre-fix code: `Set TORTOISE_EMBED_GATE=1 to enable`,
    `- name: verify TORTOISE_EMBED_GATE=1 takes effect` and
    `- # TORTOISE_EMBED_GATE=1` each returned `on=True`. That is the one input
    that can move bullet 1 toward `MET` (`score_rule_bullet` → `on_by_default`),
    so once `#5090`'s stated remainder (turn the flag on, resolve the writers) is
    done, a DOCUMENTATION edit alone would complete a DoD row — the class
    `#10465` indicator 1 exists to prevent.
    """
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "x.yml").write_text(text, encoding="utf-8")
    monkeypatch.setattr(probe, "REPO_ROOT", tmp_path)
    shipped = probe.flag_in_shipped_configs("TORTOISE_EMBED_GATE")
    assert shipped["on"] is False, shipped
    # It is still RECORDED — a mention is reported, just not scored as a value.
    assert shipped["mentions"] == ["config/x.yml"], shipped


@pytest.mark.parametrize("text", [
    "environment:\n  - TORTOISE_EMBED_GATE=1\n",            # compose array
    "environment:\n  TORTOISE_EMBED_GATE: \"true\"\n",      # compose map
    "environment:\n  TORTOISE_EMBED_GATE : 1\n",           # YAML spaced key
    "TORTOISE_EMBED_GATE=yes\n",                            # .env / dotenv
    "ENV TORTOISE_EMBED_GATE=on\n",                          # Dockerfile
    "export TORTOISE_EMBED_GATE=1\n",                        # shell
    "TORTOISE_EMBED_GATE=1  # enable it\n",                  # trailing comment
])
def test_every_real_assignment_shape_still_reads_as_shipped_ON(
        text, tmp_path, monkeypatch):
    """The tighter test must not turn a REAL shipped ON into a false `False`.

    These are the shapes `container_spec.md` §environment defines (map and
    array) plus the shell/Dockerfile spellings; a walk that stopped seeing them
    would let bullet 1 report `off by default` for a flag that ships ON.
    """
    (tmp_path / ".env.example").write_text(text, encoding="utf-8")
    monkeypatch.setattr(probe, "REPO_ROOT", tmp_path)
    shipped = probe.flag_in_shipped_configs("TORTOISE_EMBED_GATE")
    assert shipped["on"] is True, shipped
    assert shipped["files"], shipped


def test_searched_is_the_walk_not_the_list_of_mentions(tmp_path, monkeypatch):
    """`searched` must answer "what did 'nowhere' cover?" — the documented contract.

    Pre-fix, this file (which does not mention the flag) was absent from
    `searched`, so for the ordinary case the function is built for — a flag set
    NOWHERE and mentioned NOWHERE — the promised coverage was `[]`. That is the
    same defect class as the exit-code contract `test_an_unmeasurable_run_exits_two`
    was added for: a documented promise the code does not implement.
    """
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "quiet.yml").write_text("nothing here\n", encoding="utf-8")
    monkeypatch.setattr(probe, "REPO_ROOT", tmp_path)
    shipped = probe.flag_in_shipped_configs("TORTOISE_EMBED_GATE")
    assert shipped["on"] is False, shipped
    assert shipped["searched"] == ["config/quiet.yml"], shipped
    assert shipped["mentions"] == [], shipped


def test_an_unreadable_surface_is_named_not_silently_excluded(tmp_path, monkeypatch):
    """A file the walk could not read must not read as a measured `False`."""
    (tmp_path / ".env.example").write_text("TORTOISE_EMBED_GATE=1\n",
                                           encoding="utf-8")
    monkeypatch.setattr(probe, "REPO_ROOT", tmp_path)
    real_read_text = Path.read_text

    def _boom(self, *a, **kw):
        if self.name == ".env.example":
            raise OSError("unreadable")
        return real_read_text(self, *a, **kw)

    monkeypatch.setattr(Path, "read_text", _boom)
    shipped = probe.flag_in_shipped_configs("TORTOISE_EMBED_GATE")
    assert shipped["on"] is False, shipped
    assert shipped["unreadable"] == [".env.example"], shipped
    assert shipped["searched"] == [], shipped


def test_a_writer_that_names_an_unserved_label_scores_NOT_MET(tmp_path, monkeypatch):
    """The VERDICT, not just the set difference (review round 3, P3)."""
    (tmp_path / "tortoise").mkdir()
    (tmp_path / "tortoise" / "embeddings.py").write_text(
        "def encode_for_row(t, d):\n    return None\n", encoding="utf-8")
    (tmp_path / "tortoise" / "writer.py").write_text(
        "from .embeddings import encode_for_row\n"
        "def _upsert_document(ev):\n"
        "    emb = encode_for_row('x', 3)\n"
        "    return 'MERGE (n:Document {id: 1}) SET n.embedding = $emb', emb\n",
        encoding="utf-8")
    monkeypatch.setattr(probe, "REPO_ROOT", tmp_path)
    assert probe.score_path_bullet().verdict == "NOT MET"


def test_a_commented_or_blank_flag_line_is_not_a_shipped_default(tmp_path, monkeypatch):
    """`# TORTOISE_EMBED_GATE=1` is not a default; `FLAG=` is not an IndexError.

    Measured before this pin: the commented line read ON (the one input that can
    flip bullet 1 to MET without measuring), and a blank value raised
    `IndexError` out of the whole instrument.
    """
    (tmp_path / ".env.example").write_text(
        "# TORTOISE_EMBED_GATE=1  # not shipped yet\n"
        "TORTOISE_EMBED_GATE=\n"
        "# see TORTOISE_EMBED_GATE\n", encoding="utf-8")
    monkeypatch.setattr(probe, "REPO_ROOT", tmp_path)
    assert probe.flag_in_shipped_configs("TORTOISE_EMBED_GATE")["on"] is False
    (tmp_path / ".env.example").write_text("TORTOISE_EMBED_GATE=true\n",
                                           encoding="utf-8")
    assert probe.flag_in_shipped_configs("TORTOISE_EMBED_GATE")["on"] is True


def test_a_flag_at_end_of_line_does_not_raise(tmp_path, monkeypatch):
    (tmp_path / ".env.example").write_text("see TORTOISE_EMBED_GATE\n",
                                           encoding="utf-8")
    monkeypatch.setattr(probe, "REPO_ROOT", tmp_path)
    assert probe.flag_in_shipped_configs("TORTOISE_EMBED_GATE")["on"] is False


def test_a_collection_of_patterns_is_a_second_rule_set(tmp_path, monkeypatch):
    """`_PATS = [re.compile(…), re.compile(…)]` is the most natural second list."""
    t = tmp_path / "tortoise"
    t.mkdir()
    (t / "value_gate.py").write_text(
        "import re\n_V = re.compile(r'#\\d+')\nRULES = ('r',)\n", encoding="utf-8")
    (t / "embed_gate.py").write_text(
        "import re\n_PATS = [re.compile(r'\\d{4}'), re.compile(r'\\bID\\b')]\n"
        "from . import value_gate\nRULE_SET = value_gate.RULES\n", encoding="utf-8")
    monkeypatch.setattr(probe, "REPO_ROOT", tmp_path)
    counted = probe.count_embed_predicates()
    assert counted["count"] == 2, counted
    assert counted["verdict"] == "DRIFT", counted


def test_served_labels_are_the_RUNNERS_declaration():
    """`VALID_ENTITY_TYPES` + `entity_label`, not a second lookup table."""
    from tortoise.security import VALID_ENTITY_TYPES, entity_label

    assert probe.served_labels() == {entity_label(t) for t in VALID_ENTITY_TYPES}



# ── Writer identity, and the guard that has to be able to RUN ───────────────


def test_two_package_inits_keep_distinct_writer_identities(tmp_path, monkeypatch):
    """`path.stem` is a BASENAME: every package `__init__.py` collapsed to it.

    Two writers in two packages — one per `__init__.py` — used to share the key
    `__init__::_upsert_point`, so the second became `duplicate`, which
    `written_labels()` excluded. The map reported ONE writer and bullet 2
    compared a label set with both writers missing from it (review round 5, P2).
    """
    (tmp_path / "tortoise").mkdir()
    (tmp_path / "tortoise" / "embeddings.py").write_text(
        "def encode_for_row(t, d):\n    return None\n", encoding="utf-8")
    for pkg, label in (("alpha", "Point"), ("beta", "Object")):
        (tmp_path / "tortoise" / pkg).mkdir()
        (tmp_path / "tortoise" / pkg / "__init__.py").write_text(
            "from ..embeddings import encode_for_row\n"
            "def _upsert_point(ev):\n"
            "    vec = encode_for_row('x', None)\n"
            f"    return 'MERGE (n:{label} {{id: 1}}) SET n.embedding = $vec'\n",
            encoding="utf-8")
    monkeypatch.setattr(probe, "REPO_ROOT", tmp_path)
    writers = probe.embedding_writers()
    assert "tortoise.alpha::_upsert_point" in writers, sorted(writers)
    assert "tortoise.beta::_upsert_point" in writers, sorted(writers)
    assert not [k for k in writers if k.startswith("__init__::")], sorted(writers)
    written = probe.written_labels(writers)
    assert written["tortoise.alpha::_upsert_point"] == "Point", written
    assert written["tortoise.beta::_upsert_point"] == "Object", written


def test_a_duplicate_writer_identity_holds_bullet_two_at_partial(monkeypatch):
    """Bullet 2 must not read `MET` over a writer whose label it lost.

    A `duplicate` entry carries no label, so including it as `UNKNOWN` is the
    only honest option — excluding it made the two rows disagree about the same
    hole: bullet 1 held `PARTIAL` on `duplicates` while bullet 2 said `MET`
    (review round 5, P2).
    """
    writers = {
        "tortoise.x::_upsert_point": {
            "callee": probe.SEAM_CALL, "at": "tortoise/x.py:1",
            "kind": "gated", "label": "Point",
        },
        "tortoise.y::_upsert_point": {
            "callee": probe.SEAM_CALL, "at": "tortoise/y.py:2",
            "kind": "duplicate", "label": "UNKNOWN(duplicate writer name)",
        },
    }
    monkeypatch.setattr(probe, "embedding_writers", lambda: writers)
    row = probe.score_path_bullet()   # every label IS served here
    assert row.verdict == "PARTIAL", row.observed
    assert any("duplicate writer identities" in n for n in row.notes), row.notes
    assert probe.written_labels()["tortoise.y::_upsert_point"].startswith("UNKNOWN")


def _pre_312_interpreter() -> str | None:
    """A real `python3` older than the floor, or None (as the guard suite does)."""
    for candidate in ("/usr/bin/python3", shutil.which("python3")):
        if not candidate or not Path(candidate).exists():
            continue
        run = subprocess.run(
            [candidate, "-c",
             "import sys; sys.exit(0 if sys.version_info < (3, 12) else 1)"],
            capture_output=True, text=True,
        )
        if run.returncode == 0:
            return candidate
    return None


def test_the_tool_parses_under_the_floor():
    """`#5128`'s guard is only worth having if the file can REACH it.

    `tests/test_entry_point_python_guard.py` requires the refusal to be the
    first statement, which is worthless for a file the older interpreter cannot
    tokenize: nested same-quote f-strings are PEP 701 **syntax** (3.12), so the
    module dies at `f"…{d['at']}…"` before the guard runs. This pins the
    remedy, not the guard.

    Declared bound: a box with no pre-3.12 interpreter SKIPS visibly rather
    than passing.
    """
    old = _pre_312_interpreter()
    if old is None:
        pytest.skip("no <3.12 interpreter on this box: cannot re-derive the parse")
    run = subprocess.run(
        [old, "-c", "import ast,sys; ast.parse(open(sys.argv[1]).read())",
         str(Path(probe.__file__))],
        capture_output=True, text=True,
    )
    assert run.returncode == 0, (
        f"{old} cannot parse {probe.__file__}, so its `#5128` guard never runs "
        f"and the refusal is documentary (use different quote characters):\n"
        f"{run.stderr}"
    )
