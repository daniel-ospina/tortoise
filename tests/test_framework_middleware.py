"""Framework injection middleware — issue #2125 (epic #2080 end-state seams).

W3-style seam grade for the three framework extension points, mirroring
``test_platform_seams.py``'s posture for the harness seams:

1. **One shared reflex.** A REAL seeded embedded graph drives EVERY framework
   adapter through ``sdk_reflex`` → ``TortoiseSDK.volunteer_context()`` →
   ``tortoise/volunteer.py``. The same block text must land in all three
   framework prompts — there is no per-framework pipeline.
2. **Fail-open content.** A raising / degraded / clean-empty reflex injects
   NOTHING through any adapter and never raises into the app's turn; a failed
   HTTP POST is the same silence. Semantic Kernel's own ``next`` error is the
   exception that DOES propagate (framework failure, not ours).
3. **The documented extension point, duck-typed.** ADK mutates through
   ``LlmRequest.append_instructions`` (google/adk-python
   ``models/llm_request.py``), LangGraph returns state with a new message
   (``langgraph`` pre-model node), Semantic Kernel overrides
   ``context.rendered_prompt`` after ``await next(context)``
   (learn.microsoft.com Semantic Kernel filters — Prompt Render Filter).
4. **Opt-in + dependency-free (W4 user-exposure gate).** Importing
   ``tortoise.framework`` pulls in no framework and registers nothing; the
   app must construct a provider and register the hook explicitly.

The real-reflex leg builds its graph IN-PROCESS on the embedded lane (an
explicit ``db_path`` beats an env URI by construction), so the module needs no
``TORTOISE_DB_URI`` and no child process.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import threading
import types as _pytypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar

import pytest

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")

from tortoise.framework import (
    adk_before_model_callback,
    block_from,
    http_reflex,
    langgraph_pre_model_node,
    sdk_reflex,
    semantic_kernel_prompt_render_filter,
)
from tortoise.volunteer import (
    MAX_WINDOW_BYTES,
    PER_TURN_OVERHEAD_BYTES,
    degraded_response,
    empty_response,
)

_CLAIM = "The CI pipeline deploys via GitHub Actions on main merge."
_PROMPT = "How is CI deploy triggered?"


# ── Faithful fakes of the three documented shapes ──────────────────────────


class _Part:
    def __init__(self, text: str) -> None:
        self.text = text


class _Content:
    """``google.genai.types.Content`` — the ADK LlmRequest turn shape."""

    def __init__(self, role: str, text: str) -> None:
        self.role = role
        self.parts = [_Part(text)]


class _AdkRequest:
    """ADK ``LlmRequest``: mutable ``contents`` + ``append_instructions``.

    The mutator mirrors the REAL contract, not a convenient one. Every released
    ``google-adk`` takes ``list[str]`` (v0.1.0-v1.14.0) or
    ``list[str] | types.Content`` (v2.0.0 onward) and REJECTS a bare ``str`` —
    v2.x with ``TypeError``, v0.x/v1.x by joining the string character by
    character. A fake that accepted a ``str`` is what let "the ADK adapter
    silently never injects" through, so this one refuses it the same way.
    """

    def __init__(self, turns: list[tuple[str, str]]) -> None:
        self.contents = [_Content(role, text) for role, text in turns]
        self.system_instruction: str = ""

    def append_instructions(self, instructions) -> None:
        if isinstance(instructions, list) and all(
                isinstance(item, str) for item in instructions):
            joined = "\n\n".join(instructions)
            self.system_instruction = (
                f"{self.system_instruction}\n\n{joined}"
                if self.system_instruction else joined)
            return
        if isinstance(instructions, _Content):  # v2.x also accepts types.Content
            self.contents.append(instructions)
            return
        raise TypeError(
            "instructions must be list[str] or types.Content, "
            f"got {type(instructions).__name__}.")


class _AdkRequestNoAppend:
    """An ADK build without ``append_instructions`` — the fallback branch."""

    def __init__(self, turns: list[tuple[str, str]]) -> None:
        self.contents = [_Content(role, text) for role, text in turns]


class _Msg:
    """A LangChain message: ``.type`` + ``.content`` (str or content blocks)."""

    def __init__(self, type_: str, content) -> None:
        self.type = type_
        self.content = content


class _SkContext:
    def __init__(self, rendered: str | None) -> None:
        self.rendered_prompt = rendered


def _reflex(block: str = "REFLEX BLOCK", *, error: Exception | None = None):
    def reflex(window):
        assert window, "an adapter must never call the reflex with an empty window"
        if error is not None:
            raise error
        return {
            "pointers": [], "why": [], "surfaced": [],
            "block": block, "degraded_reason": None,
        }
    return reflex


def _spy():
    """A reflex capturing the window the adapter built, then injecting."""
    seen: dict = {}

    def reflex(window):
        seen["window"] = window
        return {"block": "REFLEX BLOCK", "degraded_reason": None}

    return reflex, seen


def _lg(reflex):
    """A LangGraph node with the test's message type (``langchain_core`` is not a
    dependency here; the DEFAULT factory's own branch has a dedicated test)."""
    return langgraph_pre_model_node(reflex, message_factory=lambda text: _Msg("human", text))


