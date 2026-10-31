#!/usr/bin/env python3
"""The extractor-v4 capstone MEASUREMENT run (#9080 / epic #7949).

``capstone_run.py`` (same directory) is the instrument that ships with PR #9089. It
drives ``TortoiseSDK.capture_session`` — the real entry point — and then reads four
step witnesses off the RECEIPT that the receipt does not carry (#9116), so it
refuses at G5 for every input and its table is five ``BLOCKED`` rows with
``numerator=None`` (#9737: *"a COMPLETED run still yields 0/5 BLOCKED"*). It
measures nothing, by construction.

This probe measures. It is a SEPARATE file on purpose — ``capstone_run.py`` stays
working, and its refusal table stays the artifact that pins the refusal contract:

  * **G0** reads the frozen thresholds out of the pre-registration COMMIT with
    ``git show <sha>:<artifact>`` (never the worktree, so a threshold edited after
    the numbers are in cannot be served), and every bar this probe compares
    against is PARSED out of that record by ``parse_bars`` — none is typed here.
  * **G1** checks that the corpus in THIS checkout is the corpus the record froze
    (``tests.eval.write_path.corpus.compute_fixtures_hash`` against
    ``corpus.fixtures_hash``). A different revision is REFUSED, never scored — the
    record's own ``interface_note`` says so.
  * **THE RUN** is one pass over the seven pre-registered sessions on a FRESH
    disposable graph: ``capture_session(turns, session_id=<sid>)`` per session.
  * **THE OBSERVATION** reads each of the eight steps off the object that actually
    carries it — ``stats`` for six of them, the live graph for the commit, and an
    explicit UNOBSERVED-with-cause for the two the receipt has no key for. The
    two #9116 steps are DECIDED from the surfaces the run actually carries (the
    receipt's top-level keys and its stats block), so a seam fix makes them
    OBSERVED without editing this probe, and the count is derived.
  * **THE NUMBERS** are E3 (a census over the run's own graph), E4 (the committed
    operator receipt, read verbatim), E5 (a fresh re-score of the #7947 arms
    against this corpus's current gold), and E1/E1b/E2a/E2b (parsed out of the
    landed ``e1-e2.log``). Each row's ``evidence`` is the artifact that actually
    carries its number.

⚠️ WHAT THIS PROBE DOES NOT DO, stated so the table cannot be read as more than it
is. E1/E2's only artifacts are (a) ``e1-e2.log``, whose Object population is the
25-name owner-labelled audit set — below E1's frozen floor of 30 Objects — and
which ran on a DIFFERENT corpus revision than the pre-registration froze, and
(b) nothing else: no committed artifact carries E2a's 14-unit denominator or
E1b's ``grading.strict_survival_counts`` over the m2 lane. Those two rows are
therefore reported with their proxy numbers PRINTED and their verdict ``BLOCKED``
— a refused measurement is neither a pass nor a fail, and the record says the same
(``below_floor``: *"REFUSED, which is neither a pass nor a fail"*).

⚠️ THE RUN ARM. ``TORTOISE_VET`` and ``TORTOISE_CLASSIFY_LATER`` are forced ON for
the measured arm — IN PROCESS, with the previous values restored in a ``finally``
and the set values READ BACK from ``os.environ`` before the pass, the way
``e1_e2_probe.run_arm`` does it (they default OFF in the deployed config), so
S2.2/S2.3 are ON-PATH for this pass — the convention #9076's E1/E2 run used ("the
gate is measured with its flags forced ON"). ``TORTOISE_VALUE_GATE`` is pinned
UNSET for the same reason: it also switches S2.2 on, so an ambient value would
silently change the arm. The ``arm:`` line is that read-back, never a typed
literal — a run that cannot read a flag back as set REFUSES rather than printing a
claim nothing supports. **The seam has since LANDED (#9116), so this module no
longer reports them as unobservable-by-construction.** Pre-seam, ``capture_session``'s
receipt could not carry them and the failure was ONE LAYER UP from where it was
first attributed: ``extract_session_v2`` built the surfaces
(``result["classify_later"]`` at ``tortoise/extractor_v2.py:9052-9058`` and
``result["vet"]``) while the meta fold dropped them with
``"stats": out.get("stats") or {}``. The fold now calls
``_capture_stats_with_step_witness`` (``tortoise/sdk.py:14725``), which copies each
stage's own counts onto ``stats["step_witness"]`` and drops no candidate text — so
this module READS that seam (:func:`_witness_block`) instead of citing the gap. A
run measured at a stamp BEFORE the seam still reports S2.2/S2.3 unobserved, and
that is the run's own answer, not a workaround: reading a wrapper's state instead
is the instrumentation E4's row forbids.

⛔ THAT FOLD LINE IS TYPED HERE IN PROSE ONLY (which the bundle's cite sweep
re-resolves), never in a cause this probe PRINTS. Every ``sdk.py`` number a cause
emits is read off the tree at import as ``META_FOLD_CITE`` / ``LINK_SOURCE_CITE`` /
``TURN_WRITE_CITE``, because a line cite is the one number no reader can keep true
by hand: any commit that inserts a line above it moves it, and a stale one sends
#9116's fixer to the wrong line. A cite that will not resolve to exactly ONE line
makes the probe REFUSE rather than print it.

📌 CITE CONVENTION (this bundle). Every ``file:line`` is re-derived from the tree
at the head the bundle is stamped at, and is paired with the SYMBOL it names —
plus, for the ``sdk.py`` cites, a short quote of the line — so a number that
stops resolving is self-detecting. The SYMBOL is authoritative and the number is
a convenience; where a cite cannot be pinned without drift it is symbol-only on
purpose. ``tests/test_7949_capstone_measure.py`` pins the cite each cause string
and the composition line carry, so one cannot be dropped or silently re-worded;
the number's RESOLUTION is checked against the tree at the moment it is
re-derived (``grep -n`` at the stamped head), which is the only check that can be
made without pinning a line that ``main`` is free to move.

⛔ WHERE THE LOG GOES. NOT over ``capstone.log``. That file is the shipped REFUSAL
record, its add-commit predates the pre-registration, and
``tools/capstone_preregistration_check.py::_log_declares_no_measurement`` exempts a
log only while it carries no ``numerator``/``denominator`` AND its
``run.status != "COMPLETED"`` — so a SCORED table written into it reds that
required check permanently (the ordering rule compares the file's OLDEST
add-commit, which never moves). The default ``--out`` is therefore a fresh
``capstone-measured-<head8>.log``, whose own add-commit postdates the record.

Exit codes: 0 = the table was written and is structurally sound; 3 = a gate
refused (no table). A row this probe cannot measure is written ``BLOCKED`` with
its cause and the exit code is still 0 — the table is honest, the epic is not
closeable, and ``tools/capstone_verdict_check.py --require-green`` is the second
question.
"""
from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below (the repo's own D9
# shape — a module-level 3.11+-only import would fail first).
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"probes/capstone_measure.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python docs/evidence/7949-capstone/probes/capstone_measure.py`"
    )

import argparse
import json
import os
import re
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

#: ``docs/evidence/7949-capstone/probes/capstone_measure.py`` → repo root.
ROOT = Path(__file__).resolve().parents[4]

# Running this file as a script puts the PROBES directory on sys.path, so
# ``tests.eval.write_path.corpus`` (the corpus's own fixtures_hash + fixtures)
# would not import from the acceptance command's invocation. The repo root is what
# that module lives under, and `tortoise`/`tests` are both resolved from it.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _sdk_line(code: str) -> int:
    """The ONE line of ``tortoise/sdk.py`` carrying ``code`` — READ, never typed.

    A line cite is the one number in this bundle no reader can keep true by hand:
    any commit that inserts a line above the site moves it, and the meta fold has
    moved on this branch's own history and again on ``main``'s ``#9964``. Typed,
    the CAUSE re-stales SILENTLY — a shipped cause once named ``:13593`` for a fold
    that had moved to ``:13611``, and the only thing that caught it was
    ``tests/test_7949_capstone_measure.py``, which derives the line in the PIN and
    compares. Deriving it here too means the cause a reader (and #9116's fixer)
    actually reads cannot re-stale on a merge that touched nothing this bundle
    owns. The SYMBOL is authoritative and the line is a convenience (the bundle's
    own cite convention), so deriving it is the same answer Sphinx gives
    (``py:obj`` resolves to a symbol, not a line).

    ⛔ It REFUSES rather than printing a claim nothing supports: a cite that cannot
    be resolved to exactly ONE line is not a number this run may print (the
    ``arm:`` read-back rule, applied to a cite).
    """
    source = ROOT / "tortoise/sdk.py"
    if not source.exists():  # pragma: no cover - a checkout without the tree
        raise RuntimeError(
            f"cannot resolve a cite: {source} is not in this checkout, and a "
            "cause that names no real line is worse than no cite (#9116)")
    hits = [n for n, line in enumerate(
        source.read_text(encoding="utf-8").splitlines(), 1)
        if code in line]
    if len(hits) != 1:  # pragma: no cover - a moved/ambiguous cite is a refusal
        raise RuntimeError(
            f"`{code}` is on {len(hits)} lines of sdk.py {hits} — the cause names "
            "ONE line, so this run refuses rather than pick (#9116)")
    return hits[0]


#: The meta fold's cite, in the exact shape the causes below print it — derived
#: from the tree at run time (see ``_sdk_line``), never typed.
#:
#: The anchor is the POST-#9116 fold (``"stats":
#: _capture_stats_with_step_witness(out, len(extracted))``), because the merge
#: that brought ``_sdk_line`` in also brought the seam this PR lands: the
#: pre-seam ``"stats": out.get("stats")`` occurs on NO line of the merged tree,
#: so a cite derived from it would raise at import. Deriving it means the number
#: below stays right on every tree; typing the OLD code would have frozen the
#: probe to a fold the branch deletes.
META_FOLD_CITE = f"tortoise/sdk.py:{_sdk_line('"stats": _capture_stats_with_step_witness(')}"

#: The other two ``sdk.py`` sites this probe PRINTS (the emitted composition line
#: and ``E3_RULE``), derived from the tree for exactly the reason ``_sdk_line``
#: states above: every one of them moves on a merge that inserts lines above it,
#: and a stale number in a cause or in the log is a cite the next reader follows
#: to the wrong line. A number cannot re-stale if it is read on each run.
LINK_SOURCE_CITE = f"tortoise/sdk.py:{_sdk_line('proj.link_source_to_event(')}"
TURN_WRITE_CITE = f"tortoise/sdk.py:{_sdk_line('_TURN_WRITE_CYPHER = (')}"

EVIDENCE_DIR = Path("docs/evidence/7949-capstone")
PREREG_ARTIFACT = "docs/evidence/7949-capstone/preregistration.json"
E1E2_LOG = "docs/evidence/7949-capstone/e1-e2.log"
E4_RECEIPT = "tests/eval/write_path/receipts/w2b-operator-2026-10-19.json"
E5_RESCORE = "docs/evidence/7949-capstone/e5-rescore.json"
ARMS_DIR = "tests/eval/write_path/receipts/7947-arms-2026-10-18"
CORPUS_DIR = "tests/eval/write_path"

SCHEMA = "capstone-verdict-v1"
BEGIN = "=== CAPSTONE-VERDICT-BEGIN ==="
END = "=== CAPSTONE-VERDICT-END ==="
CRITERIA = ("E1", "E2", "E3", "E4", "E5")
STEPS = ("S0", "S1", "S2.1", "S2.2", "S2.3", "S3", "S5", "S6")

#: The node classes the pre-registration's E3 denominator names (``Point + Object
#: + Event``) — not a choice this probe makes.
DERIVED_LABELS = ("Point", "Object", "Event")

#: The relationship type that points FROM a derived node at its source. The
#: anchor is a property of that DERIVATION LINK, so the derived node is its
#: START: ``(Point)-[:extractedFrom]->(GraphEvent|Event)``.
ANCHOR_OUT = ("extractedFrom",)

#: The relationship type that points AT a derived node: ``(Source)-[:references]
#: ->(Event|Document)``, written by ``_EdgeHandlers.link_source_to_event``
#: (``tortoise/projection/edges.py:2832-2857``, ``MERGE (s)-[r:references]->(e)
#: ON CREATE SET r.sourceVersion = s.contentHash``) and reached from the capture
#: path at ``tortoise/sdk.py:15006`` (``proj.link_source_to_event(source_key,
#: event_id)`` — ``(Source)-[:references]->(sessionCaptured Event)``). The anchor
#: is on the same link, but the DERIVED node is its TARGET.
#: ⚠️ Counting this one in the ``extractedFrom`` direction counts SOURCES.
ANCHOR_IN = ("references",)

E3_RULE = (
    "a derived node (Point/Object/Event) carries a version anchor iff it is the "
    "SOURCE of at least one extractedFrom relationship whose sourceVersion is a "
    "non-empty string, OR the TARGET of at least one references relationship "
    "whose sourceVersion is a non-empty string. The two directions are NOT "
    "interchangeable: extractedFrom points FROM the derived node at its source "
    "((Point)-[:extractedFrom]->(GraphEvent|Event)), while references points AT "
    "the derived node ((Source)-[:references]->(Event|Document) — "
    "tortoise/projection/edges.py:2832-2857 link_source_to_event, reached from "
    f"the capture path at {LINK_SOURCE_CITE} "
    "(proj.link_source_to_event(source_key, event_id))). The anchor is a "
    "property of the "
    "DERIVATION LINK (tortoise/projection/edges.py:774-782 "
    "_anchor_on_create/_DERIVATION_ANCHOR_SET), and the references target labels "
    "are _DERIVATION_REFERENCES_LABELS (edges.py:747: Event, Document). A "
    "Source->Object link is identity/mention and carries none BY DESIGN (owner "
    "option A, #5199; #9736)"
)

