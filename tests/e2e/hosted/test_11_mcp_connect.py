"""E2E-11-D — MCP server connect over the real HTTP transport.

Reconstructed case (#303). The full Streamable-HTTP client handshake against
the mounted /mcp app: initialize → tools/list (tenant-scoped tortoise_* tools
visible) → tools/call tortoise_create_point → the Point is readable through
the REST surface with the same key (write path verified end to end, SSE
framing parsed).

Negatives: no token → 401; non-tt_ token → 401 (format guard); unknown tool
call → JSON-RPC error (never a 200 with a silent no-op).
"""
from __future__ import annotations

import json
import uuid

from conftest import skip_unless_hosted_e2e

skip_unless_hosted_e2e()

_MCP_HEADERS = {"Content-Type": "application/json",
                "Accept": "application/json, text/event-stream"}


def _parse_sse_json(text: str) -> dict:
    """Parse a body that may be SSE-framed (event: message\\ndata: {...})."""
    if text.startswith("event:") or "\ndata: " in text:
        for line in text.splitlines():
            if line.startswith("data: "):
                return json.loads(line[len("data: "):])
        raise AssertionError(f"no data line in SSE body: {text[:200]!r}")
    return json.loads(text)


def _mcp(api, key: str, method: str, params: dict | None = None, rid: int = 1):
    payload = {"jsonrpc": "2.0", "id": rid, "method": method}
    if params is not None:
        payload["params"] = params
    headers = dict(_MCP_HEADERS)
    if key is not None:
        headers["Authorization"] = f"Bearer {key}"
    r = api.post("/mcp/", data=payload, headers=headers)
    return r


def test_mcp_full_handshake_and_tool_call(api, tenant_factory):
    """initialize → tools/list → tools/call creates a Point visible via REST."""
    t = tenant_factory("mcp")
    key = t["api_key"]

    r = _mcp(api, key, "initialize", {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "e2e-303", "version": "1.0"},
    })
    assert r.status == 200, f"initialize: {r.status} {r.text()}"
    init = _parse_sse_json(r.text())
    assert init.get("result", {}).get("serverInfo"), init

    r = _mcp(api, key, "tools/list", {}, rid=2)
    assert r.status == 200, f"tools/list: {r.status} {r.text()}"
    tools = _parse_sse_json(r.text()).get("result", {}).get("tools", [])
    names = {t_["name"] for t_ in tools}
    assert "tortoise_create_point" in names, f"tool missing: {sorted(names)[:20]}"

    content = f"mcp-created point (E2E-11-D) {uuid.uuid4().hex[:6]}"
    r = _mcp(api, key, "tools/call", {
        "name": "tortoise_create_point",
        "arguments": {"content": content, "kind": "statement"},
    }, rid=3)
    assert r.status == 200, f"tools/call: {r.status} {r.text()}"
    result = _parse_sse_json(r.text()).get("result", {})
    assert not result.get("isError"), f"tool call errored: {result}"

    # Cross-surface proof: the MCP write lands in the same tenant graph.
    h = {"Authorization": f"Bearer {key}"}
    points = api.get("/v1/points", headers=h).json()["points"]
    assert any(content in p.get("content", "") for p in points), \
        "MCP-created point not visible via REST"


def test_mcp_auth_negatives(api):
    r = _mcp(api, None, "initialize", {
        "protocolVersion": "2025-03-26", "capabilities": {},
        "clientInfo": {"name": "e2e", "version": "1.0"}})
    assert r.status == 401, f"no token must 401, got {r.status}"

    r = _mcp(api, "sk_wrong_prefix_abc", "initialize", {
        "protocolVersion": "2025-03-26", "capabilities": {},
        "clientInfo": {"name": "e2e", "version": "1.0"}})
    assert r.status == 401, f"non-tt_ token must 401, got {r.status}"


def test_mcp_unknown_tool_error(api, tenant_factory):
    t = tenant_factory("mcp-unknown")
    r = _mcp(api, t["api_key"], "tools/call",
             {"name": "tortoise_no_such_tool", "arguments": {}}, rid=9)
    assert r.status == 200, f"JSON-RPC errors ride a 200 envelope: {r.status}"
    body = _parse_sse_json(r.text())
    assert body.get("error") or body.get("result", {}).get("isError"), \
        f"unknown tool must produce a JSON-RPC error: {body}"
