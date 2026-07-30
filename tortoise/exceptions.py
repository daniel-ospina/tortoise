"""Tortoise SDK exceptions."""


class CalibrationError(Exception):
    """Raised when require_calibration=True and the graph is uncalibrated."""
    pass
