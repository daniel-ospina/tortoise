#!/usr/bin/env python3
"""Launcher for tools/ask_shape_rate.py that makes the reader PIN durable.

(copied into docs/runbook/ask_shape_run.py for the committed evidence)

Imports tortoise.mcp_server BEFORE the instrument's pinned_reader_env runs, so
mcp_server's import-time _load_dotenv() is already spent and cannot re-inject
OPENROUTER_API_KEY from the repo .env after the pin popped it (the #476
failover leg was re-armed that way and hopped question 1de5cff2 to openrouter).
Changes nothing about the measurement.
"""
from __future__ import annotations

import os
import sys

root = os.environ.get("ASK_SHAPE_REPO_ROOT") or os.getcwd()
sys.path.insert(0, root)

# The ORDER of the two imports below is load-bearing, not an accident: mcp_server
# must be imported BEFORE ask_shape_rate so its import-time _load_dotenv() is already
# spent and cannot re-inject OPENROUTER_API_KEY after the reader pin popped it (the
# #476 failover leg was re-armed that way and hopped question 1de5cff2 to openrouter).
# isort: off
from tortoise import mcp_server  # noqa: F401,E402  (import for side effect)
from tools import ask_shape_rate  # noqa: E402
# isort: on

if __name__ == "__main__":
    sys.exit(ask_shape_rate.main())