class _NeverNext:
    async def __call__(self, context) -> None:
        return None


# ── The real pipeline through every adapter (the seam grade) ───────────────


@pytest.fixture(scope="module", autouse=True)
def _offline_embedder_cache():
    """Read the embedder from the LOCAL cache, never from the hub.

    The suite's hermetic egress guard (#4387) blocks the hub by design, so the
    implicit HEAD request in the default hub client would retry against it for
    ~160 s per process — all of it sleeps, none of it work. The model is a
    local cache read, so declaring that explicitly is both faster and a MORE
    faithful read (the embedder actually loads instead of tripping the 90 s
    warm-up watchdog into a keyword-only fallback). Scoped to this module and
    undone at teardown.
    """
    mp = pytest.MonkeyPatch()
    mp.setenv("HF_HUB_OFFLINE", "1")
    mp.setenv("TRANSFORMERS_OFFLINE", "1")
    yield
    mp.undo()


@pytest.fixture(scope="module")
def seeded_sdk(tmp_path_factory, _offline_embedder_cache):
    """One EP-measured embedded graph (module-cached: the seed dominates)."""
    from tortoise.sdk import TortoiseSDK

    db = tmp_path_factory.mktemp("framework") / "framework.db"
    sdk = TortoiseSDK(db_path=str(db))
    proj = sdk._get_proj()
    evidence = sdk.create_point(
        "evidence", "CI deploys on main merge via GitHub Actions [supporting record]")
    claim = sdk.create_point("statement", _CLAIM)
    sdk.create_operator("IMPL", evidence["id"], [claim["id"]])
    for pid, alpha, beta in ((claim["id"], 12.0, 1.0), (evidence["id"], 12.0, 1.0)):
        proj.g.query(
            "MATCH (n:Point {id:$id}) SET n.confidence=$c, "
            "n.posterior_alpha=$a, n.posterior_beta=$b",
            params={"id": pid, "a": alpha, "b": beta,
                    "c": round(alpha / (alpha + beta), 4)})
    yield sdk
    sdk.close()


def test_real_reflex_block_rides_every_framework_prompt(seeded_sdk):
    """One seeded graph, one reflex, three prompts — the SAME block lands."""
    reflex = sdk_reflex(seeded_sdk)
    # The reflex's own answer for this window — every adapter must carry it verbatim.
    expected = seeded_sdk.volunteer_context(
        window=[{"role": "user", "content": _PROMPT}])["block"]
    assert "point/" in expected and _CLAIM in expected, (
        "fixture produced no block — the framework grade would be vacuous")

    # Google ADK — before_model_callback
    adk_request = _AdkRequest([("user", _PROMPT)])
    assert adk_before_model_callback(reflex)(None, adk_request) is None  # model call proceeds
    assert adk_request.system_instruction == expected

    # LangGraph — pre-model node
    langgraph_out = _lg(reflex)({"messages": [_Msg("human", _PROMPT)]})
    assert len(langgraph_out["messages"]) == 1
    assert langgraph_out["messages"][0].content == expected

    # Semantic Kernel — Prompt Render Filter (block PREPENDED to the rendered prompt)
    sk_context = _SkContext(_PROMPT)
    asyncio.run(semantic_kernel_prompt_render_filter(reflex)(sk_context, _NeverNext()))
    assert sk_context.rendered_prompt == f"{expected}\n\n{_PROMPT}"


