"""Tortoise ask lane — EVAL-ONLY internal entry point (not a product surface).

⛔ EVAL-ONLY (#3849, owner-directed). The ask lane is NOT a product surface:

  * it is NOT in the MCP surface — no ask tool in the registry, so it appears
    in neither ``tools/list`` nor ``tools/call``;
  * the SDK exposes NO ask method — ``TortoiseSDK.ask`` /
    ``TortoiseSDK.ask_assembled`` do not resolve (removal, not a dead alias);
  * there is NO REST route — ``/v1/ask`` is gone from the hosted AND
    self-host apps, and no exposure-gate flag can bring it back.

The lane survives HERE because the evals measure the SHIPPED reader
end-to-end and nothing smaller composes it: ``tools/longmem_eval/run.py``
(the 500-Q benchmark) drives ``tortoise.reader`` directly, while the
LongMemEval A/B assembly arm and ``tools/ask_spotcheck.py`` drive the two
entry points below. The reader prompt stays single-sourced in
``tortoise/reader.py`` — this module owns no prompt text.

Do NOT build product features on this module. When the reader-model decision
lands, the whole lane is one file to delete.
"""
from __future__ import annotations

import collections
import logging
import os
import threading
from datetime import UTC
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only (avoids an sdk import cycle)
    from .assembly import AssemblyAnswer
    from .sdk import TortoiseSDK

_logger = logging.getLogger(__name__)


# ── Ask-lane reader-model cache (#1987 Task 5) ─────────────────────────────
# Per-namespace cache (keyed by org/namespace — NEVER a module-global
# model): LRU bound (≤ N entries), in-flight entries NEVER evicted (an LRU
# eviction can never race an in-flight ask), closed clients on eviction,
# failed builds never cached, per-key build single-flight. The cache holds
# the LOCKED WRAPPER — the per-instance lock lives INSIDE the cached object
# (complete() + usage capture under it), so the shared cached wrapper IS the
# locked object (P2-6).

_ASK_READER_CACHE_MAX = 64
_ASK_READER_CACHE_LOCK = threading.Lock()
_ask_reader_cache_store: collections.OrderedDict = None  # type: ignore[assignment]
_ask_build_locks: dict[str, threading.Lock] = {}


def _ask_reader_cache() -> collections.OrderedDict:
    import collections
    global _ask_reader_cache_store
    if _ask_reader_cache_store is None:
        _ask_reader_cache_store = collections.OrderedDict()
    return _ask_reader_cache_store


def _ask_build_lock(key: str) -> threading.Lock:
    with _ASK_READER_CACHE_LOCK:
        return _ask_build_locks.setdefault(key, threading.Lock())


def _prune_ask_reader_cache(cache) -> None:
    """LRU bound: evict IDLE entries only (never in-flight), closing their
    clients (no leaked sockets). In-flight entries are never evicted (P1-6)."""
    while len(cache) > _ASK_READER_CACHE_MAX:
        for key, entry in list(cache.items()):
            if entry.inflight() == 0:
                cache.pop(key, None)
                # Drop the single-flight build lock alongside the idle entry
                # (P2 — the build-lock dict must not grow unbounded). Safe:
                # an idle entry can never have an in-flight build (a build
                # either returns the idle entry or the entry is absent).
                _ask_build_locks.pop(key, None)
                entry.close()
                break
        else:
            break  # all entries in-flight — stop evicting


def _default_ask_reader_factory():
    """The production ask-lane reader factory — monkeypatched in tests to
    inject fake readers/transports."""
    from tortoise.model_adapters import build_reader_model
    return build_reader_model()


def _reset_ask_reader_cache_for_tests() -> None:
    """Test seam — drop the cache + build locks (closes cached clients)."""
    global _ask_reader_cache_store, _ask_build_locks
    with _ASK_READER_CACHE_LOCK:
        cache = _ask_reader_cache()
        for entry in cache.values():
            entry.close()
        _ask_reader_cache_store = None
        _ask_build_locks = {}


class _LockedReader:
    """The CACHED ask-lane reader wrapper (P2-6): serializes the inner
    ``complete()`` + usage capture under a per-instance ``threading.Lock`` —
    the mutable ``last_completion_tokens`` write at the end of the inner
    adapter's ``complete()`` is closed against cross-thread read-after-write
    (contention bounded while the product path's per-org in-flight cap 4
    existed; the eval-only lane is unbudgeted). Forwards
    ``model``/``provider``/``route``/``last_route``/``last_prompt_tokens``/
    ``last_completion_tokens``/``last_finish_reason`` and ``close()``.
    """

    def __init__(self, model):
        self._model = model
        self._lock = threading.Lock()
        self._inflight = 0

    def complete(self, *, system: str, user: str,
                 max_tokens: int | None = None) -> str:
        with self._lock:
            self._inflight += 1
            try:
                # #2280: forward a per-call max_tokens override (RoutingModel
                # / adapters already support it) — the ask lane uses it for
                # bounded budget ESCALATION when the first call collapses
                # empty (reasoning-budget collapse on reasoning models).
                if max_tokens is None:
                    out = self._model.complete(system=system, user=user)
                else:
                    out = self._model.complete(system=system, user=user,
                                               max_tokens=max_tokens)
                # same-frame capture — atomic with the call under the lock
                self.last_prompt_tokens = getattr(
                    self._model, "last_prompt_tokens", 0)
                self.last_completion_tokens = getattr(
                    self._model, "last_completion_tokens", 0)
                self.last_finish_reason = getattr(
                    self._model, "last_finish_reason", None)
                return out
            finally:
                self._inflight -= 1

    def close(self) -> None:
        close = getattr(self._model, "close", None)
        if close is not None:
            try:  # noqa: SIM105
                close()
            except Exception:
                pass

    def failed(self) -> bool:
        return False

    def incr_inflight(self) -> None:
        self._inflight += 1

    def decr_inflight(self) -> None:
        if self._inflight > 0:
            self._inflight -= 1

    def inflight(self) -> int:
        return self._inflight

    @property
    def model(self):
        return getattr(self._model, "model", None)

    @property
    def provider(self):
        return getattr(self._model, "provider", None)

    @property
    def route(self):
        return getattr(self._model, "route", None)

    @property
    def last_route(self):
        return getattr(self._model, "last_route", None)


