"""Product retrieval-quality primitives: pool depth, context assembly, boost.

The product inversion (fix/invert-retrieval-to-product, docs/audit/
2026-08-29-product-cohesion.md retrieval PARTIAL section): the LongMemEval
harness (tools/longmem_eval/) proved several retrieval-quality knobs that
never shipped — the product ran a weaker default while the benchmark
measured the eval-side copy. This module moves that quality logic INTO the
product so the product owns it and the harness becomes a thin caller that
imports (and measures) these capabilities:

  * **Pool depth (#1947, audit G2)** — ``resolve_pool_size`` with the
    product's own ``DEFAULT_POOL_SIZE = 120``. The SDK's ``tortoise_fts_query``
    now bakes the floor at 120 (the audit G2 recommendation: at the
    hosted/MCP default limit=10 the fusion window is 120 candidates, not
    the historical 20); the eval passes its ``TORTOISE_LME_POOL_SIZE``
    measurement knob through the same resolution.
  * **Rank-interleaved context assembly (C1 #1745, audit G7)** —
    ``assemble_context`` builds the budget-capped, rank-interleaved reader
    context (points and raw chunks in true RRF rank order — a chunk ranked
    above a point enters the context at its rank, not after all points),
    bounded by BOTH a token budget and an explicit item cap;
    ``render_context`` renders it (with the ``Current Date:`` header) and
    ``dedup_pool`` applies the per-session raw-chunk cap (C5 #1745: 3).
  * **Evidence-mark boost (C2 #1745 / #1945, audit — eval-only)** —
    ``apply_evidence_boost`` re-ranks marked hits up by a stable rank
    offset (position-ceiling promotion) so marked evidence can surface into
    the top-k the reader sees. OFF by default in the product (the plan's
    fail-safe default decision, #1745): callers opt in. Marks are provided
    via the ``mark_for`` callable — the product's stored-``has_answer``
    fallback assigns the conservative source class; the eval injects its
    read-time recompute (dataset-derived marks, ``evidence.mark_for``).

These capabilities are pure functions over annotated hit dicts — no graph
dependency — so MCP/SDK consumers and the eval share the identical code.

The retry predicate (``retryable_transient`` / ``call_with_predicate``)
lives in its own product module, ``tortoise/retry.py`` (the #1806 write-
path resilience primitive); wiring it into the SDK write path is a
documented follow-up.
"""
from __future__ import annotations

import math
import os
import re
from collections.abc import Callable
from typing import Any

#: token-count estimator (matches the reader-context alignment invariant):
#: rough LLM token ≈ whitespace tokens, plus a 10% markup allowance for
#: role prefixes/JSON.
TOKEN_ESTIMATOR = "whitespace-tokens + 10% markup allowance"

#: #1947: the product's pool fetch depth. Deepened 60→120 after the
#: LongMemEval re-validation (66% of marked evidence points never entered
#: the 60-item pool, so the evidence boost had no material to work with).
#: The product OWNS this number (audit G2 — baking the floor at 120 is the
#: single highest-leverage retrieval change): ``tortoise_fts_query`` floors
#: its candidate window at it, and the eval measures the same default.
#: The deepest recall horizon (``max(ks)`` for the harness) is always the
#: floor — recall@k is computed over the deduped pool, so a knob below the
#: deepest measured surface can never silently truncate it.
DEFAULT_POOL_SIZE = 120

#: C5 (#1745): per-session raw-chunk cap in the retrieval pool. 2 -> 3 —
#: the R1 cap was capping out the evidence chunk on ~18 LongMemEval
#: questions (chunk_evidence_recall@20 = 0 with evidence_recall@20 high);
#: only 4/854 sessions have >2 marked chunks, so the budget cost is
#: bounded.
DEFAULT_MAX_CHUNKS_PER_SESSION = 3

#: C1 (#1745): reader-context ITEM cap (default 40). The measured ~114
#: tok/item means a 60-item pool (~6.8k tokens) may not bind the 8k token
#: budget, so an unceilinged budget walk would flood the reader; the item
#: cap bounds reader flood while the token budget selects within it (top-k
#: saturation research, plan §3). Temporal-reasoning callers keep their own
#: pinned cap (the eval's ``tr_top_k``) — flood control is never silently
#: undone by the budget walk.
DEFAULT_CONTEXT_ITEM_CAP = 40

#: R1 (#1540): reader-context token budget (≈ the pre-v2 baseline context
#: size — a 4.4x reduction from the measured 35k whole-session flood;
#: LightMem: compact evidence wins under tight budgets).
DEFAULT_CONTEXT_TOKEN_CAP = 8000

#: C2 (#1745) / #1945: evidence-mark boost rank-offset multipliers. The
#: answer-string mark (d, #1763 — the point's content carries the GOLD
#: ANSWER, the strongest/answer-precise signal) gets the highest
#: multiplier, verbatim/raw-chunk marks (the precise ones) the full boost,
#: source-session-only points a reduced one. Marks never influence RRF
#: ranking (content-similarity-only fusion) — the boost is the
#: evidence-aware assist. Knobs
#: ``TORTOISE_POOL_...``-free: callers pass explicit floats; the eval's
#: ``TORTOISE_LME_EVIDENCE_BOOST_*`` env knobs resolve eval-side and thread
#: through.
DEFAULT_EVIDENCE_BOOST_ANSWER_STRING = 2.0
DEFAULT_EVIDENCE_BOOST_VERBATIM = 1.5
DEFAULT_EVIDENCE_BOOST_SOURCE = 1.15

#: TORTOISE_POOL_FLOOR env upper/lower clamp (matches the SDK's limit
#: validation bound 1..10000).
_POOL_CLAMP = (1, 10000)

#: A6 (#2070): ask-lane cap env names. The retrieval-window limit and the
#: assembly caps are threaded IN TANDEM — raising only the assemble cap
#: changes NOTHING (the gold is cut at ``result_ids[:limit]`` INSIDE
#: ``tortoise_fts_query`` before dedup/assemble); raising only the window
#: floods the reader budget. Measurement-gated: default OFF = the historical
#: 40/40/8000 (byte-identical until the Step-0/6 measurements justify a
#: raise).
ASK_RETRIEVAL_LIMIT_ENV = "TORTOISE_ASK_RETRIEVAL_LIMIT"
ASK_CONTEXT_ITEM_CAP_ENV = "TORTOISE_ASK_CONTEXT_ITEM_CAP"
ASK_CONTEXT_TOKEN_CAP_ENV = "TORTOISE_ASK_CONTEXT_TOKEN_CAP"

#: A1/A4/A5 (#2070): ask-lane lever env names (all default ON for the ask
#: lane — each is a quality fix, not a gated experiment; "0"/"false"/
#: "no"/"off" opts out). A7's rerank is the exception (env-gated OFF,
#: tortoise/rerank.py).
ASK_NUMERIC_TOKENS_ENV = "TORTOISE_ASK_NUMERIC_TOKENS"
ASK_SEARCH_KEYS_PRF_ENV = "TORTOISE_ASK_SEARCH_KEYS_PRF"
ASK_EVIDENCE_BOOST_ENV = "TORTOISE_ASK_EVIDENCE_BOOST"
ASK_FUSION_WEIGHTS_ENV = "TORTOISE_ASK_FUSION_WEIGHTS"
ASK_FUSION_K_ENV = "TORTOISE_ASK_FUSION_K"

