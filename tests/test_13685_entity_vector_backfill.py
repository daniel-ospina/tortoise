"""`#13685` — the graph→record entity-vector BACKFILL, and its falsifiers.

`tools/entity_vector_backfill.py` copies each graph node's stored `embedding` into the
matching RECORD row's `embedding` jsonb — only where that column is NULL — so the
generated `embedding_vec` column computes and the record can answer a dense query for a
node whose only readable vector was in the graph. No model call, no graph mutation, no
overwrite.

Every test below falsifies ONE load-bearing claim. The two the issue names explicitly
are marked ⛔, because they are the two whose absence would make this instrument
destructive rather than additive:

| test | claim it falsifies | it goes RED when … |
|---|---|---|
| ⛔ `test_execute_never_overwrites_a_non_null_record_vector` | "only where the column is NULL" | a row that already holds a vector is rewritten |
| ⛔ `test_an_unmatched_graph_node_is_counted_and_never_dropped` | "a node whose row cannot be found is counted and reported" | an unmatched graph vector disappears from the plan and the report |
| ⛔ `test_execute_refuses_without_a_plan_or_a_digest` | the two token preconditions | `--execute` can run from a live read, or from a file alone |
| `test_point_is_in_the_default_population` | `#13685`'s scope addition | the default skips `:Point` — the label with the largest gap |
| `test_the_key_mapping_is_the_one_the_schema_pins` | `n.id`↔`node_key`, `eventId`↔`event_id` | the key property/column pair moves |
| `test_a_record_row_whose_contract_column_diverged_refuses` | the schema's own key equality | a row where `id ≠ node_key` is silently copied into |
| `test_preview_writes_nothing_to_either_store` | preview is the default | `build_plan` writes, or travels the write-capable verb |
| `test_a_second_execute_is_a_no_op` | "idempotent: a second run is a no-op" | refilling reports work it did not do |
| `test_execute_refuses_a_population_that_moved_since_the_plan` | the exact-population digest | a node added after approval is copied anyway |
| `test_execute_refuses_target_drift` | a plan is an authority over ONE graph | it is applied to another graph |
| `test_an_empty_population_refuses_unless_expect_empty` | "nothing to fill" is a refusal | an empty run reports a finished backfill |
| `test_an_uncopyable_vector_is_counted_not_written` | no fabricated vector | a non-384 payload is written and silently nulls the column |
| `test_the_per_label_numbers_are_the_census_numbers` | the census is the instrument | this tool's numbers drift from `parity.census()`'s |
| `test_buckets_has_exactly_one_definition` | ONE `_buckets`, not two | a second copy shadows the first (last-def-wins) — and `ruff`'s F811 does NOT catch an `_`-prefixed name |
| `test_the_tool_reads_the_census_module_the_suite_patches` | the tool calls the module object the suite patches | it holds a second copy of the census module, so every alias patch patches nothing |
| `test_the_digest_is_taken_over_the_copyable_population` | the digest is over the SORTED population | it becomes order-dependent, or stops covering a key that moved |
| `test_a_shortfall_is_counted_not_absorbed` | a row filled by a racing writer is REPORTED | `shortfall` stops counting it, and a partial run reads as a complete one |
| `test_a_vector_that_vanishes_between_the_census_and_the_read_is_counted` | a vector that went away is REPORTED | `vanished` stops counting it |
| `test_a_failed_batch_write_exits_3_with_the_durable_report` | exit 3 + `partial` on a failed batch | `WriteFailed` escapes `main()` as a traceback and exit 1 ("usage error") |
| `test_the_refusal_set_is_closed` | the refusal tokens are a closed set | a refusal is raised with an unpublished token |
| `test_the_scratch_postgres_round_trip_fills_and_the_census_reports_parity` | end-to-end on `pgvector/pgvector:pg16` | the fill does not reach the generated column, or the census still reads GRAPH-ONLY |
| ⛔ `test_the_sampled_point_becomes_readable_by_the_dense_leg` | `#13685`'s Point acceptance | a previously graph-only Point stays invisible to a dense query |

The end-to-end legs run against a THROWAWAY `pgvector/pgvector:pg16` container (a plain
`postgres` image cannot apply `20261109000001_record_embedding_vector.sql`'s `vector`
column) plus an EMBEDDED FalkorDB (`falkordblite`, a declared dependency) — never a hosted
database. They SKIP (never pass) when Docker or `psycopg2` is unavailable, because a leg
that silently passes on its own absence is the class this repo files under "an absence is
not a verdict".
"""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tools import entity_vector_backfill as backfill
from tools import entity_vector_parity_census as parity_mod

#: ⛔ THE SAME MODULE OBJECT THE TOOL USES. `entity_vector_backfill` puts its own
#: directory on `sys.path` and imports `entity_vector_parity_census` as a TOP-LEVEL
#: module, which is a DIFFERENT object from `tools.entity_vector_parity_census`. A test
#: that monkeypatched the package-qualified one would patch a module the code under
#: test never calls (`#13629`'s suite records the same trap).
parity = backfill.parity
# ⚠️ NO IMPORT-TIME ASSERT HERE. This spot used to carry
# `assert parity is not parity_mod or parity.__name__ == parity_mod.__name__`, which is a
# TAUTOLOGY — true whichever side of `is not` holds — so no mutation of this tree could
# ever redden it. The trap it named is real, and it is pinned BEHAVIOURALLY below by
# `test_the_tool_reads_the_census_module_the_suite_patches`.

#: The width the record's generated column accepts. Hermetic fixtures must use it: a
#: shorter vector is `skipped_uncopyable` BY DESIGN, so a test seeding 3-long vectors
#: would measure that rule instead of the one it names.
WIDTH = backfill._RECORD_WIDTH


def _vec(seed: float, width: int = WIDTH) -> list[float]:
    """A deterministic vector whose first element identifies it."""
    return [seed] + [0.0] * (width - 1)


def _stored(vector) -> list[float]:
    """``vector`` as the tool writes it: narrowed to the store's ``float32`` width.

    ⛔ NOT A TEST CONVENIENCE. The graph holds an entity vector at the engine's
    ``float32`` width and the copy narrows through `tortoise.stored_form` on purpose, so
    "the payload is the graph's vector" means "at f32 width" — comparing against the
    un-narrowed Python float would red on a correct copy (0.1 is not representable).
    """
    narrowed = backfill._narrow(vector)
    assert narrowed is not None
    return narrowed


# ── hermetic stand-ins for the two stores ───────────────────────────────────


class _Result:
    def __init__(self, rows):
        self.result_set = rows


class _Node:
    def __init__(self, nid: int, key: str | None, vector) -> None:
        self.nid = nid
        self.key = key
        self.vector = vector


_LABEL_RE = re.compile(r"MATCH \(n:(\w+)\)")


class FakeGraph:
    """Answers exactly the statement shapes the census leg and the batch read issue.

    Deliberately NOT a Cypher engine (a fake that parsed Cypher would pass on the day
    the real statement breaks): it matches the shapes this repo owns and RECORDS the
    identity property each read was keyed by, which is how the key-mapping test can
    falsify it. `query()` — the write-capable verb — raises, so "this tool never writes
    to the graph" is measured rather than asserted in prose.
    """

    def __init__(self) -> None:
        self.nodes: dict[str, list[_Node]] = {label: [] for _t, label, _k in backfill.BACKFILLED}
        self._next = 0
        self.key_props: list[tuple[str, str]] = []

    # ── seeding ────────────────────────────────────────────────────────────
    def add(self, label: str, key: str | None, vector=None):
        self._next += 1
        node = _Node(self._next, key, _vec(0.5) if vector is None else vector)
        self.nodes.setdefault(label, []).append(node)
        return node

    # ── the verbs ──────────────────────────────────────────────────────────
    def ro_query(self, cypher, params=None):
        return _Result(self._answer(cypher, params or {}))

    def query(self, cypher, params=None):  # pragma: no cover - must never be reached
        raise AssertionError(
            "entity_vector_backfill issued a WRITE-CAPABLE graph query — this tool is "
            f"read-only on the graph: {cypher}")

    def _answer(self, cypher, params):
        label = _LABEL_RE.search(cypher).group(1)
        live = [n for n in self.nodes.get(label, []) if n.vector is not None]
        if "RETURN count(n)" in cypher:
            return [[len(live)]]
        if "ORDER BY id(n)" in cypher:
            cursor = int(params.get("cursor") or 0)
            batch = int(params.get("batch") or 0)
            page = sorted((n for n in live if n.nid >= cursor), key=lambda n: n.nid)[:batch]
            return [[n.nid, n.key] for n in page]
        if "IN $keys" in cypher:
            prop = re.search(r"n\.(\w+) IN \$keys", cypher).group(1)
            self.key_props.append((label, prop))
            wanted = set(params.get("keys") or ())
            return [[n.key, n.vector] for n in live if n.key in wanted]
        raise AssertionError(f"unexpected statement shape: {cypher}")


class FakeDoor:
    """A stand-in for `RecordDoor` whose write applies whatever it is TOLD.

    ⛔ IT APPLIES UNCONDITIONALLY, and that is the falsifier's whole design: the never-
    overwrite guard lives in the tool's `UPDATE` (`AND t.embedding IS NULL`), so a fake
    that refused the write itself would pass even if the tool asked to clobber a
    non-NULL row. Here the tool's own `fillable` computation is the only thing standing
    between a stored vector and its replacement, and the test reads the damage.
    """

    def __init__(self) -> None:
        self.tables: dict[str, dict[str, dict]] = {}
        self.calls: list[tuple[str, list[str]]] = []

    def seed(self, table: str, key: str, *, vector=None, readable: bool | None = None):
        """``vector`` set ⇒ the row has a payload; ``readable`` defaults to it."""
        row = {
            "embedding": None if vector is None else list(vector),
            "vec": None if vector is None else (list(vector) if readable is not False else None),
        }
        self.tables.setdefault(table, {})[key] = row

    def targets(self, table: str) -> dict:
        rows = self.tables.get(table, {})
        return {
            "rows": set(rows),
            "missing": {k for k, r in rows.items() if r["embedding"] is None},
            "readable": {k for k, r in rows.items() if r["vec"] is not None},
        }

    def write(self, table: str, pairs) -> int:
        self.calls.append((table, [key for key, _v in pairs]))
        rows = self.tables.setdefault(table, {})
        written = 0
        for key, vector in pairs:
            row = rows.setdefault(key, {"embedding": None, "vec": None})
            row["embedding"] = list(vector)
            row["vec"] = list(vector)
            written += 1
        return written


@pytest.fixture()
def stores(monkeypatch):
    graph, door = FakeGraph(), FakeDoor()
    door.org_id, door.graph_id = "org", "graph"
    for table, _label, _key in backfill.BACKFILLED:
        door.tables.setdefault(table, {})
    # The seam the tool reads the record through — one function, so the fake is the
    # only record the tool can see.
    monkeypatch.setattr(backfill, "_read_targets", lambda d, table: d.targets(table))
    return graph, door


def _plan(graph, door, *, labels=None, batch=backfill.DEFAULT_BATCH, graph_name="g",
          graph_uri="docker://x"):
    return backfill.build_plan(
        g=graph, door=door, graph_name=graph_name, graph_uri=graph_uri,
        labels=labels, batch=batch)


def _execute(graph, door, plan, *, graph_name="g", confirmed=None, batch=None,
             expect_empty=False, graph_uri=None):
    return backfill.execute(
        g=graph, plan=plan, door=door, graph_name=graph_name,
        graph_uri="docker://x" if graph_uri is None else graph_uri,
        confirmed=plan["digest"] if confirmed is None else confirmed,
        batch=backfill.DEFAULT_BATCH if batch is None else batch,
        expect_empty=expect_empty)