#: The flags the measured arm forces ON — #9076's convention ("the gate is
#: measured with its flags forced ON"), and the reason ``e1_e2_probe.run_arm``
#: sets them the same way. Forcing them HERE is what makes the ``arm:`` line a
#: fact about the run instead of a claim about the operator's shell: without it
#: the probe printed an ON arm while measuring the default OFF one.
ARM_ON = ("TORTOISE_VET", "TORTOISE_CLASSIFY_LATER")

#: A flag the arm PINS OFF so the measured population is a function of THIS file,
#: not of whatever the operator's shell held. ``TORTOISE_VALUE_GATE`` also
#: switches S2.2 on (``extractor_v2._value_gate_enabled``), so an ambient value
#: would silently change the arm between two runs of the same probe — and
#: ``TORTOISE_S2_DECISION_SEAM`` (#5087) is a THIRD switch for S2.2 (it arms the
#: arbiter, so ``_vet_enabled`` is True), which would arm a network call and a
#: spend inside an arm this file declares OFF.
ARM_OFF = ("TORTOISE_VALUE_GATE", "TORTOISE_S2_DECISION_SEAM")

#: The three Point classes E3's frozen denominator mixes, each read off a
#: property its writer stamps (never a hand-kept list): ``is_episodic`` is set by
#: the turn writer (``_TURN_WRITE_CYPHER``, ``tortoise/sdk.py:2402`` — turn
#: rows carry ``is_episodic=true``, ``is_operator=false`` and are wired only by
#: ``CONTAINS``), ``is_operator`` by the operator writers, and an
#: ``extractedFrom`` edge by the extraction projection.
#:
#: ⚠️ ``is_operator`` Points are NOT in the derivation figure, and that is a
#: correction rather than a choice: ``create_operator`` (``tortoise/sdk.py``, the
#: IMPL/NAND/INPUT writer) never stamps ``extractedFrom``, so the class is
#: structurally incapable of the link and summing it into the denominator reads
#: as a shortfall nothing can close. The class is printed WITH its anchored count
#: so the claim is checkable rather than asserted.
POINT_COMPOSITION_CYPHER = (
    "MATCH (n:Point) "
    "OPTIONAL MATCH (n)-[xf:extractedFrom]->() "
    "OPTIONAL MATCH (n)<-[rf:references]-() "
    "WITH n, "
    "size([x IN collect(DISTINCT xf) WHERE x IS NOT NULL]) AS deriv, "
    "size([x IN collect(DISTINCT xf) WHERE x IS NOT NULL "
    "AND x.sourceVersion IS NOT NULL "
    "AND trim(toString(x.sourceVersion)) <> '']) AS deriv_anchored, "
    "size([x IN collect(DISTINCT rf) WHERE x IS NOT NULL "
    "AND x.sourceVersion IS NOT NULL "
    "AND trim(toString(x.sourceVersion)) <> '']) AS in_anchored "
    "RETURN count(n) AS points, "
    "sum(CASE WHEN coalesce(n.is_episodic, false) THEN 1 ELSE 0 END) "
    "AS turn_rows, "
    "sum(CASE WHEN NOT coalesce(n.is_episodic, false) "
    "AND coalesce(n.is_operator, false) THEN 1 ELSE 0 END) "
    "AS operator_points, "
    "sum(CASE WHEN NOT coalesce(n.is_episodic, false) "
    "AND NOT coalesce(n.is_operator, false) AND deriv > 0 "
    "THEN 1 ELSE 0 END) AS extraction_claims, "
    "sum(CASE WHEN NOT coalesce(n.is_episodic, false) "
    "AND NOT coalesce(n.is_operator, false) AND deriv = 0 "
    "THEN 1 ELSE 0 END) AS other_points, "
    # The anchored count PER CLASS. The per-class counts are RETURNED so the
    # printed line can STATE the composition instead of asserting it.
    #
    # ⛔ The derivation figure's denominator is the WRITER class (turn rows
    # excluded, operator Points excluded) — i.e. `extraction_claims +
    # other_points` — NOT `extraction_claims` alone. The narrower reading was
    # CIRCULAR: `extraction_claims` is DEFINED by `deriv > 0`, so a node that
    # LOST its `extractedFrom` link left the denominator and landed in `other`,
    # and the figure could not move (132/132 no matter what `other` held). The
    # writer class is the population that is SUPPOSED to derive, so the residual
    # is inside the denominator and a dropped link shows up as an unanchored
    # node. `other_points_anchored` is what makes the numerator complete.
    "sum(CASE WHEN NOT coalesce(n.is_episodic, false) "
    "AND NOT coalesce(n.is_operator, false) AND deriv > 0 "
    "AND (deriv_anchored > 0 OR in_anchored > 0) "
    "THEN 1 ELSE 0 END) AS extraction_claims_anchored, "
    "sum(CASE WHEN coalesce(n.is_episodic, false) "
    "AND (deriv_anchored > 0 OR in_anchored > 0) "
    "THEN 1 ELSE 0 END) AS turn_rows_anchored, "
    "sum(CASE WHEN NOT coalesce(n.is_episodic, false) "
    "AND coalesce(n.is_operator, false) "
    "AND (deriv_anchored > 0 OR in_anchored > 0) "
    "THEN 1 ELSE 0 END) AS operator_points_anchored, "
    "sum(CASE WHEN NOT coalesce(n.is_episodic, false) "
    "AND NOT coalesce(n.is_operator, false) AND deriv = 0 "
    "AND (deriv_anchored > 0 OR in_anchored > 0) "
    "THEN 1 ELSE 0 END) AS other_points_anchored"
)


# ── the measured arm (forced, read back, restored) ────────────────────────

@contextmanager
def measured_arm():
    """Force the measured arm ON, READ IT BACK, and restore it in a ``finally``.

    The read-back is the point. A probe that prints an arm it never set measures
    whatever the shell happened to hold, and the log then declares a pass that
    did not run — a FABRICATED measurement, not a disappointing one. So the
    values are re-read from ``os.environ`` AFTER setting and handed to
    :func:`arm_header`; a flag that does not read back as set REFUSES the run
    (fail closed) rather than printing a claim nothing supports.
    """
    keys = (*ARM_ON, *ARM_OFF)
    saved = {key: os.environ.get(key) for key in keys}
    try:
        for key in ARM_ON:
            os.environ[key] = "1"
        for key in ARM_OFF:
            os.environ.pop(key, None)
        observed = {key: os.environ.get(key) for key in keys}
        for key in ARM_ON:
            if observed[key] != "1":
                raise SystemExit(
                    f"capstone-measure: REFUSED — the measured arm sets "
                    f"{key}=1 but os.environ reads back {observed[key]!r}; a log "
                    f"that declares an arm it did not run is a fabricated "
                    f"measurement, so this pass writes no table")
        yield observed
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def arm_header(observed: dict[str, str | None]) -> str:
    """The ``arm:`` line, DERIVED from the read-back — never a typed literal."""
    parts = []
    for key in (*ARM_ON, *ARM_OFF):
        value = observed.get(key)
        parts.append(f"{key}={value if value not in (None, '') else '<unset>'}")
    return (" ".join(parts) + " (set/pinned IN-PROCESS for this pass and read "
            "back from os.environ after setting; the deployed config leaves "
            "both passes OFF)")


def arm_record(observed: dict[str, str | None]) -> dict:
    """The machine half of the arm: the SAME read-back the header prints."""
    record: dict[str, str | None] = {key: observed.get(key)
                                     for key in (*ARM_ON, *ARM_OFF)}
    record["source"] = "os.environ read-back after measured_arm() forced it"
    return record


# ── pure helpers (pinned by tests/test_7949_capstone_measure.py) ───────────

def _criteria(prereg: dict) -> dict[str, dict]:
    """``prereg["criteria"]`` keyed by id, refusing a shape that cannot be read."""
    crit = prereg.get("criteria")
    if not isinstance(crit, list):
        raise ValueError("preregistration carries no criteria list")
    out: dict[str, dict] = {}
    for entry in crit:
        if isinstance(entry, dict) and isinstance(entry.get("id"), str):
            out[entry["id"]] = entry
    missing = [c for c in ("E1", "E1b", "E2a", "E2b", "E3", "E4", "E5")
               if c not in out]
    if missing:
        raise ValueError(f"preregistration is missing criteria {missing}")
    return out


def _threshold(crit: dict[str, dict], key: str) -> str:
    entry = crit[key]
    text = entry.get("threshold")
    if not isinstance(text, str) or not text.strip():
        raise ValueError(f"{key} carries no threshold text: {text!r}")
    return text


def _float(pattern: str, text: str, what: str) -> float:
    match = re.search(pattern, text)
    if not match:
        raise ValueError(f"cannot parse {what} out of {text!r}")
    return float(match.group(1))


def _ints(pattern: str, text: str, what: str) -> tuple[int, int]:
    match = re.search(pattern, text)
    if not match:
        raise ValueError(f"cannot parse {what} out of {text!r}")
    return int(match.group(1)), int(match.group(2))


def parse_bars(prereg: dict) -> dict:
    """EVERY bar and floor, read out of the frozen record — never typed here.

    The record states its thresholds as prose in ``criteria[].threshold`` and its
    floors as integers in ``denominator_floors``. The prose is PARSED rather than
    transcribed: a bar typed into this file is a bar nobody froze, so a lane could
    move a threshold by editing the probe. An unparseable threshold refuses the
    run (ValueError → G0, exit 3) instead of falling back to a default.
    """
    crit = _criteria(prereg)
    floors = prereg.get("denominator_floors")
    if not isinstance(floors, dict):
        raise ValueError("preregistration's denominator_floors is not a mapping")

    def floor(key: str) -> int:
        value = floors.get(key)
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"denominator_floors[{key!r}] is not an integer: "
                             f"{value!r}")
        return value

    e1b_ok, e1b_total = _ints(r">=\s*(\d+)\s*of\s*(\d+)",
                              _threshold(crit, "E1b"), "E1b")
    e2a_ok, e2a_total = _ints(r">=\s*(\d+)\s*of\s*(\d+)",
                              _threshold(crit, "E2a"), "E2a")
    e4_ok, e4_total = _ints(r"=\s*(\d+)\s*of\s*(\d+)",
                            _threshold(crit, "E4"), "E4")
    e5_bars = [float(x) for x in
               re.findall(r">=\s*([0-9]+(?:\.[0-9]+)?)", _threshold(crit, "E5"))]
    if len(e5_bars) < 2:
        raise ValueError(f"cannot parse E5's two bars out of "
                         f"{_threshold(crit, 'E5')!r}")
    return {
        # after_share <= multiplier x before_share
        "E1": {"multiplier": _float(
            r"<=\s*([0-9]+(?:\.[0-9]+)?)\s*[×x*]\s*before_share",
            _threshold(crit, "E1"), "E1")},
        "E1b": {"required": e1b_ok, "of": e1b_total},
        "E2a": {"required": e2a_ok, "of": e2a_total},
        "E2b": {"max_fused_share": _float(r"<=\s*([0-9]+(?:\.[0-9]+)?)",
                                          _threshold(crit, "E2b"), "E2b")},
        "E3": {"required_share": _float(r"=\s*([0-9]+(?:\.[0-9]+)?)",
                                        _threshold(crit, "E3"), "E3")},
        "E4": {"required": e4_ok, "of": e4_total},
        "E5": {"ratio": e5_bars[0], "raw_absolute": e5_bars[1]},
        "floors": {
            "e1_objects": floor("e1_objects"),
            "e2b_surviving_units": floor("e2b_surviving_units"),
            "e3_derived_nodes": floor("e3_derived_nodes"),
            "e5_rate_min_sentences": floor("e5_rate_min_sentences"),
        },
    }


def outcome(*, measured: bool, below_floor: bool, passed: bool,
            bar: str, value: str) -> dict:
    """The ONE place a comparison becomes a verdict word.

    ``REFUSED`` is neither a pass nor a fail — the pre-registration says so in as
    many words (``below_floor``: *"REFUSED, which is neither a pass nor a fail"*),
    and it maps to the table's ``BLOCKED``, never to ``RED`` and never to
    ``GREEN``. Collapsing a refusal into a failure is how a floor stops being a
    floor; collapsing it into a pass is how a too-small sample becomes a verdict.
    """
    if not measured:
        verdict = "BLOCKED"
    elif below_floor:
        verdict = "REFUSED"
    else:
        verdict = "PASS" if passed else "FAIL"
    return {"outcome": verdict, "bar": bar, "value": value,
            "measured": measured, "below_floor": below_floor}


def e1_outcome(*, before_share: float | None, after_share: float | None,
               objects: int | None, bars: dict) -> dict:
    """E1: volume falls (``after <= multiplier x before``) over ALL Objects, floor 30.

    ⚠️ ``objects`` is the DENOMINATOR the criterion names ("**all** `:Object`
    nodes written by the run") — not a hand-picked population. A run under the
    frozen floor is REFUSED: with five Objects a single delete moves the share by
    20 points, which is what the floor exists to stop.
    """
    floor = bars["floors"]["e1_objects"]
    multiplier = bars["E1"]["multiplier"]
    bar = (f"after_share <= {multiplier} x before_share "
           f"(Objects floor {floor})")
    if before_share is None or after_share is None or objects is None:
        return outcome(measured=False, below_floor=False, passed=False,
                       bar=bar, value="unmeasured")
    bound = multiplier * before_share
    return outcome(measured=True, below_floor=objects < floor,
                   passed=after_share <= bound, bar=bar,
                   value=f"before={before_share:.6f} after={after_share:.6f} "
                         f"objects={objects} bound={bound:.6f}")


def e3_outcome(*, anchored: int, derived: int, bars: dict) -> dict:
    """E3: derived nodes carrying a version anchor, floor 30 derived nodes."""
    floor = bars["floors"]["e3_derived_nodes"]
    share = (anchored / derived) if derived else 0.0
    return outcome(measured=True, below_floor=derived < floor,
                   passed=share >= bars["E3"]["required_share"],
                   bar=f"share = {bars['E3']['required_share']} (zero "
                       f"unanchored), floor {floor} derived nodes",
                   value=f"anchored={anchored} derived={derived} "
                         f"share={share:.6f}")


