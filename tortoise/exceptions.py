"""Tortoise SDK exceptions."""
from __future__ import annotations


class CalibrationError(Exception):
    """Raised when require_calibration=True and the graph is uncalibrated."""
    pass


class ControlPlaneError(ValueError):
    """Registry operation failed — duplicate, not found, invalid role, etc."""
    pass


class AuditLogError(RuntimeError):
    """Fatal audit log failure — Postgres unreachable and fallback exhausted."""
    pass


class EmbeddedStoreBusyError(Exception):
    """Another PROCESS already holds this embedded FalkorDBLite store.

    Epic #900 §5.3 cross-process embedded overlap pin (cycle-21): the
    embedded topology is SINGLE-WRITER (#6761); on open, the SDK probes the
    redislite pid-registry (``<db_path>.settings`` + the recorded daemon
    pidfile with a liveness probe) and fails fast with this class when a
    LIVE holder exists — never a silent second daemon (two in-memory copies
    = split-brain; each process's writes land only in its own, last-save-wins).
    Same-process threads reuse the daemon by construction and never trip the
    probe (the holder pid is the caller's own).
    """

    def __init__(self, db_path: str, holder_pid: int):
        self.db_path = db_path
        self.holder_pid = holder_pid
        super().__init__(
            f"Embedded store busy: {db_path!r} is held by a live process "
            f"(pid {holder_pid}). FalkorDBLite is single-writer — run the "
            f"operation from that process, or point TORTOISE_DB_URI at a "
            f"server-mode FalkorDB (bolt://) for concurrent writers."
        )


class BundleValidationError(ValueError):
    """Epic #902 A2 — Phase-1 bundle validation failure (plan §5.2/§6.3).

    Carries ALL violations (not just the first) so agents can fix the whole
    bundle in one pass. ``.violations`` is the list of
    ``{"section", "index", "message"}`` dicts; ``.as_dict()`` returns
    ``{"code": "ERR_BUNDLE_INVALID", "violations": [...]}`` for the MCP wire.
    Subclasses ValueError so pre-A2 callers catching ValueError keep working.
    """

    code = "ERR_BUNDLE_INVALID"

    def __init__(self, violations: list[dict]):
        self.violations = list(violations)
        # The shipped message contract: the str() carries the FIRST
        # violation's message (Phase-2 parity); .violations has them all.
        super().__init__(self.violations[0]["message"] if self.violations else "bundle validation failed")

    def as_dict(self) -> dict:
        # Plan §6.3 pins {error, code: "ERR_BUNDLE_INVALID", violations} —
        # the error key carries the first violation's message (REVIEW-FIX P2).
        return {"error": str(self), "code": self.code,
                "violations": self.violations}


class BudgetExceededError(RuntimeError):
    """Raised when a dream pass exhausts its operator budget (epic 903-C6,
    #1244).

    The SDK-side raise site is the full-mode pass: a full pass is
    contractually complete-in-one-pass (J3), so when an EXPLICIT ``budget``
    is set and the graph requires more operators than the budget, the pass
    fails loudly instead of silently truncating (truncation is only legal
    for stale-first window passes, where deferral is the design — truncated
    claims stay stale and re-enter the staleness ranking next pass).
    ``budget=None`` keeps the existing 200_000-op DoS guard (warn +
    truncate, never raise).

    Carries the budget and the required operator count so hosted wiring
    (epic 903-C8, ``/v1/dream`` 429 + Retry-After per I4) and the MCP layer
    (903-C11, ``ERR_QUOTA``) can build their wire responses from one raise
    site.
    """

    def __init__(self, message: str, budget: int | None = None,
                 required: int | None = None):
        self.budget = budget
        self.required = required
        super().__init__(message)


class DreamNoOpError(RuntimeError):
    """Raised when a dream pass reports success while writing ZERO belief
    state on a graph that has EP factors to propagate (#3139).

    The silent-no-op failure class: the pass's operator/factor selection
    came back empty and the confidence write-back never ran, yet the pass's
    window contains derived-live operators or operator-less direct edges.
    Every mode's result shape reports ``converged``/``converged_all: True``
    and ``budget_used: 0`` in that state — indistinguishable from a
    legitimately empty graph — so the pass fails loudly instead.

    The known producer is a dropped boolean property index: ``GRAPH.COPY``
    drops the ``false`` entries of an ``is_operator`` range index (#3154), so
    ``X.is_operator = false`` filters yield ∅ over a populated graph. The
    dream/EP path uses the index-independent predicate form; this error is
    the fail-closed backstop for any future selection regression.

    A legitimately empty pass (no EP factors in the window) is NOT an error:
    it returns normally and records ``no_op_reason`` on the health surface
    (``dream_health_check``).
    """

    def __init__(self, message: str, mode: str | None = None,
                 eligible_factors: int | None = None):
        self.mode = mode
        self.eligible_factors = eligible_factors
        super().__init__(message)


