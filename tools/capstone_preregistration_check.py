#!/usr/bin/env python3
"""The capstone pre-registration gate (#9075) — the check behind ``#7949``'s method constraint.

The epic states the constraint verbatim: *"Pre-register before running — corpus, split, scoring —
the discipline that made `#3011` usable."* A registration that is a document only is a document that
can be edited after the numbers land, and a threshold chosen after the run is the exact failure the
constraint exists to prevent. This tool is what makes the artefact executable.

It verifies six things, and it fails on each of them for a specific reason:

1. **Every criteria row carries a metric, a denominator AND a numeric threshold.** A row with a
   placeholder (*"to be decided"*, *"TBD"*, an em dash) is a row that will be decided later — which is
   the same failure as no registration at all. E1–E5 must each be present (E2 may be split into
   sub-rows, e.g. ``E2a``/``E2b``, and every sub-row must itself be complete). Only E2 takes
   sub-rows: ``E1b`` is E1's anti-gaming CONTROL, so a table carrying ``E1b`` without an ``E1`` row
   is the epic's central criterion deleted behind its guard (#9075 review r3 P2).
2. **The registration strictly precedes every SCORED evidence log.** The commit that *adds*
   ``PRE-REGISTRATION.md`` must be **older** than the commit that adds each ``*.log`` beside it.
   "Same second" is not "before" — an ordering that cannot be shown is not an ordering.

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
import hashlib
import importlib
import json
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
CROSS_CHECKED_CORPUS_KEYS = (
    "fixtures_hash", "sessions", "seed", "schema_version", "planted_units",
    "planted_operator_edges", "value_bearing_units", "hedge_scope_sentences",
    "hedge_scope_sentences_total",
)

#: The rows the epic requires. ``E2`` may be carried by sub-rows (``E2a``/``E2b``); the other four
#: must be matched EXACTLY. A prefix match for every id let ``E1b`` (E1's anti-gaming control) stand
#: in for a deleted ``E1`` — the epic's central criterion gone while its guard kept the check green
#: (#9075 review r3 P2).
REQUIRED_CRITERIA = ("E1", "E2", "E3", "E4", "E5")

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


def _added_epochs(path: Path) -> list[int]:
    """Commit epochs at which ``path`` was ADDED, oldest-first. Empty when never added."""
    try:
        rel = path.resolve().relative_to(REPO_ROOT)
    except ValueError:
        return []
    try:
        out = subprocess.run(
            ["git", "log", "--diff-filter=A", "--format=%ct", "--", str(rel)],
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
       unanchored class.

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

    e3 = [r for r in rows if r["id"] == "E3"]
    if not e3:
        # ⛔ SCOPED ON THE RIGHT SUBJECT (#10525 review round 2 P3, corrected round 3 P3 — MEASURED).
        # The existence failure was unconditional, then gated on `declares_visibility` — a field of
        # the RECORD's #9738 schema — while its subject is the DOCUMENT's E3 row. That reproduced the
        # same false red one level down: a record that adopts the visibility schema but raises no E3
        # criterion was refused for a criterion it never registered. The subject is a DISAGREEMENT
        # between the two artifacts, so it fails only when the DOCUMENT registers E3 and the record
        # does not; a bundle that registers neither is DECLARED skipped (an undeclared skip is the
        # silent-green shape this tool refuses, and a failure on a subject the bundle never raised is
        # its mirror).
        document_registers_e3 = any(r["id"] == "E3" for r in rows)
        if document_registers_e3:
            failures.append(
                "no E3 row — the DOCUMENT registers an E3 criterion but the machine record carries "
                "none, so the class set it fixes cannot be compared with the model"
            )
        else:
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


def _note(scopes: list[str] | None, message: str) -> None:
    """Declare an arm this bundle is NOT asked — never a silent skip (see the caller's docstring)."""
    if scopes is not None:
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


def check_ordering(prereg: Path, logs: list[Path], pins: dict | None = None,
                   exempted: list[str] | None = None) -> list[str]:
    """``[]`` when every log is either after the registration or provably unscored.

    ``exempted``, when given, is APPENDED with the name of every log the content arm exempted
    (#9089). It is not decoration: an ordering violation that was WAIVED must be visible, or the
    green line reads exactly like a log that was ordered — the silent-green shape this tool exists
    to refuse (see the ``--no-git``/``--no-corpus`` rule ninety lines below).

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

    if history_unavailable():
        raise HistoryUnavailable(HISTORY_UNAVAILABLE, failures)

    prereg_epochs = _added_epochs(prereg)
    if not prereg_epochs:
        if not logs:
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
    prereg_epoch = min(prereg_epochs)
    for log in logs:
        log_epochs = _added_epochs(log)
        if not log_epochs:
            if _is_tracked(log):
                failures.append(
                    f"{log.name}: tracked but with no add-commit — ordering unprovable"
                )
            continue  # untracked log: it cannot predate a commit that already exists
        log_epoch = min(log_epochs)
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
            latest = max(touches)
            if latest >= prereg_epoch:
                failures.append(
                    f"{log.name}: exempted, but it was MODIFIED at {latest}, which is not "
                    f"strictly before the pre-registration's add-commit {prereg_epoch} — an "
                    "in-place edit can put a measurement number into a grandfathered log after "
                    "the thresholds were written, and the add-commit cannot see that"
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
            failures.append(
                f"{log.name}: added at {log_epoch} which is not strictly after the "
                f"pre-registration's add-commit {prereg_epoch} — the log may have been scored "
                f"against thresholds chosen after it was read, and it does not prove it measured "
                f"nothing ({why})"
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


def check_completeness(rows: list[dict[str, str]]) -> list[str]:
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
    for required in REQUIRED_CRITERIA:
        if required in SUB_ROW_CRITERIA:
            present = any(
                r["id"] == required or r["id"].startswith(required) for r in rows
            )
        else:
            # EXACT: ``E1b`` is E1's control, not a stand-in for E1 (#9075 review r3 P2).
            present = any(r["id"] == required for r in rows)
        if not present:
            failures.append(f"criterion {required} has no row")
    for row in rows:
        if is_placeholder(row["threshold"]):
            continue  # already reported above
        plain = _plain(row["threshold"])
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


def check_pins(pins: dict, derived: dict) -> list[str]:
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

    rows = pins.get("required_criteria_rows")
    if list(rows or []) != list(REQUIRED_CRITERIA):
        failures.append(
            f"pins.required_criteria_rows is {rows!r}, must be {list(REQUIRED_CRITERIA)}"
        )
    return failures


# ── entry point ─────────────────────────────────────────────────────────────


def run(directory: Path, use_git: bool = True, use_corpus: bool = True) -> dict:
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
    failures += check_completeness(rows)
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
            failures += check_ordering(prereg, logs, pins, content_exempt)
            report["ordering_exempt"] = sorted(content_exempt)
            ordering = "verified"
        except HistoryUnavailable as exc:
            # The THIRD outcome: neither a pass nor an ordering failure. Folding it into either is
            # what reds a correctly ordered tree in every CI test job (#9075 r4 P1). The git-FREE
            # findings it carries are kept — an unprovable history must not clear a laundering
            # finding that needed no history to prove.
            failures += exc.failures
            ordering = HISTORY_UNAVAILABLE
    else:
        ordering = "UNVERIFIED (--no-git)"
    report["ordering"] = ordering
    failures += check_machine_record(directory, pins)

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
        failures += check_pins(pins, derived)
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
                               use_corpus=not args.no_corpus))
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
        if report["ordering"] != "verified":
            unchecked.append(f"ordering NOT checked ({report['ordering']})")
        if report.get("corpus") != "verified":
            unchecked.append(f"corpus pins NOT checked ({report.get('corpus')})")
        if report["ok"] and not unchecked:
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
        for note in report.get("registry_scopes", []):
            print(f"registry arm NOT applied — {note}")
        exempt_note = ""
        if report.get("ordering_exempt"):
            exempt_note = (f" (exempt by CONTENT, measured nothing: "
                           f"{', '.join(report['ordering_exempt'])})")
        print(f"machine record run-order claim: {report['run_order_claim']}{exempt_note}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