def e4_outcome(*, edge_correct: int, planted: int, bars: dict) -> dict:
    """E4: planted operator edges wired AND persisted, over the frozen 15."""
    return outcome(measured=True, below_floor=False,
                   passed=(edge_correct >= bars["E4"]["required"]
                           and planted == bars["E4"]["of"]),
                   bar=f"{bars['E4']['required']} of {bars['E4']['of']}",
                   value=f"edge_correct={edge_correct} planted={planted}")


def e5_outcome(*, raw_loss: float, narrative_loss: float, sentences: int,
               bars: dict) -> dict:
    """E5: narrative/raw recovery ratio AND raw absolute recovery.

    ``recovery = 1 - loss``. The ratio ALONE is degenerate (two arms at 20% give
    1.0), which is why the record requires the raw absolute floor too — so a
    measurement can pass the ratio and still fail, and this function reports both
    halves rather than one verdict word with the other hidden.
    """
    floor = bars["floors"]["e5_rate_min_sentences"]
    raw_recovery = 1.0 - raw_loss
    narrative_recovery = 1.0 - narrative_loss
    ratio = (narrative_recovery / raw_recovery) if raw_recovery else float("inf")
    result = outcome(
        measured=True, below_floor=sentences < floor,
        passed=(ratio >= bars["E5"]["ratio"]
                and raw_recovery >= bars["E5"]["raw_absolute"]),
        bar=f"ratio >= {bars['E5']['ratio']} AND raw >= "
            f"{bars['E5']['raw_absolute']} (floor {floor} sentences)",
        value=f"ratio={ratio:.4f} raw={raw_recovery:.4f} "
              f"narrative={narrative_recovery:.4f} n={sentences}")
    result.update({
        "ratio": ratio, "raw_recovery": raw_recovery,
        "narrative_recovery": narrative_recovery,
        "ratio_ok": ratio >= bars["E5"]["ratio"],
        "raw_ok": raw_recovery >= bars["E5"]["raw_absolute"],
    })
    return result


def parse_e1e2_log(text: str) -> dict:
    """The arms of ``e1-e2.log`` as ``{arm: {metric: {key: value}}}``.

    Two line shapes: ``arm=<name> metric=<metric> k=v k=v …`` and the summary
    lines whose first token is the label (``e1 …``, ``e1-control …``,
    ``e2-state …``, ``e2-atomic …``), which land under the ``summary`` bucket. A
    ``k=v`` token is split on the FIRST ``=`` so a value containing ``=`` survives;
    a value containing SPACES (``matches=[["count", "4668"], …]``) is truncated at
    the first space — that field is a literal list this probe does not read.
    """
    arms: dict[str, dict[str, dict[str, str]]] = {}
    summary_labels = ("e1", "e1-control", "e2-state", "e2-atomic")
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        tokens = line.split(" ")
        if tokens[0].startswith("arm="):
            arm = tokens[0][len("arm="):]
            rest = tokens[1:]
            if not rest or not rest[0].startswith("metric="):
                continue
            metric = rest[0][len("metric="):]
            bucket = arms.setdefault(arm, {}).setdefault(metric, {})
            rest = rest[1:]
        elif tokens[0] in summary_labels:
            bucket = arms.setdefault("summary", {}).setdefault(tokens[0], {})
            rest = tokens[1:]
        else:
            continue
        for token in rest:
            key, sep, value = token.partition("=")
            if sep:
                bucket[key] = value
    return arms


def corpus_sha_of_log(text: str) -> str | None:
    match = re.search(r"\bcorpus_sha256=([0-9a-f]{64})\b", text)
    return match.group(1) if match else None


def line_value(text: str, key: str) -> str | None:
    """The first token of a ``key=value`` line (a line may carry more tokens)."""
    match = re.search(rf"^{re.escape(key)}=(\S+)", text, re.M)
    return match.group(1) if match else None


def normalise_corpus(value: str | None) -> str | None:
    if not value:
        return None
    return value[len("sha256:"):] if value.startswith("sha256:") else value


def verdict_word(result: dict) -> str:
    """``outcome()``'s comparison → the table's verdict vocabulary.

    PASS→GREEN, FAIL→RED, everything else (REFUSED / unmeasured)→BLOCKED. The
    table admits exactly four words (``tools/capstone_verdict_check.py``
    VERDICTS), and a refused measurement is NOT a red row: "the population was too
    small to score" and "the criterion was scored and failed" are different
    findings, and E1 is the case that makes the difference load-bearing.
    """
    return {"PASS": "GREEN", "FAIL": "RED"}.get(result["outcome"], "BLOCKED")


def build_rows(*, head: str, out_path: str, log_corpus_sha: str,
               e3: dict, e4: dict, e5: dict, e1: dict, e2: dict) -> list[dict]:
    """The five table rows — one per criterion, each with the artifact behind it.

    ``evidence`` is the path of the artifact that ACTUALLY CARRIES the number.
    ``capstone_run.py`` hard-coded ``EVIDENCE_DIR/capstone.log`` regardless of
    ``--out`` (the open P1); this function takes the path as an argument instead,
    so a run written elsewhere cites where it wrote.

    ``corpus_sha256`` is the corpus the cited artifact MEASURED. Where that is not
    the corpus THIS run froze, the row also carries ``artifact_corpus_sha256`` and
    ``tools/capstone_verdict_check.py``'s V7 requires that digest to appear in the
    artifact's own text — a declaration nothing supports is a finding.
    """
    def row(criterion: str, *, verdict: str, evidence: str, sha: str | None,
            numerator: int | None, denominator: int | None,
            corpus_sha256: str | None, blocked_by: str | None, note: str,
            artifact_corpus_sha256: str | None = None,
            sibling: str | None = None) -> dict:
        built = {
            "criterion": criterion, "verdict": verdict,
            "evidence_kind": "measurement", "evidence": evidence, "sha": sha,
            "numerator": numerator, "denominator": denominator,
            "corpus_sha256": corpus_sha256, "sibling": sibling,
            "blocked_by": blocked_by, "note": note,
        }
        if artifact_corpus_sha256 is not None:
            built["artifact_corpus_sha256"] = artifact_corpus_sha256
        return built

    return [
        row("E1", verdict=e1["verdict"], evidence=E1E2_LOG, sha=e1["sha"],
            numerator=None, denominator=None,
            corpus_sha256=e1["corpus_sha256"],
            artifact_corpus_sha256=e1["corpus_sha256"], sibling="E2",
            blocked_by="#5333", note=e1["note"]),
        row("E2", verdict=e2["verdict"], evidence=E1E2_LOG, sha=e2["sha"],
            numerator=None, denominator=None,
            corpus_sha256=e2["corpus_sha256"],
            artifact_corpus_sha256=e2["corpus_sha256"], sibling="E1",
            blocked_by="#9775", note=e2["note"]),
        row("E3", verdict=e3["verdict"], evidence=out_path, sha=head,
            numerator=e3["numerator"], denominator=e3["denominator"],
            corpus_sha256=log_corpus_sha, blocked_by="#9736", note=e3["note"]),
        row("E4", verdict=e4["verdict"], evidence=E4_RECEIPT, sha=e4["sha"],
            numerator=e4["numerator"], denominator=e4["denominator"],
            corpus_sha256=e4["corpus_sha256"],
            artifact_corpus_sha256=e4["corpus_sha256"], blocked_by="#8503",
            note=e4["note"]),
        row("E5", verdict=e5["verdict"], evidence=E5_RESCORE, sha=head,
            numerator=e5["numerator"], denominator=e5["denominator"],
            corpus_sha256=log_corpus_sha, blocked_by="#9738", note=e5["note"]),
    ]


# ── gates ─────────────────────────────────────────────────────────────────

def git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(cwd or ROOT),
                          capture_output=True, text=True, check=False)


def resolve_head() -> str:
    proc = git("rev-parse", "HEAD")
    sha = proc.stdout.strip()
    if proc.returncode != 0 or not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise SystemExit("capstone-measure: REFUSED — `git rev-parse HEAD` gave "
                         f"{sha!r}; a table whose rows carry no commit cannot be "
                         "measured")
    return sha


def read_preregistration(sha: str) -> dict:
    """G0 — the frozen record, read out of the COMMIT, never the worktree."""
    proc = git("show", f"{sha}:{PREREG_ARTIFACT}")
    if proc.returncode != 0:
        raise SystemExit(
            f"capstone-measure: REFUSED — commit {sha} does not carry "
            f"{PREREG_ARTIFACT} (git show exited {proc.returncode}); the run is "
            "fail-closed before any model call. A gate (G0) that cannot read the "
            "frozen thresholds is not evidence, and no threshold is supplied from "
            "outside it")
    try:
        record = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"capstone-measure: REFUSED — {PREREG_ARTIFACT} at {sha} "
                         f"is not JSON: {exc}") from exc
    if not isinstance(record, dict):
        raise SystemExit("capstone-measure: REFUSED — the pre-registration is "
                         f"a {type(record).__name__}, not a record")
    if record.get("schema") != "capstone-preregistration/v1":
        raise SystemExit(f"capstone-measure: REFUSED — the pre-registration's "
                         f"schema is {record.get('schema')!r}, expected "
                         "'capstone-preregistration/v1'")
    if "written_before_any_scored_run" not in record:
        raise SystemExit("capstone-measure: REFUSED — the pre-registration does "
                         "not state written_before_any_scored_run; a record that "
                         "cannot say when it was written cannot freeze a bar")
    return record


def check_corpus(prereg: dict) -> str:
    """G1 — is THIS checkout's corpus the corpus the record froze?

    The record pins a FIXTURE REVISION (``corpus.fixtures_hash`` over
    ``tests/eval/write_path``), not a turns file with a ``sha256`` — which is why
    ``capstone_run.py``'s G1 (one corpus file's sha) refuses the landed record.
    This is the same freeze, read the way the record wrote it.
    """
    from tests.eval.write_path.corpus import compute_fixtures_hash

    frozen = (prereg.get("corpus") or {}).get("fixtures_hash") or ""
    actual = compute_fixtures_hash()
    if not frozen:
        raise SystemExit("capstone-measure: REFUSED — the pre-registration freezes "
                         "no corpus.fixtures_hash, so no corpus can be checked "
                         "against it")
    if frozen != actual:
        raise SystemExit(
            f"capstone-measure: REFUSED — the pre-registration freezes {frozen} "
            f"but this checkout's corpus is {actual}. A run handed a DIFFERENT "
            "corpus must mismatch G1 and be refused, never scored (the record's "
            "own interface_note)")
    return actual


def backend_ready() -> tuple[bool, str]:
    """The two preconditions a real pass needs — checked before any write."""
    if not (os.environ.get("DEEPSEEK_API_KEY") or "").strip():
        return False, ("DEEPSEEK_API_KEY is not set — a keyless capture extracts "
                       "nothing (extraction_mode='empty'), so this would be a "
                       "measurement of the ABSENCE of a key (#3892)")
    if not (os.environ.get("TORTOISE_DB_URI") or "").strip():
        return False, ("TORTOISE_DB_URI is not set — the run needs a searchable "
                       "backend (S3 search and the S6 commit both read it); get "
                       "one with `uv run python tools/test_lane.py uri`")
    return True, "ok"


def graph_uri(base_uri: str, graph: str) -> str:
    """Address ``graph`` on the container ``base_uri`` points at.

    ``TORTOISE_DB_URI`` names a default graph; the run writes to a disposable one,
    so only the last path segment is replaced. A query string is preserved.
    """
    stem, sep, query = base_uri.partition("?")
    base = stem.rsplit("/", 1)[0]
    return f"{base}/{graph}" + (f"?{query}" if sep else "")


# ── the run ───────────────────────────────────────────────────────────────

def run_corpus(graph: str, sessions: list[str]) -> list[dict]:
    """ONE pass over the pre-registered corpus — the real entry point, 7 sessions."""
    from tests.eval.write_path.corpus import load_fixture
    from tortoise.sdk import TortoiseSDK

    sdk = TortoiseSDK(graph_name=graph)
    results = []
    for sid in sessions:
        fixture = load_fixture(sid)
        turns = fixture.get("conversation")
        if not isinstance(turns, list) or not turns:
            raise SystemExit(f"capstone-measure: REFUSED — fixture {sid} carries "
                             f"no `conversation` turns ({type(turns).__name__}); "
                             "S0 would have nothing to read")
        started = time.time()
        receipt = sdk.capture_session(turns, session_id=sid)
        stats = receipt.get("stats") or {}
        results.append({
            "session_id": sid,
            "turns": len(turns),
            "ok": bool(receipt.get("ok")),
            "extraction_mode": receipt.get("extraction_mode"),
            "extracted": receipt.get("extracted"),
            "errors": list(receipt.get("errors") or []),
            "stats": stats,
            "elapsed_s": round(time.time() - started, 1),
            # WHATEVER the probe holds for the two step witnesses #9116 names —
            # kept verbatim (never summarised to a constant) so `observe_steps`
            # can only report a step whose surface is ACTUALLY here. These are the
            # two places such a surface would land if the seam were fixed: the
            # extractor's own top-level keys (``result["vet"]`` /
            # ``result["classify_later"]``) and the stats block.
            "surfaces": {key: receipt.get(key) for key in _WITNESS_KEYS},
            "receipt_keys": sorted(str(key) for key in receipt),
            "stats_keys": sorted(str(key) for key in stats),
        })
    return results


#: The receipt's ``stats`` sub-key the SDK copies the per-stage blocks onto
#: (#9116 — ``_capture_step_witness`` in ``tortoise/sdk.py``). One name, so the
#: probe and the seam cannot spell it differently.
STEP_WITNESS_KEY = "step_witness"

#: The two stat keys a #9116 surface carries when its pass did WORK. Required for
#: ANY witness: the flag alone is not evidence (see :func:`_pass_ran`).
_WITNESS_STATS = ("s2", "union")