def _row(plan, label):
    return next(row for row in plan["labels"] if row["label"] == label)


def _digest_in_a_fresh_process(entries: list[tuple[str, str]], hash_seed: str) -> str:
    """`backfill.digest(entries)` computed by a SEPARATE interpreter with its own seed.

    The only way to falsify "the digest is process-stable": `set()` iteration order
    follows string hashing, randomised per process, and a same-process re-run would
    observe the same order either way.
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    script = (
        "import json, sys; sys.path.insert(0, 'tools');"
        "import entity_vector_backfill as b;"
        "print(b.digest([tuple(row) for row in json.loads(sys.argv[1])]))")
    done = subprocess.run(
        [sys.executable, "-c", script, json.dumps(entries)],
        capture_output=True, text=True, check=True, cwd=root,
        env={**os.environ, "PYTHONHASHSEED": hash_seed})
    return done.stdout.strip()


# ── the population: `:Point` is IN ─────────────────────────────────────────


def test_point_is_in_the_default_population():
    """`#13685`'s scope addition, asserted by NAME.

    The census's `RETIRED` population is the population a DESTRUCTIVE tool iterates, so
    it must keep `:Point` out (`NOT_IN_SCOPE`, pinned by `#13629`'s suite). THIS tool
    only fills, so `:Point` — ~9,106 graph-only Points, ~20 % of the corpus, invisible to
    the ARMED dense leg — belongs in its DEFAULT scope. Both halves are asserted, so a
    later edit cannot widen the stripper by "sharing" this tuple.
    """
    assert [label for _t, label, _k in backfill.BACKFILLED] == [
        "Object", "Subject", "Event", "Source", "Point"]
    assert [label for _t, label, _k in backfill._population(None)][-1] == "Point"
    # The DESTRUCTIVE population is untouched.
    assert "Point" not in {label for _t, label, _k in parity.RETIRED}
    assert parity.NOT_IN_SCOPE == ("Point",)


def test_the_key_mapping_is_the_one_the_schema_pins(stores):
    """`n.id`↔`node_key` for Point/Object/Subject, `eventId`↔`event_id`, `url`↔`Source`.

    The mapping is asserted on BOTH sides at once: the graph property the batch read
    was keyed by (captured from the statement the tool actually issued) and the record
    contract column the door's equality check reads (`id` / `event_id` / `url`).
    """
    graph, door = stores
    graph.add("Event", "evt-1")
    graph.add("Object", "obj-1")
    graph.add("Subject", "sub-1")
    graph.add("Point", "pt-1")
    graph.add("Source", "https://example.test/a")
    for table, label in (("event", "evt-1"), ("object", "obj-1"),
                         ("subject", "sub-1"), ("point", "pt-1"),
                         ("source", "https://example.test/a")):
        door.seed(table, label)

    plan = _plan(graph, door)
    _execute(graph, door, plan)

    assert ("Event", "eventId") in graph.key_props
    assert ("Point", "id") in graph.key_props
    assert ("Object", "id") in graph.key_props
    assert ("Subject", "id") in graph.key_props
    assert ("Source", "url") in graph.key_props
    assert backfill._record_contract_column("event") == "event_id"
    assert backfill._record_contract_column("point") == "id"
    # …and every one of the five was actually filled, through its own table.
    assert {table for table, _keys in door.calls} == {
        "event", "object", "subject", "point", "source"}


def test_a_record_row_whose_contract_column_diverged_refuses(monkeypatch):
    """`id ≠ node_key` on a record row is a REFUSAL, not a row to copy into.

    `chk_*_node_key_is_contract_key` equates the two, so a divergence means the schema
    contract this tool maps BY has moved. Copying anyway would write a vector onto a row
    the graph node was never matched to.
    """
    door = backfill.RecordDoor("postgresql://x", "org", "graph")

    class _Cursor:
        description = (("node_key",), ("id",), ("missing",), ("readable",))

        def execute(self, *_a, **_k):
            return None

        def fetchall(self):
            return [("node-1", "SOMETHING-ELSE", True, False)]

        def close(self):
            return None

    monkeypatch.setattr(door, "_connection", lambda: object())
    import tortoise.record_projection as rp

    monkeypatch.setattr(rp, "scope_connection", lambda _conn, _org: _Cursor())
    with pytest.raises(backfill.CensusError, match="chk_\\*_node_key_is_contract_key"):
        door.targets("object")


# ── ⛔ never overwrite ─────────────────────────────────────────────────────


def test_execute_never_overwrites_a_non_null_record_vector(stores):
    """⛔ THE FALSIFIER THE ISSUE NAMES: a non-NULL record vector is NEVER rewritten.

    Two halves, and both matter:

    * the tool must not ASK to write it — the row is `skipped_existing`, never in
      `fillable` (measured on the fake door, whose write applies whatever it is told,
      so an overturned `fillable` shows up as a replaced payload);
    * the STATEMENT must carry the guard even if it is asked — `AND t.embedding IS
      NULL` — because a row filled by a racing writer between this run's read and its
      write is exactly the case a read-time predicate cannot cover (`#13629`'s review
      lesson: a gate that trusts a predicate it read earlier is trusting a memory).
    """
    graph, door = stores
    graph.add("Object", "obj-new", _vec(0.25))
    graph.add("Object", "obj-held", _vec(0.75))
    door.seed("object", "obj-new")
    door.seed("object", "obj-held", vector=_vec(9.5))
    sentinel = list(door.tables["object"]["obj-held"]["embedding"])

    plan = _plan(graph, door, labels=["Object"])
    assert _row(plan, "Object")["skipped_existing"] == 1
    assert _row(plan, "Object")["fillable"] == 1

    result = _execute(graph, door, plan, batch=10)

    assert result["filled"] == 1
    assert door.tables["object"]["obj-held"]["embedding"] == sentinel, (
        "a record row that already held a vector was rewritten — the never-overwrite "
        "rule is the whole difference between a backfill and a clobber")
    assert door.tables["object"]["obj-new"]["embedding"] == _stored(_vec(0.25))
    assert result["skipped_existing_by_label"]["Object"] == 1

    # The statement-level guard, pinned as source (the e2e leg pins it behaviourally).
    assert "AND t.embedding IS NULL" in backfill._WRITE_SQL
    # …and one statement per BATCH, not one per row.
    assert "{values}" in backfill._WRITE_SQL


# ── ⛔ unmatched nodes are counted ─────────────────────────────────────────


def test_an_unmatched_graph_node_is_counted_and_never_dropped(stores):
    """⛔ A graph vector with no record row is COUNTED — never silently dropped.

    This tool has no authority to invent a record row (a row is the projection of a
    journal line; inventing one would make a record-sourced rebuild and a JSONL rebuild
    disagree), so the population it cannot repair must be REPORTED — in the plan, in the
    rendered preview a human approves, and in the post-run report. A silent skip is how
    a copy reports success over a hole.
    """
    graph, door = stores
    graph.add("Subject", "sub-matched")
    graph.add("Subject", "sub-orphan-a")
    graph.add("Subject", "sub-orphan-b")
    door.seed("subject", "sub-matched")

    plan = _plan(graph, door, labels=["Subject"])
    assert _row(plan, "Subject")["still_unmatched"] == 2
    assert _row(plan, "Subject")["copyable"] == 1
    assert plan["still_unmatched"] == 2
    # The approved artefact carries the number, not just the in-memory row.
    rendered = backfill.render_plan(plan)
    assert "graph-only and NOT copyable: 2 unmatched" in rendered

    result = _execute(graph, door, plan)
    assert result["still_unmatched_by_label"]["Subject"] == 2
    assert result["filled"] == 1
    # The unmatched nodes did not disappear into the void: they are still RESIDUAL, so
    # the strip stays blocked and the exit path says so.
    assert result["residual_graph_only"] == 2
    assert result["at_parity_after"] is False


# ── the two token preconditions ────────────────────────────────────────────


class _DoorRecorder:
    """A door that records construction, so "refused before any store opened" is measured."""

    def __init__(self, *a, **k):
        _DoorRecorder.opened.append(a)


_DoorRecorder.opened = []


def test_execute_refuses_without_a_plan_or_a_digest(stores, monkeypatch, tmp_path,
                                                  capsys):
    """⛔ `--execute` refuses without `--plan`, and without `--confirm-plan-digest`.

    Both refusals are raised BEFORE either store is opened — the authority to write is a
    reviewed artefact plus its token, and neither needs a connection to check. A run
    that forgot the digest must be told about the missing TOKEN, not about a store.
    """
    graph, door = stores
    _DoorRecorder.opened = []
    monkeypatch.setattr(backfill, "RecordDoor", _DoorRecorder)
    # A URI that cannot resolve: if the tool tried to bind the graph first, the message
    # and the exit code would both change.
    base = ["--uri", "docker://:pw@127.0.0.1:1/nope", "--graph-name", "g",
            "--record-dsn", "postgresql://x/y", "--org-id", "o", "--graph-id", "g",
            "--execute"]
    # ⛔ A STRUCTURALLY INCOMPLETE PLAN IS A REFUSAL, NOT A TRACEBACK (finding 2):
    # this file was ACCEPTED by `_load_plan` when only the token check existed, and then
    # raised `KeyError: 'graph'` from inside `execute` — uncaught, exit 1 (the code the
    # docstring reserves for USAGE), no `--json`.
    stub_path = tmp_path / "stub.json"
    stub_path.write_text(json.dumps({"tool": backfill.PLAN_TOOL,
                                     "version": backfill.PLAN_VERSION,
                                     "digest": "sha256:abc"}))

    assert backfill.main([*base]) == 2
    assert backfill.REFUSE_NO_PLAN in capsys.readouterr().err

    assert backfill.main([*base, "--plan", str(stub_path)]) == 2
    assert backfill.REFUSE_NO_DIGEST in capsys.readouterr().err

    assert _DoorRecorder.opened == [], (
        "a token-less --execute opened the record — the refusal must come before any "
        "store is touched")

    # The stub, WITH a token, still refuses — on its SHAPE, naming what is missing, and
    # with a `--json` report rather than a traceback.
    shape_report = tmp_path / "shape.json"
    assert backfill.main([*base, "--plan", str(stub_path),
                          "--confirm-plan-digest", "sha256:abc",
                          "--json", str(shape_report)]) == 2
    shape_err = capsys.readouterr().err
    assert backfill.REFUSE_PLAN_UNREADABLE in shape_err
    assert "graph is missing" in shape_err, (
        f"the refusal does not name the missing field: {shape_err!r}")
    assert _DoorRecorder.opened == [], "an unreadable plan still opened a store"
    shape_json = json.loads(shape_report.read_text())
    assert shape_json["refusal"] == backfill.REFUSE_PLAN_UNREADABLE
    assert shape_json["wrote"] is False

    # And the digest must be the plan's OWN, not merely present (an edited plan).
    graph.add("Object", "obj-1")
    door.seed("object", "obj-1")
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(_plan(graph, door, labels=["Object"])))
    report_path = tmp_path / "refusal.json"
    code = backfill.main([*base, "--plan", str(plan_path),
                          "--confirm-plan-digest", "sha256:not-the-plans",
                          "--json", str(report_path)])
    assert code == 2
    assert backfill.REFUSE_DIGEST_MISMATCH in capsys.readouterr().err
    # ⛔ `--json` on a REFUSAL too: a caller gating on a machine-readable report must not
    # get no file on exactly the run that refused.
    report = json.loads(report_path.read_text())
    assert report["outcome"] == "refused"
    assert report["refusal"] == backfill.REFUSE_DIGEST_MISMATCH
    assert report["wrote"] is False


def test_require_tokens_raises_the_published_tokens():
    """The unit half of the same rule, so a caller (not just `main`) is covered."""
    with pytest.raises(backfill.Refusal) as no_plan:
        backfill._require_tokens(SimpleNamespace(plan=None, confirm_plan_digest=None))
    assert no_plan.value.token == backfill.REFUSE_NO_PLAN
    with pytest.raises(backfill.Refusal) as no_digest:
        backfill._require_tokens(
            SimpleNamespace(plan="/tmp/x.json", confirm_plan_digest=None))
    assert no_digest.value.token == backfill.REFUSE_NO_DIGEST
    with pytest.raises(backfill.Refusal) as unreadable:
        backfill._require_tokens(
            SimpleNamespace(plan="/nonexistent/plan.json", confirm_plan_digest="s"))
    assert unreadable.value.token == backfill.REFUSE_PLAN_UNREADABLE


# ── preview, idempotence, drift, empty ─────────────────────────────────────


def test_preview_writes_nothing_to_either_store(stores, tmp_path):
    """Preview is the DEFAULT: it reads both stores and writes NEITHER.

    The fake graph's `query()` raises (the write-capable verb must never be travelled),
    and the fake door's write log must stay empty.
    """
    graph, door = stores
    graph.add("Point", "pt-1")
    door.seed("point", "pt-1")

    plan = _plan(graph, door)
    assert plan["total_fillable"] == 1
    assert door.calls == [], "the preview wrote to the record"
    # The digest is over the COPYABLE population, re-derived here from the stores. A
    # `json.loads(json.dumps(str))` round-trip used to sit on this line: identity, so it
    # could not fail under any mutation.
    assert plan["population_digest"] == backfill.digest([("Point", "pt-1")])
    assert backfill.render_plan(plan).splitlines()[0].startswith("entity-vector-backfill PLAN")


def test_the_digest_is_taken_over_the_copyable_population(stores):
    """The digest covers the WHOLE population, and it is STABLE ACROSS PROCESSES.

    Three properties: the plan's token is re-derivable from the population, one key
    leaving the set moves it, and — the one `sorted()` actually buys — a second PROCESS
    computes the same token. `set()` iteration order follows string hashing, which Python
    randomises per process, so a digest built without `sorted()` would differ between the
    run that PRINTED the plan and the run that EXECUTES it: the operator's token would
    stop matching a plan nobody edited, and every execution would refuse PLAN_STALE.
    """
    graph, door = stores
    graph.add("Point", "pt-a", _vec(0.1))
    graph.add("Point", "pt-b", _vec(0.2))
    door.seed("point", "pt-a")
    door.seed("point", "pt-b")
    plan = _plan(graph, door, labels=["Point"])

    entries = [("Point", "pt-a"), ("Point", "pt-b")]
    assert plan["population_digest"] == backfill.digest(entries)
    assert backfill.digest(entries) != backfill.digest(entries[:1]), (
        "the digest does not cover the WHOLE population — a key left the set without "
        "moving the token")

    wide = [("Object", f"obj-{index:02d}") for index in range(8)]
    across_processes = {_digest_in_a_fresh_process(wide, seed)
                        for seed in ("0", "1", "999")}
    assert across_processes == {backfill.digest(wide)}, (
        "the digest is NOT stable across processes (PYTHONHASHSEED changes it): a plan "
        "printed by one run could never be executed by another. Got "
        f"{sorted(across_processes)}, expected {backfill.digest(wide)!r}")


def test_a_second_execute_is_a_no_op(stores):
    """"Idempotent: a second run is a no-op" — and it reports that honestly."""
    graph, door = stores
    graph.add("Point", "pt-1", _vec(0.1))
    graph.add("Point", "pt-2", _vec(0.2))
    door.seed("point", "pt-1")
    door.seed("point", "pt-2")

    plan = _plan(graph, door, labels=["Point"])
    first = _execute(graph, door, plan)
    assert first["filled"] == 2
    calls_after_first = len(door.calls)

    second = _execute(graph, door, plan)
    assert second["filled"] == 0
    assert second["planned_fillable"] == 0
    assert second["skipped_existing_by_label"]["Point"] == 2
    assert second["residual_graph_only"] == 0
    assert len(door.calls) == calls_after_first, (
        "the second run issued a write — a no-op re-run must issue none")
    # The vectors are the first run's, untouched.
    assert door.tables["point"]["pt-1"]["embedding"] == _stored(_vec(0.1))


def test_execute_refuses_a_population_that_moved_since_the_plan(stores):
    """The digest authorises an exact POPULATION: one node more refuses."""
    graph, door = stores
    graph.add("Object", "obj-1")
    door.seed("object", "obj-1")
    plan = _plan(graph, door, labels=["Object"])

    graph.add("Object", "obj-2")
    door.seed("object", "obj-2")

    with pytest.raises(backfill.Refusal) as refused:
        _execute(graph, door, plan)
    assert refused.value.token == backfill.REFUSE_PLAN_STALE
    assert door.calls == [], "a refused run wrote anyway"


def test_execute_refuses_a_graph_side_census_that_moved(stores):
    """The re-measurement is not redundant with the digest: an UNKEYED node moves it.

    A vector-bearing node with no identity key is in neither side of the copyable
    intersection, so the digest cannot see it — only the graph-side number can.
    """
    graph, door = stores
    graph.add("Event", "evt-1")
    door.seed("event", "evt-1")
    plan = _plan(graph, door, labels=["Event"])

    graph.add("Event", None)  # vector-bearing, no identity: never matchable
    with pytest.raises(backfill.Refusal) as refused:
        _execute(graph, door, plan)
    assert refused.value.token == backfill.REFUSE_PLAN_STALE
    assert "unkeyed_graph_nodes" in str(refused.value)
    assert door.calls == []


# ── the two counters that make a PARTIAL run legible ──────────────────────


def test_a_shortfall_is_counted_not_absorbed(stores):
    """A row filled by a RACING WRITER between the read and the statement is REPORTED.

    The statement's `AND t.embedding IS NULL` refuses the row (correctly — it is never
    overwritten), so `rowcount` comes back SHORT. That shortfall is the only signal that
    this run is partial: absorbed, a half-done copy would report a complete one. No
    fixture drove this counter before, so the `shortfall` line was untested.
    """
    graph, door = stores
    graph.add("Object", "obj-1", _vec(0.1))
    graph.add("Object", "obj-2", _vec(0.2))
    door.seed("object", "obj-1")
    door.seed("object", "obj-2")

    class _RacedDoor(FakeDoor):
        """One row was filled by someone else between our read and our write."""

        def write(self, table, pairs):
            applied = super().write(table, pairs)
            return max(applied - 1, 0)

    raced = _RacedDoor()
    raced.tables = door.tables
    raced.org_id, raced.graph_id = "org", "graph"

    plan = _plan(graph, door, labels=["Object"])
    result = _execute(graph, raced, plan)

    assert result["planned_fillable"] == 2
    assert result["filled"] == 1
    assert result["shortfall_by_label"]["Object"] == 1, (
        "a shortfall was absorbed: the run reports itself complete over a row it did "
        "not fill")


def test_a_vector_that_vanishes_between_the_census_and_the_read_is_counted(stores):
    """A vector that went away between the re-census and the batch read is REPORTED.

    Not an error (nothing this tool owns was lost) and not a silent skip either: the
    operator must be able to tell "copied" from "was there a moment ago". No fixture
    drove `vanished` before, so that counter was untested too.
    """
    graph, door = stores
    graph.add("Object", "obj-1", _vec(0.1))
    graph.add("Object", "obj-2", _vec(0.2))
    door.seed("object", "obj-1")
    door.seed("object", "obj-2")

    class _VanishingGraph(FakeGraph):
        """The batch read's answer omits one key — that vector went away."""

        def _answer(self, cypher, params):
            rows = super()._answer(cypher, params)
            if "IN $keys" in cypher and rows:
                return rows[:-1]
            return rows

    vanishing = _VanishingGraph()
    vanishing.nodes = graph.nodes
    vanishing._next = graph._next

    plan = _plan(graph, door, labels=["Object"])
    result = _execute(vanishing, door, plan)

    assert result["filled"] == 1
    assert result["vanished_by_label"]["Object"] == 1, (
        "a vector that disappeared between the census and the batch read was silently "
        "skipped — the counter is how the operator learns the corpus moved under them")


