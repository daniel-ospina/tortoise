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
    against this corpus's current gold), and E1/E1b/E2a/E2b (read out of the
    `#9889` measurement record, ``E1_E2_SCORES``). Each row's ``evidence`` is the
    artifact that actually carries its number.

⚠️ WHAT THIS PROBE DOES NOT DO, stated so the table cannot be read as more than it
is. E1/E2 are scored from ``docs/evidence/9889/e1-e2-scores.json``, one
invocation of ``e1_e2_scores.py`` over the three ``#9889`` probes' own outputs,
and NOT from the superseded ``e1-e2.log`` — that parse path is removed, because
the log carries 25 Objects (under E1's frozen floor of 30), ONE arm, and a corpus
that is not the pre-registration's. What the record cannot give is **n**: the
after arm exists once and the before arm twice, so E1's pairings disagree (20.6 pp
apart) and the row is REFUSED rather than decided — the honest reading, and the
next requirement (n >= 2 per arm) is stated in the row's note. E2a's frozen
14-unit denominator IS carried (8 of 14 survived, falsifier reaches 14/14), so the
E2 row is scored fact rather than a refusal. E1b's
``grading.strict_survival_counts`` over the m2 lane is still unproduced, so that
half stays a no-loss CONTROL, not the frozen metric.

⚠️ WHAT A REFUSED ROW IS. ``BLOCKED`` is neither a pass nor a fail: "the
population could not decide" and "the criterion was scored and failed" are
different findings, and the table admits both on purpose
(``below_floor``: *"REFUSED, which is neither a pass nor a fail"*).

