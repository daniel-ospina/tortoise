# Archived sdk-method `calibrate_summary` — REMOVED from the product surface (#11609).
#
# batch:   12901-final
# reason:  #12901 FINAL batch: calibrate_summary left the public SDK surface — privatised (body survives as `_calibrate_summary`), no single owner-approved replacement. Frozen here so a mistake is inspected against the code that shipped it (#11609 ruling).
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
ARCHIVED_SYMBOL = 'calibrate_summary'
ARCHIVED_BATCH = '12901-final'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = '424368c05d8ee9b26aa2d77e4c5a93a4b207b58f'

# ── verbatim from tortoise/sdk.py ──

    def calibrate_summary(self) -> list[dict]:
        """Audit graph calibration state. Returns per-point guidance.
        
        Checks baseline_set flag on non-operator Points. For uncalibrated
        points, traverses extractedFrom→Source to check for inherited credibilityTier.

        #9063/#9269: the non-operator predicate is the GATE's — absence-or-false,
        with NO ``op_type`` conjunct (EP's factor-input arm applies this SAME
        absence-or-false predicate since #9269, and a hybrid
        ``create_point(op_type=...)`` node is ``is_operator = false`` WITH
        ``op_type``, so it passes on BOTH sides and IS a participant; the
        ``op_type IS NULL`` conjunct belongs to the STRICTER operator-less
        direct-edge arms only) — plus the
        id-less exclusion, so a legacy Point written before ``is_operator`` was
        stamped is LISTED here — this is the guidance the
        calibration gate's error message points at, and it must not be empty
        for a node the gate just named.

        #2199 provenance: calibrated rows carry ``baseline_source`` — the raw
        graph provenance token (one of ``set-by-author`` /
        ``inherited-from-source`` / ``system-default``; None for legacy
        ``baseline_set=true`` rows written before the token family) — plus a
        human ``provenance`` phrase routed through the single BASELINE_SOURCE_
        DISPLAY map (auto-assigned system-default priors are VISIBLE here,
        never a silent uniform: issue #2199 indicator 3). Legacy token-less
        rows render as set-by-author (the #398 2x2 mapping — their persistence
        class is author-set: never recomputed by inheritance).
        """
        proj = self._get_proj()
        # #9063: the absence-or-false HALF of the counting/claim predicates
        # (``_operator_scope_clauses``, ``summarize_structure``,
        # ``list_pointkinds``, ``ep``'s contested-claim scan) — deliberately
        # WITHOUT their ``n.op_type IS NULL`` conjunct (next paragraph) — not
        # the bare ``n.is_operator = false``. The bare form matches ZERO rows
        # on a legacy property-ABSENT Point — precisely the class the
        # calibration gate now NAMES in its error — so "Run
        # calibrate_summary() for full guidance" opened an empty page for the
        # node it had just named.
        #
        # The predicate is the GATE's, exactly — absence-or-false, with NO
        # ``op_type`` conjunct (see ``_uncalibrated_ep_participants``: EP's
        # factor-input arm carries this SAME absence-or-false predicate since
        # #9269, so a hybrid ``create_point(op_type=...)`` node — ``is_operator
        # = false`` WITH ``op_type`` — is a participant and must be listed here
        # for its remedy to exist; the ``op_type IS NULL`` conjunct belongs to
        # the STRICTER operator-less direct-edge arms, never to this one) — plus
        # the id-less exclusion the
        # gate makes: ``set_point_baseline(None)`` is not a remedy a caller can
        # run, and such a node is never an EP participant.
        not_operator = "(n.is_operator IS NULL OR n.is_operator = false)"
        # The gate's id exclusion, exactly (an UNCALIBRATABLE id — blank or
        # whitespace-padded — takes an advice ``set_point_baseline`` cannot
        # land: the engine's labelled id equality misses it, so the row would
        # advertise a remedy that silently writes nothing).
        where = (f"WHERE {not_operator} AND n.id IS NOT NULL "
                 f"AND toLower(typeof(n.id)) IN {_ID_RESOLVABLE_TYPES} "
                 "AND NOT (toLower(typeof(n.id)) = 'string' "
                 "         AND (n.id = '' "
                 f"              OR left(n.id, 1) IN [{_ID_WHITESPACE_SET}] "
                 f"              OR right(n.id, 1) IN [{_ID_WHITESPACE_SET}]))")
        params = {}
        
        from tortoise.source_credibility import resolve_tier
        rows = proj.g.query(
            f"MATCH (n:Point) {where} "
            "AND (n.pointKind IS NULL OR n.pointKind <> 'assessment') "
            "OPTIONAL MATCH (n)-[:extractedFrom]->(s:Source) "
            "RETURN n.id, n.content, n.pointKind, "
            # #9063: the SAME predicate the gate demands and
            # ``_hydrate_evidence`` loads — ``baseline_set = true`` AND an
            # ``ep_alpha`` to load — not Python truthiness of
            # ``coalesce(baseline_set, false)``: an out-of-contract non-boolean
            # flag ('true', 1, 'yes'), or a flag set with no ``ep_alpha``,
            # would otherwise read CALIBRATED here with NO suggestion, while
            # the gate refuses the run and tells the human to "run
            # calibrate_summary() for full guidance" — the same empty page this
            # audit was widened to close.
            "coalesce(n.baseline_set = true "
            "         AND n.ep_alpha IS NOT NULL AND n.ep_beta IS NOT NULL,"
            "         false) AS calibrated, "
            # #9063: the RAW flag, because it — not the gate's demand — is what
            # ``_apply_source_inheritance`` freezes on. A row whose flag is set
            # but which stores no prior is frozen against inheritance while
            # being gate-demanded, so the "run compute_confidence() to apply"
            # advice could never clear it (measured loop). This column lets the
            # audit offer the remedy that actually works.
            "coalesce(n.baseline_set, false) AS bl_flag, "
            "n.status, "
            # #7739: the legacy ``outdated=true`` flag is terminal without a
            # status write (invalidate_point), so the gate needs BOTH halves
            # of the shared #2498 terminal predicate; expose it here rather
            # than re-querying per Point.
            "coalesce(n.outdated, false) AS outdated, "
            "n.baseline_source AS bl_source, "
            "s.credibilityTier, s.sourceKind, s.url AS src_url",
            params=params,
        ).result_set
        
        results = []
        for row in rows:
            (pid, content, pk, calibrated, bl_flag, status, outdated,
             bl_source, ctier, skind, src_url) = row
            item = {"id": pid, "content": content, "pointKind": pk,
                    "calibrated": calibrated, "status": status,
                    "outdated": outdated,
                    "baseline_source": bl_source}
            # #2199: every calibrated row routes through the single provenance
            # display map so the copy never drifts per surface. Legacy
            # token-less rows (pre-#2199 baseline_set=true) render as
            # set-by-author — same persistence class (never recomputed).
            if calibrated:
                if bl_source in BASELINE_SOURCE_DISPLAY:
                    item["provenance"] = BASELINE_SOURCE_DISPLAY[bl_source]
                elif bl_source is None:
                    item["provenance"] = BASELINE_SOURCE_DISPLAY[
                        BASELINE_SOURCE_SET_BY_AUTHOR]
                else:
                    # Unrecognized token (pre-#2199 'explicit'/'inherited'
                    # spelling on a graph the rename migration has not run
                    # against) — surface it loudly with the fix path so old
                    # and new spellings never silently coexist.
                    item["provenance"] = (
                        f"Legacy baseline_source token {bl_source!r} — run "
                        "graph-scripts/2199_baseline_source_rename.py to "
                        "rename to the #2199 token family."
                    )
            # Effective tier: explicit credibilityTier > sourceKind tier-form >
            # registry default (issue #398 Task 6 — legacy-inherited advisory).
            eff_tier = resolve_tier(ctier, skind)
            
            if not calibrated:
                if bl_flag:
                    # #9063: a flag with no usable prior behind it. Inheritance
                    # SKIPS this row (``_apply_source_inheritance`` freezes a
                    # truthy flag), so "run compute_confidence() to apply" would
                    # refuse, re-print this same advice, and loop forever — the
                    # empty-remedy failure this PR closes for the source-linked
                    # legacy point. The writer here is the remedy that works.
                    item["suggestion"] = (
                        f"baseline_set is set but no prior is stored "
                        f"(ep_alpha/ep_beta missing) — call "
                        f"set_point_baseline({pid!r}, alpha, beta)"
                    )
                elif src_url and eff_tier:
                    item["suggestion"] = (
                        f"Inherited {eff_tier} from Source {src_url} — run "
                        f"compute_confidence() to apply"
                    )
                elif src_url and not eff_tier:
                    item["suggestion"] = (
                        f"Source {src_url} is untiered — call "
                        f"manage_source_trust('{src_url}', tier='T0'..'T4') or "
                        f"register_source(url, kind, tier=...) "
                        f"(covers all points from this source)"
                    )
                else:
                    item["suggestion"] = (
                        f"Call set_point_baseline({pid!r}, alpha, beta) "
                        f"or recreate with credibility kwarg"
                    )
            else:
                # Legacy-inherited advisory: explicit/inherited baseline whose
                # source was re-tiered — suggest re-derivation via the writer.
                if src_url and ctier and item.get("calibrated"):
                    item["note"] = (
                        f"Source {src_url} tier {ctier} — baseline may predate "
                        f"issue #398; re-derive via set_source_tier"
                    )
            results.append(item)

        # Deduplicate: keep one entry per Point ID, prefer Source-based
        # suggestions.
        # #9063: an UNCALIBRATED sibling wins over a calibrated one first. The
        # #7822 duplicate-id fan-out lives in one id, and the gate names that
        # id — so if one node of the fan-out is calibrated and another is not,
        # reporting the calibrated row ("no suggestion") opens the same empty
        # page this audit was widened to close (measured: row-order dependent
        # before this).
        seen = {}
        deduped = []
        for item in results:
            pid = item["id"]
            if pid not in seen:
                seen[pid] = item
                deduped.append(item)
                continue
            prev = seen[pid]

            def _rank(row: dict) -> tuple:
                """Lower wins. Uncalibrated first (the gate names the id, so its
                row must show what blocks), then the WRITER advice — a sibling
                whose flag is frozen against inheritance can only be cleared by
                ``set_point_baseline``, and the writer binds every node of the
                fan-out — then the Source advice."""
                sug = str(row.get("suggestion") or "")
                if "set_point_baseline" in sug:
                    which = 0
                elif "Source" in sug:
                    which = 1
                else:
                    which = 2
                return (1 if row.get("calibrated") else 0, which)

            if _rank(item) < _rank(prev):
                for i, d in enumerate(deduped):
                    if d["id"] == pid:
                        deduped[i] = item
                        break
                seen[pid] = item
        return deduped
