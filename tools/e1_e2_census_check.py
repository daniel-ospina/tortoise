"""The E1+E2 evidence log's structural gate (#9076).

**What this closes.** An evidence log is a claim. This is the mechanical check
that the CLAIM IS STRUCTURED so it cannot be read as more than it is: every
numerator has a denominator, the denominators are the same corpus, the anti-gaming
control is present, every dropped value names its cause, the coverage deficit is
stated, and no criterion is called PASS without a pre-registered threshold. It
does **not** re-derive the numbers — the probe owns them — and it cannot detect a
forged log (a plausible number would pass), which is the same stated limit the
sibling capstone gate carries. What it CAN do, and does, is refuse a log whose
numbers disagree with each other: every criterion line is reconciled against the
``arm=`` lines it summarizes — ``e1`` (V3), ``e1-control`` (V5), ``e2-state`` (V6)
and ``e2-atomic`` (V7) — each E2 criterion's verdict is checked against its own
before/after counts (V5-V7), and the ``e1`` line's ``observed`` conclusion against
its own ``reduction`` (V10). A reduction borrowed from a counterfactual arm, or a
state-value table that shrank under a ``PASS``, is therefore a finding rather than
a pass (#9110 reviews).

The MECHANISM the bundle's finding rests on is checked the same way (V13/V14):
the scored arm's ``metric=vet`` block must show the gate ENABLED, and its
``candidates``/``discarded`` counts must be its own ``by_outcome`` table, because
a gate that never ran also produces 0.0 pp — arithmetic-identical to the result
this bundle publishes. ``metric=vet-downgrades count=`` is likewise reconciled
with the ``vet-warning=`` lines that report each downgrade. Both held on the
committed log when they were added; a future drift in either direction is now a
finding instead of a differently-worded story (#9110 review P2).

**V15 is the bar's POPULATION half** (#9076). V9 checks that the threshold the run
used is the one the artifact froze; V15 checks that the artifact's bar was frozen
over the population this log MEASURED — a matching ``corpus_sha256``, a named pin
(``declared_path``/``declared_sha256``, with the declared sha reconciled against
the measured bytes when one is given), and a denominator reconciled with the scored
arm's own ``denominator=``. A SCORED log must carry a ``MATCHED`` binding, and a
``MATCHED`` binding must carry no refusal reason, so a refusal relabelled
``MATCHED`` is a finding rather than a verdict; a floor the binding could not
classify is printed on ``floor_unconsumed=`` and must be the reason it refused,
because a silently dropped floor is a bar its author did not write.

⚠️ A floor is only DROPPED if it is unaccounted for (#11689). V15 requires every
declared floor to land in exactly one bucket — ``floors_owned=`` (a row this probe
scores, so the floor is CHECKED, with its declared and measured sides both on the
line), ``floor_skipped=`` (a row another instrument scores, printed as
``key:CRITERION`` so the skip is declared instead of silent), or
``floor_unconsumed=`` (unclassified, and then it is the refusal). The old shape had
one bucket, so a floor on ANOTHER criterion's row voided the run — a probe that
refuses everything enforces nothing.

⚠️ What the LOG side cannot do, said plainly so the green is not read as more than
it is: V15 requires all six floor sides on a scored binding line (two bucket names
and the declared/measured pair for each owned floor), so the line must be complete
and internally consistent — but `floors_owned=` is the probe's OWN statement. A log
carries no copy of `denominator_floors`, so a hand-edited log that claims it owned
fewer floors, blanks the two declared sides and keeps `objects=` yields NO V15
finding. ⛔ Nothing in the tree closes that: `check_machine_record` (in
``tools/capstone_preregistration_check.py``) compares the RECORD with its
``pins.denominator_floors`` and never opens a log. It is an OPEN, stated residual —
not one this module can delegate.

Printing is not enough on its own: every finding below fails the run, because a
log that omits its denominator is exactly the artifact this epic exists to refuse.
That includes OMITTING IT ENTIRELY: the disclosure set is REQUIRED (four arms by
name, the mint-skip transform, one value-literal line per arm, the per-class
coverage table and the discriminating control), so a log with those lines DELETED
is a finding rather than a smaller measurement — every other rule reconciles only
the lines it is given, and an absent line reconciled against nothing used to pass
(#9110 review P2, measured as three separate deletions).

Usage (``uv run`` — a bare ``python``/``python3`` may be <3.12, and the
interpreter guard above refuses it with exit 2):
    uv run python tools/e1_e2_census_check.py docs/evidence/7949-capstone/e1-e2.log
    uv run python tools/e1_e2_census_check.py <log> --require-green   # exit 1 unless GREEN
"""
from __future__ import annotations

import sys

# #5128 shape: refuse a <3.12 interpreter before the imports below — a module-level
# 3.12+-only construct would otherwise fail first, and it would fail with a
# traceback instead of this message. The gate that enforces this shape is
# tests/test_entry_point_python_guard.py, not a style rule.
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    # 2 is this tool's own "environment/usage error" code (`main` returns 1 only
    # for FINDINGS, 3 for an unreadable log), so an old interpreter stays legible
    # in a runner that keys on the exit code alone.
    print(
        f"tools/e1_e2_census_check.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/e1_e2_census_check.py`",
        file=sys.stderr,
    )
    raise SystemExit(2)

import argparse
import json
import math
import re
from pathlib import Path

ARM_LINE = re.compile(r"^arm=(?P<arm>[a-z0-9-]+)\s+(?P<rest>.*)$")
KV = re.compile(r"(?P<key>[a-z0-9_]+)=(?P<val>\"[^\"]*\"|'[^']*'|\S+)")
OBJECTS_LINE = re.compile(r"^arm=(?P<arm>[a-z0-9-]+) metric=objects ")
DROP_POINT_LINE = re.compile(
    r"^arm=(?P<arm>[a-z0-9-]+) dropped kind=point content=(?P<content>.*) "
    r"cause=(?P<cause>\S*)\s+values=(?P<values>\[.*\])$")
FUSED_LINE = re.compile(r"^e2-atomic fused content=.* cause=(?P<cause>\S+)$")
#: The floors THIS probe scores and therefore must COMPARE (#11689). Stated here
#: independently of the probe's own `OWNED_FLOORS`: a gate that reads the list it
#: is checking cannot refuse a log whose list was edited.
OWNED_KEYS = ("e1_objects", "e1_min_before_share", "e2b_surviving_units")
CRITERIA = ("e1", "e1-control", "e2-state", "e2-atomic before=")
VERDICTS = {"PASS", "FAIL", "UNPREREGISTERED"}

#: The arms the measurement is DEFINED by, and which must therefore be PRESENT.
#: Requiring them is the difference between "reconcile whatever lines are here"
#: and "a disclosure cannot be deleted": before this list existed the whole
#: counterfactual set — both arms, the mint-skip transform and the two
#: ``counterfactual arm=`` estimates — could be deleted and the gate still exited
#: 0, taking the 22.5 pp estimate and the arbiter arm (the bundle's central
#: finding) with it (#9110 review P2). A renamed arm fails here on purpose: the
#: disclosure set is part of the contract, so changing it must move this list.
REQUIRED_ARMS = ("before", "after-vet-shipped", "after-vet-arbiter",
                 "counterfactual-mint-skip")
LITERALS_LINE = re.compile(r"matches=(?P<matches>\[.*\])$")
#: ``by_outcome=`` is a JSON object with spaces in it, so it is read with a
#: DEDICATED pattern: the shared ``KV`` regex stops at the first space and yields
#: ``{"DISCARD":``, which ``json.loads`` raises on. ``LITERALS_LINE`` beside it is
#: the same shape for the same reason.
BY_OUTCOME_LINE = re.compile(r"by_outcome=(?P<by>\{.*?\})\s+mechanical=")
MECHANICAL_LINE = re.compile(r"mechanical=(?P<mech>\{.*\})\s*$")
REMOVED_NAMES_LINE = re.compile(r"removed_names=(?P<names>\[.*\])\s+note=")
#: The marker ``apply_vet`` Rule 2 emits once per downgraded discard. It is the
#: only place a downgrade is named, so it is what ``vet-downgrades count=`` counts.
KEPT_MARKER = "kept (Layer-1 referential integrity)"


def _num(value: object) -> float | int | None:
    """The log field as a comparable number, or ``None`` when it is not one.

    ``n/a`` (the probe's word for "no floor declared") and ``""`` (absent) are
    NOT numbers, and neither is a JSON-quoted string: a helper that accepted them
    would let an unreadable floor read as a met one, which is the permissive read
    ``_declared_floor`` was fixed to refuse on the probe's side (#9513 review P2).

    MAGNITUDE-SAFE (#11689 review round 7): a floor whose decimal text exceeds
    float range — ``10 ** 400`` — is perfectly readable to the probe, which compares
    ints, so routing it through ``float()`` made the GATE reject the probe's own
    correct refusal as "no readable pair". Integral text outside float range is read
    as an ``int``; only text that is neither finite nor integral is ``None``.
    """
    text = str(value).strip()
    if not text or text == "n/a":
        return None
    try:
        number = float(text)
    except (TypeError, ValueError, OverflowError):
        return None
    if math.isfinite(number):
        return number
    try:
        return int(text)
    except (TypeError, ValueError):
        return None


def _is_number(value: object) -> bool:
    """Is this log field a plain number this gate can compare?"""
    return _num(value) is not None


#: Half the resolution of ``before_share=``, which ``_fmt`` prints at 6 dp. A printed
#: share is therefore uncertain by ±5e-7, and that — not a round 5e-6 — is the band
#: every V15 share rule may use (#11689 review rounds 11 and 12). Two shapes:
#: the floor rules compare ONE printed value against an EXACT declared floor, and the
#: arm anchor compares two prints of the SAME census float (``_fmt(cen['share'])`` on
#: the arm line, ``before_c["share"]`` on the binding), so a legitimate log's two sides
#: are identical strings and the band is headroom for float parsing — not room for
#: them to disagree. A 5e-6 tolerance there let a binding line drift four printed steps
#: off the run's value while the gate still called it GREEN.
_SHARE_PRINT_BAND = 5e-7


