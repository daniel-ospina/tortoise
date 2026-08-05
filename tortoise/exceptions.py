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