#: A1/A3/A5/A6 knob env values: explicit 1/true/yes/on flips True, explicit
#: 0/false/no/off flips False, anything else (unset OR garbage) falls back
#: to ``default`` — a typo can never silently flip a knob.
_ASK_TRUTHY = {"1", "true", "yes", "on"}
_ASK_FALSY = {"0", "false", "no", "off"}


def ask_env_bool(name: str, default: bool) -> bool:
    """Ask-lane env bool with a caller default (A1/A4/A5/A7 knob parsing).
    Unset/blank/garbage → ``default`` (a typo never flips a knob); explicit
    truthy (1/true/yes/on) → True; explicit falsy (0/false/no/off) → False.
    """
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    if raw in _ASK_TRUTHY:
        return True
    if raw in _ASK_FALSY:
        return False
    return default


def ask_env_int(name: str, default: int, lo: int = 1, hi: int | None = None) -> int:
    """Ask-lane env int with clamp: garbage or out-of-range values fall back
    to ``default`` — never a crash (mirrors the eval's ``_env_int``)."""
    raw = os.environ.get(name, "")
    if not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except (TypeError, ValueError):
        return default
    if value < lo or (hi is not None and value > hi):
        return default
    return value


def ask_env_weights(name: str, default: dict | None) -> dict | None:
    """Ask-lane JSON dict parse for the weighted-RRF knob
    (``TORTOISE_ASK_FUSION_WEIGHTS``, A3 #2070). Garbage/unparseable →
    ``default`` (None = the shared global resolution in tortoise_fts_query
    — the shipped ``{"vector": 1.5}``)."""
    raw = os.environ.get(name, "")
    if not raw.strip():
        return default
    try:
        import json as _json
        val = _json.loads(raw)
    except (TypeError, ValueError):
        return default
    if not isinstance(val, dict):
        return default
    try:
        return {str(k): float(v) for k, v in val.items()}
    except (TypeError, ValueError):
        return default


def ask_env_boost_float(name: str, default: float) -> float:
    """Ask-lane evidence-boost multiplier env (A5 #2070): domain [1.0, inf),
    finite — a factor < 1.0 is a rank DIVISION (0.0 → ZeroDivisionError;
    negative → silent pool inversion; NaN/Inf → poisoned sort keys), so
    out-of-domain values fall back to ``default`` (mirrors the eval's
    ``_env_boost_float``)."""
    raw = os.environ.get(name, "")
    if not raw.strip():
        return default
    try:
        value = float(raw.strip())
    except (TypeError, ValueError):
        return default
    if not (math.isfinite(value) and value >= 1.0):
        return default
    return value


def resolve_ask_retrieval_caps() -> dict:
    """A6 (#2070): resolve the ask lane's retrieval-window limit + assembly
    caps IN TANDEM (env-gated, default OFF = 40/40/8000). Returns
    ``{"limit", "context_item_cap", "context_token_cap"}`` — the single
    resolution ``ask()`` threads into BOTH ``tortoise_fts_query(limit=…)``
    (the ``result_ids[:limit]`` cut INSIDE the retrieval call) and
    ``assemble_context``, so a cap raise can never be half-applied."""
    return {
        "limit": ask_env_int(ASK_RETRIEVAL_LIMIT_ENV, DEFAULT_CONTEXT_ITEM_CAP),
        "context_item_cap": ask_env_int(
            ASK_CONTEXT_ITEM_CAP_ENV, DEFAULT_CONTEXT_ITEM_CAP),
        "context_token_cap": ask_env_int(
            ASK_CONTEXT_TOKEN_CAP_ENV, DEFAULT_CONTEXT_TOKEN_CAP),
    }


def resolve_ask_boost_multipliers() -> dict:
    """A5 (#2070): the ask lane's evidence-boost multipliers — the product
    defaults (2.0 / 1.5 / 1.15) with ``TORTOISE_ASK_EVIDENCE_BOOST_*`` env
    overrides (domain [1.0, inf), finite)."""
    return {
        "answer_string": ask_env_boost_float(
            "TORTOISE_ASK_EVIDENCE_BOOST_ANSWER_STRING",
            DEFAULT_EVIDENCE_BOOST_ANSWER_STRING),
        "verbatim": ask_env_boost_float(
            "TORTOISE_ASK_EVIDENCE_BOOST_VERBATIM",
            DEFAULT_EVIDENCE_BOOST_VERBATIM),
        "source": ask_env_boost_float(
            "TORTOISE_ASK_EVIDENCE_BOOST_SOURCE",
            DEFAULT_EVIDENCE_BOOST_SOURCE),
    }


def resolve_pool_size(
    floor: int,
    *,
    pool_size: int | None = None,
    env_name: str | None = None,
    default: int = DEFAULT_POOL_SIZE,
    exact: bool = True,
) -> int:
    """Resolve a retrieval pool depth: explicit ``pool_size`` > env
    (``env_name``) > ``default`` (the product's ``DEFAULT_POOL_SIZE``).

    Shared by the SDK's ``tortoise_fts_query`` and the eval harness so the
    pool-depth decision has ONE owner. Two caller contracts, one function:

    * ``exact=True`` (the SDK): an explicit ``pool_size`` is an EXACT
      override — a value below ``limit*2`` LOWERS the candidate window to
      ``pool_size`` (the env floor only RAISES); otherwise returns
      ``max(resolved, floor)`` where the caller's ``floor`` is ``limit*2``.
      The env knob is ``TORTOISE_POOL_FLOOR``.
    * ``exact=False`` (the eval): the caller's ``floor`` (the deepest
      recall horizon ``max(ks)``) is ALWAYS honored — a knob below the
      deepest measured k can never truncate the pool the recall metrics
      measure. The env knob is the eval's ``TORTOISE_LME_POOL_SIZE``.

    Env parsing is fail-safe: unset/blank/garbage/<1 values fall back to
    ``default``; the resolved value is clamped to [1, 10000].
    """
    if pool_size is not None:
        resolved = pool_size
    elif env_name is not None:
        raw = os.environ.get(env_name, "")
        resolved = default
        if raw.strip():
            try:
                value = int(raw.strip())
            except (TypeError, ValueError):
                value = default
            resolved = value if value >= 1 else default
    else:
        resolved = default
    resolved = max(_POOL_CLAMP[0], min(resolved, _POOL_CLAMP[1]))
    if exact and pool_size is not None:
        return resolved
    return max(resolved, floor)


def is_raw_chunk(h: dict) -> bool:
    """True for a raw verbatim chunk (pointKind ``session-transcript``).
    Points of every other kind (extracted statements, episodic turn points)
    are the compact epistemic surface (D3 #1540: never chunk-capped)."""
    return h.get("point_kind") == "session-transcript"


