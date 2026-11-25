"""#12364 — an MCP wrapper must declare the type its SDK method actually returns.

The defect this file pins
-------------------------
Two live MCP tools could not return ANY result. Every call failed with:

    Error calling tool '<name>': structured_content must be a dict or None.
    Got list: []. Tools should wrap non-dict values based on their output_schema.

FastMCP derives a tool's ``output_schema`` from the **wrapper's** return
annotation. When the wrapper said ``-> dict`` and the SDK method returned a
``list``, FastMCP built an ``{"type": "object"}`` schema and then refused the
list it was handed, while building the result — *after* ``_safe()`` had already
returned. So ``_safe()`` could not help, ``isError`` was True for every call, and
the failure was **not** data-dependent: it happened on an EMPTY queue.

Both instances were shipped dead:
``tortoise_belief_timeline`` (#12364) and ``tortoise_list_dedup_candidates``
(found by the AREA B template in #11081 — the same root, so it is fixed here
rather than filed as a peer).

What is pinned here
-------------------
* **the class**: for every ``ToolDefinition`` that names an ``sdk_method``, if
  the SDK method is declared to return a ``list``, the wrapper's FastMCP-derived
  ``output_schema`` MUST be the ``x-fastmcp-wrap-result`` envelope. This is the
  guard — the next ``-> dict`` typed over a list-returning SDK method fails in
  CI instead of in a client.
* **the two named regressions**: a real MCP round-trip (``mcp.call_tool``, not a
  direct call of the wrapper — a direct call returns the list happily and the
  defect is invisible) returns a result with ``structured_content`` and no
  ``isError``, including for the empty queue/list.
"""

from __future__ import annotations

import ast
import inspect
import os
import sys
import textwrap
import typing

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastmcp.tools import FunctionTool

import tortoise.mcp_server as ms
from tortoise import mcp_auth, tool_registry
from tortoise.sdk import TortoiseSDK

# ── helpers ───────────────────────────────────────────────────────────────