#: The two surfaces :func:`_witness_block` is asked about. Named here (not inline in
#: ``run_corpus``) so the per-surface cause table below can be held to it.
_WITNESS_KEYS = ("vet", "classify_later")

#: The flag-on/no-activity cause's CODE CITES, PER SURFACE — never one pass's
#: lines templated over both. The two passes are different code: a cause keyed on
#: the surface NAME while hard-wired to the VET lines (`vet_enabled`, the
#: `if vet_enabled and …` gates, `_run_vet_pass`) told a reader of the classify
#: block where the VET pass is gated, which is not where the classify pass is. The
#: classify pass has its own flag (`classify_later`), its own gates
#: (`if classify_later and …`, then `if items:` — an EMPTY ITEM SET idles it too)
#: and its own fail-open `except`; none of the vet cites describe it (#9823 r4).
#: A surface added to :func:`_witness_block` must add its cites here: a plain
#: ``_IDLE_CITES[key]`` lookup fails LOUDLY rather than borrowing another pass's
#: code.
_IDLE_CITES: dict[str, str] = {
    "vet": (
        "The extractor emits exactly this shape when the flag is ON and the list "
        "the VET pass would have worked on is empty: `vet_enabled` is computed "
        "BEFORE the `if not edus:` early return that emits {'enabled': "
        "vet_enabled, 's2': {}, 'union': {}} (tortoise/extractor_v2.py:8683, the "
        "flag; :8687, the early return; :8703, its vet surface), and each VET "
        "pass is gated on the LIST — `if vet_enabled and embed_list:` (:8771), "
        "`if vet_enabled and complete_list:` (:8893) — so "
        "a block that says enabled=true proves the FLAG, never that the pass "
        "ran. A FAILED vet pass leaves the same empty stats (`_run_vet_pass` "
        "returns {} on its fail-open path, :8562-8565)."
    ),
    "classify_later": (
        "The extractor emits exactly this shape when the flag is ON and the "
        "CLASSIFY pass had nothing to work on: `classify_later` is computed at "
        "tortoise/extractor_v2.py:8652 (and set False when its classifier cannot "
        "be constructed, :8677), the `if not edus:` early return emits "
        "{'enabled': classify_later, 's2': {}, 'union': {}, "
        "'restamp_overrides': 0, 'slot_rekeys': 0} (:8699-8702), and each "
        "classify pass is gated on the LIST and then on a NON-EMPTY item set — "
        "`if classify_later and embed_list:` (:8783) around `if items:` "
        "(:8786), `if classify_later and complete_list:` (:8908) around "
        "`if items:` (:8918) — so a block that says enabled=true proves the "
        "FLAG, never that the pass ran. A FAILED classify pass leaves the same "
        "empty stats (its fail-open `except` at :8799 / :8929)."
    ),
}


def _pass_ran(block: dict) -> bool | None:
    """Does this block SAY its pass ran? ``True`` / ``False`` / ``None`` (silent).

    ⛔ A surface EXISTING is not evidence that the pass RAN, and this is exactly
    where the two were confused. ``extract_session_v2`` emits ``result["vet"]``
    and ``result["classify_later"]`` UNCONDITIONALLY — on the flag-OFF arm and on
    the early-return path the block is ``{"enabled": False, "s2": {}, "union":
    {}}`` — so an acceptance test of "a non-empty dict"
    reports S2.2/S2.3 WITNESSED on a run measured with the flags OFF, printing
    ``enabled=false, s2={}`` beside the word WITNESSED.

    ⛔ AND THE FLAG ALONE IS NOT ACTIVITY, which is the sharper half and the one
    this rule got wrong first. ``enabled`` is the FLAG value, computed BEFORE the
    pass is reached: ``vet_enabled`` is set at ``extractor_v2.py:8683`` and
    ``edus = _edus_from_conversation(...)`` follows at ``:8686``, whose
    ``if not edus:`` early return (``:8687``) emits literally ``"vet": {"enabled":
    vet_enabled, "s2": {}, "union": {}}`` (``:8703``) — with the flag ON. The
    passes themselves are gated on the LIST, not the flag: ``if vet_enabled and
    embed_list:`` (``:8771``) and ``if vet_enabled and complete_list:``
    (``:8893``). So a session whose conversation produced no list emits
    ``enabled: True`` with empty counters and no pass ever ran; so does a pass
    that FAILED, because ``_run_vet_pass`` returns ``{}`` stats on its fail-open
    exception path (``extractor_v2.py:8562-8565``). ``enabled: true`` therefore
    proves the flag was read, never that the pass did anything.

    ⛔ The VET lines above are the VET pass's, not the rule's: the CLASSIFY pass
    is different code (``classify_later``, its own early-return
    block, its ``if classify_later and …`` gates around ``if items:``, its own
    fail-open ``except``), so the rule is stated pass-agnostically here and the
    CITES a reader gets are raised per surface from ``_IDLE_CITES`` — never one
    pass's lines templated over the other's name.

    The rule applied here, in this order:

    1. ``enabled is False`` → no witness, and non-empty stats cannot outvote the
       flag. A block that says OFF while carrying S2 counters would be a surface
       contradicting itself, so it fails CLOSED (never a witness).
    2. A non-empty ``s2`` or ``union`` → the pass DID work, and that is the
       positive evidence whatever carries it. This is the ONLY rule that can
       return ``True``: activity is required, not merely permitted.
    3. ``enabled is True`` with BOTH ``s2`` and ``union`` empty → the flag was on
       and nothing ran (an empty list, or a failed pass). Returned as ``False``:
       the block states its flag and evinces no pass. The caller keeps this
       DISTINCT from the flag-OFF refusal via ``block["enabled"]`` — it is "flag
       on, no pass activity", not the ``#9116`` absent-surface cause and not the
       OFF cause.
    4. No ``enabled`` field and no activity → silent (``None``), which is not a
       witness.
    """
    enabled = block.get("enabled")
    if enabled is False:
        return False
    if any(block.get(key) for key in _WITNESS_STATS):
        return True
    if enabled is True:
        # flag ON, zero activity — a distinct negative, never a witness
        return False
    return None


def _witness_block(runs: list[dict], key: str) -> tuple[dict | None, str, str]:
    """The first ``key`` surface a run CARRIES that SAYS its pass ran, and where.

    Looks where the surface would legitimately land — the extractor's top-level
    key on the receipt, the stats block, and (since #9116's seam landed) the
    ``stats["step_witness"]`` sub-key the SDK copies onto the receipt — and
    NOWHERE else: a witness read off a wrapper's state is the instrumentation
    E4's row forbids.

    Returns ``(block, where, refusal)``. ``block`` is non-``None`` only when a
    surface in that place states the pass ran (:func:`_pass_ran`); otherwise
    ``where`` still names the place a surface WAS found and ``refusal`` says why
    it is not a witness (``""`` when no surface was found at all).

    Precedence among the negatives: a positive witness anywhere outranks every
    refusal; an ``enabled: false`` refusal outranks a flag-on/no-activity one
    (it CONTRADICTS the arm the probe forced, which is the sharper finding); both
    outrank silence. The two refusal KINDS are kept apart on purpose — "the pass
    did not run because its flag was off", "the flag was on and the pass did no
    work" and "the surface was never delivered (``#9116``)" send a reader to
    three different places, and one message for all three is how #9116 was
    mis-blamed the first time.
    """
    refused: tuple[str, str] | None = None
    idle: tuple[str, str] | None = None
    silent: tuple[str, str] | None = None
    for run in runs:
        surfaces = run.get("surfaces") or {}
        candidates = (
            (f"receipt[{key!r}]", surfaces.get(key)),
            (f"stats[{key!r}]", (run.get("stats") or {}).get(key)),
            # #9116 seam: ``stats["step_witness"][key]`` — where the shipped
            # SDK now copies the stage's own block (`_capture_step_witness` in
            # tortoise/sdk.py, a sub-key of the ``stats`` key the receipt
            # already carried). Looked for LAST so a lane that still gets the
            # extractor's own top-level key is read from there.
            (f"stats['step_witness'][{key!r}]",
             ((run.get("stats") or {}).get(STEP_WITNESS_KEY) or {}).get(key)),
        )
        for where_prefix, block in candidates:
            if not isinstance(block, dict) or not block:
                continue
            where = f"{where_prefix} (session {run['session_id']})"
            ran = _pass_ran(block)
            if ran is True:
                return block, where, ""
            if ran is False and block.get("enabled") is True:
                # flag ON, no activity: the pass was gated out (or failed) with
                # its flag set — NOT a contradiction of the arm, and NOT #9116.
                # The cites are the SURFACE's own (``_IDLE_CITES``): the vet
                # block and the classify block are idled by different code.
                idle = idle or (
                    where,
                    f"the {key!r} surface IS present at {where} and its own "
                    f"`enabled` reads True, but it carries NO pass activity — "
                    f"neither {'/'.join(_WITNESS_STATS)} is non-empty "
                    f"({_summary(block)}). {_IDLE_CITES[key]} This is NOT the "
                    f"#9116 absent-surface cause: the surface is HERE and says "
                    f"the flag was set, and the step stays unobserved until it "
                    f"carries a non-empty {'/'.join(_WITNESS_STATS)} AS WELL")
            elif ran is False:
                refused = refused or (
                    where,
                    f"the {key!r} surface IS present at {where} but its own "
                    f"`enabled` reads False ({_summary(block)}) — a block that "
                    f"says the pass did NOT run is not evidence that it did")
            else:
                silent = silent or (
                    where,
                    f"a {key!r} surface IS present at {where} but carries "
                    f"neither `enabled: true` nor a non-empty "
                    f"{'/'.join(_WITNESS_STATS)} ({_summary(block)}), so it does "
                    f"not say the pass ran")
    if refused:
        return None, refused[0], refused[1]
    if idle:
        return None, idle[0], idle[1]
    if silent:
        return None, silent[0], silent[1]
    return None, "", ""


def _summary(block: dict) -> str:
    """A compact, honest rendering of a witness block (never a paraphrase)."""
    parts = []
    for key in ("enabled", "s2", "union", "restamp_overrides", "slot_rekeys"):
        if key in block:
            value = block[key]
            rendered = json.dumps(value, sort_keys=True, default=str)
            if len(rendered) > 160:
                rendered = rendered[:157] + "..."
            parts.append(f"{key}={rendered}")
    return ", ".join(parts) or json.dumps(block, sort_keys=True, default=str)[:200]


def anchor_census_cypher(label: str) -> str:
    """E3's per-label anchoring count — BOTH directions, one statement.

    A derived node is anchored when it is the START of an anchored
    ``extractedFrom`` link (``(Point)-[:extractedFrom]->(source)``) **or** the
    END of an anchored ``references`` link (``(Source)-[:references]->(Event)``).
    A single out-direction query counted SOURCES instead of Events and reported
    ``:Event 0/41 anchored`` beside ``references=7/7`` — two claims that cannot
    both be true, which is how the bug was caught against the shipped log.

    The two OPTIONAL MATCHes are COLLECTED separately and de-duplicated before
    the check: joining them row-wise would multiply each node by its edge count
    and change what ``anchored`` counts.
    """
    return (
        f"MATCH (n:{label}) "
        "OPTIONAL MATCH (n)-[out:" + "|".join(ANCHOR_OUT) + "]->() "
        "OPTIONAL MATCH (n)<-[inc:" + "|".join(ANCHOR_IN) + "]-() "
        "WITH n, "
        "size([x IN collect(DISTINCT out) WHERE x IS NOT NULL "
        "AND x.sourceVersion IS NOT NULL "
        "AND trim(toString(x.sourceVersion)) <> '']) AS out_anchors, "
        "size([x IN collect(DISTINCT inc) WHERE x IS NOT NULL "
        "AND x.sourceVersion IS NOT NULL "
        "AND trim(toString(x.sourceVersion)) <> '']) AS in_anchors "
        "RETURN count(n) AS nodes, "
        "sum(CASE WHEN out_anchors + in_anchors > 0 THEN 1 ELSE 0 END) "
        "AS anchored, "
        "sum(CASE WHEN out_anchors > 0 THEN 1 ELSE 0 END) "
        "AS anchored_out, "
        "sum(CASE WHEN in_anchors > 0 THEN 1 ELSE 0 END) "
        "AS anchored_in"
    )


def derivation_only(composition: dict | None) -> tuple[int, int, float | None]:
    """E3's derivation figure: ``anchored / expected-to-derive``.

    Returns ``(anchored, total, share)`` where ``total`` is the WRITER class —
    every Point that is neither a turn row nor an operator Point — i.e.
    ``extraction_claims + other_points``, and ``anchored`` is those same nodes
    carrying a version anchor (``extraction_claims_anchored +
    other_points_anchored``).

    ⛔ THE DENOMINATOR WAS WRONG TWICE, in opposite directions, and the second
    error was the more insidious because it LOOKED rigorous.

    *First it summed extraction claims + operator Points* and printed
    ``claim_anchored/claim_denominator`` — ``140/169 = 0.828402`` on the run that
    shipped that line, ``132/161 = 0.819876`` on the run that replaced it — a
    figure reading as "derivation coverage is still ~17-18% short". Nothing could
    ever close that remainder: ``create_operator`` (``tortoise/sdk.py``, the
    IMPL/NAND/INPUT writer) never writes an ``extractedFrom`` link. So operator
    Points are excluded, and turn rows with them (``_TURN_WRITE_CYPHER`` writes
    ``is_episodic=true`` and wires only ``CONTAINS``).

    *Then it took the denominator over ``extraction_claims`` ALONE, which was
    CIRCULAR.* ``extraction_claims`` is DEFINED by the very link the figure
    reports (``NOT is_episodic AND NOT is_operator AND deriv > 0``, see
    :data:`POINT_COMPOSITION_CYPHER`), so it could not do anything but restate
    ``extractedFrom=132/132`` and it was INVARIANT to a lost link: a node that
    dropped ``extractedFrom`` left the denominator and landed in ``other``, and
    the figure stayed ``1.000000``. Measured on the shipped run —
    ``derivation_only`` returned ``1.0`` for ``other_points`` of 0, 1, 9 and 40,
    and a hand-dropped link moved it from ``132/132`` to ``131/131``.

    So the denominator is the class the WRITERS stamp as "supposed to derive" —
    non-episodic, non-operator Points — and the residual is INSIDE it. ``other``
    is therefore not a footnote: it is the class where a LOST derivation link
    would land, and every unanchored node in it is now visible in the figure.
    What ``other`` cannot say on its own is whether a given node lost a link or
    was minted by a writer that never derives — that attribution is #9736's open
    question, and the line prints the composition so it can be argued from
    numbers rather than from the figure alone.
    """
    block = composition or {}
    claims = int(block.get("extraction_claims") or 0)
    other = int(block.get("other_points") or 0)
    total = claims + other
    anchored = (int(block.get("extraction_claims_anchored") or 0)
                + int(block.get("other_points_anchored") or 0))
    return anchored, total, ((anchored / total) if total else None)