⚠️ THE RUN ARM. ``TORTOISE_VET`` and ``TORTOISE_CLASSIFY_LATER`` are forced ON for
the measured arm — IN PROCESS, with the previous values restored in a ``finally``
and the set values READ BACK from ``os.environ`` before the pass, the way
``e1_e2_probe.run_arm`` does it (they default OFF in the deployed config), so
S2.2/S2.3 are ON-PATH for this pass — the convention #9076's E1/E2 run used ("the
gate is measured with its flags forced ON"). ``TORTOISE_VALUE_GATE`` is pinned to an
EXPLICIT ``"0"`` (``ARM_OFF``, NOT popped) for the same reason: it also switches
S2.2 on, so an ambient value would silently change the arm — and since ``#10996``
an ABSENT ``TORTOISE_VALUE_GATE`` IS the ON arm, so popping it would arm the
predicate this arm declares OFF. The ``arm:`` line is that read-back, never a typed
literal — a run that cannot read a flag back as set REFUSES rather than printing a
claim nothing supports. **The seam has since LANDED (#9116), so this module no
longer reports them as unobservable-by-construction.** Pre-seam, ``capture_session``'s
receipt could not carry them and the failure was ONE LAYER UP from where it was
first attributed: ``extract_session_v2`` built the surfaces
(``result["classify_later"]`` at ``tortoise/extractor_v2.py:14586-14592`` and
``result["vet"]`` at ``:14596-14600``) while the meta fold dropped them with
``"stats": out.get("stats") or {}``. The fold now calls
``_capture_stats_with_step_witness`` (``tortoise/sdk.py:17484``), which copies each
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
import importlib.util
import json
import os
import re
import subprocess
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

#: ``docs/evidence/7949-capstone/probes/capstone_measure.py`` → repo root.
ROOT = Path(__file__).resolve().parents[4]

# Running this file as a script puts the PROBES directory on sys.path, so
# ``tests.eval.write_path.corpus`` (the corpus's own fixtures_hash + fixtures)
# would not import from the acceptance command's invocation. The repo root is what
# that module lives under, and `tortoise`/`tests` are both resolved from it.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

#: Imported AFTER the sys.path fix above (this file runs as a script from ``probes/``), and through
#: a FUNCTION so the import sits where the path is already correct rather than mid-module: a
#: module-level ``from tortoise...`` here is E402, and ruff runs over this file in CI.
def _derivation_label_classes() -> frozenset[str]:
    """The LINK-anchored derivation classes, from the writer's own constant (#9736)."""
    from tortoise.projection.edges import _DERIVATION_REFERENCES_LABELS

    return frozenset(_DERIVATION_REFERENCES_LABELS)


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

#: E1/E2's artifact is the `#9889` measurement record, NOT `e1-e2.log`.
#:
#: `e1-e2.log` was the only E1/E2 artifact for eleven days, and it cannot decide
#: either row: 25 Objects (under E1's frozen floor of 30), one arm, and a corpus
#: that is not the pre-registration's frozen one. Its parse path is GONE — E1/E2
#: are scored from `e1_e2_scores.py`'s record, which reads the three `#9889`
#: probes' own outputs (`e1_paired_arm` x2 OFF realizations, the ON run's census,
#: `e2a_value_units` + its anchor-injection falsifier, `e1_gate_drops`) and
#: carries the frozen corpus digest because the OFF probe refuses to run on any
#: other corpus (G1). A row that cited the old log is now a row with no artifact.
E1_E2_SCORES = "docs/evidence/9889/e1-e2-scores.json"
E1_E2_SCORES_CMD = (
    "uv run python docs/evidence/7949-capstone/probes/e1_e2_scores.py "
    f"--out {E1_E2_SCORES}")

#: E4's evidence is selected by the receipt's OWN ``corpus_hash``, never by a filename
#: (see ``select_e4_receipt``). ``E4_RECEIPT`` names the receipt the row USED to cite and
#: is kept as that record — it is NOT a fallback for any row, and ``build_rows`` refuses an
#: ``e4`` mapping that does not carry its own ``evidence``.
E4_RECEIPT_DIR = "tests/eval/write_path/receipts"
E4_RECEIPT_GLOB = "*.json"
E4_RECEIPT = "tests/eval/write_path/receipts/w2b-operator-2026-10-19.json"
E5_RESCORE = "docs/evidence/7949-capstone/e5-rescore.json"
ARMS_DIR = "tests/eval/write_path/receipts/7947-arms-2026-10-18"
CORPUS_DIR = "tests/eval/write_path"

SCHEMA = "capstone-verdict-v1"
BEGIN = "=== CAPSTONE-VERDICT-BEGIN ==="
END = "=== CAPSTONE-VERDICT-END ==="
CRITERIA = ("E1", "E2", "E3", "E4", "E5")
STEPS = ("S0", "S1", "S2.1", "S2.2", "S2.3", "S3", "S5", "S6")

#: The node classes the pre-registration's E3 denominator names: ``Point`` (the anchor sits on the
#: node, #5038) plus the LINK-anchored classes the writer itself names —
#: ``_DERIVATION_REFERENCES_LABELS`` (``Event``, ``Document``; ``edges.py:747``).
#: ⛔ DERIVED, not hardcoded (#9736). This constant used to read ``("Point", "Object", "Event")``
#: with the comment "not a choice this probe makes" — and that WAS a choice: ``:Object`` mention
#: links carry no ``sourceVersion`` BY DESIGN (#5199 option A — ``Object`` is deliberately ABSENT
#: from ``_DERIVATION_REFERENCES_LABELS``'s own comment block, cited by SYMBOL not by line), so counting
#: them made E3's ``= 1.000`` unreachable and a re-run would report ``148/433`` while the registration
#: now names ``148/383``. A probe that disagrees with the registration measures a criterion nobody
#: registered, so the set follows the writer's constant and the gate derives the same one.
DERIVED_LABELS = ("Point", *sorted(_derivation_label_classes()))

#: The node classes the CENSUS enumerates per label — ⛔ NOT the E3 row selector above.
#: ``per_label`` is the census's per-class node/anchor block, and a SECOND instrument reads it:
#: ``tools/s3_prior_ablation.py``'s ``created_counts`` takes ``per_label["Object"]["nodes"]`` as
#: the ``objects`` numerator of ``extraction_nodes = claims + objects + events`` (#10463/#10468),
#: on live data, three times per run. Enumerating ``per_label`` with ``DERIVED_LABELS`` made that
#: key VANISH when #9736 narrowed E3 to the anchored classes — so every future ablation run would
#: print ``objects=0`` while the graph held Objects, silently recomputing #10463's published ratio
#: on a narrower definition than its committed log was measured with, with no error and no refusal
#: (#10525 review P1, the silent-zero shape). The census's set is therefore its OWN: E3's classes
#: UNION the mention class E3 deliberately excludes, so a change to the E3 row can never re-narrow
#: an instrument that is not about E3.
CENSUS_LABELS = tuple(sorted(set(DERIVED_LABELS) | {"Object"}))

#: The relationship type that points FROM a derived node at its source. The
#: anchor is a property of that DERIVATION LINK, so the derived node is its
#: START: ``(Point)-[:extractedFrom]->(GraphEvent|Event)``.
ANCHOR_OUT = ("extractedFrom",)

#: The relationship type that points AT a derived node: ``(Source)-[:references]
#: ->(Event|Document)``, written by ``_EdgeHandlers.link_source_to_event``
#: (``tortoise/projection/edges.py:2832-2857``, ``MERGE (s)-[r:references]->(e)
#: ON CREATE SET r.sourceVersion = s.contentHash``) and reached from the capture
#: path at ``tortoise/sdk.py:17882`` (``proj.link_source_to_event(source_key,
#: event_id)`` — ``(Source)-[:references]->(sessionCaptured Event)``). The anchor
#: is on the same link, but the DERIVED node is its TARGET.
#: ⚠️ Counting this one in the ``extractedFrom`` direction counts SOURCES.
ANCHOR_IN = ("references",)

E3_RULE = (
    "a derived node (Point/Event/Document — the classes the model anchors; see DERIVED_LABELS) "
    "carries a version anchor iff it is the "
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

#: The flags the arm PINS OFF so the measured population is a function of THIS
#: file, not of whatever the operator's shell held. ``TORTOISE_VALUE_GATE`` also
#: switches S2.2 on (``extractor_v2._value_gate_enabled``), so an ambient value
#: would silently change the arm between two runs of the same probe — and
#: ``TORTOISE_S2_DECISION_SEAM`` (#5087) is a THIRD switch for S2.2 (it arms the
#: arbiter, so ``_vet_enabled`` is True), which would arm a network call and a
#: spend inside an arm this file declares OFF.
#:
#: ⛔ These are pinned to ``"0"``, NOT popped (`#10996` review): popping produced
#: an OFF arm only while the flag defaulted OFF, and an ABSENT
#: ``TORTOISE_VALUE_GATE`` is now the ON arm (the S2.2 mechanical half defaults
#: ON). Measured at the review head: inside ``measured_arm()`` the header printed
#: ``TORTOISE_VALUE_GATE=<unset>`` while ``value_gate.value_gate_enabled()`` was
#: True — a re-take of a committed log would have measured a population that
#: includes the predicate, at a header claiming it was pinned off.
ARM_OFF = ("TORTOISE_VALUE_GATE", "TORTOISE_S2_DECISION_SEAM")

# ── the DOOR (#10983 / C2) ────────────────────────────────────────────────
#
# "Which code path produced this number" is a SEPARATE question from "which arm
# was it run under", and this probe answered only the second one. The extraction
# LANE is selected by an ordinary environment variable that ``measured_arm`` did
# not touch:
#
#   * ``tortoise/sdk.py`` — ``TORTOISE_SESSION_EXTRACTOR == "m2"`` routes
#     ``capture_session`` to ``_extract_session_llm``; everything else routes to
#     ``_extract_session_v2`` (the v4 door).
#   * ``tortoise/hosted_api.py::_capture_lane`` — the hosted endpoint's mirror of
#     the SAME predicate, so the two entry points select ONE lane, not two.
#
# An ambient ``TORTOISE_SESSION_EXTRACTOR=m2`` on the operator's box therefore
# rerouted all seven acceptance captures to the OTHER extractor while the log
# still printed an ``arm:`` line that said nothing about it — the silent seam
# #10983 exists to close. The lane, plus every switch on this path that changes
# what the run MEASURES through it, is pinned here and READ BACK like every other
# switch (the ``arm:`` rule, applied to the door).
#
# ⛔ The set below is CLOSED over the **measurement modules the path renders
# with**, and what is enforced MECHANICALLY is the CENSUS over that list, not the
# list's membership: `path_modules` is a HAND-MAINTAINED tuple, and every
# ``TORTOISE_*`` read inside those modules must be classified, or the suite reds.
# The module names are NOT restated here on purpose — a count in this comment is a
# transcription that rots: it said "``extractor_v2.py`` plus the three modules it
# reaches one call down" for three review rounds while the list had already grown
# past it (cycle 9 measured the comment at four modules against eight in
# `tests/test_7949_c2_door.py::path_modules`). The authority is `path_modules` in
# that test. ⚠️ Membership is hand-kept, so a read added to a module OUTSIDE that
# tuple is NOT caught by it — the ``sdk``/``hosted_api`` helpers the two hops call
# are the measured gap, tracked as #11410. Every ``TORTOISE_*`` those modules
# read is accounted for,
# either as an arm seam in ``SEAM_LEDGER`` (``TORTOISE_VET``,
# ``TORTOISE_CLASSIFY_LATER``, ``TORTOISE_S4_DELTA``), or as a pin below, or as
# an INPUT rather than a setting (``TORTOISE_DB_URI`` the graph it writes,
# ``TORTOISE_API_URL`` a backend label), or as a declared not-a-measurement-seam
# read with its reason (`acknowledged_not_a_measurement_seam`).
#
# ⚠️ The claim is scoped to what it can enforce. The same call chain also reads
# INFRASTRUCTURE variables (record DSN, packs dir, embedding gates, store
# connection). Those are what a run needs in order to happen at all, not settings
# that move the measured population, and the door neither pins nor partitions
# them. Adding another derived module is a one-line change to `path_modules` in
# the test (the arm-seam modules are already there). An earlier revision of this comment called the whole path "closed" over
# "exactly ten" variables and then partitioned 3 arm seams + 6 pins + 2 inputs =
# 11; the #10983 review (cycle 5) measured both halves of that false —
# ``TORTOISE_REFERENTIALITY`` is read on every v2 capture through
# ``referentiality.ENV_FLAG`` and was accounted for by nothing.
#
# ⚠️ The set below is what the #10983 reviews widened, in TWO rounds. The first
# revision pinned the lane and the two render switches and called that "the seam
# set"; round 1 found ``TORTOISE_SESSION_LLM_MOCK`` (decisive: it swaps the model
# for ``_V2SessionMock`` — ``sdk._session_llm_mock_enabled`` — AND short-circuits
# the provider-key check, so ``backend_ready()`` still passes on a real key and a
# fully mocked run is byte-indistinguishable in the log from a real one), the
# prompt render and the model selector; round 2 found the two TRUNCATION BUDGETS
# (``_stage_cap`` / ``_extractor_escalation_tokens``, both read at call time),
# which decide how much of each stage's output survives. A pin set that stops at
# "the pipeline" and leaves "what the pipeline reads" ambient is the same
# declaration-versus-control defect, one layer down.
#
# The provider ROUTE is deliberately NOT pinned: ``TORTOISE_EXTRACTOR_PROVIDER``
# only chooses among routes the box already has a key for, and the route is
# OBSERVED rather than forced — the receipt reports it as ``extraction_mode``
# (``llm:<route>``, ``sdk.py``), which ``run_corpus`` records and ``render_log``
# prints per session. Named here so the set below is not read as an exhaustive
# list of every ``TORTOISE_`` variable in the process.

#: The env var that selects the extraction lane.
DOOR_LANE_ENV = "TORTOISE_SESSION_EXTRACTOR"

#: The lane the acceptance runs when that var is unset — and the one the run is
#: PINNED to (``_capture_lane()``'s default, ``hosted_api.py``).
DOOR_LANE = "v2"

#: Unset (not ``"0"``) is the correct pin for each of these: every one selects a
#: DIFFERENT behaviour when PRESENT, and none is an on/off switch whose absent
#: state is the risky one.
#:
#: * ``TORTOISE_SESSION_EXTRACTOR`` — ``m2`` is the OTHER extractor; only
#:   ``unset`` is the default the fleet runs, and the log must state which one
#:   this was.
#: * ``TORTOISE_EXTRACTOR=v1`` — read by ``sdk.commit_session`` (``sdk.py``), NOT
#:   by ``capture_session``: the acceptance path dispatches on
#:   ``TORTOISE_SESSION_EXTRACTOR`` alone, so this pin is DEFENSIVE for any
#:   commit the probe performs, not a route off the capture door. Said plainly
#:   because the #10983 review found the earlier "a route off this door"
#:   rationale claimed a reach this variable does not have on this path.
#: * ``TORTOISE_LABEL_ORDER=shuffle`` — NOT a behaviour seam (#10983): it is a
#:   render DIAGNOSTIC that reorders the kind lists per call, i.e. it makes two
#:   runs of this probe differ for a reason that is not the arm. Pinned off.
#: * ``TORTOISE_SESSION_LLM_MOCK`` — the offline model seam. It replaces the
#:   model with ``_V2SessionMock`` AND short-circuits the provider-key check, so
#:   an ambient ``=1`` makes every capture canned output while
#:   ``backend_ready()`` still passes and the log still claims a real run. The
#:   acceptance is scored on a REAL provider; a mock is only ever an explicit
#:   arm, never an inherited one (#10983 review).
#: * ``TORTOISE_EXTRACTOR_PROMPT=compact`` — a third render switch beside
#:   ``TORTOISE_LABEL_ORDER`` (``extractor_v2.py`` renders a different master
#:   under it, and that render is explicitly NOT byte-identical to the default
#:   since #9733), so it changes what the run measures without changing the arm.
#: * ``TORTOISE_EXTRACT_MODEL`` — selects the extractor's MODEL (the producer the
#:   acceptance's numbers come from). It does NOT by itself move the output bound:
#:   every stage passes ``max_tokens=_stage_cap(...)`` per call, which beats the
#:   adapter's constructor default — measured at this head, an explicit model id
#:   with ``TORTOISE_EXTRACTOR_MAX_TOKENS`` unset still caps S2/S4 at 16000. The
#:   bound has its own switch, pinned below (#10983 review round 2 corrected this
#:   bullet, which had credited the model selector with the bound).
#: * ``TORTOISE_EXTRACTOR_MAX_TOKENS`` — the stage output cap (``_stage_cap``, read
#:   at CALL time): pinned unset ⇒ the shipped 1500/16000 stage defaults. An
#:   ambient value (the documented lever an operator raises after a timeout
#:   spike) shrinks how much of each stage survives truncation, i.e. it changes
#:   the population being measured, under a header that would still claim the
#:   door held (#10983 review round 2).
#: * ``TORTOISE_EXTRACTOR_ESCALATION_TOKENS`` — the length-truncation RETRY budget
#:   (``_extractor_escalation_tokens``): the same class, pinned unset.
#: * ``TORTOISE_REFERENTIALITY`` — the S2.2 mention-selection arm
#:   (``referentiality.enabled()``, reached through ``referentiality.ENV_FLAG``
#:   from ``extractor_v2``'s prompt/mention path): armed, the prompt contract and
#:   the entity mentions a run yields change without changing the lane. Found in
#:   the #10983 review (cycle 5) as the one measurement switch the set missed,
#:   because it is read one module away from the file the closure test derived
#:   from.
#: * ``TORTOISE_LABEL_ORDER_SEED`` — inert while ``TORTOISE_LABEL_ORDER`` is off,
#:   and pinned anyway so the pair cannot be split: setting the seed without the
#:   render switch is a no-op today, and a future edit that reads the seed
#:   independently must not inherit one from the operator's shell.
DOOR_PINNED_OFF = ("TORTOISE_SESSION_EXTRACTOR", "TORTOISE_EXTRACTOR",
                   "TORTOISE_LABEL_ORDER", "TORTOISE_LABEL_ORDER_SEED",
                   "TORTOISE_SESSION_LLM_MOCK", "TORTOISE_EXTRACTOR_PROMPT",
                   "TORTOISE_EXTRACT_MODEL", "TORTOISE_EXTRACTOR_MAX_TOKENS",
                   "TORTOISE_EXTRACTOR_ESCALATION_TOKENS",
                   "TORTOISE_REFERENTIALITY", "TORTOISE_CONTEXT_ENRICH",
                   # cycle 8, dropped by the peer head and restored here: an
                   # ambient =1 ADDS a :GraphEvent to the graph the run writes
                   # (change_feed.py:182 via sdk._change_feed_enabled) and the
                   # probe's S6 witness prints that count (measured: 2 vs 3).
                   "TORTOISE_CHANGE_FEED",
                   # cycle 10: read through a PARAMETER (`_pick(tau_hi,
                   # "TORTOISE_SUBJECT_TAU_HI", ...)` -> `os.environ.get(env_name)`)
                   # from the write path, and it decides whether the
                   # `:aboutSubject`/`:aboutObject` edge is written at all
                   # (measured: 0.90 -> bound at the defaults, suspected with
                   # TORTOISE_SUBJECT_TAU_HI=0.99). Absent is the shipped
                   # default, so pinning them UNSET matches today's behaviour.
                   "TORTOISE_SUBJECT_TAU_HI", "TORTOISE_SUBJECT_TAU_LO",
                   # `#11002`: the bounded re-narrate's KEEP-RATE arm
                   # (`extractor_v2._renarrate_threshold`). UNSET is the shipped
                   # default and the only state the registered corpus was scored
                   # under: unset/blank/non-numeric/out-of-range all read as
                   # DISARMED by construction, while an armed value (0 < v <= 1)
                   # fires the retry — an EXTRA S1+S2 band whose union can add
                   # candidates — on any session whose batch keep-rate falls under
                   # it. So an ambient value changes both the population and the
                   # cost of what a run measures, under a header that would still
                   # claim the door held. The other trigger arm
                   # (`coverage_suspect`) needs no threshold and is not a switch.
                   "TORTOISE_RENARRATE_KEEP_RATE",
                   # `#11534`: the S2.2b-ADD gap-fill stage's own arm
                   # (`extractor_v2._partial_gap_fill_enabled`, its one `env_flag`
                   # read). UNSET is the shipped default and the only state the
                   # registered corpus was scored under; an armed `=1` spends a
                   # bounded model call ADDING items to the embed list before S3,
                   # i.e. it moves the population this run measures under a header
                   # that would still claim the door held. Default-OFF, so pinning
                   # it OFF matches today's behaviour and needs no value.
                   "TORTOISE_PARTIAL_GAP_FILL")

#: Seams pinned to an EXPLICIT value because their ABSENT state is not neutral.
#: ``TORTOISE_S4_DELTA`` is DEFAULT-ON (``env_flag(..., True)``); an ambient
#: ``=0`` silently swaps S4's output contract for the legacy complete-list one,
#: which changes the embed list a run measures without changing the arm.
DOOR_PINNED_ON = (("TORTOISE_S4_DELTA", "1"), ("TORTOISE_JSON_MODE", "1"),
                  # cycle 9 (#10983): `sparse.py:226` `env_flag(IDENTIFIER_ENV,
                  # True)` — default-ON, read through `projection/entities.py:1478`
                  # on the write path (`create_point` -> `point_identifier_aliases`),
                  # so it REWRITES `search_keys` on every ``:Point`` the run writes.
                  # Pinned to the SHIPPED default, not off: an ambient `=0` is the
                  # non-neutral state here, and the registered corpus yields no
                  # aliases either way (0 of 27 points, 0 turns across the 7
                  # fixtures — measured), so the pin makes the population
                  # insensitive to the shell without moving a number.
                  ("TORTOISE_IDENTIFIER_TOKENS", "1"),
                  # `#10360`, and the union that absorbed `#11449` (`91dbc9a88`):
                  # `extractor_v2` is the ONLY module that reads this switch
                  # (`_state_value_verbatim_enforced`, its one `env_flag` read — the
                  # nine modules the closure test names are the path it SCANNED, not
                  # the readers), and it reads it PER POINT at the emit site. Its
                  # ABSENT state is NOT the neutral one to inherit: the code resolves
                  # `env_flag(SWITCH, True)` (absent/blank/unparseable ⇒ ON), so an
                  # ambient `=0` DISARMS the write path's verbatim-value refusal (a
                  # rewritten value is then WRITTEN and the resurrection guard cannot
                  # fire), which changes the population under a header that would
                  # still claim the door held. Pinned to the SHIPPED default, exactly
                  # as `TORTOISE_S4_DELTA`.
                  ("TORTOISE_STATE_VALUE_VERBATIM", "1"))

#: Every key a complete read-back carries — the pinned-off set plus the
#: pinned-to set. A ``door_header``/``door_record`` handed an INCOMPLETE dict is
#: treated as NOT MEASURED, because every pinned-off target state is ``<unset>``:
#: rendering a partial dict would print ``KEY=<unset> … read back from os.environ``
#: about keys nobody read (#10983 review round 11).
DOOR_KEYS = frozenset(DOOR_PINNED_OFF) | frozenset(k for k, _ in DOOR_PINNED_ON)

#: The v4 seam ledger AT THE HEAD THIS RUN MEASURED (#10983, C2's Target 3).
#:
#: ``(env var, module.resolver, default, is a behaviour seam)``. The DEFAULT
#: column is the CODE's, not a document's — a ledger copied out of prose is how
#: this one came to call ``TORTOISE_VALUE_GATE`` default-off after #10996 had
#: already made it default-ON at ``value_gate.value_gate_enabled`` and, through
#: ``extractor_v2``'s ``vet_enabled`` OR, armed the S2.2 pass on the fleet's
#: default write path. ``tests/test_7949_c2_door.py`` resolves every row against
#: the modules and reds if this table and the tree disagree.
SEAM_LEDGER = (
    ("TORTOISE_VET", "extractor_v2._vet_enabled", "off", True),
    ("TORTOISE_CLASSIFY_LATER", "extractor_v2._classify_later_enabled", "off", True),
    ("TORTOISE_S2_DECISION_SEAM", "s2_decision_seam.seam_enabled", "off", True),
    ("TORTOISE_VALUE_GATE", "value_gate.value_gate_enabled", "on", True),
    ("TORTOISE_S4_DELTA", "extractor_v2._s4_delta_enabled", "on", True),
    ("TORTOISE_LABEL_ORDER", "extractor_v2._label_order_rng", "off", False),
)

#: The three Point classes E3's frozen denominator mixes, each read off a
#: property its writer stamps (never a hand-kept list): ``is_episodic`` is set by
#: the turn writer (``_TURN_WRITE_CYPHER``, ``tortoise/sdk.py:2530`` — turn
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
    door_off = tuple(DOOR_PINNED_OFF)
    door_on = tuple(key for key, _ in DOOR_PINNED_ON)
    saved = {key: os.environ.get(key) for key in (*keys, *door_off, *door_on)}
    try:
        for key in ARM_ON:
            os.environ[key] = "1"
        for key in ARM_OFF:
            # explicit falsy, never absent — `#10996`: an unset default-ON
            # switch is the ON arm, so a pop would be a lie in the header below.
            os.environ[key] = "0"
        for key in door_off:
            os.environ.pop(key, None)
        for key, value in DOOR_PINNED_ON:
            os.environ[key] = value
        observed = {key: os.environ.get(key)
                    for key in (*keys, *door_off, *door_on)}
        for key in ARM_ON:
            if observed[key] != "1":
                raise SystemExit(
                    f"capstone-measure: REFUSED — the measured arm sets "
                    f"{key}=1 but os.environ reads back {observed[key]!r}; a log "
                    f"that declares an arm it did not run is a fabricated "
                    f"measurement, so this pass writes no table")
        leaked_lane = observed.get(DOOR_LANE_ENV)
        if leaked_lane not in (None, ""):
            raise SystemExit(
                f"capstone-measure: REFUSED — the run pins the {DOOR_LANE!r} "
                f"extraction lane, but {DOOR_LANE_ENV} reads back "
                f"{leaked_lane!r} after being unset. A capture taken under "
                f"another lane measures a DIFFERENT door (#10983), so this pass "
                f"writes no table")
        # EVERY other pinned-off switch, re-read and refused on a leak. The lane
        # has its own message above (it changes the door); these change what the
        # run MEASURES through the same door, and the #10983 review found them
        # popped, printed in the ``door:`` line, and never checked — so the
        # header's "read back" claim was one var wider than the code. A pin that
        # cannot fail is a declaration, not a control.
        for key in door_off:
            if key == DOOR_LANE_ENV:
                continue
            leaked = observed.get(key)
            if leaked not in (None, ""):
                raise SystemExit(
                    f"capstone-measure: REFUSED — {key} is pinned UNSET, but it "
                    f"reads back {leaked!r} after being popped. It changes what "
                    f"this run measures through the same door, so the arm cannot "
                    f"be declared (#10983) and this pass writes no table")
        for key, expected in DOOR_PINNED_ON:
            if observed.get(key) != expected:
                raise SystemExit(
                    f"capstone-measure: REFUSED — {key} is pinned to "
                    f"{expected!r} but reads back {observed.get(key)!r}; that "
                    f"seam changes what the run measures, so the arm cannot be "
                    f"declared (#10983)")
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
            "back from os.environ after setting; the deployed config sets none "
            "of them — since #10996 the value gate defaults ON, so the pinned 0 "
            "is what makes this arm OFF)")


def arm_record(observed: dict[str, str | None]) -> dict:
    """The machine half of the arm: the SAME read-back the header prints."""
    record: dict[str, str | None] = {key: observed.get(key)
                                     for key in (*ARM_ON, *ARM_OFF)}
    record["source"] = "os.environ read-back after measured_arm() forced it"
    return record


def door_header(observed: dict[str, str | None]) -> str:
    """The ``door:`` line, DERIVED from the run's own read-back (#10983).

    Names the entry point in CODE terms — the function chain and the lane
    selector, not "the pipeline" — plus the value each pinned switch RESOLVED TO
    on this run and the six-seam ledger with the DEFAULTS the pin suite resolves
    against the modules. A log therefore states the seam set it ran rather than
    the one a reader remembers (`#10983` Target 3).

    ⛔ An EMPTY read-back is NOT a pinned door. For every switch here the target
    state is ``<unset>``, so a header rendered from nothing used to print
    byte-identical text to a header rendered from a run whose pins HELD —
    ``render_log``'s ``door`` defaults to ``None`` and the repo's own fixtures
    call it that way, so a fixture log asserted "read back from os.environ" about
    a read-back that never happened (#10983 review). Absence is now said out
    loud, on the same rule the ``arm:`` line already follows for a flag it did
    not measure.
    """
    if not _door_measured(observed):
        return ("NOT MEASURED — this render carries NO COMPLETE measured_arm() "
                "read-back, so no lane and no pinned switch is attested. Render "
                "the log from a run that entered ``measured_arm()`` (``main`` "
                "does) before citing the door")
    lane = observed.get(DOOR_LANE_ENV)
    # The HEADLINE is the read-back, never the module constant: a read-back that
    # disagrees with the pin must not be summarised as the pin (#10983 review).
    # ``main`` never reaches here in that state (measured_arm refuses first), so
    # this is the path a re-render tool or a fixture would take.
    lane_pinned = lane in (None, "")
    lane_word = DOOR_LANE if lane_pinned else lane
    pins = [f"{key}=<unset>" if observed.get(key) in (None, "")
            else f"{key}={observed.get(key)}"
            for key in DOOR_PINNED_OFF]
    pins += [("<unset>" if observed.get(key) in (None, "")
              else f"{observed.get(key)}") .join([f"{key}=", ""])
             for key, _ in DOOR_PINNED_ON]
    seams = ", ".join(f"{env}={default}" for env, _where, default, _seam
                      in SEAM_LEDGER)
    return (f"{lane_word} lane (sdk.capture_session -> sdk._extract_session_v2 "
            f"-> extractor_v2.extract_session_v2; hosted_api._capture_lane() "
            f"mirrors the same predicate) | {DOOR_LANE_ENV}="
            f"{'<unset>' if lane_pinned else lane} "
            f"{'PINNED' if lane_pinned else 'LEAKED — NOT THE PINNED LANE'} "
            f"({' '.join(pins)} read back from os.environ) | seam defaults at "
            f"this head: {seams}")


def _door_measured(observed: dict[str, str | None]) -> bool:
    """Does this read-back carry EVERY pinned key? A partial one is not a pin."""
    return bool(observed) and set(observed) >= DOOR_KEYS


def door_record(observed: dict[str, str | None]) -> dict:
    """The machine half of the door: lane, pins, and the six-seam ledger.

    An empty (or PARTIAL) read-back records ``measured: false`` and NO pin block,
    rather than borrowing the measured record's ``source`` sentence — a machine
    record that says "os.environ read-back after measured_arm() forced the door"
    about a render that never ran one is the same false claim as the log line
    (#10983 review). ``measured`` is stated in BOTH directions so a consumer can
    key on it instead of inferring it from an absent key.

    ``lane`` follows the READ-BACK, not the module constant: a record that
    printed ``lane: v2`` beside ``lane_read_back: m2`` would let a consumer key
    on the wrong field (#10983 review round 11). ``lane_pinned`` says whether the
    read-back matched the pin.
    """
    if not _door_measured(observed):
        return {
            "measured": False,
            "lane": DOOR_LANE,
            "lane_env": DOOR_LANE_ENV,
            "entry_point": ("sdk.capture_session -> sdk._extract_session_v2 -> "
                            "extractor_v2.extract_session_v2"),
            "hosted_selector": "hosted_api._capture_lane",
            "note": ("NOT MEASURED — render_log was called without a door "
                     "read-back, so no pin is attested for this run"),
        }
    return {
        "measured": True,
        "lane": observed.get(DOOR_LANE_ENV) or DOOR_LANE,
        "lane_env": DOOR_LANE_ENV,
        "lane_read_back": observed.get(DOOR_LANE_ENV),
        "lane_pinned": observed.get(DOOR_LANE_ENV) in (None, ""),
        "entry_point": ("sdk.capture_session -> sdk._extract_session_v2 -> "
                        "extractor_v2.extract_session_v2"),
        "hosted_selector": "hosted_api._capture_lane",
        "pinned_off": {key: observed.get(key) for key in DOOR_PINNED_OFF},
        "pinned_on": {key: observed.get(key) for key, _ in DOOR_PINNED_ON},
        "seams": [{"env": env, "resolver": where, "default": default,
                   "behaviour_seam": seam}
                  for env, where, default, seam in SEAM_LEDGER],
        "source": "os.environ read-back after measured_arm() forced the door",
    }


#: The ``extraction_mode`` values that mean a capture REACHED the door.
#:
#: ⛔ The COLON is the discriminator, not the ``llm`` — the two lanes differ by
#: exactly that character, so do not "tidy" this into a route-agnostic ``llm``
#: prefix (the #10983 review measured that the whole suite stays green under
#: ``"llm"``):
#:
#: * the v2 lane reports ``llm:<route>`` (``sdk.capture_session`` ->
#:   ``_extract_session_v2`` -> the adapter's own ``route``, always truthy on a
#:   success);
#: * the OTHER lane reports a BARE ``"llm"`` — ``_extract_session_llm`` reports it
#:   with no route and no colon, pinned by
#:   ``tests/test_capture_session.py::test_capture_session_zero_extraction_is_warning_not_failure``.
#:   A prefix of ``"llm"`` would therefore admit an m2 capture as door-entered,
#:   which is the one bypass this witness exists to refuse. The bare ``"llm"``
#:   spelling is pinned in
#:   `tests/test_7949_c2_door.py::test_a_capture_that_never_ENTERED_the_door_REFUSES_the_run`
#:   so the widening cannot be made by aligning the m2 lane to this contract.
#:
#: ``replayed`` and ``no-provider`` mean the capture returned BEFORE the lane
#: dispatch (#1727 replay, #3892 keyless), and ``extraction-disabled`` is the
#: hosted endpoint's own pre-dispatch branch (the org's off switch) — it cannot
#: arise on the probed ``sdk.capture_session`` path at all, so it is listed for
#: completeness of the vocabulary, not as a shape this probe can meet. ``empty``
#: has TWO origins and neither is this door: the blank session-id and
#: blank-conversation gates in ``sdk.capture_session`` return before the dispatch,
#: AND the other lane emits it after taking that lane. So none of the four is
#: this door entered. (This sentence used to attach "took a lane that is not the
#: v2 door" to all four spellings; only ``empty`` has that origin. The earlier
#: "returned BEFORE" clause was also over-attributed to ``extraction-disabled``,
#: which no ``sdk`` gate emits — #11298 review, source-verified both ways.)
#:
#: ⛔ ``error`` IS NOT ONE OF THOSE, and listing it here was a measured defect
#: (#11298 review, round 14). ``error`` is a POST-dispatch outcome — the lane ran
#: and REPORTED a failure (raised, or caught and appended), mode set INSIDE it: by
#: ``sdk._extract_session_v2`` (the door itself) and by ``sdk._extract_session_llm``
#: (the other lane), each setting ``meta["mode"]`` to ``"error"`` iff its own
#: ``errors`` is non-empty. ``sdk.capture_session`` then maps a failed extraction
#: onto it (``elif not ok: effective_mode = "error"``). Never by the pre-dispatch
#: guards. Rows are cited by FUNCTION rather than by ``sdk.py:<line>``, because a
#: line number in prose re-stales on the next insertion — the case this file
#: already carries three times.
#: Counting it as a bypass refused a run for a cause that was false, AFTER its
#: full spend, and — because ``require_door_entered`` raises before the log is
#: written — threw away the ``errors`` that would have shown what actually
#: happened. See ``DOOR_ENTERED_THEN_FAILED``.
DOOR_ENTERED_PREFIX = "llm:"

#: The ``extraction_mode`` a capture reports when it DID enter the door and then
#: FAILED. Treated as entered, not bypassed, for the reason above. The failure is
#: not swallowed by that: ``run_corpus`` keeps each receipt's ``errors``,
#: ``main`` collects the non-``ok`` sessions into ``failed``, and the log — which
#: a bypass no longer prevents — carries them per session (``errors=[…]`` on the
#: session's own line and in the machine record) plus a ``sessions failed: […]``
#: line on stderr after the write.
#:
#: ⚠️ REPORTED, NOT SCORED — and that half is stated here rather than left to be
#: discovered. Nothing about a failed capture stops the pass from being scored:
#: ``main`` still exits 0 and the record's ``run.status`` is still ``"COMPLETED"``,
#: which is the field ``tools/capstone_verdict_check.py`` V5 reads to decide the
#: pass completed — it also reads ``run.steps_observed`` and the step-accounting
#: keys, and none of those distinguishes a failed capture either, because the
#: step totals are summed over sessions. So a run with a failed capture is not
#: distinguishable from a clean one BY THE GATE (a human reading the log is).
#: Filed as **#11414**; changing the exit or the status is a gate decision, not
#: a comment fix.
DOOR_ENTERED_THEN_FAILED = ("error",)


def door_witness(runs) -> dict:
    """How many captures actually ENTERED the door — derived from the receipts.

    ⛔ Found in the #10983 review (round 11). The door was pinned and read back as
    an ENVIRONMENT fact and nothing checked that the path was TAKEN, so a run
    that never reached ``_extract_session_v2`` still produced a table whose
    ``door:`` line asserted the v2 lane and whose record said ``measured: true``,
    exit 0. Both exits are real and both return ``ok=True``:

    * ``no_provider`` (``sdk.capture_session``) — no provider key resolves, so
      the turns are stored and NOTHING is extracted (#3892);
    * the ``#1727`` REPLAY — ``session_existed``, which a re-run against an
      existing graph (``--graph``) reaches for all seven sessions at zero cost.

    The pinned lane cannot catch either, because neither is a different lane: it
    is no lane. ``backend_ready()``'s provider-key check narrows the first, but
    it is a different predicate from ``_build_session_llm_extractor() is None``
    (five provider keys vs ``DEEPSEEK_API_KEY``), so it is not the contract.
    """
    runs = list(runs or [])
    entered = [run for run in runs
               if str(run.get("extraction_mode") or "").startswith(
                   DOOR_ENTERED_PREFIX)
               # A post-dispatch FAILURE still took the door: ``measured_arm``
               # pins this probe to the v2 lane and refuses a leak, so an
               # ``error`` receipt here is a v2 receipt that raised, not another
               # lane and not a return before the dispatch. The failure is
               # reported by ``main``'s failed list, which a bypass would have
               # prevented the log from ever reaching.
               or str(run.get("extraction_mode") or "") in
               DOOR_ENTERED_THEN_FAILED]
    bypassed = [str(run.get("session_id")) for run in runs
                if run not in entered]
    return {"entered": len(entered), "sessions": len(runs),
            "bypassed": bypassed}


def require_door_entered(runs) -> dict:
    """REFUSE a run in which any capture returned before the door.

    A table measured on a graph this run did not write through the door is not a
    measurement of the door, so the pass writes no table — the same posture
    ``measured_arm`` takes for a leaked switch.
    """
    witness = door_witness(runs)
    if witness["bypassed"]:
        raise SystemExit(
            f"capstone-measure: REFUSED — {len(witness['bypassed'])} of "
            f"{witness['sessions']} capture(s) did not enter "
            f"sdk._extract_session_v2 ({witness['bypassed']}), so this pass did "
            f"not take the door it would declare: a keyless capture extracts "
            f"nothing (#3892), a re-capture of an existing session REPLAYS "
            f"(#1727) — both report ok=True — and a capture on the other lane "
            f"reports a bare ``llm`` with no route. A table measured on a graph "
            f"they did not write is a measurement of another run (#10983). "
            f"NOTE: a capture that ENTERED the door and FAILED (mode ``error``) "
            f"is not refused here — its ``errors`` are carried on its own log "
            f"line and in the machine record, with a ``sessions failed: […]`` "
            f"line on stderr after the write, because a refusal here would "
            f"name a false cause AND discard the errors that say what "
            f"actually happened")
    return witness


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
    floors in ``denominator_floors`` — integer COUNTS plus E1's fraction
    ``e1_min_before_share`` (``0.2``), which is a floor under a SHARE and so is
    carried as a float. The prose is PARSED rather than transcribed: a bar typed
    into this file is a bar nobody froze, so a lane could move a threshold by
    editing the probe. An unparseable threshold refuses the run (ValueError → G0,
    exit 3) instead of falling back to a default.
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

    def share_floor(key: str) -> float:
        """A floor under a SHARE (a fraction), not a count.

        ``bool`` is an ``int`` in Python and a fraction is neither, so the count
        helper above would refuse ``0.2``; the record really does carry one
        non-integer floor, and leaving it out of this map is how a renderer came
        to read a key nothing emitted (#10610 review issue 1).
        """
        value = floors.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"denominator_floors[{key!r}] is not a number: "
                             f"{value!r}")
        return float(value)

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
            "e1_min_before_share": share_floor("e1_min_before_share"),
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
               objects: int | None, bars: dict,
               decidable: bool = True,
               below_floor: bool | None = None) -> dict:
    """E1: volume falls (``after <= multiplier x before``) over ALL Objects, floor 30.

    ⚠️ ``objects`` is the DENOMINATOR the criterion names ("**all** `:Object`
    nodes written by the run") — not a hand-picked population. A run under the
    frozen floor is REFUSED: with five Objects a single delete moves the share by
    20 points, which is what the floor exists to stop.

    ⚠️ ``below_floor`` is the RECORD's own floor finding, and it is passed in
    rather than re-derived, because the population that is floored and the one
    that is scored are the same one: the AFTER arm. `e1_pairings` folds it into
    ``decidable`` (`decidable = len(verdicts) == 1 and not below_floor`), so a
    caller that reads only ``decidable`` reports an under-floor after arm as a
    PAIRING DISAGREEMENT it does not have (#10610 review issue 2). The floor arm
    is therefore checked BEFORE the decidability arm, and it has its own value
    text: the pre-registration calls the floor `below_floor` and its verdict
    "REFUSED, which is neither a pass nor a fail".

    ⚠️ ``decidable`` is the second refusal, and it is measured rather than
    assumed (`#9889`): where the after arm exists once and the before arm exists
    in more than one independent realization, the pairings can disagree, and a
    metric whose verdict depends on which before run you pair against has not
    decided anything. The aggregate is REFUSED with both pairings printed — never
    the convenient one.
    """
    floor = bars["floors"]["e1_objects"]
    multiplier = bars["E1"]["multiplier"]
    bar = (f"after_share <= {multiplier} x before_share "
           f"(Objects floor {floor})")
    if before_share is None or after_share is None or objects is None:
        return outcome(measured=False, below_floor=False, passed=False,
                       bar=bar, value="unmeasured")
    bound = multiplier * before_share
    over_floor = (objects < floor) if below_floor is None else below_floor
    if over_floor:
        return outcome(measured=True, below_floor=True, passed=False, bar=bar,
                       value=(f"before={before_share:.6f} after={after_share:.6f} "
                              f"objects={objects} bound={bound:.6f} — but "
                              f"{objects} Objects is under the frozen floor of "
                              f"{floor}, so the population is too small to "
                              "decide the bar and the row is REFUSED, which is "
                              "neither a pass nor a fail"))
    if not decidable:
        return outcome(measured=False, below_floor=False, passed=False,
                       bar=bar,
                       value=(f"before={before_share:.6f} after={after_share:.6f} "
                              f"objects={objects} bound={bound:.6f} — but the "
                              "pairings DISAGREE across before realizations, so "
                              "the bar is not decided at n=1 per arm"))
    return outcome(measured=True, below_floor=False,
                   passed=after_share <= bound, bar=bar,
                   value=f"before={before_share:.6f} after={after_share:.6f} "
                         f"objects={objects} bound={bound:.6f}")


