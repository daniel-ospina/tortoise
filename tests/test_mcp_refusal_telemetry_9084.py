"""#9084 — a tool-LEVEL refusal must not be recorded `status="ok"`.

WHY THIS FILE EXISTS
--------------------
#8135 taught the `mcp_tool_call` classifier to recognise a failure `_safe`
SWALLOWED, by recording the `_SafeError` at construction and matching it by
value at the seam. That covers every refusal produced by `_safe`.

It does not cover the other producer: a tool body that answers a caller-side
refusal with a **plain dict literal** it built itself —

    if mode not in _RECALL_MODES:
        return {"mode": mode, "error": "recall mode ... not recognized"}

Those read ``status="ok"``, ``error_kind=None`` in the very series the #888/
#3834 friction evidence and the activation scorecard are measured from, so a
refused call was indistinguishable from a successful one. Measured on the
pre-fix tree for `tortoise_recall`'s unrecognised mode (the probe #8135's own
body listed and could not classify).

WHY THE FIX IS IN THE PRODUCERS, NOT IN THE SEAM
------------------------------------------------
The seam cannot test ``"error" in payload``: a *successful* write returns the
created node's own property dict, and a user prop may be literally named
``error`` — gating on key presence re-classifies those writes as failures
(#3926, pinned in ``test_mcp_safe_error_telemetry_8135.py``). There is no
property of the PAYLOAD that separates the two, so the tool body — the one
place that knows which dict it is building — has to say so by making the
refusal an `_SafeError`. `_SafeError` is a ``dict`` subclass, so the wire shape
does not move: the caller receives the same JSON as before.

WHAT IS PINNED HERE
-------------------
1. **The class, structurally** — no SERVED tool body in `mcp_server.py`
   answers a refusal with a plain dict. "Served" is `TOOL_REGISTRY` **plus**
   `RETIRED_TOOL_REGISTRY`: a retired name is not dead code, `_RetiredToolTransform`
   resolves it and runs the ORIGINAL body ("It still answers", #3883) through
   the same dispatcher, so it emits `mcp_call` rows under the retired name.
   The walk follows helper CALLS and a `return <name>` whose value was assigned
   a refusal literal, because those are the same producer class — the shapes
   the first sweep could not see. This is the gate that keeps the converted
   sites from growing another: a new refusal written any of the old ways fails
   here rather than silently re-opening the defect.
2. **The vocabulary** — the emitter's `error_kind == "safe_error"` definition
   names BOTH producer families (`_safe`'s caught failure and the tool-body
   refusal), because #9084 widened the kind without moving the telemetry code.
3. **The behaviour, per producer** — the real tools (`tortoise_recall`,
   `tortoise_create_point`, `tortoise_query`, `tortoise_file_decision`,
   `tortoise_graph_set_recording`) AND the five helpers the first sweep could
   not reach (`_overview_section`, `_onboarding_state`, `tortoise_get`,
   `_set_source_state`, `_http_excluded_error`) answer their refusal with a
   non-``ok`` status and a named `error_kind`.
4. **No wire change** — the refusal's payload is byte-identical to the plain
   dict it replaced.
5. **No re-classification of successes** — the #3926 boundary (a success whose
   props carry an ``error`` key) still reads ``ok``, and the "recovered from a
   secondary `_safe` failure" shape still reads ``ok``.

The issue's Indicator 2 (a post-fix count of `mcp_tool_call` rows with
``status="ok"`` whose result carried an error payload, target 0) is the
production measurement of (1) + (2) over real traffic; it needs a live window
and is not assertable here. (1) is its structural equivalent: with no
plain-dict refusal producer left on the served surface, a refusal cannot reach
the series as a success.

WHAT THE GATE STILL DOES NOT SEE (named, so a green run is not over-read)
------------------------------------------------------------------------
* **Domain answers.** A payload may carry an ``error`` key because the ANSWER
  says so — `tortoise_get_operator` on a point that is not an operator reports
  that fact in the ``error`` key, and it is a SUCCESSFUL fetch (#4576 pins it
  as a plain dict). Recording it as `exec_error` would invent a failure the
  caller never got, so it is a declared exemption here, not a miss.
* **A refusal nested deeper than the seam reads.** `_returned_safe_failure`
  reads the payload and its ``result`` wrap key. `tortoise_overview()` with no
  `section` nests each section's payload one level down (`combined[sec] =
  result`), so a section's refusal is not classified. Pre-existing, and it
  never turned a failure into a DIFFERENT failure — it stays `ok`.
* **A refusal answered with a KEYLESS plain dict.** The gate matches a dict
  carrying a CONSTANT ``error`` key, and the seam classifies a keyless refusal
  only when the body returns an `_SafeError` (the exact-payload arm is
  type-agnostic; a key-presence check is what #3926 forbids). So a body that
  answers a refusal with a plain ``{"installed": False, ...}`` is invisible to
  BOTH — and no served body does it at this head: the last one,
  `tortoise_pack_install`'s rejected manifest, is converted
  (`test_pack_install_rejected_manifest_is_recorded_as_a_failure`).
  `test_the_gate_coverage_claim_is_scoped_to_what_it_actually_sees` keeps
  that boundary stated instead of letting a green run read as "closed for every
  shape".
"""
from __future__ import annotations

