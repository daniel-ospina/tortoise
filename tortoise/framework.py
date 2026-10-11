"""Framework injection middleware — volunteering memory at the framework seams.

Issue #2125 (epic #2080 end-state platform). A framework app owns its own
sessions and files them with the EXISTING SDK/HTTP ingestion (``POST /v1/sessions``)
— there is no harness session format to parse here. The only missing piece per
framework is the per-turn **injection** hook, and this module supplies one thin,
fail-open hook per documented extension point:

==========================  ============================  ============================
Framework                   Extension point               Adapter
==========================  ============================  ============================
Google ADK                  ``before_model_callback``     :func:`adk_before_model_callback`
LangGraph (LangChain)       pre-model graph node          :func:`langgraph_pre_model_node`
Semantic Kernel             Prompt Render Filter          :func:`semantic_kernel_prompt_render_filter`
==========================  ============================  ============================

All three call the ONE canonical reflex (``tortoise/volunteer.py``) through a
provider the app supplies — :func:`sdk_reflex` (in-process
``TortoiseSDK.volunteer_context()``) or :func:`http_reflex` (``POST /v1/context``).
There is no second pipeline here: the middleware only moves the returned
``block`` into the framework's prompt.

Contracts (issue #2125 O/I/T)
-----------------------------
* **Injection only.** No session parsing, no graph writes, no LLM on the hot
  path, no new transport — the reflex is already built (#2103/#2104).
* **Fail-open content.** Any failure — unreachable, misconfigured, degraded
  (``degraded_reason`` set), clean-empty, or a framework object that does not
  match the expected shape — injects NOTHING and never raises into the app's
  turn. Only the app's own framework call (``next``) keeps its errors.
* **Opt-in (W4 user-exposure gate).** Nothing registers itself and nothing is
  auto-discovered: importing ``tortoise.framework`` pulls in NO framework and
  installs NO hook. The app must construct a provider and register the returned
  hook explicitly — the framework equivalent of the ``install`` command's
  "nothing auto-registers" posture.
* **Dependency-free.** Adapters are duck-typed; a framework's own message type
  is imported lazily, only in the branch that needs it. Tortoise gains no
  framework dependency.

One-line agent-installable registration::

    # Google ADK
    from tortoise.framework import adk_before_model_callback, sdk_reflex
    agent = LlmAgent(..., before_model_callback=adk_before_model_callback(sdk_reflex(sdk)))

    # LangGraph
    from tortoise.framework import langgraph_pre_model_node, sdk_reflex
    graph.add_node("volunteer", langgraph_pre_model_node(sdk_reflex(sdk)))
    graph.add_edge("volunteer", "model")

    # Semantic Kernel
    from semantic_kernel.filters import FilterTypes
    from tortoise.framework import semantic_kernel_prompt_render_filter, sdk_reflex
    kernel.add_filter(FilterTypes.PROMPT_RENDERING,
                      semantic_kernel_prompt_render_filter(sdk_reflex(sdk)))
"""
from __future__ import annotations

import json
import logging
import urllib.request
from collections.abc import Callable
from typing import Any

from .volunteer import MAX_WINDOW_BYTES, PER_TURN_OVERHEAD_BYTES

logger = logging.getLogger(__name__)

# Window bounds. The reflex validates 1..1000 turns / <= 15 KB and raises on
# out-of-contract input (tortoise/volunteer.py::validate_request), so the
# middleware bounds the window it builds rather than letting a long framework
# conversation degrade to silent nothing.
#
# The BYTE budget is IMPORTED from the reflex, never restated: validate_request
# counts UTF-8 bytes plus PER_TURN_OVERHEAD_BYTES per turn, so a character
# count — or a byte count that forgot the per-turn overhead — builds a window
# the reflex REJECTS. The rejection is swallowed by the fail-open boundary, so
# the symptom is not an error but a silent no-op in exactly the long
# conversation the bound exists for (and multibyte content, accented Spanish
# included, trips it about twice as early).
MAX_WINDOW_TURNS = 20
HTTP_TIMEOUT_S = 15.0

# A provider takes a canonical window (list of {role, content}) and returns a
# canonical volunteer response (§3.2.2). It MAY raise — the adapters fail open.
Reflex = Callable[[list[dict]], Any]


# ── The injectable block (fail-open boundary) ──────────────────────────────

def block_from(response: Any) -> str:
    """The injectable block of a canonical volunteer response, else ``""``.

    A degraded response (``degraded_reason`` set) and a clean-empty one (no
    pointers above the confidence gate) both inject nothing — the reflex's
    fail-open posture, identical to the shipped harness hook's bare-block
    default.
    """
    if not isinstance(response, dict) or response.get("degraded_reason"):
        return ""
    block = response.get("block")
    return block if isinstance(block, str) and block.strip() else ""


