"""Tortoise — live epistemic-graph extraction from transcripts.

M0 spike: file → extractor → JSONL event log → projection → static grid render.
See ../BUILD_PLAN.md. This is the spine only; storage, streaming, idempotency,
and the eval loop are later milestones.

GAP-15 / #7003: Conversation mining pipeline (mining.py) now wired.

Import-time loud-fail guard (issue #176, plan Task 8):
  `tortoise.FalkorDB` SUBCLASSES `redislite.falkordb_client.FalkorDB` and
  raises RuntimeError on relative paths. BEST-EFFORT: only code importing
  tortoise's re-export (or importing redislite AFTER `import tortoise`) is
  guarded. Direct redislite imports BEFORE tortoise are documented bypasses —
  the pre-commit grep (Child 3) is the source-level enforcement. We do NOT
  monkeypatch the redislite module globally (non-tortoise users unaffected).

  NOTE: the projection module imports redislite directly at its own
  choke-point (`projection/__init__.py:131`); Python resolves that to the
  ORIGINAL class regardless of this re-export. Protection for the projection
  path comes from FalkorProjection's hard-reject (Task 7) — this guard covers
  code importing `tortoise.FalkorDB` or importing redislite after tortoise.
"""
from __future__ import annotations

import os

from redislite.falkordb_client import FalkorDB as _OriginalFalkorDB

from tortoise.config import RELATIVE_PATH_ERROR


class FalkorDB(_OriginalFalkorDB):
    """Guarded subclass of redislite's FalkorDB.

    Raises RuntimeError when `path` is relative (never permitted — relative
    paths create per-CWD servers, the Category-3 leak). Absolute paths and
    no-arg construction pass through to the original.
    """

    def __init__(self, *args, **kwargs):
        if args and args[0] is not None and isinstance(args[0], str):
            path = args[0]
            if not os.path.isabs(path) and not path.startswith("~"):
                raise RuntimeError(RELATIVE_PATH_ERROR.format(path=path))
        super().__init__(*args, **kwargs)