# ── Fail-open content ──────────────────────────────────────────────────────


def test_fail_open_when_the_reflex_raises():
    boom = _reflex(error=RuntimeError("tortoise unreachable"))

    adk_request = _AdkRequest([("user", _PROMPT)])
    assert adk_before_model_callback(boom)(None, adk_request) is None
    assert adk_request.system_instruction == ""
    assert len(adk_request.contents) == 1  # no extra turn injected

    assert _lg(boom)({"messages": [_Msg("human", _PROMPT)]}) == {}

    sk_context = _SkContext(_PROMPT)
    asyncio.run(semantic_kernel_prompt_render_filter(boom)(sk_context, _NeverNext()))
    assert sk_context.rendered_prompt == _PROMPT  # untouched, no raise


@pytest.mark.parametrize(
    "response", [degraded_response("timeout"), empty_response(), None, "garbage"],
    ids=["degraded", "clean-empty", "none", "non-dict"],
)
def test_degraded_and_clean_empty_inject_nothing(response):
    assert block_from(response) == ""
    reflex = lambda window: response  # noqa: E731
    assert _lg(reflex)({"messages": [_Msg("human", _PROMPT)]}) == {}
    sk_context = _SkContext(_PROMPT)
    asyncio.run(semantic_kernel_prompt_render_filter(reflex)(sk_context, _NeverNext()))
    assert sk_context.rendered_prompt == _PROMPT


def test_blank_block_is_not_injected():
    assert block_from({"block": "   \n ", "degraded_reason": None}) == ""


def test_semantic_kernel_propagates_the_frameworks_own_error():
    """Only Tortoise's half is fail-open; SK's ``next`` failure is the app's."""
    async def failing_next(context):
        raise RuntimeError("semantic kernel render failed")

    with pytest.raises(RuntimeError, match="semantic kernel render failed"):
        asyncio.run(
            semantic_kernel_prompt_render_filter(_reflex())(_SkContext(_PROMPT), failing_next))


# ── The documented extension points, duck-typed ────────────────────────────


def test_adk_uses_append_instructions_and_leaves_contents_alone():
    request = _AdkRequest([("user", _PROMPT)])
    adk_before_model_callback(_reflex())(None, request)
    assert request.system_instruction == "REFLEX BLOCK"
    assert len(request.contents) == 1  # no extra turn injected


def test_adk_append_instructions_is_passed_a_list_not_a_bare_string():
    """The argument must be ``list[str]``: a bare ``str`` is a ``TypeError`` on
    ADK v2.x and a character-by-character join on v0.x/v1.x — and either way the
    block never reaches the prompt while the fail-open boundary hides it.

    The fake above now enforces the real signature, so this pins the exact
    argument rather than trusting it.
    """
    calls: list = []

    class _Strict:
        def __init__(self):
            self.contents = [_Content("user", _PROMPT)]
            self.system_instruction = ""

        def append_instructions(self, instructions):
            calls.append(instructions)
            if not (isinstance(instructions, list)
                    and all(isinstance(i, str) for i in instructions)):
                raise TypeError("instructions must be list[str] or types.Content")
            self.system_instruction = "\n\n".join(instructions)

    request = _Strict()
    assert adk_before_model_callback(_reflex())(None, request) is None
    assert calls == [["REFLEX BLOCK"]], "append_instructions got a non-list argument"
    assert request.system_instruction == "REFLEX BLOCK"


