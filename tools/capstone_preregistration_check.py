#!/usr/bin/env python3
"""The capstone pre-registration gate (#9075) — the check behind ``#7949``'s method constraint.

The epic states the constraint verbatim: *"Pre-register before running — corpus, split, scoring —
the discipline that made `#3011` usable."* A registration that is a document only is a document that
can be edited after the numbers land, and a threshold chosen after the run is the exact failure the
constraint exists to prevent. This tool is what makes the artefact executable.

It verifies six things, and it fails on each of them for a specific reason:

1. **Every criteria row carries a metric, a denominator AND a numeric threshold.** A row with a
   placeholder (*"to be decided"*, *"TBD"*, an em dash) is a row that will be decided later — which is
   the same failure as no registration at all. E1–E6 must each be present (E2 may be split into
   sub-rows, e.g. ``E2a``/``E2b``, and every sub-row must itself be complete). Only E2 takes
   sub-rows: ``E1b`` is E1's anti-gaming CONTROL, so a table carrying ``E1b`` without an ``E1`` row
   is the epic's central criterion deleted behind its guard (#9075 review r3 P2). ``E6`` was added to
   this set by ``#11673`` on the owner's 2026-11-05 ruling (``#7949`` comment ``6664402530``); it is
   REQUIRED rather than registered-not-required because the ruling names it as the capstone's sixth
   criterion, and it carries a countable bar, so it never joins ``REGISTERED_NON_BAR_ROWS``.
2. **The registration was PUBLISHED on the base before every SCORED evidence log.** The
   pre-registration's earliest add-commit *reachable from the base ref* (`origin/main`) must be
   **older** than the evidence log's add-commit — where the log's add-commit is read on the base
   when the log is already merged there, and in this contribution when it is not.
   "Same second" is not "before" — an ordering that cannot be shown is not an ordering.

   ⛔ **WHY THE BASE AND NOT THIS PR (#10463).** The claim used to compare two add-commits *inside
   one contribution*, which is **not squash-invariant**: a squash merge rewrites those commits into
   one, so the same tree passed its PR checks and redded `main` minutes later (`#10468` — a
   registration and its log landed in one commit, both epochs equal). A gate whose verdict depends
   on the merge strategy enforces nothing *before* the merge. A pre-registration is a **frozen,
   hash-pinned, published artefact** (`preregistration.json` already pins `document_sha256`), and
   the "before" relation holds between published artefacts — the base is the only side a squash
   cannot rewrite.

   ⚠️ **What this arm does NOT prove, stated so it is not read as more than it is.** It establishes
   *when* the registration became an artefact on the base. It does NOT establish that the
   registration was not **edited** by the same contribution that carries the log: only *add*
   commits are read (`--diff-filter=A`), so a change that rewrites a threshold and lands a scored
   log together is accepted by this check. That gap is pre-existing (the exemption arm has the
   `_touch_epochs` content bound; the ordinary arm never had one), closing it is a NEW RULE that
   needs an owner decision, and it is tracked as **#10555** rather than invented here.

   ⚠️ **And it does not prove the log's history either — the same hole on the LOG side.** Because
   the log's own add-commit is what the ordering compares, a log that existed before the
   registration can be **moved or copied to a new path**, and the measured add is then the rename
   commit, which is later than the registration: it passes. `SQUASH_COLLAPSE_REMEDY` recommends
   exactly that shape ("a NEW artefact at a NEW path"), which is correct for a genuinely re-run
   measurement and unguarded for a copy of a pre-registration score. This is the same class as
   #10555 (an add-only read cannot see a content move), it is **pre-existing**, and it is left
   open rather than papered over: closing it is a NEW RULE — a rename-following read, or a content
   bound on every measured log — which needs an owner decision, not a lane invention.

   **The refusal this buys, at PR time rather than after the merge.** A change that adds a
   registration *and* a log has nothing on the base to appeal to, so it is **REFUSED**: the
   registration must land first, in its own PR. That is the arm that stops a contribution instead
   of reddening `main` afterwards, and it is tested by
   `test_a_registration_added_by_the_same_change_is_refused` (the identifier is kept on ONE line so
   it can be grepped — a name split across a line break reads as coverage while resolving to
   nothing).

   **The ATOMIC ADD, and the rule this branch does NOT take by default (#10529 / #10546).** A repo
   that squash-merges collapses an ordered branch's two commits into one, so
   ``git log --diff-filter=A`` reports ONE add-commit for the registration and its log, and no epoch
   comparison can order them (``#10468`` was merged that way). This branch proposes answering that
   with a DISCLOSURE (``atomic_add_ordering`` in ``preregistration.json``) instead of a refusal —
   ⛔ and that is a shape a RECORDED RULING REFUSES, so it is off by default and unreachable from CI:

   * the ruling is ``#10463`` comment ``6564634454`` (owner-requested): item 1 *"Do not relax the
     guard. Keep 'same second is not before — an ordering that cannot be shown is not an ordering.'"*,
     item 4 *"Change WHAT the guard compares"* — the registration must already be on the merge base —
     with the marker **OVERRIDES:** *deriving the ordering from commits inside the pull request,
     rather than from the base branch*;
   * item 4 is IMPLEMENTED on the base by ``#10548``, and that rule is what ``check_ordering`` runs for
     every unarmed caller;
   * the reopen this proposal needs (requested at ``6566767704``) is UNGRANTED, so the proposal is
     reachable only behind ``--allow-atomic-disclosure`` — see ``check_ordering``, which keeps the two
     rules whole instead of splicing contradictory semantics onto the same lines.

   Under the ARMED rule the verdict is decided by COMMIT IDENTITY and ANCESTRY, never by ``%ct``:
   the log's add-commit being an ancestor of the registration's is a HARD FAIL no disclosure can
   waive; the registration's being the ancestor is correctly ordered; the SAME commit is ATOMIC and
   carries no information, so it requires a validated disclosure instead of a violation it cannot
   prove. ``branch_commits`` need not resolve (a squash leaves them unreachable) but ``squash_merge``
   must, and must be the pair's add-commit or a descendant of it. A disclosure for a log that is NOT
   atomic, or that is not present, fails on BOTH paths — a waiver that is routine is the silent-pass
   shape.

   **Two different failures, two different codes — and the distinction is load-bearing.** A
   SHALLOW clone is the documented third outcome (`UNPROVABLE`, exit 0): CI test jobs are shallow by
   design and refusing there would red a correctly ordered tree. A checkout with a remote but NO
   resolvable base ref is NOT that: it raises a plain `CheckError` (exit 2). Folding it into the
   third outcome made the tool exit 0 while it could not name its base — a fail-open, found by
   review cycle 1 and closed in review cycle 2.

   **The one exception, and why it is not a loophole.** ``e1-e2.log`` was landed by ``#9110``
   *before* this registration existed, from a lane that correctly refused to score it. A bare date
   comparison can therefore only report a permanent failure, which is not a check but a stuck
   light. The exemption is granted per-log in the pins (``pre_existing_unscored_logs``) and is
   bounded on all five sides, so it cannot be used to launder a scored number:

   a. the exempt log must genuinely **predate** this file (a postdating log needs no exemption, and
      one granted to it would hide a real ordering failure);
   b. the exempt log's **content** must not have been touched at or after this file's add-commit.
      The add-commit is immutable, so on its own it cannot see an in-place edit: a log could be
      re-committed with a fabricated measurement after the thresholds were written and still look
      exempt. This bound reads the LAST commit touching the log, not just the one that added it;
   c. the exempt log must **declare itself unscored on its own line** — ``prereg=UNPREREGISTERED``
      read out of the log, never out of this document, so the exemption cannot claim a status the
      log does not carry;
   d. **every** ``verdict=``/``result=`` value in it must be ``UNPREREGISTERED`` and every
      ``exit=`` must be ``3``. A whitelist, not a list of four known-bad strings: a closed bad-list
      admits the next scored vocabulary with no code change, which is the inverse of the gap the
      exemption exists to close;
   e. an exemption naming a log that **does not exist** fails, so an exempt set cannot grow by
      keeping stale rows around to be reused later.

   The effect is the property the epic actually wants: **no number is scored against a threshold
   whose author could already see it**. An exempt log stays unscored forever; the moment E1/E2 are
   re-run, the new log must postdate this file or the check fails.

   **The third outcome.** In a SHALLOW clone (CI test jobs check out at ``fetch-depth: 1``) git grafts
   the boundary commit, so every path reports as added by HEAD and both epochs collapse onto one
   number: "ordering violated" and "ordering unprovable" become indistinguishable. The check then
   reports ``UNPROVABLE — shallow clone`` in the report's ``ordering`` field and raises
   ``HistoryUnavailable`` from ``check_ordering`` — a verdict it cannot support is never given, and
   the tests that assert a real-artefact PASS skip instead of red-ing the required check (#9075 r4 P1).
   The full-history verdict therefore runs where the history exists: CI's ``changes`` job (the one job
   that checks out at ``fetch-depth: 0``) invokes this tool, so the third outcome never means the
   ordering is unenforced (#9075 review r6 P1).
3. **E4's denominator is 15, never 4.** The planted-operator gold is 15 edges across all seven
   sessions; the 4-edge denominator it replaced swung 0–2 of 4 on *identical* code, so a four-edge
   rate is not evidence of anything. A ``N / 4`` RATE is rejected wherever it appears in the
   document, including in a historical aside — a document that writes the bad rate down is a
   document a later reader will copy it out of. The refusal is of the RATE specifically: the
   history may still be stated as a count (``2 of 4``), which is not a fraction anyone can quote as
   a result.
4. **The pins still describe the tree.** Every denominator in the registration (``fixtures_hash``,
   90 planted units, 15 operator edges, 14 value-bearing units, 29 marker sentences, the 25/15/9/6
   salience measurement, and the TUNE/HELD-OUT split) is **re-derived from the corpus** and compared
   with the pins block. Without this, a corpus change silently re-bases every threshold and the
   pre-registration becomes decoration.

5. **The machine record agrees with the argument.** ``preregistration.json`` is the record the
   evidence probe reads (``e1-e2.log`` names it on its ``prereg=`` line). It must parse, declare the
   ``capstone-preregistration/v1`` schema, carry the honest run-order claim —
   ``written_before_any_scored_run``, NOT the unqualified ``written_before_any_run``, which is false
   whenever any log exists and which a machine consumer keys on by name — and **agree with the pins
   block** on every number it restates. Two copies of a threshold that can disagree is how a gate
   reads one while a reviewer reads the other. It must also pin ``document_sha256``: the record is
   the artefact the run probe freezes, so an argument re-edited without the record moving with it is
   a document the record no longer describes (#9075 review r3 P3). `hypothesis_bars` is compared by
   the same rule, in both directions: the measured log names only five of the seven frozen bars, so
   the record's restatement is what holds the other two — `corroboration_min_sessions`, which decides
   the published `not-majority-of-sessions` qualifier, and the emitted-only `sessions_total`.

6. **The record may not contradict the history or the model it describes.** Two contradictions, one
   rule, because both are the same defect: a frozen artefact asserting something the tree already
   refutes, with no number changing to reveal it.

   * ``measurement_visible_to_author`` is a claim about **history** — the one thing a registry can be
     wrong about while staying internally consistent. E5 read ``false`` for a metric whose
     measurement had landed two days BEFORE the record (#9738). The field is therefore held to
     ``prior_measurements`` in BOTH directions: ``false`` with a citation is the falsified claim, and
     ``true`` with none is a claim no artefact backs.
   * **A ``false`` must carry its BASES** (``measurement_visible_to_author_basis``). A citation is
     only one of the two ways a ``false`` can be wrong: the other is that the measurement exists
     but no citation names it, and then NO arm can see it — ``prior_measurements`` is the only table
     this gate reads. E4 shipped in exactly that shape (#10525 review): the field was right
     (its receipt ran 2026-10-19, before the record, but landed at ``d50217273`` on 2026-10-22,
     after it, so nothing was in the tree the author read) and the reason was stated NOWHERE, while
     the §0 row beside it said "no measurement existed" — which the measurement's own date refutes.
     A bare ``false`` cannot be told apart from a stale one, by a reader or by a rule, so the ground
     is required.
   * **E3's class set** is re-derived from ``_DERIVATION_REFERENCES_LABELS`` in
     ``tortoise/projection/edges.py`` (plus ``Point``, which carries the anchor on the node). Naming
     ``:Object`` counts a class the model leaves unanchored BY DESIGN (#5199 option A), so ``= 1.000``
     is unreachable as registered and a run would report a recorded decision as a defect (#9736).
     Omitting an anchored class fails symmetrically.

What it deliberately does NOT do: it does not judge whether the thresholds are *right*. That is the
argument in the document, and an argument is reviewed, not grepped.

Usage::

    uv run python tools/capstone_preregistration_check.py --dir docs/evidence/7949-capstone
    uv run python tools/capstone_preregistration_check.py --dir <dir> --no-git   # pre-commit
    uv run python tools/capstone_preregistration_check.py --dir <dir> --json

Exit 0 only when every check passes. Exit 1 on any failure, 2 on a usage/parse error (no document,
unparseable pins) — a check that cannot read its subject must never report success.
"""

from __future__ import annotations

import sys

# #5128 shape: refuse a <3.12 interpreter BEFORE the imports below. `from
# __future__ import annotations` is deliberately the ONLY statement allowed
# above this — a module-level 3.12+-only construct would otherwise fail first,
# and it would fail with an unattributed traceback instead of this message. The
# gate that enforces this shape is tests/test_entry_point_python_guard.py, not a
# style rule; the position is load-bearing, so it is not cosmetic ordering.
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    # 2 is this tool's own "environment/usage error" code (`main` returns 1 only
    # for FINDINGS), so an old interpreter stays legible in a runner that keys on
    # the exit code alone — the same contract the sibling e1_e2_census_check.py
    # carries.
    print(
        f"tools/capstone_preregistration_check.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/capstone_preregistration_check.py`",
        file=sys.stderr,
    )
    raise SystemExit(2)

import argparse
import base64
import hashlib
import importlib
import json
import os
import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

PREREG_NAME = "PRE-REGISTRATION.md"
PINS_FENCE = "```yaml prereregistration-pins"
#: NON-RECURSIVE on purpose: the ordering half orders the logs of THIS directory. A log written
#: under ``probes/`` (where a re-run's output naturally lands) is not in the set — the glob matches
#: the prose "each ``*.log`` in this directory" exactly (#9075 review r5 note 3). Widening it is a
#: deliberate act, not a side effect of adding a subdirectory.
EVIDENCE_LOG_GLOB = "*.log"

#: The SCORED-measurement logs, by the name the measurement probe writes (``--out`` default is
#: ``capstone-measured-<head8>.log``). Exactly one may exist: the bundle's rule is that superseding
#: a run means RE-RUNNING the probe under the new head's filename and REMOVING the file it replaces
#: (``capstone.log`` is the separate REFUSAL record and is not matched here).
SCORED_LOG_GLOB = "capstone-measured-*.log"

#: The machine-readable record beside the document. The evidence probe already names this path on
#: its ``prereg=`` line, so the name is not this check's invention.
MACHINE_RECORD = "preregistration.json"
PREREG_SCHEMA = "capstone-preregistration/v1"

#: The bundle CI has always checked. A second pre-registered bundle (#10463's ablation) is added by
#: repeating ``--dir``: before that, no CI job read it at all, so its ordering rule and its
#: record↔pins cross-check never executed — and a docs-only diff (which runs only the ``tier1``
#: slice) is exactly the change that can move a threshold after the numbers landed (#10468 r6 F1).
DEFAULT_PREREG_DIR = "docs/evidence/7949-capstone"

#: The document's own sha256, restated in the record and REQUIRED there. ``git log`` bounds the
#: document only where history is available (a shallow checkout has none) and ``--diff-filter=A``
#: takes the OLDEST add-epoch, so a delete-and-re-add rewrite of the argument keeps the old epoch
#: (#9075 review r3 P3). The digest is git-free: a re-edited document whose record was not re-cut
#: fails this comparison, in every checkout.
DOCUMENT_SHA_PIN = "document_sha256"

#: The status a log carries when no threshold existed to score it against.
UNSCORED = "UNPREREGISTERED"

#: A RETIRED record field (#10215), kept ONLY so a record that still carries it is
#: reported as a dead key to delete. An earlier fix required it as an attestation of
#: the `steps` pin's authoring order; that was a gate satisfied by asserting it, so
#: the rule now dates the block from history instead and reads the deviation from the
#: argument's `## Deviations` section. No gate reads this field.
STEPS_PINNED_AFTER = "steps_pinned_after_scored_log"

#: Every ``verdict=``/``result=`` value in an exempt log must be this. A WHITELIST, not a blacklist
#: of four known-bad strings: a closed bad-list silently admits the next scored vocabulary, which is
#: the inverse of the gap the exemption exists to close (#9075 review P2).
#:
#: Four widenings, each found by a review and each the same CLASS of gap — a marker the whitelist
#: could not see:
#:   * ``\s*=\s*`` — a spaced ``verdict = PASS`` was a false NEGATIVE (r2);
#:   * ``VALUE_TRAILING`` — a punctuated ``result=UNPREREGISTERED,`` was a false POSITIVE that
#:     refused a harmless formatting variation with a misleading diagnosis (r2);
#:   * ``re.IGNORECASE`` — an uppercase ``RESULT=PASS`` escaped entirely (r3);
#:   * ``(?<!\w)`` in place of ``(?:^|\s)`` — the PREFIX anchor required whitespace, so a marker
#:     glued to punctuation (``(verdict=PASS)``, ``[result=GREEN]``, ``,verdict=FAIL``) produced no
#:     match at all and the exemption survived it (r4). A ``\b``-style lookbehind anchors on the
#:     TOKEN, so ``subverdict=`` is still not a marker while ``(verdict=`` is.
#: The ``prereg=`` line check stays case-SENSITIVE on purpose: it fails closed on ``PREREG=``.
SCORED_KEYS = re.compile(r"(?<!\w)(verdict|result)\s*=\s*(\S+)", re.MULTILINE | re.IGNORECASE)

#: Every occurrence of a status KEY, whether or not a value can be parsed after it. Compared against
#: ``SCORED_KEYS``' match count: a marker whose value cannot be read must fail CLOSED, never be
#: silently skipped as though the log did not carry one (r4).
SCORED_KEY_TOKENS = re.compile(r"(?<!\w)(verdict|result)\s*=", re.MULTILINE | re.IGNORECASE)

#: The same token anchor as ``SCORED_KEYS``, for the same reason: ``[exit=0]`` is an exit marker.
EXIT_KEY = re.compile(r"(?<!\w)exit\s*=\s*(\S+)", re.MULTILINE | re.IGNORECASE)

#: Punctuation a value may be glued to at end-of-token (``result=UNPREREGISTERED,``).
VALUE_TRAILING = ",;."

#: Where the writer states WHICH node classes carry a version anchor on their DERIVATION LINK —
#: ``_DERIVATION_REFERENCES_LABELS = frozenset({"Event", "Document"})`` at
#: ``tortoise/projection/edges.py``. E3's class set is re-derived from this constant rather than from
#: the registration's prose (#9736): the constant is what the model does, and the row must follow it.
DERIVATION_LABELS_PATH = Path("tortoise/projection/edges.py")
DERIVATION_LABELS_NAME = "_DERIVATION_REFERENCES_LABELS"

#: ``:Point`` carries the anchor on the NODE (#5038, ``extractedFrom``), so it is a derived class that
#: is not a *link* label — E3's numerator is ``Point`` plus every entry in the constant above.
SELF_ANCHORED_CLASSES = ("Point",)

#: The node LABEL each RETIRED relation spelling is actually stored on — the writer's own statement,
#: not this gate's reading (#10525 review P1). ``edges.py:738-744``: *"POST-D10 the ``:Document``
#: LABEL is retired (a document is a ``:Source``) … the stored edge is
#: ``(Source)-[:references]->(Source)`` either way, while the alias is what still marks it a
#: DERIVATION."* So a row may honestly spell the document-derivation class either way, and the
#: shipped census proves the ambiguity costs a real count: ``Document`` is ``0`` nodes.
#:
#: ⛔ THIS TABLE EXISTS SO THE GATE DOES NOT SETTLE THE QUESTION (#9736 is OWNER-GATED). The ruling
#: on #9736 is *"the honest set may be ``Point`` + ``Event`` + ``Source``-as-document … a question
#: for your ruling, not something a lane may settle by picking the one that parses."* A rule that
#: DEMANDED ``Document`` and REFUSED ``Source`` settled it — by turning the owner's open alternative
#: into a CI failure. The gate now accepts either member, and decides only what is mechanically
#: decidable: a cell that names NO document class at all still fails (that is #9736's own shape one
#: level down), and a cell that names an unanchored class (``Object``) still fails.
RETIRED_SPELLING_LABELS = {"Document": "Source"}

#: Every node class E3's metric/denominator may NAME, keyed by the token that appears in prose. A
#: class outside ``{Point} | labels | the retired spellings' labels`` is a class the model does not
#: anchor for this criterion: naming it makes the row's own numerator unable to reach its
#: denominator (#9736, #5199 option A). ``Source`` is IN for the reason ``RETIRED_SPELLING_LABELS``
#: states — it is the label the document derivation is stored on, not an extra class.
NODE_CLASS_TOKENS = {
    "point": "Point",
    "event": "Event",
    "document": "Document",
    "object": "Object",
    "source": "Source",
}

#: The capstone verdict bundle's machine record, as the log carries it. Read from the LOG's own
#: bytes — never from a pin — so a log edited into a scored one cannot keep riding an exemption.
#: ⛔ The markers are matched as COUNTS first: `capstone_verdict_check.extract_block` refuses a log
#: carrying more than one block ("`capstone_run.py | tee -a capstone.log` is enough" to make two),
#: and a reader that took the FIRST block would exempt a log whose SECOND block carries the number.
CAPSTONE_BEGIN = "=== CAPSTONE-VERDICT-BEGIN ==="
CAPSTONE_END = "=== CAPSTONE-VERDICT-END ==="
CAPSTONE_RECORD = re.compile(
    re.escape(CAPSTONE_BEGIN) + r"\s*(\{.*?\})\s*" + re.escape(CAPSTONE_END),
    re.DOTALL,
)

#: Every pinned corpus quantity the machine record restates and is cross-checked against. A REQUIRED
#: set: a pins block missing one of these is a failure, never a silently-skipped comparison.
#: The epic's eight TOP-LEVEL steps (`EXTRACTOR-V4-ARCHITECTURE.md` §4.1). Declared
#: here so the pre-registration's step partition is validated against the DESIGN's
#: own list rather than against itself: a check that compared the document with the
#: record alone would accept both being emptied (#10215).
CAPSTONE_STEPS = ("S0", "S1", "S2.1", "S2.2", "S2.3", "S3", "S5", "S6")

#: The fields a CONDITIONAL step's declaration may not omit. A `step:` alone is a
#: waiver: `condition` says WHEN it does not apply, `idle_evidence` says what a run
#: must show to claim it did not apply, `evidence_issue` says who measured that.
CONDITIONAL_STEP_FIELDS = ("condition", "idle_evidence", "evidence_issue")

CROSS_CHECKED_CORPUS_KEYS = (
    "fixtures_hash", "sessions", "seed", "schema_version", "planted_units",
    "planted_operator_edges", "value_bearing_units", "hedge_scope_sentences",
    "hedge_scope_sentences_total",
)

#: The rows EVERY bundle's registration must carry: the five slots `#9075` closed on, and the
#: vocabulary `docs/evidence/5087-hypothesis/PRE-REGISTRATION.md` shares with the capstone (*"the
#: checker's slot vocabulary"* — the same ids name DIFFERENT measurements there). ``E2`` may be
#: carried by sub-rows (``E2a``/``E2b``); the others must be matched EXACTLY. A prefix match for
#: every id let ``E1b`` (E1's anti-gaming control) stand in for a deleted ``E1`` — the epic's
#: central criterion gone while its guard kept the check green (#9075 review r3 P2).
#:
#: ⛔ TWO SETS, AND TWO DIFFERENT QUESTIONS — conflating them is what the `#11673` review cycle kept
#: catching. THIS one is what a bundle's RECORD must carry (`required_criteria_for` decides which of
#: the two a given bundle owes). The other is the epic's CLOSING set, `CRITERIA` in
#: ``tools/capstone_verdict_check.py``, and it is the FIVE-row sentence `#7949` records. A row can be
#: REQUIRED of the record and NOT closing, and `E6` is exactly that.
CORE_REQUIRED_CRITERIA = ("E1", "E2", "E3", "E4", "E5")
#: The CAPSTONE's required set — the core slots plus ``E6``.
#:
#: ⛔ ``E6`` IS A REQUIRED ROW AND THIS IS THE TOOL CHANGE THAT MAKES IT ONE (#11673). The owner
#: ruled on 2026-11-05 (``#7949`` comment ``6664402530``) that the capstone gains a sixth row —
#: *per-pack declared → writable → written* — and ``#11083`` built the census for it without
#: registering the row, so the epic's own acceptance criterion had no bar. A row that exists only in
#: the record is refused here until its id is in this tuple, and that is deliberate: this is the
#: gate that tells "the record describes a criterion" from "the record carries a bar for it".
#: ⚠️ REQUIRED HERE IS NOT CLOSING THERE: `E6` is in this tuple and in the scorer's
#: ``RECOGNISED_CRITERIA``, and deliberately NOT in the verdict tool's ``CRITERIA``, so the epic
#: closes on five (see `CRITERIA`'s note in ``tools/capstone_verdict_check.py``).
#: ``E6`` needs no exemption from the numeric arms — it has a countable bar (``= 0`` unbound and
#: unexplained rows), so it is NOT a member of ``REGISTERED_NON_BAR_ROWS`` and the assertion below
#: must stay satisfied.
REQUIRED_CRITERIA = (*CORE_REQUIRED_CRITERIA, "E6")
#: The ONLY rows the record registers as bar-less (`UNMEASURED(...)`, §3.1's A4). Pinned here so the
#: completeness gate can tell a registered non-bar from a required row that declares itself one —
#: the branch also skips both numeric arms, so an unscoped version was a false PASS (#10982).
#: Asserted disjoint from `REQUIRED_CRITERIA` at import: the two sets can never overlap.
REGISTERED_NON_BAR_ROWS = frozenset({"A4"})
assert not (REGISTERED_NON_BAR_ROWS & set(REQUIRED_CRITERIA)), (
    "a required row can never be a registered non-bar")

#: The one required id that may be carried by sub-rows.
SUB_ROW_CRITERIA = ("E2",)

#: Cell values that mean "decided later", i.e. not decided. Compared case-folded, stripped of
#: markdown emphasis and punctuation.
PLACEHOLDER_CELLS = frozenset(
    {"", "tbd", "tbc", "todo", "to be decided", "to be determined", "n/a", "na", "none",
     "-", "--", "—", "–", "?", "??", "..."}
)

#: A threshold must carry a comparator AND a number. Prose without both is not a threshold.
COMPARATORS = ("<=", ">=", "<", ">", "=", "≤", "≥")
#: A threshold that declares itself a REGISTERED NON-BAR (`UNMEASURED(<ground>)`, §3.1's A4 row).
#: Such a cell is complete only when its ground names an issue (`#NNNN`) that gates the row — and the
#: floor clause beside it may never be what satisfies the numeric arm (see `check_completeness`).
#: ⛔ WHITESPACE-TOLERANT (`\s*`), because the prose spelling `UNMEASURED (ground)` is the natural
#: one and a literal `UNMEASURED(` let it FALL THROUGH to the numeric arms, where the floor clause
#: beside it supplied both comparator and number — so a REQUIRED row (`E1`) reading
#: `UNMEASURED (…) above the floor of `>= 30`…` left the gate at OK with no bar at all. Measured
#: on a copied evidence dir at #11331 review round 6: exit 0/OK with the space, exit 1 without it.
#: The ONE definition, so every reader of this cell agrees with the branch that decides it.
REGISTERED_NO_BAR = re.compile(r"\bUNMEASURED\s*\(")
#: The `… above the floor of `` >= 30 `` …` clause a threshold cell may carry *beside* its bar.
#: STRIPPED before the comparator test on a registered non-bar, because a floor is a refusal
#: boundary and not a bar (§3.7, §7 item 1). ⛔ It strips the COMPARATOR THE FLOOR OWNS, not a run of
#: characters: an earlier `floor of[^;]*` ate everything to the next `;`, so a cell that declared its
#: real bar AFTER the floor clause read as bar-less — the arm could not fail on the property it names
#: (#10982 review round 3). ⛔ The alternation is BUILT FROM `COMPARATORS`, never re-typed: the hand
#: -written one omitted `≤`/`≥`, so a floor written `` `≥ 30` `` — the spelling the pinned record
#: itself uses at :268,:271,:272 — read as a bar and the row was refused as a false RED
#: (#10982 review round 4).
#: ⛔ GROUP 1 IS THE NUMBER, and it is what `_strip_the_rows_own_floor` binds on. The pattern alone
#: cannot tell a floor from a bar: the two words install ANY `comparator number`, so
#: `UNMEASURED(#10988) floor of >= 0.5 typed_share` left A4 — the one row whose registration is that
#: it has NO bar — with a bar and no finding, while the same comparator without the words was
#: refused. A RECORD-ONLY edit, which §7 item 1 says is impossible (#11331 review round 8).
FLOOR_CLAUSE = re.compile(
    "floor(?:\\s+of)?\\s*(?:"
    + "|".join(re.escape(c) for c in sorted(COMPARATORS, key=len, reverse=True))
    + r")\s*(\d+(?:\.\d+)?)",
    re.IGNORECASE,
)


#: The ONLY thing that may follow a row's floor UNIT: closing punctuation / whitespace, running to the
#: END of the cell. ⛔ AN ALLOWLIST OF DELIMITERS, NEVER A DENYLIST OF RATE SPELLINGS (#11331 review
#: round 11). A rate's denominator phrase can be ANY word after the unit (`per claim`, `share`,
#: `ratio`, `over claims`, …), and a denylist is bypassed by the spelling it does not list — OWASP's
#: input-validation control (C5) names that as the reason allowlisting is the required minimum. This
#: arm is what keeps §7 item 1's "a record-only edit cannot give `A4` a bar" true, so the safe
#: direction is to accept ONLY closing punctuation and refuse every word: over-blocking here costs a
#: re-punctuated cell (the pinned `A4` cell is written so its clause ends it, §10.13), while failing
#: open settles the owner-gated `A4`-vs-`D3` decision (§3.7, §10.11).
FLOOR_CLAUSE_END = re.compile("[" + r"\s)\]}>.,;:!?—–\-" + "\"'" + "]*" + r"\Z")


def _floor_unit(key: str) -> str:
    """The denominator phrase a floor is expressed in, derived from its record KEY.

    `a4_typed_statements` -> `"typed statements"`. The row id prefix (`a<digits>_`) carries no
    meaning for the unit, so it is stripped.
    """
    return re.sub(r"^a\d+_", "", key).replace("_", " ").strip()


class FloorMap(dict):
    """`{row_id: pinned_value}` that ALSO carries each row's floor UNIT (`units[row_id]`).

    ⛔ THE VALUE ALONE CANNOT TELL A FLOOR FROM A BAR (#11331 review round 10). `>= 30` typed
    statements is `A4`'s registered floor — a population count. `>= 30 %` typed is a RATE bar that
    reuses the same number, and binding only on the number excused it: measured, `A4`'s cell
    rewritten to `floor of >= 30 % typed and compared to no bar` left the gate at `exit=0` with
    `criteria[A4].floor` untouched, while the identical bar with the words `floor of` removed was
    refused at `exit=1`. `A4` is the row the record registers as having NO bar, and this is the
    owner-gated `A4`-vs-`D3` decision (`#10988` BLOCKED, `#1022` carries it), so excusing it
    settles an owner decision this registration has no authority over (§3.7, §10.11).

    A plain `dict` still works, but it carries no units, so its floor clauses cannot be classified
    and are NOT exempted — fail CLOSED.
    """

    def __init__(self, values=None, units=None) -> None:
        super().__init__(values or {})
        self.units: dict[str, str] = dict(units or {})


