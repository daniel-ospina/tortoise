"""#10084 — DEFAULT-GRAPH-WITH-DISCLOSURE, at the dispatch seam.

The owner ruling this file pins (2026-11-02, on #10084):

    a write that names no graph resolves to the default graph, AND that
    resolution is DISCLOSED to the caller rather than being silent.

Two halves, and the second one is the point. The RESOLUTION already existed:
`plan_graph_target(None)` returns `None` and `mcp_auth._get_org_sdk` opens
`namespace=<org>`, i.e. the workspace default graph — so a graph-free write has
always landed on the default. What did not exist is any way for the caller to
KNOW that. `PointResponse` carries `id, content, kind, created_at`; nothing in a
write result named the graph. On the ChatGPT venue the graph-free form is the
ONLY form the host will deliver (a write carrying `graph` is refused client-side,
#10084's production measurement), so the silent-default branch is the one a real
user of that venue is permanently on — reading a success with no statement about
where the data went.

Why this is not a surface change, and why the test proves it STRUCTURALLY rather
than by promise: the disclosure rides the two channels `_warn_retired_result`
already established — an extra `TextContent` appended LAST and a `_meta` entry —
so `content[0]` and `structured_content` stay byte-identical — untouched BY
CONSTRUCTION, which is why no declared `outputSchema` is even consulted (the one
write tool that declares a wrap envelope is `tortoise_delete_entity`,
`result: object | boolean`; a merged key would in fact validate, but it would
change the tool's own answer, and #8482 is about the `result` VALUE's type, not
about extra keys). No tool, parameter or SDK method is
added, renamed or removed.

And what the tests refuse to allow: the disclosure must never widen the isolation
check. A `graph` naming a foreign/unreachable graph is still refused with the
shipped `No graph '<id>' is reachable from this workspace` shape, before any tool
runs, and gets NO disclosure — a refusal that also reported a landing graph would
be claiming an outcome that did not happen.
"""
from __future__ import annotations

import contextlib

import pytest
from fastmcp.exceptions import ToolError
from fastmcp.tools import FunctionTool
from mcp.types import TextContent

from tortoise import mcp_auth
from tortoise import mcp_server as ms

ORG_A = "org_a"
ORG_B = "org_b"


def _graph(gid, name, *, kind="custom", namespace=None, status="active"):
    return {
        "graph_id": gid,
        "org_id": ORG_A,
        "name": name,
        "kind": kind,
        "namespace": namespace or f"org_{ORG_A}_{gid}",
        "status": status,
    }


DEFAULT = _graph("default", "default", kind="default", namespace=f"org_{ORG_A}")
FUND_A = _graph("g_fund_a", "Fund A / Research")
FUND_B = _graph("g_fund_b", "Fund B / Research")
DELETED = _graph("g_gone", "Gone", status="deleted")
OTHER_TENANT = {
    "graph_id": "g_other",
    "org_id": ORG_B,
    "name": "Other tenant graph",
    "kind": "custom",
    "namespace": "org_org_b_x",
    "status": "active",
}

OWN_ROWS = [DEFAULT, FUND_A, FUND_B, DELETED]

PROBE = "_graph_disclosure_probe"


# ── the harness: a real write tool name, driven through the real seam ──────
#
# `mcp.call_tool` IS `_wrapped_call_tool` (installed at the single dispatch
# point), so these tests cross the same seam, the same graph resolver and the
# same wait bound a hosted `tools/call` crosses. The registered probe records
# the graph scope the TOOL BODY actually saw — which is what "landed here"
# means — instead of trusting the disclosure to describe itself.


@pytest.fixture
def probe(monkeypatch):
    """A probe registered as a WRITE tool, with the scope it observed."""
    seen: list[dict] = []
    monkeypatch.setattr(ms, "WRITE_TOOL_NAMES", frozenset({PROBE}))
    monkeypatch.setattr(mcp_auth, "reachable_graph_rows", lambda org_id: OWN_ROWS)

    def _body(note: str = "") -> dict:
        seen.append({
            "graph_id": mcp_auth._current_graph_id.get(),
            "namespace": mcp_auth._current_graph_namespace.get(),
        })
        return {"id": "pt_1", "note": note}

    ms.mcp.add_tool(FunctionTool.from_function(_body, name=PROBE,
                                               description=PROBE))
    try:
        yield seen
    finally:
        with contextlib.suppress(Exception):
            ms.mcp.local_provider.remove_tool(PROBE)


@contextlib.contextmanager
def _workspace(org_id=ORG_A, graph_id=None, namespace=None):
    tokens = [mcp_auth._current_org_id.set(org_id),
              mcp_auth._current_graph_id.set(graph_id),
              mcp_auth._current_graph_namespace.set(namespace)]
    try:
        yield
    finally:
        mcp_auth._current_graph_namespace.reset(tokens[2])
        mcp_auth._current_graph_id.reset(tokens[1])
        mcp_auth._current_org_id.reset(tokens[0])


def _text(result) -> str:
    return "\n".join(getattr(block, "text", "") for block in (result.content or []))


def _disclosure(result) -> dict:
    return ((result.meta or {}).get("tortoise") or {})["graph_resolution"]