def test_adk_fallback_appends_a_user_content(monkeypatch):
    """An ADK build without ``append_instructions`` still gets the block."""
    class _FakeContent:
        def __init__(self, role=None, parts=None):
            self.role, self.parts = role, parts or []

    class _FakePart:
        def __init__(self, text=None):
            self.text = text

    genai = _pytypes.ModuleType("google.genai")
    genai.types = _pytypes.SimpleNamespace(Content=_FakeContent, Part=_FakePart)
    monkeypatch.setitem(sys.modules, "google", _pytypes.ModuleType("google"))
    monkeypatch.setitem(sys.modules, "google.genai", genai)
    monkeypatch.setitem(sys.modules, "google.genai.types", genai.types)

    request = _AdkRequestNoAppend([("user", _PROMPT)])
    adk_before_model_callback(_reflex())(None, request)
    assert request.contents[-1].role == "user"
    assert request.contents[-1].parts[0].text == "REFLEX BLOCK"


def test_langgraph_default_message_factory_builds_a_human_message(monkeypatch):
    """The shipped default constructs ``langchain_core.messages.HumanMessage``."""
    class _FakeHumanMessage:
        def __init__(self, content=None):
            self.type, self.content = "human", content

    fake = _pytypes.ModuleType("langchain_core.messages")
    fake.HumanMessage = _FakeHumanMessage
    core = _pytypes.ModuleType("langchain_core")
    core.messages = fake
    monkeypatch.setitem(sys.modules, "langchain_core", core)
    monkeypatch.setitem(sys.modules, "langchain_core.messages", fake)

    out = langgraph_pre_model_node(_reflex())({"messages": [_Msg("human", _PROMPT)]})
    assert isinstance(out["messages"][0], _FakeHumanMessage)
    assert out["messages"][0].content == "REFLEX BLOCK"


def test_langgraph_maps_roles_and_multimodal_blocks():
    reflex, seen = _spy()
    _lg(reflex)({"messages": [
        _Msg("human", "hello"), _Msg("ai", "hi"), _Msg("tool", "{}"),
        _Msg("human", [{"type": "text", "text": "multi "},
                       {"type": "text", "text": "modal"}]),
    ]})
    assert [turn["role"] for turn in seen["window"]] == [
        "user", "assistant", "system", "user"]
    assert seen["window"][-1]["content"] == "multi modal"


def test_langgraph_list_content_may_hold_plain_strings():
    """LangChain types message content as ``str | list[str | dict]`` — a list
    member may be a plain string, not a content block. Handling only the dicts
    flattened a legal ``["Hello", " world"]`` to "", and the empty turn was
    then discarded: a user turn's text vanished (nothing injected at all when it
    was the only turn)."""
    reflex, seen = _spy()
    _lg(reflex)({"messages": [_Msg("human", ["Hello", " world"])]})
    assert seen["window"] == [{"role": "user", "content": "Hello world"}]

    reflex2, seen2 = _spy()
    _lg(reflex2)({"messages": [_Msg("human", [{"type": "text", "text": "a"}, "b"])]})
    assert seen2["window"] == [{"role": "user", "content": "ab"}]


def test_langgraph_message_factory_override():
    """A graph whose model rejects a second system message can choose its own type."""
    out = langgraph_pre_model_node(
        _reflex(), message_factory=lambda text: _Msg("system", text),
    )({"messages": [_Msg("human", _PROMPT)]})
    assert out["messages"][0].type == "system"


def test_semantic_kernel_prepends_to_the_rendered_prompt():
    context = _SkContext("USER TEMPLATE OUTPUT")
    asyncio.run(semantic_kernel_prompt_render_filter(_reflex())(
        context, _NeverNext()))
    assert context.rendered_prompt == "REFLEX BLOCK\n\nUSER TEMPLATE OUTPUT"


# ── Window bounding (the reflex validates 1..20 turns / 15 KB) ─────────────


