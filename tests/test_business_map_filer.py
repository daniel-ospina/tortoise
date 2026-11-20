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
    foreign_nodes = [["", f"foreign-{i}", ""] for i in range(30)]
    foreign_edges = [["IMPL", "", f"foreign-{i}", "", "", f"foreign-{i + 1}", ""]
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


def _row_for(doc: dict, ref: str) -> list[str]:
    """The identity row `verify()` reads back for one declared ref.

    Derived from the same authority the writer uses: an Event is keyed on the
    `name` its door writes, not on `content` (which the Event door takes as a
    prop, not as a parameter).
    """
    item = filer.doc_by_ref(doc, ref)
    if any(e["ref"] == ref for e in doc["events"]):
        prop, value = "name", filer.event_name(item)
    elif "url" in item:
        prop, value = "url", item["url"]
    elif "content" in item:
        prop, value = "content", item["content"]
    else:
        prop, value = "name", item["name"]
    row = ["", "", ""]  # `filer.IDENTITY_PROPS` order
    row[filer.IDENTITY_PROPS.index(prop)] = value
    return row


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
    names = {value for prop, value in filer.map_identities(map_doc) if prop == "name"}
    event_names = {filer.event_name(e) for e in map_doc["events"]}
    assert event_names <= names, sorted(event_names - names)
    contents = {value for prop, value in filer.map_identities(map_doc) if prop == "content"}
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
    clashing["objects"][0]["name"] = filer.event_name(map_doc["events"][0])
    errs = filer.validate(clashing)
    assert any("share the identity" in e for e in errs), errs


def test_the_readback_verdict_is_identity_scoped_not_a_count(map_doc):
    """The fail-open the review measured: 71 nodes anywhere, none of the map.

    Mutant: `return 0 if got >= expected * 0.9 else 1` over a whole-graph count.
    """
    unrelated = [["", f"unrelated-{i}", ""] for i in range(71)]
    assert filer.verdict(map_doc, unrelated, [], []) == 1


def test_the_readback_verdict_refuses_a_map_with_no_edges(map_doc):
    """Every node filed and zero edges is not a filed map.

    Mutant: a verdict that counts nodes only — `verify()`'s docstring says edges
    are "the difference between filed and findable", so this is the arm it owes.
    """
    assert filer.verdict(map_doc, _node_rows(map_doc), [], []) == 1


def _edge_rows(doc: dict) -> tuple[list[list], list[list]]:
    """The read-back rows a correctly-filed map would produce.

    Structural edges: `(type, from-identity…, to-identity…)`. Operator edges: an
    operator NODE whose typed edges reach both endpoints — grouped per operator,
    mirroring `presence()` and the writer's own shape (`MERGE (o)-[:IMPL]->(s)`).
    """
    structural: list[list] = []
    operators: list[list] = []
    op_index: dict[str, list] = {}
    for e in doc["edges"]:
        a, b = _row_for(doc, e["from"]), _row_for(doc, e["to"])
        if "op" in e:
            nid = op_index.setdefault(e["from"] + "|" + e["to"], [f"op{len(op_index)}"])
            operators.append([nid[0], e["op"], *a])
            operators.append([nid[0], e["op"], *b])
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
    with pytest.raises(RuntimeError, match="carried no tool result"):
        mcp.call("tortoise_create_edge", {})


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
    first_op = next(i for i, e in enumerate(map_doc["edges"]) if "op" in e)

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
                    seen["op"] = f"edge:{first_op}" in written
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