# ── 1. the ruled shape: omission lands on the default AND says so ──────────


@pytest.mark.asyncio
async def test_a_write_naming_no_graph_lands_on_the_default_and_says_so(probe):
    """THE test the ruling asks for: the write lands in the default graph, and
    the REPLY names it. Both halves asserted, because the write landing correctly
    was already true on `main` — the disclosure is the change."""
    with _workspace():
        result = await ms.mcp.call_tool(PROBE, {"note": "hi"})

    # half 1 — where it actually landed, read from the tool body's own scope.
    assert probe == [{"graph_id": None, "namespace": None}], probe
    # half 2 — the reply discloses it.
    disclosure = _disclosure(result)
    assert disclosure == {"graph": "default", "requested": None,
                          "omitted": True, "source": "workspace-default"}
    assert "'default'" in _text(result), _text(result)
    assert "its target is this workspace's default graph" in _text(result)
    assert "named no `graph`" in _text(result)


@pytest.mark.asyncio
async def test_the_disclosure_is_the_LAST_content_block_and_the_payload_is_untouched(probe):
    """Why the extra block is appended rather than merged into the payload:
    `structured_content` is the tool's OWN answer (a caller unwraps `result`), so
    a sibling key would change data the tool declared and every consumer would
    have to learn it. Appending leaves `content[0]` and `structured_content`
    untouched by construction. `content[0]` is the payload; the disclosure
    follows it."""
    with _workspace():
        result = await ms.mcp.call_tool(PROBE, {"note": "hi"})

    assert "its target is this workspace's default graph" not in result.content[0].text, (
        "the disclosure was placed BEFORE the payload, so payload-reading callers "
        "no longer read the payload first")
    assert "its target is this workspace's default graph" in result.content[-1].text
    assert result.structured_content == {"id": "pt_1", "note": "hi"}, (
        f"the structured payload changed: {result.structured_content!r}")
    assert result.is_error is False


# ── 2. the isolation check is NOT weakened by disclosure ──────────────────


@pytest.mark.asyncio
async def test_a_write_naming_an_unreachable_graph_is_refused_before_any_tool_runs(probe):
    """Disclosure must not become a soft landing. A foreign/unreachable graph is
    refused with the SHIPPED wording (`mcp_auth.py:490-491`), before any tool
    runs, and the refusal is a `ToolError` — not "any exception", which would
    accept a crash that never reached the resolver."""
    with _workspace(), pytest.raises(ToolError) as excinfo:
        await ms.mcp.call_tool(PROBE, {"graph": "g_other", "note": "hi"})
    message = str(excinfo.value)
    assert "No graph 'g_other' is reachable from this workspace" in message, message
    # the refusal keeps its CLASSIFIER (an argument error, not an exec error),
    # so the refusal's identity is pinned and not only its prose.
    assert getattr(excinfo.value.__cause__, "code", None) == "invalid_graph"
    # the refusal names what CAN be reached (it teaches) and confirms nothing
    # about another tenant's id.
    assert "org_org_b_x" not in message, message
    assert probe == [], "the tool body ran for a refused graph"


def test_a_refusal_result_carries_no_disclosure():
    """The other half of "refused, and disclosed nothing": a refusal that DID
    return a result object (the transport wait bound, `_safe`-answered failures)
    must not be stamped with a landing that may not have happened.

    Driven with a REAL write name on purpose — with the probe's name the
    function would return at its write-set check and this assertion would pass
    for a reason that has nothing to do with the `is_error` guard.
    """
    from fastmcp.tools import ToolResult

    refusal = ToolResult(
        content=[TextContent(type="text", text='{"error": "nope"}')],
        structured_content=None, is_error=True)
    assert ms._disclose_graph_resolution(
        refusal, "tortoise_create_point", "g_other") is refusal
    # ...and the same result DOES get one when it is not an error, so the guard
    # above is the discriminating half.
    ok = ToolResult(
        content=[TextContent(type="text", text='{"id": "pt_1"}')],
        structured_content=None, is_error=False)
    with _workspace():
        disclosed = ms._disclose_graph_resolution(
            ok, "tortoise_create_point", "g_other")
    assert disclosed is not ok, "the write-set/error gate short-circuited both ways"


@pytest.mark.asyncio
async def test_a_soft_deleted_graph_is_refused_the_same_way(probe):
    with _workspace(), pytest.raises(ToolError) as excinfo:
        await ms.mcp.call_tool(PROBE, {"graph": "g_gone", "note": "hi"})
    assert "No graph 'g_gone' is reachable from this workspace" in str(excinfo.value)
    assert probe == []


# ── 3. a named, reachable graph goes where it says — and says so ──────────


@pytest.mark.asyncio
async def test_a_write_naming_a_reachable_graph_lands_there_and_says_so(probe):
    with _workspace():
        result = await ms.mcp.call_tool(
            PROBE, {"graph": "g_fund_b", "note": "hi"})

    assert probe == [{"graph_id": "g_fund_b",
                      "namespace": f"org_{ORG_A}_g_fund_b"}], probe
    assert _disclosure(result) == {"graph": "g_fund_b",
                                   "requested": "g_fund_b",
                                   "omitted": False,
                                   "source": "argument"}
    assert "the graph you named ('g_fund_b')" in _text(result)