def e2_outcome(*, e2a_result: dict, e2b_result: dict, bars: dict) -> dict:
    """E2 is the COMPOSITE — E2a AND E2b, floored — never E2a alone.

    This function exists because the composite used to be assembled INLINE in
    ``main``, so no pin could reach it: reverting the row's verdict to
    ``verdict_word(e2a_result)`` — a 13/14 E2a beside a fused 7/114 reading GREEN
    in the table and FAIL in the artifact the row cites — left every case green
    (#10610 review issue 1). Extracting it is what makes that mutant killable, and
    the pin calls THIS function while a second pin asserts ``main`` derives its
    row from it (``test_the_e2_row_verdict_is_the_composite_main_uses``).

    ⛔ NEITHER half's refusal state is dropped. The composite used to hard-code
    ``measured=True`` and take ``below_floor`` from E2b ALONE, so an E2a half that
    was never scored (a record carrying no ``e2a.outcome`` — ``main`` then builds
    an ``measured=False`` E2a result) was folded into a MEASURED composite: the
    row read RED and its note asserted a scored shortfall for a half nobody
    measured, where the pre-composite path read BLOCKED (#10610 P2). A composite
    is unmeasured when EITHER half is, and below its floor when either half is —
    the conjunction cannot know more than its weakest operand.
    """
    measured = bool(e2a_result.get("measured")) and bool(
        e2b_result.get("measured"))
    return outcome(
        measured=measured,
        below_floor=bool(e2a_result.get("below_floor"))
        or bool(e2b_result.get("below_floor")),
        passed=(e2a_result["outcome"] == "PASS"
                and e2b_result["outcome"] == "PASS"),
        bar=f"E2a {bars['E2a']['required']} of {bars['E2a']['of']} AND E2b "
            f"fused <= {bars['E2b']['max_fused_share']}",
        value=f"E2a {e2a_result['value']}; E2b {e2b_result['value']}")


def e2a_outcome(e1: dict, bars: dict) -> dict:
    """E2a: value-bearing planted units that STRICTLY survive, bar 13 of 14.

    The denominator comes from the pre-registration's §3.3 rule (a digit in the
    ``verbatim_anchor``, minus two named exclusions), computed by
    ``e2a_value_units.py`` from the gold and read here — never typed. The bar is
    the record's own ``E2a`` criterion, so a change to either moves both.
    """
    units = e1["e2a_units"] or 0
    survived = e1["e2a_survived"] or 0
    required = bars["E2a"]["required"]
    return outcome(measured=True, below_floor=units == 0,
                   passed=survived >= required,
                   bar=f"{required} of {bars['E2a']['of']}",
                   value=f"value_units {survived}/{units} survived")


def e3_unmeasured_legs(per_label: dict[str, dict[str, int]] | None, *,
                       labels: tuple[str, ...] = DERIVED_LABELS) -> tuple[str, ...]:
    """The registered denominator classes the measured graph carries **zero** nodes of (#9736).

    E3's row is scored by SUMMING its classes (``census()`` → ``derived_total``), so a registered
    class with no nodes contributes ``0/0`` and disappears from BOTH halves of the ratio. The row
    then reports ``share = 1.000`` — a PASS — over a population NARROWER than the one it registered,
    which is a false green in the favourable direction: the exact failure ``= 1.000 (zero
    unanchored)`` cannot distinguish from a real one.

    That is the production shape, not a corner: ``:Document`` is a RETIRED label (the writer remaps
    it onto ``:Source`` under D10 — the ``entity_label = "Source"`` remap in
    ``tortoise/projection/edges.py``, cited by SYMBOL because a line number here would re-stale on
    the next insertion above it), and §10.1 of the
    pre-registration records that the run IT re-derives wrote none at all (the scored log agrees:
    ``e3 label=Document nodes=0``). So the leg E3 names is
    structurally empty on every post-D10 graph, and only #9736's owner-gated ruling can change
    WHICH spelling the row names.

    ⛔ Which classes are "registered" is ``DERIVED_LABELS`` (re-derived from the writer's
    ``_DERIVATION_REFERENCES_LABELS``) and NOT the wider instrument census: ``:Object`` is left
    unanchored BY DESIGN (#5199 option A) and #10525 dropped it from the row, so counting it as a
    missing leg would re-red E3 for a recorded decision.

    An ABSENT block is empty too — a census that never enumerated a registered class did not
    measure it, and "absent" must not read as "fine".
    """
    blocks = per_label or {}
    empty: list[str] = []
    for label in labels:
        block = blocks.get(label)
        nodes = block.get("nodes") if isinstance(block, dict) else None
        if not nodes:
            empty.append(label)
    return tuple(empty)


def e3_legs_line(per_label: dict[str, dict[str, int]] | None) -> str:
    """The log's ``e3 unmeasured_legs=`` line, DERIVED from the census it is printed beside.

    ⛔ Derived from ``per_label``, never from a caller's ``e3``/``e3_out`` dict. The first cut read
    the row's ``unmeasured_legs`` key, so a caller that built the row by hand (this bundle's own
    render fixture did exactly that, over a canned census whose ``Document`` leg is 0) rendered
    ``e3 unmeasured_legs=none — every registered class carried nodes`` two lines below
    ``e3 label=Document nodes=0``. A positive measurement claim asserted from an ABSENT KEY is the
    same defect this line exists to disclose, one layer up.
    """
    legs = e3_unmeasured_legs(per_label)
    if legs:
        return ("e3 unmeasured_legs=" + ",".join(legs)
                + " — a registered class the run wrote ZERO of; its `0/0` cannot "
                  "satisfy '= 1.000', so the measured denominator is narrower than "
                  "the registered one and E3 cannot PASS on it (#9736)")
    return ("e3 unmeasured_legs=none — every registered class the census "
            "enumerates carried nodes, so the measured denominator IS the "
            "registered one")


def e3_outcome(*, anchored: int, derived: int, bars: dict,
               unmeasured_legs: tuple[str, ...] = ()) -> dict:
    """E3: derived nodes carrying a version anchor, floor 30 derived nodes.

    ``unmeasured_legs`` is the ruling-free half of #9736 (``NOT-A-DECISION(9736-gate-empty-leg)``):
    a registered class the run measured **zero** nodes of cannot SATISFY the ``= 1.000`` bar by
    arithmetic. The bar is a claim about the REGISTERED class set, so a row whose population is a
    strict subset of it has not been measured, however clean the subset looks.

    The rule is deliberately narrow and moves NO recorded number:

    * a row that already fails **stays** FAIL and the empty leg is disclosed in the bar — no
      softening, and no new red on a reading that was already red;
    * a row whose every registered class carried nodes is scored exactly as before;
    * the node floor still wins where it applies (``derived < floor`` is REFUSED, not a verdict);
    * the class set, the threshold and ``:Object``'s exclusion are all UNTOUCHED — choosing
      ``Document`` vs ``Source``-as-document is #9736's owner-gated ruling, and this function
      refuses to certify EITHER spelling when the run wrote none of it.
    """
    floor = bars["floors"]["e3_derived_nodes"]
    share = (anchored / derived) if derived else 0.0
    legs = tuple(unmeasured_legs)
    share_ok = share >= bars["E3"]["required_share"]
    bar = (f"share = {bars['E3']['required_share']} (zero unanchored), floor "
           f"{floor} derived nodes")
    if legs:
        bar += (f"; ⛔ empty registered class leg(s) {', '.join(legs)} — the "
                f"measured denominator is NARROWER than the registered one, so "
                f"this row cannot pass by arithmetic (#9736)")
    result = outcome(measured=True, below_floor=derived < floor,
                     passed=share_ok and not legs, bar=bar,
                     value=f"anchored={anchored} derived={derived} "
                           f"share={share:.6f}")
    result["unmeasured_legs"] = legs
    return result


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


# ⛔ THE `e1-e2.log` PARSE PATH IS GONE, not merely unused (#9889 item 5,
# #10610 P3). `parse_e1e2_log` / `corpus_sha_of_log` / `line_value` survived the
# re-point with no production caller — the rows cite `E1_E2_SCORES`, and the
# README states the parse path `is deleted from probes/capstone_measure.py` — so
# the file was keeping a READER for an artifact no row may cite, which reads as
# "this log is still scored" to the next lane that greps for it. The record is
# the one source; a log reader here would be a second, quieter one.


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


def e1_pass_objects(e1: dict) -> int | None:
    """E1's row numerator: the Objects that are NOT bare-definite.

    ⛔ The table's numerator is the count that PASSED, on every other row (E1b's
    real facts, E2a's surviving units, E3's anchored nodes, E5's recovered facts).
    E1's criterion is written over the OPPOSITE quantity — the bare-definite
    `:Object` share it MINIMISES — so carrying that count in the slot would put a
    row whose best possible outcome is `0 of 61` in front of V5, which refuses a
    GREEN row with a zero numerator as the silent-drop shape ("a green row must
    measure at least ONE success"). Refusing the PERFECT outcome is the same
    green-row-cannot-be-written defect this row was repaired for, one boundary
    earlier. The complement keeps the table's polarity and the population (all
    `:Object` nodes written by the after arm, floored at 30), and leaves the
    measured bare-definite count where it belongs: in the note and in the artifact
    the row cites.

    ``None`` when the arm is unmeasured — an empty slot, which V5 refuses on a
    GREEN row and V6 covers with the blocker on every other.
    """
    numerator = e1.get("after_numerator")
    denominator = e1.get("after_objects")
    if not isinstance(numerator, int) or not isinstance(denominator, int):
        return None
    return denominator - numerator


def e1_row_note(e1: dict, e1_result: dict, bars: dict) -> str:
    """E1's row note — the narration BRANCHES on the state the row is in.

    The refusal sentence used to be unconditional (#10610 review issue 3): "the
    pairings do not agree, so the row is REFUSED" rendered on every scored log,
    so the first run where E1 became DECIDABLE would have written "… and the
    pairings do not agree, so the row is PASS — not RED and not GREEN" into the
    very log that is the evidence. A sentence no input can change is a decision
    replaced by a constant, and the repo already holds a pin for this class (the
    E3 narration defect, `#10525`); this is the same branch for E1, and
    ``test_the_e1_note_narrates_the_state_the_row_is_in`` is its pin.

    The two refusals are also kept DISTINCT here, not merged into "REFUSED": an
    after arm under the frozen floor is a population finding, and a disagreement
    between before realizations is an arm-count finding. Reporting the first as
    the second is #10610 review issue 2.
    """
    pass_objects = e1_pass_objects(e1)
    floor = bars["floors"]["e1_objects"]
    if e1.get("below_floor"):
        state = (
            f"The AFTER arm carries {e1['after_objects']} Objects, under the "
            f"frozen floor of {floor} — a population that small cannot decide "
            f"the bar at any n, so the row is {e1_result['outcome']} (neither a "
            f"pass nor a fail), not RED and not GREEN")
    elif not e1["decidable"]:
        state = (
            f"The two before realizations are {e1['before_spread_pp']} pp apart "
            f"and the pairings do not agree, so the row is "
            f"{e1_result['outcome']} — not RED and not GREEN: at n=1 per arm the "
            f"verdict would depend on which before run is chosen")
    else:
        state = (
            f"Every before realization agrees on the pairing, so the row is "
            f"{e1_result['outcome']} on the bar")
    return (
        f"E1 is scored from {E1_E2_SCORES} (one invocation over the #9889 "
        f"probes), whose corpus IS the frozen one ({e1['corpus_sha256']}) because "
        f"e1_paired_arm.py refuses any other. {e1_result['value']} vs bar "
        f"{e1_result['bar']}. {state}. The deterministic mechanism check "
        f"(e1_gate_drops) reports value_gate drops {e1['gate_drops_total']} of "
        f"{e1['gate_drops_objects']} Objects with its positive controls alive, so "
        f"the selection pass is not the mover — the removal/relocation ruling is "
        f"(#5333). n >= 2 per arm is the next requirement. The row's count is "
        f"that population — Objects that are NOT bare-definite over all Objects = "
        f"{pass_objects}/{e1['after_objects']} — i.e. the PASS-direction half of a "
        f"share the criterion MINIMISES (the measured bare-definite count is "
        f"{e1['after_numerator']} of {e1['after_objects']}); E2 carries the "
        f"planted-unit counts, over the frozen 14")


