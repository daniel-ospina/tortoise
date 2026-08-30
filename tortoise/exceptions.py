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


class Phase2Error(ValueError):
    """Epic #902 A2 — Phase-2 write failure (post-validation, partial state
    may be committed). Carries the bundle's computed batch_id so the agent
    can audit what committed before re-sending (plan §6.4 Phase-2 row,
    cycle-23/24 pin). Surfaces as `{error, batch_id}` with NO code (distinct
    from Phase-1's ERR_BUNDLE_INVALID and quota codes — E2E-15(h))."""

    def __init__(self, message: str, batch_id: str | None = None):
        self.batch_id = batch_id
        super().__init__(message)


# ── Ask-lane typed SDK exceptions (#1987 Task 5) ───────────────────────────
# The SDK hosted-mode ``_post_ask`` maps server statuses/body codes to these;
# each carries a ``code`` class attribute matching the canonical vocabulary
# constants in ``tortoise/schemas.py`` (one vocabulary, two surfaces).

class AskValidationError(ValueError):
    """Client-input validation failure (local lane) OR a 400/401/403/422
    response with a canonical/code-less body (hosted lane). Carries the
    canonical ``code`` (``invalid_question``/``question_too_long``/
    ``invalid_question_type``/``invalid_question_date``/``unauthorized``)
    and, for hosted mappings, the HTTP status."""

    code = "invalid_question"

    def __init__(self, message: str, *, code: str | None = None,
                 status_code: int | None = None):
        self.status_code = status_code
        if code is not None:
            self.code = code
        super().__init__(message)


class AskQuotaExceeded(RuntimeError):
    """429 ``quota_exceeded`` — the team's per-minute ask budget is spent.
    Carries ``retry_after`` (seconds) when the server provided one."""

    code = "quota_exceeded"

    def __init__(self, message: str, *, retry_after: float | None = None,
                 status_code: int | None = 429):
        self.retry_after = retry_after
        self.status_code = status_code
        super().__init__(message)


class AskInFlightLimit(RuntimeError):
    """429 ``in_flight_limit`` — the per-team in-flight ask cap is full."""

    code = "in_flight_limit"

    def __init__(self, message: str, *, status_code: int | None = 429):
        self.retry_after = None
        self.status_code = status_code
        super().__init__(message)


class AskReaderUnavailable(RuntimeError):
    """502 ``reader_unavailable`` — the LLM reader failed with no surviving
    lane. Also used for the code-less variants that must never be
    mislabeled ``invalid_question``: a code-less 402 (SERVER-side
    provider-billing condition, P2-3), a code-less 404 (hosted ask
    exposure gated off — ``TORTOISE_ENABLE_ASK`` unset, #2013), and the
    pre-existing connection-refused ``status_code=None`` case (hosted ask
    server unreachable)."""

    code = "reader_unavailable"

    def __init__(self, message: str, *, status_code: int | None = 502):
        self.status_code = status_code
        super().__init__(message)


class AskRetrievalUnavailable(RuntimeError):
    """502 ``retrieval_unavailable`` — retrieval/annotation/context
    assembly failed wholesale (never an untyped 500 on the ask surface)."""

    code = "retrieval_unavailable"

    def __init__(self, message: str, *, status_code: int | None = 502):
        self.status_code = status_code
        super().__init__(message)


class AskTimeout(RuntimeError):
    """504 ``timeout`` — the bounded ask section exceeded the server's
    ``_ASK_TIMEOUT_S`` (server-504-fired) OR the SDK client-side timeout
    fired (wire connect/read timeout — ``source`` marks which)."""

    code = "timeout"

    def __init__(self, message: str, *, source: str = "server",
                 status_code: int | None = 504):
        self.source = source  # "server" (received 504 body) | "client"
        self.status_code = status_code
        super().__init__(message)