@pytest.mark.asyncio
async def test_the_redundant_default_ref_reports_the_default_it_names(probe):
    """`graph="default"` is the documented no-op: the resolver short-circuits it
    BEFORE any lane check, so it resolves to exactly what omission resolves to.
    It must therefore disclose the SAME landing the omitted form does (only
    `requested`/`omitted` differ) — giving the two equivalent forms opposite
    disclosures is the one thing this feature exists to prevent."""
    with _workspace():
        result = await ms.mcp.call_tool(
            PROBE, {"graph": "default", "note": "hi"})
    assert probe == [{"graph_id": None, "namespace": None}], probe
    assert _disclosure(result) == {"graph": "default", "requested": "default",
                                   "omitted": False,
                                   "source": "workspace-default"}
    assert "the same target as omitting it" in _text(result), _text(result)
    assert "named no `graph`" not in _text(result), (
        "the reply denied an argument the caller actually sent")


@pytest.mark.asyncio
@pytest.mark.parametrize("org", [None, mcp_auth.SELFHOST_ORG_ID])
async def test_the_default_ref_on_a_no_workspace_lane_still_names_no_graph(probe, org):
    """The named-no-op and the omitted form are the SAME target, so on a lane with
    no hosted workspace they must disclose the same thing: `server-configured`
    with NO id. Echoing the caller's spelling here would name a graph id that does
    not exist on that surface — and would contradict the omitted form of the same
    call one line away."""
    tokens = [mcp_auth._current_org_id.set(org),
              mcp_auth._current_graph_id.set(None)]
    try:
        result = await ms.mcp.call_tool(
            PROBE, {"graph": "default", "note": "hi"})
    finally:
        mcp_auth._current_graph_id.reset(tokens[1])
        mcp_auth._current_org_id.reset(tokens[0])

    disclosure = _disclosure(result)
    assert disclosure["source"] == "server-configured", (org, disclosure)
    assert disclosure["graph"] is None, disclosure
    assert disclosure["requested"] == "default"
    assert disclosure["omitted"] is False
    assert "default graph" not in _text(result), (
        "a default-graph id was presented as the target on a surface that has none")
    assert "the same target as omitting it" in _text(result)


# ── 4. the honest arm: a graph-bound credential's omission is NOT the default ──


def test_a_credential_bound_to_the_default_graph_is_not_called_its_own_graph():
    """A credential BOUND TO THE DEFAULT GRAPH lands on the workspace default —
    so labelling it `credential-graph` produced the self-contradiction "its own
    graph ('default') — not the workspace default" in a served sentence
    (#10084 review P3). The classification is by WHERE IT LANDS.

    `plan_graph_target` contemplates this state explicitly (`mcp_auth.py:533-536`:
    a bound id equal to the default is a no-op, not a retarget)."""
    with _workspace(graph_id=mcp_auth._DEFAULT_GRAPH_ID):
        info = ms._graph_disclosure(None)
    assert info["source"] == "workspace-default", info
    assert info["graph"] == mcp_auth._DEFAULT_GRAPH_ID, info
    message = ms._graph_disclosure_message(info)
    assert "this workspace's default graph" in message, message
    assert "not the workspace default" not in message, message


@pytest.mark.asyncio
async def test_a_bound_credentials_omission_discloses_its_own_graph(probe):
    """`mcp_auth._get_org_sdk` opens a graph-bound key's OWN graph when the
    argument is omitted. Disclosing "default" here would be the exact wrong-graph
    lie this feature exists to remove, so the two arms are separate facts."""
    with _workspace(graph_id="g_fund_a", namespace=f"org_{ORG_A}_g_fund_a"):
        result = await ms.mcp.call_tool(PROBE, {"note": "hi"})

    assert probe == [{"graph_id": "g_fund_a",
                      "namespace": f"org_{ORG_A}_g_fund_a"}], probe
    disclosure = _disclosure(result)
    assert disclosure["graph"] == "g_fund_a"
    assert disclosure["source"] == "credential-graph"
    assert disclosure["omitted"] is True
    assert "not the workspace default" in _text(result)


# ── 4b. the arms the review found missing ────────────────────────────────


@pytest.mark.asyncio
async def test_a_tool_that_does_not_target_a_graph_gets_no_disclosure(probe, monkeypatch):
    """`GRAPH_ARGUMENT_EXCLUDED` is the registry's own declaration that a tool
    "targets the WORKSPACE or names its graph explicitly" — and several of them
    are WRITES (`tortoise_org_create`, `tortoise_graph_set_recording`, the
    onboarding family). Disclosing the credential's graph on those would assert a
    target the call never had: the wrong-graph claim, emitted by us."""
    monkeypatch.setattr(mcp_auth, "GRAPH_ARGUMENT_EXCLUDED", frozenset({PROBE}))
    with _workspace():
        result = await ms.mcp.call_tool(PROBE, {"note": "hi"})
    assert (result.meta or {}).get("tortoise") is None, result.meta