def _safe_block(reflex: Reflex, window: list[dict]) -> str:
    """Run the reflex and reduce every outcome to a string that is safe to inject."""
    if not window:
        return ""
    try:
        return block_from(reflex(window))
    except Exception:
        logger.debug("framework reflex failed — injecting nothing", exc_info=True)
        return ""


# ── Providers ──────────────────────────────────────────────────────────────

def sdk_reflex(sdk: Any, **options: Any) -> Reflex:
    """Provider over the in-process ``TortoiseSDK.volunteer_context()``.

    ``options`` are forwarded verbatim (``session_id``, ``prior_context``,
    ``min_confidence``, ``max_pointers``, ``why``).
    """
    def reflex(window: list[dict], **kwargs: Any) -> Any:
        return sdk.volunteer_context(window=window, **{**options, **kwargs})
    return reflex


def http_reflex(base_url: str, api_key: str | None = None, **options: Any) -> Reflex:
    """Provider over ``POST /v1/context`` (hosted or self-host).

    The endpoint and the credential are EXPLICIT arguments on purpose: the
    framework middleware runs inside the app's process, so it resolves no
    ambient credential chain — the app hands it the transport identity it
    already owns (the CLI/hook identity chain, #2369 D1, is for the hook
    process, not for embedded middleware).
    """
    url = base_url.rstrip("/") + "/v1/context"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    def reflex(window: list[dict], **kwargs: Any) -> Any:
        req = urllib.request.Request(
            url,
            data=json.dumps({"window": window, **{**options, **kwargs}}).encode(),
            headers=headers,
        )
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
            return json.loads(resp.read())
    return reflex


# ── Window builders ────────────────────────────────────────────────────────

def _bounded_window(turns: list[Any]) -> list[dict]:
    """Keep the last ``MAX_WINDOW_TURNS`` non-empty turns within the reflex's byte bound.

    Truncation keeps the END of an over-long turn (the turn being answered,
    which is what the reflex resolves against) and drops the OLDEST turns
    first — the same discipline the shipped hook script applies.

    The budget is the REFLEX's own: ``MAX_WINDOW_BYTES`` against the UTF-8
    bytes of the content plus ``PER_TURN_OVERHEAD_BYTES`` per turn
    (tortoise/volunteer.py::validate_request). Bounding a different quantity
    than the validator measures is not a smaller bound — it is no bound at
    all, because the overflow path is a swallowed validation error.
    """
    kept: list[dict] = []
    total = 0
    for turn in reversed(turns or []):
        if not isinstance(turn, dict):
            continue
        role, content = turn.get("role"), turn.get("content")
        if role not in ("user", "assistant", "system"):
            continue
        if not isinstance(content, str) or not content.strip():
            continue
        if len(kept) >= MAX_WINDOW_TURNS:
            break
        # Room left for CONTENT once this turn's own overhead is reserved.
        room = MAX_WINDOW_BYTES - total - PER_TURN_OVERHEAD_BYTES
        if room <= 0:
            break
        encoded = content.encode("utf-8")
        if len(encoded) > room:
            # Slice BYTES and drop a split leading character: slicing
            # characters would let a multibyte turn exceed the byte budget it
            # was truncated to fit.
            content = encoded[-room:].decode("utf-8", errors="ignore")
            encoded = content.encode("utf-8")
        if not content.strip():
            break
        kept.append({"role": role, "content": content})
        total += len(encoded) + PER_TURN_OVERHEAD_BYTES
    kept.reverse()
    return kept


# ADK's own role vocabulary is ``user`` / ``model``; the canonical window's is
# ``user`` / ``assistant`` / ``system``. Normalizing here is what keeps an
# assistant turn from being filtered out of every ADK window.
_ADK_ROLES = {"model": "assistant"}


def _window_from_adk(llm_request: Any) -> list[dict]:
    """ADK ``LlmRequest.contents`` (``types.Content`` with text parts) → window.

    ADK's own role vocabulary is ``user`` / ``model``; the canonical window's is
    ``user`` / ``assistant`` / ``system``, so ``model`` is normalized the same
    way the LangGraph adapter normalizes ``ai``. Without it every assistant turn
    is filtered out by :func:`_bounded_window` and the reflex sees a user-only
    window.
    """
    turns = []
    for content in getattr(llm_request, "contents", None) or []:
        text = "".join(
            getattr(part, "text", "") or ""
            for part in (getattr(content, "parts", None) or [])
        )
        role = getattr(content, "role", None) or "user"
        turns.append({"role": _ADK_ROLES.get(role, role), "content": text})
    return _bounded_window(turns)


_LANGGRAPH_ROLES = {
    "human": "user", "ai": "assistant", "system": "system",
    "tool": "system",  # tool results are recall support, not a user turn
}


