#!/usr/bin/env python3
"""The extractor-v4 capstone run — S0 → S6 on the pre-registered corpus (#9080).

This is the first half of the issue's own acceptance command. It exists because
the epic owns no implementation and its closing rule is an *execution*:
*"Close this epic only when E1–E5 all have evidence attached to this issue —
not when the children close. A child closing is a claim; this epic is where the
claim is checked against the pipeline running end to end."*

⚠️ **The issue BODY's claims were re-read at `717355e67` (its figures are pinned
to `9037c7a7b`).** That sha is a MAIN commit — the base for the body re-read, NOT
a stamp for any capstone log (the shipped log is stamped at `027c8c1ab`; see
``README.md``). Two of the body's statements are STALE and this probe is built on
the corrected reading:

1. **A single entry point DOES exist.** The body says *"whether any single
   entry point … can drive all eight in one pass"* is an open question. It can:
   ``TortoiseSDK.capture_session`` → ``sdk._extract_session_v2``
   (``sdk.py:12836``, called from the hosted capture lane at ``tortoise/hosted_api.py:15515``) →
   ``extractor_v2.extract_session_v2`` (``extractor_v2.py:8733``) calls
   ``_edus_from_conversation`` (S0, ``:8851``), ``run_s1`` (S1, ``:8884``),
   ``run_s2`` (S2.1, ``:8908``), ``_run_vet_pass`` (S2.2, ``:8937``),
   ``kind_classifier.classify_items`` (S2.3, ``:8952``),
   ``_apply_classify_kinds`` (S2.3, ``:8953``), ``search_graph`` (S3,
   ``:8969``), ``run_s4`` (the gap filler, ``:8990``), and ``execute_embed``
   (S5+S6, ``:9189``). ONE cite per symbol, on the line that symbol is on:
   S2.3 makes TWO calls two lines apart, and the single ``:NNNN`` a
   ``a``/``b`` pair cite could carry is only ever checkable for one of them —
   that pair form is how ``_apply_classify_kinds`` sat on ``:8788`` (naming
   ``classify_items``' line) while the README's one-symbol row was right. The
   SYMBOL is authoritative and the line is a convenience — the three
   FILE-NAMED cites above are resolved against the tree by the bundle's pin,
   and EACH of the nine bare step cites is resolved by the same pin
   (``tests/test_7949_capstone_verdict_check.py::
   test_the_probes_line_citations_resolve_too``, which requires the cited line
   to carry the symbol it is cited for), so a merge that moves them reds a
   registered test instead of shipping a number that names unrelated code.
   They need that separate leg because a bare ``:NNNN`` carries no filename, so
   the bundle's own sweep (``_BUNDLE_CITE``) cannot see them: ``#10243`` moved
   ``extractor_v2.py`` by +88 and every cite in the list went stale while every
   other pin stayed green, which an earlier sentence here used to deny by
   pointing at a README cross-pin ``#10211`` had already invalidated. No line in
   that list is claimed to be self-detecting on its own. (No derivation head is
   named here on purpose: the
   stamps such a sentence used to name re-staled the moment `main` moved, and a
   claim that can only re-stale is not worth keeping.) So the capstone drives
   the REAL entry point — it does not compose the step functions, which the
   issue explicitly forbids.
2. **``docs/evidence/7949-capstone/`` did not exist and still does not exist as
   a measured bundle** — this probe creates it, and the bundle ships with a
   REFUSED log rather than a green table (see below).

## The gate chain — every gate FAILS CLOSED, in §4.4's order

§4.4: S0 READ is **FAIL-CLOSED — end the run before any model call or write**.
The capstone inherits that: a run whose pre-registration does not exist must be
refused BEFORE it spends a model call, and the refusal must be recorded as a
verdict table, not as an exception. The chain:

    G0  the pre-registration commit resolves, carries the artifact, and is an
        ancestor of HEAD                                   → REFUSED (#9075)
    G1  the corpus matches the sha the pre-registration froze → REFUSED (#9075)
    G2  TORTOISE_DB_URI is a SEARCHABLE backend (not embedded/lite) — an
        embedded store never searches, so S3 resolves nothing and supersession
        records are structurally zero                           → REFUSED
    G3  a routing-usable LLM provider key exists — without one `capture_session`
        SKIPS extraction and still stores the turns, i.e. NO S1–S6 would run
                                                                 → REFUSED
    G4  the real entry point runs one pass                      → REFUSED on raise
    G5  all EIGHT steps were OBSERVED in that one pass (§4.4's step count)
                                                                 → REFUSED
    G6  the per-criterion evidence is collected

Nothing is inferred. A refused run emits 5 rows that are NOT green, each naming
the issue that blocks it — which is exactly what the issue asks for:
*"it must report a red row and name the blocking issue rather than infer green
from a closed child."*

## Why the steps are observed, never instrumented by wrapping

E4's row explicitly *"fails if … the extractor was monkeypatched"*, so the probe
must not wrap the pipeline to watch it — a wrapper is the evidence-destroying
move the row is guarding against. The probe READS the pipeline's own returned
surface; it never installs a spy.

⚠️ **That surface was only half-reachable until #9116, and the seam is now
landed.** `extract_session_v2` returns the full per-stage block (``vet``,
``classify_later``, ``search``, ``payload``, ``embed_list``, ``story_arc``), but
`capture_session` — the entry point this probe drives — assembled its receipt
from ``extracted`` + ``meta``, forwarding only ``stats``. So four witnesses did
not exist on the object the probe holds and G5 refused for EVERY input.
`_extract_session_v2` now copies those stages' own facts onto the existing
``stats`` key as ``stats["step_witness"]`` (``_capture_step_witness`` in
``sdk.py`` — a sub-key of a key the receipt already carried, so no new receipt
key and no change to the SDK surface), and this probe reads THAT. Where a step
still comes back unobserved, the run reports why from the witness's own values
— and a receipt carrying no ``step_witness`` at all is still refused as the
INSTRUMENT gap it is, never as "the pipeline did not run VET/classify/search/
commit".

Run:
    python docs/evidence/7949-capstone/probes/capstone_run.py \\
        --prereg <frozen pre-registration commit> --corpus <corpus> \\
        --out docs/evidence/7949-capstone/capstone-<sha>.log
    python tools/capstone_verdict_check.py docs/evidence/7949-capstone/capstone-<sha>.log

⛔ NEVER write a run over ``docs/evidence/7949-capstone/capstone.log``: that file is the
shipped PRE-LANDING REFUSAL and its add-commit predates the pre-registration, so the
only log it can hold is one that measured nothing (``capstone_preregistration_check``
enforces exactly that, and its comparison uses the file's OLDEST add-commit — a scored
run written there could never be un-redded by a later re-run). ``--out`` takes the path
for a scored run.

``--head <sha>`` RE-STAMPS the run at another commit of the SAME TREE (a tag, or
this tree checked out elsewhere) — it does not measure different code. It is
validated like a row sha (7-40 lowercase hex digits that resolve to a commit
here) AND its ``^{tree}`` must equal the worktree's ``HEAD^{tree}``: a commit
whose tree differs is a different program, so it is REFUSED — check that
revision out and run there instead. A checkout with no resolvable ``HEAD``
commit is refused too. Every refusal happens before G0, because every row is
stamped with the value and V5 resolves the pre-registration's ancestry against
it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

# ``parents`` is indexed from the FILE: [0]=probes, [1]=7949-capstone,
# [2]=evidence, [3]=docs, [4]=<repo>. ``parents[3]`` is ``<repo>/docs`` — the
# off-by-one was masked only because every git call is subdirectory-safe, and
# any later ``ROOT / <repo-relative path>`` join (the shape
# ``capstone_verdict_check.check`` already uses with ``repo / ev_path``) would
# silently resolve under ``docs/``. Pinned by
# tests/test_7949_capstone_verdict_check.py::test_root_is_the_repo_root.
ROOT = Path(__file__).resolve().parents[4]          # repo root
EVIDENCE_DIR = Path("docs/evidence/7949-capstone")
DEFAULT_PREREG_ARTIFACT = EVIDENCE_DIR / "preregistration.json"

SCHEMA = "capstone-verdict-v1"
PREREG_SCHEMA = "capstone-preregistration/v1"
STEPS = ("S0", "S1", "S2.1", "S2.2", "S2.3", "S3", "S5", "S6")

#: A commit sha as the gate's own V3 accepts it. `--head` (below) stamps EVERY
#: row and is what V5's ancestry test resolves against the pre-registration, so
#: an operator override that reaches the log unvalidated would make the whole
#: provenance chain rest on free text.
HEAD_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")

#: Every witness BLOCK ``observe_steps`` reads out of the receipt's
#: ``stats["step_witness"]`` record (#9116). Declared so a witness can never be
#: added silently: the contract test
#: ``test_every_witness_block_observe_steps_reads_is_produced_by_the_seam``
#: derives the READ set from this module's source and the PRODUCED set by
#: calling ``tortoise.sdk._capture_step_witness`` — the real seam, not a
#: fixture — then requires ``WITNESS_KEYS <= produced`` exactly. Read an
#: undeclared block, or let a block stop being produced, and it reds.
WITNESS_KEYS = ("reader", "story", "map", "vet", "classify_later",
                "search", "merge", "commit")

#: The steps with no witness EXCEPT the seam's (#9116): S0/S1/S2.1/S5 are
#: readable straight off ``stats`` (``edus``/``chunks``, ``llm.by_stage``,
#: ``s4_merge``), so a receipt that carries no ``step_witness`` at all — a
#: replay, a keyless capture, the M2 lane — still witnesses those four. These
#: four have nothing else: on such a receipt their absence is the INSTRUMENT
#: (#9116), and G5 says so instead of reporting a pipeline that skipped work.
#: On a receipt that DOES carry the seam, an absent step is a different finding
#: — the stage ran (or was gated off) and the witness shows which.
STEPS_NEEDING_WITNESS = ("S2.2", "S2.3", "S3", "S6")

# Each criterion's OWNER — the issue that must land for the row to be
# measurable at all. The table's non-green rows cite these, so a red row always
# carries its cause and never reads as "not done yet".
OWNERS = {
    "E1": "#9075",   # pre-registration (thresholds) gates the measurement in #9076
    "E2": "#9075",
    "E3": "#5199",   # :Object/:Event version anchoring — the anchor stops at Points
    "E4": "#9077",   # the planted-operator corpus, real path, no monkeypatch
    # ⛔ E5 was #7947 until 2026-10-20. That issue is CLOSED COMPLETED (the
    # falsifier ran twice, PR #9071 merged, §10 carries the result), so citing
    # it sent every reader of this table to a LANDED lane — the same durable
    # misattribution ALSO_BLOCKED_BY exists to prevent, and two commits here
    # re-touched the README after the 12:54Z closure without re-reading it
    # (#9089 review). What E5 still lacks is not its measurement but a
    # PRE-REGISTERED capstone pass to re-derive it in — G0, i.e. #9075, which
    # the epic's own row already named alongside #7947. A row moves only by
    # RE-RUNNING this probe; it never moves by reading an issue's state.
    "E5": "#9075",
}
# Criterion-specific causes that survive the pre-registration landing: named in
# the row's note so the reader is not told "#9075" for a row #9075 cannot fix.
ALSO_BLOCKED_BY = {"E1": "#9076", "E2": "#9076"}
# Criterion lanes that have LANDED (state re-read, not assumed). Named in the
# row's note so the table says what is DONE as well as what is missing: E5's
# falsifier is not outstanding work, and a note that stayed silent about it
# would leave the next reader re-opening a closed issue to find that out.
LANDED_LANES = {"E5": "#7947"}
# GATES (not criterion lanes) that have LANDED since the shipped log's stamp.
# ``#9075`` is the pre-registration record itself and it is the ``blocked_by``
# of every row on a PRE-G0 refusal, so a re-run must not print the lane name
# with no state: it closed COMPLETED (`#9507`, `a6085af84`) and the record it
# produced is committed at ``docs/evidence/7949-capstone/preregistration.json``
# — the merge-up ``5767d04ac`` is where this branch learned that. The rows keep
# naming the lane (it is what OWNS the evidence and what the shipped log says)
# and say out loud that it has landed, which is the same durable-misattribution
# rule ``LANDED_LANES`` applies to a criterion lane.
LANDED_GATES = {"#9075": "#9507"}

BEGIN = "=== CAPSTONE-VERDICT-BEGIN ==="
END = "=== CAPSTONE-VERDICT-END ==="


# ── pure helpers (pinned by tests/test_7949_capstone_verdict_check.py) ─────

def corpus_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_corpus(path: Path) -> list[dict]:
    """Read the corpus as the `capture_session` conversation shape.

    Accepts a JSON list of ``{"role", "content"}`` turns or JSONL of the same.
    A corpus that does not parse is an INPUT error (§4.4 S0: "A parse/mapping
    failure is an INPUT error — surfaced, never retried").
    """
    text = path.read_text()
    stripped = text.lstrip()
    if stripped.startswith("["):
        turns = json.loads(text)
    else:
        turns = [json.loads(line) for line in text.splitlines() if line.strip()]
    if not isinstance(turns, list) or not turns:
        raise ValueError(f"corpus {path} is empty")
    for turn in turns:
        if not isinstance(turn, dict) or not str(turn.get("content", "")).strip():
            raise ValueError(f"corpus {path}: turn without content: {turn!r}")
    return turns


#: Which witness block accounts for which step — the ONE map G5's cause line
#: reads. A step absent from it has no witness of its own (nothing to quote).
STEP_WITNESS_BLOCK = {
    "S0": "reader",
    "S1": "story",
    "S2.1": "map",
    "S2.2": "vet",
    "S2.3": "classify_later",
    "S3": "search",
    "S5": "merge",
    "S6": "commit",
}


def _step_cause(witness: dict, step: str) -> str:
    """The witness's OWN values for a step it does not witness, rendered.

    A refusal that says only "S2.2 not observed" cannot be acted on: the same
    absence is produced by a flag that is off, a pass that idled on an empty
    list, and a stage that failed. Quoting the block verbatim is the difference
    between a finding and a shrug — bounded, so a big block cannot flood the log.
    """
    block = witness.get(STEP_WITNESS_BLOCK.get(step, "")) or {}
    rendered = json.dumps(block, sort_keys=True, default=str)
    if len(rendered) > 240:
        rendered = rendered[:237] + "..."
    return rendered


def observe_steps(result: dict) -> dict[str, str]:
    """Which of the EIGHT steps did THIS pass actually execute?

    Returns ``{step: why}`` for the steps observed, and nothing for the rest —
    absence is the signal. Every witness is read off the pipeline's own returned
    surface (never a wrapper), so the observation cannot alter what it watches.

    ⚠️ #9116: ``capture_session``'s receipt is the ONLY object the shipped caller
    can hand in, and it carries no ``vet``/``classify_later``/``search``/
    ``payload`` key — those live on ``extract_session_v2``'s return, one layer
    down. The seam that closes that gap is ``stats["step_witness"]``
    (``tortoise/sdk.py::_capture_step_witness``): the four stages' own facts,
    copied onto the existing ``stats`` key rather than a new receipt key. This
    observer reads THAT, and nothing it would have to reach for itself.

    The fallbacks are deliberate and are not the seam: ``stats``'s own
    ``edus``/``chunks``, ``llm.by_stage`` and ``s4_merge`` already witness S0/S1/
    S2.1/S5 on any v2 receipt, seam or no seam, so a receipt that carries no
    ``step_witness`` (a replay, a keyless capture) is still measured on those
    four rather than reported as an empty pipeline.
    """
    stats = result.get("stats") or {}
    w = stats.get("step_witness") or {}
    llm = stats.get("llm") or {}
    by_stage = llm.get("by_stage") or {}
    reader = w.get("reader") or {}
    story = w.get("story") or {}
    mapped = w.get("map") or {}
    vet = w.get("vet") or {}
    classify = w.get("classify_later") or {}
    search = w.get("search") or {}
    merge = w.get("merge") or stats.get("s4_merge") or {}
    commit = w.get("commit") or {}

    edus = reader.get("edus", stats.get("edus"))
    chunks = reader.get("chunks", stats.get("chunks"))
    story_arc_chars = int(story.get("story_arc_chars") or 0)
    items = mapped.get("items") or {}
    candidate_count = (sum(int(n or 0) for n in items.values())
                       if isinstance(items, dict) else 0)

    seen: dict[str, str] = {}
    if edus or chunks:
        seen["S0"] = (f"reader segmented the source: edus={edus} chunks={chunks}")
    if story.get("by_stage_s1") or by_stage.get("s1") or story_arc_chars:
        seen["S1"] = (f"first LLM call reached: by_stage.s1="
                      f"{bool(story.get('by_stage_s1') or by_stage.get('s1'))} "
                      f"story_arc_chars={story_arc_chars}")
    if by_stage.get("s2") or candidate_count:
        seen["S2.1"] = f"S2 map produced {candidate_count} candidate item(s)"
    # S2.2/S2.3: the pass must be ENABLED and have DONE something. `enabled`
    # alone is the flag, and a flag-on block with empty counters is the shape an
    # idled (empty list) or fail-open pass leaves — the confusion that would
    # report an OFF run as 8/8 (#9823).
    if vet.get("enabled") is not False and (vet.get("s2") or vet.get("union")):
        seen["S2.2"] = ("the VET gate reported a pass "
                        f"(step_witness['vet']: s2={bool(vet.get('s2'))}, "
                        f"union={bool(vet.get('union'))})")
    if classify.get("enabled") is not False and (classify.get("s2")
                                                 or classify.get("union")):
        seen["S2.3"] = ("a classify pass reported assignments "
                        "(step_witness['classify_later'])")
    # S3: the search stage RESOLVED, not merely attempted. `queries_run` is the
    # budget it spent and `degraded` is the stage's own verdict on itself:
    # `search_graph` increments the counter BEFORE each attempt and returns
    # `degraded=True` with a `reason` on the FIRST-QUERY exception
    # (`extractor_v2.py::search_graph`), so `queries_run > 0 and degraded` is
    # exactly "the graph search raised" — a spent budget with nothing resolved.
    # G2 cannot catch it (its `backend_is_searchable` is env-derived and issues
    # no query), so counting a degraded attempt as S3 lets V5 — the epic's ONLY
    # closing gate — pass on a step that never resolved, and every E-row is
    # published as a valid measurement off that run. The README's S3 RESOLVE row
    # records the rule ("a degraded search is not S3") and the sibling probe
    # implements it in its own S3 arm (`capstone_measure.py`); this arm must
    # state the same thing (#10214 review, P1).
    if search.get("queries_run") and not search.get("degraded"):
        seen["S3"] = ("S3 searched the graph "
                      f"(step_witness['search']: queries_run="
                      f"{search.get('queries_run')}, degraded="
                      f"{bool(search.get('degraded'))}, "
                      f"hits={search.get('hits')})")
    if merge.get("merged_items") is not None:
        seen["S5"] = (f"S5 merged {merge.get('merged_items')} item(s) "
                      f"into the commit list")
    if commit.get("payload_produced"):
        # ``committed`` is the count the write loop RESOLVED (created or folded
        # onto an existing canonical) — never a minted-only count, so the
        # message states it as such rather than as a second condition the
        # branch does not test (#10214 review, P3). Gating on it would score a
        # payload with nothing to commit as unwitnessed, which is a different
        # claim; the honest fix is to say what the value is.
        seen["S6"] = ("the Layer-1 payload was produced and its points went "
                      "through the commit loop "
                      f"(step_witness['commit']: entities={commit.get('entities')}, "
                      f"points={commit.get('points')}, "
                      f"operators={commit.get('operators')}, "
                      f"resolved={commit.get('committed')})")
    return seen


def e4_receipt(stats: dict) -> dict:
    """E4's receipt — the no-silent-drop proof (`_s4_merge_stats`).

    E4's row *"fails if the denominator is 4"*: the historical defect ran the
    merge over only 4 of the 15 edges reachable on the real extraction leg, so
    a 4 denominator is the number the row refuses. ⚠️ What THIS surface can
    measure is the merge's item accounting — `kept_from_s2` items carried of
    the `s2_items` handed in — not the 15-edge operator gold the row is phrased
    over; the edge denominator arrives with #9077's wired leg, and until then
    the receipt must not be read as "the 4-edge defect re-appeared". The
    numbers are carried verbatim into E4's row so the gate reads the same ones
    the pipeline emitted.
    """
    merge = stats.get("s4_merge") or {}
    den = int(merge.get("s2_items") or 0)
    return {
        "numerator": int(merge.get("kept_from_s2") or 0),
        "denominator": den,
        "s4_items": merge.get("s4_items"),
        "merged_items": merge.get("merged_items"),
        "corrected_by_s4": merge.get("corrected_by_s4"),
        "verbatim_reemissions": merge.get("verbatim_reemissions"),
        "added_by_s4": merge.get("added_by_s4"),
        # ⛔ Named for the NUMBER it tests, not for a cause it cannot see.
        # E4's row says it *fails if the denominator is 4* (the historical
        # defect's number), and a 4 is refused whatever the quantity counts.
        # But this surface measures the S4 merge's ITEM accounting
        # (`_s4_merge_stats`: items handed to the merge / items it carried),
        # not the 15-EDGE operator gold E4's row is phrased over — the edge
        # denominator needs #9077's wired leg. So the flag may not be read as
        # "the 4-edge defect re-appeared": it says "this measurement's
        # denominator is the number E4 refuses", which is the row's own rule.
        "denominator_is_4": den == 4,
    }


def _landed_suffix(cid: str) -> str:
    """The "… and this much is DONE" half of a row's note.

    A row that names only what is missing cannot distinguish "nobody has run
    this" from "the measurement landed and only the re-derivation is left", and
    the second reads identically to the first. E5 is that case: ``#7947`` is
    CLOSED COMPLETED, so its note says so — and says that a closed issue still
    cannot move the row, because a row moves only by re-running this probe.
    """
    lane = LANDED_LANES.get(cid)
    if not lane:
        return ""
    return (f" / {lane} LANDED (CLOSED COMPLETED) — its measurement is recorded "
            "there, but a row moves only by RE-RUNNING this probe, never by "
            "reading an issue's state")


def evidence_path(out: Path | None) -> str:
    """The artifact a row CITES — where THIS run actually wrote.

    ⛔ The rows used to carry a hard-coded ``EVIDENCE_DIR / "capstone.log"``
    regardless of ``--out`` (the open review P1 on this file): a run written to
    ``capstone-<sha>.log`` produced a table whose every row pointed at
    ``capstone.log``, so the gate's V4 resolved to a DIFFERENT file that existed
    for unrelated reasons and the citation proved nothing. Worse, the README
    tells a scored re-run to write somewhere else precisely so it does not red the
    ordering check — a citation that ignores ``--out`` silently undoes that.

    ``--out`` absent means the operator is using the issue's own acceptance
    command (``… | tee docs/evidence/7949-capstone/capstone.log``), which is the
    one case where the tee target is the artifact: the log goes to stdout and the
    tee writes it there.
    """
    return str(out) if out is not None else str(EVIDENCE_DIR / "capstone.log")


def refused_rows(reason: str, *, prereg_ok: bool,
                 prereg_unread: bool = False,
                 evidence: str | None = None) -> list[dict]:
    """The 5 rows of a REFUSED run.

    A refusal cannot make a row green, so every row is non-green and names its
    cause. ``UNPREREGISTERED`` is reserved for E1/E2 when the pre-registration
    itself is the missing thing: those two rows are the ones whose thresholds
    #9075 owns, and calling them "blocked by an unimplemented mechanism" would
    misattribute the cause.

    ``prereg_ok=True`` means G0 READ the frozen thresholds, so ``#9075`` is NOT
    this run's blocker any more — the row names the measurement lane
    (``ALSO_BLOCKED_BY``) instead. Naming ``#9075`` for E1/E2 after it landed
    sends the next lane to a closed issue; that was the G6 half of the same
    misattribution (#9089 review) and it lived on in the REACHABLE path.
    """
    rows = []
    for cid in ("E1", "E2", "E3", "E4", "E5"):
        # `UNPREREGISTERED` CLAIMS the pre-registration is the missing thing, so
        # it needs a run that actually looked (G0). A gate that stopped at G-1
        # never consulted it and must not say either way: every row is BLOCKED
        # and names the lane that OWNS the evidence, which is true regardless.
        verdict = ("UNPREREGISTERED"
                   if cid in ("E1", "E2") and not prereg_ok and not prereg_unread
                   else "BLOCKED")
        # Two distinct causes, both stated: WHY this run produced no number,
        # and WHICH lane owns the number. A row that named only the gate would
        # read as "blocked by the run", and the next lane would re-run instead
        # of landing the mechanism. A row that named only the owner would hide
        # that the table's own run never got off the ground.
        owner = (ALSO_BLOCKED_BY.get(cid, OWNERS[cid])
                 if prereg_ok and not prereg_unread else OWNERS[cid])
        note = f"gate refused: {reason}; this criterion's evidence is owned by "
        note += owner
        if cid in ALSO_BLOCKED_BY:
            note += f" / the measurement itself is {ALSO_BLOCKED_BY[cid]}"
        if owner in LANDED_GATES:
            note += (f" / {owner} LANDED ({LANDED_GATES[owner]}) — the record it "
                     "produced is frozen at "
                     "docs/evidence/7949-capstone/preregistration.json, so what "
                     "this row is missing is the SCORED pass, not the thresholds")
        note += _landed_suffix(cid)
        rows.append({
            "criterion": cid,
            "verdict": verdict,
            "evidence_kind": "refusal",
            "evidence": evidence or str(EVIDENCE_DIR / "capstone.log"),
            "sha": None,          # filled by emit_log with the run head
            "numerator": None,
            "denominator": None,
            "corpus_sha256": None,
            "sibling": None,
            "blocked_by": owner,
            "note": note,
        })
    return rows


def completed_rows(*, head: str, corpus_sha: str,
                   receipt: dict, evidence: str | None = None) -> list[dict]:
    """The 5 rows of a COMPLETED run (G6).

    ⛔ Reached ONLY after G0 read the frozen pre-registration, so no row here
    can be blaming it. The pre-fix rows said ``evidence_kind="refusal"`` and
    ``blocked_by=OWNERS[cid]`` — i.e. E1/E2 named ``#9075``, the
    pre-registration issue this run had just read successfully — on a run that
    completed every step. That calls a measurement a refusal and sends the next
    lane to a landed issue instead of the measurement lane (#9089 review), the
    exact misattribution ``ALSO_BLOCKED_BY`` exists to prevent.

    Factored out of ``main()`` because G6 is unreachable while #9116 keeps the
    receipt witnessless: a fix that only lives inside dead code is a fix
    nothing can pin.

    E4 alone carries ``numerator``/``denominator`` (from ``receipt``). The other
    four criteria have no receipt on this surface yet, and a fabricated zero
    would read as a measurement; E4's numbers are the ones the run actually
    computed, so they belong in the row the gate reads.
    """
    rows: list[dict] = []
    for cid in ("E1", "E2", "E3", "E4", "E5"):
        owner = ALSO_BLOCKED_BY.get(cid, OWNERS[cid])
        rows.append({
            "criterion": cid, "verdict": "BLOCKED",
            "evidence_kind": "measurement",
            "evidence": evidence or str(EVIDENCE_DIR / "capstone.log"),
            "sha": head,
            # ⛔ E4's row carries the receipt's OWN numbers. `numerator` /
            # `denominator` used to be None for every row while the receipt sat
            # in `run.e4_receipt` and in the note — so the gate's V10 ("a
            # measured E4 whose denominator is 4 is the defect") could not fire
            # on the only table this probe can produce: the one number that
            # decides E4 was written in three places and read from none. The
            # row is the number's home; `check()` reads it from here.
            "numerator": receipt["numerator"] if cid == "E4" else None,
            "denominator": receipt["denominator"] if cid == "E4" else None,
            "corpus_sha256": corpus_sha,
            "sibling": None, "blocked_by": owner,
            "note": "the run completed all eight steps; this criterion's "
                    "evidence artifact is still owned by " + owner,
        })
        if cid in ALSO_BLOCKED_BY:
            rows[-1]["note"] += (f" / the pre-registration half is "
                                 f"{OWNERS[cid]}")
        rows[-1]["note"] += _landed_suffix(cid)
        rows[-1]["sibling"] = "E2" if cid == "E1" else ("E1" if cid == "E2"
                                                       else None)
    for row in rows:
        if row["criterion"] == "E4":
            row["note"] += (f"; receipt carried: kept_from_s2="
                            f"{receipt['numerator']}/"
                            f"{receipt['denominator']}"
                            + (", denominator 4 — the number E4 refuses"
                               if receipt["denominator_is_4"]
                               else ""))
    return rows


def emit_log(*, head: str, prereg: dict, corpus: dict, run: dict,
             rows: list[dict], out: Path | None) -> str:
    """Render the run log — human record on top, machine table at the bottom."""
    for row in rows:
        if not row.get("sha"):
            row["sha"] = head
    record = {
        "schema": SCHEMA,
        "issue": 9080,
        "epic": 7949,
        "head": head,
        "prereg": prereg,
        "corpus": corpus,
        "run": run,
        "rows": rows,
    }
    lines = [
        "# extractor-v4 capstone — S0 → S6 on the pre-registered corpus (#9080)",
        f"# generated: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}",
        f"# head: {head}",
        f"# pre-registration: {prereg.get('sha')} ({prereg.get('status')})",
        f"# corpus: {corpus.get('path')} sha256={corpus.get('sha256')} "
        f"turns={corpus.get('turns')}",
        f"# run: {run.get('status')} — {run.get('reason')}",
    ]
    observed = run.get("steps_observed") or []
    lines.append("# steps observed: "
                 + (" ".join(observed) if observed else "(none)")
                 + f"  required: {' '.join(STEPS)}")
    missing = [s for s in STEPS if s not in observed]
    if missing:
        lines.append("# steps MISSING: " + " ".join(missing))
    lines += ["", "## Verdict", "",
              "| criterion | verdict | evidence | blocked_by | note |",
              "|---|---|---|---|---|"]
    for row in rows:
        lines.append(f"| {row['criterion']} | {row['verdict']} | "
                     f"{row['evidence']} | {row.get('blocked_by') or '—'} | "
                     f"{row['note']} |")
    lines += ["", BEGIN, json.dumps(record, indent=2, sort_keys=True), END, ""]
    text = "\n".join(lines)
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text)
    return text


# ── git / pre-registration ────────────────────────────────────────────────

def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True,
                          text=True, check=False)


def resolve_head(override: str | None) -> tuple[str, str]:
    """The commit this run is measured at, and why an override is unusable.

    ``--head`` exists for ONE case: re-stamping a run at a commit that is not the
    worktree's current ``HEAD`` but describes the SAME TREE (a re-run against a tag, or
    the same tree checked out elsewhere). It is the single value every row is stamped
    with and the value V5's ancestry rule resolves against the pre-registration, so an
    override is held to the same test as a row sha in
    ``tools/capstone_verdict_check.py`` (V3): it must LOOK like a sha, resolve to a
    commit in this repo, and name the tree this process actually executed. Anything
    else refuses instead of being written into a shipped evidence file (the pre-fix
    code stamped it verbatim, and a `git rev-parse HEAD` in an unborn-HEAD repo wrote
    the literal string ``HEAD``).

    Returns ``(head, error)``; ``error`` is empty exactly when ``head`` is
    usable. With no override the worktree's ``HEAD`` is used as before, but it is
    VALIDATED too: a repo whose ``HEAD`` does not resolve to a commit cannot stamp a
    table, because a row stamped ``HEAD`` is red by V3.

    ⛔ On an UNUSABLE override ``head`` is the worktree's ``HEAD``, never the
    string ``"unknown"``: the refusal still emits a TABLE, and the acceptance
    command pipes that table over the committed evidence file, so a stamp no
    git rev-parse can resolve would replace the shipped log with one this
    repo's own gate (V3) reds. The refusal IS a fact measured at this
    checkout, so that is the sha it carries; the rejected override survives in
    the refusal reason. When the worktree itself has no resolvable ``HEAD`` there
    is no such fact, so ``"unknown"`` is returned WITH an error — the run refuses
    before G0 and no unstampable table is offered as an override result.
    """
    head_sha = _git("rev-parse", "--verify", "HEAD").stdout.strip()
    if not HEAD_SHA_RE.match(head_sha or ""):
        return "unknown", (
            "this checkout has no HEAD that resolves to a commit (unborn HEAD, or not a "
            "git work tree) — every row of the table is stamped with the measured commit "
            "and V5 resolves the pre-registration's ancestry against it, so there is no "
            "honest value to stamp; check out a commit and re-run")
    if override is None:
        return head_sha, ""
    if not HEAD_SHA_RE.match(override):
        return head_sha, (
            f"--head {override!r} is not a commit sha (7-40 lowercase hex "
            "digits) — every row is stamped with it and V5 resolves the "
            "pre-registration's ancestry against it, so a free-text override "
            "would make the provenance chain unattributable")
    if _git("cat-file", "-e", f"{override}^{{commit}}").returncode != 0:
        return head_sha, (
            f"--head {override!r} does not resolve to a commit in this repo — "
            "an unresolvable sha cannot be the commit this run was measured "
            "at (pass the full sha, or drop --head to use the worktree HEAD)")
    # ⛔ THE OVERRIDE MUST NAME THE TREE THIS PROCESS ACTUALLY EXECUTED (#9089 review P2).
    # The checks above prove the string is a sha that resolves — not that the CODE THAT RAN is
    # that revision. `--head HEAD~5` from an unmodified checkout produced a table whose every
    # row was stamped `HEAD~5` while the run was executed on `HEAD`, so the evidence bundle would
    # record "measured at X" for code that never ran at X, and V5 then resolves the
    # pre-registration's ancestry against that false stamp. The honest test is the TREE: a re-run
    # against a tag, or the same tree checked out elsewhere, has the same `^{tree}` as the
    # worktree's HEAD and stays legitimate; a different tree is a different program, so it is
    # refused. (Uncommitted worktree edits are not seen by this test — stated, not implied.)
    head_tree = _git("rev-parse", "HEAD^{tree}").stdout.strip()
    override_tree = _git("rev-parse", f"{override}^{{tree}}").stdout.strip()
    if not head_tree or override_tree != head_tree:
        return head_sha, (
            f"--head {override!r} is a DIFFERENT tree from this checkout's HEAD — the run "
            "executes the code that is here, so stamping its rows with a revision it never "
            "ran on would make the provenance chain claim a measurement nobody took (a tag or "
            "a checkout of the SAME tree is accepted; check the revision out instead)")
    return override, ""


def read_preregistration(sha: str, artifact: Path,
                         head: str | None = None) -> tuple[dict | None, str]:
    """Read the pre-registration AS IT WAS at ``sha`` — never from the worktree.

    Reading it out of the frozen commit is the whole property: a threshold
    edited after the numbers are in is not a pre-registration, and a worktree
    read would happily serve that edit.

    ``head`` is the commit the rows are STAMPED with (``resolve_head``'s
    result); ancestry is resolved against it, never against the worktree's
    ``HEAD``. With ``--head <tag-commit>`` the two differ, and the old
    worktree-HEAD form then accepted a pre-registration committed AFTER the
    measured commit — emitting a table whose every row is stamped with a
    pre-threshold commit, which is exactly the state V5's "evidence PREDATES
    the pre-registration" rule exists to refuse (#9089 review).
    """
    if _git("cat-file", "-e", f"{sha}^{{commit}}").returncode != 0:
        return None, f"pre-registration commit {sha!r} does not resolve in this repo"
    measured = head or _git("rev-parse", "HEAD").stdout.strip()
    if measured and _git("merge-base", "--is-ancestor", sha,
                        measured).returncode != 0:
        return None, (f"pre-registration {sha} is not an ancestor of {measured} "
                      "— thresholds must be fixed before the run, not on a "
                      "branch the run cannot see")
    blob = _git("show", f"{sha}:{artifact.as_posix()}")
    if blob.returncode != 0:
        return None, (f"commit {sha} does not carry {artifact.as_posix()} — "
                      "there is no pre-registration to measure against (#9075)")
    try:
        record = json.loads(blob.stdout)
    except json.JSONDecodeError as e:
        return None, f"{artifact.as_posix()} at {sha} is not JSON: {e}"
    # ── TYPE guards, before anything indexes into it (#9089 review F3).
    # The record's producer is another lane, so its SHAPE is untrusted input:
    # a root that parses as ``[]`` used to raise ``AttributeError`` out of
    # ``main()``, which is the worst of both worlds here — the acceptance
    # command is ``capstone_run.py … | tee capstone.log``, so a traceback (stderr
    # only, exit 1) truncates the committed evidence log to an EMPTY file and
    # leaves no table saying why. Every arm below therefore REFUSES (a G0
    # refusal: exit 2, a full verdict table on stdout), the same shape the
    # non-JSON arm above already uses.
    if not isinstance(record, dict):
        return None, (f"{artifact.as_posix()} at {sha} is a JSON "
                      f"{type(record).__name__}, not an object — a "
                      "pre-registration is a schema object, so there is no "
                      "threshold to read")
    if record.get("schema") != PREREG_SCHEMA:
        return None, (f"{artifact.as_posix()} at {sha} declares schema "
                      f"{record.get('schema')!r}, expected {PREREG_SCHEMA!r}")
    # ⚠️ TWO claim spellings, because the producer's spec moved under this probe.
    # #9075 LANDED its record on `main` (#9507, artifact at
    # `docs/evidence/7949-capstone/preregistration.json`), and that spec
    # (`PRE-REGISTRATION.md` check 3) deliberately requires
    # ``written_before_any_scored_run`` **with its basis stated** — the
    # unqualified form is refused there because it is false the moment any log
    # exists. This probe predates the landing and demanded only the old name, so
    # it refused the ONE legitimate record that exists and could never read a
    # pre-registration at all — an inert gate. Both are accepted now; the landed
    # form additionally requires a non-blank basis, because a scored-run claim
    # with no stated reason is the bare boolean the spec rejects.
    scored = record.get("written_before_any_scored_run")
    if scored:
        basis = record.get("written_before_any_scored_run_basis")
        if not isinstance(basis, str) or not basis.strip():
            return None, (
                f"{artifact.as_posix()} at {sha} asserts "
                "written_before_any_scored_run without stating its basis — the "
                "claim is only checkable against the reason it gives")
    elif not record.get("written_before_any_run"):
        return None, ("the pre-registration asserts neither "
                      "written_before_any_scored_run (with its basis) nor the "
                      "legacy written_before_any_run — a threshold without "
                      "that claim may have been chosen to fit the number")
    frozen = record.get("corpus")
    if frozen is not None and not isinstance(frozen, dict):
        return None, (f"{artifact.as_posix()} at {sha} declares corpus as a "
                      f"{type(frozen).__name__}, not an object — G1 compares "
                      "corpus.sha256 / corpus.items against the run's corpus")
    if isinstance(frozen, dict) and frozen.get("items") is not None:
        items = frozen["items"]
        if isinstance(items, bool):
            return None, (f"{artifact.as_posix()} at {sha} freezes "
                          f"corpus.items as a BOOLEAN ({items!r}) — a turn "
                          "count is not a flag")
        if isinstance(items, float) and not items.is_integer():
            return None, (f"{artifact.as_posix()} at {sha} freezes "
                          f"corpus.items as {items!r} — a fractional turn "
                          "count is not a count")
        try:
            # Normalised HERE, once, so G1's `int(frozen["items"])` cannot be
            # the thing that raises: a non-numeric value (``"fifteen"``) is a
            # malformed record, not a run-time input error.
            frozen["items"] = int(items)
        except (TypeError, ValueError):
            return None, (f"{artifact.as_posix()} at {sha} freezes "
                          f"corpus.items as {items!r}, which is not a turn "
                          "count")
    return record, ""


def backend_is_searchable() -> tuple[bool, str]:
    """S3 needs a SEARCHABLE backend or the whole table is structurally void.

    `sdk.capture_session`'s own contract: over embedded/FalkorDBLite the S3
    search is skipped, so supersession records are ZERO structurally. A capstone
    run there would measure the embedded lane's absence of S3 and report it as
    the product's — so this refuses instead.

    The decision is the extractor's OWN gate, not a string test: `config.
    is_db_uri()` accepts only ``docker://`` / ``redis://`` / ``rediss://``, and
    `extractor_v2.resolve_backend_mode()` reports anything else as ``embedded``
    (``TORTOISE_API_URL`` set and no supported DB URI → ``hosted``). A
    hand-rolled heuristic drifts in BOTH directions (#9080 review): it ACCEPTED
    ``TORTOISE_DB_URI=/tmp/graph.db`` — a file path, which `is_db_uri` rejects
    and the SDK therefore treats as the EMBEDDED store, S3 skipped — and it
    REFUSED a hosted lane. No SDK exists yet at G2, so the env-derived label is
    the authoritative half of `_is_searchable_backend` here.
    """
    from tortoise.extractor_v2 import _is_searchable_backend, resolve_backend_mode

    uri = os.environ.get("TORTOISE_DB_URI")
    mode = resolve_backend_mode()
    if _is_searchable_backend(mode):
        return True, ""
    if not uri and not os.environ.get("TORTOISE_API_URL"):
        return False, ("neither TORTOISE_DB_URI nor TORTOISE_API_URL is set — "
                       f"the backend is {mode!r} (the embedded store), where "
                       "S3 does not search")
    return False, (f"TORTOISE_DB_URI={uri!r} resolves to the {mode!r} backend — "
                   "S3 does not search there, so supersession records would be "
                   "structurally zero (a supported scheme is docker/redis/"
                   "rediss, or set TORTOISE_API_URL for the hosted lane)")


def model_key_present() -> tuple[bool, str]:
    """Without a provider key `capture_session` SKIPS extraction and stores turns.

    That is a documented behaviour (#3892), not an error — but it means S1–S6
    never run, so measuring it would report a storage write as an extraction.

    The predicate is the SAME one `_extract_session_v2` gates on (#1530), not a
    hand-written key list (#9080 review): `resolve_extractor_provider()` reads
    DEEPSEEK/OPENROUTER/VENICE, and ``TORTOISE_SESSION_LLM_MOCK=1`` is the
    offline seam the repo's e2e lanes use (#1468). A list that also accepted
    OPENAI/ANTHROPIC/TORTOISE_LLM_API_KEY passed hosts the adapter cannot
    consume — they then died at G4 blaming the entry point — and refused a
    mock-seam host that would have run.
    """
    from tortoise.model_adapters import resolve_extractor_provider
    from tortoise.sdk import _session_llm_mock_enabled

    if _session_llm_mock_enabled():
        return True, ""
    try:
        primary = resolve_extractor_provider()[0]
    except ValueError as e:  # explicit TORTOISE_EXTRACTOR_PROVIDER, keyless
        return False, ("the extractor provider is explicit but its key is "
                       f"absent — the adapter fails closed: {e}")
    if primary:
        return True, ""
    return False, ("no routing-usable LLM provider key is set (DEEPSEEK_API_KEY "
                   "/ OPENROUTER_API_KEY / VENICE_API_KEY via "
                   "resolve_extractor_provider, or TORTOISE_SESSION_LLM_MOCK=1) "
                   "— capture_session would SKIP extraction (#3892) and S1–S6 "
                   "would not run")


# ── the run ───────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Run the extractor-v4 capstone (S0→S6) once (#9080).")
    ap.add_argument("--prereg", required=True,
                    help="the frozen pre-registration commit")
    ap.add_argument("--corpus", required=True, help="the pre-registered corpus")
    ap.add_argument("--prereg-artifact", default=str(DEFAULT_PREREG_ARTIFACT),
                    help=f"path of the pre-registration record (default "
                         f"{DEFAULT_PREREG_ARTIFACT})")
    ap.add_argument("--out", default=None,
                    help="also write the log here (stdout always gets it)")
    ap.add_argument("--head", default=None,
                    help="re-stamp the run at another commit of the SAME tree "
                         "(a tag, or this tree checked out elsewhere) — must be "
                         "a 7-40 hex sha that resolves to a commit here whose "
                         "^{tree} equals this worktree's HEAD^{tree}; every row "
                         "is stamped with it")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    corpus_path = Path(args.corpus)
    out = Path(args.out) if args.out else None
    # Bound before ``refuse`` so the closure always has a value; the real
    # resolution happens after the closure exists, so an unusable override can
    # still emit a refusal TABLE rather than a traceback.
    head = "unknown"

    def refuse(reason: str, *, prereg_ok: bool, code: int,
               prereg: dict | None = None, corpus: dict | None = None,
               prereg_unread: bool = False) -> int:
        """Record a refusal as a verdict table and return the gate's exit code.

        ``prereg_ok`` is not a label for the reason — it IS "G0 read the
        pre-registration successfully". Every gate after G0 passes True, so the
        recorded ``prereg.status`` must follow the SAME fact: only the G0
        refusal may write ``ABSENT``. Writing ``ABSENT`` on a G1–G5 refusal
        would tell the next lane that `#9075` is still open when the run had
        already read the frozen thresholds. Pinned by
        tests/test_7949_capstone_verdict_check.py::
        test_post_g0_refusals_record_the_preregistration_as_read.
        """
        rows = refused_rows(reason, prereg_ok=prereg_ok,
                            prereg_unread=prereg_unread,
                            evidence=evidence_path(out))
        # Three states, because there are three facts: G0 read it (OK), G0
        # looked and it was not there (ABSENT), or the gate stopped BEFORE G0 —
        # in which case `ABSENT` would assert something this run never checked.
        status = ("UNREAD" if prereg_unread
                  else ("OK" if prereg_ok else "ABSENT"))
        text = emit_log(
            head=head,
            prereg=prereg or {"sha": args.prereg, "status": status,
                              "artifact": args.prereg_artifact},
            corpus=corpus or {"path": args.corpus, "sha256": None, "turns": None},
            run={"status": "REFUSED", "reason": reason, "steps_observed": [],
                 "steps_required": list(STEPS),
                 "gate_failed": code},
            rows=rows, out=out)
        sys.stdout.write(text)
        return code

    # G-1 — the measured commit is a fact, not an argument. Refused BEFORE G0
    # because an unusable ``--head`` makes every stamp and V5's ancestry check
    # meaningless, whatever the pre-registration says (see resolve_head).
    head, head_error = resolve_head(args.head)
    if head_error:
        # G-1 stopped BEFORE G0, so the refusal must not claim anything about
        # the pre-registration: `prereg_unread` keeps `ABSENT` (a claim about a
        # check nobody ran) and `UNPREREGISTERED` (a claim that the prereg is
        # the missing thing) out of the table (#9089 review).
        return refuse(head_error, prereg_ok=False, code=2, prereg_unread=True)

    # G0 — the pre-registration (FAIL-CLOSED, before any model call).
    # `head` is threaded in: G0's ancestry test is about the commit the evidence
    # is stamped with, which `--head` can move off the worktree HEAD.
    prereg_record, why = read_preregistration(
        args.prereg, Path(args.prereg_artifact), head)
    if prereg_record is None:
        return refuse(why, prereg_ok=False, code=2)

    # G1 — the corpus is the corpus that was frozen
    if not corpus_path.is_file():
        return refuse(f"corpus {args.corpus} does not exist", prereg_ok=True,
                      code=2)
    sha = corpus_sha256(corpus_path)
    # The sha is COMPUTED, so every G1 refusal below carries it: a refusal that
    # discards a sha it already has leaves the log saying "no corpus" about a
    # corpus it read (the row's denominator would be unattributable later).
    corpus = {"path": args.corpus, "sha256": sha, "turns": None}
    frozen = prereg_record.get("corpus") or {}
    # FAIL CLOSED on a pre-registration that freezes no corpus (#9080 review):
    # both checks used to be conditional, so a record carrying thresholds but no
    # `corpus` block made G1 a no-op — the run then measured whatever corpus it
    # was handed and stamped that corpus's sha on every row as if it were the
    # pre-registered one. V7 cannot catch that: it compares a row's sha to the
    # log's own, which agree by construction.
    if not frozen.get("sha256"):
        return refuse(
            "the pre-registration freezes no corpus.sha256 — G1 would be a "
            "no-op, so the run would measure an arbitrary corpus and report it "
            "as the pre-registered one", prereg_ok=True, code=2, corpus=corpus)
    if frozen["sha256"] != sha:
        return refuse(
            f"corpus sha256 {sha} != the pre-registered {frozen['sha256']} — "
            "a run on a different corpus cannot decide a threshold frozen "
            "against this one", prereg_ok=True, code=2, corpus=corpus)
    try:
        turns = load_corpus(corpus_path)
    except (ValueError, json.JSONDecodeError) as e:
        return refuse(f"corpus is an INPUT error, not retried: {e}",
                      prereg_ok=True, code=2, corpus=corpus)
    corpus["turns"] = len(turns)
    if frozen.get("items") and int(frozen["items"]) != len(turns):
        return refuse(
            f"corpus has {len(turns)} turns, the pre-registration froze "
            f"{frozen['items']}", prereg_ok=True, code=2, corpus=corpus)

    # G2 / G3 — a run that cannot execute every step must not report a table
    ok, why = backend_is_searchable()
    if not ok:
        return refuse(why, prereg_ok=True, code=3, corpus=corpus)
    ok, why = model_key_present()
    if not ok:
        return refuse(why, prereg_ok=True, code=3, corpus=corpus)

    # G4 — ONE pass through the REAL entry point
    from tortoise.sdk import TortoiseSDK  # imported late: the gates above need no client

    sdk = TortoiseSDK()
    # ⛔ #4282 deleted the entry point. The `except Exception` below would already turn
    # the `AttributeError` into a refusal, but it would report "the entry point raised" —
    # which reads as a bug in the pass rather than as the authorised deletion it is. Name
    # it, and name the surviving door.
    if not hasattr(sdk, "capture_session"):
        return refuse("#4282 deleted TortoiseSDK.capture_session — the entry point G4 "
                      "drives does not exist, so no pass can be observed; the surviving "
                      "in-process door is the private sdk._extract_session_v2, which the "
                      "hosted handler still reaches, and a measurement against it is a "
                      "NEW instrument with its own pre-registration",
                      prereg_ok=True, code=4, corpus=corpus)
    session_id = f"capstone_{uuid.uuid4().hex[:12]}"
    try:
        captured = sdk.capture_session(turns, session_id=session_id)
        # The receipt IS the only surface. ``capture_session`` never returns an
        # ``extraction`` key (sdk.py assembles ``resp`` from ``extracted`` +
        # ``meta``), so the old ``captured.get("extraction") or captured`` was a
        # DEAD lookup whose fallback silently hid the shape mismatch — which is
        # how four unobservable witnesses went unnoticed (#9116, review F1).
        result = captured
    except Exception as e:  # noqa: BLE001, RUF100 — a refused run is a verdict, not a crash
        return refuse(f"the entry point raised: {type(e).__name__}: {e}",
                      prereg_ok=True, code=4, corpus=corpus)
    if not (result.get("stats") or {}).get("llm"):
        return refuse(
            "the pass returned no extractor stats — capture_session did not "
            "reach extract_session_v2 (extraction_mode="
            f"{captured.get('extraction_mode')!r}); no step can be observed",
            prereg_ok=True, code=4, corpus=corpus)

    # G5 — EIGHT steps, or the table is not the epic's table
    observed = observe_steps(result)
    missing = [s for s in STEPS if s not in observed]
    if missing:
        # THREE findings, not two (#9116). A receipt with NO step_witness at
        # all is the INSTRUMENT: the seam never delivered, so nothing down
        # there is observable and the four steps that need it must NOT be read
        # as "the pipeline skipped VET/classify/search/commit". A receipt whose
        # seam is present but MISSING a block is a defect in the seam itself —
        # `_capture_step_witness` — and must not be published as the pipeline's
        # own report (`f"{s}: {}"`) while asserting it accounts for the step. A
        # receipt WITH every block, where a stage still reports no work, is the
        # pipeline's own answer: the stage ran (or was gated off) and the witness
        # says which. `capstone_measure._seam_cause` keeps the same three states,
        # so the two probes cannot grade one fact oppositely (#10214 review, P3).
        witness = (result.get("stats") or {}).get("step_witness")
        if not isinstance(witness, dict) or not witness:
            return refuse(
                "steps not observed in the pass: " + ", ".join(missing)
                + " — the receipt carries no stats['step_witness'] block, so"
                " the pass did not run through extract_session_v2 (#9116; the"
                " seam that copies vet/classify_later/search/payload onto the"
                " receipt); extraction_mode="
                f"{result.get('extraction_mode')!r}. This is an INSTRUMENT gap"
                " and NOT the pipeline skipping those steps; §4.4 forbids a"
                " table from a subset",
                prereg_ok=True, code=5, corpus=corpus)
        absent_blocks = [s for s in missing
                         if STEP_WITNESS_BLOCK.get(s) not in witness]
        if absent_blocks:
            return refuse(
                "steps not observed in the pass: " + ", ".join(missing)
                + " — stats['step_witness'] IS present but carries no "
                + ", ".join(f"{STEP_WITNESS_BLOCK[s]!r} block (for {s})"
                             for s in absent_blocks)
                + ": the seam is present and INCOMPLETE, which is a defect in"
                " _capture_step_witness (tortoise/sdk.py), NOT the pipeline"
                " skipping the step and not a missing surface; §4.4 forbids a"
                " table from a subset",
                prereg_ok=True, code=5, corpus=corpus)
        return refuse(
            "steps not observed in the pass: " + ", ".join(missing)
            + " — the pass DID run through extract_session_v2 and its own"
            " stats['step_witness'] accounts for the missing step(s): "
            + "; ".join(f"{s}: {_step_cause(witness, s)}" for s in missing)
            + ". This is the PIPELINE's own report (a flag gated the pass off,"
            " or the stage produced nothing), not a missing surface; §4.4"
            " forbids a table assembled from a subset",
            prereg_ok=True, code=5, corpus=corpus)

    # G6 — per-criterion evidence
    receipt = e4_receipt(result.get("stats") or {})
    rows = completed_rows(head=head, corpus_sha=sha, receipt=receipt,
                          evidence=evidence_path(out))
    sys.stdout.write(emit_log(
        head=head,
        prereg={"sha": args.prereg, "status": "OK",
                "artifact": args.prereg_artifact},
        corpus=corpus,
        run={"status": "COMPLETED", "reason": "eight steps observed in one pass",
             "steps_observed": [s for s in STEPS if s in observed],
             "steps_required": list(STEPS), "gate_failed": None,
             "session_id": session_id, "e4_receipt": receipt},
        rows=rows, out=out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
