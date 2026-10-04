#!/usr/bin/env python3
"""Launcher for tools/ask_shape_rate.py that makes the reader PIN durable.

(copied into docs/runbook/ask_shape_run.py for the committed evidence)

Imports tortoise.mcp_server BEFORE the instrument's pinned_reader_env runs, so
mcp_server's import-time _load_dotenv() is already spent and cannot re-inject
OPENROUTER_API_KEY from the repo .env after the pin popped it (the #476
failover leg was re-armed that way and hopped question 1de5cff2 to openrouter).

SUPERSEDED by #4582: ``pinned_reader_env`` now narrows the non-pinned provider
keys to the EMPTY STRING (present-but-unkeyed) and keeps TORTOISE_API_URL
popped, so a later ``_load_dotenv()`` can no longer re-arm them and the pin is
durable on its own. The early import below is no longer required — it is kept
only so this launcher still reproduces the pre-fix receipts; it changes nothing
about the measurement.
"""
from __future__ import annotations

import os
import sys

root = os.environ.get("ASK_SHAPE_REPO_ROOT") or os.getcwd()
sys.path.insert(0, root)

# SUPERSEDED by #4582 (see the module docstring): the early mcp_server import was
# load-bearing only while pinned_reader_env POPPED the provider keys. It is
# harmless to keep, and retained for pre-fix reproduction.
# isort: off
from tortoise import mcp_server  # noqa: F401,E402  (import for side effect)
from tools import ask_shape_rate  # noqa: E402
# isort: on

if __name__ == "__main__":
    sys.exit(ask_shape_rate.main())