def _registry_pairs() -> list[tuple[str, str]]:
    """(tool name, sdk_method) for every registry row that names one."""
    pairs = []
    for row in tool_registry.TOOL_REGISTRY:
        sdk_method = getattr(row, "sdk_method", None)
        if sdk_method:
            pairs.append((row.name, sdk_method))
    # 28 on the merged surface at this head — MEASURED from a run (39 live rows, 11 of which
    # name no `sdk_method`), and the population is what moves the floor. The edition history,
    # MEASURED at each head rather than subtracted from the previous number:
    #   48 rows / 37 pairs   (before #13556)
    #   46 / 35              #13556 — create_entity's POINT and DIARY arms
    #   44 / 33              #13550 — `check_confidence`'s RECOMPUTE and RECALL arms
    #   40 / 29              #13626 — FOUR doors, and the count is stated in full
    #                        because naming only part of a wave is how this table
    #                        produced a wrong subtraction before: the two `rest_spec`
    #                        doors (`tortoise_issue_insight`, `tortoise_session_context`)
    #                        AND the two DELETE doors (`tortoise_delete_point`,
    #                        `tortoise_delete_entity`) — 44 − 4 = 40. All four named
    #                        an `sdk_method`, so the PAIRS column moves by four too;
    #                        only TWO of them were pass-throughs, which is why the
    #                        pass-through floor below moves by two (12 -> 10) — the
    #                        two columns are different populations.
    #   39 / 28              #14169's P7 batch — FIVE doors that named an `sdk_method`
    #                        retired FORWARD (`create_edge`, `create_operator`,
    #                        `events_poll`, `delete`, `list_graphs`) and FOUR targets
    #                        that name one registered (`poll_events`,
    #                        `delete_knowledge`, `get_historical_knowledge`,
    #                        `link_entities`), so BOTH columns move by the net of one:
    #                        40 − 5 + 4 = 39 and 29 − 5 + 4 = 28. The pass-through
    #                        column moves the OTHER way (10 -> 11) because exactly one
    #                        of the four new targets is a pass-through wrapper
    #                        (`tortoise_get_historical_knowledge`), which is why the
    #                        floor below is re-pinned UP in the same wave.
    #   30 / 24              #11609 lane P11 — the NINE envelope doors retired FORWARD
    #                        with no row arriving
    #                        (`search`/`search_sessions`/`suggest_entry_points` →
    #                        `search_knowledge`; `list_batches`/`diary_read` →
    #                        `list_knowledge`; `list_namespaces`/`check_structure` →
    #                        `graph_overview`; `calibrate_summary` →
    #                        `check_confidence`; `expand_relationships` →
    #                        `explore_connections`), so the ROW population falls by
    #                        exactly nine: 39 − 9 = 30 rows. The PAIR population (rows that
    #                        name an `sdk_method`) falls by only FOUR, because five of the
    #                        nine declared no `sdk_method` (`diary_read`,
    #                        `expand_relationships`, `list_namespaces`, `search_sessions`,
    #                        `suggest_entry_points` — `sdk_method: null` in the manifest):
    #                        28 − 4 = 24 pairs. ⚠️ An earlier form of this entry said "the
    #                        NINE envelope doors all named an `sdk_method` … 28 − 9 = 19
    #                        pairs — measured as 24, i.e. five of the nine did NOT name
    #                        one", which contradicted itself in one sentence: it printed
    #                        the arithmetic of a population where all nine named a method
    #                        and then reported a different measured number (#14645 review
    #                        N3). The MEASURED value is what the pin follows.
    # Every one of those waves retired doors that DID name an `sdk_method`; before them, the
    # READ/QUERY, WRITE/MUTATE and SOURCES/MINE/SESSION container waves and #11609's
    # seven-door wave did the same (`get_session`, `list_batch`,
    # `find_cross_lens_candidates` and the four `create_*` aliases all wrapped an SDK method).
    # ⛔ The population is a MEASUREMENT, not a derivation — and this floor is the proof of
    # what derivation costs: #13599 (`424368c05d`) re-pinned `> 33` to `> 29` as "4 under 33"
    # while #13626 had ALREADY taken the population to 29, so the floor landed exactly ON the
    # population and `29 > 29` reds every shard that runs this file — main-red since, #13678,
    # and the required `python-ci-gate` with it. The RULE beside the pin is the margin, never
    # a free threshold: `> 40` sat 4 under 44, `> 33` sat 4 under 37, so `> 24` sits 4 under 28.
    # ⛔ And it must stay a MEASURED CONSTANT, not a value derived from the registry in code
    # (#13678 proposes `len(TOOL_REGISTRY) - 4`): this scan reads `tool_registry.TOOL_REGISTRY`
    # ITSELF, so a scan that reached an empty or truncated registry would shrink a derived
    # floor along with it and answer `0 > -4` — passing on exactly the failure the line
    # exists to catch ("did the scan reach the registry at all?"). A floor derived from the
    # population it guards cannot see that population collapse.
    # The slack is deliberate: this is a scan-sanity floor, not a pin on the surface — a floor
    # left at the pre-consolidation number REDS on a CORRECT retirement, which is what the
    # previous `> 40` did when the seventh door left. A floor that drifts down by as much as
    # the surface does stops noticing a scan that has lost an arm of its own.
    assert len(pairs) > 20, f"registry scan found only {len(pairs)} rows — the scan is broken"
    return pairs


def _wrapper(name: str):
    fn = getattr(ms, name, None)
    assert fn is not None, (
        f"{name} is in TOOL_REGISTRY but there is no function of that name in "
        f"mcp_server — the registry and the surface disagree")
    return fn


def _output_schema(fn) -> dict:
    tool = FunctionTool.from_function(fn, name=fn.__name__, description="probe")
    return tool.output_schema or {}


