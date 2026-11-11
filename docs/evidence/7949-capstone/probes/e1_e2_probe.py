"""E1 + E2 on ONE run, one corpus (#9076 — epic #7949).

**What this probe is for.** The epic's central statement is that *E1 and E2 pull
against each other*: a selection filter tuned to drop `^the <X>` Objects can hit
its E1 bar while quietly dropping the state values E2 exists to preserve. Two
separately-run lanes would each report green. So E1 (volume falls) and E2
(correctness preserved) are measured **in one process, over one corpus, through
one pipeline**, and this file prints every numerator and every denominator it
uses.

**Which pipeline "after" means — stated, because an unstated arm makes the
metric uninterpretable.** Four arms, all driven through the REAL v2 entry point
(``extractor_v2.extract_session_v2``: S0→S1→S2.1→S2.2→S2.3→S3→S4→S5/S6), with
the SAME scripted candidate set (the corpus) feeding every arm:

============================  ==========  ==========================================
arm                           scored?     what it is
============================  ==========  ==========================================
``before``                    baseline    VET and the value gate EXPLICITLY OFF
                                          (``TORTOISE_VALUE_GATE=0``). Since `#10996`
                                          this is the kill-switch arm, not the
                                          deploy: the mechanical half defaults ON.
``after-vet-shipped``         **YES**     the SAME shipped code with the gate set ON
                                          (``TORTOISE_VET=1`` + ``TORTOISE_VALUE_GATE=1``,
                                          ``arbiter=None``). This IS what production
                                          runs by default since `#10996`: the deploy
                                          sets no switch, and an unset value gate now
                                          arms the S2.2 mechanical predicate.
``after-vet-arbiter``         no          the same, plus an INJECTED arbiter answering
                                          S2.2's *"is this an entity or a reference?"*
                                          with the in-repo predicate
                                          ``definite_description_backfill
                                          .is_positively_bare_definite_description``.
                                          Counterfactual: the ``vet_arbiter=`` seam
                                          exists but **no production caller injects
                                          one** (``vet_gate``'s own docstring).
``counterfactual-mint-skip``  no          what a MINT-TIME rule would produce: the
                                          positively-classified definite-description
                                          entities are never minted and their names are
                                          stripped from the statements'
                                          ``about_entities`` (the claim survives; only
                                          the reference goes).
============================  ==========  ==========================================

**Which arm production runs — read it before reading the number.** The deploy
sets no switch (``fly.toml``'s ``[env]`` carries none of ``TORTOISE_VET``,
``TORTOISE_VALUE_GATE``, ``TORTOISE_S2_DECISION_SEAM``), so every arm is
resolved from a default. Since `#10996` the S2.2 **mechanical** half defaults ON
(``EXTRACTOR-V4-ARCHITECTURE.md`` §S2.2, ``value_gate.value_gate_enabled``), while
the **arbiter** half stays opt-in because arming it is a paid network call
(``S2-DECISION-SEAM.md`` §6) and ``TORTOISE_CLASSIFY_LATER`` is unchanged. So
production runs the gate-armed configuration, and ``before`` is a genuine
counterfactual: the pre-`#10996` deploy, reproduced by the explicit kill-switch
``TORTOISE_VALUE_GATE=0``.

⚠️ **The arms are explicit on BOTH sides, and that is load-bearing.** ``run_arm``
used to produce its OFF arm by popping the switch — an OFF arm only while the
flag was default-OFF. `#10996` sets the value explicitly in both directions
(``"1"`` / ``"0"``) and the pops stay only to keep an ambient value out; a
pop-only OFF arm would silently be an ON arm, collapsing the comparison. Pinned
by ``tests/test_e1_e2_measurement.py::test_the_probe_arms_every_switch_explicitly``.

**The scored arm is the gate's own behaviour, and it is what production runs.**
That distinction is the open question #7949 carries: if the gate is armed in
production and the discards are undone by referential integrity, the next step is
to change ``apply_vet``'s Rule 2; if it is armed and nothing changes, the
mechanism is simply rare on this population (`#10996`'s measured 0 of 61
Objects). Only ``after-vet-shipped`` is scored, because it is the only arm that
puts the shipped gate mechanism on-path at all (``before`` measures the pipeline
with no gate, so it cannot show what the gate does). The two
counterfactual arms are reported as **estimates of what a mechanism that does not
exist would buy** — never as green. ``tools
/e1_e2_census_check.py`` fails if a counterfactual arm is ever the scored one.

⚠️ **The four arms cannot tell a GATE property from a CORPUS property, so the run
also measures the difference** (``discriminating_control``, printed as
``control variant=unreferenced-definite-descriptions``). Every audited Object in
the corpus is wired to the statement it was minted from, so ``apply_vet`` Rule 2
downgrades every discard and the arbiter arm reads 0.0 pp — arithmetic that is
identical whether "S2.2 cannot remove a definite description" or "this corpus
references every Object". The control strips only those references and re-runs the
arm: the discards then LAND (9 of 9), which is what makes the 0.0 pp a statement
about a fully-referenced population rather than about S2.2. ``E1-E2.md`` limit 6.

⚠️ **The extractor is held constant on purpose.** The candidate set is
``docs/evidence/7949-capstone/corpus/e1e2_corpus.json``, produced by a scripted
model so the pipeline runs with no network and no provider key. That is not a
weakness in the E1 measurement — it is the control: the only variable between
``before`` and ``after`` is the gate, and the probe ASSERTS
(``assert_one_gate_population``) that the pre-gate population is byte-identical
across the three gate arms, so a future edit that gave one gate arm its own
candidate list fails the run instead of moving an arm's numbers. The mint-skip
arm is EXCLUDED by construction (its whole point is a different candidate set)
and is printed apart. What the probe cannot claim is anything about extraction
quality on live traffic; the corpus is the population (see ``E1-E2.md``'s limits).

**Why a pre-registration gate (§4.4 / #9075).** Thresholds chosen after the run
are the failure the epic's pre-registration rule exists to stop. ``--prereg
<sha>`` must name a commit whose TREE carries ``preregistration.json`` with the E1
and E2 thresholds; it is read with ``git show <sha>:<path>``, never from the
worktree, so a threshold edited after the numbers are in cannot be served. With no
``--prereg`` the probe still MEASURES — and reports ``UNPREREGISTERED`` instead of
a verdict, exiting 3. Measured numbers are evidence; a verdict without a frozen
threshold is not.

⚠️ **A threshold is HALF a bar: the other half is the population it was argued
over** (#9076). A pre-registration binds a number to a corpus, so reading
``e1.min_share_reduction`` and ignoring ``corpus.path`` lets this probe score a
run over ``e1e2_corpus.json`` against a bar argued on ``tests/eval/write_path`` —
the numerator/denominator mismatch the issue's own Research Needed §1.2 refuses
one level up. A *declared* denominator floor the probe cannot read as an integer is REFUSED
too (``UNREADABLE``), never read as "no floor stated" — the same fail-closed rule
the threshold gets, because a bar whose floor is silently dropped is a bar the
author did not write. That rule covers the floor's KEY as well as its value, and it
is a rule about ACCOUNTING, not about refusing every key: the
``capstone-preregistration/v1`` record declares floors for fourteen rows, of which
three are this probe's (``OWNED_FLOORS``) and the rest belong to rows other
instruments score (``SKIPPED_FLOORS``, each printed with its owning criterion), so
"a floor this binding cannot check is not a floor it may skip" is satisfied by
CHECKING the ones it owns and DECLARING the ones it does not — a key in neither set
is still REFUSED (``floor-unconsumed``, naming the keys), which is the fail-closed
half #9513 review P2 closed (#11689: before that scoping, the record's fourteen keys
voided every run, so the floor the refusal existed to protect was never checked).
``prereg_binding`` therefore compares the artifact's corpus pin (path and/or
sha256) and its three owned floors against THIS run — ``e1_objects`` on the scored
arm's Objects, ``e1_min_before_share`` on the BEFORE arm's share (§3.2's refusal
boundary) and ``e2b_surviving_units`` on the arm's ``real_facts`` — the surviving
PLANTED units, which is the population the record's E2b row is written over
(§3.1: *"the planted units that survived (floor ≥ 30)"*) and the same quantity
both sibling consumers of that key test — prints the
comparison on ``prereg-binding``, and refuses to score when they do not agree or a
denominator is under its frozen floor (the artifact's own rule: *``below_floor:
REFUSED, which is neither a pass nor a fail``*). Exit code 3, not RED: an unsound
bar is not a missed bar.

Exit codes: ``0`` scored and every criterion green · ``1`` scored and something
red · ``3`` measured but UNPREREGISTERED (no frozen thresholds, an artifact the
probe cannot consume — reported on ``prereg-error=`` — or a bar that is not this
run's, reported on ``prereg-binding``) · ``2`` usage.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import os
import re
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

# The pack registry emits declaration notices at import time; they are not this
# measurement's subject and would bury the numbers in the evidence log. The mute
# is applied INSIDE `main()`, never at module scope: `logging.disable` sets
# `logging.Manager.disable`, which is PROCESS-global, and
# `tests/test_e1_e2_measurement.py` imports this module at COLLECTION time — so
# a module-scope call outlives the run and silences every `caplog` assertion at
# WARNING/INFO in every later test of the same pytest process (`caplog.at_level`
# sets only the logger and handler levels, never `Manager.disable`).

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

DEFAULT_CORPUS = ROOT / "docs/evidence/7949-capstone/corpus/e1e2_corpus.json"
PREREG_ARTIFACT = "docs/evidence/7949-capstone/preregistration.json"

SCORED_ARM = "after-vet-shipped"
BEFORE_ARM = "before"

#: "The record does not DECLARE this floor at all" — deliberately not `None`,
#: because a declared JSON `null` is a value the probe could not READ, and the two
#: must not both print `n/a` (#11689 review round 5: `{"e2b_surviving_units": null}`
#: refused with `declared_surviving_floor=n/a` beside
#: `floors_owned=…,e2b_surviving_units`).
_ABSENT = object()

#: A population BASIS name is one whitespace-free token: it is printed on the
#: binding line (`surviving_units_basis=<token>`), whose grammar is `key=value`
#: pairs, so a name carrying a space or `=` would break every reader of the log.
_BASIS_RE = re.compile(r"[A-Za-z0-9_.:-]+")

#: The E1 numerator, restated from the architecture document (§1:85): a
#: definite description is an Object whose name begins with `the `. The
#: denominator is every Object admitted in that arm. The production figure
#: (62.3%) is reconstructed from the G5 census as 4,901 / 7,870 = 62.27% — the
#: same rule, a different (all-Objects) population.
THE_PREFIX = re.compile(r"^the\s", re.IGNORECASE)

#: ── E2's state-value detector, pinned and printed ──────────────────────────
#:
#: A value is a literal that must survive VERBATIM: a measurement/unit, a
#: percentage, a money amount, a clock time, a month-day date, a duration, or a
#: spelled-out quantity bound to a unit ("forty rows", "ten-minute TTL"). This is
#: a HEURISTIC and it is stated as one: it is deliberately generous (it would
#: rather over-count a value than miss one, because the criterion it feeds is
#: "nothing was lost"). The patterns OVERLAP on purpose — `62 percent` is also the
#: `count` literal `62` — so the figure is a count of MATCHES, not of distinct
#: values; `state_value_points` is the distinct-Point count. Every match is
#: PRINTED in the log (one `metric=state_value_literals` line per arm, class and
#: literal, so the overlap is visible), and a reader audits those literals instead
#: of trusting the count.
VALUE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("percent", re.compile(r"\b\d+(?:\.\d+)?\s?(?:%|percent)\b", re.IGNORECASE)),
    ("measure", re.compile(r"\b\d+(?:\.\d+)?\s?(?:ms|s|sec|secs|seconds|min|mins|"
                           r"minutes|h|hrs|hours|MB|GB|KB|TB|k|rps|rows|tokens|"
                           r"bytes|days|weeks)\b", re.IGNORECASE)),
    ("clock", re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\b")),
    ("money", re.compile(r"[$€£]\s?\d[\d,.]*")),
    ("date", re.compile(r"\b(?:January|February|March|April|May|June|July|August|"
                        r"September|October|November|December)\s+\d{1,2}\b")),
    ("version", re.compile(r"\bv\d+\.\d+(?:\.\d+)?\b")),
    ("count", re.compile(r"\b\d{2,}\b")),
    ("spelled-quantity", re.compile(
        r"\b(?:two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|twenty|"
        r"thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred)\b[\s-]"
        r"(?:percent|minutes?|hours?|days?|weeks?|rows?|workers?|times?|requests?|"
        r"seconds?)", re.IGNORECASE)),
)

#: Sentence terminators for the atomicity proxy. §7 requires an atomic `Point` so
#: an operator can attach to ONE claim; a fused claim gives one confidence number
#: to several beliefs. The proxy measures SENTENCE-level fusion, which is the
#: honest reach of a text rule: two claims joined inside one sentence by "and"
#: read as atomic here. Stated as a limit, not hidden.
SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\s*;\s*|\s+—\s+|\s+--\s+")


# ── corpus → the candidate set the pipeline sees ──────────────────────────


def load_corpus(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def build_embed_list(corpus: dict) -> dict:
    """The S2.1-shaped candidate list, exactly as the extractor emits one."""
    return {
        "entities": [
            {"name": o["name"], "kind": "core:other", "lifecycle": "created",
             "supersedes": None, "note": None}
            for o in corpus["objects"]
        ],
        "events": [],
        "points": [
            {"content": p["content"], "pointKind": "statement",
             "about_entities": list(p.get("about") or [])}
            for p in corpus["points"]
        ],
        "operators": [],
        "chain_notes": [],
        "link_before_create": [],
    }


def mint_skip_transform(embed_list: dict) -> tuple[dict, dict]:
    """What a MINT-TIME definite-description rule would have produced.

    The entity is never minted; the statement that referenced it KEEPS its content
    and loses the reference. That is the difference that decides E2: a design that
    drops the referencing statement as well would be a different (and strictly
    worse) policy, and the probe prints how many statements that would cost.
    """
    from tortoise import definite_description_backfill as dd

    dropped = sorted(
        e["name"] for e in embed_list["entities"]
        if dd.is_positively_bare_definite_description(str(e["name"]))
    )
    gone = set(dropped)
    minted = dict(embed_list)
    minted["entities"] = [e for e in embed_list["entities"]
                          if str(e["name"]) not in gone]
    points = []
    stripped = 0
    would_drop_statements = 0
    for p in embed_list["points"]:
        refs = [str(n) for n in (p.get("about_entities") or [])]
        kept = [n for n in refs if n not in gone]
        stripped += len(refs) - len(kept)
        if refs and not kept:
            would_drop_statements += 1
        q = dict(p)
        q["about_entities"] = kept
        points.append(q)
    minted["points"] = points
    return minted, {
        "dropped_objects": dropped,
        "stripped_references": stripped,
        "statements_left_unreferenced": would_drop_statements,
    }


def strip_references(embed_list: dict, names: set[str]) -> tuple[dict, int]:
    """The same candidate set with `names` no longer referenced by any statement.

    The ENTITY stays and so does the statement that named it — only the
    ``about_entities`` edge goes. That is what a corpus looks like in which the
    Object was never wired to the statement it came from, and it is the single
    variable that separates *"the gate cannot remove a definite description"*
    from *"this corpus makes every removal a downgrade"* (``E1-E2.md`` limit 6).
    """
    gone = set(names)
    variant = json.loads(json.dumps(embed_list))
    stripped = 0
    for p in variant["points"]:
        refs = [str(n) for n in (p.get("about_entities") or [])]
        kept = [n for n in refs if n not in gone]
        stripped += len(refs) - len(kept)
        p["about_entities"] = kept
    return variant, stripped


def discriminating_control(corpus: dict, embed_list: dict, names: list[str],
                           arbiter_census: dict) -> dict:
    """Is E1 unreachable because of the GATE, or because of the CORPUS?

    Every audited Object in ``e1e2_corpus.json`` is wired to the statement it was
    minted from (``build_e1e2_corpus.py``: ``about: [name]`` on all 25 rows, a
    corpus-CONSTRUCTION decision — the source attests a reference on 15 of them).
    ``apply_vet`` Rule 2 therefore downgrades every discard, and the arbiter arm
    reads 0.0 pp — arithmetic that is IDENTICAL under two very different
    explanations:

    * the gate cannot remove a definite description at S2.2 (a gate property), or
    * this corpus references every Object, so Rule 2 always fires (a corpus
      property).

    The four arms cannot tell them apart, and the bundle's ``⇒ by construction``
    sentence was drawn from them alone (#9110 review P2). So: take exactly the
    Objects the arbiter arm tries to discard, strip ONLY their references, and run
    the same arm again.

    * the discards now LAND ⇒ the 0.0 pp is a CORPUS property, and the gate does
      reach a definite description when nothing defends it;
    * they are still kept ⇒ the 0.0 pp is a GATE property, and E1 would be
      unreachable at S2.2 as a rule.

    Reporting WHICH one it is — instead of asserting the stronger claim — is the
    difference between a measurement and a plausible story.
    """
    names = sorted({str(o) for o in names})
    variant, stripped = strip_references(embed_list, set(names))
    res = run_arm(variant, vet=True, arbiter=definite_description_arbiter)
    cen = census(res, corpus)
    removed = [n for n in arbiter_census.get("objects") or []
               if n not in cen["objects"]]
    return {
        "objects": cen["objects_total"],
        "the_prefix": cen["objects_the_prefix"],
        "share": cen["share"],
        "real_facts": cen["real_facts"],
        "stripped_objects": len(names),
        "stripped_references": stripped,
        "removed": len(removed),
        "removed_names": removed,
    }


def definite_description_arbiter(candidates: list[dict], narrative: str) -> dict:
    """S2.2's semantic question, answered by the ONLY detector the repo has.

    ``is_positively_bare_definite_description`` is the conservative gate over the
    owner-confirmed label set (9 of 15 positively classified). Using it here means
    the counterfactual arm assumes no capability the tree does not already carry —
    and its 6 deferred names show up as *dropped: none* rather than as a guess.
    """
    from tortoise import definite_description_backfill as dd

    verdicts = []
    for c in candidates:
        if c.get("section") != "entities":
            continue
        if dd.is_positively_bare_definite_description(str(c.get("text") or "")):
            verdicts.append({
                "id": c["id"],
                "outcome": "DISCARD",
                "rule_id": "e1.counterfactual.definite_description",
                "reason": ("positively-classified bare definite description "
                           "(tortoise/definite_description_backfill.py)"),
            })
    return {"verdicts": verdicts}


# ── the scripted model: ONE candidate set, held constant across arms ───────


class ScriptedModel:
    """Deterministic `complete(system=, user=)` adapter over the corpus.

    S1 (system carries ``STORY SUMMARIZER``) → a narrative. The first S2 call →
    the corpus. Every later call (S4's gap review) → an empty delta, so the union
    is exactly the corpus and the four arms' populations are comparable. Counts
    its calls so the log can show the pipeline actually ran end to end.
    """

    def __init__(self, embed_list: dict):
        self._payload = json.dumps(embed_list)
        self._empty = json.dumps({"entities": [], "events": [], "points": [],
                                  "operators": [], "chain_notes": [],
                                  "link_before_create": []})
        self.calls = 0
        self.stages: list[str] = []
        self.last_prompt_tokens = 0
        self.last_completion_tokens = 0

    def complete(self, *, system: str, user: str, max_tokens: int | None = None) -> str:
        self.calls += 1
        if "STORY SUMMARIZER" in system:
            self.stages.append("S1")
            return "A session over the pre-registered E1/E2 corpus."
        if self.stages.count("S2.1") == 0:
            self.stages.append("S2.1")
            return self._payload
        self.stages.append("S4")
        return self._empty


# ── one arm ────────────────────────────────────────────────────────────────


def run_arm(embed_list: dict, *, vet: bool, arbiter=None) -> dict:
    """Drive the real pipeline once, with the gate flags set for this arm."""
    from tortoise import extractor_v2 as v2

    # EVERY switch for S2.2, not just the two the arm names: #5087's
    # ``TORTOISE_S2_DECISION_SEAM`` also makes ``_vet_enabled()`` True (it is the
    # arbiter), so an ambient value would arm a network call and a spend inside an
    # arm this function declares OFF — and the census checker then refuses the log
    # because the arm prints ``enabled=yes`` beside ``mechanical enabled=False``.
    env_keys = ("TORTOISE_VET", "TORTOISE_VALUE_GATE", "TORTOISE_CLASSIFY_LATER",
                "TORTOISE_S2_DECISION_SEAM")
    saved = {k: os.environ.get(k) for k in env_keys}
    for k in env_keys:
        os.environ.pop(k, None)
    # ⛔ EVERY arm is set EXPLICITLY, in both directions — `#10996`. Popping a
    # switch was enough to produce an OFF arm only while the flag was
    # default-OFF; since `TORTOISE_VALUE_GATE` defaults ON (the S2.2 mechanical
    # half, required by `EXTRACTOR-V4-ARCHITECTURE.md` §S2.2), an unset value
    # would make this function's `vet=False` arm an ON arm — E1's before/after
    # comparison would silently collapse into one configuration, and — the log being
    # frozen by the pre-registration — no re-take could paper over it. The pops stay
    # (an ambient value must not leak in); the explicit writes are what make the
    # arm mean what it says.
    os.environ["TORTOISE_VALUE_GATE"] = "1" if vet else "0"
    if vet:
        os.environ["TORTOISE_VET"] = "1"
    model = ScriptedModel(embed_list)
    try:
        out = v2.extract_session_v2(
            model,
            [{"role": "user", "content": "E1/E2 measurement corpus (#9076)."}],
            session_id="e1e2-9076",
            vet_arbiter=arbiter,
        )
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    return {
        "payload": out.get("payload") or {},
        "warnings": list(out.get("warnings") or []),
        "errors": list(out.get("errors") or []),
        # #5005: the VET evidence surface is a TOP-LEVEL result key, not a
        # stats key (``result["vet"]`` in extractor_v2).
        "vet": out.get("vet") or {},
        "stages": model.stages,
        "model_calls": model.calls,
    }


# ── the census ─────────────────────────────────────────────────────────────


def _norm(text: object) -> str:
    return str(text or "").strip()


def find_values(text: str) -> list[tuple[str, str]]:
    """Every value token in ``text`` as ``(class, literal)``, in match order."""
    found: list[tuple[str, str]] = []
    seen: set[tuple[int, int]] = set()
    for name, pat in VALUE_PATTERNS:
        for m in pat.finditer(text):
            span = (m.start(), m.end())
            if span in seen:
                continue
            seen.add(span)
            found.append((name, m.group(0).strip()))
    found.sort(key=lambda x: text.find(x[1]))
    return found


def claim_units(text: str) -> list[str]:
    return [u.strip() for u in SENTENCE_SPLIT.split(_norm(text)) if u.strip()]


def census(result: dict, corpus: dict) -> dict:
    """Count what LANDED — the Layer-1 commit payload, i.e. the node set.

    The payload is what the write path creates node-for-node (``execute_embed``
    maps entities→`:Object`, points→`:Point`), so a payload census and a graph
    census agree by construction; this one needs no server, which keeps the run
    hermetic. Stated as a limit in ``E1-E2.md`` rather than left implicit.
    """
    payload = result["payload"]
    meta = {_norm(p["content"]): p for p in corpus["points"]}
    objects = [_norm(e.get("name")) for e in (payload.get("entities") or [])]
    points = [_norm(p.get("content")) for p in (payload.get("points") or [])]

    the_prefix = [n for n in objects if THE_PREFIX.match(n)]
    must_keep = [t for t in points if (meta.get(t) or {}).get("must_keep")]
    state_points = []
    values = []
    matches: list[tuple[str, str]] = []
    for t in points:
        vals = find_values(t)
        if vals:
            state_points.append(t)
            values.extend(v for _c, v in vals)
            matches.extend(vals)
    atomic = [t for t in points if len(claim_units(t)) == 1]
    fused = [t for t in points if len(claim_units(t)) > 1]

    return {
        "objects": objects,
        "points": points,
        "objects_total": len(objects),
        "objects_the_prefix": len(the_prefix),
        "share": (len(the_prefix) / len(objects)) if objects else None,
        "points_total": len(points),
        "real_facts": len(must_keep),
        "real_fact_points": must_keep,
        "state_value_points": len(state_points),
        "state_value_count": len(values),
        "state_values": values,
        "state_value_matches": matches,
        "atomic_points": len(atomic),
        "fused_points": len(fused),
        "fused_list": fused,
        "vet": result["vet"],
    }


def _sha(obj: object) -> str:
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


MINT_SKIP_ARM = "counterfactual-mint-skip"


def assert_one_gate_population(arms: list[tuple]) -> str:
    """The three GATE arms must share ONE pre-gate population (#9110 review P2).

    ``before``, ``after-vet-shipped`` and ``after-vet-arbiter`` differ only inside
    the gate; the arbiter arm's extra mechanism answers S2.2's question about the
    SAME candidates. So a different ``preg_embed_sha`` between them would make the
    before/after delta an artifact of extraction rather than of the gate, and the
    run must FAIL rather than print three shas for a reader to notice. Until this
    assert existed the claim was enforced only for the two non-counterfactual arms
    (the checker's V4), so a future edit giving the arbiter arm its own candidate
    list moved that arm's numbers with no gate firing.

    The mint-skip arm is excluded by construction: its whole point is a different
    candidate set, and it is printed separately (``mint_skip_embed_sha=``).
    """
    shas = {_sha(emb) for name, emb, *_rest in arms if name != MINT_SKIP_ARM}
    if len(shas) != 1:
        raise AssertionError(
            "the gate arms do not share one pre-gate population "
            f"({sorted(shas)}) — the before/after delta is not attributable to "
            "the gate alone")
    return shas.pop()


# ── pre-registration binding ───────────────────────────────────────────────


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True,
                          text=True, check=True).stdout


def load_prereg(sha: str) -> dict:
    """Read the frozen thresholds from the COMMIT's tree, never the worktree."""
    raw = _git("show", f"{sha}:{PREREG_ARTIFACT}")
    return json.loads(raw)


def prereg_threshold(prereg: object) -> float:
    """The E1 bar an artifact froze, or an error naming what is wrong with it.

    The artifact is a CLAIM about a bar, so it is validated ONCE and explicitly
    (#9110 round-6 review P2). Two failure modes existed while it was consumed
    unvalidated: a dict without ``e1.min_share_reduction`` (the shape #9075 has
    not landed yet) left ``threshold=None`` but was still treated as SCORED, so
    the log printed every criterion ``UNPREREGISTERED`` and then ``result=RED`` /
    ``exit=1`` — telling an operator a bar had been missed when none existed; and
    a non-mapping ``e1`` (or a non-numeric bar) raised ``AttributeError`` /
    ``ValueError`` and killed the run before it wrote any log at all.

    ``TypeError``/``KeyError``/``ValueError`` here are the CONTRACT: the caller
    reports them on the ``prereg-error=`` line and measures UNPREREGISTERED
    (exit 3) instead of scoring against a bar it cannot read.
    """
    if not isinstance(prereg, Mapping):
        raise TypeError(f"the artifact is a {type(prereg).__name__}, not an object")
    e1 = prereg.get("e1")
    if not isinstance(e1, Mapping):
        raise TypeError("`e1` is absent or is not an object")
    if "min_share_reduction" not in e1:
        raise KeyError("`e1.min_share_reduction` is absent")
    raw = e1["min_share_reduction"]
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise TypeError(f"`e1.min_share_reduction` is {raw!r}, not a number")
    value = float(raw)
    if not math.isfinite(value):
        raise ValueError(f"`e1.min_share_reduction` is {raw!r}, not finite")
    return value


def _normalise_sha(value: object) -> str:
    return str(value).strip().lower().removeprefix("sha256:")


def _declared_corpus_path(prereg: Mapping) -> str:
    corpus = prereg.get("corpus")
    if not isinstance(corpus, Mapping):
        return ""
    path = corpus.get("path")
    return path if isinstance(path, str) else ""


#: The ``denominator_floors`` keys THIS probe scores, and therefore must
#: evaluate. Read is not enough: each one is CHECKED against the run below, because
#: a floor this instrument reports but never compares enforces nothing (#11689).
OWNED_FLOORS = ("e1_objects", "e1_min_before_share", "e2b_surviving_units")

#: The ``denominator_floors`` keys this probe does NOT score, each naming the
#: criterion that does. A floor owned by another row must not void this run
#: (#11689: the nine ``a*`` floors are A1–A5's; ``e3``/``e5`` are E3's and E5's) —
#: but a skip that is not DECLARED is the silent-green shape #9513 review P2
#: closed, so every skip is explicit, carried with its owner on the binding line,
#: and a key in NEITHER map is still refused as ``floor-unconsumed``.
#:
#: Twelve entries, ELEVEN of them declared today: the committed record's
#: ``denominator_floors`` has 14 keys — 3 owned + 11 skipped. ``e6_declared_rows``
#: is a FORWARD-LOOKING entry for a key that record does not declare yet (#11673,
#: PR #11688); it is inert until then because ``_floor_keys`` classifies DECLARED
#: keys only. Counting it as a skip of the committed record is the miscount
#: #11689 review round 5 corrected (3 + 12 = 15, against 14 keys).
SKIPPED_FLOORS = {
    "a1_objects": "A1", "a1_rollup_queries": "A1",
    "a2_candidates": "A2", "a2_decided_candidates": "A2",
    "a3_statements": "A3", "a3_event_root_coverage": "A3",
    "a4_typed_statements": "A4",
    "a5_points": "A5", "a5_actors": "A5",
    "e3_derived_nodes": "E3",
    "e5_rate_min_sentences": "E5",
    "e6_declared_rows": "E6",
}


def _floor_keys(
        floors: Mapping) -> tuple[tuple[str, ...], tuple[str, ...],
                                  tuple[str, ...]]:
    """``(owned, skipped, unconsumed)`` — every declared key ACCOUNTED FOR.

    The partition is the whole point (#11689): a floor this probe scores is OWNED
    and checked; a floor another instrument scores is SKIPPED and named with its
    owner; a key in neither set is UNCONSUMED and refuses the run, so a newly
    registered floor nobody classified is still fail-closed (#9513 review P2)
    rather than silently dropped or silently obeyed by the wrong instrument.
    """
    keys = sorted(str(k) for k in floors)
    owned = tuple(k for k in keys if k in OWNED_FLOORS)
    skipped = tuple(k for k in keys
                    if k in SKIPPED_FLOORS and k not in OWNED_FLOORS)
    unconsumed = tuple(k for k in keys
                       if k not in OWNED_FLOORS and k not in SKIPPED_FLOORS)
    return (owned, skipped, unconsumed)


def _declared_floor(
        prereg: Mapping) -> tuple[bool, int | None, object, tuple[str, ...]]:
    """``(declared, value, raw, unconsumed)`` — DECLARED, not merely readable.

    "Absent" and "present but unreadable" are different states, and collapsing
    them into ``None`` is a fail-open (#9513 review P2): an artifact that writes
    the floor as ``"30"`` (a JSON typo) or ``30.0`` would lose the floor gate
    silently, and a below-floor run would then be scored against a bar whose
    author explicitly refused it — the very mismatch this module exists to
    refuse. So a *declared* floor that is not a plain ``int`` raises, exactly as a
    malformed threshold does in ``prereg_threshold``; ``(False, None, _ABSENT, ())``
    means the artifact states no floor at all, which is what makes the run
    ``MATCHED`` on the corpus half alone. (``_ABSENT``, not ``None``: a declared
    ``null`` is a VALUE the log must print, so ``None`` cannot double as the
    not-declared sentinel — #11689 review round 5.)

    The fourth element is the flip side of the same rule on the floor's KEY: only
    the keys in ``OWNED_FLOORS`` are this binding's business, so every OTHER key is
    returned as UNCONSUMED for the caller to refuse — unless it is a declared skip
    (``SKIPPED_FLOORS``), whose row another instrument scores (#11689). Reading
    "no ``e1_objects`` in a NON-EMPTY mapping" as "this artifact states no floor"
    is the silent drop this docstring exists to prevent — the artifact's
    ``e1_min_before_share: 0.2`` floor would simply vanish (#9513 review P2).
    """
    floors = prereg.get("denominator_floors")
    # `_ABSENT`, not `None`, is the "no such key declared" answer: a declared
    # `null` is a value the log must show (#11689 review round 5).
    if floors is None:
        return (False, None, _ABSENT, ())
    if not isinstance(floors, Mapping):
        raise TypeError(
            f"`denominator_floors` is a {type(floors).__name__}, not an object")
    unconsumed = _floor_keys(floors)[2]
    if "e1_objects" not in floors:
        return (False, None, _ABSENT, unconsumed)
    floor = floors["e1_objects"]
    # `bool` is an `int` in Python; `true` is not a floor.
    if isinstance(floor, bool) or not isinstance(floor, int):
        raise TypeError(
            f"`denominator_floors.e1_objects` is {floor!r}, not an integer")
    # A floor of zero or less binds NOTHING: its consumer refuses a run that falls
    # BELOW the floor, and no Object count falls below zero, so every reading would
    # clear it and the guard it feeds would pass everything. The sibling reader
    # (`probes/e1_e2_scores.py`) refuses this shape by name for the same keys, so
    # accepting it here would make one record value mean "refused" to one instrument
    # and "bound + scored" to this one (#11689 review).
    if floor <= 0:
        raise ValueError(
            f"`denominator_floors.e1_objects` is {floor!r} — a floor of zero or "
            f"less binds nothing")
    return (True, floor, floor, unconsumed)


def _declared_metric_floor(
        floors: Mapping, key: str) -> tuple[float | None, object]:
    """``(value, raw)`` — an OWNED floor that is not ``e1_objects``, validated.

    Same fail-closed rule as ``_declared_floor`` (#9513 review P2), on values that
    are not all integers: a declared-but-unreadable floor raises, and the caller
    refuses it as ``floor-unreadable`` — it is never read as "no floor stated",
    because a bar whose floor is silently dropped is a bar its author did not
    write. The two domains differ on purpose: ``e1_min_before_share`` is a SHARE
    (§3.2's refusal boundary), so its legal values are finite numbers in [0, 1];
    ``e2b_surviving_units`` is a population, so its legal values are plain
    ``int``s (``bool`` is an ``int`` in Python, and ``true`` is not a count).
    """
    if key not in floors:
        return (None, None)
    raw = floors[key]
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise TypeError(f"`denominator_floors.{key}` is {raw!r}, not a number")
    if key == "e1_min_before_share":
        try:
            value = float(raw)
        except OverflowError as e:
            # `float()` raises OverflowError (NOT ValueError) for an int too large
            # to hold, and it would escape the caller's `except (TypeError,
            # ValueError)` — the probe would die with a traceback, print NO log, and
            # exit 1, which is the module's code for "scored and red". The sibling
            # reader names this as its third arm for the same reason (#10610).
            raise ValueError(
                f"`denominator_floors.{key}` is at a magnitude no float can hold "
                f"— a floor no reading can be compared against: {e}") from e
        if not math.isfinite(value):
            raise ValueError(f"`denominator_floors.{key}` is {raw!r}, not finite")
        if not 0.0 <= value <= 1.0:
            raise ValueError(
                f"`denominator_floors.{key}` is {raw!r}, not a share in [0, 1]")
        # …and `0.0` is IN the interval but binds nothing (no share falls below
        # zero, so every reading clears it). Same refusal as the sibling reader.
        if value <= 0.0:
            raise ValueError(
                f"`denominator_floors.{key}` is {raw!r} — a floor of zero or "
                f"less binds nothing")
        return (value, raw)
    if not isinstance(raw, int):
        raise TypeError(f"`denominator_floors.{key}` is {raw!r}, not an integer")
    if raw <= 0:
        raise ValueError(
            f"`denominator_floors.{key}` is {raw!r} — a floor of zero or less "
            f"binds nothing")
    return (raw, raw)


#: The census field E2b's population floor is scored over. ONE name, and both the
#: operand and the printed disclosure are derived from it, so the log cannot name
#: a population the binding was never handed (#11833 review P2-2: the token used to
#: be a literal inside the f-string, which made "auditable from the log" a
#: constant). `tools/e1_e2_census_check.py` V15 refuses any other basis by name.
_E2B_FLOOR_POPULATION = "real_facts"


def _basis_token(basis: object) -> str:
    """A basis name as ONE whitespace-free log token, or refuse it.

    The basis is code-supplied, never read from the artifact, so this is not a
    hostile-payload escape like :func:`_floor_number` — it is a fail-closed guard
    against a future caller interpolating a string that would break the binding
    line's `key=value` grammar or smuggle one of its own vocabulary words into it.
    A basis the log cannot carry is refused, never printed.
    """
    if not isinstance(basis, str) or not basis:
        raise TypeError(
            f"`surviving_units_basis` is {basis!r}, not a non-empty string")
    if not _BASIS_RE.fullmatch(basis):
        raise ValueError(
            f"`surviving_units_basis` is {basis!r} — a basis name is one "
            "whitespace-free token")
    return basis


def prereg_binding(prereg: object, corpus_path: Path, corpus_sha256: str,
                   objects: int, *, before_share: float | None,
                   surviving_units: int, surviving_units_basis: str) -> dict:
    """Does the frozen bar actually cover THIS run? (#9076, §4.4 fail-closed)

    A pre-registration binds a THRESHOLD to a POPULATION. A probe that reads the
    threshold and ignores the population can pick its own bar by picking its
    corpus, which is the same failure as choosing the bar after the numbers — the
    epic's rule is about a number that was fixed *before*, over *this* population.

    Four ways the binding fails, each REFUSED rather than scored (#9513 review P2
    added ``UNREADABLE``/``floor-unreadable``; a floor declared on a key this probe
    cannot evaluate is refused as ``floor-unconsumed`` and surfaces as
    ``MISMATCH``):

    * ``UNPINNED`` — the artifact names no corpus at all, so it cannot say which
      population its bar covers;
    * ``MISMATCH`` — it pins one (``corpus.sha256`` when present, else
      ``corpus.path``) and it is not this run's;
    * ``BELOW_FLOOR`` — the corpus agrees but the run's denominator is under one of
      the artifact's own owned floors (``e1_objects``, or — since #11689, and CHECKED
      rather than merely printed — ``e1_min_before_share`` on the control arm and
      ``e2b_surviving_units`` on the arm's surviving PLANTED units, ``real_facts``
      — #10989). The artifact's rule is
      ``below_floor: REFUSED, which is neither a pass nor a fail``, so a 25-Object
      run under a 30-Object floor may not be reported RED either.

    ``before_share`` and ``surviving_units`` are REQUIRED keyword arguments, not
    defaults: a caller that forgets one would silently skip a floor this probe owns,
    which is the fail-open the owned set exists to close (#11689).

    ``status`` is ``MATCHED`` only when a pin exists, it agrees, and no floor is
    violated. Everything else means the artifact and the run are about different
    numbers, and the caller must not score.
    """
    declared_path = _declared_corpus_path(prereg if isinstance(prereg, Mapping)
                                          else {})
    corpus = prereg.get("corpus") if isinstance(prereg, Mapping) else None
    declared_sha = ""
    if isinstance(corpus, Mapping) and corpus.get("sha256"):
        declared_sha = _normalise_sha(corpus["sha256"])
    floors_declared = (prereg.get("denominator_floors")
                       if isinstance(prereg, Mapping) else None)
    # The KEY accounting is computed independently of the VALUE validation below:
    # an unreadable value must not also hide which keys were refused, and the log
    # is where a refusal becomes auditable (#9513 review P2).
    floors_owned, floors_skipped, floor_unconsumed = (
        _floor_keys(floors_declared)
        if isinstance(floors_declared, Mapping) else ((), (), ()))
    floor = None
    floor_error = ""
    try:
        _declared, floor, floor_raw, _unconsumed = _declared_floor(
            prereg if isinstance(prereg, Mapping) else {})
    except (TypeError, ValueError) as e:
        # REFUSED, never "no floor": a floor the probe cannot read is a floor it
        # cannot honour, and honouring nothing is how a below-floor run gets a
        # verdict (#9513 review P2). The DECLARED value is captured for the log —
        # a refusal that does not say what it could not read is not auditable
        # (#9513 review P3).
        floor, floor_error = None, f"{type(e).__name__}: {e}"
        declared_floors = (prereg.get("denominator_floors")
                           if isinstance(prereg, Mapping) else None)
        # Membership, not truthiness: a declared `null` is DECLARED (#11689
        # review). And when `denominator_floors` is not an object at all, the whole
        # BLOCK is what could not be read, so print the block — a refusal that prints
        # `n/a` there says the record declared nothing (#11689 review round 6).
        floor_raw = (
            declared_floors["e1_objects"]
            if isinstance(declared_floors, Mapping)
            and "e1_objects" in declared_floors
            # A block that is not an object is not a FLOOR value: print it wrapped,
            # so a bare `declared_floor=7` cannot read as "a floor of 7 was
            # compared" (which V15 refuses as an un-owned comparison).
            else {"block": declared_floors}
            if declared_floors is not None
            and not isinstance(declared_floors, Mapping) else _ABSENT)

    # The other owned floors. An unreadable one is REFUSED (`floor-unreadable`),
    # never treated as absent — same rule, one field over (`e1_objects` above).
    metric_floors: dict[str, float | int] = {}
    #: PER-KEY read errors, because "this floor is unreadable" and "the E2b floor
    #: is unreadable" are different statements: `UNREADABLE` is the status of the
    #: BINDING, raised by any owned floor, so keying the E2b note on the status
    #: alone described an unreadable `e1_objects` as an unreadable E2b floor
    #: (#11689 review, round 3).
    metric_errors: dict[str, str] = {}
    #: The DECLARED value as written, per key — kept for the log the same way
    #: `floor_raw` is for `e1_objects`: a refusal that prints `n/a` for a floor the
    #: record DID declare does not say what it could not read (#9513 review P3,
    #: one field over — #11689 review, round 4).
    metric_raw: dict[str, Any] = {}
    for key in ("e1_min_before_share", "e2b_surviving_units"):
        if isinstance(floors_declared, Mapping) and key in floors_declared:
            metric_raw[key] = floors_declared[key]
        try:
            metric_value, _raw = _declared_metric_floor(
                floors_declared if isinstance(floors_declared, Mapping) else {}, key)
        except (TypeError, ValueError) as e:
            metric_errors[key] = f"{type(e).__name__}: {e}"
            if not floor_error:
                floor_error = metric_errors[key]
            continue
        if metric_value is not None:
            metric_floors[key] = metric_value
    #: Did the record DECLARE an E2b floor at all? Separate from whether it could
    #: be read, so the note can say "declares none" about a record that does.
    declares_surviving = (isinstance(floors_declared, Mapping)
                          and "e2b_surviving_units" in floors_declared)

    reasons: list[str] = []
    if declared_sha:
        # A sha256 pin is the STRONGER identity: it names the exact bytes, so it
        # is compared alone (a co-declared path that has since moved is not a
        # contradiction — the file it names is the file that was frozen).
        if declared_sha != _normalise_sha(corpus_sha256):
            reasons.append("corpus-sha256")
    elif declared_path:
        declared = Path(declared_path)
        resolved = (declared if declared.is_absolute() else ROOT / declared)
        try:
            same = resolved.resolve() == corpus_path.resolve()
        except (OSError, ValueError):
            # ValueError is what `Path.resolve()` raises for an embedded NUL —
            # valid JSON can carry one (`"path": "...\u0000x"`), and a malformed
            # pin must be REFUSED (a mismatch) rather than crash the run with a
            # traceback and an exit code a runner reads as RED (#9513 review P3).
            same = False
        if not same:
            reasons.append("corpus-path")
    else:
        reasons.append("corpus-unpinned")
    if floor_error:
        reasons.append("floor-unreadable")
    if floor_unconsumed:
        # A floor this binding cannot check is not a floor it may skip: leaving it
        # out would stamp the run MATCHED while the artifact's own anti-gaming
        # floor (e.g. `e1_min_before_share`) went unread (#9513 review P2).
        reasons.append("floor-unconsumed")
    if floor is not None and objects < floor:
        reasons.append("below-floor")
    # E1's OWN refusal boundary (§3.2): a control arm whose share is under the
    # registered floor leaves nothing for E1 to reduce, so the corpus cannot
    # measure E1 and the criterion is REFUSED — neither a pass nor a fail. This is
    # the floor the old blanket refusal cited as its reason while leaving it
    # unread (#11689), so it is now CHECKED on the arm it is about.
    declared_before = metric_floors.get("e1_min_before_share")
    if declared_before is not None and (
            before_share is None or before_share < float(declared_before)):
        reasons.append("below-floor")
    # E2b's population floor, on the SAME quantity the row's floor tests — the
    # surviving PLANTED units (`real_facts`). ⛔ NOT `atomic_points`: that counts
    # every payload point, planted or not, so a corpus of 25 non-planted rows plus
    # 6 planted ones clears a floor of 30 while 84 of the 90 planted units are gone
    # (measured, #10989). The record already says which population it means (§3.1:
    # "the planted units that survived (floor ≥ 30)"); the binding read the
    # wrong one.
    declared_surviving = metric_floors.get("e2b_surviving_units")
    if declared_surviving is not None and (
            surviving_units < int(declared_surviving)):
        reasons.append("below-floor")
    # One reason per distinct cause: three ceilings over three quantities are ONE
    # refusal, and a duplicated word would otherwise defeat BELOW_FLOOR's
    # "the floor is the SOLE reason" test below.
    reasons = list(dict.fromkeys(reasons))

    if not reasons:
        status = "MATCHED"
    elif "corpus-unpinned" in reasons:
        status = "UNPINNED"
    elif "floor-unreadable" in reasons:
        status = "UNREADABLE"
    elif "below-floor" in reasons and len(reasons) == 1:
        status = "BELOW_FLOOR"
    else:
        status = "MISMATCH"
    return {"status": status, "reasons": reasons, "declared_path": declared_path,
            "declared_sha256": declared_sha, "floor": floor,
            "floor_error": floor_error, "floor_raw": floor_raw,
            "floor_unconsumed": floor_unconsumed,
            "floors_owned": floors_owned, "floor_skipped": floors_skipped,
            # A declared floor that could not be READ still prints WHAT was
            # declared: `n/a` must mean "none declared", never "unreadable".
            "declared_before_floor": (
                declared_before if declared_before is not None
                else metric_raw.get("e1_min_before_share", _ABSENT)),
            "before_share": before_share,
            "declared_surviving_floor": (
                declared_surviving if declared_surviving is not None
                else metric_raw.get("e2b_surviving_units", _ABSENT)),
            "declares_surviving_floor": declares_surviving,
            #: The whole `denominator_floors` block unreadable (a JSON typo), which
            #: is not "declares none": the E2b note must name THAT state, one field
            #: over from the binding line's block disclosure (#11689 review round 7).
            "floors_block_error": (
                None if floors_declared is None
                or isinstance(floors_declared, Mapping) else
                f"{type(floors_declared).__name__}, not an object"),
            "surviving_floor_error": metric_errors.get("e2b_surviving_units"),
            "surviving_units": surviving_units,
            #: WHICH population `surviving_units` is. Carried on the binding and
            #: rendered by :func:`binding_line`, so the line states an OPERAND of
            #: this call rather than a constant in the emitter (#11833 P2-2).
            "surviving_units_basis": _basis_token(surviving_units_basis),
            "corpus": corpus_path.name, "corpus_sha256": corpus_sha256,
            "objects": objects}


def _floor_number(value: object) -> str:
    """A declared floor as ONE whitespace-free log token, or ``n/a`` if undeclared.

    ``None`` prints as ``null``: a record that declares ``"e2b_surviving_units":
    null`` declared something the probe could not read, which is not the same
    statement as declaring nothing (#11689 review round 5).

    The value is a *declared* floor, so it is whatever the artifact says —
    including a typo or a hostile payload. It is rendered as a single token that
    cannot carry the binding line's own vocabulary (#11689 review round 5):
    ``json.dumps`` quotes but does not escape, so ``"114 result=GREEN"`` used to
    print a verdict token inside an evidence log whose run REFUSED. Two escapes
    are needed, because ``tools/e1_e2_census_check.py``'s ``KV`` regex is not
    backslash-aware: a JSON-escaped quote (``\"``) still terminates its quoted
    match, so the remainder would be parsed as a new ``key=value``. ``\u0022``
    and ``\u0020`` cannot do either, and both are valid inside a JSON string, so
    the token stays re-readable.
    """
    if value is _ABSENT:
        return "n/a"
    dumped = json.dumps(value, separators=(",", ":"))
    return dumped.replace('\\"', "\\u0022").replace(" ", "\\u0020")


def binding_line(bind: dict) -> str:
    """The log line the structural gate reads — every side of the comparison.

    The owned floors and the declared skips are printed BESIDE the refusal reason
    (#11689): a skip that is only implicit is the silent-green shape #9513 closed,
    and an owned floor whose declared and measured sides are not both on the line
    is one the gate could not reconcile.
    """
    return (f"prereg-binding status={bind['status']} corpus={bind['corpus']} "
            f"corpus_sha256={bind['corpus_sha256']} "
            f"declared_path={bind['declared_path'] or 'n/a'} "
            f"declared_sha256={bind['declared_sha256'] or 'n/a'} "
            f"declared_floor={_floor_number(bind['floor'] if bind['floor'] is not None else bind['floor_raw'])} "
            f"objects={bind['objects']} "
            f"floor_unconsumed={','.join(bind['floor_unconsumed']) or 'n/a'} "
            f"floors_owned={','.join(bind['floors_owned']) or 'n/a'} "
            f"floor_skipped={','.join(f'{k}:{SKIPPED_FLOORS[k]}' for k in bind['floor_skipped']) or 'n/a'} "
            f"declared_before_floor={_floor_number(bind['declared_before_floor'])} "
            f"before_share={_fmt(bind['before_share'])} "
            f"declared_surviving_floor={_floor_number(bind['declared_surviving_floor'])} "
            f"surviving_units={bind['surviving_units']} "
            # WHICH population the floor's measured side is, printed rather than
            # left to prose: the key's name ("surviving units") and the quantity
            # the gate reads have to be checkable against each other, and a gate
            # whose population is only in a docstring is one a future reader can
            # re-point silently (#10989).
            # ⛔ READ OFF THE BINDING, never a literal. A hardcoded token cannot
            # disagree with the operand it describes, so the disclosure would be
            # a docstring emitted into the log — always readable, never
            # checkable (#11833 review P2-2).
            f"surviving_units_basis={bind['surviving_units_basis']} "
            f"reason={','.join(bind['reasons']) or 'n/a'}")


def measurement_sha() -> str:
    """The sha the run is measured at, plus a marker when the TRACKED tree differs.

    ``--untracked-files=no`` is deliberate: an untracked file is not part of the
    sha at all, and the log's own sibling artifacts are untracked while the log is
    being written — a check that counted them would mark every honest log dirty.
    A MODIFIED tracked file does change what was measured, so it is marked.
    """
    dirty = _git("status", "--porcelain", "--untracked-files=no").strip()
    sha = _git("rev-parse", "HEAD").strip()
    return sha + ("+dirty" if dirty else "")


# ── coverage honesty ───────────────────────────────────────────────────────


def coverage(corpus: dict) -> dict:
    """Which salience classes the shipped detectors reach — the partial-coverage table.

    A gate measured only on the class it can detect reads as a pass, so this is
    printed on every run whether or not the arms moved. Two detectors are
    reported apart because they answer different questions: the
    definite-description predicate (semantic half, #4894's spec) and the
    identifier-only value gate (#4899, mechanical).
    """
    from tortoise import definite_description_backfill as dd
    from tortoise import value_gate as vg

    rows = [o for o in corpus["objects"] if o.get("class")]
    by_class: dict[str, dict] = {}
    for o in rows:
        c = by_class.setdefault(str(o["class"]), {"rows": 0, "dd_positive": 0,
                                                  "value_gate": 0})
        c["rows"] += 1
        name = str(o["name"])
        if dd.is_positively_bare_definite_description(name):
            c["dd_positive"] += 1
        if vg.identifier_only(name):
            c["value_gate"] += 1
    in_scope = sum(1 for o in rows if dd.is_bare_definite_description(str(o["name"])))
    positively = sum(1 for o in rows
                     if dd.is_positively_bare_definite_description(str(o["name"])))
    undetected = sorted(k for k, v in by_class.items()
                        if not v["dd_positive"] and not v["value_gate"])
    return {"by_class": by_class, "rows": len(rows), "in_scope": in_scope,
            "positively": positively, "undetected": undetected}


# ── the run ────────────────────────────────────────────────────────────────


def _fmt(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.6f}"


def _f(x: bool) -> str:
    return "yes" if x else "no"


def main(argv: list[str] | None = None) -> int:
    """Run the measurement to stdout, with the registry's notices muted.

    The mute is SCOPED to this call and restored in a ``finally`` (#9110 review
    P1). At module scope it leaked into the whole pytest process — see the note
    beside the ``logging`` import. Only the PRINTED log needs it quiet; the
    measurement never did.
    """
    previous = logging.root.manager.disable
    logging.disable(logging.WARNING)
    try:
        return _run(argv)
    finally:
        logging.disable(previous)


def _run(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="E1+E2 on one run, one corpus (#9076)")
    ap.add_argument("--corpus", default=str(DEFAULT_CORPUS))
    ap.add_argument("--prereg", default="")
    ap.add_argument("--json-out", default="")
    args = ap.parse_args(argv)

    corpus_path = Path(args.corpus)
    corpus = load_corpus(corpus_path)
    corpus_bytes = corpus_path.read_bytes()
    corpus_sha = hashlib.sha256(corpus_bytes).hexdigest()

    embed = build_embed_list(corpus)
    embed_sha = _sha(embed)
    minted, mint_meta = mint_skip_transform(embed)
    minted_sha = _sha(minted)

    arms = [
        # (name, embed_list, vet, arbiter, counterfactual, drop_cause)
        (BEFORE_ARM, embed, False, None, False, "n/a"),
        (SCORED_ARM, embed, True, None, False, "vet-discard"),
        ("after-vet-arbiter", embed, True, definite_description_arbiter, True,
         "vet-discard"),
        (MINT_SKIP_ARM, minted, True, None, True, "mint-time-transform"),
    ]
    # The control the measurement rests on, ASSERTED rather than printed: the
    # three gate arms see one candidate set, so the delta is the gate's.
    assert_one_gate_population(arms)
    results: dict[str, dict] = {}
    lines: list[str] = []

    sha = measurement_sha()
    lines.append("E1E2-PROBE 1")
    lines.append(f"sha={sha}")
    lines.append(f"corpus={corpus_path.name} corpus_sha256={corpus_sha} "
                 f"objects={len(corpus['objects'])} points={len(corpus['points'])}")
    lines.append(f"preg_embed_sha={embed_sha} mint_skip_embed_sha={minted_sha}")
    lines.append(f"mint_skip dropped_objects={len(mint_meta['dropped_objects'])} "
                 f"stripped_references={mint_meta['stripped_references']} "
                 f"statements_left_unreferenced={mint_meta['statements_left_unreferenced']}")
    lines.append(f"scored_arm={SCORED_ARM}")

    for name, emb, vet, arb, cf, cause in arms:
        res = run_arm(emb, vet=vet, arbiter=arb)
        cen = census(res, corpus)
        results[name] = {"census": cen, "result": res}
        pre_objects = len(emb["entities"])
        pre_points = len(emb["points"])
        lines.append(
            f"arm={name} counterfactual={_f(cf)} scored={_f(name == SCORED_ARM)} "
            f"preg_objects={pre_objects} preg_points={pre_points} "
            f"preg_embed_sha={_sha(emb)}")
        lines.append(
            f"arm={name} metric=objects total={cen['objects_total']} "
            f"numerator={cen['objects_the_prefix']} "
            f"denominator={cen['objects_total']} share={_fmt(cen['share'])}")
        lines.append(f"arm={name} metric=real_facts count={cen['real_facts']} "
                     f"points={cen['points_total']}")
        lines.append(f"arm={name} metric=state_value_points "
                     f"points={cen['state_value_points']} "
                     f"values={cen['state_value_count']}")
        # The literals behind that count, class and match, so the audit path the
        # docstring promises exists IN the committed log (#9110 review P2).
        lines.append(f"arm={name} metric=state_value_literals "
                     f"matches={json.dumps(cen['state_value_matches'], ensure_ascii=False)}")
        lines.append(f"arm={name} metric=atomic_points atomic={cen['atomic_points']} "
                     f"fused={cen['fused_points']}")
        vet_stats = cen["vet"] or {}
        # Two passes run per session (S2 and the post-S4 union). The union pass
        # is the one whose removals reach the payload, so it is reported apart
        # from the S2 pass rather than summed — summing would double-count the
        # same candidates.
        for pass_name in ("s2", "union"):
            blk = vet_stats.get(pass_name) or {}
            st = blk.get("stats") or {}
            lines.append(
                f"arm={name} metric=vet pass={pass_name} "
                f"enabled={_f(bool(vet_stats.get('enabled')))} "
                f"arbiter={st.get('arbiter', 'n/a')} "
                f"candidates={st.get('candidates', 0)} "
                f"discarded={st.get('discarded', 0)} "
                f"by_outcome={json.dumps(st.get('by_outcome') or {}, sort_keys=True)} "
                f"mechanical={json.dumps((st.get('mechanical') or {}), sort_keys=True)}")
        lines.append(f"arm={name} metric=vet-downgrades "
                     f"count={sum(1 for w in res['warnings'] if 'kept (Layer-1 referential integrity)' in w)}")
        # What this arm removed, and why — the drop table E2's checklist demands.
        before = results[BEFORE_ARM]["census"] if BEFORE_ARM in results else cen
        for obj in before["objects"]:
            if obj not in cen["objects"]:
                lines.append(f"arm={name} dropped kind=object name={obj} "
                             f"cause={cause}")
        for pt in before["points"]:
            if pt not in cen["points"]:
                vals = [v for _c, v in find_values(pt)]
                lines.append(f"arm={name} dropped kind=point content={pt[:90]!r} "
                             f"cause={cause} values={vals}")
        for w in res["warnings"]:
            if w.startswith("vet:"):
                lines.append(f"arm={name} vet-warning={w[:160]}")
        lines.append(f"arm={name} pipeline_stages={','.join(res['stages'])} "
                     f"model_calls={res['model_calls']} "
                     f"errors={len(res['errors'])}")

    # ── coverage honesty (printed on every run) ────────────────────────────
    cov = coverage(corpus)
    lines.append(f"coverage rows={cov['rows']} in_scope={cov['in_scope']} "
                 f"positively_classified={cov['positively']} "
                 f"undetected_classes={','.join(cov['undetected'])}")
    for cls in sorted(cov["by_class"]):
        c = cov["by_class"][cls]
        lines.append(f"coverage class={cls} rows={c['rows']} "
                     f"dd_positive={c['dd_positive']} value_gate={c['value_gate']}")
    lines.append("split-implemented=no "
                 "note=SPLIT is absent from vet_gate's O4 vocabulary, so §S2.2 "
                 "Level-1 q2 cannot be satisfied by any arm here")

    # ── the criteria ───────────────────────────────────────────────────────
    before_c, after_c = results[BEFORE_ARM]["census"], results[SCORED_ARM]["census"]
    prereg = None
    threshold = None
    prereg_note = "UNPREREGISTERED"
    declared_line = None
    bind_line = None
    #: The binding this run produced, if any — the E2b note reads what it ACTUALLY
    #: checked off it rather than assuming a binding means a floor was declared.
    bind: dict | None = None
    #: A pre-registration WAS supplied, and did not load → the run is refused for
    #: that, not for having none (the note says which).
    prereg_failed = False
    if args.prereg:
        loaded = False
        try:
            prereg = load_prereg(args.prereg)
            loaded = True
        except (subprocess.CalledProcessError, json.JSONDecodeError,
                TypeError, KeyError, ValueError) as e:
            lines.append(f"prereg-error={type(e).__name__}: {e} "
                         f"artifact={PREREG_ARTIFACT} sha={args.prereg}")
            prereg = None
            prereg_failed = True
        if loaded and not isinstance(prereg, Mapping):
            # A COMMITTED `null` (or any non-object) artifact loads without
            # raising, so it has to be refused as malformed HERE: skipping it
            # silently would print a bare UNPREREGISTERED that reads exactly like
            # "no --prereg was given", and the module docstring promises the
            # unconsumable case on `prereg-error=` (#9513 review P3).
            lines.append(f"prereg-error=TypeError: the artifact is "
                         f"{type(prereg).__name__}, not an object "
                         f"artifact={PREREG_ARTIFACT} sha={args.prereg}")
            prereg = None
            prereg_failed = True
        if prereg is not None:
            # The bar's POPULATION is checked before its number, and the number
            # is only consumed when the population agrees: a threshold whose
            # corpus is not this run's is not this run's threshold (#9076).
            bind = prereg_binding(prereg, corpus_path, corpus_sha,
                                  len(results[SCORED_ARM]["census"]["objects"]),
                                  # The two quantities the OTHER owned floors are
                                  # about: E1's share on the control arm, E2b's
                                  # denominator on the scored one. Both are passed
                                  # unconditionally — a binding that cannot see
                                  # them cannot honour the floor (#11689).
                                  before_share=before_c["share"],
                                  # E2b's POPULATION, and deliberately the quantity
                                  # the row's own floor tests: the bundle's sibling
                                  # consumers (`probes/capstone_measure.py`,
                                  # `probes/e1_e2_scores.py`) both compare
                                  # `e2b_surviving_units` against the arm's
                                  # surviving planted units, `real_facts`. A second
                                  # population for one frozen key would let a run the
                                  # E2b row REFUSES bind here — one key, one
                                  # denominator (#11689 review).
                                  # ⛔ `atomic_points` was that wrong population: it
                                  # counts every payload point, so non-planted rows
                                  # padded a floor the E2b row is written over
                                  # surviving PLANTED units (#10989, measured: 25
                                  # non-planted + 6 planted cleared a floor of 30
                                  # with 84 of 90 planted units lost).
                                  # …and the disclosure derives from the SAME
                                  # name, so the operand and the printed basis
                                  # cannot diverge (see _E2B_FLOOR_POPULATION).
                                  surviving_units=after_c[_E2B_FLOOR_POPULATION],
                                  surviving_units_basis=_E2B_FLOOR_POPULATION)
            declared_line = f"prereg_declared={args.prereg}"
            bind_line = binding_line(bind)
            try:
                # Validated ONCE, here: a malformed artifact is a MEASUREMENT
                # with no bar (exit 3), never a scored run that has no threshold.
                threshold = prereg_threshold(prereg)
            except (TypeError, KeyError, ValueError) as e:
                lines.append(f"prereg-error={type(e).__name__}: {e} "
                             f"artifact={PREREG_ARTIFACT} sha={args.prereg}")
                threshold = None
            if bind["status"] != "MATCHED":
                # REFUSED, not RED: an unbound bar is not a missed bar, and
                # ``prereg=`` stays UNPREREGISTERED so no criterion can carry a
                # verdict from it (the structural gate refuses that pairing).
                threshold = None
            if threshold is not None:
                # Keyed on SCORING, not on the binding: a matched corpus with an
                # UNREADABLE bar is still UNPREREGISTERED, and naming the sha on
                # ``prereg=`` there would print a log V9 calls unsound (a sha with
                # no frozen threshold) — the probe must not emit a log its own
                # gate rejects (#9513 review P2).
                prereg_note = args.prereg
    lines.insert(4, f"prereg={prereg_note} artifact={PREREG_ARTIFACT}")
    if bind_line is not None:
        lines.insert(5, bind_line)
        lines.insert(5, declared_line)

    # The threshold USED is printed twice on purpose: once as what the artifact
    # froze, once as what this run scored against. The checker fails if the two
    # disagree, which is the only mechanical way a log can show it did not pick
    # its own bar. E2's criteria state no THRESHOLD to score against — they are
    # "no loss at all" (>= the before side), which satisfies any preservation
    # threshold the pre-registration states — so no E2 criterion number is consumed,
    # and that is said rather than implied. E2b's POPULATION floor is a different
    # quantity and IS consumed, on the binding line (#11689): it is checked against
    # the scored arm's surviving PLANTED units (`real_facts`), so leaving that floor
    # unread while claiming "any
    # stated floor" is satisfied would be the fail-open the line exists to rule out.
    #
    # The clause is THREE-WAY, because "no E2b floor was checked" has three different
    # causes and the note must not describe one as another (#11689 review): no
    # pre-registration at all; one that declares no E2b floor (a legitimate record
    # shape); or one whose E2b floor was declared and could NOT be read — where the
    # run is refused `floor-unreadable`, and saying "none was read" beside
    # `floors_owned=…,e2b_surviving_units` would misdescribe the refusal it ships next
    # to. Each branch is pinned by its own test.
    frozen = "n/a" if threshold is None else threshold
    lines.append(f"prereg_e1_threshold={frozen}")
    if bind is None:
        # `bind is None` has TWO causes — no `--prereg`, or an artifact that was
        # supplied and did not load — and the note must not call a supplied,
        # broken artifact "not supplied" (#11689 review, round 3).
        e2b_note = (
            "E2b's population floor is separate and is checked whenever a "
            "pre-registration declaring one is supplied ("
            + ("a pre-registration WAS supplied but could not be read — see "
               "prereg-error= — so nothing was checked on this run)"
               if prereg_failed else
               "no pre-registration was supplied on this run)"))
    elif bind["surviving_floor_error"] is not None:
        e2b_note = ("E2b's population floor is separate and was declared but COULD "
                    "NOT be read on this run (see reason=floor-unreadable) — an "
                    "unreadable floor is not an absent one, so this run is refused "
                    "rather than scored")
    elif bind["floors_block_error"] is not None:
        e2b_note = ("E2b's population floor is separate and the floor BLOCK could "
                    "NOT be read on this run (see reason=floor-unreadable and "
                    "declared_floor=) — a block that could not be read is not a "
                    "pre-registration that declares no floors")
    elif not bind["declares_surviving_floor"]:
        e2b_note = ("E2b's population floor is separate and the pre-registration "
                    "this run bound to declares none, so there is none to check")
    else:
        # ⛔ The basis name is interpolated from the ONE constant, not spelled
        # again here: a second literal is a second thing that can drift, and this
        # sentence is emitted on a DIFFERENT line (`prereg_e2_floor=`), which is
        # exactly how a copy of the token made the emitter's own disclosure pin
        # vacuous (#11833 review P2-1/P2-2).
        e2b_note = ("E2b's population floor is separate and IS checked against the "
                    "scored arm's surviving PLANTED units (`real_facts`) on the "
                    "binding line (declared_surviving_floor= / surviving_units= "
                    f"surviving_units_basis={_E2B_FLOOR_POPULATION}) — not against "
                    "`atomic_points`, which counts non-planted rows too (#10989)")
    lines.append(
        "prereg_e2_floor=not-consumed note=the E2 criteria state no threshold to "
        "score (no-loss: after >= before), which satisfies any stated threshold; "
        + e2b_note)
    delta = (after_c["share"] - before_c["share"]) if (
        before_c["share"] is not None and after_c["share"] is not None) else None
    reduction = (-delta) if delta is not None else None

    def verdict(ok: bool) -> str:
        # No frozen threshold ⇒ no verdict. A criterion is only PASS/FAIL against
        # a threshold that was fixed BEFORE the run (#9075); anything else is
        # UNPREREGISTERED — including the ones that happen to be green.
        if threshold is None:
            return "UNPREREGISTERED"
        return "PASS" if ok else "FAIL"

    e1_ok = (reduction is not None and threshold is not None
             and reduction >= float(threshold))
    control_ok = after_c["real_facts"] >= before_c["real_facts"]
    state_ok = after_c["state_value_count"] >= before_c["state_value_count"]
    atomic_ok = after_c["atomic_points"] >= before_c["atomic_points"]

    # Threshold-free observation: "did the share fall AT ALL on the scored arm?"
    # needs no pre-registration to answer, and it is the finding that decides
    # whether any threshold could ever be met by the shipped machinery.
    observed = ("NO_REDUCTION" if (reduction is None or reduction <= 0)
                else "REDUCTION_OBSERVED")
    lines.append(f"e1 before_share={_fmt(before_c['share'])} "
                 f"after_share={_fmt(after_c['share'])} "
                 f"reduction={_fmt(reduction)} "
                 f"threshold={('n/a' if threshold is None else threshold)} "
                 f"observed={observed} verdict={verdict(e1_ok)}")
    lines.append(f"e1-control before_real_facts={before_c['real_facts']} "
                 f"after_real_facts={after_c['real_facts']} verdict={verdict(control_ok)}")
    lines.append(f"e2-state before={before_c['state_value_count']} "
                 f"after={after_c['state_value_count']} "
                 f"before_points={before_c['state_value_points']} "
                 f"after_points={after_c['state_value_points']} verdict={verdict(state_ok)}")
    lines.append(f"e2-atomic before={before_c['atomic_points']} "
                 f"after={after_c['atomic_points']} "
                 f"fused_after={after_c['fused_points']} verdict={verdict(atomic_ok)}")
    # §7's atomicity property, per fused Point, with SPLIT named as the reason it
    # survives: `SPLIT` is not in the O4 vocabulary, so no arm can split a fused
    # claim — the count is expected to be equal, and that equality is NOT a pass.
    for fused in after_c["fused_list"]:
        lines.append(f"e2-atomic fused content={fused[:90]!r} "
                     f"units={len(claim_units(fused))} "
                     f"cause=SPLIT-not-implemented")
    for name in ("after-vet-arbiter", "counterfactual-mint-skip"):
        c = results[name]["census"]
        r = (before_c["share"] - c["share"]) if (
            c["share"] is not None and before_c["share"] is not None) else None
        lines.append(f"counterfactual arm={name} reduction={_fmt(r)} "
                     f"real_facts={c['real_facts']} "
                     f"state_values={c['state_value_count']} "
                     f"note=NOT SCORED (mechanism absent from production)")

    # ── the discriminating control (E1-E2.md limit 6) ─────────────────────
    # The four arms make the 0.0 pp on the shipped/arbiter arms indistinguishable
    # from "every Object in this corpus is referenced, so Rule 2 always fires".
    # Strip only the references of the Objects the arbiter tries to discard and
    # run that arm again: if the discards now LAND, the 0.0 pp is a CORPUS
    # property, not a gate property (#9110 review P2). Printed rather than only
    # asserted in prose, so the structural gate checks it like every other claim.
    ctl = discriminating_control(corpus, embed, mint_meta["dropped_objects"],
                                 results["after-vet-arbiter"]["census"])
    lines.append(
        f"control variant=unreferenced-definite-descriptions "
        f"arm=after-vet-arbiter stripped_objects={ctl['stripped_objects']} "
        f"stripped_references={ctl['stripped_references']} "
        f"objects={ctl['objects']} the_prefix={ctl['the_prefix']} "
        f"share={_fmt(ctl['share'])} real_facts={ctl['real_facts']} "
        f"removed={ctl['removed']} "
        # WHICH Objects landed, not only how many: the count is reconciled against
        # this list by the structural gate, so the identities are auditable from
        # the log rather than only assertable from the probe (#9110 review P3).
        f"removed_names={json.dumps(ctl['removed_names'], ensure_ascii=False)} "
        f"note=the same Objects the referenced corpus keeps are REMOVED when "
        f"nothing references them, so the 0.0 pp is a corpus property (every "
        f"Object wired to its statement), not a property of S2.2")

    # SCORED means a bar was READ, not merely that an artifact existed. Keying
    # this on `prereg is not None` (the pre-fix shape) let a malformed artifact
    # report RED while every criterion said UNPREREGISTERED.
    scored = threshold is not None
    green = scored and e1_ok and control_ok and state_ok and atomic_ok
    exit_code = 0 if green else (1 if scored else 3)
    lines.append(f"result={'GREEN' if green else ('RED' if scored else 'UNPREREGISTERED')}")
    lines.append(f"exit={exit_code}")

    text = "\n".join(lines) + "\n"
    sys.stdout.write(text)
    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps({"sha": sha, "corpus_sha256": corpus_sha,
                        "prereg": prereg_note,
                        "arms": {k: v["census"] for k, v in results.items()},
                        "exit": exit_code}, indent=1, sort_keys=True) + "\n",
            encoding="utf-8")
    return exit_code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
