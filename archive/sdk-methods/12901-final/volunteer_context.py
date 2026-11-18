# Archived sdk-method `volunteer_context` — REMOVED from the product surface (#11609).
#
# batch:   12901-final
# reason:  #12901 FINAL batch: volunteer_context left the public SDK surface — privatised (body survives as `_volunteer_context`), no single owner-approved replacement. Frozen here so a mistake is inspected against the code that shipped it (#11609 ruling).
# source:  tortoise/sdk.py @ 424368c05d8ee9b26aa2d77e4c5a93a4b207b58f
#
# This file IS the source of record. To restore: copy each block below back into the
# file named in ITS OWN `verbatim from <path>` banner (one symbol can span more than
# one file), then run `uv run python tools/archive_surface.py check` (which fails
# while `archive/` and the live surface disagree).
#
# ⛔ NOT part of the product: not importable from `tortoise`, not served over MCP, and
#    not pointed at by any retired-forward name. Do not import this file.

ARCHIVED_KIND = 'sdk-method'
ARCHIVED_SYMBOL = 'volunteer_context'
ARCHIVED_BATCH = '12901-final'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = '424368c05d8ee9b26aa2d77e4c5a93a4b207b58f'

# ── verbatim from tortoise/sdk.py ──

    def volunteer_context(
        self,
        window: list[dict],
        session_id: str | None = None,
        prior_context: str | None = None,
        min_confidence: float = 0.7,
        max_pointers: int = 3,
        why: bool = True,
    ) -> dict:
        """Volunteer memory context for a turn window (issue #2103 §6.3).

        ONE code path with POST /v1/context (the shared canonical pipeline in
        tortoise/volunteer.py). In-process, fully stateless per call
        (``session_id``/``prior_context`` are continuity inputs only — zero
        graph writes).

        - Raises ValueError (VolunteerValidationError) on invalid window /
          budgets BEFORE any graph work (SDK validates first — issue §6.3).
        - Fail-open: any retrieval/assembly error returns the degraded shape
          (empty block + degraded_reason) — never breaks the caller's turn.
        - Zero LLM on the read path — no provider key required.
        """
        from .volunteer import (  # local import: avoid module cycle
            run_volunteer_pipeline,
            validate_request,
        )
        # SDK validates FIRST — before any graph/network work (issue §6.3).
        validate_request(
            window, session_id=session_id, prior_context=prior_context,
            min_confidence=min_confidence, max_pointers=max_pointers, why=why,
        )
        proj = self._get_proj()
        return run_volunteer_pipeline(
            proj, window, session_id=session_id, prior_context=prior_context,
            min_confidence=min_confidence, max_pointers=max_pointers, why=why,
            # #12297: the volunteer resolve pool is another page whose ORDER
            # this read does not own — the pipeline re-sorts the pool (stage 6)
            # before the cut, so a pre-cut re-pick moves MEMBERSHIP only.
            # Passed as a kwargs SEAM rather than at a call site, which is why
            # the AST census over ``.tortoise_fts_query(`` calls cannot see it.
            _search_fn=self.tortoise_fts_query,
            _search_kwargs={"_rerank_candidates": False},
        )
