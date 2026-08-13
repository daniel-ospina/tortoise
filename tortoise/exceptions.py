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


class Phase2Error(ValueError):
    """Epic #902 A2 — Phase-2 write failure (post-validation, partial state
    may be committed). Carries the bundle's computed batch_id so the agent
    can audit what committed before re-sending (plan §6.4 Phase-2 row,
    cycle-23/24 pin). Surfaces as `{error, batch_id}` with NO code (distinct
    from Phase-1's ERR_BUNDLE_INVALID and quota codes — E2E-15(h))."""

    def __init__(self, message: str, batch_id: str | None = None):
        self.batch_id = batch_id
        super().__init__(message)