def point_composition_line(composition: dict | None) -> str:
    """The per-class Point decomposition, DERIVED from the census — never typed."""
    block = composition or {}
    anchored, total, share = derivation_only(block)
    claims = int(block.get("extraction_claims") or 0)
    claims_anchored = int(block.get("extraction_claims_anchored") or 0)
    operators = int(block.get("operator_points") or 0)
    operators_anchored = int(block.get("operator_points_anchored") or 0)
    turns = int(block.get("turn_rows") or 0)
    turns_anchored = int(block.get("turn_rows_anchored") or 0)
    others = int(block.get("other_points") or 0)
    others_anchored = int(block.get("other_points_anchored") or 0)
    classes = (
        f"turn_rows={turns} (anchored {turns_anchored}) "
        f"extraction_claims={claims} (anchored {claims_anchored}) "
        f"operator_points={operators} (anchored {operators_anchored}) "
        f"other={others} (anchored {others_anchored}) "
        f"(total={int(block.get('points') or 0)} Point nodes)")
    figure = (f"{anchored}/{total}" +
              (f" = {share:.6f}" if share is not None else ""))
    return (
        f"e3 point composition: {classes} — each class read off the property its "
        f"WRITER stamps (is_episodic for turn rows, is_operator for operator "
        f"Points, an extractedFrom edge for extraction claims). 'other' = Points "
        f"with NO extractedFrom edge that are neither turn rows nor operator "
        f"Points ({others} of them, {others_anchored} anchored) — the class where "
        f"a LOST derivation link lands, so it is INSIDE the derivation figure's "
        f"denominator and an unanchored node in it is visible there. The "
        f"denominator is the class the writers stamp as supposed-to-derive "
        f"(non-episodic, non-operator Points = {claims} extraction claims + "
        f"{others} other = {total}); it is NOT defined by the extractedFrom link "
        f"itself, because a denominator filtered on the link cannot move when the "
        f"link is lost (measured on the superseded `8a8bbcba` run: the earlier "
        f"reading of this figure was 132/132 = 1.000000 while 9 Points sat in "
        f"'other' — and that run's log is retained in HISTORY, not in the tree: "
        f"superseding DELETES the TREE entry it replaces, never the history, so "
        f"`git show 6020cbd5f:docs/evidence/7949-capstone/capstone-measured-8a8bbcba.log` "
        f"yields it WHILE this branch's ref exists — and that ref is temporary. "
        f"After the squash-merge a branch-only commit is unresolvable BY DESIGN "
        f"(V11), and the route is the PR ref: `git fetch origin "
        f"refs/pull/9823/head`, then that same `git show <sha>:<path>`. This "
        f"bundle's README carries the figure too, so it does not depend on the "
        f"retrieval. What the deletion does cost is "
        f"citability as a row's `evidence`, which V4 resolves against the TREE: "
        f"the figure is provenance you can CHECK from the commit, not a file this "
        f"bundle can hand you). TWO classes are "
        f"STRUCTURALLY INCAPABLE of an extractedFrom link and are therefore NOT "
        f"in the denominator, with their anchored counts stated: turn rows carry "
        f"none BY CONSTRUCTION (_TURN_WRITE_CYPHER in _write_capture_turns, "
        f"{TURN_WRITE_CITE}, t.is_episodic=true, wired only by CONTAINS), and "
        f"operator Points are written by create_operator (tortoise/sdk.py — the "
        f"IMPL/NAND/INPUT operator writer), which never writes extractedFrom "
        f"(measured: {operators_anchored} of {operators} anchored). Whether an "
        f"unanchored node in 'other' LOST a link or was minted by a writer that "
        f"never derives is the attribution #9736 still owns; the figure counts "
        f"it, the line does not excuse it. derivation figure (anchored / "
        f"expected-to-derive): {figure}. The frozen 'all derived nodes' reading "
        f"above is unchanged")


def e3_row_note(*, composition: dict | None, e3_result: dict, graph: str,
                corpus_sha: str) -> str:
    """The E3 row's note. Every figure DERIVED; no live count in a historical claim.

    Two defects this function exists to make unrepresentable (#9823 review, round 5):

    (1) The superseded-reading clause used to splice THIS run's counts into a
    ratio about the EARLIER one — ``f"{claims_anchored}/{claims} = 1.000000"`` —
    with the quotient TYPED rather than computed. On the shipped run that read
    ``141/141 = 1.000000`` (coincidentally right, since every claim happened to be
    anchored) and on a run that lost one link it would have printed
    ``140/141 = 1.000000``, a false arithmetic identity inside the record.

    (2) Those counts are this census's, so the sentence asserted that the
    SUPERSEDED probe produced them; its own figure was ``132/132``. A claim the
    artifact cannot carry is exactly what this bundle refuses everywhere else.

    So the clause now states the circularity as a CONSTRUCTION — a denominator
    defined by the link it reports can only ever read 1.0 — with no number taken
    from the current run and no invented ratio. The numbers of the earlier run
    are provenance the bundle README carries, and they are CHECKABLE rather than
    exempt: superseding deletes the TREE entry, not the history, so the log is
    still retrievable with
    ``git show 6020cbd5f:docs/evidence/7949-capstone/capstone-measured-8a8bbcba.log``
    (what it cannot be is a row's ``evidence``, which V4 resolves against the
    tree); the note does not restate them.

    ⚠️ That recipe is REF-QUALIFIED, not durable, and it must say so.
    ``6020cbd5f`` is BRANCH-ONLY — ``git for-each-ref --contains 6020cbd5f``
    names this branch and its remote, never ``origin/main`` — and this repo
    squash-merges, so once the branch ref is deleted the command above returns
    ``fatal: bad object``. That is not an accident: V11 in
    ``tools/capstone_verdict_check.py`` records it ("a squash-merged branch sha
    NEVER reaches main"). The route that survives the merge is the PR ref —
    ``git fetch origin refs/pull/9823/head``, whose history carries the commit,
    then the same ``git show <sha>:<path>``. The README states the figures as
    well, so a reader who cannot retrieve the log still has the number; what
    this bundle cannot do is promise the retrieval outlives the branch.
    """
    block = composition or {}
    # NOTE the names: this is the WRITER class (claims + other), NOT the claims
    # alone — a `claim_den` here would be the circular denominator again.
    deriv_anchored, deriv_expected, derivation_share = derivation_only(block)
    derivation = (f"{deriv_anchored}/{deriv_expected}" +
                  (f" = {derivation_share:.6f}"
                   if derivation_share is not None else ""))
    return (
        f"{e3_result['value']}; bar {e3_result['bar']}; measured on THIS "
        f"run's own graph ({graph}, fixtures_hash {corpus_sha}). The "
        f"Source->Object identity link carries no sourceVersion BY "
        f"DESIGN (owner option A, #5199), so ':Object mention links' can "
        f"never anchor — and the frozen denominator counts them anyway, "
        f"so the = 1.000 bar is unreachable as registered (#9736: either "
        f"the metric is re-registered or the model is reopened; a lane "
        f"cannot pick). COMPOSITION of the {block.get('points', 0)} "
        f"Point nodes the denominator also counts: "
        f"{block.get('turn_rows', 0)} mechanical turn rows + "
        f"{block.get('extraction_claims', 0)} extraction claims "
        f"({block.get('extraction_claims_anchored', 0)} anchored) "
        f"+ {block.get('operator_points', 0)} operator Points "
        f"({block.get('operator_points_anchored', 0)} anchored) "
        f"+ {block.get('other_points', 0)} other "
        f"({block.get('other_points_anchored', 0)} anchored), each "
        f"class read off the property its writer stamps. TWO of those "
        f"classes cannot carry a derivation link AT ALL and are named "
        f"rather than folded into the figure: the turn rows carry none BY "
        f"CONSTRUCTION (_TURN_WRITE_CYPHER in _write_capture_turns, "
        f"{TURN_WRITE_CITE}, t.is_episodic=true, wired only by "
        f"CONTAINS), and the operator Points are written by "
        f"create_operator (tortoise/sdk.py — the IMPL/NAND/INPUT "
        f"operator writer), which never writes extractedFrom. So "
        f"counting them under 'all derived nodes' is defensible but "
        f"they can never anchor and are not a provenance shortfall, and "
        f"a denominator summing them would report a shortfall nothing "
        f"could close. The DERIVATION figure is anchored / "
        f"expected-to-derive over the WRITER class (non-episodic, "
        f"non-operator Points = the extraction claims PLUS the 'other' "
        f"Points, which are the class where a LOST derivation link lands "
        f"— Points with no extractedFrom that are neither turn rows nor "
        f"operator Points): {derivation}. It is computed over that "
        f"class and NOT over the claims alone, because a denominator "
        f"defined by the extractedFrom link itself cannot move when that "
        f"link is lost: the earlier reading divided by the extraction "
        f"claims ALONE — the class that link defines — so it read "
        f"nothing but 1.000000 BY CONSTRUCTION, with the 'other' Points "
        f"sitting outside it. "
        f"Whether an unanchored 'other' Point lost a link or "
        f"was minted by a writer that never derives is the attribution "
        f"#9736 still owns. #9736 must choose between "
        f"re-registering the metric and reopening the model, and this "
        f"row reports BOTH readings so neither choice is made on a "
        f"denominator whose composition was hidden")


def _stage_calls(stats: dict, stage: str) -> int:
    """LLM calls rolled up under ``stats.llm.by_stage[stage]`` (any provider/model)."""
    by_stage = (stats.get("llm") or {}).get("by_stage") or {}
    stage_map = by_stage.get(stage) or {}
    total = 0
    if isinstance(stage_map, dict):
        for provider in stage_map.values():
            if not isinstance(provider, dict):
                continue
            for model in provider.values():
                if not isinstance(model, dict):
                    continue
                calls = model.get("calls")
                if isinstance(calls, int) and not isinstance(calls, bool):
                    total += calls
    return total


def _stat_total(stats_list: list[dict], key: str) -> int:
    return sum(int(s.get(key) or 0) for s in stats_list)


def _seam_cause(runs: list[dict], stats_keys: list[str], stats_key_total: int,
                receipt_keys: list[str], key: str) -> str:
    """Why a step with a KNOWN landed seam still has no witness (#9116).

    Three states, and they send a reader to three different places, so the line
    reports which one this run is in rather than one message for all three:

      * the receipt carries **no** ``stats['step_witness']`` at all — the seam
        did not run (extraction never reached ``_extract_session_v2``, or the
        capture was a replay/keyless lane). Measured, not assumed: the receipt's
        own key inventory is printed beside it.
      * the receipt carries the seam but **not this block** — the seam is
        present and incomplete, which is a defect in ``_capture_step_witness``.
      * the third possibility — the block is here and says the pass did not do
        work — never reaches this function; ``_witness_block`` returns a
        REFUSAL for it, and that message is the pass's own.
    """
    with_seam = [run["session_id"] for run in runs
                 if isinstance((run.get("stats") or {}).get(STEP_WITNESS_KEY),
                               dict)]
    measured = (f"Measured on this run: inspected {stats_key_total} stats "
                f"key(s) across {len(runs)} receipt(s) "
                f"[{', '.join(stats_keys) or 'none'}] and "
                f"{len(receipt_keys)} receipt top-level key(s) "
                f"[{', '.join(receipt_keys) or 'none'}]")
    if not with_seam:
        return (
            f"{measured}; no receipt carries stats[{STEP_WITNESS_KEY!r}], so the "
            f"#9116 seam that copies result[{key!r}] onto the receipt ("
            f"`_capture_step_witness`, called at the meta fold "
            f"{META_FOLD_CITE} in _extract_session_v2) did not reach this "
            f"capture. This is an "
            f"INSTRUMENT gap, NOT the pipeline skipping the step; reading a "
            f"wrapper instead is the instrumentation E4's row forbids")
    return (
        f"{measured}; stats[{STEP_WITNESS_KEY!r}] IS present on "
        f"{len(with_seam)} of {len(runs)} receipt(s), but it carries no "
        f"{key!r} block — the seam is present and INCOMPLETE, which is a defect "
        f"in _capture_step_witness (tortoise/sdk.py), not in the pass")