def test_execute_refuses_target_drift(stores):
    """A plan is an authority over ONE graph."""
    graph, door = stores
    graph.add("Object", "obj-1")
    door.seed("object", "obj-1")
    plan = _plan(graph, door, labels=["Object"], graph_name="g-one")

    with pytest.raises(backfill.Refusal) as refused:
        _execute(graph, door, plan, graph_name="g-two")
    assert refused.value.token == backfill.REFUSE_TARGET_DRIFT
    assert door.calls == []


def test_execute_refuses_a_plan_built_for_another_record_tenant(stores):
    """A plan is an authority over ONE target — and the DIGEST cannot see the tenant.

    ⛔ THE NON-VACUOUS CASE (review F1). `digest()` is over `label<TAB>key` lines only, so
    it encodes the POPULATION and never the destination: two doors holding the SAME keys
    produce the SAME digest. That is why this pin seeds an identical population in both
    and only moves the tenant — a different-population case would red
    `REFUSE_PLAN_STALE` instead and would prove nothing about this guard.

    Pre-fix this call SUCCEEDED and wrote into the second tenant's rows, because
    `plan["org_id"]`/`plan["graph_id"]` were recorded, printed as the approved
    artefact's own `record tenant:` line, and read back by nothing.

    BOTH halves of the tenant are falsified (`graph_id` and `org_id` separately), because
    a guard that compared only one of them would pass a one-sided pin — and the same plan
    is then executed against the tenant it WAS built for, so the guard is a comparison
    rather than a blanket refusal.
    """
    graph, door = stores
    graph.add("Object", "obj-1")
    door.seed("object", "obj-1")
    plan = _plan(graph, door, labels=["Object"])

    for org_id, graph_id in (("org", "other-graph"), ("other-org", "graph")):
        other = FakeDoor()
        other.org_id, other.graph_id = org_id, graph_id
        for table, _label, _key in backfill.BACKFILLED:
            other.tables.setdefault(table, {})
        other.tables["object"]["obj-1"] = dict(door.tables["object"]["obj-1"])

        assert _plan(graph, other, labels=["Object"])["population_digest"] == (
            plan["population_digest"]), (
            "the fixture is meant to hold the SAME population in both tenants, so the "
            "POPULATION digest cannot distinguish them — if this moved, the pin is "
            "case-blind")
        # ⛔ …while the TOKEN the operator approves DOES distinguish them, because it
        # binds the plan's content (which names the tenant). That is a second,
        # independent reason the write cannot land in the other tenant's rows.
        assert _plan(graph, other, labels=["Object"])["digest"] != plan["digest"], (
            "the approval token is blind to the record tenant — the content half is "
            "meant to cover org_id/graph_id")
        with pytest.raises(backfill.Refusal) as refused:
            _execute(graph, other, plan)
        assert refused.value.token == backfill.REFUSE_TARGET_DRIFT
        assert graph_id in str(refused.value) or org_id in str(refused.value), (
            "the refusal must name the tenant this run resolves, so an operator can see "
            "which one they are about to write to")
        assert other.calls == [], "a plan approved for one tenant wrote into another"

    # …and against the tenant it WAS built for the same plan executes.
    assert _execute(graph, door, plan)["filled"] == 1
    assert other.calls == [], "a plan approved for one tenant wrote into another"


