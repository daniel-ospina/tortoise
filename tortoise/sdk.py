"""Tortoise SDK — Layer 1 facade for Tortoise epistemic graph interaction.

Wraps FalkorProjection (Docker/server FalkorDB by default, embedded via path argument).
Lazy-opens on first call. Returns structured dicts, never raw FalkorDB result sets.

Builder capability catalog note (#2004 W8 / epic #1976 DM-5): this module is
referenced in the builder capability catalog (onboarding) — catalog module
'Session recorder' (``TortoiseSDK.capture_session`` is the SDK recorder
facade) — tortoise/tool_registry.py CAPABILITY_CATALOG. If you add or rename
an extractor/indexer, update the catalog reference.
"""
from __future__ import annotations  # noqa: I001

import hashlib
import json as _json
import logging
import os
import re
import stat
from time import monotonic as _monotonic
from typing import Any

from .domain_loader import known_kinds, register_kind
from .cross_lens import DEFAULT_THRESHOLD
from .ids import ulid
from .embedded_lifecycle import atexit_fast_close  # #1371: registers the batch flush
from .retrieval import DEFAULT_POOL_SIZE, resolve_pool_size
from . import monitoring
from . import file_indexer  # noqa: F401 — import-time sourceKind registration (§4.4)
from .projection import FalkorProjection
from .projection import is_missing_graph_error  # #2163: absent-graph family == success
from .quota import MAX_EXTRACTIONS_PER_TURN, MAX_SESSION_TURNS
from .canonical import derive_batch_id
import threading
import collections
from datetime import UTC

# ── Ask-lane reader-model cache (#1987 Task 5) ─────────────────────────────
# Per-namespace cache (keyed by team/namespace — NEVER a module-global
# model): LRU bound (≤ N entries), in-flight entries NEVER evicted (an LRU
# eviction can never race an in-flight ask), closed clients on eviction,
# failed builds never cached, per-key build single-flight. The cache holds
# the LOCKED WRAPPER — the per-instance lock lives INSIDE the cached object
# (complete() + usage capture under it), so the shared cached wrapper IS the
# locked object (P2-6).

ASK_SDK_TIMEOUT_S = 75  # > the server's _ASK_TIMEOUT_S (60) — its 504 is always receivable
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
    (contention bounded by the per-team in-flight cap 4). Forwards
    ``model``/``provider``/``route``/``last_route``/``last_prompt_tokens``/
    ``last_completion_tokens``/``last_finish_reason`` and ``close()``.
    """

    def __init__(self, model):
        self._model = model
        self._lock = threading.Lock()
        self._inflight = 0

    def complete(self, *, system: str, user: str) -> str:
        with self._lock:
            self._inflight += 1
            try:
                out = self._model.complete(system=system, user=user)
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

# P0 Group 3: register custom kinds for diary + checkpoint
register_kind("diary")
register_kind("checkpoint-item")
register_kind("option")    # used by file_decision (#133)
register_kind("evidence")  # used by file_decision (#133)

# Valid status values for Point nodes (used by update_point status validation)
# #432: claim lifecycle vocabulary — draft → live → retracted → superseded
# (plus outdated/archived). challenged is a DERIVED condition (NAND operator
# edge on a live point), NOT a stored status.
POINT_STATUS_VALUES = frozenset({'draft', 'live', 'retracted', 'superseded', 'outdated', 'archived'})

# #913: statuses that make a Point STALE for review_connections(mode=prune)
# — terminal states plus the legacy outdated flag (supersede/invalidate set
# status='superseded'/'outdated' AND/OR outdated=true). draft/live are the
# only statuses a connection to is NOT stale.
STALE_TERMINAL_STATUSES = frozenset(
    {'retracted', 'superseded', 'outdated', 'archived'})

# Epic #902 W4 A0 — single-source valid-value sets for ingest() (consumed by
# the SDK validation AND the MCP pre-validation so the two layers cannot
# drift; INGEST_CONTRACT.md §2/§5 pins the exact values + error shapes).
INGEST_GRANULARITIES = ("bulk", "granular")
INGEST_PROMOTION_POLICIES = ("gated", "auto")


# ── Session LLM extraction (epic #909 M2 — issue #822) ─────────────────────
# The deterministic regex extraction loop is REMOVED as a product path: LLM
# extraction is the DEFAULT (and only) capture extraction. These helpers build
# the M2 two-stage LLMExtractor (tortoise/extractor.py) from the configured
# provider and run it over a conversation; both hosted_api.capture_session and
# TortoiseSDK.capture_session consume them so the two copies stay in sync.

# Provider priority when MULTIPLE keys are set (first configured wins — the
# deploy-time decision is which key is present; a key is only ever sent to the
# provider that issued it, #329). Order mirrors tortoise.ingest._PROVIDERS.
_SESSION_LLM_PROVIDER_PRIORITY = ("openrouter", "deepseek", "openai", "gemini")

# Default model per provider when TORTOISE_SESSION_LLM_MODEL is unset. The
# provider/model choice is a product decision (deploy-time) — these are
# cheap-tier defaults matching the analyzer's model choices (analyze.py
# _LLM_PROVIDERS) and session_indexer's whitelist family.
_SESSION_LLM_DEFAULT_MODELS = {
    "openrouter": "deepseek/deepseek-chat",
    "deepseek": "deepseek-chat",
    "openai": "gpt-4o-mini",
    "gemini": "gemini-2.0-flash",
}


def _session_llm_provider() -> str | None:
    """First configured session-extraction provider, or None when no provider
    key is set (fail-closed). Mirrors ingest._PROVIDERS exactly — the same key
    set hosted_api._llm_provider_available() reports (#722 parity)."""
    from tortoise.ingest import _PROVIDERS

    for provider in _SESSION_LLM_PROVIDER_PRIORITY:
        if provider in _PROVIDERS:
            _base_url, key_env = _PROVIDERS[provider]
            if os.environ.get(key_env):
                return provider
    return None


def _session_llm_mock_enabled() -> bool:
    """TORTOISE_SESSION_LLM_MOCK=1 test seam — normalized exactly like the
    sibling gates (sdk._build_session_llm_extractor, hosted_api.
    _llm_provider_available): strip + lower, so a padded env value (" 1 ")
    can't diverge the outer gates from _extract_session_v2's inner gate
    (divergence = ValueError → HTTP 500, the #1468 failure class)."""
    return os.environ.get(
        "TORTOISE_SESSION_LLM_MOCK", "").strip().lower() == "1"


def _build_session_llm_extractor():
    """Build the M2 LLMExtractor for session capture from the configured
    provider (or None when no provider key is set — the no-key case fails
    closed). TORTOISE_SESSION_LLM_MOCK=1 is a test seam (precedent:
    TORTOISE_BACKUP_STORAGE=memory / RATE_LIMIT_DISABLED) that swaps in the
    deterministic MockModel so the E2E/unit suites exercise the real LLM
    pipeline shape with zero network."""
    if os.environ.get("TORTOISE_SESSION_LLM_MOCK", "").strip().lower() == "1":
        from tortoise.extractor import LLMExtractor, MockModel

        return LLMExtractor(MockModel("mock-point"), MockModel("mock-relation"))
    provider = _session_llm_provider()
    if provider is None:
        return None
    from tortoise.ingest import _PROVIDERS  # noqa: I001
    from tortoise.extractor import LLMExtractor
    from tortoise.models import OpenAICompatModel

    base_url, key_env = _PROVIDERS[provider]
    model_id = _SESSION_LLM_DEFAULT_MODELS[provider]
    spec = os.environ.get("TORTOISE_SESSION_LLM_MODEL", "").strip()
    if spec:
        if ":" not in spec:
            # Bare <model> — resolves against the configured provider key. The
            # error message promises <model> or <provider>:<model>; before
            # #1194 a bare name fell into the partition() branch where p=<model>
            # and m="", so it could NEVER be accepted (misleading provider
            # mismatch error, or "bad model spec" when it equaled the provider
            # name) — surfaced as an uncaught 500 on every hosted capture.
            model_id = spec
        else:
            p, _, m = spec.partition(":")
            if p and p != provider:
                raise ValueError(
                    f"TORTOISE_SESSION_LLM_MODEL={spec!r} names provider {p!r} but "
                    f"{provider!r} is the configured session provider (its key is set)."
                )
            if not m:
                raise ValueError(f"bad model spec {spec!r}; expected <model> or <provider>:<model>")
            model_id = m
    return LLMExtractor(
        OpenAICompatModel(id=model_id, base_url=base_url, api_key_env=key_env),
        OpenAICompatModel(id=model_id, base_url=base_url, api_key_env=key_env),
    )


def _session_llm_effective_model(spec: str, provider: str | None) -> str:
    """Effective model id that would be sent to the provider wire.

    ``spec`` is the raw TORTOISE_SESSION_LLM_MODEL value (`<provider>:<model>`
    or bare `<model>`); the provider prefix is stripped for the wire id, and an
    unset spec resolves to the provider's default. The extractor build
    validates provider matching separately (ValueError on mismatch); this
    helper only resolves the model id for reporting/shape checks."""
    if not spec:
        return _SESSION_LLM_DEFAULT_MODELS.get(provider or "", "")
    _p, _, m = spec.partition(":")
    return m if (_p and m) else spec


def _session_llm_model_shape_warning(spec: str, provider: str | None) -> str | None:
    """OpenRouter route-shape warning — None when the model id is routable.

    OpenRouter addresses models as `<family>/<model>` (e.g. deepseek/deepseek-chat);
    a bare `<model>` spec (`openrouter:deepseek-chat`) builds an extractor fine
    but the FIRST capture 404s at call time — the route id is not routable even
    though the provider key is configured. Warn, never fail: only the route
    shape is suspect, the configuration itself is valid."""
    if provider != "openrouter":
        return None
    eff = _session_llm_effective_model(spec, provider)
    if "/" in eff:
        return None
    return (
        f"model {eff!r} lacks <family>/<model> shape — OpenRouter routes are "
        f"family-prefixed (e.g. deepseek/deepseek-chat); the first capture "
        f"would 404 at call time. Set "
        f"TORTOISE_SESSION_LLM_MODEL=openrouter:<family>/<model>."
    )


class _InMemoryEventLog:
    """Duck-typed EventLog (append/read_all) backing the session-capture
    extraction readback. The graph (via the EventAPI projection) is the
    durable store; this log only carries the events of the current capture so
    the caller can read the extracted Points back. No file I/O, no temp files."""

    def __init__(self):
        self._events: list[dict] = []

    def append(self, event: dict) -> None:
        self._events.append(event)

    def read_all(self) -> list[dict]:
        return list(self._events)


# #1529 P1 (E3 owner note): whitelist of point properties that pass through
# the capture response's `props` superset — E3 writes source_turn_id /
# search_keys / when / quote via the v2 payload point dict / the M2 folded
# statement dict; capture must never drop or overwrite them. Deliberately a
# WHITELIST (not a blacklist): folded statement dicts carry internal
# projection state (provenance run_id/source, status, createdAt, operator,
# speaker) that must never leak into the public capture response. E3 (#1535)
# landed search_keys/when/quote on the v2 payload — extend here, keep the
# no-clobber turn-point guard (MERGE SET list excludes these).
_CAPTURE_PASSTHROUGH_PROPS = frozenset({
    "source_turn_id", "search_keys", "when", "quote"})


def _session_llm_transcript(conversation: list[dict]) -> tuple[str, int]:
    """Build the LLM-extraction transcript + pre-write estimate for a
    conversation (#822). One ``Speaker: text`` line per turn (the
    extractor._utterances segmenter format; non-name roles fall back to
    ``Speaker``), content from the SAME 5000-char window the turn Points store
    (#721 parity — a phrase past the cut has no home in any stored turn),
    newlines flattened so multi-line turns are still extracted, sentences
    capped per turn at MAX_EXTRACTIONS_PER_TURN (the #329 flood gate — the
    removed regex loop capped extracted Points per turn the same way).

    Returns ``(transcript, estimate)`` where ``estimate`` is the fail-closed
    upper bound on NEW non-episodic nodes: extracted Points (one per sentence)
    ×2 for the M2 relations stage's IMPL/NAND operator nodes (the regex loop
    never created operators). The ×2 is a true ceiling only because the session
    extractor clamps operators ≤ points (LLMExtractor.run dedupe+cap, #1194) —
    a permissive relation model can no longer write more operator nodes than
    points, which would have bypassed this estimate and the 402 gate it feeds."""
    from tortoise.extractor import _SENT

    lines: list[str] = []
    n_sentences = 0
    for turn in conversation:
        raw_role = turn.get("role")
        role = raw_role if isinstance(raw_role, str) else (
            "unknown" if raw_role is None else str(raw_role))
        speaker = role.title()
        if not re.match(r"^[A-Z][\w .'-]{0,40}$", speaker):
            # non-word roles (123, dict str, weird casing) still extract under
            # a generic speaker label — never drop the turn's content.
            speaker = "Speaker"
        raw = turn.get("content")
        content = raw if isinstance(raw, str) else ("" if raw is None else str(raw))
        body = " ".join(content[:5000].split())
        sents = [s.group(0).strip() for s in _SENT.finditer(body)]
        sents = [s for s in sents if len(s) >= 3]
        capped = sents[:MAX_EXTRACTIONS_PER_TURN]
        n_sentences += len(capped)
        if capped:
            lines.append(f"{speaker}: {' '.join(capped)}")
    return "\n".join(lines), n_sentences * 2


def _normalize_turn_role(raw) -> str:
    """Shared turn-role normalization (#1532 D2): None -> 'unknown', truthy
    non-strings -> str() — never a raw non-string stored as `speaker`
    (#721) and never None stored as-is (SDK/hosted drift)."""
    if isinstance(raw, str):
        return raw
    return "unknown" if raw is None else str(raw)


def _capture_turn_window(conversation: list[dict], cap: int = 5000) -> list[dict]:
    """Truncate each turn's content to the stored-window cap (#1532 D1).

    Returns a NEW list; the windowed conversation feeds BOTH the turn-store
    loop and the extraction call so the LLM never sees a phrase with no home
    in any stored turn (stored-source parity, #721). Content coercion matches
    the store loop: None -> '', truthy non-strings -> str() (isinstance-first,
    #721). Idempotent when the caller already truncated."""
    out: list[dict] = []
    for turn in conversation:
        t = dict(turn)
        raw = t.get("content")
        content = raw if isinstance(raw, str) else ("" if raw is None else str(raw))
        t["content"] = content[:cap]
        out.append(t)
    return out


# #1352: minimal stopword set for the cheap session-Source topic derivation —
# content-word frequency over the transcript (the metadata extractor's LLM
# path is not available on the capture path; this is the deterministic
# fallback the session Source carries). Deliberately small — domain terms
# (auth, http, deploy) must survive the filter.
_SESSION_SOURCE_STOPWORDS = frozenset({
    "a", "an", "the", "and", "or", "but", "so", "for", "with", "without",
    "from", "into", "onto", "over", "under", "about", "against", "between",
    "through", "during", "before", "after", "above", "below", "to", "of",
    "in", "on", "at", "by", "is", "are", "was", "were", "be", "been",
    "being", "have", "has", "had", "do", "does", "did", "will", "would",
    "can", "could", "should", "may", "might", "must", "shall", "not",
    "no", "yes", "ok", "it", "its", "this", "that", "these", "those",
    "i", "we", "you", "he", "she", "they", "them", "me", "us", "my",
    "our", "your", "their", "his", "her", "as", "if", "then", "than",
    "so", "too", "very", "just", "really", "there", "here", "when",  # noqa: B033
    "what", "which", "who", "whom", "why", "how", "all", "any", "both",
    "each", "few", "more", "most", "other", "some", "such", "only",
    "own", "same", "also", "because", "into", "out", "up", "down",  # noqa: B033
    "again", "once", "ago", "now", "new", "old", "first", "last",
    "user", "assistant", "speaker", "think", "say", "says", "said",
    "sure", "yeah", "agree", "agreed", "want", "need", "go", "going",
    "get", "got", "see", "know", "like", "make", "take", "come", "well",
    "right", "okay",
})


def _session_source_metadata(transcript: str) -> tuple[str, list[str]]:
    """Derive cheap capture metadata (summary + topics) for the session Source
    from the extraction transcript (#1352) — no LLM call, deterministic:
    the summary is the first substantive utterance, topics are the top
    frequent content words. Returns ("", []) for an empty transcript."""
    if not transcript.strip():
        return "", []
    summary = ""
    for line in transcript.splitlines():
        text = line.split(": ", 1)[1] if ": " in line else line
        text = text.strip()
        if len(text) >= 10:
            summary = text[:200]
            break
    if not summary:
        summary = transcript.strip()[:200]
    from collections import Counter
    words = re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", transcript.lower())
    counts = Counter(w for w in words if w not in _SESSION_SOURCE_STOPWORDS)
    topics = [w for w, _ in counts.most_common(6)]
    return summary, topics


def _session_extraction_estimate(conversation: list[dict], *,
                                 extractor: str | None = None) -> int:
    """Pre-write fail-closed estimate of NEW non-episodic nodes a capture
    will produce. v2 (default, mirrors commit_session's TORTOISE_EXTRACTOR
    selection): 3 x sentence-cap — points + operators (<= points via Layer-1
    drop) + a x1 allowance for entities/events, each ~<= points in the S2/S4
    embed list (#1532 D4). m2: 2 x sentence-cap (points + operators, #822).
    Turn Points/Session/Event are episodic and never counted (#947)."""
    import os
    mode = (extractor or os.environ.get("TORTOISE_EXTRACTOR", "v2")).lower()
    factor = 3 if mode == "v2" else 2
    _transcript, est = _session_llm_transcript(conversation)  # est = sents*2
    return est // 2 * factor


def _session_llm_extraction_estimate(conversation: list[dict]) -> int:
    """Deprecated-compat alias for _session_extraction_estimate (#1532 D4):
    the hosted 402 gate now calls the v2-aware estimator; this name stays for
    pre-migration callers (removed after the migration completes)."""
    return _session_extraction_estimate(conversation)


def _first_non_draft_status(points: list) -> tuple[int, object] | None:
    """Row-9 guard helper (PR #1073): return (index, effective_status) of the
    first point item whose EFFECTIVE status is not exactly 'draft'.

    The effective status is the top-level ``status`` key, or the nested
    ``props={"status": ...}`` value when top-level is absent (create_point
    flattens nested props via _coerce_props — top-level wins on conflict,
    mirrored here). Only the exact canonical string "draft" is storable under
    promotion_policy='gated': case/whitespace variants ("Draft", "draft "),
    terminal statuses, and non-str values are all rejected (EP _live_only
    excludes only exact 'draft', so every other value would be EP-live).

    Shared by TortoiseSDK.ingest and tortoise_ingest so the two layers
    cannot drift. Returns None when every item is draft/absent.
    """
    for i, item in enumerate(points or []):
        if not isinstance(item, dict):
            continue
        st = item.get("status")
        nested = item.get("props")
        has_status = "status" in item
        if not has_status and isinstance(nested, dict):
            st = nested.get("status")
            has_status = "status" in nested
        # An explicit status key present with value None is a violation too
        # (None would otherwise be stored as NULL, which _live_only treats as
        # LIVE) — uniform row-9 message, not the vocabulary error.
        if has_status and st is None:
            return i, None
        if st is not None and str(st) != "draft":
            return i, st
    return None

# #913: whole-graph mode=add cap — pairwise scoring is O(n²) in time AND
# memory (dense cosine matrix + pair dict); a read-only MCP call must not
# OOM a hosted server (#329/#579 bounding precedent). The unscoped candidate
# pool is truncated to the cap most-recently-updated Points (deterministic:
# updatedAt desc, id tie-break); callers with larger graphs should pass a
# scope (bounded at 200 by hybrid retrieval).
REVIEW_ADD_POOL_CAP = 1000

# ── Cross-lens candidate discovery (#438, BYOA) cost bounds ─────────────
# D4: hard cap on candidates per discovery cycle (predictable per-cycle
# cost; local-model pre-filter experiments live in epic #909, NOT here).
CROSS_LENS_CANDIDATE_CAP = 200
# Pool-scan bound: the per-cycle scan is capped at the most-recently-updated
# eligible points so cost stays proportional to new data, not O(n²) over the
# whole graph (indicator I3, #438).
CROSS_LENS_POOL_CAP = 1000
# Per-point ANN recall bound (neighbors pulled per pool point over the
# vector index — HNSW on Docker, brute-force euclidean on embedded).
CROSS_LENS_ANN_TOP_K = 20
# D4-style hard cap on per-point ANN recall: an agent-passed top_k above
# this is clamped, never honored, so the per-cycle recall budget stays
# predictable (embedded brute-force recall is O(pool x total_points) —
# see the _cross_lens_pairs docstring — so an unbounded top_k would
# undermine the cost contract entirely).
CROSS_LENS_ANN_TOP_K_MAX = 100

# #432: declarative spec for the retract/supersede transition guards. NOT
# consulted by update_point per-call (update_point only promotes draft→live);
# every claim transition is observable via a Task 3 emit hook, and no
# transition can slip through update_point unemitted.
_ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    # P2 (code-review): guards allow draft→retracted/superseded (a draft point
    # can be terminal before ever going live); keep the declarative spec
    # aligned with the retract/supersede guards.
    "draft": frozenset({"live", "retracted", "superseded"}),  # promote + terminal
    "live": frozenset({"retracted", "superseded"}),    # via retract_point / supersede_point
    "retracted": frozenset(),                            # terminal
    "superseded": frozenset(),                           # terminal
    "outdated": frozenset({"retracted"}),               # outdated stays a flag; retract allowed
    "archived": frozenset(),                             # terminal (reserved — no v1 write path)
}

# Event types that write to the :GraphEvent store (#432). Other types
# (e.g. PointRevised) only write to the JSONL event log (#548).
_GRAPH_EVENT_TYPES = frozenset({
    "PointAdded",
    "OperatorAdded",
    "PointRetracted",
    "PointSuperseded",
    "OperatorAnnotated",
    "PointPromoted",   # #785: reviewer-gated draft→live promotion
    "OperatorPromoted",  # #785: R16 zombie-operator prevention
    "DedupeRecorded",  # #784: content-dedup candidate recorded/merged
    "DedupeRejected",  # #784: content-dedup candidate rejected
    "ObjectSuperseded",  # #1350: Object status fold source (supersession)
})

# Epic #902 A4 (§4.2): JSONL-ONLY batch_id record type — deliberately NOT in
# _GRAPH_EVENT_TYPES, so _stamp_batch_id writes the prop SET + this record and
# NO GraphEvent-store event (Q4 stays deferred). A10's rebuild pass-2b replays
# these records to restore batch_id (+ content_hash / mitigates_operator_id /
# mitigation_strength) after a rebuild.
_BATCH_ID_RECORD_TYPE = "BatchIdStamped"


def _raise_update_point_status_error(proj, id: str) -> None:
    """#432: error path for the update_point draft→live promote guard.

    Runs a diagnostic existence read ONLY when the guarded SET returned no
    rows, so the happy path stays a single round trip. Missing point →
    ValueError matching the historical missing-point behavior; present but
    not draft → illegal-transition ValueError.
    """
    exists = proj.g.query(
        "MATCH (n:Point {id:$id}) RETURN count(n)", params={"id": id},
    ).result_set[0][0]
    if not exists:
        raise ValueError(f"No point {id!r}")
    raise ValueError(
        f"Illegal status transition — update_point only promotes draft→live; "  # noqa: F541
        f"use retract_point()/supersede_point() for lifecycle transitions"  # noqa: F541
    )

_logger = logging.getLogger(__name__)

# #1709: process-local serialization for the registry recovery mint (FalkorDB
# has no transactions; parity with the Supabase lane's FOR UPDATE row lock —
# concurrent same-token recoveries must not overshoot the non-bootstrap key
# cap). See TortoiseSDK.signup_token_recover.
_SIGNUP_TOKEN_RECOVER_LOCK = threading.Lock()


def _sanitize_props(props: dict, *, reject_id: bool = False) -> dict:
    """#329: reject server-managed fields on tenant write surfaces.

    ``sourcePath``/``source_path`` are server-filesystem fields consumed by the
    operator ``--upgrade-all`` path (projection maps ``source_path`` →
    ``d.sourcePath`` via ``_DOCUMENT_HANDLED``); a tenant setting them turns the
    graph into a file-read oracle. ``id`` overrides on entity surfaces mutate
    node identity / mint tenant-chosen Document ids. Both are rejected with a
    clear ValueError (fail-closed). ``api.add_document``'s explicit
    ``source_path`` parameter is UNTOUCHED — this only guards props passthrough.
    """
    props = dict(props)
    for key in ("sourcePath", "source_path"):
        if key in props:
            raise ValueError(
                f"{key!r} is a server-managed field and cannot be set via props."
            )
    # #1486 (code-review P1): is_episodic is the points-quota discriminator
    # (quota.py counts only `is_episodic IS NULL OR = false` points). A tenant
    # setting it true via props would exclude their points from the quota —
    # unlimited points past the paid-tier cap. Server-managed: internal
    # capture/extractor callers set it via the explicit `is_episodic` kwarg on
    # create_point/create_entity/create_event/update_point (bound before this
    # props passthrough runs), and the MCP tenant tools reject it at the
    # boundary — this reject is the fail-closed backstop for any other surface.
    if "is_episodic" in props:
        raise ValueError(
            "'is_episodic' is a server-managed field (quota discriminator) "
            "and cannot be set via props."
        )
    if reject_id and "id" in props:
        raise ValueError("'id' is server-managed and cannot be set via props.")
    return props


def _flatten_search_keys_prop(props: dict) -> None:
    """R2 (#1541) D3: search_keys must be a FLAT space-joined STRING in the
    graph — FalkorDB's fulltext index (``db.idx.fulltext.createNodeIndex``)
    does NOT index array-valued properties (verified on server v4.16.7: a
    list-valued search_keys never matches, the flat string does). E3's
    canonical payload keeps the list (commit_schema pins ``list[str]`` and
    E3's persistence tests assert the list at the node); the GRAPH value is
    flattened here — a cross-lane deviation the R2 plan pre-authorized with
    an owner flag (plan D3: "R2 normalizes at index time via ' '.join(...) in
    the projection"). The FTS index then matches unqualified query tokens
    against content ∪ search_keys — the issue's "question ∪ search_keys"
    query expansion, at the index level.

    No-op for str values, None, and absent keys (absent search_keys → no
    prop written, the pre-E3 shape). An empty list drops the prop (E3's
    ``or None`` convention).
    """
    sk = props.get("search_keys")
    if sk is None:
        return
    if isinstance(sk, (list, tuple)):
        flat = " ".join(str(s).strip() for s in sk if str(s).strip())
        if flat:
            props["search_keys"] = flat
        else:
            props.pop("search_keys", None)


def _coerce_props(props: dict) -> dict:
    """Flatten a nested 'props' dict into top-level keyword props, in place.

    The MCP server passes props= through as-is (shallow copy, no flatten), so
    this helper is the single place that handles both conventions. Direct SDK
    callers naturally mirror the MCP tool signature and pass props={"k": v}.
    FalkorDB rejects non-primitive property values, so a dict-valued 'props'
    keyword would otherwise fail with
    "Property values can only be of primitive types". Accept both conventions:

      - props={"k": v}  -> k, v merged into top-level props
      - props=None       -> no-op (MCP convention for absent props)
      - flattened kwargs -> unchanged

    A non-dict, non-None 'props' value (e.g. a string) is preserved as a literal
    property named 'props' — scalars are legal FalkorDB property values.
    """
    nested = props.pop("props", None)
    if isinstance(nested, dict):
        # Explicit top-level kwargs are the caller's more specific intent —
        # they win over nested props on collision (mirrors the MCP server,
        # where explicit tool args override user-supplied props).
        props.update({k: v for k, v in nested.items() if k not in props})
    elif nested is not None:
        # Scalar 'props' value — restore as a literal property.
        props["props"] = nested
    return props


# ── ULID validation (Issue #52) ──
# Canonical format (from tortoise/ids.py): <timestamp-hex>-<uuid12>
_ULID_RE = re.compile(r"^[0-9a-f]+-[0-9a-f]{12}$")
# Standard Crockford base32 ULID (26 chars) — recognized as valid
_CROCKFORD_ULID_RE = re.compile(r"^[0-7][0-9A-HJKMNP-TV-Z]{25}$", re.IGNORECASE)


def _is_ulid(s: str) -> bool:
    """Return True if *s* matches a valid ULID format (canonical or Crockford)."""
    return bool(_ULID_RE.match(s) or _CROCKFORD_ULID_RE.match(s))


# #1516: entity ids minted by _entity_name_id / create_entity are PREFIXED
# (label[:3] + '-' + sha256[:26], e.g. ``sub-<hex26>`` / ``obj-<hex26>``).
# These are IDs, not names — a guard that only recognizes bare ULIDs treats
# them as names and runs the stub-creating keyword fallback.
_ENTITY_ID_RE = re.compile(r"^[a-z]{2,3}-[0-9a-f]{26}$")


def _is_entity_id(s: str) -> bool:
    """Return True if *s* is an entity id: the prefixed _entity_name_id format
    OR a bare ULID. Used by the about* wiring guards (create_entity event
    branch) so ID-valued aboutSubject/aboutObject/aboutPoint/aboutDocument
    props never hit the name-resolution fallback.

    Boundary: a NAME shaped exactly like ``[a-z]{2,3}-<26 lowercase hex>``
    (e.g. ``ab-0123456789abcdef0123456789``) is classified as an id and skips
    name resolution — vanishingly rare for human names and consistent with
    the pre-existing bare-ULID/Crockford behavior."""
    return bool(_is_ulid(s) or _ENTITY_ID_RE.match(s))


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def _entity_name_id(label: str, name: str) -> str:
    """Deterministic entity id from name — mirrors create_point's content-hash
    dedup so the projection's MERGE-by-name is IDEMPOTENT: the same name
    always yields the same id, so a second create_object/create_subject
    returns the canonical node id (#452) and the #1155-P1
    ``coalesce($id, o.id)`` ON-MATCH write is a no-op (same id), while a
    name-stub Object minted with a random ulid by the event path still gets
    replaced by the canonical id on the first ObjectRegistered write.
    """
    digest = hashlib.sha256(f"{label}:{name}".encode("utf-8")).hexdigest()[:26]  # noqa: UP012
    return f"{label[:3].lower()}-{digest}"


def _cosine(a: np.ndarray, b: np.ndarray) -> float:  # noqa: F821
    """Cosine similarity between two embedding vectors (#438).

    Stored embeddings round-trip as float32 lists — normalize defensively so
    the shared cosine thresholds (tortoise.embeddings DEFAULT_THRESHOLD /
    NEAR_DUPLICATE_THRESHOLD, bge-small calibration 2026-08-21) apply to
    the exact same metric cross_lens.py thresholds on.
    """
    import numpy as np

    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def _to_iso_utc(value) -> str:
    """Normalize a datetime or ISO-8601 string to UTC ISO-8601 (with +00:00).

    AgentSession startedAt values are stored via
    ``datetime.now(timezone.utc).isoformat()`` (e.g.
    ``2026-08-07T01:20:50.123456+00:00``), so a canonical, timezone-stripped-
to-UTC string keeps ``>=`` / ``<=`` lexicographic comparisons valid regardless
of the caller's local timezone or whether they passed ``Z`` or an offset.
    """
    from datetime import datetime, timezone
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:  # noqa: SIM108
            dt = dt.replace(tzinfo=timezone.utc)  # noqa: UP017
        else:
            dt = dt.astimezone(timezone.utc)  # noqa: UP017
        return dt.isoformat()
    # ISO-8601 string — normalize any timezone/offset to UTC. A naive string
    # (no offset suffix) is treated as UTC, mirroring the naive-datetime
    # branch — NOT as local time (which would shift the filter window by the
    # caller's offset; #243/#244 review).
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)  # noqa: UP017
    return dt.astimezone(timezone.utc).isoformat()  # noqa: UP017


def _save_progress(progress_file: str, directory: str, total: int, processed: int,
                   ingested: int, updated: int, skipped: int, failed: int,
                   errors: list[dict],
                   completed_files: list[str] | None = None) -> None:
    """Save batch indexing progress for resumability."""
    from datetime import datetime, timezone
    try:
        with open(progress_file, 'w') as f:
            _json.dump({
                "started": datetime.now(timezone.utc).isoformat(),  # noqa: UP017
                "directory": directory,
                "total_files": total,
                "processed": processed,
                "ingested": ingested,
                "updated": updated,
                "skipped": skipped,
                "failed": failed,
                "completed_files": completed_files or [],
                "errors": errors[-20:],  # keep last 20 errors
            }, f, indent=2)
    except Exception:
        pass  # progress file is best-effort


# ── Index workflow (epic #900 T3) module helpers ──────────────────────────
# Connection-level retry budget for the bounded-abort disposition (E2E-19 /
# §6.4): DB write failures are per-file failed{retryable:true} while the
# connection can recover, up to this many consecutive failures — then the run
# aborts with a partial report (aborted/aborted_reason).
_INDEX_DB_RETRY_BUDGET = 3

# In-process per-url locks serializing the Source conditional MERGE + outcome
# detection (E2E-9 threads leg): the embedded FalkorDBLite executes concurrent
# same-key MERGEs in a parallel executor that reports "Nodes created: 1" for
# BOTH writers (and even re-fires both ON CREATE branches — commit order
# nondeterministic), so the counter-authority outcome needs the writers
# serialized. The threads leg is single-process (one daemon, per-thread SDK
# instances) — an in-process lock suffices; the bolt:// subprocess leg's stats
# are honest (and the __runId marker backstops cross-process ambiguity).
_source_merge_lock_guard = threading.Lock()
_source_merge_locks: dict[str, threading.Lock] = {}

# T6 (issue #1042): eventKind → Source sourceKind mapping for the legacy
# backfill reconciliation (CYCLE-25 v3.6 #6 spelling: agentSession).
_BACKFILL_SOURCE_KIND: dict[str, str] = {
    "AgentSession": "agentSession",
    "DocumentCreated": "document",
}

# Embedded db paths this PROCESS has opened (the §5.3 busy probe passes
# same-process daemon reuse — the registry records the DAEMON pid, which is
# never our own python pid).
_embedded_busy_known: set[str] = set()
_embedded_busy_guard = threading.Lock()


def _ep_require_calibration_default() -> bool:
    """#1157: shared default calibration posture for EP surfaces.

    Every EP surface (compute_confidence, dream, get_confidence) resolves
    ``require_calibration=None`` to this value — one knob for the #7478
    target ("0 silent uncalibrated EP runs"). Reads
    ``TORTOISE_EP_REQUIRE_CALIBRATION`` (default "1" → True — fail-closed
    since the #344 flip landed via PR #1212; set "0" to opt out). Draft
    points are excluded from the gate (#780/#1212), so draft-heavy test
    graphs stay passable under the fail-closed default.
    """
    import os
    raw = os.environ.get("TORTOISE_EP_REQUIRE_CALIBRATION", "1").strip().lower()
    return raw in ("1", "true", "yes", "on")


# Per-corpus in-process run locks: the embedded FalkorDBLite's cross-connection
# MERGE semantics are broken under concurrency (observed: the ON CREATE branch
# re-fires against an existing key created by ANOTHER connection — the
# counter-authority outcome then lies). The threads leg (E2E-9) runs in ONE
# process, so serializing whole index_directory runs per corpus makes the
# second run's gate see the first's completed units → honest fast-path skips
# (the plan's "later completions report skipped/updated honestly"). The bolt://
# subprocess leg is cross-process (no shared lock) but server-mode stats/MERGE
# are honest there. This is an ORCHESTRATION-side serialization — the
# write-path machinery (conditional MERGE, repair carve-out) is unchanged.
_index_run_lock_guard = threading.Lock()
_index_run_locks: dict[str, threading.Lock] = {}


def _index_run_lock_for(key: str) -> threading.Lock:
    with _index_run_lock_guard:
        lock = _index_run_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _index_run_locks[key] = lock
        return lock


def _mark_embedded_opened(db_path: str) -> None:
    with _embedded_busy_guard:
        _embedded_busy_known.add(str(db_path))


def _stream_to_payload(summary: dict, session_id: str, stream: dict) -> dict:
    """Payload from the constructed graph stream (the wired structure).

    T4 (#1272): stream ids from the LLM are NOT trustworthy (the prompt says
    "the server re-derives them" but the code does not) — re-derive every
    point/event id from content (pt_<sha>/ev_<sha>) and REMAP operator
    src/dst + MITIGATES target triples to the re-derived ids, else Layer-1
    referential integrity fails. Points are enriched with the required
    fields (reason/confidence/c_cal) at the honest neutral prior 0.5/0.5
    (T11 — never derived from LLM text in v1) and status draft (EP-inert
    until #785)."""
    from tortoise.ids import content_hash

    id_map: dict[str, str] = {}
    points = []
    for p in stream.get("points", []):
        if not p.get("id", "").startswith("pt_"):
            continue
        content = p.get("content", "")[:1000]
        new_id = f"pt_{content_hash(content)[:62]}"
        id_map[p["id"]] = new_id
        points.append({
            "id": new_id, "content": content,
            "pointKind": "statement", "reason": "NEW",
            "confidence": 0.5, "c_cal": 0.5,   # neutral prior (T11), never fabricated
            "about_entities": p.get("about_entities", []) or [],
            "source_ref": "session.md", "quote": "", "status": "draft",
        })

    events = []
    for e in stream.get("events", []):
        if not e.get("id", "").startswith("ev_"):
            continue
        content = e.get("content", "")[:1000]
        new_id = f"ev_{content_hash(content)[:62]}"
        id_map[e["id"]] = new_id
        events.append({
            "id": new_id, "eventKind": e.get("eventKind", "occurrence"),
            "content": content, "confidence": 0.5,   # neutral prior (T11)
            "about_entities": e.get("about_entities", []) or [],
            "source_ref": "session.md",
        })

    def _remap(ref):
        return id_map.get(ref, ref)

    def _mapped(ref):
        """True iff the ref is in the re-derived id map. An operator whose
        src/dst/target references an LLM-fabricated id NOT emitted by this
        stream is unknowable — drop it rather than 422 the whole commit
        (P2-3 #1272 review)."""
        return ref in id_map

    operators = []
    for op in stream.get("operators", []) or []:
        o = dict(op)
        if not (_mapped(o.get("src", "")) and _mapped(o.get("dst", ""))):
            continue
        o["src"] = _remap(o.get("src", ""))
        o["dst"] = _remap(o.get("dst", ""))
        if o.get("target"):
            t = dict(o["target"])
            if not (_mapped(t.get("src", "")) and _mapped(t.get("dst", ""))):
                continue
            t["src"] = _remap(t.get("src", ""))
            t["dst"] = _remap(t.get("dst", ""))
            o["target"] = t
        operators.append(o)

    entities = [e for e in stream.get("entities", []) if e.get("name")]
    return {
        "schema_version": "1", "session_id": session_id,
        "client_commit_id": "", "captured_at": _now_iso(),
        "extractor": {"version": "value@0.4.0+construct", "mode": "byok",
                      "calibration_version": "v1"},
        "summary": (summary.get("session") or {}).get("summary", "")[:2000],
        "story_arc": "",
        "provenance_refs": [{"path": "session.md", "spans": []}],
        "sources": [], "entities": entities, "points": points,
        "events": events, "operators": operators,
        "telemetry": {"extractor": {"version": "value@0.4.0+construct", "mode": "byok"},
                      "model": {"provider": "byok", "id": "user-model", "cfg_hash": ""},
                      "counts": {"kept": len(points),
                                 "candidate": len(events),
                                 "segment": 1, "window": 1, "empty_windows": 0},
                      "keep_ratio": None, "dedup_hits": None, "frontier_calls": 2,
                      "llm_cost_usd": None, "extraction_ms": 0, "retry_count": 0,
                      "last_error_code": None, "confidence_histogram": None},
    }


def _now_iso() -> str:
    """UTC now in ISO format (module-level — shared by write paths)."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()  # noqa: UP017


def _source_merge_lock_for(url: str) -> threading.Lock:
    with _source_merge_lock_guard:
        lock = _source_merge_locks.get(url)
        if lock is None:
            lock = threading.Lock()
            _source_merge_locks[url] = lock
        return lock


def _resolve_under_base_realpath(candidate: str, base: str) -> str | None:
    """Realpath-resolved containment check (the index_file path argument gets
    the same resolved-target discipline as progress_file/directory — §6.4
    cycle-7 pin: an in-base symlink whose target resolves OUTSIDE the base
    raises; a lexical check alone would pass the #329 argument-path test)."""
    import os as _os
    try:
        real = _os.path.realpath(candidate)
        from pathlib import Path
        Path(real).relative_to(Path(_os.path.realpath(base)))
        return real
    except (ValueError, OSError):
        return None


def _classify_db_failure(e: BaseException) -> str | None:
    """Map an exception to the §6.4 DB-failure cause-class (E2E-19 naming):
    ``db`` when the write path hit the graph engine (ResponseError /
    ConnectionError / TimeoutError / ENOSPC-family); None otherwise (the
    per-file handler re-buckets it as structural)."""
    import os as _os  # noqa: F401, I001
    import errno
    try:
        import redis.exceptions as _re
        if isinstance(e, (_re.ResponseError, _re.ConnectionError,
                          _re.TimeoutError, _re.BusyLoadingError,
                          _re.InvalidResponse)):
            return "db"
    except Exception:  # noqa: BLE001, RUF100
        pass
    if isinstance(e, OSError) and getattr(e, "errno", None) in (
            getattr(errno, "ENOSPC", None), getattr(errno, "EIO", None),
            getattr(errno, "EROFS", None)):
        return "db"
    return None


# ── Module-level cached registry for kind expansion
_registry_cache: "PackRegistry | None" = None  # noqa: F821, UP037
_registry_lock = threading.Lock()


def _get_kind_expander():
    """Return cached PackRegistry with pre-computed expansion table."""
    global _registry_cache
    if _registry_cache is None:
        with _registry_lock:
            if _registry_cache is None:
                from .pack_registry import PackRegistry  # noqa: I001
                from .pack_registry import default_packs_dir
                packs_dir = default_packs_dir()
                _registry_cache = PackRegistry(packs_dir)
                _registry_cache.load_all()
    return _registry_cache


def _decorate_fallback_hits(results: list[dict], graph) -> list[dict]:
    """Attach promoted epistemic state (D8) to embedded-fallback hits — one
    batch fetch (#1353, E5 #1537). Additive, mirroring SearchResult.to_dict:
    keys are set ONLY when the state is non-empty, so a graph with no
    CORRECTS edges renders byte-identically to today. Decoration must never
    break retrieval — a graph failure returns the hits undecorated."""
    if not results:
        return results
    try:
        from tortoise.search_engine import fetch_point_epistemic_state
        state = fetch_point_epistemic_state(graph, [r["id"] for r in results])
    except Exception:
        _logger.warning("embedded fallback decoration failed — returning "
                        "undecorated hits", exc_info=True)
        return results
    for r in results:
        st = state.get(r["id"]) or {}
        if st.get("status"):
            r["status"] = st["status"]
        if st.get("superseded_by"):
            r["superseded_by"] = st["superseded_by"]
        if st.get("supersedes"):
            r["supersedes"] = st["supersedes"]
        # E6 (#1538) D7: promoted window fields — additive, only when present.
        for key in ("valid_from", "valid_to", "expired_at"):
            if st.get(key):
                r[key] = st[key]
    return results


# ── Session-context digest noise filter (#2207) ─────────────────────────
# The session-start digest (`tortoise context` → TortoiseSDK.session_context(),
# mirrored by hosted /v1/context) must surface GENUINE decision/claim points.
# Rule/config noise and markdown fragments that reach the graph via
# document/transcript extraction — HR separators ('---'), label-led rule
# bullets ('*Gate: filed as child issue…'), heading/table/quote/fence lines,
# bare 'Label: value' config residue — are filtered out at digest time
# (display-layer defense only; the extractor itself is unchanged).
_DIGEST_STRUCTURE_RE = re.compile(r"^(?:[-*=~_`|#>]{2,}|\.{2,}|[-*+]\s*)$")
_DIGEST_MD_LEAD_RE = re.compile(r"^(?:#{1,6}\s|>{1,}|`{3,}|~{3,}|\|)")
# Label-led rule/config lines: an optional list/number marker and optional
# emphasis, then a label ending in ':' before the value — '*Gate: filed as
# child issue…', '- model: gpt-5', '* HARD RULE: Skill Compliance',
# 'TORTOISE_DB_URI: docker://…'. All-caps continuations keep multi-word rule
# labels ('HARD RULE', 'DO NOT EDIT') together; prose claims starting
# mid-sentence are never label-led.
_DIGEST_LABEL_RE = re.compile(
    r"^(?:[-*+]\s+|\d+[.)]\s+)?(?:[*_]{1,2})?"
    r"[A-Za-z0-9][A-Za-z0-9_.-]*(?:\s+[A-Z][A-Z0-9_.-]*)*"
    r"\s*:(?:[*_]{1,2})?\s+\S"
)


def _is_digest_noise(content) -> bool:
    """True when a Point's content is rule/config noise rather than a
    digest-worthy decision/claim (#2207). Pure function over content so the
    local digest, hosted /v1/context and the CLI share one definition."""
    if not isinstance(content, str):
        return True
    t = content.strip()
    if not t:
        return True
    if _DIGEST_STRUCTURE_RE.fullmatch(t):
        return True
    if _DIGEST_MD_LEAD_RE.match(t):
        return True
    return bool(_DIGEST_LABEL_RE.match(t))


class TortoiseSDK:
    """Layer 1 facade for Tortoise epistemic graph interaction.

    Args:
        db_path: Optional path to FalkorDBLite database file (None = use TORTOISE_DB_URI env var).
        namespace: Optional namespace for graph-name isolation.
        event_log_path: Optional path to JSONL event log. When set, SDK write
            paths append events so rebuild_all can restore SDK-created points (#548).
            If None, no events are emitted (backward-compatible).

    Precedence: an explicitly-provided db_path wins over the TORTOISE_DB_URI
    env var. This lets tests/fixtures force a temp embedded DB even when a
    shared test URI is set in the environment (#139).
    """

    def __init__(self, db_path: str | None = None, *, namespace: str | None = None,
                 event_log_path: str | None = None):
        import os, re  # noqa: E401, I001
        db_uri = os.environ.get("TORTOISE_DB_URI")
        if db_uri and db_path is None:
            self._db_path = None
            self._db_uri = db_uri
        else:
            # P0: Crash early if running in production with no database configured.
            # Embedded redislite has no persistent volume → all data lost on deploy.
            # Must evaluate BEFORE resolve_db_path() fills in the default.
            if not db_uri and not db_path:  # noqa: SIM102
                if os.environ.get("FLY_APP_NAME"):
                    raise RuntimeError(
                        "TORTOISE_DB_URI is empty in production. "
                        "Set FALKORDB_PASSWORD (recommended: entrypoint.sh auto-constructs the URI) "
                        "or set TORTOISE_DB_URI directly. "
                        "See docs/infra-runbook.md §1."
                    )
                # Dev/CI: proceed, will use embedded redislite (tests set their own URI)
            # Task 6 wiring (issue #176): when neither a path nor a URI is
            # given, default to the canonical embedded path via resolve_db_path()
            # so the SDK is not blind to TORTOISE_DB_PATH.
            if db_path is None and not db_uri:
                from tortoise.config import resolve_db_path
                db_path = resolve_db_path()
            self._db_path = db_path
            self._db_uri = None
        # Namespace isolation: prefix graph name to segregate data
        if namespace is not None:  # noqa: SIM102
            if not re.match(r'^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$', namespace):
                raise ValueError(
                    f"Invalid namespace {namespace!r}. "
                    "Use alphanumeric, hyphens, underscores; max 64 chars."
                )
        self._namespace = namespace
        self._event_log_path = event_log_path
        self._event_log = None  # lazy-init EventLog (#548)
        # Epic #900 §5.3 (cycle-21): cross-process embedded overlap probe —
        # fail-fast when another PROCESS holds this embedded store (redislite
        # pid-registry + liveness probe). Same-process threads reuse the daemon
        # by construction — the process-local opened-set passes them (the
        # registry records the DAEMON pid, which is never our own python pid).
        if self._db_uri is None and self._db_path:
            self._probe_embedded_busy(self._db_path)
            _mark_embedded_opened(self._db_path)
        self._proj: FalkorProjection | None = None
        self._ep = None  # lazy-init TortoiseEP
        self._evidence: dict[str, tuple[float, float]] = {}
        self._registry_g = None
        self._audit_logger = None
        # Issue #1005 (lifecycle): idempotent close + context-manager support
        # + atexit registration so a NORMAL process exit never orphans the
        # embedded server (the dominant leak path — sessions ending without
        # closing). No weakref.finalize: the atexit bound method keeps the
        # SDK alive until exit, so a GC finalizer could never fire.
        self._t_closed = False
        # #1371: route the atexit seam through the fast-close wrapper (see
        # FalkorDB._atexit_close). _t_close/close/__exit__ are unchanged.
        # #1475 review (P2): register through the embedded_lifecycle wrapper —
        # a bare weakref.WeakMethod is a silent NO-OP (WeakMethod.__call__
        # RETURNS the bound method instead of invoking it, so atexit never
        # runs the body). The wrapper derefs a plain weakref and invokes the
        # method only while the object is alive, keeping the SDK collectable
        # (close-on-GC) without pinning it.
        from .embedded_lifecycle import register_atexit_close
        register_atexit_close(self)
        # Dreaming (#85): dirty claim roots awaiting EP stabilization. Write
        # paths mark affected claims dirty; dream()/lazy-read consume them.
        self._dirty_roots: set[str] = set()
        self._dreamer = None  # lazy-init Dreamer
        # Epic 903-C5 (#1243): non-convergence retention state. A failed
        # pass KEEPS the affected claim-roots dirty (W4 — retry, never
        # silently drop); attempts are counted per root and capped; capped
        # roots are dropped from the dirty set and surfaced as
        # ``stale_unresolved`` (region_attempts record — consumed by the
        # 903-C7 health surface). Backoff state is recorded, not slept —
        # enforcement uses the injectable clock (tests pass a fake).
        self._retry_attempts: dict[str, int] = {}
        self._retry_backoff_until: dict[str, float] = {}
        self._stale_unresolved: dict[str, dict] = {}
        self.retry_attempt_cap: int = 3
        self._retry_clock = _monotonic  # injectable for deterministic tests
        # Epic 903-C7 (#1245): per-pass observability record — the zero-
        # output alarm's inputs + the hosted /v1/dream/health surface
        # (I5 field set). Updated by the dream adapters.
        self._dream_metrics: dict = {
            "last_pass_at": None,
            "last_pass_output": 0,
            "last_pass_mode": None,
            "per_mode_counts": {},
            "pass_count": 0,
            "failure_count": 0,
        }

    def _probe_embedded_busy(self, db_path: str) -> None:
        """Epic #900 §5.3: fail-fast on cross-process embedded overlap.

        Reads the redislite daemon registry (``<db_path>.settings`` → the
        recorded ``pidfile``) and liveness-probes the recorded pid: a LIVE
        holder that is NOT this process raises ``EmbeddedStoreBusyError``
        (naming db_path + holder pid) — never a silent second daemon
        (two in-memory copies = split-brain on the default embedded topology).
        Same-process opens (paths in ``_embedded_busy_known`` — threads reuse
        the daemon by construction) and dead-pid leftovers (crash residue)
        pass.
        """
        import os as _os  # noqa: I001
        import json as _json
        from pathlib import Path as _Path
        if not db_path or str(db_path) == ":memory:":
            return
        if str(db_path) in _embedded_busy_known:
            return  # this process already owns/holds the daemon — threads reuse
        registry = _Path(db_path).with_name(_Path(db_path).name + ".settings")
        if not registry.is_file():
            return
        try:
            settings = _json.loads(registry.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001, RUF100
            return
        pidfile = settings.get("pidfile")
        if not pidfile or not _os.path.isfile(str(pidfile)):
            return
        try:
            pid = int(_Path(pidfile).read_text().strip())
        except Exception:  # noqa: BLE001, RUF100
            return
        if pid <= 0:
            return
        try:
            _os.kill(pid, 0)  # liveness probe
        except ProcessLookupError:
            return  # dead daemon (crash residue) — safe to open
        except PermissionError:
            pass  # exists but not ours to signal — still a live holder
        from tortoise.exceptions import EmbeddedStoreBusyError
        raise EmbeddedStoreBusyError(db_path, pid)

    def _get_proj(self) -> FalkorProjection:
        if self._proj is None:
            # Resolve the URI's own graph name first (used as the fallback
            # when no namespace is set — preserves the conftest per-session
            # test graph, #221).
            uri_graph: str | None = None
            if self._db_uri is not None:
                from urllib.parse import urlparse
                uri_graph = urlparse(self._db_uri).path.lstrip('/') or "tortoise"

            if self._namespace == "registry":
                # Control-plane SDK: shared registry main graph.
                graph_name = "registry_tortoise"
            elif self._namespace:
                if self._namespace.startswith(("test_", "tortoise_test")):
                    # Test namespace: isolate on a test-prefixed graph so the
                    # _assert_test_graph guard still passes (#221). Matches the
                    # historical {ns}_tortoise naming.
                    graph_name = f"{self._namespace}_tortoise"
                elif self._namespace.startswith("test-"):
                    # Epic #1647 (T7, cycle-5 P1-5): the hyphenated test-*
                    # family (test-tiers, test-invites, test-hosted, test-e1,
                    # test-team-722, ...) is a TEST namespace too — normalize
                    # '-' → '_' so it maps to the guard-passing
                    # test_<ns>_tortoise graph (test-tiers →
                    # test_tiers_tortoise). Without the branch it falls
                    # into team_<ns> (team_test-tiers) — a NON-test graph that
                    # is invisible to `grep -v 'namespace="test_'` and fails
                    # _assert_test_graph on bulk wipe.
                    graph_name = f"{self._namespace.replace('-', '_')}_tortoise"
                else:
                    # Team SDK: isolated team graph (matches provision's
                    # team_{team_id} namespace creation, #7886).
                    graph_name = f"team_{self._namespace}"
            else:
                # No namespace: honor the URI's own graph (the conftest
                # session graph for tests). Fixes #7886 regression that
                # hardcoded 'tortoise' and clobbered the test graph.
                graph_name = uri_graph or "tortoise"
            if self._db_uri is not None:
                # Multi-tenant isolation (#7886): pass the namespaced graph
                # name so tenants never share the URI's default graph.
                self._proj = FalkorProjection.from_uri(self._db_uri, graph_name=graph_name)
            else:
                self._proj = FalkorProjection(self._db_path, graph_name=graph_name)
        return self._proj

    def _get_event_log(self):
        """Lazy-init the EventLog for SDK event emission (#548).

        Returns None when no event_log_path was configured — callers MUST
        handle the None case before appending.
        """
        if self._event_log is None and self._event_log_path:
            from tortoise.log import EventLog
            self._event_log = EventLog(self._event_log_path)
        return self._event_log

    def test_guard(self) -> None:
        """Assert the connected graph is safe for destructive test teardowns.

        Raises RuntimeError if the graph appears to be a production graph
        (named ``tortoise`` or ``tortoise_restored_*``).  Test fixtures
        should call this before any ``MATCH (n) DETACH DELETE n``.

        Override with ``TORTOISE_ALLOW_PRODUCTION=1``.
        """
        import os
        if os.environ.get("TORTOISE_ALLOW_PRODUCTION") == "1":
            return

        proj = self._get_proj()
        graph_name = getattr(proj, "graph_name", None)
        if graph_name is None:
            graph_name = getattr(proj.g, "name", "unknown")

        # Block destructive ops on production graphs:
        #   tortoise             — the real graph
        #   tortoise_restored_*  — restored snapshots (precious)
        blocked = (
            graph_name == "tortoise"
            or graph_name.startswith("tortoise_restored")
        )
        if blocked:
            raise RuntimeError(
                f"SAFETY GUARD: Destructive operation blocked on graph "
                f"'{graph_name}'. This appears to be a production graph. "
                f"Use an isolated test graph (e.g. "
                f"'tortoise_test_calibration') instead. "
                f"Override with TORTOISE_ALLOW_PRODUCTION=1."
            )

    def _get_registry(self):
        """Return the control_plane registry graph handle (cached).

        Uses the existing db connection — no second FalkorDB connection.
        Registry graph name is namespace-scoped (``{ns}_control_plane``) so
        different namespaces never share registry state, and test graphs get
        an isolated name (``{ns}_{test_graph}_control_plane``) so parallel
        test runs stay independent (#135, #139).
        """
        if self._registry_g is None:
            proj = self._get_proj()
            graph_name = getattr(proj, "graph_name", None)
            ns = self._namespace or ""
            # Epic #1647 (T7): the hyphenated test-* namespace family is
            # normalized in _get_proj (test-tiers → test_tiers_tortoise); the
            # control-plane prefix must normalize identically so the registry
            # graph ({ns}_{test_graph}_control_plane) — the JOURNAL sweep owns
            # these (wipe_server's prefix filter cannot: namespaced registries
            # start with the ns, not test_).
            if ns.startswith("test-"):
                ns = ns.replace("-", "_")
            if graph_name and graph_name.startswith(("tortoise_test_", "test_")):
                # Keep the test prefix so test-graph guards still apply.
                registry_name = f"{ns}_{graph_name}_control_plane" if ns else f"{graph_name}_control_plane"
            elif ns:
                registry_name = f"{ns}_control_plane"
            else:
                registry_name = "control_plane"
            self._registry_g = proj.db.select_graph(registry_name)
            # Epic #1647 (CI P2): the registry name derived from a TEST graph
            # is `{ns}_{test_graph}_control_plane` — NOT test-prefixed (starts
            # with registry_/ns_), so wipe_server's test-prefix filter skips
            # it AND it never reaches the session journal (the redirect only
            # journals the main graph). Every such mint LEAKED one server
            # graph per construction (E2E-7 GRAPH.LIST bound red at 466+).
            # Journal the registry name too (product writer: env-gated no-op
            # outside a test session).
            from tortoise.projection import _journal_append_product
            _journal_append_product(registry_name)
            self._ensure_registry_indexes()
        return self._registry_g

    def _ensure_registry_indexes(self) -> None:
        """Create indexes on registry graph labels (idempotent)."""
        g = self._registry_g
        if g is None:
            return
        indexes = [
            ("Team", "name"),
            ("Membership", "team_id"),
            ("Membership", "user_id"),
            ("APIKey", "team_id"),
            ("APIKey", "key_hash"),
            ("APIKey", "key_prefix"),
            ("Invitation", "team_id"),
            ("Invitation", "token_hash"),
            ("SignupToken", "lookup_key"),
        ]
        for label, prop in indexes:
            try:
                g.query(f"CREATE INDEX FOR (n:{label}) ON (n.{prop})")
            except Exception:
                _logger.debug("Index may already exist: %s.%s", label, prop)

    # ── Core CRUD ─────────────────────────────────────────────────

    def _sync_tags(self, proj: FalkorProjection, pid: str, tags) -> None:
        """Reconcile TAGGED edges for a point against a tags value (#485).

        Idempotent: MERGE creates any missing :Tag nodes + TAGGED edges, and
        edges to tags no longer in the list are deleted. Only list values are
        synced — matching create_point's behavior, a non-list tag value is
        stored as a plain property but gets no edges (and leaves existing
        edges untouched).
        """
        if not isinstance(tags, list):
            return
        for tag in tags:
            proj.g.query(
                "MATCH (p:Point {id:$pid}) "
                "MERGE (t:Tag {name:$tag}) "
                "MERGE (p)-[:TAGGED]->(t)",
                params={"pid": pid, "tag": tag},
            )
        # Delete edges to tags no longer in the list (diff via graph read to
        # avoid list-parameter IN-clause portability concerns on FalkorDB).
        stale = proj.g.query(
            "MATCH (p:Point {id:$pid})-[:TAGGED]->(t:Tag) RETURN t.name",
            params={"pid": pid},
        ).result_set
        removed = 0
        for row in stale:
            if row[0] not in tags:
                proj.g.query(
                    "MATCH (p:Point {id:$pid})-[r:TAGGED]->(t:Tag {name:$tag}) "
                    "DELETE r",
                    params={"pid": pid, "tag": row[0]},
                )
                removed += 1
        # GC orphaned :Tag nodes when the sync actually removed edges — tag
        # removal can leave a Tag with no TAGGED referrers (#485). Skipped on
        # create_point (no stale edges there), so new-point creation never pays
        # the scan.
        if removed:
            proj.g.query("MATCH (t:Tag) WHERE NOT (t)<-[:TAGGED]-() DELETE t")

    # ── Events: cursor-based poll (Task 5) ────────────────────────────

    @staticmethod
    def _encode_cursor(seq: int) -> str:
        """Opaque cursor: base64url JSON {v:1, seq:N} — ONE format for every
        cursor incl. the empty graph ({v:1, seq:0}). Plan-review P2."""
        import base64
        import json

        raw = json.dumps({"v": 1, "seq": int(seq)}, separators=(",", ":"))
        return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")

    @staticmethod
    def _decode_cursor(cursor: str) -> int:
        """Decode an opaque cursor → seq. Raises ValueError('invalid cursor')."""
        import base64
        import json

        try:
            raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
            data = json.loads(raw)
            if data.get("v") != 1 or "seq" not in data:
                raise ValueError
            seq = int(data["seq"])
            if seq < 0:  # P2 (Qwen): negative cursors must not bypass expiry
                raise ValueError
            return seq
        except Exception:  # noqa: BLE001, RUF100
            raise ValueError("invalid cursor") from None

    def events_poll(self, after: str | None = None, types: list[str] | None = None,
                    limit: int = 100) -> dict:
        """Poll graph/claim events after a cursor (at-least-once; idempotent on replay).

        Returns {"events": [payload dicts ordered by seq], "next_cursor": opaque}.
        after=None → tail (oldest retained). Expired cursor → ValueError(
        'cursor expired — replay from tail'); malformed → ValueError('invalid cursor').
        Types are validated against the EventCodec registry (unknown → ValueError).
        Events live in THIS SDK's graph namespace (the team partition).
        """
        from .event_store import read_after

        after_seq = 0 if after is None else self._decode_cursor(after)
        if types:
            from .shared_state.events import event_types

            registered = event_types()
            unknown = [t for t in types if t not in registered]
            if unknown:
                raise ValueError(f"unknown event type: {unknown[0]}")
        proj = self._get_proj()
        # Lazy retention (plan-review P2 / Task 6 readOnlyHint tension):
        # maintenance purge at most once per TORTOISE_EVENT_RETENTION_INTERVAL
        # per process, so steady-state polls are read-only. Best-effort — a
        # purge failure never blocks the poll.
        self._maybe_purge_events(proj)
        # Expired-cursor check: a NON-ZERO cursor pointing below the graph's
        # min seq was purged/compacted. after_seq == 0 is the "from the start"
        # sentinel (after=None) — it never expires, it just returns all
        # retained events.
        if after_seq != 0:
            # Watermark: first_seq on GraphEventMeta (maintained by purges) —
            # a cursor below it was purged/compacted → expired (410). Works
            # even when the graph is empty after a full purge.
            rows = proj.g.query(
                "MATCH (m:GraphEventMeta) RETURN m.first_seq"
            ).result_set
            first_seq = rows[0][0] if rows and rows[0][0] is not None else None
            if first_seq is not None and after_seq < int(first_seq):
                raise ValueError("cursor expired — replay from tail")
        evs = read_after(proj, after_seq, types=types, limit=limit)
        last = evs[-1]["seq"] if evs else after_seq
        return {"events": evs, "next_cursor": self._encode_cursor(last)}

    _EVENT_PURGE_ATTR = "_tortoise_last_purge"
    _EVENT_PURGE_LAST: float = 0.0  # process-level gate (P1 review fix)

    def _maybe_purge_events(self, proj) -> None:
        """Best-effort, interval-gated retention purge (see events_poll).

        Runs at most once per TORTOISE_EVENT_RETENTION_INTERVAL seconds per
        PROCESS (module-global monotonic — NOT per-projection: hosted REST/MCP
        build a fresh SDK+projection per request, so a per-projection gate
        would fire the purge on EVERY poll). Reads config via env with
        defaults (30d retention, 500k cap, 3600s interval).
        """
        import os
        import time

        interval = int(os.environ.get("TORTOISE_EVENT_RETENTION_INTERVAL", "3600"))
        now = time.monotonic()
        if now - TortoiseSDK._EVENT_PURGE_LAST < interval:
            return
        TortoiseSDK._EVENT_PURGE_LAST = now
        try:
            from .event_store import purge_expired, purge_overflow

            days = int(os.environ.get("TORTOISE_EVENT_RETENTION_DAYS", "30"))
            cap = int(os.environ.get("TORTOISE_EVENT_MAX_PER_TEAM", "500000"))
            purge_expired(proj, retention_days=days)
            purge_overflow(proj, max_events=cap)
        except Exception:  # noqa: BLE001, RUF100
            _logger.warning("event retention purge failed — continuing", exc_info=True)

    def _emit_event(self, type_: str, payload: dict | None = None, *,
                    point: dict | None = None,
                    id: str | None = None, **extra) -> None:
        """Unified event emission: JSONL rebuild log (#548) + graph event store (#432).

        Both stores are best-effort — failures log and continue (never crash
        the mutation).

        Two call styles supported:
        1. ``_emit_event("PointAdded", {"id": ..., ...})``
           — #432: domain payload for the :GraphEvent store.
        2. ``_emit_event("PointAdded", point=point_dict)``
           — #548: full point snapshot for JSONL rebuild replay.
        3. ``_emit_event("PointRevised", id=pid, **props)``
           — #548: id + extra fields for JSONL.

        **Graph event store (#432):** written when *type_* is in
        ``_GRAPH_EVENT_TYPES`` (PointAdded, OperatorAdded, PointRetracted,
        PointSuperseded, OperatorAnnotated). The payload is taken from
        *payload* if given, otherwise synthesized from ``point["id"]`` or
        *id* + *extra*.

        **JSONL event log (#548):** written when *point* is provided (cleaned
        and appended as the ``"point"`` key) or *id* is provided. Events with
        neither are skipped (nothing meaningful to log). The full point
        snapshot is needed for ``rebuild_all`` replay.
        """
        # ── Graph event store (#432) ──────────────────────────────
        if type_ in _GRAPH_EVENT_TYPES:
            graph_payload = payload
            if graph_payload is None:
                if point is not None:
                    graph_payload = {"id": point.get("id")}
                elif id is not None:
                    graph_payload = {"id": id, **extra}
                else:
                    graph_payload = {}
            try:
                from .event_store import append_event, ensure_event_schema, next_seq
                proj = self._get_proj()
                ensure_event_schema(proj)
                seq = next_seq(proj)
                append_event(proj, seq, type_, graph_payload, self.ulid())
            except Exception:  # noqa: BLE001, RUF100
                _logger.warning("event emission failed for %s — continuing", type_)

        # ── JSONL event log (#548) ─────────────────────────────────
        if point is None and id is None:
            return  # nothing meaningful to log
        log = self._get_event_log()
        if log is None:
            return
        from .ids import ulid, now_iso  # noqa: I001
        event: dict = {
            "event_id": ulid(),
            "ts": now_iso(),
            "type": type_,
            "initiated_by": "sdk",
            "projection_version": 2,
        }
        if point is not None:
            # Strip embedding — it is recomputed on replay by
            # _upsert_point_props (vecf32 serialization is fragile).
            # content_hash is also stripped — it is derived from content.
            clean = {k: v for k, v in point.items()
                     if k not in ("embedding", "content_hash")}
            # Operators may not store 'content' as a node property (#548);
            # _upsert_point_props requires it — synthesize a fallback.
            if "content" not in clean:
                op_type = clean.get("op_type", "IMPL")
                op_inputs = (point.get("operator") or {}).get("inputs", [])
                clean["content"] = f"{op_type}({', '.join(op_inputs)})"
            # Similarly, operators may lack 'pointKind' — default to empty.
            if "pointKind" not in clean:
                clean["pointKind"] = ""
            event["point"] = clean
        if id is not None:
            event["id"] = id
        event.update(extra)
        try:
            log.append(event)
        except Exception as exc:
            # The graph mutation already succeeded — a log-write failure must
            # not crash the caller or pretend the write failed. Rebuild parity
            # is best-effort here: rebuild_all's graph snapshot catches any
            # point missing from the log on the next rebuild (#548).
            _logger.warning(
                "failed to append %s event to SDK log %s: %s",
                type_, self._event_log_path, exc,
            )

    def create_point(self, kind: str, content: str, *, is_episodic: bool | None = None,
                      **props) -> dict:
        # #1486 (code-review P1): is_episodic is a SERVER-MANAGED flag — bound
        # to this explicit param (never the props passthrough, which
        # _sanitize_props rejects). Internal capture/extractor callers pass it
        # here; tenant props cannot reach it (MCP boundary rejects + sanitize
        # backstop).
        """Create a new Point node. Raises ValueError if kind is invalid.

        Set dedup=True for idempotent creation (matches by content hash).
        """
        self._validate_kind(kind)
        _coerce_props(props)
        # #329: server-managed fields rejected on the props passthrough
        # (the explicit-id path via props.pop("id") below is preserved for operators)
        props = _sanitize_props(props)
        # R2 (#1541) D3: search_keys is stored flat (see _flatten_search_keys_prop).
        _flatten_search_keys_prop(props)
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()  # noqa: UP017
        proj = self._get_proj()

        # #49 Phase 2: context is REMOVED — raise TypeError if passed
        if "context" in props:
            raise TypeError(
                "create_point() got unexpected keyword argument 'context'. "
                "Context has been removed. Use pointKind for filtering, "
                "anchors for EP scoping, extractedFrom for provenance. See #49."
            )

        if is_episodic is not None:
            props["is_episodic"] = is_episodic  # server-managed (explicit param only)
        # Calibration: pop credibility before storing as node property
        credibility = props.pop("credibility", None)
        # Always compute and store content hash — dedup flag only gates the
        # existing-point lookup, not hash persistence (fix #80).
        ch = _content_hash(content)
        props["content_hash"] = ch
        # Explicit id must be popped BEFORE the dedup branch: the dedup-hit
        # path calls update_point(pid, **props) and a residual 'id' in props
        # crashed with "multiple values for argument 'id'" (review fix, PR
        # #953 — the commit endpoint writes deterministic pt_<sha> ids with
        # dedup=True).
        explicit_id = props.pop("id", None)
        # Idempotency guard: dedup by content hash when requested
        dedup = props.pop("dedup", False)
        # Points enter as draft, go live when first edge is created (#131).
        # Status is popped+validated BEFORE the dedup branch (#1905): a dedup
        # hit must never forward the caller's status into update_point (which
        # rejects any non-'live' status — a gated re-ingest of the same draft
        # item raised a raw ValueError mid-batch, stranding earlier bundle
        # items as committed). Popping up front also keeps vocabulary
        # validation uniform for both paths: an invalid status raises even
        # when the point already exists (no silent-ignore asymmetry).
        status = props.pop("status", "draft")
        # Fail-closed vocabulary validation (mirrors update_point): a
        # non-canonical status (case variant, junk, non-str, typo) would
        # otherwise be stored verbatim and treated as EP-LIVE by _live_only
        # (which excludes only exact 'draft') — a silent-promotion hole
        # (PR #1073 review P0/P1). isinstance guard keeps the error a
        # ValueError for unhashable values (list/dict) too.
        if not isinstance(status, str) or status not in POINT_STATUS_VALUES:
            raise ValueError(
                f"Invalid status {status!r}. Valid statuses: "
                f"{sorted(POINT_STATUS_VALUES)}"
            )
        if dedup:
            ch = _content_hash(content)
            # P1 #49: dedup by content_hash + pointKind (NOT context, which is no longer written)
            existing = proj.g.query(
                "MATCH (n:Point {content_hash:$ch}) "
                "WHERE n.is_operator = false "
                "AND n.pointKind = $kind "
                "RETURN n.id",
                params={"ch": ch, "kind": kind},
            ).result_set
            # A10 CONTENT+KIND FALLBACK SCAN (cycle-17/18): a mid-function
            # crash inside create_point (between the node CREATE and the
            # content_hash/props SET loop) leaves a live Point WITHOUT
            # content_hash — the content-hash MATCH misses, and a second
            # point would be a permanent duplicate no later submission can
            # dedup (exactly-once violated, E2E-6.4). On the shared miss, the
            # fallback matches stored content against the item's content,
            # constrained to hash-less points of the same kind. Order pin:
            # fallback runs FIRST on the miss; a fallback HIT is a dedup (no
            # straddle warning); a fallback MISS runs the raw MATCH below for
            # the pre-change straddle. Rebuild-durable: content+kind survive
            # the #548 snapshot (the hash-less point's content is intact).
            if not existing:
                fallback = proj.g.query(
                    "MATCH (n:Point) "
                    "WHERE n.is_operator = false "
                    "AND n.pointKind = $kind "
                    "AND n.content_hash IS NULL "
                    "AND n.content = $content "
                    "RETURN n.id",
                    params={"kind": kind, "content": content},
                ).result_set
                if fallback:
                    existing = fallback
            if existing:
                pid = existing[0][0]
                # Existing point already stores content_hash — don't re-write it
                # (would make the `if props:` guard always truthy and bump
                # updatedAt on every dedup hit, #80 review).
                props.pop("content_hash", None)
                if credibility is not None:
                    _logger.warning(
                        "credibility=%r ignored — point %s already exists and dedup=True",
                        credibility, pid)
                if props:
                    # Only touch the existing point when the caller passed
                    # other props — a pure dedup hit (no props) must not bump
                    # updatedAt or re-trigger EP dirty-marking (#490 review
                    # P2-1: re-capture would churn confidence for every point).
                    props["updatedAt"] = now
                    self.update_point(pid, **props)
                return self.get_point(pid)

        # Issue #52 — warn when caller passes an explicit non-ULID id
        if explicit_id is not None:
            if not _is_ulid(explicit_id):
                _logger.warning(
                    "create_point received non-ULID id=%r — canonical format is "
                    "<timestamp-hex>-<uuid12>. This will override the auto-generated ULID. "
                    "Prefer omitting 'id' to use auto-generated ULID.",
                    explicit_id,
                )
            pid = explicit_id
        else:
            pid = ulid()

        # Compute embedding (Phase 1A, #7698) — stored as Point property
        embedding = None
        try:
            from .embeddings import compute_embedding
            embedding = compute_embedding(content)
        except Exception:
            pass  # Graceful — embedding is optional

        proj.g.query(
            "CREATE (n:Point {id:$id, content:$c, pointKind:$k, "
            "is_operator:false, status:$st, createdAt:$now, updatedAt:$now}) "
            "SET n.embedding = vecf32($embedding)",
            params={"id": pid, "c": content, "k": kind, "st": status, "now": now,
                    "embedding": embedding},
        )
        # Tag handling: create :Tag nodes + TAGGED edges (#215, #485)
        tags = props.get("tags") or []
        if isinstance(tags, list):
            self._sync_tags(proj, pid, tags)
        for key, val in props.items():
            proj.g.query(
                "MATCH (n:Point {id:$id}) SET n += $props",
                params={"id": pid, "props": {key: val}},
            )
        # P1-1: Ontology v2.1 — link Point → Source via extractedFrom
        if props.get("extractedFrom"):
            proj._link_source(pid, props["extractedFrom"])
            # Inheritance gate dirty-mark: a freshly-sourced point is always
            # inherit-eligible on the next EP run (no interval wait, #398).
            self._invalidate_inheritance_gate([pid])

        # Apply credibility baseline (only on new creation, not dedup)
        if credibility is not None:
            tier_map = {
                "gold": (10, 1), "T0": (10, 1), 0: (10, 1),
                "high": (5, 1), "T1": (5, 1), 1: (5, 1),
                "medium": (3, 1), "T2": (3, 1), 2: (3, 1),
                "low": (2, 1), "T3": (2, 1), 3: (2, 1),
                "unverified": (1.1, 1), "T4": (1.1, 1), 4: (1.1, 1),
            }
            alpha, beta = tier_map.get(credibility, (1, 1))
            self.set_point_baseline(pid, alpha, beta)
        # Dreaming (#85): a new point can carry confidence-affecting props;
        # mark it dirty so the next dream/lazy-read stabilizes it.
        self._mark_dirty([pid])
        # #432+#548 unified: domain payload + full point snapshot for both
        # the :GraphEvent store (subscriptions/poll) and JSONL (rebuild_all).
        self._emit_event("PointAdded", {"id": pid, "kind": kind, "content_hash": ch},
                         point=self.get_point(pid))
        return self.get_point(pid)

    def create_or_update_point(self, kind: str, content: str, **props) -> dict:
        """Idempotent create/update — matches by content hash."""
        return self.create_point(kind, content, dedup=True, **props)

    # ── Resolution helper (Issue #52) ──

    def resolve_id(self, id_str: str) -> dict | None:
        """Resolve any Point ID (legacy / numeric / ULID) to the canonical point.

        Returns the Point dict if found, None otherwise.

        Strategy:
        1. Exact match on Point.id
        2. If the id looks like a numeric reference, search by content/properties
           (best-effort — legacy numeric IDs may not have explicit mappings yet)

        Non-destructive — read-only operation.

        Limitations:
        - For legacy prefix IDs (letta-*, op-*, etc.) with no exact match,
          there is currently no migration mapping to a canonical ULID.
          This is a known gap covered by docs/migrations/id-normalization-plan.md.
        - The resolution is exact-id-first; fuzzy matching is future work.
        """
        proj = self._get_proj()

        # 1. Exact match
        rows = proj.g.query(
            "MATCH (n:Point {id: $id}) RETURN n.id, n.content, n.pointKind, n.status",
            params={"id": id_str},
        ).result_set
        if rows:
            return self.get_point(rows[0][0])

        # 2. If numeric, try finding a point whose properties reference it
        #    (best-effort — many numeric IDs are native node IDs and would have
        #     matched in step 1; this handles edge cases like internal refs)
        if id_str.isdigit():
            # Search for points whose content or any property contains the numeric ID
            rows = proj.g.query(
                "MATCH (n:Point) WHERE n.content CONTAINS $id_str "
                "RETURN n.id, n.content LIMIT 5",
                params={"id_str": id_str},
            ).result_set
            if rows:
                _logger.info(
                    "resolve_id: numeric %r not found as direct id; "
                    "returning best-match point %r", id_str, rows[0][0]
                )
                return self.get_point(rows[0][0])

        return None

    def commit_session(self, conversation=None, session_id=None, *,
                       summary=None, existing_state=None,
                       extractor_model=None, chunk_size=None,
                       base_url=None, api_key=None, mode: str = "fail-closed",
                       extractor: str | None = None,
                       session_date: str | None = None) -> dict:
        """The production commit path (epic #909, issue #1350).

        Extractor selection (reversible):
          - v2 (DEFAULT, ``TORTOISE_EXTRACTOR=v2`` or unset): the 5-stage
            narrative-first pipeline (extractor_v2.extract_session_v2 — S1
            story summary chunked+compiled, S2 map-to-embed, S3 real-backend
            graph search, S4 gap review, S5 deterministic embed execution) →
            a complete Layer-1 payload (story_arc populated, client_commit_id
            replay-safe) POSTed to /v1/sessions/commit.
          - v1 (``TORTOISE_EXTRACTOR=v1`` or ``extractor='v1'`` or a direct
            ``summary=`` argument): the legacy summarize→construct→ground
            path. The env switch is the reversibility seam while the S2/S4
            prompts finish the owner-in-the-loop loop (design doc §5.5).

        The v2 payload is Layer-1 validated BEFORE the POST (a rejected
        payload returns ok=False with the Layer-1 errors — nothing is sent).
        ``chunk_size`` means EDUs per S1 chunk in v2 (default 50) vs EDUs per
        summary chunk in v1 (default 6) — a caller that previously tuned
        v1's 6-EDU summary chunks now tunes v2's S1 chunking; pass
        extractor="v1" for the legacy meaning. ``existing_state`` is v1-only
        (v2's S3 search replaces it). ``mode`` is v1-only (fail-closed vs
        warn).

        ``summary`` may be passed directly (already extracted, v1-shaped) to
        skip extraction — routes to the v1 path. ``session_date`` (E1, #1533)
        is the ISO date the conversation happened on — the S1/S2/S4 date
        anchor; when None, the v2 path defaults it to the capture time
        (``datetime.now(timezone.utc).isoformat()``), so production commits
        get date-anchored extraction by default. Returns the endpoint
        response, or the local extraction result with the payload when the
        endpoint is unreachable."""
        import os
        import uuid
        session_id = session_id or f"session_{uuid.uuid4().hex[:12]}"
        extractor = (extractor or os.environ.get("TORTOISE_EXTRACTOR", "v2")).lower()
        if extractor == "v1" or summary is not None:
            return self._commit_session_v1(
                conversation, session_id, summary, existing_state,
                extractor_model, chunk_size or 6, base_url, api_key, mode)
        return self._commit_session_v2(
            conversation or [], session_id, extractor_model,
            chunk_size or 50, base_url, api_key, session_date)

    def _commit_session_v2(self, conversation, session_id, extractor_model,
                           chunk_size, base_url, api_key,
                           session_date: str | None = None) -> dict:
        """v2 path: S1→S5 via extractor_v2.extract_session_v2 → Layer-1-
        validated payload → POST. Errors are surfaced (ok=False) with the
        payload for inspection — never a silent partial write."""
        from tortoise.extractor_v2 import extract_session_v2  # noqa: I001
        from tortoise.commit_schema import validate_payload_dict
        from datetime import datetime, timezone
        model = extractor_model or _default_byok_model()
        # E1 (#1533, D8): capture time is the production session date — an
        # undated production session is the bug (anchoring is the point of
        # E1). Explicit session_date= overrides.
        if not session_date:
            session_date = datetime.now(timezone.utc).isoformat()  # noqa: UP017
        out = extract_session_v2(model, conversation, sdk=self,
                                 session_id=session_id, chunk_size=chunk_size,
                                 session_date=session_date)
        payload = out.get("payload")
        errors = list(out.get("errors", []) or [])
        if payload is None and not errors:
            # empty/blank conversation short-circuits with no errors and no
            # payload — never report ok=True for a nothing-committed session.
            errors.append("no payload produced (empty or failed conversation)")
        l1_errors: list[str] = []
        if payload is not None:
            l1, _model = validate_payload_dict(payload)
            if not l1.ok:
                for field, reasons in l1.errors.items():
                    for r in reasons:
                        l1_errors.append(f"Layer-1 {field}: {r}")
        if l1_errors:
            errors = l1_errors + errors
        result = {
            "session_id": session_id,
            "ok": not errors and payload is not None,
            "errors": errors,
            "payload": payload,
            "warnings": out.get("warnings", []),
            "minted_kinds": out.get("minted_kinds", []),
            "chain_notes": out.get("chain_notes", []),
            "link_before_create": out.get("link_before_create", []),
            "supersessions": out.get("supersessions", []),
            "story_arc": out.get("story_arc", ""),
            "search": out.get("search", {}),
            "stats": out.get("stats", {}),
        }
        # #1350: carry the client-derived supersession records to the server
        # (the deterministic channel for the Object status fold). Only set
        # when non-empty — old-client payloads stay byte-identical.
        supersessions = out.get("supersessions", []) or []
        if supersessions and payload is not None:
            payload["supersessions"] = supersessions
        if errors or payload is None:
            return result
        try:
            r = _post_commit(payload, base_url=base_url, api_key=api_key)
            # merge the server response but never let the extractor warnings
            # clobber the server's domain-rule warnings[] (the §6.1 contract)
            return {**r, **result, "ok": True,
                    "warnings": (r.get("warnings") or []) + result["warnings"]}
        except Exception as e:
            return {**result, "ok": False, "error": str(e)}

    def _commit_session_v1(self, conversation, session_id, summary,
                           existing_state, extractor_model, chunk_size,
                           base_url, api_key, mode) -> dict:
        """The legacy summarize→construct→ground path (v1, #1272) — kept
        behind TORTOISE_EXTRACTOR=v1 / direct summary= as the reversibility
        seam. See commit_session docstring."""
        from tortoise.value_extractor import (extract_session,  # noqa: I001
                                              validate_summary, check_guards)

        from tortoise.value_extractor import construct_graph
        if summary is None and conversation is not None:
            model = extractor_model or _default_byok_model()
            extracted = extract_session(
                model, conversation, existing_state=existing_state,
                session_id=session_id, chunk_size=chunk_size, mode=mode)
            summary = extracted["summary"]
            errors = extracted.get("errors", [])
            guards = extracted.get("guards", [])
            delta = extracted.get("delta")
        else:
            errors = validate_summary(summary or {}, mode=mode)
            guards = check_guards(summary or {})
            delta = None

        # Step 2: construct the graph structure (arguments as wired points).
        try:
            stream = construct_graph(summary, extractor_model or _default_byok_model())
        except Exception:
            stream = None
        payload = _summary_to_payload(summary, session_id, stream=stream)
        # T5 (#1272): compute the replay-safe client_commit_id in the mapper
        # so the payload is complete on BOTH the POST and the error path
        # (canonical excludes confidence/c_cal/status — enrichment is
        # replay-safe; same content → same id).
        from tortoise.commit_schema import compute_client_commit_id
        payload["client_commit_id"] = compute_client_commit_id(
            payload["session_id"], payload["points"], payload["entities"],
            payload["operators"], payload["summary"], payload["story_arc"],
            payload.get("events", []), payload.get("supersessions", []))
        if errors:
            return {"session_id": session_id, "ok": False, "errors": errors,
                    "payload": payload}
        try:
            r = _post_commit(payload, base_url=base_url, api_key=api_key)
            return {**r, "session_id": session_id, "ok": True,
                    "guards": guards, "delta": delta}
        except Exception as e:
            return {"session_id": session_id, "ok": False, "error": str(e),
                    "payload": payload, "guards": guards, "delta": delta}

    def capture_session(
        self,
        conversation: list[dict[str, str]],
        session_id: str | None = None,
        *,
        max_turns: int = MAX_SESSION_TURNS,
        harness: str | None = None,
    ) -> dict:
        """Capture an agent session into the graph (#312 delta 4, #822).

        Mirrors the hosted POST /v1/sessions logic minus quota/auth:
        turns become episodic Points keyed {session_id}_t{i} (deterministic +
        idempotent), the M2 LLM extractor (epic #909) turns the conversation
        into epistemic Points (+ IMPL/NAND operators — provenance-grounded,
        extracted via the EventAPI projection), plus a :Session node and an
        ontology-compliant :Event {eventKind:'sessionCaptured'} whose
        eventId is stamped onto the extracted Points as their provenance
        surface (#1417 — provenance is eventId, NOT the aboutEvent content
        edge). The deterministic regex loop is removed as a product
        path — LLM extraction is the default and no-key fails closed.

        ``conversation`` is a list of {"role", "content"} dicts. Returns
        {"session_id", "turns", "extracted", "points": [...],
        "extraction_mode", "extraction_provider", "ok", "errors",
        "warnings"} — the v2 path reports "extraction_mode": "llm:<route>"
        (e.g. "llm:deepseek-direct") and the configured
        "extraction_provider" (#1530 D8). P1 #1529 fail-closed contract:
        "ok" is the success signal (never "extracted" — extracted > 0 can
        co-occur with ok=False on partial-emission failure), "extraction_mode"
        is truthful ("llm:<route>" / "llm" on success, "empty" when the
        conversation has no extractable content — always with ok=False and an
        errors entry — and "error" when extraction failed), errors/warnings
        are additive and never clobbered, and an empty/blank conversation is
        rejected BEFORE any write (no Session stub, turns=0).

        Requires an LLM provider key (OPENROUTER/DEEPSEEK/OPENAI/GEMINI_API_KEY)
        or the TORTOISE_SESSION_LLM_MOCK=1 test seam — raises ValueError
        otherwise (fail-closed, mirroring the hosted 503; the no-extractor
        check precedes the empty gate).
        """
        import uuid
        from datetime import datetime, timezone

        if _build_session_llm_extractor() is None:
            raise ValueError(
                "capture_session requires an LLM provider key (set e.g. "
                "OPENROUTER_API_KEY or DEEPSEEK_API_KEY) — the regex "
                "extraction loop was removed as a product path (#822). "
                "Set TORTOISE_SESSION_LLM_MOCK=1 in tests for the offline "
                "MockModel extractor."
            )

        proj = self._get_proj()
        now = datetime.now(timezone.utc).isoformat()  # noqa: UP017
        session_id = session_id or f"session_{uuid.uuid4().hex[:12]}"

        if len(conversation) > max_turns:
            raise ValueError(
                f"Session turn cap exceeded: {len(conversation)} > {max_turns}")

        # #1532 D1: compute the shared stored-window conversation ONCE — the
        # empty/blank gate, the turn-store loop, and the extraction call all
        # consume the SAME window so the extractors can never see a phrase
        # with no home in any stored turn (stored-source parity). Coercion +
        # truncation happen before the first graph write (partial-write guard).
        windowed = _capture_turn_window(conversation)

        # P1 #1529 (D3): empty/blank conversation fails closed BEFORE any write
        # — whole-conversation transcript emptiness (of the STORED window, the
        # exact input the extractors receive), the SAME signal the extractors
        # use, so the gate and the extractors cannot disagree, and
        # pre-mutation (no Session stub). turns reports the COMMITTED state (0)
        # — nothing lands. (The no-extractor ValueError above precedes this
        # gate — hosted 503-first precedent, #1529 OQ14.)
        transcript, _est = _session_llm_transcript(windowed)
        if not transcript.strip():
            return {
                "session_id": session_id,
                "turns": 0,
                "extracted": 0,
                "points": [],
                "extraction_mode": "empty",
                "ok": False,
                "errors": ["no extractable content — empty or blank conversation"],
                "warnings": [],
            }

        # #1727 Slice 2 (Task 11): harness is set set-only-when-present (None
        # NEVER erases a stored value) — the conditional clause keeps the
        # query valid in both embedded and Docker lanes (no unused binding).
        # Review PR #1827 (parity with hosted_api.py): created_at uses
        # coalesce so an idempotent re-POST preserves the ORIGINAL capture
        # time.
        _merge_sets = ["s.created_at=coalesce(s.created_at, $now)",
                       "s.turn_count=$tc", "s.is_episodic=true"]
        _merge_params = {"sid": session_id, "now": now,
                         "tc": len(conversation)}
        if harness:
            _merge_sets.append("s.harness=$harness")
            _merge_params["harness"] = harness
        proj.g.query(
            f"MERGE (s:Session {{id:$sid}}) SET {', '.join(_merge_sets)}",
            params=_merge_params,
        )

        # NOTE: this per-turn loop (episodic turn Points) is duplicated from
        # tortoise/hosted_api.py POST /v1/sessions — the shared primitives
        # #1532 D1/D2 (_capture_turn_window / _normalize_turn_role) keep the
        # two loops byte-identical for identical input: same stored-window
        # truncation, same role normalization (None -> "unknown", truthy
        # non-strings -> str()), same `speaker` property write (delta 5).
        # Hosted additionally adds quota/auth bounds; the extraction that
        # follows the loop is shared via _extract_session_llm/_extract_session_v2
        # (#822). Keep the two in sync when touching either.
        for i, turn in enumerate(windowed):
            # #721: _normalize_turn_role is the isinstance-first pattern — an
            # `or "unknown"` fallback only fixes falsy roles, but TRUTHY
            # non-string roles (123, {"a": 1}) would pass raw and be stored as
            # a non-string `speaker` (contradicting the `speaker | string`
            # ontology row) — and a dict role could fail the Cypher write
            # mid-loop, leaving a partial session. Coerce via str() so the
            # speaker property is always a string; only None maps to "unknown".
            role = _normalize_turn_role(turn.get("role"))
            raw = turn.get("content")
            # #721: defensive coercion — check isinstance FIRST so falsy
            # non-strings (0, False, {}, []) are not swallowed to "" by an
            # `or ""` fallback, then coerce via str() (0 -> "0", False ->
            # "False", [] -> "[]") before the write so the episodic point and
            # the extraction loop share one value. Only None maps to "".
            content = raw if isinstance(raw, str) else ("" if raw is None else str(raw))

            # Episodic turn point — deterministic id, structured speaker tag
            # (delta 5), content hash, session-scoped (never conflated across
            # sessions — #490).
            turn_id = f"{session_id}_t{i}"
            # _capture_turn_window already truncated content to the cap — the
            # [:cap] here is the idempotent no-op keeping the store loop's own
            # window definition explicit (#1532 D1).
            turn_text = f"[{role}] {content[:5000]}"
            proj.g.query(
                "MERGE (t:Point {id:$id}) "
                "SET t.content=$c, t.pointKind=$k, t.is_operator=false, "
                "    t.speaker=$speaker, "
                "    t.is_episodic=true, "
                "    t.status=coalesce(t.status, $s), "
                "    t.createdAt=coalesce(t.createdAt, $now), "
                "    t.updatedAt=$now, t.content_hash=$ch",
                params={"id": turn_id, "c": turn_text, "k": "event",
                        "speaker": role, "s": "draft", "now": now,
                        "ch": _content_hash(turn_text)},
            )
            proj.g.query(
                "MATCH (s:Session {id:$sid}), (t:Point {id:$tid}) "
                "MERGE (s)-[:CONTAINS]->(t)",
                params={"sid": session_id, "tid": turn_id},
            )

        # M2 LLM extraction over the whole conversation (#822) — replaces the
        # regex decision/claim loop (removed as a product path). Shared with
        # the hosted copy so the two stay in sync.
        # #1350: capture runs the v2 5-stage extractor (hosted + selfhost);
        # the M2 two-stage extractor remains behind TORTOISE_SESSION_EXTRACTOR=m2.
        # P1 #1529 (D5): BOTH branches return the same (extracted, meta)
        # structured contract — the M2 branch's meta now carries
        # errors/warnings/mode (no fabricated empty meta) so the assembly is
        # branch-independent and fails closed on either extractor.
        if os.environ.get("TORTOISE_SESSION_EXTRACTOR") == "m2":
            extracted, meta = self._extract_session_llm(
                windowed, session_id, now)
        else:
            extracted, meta = self._extract_session_v2(
                windowed, session_id, now)

        # P1 #1529: the fail-closed assembly consumes the shared contract.
        extraction_errors = list(meta.get("errors") or [])
        extraction_warnings = list(meta.get("warnings") or [])

        # Ontology episodic model (v3.1 §4.5/§3.2): Event node + the point's
        # eventId provenance property. #1417: provenance is the point's
        # eventId property — NOT the aboutEvent content edge (ONTOLOGY §3.4
        # reserves aboutEvent for "What Event this describes"). Stamp each
        # extracted point's provenance surface; aboutEvent stays clean for
        # content (B3's event slot).
        event_id: str | None = None
        try:
            event = self.create_event(
                f"session_{session_id}", "sessionCaptured",
                startedAt=now, endedAt=now, sessionId=session_id,
                is_episodic=True,
            )
            event_id = event.get("id") or event.get("eventId")
            if event_id:
                proj.g.query(
                    "MATCH (n:Point) WHERE n.id IN $ids SET n.eventId=$eid",
                    params={"ids": [p["id"] for p in extracted],
                            "eid": event_id},
                )
            else:
                # P1 #1529 (D4): create_event returning no id/eventId silently
                # skips stamping — surface as an additive warning.
                _logger.warning(
                    "capture_session: sessionCaptured Event write returned no "
                    "id/eventId for session %s — extracted points not stamped",
                    session_id,
                )
                extraction_warnings.append(
                    "sessionCaptured Event write returned no id/eventId — "
                    "extracted points not stamped")
        except Exception as e:
            # Non-fatal — mirrors hosted behavior, but surface the failure so
            # silent event-log loss is visible (#721). P1 #1529 (D4): also
            # append an additive warnings entry (never indistinguishable from
            # a clean capture).
            _logger.warning(
                "capture_session: sessionCaptured Event/EventRecorded write "
                "failed (non-fatal) for session %s: %s", session_id, e,
                exc_info=True,
            )
            extraction_warnings.append(
                f"sessionCaptured Event write failed: {type(e).__name__}: {e}")

        # #1352: the extraction projection auto-created a document-typed Source
        # stub at `session:{id}` (default sourceKind in _link_source) — the
        # ontology v3.6 §4.6 session source kind is agentSession. Materialize
        # the typed Source (capture metadata + sessionId + capturedAt + eventId)
        # and wire (Source)-[:references]->(sessionCaptured Event). Always runs:
        # the Source upgrade is independent of the Event write's health; the
        # references edge is skipped when no Event landed (event_id None).
        # P1 #1529 (D4): a Source materialization failure is non-fatal and
        # surfaced as an additive warning — never a raw exception after
        # partial writes.
        try:
            self._materialize_session_source(
                session_id, event_id, now, conversation)
        except Exception as e:
            _logger.warning(
                "capture_session: session Source materialization failed "
                "(non-fatal) for session %s: %s", session_id, e, exc_info=True,
            )
            extraction_warnings.append(
                f"session Source materialization failed: {type(e).__name__}: {e}")

        # P1 #1529 (D2): truthful extraction_mode + ok/errors/warnings on every
        # response. "empty" always co-occurs with an error entry; belt-and-
        # braces: map mode=="empty" → ok=False regardless of the error list.
        ok = not extraction_errors and meta.get("mode") != "empty"
        if not ok and meta.get("mode") == "empty":
            effective_mode = "empty"
        elif not ok:
            effective_mode = "error"
        elif meta.get("route"):
            effective_mode = f"llm:{meta['route']}"
        else:
            effective_mode = "llm"
        resp = {
            "session_id": session_id,
            "turns": len(conversation),
            "extracted": len(extracted),
            "points": extracted,
            "extraction_mode": effective_mode,
            "ok": ok,
            "errors": extraction_errors,
            "warnings": extraction_warnings,
        }
        # #1530 D8: extraction_provider reports the configured provider when a
        # route was resolved (the v2 path); the M2 path has no route/provider.
        if meta.get("route"):
            resp["extraction_provider"] = meta.get("provider")
        return resp

    def _extract_session_llm(
        self,
        conversation: list[dict[str, str]],
        session_id: str,
        now: str,
    ) -> tuple[list[dict], dict]:
        """M2 LLM extraction (epic #909) over a conversation (#822).

        Runs the two-stage LLMExtractor on the conversation transcript
        through an EventAPI bound to THIS SDK's projection — the same write
        path mining uses (points + IMPL/NAND operators land via the shared
        event projection with provenance spans + extractor-version stamping).
        Every extracted Point is then wired to the session (:CONTAINS edge,
        same as the removed regex loop).

        Returns ``(extracted, meta)`` — the SAME shared contract as
        _extract_session_v2 (#1530 D8; #1529 P1 adds the fail-closed surface):
        ``extracted`` is the [{id, kind, text, props}] list the capture
        contract reports (``kind`` reflects the stored pointKind; the M2
        conversation stage writes untyped Points, reported as "statement";
        ``props`` is the whitelisted _CAPTURE_PASSTHROUGH_PROPS superset so
        E3 fields pass through); ``meta`` = {"provider", "route",
        "failover_used", "errors", "warnings", "mode"} — P1 #1529 makes
        this branch fail closed: extraction-stage exceptions are captured as
        structured errors (never re-raised — turn points have already
        landed), "mode" is "empty" | "error" | "llm", and a completed
        run with zero points is an additive warning, never a silent 0.

        Shared by the hosted copy so the two capture_session loops stay in
        sync — the regex decision/claim loop is removed as a product path
        and no-key fails closed (the caller gates on _build_session_llm_extractor).
        """
        extractor = _build_session_llm_extractor()
        if extractor is None:
            raise ValueError(
                "_extract_session_llm requires an LLM provider key or "
                "TORTOISE_SESSION_LLM_MOCK=1 — no extractor available (#822)")
        transcript, _est = _session_llm_transcript(conversation)
        if not transcript.strip():
            # P1 #1529 (D2): the internal defense-in-depth empty guard must be
            # self-consistent — mode="empty" WITH an error entry, so a caller
            # mapping empty→ok=False can never compute ok=True on this path.
            return [], {
                "provider": None, "route": None, "failover_used": False,
                "errors": ["no extractable content — empty or blank conversation"],
                "warnings": [], "mode": "empty",
            }

        from tortoise.api import EventAPI
        from tortoise.projection import fold, split

        log = _InMemoryEventLog()
        api = EventAPI(log, initiated_by="extractor", agent_id=extractor.version,
                       projection=self._get_proj())
        source_id = f"session:{session_id}"
        # P1 #1529 (D4): no extraction-stage exception escapes the seam — a
        # run() failure is recorded with its class name preserved (P2's fatal-
        # 4xx classification keys on TypeName), and the fold/wiring still runs
        # over whatever the run() partially logged (partial emission is
        # reported, never lost, never a raw exception after partial writes).
        errors: list[str] = []
        try:
            extractor.run(transcript, source_id, api)
        except Exception as e:
            errors.append(f"{type(e).__name__}: {e}")

        extracted: list[dict] = []
        proj = self._get_proj()
        try:
            statements, _operators = split(fold(log.read_all()))
            for p in statements:
                pid = p["id"]
                proj.g.query(
                    "MATCH (s:Session {id:$sid}), (p:Point {id:$pid}) "
                    "MERGE (s)-[:CONTAINS]->(p)",
                    params={"sid": session_id, "pid": pid},
                )
                props = {k: v for k, v in p.items()
                         if k in _CAPTURE_PASSTHROUGH_PROPS}
                extracted.append({
                    "id": pid,
                    "kind": p.get("pointKind") or "statement",
                    "text": p.get("content", "")[:200],
                    "props": props,
                })
        except Exception as e:
            errors.append(f"{type(e).__name__}: {e}")

        warnings: list[str] = []
        if not errors and not extracted:
            # P1 #1529 (D6): completed-but-empty output is an additive
            # warning (nothing extractable ≠ failure), never a silent 0.
            warnings.append("LLM extraction produced no points")
        meta = {
            "provider": None, "route": None, "failover_used": False,
            "errors": errors, "warnings": warnings,
            "mode": "error" if errors else "llm",
        }
        return extracted, meta

    def _extract_session_v2(
        self,
        conversation: list[dict[str, str]],
        session_id: str,
        now: str,
        master: dict | None = None,
    ) -> tuple[list[dict], dict]:
        """v2 LLM extraction over a conversation (#1350) — the 5-stage
        pipeline for hosted/self-hosted capture, replacing the M2 two-stage
        extractor. Runs extractor_v2.extract_session_v2 (S1 story → S2 map →
        S3 real-backend search → S4 gap review → S5 embed) and writes the
        Layer-1 payload (entities/events/points/operators) to THIS SDK's
        graph — entities via create_entity, points via create_point with the
        content-addressed ids, events via create_event, IMPL/NAND via
        create_operator — then wires session CONTAINS + aboutObject edges.

        Returns ``(extracted, meta)`` (#1530 D8 — shared contract, P1
        consumes it): ``extracted`` is the same [{id, kind, text}] contract
        as _extract_session_llm so the capture loops (hosted + selfhost,
        which share this) stay unchanged; ``meta`` = {"provider", "route",
        "failover_used", "errors", "warnings"} carries the provider-routing
        surface (which adapter ran, whether failover was used, and the v2
        pipeline's errors/warnings lists for P1's fail-closed surfacing).

        Fails closed without a routing-usable provider key — the inner gate
        checks exactly what the adapter consumes: DEEPSEEK_API_KEY /
        OPENROUTER_API_KEY (via resolve_extractor_provider; OPENAI_API_KEY is
        NOT accepted — the adapter cannot consume it, #1530 gate match) or the
        TORTOISE_SESSION_LLM_MOCK=1 offline seam (hosted e2e runs the seam
        with provider keys scrubbed, #1468).

        #2031: ``master`` is the optional pre-compiled master list — the
        hosted capture passes the tenant-scoped master
        (``build_master_list(sdk)`` — the tenant-scoped SDK) so tenant pack kinds reach the
        prompts and write gates; None → the default master (self-host
        unchanged, byte-identical).
        """
        # #1530: the inner gate checks exactly what the adapter consumes —
        # a routing-usable key (DEEPSEEK/OPENROUTER via resolve_extractor_provider)
        # or the mock seam. resolve_extractor_provider() itself raises ValueError
        # for an explicit-but-keyless provider (fail closed) — that propagates.
        from tortoise.model_adapters import resolve_extractor_provider
        if not _session_llm_mock_enabled() \
                and resolve_extractor_provider()[0] is None:
            raise ValueError(
                "_extract_session_v2 requires a routing-usable LLM provider key "
                "(DEEPSEEK_API_KEY / OPENROUTER_API_KEY — "
                "TORTOISE_EXTRACTOR_PROVIDER selects the primary, default "
                "deepseek-direct when both are set, #1530) or "
                "TORTOISE_SESSION_LLM_MOCK=1")
        from tortoise.extractor_v2 import extract_session_v2
        if _session_llm_mock_enabled():
            model = _V2SessionMock()
        else:
            # _model_adapter (this module) is the in-module BYOK adapter.
            # The constructor default stays UNCAPPED (max_tokens=None — the
            # adapter-level #1468 semantics are unchanged); the output bound
            # now applies at the _complete seam (M3 #1524): S1 → 1500 / S2,S4
            # → 16000 tokens per stage (#1787 — raised from 8000; the V4
            # ceiling is 384K and the old cap silently truncated dense embed
            # lists), overridable via
            # TORTOISE_EXTRACTOR_MAX_TOKENS, with truncation DETECTED
            # (finish_reason=="length" → census) instead of silently lost. An
            # explicit TORTOISE_EXTRACT_MODEL override keeps the bounded
            # 4000-token default (summary/construct posture, T13 #1272).
            configured = os.environ.get("TORTOISE_EXTRACT_MODEL", "").strip()
            model = (_model_adapter(configured) if configured
                     else _model_adapter("deepseek/deepseek-v4-flash",
                                         max_tokens=None, temperature=0.0))

        out = extract_session_v2(model, conversation, sdk=self,
                                 session_id=session_id, master=master)
        payload = out.get("payload") or {}
        # P1 #1529 (D1/D4): consult out["errors"]/out["warnings"] — the
        # issue's "_extract_session_v2 discards out[errors]" checklist item.
        # The class name is preserved (v2 pipeline errors already carry
        # "TypeName: message") so P2's fatal-4xx classification can key on it.
        errors = [e if isinstance(e, str) else f"{type(e).__name__}: {e}"
                  for e in (out.get("errors") or [])]
        warnings = list(out.get("warnings") or [])
        proj = self._get_proj()

        # ── entities ──
        for e in payload.get("entities", []) or []:
            name = str(e.get("name", "")).strip()
            if not name:
                continue
            try:  # noqa: SIM105
                self.create_entity("object", name,
                                   objectKind=str(e.get("kind", "core:other")),
                                   is_episodic=False)
            except Exception:  # noqa: BLE001, RUF100
                pass

        # ── points + aboutObject edges + session CONTAINS ──
        extracted: list[dict] = []
        skipped = 0
        for pt in payload.get("points", []) or []:
            pid = str(pt.get("id", "")).strip()
            content = str(pt.get("content", "")).strip()
            if not pid or not content:
                skipped += 1
                continue
            try:
                self.create_point(
                    str(pt.get("pointKind", "statement")), content,
                    id=pid, session_id=session_id, is_episodic=False,
                    status="draft",
                    # #1350: link the extracted point to the session Source
                    # (mirrors the M2 EventAPI provenance; create_point wires
                    # the extractedFrom edge).
                    extractedFrom=f"session:{session_id}",
                )
                for name in (pt.get("about_entities") or []):
                    if isinstance(name, str) and name.strip():
                        proj.g.query(
                            "MATCH (p:Point {id:$pid}), (o:Object {name:$n}) "
                            "MERGE (p)-[:aboutObject]->(o)",
                            params={"pid": pid, "n": name.strip()})
                proj.g.query(
                    "MATCH (s:Session {id:$sid}), (p:Point {id:$pid}) "
                    "MERGE (s)-[:CONTAINS]->(p)",
                    params={"sid": session_id, "pid": pid})
                # P1 #1529 (D8/E3): whitelisted props passthrough — E3's
                # source_turn_id (arriving on the payload point dict) must
                # never be dropped or rebuilt into a reduced {id, kind, text}
                # shape.
                props = {k: v for k, v in pt.items()
                         if k in _CAPTURE_PASSTHROUGH_PROPS}
                extracted.append({
                    "id": pid, "kind": "statement", "text": content[:200],
                    "props": props})
            except Exception as e:  # noqa: BLE001, RUF100 — P1 #1529: counted
                # (was a silent `except: pass`) — a per-point write failure
                # surfaces with its class name + id, never an invisible
                # partial write.
                skipped += 1
                errors.append(
                    f"{type(e).__name__}: point write failed for {pid}: {e}")
        if skipped:
            warnings.append(f"{skipped} extracted point(s) failed to write")

        # ── events ──
        for ev in payload.get("events", []) or []:
            content = str(ev.get("content", "")).strip()
            if not content:
                continue
            try:  # noqa: SIM105
                self.create_event(
                    content[:80],
                    str(ev.get("eventKind", "core:occurrence")).rsplit(":", 1)[-1],
                    sessionId=session_id, is_episodic=True)
            except Exception:  # noqa: BLE001, RUF100
                pass

        # ── operators (IMPL/NAND + MITIGATES — shared commit semantics,
        #    #1532 D3: same artifact + deep-miss drop as the commit path via
        #    apply_payload_operators) ──
        ops = payload.get("operators", []) or []
        if ops:
            from tortoise.commit_ops import _payload_point_content_by_id, apply_payload_operators
            apply_payload_operators(
                proj, self, ops,
                point_content_by_id=lambda pid: _payload_point_content_by_id(
                    payload, pid))
        # P1 #1529 (D6): completed-but-empty v2 output (no errors, no points)
        # is an additive warning — nothing extractable is not a failure and
        # never a silent extracted: 0.
        if not errors and not extracted:
            warnings.append("LLM extraction produced no points")
        meta = {
            # #1530 D8: the routing surface — which adapter ran, whether
            # failover was used, and the v2 pipeline's errors/warnings for
            # P1's fail-closed surfacing (shared contract; P1 must not
            # re-shape it). The mock seam has no real route — reports "mock".
            # #1529 P1: meta["mode"] is the truthful branch state the capture
            # assembly maps onto extraction_mode.
            "provider": getattr(model, "provider", "mock"),
            "route": getattr(model, "last_route", None)
            or getattr(model, "route", "mock"),
            "failover_used": bool(getattr(model, "failover_used", False)),
            "errors": errors,
            "warnings": warnings,
            "mode": "error" if errors else "v2",
        }
        return extracted, meta

    def _materialize_session_source(
        self,
        session_id: str,
        event_id: str | None,
        now: str,
        conversation: list[dict[str, str]] | None = None,
    ) -> None:
        """Materialize the typed session Source (#1352).

        The M2 extraction projection auto-creates a Source stub at
        ``session:{session_id}`` via ``_link_source`` with the DEFAULT
        ``sourceKind: 'document'`` (title=url, empty contentHash, no capture
        metadata) — but the ontology v3.6 §4.6 registers the session source
        kind as ``agentSession``. This MERGE upgrades the stub IN PLACE
        (sourceKind, contentHash of the stored transcript, cheap summary +
        topics, sessionId, capturedAt, eventId) and wires
        ``(Source)-[:references]->(sessionCaptured Event)`` — parity with the
        ``_session_event_write`` agentSession pattern and the backfill's
        references edge (test_backfill_sources.py).

        Additive and idempotent: never touches ``_link_source`` or
        ``_session_event_write``; re-capturing a session re-MERGEs the same
        url. The references edge is skipped when ``event_id`` is None (Event
        write failed — the Source materialization is independent of the
        Event's health).
        """
        url = f"session:{session_id}"
        transcript, _ = _session_llm_transcript(conversation or [])
        summary, topics = _session_source_metadata(transcript)
        content_hash = (
            hashlib.sha256(transcript.encode("utf-8")).hexdigest()
            if transcript.strip() else ""
        )
        params = {
            "url": url,
            "sk": "agentSession",
            "sid": session_id,
            "cap": now,
            "ch": content_hash,
            "sum": summary,
            "topics": topics,
            "now": now,
        }
        set_clauses = [
            "s.sourceKind=$sk",
            "s.sessionId=$sid",
            "s.capturedAt=$cap",
            "s.contentHash=$ch",
            "s.summary=$sum",
            "s.topics=$topics",
            "s.ingestedAt=coalesce(s.ingestedAt, $now)",
        ]
        if event_id:
            set_clauses.append("s.eventId=$eid")
            params["eid"] = event_id
        proj = self._get_proj()
        proj.g.query(
            "MERGE (s:Source {url:$url}) SET " + ", ".join(set_clauses),
            params=params,
        )
        if event_id:
            proj.g.query(
                "MATCH (s:Source {url:$url}), (e:Event {eventId:$eid}) "
                "MERGE (s)-[:references]->(e)",
                params={"url": url, "eid": event_id},
            )

    # ── Update / Delete consolidation (epic #888 W2, PR #912) ─────────
    # One update()/delete() for Points AND entities. The legacy methods
    # (update_point/update_entity/delete_point/delete_entity) remain the
    # implementations — update()/delete() resolve the node label and dispatch
    # to them, so behavior is bit-identical for existing callers while the
    # consolidated surface stays the canonical entry.

    def update(self, id: str, **props) -> dict:
        """One update for a Point OR an entity (epic #888 W2).

        Detects the node type by label:
          - Point → point-lifecycle semantics (delegates to update_point):
            draft→live promote via status (only transition allowed), version
            increment for :Point:Object nodes, status validation against
            POINT_STATUS_VALUES, context rejected.
          - Entity (Subject/Object/Event/Document/Source) → plain property
            update (delegates to update_entity).
          - Unknown id → returns {} (no write) — legacy-compatible.
        """
        resolved = self._get_proj()._resolve_entity(
            id, by_id=True, by_eventId=True)
        if not resolved:
            return {}
        if resolved[0]["label"] == "Point":
            return self.update_point(id, **props)
        return self.update_entity(id, **props)

    def delete(self, id: str) -> bool:
        """One delete for a Point OR an entity (epic #888 W2).

        Destructive. Detects the node type by label:
          - Point → delete_point (tag GC + PointRetracted event)
          - Entity → delete_entity
        Returns True if a node was found and deleted, False otherwise.
        """
        resolved = self._get_proj()._resolve_entity(
            id, by_id=True, by_eventId=True)
        if not resolved:
            return False
        if resolved[0]["label"] == "Point":
            return self.delete_point(id)
        return self.delete_entity(id)

    def update_point(self, id: str, *, is_episodic: bool | None = None, **props) -> dict:
        """Update properties on an existing Point. Returns updated point dict.

        Implementation behind update(id, ...) — the consolidated Point/entity
        update (epic #888 W2).

        For :Object-labeled nodes, version is auto-incremented on every update.
        Status changes are validated against POINT_STATUS_VALUES.
        """
        proj = self._get_proj()
        _coerce_props(props)  # accept MCP-style nested props= dict (#218)
        # #329: id mutation + server-managed fields rejected
        props = _sanitize_props(props, reject_id=True)
        # R2 (#1541) D3: search_keys is stored flat (see _flatten_search_keys_prop).
        _flatten_search_keys_prop(props)
        # #1904 (bug-hunt 2026-08-28 P1-3): a content edit MUST recompute
        # content_hash in the same round trip — every dedup surface matches
        # on the stored hash (create_point dedup, ingest, _content_exists),
        # so a stale hash after update_point(content=...) silently breaks
        # dedup and diverges the live graph from JSONL replay (which derives
        # the hash from PointRevised.new_content).
        if "content" in props:
            props["content_hash"] = _content_hash(props["content"])
        if is_episodic is not None:
            props["is_episodic"] = is_episodic  # server-managed (explicit param only)

        # #49 Phase 2: context is REMOVED — raise TypeError if passed
        if "context" in props:
            raise TypeError(
                "update_point() got unexpected keyword argument 'context'. "
                "Context has been removed. See #49."
            )

        # Validate status if present
        if 'status' in props and (not isinstance(props['status'], str)
                                  or props['status'] not in POINT_STATUS_VALUES):
            raise ValueError(
                f"Invalid status {props['status']!r}. "
                f"Must be one of: {', '.join(sorted(POINT_STATUS_VALUES))}"
            )

        # #432 plan-review P1: update_point is non-status except the draft→live
        # promote (matches the create_operator promote). Any other status value
        # is rejected BEFORE the query — lifecycle transitions go through
        # retract_point()/supersede_point() (which emit events). This keeps
        # every claim transition observable via an emit hook.
        if 'status' in props:  # noqa: SIM102
            if props['status'] != 'live':
                raise ValueError(
                    "update_point only promotes draft→live — use "
                    "retract_point()/supersede_point() for lifecycle transitions"
                )

        # Check if node carries :Object label (entity node with version tracking)
        has_object = proj.g.query(
            "MATCH (n:Point:Object {id:$id}) RETURN count(n) > 0",
            params={"id": id},
        ).result_set[0][0]

        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()  # noqa: UP017

        if has_object:
            if 'status' in props:
                # Promote guard folded INTO the WHERE clause (plan-review P2:
                # single round trip, no widened write window).
                res = proj.g.query(
                    "MATCH (n:Point:Object {id:$id}) "
                    "WHERE (n.status IS NULL OR n.status = 'draft') "
                    "SET n.status = 'live', n.updatedAt = $now, "
                    "n.version = coalesce(n.version, 0) + 1 RETURN n",
                    params={"id": id, "now": now},
                )
                if not res.result_set:
                    _raise_update_point_status_error(proj, id)
            else:
                proj.g.query(
                    "MATCH (n:Point:Object {id:$id}) "
                    "SET n += $props, n.version = coalesce(n.version, 0) + 1, n.updatedAt = $now",
                    params={"id": id, "props": props, "now": now},
                )
        else:
            if 'status' in props:
                # Promote guard folded INTO the WHERE clause (plan-review P2).
                res = proj.g.query(
                    "MATCH (n:Point {id:$id}) "
                    "WHERE (n.status IS NULL OR n.status = 'draft') "
                    "SET n.status = 'live', n.updatedAt = $now RETURN n",
                    params={"id": id, "now": now},
                )
                if not res.result_set:
                    _raise_update_point_status_error(proj, id)
            else:
                for key, val in props.items():
                    proj.g.query(
                        "MATCH (n:Point {id:$id}) SET n += $props",
                        params={"id": id, "props": {key: val}},
                    )
        # Tag sync (#485): keep TAGGED edges consistent with the n.tags
        # property — update_point previously set the property but left edges
        # stale, so query_points_by_tag missed updated points. Falsy tag
        # values (None, "") normalize to [] like create_point, so the
        # "clear tags" idiom removes edges instead of leaving them stale.
        if "tags" in props:
            self._sync_tags(proj, id, props["tags"] or [])
        # Dreaming (#85): property mutations can affect confidence.
        self._mark_dirty([id])
        result = self.get_point(id)
        # #548: emit PointRevised event for rebuild parity
        # #1904: content_hash is derived from content — keep it out of the
        # event record (mirrors create_point's snapshot strip at emit).
        emit_props = {k: v for k, v in props.items() if k != "content_hash"}
        self._emit_event("PointRevised", id=id,
                         new_content=props.get("content"), **emit_props)
        return result

    def delete_point(self, id: str) -> bool:
        """Delete a Point and its relationships. Returns True if found.

        Implementation behind delete(id) — the consolidated Point/entity
        delete (epic #888 W2).
        """
        proj = self._get_proj()
        exists = proj.g.query(
            "MATCH (n:Point {id:$id}) RETURN count(n) > 0",
            params={"id": id},
        ).result_set[0][0]
        if not exists:
            return False
        # A tag's edge count can only change for this point's own TAGGED
        # edges — scope the orphan scan to that case (skips the global tag
        # scan on every untagged delete; #485).
        has_tag_edges = proj.g.query(
            "MATCH (n:Point {id:$id})-[:TAGGED]->() RETURN count(*) > 0",
            params={"id": id},
        ).result_set[0][0]
        # #1916: capture the deleted point's 1-hop reverse-BFS neighbors
        # BEFORE the DETACH DELETE — the delete removes the node's edges, so
        # the post-delete _mark_dirty reverse-BFS can no longer see them and
        # no neighbor would be dirtied (stored confidences stay stale at
        # pre-delete values). Shared helper keeps this capture in lockstep
        # with _mark_dirty's own traversal.
        op_ids, neighbor_claims = self._reverse_bfs_neighbors(proj, [id])
        neighbor_ids = (
            [oid for oid in op_ids if oid != id]
            + [cid for cid in neighbor_claims if cid != id]
        )
        proj.g.query("MATCH (n:Point {id:$id}) DETACH DELETE n", params={"id": id})
        # #548: emit PointRetracted event for rebuild parity (after delete,
        # so the graph mutation is committed before the event is written)
        self._emit_event("PointRetracted", id=id)
        # Tag GC (#485): delete orphaned :Tag nodes (no incoming TAGGED edges).
        # Idempotent — DETACH DELETE leaves count-0 tags behind that would
        # otherwise accumulate in list_tags.
        if has_tag_edges:
            proj.g.query("MATCH (t:Tag) WHERE NOT (t)<-[:TAGGED]-() DELETE t")
        # Dreaming (#85): deletion changes the graph structure around
        # neighbors — mark the pre-captured neighbors dirty (#1916) so the
        # reverse-BFS has surviving edges to traverse (the deleted point's
        # own edges are already gone) and a dream fires.
        self._mark_dirty([id, *neighbor_ids])
        # Epic 903-C4 (#1242): a deleted operator/claim changes the factor
        # graph — surviving neighbors' edges carry messages computed under
        # the OLD graph. Full drop (delete is rare; cheap). P2-review: the
        # equivalence gate caught a 0.31 drift WITHOUT this (stale seeds
        # reused across the deleted factor's boundary).
        self._get_ep().invalidate_messages()
        return True

    def delete_point_wrapped(self, id: str) -> dict:
        """Delete a Point. Returns dict for MCP tool consumption."""
        found = self.delete_point(id)
        return {"deleted": found, "id": id}

    # ── Invalidate / Supersede (#6999 GAP-12) ────────────────────

    def invalidate_point(self, id: str, corrected_by_id: str) -> dict:
        """Mark a Point outdated, linked to its replacement via CORRECTS edge.

        Validation contract (#330) — all checks run BEFORE any write so a
        failure can never leave a partial graph state:
        - id == corrected_by_id → ValueError (a self-CORRECTS edge poisons
          traversal/credibility chains).
        - old point missing (never existed or already deleted) →
          {"invalidated": False} with no writes (retry-friendly).
        - corrected_by point missing → ValueError (structural failure: would
          orphan an outdated point with no replacement).
        Re-invalidating a point that still EXISTS re-asserts (returns True,
        MERGE keeps a single CORRECTS edge).
        """
        from datetime import datetime, timezone
        proj = self._get_proj()
        if id == corrected_by_id:
            raise ValueError(
                f"invalidate_point: corrected_by cannot be the point itself ({id!r})"
            )
        old_exists = proj.g.query(
            "MATCH (n:Point {id:$id}) RETURN count(n) > 0", params={"id": id},
        ).result_set[0][0]
        if not old_exists:
            return {"invalidated": False, "id": id, "corrected_by": corrected_by_id}
        new_exists = proj.g.query(
            "MATCH (n:Point {id:$id}) RETURN count(n) > 0",
            params={"id": corrected_by_id},
        ).result_set[0][0]
        if not new_exists:
            raise ValueError(
                f"invalidate_point: corrected_by point {corrected_by_id!r} does not "
                f"exist — refusing to orphan outdated point {id!r}"
            )
        now = datetime.now(timezone.utc).isoformat()  # noqa: UP017

        proj.g.query(
            "MATCH (n:Point {id:$id}) SET n.outdated = true, "
            "n.updatedAt = $now, n.validTo = $now, n.expiredAt = $now",
            params={"id": id, "now": now},
        )
        proj.g.query(
            "MATCH (a:Point {id:$new_id}), (b:Point {id:$old_id}) "
            "MERGE (a)-[:CORRECTS]->(b)",
            params={"new_id": corrected_by_id, "old_id": id},
        )
        # Dreaming (#85): invalidation changes the propagation graph.
        self._mark_dirty([id, corrected_by_id])
        # Epic 903-C9 (#1247): edge TRANSFER — invalidate warm-start seeds on
        # both endpoints' edges (a transfer is not a new/deleted edge; the
        # C4 topology hooks don't fire here).
        self._get_ep().invalidate_messages([id, corrected_by_id])
        return {"invalidated": True, "id": id, "corrected_by": corrected_by_id}

    # ── Supersede / Invalidate consolidation (epic #888 W2) ───────────
    # supersede() is the unified node-lifecycle entry; transfer_edges picks the
    # full transfer (supersede_point) or the invalidate behavior
    # (invalidate_point). Legacy methods remain the implementations.

    def supersede(self, old_id: str, new_id: str,
                  transfer_edges: bool = True) -> dict:
        """Unified supersede / invalidate (epic #888 W2, PR #912).

        transfer_edges=True  → full supersede (supersede_point): CORRECTS edge
        + outdated flag + ALL edges transferred from old to new.
        transfer_edges=False → invalidate behavior (invalidate_point): mark
        old outdated + CORRECTS edge only, NO edge transfer. This absorbs the
        legacy invalidate surface.

        Returns {invalidated, id, corrected_by} (+ edges_transferred when
        transfer_edges=True). Raises ValueError on missing/self/terminal input
        (the underlying point-level guards, unchanged).
        """
        if transfer_edges:
            return self.supersede_point(old_id, new_id)
        return self.invalidate_point(old_id, new_id)

    def supersede_point(self, old_id: str, new_id: str,
                        *, valid_from: str | None = None) -> dict:
        """Atomically replace old Point with new — CORRECTS edge + outdated flag + edge transfer.

        E6 (#1538) D2 — bi-temporal validity windows: the supersession ALSO
        stamps the old point's window END (``validTo`` = the successor's
        ``validFrom`` — contiguous Graphiti windows: the old fact stops
        being true exactly when the new fact became true) and its
        transaction-time expiry (``expiredAt`` = now). The successor's
        window START is its own ``validFrom`` (the create path, D3), or the
        optional ``valid_from`` kwarg when the caller knows it (else read
        the successor's ``validFrom``, fall back to its ``createdAt``, fall
        back to now — monotone, never a gap). Additive-only: no behavior
        change for callers that don't pass the kwarg.

        Transfers all edges from the old point to the new point:
          - Operator edges (IMPL, NAND, hasPart) with idx
          - Plain structural edges (aboutSubject, aboutObject, aboutAction,
            aboutEvent, aboutPoint, aboutDocument, extractedFrom, etc.)
        Preserves edge type and idx (source vs target position).
        Leaves the old point outdated with only the CORRECTS edge from the new point.
        """
        from datetime import datetime, timezone
        proj = self._get_proj()
        now = datetime.now(timezone.utc).isoformat()  # noqa: UP017

        # #432: transition guard — old point must exist, be a statement (not
        # an operator), and not already be terminal (mirrors the retract
        # guard; supersede is already multi-query so the read is cheap).
        guard = proj.g.query(
            "MATCH (n:Point {id:$id}) RETURN n.is_operator, n.status",
            params={"id": old_id},
        ).result_set
        if not guard:
            raise ValueError(f"No point {old_id!r}")
        is_op, cur = guard[0][0], guard[0][1]
        if is_op:
            raise ValueError(
                f"Point {old_id!r} is an operator — supersession is for statement points")
        if cur in ("retracted", "superseded", "archived"):
            raise ValueError(
                f"Point {old_id!r} is already terminal ({cur!r}) — supersession is terminal")

        # P1 (Qwen review): validate the NEW point too — it must exist, be a
        # statement, not be terminal, and differ from the old point. A missing /
        # self / terminal successor would terminalize the old point with no valid
        # replacement (phantom PointSuperseded).
        if old_id == new_id:
            raise ValueError("supersede_point: old_id and new_id must differ")
        new_guard = proj.g.query(
            "MATCH (n:Point {id:$id}) RETURN n.is_operator, n.status",
            params={"id": new_id},
        ).result_set
        if not new_guard:
            raise ValueError(f"No point {new_id!r}")
        n_is_op, n_cur = new_guard[0][0], new_guard[0][1]
        if n_is_op:
            raise ValueError(
                f"Point {new_id!r} is an operator — supersession target must be a statement")
        if n_cur in ("retracted", "superseded", "archived"):
            raise ValueError(
                f"Point {new_id!r} is already terminal ({n_cur!r}) — cannot supersede into it")

        # 0. #329: collect + validate ALL edge types BEFORE any mutation.
        #    The edge types are interpolated into query structure (no params
        #    possible) — an unvalidated type (e.g. from a crafted edge) is a
        #    Cypher injection primitive AND would cause a partial transfer.
        #    Validation strictly precedes the outdated-flag/CORRECTS writes so
        #    a failure leaves the graph untouched.
        edges_result = proj.g.query(
            "MATCH (op:Point {is_operator:true})-[r]->(old:Point {id:$old_id}) "
            "RETURN op.id, type(r), r.idx, op.label",
            params={"old_id": old_id},
        )
        from .security import validate_rel_type
        for row in edges_result.result_set:
            validate_rel_type(row[1])  # raises ValueError before any mutation

        # #432 Task 3: durable PointSuperseded event (append-before-mutation,
        # AFTER the guard + edge-type validation — P2 review fix: emitting
        # before validation produced phantoms on corrupt-edge data).
        # E6 (#1538) D2: resolve the successor's validFrom (window contiguity
        # source) BEFORE the emit so the event payload carries the same
        # values the stamp block writes (read-only — no ordering impact).
        if valid_from is not None:
            succ_vf = str(valid_from)
        else:
            vf_rows = proj.g.query(
                "MATCH (n:Point {id:$id}) RETURN n.validFrom, n.createdAt",
                params={"id": new_id},
            ).result_set
            if vf_rows and vf_rows[0][0]:
                succ_vf = vf_rows[0][0]
            elif vf_rows and vf_rows[0][1]:
                succ_vf = vf_rows[0][1]
            else:
                succ_vf = now  # monotone fallback — never a gap
        self._emit_event(
            "PointSuperseded",
            {"id": old_id, "new_id": new_id,
             "valid_from": succ_vf, "valid_to": succ_vf, "expired_at": now},
            id=old_id,
        )

        # CYCLE-26 REVIEW-FIX P1 (cycle-7 pin): the superseded-status write +
        # CORRECTS edge MOVE AFTER the transfers (2a / 2a-DIRECT / 2b). The
        # transfers read old's edges directly and don't depend on the status;
        # under the pinned order a crash mid-transfer leaves old NOT yet
        # terminal, and a RE-RUN converges (the terminal guard never fires
        # on the retry). The old order (status first) left a terminal point
        # with EP-active incident direct edges and no remediation short of
        # rebuild_all.

        # 2a. Transfer operator edges (IMPL, NAND, hasPart) — preserve provenance

        transferred = 0
        for row in edges_result.result_set:
            op_id, edge_type, idx = row[0], row[1], row[2]
            op_label = row[3] if len(row) > 3 else None
            if op_label == "alreadyDecided":
                # #1080 review: dedup context edges must NOT be re-pointed at
                # the replacement — an alreadyDecided IMPL on the superseded
                # prior declares the OLD decision a duplicate, not the new one.
                _logger.info(
                    "supersede_point: keeping alreadyDecided operator %s "
                    "attached to the superseded point %s (dedup context edge)",
                    op_id, old_id,
                )
                continue
            # Create new edge: operator → new point (same idx preserves source/target position)
            proj.g.query(
                f"MATCH (op:Point {{id:$op_id}}), (new:Point {{id:$new_id}}) "
                f"CREATE (op)-[:{edge_type} {{idx:$idx}}]->(new)",
                params={"op_id": op_id, "new_id": new_id, "idx": idx},
            )
            # Delete old edge (match by idx for precision)
            proj.g.query(
                f"MATCH (op:Point {{id:$op_id}})-[r:{edge_type} {{idx:$idx}}]->(old:Point {{id:$old_id}}) "
                f"DELETE r",
                params={"op_id": op_id, "idx": idx, "old_id": old_id},
            )
            transferred += 1

        # 2a-DIRECT (epic #902 §8 / plan §5.3 review fix): transfer
        # OPERATOR-LESS direct IMPL/NAND edges incident to the superseded
        # point, BOTH directions:
        #   (old)-[:IMPL|NAND]->(x)  and  (x)-[:IMPL|NAND]->(old)
        # Repointed to new_id preserving type/direction/confidence/weight/
        # label/batch_id (E2E-11.6: zero direct edges remain incident to a
        # superseded point, live AND post-rebuild). The REPOINT descriptor is
        # emitted BEFORE the transfer (EMIT-BEFORE-TRANSFER, §4.4) so a
        # transfer×emission crash cannot leave the stale descriptor in the
        # JSONL while the live edge moves.
        for direction in ("out", "in"):
            if direction == "out":
                dq = ("MATCH (old:Point {id:$old_id})-[r:IMPL|NAND]->(t) "
                      "RETURN type(r), r, t.id, ID(r)")
            else:
                dq = ("MATCH (t)-[r:IMPL|NAND]->(old:Point {id:$old_id}) "
                      "RETURN type(r), r, t.id, ID(r)")
            rows = proj.g.query(dq, params={"old_id": old_id}).result_set
            # Filter: exclude edges whose OTHER endpoint is an operator Point
            # (REVIEW-FIX P2 — a mitigation-style (m)-[:IMPL]->(op) edge has
            # an operator target; 2a owns op->input edges and runs first;
            # repointing an operator target would create a topology
            # create_direct_edge itself rejects). Operators carry
            # is_operator=true.
            plain = []
            if rows:
                other_ids = [row[2] for row in rows]
                op_rows = proj.g.query(
                    "MATCH (p:Point) WHERE p.id IN $ids AND "
                    "coalesce(p.is_operator, false) = true RETURN p.id",
                    params={"ids": other_ids},
                ).result_set
                op_set = {r[0] for r in op_rows}
                plain = [row for row in rows if row[2] not in op_set]
            rows = plain
            for row in rows:
                rtype, r, tid, rid = row[0], row[1], row[2], row[3]
                # FalkorDB returns an Edge object — read its properties.
                rdict = dict(r.properties) if hasattr(r, "properties") else {}
                if tid == new_id:
                    # REVIEW-FIX P1 (cycle-26): the edge already terminates at
                    # the successor — repointing would CREATE A PHANTOM
                    # SELF-EDGE (new)->(new) (the MERGE runs before the old
                    # delete, and no existing (new)->(new) edge collapses it).
                    # The old edge is DELETED and NO repoint descriptor is
                    # emitted (pass-2b must not recreate the self-edge
                    # post-rebuild). E2E-11.6: zero direct edges incident to
                    # a superseded point, live AND post-rebuild.
                    proj.g.query(
                        f"MATCH (a)-[r:{rtype}]->(b) WHERE ID(r) = $rid DELETE r",
                        params={"rid": rid},
                    )
                    transferred += 1
                    continue
                _attrs = {k: v for k, v in rdict.items()
                          if k in ("direction", "confidence", "weight",
                                   "label", "batch_id")}
                # EMIT-BEFORE-TRANSFER: the REPOINT descriptor (plan §4.4 —
                # A10's pass-2b repoint apply consumes it post-rebuild).
                # Flat descriptor shape (REVIEW-FIX P2: match
                # DirectEdgeCreated's §4.4 canonical flat shape — A10's
                # pass-2b reads the same fields from both record types).
                self._emit_event(
                    "DirectEdgeRepoint",
                    id=f"{old_id}->{new_id}:{rtype}",
                    src=(tid if direction == "in" else new_id),
                    tgt=(new_id if direction == "in" else tid),
                    edge_type=rtype,
                    **{k: v for k, v in _attrs.items()},
                )
                # MERGE-collapse (count()==1 twin guard): the MERGE matches
                # the bare pattern; if the target edge already exists it
                # converges instead of duplicating. rtype is validated
                # IMPL/NAND (from the query) — interpolated into the MERGE
                # pattern (FalkorDB requires ONE relationship type per
                # MERGE pattern).
                # MERGE (bare) then SET separately — MERGE+SET in ONE
                # statement matches the full pattern (attrs included) and
                # creates a PARALLEL edge on attribute change; the two-
                # statement form collapses to one edge and last-writer-wins
                # on attrs (the plan's no-parallel-direct-edges contract).
                # Two-step MERGE (FalkorDB quirk — same as create_direct_edge):
                # MATCH the existing nodes, then MERGE the edge between them
                # (never creates duplicate nodes; collapses to one edge).
                if direction == "out":
                    mq = (f"MATCH (new:Point {{id:$new_id}}), "
                          f"(t:Point {{id:$tid}}) "
                          f"MERGE (new)-[nr:{rtype}]->(t)")
                else:
                    mq = (f"MATCH (t:Point {{id:$tid}}), "
                          f"(new:Point {{id:$new_id}}) "
                          f"MERGE (t)-[nr:{rtype}]->(new)")
                proj.g.query(mq, params={"new_id": new_id, "tid": tid})
                _keep = {k: v for k, v in rdict.items()
                         if k in ("direction", "confidence", "weight",
                                  "label", "batch_id")}
                proj.g.query(
                    f"MATCH (a:Point {{id:$a}})-[r:{rtype}]->"
                    f"(b:Point {{id:$b}}) SET r += $attrs",
                    params={"a": (tid if direction == "in" else new_id),
                            "b": (new_id if direction == "in" else tid),
                            "attrs": _keep},
                )
                # Delete the OLD edge by its internal id (precision),
                # constrained to the old point's incident edges (REVIEW-FIX
                # P2: never graph-wide — internal ids may be reused).
                if direction == "out":
                    proj.g.query(
                        f"MATCH (old:Point {{id:$old_id}})-[r:{rtype}]->() "
                        f"WHERE ID(r) = $rid DELETE r",
                        params={"rid": rid, "old_id": old_id},
                    )
                else:
                    proj.g.query(
                        f"MATCH ()-[r:{rtype}]->(old:Point {{id:$old_id}}) "
                        f"WHERE ID(r) = $rid DELETE r",
                        params={"rid": rid, "old_id": old_id},
                    )
                transferred += 1

        # 2b. Transfer plain structural edges (#122) — about*, extractedFrom, wasDerivedFrom, etc.
        # These edges connect the Point to entities (Subject, Object, Source, etc.)
        structural_rels = [
            'aboutSubject', 'aboutObject', 'aboutAction', 'aboutEvent',
            'aboutPoint', 'aboutDocument', 'extractedFrom', 'wasDerivedFrom'
        ]
        for rel in structural_rels:
            struct_rows = proj.g.query(
                f"MATCH (old:Point {{id:$old_id}})-[r:{rel}]->(target) "
                f"RETURN id(target), target.id, labels(target)",
                params={"old_id": old_id},
            ).result_set
            for row in struct_rows:
                target_graph_id = row[0]  # FalkorDB internal node id — exact match
                # Create new edge: new point → same target (MERGE = idempotent, no dupes)
                proj.g.query(
                    f"MATCH (new:Point {{id:$new_id}}), (t) WHERE id(t) = $tid "
                    f"MERGE (new)-[:{rel}]->(t)",
                    params={"new_id": new_id, "tid": target_graph_id},
                )
                # Delete old edge (match by exact internal node id)
                proj.g.query(
                    f"MATCH (old:Point {{id:$old_id}})-[r:{rel}]->(t) WHERE id(t) = $tid "
                    f"DELETE r",
                    params={"old_id": old_id, "tid": target_graph_id},
                )
                transferred += 1

        # 1. Mark old superseded + outdated + create CORRECTS edge — AFTER the
        # transfers (CYCLE-26 REVIEW-FIX P1 / plan §5.3 cycle-7 pin: the crash
        # window between the status write and the transfers is closed by
        # re-run convergence; this block is the last mutation before dreaming).
        # #432: status='superseded' alongside the legacy outdated=true flag
        # (back-compat for consumers reading the flag; #690 will consolidate).
        proj.g.query(
            "MATCH (n:Point {id:$id}) SET n.status = 'superseded', "
            "n.outdated = true, n.updatedAt = $now, "
            "n.validTo = $valid_to, n.expiredAt = $expired_at",
            params={"id": old_id, "now": now,
                    "valid_to": succ_vf, "expired_at": now},
        )
        proj.g.query(
            "MATCH (a:Point {id:$new_id}), (b:Point {id:$old_id}) "
            "MERGE (a)-[:CORRECTS]->(b)",
            params={"new_id": new_id, "old_id": old_id},
        )

        # Dreaming (#85): supersede changes the propagation graph around both.
        self._mark_dirty([old_id, new_id])
        # Epic 903-C9 (#1247): edge TRANSFER — invalidate warm-start seeds on
        # both endpoints' edges (supersede transfers edges with their msg_*
        # properties; the seeds are computed under the old factor context).
        self._get_ep().invalidate_messages([old_id, new_id])

        return {
            "invalidated": True,
            "id": old_id,
            "corrected_by": new_id,
            "edges_transferred": transferred,
        }

    def retract_point(self, id: str) -> dict:
        """Tombstone-retract a Point: status='retracted' (point stays in graph).

        #432: retraction is a TERMINAL state transition, not a deletion — the
        projection keeps the point with status='retracted' and default query
        surfaces exclude it (opt-in via include_retracted). Single atomic
        conditional query on the happy path; diagnostic read only on the error
        path.

        Raises ValueError if the point is missing, is an operator node, or is
        already terminal (retracted/superseded/archived).
        """
        from datetime import datetime, timezone
        proj = self._get_proj()
        # P1 (code-review): validate FIRST, then emit, then mutate — the emit
        # before the guard produced phantom PointRetracted events on the
        # NORMAL invalid-input path (missing / operator / terminal), which
        # poll consumers would see as retractions that never happened.
        row = proj.g.query(
            "MATCH (n:Point {id:$id}) RETURN n.is_operator, n.status",
            params={"id": id}).result_set
        if not row:
            raise ValueError(f"No point {id!r}")
        is_op, cur = row[0][0], row[0][1]
        if is_op:
            raise ValueError(
                f"Point {id!r} is an operator — retraction is for statement points")
        if cur in ("retracted", "superseded", "archived"):
            raise ValueError(
                f"Point {id!r} is already terminal ({cur!r}) — retraction is terminal")
        # #432 Task 3: durable PointRetracted event (append-before-mutation;
        # only after the input contract validates).
        self._emit_event("PointRetracted", {"id": id}, id=id)
        # P1 (Qwen review): CAS the SET — the WHERE re-checks terminal state so
        # a concurrent retract/supersede can't both pass validation and have a
        # terminal overwrite (retracted overwriting superseded, or vice versa).
        r = proj.g.query(
            "MATCH (n:Point {id:$id}) "
            "WHERE (n.status IS NULL OR NOT (n.status IN $terminal)) "
            "SET n.status = 'retracted', n.updatedAt = $now RETURN properties(n)",
            params={"id": id, "now": datetime.now(timezone.utc).isoformat(),  # noqa: UP017
                    "terminal": ["retracted", "superseded", "archived"]})
        if not r.result_set:
            raise ValueError(
                f"Point {id!r} is already terminal — retraction is terminal")
        return r.result_set[0][0]  # updated node props (no trailing get_point round trip)

    # ── Promotion (Phase-4 EP-safe lifecycle, #785) ────────────────

    def promote_point(self, point_id: str) -> dict:
        """Reviewer-gated draft→live promotion (plan §6.1, J-5, DE2E-8).

        The ONLY path a draft extraction Point may go live — never via the
        SDK #131 edge auto-promotion for extraction paths
        (`create_operator(promote_source=False)`, #780).

        Semantics (all responses include id + status + promoted + blocked):
          - already live → NO-OP {promoted: False, blocked: False,
            reason: "already_live"} — DE2E-N9
          - operator node → blocked {blocked: True, reason: "is_operator"} —
            operators only go live via the R16 endpoint gate (matching
            retract_point's operator rejection)
          - terminal (retracted/superseded/outdated/archived) → blocked
            {blocked: True, reason: "not_draft"}
          - Point belongs to a QUARANTINED batch → blocked
            {blocked: True, reason: "batch_quarantined", batch_id} — quarantine
            is batch-level (plan §3): the batch's Points stay draft until the
            W-3 re-run passes (EpSafeCommit recovery loop)
          - otherwise → status draft→live, `reviewed: true` derived flag set,
            PointPromoted event emitted, and R16 zombie prevention:
            incident DRAFT operator nodes (status 'draft' — the post-#780
            extraction shape) are promoted to live ONCE ALL their endpoint
            Points are live, so a contradiction never stays a dead draft
            operator after its claims go live.

        NOTE (review #944/#990): the quarantine lock is a read-then-CAS —
        FalkorDBLite has no EXISTS subqueries, so the batch check cannot fold
        into the CAS. #990 added a post-CAS re-check that SURFACES a lost
        race instead of hiding it: when a quarantine lands between the read
        and the CAS, the promotion completes but the response carries
        ``race_detected: true`` + ``race_warning`` (the point is live while
        its batch is quarantined — an operator action is required).

        Raises ValueError if the Point does not exist.
        """
        proj = self._get_proj()
        row = proj.g.query(
            "MATCH (n:Point {id:$id}) RETURN properties(n)",
            params={"id": point_id},
        ).result_set
        if not row:
            raise ValueError(f"No point {point_id!r}")
        point = row[0][0]
        status = point.get("status")

        # Operators go live ONLY via the R16 endpoint gate — promoting a
        # draft operator directly would create the live-operator-with-draft-
        # endpoints state R16 exists to prevent (review #944).
        if point.get("is_operator") or point.get("op_type"):
            return {"id": point_id, "status": status, "promoted": False,
                    "blocked": True, "reason": "is_operator"}
        # DE2E-N9: already-live promote → no-op, no error.
        if status == "live":
            return {"id": point_id, "status": "live", "promoted": False,
                    "blocked": False, "reason": "already_live"}
        # Terminal states cannot be promoted back to live.
        if status != "draft":
            return {"id": point_id, "status": status, "promoted": False,
                    "blocked": True, "reason": "not_draft"}

        # Quarantine lock (batch-level): a quarantined batch's Points stay
        # draft until re-review (W-3 recovery).
        batch_id = point.get("batch_id")
        if batch_id:
            from .mining import batch_status  # lazy: no module-level sdk↔mining cycle
            bs = batch_status(proj, batch_id)
            if bs is not None and bs["status"] == "quarantined":
                return {"id": point_id, "status": status, "promoted": False,
                        "blocked": True, "reason": "batch_quarantined",
                        "batch_id": batch_id}

        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()  # noqa: UP017
        # CAS-guarded SET (concurrent promote can't double-fire); reviewed is
        # the DERIVED reviewer flag (plan §3 — no new stored status).
        r = proj.g.query(
            "MATCH (n:Point {id:$id}) WHERE n.status = 'draft' "
            "SET n.status = 'live', n.reviewed = true, n.promotedAt = $now, "
            "n.updatedAt = $now RETURN n.id",
            params={"id": point_id, "now": now},
        )
        if not r.result_set:
            # Raced with a concurrent promote — re-read and report the outcome.
            re = self.get_point(point_id)
            if re.get("status") == "live":
                return {"id": point_id, "status": "live", "promoted": False,
                        "blocked": False, "reason": "already_live"}
            return {"id": point_id, "status": re.get("status"),
                    "promoted": False, "blocked": True,
                    "reason": "not_draft"}

        # Epic 903-C2 (#1240): a status→live transition invalidates the
        # promoted claim's EP-derived confidence — mark it dirty (plus its
        # operator neighborhood via the 1-hop reverse BFS) so the next dream
        # re-derives it. Verified gap: promote_point did NOT call _mark_dirty
        # (the W1 lifecycle contract says every status→live transition must),
        # which left a promoted claim permanently excluded from dreaming.
        self._mark_dirty([point_id])

        # Post-CAS race re-check (#990): the quarantine lock is a
        # read-then-CAS (two statements — FalkorDBLite has no EXISTS
        # subqueries), so a quarantine landing between the batch_status read
        # and the CAS can race a promotion through. Surface the race instead
        # of hiding it: the point is live, but the batch is quarantined.
        race_detected = False
        if batch_id:
            from .mining import batch_status
            bs = batch_status(proj, batch_id)
            if bs is not None and bs["status"] == "quarantined":
                race_detected = True
                _logger.warning(
                    "promote_point: batch %s quarantined concurrently with "
                    "promotion of %s (TOCTOU race, #990) — point is live but "
                    "batch is quarantined",
                    batch_id, point_id,
                )

        # Variant C (#784): a MERGED content-dedup candidate whose prior was
        # LIVE at approve time is wired NOW — the candidate is live, so the
        # "already decided" IMPL becomes a live→live link (exactly one).
        # The exists-guard is OPERATOR-MEDIATED (create_operator writes
        # (op)-[:IMPL]->(endpoint) edges — direct-edge counts are always 0,
        # #784 review) and the prior is validated BEFORE the CAS so a stale
        # dedup_target_id cannot produce a live point with no event.
        dedup_wired = False
        dedup_target_valid = True
        prior = point.get("dedup_target_id")
        if point.get("dedup_reviewed") == "merge" and prior:
            prow = proj.g.query(
                "MATCH (n:Point {id:$id}) RETURN n.id",
                params={"id": prior},
            ).result_set
            if not prow:
                dedup_target_valid = False
                _logger.warning(
                    "promote_point: dedup target %s for %s no longer exists — "
                    "skipping the alreadyDecided wire", prior, point_id)
        if point.get("dedup_reviewed") == "merge" and prior and dedup_target_valid:
            exists = proj.g.query(
                "MATCH (op:Point {label:'alreadyDecided'})-[:IMPL]->"
                "(:Point {id:$cand}), "
                "(op)-[:IMPL]->(:Point {id:$prior}) RETURN count(op)",
                params={"cand": point_id, "prior": prior},
            ).result_set
            if not exists or exists[0][0] == 0:
                self.create_operator(
                    "IMPL", point_id, [prior], label="alreadyDecided",
                    direction="unidirectional")
                dedup_wired = True

        # Temporal wiring at promotion (W-4, #786): a MERGED temporal
        # candidate whose prior was LIVE at extraction gets its NAND now
        # (live→live), or — for an explicit replacement — the prior is
        # superseded (CORRECTS + outdated:true). Targets are validated
        # BEFORE the CAS (mirroring the dedup block) so a stale/terminal
        # target can never produce a live point with no event (#1080 review).
        temporal_wired = False
        superseded = False
        temporal_targets = point.get("temporal_target_ids") or []
        if point.get("temporal_target_id") and point.get("temporal_target_id") not in temporal_targets:
            temporal_targets.append(point["temporal_target_id"])
        live_targets: list[str] = []
        if (point.get("temporal_candidate")
                and point.get("temporal_reviewed") == "merge"):
            for tgt in temporal_targets:
                trow = proj.g.query(
                    "MATCH (n:Point {id:$id}) RETURN n.status",
                    params={"id": tgt},
                ).result_set
                if not trow:
                    # MISSING node (stale target) — distinct from an unset
                    # status node, which is live by the canonical read model
                    # (#1080 round-2 review: missing targets crashed the
                    # NAND create AFTER the CAS).
                    _logger.warning(
                        "promote_point: temporal target %s for %s no longer "
                        "exists — skipping the wire", tgt, point_id)
                    continue
                tstatus = trow[0][0] or "live"
                if tstatus == "live":
                    live_targets.append(tgt)
                else:
                    _logger.warning(
                        "promote_point: temporal target %s for %s is %s — "
                        "skipping the wire", tgt, point_id, tstatus)
        if live_targets and point.get("temporal_replacement"):
            for tgt in live_targets:
                try:
                    self.supersede_point(tgt, point_id)
                    superseded = True
                except ValueError as exc:
                    _logger.warning(
                        "promote_point: supersede of %s failed for %s: %s",
                        tgt, point_id, exc)
        elif live_targets:
            for tgt in live_targets:
                exists = proj.g.query(
                    "MATCH (op:Point {is_operator:true})-[:NAND]->"
                    "(:Point {id:$cand}), "
                    "(op)-[:NAND]->(:Point {id:$prior}) RETURN count(op)",
                    params={"cand": point_id, "prior": tgt},
                ).result_set
                if exists and exists[0][0] > 0:
                    continue
                # Cross-guard: never NAND a pair already linked by an
                # alreadyDecided IMPL (dedup+temporal conflict, #1080).
                dup = proj.g.query(
                    "MATCH (op:Point {label:'alreadyDecided'})-[:IMPL]->"
                    "(:Point {id:$cand}), "
                    "(op)-[:IMPL]->(:Point {id:$prior}) RETURN count(op)",
                    params={"cand": point_id, "prior": tgt},
                ).result_set
                if dup and dup[0][0] > 0:
                    _logger.warning(
                        "promote_point: %s already linked to %s by an "
                        "alreadyDecided IMPL — skipping temporal NAND",
                        point_id, tgt)
                    continue
                try:
                    self.create_operator("NAND", point_id, [tgt])
                    temporal_wired = True
                except ValueError as exc:
                    # A racing delete between validation and create must
                    # never leave a live point without its event (#1080).
                    _logger.warning(
                        "promote_point: NAND wiring to %s failed for %s: %s",
                        tgt, point_id, exc)

        # R16 zombie-operator prevention: promote incident draft operators
        # once ALL their endpoint Points are live.
        promoted_ops = self._promote_incident_operators(proj, point_id, now)

        self._emit_event("PointPromoted", point=self.get_point(point_id))
        result = {"id": point_id, "status": "live", "promoted": True,
                  "reviewed": True,
                  "operator_nodes_promoted": promoted_ops}
        if dedup_wired:
            result["dedup_wired"] = True
        if temporal_wired:
            result["temporal_wired"] = True
        if superseded:
            result["superseded"] = True
        if race_detected:
            result["race_detected"] = True
            result["race_warning"] = (
                "batch quarantined concurrently with promotion — point is live")
        return result

    def _promote_incident_operators(self, proj, point_id: str, now: str) -> list[str]:
        """R16: promote draft operator nodes incident to a freshly-live Point.

        An operator is promoted only when it carries EXPLICIT status 'draft'
        (the post-#780 `create_operator(promote_source=False)` shape) AND
        every endpoint Point is live — otherwise the draft endpoints would
        inherit a live edge (the exact pollution the draft lifecycle
        prevents). Unset-status operators are LIVE under the canonical read
        model (projection coalesce default; #944 review) and are skipped —
        never re-promoted into the event stream.
        """
        rows = proj.g.query(
            "MATCH (o:Point {is_operator:true})-[r]->(n:Point {id:$id}) "
            "WHERE o.status = 'draft' RETURN DISTINCT o.id",
            params={"id": point_id},
        ).result_set
        promoted = []
        for (oid,) in rows:
            eps = proj.g.query(
                "MATCH (o:Point {id:$oid})-[r]->(s:Point) "
                "RETURN s.id, s.status",
                params={"oid": oid},
            ).result_set
            if not eps:
                continue  # no endpoints — nothing to gate on
            all_live = all((st or "live") == "live" for _, st in eps)
            if not all_live:
                continue  # keep draft until every endpoint is live
            # CAS-guarded SET: only an explicitly-draft operator may flip,
            # and only once (concurrent endpoint promotions can't double-emit).
            r = proj.g.query(
                "MATCH (o:Point {id:$oid}) WHERE o.status = 'draft' "
                "SET o.status = 'live', o.promotedAt = $now, o.updatedAt = $now "
                "RETURN o.id",
                params={"oid": oid, "now": now},
            )
            if not r.result_set:
                continue  # lost a race — another promote already flipped it
            # Full snapshot for JSONL rebuild parity (#548, review #944).
            self._emit_event("OperatorPromoted", id=oid,
                             point=self.get_point(oid))
            promoted.append(oid)
        return promoted

    def quarantine_batch(self, batch_id: str, *, reason: str) -> dict:
        """Quarantine a batch (W-3 fail path) — blocks promote_point on its
        Points until a re-run passes (plan §6.1 pinned SDK signature).

        Thin delegate to tortoise.mining.quarantine_batch — batch lifecycle
        state lives on :Batch marker nodes (operational metadata).
        """
        from .mining import quarantine_batch as _qb
        return _qb(self._get_proj(), batch_id, reason=reason)

    # ── Temporal belief timeline (W-4, #786, DE2E-6) ───────────────

    def belief_timeline(self, topic: str, limit: int = 50) -> list[dict]:
        """Dated, ordered belief chain for a topic (plan §6.1, J-4, DE2E-6).

        Returns decision Points aboutObject-connected to the topic entity,
        ordered by validFrom ascending, each shaped as
        {content, pointKind, validFrom, status, linked_by, related} where
        linked_by is the temporal edge (NAND via an operator, or CORRECTS via
        supersede_point) to the NEXT point in the chain, and related holds
        the linked point ids.
        """
        if limit < 1:
            raise ValueError(f"limit must be >= 1, got {limit}")
        proj = self._get_proj()
        rows = proj.g.query(
            "MATCH (p:Point {pointKind:'decision'})-[:aboutObject]->"
            "(o:Object) "
            "WHERE (p.is_operator IS NULL OR p.is_operator = false) "
            "AND (o.name = $topic OR o.canonical_name = $topic) "
            "RETURN p.id, p.content, p.validFrom, p.status, p.outdated "
            "ORDER BY p.validFrom LIMIT $limit",
            params={"topic": topic, "limit": limit},
        ).result_set
        entries = [list(r) for r in rows]
        # Superseded priors dropped out of the topic query (supersede_point
        # transferred their aboutObject edges) — re-attach them via the
        # CORRECTS chain so the timeline keeps the outdated belief (#1080).
        if entries:
            topic_ids = [r[0] for r in entries]
            old_rows = proj.g.query(
                "MATCH (cur:Point)-[:CORRECTS]->(old:Point {pointKind:'decision'}) "
                "WHERE cur.id IN $ids "
                "RETURN old.id, old.content, old.validFrom, old.status, "
                "       old.outdated ORDER BY old.validFrom",
                params={"ids": topic_ids},
            ).result_set
            known = {r[0] for r in entries}
            for r in old_rows:
                if r[0] not in known:
                    entries.append(list(r))
            # Globally ordered by validFrom (superseded priors appended).
            entries.sort(key=lambda e: (e[2] is None, e[2] or ""))
        out = []
        ids = [e[0] for e in entries]
        for pid, content, vf, status, outdated in entries[-limit:]:
            # Temporal link to the NEXT point in the chain.
            linked_by = None
            related = []
            for other_id in ids:
                if other_id == pid:
                    continue
                nand = proj.g.query(
                    "MATCH (op:Point {is_operator:true})-[:NAND]->"
                    "(:Point {id:$a}), "
                    "(op)-[:NAND]->(:Point {id:$b}) RETURN count(op)",
                    params={"a": pid, "b": other_id},
                ).result_set
                if nand and nand[0][0] > 0:
                    linked_by = "NAND"
                    related.append(other_id)
                    break
                corr = proj.g.query(
                    "MATCH (:Point {id:$a})-[:CORRECTS]->(:Point {id:$b}) "
                    "RETURN count(*)",
                    params={"a": pid, "b": other_id},
                ).result_set
                if corr and corr[0][0] > 0:
                    linked_by = "CORRECTS"
                    related.append(other_id)
                    break
            entry = {
                "content": content,
                "pointKind": "decision",
                "validFrom": vf,
                "status": status,
                "linked_by": linked_by,
                "related": related,
            }
            if outdated:
                entry["outdated"] = True
            out.append(entry)
        return out

    # ── Content dedup queue (W-2, #784) ─────────────────────────────
    # Content candidates are DRAFT decision Points flagged by the two-tier
    # dedup (hash + embedding) against existing decision Points. The
    # candidate state lives in Point properties (dedup_candidate /
    # dedup_method / dedup_similarity / dedup_target_id / dedup_reviewed) —
    # review-queue operational state, JSONL-rebuild non-durable (tracked).

    # Pinned review band (plan §7 preamble): calibration keeps production
    # values within the band; tests assert against the pinned constants.
    # #1349 T14: bge-small calibration (2026-08-21) — 0.60->0.84 / 0.92->0.94
    # (the bge cosine distribution is compressed toward 1.0 vs MiniLM).
    DEDUP_REVIEW_THRESHOLD = 0.84
    DEDUP_AUTO_MERGE_THRESHOLD = 0.94

    def _dedup_content_candidates(self, point_ids: list[str],
                                  threshold: float = DEDUP_REVIEW_THRESHOLD,
                                  sdk_for_wiring=None) -> dict:
        """Two-tier content dedup over freshly-extracted Points (#784, W-2).

        For each new non-operator Point whose pointKind is 'decision':
        Tier 1 — content-hash vs existing decision Points; Tier 2 — embedding
        cosine vs existing decision Points. On a hit:
          - existing prior is DRAFT → wire the "already decided" IMPL now
            (draft-to-draft, create_operator(promote_source=False)) and flag
            the candidate (DedupeRecorded event).
          - existing prior is LIVE → flag the candidate WITHOUT wiring
            (W-2 live-prior rule: a draft must never wire an operator to a
            live Point) — the link is scheduled for D2's promotion time
            (Variant C, wired by promote_point).
        Idempotent: points already carrying dedup_candidate=true are skipped
        (re-run → no duplicate IMPL, no new DedupeRecorded — DE2E-3).

        Returns {"hits": n, "wired_draft_to_draft": n, "deferred_live_prior": n}.
        """
        threshold = self.DEDUP_REVIEW_THRESHOLD if threshold is None else threshold
        proj = self._get_proj()
        rows = proj.g.query(
            "MATCH (n:Point) WHERE n.id IN $ids "
            "AND (n.is_operator IS NULL OR n.is_operator = false) "
            "RETURN n.id, n.content, n.pointKind, n.status, "
            "       coalesce(n.dedup_candidate, false)",
            params={"ids": list(point_ids)},
        ).result_set
        hits = wired = deferred = 0
        for pid, content, kind, status, already in rows:
            if already or status != "draft" or kind != "decision":
                continue
            if not content:
                continue
            # Tier 1: hash vs existing decisions (pointKind-scoped, N11).
            # The candidate itself is excluded — never its own prior (#784
            # review self-match fix).
            prior = self._content_exists(content, pointKind="decision",
                                         exclude_id=pid)
            method = "hash"
            similarity = 1.0
            if prior is None:
                # Tier 2: embedding similarity vs existing decisions,
                # excluding the candidate itself (self-cosine 1.0 would
                # otherwise always win the argmax and garbage-flag every
                # novel decision — #784 review P1).
                pairs = self._semantic_dedup(
                    [({"id": pid, "content": content}, "")],
                    threshold=threshold,
                    pointKind="decision",
                    return_pairs=True,
                    exclude_ids={pid},
                )
                if not pairs:
                    continue
                prior = pairs[0]["existing"]
                method = "embedding"
                similarity = pairs[0]["similarity"]
            if prior == pid:
                # Belt-and-braces: never self-target.
                _logger.warning("dedup: candidate %s matched itself — skipped", pid)
                continue
            # Mark the candidate (review-queue state on the Point).
            proj.g.query(
                "MATCH (n:Point {id:$id}) SET n.dedup_candidate = true, "
                "n.dedup_method = $m, n.dedup_similarity = $s, "
                "n.dedup_target_id = $t",
                params={"id": pid, "m": method, "s": similarity, "t": prior},
            )
            hits += 1
            # Prior status decides wiring vs deferral.
            prow = proj.g.query(
                "MATCH (n:Point {id:$id}) RETURN n.status",
                params={"id": prior},
            ).result_set
            prior_status = (prow[0][0] if prow else None) or "live"
            if prior_status == "draft":
                # Variant A: draft-to-draft "already decided" IMPL.
                if sdk_for_wiring is not None:
                    sdk_for_wiring.create_operator(
                        "IMPL", pid, [prior], label="alreadyDecided",
                        direction="unidirectional", promote_source=False)
                    wired += 1
            else:
                # Live prior: defer to promotion (Variant C).
                deferred += 1
            self._emit_event("DedupeRecorded", point=self.get_point(pid))
        return {"hits": hits, "wired_draft_to_draft": wired,
                "deferred_live_prior": deferred}

    def list_dedup_candidates(self, candidate_type: str = "content",
                              limit: int = 50) -> list[dict]:
        """Review queue for dedup candidates (plan §6.1, DE2E-3/DE2E-2).

        candidate_type='content': pending (unreviewed) content candidates —
        draft decision Points flagged by the two-tier dedup, shaped as
        {id, content, pointKind, method, similarity, target_id, batch_id}.
        candidate_type='entity': entity ambiguity pairs — the entity
        resolver surface (#783) is not yet implemented; returns [] (the MCP
        tool contract still holds).
        """
        if limit < 1:
            raise ValueError(f"limit must be >= 1, got {limit}")
        proj = self._get_proj()
        if candidate_type == "entity":
            return []  # #783 partial — entity queue tracked for epic completion
        if candidate_type not in ("content", "temporal"):
            raise ValueError(
                f"candidate_type must be 'content', 'temporal' or 'entity', "
                f"got {candidate_type!r}")
        if candidate_type == "content":
            rows = proj.g.query(
                "MATCH (n:Point) "
                "WHERE n.dedup_candidate = true AND n.dedup_reviewed IS NULL "
                "AND n.status = 'draft' "
                "AND (n.is_operator IS NULL OR n.is_operator = false) "
                "RETURN properties(n) ORDER BY n.createdAt DESC LIMIT $limit",
                params={"limit": limit},
            ).result_set
            out = []
            for (props,) in rows:
                out.append({
                    "id": props.get("id"),
                    "content": props.get("content"),
                    "pointKind": props.get("pointKind"),
                    "method": props.get("dedup_method"),
                    "similarity": props.get("dedup_similarity"),
                    "target_id": props.get("dedup_target_id"),
                    "existing_id": props.get("dedup_target_id"),  # §6.1 alias
                    "candidate_type": "content",
                    "status": props.get("dedup_reviewed") or "pending",
                    "batch_id": props.get("batch_id"),
                })
            return out
        # Temporal candidates (W-4, #786): contradictory/replacement decision
        # Points whose prior was LIVE at extraction — wire at promotion.
        rows = proj.g.query(
            "MATCH (n:Point) "
            "WHERE n.temporal_candidate = true AND n.temporal_reviewed IS NULL "
            "AND n.status = 'draft' "
            "AND (n.is_operator IS NULL OR n.is_operator = false) "
            "RETURN properties(n) ORDER BY n.createdAt DESC LIMIT $limit",
            params={"limit": limit},
        ).result_set
        out = []
        for (props,) in rows:
            out.append({
                "id": props.get("id"),
                "content": props.get("content"),
                "pointKind": props.get("pointKind"),
                "target_id": props.get("temporal_target_id"),
                "existing_id": props.get("temporal_target_id"),
                "replacement": bool(props.get("temporal_replacement")),
                "candidate_type": "temporal",
                "status": props.get("temporal_reviewed") or "pending",
                "batch_id": props.get("batch_id"),
            })
        return out

    def approve_merge(self, candidate_id: str, action: str = "merge") -> dict:
        """Review a content dedup candidate (plan §6.1, DE2E-3 Variants B/C).

        action='reject' → the candidate stays separate (dedup_reviewed=
        'reject', reviewed=true, DedupeRejected event) and is no longer
        surfaced by list_dedup_candidates.
        action='merge' → the "already decided" IMPL is wired now when the
        prior is DRAFT; when the prior is LIVE it is DEFERRED and wired at
        the candidate's promotion time (Variant C — promote_point wires the
        live→live link). Returns {candidate_id, action, wired,
        deferred_to_promotion, target_id}.
        """
        if action not in ("merge", "reject"):
            raise ValueError(f"action must be 'merge' or 'reject', got {action!r}")
        proj = self._get_proj()
        rows = proj.g.query(
            "MATCH (n:Point {id:$id}) RETURN properties(n)",
            params={"id": candidate_id},
        ).result_set
        if not rows:
            raise ValueError(f"No point {candidate_id!r}")
        props = rows[0][0]
        is_temporal = bool(props.get("temporal_candidate"))
        if not (props.get("dedup_candidate") or is_temporal):
            raise ValueError(
                f"Point {candidate_id!r} is not a dedup/temporal candidate")
        if is_temporal:
            # Temporal candidates: mark reviewed; the wire/supersede happens
            # at promotion (live-prior rule — never draft→live wiring).
            # Idempotent: re-approving with the SAME action is a no-op (no
            # duplicate event — #1080 review).
            if props.get("temporal_reviewed") == action:
                return {"candidate_id": candidate_id, "action": action,
                        "candidate_type": "temporal",
                        "wired": False,
                        "deferred_to_promotion": action == "merge",
                        "target_id": props.get("temporal_target_id"),
                        "already_reviewed": True}
            proj.g.query(
                "MATCH (n:Point {id:$id}) SET n.temporal_reviewed = $a, "
                "n.reviewed = true",
                params={"id": candidate_id, "a": action},
            )
            if action == "reject":
                self._emit_event("DedupeRejected",
                                 point=self.get_point(candidate_id))
            else:
                self._emit_event("DedupeRecorded",
                                 point=self.get_point(candidate_id))
            return {"candidate_id": candidate_id, "action": action,
                    "candidate_type": "temporal",
                    "wired": False,
                    "deferred_to_promotion": action == "merge",
                    "target_id": props.get("temporal_target_id")}
        target_id = props.get("dedup_target_id")
        proj.g.query(
            "MATCH (n:Point {id:$id}) SET n.dedup_reviewed = $a, n.reviewed = true",
            params={"id": candidate_id, "a": action},
        )
        wired = False
        deferred = False
        if action == "merge" and target_id:
            trow = proj.g.query(
                "MATCH (n:Point {id:$id}) RETURN n.status",
                params={"id": target_id},
            ).result_set
            target_status = (trow[0][0] if trow else None) or "live"
            if target_status == "draft":
                exists = proj.g.query(
                    "MATCH (op:Point {label:'alreadyDecided'})-[:IMPL]->"
                    "(:Point {id:$cand}), "
                    "(op)-[:IMPL]->(:Point {id:$prior}) RETURN count(op)",
                    params={"cand": candidate_id, "prior": target_id},
                ).result_set
                if not exists or exists[0][0] == 0:
                    self.create_operator(
                        "IMPL", candidate_id, [target_id],
                        label="alreadyDecided",
                        direction="unidirectional", promote_source=False)
                    wired = True
                else:
                    wired = True  # already linked — idempotent approve
            else:
                deferred = True  # wired at promotion (Variant C)
        if action == "reject":
            self._emit_event("DedupeRejected", point=self.get_point(candidate_id))
        else:
            self._emit_event("DedupeRecorded", point=self.get_point(candidate_id))
        return {"candidate_id": candidate_id, "action": action,
                "wired": wired, "deferred_to_promotion": deferred,
                "target_id": target_id}

    def list_drafts(self, *, limit: int = 50) -> list[dict]:
        """Draft queue for promotion review (J-5 companion, plan §6.1).

        Returns up to `limit` non-operator Points with status 'draft' (newest
        first), each shaped as {id, content, pointKind, provenance,
        dedup_context, batch_id}. `provenance` is the node's extractedFrom/
        provenance property when present; `dedup_context` is assembled from the
        #782 dedup candidate properties when the Point is a dedup candidate.
        """
        if limit < 1:
            raise ValueError(f"limit must be >= 1, got {limit}")
        proj = self._get_proj()
        # Plain points carry NO is_operator property (only operators set it),
        # so match on absence-or-false, not the literal property value.
        rows = proj.g.query(
            "MATCH (n:Point) "
            "WHERE n.is_operator = false "
            "AND n.status = 'draft' "
            "RETURN properties(n) ORDER BY n.createdAt DESC LIMIT $limit",
            params={"limit": limit},
        ).result_set
        out = []
        for (props,) in rows:
            dedup_context = None
            if props.get("dedup_candidate"):
                dedup_context = {
                    k: props.get(k) for k in (
                        "dedup_method", "dedup_similarity", "dedup_target_id")
                }
            out.append({
                "id": props.get("id"),
                "content": props.get("content"),
                "pointKind": props.get("pointKind"),
                "provenance": props.get("provenance")
                or props.get("extractedFrom"),
                "dedup_context": dedup_context,
                "batch_id": props.get("batch_id"),
            })
        return out

    # ── Operators ─────────────────────────────────────────────────

    def create_operator(self, op_type: str, source_id: str, target_ids: list[str],
                        label: str | None = None,
                        direction: str = "bidirectional",
                        promote_source: bool = True) -> dict:
        """Create an operator Point with optional semantic label.

        Semantic-epistemic edge model (#7801):
          - op_type: IMPL or NAND (epistemic mechanism)
          - label: domain verb — "addresses", "hasPart", "opposes" (semantic layer)
          - direction: "bidirectional" (default) or "unidirectional" — explicit
            flag controlling EP back-propagation (ONTOLOGY v3.1 §3.1, §8).
            Default bidirectional (mutual) for all op types; pass
            "unidirectional" for a directed attack (no back-pressure).
          - Operator carries the label and direction; IMPL/NAND edges carry confidence via EP.
          - promote_source: default True preserves the #131 draft→live lifecycle
            (source point goes live when its first edge is created). Pass
            False for extraction paths (#780): the operator node itself is
            created with status:'draft' AND the source is NOT auto-promoted —
            a draft must never wire an operator to a live Point. NOTE: there is
            currently NO public promote API for draft operators — they stay
            draft until the reviewer-gated promotion path lands (#785
            promote_point); run(include_draft=True) is the only sanctioned
            escape hatch today. The emitted OperatorAdded event carries the
            draft status so JSONL replay preserves it
            (projection/entities.py coalesce default is 'live').
        """
        if op_type not in ("IMPL", "NAND", "composedOf", "decomposesInto", "contains", "wraps"):
            raise ValueError(
                f"op_type must be 'IMPL', 'NAND', or a part/whole type, got {op_type!r}"
            )
        # #1934 (epic #1891 slice 3): relation-level write validation —
        # warn-not-block (governance D1/D2). An undeclared domain label is
        # warned (structured) and the write proceeds; the violations event
        # feeds the future governance app. Consults the pack registry's
        # declared relation predicates — the ladder is no longer dead config.
        #
        # #2030 (verified, NOT a bug): relation predicates are declared and
        # matched BARE by contract — manifests declare bare `relations[]
        # .predicate` verbs and the write path passes bare labels, so the
        # bare-vs-bare comparison is consistent. Namespaced enforcement
        # resolution (#2030) applies to the KIND arm of resolve_enforcement
        # only; a namespaced relation label is out of scope here. (Epic §6
        # scope: the "undeclared relation/kind-PAIR → warn-not-block"
        # contract has NO kind-pair leg on the write path — this check
        # covers the relation leg only; the kind-pair leg is an epic-level
        # gap, out of #2030 scope.)
        warnings: list[dict] = []
        if label:
            from tortoise.domain_loader import _get_registry
            from tortoise.enforcement import emit_violation, warning_for_relation
            reg = _get_registry()
            declared = set()
            if reg is not None:
                declared = {r.get("predicate") for r in reg.list_relations()}
            if label not in declared and label not in ("IMPL", "NAND", "MITIGATES"):
                warnings.append(warning_for_relation(label))
                emit_violation(code="undeclared_relation", relation=label,
                               detail=f"op_type={op_type}")
        # Direction default (CYCLE-25 per-op_type, ontology v3.6 #5):
        # direction-absent canonicalizes per op_type — IMPL → "bidirectional"
        # (unchanged), NAND → "unidirectional" (extraction default — ingest IS
        # the extraction path: new-claim-attacks-existing is the common case;
        # bidirectional NAND requires explicit mutual-restatement declaration).
        # A caller may explicitly pass "unidirectional"/"bidirectional" to
        # override.
        if direction is None:
            direction = self._canonical_direction(op_type, None)
        if direction not in ("bidirectional", "unidirectional"):
            raise ValueError(
                f"direction must be 'bidirectional' or 'unidirectional', got {direction!r}"
            )
        pid = ulid()
        inputs = [source_id] + list(target_ids)  # noqa: RUF005
        proj = self._get_proj()

        # Validate all source/target Points exist FIRST (fail loudly, not
        # silently) — then emit, then mutate. P1 (code-review): emitting
        # before validation produced phantom OperatorAdded events on missing
        # inputs, visible to subscription poll consumers.
        existing = proj.g.query(
            "MATCH (n) WHERE (n:Point OR n:Event) AND n.id IN $ids RETURN n.id",
            params={"ids": inputs},
        ).result_set
        existing_ids = {row[0] for row in existing}
        missing = [i for i in inputs if i not in existing_ids]
        if missing:
            raise ValueError(f"Cannot create operator: Points {missing} do not exist")

        # Build operator node with direction + optional label (context is NOT written — P1 #49).
        # #780: extraction operators (promote_source=False) carry status:'draft' so
        # the EP draft filter excludes them; the event path default ('live',
        # projection/entities.py coalesce) is overridden by the explicit status.
        extra_props = []
        params = {"id": pid, "op": op_type, "direction": direction}
        if label:
            extra_props.append("label:$label")
            params["label"] = label
        if not promote_source:
            extra_props.append("status:$st")
            params["st"] = "draft"
        props_clause = ", " + ", ".join(extra_props) if extra_props else ""
        proj.g.query(
            f"CREATE (o:Point {{id:$id, is_operator:true, op_type:$op, direction:$direction{props_clause}}})",
            params=params,
        )
        # Ontology v2.1: map part/whole ops to hasPart.
        # A1b (#1272): operator endpoints may be Point OR Event nodes.
        # #1919 (P2-10): the typed edge is mirrored by a reverse
        # (s)-[:INPUT]->(o) edge — the same shape the OperatorAdded replay
        # MERGEs (projection/edges.py), so live graphs and rebuilt graphs
        # carry identical INPUT edges (fold-parity).
        edge_type = "hasPart" if op_type not in ("IMPL", "NAND") else op_type
        for i, inp_id in enumerate(inputs):
            proj.g.query(
                f"MATCH (o:Point {{id:$oid}}), (s) WHERE (s:Point OR s:Event) "
                f"AND s.id = $sid "
                f"CREATE (o)-[:{edge_type} {{idx:$i}}]->(s) "
                f"CREATE (s)-[:INPUT {{idx:$i}}]->(o)",
                params={"oid": pid, "sid": inp_id, "i": i},
            )
        # Draft → live lifecycle (#131): source point goes live when first edge created.
        # P1 (code-review): draft → live promote ONLY for draft/null sources —
        # an unconditional promote resurrected retracted (terminal) sources,
        # violating the terminal-state contract with no event in the stream.
        # #780: extraction paths (promote_source=False) skip this entirely —
        # the source stays draft and the draft operator node carries the status.
        if promote_source:
            proj.g.query(
                "MATCH (s:Point {id:$sid}) "
                "WHERE (s.status IS NULL OR s.status = 'draft') "
                "SET s.status = 'live'",
                params={"sid": source_id},
            )
        # Dreaming (#85): new edges change propagation — mark all inputs dirty.
        self._mark_dirty(inputs)
        # Epic 903-C4 (#1242): a new operator changes the factor graph —
        # existing direct-edge messages around its endpoints may be stale
        # (a formerly operator-less claim now has an operator). Drop seeds.
        self._get_ep().invalidate_messages(inputs)
        result = self.get_point(pid)
        # #1934: merge the structured warnings into the result (warn-not-block
        # contract — consumers may surface or ignore; the shape is stable).
        if warnings:
            result["warnings"] = warnings
        # #432+#548 unified: domain payload + full point snapshot for both
        # the :GraphEvent store (subscriptions/poll) and JSONL (rebuild_all).
        event_point = dict(result)
        event_point["operator"] = {"op_type": op_type, "inputs": list(inputs)}
        self._emit_event("OperatorAdded", {
            "id": pid, "op_type": op_type, "source_id": source_id,
            "target_ids": list(target_ids),
        }, point=event_point)
        return result

    # ── Operator action consolidation (epic #888 W2) ──────────────────
    # operator_action() is the unified operator write entry; the legacy
    # mitigate_operator/annotate_operator remain the implementations.

    def operator_action(self, action: str, **kwargs) -> dict:
        """Consolidated operator write action (epic #888 W2, PR #912).

        action='mitigate' → mitigate_operator(id=..., reason=..., strength=)
            Creates/updates the mitigation Point modulating an operator's edge
            strength (idempotent).
        action='annotate' → annotate_operator(id=..., bias=..., precision=...,
            consistency=..., directness=...) — structured epistemic dims.

        Unknown action raises ValueError.
        """
        if action == "mitigate":
            return self.mitigate_operator(
                kwargs["id"], kwargs["reason"],
                kwargs.get("strength", 0.5))
        if action == "annotate":
            return self.annotate_operator(
                kwargs["id"], kwargs["bias"], kwargs["precision"],
                kwargs["consistency"], kwargs["directness"])
        raise ValueError(
            f"operator_action: unknown action {action!r} — must be "
            f"'mitigate' or 'annotate'")

    def annotate_operator(self, id: str, bias: float, precision: float,
                          consistency: float, directness: float) -> dict:
        """Annotate an operator Point with structured epistemic dimensions.

        Args:
            id: Operator Point ID (must have is_operator=true).
            bias: 0-1, how much hidden stake/additional interest beyond stated position.
            precision: 0-1, how narrow/well-defined the relevance claim is.
            consistency: 0-1, how stable this relevance is across contexts.
            directness: 0-1, how directly the source bears on the target.

        Raises ValueError if id not found, not an operator, or dims out of [0,1].
        """
        point = self.get_point(id)
        if not point:
            raise ValueError(f"Operator {id!r} not found")
        if not point.get("is_operator"):
            raise ValueError(f"Point {id!r} is not an operator")
        for name, val in (("bias", bias), ("precision", precision),
                          ("consistency", consistency), ("directness", directness)):
            if not 0 <= val <= 1:
                raise ValueError(f"{name} must be 0-1, got {val}")
        # #432 Task 3: durable OperatorAnnotated event (append-before-mutation).
        self._emit_event("OperatorAnnotated", {
            "id": id, "bias": bias, "precision": precision,
            "consistency": consistency, "directness": directness,
        })
        return self.update_point(id,
            annotator_bias=bias, annotator_precision=precision,
            annotator_consistency=consistency, annotator_directness=directness)

    def mitigate_operator(self, id: str, reason: str, strength: float = 0.5) -> dict:
        """Create a mitigation Point that modulates an operator's edge strength.

        Args:
            id: Operator Point ID to mitigate.
            reason: Why the edge is weaker than it appears.
            strength: 0-1, 0=fully neutralized, 1=fully intact (default 0.5).

        Raises ValueError if id not found or not an operator.
        Idempotent: second call updates existing mitigation (reason + strength),
        does not create a duplicate.
        """
        if not 0 <= strength <= 1:
            raise ValueError(f"strength must be 0-1, got {strength}")
        point = self.get_point(id)
        if not point:
            raise ValueError(f"Operator {id!r} not found")
        if not point.get("is_operator"):
            raise ValueError(f"Point {id!r} is not an operator")
        # Idempotency: check for existing mitigation
        proj = self._get_proj()
        existing = proj.g.query(
            "MATCH (op:Point {id:$id})-[r:mitigated_by]->(m:Point) RETURN m.id",
            params={"id": id},
        ).result_set
        if existing:
            mid = existing[0][0]
            return self.update_point(mid, content=f"[MITIGATION] {reason}",
                                     mitigation_strength=strength)
        # Create new mitigation Point
        mid = ulid()
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()  # noqa: UP017
        proj.g.query(
            "CREATE (m:Point {id:$id, content:$c, pointKind:'statement', "
            "mitigation_strength:$s, is_operator:false, createdAt:$now, updatedAt:$now})",
            params={"id": mid, "c": f"[MITIGATION] {reason}", "s": strength, "now": now},
        )
        # Bidirectional link: mitigation Point -[:IMPL]-> operator, operator <-[:mitigated_by]- mitigation
        proj.g.query(
            "MATCH (m:Point {id:$mid}), (op:Point {id:$oid}) "
            "CREATE (m)-[:IMPL]->(op), (op)-[:mitigated_by]->(m)",
            params={"mid": mid, "oid": id},
        )
        # Dreaming (#85): new mitigation + IMPL edge change propagation.
        self._mark_dirty([mid, id])
        # #548: emit events for rebuild parity
        self._emit_event("PointAdded", point=self.get_point(mid))
        # Emit OperatorAdded so the IMPL edge (mitigation → operator) is
        # recreated on replay. mitigated_by edges are ancillary and
        # reconstructed separately via the operator's edge replay.
        mit_point = self.get_point(mid)
        mit_point["operator"] = {"op_type": "IMPL", "inputs": [id]}
        self._emit_event("OperatorAdded", point=mit_point)
        return self.get_point(mid)

    # ── Query ─────────────────────────────────────────────────────

    def query(self, kind: str | None = None,
              *, include_retracted: bool = False,
              **filters) -> list[dict]:
        """Query points by pointKind and/or custom property filters.

        #432 Task 2: retracted points (status='retracted') are EXCLUDED by
        default — pass include_retracted=True, or an explicit status= filter
        (e.g. status='retracted'), to surface tombstones.

        For confidence-aware queries, use tortoise_fts_query() with query=None
        for full-scan mode with EP annotation.

        #1391: the default exclusion now covers ALL terminal statuses
        (retracted, superseded, outdated, archived) — stale claims are not
        served as current. include_retracted (name kept for compat) surfaces
        them for audit. Use a raw Cypher query via proj.g.query() to inspect
        tombstones.
        """
        proj = self._get_proj()
        clauses = ["n.is_operator = false"]
        params: dict[str, Any] = {}
        # #1391: terminal-status exclusion — skipped when the caller explicitly
        # filters by status (their filter controls visibility). include_retracted
        # (name kept for compat) surfaces ALL terminal statuses for audit.
        if not include_retracted and "status" not in filters:
            from .search_engine import _exclude_status_clause
            clauses.append(_exclude_status_clause("n"))
        if kind:
            expanded = self._expand_kind(kind)
            if len(expanded) == 1:
                clauses.append("n.pointKind = $kind")
                params["kind"] = expanded[0]
            else:
                placeholders = [f"$kind_{i}" for i in range(len(expanded))]
                clauses.append(f"n.pointKind IN [{', '.join(placeholders)}]")
                for i, k in enumerate(expanded):
                    params[f"kind_{i}"] = k
        for key, val in filters.items():
            # #329: strict ASCII identifier + reserved-key rejection (the old
            # isalnum check allowed Unicode keys that broke Cypher params and
            # filter keys colliding with kind/kind_N/skip/limit silently
            # overwrote auto-generated parameters).
            from .security import validate_filter_key
            validate_filter_key(key)
            clauses.append(f"n.`{key}` = ${key}")
            params[key] = val
        where = " AND ".join(clauses)
        rows = proj.g.query(
            f"MATCH (n:Point) WHERE {where} RETURN properties(n)",
            params=params,
        ).result_set
        return [r[0] for r in rows]

    def paginated_query(self, kind: str | None = None,
                        skip: int = 0, limit: int = 20,
                        *, include_retracted: bool = False,
                        **filters) -> dict:
        """Query points with pagination. Returns {results, total, hasMore}.

        #432 Task 2: retracted points (status='retracted') are EXCLUDED by
        default — pass include_retracted=True, or an explicit status= filter,
        to surface tombstones. #1391: the default exclusion now covers ALL
        terminal statuses (retracted, superseded, outdated, archived).
        """
        # #1914: invalid pagination params fail cleanly instead of looping
        # (limit=0 → hasMore = skip + 0 < total always True on non-empty
        # graphs → infinite pagination loop) or passing raw negatives into
        # Cypher. Mirrors the guard in tortoise_query (mcp_server.py) and
        # the other paginators (list_drafts, belief_timeline).
        if skip < 0:
            raise ValueError(f"skip must be >= 0, got {skip}")
        if limit < 1:
            raise ValueError(f"limit must be >= 1, got {limit}")
        proj = self._get_proj()
        clauses = ["n.is_operator = false"]
        params: dict[str, Any] = {}
        # #1391: terminal-status exclusion — skipped when the caller explicitly
        # filters by status (their filter controls visibility). include_retracted
        # (name kept for compat) surfaces ALL terminal statuses for audit.
        if not include_retracted and "status" not in filters:
            from .search_engine import _exclude_status_clause
            clauses.append(_exclude_status_clause("n"))
        if kind:
            expanded = self._expand_kind(kind)
            if len(expanded) == 1:
                clauses.append("n.pointKind = $kind")
                params["kind"] = expanded[0]
            else:
                placeholders = [f"$kind_{i}" for i in range(len(expanded))]
                clauses.append(f"n.pointKind IN [{', '.join(placeholders)}]")
                for i, k in enumerate(expanded):
                    params[f"kind_{i}"] = k
        for key, val in filters.items():
            # #329: strict ASCII identifier + reserved-key rejection (the old
            # isalnum check allowed Unicode keys that broke Cypher params and
            # filter keys colliding with kind/kind_N/skip/limit silently
            # overwrote auto-generated parameters).
            from .security import validate_filter_key
            validate_filter_key(key)
            clauses.append(f"n.`{key}` = ${key}")
            params[key] = val
        where = " AND ".join(clauses)
        total = proj.g.query(
            f"MATCH (n:Point) WHERE {where} RETURN count(n)",
            params=params,
        ).result_set[0][0]
        rows = proj.g.query(
            f"MATCH (n:Point) WHERE {where} RETURN properties(n)"
            f" ORDER BY n.createdAt DESC SKIP $skip LIMIT $limit",
            params={**params, "skip": skip, "limit": limit},
        ).result_set
        results = [r[0] for r in rows]

        return {"results": results, "total": total, "hasMore": skip + limit < total}

    def get_point(self, id: str) -> dict:
        """Get a Point by ID. Returns dict of all properties, or {} if not found.

        #432: retracted points (status='retracted') ARE returned by get_point
        — they are tombstoned, not deleted. Query surfaces exclude them by
        default (opt-in via include_retracted).
        """
        proj = self._get_proj()
        rows = proj.g.query(
            "MATCH (n:Point {id:$id}) RETURN properties(n)",
            params={"id": id},
        ).result_set
        return rows[0][0] if rows else {}

    def traverse(self, id: str, relationship_type: str, direction: str = "outgoing") -> list[dict]:
        """Traverse relationships from a Point. Returns connected point dicts.

        #329: relationship_type is allowlisted (KNOWN_REL_TYPES) — it is
        interpolated into the query STRUCTURE (``-[:TYPE]->``) where
        parameterization is impossible; an unvalidated value is a Cypher
        injection primitive. direction is validated to outgoing/incoming.
        """
        proj = self._get_proj()
        # #329: validate before building any Cypher
        from .security import validate_rel_type
        validate_rel_type(relationship_type)
        if direction not in ("outgoing", "incoming"):
            raise ValueError(f"Invalid direction: {direction!r}. Use 'outgoing' or 'incoming'.")
        pat = (f"(n:Point {{id:$id}})-[:{relationship_type}]->(m:Point)"
               if direction == "outgoing" else
               f"(n:Point {{id:$id}})<-[:{relationship_type}]-(m:Point)")
        rows = proj.g.query(
            f"MATCH {pat} RETURN m.id, m.content, m.pointKind",
            params={"id": id},
        ).result_set
        return [
            {"id": r[0], "content": r[1], "pointKind": r[2]}
            for r in rows
        ]

    # ── Chain Integrity ───────────────────────────────────────────

    def check_structure(self) -> list[dict]:
        """Check Gate 0→4 chain integrity. Thin delegation wrapper over the
        domain-validator registry (issue #405): runs the product-strategy
        graph-surface validator and maps its enriched output back to the
        legacy {type, id, message} contract (preserved for the mcp_server,
        tool_registry, test_sdk and test_integration_search consumers).

        Behavior note (#405): chain rules now run on LIVE points only (draft
        points are the orphaned_draft rule's job) — a draft with a dangling
        ref or no JTBD parent is no longer double-flagged."""
        proj = self._get_proj()
        from .domain_validators import (  # noqa: I001
            run_domain_graph_validators, to_legacy_shape,
        )
        violations, _drift = run_domain_graph_validators(
            "product-strategy", proj)
        return to_legacy_shape(violations)

    def validate_domain(self, domain: str) -> dict:
        """Run a domain's graph-surface validators (issue #405) — advisory,
        read-only, on-demand. Returns
        ``{domain, ok, violations, drift}`` where violations are enriched,
        actionable dicts ({rule, kind, ref, message, fix}). Raises ValueError
        for an unknown domain (no loaded pack AND no registered validators)."""
        proj = self._get_proj()
        from .domain_validators import run_domain_graph_validators
        violations, drift = run_domain_graph_validators(domain, proj)
        return {
            "domain": domain,
            "ok": not violations,
            "violations": violations,
            "drift": drift,
        }

    def audit(self, point_kinds: list[str] | None = None) -> dict:
        """Audit graph wiring quality — structured JSON report (epic #348).

        Runs the audit checks (missing sourceKind point-level legacy + Source-
        level canonical, missing sourceDate, superseded points without a
        CORRECTS edge, live IMPL/NAND edges into superseded points, naive-IMPL
        heuristic, low-confidence operators without mitigation, and legacy
        ``mitigates`` edges). Returns per-check counts (uncapped) + capped
        samples + summary + exit_code (0 clean, 1 issues found — the
        check-consistency precedent).

        point_kinds: Optional list of pointKind values to scope the audit
                     (default: all Points).
        """
        from tortoise.audit import audit_graph
        proj = self._get_proj()
        return audit_graph(proj, point_kinds=point_kinds).to_dict()

    def summarize_structure(self) -> dict:
        """Count points per Gate (by pointKind). Returns {gate: count, ..., total}.

        P1 #49: re-keyed from context strings (tortoise-wf-gate0..4) to pointKind
        (jobToBeDone, useCase, userJourney, workflow, requirement). Pre-existing
        experimental points that had context but no matching pointKind may show 0
        — expected under the #49 re-home (pointKind is the target vocabulary).
        """
        proj = self._get_proj()
        gates = [
            ("gate0_jtbds", "jobToBeDone"),
            ("gate1_use_cases", "useCase"),
            ("gate2_user_journeys", "userJourney"),
            ("gate3_workflows", "workflow"),
            ("gate4_requirements", "requirement"),
        ]
        result: dict[str, int] = {}
        for key, kind in gates:
            result[key] = proj.g.query(
                "MATCH (n:Point {pointKind:$k}) "
                "WHERE n.is_operator = false "
                "RETURN count(n)",
                params={"k": kind},
            ).result_set[0][0]
        result["total"] = sum(result.values())
        return result

    # ── Taxonomy ─────────────────────────────────────────────────

    def taxonomy(self) -> dict[str, int]:
        """Count entities by node label. Returns {Point: N, Event: N, ...}."""
        from .taxonomy import taxonomy as _taxonomy
        return _taxonomy(self._get_proj())

    def list_pointkinds(self) -> list[dict]:
        """All pointKinds present in the graph with counts. Returns [{kind, count, pack}]."""
        proj = self._get_proj()
        rows = proj.g.query(
            "MATCH (n:Point) "
            "WHERE n.is_operator = false "
            "AND n.pointKind IS NOT NULL "
            "RETURN n.pointKind, count(n) ORDER BY count(n) DESC"
        ).result_set
        result: list[dict] = []
        for row in rows:
            kind = row[0]
            count = row[1]
            pack = kind.split(":", 1)[0] if ":" in kind else ""
            result.append({"kind": kind, "count": count, "pack": pack})
        return result

    def list_sources(self) -> list[dict]:
        """All Sources with point counts. Returns [{url, sourceKind, points}]."""
        proj = self._get_proj()
        rows = proj.g.query(
            "MATCH (s:Source) "
            "OPTIONAL MATCH (p:Point)-[:extractedFrom]->(s) "
            "RETURN s.url, s.sourceKind, count(p) AS points "
            "ORDER BY points DESC"
        ).result_set
        return [
            {"url": row[0], "sourceKind": row[1], "points": row[2]}
            for row in rows
        ]

    def list_tags(self) -> list[dict]:
        """All Tag names with count of tagged Points. Returns [{name, count}].

        Orphaned :Tag nodes (no TAGGED edges) are garbage-collected when
        edges are removed (update_point tag sync + delete_point, #485), so
        count 0 entries should not normally appear.
        """
        proj = self._get_proj()
        rows = proj.g.query(
            "MATCH (t:Tag) "
            "OPTIONAL MATCH (p:Point)-[:TAGGED]->(t) "
            "RETURN t.name AS name, count(p) AS count "
            "ORDER BY name"
        ).result_set
        return [{"name": row[0], "count": row[1]} for row in rows]

    def query_points_by_tag(self, tag: str) -> list[dict]:
        """Return Points connected via TAGGED edge to the given Tag name."""
        proj = self._get_proj()
        rows = proj.g.query(
            "MATCH (p:Point)-[:TAGGED]->(t:Tag {name:$tag}) "
            "RETURN properties(p) "
            "ORDER BY p.createdAt DESC",
            params={"tag": tag},
        ).result_set
        return [r[0] for r in rows]

    def list_namespaces(self) -> list[dict]:
        """Installed pack namespaces with kind counts. Returns [{namespace, name, kind_count}]."""
        registry = _get_kind_expander()
        packs = registry.list_packs()
        return [
            {
                "namespace": p["namespace"],
                "name": p["name"],
                "kind_count": sum(p["kind_counts"].values()),
            }
            for p in packs
        ]

    def list_topics(self, entity_id: str) -> dict:
        """entityProfile lite for an entity. Returns {id, pointKind, context, neighbors, neighborCounts}."""
        from .taxonomy import list_topics as _list_topics
        return _list_topics(self._get_proj(), entity_id)

    def topic_summarize(
        self,
        topic: str,
        *,
        max_seeds: int = 50,
        max_hops: int = 1,
        include_relationships: bool = True,
    ) -> dict:
        """Epistemic topic summarization — settled vs contested structure (#592).

        For a topic query, returns the epistemic structure: what is significant
        (settled — high confidence, strong connections) and what is contested
        (elevated variance, NAND conflicts), plus the argument topology.

        Args:
            topic: Topic string (e.g. "pricing", "architecture").
            max_seeds: Max seed Points to retrieve from about* edges + content match.
            max_hops: Operator-chain expansion depth (0 = seeds only, 1 = neighbors).
            include_relationships: Whether to include argument topology.

        Returns:
            dict with keys: topic, total_points, significant, contested,
            disputed_pairs, argument_structure, meta.
        """
        from .topic_summarization import topic_summarize as _summarize
        result = _summarize(
            self._get_proj().g,
            topic,
            max_seeds=max_seeds,
            max_hops=max_hops,
            include_relationships=include_relationships,
        )
        return result.to_dict()

    # ── Bulk ──────────────────────────────────────────────────────

    def batch_create_points(self, points_list: list[dict]) -> list[dict]:
        """Create multiple points. Each dict needs {kind, content, **props}."""
        return [self.create_point(**p) for p in points_list]

    # ── Heterogeneous bulk write — ingest (epic #888 W4) ─────────────

    # Operator vocabularies accepted by connection specs (create_operator's
    # op_type whitelist — kept in sync with create_operator's validation).
    _INGEST_OPERATOR_TYPES = frozenset(
        ("IMPL", "NAND", "composedOf", "decomposesInto", "contains", "wraps")
    )

    # ── Shared pre-validation check helpers (epic #902 W4 A1) ────────────
    #
    # Checks 1-8 from plan §5.2, extracted from the checks originally raised
    # mid-write inside ingest(). Consumed by BOTH `_validate_bundle` (Phase 1
    # — collects ALL violations, zero mutation) and ingest()'s write path
    # (Phase 2 — defense-in-depth raises). The SAME helper emits the SAME
    # message in both phases, so Phase-1/2 parity holds by construction
    # (asserted by the parity unit test).
    #
    # CYCLE-25 (ontology v3.8): pointKind `statement` is THE write kind;
    # legacy kinds are write-compat-only; `event` is REJECTED (episodic
    # records are entity items type:'event' with eventKind); a point item
    # WITHOUT kind DEFAULTS to `statement`. `quote` (≤200 chars) is a
    # permitted point-item field; `c_cal` is calibrated-pipeline-write-only
    # (a bundle carrying it is a violation).
    #
    # CYCLE-26 (GATE-2 Q3): check 5's fail-closed policy-feasibility
    # rejection is REMOVED — gated operator-requiring connections are
    # ACCEPTED (the operator's EP activity is derived: ≥2 connected points
    # live — A9 #1059). The RETAINED piece: gated + explicit status:"live"
    # on a point item is a violation (no bypass of the gated contract).

    _INGEST_TERMINAL_STATUSES = frozenset(("superseded", "retracted"))
    _INGEST_DIRECTION_VALUES = frozenset(("bidirectional", "unidirectional"))
    _INGEST_LEGACY_WRITE_KINDS = frozenset((
        "decision", "vision", "strategy", "plan", "goal", "target",
        "humanApproval", "observation", "hypothesis",
    ))

    def _check_item_shape(self, section: str, index: int, item: Any,
                          violations: list[dict]) -> None:
        """Check 1 — section item shape (dict-ness + required fields, incl.
        the CYCLE-25 quote/c_cal rules) and check 8 — the ingest-scoped
        batch_id guard (a bundle item carrying `batch_id` is a violation;
        the server computes and stamps it, §4.2)."""
        if not isinstance(item, dict):
            violations.append({
                "section": section, "index": index,
                "message": f"ingest: {section}[{index}] must be a dict",
            })
            return
        if "batch_id" in item:
            violations.append({
                "section": section, "index": index,
                "message": f"ingest: {section}[{index}] batch_id is "
                           f"server-managed and cannot be set on bundle items",
            })
        # #1486 (P0 re-review): is_episodic is the points-quota discriminator —
        # a tenant marking bundle items episodic would exclude them from the
        # points quota (unlimited points past the paid-tier cap). Rejected at
        # shape time so the **item splats below can never bind the SDK's
        # explicit server-managed params.
        if "is_episodic" in item:
            violations.append({
                "section": section, "index": index,
                "message": f"ingest: {section}[{index}] is_episodic is "
                           f"server-managed and cannot be set on bundle items",
            })
        if section == "points":
            # kind is OPTIONAL (CYCLE-25: kind-absent defaults to
            # 'statement'); content is required.
            if item.get("content") is None or not isinstance(item.get("content"), str):
                # REVIEW-FIX P1 (cycle-26): non-string content is a Phase-1
                # violation (Phase 2 would AttributeError at _content_hash
                # mid-write after earlier sections commit).
                violations.append({
                    "section": section, "index": index,
                    "message": f"ingest: points[{index}] requires 'content'",
                })
            quote = item.get("quote")
            if quote is not None and (not isinstance(quote, str) or len(quote) > 200):
                violations.append({
                    "section": section, "index": index,
                    "message": f"ingest: points[{index}] quote exceeds 200 "
                               f"characters (provenance quote cap, v3.6 #11)",
                })
            if "c_cal" in item:
                violations.append({
                    "section": section, "index": index,
                    "message": f"ingest: points[{index}] c_cal is "
                               f"calibrated-pipeline-write-only — ingest never "
                               f"writes calibrated confidence",
                })
        elif section == "sources":
            url = item.get("url")
            if not url or not isinstance(url, str):
                violations.append({
                    "section": section, "index": index,
                    "message": f"ingest: sources[{index}] requires a "
                               f"non-empty 'url'",
                })
            if not item.get("sourceKind"):
                violations.append({
                    "section": section, "index": index,
                    "message": f"ingest: sources[{index}] requires 'sourceKind'",
                })
        elif section == "entities":
            _etype_v = item.get("type")
            etype = (_etype_v if isinstance(_etype_v, str) else "").strip().lower()
            if not etype:
                violations.append({
                    "section": section, "index": index,
                    "message": f"ingest: entities[{index}] requires 'type' "
                               f"(subject|object|event|document)",
                })
            elif etype not in ("subject", "object", "event", "document"):
                violations.append({
                    "section": section, "index": index,
                    "message": f"ingest: entities[{index}] type must be "
                               f"subject|object|event|document, got {etype!r}",
                })
            if not item.get("name"):
                violations.append({
                    "section": section, "index": index,
                    "message": f"ingest: entities[{index}] requires 'name'",
                })
            if etype == "event" and not item.get("eventKind"):
                violations.append({
                    "section": section, "index": index,
                    "message": f"ingest: entities[{index}] type='event' "
                               f"requires 'eventKind'",
                })
            if etype == "document" and not item.get("documentKind"):
                violations.append({
                    "section": section, "index": index,
                    "message": f"ingest: entities[{index}] type='document' "
                               f"requires 'documentKind'",
                })
        # connections: only dict-ness + the batch_id guard here — the
        # connection contract is check 4's job.

    def _check_kind(self, kind: Any, index: int, violations: list[dict]) -> None:
        """Check 2 — CYCLE-25 point-kind vocabulary (ontology v3.8).

        Accepts `statement` (THE extraction write kind — canonical) plus the
        legacy write-compat kinds (decision/vision/strategy/plan/goal/target/
        humanApproval/observation/hypothesis) for back-compat — and any other
        kind, since the kind registry is descriptive (warnings), not
        restrictive. REJECTS pointKind `event` (REMOVED, #1013 — episodic
        records are entity items type:'event' with eventKind). A point item
        WITHOUT kind is LEGAL and defaults to `statement` — the default is
        applied by the write path, not flagged here.
        """
        if kind is None:
            return
        if not isinstance(kind, str):
            # REVIEW-FIX P2 (cycle-26): a non-string kind must not silently
            # write pointKind:<int> into the graph — Phase-1 violation.
            violations.append({
                "section": "points", "index": index,
                "message": f"pointKind must be a string, got "
                           f"{type(kind).__name__}",
            })
            return
        if kind == "event":
            violations.append({
                "section": "points", "index": index,
                "message": "pointKind 'event' is not a write kind — use an "
                           "entity item with type:'event' and eventKind "
                           "('occurrence'/'turn') for episodic records",
            })

    def _check_refs(self, bundle: dict, violations: list[dict]) -> None:
        """Check 3 (ref-table half) — duplicate refs across the whole bundle
        and node-id-shadowing rejection: a ref shaped like a real node id —
        a bare ULID OR a prefixed entity id (e.g. ``sub-<hex26>``, #1553) —
        would make refs.get(x, x) silently address an existing node."""
        seen: dict[str, str] = {}
        for section in ("sources", "points", "entities"):
            for i, item in enumerate(bundle.get(section) or []):
                if not isinstance(item, dict):
                    continue  # shape violation already reported (check 1)
                ref = item.get("ref")
                if not ref:
                    continue
                if _is_entity_id(str(ref)):
                    violations.append({
                        "section": section, "index": i,
                        "message": f"ingest: {section}[{i}] ref {ref!r} is "
                                   f"shaped like a real node id (ULID or "
                                   f"prefixed entity id) — refs are bundle-"
                                   f"local labels, not node ids (a node-id-"
                                   f"shaped ref would silently shadow an "
                                   f"existing node)",
                    })
                    continue
                if ref in seen:
                    violations.append({
                        "section": section, "index": i,
                        "message": f"ingest: duplicate bundle ref {ref!r} "
                                   f"({section}) — refs must be unique across "
                                   f"the bundle",
                    })
                else:
                    seen[ref] = section

    def _connection_route(self, conn: dict) -> str:
        """Route classification for a connection item: 'direct' for plain
        IMPL/NAND (operator-less per §8 — the terminal-status guard is
        direct-edge-scoped), 'operator' for mitigation/reify:true/part-whole,
        'relation' otherwise."""
        if "operator" not in conn:
            return "relation"
        if conn["operator"] in ("IMPL", "NAND") and not conn.get("mitigation") \
                and not conn.get("reify"):
            return "direct"
        return "operator"

    @staticmethod
    def _canonical_direction(op_type: str, direction: str | None) -> str | None:
        """CYCLE-25 per-op_type absent↔default canonicalization (ontology v3.6
        #5): direction-ABSENT canonicalizes per op_type — IMPL → "bidirectional",
        NAND → "unidirectional" (the extraction default); other operator types
        keep create_operator's bidirectional default. Absent is NOT a distinct
        value in the §5.2.7 comparison."""
        if direction is not None:
            return direction
        return "unidirectional" if op_type == "NAND" else "bidirectional"

    def _check_connection(self, index: int, conn: Any,
                          violations: list[dict]) -> None:
        """Check 4 — the connection contract (pure; no graph access).

        exactly one of relation/operator; from/to presence; to shape/emptiness
        (zero-target rows c8(i-iv)); operator/relation vocabulary; self-edge
        rejection on IMPL/NAND; multi-target rejection on plain IMPL/NAND
        (cycle-7); §8 field validity (direction/confidence/weight/mitigation)
        + route-scoped attribute rejection (cycle-13: relation carries
        direction/confidence/weight; operator route carries confidence/weight).
        """
        if not isinstance(conn, dict):
            return  # dict-ness already reported by check 1
        if "from" not in conn or "to" not in conn:
            violations.append({
                "section": "connections", "index": index,
                "message": f"ingest: connections[{index}] requires 'from' "
                           f"and 'to'",
            })
            return
        tos = conn["to"]
        if not isinstance(tos, (list, str)):
            violations.append({
                "section": "connections", "index": index,
                "message": f"ingest: connections[{index}] 'to' must be a "
                           f"list or string, got {type(tos).__name__}",
            })
        frm = conn.get("from")
        if not isinstance(frm, str):
            # REVIEW-FIX P1 (cycle-26): non-string from is a Phase-1 violation
            # (Phase 2 would raise 'Points [5] do not exist' AFTER points are
            # committed — partial mutation, J2/E2E-1 zero-mutation violation).
            violations.append({
                "section": "connections", "index": index,
                "message": f"ingest: connections[{index}] 'from' must be a "
                           f"string ref, got {type(frm).__name__}",
            })
        if isinstance(tos, list) and not tos:
            violations.append({
                "section": "connections", "index": index,
                "message": f"ingest: connections[{index}] 'to' cannot be "
                           f"empty — at least one target is required",
            })
        elif isinstance(tos, list):
            for t in tos:
                if not isinstance(t, str):
                    violations.append({
                        "section": "connections", "index": index,
                        "message": f"ingest: connections[{index}] 'to' items "
                                   f"must be string refs, got "
                                   f"{type(t).__name__}",
                    })
                    break
        has_rel = "relation" in conn
        has_op = "operator" in conn
        if has_rel == has_op:
            violations.append({
                "section": "connections", "index": index,
                "message": f"ingest: connections[{index}] must carry exactly "
                           f"one of 'relation' (structural edge) or 'operator' "
                           f"(IMPL/NAND reification)",
            })
            return
        if has_op:
            op_type = conn["operator"]
            if op_type not in self._INGEST_OPERATOR_TYPES:
                violations.append({
                    "section": "connections", "index": index,
                    "message": f"ingest: connections[{index}] operator must "
                               f"be one of {sorted(self._INGEST_OPERATOR_TYPES)}, "
                               f"got {op_type!r}",
                })
            frm = conn.get("from")
            to_list = tos if isinstance(tos, list) else [tos]
            if op_type in ("IMPL", "NAND") and (frm == tos or frm in to_list):
                violations.append({
                    "section": "connections", "index": index,
                    "message": f"ingest: connections[{index}] self-edges are "
                               f"not allowed on IMPL/NAND — from and to "
                               f"address the same endpoint (EP cavity risk)",
                })
            route = self._connection_route(conn)
            if route == "direct" and isinstance(tos, list) and len(tos) > 1:
                violations.append({
                    "section": "connections", "index": index,
                    "message": f"ingest: connections[{index}] multi-item 'to' "
                               f"on a plain IMPL/NAND connection is not "
                               f"supported — split into singular connections, "
                               f"or use the operator route (reify:true or "
                               f"mitigation) for multi-input fan-out",
                })
            direction = conn.get("direction")
            if direction is not None and direction not in self._INGEST_DIRECTION_VALUES:
                violations.append({
                    "section": "connections", "index": index,
                    "message": f"ingest: connections[{index}] direction must "
                               f"be 'bidirectional' or 'unidirectional', got "
                               f"{direction!r}",
                })
            if route == "operator":
                # cycle-13 route scoping: create_operator accepts no
                # confidence/weight (they are plain direct-edge attributes).
                for key in ("confidence", "weight"):
                    if key in conn:
                        violations.append({
                            "section": "connections", "index": index,
                            "message": f"ingest: connections[{index}] {key} is "
                                       f"not allowed on the operator route "
                                       f"(reify:true/mitigation) — it belongs "
                                       f"on plain IMPL/NAND direct edges",
                        })
            else:
                for key in ("confidence", "weight"):
                    val = conn.get(key)
                    if val is not None and not (isinstance(val, (int, float))
                                                and not isinstance(val, bool)
                                                and 0 <= val <= 1):
                        violations.append({
                            "section": "connections", "index": index,
                            "message": f"ingest: connections[{index}] {key} must "
                                       f"be a number in [0, 1], got {val!r}",
                        })
            mitigation = conn.get("mitigation")
            if mitigation is not None:
                if not isinstance(mitigation, dict):
                    violations.append({
                        "section": "connections", "index": index,
                        "message": f"ingest: connections[{index}] mitigation must "
                                   f"be a dict {{reason, strength}}",
                    })
                else:
                    reason = mitigation.get("reason")
                    if not reason or not isinstance(reason, str):
                        violations.append({
                            "section": "connections", "index": index,
                            "message": f"ingest: connections[{index}] "
                                       f"mitigation.reason must be a non-empty "
                                       f"string",
                        })
                    strength = mitigation.get("strength")
                    if strength is None or not (isinstance(strength, (int, float))
                                                and not isinstance(strength, bool)
                                                and 0 <= strength <= 1):
                        violations.append({
                            "section": "connections", "index": index,
                            "message": f"ingest: connections[{index}] "
                                       f"mitigation.strength must be a number "
                                       f"in [0, 1], got {strength!r}",
                        })
        else:
            rel = conn["relation"]
            from .projection.edges import _VALID_EDGE_PREDICATES
            if rel != "extractedFrom" and rel not in _VALID_EDGE_PREDICATES:
                violations.append({
                    "section": "connections", "index": index,
                    "message": f"ingest: connections[{index}] unknown relation "
                               f"{rel!r} — must be a structural predicate or "
                               f"'extractedFrom'",
                })
            # cycle-13 route scoping: relation connections carry none of the
            # §8 edge attributes (direction/confidence/weight).
            for key in ("direction", "confidence", "weight"):
                if key in conn:
                    violations.append({
                        "section": "connections", "index": index,
                        "message": f"ingest: connections[{index}] {key} is not "
                                   f"allowed on relation connections (structural "
                                   f"edges) — it belongs on plain IMPL/NAND "
                                   f"direct edges",
                    })

    def _fetch_endpoint_info(self, values: set[str]) -> dict[str, dict]:
        """ONE batched query resolving external endpoint existence, label
        (node type) and status for a set of raw ids/urls (review fix: existence
        alone cannot distinguish, since extractedFrom legitimately targets
        Sources)."""
        if not values:
            return {}
        proj = self._get_proj()
        rows = proj.g.query(
            "MATCH (n) WHERE n.id IN $vals OR n.url IN $vals "
            "OR n.eventId IN $vals "
            "RETURN coalesce(n.id, n.url, n.eventId) AS key, "
            "head(labels(n)) AS label, n.is_operator, n.status, "
            "n.id, n.eventId",
            params={"vals": sorted(values)},
        ).result_set
        # Key the result so a lookup by the raw endpoint value finds the
        # node. The coalesce key covers the primary identifier; an Event may
        # additionally be addressed by its eventId (its other canonical key,
        # equal to id on SDK-created Events). A Point's provenance eventId
        # (#1417 capture_session stamp) is NEVER registered as a resolvable
        # endpoint key — a Point addressed by its eventId would pass the
        # Point branch of the endpoint checks (which has no id-guard), then
        # crash the id-keyed write primitives (create_operator /
        # create_direct_edge) mid-write: partial mutation. Rows sharing a
        # key resolve deterministically: an id-owner row (node_id == key)
        # wins over a row matched by another property.
        out: dict[str, dict] = {}
        for key, label, is_op, status, node_id, event_id in rows:
            entry = {"label": label, "is_operator": bool(is_op),
                     "status": status, "id": node_id}
            if key not in out or node_id == key:
                out[key] = entry
            if label == "Event" and event_id:
                out[event_id] = entry
        return out

    def _find_terminal_dedup_hit(self, content: str, kind: str) -> str | None:
        """Read-only NFC-keyed dedup MATCH (mirrors create_point's dedup key)
        restricted to TERMINAL hits — the Phase-1 mechanism behind the
        bundle-local-refs-resolving-to-terminal-points guard (cycle-17/18)."""
        proj = self._get_proj()
        rows = proj.g.query(
            "MATCH (n:Point {content_hash:$ch}) WHERE n.is_operator = false "
            "AND n.pointKind = $kind AND n.status IN $terminal RETURN n.id LIMIT 1",
            params={"ch": _content_hash(content), "kind": kind,
                    "terminal": sorted(self._INGEST_TERMINAL_STATUSES)},
        ).result_set
        return rows[0][0] if rows else None

    def _check_endpoints(self, bundle: dict, violations: list[dict]) -> None:
        """Check 3 (endpoint half) — typed endpoints (external + bundle-local,
        cycle-2) and the direct-edge terminal-status guard (cycle-3, refined
        cycle-17/18). Operator-ROUTED connections (reify:true / mitigation /
        part-whole → create_operator) accept Point OR Event endpoints (A1b
        #1272 parity — _find_operator's dedup key collects both); direct-edge
        connections (plain IMPL/NAND → create_direct_edge, which guards plain
        Points) require plain-Point endpoints AND additionally reject terminal
        (superseded/retracted) endpoints (#2062). Bundle-local refs resolving
        to EXISTING (dedup-hit) terminal points are Phase-1 violations (never
        a Phase-2 raise)."""
        local: dict[str, tuple[str, dict]] = {}
        for section in ("sources", "points", "entities"):
            for item in bundle.get(section) or []:
                if isinstance(item, dict) and item.get("ref"):
                    local[item["ref"]] = (section, item)
        external: set[str] = set()
        conns: list[tuple[int, dict, str, list]] = []
        for i, conn in enumerate(bundle.get("connections") or []):
            if not isinstance(conn, dict) or "operator" not in conn:
                continue
            frm = conn.get("from")
            tos = conn.get("to")
            to_list = tos if isinstance(tos, list) else [tos]
            route = self._connection_route(conn)
            for v in [frm] + to_list:  # noqa: RUF005
                if isinstance(v, str) and v not in local:
                    external.add(v)
            conns.append((i, conn, route, [frm] + to_list))  # noqa: RUF005
        node_info = self._fetch_endpoint_info(external)
        entity_labels = {"subject": "Subject", "object": "Object",
                         "event": "Event", "document": "Document"}
        for i, conn, route, vals in conns:  # noqa: B007
            # #2062: the operator route (reify/mitigation/part-whole →
            # create_operator) accepts Point OR Event endpoints — the direct
            # route (plain IMPL/NAND) stays plain-Point only. An Event is
            # accepted only when the endpoint addresses the node BY ITS id
            # (the create_operator write key): an Event that resolves only by
            # eventId (the ingest_corpus DocumentCreated shape) would pass
            # validation but crash create_operator mid-write — partial
            # mutation — so it stays a Phase-1 violation.
            op_route = route == "operator"
            for v in vals:
                if not isinstance(v, str):
                    continue
                if v in local:
                    section, item = local[v]
                    plain_point = section == "points" \
                        and not item.get("is_operator")
                    event_item = op_route and section == "entities" \
                        and str(item.get("type") or "").strip().lower() == "event"
                    if not (plain_point or event_item):
                        if section == "sources":
                            label = "Source"
                        elif section == "entities":
                            label = entity_labels.get(
                                str(item.get("type") or "").strip().lower(),
                                "entity")
                        else:
                            label = "operator-shaped point item"
                        violations.append({
                            "section": "connections", "index": i,
                            "message": f"ingest: connections[{i}] bundle-local "
                                       f"endpoint {v!r} must be a plain Point — "
                                       f"got a {label} item",
                        })
                else:
                    info = node_info.get(v)
                    if info is None:
                        violations.append({
                            "section": "connections", "index": i,
                            "message": f"ingest: connections[{i}] external "
                                       f"endpoint {v!r} does not exist",
                        })
                    elif info["is_operator"] or (info["label"] != "Point"
                            and not (op_route and info["label"] == "Event"
                                     and info.get("id") == v)):
                        got = ("operator Point" if info["is_operator"]
                               else info["label"] or "unknown node type")
                        violations.append({
                            "section": "connections", "index": i,
                            "message": f"ingest: connections[{i}] endpoint "
                                       f"{v!r} must be a plain Point — got a "
                                       f"{got} endpoint",
                        })
                    elif route == "direct" and info["status"] in self._INGEST_TERMINAL_STATUSES:
                        violations.append({
                            "section": "connections", "index": i,
                            "message": f"ingest: connections[{i}] endpoint "
                                       f"{v!r} is {info['status']} — new direct "
                                       f"edges to terminal points are rejected",
                        })
        # Bundle-local dedup-hit terminal guard (cycle-17/18) — Phase-1 ONLY.
        for i, conn, route, vals in conns:  # noqa: B007
            if route != "direct":
                continue
            for v in vals:
                if not isinstance(v, str) or v not in local:
                    continue
                section, item = local[v]
                if section != "points":
                    continue
                content = item.get("content")
                if content is None or not isinstance(content, str):
                    # REVIEW-FIX P1 (cycle-26): non-string content would crash
                    # _find_terminal_dedup_hit -> _content_hash (AttributeError
                    # 'int' object has no attribute 'encode') — shape violation
                    # already reported (check 1), skip the dedup-hit scan.
                    continue
                kind = item.get("kind") or "statement"
                hit = self._find_terminal_dedup_hit(content, kind)
                if hit:
                    violations.append({
                        "section": "connections", "index": i,
                        "message": f"ingest: connections[{i}] bundle-local "
                                   f"endpoint {v!r} resolves to terminal point "
                                   f"{hit!r} (dedup hit) — new direct edges to "
                                   f"terminal points are rejected",
                    })

    def _check_endpoint_race(self, index: int, conn: dict,
                             refs: dict[str, str], violations: list[dict]) -> None:
        """Phase-2 defense-in-depth (check 3): re-verify endpoints right
        before the connection write — a node deleted/superseded between
        Phase-1 validation and this write is the race class this exists for.
        Reuses the SAME message shapes as Phase 1 on the external-endpoint
        leg (bundle-local refs resolve to external ids in Phase-2; the
        bundle-local '{label} item' template is Phase-1-only). #2062: the
        operator route accepts Point OR Event endpoints when the endpoint
        addresses the node BY ITS id (matching Phase-1 and create_operator's
        A1b #1272 contract)."""
        if not isinstance(conn, dict) or "operator" not in conn:
            return
        route = self._connection_route(conn)
        op_route = route == "operator"
        frm = conn.get("from")
        tos = conn.get("to")
        vals = []
        for v in ([frm] + (tos if isinstance(tos, list) else [tos])):
            vals.append(refs.get(v, v) if isinstance(v, str) else v)
        external = {v for v in vals if isinstance(v, str)}
        info = self._fetch_endpoint_info(external)
        for v in vals:
            if not isinstance(v, str):
                continue
            info_i = info.get(v)
            if info_i is None:
                violations.append({
                    "section": "connections", "index": index,
                    "message": f"ingest: connections[{index}] external "
                               f"endpoint {v!r} does not exist",
                })
            elif info_i["is_operator"] or (info_i["label"] != "Point"
                    and not (op_route and info_i["label"] == "Event"
                             and info_i.get("id") == v)):
                got = ("operator Point" if info_i["is_operator"]
                       else info_i["label"] or "unknown node type")
                violations.append({
                    "section": "connections", "index": index,
                    "message": f"ingest: connections[{index}] endpoint "
                               f"{v!r} must be a plain Point — got a {got} "
                               f"endpoint",
                })
            elif route == "direct" and info_i["status"] in self._INGEST_TERMINAL_STATUSES:
                violations.append({
                    "section": "connections", "index": index,
                    "message": f"ingest: connections[{index}] endpoint "
                               f"{v!r} is {info_i['status']} — new direct edges "
                               f"to terminal points are rejected",
                })

    def _connections_conflict(self, ca: dict, cb: dict) -> bool:
        """§5.2.7 conflict predicate for a same from/to/operator pair.

        Conflicts: differing canonical direction (per op_type), differing
        confidence/weight/mitigation.strength; label-absent as a DISTINCT
        value (absent-vs-present → conflict); label-differing on the
        direct-edge path (label joins the predicate there); mitigation-reason
        conflict on same-label pairs. Identical pairs are clean dedup.
        """
        op = ca["operator"]
        if self._canonical_direction(op, ca.get("direction")) != \
                self._canonical_direction(op, cb.get("direction")):
            return True
        if ca.get("confidence") != cb.get("confidence"):
            return True
        if ca.get("weight") != cb.get("weight"):
            return True
        ma, mb = ca.get("mitigation"), cb.get("mitigation")
        sa = ma.get("strength") if isinstance(ma, dict) else None
        sb = mb.get("strength") if isinstance(mb, dict) else None
        if sa != sb:
            return True
        la, lb = ca.get("label"), cb.get("label")
        if la != lb:
            if la is None or lb is None:
                return True  # label-absent is a DISTINCT value (cycle-14)
            if self._connection_route(ca) == "direct" \
                    or self._connection_route(cb) == "direct":
                return True  # label joins the direct-edge predicate (cycle-8)
            # both operator-routed with differing labels → LEGAL (two
            # operators; _find_operator's key includes label).
        elif isinstance(ma, dict) and isinstance(mb, dict) and sa == sb \
                and ma.get("reason") != mb.get("reason"):
            return True  # mitigation-reason conflict, same-label pairs
        return False

    def _check_duplicates(self, bundle: dict, violations: list[dict]) -> None:
        """Check 7 — intra-bundle duplicate connections (§5.2.7).

        Identical duplicates (same from/to/operator with identical direction
        (canonicalized per op_type), confidence, weight, mitigation.strength,
        label) are clean dedup — NO violation. Conflicting duplicates are
        violations (fail-closed — _find_operator's key lacks strength, so
        acceptance would be order-dependent). Reversed-pair bidirectional
        contradictions (a→b AND b→a, IMPL/NAND) are violations.
        """
        ops = [(i, c) for i, c in enumerate(bundle.get("connections") or [])
               if isinstance(c, dict) and "operator" in c]
        for a in range(len(ops)):
            for b in range(a + 1, len(ops)):
                ia, ca = ops[a]
                ib, cb = ops[b]
                if (ca.get("from") != cb.get("from")
                        or ca.get("to") != cb.get("to")
                        or ca["operator"] != cb["operator"]):
                    continue
                if self._connections_conflict(ca, cb):
                    violations.append({
                        "section": "connections", "index": ib,
                        "message": f"ingest: connections[{ia}] and "
                                   f"connections[{ib}] are conflicting "
                                   f"duplicates — same from/to/operator with "
                                   f"differing direction/confidence/weight/"
                                   f"mitigation/label is ambiguous (identical "
                                   f"duplicates dedup cleanly)",
                    })
        for a in range(len(ops)):
            for b in range(a + 1, len(ops)):
                ia, ca = ops[a]
                ib, cb = ops[b]
                if ca["operator"] not in ("IMPL", "NAND") \
                        or cb["operator"] not in ("IMPL", "NAND"):
                    continue
                if ca.get("from") == cb.get("to") and ca.get("to") == cb.get("from"):
                    da = self._canonical_direction(ca["operator"], ca.get("direction"))
                    db = self._canonical_direction(cb["operator"], cb.get("direction"))
                    if da == "bidirectional" or db == "bidirectional":
                        violations.append({
                            "section": "connections", "index": ib,
                            "message": f"ingest: connections[{ia}] and "
                                       f"connections[{ib}] are reversed-pair "
                                       f"contradictions — a bidirectional edge "
                                       f"already covers both directions",
                        })

    def _check_gated_status(self, bundle: dict, promotion_policy: str,
                            violations: list[dict]) -> None:
        """Check 5 (RETAINED piece, CYCLE-26) — under gated, an explicit
        status:'live' on a point item is a violation (no bypass of the gated
        contract; the sanctioned routes are promotion_policy='auto' or
        update_point(status='live') after ingest)."""
        if promotion_policy != "gated":
            return
        # CYCLE-26 merge resolution: match the SHIPPED A0 semantics (PR #1073
        # _first_non_draft_status) — ANY status other than the exact canonical
        # "draft" on a point item is a violation (no bypass; status:'draft'
        # accepted as a no-op). Phase-1/2 parity via the shared helper.
        for i, item in enumerate(bundle.get("points") or []):
            if not isinstance(item, dict):
                continue
            # top-level status wins on conflict; nested props flattened by
            # _coerce_props — mirror _first_non_draft_status (PR #1073).
            st = item.get("status")
            has_status = "status" in item
            if not has_status and isinstance(item.get("props"), dict):
                st = item["props"].get("status")
                has_status = "status" in item["props"]
            # An explicit status key with value None is a violation too
            # (None stores NULL, which _live_only treats as LIVE) — the
            # explicit-None and non-draft cases both violate row 9.
            if has_status and (st is None or st != "draft"):
                violations.append({
                    "section": "points", "index": i,
                    "message": f"ingest: points[{i}] status:{st!r} "
                               f"is not allowed under promotion_policy 'gated' "
                               f"— under gated points stay draft; pass "
                               f"promotion_policy='auto' for explicit live, or "
                               f"keep draft and promote via "
                               f"update_point(status='live')",
                })

    def _validate_bundle(self, bundle: dict, *,
                         promotion_policy: str = "gated") -> list[dict]:
        """Phase-1 validation (epic #902 A1 — plan §5.2 checks 1-8).

        Pure and zero-mutation: walks ALL sections and returns EVERY violation
        as {section, index, message} (aggregated, never fail-fast).
        Mode-invariant — runs identically for granularity='bulk' and
        'granular'. Consumes the same shared check helpers as ingest()'s
        Phase-2 defense-in-depth raises, so Phase 1 catches every violation
        class Phase 2 can raise (asserted by the parity unit test).
        """
        violations: list[dict] = []
        for section in ("sources", "points", "entities"):
            for i, item in enumerate(bundle.get(section) or []):
                self._check_item_shape(section, i, item, violations)
                if section == "points" and isinstance(item, dict):
                    self._check_kind(item.get("kind"), i, violations)
        for i, conn in enumerate(bundle.get("connections") or []):
            self._check_item_shape("connections", i, conn, violations)
            self._check_connection(i, conn, violations)
        self._check_refs(bundle, violations)
        self._check_endpoints(bundle, violations)
        self._check_duplicates(bundle, violations)
        self._check_gated_status(bundle, promotion_policy, violations)
        return violations

    def ingest(self, bundle: dict, granularity: str = "bulk", *,
               promotion_policy: str = "gated") -> dict:
        """Heterogeneous bulk write (epic #888 W4, design ref PR #912).

        One call writes points + entities + sources + connections coherently:
        all nodes first, then the connections between them. Indexing many
        interconnected items is ONE operation, not N.

        bundle = {
          points:      [{kind, content, ref?, status?, **props}],
          entities:    [{type: subject|object|event|document, name, ref?, **props}],
          sources:     [{url, sourceKind, tier?, sourceDate?, ref?, **props}],
          connections: [{from, to, relation, ...} | {from, to, operator, label?, direction?}],
        }

        - ``ref``: optional local addressing label usable in any connection's
          from/to (and in entity authoredBy/ownedBy/managedBy + about* props,
          and point extractedFrom) instead of the created id/url. Must be
          unique within the bundle. Never stored as a node property.
        - Connections resolve from/to by local ref first, then pass through as
          raw ids/urls to the underlying primitive (create_operator /
          create_edge / _link_source).
        - Reification rule (ontology v3.5 §8, updated for A3 §8 routing #1256):
          a connection carrying ``operator`` reifies to an operator Point ONLY
          when it carries a reification anchor (``reify: true`` or
          ``mitigation``); a PLAIN IMPL/NAND ``operator`` connection is
          operator-less — routed to a DIRECT edge (no operator node, see
          INGEST_CONTRACT.md §8). A connection carrying ``relation`` stays a
          PLAIN structural edge (structural edges never reify).
        - Endpoint typing (#2062): operator-ROUTED connections (reify:
          true/mitigation/part-whole → create_operator) accept Point OR Event
          endpoints (A1b #1272 parity — the dedup key in _find_operator
          collects both); bundle-local refs may address point items or
          ``type:"event"`` entity items. An external Event endpoint must
          address the node BY ITS id (the create_operator write key — an
          Event that resolves only by eventId, e.g. the ingest_corpus
          DocumentCreated shape, is rejected at validation, never a mid-write
          crash). NOTE: Event entity items are append-only — re-ingesting a
          FULL bundle mints a new Event occurrence (new id) and thus a new
          operator; dedup applies to id-stable external Event endpoints (or
          re-ingesting connection-only against the resolved id). DIRECT-edge
          connections (plain IMPL/NAND → create_direct_edge, which guards
          plain Points) require plain-Point endpoints only.
        - granularity='bulk' (default): whole bundle in one coherent pass,
          returns aggregated {created, ids, nudges}. granularity='granular':
          additionally returns per-item ``results`` for agent step-by-step
          control (each item's primitive result + deduped flag).
        - promotion_policy: "gated" (DEFAULT, Q2) — points stay draft;
          connections never promote (operator path: promote_source=False via
          #780 — the operator node is created draft and the source is NOT
          auto-promoted). "auto" — #131 parity preserved: a source point
          promotes on wire when its FIRST operator edge is created
          (CYCLE-26 REVIEW FIX P2: NOT retroactive — re-ingest dedup of an
          existing operator does not retro-promote a previously-gated
          source). ORTHOGONAL to granularity: both modes honor the same
          policy.
        - Idempotent-ish: points dedup by (content_hash, pointKind) via
          create_point(dedup=True); sources merge by url; Subject/Object
          merge by name; operator connections dedup by (op_type, input set);
          structural edges MERGE. Document/Event entities are append-only
          occurrence records — re-ingest duplicates them by design.
        - EP-safe: created points default to status='draft' (#131 draft→live
          lifecycle). Under promotion_policy='gated' ANY effective status other
          than 'draft' on a point item is rejected (INGEST CONTRACT row 9 —
          no bypass of the gated contract; the sanctioned routes are
          promotion_policy='auto' or update_point(status='live') after ingest;
          case variants, nested props={...}, and canonical terminal statuses
          are rejected too — EP _live_only excludes only exact 'draft').
          Connection-driven promotion (source → live on first edge) only
          happens under promotion_policy='auto', and only for draft/null-status sources
          (retracted/deprecated terminal sources are never resurrected).
          Under auto the operator node is written WITHOUT a status property
          (live by projection — the #780 asymmetry: gated writes explicit
          draft on the operator, auto writes none).

        Returns {granularity, batch_id, created: {points, entities, sources,
        connections}, deduped: {...}, ids: {points, entities, sources,
        connections, refs}, nudges: [...], warnings: [...]} (+ results for
        granularity='granular'). The key set is the canonical enumeration
        (INGEST_CONTRACT §2.2 — a docs/behavior conformance test pins it).
        """
        if granularity not in INGEST_GRANULARITIES:
            raise ValueError(
                f"ingest: granularity must be 'bulk' or 'granular', got {granularity!r}"
            )
        if promotion_policy not in INGEST_PROMOTION_POLICIES:
            raise ValueError(
                f"ingest: promotion_policy must be 'gated' or 'auto', got {promotion_policy!r}"
            )
        if not isinstance(bundle, dict):
            raise ValueError(
                f"ingest: bundle must be a dict with points/entities/sources/"
                f"connections sections, got {type(bundle).__name__}"
            )
        # Row 9 of INGEST_CONTRACT.md: under gated, an explicit status:'live'
        # on a point item is a violation — the Q2-lock must not be bypassable
        # via the bundle's own status field (the sanctioned routes are
        # promotion_policy='auto' or update_point(status='live') after ingest).
        # This is check 5's RETAINED piece (CYCLE-26) — enforced by the shared
        # _check_gated_status helper below (see _validate_bundle).

        # ── Phase 1 — shared check helpers (plan §5.2 checks 1-8): collect
        # ALL violations with ZERO mutation before any write. Fail-fast raise
        # carries the first violation's message (the shipped message contract);
        # the A2 failure contract upgrades this to BundleValidationError with
        # ALL violations (.violations, .as_dict()).
        violations = self._validate_bundle(bundle, promotion_policy=promotion_policy)
        if violations:
            # A2 failure contract: BundleValidationError carries ALL
            # violations (str() = first message for back-compat parity);
            # _safe maps it to {error, code: ERR_BUNDLE_INVALID, violations}.
            from .exceptions import BundleValidationError
            raise BundleValidationError(violations)

        proj = self._get_proj()
        # §4.2 (A4): deterministic content-derived batch_id over the canonical
        # serialization of the RESOLVED bundle (refs expanded, NFC-normalized,
        # int/float-collapsed, item/connection order normalized) — computed ONCE
        # here, stamped on every NEW point the commit creates and applied
        # stamp-when-absent on dedup hits. Clock-independent by construction
        # (no time component — CYCLE-21 pin).
        from .exceptions import Phase2Error
        batch_id = derive_batch_id(bundle)
        refs: dict[str, str] = {}          # ref → canonical id (or url for sources)
        source_refs: set[str] = set()      # refs that address Source nodes (url-keyed)
        ids = {"points": [], "entities": [], "sources": [],
               "connections": [], "refs": {}}
        created = {"points": 0, "entities": 0, "sources": 0, "connections": 0}
        deduped = {"points": 0, "entities": 0, "sources": 0, "connections": 0}
        results = [] if granularity == "granular" else None
        # A3 warnings contract (cycle-23): the ELEVEN-key enumeration lives in
        # E2E-6.2; this accumulator carries the operator-absorb / residue /
        # drift warnings appended during the connection loop.
        warnings: list[str] = []
        self._ingest_warnings = warnings

        def _register_ref(ref: str, cid: str, section: str) -> None:
            if ref in refs:
                # REVIEW-FIX P2 (cycle-26): the Phase2Error must carry the
                # computed batch_id (plan §6.4 — the agent audits the partial
                # commit) AND the message must interpolate it (the literal
                # "batch_id=batch_id" text was a bug).
                raise Phase2Error(
                    f"ingest: duplicate bundle ref {ref!r} "
                    f"({section}, batch_id={batch_id}) — refs must be unique "
                    f"across the bundle",
                    batch_id=batch_id,
                )
            refs[ref] = cid
            ids["refs"][ref] = cid

        # ── Pre-scan refs: source refs resolve to their url (the canonical
        # Source key, known BEFORE creation); point/entity refs resolve to ids
        # registered as nodes are created.
        for item in bundle.get("sources") or []:
            ref = item.get("ref") if isinstance(item, dict) else None
            if ref:
                if ref in refs:
                    raise Phase2Error(
                        f"ingest: duplicate bundle ref {ref!r} "
                        f"(sources, batch_id={batch_id})",
                        batch_id=batch_id,
                    )
                refs[ref] = item.get("url", "")
                ids["refs"][ref] = item.get("url", "")
                source_refs.add(ref)

        # ── 1. Sources (first: points may reference them via extractedFrom) ──
        # Phase-2 defense-in-depth: the shared shape helper re-checks the raw
        # item right before the write (the same helper Phase 1 ran — parity).
        for i, item in enumerate(bundle.get("sources") or []):
            viols: list[dict] = []
            self._check_item_shape("sources", i, item, viols)
            if viols:
                raise Phase2Error(viols[0]["message"], batch_id=batch_id)
            item = dict(item)
            ref = item.pop("ref", None)
            url = item.pop("url", None)
            source_kind = item.pop("sourceKind", None)
            existed = proj.g.query(
                "MATCH (s:Source {url:$url}) RETURN count(s)",
                params={"url": url},
            ).result_set
            node = self.create_source(url, source_kind, **item)
            canonical = node.get("id") or node.get("url") or url
            if ref:
                # Pre-registered in the ref pre-scan (url known upfront) —
                # refresh the canonical value (id may differ from url).
                refs[ref] = canonical
                ids["refs"][ref] = canonical
                source_refs.add(ref)
            ids["sources"].append(canonical)
            if existed and existed[0][0]:
                deduped["sources"] += 1
            else:
                created["sources"] += 1
            if results is not None:
                results.append({"section": "sources", "index": i, "ref": ref,
                                "item": item, "result": node,
                                "deduped": bool(existed and existed[0][0])})

        # ── 2. Points (default status='draft', #131) ────────────────────
        for i, item in enumerate(bundle.get("points") or []):
            viols = []
            self._check_item_shape("points", i, item, viols)
            if viols:
                raise Phase2Error(viols[0]["message"], batch_id=batch_id)
            item = dict(item)
            ref = item.pop("ref", None)
            # CYCLE-25: kind-absent DEFAULTS to 'statement' (v3.8 canonical —
            # the extraction write kind). Legacy kinds are write-compat; the
            # event kind is rejected by the shared _check_kind helper (check 2).
            kind = item.pop("kind", None) or "statement"
            self._check_kind(kind, i, viols)
            if viols:
                raise Phase2Error(viols[0]["message"], batch_id=batch_id)
            content = item.pop("content", None)
            # extractedFrom may address a bundle source by its local ref
            if isinstance(item.get("extractedFrom"), str) \
                    and item["extractedFrom"] in source_refs:
                item["extractedFrom"] = refs[item["extractedFrom"]]
            existed = proj.g.query(
                "MATCH (n:Point {content_hash:$ch}) "
                "WHERE n.is_operator = false "
                "AND n.pointKind = $kind RETURN n.id",
                params={"ch": _content_hash(content), "kind": kind},
            ).result_set
            # A10 CONTENT+KIND fallback (cycle-17/18): mirror create_point's
            # hash-less sibling detection so the counter is honest (a
            # fallback hit counts as deduped, never created).
            if not existed:
                fallback = proj.g.query(
                    "MATCH (n:Point) "
                    "WHERE n.is_operator = false "
                    "AND n.pointKind = $kind "
                    "AND n.content_hash IS NULL "
                    "AND n.content = $content "
                    "RETURN n.id",
                    params={"kind": kind, "content": content},
                ).result_set
                if fallback:
                    existed = fallback
            point = self.create_point(kind, content, dedup=True, **item)
            pid = point["id"]
            if ref:
                _register_ref(ref, pid, "points")
            ids["points"].append(pid)
            # §4.2 (A4): stamp the bundle's batch_id on EVERY point — created
            # points get the full stamp; dedup hits get stamp-when-absent
            # (a batch-less pre-existing point ACQUIRES the bundle's batch_id on
            # its first dedup-hit — E2E-10 row 14 — and a crash between
            # create_point and the stamp, position (f), is completed on retry).
            # _stamp_batch_id itself decides: full stamp vs (h) record-repair
            # vs no-op (a point already stamped with a DIFFERENT batch_id keeps
            # it — dedup never rewrites provenance).
            self._stamp_batch_id(pid, batch_id, content_hash=_content_hash(content))
            if existed:
                deduped["points"] += 1
            else:
                created["points"] += 1
            if results is not None:
                results.append({"section": "points", "index": i, "ref": ref,
                                "item": item, "result": point,
                                "deduped": bool(existed)})

        # ── 3. Entities (subject/object merge by name; event/document append) ─
        for i, item in enumerate(bundle.get("entities") or []):
            viols = []
            self._check_item_shape("entities", i, item, viols)
            if viols:
                raise Phase2Error(viols[0]["message"], batch_id=batch_id)
            item = dict(item)
            ref = item.pop("ref", None)
            etype = (item.pop("type", None) or "").strip().lower()
            name = item.pop("name", None)
            # Entity props that wire edges may address earlier bundle items
            # by local ref (points + entities are registered by now).
            for key in ("authoredBy", "ownedBy", "managedBy",
                        "aboutSubject", "aboutObject", "aboutPoint",
                        "aboutDocument"):
                if isinstance(item.get(key), str) and item[key] in refs:
                    item[key] = refs[item[key]]
            if etype == "subject":
                existed = proj.g.query(
                    "MATCH (n:Subject {name:$name}) RETURN n.id",
                    params={"name": name},
                ).result_set
                node = self.create_subject(name, **item)
                canonical = node.get("id") or name
            elif etype == "object":
                existed = proj.g.query(
                    "MATCH (n:Object {name:$name}) RETURN n.id",
                    params={"name": name},
                ).result_set
                node = self.create_object(name, **item)
                canonical = node.get("id") or name
            elif etype == "event":
                event_kind = item.pop("eventKind", None)
                node = self.create_event(name, event_kind, **item)
                canonical = node.get("eventId") or node.get("id") or name
                existed = []  # Event records are append-only — never deduped
            elif etype == "document":
                doc_kind = item.pop("documentKind", None)
                node = self.create_document(name, doc_kind, **item)
                canonical = node.get("id") or name
                existed = []  # Document records are append-only — never deduped
            else:
                raise Phase2Error(
                    f"ingest: entities[{i}] type must be subject|object|event|"
                    f"document, got {etype!r}"
                , batch_id=batch_id)
            if ref:
                _register_ref(ref, canonical, "entities")
            ids["entities"].append(canonical)
            if existed:
                deduped["entities"] += 1
            else:
                created["entities"] += 1
            if results is not None:
                results.append({"section": "entities", "index": i, "ref": ref,
                                "item": item, "result": node,
                                "deduped": bool(existed)})

        # ── 4. Connections (nodes exist — resolve refs, apply reification) ──
        # Phase-2 defense-in-depth: the shared contract helper + a live
        # endpoint re-verify run right before each write — a node deleted/
        # superseded between Phase-1 validation and this write is the race
        # class the re-check exists for (same helpers, same messages — parity).
        for i, conn in enumerate(bundle.get("connections") or []):
            viols = []
            self._check_item_shape("connections", i, conn, viols)
            self._check_connection(i, conn, viols)
            self._check_endpoint_race(i, conn, refs, viols)
            if viols:
                raise Phase2Error(viols[0]["message"], batch_id=batch_id)
            src = refs.get(conn["from"], conn["from"])
            to_list = conn["to"] if isinstance(conn["to"], list) else [conn["to"]]
            dsts = [refs.get(x, x) for x in to_list]
            if "operator" in conn:
                op_type = conn["operator"]
                label = conn.get("label")
                direction = conn.get("direction")
                # §8 connection-routing (A3, #1053): a PLAIN IMPL/NAND
                # connection (route 'direct' — no reify/mitigation) is
                # operator-less per §8 — route to create_direct_edge, carrying
                # label/direction/batch_id on the EDGE (no operator node). The
                # promotion SET fires only on created==True (the promotion-on-
                # created-only pin, §5.3/CYCLE-24) — the bare-MERGE dedup hit
                # applies NO promotion.
                if self._connection_route(conn) == "direct":
                    edge_res = self.create_direct_edge(
                        op_type, src, dsts[0],
                        label=label,
                        direction=direction,
                        batch_id=batch_id,
                        promote_source=(promotion_policy == "auto"),
                    )
                    created_flag = bool(edge_res.get("created"))
                    if created_flag:
                        created["connections"] += 1
                    else:
                        deduped["connections"] += 1
                    conn_result = {"direct_edge": op_type, "from": src,
                                   "to": dsts[0], "deduped": not created_flag}
                    # §6.1 descriptor: the ids["connections"] entry is the
                    # STABLE shape (no deduped — aggregated in
                    # deduped.connections) so identical-resubmission equality
                    # holds; deduped lives only in the granular conn_result
                    # (plan §5.5 route matrix, cycle-22).
                    ids["connections"].append(
                        {"direct_edge": op_type, "from": src, "to": dsts[0]})
                    if results is not None:
                        results.append({"section": "connections", "index": i,
                                        "ref": conn.get("ref"), "item": conn,
                                        "result": conn_result,
                                        "deduped": bool(conn_result.get("deduped"))})
                    continue
                existing = self._find_operator(op_type, [src] + dsts,  # noqa: RUF005
                                               label=label, direction=direction)
                if existing is not None and existing.get("kind") == "exact":
                    oid = existing["id"]
                    # k=N boundary (cycle-15): re-apply promotion when the
                    # exact-hit is NULL-status under auto (a crash after the
                    # full input-edge loop, before the promotion SET).
                    if (promotion_policy == "auto"
                            and existing.get("status") is None):
                        try:  # noqa: SIM105
                            self._reapply_operator_promotion(oid)
                        except Exception:  # noqa: BLE001, RUF100
                            pass
                    deduped["connections"] += 1
                    conn_result = {"operator_id": oid, "deduped": True}
                elif existing is not None and existing.get("kind") == "partial":
                    # partial-absorb (cycle-11): complete the written input
                    # set to the full set via IDEMPOTENT bare-MERGE + SET per
                    # input (never the raw-CREATE loop), then proceed to
                    # promotion under auto.
                    oid = existing["id"]
                    written = set(existing.get("written") or [])
                    for _i, _inp in enumerate([src] + dsts):  # noqa: RUF005
                        if _inp in written:
                            continue
                        # IDEMPOTENT edge-completion (P0 fix, review gate): the
                        # ONE-STEP property-filtered MERGE
                        #   MERGE (o:Point {id:$oid})-[r:REL]->(t:Point {id:$inp})
                        # CREATES DUPLICATE NODES when the edge is absent (the
                        # FalkorDB quirk create_direct_edge's cycle-26 comment
                        # documents) — the completion would land on a fresh
                        # bare Point instead of the operator. Use the TWO-STEP
                        # pattern: MATCH both endpoints, then edge-only MERGE
                        # (with idx parity for create_operator's edge shape).
                        # #1919 (P1, review gate): the completion endpoints are
                        # Point OR Event (A1b #1272) — a Point-only MATCH would
                        # silently drop the typed edge for an absorbed Event
                        # input (never converging to the dedup key). The
                        # reverse INPUT edge mirrors create_operator + the
                        # replay convention (fold-parity).
                        proj.g.query(
                            f"MATCH (o:Point {{id:$oid}}), (t) "
                            f"WHERE (t:Point OR t:Event) AND t.id = $inp "
                            f"MERGE (o)-[r:{op_type if op_type in ('IMPL','NAND') else 'hasPart'}]->(t) "
                            f"SET r.idx = $idx "
                            f"MERGE (t)-[:INPUT {{idx:$idx}}]->(o)",
                            params={"oid": oid, "inp": _inp, "idx": _i},
                        )
                    completed = len([src] + dsts) - len(written)  # noqa: RUF005
                    if promotion_policy == "auto" and existing.get("status") is None:
                        try:  # noqa: SIM105
                            self._reapply_operator_promotion(oid)
                        except Exception:  # noqa: BLE001, RUF100
                            pass
                    deduped["connections"] += 1
                    conn_result = {"operator_id": oid, "deduped": True}
                    self._append_warning(
                        f"operator_absorb_completed:{oid}:{completed}")
                elif existing is not None and existing.get("kind") == "decline":
                    # TWO+ candidates → decline + partial_operator_residue
                    # warning; the retry creates a fresh complete operator.
                    self._append_warning(
                        "partial_operator_residue:"
                        + ",".join(existing.get("candidates") or []))
                    op = self.create_operator(op_type, src, dsts,
                                              label=label, direction=direction,
                                              promote_source=(promotion_policy == "auto"))
                    oid = op["id"]
                    created["connections"] += 1
                    conn_result = {"operator_id": oid, "deduped": False}
                else:
                    op = self.create_operator(op_type, src, dsts,
                                              label=label, direction=direction,
                                              promote_source=(promotion_policy == "auto"))
                    oid = op["id"]
                    created["connections"] += 1
                    conn_result = {"operator_id": oid, "deduped": False}
                # §4.2 (A4): operator Points are stamped POST-WRITE keyed on the
                # returned id (create_operator accepts no props). Stamp-when-
                # absent covers the operator-path crash boundary too (position
                # (g)): a retry's _find_operator dedup hit on a batch-less
                # operator Point acquires the bundle's batch_id.
                self._stamp_batch_id(oid, batch_id)
                ids["connections"].append(oid)
            else:
                rel = conn["relation"]
                if rel == "extractedFrom":
                    # (Point)-[:extractedFrom]->(Source) — MERGE-based, so
                    # re-ingest is safe. Source side resolves by url/ref.
                    existed = proj.g.query(
                        "MATCH (n:Point {id:$pid})-[:extractedFrom]->"
                        "(s:Source {url:$url}) RETURN count(*)",
                        params={"pid": src, "url": dsts[0]},
                    ).result_set
                    if not existed or not existed[0][0]:
                        proj._link_source(src, dsts[0])
                        created["connections"] += 1
                    else:
                        deduped["connections"] += 1
                    conn_result = {"relation": rel, "from": src, "to": dsts[0],
                                   "deduped": bool(existed and existed[0][0])}
                else:
                    existed = proj.g.query(
                        f"MATCH (a)-[r:{rel}]->(b) "
                        "WHERE (a.id = $f OR a.eventId = $f OR a.url = $f) "
                        "AND (b.id = $t OR b.eventId = $t OR b.url = $t) "
                        "RETURN count(r)",
                        params={"f": src, "t": dsts[0]},
                    ).result_set
                    ok = proj.create_edge(src, dsts[0], rel)
                    if not ok:
                        raise Phase2Error(
                            f"ingest: connections[{i}] could not create "
                            f"{rel!r} edge — endpoints not found"
                        , batch_id=batch_id)
                    if existed and existed[0][0]:
                        deduped["connections"] += 1
                    else:
                        created["connections"] += 1
                    conn_result = {"relation": rel, "from": src, "to": dsts[0],
                                   "deduped": bool(existed and existed[0][0])}
                ids["connections"].append(conn_result)
            if results is not None:
                results.append({"section": "connections", "index": i,
                                "ref": conn.get("ref"), "item": conn,
                                "result": conn_result,
                                "deduped": bool(conn_result.get("deduped"))})

        # ── Nudges (write nudges, PR #912): populated once W2's
        # _nudge_candidates lands — advisory only, never enforced.
        nudges: list[dict] = []
        if hasattr(self, "_nudge_candidates"):
            try:
                for pid in ids["points"]:
                    nudges.extend(self._nudge_candidates(
                        "related", exclude_ids=[pid])[:2])
            except Exception:
                pass  # nudges are advisory — never fail the ingest

        out = {
            "granularity": granularity,
            "batch_id": batch_id,
            "created": created,
            "deduped": deduped,
            "ids": ids,
            "nudges": nudges,
            "warnings": warnings,
        }
        if results is not None:
            out["results"] = results
        self._ingest_warnings = None
        return out

    # ── batch_id stamping (epic #902 A4, §4.2) ────────────────────────

    def _stamp_batch_id(self, point_id: str, batch_id: str, *,
                        content_hash: str | None = None,
                        mitigates_operator_id: str | None = None,
                        mitigation_strength: float | None = None) -> bool:
        """Stamp ``batch_id`` onto a Point — single SET + JSONL-only record.

        §4.2 (A4): ``batch_id`` is SERVER-MANAGED. The stamp is two writes:
        (1) one prop SET (no GraphEvent — ``BatchIdStamped`` is NOT a
        ``_GRAPH_EVENT_TYPES`` member), (2) a JSONL-only batch_id record
        ``{id, batch_id, content_hash?, mitigates_operator_id?,
        mitigation_strength?}`` for rebuild durability — the PointAdded
        snapshot predates the stamp, so without the record a rebuild loses
        every bundle-created point's batch_id (A10 pass-2b replays it).

        Stamp-when-absent (cycle-2/3/4 pins):
        - prop MISSING → full stamp (SET + record) — completes an interrupted
          create→stamp write (crash positions (f)/(g)) and lets a batch-less
          pre-existing dedup hit ACQUIRE the bundle's batch_id on its first
          dedup-hit (E2E-10 row 14 — there is no implementable discriminator
          between a crash sibling and a pre-#902 point);
        - prop PRESENT and equal → the JSONL record is checked; a missing
          record is re-emitted ONLY (crash sub-position (h): the SET landed
          but the record did not — completing the interrupted record, NOT
          rewriting provenance);
        - prop PRESENT and different → no-op (dedup never rewrites
          provenance — a re-ingest must not reparent an existing point).

        Returns True when the stamp changed state (SET or record emitted),
        False when it was a no-op.
        """
        proj = self._get_proj()
        rows = proj.g.query(
            "MATCH (n:Point {id:$id}) RETURN n.batch_id",
            params={"id": point_id},
        ).result_set
        if not rows:
            return False  # point does not exist — nothing to stamp
        existing = rows[0][0]
        if existing is not None and existing != batch_id:
            return False  # dedup hit keeps its original batch_id
        if existing is None:
            proj.g.query(
                "MATCH (n:Point {id:$id}) SET n.batch_id = $bid",
                params={"id": point_id, "bid": batch_id},
            )
            self._emit_batch_id_record(
                point_id, batch_id,
                content_hash=content_hash,
                mitigates_operator_id=mitigates_operator_id,
                mitigation_strength=mitigation_strength,
            )
            return True
        # prop present and equal — (h) record-repair: re-emit ONLY the record
        # when it is missing (the SET survived a crash the record did not).
        if self._batch_id_record_missing(point_id, batch_id):
            self._emit_batch_id_record(
                point_id, batch_id,
                content_hash=content_hash,
                mitigates_operator_id=mitigates_operator_id,
                mitigation_strength=mitigation_strength,
            )
            return True
        return False

    def _emit_batch_id_record(self, point_id: str, batch_id: str, *,
                              content_hash: str | None = None,
                              mitigates_operator_id: str | None = None,
                              mitigation_strength: float | None = None) -> None:
        """Emit the JSONL-only batch_id record via ``_emit_event``'s JSONL branch.

        The record type is NOT in ``_GRAPH_EVENT_TYPES`` — no GraphEvent-store
        write (Q4 stays deferred). Optional fields are omitted when None so
        A10's pass-2b can distinguish absent from explicit-null.
        """
        extra: dict = {"batch_id": batch_id}
        if content_hash is not None:
            extra["content_hash"] = content_hash
        if mitigates_operator_id is not None:
            extra["mitigates_operator_id"] = mitigates_operator_id
        if mitigation_strength is not None:
            extra["mitigation_strength"] = mitigation_strength
        self._emit_event(_BATCH_ID_RECORD_TYPE, id=point_id, **extra)

    def _batch_id_record_missing(self, point_id: str, batch_id: str) -> bool:
        """True when no ``BatchIdStamped`` JSONL record exists for the pair.

        Best-effort — a log-read failure is logged and treated as "not
        missing" (the graph mutation already succeeded; a log glitch must not
        crash the stamp or spin the repair).
        """
        log = self._get_event_log()
        if log is None:
            return False  # no log configured — no record was or will be written
        try:
            for event in log.read_all():
                if (event.get("type") == _BATCH_ID_RECORD_TYPE
                        and event.get("id") == point_id
                        and event.get("batch_id") == batch_id):
                    return False
        except Exception:  # noqa: BLE001, RUF100
            _logger.warning(
                "batch_id record check failed for %s — treating as present",
                point_id, exc_info=True,
            )
            return False
        return True

    # ── batch audit surface (epic #902 A13, §4.2 audit row) ──────────

    def list_batch(self, batch_id: str) -> dict:
        """Audit the stamped artifacts of ONE bundle (epic #902 A13).

        Returns the bundle's STAMPED set — every Point carrying this
        ``batch_id`` (created OR ADOPTED via dedup — E2E-10 row 14: a
        batch-less pre-existing point acquires the bundle's batch_id on its
        first dedup hit) and every operator-less direct edge carrying it.
        Operator/mitigation Points are ordinary Points (stamped the same
        way, §4.2). Entities and sources are OUT of stamp scope on the
        INGEST path (bundle entities/sources are never stamped — a bundle
        item carrying ``batch_id`` is rejected at Phase-1; the raw-SDK
        props-passthrough surface can still stamp an entity via
        ``create_subject(batch_id=...)`` — a DOCUMENTED interim gap until
        #785 adopts the shared ``_sanitize_props`` batch_id rejection, the
        A4-gated final sub-step). Editorial supersede artifacts are outside
        audit: the superseding (editorial) point is not stamped with the
        originating bundle's batch_id, and repointed edges KEEP their
        originating batch_id (E2E-11.6).

        Completeness across ``rebuild_all``: batch_id on Points is restored
        from the pre-wipe live-graph snapshot (projection pass-1b tail) —
        a rebuild of the SAME store keeps the POINT half of the audit
        intact; DIRECT EDGES are lost even on same-store rebuilds today
        (their ``DirectEdgeCreated`` descriptors are journaled but not
        replayed). The JSONL journal replay of ``BatchIdStamped`` /
        ``DirectEdgeCreated`` records (a fresh-store rebuild from the
        journal only) is A10 pass-2b (#1048, gated on A3) — NOT yet
        landed, so journal-only rebuilds lose batch_id today (recorded
        deferral).

        Returns ``{"batch_id", "points": [{id, pointKind, status,
        is_operator, op_type, content}], "direct_edges": [{direct_edge,
        from, to, direction, confidence, weight, label}], "counts":
        {"points": n, "direct_edges": m}}``.
        """
        if not isinstance(batch_id, str) or not batch_id:
            raise ValueError("list_batch: batch_id must be a non-empty string")
        proj = self._get_proj()
        point_rows = proj.g.query(
            "MATCH (n:Point {batch_id:$bid}) "
            "RETURN n.id, n.pointKind, n.status, "
            "       (n.is_operator = true), n.op_type, n.content "
            "ORDER BY n.id",
            params={"bid": batch_id},
        ).result_set
        edge_rows = proj.g.query(
            "MATCH (a:Point)-[r:IMPL|NAND]->(b:Point) "
            "WHERE r.batch_id = $bid "
            "AND coalesce(a.is_operator, false) = false "
            "AND a.op_type IS NULL "
            "AND coalesce(b.is_operator, false) = false "
            "AND b.op_type IS NULL "
            "RETURN type(r), a.id, b.id, "
            "       coalesce(r.direction, 'bidirectional'), "
            "       r.confidence, r.weight, r.label "
            "ORDER BY a.id",
            params={"bid": batch_id},
        ).result_set
        points = [{"id": r[0], "pointKind": r[1], "status": r[2],
                   "is_operator": bool(r[3]), "op_type": r[4],
                   "content": r[5]} for r in point_rows]
        edges = [{"direct_edge": r[0], "from": r[1], "to": r[2],
                  "direction": r[3], "confidence": r[4], "weight": r[5],
                  "label": r[6]} for r in edge_rows]
        return {"batch_id": batch_id, "points": points,
                "direct_edges": edges,
                "counts": {"points": len(points),
                            "direct_edges": len(edges)}}

    def list_batches(self, limit: int = 20) -> list[dict]:
        """Batch discovery — the most recent distinct batch_ids (A13).

        Returns ``[{batch_id, points, direct_edges}]`` ordered by the newest
        stamped point's ``createdAt`` descending (a batch whose ONLY stamped
        artifacts are direct edges sorts by the edge's newest timestamp),
        capped at ``limit`` (default 20). Entities/sources are out of stamp
        scope (never counted). The ``points``/``direct_edges`` counts are the
        aggregate counts per batch (not the full rows — use ``list_batch``
        for the audit detail). Edge-only batches ARE discoverable (the
        transport-death recovery path must not lose them).
        """
        if not isinstance(limit, int) or limit < 1:
            raise ValueError("list_batches: limit must be a positive int")
        proj = self._get_proj()
        # point-stamped batches: (bid, newest point createdAt, point count)
        rows = proj.g.query(
            "MATCH (n:Point) WHERE n.batch_id IS NOT NULL "
            "WITH n.batch_id AS bid, max(n.createdAt) AS newest, count(n) AS c "
            "RETURN bid, newest, c",
        ).result_set
        # edge-stamped batches: edge-only batches (endpoints carry no
        # batch_id) must ALSO be discoverable — the transport-death recovery
        # path must not lose them
        edge_rows = proj.g.query(
            "MATCH ()-[r:IMPL|NAND]->() "
            "WHERE r.batch_id IS NOT NULL "
            "WITH r.batch_id AS bid, max(r.createdAt) AS newest, count(r) AS c "
            "RETURN bid, newest, c",
        ).result_set
        newest_by_bid: dict[str, str] = {}
        by_bid: dict[str, dict] = {}
        for (bid, newest, c) in rows:
            by_bid[bid] = {"batch_id": bid, "points": c, "direct_edges": 0}
            if newest is not None and (bid not in newest_by_bid
                                       or newest > newest_by_bid[bid]):
                newest_by_bid[bid] = newest
        for (bid, newest, c) in edge_rows:
            if bid in by_bid:
                by_bid[bid]["direct_edges"] = c
            else:
                by_bid[bid] = {"batch_id": bid, "points": 0,
                               "direct_edges": c}
            if newest is not None and (bid not in newest_by_bid
                                       or newest > newest_by_bid[bid]):
                newest_by_bid[bid] = newest
        items = list(by_bid.values())
        items.sort(key=lambda b: newest_by_bid.get(b["batch_id"], ""),
                   reverse=True)
        return items[:limit]

    def create_direct_edge(self, op_type: str, source_id: str, target_id: str, *,
                           direction: str | None = None,
                           confidence: float | None = None,
                           weight: float | None = None,
                           label: str | None = None,
                           batch_id: str | None = None,
                           promote_source: bool = True) -> dict:
        """Create an OPERATOR-LESS direct IMPL/NAND Point→Point edge (plan §5.3).

        Ontology v3.5 §8 / v3.8: plain IMPL/NAND connections are direct edges
        (edge-carried direction/confidence/weight/label/batch_id; NO operator
        node). The edge is a BARE-pattern MERGE + attribute SET — exactly one
        edge per (src,tgt,type), last-writer-wins on attribute change
        (MERGE-with-attributes would create a parallel edge on attribute
        change, violating EP's no-parallel-direct-edges contract).

        Guards (shared with #901, E2E-11.7):
          - endpoints exist AND are plain Points (a Source/Subject/event/
            operator endpoint is a typed error);
          - terminal-endpoint guard: `status NOT IN {superseded, retracted}`
            (a direct edge incident to a terminal point would recreate the
            terminal-point propagation hazard).
        op_type ∈ {IMPL, NAND}.

        Promotion (CYCLE-24 promotion-on-created-only pin): `promote_source`
        fires ONLY when the MERGE CREATED the edge (`created==True`) — a
        bare-MERGE dedup hit applies NO promotion (dedup never rewrites).
        CYCLE-25: direction-absent NAND defaults to "unidirectional" on the
        edge (extraction default — new-claim-attacks-existing); direction-
        absent IMPL stays "bidirectional".

        Emits the dedicated JSONL edge descriptor for rebuild durability
        (plan §4.4 — A10's pass-2b consumer) on created==True.

        Returns {"direct_edge": op_type, "from": source_id, "to": target_id,
                 "created": bool, "deduped": bool}.
        """
        if op_type not in ("IMPL", "NAND"):
            raise ValueError(
                f"create_direct_edge: op_type must be IMPL or NAND, got {op_type!r}"
            )
        if source_id == target_id:
            raise ValueError("create_direct_edge: source_id and target_id must differ")
        if confidence is not None and not (0.0 <= float(confidence) <= 1.0):
            raise ValueError(
                f"create_direct_edge: confidence must be in [0,1], got {confidence!r}"
            )
        # CYCLE-25: NAND extraction default — direction-absent NAND is
        # "unidirectional" (new-claim-attacks-existing); IMPL stays
        # bidirectional. An explicit value is preserved verbatim.
        if direction is None:
            direction = "unidirectional" if op_type == "NAND" else "bidirectional"
        if direction not in ("bidirectional", "unidirectional"):
            raise ValueError(
                f"create_direct_edge: direction must be 'bidirectional' or "
                f"'unidirectional', got {direction!r}"
            )

        proj = self._get_proj()
        # Endpoint validation: exist, plain Points, non-terminal.
        rows = proj.g.query(
            "MATCH (p:Point) WHERE p.id IN $ids "
            "RETURN p.id, coalesce(p.is_operator, false), p.status",
            params={"ids": [source_id, target_id]},
        ).result_set
        found = {r[0]: (bool(r[1]), r[2]) for r in rows}
        for pid in (source_id, target_id):
            if pid not in found:
                raise ValueError(
                    f"create_direct_edge: endpoint {pid!r} does not exist or "
                    f"is not a Point"
                )
            is_op, status = found[pid]
            if is_op:
                raise ValueError(
                    f"create_direct_edge: endpoint {pid!r} is an operator — "
                    f"direct edges connect plain Points only"
                )
            if status in ("superseded", "retracted"):
                raise ValueError(
                    f"create_direct_edge: endpoint {pid!r} is terminal "
                    f"({status!r}) — a direct edge incident to a terminal "
                    f"point is rejected (terminal-point propagation hazard)"
                )

        # BARE-pattern MERGE + attribute SET (last-writer-wins; exactly one
        # edge per (src,tgt,type)). `created` is detected by a pre-count
        # BEFORE the MERGE (the MERGE's stats aren't reliably surfaced, and a
        # post-MERGE count always sees 1).
        rel_type = op_type  # IMPL / NAND
        pre = proj.g.query(
            f"MATCH (a:Point {{id:$src}})-[r:{rel_type}]->(b:Point {{id:$tgt}}) "
            f"RETURN count(r)",
            params={"src": source_id, "tgt": target_id},
        ).result_set
        created = (pre[0][0] == 0)
        # Two-step MERGE (FalkorDB quirk fixed, cycle-26): a MERGE whose node
        # patterns carry property filters can CREATE DUPLICATE NODES when the
        # relationship is absent (the node match is ambiguous). MATCH the
        # existing nodes first, then MERGE the edge BETWEEN them — exactly one
        # edge per (src,tgt,type), no duplicate nodes.
        proj.g.query(
            f"MATCH (a:Point {{id:$src}}), (b:Point {{id:$tgt}}) "
            f"MERGE (a)-[r:{rel_type}]->(b)",
            params={"src": source_id, "tgt": target_id},
        )
        attrs = {"direction": direction}
        if confidence is not None:
            attrs["confidence"] = float(confidence)
        if weight is not None:
            attrs["weight"] = float(weight)
        if label is not None:
            attrs["label"] = label
        if batch_id is not None:
            attrs["batch_id"] = batch_id
        # SET r += $attrs (additive — never clobbers EP-managed msg_* fields)
        proj.g.query(
            f"MATCH (a:Point {{id:$src}})-[r:{rel_type}]->(b:Point {{id:$tgt}}) "
            f"SET r += $attrs",
            params={"src": source_id, "tgt": target_id, "attrs": attrs},
        )

        # Promotion-on-created-only (CYCLE-24 pin): the guarded #131-style SET
        # fires ONLY when the MERGE created the edge.
        if created and promote_source:
            proj.g.query(
                "MATCH (s:Point {id:$id}) "
                "WHERE s.status IS NULL OR s.status = 'draft' "
                "SET s.status = 'live', s.updatedAt = $now",
                params={"id": source_id,
                        "now": _now_iso()},
            )

        # JSONL edge descriptor (plan §4.4) — emitted on EVERY write (the
        # MERGE-keyed replay is idempotent, so dedup-hit emissions are
        # harmless, and the crash window closes: a crash between the MERGE
        # and the emission on attempt 1 is recovered by attempt 2's emission —
        # a created-only gate would lose the edge from the log forever on
        # crash-retry). REVIEW-FIX P1 (cycle-26): last-writer-wins attr
        # updates must replay the FINAL attrs post-rebuild.
        self._emit_event(
            "DirectEdgeCreated",
            id=f"{source_id}->{target_id}:{op_type}",
            src=source_id, tgt=target_id, edge_type=op_type,
            direction=direction,
            **({"confidence": float(confidence)} if confidence is not None else {}),
            **({"weight": float(weight)} if weight is not None else {}),
            **({"label": label} if label is not None else {}),
            **({"batch_id": batch_id} if batch_id is not None else {}),
        )

        self._mark_dirty([source_id, target_id])
        # Epic 903-C4 (#1242): a new direct edge changes the factor graph —
        # invalidate warm-start seeds on the endpoints' edges.
        self._get_ep().invalidate_messages([source_id, target_id])
        return {"direct_edge": op_type, "from": source_id, "to": target_id,
                "created": created, "deduped": not created}

    def _find_operator(self, op_type: str, inputs: list[str],
                       label: str | None = None,
                       direction: str | None = None) -> dict | None:
        """§5.5 OPERATOR-DEDUP (A3): richer return — exact-hit | partial-absorb
        | miss.

        Exact-hit: an operator with the SAME (op_type, input set) — returns
        {"id", "status", "kind": "exact"}. Condition builders apply the
        CYCLE-25 per-op_type absent↔default canonicalization (IMPL
        direction-absent → stored "bidirectional"; NAND direction-absent →
        stored "unidirectional" — the extraction default) so a
        direction-omitting retry dedups its own run-1 operator (exactly-once);
        label-absent appends `o.label IS NULL` (no-label requests match only
        unlabeled operators).

        Partial-absorb: on an exact miss, a NULL-status (unpromoted) operator
        whose input set is a PROPER SUBSET of the requested set AND whose
        label/direction STRICT-MATCH the declared values (a label-absent
        request must NOT absorb a labeled operator — the cross-absorption
        semantic-theft class) AND whose written input set is NON-EMPTY —
        returns {"id", "status", "kind": "partial", "written": [...]}.
        TWO+ qualifying candidates → {"kind": "decline",
        "candidates": [...]} (never an arbitrary absorption).

        Miss → None.

        The SOLE caller (ingest operator path) unwraps the plain operator-id
        str for the exact-hit branch and runs the EDGE-COMPLETION step
        (bare-MERGE + SET per input — IDEMPOTENT) for a partial-absorb.
        """
        proj = self._get_proj()
        edge_rel = "hasPart" if op_type not in ("IMPL", "NAND") else op_type
        canonical_dir = self._canonical_direction(op_type, direction)
        params: dict = {"op": op_type, "inputs": list(inputs)}
        conds = [
            "size(targets) = size($inputs)",
            "all(x IN targets WHERE x IN $inputs)",
        ]
        if label is not None:
            conds.append("o.label = $label")
            params["label"] = label
        else:
            # CYCLE-17 clause: label-absent → `o.label IS NULL` (no-label
            # requests match only unlabeled operators — never a wildcard)
            conds.append("o.label IS NULL")
        if direction is not None or canonical_dir is not None:
            # CYCLE-17/25: direction-absent canonicalizes per op_type (IMPL
            # → bidirectional, NAND → unidirectional) — NEVER `o.direction IS
            # NULL` (direction is always stored; a NULL clause matches
            # NOTHING and recreates the exactly-once P1 on the default shape)
            conds.append("o.direction = $direction")
            params["direction"] = canonical_dir
        rows = proj.g.query(
            f"MATCH (o:Point {{is_operator:true, op_type:$op}}) "
            f"OPTIONAL MATCH (o)-[r:{edge_rel}]->(t) "
            f"WHERE (t:Point OR t:Event) "
            f"WITH o, collect(t.id) AS targets, collect(r) AS _ "
            f"WHERE {' AND '.join(conds)} "
            f"RETURN o.id, o.status LIMIT 1",
            params=params,
        ).result_set
        if rows:
            return {"id": rows[0][0], "status": rows[0][1],
                    "kind": "exact", "written": list(inputs)}
        # ── partial-absorb (cycle-11/12/13): on an exact miss, look for a
        # NULL-status partial whose written set is a PROPER SUBSET of the
        # requested set with STRICT label/direction matching (the declared
        # values — absent↔default canonicalized the same way).
        pcond = [
            "size(targets) < size($inputs)",
            "all(x IN targets WHERE x IN $inputs)",
            "size(targets) > 0",
            "(o.status IS NULL OR o.status = 'draft')",
        ]
        if label is not None:
            pcond.append("o.label = $label")
        else:
            pcond.append("o.label IS NULL")
        if direction is not None or canonical_dir is not None:
            pcond.append("o.direction = $direction")
        prows = proj.g.query(
            f"MATCH (o:Point {{is_operator:true, op_type:$op}}) "
            f"OPTIONAL MATCH (o)-[r:{edge_rel}]->(t) "
            f"WHERE (t:Point OR t:Event) "
            f"WITH o, collect(t.id) AS targets "
            f"WHERE {' AND '.join(pcond)} "
            f"RETURN o.id, o.status, targets",
            params=params,
        ).result_set
        if len(prows) == 1:
            return {"id": prows[0][0], "status": prows[0][1],
                    "kind": "partial", "written": list(prows[0][2])}
        if len(prows) > 1:
            return {"kind": "decline",
                    "candidates": [r[0] for r in prows]}
        return None

    def _append_warning(self, entry: str) -> None:
        """Append to the current ingest's warnings accumulator (A3 cycle-23
        contract). The accumulator is the module-level thread-local set by
        ingest(); outside an ingest, the entry is a no-op."""
        acc = getattr(self, "_ingest_warnings", None)
        if acc is not None:
            acc.append(entry)

    def _reapply_operator_promotion(self, op_id: str) -> None:
        """k=N boundary (cycle-15): re-apply promotion to a NULL-status
        operator under auto (a crash after the full input-edge loop, before
        the promotion SET). Mirrors create_operator's #131 guarded SET."""
        proj = self._get_proj()
        proj.g.query(
            "MATCH (s:Point {id:$id}) "
            "WHERE s.status IS NULL OR s.status = 'draft' "
            "SET s.status = 'live', s.updatedAt = $now",
            params={"id": op_id, "now": _now_iso()},
        )

    def file_decision(self, options: list[str], evidence: list[str],
                      choice: int) -> dict:
        """File a simple decision directly to the graph — no EP, no calibration,
        no research cycles. Creates decision + options + evidence + IMPL edges
        atomically. For low-stakes decisions where the answer is clear (#133).

        Args:
            options: list of option descriptions (e.g. ["JSON", "YAML"])
            evidence: list of evidence statements supporting the choice
            choice: 0-indexed index into options (the chosen option)

        Returns {decision_id, option_ids: [...], evidence_ids: [...]}.
        """
        if not options:
            raise ValueError("At least one option required")
        if choice < 0 or choice >= len(options):
            raise ValueError(f"choice={choice} out of range [0, {len(options)-1}]")

        # 1. Create decision point
        decision = self.create_point(
            "decision",
            f"Decision: {options[choice]}",
            status="live",
        )
        decision_id = decision["id"]

        # 2. Create option points + IMPL edges from decision
        option_ids = []
        for i, opt in enumerate(options):
            opt_point = self.create_point(
                "option",
                f"Option {i+1}: {opt}",
                status="live",  # options are targets, not sources — explicit live
            )
            option_ids.append(opt_point["id"])
            # IMPL edge: decision -> option ("decision considers option")
            self.create_operator("IMPL", decision_id, [opt_point["id"]])

        # 3. Create evidence points + IMPL edges to the chosen option
        evidence_ids = []
        chosen_id = option_ids[choice]
        for ev in evidence:
            ev_point = self.create_point(
                "evidence",
                ev,
            )
            evidence_ids.append(ev_point["id"])
            # IMPL edge: evidence -> chosen option ("evidence supports choice")
            self.create_operator("IMPL", ev_point["id"], [chosen_id])

        return {
            "decision_id": decision_id,
            "option_ids": option_ids,
            "evidence_ids": evidence_ids,
        }

    def file_human_approval(self, approver_id: str, artifact_id: str,
                            point_ids: list[str],
                            decision_content: str | None = None) -> dict:
        """Record a human approval of a planning artifact (#531).

        Canonical approval pattern (research #421): an Event
        (eventKind: ``humanApproval``) records the occurrence with full
        provenance — approver Subject, artifact, approved claim Points — while
        a decision Point (pointKind: ``humanApproval``) carries the epistemic
        weight: it seeds the grounding a-vector and receives an EP evidence
        prior so dependent claims strengthen. Unidirectional IMPL fan-out
        (label ``approvedBy``) links the approval Point to each approved claim
        Point — deliberately unidirectional so EP never propagates claim
        weakness back into the approval.

        Deliberately NOT stored: no ``approved`` status flag on the artifact —
        approval is derived from the event stream at query time (ONTOLOGY §2).

        Args:
            approver_id: Subject id (or name) of the human approving.
            artifact_id: Object/Document id (or name) of the artifact approved.
            point_ids: claim Point ids the approval covers (non-operator).
            decision_content: optional content override for the decision Point
                (default ``"Approved: <artifact>"``).

        Returns {event_id, decision_point_id, impl_operator_ids,
                 confidence_delta} where confidence_delta maps each approved
        claim id to its confidence change after the EP run.
        """
        from datetime import datetime, timezone
        proj = self._get_proj()

        # 1. Validate approver Subject exists (fail loudly, not silently)
        r = proj.g.query(
            "MATCH (s:Subject) WHERE s.id = $id OR s.name = $id RETURN s.id",
            params={"id": approver_id},
        ).result_set
        if not r:
            raise ValueError(
                f"Cannot file human approval: Subject {approver_id!r} does not exist"
            )

        # 2. Validate artifact exists (Object or Document)
        r = proj.g.query(
            "MATCH (n) WHERE (n:Object OR n:Document) "
            "AND (n.id = $id OR n.name = $id) RETURN labels(n), n.id",
            params={"id": artifact_id},
        ).result_set
        if not r:
            raise ValueError(
                f"Cannot file human approval: artifact {artifact_id!r} does not exist"
            )

        # 3. Validate point_ids exist and are non-operator Points
        if not point_ids:
            raise ValueError("Cannot file human approval: at least one claim Point required")
        r = proj.g.query(
            "MATCH (n:Point) WHERE n.id IN $ids "
            "AND n.is_operator = false RETURN n.id",
            params={"ids": point_ids},
        ).result_set
        existing = {row[0] for row in r}
        missing = [pid for pid in point_ids if pid not in existing]
        if missing:
            raise ValueError(
                f"Cannot file human approval: Points {missing} do not exist or are operators"
            )

        now = datetime.now(timezone.utc).isoformat()  # noqa: UP017

        # 4. Decision Point (pointKind humanApproval) — epistemic weight carrier
        content = decision_content or f"Approved: {artifact_id}"
        decision = self.create_point(
            "humanApproval",
            content,
            status="live",
            authoredBy=approver_id,
        )
        decision_id = decision["id"]

        # 5. Event (eventKind humanApproval) — the occurrence record
        event = self.create_event(
            name=f"human approval of {artifact_id}",
            eventKind="humanApproval",
            startedAt=now,
            eventStatus="completed",
        )
        event_id = event["eventId"]
        # Wire provenance: approver performs; uses artifact; aboutPoint each
        # approved claim; produces the decision Point (design #421).
        proj.create_edge(approver_id, event_id, "performs")
        proj.create_edge(event_id, artifact_id, "uses")
        for pid in point_ids:
            proj.create_about_edge(event_id, pid, "aboutPoint")
        proj.create_edge(event_id, decision_id, "produces")

        # 6. Unidirectional IMPL fan-out: approval → each approved claim.
        #    create_operator defaults to bidirectional — direction must be
        #    explicit so EP never back-propagates claim weakness into the
        #    approval point.
        op = self.create_operator(
            "IMPL", decision_id, point_ids,
            label="approvedBy", direction="unidirectional",
        )

        # 7. EP with evidence prior on the approval Point: Beta(10,1) is a
        #    strong positive prior — dependents strengthen (issue #531).
        before = {}
        for pid in point_ids:
            p = self.get_point(pid)
            before[pid] = p.get("confidence", 0.5) if p else 0.5
        # #344: conscious opt-out from the fail-closed gate — the gate is
        # graph-wide (no anchors/factors are passed, so it would audit every
        # evidence point); this run's epistemic content is fully carried by
        # the Beta(10,1) evidence prior on the decision Point; the approval
        # path deliberately opts out (never a silent swallow; the gate stays
        # on for every explicit compute_confidence()).
        self.compute_confidence(evidence={decision_id: (10, 1)},
                                require_calibration=False)
        deltas = {}
        for pid in point_ids:
            p = self.get_point(pid)
            after = p.get("confidence", 0.5) if p else 0.5
            deltas[pid] = round(after - before[pid], 4)

        # 8. Approval seeds the grounding a-vector — re-compute (api.py pattern)
        if hasattr(proj, "compute_grounding"):
            proj.compute_grounding()

        return {
            "event_id": event_id,
            "decision_point_id": decision_id,
            "impl_operator_ids": [op["id"]],
            "confidence_delta": deltas,
        }

    # ── Lifecycle ─────────────────────────────────────────────────

    def list_graphs(self) -> list[str]:
        """List all graph names in the database."""
        return self._get_proj().list_graphs()

    def _audit(self, team_id: str, actor_user_id: str | None,
                operation: str, **kwargs) -> None:
        """Log an audit event. No-op if audit logger not initialized."""
        if self._audit_logger is None:
            from .audit_events import AuditLogger
            self._audit_logger = AuditLogger()
        self._audit_logger.append(
            team_id=team_id,
            actor_user_id=actor_user_id,
            operation=operation,
            **kwargs,
        )

    def list_relations(self) -> list[dict]:
        """List all relation declarations across installed packs.

        Returns [{"pack": ..., "predicate": ..., "fromKind": ..., "toKind": ...,
        "mechanism": ...}]. Pack relations describe valid edge types between
        entity kinds — use for schema discovery.
        """
        return _get_kind_expander().list_relations()

    def _atexit_close(self) -> None:
        """#1371: atexit seam — collect ephemeral test servers for the
        batch flush first.

        Falls through to the normal _t_close when the fast path does not
        apply.
        """
        proj = getattr(self, "_proj", None)
        db = getattr(proj, "db", None) if proj is not None else None
        if db is not None and atexit_fast_close(getattr(db, "client", db)):
            self._t_closed = True
            return
        self._t_close()

    def _t_close(self) -> None:
        """Idempotent close; safe from atexit or __exit__ (#1005).

        Does NOT set _t_closed itself — close() owns the flag (setting it
        here would make close() short-circuit and never run its body).
        """
        if getattr(self, "_t_closed", False):
            return
        try:
            self.close()
        except Exception:
            self._t_closed = True  # never retry a failing close

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._t_close()
        return False

    def close(self) -> None:
        """Close the underlying database connection and audit logger."""
        if getattr(self, "_t_closed", False):
            return
        self._t_closed = True
        if self._audit_logger is not None:
            self._audit_logger.close()
            self._audit_logger = None
        if self._proj is not None:
            self._proj.close()
            self._proj = None
        self._registry_g = None

    # ── P1-4: Entity Linking ────────────────────────────────────

    def provenance(self, point_id: str) -> dict:
        """Provenance chain — "Who decided this?" Point → Subject → delegation."""
        point = self.get_point(point_id)
        if not point:
            return {"error": f"Point {point_id} not found"}
        author = point.get("authoredBy", "")
        chain = {"point": {"id": point_id, "content": (point.get("content") or "")[:200],
                           "authoredBy": author}}
        if not author:
            return chain
        proj = self._get_proj()
        rows = proj.g.query(
            "MATCH (s:Subject) WHERE toLower(s.name) = toLower($n) RETURN properties(s)",
            params={"n": author},
        ).result_set
        if not rows:
            return {**chain, "subject": None}
        sub = rows[0][0]
        chain["subject"] = {"id": sub.get("id"), "name": sub.get("name"),
                             "kind": sub.get("subjectKind", "")}
        # ponytail: follow outgoing rels for Role → Team delegation
        rels = proj.g.query(
            "MATCH (s:Subject {id:$sid})-[r]->(n) RETURN type(r), labels(n)[0], properties(n)",
            params={"sid": sub["id"]},
        ).result_set
        chain["delegation"] = [{"via": r[0], "node_type": r[1], "props": r[2]} for r in rels]
        return chain

    # ── #7045: about edges backfill (Ontology v2.1) ──────────

    def backfill_about_entities(self) -> dict:
        """Keyword-match Points against Subject/Object/Event/Document names → about edges.

        For each Point (non-operator), checks if its content contains any Subject,
        Object, Event, or Document name/title. If yes, creates the matching about*
        edge (aboutSubject, aboutObject, aboutEvent, aboutDocument).
        Idempotent — MERGE prevents duplicates.

        Returns {scanned, updated, entities_matched}.
        """
        proj = self._get_proj()
        # Load all entity names → ids (flat dict for membership check)
        entities: dict[str, str] = {}
        # Subject + Object: matched by name property
        for label in ("Subject", "Object"):
            rows = proj.g.query(
                f"MATCH (e:{label}) WHERE e.name IS NOT NULL RETURN e.name, e.id"
            ).result_set
            for name, eid in rows:
                if name:
                    entities[name.lower()] = eid
        # Event: matched by name property (set by create_event)
        for row in proj.g.query(
            "MATCH (e:Event) WHERE e.name IS NOT NULL RETURN e.name, e.eventId"
        ).result_set:
            name, eid = row[0], row[1]
            if name:
                entities[name.lower()] = eid
        # Document: matched by title (primary display name) or name
        for row in proj.g.query(
            "MATCH (d:Document) WHERE d.title IS NOT NULL RETURN d.title, d.id"
        ).result_set:
            title, did = row[0], row[1]
            if title:
                entities[title.lower()] = did

        # Ontology v2.1: use per-type about* edges instead of property
        rows = proj.g.query(
            "MATCH (n:Point) "
            "WHERE n.is_operator = false "
            "RETURN n.id, n.content"
        ).result_set

        scanned, updated, matched = 0, 0, 0
        for pid, content in rows:
            scanned += 1
            if not content:
                continue
            content_lower = content.lower()
            for name, eid in entities.items():  # noqa: B007
                if name in content_lower:
                    proj._create_about_edges(pid, name)
                    matched += 1
            if matched > 0:
                updated += 1

        return {"scanned": scanned, "updated": updated, "entities_matched": matched}

    # ── P1-3: Staleness ─────────────────────────────────────────

    def stale_points(self, days: int = 30, limit: int = 50) -> dict:
        """Return Points not updated in N days. Returns {stale: [...], count: N, cutoff: '...'}."""
        proj = self._get_proj()
        stale = proj.stale_points(days=days, limit=limit)
        return {"stale": stale, "count": len(stale),
                "cutoff": f"{days} days", "limit": limit}

    # ── Connection review (#913 W6) ─────────────────────────────────

    def review_connections(
        self,
        mode: str = "both",
        scope: str | None = None,
        *,
        # #1349 T14: derived from tortoise.embeddings DEFAULT_THRESHOLD
        # (0.72 for bge-small) — single source of threshold truth; a model
        # rotation recalibrates embeddings.py and this follows.
        similarity_threshold: float = DEFAULT_THRESHOLD,
        variance_threshold: float = 0.04,
        add_limit: int = 20,
        prune_limit: int = 50,
        similarity_fn=None,
    ) -> dict:
        """Review graph connections — the hygiene counterpart to connect
        (#913 W6, design: product/2026-08-11-tooling-surface-consolidation.md).

        READ-ONLY: surfaces suggestions and flags; never mutates the graph.
        The agent decides, then acts via create_operator / supersede / delete.

        mode=add: find relevant-but-MISSING connections. Pairs of Points that
            are semantically related (embedding cosine similarity above
            ``similarity_threshold``) but NOT yet connected (no shared
            operator, no direct edge) are surfaced as suggested connections:
            {from, to, suggested_relation: "IMPL", reason, similarity}.
            Suggestions only — nudge, don't enforce (design principle 4).
            Scope: ``scope`` (topic text or Point id) narrows the candidate
            pool via hybrid retrieval; None = whole graph, capped at
            REVIEW_ADD_POOL_CAP most-recently-updated non-terminal,
            non-operator Points (pairwise scoring is O(n²) — bound the work;
            pass a scope for larger graphs).
        mode=prune: find ILLOGICAL/stale connections to fix or prune, using
            EP signals. Flags IMPL/NAND edges (operator-mediated OR direct
            per the reification rule) where:
              * stale         — edge incident to a retracted/superseded/
                                outdated/archived Point (or legacy
                                outdated=true flag). suggested_action is
                                "re-point" when a CORRECTS successor exists,
                                else "prune".
              * contested     — edge incident to a claim with high posterior
                                variance (stored EP params only — an
                                unmeasured uniform prior is NOT contested)
                                or a claim with an incoming NAND operator
                                edge (the derived `challenged` condition,
                                ontology §5). suggested_action "review".
              * contradictory — the same pair is BOTH IMPL- and NAND-linked
                                (implication + mutual exclusion at once).
                                suggested_action "review".
        mode=both: run both, return {add: [...], prune: [...]}.

        Returns {add: [{from, to, suggested_relation, reason, similarity}],
                 prune: [{from, to, relation, issue, suggested_action,
                          detail}]} — only the key(s) for the requested mode.

        Args:
            mode: "add", "prune" or "both" (default "both").
            scope: optional topic text or Point id — narrows the review.
            similarity_threshold: minimum cosine similarity for mode=add
                (default 0.72 — the bge-small DEFAULT_THRESHOLD "semantically
                related" cross-vocabulary band, see tortoise/embeddings.py;
                near-duplicates are 0.89+).
            variance_threshold: posterior variance above which a claim is
                contested (default 0.04 — same signal as search_engine's
                has_ep-guarded contested flag; deliberately NOT
                TortoiseEP.get_contested_claims, which flags unmeasured
                uniform priors — an unmeasured claim is not contested).
            add_limit: max suggestions (default 20).
            prune_limit: max flagged entries (default 50).
            similarity_fn: injectable pairwise-similarity function for
                mode=add (tests / tuning). Signature:
                similarity_fn(points: list[dict]) -> {(a_id, b_id): score}
                with a_id < b_id. Default: embedding cosine via
                tortoise.embeddings._encode (TF-IDF fallback when the model
                is unavailable).

        Raises ValueError on an invalid mode or out-of-range parameters.
        """
        if mode not in ("add", "prune", "both"):
            raise ValueError(f"mode must be 'add', 'prune' or 'both', got {mode!r}")
        if not (0.0 <= similarity_threshold <= 1.0):
            raise ValueError(
                f"similarity_threshold must be 0.0-1.0, got {similarity_threshold!r}")
        if variance_threshold < 0:
            raise ValueError(
                f"variance_threshold must be >= 0, got {variance_threshold!r}")
        if add_limit < 1 or prune_limit < 1:
            raise ValueError(
                f"add_limit and prune_limit must be >= 1, got {add_limit!r}/{prune_limit!r}")

        result: dict = {}
        if mode in ("add", "both"):
            result["add"] = self._review_add(
                scope, similarity_threshold, add_limit, similarity_fn)
        if mode in ("prune", "both"):
            result["prune"] = self._review_prune(
                scope, variance_threshold, prune_limit)
        return result

    # ── Cross-lens candidate discovery (#438, BYOA) ───────────────────────

    def get_cross_lens_candidates(
        self,
        *,
        threshold: float = DEFAULT_THRESHOLD,
        max_candidates: int = CROSS_LENS_CANDIDATE_CAP,
        routing: str = "truth",
        top_k: int = CROSS_LENS_ANN_TOP_K,
    ) -> dict:
        """Surface unverified cross-lens candidate pairs (#438, bring-your-own-agent).

        READ-ONLY — never writes to the graph and never decides operator
        semantics (the candidates-never-write/never-decide contract extends
        from cross_lens.py to this surface). Returns candidate pairs between
        Points from DIFFERENT sources (cross-stream discovery over the
        existing HNSW vector index) with lens pair, cosine similarity, point
        context, and dedup vs existing operators. The customer agent confirms
        candidates and writes operators through the normal API (no in-repo
        verifier — D7).

        Contract (2026-08-15 scoping, docs/scoping/
        2026-08-15-438-cross-domain-discovery-scoping.md):
          - D2: the payload carries a single ``routing`` field ("truth" |
            "relevance", #901 semantics). The surface stays NEUTRAL — no
            op_type / suggested_relation hint; the caller's declared routing
            context is echoed per candidate (deciding semantics is the
            customer agent's job).
          - D3: candidates are gated on a REGISTERED sourceKind (any tier) —
            Points whose Source has an unregistered sourceKind (or no Source
            at all) never appear, since unregistered kinds stay neutral
            (#398 / source_credibility.SOURCE_KIND_DEFAULTS).
          - D4: hard cap 200 candidates/cycle — ``max_candidates`` is clamped
            to CROSS_LENS_CANDIDATE_CAP (predictable per-cycle cost; local
            pre-filter tuning is #909's territory).
          - D8: empty results (not errors) when there is nothing to see.

        Discovery cost: bounded ANN pull over the existing Point.embedding
        vector index (HNSW on Docker/server FalkorDB; brute-force euclidean
        fallback on embedded — the documented degradation) with ``top_k``
        neighbors per pool point, then exact cosine recompute from stored
        embeddings (index scores are rank/euclidean, not the calibrated
        cosine). The scanned pool is capped at CROSS_LENS_POOL_CAP
        most-recently-updated eligible points — pair enumeration is
        pool-bounded (each pool point contributes at most ``top_k``
        neighbors, candidate space O(pool x top_k)), not O(n²) over the
        whole graph (indicator I3). Cost caveat: on the embedded backend
        run_vector_query is a brute-force scan over the ENTIRE Point index,
        so embedded recall is O(pool x total_points) — the pool cap bounds
        the pair space, not the per-query scan cost (the documented
        degradation).

        Recall caveat: the default top_k=20 favors dense recent clusters —
        same-source near-duplicates (D5-excluded territory) can crowd the
        top-k and crowd out genuine cross-lens pairs. Raise top_k (up to
        the hard cap) or lower ``threshold`` when cross-lens recall looks
        thin.

        Args:
            threshold: minimum cosine similarity (default 0.72 — the
                bge-small DEFAULT_THRESHOLD cross-vocabulary band from
                tortoise/embeddings.py; near-duplicates are 0.89+).
            max_candidates: requested result cap — HARD-clamped to 200 (D4).
            routing: the #901 routing context the caller is mining under
                ("truth" | "relevance") — echoed per candidate; the surface
                itself stays neutral.
            top_k: ANN neighbors pulled per pool point (recall bound) —
                HARD-clamped to CROSS_LENS_ANN_TOP_K_MAX (100) so an agent
                cannot inflate the per-cycle recall budget (D4 philosophy).

        Returns:
            {"candidates": [{src, dst, similarity, lenses, sourceKinds,
              src_content, dst_content, src_source, dst_source, routing}],
             "count": N, "cap": 200, "truncated": bool, "routing": str}.
            Candidates sorted by similarity descending.

        Raises ValueError on invalid routing/threshold/max_candidates/top_k.
        """
        if routing not in ("truth", "relevance"):
            raise ValueError(
                f"routing must be 'truth' or 'relevance' (#901), got {routing!r}"
            )
        if not (0.0 <= threshold <= 1.0):
            raise ValueError(f"threshold must be 0.0-1.0, got {threshold!r}")
        if max_candidates < 1:
            raise ValueError(f"max_candidates must be >= 1, got {max_candidates!r}")
        if top_k < 1:
            raise ValueError(f"top_k must be >= 1, got {top_k!r}")
        top_k = min(int(top_k), CROSS_LENS_ANN_TOP_K_MAX)  # hard cap (conf 75)

        limit = min(int(max_candidates), CROSS_LENS_CANDIDATE_CAP)  # D4
        pool = self._cross_lens_pool()
        if len(pool) < 2:
            return {"candidates": [], "count": 0,
                    "cap": CROSS_LENS_CANDIDATE_CAP,
                    "truncated": False, "routing": routing}

        connected = self._connected_pairs(list(pool))
        pairs = self._cross_lens_pairs(pool, threshold=threshold, top_k=top_k)

        all_candidates: list[dict] = []
        for (a, b), score in pairs:
            if frozenset((a, b)) in connected:
                continue  # dedup vs existing operators (Slice 1)
            pa, pb = pool[a], pool[b]
            all_candidates.append({
                "src": a,
                "dst": b,
                "similarity": round(score, 6),
                "lenses": [pa["source"], pb["source"]],
                "sourceKinds": [pa["sourceKind"], pb["sourceKind"]],
                "src_content": pa["content"],
                "dst_content": pb["content"],
                "src_source": pa["source_id"],
                "dst_source": pb["source_id"],
                "routing": routing,
            })

        truncated = len(all_candidates) > limit
        candidates = all_candidates[:limit]
        return {"candidates": candidates, "count": len(candidates),
                "cap": CROSS_LENS_CANDIDATE_CAP,
                "truncated": truncated, "routing": routing}

    def _cross_lens_pool(self) -> dict[str, dict]:
        """Eligible pool for cross-lens discovery (#438).

        Non-operator, non-terminal Points with a stored embedding AND a Source
        whose sourceKind is REGISTERED (D3 — any tier: T0-T4 identity kinds
        and explicitly-registered kinds count; unregistered kinds stay
        neutral). Bounded to the CROSS_LENS_POOL_CAP most-recently-updated
        points so the per-cycle scan stays proportional to new data.
        """
        from tortoise.source_credibility import SOURCE_KIND_DEFAULTS

        registered = [k for k in SOURCE_KIND_DEFAULTS]
        proj = self._get_proj()
        rows = proj.g.query(
            "MATCH (p:Point)-[:extractedFrom]->(s:Source) "
            "WHERE p.is_operator = false "
            "  AND p.embedding IS NOT NULL "
            "  AND (p.status IS NULL OR p.status IN ['draft', 'live']) "
            "  AND (p.outdated IS NULL OR p.outdated = false) "
            "  AND s.sourceKind IN $registered "
            "RETURN p.id, p.content, p.status, s.id, s.url, s.sourceKind, "
            "       p.updatedAt "
            "ORDER BY p.updatedAt DESC "
            "LIMIT $limit",
            params={"registered": registered, "limit": CROSS_LENS_POOL_CAP},
        ).result_set
        pool: dict[str, dict] = {}
        for pid, content, status, sid, url, skind, updated_at in rows:
            content = content or ""
            if content.startswith("[MITIGATION]"):
                continue  # mitigation bookkeeping — not a standalone claim
            pool[pid] = {
                "id": pid,
                "content": content,
                "status": status or "draft",
                "source_id": sid,
                # lens identity: canonical Source url; fall back to node id.
                "source": url or sid,
                "sourceKind": skind,
                "updated_at": updated_at or "",
            }
        return pool

    def _cross_lens_pairs(self, pool: dict[str, dict], *,
                          threshold: float, top_k: int) -> list[tuple[tuple[str, str], float]]:
        """Similar cross-source point pairs via bounded ANN pull (#438).

        For each pool point, pull its top-k neighbors over the vector index
        (run_vector_query: HNSW on Docker/server, brute-force euclidean on
        embedded), restrict to pool members from a DIFFERENT source, and
        recompute the exact cosine similarity from stored embeddings (the
        calibrated #399 metric). Returns [((a, b), score)] with a < b, sorted
        by score descending. Never writes.

        Cost is bounded on BOTH axes: pair enumeration is pool-bounded
        (each pool point contributes at most top_k neighbors, so the
        candidate space is O(pool x top_k)) and the pool itself is capped
        at CROSS_LENS_POOL_CAP. Caveat: on the embedded backend the
        run_vector_query recall is a brute-force scan over the ENTIRE Point
        index, so embedded cost is O(pool x total_points) regardless of the
        pool cap — the documented degradation. ``top_k`` is hard-clamped at
        CROSS_LENS_ANN_TOP_K_MAX by the caller.
        """
        from .search_engine import run_vector_query  # noqa: I001
        import numpy as np

        proj = self._get_proj()
        is_embedded = getattr(proj, "_is_embedded", True)
        # Fetch stored embeddings in one query (round-trip as float32 lists).
        rows = proj.g.query(
            "MATCH (p:Point) WHERE p.id IN $ids AND p.embedding IS NOT NULL "
            "RETURN p.id, p.embedding",
            params={"ids": list(pool)},
        ).result_set
        embeddings: dict[str, np.ndarray] = {}
        for pid, emb in rows:
            if isinstance(emb, list) and emb:
                embeddings[pid] = np.asarray(emb, dtype=np.float64)

        best: dict[tuple[str, str], float] = {}
        for pid, meta in pool.items():
            vec = embeddings.get(pid)
            if vec is None:
                continue
            hits = run_vector_query(proj.g, vec.tolist(), limit=top_k + 1,
                                    is_embedded=is_embedded,
                                    # #1359: recorded index API → skip the
                                    # failing signature attempt on Cypher engines.
                                    vector_index_api=getattr(
                                        proj, "_vector_index_api", None))
            for nid, _score in hits:
                if nid == pid or nid not in pool:
                    continue
                if pool[nid]["source"] == meta["source"]:
                    continue  # same lens — cross-lens only
                nvec = embeddings.get(nid)
                if nvec is None:
                    continue
                sim = _cosine(vec, nvec)
                if sim < threshold:
                    continue
                a, b = (pid, nid) if pid < nid else (nid, pid)
                if best.get((a, b), 0.0) < sim:
                    best[(a, b)] = sim
        ordered = sorted(best.items(), key=lambda kv: kv[1], reverse=True)
        return [((a, b), s) for (a, b), s in ordered]

    def _review_pool(self, scope: str | None) -> dict[str, dict]:
        """Candidate pool for a review: non-operator, non-terminal Points.

        Whole graph when scope is None; otherwise the hybrid-retrieval pool
        for the scope (topic text, or the resolved Point's content when
        scope is a node id). Retrieval failure degrades to an EMPTY pool
        (fail quiet — never crash a read-only review).
        """
        proj = self._get_proj()
        rows = proj.g.query(
            "MATCH (n:Point) "
            "WHERE n.is_operator = false "
            "  AND (n.status IS NULL OR n.status IN ['draft', 'live']) "
            "  AND (n.outdated IS NULL OR n.outdated = false) "
            "RETURN n.id, n.content, n.status, n.updatedAt",
        ).result_set
        pool = {}
        for pid, content, status, updated_at in rows:
            content = content or ""
            if content.startswith("[MITIGATION]"):
                continue  # mitigation bookkeeping — not a standalone claim
            pool[pid] = {"id": pid, "content": content,
                          "status": status or "draft",
                          "updated_at": updated_at or ""}
        if not scope:
            # Whole-graph scan: cap at the most-recently-updated points so
            # the O(n²) pairwise pass stays bounded (REVIEW_ADD_POOL_CAP).
            ordered = sorted(pool.values(),
                             key=lambda p: (p["updated_at"], p["id"]),
                             reverse=True)
            return {p["id"]: p for p in ordered[:REVIEW_ADD_POOL_CAP]}
        pool = {pid: {k: v for k, v in meta.items() if k != "updated_at"}
                for pid, meta in pool.items()}

        resolved = self.resolve_id(scope)
        query_text = (resolved or {}).get("content") or scope
        try:
            results = self.tortoise_fts_query(
                query_text, entity_type="point", limit=200)
        except Exception:  # noqa: BLE001, RUF100
            return {}
        scoped = {}
        for r in results:
            pid = r.get("id")
            if pid is None or pid not in pool:
                continue
            scores = r.get("scores") or {}
            score = scores.get("rrf") if isinstance(scores, dict) else None
            if score is None:
                score = r.get("match_score", 0.0) or 0.0
            if float(score) <= 0:
                continue  # zero-score tail is not "in scope"
            scoped[pid] = pool[pid]
        return scoped

    def _connected_pairs(self, ids: list[str]) -> set[frozenset]:
        """Unordered pairs of pool Points that already share a connection:
        a common operator node (any edge type) or a direct edge (any type)."""
        proj = self._get_proj()
        pairs: set[frozenset] = set()
        rows = proj.g.query(
            "MATCH (op:Point {is_operator:true})-[r1]->(a:Point), "
            "      (op)-[r2]->(b:Point) "
            "WHERE a.id IN $ids AND b.id IN $ids AND a.id < b.id "
            "RETURN DISTINCT a.id, b.id",
            params={"ids": ids},
        ).result_set
        for a, b in rows:
            pairs.add(frozenset((a, b)))
        rows = proj.g.query(
            "MATCH (a:Point)-[r]-(b:Point) "
            "WHERE a.id IN $ids AND b.id IN $ids AND a.id < b.id "
            "RETURN DISTINCT a.id, b.id",
            params={"ids": ids},
        ).result_set
        for a, b in rows:
            pairs.add(frozenset((a, b)))
        return pairs

    @staticmethod
    def _default_pairwise_similarity(points: list[dict]) -> dict[tuple[str, str], float]:
        """Embedding cosine for every unordered pair (a_id < b_id).

        Degrades to deterministic TF-IDF when the embedding model is
        unavailable (embeddings stay OPTIONAL — #399 contract).
        """
        from .embeddings import _encode, cosine_similarity_matrix
        contents = [p.get("content") or "" for p in points]
        vecs, _ = _encode(contents)
        mat = cosine_similarity_matrix(vecs)
        out: dict[tuple[str, str], float] = {}
        n = len(points)
        for i in range(n):
            for j in range(i + 1, n):
                s = float(mat[i][j])
                if s > 0:
                    out[(points[i]["id"], points[j]["id"])] = s
        return out

    def _review_add(self, scope: str | None, similarity_threshold: float,
                    add_limit: int, similarity_fn) -> list[dict]:
        """mode=add: related-but-MISSING connection suggestions (no writes)."""
        pool = self._review_pool(scope)
        if len(pool) < 2:
            return []
        points = [pool[pid] for pid in sorted(pool)]
        connected = self._connected_pairs([p["id"] for p in points])
        sim_fn = similarity_fn or self._default_pairwise_similarity
        scores = sim_fn(points) or {}

        suggestions = []
        for (a, b), s in scores.items():
            if a not in pool or b not in pool:
                continue
            if frozenset((a, b)) in connected:
                continue  # already connected — nothing to suggest
            try:
                score = float(s)
            except (TypeError, ValueError):
                continue
            if score < similarity_threshold:
                continue
            ordered = tuple(sorted((a, b)))
            suggestions.append({
                "from": ordered[0],
                "to": ordered[1],
                "suggested_relation": "IMPL",
                "reason": (
                    f"semantically related (similarity={score:.2f}) with no "
                    "existing connection — candidate for operator_action"
                ),
                "similarity": round(score, 6),
            })
        suggestions.sort(key=lambda x: (-x["similarity"], x["from"], x["to"]))
        return suggestions[:add_limit]

    def _epistemic_edges(self) -> list[dict]:
        """Every IMPL/NAND connection as {from, to, relation, via}.

        Operator-mediated (operator node, idx=0 is the source) and direct
        operator-less edges (reification rule, ontology v3.5 §8). Legacy
        operators lacking idx degrade to one entry per unordered input pair.
        """
        proj = self._get_proj()
        edges: list[dict] = []
        rows = proj.g.query(
            "MATCH (op:Point {is_operator:true})-[r:IMPL|NAND]->(p:Point) "
            "RETURN op.id, type(r), p.id, r.idx",
        ).result_set
        # Group per (operator, relation) as (idx, pid) rows — NOT keyed by
        # idx: legacy edges without the idx property all carry None, and a
        # dict keyed by None collapses them (silently dropping inputs —
        # #913 review round 1).
        by_op: dict[tuple[str, str], list[tuple]] = {}
        for op_id, rel, pid, idx in rows:
            by_op.setdefault((op_id, rel), []).append((idx, pid))
        for (op_id, rel), inputs in by_op.items():
            sources = [pid for idx, pid in inputs if idx == 0]
            if sources:
                # Fast path: idx=0 is the source; every other input (idx'd
                # or legacy None) is a target.
                src = sources[0]
                for pid in sorted({pid for idx, pid in inputs if pid != src}):
                    edges.append({"from": src, "to": pid,
                                  "relation": rel, "via": op_id})
            else:
                # Legacy operator without idx — degrade to one entry per
                # unordered input pair (deterministic: sorted ids).
                pids = sorted({pid for _, pid in inputs})
                for i in range(len(pids)):
                    for j in range(i + 1, len(pids)):
                        edges.append({"from": pids[i], "to": pids[j],
                                      "relation": rel, "via": op_id})
        rows = proj.g.query(
            "MATCH (a:Point)-[r:IMPL|NAND]->(b:Point) "
            "WHERE a.is_operator = false "
            "  AND b.is_operator = false "
            "RETURN a.id, type(r), b.id",
        ).result_set
        for a, rel, b in rows:
            edges.append({"from": a, "to": b, "relation": rel, "via": "direct"})
        return edges

    def _review_prune(self, scope: str | None, variance_threshold: float,
                      prune_limit: int) -> list[dict]:
        """mode=prune: flag illogical/stale connections (no writes).

        Entry shape: {from, to, relation, issue, suggested_action, detail}
        with issue in (contradictory, stale, contested) — a single edge may
        carry multiple issues (one entry each, deduped).
        """
        edges = self._epistemic_edges()
        if not edges:
            return []
        proj = self._get_proj()
        ids = sorted({e["from"] for e in edges} | {e["to"] for e in edges})

        # Endpoint statuses (terminal / legacy outdated flag).
        rows = proj.g.query(
            "MATCH (n:Point) WHERE n.id IN $ids "
            "RETURN n.id, n.status, coalesce(n.outdated, false)",
            params={"ids": ids},
        ).result_set
        statuses = {r[0]: r[1] for r in rows}
        outdated = {r[0]: bool(r[2]) for r in rows}
        stale_endpoint = {
            pid for pid in ids
            if statuses.get(pid) in STALE_TERMINAL_STATUSES or outdated.get(pid)
        }
        # CORRECTS successors for stale endpoints → re-point vs prune.
        successors: dict[str, str] = {}
        if stale_endpoint:
            rows = proj.g.query(
                "MATCH (s:Point)-[:CORRECTS]->(o:Point) "
                "WHERE o.id IN $ids RETURN o.id, s.id ORDER BY s.id",
                params={"ids": sorted(stale_endpoint)},
            ).result_set
            for oid, sid in rows:
                successors[oid] = sid

        # Contested claims: high posterior variance (stored EP params only —
        # an unmeasured uniform prior is NOT contested) OR an incoming NAND
        # operator edge on a LIVE point (the derived `challenged` condition,
        # ontology §5).
        contested: dict[str, dict] = {}
        rows = proj.g.query(
            "MATCH (n:Point) "
            "WHERE n.is_operator = false "
            "  AND (n.posterior_alpha IS NOT NULL OR n.ep_alpha IS NOT NULL) "
            "  AND (n.posterior_beta IS NOT NULL OR n.ep_beta IS NOT NULL) "
            "WITH n, coalesce(n.posterior_alpha, n.ep_alpha, 1.0) AS a, "
            "     coalesce(n.posterior_beta, n.ep_beta, 1.0) AS b "
            "WITH n, a, b, (a*b)/((a+b)*(a+b)*(a+b+1)) AS v "
            "WHERE a > 0 AND b > 0 AND v > $t RETURN n.id, v",
            params={"t": variance_threshold},
        ).result_set
        for pid, v in rows:
            contested[pid] = {"variance": round(v, 8),
                              "reason": "high posterior variance"}
        # #913 round-1: the derived `challenged` condition (ontology §5) is
        # a NAND edge on a LIVE point — draft/terminal endpoints are already
        # handled by stale/draft semantics and must not double-flag.
        rows = proj.g.query(
            "MATCH (op:Point {is_operator:true})-[r:NAND]->(n:Point) "
            "WHERE n.is_operator = false "
            "  AND (n.status IS NULL OR n.status = 'live') "
            "RETURN DISTINCT n.id",
        ).result_set
        for (pid,) in rows:
            contested.setdefault(pid, {"reason": "incoming NAND (challenged)"})

        # Contradiction: pairs linked by BOTH IMPL and NAND.
        pair_rels: dict[frozenset, set] = {}
        for e in edges:
            pair_rels.setdefault(frozenset((e["from"], e["to"])), set()).add(
                e["relation"])
        contradictory = {k for k, v in pair_rels.items()
                         if {"IMPL", "NAND"} <= v}

        entries: list[dict] = []
        for e in edges:
            frm, to, rel, via = e["from"], e["to"], e["relation"], e["via"]
            if frozenset((frm, to)) in contradictory:
                entries.append({
                    "from": frm, "to": to, "relation": rel,
                    "issue": "contradictory", "suggested_action": "review",
                    "detail": {"via": via, "reason": "pair is both IMPL- and NAND-linked"},
                })
            # Prefer the stale endpoint that HAS a CORRECTS successor (so
            # the actionable re-point surfaces); otherwise the first stale
            # endpoint (Qwen gate, PR #933).
            stale_pids = [p for p in (frm, to) if p in stale_endpoint]
            chosen = next((p for p in stale_pids if p in successors), None)
            if chosen is None and stale_pids:
                chosen = stale_pids[0]
            if chosen is not None:
                pid = chosen
                display_status = statuses.get(pid)
                if display_status not in STALE_TERMINAL_STATUSES:
                    # Legacy invalidate: status stayed 'live' but the
                    # outdated=true flag marks it stale — report the
                    # signal that actually made it stale.
                    display_status = "outdated" if outdated.get(pid) \
                        else (display_status or "unknown")
                entries.append({
                    "from": frm, "to": to, "relation": rel,
                    "issue": "stale",
                    "suggested_action": "re-point" if pid in successors else "prune",
                    "detail": {
                        "via": via, "stale_endpoint": pid,
                        "status": display_status,
                        **({"successor": successors[pid]} if pid in successors else {}),
                    },
                })
            for pid in (frm, to):
                if pid in contested:
                    detail = {"via": via, "contested_endpoint": pid,
                              "reason": contested[pid]["reason"]}
                    if "variance" in contested[pid]:
                        detail["variance"] = contested[pid]["variance"]
                    entries.append({
                        "from": frm, "to": to, "relation": rel,
                        "issue": "contested", "suggested_action": "review",
                        "detail": detail,
                    })
                    break  # one contested entry per edge

        # Dedupe + deterministic order: contradictory > stale > contested.
        seen = set()
        unique = []
        for e in entries:
            k = (e["issue"], e["from"], e["to"], e["relation"])
            if k in seen:
                continue
            seen.add(k)
            unique.append(e)
        issue_order = {"contradictory": 0, "stale": 1, "contested": 2}
        unique.sort(key=lambda x: (issue_order[x["issue"]], x["from"],
                                   x["to"], x["relation"]))

        # Optional scope narrowing: keep entries touching the scoped pool.
        # An EMPTY scoped pool means "nothing in scope" (retrieval failure or
        # zero hits) — filter to [] then, never fall back to the whole-graph
        # list (fail quiet, consistent with mode=add; #913 review round 1).
        if scope:
            pool = self._review_pool(scope)
            unique = [e for e in unique
                      if e["from"] in pool or e["to"] in pool]
        return unique[:prune_limit]

    # ── EP Belief Propagation (#6908) ────────────────────────────

    def _get_ep(self):
        if self._ep is None:
            from .ep import TortoiseEP
            self._ep = TortoiseEP(self._get_proj())
        return self._ep

    # ── Dreaming (#85) ──────────────────────────────────────────────

    def _get_dreamer(self):
        """Lazy-init the Dreamer (thread-safe)."""
        if self._dreamer is None:
            from .dream import Dreamer
            self._dreamer = Dreamer(self)
        return self._dreamer

    # ── Epic 903-C5 (#1243): non-convergence retention ──────────────

    def _register_failed_attempt(self, roots: set[str]) -> None:
        """Record a failed (non-converged) attempt for the given dirty roots.

        W4 retention: the roots STAY dirty (retry) until the attempt cap;
        a capped root is dropped from the dirty set and surfaced as
        ``stale_unresolved`` with its attempt count + backoff state. Backoff
        is exponential in attempts (2**n seconds, capped) — recorded as
        state, never slept (tests inject a fake clock).
        """
        now = self._retry_clock()
        for root in roots:
            if root not in self._dirty_roots:
                continue  # already resolved/dropped by another path
            attempts = self._retry_attempts.get(root, 0) + 1
            if attempts > self.retry_attempt_cap:
                self._dirty_roots.discard(root)
                self._retry_attempts.pop(root, None)
                self._retry_backoff_until.pop(root, None)
                self._stale_unresolved[root] = {
                    "attempts": attempts,
                    "last_attempt_at": now,
                    "backoff_state": "capped",
                    "reason": "non_converged",
                }
                # #1163: a capped root is dropped from the dirty set — its
                # GRAPH flag must go too, or a fresh SDK would rehydrate it
                # and retry forever (the cap is meaningless cross-process).
                try:  # noqa: SIM105
                    self._get_proj().g.query(
                        "MATCH (n:Point {id:$id}) "
                        "SET n.ep_dirty = null, n.ep_dirty_at = null",
                        params={"id": root},
                    )
                except Exception:  # noqa: BLE001, RUF100
                    pass
            else:
                self._retry_attempts[root] = attempts
                self._retry_backoff_until[root] = now + min(
                    2 ** attempts, 3600)
                # P2-review (#1243): a re-dirtied root re-entering the retry
                # cycle must drop its OLD capped record — C7 aggregation must
                # never see a root in both retry_attempts and
                # stale_unresolved at once.
                self._stale_unresolved.pop(root, None)

    def _reset_retry_state(self, roots: set[str]) -> None:
        """Clear attempt/backoff state for roots that CONVERGED."""
        for root in roots:
            self._retry_attempts.pop(root, None)
            self._retry_backoff_until.pop(root, None)
            self._stale_unresolved.pop(root, None)

    def _is_backed_off(self, root: str) -> bool:
        """True when the root is inside its backoff window (retry later).

        P2-review (#1243): this is a CONSUMER helper for the 903-C8 hosted
        wiring (backoff enforcement point — a backed-off root is skipped,
        not attempt-penalized). The embedded dream paths do NOT enforce it
        today: backoff is recorded-not-slept by contract; attempts are
        counted per actual run. Do not wire it into _dream_local without
        deciding the skip-vs-penalize semantics with C8.
        """
        until = self._retry_backoff_until.get(root)
        if until is None:
            return False
        return self._retry_clock() < until

    def _prune_nonexistent_dirty_roots(self) -> None:
        """Drop dirty roots whose Point no longer exists (deleted-point
        zombies — P2-review #1243). Query once, bounded by the dirty set."""
        if not self._dirty_roots:
            return
        proj = self._get_proj()
        ids = list(self._dirty_roots)
        rows = proj.g.query(
            "MATCH (n:Point) WHERE n.id IN $ids RETURN n.id",
            params={"ids": ids},
        ).result_set
        existing = {r[0] for r in rows}
        gone = self._dirty_roots - existing
        if gone:
            self._dirty_roots -= gone
            for g in gone:
                self._retry_attempts.pop(g, None)
                self._retry_backoff_until.pop(g, None)
                self._stale_unresolved.pop(g, None)

    def dream_health_check(self) -> dict:
        """Epic 903-C7 (#1245): the zero-output silent-death alarm +
        observability record (embedded evaluator — call-triggered, no
        daemon per #176; the hosted surface is /v1/dream/health).

        Alarm (COUNTER-based, never wall-clock — DE2E-8): stale backlog
        exists (dirty roots non-empty) AND the last dream pass produced
        zero output (affected claims == 0). A dead/silently-skipped dreamer
        looks identical to "nothing to do" — this is the A8 detection.
        Positive-control semantics: a backlog with REAL output is healthy —
        the alarm MUST NOT fire.
        """
        # #1163: hydrate graph-persisted dirty roots first so the hosted
        # health surface sees cross-process backlog (a fresh request-scoped
        # SDK would otherwise report a zero backlog over dirty graphs).
        self._hydrate_dirty_roots()
        backlog = len(self._dirty_roots)
        last_output = self._dream_metrics.get("last_pass_output", 0)
        last_pass_at = self._dream_metrics.get("last_pass_at")
        alarm = (backlog > 0 and last_output == 0
                 and last_pass_at is not None)
        state = self.dream_health_state()
        stale_backlog = sum(  # noqa: F841
            1 for _ in self._dirty_roots)  # non-empty check below
        return {
            "alarm_verdict": alarm,
            "alarm_reason": (
                "zero_output_with_backlog" if alarm else "ok"),
            "stale_backlog": backlog,
            "last_pass_at": last_pass_at,
            "last_pass_output": last_output,
            "coverage_pct": self._metrics_coverage(),
            "failure_rate": (
                self._dream_metrics["failure_count"]
                / max(self._dream_metrics["pass_count"], 1)),
            "operator_counts": getattr(
                self._get_dreamer(), "_last_operator_count", 0) or 0,
            "per_mode_counts": dict(
                self._dream_metrics.get("per_mode_counts", {})),
            "region_attempts": state["retry_attempts"],
            "warm_skipped_updates": state["warm_skipped_updates"],
        }

    def _metrics_coverage(self) -> float:
        """Coverage % = fraction of live non-operator claims with a
        lastDreamedAt stamp (graph-wide)."""
        try:
            proj = self._get_proj()
            total = proj.g.query(
                "MATCH (n:Point) "
                "WHERE (n.is_operator IS NULL OR n.is_operator = false) "
                "AND (n.status IS NULL OR n.status <> 'draft') "
                "RETURN count(n)"
            ).result_set
            stamped = proj.g.query(
                "MATCH (n:Point) "
                "WHERE (n.is_operator IS NULL OR n.is_operator = false) "
                "AND (n.status IS NULL OR n.status <> 'draft') "
                "AND n.lastDreamedAt IS NOT NULL RETURN count(n)"
            ).result_set
            n_total = int(total[0][0]) if total else 0
            n_stamped = int(stamped[0][0]) if stamped else 0
            return (n_stamped / n_total) if n_total else 0.0
        except Exception:
            return 0.0

    def _record_dream_metrics(self, result: dict, mode: str) -> None:
        """Per-pass observability record (C7) — called by the dream adapters."""
        from datetime import datetime, timezone
        self._dream_metrics["last_pass_at"] = datetime.now(
            timezone.utc).isoformat()  # noqa: UP017
        self._dream_metrics["last_pass_output"] = len(
            result.get("affected_claims", []))
        self._dream_metrics["last_pass_mode"] = result.get("mode", mode)
        counts = self._dream_metrics["per_mode_counts"]
        counts[mode] = counts.get(mode, 0) + 1
        self._dream_metrics["pass_count"] += 1
        if not result.get("converged", result.get("converged_all", True)):
            self._dream_metrics["failure_count"] += 1

    def dream_health_state(self) -> dict:
        """C5-emitted region_attempts record (consumed by 903-C7's health
        surface): per-root attempt count + backoff state + stale_unresolved.
        """
        # P2-review (#1243): timestamp fields are _monotonic() BASE
        # (process-local seconds, meaningless across restart/process) —
        # C7's hosted health surface must not present them as wall-clock.
        dreamer = self._get_dreamer()
        return {
            "clock": "monotonic",
            "retry_attempts": dict(self._retry_attempts),
            "retry_backoff_until": dict(self._retry_backoff_until),
            "stale_unresolved": dict(self._stale_unresolved),
            # Epic 903-C4 (#1242): DE2E-6b cost metric — censored-update
            # count from the last dream pass (C7 surfaces it; never gated).
            "warm_skipped_updates": getattr(
                dreamer, "_last_warm_skipped", 0) or 0,
        }

    def _ep_epoch(self) -> int:
        """Current graph-wide EP epoch (the :EpMeta node's ep_version).

        0 on a graph that has never dirtied EP. #1163: the epoch is the
        ordering stamp for dirty markings AND the multi-process stale-run
        guard — see ``TortoiseEP._flush_cache``."""
        try:
            rows = self._get_proj().g.query(
                "MATCH (m:EpMeta) RETURN m.ep_version").result_set
            if not rows:
                return 0
            return int(rows[0][0])
        except (TypeError, ValueError):
            return 0

    def _hydrate_dirty_roots(self) -> set[str]:
        """Merge graph-persisted dirty roots (#1163) into the in-memory set.

        The graph (``n.ep_dirty = true``) is the cross-process source of
        truth; ``_dirty_roots`` is the hot-path mirror. A fresh SDK (HTTP
        request scope, hosted dream worker) hydrates here so persisted dirty
        state survives request/process boundaries — the #395 local-EP
        no-arg path then works over HTTP. Returns the roots newly loaded
        from the graph (empty when the graph is clean or the in-memory set
        already covers them). Best-effort: a hydration failure never breaks
        a dream.
        """
        if self._dirty_roots:
            return set()
        try:
            rows = self._get_proj().g.query(
                "MATCH (n:Point) WHERE n.ep_dirty = true RETURN n.id"
            ).result_set
            loaded = {r[0] for r in rows}
            self._dirty_roots |= loaded
            return loaded
        except Exception:  # noqa: BLE001, RUF100
            return set()

    def _sweep_dirty_roots(self, affected: set[str],
                           run_ep: int | None = None) -> None:
        """Sweep a converged pass's affected claims from BOTH the in-memory
        dirty set and the graph flags (#1163).

        The graph sweep is epoch-guarded: a flag stamped at a NEWER epoch
        than ``run_ep`` (the pass's world-view snapshot) is never cleared —
        a concurrent process re-marked it dirty mid-run and its marking
        must survive. ``run_ep=None`` (single-process callers without a
        snapshot) clears unconditionally.
        """
        self._dirty_roots -= affected
        self._reset_retry_state(affected)
        if not affected:
            return
        ids = list(affected)
        if run_ep is None:
            self._get_proj().g.query(
                "MATCH (n:Point) WHERE n.id IN $ids AND n.ep_dirty = true "
                "SET n.ep_dirty = null, n.ep_dirty_at = null",
                params={"ids": ids},
            )
        else:
            self._get_proj().g.query(
                "MATCH (n:Point) WHERE n.id IN $ids AND n.ep_dirty = true "
                "AND n.ep_dirty_at <= $run_ep "
                "SET n.ep_dirty = null, n.ep_dirty_at = null",
                params={"ids": ids, "run_ep": run_ep},
            )

    def _reverse_bfs_neighbors(self, proj, point_ids: list[str]
                               ) -> tuple[list[str], list[str]]:
        """1-hop reverse-BFS from ``point_ids``: operators targeting them
        (reverse of operator→point), then the claims those operators target
        (1-hop forward). Shared by ``_mark_dirty`` (post-write marking) and
        ``delete_point`` (pre-delete neighbor capture, #1916) so the
        traversal can never drift between the two — a change to the BFS
        shape updates both call sites in lockstep. Returns ``(op_ids,
        claim_ids)``."""
        rows = proj.g.query(
            "MATCH (op:Point {is_operator:true})-[:IMPL|NAND]->(p:Point) "
            "WHERE p.id IN $ids RETURN DISTINCT op.id",
            params={"ids": list(point_ids)},
        ).result_set
        op_ids = [r[0] for r in rows]
        claim_ids: list[str] = []
        if op_ids:
            rows = proj.g.query(
                "MATCH (op:Point {is_operator:true})-[:IMPL|NAND]->(c:Point) "
                "WHERE op.id IN $oids RETURN DISTINCT c.id",
                params={"oids": op_ids},
            ).result_set
            claim_ids = [r[0] for r in rows]
        return op_ids, claim_ids

    def _mark_dirty(self, point_ids: list[str]) -> None:
        """Mark claims whose confidence is now stale after a write.

        1-hop reverse BFS (#85 contract): from the mutated point, collect the
        operators that target it, then the claims those operators target.
        The dream expands to max_hops=2 for full propagation — do not reduce
        the dream's max_hops below 2 without expanding this marking.

        #1163 (multi-process EP): the marking is GRAPH-PERSISTED — the
        mutated points + reverse-BFS claims get ``ep_dirty = true`` with an
        ``ep_dirty_at`` ordering stamp, and the graph-wide ``:EpMeta``
        ``ep_version`` epoch advances (every write that dirties EP bumps it).
        The in-memory ``_dirty_roots`` set stays as the hot-path mirror; any
        process (fresh request-scoped SDK) hydrates from the graph.
        """
        # #1375: every write that dirties EP also invalidates the degraded-
        # fallback corpus snapshot (covers create/update/supersede/retract/
        # operator/mitigation/delete/ingest/dream write surfaces in one hook).
        try:
            from tortoise.fallback_snapshot import _store as _fb_store, snapshot_key  # noqa: I001
            _fb_store.invalidate(
                snapshot_key(self._get_proj(), getattr(self, "_namespace", None)),
            )
        except Exception:  # noqa: BLE001, RUF100
            pass
        if not point_ids:
            return
        # The mutated points themselves are always dirty (their baseline
        # priors / properties changed).
        self._dirty_roots.update(point_ids)
        proj = self._get_proj()
        # #1163: advance the graph-wide EP epoch FIRST — the new value is the
        # ordering stamp for this write's dirty markings (and the stale-run
        # guard's discriminator).
        rows = proj.g.query(
            "MERGE (m:EpMeta) "
            "SET m.ep_version = coalesce(m.ep_version, 0) + 1 "
            "RETURN m.ep_version"
        ).result_set
        ep_version = int(rows[0][0]) if rows else 1
        # Operators targeting the mutated points, then the claims those
        # operators target (shared 1-hop reverse-BFS — delete_point's
        # pre-delete neighbor capture uses the same helper, #1916).
        _op_ids, claim_ids = self._reverse_bfs_neighbors(proj, point_ids)
        dirty_ids = list(point_ids) + claim_ids
        # #1163: persist the dirty markings (points + reverse-BFS claims) so
        # any process/request can see them — the graph is the source of
        # truth, the in-memory set above is the mirror.
        proj.g.query(
            "UNWIND $ids AS pid "
            "MATCH (n:Point {id: pid}) "
            "SET n.ep_dirty = true, n.ep_dirty_at = $ep",
            params={"ids": dirty_ids, "ep": ep_version},
        )
        self._dirty_roots.update(dirty_ids)

    #: Accepted explicit dream modes (epic 903-C6 #1244, I1 precedence table).
    _DREAM_MODES = ("local", "stale-first", "full")
    #: Auto-select heuristic (I1 rule 3 — mode=None, full=False): graphs
    #: with fewer live operators than this are "small" → full mode (J3 — no
    #: reason to window a graph this small; one pass drains it). Simple,
    #: documented count-query heuristic: ``MATCH (n:Point
    #: {is_operator:true}) RETURN count(n)`` — cheap and deterministic.
    _AUTO_FULL_OPERATOR_THRESHOLD = 50

    def dream(self, dirty_only: bool = True, full: bool = False,
              max_hops: int = 2,
              require_calibration: bool | None = None,
              stamp_dreamed_at: bool = True,
              mode: str | None = None,
              budget: int | None = None,
              warm_start: bool = True) -> dict:
        """Run EP stabilization (#85) with strategy auto-selection (epic 903).

        One call, three strategies (I1): ``local`` (write-triggered refresh
        of the dirty roots), ``stale-first`` (scheduler: staleness-ranked
        window ∪ dirty roots, bounded per-pass), ``full`` (whole-graph
        stabilization). Users never pick — the router picks, unless an
        explicit ``mode`` overrides.

        Args:
            dirty_only: sugar (default True) — DEPRECATED (epic 903-C6,
                  #1244): kept for backward compatibility, currently IGNORED
                  by the mode router (auto-select and explicit mode govern).
                  Historical semantics (dirty_only=False = suppress local
                  dreaming) are NOT preserved — with no dirty roots the
                  router may route small graphs to full. Callers wanting the
                  old suppression should pass mode="local" explicitly.
            full: sugar — whole-graph stabilization. Maps to
                  mode="full" ONLY when ``mode`` is None (I1 rule 2).
            max_hops: EP subgraph expansion (keep ≥2 — contract with
                      _mark_dirty).
            require_calibration: gate the EP run on calibration state —
                  raises CalibrationError when evidence-kind points are
                  uncalibrated (#1157). None (default) resolves to the shared
                  posture, TORTOISE_EP_REQUIRE_CALIBRATION (default True —
                  fail-closed, post-#344; set it to "0" to opt out). Dream
                  WRITES n.confidence,
                  so it must never silently run uncalibrated EP (#7478).
            stamp_dreamed_at: when False, the pass never writes
                  lastDreamedAt (epic 903-C2) — the read-triggered
                  lazy-consistency paths (get_confidence /
                  compute_confidence) pass False so a READ never
                  moves the freshness signal that the 903-C4
                  stale-first scheduler ranks on. Default True for
                  write-triggered/operator-initiated dreams.
            mode: explicit strategy override ∈ {"local", "stale-first",
                  "full"} — WINS over the full/dirty_only sugar (I1 rule 1);
                  unknown mode → ValueError. None → auto-select by context
                  (I1 rule 3): write context (dirty roots) → local;
                  small-graph/first-run → full; otherwise → local. The
                  scheduled path calls with mode="stale-first" explicitly
                  (903-C8 wiring) — auto never silently picks the window.
            warm_start (epic 903-C4, #1242): True (default) — dream passes
                  reuse the graph-persisted edge messages with gamma-skip
                  censoring. False → a from-scratch pass (the fast path /
                  oracle captures, e.g. F4's frozen ground truth, which must
                  be bit-reproducible without censoring).
            budget: per-pass operator cap. None → the existing 200-op
                  selector cap (every pass bounded by construction); an
                  explicit budget overrides it; budget=0 → no-op result
                  (not an error — exact key-set, all zeros, no stamps).
                  An explicit budget a full pass cannot satisfy raises
                  BudgetExceededError (tortoise/exceptions.py) — full is
                  complete-in-one-pass by contract; stale-first never
                  raises (deferral is its design).

        Returns (per-mode key-sets, pinned per I1 — exact):
            local: {mode, iterations, converged, affected_claims,
                    budget_used, coverage} — converged retained for the
                    dirty-root logic (roots covered by the affected set are
                    cleared; roots outside stay dirty for a later dream).
            stale-first: {mode, batches, converged_all, converged,
                    affected_claims, budget_used, coverage} — converged =
                    this pass's window-level convergence (drives the
                    dirty-root logic: a failed pass clears nothing, W4);
                    budget_used = distinct operators processed after dedup.
            full: {mode, batches, total_affected, converged_all,
                    budget_used, coverage, scanned_count} — total_affected
                    = reachable claims only; scanned_count = operator-less
                    claims trivially stamped by the scan path (DE2E-1).

        Coverage semantics per mode (I1): local → affected /
        window-reachable (live non-operator claims in the anchors' BFS
        closure); stale-first → affected / remaining-stale-before-pass (the
        pre-pass window); full → total_affected / reachable non-operator
        claims (claims with ≥1 IMPL|NAND edge; operator-less claims are
        reported via scanned_count, not the denominator). Coverage is 0.0
        when the denominator is empty (no-op / zero-operator graph).
        """
        # #1157: gate BEFORE any work — dream is an EP write surface
        # (persists n.confidence); same posture as compute_confidence.
        if require_calibration is None:
            require_calibration = _ep_require_calibration_default()
        if require_calibration:
            self._ensure_calibrated("dream")
        # #1163 (multi-process EP): merge graph-persisted dirty roots into
        # the in-memory set so a fresh SDK (HTTP request scope, hosted dream
        # worker) runs the same local EP the writing process would (the W1
        # write-context rule below then resolves to local). Best-effort and
        # skipped when the in-memory set already covers the state.
        self._hydrate_dirty_roots()
        resolved = self._resolve_dream_mode(mode, full)
        if budget is not None and budget <= 0:
            # I1: budget=0 → no-op result, not an error. No EP, no stamps.
            return self._dream_noop(resolved)
        dreamer = self._get_dreamer()
        if resolved == "local":
            return self._dream_local(dreamer, max_hops, stamp_dreamed_at,
                                     budget, warm_start)
        if resolved == "stale-first":
            return self._dream_stale_first(dreamer, max_hops, budget,
                                           warm_start)
        return self._dream_full(dreamer, max_hops, stamp_dreamed_at, budget,
                                warm_start)

    def _resolve_dream_mode(self, mode: str | None, full: bool) -> str:
        """I1 precedence table (epic 903-C6 #1244).

        1. Explicit ``mode`` (∈ {"local", "stale-first", "full"}) WINS over
           the full/dirty_only sugar; unknown mode → ValueError.
        2. ``full=True`` maps to mode="full" ONLY when ``mode`` is None.
        3. mode=None + full=False → auto-select by context (write → local;
           scheduled → stale-first — via the scheduler's explicit mode, not
           here; small-graph/first-run → full; otherwise → local).
        """
        if mode is not None:
            if mode not in self._DREAM_MODES:
                raise ValueError(
                    f"unknown dream mode {mode!r} — expected one of "
                    + ", ".join(repr(m) for m in self._DREAM_MODES)
                )
            return mode
        if full:
            return "full"
        return self._auto_dream_mode()

    def _auto_dream_mode(self) -> str:
        """Auto-selection (I1 rule 3): write context → local; small-graph /
        first-run → full; otherwise → local (the safe bounded default).

        Context detection hooks: the scheduler path is called with
        mode="stale-first" explicitly (903-C8 wiring), so the SDK only
        needs the write-context rule (dirty roots present → the write path
        ran _mark_dirty → local, W1) and the small-graph rule (total
        operators below ``_AUTO_FULL_OPERATOR_THRESHOLD`` → full, J3).
        """
        if self._dirty_roots:
            # Write context (W1): the dirty roots ARE the local window.
            # D2-5 structural guarantee: the write path can never reach
            # window/full modes from here.
            return "local"
        proj = self._get_proj()
        rows = proj.g.query(
            "MATCH (n:Point {is_operator:true}) RETURN count(n)"
        ).result_set
        n_operators = int(rows[0][0])
        if n_operators < self._AUTO_FULL_OPERATOR_THRESHOLD:
            # Small graph / first run (J3): nothing worth windowing — one
            # full pass drains it.
            return "full"
        return "local"

    def _dream_noop(self, mode: str) -> dict:
        """budget ≤ 0 → no-op result with the mode's EXACT key-set (I1:
        "budget=0 → no-op result (not an error)"). No EP, no stamps;
        converged flags are vacuously True (matches dream_window's budget=0
        contract)."""
        if mode == "local":
            return {"mode": "local", "iterations": 0, "converged": True,
                    "affected_claims": [], "budget_used": 0, "coverage": 0.0}
        if mode == "stale-first":
            return {"mode": "stale-first", "batches": 0,
                    "converged_all": True, "converged": True,
                    "affected_claims": [], "budget_used": 0, "coverage": 0.0}
        return {"mode": "full", "batches": 0, "total_affected": 0,
                "converged_all": True, "budget_used": 0, "coverage": 0.0,
                "scanned_count": 0}

    def _dream_local(self, dreamer, max_hops: int, stamp_dreamed_at: bool,
                     budget: int | None, warm_start: bool = True) -> dict:
        """Local mode (W1): EP over the dirty roots. I1 local key-set.

        Dirty-root clearing keeps the #395-era retention semantics (roots in
        the affected set are cleared; roots OUTSIDE the dreamed subgraph
        stay dirty for a later dream; an empty affected set subtracts
        nothing) — the non-convergence divergence is owned by 903-C5.
        """
        anchors = list(self._dirty_roots)
        if not anchors:
            return {"mode": "local", "iterations": 0, "converged": True,
                    "affected_claims": [], "budget_used": 0, "coverage": 0.0}
        result = dreamer.dream(
            anchors, max_hops=max_hops,
            stamp_dreamed_at=stamp_dreamed_at,
            op_cap=budget if budget is not None else 200,
            warm_start=warm_start,
        )
        affected = set(result.get("affected_claims", []))
        # Epic 903-C5 (#1243) — W4 retention (the A2-bug fix): affected
        # claim-roots are cleared ONLY when the run CONVERGED. A failed run
        # keeps them dirty (retry) and registers the attempt — the old
        # unconditional ``-= affected`` dropped non-converged roots, leaving
        # regions permanently stale. Capped roots are surfaced as
        # ``stale_unresolved`` (region_attempts record, consumed by 903-C7).
        if result.get("converged", False) and not getattr(
                dreamer, "_last_flush_skipped", False):
            # #1163: the sweep is epoch-guarded — a concurrent process's
            # newer marking (ep_dirty_at > the pass's snapshot) survives.
            self._sweep_dirty_roots(
                affected, run_ep=getattr(dreamer, "_last_run_ep_version", None))
            # P2-review (#1243): prune deleted-point zombies — delete_point
            # marks the deleted id dirty (reverse-BFS finds no edges), so a
            # nonexistent Point can sit in _dirty_roots forever, pinning
            # auto-select to local and inflating the W5 backlog alarm.
            self._prune_nonexistent_dirty_roots()
        elif not result.get("converged", False):
            self._register_failed_attempt(affected)
        reachable = self._window_closure(anchors, max_hops)
        coverage = (len(affected) / len(reachable)) if reachable else 0.0
        result["coverage"] = coverage
        result["budget_used"] = getattr(
            dreamer, "_last_operator_count", 0) or 0
        result["mode"] = "local"
        self._record_dream_metrics(result, "local")
        return result

    def _dream_stale_first(self, dreamer, max_hops: int,
                           budget: int | None,
                           warm_start: bool = True) -> dict:
        """Stale-first mode (W2): staleness-ranked window ∪ dirty roots.

        I1 key-set (I6→I1 mapping): ``operators_deduped`` is dropped
        (``budget_used`` already = distinct operators after dedup) and
        ``coverage`` is added = affected / remaining-stale-before-pass (the
        claim-hop closure of the PRE-PASS window recorded by dream_window —
        the pre-pass value because stamps written by the pass reorder the
        ranking; the closure guarantees 0 ≤ coverage ≤ 1 and a converged
        full-window pass reports 1.0). The dirty-root logic is driven from
        the window-level ``converged`` flag: a converged pass clears the
        affected roots; a failed pass clears nothing (W4 retention —
        non-converged regions reselect via the window union).
        """
        result = dreamer.dream_window(budget=budget, max_hops=max_hops,
                                     warm_start=warm_start)
        window = getattr(dreamer, "_last_window", []) or []
        affected = set(result.get("affected_claims", []))
        if result.get("converged") and not getattr(
                dreamer, "_last_flush_skipped", False):
            # #1163: guarded sweep (a concurrent process's newer marking
            # survives — the stale-run flush guard stands the pass down).
            self._sweep_dirty_roots(
                affected, run_ep=getattr(dreamer, "_last_run_ep_version", None))
        elif not result.get("converged"):
            # Epic 903-C5 (#1243): a failed window pass registers attempts on
            # the retained dirty roots (retention already keeps them via the
            # W2 union — the attempt cap bounds the retry loop).
            self._register_failed_attempt(
                set(self._dirty_roots) & affected)
        reachable = self._window_closure(window, max_hops)
        coverage = (len(affected) / len(reachable)) if reachable else 0.0
        result["coverage"] = coverage
        # I6→I1 mapping: drop the scheduler-internal operators_deduped
        # (budget_used already = distinct operators after dedup).
        result.pop("operators_deduped", None)
        self._record_dream_metrics(result, "stale-first")
        return result

    def _dream_full(self, dreamer, max_hops: int, stamp_dreamed_at: bool,
                    budget: int | None, warm_start: bool = True) -> dict:
        """Full mode (J3): whole-graph stabilization. I1 full key-set.

        An explicit budget the graph cannot satisfy raises
        BudgetExceededError inside dream_all (full is complete-in-one-pass
        by contract — never silently truncate an explicit budget).
        """
        result = dreamer.dream_all(
            max_hops=max_hops, stamp_dreamed_at=stamp_dreamed_at,
            budget=budget, warm_start=warm_start,
        )
        # P2-review (#1243): a CONVERGED full pass resolves every reachable
        # region — clear the affected roots' retry state so a later failed
        # window pass cannot surface an already-converged region as
        # stale_unresolved, and prune deleted-point zombies. #1163: the
        # guarded sweep stands down when the stale-run guard fired (a
        # concurrent process re-marked dirty mid-pass).
        if result.get("converged_all", False) and not getattr(
                dreamer, "_last_flush_skipped", False):
            affected = set(result.get("affected_claims", []))
            self._sweep_dirty_roots(
                affected, run_ep=getattr(dreamer, "_last_run_ep_version", None))
            self._prune_nonexistent_dirty_roots()
        reachable = self._reachable_claim_count()
        coverage = ((result["total_affected"] / reachable)
                    if reachable else 0.0)
        result["coverage"] = coverage
        result["mode"] = "full"
        self._record_dream_metrics(result, "full")
        return result

    def _window_closure(self, window: list[str], max_hops: int) -> set[str]:
        """Claim-hop closure of a dream window (I1 coverage denominator:
        the claims a pass COULD reach from its window).

        Mirrors ``TortoiseEP._affected_claims``'s batched per-hop expansion
        exactly (operators are transparent bridges — one claim-hop per BFS
        level; operator-less direct edges via #888 W5 semantics; #780 draft
        exclusion) so the denominator matches the pass's universe:
        affected ⊆ closure, and a converged pass over its whole window
        reports coverage = 1.0. The window members themselves are reachable
        at 0 claim-hops (an operator-less isolated claim is its own window).

        Two batched queries per hop (operator-bridge + direct-edge), seeded
        with the whole window — cheap for the scheduler's large windows
        (2 × max_hops queries total), unlike a per-seed BFS.
        """
        proj = self._get_proj()
        live = "(n.status IS NULL OR n.status <> 'draft')"
        rows = proj.g.query(
            "MATCH (n:Point) WHERE n.id IN $ids "
            "AND (n.is_operator IS NULL OR n.is_operator = false) "
            f"AND {live} RETURN n.id",
            params={"ids": list(window)},
        ).result_set
        closure: set[str] = {r[0] for r in rows}
        frontier = list(closure)
        hops = 0
        while frontier and (max_hops is None or hops < max_hops):
            hops += 1
            new_frontier: list[str] = []
            if frontier:
                # Operator-mediated bridges (op_type OR is_operator — legacy
                # operator detection parity, #943). Never hops through drafts.
                nbr_rows = proj.g.query(
                    "MATCH (n:Point)-[r]-(op:Point)-[r2]-(m:Point) "
                    "WHERE n.id IN $ids AND m.id <> n.id "
                    "AND (op.is_operator = true OR op.op_type IS NOT NULL) "
                    "AND (op.status IS NULL OR op.status <> 'draft') "
                    "AND (m.status IS NULL OR m.status <> 'draft') "
                    "RETURN DISTINCT n.id, m.id",
                    params={"ids": frontier},
                ).result_set
                for _nid, mid in nbr_rows:
                    if mid not in closure:
                        closure.add(mid)
                        new_frontier.append(mid)
                # Operator-less direct IMPL|NAND edges between plain Points
                # (#888 W5). Draft endpoints never propagate (#780).
                dir_rows = proj.g.query(
                    "MATCH (a:Point)-[r:IMPL|NAND]-(b:Point) "
                    "WHERE a.id IN $ids AND b.id <> a.id "
                    "AND (a.status IS NULL OR a.status <> 'draft') "
                    "AND (b.status IS NULL OR b.status <> 'draft') "
                    "AND a.is_operator = false AND a.op_type IS NULL "
                    "AND b.is_operator = false AND b.op_type IS NULL "
                    "RETURN DISTINCT a.id, b.id",
                    params={"ids": frontier},
                ).result_set
                for _aid, bid in dir_rows:
                    if bid not in closure:
                        closure.add(bid)
                        new_frontier.append(bid)
            frontier = new_frontier
        return closure

    def _reachable_claim_count(self) -> int:
        """Live non-operator claims reachable from EP (full-mode coverage
        denominator: total_affected / reachable non-operator claims).

        Reachable = claims participating in ≥1 IMPL|NAND edge (operator-
        mediated or direct) — the EP-run universe. Operator-less claims with
        no edges are reported separately via ``scanned_count`` and are NOT
        in the denominator (EP cannot reach them; DE2E-1 keeps
        total_affected reachable-only).

        P2-review (#1244): the FAR endpoint is draft-filtered too — a live
        claim whose only IMPL edge connects to a draft cannot be affected by
        EP (#780: an operator needs ≥2 live endpoints; a draft neighbor is
        excluded), so it must not inflate the denominator (else a fully
        converged pass reports coverage < 1.0 forever).
        """
        proj = self._get_proj()
        rows = proj.g.query(
            "MATCH (n:Point)-[r:IMPL|NAND]-(m:Point) "
            "WHERE (n.is_operator IS NULL OR n.is_operator = false) "
            "AND (n.status IS NULL OR n.status <> 'draft') "
            "AND (m.status IS NULL OR m.status <> 'draft') "
            "RETURN count(DISTINCT n)"
        ).result_set
        return int(rows[0][0])

    def _select_subgraph(self, anchors: list[str], max_hops: int = 1,
                         rel_filter: str = "IMPL|NAND",
                         direction: str = "both",
                         include_draft: bool = False) -> tuple[list[str], set[tuple[str, str, str]]]:
        """BFS subgraph selection from anchor Points — A9 return contract.

        Delegates to the shared _bfs_select_operators in tortoise.analyze.
        Returns ``(operator_ids, factor_anchors)`` — the operator Point IDs
        AND the direct-edge factor anchors ((src, tgt, type) descriptors)
        discovered in the traversal (epic #902 §5.6: a direct-edge-only
        subgraph yields ZERO operators but a non-empty direct-factor
        selection; the ≥2-live-endpoints derived-liveness predicate applies
        to operator nodes, GATE-2 Q3). With include_draft=False (default,
        #780) draft anchors, operators and frontier points are excluded.
        """
        from .analyze import _bfs_select_operators
        proj = self._get_proj()
        ops, anchors_out = _bfs_select_operators(proj, anchors, max_hops=max_hops,
                                                 rel_filter=rel_filter, direction=direction,
                                                 include_draft=include_draft)
        return list(ops), anchors_out

    def compute_confidence(self, factors=None, evidence=None,
                           anchors: list[str] | None = None,
                           max_hops: int = 1,
                           rel_filter: str = "IMPL|NAND",
                           direction: str = "both",
                           require_calibration: bool | None = None,
                           recency_decay: float | None = None) -> dict:
        """Compute confidence via EP belief propagation. Returns {iterations, converged, confidences}.

        #780: draft Points/operators are EXCLUDED by default (EP only runs
        over live claims); there is no include_draft escape hatch on this
        surface — call TortoiseEP.run(include_draft=True) directly for
        legacy behavior.

        Args:
            factors: operator IDs (list[str]) or factor tuples. If None, BFS-
                selects from anchors or runs LOCAL EP over the dirty roots
                (#395 delta C — no-arg no longer scans the whole graph).
            evidence: optional {claim_id: (alpha, beta)} priors — merged into
                a CALL-SCOPED copy for the run (never mutated into the SDK's
                persistent evidence, #395).
            anchors: list of Point IDs for BFS subgraph selection.
            max_hops: BFS expansion depth when using anchors (default 1),
                threaded to the run so selection depth == run depth (#395
                AC8). The no-arg path always uses the exact affected closure
                (max_hops=None) over the dirty roots.
            rel_filter: edge types for BFS — "IMPL", "NAND", or "IMPL|NAND" (default).
            direction: IMPL edge traversal direction — "incoming", "outgoing", or "both" (default).
            require_calibration: fail-closed gate — raises CalibrationError
                when evidence points are uncalibrated. None (default) resolves
                to the shared TORTOISE_EP_REQUIRE_CALIBRATION posture
                (default "1" → True fail-closed, post-#344). Pass False
                explicitly to opt out and run EP on topology alone (the #7478
                degenerate case — do not do this silently).
            recency_decay: optional recency decay factor (default 0.95 from TORTOISE_EP_RECENCY_DECAY).
                T0 sources exempt; lower tiers get gentle decay. 1.0 = no decay.

        Precedence: factors > anchors > no-arg local EP over the dirty roots.
        """
        proj = self._get_proj()
        ep = self._get_ep()
        # Hydrate evidence from graph-persisted baselines (survives SDK restarts)
        self._hydrate_evidence()
        # Apply source-based credibility inheritance (with recency modulation #122)
        self._apply_source_inheritance(recency_decay=recency_decay)
        # #395: evidence is CALL-SCOPED — merge into a local copy so the SDK's
        # persistent _evidence is never mutated by a run (same pattern as
        # TortoiseEP.run's run_evidence).
        run_evidence = dict(self._evidence)
        if evidence:
            run_evidence.update(evidence)
        # Calibration gate (#1157: shared check — dream / get_confidence use
        # the same _ensure_calibrated helper; fail-closed by default (#344),
        # one knob TORTOISE_EP_REQUIRE_CALIBRATION).
        if require_calibration is None:
            require_calibration = _ep_require_calibration_default()
        if require_calibration:
            self._ensure_calibrated("compute_confidence")
        if factors is not None:
            operator_ids = [f if isinstance(f, str) else f[0] for f in factors]
            if not operator_ids:
                return {"iterations": 0, "converged": True, "confidences": {}, "diagnostic": "no_factors"}
            iterations, converged = ep.run(
                operator_ids, max_hops=max_hops, evidence=run_evidence)
        elif anchors is not None:
            # BFS subgraph selection from anchor points (A9, epic #902): the
            # selection carries the direct-edge factor anchors too — a
            # direct-edge-only subgraph yields ZERO operators but a non-empty
            # direct-factor selection; ep.run accepts plain-point seeds, so
            # the run seeds = operators + the anchor endpoints (§5.6).
            # #395 (AC8): selection depth == run depth — max_hops threads
            # through BOTH the selector and ep.run.
            operator_ids, _factor_anchors = self._select_subgraph(
                anchors, max_hops=max_hops, rel_filter=rel_filter,
                direction=direction)
            for (src, tgt, _t) in _factor_anchors:
                operator_ids.append(src)
                operator_ids.append(tgt)
            operator_ids = list(dict.fromkeys(operator_ids))
            if not operator_ids:
                return {"iterations": 0, "converged": True, "confidences": {},
                        "diagnostic": "no_factors"}
            iterations, converged = ep.run(
                operator_ids, max_hops=max_hops, evidence=run_evidence)
        else:
            # ── No-arg (#395 AC8 + delta C): LOCAL EP over the affected
            # subgraph — dirty roots seed a max_hops=None run over the exact
            # affected closure; NO global extract_svbp_factors() scan (AC2 —
            # the epic-903 refactor regressed this no-arg path to a
            # whole-graph extract+run; restored to the #395 local contract,
            # #1162). Clean graph → 'no_dirty_roots' (AC8).
            # #1163 (multi-process EP): hydrate graph-persisted dirty roots
            # first — a fresh request-scoped SDK must run the same local EP
            # the writing process would (the HTTP no-arg acceptance).
            self._hydrate_dirty_roots()
            roots = list(self._dirty_roots)
            if not roots:
                return {"iterations": 0, "converged": True, "confidences": {},
                        "diagnostic": "no_dirty_roots"}
            # Bounded fail-safe pass (dream, max_hops=2) FIRST — preserves the
            # dirty-clear/retry lifecycle (#85); then the exact local pass
            # (max_hops=None). Roots are captured BEFORE the dream so the
            # exact pass seeds the ORIGINAL dirty set (the dream clears
            # converged roots). Double-pass rationale (scoping P2): bounded-
            # then-exact is a fail-safe against dream-alone silently dropping
            # dirty roots reachable only through legacy op_type-only operators
            # (the dream selector is {is_operator:true}-only; _affected_claims
            # is op_type-aware).
            # Epic 903-C4 (#1242): the read-path fail-safe pass is
            # FROM-SCRATCH (warm_start=False) — reads never censor.
            # Epic 903-C2 (#1240): a READ never moves the freshness signal
            # that the 903-C4 stale-first scheduler ranks on.
            # #1315/#1314: propagate the caller's calibration posture — the
            # epic903 refactor re-added an unpropagated dream here (same
            # class as the #1315 fix; line 7050/7202 already propagate).
            self.dream(dirty_only=True, stamp_dreamed_at=False,
                       warm_start=False,
                       require_calibration=require_calibration)
            iterations, converged = ep.run(roots, max_hops=None,
                                           evidence=run_evidence)
            if not ep._last_affected and not ep._last_truncated:
                return {"iterations": iterations, "converged": converged,
                        "confidences": {}, "diagnostic": "no_factors"}
        confidences = {}
        proj = self._get_proj()
        # #395 (delta C): the write-back set == the run set — consume
        # ep._last_affected (stashed by run, assigned before its early
        # returns) instead of re-running the BFS with a default depth.
        for claim_id in (ep._last_affected or set()):
            conf = ep.compute_confidence(claim_id)
            confidences[claim_id] = conf
        # Batch write-back via UNWIND (drops the per-claim SET loop; the
        # full-precision mean is the loop's only unique write — n.confidence
        # itself is also batch-written by _flush_cache).
        # #1915: a READ never moves the freshness signal — no updatedAt stamp
        # here (get_confidence passes stamp_dreamed_at=False for the same
        # reason); the dream write-back owns the write-path stamp.
        if confidences:
            proj.g.query(
                "UNWIND $params AS p "
                "MATCH (n:Point {id: p.id}) SET n.confidence = p.c",
                params={"params": [{"id": cid, "c": conf["mean"]}
                                    for cid, conf in confidences.items()]},
            )
        result = {"iterations": iterations, "converged": converged,
                  "confidences": confidences}
        # #395: the degeneration guard never aborts the interactive path — it
        # proceeds with the capped set and reports the diagnostic.
        if ep._last_truncated:
            result["diagnostic"] = "truncated"
        return result

    def _hydrate_evidence(self) -> None:
        """Load graph-persisted baselines (baseline_set=true) into _evidence.

        Idempotent — only adds claim ids not already present. Shared by
        compute_confidence and the Dreamer (#330) so dream runs honour the
        same persistent evidence contract as explicit confidence reads.
        """
        proj = self._get_proj()
        # #689: retracted points must not feed Beta priors into EP.
        rows = proj.g.query(
            "MATCH (n:Point) WHERE n.baseline_set = true AND n.ep_alpha IS NOT NULL "
            "AND (n.status IS NULL OR n.status <> 'retracted') "
            "RETURN n.id, n.ep_alpha, n.ep_beta"
        ).result_set
        for pid, alpha, beta in rows:
            if pid not in self._evidence:
                self._evidence[pid] = (alpha, beta)

    def set_point_baseline(self, claim_id: str, alpha: float, beta: float, *,
                           source: str = "explicit") -> dict:
        """Set Beta prior evidence for a claim. Persists to graph immediately.

        ``source`` records the baseline's provenance (``baseline_source`` graph
        property): "explicit" (default) — manual/hosted baseline, NEVER
        recomputed by ``_apply_source_inheritance``; "inherited" — derived from
        Source evidence, recomputed per EP run subject to the per-point time
        gate (``n.inherited_at``). Explicit baselines are always distinguishable
        from legacy ``baseline_set=true`` rows (issue #398 2x2 mapping).
        """
        self._evidence[claim_id] = (alpha, beta)
        # Persist to graph so baselines survive SDK restarts
        proj = self._get_proj()
        proj.g.query(
            "MATCH (n:Point {id: $id}) "
            "SET n.ep_alpha = $a, n.ep_beta = $b, n.baseline_set = true, "
            "    n.baseline_source = $src, "
            "    n.posterior_alpha = null, n.posterior_beta = null",
            params={"id": claim_id, "a": alpha, "b": beta, "src": source},
        )
        # Dreaming (#85, P1): a baseline change alters the prior — neighbors
        # whose confidence derived from this claim are now stale.
        self._mark_dirty([claim_id])
        # Epic 903-C4 (#1242): a prior change alters factor behavior WITHOUT
        # a topology change — warm-start seeds (edge messages) computed under
        # the OLD prior are stale. Full drop (cheap) per the plan's W3 rule.
        self._get_ep().invalidate_messages()
        return {"claim_id": claim_id, "alpha": alpha, "beta": beta, "source": source}

    def _invalidate_inheritance_gate(self, point_ids: list[str]) -> None:
        """Dirty-mark the per-point recompute gate for inherited baselines.

        Called by write events that change the inputs of source inheritance
        (point created from a tiered source, extractedFrom edge deleted, source
        tier/assessment changed). Clears the point's ``inherited_at`` stamp so
        the next ``_apply_source_inheritance`` recomputes immediately regardless
        of the time-gate interval.
        """
        if not point_ids:
            return
        proj = self._get_proj()
        proj.g.query(
            "MATCH (n:Point) WHERE n.id IN $ids REMOVE n.inherited_at",
            params={"ids": point_ids},
        )
        self._mark_dirty(point_ids)

    def get_confidence(self, claim_id: str,
                       require_calibration: bool | None = None) -> dict:
        """Get EP confidence for a claim: {mean, variance, alpha, beta}.

        Lazy consistency (#85): if the claim is a dirty root (confidence
        diverged after writes), dream it first so reads return fresh values.

        Calibration posture (#1157): the lazy dream path WRITES n.confidence
        and the read returns belief numbers — both must be gated on
        calibration like compute_confidence. require_calibration=True raises
        CalibrationError when evidence-kind points are uncalibrated; None
        (default) resolves to the shared TORTOISE_EP_REQUIRE_CALIBRATION
        posture (default True — fail-closed, post-#344; set
        TORTOISE_EP_REQUIRE_CALIBRATION to "0" to opt out).
        """
        if require_calibration is None:
            require_calibration = _ep_require_calibration_default()
        if require_calibration:
            self._ensure_calibrated("get_confidence")
        if claim_id in self._dirty_roots:
            self.dream(dirty_only=True,
                       require_calibration=require_calibration,
                       stamp_dreamed_at=False,  # read path — never stamps (epic 903-C2)
                       warm_start=False)  # from-scratch reads (epic 903-C4)
        return self._get_ep().compute_confidence(claim_id)

    def _apply_source_inheritance(self, recency_decay: float | None = None,
                                   recompute_interval: float | None = None):
        """Apply source credibility (tier → Beta prior) to Points via extractedFrom.

        Issue #398 — log-scale multi-source aggregation (replaces
        highest-tier-wins) through the real graph path:

          - Tier resolution per source: explicit ``credibilityTier`` >
            ``sourceKind`` tier-form > registry default (``SOURCE_KIND_DEFAULTS``)
            > None (neutral — no inheritance, preserving the opt-in guard).
          - Aggregation: pinned formula
            ``pc_t = log2(N_t+1) * decay_t * mean_i(base_pc(tier_i) * factor_i)``
            with ``decay_t`` keyed on the tier's MOST-RECENT source (T0 exempt);
            per-source ``factor_i`` = assessment factor (1.0 until assess_source
            lands — Task 5).
          - Positive-only: NAND contradiction is EP's factor domain — inheritance
            never folds negative pseudo-counts (double-count guard).
          - Baseline provenance (2x2 mapping): explicit baselines (baseline_source
            = 'explicit' or legacy baseline_set=true) are NEVER recomputed;
            inherited baselines (baseline_source='inherited') recompute per run
            subject to the per-point time gate (``n.inherited_at``); points with
            no baseline (baseline_source IS NULL AND baseline_set IS NOT true)
            are ALWAYS eligible.
          - Gate: recompute at most once per ``recompute_interval`` (default 3600s,
            env TORTOISE_EP_REINHERIT_INTERVAL; 0 = always), unless the gate was
            dirty-marked by a write event. Epsilon guard (rel 1e-9) suppresses
            identical rewrites; ``inherited_at`` is always refreshed on a
            dirty-marked recompute so dirty points settle after one pass.
          - Assessment points (pointKind='assessment') are excluded — they are
            evidence ABOUT sources, not extracted FROM them.

        ep.py is untouched (additive-only — another issue owns EP propagation).
        """
        import os  # noqa: I001
        from datetime import datetime, timezone
        from tortoise.source_credibility import (
            aggregate_prior,
            assessment_factor,
            resolve_tier,
        )

        if recency_decay is None:
            recency_decay = float(os.environ.get("TORTOISE_EP_RECENCY_DECAY", "0.95"))
        if recompute_interval is None:
            recompute_interval = float(
                os.environ.get("TORTOISE_EP_REINHERIT_INTERVAL", "3600")
            )
        proj = self._get_proj()
        now = datetime.now(timezone.utc)  # noqa: UP017
        from collections import defaultdict
        # Per-source assessment factors (latest per (url, assessor), outdated
        # filtered, reputation snapshotted at write). Batched — one query.
        factor_by_source: dict[str, float] = {}
        arows = proj.g.query(
            "MATCH (p:Point {pointKind:'assessment'}) "
            "WHERE (p.outdated IS NULL OR p.outdated = false) "
            "RETURN p.targetSource, p.assessor, p.score, "
            "coalesce(p.assessorReputation, 0.5), p.createdAt "
            "ORDER BY p.createdAt",
            params={},
        ).result_set
        latest_by_source: dict[str, dict[str, tuple[float, float]]] = defaultdict(dict)
        for tsrc, assessor, score, rep, _created in arows:
            if not tsrc:
                continue
            try:
                latest_by_source[tsrc][assessor] = (float(rep), float(score))
            except (TypeError, ValueError):
                continue
        for tsrc, by_assessor in latest_by_source.items():
            factor_by_source[tsrc] = assessment_factor(by_assessor.values())

        # Inherit-eligible points:
        #   (baseline_source IS NULL AND baseline_set IS NOT true)  → always eligible
        #   (baseline_source = 'inherited')                          → gated by inherited_at
        where = (
            "WHERE n.is_operator = false "
            "AND (n.pointKind IS NULL OR n.pointKind <> 'assessment') "
            "AND ("
            "  (n.baseline_source IS NULL AND (n.baseline_set IS NULL OR n.baseline_set = false)) "
            "  OR n.baseline_source = 'inherited'"
            ") "
            "AND (s.credibilityTier IS NOT NULL OR s.sourceKind IS NOT NULL) "
        )
        rows = proj.g.query(
            f"MATCH (n:Point)-[:extractedFrom]->(s:Source) {where} "
            "RETURN n.id, s.url, s.credibilityTier, s.sourceKind, "
            "s.sourceDate, s.ingestedAt, n.baseline_source, n.inherited_at",
            params={},
        ).result_set

        # Collect per-point source evidence
        from collections import defaultdict
        point_sources: dict[str, list[dict]] = defaultdict(list)
        for pid, url, ctier, skind, sdate, ingested, bl_src, inherited_at in rows:  # noqa: B007
            tier = resolve_tier(ctier, skind)
            if tier is None:
                continue  # neutral source — no inheritance contribution
            point_sources[pid].append({
                "url": url, "tier": tier, "sourceDate": sdate,
                "ingestedAt": ingested,
            })

        # Revert: points with an inherited baseline but NO eligible sources
        # (all edges deleted or all sources neutral) return to neutral — subject
        # to the same per-point gate (dirty-marked or interval elapsed).
        if point_sources:  # noqa: SIM108
            sourced_ids = set(point_sources)
        else:
            sourced_ids = set()
        revert_rows = proj.g.query(
            "MATCH (n:Point) WHERE n.baseline_source = 'inherited' "
            "AND (n.pointKind IS NULL OR n.pointKind <> 'assessment') "
            "RETURN n.id, n.inherited_at",
            params={},
        ).result_set
        for pid, inherited_at in revert_rows:
            if pid in sourced_ids:
                continue
            # Gate check (same as write path)
            if inherited_at is not None and recompute_interval > 0:
                try:
                    last = datetime.fromisoformat(str(inherited_at).replace("Z", "+00:00"))
                    if last.tzinfo is None:
                        last = last.replace(tzinfo=timezone.utc)  # noqa: UP017
                    age = (now - last).total_seconds()
                except (ValueError, TypeError):
                    age = recompute_interval + 1
                if age < recompute_interval:
                    continue  # within interval and not dirty-marked → keep
            proj.g.query(
                "MATCH (n:Point {id:$id}) REMOVE n.ep_alpha, n.ep_beta, "
                "n.baseline_set, n.baseline_source, n.inherited_at, "
                "n.posterior_alpha, n.posterior_beta",
                params={"id": pid},
            )
            # Clear the stale prior from in-memory evidence cache (#652).
            # set_point_baseline writes (alpha, beta) into self._evidence
            # unconditionally, and _hydrate_evidence is additive-only — so
            # the stale entry survives the graph-level remove and gets
            # re-applied by ep.run(evidence=self._evidence).
            self._evidence.pop(pid, None)
            self._mark_dirty([pid])

        for pid, sources in point_sources.items():
            # Fetch the point's current baseline marker state
            row = proj.g.query(
                "MATCH (n:Point {id:$id}) RETURN n.baseline_source, n.inherited_at, "
                "coalesce(n.ep_alpha, 1.0), coalesce(n.ep_beta, 1.0)",
                params={"id": pid},
            ).result_set
            bl_src, inherited_at, cur_a, cur_b = row[0] if row else (None, None, 1.0, 1.0)

            is_inherited = bl_src == "inherited"
            if is_inherited and inherited_at is not None and recompute_interval > 0:
                try:
                    last = datetime.fromisoformat(str(inherited_at).replace("Z", "+00:00"))
                    if last.tzinfo is None:
                        last = last.replace(tzinfo=timezone.utc)  # noqa: UP017
                    age = (now - last).total_seconds()
                except (ValueError, TypeError):
                    age = recompute_interval + 1  # stale stamp → recompute
                if age < recompute_interval:
                    continue  # within interval and not dirty-marked → skip

            # Per-source assessment factor (clamped [0.1, 2.0]); factor = 1.0
            # when no assessments — exact tier priors preserved.
            groups = [
                (src["tier"], src["sourceDate"], src["ingestedAt"],
                 factor_by_source.get(src["url"], 1.0))
                for src in sources
            ]
            alpha, beta = aggregate_prior(
                groups, recency_decay=recency_decay, now=now,
            )

            # Epsilon guard: skip identical rewrites (no dirty churn).
            if is_inherited and abs(alpha - cur_a) < 1e-9 * max(1.0, abs(alpha)) \
                    and abs(beta - cur_b) < 1e-9 * max(1.0, abs(beta)):
                # Refresh the gate stamp so dirty points settle after one pass.
                self._touch_inherited_at(pid, now)
                continue

            self.set_point_baseline(pid, alpha, beta, source="inherited")
            self._touch_inherited_at(pid, now)

    def _touch_inherited_at(self, point_id: str, now) -> None:
        """Stamp the per-point inheritance gate timestamp (graph-persisted)."""
        proj = self._get_proj()
        proj.g.query(
            "MATCH (n:Point {id:$id}) SET n.inherited_at = $ts",
            params={"id": point_id, "ts": now.isoformat()},
        )

    def calibrate_summary(self) -> list[dict]:
        """Audit graph calibration state. Returns per-point guidance.
        
        Checks baseline_set flag on non-operator Points. For uncalibrated
        points, traverses extractedFrom→Source to check for inherited credibilityTier.
        """
        proj = self._get_proj()
        where = "WHERE n.is_operator = false"
        params = {}
        
        from tortoise.source_credibility import resolve_tier
        rows = proj.g.query(
            f"MATCH (n:Point) {where} "
            "AND (n.pointKind IS NULL OR n.pointKind <> 'assessment') "
            "OPTIONAL MATCH (n)-[:extractedFrom]->(s:Source) "
            "RETURN n.id, n.content, n.pointKind, "
            "coalesce(n.baseline_set, false) AS calibrated, "
            "n.status, "
            "s.credibilityTier, s.sourceKind, s.url AS src_url",
            params=params,
        ).result_set
        
        results = []
        for row in rows:
            pid, content, pk, calibrated, status, ctier, skind, src_url = row
            item = {"id": pid, "content": content, "pointKind": pk,
                    "calibrated": calibrated, "status": status}
            # Effective tier: explicit credibilityTier > sourceKind tier-form >
            # registry default (issue #398 Task 6 — legacy-inherited advisory).
            eff_tier = resolve_tier(ctier, skind)
            
            if not calibrated:
                if src_url and eff_tier:
                    item["suggestion"] = (
                        f"Inherited {eff_tier} from Source {src_url} — run "
                        f"compute_confidence() to apply"
                    )
                elif src_url and not eff_tier:
                    item["suggestion"] = (
                        f"Source {src_url} is untiered — call "
                        f"set_source_tier('{src_url}', 'T0'..'T4') or "
                        f"create_source(url, kind, tier=...) "
                        f"(covers all points from this source)"
                    )
                else:
                    item["suggestion"] = (
                        f"Call set_point_baseline('{pid}', alpha, beta) "
                        f"or recreate with credibility kwarg"
                    )
            else:
                # Legacy-inherited advisory: explicit/inherited baseline whose
                # source was re-tiered — suggest re-derivation via the writer.
                if src_url and ctier and item.get("calibrated"):
                    item["note"] = (
                        f"Source {src_url} tier {ctier} — baseline may predate "
                        f"issue #398; re-derive via set_source_tier"
                    )
            results.append(item)

        # Deduplicate: keep one entry per Point ID, prefer Source-based suggestions
        seen = {}
        deduped = []
        for item in results:
            pid = item["id"]
            if pid not in seen:
                seen[pid] = item
                deduped.append(item)
            elif "Source" in str(item.get("suggestion", "")) and "Source" not in str(seen[pid].get("suggestion", "")):
                for i, d in enumerate(deduped):
                    if d["id"] == pid:
                        deduped[i] = item
                        break
                seen[pid] = item
        return deduped

    def _ensure_calibrated(self, surface: str) -> None:
        """#1157: shared calibration gate for EP surfaces.

        Raises CalibrationError when evidence-kind Points (statement /
        observation / hypothesis) are uncalibrated (no baseline). Extracted
        from compute_confidence's require_calibration gate so dream and
        get_confidence share the SAME check — the #7478 target is zero EP
        surfaces running uncalibrated EP silently, not just the explicit
        compute_confidence surface (#344 only covers the explicit surface).

        Args:
            surface: human-readable caller name for the error message
                (e.g. "dream", "get_confidence").
        """
        from .exceptions import CalibrationError
        summary = self.calibrate_summary()
        evidence_kinds = {"statement", "observation", "hypothesis"}
        uncalibrated = [
            s for s in summary
            if not s["calibrated"] and s.get("pointKind") in evidence_kinds
            # #780/PR #1212: draft points never feed EP (factor extraction
            # and propagation exclude them by default) — demanding
            # calibration of a draft is noise; the gate only guards live
            # evidence. Status NULL = live, mirroring _live_only.
            and s.get("status") != "draft"
        ]
        if uncalibrated:
            ids = [s["id"] for s in uncalibrated[:10]]
            msg = (
                f"{surface}: {len(uncalibrated)} uncalibrated evidence points. "
                f"First 10: {ids}. Run calibrate_summary() for full guidance."
            )
            raise CalibrationError(msg)


    # ── P0 Group 3: Checkpoint, Diary, Status, Analyze, Ingest ────

    def _content_exists(self, content: str,
                        pointKind: str | None = None,
                        exclude_id: str | None = None) -> str | None:
        """Return point ID if a point with this content hash exists, else None.

        #784: optional pointKind scoping — a duplicate observation must never
        suppress a decision (DE2E-N11); ``exclude_id`` excludes a specific
        point (the dedup candidate itself — self-match guard, #784 review).
        Default None preserves the legacy any-kind behavior.
        """
        ch = _content_hash(content)
        proj = self._get_proj()
        kind_clause = " AND n.pointKind = $kind" if pointKind else ""
        exclude_clause = " AND n.id <> $exclude" if exclude_id else ""
        params: dict = {"ch": ch}
        if pointKind:
            params["kind"] = pointKind
        if exclude_id:
            params["exclude"] = exclude_id
        rows = proj.g.query(
            f"MATCH (n:Point {{content_hash:$ch}}) "
            "WHERE (n.is_operator IS NULL OR n.is_operator = false) "
            f"{kind_clause} {exclude_clause} "
            "RETURN n.id",
            params=params,
        ).result_set
        return rows[0][0] if rows else None

    def checkpoint(self, items: list[dict], agent_name: str = "checkpoint",
                   threshold: float = 0.95) -> dict:
        """Session batch save — two-tier dedup (content hash + embedding similarity).

        Each item: {wing, room, content}. Returns {filed: N, duplicates: M}.
        threshold: cosine similarity for semantic dedup (0.0-1.0).
                   Set to 1.0 to disable semantic dedup (hash-only).

        The 0.95 default is the near-IDENTICAL-text bar (re-filed session
        notes), deliberately stricter than the paraphrase band: re-validated
        under bge-small 2026-08-21 (#1349 T14) — the strictest fixture
        paraphrase ("Deployments must be automated for reliability" ↔
        "Automating deployments is required for reliability") scores 0.955
        ≥ 0.95, while looser paraphrases (0.77-0.90) stay below and file
        as distinct points. Keep in sync with mcp_server.tortoise_checkpoint.
        """
        from datetime import datetime, timezone
        filed, duplicates = 0, 0
        proj = self._get_proj()
        now = datetime.now(timezone.utc).isoformat()  # noqa: UP017

        # Tier 1: content hash dedup
        to_check: list[tuple[dict, str]] = []
        seen: set[str] = set()
        for item in items:
            content = item["content"]
            ch = _content_hash(content)
            if ch in seen or self._content_exists(content):
                duplicates += 1
                continue
            seen.add(ch)
            to_check.append((item, ch))

        if not to_check:
            return {"filed": 0, "duplicates": duplicates}

        # Tier 2: embedding similarity dedup (GAP-08)
        to_file = to_check
        if threshold < 1.0:
            try:
                to_file = self._semantic_dedup(
                    to_check, threshold, pointKind="checkpoint-item")
            except ImportError:
                # Expected in zero-dependency environments — hash-only fallback.
                # #330: previously a bare `except Exception: pass` swallowed
                # real failures silently; log the designed fallback at INFO.
                _logger.info(
                    "Semantic dedup unavailable (embeddings deps missing) — "
                    "hash-only fallback"
                )
                to_file = to_check
            except Exception as e:
                # #330: real dedup backend failures must be observable — a
                # silent degrade to hash-only dedup would file duplicates.
                _logger.warning(
                    "Semantic dedup failed — falling back to hash-only dedup: %s", e
                )
                to_file = to_check

        duplicates += len(to_check) - len(to_file)

        for item, ch in to_file:
            p = self.create_point(
                "checkpoint-item", item["content"],
                wing=item.get("wing", ""),
                room=item.get("room", ""),
                content_hash=ch,
            )
            # GAP-07 (partially closed by #432 _emit_event — graph mutations
            # now emit :GraphEvent; this EventRecorded path is session-capture
            # provenance and stays separate)
            try:
                proj.apply({
                    "type": "EventRecorded",
                    "id": ulid(),
                    "eventKind": "pointAdded",
                    "subject": agent_name,
                    "object": p["id"],
                    "startedAt": now,
                })
            except Exception:
                _logger.warning("Failed to emit provenance event for point %s", p["id"])
            filed += 1

        monitoring.record_ingest()
        return {"filed": filed, "duplicates": duplicates}

    def _semantic_dedup(self, candidates: list[tuple[dict, str]],
                        threshold: float,
                        pointKind: str = "checkpoint-item",
                        return_pairs: bool = False,
                        similarity_out: bool = False,
                        exclude_ids: set[str] | None = None
                        ) -> list[tuple[dict, str]] | list[dict]:
        """Filter candidates by embedding similarity against existing points.

        #784 generalization: ``pointKind`` scopes the existing-point universe
        (default 'checkpoint-item' preserves the legacy checkpoint() behavior
        — R14); ``return_pairs=True`` returns above-threshold HITS as
        {candidate, existing, similarity} dicts (the review-queue mode);
        ``similarity_out`` appends the max similarity to each surviving
        (item, ch) tuple when filtering.
        """
        import numpy as np
        proj = self._get_proj()
        exclude = exclude_ids or set()
        excl_clause = " AND NOT n.id IN $exclude" if exclude else ""
        params: dict = {"kind": pointKind}
        if exclude:
            params["exclude"] = list(exclude)
        rows = proj.g.query(
            "MATCH (n:Point {pointKind:$kind}) "
            "WHERE (n.is_operator IS NULL OR n.is_operator = false) "
            f"{excl_clause} "
            "RETURN n.id, n.content",
            params=params,
        ).result_set
        existing = [(r[0], r[1]) for r in rows if r[1]]
        if not existing:
            return [] if return_pairs else candidates

        new_texts = [item["content"] for item, _ch in candidates]
        # Single degrade chain (embeddings._encode): real model → TF-IDF → zeros.
        # #880: this used to instantiate SentenceTransformer directly. A missing
        # model under HF_HUB_OFFLINE raises LocalEntryNotFoundError (an OSError,
        # NOT ImportError) → checkpoint()'s except Exception silently dropped
        # semantic dedup to hash-only and near-duplicates were filed. Reuse the
        # EmbeddingModel singleton + degrade chain so any load/encode failure
        # degrades to deterministic TF-IDF instead of hash-only.
        from .embeddings import _encode
        all_vecs, _degraded = _encode([c for _i, c in existing] + new_texts)

        e_vecs, n_vecs = all_vecs[:len(existing)], all_vecs[len(existing):]

        def _norm(v):
            n = np.linalg.norm(v, axis=1, keepdims=True)
            n[n == 0] = 1
            return v / n

        sims = (_norm(n_vecs) @ _norm(e_vecs).T)
        max_sims = sims.max(axis=1)
        argmax = sims.argmax(axis=1)

        if return_pairs:
            pairs = []
            for i, ((item, _ch), sim) in enumerate(zip(candidates, max_sims)):  # noqa: B905
                if sim >= threshold:
                    eid, econtent = existing[argmax[i]]
                    pairs.append({
                        "candidate": item,
                        "candidate_id": item.get("id"),
                        "existing": eid,
                        "existing_content": econtent,
                        "similarity": round(float(sim), 4),
                    })
            return pairs

        if similarity_out:
            return [(item, ch, round(float(max_sims[i]), 4))
                    for i, (item, ch) in enumerate(candidates)
                    if max_sims[i] < threshold]
        return [(item, ch) for i, (item, ch) in enumerate(candidates)
                if max_sims[i] < threshold]

    def diary_write(self, agent_name: str, entry: str,
                    topic: str | None = None, wing: str | None = None) -> dict:
        """Write an agent diary entry. Returns the created Point."""
        from datetime import datetime, timezone  # noqa: F401
        props: dict[str, Any] = {"authoredBy": agent_name}
        if topic:
            props["topic"] = topic
        # P1 #49: use wing property only — context is deprecated
        if wing:
            props["wing"] = wing
        return self.create_point("diary", entry, **props)

    def diary_read(self, agent_name: str, last_n: int = 10,
                   wing: str | None = None) -> list[dict]:
        """Read recent diary entries for an agent, newest first."""
        proj = self._get_proj()
        if wing:
            rows = proj.g.query(
                "MATCH (n:Point {pointKind:'diary', authoredBy:$agent, wing:$wing}) "
                "WHERE n.is_operator = false "
                "RETURN properties(n) ORDER BY n.createdAt DESC LIMIT $lim",
                params={"agent": agent_name, "wing": wing, "lim": last_n},
            ).result_set
        else:
            rows = proj.g.query(
                "MATCH (n:Point {pointKind:'diary', authoredBy:$agent}) "
                "WHERE n.is_operator = false "
                "RETURN properties(n) ORDER BY n.createdAt DESC LIMIT $lim",
                params={"agent": agent_name, "lim": last_n},
            ).result_set
        return [r[0] for r in rows]

    def status(self) -> dict:
        """Graph health + entity counts + FalkorDB connectivity.

        Returns {connected, counts: {Point, Event, ...}, total_entities}.
        """
        proj = self._get_proj()
        connected = False
        try:
            proj.g.query("MATCH (n) RETURN count(n) LIMIT 1")
            connected = True
        except Exception:
            pass
        counts = self.taxonomy()
        total = sum(counts.values())
        result = {"connected": connected, "counts": counts, "total_entities": total}
        if self._namespace:
            result["namespace"] = self._namespace
        return result

    def ingest_corpus(self, directory: str, eventKind: str = "DocumentCreated",
                      extract_metadata: bool = False, llm_model: str | None = "gpt-5-mini",
                      progress_file: str | None = None) -> dict:
        """Batch ingestion — walk directory, parse YAML frontmatter,
        create/update Event nodes. Returns {ingested, updated, skipped, failed, errors}.

        DEPRECATED — use ``index_directory`` (epic #900). During the W3
        deprecation window this FROZEN legacy branch keeps its behavior
        byte-identical (SC4). FLAG-SEMANTICS DIVERGENCE (W3, §6.1):
        ``extract_metadata=False`` STILL computes session embeddings on the
        legacy path (``_session_embedding`` unconditional in both AgentSession
        branches) while the new path short-circuits the embedding to None —
        the SAME flag, different Event semantics on two live paths.
        """
        import os as _os  # noqa: I001
        import json as _json
        from pathlib import Path
        from datetime import datetime, timezone

        # #329: ingest path validation — absolute, no `..`, and under the
        # optional TORTOISE_INGEST_BASE_DIR base. The stdio/CLI surface is
        # operator-trusted, but the directory walk reads host files: bound it.
        from .security import ingest_dir_is_safe, resolve_under_base
        ingest_base = None
        raw_base = _os.environ.get("TORTOISE_INGEST_BASE_DIR")
        if raw_base:
            ingest_base = _os.path.realpath(_os.path.expanduser(raw_base))
        if not ingest_dir_is_safe(directory, ingest_base):
            raise ValueError(
                f"Unsafe ingest directory: {directory!r}. Directory must be "
                f"absolute, contain no '..' components, and resolve under "
                f"TORTOISE_INGEST_BASE_DIR when set ({ingest_base or '<unset>'})."
            )
        if progress_file is not None:
            if not isinstance(progress_file, str) or not progress_file:
                raise ValueError("progress_file must be a non-empty string.")
            if not _os.path.isabs(progress_file):
                raise ValueError(f"progress_file must be absolute: {progress_file!r}")
            if ".." in Path(progress_file).parts:
                raise ValueError(f"progress_file contains '..': {progress_file!r}")
            if ingest_base is not None and resolve_under_base(progress_file, ingest_base) is None:
                raise ValueError(
                    f"progress_file {progress_file!r} not under TORTOISE_INGEST_BASE_DIR."
                )

        # Canonical boundary regex lives in file_indexer (#280 review round 6):
        # hoisted so extract/health/ingest can never drift apart again (the round-5
        # bug was exactly two copies diverging → permanent sweep non-convergence).
        from .file_indexer import _FM_RE
        ingested, updated, skipped, failed = 0, 0, 0, 0
        errors: list[dict] = []
        now = datetime.now(timezone.utc).isoformat()  # noqa: UP017
        proj = self._get_proj()

        files = sorted(Path(directory).rglob("*.md"))

        # Resume from progress file
        completed_files: list[str] = []
        if progress_file:
            try:
                with open(progress_file) as pf:
                    progress = _json.load(pf)
                    completed_files = progress.get("completed_files", [])
            except Exception:
                pass
        processed_set = set(completed_files)

        # #280 review P2: two corpus files sharing a sessionId (duplicated
        # frontmatter, or rglob picking up copies) used to make the sweep
        # permanently non-convergent — MERGE is last-writer-wins, so the
        # losing copy stays hash-stale and every run re-merges both. Dedupe
        # the scan to ONE primary file per sessionId (first in sorted order);
        # non-primary copies are surfaced by session_index_health()'s
        # `duplicates` bucket instead of being re-indexed every run.
        _primary_sessions: dict[str, str] = {}

        for i, filepath in enumerate(files):
            rel_path = str(filepath)
            if rel_path in processed_set:
                skipped += 1
                continue

            if eventKind == "AgentSession" and filepath.is_symlink():
                # R17 parity with mining.py's pre-scan: a symlinked *.md is
                # never read (host-file read + LLM exfiltration when
                # extract_metadata/llm_model is set) and never participates in
                # primary-session selection — otherwise a symlink sorting
                # before a real file sharing its sessionId becomes ingest's
                # primary (target content read+indexed) while mining picks the
                # real file → hash never matches → re-mined every run (point
                # stacking; round-2 review).
                skipped += 1
                errors.append({"file": rel_path,
                               "error": "symlinked file skipped (R17: the corpus "
                                         "walk must not follow symlinks)",
                               "retryable": False})
                continue

            try:
                text = filepath.read_text(encoding="utf-8")
            except Exception as e:
                failed += 1
                errors.append({"file": rel_path, "error": str(e), "retryable": False})
                continue

            m = _FM_RE.match(text)
            frontmatter: dict = {}
            if m:
                try:
                    import yaml as _yaml
                    parsed = _yaml.safe_load(m.group(1))
                    if isinstance(parsed, dict):
                        frontmatter = parsed
                        # YAML types (bool/int) must not leak into string fields
                        # (regression vs old line-by-line parser). Coerce known
                        # string fields to str.
                        for _k in ("doc_status", "format", "version", "title",
                                   "sessionId", "session_id", "agent"):
                            if _k in frontmatter and frontmatter[_k] is not None:
                                frontmatter[_k] = str(frontmatter[_k])
                except Exception:
                    pass  # fallback to empty dict

            # Optional frontmatter-metadata validation (#1362) — warn-only,
            # gated by TORTOISE_VALIDATE_FRONTMATTER=1 (default OFF). The
            # tolerant parse above is UNCHANGED ({} on malformed still
            # degrades); this only reports missing/malformed required fields.
            from .frontmatter_validator import validate_and_warn
            validate_and_warn(
                frontmatter,
                kind="session" if eventKind == "AgentSession" else "document",
                context=f"ingest_corpus:{rel_path}",
            )

            # #330: content identity hash shared by both modes (hashlib is
            # module-imported). byte-identical re-ingest -> skipped.
            file_hash = hashlib.sha256(text.encode()).hexdigest()

            if eventKind == "AgentSession":
                # AgentSession branch — session indexing with metadata extraction
                session_id = frontmatter.get("sessionId") or frontmatter.get("session_id") or f"file_{filepath.stem}"
                # #280 review P2: non-primary copy of a duplicated sessionId —
                # skip deterministically (retryable:False — re-running changes
                # nothing); the duplicate is surfaced, not silently re-indexed.
                _primary = _primary_sessions.get(session_id)
                if _primary is not None and _primary != rel_path:
                    skipped += 1
                    errors.append({"file": rel_path,
                                   "error": f"duplicate sessionId '{session_id}' "
                                            f"(primary file: {_primary}) — non-primary "
                                            f"copy skipped (see session_index_health "
                                            f"'duplicates')",
                                   "retryable": False})
                    continue
                _primary_sessions.setdefault(session_id, rel_path)
                event_id = f"session_{session_id}"
                name = frontmatter.get("title", filepath.stem)
                # #280: per-session flock — serialize against the session-end hook's
                # single-file writer and concurrent sweeps (MATCH->SET is not MERGE-atomic).
                # A live holder -> skip WITHOUT marking the file complete (retried later).
                from .index_lock import SessionIndexLock
                _lock = SessionIndexLock(session_id)
                try:
                    _lock_status = _lock.acquire()
                except (OSError, AttributeError, ImportError) as _lock_err:
                    # #280 review P2 (robustness): an unusable lock path must
                    # never abort the batch sweep — unwritable/blocked lock dir
                    # (EACCES/EROFS/ENOSPC/EMFILE) or a planted symlink (ELOOP
                    # from O_NOFOLLOW) is recorded as a retryable error and the
                    # sweep continues (same as the held path).
                    skipped += 1
                    errors.append({"file": rel_path,
                                   "error": f"session lock unavailable: {_lock_err}",
                                   "retryable": True})
                    continue
                if _lock_status == "held":
                    skipped += 1
                    errors.append({"file": rel_path,
                                   "error": f"session lock held: {_lock.detail}",
                                   "retryable": True})
                    continue
                try:

                    # Check dedup
                    exists_rows = proj.g.query(
                        "MATCH (e:Event {eventId:$eid}) RETURN properties(e)",
                        params={"eid": event_id},
                    ).result_set
    
                    if exists_rows:
                        existing_props = exists_rows[0][0]
                        # #330: skip unchanged + complete events. has_keywords is the
                        # sole completeness signal (an eventStatus disjunct would skip
                        # incomplete sessions that still need enrichment).
                        has_keywords = bool(existing_props.get("keywords"))
                        if existing_props.get("file_hash") == file_hash and has_keywords:
                            skipped += 1
                            completed_files.append(rel_path)
                            continue
                        # Always extract keywords (even without LLM)
                        from .session_indexer import extract_keywords_from_frontmatter as _kw_fallback  # noqa: I001
                        if extract_metadata:
                            from .session_indexer import extract_metadata as _extract
                            try:
                                metadata = _extract(text, llm_model)
                            except Exception:
                                metadata = _kw_fallback(text)
                        else:
                            metadata = _kw_fallback(text)
    
                        merged_keywords = list(dict.fromkeys(
                            existing_props.get("keywords", []) + metadata.get("keywords", [])
                        ))[:20]
                        existing_arc = existing_props.get("content_metadata", "{}")
                        try:
                            existing_arc = _json.loads(existing_arc) if isinstance(existing_arc, str) else existing_arc
                        except Exception:
                            existing_arc = {}
                        # #330: dedup + cap the narrative arc so repeated enrichment
                        # of unchanged files never grows it unboundedly (and the
                        # state-change comparison below stays deterministic). Arc
                        # entries may be dicts (phase/topic/decisions), so dedup via
                        # a canonical JSON key (str-sorted to tolerate mixed-type
                        # keys), not dict.fromkeys.
                        _arc_seen = {}
                        for _phase in (existing_arc.get("narrative_arc", [])
                                       + metadata.get("narrative_arc", [])):
                            _key = _json.dumps(_phase, sort_keys=True, default=str)
                            _arc_seen.setdefault(_key, _phase)
                        _merged_phases = list(_arc_seen.values())
                        # Cap while preferring genuinely-new phases: keep existing
                        # ones first (already-merged), then append new ones up to
                        # the cap so a full arc never starves fresh phases.
                        _existing_phases = existing_arc.get("narrative_arc", [])
                        _new_only = [p for p in _merged_phases
                                     if _json.dumps(p, sort_keys=True, default=str)
                                     not in {_json.dumps(e, sort_keys=True, default=str)
                                             for e in _existing_phases}]
                        new_phases = (_merged_phases[:len(_existing_phases)]
                                      + _new_only)[:50]
    
                        # Normalize topics to a comparable, hashable form (#330):
                        # LLM output is unvalidated — a list-of-dicts would crash
                        # set() and abort the whole run.
                        def _norm_topics(t) -> list:
                            t = t or []
                            if not isinstance(t, list):
                                t = [t]
                            return [str(x) for x in t]
                        _new_topics = _norm_topics(metadata.get("topics",
                                                                existing_props.get("topics", [])))
                        _stored_topics = _norm_topics(existing_props.get("topics"))
                        _stored_keywords = _norm_topics(existing_props.get("keywords"))
                        _new_name = metadata.get("summary", existing_props.get("name", name))
    
                        update_props = {
                            "name": _new_name,
                            "keywords": merged_keywords,
                            "topics": _new_topics,
                            "file_hash": file_hash,
                            "content_metadata": _json.dumps({
                                "schema_version": 1,
                                "summary": metadata.get("summary", ""),
                                "narrative_arc": new_phases,
                                "issues": metadata.get("issues", []),
                                "prs": metadata.get("prs", []),
                                "critical_decisions": metadata.get("critical_decisions", []),
                            }),
                            "message_count": frontmatter.get("message_count", 0),
                        }
                        # #330: unchanged content whose enrichment produced nothing
                        # new counts as skipped, not updated (counter honesty).
                        # Compare the FULL payload that would be written (keywords,
                        # normalized topics, name, narrative_arc, issues/prs/
                        # critical_decisions — the latter feed _connect_issue_objects)
                        # so a real change in any persisted field is never
                        # miscounted as a skip.
                        if existing_props.get("file_hash") == file_hash:
                            _old_meta = existing_arc
                            changed = (
                                set(_norm_topics(merged_keywords)) != set(_stored_keywords)
                                or set(_new_topics) != set(_stored_topics)
                                or _new_name != existing_props.get("name", name)
                                or new_phases != list(existing_arc.get("narrative_arc", []))
                                or _norm_topics(metadata.get("issues", [])) != _norm_topics(_old_meta.get("issues", []))
                                or _norm_topics(metadata.get("prs", [])) != _norm_topics(_old_meta.get("prs", []))
                                or _norm_topics(metadata.get("critical_decisions", [])) != _norm_topics(_old_meta.get("critical_decisions", []))
                            )
                            if not changed:
                                skipped += 1
                                completed_files.append(rel_path)
                                continue
    
                        # #244: (re)compute the session embedding from the merged
                        # surface and store as vecf32 — None when model unavailable.
                        embedding = self._session_embedding(
                            update_props["name"], metadata.get("summary", ""),
                            merged_keywords, update_props["topics"],
                        )
                        proj.g.query(
                            "MATCH (e:Event {eventId:$eid}) SET e += $props, "
                            "e.embedding = CASE WHEN $embedding IS NOT NULL THEN vecf32($embedding) ELSE e.embedding END",
                            params={"eid": event_id, "props": update_props, "embedding": embedding},
                        )
                        updated += 1
                        completed_files.append(rel_path)
                        # Connect issue/PR references to Objects
                        self._connect_issue_objects(event_id, metadata)
                    else:
                        # New session Event — always extract keywords
                        from .session_indexer import extract_keywords_from_frontmatter as _kw_fallback  # noqa: I001
                        if extract_metadata:
                            from .session_indexer import extract_metadata as _extract
                            try:
                                metadata = _extract(text, llm_model)
                            except Exception:
                                metadata = _kw_fallback(text)
                        else:
                            metadata = _kw_fallback(text)
    
                        props = {
                            "name": metadata.get("summary", name),
                            "eventKind": eventKind,
                            "session_id": session_id,
                            "agent": frontmatter.get("agent", "pi"),
                            "source_file": rel_path,
                            "file_hash": file_hash,
                            "keywords": metadata.get("keywords", []),
                            "topics": metadata.get("topics", []),
                            "message_count": frontmatter.get("message_count", 0),
                            "startedAt": now,
                            "content_metadata": _json.dumps({
                                "schema_version": 1,
                                "summary": metadata.get("summary", ""),
                                "narrative_arc": metadata.get("narrative_arc", []),
                                "issues": metadata.get("issues", []),
                                "prs": metadata.get("prs", []),
                                "critical_decisions": metadata.get("critical_decisions", []),
                            }),
                            "eventStatus": "completed",
                            "classificationLevel": "internal",
                            "format": "markdown",
                        }
                        # #244: compute the session embedding (name + summary +
                        # keywords + topics) and store as vecf32 — None when the
                        # model is unavailable (indexing never depends on it).
                        embedding = self._session_embedding(
                            props["name"], metadata.get("summary", ""),
                            props["keywords"], props["topics"],
                        )
                        proj.g.query(
                            "MERGE (e:Event {eventId:$eid}) SET e += $props, "
                            "e.embedding = CASE WHEN $embedding IS NOT NULL THEN vecf32($embedding) ELSE e.embedding END",
                            params={"eid": event_id, "props": props, "embedding": embedding},
                        )
                        ingested += 1
                        completed_files.append(rel_path)
                        # Connect issue/PR references to Objects
                        self._connect_issue_objects(event_id, metadata)
                finally:
                    _lock.release()
            else:
                # Original DocumentCreated logic
                doc_id = str(filepath.relative_to(directory))
                title = frontmatter.get("title", filepath.stem)
                doc_kind = frontmatter.get("type", frontmatter.get("document_kind", ""))
                domain = frontmatter.get("domain", frontmatter.get("documentKnowledgeDomain", ""))

                # #330: three-way on ROW PRESENCE (new doc -> zero rows;
                # legacy event -> row with file_hash=None). RETURN e.file_hash
                # so byte-identical re-ingest is counted as skipped.
                exists_rows = proj.g.query(
                    "MATCH (e:Event {eventId:$eid}) RETURN e.file_hash",
                    params={"eid": doc_id},
                ).result_set

                props = {
                    "title": title,
                    "document_kind": doc_kind,
                    "document_knowledge_domain": domain,
                    "authored_by": frontmatter.get("authoredBy", ""),
                    "owned_by": frontmatter.get("ownedBy", ""),
                    "managed_by": frontmatter.get("managedBy", ""),
                    "governing_agreement": frontmatter.get("governedBy", frontmatter.get("governingAgreement", "")),
                    "doc_status": frontmatter.get("doc_status", "draft"),
                    "format": "markdown",
                    "version": frontmatter.get("version", ""),
                    "createdAt": frontmatter.get("created", now),
                    "updatedAt": frontmatter.get("updated", now),
                    "eventKind": "DocumentCreated",
                    "classificationLevel": "internal",
                    "file_hash": file_hash,
                }

                if not exists_rows:
                    # New document
                    proj.g.query(
                        "CREATE (e:Event {eventId:$eid}) SET e += $props",
                        params={"eid": doc_id, "props": props},
                    )
                    ingested += 1
                elif exists_rows[0][0] == file_hash:
                    # Byte-identical re-ingest — nothing to do (#330)
                    skipped += 1
                else:
                    # Changed content, or legacy event without a stored hash
                    # (None) — update and backfill the hash.
                    proj.g.query(
                        "MATCH (e:Event {eventId:$eid}) SET e += $props",
                        params={"eid": doc_id, "props": props},
                    )
                    updated += 1

            # Progress checkpoint every 100 files
            if progress_file and (i + 1) % 100 == 0:
                _save_progress(progress_file, str(directory), len(files),
                              ingested + updated + skipped + failed,
                              ingested, updated, skipped, failed, errors,
                              completed_files=completed_files)

        monitoring.record_ingest()

        if progress_file:
            _save_progress(progress_file, str(directory), len(files),
                          ingested + updated + skipped + failed,
                          ingested, updated, skipped, failed, errors,
                          completed_files=completed_files)

        return {"ingested": ingested, "updated": updated, "skipped": skipped,
                "failed": failed, "errors": errors}

    def mine_corpus(self, directory: str, *, extract_entities: bool = True,
                    progress_file: str | None = None, model=None,
                    event_log_path: str | None = None) -> dict:
        """Batch-mine a session corpus (J-1, plan §6.1) into this graph.

        COMPOSES :meth:`ingest_corpus` (security, resume, file_hash — R17):
        each file is first indexed as an AgentSession Event by the shared
        machinery, then mined via ConversationMiner (Points/Operators/Events +
        Phase-2 entity Objects with aboutObject/aboutEvent wiring, DE2E-1).
        ``event_log_path`` routes mining events to the given JSONL log
        (default: this SDK's configured event log, else a fallback next to
        the DB path).

        Returns: {sessions, ingested, updated, skipped, failed, entities,
        objects, dedup_hits, drafts, errors:[{file, error, retryable}]}.
        Unchanged re-runs report ``skipped`` via file_hash and add no new
        entities/objects (DE2E-N8).
        """
        from tortoise.mining import mine_corpus_with_sdk
        return mine_corpus_with_sdk(
            self, directory, extract_entities=extract_entities,
            progress_file=progress_file, model=model,
            event_log_path=event_log_path,
        )

    # ── Entity Resolution (GAP-01 #6987) ──────────────────────

    def suggest_entry_points(self, query: str, *, limit: int = 5,
                             kind_filter: str | None = None,
                             graph_ranker=None) -> list[dict]:
        """Entity resolution — NL query → matching entities from the graph.

        String match on content (Cypher CONTAINS) + embedding fallback.
        Returns [{id, name, kind, confidence}] sorted by confidence DESC.
        kind_filter filters by n.pointKind.
        graph_ranker: optional GraphRanker (tortoise.ranking) to rerank the
        results with graph signals (persisted EP confidence, connectivity,
        recency) — #25. Off by default for backward compatibility.
        """
        if limit < 1:
            raise ValueError(f"limit must be >= 1, got {limit}")
        q = query.strip()[:500]  # ponytail: bound to 500 chars; embedding APIs have token limits
        if not q:
            return []

        proj = self._get_proj()
        clauses = ["n.is_operator = false",
                   "toLower(n.content) CONTAINS toLower($q)"]
        params = {"q": q}
        if kind_filter:
            clauses.append("n.pointKind = $kf")
            params["kf"] = kind_filter

        where = " AND ".join(clauses)
        rows = proj.g.query(
            f"MATCH (n:Point) WHERE {where} "
            "RETURN n.id, n.content, n.pointKind",
            params=params,
        ).result_set

        results = []
        q_lower = q.lower()
        for pid, content, kind in rows:
            # ponytail: guard empty content (stub nodes may have '')
            if not content:
                continue
            # Confidence formula (#22): exact match → 1.0, partial match →
            # [0.5, 1.0) via length ratio, smoothed to avoid scale collapse.
            # len(q)/len(content) alone would give 0.001 for 1-char in 1000-char
            # doc and 0.5 for 5-char in 10-char — not comparable. The 0.5 offset
            # ensures all substring matches score ≥ 0.5, reserving [0, 0.5) for
            # the hybrid fallback path (which has no substring match at all).
            # The fallback band-normalizes its RRF scores into [0, 0.5) (#22).
            if content.lower() == q_lower:
                confidence = 1.0
            else:
                ratio = len(q) / len(content)
                confidence = round(0.5 + 0.5 * ratio, 4)
            results.append({"id": pid, "name": content, "kind": kind or "", "confidence": confidence})

        results.sort(key=lambda r: r["confidence"], reverse=True)
        results = results[:limit]

        # Hybrid fallback if no string matches (Phase 0, #7748).
        # Confidence contract (#22): fallback results must live in the [0, 0.5)
        # band reserved by the substring-match formula above. Raw RRF scores are
        # NOT comparable to that band — rank-based fusion caps near 0.016 per
        # ranked list (~0.05 with 3 fused lists) while embedded FTS raw scores
        # are unbounded above — so `rrf * 0.5` landed anywhere from ~0.008 to
        # >1.0, tripping downstream conf > 0.3 thresholds. Band-normalize:
        # scale each RRF score by the set's max so the strongest fallback hit
        # lands at 0.49 (just under the 0.5 boundary) and weaker hits scale
        # proportionally. Invariant to the number of fused ranked lists (no
        # hardcoded multiplier).
        if not results:
            fts_results = self.tortoise_fts_query(q, limit=limit)
            results = []
            max_rrf = max(
                (r.get("scores", {}).get("rrf", 0.0) for r in fts_results),
                default=0.0,
            )
            # No fusion signal at all (max_rrf == 0, e.g. TF-IDF fallback with
            # all-zero similarity) → return NOTHING: every result would carry
            # confidence 0.0, which is indistinguishable from 'no match' and
            # pollutes suggest_entry_points with decoys for garbage queries
            # (stale test #test_no_match_returns_empty).
            if max_rrf <= 0:
                return []
            for r in fts_results:
                rrf = r.get("scores", {}).get("rrf", 0.0)
                confidence = round(0.49 * rrf / max_rrf, 4)
                results.append({"id": r["id"], "name": r.get("content", ""),
                                "kind": r.get("point_kind", ""), "confidence": confidence})
            results.sort(key=lambda r: r["confidence"], reverse=True)

        # #25: optional graph-informed rerank (persisted EP confidence,
        # operator connectivity, recency). Off by default (backward compat).
        if graph_ranker is not None and results:
            results = graph_ranker.rerank(results, entity_type="point")

        return results

    # ── Session Context (#6989) ──────────────────────────────

    def session_context(self) -> dict:
        """Return 'what happened last session' — diary entries, Points, Events, confidence changes.
        Returns structured dict with explicit 'no_prior_sessions' when graph is empty.

        #2207: recent_points / confidence_changes are digest surfaces — rule/config
        noise and markdown fragments ('---', '*Gate: filed as child issue…', table
        rows, 'Label: value' lines) are excluded so the session-start digest lists
        actual decisions/claims only.
        """
        proj = self._get_proj()
        diary_entries = [r[0] for r in proj.g.query(
            "MATCH (n:Point {pointKind:'diary'}) "
            "WHERE n.is_operator = false "
            "RETURN properties(n) ORDER BY n.createdAt DESC LIMIT 10"
        ).result_set]
        recent_points = [r[0] for r in proj.g.query(
            "MATCH (n:Point) "
            "WHERE n.is_operator = false "
            "RETURN properties(n) ORDER BY n.createdAt DESC LIMIT 20"
        ).result_set]
        recent_events = [r[0] for r in proj.g.query(
            "MATCH (e:Event) RETURN properties(e) ORDER BY e.startedAt DESC LIMIT 20"
        ).result_set]
        confidence_changes = [
            {"id": r[0], "content": r[1], "pointKind": r[2],
             "confidence": r[3], "updatedAt": r[4]}
            for r in proj.g.query(
                "MATCH (n:Point) WHERE n.confidence IS NOT NULL "
                "AND n.is_operator = false "
                "RETURN n.id, n.content, n.pointKind, n.confidence, n.updatedAt "
                "ORDER BY n.updatedAt DESC LIMIT 20"
            ).result_set
        ]
        # #2207: rule/config noise must not reach the digest as 'recent decisions'.
        recent_points = [p for p in recent_points
                         if not _is_digest_noise(p.get("content"))]
        confidence_changes = [c for c in confidence_changes
                              if not _is_digest_noise(c.get("content"))]
        no_prior = not diary_entries and not recent_points and not recent_events
        return {
            "no_prior_sessions": no_prior,
            "diary_entries": diary_entries,
            "recent_points": recent_points,
            "recent_events": recent_events,
            "confidence_changes": confidence_changes,
        }

    # ── Issue Insight (#1196) ────────────────────────────────────
    # Review c70: the semantic stage must not report unrelated hits as
    # "relates to this issue". Gate: EP-confirmed claims (confidence_mean
    # >= 0.5) count, and so do hits sharing >= 2 tokens with the query text
    # (a single-token TF-IDF coincidence — e.g. one shared word like
    # "unrelated" — is a false positive, not prior knowledge). Works across
    # both retrieval modes: FTS/RRF (EP-annotated) and TF-IDF fallback
    # (ep=None, zero-overlap matches are pure noise).
    _ISSUE_INSIGHT_MIN_EP_CONFIDENCE = 0.5
    _ISSUE_INSIGHT_MIN_SHARED_TOKENS = 2
    _ISSUE_INSIGHT_TOKEN_RE = re.compile(r"[a-z0-9']+")

    def issue_insight(self, title: str, body: str | None = None,
                      repo: str | None = None, limit: int = 2) -> dict:
        """Return a compact 'there's more in the graph' insight for a would-be issue.

        Surface (b) of #1196: called by the creating agent at issue-creation time
        BEFORE filing. Two stages:
          * Semantic (always): hybrid search on title+body for cross-session
            decisions / EP-tagged claims ('we already decided this'). Hits must
            clear a relevance gate (EP confidence >= 0.5, or >= 2 shared tokens
            with the query) — otherwise the stage reports no matches instead of
            counting false positives.
          * Repo (when repo= given): structural count of indexed GitHub
            observation points for that repo (source='github').
        Fail-closed: empty graph -> no_prior_knowledge; repo given + graph
        non-empty + zero points for repo -> repo_not_indexed; never raises
        (graph-service failure -> error dict via _safe at the handler).
        data_points never exceeds `limit` rows (repo-stage append is capped).
        """
        proj = self._get_proj()
        count_rows = proj.g.query(
            "MATCH (n:Point) WHERE (n.is_operator IS NULL OR n.is_operator = false) "
            "RETURN count(n)"
        ).result_set
        total_points = int(count_rows[0][0]) if count_rows else 0

        text = " ".join(p for p in (title or "", body or "") if p and p.strip()).strip()
        semantic_hits: list[dict] = []
        if text:
            # Fetch a wider candidate pool than `limit` (review P3): the gate
            # below filters post-retrieval, so a relevant hit ranked just past
            # the limit would otherwise be invisible at small limits.
            semantic_hits = self.tortoise_fts_query(
                text, entity_type="point", limit=max(limit * 5, 20),
            )
            # c70: drop hits that clear neither the EP-confidence floor nor a
            # shared-token floor (TF-IDF fallback hits carry no EP annotation;
            # FTS-mode hits are EP-annotated but unmeasured points must still
            # show real lexical overlap to count).
            semantic_hits = [
                h for h in semantic_hits
                if self._issue_insight_relevant(h, text)
            ][:limit]

        repo_points: list[dict] = []
        if repo:
            repo_points = self.query(
                kind="observation", source="github", github_repo=repo
            )

        if total_points == 0:
            return {
                "has_prior": False,
                "no_prior_knowledge": True,
                "repo_not_indexed": False,
                "data_points": [],
                "insight": "The graph has no prior knowledge yet — nothing to pull. "
                            "Run tortoise_onboarding_github_index to seed it.",
                "more_in_graph": None,
                "repo_stats": None,
            }

        data_points: list[dict] = []
        for h in semantic_hits:
            dp = {"kind": h.get("point_kind"), "content": (h.get("content") or "")[:200]}
            if h.get("ep") and h["ep"].get("confidence_mean") is not None:
                dp["confidence_mean"] = round(h["ep"]["confidence_mean"], 3)
            data_points.append(dp)

        repo_stats = None
        if repo:
            repo_stats = {
                "repo": repo,
                "prior_issues": len(repo_points),
                "open": sum(1 for p in repo_points if p.get("github_state") == "open"),
            }
            # Cap the repo-stage append so data_points never exceeds `limit`
            # (semantic hits are already truncated to `limit` above).
            if repo_points and len(data_points) < limit:
                data_points.append({
                    "kind": "repo",
                    "content": f"{repo}: {len(repo_points)} prior issue(s) in the graph",
                })

        if repo and not repo_points:
            return {
                "has_prior": True,
                "no_prior_knowledge": False,
                "repo_not_indexed": True,
                "data_points": data_points,
                "insight": f"Graph is populated but '{repo}' has no indexed issues yet — "
                            "run tortoise_onboarding_github_index to index it.",
                "more_in_graph": None,
                "repo_stats": None,
            }

        # c70: nothing cleared the semantic gate -> "no matches", even when the
        # repo stage found points (repo context is still reported via repo_stats).
        if not semantic_hits:
            return {
                "has_prior": False,
                "no_prior_knowledge": False,
                "repo_not_indexed": False,
                "data_points": [],
                "insight": "No graph matches for this issue title.",
                "more_in_graph": None,
                "repo_stats": repo_stats,
            }

        top = semantic_hits[0]
        return {
            "has_prior": True,
            "no_prior_knowledge": False,
            "repo_not_indexed": False,
            "data_points": data_points,
            "insight": f"{len(semantic_hits)} graph hit(s) relate to this issue — check the graph before filing.",
            "more_in_graph": ((top.get("content") or "")[:80] if top else None),
            "repo_stats": repo_stats,
        }

    def _issue_insight_relevant(self, hit: dict, query_text: str) -> bool:
        """#1196 review c70 — semantic-stage relevance gate.

        A hit counts as "relates to this issue" when it is EP-confirmed
        (confidence_mean >= 0.5 — the 'we already decided this' signal) OR it
        shares >= 2 tokens with the query text. The token floor protects the
        TF-IDF fallback path (ep=None) from single-token coincidences and
        keeps unmeasured FTS hits out unless they show real lexical overlap.
        """
        ep = hit.get("ep")
        if ep is not None and ep.get("confidence_mean") is not None \
                and ep["confidence_mean"] >= self._ISSUE_INSIGHT_MIN_EP_CONFIDENCE:
            return True
        q_tokens = set(self._ISSUE_INSIGHT_TOKEN_RE.findall(query_text.lower()))
        c_tokens = set(self._ISSUE_INSIGHT_TOKEN_RE.findall((hit.get("content") or "").lower()))
        return len(q_tokens & c_tokens) >= self._ISSUE_INSIGHT_MIN_SHARED_TOKENS

    # ── Ask-path hit annotation (#1987 Task 4) ──────────────────

    def annotate_ask_hits(self, hits: list[dict]) -> list[dict]:
        """Ask-path-local hit annotation: close the ``SearchResult.to_dict()``
        gap (sessionId present, ``session_date``/``speaker`` absent) so
        ``render_context``'s date markers + speaker decoration and the
        temporal/KU fragments function on the ask path (#1987 Task 4).

        One BATCH Cypher over the returned hits' ids (never N+1): a join to
        the ``:Event`` node (``eventId``) for ``startedAt`` and to the source
        turn ``:Point`` (``source_turn_id``) for ``speaker``. Produces ADDITIVE
        keys only — undated hits render byte-identical:

          * ``session_date`` — ``startedAt[:10]`` from the Event join;
          * ``speaker`` — the hit's own ``speaker`` prop, else the source
            turn's ``speaker``, else "" (the ``_render_block`` role-bracket
            guard suppresses double-attribution);
          * ``session_id`` — the Event's ``sessionId`` when the join yields
            one (hits lacking ``sessionId`` but sharing an Event join group
            together for the per-session dedup — P2-20), else the hit's own
            value unchanged.

        D8 supersession/validity keys are ALREADY attached to point hits by
        ``tortoise_fts_query`` via ``fetch_point_epistemic_state`` — this
        method MUST NOT re-fetch them; they ride through untouched (no drift
        risk, no duplicate join). ``has_answer`` and every other passthrough
        key survive unchanged. The annotation covers the full returned set
        (the ask lane retrieves with ``limit=DEFAULT_CONTEXT_ITEM_CAP``).
        """
        if not hits:
            return hits
        proj = self._get_proj()
        ids = [str(h.get("id")) for h in hits if h.get("id")]
        if not ids:
            return hits
        try:
            rows = proj.g.query(
                "MATCH (n:Point) WHERE n.id IN $ids "
                "OPTIONAL MATCH (ev:Event) WHERE ev.eventId = n.eventId "
                "OPTIONAL MATCH (t:Point) WHERE t.id = n.source_turn_id "
                "RETURN n.id, ev.startedAt, n.speaker, t.speaker, "
                "ev.sessionId, n.sessionId",
                params={"ids": ids},
            ).result_set
        except Exception:
            # A whole-batch annotation raise maps to 502 retrieval_unavailable
            # upstream (the ask lane); the hits are returned untouched so the
            # caller's error map is the single authority.
            raise
        joined: dict[str, dict] = {}
        for row in rows:
            pid = row[0]
            ev_started = (row[1] or "") if len(row) > 1 else ""
            own_speaker = (row[2] or "") if len(row) > 2 else ""
            turn_speaker = (row[3] or "") if len(row) > 3 else ""
            ev_session = (row[4] or "") if len(row) > 4 else ""
            n_session = (row[5] or "") if len(row) > 5 else ""
            joined[pid] = {
                "session_date": (str(ev_started)[:10]
                                 if ev_started else ""),
                "speaker": own_speaker or turn_speaker or "",
                "session_id": ev_session or n_session,
            }
        out = []
        for h in hits:
            ann = joined.get(str(h.get("id")))
            if ann is None:
                # null-join hit (no Event node / no source turn): additive
                # keys stay absent — byte-identical rendering, no crash.
                out.append(h)
                continue
            enriched = dict(h)
            # additive keys only — never overwrite existing values
            enriched.setdefault("session_date", ann["session_date"])
            enriched.setdefault("speaker", ann["speaker"])
            if ann["session_id"]:
                enriched.setdefault("session_id", ann["session_id"])
            out.append(enriched)
        return out

    # ── Hybrid Search (Phase 0, #7748) ───────────────────────────

    def tortoise_fts_query(
        self,
        query: str | None = None,
        kind: str | None = None,
        *,
        entity_type: str = "point",
        structural_kind: str | None = None,
        structural_hops: int = 0,
        min_confidence: float = 0.0,
        order_by: str = "relevance",
        graph_ranker=None,
        limit: int = 10,
        threshold: float = 0.0,
        relationship_filter: str | None = None,
        traversal_path: str | None = None,
        exclude_status: list[str] | None = None,
        include_terminal: bool = False,
        _elevated_timeout_ms: int | None = None,
        pool_size: int | None = None,
        leg_trace: list[dict] | None = None,
        recency_field: str | None = None,
        recency_boost: float = 0.0,
        keep_numeric: bool = False,
        search_keys_prf: bool = False,
        fusion_weights: dict | None = None,
        fusion_k: int = 60,
        w4_enrich: bool = True,
    ) -> list[dict]:
        """Hybrid search with RRF fusion + EP annotation.

        entity_type: 'point' (default), 'event', 'subject', 'document', 'object', 'operator', or 'source'.
        Full-scan mode: omit query, set kind → all Points of that kind.
        Best-match mode: provide query → RRF fusion of FTS + vector + structural.
        include_terminal (#1391): default False — terminal-status Points
        (retracted, superseded, outdated, archived) are excluded from the
        BASE retrieval; pass True to surface them (audit/history queries).

        pool_size: EXACT per-strategy retrieval depth override (benchmark/tests).
        Precedence: pool_size > TORTOISE_POOL_FLOOR env > the baked floor
        (the product's DEFAULT_POOL_SIZE = 120, #1947/G2). pool_size/floor
        only raise the candidate window (str_limit); the RETURNED limit is
        unchanged — truncation at result_ids[:limit] precedes EP
        decoration, so a deeper pool has zero decoration cost. pool_size
        is an EXACT override: a value below limit*2 LOWERS the pool to
        pool_size (the env floor only raises — max(limit*2, floor)).
        #1348. Resolution is the product's
        (tortoise/retrieval.py::resolve_pool_size).

        Point results annotated with EP breakdown (confidence_mean + evidence + contention).
        Non-Point entities skip EP annotation.
        min_confidence defaults to 0.0 (no filter).

        relationship_filter: 'predicate:target_id' — only return points connected to
            target_id via an operator with label=predicate (e.g., 'addresses:customerSegment-1').
        traversal_path: 'FromKind→ToKind' — only return points that participate in a
            pack-declared relation chain (e.g., 'Product→Feature'). Resolved via pack registry.
        exclude_status: Point status values to EXCLUDE from results, applied to the
            fused candidate set BEFORE the final limit truncation (so filtering cannot
            silently shrink the result count — epic #898 recall_state). Default None =
            no filtering (existing behavior unchanged; retracted is already excluded at
            the retrieval layer, #689). Points with no status property are kept.
        _elevated_timeout_ms: PRIVATE — benchmark-only (#316). Threads an elevated
            collective-cap override into degradation_chain to measure uncensored
            true-completion latency. Default None = production 500ms cap. Never
            passed by production callers.
        leg_trace (R3 #1542 D4): PRIVATE trace contract — when provided,
            appends per-leg entries (vector/fts/structural/fallback — the
            R2 #1541 shared shape {"leg", "ran", "degraded", "reason",
            "count"}) so the dense leg's contribution is recorded per call,
            never null (E2E-1 leg-mix precondition). ``no_embedder`` vs
            ``encode_failed`` are distinguished in the query_vec block; the
            ``fallback`` entry is appended last on every early-return branch
            (snapshot TF-IDF path, legacy fallback_tfidf path, non-point
            return []). Default None = no trace, byte-identical behavior.
        structural_kind (R4 #1543): keyword-only, default None — the kind
            passed to the STRUCTURAL strategy only (kind-scan), WITHOUT
            triggering the post-retrieval kind filter. Deliberately distinct
            from ``kind``: the eval's pool mixes kinds (turn points + raw
            transcripts + extracted points — the R1 union design), so a
            top-level filter would drop the raw-chunk leg. Legacy callers
            pass kind → struct_kind + post-filter exactly as today.
        structural_hops (R4 #1543): keyword-only, default 0 (off) — after
            the degradation chain returns, expands the FTS+vector hit ids
            over [:IMPL|NAND*1..N] edges (hop-1 = 1.0, hop-2 = 0.5) and
            folds the neighbors into the structural strategy's ranked list
            (deduped) — the graph-as-recall-amplifier pass. Default 0 skips
            the expansion entirely (byte-identical to pre-R4).
        recency_field (R5 #1544): keyword-only, default None (off) — the
            Cypher property name whose value re-ranks the fused pool by
            date (e.g. ``createdAt`` on Points, ``startedAt`` on Events;
            must match the STORED key — ``createdAt``, not the ``created_at``
            API alias). One batch date fetch per call; the rank-based
            factor comes from ``_recency_factors`` (newest → 1.0, undated →
            0.0) and multiplies each doc's RRF score by
            ``(1 + recency_boost × factor)``. Applied AFTER the RRF fusion +
            threshold filter, BEFORE the kind filter — re-ranks the fused
            candidate set so truncation picks up the new order. Any graph
            error → fail-open to plain RRF (logged, never crashes).
        recency_boost (R5 #1544): multiplier strength, clamped 0.0–10.0.
            Default 0.0 = off (byte-identical output to pre-R5).
        keep_numeric (A1 #2070): ask-lane numeric-token policy — all-digit
            tokens (money/quantity amounts) survive the sparse tokenizer so
            SAME-VALUE dollar turns become retrievable. Default False = the
            search lane stays byte-identical (numeric tokens still dropped).
        search_keys_prf (A4 #2070): pseudo-relevance-feedback expansion — a
            bounded SECOND FTS pass whose OR-union is the original query's
            tokens (slots reserved) PLUS additive aliases harvested from the
            retrieved pool's top-5 hits' ``search_keys`` (never replacing
            original tokens; bounded raise to 12 + 8 terms). Default False =
            single pass, byte-identical. Ask lane passes True.
        fusion_weights (A3 #2070): explicit per-strategy RRF weights
            override. Default None = the shared global resolution
            (TORTOISE_FUSION_WEIGHTS env → the shipped ``{"vector": 1.5}``)
            unchanged. Ask lane passes its own TORTOISE_ASK_FUSION_WEIGHTS
            knob through this slot.
        fusion_k (A3 #2070): the RRF damping constant override (the
            historical Cormack k=60). Default 60 = unchanged. Ask lane
            threads its TORTOISE_ASK_FUSION_K knob through this slot.
        """
        from .search_engine import (  # noqa: I001
            classify_query, degradation_chain, rrf_fusion,
            annotate_ep_batch, get_relationships_bounded,
            fetch_point_epistemic_state, fallback_tfidf,
            SearchResult, SearchScores,
            filter_by_relationship, filter_by_traversal_predicate,
            expand_structural_hops,
            _recency_factors,
            _trace_entry,
        )
        # W4 (#2101): the additive why-layer enrichment (shared assembly —
        # DM-1; lazy import — why.py pulls search_engine helpers only).
        from .why import (
            enrich_items as w4_enrich_items,
            w4_enrichment_enabled,
        )

        if entity_type not in ("point", "event", "subject", "document", "object", "operator", "source"):
            raise ValueError(f"entity_type must be 'point', 'event', 'subject', 'document', 'object', 'operator', or 'source', got {entity_type!r}")
        if limit < 1 or limit > 10000:
            raise ValueError(f"limit must be 1-10000, got {limit}")
        if not (0.0 <= threshold <= 1.0):
            raise ValueError(f"threshold must be 0.0-1.0, got {threshold}")
        if not (0.0 <= min_confidence <= 1.0):
            raise ValueError(f"min_confidence must be 0.0-1.0, got {min_confidence}")
        if not (0.0 <= recency_boost <= 10.0):
            raise ValueError(
                f"recency_boost must be 0.0-10.0, got {recency_boost!r}")
        if order_by not in ("relevance", "confidence", "graph"):
            raise ValueError(f"order_by must be 'relevance', 'confidence', or 'graph', got {order_by!r}")

        proj = self._get_proj()
        graph = proj.g
        label = entity_type.capitalize()  # point→Point, event→Event, subject→Subject
        # Operator: Point nodes with is_operator=true, kind=op_type
        # Source: Source nodes, kind=sourceKind
        kind_field = {"point": "pointKind", "event": "eventKind", "subject": "subjectKind", "document": "documentKind", "object": "objectKind", "operator": "op_type", "source": "sourceKind"}[entity_type]

        # 1. Classify query → determine active strategies
        strategies = classify_query(query, kind)
        is_full_scan = (query is None and kind is not None)

        # Expand kind early for pack-aware structural query + kind filter
        expanded_kinds = self._expand_kind(kind) if kind else None

        # 2. Get query vector if needed (all core entity types now have embeddings #7845)
        # R3 (#1542) D4: no_embedder vs encode_failed are distinguished —
        # both leave query_vec None (the vector strategy is never submitted),
        # but the trace records WHY: a run can never confuse "no embedder
        # installed" with "embedder present but broken" (present-but-broken
        # used to be conflated into query_vec=None silently).
        query_vec = None
        _vec_reason: str | None = None
        if strategies.get("vector") and query and query.strip():
            from .embeddings import EmbeddingModel
            model = None
            try:
                model = EmbeddingModel.get()
            except Exception:  # noqa: BLE001, RUF100
                model = None
            if model is None:
                _vec_reason = "no_embedder"
            else:
                try:
                    query_vec = model.encode([query])[0].tolist()
                except Exception:  # noqa: BLE001, RUF100
                    _vec_reason = "encode_failed"
        if _vec_reason is not None and leg_trace is not None:
            # Recorded at the source (the strategy is never submitted) —
            # BEFORE degradation_chain merges its own entries, so the
            # vector entry always precedes the fts/structural merge.
            leg_trace.append(_trace_entry(
                "vector", ran=False, degraded=True,
                reason=_vec_reason, count=0))

        # 3. Run retrieval with degradation
        is_embedded = getattr(proj, '_is_embedded', True)
        # Full-scan mode: no truncation — return ALL Points in context (#7811 completeness)
        # #1947 (audit G2): the product OWNS the pool-depth number. The floor
        # now BAKES the product's ``DEFAULT_POOL_SIZE`` (120) instead of the
        # historical limit*2 (20 at limit=10): the LongMemEval depth finding
        # (66% of marked evidence never entered a 60-item pool) applies
        # equally to the product's default 20-item window, and the deepened
        # candidate window is the single highest-leverage retrieval change.
        # Resolution is delegated to ``tortoise.retrieval.resolve_pool_size``:
        # pool_size exact override > TORTOISE_POOL_FLOOR env > the baked
        # 120 floor (limit*2 stays the per-call lower bound). pool_size is
        # validated up front (any mode, incl. full-scan) for consistency
        # with the limit validation bound (code-review P2 fix).
        if pool_size is not None and (pool_size < 1 or pool_size > 10000):
            raise ValueError(f"pool_size must be 1-10000, got {pool_size}")
        if is_full_scan:
            str_limit = 100000
        else:
            str_limit = resolve_pool_size(
                limit * 2,
                pool_size=pool_size,
                env_name="TORTOISE_POOL_FLOOR",
                default=DEFAULT_POOL_SIZE,
                exact=True,
            )
        # R4 (#1543): the structural strategy's kind — an explicit override
        # that does NOT trigger the post-retrieval kind filter below (the
        # eval's pool mixes kinds: turn points + raw transcripts + extracted
        # points — a top-level filter would drop the raw-chunk leg). Legacy
        # callers keep kind → structural kind + post-filter exactly as today.
        struct_kind = structural_kind if structural_kind is not None else kind
        raw_results = degradation_chain(
            graph, query, struct_kind, query_vec, strategies,
            entity_type=entity_type, limit=str_limit,
            is_embedded=is_embedded,
            elevated_timeout_ms=_elevated_timeout_ms,
            # #1359: the recorded vector-index API (procedure vs Cypher-native)
            # lets run_vector_query skip the failing signature attempt.
            vector_index_api=getattr(proj, "_vector_index_api", None),
            excluded_statuses=() if include_terminal else None,
            leg_trace=leg_trace,
            # A1 (#2070): the ask-lane numeric-token policy threads into the
            # sparse leg's OR-union (default False = search lane unchanged).
            keep_numeric=keep_numeric,
        )

        if not raw_results:
            # All strategies failed — fallback to in-memory TF-IDF (Point only).
            if query and entity_type == "point":
                # #1375: serve from the cached lean corpus snapshot when
                # available (kills the ~350ms full-payload re-fetch + per-call
                # TF-IDF re-fit). Falls back to the legacy path when the
                # snapshot is unavailable (too big / build failed).
                from tortoise.fallback_snapshot import (  # noqa: I001
                    _store as _fb_store, snapshot_key, build_snapshot,
                    search_snapshot,
                )
                _key = snapshot_key(proj, getattr(self, "_namespace", None))
                _snap = _fb_store.get(_key)
                if _snap is None:
                    _snap = build_snapshot(proj)
                    if _snap is not None:
                        _fb_store.put(_key, _snap)
                if _snap is not None:
                    snap_hits = search_snapshot(
                        query, _snap, limit=limit, kind=kind,
                        exclude_status=exclude_status,
                        include_terminal=include_terminal,
                    )
                    if leg_trace is not None:
                        leg_trace.append(_trace_entry(
                            "fallback", ran=True, degraded=True,
                            reason="tfidf_snapshot", count=len(snap_hits)))
                    return _decorate_fallback_hits(snap_hits, graph)
                points = self.query(kind=kind,
                                    include_retracted=include_terminal)
                if exclude_status and points:
                    # Same status exclusion as step 5d (#898 review round-2):
                    # the degraded fallback must not leak superseded/deprecated
                    # into the UC1 state view. self.query returns raw node
                    # dicts carrying the status property.
                    points = [p for p in points
                              if (p.get("status") or "") not in set(exclude_status)]
                legacy_hits = fallback_tfidf(query, points, limit=limit)
                if leg_trace is not None:
                    leg_trace.append(_trace_entry(
                        "fallback", ran=True, degraded=True,
                        reason="tfidf_legacy", count=len(legacy_hits)))
                return _decorate_fallback_hits(legacy_hits, graph)
            if leg_trace is not None:
                leg_trace.append(_trace_entry(
                    "fallback", ran=True, degraded=True,
                    reason="no_fallback_applicable", count=0))
            return []

        # R4 (#1543): 1-2 hop IMPL/NAND expansion on TEXT hits (graph as
        # recall amplifier — graphiti episode-mentions pattern). Folds into
        # the structural strategy so RRF fusion, match_source, and the E2E-1
        # leg-mix keep the established leg vocabulary (fts/vector/structural/
        # tfidf). Seeds = FTS + vector hit ids only — never the structural
        # kind-scan hits (no circular amplification). Default structural_hops
        # = 0 skips the block entirely (byte-identical to pre-R4); the TF-IDF
        # fallback above returns before this block, so degraded runs are
        # unchanged too.
        if structural_hops > 0:
            text_hit_ids = [
                pid
                for _strat in ("fts", "vector")
                for pid, _score in raw_results.get(_strat, [])
            ]
            if text_hit_ids:
                expansion = expand_structural_hops(
                    graph, text_hit_ids, max_hops=structural_hops,
                    limit=str_limit,
                    excluded_statuses=() if include_terminal else None,
                    timeout_ms=int(_elevated_timeout_ms or 500),
                )
                if expansion:
                    merged = list(dict(raw_results.get("structural", [])).items())
                    seen = {pid for pid, _score in merged}
                    merged.extend(
                        (pid, score) for pid, score in expansion
                        if pid not in seen)
                    raw_results["structural"] = merged

        # A4 (#2070): search_keys pseudo-relevance-feedback expansion (ask
        # lane, caller-gated). A bounded SECOND FTS pass whose OR-union is
        # the original query's tokens (slots reserved per build_or_query's
        # cap) PLUS additive aliases harvested from the retrieved pool's
        # top-5 hits — never replacing original tokens. Fail-open: any fetch/
        # query failure keeps the ORIGINAL fts leg (byte-identical); the
        # expansion is additive recall only.
        if search_keys_prf and raw_results.get("fts"):
            expanded_fts = self._search_keys_prf_expansion(
                query, raw_results["fts"],
                str_limit=str_limit,
                excluded_statuses=() if include_terminal else None,
                leg_trace=leg_trace,
                # P1-fix (#2070): the second pass respects the caller's A1
                # keep_numeric — an operator who opted OUT of numeric tokens
                # must not have them silently re-introduced by the PRF pass
                # (the alias harvest stays numeric-aware; only the original
                # query's re-tokenization honors the opt-out).
                keep_numeric=keep_numeric,
            )
            if expanded_fts is not None:
                raw_results["fts"] = expanded_fts

        # 4. Fuse via RRF (skip if single strategy or full-scan)
        if is_full_scan or len(raw_results) == 1:
            strat_name, ranked = next(iter(raw_results.items()))
            # Apply threshold filter (score floor)
            fused = {pid: score for pid, score in ranked if score >= threshold}
            match_source = strat_name
        else:
            ranked_lists = list(raw_results.values())
            # #1657 weighted RRF: per-strategy fusion weights. The measured
            # fix for fusion dilution — the burn showed equal-weight RRF
            # costs ~2 nDCG pts when a strong vector leg is diluted by weak
            # FTS/structural legs. PRODUCTION DEFAULT (owner decision
            # 2026-08-25): vector 1.5x — the bge-small vector leg is the
            # strongest retriever; the HNSW surface shows hybrid > vector-only
            # (additive value from FTS/structural), so a moderate 1.5x shifts
            # toward the stronger leg without abandoning the others. Env
            # override for tuning: TORTOISE_FUSION_WEIGHTS='{"vector": 2.0}'
            # (JSON; the LongMemEval-S re-burn later will refine this).
            # A3 (#2070): the ASK lane threads its own
            # TORTOISE_ASK_FUSION_WEIGHTS / TORTOISE_ASK_FUSION_K knobs via
            # the ``fusion_weights``/``fusion_k`` kwargs — when the caller
            # passes None/60 the shared resolution below is unchanged
            # (search lane byte-identical).
            if fusion_weights is None:
                fusion_weights = {"vector": 1.5}
                _fw = os.environ.get("TORTOISE_FUSION_WEIGHTS")
                if _fw:
                    import json as _json
                    try:
                        fusion_weights = _json.loads(_fw)
                    except (ValueError, TypeError):
                        fusion_weights = {"vector": 1.5}
            fused = rrf_fusion(
                ranked_lists,
                strategy_names=list(raw_results.keys()),
                weights=fusion_weights,
                k=fusion_k,
            )
            # Apply threshold filter to RRF scores
            if threshold > 0:
                fused = {pid: score for pid, score in fused.items() if score >= threshold}
            match_source = "rrf"

        # 5. Apply kind filter BEFORE truncating (skip if structural-only already filtered)
        result_ids = list(fused.keys())
        if entity_type == "source":
            id_field = "url"
        elif entity_type == "event":
            id_field = "eventId"
        else:
            id_field = "id"
        # Graph label for MATCH (operators are Point nodes with is_operator=true)
        graph_label = "Point" if entity_type == "operator" else label

        # 4b. Recency re-rank (#1544 R5): optional date weight on the fused
        #     pool. Rank-based factor from ``recency_field`` (newest → 1.0,
        #     undated → 0.0 via ``_recency_factors``); fail-open to plain
        #     RRF on any graph error (circuit-breaker pattern). Default off
        #     → byte-identical. ``id_field`` is the existing per-entity id
        #     (``id`` for Point, ``eventId`` for Event) and ``graph_label``
        #     likewise — the one batch Cypher per call stays label-correct.
        if recency_field and recency_boost > 0 and result_ids:
            try:
                rows = graph.query(
                    f"MATCH (n:{graph_label}) WHERE n.{id_field} IN $ids "
                    f"RETURN n.{id_field}, n.{recency_field}",
                    params={"ids": result_ids},
                ).result_set
                weights = _recency_factors([(row[0], row[1]) for row in rows])
                fused = {pid: s * (1.0 + recency_boost * weights.get(pid, 0.0))
                         for pid, s in fused.items()}
                # Secondary sort key = the recency factor: at EQUAL multiplied
                # score (including the degenerate all-0.0 case — FalkorDBLite's
                # fulltext scores identical documents 0.0), the newer doc still
                # ranks first (the plan's D1 multiplier can't break a 0×1.5=0
                # tie on its own). Enabled branch only — default stays
                # byte-identical.
                fused = dict(sorted(fused.items(),
                                    key=lambda x: (x[1], weights.get(x[0], 0.0)),
                                    reverse=True))
                result_ids = list(fused.keys())
            except Exception as e:
                _logger.warning("recency weighting failed (%s) — plain RRF", e)

        if kind and query is not None and result_ids:
            expanded = expanded_kinds
            kind_ids = set()
            extra_clause = "AND n.is_operator = true" if entity_type == "operator" else ""
            try:
                if len(expanded) == 1:
                    kind_rows = graph.query(
                        f"MATCH (n:{graph_label}) WHERE n.{kind_field} = $kind {extra_clause} AND n.{id_field} IN $ids RETURN n.{id_field}",
                        params={"kind": expanded[0], "ids": result_ids},
                    ).result_set
                else:
                    placeholders = [f"$kind_{i}" for i in range(len(expanded))]
                    params_dict: dict[str, Any] = {"ids": result_ids}
                    for i, k in enumerate(expanded):
                        params_dict[f"kind_{i}"] = k
                    kind_rows = graph.query(
                        f"MATCH (n:{graph_label}) WHERE n.{kind_field} IN [{', '.join(placeholders)}] {extra_clause} AND n.{id_field} IN $ids RETURN n.{id_field}",
                        params=params_dict,
                    ).result_set
                kind_ids = {row[0] for row in kind_rows}
            except Exception:
                kind_ids = set(result_ids)  # Pass-through on error
            result_ids = [pid for pid in result_ids if pid in kind_ids]

        # 5b. Apply relationship_filter (predicate:target_id format)
        if relationship_filter and result_ids:
            parts = relationship_filter.split(":", 1)
            if len(parts) == 2:
                pred, tid = parts[0].strip(), parts[1].strip()
                if pred and tid:
                    result_ids = filter_by_relationship(
                        graph, result_ids, pred, tid,
                        entity_type=entity_type, id_field=id_field,
                    )
                else:
                    _logger.warning("Invalid relationship_filter format: %s", relationship_filter)
            else:
                _logger.warning(
                    "relationship_filter must be 'predicate:target_id', got: %s",
                    relationship_filter,
                )

        # 5c. Apply traversal_path (e.g., 'Product→Feature') — resolve via pack registry
        if traversal_path and result_ids:
            resolved = self._resolve_traversal_path(traversal_path)
            if resolved:
                pred = resolved["predicate"]
                result_ids = filter_by_traversal_predicate(
                    graph, result_ids, pred,
                    entity_type=entity_type, id_field=id_field,
                )
            else:
                _logger.warning(
                    "traversal_path %r could not be resolved to a pack relation",
                    traversal_path,
                )

        # 5d. Apply exclude_status BEFORE truncation (#898): filtering after the
        #     limit cut would silently shrink results when superseded/deprecated
        #     points dominate the pool. Points with no status are kept; only
        #     Point-label entities have status (operators are Points too).
        if exclude_status and result_ids and graph_label == "Point":
            try:
                excluded = set(exclude_status)
                status_rows = graph.query(
                    "MATCH (n:Point) WHERE n.id IN $ids AND n.status IN $statuses "
                    "RETURN n.id",
                    params={"ids": result_ids, "statuses": sorted(excluded)},
                ).result_set
                status_excluded_ids = {row[0] for row in status_rows}
                if status_excluded_ids:
                    result_ids = [pid for pid in result_ids if pid not in status_excluded_ids]
            except Exception:
                _logger.warning("exclude_status filter failed — pass-through", exc_info=True)

        # Truncate AFTER filtering
        result_ids = result_ids[:limit]

        # 6. EP annotation (Point only)
        ep_breakdowns = annotate_ep_batch(graph, result_ids) if entity_type == "point" else {}

        # 7. Fetch entity content in BATCH (not N+1)
        entity_data: dict[str, dict] = {}
        try:
            if entity_type == "point":
                rows = graph.query(
                    "MATCH (n:Point) WHERE n.id IN $ids "
                    "RETURN n.id, n.content, n.pointKind, "
                    "       coalesce(n.has_answer, false)",
                    params={"ids": result_ids},
                ).result_set
                for row in rows:
                    pid = row[0]
                    entity_data[pid] = {
                        "content": row[1],
                        "kind": row[2],
                        # A5 (#2070): the stored evidence mark rides the hit
                        # payload (mirrors the eval's point_props_for_hits)
                        # so the ask lane's evidence boost has material.
                        "has_answer": bool(row[3]),
                    }
            elif entity_type == "event":
                rows = graph.query(
                    "MATCH (n:Event) WHERE n.eventId IN $ids RETURN n.eventId, n.subject, n.eventKind",
                    params={"ids": result_ids},
                ).result_set
                for row in rows:
                    eid = row[0]
                    entity_data[eid] = {
                        "content": row[1] or "",
                        "kind": row[2] or "",
                    }
            elif entity_type == "subject":
                rows = graph.query(
                    "MATCH (n:Subject) WHERE n.id IN $ids RETURN n.id, n.name, n.subjectKind",
                    params={"ids": result_ids},
                ).result_set
                for row in rows:
                    sid = row[0]
                    entity_data[sid] = {
                        "content": row[1] or "",
                        "kind": row[2] or "",
                    }
            elif entity_type == "document":
                rows = graph.query(
                    "MATCH (n:Document) WHERE n.id IN $ids "
                    "RETURN n.id, n.title, n.documentKind, n.topics, n.summary, "
                    "n.sessionId, n.eventId, n.sourcePath",
                    params={"ids": result_ids},
                ).result_set
                for row in rows:
                    did = row[0]
                    entity_data[did] = {
                        "content": row[1] or "",
                        "kind": row[2] or "",
                        "topics": row[3] or [],
                        "summary": row[4] or "",
                        "sessionId": row[5] or "",
                        "eventId": row[6] or "",
                        "sourcePath": row[7] or "" if len(row) > 7 else "",
                    }
            elif entity_type == "object":
                rows = graph.query(
                    "MATCH (n:Object) WHERE n.id IN $ids "
                    "RETURN n.id, n.name, n.objectKind, n.status, n.supersededBy",
                    params={"ids": result_ids},
                ).result_set
                for row in rows:
                    oid = row[0]
                    entity_data[oid] = {
                        "content": row[1] or "",
                        "kind": row[2] or "",
                        # #1350: projection-owned status + successor (Object
                        # status fold; absent = honestly absent)
                        "status": row[3] or "",
                        "superseded_by": row[4] or "",
                    }
            elif entity_type == "operator":
                rows = graph.query(
                    "MATCH (n:Point {is_operator: true}) WHERE n.id IN $ids RETURN n.id, n.label, n.op_type",
                    params={"ids": result_ids},
                ).result_set
                for row in rows:
                    oid = row[0]
                    entity_data[oid] = {
                        "content": row[1] or "",  # label is searchable text
                        "kind": row[2] or "",    # op_type is kind
                    }
            elif entity_type == "source":
                rows = graph.query(
                    "MATCH (n:Source) WHERE n.url IN $ids RETURN n.url, n.title, n.sourceKind",
                    params={"ids": result_ids},
                ).result_set
                for row in rows:
                    sid = row[0]
                    entity_data[sid] = {
                        "content": row[1] or "",
                        "kind": row[2] or "",
                    }
        except Exception:
            _logger.warning("Batch content fetch failed — returning results with minimal metadata")
            for pid in result_ids:
                entity_data[pid] = {"content": "", "kind": ""}

        # 7.5. Fetch relationships for result Points (Point only) — bounded,
        #      state-centric decoration (#1353 D2/D3/D4; D12 keeps the shared
        #      unbounded get_relationships for topic_summarization).
        point_relationships = get_relationships_bounded(graph, result_ids) if entity_type == "point" else {}
        # 7.6. Promoted epistemic state (status/superseded_by/supersedes/subject) — #1353 D8/D10
        point_state = fetch_point_epistemic_state(graph, result_ids) if entity_type == "point" else {}

        # 8. Build SearchResult objects, filter, and order
        results = []
        for pid in result_ids:
            pt = entity_data.get(pid)
            if not pt:
                continue
            content, pt_kind = pt["content"], pt["kind"]
            ep = ep_breakdowns.get(pid) if entity_type == "point" else None
            # #125 capture metadata (document entity_type)
            cap_topics = pt.get("topics", [])
            cap_summary = pt.get("summary", "")
            cap_session = pt.get("sessionId", "")
            cap_event = pt.get("eventId", "")
            cap_source_path = pt.get("sourcePath", "")  # #167

            # Apply min_confidence filter (Point only; non-Point always pass)
            if entity_type == "point" and ep and ep.confidence_mean < min_confidence:
                continue

            # Build scores
            scores = SearchScores(rrf=fused.get(pid, 0.0))
            if "fts" in raw_results:
                for fid, fscore in raw_results["fts"]:
                    if fid == pid:
                        scores.fts = fscore
                        break
            if "vector" in raw_results:
                for vid, vscore in raw_results["vector"]:
                    if vid == pid:
                        scores.vector = vscore
                        break
            if "structural" in raw_results:
                for sid, sscore in raw_results["structural"]:
                    if sid == pid:
                        scores.structural = sscore
                        break

            result = SearchResult(
                id=pid,
                content=content,
                point_kind=pt_kind,
                scores=scores,
                match_source=match_source,
                ep=ep,
                relationships=point_relationships.get(pid, []),
                status=point_state.get(pid, {}).get("status", "")
                or pt.get("status", ""),
                superseded_by=point_state.get(pid, {}).get("superseded_by")
                or pt.get("superseded_by") or None,
                supersedes=point_state.get(pid, {}).get("supersedes") or [],
                # E6 (#1538) D7: promoted window fields (additive — empty
                # string when undated ⇒ no marker, byte-identical output).
                valid_from=point_state.get(pid, {}).get("valid_from")
                or pt.get("validFrom") or "",
                valid_to=point_state.get(pid, {}).get("valid_to")
                or pt.get("validTo") or "",
                expired_at=point_state.get(pid, {}).get("expired_at")
                or pt.get("expiredAt") or "",
                subject=point_state.get(pid, {}).get("subject"),
                topics=cap_topics,
                summary=cap_summary,
                session_id=cap_session,
                event_id=cap_event,
                source_path=cap_source_path,
                # A5 (#2070): stored evidence mark (additive in to_dict).
                has_answer=pt.get("has_answer", False),
            )
            results.append(result)

        # 9. Order results
        if order_by == "graph":
            # #25: graph-informed rerank — weighted fusion of similarity +
            # persisted EP confidence + operator connectivity + recency decay.
            # Requires the caller to allow a large enough candidate pool (limit
            # here is the pool size; final length is capped below).
            from .ranking import GraphRanker
            ranker = graph_ranker or GraphRanker(proj)
            dicts = [r.to_dict() for r in results]
            ranked = ranker.rerank(dicts, entity_type=entity_type)[:limit]
            # W4 (#2101): additive why-layer enrichment (flag-gated) — search
            # surface; recall_state's pool and the ask lane inherit it through
            # their own calls. Zero-LLM, bounded reads, fail-open (items
            # unchanged on any assembly error).
            if entity_type == "point" and w4_enrich \
                    and w4_enrichment_enabled():
                ranked = w4_enrich_items(proj, ranked)
            return ranked
        if order_by == "confidence":
            # #25: sort by the PERSISTED EP confidence (n.confidence, written
            # by compute_confidence), not the structural impl/(impl+nand) proxy
            # from annotate_ep_batch (which is edge-ratio, not belief).
            from .ranking import GraphRanker
            ranker = graph_ranker or GraphRanker(proj)
            signals = ranker._fetch_signals([r.id for r in results], entity_type)
            results.sort(
                key=lambda r: signals.get(r.id, {}).get("confidence", 0.5),
                reverse=True,
            )
        # Default: RRF relevance order (already in fused order)

        out = [r.to_dict() for r in results[:limit]]
        # W4 (#2101): additive why-layer enrichment (flag-gated) — see the
        # order_by=graph branch above for the contract.
        if entity_type == "point" and w4_enrich \
                and w4_enrichment_enabled():
            out = w4_enrich_items(proj, out)
        return out

    # ── A4 (#2070): search_keys PRF expansion (ask lane) ──────────────────

    def _search_keys_prf_expansion(
        self,
        query: str,
        fts_hits: list[tuple[str, float]],
        *,
        str_limit: int,
        excluded_statuses: tuple | None,
        leg_trace: list[dict] | None,
        keep_numeric: bool = False,
    ) -> list[tuple[str, float]] | None:
        """A4 (#2070): bounded second FTS pass with ADDITIVE search_keys
        expansion terms harvested from the retrieved pool's top-5 hits.

        Pseudo-relevance feedback: the top hits of the FIRST pass are the
        relevance seeds — their stored ``search_keys`` aliases (the E3
        #1535 extractor-written vocabulary) become extra OR terms for a
        second FTS pass, so turns that share the aliases but not the query's
        literal tokens surface. Injection respects ``build_or_query``'s
        OR-cap contract: the original query's tokens keep their slots
        (``DEFAULT_MAX_OR_TERMS``) and the aliases fill ONLY the bounded
        expansion tail (``DEFAULT_MAX_EXPANSION_TERMS``) — an alias can
        never displace an original token, and the expansion is additive
        recall only (never a replacement).

        Fail-open contract (the pre-mortem's "budget-compatible" guard): any
        fetch/query failure returns ``None`` and the caller keeps the
        ORIGINAL fts leg — byte-identical behavior, and the A4 pass can
        never turn a working lane into a broken one. Returns the MERGED fts
        leg (expanded run first, first-pass-only ids appended in their
        original order) when the expansion ran and returned hits; ``None``
        when there is nothing to expand (no aliases / empty result).
        """
        if not fts_hits:
            return None
        top_ids = [pid for pid, _score in fts_hits[:5] if pid]
        if not top_ids:
            return None
        proj = self._get_proj()
        try:
            rows = proj.g.query(
                "MATCH (n:Point) WHERE n.id IN $ids "
                "RETURN n.id, coalesce(n.search_keys, '')",
                params={"ids": top_ids},
            ).result_set
        except Exception:
            _logger.warning(
                "A4 search_keys fetch failed — keeping the original fts "
                "leg", exc_info=True)
            return None
        aliases: list[str] = []
        for row in rows:
            v = row[1]
            if isinstance(v, (list, tuple)):
                aliases.extend(str(x) for x in v if x)
            elif v:
                aliases.append(str(v))
        aliases = [a.strip() for a in aliases if a and a.strip()]
        if not aliases:
            return None

        from .search_engine import run_fts_query
        _exp_trace: list[dict] = []
        try:
            expanded = run_fts_query(
                proj.g, query, entity_type="point", limit=str_limit,
                excluded_statuses=excluded_statuses,
                # P1-fix: honor the caller's A1 opt-out for the original
                # query's re-tokenization (the alias harvest below already
                # runs numeric-aware via expansion_tokens' keep_numeric).
                keep_numeric=keep_numeric,
                expansion_terms=aliases,
                leg_trace=_exp_trace,
            )
        except Exception:
            _logger.warning(
                "A4 expansion FTS pass failed — keeping the original fts "
                "leg", exc_info=True)
            return None
        if not expanded:
            # Empty second pass = fail-open: the ORIGINAL fts leg stays
            # (byte-identical). P2-fix (#2070): do NOT ride the degraded
            # entry into the shared trace — a healthy lane that got a
            # transiently-empty expansion must not report
            # retrieval_degraded (the A4 fail-open contract: an expansion
            # can never turn a working lane into a broken one).
            return None
        if leg_trace is not None:
            # the second pass's own per-leg entry rides the shared trace
            # (the FIRST pass's entry was already merged by degradation_chain)
            leg_trace.extend(_exp_trace)
        seen = {pid for pid, _score in expanded}
        merged = list(expanded)
        merged.extend((pid, s) for pid, s in fts_hits if pid not in seen)
        return merged

    # ── Ask lane (#1987 Task 5): the SDK answer surface ─────────────────────

    def _ask_validate(self, question: str, question_type: str | None,
                      question_date: str | None) -> None:
        """Local-lane validation — the FIRST pipeline stage (P2-8: invalid
        inputs never reach retrieval — zero model calls AND zero retrieval
        calls). Raises ``AskValidationError`` with the pinned canonical
        instance codes (matching the wire codes — P2-14)."""
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

    def ask(self, question: str, *, question_type: str | None = None,
            question_date: str | None = None, team_id: str | None = None,
            _reader_factory=None, _selfhost_transport: bool = False) -> dict:
        """Answer a question about captured memory (#1987 Task 5) — ONE
        bounded RAG pass locally (or a POST to hosted ``/v1/ask`` when
        ``TORTOISE_API_URL`` is set).

        ⛔ GATED / EXPERIMENTAL (#2013 product decision): the ask surface is
        NOT served to hosted customers — ``/v1/ask`` and the MCP
        ``tortoise_ask`` tool are OFF by default (``TORTOISE_ENABLE_ASK=1``
        unlocks them for tests/dev only). This method stays shipped as the
        EVAL's reader path (the LongMemEval benchmark runs through the
        product reader) — do not build production features on it until the
        reader-model decision is made (the benchmark will use a strong
        reader model).

        Local lane pipeline: validation FIRST (``AskValidationError``, zero
        model calls) → ``tortoise_fts_query`` (``include_terminal=True`` —
        the D8 supersession markers reach the reader; cost-bounded by the
        same 8k/40 caps) → ask-path annotation (session-date join + speaker)
        → ``dedup_pool`` (per-session cap 3, keyed on the annotated session)
        → A5 evidence-mark boost (default ON — reorders the deduped pool by
        stored ``has_answer`` marks; zero marks = no-op) → A7 rerank
        (env-gated OFF by default) → ``assemble_context`` (8000-token
        estimate cap AND 32 KiB byte cap, whole-hit drop) →
        ``detect_question_type`` (or caller override) →
        ONE reader call via ``build_reader_model()`` (never an
        LLM-skip pre-gate — exactly one model call incl. empty context) →
        ``_looks_abstained`` (blank → ``NO_EVIDENCE_TEXT``) → best-effort
        ``record_ask_usage`` (ONLY with an explicit ``team_id``; default
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
            ``TORTOISE_ASK_CONTEXT_TOKEN_CAP`` (default OFF = 40/40/8000;
            the retrieval-window limit is threaded IN TANDEM with the
            assembly caps — raising only the assemble cap changes nothing).
          * A7 rerank — ``TORTOISE_ASK_RERANK`` (default OFF, phase 2):
            cross-encoder + MMR port (tortoise/rerank.py), degrade-to-
            current contract.

        Returns the 12-field response shape: ``{answer, abstained,
        question_type, question_date, evidence, context_tokens, model,
        provider, route, cost_estimate_usd, duration_ms,
        retrieval_degraded}``. ``question_date`` is ALWAYS the RESOLVED value
        (server-now-UTC ``YYYY-MM-DD`` default when omitted; the caller
        override when provided). No retrieval time-travel v1 — the pool stays
        the live graph.

        Raises: ``AskValidationError`` (input), ``AskRetrievalUnavailable``
        (retrieval/annotation/assembly raise), ``AskReaderUnavailable``
        (the reader failed with no surviving lane).
        """
        import time as _time  # noqa: I001
        from datetime import datetime as _dt2
        from tortoise.retrieval import (
            DEFAULT_MAX_CHUNKS_PER_SESSION,
            apply_evidence_boost,
            ask_env_bool,
            assemble_context,
            dedup_pool,
            estimate_tokens_ask,
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
            return self._post_ask(question, question_type=question_type,
                                  question_date=question_date)

        t0 = _time.monotonic()
        # 1. Validation FIRST (P2-8): zero model calls AND zero retrieval
        #    calls for invalid inputs.
        self._ask_validate(question, question_type, question_date)
        if question_date is None:
            question_date = _dt2.now(UTC).strftime("%Y-%m-%d")

        # 2. Retrieval (whole-retrieval raises → AskRetrievalUnavailable).
        #    A1/A3/A4/A6 (#2070): the ask-lane retrieval knobs resolve ONCE
        #    here (env-gated; defaults = historical behavior) and thread into
        #    the one bounded RAG pass. A6's retrieval-window limit and the
        #    assembly caps resolve IN TANDEM (``resolve_ask_retrieval_caps``)
        #    — the gold is cut at ``result_ids[:limit]`` inside the retrieval
        #    call BEFORE dedup/assemble, so a cap raise that does not also
        #    raise the window changes nothing.
        caps = resolve_ask_retrieval_caps()
        keep_numeric = ask_env_bool(
            "TORTOISE_ASK_NUMERIC_TOKENS", True)       # A1, default ON
        search_keys_prf = ask_env_bool(
            "TORTOISE_ASK_SEARCH_KEYS_PRF", True)      # A4, default ON
        evidence_boost = ask_env_bool(
            "TORTOISE_ASK_EVIDENCE_BOOST", True)       # A5, default ON
        from tortoise.retrieval import (  # noqa: I001
            ASK_FUSION_WEIGHTS_ENV, ASK_FUSION_K_ENV, ask_env_int,
            ask_env_weights,
        )
        fusion_weights = ask_env_weights(ASK_FUSION_WEIGHTS_ENV, None)  # A3
        fusion_k = ask_env_int(ASK_FUSION_K_ENV, 60)                    # A3
        leg_trace: list[dict] = []
        try:
            hits = self.tortoise_fts_query(
                question, limit=caps["limit"],
                pool_size=DEFAULT_POOL_SIZE, include_terminal=True,
                leg_trace=leg_trace,
                keep_numeric=keep_numeric,
                search_keys_prf=search_keys_prf,
                fusion_weights=fusion_weights,
                fusion_k=fusion_k)
        except AskRetrievalUnavailable:
            raise
        except Exception as e:  # noqa: BLE001, RUF100 — map to the ask surface
            raise AskRetrievalUnavailable(
                f"retrieval unavailable: {type(e).__name__}") from e

        # 3. Annotation (batch raise → AskRetrievalUnavailable).
        try:
            annotated = self.annotate_ask_hits(hits)
        except AskRetrievalUnavailable:
            raise
        except Exception as e:  # noqa: BLE001, RUF100
            raise AskRetrievalUnavailable(
                f"annotation unavailable: {type(e).__name__}") from e

        # 4. Dedup (annotated session key — P2-20) → A5 evidence boost → A7
        #    rerank → assembly (8k/40/32KiB caps from ``caps``).
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
            # deduped pool untouched; the rerank never raises.
            from tortoise.rerank import ask_lane_rerank
            deduped, _rerank_stats = ask_lane_rerank(
                question, deduped, proj=self._get_proj(),
                top_k=caps["context_item_cap"])
            assembled = assemble_context(
                deduped, top_k=caps["context_item_cap"],
                max_context_tokens=caps["context_token_cap"],
                question_date=question_date,
                context_item_cap=caps["context_item_cap"],
                byte_cap=32768)
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
        except Exception as e:  # noqa: BLE001, RUF100 — map to the ask surface
            raise AskRetrievalUnavailable(
                f"context rendering unavailable: {type(e).__name__}") from e
        try:
            model = self._ask_reader_model(_reader_factory)
        except Exception as e:  # noqa: BLE001, RUF100 — a build failure is a reader failure
            raise AskReaderUnavailable(
                f"reader unavailable (build): {type(e).__name__}") from e
        try:
            raw = model.complete(
                system=system_prompt_for(qtype),
                user=build_reader_user_message(evidence, question))
        except Exception as e:  # noqa: BLE001, RUF100
            raise AskReaderUnavailable(
                f"reader unavailable: {type(e).__name__}") from e
        finally:
            decr = getattr(model, "decr_inflight", None)
            if decr is not None:
                decr()
        answer = (raw or "").strip()
        abstained = _looks_abstained(answer)
        if abstained and not answer:
            answer = NO_EVIDENCE_TEXT

        # 7. Metering (best-effort; ONLY with an explicit team_id).
        # #2069: the record's cost_usd is metered at the SERVING lane's
        # family rates (``select_ask_meter_rates`` on ``_LockedReader.model``
        # — the strong lane never under-counts at the deepseek envelope).
        if team_id:
            try:
                from tortoise.metering import record_ask_usage
                input_tokens = (estimate_tokens_ask(system_prompt_for(qtype))
                                + estimate_tokens_ask(evidence))
                out_tokens = getattr(model, "last_completion_tokens", 0) \
                    or 500
                record_ask_usage(
                    team_id,
                    tokens_in=input_tokens, tokens_out=out_tokens,
                    cost_usd=estimate_ask_cost_usd(
                        input_tokens, out_tokens,
                        rates=select_ask_meter_rates(
                            getattr(model, "model", None) or "")),
                    _selfhost_transport=_selfhost_transport)
            except Exception:  # noqa: BLE001, RUF100 — metering never blocks
                pass

        # 8. Degradation signal (leg_trace + D8-decoration-unavailable).
        degraded = any(bool(leg.get("degraded")) for leg in leg_trace)
        if not degraded:
            try:
                degraded = self._ask_d8_decoration_unavailable(hits)
            except Exception:  # noqa: BLE001, RUF100 — degrade to False
                degraded = False

        # W4 (#2101): additive why-layer entries for the evidence pool the
        # reader saw (flag-gated — the ``why`` key is ABSENT with the flag
        # OFF, keeping the 12-field response byte-identical). The hits
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
        duration_ms = int((_time.monotonic() - t0) * 1000)
        try:
            # #2069: the response's cost_estimate_usd uses the SERVING lane's
            # family rates (STRONG for qwen//upstage//anthropic// specs — a
            # strong-lane ask metered at the deepseek envelope would
            # under-count ~10×).
            cost_estimate = estimate_ask_cost_usd(
                estimate_tokens_ask(system_prompt_for(qtype))
                + estimate_tokens_ask(evidence),
                getattr(model, "last_completion_tokens", 0) or 500,
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
        }
        # W4 (#2101): additive why-layer entries — emitted ONLY with the W4
        # flag ON (absent otherwise — the 12-field response stays
        # byte-identical).
        if w4_enrichment_enabled():
            resp["why"] = why_entries
        return resp

    @staticmethod
    def _ask_d8_decoration_unavailable(hits: list[dict]) -> bool:
        """D8-decoration-unavailable detection (P1-13/P1-5): terminal-status
        hits returned WITHOUT supersession keys when ``include_terminal=True``
        — the decoration silently failed to attach the markers, so the
        evidence would render superseded content as current. 200 +
        ``retrieval_degraded=True`` (never a silent success)."""
        from tortoise.search_engine import TERMINAL_EXCLUDED_STATUSES
        for h in hits:
            if ((h.get("status") or "") in TERMINAL_EXCLUDED_STATUSES
                    and not (h.get("superseded_by") or h.get("supersedes")
                             or h.get("valid_from") or h.get("valid_to")
                             or h.get("expired_at"))):
                return True
        return False

    # ── Per-namespace reader-model cache (#1987 Task 5) ────────────────────

    def _ask_reader_model(self, factory=None):
        """Resolve the ask-lane reader model for THIS SDK's namespace from the
        per-namespace cache (never module-global): LRU bound, in-flight
        entries never evicted, failed builds never cached, per-key build
        single-flight, closed clients on eviction."""
        namespace = getattr(self, "_namespace", None) or "default"
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

    def _post_ask(self, question: str, *, question_type: str | None = None,
                  question_date: str | None = None) -> dict:
        """Hosted-mode POST to ``TORTOISE_API_URL`` ``/v1/ask`` (Task 5) —
        mirrors ``_post_commit``'s pattern (auth header, NO auto-retry v1).
        The SDK-side timeout (75s) is STRICTLY GREATER than the server's
        ``_ASK_TIMEOUT_S`` (60s) so the server's 504 is always receivable and
        mapped to ``AskTimeout`` reliably. Maps statuses/body codes to the
        typed SDK exceptions (exceptions.py) per the pinned vocabulary.
        """
        from tortoise.schemas import (  # noqa: I001
            CODE_IN_FLIGHT_LIMIT,
            CODE_INVALID_QUESTION,
            CODE_INVALID_QUESTION_DATE,
            CODE_INVALID_QUESTION_TYPE,
            CODE_QUESTION_TOO_LONG,
            CODE_RETRIEVAL_UNAVAILABLE,
            CODE_UNAUTHORIZED,
        )
        from tortoise.exceptions import (
            AskInFlightLimit,
            AskQuotaExceeded,
            AskReaderUnavailable,
            AskRetrievalUnavailable,
            AskTimeout,
            AskValidationError,
        )
        import requests as _requests
        base = os.environ.get("TORTOISE_API_URL", "http://localhost:8000")
        key = os.environ.get("TORTOISE_API_KEY", "")
        payload = {"question": question}
        if question_type is not None:
            payload["question_type"] = question_type
        if question_date is not None:
            payload["question_date"] = question_date
        try:
            r = _requests.post(
                f"{base.rstrip('/')}/v1/ask",
                headers={"Authorization": f"Bearer {key}"},
                json=payload, timeout=ASK_SDK_TIMEOUT_S)
        except _requests.exceptions.Timeout:
            raise AskTimeout("client-side timeout awaiting /v1/ask",
                             source="client") from None
        except _requests.exceptions.ConnectionError:
            raise AskReaderUnavailable(
                "cannot reach the hosted ask server (connection refused)",
                status_code=None) from None
        body: dict = {}
        try:
            body = r.json()
        except ValueError:
            body = {}
        err = body.get("error") or {}
        code = err.get("code") if isinstance(err, dict) else None
        status = r.status_code

        def _val_err(default_code: str, message: str) -> AskValidationError:
            return AskValidationError(message, code=code or default_code,
                                      status_code=status)

        if status == 429:
            if code == CODE_IN_FLIGHT_LIMIT:
                raise AskInFlightLimit("in-flight ask limit",
                                       status_code=status)
            # 429-is-quota: a code-less 429 → AskQuotaExceeded
            # (retry_after=None — NEVER AskValidationError, P2-15).
            # The hosted server emits Retry-After in the HTTP HEADER and
            # ALSO ships the seconds in the 429 body (P1) — prefer the
            # header, fall back to the body field. Parse the header FIRST;
            # RFC 7231 allows an HTTP-date (float() raises → None) — only
            # then fall back to the body.
            retry_after = None
            header_ra = r.headers.get("Retry-After")
            if header_ra is not None:
                try:
                    retry_after = float(header_ra)
                except (TypeError, ValueError):
                    retry_after = None
            if retry_after is None:
                body_ra = err.get("retry_after") if isinstance(err, dict) else None
                if body_ra is not None:
                    try:
                        retry_after = float(body_ra)
                    except (TypeError, ValueError):
                        retry_after = None
            raise AskQuotaExceeded("ask quota exceeded",
                                   retry_after=retry_after,
                                   status_code=status)
        if status == 502:
            if code == CODE_RETRIEVAL_UNAVAILABLE:
                raise AskRetrievalUnavailable("retrieval unavailable",
                                              status_code=status)
            raise AskReaderUnavailable("reader unavailable",
                                       status_code=status)
        if status == 504:
            raise AskTimeout("server timeout", source="server",
                             status_code=status)
        if status == 402:
            # a code-less 402 is a SERVER-side provider-billing condition —
            # never mislabeled invalid_question (P2-3).
            raise AskReaderUnavailable(
                "provider billing 402 (status retained)",
                status_code=status)
        if status == 404:
            # #2013: /v1/ask is NOT registered when the hosted ask exposure is
            # gated off (TORTOISE_ENABLE_ASK unset) — a 404 is the EXPECTED
            # gated state, not an invalid question (the default code-less 4xx
            # map would mislabel it invalid_question).
            raise AskReaderUnavailable(
                "ask exposure is not enabled on this server",
                status_code=status)
        if 400 <= status < 500:
            if code in (CODE_INVALID_QUESTION, CODE_QUESTION_TOO_LONG,
                        CODE_INVALID_QUESTION_TYPE, CODE_INVALID_QUESTION_DATE,
                        CODE_UNAUTHORIZED):
                raise _val_err(code, f"ask rejected: {code}")
            # code-less 4xx → the status-derived documented default
            defaults = {400: CODE_INVALID_QUESTION, 401: CODE_UNAUTHORIZED,
                        403: CODE_UNAUTHORIZED, 422: CODE_INVALID_QUESTION}
            default_code = defaults.get(status, CODE_INVALID_QUESTION)
            raise _val_err(default_code, f"ask rejected (HTTP {status})")
        if status >= 500:
            # Residual 5xx NOT covered above (500 handler failure, 503
            # LB/deploy drain) — never escapes as an untyped
            # requests.HTTPError on the ask surface: map to the typed
            # unavailable exceptions with the status retained, mirroring
            # the 502 branch (P2).
            if code == CODE_RETRIEVAL_UNAVAILABLE:
                raise AskRetrievalUnavailable("retrieval unavailable",
                                              status_code=status)
            raise AskReaderUnavailable("reader unavailable",
                                       status_code=status)
        r.raise_for_status()
        return body


    def expand_relationships(self, point_id: str) -> list[dict]:
        """Full relationship payload for a single Point, incl. related_content (#1353 D14).

        The expand side of the list/expand split: search list view carries bounded
        state entries (IDs + labels + direction + peer state, no content); this
        returns the complete unbounded payload for one point (single-point
        fan-out is trivially cheap) so an agent can read a neighbor's full text
        on demand. Each entry gains `related_status` (the neighbor's point
        status — live/superseded/retracted/draft) so the full-fidelity view is
        not blind to terminal state (#689/#898 posture).
        """
        from .search_engine import get_relationships
        proj = self._get_proj()
        entries = get_relationships(proj.g, [point_id]).get(point_id, [])
        if not entries:
            return entries
        related_ids = [e.get("related_id") for e in entries if e.get("related_id")]
        statuses: dict[str, str] = {}
        if related_ids:
            try:
                rows = proj.g.query(
                    "MATCH (n:Point) WHERE n.id IN $ids RETURN n.id, n.status",
                    params={"ids": related_ids},
                    timeout=200,
                ).result_set
                statuses = {row[0]: row[1] or "" for row in rows}
            except Exception:
                pass
        for e in entries:
            e["related_status"] = statuses.get(e.get("related_id"), "")
        return entries

    # ── Point-in-time restore (E6 #1538, D6) ─────────────────────────

    # Chain-walk guard: at most this many CORRECTS hops before giving up
    # (bounded BFS — a pathological/cyclic chain can't loop forever).
    _RESTORE_CHAIN_GUARD = 32

    def restore_point_at(self, point_id: str, at_date: str) -> dict:
        """Point-in-time restore: what was true at ``at_date`` (E2E-9, #1538).

        V3 mechanism: walk the CORRECTS chain from the given (current/live)
        point to the point whose ``[validFrom, validTo]`` interval covers
        ``at_date``. Every supersede/invalidate written by THIS SDK stamps
        contiguous windows (D2: old.validTo == successor's validFrom), so the
        walk answers "what was true then" exactly.

        Return shapes:
          {valid_point: {id, content, valid_from, valid_to},
           current: {id, content},
           chain: [{id, valid_from, valid_to}], ...}
            — exactly one candidate covers ``at_date``.
          {ambiguous: true, candidates: [...]} — two+ candidates whose
            windows both plausibly cover (malformed/overlapping windows):
            explicit ambiguity signal, never a silent wrong answer.
          {found: false, nearest: {...}, chain: [...]} — honest absence:
            the date lies outside every window (before the earliest / after
            the latest); ``nearest`` is the closest window for context.

        Legacy undated points: no ``validFrom`` ⇒ open start; a superseded
        point without ``validTo`` ⇒ open end (covers everything before the
        successor's ``validFrom`` — no special case beyond IS NULL
        handling). Missing point / empty chain ⇒ ``{found: false}``.

        Date comparison reuses ``_created_sort_key``'s mixed-format
        tolerance (ISO strings + numeric epoch both parse).
        """
        from .search_engine import _created_sort_key

        at_date = str(at_date or "").strip()
        if not at_date:
            raise ValueError("restore_point_at: at_date must be non-empty")
        proj = self._get_proj()

        # Walk the CORRECTS chain: (new)-[:CORRECTS]->(old) — outgoing
        # CORRECTS from the current point leads to OLDER claims.
        chain: list[dict] = []
        seen: set[str] = set()
        cur = point_id
        for _ in range(self._RESTORE_CHAIN_GUARD):
            if cur in seen:
                break  # cycle guard — never loop forever
            seen.add(cur)
            rows = proj.g.query(
                "MATCH (p:Point {id:$id}) RETURN p.content, p.validFrom, "
                "p.validTo, p.createdAt",
                params={"id": cur},
            ).result_set
            if not rows:
                if cur == point_id:
                    # Asked point missing entirely.
                    return {"found": False, "point_id": point_id,
                            "at_date": at_date, "chain": []}
                break
            content, vf, vt, created = rows[0]  # noqa: RUF059
            chain.append({"id": cur, "content": content,
                          "valid_from": vf, "valid_to": vt})
            older = proj.g.query(
                "MATCH (p:Point {id:$id})-[:CORRECTS]->(old:Point) "
                "RETURN old.id ORDER BY old.createdAt LIMIT 1",
                params={"id": cur},
            ).result_set
            if not older:
                break
            cur = older[0][0]

        if not chain:
            return {"found": False, "point_id": point_id,
                    "at_date": at_date, "chain": []}

        t_key = _created_sort_key(at_date)

        def _covers(vf, vt) -> bool:
            """Window coverage with IS-NULL open ends + mixed-format keys."""
            if vf is not None and _created_sort_key(vf) > t_key:
                return False
            if vt is not None and _created_sort_key(vt) < t_key:  # noqa: SIM103
                return False
            return True

        covering = [e for e in chain if _covers(e["valid_from"], e["valid_to"])]
        out = {
            "point_id": point_id,
            "at_date": at_date,
            "current": {"id": chain[0]["id"], "content": chain[0]["content"]},
            "chain": [{"id": e["id"], "valid_from": e["valid_from"],
                        "valid_to": e["valid_to"]} for e in chain],
        }
        if len(covering) == 1:
            e = covering[0]
            out["valid_point"] = {"id": e["id"], "content": e["content"],
                                   "valid_from": e["valid_from"],
                                   "valid_to": e["valid_to"]}
            out["found"] = True
            return out
        if len(covering) > 1:
            # Overlapping/malformed windows — never a silent wrong answer.
            out["ambiguous"] = True
            out["candidates"] = [{"id": e["id"], "content": e["content"],
                                   "valid_from": e["valid_from"],
                                   "valid_to": e["valid_to"]}
                                  for e in covering]
            return out

        # Honest absence: no window covers. Report the nearest window.
        out["found"] = False
        nearest = None
        best = None
        for e in chain:
            vf, vt = e["valid_from"], e["valid_to"]
            dist = None
            if vf is not None and _created_sort_key(vf) > t_key:
                kf = _created_sort_key(vf)
                if kf[0] == 0 and t_key[0] == 0:  # both parseable
                    dist = kf[1] - t_key[1]
            elif vt is not None and _created_sort_key(vt) < t_key:
                kt = _created_sort_key(vt)
                if kt[0] == 0 and t_key[0] == 0:  # both parseable
                    dist = t_key[1] - kt[1]
            if dist is None:
                continue  # open interval / unparseable — not a candidate
            if best is None or dist < best:
                best = dist
                nearest = e
        if nearest is not None:
            out["nearest"] = {"id": nearest["id"],
                               "valid_from": nearest["valid_from"],
                               "valid_to": nearest["valid_to"]}
        return out

    # ── Recall (epic #898) — UC1 STATE ──────────────────────────────

    # Status values excluded from the UC1 "current state" view by default.
    # `retracted` is additionally hard-excluded at the retrieval layer (#689).
    STATE_EXCLUDED_STATUS = frozenset({"superseded", "deprecated", "retracted"})
    # About-edge family used for object-centric linking (mirrors
    # ranking.ABOUT_EDGE_TYPES and supersede_point's about* structural rels).
    _ABOUT_TYPES = "aboutSubject|aboutObject|aboutAction|aboutEvent|aboutPoint|aboutDocument"

    def recall_state(
        self,
        query: str | None = None,
        *,
        kind: str | None = None,
        limit: int = 10,
        include_superseded: bool = False,
        min_confidence: float = 0.0,
        relevance_exp: float = 1.0,
        confidence_exp: float = 1.0,
        centrality_weight: float = 0.10,
        object_centric: bool = True,
        state_ranker=None,
    ) -> list[dict]:
        """UC1 "state" recall (epic #898 Wave A) — what is true and
        high-confidence right now.

        Retrieves Points + Objects (hybrid search), then re-ranks the merged
        pool with the multiplicative confidence gate (StateRanker):

            base  = relevance_norm^a × confidence^b
            score = base × (1 + w_c × centrality_norm)

        State semantics:
        - Excludes status in (superseded, deprecated, retracted) by default;
          ``include_superseded=True`` brings superseded/deprecated back
          (retracted stays excluded — #689 leak guard).
        - Object-centric: Objects and the Points about them are ranked
          together; an Object's confidence is the mean EP posterior of the
          Points about it (neutral 0.5 when none).
        - Contested claims are SURFACED, never buried: ``contested:true`` +
          ``counter_evidence`` (NANDing point id/content) attached.
        - Most important arguments (operators, by annotator_precision) and
          high-contention NANDs / mitigations attached to top results.
        - Uncalibrated points fall back to documented neutral confidence 0.5
          (absence of measurement is NOT low support).

        Returns the same SearchResult shape as ``tortoise_fts_query`` (list
        of dicts), each annotated with ``entity_type``, ``recall_ranking``
        (score breakdown), and state-context keys (``contested``,
        ``counter_evidence``, ``arguments``, ``nands``, ``mitigations``,
        ``related_objects`` / ``related_points``).
        """
        from .ranking import StateRanker

        if limit < 1 or limit > 10000:
            raise ValueError(f"limit must be 1-10000, got {limit}")
        if not (0.0 <= min_confidence <= 1.0):
            raise ValueError(f"min_confidence must be 0.0-1.0, got {min_confidence}")
        if relevance_exp <= 0 or confidence_exp <= 0:
            raise ValueError(
                f"relevance_exp/confidence_exp must be > 0, got {relevance_exp}/{confidence_exp}")
        if not 0.0 <= centrality_weight <= 1.0:
            raise ValueError(f"centrality_weight must be 0-1, got {centrality_weight}")

        proj = self._get_proj()
        ranker = state_ranker or StateRanker(
            proj,
            relevance_exp=relevance_exp,
            confidence_exp=confidence_exp,
            centrality_weight=centrality_weight,
        )

        # 1. Candidate pool (Points + Objects), hybrid retrieval per entity.
        #    State filter applied INSIDE retrieval (exclude_status is enforced
        #    before pool truncation, so live claims ranked behind superseded
        #    ones are not dropped — #898 review P1). retracted is already
        #    hard-excluded at the retrieval layer (#689).
        pool = max(limit * 3, 30)
        # retracted stays hard-excluded at the retrieval layer (#689);
        # superseded/deprecated are excluded here unless include_superseded.
        exclude_status = None if include_superseded else sorted(
            self.STATE_EXCLUDED_STATUS - {"retracted"})
        point_results = self.tortoise_fts_query(
            query, kind=kind, entity_type="point", limit=pool,
            exclude_status=exclude_status,
            include_terminal=include_superseded,
            # Sec-1 (code-review gate): the pool can reach ~30k ids — W4
            # enrichment on the PRE-rerank pool would fold + project ~30k
            # candidates that StateRanker then discards ~2/3 of. Enrich
            # POST-rerank on the final top-limit list instead (see below).
            w4_enrich=False)
        object_results = (
            self.tortoise_fts_query(
                query, kind=kind, entity_type="object", limit=pool)
            if object_centric else []
        )
        # #1350: Object status filter (decision 2a — completed/in_progress
        # stay visible; superseded/deprecated/archived/retracted excluded
        # from the state view unless include_superseded brings them back).
        objects = [dict(r, entity_type="object") for r in object_results]
        if not include_superseded:
            objects = [o for o in objects
                       if (o.get("status") or "") not in
                       ("superseded", "deprecated", "archived", "retracted")]

        # UC1 state view: hide mitigation bookkeeping points (they are
        # surfaced ATTACHED to results as context, not standalone claims —
        # review round-2 P3).
        points = [dict(r, entity_type="point") for r in point_results
                  if not (r.get("content") or "").startswith("[MITIGATION]")]
        # #1391: retracted stays hard-excluded even when include_superseded
        # brings the other terminal statuses back (#689 leak guard) — the
        # base retrieval opt-in (include_terminal) surfaces all terminal
        # statuses, so recall_state re-filters retracted from its own pool.
        if include_superseded:
            points = [p for p in points if p.get("status") != "retracted"]

        # 3. Multiplicative-gate ranking over the merged pool.
        merged = points + objects
        ranked = ranker.rerank(merged, entity_type="point")

        # 4. Explicit confidence floor (orthogonal to the multiplicative gate).
        ranked = [
            r for r in ranked
            if r["recall_ranking"]["confidence"] >= min_confidence
        ][:limit]

        # 5. State context surfacing (batched, not N+1).
        point_ids = [r["id"] for r in ranked if r.get("entity_type") == "point"]
        object_ids = [r["id"] for r in ranked if r.get("entity_type") == "object"]

        counter_evidence = self._state_counter_evidence(point_ids)
        arguments, nands = self._state_arguments(point_ids)
        mitigations = self._state_mitigations(arguments, nands)
        related_objects = self._state_related_objects(point_ids)
        related_points = self._state_related_points(object_ids)

        out: list[dict] = []
        for r in ranked:
            rid = r["id"]
            copy = dict(r)
            if rid in counter_evidence:
                copy["counter_evidence"] = counter_evidence[rid]
            if rid in arguments:
                copy["arguments"] = arguments[rid]
            if rid in nands:
                copy["nands"] = nands[rid]
            if rid in mitigations:
                copy["mitigations"] = mitigations[rid]
            if rid in related_objects:
                copy["related_objects"] = related_objects[rid]
            if rid in related_points:
                copy["related_points"] = related_points[rid]
            # Contestation surfaced at top level (ep carries variance/contested
            # for points; mirror it here so state consumers can flag without
            # digging into ep). Never a ranking demoter.
            ep = copy.get("ep")
            contested = bool(ep.get("contested")) if isinstance(ep, dict) else False
            copy["contested"] = contested
            out.append(copy)
        # Sec-1 (code-review gate): W4 enrichment POST-rerank on the final
        # top-limit list (the pool call above passed w4_enrich=False) — the
        # additive keys ride only the results the caller actually receives.
        # Flag-gated + fail-open (unchanged on any assembly error).
        try:
            from .why import enrich_items as _w4_enrich_items
            from .why import w4_enrichment_enabled as _w4_enabled
            if _w4_enabled():
                point_items = [i for i in out if i.get("entity_type") == "point"]
                enriched = _w4_enrich_items(proj, point_items)
                by_id = {i["id"]: i for i in enriched}
                out = [by_id.get(i["id"], i) for i in out]
        except Exception as e:  # noqa: BLE001, RUF100 — fail-open
            _logger.warning("W4 enrichment failed (recall_state): %s", e)
        return out

    def _state_counter_evidence(self, point_ids: list[str]) -> dict[str, list[dict]]:
        """NANDing points (id/content) for contested targets.

        Includes both statement-point NANDers and NAND operators (label used
        as content) — a NAND operator IS the counter-claim in the Tortoise
        model. Contestation is surfaced, never a ranking demoter.
        """
        if not point_ids:
            return {}
        rows = self._get_proj().g.query(
            "MATCH (c:Point)-[r:NAND]->(n:Point) "
            "WHERE n.id IN $ids "
            "RETURN n.id, c.id, coalesce(c.label, c.content, ''), "
            "  coalesce(c.is_operator, false), coalesce(c.op_type, '')",
            params={"ids": point_ids},
        ).result_set
        out: dict[str, list[dict]] = {}
        for target_id, cid, text, is_op, op_type in rows:
            if is_op and not text:
                # Unlabeled NAND operators: give the counter-evidence a
                # meaningful default so the content half is never empty.
                text = f"[NAND operator{(' ' + op_type) if op_type else ''}]"
            out.setdefault(target_id, []).append(
                {"id": cid, "content": text or "", "is_operator": bool(is_op)})
        return out

    def _state_arguments(self, point_ids: list[str]) -> tuple[dict[str, list[dict]], dict[str, list[dict]]]:
        """Operators (arguments) attached to top points + NAND operators.

        Returns (arguments, nands): arguments = operators sorted by
        annotator_precision desc (most important first, capped 3 per result);
        nands = NAND-edge operators (contradictions) with target ids.
        """
        if not point_ids:
            return {}, {}
        rows = self._get_proj().g.query(
            "MATCH (op:Point {is_operator:true})-[r:IMPL|NAND|hasPart]-(n:Point) "
            "WHERE n.id IN $ids "
            "RETURN n.id, op.id, op.label, op.op_type, "
            "  coalesce(op.annotator_precision, op.precision, 0.5), type(r)",
            params={"ids": point_ids},
        ).result_set
        args: dict[str, list[dict]] = {}
        nands: dict[str, list[dict]] = {}
        for nid, op_id, label, op_type, precision, rel in rows:
            op = {"id": op_id, "label": label or "", "op_type": op_type or "",
                  "precision": round(float(precision), 4), "mechanism": rel}
            if rel == "NAND":
                nands.setdefault(nid, []).append(op)
            else:
                args.setdefault(nid, []).append(op)
        for nid in args:
            args[nid].sort(key=lambda o: o["precision"], reverse=True)
            args[nid] = args[nid][:3]
        for nid in nands:
            nands[nid].sort(key=lambda o: o["precision"], reverse=True)
            nands[nid] = nands[nid][:5]  # bounded high-contention list
        return args, nands

    def _state_mitigations(self, arguments: dict[str, list[dict]],
                           nands: dict[str, list[dict]] | None = None) -> dict[str, list[dict]]:
        """Mitigation points attached to surfaced operators (top points).

        Includes operators surfaced as arguments AND as high-contention NANDs
        — a mitigation on the very NAND that contradicts a surfaced claim is
        exactly the epistemic context the state view should show.
        """
        op_ids = sorted({op["id"] for ops in arguments.values() for op in ops}
                        | {op["id"] for ops in (nands or {}).values() for op in ops})
        if not op_ids:
            return {}
        rows = self._get_proj().g.query(
            "MATCH (op:Point {is_operator:true})-[r:mitigated_by]->(m:Point) "
            "WHERE op.id IN $ids RETURN op.id, m.id, m.content, m.mitigation_strength",
            params={"ids": op_ids},
        ).result_set
        out: dict[str, list[dict]] = {}
        for op_id, mid, content, strength in rows:
            out.setdefault(op_id, []).append(
                {"id": mid, "content": content or "",
                 "strength": float(strength) if strength is not None else None})
        # Attach under the owning result point (via its surfaced operator).
        # Dedup: an operator can appear in BOTH arguments and nands for the
        # same target (mixed IMPL+NAND edges) — mitigations must not double.
        by_point: dict[str, list[dict]] = {}
        seen: set[tuple[str, str]] = set()
        for nid, ops in list(arguments.items()) + list((nands or {}).items()):
            for op in ops:
                if op["id"] not in out:
                    continue
                for m in out[op["id"]]:
                    if (nid, m["id"]) in seen:
                        continue
                    seen.add((nid, m["id"]))
                    by_point.setdefault(nid, []).append(
                        dict(m, operator_id=op["id"]))
        return by_point

    def _state_related_objects(self, point_ids: list[str]) -> dict[str, list[dict]]:
        """Objects/entities a point is about (about* edge targets)."""
        if not point_ids:
            return {}
        rows = self._get_proj().g.query(
            "MATCH (n:Point)-[a:" + self._ABOUT_TYPES + "]->(t) "
            "WHERE n.id IN $ids "
            "RETURN n.id, labels(t)[0], t.id, coalesce(t.name, t.title, t.content, '')",
            params={"ids": point_ids},
        ).result_set
        out: dict[str, list[dict]] = {}
        for pid, tlabel, tid, display in rows:
            out.setdefault(pid, []).append(
                {"id": tid, "entity_type": (tlabel or "").lower(),
                 "content": display or ""})
        return out

    def _state_related_points(self, object_ids: list[str]) -> dict[str, list[dict]]:
        """Points about an Object (about* edges from Points to the Object)."""
        if not object_ids:
            return {}
        rows = self._get_proj().g.query(
            "MATCH (p:Point)-[a:" + self._ABOUT_TYPES + "]->(o:Object) "
            "WHERE o.id IN $ids RETURN o.id, p.id, p.content, p.pointKind",
            params={"ids": object_ids},
        ).result_set
        out: dict[str, list[dict]] = {}
        for oid, pid, content, pkind in rows:
            out.setdefault(oid, []).append(
                {"id": pid, "content": content or "", "point_kind": pkind or ""})
        return out

    def recall_gaps(
        self,
        query: str | None = None,
        *,
        kind: str | None = None,
        limit: int = 20,
        min_load: int = 1,
        max_support: int = 2,
        include_superseded: bool = False,
        gaps_ranker=None,
    ) -> list[dict]:
        """UC2 "gaps" recall (epic #898 Wave B) — load-bearing claims that
        are themselves under-supported.

        Finds the weak links of a reasoning cycle: claims the graph leans on
        (they provide confidence to others via IMPL, or actively attack via a
        strong NAND) but that are poorly sourced/supported themselves (few
        incoming IMPL, no Source). This is a graph-STRUCTURE query (epistemic
        load vs epistemic support), NOT semantic similarity:

            load    = outgoing IMPL + outgoing NAND edge count
            support = incoming IMPL + extractedFrom→Source edge count
            score   = load / (1 + support)      # "load high AND support low"

        Reads IMPL/NAND edges whether operator-mediated or DIRECT (reification
        rule, ontology v3.5 §8) — see GapsRanker docstring for the edge
        semantics. Incoming NAND is surfaced as contention, never support.

        Args:
            query: optional topic scope (hybrid retrieval). One of query or
                kind must be provided — the population scan (kind) or the
                topic scope (query) defines the candidate pool.
            kind: pointKind to scan (full-scan mode, complete population).
            limit: max results (1-10000).
            min_load: only claims with load >= min_load are gaps (default 1
                — an isolated claim nothing leans on is NOT a gap).
            max_support: only claims with support <= max_support are gaps
                (default 2 — "few incoming IMPL, no Source" boundary).
            include_superseded: bring superseded/deprecated back (retracted
                stays hard-excluded, #689).
            gaps_ranker: injectable GapsRanker (tests / tuning).

        Returns the SearchResult shape (list of dicts), each annotated with
        ``entity_type`` and ``gaps_ranking`` (score breakdown).
        """
        from .ranking import GapsRanker

        if query is None and kind is None:
            raise ValueError(
                "recall_gaps needs a topic query (topic scope) or a kind "
                "(population scan) to define the candidate pool")
        if limit < 1 or limit > 10000:
            raise ValueError(f"limit must be 1-10000, got {limit}")
        if min_load < 0:
            raise ValueError(f"min_load must be >= 0, got {min_load}")
        if max_support < 0:
            raise ValueError(f"max_support must be >= 0, got {max_support}")

        proj = self._get_proj()
        exclude_status = None if include_superseded else sorted(
            self.STATE_EXCLUDED_STATUS - {"retracted"})
        excluded_set = set(exclude_status or [])

        if query:
            # Topic scope: hybrid retrieval pool (operators can surface via
            # point retrieval — batch-filter them out below). Capped at the
            # retrieval layer's 10000 limit (P2: a valid large limit must
            # not blow the internal pool past it).
            pool = min(max(limit * 3, 50), 10000)
            results = self.tortoise_fts_query(
                query, kind=kind, entity_type="point", limit=pool,
                exclude_status=exclude_status,
                include_terminal=include_superseded)
            pool_ids = [r["id"] for r in results if r.get("id")]
            op_ids = {
                row[0] for row in proj.g.query(
                    "MATCH (n:Point {is_operator:true}) WHERE n.id IN $ids "
                    "RETURN n.id", params={"ids": pool_ids}).result_set
            } if pool_ids else set()
            pool_results = [dict(r, entity_type="point") for r in results
                            if r["id"] not in op_ids]
            # #1391: retracted stays hard-excluded even when include_superseded
            # brings the other terminal statuses back (#689 leak guard).
            if include_superseded:
                pool_results = [p for p in pool_results
                                if p.get("status") != "retracted"]
        else:
            # Population scan: raw claim nodes (self.query already excludes
            # operators + terminal statuses; include_retracted surfaces
            # superseded/deprecated for the opt-in — retracted is then
            # re-filtered below by excluded_set).
            nodes = self.query(kind=kind,
                               include_retracted=include_superseded)
            pool_results = []
            for n in nodes:
                if (n.get("status") or "") in excluded_set:
                    continue
                if (n.get("status") or "") == "retracted":
                    # #689 leak guard: retracted is never a gap candidate,
                    # even when include_superseded surfaces the others.
                    continue
                pool_results.append({
                    "id": n["id"],
                    "content": n.get("content") or "",
                    "point_kind": n.get("pointKind") or (kind or ""),
                    "status": n.get("status") or "",
                    "entity_type": "point",
                })

        # Mitigation bookkeeping points are surfaced attached to results, not
        # as standalone claims — same convention as recall_state.
        claims = [r for r in pool_results
                  if not (r.get("content") or "").startswith("[MITIGATION]")]

        ranker = gaps_ranker or GapsRanker(proj)
        ranked = ranker.rerank(claims)
        ranked = [
            r for r in ranked
            if r["gaps_ranking"]["load"] >= min_load
            and r["gaps_ranking"]["support"] <= max_support
        ][:limit]
        return ranked

    def recall_subgraph(self, seed: str, *,
                        depth: int = 2,
                        completeness: str = "full",
                        max_nodes: int = 500) -> dict:
        """UC3 "subgraph" recall (epic #898 Wave B) — the COMPLETE connected
        subgraph for a seed/topic, completeness-optimized (high recall,
        precision secondary). Used before connecting a new document to the
        graph: deep understanding first.

        Args:
            seed: a node id (Point/Object/Subject/Event/Document id, Source
                url) OR a topic text (resolved via hybrid retrieval).
            depth: BFS expansion depth, 1-5 (default 2).
            completeness: "full" (default — every relationship type is an
                edge: about*, extractedFrom, hasPart, mitigated_by, ...) or
                "core" (epistemic core only: IMPL|NAND).
            max_nodes: node-count cap (10-5000, default 500) — bounded, not
                exhaustive-until-crash.

        Returns ``{nodes, edges, stats}``:
            nodes: [{id, type, content, kind, is_operator?, status?,
                confidence?}] — type is the lowercased graph label
                (point/object/subject/event/source/document).
            edges: [{source, type, target}] — every edge with BOTH endpoints
                in the node set (the subgraph is closed over its edges).
            stats: {node_count, edge_count, depth, seed_count, truncated}.
        """
        from .ranking import SubgraphExpander

        # Validate bounds BEFORE seed resolution so an unresolvable seed
        # cannot silently skip validation (P2: depth=6 on a bogus seed must
        # error consistently, not depend on retrieval luck).
        if depth < 1 or depth > 5:
            raise ValueError(f"depth must be 1-5, got {depth}")
        if completeness not in ("core", "full"):
            raise ValueError(
                f"completeness must be 'core' or 'full', got {completeness!r}")
        if max_nodes < 10 or max_nodes > 5000:
            raise ValueError(f"max_nodes must be 10-5000, got {max_nodes}")

        seeds = self._resolve_subgraph_seed(seed)
        if not seeds:
            return {
                "nodes": [], "edges": [],
                "stats": {"node_count": 0, "edge_count": 0,
                           "depth": 0, "seed_count": 0,
                           "truncated": False},
            }
        expander = SubgraphExpander(self._get_proj())
        return expander.expand(seeds, depth=depth, completeness=completeness,
                               max_nodes=max_nodes)

    def _resolve_subgraph_seed(self, seed: str) -> list[str]:
        """Resolve a subgraph seed: exact node id/url match first, then topic
        text via hybrid retrieval (top 5 points). Returns a list of node ids
        (empty when nothing resolves)."""
        if not seed or not seed.strip():
            raise ValueError("recall_subgraph requires a seed "
                             "(node id or topic text)")
        proj = self._get_proj()
        rows = proj.g.query(
            "MATCH (n) WHERE (n.id = $seed OR n.url = $seed) "
            "AND labels(n)[0] IN ['Point', 'Object', 'Subject', 'Event', "
            "                      'Source', 'Document'] "
            "RETURN n.id",
            params={"seed": seed.strip()},
        ).result_set
        if rows:
            return [row[0] for row in rows]
        # Topic text → hybrid retrieval (points about the topic are the seeds;
        # the expansion pulls in the entities they touch).
        return [r["id"] for r in self.tortoise_fts_query(
            seed.strip(), entity_type="point", limit=5)]

    # ── Multi-tenancy (#7001) ─────────────────────────────────

    # ── Control Plane: Team CRUD ───────────────────────────────────

    def team_create(self, name: str, *, idempotency_key: str | None = None,
                    mint_key: bool = True, owner_user_id: str | None = None) -> dict:
        """Create a team with its own graph namespace.

        Writes to the control_plane registry graph. Creates a tenant
        graph (team_{name}) for Point/Operator storage.

        Returns {name, graph_name, api_key, id} on first creation; on an
        idempotent re-call (same idempotency_key) returns
        {name, graph_name, id, existing: True} with NO api_key — the caller
        already holds the plaintext from the original creation (#1710).
        mint_key=False (#1716, onboarding sub-team parity) provisions a
        KEYLESS team: no tt_ mint and no api_key hash on the Team node — the
        return dict omits api_key entirely. The team stays keyless until a
        session-key mint (apikey_create / POST /v1/session/key). A minted
        key whose plaintext is never returned is an unrecoverable dead
        credential.

        owner_user_id (#1748, onboarding sub-team parity): when set, the
        user becomes an OWNER member (Membership role=owner/status=active,
        the registry twin of provision_team's membership upsert) so the
        keyless team is reachable by session-key mint / team list / owner
        delete. Without it a keyless team has NO membership — an unmintable,
        undeletable orphan. Default None = no membership (back-compat for
        CLI/MCP/embedded callers with no user context).

        #765 (plan Task 8 — SDK control-plane backend env-gated): the SDK
        control-plane backend stays REGISTRY-BACKED — the
        TORTOISE_CONTROL_PLANE env gate lives at the hosted layer
        (hosted_api.py), and the hosted create-team writers (POST /v1/teams,
        /v1/agent/signup, /v1/register, onboarding sub-team) route their
        Supabase writes through the atomic provision_team RPC instead of
        this method. Selfhost + embedded (where this SDK runs) have no
        Supabase control plane — the registry IS the control plane there.
        """
        import re, uuid  # noqa: E401, I001
        from datetime import datetime, timezone
        from tortoise.auth import hash_api_key
        from .exceptions import ControlPlaneError

        # Input validation
        if not name or not name.strip():
            raise ControlPlaneError("Team name must not be empty")
        if len(name) > 64:
            raise ControlPlaneError("Team name must be 64 characters or fewer")
        if not re.match(r'^[a-zA-Z0-9][a-zA-Z0-9_-]*$', name):
            raise ControlPlaneError(
                f"Invalid team name: {name!r}. Use alphanumeric, hyphens, underscores."
            )

        graph_name = f"team_{name}"
        proj = self._get_proj()
        reg = self._get_registry()
        now = datetime.now(timezone.utc).isoformat()  # noqa: UP017

        # Idempotency — check registry graph for existing team
        if idempotency_key:
            existing = reg.query(
                "MATCH (t:Team {idempotency_key:$ik}) RETURN t.id, t.name",
                params={"ik": idempotency_key},
            ).result_set
            if existing:
                row = existing[0]
                # #1710: return NO api_key here — a key minted at the top of
                # the function is never hashed/persisted on this branch, so
                # returning it handed callers a dead key that fails auth on
                # first use. The plaintext belongs to the original creation.
                return {"name": name, "graph_name": graph_name,
                        "id": row[0], "existing": True}

        # Mint the key only on the CREATE path (after the idempotency check)
        # so the existing branch never mints or persists anything (#1710).
        # #1716: mint_key=False provisions a KEYLESS team — no tt_ mint and
        # no api_key hash on the Team node (the onboarding sub-team path: a
        # minted key whose plaintext is never returned is an unrecoverable
        # dead credential). The team stays keyless until a session-key mint.
        api_key = key_hash = None
        if mint_key:
            api_key = f"tt_{uuid.uuid4().hex}"
            key_hash = hash_api_key(api_key)

        # Duplicate name check
        dup = reg.query(
            "MATCH (t:Team {name:$name}) RETURN count(t) > 0",
            params={"name": name},
        ).result_set[0][0]
        if dup:
            raise ControlPlaneError(f"Team {name!r} already exists")

        tid = ulid()
        # Tier-driven limits from product/pricing.json (decision 1d) — no max_teams
        # field: multi-team is a user-level capability, NOT a tier limit.
        from tortoise.pricing import tier_limits
        lim = tier_limits("free")  # provision defaults to Free; upgrades = billing epic
        # #1716 keyless: the api_key property is omitted from the Team node
        # (a NULL property is a delete in redisgraph semantics; omitting the
        # attribute + param is the unambiguous keyless shape).
        key_attr = "api_key:$key, " if mint_key else ""
        team_params = {"id": tid, "name": name, "gn": graph_name,
                       "now": now,
                       "max_graphs": lim["max_graphs_per_team"],
                       "max_users": lim["max_users_per_team"],
                       "max_keys": lim["max_api_keys"],
                       "ops": lim["included_write_ops_per_month"],
                       "nodes": lim["max_graph_nodes"]}
        if mint_key:
            team_params["key"] = key_hash
        reg.query(
            "CREATE (t:Team {id:$id, name:$name, " + key_attr
            + "graph_name:$gn, createdAt:$now, tier:'free', "
            + "max_graphs:$max_graphs, max_users:$max_users, "
            + "max_api_keys:$max_keys, ops_allowance:$ops, "
            + "graph_size_cap:$nodes})",
            params=team_params,
        )
        if idempotency_key:
            reg.query(
                "MATCH (t:Team {id:$id}) SET t.idempotency_key = $ik",
                params={"id": tid, "ik": idempotency_key},
            )
        try:
            team_graph = proj.db.select_graph(graph_name)
            # #2001 (W5): eager OnboardingState init in the same statement as
            # TeamMeta (graph-side atomicity). compact = creator's prior
            # memberships > 0 (registry Membership nodes); fork inherited from
            # the creator's EARLIEST prior team's OnboardingState.fork with
            # 'self' fallback (never re-asks the fork card); None when the
            # creator has no prior orgs (fork card asked exactly once) or no
            # user context (CLI/embedded mint).
            from tortoise.onboarding import state as _os
            init_fork: str | None = None
            init_compact = False
            prior_ids: list[str] = []
            if owner_user_id:
                rows = reg.query(
                    "MATCH (m:Membership {user_id:$uid, status:'active'}) "
                    "WHERE m.team_id <> $org "
                    "RETURN m.team_id, m.created_at ORDER BY m.created_at",
                    params={"uid": owner_user_id, "org": tid},
                ).result_set
                prior_ids = [r[0] for r in rows]
                prior_fork = None
                if prior_ids:
                    try:
                        # registry graphs are team_{name} — resolve the
                        # earliest prior team's graph before reading its fork.
                        _prior = reg.query(
                            "MATCH (t:Team {id:$id}) RETURN t.graph_name",
                            params={"id": prior_ids[0]},
                        ).result_set
                        if _prior and _prior[0][0]:
                            prior_fork = _os.read_prior_org_fork(
                                proj.db.select_graph(_prior[0][0]),
                                prior_ids[0])
                    except Exception:
                        prior_fork = None
                init_fork, init_compact = _os.resolve_init_fork_compact(
                    bool(prior_ids), prior_fork)
            _init_q, _init_p = _os.eager_init_query(
                "CREATE (:TeamMeta {name:$name, created:$now})",
                {"name": name, "now": now},
                org_id=tid, fork=init_fork, compact=init_compact)
            team_graph.query(_init_q, params=_init_p)
            # #1686: journal the minted team_{name} graph IMMEDIATELY after
            # the TeamMeta CREATE succeeds (and before _graph_create, whose
            # failure rolls back only the registry Team node — the graph is
            # already minted; journaling before it captures the orphan). The
            # session-end sweep drops journaled names, so team_* graphs no
            # longer accumulate on the docker. No-op outside test sessions
            # (journal env absent).
            from tortoise.projection import _journal_append_product
            _journal_append_product(graph_name)
            # Graph node (team→graph 1:N, product ontology): the default graph
            self._graph_create(tid, "default", kind="default", namespace=graph_name)
            # #1748: the owner Membership for the session user — INSIDE the
            # rollback-protected try so a membership failure tears the Team
            # node down (a keyless team with no membership is an unmintable,
            # undeletable orphan). Mirrors the Supabase provision_team
            # membership upsert (role=owner, status=active, user_id=session
            # user) and membership_create (BELONGS_TO edge).
            if owner_user_id:
                self.membership_create(tid, owner_user_id, "owner")
        except Exception:
            try:  # noqa: SIM105
                reg.query("MATCH (t:Team {id:$id}) DETACH DELETE t",
                          params={"id": tid})
            except Exception:
                pass
            raise

        self._audit(tid, None, "team_create", resource_type="team", resource_id=tid)
        result = {"name": name, "graph_name": graph_name, "id": tid}
        if mint_key:
            result["api_key"] = api_key  # plaintext delivered exactly once
        return result

    def _graph_create(self, team_id: str, name: str, *, kind: str = "custom",
                      namespace: str | None = None) -> dict:
        """Create a Graph node in the registry (team→graph 1:N).

        The tenant namespace for a custom graph is team_{team_id}_{graph_id};
        custom namespaces are NOT minted until a consumer exists (E2E-11
        decision — v1 writes resolve the default graph only). The default
        graph's namespace is the team namespace itself (back-compat).

        #765 (plan Task 8 — SDK control-plane backend env-gated): in
        Supabase control-plane mode (TORTOISE_CONTROL_PLANE=supabase / creds
        configured) NO registry write happens. Since C1 (#2110) the hosted
        SOR is the ``graphs`` table (20260901000001) — but the INSERT path
        into it is C2/C3 (provisioning service, out of C1 scope), so this
        method still returns the deterministic id WITHOUT persisting; the
        registry-shaped list seam (graph_metadata/graph_list) derives the
        default graph from teams.graph_name and reads custom rows once they
        exist. Selfhost (registry mode) keeps the registry Graph node. The
        zero-registry-writes cutover contract (registry node count == 0)
        requires this gate.
        """
        import uuid as _uuid  # noqa: I001
        import hashlib as _hashlib
        from datetime import datetime, timezone as _tz
        from tortoise.supabase_control import is_supabase_enabled
        if is_supabase_enabled():
            # Deterministic per-(team, name) id — stable across calls so a
            # re-created graph maps to the same display key; namespace shape
            # matches the registry mode (team_{team_id}_{gid}).
            gid = f"g_{_hashlib.sha256(f'{team_id}:{name}'.encode()).hexdigest()[:16]}"
            ns = namespace or f"team_{team_id}_{gid}"
            return {"graph_id": gid, "name": name, "kind": kind,
                    "namespace": ns}
        reg = self._get_registry()
        gid = f"g_{_uuid.uuid4().hex[:16]}"
        ns = namespace or f"team_{team_id}_{gid}"
        now = datetime.now(_tz.utc).isoformat()  # noqa: UP017
        # C1 (#2110): Graph node gains status (v1 lifecycle: active only —
        # delete = soft tombstone; no archive). recording stays absent =
        # NULL = inherit team default (back-compat with pre-C1 nodes).
        reg.query(
            "CREATE (g:Graph {id:$gid, team_id:$tid, name:$name, kind:$kind, "
            "namespace:$ns, status:'active', created_at:$now})",
            params={"gid": gid, "tid": team_id, "name": name,
                    "kind": kind, "ns": ns, "now": now},
        )
        return {"graph_id": gid, "name": name, "kind": kind, "namespace": ns}

    def graph_list(self, team_id: str) -> list[dict]:
        """List Graph nodes for a team (default graph first).

        #765 (plan Task 8 reader inventory): in Supabase control-plane mode
        the default graph is derived from ``teams.graph_name`` via the seam
        (graph_metadata — C1 now also lists custom graphs table rows); the
        registry Graph-node read stays for selfhost. Registry-shaped rows
        (graph_id/team_id/name/kind/namespace/status) so callers are
        mode-agnostic.
        """
        from tortoise.supabase_control import (  # noqa: I001
            get_control_plane, graph_metadata, is_supabase_enabled,
        )
        if is_supabase_enabled():
            return graph_metadata(get_control_plane(), team_id)
        reg = self._get_registry()
        rows = reg.query(
            "MATCH (g:Graph {team_id:$tid}) RETURN properties(g) "
            "ORDER BY CASE g.kind WHEN 'default' THEN 0 ELSE 1 END, g.created_at",
            params={"tid": team_id},
        ).result_set
        out = []
        for (props,) in rows:
            out.append({
                "graph_id": props.get("id"),
                "team_id": props.get("team_id"),
                "name": props.get("name"),
                "kind": props.get("kind", "custom"),
                "namespace": props.get("namespace"),
                # C1 (#2110): status/recording ride the seam. Pre-C1 nodes
                # lack them → status coalesces to "active" (mode-agnostic
                # with the Supabase seam, which always emits "active"; deep
                # review P2: a consumer filtering status=='active' must not
                # silently drop legacy selfhost graphs). recording None =
                # inherit team default.
                "status": props.get("status") or "active",
                "recording": props.get("recording"),
            })
        return out

    def graph_count(self, team_id: str) -> int:
        """Graph count for a team — the quota meter's source (C1 #2110).

        Supabase mode: 1 (the default graph — always present, derived from
        teams.graph_name) + count(custom active). Deleted rows excluded
        (delete frees the slot; v1 has no archive). Registry mode: the
        existing MATCH (team_create :12055 creates the kind='default' node,
        so the registry count already includes the default). C2 (#2111):
        registry branch now filters status <> 'deleted' (soft-delete must
        free the slot — E2E-8; pre-C1 nodes without the prop count as
        active) — parity with the Supabase branch (plan-review P1 #3).
        """
        from tortoise.supabase_control import (  # noqa: I001
            get_control_plane, is_supabase_enabled,
        )
        if is_supabase_enabled():
            cp = get_control_plane()
            try:
                rows = cp.query(
                    "graphs", select=["id"],
                    filters=[("team_id", "eq", team_id),
                             ("kind", "eq", "custom"),
                             ("status", "eq", "active")],
                )
            except Exception as e:
                # Drift-safe: pre-C1 schema (no graphs table) → default-only.
                _logger.warning(
                    "graphs count read failed — counting default only "
                    "(migration 20260901000001 applied?): %s", e)
                return 1
            return 1 + len(rows)
        reg = self._get_registry()
        return reg.query(
            "MATCH (g:Graph {team_id:$tid}) "
            "WHERE g.status IS NULL OR g.status <> 'deleted' "
            "RETURN count(g)",
            params={"tid": team_id},
        ).result_set[0][0]

    def graph_delete(self, team_id: str, graph_id: str) -> bool:
        """Soft-delete a Graph node (status='deleted' tombstone — the v1
        lifecycle, C2 #2111). Returns True when a non-default node was
        tombstoned; False when unknown OR the default (callers map to
        404/403). Pre-C1 nodes without status gain it on delete."""
        reg = self._get_registry()
        rows = reg.query(
            "MATCH (g:Graph {id:$gid, team_id:$tid}) RETURN g.kind",
            params={"gid": graph_id, "tid": team_id},
        ).result_set
        if not rows:
            return False
        if rows[0][0] == "default":
            return False
        reg.query(
            "MATCH (g:Graph {id:$gid, team_id:$tid}) "
            "SET g.status = 'deleted'",
            params={"gid": graph_id, "tid": team_id},
        )
        return True

    def graph_key_ids(self, team_id: str, graph_id: str) -> list[str]:
        """APIKey node ids bound to a graph — the delete-cascade source
        (every key dies with the graph, E2E-8). Revoked or not — the
        cascade must revoke rows that are somehow still active AND clean
        up revoked ones (idempotent)."""
        reg = self._get_registry()
        rows = reg.query(
            "MATCH (k:APIKey {team_id:$tid, graph_id:$gid}) RETURN k.id",
            params={"tid": team_id, "gid": graph_id},
        ).result_set
        return [r[0] for r in rows]

    def graph_active_key_count(self, team_id: str, graph_id: str) -> int:
        """ACTIVE (non-revoked) APIKey nodes bound to a graph — the
        key_count source for GET /v1/graphs (parity with the Supabase
        count_graph_keys seam; C2 P2: graph_key_ids is the cascade source
        and must NOT be reused for the meter — after C3's standalone
        revoke, counting all keys would overcount vs Supabase)."""
        reg = self._get_registry()
        rows = reg.query(
            "MATCH (k:APIKey {team_id:$tid, graph_id:$gid}) "
            "WHERE k.revoked_at IS NULL RETURN count(k)",
            params={"tid": team_id, "gid": graph_id},
        ).result_set
        return int(rows[0][0]) if rows else 0

    def team_get(self, team_id: str) -> dict | None:
        """Get a team by ID. Returns None if not found."""
        reg = self._get_registry()
        rows = reg.query(
            "MATCH (t:Team {id:$id}) RETURN properties(t)",
            params={"id": team_id},
        ).result_set
        return rows[0][0] if rows else None

    def team_list(self) -> list[dict]:
        """List all teams."""
        reg = self._get_registry()
        rows = reg.query(
            "MATCH (t:Team) RETURN properties(t) ORDER BY t.createdAt"
        ).result_set
        return [r[0] for r in rows]

    def team_update(self, team_id: str, **fields) -> dict:
        """Update mutable team fields."""
        from .exceptions import ControlPlaneError
        allowed = {
            "name", "tier", "stripe_customer_id", "subscription_id",
            "backup_enabled", "max_users", "max_graphs",
            # #329 relief path: quota limits settable via the control plane so
            # a team at cap can be upgraded (no REST surface exists yet — the
            # fields are SDK/registry-level; get_current_team honors them).
            "max_points", "max_api_keys", "max_sessions",
        }
        invalid = set(fields.keys()) - allowed
        if invalid:
            raise ControlPlaneError(f"Invalid team fields: {invalid}")
        reg = self._get_registry()
        reg.query(
            "MATCH (t:Team {id:$id}) SET t += $fields",
            params={"id": team_id, "fields": fields},
        )
        self._audit(team_id, None, "team_update", resource_type="team",
                     resource_id=team_id)
        return self.team_get(team_id) or {}

    def team_delete(self, team_id: str, *, confirmation: str) -> dict:
        """Delete a team and all associated control-plane entities.

        Cascading: Membership, APIKey, Invitation nodes are deleted.
        Tenant graphs are dropped (best-effort — FalkorDBLite may skip).
        Postgres audit_events are preserved (immutable).

        Requires confirmation matching the team name.
        """
        from .exceptions import ControlPlaneError
        team = self.team_get(team_id)
        if team is None:
            raise ControlPlaneError(f"Team {team_id!r} not found")
        if confirmation != team.get("name", ""):
            raise ControlPlaneError(
                "Confirmation must match team name exactly"
            )

        reg = self._get_registry()
        # Cascade delete: Membership, APIKey, Invitation
        reg.query(
            "MATCH (m:Membership {team_id:$tid}) DETACH DELETE m",
            params={"tid": team_id},
        )
        reg.query(
            "MATCH (k:APIKey {team_id:$tid}) DETACH DELETE k",
            params={"tid": team_id},
        )
        reg.query(
            "MATCH (i:Invitation {team_id:$tid}) DETACH DELETE i",
            params={"tid": team_id},
        )
        reg.query(
            "MATCH (t:Team {id:$id}) DETACH DELETE t",
            params={"id": team_id},
        )

        # Best-effort tenant graph deletion
        graph_name = team.get("graph_name", f"team_{team.get('name', '')}")
        proj = self._get_proj()
        try:
            # #2163: proj.db (falkordb.FalkorDB on every lane) has NO
            # delete_graph attr on the pip client — the old hasattr probe
            # skipped on FalkorDB Cloud and orphaned the graph after the
            # registry rows were deleted. select_graph(...).delete() issues
            # GRAPH.DELETE on embedded + server/cloud alike. An absent-graph
            # raise (GRAPH.DELETE on a dropped/never-minted graph) is
            # treated as success — the graph being gone is the desired end
            # state; genuine failures still log and fall through to the
            # best-effort swallow.
            proj.db.select_graph(graph_name).delete()
        except Exception as e:
            if is_missing_graph_error(e):
                _logger.debug("tenant graph %s already absent — skipping",
                              graph_name)
            else:
                _logger.debug("Failed to delete tenant graph %s — skipping",
                              graph_name)

        self._audit(team_id, None, "team_delete", resource_type="team",
                     resource_id=team_id)
        return {"deleted": True, "team_id": team_id}

    def migrate_teams_to_registry(self) -> dict:
        """One-shot: move Team nodes from tortoise graph to control_plane graph.

        Idempotent — running twice produces the same state.
        Existing Team nodes in the tortoise graph are marked as outdated.
        """
        proj = self._get_proj()
        reg = self._get_registry()
        teams = proj.g.query("MATCH (t:Team) RETURN properties(t)").result_set
        migrated, skipped = 0, 0
        for row in teams:
            team = row[0]
            name = team.get("name", "")
            # Check if already in registry
            existing = reg.query(
                "MATCH (t:Team {name:$name}) RETURN count(t) > 0",
                params={"name": name},
            ).result_set[0][0]
            if existing:
                skipped += 1
                continue
            reg.query(
                "CREATE (t:Team {id:$id, name:$name, api_key:$key, "
                "graph_name:$gn, createdAt:$now})",
                params={
                    "id": team.get("id", ulid()),
                    "name": name,
                    "key": team.get("api_key", ""),
                    "gn": team.get("graph_name", f"team_{name}"),
                    "now": team.get("createdAt", ""),
                },
            )
            migrated += 1
        if migrated > 0:
            proj.g.query("MATCH (t:Team) SET t.status = 'outdated'")
        return {"migrated": migrated, "skipped": skipped}

    # ── Control Plane: Membership CRUD ─────────────────────────────

    def membership_create(self, team_id: str, user_id: str, role: str) -> dict:
        """Add a user to a team with a given role.

        Validates role, team existence, and max_users constraint.
        Creates BELONGS_TO edge to Team.
        """
        from datetime import datetime, timezone  # noqa: I001
        from .exceptions import ControlPlaneError

        if role not in ("owner", "admin", "member"):
            raise ControlPlaneError(
                f"Invalid role {role!r}. Must be 'owner', 'admin', or 'member'."
            )

        team = self.team_get(team_id)
        if team is None:
            raise ControlPlaneError(f"Team {team_id!r} not found")

        # Check max_users constraint
        max_users = team.get("max_users")
        if max_users is not None:
            reg = self._get_registry()
            count = reg.query(
                "MATCH (m:Membership {team_id:$tid}) "
                "WHERE m.status = 'active' RETURN count(m)",
                params={"tid": team_id},
            ).result_set[0][0]
            if count >= max_users:
                raise ControlPlaneError(
                    f"Team at max users ({max_users}). Upgrade to add more."
                )

        mid = ulid()
        now = datetime.now(timezone.utc).isoformat()  # noqa: UP017
        reg = self._get_registry()
        reg.query(
            "CREATE (m:Membership {id:$id, user_id:$uid, team_id:$tid, "
            "role:$role, status:'active', joinedAt:$now, created_at:$now})",
            params={"id": mid, "uid": user_id, "tid": team_id,
                    "role": role, "now": now},
        )
        # Create BELONGS_TO edge
        reg.query(
            "MATCH (m:Membership {id:$mid}), (t:Team {id:$tid}) "
            "CREATE (m)-[:BELONGS_TO]->(t)",
            params={"mid": mid, "tid": team_id},
        )

        self._audit(team_id, user_id, "membership_create",
                     resource_type="membership", resource_id=mid)
        return {"id": mid, "team_id": team_id, "user_id": user_id, "role": role}

    def membership_get(self, membership_id: str) -> dict | None:
        """Get a membership by ID."""
        reg = self._get_registry()
        rows = reg.query(
            "MATCH (m:Membership {id:$id}) RETURN properties(m)",
            params={"id": membership_id},
        ).result_set
        return rows[0][0] if rows else None

    def membership_list(self, team_id: str) -> list[dict]:
        """List all memberships for a team."""
        reg = self._get_registry()
        rows = reg.query(
            "MATCH (m:Membership {team_id:$tid}) RETURN properties(m)",
            params={"tid": team_id},
        ).result_set
        return [r[0] for r in rows]

    def membership_update_role(self, membership_id: str,
                                new_role: str) -> dict:
        """Update a membership's role."""
        from .exceptions import ControlPlaneError
        if new_role not in ("owner", "admin", "member"):
            raise ControlPlaneError(
                f"Invalid role {new_role!r}. Must be 'owner', 'admin', or 'member'."
            )
        m = self.membership_get(membership_id)
        if m is None:
            raise ControlPlaneError(f"Membership {membership_id!r} not found")
        reg = self._get_registry()
        reg.query(
            "MATCH (m:Membership {id:$id}) SET m.role = $role",
            params={"id": membership_id, "role": new_role},
        )
        self._audit(m["team_id"], m["user_id"], "membership_update_role",
                     resource_type="membership", resource_id=membership_id)
        return self.membership_get(membership_id) or {}

    def membership_delete(self, membership_id: str) -> dict:
        """Delete a membership. Idempotent."""
        m = self.membership_get(membership_id)
        if m is None:
            return {"deleted": False, "reason": "not found"}
        reg = self._get_registry()
        reg.query(
            "MATCH (m:Membership {id:$id}) DETACH DELETE m",
            params={"id": membership_id},
        )
        self._audit(m["team_id"], m["user_id"], "membership_delete",
                     resource_type="membership", resource_id=membership_id)
        return {"deleted": True, "membership_id": membership_id}

    # ── Control Plane: APIKey CRUD ─────────────────────────────────

    def _verify_hashed_lookup(self, label: str, prop: str, plaintext: str) -> list[dict]:
        """Verify a plaintext secret against stored salted hashes in the registry.

        hash_api_key() embeds a per-key random salt ("salt:hash"), so we can
        NOT look up by exact hash match — the lookup hash would never equal the
        stored hash (same root cause as #130).

        #687: For APIKey nodes, we short-circuit the O(keys) scan by filtering
        on key_prefix (key[:10] = "tt_<8 hex chars>"). The key_prefix index
        (created in _ensure_registry_indexes) makes this O(1) per lookup.
        Falls back to full scan for legacy provision_tenant keys whose
        key_prefix was set to team_id[:8] (which won't match token[:10]).
        """
        from tortoise.auth import API_KEY_PREFIXES, verify_api_key
        reg = self._get_registry()

        # #687: indexed key_prefix lookup avoids O(keys) PBKDF2 scan
        if label == "APIKey" and plaintext.startswith(API_KEY_PREFIXES):
            prefix = plaintext[:10]
            rows = reg.query(
                f"MATCH (n:{label}) WHERE n.key_prefix = $prefix "
                f"RETURN n.{prop}, properties(n)",
                params={"prefix": prefix},
            ).result_set
            out = []
            for stored_hash, props in rows:
                if verify_api_key(plaintext, stored_hash):
                    out.append(props)
            if out:
                return out
            # Fall through to full scan for legacy provision_tenant keys
            # (key_prefix = team_id[:8] won't match token[:10] = "tt_<8 hex>")

        rows = reg.query(
            f"MATCH (n:{label}) RETURN n.{prop}, properties(n)"
        ).result_set
        out = []
        for stored_hash, props in rows:
            if verify_api_key(plaintext, stored_hash):
                out.append(props)
        return out

    def apikey_create(self, team_id: str, created_by: str,
                      *, graph_id: str | None = None,
                      scopes: list | None = None,
                      created_by_key_id: str | None = None,
                      delegation_depth: int | None = None,
                      prefix: str = "tt_",
                      name: str | None = None,
                      created_via: str | None = None) -> dict:
        """Generate an API key for a team.

        Stores SHA-256 hash (never plaintext). Plaintext returned once.

        C1 (#2110) tenancy kwargs (all optional — absent = legacy shape,
        back-compat for existing callers): graph_id (NULL = team-wide key
        → default graph), scopes (FLAT allowlist, default []), mint lineage
        (created_by_key_id + delegation_depth; 0 = minted cannot-escalate,
        NULL = owner-minted). C2 (#2111): ``prefix`` (default "tt_") lets
        the provisioning service mint tk_ per-graph keys (epic vocabulary);
        existing callers unchanged. C3 (#2112): ``name`` (user-facing label,
        20260825000001 parity — the hosted create_api_key lane passes it)
        and ``created_via`` (mint-source classification — "provisioned" /
        "agent_signup" etc.) ride the node as optional props; absent =
        legacy nodes without them.

        Registry-side invariant (code-review #2b, mirrors the Supabase DB
        CHECK chk_minted_key_no_escalation): a MINTED key (delegation_depth
        = 0) can never hold escalation scopes (graphs:create/delete,
        keys:manage, team:manage) — only owner-minted keys may. The app
        mint (_mint_graph_key) already ∩ _MINTABLE_SCOPES, but apikey_create
        is a public SDK method (graph-scripts, C3 drift surface): the check
        here keeps selfhost parity with the hosted DB invariant once C5
        flips deleg=0 dormancy off and starts honoring scopes.
        """
        import uuid  # noqa: I001
        from datetime import datetime, timezone
        from tortoise.auth import hash_api_key
        from .exceptions import ControlPlaneError

        _ESCALATION_SCOPES = {"graphs:create", "graphs:delete",
                              "keys:manage", "team:manage"}
        if delegation_depth == 0 and scopes and any(
                s in _ESCALATION_SCOPES for s in scopes):
            raise ControlPlaneError(
                "Minted keys (delegation_depth=0) cannot hold escalation "
                "scopes: " + ",".join(sorted(
                    _ESCALATION_SCOPES & set(scopes))) + ".",
            )

        team = self.team_get(team_id)
        if team is None:
            raise ControlPlaneError(f"Team {team_id!r} not found")

        api_key = f"{prefix}{uuid.uuid4().hex}"
        key_hash = hash_api_key(api_key)
        key_prefix = api_key[:10]
        kid = ulid()
        now = datetime.now(timezone.utc).isoformat()  # noqa: UP017

        reg = self._get_registry()
        # C1 (#2110): include non-None tenancy props in the CREATE (absent =
        # old callers unchanged; old registry nodes without the props resolve
        # with safe defaults). C3 (#2112): name/created_via ride the same
        # optional-props pattern.
        extra = ""
        params = {"id": kid, "tid": team_id, "kh": key_hash,
                  "kp": key_prefix, "cb": created_by, "now": now}
        if graph_id is not None:
            extra += ", graph_id:$gid"; params["gid"] = graph_id  # noqa: E702 (baseline #1503)
        if scopes:
            extra += ", scopes:$sc"; params["sc"] = list(scopes)  # noqa: E702 (baseline #1503)
        if created_by_key_id is not None:
            extra += ", created_by_key_id:$cbk"; params["cbk"] = created_by_key_id  # noqa: E702 (baseline #1503)
        if delegation_depth is not None:
            extra += ", delegation_depth:$dd"; params["dd"] = delegation_depth  # noqa: E702 (baseline #1503)
        if name is not None:
            extra += ", name:$nm"; params["nm"] = name  # noqa: E702 (baseline #1503)
        if created_via is not None:
            extra += ", created_via:$cv"; params["cv"] = created_via  # noqa: E702 (baseline #1503)
        reg.query(
            "CREATE (k:APIKey {id:$id, team_id:$tid, key_hash:$kh, "
            "key_prefix:$kp, created_by:$cb, created_at:$now"
            + (extra or "") + "})",
            params=params,
        )
        # BELONGS_TO edge
        reg.query(
            "MATCH (k:APIKey {id:$kid}), (t:Team {id:$tid}) "
            "CREATE (k)-[:BELONGS_TO]->(t)",
            params={"kid": kid, "tid": team_id},
        )

        self._audit(team_id, created_by, "apikey_create",
                     resource_type="apikey", resource_id=kid)
        return {"id": kid, "key_prefix": key_prefix, "api_key": api_key,
                "team_id": team_id, "created_at": now}

    def apikey_list(self, team_id: str) -> list[dict]:
        """List API keys for a team (no plaintext or hashes).

        C1 (#2110): rows gain the tenancy props (graph_id/scopes/
        delegation_depth/created_by_key_id) — absent on pre-C1 nodes →
        None-safe defaults (graph_id None = team-wide, scopes [] = legacy).
        """
        reg = self._get_registry()
        rows = reg.query(
            "MATCH (k:APIKey {team_id:$tid}) "
            "RETURN k.id, k.key_prefix, k.created_by, k.created_at, "
            "k.last_used_at, k.revoked_at, "
            "k.graph_id, k.scopes, k.delegation_depth, k.created_by_key_id",
            params={"tid": team_id},
        ).result_set
        keys = []
        for r in rows:
            keys.append({
                "id": r[0], "key_prefix": r[1], "created_by": r[2],
                "created_at": r[3], "last_used_at": r[4], "revoked_at": r[5],
                "graph_id": r[6], "scopes": r[7] or [],
                "delegation_depth": r[8], "created_by_key_id": r[9],
            })
        return keys

    def apikey_revoke(self, key_id: str) -> dict:
        """Revoke an API key (soft delete — sets revoked_at). Idempotent."""
        from datetime import datetime, timezone
        reg = self._get_registry()
        rows = reg.query(
            "MATCH (k:APIKey {id:$id}) RETURN k.revoked_at, k.team_id",
            params={"id": key_id},
        ).result_set
        if not rows:
            return {"revoked": False, "reason": "not found"}
        if rows[0][0] is not None:
            return {"revoked": True, "already": True, "key_id": key_id}
        now = datetime.now(timezone.utc).isoformat()  # noqa: UP017
        reg.query(
            "MATCH (k:APIKey {id:$id}) SET k.revoked_at = $now",
            params={"id": key_id, "now": now},
        )
        self._audit(rows[0][1], None, "apikey_revoke",
                     resource_type="apikey", resource_id=key_id)
        return {"revoked": True, "key_id": key_id, "revoked_at": now}

    def apikey_verify(self, key_plaintext: str) -> dict | None:
        """Verify an API key against stored hashes.

        Returns {team_id, key_id} if valid, None if not found or revoked.
        C2 (#2111): also returns delegation_depth + scopes when present on
        the node (MCP's TeamResolutionMiddleware rejects deleg=0 minted
        keys — the REST surface is gated; MCP must not be the fail-open
        lane for handed-out per-graph keys). Uses salted-hash verification
        (per-key salt means exact-hash lookup never matches — see #130,
        #139). #1709: expires_at filtering with NULL-as-never-expires
        semantics (mirrors the REST path #742 + the agent_signup mint,
        which now writes expires_at:null) — a legacy selfhost key without
        the prop must keep authenticating.
        """
        from datetime import datetime, timezone as _tz  # noqa: I001
        now_iso = datetime.now(_tz.utc).isoformat()  # noqa: UP017
        matches = [
            p for p in self._verify_hashed_lookup("APIKey", "key_hash", key_plaintext)
            if p.get("revoked_at") is None
            and (p.get("expires_at") is None or p.get("expires_at") > now_iso)
        ]
        if matches:
            m = matches[0]
            return {"team_id": m["team_id"], "key_id": m["id"],
                    "delegation_depth": m.get("delegation_depth"),
                    "scopes": m.get("scopes") or []}
        return None

    # ── Control Plane: Agent signup tokens (#1709, approach C) ────────
    # The signup token is a server-issued 256-bit st_<64hex> bearer token
    # stored hash-only (SHA-256(PEPPER + token)) — re-presenting it is the
    # dedupe check AND the keyless-recovery credential. Token lookup reuses
    # the hashed-lookup pattern (Invitation precedent); FalkorDB has no
    # transactions, so the recovery check+mint+revoke is serialized under a
    # process-local lock (parity with the Supabase lane's FOR UPDATE row
    # lock — the non-bootstrap key cap cannot overshoot under concurrency).

    def signup_token_lookup(self, token_plaintext: str) -> dict | None:
        """Resolve a signup token → its node props; None when unknown/revoked.

        Registry parity for resolve_signup_token (Supabase lane). The caller
        (hosted_api) maps None to the UNIFORM 422 invalid_signup_token.
        """
        if not isinstance(token_plaintext, str) or not token_plaintext:
            # #1709 fixer P2.1: a non-str token must be "unknown" (None →
            # uniform 422), NEVER an AttributeError from the registry scan
            # (verify_api_key encodes the plaintext) — defense-in-depth
            # behind hosted_api's format gate.
            return None
        # [SECOND-MODEL-GATE] P2: exact-match on the deterministic lookup_key
        # (SHA-256+pepper, stored at mint) FIRST — avoids the O(keys) PBKDF2
        # full-scan per probe (a distributed-IP DoS vector on multi-team
        # selfhosts). PBKDF2 verify below is defense-in-depth (the minted
        # node carries both hashes).
        from tortoise.auth import lookup_hash as _lookup_hash
        lk = _lookup_hash(token_plaintext)
        rows = self._get_registry().query(
            "MATCH (n:SignupToken {lookup_key:$lk}) RETURN properties(n)",
            params={"lk": lk},
        ).result_set
        matches = [r[0] for r in rows if r[0].get("revoked_at") is None]
        if not matches:
            matches = [
                p for p in self._verify_hashed_lookup("SignupToken", "token_hash", token_plaintext)
                if p.get("revoked_at") is None
            ]
        return matches[0] if matches else None

    def signup_token_recover(self, token_plaintext: str) -> dict:
        """Keyless recovery: mint a NEW key on the token's team.

        Registry parity for recover_team_key (Supabase lane). Cap + revoke-
        oldest-non-bootstrap (#750.10 semantics) mirror the SQL; created_by
        is token-attributable ('st_' + left(token_hash, 12)) — never a
        caller-supplied identity. Returns {api_key, team_id, team_name,
        tier, graph_name}. Raises ControlPlaneError (→ uniform 422) when the
        token is unknown/revoked or the team is soft-deleted.
        """
        from datetime import datetime, timezone as _tz  # noqa: I001
        from .exceptions import ControlPlaneError

        node = self.signup_token_lookup(token_plaintext)
        if node is None:
            raise ControlPlaneError("signup token not found or revoked")
        team_id = node.get("team_id") or ""
        team = self.team_get(team_id)
        if team is None or team.get("deleted_at"):
            raise ControlPlaneError("signup token team deleted")

        import uuid  # noqa: I001
        from tortoise.auth import hash_api_key, lookup_hash
        api_key = f"tt_{uuid.uuid4().hex}"
        key_hash = hash_api_key(api_key)
        now_iso = datetime.now(_tz.utc).isoformat()  # noqa: UP017
        token_hash = lookup_hash(token_plaintext)
        reg = self._get_registry()
        with _SIGNUP_TOKEN_RECOVER_LOCK:
            # re-verify under the lock (parity with the FOR UPDATE re-check)
            node = self.signup_token_lookup(token_plaintext)
            if node is None:
                raise ControlPlaneError("signup token not found or revoked")
            if node.get("team_id") != team_id:
                raise ControlPlaneError("signup token not found or revoked")
            # cap: active non-bootstrap keys; insert FIRST, re-count AFTER,
            # revoke-oldest only when genuinely over cap ([SECOND-MODEL-GATE]
            # P2: mirrors the SQL's self-healing ordering — an unlocked race
            # still converges to <= cap, unlike count-then-revoke-then-insert).
            max_keys = int(team.get("max_api_keys")
                           or self._default_max_api_keys())
            kid = ulid()
            # C1 (#2110) decision record: the recovery-mint is NOT extended
            # with tenancy kwargs — recovery keys are the owner-level keyless
            # path (created_via='recovery'), so by D2 they resolve as the
            # legacy/owner full-access class (deleg NULL + no scopes). This is
            # intended (E2E-5 zero behavior shift); C3 must NOT assume minted
            # keys are all deleg=0 — recovery keys are owner-class.
            reg.query(
                "CREATE (k:APIKey {id:$id, team_id:$tid, key_hash:$kh, "
                "key_prefix:$kp, created_by:$cb, created_via:'recovery', "
                "created_at:$now, expires_at:null})",
                params={"id": kid, "tid": team_id, "kh": key_hash,
                        "kp": api_key[:10], "cb": "st_" + token_hash[:12],
                        "now": now_iso},
            )
            # re-count AFTER the insert; only revoke when over cap (and a row
            # genuinely existed to revoke)
            rows = reg.query(
                "MATCH (k:APIKey {team_id:$tid}) WHERE k.revoked_at IS NULL "
                "AND (k.created_via IS NULL OR k.created_via <> 'bootstrap') "
                "RETURN k.id, k.created_at ORDER BY k.created_at ASC",
                params={"tid": team_id},
            ).result_set
            if len(rows) > max_keys and rows:
                reg.query(
                    "MATCH (k:APIKey {id:$id}) SET k.revoked_at = $now",
                    params={"id": rows[0][0], "now": now_iso},
                )
        self._audit(team_id, "st_" + token_hash[:12], "apikey_create",
                     resource_type="apikey", resource_id=kid)
        return {"api_key": api_key, "team_id": team_id,
                "team_name": team.get("name"), "tier": team.get("tier") or "free",
                "graph_name": team.get("graph_name") or f"team_{team_id}"}

    def signup_token_revoke(self, token_plaintext: str, team_id: str) -> dict:
        """Revoke a signup token (set revoked_at) — registry parity (#1715).

        Team-scoped: the SignupToken node's team_id must match ``team_id`` or
        the revoke is refused (a caller can only kill their own team's
        token). Idempotent: an unknown / other-team / already-revoked token
        is a no-op, never an error. Returns
        ``{"team_id": str, "status": "revoked" | "already" |
        "not_found" | "not_owned"}`` — the endpoint maps status to
        200/404/403 (the no-oracle uniform-422 contract is preserved for
        malformed tokens upstream; an authenticated caller probing a valid
        token learns only whether it is THEIR team's).

        #1754: (a) the revoke WRITE is atomically team-scoped (parity with
        the SQL lane's UPDATE ... AND team_id = p_team_id) — the pre-read
        can never be raced by a foreign-team node; (b) a node carrying only
        token_hash (no lookup_key) is found via the PBKDF2 fallback (mirror
        signup_token_lookup) so it is REVOCABLE, not just recoverable.
        """
        from tortoise.auth import lookup_hash as _lookup_hash
        lk = _lookup_hash(token_plaintext)
        rows = self._get_registry().query(
            "MATCH (n:SignupToken {lookup_key:$lk}) RETURN properties(n)",
            params={"lk": lk},
        ).result_set
        matches = [r[0] for r in rows]
        if not matches:
            # #1754 (b): PBKDF2 fallback parity with signup_token_lookup — a
            # node carrying only the salted token_hash (minted before
            # lookup_key, or a legacy row) resolves here, so it can be
            # revoked instead of silently no-op'ing as "not_found".
            matches = list(self._verify_hashed_lookup(
                "SignupToken", "token_hash", token_plaintext))
        if not matches:
            return {"team_id": team_id, "status": "not_found"}
        node = matches[0]
        if node.get("team_id") != team_id:
            return {"team_id": team_id, "status": "not_owned"}
        if node.get("revoked_at") is not None:
            return {"team_id": team_id, "status": "already"}
        from datetime import datetime, timezone as _tz  # noqa: I001
        now_iso = datetime.now(_tz.utc).isoformat()  # noqa: UP017
        # #1754 (a): the MATCH is team-scoped so the write itself can never
        # revoke a foreign team's node. Hash-only fallback nodes (no
        # lookup_key) are targeted by their stored salted hash — unique per
        # token (random salt per mint) and team-scoped.
        if node.get("lookup_key"):
            self._get_registry().query(
                "MATCH (n:SignupToken {lookup_key:$lk, team_id:$tid}) "
                "SET n.revoked_at = $now",
                params={"lk": lk, "tid": team_id, "now": now_iso},
            )
        else:
            self._get_registry().query(
                "MATCH (n:SignupToken {token_hash:$th, team_id:$tid}) "
                "SET n.revoked_at = $now",
                params={"th": node["token_hash"], "tid": team_id,
                        "now": now_iso},
            )
        return {"team_id": team_id, "status": "revoked"}

    def _default_max_api_keys(self) -> int:
        from tortoise.pricing import tier_limits
        return int(tier_limits("free").get("max_api_keys", 2))

    # ── Control Plane: Invitation CRUD ─────────────────────────────

    def invitation_create(self, team_id: str, email: str, role: str,
                          created_by: str) -> dict:
        """Create an invitation with 7-day expiry.

        Token is hashed for storage; plaintext returned once.
        """
        import uuid  # noqa: I001
        from datetime import datetime, timedelta, timezone
        from tortoise.auth import hash_api_key
        from .exceptions import ControlPlaneError

        team = self.team_get(team_id)
        if team is None:
            raise ControlPlaneError(f"Team {team_id!r} not found")
        if role not in ("owner", "admin"):
            raise ControlPlaneError(
                f"Invalid role {role!r}. Must be 'owner' or 'admin'."
            )

        # Reject duplicate pending invitations for same email+team
        reg = self._get_registry()
        dup = reg.query(
            "MATCH (i:Invitation {team_id:$tid, email:$email}) "
            "WHERE i.accepted_at IS NULL AND (i.status IS NULL OR i.status <> 'revoked') "
            "RETURN count(i) > 0",
            params={"tid": team_id, "email": email},
        ).result_set[0][0]
        if dup:
            raise ControlPlaneError(
                f"Pending invitation already exists for {email} in this team"
            )

        token = str(uuid.uuid4())
        token_hash = hash_api_key(token)
        iid = ulid()
        now = datetime.now(timezone.utc).isoformat()  # noqa: UP017
        expires_at = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()  # noqa: UP017

        reg.query(
            "CREATE (i:Invitation {id:$id, team_id:$tid, email:$email, "
            "role:$role, token_hash:$th, created_by:$cb, "
            "created_at:$now, expires_at:$exp, accepted_at:null})",
            params={"id": iid, "tid": team_id, "email": email,
                    "role": role, "th": token_hash, "cb": created_by,
                    "now": now, "exp": expires_at},
        )
        # FOR_TEAM edge
        reg.query(
            "MATCH (i:Invitation {id:$iid}), (t:Team {id:$tid}) "
            "CREATE (i)-[:FOR_TEAM]->(t)",
            params={"iid": iid, "tid": team_id},
        )

        self._audit(team_id, created_by, "invitation_create",
                     resource_type="invitation", resource_id=iid)
        return {"id": iid, "email": email, "role": role,
                "expires_at": expires_at, "token": token}

    def invitation_list(self, team_id: str) -> list[dict]:
        """List invitations for a team (no token hashes)."""
        reg = self._get_registry()
        rows = reg.query(
            "MATCH (i:Invitation {team_id:$tid}) "
            "RETURN i.id, i.email, i.role, i.created_by, i.created_at, "
            "i.expires_at, i.accepted_at, i.status",
            params={"tid": team_id},
        ).result_set
        invs = []
        for r in rows:
            invs.append({
                "id": r[0], "email": r[1], "role": r[2],
                "created_by": r[3], "created_at": r[4],
                "expires_at": r[5], "accepted_at": r[6], "status": r[7],
            })
        return invs

    def invitation_get_by_token(self, token_plaintext: str) -> dict | None:
        """Look up an invitation by its plaintext token (salted-hash verify)."""
        matches = self._verify_hashed_lookup("Invitation", "token_hash", token_plaintext)
        return matches[0] if matches else None

    def invitation_accept(self, invitation_id: str, user_id: str) -> dict:
        """Accept an invitation and create a membership.

        Checks expiry and single-use (not already accepted).
        """
        from datetime import datetime, timezone  # noqa: I001
        from .exceptions import ControlPlaneError

        inv = self.invitation_get_by_id(invitation_id)
        if inv is None:
            raise ControlPlaneError(f"Invitation {invitation_id!r} not found")

        expires_at = inv.get("expires_at", "")
        now = datetime.now(timezone.utc)  # noqa: UP017
        if expires_at:
            exp = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            if now > exp:
                raise ControlPlaneError("Invitation has expired")

        if inv.get("accepted_at"):
            raise ControlPlaneError("Invitation already accepted")

        if inv.get("status") == "revoked":
            raise ControlPlaneError("Invitation has been revoked")

        # Accept: mark as accepted + create membership
        now_iso = now.isoformat()
        reg = self._get_registry()
        reg.query(
            "MATCH (i:Invitation {id:$id}) SET i.accepted_at = $now",
            params={"id": invitation_id, "now": now_iso},
        )

        membership = self.membership_create(
            team_id=inv["team_id"],
            user_id=user_id,
            role=inv.get("role", "admin"),
        )

        self._audit(inv["team_id"], user_id, "invitation_accept",
                     resource_type="invitation", resource_id=invitation_id)
        return {"membership_id": membership["id"],
                "team_id": inv["team_id"], "accepted_at": now_iso}

    def invitation_get_by_id(self, invitation_id: str) -> dict | None:
        """Get an invitation by its ULID."""
        reg = self._get_registry()
        rows = reg.query(
            "MATCH (i:Invitation {id:$id}) RETURN properties(i)",
            params={"id": invitation_id},
        ).result_set
        return rows[0][0] if rows else None

    def invitation_revoke(self, invitation_id: str) -> dict:
        """Revoke an invitation (soft delete). Idempotent."""
        inv = self.invitation_get_by_id(invitation_id)
        if inv is None:
            return {"revoked": False, "reason": "not found"}
        if inv.get("status") == "revoked":
            return {"revoked": True, "already": True,
                    "invitation_id": invitation_id}
        reg = self._get_registry()
        reg.query(
            "MATCH (i:Invitation {id:$id}) SET i.status = 'revoked'",
            params={"id": invitation_id},
        )
        self._audit(inv["team_id"], None, "invitation_revoke",
                     resource_type="invitation", resource_id=invitation_id)
        return {"revoked": True, "invitation_id": invitation_id}

    def cleanup_expired_invitations(self) -> dict:
        """Mark expired invitations as 'expired' status AND delete the fake
        invite-{iid} Membership row for each (#1908) — an expired invite
        must not leave a permanent ghost in registry list_members (pre-#1880
        ghosts are swept by sweep_invite_ghost_memberships).

        Order matters (#1908 review P2): the ghost rows are deleted BEFORE
        the 'expired' stamp so a failed delete is retried on the next run
        (a stamp-first ordering would strand the ghost forever — the
        re-run predicate skips already-expired invites). Deletes are
        best-effort, mirroring _delete_fake_invite_membership (#1902 P2).

        Returns counts: ``cleaned`` invitations marked expired and
        ``ghosts_deleted`` fake membership rows removed.
        """
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()  # noqa: UP017
        reg = self._get_registry()
        pending = reg.query(
            "MATCH (i:Invitation) "
            "WHERE i.expires_at < $now AND i.accepted_at IS NULL "
            "AND (i.status IS NULL OR i.status <> 'expired') "
            "RETURN i.id, i.team_id",
            params={"now": now},
        ).result_set
        deleted = 0
        for iid, team_id in pending:
            try:
                reg.query(
                    "MATCH (m:Membership {team_id:$tid, user_id:$fake}) DELETE m",
                    params={"tid": team_id, "fake": f"invite-{iid}"},
                )
                deleted += 1
            except Exception as _e:
                _logger.warning(
                    "invite ghost-cleanup failed for %s on %s (%s)",
                    iid, team_id, _e)
        reg.query(
            "MATCH (i:Invitation) "
            "WHERE i.expires_at < $now AND i.accepted_at IS NULL "
            "AND (i.status IS NULL OR i.status <> 'expired') "
            "SET i.status = 'expired'",
            params={"now": now},
        )
        return {"cleaned": len(pending), "ghosts_deleted": deleted}

    def sweep_invite_ghost_memberships(self, *, dry_run: bool = False) -> dict:
        """#1908: one-time backfill sweep for pre-#1880 invite-{iid} ghosts.

        Deletes Membership rows whose user_id matches 'invite-*' when the
        backing Invitation node is TERMINAL — consumed (accepted_at set),
        expired (expires_at past or status='expired'), revoked
        (status='revoked' / 'accepted'), or MISSING (orphaned fake row with
        no Invitation node). Rows backing a still-pending, unexpired invite
        are kept (they are the legit 'invited' placeholder in list_members).

        Idempotent — re-running after a sweep finds nothing. With
        ``dry_run=True`` reports the ghost count without writing (the
        graph-script's --dry-run mode).

        Returns {"found", "ghosts", "deleted"} — found = invite-* rows
        examined, ghosts = rows qualifying for deletion, deleted = rows
        actually removed (0 under dry_run).
        """
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()  # noqa: UP017
        reg = self._get_registry()
        rows = reg.query(
            "MATCH (m:Membership) WHERE m.user_id STARTS WITH 'invite-' "
            "OPTIONAL MATCH (i:Invitation {id: substring(m.user_id, 7)}) "
            "RETURN m.user_id, m.team_id, properties(i)",
        ).result_set
        ghosts: list[tuple[str, str]] = []
        for fake_uid, team_id, inv_props in rows:
            if inv_props is None:
                ghosts.append((fake_uid, team_id))  # orphaned fake row
                continue
            node = dict(inv_props)
            consumed = node.get("accepted_at") is not None \
                or node.get("status") in ("accepted", "revoked", "expired")
            expired = node.get("expires_at") is not None \
                and node["expires_at"] < now
            if consumed or expired:
                ghosts.append((fake_uid, team_id))
        failed = 0
        if not dry_run:
            for fake_uid, team_id in ghosts:
                try:
                    reg.query(
                        "MATCH (m:Membership {team_id:$tid, user_id:$uid}) DELETE m",
                        params={"tid": team_id, "uid": fake_uid},
                    )
                except Exception as _e:
                    # best-effort, mirroring _delete_fake_invite_membership
                    # (#1902 P2) — a transient failure is retried by a re-run.
                    failed += 1
                    _logger.warning(
                        "invite ghost-cleanup failed for %s on %s (%s)",
                        fake_uid, team_id, _e)
        return {"found": len(rows), "ghosts": len(ghosts),
                "deleted": 0 if dry_run else len(ghosts) - failed}

    # ── Helpers ───────────────────────────────────────────────────

    def _expand_kind(self, kind: str) -> list[str]:
        """Expand kind via subclassOf + equivalentTo for Cypher IN clause.

        Uses PackRegistry.expand_kind(). Registry is loaded once and cached.
        Returns [kind] if no packs loaded or kind is unknown.
        """
        return _get_kind_expander().expand_kind(kind)

    def _resolve_traversal_path(self, path: str) -> dict | None:
        """Resolve 'Product→Feature' to {predicate, fromKind, toKind} from pack registry.

        Matches against pack-declared relations — fromKind/toKind suffixes
        (e.g., 'product-strategy:product' matches 'Product' via kind name 'product').
        Returns None if no matching relation found.
        """
        segments = [s.strip() for s in path.split("→")]
        if len(segments) < 2:
            # Hint: user may have used ASCII '->' instead of Unicode '→'
            if "->" in path:
                _logger.warning(
                    "traversal_path uses ASCII '->' — use Unicode '→' instead "
                    "(e.g., 'Product→Feature')"
                )
            return None

        registry = _get_kind_expander()
        relations = registry.list_relations()
        if not relations:
            return None

        from_name, to_name = segments[0].strip(), segments[1].strip()

        for rel in relations:
            if "fromKind" not in rel or "toKind" not in rel:
                continue
            fk = rel["fromKind"]
            tk = rel["toKind"]
            # Extract kind name after the namespace prefix
            fk_name = fk.split(":", 1)[-1] if ":" in fk else fk
            tk_name = tk.split(":", 1)[-1] if ":" in tk else tk
            # Match case-insensitively against path segments
            if fk_name.lower() == from_name.lower() and tk_name.lower() == to_name.lower():
                return {"predicate": rel["predicate"], "fromKind": fk, "toKind": tk}

        return None

    @staticmethod
    def _validate_kind(kind: str) -> None:
        # ponytail: open-ended kind vocabularies — any string accepted.
        # Warning for unrecognized values; domain_loader.register_kind() can suppress.
        # #951: vocabulary source is the domain_loader adapter — the compiled
        # pack pointKind bucket (pack_registry canonical, plan §5.2 boundary 4).
        if kind not in known_kinds("pointKind"):
            _logger.warning(
                "Unrecognized pointKind %r. Known values: %s. "
                "Use tortoise.domain_loader.register_kind(%r) to register it.",
                kind, sorted(known_kinds("pointKind")), kind,
            )


    # ── Entity CRUD (ONTOLOGY v2.5 §3, all 7 types) ──────────────────

    def _create_entity(self, label: str, id_val: str, props: dict, event_type: str,
                       *, _skip_sanitize: bool = False,
                       is_episodic: bool | None = None) -> dict:
        """Generic entity creation. Applies to graph via projection
        (FalkorDB); SDK-created Events additionally journal EventRecorded
        via ``_emit_event`` (#2061).

        ``_skip_sanitize=True`` (epic #900 T3, create_source's sanctioned
        source_path route): the caller has already extracted the server-
        managed ``source_path``/``source_path`` keys into an explicit kwarg and
        sanitized the remainder — bypassing ``_sanitize_props`` would otherwise
        fail-closed on the sanctioned key (the sanitizer's own docstring carves
        out ``api.add_document(source_path=)``; create_source(source_path=) is
        the mirror route).
        """
        # #329: id + sourcePath/source_path are server-managed — reject.
        # is_episodic is ALSO server-managed (#1486, quota discriminator) —
        # NEVER popped here: the sanitizer's unconditional reject is the
        # fail-closed backstop, and the ONLY way the flag is set is the
        # explicit `is_episodic` parameter below (internal callers), merged
        # into the event dict AFTER sanitize.
        if not _skip_sanitize:
            props = _sanitize_props(props, reject_id=True)
        if is_episodic is not None:
            props["is_episodic"] = is_episodic  # server-managed (explicit param only)
        proj = self._get_proj()
        # Build event dict
        event = {"type": event_type, "id": id_val, **props}
        # Normalize field names for projection compatibility
        if label == "Subject" and "subjectKind" in props:
            event["subject_kind"] = props["subjectKind"]
        if label == "Object" and "objectKind" in props:
            event["object_kind"] = props["objectKind"]
        if label == "Document" and "documentKind" in props:
            event["document_kind"] = props["documentKind"]
        if label == "Event" and "eventKind" in props:
            event["eventKind"] = event.get("eventKind", props.get("eventKind"))
            if "eventId" not in event:
                event["eventId"] = id_val
        if label == "Source":
            event["url"] = id_val
        if label == "Event":
            # #2061: 'point'/'payload' are JSONL-envelope-reserved names (the
            # rebuild journal uses 'point' for full point snapshots, and
            # _emit_event reserves both as kwargs) — drop them from the node
            # write as well so live and replay stay byte-identical (a caller
            # passing them as event props gets NO persistence on either path,
            # never divergent persistence).
            event.pop("point", None)
            event.pop("payload", None)
        # Apply through projection (writes to FalkorDB)
        apply_result = proj.apply(event)
        if label == "Source":
            # epic #900 T3: thread the conditional-MERGE QueryResult so
            # create_source can attribute the counter-authority outcome
            # (nodes_created) from the single statement (pin b).
            proj._source_merge_result = apply_result
        if label == "Event":
            # #2061: journal EventRecorded so SDK-created Events survive
            # rebuild_all (fold parity for Event-input operators).
            # proj.apply writes the LIVE graph only — the SDK JSONL journal
            # is written exclusively via _emit_event, and without this line a
            # rebuild replays EventRecorded (pass 1b → _upsert_event) but
            # never sees this Event → an operator with an Event input loses
            # its INPUT edge on replay ("input source ... does not resolve").
            # The journaled payload mirrors the exact dict applied above
            # (minus 'type'/'id' — _emit_event carries those; 'point'/
            # 'payload' were popped pre-apply and the journal envelope keys
            # event_id/ts/initiated_by/projection_version are excluded so a
            # caller prop can never override them) so replay upserts a
            # byte-identical Event node. EventRecorded is NOT in
            # _GRAPH_EVENT_TYPES → JSONL-only emission, no graph-event-store
            # double-write. The session-indexing path (_session_event_write)
            # journals its own EventRecorded and never routes through
            # _create_entity — no double-emission.
            self._emit_event(
                "EventRecorded",
                id=event["id"],
                **{k: v for k, v in event.items()
                   if k not in ("type", "id", "point", "payload",
                                "event_id", "ts", "initiated_by",
                                "projection_version")},
            )
        # #452: Subject/Object MERGE by name (content-hash dedup).
        # When the name already exists, the fresh id_val never lands on the
        # node (ON CREATE never fires).  Re-fetch the canonical id from the
        # graph so callers get a usable return value — matching create_point
        # dedup behavior which returns the existing point id.
        canonical_id = id_val
        if label in ("Subject", "Object") and "name" in event:
            name = event["name"]
            r = proj.g.query(
                f"MATCH (n:{label} {{name: $name}}) RETURN n.id",
                params={"name": name},
            )
            if r.result_set and r.result_set[0]:
                canonical_id = r.result_set[0][0]
        # Wire edges after entity exists in graph (use canonical id)
        if props.get("authoredBy"):
            proj.create_authored_by(canonical_id, props["authoredBy"])
        if props.get("ownedBy"):
            proj.create_owned_by(canonical_id, props["ownedBy"])
        if props.get("managedBy"):
            proj.create_managed_by(canonical_id, props["managedBy"])
        return self._get_entity(canonical_id)

    def _get_entity(self, id_val: str) -> dict:
        # NOTE (issue #327): Session/APIKey/Team/Tag nodes are intentionally
        # excluded from entity resolution — only Point/Subject/Object/Document/
        # Event/Source resolve (index-backed union). On a cross-label id
        # collision the first _RESOLVE_BRANCHES match wins (Point priority) —
        # more deterministic than the previous scan-order-arbitrary LIMIT 1.
        resolved = self._get_proj()._resolve_entity(
            id_val, by_id=True, by_eventId=True, by_url=True)
        return resolved[0]["properties"] if resolved else {}

    def _update_entity(self, id_val: str, **props) -> dict:
        # #329: id + sourcePath/source_path are server-managed — reject
        props = _sanitize_props(props, reject_id=True)
        proj = self._get_proj()
        # NOTE (issue #327): like _get_entity, entity mutation covers only the
        # canonical labels (Point/Subject/Object/Document/Source/Event).
        # Session/APIKey/Team/Tag nodes are intentionally NOT updated — legacy
        # matched them via id/eventId but no caller relies on it.
        # Per-label indexed writes (id OR eventId — original predicate; no url).
        # UNION cannot carry SET, so run each branch sequentially (#327).
        for label, prop in (("Point", "id"), ("Subject", "id"), ("Object", "id"),
                            ("Document", "id"), ("Source", "id"), ("Event", "eventId")):
            proj.g.query(
                f"MATCH (n:{label} {{{prop}:$id}}) SET n += $p",
                params={"id": id_val, "p": props},
            )
        return self._get_entity(id_val)

    def _delete_entity(self, id_val: str) -> bool:
        proj = self._get_proj()
        # NOTE (issue #327): deletion covers only canonical entity labels —
        # Session/APIKey/Team/Tag nodes are intentionally NOT deleted (legacy
        # matched them by id/eventId; no caller relies on it).
        total = 0
        for label, prop in (("Point", "id"), ("Subject", "id"), ("Object", "id"),
                            ("Document", "id"), ("Source", "id"), ("Event", "eventId")):
            r = proj.g.query(
                f"MATCH (n:{label} {{{prop}:$id}}) DETACH DELETE n RETURN count(n)",
                params={"id": id_val},
            )
            if r.result_set:
                total += r.result_set[0][0]
        return bool(total)

    def create_entity(self, type: str, name: str, *, is_episodic: bool | None = None,
                       **props) -> dict:
        """Create an entity — consolidated surface (epic #888 W2, PR #912).

        ``type`` routes to the right entity kind:
          - subject  → Subject node (subjectKind, status='live')
          - object   → Object node (objectKind, status='live')
          - event    → Event node (eventKind required) — preserves the legacy
            about* edge wiring: aboutSubject/aboutObject/aboutPoint/
            aboutDocument props are extracted and wired as typed edges
            (Event)-[:aboutSubject]->(Subject) etc. rather than stored as
            string properties (ID or name resolution, legacy behavior).
          - document → Document node (documentKind required, status='draft')

        Write nudges (nudge, don't enforce): returns ``{node, nudges}`` where
        ``nudges`` lists top related Points (by name/content token overlap) with
        a suggested IMPL/NAND/mitigate relation — advisory only, never enforced.
        """
        _coerce_props(props)  # accept MCP-style nested props= dict (#218)
        t = (type or "").strip().lower()
        if t == "subject":
            node = self._create_entity(
                "Subject", _entity_name_id("Subject", name),
                {"name": name, "subjectKind": props.pop("subjectKind", "other"),
                 "status": "live", **props},
                "SubjectAdded", is_episodic=is_episodic)
        elif t == "object":
            node = self._create_entity(
                "Object", _entity_name_id("Object", name),
                {"name": name, "objectKind": props.pop("objectKind", "other"),
                 "status": "live", **props},
                "ObjectRegistered", is_episodic=is_episodic)
        elif t == "event":
            eventKind = props.pop("eventKind", None)
            if not eventKind:
                raise ValueError(
                    "create_entity(type='event') requires eventKind")
            # Legacy create_event about* wiring — preserved verbatim (#888 W2).
            eid = self.ulid()
            about_subject = props.pop("aboutSubject", None)
            about_object = props.pop("aboutObject", None)
            about_point = props.pop("aboutPoint", None)
            about_document = props.pop("aboutDocument", None)
            node = self._create_entity("Event", eid, {
                "eventId": eid, "name": name, "eventKind": eventKind,
                "eventStatus": "scheduled", **props}, "EventRecorded",
                is_episodic=is_episodic)
            proj = self._get_proj()
            if about_subject:
                proj.create_about_edge(eid, about_subject, "aboutSubject")
                # Only name-resolve if it looks like a plain name, not an ID
                if isinstance(about_subject, str) and not _is_entity_id(about_subject):
                    proj._create_about_edges(eid, about_subject)
            if about_object:
                proj.create_about_edge(eid, about_object, "aboutObject")
                if isinstance(about_object, str) and not _is_entity_id(about_object):
                    proj._create_about_edges(eid, about_object)
            if about_point:
                proj.create_about_edge(eid, about_point, "aboutPoint")
                if isinstance(about_point, str) and not _is_entity_id(about_point):
                    proj._create_about_edges(eid, about_point)
            if about_document:
                proj.create_about_edge(eid, about_document, "aboutDocument")
                if isinstance(about_document, str) and not _is_entity_id(about_document):
                    proj._create_about_edges(eid, about_document)
        elif t == "document":
            documentKind = props.pop("documentKind", None)
            if not documentKind:
                raise ValueError(
                    "create_entity(type='document') requires documentKind")
            did = self.ulid()
            node = self._create_entity("Document", did, {
                "title": name, "documentKind": documentKind,
                "objectKind": "document", "status": "draft", **props},
                "DocumentCreated", is_episodic=is_episodic)
        else:
            raise ValueError(
                f"create_entity: unknown type {type!r} — must be one of "
                f"subject, object, event, document")
        return {
            "node": node,
            "nudges": self._nudge_candidates(
                name, exclude_ids=[node.get("id") or node.get("eventId")]),
        }

    # ── Write nudges (epic #888 W2 — nudge, don't enforce) ───────────

    _NUDGE_NAND_MARKERS = ("contradict", "disagree", "oppos", "invalid",
                           "incorrect", "false claim")

    def _nudge_candidates(self, text: str, *, exclude_ids: list[str] | None = None,
                          limit: int = 3) -> list[dict]:
        """Lightweight candidate finder for write nudges (epic #888 W2).

        Deterministic token-overlap match — no model dependency: the new node's
        name/content tokens are matched against existing Points' content/name/
        label. Bounded scan (400 rows), top-``limit`` candidates by shared-token
        count. Suggested relation:
          - IMPL     — statement Point candidate (default support link)
          - NAND     — either side carries contradiction markers
          - mitigate — candidate is an operator Point (mitigation anchor)
        Nudges are advisory only — surfaced in the write response, never
        enforced (the agent acts via operator_action/create_edge if it wants).
        """
        tokens = {w for w in re.findall(r"[a-z0-9]{4,}", (text or "").lower())}
        if not tokens:
            return []
        proj = self._get_proj()
        excluded = set(exclude_ids or [])
        rows = proj.g.query(
            "MATCH (n:Point) WHERE NOT n.id IN $ex "
            "RETURN n.id, n.content, n.name, n.label, n.is_operator LIMIT 400",
            params={"ex": list(excluded)},
        ).result_set
        scored = []
        for nid, content, pname, plabel, is_op in rows:
            if not nid or nid in excluded:
                continue
            blob = " ".join(str(x) for x in (pname, content, plabel) if x)
            wt = {w for w in re.findall(r"[a-z0-9]{4,}", blob.lower())}
            overlap = len(tokens & wt)
            if not overlap:
                continue
            if is_op:
                rel = "mitigate"
            elif any(m in (text or "").lower() or m in blob.lower()
                     for m in self._NUDGE_NAND_MARKERS):
                rel = "NAND"
            else:
                rel = "IMPL"
            scored.append({
                "candidate": nid,
                "suggested_relation": rel,
                "score": overlap,
                "reason": f"{overlap} shared term(s) with {text[:40]!r}",
            })
        scored.sort(key=lambda r: (-r["score"], r["candidate"]))
        return [{k: r[k] for k in ("candidate", "suggested_relation", "reason")}
                for r in scored[:limit]]

    def create_subject(self, name: str, subjectKind: str = "other", **props) -> dict:
        """Thin alias for create_entity(type='subject') — epic #888 W2."""
        _coerce_props(props)  # accept MCP-style nested props= dict (#218)
        if "is_episodic" in props:  # #1486: server-managed (quota discriminator)
            raise ValueError(
                "'is_episodic' is a server-managed field and cannot be set via props.")
        return self.create_entity("subject", name,
                                  subjectKind=subjectKind, **props)["node"]

    def create_object(self, name: str, objectKind: str = "other", **props) -> dict:
        """Thin alias for create_entity(type='object') — epic #888 W2."""
        _coerce_props(props)  # accept MCP-style nested props= dict (#218)
        if "is_episodic" in props:  # #1486: server-managed (quota discriminator)
            raise ValueError(
                "'is_episodic' is a server-managed field and cannot be set via props.")
        return self.create_entity("object", name,
                                  objectKind=objectKind, **props)["node"]

    def create_event(self, name: str, eventKind: str, *, is_episodic: bool | None = None,
                      **props) -> dict:
        """Create an Event node (alias for create_entity(type='event')).

        If aboutSubject, aboutObject, aboutPoint, or aboutDocument are provided
        in **props, they are extracted and wired as graph edges:
          (Event)-[:aboutSubject]->(Subject)
          (Event)-[:aboutObject]->(Object)
          (Event)-[:aboutPoint]->(Point)
          (Event)-[:aboutDocument]->(Document)
        rather than stored as string properties.
        """
        _coerce_props(props)  # accept MCP-style nested props= dict (#218)
        # Second-model gate P2: a single-nested props={"is_episodic": true}
        # flattens here and would splat-bind create_entity's explicit param —
        # reject it; the explicit `is_episodic` param (internal callers) is the
        # only channel.
        if "is_episodic" in props:
            raise ValueError(
                "'is_episodic' is a server-managed field and cannot be set via props.")
        if is_episodic is not None:
            props["is_episodic"] = is_episodic  # server-managed (explicit param only)
        return self.create_entity("event", name,
                                  eventKind=eventKind, **props)["node"]


    # ── Session Indexing (AgentSession) ─────────────────────────

    def _session_embedding(self, name: str, summary: str = "",
                           keywords: list[str] | None = None,
                           topics: list[str] | None = None) -> list[float] | None:
        """Compute the embedding for an AgentSession Event (#244).

        name + summary + keywords + topics → 384-dim vector, or None when the
        model is unavailable (session indexing must never depend on it).
        """
        from .session_indexer import compute_session_embedding
        return compute_session_embedding(name, summary, keywords, topics)

    def search_sessions(self, query: str, *, agent: str | None = None,
                        topics: list[str] | None = None,
                        after: "datetime | str | None" = None,  # noqa: F821, UP037
                        before: "datetime | str | None" = None,  # noqa: F821, UP037
                        limit: int = 10, offset: int = 0) -> list[dict]:
        """Search indexed agent sessions. Returns Events with metadata snippets.

        Routes through the hybrid engine (tortoise_fts_query entity_type='event'
        kind='AgentSession'): RRF fusion of FTS (Event subject+name index),
        vector (session embeddings computed at index time, #244) and structural
        (eventKind) strategies. Results are ordered by relevance with startedAt
        DESC as tiebreak.

        agent/topics post-filter the candidates. after/before bound the search
        to sessions whose ``startedAt`` falls in ``[after, before]`` (inclusive).
        Each may be a ``datetime`` or an ISO-8601 string (``Z`` or offset
        accepted); both are normalized to UTC ISO-8601 so the comparison
        against stored ``startedAt`` values is valid regardless of the caller's
        timezone. Sessions that lack a ``startedAt`` are EXCLUDED whenever a
        bound is set.

        When no semantic strategy contributed (FTS/vector unavailable — e.g.
        embedded FalkorDBLite without embeddings, prod before #160 deploys),
        falls back to the legacy keyword CONTAINS surface (name + keywords) so
        the previous behavior keeps working.
        """
        if not query or not query.strip():
            return []
        proj = self._get_proj()
        has_bound = after is not None or before is not None
        after_utc = _to_iso_utc(after) if after is not None else None
        before_utc = _to_iso_utc(before) if before is not None else None

        # ── Hybrid route: RRF fusion of FTS + vector + structural ──
        # Generous candidate pool — agent/topics/temporal filters drop rows
        # post-retrieval, so fetch beyond the caller's limit + offset.
        candidate_limit = max(limit * 5, 50) + offset
        hybrid = self.tortoise_fts_query(
            query, kind="AgentSession", entity_type="event",
            limit=candidate_limit,
        )
        has_semantic = any(
            r.get("scores")
            and (r["scores"].get("fts") is not None
                 or r["scores"].get("vector") is not None)
            for r in hybrid
        )
        if has_semantic and hybrid:
            # Precision gate (#244 review): rows with NO semantic signal — no
            # FTS score and no vector score (structural-only; the kind filter
            # matches ALL AgentSession events with score 1.0) are NOT results,
            # they only fed the RRF candidate pool. Note: vector-only rows are
            # deliberately kept — word-distinct semantic recall is the point
            # of #244 ("port migration" finds a session about changing the
            # FalkorDB default port); see docstring. The brute-force vector
            # strategy is threshold-less, so those rows are ranked nearest-
            # neighbors-first and precision drops when a query has no real
            # semantic match (documented behavior).
            ids = [r["id"] for r in hybrid]
            rows = proj.g.query(
                "MATCH (e:Event) WHERE e.eventId IN $ids RETURN properties(e)",
                params={"ids": ids},
            ).result_set
            props_by_id = {}
            for row in rows:
                props = dict(row[0])
                if props.get("eventId"):
                    props_by_id[props["eventId"]] = props
            ranked = []
            for r in hybrid:
                props = props_by_id.get(r["id"])
                if props is None:
                    continue
                if agent and props.get("agent") != agent:
                    continue
                if topics:
                    s_topics = set(props.get("topics") or [])
                    if not any(t in s_topics for t in topics):
                        continue
                if has_bound:
                    started = props.get("startedAt")
                    if not started:
                        continue  # sessions without startedAt excluded when a bound is set
                    if after_utc is not None and started < after_utc:
                        continue
                    if before_utc is not None and started > before_utc:
                        continue
                # Precision gate (#244 review): structural-only rows (no FTS,
                # no vector score) are NOT results — a session that neither
                # keyword-matches nor semantically matches must not appear.
                scores = r.get("scores") or {}
                if scores.get("fts") is None and scores.get("vector") is None:
                    continue
                ranked.append((scores.get("rrf") or 0.0, props))
            # Relevance (RRF) desc, startedAt DESC as tiebreak (missing = last)
            ranked.sort(
                key=lambda item: (item[0], item[1].get("startedAt") or ""),
                reverse=True,
            )
            return [props for _, props in ranked[offset:offset + limit]]

        # ── Legacy keyword fallback (no FTS/vector contribution) ──
        # Preserves the pre-#244 CONTAINS surface (name + keywords) plus the
        # #243 temporal filters (after/before, ISO-8601 UTC normalization,
        # startedAt IS NOT NULL exclusion when a bound is set).
        clauses = ["e.eventKind = 'AgentSession'"]
        params: dict = {"limit": max(limit * 3, 30)}
        query_lower = query.strip().lower()
        clauses.append("(toLower(e.name) CONTAINS $q OR any(kw IN e.keywords WHERE toLower(kw) CONTAINS $q))")
        params["q"] = query_lower
        if agent:
            clauses.append("e.agent = $agent")
            params["agent"] = agent
        if topics:
            topic_clauses = []
            for i, t in enumerate(topics):
                pk = f"topic{i}"
                topic_clauses.append(f"${pk} IN e.topics")
                params[pk] = t
            clauses.append(f"({' OR '.join(topic_clauses)})")
        has_bound = after is not None or before is not None
        if has_bound:
            # Explicitly drop sessions without startedAt — same outcome as
            # null-comparison semantics, but self-documenting and robust.
            clauses.append("e.startedAt IS NOT NULL")
        if after is not None:
            clauses.append("e.startedAt >= $after")
            params["after"] = _to_iso_utc(after)
        if before is not None:
            clauses.append("e.startedAt <= $before")
            params["before"] = _to_iso_utc(before)
        where = " AND ".join(clauses)
        params["offset"] = offset
        rows = proj.g.query(
            f"MATCH (e:Event) WHERE {where} "
            "RETURN properties(e) ORDER BY e.startedAt DESC SKIP $offset LIMIT $limit",
            params=params,
        ).result_set
        # Fetch a bit extra for scoring headroom, but honor the caller's limit.
        return [dict(r[0]) for r in rows[:limit]]

    def get_events(self, eventKind: str | None = None, limit: int = 20) -> list[dict]:
        """Get recent Events, optionally filtered by eventKind."""
        proj = self._get_proj()
        if eventKind:
            return [r[0] for r in proj.g.query(
                "MATCH (e:Event {eventKind: $ek}) RETURN properties(e) ORDER BY e.startedAt DESC LIMIT $lim",
                params={"ek": eventKind, "lim": limit}
            ).result_set]
        return [r[0] for r in proj.g.query(
            "MATCH (e:Event) RETURN properties(e) ORDER BY e.startedAt DESC LIMIT $lim",
            params={"lim": limit}
        ).result_set]

    def get_session(self, session_id: str) -> dict | None:
        """Get a single session Event by session_id (matches snake or camel case)."""
        if not session_id:
            return None
        proj = self._get_proj()
        rows = proj.g.query(
            "MATCH (e:Event {eventKind: 'AgentSession'}) "
            "WHERE e.session_id = $sid OR e.sessionId = $sid RETURN properties(e)",
            params={"sid": session_id}
        ).result_set
        return rows[0][0] if rows else None

    def index_file(self, path: str,
                   file_type: str | None = None,   # "agent_session"|"meeting"|"doc"|None(auto)
                   *, corpus_root: str | None = None,
                   corpus_name: str | None = None,
                   extract_metadata: bool = True,
                   llm_model: str | None = "gpt-5-mini",
                   embedding_repair_backoff: float | None = None,
                   ) -> dict:
        """One idempotent unit operation (epic #900 T3; A8). Returns:
        {"status": "indexed"|"updated"|"skipped"|"failed",
         "url": str, "eventId"|"documentId": str, "sourceKind": str,
         "reason"?: str,              # skipped/failed explanation. SKIPPED reasons
                                       # (PINNED): "unchanged" | "lock-held"
                                       # (retryable:true) | "duplicate-sessionId" |
                                       # "symlink-duplicate" | "inode-duplicate" |
                                       # "embedding-unavailable"
         "retryable"?: bool}
        `updated` is TWO-ARMED: (a) hash-diff MERGE — in-place update + version
        bump; (b) repair work with UNCHANGED hash (unit completion / embedding
        heal) — NO version bump.
        Raises: ValueError — unsafe path (#329), file outside corpus_root,
        unknown file_type value, unresolved corpus_root (§6.1 I6 default
        resolution: nearest ancestor equal to TORTOISE_INGEST_BASE_DIR, else
        explicit corpus_root demanded).
        """
        import os as _os
        from pathlib import Path
        # #329: single-file path validation — absolute, no `..`, and under
        # TORTOISE_INGEST_BASE_DIR when set (same family as ingest_corpus).
        if not isinstance(path, str) or not path:
            raise ValueError("index_file: path must be a non-empty string")
        raw_base = _os.environ.get("TORTOISE_INGEST_BASE_DIR")
        ingest_base = _os.path.realpath(_os.path.expanduser(raw_base)) if raw_base else None
        if not _os.path.isabs(path) or ".." in Path(path).parts:
            raise ValueError(
                f"Unsafe path {path!r} — must be absolute with no '..' components."
            )
        if ingest_base is not None and _resolve_under_base_realpath(path, ingest_base) is None:
            raise ValueError(
                f"Unsafe path {path!r} — resolves outside "
                f"TORTOISE_INGEST_BASE_DIR ({ingest_base})."
            )
        if not _os.path.exists(path):
            return {"status": "failed", "reason": "file not found",
                    "retryable": False}
        # corpus_root default resolution (§6.1 I6 pin).
        root, name = self._index_resolve_corpus_root(path, corpus_root, corpus_name)
        # REVIEW-FIX P1 (cycle-26): TORTOISE_INDEX_NO_NETWORK honored at the
        # NEW-PATH call boundary REGARDLESS of extract_metadata (cycle-10/12
        # pin; index_file is a new-path boundary — S14 precedence unit test
        # "var set + flag True → resolved False" covers this path too).
        if self._index_no_network():
            extract_metadata = False
        # REVIEW-FIX P1 (cycle-26): index_file OUTSIDE-ROOT mount-source class
        # (§6.4 cycle-7 — the (s2) class: realpath resolves in-root, st_nlink
        # == 1, "the mount-source check is the ONLY catch"). index_file is the
        # THIRD mount_source_for seam consumer — check the resolved file's
        # parent-dir chain before any read; fail closed on outside-root source.
        from .index_walk import mount_source_for_file
        ms = mount_source_for_file(_os.path.realpath(path), str(root), ingest_base)
        # REVIEW-FIX P1 (cycle-26): mount_source_for_file returns list[dict] —
        # any() over the entries (the earlier fix called .get on the list and
        # AttributeError'd on BOTH the warn and fail cells).
        if ms and any(n.get("fail") for n in ms):
            return {"status": "failed", "reason": "escape",
                    "retryable": False}
        result = self._index_process_unit(
            Path(path), corpus_root=root, corpus_name=name,
            file_type_declared=file_type, extract_metadata=extract_metadata,
            llm_model=llm_model, repair_backoff=self._index_repair_backoff(
                embedding_repair_backoff),
            fast_skip_key=None, disposition=None,
            election_owner=None, single_file=True,
        )
        return {k: v for k, v in result.items() if k in (
            "status", "url", "eventId", "documentId", "sourceKind",
            "reason", "retryable")}

    def index_directory(self, directory: str,
                        *, file_type: str = "auto",
                        corpus_name: str | None = None,
                        extract_metadata: bool = True,
                        llm_model: str | None = "gpt-5-mini",
                        embedding_repair_backoff: float | None = None,
                        progress_file: str | None = None,
                        ) -> dict:
        """Batch walk (custom bounded walker, W1 cycle-4 pin). Returns:
        {"directory", "corpus_name", "file_count", "indexed", "updated",
         "skipped", "failed", "aborted", "ignored", "errors": [...],
         "by_kind": {kind: count}, "aborted_reason"?}
        Invariant: indexed + updated + skipped + failed + aborted == file_count.
        file_count counts `*.md` files ONLY (non-md walk entries land in
        `ignored`, never in file_count or the four buckets); aborted = files
        never reached because the run aborted (bounded-abort disposition,
        E2E-19); aborted_reason names the DB-failure class when aborted > 0.
        errors[] entries carry the cause-class token (decode/size/escape/
        structural/filename/db/lock) + the rel-path + limit values where
        applicable (§6.4 cycle-21).
        Raises: ValueError — #329 boundary violations (directory +
        progress_file, realpath-resolved; symlink roots resolving outside the
        base, E2E-7(v1)); TORTOISE_MAX_FILE_MB garbage/<=0.
        """
        import os as _os  # noqa: I001
        from pathlib import Path
        from .security import resolve_under_base as _rub
        from .index_walk import (walk_markdown, compute_dispositions,  # noqa: F401
                                 DISP_ESCAPE, DISP_UNRECONCILED,
                                 DISP_STRUCTURAL)
        raw_base = _os.environ.get("TORTOISE_INGEST_BASE_DIR")
        ingest_base = _os.path.realpath(_os.path.expanduser(raw_base)) if raw_base else None

        # ── directory-argument resolution (cycle-7): the DIRECTORY argument
        # gets the SAME realpath-resolved treatment as progress_file — an
        # in-base symlink root whose target resolves OUTSIDE the base raises
        # ValueError BEFORE any walk (E2E-7(v1)); inside → indexes normally
        # with realpath-derived urls (E2E-7(v2)).
        if not isinstance(directory, str) or not directory:
            raise ValueError("index_directory: directory must be a non-empty string")
        if not _os.path.isabs(directory) or ".." in Path(directory).parts:
            raise ValueError(
                f"Unsafe directory {directory!r} — must be absolute with no "
                f"'..' components (#329)."
            )
        dir_path = Path(directory)
        if not dir_path.is_dir():
            # cycle-12 disposition: nonexistent walk root = zero-count no-op
            # (legacy session_index_health is_dir() parity — the backgrounded
            # hook must never see an unhandled FileNotFoundError traceback).
            return {"directory": str(dir_path), "corpus_name": corpus_name or "",
                    "file_count": 0, "indexed": 0, "updated": 0,
                    "skipped": 0, "failed": 0, "aborted": 0, "ignored": 0,
                    "errors": [], "by_kind": {}}
        resolved_dir = _os.path.realpath(str(dir_path))
        if ingest_base is not None and _rub(resolved_dir, ingest_base) is None:
            raise ValueError(
                f"Unsafe directory {directory!r} — its resolved target "
                f"{resolved_dir!r} is outside TORTOISE_INGEST_BASE_DIR "
                f"({ingest_base})."
            )

        # ── progress_file bounds (cycle-6): REALPATH-RESOLVED through
        # resolve_under_base BEFORE any walk/write; nothing may materialize at
        # a resolved target outside the base (E2E-10(g3)).
        if progress_file is not None:
            if not isinstance(progress_file, str) or not progress_file:
                raise ValueError("progress_file must be a non-empty string.")
            if not _os.path.isabs(progress_file):
                raise ValueError(f"progress_file must be absolute: {progress_file!r}")
            if ".." in Path(progress_file).parts:
                raise ValueError(f"progress_file contains '..': {progress_file!r}")
            if ingest_base is not None and _rub(progress_file, ingest_base) is None:
                raise ValueError(
                    f"progress_file {progress_file!r} not under "
                    f"TORTOISE_INGEST_BASE_DIR (resolved-target discipline, "
                    f"§6.4/E2E-10(g3))."
                )

        corpus_root = Path(resolved_dir)
        corpus_name = corpus_name or corpus_root.name
        max_bytes = self._index_max_bytes()  # noqa: F841
        repair_backoff = self._index_repair_backoff(embedding_repair_backoff)
        # TORTOISE_INDEX_NO_NETWORK (test-only, cycle-10): FORCES
        # extract_metadata=False-equivalent omission at the NEW-PATH call
        # boundary regardless of the flag (NEVER inside the shared
        # `_session_embedding` — the legacy path must still embed; SC4).
        if self._index_no_network():
            extract_metadata = False

        result = {
            "directory": str(corpus_root),
            "corpus_name": corpus_name,
            "file_count": 0, "indexed": 0, "updated": 0, "skipped": 0,
            "failed": 0, "aborted": 0, "ignored": 0,
            "errors": [], "by_kind": {}, "aborted_reason": None,
        }

        # ── bounded walk + pre-write dispositions ─────────────────────
        # Per-corpus run lock: serializes concurrent index_directory runs on
        # the SAME corpus in this process (the threads leg; the embedded
        # engine's cross-connection MERGE semantics cannot provide a race-safe
        # counter outcome otherwise). Different corpora parallelize.
        with _index_run_lock_for("dir:" + resolved_dir):
            return self._index_directory_locked(
                corpus_root, corpus_name, file_type, extract_metadata,
                llm_model, repair_backoff, progress_file, ingest_base, result,
                keys={} if progress_file is None else self._index_load_progress(
                    progress_file, str(corpus_root)))

    def _index_directory_locked(self, corpus_root, corpus_name, file_type,
                                extract_metadata, llm_model, repair_backoff,
                                progress_file, ingest_base, result,
                                keys) -> dict:
        """The locked body of index_directory (see the caller)."""
        import os as _os  # noqa: I001
        from pathlib import Path  # noqa: F401
        from .index_walk import (walk_markdown, compute_dispositions,
                                 DISP_ESCAPE, DISP_UNRECONCILED, DISP_STRUCTURAL)
        # REVIEW-FIX P2: file_type validated PRE-WALK (never mid-walk after
        # partial writes — the plan pins "validation, not a bucket").
        if file_type not in ("auto", "agent_session", "meeting", "doc"):
            raise ValueError(
                f"index_directory: unknown file_type {file_type!r} — "
                f"expected auto|agent_session|meeting|doc"
            )
        walked = walk_markdown(corpus_root, base=ingest_base)
        result["file_count"] = len(walked.files)
        result["ignored"] = walked.ignored
        for derr in walked.dir_errors:
            result["errors"].append({**derr, "cause": "structural"})
        for m in walked.mount_warnings:
            result["errors"].append(
                {"file": f"(mount) {m}", "error": m, "retryable": False,
                 "cause": "structural"})  # REVIEW-FIX P2: warn-not-fail mount
        # entries are NOT escapes — "escape" is reserved for real rejections
        disp = compute_dispositions(walked.files, corpus_root,
                                    banned_prefixes=walked.banned_prefixes)

        # ── resume checkpoint (fast-skip keys; §5.3) — the caller already
        # loaded them (keys param); stale/corrupt → empty → full re-run.
        checkpoint_counter = 0

        proj = self._get_proj()
        # Primary-election map (W4 duplicate-sessionId row; derived-id
        # collisions included): FIRST sorted rel-path owns the Event.
        session_owners: dict[str, str] = {}
        # Connection-level DB retry budget (E2E-19 recover-vs-abort pin).
        db_streak = 0
        aborted_reason: str | None = None
        processed = 0

        try:
            for path, _st in walked.files:
                rel = _os.path.relpath(str(path), str(corpus_root))
                key = keys.get(rel)
                # flag-conditional fast-skip (cycle-7): True-recorded keys are
                # fast-skippable by either flag; False-recorded keys are
                # RE-EXAMINED by a True run (the completeness gate must run).
                if key is not None and extract_metadata and not key.get("metadata", False):
                    key = None
                sid_claim = key.get("sid") if key is not None else None
                if sid_claim is not None:
                    session_owners.setdefault(sid_claim, rel)
                disposition = disp.by_path.get(str(path))
                if disposition in (DISP_ESCAPE, DISP_UNRECONCILED, DISP_STRUCTURAL):
                    session_owners.setdefault(sid_claim or "", "")
                per_file = self._index_process_unit(
                    path, corpus_root=corpus_root, corpus_name=corpus_name,
                    file_type_declared=(None if file_type == "auto" else file_type),
                    extract_metadata=extract_metadata, llm_model=llm_model,
                    repair_backoff=repair_backoff, fast_skip_key=key,
                    disposition=disposition, election_owner=session_owners,
                    single_file=False,
                )
                processed += 1
                status = per_file["status"]
                if per_file.get("skipped_reason") == "duplicate-sessionId":
                    result["skipped"] += 1
                elif status == "indexed":
                    result["indexed"] += 1
                elif status == "updated":
                    result["updated"] += 1
                elif status == "skipped":
                    result["skipped"] += 1
                else:
                    result["failed"] += 1
                kind = per_file.get("sourceKind")
                if kind:
                    result["by_kind"][kind] = result["by_kind"].get(kind, 0) + 1
                err = per_file.get("error")
                if err:
                    result["errors"].append(err)
                # fast-skip key recording discipline (§5.3): completed-under-
                # pin-(a) units only — failed/lock-held/embedding-unavailable/
                # embedding-NULL-under-True units are NEVER keyed (re-attempted
                # honestly on resume, E2E-10(e3)/(e5)).
                if progress_file is not None and per_file.get("keyed") and key is None:
                    keys[rel] = per_file.get("fast_skip_key")
                elif per_file.get("keyed") and key is not None:
                    keys[rel] = per_file.get("fast_skip_key") or key
                checkpoint_counter += 1
                if progress_file is not None and checkpoint_counter % 100 == 0:
                    self._index_save_progress(
                        progress_file, str(corpus_root), corpus_name,
                        extract_metadata, result, keys)
                # DB-failure bounded abort (E2E-19/§6.4): recoverable failures
                # are per-file failed{retryable:true} up to the connection
                # retry budget; then the run aborts with honest `aborted`.
                if per_file.get("db_failure"):
                    db_streak += 1
                    if db_streak >= _INDEX_DB_RETRY_BUDGET or not proj._probe_ok():
                        aborted_reason = per_file.get("db_reason", "db")
                        break
                else:
                    db_streak = 0
        finally:
            if progress_file is not None:
                self._index_save_progress(
                    progress_file, str(corpus_root), corpus_name,
                    extract_metadata, result, keys)
        if aborted_reason is not None:
            remaining = len(walked.files) - processed
            result["aborted"] = max(remaining, 0)
            result["aborted_reason"] = aborted_reason
        return result

    # ── T3 internal helpers ────────────────────────────────────────────

    def _index_resolve_corpus_root(self, file_path: str, corpus_root: str | None,
                                   corpus_name: str | None) -> tuple:
        """§6.1 I6 pin: index_file's corpus_root default resolution.

        Explicit param → realpath-resolved. Else the nearest ancestor of the
        file that equals TORTOISE_INGEST_BASE_DIR; else ValueError demanding
        an explicit corpus_root (never an implicit walk-root guess). Guarantees
        single-file + directory-sweep entry points derive the SAME corpus://
        url for the same file (E2E-4 cross-entry-point variant).
        """
        import os as _os
        from pathlib import Path
        if corpus_root is not None:
            root = Path(_os.path.realpath(str(corpus_root)))
            # the file must resolve under the declared root (escape policy)
            from .file_indexer import _resolve_rel_path
            _resolve_rel_path(file_path, root)  # raises ValueError on escape
            return root, (corpus_name or root.name)
        base = _os.environ.get("TORTOISE_INGEST_BASE_DIR")
        if base:
            base_real = _os.path.realpath(_os.path.expanduser(base))
            p = Path(_os.path.realpath(str(file_path)))
            while True:
                if str(p) == base_real:
                    return p, (corpus_name or p.name)
                parent = p.parent
                if parent == p:
                    break
                p = parent
            raise ValueError(
                f"index_file: file {file_path!r} is not under "
                f"TORTOISE_INGEST_BASE_DIR ({base_real}) and no corpus_root "
                f"was given — pass corpus_root explicitly (§6.1 I6)."
            )
        raise ValueError(
            f"index_file: corpus_root required — TORTOISE_INGEST_BASE_DIR is "  # noqa: F541
            f"unset and no corpus_root was given (§6.1 I6)."  # noqa: F541
        )

    def _index_max_bytes(self) -> int:
        """Two-layer size-guard threshold (§6.4): env TORTOISE_MAX_FILE_MB as
        float MB (default 50); invalid/garbage → pre-walk ValueError; <= 0 →
        fail-closed ValueError (cycle-11 pin, E2E-7(p))."""
        import os as _os
        raw = _os.environ.get("TORTOISE_MAX_FILE_MB", "50")
        try:
            mb = float(raw)
        except (TypeError, ValueError):
            raise ValueError(
                f"TORTOISE_MAX_FILE_MB must be a float number of MiB, got "
                f"{raw!r}") from None
        if mb <= 0:
            raise ValueError(
                f"TORTOISE_MAX_FILE_MB must be > 0 (got {raw!r}) — a "
                f"0/negative limit silently fails every non-empty file."
            )
        return int(mb * 1024 * 1024)

    def _index_repair_backoff(self, explicit: float | None) -> float:
        """Embedding-repair backoff (hours) precedence (cycle-7/8): explicit
        kwarg > env TORTOISE_EMBEDDING_REPAIR_BACKOFF_HOURS > 24h default."""
        import os as _os
        if explicit is not None:
            return float(explicit)
        raw = _os.environ.get("TORTOISE_EMBEDDING_REPAIR_BACKOFF_HOURS")
        if raw is not None and str(raw).strip():
            try:
                return float(raw)
            except (TypeError, ValueError):
                pass
        return 24.0

    def _index_no_network(self) -> bool:
        import os as _os
        return _os.environ.get("TORTOISE_INDEX_NO_NETWORK", "").strip().lower() in (
            "1", "true", "yes")

    def _index_read_file(self, path, max_bytes: int):
        """Layer-2 BOUNDED BINARY read (§6.4 cycle-4 pin).

        The cap counts BYTES (a text-mode read(n) caps CHARACTERS — a
        TOCTOU-grown multibyte file could allocate ~4× the cap); the bounded
        buffer is incrementally UTF-8-decoded with universal-newline
        translation and hashed as TEXT. Returns (text, error_reason|None):
        over-limit → (None, 'size'); truncation mid-multibyte-sequence →
        decode failure → (None, 'decode') — never a truncated-buffer hash
        reaching hash_text. This is the SINGLE read (pin c): the returned
        buffer is BOTH hashed and parsed.
        """
        with open(path, "rb") as f:
            buf = f.read(max_bytes + 1)
        if len(buf) > max_bytes:
            return None, "size"
        try:
            text = buf.decode("utf-8")
        except UnicodeDecodeError:
            return None, "decode"
        # universal-newline translation (matches compute_file_hash's text-mode
        # read — the canonical hash is CRLF-immune, §4.5).
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        return text, None

    def _index_gate_read(self, url: str, event_id: str | None,
                         doc_id: str | None) -> dict:
        """Fast-path ROUTER read (NEVER the counter authority, §5.1 pin b).

        Returns source existence/hash/version + unit completeness (Source AND
        Event/Document AND references edge — the CURRENT derived identity,
        §5.1 pin (a)) + embedding/marker state for the embedding clause.
        """
        proj = self._get_proj()
        g = {"source": False, "hash": None, "version": None,
             "entity": False, "edge": False, "embedding": None,
             "marker": None, "stored_source_file": None}
        rows = proj.g.query(
            "MATCH (s:Source {url:$url}) RETURN s.contentHash, s.version",
            params={"url": url},
        ).result_set
        if rows:
            g["source"] = True
            g["hash"] = rows[0][0]
            g["version"] = rows[0][1]
        if event_id:
            rows = proj.g.query(
                "MATCH (e:Event {eventId:$eid}) RETURN e.embedding, "
                "e.embeddingRepairFailedAt, e.source_file",
                params={"eid": event_id},
            ).result_set
            if rows:
                g["entity"] = True
                g["embedding"] = rows[0][0]
                g["marker"] = rows[0][1]
                g["stored_source_file"] = rows[0][2]
        elif doc_id:
            rows = proj.g.query(
                "MATCH (d:Document {id:$did}) RETURN 1",
                params={"did": doc_id},
            ).result_set
            g["entity"] = bool(rows)
        rows = proj.g.query(
            "MATCH (s:Source {url:$url})-[:references]->(n) RETURN count(n)",
            params={"url": url},
        ).result_set
        g["edge"] = bool(rows and rows[0][0])
        return g

    def _index_process_unit(self, path, *, corpus_root, corpus_name,
                            file_type_declared, extract_metadata, llm_model,
                            repair_backoff, fast_skip_key, disposition,
                            election_owner, single_file: bool) -> dict:
        """The per-file unit op — the whole W1 pipeline for ONE file.

        Returns a per-file result dict consumed by index_file/index_directory:
        {status, reason?, retryable?, url?, eventId?, documentId?, sourceKind?,
         skipped_reason?, error? (errors[] entry), keyed? (fast-skip
         eligibility), fast_skip_key? (dict), db_failure?, db_reason?}
        """
        import os as _os  # noqa: I001
        import time as _time  # noqa: F401
        from pathlib import Path
        from .file_indexer import (
            hash_text, parse_frontmatter, classify_file, derive_source_url,
            derive_session_id, derive_meeting_event_id, derive_document_id,
            source_kind_for_classifier, normalize_source_date,
        )
        out = {"status": "failed", "keyed": False, "db_failure": False}
        proj = self._get_proj()
        rel = _os.path.relpath(str(path), str(corpus_root))
        abs_path = str(path)
        # realpath-relativized stored form (§4.2 cycle-16 form pin — the
        # meeting guard's canonical source_file and the session #320 rel-path
        # convention family).
        try:
            sf_rel = _os.path.relpath(_os.path.realpath(abs_path),
                                      _os.path.realpath(str(corpus_root)))
        except ValueError:
            sf_rel = rel
        url = None

        def _fail(error: str, *, retryable: bool, cause: str) -> dict:
            out["error"] = {"file": rel, "error": error,
                             "retryable": retryable, "cause": cause}
            out["reason"] = error
            out["retryable"] = retryable
            return out

        def _skip(reason: str, *, retryable: bool = False,
                  detail: str = "") -> dict:
            out["status"] = "skipped"
            out["skipped_reason"] = reason
            out["reason"] = reason if not detail else f"{reason} ({detail})"
            out["retryable"] = retryable
            if retryable:
                out["error"] = {"file": rel, "error": out["reason"],
                                 "retryable": True, "cause": "lock"}
            return out

        # ── pre-write dispositions (computed on the sorted walk list) ──
        if disposition is not None:
            if disposition == "symlink-duplicate":
                return _skip("symlink-duplicate")
            if disposition == "inode-duplicate":
                out["error"] = {"file": rel,
                                 "error": "inode-duplicate: mount/firmlink "
                                           "alias of an indexed path — "
                                           "deduped to ONE Source per "
                                           "physical file (W4 mount row)",
                                 "retryable": False, "cause": "structural"}
                return _skip("inode-duplicate")
            if disposition == "escape":
                return _fail("escape rejected: path/mount resolves outside "
                             "the corpus root — never read",
                             retryable=False, cause="escape")
            if disposition == "unreconciled":
                return _fail("hardlink alias cannot be proven root-local "
                             "(st_nlink > in-walk count) — stat-only "
                             "rejection, never read (W4 hardlink row)",
                             retryable=False, cause="escape")
            if disposition == "structural":
                return _fail("non-regular file (FIFO/socket/dir-named-.md) "
                             "— S_ISREG check failed before any open "
                             "(E2E-7(w))", retryable=False, cause="structural")

        # ── resume fast-skip (cycle-4): stat (size, mtime) match → skipped
        # WITHOUT open/read/hash — the checkpoint avoids a full-corpus re-hash.
        if fast_skip_key is not None:
            try:
                st = _os.lstat(abs_path)
                if (st.st_size == fast_skip_key.get("size")
                        and st.st_mtime == fast_skip_key.get("mtime")):
                    out["status"] = "skipped"
                    out["skipped_reason"] = "unchanged"
                    out["reason"] = "unchanged"
                    out["keyed"] = True
                    out["fast_skip_key"] = fast_skip_key
                    return out
            except OSError:
                pass  # vanished since the checkpoint → full fast path

        # ── pre-read stat: S_ISREG + layer-1 size guard (before any open) ──
        # os.stat (FOLLOWS symlinks): a symlink entry's lstat shows the link
        # itself (S_IFLNK) — the type/size guard must see the RESOLVED target
        # (symlink disposition/escape already handled pre-write). Broken/loop
        # symlinks raise OSError here → failed structural.
        try:
            st = _os.stat(abs_path)
        except OSError as e:
            if isinstance(e, PermissionError):
                return _fail(f"permission denied: {e}", retryable=True,
                             cause="structural")
            return _fail(f"stat failed: {e}", retryable=False,
                         cause="structural")
        if not stat.S_ISREG(st.st_mode):
            return _fail("non-regular file — S_ISREG check before any open "
                         "(E2E-7(w))".strip(), retryable=False,
                         cause="structural")
        max_bytes = self._index_max_bytes()
        if st.st_size > max_bytes:
            return _fail(
                f"file exceeds size guard ({max_bytes} bytes, "
                f"TORTOISE_MAX_FILE_MB) — rejected before any read (layer-1 "
                f"stat, §6.4)", retryable=False, cause="size")

        # ── single read (pin c): bounded binary read → decode → hash+parse ──
        try:
            text, read_err = self._index_read_file(abs_path, max_bytes)
        except IsADirectoryError:
            return _fail("IsADirectoryError: entry named *.md is a directory",
                         retryable=False, cause="structural")
        except PermissionError as e:
            return _fail(f"permission denied: {e}", retryable=True,
                         cause="structural")
        except OSError as e:
            return _fail(f"open failed: {e}", retryable=False,
                         cause="structural")
        if read_err == "size":
            return _fail(
                f"file grew past the size guard between stat and read "
                f"(layer-2 bounded read, {max_bytes} bytes) — failed closed",
                retryable=False, cause="size")
        if read_err == "decode":
            return _fail("non-UTF-8 content (decode failure) — never hashed",
                         retryable=False, cause="decode")
        content_hash = hash_text(text)
        frontmatter = parse_frontmatter(text)

        # ── classify + identity (T1 OWNS derivation; T3 consumes) ──
        try:
            classifier = classify_file(frontmatter, path, file_type_declared)
            url = derive_source_url(path, corpus_root, corpus_name)
        except (UnicodeEncodeError, ValueError) as e:
            # undecodable-filename guard (cycle-12): derive_source_url's
            # quote() raises UnicodeEncodeError on surrogate filenames — a
            # per-file catch at the ENTRY point, never inside the pure
            # function; the run completes (E2E-7(x)). ValueError from the
            # escape rejection → cause-class `escape` (§6.4 cycle-21).
            if isinstance(e, UnicodeEncodeError):
                return _fail(f"identity derivation failed: {e}",
                             retryable=False, cause="filename")
            if "escape" in str(e):
                return _fail(f"identity derivation failed: {e}",
                             retryable=False, cause="escape")
            raise
        kind = source_kind_for_classifier(classifier)
        out["sourceKind"] = kind
        out["url"] = url
        source_date = None
        # §4.1 per-path sourceDate consumption: session/meeting paths consume
        # startedAt/date/created; the DOC path consumes created/updated ONLY
        # (date/startedAt whitelisted-but-DROPPED — E2E-7(u2)).
        if classifier == "doc":
            source_date = normalize_source_date(
                frontmatter.get("created") or frontmatter.get("updated"))
        else:
            source_date = normalize_source_date(
                frontmatter.get("startedAt") or frontmatter.get("date")
                or frontmatter.get("created"))
        title = str(frontmatter.get("title") or Path(path).stem)

        event_id = None
        doc_id = None
        session_id = None
        if classifier == "agent_session":
            session_id = derive_session_id(frontmatter, Path(path).stem)
            event_id = f"session_{session_id}"
            out["eventId"] = event_id
        elif classifier == "meeting":
            def _sf_lookup(candidate: str) -> str | None:
                rows = proj.g.query(
                    "MATCH (e:Event {eventId:$eid}) RETURN e.source_file",
                    params={"eid": candidate},
                ).result_set
                return rows[0][0] if rows else None
            event_id = derive_meeting_event_id(
                frontmatter, path, _sf_lookup, source_file=sf_rel)
            out["eventId"] = event_id
        else:
            doc_id = derive_document_id(path, corpus_root)
            out["documentId"] = doc_id

        # ── primary election (directory mode) + single-file duplicate rule ──
        # FIRST sorted rel-path owns the Event (W4 row; derived-id collisions
        # included): the walk is sorted and units process in order, so the
        # first unit per session_id claims the Event here — the loop's
        # fast-skip sid_claim (keyed files) uses setdefault, so this claim is
        # idempotent across resume.
        non_primary = False
        if not single_file and session_id is not None and election_owner is not None:
            election_owner.setdefault(session_id, rel)
            owner = election_owner[session_id]
            if owner != rel:
                non_primary = True

        # ── session lock (sessions only; §5.3 acquisition-point pin) ──
        lock = None
        if classifier == "agent_session" and not non_primary:
            from .index_lock import SessionIndexLock
            lock = SessionIndexLock(session_id)
            try:
                status = lock.acquire()
            except (OSError, AttributeError, ImportError) as e:
                lock = None
                return _skip("lock-held", retryable=True,
                             detail=f"lock unavailable: {e}")
            if status == "held":
                lock_detail = str(getattr(lock, "detail", ""))
                lock.release()
                lock = None
                return _skip("lock-held", retryable=True, detail=lock_detail)

        try:
            # ── GATE (fast-path router; completeness per §5.1 pin (a)) ──
            gate = self._index_gate_read(url, event_id, doc_id)
            if non_primary:
                complete = gate["source"]  # election-suppressed: Source-existence ONLY
                base_complete = complete
                hash_unchanged = (gate["hash"] == content_hash)
                embedding_incomplete = False
            else:
                base_complete = bool(gate["source"] and gate["entity"] and gate["edge"])
                hash_unchanged = (gate["hash"] == content_hash)
                # embedding completeness clause (pin (a), cycle-5): for
                # agent_session units under extract_metadata=True, completeness
                # ADDITIONALLY requires e.embedding IS NOT NULL — a session
                # indexed during an outage would otherwise stay None forever
                # (every later run reports skipped and the embedding never
                # heals). Election-suppressed (non-primary) units are
                # Source-existence-only — no Event exists to hold e.embedding.
                embedding_incomplete = bool(
                    classifier == "agent_session" and extract_metadata
                    and gate["entity"] and gate["embedding"] is None)
                complete = base_complete and not embedding_incomplete
            # single-file duplicate-sessionId rule (W4 row; no election context)
            single_dup = False
            if (single_file and classifier == "agent_session"
                    and gate["entity"]
                    and gate["stored_source_file"] is not None
                    and gate["stored_source_file"] != sf_rel):
                single_dup = True

            if (not non_primary and not single_dup and complete
                    and hash_unchanged):
                # ── fast path: conditional MERGE only (skipped expected) ──
                merge_outcome = self._index_source_merge(
                    url, kind, source_date, content_hash, title, abs_path,
                    gate_v=gate["version"])
                if merge_outcome == "updated":
                    out["status"] = "updated"
                    out["reason"] = "updated"
                else:
                    out["status"] = "skipped"
                    out["skipped_reason"] = "unchanged"
                    out["reason"] = "unchanged"
                out["keyed"] = True
                out["fast_skip_key"] = {
                    "size": st.st_size, "mtime": st.st_mtime,
                    "metadata": extract_metadata,
                    "sid": session_id if (session_id and not non_primary) else None,
                }
                return out

            # ── write path: Source (conditional MERGE) → Event/Document → edge ──
            merge_outcome = self._index_source_merge(
                url, kind, source_date, content_hash, title, abs_path,
                gate_v=gate["version"])
            repair_work = False
            embedding_only_incomplete = bool(
                embedding_incomplete and base_complete and hash_unchanged)
            if not non_primary and not single_dup and (not complete or not hash_unchanged):
                if embedding_only_incomplete:
                    # unit otherwise complete — only the embedding clause is
                    # unmet (pin (a)): repair-only path (heal/suppress/marker),
                    # NEVER a metadata re-write.
                    attempt, _ts = self._embedding_repair_attempt(
                        event_id, frontmatter, text, title, repair_backoff,
                        gate["marker"])
                    if attempt == "healed":
                        repair_work = True
                    else:
                        # failed OR suppressed → skipped embedding-unavailable
                        # (a failed repair performs NO unit-completion write →
                        # never updated; marker already recorded inside).
                        out["status"] = "skipped"
                        out["skipped_reason"] = "embedding-unavailable"
                        out["reason"] = "embedding-unavailable"
                        out["retryable"] = True
                        out["error"] = {"file": rel,
                                         "error": "embedding repair failed and is "
                                                   "within the repair-backoff window "
                                                   "— suppressed (zero embedding calls)",
                                         "retryable": True, "cause": "db"}
                        return out
                else:
                    # unit-completion work (repair carve-out, pin (b))
                    if classifier == "agent_session":
                        embed_val = self._session_event_write(
                            frontmatter, text, path, event_id, session_id,
                            content_hash, title, extract_metadata, llm_model,
                            sf_rel, rel)
                        # failed embedding on an EXISTING Event → record the
                        # repair marker (mixed precedence pin: unit work still
                        # reports updated; the marker bounds the NEXT run).
                        if (embed_val is None and extract_metadata
                                and gate["entity"]):
                            self._record_embedding_marker(event_id)
                        repair_work = not base_complete or merge_outcome == "updated"
                    elif classifier == "meeting":
                        resolved_eid, rejected = self._meeting_event_write(
                            frontmatter, path, event_id, content_hash, sf_rel,
                            source_date)
                        event_id = resolved_eid
                        out["eventId"] = event_id
                        if rejected:
                            # REVIEW-FIX P2/P1 (cycle-26): ALL suffix widths
                            # (8/12/16) taken by other-source meetings — never
                            # a silent clobber: no EventRecorded emission, no
                            # edge wiring, an errors[] entry, and the unit is
                            # bucketed failed (the §4.2 mechanism's "never a
                            # silent clobber" guarantee; the journaled
                            # candidate would otherwise replay props onto the
                            # colliding meeting's Event). The caller appends
                            # out["error"] to the run's errors[] (REVIEW-FIX
                            # P1: this unit has NO access to the run result
                            # dict — the earlier fix referenced a phantom
                            # `result` and NameError'd).
                            out["error"] = {
                                "file": str(path),
                                "error": f"meeting eventId {event_id} "
                                         f"collides at all suffix widths "
                                         f"(sha256 [:8]/[:12]/[:16]) — "
                                         f"refusing to clobber; re-name the "
                                         f"file or split the meeting",
                                "retryable": False,
                                "cause": "structural"}
                            out["status"] = "failed"
                            out["reason"] = "meeting-id-collision"
                            out["retryable"] = False
                            return {k: v for k, v in out.items() if k in (
                                "status", "url", "eventId", "documentId",
                                "sourceKind", "reason", "retryable",
                                "skipped_reason", "error")}
                        repair_work = not base_complete or merge_outcome == "updated"
                    else:
                        self._doc_write(frontmatter, doc_id, title, abs_path, url)
                        repair_work = not base_complete or merge_outcome == "updated"
                    # wire (Source)-[:references]->(Event|Document) — plain edge
                    target = event_id if classifier != "doc" else doc_id
                    label = "Event" if classifier != "doc" else "Document"
                    proj.link_source_to_entity(url, target, label)

            # ── embedding repair (sessions; extract_metadata=True) — runs only
            # when the FULL write path just executed but the embedding attempt
            # inside it failed AND the Event pre-existed (the repair-only
            # branch above is NOT re-entered; its failed case already returned).
            embed_skip = False
            if (classifier == "agent_session" and not non_primary
                    and not single_dup and extract_metadata
                    and gate["embedding"] is None and gate["entity"]
                    and not embedding_only_incomplete
                    and not repair_work and merge_outcome in ("skipped", "updated")):
                attempt, _ts = self._embedding_repair_attempt(
                    event_id, frontmatter, text, title, repair_backoff,
                    gate["marker"])
                if attempt == "healed":
                    repair_work = True
                elif attempt == "suppressed":
                    embed_skip = True

            # ── counter attribution ──
            if non_primary:
                out["status"] = "indexed" if merge_outcome == "indexed" else \
                    ("updated" if merge_outcome == "updated" else "skipped")
                if out["status"] == "skipped":
                    out["skipped_reason"] = "unchanged"
                out["keyed"] = True
            elif single_dup:
                # Source update visible via fields/counters; status stays
                # skipped (E2E-14 single-file variant)
                out["status"] = "skipped"
                out["skipped_reason"] = "duplicate-sessionId"
                out["reason"] = ("duplicate sessionId (primary="
                                  f"{gate['stored_source_file']})")
                out["keyed"] = True
            elif embed_skip:
                out["status"] = "skipped"
                out["skipped_reason"] = "embedding-unavailable"
                out["reason"] = "embedding-unavailable"
                out["retryable"] = True
                out["error"] = {"file": rel,
                                 "error": "embedding repair failed and is "
                                           "within the repair-backoff window "
                                           "— suppressed (zero embedding calls)",
                                 "retryable": True, "cause": "db"}
            elif repair_work and merge_outcome == "skipped":
                out["status"] = "updated"
                out["reason"] = "updated (repair work with unchanged hash)"
                out["keyed"] = True
            elif merge_outcome == "indexed":
                out["status"] = "indexed"
                out["reason"] = "indexed"
                # initial index during an embedding outage: warning, NEVER
                # silent (E2E-11(c)); NO marker (the first repair attempt
                # happens on the next extract_metadata=True run — E2E-11(d)).
                if (classifier == "agent_session" and extract_metadata
                        and not gate["entity"]):
                    ev_state = proj.g.query(
                        "MATCH (e:Event {eventId:$eid}) RETURN e.embedding",
                        params={"eid": event_id},
                    ).result_set
                    if not ev_state or ev_state[0][0] is None:
                        out["error"] = {"file": rel,
                                         "error": "indexed with embedding=None "
                                                   "(model unavailable) — will be "
                                                   "repaired on a later "
                                                   "extract_metadata=True run",
                                         "retryable": False, "cause": "db"}
                out["keyed"] = True
            elif merge_outcome == "updated":
                out["status"] = "updated"
                out["reason"] = "updated"
                out["keyed"] = True
            else:
                out["status"] = "skipped"
                out["skipped_reason"] = "unchanged"
                out["reason"] = "unchanged"
                out["keyed"] = True
            # keyed discipline (pin (a)): embedding-NULL under extract_metadata
            # = True is NEVER keyed (resume re-attempts through the gate).
            if (out["status"] in ("indexed", "updated")
                    and classifier == "agent_session" and extract_metadata
                    and event_id is not None):
                ev_state = proj.g.query(
                    "MATCH (e:Event {eventId:$eid}) RETURN e.embedding",
                    params={"eid": event_id},
                ).result_set
                if not ev_state or ev_state[0][0] is None:
                    out["keyed"] = False  # incomplete under pin (a) — never keyed
            if out["keyed"]:
                out["fast_skip_key"] = {
                    "size": st.st_size, "mtime": st.st_mtime,
                    "metadata": extract_metadata,
                    "sid": session_id if (session_id and not non_primary) else None,
                }
            return out
        except Exception as e:  # noqa: BLE001, RUF100
            db_class = _classify_db_failure(e)
            if db_class:
                out["db_failure"] = True
                out["db_reason"] = db_class
                out["status"] = "failed"
                out["retryable"] = True
                out["error"] = {"file": rel,
                                 "error": f"db write failure ({db_class}): {e}",
                                 "retryable": True, "cause": "db"}
            else:
                out["status"] = "failed"
                out["retryable"] = False
                out["error"] = {"file": rel, "error": str(e),
                                 "retryable": False, "cause": "structural"}
            return out
        finally:
            if lock is not None:
                lock.release()

    def _index_source_merge(self, url, kind, source_date, content_hash, title,
                            abs_path, *, gate_v) -> str:
        """The conditional single-statement Source MERGE (via create_source) —
        outcome = COUNTER AUTHORITY (pin b): ON CREATE → 'indexed'; ON MATCH
        hash-diff → 'updated'; ON MATCH hash-equal → 'skipped'. Never from the
        pre-gate read (concurrent runs cannot double-count a file they all
        pre-read as absent).

        CREATED DETECTION: the embedded FalkorDBLite reports ``Nodes created:
        1`` for BOTH of two concurrent same-key MERGEs (server-side
        parallel-executor quirk) — ``nodes_created`` is NOT a race-safe
        discriminator there. The ON CREATE branch therefore records the
        caller's per-run token (``s.__runId``); re-reading it after the merge
        tells us whether THIS run's CREATE fired (token matches ⇒ we created ⇒
        the token is then removed — a crash-stray vanishes on rebuild). On
        bolt:// (server mode) the same mechanism holds (the MERGE is atomic).
        Matched outcomes use the version delta bracketing the merge (bumped ⇒
        the conditional SET fired).
        """
        from .ids import ulid as _ulid
        rid = _ulid()
        lock = _source_merge_lock_for(url)
        with lock:
            # Serialized per-url: the embedded parallel executor cannot give a
            # race-safe creator signal for concurrent same-key MERGEs; the
            # lock makes the single-statement MERGE + detection atomic w.r.t.
            # other index writers (threads leg). bolt:// stats stay honest.
            self.create_source(
                url, kind, sourceDate=source_date, source_path=abs_path,
                contentHash=content_hash, title=title, _searchText=title,
                format="markdown", _merge_run_id=rid)
            proj = self._get_proj()
            rows = proj.g.query(
                "MATCH (s:Source {url:$url}) RETURN s.__runId, s.version",
                params={"url": url},
            ).result_set
            run_id = rows[0][0] if rows else None
            v = int(rows[0][1]) if rows and rows[0][1] is not None else 0
            if run_id == rid:
                # OUR ON CREATE fired — this run created the Source.
                try:  # noqa: SIM105
                    proj.g.query(
                        "MATCH (s:Source {url:$url}) REMOVE s.__runId",
                        params={"url": url},
                    )
                except Exception:  # noqa: BLE001, RUF100
                    pass
                return "indexed"
            if gate_v is None:
                # concurrent create between gate and merge — our ON MATCH either
                # bumped (hash-diff) or not
                return "updated" if v >= 2 else "skipped"
            return "updated" if v > gate_v else "skipped"

    def _session_event_write(self, frontmatter, text, path, event_id, session_id,
                             content_hash, title, extract_metadata, llm_model,
                             sf_rel, rel) -> list | None:
        """Session Event write — raw-MERGE wrapper mirroring legacy #320
        semantics (E2E-10(a) cycle-18 target pin: NOT _upsert_event): locking,
        metadata tiers, embeddings, _connect_issue_objects, INSTANTIATES edges.
        CYCLE-25: sourceKind value agentSession (v3.6 #6); capturedAt = ingest
        time; no is_episodic/story_arc on the index path. Journals EventRecorded
        (contract (a), emit-on-every-write) with the live embedding as the
        replay carrier. Returns the computed embedding (None = unavailable —
        the caller records the repair marker when the Event pre-existed)."""
        import json as _json  # noqa: I001
        from datetime import datetime, timezone
        from pathlib import Path
        from .session_indexer import (
            extract_keywords_from_frontmatter as _kw_fallback,
            extract_metadata as _extract,
        )
        # Optional frontmatter-metadata validation (#1362) — warn-only, gated
        # by TORTOISE_VALIDATE_FRONTMATTER=1 (default OFF). Metadata-write
        # logic below is UNCHANGED — this only reports missing/malformed
        # required session-template fields.
        from .frontmatter_validator import validate_and_warn
        validate_and_warn(frontmatter, kind="session",
                          context=f"session {session_id}")
        proj = self._get_proj()
        now = datetime.now(timezone.utc).isoformat()  # noqa: UP017
        if extract_metadata:
            try:
                metadata = _extract(text, llm_model)
            except Exception:  # noqa: BLE001, RUF100
                metadata = _kw_fallback(text)
        else:
            metadata = _kw_fallback(text)
        name = str(frontmatter.get("title") or Path(path).stem)
        props = {
            "id": event_id,  # ensure Event node has id for edge matching (#122)
            "name": metadata.get("summary", name),
            "eventKind": "AgentSession",
            "session_id": session_id,
            "agent": str(frontmatter.get("agent", "pi")),
            "source_file": sf_rel,
            "file_hash": content_hash,
            "keywords": metadata.get("keywords", []),
            "topics": metadata.get("topics", []),
            "message_count": int(frontmatter.get("message_count", 0) or 0),
            # REVIEW-FIX P2 (cycle-26): startedAt/capturedAt are ONLY set on
            # CREATE — the MERGE's ON MATCH arm preserves the recorded
            # timestamps (a repair re-run of a hash-unchanged unit must not
            # drift capturedAt, pinned = ingest time). The conditional MERGE
            # below branches ON CREATE SET full / ON MATCH SET additive
            # (without startedAt/capturedAt).
            "startedAt": now,
            "capturedAt": now,   # cycle-25(b): capture/ingest transaction time
            "content_metadata": _json.dumps({
                "schema_version": 1,
                "summary": metadata.get("summary", ""),
                "narrative_arc": metadata.get("narrative_arc", []),
                "issues": metadata.get("issues", []),
                "prs": metadata.get("prs", []),
                "critical_decisions": metadata.get("critical_decisions", []),
            }),
            "eventStatus": "completed",
            "classificationLevel": "internal",
            "format": "markdown",
        }
        # embedding — short-circuited to None under extract_metadata=False
        # (I15 pin) or NO_NETWORK; CASE-guarded $embedding is the ONLY node-
        # write surface (never rides $props).
        embedding = None
        if extract_metadata:
            try:
                embedding = self._session_embedding(
                    props["name"], metadata.get("summary", ""),
                    props["keywords"], props["topics"])
            except Exception:  # noqa: BLE001, RUF100
                embedding = None
        # REVIEW-FIX P2 (cycle-26): timestamps preserved on MATCH — the MERGE
        # splits ON CREATE (full props incl. startedAt/capturedAt) vs ON MATCH
        # (additive props WITHOUT the timestamps), so a repair re-run of a
        # hash-unchanged unit never drifts capturedAt (pinned = ingest time).
        # The CASE-guarded embedding clause stays the ONLY embedding write.
        create_props = {k: v for k, v in props.items()
                        if k not in ("startedAt", "capturedAt")}
        proj.g.query(
            "MERGE (e:Event {eventId:$eid}) "
            "ON CREATE SET e += $props, "
            "e.embedding = CASE WHEN $embedding IS NOT NULL THEN vecf32($embedding) "
            "ELSE e.embedding END "
            "ON MATCH SET e += $match_props, "
            "e.embedding = CASE WHEN $embedding IS NOT NULL THEN vecf32($embedding) "
            "ELSE e.embedding END",
            params={"eid": event_id, "props": props,
                    "match_props": create_props, "embedding": embedding},
        )
        self._connect_issue_objects(event_id, metadata)
        # JOURNALING CONTRACT (a): emit-on-every-write; embedding rides the
        # journaled payload (the sanctioned replay carrier, cycle-18/19).
        payload = {k: v for k, v in props.items() if k != "id"}
        self._emit_event("EventRecorded", id=event_id, **{
            **payload, "embedding": embedding, "eventId": event_id,
            "eventKind": "AgentSession"})
        return embedding

    def _record_embedding_marker(self, event_id: str) -> None:
        """Targeted marker SET (pin (a) cycle-7): e.embeddingRepairFailedAt =
        $ts touching NO other Event key (NOT a partial-prop re-write — the
        E2E-11(d) prop-snapshot guard asserts preservation)."""
        from datetime import datetime, timezone
        try:
            proj = self._get_proj()
            proj.g.query(
                "MATCH (e:Event {eventId:$eid}) "
                "SET e.embeddingRepairFailedAt = $ts",
                params={"eid": event_id,
                        "ts": datetime.now(timezone.utc).isoformat()},  # noqa: UP017
            )
        except Exception:  # noqa: BLE001, RUF100
            pass

    def _meeting_event_write(self, frontmatter, path, candidate, content_hash,
                             sf_rel, source_date) -> tuple:
        """Meeting Event write — the meeting-scoped source_file-aware GUARD
        (§4.2 cycle-12/13/17/18): returns (resolved_event_id, guard_rejected).
        The guard-rejected step emits NO EventRecorded; the suffix follow-up
        journals exactly once with the suffixed id (cycle-14/18)."""
        import json as _json
        proj = self._get_proj()
        participants = frontmatter.get("participants") or []
        if isinstance(participants, str):
            participants = [participants]
        participants = [str(p) for p in participants]
        topics = frontmatter.get("topics") or []
        if not isinstance(topics, list):
            topics = [topics]
        topics = [str(t) for t in topics]
        # content_metadata: decisions + absorbed whitelisted non-contract
        # extras (cycle-8 per-path consumption pin — never node props).
        absorbed = {}
        for k in ("sessionId", "session_id", "agent", "created", "updated",
                  "domain"):
            if k in frontmatter and frontmatter[k] is not None:
                absorbed[k] = frontmatter[k]
        content_metadata = {"schema_version": 1,
                            "decisions": frontmatter.get("decisions") or []}
        content_metadata.update(absorbed)
        inner = {
            "id": candidate,
            "eventId": candidate,
            "eventKind": "meeting",
            "title": str(frontmatter.get("title") or Path(path).stem),  # noqa: F821
            "startedAt": source_date or "",
            "topics": topics,
            "participants": participants,
            "content_metadata": _json.dumps(content_metadata, default=str),
            "file_hash": content_hash,
            "source_file": sf_rel,
            "format": "markdown",
            "classificationLevel": "internal",
            "eventStatus": "completed",
        }
        resolved, rejected = proj._upsert_event(
            inner, guard=True, guard_source_file=sf_rel)
        # journal: emit-on-every-write with the RESOLVED id (candidate on hit,
        # suffixed on reject — the suffixed Event is the one that exists live).
        # REVIEW-FIX P2 (cycle-26): when the guard exhausts ALL suffix widths
        # and returns the BARE candidate with rejected=True (the all-widths-
        # taken cell — `resolved == candidate` is the OTHER meeting's id),
        # NO emission: the journal would otherwise replay this file's payload
        # onto the colliding meeting's Event via the plain-replay ON MATCH SET.
        if not (rejected and resolved == candidate):
            payload = {k: v for k, v in {**inner, "eventId": resolved}.items()
                       if k != "id"}
            self._emit_event("EventRecorded", id=resolved, **payload)
        return resolved, rejected

    def _doc_write(self, frontmatter, doc_id, title, abs_path, source_url) -> None:
        """Document path — via the journaled DocumentCreated event (route pin):
        proj.apply honors the source_url override (#205 auto-wire onto the REAL
        Source — no phantom) and the embedding-suppression flag (cycle-19); the
        event rides the JSONL so replay reproduces the edge. Frontmatter→ev-dict
        WHITELIST (cycle-7/8): doc handled set only — title/documentKind/domain/
        doc_status/topics; everything else DROPPED (authoredBy never persisted;
        date/startedAt whitelisted-but-dropped → sourceDate falls to ingestedAt)."""
        # Optional frontmatter-metadata validation (#1362) — warn-only, gated
        # by TORTOISE_VALIDATE_FRONTMATTER=1 (default OFF). The write logic
        # below is UNCHANGED — this only reports missing/malformed required
        # document-template fields on the #900 index path.
        from .frontmatter_validator import validate_and_warn
        validate_and_warn(frontmatter, kind="document",
                          context=f"_doc_write:{abs_path}")
        proj = self._get_proj()
        doc_kind = (str(frontmatter.get("type") or frontmatter.get("documentKind")
                        or frontmatter.get("document_kind") or ""))
        topics = frontmatter.get("topics") or []
        if not isinstance(topics, list):
            topics = [topics]
        topics = [str(t) for t in topics]
        ev = {
            "type": "DocumentCreated",
            "id": doc_id,
            "title": title,
            "document_kind": doc_kind or "brief",  # §8.3 flag 1 fallback
            "doc_status": str(frontmatter.get("doc_status") or "draft"),
            "format": "markdown",
            "source_path": str(abs_path),
            "source_url": source_url,
            "suppress_embedding": True,
            "topics": topics,
        }
        if frontmatter.get("domain") is not None:
            ev["domain"] = str(frontmatter["domain"])
        proj.apply(ev)
        payload = {k: v for k, v in ev.items() if k not in ("type", "id")}
        self._emit_event("DocumentCreated", id=doc_id, **payload)

    def _embedding_repair_attempt(self, event_id, frontmatter, text, title,
                                  repair_backoff, marker) -> tuple:
        """Embedding repair (sessions; §5.1 pin (a) cycle-6/7/8). Returns
        (attempt, marker_ts): 'healed' | 'suppressed' | 'failed'.

        Backoff: a FAILED repair attempt records e.embeddingRepairFailedAt
        (targeted SET touching no other key) and reports skipped
        'embedding-unavailable' — a failed attempt performs NO unit-completion
        write, so it is NEVER updated. Subsequent runs suppress the network
        retry while elapsed <= the backoff window (ZERO embedding calls). On
        success the embedding heals (carve-out updated, no version bump) via a
        JOURNALED EventRecorded emission (survives rebuild) + marker clear.
        """
        import time as _time  # noqa: F401, I001
        from datetime import datetime, timezone
        from .session_indexer import extract_keywords_from_frontmatter as _kw_fallback
        if marker is not None:
            try:
                elapsed_h = (datetime.now(timezone.utc)  # noqa: UP017
                             - datetime.fromisoformat(marker).replace(tzinfo=timezone.utc))  # noqa: UP017
                if elapsed_h.total_seconds() / 3600.0 <= repair_backoff:
                    return ("suppressed", None)
            except (TypeError, ValueError):
                pass  # unparseable marker → re-attempt
        proj = self._get_proj()
        try:
            metadata = _kw_fallback(text)
            embedding = self._session_embedding(
                str(frontmatter.get("title") or title), metadata.get("summary", ""),
                metadata.get("keywords", []), metadata.get("topics", []))
        except Exception:  # noqa: BLE001, RUF100
            embedding = None
        if embedding is None:
            # failed attempt → targeted marker write (BOOKKEEPING, not unit
            # work) — the unit is NEVER updated by this run.
            ts = datetime.now(timezone.utc).isoformat()  # noqa: UP017
            try:  # noqa: SIM105
                proj.g.query(
                    "MATCH (e:Event {eventId:$eid}) "
                    "SET e.embeddingRepairFailedAt = $ts",
                    params={"eid": event_id, "ts": ts},
                )
            except Exception:  # noqa: BLE001, RUF100
                pass
            return ("failed", ts)
        # heal — the CASE-guarded $embedding is the ONLY embedding node-write
        # surface (never $props); the marker clear is a targeted REMOVE.
        proj.g.query(
            "MATCH (e:Event {eventId:$eid}) SET "
            "e.embedding = CASE WHEN $embedding IS NOT NULL THEN "
            "vecf32($embedding) ELSE e.embedding END, "
            "e.embeddingRepairFailedAt = null",
            params={"eid": event_id, "embedding": embedding},
        )
        # journaled EventRecorded emission = the heal write surface (cycle-8):
        # a healed embedding SURVIVES rebuild; only the marker resets.
        rows = proj.g.query(
            "MATCH (e:Event {eventId:$eid}) RETURN properties(e)",
            params={"eid": event_id},
        ).result_set
        if rows:
            props = {k: v for k, v in rows[0][0].items()
                     if k not in ("embedding", "embeddingRepairFailedAt",
                                  "_searchText", "id")}
            self._emit_event("EventRecorded", id=event_id, **{
                **props, "embedding": embedding, "eventId": event_id})
        return ("healed", None)

    def _index_save_progress(self, progress_file: str, directory: str,
                             corpus_name: str, extract_metadata: bool,
                             result: dict, keys: dict) -> None:
        """Atomic checkpoint write (temp+rename — torn-checkpoint guard,
        E2E-10(h)); a write failure degrades to no-checkpoint semantics, never
        crashes (E2E-19(c))."""
        import os as _os
        import tempfile
        from datetime import datetime, timezone
        try:
            payload = {
                "started": datetime.now(timezone.utc).isoformat(),  # noqa: UP017
                "directory": directory,
                "corpus_name": corpus_name,
                "extract_metadata": extract_metadata,
                "counters": {k: result.get(k, 0) for k in (
                    "file_count", "indexed", "updated", "skipped", "failed",
                    "aborted", "ignored")},
                "keys": keys,
            }
            fd, tmp = tempfile.mkstemp(
                dir=_os.path.dirname(_os.path.abspath(progress_file)) or ".",
                prefix=".idx-", suffix=".tmp")
            try:
                with _os.fdopen(fd, "w", encoding="utf-8") as f:
                    _json.dump(payload, f)
                _os.replace(tmp, progress_file)
            except Exception:  # noqa: BLE001, RUF100
                try:  # noqa: SIM105
                    _os.unlink(tmp)
                except OSError:
                    pass
                raise
        except Exception:  # noqa: BLE001, RUF100
            _logger.warning("index progress checkpoint write failed — "
                            "degrading to no-checkpoint (E2E-19(c))")

    def _index_load_progress(self, progress_file: str,
                             directory: str) -> dict:
        """Load fast-skip keys — stale/corrupt checkpoints (g1/g2) and
        directory mismatches → no-checkpoint (full honest re-run)."""
        try:
            with open(progress_file, encoding="utf-8") as f:
                data = _json.load(f)
            if data.get("directory") != directory:
                return {}
            keys = data.get("keys") or {}
            return {k: v for k, v in keys.items() if isinstance(v, dict)}
        except Exception:  # noqa: BLE001, RUF100
            return {}

    def _journal_line_state(self, event_id: str) -> dict | None:
        """Latest journaled EventRecorded payload for event_id (repair-detection
        mechanism, cycle-17/18: re-emit iff NO line OR the recorded state
        differs from the live write)."""
        log = self._get_event_log()
        if log is None:
            return None
        try:
            lines = log.read_all()
        except Exception:  # noqa: BLE001, RUF100
            return None
        state = None
        for ev in lines:
            if (ev.get("type") == "EventRecorded"
                    and (ev.get("id") == event_id
                         or ev.get("eventId") == event_id)):
                state = ev
        return state

    def backfill_sources(self, directory: str | None = None,
                         *, dry_run: bool = False,
                         corpus_name: str | None = None,
                         event_kinds: tuple[str, ...] = ("AgentSession", "DocumentCreated"),
                         ) -> dict:
        """Additive reconciliation for legacy Events of BOTH eventKinds (scope
        item 6; E2E-8 parametrized). Path derivation: AgentSession → Event.source_file;
        DocumentCreated → eventId IS the rel-path (verified sdk.py). Returns —
        PINNED shape split (I27):
          dry_run=True  → {"dry_run": true, "corpus_name": str,
                           "would_create": int, "would_link": int,
                           "degraded_no_file": int, "errors": [...]}
          dry_run=False → {"dry_run": false, "corpus_name": str,
                           "created": int, "linked": int, "skipped": int,
                           "degraded_no_file": int, "errors": [...]}
        `skipped` DEFINED (cycle-4): a legacy Event whose Source ALREADY exists AND
        whose `references` edge ALREADY exists — `skipped` is exclusive per Event
        per run (a FRESH Event counts in BOTH `created` and `linked`; `skipped`
        counts only Events already fully linked — index_file enumerates skip
        reasons; backfill's skipped bucket had no definition until this pin —
        E2E-8's second run asserts it).
        Counters are HONEST in the `db` failure class: a Source-write failure
        NEVER increments `linked` (an edge that was not written is never counted),
        and a re-run REPAIRS the missing edge (`linked`) instead of skipping.
        errors[] entries cover: null-file_hash-with-missing-file (no Source created),
        non-relativizable paths (counted identically in both modes),
        hash-mismatch-since-capture notes, hardlink aliases with NO walk context
        (`st_nlink > 1` → refuse the read, degrade to Event.file_hash when present —
        cycle-5, W2 escape/inode rule), escape-rejected paths (derive_source_url's
        §4.2 hard error caught PER FILE — errors[] entry, no Source, never an
        aborting raise; E2E-8(e)), and mount-alias same-inode pairs (cycle-6
        sibling-Event same-inode scan — alias Events converge onto one Source,
        note names the pair; E2E-8(f)). Over-limit files (size guard, §6.4) are
        NEVER read by backfill — they degrade to Event.file_hash (errors[] note +
        degraded_no_file count, cycle-4), so backfill can never write a Source whose
        hash the forward indexer would refuse (the OOM class stays closed at
        4,190-file scale). Edge states: E2E-8 variants.
        """
        import os as _os  # noqa: I001
        from pathlib import Path
        from .file_indexer import compute_file_hash, derive_source_url, hash_text

        if directory is None:
            from .session_indexer import session_corpus_dir
            directory = str(session_corpus_dir())
        if not isinstance(directory, str) or not directory:
            raise ValueError("backfill_sources: directory must be a non-empty string")
        dir_path = Path(directory)
        if not dir_path.is_dir():
            raise ValueError(
                f"backfill_sources: directory {directory!r} does not exist"
            )
        root_real = _os.path.realpath(str(dir_path))
        name = corpus_name or Path(root_real).name
        # Two-layer size-guard inheritance (§6.4/W2): backfill NEVER reads a file
        # beyond the shared limit (an unguarded compute_file_hash would re-enter
        # the OOM class at 4,190-file scale) — over-limit files degrade to
        # Event.file_hash.
        max_bytes = self._index_max_bytes()

        proj = self._get_proj()
        rows = proj.g.query(
            "MATCH (e:Event) WHERE e.eventKind IN $kinds "
            "RETURN e.eventId, e.eventKind, e.source_file, e.file_hash, e.title",
            params={"kinds": list(event_kinds)},
        ).result_set

        report: dict = {"dry_run": bool(dry_run), "corpus_name": name}
        if dry_run:
            report.update(would_create=0, would_link=0,
                          degraded_no_file=0, errors=[])
        else:
            report.update(created=0, linked=0, skipped=0,
                          degraded_no_file=0, errors=[])
        errors = report["errors"]

        # ── per-Event analysis: path derivation (per eventKind) + url ──
        units: list[dict] = []
        for r in rows:
            eid, ekind, source_file, file_hash, title = r
            u = {"eid": eid, "kind": ekind, "title": title,
                 "file_hash": file_hash, "url": None, "abs_path": None,
                 "content_hash": None, "degrade": False,
                 "inode": None, "error": None, "note": None}
            if eid is None or str(eid) == "":
                u["error"] = {"event": None, "eventKind": ekind,
                              "error": "Event has no eventId — no path derivable",
                              "cause": "structural"}
                units.append(u)
                continue
            kind_name = _BACKFILL_SOURCE_KIND.get(ekind)
            if kind_name is None:
                # unmapped eventKind (custom event_kinds tuple) — NEVER a silent
                # wrong-sourceKind write (§6.2 mapping-table pin): the unknown
                # kind is reported and skipped, never fatal, never a Source.
                u["error"] = {
                    "event": eid, "eventKind": ekind,
                    "error": f"eventKind {ekind!r} has no Source sourceKind "
                             f"mapping — no Source created",
                    "cause": "structural"}
                units.append(u)
                continue
            u["source_kind"] = kind_name
            if ekind == "AgentSession":
                raw = source_file
                if raw is None or str(raw) == "":
                    # pre-#320 shape (cycle-21): no source_file → structural
                    # error, NO Source, never an aborting raise
                    u["error"] = {
                        "event": eid, "eventKind": ekind,
                        "error": "AgentSession Event has no source_file — "
                                 "pre-#320 shape, no Source created",
                        "cause": "structural"}
                    units.append(u)
                    continue
            else:
                raw = eid
            try:
                p = Path(str(raw))
                candidate = p if p.is_absolute() else (dir_path / p)
                u["url"] = derive_source_url(candidate, dir_path, corpus_name=name)
                u["abs_path"] = str(candidate)
            except UnicodeEncodeError:
                # (x) undecodable source_file class (cycle-12) — errors[] entry,
                # run continues, never an aborting raise (the shared per-file
                # guard at the entry points)
                u["error"] = {
                    "event": eid, "eventKind": ekind,
                    "error": f"source_file {str(raw)!r} is not decodable — "
                             f"no Source created",
                    "cause": "filename", "path": str(raw)}
                units.append(u)
                continue
            except (ValueError, OSError) as exc:
                # escape / non-relativizable path (E2E-8 variant (b)) — counted
                # IDENTICALLY in dry-run and real run (would_create excludes it)
                u["error"] = {
                    "event": eid, "eventKind": ekind,
                    "error": f"source_file {str(raw)!r} not relativizable under "
                             f"corpus root {directory!r} — {exc}",
                    "cause": "escape", "path": str(raw)}
                units.append(u)
                continue
            units.append(u)

        # ── file-state pass (reads allowed only past the fail-closed rules) ──
        for u in units:
            if u["url"] is None:
                continue
            eid, ekind = u["eid"], u["kind"]
            fh = u["file_hash"]
            hash_s = str(fh) if fh is not None and str(fh) != "" else None
            try:
                cand = Path(u["abs_path"])
                if not cand.is_file():
                    if hash_s is None:
                        u["error"] = {
                            "event": eid, "eventKind": ekind,
                            "error": "source file missing and file_hash null — "
                                     "no REQUIRED-compliant Source possible",
                            "cause": "structural"}
                    else:
                        # MOVED-vs-DELETED distinction (W2 moved-file divergence
                        # class, cycle-14/15): cross-check the stored file_hash
                        # against every OTHER file in the corpus — a hash MATCH
                        # at a different rel-path indicates a likely MOVE (the
                        # file exists, just elsewhere); no match → genuine
                        # deletion. Both degrade to the stored hash at the OLD
                        # url (additive-only); the wording distinguishes the
                        # classes so F-900-1's report can count the fork.
                        moved_target = None
                        if hash_s:
                            # the file at this path is absent; cross-check the
                            # stored hash against every OTHER file in the corpus.
                            # A match at a DIFFERENT rel-path that is NOT claimed
                            # by another legacy Event in this same run (cycle-8
                            # scan-scope: hardlink siblings / mount-alias pairs
                            # are covered by their own rows and must NOT be
                            # misread as moves — the hardlink-pair mixed leg's
                            # surviving sibling matches the deleted member's
                            # hash, which is a sibling, not a move) indicates a
                            # likely MOVE.
                            claimed = {u2.get("abs_path") for u2 in units
                                       if u2.get("abs_path") is not None}
                            try:
                                for other in dir_path.rglob("*"):
                                    if not other.is_file() or str(other) == u["abs_path"]:
                                        continue
                                    if str(other) in claimed:
                                        continue  # sibling/alias, not a move
                                    try:
                                        if other.stat().st_size > max_bytes:
                                            continue
                                    except OSError:
                                        continue
                                    try:
                                        if compute_file_hash(str(other)) == hash_s:
                                            moved_target = str(other)
                                            break
                                    except Exception:  # noqa: BLE001, RUF100
                                        continue
                            except OSError:
                                pass
                        u["degrade"] = True
                        u["content_hash"] = hash_s
                        if moved_target is not None:
                            u["note"] = {
                                "event": eid, "eventKind": ekind,
                                "error": f"source file moved since capture — "
                                         f"identical content found at "
                                         f"{moved_target!r}; Source built from "
                                         f"stored file_hash at the OLD url "
                                         f"(degraded; accept-and-document "
                                         f"divergence, W2 moved-file row)",
                                "cause": "moved", "path": u["abs_path"]}
                        else:
                            u["note"] = {
                                "event": eid, "eventKind": ekind,
                                "error": "source file deleted since capture — "
                                         "Source built from stored file_hash "
                                         "(degraded)",
                                "cause": "missing", "path": u["abs_path"]}
                    continue
                st = cand.stat()
                if st.st_nlink > 1:
                    # escape/inode fail-closed — NO walk context (cycle-5): a
                    # hardlink entry whose aliases cannot be proven root-local is
                    # NEVER read; degrade to the stored hash when present
                    if hash_s is None:
                        u["error"] = {
                            "event": eid, "eventKind": ekind,
                            "error": "source file hardlinked (st_nlink>1) and "
                                     "file_hash null — no REQUIRED-compliant "
                                     "Source possible",
                            "cause": "inode", "path": u["abs_path"]}
                    else:
                        u["degrade"] = True
                        u["content_hash"] = hash_s
                        u["note"] = {
                            "event": eid, "eventKind": ekind,
                            "error": "source file hardlinked (st_nlink>1) — "
                                     "never read without walk context; Source "
                                     "built from stored file_hash (degraded)",
                            "cause": "inode", "path": u["abs_path"]}
                    continue
                if st.st_size > max_bytes:
                    # two-layer size-guard inheritance (§6.4/W2) — never read
                    if hash_s is None:
                        u["error"] = {
                            "event": eid, "eventKind": ekind,
                            "error": "source file over max_file_mb size guard and "
                                     "file_hash null — no REQUIRED-compliant "
                                     "Source possible",
                            "cause": "size", "path": u["abs_path"]}
                    else:
                        u["degrade"] = True
                        u["content_hash"] = hash_s
                        u["note"] = {
                            "event": eid, "eventKind": ekind,
                            "error": "source file over max_file_mb size guard — "
                                     "never read; Source built from stored "
                                     "file_hash (degraded)",
                            "cause": "size", "path": u["abs_path"]}
                    continue
                # layer-2 bounded read (T6 inherits the §6.4 two-layer guard: a
                # file that GREW past max_bytes between the stat and the read is
                # never read unbounded — it degrades to the stored hash, keeping
                # the OOM class closed at 4,190-file scale; hash_text on the
                # universal-newline-normalized buffer == compute_file_hash)
                text, read_err = self._index_read_file(u["abs_path"], max_bytes)
                if read_err == "size":
                    if hash_s is None:
                        u["error"] = {
                            "event": eid, "eventKind": ekind,
                            "error": "source file grew past max_file_mb between "
                                     "stat and read (layer-2) and file_hash null "
                                     "— no REQUIRED-compliant Source possible",
                            "cause": "size", "path": u["abs_path"]}
                    else:
                        u["degrade"] = True
                        u["content_hash"] = hash_s
                        u["note"] = {
                            "event": eid, "eventKind": ekind,
                            "error": "source file grew past max_file_mb between "
                                     "stat and read (layer-2) — Source built "
                                     "from stored file_hash (degraded)",
                            "cause": "size", "path": u["abs_path"]}
                    continue
                if read_err == "decode":
                    if hash_s is None:
                        u["error"] = {
                            "event": eid, "eventKind": ekind,
                            "error": "source file unreadable and file_hash null — "
                                     "no REQUIRED-compliant Source possible",
                            "cause": "decode", "path": u["abs_path"]}
                    else:
                        u["degrade"] = True
                        u["content_hash"] = hash_s
                        u["note"] = {
                            "event": eid, "eventKind": ekind,
                            "error": "source file unreadable — Source built from "
                                     "stored file_hash (degraded)",
                            "cause": "decode", "path": u["abs_path"]}
                    continue
                u["content_hash"] = hash_text(text)
                u["inode"] = (st.st_dev, st.st_ino)
                if hash_s is not None and u["content_hash"] != hash_s:
                    # W2 "file edited since capture": Source gets the CURRENT
                    # hash, the legacy Event KEEPS its stored file_hash
                    # (additive-only, SC4) — the mismatch is a report artifact
                    u["note"] = {
                        "event": eid, "eventKind": ekind,
                        "error": "file edited since capture — Source has CURRENT "
                                 "file hash, Event keeps stored file_hash "
                                 "(additive-only)",
                        "cause": "hash-mismatch", "path": u["abs_path"]}
            except OSError as exc:
                u["error"] = {
                    "event": eid, "eventKind": ekind,
                    "error": f"cannot stat source file {u['abs_path']!r}: {exc}",
                    "cause": "structural", "path": u["abs_path"]}

        # ── sibling-Event same-inode convergence (cycle-6): the scan applies ONLY
        # to nlink==1 aliases (the mount-alias class); nlink>1 hardlink pairs are
        # EXCLUDED by the cycle-7 scan-scope pin (they degrade per Event) ──
        groups: dict[tuple[int, int], list[int]] = {}
        for i, u in enumerate(units):
            if u["inode"] is not None:
                groups.setdefault(u["inode"], []).append(i)
        alias_of: dict[int, int] = {}
        for members in groups.values():
            if len(members) < 2:
                continue
            members_sorted = sorted(members, key=lambda i: units[i]["abs_path"])
            winner = members_sorted[0]
            for m in members_sorted[1:]:
                alias_of[m] = winner
            errors.append({
                "event": units[winner]["eid"], "eventKind": units[winner]["kind"],
                "error": "same-inode mount-alias pair — one physical file at "
                         f"{units[winner]['abs_path']!r} and "
                         f"{units[m]['abs_path']!r}; converging on ONE Source "
                         "at the first sorted path",
                "cause": "inode-alias",
            })

        # ── accounting (deterministic order: sorted rel path) ──
        order = sorted(range(len(units)),
                       key=lambda i: (units[i]["abs_path"] or "",
                                      units[i]["eid"] or ""))
        group_created: dict[int, bool] = {}
        for i in order:
            u = units[i]
            if u["error"] is not None:
                errors.append(u["error"])
                continue
            if u["note"] is not None:
                errors.append(u["note"])
            if u["degrade"]:
                report["degraded_no_file"] += 1
            if u["content_hash"] is None or u["url"] is None:
                errors.append({"event": u["eid"], "eventKind": u["kind"],
                               "error": "no content hash derivable — no Source",
                               "cause": "structural"})
                continue
            winner_i = alias_of.get(i, i)
            winner = units[winner_i]
            url = winner["url"]
            eid = u["eid"]
            try:
                source_exists, edge_exists = self._backfill_probe(url, eid)
            except Exception as exc:  # noqa: BLE001, RUF100
                errors.append({"event": eid, "eventKind": u["kind"],
                               "error": f"probe failed: {exc}", "cause": "db",
                               "path": u["abs_path"]})
                continue
            if source_exists and edge_exists:
                if not dry_run:
                    report["skipped"] += 1
                continue
            created_here = False
            if not source_exists and not group_created.get(winner_i, False):
                if dry_run:
                    report["would_create"] += 1
                    group_created[winner_i] = True
                else:
                    try:
                        outcome = self._index_source_merge(
                            url, u["source_kind"],
                            None, winner["content_hash"],
                            str(u["title"] or Path(winner["abs_path"] or eid).stem),
                            winner["abs_path"], gate_v=None)
                        if outcome == "indexed":
                            report["created"] += 1
                        group_created[winner_i] = True
                        created_here = True
                    except Exception as exc:  # noqa: BLE001, RUF100
                        errors.append({"event": eid, "eventKind": u["kind"],
                                       "error": f"Source write failed: {exc}",
                                       "cause": "db", "path": u["abs_path"]})
                        # outcome UNKNOWN (a journal-append failure can raise
                        # AFTER the graph write committed): do NOT set
                        # group_created — an alias member may retry the create;
                        # the link below is gated on ACTUAL Source existence so
                        # a failed create NEVER counts a phantom link (honest
                        # counters in the db failure class).
            if not edge_exists:
                if dry_run:
                    report["would_link"] += 1
                else:
                    try:
                        # gate the link on ACTUAL Source existence: the normal
                        # path knows it (source_exists or created_here); the
                        # unknown-outcome path (a journal-append failure can
                        # raise AFTER the graph write committed) re-probes
                        # before linking; a create that left NO Source is
                        # covered by the "Source write failed" error already —
                        # never a phantom link count (honest counters).
                        if (source_exists or created_here
                                or self._backfill_probe(url, eid)[0]):
                            self._backfill_link(url, eid)
                            report["linked"] += 1
                    except Exception as exc:  # noqa: BLE001, RUF100
                        errors.append({"event": eid, "eventKind": u["kind"],
                                       "error": f"references-link step failed: {exc}",
                                       "cause": "db", "path": u["abs_path"]})
        return report

    def _backfill_probe(self, url: str, event_id: str) -> tuple[bool, bool]:
        """Source + references-edge existence for backfill accounting.

        Crash-repair semantics (W2 pin): Source existence alone NEVER satisfies
        a legacy Event — a re-run must REPAIR a missing edge (linked), never
        report the Event skipped.
        """
        proj = self._get_proj()
        srows = proj.g.query(
            "MATCH (s:Source {url:$url}) RETURN 1",
            params={"url": url},
        ).result_set
        erows = proj.g.query(
            "MATCH (s:Source {url:$url})-[:references]->(e:Event {eventId:$eid}) "
            "RETURN 1",
            params={"url": url, "eid": event_id},
        ).result_set
        return bool(srows), bool(erows)

    def _backfill_link(self, url: str, event_id: str) -> None:
        """Wire (Source)-[:references]->(Event) — the backfill link step.

        Kept as a SEPARATE method so the kill-between crash-repair test can
        monkeypatch it to raise AFTER the Source write (simulated crash): the
        run catches per-Event into errors[] (per-file isolation, J1) and a
        re-run repairs the missing edge (linked), never skipping the Event just
        because the Source exists.

        The edge MATCHES BY ``eventId`` (NOT ``id``): legacy raw-Cypher Events
        carry no ``id`` property, so the projection's id-based auto-wire would
        silently no-op on them — the edge must bind the legacy node directly.
        """
        proj = self._get_proj()
        proj.g.query(
            "MATCH (s:Source {url:$url}), (e:Event {eventId:$eid}) "
            "MERGE (s)-[:references]->(e)",
            params={"url": url, "eid": event_id},
        )

    def index_sessions(self, directory: str, extract_metadata: bool = True,
                       llm_model: str | None = "gpt-5-mini",
                       progress_file: str | None = None) -> dict:
        """Index session files as AgentSession Events.

        DEPRECATED — use ``index_directory`` (epic #900). Thin wrapper
        around the frozen legacy ``ingest_corpus`` (SC4 — behavior
        unchanged); see its docstring for the ``extract_metadata``
        flag-semantics divergence (W3, §6.1).
        """
        return self.ingest_corpus(directory, eventKind="AgentSession",
                                  extract_metadata=extract_metadata,
                                  llm_model=llm_model,
                                  progress_file=progress_file)

    def session_index_health(self, directory: str | None = None) -> dict:
        """Compare session .md files against indexed AgentSession Events.

        #280 item 2 — the ``tortoise doctor`` health surface. Scans the
        canonical corpus (``~/.tortoise/docs/conversations/`` by default,
        ``TORTOISE_SESSION_CORPUS`` override) and matches each file to its
        expected Event by session_id + file_hash.

        Returns ``{directory, file_count, indexed_events, matched, unindexed,
        stale, up_to_date, duplicates}`` — ``stale`` = Event exists but hash
        differs (re-index needed); ``unindexed`` = no Event at all.

        ``indexed_events`` is CORPUS-SCOPED (#280 review P3): the count of
        AgentSession Events whose eventId matches a corpus file — not all
        AgentSession Events in the graph (other sessions would make the
        doctor arithmetic misleading). ``duplicates`` surfaces sessionIds
        claimed by more than one corpus file (rglob copies / duplicated
        frontmatter): those copies made the sweep permanently non-convergent
        (MERGE is last-writer-wins) — they are surfaced here and skipped in
        ingest_corpus, never silently re-indexed (#280 review P2).
        """
        from pathlib import Path  # noqa: I001
        from .file_indexer import compute_file_hash
        from .session_indexer import (
            extract_session_id, session_corpus_dir,
        )

        dir_path = Path(directory or session_corpus_dir())
        if not dir_path.is_dir():
            return {"directory": str(dir_path), "file_count": 0,
                    "indexed_events": 0, "matched": 0,
                    "unindexed": [], "stale": [], "up_to_date": [],
                    "duplicates": []}

        proj = self._get_proj()
        rows = proj.g.query(
            "MATCH (e:Event {eventKind:'AgentSession'}) "
            "RETURN e.eventId, e.file_hash"
        ).result_set
        by_event = {r[0]: r[1] for r in rows}

        files = sorted(dir_path.rglob("*.md"))
        # Group by session id: two files may share a sessionId (rglob picking
        # up copies, or duplicated frontmatter). Classify only the PRIMARY
        # file (first in sorted order) so the delta drives the sweep to
        # convergence; non-primary copies surface in the `duplicates` bucket.
        by_sid: dict[str, list[str]] = {}
        for f in files:
            # str() coercion keeps the value hashable as a dict key (a YAML
            # list/dict frontmatter sessionId would raise TypeError) while
            # preserving the base event_id derivation (str() == repr() for
            # str/int/float/bool/list/dict), so health stays consistent with
            # ingest_corpus's frontmatter coercion. Only a read failure
            # (extract returns None) falls back to the file-stem id.
            _sid_raw = extract_session_id(str(f))
            sid = str(_sid_raw) if _sid_raw is not None else f"file_{f.stem}"
            by_sid.setdefault(sid, []).append(str(f))
        unindexed: list[str] = []
        stale: list[str] = []
        up_to_date: list[str] = []
        duplicates: list[dict] = []
        for sid, flist in by_sid.items():
            event_id = f"session_{sid}"
            if len(flist) > 1:
                duplicates.append({"session_id": sid, "event_id": event_id,
                                   "files": flist})
            primary = flist[0]
            file_hash = compute_file_hash(primary)
            existing = by_event.get(event_id)
            if existing is None:
                unindexed.append(primary)
            elif existing == file_hash:
                up_to_date.append(primary)
            else:
                stale.append(primary)
        corpus_event_ids = {f"session_{sid}" for sid in by_sid}
        return {"directory": str(dir_path),
                "file_count": len(files),
                "indexed_events": sum(1 for eid in corpus_event_ids
                                       if eid in by_event),
                "matched": len(up_to_date),
                "unindexed": unindexed,
                "stale": stale,
                "up_to_date": up_to_date,
                "duplicates": duplicates}

    def reconcile_sessions(self, directory: str | None = None,
                           extract_metadata: bool = False,
                           llm_model: str | None = "gpt-5-mini") -> dict:
        """Reconciliation sweep (#280 item 3) — scan for unindexed session
        files and re-index them.

        Scan-then-replay: ``session_index_health()`` computes the delta
        (unindexed + hash-stale files), then ``ingest_corpus()`` replays the
        directory — its dedup skips everything up-to-date and the per-session
        flock (#280 item 1) serializes against concurrent hook writers. No
        cron infra needed: the sweep triggers from the same hook/CLI surface
        (align decision) — run it manually via ``tortoise index sessions`` or
        from session-end.sh.

        ``extract_metadata`` defaults to False so sweeps use the cheap
        keyword fallback — never burn LLM tokens on bulk retry.
        """
        from pathlib import Path  # noqa: I001
        from .session_indexer import session_corpus_dir

        directory = str(Path(directory or session_corpus_dir()).resolve())
        health = self.session_index_health(directory)
        result: dict = {}
        if health["unindexed"] or health["stale"]:
            result = self.ingest_corpus(
                directory, eventKind="AgentSession",
                extract_metadata=extract_metadata, llm_model=llm_model,
            )
        return {**health, "reindex": result}


    def _connect_issue_objects(self, event_id: str, metadata: dict) -> int:
        """Create aboutObject edges from an AgentSession Event to issue/PR Objects (ONTOLOGY §3.2).

        The Object node carries its identifying props (``name``, ``objectKind`` and — for
        dict items — ``repo``/``issue_number``/``url``) so the references are resolvable
        outside the edge itself. Only successfully-resolved connections are counted; a
        resolution failure is logged at debug (the session_indexer call site otherwise
        swallows it).
        """
        proj = self._get_proj()
        connected = 0
        for key in ("issues", "prs"):
            for item in metadata.get(key, []) or []:
                if isinstance(item, dict):
                    oid = item.get("id") or item.get("number")
                    name = item.get("title") or item.get("name") or str(item)
                    repo = item.get("repo")
                    try:
                        issue_number = int(item.get("number")) if item.get("number") is not None else None
                    except (TypeError, ValueError):
                        issue_number = item.get("number")
                    url = item.get("url")
                else:
                    oid = None
                    name = str(item)
                    repo = None
                    issue_number = None
                    url = None
                if not oid:
                    # Deterministic hash (builtin hash() is salted per-process →
                    # would create duplicate Objects on every run).
                    oid = f"{key.rstrip('s')}_{hashlib.sha256(name.encode()).hexdigest()[:8]}"
                okind = "pr" if key == "prs" else "issue"
                proj.g.query(
                    "MERGE (o:Object {id:$oid}) SET o.name=$name, o.objectKind=$okind, "
                    "o.repo=$repo, o.issue_number=$issue_number, o.url=$url",
                    params={"oid": oid, "name": name[:200], "okind": okind,
                            "repo": repo, "issue_number": issue_number, "url": url},
                )
                if proj.create_about_edge(event_id, oid, "aboutObject"):
                    connected += 1
                else:
                    _logger.debug(
                        "connect_issue_objects: unresolved aboutObject target %s for event %s",
                        oid, event_id,
                    )
        return connected


    def create_document(self, title: str, documentKind: str, **props) -> dict:
        """Thin alias for create_entity(type='document') — epic #888 W2."""
        _coerce_props(props)  # accept MCP-style nested props= dict (#218)

        did = self.ulid()
        result = self._create_entity("Document", did, {"title": title, "documentKind": documentKind, "objectKind": "document", "status": "draft", **props}, "DocumentCreated")
        # #394: provenance parity with create_point — link Document → Source
        # via extractedFrom (Ontology v3.3) when the caller passes a source ref.
        if props.get("extractedFrom"):
            self._get_proj()._link_source(did, props["extractedFrom"], label="Document")
        return result

    def create_source(self, url: str, sourceKind: str, *,
                      tier: str | None = None, sourceDate: str | None = None,
                      source_path: str | None = None,
                      is_episodic: bool | None = None,
                      _merge_run_id: str | None = None,
                      **props) -> dict:
        """Create (or merge) a Source node (issue #398 Task 6).

        Dual-write rule (ontology v3.1 §4.6 + code reader contract):
          - ``tier`` given → stored on ``credibilityTier`` (the property the
            inheritance adapter reads); ``sourceKind`` left untouched.
          - ``sourceKind`` is itself a tier-form (T0-T4) → mirrored to
            ``credibilityTier`` as well (canonical per ontology).
          - An EXISTING Source (URL collision) NEVER has its ``sourceKind``
            overwritten — tier lands on ``credibilityTier`` only.
        ``sourceDate`` is the evidence-age clock for decay (falls back to
        ``ingestedAt`` — the documented pipeline-arrival proxy). Invalid tier
        values raise ValueError.

        Epic #900 T3 extensions (plan §4.1/§5.1 pins b/d):
          - ``source_path=`` — the SANCTIONED route for the ``sourcePath``
            secondary prop (mirrors the ``api.add_document(source_path=)``
            precedent the sanitizer's docstring carves out): the path is
            pre-validated under the corpus root / TORTOISE_INGEST_BASE_DIR by
            the indexer, and props-passthrough of ``sourcePath``/``source_path``
            stays rejected by ``_sanitize_props`` (fail-closed, #329). The
            write path emits the ev key ``source_path`` → ``s.sourcePath`` on
            the node (camelCase; ``_SOURCE_HANDLED`` membership keeps the
            snake_case key from persisting verbatim).
          - the Source MERGE is CONDITIONAL (single statement, pin b): the ON
            MATCH version/updatedAt/contentHash/title/``_searchText`` bump
            fires ONLY when the stored contentHash differs (a stub Source with
            NULL contentHash is completed). The MERGE outcome (via
            ``proj._source_merge_result`` nodes_created) is the counter
            authority for the index path.
          - JOURNALING CONTRACT (a) (cycle-18): the write path emits a
            SourceCreated JSONL line on EVERY write (emit-on-every-write — a
            create-only cadence would revert updated Sources to create-time
            state post-rebuild); replay re-MERGEs by url and the hash-diff-gated
            bump lands at the live converged value.
        """
        _coerce_props(props)  # accept MCP-style nested props= dict (#218)
        if not url or not url.strip():
            raise ValueError("url must be a non-empty string")
        from tortoise.source_credibility import TIER_PRIORS, canonical_tier
        if tier is not None:
            _orig_tier = tier
            tier = canonical_tier(tier)
            if tier is None:
                raise ValueError(
                    f"Invalid tier {_orig_tier!r} — must be T0..T4 or a legacy alias "
                    f"(gold/high/medium/low/unverified)"
                )
        if sourceKind in TIER_PRIORS and tier is None:
            tier = sourceKind  # tier-form sourceKind mirrors to credibilityTier
        # #329: props passthrough of the server-managed sourcePath keys stays
        # fail-closed — ONLY the sanctioned source_path= keyword route carries
        # it (the sanitizer's docstring carve-out; §4.1). ``id`` overrides are
        # equally server-managed (node identity) — rejected here because
        # ``_create_entity``'s reject_id is bypassed for the sanctioned route.
        for _k in ("sourcePath", "source_path", "id", "is_episodic"):
            if _k in props:
                # #1501: name the actual sanctioned keyword per key (is_episodic
                # became a sanctioned create_source keyword in this change).
                sanctioned = ("source_path" if _k in ("sourcePath", "source_path")
                              else _k)
                raise ValueError(
                    f"{_k!r} is a server-managed field and cannot be set via "
                    f"props — use the sanctioned create_source({sanctioned}=) "
                    f"keyword (epic #900 §4.1)."
                )
        ev = {
            "url": url,
            "sourceKind": sourceKind,
            "ingestedAt": __import__('datetime').datetime.now(
                __import__('datetime').timezone.utc).isoformat(),
            **props,
        }
        if is_episodic is not None:
            # #1488: server-managed quota discriminator — explicit param only
            # (mirrors create_point). The props passthrough is rejected above.
            ev["is_episodic"] = is_episodic
        if source_path is not None:
            ev["source_path"] = str(source_path)
        if tier is not None:
            ev["credibilityTier"] = tier
        if sourceDate is not None:
            ev["sourceDate"] = sourceDate
        if _merge_run_id is not None:
            # internal run token: the race-safe CREATE discriminator for the
            # conditional-MERGE outcome (the embedded backend's nodes_created
            # stats are unreliable under concurrent same-key MERGEs). Rides
            # the ev dict to _upsert_source (popped by apply) and is stripped
            # from the journaled payload below.
            ev["_merge_run_id"] = _merge_run_id
        proj = self._get_proj()
        proj._source_merge_result = None
        result = self._create_entity("Source", url, ev, "SourceCreated",
                                     _skip_sanitize=True)
        # Journaling contract (a): emit-on-every-write SourceCreated. Best-
        # effort (no-op when no event_log_path configured) — never crashes the
        # write (mirrors _emit_event's discipline). The internal run token
        # never rides the journaled payload.
        try:
            payload = {k: v for k, v in ev.items() if k != "_merge_run_id"}
            self._emit_event("SourceCreated", id=url, **payload)
        except Exception:  # noqa: BLE001, RUF100
            _logger.warning("SourceCreated journal emission failed for %s — continuing", url)
        # Write events invalidate the inheritance gate + reliability cache
        self._invalidate_inheritance_gate_for_source(url)
        self._clear_reliability_cache(url)
        return result

    def set_source_tier(self, url: str, tier: str) -> dict:
        """Set (or change) a Source's credibility tier — non-destructive.

        Writes ``credibilityTier`` only; never touches ``sourceKind`` (legacy
        type strings are preserved). Mirrors to ``sourceKind`` when it is
        already a tier-form (keeps the dual-write invariant). Dirty-marks the
        inheritance gate + clears the reliability cache.
        """
        from tortoise.source_credibility import TIER_PRIORS, canonical_tier
        _orig = tier
        tier = canonical_tier(tier)
        if tier is None:
            raise ValueError(
                f"Invalid tier {_orig!r} — must be T0..T4 or a legacy alias "
                f"(gold/high/medium/low/unverified)"
            )
        proj = self._get_proj()
        rows = proj.g.query(
            "MATCH (s:Source {url:$url}) RETURN s.sourceKind",
            params={"url": url},
        ).result_set
        if not rows:
            raise ValueError(f"Source {url} does not exist")
        skind = rows[0][0]
        if skind in TIER_PRIORS:
            # Keep the dual-write invariant: tier-form sourceKind mirrors the tier
            proj.g.query(
                "MATCH (s:Source {url:$url}) SET s.credibilityTier = $t, s.sourceKind = $t",
                params={"url": url, "t": tier},
            )
        else:
            proj.g.query(
                "MATCH (s:Source {url:$url}) SET s.credibilityTier = $t",
                params={"url": url, "t": tier},
            )
        self._invalidate_inheritance_gate_for_source(url)
        self._clear_reliability_cache(url)
        return {"url": url, "credibilityTier": tier, "sourceKind": skind}

    # ── Entity Derivation (#122 Part 2) ──────────────────────────

    def create_derivation(self, src_id: str, dst_id: str) -> dict:
        """Create a wasDerivedFrom edge: (dst)-[:wasDerivedFrom]->(src).

        PROV-O entity derivation — dst was derived from src. Distinct from
        extractedFrom (claim provenance) — wasDerivedFrom is Object→Object
        entity derivation.
        """
        proj = self._get_proj()
        ok = proj.create_edge(dst_id, src_id, "wasDerivedFrom")
        return {"derived": ok, "src": src_id, "dst": dst_id}

    # ── Source reliability (issue #398 Task 4) ──────────────────────

    def _compute_source_prior(self, url: str) -> dict | None:
        """Compute a Source's effective Beta prior + components (single source of truth).

        Used by BOTH the inheritance adapter (per-point base weight) and the
        reliability cache — the two cannot drift. Returns None when the source
        is untiered (no inheritance contribution). Assessment factor is read
        from `pointKind='assessment'` Points (latest per assessor wins by
        createdAt; outdated filtered); until Task 5 lands, factor = 1.0.
        """
        import os  # noqa: I001
        from datetime import datetime, timezone  # noqa: F401
        from tortoise.source_credibility import (
            aggregate_prior,
            assessment_factor,
            resolve_tier,
        )
        proj = self._get_proj()
        rows = proj.g.query(
            "MATCH (s:Source {url:$url}) "
            "RETURN s.credibilityTier, s.sourceKind, s.sourceDate, s.ingestedAt",
            params={"url": url},
        ).result_set
        if not rows:
            return None
        ctier, skind, sdate, ingested = rows[0]
        tier = resolve_tier(ctier, skind)
        if tier is None:
            return None

        # Batched assessment aggregation (latest per (targetSource, assessor),
        # outdated filtered; assessorReputation snapshotted at write time).
        arows = proj.g.query(
            "MATCH (p:Point {pointKind:'assessment'}) "
            "WHERE p.targetSource = $url AND (p.outdated IS NULL OR p.outdated = false) "
            "RETURN p.assessor, p.score, coalesce(p.assessorReputation, 0.5), p.createdAt "
            "ORDER BY p.createdAt",
            params={"url": url},
        ).result_set
        latest: dict[str, tuple[float, float]] = {}
        for assessor, score, rep, _created in arows:
            try:
                latest[assessor] = (float(rep), float(score))
            except (TypeError, ValueError):
                continue
        assessments = list(latest.values())
        factor = assessment_factor(assessments)

        from tortoise.source_credibility import decay_factor as _decay_factor
        alpha, beta = aggregate_prior(
            [(tier, sdate, ingested, factor)],
            recency_decay=float(os.environ.get("TORTOISE_EP_RECENCY_DECAY", "0.95")),
        )
        return {
            "tier": tier,
            "sourceDate": sdate,
            "ingestedAt": ingested,
            "decay": _decay_factor(
                sdate, ingested,
                recency_decay=float(os.environ.get("TORTOISE_EP_RECENCY_DECAY", "0.95")),
                tier=tier,
            ),
            "factor": factor,
            "assessment_count": len(assessments),
            "alpha": alpha,
            "beta": beta,
        }

    def get_source_reliability(self, url: str) -> dict:
        """Derive a Source's reliability (0-1) — query-time, cache-consistency-checked.

        Returns {"url", "reliability" (float 0-1 or None), "components",
        "cache": "fresh"|"recomputed"|"miss"}. The reliability value is the mean
        of the SAME modulated prior EP uses as base weight (single source of
        truth ``_compute_source_prior``), so ``reliability == inherited prior
        mean`` for single-source points (consistency invariant). Untiered +
        unassessed → None (reason 'untiered'). The cache
        (reliability/reliabilityComponents/reliability_derived_at) is a
        documented projection — recomputed when stale (interval elapsed or tier/
        sourceDate changed vs cached components) or after write events
        (set_source_tier/assess_source/create_source(tier=)).
        """
        import os  # noqa: I001
        from datetime import datetime, timezone
        from tortoise.source_credibility import resolve_tier
        proj = self._get_proj()
        now = datetime.now(timezone.utc)  # noqa: UP017
        interval = float(os.environ.get("TORTOISE_EP_REINHERIT_INTERVAL", "3600"))

        # Cache freshness check
        rows = proj.g.query(
            "MATCH (s:Source {url:$url}) "
            "RETURN s.reliability, s.reliabilityComponents, s.reliability_derived_at, "
            "s.credibilityTier, s.sourceKind, s.sourceDate, s.ingestedAt",
            params={"url": url},
        ).result_set
        cached = None
        if rows and rows[0][2]:
            cached_rel, cached_comp_raw, cached_at, c_tier, c_kind, c_sdate, c_ingested = rows[0]  # noqa: RUF059
            fresh = False
            try:
                derived = datetime.fromisoformat(str(cached_at).replace("Z", "+00:00"))
                if derived.tzinfo is None:
                    derived = derived.replace(tzinfo=timezone.utc)  # noqa: UP017
                fresh = (now - derived).total_seconds() < interval
            except (ValueError, TypeError):
                fresh = False
            if fresh:
                try:
                    import json as _json
                    cached_comp = _json.loads(cached_comp_raw) if cached_comp_raw else {}
                    # Inputs unchanged → serve cache (tier, sourceDate, ingestedAt
                    # are the derivation inputs; assessments clear the cache at write)
                    if (cached_comp.get("tier") == resolve_tier(c_tier, c_kind)
                            and cached_comp.get("sourceDate") == c_sdate
                            and cached_comp.get("ingestedAt") == rows[0][5]):
                        cached = (cached_rel, cached_comp)
                except (TypeError, ValueError, KeyError):
                    cached = None

        if cached is not None:
            rel, comp = cached
            return {"url": url, "reliability": rel, "components": comp, "cache": "fresh"}

        # Recompute from the single source of truth
        prior = self._compute_source_prior(url)
        if prior is None:
            # Untiered: assessment-only reliability (display — never feeds EP)
            arows = proj.g.query(
                "MATCH (p:Point {pointKind:'assessment'}) "
                "WHERE p.targetSource = $url AND (p.outdated IS NULL OR p.outdated = false) "
                "RETURN p.assessor, p.score, coalesce(p.assessorReputation, 0.5), p.createdAt "
                "ORDER BY p.createdAt",
                params={"url": url},
            ).result_set
            if arows:
                # Latest per (url, assessor) — mirrors _compute_source_prior dedup
                latest: dict[str, tuple[float, float]] = {}
                for assessor, score, rep, _created in arows:
                    try:
                        latest[assessor] = (float(rep), float(score))
                    except (TypeError, ValueError):
                        continue
                reps = [r for r, _s in latest.values()]
                rep_sum = sum(reps)
                weighted = (sum(r * s for r, s in latest.values()) / rep_sum
                            if rep_sum else 0.0)
                if weighted != weighted:  # NaN guard
                    weighted = 0.0
                comp = {"tier": None, "reason": "untiered; assessment-only",
                        "assessment_count": len(arows),
                        "assessment_weighted_mean": weighted}
                self._write_reliability_cache(url, weighted, comp, now)
                return {"url": url, "reliability": weighted, "components": comp,
                        "cache": "recomputed"}
            comp = {"tier": None, "reason": "untiered", "assessment_count": 0}
            self._write_reliability_cache(url, None, comp, now)
            return {"url": url, "reliability": None, "components": comp, "cache": "miss"}

        mean = prior["alpha"] / (prior["alpha"] + prior["beta"])
        comp = {
            "tier": prior["tier"],
            "sourceDate": prior["sourceDate"],
            "ingestedAt": prior["ingestedAt"],
            "factor": prior["factor"],
            "assessment_count": prior["assessment_count"],
            "decay": prior["decay"],
            "mean": mean,
        }
        self._write_reliability_cache(url, mean, comp, now)
        return {"url": url, "reliability": mean, "components": comp, "cache": "recomputed"}

    def assess_source(self, url: str, assessor: str, score: float, rationale: str) -> dict:
        """Record an agent's assessment of a Source (issue #398 Task 5).

        Creates a ``pointKind='assessment'`` Statement Point (ontology §2 —
        evaluations of subjects are Points with EP confidence, NOT edges),
        property-linked via ``targetSource`` (never ``extractedFrom`` — it is
        evidence ABOUT the source, not extracted FROM it).

        Semantics:
          - score ∈ [0, 1] (non-numeric → ValueError); rationale required.
          - Assessor reputation is SNAPSHOTTED at write time
            (``compute_reputation(assessor).mean``, stored as
            ``assessorReputation``) so later reputation changes never rewrite
            past assessments' factors.
          - Latest-wins per (url, assessor): older assessments from the same
            assessor are marked ``outdated:true`` — the aggregation query picks
            the latest active assessment by construction (crash-safe).
          - The assessment factor is clamped [0.1, 2.0] at the read path.
          - Refreshes the reliability cache + dirty-marks the inheritance gate
            so EP recomputes promptly.
        """
        try:
            score_f = float(score)
        except (TypeError, ValueError):
            raise ValueError(f"score must be numeric, got {score!r}") from None
        if not (0.0 <= score_f <= 1.0):
            raise ValueError(f"score must be in [0, 1], got {score_f}")
        if not rationale or not str(rationale).strip():
            raise ValueError("rationale is required")
        if not assessor or not str(assessor).strip():
            raise ValueError("assessor is required")
        if not url or not str(url).strip():
            raise ValueError("url is required")

        from datetime import datetime, timezone
        rep = self.compute_reputation(str(assessor))["mean"]
        now = datetime.now(timezone.utc).isoformat()  # noqa: UP017

        proj = self._get_proj()
        # Create the new assessment FIRST, then mark older ones outdated EXCLUDING
        # the new point (crash-safe: a failure between the two leaves the new
        # point active and the old one active — the read path dedupes
        # latest-per-(url, assessor) by createdAt, so no double-count; a failure
        # before the mark leaves the previous assessment intact, not orphaned).
        p = self.create_point(
            "assessment", str(rationale).strip(),
            props={
                "targetSource": url,
                "assessor": str(assessor),
                "score": score_f,
                "assessorReputation": rep,
                "createdAt": now,
            },
        )
        proj.g.query(
            "MATCH (p:Point {pointKind:'assessment'}) "
            "WHERE p.targetSource = $url AND p.assessor = $assessor "
            "  AND p.id <> $new_id "
            "  AND (p.outdated IS NULL OR p.outdated = false) "
            "SET p.outdated = true",
            params={"url": url, "assessor": str(assessor), "new_id": p["id"]},
        )
        # Refresh reliability cache (clear → next read recomputes) + dirty-mark
        # the inheritance gate so EP recomputes promptly.
        self._invalidate_inheritance_gate_for_source(url)
        self._clear_reliability_cache(url)
        return {"assessment_point_id": p["id"], "url": url, "assessor": str(assessor),
                "score": score_f, "reputation": rep}

    def _invalidate_inheritance_gate_for_source(self, url: str) -> None:
        """Dirty-mark all points extracted from a source (inheritance recompute)."""
        proj = self._get_proj()
        rows = proj.g.query(
            "MATCH (n:Point)-[:extractedFrom]->(s:Source {url:$url}) "
            "RETURN n.id",
            params={"url": url},
        ).result_set
        pids = [r[0] for r in rows]
        self._invalidate_inheritance_gate(pids)

    def _write_reliability_cache(self, url: str, reliability, components: dict, now) -> None:
        """Write-through reliability projection on the Source node (documented cache)."""
        import json as _json
        proj = self._get_proj()
        proj.g.query(
            "MATCH (s:Source {url:$url}) "
            "SET s.reliability = $r, s.reliabilityComponents = $c, "
            "s.reliability_derived_at = $ts",
            params={"url": url, "r": reliability, "c": _json.dumps(components),
                    "ts": now.isoformat()},
        )

    def _clear_reliability_cache(self, url: str) -> None:
        """Invalidate the reliability cache (next read recomputes from scratch).

        Called by write events that change the derivation inputs: assess_source,
        set_source_tier, create_source(tier=). Prevents indefinite staleness —
        the cache is a documented projection, never authoritative.
        """
        proj = self._get_proj()
        proj.g.query(
            "MATCH (s:Source {url:$url}) "
            "REMOVE s.reliability, s.reliabilityComponents, s.reliability_derived_at",
            params={"url": url},
        )

    # ── Reputation (#122 Part 4) ─────────────────────────────────

    def compute_reputation(self, subject_id: str) -> dict:
        """Derive reputation score for a Subject from event outcomes.

        Traverses: Subject -[:performs]-> Event -[:IMPL|NAND]-> Point
        Aggregates success/failure from direct event outcomes.
        Returns derived score (NOT stored).

        Returns {mean, total_events, impl_count, nand_count, alpha, beta, outcomes}.
        """
        proj = self._get_proj()
        # Try exact id match first, fall back to name if no id match (#152).
        # Prevents merging outcomes from Subject A (id='alice') with Subject B
        # (name='alice') when a subject_id collides with another Subject's name.
        id_check = proj.g.query(
            "MATCH (s:Subject {id: $sid}) RETURN count(s) > 0",
            params={"sid": subject_id},
        ).result_set
        if id_check and id_check[0][0]:  # noqa: SIM108
            match_clause = "s.id = $sid"
        else:
            match_clause = "s.name = $sid"

        # Direct: Event connects directly to claim Points via IMPL/NAND
        # (Operators connect ONLY epistemic targets per ONTOLOGY: Event→Point, Point→Point)
        impl_rows = proj.g.query(
            "MATCH (s:Subject)-[:performs]->(e:Event) "
            "MATCH (e)-[:IMPL]->(p:Point) "
            "WHERE p.is_operator = false "
            "AND (p.outdated IS NULL OR p.outdated = false) "
            "AND e.eventKind <> 'humanApproval' "  # #531: no reputation from own approvals
            f"AND {match_clause} "
            "RETURN p.id, p.content, coalesce(p.confidence, 0.5) AS conf",
            params={"sid": subject_id},
        ).result_set
        nand_rows = proj.g.query(
            "MATCH (s:Subject)-[:performs]->(e:Event) "
            "MATCH (e)-[:NAND]->(p:Point) "
            "WHERE p.is_operator = false "
            "AND (p.outdated IS NULL OR p.outdated = false) "
            "AND e.eventKind <> 'humanApproval' "  # #531: no reputation from own approvals
            f"AND {match_clause} "
            "RETURN p.id, p.content, coalesce(p.confidence, 0.5) AS conf",
            params={"sid": subject_id},
        ).result_set

        # Collect outcomes
        outcomes: list[dict] = []
        for row in impl_rows:
            outcomes.append({"point_id": row[0], "content": row[1], "confidence": float(row[2]), "outcome": "IMPL"})
        for row in nand_rows:
            outcomes.append({"point_id": row[0], "content": row[1], "confidence": float(row[2]), "outcome": "NAND"})

        total = len(outcomes)
        impl_count = sum(1 for o in outcomes if o["outcome"] == "IMPL")
        nand_count = sum(1 for o in outcomes if o["outcome"] == "NAND")

        if total == 0:
            return {"mean": 0.5, "total_events": 0, "impl_count": 0, "nand_count": 0,
                    "alpha": 1.0, "beta": 1.0, "outcomes": []}

        # Simple Beta reputation: IMPL = success, NAND = failure
        # Prior: Beta(1, 1) uniform
        alpha = 1.0 + impl_count
        beta = 1.0 + nand_count
        mean = alpha / (alpha + beta)

        return {
            "mean": round(mean, 4),
            "total_events": total,
            "impl_count": impl_count,
            "nand_count": nand_count,
            "alpha": alpha,
            "beta": beta,
            "outcomes": outcomes[:20],  # cap for readability
        }

    def get_entity(self, id_val: str) -> dict:
        return self._get_entity(id_val)

    def update_entity(self, id_val: str, **props) -> dict:
        """Update any entity's properties. Implementation behind
        update(id, ...) — the consolidated Point/entity update (epic #888 W2)."""
        _coerce_props(props)  # accept MCP-style nested props= dict (#218)
        return self._update_entity(id_val, **props)

    def delete_entity(self, id_val: str) -> bool:
        """Delete any entity by ID. Implementation behind delete(id) — the
        consolidated Point/entity delete (epic #888 W2)."""
        return self._delete_entity(id_val)

    # ── Typed structural edges (epic #888 W2, reification rule v3.5 §8) ──

    def create_edge(self, relation: str, from_id: str, to_id: str) -> dict:
        """Create a typed structural edge (epic #888 W2, PR #912).

        Reification rule (ontology v3.5 §8): structural edges stay PLAIN — no
        operator is created (operator iff mitigation, or Point↔Point
        support/contradict). Lazy promotion: when mitigation becomes needed,
        create the operator via create_operator and mitigate it with
        operator_action(action='mitigate').

        ``relation`` must be one of the typed structural relations:
        performs, produces, uses, authoredBy, ownedBy, managedBy, hasMember,
        holdsRole, memberOf, reportsTo, participatesIn, hasPart, related,
        dependsOn, references, wasDerivedFrom, aboutSubject, aboutObject,
        aboutEvent, aboutDocument, aboutSource, aboutAction
        (``from``/``to`` are Python keywords — mapped to from_id/to_id).

        Returns {edge: {relation, from, to}, created: bool, nudges: [...]}.
        """
        proj = self._get_proj()
        created = proj.create_edge(from_id, to_id, relation)  # validates relation
        return {
            "edge": {"relation": relation, "from": from_id, "to": to_id},
            "created": created,
            "nudges": self._nudge_candidates(
                relation, exclude_ids=[from_id, to_id]),
        }

    # ── Query Helpers ─────────────────────────────────────────────

    def get_owned_entities(self, subject_id: str) -> list:
        """Return all entities owned by a Subject (governance query)."""
        proj = self._get_proj()
        # Issue #327: start from the labeled, indexed Subject (both id and name
        # are RANGE-indexed -> OR uses the index) and traverse ownedBy inward.
        # Narrowing: ownedBy/memberOf targets are canonically Subject (#216);
        # non-Subject targets are out of contract.
        r = proj.g.query(
            "MATCH (s:Subject) WHERE s.id = $sid OR s.name = $sid "
            "MATCH (s)<-[:ownedBy]-(e) RETURN properties(e) LIMIT 100",
            params={"sid": subject_id},
        )
        return [dict(row[0]) for row in r.result_set]

    def get_provenance_chain(self, point_id: str) -> list:
        """Return full provenance chain for a Point."""
        proj = self._get_proj()
        r = proj.g.query(
            "MATCH (p:Point {id:$pid})-[:extractedFrom]->(src:Source)-[:references]->(entity) "
            "RETURN properties(src) as source, properties(entity) as entity, labels(entity) as labels LIMIT 1",
            params={"pid": point_id},
        )
        return [{"source": dict(row[0]), "entity": dict(row[1]), "labels": list(row[2])} for row in r.result_set]

    def link_source_to_entity(self, source_url: str, entity_id: str, entity_label: str, source_kind: str = "document") -> None:
        """Create Source → Entity references edge (Ontology v3.1 §3.4).

        Auto-creates the Source node if it doesn't exist (MERGE + ON CREATE SET)
        so the edge works even when no Point extracted the source yet (#205).

        Args:
            source_url: the Source node's url (auto-created if missing)
            entity_id: the Document/Event/Object node id the source references
            entity_label: the entity label (Document|Event|Object) for the MATCH
            source_kind: sourceKind to set on auto-created Source (default: "document")

        Raises:
            ValueError: if entity_label is not one of Document, Event, Object
                (Action was dissolved in Ontology v3.0).
        """
        proj = self._get_proj()
        proj.link_source_to_entity(source_url, entity_id, entity_label, source_kind)

    def get_org_structure(self, subject_id: str) -> dict:
        """Return organisational structure: members, roles, sub-teams."""
        proj = self._get_proj()
        # Issue #327: labeled Subject start (id|name OR both indexed -> Index
        # Scan) then traverse outward; roles filters the source Subject p.
        members = proj.g.query(
            "MATCH (s:Subject) WHERE s.id = $sid OR s.name = $sid "
            "MATCH (p:Subject)-[:memberOf]->(s) RETURN properties(p)",
            params={"sid": subject_id},
        )
        roles = proj.g.query(
            "MATCH (p:Subject) WHERE p.id = $sid OR p.name = $sid "
            "MATCH (p)-[:holdsRole]->(r:Subject) RETURN properties(r)",
            params={"sid": subject_id},
        )
        return {
            "members": [dict(row[0]) for row in members.result_set],
            "roles": [dict(row[0]) for row in roles.result_set],
        }

    def ulid(self) -> str:
        from .ids import ulid as _ulid
        return _ulid()

    # ── Source Node Completion ────────────────────────────────────

    def complete_source(self, url: str, content: str = None, external_id: str = None) -> dict:  # noqa: RUF013
        """Populate Source node fields: contentHash, version, externalId."""
        import hashlib
        proj = self._get_proj()
        updates = {}
        if content is not None:
            updates["contentHash"] = hashlib.sha256(content.encode()).hexdigest()
        if external_id is not None:
            updates["externalId"] = external_id
        # Increment version
        r = proj.g.query(
            "MATCH (s:Source {url:$url}) "
            "SET s.version = coalesce(s.version, 0) + 1, s.updatedAt = $now "
            "SET s += $updates "
            "RETURN properties(s)",
            params={"url": url, "updates": updates, "now": __import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat()},
        )
        return dict(r.result_set[0][0]) if r.result_set else {}

    # ── Backfill Migration ────────────────────────────────────────

    def backfill_v25(self, dry_run: bool = False) -> dict:
        """Backfill existing tortoise.db to ONTOLOGY v2.5 schema."""
        proj = self._get_proj()
        report = {"dry_run": dry_run, "actions": []}

        # 1. Backfill status on Points
        r = proj.g.query("MATCH (n:Point) WHERE n.status IS NULL RETURN count(n)")
        missing_status = r.result_set[0][0]
        if missing_status > 0:
            report["actions"].append(f"status_backfill: {missing_status} Points")
            if not dry_run:
                proj.g.query(f"MATCH (n:Point) WHERE n.status IS NULL SET n.status = 'live'")  # noqa: F541

        # 2. Backfill pointKind
        r = proj.g.query("MATCH (n:Point) WHERE n.pointKind IS NULL RETURN count(n)")
        missing_kind = r.result_set[0][0]
        if missing_kind > 0:
            report["actions"].append(f"pointKind_backfill: {missing_kind} Points")
            if not dry_run:
                proj.g.query("MATCH (n:Point) WHERE n.pointKind IS NULL SET n.pointKind = 'statement'")

        # 3. Count existing edges
        r = proj.g.query("MATCH ()-[r]->() RETURN count(r)")
        report["edge_count"] = r.result_set[0][0]

        # 4. Verify Point count unchanged
        r = proj.g.query("MATCH (n:Point) RETURN count(n)")
        report["point_count"] = r.result_set[0][0]

        return report

    # ── Gate B Calibration Milestone (epic-264 #779) ───────────────

    _CALIBRATION_MARKER_KEY = "calibration_milestone"
    # Gate B criterion (epic-264 align): ≥70% human-reviewed precision.
    # Enforced at write time — a below-target record would falsely open
    # Gate B via calibration_passed().
    _CALIBRATION_PRECISION_TARGET = 0.70

    def record_calibration(self, *, precision: float | None = None,
                           sample_size: int | None = None,
                           mean_grounding_delta: float | None = None,
                           notes: str | None = None) -> dict:
        """Record the Gate B calibration milestone as a persisted :Meta marker.

        Persists a ``:Meta {key: 'calibration_milestone'}`` node in the graph
        DB (the ``event_fts_v2`` marker pattern, projection/__init__.py) so the
        marker survives restarts and is visible to any SDK instance on the
        same DB. ``calibration_passed()`` reads it.

        The 50-session calibration RUN with ≥70% human-reviewed precision is
        an ops follow-up (issue #779) — this writer exists so the milestone
        can be recorded when that run completes. A ``CalibrationRecorded``
        event is emitted when an event log is configured (#548 best-effort).

        Args:
            precision: measured extraction precision in [0, 1]. REQUIRED
                (review round 2) and ENFORCED at write time — must be ≥ 0.70
                (Gate B criterion); a missing value or a below-target value
                raises ValueError and leaves no marker.
            sample_size: sessions in the human-reviewed sample (> 0).
            mean_grounding_delta: measured pre/post drift. REQUIRED (review
                round 2) and ENFORCED at write time — must be ≤ 0.02
                (``MAX_GROUNDING_DRIFT``, the #785 seam); a missing value or
                an above-ceiling value raises ValueError and leaves no marker.
            notes: free-form ops documentation (e.g. reviewer count, corpus).

        Returns:
            The stored marker properties (key + recordedAt + given fields).
        """
        # Measured metrics are REQUIRED (review round 2): a marker written
        # with no precision/mean_grounding_delta (e.g. notes only) would
        # skip every gate check and flip calibration_passed() True with zero
        # measured evidence — refuse BEFORE any gate check can be bypassed.
        if precision is None or mean_grounding_delta is None:
            raise ValueError(
                "record_calibration requires measured metrics: precision and "
                "mean_grounding_delta are both required (Gate B must not "
                "open without measured evidence)"
            )
        if not 0.0 <= precision <= 1.0:
            raise ValueError(f"precision must be in [0, 1], got {precision}")
        # Gate B criterion enforcement (review round 1): the docstring
        # documents ≥0.70 precision / ≤0.02 drift as binding — enforce them
        # BEFORE the MERGE so a below-target marker cannot open Gate B.
        if precision < self._CALIBRATION_PRECISION_TARGET:
            raise ValueError(
                f"precision {precision} is below the Gate B target of "
                f"{self._CALIBRATION_PRECISION_TARGET:.2f} (≥70% human-reviewed "
                "precision) — refusing to record a calibration milestone that "
                "does not pass the gate"
            )
        if sample_size is not None and sample_size <= 0:
            raise ValueError(f"sample_size must be positive, got {sample_size}")
        # Lazy import: sdk/analyze are mutually imported at call sites, never
        # at module load (analyze.py contract). MAX_GROUNDING_DRIFT is the
        # #785 seam constant, pinned at 0.02 by tests.
        from tortoise.analyze import MAX_GROUNDING_DRIFT
        if mean_grounding_delta > MAX_GROUNDING_DRIFT:
            raise ValueError(
                f"mean_grounding_delta {mean_grounding_delta} exceeds the "
                f"MAX_GROUNDING_DRIFT ceiling of {MAX_GROUNDING_DRIFT} (≤2% "
                "mean absolute drift) — refusing to record a calibration "
                "milestone that does not pass the gate"
            )
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()  # noqa: UP017
        proj = self._get_proj()
        props: dict[str, Any] = {"recordedAt": now}
        # precision / mean_grounding_delta are guaranteed non-None above.
        props["precision"] = precision
        if sample_size is not None:
            props["sample_size"] = sample_size
        props["mean_grounding_delta"] = mean_grounding_delta
        if notes is not None:
            props["notes"] = notes
        proj.g.query(
            "MERGE (m:Meta {key:$key}) SET m += $props RETURN m",
            params={"key": self._CALIBRATION_MARKER_KEY, "props": props},
        )
        self._emit_event("CalibrationRecorded",
                         id=self._CALIBRATION_MARKER_KEY, **props)
        return {"key": self._CALIBRATION_MARKER_KEY, **props}

    def calibration_passed(self) -> bool:
        """True once the Gate B calibration milestone marker is stored.

        Local SDK contract (DE2E-7, epic-264 §6.3): no GitHub dependency —
        the workflow-layer ``check_gates`` helper composes this with #320's
        state. #787 re-uses this reader; it does NOT re-implement it.
        """
        proj = self._get_proj()
        rows = proj.g.query(
            "MATCH (m:Meta {key:$key}) RETURN m.recordedAt",
            params={"key": self._CALIBRATION_MARKER_KEY},
        ).result_set
        return bool(rows)


class _V2SessionMock:
    """Deterministic offline v2 extractor stand-in (TORTOISE_SESSION_LLM_MOCK=1,
    #1350). complete() adapts the v2 pipeline's prompts: S1 → narrative text,
    S2/S4 → the embed-list JSON. Mirrors tests' V2MockModel. Accepts the
    per-call ``max_tokens`` kwarg (M3 #1524 GATE-2 — ignored: deterministic)."""

    def complete(self, *, system: str, user: str,
                 max_tokens: int | None = None) -> str:
        if "STORY SUMMARIZER" in system:
            return "The session revealed a new strategy."
        return ("{\"entities\": [{\"name\": \"the strategy\", "
                "\"kind\": \"core:strategy\", \"lifecycle\": \"created\", "
                "\"supersedes\": null, \"note\": null}], "
                "\"events\": [{\"content\": \"we decided on the new strategy\", "
                "\"eventKind\": \"core:decision\", \"about_entities\": [\"the strategy\"]}], "
                "\"points\": [{\"content\": \"the new strategy is durable\", "
                "\"pointKind\": \"statement\", \"about_entities\": [\"the strategy\"]}], "
                "\"operators\": [], \"chain_notes\": [], \"link_before_create\": []}")


def _default_byok_model():
    """Default BYOK model adapter (env-configured; tests inject mocks)."""
    import os
    name = os.environ.get("TORTOISE_EXTRACT_MODEL", "deepseek/deepseek-v4-flash")
    return _model_adapter(name)


def _model_adapter(model_id: str, max_tokens: int | None = 4000, temperature: float = 0.0):
    """BYOK model adapter with explicit bounds (T13 #1272) + provider routing.

    The production summary/construct workload needs a real output budget —
    the gate-judge default (500) truncates summaries and silently loses
    chunks. 4000 is the summary/construct-sized default (temperature 0.0
    for determinism); callers may override. max_tokens=None means UNCAPPED —
    the cap is omitted from the request body entirely (#1468): the v2 session
    extractor's flash fallback needs the full output budget (capped
    4000-token adapters truncate and lose chunks).

    #1530: the body delegates to the production router
    (tortoise.model_adapters.build_extractor_model) — a RoutingModel over the
    DeepSeek-direct primary / OpenRouter fallback adapters (D7). The signature
    is unchanged so every call site (summary/construct BYOK, _extract_session_v2,
    value-extractor tests) routes through one adapter contract. Builds
    leniently (no-key → single OpenRouter adapter, back-compat); fail-closed
    is enforced at the pipeline gates (D3)."""
    from tortoise.model_adapters import build_extractor_model

    return build_extractor_model(model_id, max_tokens=max_tokens,
                                 temperature=temperature)


def _summary_to_payload(summary: dict, session_id: str,
                       stream: dict | None = None) -> dict:
    """Map the summary to the derived-commit payload. When a constructed
    stream (Step 2 output) is provided, its wired structure (argument points
    with about_entities + IMPL/NAND/MITIGATES operators + decision events) is
    used directly; otherwise the loose mapping is applied."""
    if stream and (stream.get("points") or stream.get("events")):
        return _stream_to_payload(summary, session_id, stream)
    """Map the summary stream to the derived-commit payload (#1013 shape):
    decisions -> events[] (eventKind decision, about_entities = options);
    logic -> points[] (pointKind statement) + IMPL/NAND between logic points;
    state -> entities[]; issues -> events[] (occurrence)."""
    from tortoise.ids import content_hash

    events = []
    for i, d in enumerate((summary.get("decisions") or [])):  # noqa: UP034
        events.append({
            "id": f"ev_{content_hash(f'{session_id}:decision:{i}')[:62]}",
            "eventKind": "decision",
            "content": d.get("content", "")[:1000],
            "confidence": 0.5,   # T11 neutral prior (was fabricated 0.9)
            "about_entities": d.get("options") or [],
            "source_ref": "session.md",
        })
    for i, iss in enumerate((summary.get("issues") or [])):  # noqa: UP034
        events.append({
            "id": f"ev_{content_hash(f'{session_id}:issue:{i}')[:62]}",
            "eventKind": "occurrence",
            "content": f"{iss.get('id', 'issue')} {iss.get('status', '')}: {iss.get('content', '')[:900]}",
            "confidence": 0.5,   # T11 neutral prior (was fabricated 0.9)
            "about_entities": [],
            "source_ref": "session.md",
        })

    points, logic_ids = [], {}
    for i, l in enumerate((summary.get("logic") or [])):  # noqa: B007, E741, UP034
        pid = f"pt_{content_hash(l.get('point', ''))[:62]}"
        logic_ids[l.get("point", "")[:40]] = pid
        points.append({
            "id": pid, "content": l.get("point", "")[:1000],
            "pointKind": "statement", "reason": "NEW",
            "confidence": 0.5, "c_cal": 0.5,   # T11 neutral prior (was 0.8/0.7)
            "about_entities": [], "source_ref": "session.md",
            "quote": "", "status": "draft",   # T11: draft (EP-inert until #785)
        })

    operators = []
    for l in (summary.get("logic") or []):  # noqa: E741
        src = logic_ids.get(l.get("point", "")[:40])
        if not src:
            continue
        if l.get("supports"):
            tgt = logic_ids.get(str(l["supports"])[:40])
            if tgt and tgt != src:
                operators.append({"src": src, "dst": tgt, "op_type": "IMPL",
                                  "direction": "unidirectional"})
        if l.get("opposes"):
            tgt = logic_ids.get(str(l["opposes"])[:40])
            if tgt and tgt != src:
                operators.append({"src": src, "dst": tgt, "op_type": "NAND",
                                  "direction": "unidirectional"})

    entities, seen = [], set()
    for st in (summary.get("state") or []):
        name = st.get("name", "")
        key = (name, st.get("objectKind", "core:concept"))
        if not name or key in seen:
            continue
        seen.add(key)
        entities.append({"name": name, "kind": st.get("objectKind", "core:concept"),
                         "passes_frequency_gate": True})

    return {
        "schema_version": "1", "session_id": session_id,
        "client_commit_id": "", "captured_at": _now_iso(),
        "extractor": {"version": "value@0.3.0+summary", "mode": "byok",
                      "calibration_version": "v1"},
        "summary": (summary.get("session") or {}).get("summary", "")[:2000],
        "story_arc": "",
        "provenance_refs": [{"path": "session.md", "spans": []}],
        "sources": [], "entities": entities, "points": points,
        "events": events, "operators": operators,
        "telemetry": {"extractor": {"version": "value@0.3.0+summary", "mode": "byok"},
                      "model": {"provider": "byok", "id": "user-model", "cfg_hash": ""},
                      "counts": {"kept": len(points), "candidate": len(events),
                                 "segment": 1, "window": 1, "empty_windows": 0},
                      "keep_ratio": None, "dedup_hits": None, "frontier_calls": 1,
                      "llm_cost_usd": None, "extraction_ms": 0, "retry_count": 0,
                      "last_error_code": None, "confidence_histogram": None},
    }


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()  # noqa: UP017


def _post_commit(payload: dict, *, base_url=None, api_key=None) -> dict:
    """POST the derived payload to /v1/sessions/commit (replay-safe)."""
    import os  # noqa: I001
    from tortoise.commit_schema import compute_client_commit_id
    payload["client_commit_id"] = compute_client_commit_id(
        payload["session_id"], payload["points"], payload["entities"],
        payload["operators"], payload["summary"], payload["story_arc"],
        payload.get("events", []), payload.get("supersessions", []))
    base = base_url or os.environ.get("TORTOISE_API_URL", "http://localhost:8000")
    key = api_key or os.environ.get("TORTOISE_API_KEY", "")
    import requests
    r = requests.post(f"{base.rstrip('/')}/v1/sessions/commit",
                      headers={"Authorization": f"Bearer {key}"},
                      json=payload, timeout=120)
    r.raise_for_status()
    return r.json()