def e2_row_note(e1: dict, e2a_result: dict, e2b_result: dict,
                e2_result: dict) -> str:
    """E2's row note — and `E2a falls short of …` only where it DID fall short.

    The clause was hard-coded (#10610 review issue 3), so the 13-of-14 run this
    record exists to unbury — the one where E2's row finally reads GREEN — would
    have printed "E2a falls short of 13 of 14 on the run's own graph" beside its
    own GREEN verdict. The row and the artifact it cites disagreeing, inside the
    log that IS the evidence, is the class `#10525`'s narration pin closed for E3.

    ⛔ …and "falls short" is a SCORED finding, so it may not be written over a half
    that was never measured. `main` builds an ``measured=False`` E2a result when
    the record carries no ``e2a.outcome``; an unmeasured outcome is not ``PASS``,
    so the old clause asserted a shortfall for a half nobody scored — beside a
    composite that (correctly) reads BLOCKED (#10610 P2).
    """
    return (
        f"E2a: {e2a_result['outcome']} — {e2a_result['value']} vs bar "
        f"{e2a_result['bar']} (denominator = pre-registration §3.3's "
        f"value-bearing planted units, read from "
        f"docs/evidence/9889/e2a-value-units.json, and PROVEN live by the "
        f"anchor-injection falsifier reaching "
        f"{e1['e2a_falsifier_survived']}/{e1['e2a_units']} over the same "
        f"population). E2b: "
        f"{e2b_result['outcome']} — {e2b_result['value']} vs bar "
        f"{e2b_result['bar']}. The E2 row is "
        f"{verdict_word(e2_result)} on the COMPOSITE "
        f"({e2_result['bar']}): "
        + (f"E2a was NOT measured on this record (no `e2a.outcome`), so the "
           f"composite is {verdict_word(e2_result)} rather than a scored "
           f"shortfall; the {e1['e2a_required']}-of-{e1['e2a_units']} bar is "
           f"neither met nor missed"
           if not e2a_result["measured"] else
           f"E2a falls short of {e1['e2a_required']} of {e1['e2a_units']} on "
           f"the run's own graph"
           if e2a_result["outcome"] != "PASS" else
           f"E2a meets the {e1['e2a_required']}-of-{e1['e2a_units']} bar on the "
           f"run's own graph")
        + f", and E2b's fused count is a proxy over atomic POINTS "
        f"rather than surviving planted units. #10360 records that "
        f"nothing enforces retention (the render half closed, #9775). "
        f"The row's count slots carry the E2a count over the FROZEN "
        f"denominator ({e1['e2a_survived']}/{e1['e2a_units']}), so a "
        f"green row here states the number the bar is written over "
        f"instead of leaving it empty")


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

    ⛔ E4 is the row where the two are the SAME, so it declares nothing: since the
    selector landed, ``build_e4_row`` REFUSES a receipt whose ``corpus_hash`` is not
    the frozen pin, so ``e4["corpus_sha256"]`` is this run's corpus by construction.
    It carried ``artifact_corpus_sha256`` unconditionally anyway — a declaration that
    told a machine consumer "a different revision" about the one row whose own note
    says the opposite, and that V7 could never verify, because its same-corpus arm
    short-circuits before it reads the field.
    """
    def row(criterion: str, *, verdict: str, evidence: str, sha: str | None,
            numerator: int | None, denominator: int | None,
            corpus_sha256: str | None, blocked_by: str | None, note: str,
            artifact_corpus_sha256: str | None = None,
            sibling: str | None = None) -> dict:
        # GREEN and BLOCKED are mutually exclusive in the gate (V5 refuses a green
        # row that carries a blocker; V6 requires one on every non-green row), so
        # the pair is resolved ONCE, here. Without this a row that is correctly
        # measured GREEN arrives still carrying the issue it was blocked on and is
        # refused for it — the row for the criterion a record unblocks (E2 at
        # 13 of 14) could never be written green at all.
        if verdict == "GREEN":
            blocked_by = None
        built = {
            "criterion": criterion, "verdict": verdict,
            "evidence_kind": "measurement", "evidence": evidence, "sha": sha,
            "numerator": numerator, "denominator": denominator,
            "corpus_sha256": corpus_sha256,
            "blocked_by": blocked_by, "note": note,
        }
        # `sibling` is emitted only where a row really has one: V8 reads a pair as
        # two copies of ONE population, and a `"sibling": null` on every row reads
        # as "the pair is there, the numbers are missing" rather than "no pair".
        if sibling is not None:
            built["sibling"] = sibling
        if artifact_corpus_sha256 is not None:
            built["artifact_corpus_sha256"] = artifact_corpus_sha256
        return built

    # The docstring's rule, applied to E4's operands rather than hard-coded to
    # "no declaration": on the real path this is False (`build_e4_row` refused an
    # off-pin receipt before we got here), and an `e4` handed in from another
    # revision still declares itself.
    #
    # ⛔ A NAMED REFUSAL FOR BOTH KEYS, AND BEFORE EITHER IS SUBSCRIPTED. The guard
    # below was added for `evidence` alone and sat AFTER this line, so a caller
    # omitting `corpus_sha256` still got the bare ``KeyError`` the guard exists to
    # eliminate — from the line above it. Both are checked here, in one place, ahead
    # of every subscript.
    missing = [key for key in ("evidence", "corpus_sha256") if key not in e4]
    if missing:
        raise SystemExit(
            "capstone-measure: REFUSED — build_rows was handed an e4 mapping with no "
            + " and no ".join(f"`{key}`" for key in missing)
            + ". Defaulting `evidence` to E4_RECEIPT would cite the pre-#8503 "
            "artifact beside this run's numbers, and a missing `corpus_sha256` would "
            "raise a bare KeyError from the comparison below — so the mapping is "
            "refused by name rather than left to either")

    # ⛔ `_same_corpus` coerces both operands itself, so this line passes them RAW on
    # purpose: a non-string `corpus_sha256` must yield a ROW the gate can report (V7
    # reds a non-string declaration), never an `AttributeError` from inside the
    # comparison. `build_e4_row` cannot produce one — this is the synthetic-caller
    # path — but the predicate's job is a verdict, not a traceback.
    e4_off_this_corpus = not _same_corpus(e4["corpus_sha256"], log_corpus_sha)

    return [
        # E1 and E2 carry the counts their OWN criteria are written over — E2 the
        # E2a count over the FROZEN 14 value-bearing units (8/14, bar >= 13 of 14),
        # E1 the after arm's Objects that are NOT bare-definite over all its
        # Objects (48/61 on the #9889 record, whose bare-definite count is 13).
        #
        # ⛔ They are NOT a sibling pair and name no `sibling`. V8 requires a pair
        # to carry the SAME numerator/denominator, which is right for two readings
        # of ONE population; these two read two populations (all Objects vs the
        # 14 planted value-bearing units) that no edit can reconcile. Naming them
        # siblings forced BOTH counts to None — and an empty count on a GREEN row
        # is exactly what V5 refuses, so the row for the criterion this record
        # unburies (E2a >= 13 of 14) could not have been written green. The
        # populations are stated per row instead: in the note, and in the
        # numerator/denominator themselves.
        row("E1", verdict=e1["verdict"], evidence=E1_E2_SCORES, sha=e1["sha"],
            numerator=e1["numerator"], denominator=e1["denominator"],
            corpus_sha256=e1["corpus_sha256"],
            blocked_by="#5333", note=e1["note"]),
        row("E2", verdict=e2["verdict"], evidence=E1_E2_SCORES, sha=e2["sha"],
            numerator=e2["numerator"], denominator=e2["denominator"],
            corpus_sha256=e2["corpus_sha256"],
            blocked_by="#10360", note=e2["note"]),
        row("E3", verdict=e3["verdict"], evidence=out_path, sha=head,
            numerator=e3["numerator"], denominator=e3["denominator"],
            corpus_sha256=log_corpus_sha, blocked_by="#9736", note=e3["note"]),
        row("E4", verdict=e4["verdict"], evidence=e4["evidence"], sha=e4["sha"],
            numerator=e4["numerator"], denominator=e4["denominator"],
            corpus_sha256=e4["corpus_sha256"],
            artifact_corpus_sha256=(e4["corpus_sha256"] if e4_off_this_corpus
                                    else None),
            # `#9596` is the row's LIVE BLOCKER POINTER on a red row — NOT the owner
            # of the unformed edges. It is the only OPEN issue on E4 and it owns a
            # DIFFERENT gap (the receipt's unmeasured retrievability rate); the
            # planted-edge residual is UNOWNED, which `build_e4_row`'s note states and
            # `#8503`'s own body says in its own words. `#8503` is not the pointer
            # because it CLOSED 2026-10-24 — that closure records the DISPROOF, so it
            # is this row's measurement record — and `#10601`'s V12 REFUSES a row for
            # naming a blocker recorded CLOSED in `blocker-states.json`.
            #
            # ⛔ The literal is unconditional HERE because the GREEN invariant is
            # enforced one layer down, in `row()`: "GREEN carries a blocker … green and
            # blocked are mutually exclusive" (V5) is resolved once for all five rows,
            # before the dict is built. Emitting the conditional here as well was a
            # narrower duplicate of that rule, and it made the cell unreadable to
            # `test_the_shipped_logs_blocker_cells_are_the_emitters_own`, which reads
            # the five `blocked_by="#NNNN"` literals in ROW ORDER to hold the shipped
            # log's cells to what this probe emits.
            blocked_by="#9596",
            note=e4["note"]),
        row("E5", verdict=e5["verdict"], evidence=E5_RESCORE, sha=head,
            numerator=e5["numerator"], denominator=e5["denominator"],
            corpus_sha256=log_corpus_sha, blocked_by="#5087", note=e5["note"]),
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
    # ⛔ `A`-rows score on the SAME corpus the `E`-rows pin (`#10982` review, F3). §3.7
    # registers ONE corpus for both row families — "the record's corpus pin is a single
    # `fixtures_hash` and two corpora would make the two row families incomparable on one
    # run" — and the record's own `a_corpus.pinned_by` says a different corpus is REFUSED by
    # G1. Nothing read `a_corpus` at all, so a record whose `a_corpus.path` pointed elsewhere
    # still passed G1 and the claim was prose. It is checked here, before any model call.
    a_corpus = (prereg.get("a_corpus") or {}).get("path")
    corpus = (prereg.get("corpus") or {}).get("path")
    if a_corpus is not None and a_corpus != corpus:
        raise SystemExit(
            f"capstone-measure: REFUSED — the pre-registration's a_corpus.path is "
            f"{a_corpus!r} but corpus.path is {corpus!r}. §3.7 registers ONE corpus for "
            "both row families (a single fixtures_hash), so a second corpus makes the "
            "A-rows and the E-rows incomparable on one run: the run is refused, never "
            "scored")
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
        receipt = sdk._capture_session(turns, session_id=sid)
        stats = receipt.get("stats") or {}
        results.append({
            "session_id": sid,
            "turns": len(turns),
            "ok": bool(receipt.get("ok")),
            "extraction_mode": receipt.get("extraction_mode"),
            "extracted": receipt.get("extracted"),
            "errors": list(receipt.get("errors") or []),
            # The receipt's OTHER two error surfaces, kept verbatim for the same
            # reason: `capstone_run.observe_idle` disqualifies an idle claim by
            # searching errors/diagnostics/warnings, and a pass whose failure text
            # only reached one of the three would read as idle here and as failed
            # there — the two probes grading one fact oppositely (#10215 review P1).
            "diagnostics": list(receipt.get("diagnostics") or []),
            "warnings": list(receipt.get("warnings") or []),
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
        "vet_enabled, 's2': {}, 'union': {}} (tortoise/extractor_v2.py:13766, the "
        "flag; :13770, the early return; :13801, its vet surface), and each VET "
        "pass is gated on the LIST — `if vet_enabled and embed_list:` (:16779), "
        "`if vet_enabled and complete_list:` (:14350) — so "
        "a block that says enabled=true proves the FLAG, never that the pass "
        "ran. A FAILED vet pass leaves the same empty stats (`_run_vet_pass` "
        "returns {} on its fail-open path, :13495-13498)."
    ),
    "classify_later": (
        "The extractor emits exactly this shape when the flag is ON and the "
        "CLASSIFY pass had nothing to work on: `classify_later` is computed at "
        "tortoise/extractor_v2.py:13735 (and set False when its classifier cannot "
        "be constructed, :13760), the `if not edus:` early return emits "
        "{'enabled': classify_later, 's2': {}, 'union': {}, "
        "'restamp_overrides': 0, 'slot_rekeys': 0} (:13797-13800), and each "
        "classify pass is gated on the LIST and then on a NON-EMPTY item set — "
        "`if classify_later and embed_list:` (:14219) around `if items:` "
        "(:14222), `if classify_later and complete_list:` (:14365) around "
        "`if items:` (:14375) — so a block that says enabled=true proves the "
        "FLAG, never that the pass ran. A FAILED classify pass leaves the same "
        "empty stats (its fail-open `except` at :14235 / :14386)."
    ),
}


def _pass_ran(block: dict) -> bool | None:
    """Does this block SAY its pass ran? ``True`` / ``False`` / ``None`` (silent).

    ⛔ A surface EXISTING is not evidence that the pass RAN, and this is exactly
    where the two were confused. ``extract_session_v2`` emits ``result["vet"]``
    and ``result["classify_later"]`` UNCONDITIONALLY — on the flag-OFF arm and on
    the early-return path the block is ``{"enabled": False, "s2": {}, "union":
    {}}`` (:13797-13800, :13801, :14586-14592, :14596-14600) — so an acceptance test of "a non-empty dict"
    reports S2.2/S2.3 WITNESSED on a run measured with the flags OFF, printing
    ``enabled=false, s2={}`` beside the word WITNESSED.

    ⛔ AND THE FLAG ALONE IS NOT ACTIVITY, which is the sharper half and the one
    this rule got wrong first. ``enabled`` is the FLAG value, computed BEFORE the
    pass is reached: ``vet_enabled`` is set at ``extractor_v2.py:13766`` and
    ``edus = _edus_from_conversation(...)`` follows at ``:13769``, whose
    ``if not edus:`` early return (``:13770``) emits literally ``"vet": {"enabled":
    vet_enabled, "s2": {}, "union": {}}`` (``:13801``) — with the flag ON. The
    passes themselves are gated on the LIST, not the flag: ``if vet_enabled and
    embed_list:`` (``:16779``) and ``if vet_enabled and complete_list:``
    (``:14350``). So a session whose conversation produced no list emits
    ``enabled: True`` with empty counters and no pass ever ran; so does a pass
    that FAILED, because ``_run_vet_pass`` returns ``{}`` stats on its fail-open
    exception path (``extractor_v2.py:13495-13498``). ``enabled: true`` therefore
    proves the flag was read, never that the pass did anything.

    ⛔ The VET lines above are the VET pass's, not the rule's: the CLASSIFY pass
    is different code (``classify_later`` at `:13735`, its own early-return
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
                    f"carries a non-empty {'/'.join(_WITNESS_STATS)} AS WELL — "
                    "which is not a refusal on its own since #10215: this same "
                    "shape is what `step_accounting` accounts as a WITNESSED IDLE "
                    "step when the four idle predicates hold (no failure marker, "
                    "a positive candidate count)")
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
    return ", ".join(parts) or json.dumps(block, sort_keys=True, default=str)[:201]


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
        f"expected-to-derive): {figure}. The 'all derived nodes' figure above IS "
        f"the REGISTERED reading, not the PRE-AMENDMENT one: §10.1 re-registered "
        f"E3's denominator to the classes the model anchors, so this figure no "
        f"longer counts ':Object' and is NARROWER than the pre-amendment reading, "
        f"which §9's provenance table states (and §10.1 restates) and this line does not recompute (the census "
        f"that produced the old figure is retained in HISTORY, so it is checkable "
        f"from the commit rather than re-emitted here as if it were this run's). "
        f"That amendment is a "
        f"DISCLOSED revision of a frozen denominator — §10.1 states old and new, "
        f"the authority and the reason — not a silent one, and whether it stands "
        f"is #9736's ruling to give (owner-gated, still open)")


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
        f"never anchor, and the UNAMENDED denominator counted them, which made "
        f"the = 1.000 bar unreachable as registered. §10.1 re-registered E3's "
        f"class set to the classes the model anchors, so the denominator a "
        f"conforming row declares no longer counts ':Object' — a DISCLOSED reopen "
        f"of a frozen denominator, whose ruling is #9736's to give (owner-gated, "
        f"still open), which is why §10.1 reports both readings rather than one — "
        f"this row scores the AMENDED set and says so — and it states the "
        f"COMPOSITION of the {block.get('points', 0)} "
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
        f"#9736 still owns. #9736 holds BOTH the amendment §10.1 makes and "
        f"the reopening of the model, and §10.1 reports BOTH readings — the "
        f"amended denominator and the pre-amendment one — while this row states the "
        f"amended one's composition, so neither is read "
        f"off a denominator whose composition was hidden"
        + _e3_empty_leg_clause(e3_result))


def _e3_empty_leg_clause(e3_result: dict) -> str:
    """The #9736 empty-leg disclosure, when the run measured a registered class of ZERO nodes.

    Empty when every registered class carried nodes, so a fully-measured run's note is byte-for-byte
    what it was before this rule. When a leg IS empty the note says so on the row itself — the one
    place a reader quotes — rather than leaving it in the log's per-label lines, because a
    ``0/0`` leg is invisible in the totals the row prints.
    """
    legs = tuple(e3_result.get("unmeasured_legs") or ())
    if not legs:
        return ""
    return (
        f" ⛔ AND THE POPULATION IS NARROWER THAN THE REGISTRATION: this run "
        f"wrote ZERO nodes of the registered class(es) {', '.join(legs)}, so the "
        f"denominator above is a strict SUBSET of the one §3.1 registers, and "
        f"`= 1.000` over a subset is not the criterion. The empty leg contributes "
        f"0/0: it can neither pass nor fail, so the row may not PASS on it "
        f"(#9736 NOT-A-DECISION(9736-gate-empty-leg); WHICH spelling the document "
        f"class should name stays #9736's owner-gated ruling)")


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

    # ⛔ THE BRACKET MUST SUM TO THE TOTAL IT RENDERS BESIDE (#10525 review P2).
    # ``per_label`` is the WIDER instrument census — it carries ``:Object`` for
    # ``s3_prior_ablation`` (see ``CENSUS_LABELS``) — while ``derived_total`` is E3's row
    # (``Point`` + the link-anchored classes). Rendering every block beside the narrower
    # total put two irreconcilable numbers on ONE line: the shipped log's own S5 line is
    # ``433 … [Point 338, Object 50, Event 45]`` (``capstone-measured-e0fc940b.log:26``),
    # so the same graph would now print ``383 … [Document 0, Event 45, Object 50,
    # Point 338]`` — parts summing to 433 beside a total of 383, in the bundle whose
    # subject is that a number must be carried by what backs it. The narrowing had been
    # applied to the denominator and not to the WITNESS that renders next to it.
    # The classes the census carries but E3's row does not are therefore named APART
    # from the total rather than dropped: a reader not told ``:Object`` is excluded reads
    # its absence from the bracket as "the graph held none".
    census_per_label = census_data.get("per_label") or {}
    e3_census = [(label, census_per_label[label]) for label in DERIVED_LABELS
                 if label in census_per_label]
    outside_census = [(label, block) for label, block in census_per_label.items()
                      if label not in DERIVED_LABELS]
    if census_data.get("derived_total"):
        per_label = ", ".join(
            f"{label} {block['nodes']} ({block['anchored']} anchored)"
            for label, block in e3_census)
        outside = ", ".join(
            f"{label} {block['nodes']} ({block['anchored']} anchored)"
            for label, block in outside_census)
        outside_note = (
            f"; outside E3's row — counted by this census, NOT by the total above, "
            f"because their links carry no sourceVersion BY DESIGN (#5199 option A): "
            f"[{outside}]" if outside else "")
        operators = ", ".join(f"{name}={count}" for name, count in
                              sorted(census_data["operator_relationships"].items()))
        observed["S5"] = (
            f"the graph write landed: {census_data['derived_total']} derived "
            f"node(s) written by the run [{per_label}]{outside_note}; "
            f"stats.operator_endpoints_minted={endpoints}; operator-shaped "
            f"relationships written [{operators or 'none'}]")
    else:
        outside_note = (
            f" (the census did carry {', '.join(label for label, _ in outside_census)}, "
            f"which E3's row does not count)" if outside_census else "")
        unobserved["S5"] = (f"the run's own graph carries no derived node "
                            f"({'/'.join(DERIVED_LABELS)}, E3's classes) — nothing was "
                            f"written{outside_note}")

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


