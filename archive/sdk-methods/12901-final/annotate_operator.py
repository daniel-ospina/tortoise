# Archived sdk-method `annotate_operator` — REMOVED from the product surface (#11609).
#
# batch:   12901-final
# reason:  #12901 FINAL batch: annotate_operator left the public SDK surface — privatised (body survives as `_annotate_operator`), no single owner-approved replacement. Frozen here so a mistake is inspected against the code that shipped it (#11609 ruling).
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
ARCHIVED_SYMBOL = 'annotate_operator'
ARCHIVED_BATCH = '12901-final'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = '424368c05d8ee9b26aa2d77e4c5a93a4b207b58f'

# ── verbatim from tortoise/sdk.py ──

    def annotate_operator(self, id: str, bias: float, precision: float,
                          consistency: float, directness: float) -> dict:
        """Annotate an operator Point with structured epistemic dimensions.

        These dimensions are not inert: they modulate the operator's EP weight in a
        symmetric bounded band, so a well-rated connection counts for MORE than an
        unrated one and the worst-rated for less. Leaving them unset is exactly
        neutral (factor 1.0) and is the default — set them only to express a
        deliberate judgement about the connection's quality.

        Args:
            id: Operator Point ID (must have is_operator=true).
            bias: 0-1, how much hidden stake/additional interest beyond stated position.
                INVERTED: higher bias means LESS weight, because a one-sided source
                should count for less than a disinterested one.
            precision: 0-1, how narrow/well-defined the relevance claim is (higher = more weight).
            consistency: 0-1, how stable this relevance is across contexts (higher = more weight).
            directness: 0-1, how directly the source bears on the target (higher = more weight).

        The four are averaged (never multiplied), so rating one well cannot penalise
        the others, and a partial rating moves the weight only as far as that
        dimension justifies. Do not use these to disagree with a connection — that is
        NAND's job; use them to grade how much the connection contributes. A rating
        does not require a source.

        HOW FAR IT MOVES depends on the operator's base weight, because the rating
        multiplies the base and the result is then clamped to ``[0.1, 10.0]``. A
        NAND starts at 8.0 and DOUBLES when it targets another operator, so a
        best-rated operator-targeting NAND is clamped both before and after the
        rating and gains NOTHING; even a top-level NAND gains only 1.25x
        (8.0 -> 16.0 -> clamped to 10.0). On an ordinary IMPL the base is 1.0
        (2.0 when operator-targeting), and the band applies in full there. So the
        band is exact near the low end of the range and compresses as the base
        approaches the ceiling — a rating on a contradiction is rarely worth much.

        Raises ValueError if id not found, not an operator, or dims out of [0,1].
        """
        point = self.get_point(id)
        if not point:
            raise ValueError(f"Operator {id!r} not found")
        if not point.get("is_operator"):
            raise ValueError(f"Point {id!r} is not an operator")
        for name, val in (("bias", bias), ("precision", precision),
                          ("consistency", consistency), ("directness", directness)):
            # #4647: the guard runs BEFORE the range check because
            # ``0 <= Decimal('NaN') <= 1`` raises ``decimal.InvalidOperation``
            # (an ArithmeticError, not the documented ValueError), so the
            # range check would raise an unnamed wrong-class error for the
            # exact values this guard exists to name (cycle-8 P2). It also
            # runs before ``_emit_event`` below: the range check admits an
            # inexact Decimal/Fraction/np.float32, and the emit would then
            # record the caller's raw value. This closes the INEXACT subset;
            # an exact-but-non-JSON-native value (``Decimal('0.5')``) is still
            # emitted raw and its journal write still fails — a pre-existing
            # serializer gap tracked in #7174, not this PR.
            _reject_unrepresentable_number(name, val)
            if not 0 <= val <= 1:
                raise ValueError(f"{name} must be 0-1, got {val}")
        # #432 Task 3: durable OperatorAnnotated event (append-before-mutation).
        # #3689: the positional payload is the :GraphEvent contract
        # (docs/event-catalog.md — id/bias/precision/consistency/directness),
        # so it is kept verbatim. `id=` + the annotator_* extras are REQUIRED
        # for the JSONL branch: without them `_emit_event`'s
        # `point is None and id is None` early-return dropped the record from
        # the rebuild journal entirely, so rebuild_all erased the annotation
        # silently (the #3299 class). Same payload+id shape as PointRetracted.
        self._emit_event("OperatorAnnotated", {
            "id": id, "bias": bias, "precision": precision,
            "consistency": consistency, "directness": directness,
        }, id=id,
            annotator_bias=bias, annotator_precision=precision,
            annotator_consistency=consistency, annotator_directness=directness)
        return self.update_point(id,
            annotator_bias=bias, annotator_precision=precision,
            annotator_consistency=consistency, annotator_directness=directness)