def test_window_is_bounded_to_the_last_twenty_turns():
    reflex, seen = _spy()
    _lg(reflex)({"messages": [_Msg("human", f"turn {i}") for i in range(40)]})
    window = seen["window"]
    assert len(window) == 20
    assert window[-1]["content"] == "turn 39"  # the tail — the turn being answered
    assert window[0]["content"] == "turn 20"


def test_overlong_turn_is_truncated_keeping_the_tail():
    """The tail survives, and the truncated turn fits the REFLEX's byte budget.

    The budget is ``MAX_WINDOW_BYTES`` against UTF-8 bytes plus the per-turn
    overhead ``validate_request`` charges — not a character count (see
    :func:`test_built_window_is_accepted_by_the_real_reflex`).
    """
    reflex, seen = _spy()
    _lg(reflex)({"messages": [_Msg("human", "A" * 10_000 + "Z" * 10_000)]})
    content = seen["window"][0]["content"]
    assert content.endswith("Z" * 1_000)  # the tail — the turn being answered
    assert len(content.encode("utf-8")) == MAX_WINDOW_BYTES - PER_TURN_OVERHEAD_BYTES
    assert content[-1_000:].count("A") == 0  # only the tail is kept


@pytest.mark.parametrize("filler", ["á", "中", "😀"])  # 2, 3 and 4 bytes
def test_overlong_multibyte_turn_is_truncated_on_bytes_not_characters(filler):
    """A byte budget must slice BYTES (a character slice overruns it) AND must
    not keep a split codepoint (a naive byte slice does).

    The 3-byte filler is the load-bearing one: the content budget is not a
    multiple of 3 (15296 % 3 == 2), so a byte slice leaves a partial codepoint
    that only the ``errors="ignore"`` decode drops. A 2-byte filler cannot show
    this — ``15296 % 2 == 0`` — which is why this is parametrized.
    """
    reflex, seen = _spy()
    _lg(reflex)({"messages": [_Msg("human", filler * 20_000)]})
    content = seen["window"][0]["content"]
    room = MAX_WINDOW_BYTES - PER_TURN_OVERHEAD_BYTES
    assert content  # never emptied
    # Whole codepoints only: a kept partial would break this equality (or raise).
    assert content == filler * len(content)
    assert content.endswith(filler)
    # EXACTLY the whole codepoints that fit — proof the partial was dropped.
    assert len(content) == room // len(filler.encode("utf-8"))


def test_whitespace_tail_does_not_discard_the_turn_or_the_window():
    """A tail that is only whitespace must not empty the window.

    The byte fit keeps the TAIL, so a turn ending in a long whitespace run was
    truncated to whitespace alone; the guard that rejected it used ``break``,
    which abandoned the OLDER turns too. On the newest turn the window went
    empty and the adapter injected NOTHING, though real content existed.
    """
    reflex, seen = _spy()
    _lg(reflex)({"messages": [
        _Msg("human", "REAL QUESTION: how is CI deploy triggered?"),
        _Msg("human", "Z" + " " * 20_000),
    ]})
    window = seen["window"]
    assert any("REAL QUESTION" in turn["content"] for turn in window)
    # trailing whitespace is stripped before the fit, never kept in its place
    assert all(turn["content"] == turn["content"].rstrip() for turn in window)
    assert all(turn["content"] for turn in window)


def test_single_turn_with_a_long_whitespace_tail_still_injects():
    """The degenerate case: ONE turn whose byte tail is all whitespace."""
    reflex, seen = _spy()
    _lg(reflex)({"messages": [
        _Msg("human", "REAL QUESTION: CI deploy?" + " " * 20_000)]})
    assert seen["window"], "the window emptied although the turn had content"
    assert "REAL QUESTION" in seen["window"][0]["content"]
    assert seen["window"][0]["content"].endswith("?")


