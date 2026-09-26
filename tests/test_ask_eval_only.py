"""#3849 — the ask lane is EVAL-ONLY: surface-removal contract (N1-N7).

Owner-directed surface curation (plan objectives 5 and 9): ``ask`` is not an
intended product surface. It must not appear in the MCP surface (registry →
``tools/list`` / ``tools/call``), in the SDK, or on either REST surface; it
survives as the eval-only home ``tortoise/ask_lane.py``.

Every assertion below EXECUTES real code — a registry read, a real FastMCP
registration, a real mounted-HTTP ``tools/list``, a real app route table, a
real lane invocation. None of them greps a source file to prove removal (the
source-text variant is exactly what the old R14 guard did, and it can be
satisfied by a comment).

⛔ The literal MCP tool name cannot appear in this tree (#3849 §8 makes its
absence a ``git grep`` gate), so the name is COMPOSED as ``_ASK_TOOL`` below.
The assertions are about the name's absence; spelling it out would defeat the
gate they verify.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise.sdk import TortoiseSDK

_REPO_ROOT = Path(__file__).resolve().parent.parent

# Composed, never literal — see the module docstring.
_ASK_TOOL = "tortoise" + "_ask"
_ASK_ROUTE = "/v1/" + "ask"
# The removed exposure-gate env var + function: named only via composition,
# because #3849 §8 makes their literal absence a `git grep` gate. N5 must
# still SET the flag and probe for the function to prove the gate is gone —
# which is exactly why the names are composed here.
_EXPOSURE_FLAG = "TORTOISE_ENABLE" + "_ASK"
_EXPOSURE_FN = "ask_exposure" + "_enabled"

_CANONICAL_13 = {
    "answer", "abstained", "question_type", "question_date", "evidence",
    "context_tokens", "model", "provider", "route", "cost_estimate_usd",
    "duration_ms", "retrieval_degraded", "retrieved_session_ids",
}


@pytest.fixture(autouse=True)
def _local_graph_lane(monkeypatch):
    """The ambient shell may carry ``TORTOISE_API_URL`` (fleet env); the
    eval lane is a LOCAL-graph lane and refuses when it is set. Isolation,
    not a skip — the lane call below really executes.

    The process-global ask-reader cache is ALSO reset around every test
    (the sibling lane suites — test_ask_sdk / test_d3_session_identity /
    test_w4_why_enrichment — do the same): ``run_ask_lane`` caches its
    reader under a namespace key, and a leaked entry makes a later
    ``monkeypatch.setattr(al, "_default_ask_reader_factory", ...)`` a
    no-op — an order-dependent FALSE PASS rather than a failure."""
    from tortoise.ask_lane import _reset_ask_reader_cache_for_tests

    monkeypatch.delenv("TORTOISE_API_URL", raising=False)
    _reset_ask_reader_cache_for_tests()
    yield
    _reset_ask_reader_cache_for_tests()


class _CapturingReader:
    """Reader stub capturing the system + user prompts actually sent."""

    model = "stub-reader"
    provider = "stub"
    route = "stub"
    last_route = "stub"
    last_finish_reason = "stop"
    last_completion_tokens = 3

    def __init__(self, reply: str = "stub answer") -> None:
        self.reply = reply
        self.calls = 0
        self.last_system: str | None = None
        self.last_user: str | None = None

    def complete(self, *, system: str, user: str) -> str:
        self.calls += 1
        self.last_system = system
        self.last_user = user
        return self.reply

    def close(self) -> None:
        pass


def _seed(sdk: TortoiseSDK) -> None:
    from tests.test_ask_sdk import _seed_event_graph
    _seed_event_graph(sdk, [
        {"content": "the gym schedule is Monday and Wednesday",
         "eventId": "ev1", "session_date": "2026-08-01", "speaker": "user"},
    ])


# ── N1: no public ask surface; the eval-only entry still executes ──────────

def test_n1_sdk_exposes_no_ask_entry_point():
    """RED mutation: re-add ``def ask(...)`` to ``TortoiseSDK`` (a half-done
    rename) → the ``hasattr`` assertions fail."""
    import tortoise.sdk as sdk_mod

    assert not hasattr(TortoiseSDK, "ask"), \
        "TortoiseSDK.ask must not resolve (#3849: removed from the SDK)"
    assert not hasattr(TortoiseSDK, "ask_assembled"), \
        "TortoiseSDK.ask_assembled must not resolve (#3849)"
    assert not hasattr(TortoiseSDK, "_post_ask"), \
        "the hosted ask client was removed with the REST surface (#3849)"
    # No dead module-level exports either: removal means it does not resolve.
    assert not hasattr(sdk_mod, "ASK_SDK_TIMEOUT_S")
    assert not hasattr(sdk_mod, "_ask_reader_cache")
    assert not hasattr(sdk_mod, "_LockedReader")
    # Anti-over-removal: the hit annotator is KEPT (assembly.py uses it).
    assert hasattr(TortoiseSDK, "annotate_ask_hits")


def test_n1_eval_lane_entry_is_callable_and_returns_the_13_field_shape(monkeypatch):
    """RED mutation: delete/rename ``run_ask_lane`` in ask_lane.py → ImportError
    / AttributeError here (the eval lane must SURVIVE the surface removal)."""
    import tortoise.ask_lane as al
    from tests.test_ask_sdk import _install_fake, _new_sdk

    assert callable(al.run_ask_lane)
    assert callable(al.run_ask_assembled)
    sdk = _new_sdk()
    try:
        _seed(sdk)
        _install_fake(sdk, monkeypatch)
        result = al.run_ask_lane(sdk, "what is the gym schedule?",
                                 question_date="2026-08-29")
        assert set(result) == _CANONICAL_13
        assert result["answer"]
    finally:
        sdk.close()


# ── N2: the MCP surface registers and lists no ask tool ───────────────────

def test_n2_registry_registration_has_no_ask_tool():
    """RED mutation: restore the ask ToolDefinition in ``TOOL_REGISTRY`` →
    ``register_all`` registers it → the absence assertion fails."""
    from fastmcp import FastMCP

    from tortoise.tool_registry import TOOL_REGISTRY, FastMCPAdapter

    mcp = FastMCP("t3849")
    FastMCPAdapter(mcp).register_all(
        TOOL_REGISTRY, {t.name: (lambda: None) for t in TOOL_REGISTRY})
    registered = {getattr(c, "name", None)
                  for c in mcp._local_provider._components.values()}
    assert len(registered) == len(TOOL_REGISTRY), \
        "the probe must register EVERY registry entry (else absence is vacuous)"
    assert _ASK_TOOL not in registered


def test_n2_mcp_tools_list_serves_no_ask_tool(monkeypatch):
    """The MCP PROTOCOL listing (real mounted HTTP app) serves no ask tool.

    RED mutation: restoring the registry entry ALONE is not enough — the
    module-bottom ``register_all`` skips an entry with no handler, so
    ``tools/list`` only regains it when the ``mcp_server`` handler is
    restored too (N2's direct ``register_all`` assertion catches the
    registry-only restore)."""
    from tests.test_mcp_server_auth_modes import _mcp_post, _mounted_test_client
    from tortoise.mcp_server import create_http_app

    app = create_http_app(allowed_origins=["http://localhost:8000"],
                          auth_mode="none")
    with _mounted_test_client(app) as tc:
        r = _mcp_post(tc, {"jsonrpc": "2.0", "id": 1, "method": "tools/list",
                           "params": {}})
        data_line = next((ln[6:] for ln in r.text.splitlines()
                          if ln.startswith("data: ")), r.text)
        names = [t["name"] for t in
                 json.loads(data_line).get("result", {}).get("tools", [])]
    assert len(names) >= 75, f"default surface shrank to {len(names)}"
    assert _ASK_TOOL not in names


# ── N3: no ask curation group ─────────────────────────────────────────────

def test_n3_group_catalogue_has_no_ask_group():
    """RED mutation: re-add the ``"ask"`` group entry to ``GROUP_BY_NAME`` →
    ``tool_groups()`` gains the key and ``tools_by_group("ask")`` is non-empty."""
    from tortoise.tool_registry import tool_groups, tools_by_group

    assert "ask" not in tool_groups(), "the ask curation group was removed (#3849)"
    assert tools_by_group("ask") == []


# ── N4/N5: no REST route and no exposure flag (subprocess, import-time) ────

_PROBE = '''
import json, os
os.environ["@@EXPOSURE_FLAG@@"] = "1"   # the removed flag — must change nothing
import tortoise.transport as tr
from tortoise.hosted_api import app as hosted_app
from tortoise.selfhost_api import router
print(json.dumps({
    "has_flag_fn": hasattr(tr, "@@EXPOSURE_FN@@"),
    "hosted_routes": [getattr(r, "path", None) for r in hosted_app.routes],
    "selfhost_routes": [getattr(r, "path", None) for r in router.routes],
}))
'''.replace("@@EXPOSURE_FLAG@@", _EXPOSURE_FLAG).replace("@@EXPOSURE_FN@@", _EXPOSURE_FN)


def _probe() -> dict:
    proc = subprocess.run([sys.executable, "-c", _PROBE],
                          capture_output=True, text=True, cwd=str(_REPO_ROOT))
    assert proc.returncode == 0, proc.stderr[-3000:]
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_n4_no_ask_rest_route_on_either_surface(monkeypatch):
    """RED mutation: restore ``_register_ask_route()`` (hosted) or the
    ``@router.post("/ask")`` handler (self-host) → the route appears.

    Run in a SUBPROCESS with the removed exposure flag set to its ON value,
    so the import-time registration the old gate performed would have fired.
    The predicate
    matches the FULL path AND any ``/ask`` suffix — a self-host router carries
    the ``/v1`` prefix, so a bare ``"/ask"`` membership test would be
    tautologically true (verified by mutation)."""
    probe = _probe()

    def _has_ask(paths: list) -> bool:
        return any(p == _ASK_ROUTE or (p or "").endswith("/ask") for p in paths)

    assert not _has_ask(probe["hosted_routes"]), probe["hosted_routes"]
    assert not _has_ask(probe["selfhost_routes"]), probe["selfhost_routes"]


def test_n5_exposure_flag_no_longer_exists(monkeypatch):
    """RED mutation: re-introduce the ask exposure gate fn in transport.py →
    the ``hasattr`` half fails. The BEHAVIOURAL half (below) is what stops a
    pure rename passing: with the flag set, the surfaced behaviour is
    unchanged — no route, still 404."""
    probe = _probe()
    assert probe["has_flag_fn"] is False, \
        "the #2013 exposure gate was removed with the surface (#3849)"


def test_n5_flag_on_changes_nothing_behaviourally(monkeypatch, tmp_path):
    """Flag set to the old ON value: the self-host REST surface still 404s."""
    from tests.test_selfhost_rest import _client_for_env

    monkeypatch.setenv(_EXPOSURE_FLAG, "1")
    tc = _client_for_env(monkeypatch, tmp_path)
    with tc:
        r = tc.post(_ASK_ROUTE, json={"question": "anything"})
    assert r.status_code == 404, r.status_code


# ── N6: the eval measures the SHIPPED reader (single-sourced prompt) ───────

def test_n6_eval_reexports_the_shipped_prompt_objects():
    """RED mutation: copy the prompt text into the eval lane (a literal in
    ``tools/longmem_eval/reader.py``) → the identity assertion fails."""
    import tools.longmem_eval.reader as eval_reader
    import tortoise.reader as reader

    assert eval_reader._SYSTEM_PROMPT is reader._SYSTEM_PROMPT, \
        "the eval must re-export the shipped prompt object, not a copy"
    assert eval_reader.system_prompt_for is reader.system_prompt_for


def test_n6_lane_call_sends_the_shipped_system_prompt(monkeypatch):
    """The executed half: a real lane call sends the shipped reader prompt.

    RED mutation: inline a prompt literal into the lane's reader call → the
    captured system prompt no longer equals ``system_prompt_for(qtype)``."""
    import tortoise.ask_lane as al
    import tortoise.reader as reader
    from tests.test_ask_sdk import _new_sdk

    sdk = _new_sdk()
    cap = _CapturingReader()
    al._reset_ask_reader_cache_for_tests()
    try:
        _seed(sdk)
        monkeypatch.setattr(al, "_default_ask_reader_factory", lambda: cap)
        result = al.run_ask_lane(sdk, "what is the gym schedule?",
                                 question_date="2026-08-29")
        assert cap.calls == 1
        assert cap.last_system == reader.system_prompt_for(result["question_type"])
        assert reader._SYSTEM_PROMPT in (cap.last_system or "")
    finally:
        sdk.close()
        al._reset_ask_reader_cache_for_tests()


# ── N7: the lane's hosted-mode refusal (the one semantic change in the move) ─

def test_n7_lane_refuses_a_hosted_client(monkeypatch):
    """RED mutation: restore the removed ``_post_ask`` delegation (``return
    sdk._post_ask(...)``) in ``run_ask_lane`` → no raise here.

    The hosted branch is the ONE semantic change in the pipeline move: the old
    ``TortoiseSDK.ask`` delegated to ``_post_ask`` when ``TORTOISE_API_URL``
    was set; the eval-only lane refuses instead, because the hosted ``/v1/ask``
    route it delegated to no longer exists. ``run_ask_assembled``'s refusal is
    pinned in ``tests/test_assembly_sdk.py::test_hosted_delegated_client_raises``;
    this pins the ``run_ask_lane`` half (the autouse fixture deletes the var, so
    without this test nothing reaches the branch)."""
    import tortoise.ask_lane as al
    from tests.test_ask_sdk import _new_sdk
    from tortoise.exceptions import AskRetrievalUnavailable

    sdk = _new_sdk()
    try:
        monkeypatch.setenv("TORTOISE_API_URL", "https://example.test")
        with pytest.raises(AskRetrievalUnavailable):
            al.run_ask_lane(sdk, "what is the gym schedule?")
    finally:
        monkeypatch.delenv("TORTOISE_API_URL", raising=False)
        sdk.close()