def _strip_the_rows_own_floor(text: str, value: float | None, unit: str | None = None) -> str:
    """`text` with the row's OWN registered floor clause removed — and NOTHING when it has no pin.

    ⛔ THE CLAUSE IS IDENTIFIED BY ITS VALUE **AND ITS UNIT**, not by the words in front of it.
    Only a number the record PINS, expressed in the row's own denominator, is a floor (a refusal
    boundary, §3.7); anything else is a bar however it is spelled, and it stays in the cell and
    reds the arm.

    ⛔ `value is None` STRIPS NOTHING — that is the absent-row case, and it must fail CLOSED.
    An earlier cut stripped unconditionally on `None`, which put the ABSENT-row path back on the
    laundering branch: measured at #11331 review round 9, deleting `criteria[A4].floor` while
    writing a bar into A4's cell left the gate at exit 0, while the identical cell with the
    pointer kept exited 1 — i.e. a record-only edit REMOVED the refusal, and both this function
    and `_registered_floor_values` documented the opposite.

    ⛔ `unit` IS PART OF THE BINDING, NOT DECORATION (round 10). Binding on the bare number excused
    `floor of >= 30 % typed`, which reuses `A4`'s pinned 30 as a RATE — the bar the owner has not
    ruled on. A clause is therefore exempted only when the text following its number names the
    row's own floor denominator (`a4_typed_statements` -> "typed statements"). No unit ⇒ strip
    nothing ⇒ the comparator reds.

    ⛔ AND THE WHOLE UNIT PHRASE, NOT A PREFIX OF ONE (round 11). `typed statements per claim`,
    `typed statements share` and `typed statements ratio` all START with the row's own unit, so the
    round-10 prefix test exempted a RATE that reuses the floor's pinned 30 — measured, `floor of
    >= 30 typed statements per claim, and compared to no bar` left the gate at exit 0 while the
    identical cell with `%` after the number was refused. The clause must END at the unit: only
    closing punctuation (`FLOOR_CLAUSE_END`) may follow it, to the end of the cell, because any
    word there can re-scope the number into a rate and that is the `A4`-vs-`D3` bar `#10988`/`#1022`
    have not ruled on. `, per claim` is refused for the same reason as ` per claim`: the delimiter
    does not license a following denominator phrase. The pinned record's own `A4` cell is written so
    its floor clause ends it (`#11331` §10.13).

    The no-record caller (a unit test on a bare row list) is the CHECKER's concern, not this
    function's: `check_completeness` strips by shape alone only when it was handed no map at all.
    The gate always hands it one (`check()` reads the map from the same record it validates), so
    the value+unit binding is what runs on every real check — and an unreadable record yields `{}`,
    which strips nothing, which fails CLOSED.
    """
    if value is None or not unit:
        return text

    wanted = unit.strip().lower()

    def _drop(match: re.Match[str]) -> str:
        try:
            if abs(float(match.group(1)) - value) >= 1e-9:
                return match.group(0)
        except (TypeError, ValueError):
            return match.group(0)
        # The denominator the floor is expressed in must FOLLOW the number, ignoring the markdown
        # that usually wraps the value (`` `>= 30` typed statements ``).
        tail = re.sub(r"[`*_\s]+", " ", text[match.end():]).strip().lower()
        if not tail.startswith(wanted):
            return match.group(0)
        # …and it must be the WHOLE phrase, at the END of the cell: only closing punctuation may
        # follow it, so a rate spelled after the unit — `typed statements per claim`, and
        # `typed statements, per claim` alike — is NOT the floor (#11331 r11).
        rest = tail[len(wanted):]
        return "" if FLOOR_CLAUSE_END.match(rest) else match.group(0)

    return FLOOR_CLAUSE.sub(_drop, text)


def _registered_floor_values(directory: Path) -> FloorMap | None:
    """Row id → the PINNED value of the floor that row's own registration names.

    One direction only: `criteria[].floor` names a key in `denominator_floors`, and the pair is
    what makes a floor clause identifiable. A row with no floor, or a floor key the record does
    not pin, is simply ABSENT from the result — and an absent row strips nothing at the caller
    (`_strip_the_rows_own_floor` returns the cell untouched for `None`), which fails CLOSED.
    Unreadable or malformed records yield `{}` for the same reason: nothing is pinned, so no
    clause is a floor, so no comparator can be excused by the phrase in front of it.
    """
    try:
        record = json.loads((directory / MACHINE_RECORD).read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(record, dict):
        return {}
    floors = record.get("denominator_floors")
    if not isinstance(floors, dict):
        return {}
    out: FloorMap = FloorMap()
    for crit in record.get("criteria") or []:
        if not isinstance(crit, dict):
            continue
        key, row_id = crit.get("floor"), crit.get("id")
        if not isinstance(key, str) or not isinstance(row_id, str) or key not in floors:
            continue
        value = floors[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        out[row_id] = float(value)
        out.units[row_id] = _floor_unit(key)
    return out
#: A registered non-bar must name WHO gates it. ⛔ The check is `#\d+` — "names an issue" — NOT "names
#: the row's registered decision": the record's cells and §7 item 1 were restated to claim exactly
#: what this tests, because the stronger sentence would be a claim the code does not perform.
OWNER_DECISION = re.compile(r"#\d+")

#: The four-edge denominator as a RATE: ``\\d+ / 4`` — and never ``4 / 40``, hence the trailing
#: guard. A count in words (``2 of 4``) is deliberately NOT matched: §1.4 states the history that
#: way, and refusing it would red the document for narrating its own reasoning.
FOUR_EDGE_RATE = re.compile(r"(?<![\d.])(\d+)\s*/\s*4(?![\d.])")


class CheckError(Exception):
    """A check could not be performed (distinct from a check that failed)."""


# ── document parsing ────────────────────────────────────────────────────────


def _cells(line: str) -> list[str]:
    """Split a markdown table row into stripped cells (leading/trailing pipes dropped)."""
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|"):
        body = body[:-1]
    return [c.strip() for c in body.split("|")]


def _plain(cell: str) -> str:
    """Cell text with markdown emphasis/code markers removed, for placeholder comparison."""
    return re.sub(r"[`*_]", "", cell).strip().strip(".").strip()


def is_placeholder(cell: str) -> bool:
    return _plain(cell).casefold() in PLACEHOLDER_CELLS


def parse_criteria_rows(text: str) -> list[dict[str, str]]:
    """The criteria table: the one whose header names metric, denominator and threshold.

    Returns one dict per row: ``{"id", "metric", "denominator", "threshold", "line"}``. A document
    with no such table returns ``[]`` — which the caller reports as a missing-table failure rather
    than silently passing an empty row set.
    """
    lines = text.splitlines()
    rows: list[dict[str, str]] = []
    for i, line in enumerate(lines):
        if not line.lstrip().startswith("|"):
            continue
        header = [c.casefold() for c in _cells(line)]
        if not any("metric" in c for c in header):
            continue
        if not any("denominator" in c for c in header):
            continue
        if not any("threshold" in c for c in header):
            continue
        # Column positions, so a re-ordered table still parses.
        idx_metric = next(n for n, c in enumerate(header) if "metric" in c)
        idx_denom = next(n for n, c in enumerate(header) if "denominator" in c)
        idx_thresh = next(n for n, c in enumerate(header) if "threshold" in c)
        for j in range(i + 1, len(lines)):
            row = lines[j]
            if not row.lstrip().startswith("|"):
                break
            cells = _cells(row)
            if all(re.fullmatch(r":?-{2,}:?", _plain(c)) for c in cells if c):
                continue  # the |---|---| separator
            if len(cells) <= max(idx_metric, idx_denom, idx_thresh):
                rows.append({"id": _plain(cells[0]) if cells else "?", "metric": "",
                             "denominator": "", "threshold": "", "line": str(j + 1)})
                continue
            rows.append(
                {
                    "id": _plain(cells[0]),
                    "metric": cells[idx_metric],
                    "denominator": cells[idx_denom],
                    "threshold": cells[idx_thresh],
                    "line": str(j + 1),
                }
            )
    return rows


def parse_pins(text: str) -> dict:
    """The ``yaml prereregistration-pins`` fenced block, parsed as YAML."""
    start = text.find(PINS_FENCE)
    if start < 0:
        raise CheckError(f"no `{PINS_FENCE}` block — the pins cannot be read")
    body_start = text.find("\n", start)
    if body_start < 0:
        raise CheckError("pins fence is the last line — no YAML body")
    end = text.find("\n```", body_start)
    if end < 0:
        raise CheckError("pins fence is unterminated")
    try:
        import yaml  # declared dependency (pyproject: pyyaml>=6.0)
    except ImportError as exc:  # pragma: no cover - environment guard
        raise CheckError(f"pyyaml is required to read the pins: {exc}") from exc
    try:
        pins = yaml.safe_load(text[body_start:end])
    except Exception as exc:
        raise CheckError(f"pins block is not valid YAML: {exc}") from exc
    if not isinstance(pins, dict):
        raise CheckError("pins block did not parse to a mapping")
    return pins


# ── the corpus re-derivation ────────────────────────────────────────────────


def _import_from_repo(dotted: str):
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    return importlib.import_module(dotted)


def _sentence_count(content: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", content) if s.strip()]


def _marker_pattern(lexemes: list[str]) -> re.Pattern[str]:
    """Word-bounded, case-insensitive; each lexeme tolerates a plural/past suffix.

    The registration declares the LEXEMES; the suffixes are mechanics (a measurement that missed
    "seems" because it keyed on "seem" would understate the denominator in one direction only).
    """
    parts = [re.escape(lex) + r"(?:s|es|d|ed)?" for lex in lexemes]
    return re.compile(r"\b(?:" + "|".join(parts) + r")\b", re.IGNORECASE)


def derive_corpus_facts(pins: dict) -> dict:
    """Re-derive every pinned corpus quantity from the tree at HEAD."""
    corpus = _import_from_repo("tests.eval.write_path.corpus")
    dd = _import_from_repo("tortoise.definite_description_backfill")

    root = REPO_ROOT / str(pins.get("corpus", {}).get("root", "tests/eval/write_path"))
    if not root.is_dir():
        raise CheckError(f"corpus root not found: {root}")

    session_ids = sorted(corpus.session_ids(root))
    units_total = 0
    excluded: set[str] = set(pins.get("corpus", {}).get("value_excluded_unit_ids") or [])
    value_ids: list[str] = []
    per_session_units: dict[str, int] = {}
    per_session_ops: dict[str, int] = {}
    per_session_values: dict[str, int] = {}
    for sid in session_ids:
        gold = corpus.load_gold(sid, root)
        units = gold.get("planted_units") or []
        per_session_units[sid] = len(units)
        units_total += len(units)
        per_session_ops[sid] = len(gold.get("planted_operators") or [])
        n_value = 0
        for unit in units:
            anchor = str(unit.get("verbatim_anchor") or "")
            if not re.search(r"\d", anchor):
                continue
            if str(unit.get("id")) in excluded:
                continue
            n_value += 1
            value_ids.append(str(unit.get("id")))
        per_session_values[sid] = n_value

    # The corpus-wide total comes from the corpus package's OWN denominator function — its
    # docstring calls itself "the ONE source for an operator-edge DENOMINATOR in tests and lanes",
    # and PRE-REGISTRATION.md §1.1 names it as the derivation method. Re-deriving it inline here
    # would be a second encoding that has to be kept in sync by hand (#9075 review r1 P2); the
    # per-session counts above stay only because the split reports per-half counts.
    operator_edges = corpus.planted_operator_count(root=root)

    # An exclusion that does not exist, or that excludes nothing digit-bearing, is a stale pin.
    all_ids = set()
    digit_bearing: set[str] = set()
    for sid in session_ids:
        for unit in corpus.load_gold(sid, root).get("planted_units") or []:
            uid = str(unit.get("id"))
            all_ids.add(uid)
            if re.search(r"\d", str(unit.get("verbatim_anchor") or "")):
                digit_bearing.add(uid)
    missing_exclusions = sorted(excluded - all_ids)
    # The other half of the same pin (§3.3: "both exclusions are re-derived"): an id that exists but
    # whose anchor carries no digit excludes nothing from the value-bearing class, so it is a pin
    # from a corpus that no longer exists (#9075 review r5 note 1).
    idle_exclusions = sorted((excluded & all_ids) - digit_bearing)

    lexemes = list(pins.get("corpus", {}).get("hedge_scope_lexemes") or [])
    pattern = _marker_pattern(lexemes) if lexemes else None
    per_session_markers: dict[str, int] = {}
    sentences_total = 0
    markers_total = 0
    for sid in session_ids:
        fixture = corpus.load_fixture(sid, root)
        n = 0
        for turn in fixture.get("conversation") or []:
            for sentence in _sentence_count(str(turn.get("content") or "")):
                sentences_total += 1
                if pattern is not None and pattern.search(sentence):
                    n += 1
        per_session_markers[sid] = n
        markers_total += n

    rows = [
        json.loads(line)
        for line in (root / "salience_labels.jsonl").read_text().splitlines()
        if line.strip()
    ]
    in_scope = [r for r in rows if dd.is_bare_definite_description(r["object_name"])]
    positive = [r for r in rows if dd.is_positively_bare_definite_description(r["object_name"])]
    detected = sorted({r["class"] for r in positive})
    classes = {r["class"] for r in rows}

    return {
        "fixtures_hash": corpus.compute_fixtures_hash(root),
        "sessions": session_ids,
        "planted_units": units_total,
        "planted_operator_edges": operator_edges,
        "value_bearing_units": len(value_ids),
        "value_bearing_ids": sorted(value_ids),
        "hedge_scope_sentences": markers_total,
        "sentences_total": sentences_total,
        "missing_exclusions": missing_exclusions,
        "idle_exclusions": idle_exclusions,
        "salience": {
            "rows": len(rows),
            "in_scope": len(in_scope),
            "positively_classified": len(positive),
            "deferred": len(in_scope) - len(positive),
            "detected_classes": detected,
            "undetected_classes": sorted(classes - set(detected)),
        },
        "per_session": {
            sid: {
                "units": per_session_units[sid],
                "operator_edges": per_session_ops[sid],
                "value_bearing_units": per_session_values[sid],
                "hedge_scope_sentences": per_session_markers[sid],
            }
            for sid in session_ids
        },
    }


def _half_counts(derived: dict, ids: list[str]) -> dict[str, int]:
    return {
        "units": sum(derived["per_session"][i]["units"] for i in ids),
        "operator_edges": sum(derived["per_session"][i]["operator_edges"] for i in ids),
        "value_bearing_units": sum(
            derived["per_session"][i]["value_bearing_units"] for i in ids
        ),
        "hedge_scope_sentences": sum(
            derived["per_session"][i]["hedge_scope_sentences"] for i in ids
        ),
    }


# ── git ordering ────────────────────────────────────────────────────────────

#: The ordering half's THIRD outcome: the history it reads is not in this checkout.
#: CI test jobs check out at ``fetch-depth: 1`` — a settled property of this repo
#: (``tests/test_lint_tripwire.py`` records it: *"CI test jobs use shallow fetch-depth:1"*) — and git
#: GRAFTS a depth-1 clone at its tip, so ``git log --diff-filter=A`` reports every path as added by
#: HEAD. Both sides of the comparison then resolve to the same epoch, and the gate can no longer tell
#: "the ordering was violated" from "the ordering cannot be proved". Reporting the first while the
#: second is true is a false RED on a correctly ordered tree, blamed on the wrong thing (#9075 r4 P1).
HISTORY_UNAVAILABLE = (
    "UNPROVABLE — shallow clone: the add-commit epochs the ordering half reads are not in this "
    "checkout, so the ordering of this registration against its evidence logs cannot be "
    "established here (run against a full clone: git fetch --unshallow)"
)


class HistoryUnavailable(CheckError):
    """The checkout does not carry the history the ordering half needs to reach a verdict.

    Carries the failures the GIT-FREE half already found, so an unprovable history cannot swallow a
    laundering finding that needed no history at all (#9075 review r6 P2).
    """

    def __init__(self, message: str, failures: list[str] | None = None) -> None:
        super().__init__(message)
        self.failures = list(failures or [])


def history_unavailable() -> bool:
    """True when this checkout is shallow, i.e. when add-commit epochs are unusable.

    ``git rev-parse --is-shallow-repository`` is the exact signal: in a depth-1 clone the boundary
    commit is a grafted root, so every tracked path resolves to HEAD's own epoch.
    """
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--is-shallow-repository"],
            cwd=str(REPO_ROOT), capture_output=True, text=True, check=False,
        )
    except OSError:  # pragma: no cover - git absent
        return False
    if out.returncode != 0:
        return False
    return out.stdout.strip().lower() == "true"


def _added_commits(path: Path) -> list[tuple[int, str]]:
    """``(epoch, sha)`` for every commit that ADDED ``path``, oldest-first. Empty when never added.

    ⛔ THE SHA IS NOT DECORATION (#10529 review cycle 4, P1). The first cut of this change inferred
    "the pair entered the repository atomically" from ``log_epoch == prereg_epoch`` — but two
    DIFFERENT commits can share a second, and git can order them by ANCESTRY. Comparing epochs alone
    therefore (a) let the attack shape through once a disclosure was supplied — a log commit and a
    thresholds commit in the same second, where the log is a true ancestor — and (b) told the
    HONEST two-commit-same-second shape it was "the SAME commit", compelling a false record entry.
    Atomicity is identical-SHA, and distinct commits are ordered by ``git merge-base --is-ancestor``;
    the epoch is only what names the commit in a message.
    """
    try:
        rel = path.resolve().relative_to(REPO_ROOT)
    except ValueError:
        return []
    try:
        out = subprocess.run(
            # ⚠️ KNOWN LIMITATION, DISCLOSED NOT SILENT (#10529 review cycle 10, P1): WITHOUT
            # `--follow` a `git mv` reports the rename as an ADD of the new path, so a renamed
            # log's pre-rename history — including a content that predates the thresholds — is
            # invisible here. `--follow` was tried and REVERTED: measured on this tree it made
            # `--diff-filter=A` report the MERGE-BASE commit as the add for ORDINARY files, which
            # broke the base ordering verdict for logs added after the registration (8 tests). A
            # guard that is wrong about the common case to cover the rare one is worse. Renames are
            # therefore NOT a defended attack surface; the bound is disclosed here so the next lane
            # inherits the fact rather than the illusion.
            ["git", "log", "--diff-filter=A", "--format=%H %ct", "--", str(rel)],
            cwd=str(REPO_ROOT), capture_output=True, text=True, check=False,
        )
    except OSError:  # pragma: no cover - git absent
        return []
    if out.returncode != 0:
        return []
    found: list[tuple[int, str]] = []
    for line in out.stdout.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].strip().isdigit():
            found.append((int(parts[1]), parts[0]))
    return sorted(found)


def _is_ancestor(older: str, newer: str) -> bool:
    """Is ``older`` an ancestor of ``newer`` (or the same commit)? Never raises."""
    try:
        out = subprocess.run(
            ["git", "merge-base", "--is-ancestor", older, newer],
            cwd=str(REPO_ROOT), capture_output=True, text=True, check=False,
        )
    except OSError:  # pragma: no cover - git absent
        return False
    return out.returncode == 0


def _resolves_to_commit(sha: str) -> bool:
    """Does ``sha`` name a commit THIS repository has? Never raises.

    ⛔ THE ASYMMETRY IS THE POINT (#10529 review cycle 14, P2). ``branch_commits`` may NOT be
    required to resolve — a squash merge leaves them unreachable from ``main``, so demanding the
    object would make the disclosure permanently red in exactly the case it exists for. The same
    argument does NOT reach ``squash_merge``: that commit is ON ``main`` by construction, so in any
    tree where this check runs it either exists or the claim is about a repository that does not.
    Format alone accepted ``deadbeef1`` — and the CLI then printed it as "squashed by deadbeef1",
    presenting the ONE line this change added to make the disclosure checkable as an unresolvable
    pointer. ``rev-parse --verify --quiet`` is the same primitive ``_blob_at`` already uses.
    """
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}"],
            cwd=str(REPO_ROOT), capture_output=True, text=True, check=False,
        )
    except OSError:  # pragma: no cover - git absent
        return False
    return out.returncode == 0 and bool(out.stdout.strip())


def _added_epochs(path: Path) -> list[int]:
    """Commit epochs at which ``path`` was ADDED, oldest-first. Empty when never added."""
    try:
        rel = path.resolve().relative_to(REPO_ROOT)
    except ValueError:
        return []
    try:
        out = subprocess.run(
            # ⛔ `--full-history`: without it git's history simplification follows a TREESAME
            # parent at a merge and can return only the SECOND add of a path, so `min()` is later
            # than the truth and a log that predates the registration is ACCEPTED (review cycle 13,
            # finding 1 — reproduced: adds at 1577880000 and 1578009600 on two branches, merge
            # resolved to the later side, default log returns only 1578009600). This is the same
            # hazard `_touch_epochs` already guards with the same flag; there it was applied to a
            # DIAGNOSTIC and here it decides the VERDICT, which is why it went unnoticed.
            ["git", "log", "--full-history", "--diff-filter=A", "--format=%ct", "--", str(rel)],
            cwd=str(REPO_ROOT), capture_output=True, text=True, check=False,
        )
    except OSError:  # pragma: no cover - git absent
        return []
    if out.returncode != 0:
        return []
    epochs = [int(x) for x in out.stdout.split() if x.strip().isdigit()]
    return sorted(epochs)


def _is_tracked(path: Path) -> bool:
    try:
        rel = path.resolve().relative_to(REPO_ROOT)
    except ValueError:
        return False
    try:
        out = subprocess.run(
            ["git", "ls-files", "--error-unmatch", str(rel)],
            cwd=str(REPO_ROOT), capture_output=True, text=True, check=False,
        )
    except OSError:  # pragma: no cover - git absent
        # Every other git call here guards this; without it a git-less host raised an unhandled
        # FileNotFoundError past the exit-2 contract (#9075 review r6 P3).
        return False
    return out.returncode == 0


def _log_declares_unscored(log: Path) -> tuple[bool, str]:
    """Does the LOG ITSELF say it was never scored? Returns (ok, reason).

    Read from the log, never from the pins: an exemption that took the document's word for it would
    let a grandfathered log be edited into a scored one and still pass.
    """
    try:
        text = log.read_text()
    except OSError as exc:
        return False, f"cannot be read ({exc})"
    except UnicodeDecodeError as exc:
        # #9089 review cycle 6: this one read without the errors="replace" the sibling predicate
        # uses, so a non-UTF-8 log raised past every CheckError handler and surfaced as exit 1 —
        # the code reserved for FINDINGS — on a log the tool never read.
        return False, f"cannot be read as UTF-8 ({exc})"
    declared = ""
    for line in text.splitlines():
        if line.startswith("prereg="):
            declared = line[len("prereg="):].split()[0] if line[len("prereg="):].strip() else ""
            break
    if declared != UNSCORED:
        return False, (
            f"does not declare prereg={UNSCORED} on its own line (found {declared!r}) — "
            "an exemption covers a log that is unscored, not a log that was scored and renamed"
        )
    markers = SCORED_KEYS.findall(text)
    tokens = SCORED_KEY_TOKENS.findall(text)
    if len(markers) < len(tokens):
        return False, (
            "carries a verdict=/result= marker whose value cannot be parsed — an unreadable "
            "status must fail closed, not be skipped as though the log did not carry one"
        )
    for key, value in markers:
        if value.rstrip(VALUE_TRAILING) != UNSCORED:
            return False, (
                f"carries {key}={value} — a grandfathered log may only ever carry "
                f"{key}={UNSCORED}; anything else was measured against a bar that did not exist"
            )
    exits = EXIT_KEY.findall(text)
    if not exits:
        return False, (
            "does not record an exit= line — an UNPREREGISTERED log must exit 3, so a missing "
            "exit line means the status and the outcome disagree"
        )
    if any(value.rstrip(VALUE_TRAILING) != "3" for value in exits):
        return False, (
            f"records exit={','.join(exits)} — an UNPREREGISTERED log must exit 3 and only 3"
        )
    return True, ""


def _touch_epochs(path: Path) -> list[int]:
    """Every commit epoch that TOUCHED ``path`` — not just the one that added it.

    ``--diff-filter=A`` is immutable by construction, so on its own it cannot see an in-place
    edit: a grandfathered log could be re-committed with a fabricated measurement number and its
    add-commit would still predate the registration (#9075 review P1).
    """
    try:
        rel = path.resolve().relative_to(REPO_ROOT)
    except ValueError:
        return []
    try:
        out = subprocess.run(
            # `--full-history`: without it git's history simplification can drop a MERGE commit
            # that touched the log, and the content bound would then miss an edit landed on a side
            # branch (#9075 review r4 P3).
            ["git", "log", "--full-history", "--format=%ct", "--", str(rel)],
            cwd=str(REPO_ROOT), capture_output=True, text=True, check=False,
        )
    except OSError:  # pragma: no cover - git absent
        return []
    if out.returncode != 0:
        return []
    return sorted(int(x) for x in out.stdout.split() if x.strip().isdigit())


# ── the ordering BASE (#10463) ──────────────────────────────────────────────
#
# ⛔ WHY THE COMPARISON IS MEASURED AGAINST THE BASE AND NOT AGAINST THE PR. A pre-registration's
# ordering claim is that the registration was a **published artefact before the evidence log's
# add-commit**. Measuring that inside one contribution is not squash-invariant: a squash merge
# rewrites the contribution's commits into ONE, so a log added after the registration on the branch
# and a log added beside it in the same commit become indistinguishable — the same tree passed its
# PR checks and redded `main` minutes later (#10468, which is how this defect was found). A gate
# whose verdict depends on the merge strategy enforces nothing before the merge.
#
# So the predicate is: **the registration's earliest add-commit ON THE BASE must strictly precede
# the log's add-commit** — measured on the base when the log is already merged there, and in this
# contribution when it is not. That reads only commits that are ALREADY published, which is exactly
# the set a squash cannot rewrite; external practice agrees, since a registration's "before"
# relation holds between published, hash-pinned artefacts and never between sibling commits.
#
# The consequence is the arm the ruling asks for: a contribution that adds a registration AND a log
# has nothing on the base to appeal to, so it is REFUSED — the registration must land FIRST, in its
# own PR. That refusal is what makes this a gate rather than a red `main`.

def _touch_commits(path: Path) -> list[str]:
    """Every commit sha that TOUCHED ``path`` (``--full-history``, so a merge on a side branch counts).

    ⛔ TOPOLOGY, NOT DATES (#10529 review cycle 9, P1). `_touch_epochs` answers the same question in
    ``%ct``, and ``%ct`` is attacker-controlled (``GIT_COMMITTER_DATE``) — the lesson this module
    already learned for the ADD-commit in cycle 5. A number rewritten in a commit whose dates were
    set to look EARLIER than the registration defeated the epoch bound and passed green. Whether a
    touching commit is *behind* the registration is a fact about the graph, not about a clock.
    """
    try:
        rel = path.resolve().relative_to(REPO_ROOT)
    except ValueError:
        return []
    try:
        out = subprocess.run(
            # ⚠️ `--follow` is ABSENT here for the SAME reason as `_added_commits` above: it was
            # tried and REVERTED (#10529 review cycle 10), because it made `--diff-filter=A` report
            # the MERGE-BASE commit as the add for ordinary files and broke the base ordering verdict
            # for logs added after the registration (8 tests). The consequence is the DISCLOSED
            # limitation at `_added_commits`: a `git mv` hides the pre-rename history from this bound
            # too, so renames are NOT a defended attack surface. Do not "fix" this by adding the flag
            # without re-running that experiment.
            ["git", "log", "--full-history", "--format=%H", "--", str(rel)],
            cwd=str(REPO_ROOT), capture_output=True, text=True, check=False,
        )
    except OSError:  # pragma: no cover - git absent
        return []
    if out.returncode != 0:
        return []
    return [line.strip() for line in out.stdout.splitlines() if line.strip()]