import ast
import contextlib
import pathlib

import pytest
from fastmcp.tools import FunctionTool

from tortoise import mcp_server as ms

# ── Harness ───────────────────────────────────────────────────────────────

MCP_SERVER = pathlib.Path(ms.__file__)


@pytest.fixture
def events(monkeypatch):
    """Capture emitted rows, and make sure no analytics write leaves the box."""
    captured: list[dict] = []

    def _capture(org_id, tool_name, status, latency_ms, error_kind,
                 result_count=None):
        captured.append({"tool_name": tool_name, "status": status,
                         "error_kind": error_kind,
                         "result_count": result_count})

    monkeypatch.setattr(ms, "_emit_mcp_tool_call_telemetry", _capture)
    return captured


@pytest.fixture
def hosted(monkeypatch):
    """A transport mode, so `_safe`'s fail-closed gate does not pre-empt the
    refusal these tests are about."""
    token = ms._transport_mode.set("http")
    yield
    ms._transport_mode.reset(token)


# ── 1. The class, structurally ────────────────────────────────────────────

#: Producers whose payload carries an ``error`` key but is NOT a refusal — a
#: DOMAIN ANSWER. `tortoise_get_operator` on a point that is not an operator
#: ANSWERED the question "is this an operator?" (with ''no''); converting it
#: would fabricate a failure, and #4576's typed-channel distinction pins it as
#: a plain dict (`tests/test_mcp_server.py::
#: test_get_operator_still_rejects_real_non_operator`). Declared here so the
#: exclusion is VISIBLE instead of being a hole in the walk.
DOMAIN_ANSWER_PRODUCERS = {
    "tortoise_get_operator": (
        "a successful fetch reporting a domain fact (`is_operator: false`), "
        "not a refusal (#4576)"
    ),
}


def _served_tool_names() -> set[str]:
    """Every name a client can call — the LIVE registry AND the retired ones.

    A retired name is served: `build_retired_tools` / `_RetiredToolTransform`
    resolve it and run the original body (that is what #3883's "It still
    answers" means), so its refusals reach the seam and emit rows exactly like
    a live tool's. Scoping the gate to `TOOL_REGISTRY` alone left ten
    plain-dict refusals inside bodies that answer day to day.
    """
    from tortoise.tool_registry import RETIRED_TOOL_REGISTRY, TOOL_REGISTRY

    return ({t.name for t in TOOL_REGISTRY}
            | {t.name for t in RETIRED_TOOL_REGISTRY})


def _is_error_dict(node: ast.AST) -> bool:
    """`{... "error": ...}` as a dict LITERAL — the pre-#9084 shape."""
    if not isinstance(node, ast.Dict):
        return False
    return any(isinstance(k, ast.Constant) and k.value == "error"
               for k in node.keys)


def _value_produces_refusal(node: ast.AST, producers: set[str]) -> bool:
    """Does this RETURN VALUE evaluate to a plain-dict refusal?

    A literal, or a CALL to a module-level function that returns one. A call to
    `_SafeError(...)` is the CONVERTED shape and is never a hit (and the class
    is not a function def, so it cannot enter `producers`).
    """
    if _is_error_dict(node):
        return True
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id in producers)