def _ask_reader_complete(model, *, system: str, user: str) -> tuple[str, int]:
    """ONE ask-lane reader call with bounded output-budget escalation
    (#2280).

    Reasoning-capable models (e.g. qwen3.8-max via OpenRouter) can spend
    the whole reader output budget (``DEFAULT_READER_MAX_TOKENS``=500)
    THINKING on hard questions and emit NOTHING — ``content`` empty/None
    with ``finish_reason="length"`` (the reasoning-budget collapse class;
    the DeepSeekDirect variant is fixed by disabling thinking, #1790, but
    qwen refuses that knob). An empty model output is NEVER a legitimate
    abstention — the two-phase prompt abstains in WRITING — so the lane
    must not read a collapsed call as "no evidence" (the pre-#2280 behavior
    silently fabricated abstentions on answerable questions).

    Policy (bounded, cost-controlled; at most TWO calls):
      * non-empty output → returned (exactly one call, the common path);
      * empty + ``finish_reason == "length"`` (budget exhausted before any
        content) → ONE retry at an escalated budget
        (``TORTOISE_ASK_ESCALATION_TOKENS``, default
        ``DEFAULT_READER_ESCALATION_MAX_TOKENS``);
      * empty + any other finish reason → ONE retry at the SAME budget
        (transient empty/provider variance);
      * still empty after the retry → ``AskReaderUnavailable``
        (fail-loud) — NEVER abstained/``NO_EVIDENCE_TEXT``.

    Returns ``(raw, completion_tokens_total)`` — the total is the SUM of
    billed completion tokens across the (≤2) calls, so the collapsed first
    call's tokens are never dropped from metering/cost estimates (the
    per-call ``last_completion_tokens`` capture on ``_LockedReader`` only
    reflects the LAST call).
    """
    from tortoise.exceptions import AskReaderUnavailable
    from tortoise.reader import DEFAULT_READER_ESCALATION_MAX_TOKENS
    from tortoise.retrieval import ask_env_int

    def _billed() -> int:
        return int(getattr(model, "last_completion_tokens", 0) or 0)

    total = 0
    raw = model.complete(system=system, user=user)
    total += _billed()
    if raw is not None and str(raw).strip():
        return raw, total
    finish_reason = getattr(model, "last_finish_reason", None)
    if finish_reason == "length":
        # Budget exhausted before any content — escalate ONCE.
        esc = ask_env_int(
            "TORTOISE_ASK_ESCALATION_TOKENS",
            DEFAULT_READER_ESCALATION_MAX_TOKENS, lo=512, hi=8192)
        raw = model.complete(system=system, user=user, max_tokens=esc)
        total += _billed()
        if raw is not None and str(raw).strip():
            _logger.warning(
                "ask reader: empty output at budget cap, escalated to "
                "max_tokens=%s and answered (finish_reason=%r)", esc,
                finish_reason)
            return raw, total
        raise AskReaderUnavailable(
            "reader returned empty output after budget escalation "
            f"(finish_reason={finish_reason!r}) — not an abstention")
    # Non-length empty output — retry ONCE at the same budget (transient),
    # then fail loud. Never a silent abstention.
    raw = model.complete(system=system, user=user)
    total += _billed()
    if raw is not None and str(raw).strip():
        return raw, total
    raise AskReaderUnavailable(
        "reader returned empty output "
        f"(finish_reason={finish_reason!r}) — not an abstention")

def _ask_validate(question: str, question_type: str | None,
                  question_date: str | None) -> None:
    """Local-lane validation — the FIRST pipeline stage (P2-8: invalid
    inputs never reach retrieval — zero model calls AND zero retrieval
    calls). Raises ``AskValidationError`` with the pinned canonical
    instance codes (pinned to the canonical vocabulary constants — P2-14)."""
    from tortoise.schemas import (  # noqa: I001
        MAX_ASK_QUESTION_CHARS,
        ASK_QUESTION_TYPES,
        VALIDATION_CODE_BAD_DATE,
        VALIDATION_CODE_BAD_TYPE,
        VALIDATION_CODE_EMPTY,
        VALIDATION_CODE_OVERSIZE,
        ask_question_has_control_chars,
        ask_question_is_punctuation_only,
        validate_ask_question_date,
    )
    from tortoise.exceptions import AskValidationError
    if not isinstance(question, str):
        raise AskValidationError(
            "question must be a non-empty string",
            code=VALIDATION_CODE_EMPTY)
    if question is None or not str(question).strip():
        raise AskValidationError(
            "question must be a non-empty string",
            code=VALIDATION_CODE_EMPTY)
    q = str(question)
    if ask_question_has_control_chars(q):
        raise AskValidationError(
            "question contains control/zero-width characters",
            code=VALIDATION_CODE_EMPTY)
    if ask_question_is_punctuation_only(q):
        raise AskValidationError(
            "question is punctuation-only",
            code=VALIDATION_CODE_EMPTY)
    if len(q) > MAX_ASK_QUESTION_CHARS:
        raise AskValidationError(
            f"question exceeds {MAX_ASK_QUESTION_CHARS} chars",
            code=VALIDATION_CODE_OVERSIZE)
    if question_type is not None and question_type not in ASK_QUESTION_TYPES:
        raise AskValidationError(
            f"unknown question_type {question_type!r}; valid: "
            f"temporal-reasoning|knowledge-update|multi-session|"
            f"single-session-preference",
            code=VALIDATION_CODE_BAD_TYPE)
    if question_date is not None and not validate_ask_question_date(str(question_date)):
        raise AskValidationError(
            f"invalid question_date {question_date!r} (expected "
            f"YYYY-MM-DD, real calendar date)",
            code=VALIDATION_CODE_BAD_DATE)