def test_every_served_excluded_writer_is_skipped_by_name():
    """The excluded tools that write NO graph are skipped, and the split is
    measured against the registry rather than assumed.

    `GRAPH_ARGUMENT_EXCLUDED` says "takes no routing argument", which is NOT
    "touches no graph": five of them write the workspace DEFAULT graph and are
    disclosed instead (review P2). So the skip list is the difference.
    """
    writers = {n for n in ms.WRITE_TOOL_NAMES
               if n in mcp_auth.GRAPH_ARGUMENT_EXCLUDED}
    assert writers, "the overlap this guard exists for went empty — re-derive it"
    skipped = writers - mcp_auth.GRAPH_ARGUMENT_EXCLUDED_DEFAULT_TARGET
    assert skipped, "every excluded writer became a graph writer — re-derive it"
    from fastmcp.tools import ToolResult

    ok = ToolResult(content=[TextContent(type="text", text='{"id": "pt_1"}')],
               structured_content=None, is_error=False)
    for name in sorted(skipped):
        assert ms._disclose_graph_resolution(ok, name, None) is ok, name


# A placeholder argument set that makes each default-target writer's own
# CONDITION true (only `tortoise_manage_sources` has one — and its condition is
# the PAIR, not just the action: only a GitHub `enable` starts an index job that
# writes graph data).
_DEFAULT_TARGET_ARGUMENTS = {
    "tortoise_manage_sources": {"action": "enable", "source": "github_issues"},
}


def test_the_excluded_writers_that_write_the_default_graph_are_disclosed():
    """The five excluded WRITERS that write the org DEFAULT graph must disclose
    it. Leaving them silent left the #10084 defect — a write landing on a graph
    the reply never names — in place on every tool except the one the issue
    happened to measure (review P2)."""
    from fastmcp.tools import ToolResult

    ok = ToolResult(content=[TextContent(type="text", text='{"id": "pt_1"}')],
                    structured_content=None, is_error=False)
    for name in sorted(mcp_auth.GRAPH_ARGUMENT_EXCLUDED_DEFAULT_TARGET):
        args = _DEFAULT_TARGET_ARGUMENTS.get(name)
        with _workspace():
            out = ms._disclose_graph_resolution(ok, name, None, args)
        info = ((out.meta or {}).get("tortoise") or {})["graph_resolution"]
        assert info["source"] == "workspace-default", (name, info)
        assert info["excluded"] is True, (name, info)
        # The prose must not offer `graph` as a way to re-target a tool that
        # refuses it (`_take_graph_argument` raises on every excluded name).
        text = _text(out)
        assert "takes no `graph` argument" in text, (name, text)
        assert "pass `graph`" not in text, (name, text)


def test_the_manage_sources_actions_that_write_no_graph_get_no_disclosure():
    """`tortoise_manage_sources` is excluded-and-disclosed only for the ONE
    branch that writes graph DATA: `enable` on a GitHub source, which starts the
    index job (`_run_indexing` / `_run_docs_indexing`) that projects into the
    default graph.

    Every other mutating call writes a CONTROL-PLANE flag through
    `_update_onboarding_state` — an `_ALLOWED_STATE_KEYS` key routed to the org's
    onboarding `jsonb` — and `agent_sessions` is *literally the same write*
    `tortoise_onboarding_session_recording` makes, which the registry keeps
    SILENT for "flips a control-plane flag". The round-12 seam disclosed all of
    them (`action != "list"`) and emitted "its target is this workspace's
    default graph" over a config toggle — a target the call never had, which is
    the wrong-graph claim this feature exists to remove (review P2).

    `list` is a READ: its target is not a change to the caller's data, and the
    prose says "this write"."""
    from fastmcp.tools import ToolResult

    ok = ToolResult(content=[TextContent(type="text", text='{"sources": []}')],
                    structured_content=None, is_error=False)
    silent = [
        {"action": "list"},
        {"action": "LIST"},
        {"action": " list "},
        # GitHub `disable`: ONLY the per-source enable-intent flag is written
        # (`_set_source_state` :5841-5844 — no index-job branch).
        {"action": "disable", "source": "github_issues"},
        {"action": "disable", "source": "github_documents"},
        # `agent_sessions`, either direction: the ``session_recording`` /
        # ``capture_revised`` control-plane write, never a graph write.
        {"action": "enable", "source": "agent_sessions"},
        {"action": "disable", "source": "agent_sessions"},
        # A malformed pair answers `_SafeError` before the seam's ``ok`` gate, so
        # it is not reachable — pinned here so the gate cannot be widened by a
        # default-to-True.
        {"action": "enable"},
        {"action": "enable", "source": "nope"},
    ]
    for args in silent:
        with _workspace():
            assert ms._disclose_graph_resolution(
                ok, "tortoise_manage_sources", None, args) is ok, args
    # …and the ONE branch that writes the graph IS disclosed, so the pin cannot
    # pass by the gate being dead.
    for source in ("github_issues", "github_documents"):
        with _workspace():
            out = ms._disclose_graph_resolution(
                ok, "tortoise_manage_sources", None,
                {"action": "enable", "source": source})
        assert out is not ok, source
        assert _disclosure(out)["source"] == "workspace-default"