def _below_floor_beyond_rounding(measured: object, floor: object) -> bool:
    """Is ``measured`` under ``floor`` by MORE than one print-ulp?

    ``before_share=`` prints via ``_fmt`` (6 dp) while the probe compares at full
    precision, so a value that is genuinely below its floor can print equal to it, or
    a full ulp above it. The band is one print half-width (``_SHARE_PRINT_BAND``) and
    is used in BOTH rules so the two are polarity-consistent (#11689 review round 8,
    corrected in round 11 — the first revision used 5e-6, ten times the printing
    resolution, which swallowed real below-floor gaps and still red the ulp-above
    case).

    Subtracted rather than added to a tolerance because ``_num`` deliberately returns
    an ``int`` for a magnitude outside float range — `10 ** 400` (round 7) — and
    ``int + 5e-7`` raises ``OverflowError``. Every comparison here is exact.
    """
    m, f = _num(measured), _num(floor)
    if m is None or f is None or not m < f:
        return False
    if isinstance(m, int) and isinstance(f, int):
        return (f - m) > _SHARE_PRINT_BAND   # exact integer arithmetic
    try:
        return (f - m) > _SHARE_PRINT_BAND
    except OverflowError:                    # one side is outside float range,
        return True                          #  so the gap is far beyond any rounding


class Log:
    def __init__(self, text: str):
        self.text = text
        self.lines = [ln.rstrip("\n") for ln in text.splitlines() if ln.strip()]

    def find(self, prefix: str) -> list[str]:
        return [ln for ln in self.lines if ln.startswith(prefix)]

    def one(self, prefix: str) -> str | None:
        hits = self.find(prefix)
        return hits[-1] if hits else None

    @staticmethod
    def kv(line: str | None) -> dict[str, str]:
        if not line:
            return {}
        return {m.group("key"): m.group("val").strip("\"'")
                for m in KV.finditer(line)}


