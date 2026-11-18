# Archived sdk-method `get_confidence` — REMOVED from the product surface (#11609).
#
# batch:   12901-final
# reason:  #12901 FINAL batch: get_confidence left the public SDK surface — privatised (body survives as `_get_confidence`), no single owner-approved replacement. Frozen here so a mistake is inspected against the code that shipped it (#11609 ruling).
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
ARCHIVED_SYMBOL = 'get_confidence'
ARCHIVED_BATCH = '12901-final'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = '424368c05d8ee9b26aa2d77e4c5a93a4b207b58f'

# ── verbatim from tortoise/sdk.py ──

    def get_confidence(self, claim_id: str,
                       require_calibration: bool | None = None) -> dict:
        """Get EP confidence for a claim: {mean, variance, alpha, beta}.

        Lazy consistency (#85): if the claim is a dirty root (confidence
        diverged after writes), dream it first so reads return fresh values.

        Calibration posture (#1157): the lazy dream path WRITES n.confidence
        and the read returns belief numbers — both must be gated on
        calibration like compute_confidence. require_calibration=True raises
        CalibrationError when THIS CLAIM is uncalibrated (the read puts only
        this claim in a natural parameter, #7739) and, when the claim is a
        dirty root, when the lazy dream consumes an uncalibrated point — never
        for an unrelated point elsewhere in the graph. None
        (default) resolves to the shared TORTOISE_EP_REQUIRE_CALIBRATION
        posture (default True — fail-closed, post-#344; set
        TORTOISE_EP_REQUIRE_CALIBRATION to "0" to opt out).
        """
        if require_calibration is None:
            require_calibration = _ep_require_calibration_default()
        if require_calibration:
            # #7739: the read itself consumes exactly ONE claim —
            # ``ep.compute_confidence`` just reads that node's params — so the
            # gate is scoped to the claim. Scoping it to the claim's NEIGHBOUR
            # HOOD would over-block: a connected point the read never puts in
            # a natural parameter would freeze a per-claim read. When the claim
            # is a dirty root, the lazy dream below runs EP over the dirty
            # roots and gates ITSELF on the seeds it will consume (fail-closed
            # where it matters). An uncalibrated CLAIM still raises: narrowing
            # the scope never excuses the thing asked for.
            self._ensure_calibrated("get_confidence", scope={claim_id})
        if claim_id in self._dirty_roots:
            self.dream(dirty_only=True,
                       require_calibration=require_calibration,
                       stamp_dreamed_at=False,  # read path — never stamps (epic 903-C2)
                       warm_start=False)  # from-scratch reads (epic 903-C4)
        return self._get_ep().compute_confidence(claim_id)