def test_the_manage_sources_gate_reads_the_index_job_marker_not_a_source_list():
    """The gate derives "does this source index into the graph" from
    `_SOURCE_ROSTER`'s own `connection` marker, so a fourth source added there
    cannot silently acquire a graph disclosure (or lose one)."""
    github = {s for s, spec in ms._SOURCE_ROSTER.items()
              if spec.get("connection") == "github"}
    assert github == {"github_issues", "github_documents"}, github
    for source in ms._SOURCE_ROSTER:
        assert ms._manage_sources_call_indexes_into_the_default_graph(
            {"action": "enable", "source": source}) is (source in github), source
    # `disable` never indexes, on any source.
    for source in ms._SOURCE_ROSTER:
        assert ms._manage_sources_call_indexes_into_the_default_graph(
            {"action": "disable", "source": source}) is False, source


def test_every_disclosed_excluded_writer_refuses_a_graph_bound_key():
    """The invariant the `workspace-default` arm RESTS on, derived from the
    served handlers: every excluded-and-disclosed writer rejects a graph-bound
    key outright (`_reject_graph_bound_mcp_org_surface`), so it can never be on
    the `credential-graph` arm — which is what makes the disclosure true rather
    than merely plausible."""
    import inspect

    for name in sorted(mcp_auth.GRAPH_ARGUMENT_EXCLUDED_DEFAULT_TARGET):
        assert name in ms.WRITE_TOOL_NAMES, f"{name} is not a write tool"
        assert name in mcp_auth.GRAPH_ARGUMENT_EXCLUDED, (
            f"{name} advertises `graph`; it would belong in neither set")
        handler = getattr(ms, name, None)
        assert handler is not None, name
        src = inspect.getsource(handler)
        assert "_reject_graph_bound_mcp_org_surface(" in src, (
            f"{name} accepts a graph-bound key, so its target is NOT always the "
            "workspace default and this disclosure would be a wrong-graph claim")
        params = set(inspect.signature(handler).parameters)
        assert not (params & ms.GRAPH_SELECTOR_PARAMS), (
            f"{name} takes its own graph selector: {sorted(params)}")


def test_an_excluded_writer_on_a_server_configured_transport_is_not_offered_graph():
    """The `server-configured` arm (stdio, or the self-host daemon's
    `SELFHOST_ORG_ID`) must use the same "takes no `graph` argument" wording the
    workspace arm uses.

    That arm is reachable for EVERY excluded default-target writer: on stdio
    `_current_org_id` is unset and the self-host daemon sets it to
    `SELFHOST_ORG_ID`, which are exactly the two values the arm's predicate keys
    on. Its fall-through sentence was "this write **named no `graph`**" — an
    implication that one COULD have been named, served for a tool whose own
    `_take_graph_argument` RAISES if one is sent. A model reading that can be led
    to send an argument the tool refuses (review P3)."""
    from fastmcp.tools import ToolResult

    ok = ToolResult(content=[TextContent(type="text", text='{"id": "pt_1"}')],
                    structured_content=None, is_error=False)
    for org in (None, mcp_auth.SELFHOST_ORG_ID):
        for name in sorted(mcp_auth.GRAPH_ARGUMENT_EXCLUDED_DEFAULT_TARGET):
            args = _DEFAULT_TARGET_ARGUMENTS.get(name)
            with _workspace(org_id=org):
                out = ms._disclose_graph_resolution(ok, name, None, args)
            assert out is not ok, (org, name)
            info = _disclosure(out)
            assert info["source"] == "server-configured", (org, name, info)
            assert info["excluded"] is True, (org, name, info)
            text = _text(out)
            assert "takes no `graph` argument" in text, (org, name, text)
            assert "named no `graph`" not in text, (
                f"{name} on {org!r} is told it 'named no `graph`', which offers "
                f"an argument the tool refuses: {text}")


def test_a_non_excluded_writer_on_a_server_configured_transport_still_says_omitted():
    """The complement, so the P3 fix cannot be "delete the sentence": a tool
    that DOES advertise `graph` must keep the omission wording on the same arm,
    because omission is exactly what happened there."""
    with _workspace(org_id=None):
        info = ms._graph_disclosure(None)
    assert info["source"] == "server-configured"
    text = ms._graph_disclosure_message(info)
    assert "named no `graph`" in text, text
    assert "takes no `graph` argument" not in text, text


