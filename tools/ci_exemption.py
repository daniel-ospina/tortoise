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

import re
from dataclasses import dataclass, field

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


def _signatures_overlap(pr: frozenset[str], main: frozenset[str]) -> bool:
    """True when the PR's failure matches a signature measured on main.

    When either side carries no signature information we cannot establish a match,
    and the fail-closed answer is to BLOCK rather than to exempt on an assumption.
    """
    if not pr or not main:
        return False
    return bool(pr & main)


def decide(
    pr_failures: dict[str, Failure],
    main_rates: dict[str, Rate],
    *,
    main_signatures: dict[str, frozenset[str]] | None = None,
    k_pr: int | None = None,
    k_main: int | None = None,
    rate_tolerance: float = 1.5,
    min_runs: int = 3,
) -> Decision:
    """Decide every PR failure: block it, or exempt it **visibly**.

    Rules, in order — each one closes a declared class:

    * id unknown to main's measurement -> **BLOCK** (no evidence of pre-existence).
    * signature disjoint from main's -> **BLOCK** (a DIFFERENT failure inside an id
      main also failed; same id is not same failure).
    * ``k_main`` below ``min_runs`` -> **BLOCK** (insufficient evidence; one
      observation cannot establish a rate).
    * PR rate materially above main's -> **BLOCK** (the PR made it worse).
    * otherwise -> **EXEMPT**, recorded with both rates.

    ``main_rates`` is a RATE per id, not a set of ids: presence alone is never
    sufficient, because presence is what excused ``main 1/8`` against ``PR 8/8``.

    ``main_signatures`` is explicit rather than global-by-accident: an EMPTY map
    means "no signature evidence", which makes every signature check fail CLOSED.
    """
    decision = Decision()
    sig_main = main_signatures or {}

    if k_main is not None and k_main < min_runs:
        decision.notes.append(
            f"insufficient evidence: k_main={k_main} < min_runs={min_runs} — "
            "no exemption may rest on a single sample"
        )
    if k_pr is not None and k_pr < 1:
        decision.notes.append("pr sample empty — treating every failure as PR-side")

    main_k = k_main if k_main is not None else 0

    for nodeid in sorted(pr_failures):
        pr = pr_failures[nodeid]
        mr = main_rates.get(nodeid)

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

        if main_k and main_k < min_runs:
            decision.blocked.append(Verdict(
                nodeid, True,
                f"insufficient evidence (main {mr}, k_main={main_k} < {min_runs})"))
            continue

        # THE RATE COMPARISON — the heart of the fix. Compares the two RATES
        # (floats), never the two presences.
        pr_rate = pr.rate.rate
        if mr.rate > 0 and pr_rate > mr.rate * rate_tolerance:
            decision.blocked.append(Verdict(
                nodeid, True,
                f"PR rate {pr.rate} materially higher than main {mr} "
                f"(>{rate_tolerance}x) — the PR made it worse"))
            continue

        decision.exempt.append(Verdict(
            nodeid, False,
            f"main {mr} vs PR {pr.rate} — rates equivalent at declared "
            f"k_main={main_k}, k_pr={k_pr}"))

    return decision
