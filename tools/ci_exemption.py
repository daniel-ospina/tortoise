"""tools/ci_exemption.py — the pre-merge EXEMPTION decision (tortoise #3756).

The pre-merge classifier used to decide ownership of a failure by MEMBERSHIP IN A
SAMPLE OF MAIN::

    unique-to-PR = (PR failing ids) - (union of main-failing ids over last N runs)

That is unsound in BOTH directions, and both directions are a defect:

* **face 1 (over-block, fail-closed).** A non-deterministic main-side failure whose
  recent main runs happened to be green reads as novel -> a clean PR is blocked.
  Costs a cycle. A COST, not a bypass.
* **face 2 (fail-OPEN, category A).** Any id that appears even ONCE in main's window
  is subtracted forever, so a PR that genuinely BREAKS that id is EXCUSED and the
  gate reports green. Reachable today.

The invariant this module exists to enforce:

    A test that fails on ``origin/main`` WITHOUT the PR's diff is NEVER PR-unique --
    and a test must never be labelled PR-unique FROM A SINGLE SAMPLE.

The fix reasons about the EFFECT ("did this PR change the failure rate?") rather
than about the sample, which makes the decision **signature-scoped**, **rate-
compared** and **visible**:

* **signature-scoped** -- an exemption covers the failure that was measured on main,
  not the whole node id. A PR that holds the same RATE while breaking a DIFFERENT
  assertion inside the same test is NOT exempt: same id, different signature.
* **rate-compared** -- a declared ``K`` on BOTH trees; presence is never sufficient.
  ``main 1/8`` vs ``PR 8/8`` is an eight-fold regression and must block; a presence
  test ("it fails on main too") would excuse it.
* **visible** -- every exemption is RECORDED with both rates. An exemption that
  exists only as an absence is the fail-open defect itself.

And the parser is **fail-closed** (#3705's rule applied to the exemption parser):
the union IS an allowlist, so junk in it is a zero-evidence pass. An unparseable or
non-nodeid line must REFUSE the exemption and be COUNTED AND REPORTED -- never
silently swallowed, never read as "no failures".

Pure functions only: no I/O, no network, no git. The caller supplies the observed
numbers; this module decides, and explains.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# --------------------------------------------------------------------------
# Strict parsing — the union is an allowlist, so junk must not enter it
# --------------------------------------------------------------------------

#: A pytest nodeid: ``path/to/test_x.py::[Class::]name`` (params allowed).
#: Deliberately anchored and narrow — anything else is REJECTED, not guessed.
_NODEID_RE = re.compile(
    r"^(?P<path>[A-Za-z0-9_./\-]+\.py)"
    r"::(?P<rest>[A-Za-z0-9_\[\]\-.:]+)$"
)

#: The only line shape the extractor accepts.
_FAILED_RE = re.compile(r"^\s*(?:FAILED|ERROR)\s+(?P<nodeid>\S+)\s*$")


@dataclass
class ParseResult:
    """Parsed failure ids plus the lines that were REJECTED, and why.

    ``rejected`` is not diagnostics — it is evidence. A non-zero count means the
    union was filtered, and the reader is entitled to see it next to ``K`` and the
    rates rather than take a silently-trimmed set on trust.
    """

    ids: list[str] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """False when the input yielded NO usable id at all.

        Fail-closed: an unreadable failure set must never present as an empty one.
        "I could not read main's failures" must not become "main has no failures",
        because that would exempt every PR failure in the other direction and, used
        as a PR set, would block nothing.
        """
        return bool(self.ids)


def parse_failed_ids(lines: str) -> ParseResult:
    """Extract test ids from ``FAILED <nodeid>`` / ``ERROR <nodeid>`` lines.

    Strict by construction:

    * a line that is not ``FAILED``/``ERROR`` shaped is **rejected** (it is not a
      failure record — e.g. a bare ``may`` token, a blank line, prose);
    * a ``FAILED`` line whose payload is not a nodeid is **rejected** (e.g.
      ``FAILED may``);
    * rejected lines are counted in ``rejected`` and never enter ``ids``.

    This is the fail-closed rule: unknown resolves to *"not exempt"*, never to
    *"exempt"*. Silent permissiveness here is a zero-evidence pass.
    """
    ids: list[str] = []
    rejected: list[str] = []
    for raw in lines.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = _FAILED_RE.match(line)
        if not m:
            rejected.append(line)
            continue
        nodeid = m.group("nodeid")
        if not _NODEID_RE.match(nodeid):
            rejected.append(line)
            continue
        ids.append(nodeid)
    return ParseResult(ids=sorted(set(ids)), rejected=rejected)


# --------------------------------------------------------------------------
# The decision — signature-scoped, rate-compared, visible
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Rate:
    """Failures observed over ``runs`` runs on one tree (PR or main)."""

    failures: int
    runs: int

    @property
    def rate(self) -> float:
        return (self.failures / self.runs) if self.runs > 0 else 0.0

    def __str__(self) -> str:  # "4/8 (50%)"
        return f"{self.failures}/{self.runs} ({self.rate:.0%})"

    def __gt__(self, other: object) -> bool:
        """Compare by RATE, so a Rate is never silently compared by identity."""
        if isinstance(other, Rate):
            return self.rate > other.rate
        return NotImplemented


@dataclass(frozen=True)
class RatesResult:
    """The rate table read from ``ci-failure-set.sh --main-union-rates``."""

    rates: dict[str, Rate] = field(default_factory=dict)
    rejected: list[str] = field(default_factory=list)

    @property
    def runs(self) -> int:
        """The declared K, or 0 when nothing was usable."""
        return max((r.runs for r in self.rates.values()), default=0)


_RATES_RE = re.compile(
    r"^(?P<nodeid>\S+)\t(?P<failures>[0-9]+)\t(?P<runs>[0-9]+)$"
)


def parse_rates(lines: str) -> RatesResult:
    """Parse the ``<nodeid>\\t<failures>\\t<runs>`` rate table.

    Fail-closed, for the same reason as :func:`parse_failed_ids`: this table feeds
    the comparison that OVERRIDES a failure, so a line the reader does not fully
    understand must never become evidence of main-side unhealth. Anything not
    exactly three tab-separated fields -- a valid nodeid, a non-negative failure
    count, and a **positive** run count -- is rejected, counted and reported.

    ``runs`` must be positive: ``X\\t0\\t0`` would give a ``0/0`` rate of ``0.0``
    and read as "main never fails this", which is the exemption-by-vacuity this
    module exists to prevent. A rejected line simply contributes no rate, and an
    id with no rate is **not exempt** (the existing default in :func:`decide`).
    """
    rates: dict[str, Rate] = {}
    rejected: list[str] = []
    for raw in lines.splitlines():
        line = raw.strip("\n")
        if not line.strip():
            continue
        m = _RATES_RE.match(line)
        if not m:
            rejected.append(line)
            continue
        nodeid = m.group("nodeid")
        failures = int(m.group("failures"))
        runs = int(m.group("runs"))
        if not _NODEID_RE.match(nodeid) or runs <= 0 or failures > runs:
            rejected.append(line)
            continue
        if nodeid in rates:
            # Duplicate rows (latent finding 2): "A 8 8" then "A 0 8" yielded
            # Rate(0,8) while the REVERSED order yielded Rate(8,8) — row order
            # decided whether the PR blocked, an order-dependence inside the
            # verdict-stability class. Reject rather than pick a winner: a table
            # that contradicts itself is not evidence, and fail-closed means the id
            # then has no rate at all (not exempt).
            rejected.append(line)
            rates.pop(nodeid, None)
            continue
        rates[nodeid] = Rate(failures=failures, runs=runs)
    return RatesResult(rates=rates, rejected=rejected)


@dataclass(frozen=True)
class Failure:
    """A PR-side failure: how often, and with which signatures."""

    rate: Rate
    signatures: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Verdict:
    """One decision, with the reason stated so it can be audited."""

    nodeid: str
    blocked: bool
    reason: str

    @property
    def line(self) -> str:
        mark = "BLOCK " if self.blocked else "EXEMPT"
        return f"{mark}: {self.nodeid}  {self.reason}"


@dataclass
class Decision:
    blocked: list[Verdict] = field(default_factory=list)
    unattributable: list[Verdict] = field(default_factory=list)
    exempt: list[Verdict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def any_blocked(self) -> bool:
        return bool(self.blocked)

    def visible_exemptions(self) -> list[str]:
        """The EXEMPT lines that MUST be recorded — never a silent exemption."""
        return [v.line for v in self.exempt]

    def report(self) -> str:
        out = [v.line for v in self.blocked] + self.visible_exemptions()
        out.extend(f"NOTE  : {n}" for n in self.notes)
        return "\n".join(out)



def class_key(nodeid: str) -> str:
    """The FILE/CLASS prefix — the unit that stays red while the IDENTITY moves."""
    parts = nodeid.split("::")
    return "::".join(parts[:2]) if len(parts) >= 2 else parts[0]


def detect_rotating_identity(runs: list[frozenset[str]]) -> dict[str, frozenset[str]]:
    """Keys whose failing IDENTITY changed between runs (required class E5).

    **An identity that changes across runs is NOT novel.** The baseline observed the
    CLASS; the id provably will not be the same next run. Measured on a real refusal
    (B6 on #3577): the rail reported ONE unique failure in a class that was red in
    three runs with THREE DIFFERENT ids inside it — run 1 two ids, runs 2-3 another.
    The class stayed red; the identity moved.

    The old re-run heuristic assumes a flake **passes** on retry and has no
    representation for "the failure moved", so the verdict became a function of which
    run you happened to sample. Both directions are in-surface:

    * **false-block** — a changed id is labelled "a NEW failure this PR introduces";
    * **false-PASS** — PR-minus-main compares SETS OF IDS from different samples, so an
      order-dependent flake can move OUT of the PR's set and INTO main's between runs
      and be exempted silently.

    Returns ``{class_key: union of ids seen red in that key}`` for every key that was
    red in >= 2 runs with a NON-CONSTANT id set. A class red in a single run is not
    reported (one sample cannot distinguish "moved" from "not yet moved").
    """
    per_key: dict[str, list[frozenset[str]]] = {}
    for r in runs:
        keys = {class_key(n) for n in r}
        for k in keys:
            per_key.setdefault(k, []).append(frozenset(n for n in r if class_key(n) == k))
    out: dict[str, frozenset[str]] = {}
    for k, sets in per_key.items():
        if len(sets) >= 2 and len({frozenset(x) for x in sets}) > 1:
            out[k] = frozenset().union(*sets)
    return out


def _signatures_overlap(pr: frozenset[str], main: frozenset[str]) -> bool:
    """True when the PR's failure matches a signature measured on main.

    When either side carries no signature information we cannot establish a match,
    and the fail-closed answer is to BLOCK rather than to exempt on an assumption.
    """
    if not pr or not main:
        return False
    # SUBSET, not intersection (review cycle 1, bypass 2). Intersection only asked
    # "do the two sets share ANY failure", so a PR that keeps main's assertion and
    # ADDS a new one was exempt — and the NEW failure, the one the PR introduced,
    # was exactly what got masked. Every signature the PR failed with must have
    # been measured on main; an extra one is a failure main never had.
    return pr <= main


def decide(
    pr_failures: dict[str, Failure],
    main_rates: dict[str, Rate],
    *,
    main_signatures: dict[str, frozenset[str]] | None = None,
    rotating: dict[str, frozenset[str]] | None = None,
    k_pr: int | None = None,
    rate_tolerance: float = 1.5,
    min_runs: int = 3,
) -> Decision:
    """Decide every PR failure: block it, or exempt it **visibly**.

    Rules, in order — each one closes a declared class:

    * id unknown to main's measurement -> **BLOCK** (no evidence of pre-existence).
    * signature disjoint from main's -> **BLOCK** (a DIFFERENT failure inside an id
      main also failed; same id is not same failure).
    * THIS id's main row measured over fewer than ``min_runs`` runs -> **BLOCK**
      (insufficient evidence; one observation cannot establish a rate). The floor is
      PER-ID and has no table-wide or caller-declared form: a ``k_main`` knob was
      removed because it was accepted and never read, which is the shape that
      produced this whole family of defects.
    * PR rate materially above main's -> **BLOCK** (the PR made it worse).
    * otherwise -> **EXEMPT**, recorded with both rates.

    ``main_rates`` is a RATE per id, not a set of ids: presence alone is never
    sufficient, because presence is what excused ``main 1/8`` against ``PR 8/8``.

    ``main_signatures`` is explicit rather than global-by-accident: an EMPTY map
    means "no signature evidence", which makes every signature check fail CLOSED.
    """
    decision = Decision()
    sig_main = main_signatures or {}

    # The floor is PER-ID and has NO table-wide form (review cycle 2). A table-max
    # derivation has no legitimate use: the question is always "does THIS id's row
    # rest on enough runs?", and when the id has no row at all `mr` is None and the
    # decision already BLOCKS. Keeping it per-id makes the bug class — a healthy
    # NEIGHBOUR licensing a thin row's exemption — impossible to express at all,
    # rather than merely un-triggered by the current call sites.
    if k_pr is not None and k_pr < 1:
        decision.notes.append("pr sample empty — treating every failure as PR-side")

    for nodeid in sorted(pr_failures):
        pr = pr_failures[nodeid]
        mr = main_rates.get(nodeid)

        # PR-side validity (latent finding 1). `Failure(rate=Rate(3, 0))` has
        # `.rate == 0.0`, so it slipped past the comparison into EXEMPT while the
        # code emitted "pr sample empty — treating every failure as PR-side", which
        # claims the opposite. The runs>0 guard covered only the main-side parser.
        if pr.rate.runs <= 0:
            decision.notes.append(
                f"PR sample for {nodeid} is empty ({pr.rate}) — a PR failure with no "
                "measured sample cannot be exempted")
            decision.blocked.append(Verdict(
                nodeid, True,
                f"PR rate is unmeasurable ({pr.rate}) — NOT exempt"))
            continue

        # Required class E5 — ROTATING IDENTITY, checked FIRST. An id whose class was
        # red across runs with a MOVING id is UNATTRIBUTABLE: never "unique to this
        # PR", never exempt-and-silent. This must precede every other rule because
        # both of the other outcomes are attributions, and the evidence here supports
        # neither.
        if rotating and nodeid in rotating.get(class_key(nodeid), frozenset()):
            decision.unattributable.append(Verdict(
                nodeid, True,
                f"UNATTRIBUTABLE: {class_key(nodeid)} was red across runs with a "
                "DIFFERENT failing id each run — a changing identity is not novel "
                "and cannot be attributed to this PR"))
            continue

        if mr is None:
            decision.blocked.append(Verdict(
                nodeid, True,
                f"no main-side measurement (PR {pr.rate}) — NOT exempt"))
            continue

        if not _signatures_overlap(pr.signatures, sig_main.get(nodeid, frozenset())):
            decision.blocked.append(Verdict(
                nodeid, True,
                f"signature differs from main's ({pr.rate} vs {mr}) — same id, "
                "a DIFFERENT failure is not exempt"))
            continue

        if mr.runs < min_runs:
            # The NOTE is emitted alongside the block (review cycle 2): the reviewer's
            # required observation is `BLOCK, with the note`, and a per-id block that
            # left `notes` empty would report the refusal without the reason.
            decision.notes.append(
                f"insufficient evidence for {nodeid}: main {mr} rests on "
                f"{mr.runs} run(s) < min_runs={min_runs} — one observation cannot "
                "establish a rate"
            )
            decision.blocked.append(Verdict(
                nodeid, True,
                f"insufficient evidence (main {mr}, {mr.runs} run(s) < "
                f"min_runs={min_runs}) — one observation cannot establish a rate"))
            continue

        # THE RATE COMPARISON — the heart of the fix. Compares the two RATES
        # (floats), never the two presences.
        #
        # No `mr.rate > 0` guard (review cycle 1, bypass 1a). Guarding on it SKIPPED
        # the comparison whenever main's rate was 0, so `main 0/8` vs `PR 8/8` — the
        # weakest possible main evidence — printed as "rates equivalent" and the
        # strongest exemption was bought with no evidence at all. A PR failure main
        # never had is a NEW failure, and `pr_rate > 0` against a zero main rate
        # blocks it.
        pr_rate = pr.rate.rate
        if pr_rate > mr.rate * rate_tolerance:
            decision.blocked.append(Verdict(
                nodeid, True,
                f"PR rate {pr.rate} materially higher than main {mr} "
                f"(>{rate_tolerance}x) — the PR made it worse"))
            continue

        decision.exempt.append(Verdict(
            nodeid, False,
            f"main {mr} vs PR {pr.rate} — rates equivalent, main measured over "
            f"{mr.runs} run(s) (min_runs={min_runs}), k_pr={k_pr}"))

    return decision


# ==========================================================================
# Step A (#3756) — the PR-side SIGNATURE producer
# ==========================================================================
#
# GROUNDED IN THE REAL ARTIFACT, not in an invented format. Measured on
# tortoise run 35223536174 (python-ci.yml, 2026-09-17) via
# ``gh run view <id> --log-failed``:
#
#   <job>\t<step>\t\ufeff2026-09-17T12:54:37.9171029Z ##[group]Run set +e
#   test (a)\tRun fast test suite\t2026-09-17T12:54:37.9171442Z ^[[36;1mset +e^[[0m
#   test (a)\tRun fast test suite\t2026-09-17T13:10:44.1728389Z ===== FAILURES =====
#   test (a)\tRun fast test suite\t2026-09-17T13:10:44.1728829Z ____ test_name ____
#   test (a)\tRun fast test suite\t2026-09-17T13:10:44.1735420Z E  +  where 200 = <Response [200 OK]>.status_code
#   test (a)\tRun fast test suite\t2026-09-17T13:10:44.1735758Z tests/test_oauth_token_fault.py:779: AssertionError
#   test (a)\tRun fast test suite\t2026-09-17T13:10:44.1763755Z FAILED tests/test_oauth_token_fault.py::test_capture... - assert (200 == 503)
#
# So the wire IS plain pytest text behind a GH Actions prefix. Two facts
# follow, and they are why this parser is shaped this way:
#
#   * the ``FAILED <nodeid> [- <detail>]`` short-summary line is the ONLY place
#     a nodeid appears in a failure record, and it already carries a one-line
#     assertion payload. It is also exactly the line ``extract_failed_tests``
#     consumes — so the id set cannot drift between the shell and this parser.
#   * the FULL failure text (the ``E`` lines and the
#     ``path.py:LINE: ExceptionType`` trailer) lives in the ``FAILURES`` block,
#     which is keyed by test NAME, not by nodeid. Attribution is therefore a
#     name join, and a name join can be ambiguous.
#
# Ambiguity is handled fail-closed: a block that joins to zero or to more than
# one id is REJECTED AND REPORTED (``unattributed``), and the id it would have
# described is left ``unsigned`` — which makes ``_signatures_overlap`` fail
# closed (BLOCK). It is never resolved by guessing.

# An ANSI SGR/CSI escape — the shell's own extractor strips these, so the
# producer must too or the two disagree on what a line says.
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

# ``<job>\t<step>\t\ufeff<ISO-8601>Z <content>`` — the gh ``--log-failed`` prefix.
# ``.*`` is greedy up to the LAST tab before the timestamp so a tab inside a job
# or step name cannot break the strip; a tab inside pytest CONTENT followed by a
# timestamp-shaped token would, which is vanishingly unlikely and fails closed
# (the content would not parse, so the id is unsigned).
_LOG_PREFIX_RE = re.compile(
    r"^.*\t\ufeff?\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z[ \t]"
)

# The short-summary line. Split on the FIRST `` - `` so a nodeid whose params
# contain a bare ``-`` (``[chromium-Claude Desktop]``) is not torn apart. The
# shell's ``-r`` short summary uses exactly this separator.
_SUMMARY_RE = re.compile(r"^(?:FAILED|ERROR)\s+(?P<nodeid>.+?)(?:\s+-\s+(?P<detail>.*))?$")

# A LOOSE nodeid: the file/class part stays strict, the ``::`` tail is allowed
# to carry spaces/brackets because pytest parameter ids legitimately do
# (``::test_x[chromium-Claude Desktop]``). ``_NODEID_RE`` stays narrow for the
# RATE/UNION parsers; widening it there would loosen their fail-closed check for
# no gain. Dropping a real id here would be fail-OPEN (an undecided id is an
# unblocked id), which is why this path is deliberately the permissive one.
_NODEID_LOOSE_RE = re.compile(r"^[A-Za-z0-9_./\-]+\.py::[^\t]+$")

# pytest's failure-block header: ``________ test_name[param] _________``.
_BLOCK_HEADER_RE = re.compile(r"^_{3,}\s*(?P<name>.+?)\s*_{3,}$")
# ``E       <assertion or exception>`` — the ``+  where …`` continuations are
# not the primary failure and are skipped.
_E_LINE_RE = re.compile(r"^E\s+(?P<rest>\S.*)$")
# ``path/to/test_x.py:779: AssertionError`` — the authoritative exception TYPE.
_TRAILER_RE = re.compile(
    r"^(?P<path>[^\s:][^\s]*\.py):(?P<line>\d+):\s*(?P<exc>[A-Za-z_][\w.]*)\s*$"
)
# A section boundary that ends the current failure block.
_BLOCK_END_RE = re.compile(r"^(?:={3,}|-{3,}|Captured\b).*$")

# Volatile substrate IDENTIFIERS, each one named by measured evidence. The mask
# list is deliberately CLOSED and documented: over-normalising is the fail-OPEN
# direction (two different failures collapsing into one signature would make a
# PR's signature set a subset of main's more easily), so nothing is masked that
# the evidence does not demand. Ids/addresses/tenants only — never data values.
_VOLATILE_NORMALIZERS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b0x[0-9a-fA-F]+\b"), "<ADDR>"),
    (
        re.compile(
            r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
            r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
        ),
        "<UUID>",
    ),
    # The declared class's ``_drill_..._416bf7c5``: an 8+ hex suffix is a
    # per-run subsystem id. ``(?<![0-9a-fA-F])`` (not ``\b``) so the underscore
    # boundary in ``_416bf7c5`` still matches, while ``assert 3 == 2`` (single
    # digits) is untouched.
    (re.compile(r"(?<![0-9a-zA-Z])[0-9a-f]{8,}(?![0-9a-zA-Z])"), "<HEX>"),
    # ``team-graph enumeration failed for team_x`` — the tenant name is the
    # declared interpolation; the stable part is its shape.
    (re.compile(r"\bteam_[A-Za-z0-9][A-Za-z0-9_]{0,63}\b"), "team_<X>"),
    (re.compile(r"\b(?:127\.0\.0\.1|localhost|0\.0\.0\.0):\d{2,5}\b"), "<HOSTPORT>"),
)


@dataclass
class SignatureParse:
    """Signature extraction for one raw ``gh run view --log-failed`` capture.

    ``unsigned`` and ``unattributed`` are EVIDENCE, not diagnostics: they are the
    rejections that make an exemption unavailable, and the swap must be able to
    see the count rather than take a silently-trimmed signature set on trust.
    """

    ids: list[str] = field(default_factory=list)
    signatures: dict[str, frozenset[str]] = field(default_factory=dict)
    rejected: list[str] = field(default_factory=list)
    unsigned: list[str] = field(default_factory=list)
    unattributed: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """False when no usable failure id was found — a capture that proves nothing."""
        return bool(self.ids)


def _strip_log_prefix(line: str) -> str:
    """Remove the gh ``--log-failed`` ``<job>\\t<step>\\t<ts>Z `` prefix, if present."""
    return _LOG_PREFIX_RE.sub("", line, count=1)


def normalize_signature(text: str | None) -> str:
    """Reduce raw failure text to the STABLE part, or ``''`` if nothing survives.

    Whitespace is collapsed first (the same assertion is rendered at different
    prefix widths between runs), then the closed volatile-identifier list above
    is masked. A result of ``''`` is a REJECTION — callers must never store it as
    a signature, because an empty signature makes the subset rule vacuous.
    """
    if not text:
        return ""
    out = " ".join(str(text).split())
    for pattern, replacement in _VOLATILE_NORMALIZERS:
        out = pattern.sub(replacement, out)
    return out.strip()


def _signature_from(e_line: str | None, exc: str | None) -> str:
    """``<ExceptionType>: <assertion>`` — the exception type is always present.

    The type is taken from pytest's ``path.py:LINE: ExceptionType`` trailer and
    prefixed only when the primary ``E`` line does not already name it, so a
    message that repeats the class (``AssertionError: family row drifted``) is
    not doubled.
    """
    core = normalize_signature(e_line)
    exc_n = normalize_signature(exc)
    if not core:
        return exc_n
    if exc_n and not core.startswith(exc_n):
        return f"{exc_n}: {core}"
    return core


def parse_pr_failure_text(text: str) -> SignatureParse:
    """Extract ``{nodeid: signature}`` from a raw ``--log-failed`` capture.

    Two passes over the same normalized lines:

    1. the ``FAILED <nodeid> [- <detail>]`` short-summary lines — the
       authoritative id set, the same lines ``extract_failed_tests`` reads;
    2. the ``FAILURES`` blocks — keyed by test NAME, joined to ids by the
       nodeid's final ``::`` segment.

    Per id the block-derived signature is preferred (it is the only source that
    carries the exception TYPE); the summary's inline detail is used ONLY when no
    block joined, so one id never contributes two competing forms. An id with
    neither is ``unsigned`` and produces NO signature entry — never a blank one.
    """
    lines = [_strip_log_prefix(_ANSI_RE.sub("", raw)) for raw in text.splitlines()]

    id_list: list[str] = []
    details: dict[str, set[str]] = {}
    rejected: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        m = _SUMMARY_RE.match(stripped)
        if not m:
            continue
        nodeid = m.group("nodeid").strip()
        if not _NODEID_LOOSE_RE.match(nodeid):
            rejected.append(stripped)
            continue
        id_list.append(nodeid)
        detail = normalize_signature(m.group("detail"))
        if detail:
            details.setdefault(nodeid, set()).add(detail)

    # Pass 2 — FAILURES blocks. A block whose name is carried by exactly one id
    # is attributed; zero or several candidates is a rejection, never a guess.
    block_sigs: dict[str, set[str]] = {}
    cur_name: str | None = None
    cur_e: str | None = None
    cur_exc: str | None = None

    def _flush() -> None:
        nonlocal cur_name, cur_e, cur_exc
        if cur_name is not None:
            sig = _signature_from(cur_e, cur_exc)
            if sig:
                block_sigs.setdefault(cur_name, set()).add(sig)
        cur_name, cur_e, cur_exc = None, None, None

    for line in lines:
        stripped = line.strip()
        header = _BLOCK_HEADER_RE.match(stripped)
        if header:
            _flush()
            cur_name = header.group("name").strip()
            continue
        if cur_name is None:
            continue
        if _BLOCK_END_RE.match(stripped):
            _flush()
            continue
        trailer = _TRAILER_RE.match(stripped)
        if trailer:
            cur_exc = trailer.group("exc")
            continue
        e_line = _E_LINE_RE.match(line)
        if e_line and cur_e is None:
            rest = e_line.group("rest").strip()
            if not rest.startswith("+"):
                cur_e = rest
    _flush()

    by_name: dict[str, list[str]] = {}
    for nodeid in sorted(set(id_list)):
        by_name.setdefault(nodeid.split("::")[-1], []).append(nodeid)

    signatures: dict[str, frozenset[str]] = {}
    unsigned: list[str] = []
    unattributed: list[str] = []
    attributed: set[str] = set()
    for name in sorted(block_sigs):
        candidates = by_name.get(name, [])
        if len(candidates) == 1:
            attributed.add(candidates[0])
            continue
        unattributed.append(name)
    for nodeid in sorted(set(id_list)):
        if nodeid in attributed:
            signatures[nodeid] = frozenset(block_sigs[nodeid.split("::")[-1]])
        elif nodeid in details:
            signatures[nodeid] = frozenset(details[nodeid])
        else:
            unsigned.append(nodeid)
    return SignatureParse(
        ids=sorted(set(id_list)),
        signatures=signatures,
        rejected=rejected,
        unsigned=unsigned,
        unattributed=unattributed,
    )


# ==========================================================================
# Step B (#3756) — the wire formats and the ``decide`` CLI
# ==========================================================================
#
# ``<nodeid>\t<failures>\t<runs>\t<signature>`` — the PR-set row. One row per
# (id, signature): an id that failed with two different signatures across runs
# contributes two rows and their signatures union. This is the SAME shape as
# ``--main-union-rates`` plus one column, so either side's producer can feed it.
_ROW_RE = re.compile(
    r"^(?P<nodeid>[^\t]+)\t(?P<failures>[0-9]+)\t(?P<runs>[0-9]+)\t(?P<sig>.*)$"
)
# ``<nodeid>\t<signature>`` — the main-side signature table. Kept separate from
# the 3-column rate table because ``parse_rates`` REJECTS a 4th column (a
# self-contradicting table is not evidence); main's rates and main's signatures
# are therefore two files.
_SIG_ROW_RE = re.compile(r"^(?P<nodeid>[^\t]+)\t(?P<sig>.*)$")


@dataclass
class FailureRowsResult:
    """The PR failure set, parsed from its wire rows."""

    failures: dict[str, Failure] = field(default_factory=dict)
    rejected: list[str] = field(default_factory=list)
    unsigned: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.failures)


def parse_failure_rows(text: str) -> FailureRowsResult:
    """Parse ``<nodeid>\\t<failures>\\t<runs>\\t<signature>`` rows.

    Fail-closed at every corner:

    * a malformed row (bad id, non-numeric fields, ``runs <= 0``,
      ``failures > runs``) is rejected and contributes NOTHING;
    * a BLANK signature is DROPPED, COUNTED and the id listed in ``unsigned`` —
      the row's rate survives, and the id then has no signature, so
      ``_signatures_overlap`` fails closed. It is never stored as ``''``;
    * two rows for one id that agree on the rate union their signatures; two
      rows that CONTRADICT each other set the id's rate to ``0/0``. That rate is
      unmeasurable, so :func:`decide` BLOCKS it (``PR rate is unmeasurable``).
      Rejecting such a row without leaving the id behind would let the id vanish
      from ``pr_failures`` — and an id the decision never sees is an id the gate
      never blocks, which is the fail-OPEN direction.
    """
    failures: dict[str, Failure] = {}
    rejected: list[str] = []
    unsigned: set[str] = set()
    for raw in text.splitlines():
        line = raw.strip("\n")
        if not line.strip():
            continue
        m = _ROW_RE.match(line)
        if not m:
            rejected.append(line)
            continue
        nodeid = m.group("nodeid")
        failures_n = int(m.group("failures"))
        runs_n = int(m.group("runs"))
        if (
            not _NODEID_LOOSE_RE.match(nodeid)
            or runs_n <= 0
            or failures_n > runs_n
        ):
            rejected.append(line)
            continue
        sig = normalize_signature(m.group("sig"))
        if not sig:
            unsigned.add(nodeid)
        existing = failures.get(nodeid)
        if existing is None:
            failures[nodeid] = Failure(
                rate=Rate(failures_n, runs_n),
                signatures=frozenset({sig}) if sig else frozenset(),
            )
            continue
        if existing.rate != Rate(failures_n, runs_n):
            # Contradictory evidence: keep the id (so it cannot vanish) but make
            # its rate unmeasurable, which is the existing BLOCK path.
            failures[nodeid] = Failure(rate=Rate(0, 0), signatures=frozenset())
            rejected.append(line)
            continue
        failures[nodeid] = Failure(
            rate=existing.rate,
            signatures=existing.signatures | (frozenset({sig}) if sig else frozenset()),
        )
    return FailureRowsResult(
        failures=failures, rejected=rejected, unsigned=sorted(unsigned)
    )


def parse_signature_rows(text: str) -> dict[str, frozenset[str]]:
    """Parse ``<nodeid>\\t<signature>`` rows, dropping blanks (counted by caller)."""
    out: dict[str, set[str]] = {}
    for raw in text.splitlines():
        line = raw.strip("\n")
        if not line.strip():
            continue
        m = _SIG_ROW_RE.match(line)
        if not m:
            continue
        nodeid = m.group("nodeid")
        sig = normalize_signature(m.group("sig"))
        if not sig or not _NODEID_LOOSE_RE.match(nodeid):
            continue
        out.setdefault(nodeid, set()).add(sig)
    return {k: frozenset(v) for k, v in out.items()}


def parse_rotation_runs(text: str) -> list[frozenset[str]]:
    """Per-run id sets, one run per non-blank line, ids whitespace-separated."""
    runs: list[frozenset[str]] = []
    for raw in text.splitlines():
        if not raw.strip():
            continue
        runs.append(frozenset(raw.split()))
    return runs


def render_signature_table(signatures: dict[str, frozenset[str]]) -> str:
    """``<nodeid>\\t<signature>`` rows, sorted — the producer's stdout."""
    return "".join(
        f"{nodeid}\t{sig}\n"
        for nodeid in sorted(signatures)
        for sig in sorted(signatures[nodeid])
    )