@pytest.mark.asyncio
async def test_a_dry_run_is_disclosed_as_a_preview(monkeypatch):
    """Seven served write tools take `dry_run`, and `tortoise_delete`'s default
    FIRST call changes nothing. The disclosure must not read as a completed
    write, or an agent believes a preview executed.

    `_DRY_RUN_TOOLS` is pinned to the PROBE deliberately: it is registry-derived,
    so without this the argument half of `_is_preview` is inert in every dispatch
    test (the probe is not a registry row) and a mutation of `is True` back to
    `bool(...)` would change no outcome.
    """
    seen: list = []
    monkeypatch.setattr(ms, "WRITE_TOOL_NAMES", frozenset({PROBE}))
    monkeypatch.setattr(ms, "_DRY_RUN_TOOLS", frozenset({PROBE}))

    def _preview(dry_run: bool = False) -> dict:
        seen.append(dry_run)
        return {"dry_run": dry_run}

    ms.mcp.add_tool(FunctionTool.from_function(_preview, name=PROBE,
                                               description=PROBE))
    try:
        with _workspace():
            result = await ms.mcp.call_tool(PROBE, {"dry_run": True})
            written = await ms.mcp.call_tool(PROBE, {"dry_run": False})
            # The raw PRE-validation dict trap: a JSON string the handler coerces
            # to False must not be reported as a preview over an executed write.
            coerced = await ms.mcp.call_tool(PROBE, {"dry_run": "false"})
    finally:
        with contextlib.suppress(Exception):
            ms.mcp.local_provider.remove_tool(PROBE)

    assert seen == [True, False, False]
    assert _disclosure(result)["preview"] is True
    assert "preview" in _text(result).lower()
    assert "preview" not in _text(written).lower()
    assert "preview" not in _disclosure(written)
    assert "preview" not in _disclosure(coerced), (
        'a string "false" was read as a preview while the write executed')


@pytest.mark.parametrize(("payload", "expected"), [
    # the bare shape a dict-returning preview tool produces
    ({"dry_run": True}, True),
    # the `x-fastmcp-wrap-result` shape `tortoise_delete_entity` (the ONE write
    # tool that declares a wrap envelope: `result: object | boolean`) advertises
    ({"result": {"dry_run": True, "token": "t"}}, True),
    # ...and the shapes that must NOT read as a preview
    ({"result": True}, False),
    ({"result": [{"dry_run": True}]}, False),
    ({"dry_run": "true"}, False),
    ({"dry_run": 1}, False),
    ({}, False),
])
def test_the_receipt_marker_is_read_in_both_envelope_shapes(payload, expected):
    """`tortoise_delete_entity` advertises `x-fastmcp-wrap-result` with
    `result: object | boolean`; an unconfirmed delete sets no `dry_run` argument
    at all. The receipt branch is the only one that can label either, so both
    shapes are pinned — with the negatives that make it discriminating (`is True`,
    not truthiness). The `result`-as-ARRAY case below is a future-proofing
    negative, not an advertised write shape."""
    from fastmcp.tools import ToolResult

    result = ToolResult(content=[], structured_content=payload, is_error=False)
    assert ms._payload_says_preview(result) is expected, payload


@pytest.mark.asyncio
async def test_a_caller_prop_named_dry_run_cannot_forge_a_preview(monkeypatch):
    """MEASURED DEFECT this guards: `dry_run` is NOT a server-managed prop, so
    `tortoise_create_point(..., props={"dry_run": True})` echoes the key back in
    the node it created — and reading that payload ungated made the reply say
    "Nothing was written: this call was a preview" over an EXECUTED write (the
    #3926/#8135 class: key presence cannot distinguish a caller's prop from a
    server marker). The declared-set gate is what makes it unforgeable.
    """
    monkeypatch.setattr(ms, "WRITE_TOOL_NAMES", frozenset({PROBE}))

    def _echoes_props(props: dict | None = None) -> dict:
        # Exactly what `tortoise_create_point` returns: the caller's props come
        # back inside the created node.
        return {"id": "pt_1", "status": "live", **(props or {})}

    ms.mcp.add_tool(FunctionTool.from_function(_echoes_props, name=PROBE,
                                               description=PROBE))
    try:
        with _workspace():
            forged = await ms.mcp.call_tool(
                PROBE, {"props": {"dry_run": True}})
    finally:
        with contextlib.suppress(Exception):
            ms.mcp.local_provider.remove_tool(PROBE)

    # the payload really does carry the caller's prop …
    assert "dry_run" in _text(forged), _text(forged)
    # … and it must not be read as a preview marker.
    assert "preview" not in _disclosure(forged), _disclosure(forged)
    assert "Nothing was written" not in _text(forged)


@pytest.mark.asyncio
async def test_an_unconfirmed_delete_shape_is_labelled_a_preview(monkeypatch):
    """`tortoise_delete` with no `confirmation` writes nothing and sets NO
    `dry_run` argument — it marks its own receipt instead. That receipt marker is
    the only thing that can label it, and without it the same nothing-happened
    call would read as a completed write."""
    monkeypatch.setattr(ms, "WRITE_TOOL_NAMES", frozenset({PROBE}))
    # The receipt branch is gated on the DECLARED preview set (a tool that does
    # not declare `dry_run` can be forged through its own echoed `props`), so the
    # probe must be declared for the receipt-only path to be exercised at all.
    monkeypatch.setattr(ms, "_DRY_RUN_TOOLS", frozenset({PROBE}))

    def _receipt(dry_run_arg: bool = False) -> dict:
        return {"dry_run": True, "token": "tok_1", "preview": True}

    ms.mcp.add_tool(FunctionTool.from_function(_receipt, name=PROBE,
                                               description=PROBE))
    try:
        with _workspace():
            result = await ms.mcp.call_tool(PROBE, {})
    finally:
        with contextlib.suppress(Exception):
            ms.mcp.local_provider.remove_tool(PROBE)

    assert _disclosure(result)["preview"] is True
    assert "Nothing was written" in _text(result)