def run_ask_lane(sdk: TortoiseSDK, question: str, *,
                 question_type: str | None = None,
                 question_date: str | None = None,
                 org_id: str | None = None,
                 _reader_factory=None,
                 _selfhost_transport: bool = False) -> dict:
    """EVAL-ONLY entry point — the LongMemEval A/B arm's reader path.

    ⛔ NOT A PRODUCT SURFACE (#3849): no MCP tool, no SDK method, no REST
    route. Called only from eval/test code — the LongMemEval arms,
    ``tools/ask_spotcheck.py`` and the lane's own suites; no product
    caller. Requires a LOCAL graph: in hosted client
    mode (``TORTOISE_API_URL`` set) it raises ``AskRetrievalUnavailable`` —
    the hosted ``/v1/ask`` surface no longer exists.

    Local lane pipeline: validation FIRST (``AskValidationError``, zero
    model calls) → ``tortoise_fts_query`` (``include_terminal=True`` —
    the D8 supersession markers reach the reader; cost-bounded by the
    resolved caps — ``resolve_ask_retrieval_caps()``, default
    200/200/16000/derived) → ask-path annotation (session-date join + speaker)
    → ``dedup_pool`` (per-session cap 3, keyed on the annotated session)
    → A5 evidence-mark boost (default ON — reorders the deduped pool by
    stored ``has_answer`` marks; zero marks = no-op) → A7 rerank
    (env-gated OFF by default) → ``assemble_context`` (resolved token
    AND byte caps, whole-hit drop; #4105: the byte cap was a 32 KiB literal
    and made every cap raise above it a silent no-op) →
    ``detect_question_type`` (or caller override) →
    ONE reader call via ``build_reader_model()`` (never an
    LLM-skip pre-gate — exactly one model call incl. empty context;
    #2280: an EMPTY model output escalates ONCE to a larger output
    budget when the first call collapsed thinking-only
    (``finish_reason="length"``), then fails loud as
    ``AskReaderUnavailable`` — an empty output is never read as
    an abstention) →
    ``_looks_abstained`` (abstained is ALWAYS the model's written
    decision; the blank→``NO_EVIDENCE_TEXT`` substitution is a
    retired defensive invariant) → best-effort
    ``record_ask_usage`` (ONLY with an explicit ``org_id``; default
    None → no-op).

    #2070 retrieval knobs (ask-lane only — the search lane is
    untouched, both-not-either preserved):

      * A1 numeric tokens — ``TORTOISE_ASK_NUMERIC_TOKENS`` (default ON):
        all-digit money/quantity tokens survive the sparse tokenizer
        (same-value dollar questions retrieve their turns).
      * A2 vector leg — the ``embeddings`` extra is a DOCUMENTED runtime
        requirement for ask quality (NEVER enforced): when the embedder
        is absent the vector strategy is never submitted and
        ``retrieval_degraded`` stays honest (no silent success).
      * A3 fusion — ``TORTOISE_ASK_FUSION_WEIGHTS`` (JSON; default None
        = the shared global 1.5) + ``TORTOISE_ASK_FUSION_K`` (default 60).
      * A4 search_keys PRF — ``TORTOISE_ASK_SEARCH_KEYS_PRF`` (default
        ON): additive expansion terms from the retrieved pool's
        top-5 hits' ``search_keys`` (original tokens always keep their
        OR-cap slots).
      * A5 evidence boost — ``TORTOISE_ASK_EVIDENCE_BOOST`` (default
        ON) + ``TORTOISE_ASK_EVIDENCE_BOOST_ANSWER_STRING/VERBATIM/SOURCE``.
      * A6 caps — ``TORTOISE_ASK_RETRIEVAL_LIMIT`` /
        ``TORTOISE_ASK_CONTEXT_ITEM_CAP`` /
        ``TORTOISE_ASK_CONTEXT_TOKEN_CAP`` /
        ``TORTOISE_ASK_CONTEXT_BYTE_CAP`` /
        ``TORTOISE_ASK_POOL_SIZE`` (defaults 200/200/16000/derived(128000 bytes)/200
        since #4105; the retrieval-window limit is threaded IN TANDEM with the
        assembly caps and the pool floor, and the byte ceiling is resolved
        rather than hard-coded — raising only the assemble cap changes
        nothing, and a byte ceiling that cannot be raised is now impossible:
        an unset byte cap is DERIVED from the token cap).
      * A7 rerank — ``TORTOISE_ASK_RERANK`` (default OFF, phase 2):
        cross-encoder + MMR port (tortoise/rerank.py), degrade-to-
        current contract + a context/token budget guard (#2976): a
        reranked set over the resolved token / byte caps is refused whole
        (unreranked order), never silently truncated.
      * A8 evidence-package assembly (Slice A #2683, epic #2080) —
        ``TORTOISE_ASK_EVIDENCE_ASSEMBLY`` (default OFF, fail-safe):
        collapses a distilled point's own source raw chunks/turns into
        ONE reader entry + dedups cross-item near-duplicate facts, so
        the resolved-item reader window admits distinct facts instead of
        flooding on duplicates. PURE function (package_evidence_pool)
        — recall surface unchanged, hermetic no-dupe tests prove the
        ON path is byte-identical on duplicate-free pools.

    Returns the 13-field response shape: ``{answer, abstained,
    question_type, question_date, evidence, context_tokens, model,
    provider, route, cost_estimate_usd, duration_ms,
    retrieval_degraded, retrieved_session_ids}``. ``question_date`` is ALWAYS
    the RESOLVED value
    (server-now-UTC ``YYYY-MM-DD`` default when omitted; the caller
    override when provided). No retrieval time-travel v1 — the pool stays
    the live graph. ``retrieved_session_ids`` (D3 session identity, #3800)
    lists the DISTINCT session ids of the assembled evidence in the order the
    evidence presents them — the structured counterpart of the DERIVED
    ``[session <id>]`` tags; empty when no hit's identity could be derived
    (never fabricated). It is the honest set of identities the retrieved hits
    carry, NOT a mirror of the tags: a hit on the eval lane
    (``lme_session_index``) keeps its historical tag whatever id it carries.
    The derived tag is also part of the BYTE accounting, so a pool already at
    the RESOLVED byte ceiling (128 000 bytes by default; #4105) can admit
    slightly fewer hits than a pool below it (the token cap is unaffected —
    the tag adds bytes, not whitespace words, and the non-ASCII surcharge is
    zero for the ASCII tag).

    Raises: ``AskValidationError`` (input), ``AskRetrievalUnavailable``
    (retrieval/annotation/assembly raise), ``AskReaderUnavailable``
    (the reader failed with no surviving lane).
    """
    import time as _time  # noqa: I001
    from datetime import datetime as _dt2
    from tortoise.retrieval import (
        DEFAULT_MAX_CHUNKS_PER_SESSION,
        _distinct_session_ids,
        apply_evidence_boost,
        ask_env_bool,
        assemble_context,
        dedup_pool,
        estimate_tokens_ask,
        package_evidence_pool,
        render_context,
        resolve_ask_boost_multipliers,
        resolve_ask_retrieval_caps,
    )
    from tortoise.metering import estimate_ask_cost_usd, select_ask_meter_rates
    from tortoise.reader import (
        NO_EVIDENCE_TEXT,
        _looks_abstained,
        build_reader_user_message,
        detect_question_type,
        system_prompt_for,
    )
    from tortoise.exceptions import (
        AskReaderUnavailable,
        AskRetrievalUnavailable,
    )
    # W4 (#2101): additive why-layer enrichment flag (shared resolver).
    from .why import w4_enrichment_enabled

    if os.environ.get("TORTOISE_API_URL"):
        # Hosted client mode: the eval lane needs a LOCAL graph. The hosted
        # /v1/ask surface was removed (#3849), so there is no delegated path
        # to fall back to (mirrors run_ask_assembled).
        raise AskRetrievalUnavailable(
            "the ask lane is eval-only and requires a local graph "
            "(TORTOISE_API_URL is set — the hosted /v1/ask surface no "
            "longer exists)")

    t0 = _time.monotonic()
    # 1. Validation FIRST (P2-8): zero model calls AND zero retrieval
    #    calls for invalid inputs.
    _ask_validate(question, question_type, question_date)
    if question_date is None:
        question_date = _dt2.now(UTC).strftime("%Y-%m-%d")

    # 2. Connected-assembly branch (#2165 Task 6) — env-gated OFF by
    #    default (flag OFF / unrouted / unresolved → legacy byte-identical
    #    by construction: the branch precedes retrieval). Slots AFTER the
    #    hosted-mode check AND after _ask_validate (validation always
    #    precedes the branch: invalid inputs raise AskValidationError on
    #    fired shapes too). Whole-branch envelope: any assembler-stage
    #    raise (classify/walk/render/decorate/enrich/date-parse) maps to
    #    AskRetrievalUnavailable — never an untyped exception. The fired
    #    block's evidence is rendered by the SHARED reader tail below
    #    (ONE reader call, legacy AskReaderUnavailable envelope — no
    #    double metering). R14 drift guard is BEHAVIOURAL (not a source-text
    #    grep): both entry points are driven with the flag on and must return
    #    the assembled shape — tests/test_assembly_sdk.py::test_r14_drift_guard.
    caps = resolve_ask_retrieval_caps()
    fired_block = None
    if ask_env_bool("TORTOISE_ASK_CONNECTED_ASSEMBLY", False):
        try:
            from tortoise.assembly import _assemble_connected
            fired_block = _assemble_connected(
                sdk, question, question_date=question_date, caps=caps)
        except AskRetrievalUnavailable:
            raise
        except Exception as e:  # noqa: BLE001, RUF100 — fired envelope
            raise AskRetrievalUnavailable(
                f"connected assembly unavailable: {type(e).__name__}"
            ) from e
    if fired_block is not None and fired_block.fired:
        # fired: assembled = the assembled post-cap lines; the retrieval
        # knobs/dedup/boost/rerank never run. hits=[] + leg_trace=[] make
        # the shared degradation gate below pass [] to the D8 check (R11:
        # a fired render NEVER reports retrieval_degraded).
        assembled = fired_block.post_cap_lines
        hits: list[dict] = []
        leg_trace: list[dict] = []
        # #4105 review fix: the honest-budget census must cover the FIRED
        # path too. It assembles under the SAME resolved token/byte caps, and
        # a byte-bound drop there was previously silent (assembly.py has no
        # logger), which contradicts the lane's own "never silently accepted
        # and dropped" contract.
        _fired_stats = fired_block.cap_stats or {}
        if _fired_stats.get("dropped_by_byte_cap"):
            _logger.warning(
                "ask lane (connected assembly): byte cap %s dropped %d "
                "hit(s) the token cap admitted (byte budget is the binding "
                "constraint; raise TORTOISE_ASK_CONTEXT_BYTE_CAP or lower "
                "TORTOISE_ASK_CONTEXT_TOKEN_CAP to match)",
                _fired_stats.get("byte_cap"),
                _fired_stats["dropped_by_byte_cap"])
    else:
        # Legacy lane: retrieval (whole-retrieval raises →
        # AskRetrievalUnavailable). A1/A3/A4/A6 (#2070): the ask-lane
        # retrieval knobs resolve ONCE here (env-gated; defaults =
        # historical behavior) and thread into the one bounded RAG pass.
        # A6's retrieval-window limit and the assembly caps resolve IN
        # TANDEM (``resolve_ask_retrieval_caps``) — the gold is cut at
        # ``result_ids[:limit]`` inside the retrieval call BEFORE
        # dedup/assemble, so a cap raise that does not also raise the
        # window changes nothing.
        keep_numeric = ask_env_bool(
            "TORTOISE_ASK_NUMERIC_TOKENS", True)  # A1, default ON
        search_keys_prf = ask_env_bool(
            "TORTOISE_ASK_SEARCH_KEYS_PRF", True)      # A4, default ON
        evidence_boost = ask_env_bool(
            "TORTOISE_ASK_EVIDENCE_BOOST", True)       # A5, default ON
        # A8 (Slice A #2683): the evidence-package assembly arm —
        # ``TORTOISE_ASK_EVIDENCE_ASSEMBLY`` (default OFF — fail-safe,
        # the #1745 default decision; hermetic no-dupe tests prove the
        # ON path is byte-identical to OFF when no near-duplicates
        # exist). mark_for=None = the stored-``has_answer`` fallback
        # (source-session class only) — product graphs carry zero value
        # marks, so the package is pure collapse+ordering-by-rank on
        # real graphs (never a silent mark-driven reorder).
        evidence_assembly = ask_env_bool(
            "TORTOISE_ASK_EVIDENCE_ASSEMBLY", False)
        from tortoise.retrieval import (  # noqa: I001
            ASK_FUSION_WEIGHTS_ENV, ASK_FUSION_K_ENV, ask_env_int,
            ask_env_weights,
        )
        fusion_weights = ask_env_weights(ASK_FUSION_WEIGHTS_ENV, None)  # A3
        fusion_k = ask_env_int(ASK_FUSION_K_ENV, 60)                    # A3
        leg_trace: list[dict] = []
        try:
            hits = sdk.tortoise_fts_query(
                question, limit=caps["limit"],
                pool_size=caps["pool_size"], include_terminal=True,
                leg_trace=leg_trace,
                keep_numeric=keep_numeric,
                search_keys_prf=search_keys_prf,
                fusion_weights=fusion_weights,
                fusion_k=fusion_k)
        except AskRetrievalUnavailable:
            raise
        except Exception as e:  # noqa: BLE001, RUF100 — map to the ask error vocabulary
            raise AskRetrievalUnavailable(
                f"retrieval unavailable: {type(e).__name__}") from e

        # 3. Annotation (batch raise → AskRetrievalUnavailable).
        try:
            annotated = sdk.annotate_ask_hits(hits)
        except AskRetrievalUnavailable:
            raise
        except Exception as e:  # noqa: BLE001, RUF100
            raise AskRetrievalUnavailable(
                f"annotation unavailable: {type(e).__name__}") from e

        # 4. Dedup (annotated session key — P2-20) → A5 evidence boost → A7
        #    rerank → assembly (resolved / pool / byte caps from ``caps``).
        try:
            def _ask_session_key(h: dict) -> str:
                return (h.get("session_id")
                        or h.get("session_date")
                        or f"idx:{h.get('lme_session_index', -1)}")

            deduped = dedup_pool(
                annotated, max_chunks_per_session=DEFAULT_MAX_CHUNKS_PER_SESSION,
                session_key=_ask_session_key)
            # A5 (#2070): evidence-mark boost before assembly (mark_for=None =
            # the stored-``has_answer`` fallback — source-session class,
            # conservative). Zero marks → byte-identical order (all factors
            # 1.0); the boost is a rank reorder, never a filter. Real product
            # graphs carry zero marks until the extractor writes them
            # (documented — the value is measured on seeded fixtures).
            if evidence_boost:
                boost_mult = resolve_ask_boost_multipliers()
                deduped, _boost_stats = apply_evidence_boost(
                    deduped,
                    boost_answer_string=boost_mult["answer_string"],
                    boost_verbatim=boost_mult["verbatim"],
                    boost_source=boost_mult["source"],
                )
            # A7 (#2070): cross-encoder + MMR rerank (env-gated, default
            # OFF — phase 2). Degrade-to-current: any failure keeps the
            # deduped pool untouched; the rerank never raises. Budget
            # guard (#2976): the measured lever costs ~6.6x context, so a
            # reranked set that overruns the SAME resolved token / byte caps
            # ``assemble_context`` enforces is refused WHOLE — degrade to
            # the unreranked order (declared in the stats), never a silent
            # truncation of the reranked set.
            from tortoise.rerank import ask_lane_rerank
            deduped, _rerank_stats = ask_lane_rerank(
                question, deduped, proj=sdk._get_proj(),
                top_k=caps["context_item_cap"],
                max_context_tokens=caps["context_token_cap"],
                max_context_bytes=caps["context_byte_cap"],
                question_date=question_date)
            # A8 (Slice A #2683): package the evidence pool BEFORE the
            # reader window fill — a distilled point's own source raw
            # chunks/turns collapse to one package entry, cross-item
            # near-dupe points restate one fact in one slot, so the
            # capped reader window admits distinct facts instead of
            # flooding on duplicates. Recall surface unchanged; the
            # package shapes only what ``assemble_context`` hands the
            # reader. Env-gated OFF by default (fail-safe); hermetic
            # tests in tests/test_evidence_assembly.py prove the ON
            # path is byte-identical when the pool has no
            # near-duplicates.
            if evidence_assembly:
                deduped, _pkg_stats = package_evidence_pool(
                    deduped, mark_for=None)
            _asm_stats: dict = {}
            assembled = assemble_context(
                deduped, top_k=caps["context_item_cap"],
                max_context_tokens=caps["context_token_cap"],
                question_date=question_date,
                context_item_cap=caps["context_item_cap"],
                byte_cap=caps["context_byte_cap"],
                # #4105: the non-ASCII surcharge is OPT-IN — the ask lane is
                # the one caller that opts in, so the shared function (and
                # its eval re-export, #2070) keeps pre-#4105 default behaviour.
                nonascii_token_surcharge=True,
                stats=_asm_stats)
            # #4105 honest budget: the byte ceiling was a hard literal, so a
            # raised item/token cap was SILENTLY a no-op past 32 KiB. The
            # budget is now resolved IN TANDEM (``resolve_ask_retrieval_caps``)
            # and the assembly census names the binding constraint — if the
            # byte cap is the one dropping hits the token cap admitted, say so
            # LOUDLY rather than accept the budget and drop evidence.
            if _asm_stats.get("dropped_by_byte_cap"):
                _logger.warning(
                    "ask lane: byte cap %s dropped %d hit(s) the token cap "
                    "admitted (byte budget is the binding constraint; raise "
                    "TORTOISE_ASK_CONTEXT_BYTE_CAP or lower "
                    "TORTOISE_ASK_CONTEXT_TOKEN_CAP to match)",
                    caps["context_byte_cap"],
                    _asm_stats["dropped_by_byte_cap"])
        except Exception as e:  # noqa: BLE001, RUF100
            raise AskRetrievalUnavailable(
                f"context assembly unavailable: {type(e).__name__}") from e

    # 5. Question type (deterministic detector or caller override).
    qtype = question_type if question_type is not None \
        else detect_question_type(question)

    # 6. ONE reader call via the per-namespace cached model.
    try:
        evidence = render_context(assembled, question_date=question_date)
        context_tokens = estimate_tokens_ask(evidence)
    except Exception as e:  # noqa: BLE001, RUF100 — map to the ask error vocabulary
        raise AskRetrievalUnavailable(
            f"context rendering unavailable: {type(e).__name__}") from e
    try:
        model = _ask_reader_model(sdk, _reader_factory)
    except Exception as e:  # noqa: BLE001, RUF100 — a build failure is a reader failure
        raise AskReaderUnavailable(
            f"reader unavailable (build): {type(e).__name__}") from e
    try:
        raw, reader_out_tokens = _ask_reader_complete(
            model,
            system=system_prompt_for(qtype),
            user=build_reader_user_message(evidence, question))
    except AskReaderUnavailable:
        raise  # #2280: empty-output failure — never an abstention
    except Exception as e:  # noqa: BLE001, RUF100
        raise AskReaderUnavailable(
            f"reader unavailable: {type(e).__name__}") from e
    finally:
        decr = getattr(model, "decr_inflight", None)
        if decr is not None:
            decr()
    answer = (raw or "").strip()
    abstained = _looks_abstained(answer)
    # #2280: an empty output can no longer reach here — the escalation
    # helper either returns non-empty text or raises AskReaderUnavailable.
    # ``abstained`` is therefore ALWAYS the model's written abstention
    # decision, never a blank-output substitution. (The substitution is
    # retained as a defensive invariant for a future caller that skips
    # the helper — it must never fire on this lane's path.)
    if abstained and not answer:
        answer = NO_EVIDENCE_TEXT

    # 7. Metering (best-effort; ONLY with an explicit org_id).
    # #2069: the record's cost_usd is metered at the SERVING lane's
    # family rates (``select_ask_meter_rates`` on ``_LockedReader.model``
    # — the strong lane never under-counts at the deepseek envelope).
    # #3981: metering never blocks the answer — but a dropped increment is
    # never silent. ``record_ask_usage`` raises on an unresolvable metering
    # window; that raise is a SIGNAL, not a refusal (the answer is already
    # produced by this point), so it is absorbed here and reported to the
    # operator as lane=ask_ledger.
    if org_id:
        try:
            from tortoise.metering import record_ask_usage
            input_tokens = (estimate_tokens_ask(system_prompt_for(qtype))
                            + estimate_tokens_ask(evidence))
            out_tokens = reader_out_tokens or 500
            record_ask_usage(
                org_id,
                tokens_in=input_tokens, tokens_out=out_tokens,
                cost_usd=estimate_ask_cost_usd(
                    input_tokens, out_tokens,
                    rates=select_ask_meter_rates(
                        getattr(model, "model", None) or "")),
                _selfhost_transport=_selfhost_transport)
        except Exception as e:  # noqa: BLE001, RUF100 — never blocks
            # #3981: the alert import is itself guarded — the metering
            # module may be the thing that failed, and an unguarded import
            # inside this handler would lose the already-produced answer.
            try:
                from tortoise.metering import report_unmetered_increment
            except Exception:  # noqa: BLE001, RUF100 — never blocks
                _logger.error(
                    "UNMETERED INCREMENT (#3981): lane=ask_ledger "
                    "team=%s error=%s: %s (metering module unavailable)",
                    org_id or "<none>", type(e).__name__, e)
                # The fallback still ALERTS — the ledger write and the reporter
                # are both down here, so a log line is the only other signal.
                # ``operator_alert`` is importable when ``metering`` is not.
                try:
                    from tortoise.operator_alert import alert_unmetered_increment
                except Exception:  # noqa: BLE001, RUF100 — never blocks
                    pass
                else:
                    alert_unmetered_increment("ask_ledger", org_id, e)
            else:
                report_unmetered_increment(lane="ask_ledger",
                                           org_id=org_id, error=e)

    # 8. Degradation signal (leg_trace + D8-decoration-unavailable).
    degraded = any(bool(leg.get("degraded")) for leg in leg_trace)
    if not degraded:
        try:
            degraded = _ask_d8_decoration_unavailable(hits)
        except Exception:  # noqa: BLE001, RUF100 — degrade to False
            degraded = False

    # W4 (#2101): additive why-layer entries for the evidence pool the
    # reader saw (flag-gated — the ``why`` key is ABSENT with the flag
    # OFF, keeping the response byte-identical for the flag-only key). The hits
    # already carry the search-path enrichment; projection is a pure
    # dict op (zero extra graph reads). Fail-open: any error → ``[]``.
    why_entries: list[dict] = []
    if w4_enrichment_enabled():
        try:
            from .why import item_to_why_entry
            for _hit in assembled:
                _entry = item_to_why_entry(_hit)
                if _entry and _entry.get("point_id"):
                    why_entries.append(_entry)
        except Exception as e:  # noqa: BLE001, RUF100 — fail-open
            _logger.warning("W4 why-layer enrichment failed (ask): %s", e)
            why_entries = []
    serving = getattr(model, "last_route", None) or \
        getattr(model, "route", None)
    # D3 session identity: the DISTINCT derived session ids of the
    # assembled evidence, in the order the evidence presents them (this
    # lane's post-dedup ranking order — post-boost, and when enabled
    # post-rerank (A7) / post-package (A8) — NOT raw RRF once
    # ``apply_evidence_boost`` has reordered the pool). Derived from the
    # SAME hits the reader window contains, in the same order, so the field
    # and the evidence cover exactly the same hits; the TAG each hit
    # shows can still differ when the hit carries ``lme_session_index``
    # (that lane's index tag wins — see ``_render_block``). Hits whose
    # identity cannot be derived contribute nothing (never fabricated).
    retrieved_session_ids = _distinct_session_ids(assembled)
    duration_ms = int((_time.monotonic() - t0) * 1000)
    try:
        # #2069: the response's cost_estimate_usd uses the SERVING lane's
        # family rates (STRONG for qwen//upstage//anthropic// specs — a
        # strong-lane ask metered at the deepseek envelope would
        # under-count ~10×).
        cost_estimate = estimate_ask_cost_usd(
            estimate_tokens_ask(system_prompt_for(qtype))
            + estimate_tokens_ask(evidence),
            reader_out_tokens or 500,
            rates=select_ask_meter_rates(
                getattr(model, "model", None) or ""))
    except Exception:  # noqa: BLE001, RUF100
        cost_estimate = 0.0
    resp = {
        "answer": answer,
        "abstained": abstained,
        "question_type": qtype,
        "question_date": question_date,
        "evidence": evidence,
        "context_tokens": context_tokens,
        "model": getattr(model, "model", None),
        "provider": serving,
        "route": serving,
        "cost_estimate_usd": cost_estimate,
        "duration_ms": duration_ms,
        "retrieval_degraded": degraded,
        "retrieved_session_ids": retrieved_session_ids,
    }
    # W4 (#2101): additive why-layer entries — emitted ONLY with the W4
    # flag ON (absent otherwise — every non-``why`` field, including the
    # D3 ``retrieved_session_ids``, stays byte-identical).
    if w4_enrichment_enabled():
        resp["why"] = why_entries
    return resp