def test_execute_refuses_a_plan_for_a_different_graph_uri(stores):
    """`graph_uri` is recorded for provenance — as a HASH plus a REDACTED copy.

    ⛔ THE CREDENTIAL NEVER REACHES THE PLAN (review finding 6). The artefact is written
    to `--plan-out`, handed to a human for approval and kept until execution, and a
    FalkorDB URI's userinfo carries the password. The drift guard is exactly as strong:
    it compares digests of the address, not the address.
    """
    graph, door = stores
    graph.add("Object", "obj-1")
    door.seed("object", "obj-1")
    plan = _plan(graph, door, labels=["Object"],
                 graph_uri="docker://:hunter2@db.example:6379/g")
    assert "graph_uri" not in plan, (
        "the plan still carries the RAW address — a credential reaches the artefact")
    assert plan["graph_uri_redacted"] == "docker://:***@db.example:6379/g"
    assert "hunter2" not in json.dumps(plan), (
        "a password reached the plan artefact or the --json report")
    assert plan["graph_uri_sha256"].startswith("sha256:")

    with pytest.raises(backfill.Refusal) as refused:
        _execute(graph, door, plan, graph_uri="docker://a-replica")
    assert refused.value.token == backfill.REFUSE_TARGET_DRIFT
    assert "a-replica" in str(refused.value)
    assert door.calls == []

    # …and the plan executes against the address it WAS built for, credential intact.
    assert _execute(graph, door, plan,
                    graph_uri="docker://:hunter2@db.example:6379/g")["filled"] == 1


# ── the plan is an ARTEFACT, not a field ────────────────────────────────────


def test_a_retargeted_record_table_is_refused_and_cannot_redirect_the_write(stores):
    """⛔ THE TABLE IS DERIVED FROM THE LABEL (review finding 1a, reproduced).

    Pre-fix `execute` took `table` from `plan["labels"][i]["record_table"]`, so a
    one-word edit on an otherwise untouched plan made this tool write an **Object's**
    vector onto a **Point**'s record row — and the null-only guard does not help, because
    the WRONG row's `embedding` is NULL too, which is exactly what the guard looks for.

    The mutant is the line the reviewer demonstrated: set the plan's recorded table to
    `point` and hand the same plan to `execute`.
    """
    graph, door = stores
    graph.add("Object", "obj-1", _vec(0.25))
    door.seed("object", "obj-1")
    plan = _plan(graph, door, labels=["Object"])
    assert plan["labels"][0]["record_table"] == "object"

    plan["labels"][0]["record_table"] = "point"
    with pytest.raises(backfill.Refusal) as refused:
        _execute(graph, door, plan)
    assert refused.value.token == backfill.REFUSE_PLAN_TAMPERED
    assert door.calls == [], (
        "the retargeted plan issued a write — an Object's vector could reach a Point row")
    assert door.tables.get("point", {}) == {}, "a Point row was created or written"


def test_a_plan_whose_content_was_edited_is_refused_at_load(tmp_path, stores):
    """⛔ THE APPROVED TOKEN COVERS THE CONTENT, IN TWO HALVES (finding 1b).

    The v1 token was one string field compared to one string field, so the demonstrated
    tamper — blanking the plan's `census` rows, which voids the graph-side drift guard
    (`_assert_census_matches` compares against exactly those rows) — left `digest`
    untouched and the plan EXECUTED.

    Both attacks are pinned: a bare edit is caught by `content_sha256`; an editor who
    re-seals that hash is still caught, because the population half of the token is
    derived from keys the plan does not carry.
    """
    graph, door = stores
    graph.add("Object", "obj-1")
    door.seed("object", "obj-1")
    plan = _plan(graph, door, labels=["Object"])
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan))

    # the honest artefact loads, so the pins below are not a blanket refusal
    assert backfill._load_plan(str(path), plan["digest"])["digest"] == plan["digest"]

    # (a) blank the census — the graph-side drift guard's only input
    tampered = json.loads(json.dumps(plan))
    tampered["census"] = []
    path.write_text(json.dumps(tampered))
    with pytest.raises(backfill.Refusal) as caught:
        backfill._load_plan(str(path), plan["digest"])
    assert caught.value.token == backfill.REFUSE_PLAN_TAMPERED
    assert "content" in str(caught.value)

    # (b) …and re-seal the content hash the way an editor would
    resealed = json.loads(json.dumps(tampered))
    resealed["content_sha256"] = backfill.content_sha256(resealed)
    path.write_text(json.dumps(resealed))
    with pytest.raises(backfill.Refusal) as caught_again:
        backfill._load_plan(str(path), plan["digest"])
    assert caught_again.value.token == backfill.REFUSE_PLAN_TAMPERED, (
        "a re-sealed content hash was accepted — the token does not bind the content")
    assert door.calls == []


def test_an_unopenable_record_is_a_census_error_not_a_traceback(tmp_path, monkeypatch,
                                                              stores):
    """⛔ `RecordUnavailable` IS THE READ'S FAILURE, NOT AN ESCAPE (finding 3).

    `conn = self._connection()` sat OUTSIDE the `try` that turns a read failure into a
    `CensusError`, and `RecordUnavailable` is not a `CensusError`, so a wrong password / a
    down database / a missing `psycopg2` escaped `main()` as a traceback with **exit 1** —
    the code this module's own exit table reserves for USAGE errors — and **no `--json`
    report**, on the likeliest operator mistake there is.
    """
    graph, _door = stores
    graph.add("Object", "obj-1")
    import tortoise.projection as projection
    import tortoise.record_serve as record_serve

    assert not issubclass(record_serve.RecordUnavailable, backfill.CensusError), (
        "vacuous pin: RecordUnavailable already IS a CensusError")

    monkeypatch.setattr(
        projection, "FalkorProjection",
        SimpleNamespace(from_uri=lambda *_a, **_k: SimpleNamespace(g=graph)))
    monkeypatch.setattr(backfill.census_mod, "_refuse_unless_graph_present",
                        lambda *_a, **_k: None)
    monkeypatch.setattr(backfill.census_mod, "resolve_graph_name",
                        lambda *_a, **_k: "g")

    def _unreachable(_dsn):
        raise record_serve.RecordUnavailable(
            "the record could not be opened (OperationalError) — an absence is NOT "
            "established")

    monkeypatch.setattr(record_serve, "open_record", _unreachable)

    base = ["--uri", "docker://:pw@127.0.0.1:1/g", "--graph-name", "g"]
    tenant = ["--record-dsn", "postgresql://x/y", "--org-id", "org",
              "--graph-id", "graph"]
    path = tmp_path / "unopenable.json"
    # exit 3, not 1: a side that could not be read, not a usage error.
    assert backfill.main([*base, *tenant, "--json", str(path)]) == 3
    report = json.loads(path.read_text())
    assert report["outcome"] == "failed"
    assert report["error"] == "CENSUS_ERROR"
    assert "an absence is NOT established" in report["message"]
    assert report["wrote"] is False


def test_execute_re_verifies_the_token_on_a_direct_call(stores):
    """⛔ `execute` IS NOT A CLOSED SET OF ONE CALLER (review cycle 2, finding 1).

    `main()` hands `execute` a plan `_require_tokens` already loaded, so the load-time
    checks were enough FOR THAT CALLER — and this function's docstring promises
    "every precondition", not "preconditions if you came through `main`". Before v2 it
    compared the live population to `confirmed` and could not write on a bad token;
    after v2 (whose token covers the content, so it cannot be compared to a
    population) that comparison had to move, and moving it must not leave this entry
    point able to write a plan nobody sealed — and to mint a report naming the
    unverified digest as the authority.
    """
    graph, door = stores
    graph.add("Object", "obj-1", _vec(0.25))
    door.seed("object", "obj-1")
    plan = _plan(graph, door, labels=["Object"])

    # (a) a token that is not the plan's
    with pytest.raises(backfill.Refusal) as bogus:
        _execute(graph, door, plan, confirmed="sha256:not-a-token-at-all")
    assert bogus.value.token == backfill.REFUSE_DIGEST_MISMATCH
    assert door.calls == [], "a bogus token wrote"

    # (b) a plan whose CONTENT was edited, with `content_sha256` left as it was
    unsealed = json.loads(json.dumps(plan))
    unsealed["total_copyable"] = 999999
    with pytest.raises(backfill.Refusal) as tampered:
        _execute(graph, door, unsealed)
    assert tampered.value.token == backfill.REFUSE_PLAN_TAMPERED
    assert door.calls == [], "an unsealed plan wrote"

    # (c) …and the honest plan still executes, so the pins above are not a blanket refusal
    assert _execute(graph, door, plan)["filled"] == 1


def test_the_batch_knob_is_provenance_not_part_of_the_approval(tmp_path, stores):
    """⛔ A SEAL MAY ONLY COVER WHAT SCOPES THE WRITE (review cycle 2, finding 1).

    `batch` sizes one `UPDATE`; `execute` takes it from the COMMAND LINE, so the value
    the plan records is inert (`keys = sorted(fillable)` is partitioned, never sampled).
    While it sat in `_PLAN_CONTENT_FIELDS` an edit to that inert value reported
    `REFUSE_PLAN_TAMPERED` — "the file was edited after it was printed" — for a plan
    whose copy is byte-identical, and the refusal would have been raised at the operator
    instead of the harmless datum. The seal now covers only the target and the
    population, which is the property this pin reds on: re-add `batch` to
    `_PLAN_CONTENT_FIELDS` and both the hash equality and the load below go red.
    """
    graph, door = stores
    graph.add("Object", "obj-1", _vec(0.25))
    door.seed("object", "obj-1")
    plan = _plan(graph, door, labels=["Object"], batch=1)

    edited = json.loads(json.dumps(plan))
    edited["batch"] = 5000
    assert backfill.content_sha256(edited) == plan["content_sha256"], (
        "the batch is sealed — an inert pace knob now reports a tamper that did not "
        "change the migration")
    path = tmp_path / "batch-edited.json"
    path.write_text(json.dumps(edited))
    loaded = backfill._load_plan(str(path), edited["digest"])
    assert _execute(graph, door, loaded, batch=7)["filled"] == 1


def test_a_v2_plan_is_REFUSED_AS_UNREADABLE_not_as_tampered(tmp_path, stores):
    """⛔ A SEAL-COMPOSITION CHANGE MOVES THE VERSION, NOT THE DIAGNOSIS (cycle 2).

    v3 dropped `batch` from the seal (see the test above), so a v2 plan's
    `content_sha256` was taken over a DIFFERENT field set. If PLAN_VERSION had stayed 2
    that file would pass the version gate and fail the CONTENT gate, and the operator
    would be told `REFUSE_PLAN_TAMPERED` — "the file was edited after it was printed" —
    about a file nobody edited. "Re-plan" and "someone tampered with your artefact" send
    an operator to two different places, so the version must be what refuses it.
    """
    graph, door = stores
    graph.add("Object", "obj-1", _vec(0.25))
    door.seed("object", "obj-1")
    plan = _plan(graph, door, labels=["Object"])

    stale = json.loads(json.dumps(plan))
    stale["version"] = 2
    path = tmp_path / "v2.json"
    path.write_text(json.dumps(stale))
    with pytest.raises(backfill.Refusal) as refused:
        backfill._load_plan(str(path), stale["digest"])
    assert refused.value.token == backfill.REFUSE_PLAN_UNREADABLE, (
        "a previous-schema plan must refuse as UNREADABLE (re-plan), never as TAMPERED")
    assert "v3 plan" in str(refused.value)


