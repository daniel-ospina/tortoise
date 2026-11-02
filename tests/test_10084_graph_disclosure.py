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
so `content[0]` and `structured_content` stay byte-identical. That is what keeps
every declared `outputSchema` intact, including the `x-fastmcp-wrap-result`
array envelope of a `-> list[dict]` write tool, which a dict field WOULD violate
(the failure class #8482 exists to repair). No tool, parameter or SDK method is
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
    """Why the extra block is appended rather than merged into the payload: the
    tool's own `structured_content` is what a conforming client validates against
    the advertised `outputSchema`, and the array-envelope tools would reject a
    dict field (#8482). `content[0]` is the payload; the disclosure follows it."""
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

    refusal = ToolResult(content=[], structured_content=None, is_error=True)
    assert ms._disclose_graph_resolution(
        refusal, "tortoise_create_point", "g_other") is refusal
    # ...and the same result DOES get one when it is not an error, so the guard
    # above is the discriminating half.
    ok = ToolResult(content=[], structured_content=None, is_error=False)
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
    """`graph="default"` is the documented no-op. It must disclose the same
    landing the omitted form does — while staying distinguishable as NAMED,
    because the caller did name it."""
    with _workspace():
        result = await ms.mcp.call_tool(
            PROBE, {"graph": "default", "note": "hi"})
    assert probe == [{"graph_id": None, "namespace": None}], probe
    assert _disclosure(result) == {"graph": "default", "requested": "default",
                                   "omitted": False, "source": "argument"}


# ── 4. the honest arm: a graph-bound credential's omission is NOT the default ──


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
    """The overlap measured against the registry, pinned: the excluded writers
    are exactly the names the disclosure must not fire on."""
    writers = {n for n in ms.WRITE_TOOL_NAMES
               if n in mcp_auth.GRAPH_ARGUMENT_EXCLUDED}
    assert writers, "the overlap this guard exists for went empty — re-derive it"
    from fastmcp.tools import ToolResult

    ok = ToolResult(content=[], structured_content=None, is_error=False)
    for name in sorted(writers):
        assert ms._disclose_graph_resolution(ok, name, None) is ok, name


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
    # the `x-fastmcp-wrap-result` shape `tortoise_delete_entity` (a dry_run
    # WRITE tool) advertises: `{"result": {...}, "x-fastmcp-wrap-result": true}`
    ({"result": {"dry_run": True, "token": "t"}}, True),
    # ...and the shapes that must NOT read as a preview
    ({"result": True}, False),
    ({"result": [{"dry_run": True}]}, False),
    ({"dry_run": "true"}, False),
    ({"dry_run": 1}, False),
    ({}, False),
])
def test_the_receipt_marker_is_read_in_both_envelope_shapes(payload, expected):
    """`tortoise_delete_entity` is BOTH a `dry_run` tool and an array/wrap-envelope
    tool; an unconfirmed delete sets no `dry_run` argument at all. The receipt
    branch is the only one that can label either, so both shapes are pinned —
    with the negatives that make it discriminating (`is True`, not truthiness)."""
    from fastmcp.tools import ToolResult

    result = ToolResult(content=[], structured_content=payload, is_error=False)
    assert ms._payload_says_preview(result) is expected, payload


@pytest.mark.asyncio
async def test_an_unconfirmed_delete_shape_is_labelled_a_preview(monkeypatch):
    """`tortoise_delete` with no `confirmation` writes nothing and sets NO
    `dry_run` argument — it marks its own receipt instead. That receipt marker is
    the only thing that can label it, and without it the same nothing-happened
    call would read as a completed write."""
    monkeypatch.setattr(ms, "WRITE_TOOL_NAMES", frozenset({PROBE}))

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

    ok = ToolResult(content=[], structured_content=None, is_error=False)
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
    criterion, so it would not catch a new selector-taking write tool.
    """
    import inspect

    selectors = {"graph_id", "graph_name", "namespace"}
    offenders = []
    for name in sorted(ms.WRITE_TOOL_NAMES - mcp_auth.GRAPH_ARGUMENT_EXCLUDED):
        handler = getattr(ms, name, None)
        if handler is None:
            continue
        try:
            params = set(inspect.signature(handler).parameters)
        except (TypeError, ValueError):  # pragma: no cover - non-callable
            continue
        if params & selectors:
            offenders.append((name, sorted(params & selectors)))
    assert offenders == [], (
        "these write tools name their own graph and must be added to "
        f"GRAPH_ARGUMENT_EXCLUDED: {offenders}")


def test_an_error_result_is_not_disclosed():
    from fastmcp.tools import ToolResult

    err = ToolResult(content=[], structured_content=None, is_error=True)
    assert ms._disclose_graph_resolution(err, "tortoise_create_point", None) is err
