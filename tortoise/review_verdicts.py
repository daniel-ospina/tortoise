"""#2354 — the uncertainty-verdict layer for correction conflicts.

Epic #2349 (record-axis history layer), workstream **E-review**. #1088 gates
non-ingest promotion and ``quarantine_batch`` holds a failed batch, but an
uncertain *correction conflict* — a decision Point whose LIVE prior it refutes
or replaces — had no named decision state and no recorded verdict. The
extraction path mints the candidate (``mining.py::_temporal_wire``) and then
leaves it ``temporal_reviewed IS NULL``: a queue with a two-word action
vocabulary (``merge`` / ``reject``) and no contract for who decides or on what
evidence.

This module is the missing contract. It **adds no MCP tool and no SDK method**
(the surface is frozen — see ``AGENTS.md`` "MCP/SDK Surface Approval"); it is
the orchestration layer that rides the existing quarantine + promote/retract
mechanics:

    keep    → promote the candidate (``approve_merge(merge)`` →
              ``promote_point``); the deferred NAND/CORRECTS wire lands here.
    reject  → drop the candidate (``approve_merge(reject)``, journaled as
              ``DedupeRejected``); retract it when it is already live.
    close   → decided-no-action: the candidate is marked decided and stays
              draft — no promote, no retract.

**The two postures that make the loop deterministic** (epic #2349 policy 3):

1. *Auto-accept is EP-only, and opt-in.* A candidate is auto-accepted ONLY
   when its **EP belief** (``TortoiseEP.compute_confidence`` — the persisted
   ``posterior_*``, else the ``ep_*`` prior, else the neutral Beta(1,1);
   never an LLM self-report) clears the configurable threshold. The default is
   **OFF** (``TORTOISE_VERDICT_AUTO_ACCEPT``): every candidate queues until a
   verdict is supplied. The canonical measurement state (#3276) is recorded on
   the queue entry, and an unmeasured claim is warned about — not silently
   accepted the way a neutral Beta(1,1) at a lowered threshold could look.
2. *The sweep never resolves a queued decision.* ``sweep()`` may only
   *queue* a candidate or *auto-accept* (keep) one above threshold; it has no
   path to ``close`` or ``reject``. ``drain()`` applies exactly the verdicts
   it is handed, in deterministic (ascending candidate id) order, and applies
   none for a candidate it was not handed. A queued decision is therefore
   never resolved by any automatic path.

Threshold configuration: ``TORTOISE_VERDICT_THRESHOLD`` (default
:data:`DEFAULT_THRESHOLD`, 0.75 — the same confidence bar the source
implementation uses). A garbage/out-of-range env value warns and falls back to
the default rather than silently flipping the bar or crashing the pipeline; an
explicit out-of-range argument raises.

Provenance: every verdict is written onto **both** points — the candidate
(``reviewVerdict``, ``reviewVerdictAt``, ``reviewVerdictBy``, the target ids)
and each prior it concerns (``reviewVerdict``, ``reviewVerdictCandidate``, …)
— so the decision is readable from either end. This is review-state metadata,
written directly exactly like the pre-existing ``temporal_reviewed`` /
``dedup_reviewed`` state; ``keep``/``reject`` additionally emit their existing
``DedupeRecorded``/``DedupeRejected`` journal events through ``approve_merge``.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

from .env_truthy import env_flag
from .live import LIFECYCLE_FANOUT_QUERY

logger = logging.getLogger(__name__)


# ── The verdict vocabulary — declared ONCE, here ────────────────────────
VERDICT_CLOSE = "close"
VERDICT_KEEP = "keep"
VERDICT_REJECT = "reject"

#: The whole vocabulary, in no implied order; ``VERDICT_MECHANICS`` gives each
#: term's mapping onto existing mechanics. A value outside this tuple is a
#: contract change, not a local extension.
VERDICT_VALUES: tuple[str, ...] = (VERDICT_CLOSE, VERDICT_KEEP, VERDICT_REJECT)

#: term -> the EXISTING mechanic it maps to. Consumer-visible so a caller (or
#: another lane) reads the mapping instead of re-deriving it.
VERDICT_MECHANICS: dict[str, str] = {
    VERDICT_KEEP: "promote the candidate (draft→live; wires the deferred NAND/CORRECTS)",
    VERDICT_REJECT: "drop the candidate from the queue; retract it when it is already live",
    VERDICT_CLOSE: "decided-no-action — no promote, no retract",
}

#: The candidate's decision state. ``queued`` is the named below-threshold
#: state (never "limbo"); ``decided`` is terminal for this layer.
DECISION_QUEUED = "queued"
DECISION_DECIDED = "decided"

#: The calibration bar an EP posterior must clear for auto-accept. The number
#: is the source implementation's, adopted as the tortoise default; the
#: *confidence* it is compared against is always the calibrated posterior.
DEFAULT_THRESHOLD = 0.75
THRESHOLD_ENV = "TORTOISE_VERDICT_THRESHOLD"

#: Auto-accept is OFF unless an operator opts in — the epic's "everything else
#: queues" default, and the fail-closed direction (a missing knob never
#: promotes an uncertain correction).
AUTO_ACCEPT_ENV = "TORTOISE_VERDICT_AUTO_ACCEPT"

# Provenance property names. ``reviewVerdict`` is written on BOTH points; the
# candidate also carries the target list, each target the candidate id — so the
# record is readable from either end.
_P_CANDIDATE_DECISION = "reviewDecision"
_P_CANDIDATE_VERDICT = "reviewVerdict"
_P_CANDIDATE_VERDICT_AT = "reviewVerdictAt"
_P_CANDIDATE_VERDICT_BY = "reviewVerdictBy"
_P_CANDIDATE_VERDICT_REASON = "reviewVerdictReason"
_P_CANDIDATE_VERDICT_TARGETS = "reviewVerdictTargets"
_P_CANDIDATE_VERDICT_SOURCE = "reviewVerdictSource"
_P_TARGET_VERDICT = "reviewVerdict"
_P_TARGET_CANDIDATE = "reviewVerdictCandidate"
_P_TARGET_VERDICT_AT = "reviewVerdictAt"
_P_TARGET_VERDICT_BY = "reviewVerdictBy"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()  # noqa: UP017


def resolve_threshold(explicit: float | None = None) -> float:
    """Resolve the auto-accept threshold: explicit arg > env > default.

    A malformed or out-of-range ENV value warns and returns the default (a
    typo must not silently move the bar, and must not crash a background
    sweep); a malformed/out-of-range EXPLICIT value raises (the caller passed
    it deliberately, so it is a programming error).
    """
    if explicit is not None:
        value = float(explicit)
        if not 0.0 < value <= 1.0:
            raise ValueError(
                f"verdict threshold must be in (0, 1], got {explicit!r}")
        return value
    raw = (os.environ.get(THRESHOLD_ENV) or "").strip()
    if not raw:
        return DEFAULT_THRESHOLD
    try:
        value = float(raw)
    except ValueError:
        logger.warning("%s=%r is not a number — using default %s",
                       THRESHOLD_ENV, raw, DEFAULT_THRESHOLD)
        return DEFAULT_THRESHOLD
    if not 0.0 < value <= 1.0:
        logger.warning("%s=%r is outside (0, 1] — using default %s",
                       THRESHOLD_ENV, raw, DEFAULT_THRESHOLD)
        return DEFAULT_THRESHOLD
    return value


def auto_accept_enabled(explicit: bool | None = None) -> bool:
    """Resolve auto-accept: explicit arg > env (``env_flag``) > OFF."""
    if explicit is not None:
        return bool(explicit)
    return env_flag(AUTO_ACCEPT_ENV, False)


class VerdictLayer:
    """The uncertainty-verdict layer over an existing ``TortoiseSDK`` handle.

    The SDK is the *mechanics port*: the layer calls the existing review verbs
    (``approve_merge``, ``promote_point``, ``retract_point``) unmodified and
    writes only the review-state metadata those verbs leave unowned.
    """

    def __init__(self, sdk: Any, *, threshold: float | None = None,
                 auto_accept: bool | None = None, ep: Any = None) -> None:
        self.sdk = sdk
        self.threshold = resolve_threshold(threshold)
        self.auto_accept = auto_accept_enabled(auto_accept)
        self._ep = ep

    # ── internals ───────────────────────────────────────────────────
    @property
    def _proj(self):
        return self.sdk._get_proj()

    def _ep_obj(self):
        return self._ep if self._ep is not None else self.sdk._get_ep()

    def ep_posterior(self, candidate_id: str) -> dict:
        """The candidate's EP belief — the only confidence input.

        ``mean``/``alpha``/``beta`` come from ``TortoiseEP.compute_confidence``
        (the persisted ``posterior_*``, else the ``ep_*`` prior, else the
        neutral Beta(1,1)); it is graph EP state, never a model's self-report.
        ``measurement`` is the canonical three-state label
        (``measured`` | ``baseline`` | ``unmeasured``, #3276) recorded for
        provenance — a claim with no EP state at all reads ``unmeasured``.
        """
        conf = self._ep_obj().compute_confidence(candidate_id)
        raw = self._proj.g.query(
            "MATCH (n:Point {id:$id}) "
            "RETURN n.posterior_alpha, n.ep_alpha, n.baseline_set",
            params={"id": candidate_id},
        ).result_set
        posterior_alpha, ep_alpha, baseline_set = raw[0] if raw else (None, None, None)
        from .search_engine import ep_measurement_state
        measurement = ep_measurement_state(
            posterior_alpha=posterior_alpha, ep_alpha=ep_alpha,
            baseline_set=bool(baseline_set))
        return {
            "mean": conf["mean"],
            "alpha": conf["alpha"],
            "beta": conf["beta"],
            "effective_n": conf["effective_n"],
            "measurement": measurement,
        }

    def _candidate_props(self, candidate_id: str) -> dict:
        rows = self._proj.g.query(
            "MATCH (n:Point {id:$id}) RETURN properties(n)",
            params={"id": candidate_id},
        ).result_set
        if not rows:
            raise ValueError(f"No point {candidate_id!r}")
        props = rows[0][0]
        if not (props.get("dedup_candidate") or props.get("temporal_candidate")):
            raise ValueError(
                f"Point {candidate_id!r} is not a review candidate "
                f"(no dedup_candidate/temporal_candidate flag)")
        return props

    @staticmethod
    def _is_temporal(props: dict) -> bool:
        return bool(props.get("temporal_candidate"))

    @staticmethod
    def _targets(props: dict) -> list[str]:
        """The prior(s) a candidate concerns, de-duplicated and ordered."""
        out: list[str] = []
        for key in ("temporal_target_ids", "temporal_target_id", "dedup_target_id"):
            value = props.get(key)
            if isinstance(value, (list, tuple, set)):
                out.extend(str(v) for v in value if v)
            elif value:
                out.append(str(value))
        seen: set[str] = set()
        return [t for t in out if not (t in seen or seen.add(t))]

    def _batch_quarantined(self, props: dict) -> str | None:
        """The quarantine reason blocking promotion, or None when promotable."""
        batch_id = props.get("batch_id")
        if not batch_id:
            return None
        from .mining import batch_status
        status = batch_status(self._proj, batch_id)
        if status and status.get("status") == "quarantined":
            return status.get("reason") or "batch quarantined"
        return None

    def _keep_refusal(self, props: dict) -> str | None:
        """Why ``promote_point`` would refuse this candidate, or None.

        Mirrors ``promote_point``'s own block reasons EXACTLY and in its own
        order (``is_operator`` → already-live no-op → ``not_draft`` →
        ``batch_quarantined``), so ``keep`` can be refused BEFORE any durable
        review write or journal emit. Without this, `keep` half-applies: the
        pre-write ``approve_merge`` stamps ``temporal_reviewed='merge'`` (and
        emits ``DedupeRecorded``), which drops the candidate out of
        ``list_dedup_candidates``, and the promotion is then blocked — so the
        candidate lands in NO review surface (the limbo #2354 exists to
        eliminate) and re-issuing ``keep`` can never recover it.
        """
        if props.get("is_operator") or props.get("op_type"):
            return "is_operator"
        status = props.get("status")
        if status == "live":
            # ``promote_point`` no-ops (already_live, blocked=False) BEFORE the
            # quarantine check, so a live candidate has no refusal here.
            return None
        if status != "draft":
            return f"not_draft (status {status!r})"
        reason = self._batch_quarantined(props)
        if reason:
            return f"batch quarantined ({reason})"
        return None

    # ── queue ───────────────────────────────────────────────────────
    def mark_queued(self, candidate_id: str, *, posterior: dict | None = None,
                    reason: str | None = None) -> dict:
        """Stamp the candidate into the named below-threshold decision state.

        A decided candidate is returned untouched — this never un-decides.
        """
        props = self._candidate_props(candidate_id)
        if props.get(_P_CANDIDATE_DECISION) == DECISION_DECIDED:
            return {"candidate_id": candidate_id, "decision": DECISION_DECIDED,
                    "already_decided": True,
                    "verdict": props.get(_P_CANDIDATE_VERDICT)}
        post = posterior if posterior is not None else self.ep_posterior(candidate_id)
        at = _now()
        self._proj.g.query(
            "MATCH (n:Point {id:$id}) SET "
            "n.reviewDecision=$decision, n.reviewDecisionAt=$at, "
            "n.reviewThreshold=$threshold, n.reviewConfidence=$confidence, "
            "n.reviewMeasurement=$measurement, n.reviewDecisionReason=$reason",
            params={"id": candidate_id, "decision": DECISION_QUEUED, "at": at,
                    "threshold": self.threshold, "confidence": post["mean"],
                    "measurement": post["measurement"], "reason": reason},
        )
        logger.info(
            "review-verdict queue: candidate %s queued "
            "(EP mean %.4f, %s, threshold %.4f)",
            candidate_id, post["mean"], post["measurement"], self.threshold)
        return {"candidate_id": candidate_id, "decision": DECISION_QUEUED,
                "confidence": post["mean"], "measurement": post["measurement"],
                "threshold": self.threshold, "queuedAt": at, "reason": reason}

    def pending(self, *, candidate_type: str = "all") -> list[dict]:
        """Every QUEUED decision, in deterministic (ascending id) order."""
        where = ["n.reviewDecision = $q",
                 "(n.temporal_candidate = true OR n.dedup_candidate = true)"]
        if candidate_type == "temporal":
            where[1] = "n.temporal_candidate = true"
        elif candidate_type == "content":
            where[1] = "n.dedup_candidate = true"
        rows = self._proj.g.query(
            "MATCH (n:Point) WHERE " + " AND ".join(where) +
            " RETURN properties(n) ORDER BY n.id",
            params={"q": DECISION_QUEUED},
        ).result_set
        return [self._shape_pending(row[0]) for row in rows]

    @staticmethod
    def _shape_pending(props: dict) -> dict:
        return {
            "id": props.get("id"),
            "candidate_type": "temporal" if props.get("temporal_candidate")
                              else "content",
            "targets": VerdictLayer._targets(props),
            "confidence": props.get("reviewConfidence"),
            "measurement": props.get("reviewMeasurement"),
            "threshold": props.get("reviewThreshold"),
            "queuedAt": props.get("reviewDecisionAt"),
            "reason": props.get("reviewDecisionReason"),
            "verdicts": list(VERDICT_VALUES),
        }

    # ── verdict application ─────────────────────────────────────────
    def decide(self, candidate_id: str, verdict: str, *, reason: str | None = None,
               decided_by: str | None = None, source: str = "manual") -> dict:
        """Apply one explicit verdict and record its provenance on both points.

        Raises ``ValueError`` for an out-of-vocabulary verdict, a non-candidate,
        a candidate already decided with a DIFFERENT verdict, or a ``keep`` on
        a candidate ``promote_point`` would refuse (an operator node, a
        terminal/absent status, or a quarantined batch) — refused BEFORE any
        durable write so the verdict cannot half-apply.
        """
        if verdict not in VERDICT_VALUES:
            raise ValueError(
                f"verdict must be one of {VERDICT_VALUES}, got {verdict!r}")
        props = self._candidate_props(candidate_id)
        existing = props.get(_P_CANDIDATE_DECISION)
        if existing == DECISION_DECIDED:
            if props.get(_P_CANDIDATE_VERDICT) == verdict:
                return {"candidate_id": candidate_id, "verdict": verdict,
                        "decision": DECISION_DECIDED, "already_decided": True}
            raise ValueError(
                f"candidate {candidate_id!r} is already decided "
                f"{props.get(_P_CANDIDATE_VERDICT)!r} — cannot re-decide "
                f"{verdict!r}")

        is_temporal = self._is_temporal(props)
        targets = self._targets(props)
        at = _now()
        applied: dict = {}

        if verdict == VERDICT_KEEP:
            # Resolve promotability FIRST, before any durable write: the
            # promotion wire needs ``*_reviewed='merge'`` set before
            # ``promote_point`` runs (Variant C), so approval cannot be
            # reordered after it — the refusal must instead precede both.
            blocked = self._keep_refusal(props)
            if blocked:
                raise ValueError(
                    f"candidate {candidate_id!r} cannot be kept: {blocked} — "
                    f"keep refused with no side effects; the candidate stays "
                    f"queued")
            review_key = "temporal_reviewed" if is_temporal else "dedup_reviewed"
            prior_review = props.get(review_key)
            prior_reviewed = props.get("reviewed")

            def _promotion_landed() -> bool:
                """Whether the promotion should be TREATED as landed.

                ``promote_point`` answers ``already_live`` for a node that was
                live BEFORE its CAS — it returns, it does not raise — so in the
                cases this handler exists for, a raise with a live node is a
                promotion that landed and whose tail then failed. EVERY step of
                the landed tail can raise, and all of them leave the node live:
                ``_mark_dirty`` (a retried write that can exhaust); the
                post-CAS quarantine re-check's ``batch_status`` read; the
                ``alreadyDecided`` wire, whose two graph reads are uncaught and
                which also raises ``ValueError`` when the prior is deleted
                between promote_point's own check and that call; the temporal
                NAND/supersede block, which catches only ``ValueError``, so a
                graph abort escapes; the incident-operator promote; and the
                ``PointPromoted`` snapshot read (``get_point`` — the emit itself
                is best-effort and does not raise). Rolling the review write
                back there is the INVERSE of the #2354 limbo: the stamps are
                erased from a promotion that LANDED, so the graph contradicts
                its own journal (``DedupeRecorded`` records the merge, the node
                says undecided). For a temporal candidate, ``sweep()`` selects on
                the stamp and not on status — unlike ``list_dedup_candidates``,
                which hides a live node — so it offers the candidate back as
                though it had never been decided, and above the auto-accept bar
                it re-issues ``keep``, whose ``approve_merge`` no longer
                short-circuits (the stamp it compares against is the one just
                erased) and so journals a SECOND ``DedupeRecorded`` for one
                logical merge. A content candidate is re-surfaced by nothing
                (``list_dedup_candidates`` wants a draft), so there the journal
                is the only record that its merge ever happened. Re-issuing
                ``keep`` by hand does eventually re-stamp either arm, so what is
                lost is not the write but the record that it had already been
                decided; that is why the rollback must be reserved for the raises
                that really did not land.

                The answer is deliberately CONSERVATIVE, not a proof: a live
                node carrying the id may be a promotion under the same id that
                is already live rather than this call's. Treating that as
                landed is the safe direction because the rollback is itself
                fan-out-wide (a bare ``MATCH … SET``), so guessing "not landed"
                could erase the stamps of the node that DID land; a candidate
                stranded by the wrong guess (this CAS did not land, a sibling
                live) is recoverable by re-issuing ``keep``.

                Scanned over the WHOLE id fan-out (#7822): point ids are not
                unique, every lifecycle writer's bare ``MATCH … SET`` binds
                every node carrying the id, and a verdict read off ``rows[0]``
                is server-row-order dependent — here that would erase the stamp
                of a landed promotion whenever a duplicate sibling happened to
                be returned first. Only the literal ``status`` column is
                consulted: the CAS writes ``status='live'`` and nothing else
                that this asks about, so an ``is_operator``, ``outdated`` or
                statusless sibling neither counts as landed nor is filtered
                out of the scan.

                An unreadable graph reads as landed: a rollback that cannot be
                verified must not be risked against stamps that are correct,
                and the limbo it would otherwise leave is recoverable by
                re-issuing ``keep`` (``reviewDecision`` is unset, and
                ``approve_merge`` is idempotent for a temporal candidate whose
                stamp still matches — a content candidate re-stamps and
                journals a further ``DedupeRecorded``) whereas a rollback erases
                a record that a landed promotion had already paid for.
                """
                try:
                    rows = self._proj.g.query(
                        LIFECYCLE_FANOUT_QUERY,
                        params={"id": candidate_id},
                    ).result_set
                    # Indexed, not unpacked: the shape is owned by a SHARED
                    # constant that may grow a column, and an unpack error here
                    # would replace the promote error this whole block exists
                    # to preserve.
                    return any(row[1] == "live" for row in rows)
                except Exception:
                    logger.warning(
                        "review rollback skipped: could not read %r after a "
                        "failed promote — leaving the review stamps in place",
                        candidate_id, exc_info=True)
                    return True

            def _roll_back_review_write() -> None:
                # Roll the review write back so the candidate is NOT left
                # reviewed-but-unpromoted (#2354), then surface the failure.
                # The ``DedupeRecorded`` emit inside approve_merge cannot be
                # unjournaled — it remains an audit record of an attempt.
                # Reached for a ``blocked`` answer (which never follows a
                # landed CAS: ``not_draft`` means the CAS matched nothing,
                # ``already_live`` answers blocked=False) and for a raise that
                # did NOT land (an absent node, or a rebuild that exhausted
                # its retries before the CAS) — ``_promotion_landed`` excludes
                # the post-CAS raise, where the stamps are correct.
                # A failed cleanup must never mask the failure it cleans up:
                # the caller re-raises the ORIGINAL exception, so this only
                # logs.
                try:
                    self._proj.g.query(
                        f"MATCH (n:Point {{id:$id}}) "
                        f"SET n.{review_key} = $prior, n.reviewed = $reviewed",
                        params={"id": candidate_id, "prior": prior_review,
                                "reviewed": prior_reviewed},
                    )
                except Exception:
                    logger.warning(
                        "review rollback FAILED for %r — the review stamps may "
                        "be left applied (the #2354 limbo)", candidate_id,
                        exc_info=True)

            self.sdk.approve_merge(candidate_id, action="merge")
            try:
                applied = self.sdk.promote_point(candidate_id)
            except Exception:
                if not _promotion_landed():
                    _roll_back_review_write()
                raise
            if applied.get("blocked"):
                _roll_back_review_write()
                raise ValueError(
                    f"candidate {candidate_id!r} could not be promoted: "
                    f"{applied.get('reason') or 'blocked'}")
        elif verdict == VERDICT_REJECT:
            self.sdk.approve_merge(candidate_id, action="reject")
            # A live candidate was already promoted — reject must tombstone it.
            if props.get("status") == "live":
                applied = {"retracted": True,
                           "retract": self.sdk.retract_point(candidate_id)}
        else:  # VERDICT_CLOSE
            reviewed_key = "temporal_reviewed" if is_temporal else "dedup_reviewed"
            self._proj.g.query(
                f"MATCH (n:Point {{id:$id}}) "
                f"SET n.{reviewed_key} = 'close', n.reviewed = true",
                params={"id": candidate_id},
            )

        self._proj.g.query(
            "MATCH (n:Point {id:$id}) SET "
            "n.reviewDecision=$decision, n.reviewVerdict=$verdict, "
            "n.reviewVerdictAt=$at, n.reviewVerdictBy=$by, "
            "n.reviewVerdictReason=$reason, n.reviewVerdictSource=$source, "
            "n.reviewVerdictTargets=$targets",
            params={"id": candidate_id, "decision": DECISION_DECIDED,
                    "verdict": verdict, "at": at, "by": decided_by,
                    "reason": reason, "source": source, "targets": targets},
        )
        recorded_on: list[str] = []
        for target in targets:
            rows = self._proj.g.query(
                "MATCH (n:Point {id:$id}) SET "
                "n.reviewVerdict=$verdict, n.reviewVerdictCandidate=$candidate, "
                "n.reviewVerdictAt=$at, n.reviewVerdictBy=$by RETURN n.id",
                params={"id": target, "verdict": verdict,
                        "candidate": candidate_id, "at": at, "by": decided_by},
            ).result_set
            if rows:
                recorded_on.append(target)

        logger.info(
            "review verdict: candidate %s → %s (by=%s source=%s targets=%s)",
            candidate_id, verdict, decided_by, source, targets)
        return {
            "candidate_id": candidate_id,
            "verdict": verdict,
            "decision": DECISION_DECIDED,
            "candidate_type": "temporal" if is_temporal else "content",
            "targets": targets,
            "provenance_targets": recorded_on,
            "applied": applied,
            "decidedAt": at,
            "decidedBy": decided_by,
            "source": source,
            "reason": reason,
        }

    # ── the sweep and the drainer ───────────────────────────────────
    def sweep(self, *, batch_id: str | None = None,
              candidate_ids: list[str] | None = None) -> dict:
        """Queue every undecided correction conflict; auto-accept above bar.

        Deterministic (ascending candidate id) and NON-RESOLVING: only
        ``keep`` is reachable here, only when auto-accept is enabled AND the EP
        belief clears the threshold AND the batch is not quarantined.
        Everything else is stamped ``queued`` and left for an explicit verdict.
        """
        if candidate_ids is not None:
            ids = sorted({str(c) for c in candidate_ids})
        elif batch_id is not None:
            rows = self._proj.g.query(
                "MATCH (n:Point {batch_id:$b}) WHERE n.temporal_candidate = true "
                "RETURN n.id ORDER BY n.id",
                params={"b": batch_id},
            ).result_set
            ids = [row[0] for row in rows]
        else:
            rows = self._proj.g.query(
                "MATCH (n:Point) WHERE n.temporal_candidate = true "
                "AND n.temporal_reviewed IS NULL "
                "AND (n.reviewDecision IS NULL OR n.reviewDecision <> $decided) "
                "RETURN n.id ORDER BY n.id",
                params={"decided": DECISION_DECIDED},
            ).result_set
            ids = [row[0] for row in rows]

        quarantined = None
        if batch_id is not None:
            from .mining import batch_status
            status = batch_status(self._proj, batch_id)
            if status and status.get("status") == "quarantined":
                quarantined = status.get("reason") or "batch quarantined"

        report: dict = {"examined": 0, "queued": 0, "auto_accepted": 0,
                        "skipped_reviewed": 0, "batch_quarantined": quarantined,
                        "threshold": self.threshold,
                        "auto_accept": bool(self.auto_accept),
                        "decisions": []}
        for cid in ids:
            report["examined"] += 1
            try:
                props = self._candidate_props(cid)
            except ValueError as exc:
                logger.warning("verdict sweep: skipping %s: %s", cid, exc)
                continue
            if (props.get(_P_CANDIDATE_DECISION) == DECISION_DECIDED
                    or props.get("temporal_reviewed") is not None):
                report["skipped_reviewed"] += 1
                continue
            posterior = self.ep_posterior(cid)
            if (self.auto_accept and not quarantined
                    and posterior["mean"] >= self.threshold):
                if posterior["measurement"] == "unmeasured":
                    logger.warning(
                        "verdict sweep: candidate %s has NO EP state "
                        "(neutral Beta(1,1), mean %.4f) — auto-accepting at "
                        "the configured threshold %.4f",
                        cid, posterior["mean"], self.threshold)
                try:
                    record = self.decide(
                        cid, VERDICT_KEEP,
                        reason=(f"auto-accept: measured EP posterior "
                                f"{posterior['mean']:.4f} >= threshold "
                                f"{self.threshold}"),
                        decided_by="sweep", source="auto")
                except ValueError as exc:
                    logger.warning(
                        "verdict sweep: auto-accept refused for %s: %s",
                        cid, exc)
                else:
                    report["auto_accepted"] += 1
                    report["decisions"].append(record)
                    continue
            self.mark_queued(cid, posterior=posterior, reason=quarantined)
            report["queued"] += 1
        return report

    def drain(self, verdicts: dict[str, str], *,
              decided_by: str | None = None) -> dict:
        """Apply an explicit ``{candidate_id: verdict}`` map, deterministically.

        Applies NOTHING that was not handed to it — an empty map is a no-op and
        every queued decision survives it. Candidates are processed in
        ascending id order; an unknown/undecidable candidate is reported in
        ``errors`` without aborting the rest.
        """
        applied: list[dict] = []
        errors: list[dict] = []
        for cid in sorted(verdicts):
            try:
                applied.append(self.decide(cid, verdicts[cid],
                                           decided_by=decided_by))
            except (ValueError, KeyError) as exc:
                errors.append({"candidate_id": cid, "error": str(exc)})
        return {"applied": len(applied), "results": applied, "errors": errors,
                "remaining_queued": len(self.pending())}