def dedup_pool(annotated: list[dict], *,
               max_chunks_per_session: int,
               session_key: Callable[[dict], str] | None = None) -> list[dict]:
    """Per-session chunk cap (rank order): at most ``max_chunks_per_session``
    raw chunks per session survive in the pool (E2E-1 #1540). Bucket key =
    the hit's session_id when present, else its lme_session_index —
    distinct sessions NEVER share a bucket (no ``-1`` collapse).
    Points/turn points are never capped (compact epistemic surface, D3).

    ``session_key`` (#1987 Task 4, P2-20): optional per-hit key extractor.
    Default None = the historical bucket key (session_id / lme index). The
    ASK lane passes a key extractor preferring the UNIQUE session identifier
    (``session_id`` from the Event join, falling back to the annotated
    ``session_date``) — distinct same-day sessions never collapse into one
    bucket; hits LACKING ``sessionId`` but sharing an Event-derived session
    date still cap together (pre-annotation dedup could not group them).
    """
    if max_chunks_per_session < 1:
        raise ValueError("max_chunks_per_session must be >= 1, got "
                         f"{max_chunks_per_session!r}")
    if session_key is None:
        def _key(h: dict) -> str:
            return (h.get("session_id") or
                    f"idx:{h.get('lme_session_index', -1)}")
    else:
        _key = session_key
    seen: dict[str, int] = {}
    pool: list[dict] = []
    for h in annotated:
        if is_raw_chunk(h):
            key = _key(h)
            if seen.get(key, 0) >= max_chunks_per_session:
                continue
            seen[key] = seen.get(key, 0) + 1
        pool.append(h)
    return pool


def estimate_tokens(text: str) -> int:
    """Rough LLM token estimate for a rendered context (whitespace tokens +
    10% markup allowance). ``assemble_context``'s budget accounting uses the
    identical per-block words, so ``estimate_tokens(render_context(...))``
    equals the assembly's ``context_tokens`` exactly (no per-block int
    drift — the alignment invariant, R1 #1540)."""
    return int(len(text.split()) * 1.1)


_NON_WHITESPACE_RUN = re.compile(r"\S+")

#: #1987 Task 6 (P2-1): the ask lane's conservative token estimate for
#: NON-whitespace-delimited runs (CJK/emoji) — ~0.6-0.7 token/char,
#: OVER-estimated versus the DeepSeek rate the meter cites, so the meter can
#: never under-count and the per-query cost stays honestly bounded.
_ASK_CJK_TOKENS_PER_CHAR = 0.65

#: Emoji/other-symbol runs tokenize at ~1.5-2+ tokens per codepoint (ZWJ
#: sequences are the extreme — a 7-codepoint family emoji charges ~7-15
#: tokens). A dedicated per-codepoint floor keeps the estimate conservative
#: (the meter must never under-count).
_ASK_SYMBOL_TOKENS_PER_CHAR = 2.0


def _run_has_symbol(run: str) -> bool:
    """True when a non-whitespace run contains a Unicode Symbol character
    (categories So/Sk — emoji, dingbats, modifier symbols) that tokenizers
    charge per-codepoint (never a single whitespace-token run)."""
    import unicodedata
    return any(unicodedata.category(ch) in ("So", "Sk") for ch in run)


def estimate_tokens_ask(text: str) -> int:
    """The ask lane's conservative token estimator (#1987 Task 6, P2-1).

    The historical ``estimate_tokens`` is whitespace-based
    (``int(len(text.split()) * 1.1)``) and under-counts unspaced CJK/emoji
    runs to ~0 words. The ask lane uses this conservative per-char
    multiplier for non-whitespace-delimited runs — pinned at ~0.6-0.7
    token/char (OVER-estimated versus the DeepSeek rate, so the meter can
    never under-count). Because the multiplier is conservative, on
    CJK-heavy pools the 32 KiB BYTE cap binds FIRST (32 KiB ≈ 10.9K chars ≈
    ~6.5-7.6K estimated tokens < 8000).

    This is a conservative ESTIMATE, never an exact bill; it is the source
    of the response field ``context_tokens`` (the RENDERED-CONTEXT tokens
    only) and the metering input ``input_tokens``.
    """
    if not text:
        return 0
    # whitespace-delimited words: the standard estimate + markup (identical
    # to ``estimate_tokens`` for plain whitespace text)
    base = int(len(text.split()) * 1.1)
    surcharge = 0
    for run in _NON_WHITESPACE_RUN.findall(text):
        if not any(ord(ch) > 127 for ch in run):
            continue  # pure-ASCII runs (normal words) keep the base estimate
        # A NON-ASCII run (any length — threshold 1 so short CJK/emoji runs
        # are covered) is charged a conservative per-char rate; symbol/emoji
        # runs use the higher per-codepoint floor. The run already counted as
        # one whitespace token in ``base``, so the surcharge is the per-char
        # overage beyond that.
        per_char = (_ASK_SYMBOL_TOKENS_PER_CHAR if _run_has_symbol(run)
                    else _ASK_CJK_TOKENS_PER_CHAR)
        char_est = max(1, int(len(run) * per_char))
        surcharge += max(0, char_est - 1)
    return base + surcharge


_ROLE_PREFIX = re.compile(r"^\[(user|assistant|system|tool|unknown)\]\s+",
                             re.IGNORECASE)


def _validity_marker(h: dict) -> str:
    """Validity-window marker text for one hit (E6 #1538, D7).

    Extends the #1367 supersession markers with the promoted window fields:
      - live hit with ``valid_from`` → ``[valid since <from>]``
      - superseded hit → ``[valid <from> → <to>]``; with ``expired_at`` →
        ``[valid <from> → <to>; expired <tx-date>]``
      - undated hits → NO validity marker (byte-identical rendering)
    ISO date strings (YYYY-MM-DD — the dataset/``when`` normalization): no
    full timestamps in the reader context; timestamps stay on the graph
    properties. The supersession markers (SUPERSEDED BY / SUPERSEDES) are
    unchanged and render first."""
    marks: list[str] = []
    sb = h.get("superseded_by") or {}
    snippet = (sb.get("content_snippet") or "").strip()
    if snippet:
        marks.append(f"[SUPERSEDED BY: {snippet}]")
    supersedes = h.get("supersedes") or []
    snips = [(s.get("content_snippet") or "").strip()
             for s in supersedes if (s.get("content_snippet") or "").strip()]
    if snips:
        marks.append("[SUPERSEDES: " + " ; ".join(snips) + "]")
    vf = (h.get("valid_from") or "").strip()
    vt = (h.get("valid_to") or "").strip()
    ex = (h.get("expired_at") or "").strip()
    if vf:
        # ISO date strings only — truncate full timestamps to YYYY-MM-DD.
        vfd = vf[:10] if len(vf) > 10 else vf
        if vt:
            vtd = vt[:10] if len(vt) > 10 else vt
            if ex:
                exd = ex[:10] if len(ex) > 10 else ex
                marks.append(f"[valid {vfd} → {vtd}; expired {exd}]")
            else:
                marks.append(f"[valid {vfd} → {vtd}]")
        else:
            marks.append(f"[valid since {vfd}]")
    return " ".join(marks)


