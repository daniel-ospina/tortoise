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
