"""python -m battery — CLI entry (uv run python -m battery run ...)."""
from __future__ import annotations

import sys

from battery.cli import run_cli

if __name__ == "__main__":
    sys.exit(run_cli())