def _render_block(h: dict) -> str:
    """One hit's rendered context block — the SINGLE implementation shared
    by ``render_context`` and the token budget (factored out of
    ``render_context``, R1 #1540). ``question_date`` never appears here: it
    only prepends the ``Current Date:`` header once in ``render_context``.
    Per-hit dates come from the hit's own ``session_date``."""
    idx = h.get("lme_session_index")
    prefix = f"[session {idx}]" if idx is not None and idx >= 0 else "[session ?]"
    sdate = h.get("session_date")
    if sdate:
        prefix = f"{prefix} (session date {sdate})"
    # E3 (#1535): speaker decoration — mirrors the deterministic leg's
    # "[role] text" turn shape so the reader sees who asserted the fact.
    # Unknown → byte-identical rendering (backward-compat). Skip when the
    # content ALREADY carries a role bracket (turn points are written as
    # "[role] text" AND have the speaker prop — decorating both would
    # double-attribute, e.g. "[user] [user] ..." on the deterministic leg's
    # primary recall surface).
    spk = h.get("speaker") or ""
    # only the deterministic leg's own role-bracket shape suppresses the
    # decoration — a non-role bracket prefix ([context], [IMPORTANT])
    # must not suppress speaker attribution
    if spk and not _ROLE_PREFIX.match(h.get("content", "")):
        prefix = f"{prefix} [{spk}]"
    marker = _validity_marker(h)
    if marker:
        # _validity_marker already returns self-bracketed groups
        # (e.g. "[SUPERSEDED BY: x] [valid 2026-06-10 → 2026-06-12]") — no
        # extra wrap.
        prefix = f"{prefix} {marker}"
    return f"{prefix} {h.get('content', '')}"


def assemble_context(
    pool: list[dict], *,
    top_k: int,
    max_context_tokens: int,
    question_date: str | None = None,
    context_item_cap: int | None = None,
    byte_cap: int | None = None,
) -> list[dict]:
    """Budget-capped, rank-interleaved reader context (C1 #1745).

    Iterates the pool in TRUE RRF rank order — extracted points and raw
    chunks interleaved, a chunk ranked above a point enters the context at
    its rank, not after all points (the historical points-first partition
    starved the chunk leg: any pool with >= top_k points dropped chunks
    entirely regardless of rank). Bounded by BOTH the token budget
    (``max_context_tokens``) and an explicit item cap
    (``context_item_cap``; defaults to ``top_k`` for back-compat with
    pure-function callers — the retrieval path passes the resolved
    ``context_item_cap``). ``top_k`` stays "the max number of context
    items" at the default cap.

    ``byte_cap`` (#1987 Task 5, P1-2): keyword-only, default None = unchanged
    behavior (the extraction/search AND eval lanes are unaffected — the eval
    re-export ``assemble_context as _assemble_context`` never passes it). The
    ASK lane passes ``byte_cap=32768``: the assembled evidence is enforced to
    BOTH the 8000-token estimate cap AND a 32 KiB UTF-8 byte cap
    independently, by the SAME mechanism as the token cap — WHOLE-HIT DROP
    (lowest-ranked hits dropped until under budget, never mid-hit character
    truncation), so decoding the evidence never splits a character
    (P2-18) and ``len(evidence.encode("utf-8")) <= 32768`` is a hard output
    invariant by construction.

    Token accounting (the alignment invariant): raw whitespace words
    accumulate per block (question_date-independent) + the once-prepended
    ``Current Date: …`` header words; the 1.1 markup multiplier applies
    ONCE to the joined total, so ``context_tokens ==
    estimate_tokens(render_context(...))`` holds exactly (no per-block
    ``int()`` drift). Oversized hits are SKIPPED (continue), never starving
    the rest of the context.
    """
    if max_context_tokens < 1:
        raise ValueError("max_context_tokens must be >= 1, got "
                         f"{max_context_tokens!r}")
    item_bound = context_item_cap if context_item_cap is not None else top_k
    if item_bound < 1:
        raise ValueError("context_item_cap must be >= 1, got "
                         f"{item_bound!r}")
    if byte_cap is not None and byte_cap < 1:
        raise ValueError("byte_cap must be >= 1, got "
                         f"{byte_cap!r}")
    header_words = (len(f"Current Date: {question_date}".split())
                    if question_date else 0)
    selected: list[dict] = []
    words = header_words
    # The separator framing bytes (P1): render_context joins blocks with
    # "\n\n" AND appends a trailing "\n\n" after the header — account those
    # so ``len(evidence) <= byte_cap`` is a HARD invariant (not just the
    # block bytes). ``+2`` per accepted block is deliberately conservative
    # (over-counts the last block's absent trailing separator by 2 bytes).
    bytes_used = (len(f"Current Date: {question_date}".encode()) + 2) \
        if question_date else 0
    for h in pool:
        if len(selected) >= item_bound:
            break
        block = _render_block(h)
        cost = len(block.split())
        if int((words + cost) * 1.1) > max_context_tokens:
            continue  # skip this hit; keep later ones (no starvation)
        if byte_cap is not None:
            # whole-hit drop under the byte cap — a hit is fully in or fully
            # out; the skip keeps later (lower-ranked) hits' chance like the
            # token cap (no starvation), mirroring the token-budget behavior.
            if bytes_used + len(block.encode("utf-8")) + 2 > byte_cap:
                continue
            bytes_used += len(block.encode("utf-8")) + 2
        selected.append(h)
        words += cost
    return selected


def render_context(hits: list[dict], *, question_date: str | None = None) -> str:
    """Render annotated hits as the reader-facing context text.

    Shared by LLM readers (their prompt input) and the token estimator so
    ``context_tokens`` always matches what the reader actually consumed.

    The rendering follows the OFFICIAL LongMemEval gen.py shape: a
    ``Current Date: {question_date}`` header (the question's date, needed to
    answer temporal-reasoning questions — "how many days ago") and a
    per-session date annotation on every chunk. Without these, TR questions
    are structurally unanswerable (TR ≈ 0% regardless of retrieval) — P1
    #1144.

    #1367: hits carrying the promoted supersession state (superseded_by /
    supersedes — #1353 D8) are annotated so the reader sees "this statement
    replaced that one": a superseded hit is marked ``[SUPERSEDED BY:
    <newest superseding claim>]`` and a superseding hit ``[SUPERSEDES:
    <replaced claims>]`` (the superseding claim's content is included via
    its snippet; when the superseding point is itself in the hits its full
    content renders too). Hits without the state render byte-identically.

    R1 #1540: per-hit rendering is the shared ``_render_block`` (the token
    budget uses the identical accounting), so ``context_tokens`` always
    matches what the reader consumed. Output is byte-identical to pre-R1
    for non-chunk hits.
    """
    text = "\n\n".join(_render_block(h) for h in hits)
    if question_date:
        text = f"Current Date: {question_date}\n\n{text}"
    return text


def _stored_marks(h: dict) -> dict[str, bool]:
    """Product-native mark fallback: a stored ``has_answer`` hit (the mark
    the extractor/ingest wrote) is treated as source-session evidence. The
    precise verbatim/raw-chunk/answer-string classes require read-time
    provenance the product does not recompute by default — callers with
    richer marks (the eval's dataset-derived recompute) inject ``mark_for``
    instead."""
    return {
        "source_session": bool(h.get("has_answer")),
        "verbatim": False,
        "raw_chunk": False,
        "answer_string": False,
    }