def _annotation_kind(ann) -> str | None:
    """'list' | 'dict' | None, read from the RAW annotation.

    Never through ``typing.get_type_hints``: the SDK modules use ``from
    __future__ import annotations``, so evaluation resolves every forward
    reference in the signature and raises on any annotation imported only under
    ``TYPE_CHECKING`` (measured: ``NameError: name 'datetime' is not defined``).
    The container kind is legible in the un-evaluated text.
    """
    if ann is inspect.Signature.empty or ann is None:
        return None
    if isinstance(ann, str):
        compact = ann.replace(" ", "")
        if compact.startswith(("list[", "List[", "Sequence[", "tuple[", "set[")) \
                or compact in ("list", "List"):
            return "list"
        if "dict" in compact or "Mapping" in compact or compact == "Any":
            return "dict" if compact != "Any" else None
        return None
    origin = typing.get_origin(ann)
    if origin in (list, set, tuple) or ann is list:
        return "list"
    if origin is dict or ann is dict:
        return "dict"
    return None


#: The two statement shapes that RETURN the SDK value unwrapped. Both are
#: "pass-throughs" and both owe their SDK method's declared type; a wrapper in
#: any other shape is reshaping and keeps its own annotation. Two shapes exist
#: because both are idiomatic here and a guard that saw only the first would be
#: SILENT (not merely narrow) about the second — a review round measured
#: exactly that: `return _safe(_get_org_sdk().m, …)` was seen, while the
#: equivalent `rows = _safe(_get_org_sdk().m, …); return rows` was skipped and
#: the wrapper therefore escaped the guard with no offender and no change to the
#: `checked` floor.
def _tail_name(node) -> str | None:
    """The last name of a call target — `_safe` and `ms._safe` both resolve to
    `_safe`, because a module-qualified call is the same pass-through."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _sdk_method_called_by(val) -> str | None:
    """The `<m>` in `_safe(_get_org_sdk().<m>, …)`, or None."""
    if not (isinstance(val, ast.Call) and _tail_name(val.func) == "_safe" and val.args):
        return None
    first = val.args[0]
    if (isinstance(first, ast.Attribute)
            and isinstance(first.value, ast.Call)
            and _tail_name(first.value.func) == "_get_org_sdk"):
        return first.attr
    return None


class UnreadableWrapper(AssertionError):
    """The classifier could not READ a wrapper's source.

    ⛔ It must never be reported as "reshaping": `None` from
    `_passthrough_of` means "this wrapper's shape is its own business", and a
    source that could not be read is NOT that fact — it is a hole in the scan,
    and a hole that reports clean is the vacuity the guard below exists to
    prevent.
    """


def _passthrough_of(fn) -> str | None:
    """The SDK method a wrapper RETURNS UNCHANGED, or None (see above)."""
    try:
        src = textwrap.dedent(inspect.getsource(fn))
    except (OSError, TypeError) as exc:
        raise UnreadableWrapper(
            f"cannot read the source of {getattr(fn, '__name__', fn)!r}: {exc}") from exc
    body = [n for n in ast.parse(src).body[0].body
            if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant))]
    # shape A — one statement, returning the call itself:
    if len(body) == 1 and isinstance(body[0], ast.Return):
        return _sdk_method_called_by(body[0].value)
    # shape B — bind it, then return the binding as the LAST statement:
    if (len(body) == 2
            and isinstance(body[0], ast.Assign)
            and isinstance(body[1], ast.Return)
            and isinstance(body[1].value, ast.Name)):
        bound = {t.id for t in body[0].targets if isinstance(t, ast.Name)}
        if body[1].value.id in bound:
            return _sdk_method_called_by(body[0].value)
    return None


def _passthrough_method(name: str) -> str | None:
    return _passthrough_of(getattr(ms, name))


# ── 1. THE GUARD: a list-returning SDK method needs the wrap envelope ─────


def test_every_passthrough_wrapper_declares_its_sdk_methods_type():
    """THE guard this issue asks for. A pass-through wrapper annotated `-> dict`
    over an SDK method declared `-> list[dict]` produces an object schema over a
    list value, and FastMCP refuses that value — the tool is dead, silently, on
    every call."""
    offenders = []
    checked = 0
    reshaped = 0
    unreadable: list[str] = []
    unresolved: list[str] = []
    pairs = _registry_pairs()
    for name, sdk_method in pairs:
        if not hasattr(TortoiseSDK, sdk_method):
            # A row naming a method the SDK does not have would be SKIPPED, and a
            # skipped row is a hole in an exhaustive guard — so it is measured,
            # not silently passed over.
            unresolved.append(f"{name} -> TortoiseSDK.{sdk_method}")
            continue
        try:
            passed_through = _passthrough_method(name)
        except UnreadableWrapper as exc:
            unreadable.append(str(exc))
            continue
        if passed_through is None:
            reshaped += 1
            continue  # a reshaping wrapper is entitled to its own annotation
        checked += 1
        wrapper_kind = _annotation_kind(
            inspect.signature(_wrapper(name)).return_annotation)
        sdk_kind = _annotation_kind(
            inspect.signature(getattr(TortoiseSDK, passed_through)).return_annotation)
        if wrapper_kind != sdk_kind:
            offenders.append(
                f"{name}: wrapper declares {wrapper_kind!r} but "
                f"TortoiseSDK.{passed_through} returns {sdk_kind!r}")
    # ── non-vacuity, DERIVED (never a literal floor) ─────────────────────
    # A count literal is the wrong instrument for EXHAUSTIVENESS: it reds on a CORRECT
    # retirement — the pre-#4282-Phase-2 population measured 22 pass-throughs;
    # the WRITE/MUTATE wave alone took it to 19 (`tortoise_annotate_operator`,
    # `tortoise_promote_point` and `tortoise_set_point_baseline` retired into
    # reshaping containers), and the MERGED head measures 16, because the
    # READ/QUERY wave also retired `tortoise_list_dedup_candidates` into
    # `review_link_candidates`, which returns `Any` and so owes no list type —
    # and nothing broke. (⛔ Not "20": that number is the old `>= 20` LITERAL
    # re-read as a count — measured against origin/main with this file's own
    # classifier: 61 rows, 22 pass-through, 39 reshaping.) Meanwhile a literal still
    # goes quiet when the scan stops READING wrappers. What the
    # guard actually needs is that the scan is EXHAUSTIVE: every row that names
    # an SDK method is classified into exactly one of pass-through / reshaping,
    # and no row is dropped for an unreadable source or a missing SDK method.
    # The ONE literal below (`checked >= 11`) is not this instrument — it is the
    # population floor, argued where it sits, including why it keeps 0 slack while
    # the `pairs` floor above keeps 4 rows of margin.
    assert not unresolved, (
        "these registry rows name an SDK method that is not on TortoiseSDK, so the "
        "guard skipped them and cannot be exhaustive:\n  " + "\n  ".join(unresolved))
    assert not unreadable, (
        "these wrappers' source could not be read, so they were never classified — "
        "an unreadable wrapper is a hole in the scan, not a reshaping one:\n  "
        + "\n  ".join(unreadable))
    # ⛔ AND a floor of ONE — read what it does and does not buy. Exhaustiveness
    # alone is a TAUTOLOGY: every iteration increments exactly one counter (or lands
    # in a list already proven empty), so an "unclassified row" is unrepresentable
    # and that equality can never fail. The bucket that actually swallows holes is
    # `reshaped`: `_passthrough_of` returns None for ANY shape it does not recognise,
    # so a real pass-through rewritten in an idiom the classifier does not know (e.g.
    # `return _safe(_quota_gated(_get_org_sdk().m, "points"), …)`, which the registry
    # does use) is counted as "reshaping" and escapes — the same escape `_tail_name`'s
    # note records a review round measuring. This floor therefore detects TOTAL
    # collapse only (measured: `_passthrough_of` -> None answers checked=0,
    # reshaped=44 and would otherwise satisfy every assert above); it is NOT a drift
    # detector, and it cannot be one without a second, independent classifier.
    # It is also a deliberate FALSE-RED risk in the other direction: a future batch
    # that absorbs EVERY remaining pass-through door into reshaping containers takes
    # checked to 0 and WILL red here, on a change that may be correct. Accepted on
    # purpose — "no wrapper in the registry passes its SDK value through any more" is
    # a class-level change a reviewer should see, not a silent one. (What the literal
    # `>= 20` got wrong was different: it red at 19 while 19 was CORRECT.)
    assert checked > 0, (
        f"not one of {len(pairs)} registry rows was classified as a pass-through "
        f"({reshaped} read as reshaping) — a classifier that matches no real "
        "wrapper compares nothing, so a clean result here would be vacuous")
    assert checked + reshaped == len(pairs), (
        f"the scan classified {checked + reshaped} of {len(pairs)} registry rows "
        f"({checked} pass-through, {reshaped} reshaping) — an unclassified row is a "
        "wrapper this guard never looked at, so a clean result here would be vacuous")
    # AND the pass-through POPULATION floor — the vacuity guard, not a target, and
    # the measured merged number like every other pin in this file. 19 -> 16 with
    # the surface: `tortoise_list_dedup_candidates` left the pass-through population
    # when its SDK method retired into `review_link_candidates` (#11609 Phase 2,
    # which returns `Any` and so owes no list type), and the three doors named above
    # left it with the WRITE/MUTATE containers. 16 -> 13 with #11609's door-retirement
    # wave: THREE of its seven doors were pass-through wrappers and left this
    # population with them (`get_session`, `list_batch`, `find_cross_lens_candidates`);
    # the four `create_*` aliases were NOT pass-throughs — they reshaped, so they are
    # counted by the `reshaped` term above and subtract nothing here: 16 − 3 = 13.
    # 13 -> 12 with #13550's arm-retirement wave, and only ONE of its two doors counts
    # here: `tortoise_compute_confidence` was a pass-through (`return _safe(sdk
    # .compute_confidence, …)`), while `tortoise_recall` RESHAPED (`return {"mode": mode,
    # "results": results}`), so it left the `reshaped` term above instead.
    # 12 -> 10 with #13626's `rest_spec` wave, MEASURED at `2c1065a52e` (44 rows / 33
    # pairs / 12 pass-through) against this head (39 / 28 / 11): TWO of its four retired
    # doors were pass-throughs — `tortoise_session_context` (`return
    # _safe(_get_org_sdk().session_context)`) and `tortoise_issue_insight` (same shape A,
    # with the door's own args) — while the two DELETE doors (`tortoise_delete_point`,
    # `tortoise_delete_entity`) RESHAPED into the kept single entry, so they are the pair
    # that left `reshaped`.
    # This is why a count can move by FOUR while the pass-through population moves by TWO:
    # the two terms are not the same population, and the delta was measured, not assumed.
    # (An earlier cut of this comment claimed all seven subtracted — 16 − 7 = 9 — and
    # that arithmetic put the floor four rows under the population the scan actually
    # produces, which is slack on the one guard whose job is to notice a collapse.)
    # The floor moves DOWN with the surface, exactly ON the measurement (0 slack, as
    # at 16); it is not lowered to whatever a green run last produced. Measured at
    # this head: 11 pass-through + 17 reshaping = the 28 rows the scan above found.
    # ⛔ WHAT NO GUARD IN THIS TEST CATCHES — written down because the sentence that used
    # to stand here ("the two guards ABOVE … are what still make a vacuous scan
    # impossible") was FALSE, and a false non-vacuity claim is the class-A hazard this
    # whole file exists to stop:
    #   • `checked + reshaped == len(pairs)` is the TAUTOLOGY `:276` already documents —
    #     `unresolved` and `unreadable` are asserted empty BEFORE it, so every row lands
    #     in exactly one counter and the equality cannot fail.
    #   • `checked > 0` sees only the pass-through term collapsing to ZERO.
    #   ⇒ a LOSS OF RESHAPING ROWS is covered by NOTHING here. Measured at this head:
    #     three reshaping rows disappear (a scan that lost them, not a real retirement)
    #     → pairs 28 → 25 (`25 > 24` GREEN), checked 11 → 11 (`>= 11` GREEN), 11 + 14 ==
    #     25 (tautology, GREEN), `checked > 0` GREEN. Every guard passes over a registry
    #     three rows short. The mirror case IS caught: three pass-throughs going
    #     unrecognised answer checked=8 and red this floor.
    #     So the `pairs` floor's 4-row margin is bought by THIS floor's 0 slack for the
    #     PASS-THROUGH term ONLY; the reshaping term carries no pin, and closing it needs
    #     a third floor (`reshaped >= N`) that a comment-only pass may not invent — a
    #     0-slack floor on the term whose job is to SWALLOW unrecognised shapes is a
    #     standing false-red on every reshaping-door retirement, so it owes its own
    #     change and its own mutant attribution (#13704 owns that decision).
    # ⛔ WHY 0 SLACK HERE, and 4 rows of margin 200 lines above (`> 24` for pairs) —
    # the #13676 review named this as the file's one unexplained convention clash,
    # so the rule is stated: the `pairs` floor answers a REACH question ("did the scan
    # reach the registry at all?"), where a floor left at a pre-consolidation number
    # reds on a CORRECT retirement — and, as immediately above, NOTHING downstream
    # catches what its 4-row margin misses. THIS floor answers a COVERAGE question, and
    # a slack row buys exactly one thing: a wrapper the classifier stopped recognising
    # goes uncounted, which is the escape `_passthrough_of` is measured to allow. False
    # PASS is class A, an over-block is class B (AGENTS.md A/B), so the margin that is
    # right for `pairs` is a blind spot here. The price is named, not hidden: a wave
    # that retires a pass-through door reds this line and re-pins it in the same wave —
    # 19 -> 16 -> 13 -> 12 -> 10 -> 11 above IS that procedure — which is the same FALSE-RED
    # direction `checked > 0` accepts deliberately a few lines up.
    # 11 -> 4 in #11609 lane P11: SEVEN of the nine retired envelope doors were
    # pass-through wrappers (`search`, `search_sessions`, `suggest_entry_points`,
    # `list_batches`, `diary_read`, `expand_relationships`, `calibrate_summary`), and
    # re-pinning this floor in the same wave is the procedure its own text prescribes.
    # The measured population is 4 and the count is the scan's own output, not a target.
    assert checked >= 4, (
        f"only {checked} pass-through wrappers were found ({reshaped} read as "
        "reshaping) against a floor of 4, the population measured at this head. "
        "The count cannot tell its two causes apart: a CORRECT retirement of a "
        "pass-through door, or the classifier going blind (a pass-through rewritten "
        "in an idiom `_passthrough_of` does not know is counted as reshaping). "
        "Re-measure the registry before choosing — if the doors really retired, "
        "re-pin this floor in the same wave as the retirement; if not, the scan lost "
        "a shape and a clean result here would be vacuous")
    assert not offenders, (
        "these pass-through MCP tools declare a return type their SDK method "
        "contradicts, so FastMCP refuses their own result and every call fails:\n  "
        + "\n  ".join(offenders))


def test_the_two_named_tools_declare_a_shape_fastmcp_accepts():
    """Both named tools are on the surface, their SDK method returns a LIST, and
    the wrapper advertises a shape FastMCP can ACCEPT.

    Two advertised shapes are acceptable and this asserts the disjunction, not
    one branch of it (`-> dict` over an ENVELOPE, or `-> list[dict]`, which
    FastMCP wraps itself into `{"result": …}` with `x-fastmcp-wrap-result`) —
    the alternative a fixer may legitimately pick must not red the test that
    tells them to pick it.

    ⚠️ This is a CONTRACT pin, not the discriminating test: it also holds on the
    broken tree, because `-> dict` + an object schema IS the broken state. The
    tools being DEAD is what the round-trip tests below measure, and the class is
    what the scan above measures. Do not delete those on the strength of this
    passing.
    """
    for name, sdk_method, expected in (
            ("tortoise_belief_timeline", "_belief_timeline", "list"),
            # #11609: the SDK method retired into `review_link_candidates`, which
            # returns `Any` — so this door owes its OWN envelope (`-> dict`) rather
            # than the SDK method's list type. The premise is re-derived, not dropped.
            ("tortoise_list_dedup_candidates", "review_link_candidates", typing.Any)):
        assert callable(getattr(ms, name, None)), f"{name} is not on the MCP surface"
        raw = inspect.signature(getattr(TortoiseSDK, sdk_method)).return_annotation
        if expected is typing.Any:
            # ⛔ `_annotation_kind` answers `None` for BOTH `-> Any` and "no annotation
            # at all", so `assert sdk_kind == None` also passed once the container lost
            # its annotation entirely (-> the door's own envelope would be derived from
            # nothing). Pin the CONTAINER's shape on the raw annotation (#12951 fix-round
            # review P3); `from __future__ import annotations` makes it the string "Any".
            assert raw in (typing.Any, "Any"), (
                f"TortoiseSDK.{sdk_method} no longer declares `-> Any` (raw annotation "
                f"{raw!r}): the container's shape is what {name}'s own envelope is "
                f"derived from — re-derive the guard instead of deleting it")
        else:
            sdk_kind = _annotation_kind(raw)
            assert sdk_kind == expected, (
                f"TortoiseSDK.{sdk_method} now returns {sdk_kind!r}, not {expected!r} "
                f"— this test's premise changed; re-derive the guard instead of "
                f"deleting it")
        wrapper_kind = _annotation_kind(
            inspect.signature(getattr(ms, name)).return_annotation)
        schema = _output_schema(getattr(ms, name))
        wrapped = bool(schema.get("x-fastmcp-wrap-result"))
        if wrapped:
            # FastMCP's own envelope: the wrapper must declare the list it returns.
            assert wrapper_kind == "list", (
                f"{name} advertises a wrap envelope but declares {wrapper_kind!r}")
        else:
            # The wrapper's own envelope: object schema, and the rows are inside it.
            assert wrapper_kind == "dict", (
                f"{name} declares {wrapper_kind!r} over a list-returning SDK "
                f"method, and advertises no wrap envelope — the object schema "
                f"would be refused at call time. Envelop the rows (-> dict) or "
                f"annotate -> list[dict]")
            assert schema.get("type") == "object", schema


# ── 2. THE ROUND-TRIP: a real call_tool must not be refused ───────────────


class _StubSDK:
    """The SDK as both wrappers see it: a method that returns a list."""

    def _belief_timeline(self, topic, limit=50):
        return []

    def review_link_candidates(self, kind="connections", *,
                               candidate_type="content", limit=50):
        return []


@pytest.fixture()
def stubbed(monkeypatch):
    monkeypatch.setattr(ms, "_get_org_sdk", lambda *a, **k: _StubSDK())
    token = mcp_auth._transport_mode.set("http")
    try:
        yield
    finally:
        mcp_auth._transport_mode.reset(token)


@pytest.mark.asyncio
@pytest.mark.parametrize("name,args", [
    ("tortoise_belief_timeline", {"topic": "Helios"}),
    ("tortoise_list_dedup_candidates", {}),
])
async def test_an_empty_list_result_round_trips_through_call_tool(name, args, stubbed):
    """The acceptance test #12364 asks for: a REAL MCP round-trip. The wrapper
    call alone succeeds on the broken tree, which is how both tools shipped."""
    result = await ms.mcp.call_tool(name, args)

    assert not result.is_error, (
        f"{name} was refused: "
        + "\n".join(getattr(b, "text", "") for b in (result.content or [])))
    assert result.structured_content is not None, (
        f"{name} returned no structured_content — the client cannot unwrap it")
    assert result.structured_content["result"] == []


@pytest.mark.asyncio
async def test_a_non_empty_list_result_keeps_its_rows(stubbed):
    """The fix must not just satisfy the empty case: the rows a caller wants
    have to survive the envelope."""
    class _Rows(_StubSDK):
        def review_link_candidates(self, kind="connections", *,
                                  candidate_type="content", limit=50):
            return [{"id": "c1", "content": "a", "candidate_type": candidate_type}]

    ms._get_org_sdk = lambda *a, **k: _Rows()
    try:
        result = await ms.mcp.call_tool("tortoise_list_dedup_candidates", {})
    finally:
        ms._get_org_sdk = lambda *a, **k: _StubSDK()

    assert not result.is_error
    assert result.structured_content["result"] == [
        {"id": "c1", "content": "a", "candidate_type": "content"}]


@pytest.mark.asyncio
@pytest.mark.parametrize("name,args,raising", [
    ("tortoise_belief_timeline", {"topic": "Helios"}, "_belief_timeline"),
    ("tortoise_list_dedup_candidates", {}, "review_link_candidates"),
])
async def test_a_safe_failure_is_not_demoted_into_the_success_payload(
        name, args, raising, monkeypatch):
    """The envelope must not swallow `_safe`'s failure dict.

    Measured on a mutant that deletes the `isinstance(rows, _SafeError)` branch
    (mutation probe, this file unchanged): the call still returns 5 passed, and
    a raising SDK arrives as `structured_content == {"result": {"error": "db
    down"}}`. It is invisible to the #8482 repair seam: these two tools carry no
    `x-fastmcp-wrap-result` envelope, so `_maybe_wrap_envelope_violation` never
    fires on them. This is the test that branch was missing.

    ⚠️ Re-measured after #11153: that mutant's payload now carries
    `is_error=True`, because the new arm reads the failure THROUGH the envelope
    (`_returned_safe_failure`), so it no longer lands in the success channel. The
    branch is still required — the PAYLOAD would be the failure dict instead of
    the rows — but the verdict is no longer the half that regresses here.
    """
    class _Boom:
        """A stub whose ONE method raises, so `_safe` answers with its error dict."""

        def __init__(self):
            def _raise(*a, **k):
                raise RuntimeError("db down")
            setattr(self, raising, _raise)

    monkeypatch.setattr(ms, "_get_org_sdk", lambda *a, **k: _Boom())
    token = mcp_auth._transport_mode.set("http")
    try:
        result = await ms.mcp.call_tool(name, args)
    finally:
        mcp_auth._transport_mode.reset(token)

    payload = result.structured_content
    assert payload is not None
    assert "result" not in payload, (
        f"{name} enveloped a _safe failure: {payload!r} — the error is now inside "
        f"the success payload instead of on _safe's channel")
    assert payload.get("error"), f"{name} lost the failure message: {payload!r}"
    # `_safe` answers a raising SDK with its own error dict; the SDK's message survives.
    assert "db down" in str(payload), payload


def test_the_passthrough_recogniser_sees_both_idiomatic_shapes():
    """A guard that recognises only ONE way of writing a pass-through is not
    narrow, it is silent: the unrecognised wrapper leaves the population with no
    offender and no change to the `checked` floor, so a `-> dict` over a
    list-returning SDK method ships through it. Measured in a review round on
    this very file. Both shapes are pinned here, and both reshape shapes are
    pinned OUT, because over-recognising would flag the legitimate reshaping
    tools (`tortoise_recall`) and force a rewrite of working code."""
    def one_statement(topic):
        return ms._safe(ms._get_org_sdk()._belief_timeline, topic)  # pragma: no cover

    def bound_then_return(topic):
        rows = ms._safe(ms._get_org_sdk()._belief_timeline, topic)  # pragma: no cover
        return rows  # pragma: no cover

    def enveloped(topic):
        rows = ms._safe(ms._get_org_sdk()._belief_timeline, topic)  # pragma: no cover
        if isinstance(rows, dict):
            return rows  # pragma: no cover
        return {"result": rows}  # pragma: no cover

    def reshaped(topic):
        return {"mode": "state", "rows": ms._safe(  # pragma: no cover
            ms._get_org_sdk()._belief_timeline, topic)}

    # #12901: the door calls the PRIVATE seam, so the recogniser answers the
    # spelling the class actually holds.
    assert _passthrough_of(one_statement) == "_belief_timeline"
    assert _passthrough_of(bound_then_return) == "_belief_timeline"
    assert _passthrough_of(enveloped) is None
    assert _passthrough_of(reshaped) is None
