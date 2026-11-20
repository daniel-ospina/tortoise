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


def test_the_readback_verdict_is_identity_scoped_not_a_count(map_doc):
    """The fail-open the review measured: 71 nodes anywhere, none of the map.

    Mutant: `return 0 if got >= expected * 0.9 else 1` over a whole-graph count.
    """
    unrelated = [["", f"unrelated-{i}", ""] for i in range(71)]
    assert filer.verdict(map_doc, unrelated, []) == 1


def test_the_readback_verdict_refuses_a_map_with_no_edges(map_doc):
    """Every node filed and zero edges is not a filed map.

    Mutant: a verdict that counts nodes only — `verify()`'s docstring says edges
    are "the difference between filed and findable", so this is the arm it owes.
    """
    assert filer.verdict(map_doc, _node_rows(map_doc), []) == 1


def test_the_readback_verdict_passes_a_complete_map(map_doc):
    """Non-vacuity for the two refusals above: the real map reads back GREEN."""
    edge_rows = [_row_for(map_doc, e["from"]) + _row_for(map_doc, e["to"])
                 for e in map_doc["edges"]]
    assert filer.verdict(map_doc, _node_rows(map_doc), edge_rows) == 0