def apply_evidence_boost(
    pool: list[dict],
    *,
    mark_for: Callable[[dict], dict[str, bool]] | None = None,
    boost_answer_string: float = DEFAULT_EVIDENCE_BOOST_ANSWER_STRING,
    boost_verbatim: float = DEFAULT_EVIDENCE_BOOST_VERBATIM,
    boost_source: float = DEFAULT_EVIDENCE_BOOST_SOURCE,
) -> tuple[list[dict], dict[str, Any]]:
    """C2 (#1745): evidence-mark rank boost over the deduped pool.

    Marked hits (marks never influence RRF ranking — the fused score is
    content-similarity-only) move up by a stable rank offset so they can
    surface into the top-k the reader sees. The boost is a RANK re-scaling,
    NOT an RRF-score multiplier (annotated hits drop ``scores.rrf`` — there
    is no score to multiply): ``scaled_rank = original_index / factor``
    with factor 1.0 unmarked, ``boost_source`` for source-session-only
    marks, ``boost_verbatim`` for verbatim/raw-chunk marks,
    ``boost_answer_string`` for the #1763 answer-string mark (d). Placement
    is position-ceiling promotion (Horn's greedy): hits are processed in
    descending scaled-priority order and each takes the LARGEST free
    position <= its ceiling, where the ceiling is the ORIGINAL pool index
    for marked hits and unconstrained for unmarked hits. Properties:
      * position-ceiling — never demotes a marked hit below its original
        pool index (a dense run of higher-factor marked hits cannot push
        a lower-factor marked hit out of the top-k it occupied pre-boost),
      * never reorders within a boost class (same factor -> monotonic)
        and never reorders unmarked hits (relative order preserved),
      * bounded — no negative ranks, every hit lands in [0, n).

    #1945: the answer_string mark (d) is a FIRST-CLASS boost class with
    the STRONGEST multiplier (>= verbatim's) — it is the honest "this
    point contains the gold answer" signal. Class priority: answer_string
    (strongest) > verbatim/raw_chunk > source_session > unmarked. A stored
    ``has_answer`` hit with no read-time mark still falls back to the
    source class (conservative).

    ``mark_for`` supplies the per-hit marks (``{"source_session": bool,
    "verbatim": bool, "raw_chunk": bool, "answer_string": bool}``). Default
    None → the product's stored-mark fallback (``_stored_marks``); the eval
    injects its read-time recompute (``evidence.mark_for_question``) so the
    verbatim-vs-source split is recomputed from the question's answer turns
    and gold answer at retrieval time — no graph change needed.

    OFF by default in the product (the #1745 fail-safe default): the
    caller opts in (the eval's ``TORTOISE_LME_EVIDENCE_BOOST`` / explicit
    flag). Placement contract: the caller applies this BEFORE computing
    recall@k so the reported recall is honestly "recall over the boosted
    pool"; the pre-boost ranking rides back in
    ``stats["pre_boost_ranked_ids"]`` for the pre/post ablation.

    Returns ``(boosted_pool, stats)``; ``stats`` carries the per-class
    multiplier, the mark census and the pre-boost id order.
    """
    # factor domain guard: a boost factor < 1.0 is a rank DIVISION — 0.0 is
    # a ZeroDivisionError and a negative factor silently inverts the pool
    # order. Non-finite values (NaN/Inf) are rejected the same way (a NaN
    # passes the < 1.0 comparison and would poison every sort key; inf
    # would zero every key and make the boost a silent no-op). Reject
    # loudly at the function boundary (env/CLI layers clamp >= 1.0
    # independently; this is the last line).
    if not (math.isfinite(boost_answer_string) and boost_answer_string >= 1.0) \
            or not (math.isfinite(boost_verbatim) and boost_verbatim >= 1.0) \
            or not (math.isfinite(boost_source) and boost_source >= 1.0):
        raise ValueError(
            "evidence-boost multipliers must be >= 1.0 and finite (a "
            "rank-scaling division), got answer_string="
            f"{boost_answer_string!r} verbatim={boost_verbatim!r} "
            f"source={boost_source!r}")
    if mark_for is None:
        mark_for = _stored_marks
    census = {"source_session": 0, "verbatim": 0, "raw_chunk": 0,
              "answer_string": 0}
    scored: list[tuple[dict, float, int]] = []
    marked_by_idx: dict[int, bool] = {}
    for i, h in enumerate(pool):
        marks = mark_for(h)
        for mk in census:
            if marks.get(mk):
                census[mk] += 1
        # #1945: the answer-string class is the strongest signal (the
        # point carries the GOLD ANSWER) and takes priority over the
        # verbatim/raw-chunk provenance classes.
        if marks.get("answer_string"):
            factor = boost_answer_string
        elif marks.get("verbatim") or marks.get("raw_chunk"):
            factor = boost_verbatim
        elif marks.get("source_session") or h.get("has_answer"):
            factor = boost_source
        else:
            factor = 1.0
        scored.append((h, i / factor, i))
        marked_by_idx[i] = factor > 1.0
    # Position-ceiling promotion: the plain ascending sort let a dense run
    # of higher-factor marked hits (verbatim chunks, x1.5) pass a
    # lower-factor marked hit (source point, x1.15) and DEMOTE it below its
    # original pool index — the reproduced counter-example moved the point
    # 19 -> 22, out of top-20 (evidence_recall@20 dropped 1 -> 0 with the
    # boost ON). Horn's greedy: process hits in DESCENDING scaled-priority
    # order (for unmarked hits the scaled key IS the pool index, so they
    # run in descending index order) and assign each hit the LARGEST free
    # position <= its ceiling — original index for marked hits,
    # unconstrained for unmarked. Properties (verified by brute force):
    #   (a) marked hits never land below their original index;
    #   (b) unmarked relative order preserved (descending processing +
    #       largest-free assignment = strictly decreasing slots);
    #   (c) order within a boost class preserved (same argument).
    n = len(pool)
    free = list(range(n))
    placement: dict[int, tuple[dict, float, int]] = {}
    for h, key, i in sorted(
            scored, key=lambda x: (-x[1], -x[2])):
        ceiling = i if marked_by_idx[i] else n  # +inf ~ n: always satisfiable
        pos = max(p for p in free if p <= ceiling)
        free.remove(pos)
        placement[pos] = (h, key, i)
    scored = [placement[p] for p in range(n)]
    stats: dict[str, Any] = {
        "applied": True,
        "boost_answer_string": boost_answer_string,
        "boost_verbatim": boost_verbatim,
        "boost_source": boost_source,
        "marks_census": census,
        "pre_boost_ranked_ids": [h["id"] for h in pool],
        "moved": sum(1 for _, _, i in scored
                      if _rank_delta(scored, i)),
    }
    return [h for h, _, _ in scored], stats


def _rank_delta(scored: list[tuple[dict, float, int]], orig_index: int) -> bool:
    """True when the hit with original pool index ``orig_index`` moved to a
    strictly earlier position after the boost (the ``moved`` counter)."""
    new_pos = next(pos for pos, (_, _, i) in enumerate(scored)
                   if i == orig_index)
    return new_pos < orig_index