def _returns_plain_dict_refusal(fn: ast.AST, producers: set[str]):
    """(lineno) of every `return` in `fn` that yields a plain-dict refusal.

    Three shapes, all one producer class:

    * `return {"error": ...}` — the literal the first sweep converted;
    * `return _helper(...)` / a bare name this body assigned one of those to —
      `tortoise_overview` returning `_overview_section(...)`,
      `tortoise_session_capture`'s `out = {...}; return out`;
    * a helper reached transitively — `tortoise_get_entity` -> `tortoise_get`
      -> `tortoise_get_operator`.
    """
    assigned: dict[str, list[ast.AST]] = {}
    for sub in ast.walk(fn):
        if isinstance(sub, ast.Assign):
            for target in sub.targets:
                if isinstance(target, ast.Name):
                    assigned.setdefault(target.id, []).append(sub.value)
    hits = []
    for sub in ast.walk(fn):
        if not isinstance(sub, ast.Return) or sub.value is None:
            continue
        value = sub.value
        refused = _value_produces_refusal(value, producers) or (
            isinstance(value, ast.Name)
            and any(_value_produces_refusal(v, producers)
                    for v in assigned.get(value.id, ())))
        if refused:
            hits.append(sub.lineno)
    return hits


def _plain_dict_refusal_producers(funcs: dict[str, ast.AST],
                                  exempt: set[str] = frozenset()):
    """Module-level functions that can RETURN a plain-dict refusal.

    A FIXPOINT, not one call level: a refusal is forwarded one helper at a time
    — `tortoise_get_entity` (LIVE) `return tortoise_get(...)`, whose body holds
    a literal AND forwards to `tortoise_get_operator`. One level was not enough
    for exactly the survivor that mattered most.

    `exempt` names are never ADMITTED to the producer set, rather than being
    subtracted from it afterwards: an exempt DOMAIN ANSWER must not make its
    forwarders producers either, or converting the real refusals would still
    flag `tortoise_get` / `tortoise_get_entity` for relaying a domain answer.
    """
    producers: set[str] = set()
    changed = True
    while changed:
        changed = False
        for name, fn in funcs.items():
            if name in producers or name in exempt:
                continue
            if _returns_plain_dict_refusal(fn, producers):
                producers.add(name)
                changed = True
    return producers


def _plain_error_payloads(fn: ast.AST) -> list[str]:
    """The source text of every plain-dict ``error`` value `fn` returns."""
    texts = []
    for sub in ast.walk(fn):
        if isinstance(sub, ast.Return) and _is_error_dict(sub.value):
            for key, value in zip(sub.value.keys, sub.value.values,
                                  strict=True):
                if isinstance(key, ast.Constant) and key.value == "error":
                    texts.append(ast.unparse(value))
    return texts