def _ask_d8_decoration_unavailable(hits: list[dict]) -> bool:
    """D8-decoration-unavailable detection (P1-13/P1-5): terminal-status
    hits returned WITHOUT supersession keys when ``include_terminal=True``
    — the decoration silently failed to attach the markers, so the
    evidence would render superseded content as current — surfaced as
    ``retrieval_degraded=True`` (never a silent success)."""
    from tortoise.search_engine import TERMINAL_EXCLUDED_STATUSES
    for h in hits:
        if ((h.get("status") or "") in TERMINAL_EXCLUDED_STATUSES
                and not (h.get("superseded_by") or h.get("supersedes")
                         or h.get("valid_from") or h.get("valid_to")
                         or h.get("expired_at"))):
            return True
    return False

def run_ask_assembled(sdk: TortoiseSDK, question: str, *,
                      question_date: str | None = None,
                      question_type: str | None = None,
                      caps: dict | None = None,
                      _reader_factory=None) -> AssemblyAnswer:
    """#2165 Task 6 — connected-assembly ask (the eval arm's reader path).

    ⛔ EVAL-ONLY (#3849) — not a product surface: no MCP tool, no SDK method,
    no REST route. Called only from eval/test code — no product caller.

    One fired pipeline (classify → resolve → walk → render → decorate →
    enrich → assemble) with a PURE-ASSEMBLY default: when no reader is
    supplied the assembly fields populate and ``answer=None`` — the arm
    measures gold-id admission (``post_cap_lines``) independently of any
    reader. With a reader via ``_reader_factory`` the ONE reader call
    fills ``answer`` (the shared reader machinery + legacy
    AskReaderUnavailable envelope). Fires regardless of the caller's
    ``question_type``, but the RESPONSE reports the caller/detector value
    unchanged. ``fired=False`` returns the empty shape (no block, no
    raise). In hosted client mode (``TORTOISE_API_URL`` set, no local graph)
    raises ``AskRetrievalUnavailable`` — the hosted answer surface was
    removed (#3849) and no longer exists.
    """
    import os as _os

    from tortoise.exceptions import (
        AskReaderUnavailable,
        AskRetrievalUnavailable,
    )
    if _os.environ.get("TORTOISE_API_URL"):
        raise AskRetrievalUnavailable(
            "ask_assembled requires a local graph (TORTOISE_API_URL is "
            "set — the hosted /v1/ask surface was removed in #3849 and no "
            "longer exists)")
    from tortoise.assembly import AssemblyAnswer as _AssemblyAnswer
    from tortoise.assembly import _assemble_connected
    from tortoise.reader import (
        NO_EVIDENCE_TEXT,
        _looks_abstained,
        build_reader_user_message,
        detect_question_type,
        system_prompt_for,
    )
    from tortoise.retrieval import (
        estimate_tokens_ask,
        render_context,
        resolve_ask_retrieval_caps,
    )
    # validation FIRST (same canonical codes as run_ask_lane()); date
    # resolved to
    # server-now-UTC when omitted (identical default semantics)
    _ask_validate(question, question_type, question_date)
    if question_date is None:
        from datetime import UTC as _UTC2
        from datetime import datetime as _dt3
        question_date = _dt3.now(_UTC2).strftime("%Y-%m-%d")
    if caps is None:
        caps = resolve_ask_retrieval_caps()
    try:
        block = _assemble_connected(
            sdk, question, question_date=question_date, caps=caps)
    except AskRetrievalUnavailable:
        raise
    except Exception as e:  # noqa: BLE001, RUF100 — fired envelope
        raise AskRetrievalUnavailable(
            f"connected assembly unavailable: {type(e).__name__}") from e
    qtype = question_type if question_type is not None \
        else detect_question_type(question)
    if not block.fired:
        return _AssemblyAnswer(
            fired=False, shape=None, question_type=qtype, subjects=[],
            slices={}, post_cap_lines=[], admission={}, evidence="",
            context_tokens=0, answer=None, retrieval_degraded=False)
    evidence = render_context(block.post_cap_lines,
                              question_date=question_date)
    context_tokens = estimate_tokens_ask(evidence)
    answer: str | None = None
    if _reader_factory is not None:
        # the ONE reader call (pure-assembly mode when no reader — the
        # eval arm decides whether conversion is measured)
        try:
            model = _ask_reader_model(sdk, _reader_factory)
        except Exception as e:  # noqa: BLE001, RUF100
            raise AskReaderUnavailable(
                f"reader unavailable (build): {type(e).__name__}") from e
        try:
            raw, _out_tokens = _ask_reader_complete(
                model,
                system=system_prompt_for(qtype),
                user=build_reader_user_message(evidence, question))
        except AskReaderUnavailable:
            raise
        except Exception as e:  # noqa: BLE001, RUF100
            raise AskReaderUnavailable(
                f"reader unavailable: {type(e).__name__}") from e
        finally:
            decr = getattr(model, "decr_inflight", None)
            if decr is not None:
                decr()
        answer = (raw or "").strip()
        if _looks_abstained(answer) and not answer:
            answer = NO_EVIDENCE_TEXT
    return _AssemblyAnswer(
        fired=True, shape=block.shape, question_type=qtype,
        subjects=list(block.subjects), slices=block.slices,
        post_cap_lines=block.post_cap_lines,
        admission=dict(block.admission), evidence=evidence,
        context_tokens=context_tokens, answer=answer,
        retrieval_degraded=False)