# ═══════════════════════════════════════════════════════════════════════════
# Slice A (#2683, epic #2080): evidence-package assembly — collapse a fact's
# own-source duplicates, dedup cross-item near-dupes, then order the package
# (exact-value/verbatim first, relevance order preserved, recency tiebreak).
# ---------------------------------------------------------------------------
# The measured regression the wave attacks (docs/scoping/2026-09-09-evidence-
# assembly-wave.md §5 Slice A): the reader context floods with near-duplicate
# evidence — a distilled point + its own source raw chunks + its source turns
# all restate the same fact, each occupying a window slot. The reader window
# is a FROZEN measurement lens (never a change target); the EVIDENCE PACKAGE
# handed to it is the product. These helpers build that package over the
# annotated pool (pure functions over hit dicts — no graph dependency, so
# the eval, MCP/SDK consumers and hermetic tests share the identical code).
#
# The collapse is deterministic + hermetic by construction: it keys on the
# provenance fields the ingest wrote (``source_turn_id`` / verbatim ``quote``
# containment — R1 #1540 keeps the raw chunk text, E3 #1535 writes the
# point→source-turn link) and on normalized content overlap — NEVER an LLM or
# an embedder. Arm posture: tri-state fail-safe OFF (only an explicit flag or
# the env enables it — the #1745 default decision); the OFF path never calls
# these helpers (byte-identical).
# ═══════════════════════════════════════════════════════════════════════════

#: Slice A (#2683): max verbatim source refs a single fact package keeps in
#: the reader window (the point + at most ONE source chunk/turn — the scope's
#: "one fact occupies one slot" bound).
DEFAULT_PACKAGE_MAX_VERBATIM = 1

#: Slice A (#2683): min token-overlap (shared / smaller set, stopword-stripped
#: normalized tokens) for a NEAR-VERBATIM restatement to count as the same
#: fact. 0.9 on the shorter side = the same claim restated; a same-frame
#: DIFFERENT-VALUE claim ("the tea set cost 300" vs "cost 400" — the MR
#: aggregation numerator Slice B protects) shares only ~0.8-0.875 of its
#: content (the differing value token drops the shared fraction below 0.9)
#: and stays DISTINCT. Exact normalized-content equality is always the same
#: fact. Never an LLM (deterministic + hermetic).
DEFAULT_PACKAGE_VERBATIM_OVERLAP = 0.9

#: Slice A (#2683): min token-overlap for the same-SOURCE-TURN / same-QUOTE
#: leg — two statements distilled from the SAME source turn (same
#: ``source_turn_id``, E3 #1535) whose verbatim QUOTES also overlap are
#: duplicate extractions of one utterance (the same quote span ⇒ the same
#: fact; two distinct claims from one turn carry distinct quote spans —
#: different values never collapse).
DEFAULT_PACKAGE_SAME_TURN_OVERLAP = 0.75

#: The role-bracket shape the deterministic leg writes turn points as (E3
#: #1535) — used to strip the bracket before verbatim containment compares a
#: turn's text against its source chunk's text (the chunk stores "Role: …",
#: the turn point "[role] …").
_ROLE_PREFIX_RE = re.compile(r"^\[(user|assistant|system|tool|unknown)\]\s*",
                             re.IGNORECASE)


_PACKAGE_STOPWORDS = frozenset({
    "a", "an", "the", "and", "or", "but", "if", "then", "else", "of",
    "to", "in", "on", "at", "for", "with", "from", "by", "about",
    "is", "are", "was", "were", "be", "been", "being", "do", "does",
    "did", "it", "this", "that", "these", "those", "i", "we", "you",
    "he", "she", "they", "me", "my", "our", "your", "their",
    "yes", "so", "as", "than", "now",
    # NB: "not"/"no" are DELIBERATELY absent (P2 #2687 review): negation
    # is semantic content for a fact-dedup tokenizer — "did not cost 300"
    # must never normalize to "cost 300".
})


#: Fact-critical token classes (P1 #2687 review): a collapse that would
#: merge two texts differing in ANY of these is an information-loss bug —
#: values, currencies, units, quantities and dates are the aggregation
#: numerator Slice B protects. Ratio-based overlap can only merge
#: restatements whose differing tokens are synonym-level; if the differing
#: set contains a fact-critical token the claims are DIFFERENT facts.
_NUMERIC_TOKEN_RE = re.compile(r"^[+-]?\d+(?:[.,]\d+)*$")
_CURRENCY_PREFIX_RE = re.compile(r"^[$£€¥]")
_UNIT_WORDS = frozenset({
    "dollars", "dollar", "bucks", "pounds", "pound", "quid", "euros",
    "euro", "yen", "cents", "cent", "percent", "percentage", "points",
    "point", "km", "miles", "mile", "meters", "meter", "feet", "foot",
    "inches", "inch", "kgs", "kg", "lbs", "grams", "gram",
    "liters", "liter", "hours", "hour", "minutes", "minute", "seconds",
    "second", "days", "day", "weeks", "week", "months", "month",
    "years", "year", "times", "time", "degrees", "degree", "items",
    "item", "prices", "price", "cost", "costs", "worth",
    "amount", "total", "sum", "count", "number", "qty", "quantity",
})
_NEGATION_WORDS = frozenset({"not", "no", "never", "neither", "nor",
                             "cannot", "can't", "didnt", "doesnt",
                             "doesn't", "don't"})


def _token_is_fact_critical(tok: str) -> bool:
    """Slice A: True when ``tok`` changes a fact if it differs between two
    otherwise-similar claims: a number, a currency-denominated amount, a
    unit/quantity word, or a negation. (Dates: numeric forms are caught by
    the numeric class; month/weekday names are a documented residual.)"""
    if _NUMERIC_TOKEN_RE.match(tok) or _CURRENCY_PREFIX_RE.match(tok):
        return True
    if tok in _UNIT_WORDS or tok in _NEGATION_WORDS:
        return True
    # plural/possessive numeric artifacts ("300s", "400's") normalize to
    # digits + suffix — the digit prefix is still a value difference.
    return bool(re.match(r"^[+-]?\d+(?:[.,]\d+)*[a-z']*$", tok))


def _differing_tokens(a_toks: set[str], b_toks: set[str]) -> set[str]:
    """Slice A: the symmetric-difference token set of two normalized
    contents. If ANY member is fact-critical, the claims differ in a value/
    negation/unit dimension → they are distinct facts regardless of how
    much content they share (the P1 #2687 value-safety guard)."""
    return (a_toks - b_toks) | (b_toks - a_toks)


def _pkg_norm(text: str) -> str:
    """Slice A: case/whitespace + PUNCTUATION-normalized verbatim form
    (mirrors the eval's ``evidence._normalize`` — deterministic, hermetic).
    Punctuation becomes whitespace so "300 dollars," and "300 dollars"
    tokenize identically for overlap AND containment legs (the role bracket
    survives here — ``_verbatim_core`` strips it before the containment
    compare). CURRENCY SYMBOLS ARE CONTENT (P2 #2687 review): "£300",
    "$300" and "300" are different amounts — the symbol must not be
    erased by the punctuation strip."""
    t = re.sub(r"[^\w\s\[\]$£€¥]", " ", str(text or ""))
    return re.sub(r"\s+", " ", t.lower()).strip()


def _pkg_tokens(text: str) -> set[str]:
    """Slice A: stopword-stripped content tokens of ``text`` (the product's
    own local set — importing the eval's stopword list would invert the
    layering; the vocabulary is content words only, which is what a
    restatement shares)."""
    return {t for t in _pkg_norm(text).split()
            if t not in _PACKAGE_STOPWORDS and len(t) > 1}


def _pkg_overlap(a: str, b: str) -> float:
    """Slice A: min-denominator content-token overlap — the SHORTER text's
    coverage decides restatement (a 200-char point restating a 40-char
    source quote shares 40/40 = 1.0, not 40/200 = 0.2)."""
    ta, tb = _pkg_tokens(a), _pkg_tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / min(len(ta), len(tb))