# ── the conditional step, ACCOUNTED (#10215) ──────────────────────────────

#: ``capstone_run`` resolved once, so two calls cannot disagree about the rules.
_SIBLING_CACHE: dict[str, object] = {}


def _sibling(name: str):
    """The sibling probe, loaded from THIS file's own directory.

    The conditional-step rule has ONE implementation — ``capstone_run``'s
    ``declared_conditional`` (which steps the FROZEN record makes conditional) and
    ``observe_idle`` (the four predicates that separate an idle pass from a crashed
    one). A second copy here is not a style question: it is how the two probes came
    to grade one fact oppositely. ``capstone_run``'s G5 accepted a classify pass
    witnessed idle from #10215, while THIS probe emitted neither ``steps_required``
    nor ``steps_conditional`` at all — so V5 read every log it produces as the
    pre-#10215 "all eight required" shape and refused a GREEN row for S2.3 on the
    very path that produces E1–E5's numbers. That is #10215's own symptom, and the
    file that still had it was this one (review P1).

    Loaded by PATH, never through ``sys.path``: this file is run as a script, exec'd
    from ``tests/test_7949_capstone_measure.py`` and started from other cwds, and an
    import that resolved in only one of those would fail OPEN (no split emitted)
    instead of loudly.
    """
    cached = _SIBLING_CACHE.get(name)
    if cached is not None:
        return cached
    path = Path(__file__).resolve().parent / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"capstone_sibling_{name}", path)
    if spec is None or spec.loader is None:          # pragma: no cover — defensive
        raise SystemExit(f"capstone-measure: REFUSED — cannot load the sibling "
                         f"probe {path}; the step accounting has ONE implementation "
                         "and it is not in this file")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _SIBLING_CACHE[name] = module
    return module


def read_conditional(prereg: dict | None) -> dict[str, dict]:
    """The steps the FROZEN record declares CONDITIONAL, or REFUSE the run.

    Read from the pre-registration COMMIT (``read_preregistration``), never the
    worktree, so conditionality is a reviewed edit to a hash-pinned artefact rather
    than a lane's local choice.

    A MALFORMED declaration refuses, for ``capstone_run.G5``'s own reason: degrading
    to "all eight required" looks exactly like correct behaviour, so a broken
    declaration must stop the run rather than quietly change what the table means.
    """
    conditional, why = _sibling("capstone_run").declared_conditional(prereg)
    if why:
        raise SystemExit(
            f"capstone-measure: REFUSED — the pre-registration's step declaration "
            f"is unreadable: {why} — the split between unconditional and conditional "
            "steps cannot be told, and a malformed declaration must not silently "
            "mean 'all eight required'")
    return conditional


def _idle_view(run: dict) -> dict:
    """One measured session, as ``capstone_run.observe_idle`` reads a receipt.

    ``observe_idle`` reads the extractor's stage keys, ``stats`` (and the
    ``step_witness`` sub-key the #9116 seam copies onto it) and the receipt's own
    error surfaces. This probe holds each stage under ``surfaces`` and keeps
    ``errors``/``diagnostics``/``warnings`` verbatim, so the view RE-HOMES what the
    run already carries — it derives no fact of its own, which is what lets the two
    probes share one rule.
    """
    view: dict = {
        "stats": run.get("stats") or {},
        "errors": list(run.get("errors") or []),
        "diagnostics": list(run.get("diagnostics") or []),
        "warnings": list(run.get("warnings") or []),
    }
    for key, block in (run.get("surfaces") or {}).items():
        if block:
            view[key] = block
    return view


def _idle_witness(runs: list[dict], step: str) -> tuple[str, str]:
    """A RUN-WIDE idle basis for a conditional step, or ``("", why not)``.

    One session's idle block is not a run-wide claim, and one session's ACTIVITY
    contradicts it — so the step is accounted idle only when EVERY session that
    carries its surface meets the idle predicates, and at least one does. The
    predicates are ``capstone_run.observe_idle``'s, called on the receipt-shaped
    view, so the two probes cannot grade one fact oppositely.
    """
    surface = _sibling("capstone_run")._SURFACE_FOR_STEP.get(step)
    if not surface:
        return "", (f"the sibling probe has no idle witness for {step!r}, so no "
                     "receipt surface can account it")
    observe_idle = _sibling("capstone_run").observe_idle
    carried = 0
    idle_basis: str | None = None
    not_idle: list[str] = []
    for run in runs:
        view = _idle_view(run)
        # A session that carries NO surface for this step is the #9116 instrument
        # gap for that session, not an idle pass — and not a contradiction either.
        if not view.get(surface) and not (
                ((view.get("stats") or {}).get(STEP_WITNESS_KEY) or {})
                .get(surface)):
            continue
        carried += 1
        idle = observe_idle(view)
        if step in idle and idle_basis is None:
            idle_basis = idle[step]
        elif step not in idle:
            not_idle.append(str(run.get("session_id")))
    if not carried:
        return "", (f"no measured session carries a {surface!r} surface, which is the "
                     "#9116 INSTRUMENT gap and not an idle pass")
    if not_idle or idle_basis is None:
        return "", (f"{len(not_idle) or carried} of the {carried} session(s) carrying a "
                     f"{surface!r} surface do not meet the idle predicates "
                     f"({', '.join(not_idle) if not_idle else '—'}: flag off, a "
                     "fail-open marker, or no usable candidate count), so the run as a "
                     "whole is not witnessed idle")
    return (f"every one of the {carried} measured session(s) that carry the "
            f"{surface!r} surface reads ENABLED with no activity and no fail-open "
            f"marker — {idle_basis}"), ""


def step_accounting(conditional: dict[str, dict], runs: list[dict],
                    observed: dict[str, str]) -> tuple[list[str], list[dict], str]:
    """The ``run`` block's step split, from the RECORD and the RUN (#10215).

    Returns ``(steps_required, steps_conditional, why_not)``. The split comes from
    the frozen record, exactly as ``capstone_run``'s G5 takes it, so a log from this
    probe is judged by the same rule as a log from the run probe — V5's
    ``_step_accounting`` reads these two fields and nothing else.

    ``why_not`` is non-empty when a conditional step is neither observed nor
    witnessed idle. The log then states the pre-#10215 shape — all eight required —
    instead of emitting conditionality it cannot account for. Both directions of
    that fallback are STRICTER: V5 then refuses the row for the fact that is
    actually true ("steps not observed in the run: S2.3", which is what it said
    before this change) rather than for a partition error this log would be
    inventing by declaring a conditional step and leaving it unaccounted.
    """
    if not conditional:
        return list(STEPS), [], ""
    required = [step for step in STEPS if step not in conditional]
    accounts: list[dict] = []
    unaccounted: list[str] = []
    for step in STEPS:
        if step not in conditional:
            continue
        if step in observed:
            accounts.append({"step": step, "state": "observed",
                             "basis": observed[step]})
            continue
        basis, why = _idle_witness(runs, step)
        if basis:
            accounts.append({"step": step, "state": "idle", "basis": basis})
        else:
            unaccounted.append(f"{step} — {why}")
    if unaccounted:
        return list(STEPS), [], (
            "conditional step(s) neither observed nor witnessed idle: "
            + "; ".join(unaccounted))
    return required, accounts, ""


