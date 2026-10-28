#!/usr/bin/env python3
"""The extractor-v4 capstone's closing gate (#9080 / epic #7949).

The epic states its own closing rule verbatim: *"Close this epic only when
E1–E5 all have evidence attached to this issue — not when the children close.
A child closing is a claim; this epic is where the claim is checked against the
pipeline running end to end."* A rule in prose is not a gate: nothing fails
when a row is marked green on a closed child, on an evidence file that does not
exist, or on a denominator inherited from a different corpus. This tool is the
mechanical form of that rule, and it is the second half of the issue's own
acceptance command:

    python docs/evidence/7949-capstone/probes/capstone_run.py \\
        --prereg <frozen pre-registration commit> --corpus <pre-registered corpus> \\
        --out docs/evidence/7949-capstone/capstone-<sha>.log
    python tools/capstone_verdict_check.py docs/evidence/7949-capstone/capstone-<sha>.log

(A scored run is NEVER written over ``capstone.log``: that shipped file is the
pre-landing REFUSAL record, and its add-commit predates the pre-registration —
see ``docs/evidence/7949-capstone/README.md``.)

The runner EMITS the verdict table (``capstone-verdict-v1``, between the two
marker lines); this tool CHECKS it. Splitting them is the point: the run is
expensive and privileged (a real LLM + a real graph), the check is pure and
runs in CI, so a hand-edited table cannot pass without the artifacts behind it.

⚠️ What this tool can and cannot tell you. It CAN establish that every row
carries an artifact that exists, a sha whose FORM is a commit sha (and, with
``--require-resolvable``, one this checkout still carries), a denominator that
agrees with its sibling, and a named blocker when it is not green. It CANNOT
establish that the measurement itself was performed honestly — a fabricated
evidence file with a plausible sha passes. That limit is stated rather than
hidden because the epic's failure mode is *inference*, not forgery: five rows
marked green off closed children, with no artifact at all. This closes that.

## The rule set (each finding carries a stable code)

    V1  root schema is ``capstone-verdict-v1``
    V2  exactly five rows, ids E1..E5, one each — no missing, no duplicate
    V3  per-row required fields; verdict/evidence_kind in vocabulary; sha SHAPE
        (always fatal, every row, whatever its verdict). Resolvability is
        REPORTED and fails only under ``--require-resolvable`` — see V11
    V4  every row's evidence path exists (repo-relative, no escape)
    V5  GREEN requires: evidence_kind=measurement, 0 < numerator <= denominator,
        blocked_by null, a FROZEN pre-registration sha, and run.status=COMPLETED
        with ALL eight steps ACCOUNTED FOR in one pass — the seven UNCONDITIONAL
        steps observed, plus every CONDITIONAL step (as the frozen
        pre-registration declares them, #10215) either observed or witnessed
        IDLE with its basis stated. A conditional step may not be dropped by
        omission, and a log may not declare a step conditional on its own: the
        declaration is read from the record at the pre-registration commit, so
        the only way a step becomes conditional is a reviewed edit to a
        hash-pinned artefact. Its ancestry half (evidence
        does not predate the pre-registration) is answerable only where the
        measured commit is still reachable — see V11 for why that is reported
        rather than fatal by default.
        ⛔ THE ANCESTRY INVARIANT HAS ONE OWNER: the commit-level form of it
        ("the registration's add-commit strictly precedes every SCORED evidence
        log") belongs to `tools/capstone_preregistration_check.py::check_ordering`,
        which `changes` runs on every PR. V5's half is the ROW-level reading of
        the same rule (a GREEN row's cited sha must not predate the frozen
        registration), and it fires only for GREEN rows — which is why a bundle
        can pass here and still red there (#9089 review P2). Do not grow a third
        copy of this rule: change `check_ordering` and let this row reference it.
    V6  non-green requires a named blocker (``#NNN``) — *name the blocking issue*;
        a ``refusal`` row must cite a capstone log that carries that row
    V7  a measurement row's ``corpus_sha256`` equals the log's corpus sha —
        "every green row cites an evidence file whose before/after denominators
        are the same corpus" — and the LOG must name a corpus to compare with.
        A NON-GREEN row may cite an artifact measured on a DIFFERENT corpus
        revision (E1/E2 cite the #9076 run, E4 cites an operator receipt whose
        ``corpus_hash`` is not the frozen ``fixtures_hash``); it must then
        DECLARE that revision in ``artifact_corpus_sha256``, the declaration must
        equal the row's own ``corpus_sha256``, and the digest must appear IN the
        cited artifact's text — so the claim is checked against the artifact
        instead of trusted. GREEN never takes this path (V7's first arm): a green
        row on another corpus is exactly what this rule exists to refuse.
    V8  a row naming a ``sibling`` must match that sibling's numerator AND
        denominator — the denominators are carried, not restated
    V9  --require-green (the EPIC's closing rule): 5/5 GREEN
    V10 a MEASURED E4 whose denominator is 4 fails the criterion on the row's
        own rule, and E4's row must carry the SAME denominator the run's
        ``e4_receipt`` computed. A MEASURED E4 must also carry a POSITIVE INT
        denominator — an empty denominator measures nothing, and V5's GREEN arm
        cannot reach this row (E4 is never GREEN on this surface)
    V11 (``--require-resolvable``) the provenance POINTERS resolve in this
        checkout — each row's sha AND the pre-registration commit. OFF by
        default: a squash-merged branch sha is unresolvable in ``main`` by
        design, so a default finding would red a shipped bundle after merge

Exit codes: 0 = clean, 1 = findings, 2 = usage / unreadable log. Without
``--require-green`` an honest all-blocked table is CLEAN (exit 0): the epic is
not closeable, but the table is not lying, and that is the distinction the
verdict vocabulary exists to make.
"""
from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below — a module-level
# 3.11+-only import (`from datetime import UTC`) would fail first (D9 shape).
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/capstone_verdict_check.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/capstone_verdict_check.py`"
    )

import argparse
import json
import re
import subprocess
from pathlib import Path

SCHEMA = "capstone-verdict-v1"
CRITERIA = ("E1", "E2", "E3", "E4", "E5")
STEPS = ("S0", "S1", "S2.1", "S2.2", "S2.3", "S3", "S5", "S6")
#: The two states a CONDITIONAL step may be accounted in (#10215). ``observed``
#: means the pass did work; ``idle`` means its flag was ON, it did no work, and
#: its fail-open path was NOT taken — the pass had nothing to do, which is a
#: property of the candidate set (``_collect_classify_items``), not a defect.
STEP_STATES = ("observed", "idle")
VERDICTS = ("GREEN", "RED", "BLOCKED", "UNPREREGISTERED")
EVIDENCE_KINDS = ("measurement", "refusal")

BEGIN = "=== CAPSTONE-VERDICT-BEGIN ==="
END = "=== CAPSTONE-VERDICT-END ==="

_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")
_BLOCKER_RE = re.compile(r"^#\d+$")

#: The nested blocks ``check()`` reads as OBJECTS. Declared so the type guard is
#: derived from one list rather than repeated per call site.
_BLOCK_KEYS = ("run", "prereg", "corpus")

#: The scalar types a row name may have. A ``criterion`` / ``sibling`` that is a
#: LIST or DICT is UNHASHABLE, so using it as a set member or dict key raises
#: TypeError out of ``check()`` — an escape that ``main()`` has no handler for,
#: so it exits 1, the code for "the table has findings" (a verdict on the
#: EVIDENCE) when the artifact was merely malformed. Declared once so the guard
#: cannot be remembered on three sites of the same shape and forgotten on a
#: fourth (#9089 review).
HASHABLE_SCALARS = (str, int, float, bool, type(None))


def _count(value: object) -> bool:
    """An INTEGER count — never a bool (#9089 review).

    ``bool`` is a subclass of ``int``, so a bare ``isinstance(x, int)`` accepts
    a JSON ``true``, and ``0 < True <= True`` then passes every numeric arm of
    V5/V10: a block saying ``"numerator": true, "denominator": true`` was read
    as a GREEN *measurement* (reproduced on this module's own ``_green``
    fixture: no finding, ``--require-green`` exit 0). That is the one malformed
    shape this file's stated contract reads the WRONG WAY — every other
    wrong-typed block (non-object block, unhashable criterion, non-iterable
    ``steps_observed``) is refused as malformed — so it is refused here too
    rather than counted.

    Refusal has TWO halves and both are load-bearing: a value that is not a
    count must be REFUSED where a count is required, and must not silently
    SKIP the comparison that found it (#9089 review cycle 2 — replacing the
    ``isinstance`` guard with this helper made the ``e4_receipt`` agreement arm
    skip a boolean receipt denominator, dropping a V10 that used to fire).
    """
    return isinstance(value, int) and not isinstance(value, bool)


def _count_key(value: object) -> tuple:
    """A value's IDENTITY for the agreement checks (V8, V10).

    ``True == 1`` and ``1 == 1.0`` in Python, so a raw ``!=`` reads a JSON
    ``true`` as AGREEING with a sibling row's ``1`` — the same malformed-shape
    class ``_count`` refuses, at the comparison that decides whether two copies
    of one number match (#9089 review cycle 2). A real count compares as
    itself; anything else compares by type and repr, so a boolean, a float or a
    string can never stand in for the number the other copy carries.
    """
    if _count(value):
        return (True, value)
    return (False, type(value).__name__, repr(value))


def _corpus_digest(value: object) -> str | None:
    """A corpus digest as BARE lowercase hex, or ``None`` when it is not one.

    The record and the artifacts each spell the same digest their own way
    (``preregistration.json`` writes ``sha256:<hex>``; ``e1-e2.log`` writes
    ``corpus_sha256=<hex>``), so a string comparison across them would red a
    correct declaration on the prefix alone. Normalising here — rather than at
    each call site — keeps the equality test about the DIGEST.

    A value that is not a 64-digit hex string is not a digest, and returning
    ``None`` for it is what makes V7 report an unusable declaration instead of
    comparing two strings that were never digests.
    """
    if not isinstance(value, str):
        return None
    text = value[len("sha256:"):] if value.startswith("sha256:") else value
    return text if re.fullmatch(r"[0-9a-f]{64}", text) else None


def _same_corpus(a: object, b: object) -> bool:
    """Two corpus spellings of the SAME revision?

    ``preregistration.json`` writes ``sha256:<hex>``, ``e1-e2.log`` writes
    ``corpus_sha256=<hex>`` and an operator receipt writes ``sha256:<hex>`` — three
    spellings, one digest. Comparing the raw strings would report a row as
    "measured on a different corpus" on the prefix alone, which is the opposite of
    what this rule is for. Where either value is not a digest at all, the raw
    comparison stands (and the callers above have already reported the shape).
    """
    left, right = _corpus_digest(a), _corpus_digest(b)
    if left is not None and right is not None:
        return left == right
    return a == b


def _non_object_blocks(record: dict) -> list[tuple[str, str]]:
    """Nested blocks that are PRESENT but not JSON objects, as ``(key, type)``.

    ``check()`` reads these three blocks with ``.get`` unconditionally
    (``prereg.get("sha")``, ``run.get("status")``, ``corpus.get("sha256")``),
    and only the ROOT record was type-checked. A truthy non-object therefore
    raised ``AttributeError: 'list' object has no attribute 'get'`` out of
    ``check()`` and surfaced as **exit 1 with a traceback** — the code this tool
    reserves for "the table has findings", i.e. a verdict on the evidence that
    was never reached (#9089 review). Measured before the fix: ``{"corpus":
    [1]}`` reached ``corpus.get("sha256")`` from V7's non-GREEN arm.

    ABSENT (``None``) is legitimate — an older or partial log may carry no block
    at all — so only a present, wrong-typed value counts as malformed. ``main()``
    maps it to usage error 2 (a malformed machine block is not a verdict on the
    evidence, the same call the root-not-a-table guard makes); ``check()`` records
    it as a V1 finding so a direct caller can never be handed a traceback either.
    """
    bad: list[tuple[str, str]] = []
    for key in _BLOCK_KEYS:
        value = record.get(key)
        if value is not None and not isinstance(value, dict):
            bad.append((key, type(value).__name__))
    return bad


def _object(record: dict, key: str) -> dict:
    """``record[key]`` when it is an object, else ``{}`` (see
    ``_non_object_blocks``: the malformation is reported, never raised)."""
    value = record.get(key)
    return value if isinstance(value, dict) else {}


def extract_block(text: str) -> dict:
    """Pull the verdict JSON out of a capstone log.

    The log is a human-readable run record with the machine block embedded, so
    the operator reads the same file the gate parses — one artifact, not two
    that can disagree. A missing/malformed block is a usage error (exit 2), not
    a finding: a log with no table is not a red row, it is not a table.

    ⛔ EXACTLY ONE BLOCK, because the gate reads one RUN. `text.index` alone
    stops at the FIRST marker pair, so an appended re-run left the newer table
    unread while the stale one still decided the epic — measured on the pre-fix
    tool: a log holding a 5/5-green block followed by a REFUSED block exited 0,
    and `--require-green` answered "closeable" for an artifact whose newest run
    refused. That is reachable WITHOUT forgery — `capstone_run.py | tee -a
    capstone.log` is enough — so it is an inference hole, not the documented
    forgery limit. Refusing the ambiguity keeps "one run decides" true.
    """
    if BEGIN not in text or END not in text:
        raise ValueError(
            f"no verdict block: expected the markers {BEGIN!r} / {END!r} "
            "(capstone_run.py emits them)")
    begins, ends = text.count(BEGIN), text.count(END)
    if begins != 1 or ends != 1:
        raise ValueError(
            f"expected exactly one verdict block, found {begins} {BEGIN!r} and "
            f"{ends} {END!r} marker(s) — the gate reads ONE run, so an appended "
            "re-run would leave its table unread (write each run to its own "
            "file; capstone_run.py emits exactly one pair)")
    start = text.index(BEGIN) + len(BEGIN)
    end = text.index(END)
    if end < start:
        raise ValueError("verdict block markers are out of order")
    try:
        return json.loads(text[start:end].strip())
    except RecursionError as exc:
        # #9089 review P2: deeply nested JSON raises RecursionError, which is NOT a JSONDecodeError
        # and is caught by NEITHER this module's ValueError arm nor `main` — it surfaced as a
        # traceback with exit 1, the code reserved for "the table has findings". The log's block is
        # unreadable, so the honest answer is the usage error (exit 2) every other unreadable
        # block gets.
        raise ValueError(
            "the verdict block nests too deeply to parse — a block this gate "
            "cannot read is a usage error, not a finding about the table"
        ) from exc


class Findings:
    def __init__(self) -> None:
        self.items: list[tuple[str, str, str]] = []
        #: Provenance POINTERS this checkout cannot answer for, as
        #: ``(kind, pointer, reason)`` where kind is ``"row"`` or ``"prereg"``.
        #: Reported, not failed by default: a squash-merged branch sha NEVER
        #: reaches main, so making it fatal would red the shipped bundle on
        #: every PR after merge (V11, and `--require-resolvable` for the lane
        #: that still carries the commit).
        self.unresolved: list[tuple[str, str, str]] = []

    def add(self, code: str, where: str, msg: str) -> None:
        self.items.append((code, where, msg))

    def __bool__(self) -> bool:
        return bool(self.items)

    def render(self) -> str:
        return "\n".join(f"  [{c}] {w}: {m}" for c, w, m in self.items)


#: Where the frozen record's OWN step declaration lives (#10215). The log's
#: ``run.steps_conditional`` is cross-checked against this, so conditionality is
#: a reviewed edit to a hash-pinned artefact rather than a lane's local choice.
RECORD_STEPS_KEY = "steps"


def _record_conditional(repo: Path, prereg_sha: str | None,
                        artifact: str) -> tuple[set[str] | None, str]:
    """The conditional steps the FROZEN record declares, or ``(None, why not)``.

    Read out of the pre-registration COMMIT (``git show <sha>:<artifact>``) for
    the same reason the run probe reads it that way: a record edited after the
    numbers are in is not a pre-registration, and a worktree read would serve
    that edit.
    """
    if not prereg_sha or not isinstance(prereg_sha, str):
        return None, "the log names no pre-registration sha"
    if not artifact or not isinstance(artifact, str):
        return None, "the log names no pre-registration artifact"
    blob = _git(repo, "show", f"{prereg_sha}:{artifact}")
    if blob.returncode != 0:
        return None, f"{prereg_sha}:{artifact} does not resolve in this checkout"
    try:
        record = json.loads(blob.stdout)
    except json.JSONDecodeError as exc:
        return None, f"{artifact} at {prereg_sha} is not JSON: {exc}"
    if not isinstance(record, dict):
        return None, f"{artifact} at {prereg_sha} is a {type(record).__name__}, not an object"
    block = record.get(RECORD_STEPS_KEY)
    if not isinstance(block, dict):
        return None, (f"{artifact} at {prereg_sha} carries no {RECORD_STEPS_KEY!r} "
                      "block, so no step is declared conditional")
    entries = block.get("conditional")
    if not isinstance(entries, list):
        return None, (f"{RECORD_STEPS_KEY}.conditional in {artifact} at {prereg_sha} "
                      f"is {type(entries).__name__}, expected a list")
    steps: set[str] = set()
    for entry in entries:
        if isinstance(entry, dict) and isinstance(entry.get("step"), str):
            steps.add(entry["step"])
    return steps, ""


def _step_accounting(repo: Path, run: dict, observed: tuple,
                     prereg_sha: str | None, artifact: str) -> list[tuple[str, str]]:
    """V5's step half: every one of the eight ACCOUNTED FOR, never dropped (#10215).

    Four rules, all of them conservative:

    1. ``steps_required`` and ``steps_conditional`` are a strict PARTITION of the
       eight step ids. A log that removes a step from ``steps_required`` without
       declaring it conditional is the silent-drop shape this rule exists to
       refuse — otherwise "we dropped S2.3" and "S2.3 was conditional" read
       identically.
    2. Every required step is observed in the one pass (unchanged from before).
    3. Every conditional step is either observed, or declared ``idle`` with a
       NON-EMPTY basis. A bare ``{"step": "S2.3", "state": "idle"}`` is a
       claim with no artefact and is refused.
    4. The conditional SET must equal the frozen record's declaration. A log may
       not promote a step to conditional on its own authority, and where the
       record cannot be read the claim is REFUSED rather than trusted — a
       conditionality nobody can check is not a recorded decision.
    """
    required_raw = run.get("steps_required")
    required = (list(required_raw) if isinstance(required_raw, (list, tuple))
                else list(STEPS))
    entries = run.get("steps_conditional") or []
    out: list[tuple[str, str]] = []
    if not isinstance(entries, (list, tuple)):
        # A green row cannot lean on a conditional declaration that is not a list:
        # report it and fall back to "nothing is conditional" (rule 2 then applies).
        out.append(("V5", "steps_conditional is "
                          f"{type(entries).__name__}, expected a list — "
                          "no step can be accounted conditional through it"))
        entries = []
    accounts: dict[str, dict] = {}
    for entry in entries:
        if isinstance(entry, dict) and isinstance(entry.get("step"), str):
            accounts[entry["step"]] = entry
        else:
            out.append(("V5", "steps_conditional carries an entry that is "
                              f"not a {{'step': …}} object ({entry!r}) — unreadable "
                              "as an accounting"))
    conditional = set(accounts)

    # 1 — a strict partition of the eight. `steps_required` gates this only for KEYS
    # the log actually declares; a log with no such key is read as "the eight" (the
    # pre-#10215 shape), so an older log's verdict is unchanged.
    dropped = [s for s in STEPS if s not in required and s not in conditional]
    invent = [s for s in list(required) + list(conditional) if s not in STEPS]
    overlap = [s for s in required if s in conditional]
    if dropped:
        out.append(("V5", "GREEN with steps neither observed-required nor declared "
                           f"conditional: {', '.join(dropped)} — a step cannot leave "
                           "the gate by being omitted from both lists"))
    if invent:
        out.append(("V5", "GREEN with step ids that are not part of the epic's "
                           f"eight: {', '.join(sorted(set(invent)))} — the step list "
                           "is fixed (§4.1: S0, S1, S2.1, S2.2, S2.3, S3, S5, S6)"))
    if overlap:
        out.append(("V5", "GREEN with steps declared BOTH required and conditional: "
                           f"{', '.join(overlap)} — conditionality is not an escape "
                           "hatch for a step that did not run"))

    # 4 — the conditional set is the FROZEN record's, not the log's. Checked before
    # the observation rules so a fabricated conditionality is reported as exactly
    # that rather than as a missing step.
    if conditional:
        declared, why = _record_conditional(repo, prereg_sha, artifact)
        if declared is None:
            out.append(("V5", "GREEN with steps declared conditional "
                               f"({', '.join(sorted(conditional))}) while the frozen "
                               f"pre-registration cannot confirm it — {why}. A "
                               "conditionality the record does not declare is not a "
                               "recorded decision, so it is refused rather than "
                               "trusted"))
        elif conditional != declared:
            out.append(("V5", "GREEN with conditional steps "
                               f"{', '.join(sorted(conditional))} but the frozen "
                               f"pre-registration declares "
                               f"{', '.join(sorted(declared)) or 'none'} — the log may "
                               "not change which steps are conditional"))

    # 2 — every required step observed.
    missing = [s for s in required if s not in observed]
    if missing:
        out.append(("V5", "GREEN with steps not observed in the run: "
                           f"{', '.join(missing)} — the epic measures the pipeline "
                           "end to end, not a step probe"))

    # 3 — every conditional step ACCOUNTED. Observed outranks idle (a pass that ran
    # is a pass that ran); an idle claim needs a basis, and its state vocabulary is
    # closed for the same reason every other one here is.
    for step in sorted(conditional):
        if step in observed:
            continue
        entry = accounts.get(step) or {}
        state = entry.get("state")
        basis = str(entry.get("basis") or "").strip()
        if state not in STEP_STATES:
            out.append(("V5", f"GREEN with conditional step {step} accounted as "
                               f"state={state!r} — the vocabulary is "
                               f"{', '.join(STEP_STATES)}; a conditional step must "
                               "say whether it ran or had nothing to do"))
        elif state == "idle" and not basis:
            out.append(("V5", f"GREEN with conditional step {step} declared idle "
                               "with no basis — an idle claim without the run's own "
                               "evidence (flag ON, no activity, no fail-open error) "
                               "is a claim, not a witness"))
    return out


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(repo), capture_output=True,
                          text=True, check=False)


def _is_git_repo(repo: Path) -> bool:
    return _git(repo, "rev-parse", "--is-inside-work-tree").returncode == 0


def _resolves(repo: Path, sha: str) -> bool:
    return _git(repo, "cat-file", "-e", f"{sha}^{{commit}}").returncode == 0


def _is_ancestor(repo: Path, maybe_ancestor: str, of: str) -> bool | None:
    """True when ``maybe_ancestor`` is ``of`` or precedes it; None if UNKNOWN.

    This is the mechanical form of *"0 rows green where the evidence artifact
    predates the pre-registration"*: evidence measured BEFORE the thresholds
    were frozen cannot be evidence for them — the threshold would have been
    chosen to fit the number.

    ⛔ THREE outcomes, not two. ``git merge-base --is-ancestor`` exits 1 for
    "no" and 128 for "I cannot answer" (a missing object, e.g. a prereg commit
    that was squash-merged away). Reading 128 as "not an ancestor" emits a
    finding that CLAIMS the evidence predates the thresholds — a false
    accusation the lane cannot remediate, so the unknown case returns None and
    the caller reports it as unreported provenance instead.
    """
    if maybe_ancestor == of:
        return True
    rc = _git(repo, "merge-base", "--is-ancestor", maybe_ancestor, of).returncode
    if rc == 0:
        return True
    if rc == 1:
        return False
    return None


def check(record: dict, *, repo: Path,
          require_green: bool, require_resolvable: bool = False) -> Findings:
    """Apply V1–V11 to a parsed verdict table.

    ``repo`` is the checkout the evidence paths and shas are resolved against —
    the ONLY input besides the record. (A ``log_path`` parameter used to be
    threaded here and never read; a dead parameter in a gate reads as if the
    path took part in the verdict, so it is gone rather than documented away.)
    """
    f = Findings()

    # ── V1 root schema ────────────────────────────────────────────────────
    if record.get("schema") != SCHEMA:
        f.add("V1", "root", f"schema is {record.get('schema')!r}, expected "
                            f"{SCHEMA!r} — refuse rather than guess a shape")

    # ── V2 the five-criterion contract ────────────────────────────────────
    rows = record.get("rows")
    if not isinstance(rows, list):
        f.add("V2", "root.rows", "missing or not a list — the table is 5 rows")
        return f
    # A `criterion` that is a LIST (or dict) is unhashable: the census below
    # would raise on `count()`/`in` and surface as exit 1 — the code reserved
    # for "the table has findings". Only hashable scalars take part.
    seen = [r.get("criterion") for r in rows
            if isinstance(r, dict)
            and isinstance(r.get("criterion"), HASHABLE_SCALARS)]
    for criterion in CRITERIA:
        n = seen.count(criterion)
        if n == 0:
            f.add("V2", f"rows[{criterion}]", "row is MISSING — the epic closes "
                                              "on 5/5, so an absent row can "
                                              "never be inferred from the rest")
        elif n > 1:
            f.add("V2", f"rows[{criterion}]", f"{n} rows — a criterion is "
                                              "decided once, not voted on")
    for criterion in seen:
        if criterion not in CRITERIA:
            f.add("V2", f"rows[{criterion}]", "not an E1–E5 criterion")

    # A block that is `[]`/`"x"`/`5` is REPORTED here and read as empty below.
    # `.get` on a non-object raised AttributeError and escaped as exit 1 on a
    # traceback (#9089 review) — see `_non_object_blocks`.
    for key, typename in _non_object_blocks(record):
        f.add("V1", f"root.{key}", f"the {key!r} block is a {typename}, not an "
                                     "object — the machine block is malformed, "
                                     "so nothing in it can be checked")

    run = _object(record, "run")
    prereg = _object(record, "prereg")
    corpus = _object(record, "corpus")
    prereg_sha = prereg.get("sha")
    if not (isinstance(prereg_sha, str) and _SHA_RE.match(prereg_sha or "")):
        # A table with no frozen pre-registration can hold no green row; the
        # rows themselves carry the honest verdict, so this is a note unless a
        # green row tries to lean on it (V5).
        prereg_sha = None

    by_id = {r.get("criterion"): r for r in rows
             if isinstance(r, dict)
             and isinstance(r.get("criterion"), HASHABLE_SCALARS)}

    for row in rows:
        if not isinstance(row, dict):
            f.add("V3", "rows[]", f"row is not an object: {row!r}")
            continue
        cid = row.get("criterion")
        where = f"rows[{cid}]"

        # ── V3 vocabulary + shape ─────────────────────────────────────────
        verdict = row.get("verdict")
        if verdict not in VERDICTS:
            f.add("V3", where, f"verdict {verdict!r} not in {VERDICTS} — "
                               "'unknown' is not a verdict")
        kind = row.get("evidence_kind")
        if kind not in EVIDENCE_KINDS:
            f.add("V3", where, f"evidence_kind {kind!r} not in "
                               f"{EVIDENCE_KINDS}")
        sha = row.get("sha")
        if not (isinstance(sha, str) and _SHA_RE.match(sha)):
            f.add("V3", where, f"sha {sha!r} is not a commit sha — every row "
                               "is measured AT a sha")
        elif not _is_git_repo(repo):
            f.unresolved.append(("row", sha, "not a git work tree"))
        elif not _resolves(repo, sha):
            # ⛔ EVERY row, not only the green ones (#9089 review). The resolve
            # check used to sit inside the GREEN branch, and the shipped table
            # is 0/5 green — so the property this tool advertises ("a sha that
            # RESOLVES") was enforced on zero rows of the artifact it blesses.
            # Measured before the fix: five rows stamped `"0"*40` returned NO
            # findings.
            #
            # But the sha is a PROVENANCE POINTER, and a pointer to a
            # squash-merged branch commit is unresolvable BY DESIGN once it
            # lands: the acceptance command stamps the branch head, and main
            # carries single-parent squashes, so the sha can never resolve
            # there. Making that fatal would red this bundle's own shipped-log
            # test on every PR after merge — a durable main-red made by the
            # instrument. So resolution is REPORTED here (V11) and only FAILS
            # under `--require-resolvable`, which the lane that still holds the
            # commit passes. Shape is always fatal (above).
            f.unresolved.append(("row", sha, "not in this object DB"))

        # ── V4 the artifact must exist ────────────────────────────────────
        evidence = row.get("evidence")
        ev_path = None
        if not (isinstance(evidence, str) and evidence.strip()):
            f.add("V4", where, "no evidence path — a row with no artifact is "
                               "an inference, not evidence")
        else:
            ev_path = Path(evidence)
            if ev_path.is_absolute() or ".." in ev_path.parts:
                f.add("V4", where, f"evidence path {evidence!r} must be "
                                   "repo-relative with no escape")
                ev_path = None
            elif not (repo / ev_path).is_file():
                f.add("V4", where, f"evidence path {evidence!r} does not exist "
                                   "— a green row on a missing file is the "
                                   "defect this gate exists for")

        num, den = row.get("numerator"), row.get("denominator")

        # ── V5 green means measured ───────────────────────────────────────
        if verdict == "GREEN":
            if kind != "measurement":
                f.add("V5", where, "GREEN requires evidence_kind='measurement' "
                                   "— a refusal record cannot be green")
            if not _count(num) or not _count(den):
                f.add("V5", where, "GREEN requires integer numerator and "
                                   "denominator")
            elif den <= 0:
                f.add("V5", where, f"denominator is {den} — a green row with an "
                                   "empty denominator measures nothing")
            elif not 0 < num <= den:
                # ``0 < num``, not ``0 <= num`` (#9080 review F3): a GREEN 0/15
                # is the total-silent-drop shape E4's row exists to refuse —
                # zero successes reported as a measurement that passed.
                f.add("V5", where, f"numerator {num} outside 1..{den} — a green "
                                   "row must measure at least ONE success; "
                                   "0 is the silent-drop shape, not a result")
            if row.get("blocked_by"):
                f.add("V5", where, f"GREEN carries a blocker "
                                   f"({row.get('blocked_by')!r}) — green and "
                                   "blocked are mutually exclusive")
            if run.get("status") != "COMPLETED":
                f.add("V5", where, f"GREEN with run.status="
                                   f"{run.get('status')!r} — every row comes "
                                   "from ONE completed pass")
            observed_raw = run.get("steps_observed")
            # `tuple(run.get("steps_observed") or ())` raised TypeError on a
            # non-iterable (`"steps_observed": 5`) — exit 1 on a traceback. A
            # non-list value observes nothing, which is what this finding says.
            observed = (tuple(observed_raw)
                        if isinstance(observed_raw, (list, tuple)) else ())
            # ── the step ACCOUNTING (#10215). S2.3 is conditional: it has work
            # only when some candidate's kind is missing or the `unclassified`
            # sentinel, so a corpus whose S2 map kinds every candidate legitimately
            # idles it. Requiring all eight UNCONDITIONALLY was false by
            # construction — no run could ever satisfy V5 — which is the inert
            # form of this rule. The restatement is NOT a relaxation: every one of
            # the eight still has to be accounted for, the conditional set comes
            # from the frozen record (never from the log), and an idle step must
            # carry the run's own basis (its flag ON, no activity, no fail-open
            # error — a crashed pass leaves the SAME empty counters).
            for code, msg in _step_accounting(repo, run, observed,
                                             prereg_sha, prereg.get("artifact")):
                f.add(code, where, msg)
            if sha and isinstance(sha, str) and _SHA_RE.match(sha):
                # The FROZEN-THRESHOLD check needs no git at all, so it must not
                # sit behind resolvability: a 5/5-green table with NO frozen
                # pre-registration at all reported "structurally sound" (exit 0,
                # even with --require-green) as soon as the row shas were
                # absent-but-well-formed — the exact shape a depth-1 CI clone
                # produces (#9089 review cycle 3).
                if prereg_sha is None:
                    f.add("V5", where, "GREEN with no frozen pre-registration "
                                       "sha — the verdict would be measured "
                                       "against a threshold nobody fixed "
                                       "first (#9075)")
                elif prereg.get("status") != "OK":
                    # The block ALSO records whether G0 read the frozen record.
                    # A table saying `status: "ABSENT"` (in the same breath as a
                    # resolvable, ancestral prereg sha) denies the read that the
                    # green verdict rests on. Measured before the fix: such a
                    # table passed 5/5 GREEN and `--require-green` exited 0 —
                    # the epic's closing verdict granted on a log that says the
                    # pre-registration was never read.
                    f.add("V5", where, "GREEN under a log whose own prereg "
                                       f"block records status="
                                       f"{prereg.get('status')!r} — green "
                                       "rests on a pre-registration this run "
                                       "did not read")
                # ...whereas ANCESTRY is a git question and only answerable
                # where the measured commit is reachable. It is reported, not
                # failed, for the same reason V3's resolution is (a
                # squash-merged branch sha never reaches main);
                # `--require-resolvable` turns it into a finding.
                elif not _is_git_repo(repo) or not _resolves(repo, sha):
                    pass
                elif not _resolves(repo, prereg_sha):
                    # The prereg commit is the OTHER operand of the ancestry
                    # test. Without this, a prereg object this checkout cannot
                    # see made `_is_ancestor` answer "no" and the gate printed
                    # five FALSE "evidence PREDATES the pre-registration"
                    # findings with a cause the lane cannot remediate.
                    f.unresolved.append(
                        ("prereg", prereg_sha, "not in this object DB"))
                else:
                    ancestry = _is_ancestor(repo, prereg_sha, sha)
                    if ancestry is None:
                        f.unresolved.append(
                        ("prereg", prereg_sha, "not in this object DB"))
                    elif ancestry is False:
                        f.add("V5", where, "evidence PREDATES the "
                                           f"pre-registration ({sha} is not a "
                                           f"descendant of {prereg_sha}) — "
                                           "evidence cannot validate "
                                           "thresholds it was measured before")

        # ── V6 non-green names its blocker ────────────────────────────────
        else:
            blocker = row.get("blocked_by")
            if not (isinstance(blocker, str) and _BLOCKER_RE.match(blocker)):
                f.add("V6", where, f"verdict {verdict!r} must name the blocking "
                                   f"issue as '#NNN' (got {blocker!r}) — the "
                                   "epic asks for a red row WITH its cause")
            if kind == "refusal" and ev_path is not None:
                # A refusal row cites the RUN LOG that records the refusal.
                # Checked by CONTENT rather than by path equality so the log
                # stays relocatable (the acceptance command pipes it through
                # `tee`): the cited file must itself be a capstone log that
                # carries THIS row. A prose file, a README, or another lane's
                # artifact is not a refusal record, and citing one would put a
                # measurement's clothes on a refusal.
                try:
                    cited = extract_block((repo / ev_path).read_text())
                    # ``extract_block`` is raw ``json.loads``, so a cited file
                    # whose block is ``[]`` / ``"x"`` made ``cited.get`` raise
                    # AttributeError — NOT in the tuple below — and an uncaught
                    # exception exits 1 ("the table has findings") with a
                    # traceback and no verdict reached. A non-object block is
                    # simply not a capstone log, which is this arm's finding.
                    if not isinstance(cited, dict):
                        raise ValueError(
                            f"the cited block is a {type(cited).__name__}, "
                            "not a capstone log")
                    cited_rows = cited.get("rows")
                    # The criterion set is built from HASHABLE scalars only —
                    # a `criterion` that is a LIST is unhashable and raised
                    # TypeError out of the comprehension, the same escape as
                    # the V2 census above (which already guards it).
                    cited_ids = {
                        r.get("criterion")
                        for r in (cited_rows if isinstance(cited_rows, list) else [])
                        if isinstance(r, dict)
                        and isinstance(r.get("criterion"), HASHABLE_SCALARS)}
                except (OSError, ValueError, json.JSONDecodeError):
                    f.add("V6", where, f"evidence_kind='refusal' must cite a "
                                       f"capstone log; {evidence!r} is not one")
                else:
                    # ⛔ `cid` is the OUTER row's criterion — and it is a SET
                    # MEMBER here, `cid not in cited_ids`. Both peer sites of
                    # this shape (the V2 census and `cited_ids` above) filter
                    # to hashable scalars; this third one did not, so a row
                    # whose criterion is `["E5"]` raised
                    # `TypeError: unhashable type: 'list'` out of `check()` and
                    # `main()` has no handler — exit **1**, documented as "the
                    # table has findings", i.e. a verdict on the EVIDENCE,
                    # when the artifact was merely malformed (#9089 review).
                    # Reported here instead, in the same words the cited-log
                    # arm already uses for a row name no log can carry.
                    if not isinstance(cid, HASHABLE_SCALARS):
                        f.add("V6", where, f"criterion {cid!r} is a "
                                           f"{type(cid).__name__}, not a row "
                                           "name — no cited log can carry it")
                    elif cid not in cited_ids:
                        f.add("V6", where, f"the cited log {evidence!r} does "
                                           f"not carry a {cid} row")

        # ── V7 one corpus ─────────────────────────────────────────────────
        row_corpus = row.get("corpus_sha256")
        # ⛔ MEASURED, NOT MERELY VERDICTED (#9089 review cycle 8). The arm used
        # to fire only on GREEN/RED, so a row that DECLARES a measurement
        # (`evidence_kind: "measurement"`) under a non-GREEN verdict — exactly
        # the shape `capstone_run.py::completed_rows` writes for four of five
        # rows — carried no corpus obligation at all: a null `corpus_sha256`
        # against a log that identified a corpus produced NO finding, while the
        # same row as RED produced V7. The docstring states this rule of "a
        # measurement row", so the row's EVIDENCE KIND is the predicate, not
        # its verdict word.
        if verdict in ("GREEN", "RED") or kind == "measurement":
            if not row_corpus:
                f.add("V7", where, "a measured row must carry corpus_sha256 — "
                                   "the before/after denominators have to be "
                                   "the same corpus")
            elif not corpus.get("sha256"):
                # ⛔ The comparison used to be SKIPPED when the log carried no
                # corpus sha (``and corpus.get("sha256")``), so a measured row
                # could name ANY corpus and pass — measured: a GREEN row whose
                # corpus_sha256 was `b*64` against a log whose corpus.sha256
                # was null returned no finding. "Two corpora cannot share one
                # verdict" is unenforceable exactly where nobody wrote down
                # which corpus ran, so that absence is the finding.
                f.add("V7", where, "a measured row needs the LOG to identify "
                                   "its corpus (corpus.sha256 is null) — "
                                   "without it the row's corpus cannot be "
                                   "checked against anything")
            elif _same_corpus(row_corpus, corpus.get("sha256")):
                pass
            elif verdict == "GREEN":
                f.add("V7", where, f"corpus_sha256 {row_corpus!r} != the log's "
                                   f"{corpus.get('sha256')!r} — two corpora "
                                   "cannot share one verdict")
            else:
                # ⛔ A MULTI-ARTIFACT TABLE, CHECKED RATHER THAN TRUSTED. This
                # bundle's numbers do not all come from one run: E1/E2 come from
                # #9076's arm run and E4 from an operator receipt, and neither
                # was measured on the corpus THIS run froze (their own records
                # say which corpus they ran — `corpus_sha256=` in e1-e2.log,
                # `corpus_hash` in the receipt). Refusing those rows outright
                # would hide the only numbers E1/E2/E4 have; accepting them on
                # the row's word would let any red row name any corpus. So the
                # row DECLARES the artifact's revision and the declaration is
                # verified against the ARTIFACT ITSELF — a stronger test than the
                # one it replaces, and unreachable for a GREEN row (the arm above
                # keeps green strict).
                declared = row.get("artifact_corpus_sha256")
                digest = _corpus_digest(declared)
                if digest is None:
                    f.add("V7", where,
                          f"corpus_sha256 {row_corpus!r} is not the log's corpus "
                          f"({corpus.get('sha256')!r}), so the row must declare "
                          "the artifact's own revision in "
                          f"artifact_corpus_sha256 (got {declared!r}) — an "
                          "undeclared corpus is what 'two corpora cannot share "
                          "one verdict' refuses")
                elif _corpus_digest(row_corpus) != digest:
                    f.add("V7", where,
                          f"artifact_corpus_sha256 ({declared!r}) disagrees with "
                          f"the row's corpus_sha256 ({row_corpus!r}) — the row "
                          "must name ONE corpus for its number")
                elif ev_path is None:
                    pass  # V4 already reported the unusable evidence path
                else:
                    try:
                        cited_text = (repo / ev_path).read_text(errors="replace")
                    except OSError:
                        cited_text = None
                    if cited_text is None:
                        pass  # V4 owns an unreadable artifact
                    elif digest not in cited_text:
                        f.add("V7", where,
                              f"artifact_corpus_sha256 {digest} does not appear "
                              f"in the cited artifact {evidence!r} — a declared "
                              "corpus the artifact itself never records is a "
                              "declaration nothing supports")
                    elif not (isinstance(row.get("note"), str)
                              and row.get("note", "").strip()):
                        f.add("V7", where,
                              "a row measured on a different corpus revision "
                              "must say so in its note — the reader cannot see "
                              "it from two digests")
        elif row_corpus and corpus.get("sha256") and \
                not _same_corpus(row_corpus, corpus.get("sha256")):
            f.add("V7", where, "corpus_sha256 disagrees with the log's corpus")

    # ── V10 E4's own failing condition ────────────────────────────────
    # E4's row says it *"fails if the denominator is 4"*: the historical
    # defect merged 4 of the 15 edges reachable on the real extraction leg.
    # The probe computes the number, and it once carried it only in a NOTE and
    # in `run.e4_receipt` while the row's `denominator` stayed null — so the
    # defect could reappear inside the one table the probe produces and this
    # gate still called it sound (#9089 review). Two things follow:
    #   1. the check reads a MEASURED row, not only a GREEN/RED one: on this
    #      surface E4 is `BLOCKED` (its evidence artifact is another lane's) and
    #      still measured, which is exactly the row that was unreachable here;
    #   2. the row and the receipt must carry the SAME number. Otherwise a
    #      hand-edit of one of the two copies decides E4 while the other
    #      contradicts it in the same file — the disagreement is the finding.
    e4 = by_id.get("E4")
    if e4 is not None:
        e4_den = e4.get("denominator")
        measured = (e4.get("verdict") in ("GREEN", "RED")
                    or e4.get("evidence_kind") == "measurement")
        if measured and e4_den == 4:
            f.add("V10", "rows[E4]", "E4 measured with denominator 4 — the "
                                        "criterion fails on that number (the "
                                        "historical 4-edge defect's "
                                        "denominator), so this is not a "
                                        "measurement of the criterion")
        # V5's "an empty denominator measures nothing" lives in its GREEN arm,
        # and on this surface E4 can never be green (`completed_rows` writes
        # BLOCKED for every row while #9116 holds). Without this arm the ONE
        # row that carries numbers could be measured 0/0 and the gate would
        # still print "denominators agree" — the silent-zero shape V5 exists
        # to refuse, reachable only through the row V5 cannot see.
        if measured and not (_count(e4_den) and e4_den > 0):
            f.add("V10", "rows[E4]", f"E4 is MEASURED but carries no positive "
                                        f"denominator ({e4_den!r}) — an empty "
                                        "denominator measures nothing, and "
                                        "V5's GREEN-only arm cannot reach this "
                                        "row (E4 is never GREEN here)")
        receipt = run.get("e4_receipt")
        # The agreement arm's guard is NOT a skip (#9089 review cycle 2): a
        # receipt whose denominator is present but is not an integer count (a
        # JSON ``true``, a string) is a SECOND COPY THAT CANNOT AGREE, and the
        # pre-helper arm refused it because ``True != 15``. Guarding with
        # ``_count(...)`` alone silently skipped that comparison and dropped a
        # true-positive V10, so the non-count receipt is reported explicitly.
        #
        # ⛔ AND SO IS AN ABSENT ONE (#9089 review cycle 8): a receipt that is
        # present but carries no `denominator` key was skipped by the
        # `in receipt` guard, so the row's 15/15 stood with nothing behind it
        # while the gate still reported one number deciding E4. By this arm's
        # own rule E4's row must carry the SAME denominator the receipt
        # computed, and absence is the same non-agreement as a wrong value.
        if measured and _count(e4_den):
            if not isinstance(receipt, dict):
                # ⛔ ABSENT OR NON-OBJECT IS THE SAME FINDING (#9089 review cycle 9):
                # deleting the whole receipt block bypassed the arm cycle 8 tightened for a
                # missing KEY, so `15/15` stood with no second copy at all while the gate called
                # the table sound. There is no receipt on a REFUSED run, and this arm only asks
                # its question of a MEASURED row, so the shipped table is untouched.
                f.add("V10", "rows[E4]",
                      f"E4 is MEASURED ({e4_den}) but the run carries no "
                      f"e4_receipt object ({receipt!r}) — the row's number has "
                      "no second copy in the same log, and 'one number decides "
                      "E4' is exactly what this arm refuses to accept on trust")
            elif "denominator" not in receipt:
                f.add("V10", "rows[E4]",
                      f"the run's e4_receipt carries no denominator while the "
                      f"row's is {e4_den} — the row's number is unbacked, and "
                      "one number decides E4 only when both copies carry it")
            else:
                receipt_den = receipt.get("denominator")
                if not _count(receipt_den):
                    f.add("V10", "rows[E4]",
                          f"the run's e4_receipt denominator is not an integer "
                          f"count ({receipt_den!r}) while the row's is {e4_den} "
                          "— one number decides E4, and a copy that is not a "
                          "number cannot agree with the row's")
                elif receipt_den != e4_den:
                    f.add("V10", "rows[E4]",
                          f"the row's denominator ({e4_den}) and the run's "
                          f"e4_receipt ({receipt_den}) disagree — one number "
                          "decides E4, and two copies that differ let a hand-edit "
                          "pick which")

    # ── V8 denominators carried, not restated ────────────────────────────
    for cid, row in by_id.items():
        sib = row.get("sibling")
        if not sib:
            continue
        if not isinstance(sib, (str, int, float, bool)):
            # An unhashable sibling (``["E1"]``) raised TypeError out of
            # `by_id.get(sib)` — exit 1 on a traceback, the same escape class as
            # a wrong-typed block (#9089 review).
            f.add("V8", f"rows[{cid}]", f"sibling {sib!r} is not a row name")
            continue
        other = by_id.get(sib)
        if other is None:
            f.add("V8", f"rows[{cid}]", f"sibling {sib!r} is not a row")
            continue
        if (_count_key(row.get("numerator")),
                _count_key(row.get("denominator"))) != \
                (_count_key(other.get("numerator")),
                 _count_key(other.get("denominator"))):
            f.add("V8", f"rows[{cid}]",
                  f"denominators disagree with sibling {sib}: "
                  f"{row.get('numerator')}/{row.get('denominator')} vs "
                  f"{other.get('numerator')}/{other.get('denominator')}")

    # ── V11 provenance pointers that do not resolve here ────────────────
    # Reported, and FAILED only on request: a squash-merged branch sha is
    # unresolvable in main BY DESIGN (see V3), so a default finding here would
    # red the shipped bundle forever. `--require-resolvable` is for the lane
    # that still carries the commit and wants the pointers proven.
    if require_resolvable:
        for kind, pointer, why in list(dict.fromkeys(f.unresolved)):
            what = "row sha" if kind == "row" else "pre-registration commit"
            f.add("V11", f"{kind}[{pointer}]", f"{what} {pointer} does not "
                                               f"resolve in this repo ({why}) "
                                               "— pass --require-resolvable "
                                               "only where the measured "
                                               "commit is still reachable")

    # ── V9 the epic's closing rule ───────────────────────────────────────
    if require_green:
        red = [c for c in CRITERIA
               if (by_id.get(c) or {}).get("verdict") != "GREEN"]
        if red:
            f.add("V9", "epic #7949", f"--require-green: {len(red)}/5 rows are "
                                      f"not green ({', '.join(red)}) — the "
                                      "epic is NOT closeable")

    return f


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Check the extractor-v4 capstone verdict table (#9080).")
    ap.add_argument("log", help="the capstone log (capstone_run.py output)")
    ap.add_argument("--repo", default=".", help="repo root (default: cwd)")
    ap.add_argument("--require-resolvable", action="store_true",
                    help="FAIL when a provenance pointer (a row's sha or the "
                         "pre-registration commit) is not resolvable in this "
                         "checkout. Off by default: a squash-merged branch sha "
                         "never reaches main, so requiring it would red a "
                         "shipped bundle after merge")
    ap.add_argument("--require-green", action="store_true",
                    help="the EPIC's closing rule: exit 0 only when 5/5 rows "
                         "are green (without this, an honest all-blocked table "
                         "is clean)")
    args = ap.parse_args(argv)

    log_path = Path(args.log).resolve()
    try:
        text = log_path.read_text()
    except (OSError, UnicodeDecodeError) as e:
        # A single non-UTF-8 byte used to escape the OSError guard and surface
        # as exit 1 ("the table has findings") on a traceback.
        print(f"capstone-verdict-check: cannot read {args.log}: {e}",
              file=sys.stderr)
        return 2
    try:
        record = extract_block(text)
    except (ValueError, json.JSONDecodeError) as e:
        print(f"capstone-verdict-check: {args.log}: {e}", file=sys.stderr)
        return 2
    if not isinstance(record, dict):
        # The block parsed but is not a table (`[]`, `null`, `"x"`): a usage
        # error, not a verdict on the evidence.
        print(f"capstone-verdict-check: {args.log}: the verdict block is "
              f"{type(record).__name__}, not a table", file=sys.stderr)
        return 2

    for key, typename in _non_object_blocks(record):
        # Exit 1 means "the table has findings", and a lane reads it as a verdict
        # on the evidence. A machine block that is `[]`/`"x"`/`5` is not a
        # verdict, it is a malformed artifact — the same call the root-not-a-table
        # guard above makes. `check()` records it as V1 too, so a direct caller
        # never sees a traceback either.
        print(f"capstone-verdict-check: {args.log}: the {key!r} block is a "
              f"{typename}, not an object — the machine block is malformed",
              file=sys.stderr)
        return 2

    repo = Path(args.repo).resolve()
    if not repo.is_dir():
        # Exit 1 means "the table has findings" and a lane reads it as a
        # verdict on the evidence. An unreadable `--repo` is usage error 2;
        # without this the first `subprocess.run(cwd=…)` raised
        # FileNotFoundError and the traceback surfaced as exit 1.
        print(f"capstone-verdict-check: --repo {args.repo} is not a "
              "directory", file=sys.stderr)
        return 2
    findings = check(record, repo=repo, require_green=args.require_green,
                     require_resolvable=args.require_resolvable)
    rows = record.get("rows")
    # `record.get("rows") or []` handed a truthy non-list (`"rows": 5`) to the
    # tally loop below, which raised TypeError — exit 1 on a traceback after the
    # verdict had already been computed. `check()` reports it as V2.
    rows = rows if isinstance(rows, list) else []
    tally = {v: sum(1 for r in rows if isinstance(r, dict)
                    and r.get("verdict") == v) for v in VERDICTS}
    print(f"capstone-verdict-check: {args.log} — "
          + ", ".join(f"{v}={tally[v]}" for v in VERDICTS))
    unresolved = list(dict.fromkeys(findings.unresolved))
    if unresolved and not args.require_resolvable:
        # Say it, do not fail it: the pointer may be a squash-merged branch sha
        # that this checkout is not expected to carry. Named PER POINTER KIND,
        # because "5 row sha(s)" about five rows that DO resolve and one prereg
        # commit that does not is a wrong remediation target.
        rows_n = sum(1 for kind, _p, _w in unresolved if kind == "row")
        prereg_n = len(unresolved) - rows_n
        parts = []
        if rows_n:
            parts.append(f"{rows_n} row sha(s)")
        if prereg_n:
            parts.append(f"{prereg_n} pre-registration commit(s)")
        print(f"  note: {' + '.join(parts)} not resolvable in this checkout "
              "(expected after a squash merge; re-run with "
              "--require-resolvable where the commit exists)")
    if findings:
        print(f"❌ {len(findings.items)} finding(s):", file=sys.stderr)
        print(findings.render(), file=sys.stderr)
        return 1
    print("✅ verdict table is structurally sound: 5/5 rows carry an evidence "
          "artifact + sha, denominators agree, no row is green without a file")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