def _gate_hits(source: str, served: set[str] | None = None):
    """(tool, lineno) of every plain-dict refusal reachable from a SERVED tool."""
    tree = ast.parse(source)
    funcs = {n.name: n for n in tree.body
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    served = _served_tool_names() if served is None else served
    # A served name whose body is NOT in this module cannot be walked, so it
    # cannot be cleared either: this layer once SKIPPED such a name silently,
    # which would let a body defined elsewhere answer a refusal unseen forever.
    unreadable = sorted(served - set(funcs))
    assert not unreadable, (
        "served tool(s) whose body is not a module-level def here, so the gate "
        f"cannot inspect them: {unreadable} — teach the walk to read that "
        "module, or name the exclusion in the module docstring")
    producers = _plain_dict_refusal_producers(funcs,
                                             exempt=set(DOMAIN_ANSWER_PRODUCERS))
    hits = []
    for tool in sorted(served - set(DOMAIN_ANSWER_PRODUCERS)):
        fn = funcs.get(tool)
        if fn is None:                      # unreachable: asserted above
            continue
        for lineno in _returns_plain_dict_refusal(fn, producers):
            hits.append((tool, lineno))
    return hits


def test_no_served_tool_body_returns_a_plain_dict_refusal():
    """The gate: a refusal must BE an `_SafeError`, not a dict that looks like
    one (#9084). Without the fix this fails with the 53 measured literals plus
    every helper-built and retired-but-served survivor.

    Scope is stated in the module docstring: this walks the SERVED surface
    (live + retired), follows helper calls and refused-name returns, and
    exempts declared DOMAIN ANSWER producers — never a refusal.
    """
    hits = _gate_hits(MCP_SERVER.read_text())

    assert hits == [], (
        "a served tool answers a refusal with a plain dict, so the dispatch is "
        "recorded status='ok' (#9084). Wrap the payload in `_SafeError({...})` "
        "— it IS a dict, so the wire shape is unchanged:\n"
        + "\n".join(f"  {MCP_SERVER.name}:{ln}  {tool}" for tool, ln in hits))


def test_the_gate_sees_the_shapes_the_first_sweep_could_not():
    """MUTATION proof that the widened walk is not decorative.

    Each replacement puts ONE converted site back the way it was at
    ``fa2876fc`` and the gate must flag it. Without these the gate could pass
    by scanning nothing while three real producer shapes stayed open — which is
    exactly what the first version of this file did.
    """
    source = MCP_SERVER.read_text()

    # (a) A refusal built into a NAME and returned by it (the seam cannot see
    #     a type it never saw, and the first gate only matched AST literals).
    by_name = source.replace(
        'out = _SafeError({"error": str(detail), "status": status})',
        'out = {"error": str(detail), "status": status}', 1)
    assert by_name != source, "the capture site moved — update this mutation"
    assert "tortoise_session_capture" in {t for t, _ in _gate_hits(by_name)}

    # (b) A refusal built by a HELPER and returned verbatim by a LIVE tool.
    by_helper = source.replace(
        'return _SafeError(\n        {"error": f"overview: unknown section {section!r}. "',
        'return (\n        {"error": f"overview: unknown section {section!r}. "', 1)
    assert by_helper != source, "the overview site moved — update this mutation"
    assert "tortoise_overview" in {t for t, _ in _gate_hits(by_helper)}

    # (c) RETIRED but SERVED — the name set the first gate excluded by
    #     construction, while the shim kept answering it.
    by_retired = source.replace(
        'return _SafeError({"error": f"get: unknown type {type!r}. "',
        'return ({"error": f"get: unknown type {type!r}. "', 1)
    assert by_retired != source, "the tortoise_get site moved — update this"
    assert "tortoise_get" in {t for t, _ in _gate_hits(by_retired)}


def test_every_declared_domain_answer_exemption_is_still_needed():
    """A stale exemption is a hole nobody can see: it silently widens the gate.

    Each declared producer must (i) still be a served name and (ii) still hold
    a plain-dict ``error`` return. Convert one and this fails, so the exemption
    is removed in the same change rather than surviving as dead cover.
    """
    served = _served_tool_names()
    tree = ast.parse(MCP_SERVER.read_text())
    funcs = {n.name: n for n in tree.body
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}

    for name in DOMAIN_ANSWER_PRODUCERS:
        assert name in served, f"{name} is no longer served — drop its exemption"
        # NOT a wholesale exemption (a whole-function exemption also covers a
        # NEW refusal added to it, and covers its forwarders). The ONE pinned
        # #4576 payload is the exemption; anything else riding on it reds here.
        texts = _plain_error_payloads(funcs[name])
        assert len(texts) == 1 and "is not an operator" in texts[0], (
            f"{name}'s plain `error` returns must be exactly the pinned #4576 "
            "DOMAIN ANSWER (`Point <id> is not an operator`); a second or "
            f"different payload is a REFUSAL riding the exemption: {texts}")


def test_the_gate_coverage_claim_is_scoped_to_what_it_actually_sees():
    """#9609 review (claim-mismatch): the gate's boundary is the SHAPE it matches,
    never "a refusal the gate cannot see is impossible to write".

    Three facts, all structural, so neither the claim nor the boundary can
    drift:

    (i)   the gate is BLIND to a refusal that carries NO ``error`` key AND is
          not an `_SafeError` — demonstrated on a synthetic body below, so the
          limit is measured rather than described. The seam classifies such a
          refusal only through the exact-payload arm, i.e. only when the
          producer returns an `_SafeError`; the last served body that answered
          one with a plain dict (`tortoise_pack_install`'s rejected manifest)
          is converted in this change, and
          `test_pack_install_rejected_manifest_is_recorded_as_a_failure` pins
          that it now reads a failure;
    (ii)  every description of the gate says so. Before this fix
          `tests/test_mcp_safe_error_telemetry_8135.py` claimed the gate's walk
          meant "a refusal the gate cannot see is not possible to write without
          failing it", which is false: a lane reading it would take a green
          gate run as coverage of shapes the gate never matches, and re-open
          #9084 in one of them;
    (iii) the OTHER limit that is live today is still there, so a green run
          cannot be read as "nothing at all is left": `tortoise_overview()`
          with no `section` nests each section's payload one level down.
    """
    funcs = {n.name: n for n in ast.parse(MCP_SERVER.read_text()).body
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}

    # (i) the limit, MEASURED: a keyless refusal return is invisible.
    synthetic = (
        "def _9084_served_fake() -> dict:\n"
        "    return {'installed': False, 'validation_errors': ['bad']}\n")
    assert _gate_hits(synthetic, served={"_9084_served_fake"}) == [], (
        "the gate now SEES a keyless refusal — a widened gate is a corpus "
        "change to re-state here and in test_mcp_safe_error_telemetry_8135.py, "
        "never a stale sentence")

    # ... and the one served body that had that shape no longer does.
    assert "tortoise_pack_install" in _served_tool_names()
    body = funcs["tortoise_pack_install"]
    keyless_refusals = [sub for sub in ast.walk(body)
                        if isinstance(sub, ast.Return)
                        and isinstance(sub.value, ast.Dict)
                        and not _is_error_dict(sub.value)
                        and any(isinstance(k, ast.Constant)
                                and k.value == "validation_errors"
                                for k in sub.value.keys)]
    assert keyless_refusals == [], (
        "tortoise_pack_install answers a rejection with a plain dict again — "
        "the row reads `ok` and this module's stated boundary is a lie")

    # (iii) the nested-refusal limit is still live (not a stale sentence).
    overview = ast.get_source_segment(MCP_SERVER.read_text(), funcs["tortoise_overview"])
    assert "combined[sec] = result" in overview, (
        "the nested-section refusal limit moved — re-derive WHAT THE GATE "
        "STILL DOES NOT SEE rather than leaving a stale shape in it")

    seam = pathlib.Path(__file__).with_name("test_mcp_safe_error_telemetry_8135.py")
    sibling = ast.parse(seam.read_text())
    claim = next(n for n in sibling.body
                 if getattr(n, "name", None)
                 == "test_a_plain_dict_payload_is_still_recorded_ok_at_the_seam")
    doc = " ".join((ast.get_docstring(claim) or "").split())

    assert "not possible to write" not in doc, (
        "the seam docstring is claiming a gate-invisible refusal cannot be "
        "written again. The gate matches one shape — a dict literal (or a "
        "helper/name resolving to one) carrying a CONSTANT `error` key")
    assert "WHAT THE GATE STILL DOES NOT SEE" in doc, (
        "the seam docstring must point at THIS module's named limits: the gate's "
        "boundary is scoped there, and only there")
    assert "tortoise_overview" in doc, (
        "the seam docstring must name a concrete shape the gate does not "
        "classify, not merely gesture at limits")
    assert "converted" in doc, (
        "the seam docstring names the KEYLESS shape (`tortoise_pack_install`), "
        "so it must also say that shape is CONVERTED — otherwise a reader "
        "takes a closed hole for a live one, which is the drift this pin exists "
        "to stop")


# ── 2. The vocabulary ──────────────────────────────────────────────────────

def _flatten(text: str) -> str:
    return " ".join(text.split())


def test_the_emitter_vocabulary_names_BOTH_producer_families():
    """#9084 widened ``error_kind == "safe_error"``; the definition must say so.

    The emitter's docstring calls itself "the single emission point" and states
    the vocabulary HERE. Before #9084 it defined the kind as "a dispatch whose
    failure `_safe` ANSWERED with a dict" — after this fix the majority of that
    family is a TOOL-BODY refusal the SDK was never called for (`_safe` never
    runs), so the old definition now describes a strictly narrower set than the
    data. A reader would derive the pre-#9084 scope from it, which is exactly
    the misreading the runbook clause exists to prevent.

    Pinned as BOTH halves plus the stale half's ABSENCE, so "fix the docstring"
    cannot regress into "rename it and move on".
    """
    flat = _flatten(ms._emit_mcp_tool_call_telemetry.__doc__ or "")

    assert "a dispatch whose failure `_safe` ANSWERED with a dict" not in flat, (
        "the emitter still defines `safe_error` as `_safe`-produced only — after "
        "#9084 the tool bodies are the other producer"
    )
    assert "TWO producers write that kind" in flat, (
        "the vocabulary must state that two producer families share "
        "`error_kind = 'safe_error'` (#8135 `_safe`, #9084 tool body)"
    )
    assert "a failure `_safe` CAUGHT" in flat, (
        "the `_safe` half of the family must still be named (#8135)"
    )
    assert "a TOOL-BODY refusal the SDK was never called for" in flat, (
        "the #9084 half of the family must be named — that is this fix's "
        "whole point"
    )


# ── 3. The behaviour, per producer ────────────────────────────────────────

@pytest.mark.asyncio
async def test_recall_bad_mode_is_recorded_as_a_failure(events, hosted):
    """#8135's own unclassifiable probe — `tortoise_recall`, unrecognised mode.

    The refusal is a tool-body branch: the SDK is never reached, so there is no
    exception for the ladder to catch and nothing `_safe` produced.
    """
    result = await ms.mcp.call_tool("tortoise_recall", {"query": "q",
                                                        "mode": "bogus"})

    payload = result.structured_content
    assert "not recognized" in payload["error"], payload
    row = events[-1]
    assert row["tool_name"] == "tortoise_recall", row
    assert row["status"] == "exec_error", row
    assert row["error_kind"] == "safe_error", row
    # #3675: a refusal returned no collection — UNKNOWN, never 0.
    assert row["result_count"] is None, row


@pytest.mark.asyncio
async def test_create_point_tag_cap_is_recorded_as_a_failure(events, hosted):
    """`ERR_QUOTA`, produced by the tool body's own batch cap (#329)."""
    from tortoise.quota import MAX_TAGS_PER_POINT

    tags = [f"t{i}" for i in range(MAX_TAGS_PER_POINT + 1)]
    result = await ms.mcp.call_tool(
        "tortoise_create_point",
        {"kind": "statement", "content": "x", "props": {"tags": tags}})

    payload = result.structured_content
    assert payload["code"] == ms.ERR_QUOTA, payload
    row = events[-1]
    assert row["status"] == "exec_error", row
    assert row["error_kind"] == "safe_error", row


@pytest.mark.asyncio
async def test_query_bad_paging_is_recorded_as_a_failure(events, hosted):
    """`tortoise_query` is a `list[dict] | dict` tool — a dict is ADMISSIBLE in
    its envelope, so #8482's repair has nothing to act on and the row used to
    read `ok`. This is the union-envelope half of the producer class.

    The refusal arrives under the wrap envelope's ``result`` (measured), so the
    read is through it — the seam's `_returned_safe_failure` reads both levels
    for exactly this reason (#8135 P1).
    """
    result = await ms.mcp.call_tool("tortoise_query", {"page": 0})

    payload = result.structured_content
    refusal = payload.get("result", payload)
    assert "page must be >= 1" in refusal["error"], payload
    row = events[-1]
    assert row["status"] == "exec_error", row
    assert row["error_kind"] == "safe_error", row


@pytest.mark.asyncio
async def test_file_decision_option_cap_is_recorded_as_a_failure(events, hosted):
    from tortoise.quota import MAX_FILE_DECISION_OPTIONS

    options = [f"o{i}" for i in range(MAX_FILE_DECISION_OPTIONS + 1)]
    result = await ms.mcp.call_tool(
        "tortoise_file_decision",
        {"options": options, "evidence": ["e"], "choice": 0})

    payload = result.structured_content
    assert payload["code"] == ms.ERR_QUOTA, payload
    row = events[-1]
    assert row["status"] == "exec_error", row
    assert row["error_kind"] == "safe_error", row


@pytest.mark.asyncio
async def test_a_refusal_carrying_an_http_status_still_reads_exec_error(
        events, hosted):
    """A refusal whose payload carries a second, unrelated key.

    `tortoise_graph_set_recording`'s scope branch answers
    ``{"error": ..., "status": 403}``. Pinned separately because the
    classification must follow the `_SafeError` TYPE: a ``status`` field is also
    what a SUCCESS carries on several tools, so a fix that keyed on it would
    re-classify real successes as failures. The org/scope contextvars are set
    here so the SCOPE branch is the one that answers (the earlier branch is the
    hosted-mode refusal, which carries no ``status``).
    """
    tok_org = ms._current_org_id.set("team-9084")
    # graphs:write so the #4170 scope gate admits the call, and NO
    # team:manage so the tool's own scope branch is what answers.
    tok_scopes = ms._current_scopes.set(["graphs:write"])
    try:
        result = await ms.mcp.call_tool("tortoise_graph_set_recording",
                                       {"recording": True})
    finally:
        ms._current_scopes.reset(tok_scopes)
        ms._current_org_id.reset(tok_org)

    payload = result.structured_content
    assert payload["status"] == 403, payload
    assert "team:manage" in payload["error"], payload
    row = events[-1]
    assert row["status"] == "exec_error", row
    assert row["error_kind"] == "safe_error", row


@pytest.mark.asyncio
async def test_overview_unknown_section_is_recorded_as_a_failure(events, hosted):
    """`tortoise_overview` delegates its section refusal to `_overview_section`.

    The sibling guard three lines above it in the SAME body was converted by
    the first sweep (`overview: section must be a string`), while the helper
    two lines below was not — so the tool answered a refusal both ways and only
    one of them was a failure row. `tortoise_overview(section="bogus")` is the
    context-free probe: no org, no graph, no SDK.
    """
    result = await ms.mcp.call_tool("tortoise_overview", {"section": "bogus"})

    payload = result.structured_content
    assert "unknown section" in payload["error"], payload
    row = events[-1]
    assert row["tool_name"] == "tortoise_overview", row
    assert row["status"] == "exec_error", row
    assert row["error_kind"] == "safe_error", row
    assert row["result_count"] is None, row


@pytest.mark.asyncio
async def test_overview_topics_without_entity_id_is_recorded_as_a_failure(
        events, hosted):
    """The helper's OTHER refusal branch — a required-argument gate."""
    result = await ms.mcp.call_tool("tortoise_overview", {"section": "topics"})

    payload = result.structured_content
    assert "requires entity_id" in payload["error"], payload
    row = events[-1]
    assert row["status"] == "exec_error", row
    assert row["error_kind"] == "safe_error", row


@pytest.mark.asyncio
async def test_onboarding_state_without_team_context_is_recorded_as_a_failure(
        events, hosted):
    """`_onboarding_state` builds the refusal; the tool returns it verbatim.

    The IDENTICAL "No team context" message was converted in six sibling tool
    bodies by the first sweep and left plain in the helper that produces it —
    so the same refusal from the same family was a failure in five tools and a
    success in the sixth (`tortoise_onboarding_state`).
    """
    token = ms._current_org_id.set(None)
    try:
        result = await ms.mcp.call_tool("tortoise_onboarding_state", {})
    finally:
        ms._current_org_id.reset(token)

    payload = result.structured_content
    assert "No team context" in payload["error"], payload
    row = events[-1]
    assert row["status"] == "exec_error", row
    assert row["error_kind"] == "safe_error", row


@pytest.mark.asyncio
async def test_get_entity_bad_type_is_recorded_as_a_failure(events, hosted):
    """A LIVE canonical tool forwarding to a RETIRED name's handler.

    `tortoise_get_entity` is the documented replacement for `tortoise_get` and
    forwards to it (`return tortoise_get(id, type=type, limit=limit)`), so a
    refusal built in a retired body reaches the advertised surface. The gate
    originally scoped retired names out — which is why this arm is pinned.
    """
    result = await ms.mcp.call_tool("tortoise_get_entity", {"type": "bogus"})

    payload = result.structured_content
    assert "'id' is required" in payload["error"], payload
    row = events[-1]
    assert row["tool_name"] == "tortoise_get_entity", row
    assert row["status"] == "exec_error", row
    assert row["error_kind"] == "safe_error", row


@pytest.mark.asyncio
async def test_http_excluded_tool_refusal_is_recorded_as_a_failure(events, hosted):
    """`_http_excluded_error()` is ONE funnel feeding six live tool bodies.

    Its `error` is a MAPPING (`{"code": ..., "message": ...}`), so the seam's
    text arm cannot classify it — it is matched by value. These are the hosted
    -transport refusals, i.e. the population the activation scorecard is
    measured on, and every one of them used to read `ok`.
    """
    result = await ms.mcp.call_tool("tortoise_index_files",
                                    {"directory": "/tmp"})

    payload = result.structured_content
    assert payload["error"]["code"] == ms.ERR_EXCLUDED, payload
    row = events[-1]
    assert row["status"] == "exec_error", row
    assert row["error_kind"] == "safe_error", row


@pytest.mark.asyncio
async def test_manage_sources_unreadable_projection_is_recorded_as_a_failure(
        events, hosted, monkeypatch):
    """`_set_source_state`'s ten refusals — reached through a LIVE tool.

    The tool's OWN two refusals were converted by the first sweep; the helper it
    returns from (`return _set_source_state(...)`) was not, so the most-
    trafficked refusal on this tool ("could not be read … nothing was changed")
    was recorded as a SUCCESS. The projection is stubbed UNAVAILABLE rather than
    absent: a missing key is the product DEFAULT, not an unreadable read.
    """
    monkeypatch.setattr(ms, "_onboarding_state",
                        lambda: {"operational": "unavailable"})
    token = ms._current_org_id.set("team-9084")
    try:
        result = await ms.mcp.call_tool(
            "tortoise_manage_sources",
            {"action": "enable", "source": "github_issues"})
    finally:
        ms._current_org_id.reset(token)

    payload = result.structured_content
    assert "could not be read" in payload["error"], payload
    row = events[-1]
    assert row["status"] == "exec_error", row
    assert row["error_kind"] == "safe_error", row


@pytest.mark.asyncio
async def test_pack_install_rejected_manifest_is_recorded_as_a_failure(
        events, hosted, monkeypatch):
    """A refusal carrying NO ``error`` key at all (`validation_errors`).

    A rejected manifest IS a refusal: the install was refused. The two sibling
    branches in the same body were converted by the first sweep; this one was
    left as a plain dict because it has no `error` key, so the dispatch was
    recorded ``status="ok"`` — the #9084 defect on a live, served tool
    (#9609 review P2). It is classified by the seam's exact-payload arm (the
    payload is recorded when the `_SafeError` is CONSTRUCTED), which is why the
    fix does not need — and must not add — a key-presence heuristic (#3926).
    """
    import tortoise.pack_manifest_store as store

    def _reject(*_a, **_kw):
        raise ValueError("packs: 'bogus' is not a known starter pack")

    # The HOSTED-only gate above the branch is a ``sys.modules`` lookup, so
    # importing the real module is enough — this test is about the refusal, not
    # deployment (and a placeholder module would break conftest's probe reset).
    import tortoise.hosted_api  # noqa: F401  — satisfies the hosted-only gate

    monkeypatch.setattr(store, "upsert_tenant_manifest", _reject)
    monkeypatch.setattr(ms, "_get_org_sdk", lambda: object())

    result = await ms.mcp.call_tool("tortoise_pack_install",
                                    {"manifest_yaml": "packs: []"})

    payload = result.structured_content
    assert payload["installed"] is False, payload
    assert payload["validation_errors"] == [
        "packs: 'bogus' is not a known starter pack"], payload
    assert "error" not in payload, payload
    row = events[-1]
    assert row["tool_name"] == "tortoise_pack_install", row
    assert row["status"] == "exec_error", row
    assert row["error_kind"] == "safe_error", row


# ── 4. No wire change ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_refusal_wire_shape_is_the_plain_dict_it_replaced(
        events, hosted):
    """`_SafeError` is a dict subclass: the caller sees the same JSON.

    Stated as an equality against the literal the body used to return, so a
    later refactor that "cleans up" the payload is caught here rather than by a
    customer parsing the wire.
    """
    result = await ms.mcp.call_tool("tortoise_recall", {"query": "q",
                                                        "mode": "bogus"})

    payload = dict(result.structured_content)      # what a client deserializes
    assert type(result.structured_content) is not ms._SafeError, (
        "the `_SafeError` TYPE must not survive to the client-facing payload "
        "(#8482's premise: FastMCP rebuilds it through "
        "`to_jsonable_python`, so the seam never sees the subclass)")
    assert payload == {"mode": "bogus",
                       "error": "recall mode 'bogus' not recognized — use "
                                "state|gaps|subgraph|custom."}