def check(text: str) -> tuple[list[str], dict]:
    findings: list[str] = []
    log = Log(text)
    state: dict = {}

    # ── V1: identity of the run ───────────────────────────────────────────
    if not log.one("E1E2-PROBE "):
        findings.append("[V1] the log does not start with an E1E2-PROBE header")
    head = log.kv(log.one("sha="))
    if not log.one("sha=") or len(head.get("sha", "")) < 7:
        findings.append("[V1] the log does not record the sha it was measured at")

    # ── V2: one corpus, one scored arm ────────────────────────────────────
    corpus = log.kv(log.one("corpus="))
    if not corpus.get("corpus_sha256"):
        findings.append("[V2] the log does not record the corpus's sha256 — the "
                        "before and after sides could be different corpora")
    for key in ("objects", "points"):
        if not corpus.get(key, "").isdigit():
            findings.append(f"[V2] the corpus population is not stated ({key}=)")
    scored = log.kv(log.one("scored_arm=")).get("scored_arm", "")
    if not scored:
        findings.append("[V2] the log does not name the scored arm — an unstated "
                        "arm makes the metric uninterpretable")
    arms = sorted({m.group("arm") for ln in log.lines
                   for m in [ARM_LINE.match(ln)] if m})
    state["arms"] = arms
    # The per-arm first line carries counterfactual=; keep the first occurrence.
    seen: dict[str, str] = {}
    for ln in log.find("arm="):
        kv = log.kv(ln)
        a = kv.get("arm")
        if a and "counterfactual" in kv and a not in seen:
            seen[a] = kv["counterfactual"]
    if seen.get(scored) == "yes":
        findings.append(f"[V2] the SCORED arm ({scored}) is a counterfactual — a "
                        "mechanism that does not exist in production cannot be "
                        "the product's measurement")
    if not any(v == "no" for v in seen.values()):
        findings.append("[V2] no non-counterfactual arm is present")
    # ── V2 (cont.): the disclosure set is REQUIRED, never conditional ─────
    # Every rule below reconciles the lines it is GIVEN, so an ABSENT line was
    # indistinguishable from a satisfied one. A log with its disclosures deleted
    # is not a smaller measurement — it is the same claim with the parts that
    # could contradict it removed (#9110 review P2, measured as M1/M2/M3).
    for required in REQUIRED_ARMS:
        if required not in arms:
            findings.append(f"[V2] the log omits the required arm {required!r} — "
                            f"an absent arm is not a pass")
    before_arm = next((a for a in arms
                       if seen.get(a) == "no" and a != scored), None)
    cf_arms = [a for a in arms if seen.get(a) == "yes"]
    if not cf_arms:
        findings.append("[V2] the log carries no counterfactual arm — the estimate "
                        "of what a mechanism that does not exist would buy is part "
                        "of the measurement, not an optional extra")

    # ── V3 + V4: numerators, denominators, and their equality ─────────────
    shares: dict[str, float] = {}
    totals: dict[str, int] = {}
    #: E2b's population per arm — the ANCHOR for the binding's `surviving_units=`,
    #: and the same quantity `capstone_measure`/`e1_e2_scores` compare that floor
    #: against (one frozen key, one denominator — #11689 review). ⛔ It is the
    #: surviving PLANTED units (`real_facts`), never `atomic_points`: the record's
    #: §3.1 E2b row is written over "the planted units that survived (floor ≥ 30)",
    #: while `atomic_points` counts every payload point — so 25 non-planted rows
    #: plus 6 planted ones cleared a floor of 30 with 84 of 90 planted units lost
    #: (#10989).
    planted_pts: dict[str, int] = {}
    #: Nothing is accumulated for the atomicity control: it is VALIDATED below
    #: (a non-count `atomic=` token must be a V3 finding, not an absent line) and
    #: then dropped. There is no accumulator because there is no reader — V5/V6/V7
    #: go through `arm_metric()` straight off the log (V7 words an ABSENT arm line
    #: itself: `arm_metric` returns `{}`, so its row reads "prints no
    #: atomic_points"), and V15's anchor is `planted_pts` (#10989). The
    #: `state["atomic_points"]` publish that used to sit here was write-only and is
    #: gone (#11833 review r1 P2-2, r2 P2-1).
    pre_shas: dict[str, str] = {}
    for arm in arms:
        line = next((ln for ln in log.find(f"arm={arm} metric=objects ")), None)
        kv = log.kv(line)
        if line is None:
            findings.append(f"[V3] arm {arm} prints no objects line")
            continue
        try:
            num = int(kv["numerator"])
            den = int(kv["denominator"])
            share = float(kv["share"])
        except (KeyError, ValueError):
            findings.append(f"[V3] arm {arm}: numerator/denominator/share missing "
                            f"or unparseable ({kv})")
            continue
        if den <= 0:
            findings.append(f"[V3] arm {arm}: denominator is {den}")
        elif num > den:
            findings.append(f"[V3] arm {arm}: numerator {num} exceeds denominator {den}")
        elif not math.isclose(share, num / den, rel_tol=0, abs_tol=5e-6):
            findings.append(f"[V3] arm {arm}: share {share} is not {num}/{den} "
                            f"({num / den:.6f}) — the share is not the printed counts")
        shares[arm] = share
        totals[arm] = den
        pre = log.kv(next((ln for ln in log.find(f"arm={arm} ")
                           if "preg_embed_sha=" in ln), None))
        if pre.get("preg_embed_sha"):
            pre_shas[arm] = pre["preg_embed_sha"]
        # E2b's denominator per arm — the ANCHOR for the binding's
        # `surviving_units=`. Unparseable is a finding rather than a skip: an
        # unreadable population is not an absent one (same rule as `objects`).
        #: FIRST match, like every other reader of the arm data (`shares`/`totals`
        #: at :294 and V7's `arm_metric` at :461) — `Log.one()` returns the LAST.
        #: #11689 review round 9 (T4): an appended duplicate arm line re-anchored
        #: the binding's `surviving_units=` to a population the run did not
        #: produce, so a run the E2b row refuses certified GREEN.
        ap_line = next((ln for ln in log.find(f"arm={arm} metric=atomic_points ")), None)
        if ap_line is not None:
            ap = log.kv(ap_line).get("atomic", "")
            #: VALIDATE ONLY — the value is not kept: anything reading the atomicity
            #: control reads it off the log (`arm_metric()`), so an accumulator here
            #: would be dead weight (#11833 review r2 P2-1). The check itself earns
            #: its keep: `arm_metric()` reads a missing key as an ABSENT line, so a
            #: non-count token has to be refused HERE or it would read as "no
            #: atomic_points printed" rather than as the defect it is.
            if not ap.isdigit():
                findings.append(
                    f"[V3] arm {arm}: metric=atomic_points atomic={ap!r} is "
                    f"not a count — the atomicity control cannot be reconciled")
        # The binding's `surviving_units=` anchor: the SAME population the E2b row's
        # floor is written over — the surviving PLANTED units, which the probe prints
        # on `metric=real_facts count=`. Read from the arm's OWN line, first match,
        # for the reason stated above: an appended duplicate must not re-anchor it.
        rf_line = next((ln for ln in log.find(f"arm={arm} metric=real_facts ")), None)
        if rf_line is not None:
            rf = log.kv(rf_line).get("count", "")
            if rf.isdigit():
                planted_pts[arm] = int(rf)
            else:
                findings.append(
                    f"[V3] arm {arm}: metric=real_facts count={rf!r} is not a "
                    f"count — E2b's population cannot be reconciled")
    state["shares"] = shares

    # ── V3 (cont.): the `e1` line reconciles with the arm lines ───────────
    # The `e1` line is the one line the VERDICT is read from (V9/V10), and it
    # prints every operand the arm lines print: the before and after shares and
    # the reduction between them. Without this reconciliation the log could
    # claim the shipped arm reduced E1 by the COUNTERFACTUAL's margin — the
    # number this bundle exists to attribute to a mechanism that does not exist —
    # and pass every other check, because V3 reconciled only the arm lines with
    # themselves. Within-log arithmetic on printed counts, the same class of
    # check V3 already does: it still cannot detect a wholly forged log.
    e1_kv = log.kv(log.one("e1 "))
    if before_arm is None or scored not in shares or before_arm not in shares:
        findings.append("[V3] the e1 line cannot be reconciled: the before arm "
                        f"({before_arm!r}) or the scored arm ({scored!r}) prints "
                        "no objects share")
    else:
        try:
            e1_before = float(e1_kv["before_share"])
            e1_after = float(e1_kv["after_share"])
            e1_reduction = float(e1_kv["reduction"])
        except (KeyError, ValueError):
            findings.append("[V3] the e1 line does not print before_share, "
                            f"after_share and reduction ({e1_kv})")
        else:
            if not math.isclose(e1_before, shares[before_arm], rel_tol=0,
                                abs_tol=5e-6):
                findings.append(
                    f"[V3] the e1 line's before_share={e1_before} is not the "
                    f"{before_arm} arm's share={shares[before_arm]} — the line "
                    f"the verdict is read from disagrees with its own counts")
            if not math.isclose(e1_after, shares[scored], rel_tol=0,
                                abs_tol=5e-6):
                findings.append(
                    f"[V3] the e1 line's after_share={e1_after} is not the "
                    f"scored arm's ({scored}) share={shares[scored]}")
            expected_reduction = shares[before_arm] - shares[scored]
            if not math.isclose(e1_reduction, expected_reduction, rel_tol=0,
                                abs_tol=5e-6):
                findings.append(
                    f"[V3] the e1 line's reduction={e1_reduction} is not the "
                    f"arm lines' difference (before - after = "
                    f"{expected_reduction:.6f}) — the movement claimed is not "
                    f"the printed counts")

    # ── V3 (cont.): the value-literal disclosure, one line per arm ────────
    # `values=42` is a claim about a set of literals, and every arm prints the
    # set (``metric=state_value_literals``). Requiring the line and reconciling
    # its decoded length with the count is what makes ``E1-E2.md`` limit 5's
    # "every match is printed, so a reader audits the literals" true of the log
    # rather than of the code — deleting the lines used to pass (#9110 review P2).
    for arm in arms:
        declared = log.kv(next((ln for ln in log.find(
            f"arm={arm} metric=state_value_points ")), None)).get("values", "")
        lit = log.one(f"arm={arm} metric=state_value_literals ")
        if lit is None:
            findings.append(f"[V3] arm {arm} prints no state_value_literals line — "
                            f"the count in values= is unauditable")
            continue
        m = LITERALS_LINE.search(lit)
        try:
            parsed = json.loads(m.group("matches")) if m else None
        except json.JSONDecodeError:
            parsed = None
        if not isinstance(parsed, list) or not all(
                isinstance(x, list) and len(x) == 2
                and all(isinstance(y, str) for y in x) for x in parsed):
            findings.append(f"[V3] arm {arm}: the state_value_literals line does "
                            f"not carry a [class, literal] list")
        elif declared.isdigit() and len(parsed) != int(declared):
            findings.append(
                f"[V3] arm {arm}: the value count says {declared} but the printed "
                f"literals are {len(parsed)} — the number is not the set it counts")

    # ── V4 (cont.): each counterfactual's estimate reconciles with its arm ─
    # The `counterfactual arm=` line is the ONLY place the bundle's "what a
    # mint-time rule would buy" figure is stated, so it is reconciled with the
    # arm's own share the same way the `e1` line is (#9110 review P2: it could be
    # deleted, or disagree with the counts, and nothing noticed).
    for arm in cf_arms:
        line = log.one(f"counterfactual arm={arm} ")
        if line is None:
            findings.append(f"[V4] counterfactual arm {arm} states no "
                            f"`counterfactual arm=` estimate — the arm is present "
                            f"but what it would buy is not")
            continue
        kv = log.kv(line)
        if (before_arm in shares and arm in shares
                and kv.get("reduction", "").replace("-", "").replace(".", "").isdigit()):
            expected = shares[before_arm] - shares[arm]
            if not math.isclose(float(kv["reduction"]), expected, rel_tol=0,
                                abs_tol=5e-6):
                findings.append(
                    f"[V4] counterfactual arm {arm}: reduction={kv['reduction']} is "
                    f"not the arm lines' difference ({expected:.6f}) — the estimate "
                    f"is not the printed counts")
        elif before_arm in shares and arm in shares:
            findings.append(f"[V4] counterfactual arm {arm} states no readable "
                            f"reduction")
        facts = log.kv(next((ln for ln in log.find(
            f"arm={arm} metric=real_facts ")), None)).get("count", "")
        values = log.kv(next((ln for ln in log.find(
            f"arm={arm} metric=state_value_points ")), None)).get("values", "")
        for key, printed in (("real_facts", facts), ("state_values", values)):
            if kv.get(key, "").isdigit() and printed.isdigit() \
                    and int(kv[key]) != int(printed):
                findings.append(
                    f"[V4] counterfactual arm {arm}: {key}={kv[key]} is not the "
                    f"arm's own {printed} — the estimate disagrees with its arm")

    # ── V3 (cont.) / V5-V7: EVERY criterion line reconciles with its arm lines ──
    # `e1` is reconciled above. The three sibling criteria are printed by the
    # probe from the same `census()` and were, until the #9110 round-6 review,
    # never checked against the `arm=` lines they summarize — so the anti-gaming
    # control could print one number while its arm table printed another, and a
    # state-value table could shrink under a `PASS`, with every V-check green.
    # Same within-log arithmetic class as V3: it still cannot detect a wholly
    # forged log, only a log that disagrees with itself.
    criteria_pairs = (
        # (finding tag, criterion, arm metric line, arm metric key,
        #  criterion before key, criterion after key)
        ("V5", "e1-control", "real_facts", "count",
         "before_real_facts", "after_real_facts"),
        ("V6", "e2-state", "state_value_points", "values", "before", "after"),
        ("V7", "e2-atomic", "atomic_points", "atomic", "before", "after"),
    )

    def arm_metric(arm: str | None, metric: str) -> dict[str, str]:
        return log.kv(next((ln for ln in log.find(f"arm={arm} metric={metric} ")),
                           None))

    for tag, name, metric, arm_key, kb, ka in criteria_pairs:
        prefix = "e2-atomic before=" if name == "e2-atomic" else f"{name} "
        crit = log.kv(log.one(prefix))
        for crit_key, arm in ((kb, before_arm), (ka, scored)):
            printed = crit.get(crit_key, "")
            if not printed.isdigit():
                continue  # V5/V6/V7 already report the missing side
            arm_line = arm_metric(arm, metric)
            arm_value = arm_line.get(arm_key, "")
            if not arm_value.isdigit():
                findings.append(
                    f"[{tag}] the {name} line cannot be reconciled: arm {arm!r} "
                    f"prints no {metric} {arm_key}=")
            elif int(printed) != int(arm_value):
                findings.append(
                    f"[{tag}] the {name} line's {crit_key}={printed} is not the "
                    f"{arm} arm's {metric} {arm_key}={arm_value} — the line the "
                    f"criterion is read from disagrees with its own counts")

    # The two E2 criteria carry the anti-gaming control, so each verdict is checked
    # against its own printed counts: a loss may never read as a pass (#9110 P1).
    # ── V13/V14: the MECHANISM block — the scored arm's gate RAN ──────────
    # The bundle's central finding is a mechanism: TORTOISE_VET=1 fired on 3
    # candidates and ``apply_vet`` Rule 2 downgraded all 3, so E1 could not move.
    # Until this rule existed NO part of the ``metric=vet`` / ``metric=vet-
    # downgrades`` / ``vet-warning=`` block was read, so the scored arm could print
    # ``enabled=no``, ``discarded=0`` and no warnings at all and still be called
    # structurally sound — a gate that never ran produces the same 0.0 pp as one
    # that ran and could not move, so nothing else in the log shifted (#9110
    # review P2, measured as five separate accepted mutations).
    for arm in arms:
        vet_lines = log.find(f"arm={arm} metric=vet ")
        if not vet_lines:
            findings.append(f"[V13] arm {arm} prints no metric=vet line — whether "
                            f"the gate RAN on this arm is then unstated, and 0.0 pp "
                            f"from a gate that never ran is indistinguishable from "
                            f"one that could not move")
        for ln in vet_lines:
            kv = log.kv(ln)
            pass_name = kv.get("pass", "?")
            enabled = kv.get("enabled", "")
            mech_m = MECHANICAL_LINE.search(ln)
            try:
                mech = json.loads(mech_m.group("mech")) if mech_m else None
            except json.JSONDecodeError:
                mech = None
            if enabled not in ("yes", "no"):
                findings.append(f"[V13] arm {arm} (pass={pass_name}) does not state "
                                f"enabled= — the gate's on/off state is not printed")
            elif enabled == "no" and arm != before_arm:
                # `no` is legal ONLY on the declared baseline arm: every other arm
                # is a measurement of the gate, and one with the gate off says
                # nothing about it.
                findings.append(
                    f"[V13] arm {arm} (pass={pass_name}) prints enabled=no — only "
                    f"the baseline arm ({before_arm}) may run with the gate off; a "
                    f"scored or counterfactual arm with the gate disabled measures "
                    f"no mechanism at all")
            if isinstance(mech, dict) and "enabled" in mech \
                    and enabled in ("yes", "no") \
                    and bool(mech["enabled"]) != (enabled == "yes"):
                findings.append(
                    f"[V13] arm {arm} (pass={pass_name}) prints enabled={enabled} "
                    f"beside mechanical enabled={mech['enabled']} — the two "
                    f"disclosures of the same switch disagree")
            by_m = BY_OUTCOME_LINE.search(ln)
            try:
                by = json.loads(by_m.group("by")) if by_m else None
            except json.JSONDecodeError:
                by = None
            if not isinstance(by, dict) or not all(
                    isinstance(v, int) and not isinstance(v, bool) for v in by.values()):
                findings.append(
                    f"[V13] arm {arm} (pass={pass_name}): by_outcome= is not a JSON "
                    f"object of integer counts — the outcome table the discard "
                    f"arithmetic is read from is unreadable")
                continue
            cand = kv.get("candidates", "")
            if not cand.isdigit():
                findings.append(f"[V13] arm {arm} (pass={pass_name}) does not state "
                                f"candidates=")
            elif int(cand) != sum(by.values()):
                findings.append(
                    f"[V13] arm {arm} (pass={pass_name}): candidates={cand} is not "
                    f"the sum of by_outcome= ({sum(by.values())}) — the "
                    f"candidate count is not its own outcome table")
            disc = kv.get("discarded", "")
            if not disc.isdigit():
                findings.append(f"[V13] arm {arm} (pass={pass_name}) does not state "
                                f"discarded=")
            elif int(disc) != by.get("DISCARD", 0):
                findings.append(
                    f"[V13] arm {arm} (pass={pass_name}): discarded={disc} is not "
                    f"the DISCARD outcome count ({by.get('DISCARD', 0)}) — the "
                    f"number of removals is not the table beside it")

    for arm in arms:
        line = log.one(f"arm={arm} metric=vet-downgrades ")
        if line is None:
            findings.append(f"[V14] arm {arm} prints no metric=vet-downgrades line — "
                            f"how many of its discards were downgraded to KEEP is "
                            f"then unstated, which is not the same as none")
            continue
        count = log.kv(line).get("count", "")
        kept = sum(1 for ln in log.find(f"arm={arm} vet-warning=")
                   if KEPT_MARKER in ln)
        if not count.isdigit():
            findings.append(f"[V14] arm {arm}: vet-downgrades does not state count=")
        elif int(count) != kept:
            findings.append(
                f"[V14] arm {arm}: vet-downgrades count={count} but {kept} of its "
                f"vet-warning= line(s) report a discard downgraded to KEEP — the "
                f"count is not the set of downgrades it counts")

    for tag, name, _metric, _key, kb, ka in criteria_pairs[1:]:
        prefix = "e2-atomic before=" if name == "e2-atomic" else f"{name} "
        crit = log.kv(log.one(prefix))
        if crit.get(kb, "").isdigit() and crit.get(ka, "").isdigit():
            before_n, after_n = int(crit[kb]), int(crit[ka])
            if after_n < before_n:
                findings.append(
                    f"[{tag}] the {name} count FELL ({before_n} → {after_n}) — "
                    f"the property this criterion exists to protect was lost")
            verdict = crit.get("verdict", "")
            if verdict == "PASS" and after_n < before_n:
                findings.append(
                    f"[{tag}] {name} is PASS while its own counts fell "
                    f"({before_n} → {after_n})")
            if verdict == "FAIL" and after_n >= before_n:
                findings.append(
                    f"[{tag}] {name} is FAIL while its own counts did not fall "
                    f"({before_n} → {after_n})")

    gate_arms = [a for a in arms if seen.get(a) == "no"]
    gate_shas = {pre_shas.get(a) for a in gate_arms if pre_shas.get(a)}
    if len(gate_shas) > 1:
        findings.append(f"[V4] the non-counterfactual arms do not share one "
                        f"pre-gate population ({sorted(gate_shas)}) — different "
                        f"corpora make the before/after delta meaningless")
    if not gate_shas:
        findings.append("[V4] no pre-gate population sha is recorded for the "
                        "non-counterfactual arms")
    mint = log.kv(log.one("mint_skip "))
    if not mint.get("dropped_objects", "").isdigit():
        findings.append("[V4] the mint-skip transform does not state how many "
                        "objects it removed — the counterfactual's denominator is "
                        "unstated, which is not the same as zero")
    # ── V4 (cont.): the mint-skip's own transform, disclosed not implied ───
    # `dropped_objects` is the denominator of the counterfactual, but the CLAIM
    # the transform makes is about REFERENCES: the Object is removed and the
    # statements it explained are left behind. Those two fields were printed and
    # never read, so they could be deleted — or disagree with the drop table —
    # without a finding (#9110 review P3).
    if mint.get("dropped_objects", "").isdigit():
        for key in ("stripped_references", "statements_left_unreferenced"):
            if not mint.get(key, "").isdigit():
                findings.append(
                    f"[V4] the mint-skip transform does not state {key}= — the "
                    f"transform's claim is about references, and an absent field "
                    f"cannot be read as zero")
        if mint.get("stripped_references", "").isdigit() and mint.get(
                "statements_left_unreferenced", "").isdigit():
            n_refs = int(mint["stripped_references"])
            n_bare = int(mint["statements_left_unreferenced"])
            if n_bare > n_refs:
                findings.append(
                    f"[V4] the mint-skip transform leaves {n_bare} statement(s) "
                    f"unreferenced after stripping only {n_refs} reference(s) — a "
                    f"statement can only lose its last reference by losing one")
            if n_bare > int(mint["dropped_objects"]):
                findings.append(
                    f"[V4] the mint-skip transform leaves {n_bare} statement(s) "
                    f"unreferenced but drops only {mint['dropped_objects']} "
                    f"Object(s) — more statements were emptied than objects removed")
    if mint.get("dropped_objects", "").isdigit():
        n_drop = int(mint["dropped_objects"])
        declared = sum(1 for ln in log.find("arm=") if "dropped kind=object" in ln
                       and "mint-time-transform" in ln)
        if declared != n_drop:
            findings.append(f"[V4] mint-skip removed {n_drop} objects but the drop "
                            f"table attributes {declared} to the mint-time "
                            f"transform — the removal is not fully accounted for")

    # ── V5: the anti-gaming control, both sides ───────────────────────────
    control = log.kv(log.one("e1-control "))
    for key in ("before_real_facts", "after_real_facts"):
        if not control.get(key, "").isdigit():
            findings.append(f"[V5] the real-fact control is not printed on both "
                            f"sides ({key}= missing) — the share could fall only "
                            f"because fewer facts were extracted")
    if (control.get("before_real_facts", "").isdigit()
            and control.get("after_real_facts", "").isdigit()
            and int(control["after_real_facts"]) < int(control["before_real_facts"])):
        findings.append("[V5] the real-fact count FELL "
                        f"({control['before_real_facts']} → "
                        f"{control['after_real_facts']})")

    # ── V6: state values, both sides, and every loss with its cause ─────────
    st = log.kv(log.one("e2-state "))
    for key in ("before", "after"):
        if not st.get(key, "").isdigit():
            findings.append(f"[V6] the state-value count is not printed on both "
                            f"sides ({key}= missing)")
    # `42` is a count of MATCHES over `34` Points (E1-E2.md limit 5), and the
    # Point count was printed but never required or reconciled — so the sentence
    # "over 34 Points" could lose its operand with nothing noticing (#9110 review
    # P3). It reconciles with the same arms `before`/`after` do.
    for crit_key, arm in (("before_points", before_arm), ("after_points", scored)):
        printed = st.get(crit_key, "")
        if not printed.isdigit():
            findings.append(f"[V6] the e2-state line does not state {crit_key}= — "
                            f"the value count is then a number of matches with no "
                            f"denominator of Points")
            continue
        arm_value = arm_metric(arm, "state_value_points").get("points", "")
        if not arm_value.isdigit():
            findings.append(f"[V6] the e2-state line cannot be reconciled: arm "
                            f"{arm!r} prints no state_value_points points=")
        elif int(printed) != int(arm_value):
            findings.append(
                f"[V6] the e2-state line's {crit_key}={printed} is not the {arm} "
                f"arm's state_value_points points={arm_value} — the denominator "
                f"beside the count is not the arm's")
    for ln in log.lines:
        m = DROP_POINT_LINE.match(ln)
        if not m:
            continue
        cause = m.group("cause")
        vals = m.group("values")
        if cause in ("", "n/a", "None"):
            findings.append(f"[V6] a dropped Point names no cause: {ln[:120]}")
        if vals not in ("[]", "") and cause == "":
            findings.append(f"[V6] a dropped Point carried values but no cause: "
                            f"{ln[:120]}")

    # ── V7: atomicity, and SPLIT named as the reason it cannot move ───────
    at = log.kv(log.one("e2-atomic before="))
    for key in ("before", "after", "fused_after"):
        if not at.get(key, "").isdigit():
            findings.append(f"[V7] the atomic-Point count is not printed ({key}= "
                            f"missing)")
    if not log.one("split-implemented=no"):
        findings.append("[V7] the log does not state that SPLIT is unimplemented — "
                        "an atomicity failure could then be read as a pass")
    fused_rows = log.find("e2-atomic fused ")
    for ln in fused_rows:
        m = FUSED_LINE.match(ln)
        if not m or m.group("cause") in ("", "n/a", "None"):
            findings.append(f"[V7] a fused Point names no cause: {ln[:120]}")
    # `fused_after` is a count of the fused Points printed below it, so it equals
    # them — and the two buckets partition the population, so they sum to it. Both
    # were unread: `fused_after=0` beside a fused row passed with 0 findings
    # (#9110 review P3).
    fused_arm = arm_metric(scored, "atomic_points")
    if at.get("fused_after", "").isdigit() \
            and int(at["fused_after"]) != len(fused_rows):
        findings.append(
            f"[V7] e2-atomic fused_after={at['fused_after']} but the log prints "
            f"{len(fused_rows)} `e2-atomic fused` line(s) — the count is not the "
            f"set it counts")
    if fused_arm.get("fused", "").isdigit() and at.get("fused_after", "").isdigit() \
            and int(fused_arm["fused"]) != int(at["fused_after"]):
        findings.append(
            f"[V7] e2-atomic fused_after={at['fused_after']} is not the scored "
            f"arm's atomic_points fused={fused_arm['fused']} — the line disagrees "
            f"with its own arm")
    if fused_arm.get("atomic", "").isdigit() and fused_arm.get("fused", "").isdigit():
        points_total = arm_metric(scored, "real_facts").get("points", "")
        buckets = int(fused_arm["atomic"]) + int(fused_arm["fused"])
        if points_total.isdigit() and buckets != int(points_total):
            findings.append(
                f"[V7] atomic + fused = {buckets} but the arm has "
                f"{points_total} Point(s) — the two buckets are supposed to "
                f"partition the population")

    # ── V8: coverage honesty ──────────────────────────────────────────────
    cov = log.kv(log.one("coverage rows="))
    if not cov:
        findings.append("[V8] no coverage table — a gate measured only on the "
                        "class it can detect reads as a pass")
    else:
        for key in ("rows", "in_scope", "positively_classified"):
            if not cov.get(key, "").isdigit():
                findings.append(f"[V8] coverage does not state {key}=")
        # ABSENT is not EMPTY (#9110 review P2). The table is complete when every
        # class has a detector — that is the outcome the semantic half (#4894) is
        # meant to produce — so an empty list is a PASS. Only a field that is not
        # there at all is a finding: the pair is indistinguishable in `kv()` (the
        # KV regex needs a non-space value), so presence is read off the raw line.
        cov_line = log.one("coverage rows=") or ""
        if "undetected_classes=" not in cov_line:
            findings.append("[V8] coverage does not state undetected_classes= — a "
                            "missing field cannot be read as an all-detected table")
            state["undetected_classes"] = []
        else:
            undetected = [c for c in cov.get("undetected_classes", "").split(",")
                          if c]
            state["undetected_classes"] = undetected
        per_class = [log.kv(ln) for ln in log.find("coverage class=")]
        declared = sum(int(kv.get("rows", "0") or 0) for kv in per_class)
        if not per_class:
            findings.append("[V8] the coverage table has no per-class rows — the "
                            "2-detector table is the disclosure V8 exists to "
                            "protect, and an absent table is not an all-detected "
                            "one")
        else:
            if cov.get("rows", "").isdigit() and declared != int(cov["rows"]):
                findings.append(f"[V8] the per-class rows sum to {declared} but the "
                                f"coverage header says {cov['rows']}")
            missing = [kv for kv in per_class
                       if not all(kv.get(k, "").isdigit()
                                  for k in ("rows", "dd_positive", "value_gate"))]
            if missing:
                findings.append(f"[V8] {len(missing)} coverage row(s) do not state "
                                f"rows=, dd_positive= and value_gate= — a row without "
                                f"both detectors cannot show which one reached it")
            else:
                positives = sum(int(kv["dd_positive"]) for kv in per_class)
                if cov.get("positively_classified", "").isdigit() and \
                        positives != int(cov["positively_classified"]):
                    findings.append(
                        f"[V8] the per-class dd_positive column sums to {positives} "
                        f"but the header says positively_classified="
                        f"{cov['positively_classified']} — the two disclosures "
                        f"disagree")
                # ABSENT is neither EMPTY nor a pass: the undetected list and the "
                # table are two statements about the same thing, so each must be
                # exactly the other (#9110 review P2 — M2 deleted the table and an "
                # undetected list could stand alone, unchecked).
                class_names = [ln.split("class=", 1)[1].split()[0]
                               for ln in log.find("coverage class=")]
                detectless = sorted(
                    cls for cls, kv in zip(class_names, per_class, strict=True)
                    if int(kv["dd_positive"]) == 0 and int(kv["value_gate"]) == 0)
                if "undetected_classes=" in cov_line \
                        and sorted(state.get("undetected_classes") or []) != detectless:
                    findings.append(
                        f"[V8] undetected_classes="
                        f"{','.join(sorted(state.get('undetected_classes') or []))} is "
                        f"not what the class table shows "
                        f"({','.join(detectless)}) — the coverage header and its "
                        f"table disagree")

    # ── V9: pre-registration binding ──────────────────────────────────────
    pre = log.kv(log.one("prereg="))
    if not pre.get("prereg"):
        findings.append("[V9] the log does not record the pre-registration commit "
                        "it is scored against")
    if not pre.get("artifact"):
        findings.append("[V9] the log does not name the pre-registration artifact")
    unprereg = pre.get("prereg") == "UNPREREGISTERED"
    # The threshold the run USED must be the one the artifact FROZE. Without this
    # pair a log could name a pre-registration commit and still score itself
    # against a bar it chose — which is the whole failure the pre-registration
    # exists to prevent.
    frozen = log.kv(log.one("prereg_e1_threshold=")).get("prereg_e1_threshold", "")
    used = log.kv(log.one("e1 ")).get("threshold", "")
    if not unprereg:
        if frozen in ("", "n/a", "ABSENT"):
            findings.append("[V9] the log names a pre-registration but does not "
                            "print the E1 threshold that artifact froze")
        elif used != frozen:
            findings.append(f"[V9] the run scored against threshold={used} but the "
                            f"frozen artifact states {frozen} — the bar moved")
    if not log.one("prereg_e2_floor="):
        findings.append("[V9] the log does not state whether an E2 threshold was "
                        "consumed")
    for name in CRITERIA:
        prefix = name if name.endswith("=") else f"{name} "
        line = log.one(prefix)
        verdict = log.kv(line).get("verdict", "")
        if verdict not in VERDICTS:
            findings.append(f"[V9] criterion {name} carries no verdict "
                            f"(got {verdict!r})")
        if unprereg and verdict == "PASS":
            findings.append(f"[V9] criterion {name} is PASS with no frozen "
                            f"threshold — a verdict was invented, not measured")
    state["verdicts"] = {
        name: log.kv(log.one(name if name.endswith("=") else f"{name} "))
        .get("verdict")
        for name in CRITERIA
    }

    # ── V10: the result line agrees with the criteria ─────────────────────
    result = log.kv(log.one("result=")).get("result", "")
    exit_kv = log.kv(log.one("exit=")).get("exit", "")
    if result not in ("GREEN", "RED", "UNPREREGISTERED"):
        findings.append(f"[V10] no result line (got {result!r})")
    if result == "GREEN" and any(v != "PASS" for v in state["verdicts"].values()):
        findings.append("[V10] the log reports GREEN while a criterion is not PASS")
    # ... and the two REVERSE directions. Without them a malformed
    # pre-registration that left every criterion UNPREREGISTERED could still be
    # reported as RED ("scored and something red") when nothing was scored, and
    # a scored log could print `FAIL` under an UNPREREGISTERED result.
    if result == "RED" and not any(v == "FAIL" for v in state["verdicts"].values()):
        findings.append("[V10] the log reports RED while no criterion is FAIL — "
                        "nothing was scored red")
    if result == "UNPREREGISTERED" and any(
            v != "UNPREREGISTERED" for v in state["verdicts"].values()):
        findings.append("[V10] an UNPREREGISTERED log carries a scored criterion "
                        "verdict — no frozen bar existed")
    if result == "UNPREREGISTERED" and exit_kv != "3":
        findings.append(f"[V10] an UNPREREGISTERED log must exit 3, not {exit_kv}")
    if result in ("GREEN", "RED") and exit_kv not in ("0", "1"):
        findings.append(f"[V10] a scored log must exit 0 or 1, not {exit_kv}")
    # The threshold-free observation is a CONCLUSION about the reduction, so it
    # must agree with the reduction printed beside it: "the share fell" and
    # "NO_REDUCTION" cannot both be true.
    observed = e1_kv.get("observed", "")
    try:
        reduction = float(e1_kv["reduction"])
    except (KeyError, ValueError):
        reduction = None
    if observed not in ("NO_REDUCTION", "REDUCTION_OBSERVED"):
        findings.append(f"[V10] the e1 line carries no observation "
                        f"(got {observed!r})")
    elif reduction is None:
        findings.append("[V10] the e1 line carries no reduction to observe")
    elif observed == "REDUCTION_OBSERVED" and reduction <= 0:
        findings.append(
            f"[V10] the e1 line observes a REDUCTION with reduction="
            f"{reduction} — the observation contradicts the arithmetic beside it")
    elif observed == "NO_REDUCTION" and reduction > 0:
        findings.append(
            f"[V10] the e1 line observes NO_REDUCTION with reduction="
            f"{reduction} — the observation contradicts the arithmetic beside it")

    # ── V12: the discriminating control (E1-E2.md limit 6) ────────────────
    # The bundle's central conclusion rests on an arm whose 0.0 pp is
    # arithmetic-IDENTICAL under two different explanations: the gate cannot
    # remove a definite description, or this corpus references every Object so
    # Rule 2 always fires. Without the control the log cannot tell them apart, and
    # the doc's ``⇒ by construction`` sentence was drawn from the arms alone
    # (#9110 review P2). So the control is REQUIRED, and its numbers are
    # reconciled like every other claim in the log.
    ctl = log.kv(log.one("control variant=unreferenced-definite-descriptions "))
    if not ctl:
        findings.append("[V12] the log carries no discriminating control — the "
                        "0.0 pp cannot then be told apart from a corpus that "
                        "references every Object")
        state["control"] = None
    else:
        for key in ("stripped_objects", "stripped_references", "objects",
                    "the_prefix", "share", "removed"):
            if not ctl.get(key):
                findings.append(f"[V12] the control does not state {key}=")
        # WHICH Objects the control removed was computed and never printed, so the
        # discards it exists to show landing could be read only as a COUNT
        # (#9110 review P3). The names are now the set that count counts — read
        # with a dedicated pattern, because the shared KV regex stops at the first
        # space inside a JSON list of names.
        names_m = REMOVED_NAMES_LINE.search(log.one(
            "control variant=unreferenced-definite-descriptions ") or "")
        try:
            removed_names = json.loads(names_m.group("names")) if names_m else None
        except json.JSONDecodeError:
            removed_names = None
        if removed_names is None:
            findings.append("[V12] the control does not name what it removed "
                            "(removed_names=) — the discards it exists to show "
                            "landing are then a bare count")
        elif (not isinstance(removed_names, list)
                or not all(isinstance(n, str) for n in removed_names)):
            findings.append("[V12] the control's removed_names= is not a list of "
                            "Object names")
            removed_names = None
        if ctl.get("removed", "").isdigit() and removed_names is not None \
                and len(removed_names) != int(ctl["removed"]):
            findings.append(
                f"[V12] the control says it removed {ctl['removed']} Object(s) but "
                f"names {len(removed_names)} — the count is not the set it counts")
        nums = {k: ctl.get(k, "") for k in
                ("stripped_objects", "objects", "the_prefix", "removed")}
        if all(v.isdigit() for v in nums.values()):
            if int(nums["stripped_objects"]) <= 0:
                findings.append("[V12] the control strips no reference — it then "
                                "separates nothing")
            if int(nums["removed"]) != int(nums["stripped_objects"]):
                findings.append(
                    f"[V12] the control strips {nums['stripped_objects']} "
                    f"reference(s) but removes {nums['removed']} Object(s) — the "
                    f"discards it exists to show landing did not all land")
            if mint.get("dropped_objects", "").isdigit() and \
                    int(nums["stripped_objects"]) != int(mint["dropped_objects"]):
                findings.append(
                    f"[V12] the control strips {nums['stripped_objects']} "
                    f"reference(s) but the mint-skip transform names "
                    f"{mint['dropped_objects']} positively-classified Objects — "
                    f"the two are not the same population")
            if int(nums["objects"]) <= 0 or int(nums["the_prefix"]) > int(nums["objects"]):
                findings.append(f"[V12] the control's object counts are impossible "
                                f"({nums})")
        try:
            ctl_share = float(ctl["share"])
            ctl_objects = int(ctl["objects"])
            ctl_prefix = int(ctl["the_prefix"])
        except (KeyError, ValueError):
            findings.append("[V12] the control's share/objects/the_prefix are not "
                            "readable numbers")
        else:
            if ctl_objects <= 0 or not math.isclose(
                    ctl_share, ctl_prefix / ctl_objects, rel_tol=0, abs_tol=5e-6):
                findings.append(
                    f"[V12] the control's share={ctl_share} is not "
                    f"{ctl_prefix}/{ctl_objects} — the share is not its own counts")
        state["control"] = ctl
    # ── V15: the bar is bound to the population it was argued for ──────────
    # V9 checks that the threshold the run USED is the one the artifact FROZE.
    # That is half a bar: the artifact freezes a NUMBER and a POPULATION, and a
    # log can satisfy V9 while measuring a corpus the bar was never argued over
    # (#9076 — the numerator/denominator mismatch the issue refuses one level up).
    bind = log.kv(log.one("prereg-binding "))
    if bind:
        # The DECLARED side is read from the RAW token, never the quote-stripped
        # value (#11689 review round 10). A record that declares the STRING `"n/a"`
        # (or `""`) prints it QUOTED by `_floor_number`, and `kv` strips the quotes,
        # so a stripped value cannot tell "declared something unreadable" from
        # "declared nothing" — the same collapse rounds 4–6 fixed for a declared
        # `null`, one token over. An honest `floor-unreadable` refusal is then red for
        # a contradiction it does not contain.
        binding_raw = {m.group("key"): m.group("val")
                       for m in KV.finditer(log.one("prereg-binding ") or "")}

        def _declared(field: str) -> bool:
            """Did the record DECLARE this floor, whatever its value?"""
            return binding_raw.get(field, "") not in ("", "n/a")

        def _raw(field: str) -> float | int | None:
            """The field as a comparable number, read from its RAW token.

            ``Log.kv`` strips the surrounding quotes, so a declared-but-unreadable
            floor that the probe printed as ``"0.2"`` reaches ``bind`` as the bare
            ``0.2`` and reads as a MET floor — the exact permissive read ``_num``'s
            own docstring promises to refuse, defeated at every call site that read
            the stripped value (#11725 review P1). The raw token is the only place
            the quoting survives, so every rule that treats a binding field as a
            NUMBER — the owned-floor mirror, the floor arithmetic, the denominator
            pair — reads through here.
            """
            return _num(binding_raw.get(field, ""))

        statuses = ("MATCHED", "MISMATCH", "UNPINNED", "BELOW_FLOOR",
                    "UNREADABLE")
        if bind.get("status") not in statuses:
            findings.append(f"[V15] the prereg-binding status is "
                            f"{bind.get('status')!r}, not one of {statuses}")
        # The binding must be about the corpus this log MEASURED — otherwise it
        # is a claim about a file nobody ran (the same class as a threshold the
        # run did not use).
        measured_sha = log.kv(log.one("corpus=")).get("corpus_sha256", "")
        if not measured_sha:
            findings.append("[V15] the log prints a prereg-binding but no "
                            "corpus sha256 to reconcile it against")
        elif bind.get("corpus_sha256") != measured_sha:
            findings.append(
                f"[V15] the binding names corpus_sha256={bind.get('corpus_sha256')} "
                f"but the run measured {measured_sha} — the binding is about a "
                f"corpus this log did not run")
        # The binding's denominator is the scored arm's own `objects` line, so
        # the two must agree: a hand-edited log could otherwise inflate
        # `objects=` to dodge the floor while the arm still prints the real
        # denominator (#9513 review P2).
        if bind.get("objects", "").isdigit() and scored in totals \
                and int(bind["objects"]) != totals[scored]:
            findings.append(
                f"[V15] the binding checks objects={bind['objects']} but the "
                f"scored arm {scored} measured {totals[scored]} — the floor was "
                f"checked against a denominator this run did not produce")
        # ── the FLOOR ACCOUNTING (#11689) ──────────────────────────────────
        # Every declared `denominator_floors` key must land in exactly one bucket:
        # OWNED (this probe scores the row, so it CHECKS the floor), SKIPPED with the
        # criterion that owns it, or UNCONSUMED (and then it is the reason the
        # binding refused). The buckets exist because the old shape had one bucket —
        # "consumed" — so a floor on another row's criterion voided every run; a skip
        # that is not declared and owned by somebody is the silent-drop #9513 review
        # P2 closed, so the accounting is what makes the scoping auditable instead of
        # silent.
        owned_keys = [k for k in bind.get("floors_owned", "").split(",")
                      if k not in ("", "n/a")]
        skipped_entries = [k for k in bind.get("floor_skipped", "").split(",")
                           if k not in ("", "n/a")]
        unconsumed_keys = [k for k in bind.get("floor_unconsumed", "").split(",")
                           if k not in ("", "n/a")]
        for entry in skipped_entries:
            key, _sep, owner = entry.partition(":")
            if not key or not owner.strip():
                findings.append(
                    f"[V15] floor_skipped entry {entry!r} names no owning "
                    f"criterion — a floor skipped without its row is a floor "
                    f"dropped, and the reader cannot tell which instrument does "
                    f"check it")
            # …and a SKIP may not be used to shed a floor THIS probe owns. The
            # bucket exists for rows another instrument scores; delegating an owned
            # key to a made-up owner is how a scored log could certify a run whose
            # owned floors were never compared — the same "compared it and denied
            # owning it" class the MATCHED mirror closes for `floors_owned=`
            # (#11689 review round 6).
            if key in OWNED_KEYS:
                findings.append(
                    f"[V15] floor_skipped={entry} skips {key!r}, which is a floor "
                    f"THIS probe owns and is expected to compare — an owned floor "
                    f"may not be delegated to another row")
        buckets: dict[str, list[str]] = {}
        for label, keys in (("floors_owned", owned_keys),
                            ("floor_skipped", [e.partition(":")[0]
                                               for e in skipped_entries]),
                            ("floor_unconsumed", unconsumed_keys)):
            for key in keys:
                buckets.setdefault(key, []).append(label)
        for key, where in sorted(buckets.items()):
            if len(where) > 1:
                findings.append(
                    f"[V15] the binding puts floor {key!r} in more than one "
                    f"bucket ({', '.join(where)}) — a key that is owned and "
                    f"skipped (or skipped and refused) is two claims about one "
                    f"floor")
        # The MEASURED side of the two newly owned floors must be anchored to the
        # ARM lines, not to the binding line itself (#11689 review P1). Comparing
        # `declared_before_floor` against a `before_share` printed on the SAME line
        # is self-consistency: a hand-edited binding could relabel a genuinely
        # below-floor run `MATCHED` while the arm lines still printed the real
        # numbers. `objects` has had this anchor since V15 was written
        # (`bind['objects']` vs the scored arm's denominator); the share and the
        # surviving count get the same one. The share anchor's two sides are prints of
        # the SAME census float, so they are identical in an honest log — the band is
        # headroom for float parsing, NOT room to disagree (#11689 review round 12: at
        # `abs_tol=5e-6` a binding line could drift four printed steps off the run's
        # share and still be certified GREEN).
        if _is_number(bind.get("before_share", "")) and before_arm in shares \
                and not math.isclose(float(bind["before_share"]),
                                     shares[before_arm], rel_tol=0,
                                     abs_tol=_SHARE_PRINT_BAND):
            findings.append(
                f"[V15] the binding checks before_share="
                f"{bind.get('before_share')} but the {before_arm} arm measured "
                f"share={shares[before_arm]} — the floor was checked against a "
                f"control-arm share this run did not produce")
        if bind.get("surviving_units", "") not in ("", "n/a"):
            if not bind.get("surviving_units", "").isdigit():
                findings.append(
                    f"[V15] the binding states surviving_units="
                    f"{bind.get('surviving_units')!r}, not a count — a population "
                    f"the gate cannot read is refused, not skipped")
            elif scored not in planted_pts:
                findings.append(
                    "[V15] the binding states surviving_units= but the scored arm "
                    "prints no metric=real_facts line — the measured side "
                    "of E2b's floor is anchored to nothing, so a hand-edited "
                    "binding could satisfy it on its own word")
            elif int(bind["surviving_units"]) != planted_pts[scored]:
                findings.append(
                    f"[V15] the binding checks surviving_units="
                    f"{bind['surviving_units']} but the scored arm {scored} "
                    f"measured {planted_pts[scored]} surviving planted units "
                    f"(`real_facts`) — the floor was "
                    f"checked against a population this run did not produce")
            # The population the floor reads is printed ON the line, so a binding
            # that states a count while naming another basis is refused rather than
            # read as the count it happens to carry. `atomic_points` was that wrong
            # basis (#10989), so a log that still prints it is a finding.
            #
            # ⛔ THE EMPTY STRING IS TOLERATED ON PURPOSE — it is not a fail-open.
            # This token does not exist before #10989, and the probe now prints it
            # on every binding it emits, so an ABSENT token can only be a log
            # written by an older probe. What this row closes is a binding that
            # NAMES another basis — `atomic_points` above all — so a NAMED basis
            # is what is graded: absent and `n/a` are both tolerated, because a
            # basis the probe could not read never named a population.
            # (#11833 review r7 P3: this sentence said "PRESENCE is what is
            # graded", which is the opposite of the tuple below and of the
            # paragraph above — an ABSENT token is tolerated, not graded.)
            #
            # ⚠️ THE TOLERANCE IS NARROW, and worth stating exactly rather than
            # as "the historical artifacts": a legacy binding is only reached
            # here when its `surviving_units` already EQUALS the scored arm's own
            # `metric=real_facts` count — the count branch above refuses every
            # legacy log whose count disagrees. So the shape this protects is a
            # well-formed pre-#10989 log on the right population, whose only sin
            # is not saying so. Narrowing this tuple to ("n/a", "real_facts") is a
            # corpus-wide decision about older logs, deliberately not taken here.
            # (#11833 review P2-3 corrected this comment: it claimed refusing the
            # absent token would refuse the historical artifacts, but the count
            # branch three lines above already refuses every one whose count does
            # not match.)
            basis = bind.get("surviving_units_basis", "")
            if basis not in ("", "n/a", "real_facts"):
                findings.append(
                    f"[V15] the binding states surviving_units_basis={basis!r} — "
                    f"the E2b floor's population is the surviving PLANTED units "
                    f"(`real_facts`); a floor checked against another population "
                    f"is the fail-open #10989 closes")
        # A floor that was COMPARED is an owned floor: `declared_*` carries a number
        # only when the probe read that floor, so a binding that prints one without
        # claiming the key is a comparison it then denies making.
        for floor_key, declared_field in (
                ("e1_objects", "declared_floor"),
                ("e1_min_before_share", "declared_before_floor"),
                ("e2b_surviving_units", "declared_surviving_floor")):
            if _is_number(bind.get(declared_field, "")) and \
                    floor_key not in owned_keys:
                findings.append(
                    f"[V15] the binding checked {floor_key} "
                    f"({declared_field}={bind.get(declared_field)}) but does not "
                    f"name it on floors_owned= — a floor it compared and then "
                    f"denied owning is not auditable")
        # Status-independent: an OWNED floor is a floor the record DECLARES — that
        # is how `floors_owned=` is built — so its declared side can never print
        # `n/a`. Owned + `n/a` is the log contradicting itself, and it is exactly
        # how a declared JSON `null` floor used to hide (`n/a` covered both "none
        # declared" and "declared but unreadable") — #11689 review round 5.
        # (A refusal DOES legitimately print owned keys whose declared value was
        # declared-but-unreadable: it prints the raw value it could not read. That
        # value is QUOTED when it is a string, so a declared `"n/a"` or `""` reaches
        # `kv` as the bare sentinel — which is why `_declared()` reads the RAW token
        # above instead of the stripped value (#11689 review round 10). Round 4
        # scoped this loop to MATCHED on the belief that a refusal prints `n/a`
        # there; round 5 removed that scope and round 6 deleted the stale comment
        # that still claimed it.)
        for key in owned_keys:
            field = {"e1_objects": "declared_floor",
                     "e1_min_before_share": "declared_before_floor",
                     "e2b_surviving_units": "declared_surviving_floor"}.get(key)
            if field and not _declared(field):
                findings.append(
                    f"[V15] the binding claims floors_owned={key} but prints "
                    f"{field}=n/a — an owned floor the record did not declare, so "
                    f"nothing was compared against it")
        if bind.get("status") == "MATCHED":
            # …and the MIRROR of the rule above, ON A MATCHED BINDING: a key it
            # CLAIMS to own must be a key this gate can locate a declared/measured
            # pair for. `floors_owned=` was otherwise trusted word-for-word, so a
            # log could claim ownership of a floor no code reads — the label coupled
            # to the check by convention only, which is the same class of fail-open
            # the anchors above close (#11689 review round 4). It is scoped to
            # MATCHED because a REFUSAL has no measured side to reconcile with: the
            # status-independent rule above already covers the `n/a` contradiction.
            known = {"e1_objects": "declared_floor",
                     "e1_min_before_share": "declared_before_floor",
                     "e2b_surviving_units": "declared_surviving_floor"}
            for key in owned_keys:
                if key not in known:
                    findings.append(
                        f"[V15] the binding claims floors_owned={key} but this gate "
                        f"knows no declared/measured pair for it — an 'owned' floor "
                        f"nothing compares is a floor that was not checked")
                elif _raw(known[key]) is None:
                    findings.append(
                        f"[V15] the binding claims floors_owned={key} but prints "
                        f"{known[key]}={binding_raw.get(known[key], '')!r}, not a "
                        f"bare number — it names a floor it never read")
        if bind.get("status") == "MATCHED":
            # A MATCHED binding is the only shape a scored log may carry, so it
            # must carry NO refusal reason — otherwise a log could print a
            # refusal beside a verdict (#9513 review P2: a declared-but-
            # unreadable floor used to read as "no floor", i.e. MATCHED).
            if bind.get("reason", "n/a") not in ("n/a", ""):
                findings.append(
                    f"[V15] the binding is MATCHED but states reason="
                    f"{bind.get('reason')!r} — a matched bar has nothing to refuse")
            # Read from the RAW tokens (#11725 review): a quoted denominator or floor
            # — what the probe prints for a value it could not read — reaches `bind`
            # quote-stripped, so `isdigit()` on the stripped value accepted
            # `declared_floor="20"` beside an unreadable floor and certified GREEN.
            # `n/a` (no floor stated) stays legal; quoting is not.
            nums = (binding_raw.get("objects", "").strip(),
                    binding_raw.get("declared_floor", "").strip())
            # MATCHED means "a pin EXISTS, it agrees, and no floor is violated"
            # (the probe's own contract), so a MATCHED line that names no pin at
            # all is the UNPINNED case RELABELLED — a relabel, not an absent
            # line, so every other rule here accepted it (#9513 review P2).
            pins = (bind.get("declared_path", ""),
                    bind.get("declared_sha256", ""))
            if all(not p or p == "n/a" for p in pins):
                findings.append(
                    f"[V15] the binding is MATCHED but names no pin "
                    f"(declared_path={pins[0]!r}, declared_sha256={pins[1]!r}) — "
                    f"a bar matched to nothing is an unbound bar relabelled")
            elif pins[1] not in ("", "n/a") and \
                    pins[1].lower().removeprefix("sha256:") != measured_sha:
                findings.append(
                    f"[V15] the binding is MATCHED but its declared pin "
                    f"declared_sha256={pins[1]} is not the corpus this log "
                    f"measured ({measured_sha}) — a matched pin that names other "
                    f"bytes is not this run's bar")
            # `declared_path` is deliberately NOT reconciled: the log prints only
            # the corpus BASENAME (`corpus=`), and a symlinked `--corpus` resolves
            # to the declared file under a different name, so a name comparison
            # would refuse a legitimate binding. The sha is the checkable half —
            # MATCHED means "the pin agrees", and the probe compares a declared
            # sha ALONE. The asymmetry is stated here so it is not read as an
            # omission (#9513 review P2).
            # The floor arithmetic is NOT gated on `isdigit()` (#9513 review P2):
            # skipping the comparison whenever a number cannot be read is the
            # permissive read `_declared_floor` was fixed to refuse in the probe,
            # and applying it to the refusal statuses while sparing MATCHED judged
            # the same log differently depending on the word its own line
            # claimed. `n/a` is the ONE legal non-integer floor: the artifact
            # states no floor at all, which is ABSENT, not unreadable.
            if not nums[0].isdigit():
                findings.append(
                    f"[V15] the binding is MATCHED but its denominator "
                    f"objects={nums[0]!r} is not a plain integer — a denominator "
                    f"the gate cannot read is refused, not skipped")
            if nums[1].isdigit():
                if nums[0].isdigit() and int(nums[0]) < int(nums[1]):
                    findings.append(
                        f"[V15] the binding is MATCHED while objects={nums[0]} is "
                        f"under declared_floor={nums[1]} — a below-floor run cannot "
                        f"be bound-ready")
            elif nums[1] not in ("n/a", ""):
                findings.append(
                    f"[V15] the binding is MATCHED but declares_floor={nums[1]!r} "
                    f"is neither a plain integer nor `n/a` — present but "
                    f"unreadable is not absent")
            # The OTHER two owned floors, in the same both-directions shape: a
            # MATCHED bar cannot sit under a floor it declares, and it cannot claim
            # to have met a floor whose measured side it never printed (#11689).
            # Both sides are read from their RAW tokens (#11725 review): `kv` strips
            # quotes, so a forged or unreadable value — the probe prints a value it
            # could not read QUOTED — would otherwise be fed to the arithmetic as a
            # number, letting `declared_before_floor="0.2"` read as a met floor and
            # skipping the below-floor check the pair exists to run.
            for floor_field, measured_field in (
                    ("declared_before_floor", "before_share"),
                    ("declared_surviving_floor", "surviving_units")):
                floor_value = _raw(floor_field)
                measured = _raw(measured_field)
                if floor_value is None:
                    if not _declared(floor_field):
                        continue          # no floor declared for this key
                    findings.append(
                        f"[V15] the binding is MATCHED but {floor_field}="
                        f"{binding_raw.get(floor_field, '')!r} is not a bare "
                        f"number — present but unreadable is not absent")
                elif measured is None:
                    findings.append(
                        f"[V15] the binding is MATCHED and declares "
                        f"{floor_field}="
                        f"{binding_raw.get(floor_field, '').strip()} but states "
                        f"no readable {measured_field}="
                        f"{binding_raw.get(measured_field, '')!r} — a floor met "
                        f"against a quantity the log never printed is not "
                        f"reconciled")
                elif _below_floor_beyond_rounding(measured, floor_value):
                    # The band is one print half-width (`_SHARE_PRINT_BAND`), NOT the
                    # arm anchor's 5e-6: the declared side is exact and only the
                    # measured side is rounded by `_fmt` (#11689 review round 11 — the
                    # first revision reused the anchor's tolerance, which is ten times
                    # the printing resolution and swallowed real below-floor gaps).
                    findings.append(
                        f"[V15] the binding is MATCHED while "
                        f"{measured_field}="
                        f"{binding_raw.get(measured_field, '').strip()} is under "
                        f"{floor_field}="
                        f"{binding_raw.get(floor_field, '').strip()} — a "
                        f"below-floor run cannot be bound-ready")
        # The floors the binding REFUSED because it cannot evaluate them must
        # be named on the line and be the reason it refused: a refusal that
        # does not say which floor it dropped is not auditable, and a line that
        # names dropped floors while claiming a clean `reason=n/a` is the same
        # silent-drop shape one field over (#9513 review P2).
        dropped = bind.get("floor_unconsumed", "")
        refused_for_dropped = "floor-unconsumed" in [
            r for r in bind.get("reason", "").split(",")]
        if refused_for_dropped and dropped in ("", "n/a"):
            findings.append(
                f"[V15] the binding refuses for floor-unconsumed but names no "
                f"floor it dropped (floor_unconsumed={dropped!r}) — the "
                f"refusal is then unauditable")
        elif dropped not in ("", "n/a") and not refused_for_dropped:
            findings.append(
                f"[V15] the binding names floors it did not evaluate "
                f"(floor_unconsumed={dropped}) while stating "
                f"reason={bind.get('reason')!r} — a dropped floor is a bar "
                f"its author did not write")
        if bind.get("status") == "UNREADABLE" and \
                "floor-unreadable" not in bind.get("reason", ""):
            findings.append("[V15] an UNREADABLE binding does not attribute it to "
                            "a floor that could not be read")
        # The status word and the reason list must agree (#11689 review round 8, T3).
        # `BELOW_FLOOR` is defined as "the corpus agreed and only a floor is under it",
        # so any other stated cause contradicts the status the log claims — and a
        # binding that refuses for the floor alone while claiming MISMATCH says the
        # corpus did not agree when nothing else was wrong. Without these, a
        # self-contradicting line certified sound.
        reasons = {r for r in bind.get("reason", "").split(",") if r}
        if bind.get("status") == "BELOW_FLOOR" and reasons != {"below-floor"}:
            findings.append(
                f"[V15] a BELOW_FLOOR binding states reason="
                f"{bind.get('reason')!r} — BELOW_FLOOR means the corpus agreed and "
                f"ONLY a floor is under the run, so it cannot carry another refusal "
                f"cause (that is a MISMATCH)")
        if bind.get("status") == "MISMATCH" and reasons == {"below-floor"}:
            findings.append(
                "[V15] a MISMATCH whose only stated cause is below-floor — a run "
                "under its floor with an agreeing corpus is BELOW_FLOOR, and the "
                "status is then the contradiction")
        if bind.get("status") == "BELOW_FLOOR":
            # Three owned floors can raise a below-floor refusal since #11689, so the
            # readable pair is required on the floor that FIRED, not on `e1_objects`
            # alone: a BELOW_FLOOR whose only numbers are readable-but-met (or absent)
            # is unauditable, and a binding refused on its share floor with
            # `declared_floor=n/a` is not a contradiction (it declares no Object
            # floor). The original single-pair rule rejected exactly that legitimate
            # shape, which is why it is generalised here rather than dropped.
            pairs = ((bind.get("objects", ""), bind.get("declared_floor", "")),
                     (bind.get("before_share", ""),
                      bind.get("declared_before_floor", "")),
                     (bind.get("surviving_units", ""),
                      bind.get("declared_surviving_floor", "")))
            # Magnitude-safe comparison (#11689 review round 7): `float()` turns a
            # floor like `10 ** 400` into `inf`, which made this rule reject the
            # probe's own correct refusal as "no readable pair".
            #
            # One band, BOTH polarities (#11689 review round 11). `before_share=` is
            # printed at 6 dp, so a printed value is uncertain by one half-ulp:
            #   * a MATCHED log whose printed share sits within one ulp BELOW its
            #     floor may have met it (not red — `_below_floor_beyond_rounding`);
            #   * a BELOW_FLOOR refusal whose printed share sits within one ulp ABOVE
            #     its floor may be under it (not red — `m <= f + band` here).
            # The two count pairs are integers: the probe compares them with a strict
            # `<`, there is no printing loss, so equality there is provably met and no
            # band applies.
            def _under(measured: object, floor: object, *, rounding: bool) -> bool:
                m, f = _num(measured), _num(floor)
                if m is None or f is None:
                    return False
                if m < f:
                    return True
                if not rounding:
                    return False
                try:
                    return m <= f + _SHARE_PRINT_BAND
                except OverflowError:   # an integer floor outside float range
                    return False        #  is far from any share ulp

            under = [(measured, floor) for measured, floor, rounding in (
                        (pairs[0][0], pairs[0][1], False),
                        (pairs[1][0], pairs[1][1], True),
                        (pairs[2][0], pairs[2][1], False))
                     if _under(measured, floor, rounding=rounding)]
            # ⛔ There is NO exception for an unmeasurable `before_share=` (#11689
            # review round 9). An earlier revision accepted `before_share=n/a` beside a
            # declared share floor on the theory that it is the probe's §3.2 output for
            # a zero-Object control arm. It is not reachable for a log this gate could
            # otherwise accept: a zero-Object arm prints `share=n/a` on the ARM line
            # too, which V3 already refuses twice, and the exception had no anchor — so
            # blanking `before_share=` converted ANY all-pairs-met refusal into an
            # accepted one. The band above is the only relaxation.
            if not under:
                findings.append(
                    f"[V15] a below-floor binding states no readable "
                    f"measured/declared floor pair that is actually under its "
                    f"floor (objects/declared_floor={pairs[0]}, "
                    f"before_share/declared_before_floor={pairs[1]}, "
                    f"surviving_units/declared_surviving_floor={pairs[2]}) — the "
                    f"refusal is then unauditable")
        if bind.get("status") == "MISMATCH" and not (
                set(bind.get("reason", "").split(","))
                & {"corpus-sha256", "corpus-path", "below-floor",
                   "floor-unconsumed"}):
            findings.append(f"[V15] a MISMATCH states no recognised reason "
                            f"(reason={bind.get('reason')!r}) — the refusal is "
                            f"then unauditable")
        # Rows that must reconcile in the other direction: a MATCHED binding is
        # the ONLY shape a scored log may carry, and an UNPREREGISTERED log that
        # matched still has to say why it did not score. These are the two
        # fail-open directions — a scoring log with an unbound bar, and a
        # refusal with no reason.
        if result in ("GREEN", "RED") and bind.get("status") != "MATCHED":
            findings.append(f"[V15] the log SCORED against a bar whose binding is "
                            f"{bind.get('status')!r} — an unbound bar is not a bar "
                            f"for this population")
        if (result == "UNPREREGISTERED" and bind.get("status") == "MATCHED"
                and not log.one("prereg-error=")):
            findings.append("[V15] the binding MATCHED and no reason is stated for "
                            "refusing to score — a bound bar must produce a verdict")
        if result in ("GREEN", "RED") and not bind.get("objects", "").isdigit():
            findings.append("[V15] the binding does not state the denominator "
                            "(objects=) the bar was checked against")
        if result in ("GREEN", "RED"):
            # A SCORED log is the one whose bar is load-bearing, so it must show the
            # floor accounting: which floors it owned (and therefore checked) and
            # which it deliberately skipped, with the row that owns each. Absent
            # fields are the pre-#11689 shape, where the skip existed but was not
            # declared — the exact silence this rule removes.
            #
            # The PAIRS are required too, not just the two bucket names, because
            # `floors_owned=` is the probe's own statement: DELETING the pair fields
            # is otherwise invisible here — the number->owned mirror cannot fire on
            # an absent field, and the owned->number mirror cannot fire on a key the
            # line no longer claims — so a scored log whose owned floors were never
            # compared read GREEN. The probe's `binding_line` prints all six
            # unconditionally, so no legitimate scored log is refused by requiring
            # them (#11689 review round 7).
            #
            # ⛔ DELETION, not blanking: a field BLANKED to `n/a` keeps all six keys
            # and is still accepted — that is the OPEN residual the module docstring
            # states, pinned by `test_a_blanked_pair_is_the_stated_open_residual`
            # (#11689 review round 9). This rule must not be read as closing it.
            for field, why in (
                    ("floors_owned", "the floors it checked are not declared"),
                    ("floor_skipped", "the rows it skipped are not declared"),
                    ("declared_before_floor",
                     "the share floor's declared side is missing"),
                    ("before_share",
                     "the share floor's measured side is missing"),
                    ("declared_surviving_floor",
                     "the E2b floor's declared side is missing"),
                    ("surviving_units",
                     "the E2b floor's measured side is missing")):
                if field not in bind:
                    findings.append(
                        f"[V15] the scored log's binding carries no {field}= — "
                        f"{why}, so a floor this criterion owns would be "
                        f"indistinguishable from one it never declared")
    else:
        declared = log.one("prereg_declared=")
        if result in ("GREEN", "RED"):
            findings.append("[V15] the log SCORED a run but carries no "
                            "prereg-binding line — the bar's corpus was never "
                            "compared to the measured one")
        elif declared and not log.one("prereg-error="):
            findings.append("[V15] a pre-registration was supplied and the run "
                            "refused to score, but the log states neither "
                            "prereg-binding nor prereg-error — the refusal has "
                            "no reason")
    state["prereg_binding"] = bind or None
    state["result"] = result
    return findings, state


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="structurally check an E1+E2 log (#9076)")
    ap.add_argument("log")
    ap.add_argument("--require-green", action="store_true",
                    help="exit 1 unless the log's result is GREEN")
    args = ap.parse_args(argv)

    path = Path(args.log)
    if not path.exists():
        print(f"no such log: {path}", file=sys.stderr)
        return 2
    findings, state = check(path.read_text(encoding="utf-8"))
    for f in findings:
        print(f)
    if findings:
        print(f"\n{len(findings)} finding(s) — the evidence log is not structurally sound")
        return 1
    print(f"structurally sound: {len(state.get('arms') or [])} arm(s), "
          f"result={state.get('result')}, "
          f"undetected classes={','.join(state.get('undetected_classes') or [])}")
    if args.require_green and state.get("result") != "GREEN":
        print(f"[V11] the log's result is {state.get('result')}, not GREEN")
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