class Phase2Error(ValueError):
    """Epic #902 A2 — Phase-2 write failure (post-validation, partial state
    may be committed). Carries the bundle's computed batch_id so the agent
    can audit what committed before re-sending (plan §6.4 Phase-2 row,
    cycle-23/24 pin). Surfaces as `{error, batch_id}` with NO code (distinct
    from Phase-1's ERR_BUNDLE_INVALID and quota codes — E2E-15(h))."""

    def __init__(self, message: str, batch_id: str | None = None):
        self.batch_id = batch_id
        super().__init__(message)


# ── Ask-lane typed exceptions (#1987 Task 5) ──────────────────────────────
# The eval-only ask lane (tortoise/ask_lane.py) maps validation/reader/
# retrieval failures to AskValidationError / AskReaderUnavailable /
# AskRetrievalUnavailable;
# each carries a ``code`` class attribute referencing the canonical vocabulary
# constants BELOW (single home — ``tortoise/schemas.py`` re-exports them).
# The wire-body half of that contract is gone: the hosted /v1/ask route and
# its path-scoped translation were removed in #3849.

# Canonical error-code vocabulary (10 codes; SIX are still carried by the
# eval-only lane — reader_unavailable, retrieval_unavailable, and the four
# validation codes invalid_question / invalid_question_type /
# invalid_question_date / question_too_long — and FOUR are the retired wire
# vocabulary with no raiser — unauthorized, quota_exceeded, in_flight_limit,
# timeout).
CODE_UNAUTHORIZED = "unauthorized"
CODE_QUOTA_EXCEEDED = "quota_exceeded"
CODE_IN_FLIGHT_LIMIT = "in_flight_limit"
CODE_READER_UNAVAILABLE = "reader_unavailable"
CODE_RETRIEVAL_UNAVAILABLE = "retrieval_unavailable"
CODE_TIMEOUT = "timeout"
CODE_INVALID_QUESTION = "invalid_question"
CODE_INVALID_QUESTION_TYPE = "invalid_question_type"
CODE_INVALID_QUESTION_DATE = "invalid_question_date"
CODE_QUESTION_TOO_LONG = "question_too_long"


class AskValidationError(ValueError):
    """Client-input validation failure on the eval-only ask lane. Carries the
    canonical ``code`` (``invalid_question``/``question_too_long``/
    ``invalid_question_type``/``invalid_question_date``). The hosted-lane
    400/401/403/422 mappings were removed with the REST surface (#3849)."""

    code = CODE_INVALID_QUESTION

    def __init__(self, message: str, *, code: str | None = None,
                 status_code: int | None = None):
        self.status_code = status_code
        if code is not None:
            self.code = code
        super().__init__(message)


class AskQuotaExceeded(RuntimeError):
    """429 ``quota_exceeded`` — the org's per-minute ask budget is spent.
    Carries ``retry_after`` (seconds) when the server provided one.

    RETIRED (#3849): no raiser. Its only producer was the removed SDK
    ``_post_ask`` status map; the ask budget itself is retained-but-uncalled
    by any product path (tests/test_quota.py still pins `run_ask_bounded`'s
    exec floor) pending the #3849 §7 D5 purge. Kept as vocabulary, not as
    live surface."""

    code = CODE_QUOTA_EXCEEDED

    def __init__(self, message: str, *, retry_after: float | None = None,
                 status_code: int | None = 429):
        self.retry_after = retry_after
        self.status_code = status_code
        super().__init__(message)


class AskInFlightLimit(RuntimeError):
    """429 ``in_flight_limit`` — the per-org in-flight ask cap is full.

    RETIRED (#3849): no raiser. Its only producer was the removed SDK
    ``_post_ask`` status map; the cap machinery is retained-but-uncalled by
    any product path (tests/test_quota.py still pins `run_ask_bounded`'s exec
    floor) pending the #3849 §7 D5 purge. Kept as vocabulary, not as live
    surface."""

    code = CODE_IN_FLIGHT_LIMIT

    def __init__(self, message: str, *, status_code: int | None = 429):
        self.retry_after = None
        self.status_code = status_code
        super().__init__(message)


class AskReaderUnavailable(RuntimeError):
    """502 ``reader_unavailable`` — the LLM reader failed with no surviving
    lane (the ask lane raises it on reader build failure, empty output after
    the bounded retry, or a reader exception).

    The code-less variants the removed SDK ``_post_ask`` client used to map
    here (a code-less 402 provider-billing condition, a code-less 404 for the
    gone hosted ask surface, and the connection-refused ``status_code=None``
    case) have NO raiser since #3849; ``status_code`` is kept for the
    vocabulary."""

    code = CODE_READER_UNAVAILABLE

    def __init__(self, message: str, *, status_code: int | None = 502):
        self.status_code = status_code
        super().__init__(message)