def _pkg_differ_value_critical(a: str, b: str) -> bool:
    """Slice A P1 guard (#2687 review): do ``a`` and ``b`` differ in a
    fact-critical dimension (a number, currency amount, unit/quantity word,
    or negation)? The ratio legs may only merge RESTATEMENTS (synonym-level
    differing tokens); a same-frame different-VALUE claim ("cost 300" vs
    "cost 400") is a different fact no matter how much content it shares —
    on production-length quotes (20-35 tokens) the ratio alone is NOT a
    safe value guard (the reviewer's measured 300-vs-400 collapse)."""
    ta, tb = _pkg_tokens(a), _pkg_tokens(b)
    if not ta or not tb:
        return False
    return any(_token_is_fact_critical(t)
               for t in _differing_tokens(ta, tb))


def _pkg_session(h: dict) -> str:
    """Slice A: a hit's session identity (the same bucket key the ask lane
    passes ``dedup_pool`` — session_id first, session_date, lme index
    fallback; distinct sessions never share a bucket)."""
    return (h.get("session_id")
            or h.get("session_date")
            or f"idx:{h.get('lme_session_index', -1)}")


def _is_turn_point(h: dict) -> bool:
    """Slice A: True for a verbatim source TURN point (kind ``event`` or a
    ``[role] …`` content — the shape the deterministic leg writes turns as).
    Distinguished from a distilled statement so own-source turns can collapse
    INTO their distilled point instead of standing as a duplicate slot."""
    return (h.get("point_kind") == "event"
            or bool(_ROLE_PREFIX_RE.match(str(h.get("content") or ""))))


def _verbatim_core(h: dict) -> str:
    """Slice A: the verbatim content of a source ref hit (raw chunk or turn),
    with a leading role bracket stripped so chunk-vs-turn containment
    compares the actual utterance (the chunk stores "Role: …" lines, the
    turn point "[role] …")."""
    text = str(h.get("content") or "")
    return _ROLE_PREFIX_RE.sub("", text, count=1)


def _is_own_source(chunk_or_turn: dict, point: dict) -> bool:
    """Slice A: is ``chunk_or_turn`` the ``point``'s OWN source restatement?
    Deterministic provenance proxy on the annotated surface:
      * the raw chunk whose verbatim text CONTAINS the point's anchored
        quote (the D3/M6 quote is verbatim from the source turn, and the
        chunk is the windowed verbatim transcript that turn lives in), or
      * the turn node whose id the point records as ``source_turn_id``
        (E3 #1535 writes the point→source-turn link), or
      * a turn whose verbatim text contains the quote.
    The quote-containment leg requires a non-empty quote (no quote → the
    point has no verbatim provenance to collapse onto — leave it standalone).
    Same-session: a point's own source chunk/turn always shares its session.
    """
    q = _pkg_norm(str(point.get("quote") or ""))
    if not q:
        return False
    if _pkg_session(chunk_or_turn) != _pkg_session(point):
        return False
    if chunk_or_turn.get("id") and chunk_or_turn["id"] == point.get(
            "source_turn_id"):
        return True
    core = _verbatim_core(chunk_or_turn)
    return bool(core) and q in _pkg_norm(core)


def _same_fact(a: dict, b: dict) -> bool:
    """Slice A: do two distilled/statement hits restate the SAME fact?
    Deterministic, hermetic, no model:
      * EXACT restatement — normalized content equality (the same statement
        re-extracted verbatim, any session),
      * NEAR-VERBATIM restatement — min-denominator content overlap ≥
        ``DEFAULT_PACKAGE_VERBATIM_OVERLAP`` (the same claim restated
        near-word-for-word),
      * same SOURCE TURN (``source_turn_id`` equal — E3 #1535) AND the
        verbatim ``quote`` spans overlap ≥ ``DEFAULT_PACKAGE_SAME_TURN_OVERLAP``
        — duplicate extractions of ONE utterance: the same quote span means
        the same fact even when the surrounding content is rephrased. Two
        distinct claims distilled from one turn carry DIFFERENT quote spans
        (different values/objects) and never collapse (the aggregation
        numerator Slice B protects). This leg is content-overlap-independent
        by design: extractors can rephrase the wrapper while anchoring the
        same verbatim quote.
    Value safety: a same-frame different-value claim ("cost 300" vs
    "cost 400") shares only ~0.8-0.875 content (< 0.9) and its quote spans
    overlap 2/3 ≈ 0.667 (< 0.75) — it survives as its own slot. No
    LLM/embedder anywhere (hermetic-testable)."""
    ca = _pkg_norm(str(a.get("content") or ""))
    cb = _pkg_norm(str(b.get("content") or ""))
    if ca and ca == cb:
        return True
    # Content ratio leg (P1 #2687 review): near-verbatim restatement
    # collapses ONLY when the differing tokens are synonym-level — a
    # fact-critical differing token (number / currency / negation) means
    # DIFFERENT facts and refuses the ratio leg even at ≥0.9 on
    # production-length quotes (the (n-1)/n math only protects toy frames).
    ov = _pkg_overlap(ca, cb)
    if ov >= DEFAULT_PACKAGE_VERBATIM_OVERLAP:
        return not _pkg_differ_value_critical(ca, cb)
    # Same-SOURCE-TURN leg (independent of content overlap — duplicate
    # extractions of ONE utterance can rephrase the surrounding content
    # widely while anchoring the same quote span): same ``source_turn_id``
    # (E3 #1535) AND the verbatim ``quote`` spans overlap ≥
    # ``DEFAULT_PACKAGE_SAME_TURN_OVERLAP`` — the same quote span means the
    # same fact. The content guard above does NOT gate this leg: two
    # extractions of one utterance may restate the value in words in the
    # content while the quote carries the number. Two distinct claims from
    # one turn carry DIFFERENT quote spans (different values/objects) and
    # never collapse (the aggregation numerator Slice B protects); a same-
    # turn quote pair that itself differs in a value is refused by the
    # quote-value guard below.
    at = str(a.get("source_turn_id") or "")
    bt = str(b.get("source_turn_id") or "")
    if not (at and at == bt):
        return False
    qa = str(a.get("quote") or "")
    qb = str(b.get("quote") or "")
    if not (qa and qb):
        return False
    if _pkg_differ_value_critical(qa, qb):
        return False
    return _pkg_overlap(qa, qb) >= DEFAULT_PACKAGE_SAME_TURN_OVERLAP


#: #1945 mark-class ordering (strongest → weakest) for the value-tier split.
_PACKAGE_VALUE_CLASSES = ("answer_string", "verbatim", "raw_chunk")


def _package_tier(h: dict, mark_for: Callable[[dict], dict[str, bool]] | None
                  ) -> tuple[int, int]:
    """Slice A: the package's value tier = (0, rank) when the anchor carries
    an exact-value/verbatim mark (answer_string / verbatim / raw_chunk — the
    #1763/#1945 precise classes), else (1, rank). The rank tiebreaks within a
    tier by the anchor's original pool position (relevance)."""
    marks = mark_for(h) if mark_for is not None else _stored_marks(h)
    if any(marks.get(cls) for cls in _PACKAGE_VALUE_CLASSES):
        return (0, h.get("_pkg_rank", 0))
    return (1, h.get("_pkg_rank", 0))