def observe_steps(runs: list[dict], census_data: dict) -> tuple[dict, dict]:
    """The EIGHT steps, each read off the object that carries it.

    Returns ``(observed, unobserved)``: ``{step: witness}`` and ``{step: cause}``.
    Nothing is inferred — a step whose surface is absent is listed with the reason
    and the file:line, never with a borrowed witness.
    """
    stats = [run["stats"] for run in runs]
    edus = _stat_total(stats, "edus")
    chunks = _stat_total(stats, "chunks")
    s1 = sum(_stage_calls(s, "s1") for s in stats)
    s2 = sum(_stage_calls(s, "s2") for s in stats)
    queries = _stat_total(stats, "search_queries")
    # `stats` is built one-per-run from `runs`, so `strict=True` states the
    # invariant instead of leaving a short zip to pass silently.
    degraded = [run["session_id"] for run, s in zip(runs, stats, strict=True)
                if s.get("search_degraded")]
    endpoints = _stat_total(stats, "operator_endpoints_minted")

    observed: dict[str, str] = {}
    unobserved: dict[str, str] = {}

    # What the probe HELD, counted — never typed. The two #9116 steps are decided
    # from these surfaces, so the count is the measurement and the cause line
    # reports it instead of quoting a remembered number.
    stats_keys = sorted({key for run in runs
                         for key in (run.get("stats_keys")
                                     or (run.get("stats") or {}).keys())})
    receipt_keys = sorted({key for run in runs
                           for key in (run.get("receipt_keys") or ())})
    stats_key_total = sum(len(run.get("stats") or {}) for run in runs)

    if edus and chunks:
        observed["S0"] = (f"the reader segmented the corpus: stats.edus={edus} "
                          f"stats.chunks={chunks} over {len(runs)} sessions")
    else:
        unobserved["S0"] = (f"the reader segmented nothing: stats.edus={edus}, "
                            f"stats.chunks={chunks} across {len(runs)} sessions")

    if s1:
        observed["S1"] = (f"the first LLM stage ran: stats.llm.by_stage.s1 "
                          f"calls={s1}")
    else:
        unobserved["S1"] = ("stats.llm.by_stage carries no 's1' roll-up — the "
                            "narration stage issued no call")

    if s2:
        observed["S2.1"] = (f"the S2 map ran: stats.llm.by_stage.s2 calls={s2}; "
                            f"entities={_stat_total(stats, 'entities')} "
                            f"events={_stat_total(stats, 'events')} "
                            f"points={_stat_total(stats, 'points')}")
    else:
        unobserved["S2.1"] = ("stats.llm.by_stage carries no 's2' roll-up — the "
                              "S2 map issued no call")

    vet_block, vet_where, vet_refusal = _witness_block(runs, "vet")
    if vet_block is not None:
        observed["S2.2"] = (
            f"the VET pass surfaced its own evidence: {vet_where} "
            f"— {_summary(vet_block)}")
    elif vet_refusal:
        # PRESENT but it does not say the pass ran. Two distinct negatives land
        # here and the refusal names which: `enabled: false` (contradicts the arm)
        # and `enabled: true` with no activity (the pass was gated out). Neither
        # is the #9116 absent-surface cause below.
        unobserved["S2.2"] = (
            f"UNOBSERVED — {vet_refusal}. The arm forced TORTOISE_VET=1 "
            f"in-process (read back from os.environ; the `arm:` line above is "
            f"that read-back), and a block that does not show the pass DID "
            f"something is never scored as its witness")
    else:
        unobserved["S2.2"] = (
            f"UNOBSERVED — the arm forced TORTOISE_VET=1 in-process and read it "
            f"back from os.environ (the `arm:` line above is that read-back), "
            f"so the pass was ENABLED for this run — and this receipt carries no "
            f"surface that says whether it then DID any work. "
            f"{_seam_cause(runs, stats_keys, stats_key_total, receipt_keys, "vet")}")

    classify_block, classify_where, classify_refusal = _witness_block(
        runs, "classify_later")
    if classify_block is not None:
        observed["S2.3"] = (
            f"the S2.3 classify pass surfaced its own evidence: "
            f"{classify_where} — {_summary(classify_block)}")
    elif classify_refusal:
        unobserved["S2.3"] = (
            f"UNOBSERVED — {classify_refusal}. The arm forced "
            f"TORTOISE_CLASSIFY_LATER=1 in-process (read back from os.environ), "
            f"and a block that does not show the pass DID something is never "
            f"scored as its witness")
    else:
        unobserved["S2.3"] = (
            f"UNOBSERVED — the arm forced TORTOISE_CLASSIFY_LATER=1 in-process "
            f"and read it back from os.environ (the `arm:` line above is that "
            f"read-back), so the pass was ENABLED for this run — and this "
            f"receipt carries no surface that says whether it then DID any work. "
            f"{_seam_cause(runs, stats_keys, stats_key_total, receipt_keys, 'classify_later')}")

    if queries and not degraded:
        observed["S3"] = (f"the graph was searched on the real backend: "
                          f"stats.search_queries={queries} > 0, "
                          f"stats.search_degraded=false on every session")
    else:
        unobserved["S3"] = (f"search did not run on the real backend "
                            f"(queries={queries}, degraded={degraded or 'none'}) — "
                            "a degraded search is not S3")

    if census_data.get("derived_total"):
        per_label = ", ".join(
            f"{label} {block['nodes']} ({block['anchored']} anchored)"
            for label, block in census_data["per_label"].items())
        operators = ", ".join(f"{name}={count}" for name, count in
                              sorted(census_data["operator_relationships"].items()))
        observed["S5"] = (
            f"the graph write landed: {census_data['derived_total']} derived "
            f"node(s) written by the run [{per_label}]; "
            f"stats.operator_endpoints_minted={endpoints}; operator-shaped "
            f"relationships written [{operators or 'none'}]")
    else:
        unobserved["S5"] = ("the run's own graph carries no derived node "
                            "(Point/Object/Event) — nothing was written")

    if census_data.get("points") and \
            census_data.get("event_stamped_points") is not None:
        observed["S6"] = (
            f"the commit persisted: {census_data['points']} :Point node(s) in the "
            f"run's graph, {census_data['event_stamped_points']} of them carrying "
            f"the commit's eventId provenance stamp; "
            f"{census_data['graph_events']} :GraphEvent(s)")
    else:
        unobserved["S6"] = (f"the commit cannot be witnessed: points="
                            f"{census_data.get('points')}, event-stamped="
                            f"{census_data.get('event_stamped_points')}")

    # The step count is DERIVED, so it cannot be a constant: every step is either
    # witnessed or carries its own cause, and the two sets may not overlap or
    # leave a hole. A step that vanished from both is exactly how "6 of 8" became
    # a value that survived a fix to its own cause.
    uncovered = [step for step in STEPS
                 if step not in observed and step not in unobserved]
    if uncovered:
        raise SystemExit(f"capstone-measure: REFUSED — step(s) {uncovered} are "
                         "neither witnessed nor given a cause; a table that "
                         "silently drops a step cannot report a count")
    overlap = sorted(set(observed) & set(unobserved))
    if overlap:
        raise SystemExit(f"capstone-measure: REFUSED — step(s) {overlap} are "
                         "both witnessed and unwitnessed; the count would be "
                         "ambiguous")

    return observed, unobserved


# ── the census ────────────────────────────────────────────────────────────

def census(uri: str) -> dict:
    """E3's census + the S5/S6 witnesses, over the RUN'S OWN graph.

    Every node in a freshly-minted graph was written by this run, which is what
    makes "derived nodes written by the run" a count rather than a filter on a
    timestamp the writer does not stamp.
    """
    from tortoise.projection import FalkorProjection

    projection = FalkorProjection.from_uri(uri)
    label_counts = {str(name): int(count) for name, count in projection.g.query(
        "MATCH (n) UNWIND labels(n) AS l RETURN l, count(*) ORDER BY l"
    ).result_set}

    per_label: dict[str, dict[str, int]] = {}
    for label in DERIVED_LABELS:
        # Per-label queries rather than one grouped query: `labels(n)[0]` would
        # group a multi-labelled node under the wrong key, and the denominator is
        # exactly the thing this census may not get subtly wrong.
        #
        # TWO DIRECTIONS, because the anchor sits on ONE link and the derived
        # node is on opposite ends of it: `(Point)-[:extractedFrom]->(source)`
        # makes the derived node the START, while
        # `(Source)-[:references]->(Event)` makes it the END. Counting both in
        # the out-direction counted SOURCES and reported `:Event 0/41` beside
        # `references=7/7` — two claims that cannot both be true.
        rows = projection.g.query(anchor_census_cypher(label)).result_set
        nodes = int(rows[0][0] or 0) if rows else 0
        anchored = int(rows[0][1] or 0) if rows else 0
        per_label[label] = {
            "nodes": nodes,
            "anchored": anchored,
            "anchored_out": int(rows[0][2] or 0) if rows else 0,
            "anchored_in": int(rows[0][3] or 0) if rows else 0,
        }

    # E3's Point denominator mixes three node classes; the split is read off the
    # properties each WRITER stamps (is_episodic for turn rows, is_operator for
    # operator Points, an extractedFrom edge for extraction claims) so the note
    # can disclose it instead of implying that every unanchored Point is an
    # extraction claim that lost its provenance.
    composition_rows = projection.g.query(POINT_COMPOSITION_CYPHER).result_set
    composition = {
        key: int(composition_rows[0][index] or 0)
        for index, key in enumerate(
            ("points", "turn_rows", "operator_points", "extraction_claims",
             "other_points", "extraction_claims_anchored",
             "turn_rows_anchored", "operator_points_anchored",
             "other_points_anchored"))
    } if composition_rows else {
        "points": 0, "turn_rows": 0, "operator_points": 0,
        "extraction_claims": 0, "other_points": 0,
        "extraction_claims_anchored": 0, "turn_rows_anchored": 0,
        "operator_points_anchored": 0, "other_points_anchored": 0}

    relationship_counts: dict[str, int] = {}
    relationship_anchored: dict[str, int] = {}
    for name, count, anchored in projection.g.query(
            "MATCH ()-[r]->() RETURN type(r) AS t, count(*) AS n, "
            "count(r.sourceVersion) AS anchored ORDER BY t").result_set:
        relationship_counts[str(name)] = int(count)
        relationship_anchored[str(name)] = int(anchored)

    event_stamped = projection.g.query(
        "MATCH (n:Point) WHERE n.eventId IS NOT NULL RETURN count(*)").result_set

    object_names = [str(row[0]) for row in projection.g.query(
        "MATCH (n:Object) RETURN n.name").result_set if row and row[0]]

    return {
        "uri": uri,
        "label_counts": label_counts,
        "per_label": per_label,
        "point_composition": composition,
        "derived_total": sum(block["nodes"] for block in per_label.values()),
        "anchored_total": sum(block["anchored"] for block in per_label.values()),
        "points": int(label_counts.get("Point", 0)),
        "graph_events": int(label_counts.get("GraphEvent", 0)),
        "event_stamped_points": (int(event_stamped[0][0])
                                 if event_stamped else None),
        "relationship_counts": relationship_counts,
        "relationship_anchored": relationship_anchored,
        "operator_relationships": {
            name: count for name, count in relationship_counts.items()
            if name in ("IMPL", "NAND", "SUPPORTS", "MITIGATES", "NEGATE",
                        "SUPERSEDE", "INPUT")},
        "object_names": object_names,
    }


# ── E4 / E5 / E1+E2 ───────────────────────────────────────────────────────

def load_e4(repo: Path = ROOT) -> dict:
    """E4's number, read VERBATIM out of the committed operator receipt."""
    path = repo / E4_RECEIPT
    if not path.is_file():
        raise SystemExit(f"capstone-measure: REFUSED — E4's receipt {E4_RECEIPT} "
                         "does not exist; a row with no artifact is an inference, "
                         "not evidence")
    record = json.loads(path.read_text())
    audit = record.get("operator_audit")
    if not isinstance(audit, dict):
        raise SystemExit(f"capstone-measure: REFUSED — {E4_RECEIPT} carries no "
                         "operator_audit block")
    for key in ("planted", "edge_correct", "content_ok", "operators_total"):
        value = audit.get(key)
        if not isinstance(value, int) or isinstance(value, bool):
            raise SystemExit(f"capstone-measure: REFUSED — {E4_RECEIPT} "
                             f"operator_audit[{key!r}] is not an integer count: "
                             f"{value!r}")
    return {
        "planted": audit["planted"],
        "edge_correct": audit["edge_correct"],
        "content_ok": audit["content_ok"],
        "operators_total": audit["operators_total"],
        "operators_provenanced": audit.get("operators_provenanced"),
        "run_id": record.get("run_id"),
        "date": record.get("date"),
        "commit": record.get("commit"),
        "corpus_hash": record.get("corpus_hash"),
        "judge_pin": record.get("judge_pin"),
        "verdict": record.get("verdict"),
        "failure_origin": record.get("failure_origin"),
    }


def score_e5(sessions: list[str], out_path: Path) -> dict:
    """Re-score the #7947 arms over the corpus's CURRENT gold; return the numbers.

    The re-score is what makes E5 a measurement of the CORPUS THIS RUN FROZE (the
    gold is part of ``fixtures_hash``) rather than a citation of a 2026-10-18
    summary. The arm RECEIPTS are the older half — they cannot be re-extracted
    here — and that is disclosed in the row's note, including the ``partial=True``
    receipt the re-score itself warns about.
    """
    cmd = [sys.executable, "-m", "tests.eval.write_path.score_qualifiers",
           "--sessions", *sessions, "--arms-dir", str(ROOT / ARMS_DIR),
           "--out", str(ROOT / out_path)]
    proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                          check=False)
    if proc.returncode != 0:
        raise SystemExit("capstone-measure: REFUSED — the E5 re-score exited "
                         f"{proc.returncode}:\n{proc.stderr[-2000:]}")
    record = json.loads((ROOT / out_path).read_text())
    aggregate = record.get("aggregate") or {}
    raw = aggregate.get("raw") or {}
    narrative = aggregate.get("narrative") or {}
    for key, block in (("raw", raw), ("narrative", narrative)):
        for field in ("loss", "loss_rate", "n"):
            value = block.get(field)
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise SystemExit("capstone-measure: REFUSED — the E5 re-score's "
                                 f"aggregate.{key}.{field} is not a number: "
                                 f"{value!r}")
    recall: dict[str, dict[str, int]] = {}
    for item in record.get("precision_recall") or []:
        arm = item.get("arm")
        if arm in ("raw", "narrative"):
            bucket = recall.setdefault(arm, {"recalled": 0, "planted": 0})
            bucket["recalled"] += int(item.get("recalled") or 0)
            bucket["planted"] += int(item.get("planted_units") or 0)
    partial: list[str] = []
    for sid, arms in (record.get("per_session") or {}).items():
        for arm, block in (arms or {}).items():
            flags = (block or {}).get("receipt_flags") or {}
            if any(flags.get(key) for key in
                   ("partial", "truncated", "s2_error", "s1_error",
                    "s1_truncated")):
                partial.append(f"{sid}:{arm} " + " ".join(
                    f"{key}={flags.get(key)}" for key in
                    ("partial", "truncated", "s2_error", "s1_error",
                     "s1_truncated")))
    warning = [line.strip() for line in proc.stdout.splitlines()
               if "INCOMPLETE RECEIPTS" in line or "partial=" in line]
    return {
        "raw_loss": float(raw["loss_rate"]),
        "raw_loss_count": int(raw["loss"]),
        "raw_n": int(raw["n"]),
        "narrative_loss": float(narrative["loss_rate"]),
        "narrative_loss_count": int(narrative["loss"]),
        "narrative_n": int(narrative["n"]),
        "recall": recall,
        "partial_receipts": partial,
        "warning": warning,
        "artifact": str(out_path),
        "arms_dir": ARMS_DIR,
    }