class AskRetrievalUnavailable(RuntimeError):
    """502 ``retrieval_unavailable`` — retrieval/annotation/context
    assembly failed wholesale, or (also raised by the lane) the eval-only
    entry point was handed a hosted client (``TORTOISE_API_URL`` set) and
    refuses it. No HTTP status ships it any more (#3849): the ``status_code``
    default is retained vocabulary."""

    code = CODE_RETRIEVAL_UNAVAILABLE

    def __init__(self, message: str, *, status_code: int | None = 502):
        self.status_code = status_code
        super().__init__(message)


class AskTimeout(RuntimeError):
    """504 ``timeout`` — the bounded ask section exceeded the server's
    ``_ASK_TIMEOUT_S`` (server-504-fired) OR the SDK client-side timeout
    fired (wire connect/read timeout — ``source`` marks which).

    RETIRED (#3849): no raiser. The server-504 half went with the hosted
    /v1/ask route and the client-side half with ``_post_ask`` /
    ``ASK_SDK_TIMEOUT_S`` (both deleted in #3849). Kept as vocabulary, not as
    live surface."""

    code = CODE_TIMEOUT

    def __init__(self, message: str, *, source: str = "server",
                 status_code: int | None = 504):
        self.source = source  # "server" (received 504 body) | "client"
        self.status_code = status_code
        super().__init__(message)


class HybridReadUnavailableError(RuntimeError):
    """(C) #2952 — a read that could not run its vector leg must not be
    labelled a hybrid read.

    Raised by ``tortoise.search_engine.require_hybrid_read`` when a
    real-lane measurement (or any fail-loud consumer) asks a read surface to
    prove it was hybrid and the vector (semantic) leg did not contribute a
    healthy result — including when the surface cannot report its legs at
    all. This is the product-side counterpart of the #2985 / PR #3005 battery
    capability gate: an FTS-only score is a degraded, keyword-only surface
    and must never be recorded as the product's hybrid retrieval.

    ``marker`` is the ``declared_degraded_read`` dict (or the
    ``leg_trace_unavailable`` variant); ``reason`` mirrors its reason.
    """

    def __init__(self, marker: dict | None, *, lane: str | None = None):
        self.marker = dict(marker or {})
        self.lane = lane
        self.reason = self.marker.get("reason")
        lane_part = f" on lane {lane!r}" if lane else ""
        super().__init__(
            f"hybrid read unavailable{lane_part}: the vector (semantic) leg "
            f"did not contribute a healthy result (reason={self.reason!r}) — a "
            f"single-leg (keyword-only) read is NOT the product's hybrid "
            f"retrieval and must not be labelled hybrid (#2952). "
            f"marker={self.marker!r}"
        )


class EmbedderUnavailableError(RuntimeError):
    """(C) #4861 — a process that REQUIRED the embedder must not be handed
    ``None``.

    Raised by ``tortoise.embeddings.EmbeddingModel.get`` when
    ``TORTOISE_EMBEDDING_MODEL_REQUIRED`` is truthy and the model cannot be
    loaded, instead of returning ``None``. This is the **third surface of one
    invariant**: a lane that cannot run hybrid must not run (#2985,
    ``retrieval_preflight.require_hybrid_retrieval``), a read that could not
    run its vector leg must not be labelled hybrid (#2952,
    :class:`HybridReadUnavailableError`) — and this one, at the ``None``
    itself, where neither of the other two can see it. Without it a
    keyword-only (FTS-only) run is indistinguishable from a healthy one: the
    degrade is invisible, which is the defect #2898 describes.

    Unlike :class:`HybridReadUnavailableError` this carries no leg *trace* — a
    load failure has no legs to report, and synthesizing a marker would make
    that class mean two different things. It carries the **cause** instead:
    ``failure_kind`` is ``not_installed`` (the environment never had it — a
    runner-down or missing-extra run) or ``load_failed`` / ``load_timeout``
    (the environment had it and the load broke — a different thing to fix).
    ``model_unavailable`` is the fallback when no kind was recorded.
    """

    def __init__(self, *, failure_kind: str, model: str,
                 revision: str | None = None,
                 last_error: str | None = None,
                 context: str | None = None):
        self.failure_kind = failure_kind
        self.model = model
        self.revision = revision
        self.last_error = last_error
        self.context = context
        where = f" ({context})" if context else ""
        rev = f" @ {revision}" if revision else ""
        err = f"; last error: {last_error}" if last_error else ""
        super().__init__(
            f"embedding model REQUIRED but unavailable{where}: {model}{rev} "
            f"could not be loaded (failure_kind={failure_kind!r}){err} — this "
            f"process set TORTOISE_EMBEDDING_MODEL_REQUIRED, so any retrieval "
            f"result it produced would be keyword-only (FTS) while claiming to "
            f"be the product's hybrid retrieval. Install the embedder "
            f"(uv sync --extra embeddings) or unset the variable to allow the "
            f"documented keyword-only degrade (#4861)."
        )