def test_unusable_turn_does_not_abandon_an_older_turn_that_fits():
    """The `continue` branch, made load-bearing.

    A byte slice yields nothing when ``room`` is smaller than the tail
    codepoint's width. That happens only for an OLDER turn, and the older turn
    still waiting behind it has to be kept: `break` would take it down too.

    Arithmetic is exact — the newer turn consumes 15230 content bytes, so the
    next turn's room is 15360 - (15230 + 64) - 64 = 2 bytes; a 3-byte "中"
    slices to a partial codepoint and decodes to "", and the turn behind it
    then gets the same 2-byte room and survives as its 2-byte tail.
    """
    reflex, seen = _spy()
    _lg(reflex)({"messages": [
        _Msg("human", "REAL QUESTION"),
        _Msg("human", "中"),
        _Msg("human", "A" * 15_230),
    ]})
    window = seen["window"]
    assert [turn["content"] for turn in window] == ["ON", "A" * 15_230]


@pytest.mark.parametrize("filler", ["A", "á", "中", "😀"])
def test_built_window_is_accepted_by_the_real_reflex(seeded_sdk, filler):
    """#8107 review P1: the bound must be the reflex's BYTE budget, not characters.

    The old bound counted characters (``MAX_WINDOW_CHARS = 15_000``) against a
    validator that counts UTF-8 bytes plus 64 per turn. A 20-turn window at the
    character cap — and every multibyte window far below it — was therefore
    REJECTED by ``validate_request``, swallowed by ``_safe_block``, and injected
    nothing: a silent no-op in exactly the long-conversation case the bound
    exists for. Accented Spanish tripped it ~2x earlier still. This drives the
    REAL validator, not a spy — a fake reflex cannot catch it.
    """
    from tortoise.volunteer import validate_request

    real = sdk_reflex(seeded_sdk)
    seen: dict = {}

    def recording(window):
        seen["window"] = window
        return real(window)  # the REAL validator runs on this window

    messages = [_Msg("human", filler * 15_000) for _ in range(20)]
    _lg(recording)({"messages": messages})  # the adapter swallows, never raises
    window = seen["window"]
    validate_request(window)  # explicit: the reflex accepted the built window
    assert 0 < len(window) <= 20
    assert any(turn["content"].strip() for turn in window)


def test_semantic_kernel_rendered_prompt_is_bounded():
    """The SK adapter handed the WHOLE rendered prompt to the reflex as one
    unbounded turn, so an over-cap prompt was rejected and injected nothing."""
    from tortoise.volunteer import validate_request

    reflex, seen = _spy()
    context = _SkContext("X" * 50_000)
    asyncio.run(semantic_kernel_prompt_render_filter(reflex)(context, _NeverNext()))
    validate_request(seen["window"])
    assert len(seen["window"]) == 1
    assert seen["window"][0]["content"].endswith("X")  # tail kept
    assert context.rendered_prompt.startswith("REFLEX BLOCK")


@pytest.mark.parametrize("state", [
    {"messages": 5},
    {"messages": None},
    {"messages": []},
    {},
    "not-a-dict",
    {"messages": [None]},
    {"messages": [_Msg("human", [{"type": "text", "text": None}])]},
    {"messages": [_Msg("human", [{"type": "text", "text": 123}])]},
    {"messages": [_Msg("human", [{"type": "text", "text": ["x"]}])]},
    {"messages": [_Msg("human", 12)]},
    {"messages": [_Msg("human", {"not": "a string"})]},
])
def test_langgraph_malformed_state_never_raises(state):
    """#8107 review P2: the builder ran OUTSIDE the fail-open boundary, so a
    framework object that did not match the expected shape (a non-list
    ``messages``, a content block whose ``text`` was None) raised into the app's
    turn — the contract the module promises to hold."""
    node = _lg(_reflex())
    assert isinstance(node(state), dict)


def test_lone_surrogate_costs_one_codepoint_not_the_whole_window():
    """A lone surrogate cannot be UTF-8 encoded, and the reflex's own validator
    encodes every turn — so one bad codepoint used to raise and discard the
    ENTIRE window, the usable turns included. Plain ``str`` is the declared
    content type: a truncated surrogate pair (``json.loads('"\\ud83d"')``) or a
    ``surrogateescape`` decode produces this without any hostile object."""
    reflex, seen = _spy()
    _lg(reflex)({"messages": [_Msg("human", "ok \ud800"), _Msg("human", "latest")]})
    window = seen["window"]
    assert [turn["content"] for turn in window] == ["ok", "latest"]
    # and the window the real validator gets is encode-safe
    from tortoise.volunteer import validate_request
    validate_request(window)


