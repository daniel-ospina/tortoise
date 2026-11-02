#!/usr/bin/env python3
"""The capstone pre-registration gate (#9075) — the check behind ``#7949``'s method constraint.

The epic states the constraint verbatim: *"Pre-register before running — corpus, split, scoring —
the discipline that made `#3011` usable."* A registration that is a document only is a document that
can be edited after the numbers land, and a threshold chosen after the run is the exact failure the
constraint exists to prevent. This tool is what makes the artefact executable.

It verifies five things, and it fails on each of them for a specific reason:

1. **Every criteria row carries a metric, a denominator AND a numeric threshold.** A row with a
   placeholder (*"to be decided"*, *"TBD"*, an em dash) is a row that will be decided later — which is
   the same failure as no registration at all. E1–E5 must each be present (E2 may be split into
   sub-rows, e.g. ``E2a``/``E2b``, and every sub-row must itself be complete). Only E2 takes
   sub-rows: ``E1b`` is E1's anti-gaming CONTROL, so a table carrying ``E1b`` without an ``E1`` row
   is the epic's central criterion deleted behind its guard (#9075 review r3 P2).
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

   **The refusal this buys, at PR time rather than after the merge.** A change that adds a
   registration *and* a log has nothing on the base to appeal to, so it is **REFUSED**: the
   registration must land first, in its own PR. That is the arm that stops a contribution instead
   of reddening `main` afterwards, and it is tested (`test_a_registration_added_by_the_same_change
   _is_refused`). A checkout with a remote but no resolvable base fails closed (`UNPROVABLE`)
   rather than silently degrading to the PR-internal rule this change removed.

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

#: The bundle checked when nothing else is named. Prefer ``--all-bundles``: a DEFAULT (or a
#: hand-maintained ``--dir`` list) can only name bundles that already exist, so it is a population
#: that silently excludes the next one.
DEFAULT_PREREG_DIR = "docs/evidence/7949-capstone"

#: Every registered bundle in the tree — the population, discovered rather than listed.
#:
#: ⛔ WHY DISCOVERY AND NOT A LIST (#10547 review P1). CI used to hand this tool two hardcoded
#: ``--dir``s. Two consequences, both measured: (a) no CI job read #10463's ablation bundle at all
#: until a second ``--dir`` was added by hand, so its ordering rule never executed anywhere
#: (#10468 r6 F1); and (b) the arm that refuses a registration added BESIDE its own log
#: (:data:`SAME_PR_REFUSAL`) can only fire for a bundle someone hands over — with a list, a
#: contribution that adds a new ``docs/evidence/<bundle>/`` is not refused, it is never READ. That
#: arm exists precisely to stop such a contribution at PR time, so it must be reached by an
#: automated path or it is decoration. Enumerating the tree is the only form that cannot go stale.
BUNDLE_GLOB = "docs/evidence/*/PRE-REGISTRATION.md"

NO_BUNDLES = (
    f"no bundles discovered under docs/evidence/ ({BUNDLE_GLOB}): --all-bundles was asked to check "
    "every registered bundle and found NONE, so this run consulted no subject at all. An empty "
    "discovery is a check that could not be performed, never a pass"
)

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

    ``fatal`` marks the reasons that have NO fallback, as opposed to the shallow-clone reason: a
    truncated history is a state every CI test job is in and the caller deliberately reports it as a
    declared-green third outcome (#9075 r4 P1), whereas a base ref that cannot be resolved has no
    such excuse — degrading to the branch's own history is exactly the PR-internal rule #10463
    removes, so that answer is "I could not check this subject" (exit 2) and must never be folded
    into a pass (#10547 review P1: folded in, the arm was INERT — exit 0 with the wrong reason).
    """

    def __init__(self, message: str, failures: list[str] | None = None, *,
                 fatal: bool = False) -> None:
        super().__init__(message)
        self.failures = list(failures or [])
        self.fatal = fatal


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

#: The ref the ordering is measured against. `origin/main` first: that is what a contribution is
#: merged into. `PREREG_ORDERING_BASE_REF` (or `--base-ref`) overrides it.
BASE_REF_ENV = "PREREG_ORDERING_BASE_REF"
BASE_REF_CANDIDATES = ("origin/main", "main")

NO_BASE_REF = (
    "UNPROVABLE — no base ref: this checkout has a remote but neither "
    f"{' nor '.join(BASE_REF_CANDIDATES)} to measure the ordering against, and a provisional base "
    "of HEAD would answer a question this checkout cannot answer (the ordering claim is against "
    "the PUBLISHED base, not against this branch's own history). This is a REFUSED check, not a "
    "green one: fetch the base (git fetch origin main) or name it with --base-ref"
)