def test_a_MISTYPED_labels_or_census_list_is_refused_rather_than_crashing(tmp_path, stores,
                                                                       monkeypatch, capsys):
    """⛔ `plan.get(field) or []` IS NOT A GUARD (review cycle 3, finding 1).

    `5 or []` evaluates to **5**, so a plan whose only defect is `"labels": 5` raised
    `TypeError: 'int' object is not iterable` INSIDE `_missing_plan_fields` — escaping
    `main()` (whose arms are `Refusal`/`CensusError`/`WriteFailed`) as a traceback, exit
    1 and no `--json`. The type problem is recorded by the `_PLAN_SHAPE` loop; the
    iteration must not then crash on the value it just complained about.
    """
    graph, door = stores
    graph.add("Object", "obj-1")
    door.seed("object", "obj-1")
    plan = _plan(graph, door, labels=["Object"])

    base = ["--uri", "docker://:pw@127.0.0.1:1/g", "--graph-name", "g",
            "--record-dsn", "postgresql://x/y", "--org-id", "org", "--graph-id", "graph",
            "--execute"]
    for field in ("labels", "census"):
        broken = json.loads(json.dumps(plan))
        broken[field] = 5
        broken["content_sha256"] = backfill.content_sha256(broken)
        broken["digest"] = backfill.approval_digest(broken["population_digest"],
                                                    broken["content_sha256"])
        path = tmp_path / f"bad-{field}.json"
        path.write_text(json.dumps(broken))
        with pytest.raises(backfill.Refusal) as refused:
            backfill._load_plan(str(path), broken["digest"])
        assert refused.value.token == backfill.REFUSE_PLAN_UNREADABLE
        assert f"{field} is int" in str(refused.value), (
            f"the refusal does not name the mistyped field: {refused.value}")

    # …and end-to-end: exit 2 with a report, not a traceback and exit 1.
    broken = json.loads(json.dumps(plan))
    broken["labels"] = 5
    broken["content_sha256"] = backfill.content_sha256(broken)
    broken["digest"] = backfill.approval_digest(broken["population_digest"],
                                                broken["content_sha256"])
    path = tmp_path / "bad-labels.json"
    path.write_text(json.dumps(broken))
    report = tmp_path / "bad-labels-report.json"
    assert backfill.main([*base, "--plan", str(path),
                          "--confirm-plan-digest", broken["digest"],
                          "--json", str(report)]) == 2
    assert backfill.REFUSE_PLAN_UNREADABLE in capsys.readouterr().err
    assert json.loads(report.read_text())["refusal"] == backfill.REFUSE_PLAN_UNREADABLE


def test_an_at_sign_in_the_PATH_does_not_move_the_authority():
    """⛔ THE USERINFO SPLIT IS SEGMENT-SCOPED (review cycle 3, finding 4).

    `rsplit("@", 1)` over everything after `://` also splits on an `@` in the graph path,
    so `docker://h:6379/a@b` and `docker://other:1/z@b` both reduced to `docker://b` and
    hashed EQUAL — two different addresses passing the drift guard, a fail-open in the
    binding `_uri_authority` exists to keep. `_redact_uri` invented a userinfo out of the
    path and lost the host.
    """
    assert backfill._uri_authority("docker://h:6379/a@b") == "docker://h:6379/a@b"
    assert backfill._uri_authority("docker://:pw@h:6379/a@b") == "docker://h:6379/a@b"
    assert backfill._uri_authority("docker://h:6379/a@b") != backfill._uri_authority(
        "docker://other:1/z@b"), "two addresses still hash equal — the binding is open"
    assert backfill._redact_uri("docker://h:6379/a@b") == "docker://h:6379/a@b", (
        "a path `@` was read as userinfo: host and graph lost, a fake credential invented")
    assert backfill._redact_uri("docker://:pw@h:6379/a@b") == "docker://:***@h:6379/a@b"
    # the rule the split exists for survives: a password containing a raw `@`
    assert backfill._redact_uri("docker://u:p@ss@h/a") == "docker://u:***@h/a"
    assert backfill._uri_authority("docker://u:p@ss@h/a") == "docker://h/a"


def test_a_census_row_with_a_NON_NUMERIC_count_is_refused_at_load(tmp_path, stores):
    """⛔ PRESENCE IS NOT SHAPE (review cycle 2, finding 2).

    The validator checked only that each `_CENSUS_FIELDS` key EXISTED, so
    `{"graph_vectors": None}` was accepted at load and then raised `TypeError` from
    `int(None)` inside `_assert_census_matches` — which escapes `main()` (its arms are
    `Refusal`/`CensusError`/`WriteFailed`) as a traceback, exit 1 and no `--json`, the
    exact class the validator exists to close.
    """
    graph, door = stores
    graph.add("Object", "obj-1")
    door.seed("object", "obj-1")
    plan = _plan(graph, door, labels=["Object"])

    broken = json.loads(json.dumps(plan))
    broken["census"][0]["graph_vectors"] = None
    broken["content_sha256"] = backfill.content_sha256(broken)
    broken["digest"] = backfill.approval_digest(broken["population_digest"],
                                                broken["content_sha256"])
    path = tmp_path / "census-null.json"
    path.write_text(json.dumps(broken))
    with pytest.raises(backfill.Refusal) as refused:
        backfill._load_plan(str(path), broken["digest"])
    assert refused.value.token == backfill.REFUSE_PLAN_UNREADABLE
    assert "graph_vectors is not an integer" in str(refused.value)

    # …and the reader is TOTAL even if such a row reaches it: a delta, never a raise.
    live = [{"label": "Object", "graph_vectors": 3, "unkeyed_graph_nodes": 0}]
    hostile = {"census": [{"label": "Object", "graph_vectors": None,
                           "unkeyed_graph_nodes": "lots"}]}
    with pytest.raises(backfill.Refusal) as stale:
        backfill._assert_census_matches(hostile, live)
    assert stale.value.token == backfill.REFUSE_PLAN_STALE
    assert "None" in str(stale.value) and "lots" in str(stale.value)


def test_a_rotated_password_is_the_same_target_and_the_digest_is_not_an_oracle(stores):
    """⛔ THE DRIFT GUARD IS ABOUT THE ADDRESS, NOT THE CREDENTIAL (cycle 2, finding 3).

    Hashing the WHOLE uri made `graph_uri_sha256` an offline dictionary over the
    password — and the password in this repo's documented URIs is a KNOWN default
    (`docker://:falkordb@host:6379`), so the digest is recoverable instantly. It also
    produced `REFUSE_TARGET_DRIFT` ("a replica or a stale address") for a ROTATED
    password, i.e. the wrong diagnosis for the same graph.
    """
    graph, door = stores
    graph.add("Object", "obj-1", _vec(0.25))
    door.seed("object", "obj-1")

    old = "docker://:firstpassword@db.example:6379/g"
    rotated = "docker://:rotatedpassword@db.example:6379/g"
    assert backfill._uri_authority(old) == backfill._uri_authority(rotated)
    plan = _plan(graph, door, labels=["Object"], graph_uri=old)

    import hashlib
    assert plan["graph_uri_sha256"] == "sha256:" + hashlib.sha256(
        b"docker://db.example:6379/g").hexdigest(), (
        "the digest still covers the credential — it is a password oracle")
    assert "firstpassword" not in json.dumps(plan)

    # the SAME address with a rotated credential is the same target, and executes
    assert _execute(graph, door, plan, graph_uri=rotated)["filled"] == 1
    # …while a different ADDRESS is still refused
    with pytest.raises(backfill.Refusal) as drifted:
        _execute(graph, door, plan, graph_uri="docker://db.example:6379/other")
    assert drifted.value.token == backfill.REFUSE_TARGET_DRIFT


def test_the_unreachable_head_pin_names_the_ref_that_re_arms_it():
    """⛔ A SKIP MUST NAME THE WAY BACK (review cycle 2, finding 4).

    The `f07b73b1de` quartet is only carried by `fix/13494-rederive-search-cites` (an
    UNMERGED sibling lane, no `refs/pull/*`), so the arm SKIPS on a fresh clone and in
    CI's `fetch-depth: 1` checkout — and a skip whose message names no ref leaves the
    four numbers with no reader anywhere, editable to anything. The skip message and
    the document clause both name the re-arm route; this pin is what keeps them naming
    it.
    """
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent
    test_src = (root / "tests" / "test_7949_capstone_measure.py").read_text(encoding="utf-8")
    doc = (root / "docs/epics/2026-11-04-7949-extractor-v4-capstone" / "05-verify.md"
           ).read_text(encoding="utf-8")
    assert "git fetch origin fix/13494-rederive-search-cites" in test_src, (
        "the skip message no longer names the ref that carries f07b73b1de — the four "
        "numbers it checks then have no reader on a fresh clone or in CI")
    assert "fix/13494-rederive-search-cites" in doc, (
        "05-verify.md's promote/dedup clause labels the quartet with a head no ref in "
        "the default refspec carries, and no longer names the sibling ref that does")


def test_a_non_finite_element_is_counted_and_never_wedges_the_batch(stores):
    """⛔ NON-FINITE IS `skipped_uncopyable`, NOT A POISONED BATCH (finding 4).

    `struct.pack("<f", nan)` succeeds, so a NaN element survived `_narrow` and the length
    check and was serialised as the bare token `NaN` — which is not JSON and which
    Postgres refuses. The batch then raised, `WriteFailed` exited 3, and because
    `keys = sorted(fillable)` is deterministic, re-executing reproduced the SAME failure
    at the SAME batch: the label was wedged until someone hand-edited the graph.
    """
    nan_vector = _vec(0.5)
    nan_vector[0] = float("nan")
    inf_vector = _vec(0.5)
    inf_vector[1] = float("inf")

    assert backfill._narrow(nan_vector) is None
    assert backfill._narrow(inf_vector) is None
    assert backfill._narrow(_vec(0.5)) is not None, (
        "the pin is vacuous: finite vectors are being rejected too")

    graph, door = stores
    graph.add("Object", "obj-nan", nan_vector)
    graph.add("Object", "obj-ok", _vec(0.25))
    door.seed("object", "obj-nan")
    door.seed("object", "obj-ok")
    plan = _plan(graph, door, labels=["Object"])

    result = _execute(graph, door, plan, batch=1)
    assert result["uncopyable_by_label"]["Object"] == 1
    assert result["filled_by_label"]["Object"] == 1, (
        "the unusable vector poisoned the rest of the label")
    written = [key for _table, keys in door.calls for key in keys]
    assert written == ["obj-ok"], (
        f"a non-finite payload was sent to the store: {written!r}")
    assert door.tables["object"]["obj-nan"]["embedding"] is None
    # …and the run is NOT a failure: the residual is reported, the batch is not wedged.
    assert result["residual_graph_only"] >= 1