def test_trailing_surrogate_does_not_re_expose_a_whitespace_tail():
    """The surrogate drop runs BEFORE the strip, and the order is load-bearing.

    Dropping a trailing lone surrogate re-exposes the whitespace run that
    ``rstrip`` had already removed, and the byte fit keeps the TAIL — so with
    the two swapped, an over-long turn whose tail is whitespace was dropped
    wholesale (`continue`), emptying a single-turn window and discarding the
    newest turn in a multi-turn one.
    """
    import tortoise.framework as fw

    content = "X" + " " * (fw.MAX_WINDOW_BYTES - fw.PER_TURN_OVERHEAD_BYTES + 1) + "\ud800"
    assert fw._bounded_window([{"role": "user", "content": content}]) == [
        {"role": "user", "content": "X"}]
    assert fw._bounded_window([
        {"role": "user", "content": "OLDER REAL TURN"},
        {"role": "user", "content": content},
    ]) == [{"role": "user", "content": "OLDER REAL TURN"},
           {"role": "user", "content": "X"}]  # the newest turn survives too


def test_adk_model_turns_are_normalized_not_dropped():
    """ADK's assistant role is literally ``model``; unnormalized, every ADK
    assistant turn was filtered out of the window before the reflex saw it."""
    reflex, seen = _spy()
    adk_before_model_callback(reflex)(
        None, _AdkRequest([("user", "hi"), ("model", "assistant reply"),
                           ("user", "a question")]))
    assert [t["role"] for t in seen["window"]] == ["user", "assistant", "user"]
    assert seen["window"][1]["content"] == "assistant reply"


def test_non_string_content_is_coerced_not_raised():
    """Both window builders are TOTAL: a non-str ``text``/``content`` becomes
    text rather than a ``TypeError`` reaching the app's turn."""
    class _BadPart:
        text: ClassVar[int] = 123

    class _BadContent:
        role: ClassVar[str] = "user"
        parts: ClassVar[list] = [_BadPart()]

    class _BadRequest:
        contents: ClassVar[list] = [_BadContent()]

    reflex, seen = _spy()
    adk_before_model_callback(reflex)(None, _BadRequest())
    assert seen["window"] == [{"role": "user", "content": "123"}]

    reflex2, seen2 = _spy()
    _lg(reflex2)({"messages": [_Msg("human", [{"type": "text", "text": 123}])]})
    assert seen2["window"] == [{"role": "user", "content": "123"}]


def test_empty_window_never_calls_the_reflex():
    calls = []

    def reflex(window):
        calls.append(window)
        return {"block": "REFLEX BLOCK", "degraded_reason": None}

    assert _lg(reflex)({"messages": []}) == {}
    assert _lg(reflex)({}) == {}
    assert _lg(reflex)({"messages": [_Msg("human", "   ")]}) == {}
    assert calls == []


# ── HTTP provider (POST /v1/context) ───────────────────────────────────────


class _ContextRecorder(BaseHTTPRequestHandler):
    captured: ClassVar[dict] = {}

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        type(self).captured = {
            "path": self.path,
            "auth": self.headers.get("Authorization"),
            "body": json.loads(self.rfile.read(length)),
        }
        payload = json.dumps({
            "pointers": [], "why": [], "surfaced": [],
            "block": "HTTP REFLEX BLOCK", "degraded_reason": None,
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):  # keep the test output clean
        return None


def _stub_context_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ContextRecorder)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


class _Redirector(BaseHTTPRequestHandler):
    """Answers every POST with a 302 to ANOTHER host (a second local server)."""

    sink_url: ClassVar[str] = ""

    def do_POST(self):
        self.send_response(302)
        self.send_header("Location", type(self).sink_url)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):
        return None


