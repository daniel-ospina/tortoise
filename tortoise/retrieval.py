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