# ── Per-namespace reader-model cache (eval lane) ──

def _ask_reader_model(sdk: TortoiseSDK, factory=None):
    """Resolve the ask-lane reader model for THIS SDK's namespace from the
    per-namespace cache (never module-global): LRU bound, in-flight
    entries never evicted, failed builds never cached, per-key build
    single-flight, closed clients on eviction."""
    namespace = getattr(sdk, "_namespace", None) or "default"
    key = f"ask:{namespace}"
    cache = _ask_reader_cache()
    with _ASK_READER_CACHE_LOCK:
        entry = cache.get(key)
        if entry is not None and not entry.failed():
            cache.move_to_end(key)
            entry.incr_inflight()
            return entry
    # Build path — per-key single-flight (P2-16): two simultaneous FIRST
    # asks for the same namespace produce ONE build.
    build_lock = _ask_build_lock(key)
    with build_lock:
        with _ASK_READER_CACHE_LOCK:
            entry = cache.get(key)
            if entry is not None and not entry.failed():
                cache.move_to_end(key)
                entry.incr_inflight()
                return entry
        try:
            builder = factory if factory is not None \
                else _default_ask_reader_factory()
            model = builder() if callable(builder) else builder
            locked = _LockedReader(model)
        except Exception:
            # Failed builds are NEVER cached (P2-21) — the key stays
            # absent so a subsequent ask rebuilds and succeeds. Also
            # drop the per-key single-flight lock (P2): a failed build
            # leaves NO cache entry to evict alongside it, so without
            # this the lock would linger in the module dict forever —
            # unbounded growth under sustained build failure across
            # namespaces. Safe: the failed build leaves no cached state
            # and this thread still holds the lock while unwinding.
            with _ASK_READER_CACHE_LOCK:
                _ask_build_locks.pop(key, None)
            raise
        with _ASK_READER_CACHE_LOCK:
            entry = cache.get(key)
            if entry is not None and not entry.failed():
                # A sibling thread's build landed while this one ran —
                # possible ONLY after a failed build popped the per-key
                # single-flight lock (P2-16): the lock is per-build, so
                # the sibling built on a FRESH lock concurrently. The
                # EXISTING entry wins — never overwrite it without
                # close() (the clobbered _LockedReader's model client
                # socket would leak and its in-flight count orphan).
                cache.move_to_end(key)
                entry.incr_inflight()
                locked.close()
                return entry
            cache[key] = locked
            cache.move_to_end(key)
            locked.incr_inflight()
            _prune_ask_reader_cache(cache)
        return locked