def _blob_at(sha: str, rel: Path) -> str:
    """The blob id of ``rel`` at ``sha``, or ``""`` when the path does not exist there."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", f"{sha}:{rel}"],
            cwd=str(REPO_ROOT), capture_output=True, text=True, check=False,
        )
    except OSError:  # pragma: no cover - git absent
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


def _late_touches(path: Path, prereg_sha: str) -> list[str]:
    """Commits that changed ``path``'s CONTENT outside the registration's ancestry.

    ⛔ CONTENT, NOT COMMIT-PRESENCE (#10529 review cycle 12, P1). `--full-history` deliberately lists
    a MERGE commit that carried the path in from a branch side, and that merge is a DESCENDANT of the
    registration's add-commit — so a commit-presence test reported the landing merge as a "late
    edit". The merge changed nothing (same blob), yet the bound is consulted BEFORE the disclosure is
    even read, so an atomic pair landed with `--merge`/`--no-ff` — which is exactly what this repo's
    own merge rail does — was PERMANENTLY RED with no waiver route, and would have red-ed `main` on
    landing: the #10468 outage class this change exists to end, re-created one shape over.

    A touching commit therefore counts only when it actually CHANGED the file relative to the
    registration's own snapshot. An in-place rewrite, a backdated edit, a stripped number and a
    delete-and-re-add all change the blob and stay red; a merge that carries the pair through
    unchanged does not.
    """
    try:
        rel = path.resolve().relative_to(REPO_ROOT)
    except ValueError:
        return []
    base = _blob_at(prereg_sha, rel)
    late: list[str] = []
    for sha in _touch_commits(path):
        if _is_ancestor(sha, prereg_sha):
            continue
        if base and _blob_at(sha, rel) == base:
            continue  # the commit touched the path but left its content identical (a landing merge)
        late.append(sha)
    return late


def _atomic_detail_entry(block: dict) -> dict:
    """The printed detail for ONE disclosed log — one construction, so the arms cannot drift.

    Duplicating this literal is what produced the "header with zero commit lines" defect twice
    (#10529 review cycles 6 and 9).
    """
    return {
        "squash_merge": str(block.get("squash_merge") or ""),
        "branch_commits": [str(c) for c in block.get("branch_commits") or []],
    }


#: The ref the ordering is measured against. `origin/main` first: that is what a contribution is
#: merged into. `PREREG_ORDERING_BASE_REF` (or `--base-ref`) overrides it.
BASE_REF_ENV = "PREREG_ORDERING_BASE_REF"
BASE_REF_CANDIDATES = ("origin/main", "main")

#: What the report says when NO base ref could be resolved. A named constant, not a literal: it is
#: a value consumers assert on, and a shallow checkout legitimately reports it (review cycle 3,
#: finding 2).
UNRESOLVED_BASE = "(unresolved)"

NO_BASE_REF = (
    "no base ref: this checkout has a remote but "
    f"{'/'.join(BASE_REF_CANDIDATES)} does not resolve and no --base-ref / ${BASE_REF_ENV} was "
    "given, so the ordering cannot be measured against the published base"
)

#: The remedy differs by CAUSE, so the message does too (review cycle 3, finding 2). Naming the
#: candidate set when the caller PINNED a ref that failed sends the operator to debug flag wiring —
#: or to wonder whether the unconditional CI step is wired — when the real answer is "that ref is not
#: fetched here". That is the same send-them-to-a-remedy-that-cannot-work defect this change closed
#: for shallow-vs-base, so it is not repeated here.
NO_BASE_REF_EXPLICIT = (
    "the base ref {explicit!r} (from --base-ref / ${env}) does not resolve in this checkout. The "
    "ordering claim is measured against the ref the contribution merges into, and an unresolvable "
    "base is NOT substituted with a different ref — that would answer a question the caller did not "
    "ask. Fetch it (`git fetch origin <branch>`) and re-run"
)

#: Appended to both base-ref failures: the exit code is the part a runner keys on.
_NO_BASE_REF_TAIL = (
    ". This is a usage / environment error (exit 2), NOT the shallow-clone third outcome: a "
    "full-history checkout that cannot name its base must not report success, and folding it into "
    "the skip path would let an environment change silently downgrade the gate to the PR-internal "
    "rule it exists to remove"
)


def no_base_ref_message(explicit: str | None) -> str:
    """The base-ref refusal for the cause that actually fired — never the other one's text."""
    if explicit:
        return NO_BASE_REF_EXPLICIT.format(explicit=explicit, env=BASE_REF_ENV) + _NO_BASE_REF_TAIL
    return NO_BASE_REF + _NO_BASE_REF_TAIL


#: The equal-epoch refusal's remedy — and it must name only a path that CAN clear the check.
#: ⛔ It used to end "…and re-add the log in a PR of its own, after the registration is published
#: there", which is wrong twice over (#10463 review cycle 7, finding F1):
#:
#: 1. It cannot work — measured. `_added_epochs_on` is `git log --diff-filter=A <base> -- <path>`,
#:    and a later DELETION does not remove the earlier add from that ref's history. So after a
#:    withdrawal AND a re-add, the base reports BOTH add-commits and `min()` is still the collapsed
#:    one (probe: add @1700000000 → withdraw → re-add @1700000300 ⇒ min() = 1700000000), which takes
#:    `log_epoch <= prereg_epoch` again and re-emits this same refusal, forever. The instruction
#:    rejects the very path it names.
#: 2. It is the move the recorded ruling forbids: #10463 comment 6564634454 item 2 — "**Do not
#:    re-add the log in a fresh commit to obtain a later add-commit.** That satisfies the letter of
#:    the check while the real ordering is unchanged — it is precisely the fabrication the guard
#:    exists to prevent." The refusal text is what an operator reads, so instructing it is adopting
#:    the forbidden move in the one surface they act on.
#:
#: What is left is the honest one: a squash-collapsed log has NO provable ordering to recover, and
#: the collapsed add-commit is permanent for its PATH — so the path forward is a new artefact at a
#: new path, whose own add-commit the base can distinguish.
SQUASH_COLLAPSE_REMEDY = (
    " Both artefacts were added on {base} by the SAME commit ({sha}), i.e. they landed there in ONE "
    "squash merge — so this history cannot be edited into an ordering, and re-running will not "
    "change the answer. This log has NO provable ordering left to recover: its add-commit on "
    "{base} IS the collapsed one, and because a deletion does not remove an earlier add from a "
    "ref's history, a deleted-then-re-added copy is still measured against that same collapsed "
    "epoch. ⛔ Do NOT re-add this log in a fresh commit to obtain a later add-commit: that "
    "satisfies the letter of the check while the real ordering is unchanged, which is the "
    "fabrication this guard exists to prevent. The path forward is a WITHDRAWAL plus a NEW "
    "artefact at a NEW path: withdraw this log from the checked directory (move it under "
    "`withdrawn/` with a one-line note of why — do not delete it, and do not edit its contents) so "
    "this directory stops claiming a measurement whose ordering cannot be shown. Withdrawing it "
    "DOES clear THIS refusal — the gate no longer sees this log at all — but it does not restore "
    "this log's ordering, so it must not come back as evidence; then take a fresh registration and "
    "a fresh evidence log at new paths, whose add-commits {base} can tell apart."
)
SAME_PR_REFUSAL = (
    "{prereg} is not on the merge base ({base}): this change adds {n} evidence log(s) against a "
    "pre-registration that is NOT published there. TWO causes, and the remedy differs — check "
    "which one you are in: (a) THIS change adds the registration, in which case it must land "
    "FIRST, in its own PR: the ordering claim is that the registration was already on the base "
    "before the evidence log's add-commit, and a registration added by the SAME PR as its log has "
    "no such claim to make, because a squash merge collapses the two into one commit and the "
    "ordering cannot be shown from the merged history at all; or (b) your base ref is STALE — "
    "the registration is already merged upstream and you have not fetched, so run "
    "`git fetch origin` and re-run (the ordering then holds on the real base)."
)


def _git_stdout(args: list[str]) -> tuple[int, str]:
    """``(returncode, stdout)`` for a git call, or ``(1, "")`` when git is absent."""
    try:
        out = subprocess.run(["git", *args], cwd=str(REPO_ROOT),
                             capture_output=True, text=True, check=False)
    except OSError:  # pragma: no cover - git absent
        return 1, ""
    return out.returncode, out.stdout


def resolve_base_ref(explicit: str | None = None) -> str | None:
    """The ref the ordering is measured against, or ``None`` when there is none to measure.

    Precedence, and each step is deliberate:

    1. An **explicit** ref (``--base-ref`` / ``$PREREG_ORDERING_BASE_REF``) is authoritative. It is
       NOT a hint: ``resolve_base_ref`` returns ``None`` when it does not resolve, and the caller
       fails closed. Silently substituting a different ref would answer a different question than
       the one the caller pinned — the exact substitution this gate exists to refuse.
    2. ``origin/main`` — the published base a contribution is merged into. Tried even when no remote
       is configured, because a remote-TRACKING ref is the published side regardless.
    3. A checkout with **no remote** is a local-only history (the shape the unit tests build in a
       temp repo). It is resolved to ``HEAD`` BEFORE the local ``main`` candidate: there is no
       published base and no merge to be invariant against, so ``HEAD`` is all the history there is.
       Resolving it to a local ``main`` branch instead would gate a local-only repo against a
       branch nobody publishes to — a different predicate than the one documented here (review
       cycle 1, finding 7). A local ``main`` is still used when a remote exists.

    A checkout WITH a remote but without a resolvable base returns ``None``, and the caller raises
    ``CheckError`` (exit 2): the alternative is degrading to the PR-internal rule this change
    removes, and a check that cannot read its subject must never report success.
    """
    code, out = _git_stdout(["remote"])
    no_remote = code == 0 and not out.strip()
    if explicit:
        code, out = _git_stdout(["rev-parse", "--verify", "--quiet", explicit])
        return explicit if code == 0 and out.strip() else None
    # `origin/main` is tried first even with no configured remote: a remote-TRACKING ref is the
    # published side, and a checkout can carry one without the remote being configured here.
    code, out = _git_stdout(["rev-parse", "--verify", "--quiet", BASE_REF_CANDIDATES[0]])
    if code == 0 and out.strip():
        return BASE_REF_CANDIDATES[0]
    if no_remote:
        # A local-only history: `HEAD` is all of it. Deliberately BEFORE the local `main`
        # candidate — a local branch named `main` is not a published base, and gating a local-only
        # repo against it would silently measure a different predicate (review cycle 1, finding 7).
        return "HEAD"
    for candidate in BASE_REF_CANDIDATES[1:]:
        code, out = _git_stdout(["rev-parse", "--verify", "--quiet", candidate])
        if code == 0 and out.strip():
            return candidate
    return None


def _added_epochs_on(ref: str, path: Path) -> list[int]:
    """Commit epochs at which ``path`` was ADDED on ``ref``'s side, oldest-first.

    Reachability is bounded by ``ref`` (``git log <ref> -- <path>``), so this reads only commits
    that are ALREADY on that ref — the set a squash merge cannot rewrite. Empty when the path was
    never added there, which is the whole signal for "is the registration published on the base".

    ⛔ ``--full-history`` is load-bearing, for the reason spelled out in ``_added_epochs``: the
    default simplification can hide the EARLIER of two adds across a merge, which turns `min()`
    into a later epoch and makes this reader accept exactly the ordering it is here to refuse.
    """
    try:
        rel = path.resolve().relative_to(REPO_ROOT)
    except ValueError:
        return []
    code, out = _git_stdout(
        ["log", "--full-history", "--diff-filter=A", "--format=%ct", ref, "--", str(rel)]
    )
    if code != 0:
        return []
    return sorted(int(x) for x in out.split() if x.strip().isdigit())


def _add_commits_on(ref: str, path: Path) -> dict[str, int]:
    """``{commit sha: epoch}`` for the commits that ADDED ``path`` on ``ref``.

    ``_added_epochs_on`` answers *when*; this answers *by which commit*, which is the only way to
    tell the two causes of an equal-epoch refusal apart: a log that legitimately predates the
    registration (nothing to do but order it properly next time) versus a log and a registration
    that arrived in ONE commit — the squash-merge collapse, whose only remedy is a withdrawal.
    """
    try:
        rel = path.resolve().relative_to(REPO_ROOT)
    except ValueError:
        return {}
    code, out = _git_stdout(
        # `--full-history`: the same hazard as `_added_epochs`/`_added_epochs_on`, which carry it
        # because git's history simplification follows only the TREESAME parent at a merge and
        # never walks the other side, hiding an add-commit that lives there (git's own
        # `--full-history` / `--dense` note: "if one of the parents is TREESAME, we follow only
        # that one"). This reader decides no epoch, but it decides WHICH refusal text the operator
        # reads — `shared = prereg_add_commits & _add_commits_on(base, log)` gates the
        # squash-collapse remedy — so a hidden add here silently downgrades the one refusal whose
        # remedy is a withdrawal to the bare "not strictly after" text (review F2).
        ["log", "--full-history", "--diff-filter=A", "--format=%H %ct", ref, "--", str(rel)]
    )
    if code != 0:
        return {}
    found: dict[str, int] = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].isdigit():
            found[parts[0]] = int(parts[1])
    return found


def _mapping(container: dict, key: str, where: str) -> dict:
    """A sub-block as a mapping, or a CheckError — never a bare ``dict()``/``.get()`` crash.

    A valid-YAML-wrong-TYPE value (``pre_existing_unscored_logs: notamap``) is a PARSE error, which
    the contract says is exit 2; ``dict("notamap")`` raised ``ValueError`` and a wrong-typed
    ``"corpus"`` in the record raised ``AttributeError``, both escaping as unhandled tracebacks
    (#9075 review r4 P3, r5 P2). Falsy wrong-types (``[]``, ``""``, ``null``) are treated as an
    absent block rather than a crash — that fail-closes into the "empty" failure, which is exit 1
    and correctly attributed.
    """
    value = (container or {}).get(key)
    if not value:
        return {}
    if not isinstance(value, dict):
        raise CheckError(
            f"{where}.{key} is {type(value).__name__}, expected a mapping — it is unparseable "
            "for this check"
        )
    return dict(value)


def _list(container: dict, key: str, where: str) -> list:
    """A sub-block as a list, or a CheckError (same contract as :func:`_mapping`)."""
    value = (container or {}).get(key)
    if not value:
        return []
    if not isinstance(value, list):
        raise CheckError(
            f"{where}.{key} is {type(value).__name__}, expected a list — it is unparseable "
            "for this check"
        )
    return value


def _pins_mapping(pins: dict, key: str) -> dict:
    return _mapping(pins, key, "pins")


def check_step_partition(pins: dict, record: dict) -> list[str]:
    """The eight steps split into unconditional + conditional, once and consistently (#10215).

    The capstone's step gate (V5 in ``tools/capstone_verdict_check.py``, G5 in
    ``probes/capstone_run.py``) reads this split out of the FROZEN record, so the record is what
    decides whether a step is required. That makes a silent edit here a silent weakening of the
    epic's gate, which is why the split is checked from both sides and against the design's own
    step list:

      * every id is one of §4.1's eight, and the two lists are a STRICT partition — a step may be
        neither dropped nor listed twice;
      * a conditional entry carries its condition, the idle evidence a run must show, and the issue
        that measured it — an entry without them is a waiver with no content;
      * the document's pins fence and the machine record agree, so neither can rot alone;
      * a wrong-typed field on EITHER side is a parse error (``CheckError`` → exit 2), never a
        crash — an uncaught exception also exits 1, which is the code this tool reserves for
        FINDINGS, so a malformed record would read as "the table has findings".
    """
    failures: list[str] = []
    record_steps = _mapping(record, "steps", MACHINE_RECORD)
    # ⛔ BOTH halves route through the module's own readers, so a wrong-typed field is a PARSE
    # error on either side — the contract this docstring states. The pins half used to catch
    # `_pins_mapping`'s CheckError and downgrade it to a finding (exit 1) while
    # `pins.steps.unconditional` (via `_list`, below) raised (exit 2), so the SAME malformation
    # got two different answers depending on which sub-key it landed in. Letting it raise here is
    # what makes the docstring true; the EMPTY declaration stays a finding below, deliberately —
    # an absent block is a document defect, not unparseable input.
    pins_steps = _pins_mapping(pins, "steps")
    if not pins_steps:
        return [
            "pins.steps is empty — the capstone's step gate reads this split out of the frozen "
            "record, so a document that does not declare it lets the required step set change "
            "without a reviewable diff"
        ]

    unconditional = _list(pins_steps, "unconditional", "pins.steps")
    conditional = _list(pins_steps, "conditional", "pins.steps")

    def _cmp_steps(label: str, want, got) -> None:
        if got != want:
            failures.append(
                f"{MACHINE_RECORD} {label} is {got!r} but the pins block says {want!r} — two "
                "copies of the required step set that can disagree"
            )

    declared = [str(s) for s in unconditional]
    entries: dict[str, dict] = {}
    for entry in conditional:
        if not isinstance(entry, dict) or not str(entry.get("step") or ""):
            failures.append(
                f"pins.steps.conditional carries an entry that is not a {{step: …}} mapping "
                f"({entry!r}) — it declares nothing"
            )
            continue
        step = str(entry["step"])
        entries[step] = entry
        for field in CONDITIONAL_STEP_FIELDS:
            value = entry.get(field)
            if value is None or (isinstance(value, str) and not value.strip()) or value == "":
                failures.append(
                    f"pins.steps.conditional[{step}] omits {field!r} — a conditional step must "
                    "state when it does not apply, what a run must show to claim that, and the "
                    "issue that measured it"
                )

    unknown = sorted((set(declared) | set(entries)) - set(CAPSTONE_STEPS))
    if unknown:
        failures.append(
            f"pins.steps declares step id(s) {unknown} that are not part of the epic's eight "
            f"({', '.join(CAPSTONE_STEPS)}) — §4.1 fixes the list, so an invented id is a step the "
            "gate cannot reach"
        )
    both = sorted(set(declared) & set(entries))
    if both:
        failures.append(
            f"pins.steps lists {both} as BOTH unconditional and conditional — the split must be a "
            "partition, or a step's requirement depends on which list a reader consults"
        )
    absent = [s for s in CAPSTONE_STEPS if s not in declared and s not in entries]
    if absent:
        failures.append(
            f"pins.steps drops step(s) {absent} from both lists — the capstone measures the "
            "pipeline end to end, and a step that is in neither list leaves the gate by omission"
        )
    if len(declared) != len(set(declared)):
        failures.append("pins.steps.unconditional repeats a step id")

    # the record must restate the document's split, field for field: the run probe freezes the
    # RECORD, so a divergence means the gate enforces something the argument never argued.
    if not record_steps:
        failures.append(
            f"{MACHINE_RECORD} carries no steps block — the run probe reads the required step set "
            "from the frozen record, so a document alone changes nothing (#10215)"
        )
        return failures

    # The RECORD half is untrusted input exactly like the pins half (its producer is another
    # lane), so it is read through the same readers rather than with a bare `.get()`. A
    # truthy wrong type (`"unconditional": 5`, `"conditional": 1`) used to raise TypeError
    # out of here — through `run()`, which catches nothing, to `main()`, which catches only
    # CheckError — escaping as a traceback with exit 1, the code this tool reserves for
    # FINDINGS. A wrong-typed field is a PARSE error (exit 2), which is what `_list` raises.
    _cmp_steps("steps.unconditional", declared,
               [str(s) for s in _list(record_steps, "unconditional", MACHINE_RECORD)])
    record_conditional: dict[str, dict] = {}
    for entry in _list(record_steps, "conditional", MACHINE_RECORD):
        if isinstance(entry, dict) and str(entry.get("step") or ""):
            record_conditional[str(entry["step"])] = entry
        else:
            # ⛔ Mirror the pins arm above: the record's producer is another lane, so a malformed
            # entry is untrusted input that must be REPORTED, not skipped. Dropping it made the
            # record-side rule strictly weaker than the document-side one — a record that kept its
            # valid entry and added a junk one compared clean, while the same document written one
            # file over was refused.
            failures.append(
                f"{MACHINE_RECORD} steps.conditional carries an entry that is not a "
                f"{{step: \u2026}} mapping ({entry!r}) — it declares nothing"
            )
    if sorted(record_conditional) != sorted(entries):
        _cmp_steps("steps.conditional (step ids)", sorted(entries),
                   sorted(record_conditional))
    else:
        for step, entry in sorted(entries.items()):
            for field in CONDITIONAL_STEP_FIELDS:
                want = entry.get(field)
                got = record_conditional[step].get(field)
                if field == "evidence_issue":
                    # `#10215` and `10215` are the same issue; compare the number.
                    want, got = _issue_number(want), _issue_number(got)
                _cmp_steps(f"steps.conditional[{step}].{field}", want, got)
    return failures


def _issue_number(value) -> object:
    """``#10215`` / ``10215`` / ``"10215"`` as the same integer, and ANYTHING else unchanged.

    ⛔ TOTAL by construction. An earlier cut tested ``str.isdigit()`` and then called ``int()`` — but
    ``isdigit()`` is True for characters ``int()`` REJECTS (``'\u00b2'.isdigit()`` is True while
    ``int('\u00b2')`` raises ``ValueError``), so an ``evidence_issue`` holding a Unicode
    digit-like character escaped as an uncaught ``ValueError``: through ``run()`` (catches nothing)
    to ``main()`` (catches only ``CheckError``) as a traceback with exit **1** — the code this tool
    reserves for FINDINGS, which is the exact escape class this function's caller documents.
    ``try/except`` makes every non-numeric value a plain comparison mismatch instead.
    """
    text = str(value).strip().lstrip("#")
    try:
        return int(text)
    except ValueError:
        return value


def _record_for_steps(directory: Path) -> dict:
    """The machine record as a mapping, ``{}`` when it cannot be read.

    A missing or unreadable record is already reported by :func:`check_machine_record`; this loader
    exists so the step rule can be checked without raising a second time for the same cause.
    """
    try:
        record = json.loads((directory / MACHINE_RECORD).read_text())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        return {}
    return record if isinstance(record, dict) else {}


def check_step_partition_applies(directory: Path, pins: dict, record: dict) -> bool:
    """Whether this bundle is one whose step set :func:`check_step_partition` governs (#10215).

    The step set is a CAPSTONE declaration, not a property of every pre-registration this tool can
    read. ``tools/capstone_verdict_check.py`` pins conditionality to ONE artifact
    (``RECORD_ARTIFACT``, ``docs/evidence/7949-capstone/preregistration.json``) for the same reason:
    a second place a step set can be declared is a second place the epic's gate can be weakened.

    ⛔ The scope is not a hole, and it is not a blanket waiver. `#10468` put a SECOND
    pre-registered bundle on this tool's ``--dir`` — ``#10463``'s two-arm S3 ablation, which has no
    eight-step concept at all — so an unscoped rule failed that bundle on every run for not
    declaring a step set it never had. The rule therefore applies to:

      * the capstone bundle, ALWAYS (``DEFAULT_PREREG_DIR``) — so deleting its ``steps`` block is
        still a refusal rather than a silent widening of the required step set; and
      * any bundle that DECLARES a step set on either side — if you declare one, it must be a
        partition of the epic's eight and the document and record must agree.

    A bundle that declares none is not asked to invent one, and nothing is weakened by skipping
    it — absence is STRICTER at both gates, never a waiver:

      * V5 (``tools/capstone_verdict_check.py::_step_accounting``) requires all eight observed
        unless the log's own ``steps_conditional`` claims conditionality, and it refuses a claim
        the frozen record does not declare (``_record_conditional``, which reads
        ``RECORD_ARTIFACT`` and nothing else);
      * G5 (``probes/capstone_run.py::declared_conditional``) documents absence explicitly as the
        pre-``#10215`` shape — *"the block is absent → ``({}, "")`` … all eight are required (a
        refusal here would red every run made against an older record)"* — and its artifact comes
        from the operator-selectable ``--prereg-artifact``, not from V5's pinned ``RECORD_ARTIFACT``.

    So a capstone bundle that loses the block does not slip through either gate: it gets the
    OLD all-eight-required rule back, which is the stricter one. This pre-check is what makes that
    loss visible as a document defect instead of silently narrowing the epic's gate.
    """
    if record.get("steps"):
        return True
    if isinstance(pins, dict) and pins.get("steps"):
        return True          # declared (even if malformed) — `check_step_partition` owns the shape
    try:
        relative = directory.resolve().relative_to(REPO_ROOT)
    except (ValueError, OSError):
        return False
    return relative.as_posix() == DEFAULT_PREREG_DIR


def derivation_label_classes() -> frozenset[str]:
    """The link-anchored derivation classes, read from the writer's OWN constant (#9736).

    Read with :mod:`ast`, not by importing ``tortoise``: this rule must run in the CI job that does
    NOT install the package (``--no-corpus``), and an import constraint would make the rule skip in
    exactly the cheap place it should always run. A constant this reader cannot find is a check that
    could not read its subject — ``CheckError`` (exit 2), never a silent pass, because a gate that
    cannot see the model would green every row it was written to refuse.
    """
    import ast

    path = REPO_ROOT / DERIVATION_LABELS_PATH
    try:
        tree = ast.parse(path.read_text())
    except OSError as exc:
        raise CheckError(
            f"cannot read {DERIVATION_LABELS_PATH} to derive E3's class set ({exc}) — the rule "
            "that E3's denominator follows the model cannot be performed"
        ) from exc
    except SyntaxError as exc:  # pragma: no cover - a broken writer module is a tree problem
        raise CheckError(f"{DERIVATION_LABELS_PATH} does not parse: {exc}") from exc

    found: set[str] | None = None
    for node in ast.walk(tree):
        targets: list[ast.expr] = []
        value: ast.expr | None = None
        if isinstance(node, ast.Assign):
            targets, value = list(node.targets), node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        if not any(isinstance(t, ast.Name) and t.id == DERIVATION_LABELS_NAME for t in targets):
            continue
        call = value
        if isinstance(call, ast.Call) and call.args:
            call = call.args[0]
        if isinstance(call, (ast.Set, ast.List, ast.Tuple)):
            literals = {
                elt.value
                for elt in call.elts
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
            }
            if literals:
                found = literals
    if not found:
        raise CheckError(
            f"{DERIVATION_LABELS_PATH} carries no readable {DERIVATION_LABELS_NAME} — E3's class "
            "set is derived from it, so a constant that cannot be read is not a passing row"
        )
    return frozenset(found)


def _classes_named(cell: str) -> set[str]:
    """Which graph classes a cell NAMES, by the prose tokens above (``:Object`` folded in).

    ⚠️ THE ANCHOR IS ``(?<![\\w])``, NOT ``(?<![\\w:])`` (#9736 review P2). A previous revision
    excluded a preceding colon to avoid matching ``tuple:point``-shaped words — which made the
    CANONICAL ``:Object`` spelling invisible: a row written ``Point`` + ``:Object`` + ``Event`` (the
    spelling §3.4 itself uses) named only ``{Point, Event}`` and PASSED the very rule written to
    refuse it, while a correct row spelled entirely with colons named nothing and was FALSELY red-ed
    for omitting all three classes. A colon is exactly the markdown/Cypher class prefix, so it must
    not block a match; ``fooObject`` still does not match because ``o`` is a word character.

    ⛔ THE BACKSLASHES ABOVE ARE DOUBLED ON PURPOSE (#10525 review P2): this is a NORMAL docstring,
    so a single-backslash ``\\w`` is an invalid escape sequence — a ``SyntaxWarning`` on every run
    that imports this gate (measured: ``-W error::SyntaxWarning`` turns it into a ``SyntaxError`` at
    this line), and a hard error once invalid escapes stop being tolerated. It is also an asymmetry
    inside one commit: the sibling docstring in ``tests/test_capstone_preregistration_9075.py``
    already writes the same text escaped, and now both compile clean.

    ⛔ THE MATCH IS CASE-SENSITIVE AND SPELLS THE LABEL (#10525 review round 2, P2 — MEASURED). The
    first revision casefolded the cell and matched the lower-case token, so the ORDINARY ENGLISH
    NOUN satisfied the rule: E3's own wording — *"``Point`` + ``Event`` … that are the SOURCE of at
    least one ``extractedFrom`` relationship"* — yielded ``{'Point', 'Event', 'Source'}`` and PASSED,
    i.e. a cell naming NO document class at all was accepted by the very arm written to refuse it,
    against §7 item 6's *"a denominator that omits a class the model does anchor fails too"* (quoted
    here as that rule states it; the document-class requirement is this arm's own derivation from it,
    not a sentence the registration contains). A Cypher node
    label is case-sensitive, so ``Point``/``Event``/``Document``/``Object``/``Source`` are the class
    spellings and ``SOURCE``/``source`` are prose. The canonical name is the TOKEN's value
    (``NODE_CLASS_TOKENS``), which is exactly the label spelling, so it is matched rather than the
    lower-case key; the plural stays tolerated (``Points`` names the class too).

    ⛔ CASE ALONE MAY NOT BUY A SKIP (#10525 review round 3, P2 — MEASURED). Making the match
    case-sensitive opened the inverse hole: a row whose cells are spelled ``point + object + event``
    names no canonical class, so the caller's "names no graph class at all" arm SKIPPED it — and on
    the previous matcher that same row was REFUSED for naming ``Object``. A lower-cased row is a
    SPELLING error, never a criterion measured over something else, which is why
    ``_class_words_mentioned`` exists: the caller asks it before treating an empty ``named`` set as
    "not a class-set row".
    """
    plain = _plain(cell)
    return {
        canonical
        for token, canonical in NODE_CLASS_TOKENS.items()
        if re.search(rf"(?<![\w]){re.escape(canonical)}s?(?![\w])", plain)
    }


def _class_words_mentioned(cell: str) -> set[str]:
    """Which class WORDS a cell mentions at all, IGNORING case — the spelling-error detector.

    It answers one question only: is this cell talking about graph classes but spelling them in
    prose case? A cell that mentions nothing (``docs/evidence/5087-hypothesis``'s E3 is a session-arm
    receipt check) is a genuine non-class row and is declared skipped; a cell that mentions a class
    word in prose case is a spelling error and FAILS, so lower-casing a row can never opt it out of
    the arm. The returned values are the CANONICAL labels the prose tokens point at, so the failure
    message can say what the row meant.
    """
    plain = _plain(cell).casefold()
    return {
        canonical
        for token, canonical in NODE_CLASS_TOKENS.items()
        if re.search(rf"(?<![\w]){token}s?(?![\w])", plain)
    }


def check_registry_contradictions(directory: Path, rows: list[dict[str, str]],
                                  pins: dict, scopes: list[str] | None = None) -> list[str]:
    """The registry may not contradict the history it describes — nor the model it scores (#9736).

    TWO CONTRADICTIONS, one rule, because both are the same defect: a frozen artefact asserting
    something the tree already refutes, with no number changing to reveal it.

    1. **``measurement_visible_to_author`` versus ``prior_measurements``.** The field is a claim about
       HISTORY, and history is the one thing a registry can be wrong about at zero cost to its own
       consistency. E5 was: the field read ``false`` for a metric whose measurement had landed two
       days BEFORE the record (#9738). A claim is therefore held to a citation in both directions —
       ``false`` with a citation is the falsified claim, and ``true`` with none is a claim no artefact
       backs (uncheckable is not harmless: it is the same sentence, minus the evidence).
    2. **E3's class set versus the model.** ``_DERIVATION_REFERENCES_LABELS`` is what the writer
       actually anchors; a denominator naming ``:Object`` counts a class the model leaves unanchored
       BY DESIGN (#5199 option A), which makes ``= 1.000`` unreachable as registered and reports a
       recorded decision as a defect (#9736). Omitting an anchored class fails symmetrically: the
       numerator then cannot reach a denominator it must equal. ⛔ WHICH SPELLING of the
       document-derivation class is honest (``Document`` — the writer's retained relation alias,
       ``0`` nodes in the shipped census — or ``Source``, the label it is stored on post-D10) is the
       owner's #9736 ruling, NOT this gate's: both are accepted, see ``RETIRED_SPELLING_LABELS``.
       The gate settles nothing here; it refuses a cell that names NEITHER, and a cell that names an
       unanchored class. ⛔ What it ALSO cannot do is check the INSTRUMENT: it never reads
       ``probes/capstone_measure.py``, whose class set is derived from the writer's constant and so
       cannot be moved by anything written in the record. A row taking the ``Source`` spelling is
       therefore DECLARED in ``scopes`` rather than passed quietly or failed — see
       ``_note_unchecked_probe_spelling``.

    ``[]`` when the record cannot be read at all: ``check_machine_record`` owns that failure, and
    reporting it twice would make one defect look like two.

    ⛔ SCOPE — AN ARM IS ASKED ONLY OF A BUNDLE THAT MAKES ITS CLAIM (#10525 review, MEASURED).
    This gate is invoked on TWO bundles in one CI step
    (``--dir docs/evidence/7949-capstone --dir docs/evidence/5087-hypothesis``), and these two arms
    were written for the first. Applied unconditionally they red-ed the second for seven things it
    never said: six criteria that carry no ``measurement_visible_to_author`` field and no
    ``prior_measurements`` block (a record that predates the field makes no visibility claim, so it
    has no claim to contradict), and an E3 that is a SESSION-ARM criterion naming no graph class at
    all (comparing it with the anchor model is a category error — the model never scored it). So:

      * the visibility arm is asked of a record that carries the schema — ``prior_measurements``, or
        the per-criterion field on at least one criterion. Deleting ONE criterion's field does not
        opt out: the record still declares the schema, so the deleted one fails as a non-boolean;
      * the class-set arm is asked of an E3 row that names at least one graph class. A row naming
        none is declared in ``scopes`` instead. A row that names classes must name every anchored
        CLASS in EVERY cell — either spelling of the document-derivation class counts, neither
        being what #9736's ruling picks (see ``e3_required_class_groups``) — so narrowing the
        numerator to escape it is itself a failure.

    ``scopes``, when given, collects one human-readable line per arm NOT applied to this bundle. A
    skip that is not declared is the silent-green shape this tool exists to refuse, so the caller
    prints them (``--json`` carries them as ``registry_scopes``); a bundle that declares nothing is
    told so rather than passed quietly.
    """
    failures: list[str] = []
    path = directory / MACHINE_RECORD
    try:
        record = json.loads(path.read_text())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        record = None
    if not isinstance(record, dict):
        return []

    criteria = [c for c in _list(record, "criteria", MACHINE_RECORD) if isinstance(c, dict)]
    prior = _mapping(record, "prior_measurements", MACHINE_RECORD)
    by_id = {str(c.get("id") or ""): c for c in criteria}

    #: Whether this record carries the #9738 schema at all. Not `bool(prior)`: an empty
    #: `prior_measurements: {}` is a DECLARATION that there are none, and a record that declares the
    #: mechanism must still answer the per-criterion field (a bundle cannot opt out by emptying it).
    declares_visibility = "prior_measurements" in record or any(
        "measurement_visible_to_author" in c for c in criteria
    )
    if not declares_visibility and by_id:
        _note(scopes, f"{directory.name}: the record carries no `prior_measurements` block and no "
                      "criterion carries `measurement_visible_to_author`, so the #9738 rule is not "
                      "asked of it — a record that makes no visibility claim has no claim to "
                      f"contradict{_skip_evidence(directory, 'visibility')}")

    for cid in sorted(prior):
        entry = prior[cid]
        where = f"{MACHINE_RECORD} prior_measurements[{cid!r}]"
        if cid not in by_id:
            failures.append(
                f"{where} names a criterion the record does not carry — a citation for nothing"
            )
            continue
        if not isinstance(entry, dict):
            failures.append(f"{where} is {type(entry).__name__}, expected a mapping of its provenance")
            continue
        for part in ("artifact", "commit", "date"):
            if not str(entry.get(part) or "").strip():
                failures.append(
                    f"{where} carries no {part} — a prior measurement a reader cannot date or locate "
                    "cannot carry the disclosure it exists for"
                )
        if by_id[cid].get("measurement_visible_to_author") is False:
            failures.append(
                f"criterion {cid} declares measurement_visible_to_author=false while "
                f"prior_measurements cites {entry.get('artifact')!r} at {entry.get('commit')!r} "
                f"({entry.get('date')!r}) — a measurement EXISTS, so the claim is falsified "
                "(#9738). Either disclose it (flip the field and record what was known and when) "
                "or delete the citation; the two may not stand together"
            )

    for cid, crit in (by_id.items() if declares_visibility else ()):
        visible = crit.get("measurement_visible_to_author")
        if not isinstance(visible, bool):
            failures.append(
                f"criterion {cid} carries measurement_visible_to_author={visible!r}, which is not a "
                "boolean — the field is what a machine consumer reads to learn whether the bar's "
                "author could see a number"
            )
            continue
        if visible and cid not in prior:
            failures.append(
                f"criterion {cid} declares measurement_visible_to_author=true and names no "
                "prior_measurements entry — an undisclosed disclosure is uncheckable, and this "
                "field's whole value is that a reader can follow it to the artefact"
            )
        #: A bare `false` is UNFALSIFIABLE — the #10525 review's E4 shape. `prior_measurements`
        #: is the only table this gate reads, so a `false` for a metric whose measurement exists
        #: but is NOT cited is invisible to every arm here; and to a reader it is
        #: indistinguishable from a `false` nobody has revalidated. E4 was that `false`: right
        #: (the measurement ran before the record but its artifact landed after it, so nothing was
        #: in the tree the author read) with the reason stated nowhere, beside a §0 row asserting
        #: "no measurement existed". The ground is therefore required, not merely encouraged.
        if visible is False and not str(
                crit.get("measurement_visible_to_author_basis") or "").strip():
            failures.append(
                f"criterion {cid} declares measurement_visible_to_author=false and carries no "
                "measurement_visible_to_author_basis — a `false` with no stated ground cannot be "
                "told apart from a stale one, and it hides a measurement that exists but is not "
                "cited. State when the measurement ran relative to the registration and whether "
                "its artefact was in the tree (E4: ran 2026-10-19, artifact landed d50217273 on "
                "2026-10-22, AFTER a6085af84 — right, and previously unstated)"
            )

    pins_prior = _pins_mapping(pins, "prior_measurements")
    pins_map = {str(k): str(v) for k, v in pins_prior.items()}
    rec_map = {
        str(k): str((v or {}).get("commit")) if isinstance(v, dict) else ""
        for k, v in prior.items()
    }
    if pins_map != rec_map:
        failures.append(
            f"pins.prior_measurements is {pins_map!r} but {MACHINE_RECORD} cites {rec_map!r} — two "
            "copies of a commit pin that can disagree is how a reader is sent to the wrong history"
        )

    # ⛔ SCOPED ON THE RIGHT SUBJECT (#10525 review round 2 P3, corrected round 3 P3 and round 11
    # P2 — MEASURED, each time). Three shapes, and only these three:
    #
    #   * the DOCUMENT registers E3 and the RECORD does not  -> FAIL (the disagreement itself);
    #   * neither registers it                              -> DECLARED skip (a failure on a subject
    #                                                          the bundle never raised is the mirror
    #                                                          of the silent-green shape this tool
    #                                                          refuses);
    #   * both do                                           -> the class-set arms below.
    #
    # The first version failed UNCONDITIONALLY (a record that adopts the visibility schema but raises
    # no E3 criterion was refused for a criterion it never registered). The second wrote the
    # disagreement test INSIDE `if not e3:` as `document_registers_e3 = any(r["id"] == "E3" for r in
    # rows)` — the same expression as `e3` itself, so it was constantly False there and the failure
    # was dead code: a record that silently DROPPED its E3 criterion while the document kept one
    # passed the gate with a declared-skip note, which is exactly the asymmetry the arm exists to
    # report. Round 11 found it by reading; `test_a_machine_record_that_drops_its_e3_row_is_refused`
    # now reds on it (measured `[]` at the pre-fix head).
    e3 = [r for r in rows if r["id"] == "E3"]
    if e3 and "E3" not in by_id:
        failures.append(
            "no E3 row — the DOCUMENT registers an E3 criterion but the machine record carries "
            "none, so the class set it fixes cannot be compared with the model"
        )
    if not e3:
        _note(scopes, "no E3 criterion is registered by this bundle, so its class set cannot "
                      "be compared with the model — the #9736 arm is not asked of it (the same "
                      "declared-skip rule as the #9738 arm above)")
    groups: tuple[frozenset[str], ...] | None = None
    for row in e3:
        cells = {name: _classes_named(row[name]) for name in E3_CLASS_CELLS}
        named = cells["metric"] | cells["denominator"]
        if not named:
            mentioned = (_class_words_mentioned(row["metric"])
                         | _class_words_mentioned(row["denominator"]))
            if mentioned:
                # ⛔ A LOWER-CASED ROW IS A SPELLING ERROR, NOT A NON-CLASS ROW (#10525 review
                # round 3 P2 — MEASURED). Treating it as "measured over something else" skipped the
                # arm for a row that names `Object`, the one class the arm exists to refuse.
                failures.append(
                    f"E3 (line {row['line']}) mentions {', '.join(sorted(mentioned))} but names no "
                    "graph class by its LABEL spelling — a Cypher node label is case-sensitive "
                    "(`Point`/`Event`/`Document`/`Object`/`Source`), and prose case is a SPELLING "
                    "error, not a criterion measured over something other than written nodes. "
                    "Spell the label, or declare a criterion that is not a class-set row"
                )
                continue
            _note(scopes, f"{directory.name}: E3 (line {row['line']}) names no graph class at "
                          "all, so the anchor model does not apply to it — a criterion measured "
                          "over something other than written nodes is not a class-set"
                          f" row{_skip_evidence(directory, 'class_set')}")
            continue
        if groups is None:
            groups = e3_required_class_groups()
        failures += _e3_class_set_failures(row, cells, groups)
        _note_unchecked_probe_spelling(scopes, directory, row, cells)
    return failures


#: The two cells that must each name every anchored class group (#9736). Both, not their union:
#: a numerator narrower than its own denominator cannot reach it, which is the #9736 defect one
#: level down, and a union test passes exactly that row.
E3_CLASS_CELLS = ("metric", "denominator")


#: Why a class missing from each cell fails, in the words of the cell it is missing from.
_E3_CELL_OMISSION_REASON = {
    "metric": "a numerator that cannot count an anchored class cannot reach the denominator it must "
              "equal (#9736)",
    "denominator": "a denominator that does not count an anchored class scores a subset of the "
                   "nodes the model writes, so `= 1.000` measures something narrower than the row "
                   "claims (#9736)",
}


def e3_required_class_groups() -> tuple[frozenset[str], ...]:
    """Each anchored class as the set of TOKENS a row may name it by (#10525 review P1).

    One group per anchored class, and a cell must satisfy **every** group — which is what keeps the
    per-cell (not union) discipline. A class whose relation spelling has a documented retired node
    label (``Document`` → ``Source``) is ONE group with two members: the owner's #9736 ruling is
    still open on which spelling is honest, so a row naming either satisfies it and the gate
    settles nothing (see ``RETIRED_SPELLING_LABELS``). ``Point`` and ``Event`` are single-token
    groups because both are real node labels in the census — there is no second way to spell them.
    """
    groups = [frozenset(SELF_ANCHORED_CLASSES)]
    for spelling in sorted(derivation_label_classes()):
        label = RETIRED_SPELLING_LABELS.get(spelling)
        groups.append(frozenset({spelling, label}) if label else frozenset({spelling}))
    return tuple(groups)


def _e3_class_set_failures(row: dict[str, str], cells: dict[str, set[str]],
                           groups: tuple[frozenset[str], ...]) -> list[str]:
    """E3's class set, held to the model **cell by cell** (#10525 review P1).

    The previous revision compared ``metric | denominator`` with one flat set. That made the
    omission arm inert against the shape it was written for: each class only had to appear in ONE
    of the two cells, so a metric counting ``Point`` + ``Event`` against a denominator counting
    ``Point`` + ``Event`` + ``Document`` — #9736's own defect, one level down — produced no failure.
    Measured on the shipped row with only the metric cell mutated: baseline ``[]``, metric-only
    omission ``[]``. Every cell is now held to the anchored set on its own.

    ⛔ THE CLASS IS THE UNIT, NOT THE SPELLING (#10525 review P1). The flat set was built from
    ``_DERIVATION_REFERENCES_LABELS``, which the writer documents as a set of RELATION SPELLINGS
    (``edges.py:716-744``), not node labels — and post-D10 ``Document`` is retired as a LABEL while
    the derivation is stored as ``(Source)-[:references]->(Source)``. Holding cells to that flat set
    therefore made ``Source`` (exactly the anchored ``references`` target) an UNANCHORED class to be
    REFUSED, and made ``Document`` — ``0`` nodes in the shipped census — MANDATORY. Both are the
    gate settling #9736's open question; with alias groups, whichever the owner rules is a row this
    rule accepts.
    """
    permitted = frozenset().union(*groups)
    failures: list[str] = []
    counted = sorted((cells["metric"] | cells["denominator"]) - permitted)
    if counted:
        where = " and ".join(name for name in E3_CLASS_CELLS if cells[name] & set(counted))
        failures.append(
            f"E3 (line {row['line']}) counts {counted} among its derived nodes in its {where}, but "
            f"the model anchors only {sorted(permitted)} ({DERIVATION_LABELS_NAME} + "
            f"{list(SELF_ANCHORED_CLASSES)}; a retired spelling's node label counts as that class) "
            "— an unanchored class in the numerator makes `= 1.000` unreachable as registered, and "
            "reports a recorded decision (#5199 option A) as a defect (#9736)"
        )
    for name in E3_CLASS_CELLS:
        for group in groups:
            if cells[name] & group:
                continue
            reason = _E3_CELL_OMISSION_REASON[name]
            if len(group) == 1:
                missing = sorted(group)
                failures.append(
                    f"E3 (line {row['line']}) omits {missing} from its {name}, but the model "
                    f"anchors {sorted(permitted)} — {reason}"
                )
            else:
                spellings = " nor ".join(f"`{member}`" for member in sorted(group))
                failures.append(
                    f"E3 (line {row['line']}) omits the document-derivation class from its "
                    f"{name}, naming neither {spellings} — the model anchors that class, and "
                    f"{reason}"
                )
    return failures


def _note_unchecked_probe_spelling(scopes: list[str] | None, directory: Path,
                                   row: dict[str, str], cells: dict[str, set[str]]) -> None:
    """Declare the ONE thing this rule cannot check: the INSTRUMENT's own class set (#10525 P2).

    The gate accepts either spelling of the document-derivation class
    (``RETIRED_SPELLING_LABELS``), because which one is honest is #9736's owner-gated ruling. But
    nothing in this tool reads ``probes/capstone_measure.py``: the probe derives its
    ``DERIVED_LABELS`` from the WRITER's constant (``("Point", *sorted(
    _derivation_label_classes()))``), so no sentence in the record can move it. A registration that
    takes the ``Source`` spelling therefore registers a node class the probe never enumerates —
    ``Document`` is the writer's own spelling and is ``0`` nodes in the shipped census, ``Source`` is
    counted nowhere — so the derivations that spelling names contribute ``0`` to E3 by construction.

    ⛔ DECLARED, NOT ENFORCED AND NOT SILENT (#9736 IS OWNER-GATED). Failing the row would turn the
    owner's open alternative into a red check — the gate settling the ruling it must not settle; and
    saying nothing would let a registration and the instrument that scores it part company with no
    trace anywhere, which is the divergence §7 item 6 is about.
    """
    drifted = sorted((cells["metric"] | cells["denominator"])
                     & set(RETIRED_SPELLING_LABELS.values()))
    if not drifted:
        return
    _note(scopes, f"{directory.name}: E3 (line {row['line']}) spells the document-derivation "
                  f"class {drifted}, which this rule ACCEPTS (#9736's spelling is owner-gated) and "
                  "which it does NOT check against the instrument — probes/capstone_measure.py "
                  f"derives DERIVED_LABELS from {DERIVATION_LABELS_NAME} alone "
                  f"({sorted(derivation_label_classes())}), so it enumerates no `Source` and the "
                  "derivations this spelling names count 0 in E3; moving the probe with the "
                  "spelling is the amendment's obligation, not this gate's check")


def _note(scopes: list[str] | None, message: str) -> None:
    """Declare something this gate did NOT enforce — never a silent skip, never a silent pass.

    Two kinds are declared here, both because an undeclared one is a false green: an ARM this bundle
    is not asked (see the caller's docstring), and a RESIDUAL the rule can see but must not fail on
    because the decision behind it is owner-gated (``_note_unchecked_probe_spelling``).

    ⛔ ``scopes`` IS OPTIONAL, BUT AN OMITTED ONE IS NOT A SILENT ONE (#10525 review, residual). It
    defaults to ``None`` so an existing caller with nowhere to put the notes need not build a list;
    the cost of that default is that ``append`` has no list to append to, and the note would then
    evaporate — a gate that skipped an arm and said nothing, which is exactly the false green this
    function exists to prevent. So the no-list path writes the same declaration to ``stderr``, the
    stream this tool already uses for ``ERROR:`` lines. There is no fifth state in which a note is
    neither carried nor printed; ``tests/test_capstone_preregistration_9075.py``
    (``test_a_caller_that_carries_no_scope_list_still_declares_the_skips``) holds that.
    """
    if scopes is None:
        print(f"NOTE — declared, not carried (the caller passed no scope list): {message}",
              file=sys.stderr)
        return
    scopes.append(message)


#: The MEASURED ground for an arm a NAMED bundle is not asked, keyed by (bundle directory, arm).
#:
#: ⛔ KEYED BY BUNDLE, NOT BAKED INTO THE MESSAGE (#10525 review P3, MEASURED). The note is emitted
#: for WHICHEVER bundle skipped the arm, so a sibling's evidence written into the sentence explained
#: the capstone's own skip with `docs/evidence/5087-hypothesis`'s receipt criterion — reproduced by
#: stripping the class names from the capstone's E3 row, which the gate then explained as the
#: sibling's criterion. A declared skip whose declaration names the wrong subject reads as a checked
#: one, which is the class this tool exists to refuse. A bundle with NO entry here still gets the
#: note (naming it is what makes the skip checkable); it gets no evidence clause rather than
#: somebody else's.
_BUNDLE_SKIP_EVIDENCE = {
    ("5087-hypothesis", "visibility"): "this bundle's record predates the field",
    ("5087-hypothesis", "class_set"): "this bundle's E3 is a session-arm receipt check",
}


def _skip_evidence(directory: Path, arm: str) -> str:
    """The measured clause for THIS bundle's skip — empty when nobody measured one for it."""
    measured = _BUNDLE_SKIP_EVIDENCE.get((directory.name, arm))
    return f" (measured: {measured})" if measured else ""



def _measured_locations(record: dict) -> list[str]:
    """Every place the machine record carries a MEASUREMENT, as human-readable labels.

    WHY IT WALKS THE WHOLE RECORD (#9089 review P1): the exemption asks "did this log produce a
    number AT ALL", and a number is not confined to ``rows``. The E4 receipt is written to
    ``run.e4_receipt`` on the completed path, so a REFUSED run never carries one — a log that
    carries both a REFUSED status and receipt numbers is claiming a measurement its own run says
    it never took, and reading only ``rows`` exempted exactly that document. A hand-editable
    artifact can put a number in any container, so the predicate asks the document instead of the
    one field it happens to know about.

    ⛔ TWO CONTAINER NAMES, NOT "ANY NUMBER ANYWHERE" (#9089 review P2). The receipt's counts are
    not all called ``numerator``/``denominator`` — ``e4_receipt`` carries ``s4_items``,
    ``merged_items``, ``corrected_by_s4``, ``verbatim_reemissions`` and ``added_by_s4`` too — so a
    two-key match exempted a record carrying five other numbers. It cannot be widened to "any
    number in the document": the shipped log legitimately carries ``run.gate_failed: 2``, and a
    blanket rule would over-block the very artifact this exemption exists for. So the two named
    measurement keys are honoured wherever they sit, and the ``e4_receipt`` container's numeric
    leaves count as measurements. A boolean is never a count (``True`` is an ``int`` in Python and
    the gate refuses booleans as denominators — the same rule here).
    """
    found: list[str] = []
    stack: list[tuple[str, object]] = [("record", record)]
    while stack:
        where, node = stack.pop()
        if isinstance(node, dict):
            label = str(node.get("criterion") or where)
            for key, item in node.items():
                if key in ("numerator", "denominator") and item is not None:
                    found.append(label)
                elif key == "e4_receipt" and _carries_number(item):
                    found.append(f"{where}.{key}")
                else:
                    stack.append((f"{where}.{key}", item))
        elif isinstance(node, list):
            for index, item in enumerate(node):
                stack.append((f"{where}[{index}]", item))
    return found


def _carries_number(node: object) -> bool:
    """Does this container hold a numeric leaf? Iterative, so a hostile document cannot recurse."""
    stack = [node]
    while stack:
        item = stack.pop()
        if isinstance(item, bool):
            continue
        if isinstance(item, (int, float)):
            return True
        if isinstance(item, dict):
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    return False


def _log_declares_no_measurement(log: Path) -> tuple[bool, str]:
    """Does the LOG ITSELF prove it produced NO measurement? Returns (ok, reason).

    The ordering rule exists so a number cannot be scored against thresholds written
    afterwards. A log that carries no number AT ALL has nothing to launder — and its own machine
    record says so, in two independent conditions this predicate requires together:

      * no row carries a ``numerator`` or a ``denominator`` (a measurement is *absent*, not
        merely unverdicted);
      * ``run.status`` is not ``COMPLETED`` — the run did not finish, so it did not measure.

    ``prereg.status`` is REQUIRED to be present, and carries NO exemption weight (see the
    measured over-block at the end of this docstring).

    ⛔ THE VOCABULARY IS THE PRODUCER'S, NOT AN INVENTED ONE (#9089 review P1). This arm used to
    test ``prereg.status == "PRESENT"`` and to read numbers out of ``rows`` only. ``PRESENT`` is a
    value ``capstone_run.py::refuse`` NEVER writes (it records ``ABSENT``, ``OK`` or ``UNREAD``),
    so that condition was INERT, and the measurement test missed the E4 receipt because it read
    only ``rows``. The measurement test now walks the WHOLE machine record, so a number in any
    container this artifact can produce is a refusal.

    ⛔ AND THE PREREG STATUS IS NOT AN EXEMPTION CONDITION AT ALL (#9089 review cycle 4,
    MEASURED). A revision of this arm required ``prereg.status == "ABSENT"``; the bundle's own
    documented re-run refutes that, measured at this head: ``capstone_run.py --prereg <sha>
    --corpus battery/config/corpus.json`` passes G0 (the artifact is committed) and refuses at G1
    (the registration freezes no corpus sha), emitting ``prereg.status=OK``,
    ``run.status=REFUSED``, and every numerator/denominator null — a log that PROVES it measured
    nothing. Refusing to exempt it turns the bundle's own acceptance command into an ordering
    violation and a repo-wide red. The question here is what the log MEASURED, which the two arms
    above answer exactly; the prereg status records what the run READ, and cannot by itself make
    laundering possible (a log with no number has nothing to launder, whichever registration it
    claims to have seen).

    ⛔ WHY THIS IS NOT A PINNED EXEMPTION (#9089). `pre_existing_unscored_logs` grandfathers a
    FILE and therefore has to prove, forever, that the file was never edited into a scored one.
    This predicate asks its question of the log's CONTENT on EVERY run, so it is self-healing: the
    moment a row gains a number, or the run completes, the log falls back into the ordered class
    and this gate reds again. It is fail-closed in the same
    direction — an unreadable log, a missing machine record, a non-JSON block, no rows, or a
    missing status is a REFUSAL to exempt, never a pass.
    """
    try:
        text = log.read_text(errors="replace")
    except OSError as exc:
        return False, f"cannot be read ({exc})"
    begins, ends = text.count(CAPSTONE_BEGIN), text.count(CAPSTONE_END)
    if (begins, ends) != (1, 1):
        return False, (
            f"carries {begins} BEGIN and {ends} END verdict markers — a log with more than one "
            "block can hide a number in the block this reader did not take, so an ambiguous "
            "record is a refusal (the same rule `capstone_verdict_check.extract_block` applies)"
        )
    match = CAPSTONE_RECORD.search(text)
    if not match:
        return False, "carries no capstone machine record, so it cannot prove it measured nothing"
    try:
        record = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        return False, f"its machine record is not JSON ({exc})"
    except RecursionError:
        # #9089 review P2: a document nested past the parser's limit raised RecursionError, which
        # is NOT a JSONDecodeError — it escaped this predicate and surfaced as exit 1, the code
        # this tool reserves for FINDINGS, on a document it never read. An unreadable shape is a
        # refusal (exit 2 via the caller's CheckError path is a reader problem, but the exemption
        # question has a safe answer here: do not exempt).
        return False, (
            "its machine record nests too deeply to parse — a shape this predicate cannot read "
            "is a refusal, not an exemption"
        )
    if not isinstance(record, dict):
        return False, "its machine record is not an object"
    # ⛔ The schema is PINNED, not inferred. Without it the whole "self-healing" argument is bounded
    # by a shape nothing validated: a `capstone-verdict-v2` that relocated the number (a `value`
    # or `ratio` field) would be exempted rather than refused. An unrecognised schema is a shape
    # this predicate cannot read, so it is a refusal.
    # Resolved BOTH ways on purpose: this module is imported as `tools.capstone_preregistration_check`
    # in tests and run as a SCRIPT in CI (invoked by path, which puts `tools/` on sys.path[0] and
    # knows no package). Either spelling must find the sibling — and a sibling that cannot be
    # found is a refusal, never a pass. (The CI step's form is spelled the way the entry-point
    # guard requires: `uv run python <path>`, never a bare `python3 <path>` in prose —
    # tests/test_entry_point_python_guard.py::test_no_doc_or_usage_advertises_the_form_the_guard_refuses
    # reads comments, not just docs, and redded a first version of this note.)
    try:
        import capstone_verdict_check as _cvc  # script invocation
    except ImportError:
        try:
            from tools import capstone_verdict_check as _cvc  # package invocation
        except Exception:
            return False, (
                "the capstone verdict check could not be imported, so its schema is unknown")
    expected_schema = _cvc.SCHEMA
    if record.get("schema") != expected_schema:
        return False, (
            f"its machine record declares schema {record.get('schema')!r}, not {expected_schema!r} "
            "— a shape this predicate cannot read is a refusal, not an exemption"
        )
    rows = record.get("rows")
    if not isinstance(rows, list) or not rows:
        return False, "its machine record carries no rows"
    measured = sorted(set(_measured_locations(record)))
    if measured:
        return False, (
            f"it carries a measurement for {', '.join(measured)} — a number in the log is exactly "
            "what the ordering rule protects"
        )
    run = record.get("run") if isinstance(record.get("run"), dict) else {}
    status = str(run.get("status") or "")
    if not status:
        return False, "its machine record declares no run.status"
    if status == "COMPLETED":
        return False, "it declares run.status=COMPLETED, so the run did measure"
    prereg = record.get("prereg") if isinstance(record.get("prereg"), dict) else {}
    prereg_status = str(prereg.get("status") or "")
    if not prereg_status:
        return False, "its machine record declares no prereg.status"
    # ⛔ NO PREREG-STATUS CONDITION (#9089 review cycle 4, MEASURED). An earlier revision exempted
    # only on `ABSENT` and refused `OK`/`UNREAD`. The bundle's OWN documented re-run contradicts
    # that: `capstone_run.py --prereg <sha> --corpus battery/config/corpus.json | tee
    # docs/evidence/7949-capstone/capstone.log` passes G0 and refuses at G1, so it emits
    # `prereg.status=OK`, `run.status=REFUSED` and every numerator/denominator null — a log that
    # proves it measured nothing — and the ABSENT-only arm refused it, which turns the acceptance
    # command into an ordering violation (and a repo-wide red) the moment that log is committed.
    # The question this predicate actually asks is "did this log produce a number", and the two
    # arms above answer it exactly: no number anywhere (including inside the E4 receipt), and a
    # run that did not complete. The prereg status says what the run READ, not what it MEASURED,
    # so it cannot on its own make laundering possible — a log with no number has nothing to
    # launder whatever registration it claims to have seen. The status is still REQUIRED (a
    # record that does not state it is a shape this predicate does not understand).
    return True, ""


#: What the report says when the ordering arm RETURNED BEFORE comparing a single epoch against
#: the base. The refusal can be perfectly correct and still have measured nothing — an untracked
#: registration with logs refuses on `_is_tracked`, and a contribution whose registration is not
#: on the base refuses on an empty base add-set — so `ordering: "verified"` plus a resolved ref
#: would say a measurement happened when the loop never ran. That is the cycle-7 F2 shape (the
#: resolved base printed on a tree where nothing was measured) on a path F2 did not close, because
#: F2 only covered the arm that RAISES; these arms RETURN (#10463 review cycle 8, follow-up 3).
ORDERING_UNMEASURED = "UNMEASURED"

#: What ``run()`` reports as ``ordering_base`` when the PROPOSED rule is armed. The armed rule orders
#: the pair out of this contribution's own history, so there is no base ref it was measured against;
#: printing the resolved candidate would claim a measurement the rule never made (#10463 cycle 4).
PROPOSED_ORDERING_BASE = "(this contribution — the proposed rule; no base is consulted)"


#: The MACHINE RECORD key that discloses an ATOMIC ADD: a pre-registration and an evidence log that
#: entered the repository in the SAME commit. Per-log, and ALL THREE fields are REQUIRED:
#: ``{log name: {reason, branch_commits, squash_merge}}``. A block that omits ``squash_merge`` is
#: REFUSED as "not a usable disclosure": a squash leaves the ``branch_commits`` unreachable from
#: main, so ``squash_merge`` is the ONE pointer a reader can resolve and verify. This block used to
#: document the two-field shape the validator rejects — an author following the only definition of
#: the key wrote a disclosure the tool refused (#10546 review cycle 20, IS-1).
#:
#: ⛔ IT IS NOT A WAIVER UNLESS THE ARM IS ARMED (#10546 review cycle 17, IS-1) — and it is NOT
#: IGNORED WITHOUT IT (#10546 review cycle 29, ISSUE 2). What the arm buys is the PASS: disarmed —
#: the CLI, ``run()`` and every invocation CI performs — an atomic pair is REFUSED and a present
#: block is reported as INERT. The block is still READ on that path, and it can FAIL there: invalid
#: or missing fields, a name that is not in the directory, or an entry carried by an ORDINARILY
#: ordered log, which the anti-rot rule refuses while disarmed BY DESIGN. So an author who commits
#: an entry must expect it to be judged, never assumed harmless. The ruling on ``#10463`` (comment
#: ``6564634454``) keeps the refusal and its reopen (``6566767704``) is ungranted; see
#: ``check_ordering`` for the full ground.
#:
#: ⛔ WHY THIS EXISTS AND WHY IT IS NOT ROUTE B (#10529) — the full argument is in the module
#: docstring's "FOURTH outcome" section; one copy on purpose, so the two cannot rot apart. In short:
#: ``<=`` conflated two states, and only one of them is an attack. The verdict is decided by COMMIT
#: IDENTITY and ANCESTRY, never by the epoch (``%ct`` is attacker-controlled): the log's add-commit
#: being an ancestor of the registration's is a HARD FAIL no disclosure can waive; the registration's
#: being the ancestor is correctly ordered; the SAME commit is ATOMIC and carries NO information — a
#: squash merge of an ordered branch and one commit written after the numbers produce the IDENTICAL
#: git object — so it requires a disclosure instead of a violation it cannot prove. Route B (re-add the log in
#: a fresh commit) was refused: it is the fabrication the guard prevents, and it does not work on
#: EITHER path — the ruled path takes ``min(add_epochs)``, the OLDEST add, and the armed path
#: refuses any add-commit git cannot order against the registration (#10546 review cycle 26,
#: ISSUE 1) — so a re-add can never replace the entry that was actually made (``#9075 review r3
#: P3``).
#:
#: ⛔ WHERE THE DISCLOSURE LIVES (#10529 review cycle 2, P1). NOT in the pre-registration's pins
#: block: that block is inside the document `document_sha256` freezes, so adding to it re-cuts the
#: digest — and a re-cut makes the record describe a document that POST-DATES the run, which is
#: exactly what `tools/s3_prior_ablation.py`'s record↔document cross-check refuses (`REFUSED — the
#: pre-registration at cfa3de857 hashes to sha256:70264efe…, but preregistration.json pins …`) and
#: what this bundle's own errata forbids ("editing it would re-cut the registration this
#: falsification is reported against"). A disclosure is a claim about HISTORY, and history is the
#: machine record's subject — the same home `written_before_any_scored_run_basis` already uses. The
#: document stays byte-identical, and its standing comes from REVIEW OF THE COMMITTED RECORD, not
#: from a digest: the disclosure sits BESIDE the document, never inside its hash.
ATOMIC_ADD_KEY = "atomic_add_ordering"

#: A commit sha as the leading token of a ``branch_commits`` or ``squash_merge`` value. FORMAT only
#: — resolvability is deliberately NOT required for ``branch_commits`` (#10529 review P1): a squash
#: merge leaves the branch commits unreachable from ``main``, so requiring the object to be present
#: would make the disclosure permanently red in exactly the case it exists for — the stuck light
#: this change removes. It is still a checkable claim: a reader resolves the sha from the PR that
#: carried it. ⛔ That reasoning stops at ``branch_commits``: ``squash_merge`` is on ``main`` by
#: construction and MUST resolve (#10529 review cycle 14, P2 — see ``_resolves_to_commit``).
COMMIT_SHA_TOKEN = re.compile(r"^[0-9a-f]{7,40}$")


def _sha_prefix_agrees(left: str, right: str) -> bool:
    """Do two ABBREVIATED commit shas name the same commit? Compared by prefix, either length.

    The real bundle writes nine characters (`a1075dfc2`), a reader may write seven or forty, and
    `git rev-parse` accepts every length in between — so an equality test on the raw strings would
    let the same commit be written twice in two lengths and pass a "these are different" rule
    (#10529 review cycle 15, P1).
    """
    shorter = min(len(left), len(right))
    return shorter > 0 and left[:shorter] == right[:shorter]


def _unverified_history_note(detail: dict) -> str:
    """The line printed when NONE of a disclosure's ``branch_commits`` is in this checkout.

    ⛔ A CLAIM THIS TOOL COULD NOT CHECK MAY NOT READ AS ONE IT DID (#10529 review cycle 15, P1).
    The disclosure records where the ordering DID exist; a squash leaves those commits unreachable
    from `main`, so in a clone taken after the landing they may simply not be here — which is why
    their ABSENCE is not a failure (requiring the object would re-create the stuck light this change
    removes). But silence about it made the printed block look verified when nothing in it had been
    resolved. Where the objects ARE present the check does verify them (see `_atomic_add_failure`);
    where they are not, it says so on the same line a reader would otherwise read as evidence.
    """
    commits = [str(entry).split()[0] for entry in detail.get("branch_commits") or []
               if str(entry).strip()]
    if not commits or any(_resolves_to_commit(commit) for commit in commits):
        return ""
    return (
        "none of these commits is in this checkout, so the branch history this rests on was NOT "
        "checked here — a squash leaves them unreachable from main; the disclosure records where "
        "the ordering DID exist, it does not prove it"
    )


def _record_atomic_add(directory: Path) -> dict:
    """The ``atomic_add_ordering`` block from the bundle's machine record.

    Returns ``None`` when the record cannot be read at all. ``check_machine_record`` owns that
    failure, and reporting it here too would make one defect look like two — worse, the second one
    was WRONG-INSTRUCTIONED: it told the author to "disclose it under atomic_add_ordering" when the
    real defect was that there is no readable record to disclose it in (#10529 review cycle 6, P2).
    """
    try:
        record = json.loads((directory / MACHINE_RECORD).read_text())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        return None
    if not isinstance(record, dict):
        return None
    return _mapping(record, ATOMIC_ADD_KEY, MACHINE_RECORD)


def _atomic_add_failure(declared: object, prereg_epoch: int,
                        prereg_sha: str = "") -> str | None:
    """``None`` when the disclosure is complete, else the one reason it is not.

    Fail-closed on TYPE, not merely on emptiness (#10529 review P2): ``str({})`` is truthy, so a
    truthiness test alone accepted ``reason: {a: 1}`` and ``branch_commits: [{}]`` — a shrug wearing
    a disclosure's shape, which is indistinguishable from a stale one. ``reason`` must be a
    non-empty STRING and every ``branch_commits`` entry must be a non-empty string whose leading
    token is a commit sha, so the claim points at something a reader can go and look at.
    """
    if not isinstance(declared, dict):
        return (
            f"it is {type(declared).__name__}, expected a mapping carrying the disclosure fields "
            "— an unparseable disclosure is a refusal"
        )
    reason = declared.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        return (
            "it carries no reason — an atomic add at "
            f"{prereg_epoch} is indistinguishable from a laundering commit without a stated ground"
        )
    commits = declared.get("branch_commits")
    if not isinstance(commits, list) or not commits:
        return (
            "its branch_commits names no commit — the disclosure must say where the ordering DID "
            "exist, or a reader cannot check the claim it is being asked to accept"
        )
    for entry in commits:
        if not isinstance(entry, str) or not entry.strip():
            return (
                "its branch_commits carries "
                f"{type(entry).__name__ if not isinstance(entry, str) else 'an empty string'}, "
                "expected a commit summary — a disclosure no reader can follow is a refusal"
            )
        token = entry.split()[0]
        if not COMMIT_SHA_TOKEN.match(token):
            return (
                f"its branch_commits entry {entry!r} does not begin with a commit sha — name the "
                "commit, or the disclosure asserts a history without pointing at it"
            )
    # ⛔ `squash_merge` is the ONE pointer a reader can actually resolve: a squash leaves the branch
    # commits unreachable from `main`, and the squash commit itself is on main (#10529 review cycle
    # 3, P2). Required, format-checked, and printed ahead of the branch commits.
    squash = declared.get("squash_merge")
    if not isinstance(squash, str) or not squash.strip():
        return (
            "it carries no squash_merge — the branch commits a squash leaves behind are not "
            "reachable from main, so the squash commit is the only pointer a reader can resolve"
        )
    squash_token = squash.split()[0]
    if not COMMIT_SHA_TOKEN.match(squash_token):
        return (
            f"its squash_merge {squash!r} does not begin with a commit sha — the merged commit is "
            "the one pointer on main, and an unaddressable one points nowhere"
        )
    # ⛔ FORMAT IS NOT ADDRESSABILITY (#10529 review cycle 14, P2). `squash_merge` is required
    # *because* it is the one pointer on main a reader can resolve; checking only its shape made
    # that argument a decoration — `deadbeef1` passed every arm and was then printed as "squashed
    # by deadbeef1". `branch_commits` stays format-only (a squash leaves them unreachable); this
    # one is on `main` by construction, so an unresolvable value is a claim about nothing.
    if not _resolves_to_commit(squash_token):
        return (
            f"its squash_merge {squash!r} names a commit this repository does not have — the "
            "merged commit is the one pointer a reader can resolve, so a value that addresses "
            "nothing cannot be told from a fabricated one; name the commit that actually landed "
            "the pair"
        )
    # ⛔ A SQUASH IS NOT ONE OF THE COMMITS IT COLLAPSED (#10529 review cycle 15, P1). The two
    # halves of this disclosure make DIFFERENT claims: `branch_commits` is where the ordering DID
    # exist, `squash_merge` is the new commit that collapsed them. Naming one commit in both
    # asserts that a commit collapsed itself — and that is precisely the shape a single laundering
    # commit takes when it cites itself as its own provenance. This is the one constraint every
    # legitimate shape satisfies for free: a squash is a NEW commit, never one of its own inputs.
    for entry in commits:
        if _sha_prefix_agrees(entry.split()[0], squash_token):
            return (
                f"its squash_merge {squash!r} is ALSO one of its branch_commits — a squash commit "
                "is a new commit and cannot be one of the commits it collapsed; a disclosure that "
                "cites itself as its own provenance asserts nothing a reader can check"
            )
    # ⛔ AND IT MUST HAVE BEEN ABLE TO LAND THE PAIR (#10529 review cycle 15, P1). `squash_merge` is
    # printed as the pointer ON MAIN that a reader resolves, so it must be a commit at or after the
    # pair's add-commit. An ANCESTOR of it — the commit main sat at BEFORE the landing — describes a
    # history in which the pair is not in the tree at all, and resolves, so the format and
    # addressability rules both pass it. Ancestry, not the epoch: `%ct` is attacker-controlled
    # everywhere in this module.
    if prereg_sha and not _is_ancestor(prereg_sha, squash_token):
        return (
            f"its squash_merge {squash!r} is not {PREREG_NAME}'s add-commit {prereg_sha[:9]} nor a "
            "descendant of it — a commit that predates the pair cannot be the commit that landed "
            "it, so the pointer resolves to a history in which the pair is not in the tree"
        )
    return None


def check_ordering(prereg: Path, logs: list[Path], pins: dict | None = None,
                   exempted: list[str] | None = None,
                   atomic_added: list[str] | None = None,
                   atomic_disclosures: dict | None = None,
                   atomic_detail: dict | None = None,
                   record_unreadable: bool = False,
                   unreadable_atomic: list[str] | None = None,
                   allow_atomic_disclosure: bool = False,
                   base_ref: str | None = None,
                   unmeasured: list[str] | None = None) -> list[str]:
    """``[]`` when every log is either after the registration or provably unscored.

    ⚠️ THAT SENTENCE IS THE RULED RULE'S. The armed rule narrows "provably unscored" to logs INSIDE
    the registration's lineage — it refuses an off-lineage unscored log by CAUSE (#10546 review at
    `99d79dd4b`, FINDING 1; the narrowing and its reason are stated in
    ``_check_ordering_proposed``, which is where this claim was once false).

    ⛔ TWO RULES, ONE FUNCTION, AND EXACTLY ONE OF THEM IS REACHABLE BY DEFAULT (#10546 review cycle
    17/23). The default is the RULED one — `#10463` comment `6564634454`, *"same second is not
    before — an ordering that cannot be shown is not an ordering"*, implemented on the base by
    #10548 — and it is what every caller in CI gets: `.github/workflows/python-ci.yml` runs this
    tool with no flag, so an atomic pair is a REFUSAL there. ``_check_ordering_ruled`` is that rule,
    unmodified in its verdicts.

    ``allow_atomic_disclosure`` arms THIS BRANCH'S PROPOSAL (``_check_ordering_proposed``): the
    ordering read out of the contribution's own history, ordered by ANCESTRY rather than by ``%ct``,
    with the identical-add-commit state DISCLOSED in the machine record instead of permanently red.
    That is the shape the ruling refuses, which is why it is off by default and why arming it is a
    reopen of `#10463` (requested at `6566767704`, ungranted) rather than a lane's call. The two
    rules are kept whole rather than interleaved: the union that matters is "the ruled verdict is the
    default and the proposal is reachable only behind the flag", and a splice of the two bodies would
    put two contradictory semantics on the same lines — the exact outcome the contradiction test
    exists to prevent.

    ``base_ref`` is the RULED path's: it names a base the claim is measured against, and the armed
    rule consults no base at all. ``atomic_added``/``atomic_detail`` are the ARMED path's: they
    report a DISCLOSED atomic add, and a disclosure is a ground only where the arm is armed, so the
    ruled path accepts them and never writes them (#10546 review cycle 23, ISSUE 1 — this said "on
    either path", which only a vacuous test could agree with). ``unmeasured`` and
    ``unreadable_atomic`` are written by BOTH paths, because "nothing was compared" and "the record
    could not be read" are facts about the bundle, not about which rule is in force: each path
    appends the reason ITS rule compared nothing (#10546 review at `01d9ce879`, ISSUE 1 — the armed
    path had no counter and no out-param, so a bundle it walked without comparing anything reported
    `verified`). None of them decide a verdict.

    Raises ``HistoryUnavailable`` (a ``CheckError``, the NON-FAILING third outcome) when this
    checkout is shallow — the ruled path needs the base's history. ``_check_ordering_proposed``
    raises it too rather than reading a grafted tip.
    """
    if allow_atomic_disclosure:
        # The proposal is armed BY this branch of the dispatch, so the flag is passed on explicitly:
        # `_check_ordering_proposed` keeps its own `allow_atomic_disclosure` parameter (its tests call
        # it directly), and leaving it to its default would silently disarm the arm this wrapper just
        # routed to — the message says so, and the same tree would then red for the wrong reason.
        return _check_ordering_proposed(
            prereg, logs, pins, exempted, atomic_added, atomic_disclosures, atomic_detail,
            record_unreadable, unreadable_atomic, allow_atomic_disclosure=True,
            # ⛔ Passed on for the same reason the flag is: leaving it at its default would leave the
            # armed path with no way to say "nothing was compared", which is the one state its
            # `else: verified` fallback cannot tell apart from a real measurement.
            unmeasured=unmeasured,
        )
    return _check_ordering_ruled(
        prereg, logs, pins, exempted, base_ref, unmeasured,
        atomic_added, atomic_disclosures, atomic_detail, record_unreadable, unreadable_atomic,
    )


def _check_ordering_ruled(prereg: Path, logs: list[Path], pins: dict | None = None,
                   exempted: list[str] | None = None,
                   base_ref: str | None = None,
                   unmeasured: list[str] | None = None,
                   atomic_added: list[str] | None = None,
                   atomic_disclosures: dict | None = None,
                   atomic_detail: dict | None = None,
                   record_unreadable: bool = False,
                   unreadable_atomic: list[str] | None = None) -> list[str]:
    """THE RULED VERDICT — `#10463` comment `6564634454`, implemented on the base by #10548.

    ⛔ THIS IS THE DEFAULT AND IT MAY NOT BE RELAXED HERE. `check_ordering` routes every unarmed
    caller to this function, so an atomic pair is a REFUSAL for every invocation CI performs. The
    disclosure machinery below waives NOTHING on this path: it validates the block (its diagnosis is
    what tells an author what to fix), reports a disclosure that could not change the verdict as
    INERT rather than silently ignored, and holds it to the same anti-rot rule
    `pins.pre_existing_unscored_logs` applies to a pre-granted exemption. The pass-path a disclosure
    would buy lives in `_check_ordering_proposed`, reachable only when a caller arms it.

    ``[]`` when every log is either after the registration ON THE BASE or provably unscored.

    **The claim, stated so it can be argued with (#10463):** for every evidence log, the
    pre-registration's earliest add-commit *reachable from the base ref* must strictly precede the
    log's add-commit — where the log's own add-commit is read on the base when the log is already
    merged there, and in this contribution when it is not. Same second is still not before: an
    ordering that cannot be shown is not an ordering. What changed is WHICH history the ordering is
    read out of — published commits, never the contribution's own siblings, because a squash merge
    rewrites the latter into one (that is the #10468 red: the same tree passed its PR checks and
    failed on `main` minutes later).

    ``unmeasured``, when given, is APPENDED with the reason on every path that RETURNS before the
    per-log epoch comparison — the caller must not report a base it never used.

    ``exempted``, when given, is APPENDED with the name of every log the content arm exempted
    (#9089). It is not decoration: an ordering violation that was WAIVED must be visible, or the
    green line reads exactly like a log that was ordered — the silent-green shape this tool exists
    to refuse (see the ``--no-git``/``--no-corpus`` rule ninety lines below).

    Raises ``HistoryUnavailable`` (a ``CheckError``, the NON-FAILING third outcome) when this
    checkout is shallow: the add-commit epochs are not here, and a wrong verdict is worse than no
    verdict. Raises a PLAIN ``CheckError`` (exit 2) when the base ref cannot be resolved in a
    full-history checkout — that is not "unprovable", it is "this environment cannot name the ref
    the claim is about", and it must never report success.
    """
    failures: list[str] = []
    exempt = _pins_mapping(pins or {}, "pre_existing_unscored_logs")
    present = {p.name for p in logs}
    for name in sorted(set(exempt) - present):
        failures.append(
            f"pins.pre_existing_unscored_logs exempts {name!r}, which is not in this directory — "
            "an exemption that exempts nothing is how an exempt set is grown one stale row at a time"
        )

    # ── the atomic-add disclosure: read, validated and REPORTED on BOTH paths ──────────────
    # ⛔ `atomic_added` is the caller's out-param and is never read back to decide anything
    # (#10529 review P1): an out-param reports, it may not judge.
    declared_atomic = dict(atomic_disclosures or {})

    # ⛔ The stale-disclosure rule runs BEFORE the history guards' early returns (#10529 review P2):
    # a disclosure naming an absent log was invisible whenever the registration was uncommitted or
    # carried no add-commit, so a stale waiver could sit undetected in exactly the states where the
    # rest of the ordering half is not looking. It needs no history — it reads names.
    for name in sorted(set(declared_atomic) - present):
        failures.append(
            f"{MACHINE_RECORD}.{ATOMIC_ADD_KEY} discloses {name!r}, which is not in this directory "
            "— an atomic-add disclosure that names no log is a stale waiver"
        )

    # The laundering whitelist needs NO git: it reads the log's own lines. Run it BEFORE the history
    # guard, because CI test jobs are always shallow and the guard would otherwise disable the only
    # defense against a scored number for every CI run (#9075 review r6 P2).
    for log in logs:
        if log.name in exempt:
            ok, why = _log_declares_unscored(log)
            if not ok:
                failures.append(f"{log.name}: exempted, but the log {why}")

    if history_unavailable():
        raise HistoryUnavailable(HISTORY_UNAVAILABLE, failures)

    # ⛔ The git-FREE arm runs BEFORE the base guard, on purpose. "You never committed the
    # registration" is the true, specific diagnosis for a checkout with no history at all (a
    # tarball, a vendored copy, `git archive`), and it must not be replaced by the more general
    # base-ref error — the remedy is completely different (review cycle 1, finding 1).
    if logs and not _is_tracked(prereg):
        if unmeasured is not None:
            unmeasured.append(f"{PREREG_NAME} is not committed, so no add-commit was read")
        failures.append(
            f"{PREREG_NAME} is not committed, but {len(logs)} evidence log(s) exist — "
            "the ordering cannot be proved, and an unprovable ordering is not an ordering"
        )
        return failures

    base = resolve_base_ref(base_ref)
    if base is None:
        # ⛔ CheckError, NOT HistoryUnavailable (review cycle 1, finding 1). `run()` folds EVERY
        # HistoryUnavailable into the non-failing third outcome, so raising that here would make an
        # unresolvable base exit 0 — a fail-open in the one direction this gate must never fail. The
        # shallow case keeps the third outcome (CI test jobs are shallow by design, and refusing
        # there would red a correctly ordered tree); "a full-history checkout that cannot name its
        # base" is an environment/usage fault, which is exit 2.
        raise CheckError(no_base_ref_message(base_ref))

    # The registration's appearance ON THE BASE — the only side a squash merge cannot rewrite.
    prereg_epochs = _added_epochs_on(base, prereg)
    if not prereg_epochs:
        if not logs:
            # Nothing to order, so nothing was measured — a bundle with no evidence log has not
            # established an ordering, and the report must not claim it did.
            if unmeasured is not None:
                unmeasured.append("no registration on the base and no evidence log to order")
            return failures  # no registration on base, and nothing to be ordered against
        # ⛔ Two DISTINCT refusals, deliberately not one: "it has no add-commit to compare at all"
        # and "it is in THIS history but not on the base" have different remedies, and the second
        # carries both of ITS causes (same-PR, or a stale base) in one message rather than guessing.
        if not _added_epochs(prereg):
            if unmeasured is not None:
                unmeasured.append(f"{PREREG_NAME} has no add-commit anywhere to compare")
            failures.append(
                f"{PREREG_NAME} has no add-commit (git log --diff-filter=A found nothing) — "
                "cannot establish that it precedes the evidence logs"
            )
            return failures
        if unmeasured is not None:
            unmeasured.append(
                f"{PREREG_NAME} is not on the base, so no pair could be compared"
            )
        failures.append(
            SAME_PR_REFUSAL.format(prereg=PREREG_NAME, base=base, n=len(logs))
        )
        return failures
    prereg_epoch = min(prereg_epochs)
    # The commit(s) that published the registration on the base — used only to diagnose, never to
    # decide: a shared add-commit means the two artefacts landed in ONE squash merge, and the
    # refusal's remedy is then a withdrawal rather than a re-ordering. Kept as a MAPPING (not a
    # set) so the collapse can be named by EPOCH below; `min()` over the shas is not that.
    prereg_add_commits = _add_commits_on(base, prereg)
    #: The registration's add-commit ON THE BASE — the commit a disclosure's `squash_merge` must be
    #: at or after. Named by the same `(epoch, sha)` order the collapse diagnosis below uses, so the
    #: two cannot disagree about which commit published the registration.
    prereg_sha = (min(prereg_add_commits, key=lambda s: (prereg_add_commits[s], s))
                  if prereg_add_commits else "")
    #: Set by the arms below: the logs this tree PROVES entered with the registration. Distinct from
    #: `atomic_added` on purpose — the out-param reports, this decides.
    atomic_seen: set[str] = set()
    #: Logs whose add-commit was actually read — the only ones whose atomic state is KNOWN.
    sha_known: set[str] = set()
    #: Logs with an add-commit that IS the registration's but whose OLDEST add predates it (a
    #: delete-and-re-add beside the registration). The disclosure's claim is TRUE for these, so the
    #: anti-rot message "but it is not one" would be FALSE (#10529 review cycle 8, P2).
    atomic_no_matter: set[str] = set()
    # ⛔ EVERY present disclosure is TYPE-CHECKED once, BEFORE the per-log arms (#10529 review cycle
    # 2, P2). The arms previously validated only the SCORED atomic path, so a disclosure on an
    # atomic-and-unscored log was never checked, and the field rules applied to whichever arm
    # happened to run first rather than to the disclosure itself.
    disclosure_failed: set[str] = set()
    for name in sorted(set(declared_atomic) & present):
        problem = _atomic_add_failure(declared_atomic.get(name), prereg_epoch, prereg_sha)
        if problem:
            disclosure_failed.add(name)
            failures.append(
                f"{MACHINE_RECORD}.{ATOMIC_ADD_KEY}[{name!r}] is not a usable disclosure. "
                f"{problem}"
            )
    # How many (registration, log) pairs actually got compared. ⛔ Tracked because `unmeasured == []`
    # is NOT the same fact as "an epoch pair was compared": a bundle with zero logs, or whose only
    # logs are untracked, walks the loop and compares NOTHING, and the caller would then print
    # `ordering measured against: <base>` over an empty comparison — the same silent-green
    # `ordering_base` exists to remove, one step outside every RETURN arm (#10463 review cycle 12).
    compared = 0
    for log in logs:
        base_log_epochs = _added_epochs_on(base, log)
        if base_log_epochs:
            # Already merged onto the base: measure the pair where BOTH are published. This is what
            # makes the verdict squash-invariant — a squash rewrites only what is not yet on base.
            log_epoch = min(base_log_epochs)
        else:
            # Added by this contribution: its add-commit is the one that lands in the squash.
            log_epochs = _added_epochs(log)
            if not log_epochs:
                if _is_tracked(log):
                    failures.append(
                        f"{log.name}: tracked but with no add-commit — ordering unprovable"
                    )
                continue  # untracked log: it cannot predate a commit that already exists
            log_epoch = min(log_epochs)
        compared += 1
        sha_known.add(log.name)
        # ⛔ ATOMIC IS IDENTICAL-ADD-COMMIT (#10529 review cycle 4, P1): the two artefacts land in
        # ONE commit, so no epoch comparison can order them. On the RULED path that is a refusal
        # and nothing else — the marker below only stops the anti-rot rule from reporting the
        # disclosure as "not an atomic add" when it demonstrably is.
        shared = set(prereg_add_commits) & set(_add_commits_on(base, log))
        if shared:
            atomic_no_matter.add(log.name)
        if log.name in exempt:
            if log_epoch > prereg_epoch:
                failures.append(
                    f"{log.name}: exempted as pre-existing but added at {log_epoch}, AFTER the "
                    f"pre-registration's appearance on {base} at {prereg_epoch} — the exemption is "
                    "unnecessary here and would hide a log that should have been ordered normally"
                )
                continue
            touches = _touch_epochs(log)
            if not touches:
                failures.append(
                    f"{log.name}: exempted but no commit touching it could be read — the "
                    "exemption needs its CONTENT to predate the registration, not just its add"
                )
                continue
            latest = max(touches)
            if latest >= prereg_epoch:
                failures.append(
                    f"{log.name}: exempted, but it was MODIFIED at {latest}, which is not "
                    f"strictly before the pre-registration's appearance on {base} at "
                    f"{prereg_epoch} — an in-place edit can put a measurement number into a "
                    "grandfathered log after the thresholds were written, and the add-commit "
                    "cannot see that"
                )
            continue
        if log_epoch <= prereg_epoch:
            ok, why = _log_declares_no_measurement(log)
            if ok:
                # A log that provably produced NO number cannot have been scored against
                # thresholds chosen after it was read. Re-asked of the content on every run, so
                # the moment it gains a number this gate reds it again (#9089).
                if exempted is not None:
                    exempted.append(log.name)
                continue
            failure = (
                f"{log.name}: added at {log_epoch} which is not strictly after the "
                f"pre-registration's appearance on {base} at {prereg_epoch} — the log may have "
                "been scored against thresholds chosen after it was read, and it does not prove "
                "it measured nothing "
                f"({why})"
            )
            if shared:
                # ⛔ THE ATOMIC SHAPE, AND IT IS A REFUSAL HERE (#10546 review cycle 17, IS-1).
                # `#10463` comment `6564634454` item 1 keeps it, verbatim: *"Do not relax the
                # guard. Keep 'same second is not before — an ordering that cannot be shown is not
                # an ordering.'"* The pair entered in ONE commit, so git cannot show which came
                # first — the disclosure below annotates that absence; it does not restore it, and
                # arming it is a reopen of that ruling, not a call this function may make.
                failure += (
                    f" The registration and this log entered in the SAME commit "
                    f"({min(shared, key=lambda s: (prereg_add_commits[s], s))[:9]}), so git cannot "
                    "show which came first, and an ordering that cannot be shown is not an ordering "
                    "(#10463 comment 6564634454 rules this stays a refusal)."
                )
                if log.name not in disclosure_failed:
                    if log.name not in declared_atomic:
                        if record_unreadable:
                            # ⛔ Do NOT fall through (#10529 review cycle 7, P1): the record itself
                            # could not be read, so there is nothing declared and nothing to index —
                            # `declared_atomic[log.name]` raised KeyError straight out of run()/
                            # main(). `check_machine_record` owns the diagnosis. It is REPORTED
                            # (cycle 8, P2): this half decided nothing, so `run_order_claim` must
                            # not read `verified`.
                            if unreadable_atomic is not None:
                                unreadable_atomic.append(log.name)
                            continue
                        # ⛔ AND THE RULED PATH MAY NOT INSTRUCT A REPAIR IT THEN REFUSES (#10546
                        # review cycle 23, ISSUE 2). It said "Disclose it under
                        # <record>.atomic_add_ordering or the ordering is unproven" — and an author
                        # who obeyed got, one paragraph down, "it waives nothing unless the caller
                        # arms the proposed arm", i.e. an INERT waiver committed into
                        # `preregistration.json`, which is the stale-waiver shape the anti-rot rule
                        # below exists to refuse. The disclosure route belongs to the arm that can
                        # honour it (`_check_ordering_proposed`, where the text is emitted); the
                        # only satisfiable repair HERE is `SQUASH_COLLAPSE_REMEDY`, appended below
                        # for every atomic pair. Same defect class the module already fixed twice:
                        # "the remedy text must match CAUSE" (cycle-3 finding 1c) and "it told the
                        # author to disclose when the real defect was that there is no readable
                        # record" (cycle-6 P2).
                        #
                        # ⛔ NOTHING IS APPENDED HERE ON PURPOSE — fall through to the refusal and
                        # its collapse remedy, so the one instruction printed is one that works.
                        pass
                    else:
                        # A complete, valid disclosure is still NOT a waiver while the arm is
                        # disarmed — and saying so here, rather than silently red-ing, is what
                        # stops an author from adding a record entry that cannot change the verdict.
                        failure += (
                            f" The disclosure under {MACHINE_RECORD}.{ATOMIC_ADD_KEY} is "
                            "well-formed, but it waives nothing unless the caller arms the proposed "
                            "arm (--allow-atomic-disclosure)."
                        )
            # ⛔ The remedy text must match CAUSE (review cycle 3, finding 1c). "Not strictly after"
            # alone sends an operator looking for an ordering to fix, and in the squash-collapsed
            # shape there is none to fix: the two artefacts share their add-commit on the base, and
            # that collapsed epoch is permanent for the path — a withdrawal plus a re-add still
            # measures the OLD add (cycle 7, finding F1). So the remedy is a new artefact at a new
            # path, never a re-ordering and never the forbidden re-add. That shape is precisely what
            # redded `main` at cfa3de857 (#10468).
            if shared:
                # ⛔ Name the collapse by EPOCH, not by sha: `sorted(shared)[0]` sorts HEX STRINGS,
                # so with more than one shared commit the sha printed is arbitrary and can name a
                # later commit than the collapse (review F2). The collapsed epoch is the fact the
                # refusal is about, and it is what the remedy tells the operator to look at.
                collapsed = min(shared, key=lambda s: (prereg_add_commits[s], s))
                failure += SQUASH_COLLAPSE_REMEDY.format(
                    base=base, sha=collapsed[:12]
                )
            failures.append(failure)
    # ── declarations are held to the same anti-rot rule as the exemption ────────────────────
    # A disclosure describing a NORMALLY ordered log is a waiver granted where none is needed — and
    # a waiver that is routine is the silent-pass shape this tool refuses. Read from `atomic_seen`,
    # the STATE, never from the caller's out-param (#10529 review P1).
    #
    # ⛔ THIS LOOP RUNS WHILE THE ARM IS DISARMED TOO (#10546 review cycle 18, IS-1). It used to be
    # gated on the arm, on the theory that disarming makes `atomic_seen` empty and the loop would
    # then emit the FALSE "discloses X as an atomic add, but it is not one". That concern is answered
    # INSIDE the loop by `atomic_no_matter`, which is populated for every atomic log before any arm
    # runs — so the gate protected against nothing, and what it DID do is disable the rule in every
    # invocation CI performs: a disclosure naming an ORDINARILY ordered log was accepted SILENTLY on
    # the default path, so the rule was only ever enforced on the one path nothing calls.
    for name in sorted(set(declared_atomic) & present):
        if (name in disclosure_failed or name in atomic_seen
                or name not in sha_known or name in atomic_no_matter):
            continue
        failures.append(
            f"{MACHINE_RECORD}.{ATOMIC_ADD_KEY} discloses {name!r} as an atomic add, but it is "
            "not one — a disclosure granted to an ORDINARILY ordered log would hide a real "
            "ordering failure, the same rule pins.pre_existing_unscored_logs applies"
        )
    if compared == 0 and unmeasured is not None:
        # Appended ONCE, for the bundle — never per log: a 3-log bundle whose fourth log is
        # untracked did measure, and a per-log append would report a real measurement as unmeasured.
        unmeasured.append(
            "no ordering pair was compared — this bundle has no evidence log the base and this "
            "history can both date"
        )
    return failures


def _check_ordering_proposed(prereg: Path, logs: list[Path], pins: dict | None = None,
                   exempted: list[str] | None = None,
                   atomic_added: list[str] | None = None,
                   atomic_disclosures: dict | None = None,
                   atomic_detail: dict | None = None,
                   record_unreadable: bool = False,
                   unreadable_atomic: list[str] | None = None,
                   allow_atomic_disclosure: bool = False,
                   unmeasured: list[str] | None = None) -> list[str]:
    """``[]`` when every log is either PROVABLY ordered after the registration, or provably
    unscored INSIDE the registration's own lineage.

    ⛔ THE SECOND HALF IS NARROWER THAN THE RULED PATH'S, AND THAT IS THE CONTRACT (#10546 review at
    `99d79dd4b`, FINDING 1). This docstring used to promise ``[]`` for *"every log either after the
    registration or provably unscored"* — the RULED path's sentence — while the UNORDERED refusal
    below (review cycle 26, ISSUE 1) runs BEFORE the content arm and is deliberately unconditional.
    A log whose add-commit is off the registration's lineage was therefore REFUSED with nothing
    exempted, and the ``#9089`` exemption this function advertises was unreachable for it: the
    promise and the code disagreed, in the direction that reads as a waiver the arm will not grant.

    Measured, both verdicts on ONE tree (side add 1700000100 → registration 1700000200 → ``--no-ff``
    merge, the log a real machine record with every numerator/denominator null and
    ``run.status=REFUSED``): ARMED refuses it by cause and appends nothing to ``exempted``; RULED
    exempts it by content. ``test_the_armed_rule_refuses_an_unscored_log_it_cannot_order`` pins both.

    ⛔ AND THE NARROWING IS NOT A GAP TO TIDY LATER — the exemption CANNOT be widened to cover the
    off-lineage case. Its own topology bound needs a snapshot of the log AT the registration
    (``_blob_at(prereg_sha, …)``, the value ``_late_touches`` compares against) and an off-lineage
    log has none: ``_blob_at`` is empty there, so EVERY touch commit — its entry included — reads as
    "late", and an ungated "measured nothing" read would be the strip-the-number-after-green hole
    that bound exists to close. The ruled path may grant it because it compares the pair on the BASE,
    where the order is published; this path compares this contribution's own history, where an
    entry git cannot order carries no such attribution. External corroboration: in OSF
    registrations a later re-addition or edit of a file does not by itself prove the file existed
    before the registration (help.osf.io/article/626) — provenance binds to the entry event, which
    is exactly what an unordered add-commit fails to prove.

    ``atomic_disclosures`` is the machine record's ``atomic_add_ordering`` block — the record, NOT
    the pins block (see ``ATOMIC_ADD_KEY`` for why the distinction is load-bearing).

    ``unmeasured``, when given, is APPENDED with the reason this rule compared NO ordering pair —
    the same out-param, with the same meaning, that ``_check_ordering_ruled`` writes (#10546 review
    at `01d9ce879`, ISSUE 1). It is not a verdict and it may not be read back as one: ``run()``
    consults it only to refuse to print `verified` over an empty comparison, which is exactly what
    this path used to do — a bundle whose only log was withdrawn (or was never committed) walked
    zero pairs and returned ``[]``, and the armed arm's final ``else`` dressed that as `verified` on
    the field a machine consumer reads by name.

    ⛔ ``allow_atomic_disclosure`` IS OFF BY DEFAULT, AND THAT IS THE WHOLE POINT (#10546 review
    cycle 17, IS-1/IS-2). The disclosure arm answers "the pre-registration and its log entered in
    ONE commit" with a PASS gated by a record the CHECKED PARTY authors. That is the opposite of
    what `#10463` comment `6564634454` rules, verbatim item 1: *"Do not relax the guard. Keep 'same
    second is not before — an ordering that cannot be shown is not an ordering.'"*, and item 4
    fixes WHAT must be compared (base ancestry, not commits inside the pull request) — carrying the
    marker *"OVERRIDES: deriving the ordering from commits inside the pull request, rather than
    from the base branch"*. The reopen that ruling requires (requested at `6566767704`) has NOT
    been granted, so the mechanism is this PR's PROPOSAL, not its result.

    The flag is how the tree keeps obeying the decision it currently obeys while the proposal stays
    deliverable and tested: with the flag absent — every caller in CI, every default invocation —
    an atomic pair is the ruled REFUSAL, and a disclosure in the record is not a waiver at all (it
    is reported as inert rather than silently ignored, so no author can believe they disclosed
    something). Nothing in CI passes it. If the owner grants the reopen, arming it is a one-line
    default change plus a comment pointing at the ruling that granted it.

    ``exempted``, when given, is APPENDED with the name of every log the content arm exempted
    (#9089), and ``atomic_added`` with every log whose ordering was disclosed as an ATOMIC ADD
    (#10529). Neither is decoration: an ordering that was WAIVED must be visible, or the green line
    reads exactly like a log that was ordered — the silent-green shape this tool exists to refuse
    (see the ``--no-git``/``--no-corpus`` rule ninety lines below).

    Raises ``HistoryUnavailable`` (a ``CheckError``) when this checkout is shallow: with a grafted
    tip the comparison cannot be evaluated at all, and a wrong verdict is worse than no verdict.
    """
    failures: list[str] = []
    exempt = _pins_mapping(pins or {}, "pre_existing_unscored_logs")
    present = {p.name for p in logs}
    for name in sorted(set(exempt) - present):
        failures.append(
            f"pins.pre_existing_unscored_logs exempts {name!r}, which is not in this directory — "
            "an exemption that exempts nothing is how an exempt set is grown one stale row at a time"
        )

    # The laundering whitelist needs NO git: it reads the log's own lines. Run it BEFORE the history
    # guard, because CI test jobs are always shallow and the guard would otherwise disable the only
    # defense against a scored number for every CI run (#9075 review r6 P2).
    for log in logs:
        if log.name in exempt:
            ok, why = _log_declares_unscored(log)
            if not ok:
                failures.append(f"{log.name}: exempted, but the log {why}")

    # `record_unreadable` means the RECORD itself could not be read: `check_machine_record` reports
    # that, and this half must not also demand a disclosure into a file that cannot be read. It is a
    # separate flag rather than "disclosures is None", so the plain three-argument form every test
    # and any future caller uses keeps its meaning (#10529 review cycle 6, P2).
    declared_atomic = dict(atomic_disclosures or {})
    #: The disclosures this run treats as a GROUND for an ordering verdict. The block is still
    #: VALIDATED when the arm is disarmed — a stale or malformed disclosure is a finding either way,
    #: and its diagnosis is the only thing that tells an author what to fix — but it can never turn
    #: an atomic pair into a pass (#10546 review cycle 17, IS-1).
    atomic = declared_atomic
    #: Set by the arms below: the logs this tree PROVES entered with the registration. Distinct from
    #: the ``atomic_added`` out-param on purpose — the out-param reports, this decides.
    atomic_seen: set[str] = set()

    # ⛔ The stale-disclosure rule runs BEFORE the history guards' early returns (#10529 review P2):
    # a disclosure naming an absent log was invisible whenever the registration was uncommitted or
    # carried no add-commit, so a stale waiver could sit undetected in exactly the states where the
    # rest of the ordering half is not looking. It needs no history — it reads names.
    for name in sorted(set(atomic) - present):
        failures.append(
            f"{MACHINE_RECORD}.{ATOMIC_ADD_KEY} discloses {name!r}, which is not in this directory "
            "— an atomic-add disclosure that names no log is a stale waiver"
        )
    if history_unavailable():
        raise HistoryUnavailable(HISTORY_UNAVAILABLE, failures)

    prereg_adds = _added_commits(prereg)
    if not prereg_adds:
        if not logs:
            # ⛔ THE RETURN IS ABOVE `compared = 0`, SO THE BUNDLE-LEVEL GUARD AT THE END OF THIS
            # FUNCTION CANNOT REACH IT (#10546 review at `43b6a3952`, ISSUE 1 — BLOCKING). Nothing
            # was ordered here, and returning SILENTLY made `run()`'s armed `else` report
            # `verified` / `run_order_claim: verified` / `ordering_base: PROPOSED_ORDERING_BASE`
            # over ZERO comparisons — the "dressed `decided nothing` as `verified`" shape this PR
            # exists to end, surviving on one return. The ruled path answers this exact tree with
            # an `unmeasured` note (see its `no registration on the base and no evidence log to
            # order` arm), so the two rules agreed about the base and disagreed about whether a
            # measurement happened. They must not: `unmeasured` is a fact about the BUNDLE, not
            # about the rule in force.
            if unmeasured is not None:
                unmeasured.append(
                    f"{PREREG_NAME} has no add-commit in this history and there is no evidence "
                    "log to order"
                )
            return failures  # not committed yet, and nothing to be ordered against
        if not _is_tracked(prereg):
            failures.append(
                f"{PREREG_NAME} is not committed, but {len(logs)} evidence log(s) exist — "
                "the ordering cannot be proved, and an unprovable ordering is not an ordering"
            )
            return failures
        failures.append(
            f"{PREREG_NAME} has no add-commit (git log --diff-filter=A found nothing) — "
            "cannot establish that it precedes the evidence logs"
        )
        return failures
    prereg_epoch, prereg_sha = prereg_adds[0]
    #: Logs whose add-commit was actually read — the only ones whose atomic state is KNOWN.
    sha_known: set[str] = set()
    #: Logs with an add-commit that IS the registration's but whose OLDEST add predates it (a
    #: delete-and-re-add beside the registration). The disclosure's claim is TRUE for these, so the
    #: anti-rot message "but it is not one" would be false — the predating-add failure already
    #: reports the real defect (#10529 review cycle 8, P2).
    atomic_no_matter: set[str] = set()
    #: ⛔ How many logs this rule actually ORDERED (#10546 review at `01d9ce879`, ISSUE 1). Its
    #: sibling has had this counter since `#10463` review cycle 12, and this path did not — so a
    #: bundle whose only log was withdrawn, or every log of which is untracked, walked the loop and
    #: compared NOTHING, returned ``[]``, and `run()`'s armed `else` dressed that as `verified` on
    #: the one field a machine consumer reads by name. Incremented where the pair is ESTABLISHED
    #: (its add-commits were read), exactly as the ruled path increments where an epoch is
    #: established — never derived from `len(logs)`, which cannot see the `continue` for an
    #: untracked log.
    compared = 0
    # ⛔ EVERY present disclosure is TYPE-CHECKED once, here, before the per-log arms (#10529 review
    # cycle 2, P2). The arms previously validated only the SCORED atomic path, so a disclosure on an
    # atomic-and-unscored log was never checked (`reason: {a: 1}` was accepted), and the field rules
    # applied to whichever arm happened to run first rather than to the disclosure itself.
    disclosure_failed: set[str] = set()
    for name in sorted(set(atomic) & present):
        problem = _atomic_add_failure(atomic.get(name), prereg_epoch, prereg_sha)
        if problem:
            disclosure_failed.add(name)
            failures.append(
                f"{MACHINE_RECORD}.{ATOMIC_ADD_KEY}[{name!r}] is not a usable disclosure. "
                f"{problem}"
            )
    for log in logs:
        log_adds = _added_commits(log)
        if not log_adds:
            if _is_tracked(log):
                failures.append(
                    f"{log.name}: tracked but with no add-commit — ordering unprovable"
                )
            continue  # untracked log: it cannot predate a commit that already exists
        # ⛔ `%ct` MAY NOT CHOOSE WHICH ADD-COMMIT IS THE PAIR (#10529 review cycle 10, P1).
        # `_added_commits` is sorted by epoch, so `log_adds[0]` was decided by a field
        # (`GIT_COMMITTER_DATE`) this module has already ruled attacker-controlled: a delete-and-
        # re-add backdated to look NEWER became the representative, made the pair look ATOMIC, and a
        # disclosure waived a tree whose other add-commit is a strict ANCESTOR of the registration —
        # the condition the docstring calls a HARD FAIL that no disclosure can waive.
        #
        # The verdict is therefore taken over EVERY add-commit, FAIL-CLOSED:
        #   * the log is ATOMIC only if an add-commit IS the registration's;
        #   * it PREDATES the registration if ANY add-commit is a strict ancestor of it.
        add_shas = {sha for _, sha in log_adds}
        log_epoch = min(epoch for epoch, _ in log_adds)
        atomic_pair = prereg_sha in add_shas
        predating_sha = next(
            (sha for sha in sorted(add_shas) if sha != prereg_sha and _is_ancestor(sha, prereg_sha)),
            "",
        )
        # ⛔ For MESSAGES only — EVERY arm must decide over all of `add_shas` (below). The epoch
        # chose a representative once and it was a P1 (#10529 review cycle 10); hex sort order is a
        # representative too, and just as unrelated to the ordering being tested (`sorted(add_shas)`
        # is the order of the SHA TEXT). git defines no ordering among several candidate commits
        # (`git-merge-base`: "When the --all option is not given, it is unspecified which best one is
        # output"), so determinism cannot be inherited from it — the rule has to hold over every
        # candidate, which is what the proven-order arm below now does (#10546 review cycle 25).
        log_sha = predating_sha or (prereg_sha if atomic_pair else sorted(add_shas)[0])
        compared += 1
        sha_known.add(log.name)
        if atomic_pair:
            atomic_no_matter.add(log.name)
        if log.name in exempt:
            if log_epoch > prereg_epoch:
                failures.append(
                    f"{log.name}: exempted as pre-existing but added at {log_epoch}, AFTER the "
                    f"pre-registration's add-commit {prereg_epoch} — the exemption is unnecessary "
                    "here and would hide a log that should have been ordered normally"
                )
                continue
            touches = _touch_epochs(log)
            if not touches:
                failures.append(
                    f"{log.name}: exempted but no commit touching it could be read — the "
                    "exemption needs its CONTENT to predate the registration, not just its add"
                )
                continue
            # ⛔ ANCESTRY, NOT THE EPOCH (#10529 review cycle 9, P2). `latest >= prereg_epoch` was
            # BOTH bypassable (a backdated edit) AND permanently unsatisfiable for an exempt log
            # whose add-commit IS the registration's — the squash shape this PR exists for, with no
            # exit short of rewriting history. Whether a touching commit is BEHIND the registration
            # is topology.
            late = _late_touches(log, prereg_sha)
            if late:
                failures.append(
                    f"{log.name}: exempted, but commit {late[0][:9]} changed it and is not an "
                    f"ancestor of the pre-registration's add-commit {prereg_sha[:9]} — an "
                    "in-place edit can put a measurement number into a grandfathered log after the "
                    "thresholds were written, and the add-commit cannot see that"
                )
                # ⛔ And then LEAVE (#10529 review cycle 9, P2): every other failure path in this
                # function continues, and this one did not, so a waiver the arm had just DENIED was
                # still reported as a granted declaration.
                continue
            # ⛔ Record the atomic state before this `continue` (#10529 review cycle 4, P2): an
            # exempt log that IS atomic and carries a disclosure reached the anti-rot loop and was
            # told "but it is not one" — the cycle-3 defect class resurfacing through this arm.
            if atomic_pair:
                # ⛔ `atomic_seen` ONLY (#10529 review cycle 11, P2). Reporting the declaration here
                # named a ground that carried nothing: deleting the disclosure from the identical
                # tree yields the same verdict, because this pass is the PINS EXEMPTION's. Cycle-7
                # ruled exactly this for the content arm; cycle-4's purpose for the block is served
                # by `atomic_seen`, which is what the anti-rot loop reads.
                atomic_seen.add(log.name)
            continue
        # ⛔ ATOMIC IS IDENTICAL-SHA, NOT EQUAL-EPOCH (#10529 review cycle 4, P1) — computed above,
        # over EVERY add-commit, so `%ct` cannot choose the representative (#10529 cycle 10, P1).

        # ⛔ ANCESTRY IS CONSULTED FOR EVERY DISTINCT PAIR, NOT ONLY FOR `epoch <=` (#10529 review
        # cycle 5, P1). `%ct` is attacker-controlled (`GIT_COMMITTER_DATE`), so gating the reverse
        # test behind the epoch let a log whose add-commit is a TRUE ANCESTOR of the registration's
        # pass green whenever its committer date was set to look LATER — the exact route this guard
        # exists to close, with no disclosure and no failure. The content arm is asked first, so an
        # unscored log is still exempt (#9089).
        # ⛔ ONE topology bound for EVERY arm that reads the log's CONTENT (#10529 review cycle 11,
        # P1). The predating arm was the last one without it, so a later commit that STRIPPED the
        # numbers out of a predating log flipped the guard's central hard fail into green ("exempt by
        # CONTENT, measured nothing") — the content read was no longer the content that entered with
        # the tree. Computed once here; the arms that claim content entered at/before the
        # registration consult it. The proven-order pass does NOT: it requires EVERY add-commit to
        # be a DESCENDANT of the registration (#10546 review cycle 26, ISSUE 1 — one descendant is
        # not the proof), so the registration precedes every entry point of that log and a later
        # touch is still after the thresholds; the pass is exactly where a log with more than one
        # add-commit made that "beside the point" claim FALSE, and an add-commit git cannot order at
        # all is now REFUSED below instead of being dropped by it.
        late = _late_touches(log, prereg_sha)
        if predating_sha:
            if late:
                failures.append(
                    f"{log.name}: commit {late[0][:9]} changed it and is not an ancestor of "
                    f"{PREREG_NAME}'s add-commit {prereg_sha[:9]} — so the content this check would "
                    "read is not the content that entered with the tree, and an in-place edit can "
                    "strip a number after the thresholds were written"
                )
                continue
            ok, why = _log_declares_no_measurement(log)
            if not ok:
                # ⛔ The ground is ANCESTRY, not the epoch (#10529 review cycle 6, P2): the committer
                # date is the very thing this arm was moved ahead of the epoch comparison to defeat,
                # so quoting it here printed "added at 1800000000 which is not strictly after …
                # 1700000500" — a sentence that is simply false. The recorded date is shown as what
                # it is, a recorded date, and the proof is the topology.
                failures.append(
                    f"{log.name}: its add-commit {predating_sha[:9]} is an ANCESTOR of the "
                    f"pre-registration's add-commit {prereg_sha[:9]} — the log entered the tree "
                    f"before the thresholds (recorded dates {log_epoch} vs {prereg_epoch}, which "
                    f"git does not order), so it may have been scored against thresholds chosen "
                    f"after it was read, and it does not prove it measured nothing ({why})"
                )
            elif exempted is not None:
                # A waived ordering is REPORTED on every path that can waive it (#9089).
                exempted.append(log.name)
            continue
        # ⛔ PROVEN ORDER IS NOT A WAIVER (#10529 review cycle 6, P2). The content arm used to run
        # first, so a pair git proves ordered was still appended to `exempted` and the claim read
        # "(exempt by CONTENT, measured nothing: X)" — a waiver reported where none was granted, the
        # mirror of the misdescription cycle 3 fixed in the verdict line, and it teaches readers that
        # waivers are routine. A proven order returns here, before any arm that records a waiver.
        # ⛔ OVER EVERY ADD-COMMIT, not `log_sha` (#10546 review cycle 25). `log_sha` falls back to
        # `sorted(add_shas)[0]`, i.e. the representative was chosen by hex text: on a log with more
        # than one add-commit (a delete and re-add) whose re-add is a descendant of the registration,
        # whether the pair was PROVEN ORDERED or REFUSED depended on which sha sorted first — two
        # topologically identical trees, two verdicts, and the refused one was a false red on a pair
        # git does prove. The predating arm above has already taken every add-commit that is an
        # ANCESTOR of the registration, so by here a descendant add-commit is the whole proof.
        # ⛔ AND THE PROOF IS OVER EVERY ENTRY POINT, NOT ONE OF THEM (#10546 review cycle 26,
        # ISSUE 1). `any(...)` closed the hex-representative defect and opened a fail-open in the
        # direction this pass exists to guard: the arm `continue`d as soon as ONE add-commit was a
        # descendant, so an add-commit git cannot order at all — a log committed on a lineage that
        # diverged BEFORE the registration and merged in later — was silently dropped. Measured on
        # the reviewer's fixture (side add 1700000100 → registration 1700000200 → merge → `git rm`
        # → re-add 1700000500), the tree was REFUSED without the re-add and `[]` with it: the re-add
        # was the ONLY difference, i.e. exactly Route B — the fabrication `ATOMIC_ADD_KEY` says this
        # guard prevents. The cycle-25 justification for not consulting `late` ("the registration
        # predates the whole log, so later touches are beside the point") is true per ENTRY POINT
        # and false for the log as a whole, which is the case a delete-and-re-add creates by
        # construction. What this arm may pass on is therefore every add-commit being a descendant.
        if not atomic_pair and add_shas and all(_is_ancestor(prereg_sha, sha) for sha in add_shas):
            continue
        # ⛔ AN ADD-COMMIT GIT CANNOT ORDER IS A REFUSAL, NOT A HOLE (#10546 review cycle 26,
        # ISSUE 1). The predating arm above has taken every strict ANCESTOR of the registration, so
        # an add-commit that is still not a descendant here is UNORDERED with it: a lineage that
        # diverged before the registration and was merged in later. Ordering that entry is not
        # something a LATER re-add can supply — the oldest entry keeps its own unorderable relation
        # to the registration — and this arm is the one place a re-add laundered it into a PASS.
        # Refusing it BY CAUSE also closes the date-shaped hole the fall-through left open: the epoch
        # comparison below is `%ct`, which this module has ruled attacker-controlled everywhere, so
        # an unordered add dated after the registration fell through with NOTHING printed.
        unordered = [sha for sha in sorted(add_shas) if not _is_ancestor(prereg_sha, sha)]
        if unordered:
            named = ", ".join(sha[:9] for sha in unordered)
            # ⛔ AND NO CONTENT EXEMPTION HERE (#10546 review cycle 26, ISSUE 1). The predating arm
            # above grants one, gated on `late`: a log that is in the registration's OWN lineage has
            # a snapshot AT the registration (`_blob_at(prereg_sha, …)`), so "unchanged since entry"
            # is checkable and an unscored read is the content that entered. A log outside that
            # lineage has no such snapshot — its every touch commit is outside the registration's
            # ancestry, `late` cannot separate the entry from a later edit, and an ungated "measured
            # nothing" read would be the strip-the-number-after-green hole the bound exists to
            # close. Unproven content cannot carry an exemption, so this refusal is unconditional.
            failures.append(
                f"{log.name}: its add-commit {named} is UNORDERED with {PREREG_NAME}'s add-commit "
                f"{prereg_sha[:9]} — neither is an ancestor of the other — so the log entered the "
                "tree off a lineage that diverged before the thresholds were written, git cannot "
                "show it came after them, and a LATER re-add cannot supply that proof; an "
                "unprovable ordering is not an ordering"
            )
            continue
        # A log that provably produced NO number cannot have been scored against thresholds
        # chosen after it was read. Re-asked of the content on every run, so the moment it gains a
        # number this gate reds it again (#9089). This arm is asked FIRST in both shapes: an
        # unscored log needs no disclosure to be ordered, whichever commit carried it.
        if log_epoch <= prereg_epoch:
            # ⛔ THE TOPOLOGY BOUND COMES BEFORE THE CONTENT PASS (#10529 review cycle 10, P2). The
            # content arm `continue`d before the bound was consulted, so an atomic log whose record
            # was STRIPPED of its numbers in a LATER commit — leaving `run.status` not COMPLETED —
            # was certified "measured nothing" and the disclosure requirement erased. A post-green
            # edit flipping the verdict is the cycle-8 silent-green class, one arm earlier. `late` is
            # the single hoisted bound above.
            if late:
                failures.append(
                    f"{log.name}: commit {late[0][:9]} changed it and is not an ancestor of "
                    f"{PREREG_NAME}'s add-commit {prereg_sha[:9]} — so the content this check is "
                    "about to read is not the content that entered with the registration, and an "
                    "in-place edit can strip a number after the thresholds were written"
                )
                continue
            ok, why = _log_declares_no_measurement(log)
            if ok:
                # ⛔ The atomic state is recorded HERE too (#10529 review P1): a log that is both
                # atomic AND provably unscored is honestly disclosable, and a disclosure was being
                # punished as "not an atomic add" because this `continue` skipped the bookkeeping.
                #
                # ⛔ BUT IT IS NOT REPORTED AS A DISCLOSURE (#10529 review cycle 7, P2). This arm
                # decided the pass on the log's CONTENT — deleting the disclosure from the same tree
                # yields the identical verdict — so claiming `the ordering HOLDS BY DISCLOSURE` here
                # would name a ground that carried nothing, and the printed block would be followed
                # by zero commit lines because `atomic_detail` was never filled. The waiver IS still
                # visible: `exempted` carries it into `ordering_exempt` and the claim line prints
                # "(exempt by CONTENT, measured nothing: <log>)". `atomic_seen` is set so the
                # anti-rot rule still sees a genuinely atomic log.
                if atomic_pair:
                    atomic_seen.add(log.name)
                if exempted is not None:
                    exempted.append(log.name)
                continue
        if atomic_pair:
            # ATOMIC ADD (#10529): the pair entered the repository in ONE commit — a squash merge of
            # an ordered branch, or a single commit written after the numbers were read. The git
            # object is identical, so the comparison answers nothing.
            #
            # ⛔ AND THAT IS WHERE IT STOPS UNLESS THE ARM IS ARMED (#10546 review cycle 17,
            # IS-1/IS-2). Disarmed — the default, and every caller in CI — the pair is the REFUSAL
            # the recorded ruling keeps: "same second is not before — an ordering that cannot be
            # shown is not an ordering". The disclosure below is then inert by construction; the
            # record is still VALIDATED (its diagnosis is what tells an author what is wrong) but it
            # is never a ground for a pass.
            # ⛔ `why` IS ASKED HERE, NOT INHERITED FROM ANOTHER ARM (#10546 review, P3). It used
            # to be read out of the `log_epoch <= prereg_epoch` arm above, which is sound only
            # because `atomic_pair` proves one of the log's add-commits IS the registration's, so
            # `min(log_epochs) <= prereg_epoch` and that arm always ran first. That is a proof no
            # line states, and a verdict resting on it is one refactor away from an
            # UnboundLocalError on the arm that carries the recorded ruling — this module has
            # already paid for exactly that class. The predicate is a pure read of the log, so
            # re-asking it costs one read on one path and makes the binding local and visible;
            # carrying a variable bound under a DIFFERENT arm's guard is a coupling a reader cannot
            # see, and initialising it to "" would print "()" as if it were an answer.
            _atomic_ok, why = _log_declares_no_measurement(log)
            assert not _atomic_ok, (
                "the content arm above must have exempted this log — if it did not, this "
                "disclosure requirement is being reported for a log that measured nothing"
            )
            refusal = (
                f"{log.name}: added at {log_epoch} which is not strictly after the "
                f"pre-registration's add-commit {prereg_epoch} — the registration and this log "
                f"entered in the SAME commit ({log_sha[:9]}), so git cannot show which came first, "
                f"and an ordering that cannot be shown is not an ordering (#10463 comment "
                f"6564634454 rules this stays a refusal). It does not prove it measured nothing "
                f"({why})"
            )
            if log.name in disclosure_failed:
                continue  # the specific field failure is already reported above
            if log.name not in atomic:
                if record_unreadable:
                    # ⛔ DO NOT FALL THROUGH (#10529 review cycle 7, P1; wording corrected in
                    # cycle 14, P3). The `continue` skips the rest of THIS LOG's arms — it does not
                    # return from the function (the earlier comment said "RETURN", which a future
                    # reader would act on by converting it). The record itself could not be read, so
                    # there is nothing declared and nothing to index — `atomic[log.name]` below
                    # raised KeyError straight out of run()/main(), printing a traceback and NO
                    # findings, on exactly the state this flag exists for.
                    # `check_machine_record` owns the diagnosis.
                    # It is REPORTED (#10529 review cycle 8, P2): the ordering half decided nothing
                    # here, so `run_order_claim` must not read `verified`.
                    if unreadable_atomic is not None:
                        unreadable_atomic.append(log.name)
                    continue
                if not allow_atomic_disclosure:
                    failures.append(refusal)
                    continue
                failures.append(
                    f"{log.name}: added at {log_epoch} by the SAME commit that added "
                    f"{PREREG_NAME} ({log_sha[:9]}) — the ordering is atomic, so git cannot show "
                    f"which came first (a squash merge of an ordered branch and one commit "
                    f"written after the numbers look identical). Disclose it under "
                    f"{MACHINE_RECORD}.{ATOMIC_ADD_KEY} or the ordering is unproven."
                )
                continue
            if not allow_atomic_disclosure:
                # A complete, valid disclosure is still not a waiver while the arm is disarmed — and
                # saying so HERE, rather than silently red-ing, is what stops an author from adding
                # a record entry that cannot possibly change the verdict.
                failures.append(
                    refusal + f". The disclosure under {MACHINE_RECORD}.{ATOMIC_ADD_KEY} is "
                    "well-formed, but it waives nothing unless the caller arms the proposed arm "
                    "(--allow-atomic-disclosure)"
                )
                continue
            # `log.name` is provably in `atomic` from here on.
            # ⛔ THE ADD-COMMIT IS IMMUTABLE, WHICH IS WHY IT CANNOT SEE AN EDIT (#10529 review
            # cycle 8, P1). The pins-exempt arm has always bound this (`_touch_epochs`), and the
            # atomic arm did not: a log re-committed with a rewritten number AFTER the bars passed
            # green with `verified (atomic-add DECLARED: …)`, invisible to every other check —
            # `document_sha256` covers only the registration, nothing pins the log's bytes. That is
            # a NEW silent-green path, which is the one thing this change may never add. The
            # disclosure can only cover the content that entered WITH the registration.
            # ⛔ TOPOLOGY, NOT THE DATE (#10529 review cycle 9, P1). The first version of this bound
            # compared `max(_touch_epochs(log))` with `prereg_epoch` — a `%ct` comparison in a
            # module whose own cycle-5 finding says `%ct` is attacker-controlled. The rewrite the
            # forward-dated test killed passed green when BACKDATED. Whether a touching commit is
            # behind the registration is a fact about the graph.
            if late:
                failures.append(
                    f"{log.name}: the pair is atomic, but commit {late[0][:9]} changed it and is "
                    f"not an ancestor of {PREREG_NAME}'s add-commit {prereg_sha[:9]} — the "
                    "disclosure can only cover the content that entered with the registration, and "
                    "an in-place edit can move a number after the thresholds were written"
                )
                continue
            # ⛔ The STATE is derived HERE, never read back from the caller's out-param (#10529
            # review P1). Deciding the anti-rot rule from `atomic_added` made the verdict depend on
            # whether a caller happened to pass a list: the same tree and pins gave `[]` via
            # `run()` and "disclosed ... but it is not one" via the three-argument form every test
            # and any future caller uses. An out-param reports; it may not judge.
            atomic_seen.add(log.name)
            if atomic_added is not None:
                atomic_added.append(log.name)
            if atomic_detail is not None:
                atomic_detail[log.name] = _atomic_detail_entry(atomic[log.name])
            continue
        # ⛔ CONTROL NEVER ARRIVES HERE, AND THE ARMS ABOVE ARE WHAT PROVE IT (#10546 review at
        # `43b6a3952`, ISSUE 2). The arm this replaces was a CONDITIONAL
        # `if log_epoch <= prereg_epoch:` whose comment claimed to be *"THE ATTACK, and the
        # conservative catch-all … `main`'s verdict, kept"* — code no tree can execute, dressed as
        # the arm that decides that shape, which is cycle 23's ISSUE 1 all over again (a claim
        # resting on a container no reachable path can enter). The predecessor arms are an
        # exhaustive partition, over git's own two-commit relation (`git-merge-base`:
        # `--is-ancestor` is parent-graph REACHABILITY and exits 1 for "not an ancestor"; there is
        # no third relation):
        #   * a strict ANCESTOR add-commit ⇒ the `predating_sha` arm refused it BY TOPOLOGY, before
        #     `%ct` could matter — `test_a_future_dated_log_that_is_a_true_ancestor_still_hard_fails`
        #     is "THE ATTACK" shape (log older, date set LATER), and it is refused THERE;
        #   * an add-commit that is neither ancestor nor descendant ⇒ the `unordered` arm refused it;
        #   * every add-commit a descendant, and not atomic ⇒ the proven-order pass `continue`d;
        #   * `atomic_pair` ⇒ the atomic arm above always `continue`d;
        #   * an exempt log ⇒ the pins-exemption arm always `continue`d.
        # It is KEPT, as the fail-closed DEFAULT rather than deleted, because the arm it replaces
        # failed OPEN on one side: control arriving with `log_epoch > prereg_epoch` was accepted by
        # falling out of the loop, and on a `%ct` comparison this module rules attacker-controlled
        # everywhere else. A default that REFUSES cannot be reached by any tree this rule accepts,
        # so its reachability is not a measurement about the log — REACHING IT MEANS A PREDECESSOR
        # ARM WAS LOOSENED, i.e. a bug in this rule, and that is exactly what it says. The reach is
        # observable rather than hypothetical: delete the `unordered` arm and
        # `test_an_unordered_add_dated_after_the_registration_is_still_refused` reds on THIS text
        # instead of the UNORDERED cause — so the net is pinned by the mutations it must catch.
        # `why` is deliberately not named: it is bound only under the content arm's guard, and a
        # fall-through is the one state its binding cannot be proven from.
        failures.append(
            f"{log.name}: no ordering arm could decide this log (its add-commit "
            f"{log_sha[:9]}, recorded dates {log_epoch} vs the pre-registration's {prereg_epoch}) "
            "— the arms above are meant to be exhaustive, so this is a defect in the ordering rule "
            "itself and NOT a verdict about the log; an undecided ordering is not an ordering"
        )
        continue
    # Declarations are held to the same anti-rot rule as the exemption: one describing a NORMALLY
    # ordered log is a waiver granted where none is needed — and a waiver that is routine is the
    # silent-pass shape this tool refuses. Read from ``atomic_seen``, the STATE, never from the
    # caller's out-param (#10529 review P1).
    #
    # ⛔ THIS LOOP RUNS WHILE THE ARM IS DISARMED TOO (#10546 review cycle 18, IS-1). It used to be
    # gated on `allow_atomic_disclosure`, on the theory that disarming makes `atomic_seen` empty for
    # an atomic log and the loop would then emit the FALSE "discloses X as an atomic add, but it is
    # not one". That concern is answered INSIDE the loop by `atomic_no_matter`, which is populated
    # (search `atomic_no_matter.add` — a LINE NUMBER here went stale the moment this file grew, which
    # is the same decay class as a rotted citation in a doc; cite the reader, never the address)
    # for EVERY atomic log before any arm runs — so the gate protected against nothing it
    # was not already guarded against. What it DID do is disable the rule in every invocation CI
    # performs: a disclosure naming an ORDINARILY ordered log was accepted SILENTLY on the default
    # path, so the anti-rot rule was only ever enforced on the one path nothing calls, and a tree
    # green today would red the day the arm is armed. A suppression that suppresses nothing is
    # reported whether or not the rule it targets is currently armed — the same separation Ruff
    # makes between a rule and `RUF100` (unused noqa), and the reason `pins.pre_existing_unscored_logs`
    # refuses a pre-granted exemption on its own sister rule.
    for name in sorted(set(atomic) & present):
        # A disclosure that is genuinely atomic but malformed was already told so — and telling it
        # "but it is not one" would be FALSE, since it is (#10529 review cycle 3, P2). The same
        # applies to a log whose add-commit could not be read AT ALL: its state is UNKNOWN, and a
        # claim about it would be a guess (#10529 review cycle 6, P2).
        if (name in disclosure_failed or name in atomic_seen
                or name not in sha_known or name in atomic_no_matter):
            continue
        failures.append(
            f"{MACHINE_RECORD}.{ATOMIC_ADD_KEY} discloses {name!r} as an atomic add, but it is "
            "not one — a disclosure granted to an ORDINARILY ordered log would hide a real "
            "ordering failure, the same rule pins.pre_existing_unscored_logs applies"
        )
    if compared == 0 and unmeasured is not None:
        # Appended ONCE, for the bundle — never per log (#10463 review cycle 12): a 3-log bundle
        # whose fourth log is untracked DID order a pair, and a per-log append would report a real
        # measurement as unmeasured, the same lie one polarity over.
        unmeasured.append(
            "no ordering pair was compared — this bundle has no evidence log carrying an "
            "add-commit the registration can be ordered against"
        )
    return failures


def check_machine_record(directory: Path, pins: dict) -> list[str]:
    """The JSON record must parse, declare its schema, and agree with the pins block."""
    failures: list[str] = []
    path = directory / MACHINE_RECORD
    if not path.is_file():
        return [f"no {MACHINE_RECORD} beside {PREREG_NAME} — the evidence probe reads the "
                "machine record, so a document alone is not a registration it can consume"]
    try:
        raw = path.read_text()
    except UnicodeDecodeError as exc:
        return [f"{MACHINE_RECORD} is not valid UTF-8: {exc}"]
    try:
        record = json.loads(raw)
    except (json.JSONDecodeError, RecursionError) as exc:
        return [f"{MACHINE_RECORD} is not JSON: {exc}"]
    if not isinstance(record, dict):
        # `record.get("schema")` on a list/str/int raised AttributeError straight out of main() with
        # NO verdict and no exit code (#10529 review cycle 8, P2). A check that cannot read its
        # subject is exit 2, never a traceback.
        raise CheckError(
            f"{MACHINE_RECORD} parses to a {type(record).__name__}, expected a JSON object — it is "
            "unparseable for this check"
        )
    if record.get("schema") != PREREG_SCHEMA:
        failures.append(
            f"{MACHINE_RECORD} declares schema {record.get('schema')!r}, expected {PREREG_SCHEMA!r}"
        )
    if not record.get("written_before_any_scored_run"):
        failures.append(
            "does not assert written_before_any_scored_run — a threshold without that claim "
            "may have been chosen to fit the number"
        )
    if "written_before_any_run" in record:
        failures.append(
            "carries the unqualified written_before_any_run — that field is FALSE whenever any "
            "run exists, including an UNPREREGISTERED one, and a machine consumer keys on the "
            "name rather than the prose. Use written_before_any_scored_run (#9075 review P1)"
        )
    basis = str(record.get("written_before_any_scored_run_basis") or "")
    if UNSCORED not in basis:
        failures.append(
            "written_before_any_scored_run is asserted without naming the basis — it must state "
            f"that no SCORED run exists and name the {UNSCORED} log that predates it"
        )

    corpus = _mapping(record, "corpus", MACHINE_RECORD)
    pins_corpus = _mapping(pins, "corpus", "pins")

    def cmp(label: str, want, got) -> None:
        """The record may not disagree with the pins on a number it RESTATES.

        The pins are tree-verified (`check_pins`); the record is not. A record that restates a
        denominator is a second copy that a reader — and the run probe — will trust, so a corpus
        change that updates `fixtures_hash` and forgets `planted_units` must not pass green while
        the probe freezes a stale denominator (#9075 review r3 P1). `e4_denominator` is the sharp
        case: the record could otherwise smuggle the forbidden four-edge denominator back into the
        artefact the probe consumes while this gate stayed green.
        """
        if got != want:
            failures.append(
                f"{MACHINE_RECORD} {label} is {got!r} but the pins block says {want!r} — "
                "two copies of a denominator that can disagree"
            )

    # A REQUIRED list, not `if key in pins`: a `in` guard means a pins edit that DROPS a key
    # silently disables that cross-check, which is the failure mode the check exists to prevent
    # (#9075 review r4 P2).
    for key in CROSS_CHECKED_CORPUS_KEYS:
        if key not in pins_corpus:
            failures.append(
                f"pins.corpus omits {key!r} — the record is cross-checked against it, so a pins "
                "block that drops the key would silently disable that comparison"
            )
            continue
        cmp(f"corpus.{key}", pins_corpus[key], corpus.get(key))

    pins_floors = _pins_mapping(pins, "denominator_floors")
    rec_floors = _mapping(record, "denominator_floors", MACHINE_RECORD)
    if not pins_floors:
        failures.append(
            "pins.denominator_floors is empty — every REFUSED boundary the run probe enforces "
            "comes from it, and the record restates it"
        )
    for key, want in pins_floors.items():
        cmp(f"denominator_floors.{key}", want, rec_floors.get(key))
    for key in sorted(set(rec_floors) - set(pins_floors)):
        failures.append(
            f"{MACHINE_RECORD} denominator_floors.{key} has no pin — the record may not invent a "
            "REFUSED boundary the document does not state"
        )

    # The hypothesis bars, same shape as `denominator_floors` (#10468 review `f0b6842eb`, P2).
    # THE SHARP CASE IS NOT A DISAGREEMENT: the measured log names only five of the seven bars, so
    # the two it cannot be held to — `corroboration_min_sessions` (which decides the published
    # `not-majority-of-sessions` qualifier) and the emitted-only `sessions_total` — are held to
    # nothing at all unless this comparison exists. `hypothesis_bars` is optional at the PINS side
    # because it is a block this bundle introduced (`docs/evidence/7949-capstone` has no such map);
    # where the document does state it, the record may neither disagree, drop a bar, nor invent one.
    pins_bars = _pins_mapping(pins, "hypothesis_bars")
    rec_bars = _mapping(record, "hypothesis_bars", MACHINE_RECORD)
    if not pins_bars:
        if rec_bars:
            failures.append(
                f"{MACHINE_RECORD} hypothesis_bars has no pins block — the record may not invent a "
                "bar the document does not state"
            )
    else:
        for key, want in pins_bars.items():
            cmp(f"hypothesis_bars.{key}", want, rec_bars.get(key))
        for key in sorted(set(rec_bars) - set(pins_bars)):
            failures.append(
                f"{MACHINE_RECORD} hypothesis_bars.{key} has no pin — the record may not invent a "
                "bar the document does not state"
            )

    pins_split = _pins_mapping(pins, "split")
    rec_split = _mapping(record, "split", MACHINE_RECORD)
    for half in ("tune", "held_out"):
        cmp(f"split.{half}", sorted(pins_split.get(half) or []), sorted(rec_split.get(half) or []))

    if "e4_denominator" in pins:
        cmp("e4_denominator", pins["e4_denominator"], record.get("e4_denominator"))

    # The record must describe THIS document. Git-free by design: the ordering half cannot see a
    # delete-and-re-add rewrite of the argument (it takes the OLDEST add-epoch) and cannot see
    # anything at all in a shallow checkout, so the digest is the one bound that always applies
    # (#9075 review r3 P3). A re-edited argument must be accompanied by a re-cut record — which is
    # a second, reviewable artefact rather than a silent in-place edit.
    document = (directory / PREREG_NAME).read_bytes()
    want_document_sha = "sha256:" + hashlib.sha256(document).hexdigest()
    got_document_sha = record.get(DOCUMENT_SHA_PIN)
    if not got_document_sha:
        failures.append(
            f"{MACHINE_RECORD} carries no {DOCUMENT_SHA_PIN} — the record must pin the argument it "
            "describes, or a rewrite after a scored log leaves the record describing nothing"
        )
    elif got_document_sha != want_document_sha:
        failures.append(
            f"{MACHINE_RECORD} {DOCUMENT_SHA_PIN} is {got_document_sha!r} but {PREREG_NAME} hashes "
            f"to {want_document_sha!r} — the argument was re-edited without the record being re-cut"
        )

    # Each criterion row's threshold in the record must match the table's, character for character.
    rows = parse_criteria_rows(document.decode())
    table = {
        r["id"]: {part: _plain(r[part]) for part in ("metric", "denominator", "threshold")}
        for r in rows
    }
    for crit in _list(record, "criteria", MACHINE_RECORD):
        if not isinstance(crit, dict):
            failures.append(
                f"{MACHINE_RECORD} has a criteria entry that is "
                f"{type(crit).__name__}, expected a mapping"
            )
            continue
        cid = str(crit.get("id") or "")
        if cid not in table:
            failures.append(f"{MACHINE_RECORD} carries criterion {cid!r} with no row in the table")
            continue
        for part in ("metric", "denominator", "threshold"):
            if _plain(str(crit.get(part) or "")) != table[cid][part]:
                failures.append(
                    f"{MACHINE_RECORD} {part} for {cid} is {crit.get(part)!r} but the table "
                    f"says {table[cid][part]!r} — the record is what the run probe FREEZES, so a "
                    "paraphrase here is a second copy that can disagree"
                )
    missing = sorted(
        set(table)
        - {str(c.get("id")) for c in _list(record, "criteria", MACHINE_RECORD)
           if isinstance(c, dict)}
    )
    if missing:
        failures.append(f"{MACHINE_RECORD} omits criteria rows {missing}")

    for match in FOUR_EDGE_RATE.finditer(raw):
        failures.append(
            f"{MACHINE_RECORD}: four-edge denominator rate {match.group(0)!r} — the record is "
            "frozen by the run probe, so the forbidden denominator may not appear here either"
        )

    # ⚠️ This field is the PINNED class only (`pre_existing_unscored_logs`): it is frozen with the
    # record, and a log the ordering check exempts on its CONTENT (see
    # `_log_declares_no_measurement`) structurally cannot join it — the pins' own bounds require a
    # `prereg=UNPREREGISTERED` vocabulary and no post-registration touch. The content-exempted set is
    # reported per run instead (`ordering_exempt`), never restated into this frozen record (#9089).
    exempt = sorted(_pins_mapping(pins, "pre_existing_unscored_logs"))
    if sorted(_list(record, "unscored_runs_before_this_registration", MACHINE_RECORD)) != exempt:
        failures.append(
            f"{MACHINE_RECORD} unscored_runs_before_this_registration is "
            f"{record.get('unscored_runs_before_this_registration')!r} but the pins exempt {exempt!r}"
        )
    return failures


# ── the checks ──────────────────────────────────────────────────────────────


def check_single_measured_log(directory: Path) -> list[str]:
    """``[]`` unless MORE THAN ONE scored log claims to be THE measurement.

    The failure this closes is not "two green rows" — it is that ``check_ordering`` iterates every
    ``*.log`` it finds and never asks how many of them are SCORED, so a superseded run left beside
    the current one is accepted (ordering-wise) and the directory then holds two artifacts claiming
    to be the measurement. Which one a reader quotes becomes a filename-order accident, and the
    bundle's own reproduce instruction (write ``capstone-measured-<new-head8>.log``) produces that
    state on every re-run unless the old file is removed.

    The message names the files and the repair, because this runs as a required CI step: a red that
    does not say what to change is a red someone disables.
    """
    scored = sorted(directory.glob(SCORED_LOG_GLOB))
    if len(scored) <= 1:
        return []
    return [
        f"{len(scored)} scored measurement logs claim to be THE measurement "
        f"({', '.join(p.name for p in scored)}), but exactly ONE may exist: a superseded "
        f"run is replaced by RE-RUNNING `probes/capstone_measure.py` (which writes a new "
        f"`capstone-measured-<head8>.log`) and DELETING the file it supersedes, with the "
        f"supersede chain disclosed in README.md and docs/00_index.md. Keeping both leaves "
        f"two artifacts claiming to be the measurement, and which one a reader quotes becomes "
        f"an accident of filename order"
    ]


def _steps_block_epoch(directory: Path, current: dict | None = None) -> int | None:
    """The commit epoch that FIRST carried the record's CURRENT ``steps`` block.

    Two weaker predicates were rejected by review, and both are why this compares
    CONTENT rather than the key's first appearance:

    * ``"steps" in record`` returns at the revision that merely CONTAINS the key, so
      an author who commits ``"steps": null`` with the record's initial (definitionally
      pre-scored) commit and fills the real block after seeing the number stays green
      forever — a key test is not a content test.
    * an already-pinned block **edited after** a scored log never moves the key's
      first appearance either, so changing the acceptance rule post-number would be
      invisible — the exact shape this rule exists to catch.

    So: the oldest revision whose ``steps`` value equals the block the record ships
    NOW. That is when the operative rule was authored. ``None`` when no revision
    carries it (or git cannot answer), which the caller reads as "nothing to
    compare", never as "fine".
    """
    path = directory / MACHINE_RECORD
    if current is None:
        current = _record_for_steps(directory)
    wanted = current.get("steps")
    if not isinstance(wanted, dict) or not wanted:
        return None                      # an empty/absent block is not a rule
    try:
        rel = path.resolve().relative_to(REPO_ROOT)
    except ValueError:
        return None
    try:
        out = subprocess.run(["git", "log", "--reverse", "--format=%H %ct", "--", str(rel)],
                             cwd=str(REPO_ROOT), capture_output=True, text=True, check=False)
    except OSError:                                   # pragma: no cover - git absent
        return None
    if out.returncode != 0:
        return None
    for line in out.stdout.splitlines():
        parts = line.split()
        if len(parts) != 2 or not parts[1].isdigit():
            continue
        sha, epoch = parts[0], int(parts[1])
        try:
            blob = subprocess.run(["git", "show", f"{sha}:{rel}"],
                                  cwd=str(REPO_ROOT), capture_output=True, text=True,
                                  check=False)
            record = json.loads(blob.stdout)
        except (OSError, json.JSONDecodeError, RecursionError):
            continue
        if isinstance(record, dict) and record.get("steps") == wanted:
            return epoch
    return None


def _heading_level(line: str) -> int:
    """ATX heading level (1-6), or 0 for a non-heading. `#` must be followed by
    space/EOL, so `#123` is not a heading."""
    hashes = len(line) - len(line.lstrip("#"))
    if not 0 < hashes <= 6:
        return 0
    rest = line[hashes:]
    return hashes if not rest or rest[0] in " \t" else 0


#: A level-2 heading that IS the deviations section, in EITHER shape this document has used:
#: a bare ``## Deviations``, or a NUMBERED one — ``## 10. Deviations — the three amendments …``,
#: which is what the shipped registration carries now that `#10525`'s own numbered section sits
#: in front of this one. ⛔ Matching only the bare spelling made the REAL document read as
#: carrying no deviations at all: a §8 gate failing on the shape of a heading rather than on the
#: disclosure, and failing CLOSED on the honest document (measured on the #10215 merge).
_DEVIATIONS_HEADING = re.compile(r"^\s*##\s+(?:\d+\.\s+)?Deviations\b")

#: An entry under it, in either spelling: ``### D1 …`` (how this section started) or the numbered
#: ``### 10.5 …`` the merged document uses for the same thing. A numbered heading is only an
#: entry when it is a SECTION heading (``N.M``), never a section of its own (``## 11. …``).
_DEVIATIONS_ENTRY = re.compile(r"^\s*###\s+(?:D\d*\b|\d+\.\d+\b)")


def _document_has_deviations(directory: Path) -> bool:
    """Whether the argument carries a REAL deviations entry (§8).

    ⛔ A bare heading is not a disclosure, and accepting one would restore the
    "add one line to clear the finding" exit this rule was repaired to close — the
    same shape, moved from JSON into Markdown. So an entry (``### D…``) must carry a
    non-blank body line.

    Two bounds, each a defect a review measured:

    * the body ENDS at the next heading of the entry's own level or SHALLOWER
      (``<= 3``). A scan to EOF let a bare ``### D1`` above a later section's prose
      pass; stopping at ANY ``#``-line instead made a properly structured entry —
      ``#### Original rule`` / text / ``#### New rule`` / text — read as bodiless,
      so the repair the failure message prescribes could not clear the finding.
    * EVERY ``### D`` entry is examined, not just the first: an empty ``### D1``
      above a filled ``### D2`` is a real disclosure, and reading only the first
      reported it as absent.
    """
    try:
        text = (directory / PREREG_NAME).read_text()
    except (OSError, UnicodeDecodeError):
        return False
    lines = text.splitlines()
    head = next((i for i, ln in enumerate(lines) if _DEVIATIONS_HEADING.match(ln)), None)
    if head is None:
        return False
    # ⛔ The scan is bounded at the SECTION, not just at the entry (#10215 round 9).
    # `body = lines[head + 1:]` ran to EOF, so ANY later `### D…`-prefixed heading — a
    # `### Denominator floors`, a `### Data sources` under `## Appendix`, a fenced
    # example — donated its body to an otherwise EMPTY Deviations section. Measured:
    # `## Deviations` / `## Appendix` / `### Data sources` / text returned True. That is
    # the "add one line to clear the finding" exit round 7 closed, reachable one section
    # over, and it is a false PASS of a §8 gate. A section ends at the next heading of
    # level 2 or shallower.
    section_end = next((j for j in range(head + 1, len(lines))
                        if 0 < _heading_level(lines[j]) <= 2), len(lines))
    body = lines[head + 1:section_end]
    for i, line in enumerate(body):
        if not _DEVIATIONS_ENTRY.match(line):
            continue
        stop = next((j for j in range(i + 1, len(body))
                     if 0 < _heading_level(body[j]) <= 3), len(body))
        # a body line is any non-blank line that is NOT itself a heading
        if any(ln.strip() and not _heading_level(ln) for ln in body[i + 1:stop]):
            return True
    return False


def check_steps_block_is_pre_scored(directory: Path) -> list[str]:
    """A ``steps`` pin authored AFTER a scored log is a PROTOCOL DEVIATION (#10215).

    ``written_before_any_scored_run`` describes the record's ORIGINAL freeze, and
    the ``steps`` block is not original: it is the acceptance rule V5 applies
    (``capstone_verdict_check.py::_step_accounting`` rules 1 and 4). Pinning the
    rule once a scored log exists is the "rule chosen after the number" shape §8
    exists to prevent, and the blanket claim makes the record read as if the new
    content were covered.

    ⛔ **The predicate is AUTHORING ORDER, never a self-declaration.** An earlier
    cut of this rule required the record to name the log it postdated, which was
    wrong in both directions: the honest lifecycle — freeze ``steps``, then let
    the probe write ``capstone-measured-<head8>.log`` into this same directory —
    arrives with a ``steps`` block and a scored log and was REFUSED, while the
    only passing exit was to add the declaration, i.e. to clear a false-pass
    finding by writing one JSON line. A gate satisfied by asserting it is not a
    gate. Order is computed instead, from the same history ``check_ordering``
    reads, and where git cannot establish it (a shallow checkout) the rule
    returns NO verdict rather than demanding an attestation.

    ⛔ ``steps_pinned_after_scored_log`` is NOT required and NOT an incompatibility.
    Those two claims describe different things — ``written_before_any_scored_run`` is
    the record's ORIGINAL freeze, while the field names a log the pin postdates — so
    a record may honestly carry both, and an earlier arm that refused the pairing was
    wrong. The field is retired: carrying it is reported as a dead key to delete.
    """
    record = _record_for_steps(directory)
    if "steps" not in record:
        return []
    scored = sorted(directory.glob(SCORED_LOG_GLOB))
    if not scored:
        return []

    failures: list[str] = []
    # ⛔ `steps_pinned_after_scored_log` is RETIRED (#10215). Round 5 REQUIRED it, which
    # was wrong in both directions; this branch then began REFUSING it, on the premise
    # that it contradicts `written_before_any_scored_run`. It does not — that field
    # describes the record's ORIGINAL freeze, so naming the log the pin postdates is the
    # honest pairing, and the repair this branch prescribed ("record the deviation") could
    # never clear it because the return preceded the document read. The gate dates the
    # block itself now, so the key is dead weight: its presence is reported as exactly
    # that — a retired key to delete — and nothing else.
    if record.get(STEPS_PINNED_AFTER) not in (None, ""):
        failures.append(
            f"{MACHINE_RECORD} carries {STEPS_PINNED_AFTER}, a RETIRED field (#10215): it was "
            "the earlier fix's self-declaration, and no gate reads it — the step rule dates the "
            "record's CURRENT `steps` block from history instead and takes the deviation from "
            "the argument's `## Deviations` section. Delete the field; nothing needs it"
        )
        return failures

    if history_unavailable():
        return failures          # UNPROVABLE — never demand an attestation instead

    steps_epoch = _steps_block_epoch(directory, record)
    log_epochs = [epoch for log in scored for epoch in _added_epochs(log)]
    if steps_epoch is None or not log_epochs:
        return failures          # nothing to compare; not a finding either way
    earliest = min(log_epochs)
    if earliest < steps_epoch and not _document_has_deviations(directory):
        failures.append(
            f"{MACHINE_RECORD}'s `steps` block was authored {steps_epoch - earliest}s AFTER "
            f"the scored log(s) {', '.join(p.name for p in scored)} were added, and "
            f"{PREREG_NAME} carries no `## Deviations` section — the step set IS the "
            "acceptance rule V5 applies, so a rule pinned after the number was seen is a "
            "PROTOCOL DEVIATION (§8): record it with the date, the original rule, the new "
            "rule and the reason"
        )
    return failures


def required_criteria_for(pins: dict) -> tuple[str, ...]:
    """The rows THIS bundle must carry — the core slots, plus ``E6`` for the capstone.

    ⛔ THE SET IS PER-BUNDLE BECAUSE THE TOOL IS SHARED. The gate's own CI step hands it TWO bundles
    in one invocation (`.github/workflows/python-ci.yml`: `--dir docs/evidence/7949-capstone
    --no-corpus --dir docs/evidence/5087-hypothesis`), and the two use the `E1`–`E5` slot names for
    DIFFERENT measurements — `#5087`'s registration says exactly that (*"the checker's slot
    vocabulary"*). One global required set therefore either demands the capstone's sixth row of an
    epic that has no such criterion, or drops the sixth row from the epic that does.

    The declaration is the pins' own ``required_criteria_rows``, and the reading is FAIL-CLOSED:
    only the EXACT core set narrows the requirement, so a bundle cannot lose a requirement by
    breaking its own declaration (absent, malformed, or any other combination is held to the full
    set), and `check_pins` refuses a declaration that is neither of the two sanctioned sets.

    ⚠️ The narrowing is gapless rather than airtight on its own: a bundle that defines a row beyond
    the core set AND does not require it is refused by `check_pins` (the corpus half), because that
    is the exact shape — a criterion the registration describes and the gate never demands — that
    let the capstone's sixth row go unregistered (#11673).
    """
    declared = pins.get("required_criteria_rows")
    if isinstance(declared, (list, tuple)) and list(declared) == list(CORE_REQUIRED_CRITERIA):
        return CORE_REQUIRED_CRITERIA
    return REQUIRED_CRITERIA


def check_completeness(rows: list[dict[str, str]],
                       row_floors: dict[str, float] | None = None,
                       required: tuple[str, ...] | None = None) -> list[str]:
    required_ids = REQUIRED_CRITERIA if required is None else tuple(required)
    failures: list[str] = []
    if not rows:
        return [
            "no criteria table found — the document must carry a table whose header names "
            "metric, denominator and threshold"
        ]
    for row in rows:
        missing = [
            name
            for name in ("metric", "denominator", "threshold")
            if is_placeholder(row[name])
        ]
        if missing:
            failures.append(
                f"row {row['id']!r} (line {row['line']}) is missing {', '.join(missing)} — "
                "a criteria row must name all three before any run"
            )
    for required_id in required_ids:
        if required_id in SUB_ROW_CRITERIA:
            present = any(
                r["id"] == required_id or r["id"].startswith(required_id) for r in rows
            )
        else:
            # EXACT: ``E1b`` is E1's control, not a stand-in for E1 (#9075 review r3 P2).
            present = any(r["id"] == required_id for r in rows)
        if not present:
            failures.append(f"criterion {required_id} has no row")
    for row in rows:
        if is_placeholder(row["threshold"]):
            continue  # already reported above
        plain = _plain(row["threshold"])
        if REGISTERED_NO_BAR.search(plain):
            # ⛔ SCOPED TO THE ROWS THE RECORD REGISTERS AS NON-BARS — never "any row that says
            # UNMEASURED". The branch below `continue`s past BOTH numeric arms, so an unscoped
            # branch let a REQUIRED row (`E1`–`E5`) declare itself bar-less and still leave the gate
            # at OK, contradicting §7 item 1's "every criteria row names a numeric threshold" — a
            # false PASS added by the very arm meant to close one (#10982 review round 5). The
            # allowed set is pinned and asserted DISJOINT from REQUIRED_CRITERIA, so a required row
            # cannot enter it by editing the record alone.
            if row["id"] not in REGISTERED_NON_BAR_ROWS:
                failures.append(
                    f"row {row['id']!r} (line {row['line']}): threshold {row['threshold']!r} "
                    f"declares UNMEASURED(...) but this row is not one the record registers as a "
                    f"non-bar (registered: {sorted(REGISTERED_NON_BAR_ROWS)}) — either give it a "
                    "numeric threshold or add it to REGISTERED_NON_BAR_ROWS in this tool (§7 item 1)"
                )
                continue
            # ⛔ A REGISTERED non-bar is not a bar, and its FLOOR clause may never stand in for one.
            # Before this arm, a threshold cell declaring `UNMEASURED(...)` passed the numeric test
            # on the `>= 30` its own floor clause happened to contain — so a row the record says has
            # NO bar satisfied the "has a bar" check, i.e. a false PASS in the completeness gate
            # (§7 item 1; #10982 review round 2). A floor is a REFUSAL boundary (§3.7), so:
            #   * the cell must name the OWNER DECISION that gates the row, and
            #   * no comparator may survive outside the floor clause.
            if not OWNER_DECISION.search(plain):
                failures.append(
                    f"row {row['id']!r} (line {row['line']}): threshold {row['threshold']!r} "
                    "declares UNMEASURED(...) but names no issue (#NNNN) that gates it — "
                    "a registered non-bar must state who decides it"
                )
            # INDEPENDENT of the arm above, never `elif`: a cell can name its owner decision and
            # STILL smuggle a comparator out of the floor clause, and that is the mutation this
            # half exists to catch.
            # ⛔ THE WHOLE CELL, minus the row's OWN floor clause — never the substring AFTER the
            # marker, and never an arbitrary `floor of …`.
            # `REGISTERED_NO_BAR.split(text, 1)[-1]` is everything to the right of `UNMEASURED(`,
            # so a bar written FIRST (`>= 0.006` typed_share — UNMEASURED(…)) was in the PREFIX
            # and never scanned: measured at #11331 review round 7, that cell exited OK with an
            # empty finding list while the same bar placed after the marker was refused. A4 is
            # the one row whose registration is that it has NO bar, and its threshold cell is
            # what the run/scoring child reads, so a bar surviving here is the owner-decision
            # bypass this arm exists to close (§7 item 1, §3.7, §10.11).
            # ⛔ AND THE EXEMPTION IS THE PINNED VALUE **AND ITS UNIT**, not the phrase (#11331
            # review rounds 8 and 10): stripping any `floor of <comparator> <number>` let the two
            # words launder a bar (`floor of >= 0.5 typed_share` exited OK), and binding on the
            # bare NUMBER still excused `floor of >= 30 % typed` — the same 30 as `A4`'s count
            # floor, spent as a RATE. `A4` is the row whose registration is that it has NO bar, so
            # either variant is an owner-decision bypass (§7 item 1, §3.7, §10.11).
            remainder = (
                # ⛔ NO MAP AT ALL is a unit test on a bare row list: strip by shape, the legacy
                # behaviour. A map that is PRESENT but does not name this row is the absent-row
                # case and strips nothing, so the comparator reds — the fail-closed direction the
                # docstring promises. The gate always supplies a map, so `None` never reaches here
                # on a real check (#11331 review round 9: `{}` used to fall into `None`).
                FLOOR_CLAUSE.sub("", plain) if row_floors is None
                else _strip_the_rows_own_floor(
                    plain,
                    row_floors.get(row["id"]),
                    getattr(row_floors, "units", {}).get(row["id"]),
                )
            )
            if any(c in remainder for c in COMPARATORS):
                failures.append(
                    f"row {row['id']!r} (line {row['line']}): threshold {row['threshold']!r} "
                    "declares UNMEASURED(...) and still carries a comparator outside its floor "
                    "clause — a registered non-bar cannot also be a bar. A floor clause is "
                    "exempted only where the row's own floor unit is followed by closing "
                    "punctuation to the END of the cell: a rate spelled after the unit "
                    "(`typed statements per claim`) re-scopes the number and is refused, so move "
                    "the floor clause to the end of the cell"
                )
            continue
        if row["id"] in REGISTERED_NON_BAR_ROWS:
            # …and the REVERSE direction: a row the code lets declare itself bar-less must still BE
            # bar-less in the record. Without this, deleting `UNMEASURED(...)` from A4's cell while
            # leaving the pin alone would silently turn the exemption into a numeric bar.
            failures.append(
                f"row {row['id']!r} (line {row['line']}): this row is registered as a non-bar but "
                f"its threshold {row['threshold']!r} carries a real bar — drop it from "
                "REGISTERED_NON_BAR_ROWS or restore UNMEASURED(...)"
            )
            continue
        if not re.search(r"\d", plain):
            failures.append(
                f"row {row['id']!r} (line {row['line']}): threshold {row['threshold']!r} carries "
                "no number"
            )
        elif not any(c in plain for c in COMPARATORS):
            failures.append(
                f"row {row['id']!r} (line {row['line']}): threshold {row['threshold']!r} carries "
                "no comparator (<=, >=, <, >, =) — a number without a direction is not a bar"
            )
    return failures


def check_e4_denominator(rows: list[dict[str, str]], text: str, pins: dict) -> list[str]:
    failures: list[str] = []
    e4_rows = [r for r in rows if r["id"] == "E4"]
    if not e4_rows:
        failures.append("no E4 row — the planted-operator denominator cannot be checked")
    for row in e4_rows:
        plain = _plain(row["denominator"])
        if not re.search(r"\b15\b", plain):
            failures.append(
                f"E4 (line {row['line']}): denominator {row['denominator']!r} does not state 15 — "
                "the planted-operator gold is 15 edges"
            )
        if FOUR_EDGE_RATE.search(plain):
            failures.append(
                f"E4 (line {row['line']}): a four-edge denominator rate appears in the denominator "
                "cell — that denominator cannot separate a fix from variance"
            )
    pinned = pins.get("e4_denominator")
    if pinned != 15:
        failures.append(f"pins.e4_denominator is {pinned!r}, must be 15")
    for match in FOUR_EDGE_RATE.finditer(text):
        line_no = text[: match.start()].count("\n") + 1
        failures.append(
            f"line {line_no}: four-edge denominator rate {match.group(0)!r} — the E4 denominator "
            "is 15; the 4-edge history is stated in words, never as a rate to be copied"
        )
    return failures


def check_pins(pins: dict, derived: dict,
               rows: list[dict[str, str]] | None = None) -> list[str]:
    failures: list[str] = []
    corpus = pins.get("corpus") or {}
    salience_pins = _pins_mapping(pins, "salience_labels")
    split = pins.get("split") or {}

    def cmp(label: str, want, got) -> None:
        if want != got:
            failures.append(f"pin {label} is {want!r} but the tree gives {got!r}")

    cmp("corpus.fixtures_hash", corpus.get("fixtures_hash"), derived["fixtures_hash"])
    cmp("corpus.sessions", corpus.get("sessions"), len(derived["sessions"]))
    cmp("corpus.planted_units", corpus.get("planted_units"), derived["planted_units"])
    cmp("corpus.planted_operator_edges", corpus.get("planted_operator_edges"),
        derived["planted_operator_edges"])
    cmp("corpus.value_bearing_units", corpus.get("value_bearing_units"),
        derived["value_bearing_units"])
    cmp("corpus.hedge_scope_sentences", corpus.get("hedge_scope_sentences"),
        derived["hedge_scope_sentences"])
    cmp("corpus.hedge_scope_sentences_total", corpus.get("hedge_scope_sentences_total"),
        derived["sentences_total"])
    cmp("salience_labels.rows", salience_pins.get("rows"), derived["salience"]["rows"])
    cmp("salience_labels.in_scope", salience_pins.get("in_scope"),
        derived["salience"]["in_scope"])
    cmp("salience_labels.positively_classified", salience_pins.get("positively_classified"),
        derived["salience"]["positively_classified"])
    cmp("salience_labels.deferred", salience_pins.get("deferred"),
        derived["salience"]["deferred"])
    cmp("salience_labels.detected_classes", salience_pins.get("detected_classes"),
        derived["salience"]["detected_classes"])

    if derived["missing_exclusions"]:
        failures.append(
            "corpus.value_excluded_unit_ids names ids absent from the gold: "
            f"{derived['missing_exclusions']}"
        )
    if derived.get("idle_exclusions"):
        failures.append(
            "corpus.value_excluded_unit_ids excludes ids whose anchor carries no digit: "
            f"{derived['idle_exclusions']} — an exclusion that excludes nothing value-bearing "
            "cannot be the named reason a unit left the 14, so it is a stale pin"
        )

    tune = list(split.get("tune") or [])
    held = list(split.get("held_out") or [])
    sessions = list(derived["sessions"])
    if sorted(tune + held) != sorted(sessions):
        failures.append(
            f"the split is not a partition of the corpus: tune={tune} held_out={held} "
            f"sessions={sessions}"
        )
    if set(tune) & set(held):
        failures.append(f"the split halves overlap: {sorted(set(tune) & set(held))}")
    unknown = sorted(set(tune + held) - set(sessions))
    if unknown:
        failures.append(f"the split names sessions that are not in the corpus: {unknown}")
    if not failures:
        cmp("split.tune_counts", split.get("tune_counts"), _half_counts(derived, tune))
        cmp("split.held_out_counts", split.get("held_out_counts"), _half_counts(derived, held))

    # ⛔ TWO SANCTIONED SETS, AND A DECLARATION OUTSIDE THEM IS REFUSED (#11673). The core set is
    # what every bundle shares; the full set adds the capstone's sixth row (§10.21). A third set —
    # invented, or the core set with an `E`-row quietly dropped — is the shape that would let a
    # required criterion disappear by editing the record alone, so it is refused here rather than
    # read as "whatever this bundle says".
    sanctioned = [list(CORE_REQUIRED_CRITERIA), list(REQUIRED_CRITERIA)]
    declared = list(pins.get("required_criteria_rows") or [])
    if declared not in sanctioned:
        failures.append(
            f"pins.required_criteria_rows is {declared!r}, must be one of {sanctioned} — the core "
            "set every bundle shares, or that set plus `E6`"
        )
    elif declared != list(REQUIRED_CRITERIA) and rows is not None:
        # …and the reverse direction: a bundle that DEFINES a required id and does not require it.
        # This is the capstone's own defect shape — `E6` described in §3.1 while the gate demanded
        # only E1–E5 — so a table carrying `E6` under a core-only declaration is refused.
        defined_beyond_core = sorted(
            r["id"] for r in rows
            if r["id"] in set(REQUIRED_CRITERIA) - set(CORE_REQUIRED_CRITERIA)
        )
        if defined_beyond_core:
            failures.append(
                f"the criteria table carries {defined_beyond_core} but pins.required_criteria_rows "
                f"is {declared!r} — a row the registration defines and the gate never demands is "
                "exactly the shape that let the capstone's sixth row go unregistered (#11673): "
                "require it, or remove the row"
            )
    return failures


# ── entry point ─────────────────────────────────────────────────────────────


def run(directory: Path, use_git: bool = True, use_corpus: bool = True,
        base_ref: str | None = None,
        allow_atomic_disclosure: bool = False) -> dict:
    """The report for one bundle.

    ``allow_atomic_disclosure`` arms THIS BRANCH'S PROPOSED ordering rule (see ``check_ordering``),
    and it is OFF by default on purpose (#10546 review cycle 17, IS-1): the recorded ruling on
    `#10463` keeps an atomic pair a refusal, and the arm is a proposal rather than a result. It is
    also the reason the armed path reports its own ``ordering_base`` — the proposal orders the pair
    out of THIS contribution's history, so printing the base the ruled path measures against would
    say a measurement happened against a ref the armed rule never consulted.
    """
    prereg = directory / PREREG_NAME
    if not prereg.is_file():
        raise CheckError(f"no {PREREG_NAME} in {directory}")
    try:
        text = prereg.read_text()
    except UnicodeDecodeError as exc:
        # #9089 review cycle 6: 
        # "a check that cannot read its subject must never report success" — and it must not
        # report a VERDICT either. A non-UTF-8 document raised UnicodeDecodeError, which is not a
        # CheckError, so `main` exited 1 (the code for findings) on a document it never read.
        # Exit 2 is the documented answer for an unreadable subject.
        raise CheckError(
            f"{PREREG_NAME} is not valid UTF-8 ({exc}) — an unreadable document is a check "
            "that could not be performed, never a finding about its content"
        ) from exc
    rows = parse_criteria_rows(text)
    pins = parse_pins(text)

    report: dict = {
        "document": str(prereg.relative_to(REPO_ROOT)) if prereg.is_relative_to(REPO_ROOT)
        else str(prereg),
        "rows": [{"id": r["id"], "line": r["line"]} for r in rows],
        "checks": {},
    }
    failures: list[str] = []
    failures += check_completeness(rows, _registered_floor_values(directory),
                                   required=required_criteria_for(pins))
    failures += check_e4_denominator(rows, text, pins)
    registry_scopes: list[str] = []
    failures += check_registry_contradictions(directory, rows, pins, registry_scopes)

    logs = sorted(directory.glob(EVIDENCE_LOG_GLOB))
    report["evidence_logs"] = [p.name for p in logs]
    report["scored_logs"] = [p.name for p in sorted(directory.glob(SCORED_LOG_GLOB))]
    failures += check_single_measured_log(directory)
    if use_git:
        try:
            content_exempt: list[str] = []
            # The base the ordering WOULD be measured against. ⛔ It is deliberately NOT written
            # into `ordering_base` here: `check_ordering` may raise before it reads a single epoch
            # (the shallow `HistoryUnavailable` arm), and publishing the resolved ref under
            # "ordering measured against" would then say a measurement happened when none did —
            # the same silent-green `ordering_base` exists to remove, one polarity over (#10463
            # review cycle 7, finding F2). Measured on a depth-1 clone of `main`, `origin/main`
            # DOES resolve, so this is the CI shape that prints a base it never used.
            candidate = resolve_base_ref(base_ref) or UNRESOLVED_BASE
            report["ordering_base_candidate"] = candidate
            unmeasured: list[str] = []
            atomic_added: list[str] = []
            atomic_detail: dict = {}
            # ONE read: the block that was JUDGED and the block that is PRINTED must be the same
            # value (#10529 review cycle 3, P2) — two reads could differ under a concurrent edit.
            disclosures = _record_atomic_add(directory)
            unreadable_atomic: list[str] = []
            ordering_failures = check_ordering(
                prereg, logs, pins, content_exempt, atomic_added, disclosures, atomic_detail,
                record_unreadable=disclosures is None, unreadable_atomic=unreadable_atomic,
                allow_atomic_disclosure=allow_atomic_disclosure,
                base_ref=base_ref, unmeasured=unmeasured,
            )
            failures += ordering_failures
            report["ordering_exempt"] = sorted(content_exempt)
            report["ordering_atomic_add"] = sorted(atomic_added)
            if atomic_detail:
                report["ordering_atomic_add_commits"] = {
                    name: atomic_detail[name] for name in sorted(atomic_detail)
                }
            if allow_atomic_disclosure:
                # ⛔ The armed path measured the pair out of THIS contribution, not against the base,
                # so naming the base here would restate the silent-green `ordering_base` exists to
                # remove (#10463 review cycle 4). The claim is qualified per outcome below — and it
                # is claimed ONLY when a pair was actually ordered: printing it over an empty
                # comparison is the same lie one polarity over (#10546 review at `01d9ce879`,
                # ISSUE 1), so the UNMEASURED arm leaves `ordering_base` at its `(not measured)`
                # default and `main()` prints the candidate on its own "would be measured" line.
                if ordering_failures:
                    # A FAILED ordering half must not carry a green CLAIM (#10529 review cycle 6,
                    # P2): `run_order_claim` is the field a machine consumer reads by name.
                    ordering = "failed"
                    report["ordering_base"] = PROPOSED_ORDERING_BASE
                elif unmeasured:
                    # ⛔ MIRRORS THE RULED PATH'S ARM, ONE PATH OVER (#10546 review at `01d9ce879`,
                    # ISSUE 1). `_check_ordering_proposed` had no comparison counter and no
                    # `unmeasured` out-param, so a bundle it walked without ordering a single pair
                    # — a registration whose only log was withdrawn, or whose every log is
                    # untracked — fell through to the `else` below and reported `verified`, and
                    # `run_order_claim` (the field a machine consumer reads by name) said
                    # `verified` over zero comparisons. The counter exists now, and this arm is
                    # where it is read: the sibling has refused this shape since #10463 cycle 12.
                    ordering = f"{ORDERING_UNMEASURED} ({unmeasured[0]})"
                elif unreadable_atomic:
                    ordering = (
                        "UNPROVEN (the machine record is unreadable, so the atomic add of "
                        + ", ".join(sorted(unreadable_atomic)) + " cannot be disclosed)"
                    )
                    report["ordering_base"] = PROPOSED_ORDERING_BASE
                elif atomic_added:
                    ordering = ("verified (atomic-add DECLARED: "
                                + ", ".join(sorted(atomic_added)) + ")")
                    report["ordering_base"] = PROPOSED_ORDERING_BASE
                else:
                    # Reaching here means `unmeasured` is empty AND the arm did not refuse: at
                    # least one pair was ordered, so the proposed base is a measurement, not a hope.
                    ordering = "verified"
                    report["ordering_base"] = PROPOSED_ORDERING_BASE
            elif unmeasured:
                # ⛔ The arm marks the outcome only when NOTHING was compared (review cycle 12):
                # a registration ReadError, a registration absent from the base, a bundle with no
                # log the history can date, or every log untracked. A bundle with no log at ALL and
                # no registration on the base returns from the branch above; a bundle that DID
                # compare a pair is measured, whatever else failed.
                ordering = f"{ORDERING_UNMEASURED} ({unmeasured[0]})"
            elif unreadable_atomic:
                # ⛔ THE RULED PATH CAN DECIDE NOTHING TOO (#10546 merge). When the machine record
                # cannot be read AT ALL, `check_ordering` reports that log as `unreadable_atomic`
                # instead of inventing an ordering finding (`check_machine_record` owns the
                # diagnosis), so `ordering_failures` is empty and — without this arm — the claim
                # would read `verified` for a half that concluded nothing about that log. That is
                # the cycle-6 defect class on the path the merge introduced: the field a machine
                # consumer reads by NAME may not dress "decided nothing" as "verified".
                ordering = (
                    "UNPROVEN (the machine record is unreadable, so the atomic add of "
                    + ", ".join(sorted(unreadable_atomic)) + " could not be judged)"
                )
                # ⛔ …and a VIOLATION beside it was still MEASURED (#10546 review cycle 25, the
                # mirror of #10548 r15 F3). This arm is consulted before `ordering_failures`, so an
                # unreadable record that ALSO carries a refusal left `ordering_base` at its
                # `(not measured)` default while the output printed the very ref the pair was
                # refused against. `unmeasured` is empty here (its arm precedes this one), so a pair
                # really was compared: the BASE answers "what was this measured against", which on a
                # refusal is the ref that did the refusing.
                if ordering_failures:
                    report["ordering_base"] = candidate
            elif ordering_failures:
                # ⛔ "verified" is a claim about the ORDERING, so it may not be taken from the
                # absence of an `unmeasured` note alone (review cycle 13, finding 2). On a
                # VIOLATING tree the arm compared its pairs and REFUSED, `unmeasured` stayed empty,
                # and this field still read `verified` beside two "not strictly after" failures —
                # the field a consumer keys on reporting the opposite of the measurement.
                ordering = "failed"
                # …but the BASE is reported, because a violated pair WAS measured (#10548 r15 F3).
                # Reaching this branch means `unmeasured` is empty, and `check_ordering` appends to
                # it on EVERY arm that returns before an epoch comparison plus once more when
                # `compared == 0` — so at least one (registration, log) pair really was compared
                # against `candidate`. Printing `(not measured)` here said no measurement happened
                # while two failure lines in the same output named `origin/main` as the ref they
                # were refused against, and `docs/evidence/7949-capstone/README.md` + the withdrawn
                # note now both tell a reader to trust precisely this line. The field is named for
                # the MEASUREMENT, not for the verdict: it answers "what was this compared
                # against", which on a refusal is the ref that did the refusing.
                report["ordering_base"] = candidate
            else:
                ordering = "verified"
                # Only NOW is the base a fact about this verdict rather than an intention.
                report["ordering_base"] = candidate
        except HistoryUnavailable as exc:
            # The THIRD outcome: neither a pass nor an ordering failure. Folding it into either is
            # what reds a correctly ordered tree in every CI test job (#9075 r4 P1). The git-FREE
            # findings it carries are kept — an unprovable history must not clear a laundering
            # finding that needed no history to prove.
            failures += exc.failures
            # ⛔ The CAUSE is carried through, not flattened to the shared constant (review cycle 1,
            # finding 2): reporting "shallow clone … run git fetch --unshallow" on a FULL clone whose
            # base ref does not resolve sends the operator to a remedy that cannot work. If a new
            # HistoryUnavailable arm is added, its own text reaches the report automatically.
            ordering = HISTORY_UNAVAILABLE if str(exc) == HISTORY_UNAVAILABLE else str(exc)
    else:
        ordering = "UNVERIFIED (--no-git)"
    # Present on EVERY path so no consumer has to handle an absent key. ⛔ `[]` does NOT mean the
    # arm ran — it is the same value whether nothing was exempted or the arm never executed
    # (`--no-git` does not run it at all). A reader that needs "did it run" must read `ordering`,
    # not this list; the earlier wording here claimed the opposite and was wrong (review cycle 7,
    # finding 4).
    report.setdefault("ordering_exempt", [])
    # …and so is the BASE, on every path (review cycle 4, finding 1). It was reported only in
    # `--json`, so the CI log a human reads could not tell a verdict measured against `origin/main`
    # from one measured against the local `HEAD` fallback — which IS the PR-internal rule #10463
    # removes. A green line that reads the same either way is the silent-green this field exists to
    # remove, and it must not be visible only to machines. ⛔ The fallback is `(not measured)`, not
    # the resolved candidate: an ordering that raised before reading an epoch measured nothing, and
    # a field named for what was measured may not carry what was merely intended (cycle 7, F2).
    report.setdefault("ordering_base", "(not measured)")
    report["ordering"] = ordering
    failures += check_machine_record(directory, pins)
    failures += check_steps_block_is_pre_scored(directory)
    # #10215: the step set the capstone's gate reads. Checked after the record so both halves are
    # already known to PARSE; a partition failure here is a weakened gate, not a document nit.
    # SCOPED to the bundles the rule governs (`check_step_partition_applies`): `#10468` added a
    # second pre-registered bundle to `--dir` that has no eight-step concept, and demanding one of
    # it reds a correct tree.
    steps_record = _record_for_steps(directory)
    if check_step_partition_applies(directory, pins, steps_record):
        failures += check_step_partition(pins, steps_record)

    if use_corpus:
        corpus_state = "verified"
        try:
            derived = derive_corpus_facts(pins)
        except CheckError:
            raise
        except (OSError, ImportError, ValueError, KeyError, TypeError, AttributeError,
                IndexError) as exc:
            # A corpus change that deletes a fixture, the salience JSONL or the module itself is
            # exactly the event this check exists to catch — it must read as an attributed exit 2,
            # not an unhandled traceback that hides the cause (#9075 review P2). The tuple is wide
            # on purpose: a fixture that parses to a list raises AttributeError/TypeError, and a
            # malformed JSONL row raises TypeError/IndexError — all of them are "cannot read my
            # subject", not an internal defect.
            raise CheckError(
                f"cannot re-derive the corpus pins from the tree: {type(exc).__name__}: {exc}"
            ) from exc
        report["derived"] = {
            "fixtures_hash": derived["fixtures_hash"],
            "planted_units": derived["planted_units"],
            "planted_operator_edges": derived["planted_operator_edges"],
            "value_bearing_units": derived["value_bearing_units"],
            "hedge_scope_sentences": derived["hedge_scope_sentences"],
            "sentences_total": derived["sentences_total"],
            "salience": derived["salience"],
        }
        failures += check_pins(pins, derived, rows)
    else:
        # A skipped half must SAY it was skipped: `--no-corpus` is what the CI ordering step uses
        # (the corpus leg needs the package deps, and the test suite owns it), and a report that
        # reads as fully checked while one half never ran is the silent-green shape this tool
        # exists to refuse (#9075 review r6 P1).
        corpus_state = "UNVERIFIED (--no-corpus)"
    report["corpus"] = corpus_state

    if registry_scopes:
        # A rule this bundle is not asked is a DECLARED skip, printed by `main` and carried in the
        # JSON — the same treatment `--no-corpus` gets, for the same reason (#10525).
        report["registry_scopes"] = registry_scopes

    report["failures"] = failures
    report["ok"] = not failures
    # The machine record's run-order claim is TRUSTED, not derived: the only independent evidence
    # that no scored run exists is the `*.log` scan below, which `--no-git` skips. Say so in the
    # report rather than letting a skip flag silently turn an assertion into a green (#9075 review
    # P2-4). CI and the §7 acceptance command run the full check.
    report["run_order_claim"] = report["ordering"]
    return report


def _finding_discriminator(document: str, failure: str) -> str:
    """A mask-proof discriminator for ONE finding (review cycle 1, #10536).

    ⛔ WHY THIS EXISTS — the fail-open it closes. The rail does not key on the annotation
    text; it NORMALISES it first (`normalize_signature` masks `[0-9a-f]{8,}` as `<HEX>`,
    then `_GUARD_INT_RE` masks every `\\b\\d+\\b` as `<N>`). Written without this
    discriminator, two DIFFERENT ordering violations reduced to ONE identity — measured
    against the rail's own functions:

        s3-ablation-a1075dfc.log added at 1793558646  ->  …s3-ablation-<HEX>.log-added-at-<HEX>-…
        s3-ablation-77d0be11.log added at 1793999999  ->  …s3-ablation-<HEX>.log-added-at-<HEX>-…
        collide: True

    and the bundle directory collided the same way (`5087-hypothesis` -> `<N>-hypothesis`).
    A NEW finding would then have inherited main's red and been exempted by the rail —
    the exact measurement-laundering shape this gate exists to refuse, and a fail-OPEN
    that did not exist before the annotation was added (with no annotation at all the
    rail refused outright, which is why "uncomparable" was the safe failure).

    Base32 because the rail's masks cannot eat it: the alphabet is `A-Z2-7`, so it is
    never a lowercase-hex run, and the `finding-` prefix means an all-digit tail can never
    present as a bare `\\b\\d+\\b` integer run. The digest is over the UNMASKED inputs, so
    the same tree yields the same discriminator on both surfaces, while a different log,
    epoch or bundle yields a different one.
    """
    # `.encode()` with no argument IS UTF-8 (Python 3's default), so the digest is
    # byte-identical to the explicit spelling — and UP012 refuses the redundant
    # argument, which redded `agent-infra-ci / lint` on this very function.
    digest = hashlib.sha256(f"{document}\x00{failure}".encode()).digest()
    return "finding-" + base64.b32encode(digest).decode("ascii").rstrip("=")[:20]


def _rail_identity_annotation(document: str, failure: str) -> str:
    """A one-line ``::error::`` naming ONE finding, for the merge rail (#10536).

    THE DEFECT THIS CLOSES. The failure block below is HUMAN prose — ``FAIL — N
    problem(s):`` — and the merge rail's exemption compares failures by IDENTITY, not by
    check name. A step that prints only prose yields ``ids=0``, so the rail cannot compare
    it against the base and refuses to merge at all.

    MEASURED (not inferred) on run `41353799666`, the `changes` job on `main`:

        $ python3 scripts/ci_exemption.py ids --log <capture>
        ci-exemption: ids=0 unattributable=0 guard-steps=0 non-nodeid=0 ...

    — even though that capture carries the aggregate's own `##[error]leg 'changes'
    reported 'failure' …` lines, because `guard_step_failures` requires the run's ROOT
    failing step to yield an identity and this step printed none. The rail therefore
    refused all 14 ready PRs (`#10536`).

    ⛔ THE VERDICT IS UNCHANGED, and that is the whole point. This adds no exemption,
    waives nothing, and leaves the same-commit refusal exactly as it was (the pinned
    `test_ordering_is_not_satisfied_by_the_same_second` still holds): the tool still
    exits 1 on the same input, for the same reason. It only makes an existing refusal
    COMPARABLE, so the rail's designed "this is already red on `main`" exemption can see
    it. Whether the ordering RULE itself should change is `#10529`'s owner decision;
    making a refusal legible is not.

    Whitespace is collapsed because an annotation is one line and the rail's parser reads
    one line. Digits are deliberately left in place — the rail masks integer runs itself
    (`_GUARD_INT_RE`), so the same finding on the same tree reduces to the same identity on
    both surfaces. That masking is ALSO why the discriminator is appended: numbers alone do
    not survive normalisation, and without it distinct findings collided (see
    :func:`_finding_discriminator`).
    """
    payload = " ".join(f"{document}: {failure}".split())
    # GitHub workflow-command escaping: `%` opens an escape sequence.
    payload = payload.replace("%", "%25")
    return f"::error::capstone preregistration {payload} {_finding_discriminator(document, failure)}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Verify the extractor-v4 capstone pre-registration (#9075)."
    )
    ap.add_argument("--dir", action="append", default=None,
                    help="directory holding PRE-REGISTRATION.md and its evidence logs; REPEAT to "
                         f"check several bundles in one run (default: {DEFAULT_PREREG_DIR})")
    ap.add_argument("--no-git", action="store_true",
                    help="skip the commit-ordering check (pre-commit authoring)")
    ap.add_argument("--no-corpus", action="store_true",
                    help="skip the corpus pin re-derivation")
    ap.add_argument("--json", action="store_true", help="emit the report as JSON")
    ap.add_argument("--allow-atomic-disclosure", action="store_true",
                    help="exercise the PROPOSED atomic-add disclosure rule: a pre-registration and "
                         f"its log that entered in ONE commit PASS when ``{ATOMIC_ADD_KEY}`` in the "
                         "machine record discloses it. OFF by default because the ruling on #10463 "
                         "keeps an atomic pair a REFUSAL (same second is not before - an ordering "
                         "that cannot be shown is not an ordering); the reopen that would arm this "
                         "is requested on #10463 and not granted, so nothing in CI passes it.")
    ap.add_argument("--base-ref", default=None,
                    help="the ref the pre-registration ordering is measured against (default: the "
                         f"first of {', '.join(BASE_REF_CANDIDATES)}; ${BASE_REF_ENV} also sets it)")
    args = ap.parse_args(argv)

    directories = []
    for entry in args.dir or [DEFAULT_PREREG_DIR]:
        directory = Path(entry)
        directories.append(directory if directory.is_absolute() else REPO_ROOT / directory)

    reports: list[dict] = []
    errors: list[tuple[Path, str]] = []
    for directory in directories:
        try:
            reports.append(run(directory, use_git=not args.no_git,
                               use_corpus=not args.no_corpus,
                               base_ref=args.base_ref or os.environ.get(BASE_REF_ENV) or None,
                               allow_atomic_disclosure=args.allow_atomic_disclosure))
        except CheckError as exc:
            errors.append((directory, str(exc)))
    # 2 outranks 1 on purpose: "I could not check this subject" is a different answer from "I
    # checked it and found problems", and a run that cannot read ONE of the bundles it was given
    # must never report the other bundles' green as the verdict.
    exit_code = 2 if errors else (0 if all(r["ok"] for r in reports) else 1)

    if args.json:
        if len(directories) == 1:
            print(json.dumps({"ok": False, "error": errors[0][1]} if errors else reports[0],
                             indent=2, sort_keys=True))
        else:
            # Each error carries its OWN directory (#10468 review: the human path prints
            # `ERROR: {directory}: {message}`, but the JSON emitted only the message — so a
            # consumer of a two-bundle `--json` run could see THAT one failed and not WHICH).
            print(json.dumps({"ok": exit_code == 0, "reports": reports,
                              "errors": [{"directory": str(directory), "error": message}
                                         for directory, message in errors]},
                             indent=2, sort_keys=True))
        return exit_code

    for directory, message in errors:
        print(f"ERROR: {directory}: {message}", file=sys.stderr)
    for report in reports:
        if len(directories) > 1:
            print(f"=== {report['document']} ===")
        print(f"pre-registration: {report['document']}")
        print(f"criteria rows: {', '.join(r['id'] for r in report['rows']) or '(none)'}")
        print(f"evidence logs: {len(report['evidence_logs'])}"
              + (f" ({', '.join(report['evidence_logs'])})" if report["evidence_logs"] else ""))
        if "derived" in report:
            d = report["derived"]
            print(
                "re-derived: units={planted_units} operators={planted_operator_edges} "
                "values={value_bearing_units} markers={hedge_scope_sentences}"
                "/{sentences_total} salience={salience[in_scope]}-in-scope"
                "/{salience[positively_classified]}-classified".format(**d)
            )
        unchecked = []
        # A DECLARED atomic add is "verified" only in the qualified sense the claim line states
        # (#10529 review P1), so the prefix test — not equality — decides whether the ordering half
        # actually ran a comparison and found nothing.
        if not report["ordering"].startswith("verified"):
            unchecked.append(f"ordering NOT checked ({report['ordering']})")
        if report.get("corpus") != "verified":
            unchecked.append(f"corpus pins NOT checked ({report.get('corpus')})")
        # ⛔ A DISCLOSED ordering is not an UNCHECKED half (#10529 review cycle 3, P2). Reusing
        # `unchecked` made a fully checked run print "OK (halves NOT checked)" — the inverse of the
        # failure this tool exists to refuse: a verdict misdescribing what happened. It is its own
        # verdict, and the ATOMIC-ADD block below carries the detail.
        disclosed = report.get("ordering_atomic_add") or []
        disclosure_verdict = (
            "; the ordering HOLDS BY DISCLOSURE, not by comparison (atomic add: "
            + ", ".join(disclosed) + ")" if disclosed else ""
        )
        if report["ok"] and disclosed and not unchecked:
            print("OK — every criteria row is complete, the E4 denominator is 15"
                  f"{disclosure_verdict}")
        elif report["ok"] and not unchecked:
            print("OK — every criteria row is complete, the ordering holds, "
                  "the E4 denominator is 15, and the pins match the tree")
        elif report["ok"]:
            # Never let an unchecked half read as a checked one: the verdict line carries the
            # reason it is missing, and it is the same string the report's claim field carries.
            print("OK (halves NOT checked) — every criteria row is complete and the E4 denominator "
                  f"is 15; {'; '.join(unchecked)}")
        else:
            print(f"FAIL — {len(report['failures'])} problem(s):")
            for f in report["failures"]:
                print(f"  - {f}")
                # The rail compares failures by IDENTITY, not by check name
                # (``scripts/admin-merge.sh``: "make the failing step print a
                # parseable identity — a '::error::<message>' annotation or a
                # 'FAILED <nodeid>' line — then re-run the rail"). A bare
                # ``FAIL — N problem(s)`` block is not parseable, so a red that
                # is main's OWN pre-existing red cannot be exempted and blocks
                # every PR — a false block, not a gate. The annotation does not
                # change the verdict (exit code unchanged); it makes the refusal
                # ATTRIBUTABLE. The identity itself comes from (#10536)'s
                # mask-proof discriminator: one annotation per finding, so a NEW
                # finding cannot inherit a red main's.
                print(_rail_identity_annotation(report["document"], f), file=sys.stderr)
        for note in report.get("registry_scopes", []):
            print(f"registry arm NOT applied — {note}")
        exempt_note = ""
        if report.get("ordering_exempt"):
            exempt_note = (f" (exempt by CONTENT, measured nothing: "
                           f"{', '.join(report['ordering_exempt'])})")
        print(f"machine record run-order claim: {report['run_order_claim']}{exempt_note}")
        # ⛔ A declared atomic add is NEVER silent (#10529): the ordering half did not compare the
        # two epochs, so a green line reading as "the ordering was checked" would be the
        # silent-green shape this tool exists to refuse. It gets its own block, and it names the
        # COMMITS the disclosure points at — the only part a reader can go and check.
        if report.get("ordering_atomic_add"):
            print("ordering: ATOMIC-ADD declared — the pre-registration and "
                  f"{', '.join(report['ordering_atomic_add'])} entered the repository in ONE "
                  "commit (a squash merge of an ordered branch, or a single commit written after "
                  "the numbers — git cannot tell them apart), so no epoch comparison can order "
                  f"them; the disclosure in {MACHINE_RECORD}.{ATOMIC_ADD_KEY} is the record of it:")
            for name, detail in sorted(report.get("ordering_atomic_add_commits", {}).items()):
                # The squash commit is printed FIRST: it is on main, so it is the one pointer a
                # reader can resolve (a squash leaves the branch commits unreachable).
                if detail.get("squash_merge"):
                    print(f"  {name}: squashed by {detail['squash_merge']}")
                for commit in detail.get("branch_commits") or []:
                    print(f"  {name}: {commit}")
                unverified = _unverified_history_note(detail)
                if unverified:
                    print(f"  {name}: {unverified}")
        # The ref the verdict was measured against, PRINTED — not only in `--json` (review cycle 4,
        # finding 1). A verdict against the local `HEAD` fallback is the PR-internal rule this
        # change removes, and the log must not read identically to one against the published base.
        # ⛔ `.get` with the same fallback `run()` sets, not `[...]`: `main()` is also the renderer
        # for a report a TEST stubs (`origin/main`'s annotation fixture builds a report dict, and
        # that key did not exist on the tree the fixture was written against). A renderer that
        # raises KeyError instead of printing its finding is the failure mode this whole block
        # exists to avoid, and the fallback is the CONSERVATIVE text — never the candidate.
        measured_base = report.get("ordering_base", "(not measured)")
        print(f"ordering measured against: {measured_base}")
        # …and when nothing was measured, name the ref that WOULD have been — separately worded, so
        # the measured/unmeasured distinction is unreadable-mistake-proof in the log (cycle 7, F2):
        # "measured against: (not measured)" for the measured line, and the intention on its own
        # line. Without this the candidate is lost and an operator cannot tell a wiring fault from
        # a shallow checkout.
        candidate = report.get("ordering_base_candidate")
        # ⛔ Keyed on whether anything was MEASURED, not on whether the verdict was `verified`
        # (#10548 r15 F3). A violated pair now reports its base — the ref that did the refusing —
        # so keying on the verdict would print "would be measured against" immediately under a
        # line that already says it WAS measured against that ref.
        if (measured_base == "(not measured)"
                and candidate and candidate != UNRESOLVED_BASE):
            print(f"would be measured against: {candidate}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
