# Archived sdk-method `checkpoint` — REMOVED from the product surface (#11609).
#
# batch:   11609-phase2-sources-containers
# reason:  name retired into mine_knowledge_from_session(items=...) (#4282 Phase 2, #11609): row 16 keeps this batch filer as its items arm, frozen here as the record of what the retired name did
# source:  tortoise/sdk.py @ 32011c4dad4b42364868cb0810ad8e7317a7dfb6
#
# This file IS the source of record. To restore: copy each block below back into the
# file named in ITS OWN `verbatim from <path>` banner (one symbol can span more than
# one file), then run `uv run python tools/archive_surface.py check` (which fails
# while `archive/` and the live surface disagree).
#
# ⛔ NOT part of the product: not importable from `tortoise`, not served over MCP, and
#    not pointed at by any retired-forward name. Do not import this file.

ARCHIVED_KIND = 'sdk-method'
ARCHIVED_SYMBOL = 'checkpoint'
ARCHIVED_BATCH = '11609-phase2-sources-containers'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = '32011c4dad4b42364868cb0810ad8e7317a7dfb6'

# ── verbatim from tortoise/sdk.py ──

    def checkpoint(self, items: list[dict], agent_name: str = "checkpoint",
                   threshold: float = 0.95) -> dict:
        """Session batch save — two-tier dedup (content hash + embedding similarity).

        Each item: {wing, room, content} — ``content`` is REQUIRED and must be
        a ``str``; a missing/``None``/non-str ``content``, a non-dict item or a
        non-list ``items`` raises ``BundleValidationError`` naming the item
        index and ``content`` (never an ``AttributeError``/``KeyError`` from a
        private helper — #8554). Returns {filed: N, duplicates: M}.
        threshold: cosine similarity for semantic dedup (0.0-1.0).
                   Set to 1.0 to disable semantic dedup (hash-only).

        The 0.95 default is the near-IDENTICAL-text bar (re-filed session
        notes), deliberately stricter than the paraphrase band: re-validated
        under bge-small 2026-08-21 (#1349 T14) — the strictest fixture
        paraphrase ("Deployments must be automated for reliability" ↔
        "Automating deployments is required for reliability") scores 0.955
        ≥ 0.95, while looser paraphrases (0.77-0.90) stay below and file
        as distinct points. Keep in sync with mcp_server.tortoise_checkpoint.
        """
        # #8554: fail-closed BEFORE the projection is opened, before any hash,
        # dedup read or write. One home for the item schema (see
        # ``_check_checkpoint_items``).
        self._check_checkpoint_items(items)

        from datetime import datetime, timezone
        filed, duplicates = 0, 0
        proj = self._get_proj()
        now = datetime.now(timezone.utc).isoformat()  # noqa: UP017

        # Tier 1: content hash dedup
        to_check: list[tuple[dict, str]] = []
        seen: set[str] = set()
        for item in items:
            content = item["content"]
            ch = _content_hash(content)
            if ch in seen or self._content_exists(content):
                duplicates += 1
                continue
            seen.add(ch)
            to_check.append((item, ch))

        if not to_check:
            return {"filed": 0, "duplicates": duplicates}

        # Tier 2: embedding similarity dedup (GAP-08)
        to_file = to_check
        if threshold < 1.0:
            try:
                to_file = self._semantic_dedup(
                    to_check, threshold, pointKind="checkpoint-item")
            except ImportError:
                # Expected in zero-dependency environments — hash-only fallback.
                # #330: previously a bare `except Exception: pass` swallowed
                # real failures silently; log the designed fallback at INFO.
                _logger.info(
                    "Semantic dedup unavailable (embeddings deps missing) — "
                    "hash-only fallback"
                )
                to_file = to_check
            except Exception as e:
                # #330: real dedup backend failures must be observable — a
                # silent degrade to hash-only dedup would file duplicates.
                _logger.warning(
                    "Semantic dedup failed — falling back to hash-only dedup: %s", e
                )
                to_file = to_check

        duplicates += len(to_check) - len(to_file)

        for item, ch in to_file:
            p = self.create_point(
                "checkpoint-item", item["content"],
                wing=item.get("wing", ""),
                room=item.get("room", ""),
                content_hash=ch,
            )
            # GAP-07 (partially closed by #432 _emit_event — graph mutations
            # now emit :GraphEvent; this EventRecorded path is session-capture
            # provenance and stays separate).
            # #7951: this provenance Event went through a bare ``proj.apply`` —
            # the projection's REPLAY fold — so it never reached the journal
            # and a rebuild LOST it. It now goes through
            # ``_apply_and_journal`` (the sanctioned live door; journal first,
            # then fold), which is added to the same try/except for the same
            # reason the fold already was: a provenance-record failure must not
            # fail the checkpoint batch.
            try:
                self._apply_and_journal(proj, {
                    "type": "EventRecorded",
                    "id": ulid(),
                    "eventKind": "pointAdded",
                    "subject": agent_name,
                    "object": p["id"],
                    "startedAt": now,
                })
            except Exception:
                _logger.warning("Failed to emit provenance event for point %s", p["id"])
            filed += 1

        monitoring.record_ingest()
        return {"filed": filed, "duplicates": duplicates}