def measure_e1_e2(repo: Path = ROOT) -> dict:
    """E1/E1b/E2a/E2b, parsed out of the landed ``e1-e2.log``."""
    path = repo / E1E2_LOG
    if not path.is_file():
        raise SystemExit(f"capstone-measure: REFUSED — {E1E2_LOG} does not exist")
    text = path.read_text()
    arms = parse_e1e2_log(text)
    before = (arms.get("before") or {}).get("objects") or {}
    after = (arms.get("after-vet-shipped") or {}).get("objects") or {}
    if not before.get("share") or not after.get("share"):
        raise SystemExit(f"capstone-measure: REFUSED — {E1E2_LOG} carries no "
                         "objects/share line for both arms; E1 cannot be parsed")
    summary = arms.get("summary") or {}
    scored = arms.get("after-vet-shipped") or {}
    real_facts = scored.get("real_facts") or {}
    state = scored.get("state_value_points") or {}
    atomic = scored.get("atomic_points") or {}
    return {
        "sha": line_value(text, "sha"),
        "before_share": float(before["share"]),
        "after_share": float(after["share"]),
        "objects_total": int(float(before.get("total") or 0)),
        "objects_numerator": int(float(before.get("numerator") or 0)),
        "real_facts": int(float(real_facts.get("count") or 0)),
        "state_values": int(float(state.get("values") or 0)),
        "state_points": int(float(state.get("points") or 0)),
        "atomic_points": int(float(atomic.get("atomic") or 0)),
        "fused": int(float(atomic.get("fused") or 0)),
        "e1_summary": summary.get("e1") or {},
        "e1_control": summary.get("e1-control") or {},
        "e2_state": summary.get("e2-state") or {},
        "e2_atomic": summary.get("e2-atomic") or {},
        "corpus": (re.search(r"\bcorpus=(\S+)", text) or [None, None])[1]
                  if re.search(r"\bcorpus=(\S+)", text) else None,
        "corpus_sha256": corpus_sha_of_log(text),
        "declared_prereg": line_value(text, "prereg"),
        "declared_result": line_value(text, "result"),
    }


# ── log rendering ─────────────────────────────────────────────────────────

def render_log(*, head: str, prereg_sha: str, bars: dict, corpus_sha: str,
               graph: str, runs: list[dict], observed: dict, unobserved: dict,
               census_data: dict, e1: dict, e1_aux: dict, e1_out: dict,
               e1b_out: dict, e2a_out: dict, e2b_out: dict, e3: dict,
               e3_out: dict, e4: dict, e4_out: dict, e5: dict, e5_out: dict,
               rows: list[dict], out_path: str,
               arm: dict | None = None) -> str:
    """The human log with the machine table at the bottom (one artifact, one read)."""
    lines: list[str] = []
    add = lines.append

    add("# extractor-v4 capstone MEASUREMENT — S0 -> S6 on the pre-registered "
        "corpus (#9080)")
    add(f"# generated: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}")
    add("# probe: docs/evidence/7949-capstone/probes/capstone_measure.py")
    add(f"# head: {head}")
    add(f"# pre-registration: {prereg_sha} (read from the COMMIT with `git show`)")
    add(f"# corpus: {CORPUS_DIR} fixtures_hash={corpus_sha} ({len(runs)} sessions) "
        "— G1 compared it with the frozen pin")
    add(f"# graph: {graph} (fresh, disposable; every node in it was written by "
        "this run)")
    add(f"# arm: {arm_header(arm or {})}")
    add("")
    add("## the run — one pass over the pre-registered corpus")
    for run in runs:
        stats = run["stats"]
        add(f"session {run['session_id']} ok={run['ok']} "
            f"extraction_mode={run['extraction_mode']} "
            f"extracted={run['extracted']} errors={run['errors']} "
            f"turns={run['turns']} edus={stats.get('edus')} "
            f"chunks={stats.get('chunks')} entities={stats.get('entities')} "
            f"events={stats.get('events')} points={stats.get('points')} "
            f"operators={stats.get('operators')} "
            f"search_queries={stats.get('search_queries')} "
            f"search_degraded={stats.get('search_degraded')} "
            f"operator_endpoints_minted={stats.get('operator_endpoints_minted')} "
            f"elapsed_s={stats.get('elapsed_s')}")
    add("")
    add("## the eight steps — witnessed, or unobserved with its cause")
    for step in STEPS:
        if step in observed:
            add(f"step {step} WITNESSED {observed[step]}")
        else:
            add(f"step {step} UNOBSERVED {unobserved.get(step, 'no witness')}")
    add(f"steps witnessed: "
        f"{len([s for s in STEPS if s in observed])}/{len(STEPS)}"
        + (f" (unwitnessed: {', '.join(s for s in STEPS if s not in observed)})"
           if len(observed) < len(STEPS) else ""))
    add("")
    add("## E3 census — derived nodes and their version anchor")
    add(f"e3 rule: {E3_RULE}")
    for label in DERIVED_LABELS:
        block = census_data["per_label"].get(label) or {"nodes": 0, "anchored": 0}
        add(f"e3 label={label} nodes={block['nodes']} "
            f"anchored={block['anchored']} "
            f"unanchored={block['nodes'] - block['anchored']} "
            f"anchored_out={block.get('anchored_out', 0)} "
            f"anchored_in={block.get('anchored_in', 0)}")
    add(f"e3 numerator={e3['numerator']} denominator={e3['denominator']} "
        f"below_floor={'REFUSED' if e3['below_floor'] else 'no'} "
        f"outcome={e3['outcome']} bar={e3['bar']}")
    add("e3 relationship anchors: " + ", ".join(
        f"{name}={census_data['relationship_anchored'].get(name, 0)}/{count}"
        for name, count in sorted(census_data["relationship_counts"].items())))
    composition = census_data.get("point_composition") or {}
    add(point_composition_line(composition))
    add(f"e3 note: Source->Object identity/mention links are unanchored BY DESIGN "
        f"(owner option A, #5199; #9736) — aboutObject="
        f"{census_data['relationship_counts'].get('aboutObject', 0)} edge(s), "
        f"{census_data['relationship_anchored'].get('aboutObject', 0)} anchored")
    objects_in_graph = len(census_data["object_names"])
    floor_objects = bars["floors"]["e1_objects"]
    if objects_in_graph < floor_objects:
        floor_note = (f"AND {objects_in_graph} Objects is below the frozen floor "
                      f"of {floor_objects}, so this share could not decide the "
                      "criterion even with a before arm")
    else:
        floor_note = (f"{objects_in_graph} Objects CLEARS the frozen floor of "
                      f"{floor_objects}, but one arm cannot decide "
                      "`after <= 0.5 x before` — the before arm is the missing "
                      "half, and two independent captures would confound the "
                      "gate with LLM non-determinism")
    add(f"e1-aux objects_in_graph={objects_in_graph} "
        f"bare_definite={e1_aux['bare_definite']} share={e1_aux['share']} "
        f"note=AFTER arm only — this run has no before arm, so E1 is NOT "
        f"scoreable from it; {floor_note}")
    add("")
    add("## E1 / E1b / E2a / E2b — parsed out of the landed e1-e2.log")
    add(f"e1 parsed: before_share={e1['before_share']:.6f} "
        f"after_share={e1['after_share']:.6f} "
        f"objects={e1['objects_numerator']}/{e1['objects_total']} "
        f"sha={e1['sha']} corpus={e1['corpus']} "
        f"corpus_sha256={e1['corpus_sha256']} "
        f"declared prereg={e1['declared_prereg']} "
        f"declared result={e1['declared_result']}")
    add(f"e1 outcome={e1_out['outcome']} {e1_out['value']} bar={e1_out['bar']}")
    add("e1 note: the artifact's denominator is the 25-name owner-labelled audit "
        "population, NOT 'all :Object nodes written by the run', and its "
        "corpus_sha256 is not the frozen fixtures_hash — so the arithmetic "
        "(0.600000 vs a 0.300000 bound) would FAIL the bar, but the record's own "
        f"floor ({bars['floors']['e1_objects']} Objects) makes it REFUSED: "
        "neither a pass nor a fail")
    add(f"e1b outcome={e1b_out['outcome']} {e1b_out['value']} bar={e1b_out['bar']}")
    add(f"e1b note: the artifact reports real_facts={e1['real_facts']} on both "
        "arms; no committed artifact carries grading.strict_survival_counts over "
        "the m2 lane, so this is a no-loss CONTROL, not the frozen metric")
    add(f"e2a outcome={e2a_out['outcome']} {e2a_out['value']} "
        f"bar={e2a_out['bar']}")
    add(f"e2a note: the artifact carries state_value_points values="
        f"{e1['state_values']} on both arms (a no-loss control); the frozen "
        "denominator is the 14 value-bearing PLANTED UNITS, which no committed "
        "artifact counts")
    add(f"e2b outcome={e2b_out['outcome']} {e2b_out['value']} "
        f"bar={e2b_out['bar']}")
    add(f"e2b note: fused={e1['fused']} of atomic={e1['atomic_points']} Points on "
        "the scored arm — the artifact's atomicity control, whose denominator is "
        "atomic POINTS rather than surviving planted units")
    add("")
    add("## E4 — the committed operator receipt, read verbatim")
    add(f"e4 receipt={E4_RECEIPT} run_id={e4['run_id']} date={e4['date']} "
        f"commit={e4['commit']} corpus_hash={e4['corpus_hash']} "
        f"judge_pin={e4['judge_pin']} verdict={e4['verdict']} "
        f"failure_origin={e4['failure_origin']}")
    add(f"e4 operator_audit planted={e4['planted']} "
        f"edge_correct={e4['edge_correct']} content_ok={e4['content_ok']} "
        f"operators_total={e4['operators_total']} "
        f"operators_provenanced={e4['operators_provenanced']}")
    add(f"e4 outcome={e4_out['outcome']} {e4_out['value']} bar={e4_out['bar']}")
    add("e4 provenance: the FILE postdates the pre-registration but the "
        f"MEASUREMENT does not — its own date={e4['date']} and its "
        f"corpus_hash={e4['corpus_hash']} are not the frozen corpus "
        f"({corpus_sha}). This row is therefore scored as a NON-GREEN measurement "
        "on ITS OWN corpus revision, and the gate requires that digest to appear "
        "in the artifact itself (V7)")
    add("")
    add("## E5 — a fresh re-score of the #7947 arms against this corpus's gold")
    add(f"e5 arms_dir={e5['arms_dir']} artifact={e5['artifact']}")
    add(f"e5 raw loss={e5['raw_loss_count']}/{e5['raw_n']} "
        f"loss_rate={e5['raw_loss']:.6f} recovery={e5_out['raw_recovery']:.6f} "
        f"recall={e5['recall'].get('raw', {}).get('recalled')}/"
        f"{e5['recall'].get('raw', {}).get('planted')}")
    add(f"e5 narrative loss={e5['narrative_loss_count']}/{e5['narrative_n']} "
        f"loss_rate={e5['narrative_loss']:.6f} "
        f"recovery={e5_out['narrative_recovery']:.6f} "
        f"recall={e5['recall'].get('narrative', {}).get('recalled')}/"
        f"{e5['recall'].get('narrative', {}).get('planted')}")
    add(f"e5 ratio={e5_out['ratio']:.4f} (bar {bars['E5']['ratio']}, "
        f"{'OK' if e5_out['ratio_ok'] else 'FAIL'}) "
        f"raw_absolute={e5_out['raw_recovery']:.4f} "
        f"(bar {bars['E5']['raw_absolute']}, "
        f"{'OK' if e5_out['raw_ok'] else 'FAIL'}) sentences={e5['raw_n']} "
        f"(floor {bars['floors']['e5_rate_min_sentences']})")
    add(f"e5 outcome={e5_out['outcome']} bar={e5_out['bar']}")
    for line in e5["warning"]:
        add(f"e5 warning: {line}")
    if e5["partial_receipts"]:
        add("e5 incomplete receipts (disclosed, not silently averaged): " +
            " | ".join(e5["partial_receipts"]))
    add("")
    add("## verdict table")
    add("criterion | verdict | measured | evidence | blocked_by")
    for row in rows:
        measured = ("unmeasured" if row["numerator"] is None
                    else f"{row['numerator']}/{row['denominator']}")
        add(f"{row['criterion']} | {row['verdict']} | {measured} | "
            f"{row['evidence']} | {row['blocked_by']}")
    add("")
    add("## why each non-green row is not green")
    for row in rows:
        add(f"row {row['criterion']} note: {row['note']}")
    add("")
    record = {
        "schema": SCHEMA,
        "issue": 9080,
        "epic": 7949,
        "head": head,
        "prereg": {
            "sha": prereg_sha,
            "status": "OK",
            "artifact": PREREG_ARTIFACT,
            "read_from": f"git show {prereg_sha}:{PREREG_ARTIFACT}",
        },
        "corpus": {
            "path": CORPUS_DIR,
            "sha256": corpus_sha,
            "fixtures_hash": corpus_sha,
            "sessions": len(runs),
            "session_ids": [run["session_id"] for run in runs],
        },
        "run": {
            "status": "COMPLETED",
            "kind": "capstone-measure",
            "probe": "docs/evidence/7949-capstone/probes/capstone_measure.py",
            "graph": graph,
            "arm": arm_record(arm or {}),
            "steps_observed": [step for step in STEPS if step in observed],
            "steps_unobserved": {step: unobserved.get(step) for step in STEPS
                                 if step not in observed},
            "step_witnesses": observed,
            "sessions": [{key: run[key] for key in
                          ("session_id", "ok", "extraction_mode", "extracted",
                           "errors", "elapsed_s")} for run in runs],
            "e3_census": {
                "rule": E3_RULE,
                "per_label": census_data["per_label"],
                "label_counts": census_data["label_counts"],
                "point_composition": census_data.get("point_composition"),
                "relationship_anchored": census_data["relationship_anchored"],
                "relationship_counts": census_data["relationship_counts"],
            },
            "e4_receipt": {
                "numerator": e4["edge_correct"],
                "denominator": e4["planted"],
                "planted": e4["planted"],
                "edge_correct": e4["edge_correct"],
                "content_ok": e4["content_ok"],
                "operators_total": e4["operators_total"],
                "measured_commit": e4["commit"],
                "source": E4_RECEIPT,
            },
            "e5_rescore": {
                "raw_loss_rate": e5["raw_loss"],
                "narrative_loss_rate": e5["narrative_loss"],
                "raw_n": e5["raw_n"],
                "ratio": round(e5_out["ratio"], 6),
                "raw_recovery": round(e5_out["raw_recovery"], 6),
                "partial_receipts": e5["partial_receipts"],
                "source": e5["artifact"],
            },
            "e1_e2_artifact": {
                "before_share": e1["before_share"],
                "after_share": e1["after_share"],
                "objects": e1["objects_total"],
                "corpus_sha256": e1["corpus_sha256"],
                "declared_prereg": e1["declared_prereg"],
                "declared_result": e1["declared_result"],
            },
        },
        "rows": rows,
    }
    add(BEGIN)
    add(json.dumps(record, indent=2))
    add(END)
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="The extractor-v4 capstone MEASUREMENT run (#9080).")
    parser.add_argument("--prereg", default=None,
                        help="the commit that CARRIES the frozen "
                             "preregistration.json (default: HEAD)")
    parser.add_argument("--corpus", default=CORPUS_DIR,
                        help=f"the pre-registered corpus directory (default and "
                             f"ONLY accepted value: {CORPUS_DIR})")
    parser.add_argument("--out", default=None,
                        help="where to write the log (default: "
                             "docs/evidence/7949-capstone/"
                             "capstone-measured-<head8>.log — deliberately NOT "
                             "capstone.log: that file is the shipped REFUSAL "
                             "record, its add-commit predates the "
                             "pre-registration, and a scored table written into "
                             "it reds tools/capstone_preregistration_check.py's "
                             "ordering rule permanently)")
    parser.add_argument("--graph", default=None,
                        help="the disposable graph name (default: derived from "
                             "the head and the clock)")
    parser.add_argument("--e5-out", default=E5_RESCORE,
                        help="where the E5 re-score artifact is written (cited "
                             f"by E5's row; default {E5_RESCORE})")
    return parser


