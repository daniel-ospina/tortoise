"""Tortoise SDK exceptions."""


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