def _window_from_langgraph(state: Any) -> list[dict]:
    """LangGraph state (``{"messages": [...]}``) → window.

    TOTAL by construction: a state that does not match the expected shape
    yields only the turns it CAN yield and never raises. The caller also keeps
    this inside its fail-open boundary — the two together are what make the
    module's "never raises into the app's turn" contract hold for a framework
    object of any shape, not just the ones with a test.
    """
    messages = state.get("messages") if isinstance(state, dict) else None
    if not isinstance(messages, (list, tuple)):
        return []
    turns = []
    for message in messages:
        role = getattr(message, "type", None) or type(message).__name__.lower()
        content = getattr(message, "content", None)
        if isinstance(content, list):  # multimodal content blocks
            content = "".join(
                (block.get("text") or "")
                for block in content if isinstance(block, dict)
            )
        turns.append({
            "role": _LANGGRAPH_ROLES.get(role, "user"),
            "content": content if isinstance(content, str) else str(content or ""),
        })
    return _bounded_window(turns)


# ── Google ADK — before_model_callback ─────────────────────────────────────

def _adk_inject(llm_request: Any, block: str) -> None:
    """Append the block to the LLM request via ADK's own documented mutators."""
    append = getattr(llm_request, "append_instructions", None)
    if callable(append):
        append(block)  # docs: ADK callbacks may modify the LlmRequest before the call
        return
    from google.genai import types  # lazy: optional dependency

    llm_request.contents.append(
        types.Content(role="user", parts=[types.Part(text=block)])
    )


def adk_before_model_callback(reflex: Reflex) -> Callable:
    """Google ADK ``before_model_callback`` factory.

    ``LlmAgent(..., before_model_callback=adk_before_model_callback(reflex))``.
    Runs before each LLM call with a mutable ``LlmRequest``; the block is
    appended to the request's instructions, so it is part of the prompt the
    model sees. Returns ``None`` so the model call proceeds — the reflex NEVER
    short-circuits the agent.
    """
    def before_model_callback(callback_context: Any, llm_request: Any) -> None:
        try:
            block = _safe_block(reflex, _window_from_adk(llm_request))
            if block:
                _adk_inject(llm_request, block)
        except Exception:
            logger.debug("ADK injection failed — injecting nothing", exc_info=True)
        return None
    return before_model_callback


# ── LangGraph — pre-model node ─────────────────────────────────────────────

def _langchain_human_message(text: str) -> Any:
    from langchain_core.messages import HumanMessage  # lazy: optional dependency

    return HumanMessage(content=text)


def langgraph_pre_model_node(
    reflex: Reflex, *, message_factory: Callable[[str], Any] | None = None,
) -> Callable:
    """LangGraph pre-model node factory.

    ``graph.add_node("volunteer", langgraph_pre_model_node(reflex))`` then wire
    it immediately before the model node. The node returns the reflex block as
    one appended message and ``{}`` (no state change) when there is nothing to
    inject.

    The default message type is ``langchain_core.messages.HumanMessage``:
    appending a second ``SystemMessage`` is rejected by some chat models, so
    the safe default is a trailing user turn. Pass ``message_factory`` to use
    another type (e.g. ``SystemMessage``, or a tool-role message) when the
    graph's model accepts it.
    """
    def volunteer_pre_model(state: Any) -> dict:
        # The window BUILDER is inside the boundary too: a framework object
        # that does not match the expected shape must inject nothing, not raise
        # into the app's turn (the ADK adapter is shaped the same way).
        try:
            block = _safe_block(reflex, _window_from_langgraph(state))
            if not block:
                return {}
            make = message_factory or _langchain_human_message
            return {"messages": [make(block)]}
        except Exception:
            logger.debug("LangGraph injection failed — injecting nothing", exc_info=True)
            return {}
    return volunteer_pre_model


# ── Semantic Kernel — Prompt Render Filter ─────────────────────────────────

def semantic_kernel_prompt_render_filter(reflex: Reflex) -> Callable:
    """Semantic Kernel Prompt Render Filter factory.

    ``kernel.add_filter(FilterTypes.PROMPT_RENDERING, f)`` (or
    ``@kernel.filter(FilterTypes.PROMPT_RENDERING)``). Per the documented
    filter order the filter awaits ``next`` first — the prompt is rendered by
    the remaining pipeline — and then PREPENDS the reflex block to the rendered
    prompt before it reaches the model. A filter error from ``next`` is the
    app's own and propagates; only Tortoise's half is fail-open.
    """
    async def prompt_render_filter(context: Any, next: Callable) -> None:
        await next(context)
        try:
            rendered = getattr(context, "rendered_prompt", None) or ""
            # Through the SAME bounded builder as the other two adapters: the
            # rendered prompt is one turn, and an over-cap one is rejected by
            # the reflex and silently injects nothing.
            block = _safe_block(
                reflex, _bounded_window([{"role": "user", "content": rendered}]))
            if block:
                context.rendered_prompt = f"{block}\n\n{rendered}"
        except Exception:
            logger.debug(
                "Semantic Kernel injection failed — injecting nothing", exc_info=True)
    return prompt_render_filter