def _is_bare_definite(name: str) -> bool:
    """The repo's own predicate (``tortoise.definite_description_backfill``)."""
    from tortoise.definite_description_backfill import (
        is_bare_definite_description,
    )
    return bool(is_bare_definite_description(name))


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.corpus != CORPUS_DIR:
        raise SystemExit(
            f"capstone-measure: REFUSED — --corpus {args.corpus!r} is not the "
            f"corpus the pre-registration froze ({CORPUS_DIR}). A run handed a "
            "different corpus must be refused, never scored")

    head = resolve_head()
    prereg_sha = args.prereg or head
    prereg = read_preregistration(prereg_sha)
    try:
        bars = parse_bars(prereg)
    except ValueError as exc:
        raise SystemExit(f"capstone-measure: REFUSED — {exc}") from exc
    corpus_sha = check_corpus(prereg)

    ready, why = backend_ready()
    if not ready:
        raise SystemExit(f"capstone-measure: REFUSED — {why}")

    from tests.eval.write_path.corpus import session_ids

    sessions = session_ids()
    frozen_sessions = (prereg.get("corpus") or {}).get("sessions")
    if isinstance(frozen_sessions, int) and not isinstance(frozen_sessions, bool) \
            and frozen_sessions and len(sessions) != frozen_sessions:
        raise SystemExit(
            f"capstone-measure: REFUSED — the pre-registration freezes "
            f"{frozen_sessions} sessions, this corpus has {len(sessions)} — a "
            "different corpus is refused, never scored")

    graph = args.graph or f"capstone_measure_{head[:8]}_{int(time.time())}"
    out_path = args.out or str(EVIDENCE_DIR /
                               f"capstone-measured-{head[:8]}.log")

    print(f"capstone-measure: G0 pre-registration {prereg_sha} READ "
          f"(schema {prereg.get('schema')})")
    print(f"capstone-measure: G1 corpus {corpus_sha} MATCHES the frozen pin")
    print(f"capstone-measure: graph {graph} (fresh, disposable)")
    print(f"capstone-measure: running {len(sessions)} sessions through "
          "capture_session ...", flush=True)

    # The measured arm is FORCED here and read back — a printed arm that the
    # shell did not actually hold measures the OFF population under an ON label
    # (`e1_e2_probe.run_arm` forces its flags the same way).
    with measured_arm() as arm:
        print(f"capstone-measure: arm {arm_header(arm)}", flush=True)
        runs = run_corpus(graph, sessions)
    failed = [run["session_id"] for run in runs if not run["ok"]]
    census_data = census(graph_uri(os.environ["TORTOISE_DB_URI"], graph))
    observed, unobserved = observe_steps(runs, census_data)

    e1 = measure_e1_e2()
    bare = sum(1 for name in census_data["object_names"]
               if _is_bare_definite(name))
    e1_aux = {"bare_definite": bare,
              "share": (bare / len(census_data["object_names"])
                        if census_data["object_names"] else None)}

    # ── E1 / E1b / E2a / E2b (E1/E2 are the table's rows) ────────────────
    e1_result = e1_outcome(before_share=e1["before_share"],
                           after_share=e1["after_share"],
                           objects=e1["objects_total"], bars=bars)
    e1b_result = outcome(measured=True, below_floor=False,
                         passed=e1["real_facts"] >= bars["E1b"]["required"],
                         bar=f"{bars['E1b']['required']} of {bars['E1b']['of']}",
                         value=f"real_facts={e1['real_facts']}")
    e2a_result = outcome(measured=False, below_floor=False, passed=False,
                         bar=f"{bars['E2a']['required']} of {bars['E2a']['of']}",
                         value=f"values={e1['state_values']} (the frozen 14-unit "
                               "denominator is not carried by any committed "
                               "artifact)")
    e2b_floor = bars["floors"]["e2b_surviving_units"]
    e2b_share = ((e1["fused"] / e1["atomic_points"])
                 if e1["atomic_points"] else 1.0)
    e2b_result = outcome(measured=True,
                         below_floor=e1["atomic_points"] < e2b_floor,
                         passed=e2b_share <= bars["E2b"]["max_fused_share"],
                         bar=f"fused share <= {bars['E2b']['max_fused_share']} "
                             f"(floor {e2b_floor} surviving units)",
                         value=f"fused={e1['fused']}/{e1['atomic_points']} = "
                               f"{e2b_share:.6f}")

    e1_row = {
        "verdict": verdict_word(e1_result),
        "sha": e1["sha"],
        "corpus_sha256": e1["corpus_sha256"],
        "note": (f"the only E1 artifact is {E1E2_LOG}: {e1_result['value']} vs "
                 f"bar {e1_result['bar']} — after_share is NOT <= 0.5 x "
                 f"before_share, so the arithmetic would FAIL — but the row is "
                 f"NOT RED, because the record's own floor makes it REFUSED: the "
                 f"artifact's denominator is {e1['objects_total']} Objects (under "
                 f"the frozen floor of {bars['floors']['e1_objects']}) and is the "
                 f"25-name owner-labelled audit population rather than 'all "
                 f":Object nodes written by the run'. Its corpus "
                 f"({e1['corpus_sha256']}) is not the frozen corpus "
                 f"({corpus_sha}), and the artifact declares "
                 f"prereg={e1['declared_prereg']} "
                 f"result={e1['declared_result']}. No before/after pair on the "
                 f"pre-registered corpus exists, and the mechanism that must move "
                 f"the share is an owner ruling on removal/relocation (#5333) — so "
                 f"no scored E1 measurement exists to be red"),
    }
    e2_row = {
        "verdict": verdict_word(e2a_result),
        "sha": e1["sha"],
        "corpus_sha256": e1["corpus_sha256"],
        "note": (f"E2a: {e2a_result['outcome']} — {e2a_result['value']} vs bar "
                 f"{e2a_result['bar']}. E2b: {e2b_result['outcome']} — "
                 f"{e2b_result['value']} vs bar {e2b_result['bar']} (a proxy: the "
                 f"artifact counts atomic POINTS, not surviving planted units). "
                 f"The E2 row is BLOCKED because the composite criterion needs "
                 f"BOTH halves and E2a's frozen 14-value-bearing-unit denominator "
                 f"is carried by no committed artifact; the artifact's no-loss "
                 f"control (values {e1['state_values']} before and after, "
                 f"real_facts {e1['real_facts']}) is consistent with E2a but does "
                 f"not compute it. Same corpus caveat as E1: "
                 f"{e1['corpus_sha256']} is not {corpus_sha}, and #9775 records "
                 f"that the state-value carve-out is absent on one render path"),
    }

    # ── E3 ────────────────────────────────────────────────────────────────
    e3_result = e3_outcome(anchored=census_data["anchored_total"],
                           derived=census_data["derived_total"], bars=bars)
    composition = census_data.get("point_composition") or {}
    e3 = {
        "verdict": verdict_word(e3_result),
        "numerator": census_data["anchored_total"],
        "denominator": census_data["derived_total"],
        "below_floor": e3_result["below_floor"],
        "outcome": e3_result["outcome"],
        "bar": e3_result["bar"],
        "note": e3_row_note(composition=composition, e3_result=e3_result,
                            graph=graph, corpus_sha=corpus_sha),
    }

    # ── E4 ────────────────────────────────────────────────────────────────
    receipt = load_e4()
    e4_result = e4_outcome(edge_correct=receipt["edge_correct"],
                           planted=receipt["planted"], bars=bars)
    e4 = {
        "verdict": verdict_word(e4_result),
        "numerator": receipt["edge_correct"],
        "denominator": receipt["planted"],
        "sha": receipt["commit"],
        "corpus_sha256": receipt["corpus_hash"],
        "note": (f"{e4_result['value']}; bar {e4_result['bar']}; content_ok="
                 f"{receipt['content_ok']} operators_total="
                 f"{receipt['operators_total']} on the SAME receipt. Its own "
                 f"date={receipt['date']}, commit={receipt['commit']} and "
                 f"corpus_hash={receipt['corpus_hash']} differ from the frozen "
                 f"corpus ({corpus_sha}), and its verdict={receipt['verdict']} "
                 f"failure_origin={receipt['failure_origin']}: the planted edges "
                 f"are not formed at all (#8503), and the receipt's "
                 f"retrievability rate is UNMEASURED (#9596)"),
    }

    # ── E5 ────────────────────────────────────────────────────────────────
    e5 = score_e5(sessions, Path(args.e5_out))
    e5_result = e5_outcome(raw_loss=e5["raw_loss"],
                           narrative_loss=e5["narrative_loss"],
                           sentences=e5["raw_n"], bars=bars)
    e5_row = {
        "verdict": verdict_word(e5_result),
        "numerator": e5["raw_n"] - e5["raw_loss_count"],
        "denominator": e5["raw_n"],
        "note": (f"{e5_result['value']}; bar {e5_result['bar']}; raw recovery = "
                 f"1 - {e5['raw_loss']:.2f} = {e5_result['raw_recovery']:.4f} "
                 f"FAILS the 0.80 absolute floor while the "
                 f"{e5_result['ratio']:.4f} ratio passes 0.90 — the record's own "
                 f"note says the ratio alone is degenerate, which is why the "
                 f"absolute floor exists. The re-score grades the 2026-10-18 arm "
                 f"receipts against THIS commit's gold (the gold is part of "
                 f"fixtures_hash) and warns INCOMPLETE RECEIPTS: "
                 f"{'; '.join(e5['partial_receipts']) or 'none'} — its holdout "
                 f"delta cannot be certified. #9738 also records that §10's prior "
                 f"measurement was visible when the record claimed it was not, and "
                 f"that the 25-bound vs 29-marker populations must be reconciled "
                 f"before either number is called final"),
    }

    rows = build_rows(head=head, out_path=out_path, log_corpus_sha=corpus_sha,
                      e3=e3, e4=e4, e5=e5_row, e1=e1_row, e2=e2_row)

    text = render_log(head=head, prereg_sha=prereg_sha, bars=bars,
                      corpus_sha=corpus_sha, graph=graph, runs=runs,
                      observed=observed, unobserved=unobserved,
                      census_data=census_data, e1=e1, e1_aux=e1_aux,
                      e1_out=e1_result, e1b_out=e1b_result,
                      e2a_out=e2a_result, e2b_out=e2b_result, e3=e3,
                      e3_out=e3_result, e4=receipt, e4_out=e4_result, e5=e5,
                      e5_out=e5_result, rows=rows, out_path=out_path,
                      arm=arm)

    destination = ROOT / out_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(text)
    sys.stdout.write(text)
    print(f"capstone-measure: wrote {out_path}", file=sys.stderr)
    print(f"capstone-measure: sessions failed: {failed or 'none'}",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