#: The other two states that resolve to "no base ref", kept DISTINCT because their remedies are
#: different and the diagnostic above would be a false statement in both (#10547 review 3 P2):
#: that message asserts "this checkout has a remote", which is not established when `git remote`
#: itself failed, and it is false in a directory that is not a checkout at all.
NO_GIT_CHECKOUT = (
    "UNPROVABLE — not a git checkout: this directory is not inside a git working tree, so no "
    "history can be read here and the pre-registration ordering cannot be measured at all. Run the "
    "check from a clone, or pass --no-git when the ordering is deliberately out of scope"
)
NO_GIT_ANSWER = (
    "UNPROVABLE — git could not report this checkout's remotes (exit {code}), so no base ref can "
    "be resolved and the ordering claim — which is against the PUBLISHED base — cannot be checked"
)

SAME_PR_REFUSAL = (
    "{prereg} is not on the merge base ({base}): this change adds {n} evidence log(s) against a "
    "pre-registration that is NOT yet published there. A pre-registration must land FIRST, in its "
    "own PR — the ordering claim is that the registration was already on the base before the "
    "evidence log's add-commit, and a registration added by the SAME PR as its log has no such "
    "claim to make: a squash merge collapses the two into one commit, so the ordering cannot be "
    "shown from the merged history at all. Land the registration alone, then add the log in a "
    "follow-up."
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

    A checkout with NO remote is a local-only history (the shape the unit tests build in a temp
    repo): there is no published base and no merge to be invariant against, so ``HEAD`` is the only
    history there is and using it is not a weakening — it is the same predicate over the whole
    available history. A checkout WITH a remote but without a resolvable base is a different state:
    the answer would silently degrade to the PR-internal rule this change exists to remove, so it
    fails closed with :data:`NO_BASE_REF` instead.
    """
    for candidate in ((explicit,) if explicit else ()) + BASE_REF_CANDIDATES:
        code, out = _git_stdout(["rev-parse", "--verify", "--quiet", candidate])
        if code == 0 and out.strip():
            return candidate
    code, out = _git_stdout(["remote"])
    if code == 0 and not out.strip():
        return "HEAD"
    return None


def no_base_ref_reason() -> str:
    """WHY :func:`resolve_base_ref` answered ``None`` — three states, three remedies.

    ``resolve_base_ref`` collapses them to one ``None``, which was fine while the reason was never
    printed. Now that the refusal is user-visible, one message for all three is a false statement in
    two of them: "this checkout has a remote" is not established when ``git remote`` returned
    non-zero, and it is false in a directory that is not a checkout at all — where the remedy it
    names (``git fetch origin main``) cannot succeed either (#10547 review 3 P2).
    """
    code, out = _git_stdout(["rev-parse", "--is-inside-work-tree"])
    if code != 0 or out.strip() != "true":
        return NO_GIT_CHECKOUT
    code, out = _git_stdout(["remote"])
    if code != 0:
        return NO_GIT_ANSWER.format(code=code)
    return NO_BASE_REF


def _added_epochs_on(ref: str, path: Path) -> list[int]:
    """Commit epochs at which ``path`` was ADDED on ``ref``'s side, oldest-first.

    Reachability is bounded by ``ref`` (``git log <ref> -- <path>``), so this reads only commits
    that are ALREADY on that ref — the set a squash merge cannot rewrite. Empty when the path was
    never added there, which is the whole signal for "is the registration published on the base".
    """
    try:
        rel = path.resolve().relative_to(REPO_ROOT)
    except ValueError:
        return []
    code, out = _git_stdout(["log", "--diff-filter=A", "--format=%ct", ref, "--", str(rel)])
    if code != 0:
        return []
    return sorted(int(x) for x in out.split() if x.strip().isdigit())


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
                   exempted: list[str] | None = None,
                   base_ref: str | None = None) -> list[str]:
    """``[]`` when every log is either after the registration ON THE BASE or provably unscored.

    **The claim, stated so it can be argued with (#10463):** for every evidence log, the
    pre-registration's earliest add-commit *reachable from the base ref* must strictly precede the
    log's add-commit — where the log's own add-commit is read on the base when the log is already
    merged there, and in this contribution when it is not. Same second is still not before: an
    ordering that cannot be shown is not an ordering. What changed is WHICH history the ordering is
    read out of — published commits, never the contribution's own siblings, because a squash merge
    rewrites the latter into one (that is the #10468 red: the same tree passed its PR checks and
    failed on `main` minutes later).

    ``exempted``, when given, is APPENDED with the name of every log the content arm exempted
    (#9089). It is not decoration: an ordering violation that was WAIVED must be visible, or the
    green line reads exactly like a log that was ordered — the silent-green shape this tool exists
    to refuse (see the ``--no-git``/``--no-corpus`` rule ninety lines below).

    Raises ``HistoryUnavailable`` (a ``CheckError``) when this checkout is shallow, or when it has
    a remote but no resolvable base ref: with neither, the comparison cannot be evaluated at all,
    and a wrong verdict is worse than no verdict.
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

    base = resolve_base_ref(base_ref)
    if base is None:
        raise HistoryUnavailable(no_base_ref_reason(), failures, fatal=True)

    # The registration's appearance ON THE BASE — the only side a squash merge cannot rewrite.
    prereg_epochs = _added_epochs_on(base, prereg)
    if not prereg_epochs:
        if not logs:
            return failures  # no registration on base, and nothing to be ordered against
        # ⛔ Three DISTINCT refusals, deliberately not one: "you never committed it", "it has no
        # add-commit to compare", and "it is in THIS change but not on the base" are three
        # different remedies, and a single message would send a contributor to whichever the
        # message happened to name. The last one is the arm #10463 asks for.
        if not _is_tracked(prereg):
            failures.append(
                f"{PREREG_NAME} is not committed, but {len(logs)} evidence log(s) exist — "
                "the ordering cannot be proved, and an unprovable ordering is not an ordering"
            )
            return failures
        if not _added_epochs(prereg):
            failures.append(
                f"{PREREG_NAME} has no add-commit (git log --diff-filter=A found nothing) — "
                "cannot establish that it precedes the evidence logs"
            )
            return failures
        failures.append(
            SAME_PR_REFUSAL.format(prereg=PREREG_NAME, base=base, n=len(logs))
        )
        return failures
    prereg_epoch = min(prereg_epochs)
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
            failures.append(
                f"{log.name}: added at {log_epoch} which is not strictly after the "
                f"pre-registration's appearance on {base} at {prereg_epoch} — the log may have "
                "been scored against thresholds chosen after it was read, and it does not prove "
                "it measured nothing "
                f"({why})"
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


def discover_bundles(root: Path | None = None) -> list[Path]:
    """Every bundle directory carrying a ``PRE-REGISTRATION.md``, sorted — the whole population.

    A bundle is *defined by* the document, so a bundle that is missing its ``preregistration.json``
    is still discovered (and then REFUSED by the record check) rather than skipped: filtering on
    the record would turn a missing record into an invisible bundle, which is the same silence this
    discovery exists to remove.
    """
    base = Path(root) if root is not None else REPO_ROOT
    return sorted({path.parent for path in base.glob(BUNDLE_GLOB)})


def run(directory: Path, use_git: bool = True, use_corpus: bool = True,
        base_ref: str | None = None) -> dict:
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
    #: Set when the ordering half refuses with a reason that has NO fallback. It is raised at the
    #: END of this function, not at the catch site: `check_machine_record` and the corpus half run
    #: AFTER the ordering half and are both history-FREE, so raising early forfeited their findings
    #: — the #9075 r6 P2 invariant, one step further out (#10547 review 3 P1, measured: a stale
    #: `document_sha256` in the record was discarded by exactly this).
    fatal_exc: HistoryUnavailable | None = None

    logs = sorted(directory.glob(EVIDENCE_LOG_GLOB))
    report["evidence_logs"] = [p.name for p in logs]
    report["scored_logs"] = [p.name for p in sorted(directory.glob(SCORED_LOG_GLOB))]
    failures += check_single_measured_log(directory)
    if use_git:
        try:
            content_exempt: list[str] = []
            # The base the ordering was measured against is REPORTED, not implied: a verdict whose
            # subject ref is invisible reads the same whether it measured `origin/main` or this
            # branch's own HEAD, and that difference is the whole point of #10463.
            report["ordering_base"] = resolve_base_ref(base_ref) or "(unresolved)"
            failures += check_ordering(prereg, logs, pins, content_exempt, base_ref=base_ref)
            report["ordering_exempt"] = sorted(content_exempt)
            ordering = "verified"
        except HistoryUnavailable as exc:
            # A reason with NO fallback is not the third outcome — it is a check that could not be
            # performed, which `main` must report as exit 2. Folding it in (as every
            # HistoryUnavailable used to be) made NO_BASE_REF INERT: the CLI exited 0 and printed
            # the shallow-clone reason for a checkout that is not shallow (#10547 review P1).
            #
            # It is NOT raised HERE: the halves below are history-free, so the refusal is deferred
            # to the end of the function and carries every finding they produce. The THIRD outcome
            # (the shallow-clone reason, `fatal=False`) is unaffected: folding THAT into a failure
            # or a pass is what reds a correctly ordered tree in every CI test job (#9075 r4 P1).
            fatal_exc = exc if exc.fatal else None
            failures += exc.failures
            # The REASON is the exception's own message, never a constant: a hardcoded
            # HISTORY_UNAVAILABLE here reported "shallow clone" for every unprovable checkout.
            ordering = str(exc)
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

    report["failures"] = failures
    report["ok"] = not failures
    # The machine record's run-order claim is TRUSTED, not derived: the only independent evidence
    # that no scored run exists is the `*.log` scan below, which `--no-git` skips. Say so in the
    # report rather than letting a skip flag silently turn an assertion into a green (#9075 review
    # P2-4). CI and the §7 acceptance command run the full check.
    report["run_order_claim"] = report["ordering"]

    if fatal_exc is not None:
        # Deferred to here on purpose: everything above except the git ordering half is history-free,
        # so `failures` now holds the completeness, E4-denominator, single-measured-log, pins-exempt,
        # laundering, machine-record and corpus findings. An exit 2 that named only the base ref
        # would clear all of them by omission (#9075 r6 P2 / #10547 review 3 P1).
        if not failures:
            raise fatal_exc
        raise CheckError(
            str(fatal_exc)
            + "\nALREADY FOUND by the checks that need no history (the base ref does not affect "
              "them, and they still stand):"
            + "".join(f"\n  - {finding}" for finding in failures)
        ) from fatal_exc

    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Verify the extractor-v4 capstone pre-registration (#9075)."
    )
    ap.add_argument("--dir", action="append", default=None,
                    help="directory holding PRE-REGISTRATION.md and its evidence logs; REPEAT to "
                         f"check several bundles in one run (default: {DEFAULT_PREREG_DIR})")
    ap.add_argument("--all-bundles", action="store_true",
                    help="check EVERY registered bundle in the tree "
                         f"({BUNDLE_GLOB}) — the population, discovered, so a bundle a change ADDS "
                         "is read rather than skipped; combines with --dir (union)")
    ap.add_argument("--no-git", action="store_true",
                    help="skip the commit-ordering check (pre-commit authoring)")
    ap.add_argument("--no-corpus", action="store_true",
                    help="skip the corpus pin re-derivation")
    ap.add_argument("--json", action="store_true", help="emit the report as JSON")
    ap.add_argument("--base-ref", default=None,
                    help="the ref the pre-registration ordering is measured against (default: the "
                         f"first of {', '.join(BASE_REF_CANDIDATES)}; ${BASE_REF_ENV} also sets it)")
    args = ap.parse_args(argv)

    discovered: list[Path] = []
    if args.all_bundles:
        discovered = discover_bundles()
        if not discovered:
            # Fail closed: "I found nothing to check" must never leave as exit 0.
            print(f"ERROR: {NO_BUNDLES}", file=sys.stderr)
            return 2

    directories: list[Path] = []
    for entry in args.dir or ([] if args.all_bundles else [DEFAULT_PREREG_DIR]):
        directory = Path(entry)
        directories.append(directory if directory.is_absolute() else REPO_ROOT / directory)
    # Union, not replacement: an explicit `--dir` and the discovered population checked together
    # would otherwise be two runs, and a bundle named by hand twice must not be reported twice.
    named = {directory.resolve() for directory in directories}
    for directory in discovered:
        if directory.resolve() not in named:
            directories.append(directory)
            named.add(directory.resolve())

    reports: list[dict] = []
    errors: list[tuple[Path, str]] = []
    for directory in directories:
        try:
            reports.append(run(directory, use_git=not args.no_git,
                               use_corpus=not args.no_corpus,
                               base_ref=args.base_ref or os.environ.get(BASE_REF_ENV) or None))
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
    if args.all_bundles:
        # The population is DECLARED, not implied: a verdict that names the ref it measured
        # (:data:`BASE_REF_CANDIDATES`) still reads the same whether it consulted one bundle or
        # every bundle, and "which bundles were read" is the question a green line cannot answer.
        print(f"bundles discovered: {len(discovered)} "
              f"({', '.join(str(d) for d in discovered)})")
    for report in reports:
        if len(directories) > 1:
            print(f"=== {report['document']} ===")
        print(f"pre-registration: {report['document']}")
        print(f"criteria rows: {', '.join(r['id'] for r in report['rows']) or '(none)'}")
        print(f"evidence logs: {len(report['evidence_logs'])}"
              + (f" ({', '.join(report['evidence_logs'])})" if report["evidence_logs"] else ""))
        if "ordering_base" in report:
            print(f"ordering measured against: {report['ordering_base']}")
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
        exempt_note = ""
        if report.get("ordering_exempt"):
            exempt_note = (f" (exempt by CONTENT, measured nothing: "
                           f"{', '.join(report['ordering_exempt'])})")
        print(f"machine record run-order claim: {report['run_order_claim']}{exempt_note}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