class _SinkRecorder(BaseHTTPRequestHandler):
    """The redirect target: records anything that reaches it, ANY method.

    ``do_GET`` matters: urllib rewrites a 302-POST into a **GET** on the
    redirected request, so a POST-only sink never sees the forwarded request and
    the leak test passes vacuously against the vulnerable opener.
    """

    seen: ClassVar[list] = []

    def _record(self):
        length = int(self.headers.get("Content-Length") or 0)
        type(self).seen.append({
            "method": self.command,
            "path": self.path,
            "auth": self.headers.get("Authorization"),
            "body": self.rfile.read(length),
        })
        payload = json.dumps(
            {"block": "LEAKED BLOCK", "degraded_reason": None}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    do_POST = _record
    do_GET = _record

    def log_message(self, *args):
        return None


def test_http_reflex_posts_the_canonical_body_and_injects():
    server, thread = _stub_context_server()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        reflex = http_reflex(base + "/", "tsk_test", min_confidence=0.8)
        out = _lg(reflex)({"messages": [_Msg("human", _PROMPT)]})
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert out["messages"][0].content == "HTTP REFLEX BLOCK"
    captured = _ContextRecorder.captured
    assert captured["path"] == "/v1/context"
    assert captured["auth"] == "Bearer tsk_test"
    assert captured["body"]["window"] == [{"role": "user", "content": _PROMPT}]
    assert captured["body"]["min_confidence"] == 0.8


def test_http_reflex_unreachable_is_fail_open():
    reflex = http_reflex("http://127.0.0.1:1", "tsk_test")
    assert _lg(reflex)({"messages": [_Msg("human", _PROMPT)]}) == {}


def test_http_reflex_refuses_a_redirect_and_never_forwards_the_credential():
    """A 3xx must NOT be followed with the credential attached.

    urllib follows redirects by default and copies the request headers onto the
    redirected request — only Content-Length/Content-Type are stripped — so a
    configured endpoint answering 302 to another host (an http->https
    canonicalising proxy, a misconfigured or compromised endpoint, a typo'd
    ``base_url``) would send ``Authorization: Bearer <key>`` to a host the app
    never named AND inject that host's response body into the prompt.
    """
    _SinkRecorder.seen = []
    sink = ThreadingHTTPServer(("127.0.0.1", 0), _SinkRecorder)
    sink_thread = threading.Thread(target=sink.serve_forever, daemon=True)
    sink_thread.start()
    _Redirector.sink_url = f"http://127.0.0.1:{sink.server_address[1]}/evil"
    redirector = ThreadingHTTPServer(("127.0.0.1", 0), _Redirector)
    red_thread = threading.Thread(target=redirector.serve_forever, daemon=True)
    red_thread.start()
    try:
        reflex = http_reflex(
            f"http://127.0.0.1:{redirector.server_address[1]}", "tsk_SUPER_SECRET")
        out = _lg(reflex)({"messages": [_Msg("human", _PROMPT)]})
    finally:
        for server, thread in ((redirector, red_thread), (sink, sink_thread)):
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    assert out == {}, "a redirect must inject nothing, not the target's body"
    assert _SinkRecorder.seen == [], (
        "the credential was forwarded to another host (note: urllib rewrites the "
        "redirected request to GET, so the sink must record EVERY method)")


# ── Opt-in + dependency-free (W4 user-exposure gate) ───────────────────────


def test_import_registers_nothing_and_pulls_in_no_framework():
    code = (
        "import sys\n"
        "import tortoise\n"
        "assert 'tortoise.framework' not in sys.modules, 'tortoise auto-imported the middleware'\n"
        "import tortoise.framework\n"
        "roots = {m.split('.')[0] for m in sys.modules}\n"
        "pulled = roots & {'google', 'langgraph', 'langchain_core', 'semantic_kernel'}\n"
        "assert not pulled, f'framework imported at import time: {sorted(pulled)}'\n"
        "print('clean')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().endswith("clean")
