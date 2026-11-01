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
| `test_every_embedding_write_path_reaches_the_gate_seam` | bullet 1's `enforced` half | a writer stops calling `encode_for_row` (the point of #5008), or a new one appears unmapped |
| `test_every_embedded_label_has_a_query_path` | bullet 2 | an embedding write path serves a label `run_vector_query` cannot query |
| `test_all_three_mechanisms_are_visible_to_the_caller` | bullet 3 (`#4999`) | the fallback stops announcing itself, or two paths report the same mechanism |
| `test_the_census_refuses_to_report_zero_for_an_unreadable_store` | the 2026-09-24 error (`stored`-only / silent 0) | a store that could not be read is reported as `0` anything |
| `test_an_unmeasurable_run_exits_two` | the documented exit-code contract | a run that could not measure exits 0 or 1, so a caller reads "could not measure" as "a row was NOT MET" |
| `test_the_instrument_is_selected_by_its_own_subjects` | the pins above actually RUN in CI | a subject diff stops selecting this file — a falsifier that never executes |

⛔ **These tests never write a graph and never flip a gate.** `#10465` is
measurement-only; the instrument is read-only and its pin must be too.
"""
from __future__ import annotations

import os
import sys

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
    for writer in ("_upsert_point_props", "_upsert_subject", "_upsert_object",
                   "_upsert_document", "_upsert_event"):
        assert writer in seams, (
            f"{writer} embeds a written row without reaching encode_for_row — "
            f"the ungated-primitive state #5008 closed. Measured seams: {sorted(seams)}"
        )
    # And the derived labels must not invent one.
    written = probe.written_labels()
    assert not [v for v in written.values() if v.startswith("UNKNOWN")], written


def test_every_embedded_label_has_a_query_path():
    """Bullet 2: nothing is STORED that `run_vector_query` cannot query."""
    row = probe.score_path_bullet()
    assert row.verdict == "MET", row.observed
    written = set(probe.written_labels().values())
    assert written <= probe.served_labels(), written - probe.served_labels()


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


def test_a_writer_that_names_an_unserved_label_reads_NOT_MET(tmp_path, monkeypatch):
    """Bullet 2's `NOT MET` branch must be reachable (review P1 #3).

    A writer whose own Cypher names `:Document` — a label `run_vector_query`
    does not serve — is the stored-and-unqueryable class the bullet forbids. A
    label map drawn from the query vocabulary itself can never produce it.
    """
    (tmp_path / "tortoise").mkdir()
    (tmp_path / "tortoise" / "embeddings.py").write_text(
        "def encode_for_row(t, d):\n    return None\n", encoding="utf-8")
    (tmp_path / "tortoise" / "writer.py").write_text(
        "from .embeddings import encode_for_row\n"
        "def _upsert_document(ev):\n"
        "    emb = encode_for_row('x', 3)\n"
        "    q = 'MERGE (n:Document {id: 1}) SET n.embedding = $emb'\n"
        "    return q, emb\n", encoding="utf-8")
    monkeypatch.setattr(probe, "REPO_ROOT", tmp_path)
    written = probe.written_labels()
    assert written == {"_upsert_document": "Document"}, written
    served = probe.served_labels()
    assert "Document" not in served, served
    assert set(written.values()) - served == {"Document"}


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
    assert writers["main"]["kind"] == "ungated", writers


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
            label = cypher.split("MATCH (n:")[1].split(")")[0]
            rows = [[self._vectors.get(label, (0, None))[0]]]
        elif "RETURN n.embedding" in cypher:
            label = cypher.split("MATCH (n:")[1].split(")")[0]
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
    """A tree that will not fully parse must not read as clean (review P2-1)."""
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
    monkeypatch.setattr(probe, "REPO_ROOT", tmp_path)
    assert probe.unparsed_modules() == ["UNPARSEABLE"], probe.unparsed_modules()
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