@pytest.mark.asyncio
async def test_the_constructed_refusal_is_recorded_even_through_a_spread(
        events, hosted):
    """A MUTATION guard for the mechanism the whole fix rests on.

    `_SafeError.__init__` records the payload for the dispatch in flight, and
    the seam matches it BY VALUE — including when a tool spreads the refusal
    into a wider payload (`tortoise_recall` answers `{mode, **refusal}`). A
    tool body that surfaced only the inner dict would still be caught, so the
    conversion does not depend on each site returning the payload verbatim.
    """
    def spread_tool() -> dict:
        return {"mode": "bogus", **ms._SafeError({"error": "spread refusal"})}

    ms.mcp.add_tool(FunctionTool.from_function(spread_tool, name="_9084_spread",
                                               description="_9084_spread"))
    try:
        await ms.mcp.call_tool("_9084_spread", {})
    finally:
        with contextlib.suppress(Exception):
            ms.mcp.local_provider.remove_tool("_9084_spread")

    row = events[-1]
    assert row["status"] == "exec_error", row
    assert row["error_kind"] == "safe_error", row


# ── 5. The false positives that must NOT move ─────────────────────────────

@pytest.mark.asyncio
async def test_a_successful_write_carrying_a_user_error_prop_stays_ok(
        events, hosted, monkeypatch):
    """#3926's boundary, re-measured against a REAL create tool.

    The conversion is at the refusal sites only; a successful write still
    returns the SDK's own props dict (a plain dict), so a user prop named
    ``error`` cannot be mistaken for a refusal.
    """
    class _WriteSDK:
        def create_point(self, *a, **kw):
            return {"id": "pt_1", "error": "a user prop",
                    "pointKind": "statement"}

    monkeypatch.setattr(ms, "_get_org_sdk", _WriteSDK)

    result = await ms.mcp.call_tool(
        "tortoise_create_point",
        {"kind": "statement", "content": "x",
         "props": {"error": "a user prop"}})

    assert result.structured_content["id"] == "pt_1"
    row = events[-1]
    assert row["status"] == "ok", row
    assert row["error_kind"] is None, row