def test_every_exit_path_writes_the_json_report(tmp_path, monkeypatch, stores):
    """⛔ `--json` ON EVERY EXIT PATH, `outcome` from a CLOSED set (review F2).

    Six returns exited non-zero with `--json` silently unwritten, including BOTH tenant
    failures — exactly the runs an operator debugging a botched `--org-id` needs a report
    for. Each now writes a payload that carries an `outcome`, so a caller can branch on
    that key instead of inferring the state from which keys happen to be present.

    ⛔ THE PRE-STORE CASES ARE NOT THE WHOLE SURFACE. The four below refuse before any
    store is opened and need no DB; the remaining arms (target resolution, the preview's
    own census, and the SUCCESS path, which used to carry no `outcome` at all) are driven
    with the projection/door seams patched, so a fix that stops at the four cheap arms
    cannot pass as "every exit path".
    """
    monkeypatch.delenv("TORTOISE_RECORD_DSN", raising=False)
    monkeypatch.delenv("TORTOISE_RECORD_ORG_ID", raising=False)
    monkeypatch.delenv("TORTOISE_RECORD_GRAPH_ID", raising=False)

    base = ["--uri", "docker://:pw@127.0.0.1:1/g", "--graph-name", "g"]
    tenant = ["--record-dsn", "postgresql://x/y", "--org-id", "org",
              "--graph-id", "graph"]
    cases = (
        ("empty-target-flag", [*base, "--uri", ""], 1, "usage", "EMPTY_GRAPH_TARGET"),
        ("batch-not-positive", [*base, *tenant, "--batch", "0"],
         1, "usage", "BATCH_INVALID"),
        ("batch-too-large", [*base, *tenant, "--batch", str(backfill.MAX_BATCH + 1)],
         1, "usage", "BATCH_TOO_LARGE"),
        ("no-record-leg", base, 3, "failed", "NO_RECORD_LEG"),
        ("tenant-unnamed", [*base, "--record-dsn", "postgresql://x/y"],
         3, "failed", "NO_RECORD_TENANT"),
    )
    for index, (name, argv, code, outcome, token) in enumerate(cases):
        path = tmp_path / f"report-{index}-{name}.json"
        assert backfill.main([*argv, "--json", str(path)]) == code, argv
        report = json.loads(path.read_text())
        assert report["outcome"] == outcome, (argv, report)
        assert report["error"] == token, (argv, report)
        assert report["wrote"] is False, (argv, report)
        assert report["outcome"] in backfill.REPORT_OUTCOMES

    # ── the arms that need the store seams patched ───────────────────────────
    graph, _door = stores
    graph.add("Object", "obj-1")
    import tortoise.projection as projection
    monkeypatch.setattr(
        projection, "FalkorProjection",
        SimpleNamespace(from_uri=lambda *_a, **_k: SimpleNamespace(g=graph)))
    monkeypatch.setattr(
        backfill.census_mod, "_refuse_unless_graph_present", lambda *_a, **_k: None)

    class _Door(FakeDoor):
        def __init__(self, _dsn, org_id, graph_id):
            super().__init__()
            self.org_id, self.graph_id = org_id, graph_id
            for table, _label, _key in backfill.BACKFILLED:
                self.tables.setdefault(table, {})
            self.tables["object"]["obj-1"] = {"embedding": None, "vec": None}

        def close(self):
            return None

    monkeypatch.setattr(backfill, "RecordDoor", _Door)
    real_build_plan = backfill.build_plan

    def _boom(exc):
        def _raise(*_a, **_k):
            raise exc
        return _raise

    # target resolution: a census failure, and any other exception
    for name, exc, token in (
            ("target-census-error", backfill.CensusError("the graph could not be opened"),
             "CENSUS_ERROR"),
            ("target-unresolved", ValueError("boom"), "TARGET_UNRESOLVED")):
        monkeypatch.setattr(backfill.census_mod, "resolve_graph_name", _boom(exc))
        path = tmp_path / f"{name}.json"
        assert backfill.main([*base, *tenant, "--json", str(path)]) == 3
        report = json.loads(path.read_text())
        assert report["outcome"] == "failed" and report["error"] == token, report
        assert report["outcome"] in backfill.REPORT_OUTCOMES

    monkeypatch.setattr(backfill.census_mod, "resolve_graph_name",
                        lambda *_a, **_k: "g")

    # the PREVIEW's own census refusing
    monkeypatch.setattr(backfill, "build_plan",
                        _boom(backfill.CensusError("the record leg could not be read")))
    path = tmp_path / "preview-census-refused.json"
    assert backfill.main([*base, *tenant, "--json", str(path)]) == 3
    report = json.loads(path.read_text())
    assert report["outcome"] == "failed" and report["error"] == "CENSUS_ERROR", report

    # the PREVIEW succeeding, then the SUCCESS path (which carried no `outcome` at all)
    monkeypatch.setattr(backfill, "build_plan", real_build_plan)
    plan_path = tmp_path / "plan.json"
    path = tmp_path / "preview.json"
    assert backfill.main([*base, *tenant, "--plan-out", str(plan_path),
                          "--json", str(path)]) == 0
    preview = json.loads(path.read_text())
    assert preview["outcome"] == "preview", preview
    # ⛔ The approved PLAN artefact stays byte-clean: it is what `--execute` reads, so
    # the report's `outcome` must not have leaked into it.
    approved = json.loads(plan_path.read_text())
    assert "outcome" not in approved, (
        "the approved plan artefact was polluted with the report's `outcome` key")

    path = tmp_path / "success.json"
    assert backfill.main([
        *base, *tenant, "--execute", "--plan", str(plan_path),
        "--confirm-plan-digest", approved["digest"], "--json", str(path)]) == 0
    success = json.loads(path.read_text())
    assert success["outcome"] == "complete", success
    assert success["filled_by_label"]["Object"] == 1


def test_argparse_failures_are_the_only_unreported_exits(tmp_path):
    """⛔ THE `--json` CLAIM HAS ONE BOUNDARY, and it is argparse's own parse failures.

    Review F2 found the module docstring asserting "`--json` is written on EVERY exit
    path" while six `main()` returns wrote nothing. The fix routed all six (pinned by
    `test_every_exit_path_writes_the_json_report`), but the claim as written stayed
    FALSE for a different class: `parse_args` raises `SystemExit(2)` on an unknown flag
    or a bad `--label` choice BEFORE `main()` holds an `args` object, so `args.json`
    does not exist to write to. Nothing can be reported there, and a caller must not be
    told otherwise.

    So the claim now NAMES its exception, and this pin holds both halves: argparse's
    failures exit 2 with no report file (measured, not assumed), and the docstring still
    says so. If a later edit routes argparse through a report — or quietly restores the
    unqualified "EVERY exit path" wording — one of these assertions reds, and the next
    reader is not told a contract holds on a path where it cannot.
    """
    unreported = [
        ["--json", str(tmp_path / "unknown-flag.json"), "--no-such-flag"],
        ["--uri", "docker://:pw@127.0.0.1:1/g", "--label", "Nonsense",
         "--json", str(tmp_path / "bad-choice.json")],
        ["--json", str(tmp_path / "missing-required.json")],  # no --uri / --db-path
    ]
    for argv in unreported:
        with pytest.raises(SystemExit) as exited:
            backfill.main(argv)
        # argparse's own usage code, never one of this tool's exit-table codes.
        assert exited.value.code == 2, argv
        report = Path(argv[argv.index("--json") + 1])
        assert not report.exists(), (
            f"argparse failed but a report appeared at {report} — the boundary moved, "
            "so the docstring's exception is now a claim the code does not honour")

    # …and the CLAIM carries that exception, so it cannot drift back to "every exit".
    doc = (Path(backfill.__file__).read_text().split('"""'))[1]
    assert "EVERY exit path" not in doc, (
        "the module docstring is back to the unqualified claim review F2 falsified — "
        "argparse's own parse failures remain unreportable")
    assert "ARGPARSE'S OWN FAILURES" in doc, (
        "the `--json` claim no longer names argparse's parse failures as its one "
        "exception, so `test_argparse_failures_are_the_only_unreported_exits` pins a "
        "boundary the docstring does not admit")


def test_the_report_outcome_set_is_closed():
    """The `--json` `outcome` vocabulary is a CLOSED, published set.

    Same rule as :data:`backfill.REFUSALS`: a caller branches on the value, so a later
    edit must not invent a spelling silently. `empty` / `residual` / `unverified` are
    members even though driving them here would need a store — they are what tells a
    caller that ``complete`` is NOT the only non-refusal answer.
    """
    assert backfill.REPORT_OUTCOMES == (
        "preview", "complete", "empty", "residual", "unverified", "refused",
        "failed", "write-failed", "usage")
    assert len(set(backfill.REPORT_OUTCOMES)) == len(backfill.REPORT_OUTCOMES)
    # …and every outcome every exit path in this file emits is a member of it.
    source = Path(backfill.__file__).read_text()
    emitted = set(re.findall(r'"outcome":\s*"([a-z-]+)"', source))
    assert emitted, "no literal `outcome` was found in the tool — the pin is blind"
    assert emitted <= set(backfill.REPORT_OUTCOMES), (
        f"the tool emits outcome(s) outside the closed set: "
        f"{sorted(emitted - set(backfill.REPORT_OUTCOMES))}")


def test_an_empty_population_refuses_unless_expect_empty(stores):
    """"Nothing to fill" is a REFUSAL, never a finished backfill."""
    graph, door = stores
    graph.add("Object", "obj-orphan")  # graph vector, no record row ⇒ nothing copyable

    plan = _plan(graph, door, labels=["Object"])
    assert plan["total_copyable"] == 0
    assert plan["ready"] is False
    with pytest.raises(backfill.Refusal) as refused:
        _execute(graph, door, plan)
    assert refused.value.token == backfill.REFUSE_NOTHING_TO_FILL

    result = _execute(graph, door, plan, expect_empty=True)
    assert result["empty"] is True
    assert door.calls == []


def test_an_uncopyable_vector_is_counted_not_written(stores):
    """No fabricated vector: a payload the converter would NULL is COUNTED, not stored.

    `public.record_embedding_vector(jsonb)` returns NULL for anything that is not a
    numeric 384-long array, so writing one would change nothing a dense query can read —
    and a copy that invented a width would be worse still.
    """
    graph, door = stores
    graph.add("Point", "pt-short", [0.1, 0.2, 0.3])
    graph.add("Point", "pt-good", _vec(0.4))
    door.seed("point", "pt-short")
    door.seed("point", "pt-good")

    plan = _plan(graph, door, labels=["Point"])
    result = _execute(graph, door, plan)

    assert result["filled"] == 1
    assert result["uncopyable_by_label"]["Point"] == 1
    assert door.tables["point"]["pt-short"]["embedding"] is None
    assert [keys for _t, keys in door.calls] == [["pt-good"]]


def test_a_failed_batch_write_exits_3_with_the_durable_report(
        stores, monkeypatch, tmp_path, capsys):
    """⛔ A batch write that FAILS part-way: exit 3, and the DURABLE per-label report.

    `WriteFailed` is deliberately NOT a `Refusal` (some batches COMMITTED) and NOT a
    `CensusError`, so before this arm existed it escaped `main()` as a traceback and exit
    `1` — which the module docstring defines as USAGE error — while the `partial` report
    the class exists to carry was never printed and never written to `--json`. The
    documented exit `3` was unreachable on this path, and no test drove a write failure
    at all.
    """
    graph, door = stores
    for key, seed in (("obj-1", 0.1), ("obj-2", 0.2), ("obj-3", 0.3)):
        graph.add("Object", key, _vec(seed))
        door.seed("object", key)

    # ⛔ The plan must name the SAME graph URI this run resolves: `execute()` refuses a
    # plan whose recorded `graph_uri` is another address (a replica, a stale target), and
    # that guard is one of the things a `--execute` must not be able to skip.
    uri = "docker://:pw@127.0.0.1:1/g"
    plan = _plan(graph, door, labels=["Object"], batch=1, graph_uri=uri)
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan))
    report_path = tmp_path / "report.json"

    class _FailingDoor(FakeDoor):
        """The FIRST batch commits; the second raises what `RecordDoor.write` raises."""

        def __init__(self, *_a, **_k):
            super().__init__()
            self.tables = door.tables
            self.org_id, self.graph_id = "org", "graph"

        def close(self):
            return None

        def write(self, table, pairs):
            if self.calls:
                raise backfill.CensusError(
                    "the `public.object` write FAILED (FakeDeadlock: 40P01) — the "
                    "transaction was rolled back, so this batch is not durable")
            return super().write(table, pairs)

    monkeypatch.setattr(backfill, "RecordDoor", _FailingDoor)
    monkeypatch.setattr(
        backfill.census_mod, "resolve_graph_name", lambda *_a, **_k: "g")
    monkeypatch.setattr(
        backfill.census_mod, "_refuse_unless_graph_present", lambda *_a, **_k: None)
    import tortoise.projection as projection
    monkeypatch.setattr(
        projection, "FalkorProjection",
        SimpleNamespace(from_uri=lambda *_a, **_k: SimpleNamespace(g=graph)))

    code = backfill.main([
        "--uri", uri, "--graph-name", "g",
        "--record-dsn", "postgresql://x/y", "--org-id", "org", "--graph-id", "graph",
        "--execute", "--plan", str(plan_path),
        "--confirm-plan-digest", plan["digest"], "--batch", "1",
        "--json", str(report_path)])

    assert code == 3, (
        "a failed batch write must exit 3 (the documented code for a write that failed "
        "part-way); 1 means USAGE error, and a traceback means the `except WriteFailed` "
        "arm is missing")
    err = capsys.readouterr().err
    assert "durable before the failure" in err, err
    report = json.loads(report_path.read_text())
    assert report["outcome"] == "write-failed"
    assert report["complete"] is False
    assert report["filled_by_label"]["Object"] == 1, (
        "the report lost the batches that ARE durable — that is the one datum the "
        "operator needs to resume the run")
    assert report["filled"] == 1
    # …and the row the second batch named is untouched, because that batch rolled back.
    assert door.tables["object"]["obj-2"]["embedding"] is None