def _read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8", errors="replace")


def _cmd_signatures(args: argparse.Namespace) -> int:
    parsed = parse_pr_failure_text(_read(args.log))
    sys.stdout.write(render_signature_table(parsed.signatures))
    for line in parsed.rejected:
        print(f"ci-exemption: rejected FAILED line: {line}", file=sys.stderr)
    for name in parsed.unattributed:
        print(
            f"ci-exemption: unattributed FAILURES block: {name} (no unique id "
            "match — its id is unsigned and will BLOCK)",
            file=sys.stderr,
        )
    for nodeid in parsed.unsigned:
        print(
            f"ci-exemption: unsigned failure (no stable signature): {nodeid} — "
            "the subset rule will fail closed and BLOCK it",
            file=sys.stderr,
        )
    print(
        f"ci-exemption: ids={len(parsed.ids)} signed={len(parsed.signatures)} "
        f"unsigned={len(parsed.unsigned)} rejected={len(parsed.rejected)} "
        f"unattributed={len(parsed.unattributed)}",
        file=sys.stderr,
    )
    return 0 if parsed.ok else 1


def _cmd_decide(args: argparse.Namespace) -> int:
    pr = parse_failure_rows(_read(args.pr_failures))
    main = parse_rates(_read(args.main_rates))
    main_sigs = (
        parse_signature_rows(_read(args.main_signatures))
        if args.main_signatures
        else {}
    )
    rotating = (
        detect_rotating_identity(parse_rotation_runs(_read(args.rotation)))
        if args.rotation
        else {}
    )

    decision = decide(
        pr.failures,
        main.rates,
        main_signatures=main_sigs,
        rotating=rotating,
        k_pr=max((f.rate.runs for f in pr.failures.values()), default=None),
        rate_tolerance=args.rate_tolerance,
        min_runs=args.min_runs,
    )

    lines = [v.line for v in decision.blocked]
    lines += [v.line for v in decision.unattributable]
    lines += decision.visible_exemptions()
    notes = list(decision.notes)
    if not args.main_signatures and any(f.signatures for f in pr.failures.values()):
        notes.append(
            "no --main-signatures supplied: every signature check fails CLOSED "
            "(BLOCK) — this is the safe-but-wrong state, not a green"
        )
    lines += [f"NOTE  : {n}" for n in notes]

    printed = list(lines)
    for line in pr.rejected:
        printed.append(f"NOTE  : rejected PR row: {line}")
    for nodeid in pr.unsigned:
        printed.append(f"NOTE  : unsigned PR failure (no signature): {nodeid}")
    for line in main.rejected:
        printed.append(f"NOTE  : rejected main rate row: {line}")
    sys.stdout.write("\n".join(printed) + ("\n" if printed else ""))

    gated = len(decision.blocked) + len(decision.unattributable)
    verdict = (
        f"VERDICT\t{'BLOCK' if gated else 'CLEAN'}"
        f"\tblocked={len(decision.blocked)}"
        f"\tunattributable={len(decision.unattributable)}"
        f"\texempt={len(decision.exempt)}"
        f"\trejected={len(pr.rejected) + len(main.rejected)}"
        f"\tunsigned={len(pr.unsigned)}"
    )
    print(verdict)

    if args.blocked_out:
        Path(args.blocked_out).write_text(
            "".join(f"{v.nodeid}\n" for v in decision.blocked), encoding="utf-8"
        )
    if args.unattributable_out:
        Path(args.unattributable_out).write_text(
            "".join(f"{v.nodeid}\n" for v in decision.unattributable),
            encoding="utf-8",
        )
    if args.exempt_out:
        Path(args.exempt_out).write_text(
            "".join(f"{line}\n" for line in decision.visible_exemptions()),
            encoding="utf-8",
        )
    if args.verdict_out:
        Path(args.verdict_out).write_text(verdict + "\n", encoding="utf-8")

    return 1 if gated else 0