@pytest.mark.asyncio
async def test_a_transport_with_no_workspace_does_not_invent_a_default_graph(probe):
    """No hosted workspace — `_current_org_id` unset (stdio) OR set to
    `SELFHOST_ORG_ID` (the self-host daemon's `TransportModeMiddleware`). The
    resolver's own predicate covers both (`plan_graph_target` refuses any named
    `graph` on `org_id in (None, SELFHOST_ORG_ID)`), so the disclosure must not
    tell a self-host caller to name a graph that lane would refuse."""
    for org in (None, mcp_auth.SELFHOST_ORG_ID):
        tokens = [mcp_auth._current_org_id.set(org),
                  mcp_auth._current_graph_id.set(None)]
        try:
            result = await ms.mcp.call_tool(PROBE, {"note": "hi"})
        finally:
            mcp_auth._current_graph_id.reset(tokens[1])
            mcp_auth._current_org_id.reset(tokens[0])

        disclosure = _disclosure(result)
        assert disclosure["source"] == "server-configured", (org, disclosure)
        assert disclosure["graph"] is None, disclosure
        assert "no workspace" in _text(result)
        assert "'default'" not in _text(result), (
            "a graph id that does not exist on that surface was named")
        assert "pass `graph`" not in _text(result), (
            "the reply told a caller to name a graph this lane's resolver refuses")


# ── 5. scope: writes only, and never on a failure ─────────────────────────


@pytest.mark.asyncio
async def test_a_read_tool_gets_no_disclosure(probe, monkeypatch):
    """The ruling is about WRITES. A read's target is not a change to the
    caller's data, and `WRITE_TOOL_NAMES` (the registry's derived write set) is
    what decides — so this cannot drift from the surface."""
    monkeypatch.setattr(ms, "WRITE_TOOL_NAMES", frozenset())
    with _workspace():
        result = await ms.mcp.call_tool(PROBE, {"note": "hi"})
    assert (result.meta or {}).get("tortoise") is None, result.meta


def test_every_served_write_tool_keeps_its_payload_at_content_zero():
    """The append convention, pinned over the REAL served write names.

    `content[0]` is the payload and the disclosure is `content[-1]` for EVERY
    write tool this seam discloses on — not just the in-process probe. This is
    the property three consumers in the tree depend on (they were joining every
    block and `json.loads`-ing the result, which broke: see
    `tests/test_mcp_client.py`, `tests/test_cursor_mcp_exit_evidence.py` and
    `client/tortoise_client/cli.py`).
    """
    from fastmcp.tools import ToolResult

    disclosed = sorted(ms.WRITE_TOOL_NAMES - mcp_auth.GRAPH_ARGUMENT_EXCLUDED)
    assert len(disclosed) > 20, "the served write set collapsed — re-derive this pin"
    for name in disclosed:
        payload = ToolResult(
            content=[type("B", (), {"text": '{"id": "pt_1", "status": "live"}'})()],
            structured_content={"id": "pt_1", "status": "live"},
            is_error=False,
        )
        with _workspace():
            out = ms._disclose_graph_resolution(payload, name, None)
        assert out.content[0].text == payload.content[0].text, name
        assert out.structured_content == payload.structured_content, name
        assert len(out.content) == 2, name
        assert "Graph disclosure:" in out.content[-1].text, name


def test_the_real_write_set_covers_the_tool_this_issue_is_about():
    """A guard on the set the seam reads: the tool #10084 measured must be in it,
    or the disclosure would silently not fire for the exact call in the report."""
    assert "tortoise_create_point" in ms.WRITE_TOOL_NAMES
    assert "tortoise_create_point" not in mcp_auth.GRAPH_ARGUMENT_EXCLUDED


# ── 6. the disclosure unit, for the arms a full dispatch cannot reach ─────


def test_the_disclosure_reports_the_arms_distinctly():
    with _workspace():
        omitted = ms._graph_disclosure(None)
    assert omitted == {"graph": "default", "requested": None,
                       "omitted": True, "source": "workspace-default"}
    named = ms._graph_disclosure("g_fund_b")
    assert named == {"graph": "g_fund_b", "requested": "g_fund_b",
                     "omitted": False, "source": "argument"}
    # a non-string ref never reaches a dispatch (the resolver refuses it first),
    # so it discloses nothing rather than inventing a name.
    assert ms._graph_disclosure(7) is None


def test_the_named_arm_reports_the_ref_as_given_not_a_canonical_id():
    """The resolver accepts an id, a `stable_id`, a NAMESPACE or a name; the
    canonicalised id lives inside the dispatch task (#9762) and is not readable
    from the seam. So the field carries the caller's own ref — which is what a
    caller can check its request against — and must not be presented as an id."""
    for ref in ("g_fund_b", f"org_{ORG_A}_g_fund_b", "Fund B / Research"):
        assert ms._graph_disclosure(ref)["graph"] == ref