def package_evidence_pool(
    pool: list[dict], *,
    mark_for: Callable[[dict], dict[str, bool]] | None = None,
    max_verbatim: int = DEFAULT_PACKAGE_MAX_VERBATIM,
) -> tuple[list[dict], dict[str, Any]]:
    """Slice A (#2683): build the reader's EVIDENCE PACKAGE from the annotated
    pool — collapse + dedup + order, pure and hermetic.

    Two passes (order-independent collapse — a raw chunk/turn ranked ABOVE
    its distilled point must still collapse INTO it, not stand alone):

    Pass 1 — distilled/statement anchors (points first): a statement opens a
    package; a second statement restating the SAME fact (``_same_fact`` —
    exact or ≥0.9 near-verbatim content equality, or the same-source-turn
    same-quote leg) collapses into the earlier package. Same-fact
    restatement is session-agnostic on the content legs (two sessions
    restating the same claim are duplicates of ONE fact for the reader
    window — the window is the scarce resource), and the same-source-turn
    leg is inherently single-session (``source_turn_id`` is one turn). The
    higher-value anchor wins (exact-value/verbatim-marked beats source-only;
    else the earlier pool rank — relevance).

    Pass 2 — verbatim sources (raw chunks + source turns): a chunk/turn that
    is a distilled anchor's OWN source (same session + its verbatim quote in
    the chunk/turn text, or the recorded ``source_turn_id`` — ``_is_own_source``)
    collapses INTO that package as at most ``max_verbatim`` ref(s); extra
    own-source chunks/turns are DROPPED (one fact occupies ≤ 1 + max_verbatim
    window slots). A chunk/turn that is no distilled anchor's own source
    stands alone as evidence; duplicate verbatim content in the SAME session
    (a turn whose utterance is contained in a kept chunk, or a byte-identical
    second chunk) dedups to the container/earlier copy.

    Ordering: surviving packages sort value-tier first (exact-value/verbatim-
    marked anchors lead the window — the #1763/#1945 precise classes), then
    by original pool relevance order (``_pkg_rank``) WITHIN a tier — the pool
    order the hybrid search + recency-aware dedup already produced (relevance,
    recency as its upstream tiebreak), so the package preserves the product's
    existing order semantics exactly for equal-tier items.

    Semantics contract:
      * membership is a SUBSET of the pool — never a synthesis (each package
        renders its own pool hits; ``render_context``/token accounting are
        unchanged per hit),
      * deterministic + hermetic — the collapse keys on ingest-written
        provenance (``quote`` / ``source_turn_id``) + normalized content
        overlap, never an LLM/embedder (hermetic tests need no model),
      * the pool recall surface (``ret["hits"]``, ``evidence_recall@k``) is
        UNCHANGED — the caller computes recall over the pool and feeds the
        packaged result to ``assemble_context``; the package is what the
        reader sees (the eval's ``reader_evidence@k`` / ``reader_surface@k``
        measure it),
      * OFF-by-default: the caller applies this ONLY when the tri-state arm
        is on; the default path never calls it (byte-identical).

    ``mark_for`` supplies the read-time mark provider (the eval injects
    ``evidence.mark_for_question``); default None = the product's stored-mark
    fallback (source-session class only). Returns ``(packaged, stats)`` with
    ``stats`` recording the package census + the dropped/collapsed counts.
    """
    if max_verbatim < 0:
        raise ValueError(f"max_verbatim must be >= 0, got {max_verbatim!r}")
    # rank-stamp every hit ONCE (deterministic tiebreak on shallow copies —
    # the caller's dicts are never mutated).
    stamped: list[dict] = []
    for i, h in enumerate(pool):
        c = dict(h)
        c["_pkg_rank"] = i
        stamped.append(c)
    points = [h for h in stamped
              if not is_raw_chunk(h) and not _is_turn_point(h)]
    sources = [h for h in stamped
               if is_raw_chunk(h) or _is_turn_point(h)]

    # Pass 1: statement/distilled anchors (pool order = relevance order).
    packages: list[dict] = []  # each: {anchor, verbatim: [hits], dropped: int}
    for h in points:
        merged = None
        for pkg in packages:
            if _same_fact(h, pkg["anchor"]):
                merged = pkg
                break
        if merged is not None:
            # keep the higher-value anchor (value tier, then earlier rank)
            if _package_tier(h, mark_for) < _package_tier(
                    merged["anchor"], mark_for):
                merged["dropped"] += 1
                merged["anchor"] = h
            else:
                merged["dropped"] += 1
            continue
        packages.append({"anchor": h, "verbatim": [], "dropped": 0})

    # Pass 2: verbatim sources (raw chunks + source turns) collapse into
    # their own-source anchor package; unclaimed ones stand alone.
    for h in sources:
        owner = None
        for pkg in packages:
            if (not is_raw_chunk(pkg["anchor"])
                    and not _is_turn_point(pkg["anchor"])
                    and _is_own_source(h, pkg["anchor"])):
                owner = pkg
                break
        if owner is not None:
            if len(owner["verbatim"]) < max_verbatim:
                owner["verbatim"].append(h)
            else:
                owner["dropped"] += 1
            continue
        # no distilled owner: standalone verbatim package — dedup same-session
        # duplicates (a contained turn vs the chunk holding it, or a
        # byte-identical second chunk), preferring the CONTAINER (the raw
        # chunk holds the full window; a contained turn alone would starve
        # the reader of context) regardless of pool arrival order.
        dup = None
        for pkg in packages:
            a = pkg["anchor"]
            if not (is_raw_chunk(a) or _is_turn_point(a)):
                continue
            if _pkg_session(a) != _pkg_session(h):
                continue
            hc = _pkg_norm(_verbatim_core(h))
            ac = _pkg_norm(_verbatim_core(a))
            if not hc or not ac:
                continue
            # same utterance, or one verbatim text CONTAINED in the other
            # (the raw chunk holds its turns verbatim)
            if hc == ac or hc in ac or ac in hc:
                dup = pkg
                break
        if dup is not None:
            # the container wins the anchor slot (a chunk arriving after its
            # contained turn REPLACES the turn; the contained hit is dropped).
            if len(hc) > len(ac) and hc != ac:
                dup["anchor"] = h
            dup["dropped"] += 1
            continue
        packages.append({"anchor": h, "verbatim": [], "dropped": 0})

    ordered = sorted(
        packages,
        key=lambda p: (_package_tier(p["anchor"], mark_for)[0],
                       p["anchor"].get("_pkg_rank", 0)),
    )
    out: list[dict] = []
    for pkg in ordered:
        out.append({k: v for k, v in pkg["anchor"].items()
                    if k != "_pkg_rank"})
        out.extend({k: v for k, v in v.items() if k != "_pkg_rank"}
                   for v in pkg["verbatim"])
    stats: dict[str, Any] = {
        "applied": True,
        "pool_items": len(pool),
        "package_items": len(out),
        "packages": len(packages),
        "collapsed_duplicates": sum(p["dropped"] for p in packages),
        "verbatim_refs_kept": sum(len(p["verbatim"]) for p in packages),
        "value_first_packages": sum(
            1 for p in packages
            if _package_tier(p["anchor"], mark_for)[0] == 0),
    }
    return out, stats