def main(argv: list[str] | None = None) -> int:
    """``python -m tools.ci_exemption <signatures|decide> …`` — the shell's door."""
    parser = argparse.ArgumentParser(
        prog="python -m tools.ci_exemption",
        description="The #3756 pre-merge exemption decision and its signature producer.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sig = sub.add_parser(
        "signatures",
        help="extract stable signatures from a raw `gh run view --log-failed` capture",
    )
    sig.add_argument("--log", required=True, help="raw --log-failed file")
    sig.set_defaults(func=_cmd_signatures)

    dec = sub.add_parser("decide", help="run the exemption decision over files")
    dec.add_argument(
        "--pr-failures",
        required=True,
        help="PR set, `<nodeid>\\t<failures>\\t<runs>\\t<signature>` rows",
    )
    dec.add_argument(
        "--main-rates",
        required=True,
        help="main rate table, `<nodeid>\\t<failures>\\t<runs>` rows",
    )
    dec.add_argument(
        "--main-signatures",
        help="main signature table, `<nodeid>\\t<signature>` rows (absent => fail closed)",
    )
    dec.add_argument(
        "--rotation",
        help="per-run id sets, one run per line (feeds detect_rotating_identity)",
    )
    dec.add_argument("--rate-tolerance", type=float, default=1.5)
    dec.add_argument("--min-runs", type=int, default=3)
    dec.add_argument("--blocked-out", help="write blocked nodeids, one per line")
    dec.add_argument("--unattributable-out", help="write unattributable nodeids")
    dec.add_argument("--exempt-out", help="write the visible EXEMPT lines")
    dec.add_argument("--verdict-out", help="write the machine-readable verdict line")
    dec.set_defaults(func=_cmd_decide)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover — exercised via main() in tests
    raise SystemExit(main())