@pytest.mark.asyncio
async def test_a_safe_answered_failure_is_not_disclosed(monkeypatch):
    """A `_safe`-answered failure is NOT stamped with the graph it "targeted".

    ⚠️ WHICH GUARD FIRES, stated after a review round measured it (this change
    moves the attribution, not the behaviour): the seam's ``if status == "ok":``
    gate is the guard that RUNS — a refused write is classified
    ``exec_error``/``safe_error`` before that line, so
    ``_disclose_graph_resolution`` is never entered for it. Since #11153 the
    helper's own ``is_error`` check would ALSO return the result untouched, so it
    is a second layer, not the first.

    Consequence, said plainly rather than implied: this test no longer kills the
    mutant that deletes the seam gate (with it replaced by ``if True:`` the
    helper's ``is_error`` check still returns early and this test stays green). It
    pins the OUTCOME (no disclosure on a refusal) and the seam gate is pinned by
    reading the code, not by this test. Do not delete either guard on the strength
    of this test.
    """
    monkeypatch.setattr(ms, "WRITE_TOOL_NAMES", frozenset({PROBE}))

    def _failing() -> dict:
        # A REAL `_safe` failure: it records the failure in the dispatch holder
        # the seam's classification reads (`_dispatch_safe_failures`), which is
        # why an arbitrary `{"error": ...}` dict is NOT a substitute.
        return ms._safe(_boom)

    ms.mcp.add_tool(FunctionTool.from_function(_failing, name=PROBE,
                                               description=PROBE))
    tokens = [mcp_auth._transport_mode.set("http")]
    try:
        with _workspace():
            result = await ms.mcp.call_tool(PROBE, {})
    finally:
        mcp_auth._transport_mode.reset(tokens[0])
        with contextlib.suppress(Exception):
            ms.mcp.local_provider.remove_tool(PROBE)

    assert result.is_error is True, (
        "#11153: a `_safe` failure IS an error result; the seam's `status == \"ok\"` "
        "gate is what keeps the graph disclosure off it (the helper's own `is_error` "
        "check is the second layer), and this line used to assert the opposite verdict")
    assert "boom" in _text(result), _text(result)
    assert (result.meta or {}).get("tortoise") is None, (
        "a failed write was stamped with the graph it 'targeted'")


def _boom() -> None:
    raise RuntimeError("boom")


def test_a_bare_return_is_not_wrapped():
    """Belt-and-braces on a public helper: nothing to disclose ONTO means no
    new object is invented (the seam itself always sees a `ToolResult`, which is
    exactly why this can only be pinned at the unit level)."""
    sentinel = {"id": "pt_1"}
    assert ms._disclose_graph_resolution(
        sentinel, "tortoise_create_point", None) is sentinel


def test_the_excluded_writer_with_its_own_selector_is_skipped():
    """`tortoise_graph_set_recording` names its target with `graph_id`, so a
    disclosure from this seam could only disagree with the `{graph_id}` payload
    the tool itself returns. Skipped — and the registry, not prose, is the
    authority for that set."""
    from fastmcp.tools import ToolResult

    ok = ToolResult(content=[TextContent(type="text", text='{"id": "pt_1"}')],
               structured_content=None, is_error=False)
    assert "tortoise_graph_set_recording" in mcp_auth.GRAPH_ARGUMENT_EXCLUDED
    assert ms._disclose_graph_resolution(
        ok, "tortoise_graph_set_recording", None) is ok


def test_no_disclosed_write_tool_carries_its_own_graph_selector():
    """The invariant the disclosure's truthfulness RESTS on, derived rather than
    assumed: a write tool that takes its own graph selector (`graph_id` /
    `graph_name` / `namespace`) writes somewhere this seam cannot see, so it must
    be in `GRAPH_ARGUMENT_EXCLUDED` or the disclosure would name the credential's
    graph over a write that went elsewhere — the wrong-graph claim this feature
    exists to remove, emitted by us.

    The registry's own guard derives `GRAPH_ARGUMENT_EXCLUDED` from
    `_reject_graph_bound_mcp_org_surface` CALLERS, which is a different
    criterion, so it would not catch a new selector-taking write tool. The set
    checked here is every tool the disclosure can actually fire on, which since
    #10084's P2 includes the excluded writers that write the default graph.
    """
    import inspect

    disclosed = ((ms.WRITE_TOOL_NAMES - mcp_auth.GRAPH_ARGUMENT_EXCLUDED)
                 | (ms.WRITE_TOOL_NAMES
                    & mcp_auth.GRAPH_ARGUMENT_EXCLUDED_DEFAULT_TARGET))
    offenders = []
    for name in sorted(disclosed):
        handler = getattr(ms, name, None)
        if handler is None:
            continue
        try:
            params = set(inspect.signature(handler).parameters)
        except (TypeError, ValueError):  # pragma: no cover - non-callable
            continue
        if params & ms.GRAPH_SELECTOR_PARAMS:
            offenders.append((name, sorted(params & ms.GRAPH_SELECTOR_PARAMS)))
    assert offenders == [], (
        "these write tools name their own graph and must be added to "
        f"GRAPH_ARGUMENT_EXCLUDED: {offenders}")


def test_an_error_result_is_not_disclosed():
    from fastmcp.tools import ToolResult

    err = ToolResult(content=[TextContent(type="text", text='{"error": "nope"}')],
               structured_content=None, is_error=True)
    assert ms._disclose_graph_resolution(err, "tortoise_create_point", None) is err