# ── the census stays the instrument ────────────────────────────────────────


def test_the_per_label_numbers_are_the_census_numbers(stores, monkeypatch):
    """This tool's per-label table EQUALS `parity.census()`'s for the four labels it covers.

    The tool does not call `parity.census()` (see the module docstring: `:Point` is
    outside that population, and a second full census read would double a 46k-Point
    run's graph cost), so the equivalence is PINNED rather than assumed — the census's
    own record leg is monkeypatched to read the same fake door, and the two tables are
    compared field by field.
    """
    graph, door = stores
    graph.add("Object", "obj-1")
    graph.add("Object", "obj-orphan")
    graph.add("Subject", "sub-held", _vec(0.9))
    graph.add("Event", "evt-1")
    graph.add("Source", "https://example.test/a")
    graph.add("Event", None)  # unkeyed
    door.seed("object", "obj-1")
    door.seed("subject", "sub-held", vector=_vec(0.9))
    door.seed("event", "evt-1")
    door.seed("source", "https://example.test/a")
    door.seed("event", "evt-unreadable", vector=_vec(0.3), readable=False)

    monkeypatch.setattr(
        parity, "read_record_vector_keys",
        lambda _dsn, _org, _graph, table: {
            "keys": door.targets(table)["readable"],
            "unreadable": len(door.targets(table)["rows"]
                              - door.targets(table)["missing"]
                              - door.targets(table)["readable"]),
            "rows": len(door.targets(table)["rows"]),
        })

    census = parity.census(g=graph, record_dsn="postgresql://x", org_id="org",
                           graph_id="graph")
    ours = {row["label"]: row for row in (
        backfill._census_row(backfill._measure_label(graph, door, table, label))
        for table, label, _k in backfill.BACKFILLED if label != "Point")}

    for row in census["labels"]:
        mine = ours[row["label"]]
        for field in ("graph_vectors", "record_vectors", "graph_only",
                      "unkeyed_graph_nodes", "record_unreadable_vector", "record_only"):
            assert mine[field] == row[field], (
                f"{row['label']}.{field}: this tool says {mine[field]!r}, the census "
                f"says {row[field]!r} — the two instruments disagree about the number a "
                "human approves")
    # Non-vacuity: the labels really do carry graph-only / unreadable rows here.
    # `obj-1` and `obj-orphan` are BOTH graph-only (`graph_only` counts a graph node whose
    # record row answers no dense query — an unfilled row and an absent row alike).
    assert ours["Object"]["graph_only"] == 2
    assert ours["Event"]["graph_only"] == 2  # evt-1 + the unkeyed node
    assert ours["Event"]["record_unreadable_vector"] == 1
    assert ours["Subject"]["graph_only"] == 0


def test_buckets_has_exactly_one_definition():
    """⛔ ONE `_buckets` def, pinned by AST — because `ruff` cannot see this one.

    The inherited file carried `_buckets(graph, record)` and `_buckets(measured)`.
    Python's last-def-wins made the earlier body UNREACHABLE while its two-argument
    signature went on advertising a contract nothing called — the defect class `#1744`
    pinned for `ingest_haystack_v2` (two defs, the parallel copy unreachable).

    ⚠️ F811 IS NOT THE GATE HERE, and assuming it was is how this survived: ruff's F811
    honours `lint.dummy-variable-rgx`, whose default pattern matches `_`-prefixed names,
    so a duplicated `_buckets` is EXEMPT. Measured on the pinned ruff 0.16.4 against the
    pre-fix file: `ruff check --select F811 tools/entity_vector_backfill.py` →
    `All checks passed!`. The pin therefore has to be the AST, not lint.
    """
    tree = ast.parse(Path(backfill.__file__).read_text())
    defs = [node.name for node in tree.body if isinstance(node, ast.FunctionDef)]
    assert defs.count("_buckets") == 1, (
        f"expected exactly ONE def _buckets, found {defs.count('_buckets')} — the "
        "earlier body is unreachable (last-def-wins) while its signature still "
        "advertises a contract nothing calls")
    duplicates = sorted({name for name in defs if defs.count(name) > 1})
    assert duplicates == [], (
        f"duplicate top-level def(s) {duplicates}: a re-fork shadows the earlier body "
        "silently, and last-def-wins makes it unreachable rather than an error")


def test_the_tool_reads_the_census_module_the_suite_patches(stores, monkeypatch):
    """The module-IDENTITY trap, pinned by OBSERVATION: patch the alias, watch the tool.

    `entity_vector_backfill` imports `entity_vector_parity_census` as a TOP-LEVEL module
    (its own directory is on `sys.path`), a DIFFERENT object from
    `tools.entity_vector_parity_census`. A suite that patched the package-qualified one
    would patch a module the code under test never calls (`#13629` records the trap),
    and the pin that used to sit at import time was a tautology. This one can only pass
    if the tool really reads its graph vectors through `backfill.parity`.
    """
    graph, door = stores
    graph.add("Object", "obj-1")
    door.seed("object", "obj-1")

    seen: list[tuple[str, str]] = []
    real = parity.read_graph_vector_keys

    def _observed(g, label, key_prop):
        seen.append((label, key_prop))
        return real(g, label, key_prop)

    monkeypatch.setattr(parity, "read_graph_vector_keys", _observed)
    _plan(graph, door, labels=["Object"])
    assert seen == [("Object", "id")], (
        "the tool did not read the graph through `backfill.parity` — it is holding a "
        "second copy of the census module, so every test that patches this alias "
        f"patches nothing (observed {seen!r})")

    # …and the trap is REAL rather than hypothetical: patching the PACKAGE-QUALIFIED
    # module reaches nothing the tool calls, which is why every seam in this file has to
    # patch `parity` and not `parity_mod`.
    monkeypatch.setattr(
        parity_mod, "read_graph_vector_keys",
        lambda *_a, **_k: pytest.fail(
            "the tool read through `tools.entity_vector_parity_census` — the suite's "
            "alias is not the module the code under test calls"))
    _plan(graph, door, labels=["Object"])
    assert seen == [("Object", "id")] * 2, (
        "the tool stopped reading through the alias after the package-qualified module "
        f"was patched (observed {seen!r})")


def test_the_refusal_set_is_closed():
    """Every refusal is one of the published tokens, and an unpublished one is a bug."""
    assert len(set(backfill.REFUSALS)) == len(backfill.REFUSALS) == 8
    for token in backfill.REFUSALS:
        assert isinstance(token, str) and token.startswith("REFUSE_")
    with pytest.raises(ValueError):
        backfill.Refusal("REFUSE_MADE_UP", "x")


def test_a_tools_only_diff_selects_this_pin():
    """Registration is CLASSIFICATION — it does not make a diff RUN this file.

    `tools/` is a flat `NON_PYTHON_PREFIXES` entry, so a change confined to the tool
    would take the docs-only early return and run tier-1 smoke, where this file is not
    registered. The tool is therefore a `CORE_ALSO` entry, and this file is a
    `slow_files` member (its acceptance leg mints a container) — so the assertion is on
    BOTH halves: the surface is selected, and the file is in the leg that will run it.
    """
    from tools import ci_selection as cs

    manifest = cs.load_manifest()
    selected = cs.select(["tools/entity_vector_backfill.py"], "pull_request", manifest)
    name = "test_13685_entity_vector_backfill.py"
    assert "core" in selected["surfaces"], selected["surfaces"]
    assert name in selected["slow_selected"], (
        f"a tools/entity_vector_backfill.py-only diff does not run this file: "
        f"surfaces={selected['surfaces']} full={selected['full']} "
        f"slow_selected={selected['slow_selected']}")
    # And it is NOT in the fast pool: its container leg would tax every core run.
    assert name not in cs.fast_pool(manifest)
    assert name in manifest["durations"], (
        "a test file this PR ADDS must carry a `durations:` row (the #3400/#4348 entry "
        "rule)")


# ══════════════════════════════════════════════════════════════════════════
# the end-to-end leg — a scratch pgvector container and a real embedded graph
# ══════════════════════════════════════════════════════════════════════════

E2E_IMAGE = "pgvector/pgvector:pg16"
E2E_PASSWORD = "backfill-e2e"


def _docker_ok() -> bool:
    """Is a docker DAEMON reachable? Retried once: a busy daemon answers `docker info`
    slowly under fleet load, and a one-shot check would turn "cannot ask right now" into
    a SILENT SKIP of the acceptance leg rather than a retry.
    """
    for _attempt in range(2):
        try:
            if subprocess.run(["docker", "info"], capture_output=True,
                              timeout=90).returncode == 0:
                return True
        except Exception:
            pass
        time.sleep(2)
    return False


def _psycopg2():
    try:
        import psycopg2
    except ImportError:  # pragma: no cover - env-dependent
        pytest.skip("psycopg2 is not installed (the 'postgres' extra) — the record "
                    "cannot be reached, so the end-to-end leg cannot run")
    return psycopg2


@pytest.fixture(scope="module")
def scratch_record():
    """A throwaway `pgvector/pgvector:pg16` with the record migrations applied.

    ⛔ `pgvector/pgvector:pg16`, NOT `postgres:16`: `20261109000001` declares
    `public.vector(384)` and a plain image cannot create the extension. ⛔ And never a
    hosted database — this is a `docker run` on an ephemeral port, removed on teardown.
    """
    if not _docker_ok():  # pragma: no cover - env-dependent
        pytest.skip("docker is not available — the scratch pgvector leg cannot run")
    psycopg2 = _psycopg2()

    name = f"e2e-13685-{os.getpid()}-{uuid.uuid4().hex[:6]}"
    started = subprocess.run(
        ["docker", "run", "-d", "--rm", "--name", name,
         "-e", f"POSTGRES_PASSWORD={E2E_PASSWORD}", "-e", "POSTGRES_DB=record",
         "-p", "127.0.0.1::5432", E2E_IMAGE],
        capture_output=True, text=True)
    if started.returncode != 0:  # pragma: no cover - env-dependent
        pytest.skip(f"the scratch container could not start: {started.stderr[-400:]}")
    try:
        published = subprocess.run(["docker", "port", name, "5432/tcp"],
                                   capture_output=True, text=True).stdout.strip()
        port = published.rsplit(":", 1)[-1]
        dsn = (f"postgresql://postgres:{E2E_PASSWORD}@127.0.0.1:{port}/record")

        deadline = time.time() + 120
        conn = None
        while time.time() < deadline:
            try:
                conn = psycopg2.connect(dsn)
                break
            except Exception:
                time.sleep(0.5)
        if conn is None:  # pragma: no cover - env-dependent
            pytest.skip("the scratch Postgres never accepted a connection")

        from tests.test_9078_record_written_by_production_path import (
            _app_role_dsn,
            _ensure_record_schema,
        )

        schema_conn = psycopg2.connect(dsn)
        schema_conn.autocommit = True
        try:
            _ensure_record_schema(schema_conn)
        finally:
            schema_conn.close()
        conn.close()
        yield {"dsn": dsn, "app_dsn": _app_role_dsn(dsn), "psycopg2": psycopg2}
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)