def step_accounting_lines(required: list[str], accounts: list[dict],
                          steps_gap: str) -> list[str]:
    """The human log's step SPLIT — the half V5 reads, printed beside the count.

    Seven unconditional steps are REQUIRED and a conditional one is ACCOUNTED
    (observed, or witnessed idle): a different claim from "eight observed", and a
    reader who is handed only the count cannot tell the two apart. A separate
    function so the rendering arm is testable without building a whole log — an
    untested print is how a claim stops being shown at all.
    """
    lines = ["steps required: " + (" ".join(required) if required else "(none)")
             + "  |  conditional: "
             + (", ".join(f"{entry['step']}={entry['state']}"
                           for entry in accounts or []) or "none")]
    for entry in accounts or []:
        lines.append(f"conditional {entry['step']}: {entry['state']} — {entry['basis']}")
    if steps_gap:
        lines.append(
            f"steps accounting: NOT CLAIMED — {steps_gap}; the table therefore states "
            "the pre-#10215 shape (all eight required), so the gate refuses the fact "
            "that is true rather than a conditionality nothing accounts for")
    return lines


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
    for label in CENSUS_LABELS:
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

    # E3's totals are summed over the E3 ROW's classes ONLY. ``per_label`` is the wider,
    # instrument-wide census (it carries ``:Object`` for ``s3_prior_ablation``), so summing
    # every block here would put the mention class straight back into E3's denominator and
    # report ``148/433`` beside a registration that names ``148/383`` — the exact defect
    # #9736 exists to remove, reintroduced from the other side.
    e3_blocks = [per_label[label] for label in DERIVED_LABELS if label in per_label]

    event_stamped = projection.g.query(
        "MATCH (n:Point) WHERE n.eventId IS NOT NULL RETURN count(*)").result_set

    object_names = [str(row[0]) for row in projection.g.query(
        "MATCH (n:Object) RETURN n.name").result_set if row and row[0]]

    return {
        "uri": uri,
        "label_counts": label_counts,
        "per_label": per_label,
        "point_composition": composition,
        "derived_total": sum(block["nodes"] for block in e3_blocks),
        "anchored_total": sum(block["anchored"] for block in e3_blocks),
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


# ── the A-row census (#10982) ─────────────────────────────────────────────
#
# The object-layer rows `#7949` was extended with on 2026-11-04 (`A1`–`A5`). They are REGISTERED
# in the pinned record (`PRE-REGISTRATION.md` §3.7) and REPORTED here; this probe compares none of
# them to a bar, because the bar is the record's and applying it is the gate's job, and no A-row has
# a scored run yet (`#10984`–`#10988` own those). The point of emitting them from the ONE existing
# instrument is that a reported number is produced by a tool and never hand-transcribed — the
# owner's 2026-11-04 `live-v4` table was read by hand on the hosted graph, has no committed artifact
# and is not re-derivable from this repository (`docs/epics/2026-11-05-5087-extractor-v4/01-align.md`).
#
# ⛔ A population this census cannot measure emits ``UNMEASURED(<what would measure it>)``, never a
# zero: a zero is a NUMBER, it reads as a measured absence, and three of these five rows are about
# populations no committed artifact has ever counted.

#: The registered object-layer rows, in the order the record lists them.
A_CRITERIA = ("A1", "A2", "A3", "A4", "A5")

#: ⛔ `A1`'s bar is "at least one edge for EACH relation family the A-corpus's pack declares", and
#: an unpinned set makes that bar unfalsifiable — a scorer could read 1, 2, 9 or 36 (#10982 review
#: cycle 2, finding 6). The set is therefore PINNED to the **labels the WRITER stamps**, and that
#: distinction is the whole point (#10982 review round 2):
#:
#:   ⛔ `decomposesInto` is a DECLARED PREDICATE, never a stored relationship label. The dev pack
#:   declares two hierarchy entries — `Theme -> Epic` and `Epic -> Issue`
#:   (`packs/dev/manifest.yaml`, `hierarchies:`) — and BOTH carry `type: hasPart`; the seam writes the
#:   hierarchy entry's `type` (`tortoise/pack_hierarchy.py`, `_binding`: "the edge this function would
#:   write carries ``decomposesInto``, which ``create_edge`` refuses"), and the operator writers fold
#:   `composedOf`/`decomposesInto`/`contains`/`wraps` onto `hasPart`
#:   (`tortoise/projection/edges.py`, the `op_type`->label map). `hasPart` IS in `create_edge`'s own
#:   predicate set and `decomposesInto` is NOT (`edges.py`, `_VALID_EDGE_PREDICATES`).
#:   ⚠️ Cited by SYMBOL and not by line: this bundle's own citation guard refuses a line number no
#:   rule can verify, and an unverifiable number drifts silently (#10982 review round 3).
#:
#:   So a pin on `decomposesInto` names a label that occurs ZERO times on every graph this repo can
#:   write, which made the registered bar "≥ 1 edge for EACH of the two families" **unsatisfiable** —
#:   `#10984` (C3) would have scored A1 RED forever on a correct product. The pin is the one written
#:   family; the declared spellings that fold onto it are named beside it so a reader can still see
#:   which pack declarations the row is about.
A1_RELATION_FAMILIES = ("hasPart",)

#: The pack-declared predicates that are WRITTEN as the label in `A1_RELATION_FAMILIES` above. This
#: map is documentation, not a measurement: `by_pinned_family` counts `type(r)`, and `type(r)` is
#: never a declared spelling. A future pin that adds a written label must add its declared
#: spellings here in the same reviewed edit.
A1_RELATION_FAMILY_FOLD = {
    "hasPart": ("hasPart", "decomposesInto", "composedOf", "contains", "wraps"),
}

#: The four subject-edge families (`#10987`). Counted PER FAMILY so that one wired family cannot
#: read as "Subjects are populated" — the failure the owner's assertion exists to catch.
SUBJECT_EDGE_FAMILIES = ("aboutSubject", "memberOf", "ownedBy", "managedBy")

#: `A3`'s chain-join definition is over these two relations — the pair the owner's 2026-11-04
#: reading counted, and the pair the registered bar names. `SUPPORTS`/`MITIGATES`/`NEGATE`/
#: `SUPERSEDE` are claim-to-claim edges too; they are **NOT** in this count and this block emits no
#: figure for them, because a wider relation set is a different definition and widening it silently
#: is how a bar stops meaning what it says. Adding them is a #10986 registration question.
CHAIN_RELATIONS = ("IMPL", "NAND")

#: ⚠️ `TAGGED` is emitted WITH this annotation and is **not a target** (`#7949` plan §2 R7): it is
#: written only by `_sync_tags` from an explicit `tags` value and no extraction path sets one, so a
#: zero here says nothing about the run's quality.
TAGGED_NOTE = "no carrier — no extraction path sets a `tags` value (#7949 R7); NOT a target"


def unmeasured(what: str) -> str:
    """The explicit non-number a population that cannot be measured emits."""
    return f"UNMEASURED({what})"


def census_reason(exc: BaseException) -> str:
    """A caught exception rendered for a COMMITTED artefact (`#10982` review, F1).

    ⛔ The log this block travels in is committed to a public repository, and
    ``TORTOISE_DB_URI`` is a credential-bearing string — ``docker://:pw@host:6379/g``,
    and for the hosted ``live-v4`` graph the URI carries host, port, user and
    password (``from_uri`` semantics). An exception raised by a driver or a
    projection routinely echoes the URI it failed to reach, so ``f"{exc}"`` is a
    credential path into ``capstone-measured-<sha>.log``.

    The scrub is the repo's OWN rule, not a second one: ``tortoise.security``
    carries the URI-userinfo pattern ``://[^@\\s]*@ -> ://***@`` and
    ``redact_error`` is its public form (class name + redacted message). It is
    stdlib-only by design, so importing it here costs the probe nothing.
    """
    from tortoise.security import redact_error

    return redact_error(exc)


def _a1_object_edges(projection) -> dict:
    """`object→object` edges BY TYPE and BY ENDPOINT KIND (`A1`)."""
    by_type = [
        {"relation": str(row[0]), "edges": int(row[1] or 0)}
        for row in projection.g.query(
            "MATCH (a:Object)-[r]->(b:Object) RETURN type(r) AS t, count(*) AS n "
            "ORDER BY n DESC, t").result_set
    ]
    by_kind = [
        {"relation": str(row[0]),
         "from_kind": str(row[1]) if row[1] is not None else "NULL",
         "to_kind": str(row[2]) if row[2] is not None else "NULL",
         "edges": int(row[3] or 0)}
        for row in projection.g.query(
            # ⛔ `objectKind`, NOT `kind`: the writer stamps `o.objectKind=coalesce($ok,'other')`
            # (tortoise/projection/entities.py:4997) and the pack declares `fromKind`/`toKind`
            # (packs/dev/manifest.yaml:144-149). Reading `a.kind` returned NULL on every real graph
            # — the "by endpoint kind" half reported as measured while measuring nothing (#10982
            # review, finding 3).
            "MATCH (a:Object)-[r]->(b:Object) RETURN type(r) AS t, a.objectKind AS ak, "
            "b.objectKind AS bk, count(*) AS n ORDER BY n DESC, t, ak, bk").result_set
    ]
    counts = {entry["relation"]: entry["edges"] for entry in by_type}
    # ⛔ A1's registered denominator is *"`:Object` nodes in the scored graph (floor 30)"*, and the
    # floor `a1_objects` is pinned — so the block must CARRY that population. It did not, which
    # left the floor consumed by no field and no arm: a floor nothing can be checked against is a
    # floor that cannot refuse. This is the rule A5's block already states for `a5_points` /
    # `a2_candidates` (#11331 review, P2-4b), applied to the row that was left out of it.
    objects = projection.g.query("MATCH (n:Object) RETURN count(n)").result_set
    objects = int(objects[0][0] or 0) if objects else 0
    return {"by_type": by_type,
            "by_endpoint_kind": by_kind,
            "objects": objects,
            "edges_total": sum(entry["edges"] for entry in by_type),
            # per PINNED family, so the logged numbers map onto the registered bar. The keys are
            # WRITTEN labels (`type(r)`), which is what the two queries above count.
            "by_pinned_family": {name: counts.get(name, 0)
                                 for name in A1_RELATION_FAMILIES},
            # the declared spellings each written family folds, so a reader can map the row onto
            # the pack's own declarations without mistaking one for a stored label.
            "pinned_family_declared_spellings": {
                name: list(A1_RELATION_FAMILY_FOLD.get(name, (name,)))
                for name in A1_RELATION_FAMILIES}}


def _a3_chain_join(projection) -> dict:
    """`A3`'s chain-join rate under the REGISTERED definition, with the cheap one beside it.

    ⛔ The registered definition is *an incident `IMPL`/`NAND` **and** at least one such edge
    crossing a batch boundary (a different `Event` root)*. "Member of a connected component of
    >= 2" is NOT a third definition — any incident edge already makes a component of two — so it
    collapses into the cheap one and is rejected for exactly the reason it looks attractive.

    ⛔ The cross-batch test requires an `eventId` on **BOTH** ends. Comparing
    ``coalesce(eventId,'')`` counted a statement with no `eventId` at all as "crossing a boundary"
    against one that has an id — an unanchored row is not a second batch, and 170 of the committed
    run's 354 Points carry no stamp (`capstone-measured-9152448d.log:63`), so the fail-open was not
    theoretical (#10982 review, finding 5). Where the population carries no stamp at all, the leg
    is ``UNMEASURED`` rather than a number nobody can reach.
    """
    pattern = "|".join(CHAIN_RELATIONS)
    statements = projection.g.query(
        "MATCH (n:Point) WHERE n.pointKind = 'statement' RETURN count(n)"
    ).result_set
    statements = int(statements[0][0] or 0) if statements else 0
    # An incident edge is INCIDENT — the neighbour is any node, not only a `:Point`, because the
    # registered definition says "at least one incident IMPL/NAND edge" and does not restrict the
    # far end (#10982 review, finding 5).
    cheap_rows = projection.g.query(
        f"MATCH (n:Point)-[r:{pattern}]-() "
        "WHERE n.pointKind = 'statement' RETURN count(DISTINCT n)").result_set
    cheap = int(cheap_rows[0][0] or 0) if cheap_rows else 0
    stamped_rows = projection.g.query(
        "MATCH (n:Point) WHERE n.pointKind = 'statement' AND n.eventId IS NOT NULL "
        "RETURN count(n)").result_set
    stamped = int(stamped_rows[0][0] or 0) if stamped_rows else 0
    strict = 0
    if stamped:
        strict_rows = projection.g.query(
            f"MATCH (n:Point)-[r:{pattern}]-(m) WHERE n.pointKind = 'statement' "
            "AND n.eventId IS NOT NULL AND m.eventId IS NOT NULL "
            "AND n.eventId <> m.eventId RETURN count(DISTINCT n)").result_set
        strict = int(strict_rows[0][0] or 0) if strict_rows else 0
    return {
        "statements": statements,
        "statements_with_an_event_root": stamped,
        "cheap_joined": cheap,
        "cheap_rate": (cheap / statements) if statements else unmeasured(
            "a statement population with pointKind='statement'"),
        "cross_batch_joined": strict if stamped else unmeasured(
            "an eventId stamp on the statement population — no statement carries one, so no "
            "'different Event root' can be established"),
        # ⛔ The rate's denominator is the STAMPED subpopulation, not all statements: a statement
        # with no `eventId` can never be counted as crossing, so dividing by every statement would
        # report "0% join chains" as a measurement of stamp coverage (#10982 review cycle 2,
        # finding 7). The coverage is emitted beside it and the row REFUSES below its floor.
        "event_root_coverage": (stamped / statements) if statements else unmeasured(
            "a statement population"),
        "cross_batch_rate": (strict / stamped) if stamped else unmeasured(
            "a statement population carrying an eventId on both ends of an IMPL/NAND edge"),
        "relations": list(CHAIN_RELATIONS),
    }


def _a4_pointkind(projection) -> dict:
    """`A4`'s `pointKind` histogram over CLAIMS, split typed / `statement` / `NULL`.

    ⛔ `NULL` is a THIRD reported state, never folded into `statement`: 19% of the owner's
    population carried no `pointKind` at all, and folding it in reports a typing win nobody made.

    ⛔ Two further folds that would have manufactured that same win (#10982 review, finding 4):
    a **blank** `pointKind` (`''` is what the #548 rebuild SNAPSHOT synthesis writes for an operator
    with no kind — `tortoise/projection/__init__.py:14121-14123`, `# Ensure pointKind is present
    (operators often lack it)` / `clean["pointKind"] = ""`; the live write is
    `projection/entities.py:2898`, which leaves the property ABSENT instead) is ABSENT, not typed;
    and **capture rows** are
    not claims — turn rows carry `pointKind='event'`: the row dict is written with `"k": "event"`
    and lands through the capture path's turn writer `_TURN_WRITE_CYPHER` (`tortoise/sdk.py:2530`)
    inside `_write_capture_turns` — and 159 of
    the committed run's 354 Points are turn rows (`capstone-measured-9152448d.log:73`), so counting
    them read ~45% "typed" against the owner's 0.6% ceiling. The excluded populations are COUNTED
    and reported, never silently dropped.
    """
    # ⛔ TRUTHINESS, not `IS NULL`: `create_point(is_episodic=False)` really writes the property
    # (`projection/entities.py:3036`), so an explicit `false` is a CLAIM — `IS NULL` dropped it
    # from the denominator while the capture count reported it as a turn row it is not. The same
    # predicate the file's own `POINT_COMPOSITION_CYPHER` uses; ⛔ NOT `tortoise/quota.py:1133`,
    # which tests `is_episodic` ALONE and would count an operator Point as a claim (#10982 review
    # round 2).
    capture = projection.g.query(
        "MATCH (n:Point) WHERE coalesce(n.is_episodic, false) OR coalesce(n.is_operator, false) "
        "RETURN count(n)").result_set
    capture = int(capture[0][0] or 0) if capture else 0
    rows = projection.g.query(
        "MATCH (n:Point) WHERE NOT coalesce(n.is_episodic, false) "
        "AND NOT coalesce(n.is_operator, false) "
        "RETURN coalesce(n.pointKind, 'NULL') AS k, count(*) AS n "
        "ORDER BY n DESC").result_set
    by_kind: dict[str, int] = {}
    for row in rows:
        key = str(row[0])
        # a blank kind is the ABSENT state, not a typed one — ACCUMULATED into the NULL bucket,
        # because assigning would overwrite the real NULL row's count instead of adding to it.
        key = "NULL" if key in ("NULL", "") else key
        by_kind[key] = by_kind.get(key, 0) + int(row[1] or 0)
    total = sum(by_kind.values())
    null_count = by_kind.pop("NULL", 0)
    statement = by_kind.pop("statement", 0)
    typed = sum(by_kind.values())
    return {"claim_points": total, "excluded_capture_rows": capture,
            "statement": statement, "null": null_count,
            "typed": typed, "typed_by_kind": dict(sorted(by_kind.items())),
            "typed_share": (typed / total) if total else unmeasured(
                "a claim-point population (is_episodic and is_operator both absent)")}


def _a5_subjects(projection) -> dict:
    """`A5`: the `:Subject` count and the four subject-edge families, PER FAMILY.

    ⛔ Both queries are UNDIRECTED, but only the CARRYING leg is claim-filtered — and that is the
    registered shape, not an oversight: §3.1 A5 pins the four subject-edge families as
    `count(DISTINCT r)` over the graph with no claim filter, so the family count and the
    "statements carrying one" count are NOT required to agree about the same edges, and adding a
    filter to the family query would silently move the pinned denominator. What the direction
    clause buys (#10982 review, finding 7) is that a forwards-only family count beside a
    direction-agnostic point count cannot let a turn row satisfy the registered bar while zero
    statements carried an edge.
    """
    subjects = projection.g.query("MATCH (n:Subject) RETURN count(n)").result_set
    subjects = int(subjects[0][0] or 0) if subjects else 0
    pattern = "|".join(SUBJECT_EDGE_FAMILIES)
    # ⛔ `count(DISTINCT r)`, NOT `count(*)`: an UNDIRECTED multi-rel-type pattern is traversed in
    # both orientations, so `count(*)` reports 2x the edges (measured on falkordb-server:v4.20.6:
    # two `aboutSubject` edges -> count(*)=4, count(DISTINCT r)=2). `tortoise/taxonomy.py:40` already
    # records that these patterns are unreliable across versions (#10982 review cycle 2, finding 1).
    rows = projection.g.query(
        f"MATCH ()-[r:{pattern}]-() RETURN type(r) AS t, count(DISTINCT r) AS n "
        "ORDER BY t").result_set
    families = {str(row[0]): int(row[1] or 0) for row in rows}
    carrying = projection.g.query(
        f"MATCH (n:Point)-[r:{pattern}]-() WHERE n.pointKind = 'statement' "
        "RETURN count(DISTINCT n)").result_set
    carrying = int(carrying[0][0] or 0) if carrying else 0
    # ⛔ A5's registered denominator promises the `:Subject` count *"and the `Point` population
    # reported beside them, never used as the actor denominator"* — so the block must carry the
    # Point population too. It was missing, which left the pinned floors `a5_points` and
    # `a2_candidates` consumed by NO field and no arm: a floor nothing can be checked against is a
    # floor that cannot refuse (#11331 review round 8, P2-4b). The count is a COMPLETE enumeration,
    # so an absent population reads as a measured 0 here — not as the `UNMEASURED` a zero
    # denominator gets, because 0 Points is a real (and REFUSED-by-floor) measurement, not silence.
    point_population = projection.g.query("MATCH (n:Point) RETURN count(n)").result_set
    point_population = int(point_population[0][0] or 0) if point_population else 0
    return {"subjects": subjects,
            # ⛔ A5's BAR divides by the distinct actors the A-corpus NAMES, so the census must carry
            # that population or the bar is not producible from the block (review cycle 2, finding 8).
            # ⛔ NOT a pending landing: `#10983` (C2) CLOSED on 2026-11-07 WITHOUT registering an actor
            # list, and the record now says so (`a_corpus.requires` — "the requirement is on the
            # CORPUS's content, not on a pending landing"). Pointing this gap at that issue would
            # aim a scoring child (`#10984`–`#10988`) at closed work — the stale-pointer shape V12
            # refuses for `blocked_by`, reachable here in prose where nothing reddens.
            "named_actors": unmeasured(
                "the distinct actors the A-corpus names — the corpus carries no registered actor "
                "list yet (#10983 closed without one), so this is a CORPUS-content gap; the "
                "`:Subject` count is NOT it, or the bar would be satisfied by the run's own writes"),
            "point_population": point_population,
            "families": {name: int(families.get(name, 0))
                         for name in SUBJECT_EDGE_FAMILIES},
            "statements_with_a_subject_edge": carrying}


def _tagged_edges(projection) -> int:
    rows = projection.g.query(
        "MATCH ()-[r:TAGGED]->() RETURN count(r)").result_set
    return int(rows[0][0] or 0) if rows else 0


def a_row_census(uri: str) -> dict:
    """The `A1`–`A5` census, over the RUN'S OWN graph, on the ONE existing instrument.

    Every number here is produced by a query; nothing is transcribed. **Each row is measured in its
    own ``try``**, so one broken query costs that row and nothing else — the other rows still report
    their measured numbers, and the failed one carries ``unmeasured(<the error>)``. A whole-block
    failure was fail-closed but it discarded four measured rows for one bad query, which is the
    opposite of what this block is for (#10982 review, finding 11).
    """
    from tortoise.projection import FalkorProjection

    projection = FalkorProjection.from_uri(uri)
    # ⛔ NO `uri` KEY (`#10982` review, F1). This dict is `json.dumps`-ed WHOLE into the
    # committed machine record, and the URI carries the graph's credentials — so the
    # block must not carry it even redacted. The E-census keeps a `uri` internally
    # (`census()`) but the machine record PROJECTS six keys out of it and never emits
    # it; traceability for THIS block is `run.graph`, which the record already states.
    result: dict = {"criteria": list(A_CRITERIA)}

    def row(name: str, fn, empty: dict) -> dict:
        try:
            return fn(projection)
        except Exception as exc:  # the row is named; the rest of the census still reports
            # ⛔ `census_reason`, never `{exc}`: an infra exception can echo the
            # credential-bearing URI, and this block is committed (review F1).
            reason = unmeasured(f"{name}'s queries could not run — {census_reason(exc)}")
            # ⛔ NOT the zero skeleton: a numeric `0` under a key a real measurement also occupies
            # reads as a MEASURED ABSENCE and scores the row RED on an infrastructure failure —
            # the exact number this block exists to not print (#10982 review cycle 2, finding 2).
            block: dict = {"error": reason}
            for key, value in empty.items():
                block[key] = ({name_: reason for name_ in value}
                              if isinstance(value, dict) else reason)
            return block

    # A1 — the object→object graph, plus the rollup half.
    result["A1"] = row("A1", _a1_object_edges,
                       {"by_type": [], "by_endpoint_kind": [], "edges_total": 0,
                        "objects": 0,
                        "by_pinned_family": {name: 0 for name in A1_RELATION_FAMILIES},
                        "pinned_family_declared_spellings": {
                            name: list(A1_RELATION_FAMILY_FOLD.get(name, (name,)))
                            for name in A1_RELATION_FAMILIES}})
    # ⛔ The rollup half of A1 is NOT the edge count: A1's real criterion is a query ANSWERING, and
    # no pack-declared rollup query is registered for this corpus yet (`#10984` owns both). An edge
    # count with no query behind it is the shape this row exists to refuse, so it is named.
    result["A1"]["rollup_queries"] = unmeasured(
        "a pack-declared part-whole rollup query for this corpus — #10984 (C3) owns "
        "registering it and asking it")

    # A2 — attach vs mint. No counter exists anywhere on the write path yet.
    # ⛔ THE BLOCK CARRIES EVERY PART THE REGISTRATION NAMES, not just the two the decision reads.
    # The metric's THIRD arm is `held` — "each held candidate named with its reason" — and the
    # threshold's RED leg is "a run that holds candidates without naming them is **RED**". The bar
    # divides by DECIDED candidates, which the denominator fixes against the all-candidate count:
    # "the all-candidate count is reported beside it, never used as the floor". Emitting only
    # attach/mint left a consumer unable to tell "zero held" from "the held arm was never
    # measured", so the registered RED leg could not be evaluated from the one block §3.7
    # designates as the place a scoring child reads — the silence `a_row_census_lines` refuses
    # everywhere else, and the exact omission the issue's Target forbids (#11331 review round 8).
    _a2_counter = ("an S3 RESOLVE attach/mint/held counter on the run's own receipt — no writer "
                   "stamps one today; #10985 (C4) owns it")
    result["A2"] = {
        "attach": unmeasured(_a2_counter),
        "mint": unmeasured("the same counter's mint arm — #10985 (C4)"),
        "held": unmeasured(
            "the same counter's held arm, each held candidate NAMED with its reason — the "
            "registration makes an unnamed held candidate RED; #10985 (C4)"),
        "candidates": unmeasured(
            "the all-candidate count the run decided over — reported BESIDE the floor, never "
            "used as it; #10985 (C4)"),
        "decided_candidates": unmeasured(
            "the decided-candidate population A2's bar divides by — the population the pinned "
            "floor a2_decided_candidates guards; #10985 (C4)"),
        "attach_share": unmeasured(
            "attach_share = attach / decided_candidates — a zero denominator is REFUSED, never "
            "rendered as 0/0; #10985 (C4)"),
    }

    result["A3"] = row("A3", _a3_chain_join, {"statements": 0,
                                              "statements_with_an_event_root": 0,
                                              "event_root_coverage": unmeasured("a statement population"),
                                              "cheap_joined": 0, "cheap_rate": unmeasured("a statement population"),
                                              "cross_batch_joined": unmeasured("an eventId stamp"),
                                              "cross_batch_rate": unmeasured("an eventId stamp"),
                                              "relations": list(CHAIN_RELATIONS)})
    result["A4"] = row("A4", _a4_pointkind,
                       {"claim_points": 0, "excluded_capture_rows": 0, "statement": 0,
                        "null": 0, "typed": 0, "typed_by_kind": {},
                        "typed_share": unmeasured("a claim-point population")})
    result["A5"] = row("A5", _a5_subjects,
                       {"subjects": 0, "named_actors": 0, "point_population": 0,
                        "families": {name: 0 for name in SUBJECT_EDGE_FAMILIES},
                        "statements_with_a_subject_edge": 0})
    tagged: dict = {"note": TAGGED_NOTE}
    try:
        tagged["edges"] = _tagged_edges(projection)
    except Exception as exc:  # the row is named; the rest of the census still reports
        tagged["edges"] = unmeasured(
            f"TAGGED's query could not run — {census_reason(exc)}")
    result["TAGGED"] = tagged
    return result


def a_row_census_lines(census_a: dict | None) -> list[str]:
    """The rendered `A`-row block. Absent, failed or incomplete census renders a NAMED gap.

    ⛔ A census that RAISED is not a census of zeros (#10982): the failure is named with its own
    exception text, because an ``A1 edges=0`` line produced by a broken query is exactly the number
    this block exists to not print.
    """
    if not census_a:
        return ["a-rows: UNMEASURED(the A-row census did not run in this pass)"]
    if census_a.get("error"):
        return ["a-rows: UNMEASURED(the A-row census could not run — "
                f"{census_a['error']})"]
    if "A1" not in census_a:
        return ["a-rows: UNMEASURED(the A-row census block is incomplete — no A1 entry)"]
    lines: list[str] = []
    add = lines.append
    add("## A-row census — the object layer (#10982; REPORTED, not scored here)")
    add("a-rows: registered in PRE-REGISTRATION.md §3.7; the bar is the record's and the "
        "gate's, and no A-row has a scored run yet (#10984-#10988 own those)")

    a1 = census_a["A1"]
    add(f"a1 objects={a1['objects']} object_object_edges={a1['edges_total']} "
        f"by_type={a1['by_type'] or '[]'} "
        f"by_pinned_family={a1['by_pinned_family']}"
        + (f" error={a1['error']}" if a1.get("error") else ""))
    add(f"a1 by_endpoint_kind={a1['by_endpoint_kind'] or '[]'}")
    # ⛔ The pinned families are WRITTEN labels (`type(r)`); the pack's declared spellings that fold
    # onto them are named beside the counts so no reader takes `decomposesInto` for a stored label
    # (#10982 review round 2).
    add("a1 pinned_family_declared_spellings="
        f"{a1.get('pinned_family_declared_spellings') or '{}'}")
    add(f"a1 rollup_queries={a1['rollup_queries']}")

    a2 = census_a["A2"]
    # ⛔ EVERY part the registration names, in the order the decision reads them: the two arms it
    # compares, the held arm whose silence is RED, the floor population, the count reported beside
    # it, and the rate. A renderer that emits a subset makes the block's contents invisible in the
    # artefact a scorer actually reads (#11331 review round 8, P2-1).
    add(f"a2 attach={a2['attach']} mint={a2['mint']} held={a2['held']} "
        f"candidates={a2['candidates']} decided_candidates={a2['decided_candidates']} "
        f"attach_share={a2['attach_share']}")

    a3 = census_a["A3"]
    add(f"a3 statements={a3['statements']} "
        f"statements_with_an_event_root={a3['statements_with_an_event_root']} "
        f"event_root_coverage={a3['event_root_coverage']} "
        f"cheap_joined={a3['cheap_joined']} cheap_rate={a3['cheap_rate']} "
        f"cross_batch_joined={a3['cross_batch_joined']} "
        f"cross_batch_rate={a3['cross_batch_rate']} relations={a3['relations']}"
        + (f" error={a3['error']}" if a3.get("error") else ""))
    add("a3 note: the CHEAP rate is reported for contrast and is NOT the bar; the registered "
        "definition requires a cross-batch link with an eventId on BOTH ends (§3.7)")

    a4 = census_a["A4"]
    add(f"a4 claim_points={a4['claim_points']} "
        f"excluded_capture_rows={a4['excluded_capture_rows']} "
        f"typed={a4['typed']} statement={a4['statement']} null={a4['null']} "
        f"typed_share={a4['typed_share']} typed_by_kind={a4['typed_by_kind'] or '{}'}"
        + (f" error={a4['error']}" if a4.get("error") else ""))
    add("a4 note: NULL (and a blank kind) is a THIRD state, never folded into statement; turn rows "
        "and operator Points are EXCLUDED and counted, because they are capture rows and not "
        "claims; the bar is UNMEASURED by registration (D3 fork, #10988/#1022)")

    a5 = census_a["A5"]
    add(f"a5 subjects={a5['subjects']} named_actors={a5['named_actors']} "
        f"point_population={a5['point_population']} families={a5['families']} "
        f"statements_with_a_subject_edge={a5['statements_with_a_subject_edge']}"
        + (f" error={a5['error']}" if a5.get("error") else ""))

    tagged = census_a["TAGGED"]
    add(f"a-rows TAGGED_edges={tagged['edges']} note: {tagged['note']}")
    return lines


# ── E4 / E5 / E1+E2 ───────────────────────────────────────────────────────

def _same_corpus(a: object, b: object) -> bool:
    """Two corpus hashes name the same corpus, with or without the ``sha256:`` prefix.

    The record writes ``sha256:<hex>``; a receipt written by a different code path may
    drop the prefix. Comparing the RAW strings made those two spellings unequal, so a
    receipt on the pinned corpus was reported as "not measured on the frozen corpus" —
    a refusal that names a false reason is the one failure mode a refusal must not have.

    ⛔ Both operands are COERCED HERE, not at the call sites. This predicate's contract
    is to RETURN A VERDICT — and a caller handing it a non-string (a synthetic `e4`, a
    caller-supplied pin) must get that verdict, not `AttributeError: 'int' object has
    no attribute 'removeprefix'` raised from inside the check. Doing it in one place is
    also why the sites cannot drift apart again: three of them coerced one operand or
    none, and each shape was a separate review round.
    """
    return (str(a or "").removeprefix("sha256:")
            == str(b or "").removeprefix("sha256:"))


def _instant(raw: object) -> tuple[int, object]:
    """A comparable instant from a receipt's ``date``, or a sentinel that sorts FIRST.

    A STRING compare is not an ordering here: ``…T01:00:00+02:00`` sorts ABOVE
    ``…T00:30:00Z`` lexically while being genuinely EARLIER. The key decides both which
    receipt wins and whether an unscorable one refuses the selection, so a wrong
    compare makes the two disagree — a false refusal one way, a reopened silent skip
    the other. Returns ``(0, "")`` for an ABSENT date, ``(0, <the date as text>)``
    for one that is PRESENT but unreadable, and ``(1, datetime)`` otherwise (so an
    undated record never displaces a dated one); the tag is compared first, so a
    datetime is never ordered against a str.

    ⛔ The two tag-0 shapes are NOT the same fact, and the CALLER must separate them:
    ``(0, "")`` is "no date recorded", which the ordering can state honestly;
    ``(0, <text>)`` is "a date is recorded and it cannot be read", which makes the
    record's position in the order unknowable. Ranking the second as if it were the
    first let an OLDER on-pin receipt publish E4's number while a newer measurement
    sat unread (and silenced the would-have-won refusal when that newer one was also
    unscorable) — so ``select_e4_receipt`` REFUSES it, on the same reasoning it
    refuses an unreadable file: nothing can prove the newest measurement on the
    frozen corpus is not the one whose date could not be parsed.

    The second element of a tag-0 key is therefore ALWAYS a ``str``: an absent date
    contributes ``""`` and a present-but-unreadable one contributes ``repr(raw)`` (a
    non-string such as ``"date": 20261024`` lands here rather than being flattened
    into the absent shape). Both are only ever compared against other strings, so
    ordering is unaffected.
    """
    if raw is None:
        return (0, "")
    if not isinstance(raw, str):
        # ⛔ NEVER PARSE `repr(raw)`. A recorded date that is not a string is
        # UNREADABLE, and the repr of an int is the ISO BASIC form — `repr(20261024)`
        # is `'20261024'`, which `datetime.fromisoformat` ACCEPTS, so parsing here
        # silently ranked a malformed date at midnight UTC instead of refusing it
        # (measured: it did exactly that, while `0` — whose repr fails to parse — was
        # refused; two non-string dates, opposite outcomes, for no stated reason).
        # Tag 0 puts every non-string on the refusal path `select_e4_receipt` already
        # implements for a present-but-unreadable date.
        return (0, repr(raw))
    text = raw
    if not text:
        return (0, "")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return (0, text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return (1, parsed)


def _sort_key(record: dict, name: str) -> tuple:
    """The ONE ordering key, shared by the winner choice and the refusal predicate."""
    return (_instant(record.get("date")), str(record.get("run_id") or ""), name)


def select_e4_receipt(repo: Path = ROOT, frozen_pin: str = "") -> tuple[str, dict]:
    """E4's artifact: the operator receipt measured on the FROZEN corpus.

    Why this is not a filename constant. E4's bar is *"the planted-operator corpus
    goes from 0/4 to 4/4"* — on the **pre-registered corpus**, like every other
    row. A hard-coded path broke that silently: the roll-up cited
    ``w2b-operator-2026-10-19.json``, whose own ``corpus_hash`` is ``943a99ec…``
    — not the frozen ``dd7e8520…`` — and scored E4 at **2/15** from it, while three
    of ``#8503``'s OWN measurement receipts, all on the frozen corpus, read
    **5/15**. So the published row was measured on a different corpus than the one
    the record froze. Nothing observed it: the row carried the mismatching
    ``corpus_hash`` as a disclosure and the gate passes a disclosed mismatch (V7).

    ⛔ NOT "written AFTER ``#8503``'s fix, on code that predates the fix" — there is
    no fix. ``#8503`` CLOSED 2026-10-24 as a **DISPROOF** (*"This issue is that
    residual, now measured post-close. No open issue owns it."*), and these three
    receipts (`w2b-operator-2026-10-24-8503-{preleg,a,b}.json`) are dated
    ``2026-10-24T05:46:51Z`` / ``06:03:47Z`` / ``06:10:47Z`` — 14.66–15.06 h BEFORE
    that issue's ``closedAt`` of ``2026-10-24T20:50:13Z``. They are its measurement
    artifacts, not the output of a repair it never made.

    A filename constant is also an evidence hole: point it at any other receipt and
    the row reports that receipt's number with no signal that the artifact moved.

    Selection is therefore by the receipt's OWN ``corpus_hash``:

    * ``frozen_pin`` must be a real pin — an un-pinned selection is refused;
    * every ``*.json`` in the receipts directory is read (the name is not trusted to
      say which files are receipts), and the one whose hash **matches** the pin wins;
      no match REFUSES rather than falling back to "the newest file";
    * among matches the ordering is by the record's own ``date`` and then ``run_id``,
      and the winner is the last. The date is PARSED where it can be (the writer emits
      a fixed ``…Z`` stamp, but a hand-authored receipt may carry an offset —
      ``…T01:00:00+02:00`` sorts ABOVE ``…T00:30:00Z`` as a STRING while being
      genuinely EARLIER); a receipt whose ``date`` is ABSENT sorts first, so an
      undated record never displaces a dated one. One helper (``_sort_key``) produces
      the key for BOTH the winner choice and the would-have-won refusal below, because
      those two disagreeing is exactly how a silent revert gets in;
    * a receipt ON the pin whose ``date`` is PRESENT but unparseable REFUSES the
      selection. Its position in the order is unknowable, so it can neither be ranked
      (an older receipt would publish while a newer measurement sat unread) nor be
      shown not to be the newest unread one (which resurrects the silent skip this
      selector exists to close). An ABSENT date is different and stays permitted: that
      is a recorded fact, not an unreadable one. The reason is the same one the next
      bullet gives for an unreadable file;
    * a file in the receipts directory that cannot be read as a JSON object REFUSES
      the selection: its corpus and date are unknowable, so nothing can prove the
      newest measurement on the frozen corpus is not it;
    * a candidate that IS on the frozen pin but carries no ``operator_audit`` REFUSES
      **when it would have won** — i.e. when it sorts after the newest scorable
      receipt. Skipping that one would let an OLDER on-pin receipt publish the row's
      number while the newest measurement on the same corpus sat unread, which is the
      same "the row's number is not the artifact you think it is" defect this selector
      exists to close, one level down. One that sorts BEFORE the winner provably cannot
      change the result and is ignored, because a refusal that names a consequence that
      is not true is the one failure mode a refusal must not have;
    * a candidate carrying no ``corpus_hash``, or one measured on another corpus, is
      ignored and NAMED in the no-match refusal, so a missing artifact cannot read as
      "no candidate".
    """
    if not frozen_pin:
        raise SystemExit("capstone-measure: REFUSED — E4 was asked to select its "
                         "receipt without the frozen corpus pin; a receipt whose "
                         "corpus cannot be checked is not evidence for a bar "
                         "written over the pre-registered corpus")
    directory = repo / E4_RECEIPT_DIR
    if not directory.exists():
        raise SystemExit(
            f"capstone-measure: REFUSED — the receipts directory {directory} does "
            "not exist, so E4's artifact cannot be looked for. Check the root this "
            "probe was pointed at; an ABSENT directory is a different failure from "
            "an empty one, and reporting both as 'none present' points the reader "
            "at a missing receipt when the real cause is a missing tree")
    if not directory.is_dir():
        # ⛔ `is_dir()` IS False FOR A PATH THAT EXISTS AS A FILE. Falling into the
        # absent-tree refusal above would then assert "does not exist" about a path
        # that IS there — and a refusal that names a false reason is the one failure
        # mode a refusal must not have (the same rule `_same_corpus` states). The
        # sibling commit that split absent-vs-empty made this exact distinction; this
        # is its third arm, so the reader is never sent to check a root that is fine.
        raise SystemExit(
            f"capstone-measure: REFUSED — {directory} exists but is NOT a "
            "directory, so E4's artifact cannot be looked for. An ABSENT directory "
            "is a different failure from one that is present but unusable; this one "
            "is present, so check what occupies that path rather than the root")
    paths = sorted(directory.glob(E4_RECEIPT_GLOB))
    matched: list[tuple[tuple, Path, dict]] = []
    skipped: list[str] = []
    #: Candidates that ARE on the frozen pin but carry no ``operator_audit``, so they
    #: cannot be scored. Kept with their ORDERING key: one that sorts after the winner
    #: is a REFUSAL (it would have won), one that sorts before it provably cannot affect
    #: the result and is ignored — refusing on those would name a false reason.
    unscorable: list[tuple[tuple, str]] = []
    #: Files in the receipts directory that could not be read as a JSON object. Their
    #: corpus and date are unknowable, so nothing can prove the newest measurement on
    #: the frozen corpus is not one of them — a REFUSAL, never a silent skip.
    unreadable: list[str] = []
    #: Files ON the frozen pin whose ``date`` is present but cannot be parsed. Their
    #: position in the order is unknowable, so they neither rank nor can be shown not
    #: to be the newest unread measurement — a REFUSAL, for the same reason an
    #: unreadable file refuses.
    unorderable: list[str] = []
    for path in paths:
        try:
            record = json.loads(path.read_text())
        except (OSError, ValueError) as exc:
            # JSONDecodeError and UnicodeDecodeError are both ValueError subclasses, so
            # this catches a non-UTF-8 file too — otherwise it escapes as a traceback
            # instead of the refusal naming the file.
            unreadable.append(f"{path.name} (unreadable: {exc})")
            continue
        if not isinstance(record, dict):
            unreadable.append(f"{path.name} ({type(record).__name__}, not a record)")
            continue
        corpus_hash = record.get("corpus_hash")
        if corpus_hash in (None, ""):
            skipped.append(f"{path.name} (no corpus_hash)")
            continue
        if not isinstance(corpus_hash, str):
            # PRESENT but not a string: the label above would be UNTRUE (the file
            # plainly carries a corpus_hash) and skipping it silently is the very
            # defect this selector exists to close — a newer on-pin measurement
            # whose hash cannot be read would lose to an older scorable one with
            # nothing said. Its corpus is therefore UNKNOWABLE, which is the reason
            # the unreadable-file refusal already names: nothing can prove the newest
            # measurement on the frozen corpus is not this one.
            unreadable.append(
                f"{path.name} (corpus_hash is a "
                f"{type(corpus_hash).__name__}, not a hash)")
            continue
        if not _same_corpus(corpus_hash, frozen_pin):
            skipped.append(f"{path.name} (corpus_hash {corpus_hash[:20]}…)")
            continue
        raw_date = record.get("date")
        if raw_date not in (None, "") and _instant(raw_date)[0] != 1:
            # PRESENT and unreadable. Ranking it first would let an older receipt
            # publish; ignoring it would let an unscorable newest measurement be
            # skipped without the would-have-won refusal firing. Both are the defect
            # this selector exists to close, so the position is unknowable ⇒ refuse.
            unorderable.append(f"{path.name} (date {raw_date!r} is not an instant)")
            continue
        key = _sort_key(record, path.name)
        if not isinstance(record.get("operator_audit"), dict):
            unscorable.append((key, path.name))
            continue
        matched.append((key, path, record))
    if unreadable:
        raise SystemExit(
            f"capstone-measure: REFUSED — {len(unreadable)} file(s) in "
            f"{directory} could not be read as a receipt: {sorted(unreadable)}. "
            f"Every {E4_RECEIPT_GLOB} there is a candidate receipt, so nothing can "
            "prove the newest measurement on the frozen corpus is not one of them — "
            "repair or remove the file, or re-run the operator probe")
    if unorderable:
        raise SystemExit(
            f"capstone-measure: REFUSED — {len(unorderable)} receipt(s) in "
            f"{directory} ARE on the frozen corpus ({frozen_pin}) but carry a date "
            f"that is not an instant: {sorted(unorderable)}. The newest measurement "
            "on the frozen corpus cannot be ORDERED against them, so no scorable "
            "receipt can be shown to be the newest one — repair the date, or re-run "
            "the operator probe")
    if not matched:
        if unscorable:
            raise SystemExit(
                f"capstone-measure: REFUSED — {len(unscorable)} receipt(s) in "
                f"{directory} ARE on the frozen corpus ({frozen_pin}) but carry no "
                "operator_audit, and nothing else matched, so none of them can be "
                f"scored: {sorted(name for _key, name in unscorable)}. Repair or "
                "remove them, or re-run the operator probe")
        raise SystemExit(
            f"capstone-measure: REFUSED — no operator receipt in "
            f"{directory} was measured on the frozen corpus "
            f"({frozen_pin}). {len(paths)} candidate(s) were scanned and none "
            f"matched: {skipped or 'none present'}. E4 is a bar over the "
            "pre-registered corpus; scoring it from another revision would report "
            "a number this run's corpus cannot support")
    matched.sort(key=lambda entry: entry[0])
    winner = matched[-1][0]
    would_have_won = sorted(name for key, name in unscorable if key > winner)
    if would_have_won:
        raise SystemExit(
            f"capstone-measure: REFUSED — {len(would_have_won)} receipt(s) in "
            f"{directory} ARE on the frozen corpus ({frozen_pin}) and sort AFTER the "
            f"newest scorable one, but carry no operator_audit so they cannot be "
            f"scored: {would_have_won}. Skipping them would let an OLDER on-pin "
            "receipt publish the row's number while the newest measurement on the "
            "same corpus sat unread — repair or remove the receipt, or re-run the "
            "operator probe")
    _key, path, record = matched[-1]
    return path.relative_to(repo).as_posix(), record


def load_e4(repo: Path = ROOT, *, frozen_pin: str = "") -> dict:
    """E4's number, read VERBATIM out of the receipt measured on the frozen corpus."""
    rel, record = select_e4_receipt(repo, frozen_pin=frozen_pin)
    audit = record["operator_audit"]
    for key in ("planted", "edge_correct", "content_ok", "operators_total"):
        value = audit.get(key)
        if not isinstance(value, int) or isinstance(value, bool):
            raise SystemExit(f"capstone-measure: REFUSED — {rel} "
                             f"operator_audit[{key!r}] is not an integer count: "
                             f"{value!r}")
    return {
        "evidence": rel,
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


def e4_outcome_for(receipt: dict, bars: dict) -> dict:
    """The E4 outcome for a receipt — ONE derivation, two call sites.

    ``build_e4_row`` and ``main``'s ``render_log`` call both need it. Deriving it
    twice is how the log comes to print an outcome the row was not built from — the
    exact "the number is not the one you think it is" class this probe exists to
    refuse — so there is one definition here and no second transcription.
    """
    return e4_outcome(edge_correct=receipt["edge_correct"],
                      planted=receipt["planted"], bars=bars)


def build_e4_row(receipt: dict, *, bars: dict, corpus_sha: str) -> dict:
    """E4's row, from a receipt ``load_e4`` selected on the frozen corpus.

    Factored out of ``main`` so the union of *selection* and *row* is one pinned
    unit: the row's ``evidence`` is the receipt it was measured from, and while this
    was inline a reversion to a constant cited a path the row was not measured on
    with every test still green.

    ⛔ Three things are separate and the note keeps them separate: the corpus hash is
    the FROZEN one (that is why the receipt was selected), the receipt's own ``date``
    and ``commit`` are the RECEIPT's (it was written on an earlier head than this
    run — saying they "equal the frozen corpus" was a sentence the fields refute),
    and ``verdict``/``failure_origin`` are the receipt's gate verdict over its own
    metrics, not this row's verdict.
    """
    if not corpus_sha or not _same_corpus(receipt.get("corpus_hash"), corpus_sha):
        raise SystemExit(
            f"capstone-measure: REFUSED — build_e4_row was handed "
            f"{receipt.get('evidence')!r}, whose corpus_hash="
            f"{receipt.get('corpus_hash')!r} is not the frozen corpus "
            f"({corpus_sha}). The note below states that equality, so building the "
            "row from a receipt that does not carry the pin would print a sentence "
            "the row's own fields refute")
    result = e4_outcome_for(receipt, bars)
    # ⛔ THIS CLAUSE IS A CLAIM ABOUT THE NUMBER BESIDE IT, so it follows that
    # number's operands. It used to be unconditional, which was harmless only while
    # every receipt read below the bar: the bar is 15 of 15, so the moment an on-pin
    # receipt reads 15/15 the row is GREEN and it announced that "the planted edges
    # are still not all formed" — a sentence the row's OWN fields refute, fired at
    # exactly the epic's success state. Nor is `FAIL` a single cause: `e4_outcome`
    # fails on `edge_correct < required` OR on `planted != of`, so a receipt that
    # plants its own count (14/14, 16/16) is RED with numerator == denominator and
    # the wiring clause would be refuted there too. The `#9596` half is a fact about
    # the RECEIPT (its retrievability is unreported whatever the edge count) and
    # stays unconditional; only the `#8503` half is a claim about this number.
    # ⛔ `#8503` is named as the MEASUREMENT RECORD, never as a live blocker: it
    # CLOSED 2026-10-24 and that closure records E4's DISPROOF. `#10601`'s V12 refuses
    # a row whose `blocked_by` has closed — so a row that still said "#8503 is what
    # blocks this" would be refused by the gate for naming work that is finished.
    #
    # ⛔ THE RESIDUAL IS UNOWNED, AND SAYING OTHERWISE WAS A FALSE ATTRIBUTION.
    # `#8503`'s own body closes by stating it plainly — *"This issue is that residual,
    # now measured post-close. No open issue owns it."* — and `#9596` is scoped to a
    # DIFFERENT gap: the receipt's retrievability rate is unmeasured (a v2 receipt must
    # re-cut it on a real run). This clause used to say `#9596` owns the ten unformed
    # planted edges; it does not, and the sentence would have been emitted into every
    # future scored log. The row's `blocked_by` cell is still `#9596`, because the gate
    # requires a non-green row to name a LIVE issue and that is the only open one on
    # E4 — but the CELL is a pointer, and this NOTE is where its scope is stated.
    # ⛔ NOT "#8503's fix holds here": there IS no fix. `#8503` closed 2026-10-24 as a
    # DISPROOF, so a receipt that reaches the bar is evidence the edges were formed on
    # that artifact — not that a fix landed. The PASS arm is printed into a future
    # scored log, so it must not name work no issue ever did.
    if result["outcome"] == "PASS":
        edge_clause = ("the planted-edge bar IS met by this receipt, so nothing is "
                       "missing on this row")
    elif receipt["edge_correct"] < receipt["planted"]:
        edge_clause = ("the planted edges are still not all formed (#8503 — CLOSED "
                       "2026-10-24: that closure records the DISPROOF, so it is this "
                       "row's measurement record and not a live blocker, and no open "
                       "issue owns the residual — #8503 says so in its own words)")
    else:
        edge_clause = (f"the planted-edge COUNT, not the wiring, is the shortfall — "
                       f"this receipt plants {receipt['planted']} where the record "
                       f"freezes {bars['E4']['of']} (#8503 — CLOSED 2026-10-24: that "
                       f"closure records the DISPROOF, so it is this row's measurement "
                       f"record and not a live blocker, and no open issue owns the "
                       f"residual — #8503 says so in its own words)")
    return {
        "verdict": verdict_word(result),
        "numerator": receipt["edge_correct"],
        "denominator": receipt["planted"],
        "sha": receipt["commit"],
        "evidence": receipt["evidence"],
        "corpus_sha256": receipt["corpus_hash"],
        "note": (f"{result['value']}; bar {result['bar']}; content_ok="
                 f"{receipt['content_ok']} operators_total="
                 f"{receipt['operators_total']} on the SAME receipt, from "
                 f"{receipt['evidence']}. Its corpus_hash={receipt['corpus_hash']} "
                 f"IS the frozen corpus ({corpus_sha}) — the receipt is selected by "
                 f"that hash, so this row cannot be scored on another revision. The "
                 f"receipt's own date={receipt['date']} and commit={receipt['commit']} "
                 f"are the RECEIPT's, not this run's head. verdict="
                 f"{receipt['verdict']} failure_origin={receipt['failure_origin']} "
                 f"is the receipt's own gate verdict over its metrics, not this "
                 f"row's: {edge_clause}, and "
                 f"the receipt's retrievability rate is UNMEASURED (#9596)"),
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
    """E1/E1b/E2a/E2b, read off the `#9889` measurement record.

    The record is produced by ONE invocation of ``e1_e2_scores.py`` over the
    three probes' own outputs (see ``E1_E2_SCORES``); this function only maps it
    onto the shape the rows need. Both rows now cite a measurement on the FROZEN
    corpus taken at a named head — which the committed ``e1-e2.log`` never was.
    """
    path = repo / E1_E2_SCORES
    if not path.is_file():
        raise SystemExit(
            f"capstone-measure: REFUSED — {E1_E2_SCORES} does not exist. It is "
            f"produced by: {E1_E2_SCORES_CMD}")
    record = json.loads(path.read_text())
    e1 = record.get("e1") or {}
    e2 = record.get("e2") or {}
    e2a = e2.get("e2a") or {}
    e2b = e2.get("e2b") or {}
    e1b = record.get("e1b") or {}
    pairings = e1.get("pairings") or []
    if not pairings:
        raise SystemExit(f"capstone-measure: REFUSED — {E1_E2_SCORES} carries "
                         "no E1 pairings; E1 cannot be scored")
    if not e2a.get("value_units"):
        raise SystemExit(f"capstone-measure: REFUSED — {E1_E2_SCORES} carries "
                         "no E2a denominator")
    # E1's arithmetic is reported against the FIRST before realization; the
    # ``decidable`` flag is what stops that reading being mistaken for a verdict.
    first = pairings[0]
    return {
        "sha": record.get("head"),
        "decidable": bool(e1.get("decidable")),
        # The record's own floor finding. `e1_pairings` folds it INTO `decidable`
        # (`decidable = len(verdicts) == 1 and not below_floor`), so reading only
        # `decidable` makes an under-floor after arm report a pairing
        # disagreement it does not have — #10610 review issue 2.
        "below_floor": bool(e1.get("below_floor")),
        "before_share": first["before_share"],
        "after_share": first["after_share"],
        "before_spread_pp": e1.get("before_spread_pp"),
        "pairings": pairings,
        "objects_total": first["before_objects"],
        # READ from the record, never re-derived from share x objects: a
        # reconstruction that disagrees with the measurement is a number this
        # bundle did not take.
        "objects_numerator": first["before_bare_definite"],
        "after_objects": first["after_objects"],
        # The AFTER arm's own numerator, for the row's count slots. It is READ
        # from the record (the ON log's `e1-aux ... bare_definite=` line), never
        # re-derived as share x objects — the rule the two lines above state, and
        # which the E1 row needs now that it carries its count instead of
        # leaving the slots empty.
        "after_numerator": first.get("after_bare_definite"),
        "real_facts": e1b.get("real_facts") or 0,
        "atomic_points": e2b.get("atomic_points") or 0,
        "fused": e2b.get("fused") or 0,
        "e2a_units": e2a.get("value_units"),
        "e2a_survived": e2a.get("value_survived"),
        "e2a_required": e2a.get("required"),
        "e2a_outcome": e2a.get("outcome"),
        "e2a_falsifier_survived": (e2a.get("falsifier") or {}).get(
            "value_survived"),
        "gate_drops_total": (record.get("gate_drops") or {}).get("drops_total"),
        "gate_drops_objects": (record.get("gate_drops") or {}).get("objects"),
        "corpus_sha256": normalise_corpus(record.get("corpus_sha256")),
    }


# ── log rendering ─────────────────────────────────────────────────────────

def render_log(*, head: str, prereg_sha: str, bars: dict, corpus_sha: str,
               graph: str, runs: list[dict], observed: dict, unobserved: dict,
               census_data: dict, e1: dict, e1_aux: dict, e1_out: dict,
               e1b_out: dict, e2a_out: dict, e2b_out: dict, e3: dict,
               e3_out: dict, e4: dict, e4_out: dict, e5: dict, e5_out: dict,
               rows: list[dict], out_path: str,
               a_census: dict | None = None,
               required: list[str] | None = None,
               accounts: list[dict] | None = None,
               steps_gap: str = "",
               arm: dict | None = None,
               door: dict | None = None) -> str:
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
    add(f"# door: {door_header(door or {})}")
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
    lines.extend(step_accounting_lines(required, accounts, steps_gap))
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
    add(e3_legs_line(census_data.get("per_label")))
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
    add("## E1 / E1b / E2a / E2b — read out of the #9889 measurement record")
    add(f"e1 source: {E1_E2_SCORES} (one invocation of e1_e2_scores.py over the "
        "#9889 probes' own outputs)")
    add(f"e1 pairing: before_share={e1['before_share']:.6f} "
        f"after_share={e1['after_share']:.6f} "
        f"before_objects={e1['objects_total']} "
        f"after_objects={e1['after_objects']} "
        f"sha={e1['sha']} corpus_sha256={e1['corpus_sha256']} "
        f"decidable={e1['decidable']} before_spread_pp={e1['before_spread_pp']}")
    for pairing in e1["pairings"]:
        add(f"e1 before={pairing['before_share']:.6f} "
            f"bound={pairing['bound']:.6f} after={pairing['after_share']:.6f} "
            f"-> {'PASS' if pairing['passed'] else 'FAIL'} "
            f"ratio={pairing['ratio']:.6f} graph={pairing['before_graph']}")
    add(f"e1 outcome={e1_out['outcome']} {e1_out['value']} bar={e1_out['bar']}")
    # The narration BRANCHES on the state — the same class as the row note
    # (#10610 review issue 3). An unconditional "the pairings disagree" writes a
    # contradiction into the log of the first run that decides E1.
    add("e1 note: both before realizations clear E1's own floors ("
        f"{bars['floors']['e1_objects']} Objects, e1_min_before_share "
        f"{bars['floors']['e1_min_before_share']}) and the record's corpus IS "
        "the frozen fixtures_hash, so the shares are measurements. "
        + (f"The AFTER arm carries {e1['after_objects']} Objects, under the "
           f"frozen floor of {bars['floors']['e1_objects']}, so the row is "
           f"REFUSED as a floor finding — neither a pass nor a fail, and not "
           f"a pairing disagreement"
           if e1.get("below_floor") else
           "What they cannot do is decide the bar: the two before shares are "
           f"{e1['before_spread_pp']} pp apart and the pairings disagree, so the "
           "row is REFUSED at n=1 per arm rather than RED or GREEN"
           if not e1["decidable"] else
           "Every before realization agrees on the pairing, so the bar is "
           f"DECIDED: the row is {e1_out['outcome']} rather than a refusal"))
    add(f"e1b outcome={e1b_out['outcome']} {e1b_out['value']} bar={e1b_out['bar']}")
    add(f"e1b note: the record reports real_facts={e1['real_facts']} on the "
        "scored arm; no committed artifact carries "
        "grading.strict_survival_counts over the m2 lane, so this is a no-loss "
        "CONTROL, not the frozen metric")
    add(f"e2a outcome={e2a_out['outcome']} {e2a_out['value']} "
        f"bar={e2a_out['bar']}")
    add(f"e2a note: the frozen denominator IS carried — "
        f"{e1['e2a_survived']} of {e1['e2a_units']} value-bearing planted units "
        "strictly survive on the run's own graph (docs/evidence/9889/"
        "e2a-value-units.json), and the anchor-injection falsifier over the SAME "
        f"{e1['e2a_units']}-unit population reaches "
        f"{e1['e2a_falsifier_survived']}/{e1['e2a_units']} — so the counter is "
        "shown to move and the short bar is a measurement, not an inert "
        "instrument")
    add(f"e2b outcome={e2b_out['outcome']} {e2b_out['value']} "
        f"bar={e2b_out['bar']}")
    add(f"e2b note: fused={e1['fused']} of atomic={e1['atomic_points']} Points on "
        "the scored arm — the artifact's atomicity control, whose denominator is "
        "atomic POINTS rather than surviving planted units")
    add("")
    add("## E4 — the committed operator receipt, read verbatim")
    add(f"e4 receipt={e4['evidence']} run_id={e4['run_id']} date={e4['date']} "
        f"commit={e4['commit']} corpus_hash={e4['corpus_hash']} "
        f"judge_pin={e4['judge_pin']} verdict={e4['verdict']} "
        f"failure_origin={e4['failure_origin']}")
    add(f"e4 operator_audit planted={e4['planted']} "
        f"edge_correct={e4['edge_correct']} content_ok={e4['content_ok']} "
        f"operators_total={e4['operators_total']} "
        f"operators_provenanced={e4['operators_provenanced']}")
    add(f"e4 outcome={e4_out['outcome']} {e4_out['value']} bar={e4_out['bar']}")
    on_pin = _same_corpus(e4["corpus_hash"], corpus_sha)
    add("e4 provenance: the receipt's own "
        f"date={e4['date']} and commit={e4['commit']} are the RECEIPT's, not this "
        f"run's head. " + (
            f"Its corpus_hash={e4['corpus_hash']} IS the frozen corpus "
            f"({corpus_sha}) — this row was selected BY that hash, so it is scored "
            "on the pre-registered corpus and V7's declared-mismatch arm is not "
            "taken for it"
            if on_pin else
            f"Its corpus_hash={e4['corpus_hash']} is NOT the frozen corpus "
            f"({corpus_sha}). This row is therefore scored as a NON-GREEN "
            "measurement on ITS OWN corpus revision, and the gate requires that "
            "digest to appear in the artifact itself (V7)"))
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
    # #10982: the A-row census, rendered from the SAME pass as the E-row census so a reader cannot
    # be served two different graphs in one log, and emitted at a REAL section boundary — ⛔ it used
    # to be spliced in right after the E3 note, which put the block INSIDE E3's section and left the
    # `e1-aux objects_in_graph=…` line under the A-row heading, so a reader attributed an E1 floor
    # reading to the A-row census (#10982 review round 2).
    lines.extend(a_row_census_lines(a_census))
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
            "door": {**door_record(door or {}),
                     "captures": door_witness(runs)},
            # #10215: V5 reads the step split from THESE two fields. A log that
            # omits them is read as the pre-#10215 "all eight required" shape,
            # which is why emitting them is the whole point of the accounting.
            "steps_required": required,
            "steps_conditional": accounts,
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
            # #10982: the A-row census travels in the machine record too, so a consumer
            # reads the same numbers the human log prints.
            "a_row_census": a_census,
            "e4_receipt": {
                "numerator": e4["edge_correct"],
                "denominator": e4["planted"],
                "planted": e4["planted"],
                "edge_correct": e4["edge_correct"],
                "content_ok": e4["content_ok"],
                "operators_total": e4["operators_total"],
                "measured_commit": e4["commit"],
                "corpus_hash": e4["corpus_hash"],
                "source": e4["evidence"],
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
                "source": E1_E2_SCORES,
                "before_share": e1["before_share"],
                "after_share": e1["after_share"],
                "before_objects": e1["objects_total"],
                "after_objects": e1["after_objects"],
                "corpus_sha256": e1["corpus_sha256"],
                "decidable": e1["decidable"],
                "before_spread_pp": e1["before_spread_pp"],
                "e2a_value_units": e1["e2a_units"],
                "e2a_value_survived": e1["e2a_survived"],
                "e2a_falsifier_survived": e1["e2a_falsifier_survived"],
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
    # The step split is read BEFORE any model call: a record whose declaration is
    # unreadable must refuse the run, not produce a table that silently means
    # something else (#10215, `capstone_run.G5`'s own rule).
    conditional = read_conditional(prereg)
    try:
        bars = parse_bars(prereg)
    except ValueError as exc:
        raise SystemExit(f"capstone-measure: REFUSED — {exc}") from exc
    corpus_sha = check_corpus(prereg)
    # ⛔ THE E4 ARTIFACT IS RESOLVED HERE, WITH THE OTHER PRE-FLIGHT READS, AND FOR
    # THE SAME REASON: `select_e4_receipt` has ~8 refusal arms (absent pin, no match
    # on the pin, unreadable / non-object / non-UTF-8 file, non-string `corpus_hash`,
    # unorderable date, would-have-won unscorable) and every one of them is a property
    # of the ARTIFACTS, not of the run. Resolved where it used to be — after
    # `run_corpus` — a bad receipts tree burned the full 7-session extraction cost and
    # wrote NOTHING, which is the exact class `#9889`'s own pin exists for ("a real run
    # burned its 7-session LLM cost and wrote NOTHING"). The receipt is CARRIED to the
    # E4 section rather than re-read, so this is one read, not two.
    receipt = load_e4(frozen_pin=corpus_sha)

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
    # The door is a PATH, not only an environment fact (#10983 review round 11):
    # a capture that returned before the lane dispatch — keyless (#3892) or a
    # re-capture of an existing session (#1727 replay) — reports ok=True, so the
    # failed-list below cannot see it and the table would be written anyway.
    require_door_entered(runs)
    failed = [run["session_id"] for run in runs if not run["ok"]]
    census_data = census(graph_uri(os.environ["TORTOISE_DB_URI"], graph))
    # #10982: the A-row census runs against the SAME graph and the SAME pass. A failure here must
    # not silently vanish: a census that could not run renders as a NAMED gap, never as zeros.
    try:
        a_census = a_row_census(graph_uri(os.environ["TORTOISE_DB_URI"], graph))
    except Exception as exc:  # the E-rows must still be scored and the gap named
        # ⛔ `census_reason`, not the raw exception: `from_uri` failures quote the URI
        # they were handed, and this string lands in the COMMITTED record (review F1).
        a_census = {"error": census_reason(exc), "criteria": list(A_CRITERIA)}
    observed, unobserved = observe_steps(runs, census_data)
    # #10215: the same split the run probe's G5 emits, read from the SAME frozen
    # record — so a GREEN row from this probe is no longer refused for S2.3.
    required, accounts, steps_gap = step_accounting(conditional, runs, observed)
    if steps_gap:
        print(f"capstone-measure: steps accounting NOT CLAIMED — {steps_gap}",
              file=sys.stderr)

    e1 = measure_e1_e2()
    bare = sum(1 for name in census_data["object_names"]
               if _is_bare_definite(name))
    e1_aux = {"bare_definite": bare,
              "share": (bare / len(census_data["object_names"])
                        if census_data["object_names"] else None)}

    # ── E1 / E1b / E2a / E2b (E1/E2 are the table's rows) ────────────────
    e1_result = e1_outcome(before_share=e1["before_share"],
                           after_share=e1["after_share"],
                           objects=e1["after_objects"], bars=bars,
                           decidable=e1["decidable"],
                           below_floor=e1["below_floor"])
    e1b_result = outcome(measured=True, below_floor=False,
                         passed=e1["real_facts"] >= bars["E1b"]["required"],
                         bar=f"{bars['E1b']['required']} of {bars['E1b']['of']}",
                         value=f"real_facts={e1['real_facts']}")
    e2a_result = (e2a_outcome(e1, bars) if e1["e2a_outcome"] else
                  outcome(measured=False, below_floor=False, passed=False,
                          bar=f"{bars['E2a']['required']} of {bars['E2a']['of']}",
                          value=f"value_units={e1['e2a_units']} survived="
                                f"{e1['e2a_survived']}"))
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
    e2_result = e2_outcome(e2a_result=e2a_result, e2b_result=e2b_result,
                           bars=bars)

    e1_row = {
        "verdict": verdict_word(e1_result),
        "sha": e1["sha"],
        "corpus_sha256": e1["corpus_sha256"],
        # The population E1's own criterion is written over: a share over ALL
        # `:Object` nodes written by the AFTER arm. The count slot carries the
        # PASS-direction half of it (Objects that are NOT bare-definite) because
        # that is the table's convention on every other row and the bare-definite
        # count it minimises can legitimately be 0 — see `e1_pass_objects`.
        "numerator": e1_pass_objects(e1),
        "denominator": e1["after_objects"],
        "note": e1_row_note(e1, e1_result, bars),
    }
    e2_row = {
        "verdict": verdict_word(e2_result),
        "sha": e1["sha"],
        "corpus_sha256": e1["corpus_sha256"],
        "numerator": e1["e2a_survived"],
        "denominator": e1["e2a_units"],
        "note": e2_row_note(e1, e2a_result, e2b_result, e2_result),
    }

    # ── E3 ────────────────────────────────────────────────────────────────
    # The empty-leg set is passed to the row because the row's arithmetic cannot see it: the
    # totals are SUMS, so a registered class with no nodes is already gone from both halves by the
    # time `e3_outcome` is called (#9736).
    e3_result = e3_outcome(anchored=census_data["anchored_total"],
                           derived=census_data["derived_total"], bars=bars,
                           unmeasured_legs=e3_unmeasured_legs(
                               census_data.get("per_label")))
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
    # `receipt` was resolved by the PRE-FLIGHT, before the model pass, so a refusal
    # cannot land after the spend. It is the same object, not a second read.
    e4_result = e4_outcome_for(receipt, bars)
    e4 = build_e4_row(receipt, bars=bars, corpus_sha=corpus_sha)

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
                 f"before either number is called final. The row's blocker moved "
                 f"on 2026-11-02 from #9738 to #5087: #9738 CLOSED that morning "
                 f"because its own record correction landed (PR #10525), which is "
                 f"not a measurement of this number — raw recovery "
                 f"{e5_result['raw_recovery']:.4f} against the 0.80 floor is S3 "
                 f"selection recovery, owned by the S0→S6 selective write path "
                 f"(#5087)"),
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
                      a_census=a_census,
                      required=required, accounts=accounts, steps_gap=steps_gap,
                      arm=arm, door=arm)

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
