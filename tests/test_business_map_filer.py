"""The #13880 map's FILE DOOR, exercised where its failure is visible.

`--dry-run` runs `validate()` and returns — it never enters `write_map()`, and
it never reads a graph back. So three legs the module advertises were reachable
by nothing before this file:

* `Mcp.call()` raising on `result.isError` — the false PASS #13880 is about (a
  handler failure arrives inside a successful JSON-RPC `result`, so reading
  `payload["error"]` alone reported every write as filed);
* `_resolve()` refusing a response with no id — a name is not an id, and an edge
  bound to one joins nodes that do not exist;
* `verify()`'s read-back verdict — which counted nodes ANYWHERE in the graph and
  passed at 90% of the expected total, so 71 unrelated nodes and none of the map
  was a PASS, and so was a run with every node and zero edges.

Each case below is the mutant's counter-example: revert the leg and the case
reds. The transport is stubbed (`_post`), so nothing here opens a socket.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import re
import urllib.error as urllib_error
from pathlib import Path
from typing import ClassVar

import pytest

ROOT = Path(__file__).resolve().parents[1]
FILER = ROOT / "graph-scripts" / "business_map_13880" / "file_map.py"
MCP_SERVER = ROOT / "tortoise" / "mcp_server.py"

_spec = importlib.util.spec_from_file_location("business_map_filer", FILER)
assert _spec and _spec.loader
filer = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(filer)

#: The real transport class, captured before any test monkeypatches `filer.Mcp`
#: (a stub that rebuilt itself through the patched name recursed forever).
_RealMcp = filer.Mcp


class _StubMcp:
    """The transport, replaced: every write returns a fresh id."""

    def __init__(self, *_args, **_kw) -> None:
        self._n = 0

    def initialize(self) -> None:  # pragma: no cover - no session to open
        return None

    def call(self, tool: str, args: dict, **_kw) -> dict:
        self._n += 1
        return {"structuredContent": {"id": f"{tool}:{self._n}"}}


class _IdlessMcp(_StubMcp):
    """A transport whose write succeeded without returning an id."""

    def call(self, tool: str, args: dict, **_kw) -> dict:
        return {"structuredContent": {"name": args.get("name") or args.get("content")}}


class _EdgeRefusingMcp:
    """Nodes and points land; every edge write is REFUSED the way this server
    does it — a SUCCESSFUL result whose payload is an error dict.

    The refusal is injected at `_post`, so the REAL `Mcp.call` path decides what
    a refusal is: a stub that swallowed `call()` would pass this test while the
    shipped detection was broken.
    """

    def __init__(self, *_args, **_kw) -> None:
        self._n = 0
        self._inner = _RealMcp("not-a-real-key")

        def _post(body: dict):
            tool = (body.get("params") or {}).get("name")
            self._n += 1
            if tool in ("tortoise_create_edge", "tortoise_create_operator"):
                return (200, {"jsonrpc": "2.0", "id": body.get("id"), "result": {
                    "structuredContent": {"error": "quota exceeded for operators",
                                          "code": "ERR_QUOTA"}}}, {})
            return (200, {"jsonrpc": "2.0", "id": body.get("id"), "result": {
                "structuredContent": {"id": f"{tool}:{self._n}"}}}, {})

        self._inner._post = _post

    def initialize(self) -> None:  # pragma: no cover - no session to open
        return None

    def call(self, tool: str, args: dict, **kw) -> dict:
        return self._inner.call(tool, args, **kw)


@pytest.fixture()
def map_doc() -> dict:
    return filer.load_map()


def test_a_tool_result_with_isError_raises_instead_of_returning(monkeypatch):
    """The headline fix: an `isError` result is a FAILED call.

    Mutant: delete the `result.get("isError")` leg — `call()` returns the failed
    result and the filer falls back to the item's own name as its id.
    """
    mcp = filer.Mcp("not-a-real-key")
    monkeypatch.setattr(
        mcp, "_post",
        lambda body: (200, {"jsonrpc": "2.0", "result": {
            "isError": True,
            "content": [{"type": "text", "text": "storage allowance reached"}],
        }}, {}),
    )
    with pytest.raises(RuntimeError, match="FAILED: storage allowance reached"):
        mcp.call("tortoise_create_subject", {"name": "x"})


def test_write_map_files_every_node_and_edge(tmp_path, monkeypatch, map_doc, capsys):
    """The door RUNS. Before the #13913 fix `_resolve` was bound as a method
    with no receiver, so this raised on the first subject.

    Mutant: restore the `self` parameter on `_resolve` — a TypeError.
    """
    monkeypatch.setattr(filer, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setenv("TORTOISE_MCP_API_KEY", "not-a-real-key")
    monkeypatch.setattr(filer, "Mcp", _StubMcp)

    assert filer.write_map(map_doc) == 0

    state = json.loads((tmp_path / "state.json").read_text())
    assert state["failed"] == []
    assert len(state["ids"]) == len(filer.map_identities(map_doc)) == 78
    assert len(state["written"]) == 78 + len(map_doc["edges"])
    capsys.readouterr()


def test_write_map_refuses_a_response_with_no_id(tmp_path, monkeypatch, map_doc):
    """`_resolve`'s refusal: a name is not an id.

    Mutant: `return got or name` — the pre-fix fallback that files edges between
    nodes which do not exist.
    """
    monkeypatch.setattr(filer, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setenv("TORTOISE_MCP_API_KEY", "not-a-real-key")
    monkeypatch.setattr(filer, "Mcp", _IdlessMcp)

    with pytest.raises(RuntimeError, match="returned no id"):
        filer.write_map(map_doc)


class _RecordingMcp:
    """A transport that records every call it is asked to make."""

    instances: ClassVar[list[_RecordingMcp]] = []

    def __init__(self, *_a, **_k) -> None:
        self.calls: list[tuple[str, dict]] = []
        _RecordingMcp.instances.append(self)

    def initialize(self) -> None:
        return None

    def call(self, tool: str, args: dict, **_kw) -> dict:
        self.calls.append((tool, dict(args)))
        return {"structuredContent": {"id": f"{tool}:{len(self.calls)}"}}


def test_write_map_refuses_a_state_whose_ids_are_item_identities(tmp_path, monkeypatch, map_doc):
    """A `state.json` full of NAMES is not proof of filing, and must not be
    resumed over.

    This is the on-disk shape of the false PASS the PR body documents: the
    pre-fix run recorded each item's name/url/content as its "id". A resume then
    finds every marker present, prints "all nodes and edges written" and exits 0
    with nothing in the graph.

    Mutant: delete the `named` guard in `write_map` — this run makes zero calls
    and returns 0, which is exactly the silent success it refuses.
    """
    stale = {"ids": {}, "written": [], "failed": []}
    for section in ("subjects", "objects", "sources", "events", "points"):
        for item in map_doc[section]:
            _prop, value = filer.declared_identity(item)
            stale["ids"][item["ref"]] = value            # an identity, not an id
    stale["written"] = [f"{section[:-1]}:{item['ref']}"
                        for section in ("subjects", "objects", "sources", "events", "points")
                        for item in map_doc[section]]
    path = tmp_path / "state.json"
    path.write_text(json.dumps(stale))
    monkeypatch.setattr(filer, "STATE_PATH", path)
    monkeypatch.setenv("TORTOISE_MCP_API_KEY", "not-a-real-key")
    monkeypatch.setattr(filer, "Mcp", _RecordingMcp)
    _RecordingMcp.instances.clear()

    assert filer.write_map(map_doc) == 2
    assert _RecordingMcp.instances[-1].calls == []


def test_write_map_refuses_index_keyed_edge_markers(tmp_path, monkeypatch, map_doc):
    """An index is not an identity: markers keyed by POSITION cannot be resumed
    after the map is edited.

    Measured on the pre-fix spelling (`f"edge:{i}"`): insert one newly-judged
    edge at `edges[0]` and the resume makes exactly ONE call — re-issuing the old
    LAST edge as the new `edge:101` — never writes the inserted edge, and exits 0
    printing `all nodes and edges written`.

    Mutant: `marker = f"edge:{i}"` and no stale-marker guard — this run returns 0
    and writes the wrong edge.
    """
    stale = {"ids": {}, "written": [], "failed": []}
    for section in ("subjects", "objects", "sources", "events", "points"):
        for item in map_doc[section]:
            stale["ids"][item["ref"]] = f"id:{item['ref']}"
            stale["written"].append(f"{section[:-1]}:{item['ref']}")
    stale["written"] += [f"edge:{i}" for i in range(len(map_doc["edges"]))]
    path = tmp_path / "state.json"
    path.write_text(json.dumps(stale))
    monkeypatch.setattr(filer, "STATE_PATH", path)
    monkeypatch.setenv("TORTOISE_MCP_API_KEY", "not-a-real-key")
    monkeypatch.setattr(filer, "Mcp", _RecordingMcp)
    _RecordingMcp.instances.clear()

    assert filer.write_map(map_doc) == 2
    assert _RecordingMcp.instances[-1].calls == []


def test_write_map_resumes_after_an_edge_is_added(tmp_path, monkeypatch, map_doc, capsys):
    """The reason the markers are identity-keyed: an ADDED edge is written, and
    the edges already filed are not re-issued.

    Mutant: index-keyed markers — the second run makes one call for the old last
    edge (skipping the inserted one entirely).
    """
    monkeypatch.setattr(filer, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setenv("TORTOISE_MCP_API_KEY", "not-a-real-key")
    monkeypatch.setattr(filer, "Mcp", _RecordingMcp)
    _RecordingMcp.instances.clear()
    assert filer.write_map(map_doc) == 0
    capsys.readouterr()

    edited = json.loads(json.dumps(map_doc))
    edited["edges"].insert(0, {"from": edited["points"][0]["ref"],
                              "to": edited["objects"][0]["ref"],
                              "relation": "references"})

    before = len(_RecordingMcp.instances)
    assert filer.write_map(edited) == 0
    calls = _RecordingMcp.instances[before].calls
    assert len(calls) == 1, calls
    assert calls[0][0] == "tortoise_create_edge", calls
    assert calls[0][1]["source_id"].startswith("tortoise_create_point:"), calls
    capsys.readouterr()


def test_a_duplicated_edge_is_refused_by_validate(map_doc):
    """The same edge judged twice is two judgements of one fact — and ONE written
    edge certifies both, so the duplicate is invisible to the read-back.

    Mutant: drop the edge-duplicate arm from `validate()`.
    """
    duped = json.loads(json.dumps(map_doc))
    duped["edges"].append(json.loads(json.dumps(duped["edges"][0])))
    errs = filer.validate(duped)
    assert any("judged twice" in e for e in errs), errs


def test_write_map_resumes_a_state_whose_ids_are_real(tmp_path, monkeypatch, map_doc, capsys):
    """Non-vacuity: a state file of REAL ids still resumes (skips) as before."""
    monkeypatch.setattr(filer, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setenv("TORTOISE_MCP_API_KEY", "not-a-real-key")
    monkeypatch.setattr(filer, "Mcp", _RecordingMcp)
    _RecordingMcp.instances.clear()
    assert filer.write_map(map_doc) == 0
    capsys.readouterr()

    before = len(_RecordingMcp.instances)
    assert filer.write_map(map_doc) == 0                       # second run
    assert len(_RecordingMcp.instances) == before + 1          # it ran...
    assert _RecordingMcp.instances[-1].calls == []             # ...and wrote nothing
    capsys.readouterr()


def test_the_edge_arm_ignores_edges_that_are_not_the_map_s(map_doc):
    """101 edges among FOREIGN nodes are not the map's 101 edges.

    Mutant: credit an edge whenever each endpoint matches ANY identity present
    (`& present`) — both foreign-edge cases go green with zero declared edges.
    """
    foreign_nodes = [["Object", f"foreign-{i}", "", ""] for i in range(30)]
    foreign_edges = [["IMPL", "Object", "", f"foreign-{i}", "",
                      "Object", "", f"foreign-{i + 1}", ""]
                     for i in range(29)]
    assert filer.verdict(map_doc, _node_rows(map_doc) + foreign_nodes,
                         foreign_edges, []) == 1
    padded = [foreign_edges[i % len(foreign_edges)] for i in range(len(map_doc["edges"]))]
    assert filer.verdict(map_doc, _node_rows(map_doc) + foreign_nodes, padded, []) == 1


def test_the_edge_arm_reads_the_relation_not_only_the_endpoints(map_doc):
    """101 edges between the right nodes with the WRONG relations is not filed.

    `NAND` vs `IMPL` is the one distinction whose inversion changes what the graph
    MEANS, so a census blind to the relation certifies exactly that defect.

    Mutant: drop the relation from the match — every case here goes green.
    """
    structural, operators = _edge_rows(map_doc)
    unrelated = [["related", *row[1:]] for row in structural]
    assert filer.verdict(map_doc, _node_rows(map_doc), unrelated, operators) == 1

    inverted = [["IMPL" if row[1] == "NAND" else row[1], *row[2:]] for row in operators]
    assert sum(1 for e in map_doc["edges"] if e.get("op") == "NAND") > 0
    assert filer.verdict(map_doc, _node_rows(map_doc), structural, inverted) == 1


def test_a_REVERSED_operator_is_not_the_declared_edge(map_doc):
    """An operator whose source and target are swapped is not the declared edge.

    46 of the map's 51 operators declare `->`, i.e. direction is what EP
    propagates, so an unordered endpoint-set match certifies a backwards map.

    Mutant: match on `a in ends and b in ends` — this goes green.
    """
    _structural, operators = _edge_rows(map_doc)
    reversed_rows = []
    for row in operators:
        slot = row[2]
        reversed_rows.append([row[0], row[1], 1 if slot == 0 else 0, *row[3:]])
    assert filer.verdict(map_doc, _node_rows(map_doc), _structural, reversed_rows) == 1


def test_an_operator_over_a_THIRD_node_does_not_cover_a_declared_pair(map_doc):
    """One operator over `a -> b` must not certify a declared `b -> c`."""
    structural, _operators = _edge_rows(map_doc)
    op_edges = [e for e in map_doc["edges"] if "op" in e]
    first = op_edges[0]
    want = filer.direction_of(first)
    rows = [["opX", first["op"], 0, want, *_row_for(map_doc, first["to"])],
            ["opX", first["op"], 1, want, *_row_for(map_doc, first["from"])],
            ["opX", first["op"], 1, want, *_row_for(map_doc, first["to"])]]
    assert filer.verdict(map_doc, _node_rows(map_doc), structural, rows) == 1


def test_the_verdict_refuses_DUPLICATED_nodes(map_doc):
    """Two copies of a non-idempotent node are invisible to a set-based read-back.

    Mutant: drop the `duplicates()` arm — this reports 78/78 and exits 0 over a
    graph holding two copies of every decision.
    """
    nodes = _node_rows(map_doc)
    structural, operators = _edge_rows(map_doc)
    assert filer.verdict(map_doc, nodes, structural, operators) == 0
    assert filer.verdict(map_doc, nodes + nodes, structural, operators) == 1
    assert filer.duplicates(map_doc, nodes) == []


def test_the_structural_edge_query_is_NULL_SAFE():
    """`NOT (a.is_operator = true)` DROPS every non-Point-sourced edge.

    A Subject/Object/Source/Event carries no `is_operator` property, so the old
    predicate evaluated to NULL for all 50 structural edges of this map and
    `WHERE NULL` excluded them — `--verify` failed a correctly filed map.

    Mutant: restore `NOT (a.is_operator = true OR a.op_type IS NOT NULL)`.
    """
    predicate = filer.NON_OPERATOR_PREDICATE
    assert "a.is_operator IS NULL" in predicate
    assert not predicate.lstrip("(").startswith("NOT ")
    # the three-valued truth table that makes the old form wrong
    def old(is_op, op_type):          # the pre-fix predicate, verbatim
        return not ((is_op is True) or (op_type is not None))
    def new(is_op, op_type):
        return (is_op is None or is_op is False) and op_type is None
    assert old(None, None) is True, "python `not None` is True; CYPHER `NOT null` is NULL"
    assert new(None, None) is True     # a non-Point source IS non-operator
    assert old(True, None) is False and new(True, None) is False


def test_an_UNKNOWN_outcome_keeps_the_intent_marker(tmp_path, monkeypatch, map_doc, capsys):
    """A transport death leaves the marker in place, so the next run does not
    re-issue the operator.

    Mutant: `except Exception: unmark(marker)` (one arm for both classes) — the
    next run writes a SECOND operator over the same endpoints (#10849).
    """
    state_path = tmp_path / "state.json"
    op_calls = {"n": 0}

    class _DiesOnOperator(_StubMcp):
        def __init__(self, *_a, **_k) -> None:
            self._n = 0
            self._inner = _RealMcp("not-a-real-key")

            def _post(body: dict):
                tool = (body.get("params") or {}).get("name")
                if tool == "tortoise_create_operator":
                    op_calls["n"] += 1
                    raise filer.urllib.error.URLError("connection reset")
                self._n += 1
                return (200, {"jsonrpc": "2.0", "id": body.get("id"), "result": {
                    "structuredContent": {"id": f"{tool}:{self._n}"}}}, {})

            self._inner._post = _post

        def call(self, tool: str, args: dict, **kw) -> dict:
            return self._inner.call(tool, args, **kw)

    monkeypatch.setattr(filer, "STATE_PATH", state_path)
    monkeypatch.setenv("TORTOISE_MCP_API_KEY", "not-a-real-key")
    monkeypatch.setattr(filer, "Mcp", _DiesOnOperator)
    first_op = next(e for e in map_doc["edges"] if "op" in e)
    assert filer.write_map(map_doc) == 1
    capsys.readouterr()
    written = json.loads(state_path.read_text())["written"]
    assert filer.edge_marker(first_op) in written, (
        "the UNKNOWN outcome retired the intent marker")


def test_a_marker_without_an_id_is_AMBIGUOUS_not_re_created(tmp_path, monkeypatch, map_doc, capsys):
    """A recorded-but-id-less Event must not be re-created on resume.

    `create_event` has no existence check, so re-issuing mints a SECOND Event
    carrying the same identity — and `--verify`'s identity arm cannot see it
    (that is what `duplicates()` is for, checked separately).

    Mutant: `if already(...) and ref in ids: continue` (the pre-fix guard) —
    every event is re-created and the run exits 0.
    """
    stale = {"ids": {}, "written": [], "failed": []}
    for e in map_doc["events"]:
        # the marker the WRITER records (`item_marker`), not a hand-built
        # `event:<ref>`: a marker the map does not declare is refused earlier
        # by the orphan arm, which is a different refusal than this one.
        stale["written"].append(filer.item_marker("events", e))
    path = tmp_path / "state.json"
    path.write_text(json.dumps(stale))
    monkeypatch.setattr(filer, "STATE_PATH", path)
    monkeypatch.setenv("TORTOISE_MCP_API_KEY", "not-a-real-key")
    monkeypatch.setattr(filer, "Mcp", _RecordingMcp)
    _RecordingMcp.instances.clear()

    assert filer.write_map(map_doc) == 1
    calls = [c for c in _RecordingMcp.instances[-1].calls
             if c[0] == "tortoise_create_event"]
    assert calls == [], f"re-created {len(calls)} event(s) whose outcome is UNKNOWN"
    capsys.readouterr()


def test_a_state_whose_SOURCE_ids_are_urls_is_legitimate(map_doc, tmp_path, monkeypatch, capsys):
    """A Source's `id` IS its url, so that must not read as a forged id.

    `entities.py`: `s.id = coalesce($id, $url)`. The guard refused every state a
    successful run wrote, and its printed remedy (delete the file) re-issued all
    14 events and 51 operators.

    Mutant: drop the `if "url" in item: return False` arm — this exits 2.
    """
    real_ids = {"ids": {s["ref"]: s["url"] for s in map_doc["sources"]},
                "written": [], "failed": []}
    path = tmp_path / "state.json"
    path.write_text(json.dumps(real_ids))
    monkeypatch.setattr(filer, "STATE_PATH", path)
    monkeypatch.setenv("TORTOISE_MCP_API_KEY", "not-a-real-key")
    monkeypatch.setattr(filer, "Mcp", _RecordingMcp)
    _RecordingMcp.instances.clear()
    assert filer.write_map(map_doc) == 0
    capsys.readouterr()


def test_validate_refuses_a_mechanism_that_CONTRADICTS_its_meaning(map_doc):
    """`IMPL` + `refutes` is the pair that INVERTS the judgement.

    The write gate only WARNS (#9963, warn-not-block) and stores the mechanism
    as asked — "An IMPL wire carrying a NAND meaning RAISES the target's
    confidence where the meaning says it must LOWER it" — so this validator is
    the last gate before an inverted operator is filed. The shipped map had
    exactly one such pair (a `refutes` edge wired `IMPL`); it is a `NAND` now.

    Mutant: drop the pairing arm — the inverted pair validates clean.
    """
    assert not [e for e in filer.validate(map_doc) if "contradicts the meaning" in e]
    inverted = json.loads(json.dumps(map_doc))
    for edge in inverted["edges"]:
        if edge.get("label") == "refutes":
            edge["op"] = "IMPL"
            break
    else:  # pragma: no cover - the map carries a refutes edge
        pytest.fail("no refutes edge in the map")
    errs = filer.validate(inverted)
    assert any("contradicts the meaning" in e for e in errs), errs


def test_a_mechanism_mismatch_warning_is_a_FAILURE(tmp_path, monkeypatch, map_doc, capsys):
    """A write that lands with the WRONG mechanism is reported, not filed silently.

    The gate warns and proceeds, so the marker must stay (un-recording would
    re-issue a non-idempotent write) while the run must NOT exit 0.

    Mutant: ignore `warnings` — this exits 0 over an inverted operator and the
    failure never reaches `state.json`.
    """
    state_path = tmp_path / "state.json"

    class _Warns(_RealMcp):
        def __init__(self, *_a, **_k) -> None:
            super().__init__("not-a-real-key")
            self._n = 0
            inner = self

            def _post(body: dict):
                tool = (body.get("params") or {}).get("name")
                inner._n += 1
                payload = {"id": f"{tool}:{inner._n}"}
                if tool == "tortoise_create_operator":
                    payload["warnings"] = [{"code": "mechanism_mismatch",
                                            "message": "IMPL carrying refutes"}]
                return (200, {"jsonrpc": "2.0", "id": body.get("id"),
                              "result": {"structuredContent": payload}}, {})

            self._post = _post

    monkeypatch.setattr(filer, "STATE_PATH", state_path)
    monkeypatch.setenv("TORTOISE_MCP_API_KEY", "not-a-real-key")
    monkeypatch.setattr(filer, "Mcp", _Warns)
    assert filer.write_map(map_doc) == 1
    state = json.loads(state_path.read_text())
    assert any("mechanism_mismatch" in f for f in state["failed"])
    assert len([m for m in state["written"] if m.startswith("edge:")]) == len(map_doc["edges"])
    capsys.readouterr()


def test_save_state_is_ATOMIC_and_leaves_no_temp(tmp_path, monkeypatch):
    """The resume record is replaced in one step.

    A kill inside a plain `write_text` truncates it, and the next run loses the
    recorded intents — which is how a truncated state file re-issues the
    non-idempotent writes the record exists to protect.
    """
    monkeypatch.setattr(filer, "STATE_PATH", tmp_path / "state.json")
    filer.save_state({"ids": {"a": "1"}, "written": ["x"], "failed": []})
    assert json.loads((tmp_path / "state.json").read_text())["written"] == ["x"]
    assert list(tmp_path.glob("*.tmp")) == []


def test_validate_refuses_an_undeclared_object_kind(map_doc):
    """A kind neither core nor an installed pack declares is refused.

    Mutant: `def validate(doc): return []`.
    """
    bad = json.loads(json.dumps(map_doc))
    bad["objects"][0]["objectKind"] = "core:notARealKind"
    errs = filer.validate(bad)
    assert any("is not declared by core or any installed pack" in e for e in errs), errs


def test_validate_refuses_an_orphan(map_doc):
    """A filed node with no edge is invisible — so it is refused."""
    orphaned = json.loads(json.dumps(map_doc))
    orphaned["edges"] = []
    errs = filer.validate(orphaned)
    assert any("no edges — an orphan node is invisible" in e for e in errs), errs


def _row_of(ident: tuple[str, str, str], extra: dict[str, str] | None = None
            ) -> list[str]:
    """A read-back row for one (label, property, value) identity.

    `extra` carries the OTHER identity properties the engine also writes on that
    node. It matters for an Event: `create_event` stores `name` AND `content`, so
    a row holding exactly one identity is a shape the graph never produces — and
    a read-back tested only against that shape cannot see the arm that counts
    identities instead of endpoints (#13913 review round 9).
    """
    row = [ident[0], "", "", ""]  # label + `filer.IDENTITY_PROPS` order
    row[1 + filer.IDENTITY_PROPS.index(ident[1])] = ident[2]
    for prop, value in (extra or {}).items():
        row[1 + filer.IDENTITY_PROPS.index(prop)] = value
    return row


def _row_for(doc: dict, ref: str) -> list[str]:
    """The identity row `verify()` reads back for one declared ref.

    Taken from the same authority the writer uses (`identity_by_ref`), so the row
    carries the LABEL as its first column: a value alone cannot say what kind of
    node carries it, and the read-back must read the kind (#13913 round 5).

    ⛔ AND IT CARRIES THE EVENT'S OTHER IDENTITY. `tortoise_create_event` writes
    `name` and `content` (both are `IDENTITY_PROPS`), so the engine's Event row is
    a TWO-identity row. A fixture that gives it one cannot exercise the operator
    arm's endpoint counting — measured: the live shape reddened four tests before
    that arm was fixed (#13913 review round 9).
    """
    ident = filer.identity_by_ref(doc)[ref]
    extra: dict[str, str] = {}
    if ident[0] == "Event":
        event = filer.doc_by_ref(doc, ref)
        extra["content"] = event["content"]
        # keep the declared identity on its own property even if `content` was
        # the identity somehow (it is not for an Event, but do not assume)
        extra.pop(ident[1], None)
    return _row_of(ident, extra)


def _node_rows(doc: dict) -> list[list[str]]:
    refs = ([s["ref"] for s in doc["subjects"]]
            + [o["ref"] for o in doc["objects"]]
            + [s["ref"] for s in doc["sources"]]
            + [e["ref"] for e in doc["events"]]
            + [p["ref"] for p in doc["points"]])
    rows = [_row_for(doc, ref) for ref in refs]
    assert len(rows) == len(filer.map_identities(doc))
    return rows


def test_every_write_uses_the_surface_spelling(tmp_path, monkeypatch, map_doc, capsys):
    """Each `tools/call` names only parameters its handler ACCEPTS, and supplies
    every one the handler requires without a default.

    This is the pin for the class #13913's review found: the door was written
    against parameter spellings the surface does not have (`src`/`targets`,
    `from_id`/`to_id`/`relation`, `status`/`documentKind` as top-level keywords,
    an event with no `name`), and `--dry-run` cannot see any of it — the first
    symptom is a `TypeError` from the handler, which the pre-fix filer reported
    as a successful write.

    The signatures are read from `tortoise/mcp_server.py` with `ast`, not copied:
    a transcription drifts, and a drifted transcription is exactly the false PASS
    this map's own validator refuses.
    """
    sent: dict[str, list[dict]] = {}

    class _Recording(_StubMcp):
        def call(self, tool: str, args: dict, **_kw) -> dict:
            sent.setdefault(tool, []).append(dict(args))
            return super().call(tool, args)

    monkeypatch.setattr(filer, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setenv("TORTOISE_MCP_API_KEY", "not-a-real-key")
    monkeypatch.setattr(filer, "Mcp", _Recording)
    assert filer.write_map(map_doc) == 0
    capsys.readouterr()

    signatures = _handler_signatures()
    assert set(sent) == {
        "tortoise_create_subject", "tortoise_create_object", "tortoise_create_source",
        "tortoise_create_event", "tortoise_create_point", "tortoise_create_operator",
        "tortoise_create_edge",
    }
    for tool, calls in sent.items():
        accepted, required = signatures[tool]
        for args in calls:
            unknown = sorted(set(args) - accepted)
            assert not unknown, (
                f"{tool} does not take {unknown} — it takes {sorted(accepted)}"
            )
            missing = sorted(required - set(args))
            assert not missing, f"{tool} requires {missing} and was not given them"


def _handler_signatures() -> dict[str, tuple[set[str], set[str]]]:
    """tool name -> (every parameter it accepts, the ones with no default).

    Read from the module the MCP adapter registers (`FastMCPAdapter` binds each
    registry entry to the module-level function of the same name), so the pin
    is against the surface's own definitions rather than a copy of them.
    """
    tree = ast.parse(MCP_SERVER.read_text(encoding="utf-8"))
    out: dict[str, tuple[set[str], set[str]]] = {}
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        positional = [a.arg for a in node.args.posonlyargs + node.args.args]
        keyword_only = [a.arg for a in node.args.kwonlyargs]
        with_default = len(node.args.defaults)
        required = set(positional[: len(positional) - with_default])
        required |= {
            name
            for name, default in zip(node.args.kwonlyargs, node.args.kw_defaults,
                                     strict=False)
            if default is None
        }
        out[node.name] = (set(positional) | set(keyword_only), required)
    return out


def test_the_signature_pin_reads_the_real_surface():
    """Non-vacuity: the pin sees the surface, and would catch a wrong spelling."""
    signatures = _handler_signatures()
    accepted, required = signatures["tortoise_create_edge"]
    assert accepted == {"source_id", "target_id", "predicate"}
    assert required == accepted
    assert "from_id" not in accepted

    accepted, required = signatures["tortoise_create_event"]
    assert required == {"name", "eventKind"}


def test_a_refusal_payload_without_isError_raises(monkeypatch):
    """The refusal channel `isError` does NOT carry.

    This server's handlers return `_SafeError(...)`, a dict subclass, so a
    quota/validation refusal arrives as a SUCCESSFUL result whose payload is
    `{"error": …}` with `isError: false`. Reading `isError` alone marked refused
    edge writes as filed — #13880's false PASS one layer down.

    Mutant: drop the payload arm of `_refusal` — this returns the error dict as
    a successful call.
    """
    mcp = filer.Mcp("not-a-real-key")
    monkeypatch.setattr(
        mcp, "_post",
        lambda body: (200, {"jsonrpc": "2.0", "result": {
            "structuredContent": {"error": "quota exceeded", "code": "ERR_QUOTA"},
        }}, {}),
    )
    with pytest.raises(RuntimeError, match="FAILED: quota exceeded"):
        mcp.call("tortoise_create_edge", {})


def test_a_refused_edge_is_not_marked_as_written(tmp_path, monkeypatch, map_doc, capsys):
    """A refused edge is a FAILURE: exit 1, recorded in `state.json`, never
    marked `written` (a marked failure is skipped forever on resume).

    Mutant: `mark(marker)` inside `try` with no refusal check.
    """
    monkeypatch.setattr(filer, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setenv("TORTOISE_MCP_API_KEY", "not-a-real-key")
    monkeypatch.setattr(filer, "Mcp", _EdgeRefusingMcp)

    assert filer.write_map(map_doc) == 1
    state = json.loads((tmp_path / "state.json").read_text())
    assert state["failed"], "the refusal vanished"
    assert not [m for m in state["written"] if m.startswith("edge:")]
    capsys.readouterr()


def test_a_non_idempotent_write_is_not_retried(monkeypatch):
    """`retry_safe=False` stops on a 5xx instead of re-issuing.

    `create_operator` is explicitly NOT idempotent — a retry writes a SECOND
    operator and EP double-counts it (#10849, `tool_registry.py`) — so the door
    must report the UNKNOWN outcome, not retry it.

    Mutant: ignore `retry_safe` and take the retry arm — `_post` is called more
    than once and the write is silently duplicated.
    """
    mcp = filer.Mcp("not-a-real-key")
    calls = []

    def _post(_body):
        calls.append(1)
        raise filer.urllib.error.URLError("connection reset")

    monkeypatch.setattr(mcp, "_post", _post)
    with pytest.raises(RuntimeError, match="outcome UNKNOWN"):
        mcp.call("tortoise_create_operator", {}, retry_safe=False)
    assert len(calls) == 1, f"retried a non-idempotent write {len(calls)} times"


def test_the_event_identity_is_the_name_its_door_writes(map_doc):
    """`map_identities` keys an Event on `name`, the property the Event door is
    REQUIRED to write — not on `content`, which it only takes as a prop.

    Mutant: key Events on `content` — a correctly filed map then reports 14
    missing nodes and `--verify` refuses forever (#13913 review).
    """
    names = {value for _label, prop, value in filer.map_identities(map_doc)
             if prop == "name"}
    event_names = {filer.event_name(e) for e in map_doc["events"]}
    assert event_names <= names, sorted(event_names - names)
    contents = {value for _label, prop, value in filer.map_identities(map_doc)
                if prop == "content"}
    assert not (event_names & contents)
    # The read-back's power rests on identities being DISTINCT: `ev:no-capture-caps`
    # and the `standard` Object "no capture caps" collide on an unprefixed slug, and
    # the read-back then cannot tell them apart (one hides the other).
    idents = filer.map_identities(map_doc)
    assert len(idents) == len(set(idents)) == 78
    assert len({filer.event_name(e) for e in map_doc["events"]}) == len(map_doc["events"])


def test_validate_refuses_two_items_sharing_one_identity(map_doc):
    """`--verify` reads by identity, so a shared identity must be refused.

    Mutant: drop the identity-coverage block from `validate()` — two distinct
    nodes collapse into one read-back key and one of them can be missing without
    the verdict noticing.
    """
    clashing = json.loads(json.dumps(map_doc))
    clashing["objects"][1]["name"] = clashing["objects"][0]["name"]
    errs = filer.validate(clashing)
    assert any("share the identity" in e for e in errs), errs


def test_a_name_shared_across_KINDS_is_not_a_collision(map_doc):
    """The label is part of the identity, so an Event and an Object may share a
    name — and the read-back must still tell them apart.

    Mutant: key identity on `(property, value)` without the label — the
    legitimate same-name pair is refused here, and the read-back in
    `test_an_Object_cannot_certify_a_declared_Event` is satisfied by the wrong
    kind.
    """
    shared = json.loads(json.dumps(map_doc))
    shared["objects"][0]["name"] = filer.event_name(shared["events"][0])
    errs = filer.validate(shared)
    assert not any("share the identity" in e for e in errs), errs


def test_the_readback_verdict_is_identity_scoped_not_a_count(map_doc):
    """The fail-open the review measured: 71 nodes anywhere, none of the map.

    Mutant: `return 0 if got >= expected * 0.9 else 1` over a whole-graph count.
    """
    unrelated = [["Object", f"unrelated-{i}", "", ""] for i in range(71)]
    assert filer.verdict(map_doc, unrelated, [], []) == 1


def test_the_readback_verdict_refuses_a_map_with_no_edges(map_doc):
    """Every node filed and zero edges is not a filed map.

    Mutant: a verdict that counts nodes only — `verify()`'s docstring says edges
    are "the difference between filed and findable", so this is the arm it owes.
    """
    assert filer.verdict(map_doc, _node_rows(map_doc), [], []) == 1


def _edge_rows(doc: dict,
               idents: dict[str, tuple[str, str, str]] | None = None,
               ) -> tuple[list[list], list[list]]:
    """The read-back rows a correctly-filed map would produce.

    Structural edges: `(type, from-label, from-identity…, to-label, to-identity…)`.
    Operator edges: an operator NODE whose typed edges reach both endpoints —
    grouped per operator, mirroring `presence()` and the writer's own shape
    (`MERGE (o)-[:IMPL]->(s)`), with the DIRECTION the write declares.

    `idents` overrides the ref -> identity map, so a case can describe a graph
    whose nodes are the WRONG KIND without hand-building 78 rows.
    """
    idents = idents if idents is not None else filer.identity_by_ref(doc)
    structural: list[list] = []
    operators: list[list] = []
    op_index: dict[str, list] = {}
    for e in doc["edges"]:
        a = _row_of(idents[e["from"]],
                    ({"content": filer.doc_by_ref(doc, e["from"])["content"]}
                     if idents[e["from"]][0] == "Event" else None))
        b = _row_of(idents[e["to"]],
                    ({"content": filer.doc_by_ref(doc, e["to"])["content"]}
                     if idents[e["to"]][0] == "Event" else None))
        if "op" in e:
            nid = op_index.setdefault(e["from"] + "|" + e["to"], [f"op{len(op_index)}"])
            # idx 0 IS the source, idx >= 1 ARE the targets (the fold writes one
            # edge per slot with `idx`) — the read-back matches on the SLOT — and
            # the DIRECTION the write declares rides the operator node too, so a
            # writer that drops the keyword is not certified by a right-ordered
            # slot pair.
            operators.append([nid[0], e["op"], 0, filer.direction_of(e), *a])
            operators.append([nid[0], e["op"], 1, filer.direction_of(e), *b])
        else:
            structural.append([e["relation"], *a, *b])
    return structural, operators


def test_the_readback_verdict_passes_a_complete_map(map_doc):
    """Non-vacuity for the refusals: the real map reads back GREEN."""
    edge_rows, op_rows = _edge_rows(map_doc)
    assert filer.verdict(map_doc, _node_rows(map_doc), edge_rows, op_rows) == 0


def test_the_verdict_refuses_when_only_UNDECLARED_edges_are_present(map_doc):
    """Declared edges, not merely edges between present nodes.

    The door mints edges the map does not declare (`props.extractedFrom` on 8
    Points materialises the Source and links it), so a read-back that counts
    joinable edges printed 101/101 with 8 declared edges absent.

    Mutant: count edges whose endpoints are present (`joined >= expected`) —
    this fixture then reports 50/50 and exits 0.
    """
    edge_rows, op_rows = _edge_rows(map_doc)
    structural = [row for row in edge_rows if row[0] != "extractedFrom"]
    assert len(structural) == 50
    # Eight declared structural edges replaced by an UNRELATED relation over the
    # same endpoints: the edge count is unchanged, the declared set is not.
    sabotaged = structural[:-8] + [["extractedFrom", *row[1:]] for row in structural[-8:]]
    assert filer.verdict(map_doc, _node_rows(map_doc), sabotaged, op_rows) == 1


def test_a_5xx_after_the_last_attempt_raises_instead_of_returning_the_body(monkeypatch):
    """A gateway error body on the final attempt is a FAILURE, never a success.

    Mutant: let the `code >= 500` arm fall through at `attempt == 5` — the body
    `{"message": "Internal Server Error"}` carries no `result`, `_refusal` finds
    nothing, and 101 edges are marked `written` while 50 were never created.
    """
    monkeypatch.setattr(filer.time, "sleep", lambda _s: None)
    mcp = _RealMcp("not-a-real-key")
    monkeypatch.setattr(
        mcp, "_post",
        lambda body: (500, {"message": "Internal Server Error", "status": 500}, {}),
    )
    with pytest.raises(RuntimeError, match="UNCONFIRMED"):
        mcp.call("tortoise_create_edge", {})


def test_a_non_rpc_body_is_not_read_as_success(monkeypatch):
    """A 4xx JSON body is not a tool result — the MCP wire answers `content[]`
    and/or `structuredContent`, so anything else is a refusal.

    Mutant: return `payload.get("result", payload)` unconditionally — a 401
    `{"message": "Unauthorized"}` is then reported as a landed write.
    """
    mcp = _RealMcp("not-a-real-key")
    monkeypatch.setattr(mcp, "_post",
                        lambda body: (401, {"message": "Unauthorized"}, {}))
    with pytest.raises(RuntimeError, match="refused: HTTP 401") as exc:
        mcp.call("tortoise_create_edge", {})
    # ⛔ AND IT IS A DEFINITE REFUSAL, not an UNKNOWN outcome: a 401 is rejected
    # before the handler runs, so the write did not land and the caller may
    # re-issue it after fixing auth. Classifying it `UnknownOutcome` left 51
    # operator intents recorded forever with no way to clear them (#13913 review
    # round 6). Mutant: treat every no-tool-result body as `UnknownOutcome`.
    assert not isinstance(exc.value, filer.UnknownOutcome)


def test_a_non_idempotent_write_records_its_intent_BEFORE_the_call(
        tmp_path, monkeypatch, map_doc, capsys):
    """The operator's marker exists at the moment its request is sent.

    `create_operator` has no existence check: a run killed between the write and
    its response must not duplicate the operator on resume (a duplicate
    double-counts the claim's EP weight, #10849), so the intent is recorded
    first and `--verify` decides whether it landed.

    Mutant: mark AFTER the call — the first operator request sees no marker.
    """
    state_path = tmp_path / "state.json"
    seen: dict[str, bool] = {}
    first_op = next(e for e in map_doc["edges"] if "op" in e)

    class _Intent(_StubMcp):
        def __init__(self, *_a, **_k) -> None:
            self._n = 0
            self._inner = _RealMcp("not-a-real-key")

            def _post(body: dict):
                tool = (body.get("params") or {}).get("name")
                if tool == "tortoise_create_operator" and "op" not in seen:
                    written = json.loads(state_path.read_text())["written"]
                    # THIS edge's marker — structural edges earlier in the list
                    # have their own markers, so a prefix test would pass either way.
                    seen["op"] = filer.edge_marker(first_op) in written
                self._n += 1
                return (200, {"jsonrpc": "2.0", "id": body.get("id"), "result": {
                    "structuredContent": {"id": f"{tool}:{self._n}"}}}, {})

            self._inner._post = _post

        def call(self, tool: str, args: dict, **kw) -> dict:
            return self._inner.call(tool, args, **kw)

    monkeypatch.setattr(filer, "STATE_PATH", state_path)
    monkeypatch.setenv("TORTOISE_MCP_API_KEY", "not-a-real-key")
    monkeypatch.setattr(filer, "Mcp", _Intent)
    assert filer.write_map(map_doc) == 0
    capsys.readouterr()
    assert seen.get("op") is True, "the operator intent was not recorded before the call"


def test_the_structural_relation_set_is_the_SHIPPED_one(map_doc):
    """`validate()` refuses a relation the surface accepts, or it is a transcription.

    `STRUCTURAL_RELS` lags the shipped predicates (`aboutAction` is missing), so
    the validator must read `tortoise.projection.edges._VALID_EDGE_PREDICATES`
    when the repo is importable.

    Mutant: validate against the literal — an `aboutAction` edge is refused.
    """
    from tortoise.projection.edges import _VALID_EDGE_PREDICATES

    vocab = filer.shipped_vocab()
    assert vocab is not None, "the shipped vocabularies are importable here"
    assert vocab["relation"] == set(_VALID_EDGE_PREDICATES)
    assert "aboutAction" in vocab["relation"]
    assert "aboutAction" not in filer.STRUCTURAL_RELS, (
        "the transcription caught up — re-derive this pin")

    # And `validate()` must USE it: a surface-legal edge the transcription omits
    # has to pass. Mutant: validate against `STRUCTURAL_RELS` — this is refused.
    legal = json.loads(json.dumps(map_doc))
    for edge in legal["edges"]:
        if "relation" in edge:
            edge["relation"] = "aboutAction"
            break
    else:  # pragma: no cover - the map always has structural edges
        pytest.fail("the map has no structural edge to re-point")
    errs = filer.validate(legal)
    assert not any("not a valid structural predicate" in e for e in errs), errs


# ── #13913 review round 5: the four fail-open arms ────────────────────────
class _UnknownOperatorOutcome:
    """The transport dies on the operator write: the outcome is UNKNOWN.

    `create_operator` is not idempotent, so `Mcp.call` raises `UnknownOutcome`
    rather than retrying — the caller must NOT un-record the intent (that is the
    arm that re-issued a second operator on resume) and must NOT re-issue it.
    """

    instances: ClassVar[list] = []

    def __init__(self, *_a, **_k) -> None:
        self._n = 0
        self.operator_calls = 0
        self._inner = _RealMcp("not-a-real-key")

        def _post(body: dict):
            tool = (body.get("params") or {}).get("name")
            if tool == "tortoise_create_operator":
                self.operator_calls += 1
                if self.operator_calls == 1:
                    raise filer.urllib.error.URLError("connection reset")
            self._n += 1
            return (200, {"jsonrpc": "2.0", "id": body.get("id"), "result": {
                "structuredContent": {"id": f"{tool}:{self._n}"}}}, {})

        self._inner._post = _post
        _UnknownOperatorOutcome.instances.append(self)

    def initialize(self) -> None:  # pragma: no cover - no session to open
        return None

    def call(self, tool: str, args: dict, **kw) -> dict:
        return self._inner.call(tool, args, **kw)


def test_an_UNCONFIRMED_operator_is_neither_un_remembered_nor_re_issued(
        tmp_path, monkeypatch, map_doc, capsys):
    """An UNKNOWN operator outcome may have LANDED, so the intent stays recorded
    and the resume reports it instead of writing it again.

    Measured before the fix: `except Exception: unmark(marker)` fired on the
    `UnknownOutcome` path too, so the re-run made **one more operator call over the
    same endpoints** and printed `all nodes and edges written` — the #10849 EP
    double-count. Mutants: (a) `except Exception: unmark(marker)` (no distinct
    class) → the resume re-issues; (b) un-recording removed everywhere → a
    DEFINITE refusal is skipped on resume.
    """
    state = tmp_path / "state.json"
    monkeypatch.setattr(filer, "STATE_PATH", state)
    monkeypatch.setenv("TORTOISE_MCP_API_KEY", "not-a-real-key")

    monkeypatch.setattr(filer, "Mcp", _UnknownOperatorOutcome)
    _UnknownOperatorOutcome.instances.clear()
    assert filer.write_map(map_doc) == 1
    capsys.readouterr()

    first = _UnknownOperatorOutcome.instances[-1]
    op_edges = len([e for e in map_doc["edges"] if "op" in e])
    assert first.operator_calls == op_edges, (
        f"{first.operator_calls} operator calls for {op_edges} operators — "
        "a non-idempotent write was retried")
    recorded = json.loads(state.read_text())
    unconfirmed = recorded["unconfirmed"]
    assert len(unconfirmed) == 1, unconfirmed
    marker = unconfirmed[0]
    assert marker in recorded["written"], "the intent was un-recorded on UNKNOWN"

    # A healthy re-run: reports the unresolved operator, never re-issues it.
    monkeypatch.setattr(filer, "Mcp", _RecordingMcp)
    _RecordingMcp.instances.clear()
    assert filer.write_map(map_doc) == 1
    capsys.readouterr()
    sent = [tool for tool, _args in _RecordingMcp.instances[-1].calls]
    assert "tortoise_create_operator" not in sent, (
        "the resume re-issued a write whose outcome is UNKNOWN")
    failed = json.loads(state.read_text())["failed"]
    assert any(marker in f for f in failed), failed


def test_a_recorded_event_intent_without_an_id_is_not_re_issued(
        tmp_path, monkeypatch, map_doc, capsys):
    """A recorded-but-unresolved event intent is REPORTED, not written again.

    `create_event` has no existence check, so the state left by an interrupted
    run (marker recorded, no id) used to fall through the `and e['ref'] in ids`
    guard and issue a SECOND event. Mutant: restore that guard as the only
    condition — the resume calls `tortoise_create_event` again.
    """
    state = tmp_path / "state.json"
    monkeypatch.setattr(filer, "STATE_PATH", state)
    monkeypatch.setenv("TORTOISE_MCP_API_KEY", "not-a-real-key")
    monkeypatch.setattr(filer, "Mcp", _RecordingMcp)
    _RecordingMcp.instances.clear()
    assert filer.write_map(map_doc) == 0
    capsys.readouterr()

    event_ref = map_doc["events"][0]["ref"]
    recorded = json.loads(state.read_text())
    del recorded["ids"][event_ref]          # the interrupted-run shape
    state.write_text(json.dumps(recorded))

    monkeypatch.setattr(filer, "Mcp", _RecordingMcp)
    _RecordingMcp.instances.clear()
    assert filer.write_map(map_doc) == 1
    err = capsys.readouterr().err
    sent = [tool for tool, _args in _RecordingMcp.instances[-1].calls]
    assert "tortoise_create_event" not in sent, "a duplicate event was written"
    # It is REPORTED (AMBIGUOUS, exit 1), not silently skipped and not rewritten.
    assert event_ref in err, err


def test_the_verdict_refuses_flipped_operator_DIRECTIONS(map_doc):
    """A recorded operator's DIRECTION is part of the assertion.

    `direction` is stored on the operator node (`entities.py`: `n.direction=$dir`,
    "stored ALWAYS") and the door's default is the MUTUAL reading for IMPL/NAND,
    so a writer that drops the keyword turns 46 one-way edges bidirectional and
    changes what the graph means. Measured before the fix: flip all 51 declared
    directions, read back the unchanged rows → `edges: 101/101`, verdict 0.

    Mutant: `held = any(kind == e['op'] and a in ends and b in ends …)` — this
    fixture passes with every direction flipped.
    """
    edge_rows, op_rows = _edge_rows(map_doc)
    assert filer.verdict(map_doc, _node_rows(map_doc), edge_rows, op_rows) == 0

    flipped = [[row[0], row[1], row[2],
                "<->" if row[3] == "->" else "->", *row[4:]]
               for row in op_rows]
    assert [row[3] for row in flipped] != [row[3] for row in op_rows]
    assert filer.verdict(map_doc, _node_rows(map_doc), edge_rows, flipped) == 1


def test_the_verdict_refuses_an_operator_over_EXTRA_endpoints(map_doc):
    """An operator over three endpoints holds a declared pair's endpoints, so a
    match without an ARITY leg certifies an edge the graph does not hold.

    Mutant: drop `len(ends) == 2` — this padded operator satisfies all 51
    declared operator edges and the verdict returns 0.
    """
    edge_rows, op_rows = _edge_rows(map_doc)
    nid = op_rows[0][0]
    first_op = next(e for e in map_doc["edges"] if "op" in e)
    # The SAME operator, with a THIRD slot (idx 2) also reaching an endpoint: the
    # declared pair is held by slots 0 and 1, so without the arity leg the extra
    # slot is invisible.
    padded = [*op_rows, [nid, first_op["op"], 2, filer.direction_of(first_op),
                         "Point", "", "a third endpoint", ""]]
    assert filer.verdict(map_doc, _node_rows(map_doc), edge_rows, padded) == 1


def test_an_Object_cannot_certify_a_declared_Event(map_doc):
    """The LABEL is part of a node's identity.

    Measured before the fix: drop the `ev:beta-gates-measured` row, add a
    `name`-only row → `nodes: 78/78`, verdict 0 — an Object of the same name
    certified a missing Event. This fixture describes exactly that graph (the
    declared Event is absent; everything joins to an Object carrying its name).

    Mutant: the FULL pre-fix identity, `(property, value)` with no label on
    EITHER side (`identity_by_ref` and `_row_identities`) — this graph then reads
    78/78 and verdict 0, which is the measured false PASS.
    """
    event = map_doc["events"][0]
    declared = filer.identity_by_ref(map_doc)[event["ref"]]
    assert declared[0] == "Event", declared
    impostor = ("Object", declared[1], declared[2])
    assert impostor != declared

    sabotaged = dict(filer.identity_by_ref(map_doc))
    sabotaged[event["ref"]] = impostor
    node_rows = [_row_of(ident) for ident in sabotaged.values()]
    structural, operators = _edge_rows(map_doc, sabotaged)

    assert filer.verdict(map_doc, node_rows, structural, operators) == 1


# ── #13913 review round 6: the ledgers, the 4xx class, and the pins ──────
def test_the_unconfirmed_intent_is_on_disk_BEFORE_the_call(
        tmp_path, monkeypatch, map_doc, capsys):
    """A run killed mid-write leaves the intent ON DISK, not only in memory.

    `pending` is a copy of the saved list, so `save_state(st)` without updating
    `st["unconfirmed"]` persisted nothing: measured with a `BaseException` kill
    at the first `create_operator`, the state on disk read `written=85,
    unconfirmed=[]`, and the healthy resume printed `all nodes and edges
    written`, exited 0, and issued 50 of 51 operators — the missing one
    indistinguishable from a completed write.

    Mutant: `save_state(st)` before `st["unconfirmed"] = sorted(pending)` — the
    kill fixture leaves `unconfirmed` empty and the resume exits 0.
    """
    state_path = tmp_path / "state.json"
    seen: dict[str, object] = {}

    class _Killed(_StubMcp):
        def __init__(self, *_a, **_k) -> None:
            self._n = 0
            self._inner = _RealMcp("not-a-real-key")

            def _post(body: dict):
                tool = (body.get("params") or {}).get("name")
                if tool == "tortoise_create_operator" and "killed" not in seen:
                    seen["killed"] = True
                    seen["on_disk"] = json.loads(state_path.read_text())
                    raise KeyboardInterrupt("SIGINT between the write and its response")
                self._n += 1
                return (200, {"jsonrpc": "2.0", "id": body.get("id"), "result": {
                    "structuredContent": {"id": f"{tool}:{self._n}"}}}, {})

            self._inner._post = _post

        def call(self, tool: str, args: dict, **kw) -> dict:
            return self._inner.call(tool, args, **kw)

    monkeypatch.setattr(filer, "STATE_PATH", state_path)
    monkeypatch.setenv("TORTOISE_MCP_API_KEY", "not-a-real-key")
    monkeypatch.setattr(filer, "Mcp", _Killed)
    with pytest.raises(KeyboardInterrupt):
        filer.write_map(map_doc)
    capsys.readouterr()

    on_disk = seen["on_disk"]
    assert on_disk["unconfirmed"], (
        "the intent was never persisted — a killed run is indistinguishable "
        "from a completed one")
    marker = on_disk["unconfirmed"][0]
    assert marker in on_disk["written"]

    # The resume REPORTS it (never re-issues it) — the round-5 behaviour, now
    # driven by what is actually on disk.
    monkeypatch.setattr(filer, "Mcp", _StubMcp)
    assert filer.write_map(map_doc) == 1
    capsys.readouterr()
    failed = json.loads(state_path.read_text())["failed"]
    assert any(marker in f for f in failed), failed


def test_a_node_is_matched_by_its_LABEL_and_its_declared_property(map_doc):
    """An Event row cannot stand in for a declared POINT.

    An Event is written with `name` AND `props.content`; harvesting every
    non-empty column (and ignoring the label) let that row satisfy a declared
    Point whose content it restates — the Point was certified present without
    ever being written, and a correct graph produced a phantom duplicate.

    ⛔ This pin entered at `b53f66001e` and was SILENTLY DROPPED by the two-lane
    merge `32767afd39`, which is why the net PR diff never carried it (#13913
    review round 9). Restored here.

    Mutant: match on any non-empty column of any row — `verdict` returns 0 here.
    """
    point = map_doc["points"][0]
    # The graph holds the Event (name + content), and NOT the Point, but the
    # Event's content column carries the Point's content.
    rows = [["Event", "a different event entirely", "", point["content"]]]
    assert filer.presence(map_doc, rows, [], [])[0] != []
    assert filer.verdict(map_doc, rows, [], []) == 1
    # And the same row does not double-count as a duplicate of anything.
    full = _node_rows(map_doc)
    assert filer.duplicates(map_doc, full) == []
    extra_row = ["Event", "a different event entirely", "", point["content"]]
    assert filer.duplicates(map_doc, [*full, extra_row]) == []


def test_no_save_persists_an_intent_without_its_unconfirmed_flag(
        tmp_path, monkeypatch, map_doc, capsys):
    """NO reader may ever see the marker without its `unconfirmed` flag.

    `write_once` used to `mark()` (which saves) and update `unconfirmed` in a
    SECOND save, so the disk held `written` = +marker / `unconfirmed` = [] for the
    width of one save — a window a resume reads as a COMPLETED write, skipping a
    non-idempotent operator forever (#13913 review round 9, measured by killing
    inside that save). Both fields are persisted together, once.

    Mutant: `mark(marker)` before `pending.add(marker)` + the combined save.
    """
    state = tmp_path / "state.json"
    snapshots: list[tuple[list, list]] = []
    real_save = filer.save_state
    first_operator_seen = {"at": None}
    counter = {"n": 0}

    class _Snapshotted(_StubMcp):
        def __init__(self, *_a, **_k) -> None:
            self._n = 0
            self._inner = _RealMcp("not-a-real-key")

            def _post(body: dict):
                tool = (body.get("params") or {}).get("name")
                if tool == "tortoise_create_operator" and first_operator_seen["at"] is None:
                    first_operator_seen["at"] = len(snapshots)
                self._n += 1
                return (200, {"jsonrpc": "2.0", "id": body.get("id"), "result": {
                    "structuredContent": {"id": f"{tool}:{self._n}"}}}, {})

            self._inner._post = _post

        def call(self, tool: str, args: dict, **kw) -> dict:
            return self._inner.call(tool, args, **kw)

    def _save(st: dict) -> None:
        counter["n"] += 1
        snapshots.append((sorted(st.get("written") or []),
                          sorted(st.get("unconfirmed") or [])))
        return real_save(st)

    monkeypatch.setattr(filer, "save_state", _save)
    monkeypatch.setattr(filer, "STATE_PATH", state)
    monkeypatch.setenv("TORTOISE_MCP_API_KEY", "not-a-real-key")
    monkeypatch.setattr(filer, "Mcp", _Snapshotted)
    assert filer.write_map(map_doc) == 0
    capsys.readouterr()

    at = first_operator_seen["at"]
    assert at is not None and at > 0, "no operator call observed"
    # Only OPERATOR markers are non-idempotent intents; a structural edge is
    # idempotent and is marked after its call.
    op_markers = {filer.edge_marker(e) for e in map_doc["edges"] if "op" in e}
    for written, unconfirmed in snapshots[:at]:
        intents = [m for m in written if m in op_markers]
        assert set(intents) <= set(unconfirmed), (
            "a snapshot persisted an operator marker with no unconfirmed flag — "
            "a resume in that window would call it a completed write")


def test_the_direction_expectation_is_the_DOOR_s_OWN_resolution(map_doc):
    """`direction_of` is the writer's resolver, not a partial restatement.

    `create_operator` applies FOUR steps (`_effective_direction`): a declared
    semantics outranks a NON-DECLARATION, an absent direction canonicalizes per
    mechanism, and `coherence` rewrites the legacy default. A read-back that
    modelled only the default refused a legal write — `IMPL`+`supports` with no
    declared direction stores `->`, the pre-fix expectation said `<->`.

    Mutant: `return "->" if op == "coherence" else "<->"` — every row below reds.
    """
    cases = [
        ({"op": "IMPL"}, "bidirectional"),                    # absent = mutual
        ({"op": "NAND"}, "unidirectional"),                    # NAND's own default
        ({"op": "coherence"}, "->"),
        ({"op": "IMPL", "direction": "->"}, "->"),             # verbatim
        ({"op": "IMPL", "label": "supports"}, "->"),           # semantics resolves
        ({"op": "NAND", "label": "refutes"}, "->"),
        ({"op": "IMPL", "label": "hasPart"}, "<->"),
        ({"op": "IMPL", "label": "produces"}, "<-"),           # #11083's `<-`
        ({"op": "IMPL", "label": "related"}, "-"),             # neutral transfers none
        ({"op": "IMPL", "direction": "->", "label": "hasPart"}, "->"),
    ]
    for edge, want in cases:
        assert filer.direction_of(edge) == want, (edge, want)


def test_the_direction_FALLBACK_is_the_shipped_resolution(map_doc):
    """The bare-box transcription resolves exactly what the door resolves.

    Non-vacuity for `_fallback_direction`, and the pin for
    `FALLBACK_SEMANTICS_DIRECTIONS`: a drifted copy is how a read-back starts
    refusing (or certifying) the wrong thing.
    """
    from tortoise.pack_registry import SEMANTICS_DIRECTIONS

    assert dict(SEMANTICS_DIRECTIONS) == filer.FALLBACK_SEMANTICS_DIRECTIONS
    edges = [{"op": op, "direction": d, "label": s}
             for op in ("IMPL", "NAND", "coherence")
             for d in (None, "->", "<->", "bidirectional", "unidirectional")
             for s in (None, *SEMANTICS_DIRECTIONS)]
    for edge in edges:
        assert filer._fallback_direction(edge) == filer.direction_of(edge), edge


def test_the_direction_aliases_are_the_SHIPPED_ones():
    """`DIRECTION_ALIASES` is `live.py`'s table, not a fresh transcription."""
    from tortoise.live import DIRECTION_ALIASES

    assert dict(DIRECTION_ALIASES) == filer.DIRECTION_ALIASES


def test_the_section_labels_are_the_projection_s_LABELS():
    """Each declared label is a label the projection actually WRITES.

    The identity's first leg is the node label, so a wrong entry makes `--verify`
    refuse a correctly filed map — and nothing in the suite could see it, because
    the fixture rows are built from `identity_by_ref` (one authority on both
    sides). This pins the declaration against the projection's own `MERGE`/`CREATE`
    statements.

    Mutant: swap any two entries (`subjects`: "Point", `points`: "Subject") — the
    suite stayed green before this pin.
    """
    # ⛔ AN EXPECTED MAPPING, not a membership test: a membership test accepts any
    # PERMUTATION of the five labels (measured — swapping subjects<->points left
    # the suite green), and a swap makes `--verify` report all 78 nodes missing on
    # a correctly filed map (#13913 review round 7).
    assert filer.SECTION_LABELS == {"subjects": "Subject", "objects": "Object",
                                    "sources": "Source", "events": "Event",
                                    "points": "Point"}
    src = (ROOT / "tortoise" / "projection" / "entities.py").read_text(encoding="utf-8")
    for section, label in filer.SECTION_LABELS.items():
        pattern = re.compile(
            rf"(?:MERGE|CREATE)\s*\(\w*:\s*{re.escape(label)}\s*\{{")
        assert pattern.search(src), (
            f"{section} -> {label!r} is not a label the projection writes")
    assert len(set(filer.SECTION_LABELS.values())) == 5


def test_a_CORRECTED_edge_spelling_is_not_silently_skipped(
        tmp_path, monkeypatch, map_doc, capsys):
    """A correction to what an edge SAYS re-files it — it is not skipped.

    Keyed on the endpoints and mechanism alone, flipping an operator's
    `direction` (`->` → `<->`) or its `label` (`addresses` →
    `mutuallySupportive`) left the marker unchanged, so the resume made zero
    calls and printed `all nodes and edges written` with exit 0 over a graph
    still holding the OLD direction and the OLD semantics (measured).
    `validate()` cannot help: both spellings are legal.

    Mutant: `edge_marker` back to `edge:{from}->{to}:{mechanism}` — run 2 exits 0
    with no calls, which is the silent skip this refuses.
    """
    state_path = tmp_path / "state.json"
    monkeypatch.setattr(filer, "STATE_PATH", state_path)
    monkeypatch.setenv("TORTOISE_MCP_API_KEY", "not-a-real-key")
    monkeypatch.setattr(filer, "Mcp", _RecordingMcp)
    _RecordingMcp.instances.clear()
    assert filer.write_map(map_doc) == 0
    capsys.readouterr()

    # A DECLARED flip: for IMPL the door treats `None`/`bidirectional`/`<->` as
    # a non-declaration, so `->` vs `<-` is the pair whose resolution differs.
    base = next(e for e in map_doc["edges"] if "op" in e)
    flipped = dict(base, direction="<-" if base.get("direction") != "<-" else "->")
    relabelled = dict(base, label=("mutuallySupportive"
                                   if base.get("label") != "mutuallySupportive"
                                   else "supports"))
    assert filer.edge_marker(flipped) != filer.edge_marker(base)
    assert filer.edge_marker(relabelled) != filer.edge_marker(base)
    # ...and the two judgements are still ONE edge for the duplicate arm.
    assert filer.edge_key(flipped) == filer.edge_key(base)

    edited = dict(map_doc, edges=[flipped if e is base else e for e in map_doc["edges"]])
    assert filer.write_map(edited) == 2, "a corrected edge must be refused, not skipped"
    out = capsys.readouterr().err
    assert "not declare" in out and "edge:" in out, out
    assert _RecordingMcp.instances[-1].calls == [], (
        "the refused run WROTE through the door instead of stopping at the guard")


def test_the_direction_VOCABULARY_is_the_SHIPPED_one(map_doc):
    """`DIRECTIONS` is READ from `tortoise.live`, not transcribed.

    `DIRECTION_ALIASES` was pinned already; `DIRECTIONS` was not, and it had
    drifted: `live.ACCEPTED_DIRECTION_VALUES - DIRECTIONS` was
    `{'bidirectional', 'unidirectional'}`, so `validate()` refused a
    door-legal `direction: unidirectional` — a validator refusing a
    surface-legal edge, the same class as the `STRUCTURAL_RELS` lag.

    Mutant: the four-literal transcription — the containment assertion reds.
    """
    from tortoise import live

    assert set(live.ACCEPTED_DIRECTION_VALUES) <= filer.DIRECTIONS

    op = next(e for e in map_doc["edges"] if "op" in e)
    declared = dict(op, direction="unidirectional", label="addresses")
    doc = dict(map_doc, edges=[declared if e is op else e for e in map_doc["edges"]])
    assert not [err for err in filer.validate(doc) if "direction" in err], (
        "validate() refused a direction the door accepts")


def test_the_readback_query_columns_are_DERIVED_from_IDENTITY_PROPS(monkeypatch):
    """`verify()`'s Cypher columns follow `IDENTITY_PROPS`, so the two cannot
    drift apart.

    `_row_identities` zips `IDENTITY_PROPS` against `row[1:4]`; if the `RETURN`
    listed the columns in another order, every identity would be read from the
    wrong column and all 78 nodes would report missing against a live graph —
    while every test still passed, because the tests build rows through
    `_row_of`, which indexes the same tuple (measured: re-ordering it reddened
    nothing).

    Mutant: hard-code the three `coalesce(...)` columns — the derived-order
    assertion reds.
    """
    import sys
    import types

    queries: list[str] = []

    class _Graph:
        def execute_command(self, _cmd, _graph, cypher):
            queries.append(cypher)
            return [0, []]

    fake = types.SimpleNamespace(from_url=lambda _uri: _Graph())
    monkeypatch.setitem(sys.modules, "redis", fake)
    monkeypatch.setenv("FALKORDB_CLOUD_URI", "redis://example.invalid:6379/0")

    doc = json.loads(filer.MAP_PATH.read_text())
    # ⛔ A PERMUTATION, not the declared order: asserting the shipped order is
    # already there accepts the hard-coded transcription this test exists to
    # exclude (measured: replacing the derived columns with the literal left the
    # first version of this test GREEN). The tuple is mutated, so a query that
    # does not follow it reds.
    monkeypatch.setattr(filer, "IDENTITY_PROPS", ("content", "name", "url"))
    filer.verify(doc)   # the verdict is not what this pins — the QUERIES are

    node_q = next(q for q in queries
                  if q.startswith("MATCH (n) RETURN labels(n)[0], coalesce("))
    expected = ", ".join(f"coalesce(n.{p},'')" for p in filer.IDENTITY_PROPS)
    assert expected in node_q, node_q
    edge_q = next(q for q in queries if q.startswith("MATCH (a)-[e]->(b)"))
    for prop in filer.IDENTITY_PROPS:
        assert f"coalesce(a.{prop},'')" in edge_q, edge_q
        assert f"coalesce(b.{prop},'')" in edge_q, edge_q
    op_q = next(q for q in queries if "o.is_operator = true" in q)
    for prop in filer.IDENTITY_PROPS:
        assert f"coalesce(x.{prop},'')" in op_q, op_q


def test_an_UNREADABLE_body_is_an_UNKNOWN_outcome_not_a_refusal(monkeypatch):
    """A 2xx whose body cannot be PARSED may still have landed.

    `_post` parses the response itself, so a truncated body (`IncompleteRead`) or
    a proxy error page (`JSONDecodeError`) raises one frame BELOW `call()`'s
    classification. Uncaught, both reached `write_once`'s refusal arm, which
    un-recorded a NON-IDEMPOTENT intent — measured: the resume re-issued
    `create_operator`, the #10849 double-count (#13913 review round 7).

    Mutant: drop `http.client.HTTPException` / `json.JSONDecodeError` from the
    except tuple — both cases raise themselves instead of `UnknownOutcome`.
    """
    for exc in (filer.http.client.IncompleteRead(b"partial"),
                json.JSONDecodeError("Expecting value", "<html>502</html>", 0)):
        mcp = _RealMcp("not-a-real-key")

        def _post(_body, _exc=exc):
            raise _exc

        monkeypatch.setattr(mcp, "_post", _post)
        with pytest.raises(filer.UnknownOutcome):
            mcp.call("tortoise_create_operator", {}, retry_safe=False)


def test_a_408_or_429_is_not_a_pre_handler_refusal(monkeypatch):
    """A timeout may have been read and acted on; a rate limiter can sit INSIDE
    the door. Both must take the UNKNOWN path, never un-record the intent.

    Measured pre-fix: a 429 un-recorded an operator intent and the resume wrote a
    SECOND operator (#13913 review round 7).

    Mutant: `400 <= code < 500` with no 408/429 exclusion — both raise a plain
    `RuntimeError` and the marker is retired.
    """
    for code in (408, 429):
        mcp = _RealMcp("not-a-real-key")
        monkeypatch.setattr(mcp, "_post",
                            lambda body, code=code: (code, None, {}))
        with pytest.raises(filer.UnknownOutcome):
            mcp.call("tortoise_create_operator", {}, retry_safe=False)


def test_the_verdict_refuses_DUPLICATED_operators(map_doc):
    """A re-issued non-idempotent operator is the corruption the intent markers
    exist to prevent — `--verify` must see it.

    An operator carries no `name`/`url`/`content`, so `duplicates()` cannot see
    it and `presence()`'s arm is existential: measured, a graph holding TWO
    copies of every declared operator read `nodes: 78/78 / edges: 101/101` and
    exited 0 (#13913 review round 7, the #10849 EP double-count).

    Mutant: `duplicate_operators()` returns [] — this fixture exits 0.
    """
    edge_rows, op_rows = _edge_rows(map_doc)
    assert filer.duplicate_operators(map_doc, op_rows) == []
    assert filer.verdict(map_doc, _node_rows(map_doc), edge_rows, op_rows) == 0

    # Every operator minted a SECOND time — a fresh node id, same endpoints.
    doubled = [*op_rows, *[[f"{row[0]}~dup", *row[1:]] for row in op_rows]]
    assert filer.duplicate_operators(map_doc, doubled), "the duplicate was invisible"
    assert filer.verdict(map_doc, _node_rows(map_doc), edge_rows, doubled) == 1


def test_an_ERROR_BODY_that_cannot_be_read_is_an_UNKNOWN_outcome(monkeypatch):
    """The BODY READ inside `except HTTPError` is part of the classification.

    That read is one frame below `call()`'s `except` tuple for transport errors,
    and an exception raised inside a sibling `except` handler is not caught by it
    — so an `IncompleteRead` there escaped to `write_once`'s refusal arm and
    un-recorded a NON-IDEMPOTENT intent. Measured over a real socket (a 504 whose
    declared Content-Length is never delivered): the marker was retired, and the
    resume re-issued all 51 operators. A status line without a readable body is
    an UNKNOWN outcome, never a refusal.

    Mutant: drop the inner `try/except` around `exc.read()` — this raises
    `IncompleteRead` and the intent would be un-recorded.
    """
    class _Unreadable(urllib_error.HTTPError):
        def read(self, *_a, **_kw):
            raise filer.http.client.IncompleteRead(b"partial")

    mcp = _RealMcp("not-a-real-key")

    def _post(_body):
        raise _Unreadable("https://example.invalid", 504, "Gateway Timeout", {}, None)

    monkeypatch.setattr(mcp, "_post", _post)
    with pytest.raises(filer.UnknownOutcome):
        mcp.call("tortoise_create_operator", {}, retry_safe=False)


def test_a_REFUSED_write_un_records_but_an_UNCLASSIFIED_one_does_not(
        tmp_path, monkeypatch, map_doc, capsys):
    """`write_once` un-records ONLY on the explicit `Refusal` type.

    The pre-fix arm was "any exception that is not `UnknownOutcome`", so an
    UNCLASSIFIED exception (an error-body read that raised, a bug in this module)
    counted as a refusal and the resume re-issued a non-idempotent write. A
    wrongly-kept intent is recoverable — the report names the remedy and
    `--verify` decides — while a wrongly-retired one duplicates a node the door
    cannot dedup (#10849).

    Mutant: `except Exception: unmark(marker)` — the second run re-issues the
    operator and this reds.
    """
    state_path = tmp_path / "state.json"

    class _Boom(ValueError):
        """Neither a Refusal nor an UnknownOutcome: an unclassified failure."""

    class _UnclassifiedOnOperator(_StubMcp):
        calls = 0

        def call(self, tool: str, args: dict, **_kw) -> dict:
            if tool == "tortoise_create_operator":
                type(self).calls += 1
                raise _Boom("this module has a bug")
            return super().call(tool, args, **_kw)

    monkeypatch.setattr(filer, "STATE_PATH", state_path)
    monkeypatch.setenv("TORTOISE_MCP_API_KEY", "not-a-real-key")
    monkeypatch.setattr(filer, "Mcp", _UnclassifiedOnOperator)

    first_op = next(e for e in map_doc["edges"] if "op" in e)
    marker = filer.edge_marker(first_op)
    assert filer.write_map(map_doc) == 1
    capsys.readouterr()
    on_disk = json.loads(state_path.read_text())
    assert marker in on_disk["written"], (
        "an UNCLASSIFIED exception retired the intent — the resume would re-issue "
        "a non-idempotent write")
    assert marker in on_disk["unconfirmed"], on_disk["unconfirmed"]


def test_the_UNCONFIRMED_operator_report_names_its_remedy(
        tmp_path, monkeypatch, map_doc, capsys):
    """The operator arm says WHICH entries to remove, like the Event arm does.

    "'--verify' decides what landed" with no route to act on that verdict leaves
    the run at exit 1 forever: nothing removes an unconfirmed marker.

    Mutant: the bare "`--verify` decides what landed" text — the remedy
    assertions red.
    """
    state_path = tmp_path / "state.json"
    monkeypatch.setattr(filer, "STATE_PATH", state_path)
    monkeypatch.setenv("TORTOISE_MCP_API_KEY", "not-a-real-key")
    monkeypatch.setattr(filer, "Mcp", _RecordingMcp)
    _RecordingMcp.instances.clear()
    assert filer.write_map(map_doc) == 0
    capsys.readouterr()

    marker = filer.edge_marker(next(e for e in map_doc["edges"] if "op" in e))
    st = json.loads(state_path.read_text())
    st["unconfirmed"] = [marker]
    state_path.write_text(json.dumps(st))

    assert filer.write_map(map_doc) == 1
    err = capsys.readouterr().err
    assert marker in err, err
    assert "unconfirmed" in err and "written" in err, err
# ── #13913 review round 8 ─────────────────────────────────────────────────
class _DeadBody:
    """A response body that dies while being read (proxy truncation / reset)."""

    def read(self) -> bytes:
        raise filer.http.client.IncompleteRead(b"partial")

    def close(self) -> None:      # pytest's resource warning calls this
        return None


def test_an_HTTPError_whose_BODY_dies_is_still_unknown(monkeypatch):
    """`exc.read()` runs INSIDE the `HTTPError` handler, where no sibling
    `except` can reach it: a truncated 5xx body used to escape as
    `IncompleteRead`, so `write_once` read a 5xx as a DEFINITE REFUSAL and
    retired a non-idempotent intent — measured: the resume re-issued
    `create_operator` (#13913 review round 8).

    Mutant: read the body unguarded inside the handler.
    """
    mcp = _RealMcp("not-a-real-key")
    for code in (500, 429, 401):
        exc = filer.urllib.error.HTTPError(
            "https://api.premiselabs.co/mcp/", code, "Server Error", {}, _DeadBody())
        monkeypatch.setattr(mcp, "_post", lambda _b, _e=exc: (_ for _ in ()).throw(_e))
        with pytest.raises(RuntimeError) as err:
            mcp.call("tortoise_create_operator", {}, retry_safe=False)
        if code in (500, 429):
            assert isinstance(err.value, filer.UnknownOutcome), code
        else:  # 401 is rejected before the handler: a refusal, re-issuable
            assert not isinstance(err.value, filer.UnknownOutcome), code


def test_a_body_that_PARSES_to_a_non_object_is_unknown(monkeypatch):
    """A proxy error page can be valid JSON (`[]`, `"Bad Gateway"`, `502`).

    `payload.get` raised `AttributeError` outside every classification, retiring
    the intent exactly like an unparsable body. Mutant: drop the isinstance check.
    """
    mcp = _RealMcp("not-a-real-key")
    for payload in ([], "Bad Gateway", 502, True):
        monkeypatch.setattr(mcp, "_post", lambda _b, _p=payload: (200, _p, {}))
        with pytest.raises(filer.UnknownOutcome):
            mcp.call("tortoise_create_operator", {}, retry_safe=False)


def test_an_operator_with_TWO_targets_in_one_slot_certifies_nothing(map_doc):
    """A slot holds a SET, so bounding slot KEYS does not bound ENDPOINTS.

    Measured pre-fix: replace the two operators over `(source -> target A)` and
    `(source -> target B)` with ONE node whose slot 1 reaches both → `78/78`,
    `101/101`, exit 0 — one node carrying two judgements, priced as one
    three-input factor (#13913 review round 8).

    The two declared edges are the ONLY thing at stake here: every other
    operator row is untouched, so a verdict of 1 is caused by the arity leg.

    Mutant: `all(slot in (0, 1) for slot in slots)` without the per-slot counts.
    """
    # The pair must share source AND mechanism AND direction, or the merged node
    # could not certify the second edge for a reason other than the arity leg —
    # which is what a first draft of this test measured (it picked a NAND and an
    # IMPL over one source, so the mutant passed).
    by_source: dict[tuple, list[dict]] = {}
    for e in map_doc["edges"]:
        if "op" in e:
            by_source.setdefault((e["from"], e["op"], e.get("direction")), []).append(e)
    pair = next(edges for edges in by_source.values() if len(edges) >= 2)
    first, second = pair[0], pair[1]

    edge_rows, op_rows = _edge_rows(map_doc)
    # The two operator nodes the merged one REPLACES: same source, distinct targets.
    nid_of: dict[str, str] = {}
    for e in map_doc["edges"]:
        if "op" in e:
            nid_of.setdefault(e["from"] + "|" + e["to"], f"op{len(nid_of)}")
    replaced = {nid_of[first["from"] + "|" + first["to"]],
                nid_of[second["from"] + "|" + second["to"]]}
    kept = [row for row in op_rows if row[0] not in replaced]
    assert len(kept) == len(op_rows) - 4, "expected two 2-row operator nodes to replace"

    idents = filer.identity_by_ref(map_doc)
    merged = [
        *kept,
        ["opMERGED", first["op"], 0, filer.direction_of(first), *_row_of(idents[first["from"]])],
        ["opMERGED", first["op"], 1, filer.direction_of(first), *_row_of(idents[first["to"]])],
        ["opMERGED", first["op"], 1, filer.direction_of(first), *_row_of(idents[second["to"]])],
    ]
    assert not filer.duplicate_operators(map_doc, merged)
    assert filer.verdict(map_doc, _node_rows(map_doc), edge_rows, merged) == 1


def test_a_duplicate_with_a_DIFFERENT_direction_or_mechanism_is_still_a_duplicate(map_doc):
    """The duplicate census is per ENDPOINT PAIR, not per (mechanism, direction).

    A re-issued `create_operator` that stored a flipped direction (or mechanism)
    still makes EP price the pair twice (#10849); keying the census on
    (mechanism, direction, pair) made it invisible — measured 101/101, exit 0
    (#13913 review round 8).

    Mutant: key `holders` by (mechanism, direction, pair).
    """
    edge_rows, op_rows = _edge_rows(map_doc)
    flipped = [[row[0] + "~dup", row[1], row[2],
                "<->" if row[3] == "->" else "->", *row[4:]] for row in op_rows]
    other_mech = [[row[0] + "~dup2", "NAND" if row[1] == "IMPL" else "IMPL",
                   row[2], row[3], *row[4:]] for row in op_rows]
    assert filer.duplicate_operators(map_doc, [*op_rows, *flipped]), "direction-blind"
    assert filer.duplicate_operators(map_doc, [*op_rows, *other_mech]), "mechanism-blind"
    assert filer.verdict(map_doc, _node_rows(map_doc), edge_rows,
                         [*op_rows, *flipped]) == 1


def test_the_bare_invocation_is_not_success(monkeypatch):
    """A runbook passing the mode through an empty variable must not read 0."""
    monkeypatch.setattr(filer.sys, "argv", ["file_map.py"])
    assert filer.main() == 2


def test_an_EDITED_node_identity_is_refused_not_silently_skipped(
        tmp_path, monkeypatch, map_doc, capsys):
    """A node marker carries the declared IDENTITY, so an edit re-files or stops.

    Keyed on `f"subject:{ref}"`, an edit to the declaration (a renamed Subject, a
    corrected Point content) left the marker unchanged: the resume made ZERO calls
    and printed `all nodes and edges written` with exit 0 while the graph kept the
    OLD value (measured). The same silent skip the edge markers were hardened
    against, one arm over (#13913 review round 9).

    Mutant: `item_marker` back to `f"{section}:{item['ref']}"` — run 2 exits 0
    with zero calls, which is the silent skip this refuses.
    """
    state_path = tmp_path / "state.json"
    monkeypatch.setattr(filer, "STATE_PATH", state_path)
    monkeypatch.setenv("TORTOISE_MCP_API_KEY", "not-a-real-key")
    monkeypatch.setattr(filer, "Mcp", _RecordingMcp)
    _RecordingMcp.instances.clear()
    assert filer.write_map(map_doc) == 0
    capsys.readouterr()

    subject = map_doc["subjects"][0]
    renamed = dict(subject, name=subject["name"] + " (renamed)")
    assert filer.item_marker("subjects", renamed) != filer.item_marker("subjects", subject)
    edited = dict(map_doc, subjects=[renamed if s is subject else s
                                     for s in map_doc["subjects"]])

    assert filer.write_map(edited) == 2, "the edited node must be refused, not skipped"
    err = capsys.readouterr().err
    assert "NODES" in err and "never updates or deletes" in err, err
    assert _RecordingMcp.instances[-1].calls == [], (
        "the refused run wrote through the door instead of stopping at the guard")


def test_the_ORPHAN_remedy_warns_that_re_marking_an_operator_duplicates_it(
        tmp_path, monkeypatch, map_doc, capsys):
    """The printed remedy must not mint the duplicate it is meant to prevent.

    "Remove those entries from state.json and re-run" is correct for a structural
    edge and WRONG for an operator: `tortoise_create_operator` has no existence
    check, so the re-run writes a SECOND operator over a pair that already carries
    one (#10849) — measured on a direction correction: one extra
    `tortoise_create_operator` call, exit 0. The remedy now says the existing
    operator must be deleted first.

    Mutant: the bare "remove those entries (KEEP the ids) and re-run" text — the
    operator warning assertion reds.
    """
    state_path = tmp_path / "state.json"
    monkeypatch.setattr(filer, "STATE_PATH", state_path)
    monkeypatch.setenv("TORTOISE_MCP_API_KEY", "not-a-real-key")
    monkeypatch.setattr(filer, "Mcp", _RecordingMcp)
    _RecordingMcp.instances.clear()
    assert filer.write_map(map_doc) == 0
    capsys.readouterr()

    op = next(e for e in map_doc["edges"] if "op" in e)
    flipped = dict(op, direction="<-" if op.get("direction") != "<-" else "->")
    edited = dict(map_doc, edges=[flipped if e is op else e for e in map_doc["edges"]])

    assert filer.write_map(edited) == 2
    err = capsys.readouterr().err
    assert "OPERATOR" in err, err
    assert "SECOND operator" in err and "never deletes" in err, err
def test_a_WIDE_or_REVERSED_operator_over_a_declared_pair_is_a_duplicate(map_doc):
    """A duplicate is a second operator PRICING a declared pair — whatever shape.

    Measured pre-fix (#13913 review round 9): an extra `A -> [B, C]` operator over
    a declared `A -> B`, and an extra REVERSED `B -> A`, both read `101/101`,
    `dup_ops=[]`, exit 0 — while `EP._priced_pairs` charges `(A, B)` twice, the
    #10849 harm this census exists to catch.

    Mutant: count holders only when the slots are exactly `{0: one, 1: one}` and
    in declared order — both cases vanish.
    """
    by_source: dict[tuple, list[dict]] = {}
    for e in map_doc["edges"]:
        if "op" in e:
            by_source.setdefault((e["from"], e["op"], e.get("direction")), []).append(e)
    pair = next(edges for edges in by_source.values() if len(edges) >= 2)
    first, second = pair[0], pair[1]
    # A declared pair that some OTHER operator also happens to price.
    declared = next(e for e in map_doc["edges"]
                    if "op" in e and e["from"] != first["from"])
    idents = filer.identity_by_ref(map_doc)
    src, dst = idents[declared["from"]], idents[declared["to"]]
    edge_rows, op_rows = _edge_rows(map_doc)

    wide = [*op_rows,
            ["opWIDE", declared["op"], 0, filer.direction_of(declared), *_row_of(src)],
            ["opWIDE", declared["op"], 1, filer.direction_of(declared), *_row_of(dst)],
            ["opWIDE", declared["op"], 1, filer.direction_of(declared), *_row_of(idents[second["to"]])]]
    assert filer.duplicate_operators(map_doc, wide), "a wide operator priced the pair twice"
    assert filer.verdict(map_doc, _node_rows(map_doc), edge_rows, wide) == 1

    reversed_op = [*op_rows,
                   ["opREV", declared["op"], 0, filer.direction_of(declared), *_row_of(dst)],
                   ["opREV", declared["op"], 1, filer.direction_of(declared), *_row_of(src)]]
    assert filer.duplicate_operators(map_doc, reversed_op), "a reversed operator was invisible"


def test_an_UNCLASSIFIED_exception_never_retires_the_intent(tmp_path, monkeypatch, map_doc, capsys):
    """The default arm is UNKNOWN, not a refusal.

    Only a `Refusal` — a door that answered no — may un-record a non-idempotent
    intent. An exception nobody classified may have arrived after the server
    acted, and re-issuing that write is the one unrecoverable error here
    (#13913 review round 9).

    Mutant: `except Exception:` on the refusal arm — the marker is retired and
    the resume re-issues the operator.
    """
    state = tmp_path / "state.json"

    class _Weird(_StubMcp):
        def __init__(self, *_a, **_k) -> None:
            self._n = 0
            self._inner = _RealMcp("not-a-real-key")

            def _post(body: dict):
                tool = (body.get("params") or {}).get("name")
                if tool == "tortoise_create_operator":
                    raise ZeroDivisionError("an exception nobody classified")
                self._n += 1
                return (200, {"jsonrpc": "2.0", "id": body.get("id"), "result": {
                    "structuredContent": {"id": f"{tool}:{self._n}"}}}, {})

            self._inner._post = _post

        def call(self, tool: str, args: dict, **kw) -> dict:
            return self._inner.call(tool, args, **kw)

    monkeypatch.setattr(filer, "STATE_PATH", state)
    monkeypatch.setenv("TORTOISE_MCP_API_KEY", "not-a-real-key")
    monkeypatch.setattr(filer, "Mcp", _Weird)
    assert filer.write_map(map_doc) == 1
    capsys.readouterr()

    recorded = json.loads(state.read_text())
    ops = {filer.edge_marker(e) for e in map_doc["edges"] if "op" in e}
    assert ops & set(recorded["written"]), "the intent was retired by an unknown error"
