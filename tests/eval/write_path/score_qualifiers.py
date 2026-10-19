"""Scoring for the raw-vs-narrative falsifier (#7947).

Pre-registered endpoint (issue comment, fixed before any run):

    per planted qualifier, classify the arm's extracted output bound to the same
    claim as preserved | weakened | strengthened | flattened | absent | unknown
    PRIMARY metric = loss rate = (flattened + absent) / planted qualifiers, per arm

**Instrument.** The primary classification is a deterministic marker instrument:
for each qualifier it asks two questions and combines them.

  * ``claim_present`` — do the bound claim's content words (from its
    ``verbatim_anchor``, or the qualifier's own words when unbound) appear among
    the arm's extracted claims?
  * ``marker_present`` — does the arm's output carry a same-class marker, i.e. a
    hedge marker for a hedge, a scope marker for a scope, a stance marker for a
    stance?

  = ``preserved`` if both; ``flattened`` if the claim survived with no same-class
  marker; ``absent`` if the claim did not survive.

This is deliberately crude and deliberately reproducible. ⛔ **The PRIMARY loss
metric is LESS exposed to the marker table, not unexposed:** ``loss = flattened +
absent`` and ``absent`` never reads a marker, but ``flattened`` does — so the
volume sensitivity that ``distortion_rate`` describes can only move a cell from
``flattened`` to ``preserved``. Measured on the committed run, restricting the
marker search to the single best-matching claim moves tuning Δloss −13.3 → −6.7 pp
and pooled −4.0 → 0.0 pp, and leaves the holdout Δ unchanged at +10.0 pp — i.e.
it can move the decision input's neighbours but not the decision input. The
DISTORTION reading (``distortion_rate``) is the volume-sensitive one and is
reported as secondary, never as the decision input.
Its known weakness is paraphrase — an arm may
preserve a hedge with vocabulary the marker list lacks, which would read as
``flattened``. ⛔ **The error is TWO-SIDED, and this module used to claim it was
not** ("only possible in the DANGEROUS direction … can only under-credit
preservation"). Both directions are real:

  * **under-credit (flattened for a preserved qualifier)** — missing vocabulary,
    as above. This is the conservative direction for a *loss* metric.
  * **over-credit (preserved for a flattened qualifier)** — ``marker_present`` is
    an UNBOUNDED SUBSTRING test over a table that mixes space-suffixed markers
    (``"may "``, ``"one "``, ``"just "``, ``"before "``) with bare stems
    (``"only"``, ``"except"``, ``"single "``, ``"support"``, ``"review"``,
    ``"decision"``, ``"likely"``), so ``"commonly"`` satisfies scope's
    ``"only"``, ``"none "``/``"gone "`` satisfy ``"one "``,
    ``"exception(al)"`` satisfies ``"except"`` and ``"unlikely"`` satisfies
    hedge's ``"likely"``. Each of those turns a ``flattened`` cell into
    ``preserved`` — i.e. it UNDERSTATES loss.

**The over-credit is NOT arm-neutral**, which is what makes it worth this much
prose: the arm that emits more claim text gets more chances to contain a mid-word
hit, and §10 records the narrative emitting 25–35 % more claims — so this error
leans toward the arm whose loss this measurement treats as the risk. It is
**latent, not a wrong number, on the committed run**: every ``preserved`` row in
both committed score files was checked and none is carried by a mid-word hit (the
three preserved ``scope`` rows are the literal "the admin screen is the only
place…" and "defaults off until the perf review signs off" ×2). The fix that would
remove the class — word-bounding the match (``re.search(rf"\\b{re.escape(m.strip())}",
matched_text)``) — is deliberately NOT applied here: the marker table is the
REGISTERED instrument, so changing it silently would make these runs and any
future run non-comparable. It belongs in a newly registered run (#7947 §10).

⚠️ The second opinion the registration contemplated for exactly those decisive
``flattened`` cases — a judge-arbitration pass — was **registered but NOT run**,
and this module has no flag for it: the deterministic instrument needed no
session-to-session variance estimate, so the registered repeat-run check was
never exercised either. Both facts are recorded in §10 of
``docs/architecture/EXTRACTOR-V4-ARCHITECTURE.md``; do not read the absence of a
``--judge`` flag as the pass having been run and folded in.

The marker lists are part of the registered instrument: they are printed in the
output so a reader can audit them.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

WRITE_PATH_DIR = Path(__file__).resolve().parent
GOLD_DIR = WRITE_PATH_DIR / "gold"

ARMS = ("raw", "narrative")
CLASSES = ("hedge", "scope", "stance")

#: Which receipt flag contaminates WHICH arm. The two arms' inputs have different
#: producers: S2 builds both extracts, but S1 builds the narrative story only, so
#: an S1 chunk that raised or hit its length cap leaves the narrative short of a
#: chunk and cannot touch the raw arm's input (which is the transcript itself).
#: Treating them symmetrically would let a broken raw-adjacent marker hide a real
#: fire; ignoring the S1 half altogether let a truncated narrative manufacture one.
CONTAMINATION_KEYS: dict[str, tuple[str, ...]] = {
    "raw": ("partial", "truncated", "s2_error"),
    "narrative": ("partial", "truncated", "s2_error", "s1_error", "s1_truncated"),
}

#: The pre-registered corpus split. The DECISION input is the HOLDOUT delta; the
#: tuning half is reported so a reader can see the split was real, and the pooled
#: figure is reported (labelled) because it is the number a reader will otherwise
#: reach for — it is NOT what the registration decides on, and on the committed
#: run it differs from the decision input by 14 pp AND IN SIGN (-4.0 pooled vs
#: +10.0 holdout), which is precisely why the two are never interchangeable.
TUNING_SESSIONS = ("wp01_quarry_debug", "wp02_lumen_refactor", "wp03_ember_design",
                   "wp04_aurora_perf")
HOLDOUT_SESSIONS = ("wp05_retro_writeup", "wp06_quarry_rollout", "wp07_bluepeak_followup")
#: Pre-registered decision band on the holdout delta (pp).
BAND_PP = 10.0
#: Pre-registered allowed drop in precision/recall (pp) before branch 1 fires.
PR_TOLERANCE_PP = 5.0

# ── the instrument: same-class markers ──────────────────────────────────────
MARKERS: dict[str, tuple[str, ...]] = {
    "hedge": (
        "probably", "maybe", "might", "may ", "perhaps", "i think", "i believe",
        "roughly", "about ", "mostly", "seems", "appears", "suggests", "likely",
        "uncertain", "not sure", "as far as i recall", "hypothesis", "tentativ",
        "circumstantial", "correlates with", "realistically", "my read", "i'd guess",
        "i would guess", "believe", "assume", "assumption", "plausible",
        "possibl", "could be", "suspect", "impression", "no evidence",
        "double check", "double-check", "verify", "unconfirmed",
    ),
    "scope": (
        "only", "just ", "except", "until", "before ", "not for", "limited to",
        "as long as", "provided", "unless", "in scope", "out of scope",
        "on my machine", "hosted lane", "eu region", "single ", "one ", "prior to",
    ),
    "stance": (
        "agree", "agreed", "push back", "comfortable", "risky", "tempting",
        "rather", "signed off", "decision", "concern", "worried", "prefer",
        "wants", "against", "support", "oppose", "review", "accepted", "rejected",
        "endorse", "sign-off", "signoff",
    ),
}

#: Words too common to identify a claim. Kept small and visible on purpose.
#: Read as a prose block on purpose — the word list is the instrument, and a
#: list literal would bury it (SIM905 is the style rule, not a defect here).
STOP = frozenset(
    "the a an and or of to in on for with is are was were be been that this it its "  # noqa: SIM905
    "we our you your they their he she his her as at by from into not no yes so but "
    "if then than there here what which who whom when where why how all any both "
    "each few more most other some such only own same too very can will just should "
    "now also has have had do does did doing i me my".split()
)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", s.lower())


def _content_words(s: str) -> set[str]:
    return {w for w in _norm(s).split() if w and w not in STOP and len(w) > 2}


def render_claims(embed_list: dict) -> list[str]:
    """One string PER EXTRACTED CLAIM for one arm.

    Per-claim, not pooled: the marker test must be bound to the claim the
    qualifier modifies. Pooling the whole output would let a hedge anywhere in
    a 20-point list "preserve" every qualifier in the session, which would
    report near-zero loss for BOTH arms and make the delta noise.
    """
    out: list[str] = []
    for p in embed_list.get("points") or []:
        if isinstance(p, dict):
            c = _norm(str(p.get("content") or ""))
            if c.strip():
                out.append(c)
    for op in embed_list.get("operators") or []:
        if isinstance(op, dict):
            c = _norm(json.dumps(op))
            if c.strip():
                out.append(c)
    return out


def _claims_matching(anchor_words: set[str], claims: list[str],
                     threshold: float = 0.6) -> list[str]:
    """The extracted claims that identify the same claim as the anchor."""
    hits: list[str] = []
    for c in claims:
        if not anchor_words:
            continue
        cw = set(c.split())
        overlap = len(anchor_words & cw) / len(anchor_words)
        if overlap >= threshold:
            hits.append(c)
    return hits


def classify(qualifier: dict, units: dict[str, dict], claims: list[str]) -> dict:
    """Deterministic classification for one qualifier against one arm.

    The marker is looked for IN THE MATCHED CLAIM(S) ONLY — never the whole
    output — so "preserved" means *this qualifier survived on its own claim*.
    """
    qclass = qualifier["class"]
    bound = qualifier.get("bound_claim")
    unit = units.get(bound) if bound else None

    # What must appear for "the same claim survived"?
    anchor = ""
    if unit:
        anchor = unit.get("verbatim_anchor") or ""
    if not anchor:
        anchor = qualifier["verbatim_qualifier"]
    want = _content_words(anchor)
    matched = _claims_matching(want, claims)
    claim_present = bool(matched)

    # Marker must be IN the matched claim, not somewhere in the session.
    matched_text = " \n ".join(matched)
    marker_present = any(m in matched_text for m in MARKERS[qclass])

    if claim_present and marker_present:
        outcome = "preserved"
    elif claim_present:
        outcome = "flattened"
    else:
        outcome = "absent"

    return {
        "id": qualifier["id"],
        "class": qclass,
        "bound_claim": bound,
        "verbatim_qualifier": qualifier["verbatim_qualifier"],
        "claim_present": claim_present,
        "marker_present": marker_present,
        "outcome": outcome,
        "matching_claims": len(matched),
        "matched_excerpt": matched_text[:220],
        "anchor_words_total": len(want),
    }


def score_session(session_id: str, arm: str, arms_dir: Path) -> dict:
    arms = json.loads((arms_dir / f"{session_id}.arms.json").read_text())
    gold = json.loads((GOLD_DIR / f"{session_id}.gold.json").read_text())
    units = {u["id"]: u for u in gold.get("planted_units", [])}
    qualifiers = gold.get("planted_qualifiers", [])

    # ⛔ An arm whose extract the EXTRACTOR ITSELF marked incomplete (`partial`,
    # rung-4 prefix-accept: the tail of the model's list was dropped) or that
    # raised is NOT that arm's output — scoring it understates its claims and
    # so overstates its loss. The run is still scored (it is the record of what
    # happened) but the receipt is flagged, the decision block refuses to
    # certify a branch on it, and the flag is written into the output.
    stats = (arms.get("s2_stats") or {}).get(arm) or {}
    s1_stats = arms.get("s1_stats") or []
    receipt_flags = {
        "partial": bool(stats.get("partial")),
        "truncated": bool(stats.get("truncated")),
        "s2_error": arms.get("s2_errors", {}).get(arm),
        # ⛔ The S1 half, recorded (run-level: S1 runs once per session, so both
        # arms carry the same value) and consumed for the NARRATIVE arm only —
        # S1 produces that arm's input; the raw arm never touches it
        # (`CONTAMINATION_KEYS`). An S1 chunk that raised, or a summary that hit
        # the length cap (`extractor_v2.run_s1`'s documented tail-drop), leaves
        # the narrative story missing a chunk: it can drop claims — raising
        # narrative loss and MANUFACTURING a branch-1 fire — or move the cell
        # across the band. Up to this point the harness wrote `errors` into the
        # receipt and nothing read it.
        "s1_error": bool(arms.get("errors")),
        "s1_truncated": any(s.get("finish_reason") == "length" for s in s1_stats),
    }
    embed_list = arms["embed_list"].get(arm) or {}
    claims = render_claims(embed_list)

    rows = [classify(q, units, claims) for q in qualifiers]
    n = len(rows)
    matched = sum(1 for r in rows if r["claim_present"])
    lost = sum(1 for r in rows if r["outcome"] in ("flattened", "absent"))
    fl = sum(1 for r in rows if r["outcome"] == "flattened")
    ab = sum(1 for r in rows if r["outcome"] == "absent")
    return {
        "session_id": session_id,
        "arm": arm,
        "n_qualifiers": n,
        "preserved": sum(1 for r in rows if r["outcome"] == "preserved"),
        "flattened": fl,
        "absent": ab,
        "matched": matched,
        # Among qualifiers whose claim SURVIVED extraction, how often was the
        # qualifier itself flattened? This is a reading of §10's question that is
        # not confounded by claim-drop (a dropped claim is `absent`, not counted
        # here) — but it IS volume-sensitive in one direction: `classify()` looks
        # for the marker in the union of every claim that matches the anchor, so
        # an arm that emits more claims gets more chances to have some matched
        # claim carry it, which biases its distortion DOWN. Measured on the
        # committed run, restricting to the single best-matching claim moves
        # pooled narrative distortion 23.5% -> 29.4% and leaves raw at 20.0%.
        # Report it as a secondary reading, never as the decision input.
        "distortion_rate": (fl / matched) if matched else None,
        "loss": lost,
        "loss_rate": (lost / n) if n else None,
        "points": len(embed_list.get("points") or []),
        "operators": len(embed_list.get("operators") or []),
        "receipt_flags": receipt_flags,
        "rows": rows,
    }


def precision_recall(session_id: str, arm: str, arms_dir: Path) -> dict:
    """Registered secondary guard: did the arm keep the planted facts?

    Lexical, same idiom as the qualifier instrument: a planted unit counts as
    RECALLED when some extracted claim carries >=60% of its anchor's content
    words. A claim counts as a HIT (precision numerator) when it matches some
    planted unit. Crude on purpose — it is the volume guard, not the endpoint:
    E1 asks that volume fall *without losing the facts that matter*.
    """
    arms = json.loads((arms_dir / f"{session_id}.arms.json").read_text())
    gold = json.loads((GOLD_DIR / f"{session_id}.gold.json").read_text())
    units = gold.get("planted_units", [])
    embed_list = arms["embed_list"].get(arm) or {}
    claims = render_claims(embed_list)

    recalled = 0
    for u in units:
        want = _content_words(u.get("verbatim_anchor") or "")
        if want and _claims_matching(want, claims):
            recalled += 1

    hits = 0
    for c in claims:
        cw = set(c.split())
        for u in units:
            want = _content_words(u.get("verbatim_anchor") or "")
            if want and len(want & cw) / len(want) >= 0.6:
                hits += 1
                break

    n_units = len(units)
    n_claims = len(claims)
    return {
        "session_id": session_id,
        "arm": arm,
        "planted_units": n_units,
        "recalled": recalled,
        "recall": (recalled / n_units) if n_units else None,
        "claims_emitted": n_claims,
        "claims_hit": hits,
        "precision": (hits / n_claims) if n_claims else None,
    }


def evaluate_branches(delta_loss_pp: float | None, delta_precision_pp: float | None,
                      delta_recall_pp: float | None, *, contaminated: list[str]) -> dict:
    """Apply the pre-registered §7 rule to the HOLDOUT deltas.

    Pure, so the boundary cases can be tested without a run. Two decisions this
    encodes, both of which the measurement depends on:

    * **Rounded percentage points, not raw floats.** The same mathematical
      +10 pp is ``9.999999999999998`` when it is summed one way and
      ``10.000000000000002`` the other, so a raw ``> 10.0`` test decides the same
      delta differently by summation order — a branch flipped by representation
      error is not reproducible. (The *discriminating* value is the second one:
      it fires a raw test and does not fire a rounded one. ``100*(7/10-6/10)``
      alone is not a witness — it reads "no fire" both ways.)
    * **No branch is certified on an incomplete receipt.** ``partial``/
      ``truncated``/error means the arm's extract is not what the model produced,
      so its loss is overstated; on this corpus one qualifier moves the holdout
      delta by 10 pp, i.e. across the band.
    * **A missing delta is not a passing one.** ``None`` means the split has no
      reading (an arm emitted zero claims, so ``precision = hits / n_claims`` is
      undefined), never that the arm matched — so parity requires all three deltas
      to be PRESENT, and an absent one is recorded as
      ``scoring_input_incomplete`` instead of silently satisfying the tolerance.
    """
    def _pp(v: float | None) -> float | None:
        return None if v is None else round(v, 9)

    loss, prec, rec = _pp(delta_loss_pp), _pp(delta_precision_pp), _pp(delta_recall_pp)
    # ⛔ The contamination rule is ONE-SIDED BY DIRECTION, not by convenience. A
    # dropped tail removes claims, so an incomplete RAW arm OVERSTATES raw's loss
    # (Δ understated → a fire is real evidence, merely under-detected), while an
    # incomplete NARRATIVE arm OVERSTATES Δ — it can MANUFACTURE a branch-1 fire off
    # an extract the extractor itself calls incomplete. So `fires` is suppressed only
    # when the narrative receipt is the dirty one; refusing it in both directions
    # would let a broken raw arm hide a real fire.
    narrative_dirty = any(t.endswith(":narrative") for t in contaminated)
    raw_dirty = any(t.endswith(":raw") for t in contaminated)
    # The CONDITION, computed once and independently of contamination: branch 1's
    # condition is met by Δloss > band OR a precision/recall drop beyond tolerance.
    #
    # ⛔ THE TWO LEGS ARE NOT SYMMETRIC IN THE SAME DIRECTION, which is why they are
    # suppressed SEPARATELY. The paragraph above reasons about LOSS, where a dropped
    # tail only removes opportunities to count a loss — so a dirty RAW arm is
    # conservative there. PRECISION is a RATIO (hits / claims_emitted): dropping a
    # tail of UNMATCHED claims RAISES raw precision, which LOWERS
    # Δprecision = prec_narr − prec_raw and can fire branch 1 off an extract the
    # extractor marked incomplete. Measured on this run: raw 78/119 = 65.5%; drop 10
    # unmatched tail claims and it is 78/109 = 71.6%, so Δprecision = 63.1 − 71.6 =
    # −8.5 pp → branch 1 fires. And `wp07_bluepeak_followup:raw` — the receipt that
    # IS partial in the committed holdout — is exactly that channel. So the P/R leg
    # is suppressed by a dirty receipt in EITHER arm, while the Δloss leg keeps the
    # one-sided rule (a dirty raw arm there leaves a fire as real, merely
    # under-detected).
    loss_leg = loss is not None and loss > BAND_PP
    pr_leg = any(d is not None and d < -PR_TOLERANCE_PP for d in (prec, rec))
    # `condition_met` is `loss_leg or pr_leg`; the legs are kept apart above because
    # each answers to a different contamination direction.
    fires = ((loss_leg and not narrative_dirty)
             or (pr_leg and not narrative_dirty and not raw_dirty))
    # ⛔ A MISSING P/R delta is not a PASSING one. `all(d is None or abs(d) <= tol)`
    # made an ABSENT delta satisfy the tolerance, so a split in which an arm emitted
    # NO claims read as certified: precision is `hits / n_claims` with `n_claims ==
    # 0` — None — and recall is None for the same reason, while Δloss is 0 because
    # every qualifier is `absent` on both sides. `evaluate_branches(0.0, None, None)`
    # therefore returned `branch_2_certified=True`, and `verdict_label` recorded D3
    # as SETTLED off two empty extracts. That is the exact failure `certified =
    # parity and not contaminated` exists to prevent, reached without tripping any
    # contamination flag. S2's flags cannot see it either: an arm that emits nothing
    # is not `partial`/`truncated`/`errored` — its OUTPUT is empty, not incomplete.
    scoring_input_incomplete = any(d is None for d in (loss, prec, rec))
    parity = (loss is not None and abs(loss) <= BAND_PP
              and all(d is not None and abs(d) <= PR_TOLERANCE_PP for d in (prec, rec)))
    certified = parity and not contaminated
    # Branch 3's EFFECT ("stays provisional") is what an uncertifiable parity
    # produces too: the registered rule has no branch for "met, but the run cannot
    # be trusted", and inventing one here would hide it.
    provisional = not fires and not certified
    return {"branch_1_fires": fires, "branch_2_applies": parity,
            "branch_2_certified": certified, "branch_3_stays_provisional": provisional,
            # Recorded so the ROUTE can be named: a split with a delta that does
            # not exist cannot evaluate any branch, and calling that "branch 3"
            # (the effect flag below) would assert a route the inputs contradict.
            "scoring_input_incomplete": scoring_input_incomplete,
            "contaminated_receipts": list(contaminated),
            # ⛔ SUPPRESSION REQUIRES THE CONDITION, not merely a dirty narrative
            # receipt. Direction alone cannot say it: at Δ = +2 pp with a partial
            # narrative arm nothing was suppressed — no fire was available — and
            # labelling that cell "branch 1's condition was MET but SUPPRESSED"
            # asserts a route the rule's own inputs contradict, while overwriting
            # the route that IS available (branch 2 met, not certifiable).
            "falsifier_suppressed_by_contamination":
                (loss_leg and narrative_dirty)
                or (pr_leg and (narrative_dirty or raw_dirty)),
            # WHICH arm withheld the fire. The two are not interchangeable: a
            # narrative receipt overstates the delta, a raw one can overstate the
            # PRECISION drop. One label for both would name a cause the run does not
            # support.
            "falsifier_suppressed_by": (
                "narrative" if narrative_dirty else "raw" if raw_dirty else None)}


def suppression_log(verdict: dict) -> list[str]:
    """The operator-facing suppression lines — ONE arm per message.

    The two suppressors work in OPPOSITE directions: a dirty NARRATIVE receipt
    overstates the delta, while a dirty RAW receipt can drop a tail of unmatched
    claims, which RAISES raw precision and so overstates the PRECISION DROP. One
    sentence for both would name a mechanism the run does not support, so the log
    and :func:`verdict_label` are derived from the same ``falsifier_suppressed_by``
    field — and a test pins the text against the arm in both directions.
    """
    if not verdict.get("falsifier_suppressed_by_contamination"):
        return []
    if verdict.get("falsifier_suppressed_by") == "raw":
        return [
            "  \u26d4 BRANCH 1 WAS SUPPRESSED: its condition was met, but the RAW receipt is"
            " incomplete — a",
            "     dropped tail of unmatched claims RAISES raw precision, so it OVERSTATES the"
            " precision",
            "     DROP (not the delta). Not a finding either way until that arm is re-run.",
        ]
    return [
        "  \u26d4 BRANCH 1 WAS SUPPRESSED: its condition was met, but the NARRATIVE receipt is"
        " incomplete — a",
        "     dropped tail removes claims and so OVERSTATES the delta. Not a finding either way"
        " until re-run.",
    ]


def verdict_label(verdict: dict) -> str:
    """The recorded verdict string. ONE place, because the route is the whole point.

    A single `branch_3` flag cannot say which route was taken — it is true for
    "parity met, uncertifiable" AND for "branch 3 reached", and collapsing the two
    records a route the flags contradict. This is the label a reader audits, so it
    must agree with the console, case by case.
    """
    if verdict.get("falsifier_suppressed_by_contamination"):
        if verdict.get("falsifier_suppressed_by") == "raw":
            return ("branch 1's condition was MET but SUPPRESSED: the raw receipt is "
                    "incomplete, so its dropped tail inflates raw precision and would "
                    "overstate the precision drop")
        return ("branch 1's condition was MET but SUPPRESSED: the narrative receipt is "
                "incomplete and would overstate the delta")
    if verdict["branch_1_fires"]:
        return "the falsifier FIRES (branch 1)"
    if verdict.get("scoring_input_incomplete"):
        # Deliberately AFTER the fire: a fire carried by the Δloss leg is real
        # evidence even when a P/R leg is missing (suppression would be silence).
        # Before the parity legs it must be, because a missing delta can never
        # certify — and this is the honest route for an unscorable split, so the
        # label reaches the sentence the module already had and nothing reached.
        return "no branch matched — scoring input incomplete"
    if verdict["branch_2_certified"]:
        return "settled (branch 2 certified)"
    if verdict["branch_2_applies"]:
        return "provisional (branch 2 met, not certifiable; branch 3 not reached)"
    if verdict["branch_3_stays_provisional"]:
        return "provisional (branch 3: anything else, by the rule's own terms)"
    return "no branch matched — scoring input incomplete"


def check_protocol_pins(arms_dir: Path, sessions: tuple[str, ...]) -> list[str]:
    """Every receipt in a run must describe the same protocol.

    "Identical protocol" is the whole basis of the comparison, and the risky
    differences are exactly the ones a console print does not survive: a different
    model, chunk size, protocol date, or — the ones this measurement's OWN limits
    section names as decisive — a different prompt/label-order/environment pin,
    since `TORTOISE_LABEL_ORDER=shuffle` reseeds the label order FROM THE STORY and
    so differs between arms at identical prompt length.
    """
    pins: dict[str, dict] = {}
    for sid in sessions:
        rec = json.loads((arms_dir / f"{sid}.arms.json").read_text())
        pins[sid] = {
            "model_id": rec.get("model_id"),
            "chunk_size": rec.get("chunk_size"),
            "session_date": rec.get("session_date"),
            # Only SET pins are compared: an all-unset run is consistent with itself,
            # and a receipt predating the `env_pins` field is not evidence of a
            # difference.
            "env_pins": {k: v for k, v in (rec.get("env_pins") or {}).items() if v},
        }
    errors: list[str] = []
    for key in ("model_id", "chunk_size", "session_date"):
        seen = {json.dumps(p[key]) for p in pins.values()}
        if len(seen) > 1:
            errors.append(f"receipts disagree on {key}: "
                          + "; ".join(f"{s}={p[key]!r}" for s, p in pins.items()))
    env_seen: dict[str, list[str]] = {}
    for sid, p in pins.items():
        env_seen.setdefault(json.dumps(p["env_pins"], sort_keys=True), []).append(sid)
    if len(env_seen) > 1:
        errors.append("receipts disagree on the ENVIRONMENT pins (a differing "
                      "TORTOISE_LABEL_ORDER/prompt/cap changes the prompting, not just the story): "
                      + " | ".join(f"{v} → {p}" for p, v in env_seen.items()))
    return errors


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="score the raw-vs-narrative arms (#7947)")
    ap.add_argument("--sessions", nargs="+", required=True)
    ap.add_argument("--arms-dir", default=str(Path.home() / ".cache" / "pi-7947" / "arms"))
    ap.add_argument("--out", default=str(Path.home() / ".cache" / "pi-7947" / "score.json"))
    args = ap.parse_args(argv)

    # ⛔ The split is pre-registered and FIXED. Running with a subset silently computes
    # the decision input on fewer qualifiers while the output still names the full
    # registered split — a verdict on 6 of 10 qualifiers recorded as the registered
    # one. Refuse instead of guessing.
    missing = [s for s in (*TUNING_SESSIONS, *HOLDOUT_SESSIONS) if s not in set(args.sessions)]
    if missing:
        ap.error(f"the pre-registered split requires all 7 sessions; missing: {', '.join(missing)}")
    # A duplicate is worse than a typo: it passes the completeness check above and
    # silently re-weights the holdout (wp05 listed twice: n=13, Δ=15.4 pp, branch 1
    # FIRES) while the per-session table still prints each session once.
    dupes = sorted({s for s in args.sessions if args.sessions.count(s) > 1})
    if dupes:
        ap.error(f"duplicate session(s) in --sessions: {', '.join(dupes)}")

    arms_dir = Path(args.arms_dir)

    # ⛔ "Identical protocol" is a claim the whole comparison rests on, and the scorer
    # did not check it: receipts from a different model, chunk size, protocol date or
    # environment would score silently as if they were one run. Two arms inside ONE
    # session may never disagree (that would mean the arms differ in more than `story`),
    # and a run must be internally consistent.
    pin_errors = check_protocol_pins(arms_dir, (*TUNING_SESSIONS, *HOLDOUT_SESSIONS))
    if pin_errors:
        ap.error("; ".join(pin_errors))
    _probe = json.loads((arms_dir / f"{TUNING_SESSIONS[0]}.arms.json").read_text())
    print(f"  protocol pins (all {len(TUNING_SESSIONS) + len(HOLDOUT_SESSIONS)} receipts agree): "
          + json.dumps({k: _probe.get(k) for k in ("model_id", "chunk_size", "session_date")}))
    results: list[dict] = []
    pr: list[dict] = []
    for sid in args.sessions:
        for arm in ARMS:
            results.append(score_session(sid, arm, arms_dir))
            pr.append(precision_recall(sid, arm, arms_dir))

    by_session: dict[str, dict[str, dict]] = {}
    for r in results:
        by_session.setdefault(r["session_id"], {})[r["arm"]] = r

    print(f"{'session':24} {'arm':10} {'n':>3} {'pres':>5} {'flat':>5} {'abs':>4} {'loss%':>7}")
    for sid, arms in by_session.items():
        for arm in ARMS:
            r = arms.get(arm)
            if not r:
                continue
            lr = "n/a" if r["loss_rate"] is None else f"{100 * r['loss_rate']:.0f}%"
            print(f"{sid:24} {arm:10} {r['n_qualifiers']:>3} {r['preserved']:>5} "
                  f"{r['flattened']:>5} {r['absent']:>4} {lr:>7}")

    # Aggregate loss rate over pooled qualifiers (equal weight per qualifier).
    agg: dict[str, dict] = {}
    for arm in ARMS:
        rows = [r for r in results if r["arm"] == arm]
        n = sum(r["n_qualifiers"] for r in rows)
        lost = sum(r["loss"] for r in rows)
        matched = sum(r["matched"] for r in rows)
        flat = sum(r["flattened"] for r in rows)
        agg[arm] = {
            "n": n, "loss": lost,
            "loss_rate": (lost / n) if n else None,
            "flattened": flat,
            "absent": sum(r["absent"] for r in rows),
            "matched": matched,
            "distortion_rate": (flat / matched) if matched else None,
            "points": sum(r["points"] for r in rows),
        }
    print()
    for arm in ARMS:
        a = agg[arm]
        lr = "n/a" if a["loss_rate"] is None else f"{100 * a['loss_rate']:.1f}%"
        dr = "n/a" if a["distortion_rate"] is None else f"{100 * a['distortion_rate']:.1f}%"
        print(f"POOLED {arm:10} n={a['n']:<3} loss={a['loss']:<3} "
              f"(flat={a['flattened']} absent={a['absent']}) loss_rate={lr}  "
              f"| matched={a['matched']} distortion={dr}")

    # ── the pre-registered split: the HOLDOUT delta is the decision input ──
    split_agg: dict[str, dict[str, dict]] = {}
    for name, sel in (("tuning", TUNING_SESSIONS), ("holdout", HOLDOUT_SESSIONS)):
        rows = [r for s in sel for r in results if r["session_id"] == s]
        split_agg[name] = {}
        for arm in ARMS:
            rs = [r for r in rows if r["arm"] == arm]
            n = sum(r["n_qualifiers"] for r in rs)
            lost = sum(r["loss"] for r in rs)
            matched = sum(r["matched"] for r in rs)
            flat = sum(r["flattened"] for r in rs)
            split_agg[name][arm] = {
                "n": n,
                "loss": lost,
                "loss_rate": (lost / n) if n else None,
                "matched": matched,
                "flattened": flat,
                "absent": sum(r["absent"] for r in rs),
                "distortion_rate": (flat / matched) if matched else None,
            }

    # Precision/recall on the same split (the other half of branch 1).
    split_pr: dict[str, dict[str, dict]] = {}
    for name, sel in (("tuning", TUNING_SESSIONS), ("holdout", HOLDOUT_SESSIONS)):
        split_pr[name] = {}
        for arm in ARMS:
            rows = [r for r in pr if r["arm"] == arm and r["session_id"] in sel]
            u = sum(r["planted_units"] for r in rows)
            rec = sum(r["recalled"] for r in rows)
            nc = sum(r["claims_emitted"] for r in rows)
            h = sum(r["claims_hit"] for r in rows)
            split_pr[name][arm] = {
                "recall": (rec / u) if u else None,
                "recalled": rec,
                "planted_units": u,
                "precision": (h / nc) if nc else None,
                "claims_hit": h,
                "claims_emitted": nc,
            }

    def _d(a: dict, b: dict, key: str) -> float | None:
        va, vb = a.get(key), b.get(key)
        return None if va is None or vb is None else 100 * (vb - va)

    def _pp_fmt(v: float | None) -> str:
        """Render a delta that may not EXIST — `n/a`, never a `TypeError`.

        `_d()` returns None whenever either arm has no reading for the key, and
        precision/recall are None when an arm emitted zero claims (or the split has
        no planted units). The three deltas used to be formatted unconditionally,
        which killed the scorer with
        ``TypeError: unsupported format string passed to NoneType.__format__``
        BEFORE ``out_path.write_text()`` — so the run that most needs the
        "cannot certify" record produced no artifact at all. The pooled lines below
        already render their rates this way.
        """
        return "n/a" if v is None else f"{v:+.1f}"

    print("\n── THE DECISION INPUT (pre-registered: HOLDOUT only) ──")
    decision: dict[str, dict] = {}
    for name in ("tuning", "holdout"):
        d_loss = _d(split_agg[name]["raw"], split_agg[name]["narrative"], "loss_rate")
        d_prec = _d(split_pr[name]["raw"], split_pr[name]["narrative"], "precision")
        d_rec = _d(split_pr[name]["raw"], split_pr[name]["narrative"], "recall")
        decision[name] = {"delta_loss_pp": d_loss, "delta_precision_pp": d_prec,
                          "delta_recall_pp": d_rec}
        ra, na = split_agg[name]["raw"], split_agg[name]["narrative"]
        print(f"  {name:8} n={ra['n']:<3} loss raw={ra['loss']}/{ra['n']} "
              f"narrative={na['loss']}/{na['n']}  Δloss={_pp_fmt(d_loss)} pp  "
              f"Δprecision={_pp_fmt(d_prec)} pp  Δrecall={_pp_fmt(d_rec)} pp")
    # ⛔ An arm that emitted NO claims has no precision to compare (`hits / 0`), so
    # the split cannot be scored at all. REFUSE the run: with the delta formatted as
    # `n/a` the scorer would otherwise write an artifact whose decision input is an
    # empty extract, and before this guard the unconditioned format string crashed
    # here instead. A run nobody can score is a harness failure, not a measurement.
    unscorable = [name for name in ("tuning", "holdout")
                  if decision[name]["delta_precision_pp"] is None]
    if unscorable:
        ap.error("no precision delta for split(s) " + ", ".join(unscorable)
                 + " — an arm emitted no claims in that split (precision is hits / "
                   "n_claims), so no branch of the rule can be evaluated; fix the "
                   "arm's extract and re-run")
    hold = decision["holdout"]
    # ⛔ A branch is certified only on CLEAN receipts. The extractor's own `partial`
    # flag (rung-4 prefix-accept: the tail of the list was dropped) or a stage error
    # means the arm's output is incomplete, so its loss is overstated — and on this
    # corpus ONE qualifier moves the holdout Δ by 10 pp, i.e. across the band.
    #
    # The keys are PER ARM because the two arms' inputs have different producers:
    # S2 builds both, but S1 builds the NARRATIVE story only. A failed or
    # length-capped S1 chunk therefore contaminates the narrative arm and cannot
    # touch the raw arm's input, while an S2 flag contaminates whichever arm it
    # belongs to.
    contaminated = [
        f"{sid}:{arm}" for sid in HOLDOUT_SESSIONS
        for arm in ARMS
        if any(by_session.get(sid, {}).get(arm, {}).get("receipt_flags", {}).get(k)
               for k in CONTAMINATION_KEYS[arm])
    ]
    verdict = evaluate_branches(hold["delta_loss_pp"], hold["delta_precision_pp"],
                                hold["delta_recall_pp"], contaminated=contaminated)
    fires = verdict["branch_1_fires"]
    parity = verdict["branch_2_applies"]
    certified = verdict["branch_2_certified"]
    branch_3 = verdict["branch_3_stays_provisional"]
    print(f"  branch 1 (falsifier fires: Δloss > +{BAND_PP:.0f} pp or a P/R drop > "
          f"{PR_TOLERANCE_PP:.0f} pp): {'FIRES' if fires else 'does not fire'}")
    print(f"  branch 2 (near-parity: |Δloss| <= {BAND_PP:.0f} pp and |ΔP/R| <= "
          f"{PR_TOLERANCE_PP:.0f} pp):      {'APPLIES' if parity else 'does not apply'}"
          + ("" if not parity else
             ("  → CERTIFIED" if certified else
              "  → NOT CERTIFIED: a holdout receipt is incomplete")))
    if verdict["scoring_input_incomplete"]:
        # A delta the rule reads does not EXIST (an empty planted-unit set gives no
        # recall; an empty extract gives no precision). No branch can be evaluated,
        # so the console must not claim branch 3 APPLIED — the label in the artifact
        # says "scoring input incomplete" and the two must agree case by case.
        print("  ⛔ SCORING INPUT INCOMPLETE: a delta this rule reads does not exist "
              "— no branch can be evaluated.")
        print("  → VERDICT: D3 STAYS PROVISIONAL (unscorable run; not a finding either way)")
    elif branch_3 and parity:
        # Branch 3 is "anything else". When branch 2 APPLIED and was then not
        # certified, branch 3 was not reached — the registration simply has no
        # branch for "met, but the run cannot be trusted". Its EFFECT is adopted and
        # is labelled as such; calling this "branch 3 applies" is a false statement
        # about the rule, and the one a reader will use to call the verdict rigged.
        print("  branch 3 (anything else → stays provisional): NOT REACHED — branch 2 applied "
              "and could not be certified.")
        print("          The registration has no branch for 'met, but the run cannot be trusted';")
        print("          its EFFECT is adopted and recorded here as exactly that.")
        print("          → VERDICT: D3 STAYS PROVISIONAL (uncertifiable parity)")
    else:
        print(f"  branch 3 (anything else → stays provisional): "
              f"{'APPLIES' if branch_3 else 'not reached'}"
              + ("  → VERDICT: D3 STAYS PROVISIONAL" if branch_3 else ""))
    for line in suppression_log(verdict):
        print(line)
    if contaminated:
        print(f"  ⛔ INCOMPLETE RECEIPTS IN THE HOLDOUT: {', '.join(contaminated)}")
        for tag in contaminated:
            sid, arm = tag.split(":")
            fl = by_session[sid][arm]["receipt_flags"]
            print(f"     {tag}: partial={fl['partial']} truncated={fl['truncated']} "
                  f"s2_error={fl['s2_error']} s1_error={fl['s1_error']} "
                  f"s1_truncated={fl['s1_truncated']}")
        print("     → that arm's output is not the extract it should have produced; its loss is"
              " overstated and a")
        print("       Δ that sits near the band cannot be certified. Re-run that arm before deciding.")
    print("  ⚠️ the holdout delta is ONE qualifier wide (10 pp here); read the split and the")
    print("     anchor-subset lines, not just the sign. The pooled delta below is NOT the decision input.")

    # ── the bound / unbound subset the anchor strictness depends on ──
    subset: dict[str, dict[str, dict]] = {}
    for name, sel in (("tuning", TUNING_SESSIONS), ("holdout", HOLDOUT_SESSIONS)):
        subset[name] = {}
        for arm in ARMS:
            rows = [r for s in sel for r in by_session.get(s, {}).get(arm, {}).get("rows", [])]
            for label, keep in (("bound", True), ("unbound", False)):
                rs = [r for r in rows if bool(r.get("bound_claim")) is keep]
                n = len(rs)
                lost = sum(1 for r in rs if r["outcome"] in ("flattened", "absent"))
                subset[name].setdefault(arm, {})[label] = {
                    "n": n, "loss": lost,
                    "loss_rate": (lost / n) if n else None,
                }
    print("\n── ANCHOR SUBSET (#7947 declares 9 of 25 unbound; they use their own words) ──")
    for name in ("tuning", "holdout"):
        for label in ("bound", "unbound"):
            ra = subset[name]["raw"][label]
            na = subset[name]["narrative"][label]
            if not ra["n"] or not na["n"]:
                continue
            d = 100 * (na["loss_rate"] - ra["loss_rate"])
            print(f"  {name:8} {label:7} n={ra['n']:<3} raw={ra['loss']}/{ra['n']} "
                  f"narrative={na['loss']}/{na['n']}  Δloss={d:+.1f} pp")

    delta = None
    ddelta = None
    if agg["raw"]["loss_rate"] is not None and agg["narrative"]["loss_rate"] is not None:
        delta = agg["narrative"]["loss_rate"] - agg["raw"]["loss_rate"]
        print(f"\nDELTA loss  (narrative - raw, POOLED) = {100 * delta:+.1f} pp"
              f"   [NOT the decision input — see the holdout delta above]")
    if agg["raw"]["distortion_rate"] is not None and agg["narrative"]["distortion_rate"] is not None:
        ddelta = agg["narrative"]["distortion_rate"] - agg["raw"]["distortion_rate"]
        print(f"DELTA flat  (narrative - raw, POOLED) = {100 * ddelta:+.1f} pp"
              f"   [distortion among claims that survived; volume-sensitive — §10]")

    for arm in ARMS:
        rows = [r for r in pr if r["arm"] == arm]
        u = sum(r["planted_units"] for r in rows)
        rec = sum(r["recalled"] for r in rows)
        nc = sum(r["claims_emitted"] for r in rows)
        h = sum(r["claims_hit"] for r in rows)
        rr = f"{100 * rec / u:.1f}%" if u else "n/a"
        pp = f"{100 * h / nc:.1f}%" if nc else "n/a"
        print(f"PLANTED {arm:10} recall={rr} ({rec}/{u})  precision={pp} "
              f"({h}/{nc} claims match a planted unit)")

    out = {
        "instrument": {
            "kind": "deterministic-same-class-markers",
            "markers": {k: list(v) for k, v in MARKERS.items()},
            "claim_present_threshold": 0.6,
            "marker_scope": "matched claim only (not the whole output)",
            # Two-sided, and the artifact records it: under-credit from missing
            # vocabulary, over-credit from unbounded substring matching (a
            # mid-word hit — "commonly" reads as scope's "only"). Word-bounding
            # the table would remove the second class, but the table is the
            # REGISTERED instrument, so that belongs in a newly registered run.
            "note": ("TWO-SIDED: under-credits preservation when the marker "
                     "vocabulary is missing (a preserved hedge reads as "
                     "flattened), and OVER-credits it when an unbounded "
                     "substring match hits mid-word (e.g. 'commonly' matches "
                     "scope's 'only'), which understates loss; the over-credit "
                     "is not arm-neutral (more claim text = more chances) — "
                     "flattened stays the arbitrable class"),
        },
        "aggregate": agg,
        "precision_recall": pr,
        "decision_input": {
            "split": "holdout",
            "sessions": list(HOLDOUT_SESSIONS),
            "band_pp": BAND_PP,
            "pr_tolerance_pp": PR_TOLERANCE_PP,
            "branches": decision,
            "branch_1_fires": fires,
            "branch_2_applies": parity,
            "branch_2_certified": certified,
            "branch_3_stays_provisional": branch_3,
            "contaminated_receipts": contaminated,
            "falsifier_suppressed_by_contamination":
                verdict["falsifier_suppressed_by_contamination"],
            "verdict": verdict_label(verdict),
            "certification_note": (
                "a branch is certified only on clean receipts; `partial`/`truncated`/"
                "s2_error means the arm's extract is incomplete and its loss overstated, "
                "and on the narrative arm `s1_error`/`s1_truncated` means its STORY input "
                "is missing a chunk for the same reason"),
            "one_qualifier_is_pp": 100 / max(1, split_agg["holdout"]["raw"]["n"]),
        },
        "splits": {"aggregate": split_agg, "precision_recall": split_pr,
                   "anchor_subset": subset},
        "delta_pp": None if delta is None else 100 * delta,
        "delta_distortion_pp": None if ddelta is None else 100 * ddelta,
        "delta_pp_is_decision_input": False,
        "band_pp": BAND_PP,
        "per_session": by_session,
    }
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2))
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