def _seed_record(psycopg2, dsn: str, org: str, graph: str, rows) -> None:
    """Seed one tenant: ``rows`` is ``[(label, node_key)]``.

    The CONTRACT column is seeded FROM the node key on purpose: the schema's own
    `chk_*_node_key_is_contract_key` constraints make them the same value, and a fixture
    that seeded them apart would be seeding a row this schema cannot hold.
    """
    contract = {"Point": ("point", "id"), "Object": ("object", "id"),
                "Subject": ("subject", "id"), "Event": ("event", "event_id"),
                "Source": ("source", "url")}
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO public.organizations (id, name) VALUES (%s, %s) "
                        "ON CONFLICT (id) DO NOTHING", (org, org))
            cur.execute("INSERT INTO public.graphs (id, org_id, name) "
                        "VALUES (%s, %s, %s) ON CONFLICT (id) DO NOTHING",
                        (graph, org, graph))
            for label, key in rows:
                table, column = contract[label]
                cur.execute(
                    "INSERT INTO public.node_identity (org_id, graph_id, node_label, "
                    "node_key) VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
                    (org, graph, label, key))
                if label in ("Point", "Object", "Subject"):
                    name_col = "" if label == "Point" else ", name"
                    name_val = "" if label == "Point" else ", %s"
                    params = ((org, graph, key, key) if label == "Point"
                              else (org, graph, key, key, f"{label}-{key}"))
                    cur.execute(
                        f"INSERT INTO public.{table} (org_id, graph_id, node_key, "
                        f"{column}{name_col}) VALUES (%s, %s, %s, %s{name_val})",
                        params)
                else:
                    cur.execute(
                        f"INSERT INTO public.{table} (org_id, graph_id, node_key, "
                        f"{column}) VALUES (%s, %s, %s, %s)",
                        (org, graph, key, key))
    finally:
        conn.close()


@pytest.fixture()
def live_graph(tmp_path, monkeypatch):
    """A real (embedded FalkorDB) graph with five labels, one per mapped key."""
    from tortoise.projection import FalkorProjection

    # ⛔ No ambient record DSN may be inherited into an end-to-end leg.
    monkeypatch.delenv("TORTOISE_RECORD_DSN", raising=False)
    proj = FalkorProjection(str(tmp_path / "graph.db"), graph_name="g",
                            skip_health_check=True, ensure_indexes=False)
    proj.g.query(
        "CREATE (p:Point {id: 'pt-live', embedding: $p}), "
        "(o:Object {id: 'obj-live', embedding: $o}), "
        "(sub:Subject {id: 'sub-live', embedding: $s}), "
        "(e:Event {eventId: 'evt-live', embedding: $e}), "
        "(src:Source {url: 'https://example.test/live', embedding: $src}), "
        "(orphan:Object {id: 'obj-orphan', embedding: $o})",
        {"p": _vec(0.11), "o": _vec(0.22), "s": _vec(0.33),
         "e": _vec(0.44), "src": _vec(0.55)})
    yield SimpleNamespace(proj=proj, path=str(tmp_path / "graph.db"), g=proj.g)
    with __import__("contextlib").suppress(Exception):
        proj.close()


def _run_main(argv) -> int:
    return backfill.main(argv)


def test_the_scratch_postgres_round_trip_fills_and_the_census_reports_parity(
        scratch_record, live_graph, tmp_path):
    """END TO END: preview → execute → the CENSUS reports PARITY for the labels filled.

    The record is a real `pgvector/pgvector:pg16` with the repo's own migrations applied
    and the app role (not the superuser) doing the copy, so the generated
    `embedding_vec` column, the RLS tenant scope and the one batched `UPDATE` are all
    exercised as production would exercise them. `obj-orphan` has no record row: it must
    remain COUNTED as `still_unmatched`, and it is why the census's Object row is the one
    this leg asserts a residual on.
    """
    org, graph = f"org-13685-{uuid.uuid4().hex[:8]}", f"graph-13685-{uuid.uuid4().hex[:8]}"
    _seed_record(scratch_record["psycopg2"], scratch_record["dsn"], org, graph, [
        ("Point", "pt-live"),
        ("Object", "obj-live"),
        ("Subject", "sub-live"),
        ("Event", "evt-live"),
        ("Source", "https://example.test/live"),
    ])

    psycopg2 = scratch_record["psycopg2"]
    conn = psycopg2.connect(scratch_record["dsn"])
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE public.object SET embedding = %s::jsonb WHERE org_id = %s "
                "AND graph_id = %s AND node_key = 'obj-live'",
                (json.dumps(_vec(9.99)), org, graph))
    finally:
        conn.close()

    common = ["--db-path", live_graph.path, "--graph-name", "g",
              "--record-dsn", scratch_record["app_dsn"],
              "--org-id", org, "--graph-id", graph]
    plan_path = tmp_path / "plan.json"

    # ── 1. PREVIEW: reads both stores, writes nothing ─────────────────────
    assert _run_main([*common, "--plan-out", str(plan_path)]) == 0
    plan = json.loads(plan_path.read_text())
    assert _row(plan, "Point")["fillable"] == 1, plan
    assert _row(plan, "Object")["still_unmatched"] == 1, plan
    assert _row(plan, "Object")["skipped_existing"] == 1, plan

    conn = psycopg2.connect(scratch_record["dsn"])
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            # The PREVIEW must have written nothing: every row the plan could fill still
            # has no generated vector. (`object` carries the fixture's PRE-EXISTING
            # vector, so it is excluded from the count rather than expected to be 0.)
            for table in ("point", "subject", "event", "source"):
                cur.execute(
                    f"SELECT count(*) FROM public.{table} WHERE org_id = %s AND "
                    "graph_id = %s AND embedding_vec IS NOT NULL", (org, graph))
                assert cur.fetchone()[0] == 0, (
                    f"the PREVIEW wrote a generated vector on public.{table} — the "
                    "preview must write nothing")
            cur.execute("SELECT count(*) FROM public.object WHERE org_id = %s AND "
                        "graph_id = %s AND embedding_vec IS NOT NULL", (org, graph))
            assert cur.fetchone()[0] == 1, (
                "the fixture's pre-existing object vector was disturbed by the preview")
    finally:
        conn.close()

    # ── 2. EXECUTE the approved plan, by its digest ───────────────────────
    assert _run_main([*common, "--execute", "--plan", str(plan_path),
                      "--confirm-plan-digest", plan["digest"]]) == 4, (
        "the run filled everything copyable but obj-orphan has no record row, so the "
        "graph-only residual is non-zero and the exit code must say so (4)")

    conn = psycopg2.connect(scratch_record["dsn"])
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT node_key, embedding, embedding_vec IS NOT NULL "
                        "FROM public.point WHERE org_id = %s AND graph_id = %s",
                        (org, graph))
            rows = dict((key, (payload, readable)) for key, payload, readable
                        in cur.fetchall())
            assert rows["pt-live"][1], "the :Point row has no generated vector"
            # The pre-existing Object vector was NOT overwritten.
            cur.execute("SELECT embedding FROM public.object WHERE org_id = %s AND "
                        "graph_id = %s AND node_key = 'obj-live'", (org, graph))
            payload = cur.fetchone()[0]
            # ⛔ The PAYLOAD column is `jsonb`: it holds the exact JSON numbers written,
            # not the `float32` narrowing (`embedding_vec` is where the narrowing shows).
            # So the sentinel is compared un-narrowed — a `_stored()` comparison here
            # would red on a copy that correctly left the row alone.
            assert [float(v) for v in payload] == _vec(9.99), (
                "a record row that already held a vector was overwritten")
    finally:
        conn.close()

    # ── 3. VERIFY with the OTHER instrument: the census reports PARITY ────
    report = parity.census(g=live_graph.proj.g, record_dsn=scratch_record["app_dsn"],
                           org_id=org, graph_id=graph)
    by_label = {row["label"]: row for row in report["labels"]}
    for label in ("Subject", "Event", "Source"):
        assert by_label[label]["verdict"] == "PARITY", by_label[label]
    assert by_label["Object"]["graph_only"] == 1, (
        "the unmatched graph node must still be visible to the census — it is the "
        "residual this tool refuses to invent a row for")
    # The census's population deliberately excludes `:Point`; this tool's own post-run
    # verdict is what covers it, and the second e2e leg pins the Point acceptance.

    # ── 4. A SECOND execute is a no-op ────────────────────────────────────
    assert _run_main([*common, "--execute", "--plan", str(plan_path),
                      "--confirm-plan-digest", plan["digest"]]) == 4


def test_the_sampled_point_becomes_readable_by_the_dense_leg(
        scratch_record, live_graph, tmp_path):
    """⛔ `#13685`'s Point acceptance, measured on the record the dense leg reads.

    The sampled graph-only Point id is picked BEFORE the run; after the fill its row
    must (a) carry a generated `embedding_vec`, and (b) be returned FIRST by a cosine
    nearest-neighbour query whose probe is that same vector — which is exactly what the
    armed dense leg does (`record_vector.vector_search` orders by
    `embedding_vec <=> probe`). Before the fill the same query finds nothing at all,
    which is the regression the issue describes.
    """
    org, graph = f"org-13685-{uuid.uuid4().hex[:8]}", f"graph-13685-{uuid.uuid4().hex[:8]}"
    _seed_record(scratch_record["psycopg2"], scratch_record["dsn"], org, graph,
                 [("Point", "pt-live")])
    psycopg2 = scratch_record["psycopg2"]
    probe = _vec(0.11)
    probe_sql = "[" + ",".join(f"{value!r}" for value in probe) + "]"

    def _dense_hits():
        conn = psycopg2.connect(scratch_record["dsn"])
        conn.autocommit = True
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT node_key, 1 - (embedding_vec <=> %s::vector) AS sim "
                    "FROM public.point WHERE org_id = %s AND graph_id = %s "
                    "AND embedding_vec IS NOT NULL "
                    "ORDER BY embedding_vec <=> %s::vector LIMIT 1",
                    (probe_sql, org, graph, probe_sql))
                row = cur.fetchone()
                return None if row is None else (row[0], float(row[1]))
        finally:
            conn.close()

    assert _dense_hits() is None, (
        "the fixture's Point is already dense-visible — the falsifier would measure "
        "nothing")

    common = ["--db-path", live_graph.path, "--graph-name", "g",
              "--record-dsn", scratch_record["app_dsn"],
              "--org-id", org, "--graph-id", graph, "--label", "Point"]
    plan_path = tmp_path / "plan.json"
    assert _run_main([*common, "--plan-out", str(plan_path)]) == 0
    plan = json.loads(plan_path.read_text())
    assert plan["labels"][0]["sample"] == ["pt-live"]
    assert _run_main([*common, "--execute", "--plan", str(plan_path),
                      "--confirm-plan-digest", plan["digest"]]) == 0, (
        "the Point run has no residual — every graph Point here has a record row")

    hit = _dense_hits()
    assert hit is not None, "the sampled Point is still invisible to a dense query"
    key, similarity = hit
    assert key == "pt-live"
    assert similarity > 0.999999, (
        f"the nearest neighbour to the node's own vector scored {similarity} — the "
        "copied payload is not the graph's vector")
