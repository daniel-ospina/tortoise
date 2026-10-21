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
   sub-rows, e.g. ``E2a``/``E2b``, and every sub-row must itself be complete).
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
3. **E4's denominator is 15, never 4.** The planted-operator gold is 15 edges across all seven
   sessions; the 4-edge denominator it replaced swung 0–2 of 4 on *identical* code, so a four-edge
   rate is not evidence of anything. The phrase is rejected wherever it appears in the document,
   including in a historical aside — a document that writes the bad denominator down is a document a
   later reader will copy it out of.
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
   reads one while a reviewer reads the other.

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

import argparse
import importlib
import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

PREREG_NAME = "PRE-REGISTRATION.md"
PINS_FENCE = "```yaml prereregistration-pins"
EVIDENCE_LOG_GLOB = "*.log"

#: The machine-readable record beside the document. The evidence probe already names this path on
#: its ``prereg=`` line, so the name is not this check's invention.
MACHINE_RECORD = "preregistration.json"
PREREG_SCHEMA = "capstone-preregistration/v1"

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

#: Every pinned corpus quantity the machine record restates and is cross-checked against. A REQUIRED
#: set: a pins block missing one of these is a failure, never a silently-skipped comparison.
CROSS_CHECKED_CORPUS_KEYS = (
    "fixtures_hash", "sessions", "seed", "schema_version", "planted_units",
    "planted_operator_edges", "value_bearing_units", "hedge_scope_sentences",
    "hedge_scope_sentences_total",
)

#: The rows the epic requires. ``E2`` may be carried by sub-rows (``E2a``/``E2b``); a required id is
#: satisfied when at least one row's id equals it or starts with it.
REQUIRED_CRITERIA = ("E1", "E2", "E3", "E4", "E5")

#: Cell values that mean "decided later", i.e. not decided. Compared case-folded, stripped of
#: markdown emphasis and punctuation.
PLACEHOLDER_CELLS = frozenset(
    {"", "tbd", "tbc", "todo", "to be decided", "to be determined", "n/a", "na", "none",
     "-", "--", "—", "–", "?", "??", "..."}
)

#: A threshold must carry a comparator AND a number. Prose without both is not a threshold.
COMPARATORS = ("<=", ">=", "<", ">", "=", "≤", "≥")

#: The four-edge denominator, in any rate phrasing. ``\\d+ / 4`` — and never ``4 / 40``, hence the
#: trailing guard.
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
    except Exception as exc:  # noqa: BLE001 - any YAML error is a parse error here
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
    operator_edges = 0
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
        operator_edges += per_session_ops[sid]
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

    # An exclusion that does not exist, or that excludes nothing digit-bearing, is a stale pin.
    all_ids = set()
    for sid in session_ids:
        for unit in corpus.load_gold(sid, root).get("planted_units") or []:
            all_ids.add(str(unit.get("id")))
    missing_exclusions = sorted(excluded - all_ids)

    lexemes = list(pins.get("corpus", {}).get("hedge_scope_lexemes") or [])
    pattern = _marker_pattern(lexemes) if lexemes else None
    per_session_markers: dict[str, int] = {}
    sentences_total = 0
    markers_total = 0
    for sid in session_ids:
        fixture = json.loads((root / "fixtures" / f"{sid}.json").read_text())
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


def check_ordering(prereg: Path, logs: list[Path], pins: dict | None = None) -> list[str]:
    """``[]`` when every log is either after the registration or provably unscored.

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
            failures.append(
                f"{log.name}: added at {log_epoch} which is not strictly after the "
                f"pre-registration's add-commit {prereg_epoch} — the log may have been scored "
                "against thresholds chosen after it was read"
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
        record = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
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

    pins_split = _pins_mapping(pins, "split")
    rec_split = _mapping(record, "split", MACHINE_RECORD)
    for half in ("tune", "held_out"):
        cmp(f"split.{half}", sorted(pins_split.get(half) or []), sorted(rec_split.get(half) or []))

    if "e4_denominator" in pins:
        cmp("e4_denominator", pins["e4_denominator"], record.get("e4_denominator"))

    # Each criterion row's threshold in the record must match the table's, character for character.
    rows = parse_criteria_rows((directory / PREREG_NAME).read_text())
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

    for match in FOUR_EDGE_RATE.finditer(path.read_text()):
        failures.append(
            f"{MACHINE_RECORD}: four-edge denominator rate {match.group(0)!r} — the record is "
            "frozen by the run probe, so the forbidden denominator may not appear here either"
        )

    exempt = sorted(_pins_mapping(pins, "pre_existing_unscored_logs"))
    if sorted(_list(record, "unscored_runs_before_this_registration", MACHINE_RECORD)) != exempt:
        failures.append(
            f"{MACHINE_RECORD} unscored_runs_before_this_registration is "
            f"{record.get('unscored_runs_before_this_registration')!r} but the pins exempt {exempt!r}"
        )
    return failures


# ── the checks ──────────────────────────────────────────────────────────────


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
        if not any(r["id"] == required or r["id"].startswith(required) for r in rows):
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
    text = prereg.read_text()
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

    logs = sorted(directory.glob(EVIDENCE_LOG_GLOB))
    report["evidence_logs"] = [p.name for p in logs]
    if use_git:
        try:
            failures += check_ordering(prereg, logs, pins)
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
    ap.add_argument("--dir", default="docs/evidence/7949-capstone",
                    help="directory holding PRE-REGISTRATION.md and its evidence logs")
    ap.add_argument("--no-git", action="store_true",
                    help="skip the commit-ordering check (pre-commit authoring)")
    ap.add_argument("--no-corpus", action="store_true",
                    help="skip the corpus pin re-derivation")
    ap.add_argument("--json", action="store_true", help="emit the report as JSON")
    args = ap.parse_args(argv)

    directory = Path(args.dir)
    if not directory.is_absolute():
        directory = REPO_ROOT / directory
    try:
        report = run(directory, use_git=not args.no_git, use_corpus=not args.no_corpus)
    except CheckError as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}))
        else:
            print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
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
        if report["ok"] and report["ordering"] == "verified":
            print("OK — every criteria row is complete, the ordering holds, "
                  "the E4 denominator is 15, and the pins match the tree")
        elif report["ok"]:
            # Never let an unchecked ordering read as a checked one: the verdict line carries the
            # reason it is missing, and it is the same string the report's claim field carries.
            print("OK (ordering NOT checked) — every criteria row is complete, the E4 denominator "
                  f"is 15, and the pins match the tree; the ordering half is {report['ordering']}")
        else:
            print(f"FAIL — {len(report['failures'])} problem(s):")
            for f in report["failures"]:
                print(f"  - {f}")
        print(f"machine record run-order claim: {report['run_order_claim']}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
