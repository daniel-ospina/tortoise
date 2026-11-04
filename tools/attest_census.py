#!/usr/bin/env python3
"""attest_census.py — one read answers "does this PR's code-review proof exist, and
does it describe the code that is actually on the PR right now?"

THE QUESTION THIS EXISTS FOR
----------------------------
The `ai-review-gate` required check reds with "No AI review evidence found in PR
#N body or comments." That message has two very different causes and they must
never be confused:

  * REPAIR — a review really happened, a record really exists on a box
    (`~/.pi/agent/reviews/<owner>-<repo>-<pr>.json`) bound to the PR's CURRENT
    head, and only its published copy is missing (a 403 refused the post). The
    proof can be re-posted verbatim.
  * FABRICATION — no record, or a record bound to a head the code has since
    moved past. There is nothing to re-post: the honest answer is a fresh review
    (re-review the DELTA), never a copied verdict.

This tool decides which, mechanically, from three facts only:

  1. record exists?                       (canonical path, then the legacy `<pr>.json`)
  2. record.head_sha == PR headRefOid?    (byte-for-byte, 40 lowercase hex)
  3. record carries a REAL verdict with ARTIFACT-SHAPED evidence — `verdict` in the clean
     allow-list (`clean` / `clean-micro` / `clean-low`), `evidence` matching `_EVIDENCE_REF`
     (`<kind>:<digits>` or a GitHub `#issuecomment-`/`#discussion_r`/`#pullrequestreview-` URL),
     and `mint` not carried.

     ⛔ Not "a non-empty string": that weaker rule is exactly what cycle 5 measured as fail-open
     (`"TODO: verify"`, `"evidence: none"`, `" : "` all licensed `RESTORE` for a record with no
     artifact at all). Weakening `real_evidence` back to a non-empty check re-opens fabrication.

BINDING IS BY HEAD, DELIBERATELY NARROWER THAN THE GATE
-------------------------------------------------------
`ai-review-gate` also accepts a marker bound to the current **diff** digest (#1351), so a record at
an older head whose diff still matches can still satisfy the gate. This census does not: its job is
"does a verdict describe the code that is live now", and the safe direction is `RE-REVIEW` (pay for
a review) rather than `RESTORE` (re-publish an unmatched verdict). Rows may therefore UNDER-report
restorable rows — never over-report them.

⛔ It never mints, never writes a record, and never posts anything. It is a
READER. A `RESTORE` row means "the artifact you would re-post already exists and
describes the live head" — a human or lane still has to do the posting, and must
quote the record's own verdict verbatim.

WHY THE GATE STATE IS READ PER-COMMIT, NOT FROM THE ROLLUP
----------------------------------------------------------
`gh pr list --json statusCheckRollup` looks cheaper (one call) but is the wrong
instrument twice over: a re-run ADDS a red attempt rather than clearing one, so
the rollup of a green commit can carry old `FAILURE` contexts; and the newest
attempt per check NAME is only resolvable by check-run `id`. So the gate state is
read the way the fleet's recorded rule says to read it
(`docs/plans/2026-09-26-5215-merge-throughput.md` §622: group per check, newest
attempt by `id`) — newest attempt by id, over `filter=all` — scoped with `check_name=` so the payload stays
small enough for a rate-limited box. `--no-gate` drops only the PER-PR check-run reads (the
expensive half); `gh pr list` is still required, so a fully offline run produces no report.

USAGE
-----
    uv run python tools/attest_census.py                    # tortoise open PRs → operations/attest-census.md
    uv run python tools/attest_census.py --no-gate          # registry only (no API calls but `gh pr list`)
    uv run python tools/attest_census.py --json             # machine-readable rows on stdout
    uv run python tools/attest_census.py --repo daniel-ospina/agent-infra

Exit code is 0 for a completed census (this is a report, not a gate) and 2 when a
surface could not be read — never 1, so a caller cannot mistake "unreadable" for
"found a problem".
"""
from __future__ import annotations

import sys

# #5128 shape: refuse a <3.12 interpreter BEFORE the imports below. This is load-bearing here
# rather than cosmetic — `from datetime import UTC` is a 3.11+ stdlib name, so on the ambient
# `/usr/bin/python3` the tool would die with an unattributed ImportError before any of its own
# messaging ran. The gate that enforces this shape is tests/test_entry_point_python_guard.py.
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    # The exit code is 2, not `SystemExit(<str>)`'s 1: this file publishes `never 1` (0 completed /
    # 2 unreadable), so a refusal reported through 1 would be indistinguishable, by exit code,
    # from "the census found a problem" — and an old interpreter is an environment error.
    print(
        f"tools/attest_census.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/attest_census.py`",
        file=sys.stderr,
    )
    raise SystemExit(2)

import argparse
import contextlib
import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote, urlsplit

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
GATE_CHECK = "ai-review-gate"

# Verdicts that are a *clean* claim. Kept as a set rather than a substring test:
# "NOT CLEAN" contains "CLEAN", and this file exists to refuse exactly this kind
# of vocabulary accident.
CLEAN_VERDICTS = {"clean", "clean-micro", "clean-low"}

# A check-run conclusion that is neither red nor pending. `cancelled`/`stale` describe an attempt that
# never answered, so they are NON-RED. Every OTHER conclusion — including one GitHub has not documented
# yet — is RED: an unnamed value must never fall through a gap and read as green (`read_gate`).
_NON_RED_CONCLUSIONS = {"success", "neutral", "skipped", "cancelled", "stale"}


def _is_carried(mint: str) -> bool:
    """Normalised, because this guard is FAIL-OPEN if it ever misses.

    Measured by review cycle 3: `mint` = `"Carried"` / `"CARRIED"` / `" carried"` / `"carried "`
    all reached `RESTORE` — a restorable proof manufactured from a pointer to ANOTHER review
    (#10177). Everything else in this file normalises; this did not.
    """
    return mint.strip().lower() == "carried"


# An evidence reference is an ARTIFACT POINTER, not prose: `<kind>:<numeric id>` or a GitHub URL
# carrying one. Cycle 5 measured that the previous test (`":" in evidence`) accepted
# `"TODO: verify"` / `"evidence: none"` / `" : "` as evidence and licensed `RESTORE` for a record
# with no artifact at all — the docstring's rule, unimplemented.
_EVIDENCE_REF = re.compile(
    r"^(?:comment|issuecomment|review|review-comment|discussion):\d+$"
    # ⛔ github.com ONLY: the docstring promises a GitHub artifact, and cycle 9 measured
    # `https://example.com/#issuecomment-9` (and bitbucket/localhost) licensing RESTORE — a pointer
    # whose SHAPE was checked but which resolves to no artifact on any repo.
    r"|^https?://(?:www\.)?github\.com/[^#\s]+/[^#\s]+#(?:issuecomment-|pullrequestreview-|discussion_r)\d+$"
)

# ⛔ The slug of a GitHub-URL pointer, so the URL arm can be checked against the PR it claims to be
# evidence FOR (review cycle 22). `_EVIDENCE_REF` validates the SHAPE — host, a two-segment path, an
# artifact anchor — and a shape cannot say WHOSE artifact it is: measured, a record declaring this
# repo and this PR, carrying `evidence` = `https://github.com/evil-org/evil-repo#issuecomment-1` or
# `…/daniel-ospina/tortoise/pull/999#discussion_r5`, both reached `RESTORE`. `RESTORE` tells a lane to
# re-post that pointer as THIS PR's proof, so a URL naming another repo or PR is the laundering this
# tool exists to refuse — `load_record` already refuses a record whose own `repo`/`pr` FIELDS
# disagree (cycles 4/5); this is the same rule on the field that is actually re-posted.
# ⛔ The ATTRIBUTION is parsed with `urlsplit`, not from this regex's path group (review cycle 23).
# Two shapes defeated the first version, each still licensing `RESTORE` for a foreign artifact:
#   `…/daniel-ospina/tortoise/../evil-org/evil-repo#issuecomment-1`  — the first two segments are
#     this repo, so the slug test passed and the REST of the path was never looked at, while the URL
#     actually normalises to a DIFFERENT repo;
#   `…/daniel-ospina/tortoise/pull/999?x=1#issuecomment-1`           — `/pull/(\d+)(?:/|$)` did not
#     match across a query string, so "no PR number found" was read as "no PR to disagree with".
# So the regex is now only the "is this a github.com URL" probe; the path is split into SEGMENTS and
# percent-DECODED first, traversal segments are refused outright, and a page that is neither
# `/pull/<n>` nor `/issues/<n>` cannot be this PR's artifact at all.
_GITHUB_URL_SLUG = re.compile(r"^https?://(?:www\.)?github\.com/(?P<owner>[^/#\s]+)/(?P<repo>[^/#\s]+)(?P<path>/[^#\s]*)?#")


def _evidence_defect(evidence: object, repo: str, pr: int) -> str:
    """`""` when `evidence` can be THIS PR's proof, else the reason it cannot.

    ONE function for both call sites — `Record.real_evidence` (which decides `RESTORE`) and
    `classify`'s reason text — because a rule that exists on one of two sibling paths is a coin
    flip, not a contract (the lesson `_emit_report` records at its own definition).

    The slug test is skipped for a pointer that carries none (`comment:<id>`) and for a `Record`
    with no attribution (`repo` empty): the census cannot invent an attribution the artifact does
    not state. Only a record the READER produced carries one, and that is the one that reaches a
    lane.
    """
    if not isinstance(evidence, str):
        return f"evidence ({evidence!r}) is not a string"
    ref = evidence.strip()
    if _EVIDENCE_REF.match(ref) is None:
        return f"evidence ({evidence!r}) is not an artifact pointer — it cannot be quoted as proof"
    if not repo:
        return ""  # a hand-built `Record`: nothing to attribute it to, so the shape test decides
    if not ref.lower().startswith(("http://", "https://")):
        # `_EVIDENCE_REF` admits exactly two forms, so a non-URL here IS a `<kind>:<id>` pointer. It
        # states no slug, so there is nothing for it to disagree with — the census must not invent an
        # attribution the artifact does not carry.
        return ""
    if _GITHUB_URL_SLUG.match(ref) is None:
        # ⛔ FAIL CLOSED on a URL this guard cannot parse (review cycle 25, P1). The previous arm read
        # "the slug regex did not match" as "attributed, nothing to check" — and `_EVIDENCE_REF`
        # accepts shapes this regex rejects, because its two path segments may contain a `/`. GitHub
        # COLLAPSES an empty segment, so `github.com/daniel-ospina//agent-infra#issuecomment-1`
        # resolves, 200 OK, to a DIFFERENT repository:
        #     curl "https://github.com/daniel-ospina//agent-infra" → <title>… daniel-ospina/agent-infra
        # but the canonical spelling of that same repo is refused two lines below. Measured: both `//`
        # shapes and `github.com//evil-org/evil-repo/pull/1#…` reached `RESTORE`. A guard that fails
        # open on the input it cannot read is not a guard.
        return (
            f"evidence ({evidence!r}) carries no attributable repository — this census cannot tell "
            "which repo it points at, so re-posting it could quote another repository's artifact"
        )
    # ⛔ Repeated slashes needed no collapsing: the `if s` below already drops empty segments, and
    # `%2e%2e` needs the `unquote` (a server decodes it; a naive split does not). An earlier cycle put
    # a `re.sub(r"/{2,}", …)` here and credited it with the `//` refusals — it was INERT (measured
    # review cycle 26: 1,000 combinatorial paths + 200,000 random, 0 differences), and the refusals are
    # carried by the fail-closed `_GITHUB_URL_SLUG is None` arm above. Deleted rather than kept as
    # decoration: a line a comment credits with a guard it does not perform is the claim/artifact
    # mismatch this file keeps having to fix.
    segs = [s for s in unquote(urlsplit(ref).path).split("/") if s]
    if any(s in (".", "..") for s in segs):
        return (
            f"evidence ({evidence!r}) carries a path-traversal segment — it normalises to a "
            "different artifact, so re-posting it would not quote this PR's proof"
        )
    if len(segs) < 2:
        return ""  # nothing to attribute; `_EVIDENCE_REF` already decided it is artifact-shaped
    slug = f"{segs[0]}/{segs[1]}"
    if slug.lower() != repo.lower():
        return (
            f"evidence ({evidence!r}) points at {slug}, not {repo} — re-posting it would quote "
            "another repository's artifact as this PR's proof"
        )
    rest = segs[2:]
    if not rest:
        return ""  # `<repo>#<anchor>`: no page to disagree with
    if len(rest) < 2 or rest[0] not in ("pull", "issues") or not rest[1].isdigit():
        return (
            f"evidence ({evidence!r}) points at a {slug} page that is not a pull request — "
            "re-posting it would quote an artifact that is not this PR's review proof"
        )
    # ⛔ `isdigit()` is NOT sufficient to make `int()` safe (review cycle 25, P2): `'²'`, `'³'` and
    # `'⑧'` are all `isdigit()` true and `int()` INVALID, and an ASCII run past CPython's 4300-digit
    # conversion limit is too. So the conversion is guarded, because it is reached from
    # `real_evidence` (a property) and from `classify` — both called OUTSIDE every handler in this
    # file — and the `ValueError` therefore escaped as a traceback and **rc 1** for the whole census,
    # on one record, from the very contract that says a record can never do that.
    try:
        page_pr = int(rest[1])
    except ValueError:
        return (
            f"evidence ({evidence!r}) carries a PR segment that is not a number — this census cannot "
            "tell which PR it points at, so it cannot be quoted as this PR's proof"
        )
    if pr and page_pr != pr:
        return (
            f"evidence ({evidence!r}) points at {slug} PR #{rest[1]}, not #{pr} — re-posting "
            "it would quote another PR's artifact as this PR's proof"
        )
    return ""

# A bare URL is MD034, and `operations/attest-census.md` is linted whole by the REQUIRED `docs` job
# with NO baseline key for that path — so one URL in one record turns a regenerated artifact into a
# red check the lane did not cause (#10687 cycle 22, measured). `_cell` wraps every match in an
# autolink, which is not a bare URL. The lookbehind skips a value that already carries an autolink
# (so a corrupt record cannot produce `<<…>>`); trailing sentence punctuation is peeled out of the
# link by `_autolink` (see there), because a GREEDY URL match always absorbs it.
_BARE_URL = re.compile(r"(?<!<)(?P<url>https?://[^\s<>]+)")


def _autolink(text: str) -> str:
    """Wrap each bare URL in `text` as a GFM autolink (`<url>`), leaving trailing punctuation out.

    ⛔ The split is done in Python, not in the regex, and that is the point: a LAZY URL group stops at
    the first position where the rest of the pattern can match, which produced `<https://g>` followed
    by the rest of the URL verbatim (measured, cycle 22 — the first version of this function). A
    GREEDY group is correct for the URL but then always absorbs a trailing `.`, so the punctuation is
    peeled off by `rstrip` afterwards.
    """

    def _repl(match: re.Match[str]) -> str:
        url = match.group("url")
        stripped = url.rstrip(".,;:!?]")
        # ⛔ A trailing `)` is punctuation only when it CLOSES nothing. A URL whose own path carries
        # an open paren legitimately ends in `)` (`…/wiki/Foo_(bar)`), and a blanket
        # `rstrip(".,;:!?)]")` peels it and points the link at a DIFFERENT resource — measured
        # (#10687 cycle 22). So the paren is peeled while, and only while, the parens are unbalanced.
        while stripped.endswith(")") and stripped.count("(") < stripped.count(")"):
            stripped = stripped[:-1].rstrip(".,;:!?]")
        # ⛔ A scheme with no host (`https://`) is not a URL, and peeling it to `https://` to then
        # wrap THAT emits `<https://>`, which is not a link at all. Keep the value verbatim and
        # autolink it as-is: an autolink is not a bare URL (MD034), and `_cell`'s job is to preserve
        # even a corrupt record readably, never to invent or delete part of it.
        if not stripped.split("://", 1)[-1]:
            return f"<{url}>"
        return f"<{stripped}>{url[len(stripped):]}"

    return _BARE_URL.sub(_repl, text)

RESTORE = "RESTORE"
RE_REVIEW = "RE-REVIEW"
NOTHING = "NOTHING-TO-RESTORE"
OK = "OK"
IN_FLIGHT = "IN-FLIGHT"
UNREADABLE = "UNREADABLE"


class RegistryUnreadable(RuntimeError):
    """The registry DIRECTORY could not be listed.

    ⛔ This must never be folded into "no record": a missing or unreadable
    `--reviews-dir` makes every PR look unrestorable, which is the same
    absence-as-verdict error the census exists to prevent, at directory scale.
    """



@dataclass
class Record:
    path: Path
    head_sha: str = ""
    mint: str = ""
    verdict: str = ""
    evidence: object = None
    diff_sha256: str = ""
    reviewed_at: str = ""
    # The attribution `load_record` resolved this record to (`owner/repo`, PR number), carried so
    # `real_evidence` can hold a GitHub-URL `evidence` to the same artifact its record claims to be
    # (review cycle 22). Empty for a hand-built `Record` — which is exactly the case where there is
    # nothing to check against, and the shape test alone decides.
    repo: str = ""
    pr: int = 0
    error: str = ""

    @property
    def real_evidence(self) -> bool:
        """The record attests that a reviewer actually reported something.

        Requires ALL of: a verdict in the clean allow-list, an evidence reference that is an
        ARTIFACT SHAPE (`<kind>:<id>` or a GitHub URL carrying one), and `mint` not carried. The
        mint exclusion is deliberate: #10177 measured a carry re-binding an older `clean` onto a
        head whose own review found a P1 — a carried record is a *pointer to another review*, and
        re-posting it as proof of this head is the laundering this tool exists to refuse.

        The match is `_EVIDENCE_REF`, not a bare colon test: cycle 5 measured `"TODO: verify"`,
        `"evidence: none"` and `" : "` passing a colon check and licensing RESTORE for a record
        with no artifact at all (a stringified empty like `"null"`/`"n/a"` is colon-free, so this
        rule also subsumes the null-ish cases without a separate list that can never fire).
        """
        return (
            bool(self.verdict)
            and self.verdict in CLEAN_VERDICTS
            and _evidence_defect(self.evidence, self.repo, self.pr) == ""
            and not _is_carried(self.mint)
        )


@dataclass
class Row:
    pr: int
    head: str
    branch: str
    title: str
    record: Record | None
    gate_status: str = ""
    gate_conclusion: str = ""
    gate_checked: bool = False
    outcome: str = ""
    why: str = ""
    notes: list[str] = field(default_factory=list)


# ── pure classifier (unit-tested) ────────────────────────────────────────────


def classify(
    head: str, record: Record | None, gate_status: str, gate_conclusion: str, gate_known: bool = True
) -> tuple[str, str]:
    """Return (outcome, why) for one PR. No I/O — this is the whole rule.

    Precedence, and why: a record we could not READ or could not TIE to a head is
    `UNREADABLE`, never `NOTHING-TO-RESTORE` — the latter asserts "there is no
    artifact", and a failed read is not evidence of absence.
    """
    gate_green = gate_known and gate_conclusion in ("success", "neutral", "skipped")
    gate_in_flight = gate_known and gate_status not in ("", "completed")

    if record is not None and record.error:
        return UNREADABLE, f"record {record.path.name} could not be used: {record.error}"

    if record is None:
        if gate_green:
            return OK, "gate green; no local record (evidence lives on GitHub only)"
        if gate_in_flight:
            return IN_FLIGHT, "gate has not concluded"
        # The marker half of the story depends on what was ACTUALLY read, so `main` appends it.
        return NOTHING, "no record on this box at any head"

    rec_head = record.head_sha
    if not SHA_RE.match(rec_head or ""):
        return UNREADABLE, (
            f"record {record.path.name} carries no usable head_sha ({rec_head!r}) — it cannot be "
            "bound to this head, so it is neither restorable nor provably absent"
        )
    if rec_head == head and record.real_evidence:
        if gate_green:
            return OK, f"record binds live head and carries {record.verdict}/{record.evidence}"
        if gate_in_flight:
            return IN_FLIGHT, "record binds live head; gate has not concluded"
        return RESTORE, (
            f"record binds the live head with {record.verdict} + evidence {record.evidence} — "
            "the artifact exists; re-post its own verdict verbatim"
        )

    if rec_head and rec_head != head:
        if gate_green:
            return OK, f"gate green (record is stale at {rec_head[:12]}…, irrelevant)"
        return RE_REVIEW, f"record binds {rec_head[:12]}…, PR head is {head[:12]}… — the code moved"

    # Record exists, head matches (or is unreadable), but the verdict/evidence
    # is not a review of this code.
    if gate_green:
        return OK, "gate green"
    if gate_in_flight:
        return IN_FLIGHT, "gate has not concluded"
    if _is_carried(record.mint):
        return RE_REVIEW, "record is mint:\"carried\" — a pointer to an older review, not evidence of this head (#10177)"
    if record.evidence in (None, "", False):
        return NOTHING, "record carries no evidence (evidence: null) — nothing to restore"
    # ⛔ Name the ACTUAL defect: a clean verdict with an unusable pointer is not "a non-clean
    # verdict" (cycle 6: the row asserted the opposite of its own `verdict` column, so a lane
    # concluded the review had found issues and re-reviewed for the wrong reason).
    defect = _evidence_defect(record.evidence, record.repo, record.pr)
    if defect:
        # ⛔ Do NOT assert a verdict here: this branch fires on the EVIDENCE test, and cycle 7
        # measured the claim "is clean" printed beside `verdict=ISSUES` — which sends a lane to
        # repair a pointer on a review that actually found issues, without reading the findings.
        # The message is `_evidence_defect`'s, so the row names the ACTUAL defect: a shape failure
        # and a "resolves to a different artifact" failure read identically today only because
        # both are described by the one function that decides both.
        return NOTHING, f"record verdict {record.verdict!r} at the live head, but its {defect}"
    return NOTHING, f"record verdict {record.verdict!r} is not a clean verdict — nothing to restore"


# ── readers ──────────────────────────────────────────────────────────────────


def load_record(
    reviews_dir: Path, owner: str, repo: str, pr: int
) -> Record | None:
    """Find this PR's record, or None when the registry genuinely has none.

    ⛔ Three things this must never do, each a measured defect:
      * fold an UNREADABLE registry into "no record" — raises `RegistryUnreadable`;
      * fold a CORRUPT record into "no record" — returns a Record carrying `.error`
        (which `classify` maps to `UNREADABLE`, never `NOTHING-TO-RESTORE`);
      * accept another repo's/PR's record — a record whose own `repo`/`pr` fields
        disagree is returned with `.error`, so it can never become evidence.
    Filenames are matched CASE-INSENSITIVELY: `gh` treats `DMeer` and `dmeer` as the
    same repo while the registry path is case-sensitive, so an exact-case lookup
    reports a false absence (measured on `daniel-ospina-DMeer-*.json`).
    """
    try:
        entries = [e for e in reviews_dir.iterdir() if e.name.lower().endswith(".json")]
    except FileNotFoundError as exc:
        raise RegistryUnreadable(f"registry directory does not exist: {reviews_dir}") from exc
    except OSError as exc:
        raise RegistryUnreadable(f"registry directory cannot be listed: {reviews_dir} ({exc})") from exc

    wanted = [f"{owner}-{repo}-{pr}.json".lower(), f"{pr}.json"]
    chosen: Path | None = None
    canonical = False
    for idx, want in enumerate(wanted):
        for entry in entries:
            if entry.name.lower() == want:
                chosen = entry
                canonical = idx == 0
                break
        if chosen is not None:
            break
    if chosen is None:
        return None

    try:
        # ⛔ Pinned to UTF-8, not the ambient locale (review cycle 14). JSON is UTF-8 by RFC 8259 and
        # `record-review.sh` writes it that way, so under a C locale the locale default turns a
        # perfectly good record into a false `UNREADABLE` — under-reporting a restorable proof.
        raw = chosen.read_text(encoding="utf-8")
    except (OSError, ValueError) as exc:
        # OSError: permission denied, dangling link, a DIRECTORY named *.json.
        # ValueError: invalid UTF-8 (`UnicodeDecodeError`) — cycle 3 measured it escaping as rc 1.
        return Record(path=chosen, error=f"unreadable: {exc}")
    try:
        data = json.loads(raw)
    except ValueError as exc:
        return Record(path=chosen, error=f"unreadable: not JSON ({exc})")
    if not isinstance(data, dict):
        return Record(path=chosen, error=f"unreadable: top-level JSON is {type(data).__name__}, not an object")

    rec = Record(
        path=chosen,
        head_sha=str(data.get("head_sha") or ""),
        mint=str(data.get("mint") or ""),
        verdict=str(data.get("verdict") or data.get("outcome") or ""),
        evidence=data.get("evidence"),
        diff_sha256=str(data.get("diff_sha256") or ""),
        reviewed_at=str(data.get("reviewed_at") or ""),
    )
    # Identity: a record names its own PR and repo; one that disagrees is another
    # artifact entirely (mirrors/forks/lineage repos collide on small PR numbers).
    # ⛔ The LEGACY `<pr>.json` name carries no repo at all, so a legacy record that does not
    # DECLARE one cannot be attributed to this repo — and an unattributable artifact that reaches
    # RESTORE would have a lane re-post `comment:<id>` from another repo's PR. Under-report
    # (UNREADABLE), never over-report (cycle 4, finding 5).
    rec_repo = data.get("repo")
    declared_repo = isinstance(rec_repo, str) and rec_repo.strip() != ""
    if declared_repo and rec_repo.strip().lower() != f"{owner}/{repo}".lower():
        rec.error = f"foreign record: repo={rec_repo!r}, census repo is {owner}/{repo}"
    elif not canonical and not declared_repo:
        rec.error = (
            f"legacy record {chosen.name} declares no repo — it cannot be attributed to "
            f"{owner}/{repo}, so it is not evidence"
        )
    # Attribution for the EVIDENCE pointer, which is the field a lane actually re-posts: a record
    # that passed every check above IS this PR's, so its `evidence` URL must name this PR too.
    # Unconditional, including on an `error` record — `classify` returns before reading evidence
    # for those, so it can only ever under-report, never licence a cross-artifact RESTORE.
    rec.repo = f"{owner}/{repo}"
    rec.pr = pr
    rec_pr = data.get("pr")
    if rec_pr is not None and str(rec_pr).strip() != str(pr):
        # NOT `isinstance(rec_pr, int)`: cycle 5 measured a record declaring `"pr":"8"` escaping
        # the check and reaching RESTORE for PR 7 — a re-post of PR 8's `comment:<id>` as PR 7's
        # proof. Any declared pr that is not this pr is foreign, whatever its JSON type.
        rec.error = f"foreign record: pr={rec_pr!r}, census pr is {pr}"
    return rec


def gh_json(args: list[str]) -> object:
    """Run `gh` and parse JSON. Every failure mode — gh missing, non-zero rc, non-JSON body —
    becomes a RuntimeError, never an uncaught OSError/ValueError: the exit-code contract
    (0/2, never 1) depends on it (cycle 3 measured `FileNotFoundError` escaping as rc 1)."""
    try:
        # ⛔ Pinned to UTF-8, not the ambient locale (review cycle 22). `gh --json` emits raw UTF-8
        # (`—` in a PR title, an accented branch), and `text=True` alone decodes with
        # `locale.getpreferredencoding(False)`: under `LC_ALL=C PYTHONUTF8=0` that is `ascii`, so the
        # WHOLE census died on `'ascii' codec can't decode byte 0xe2` (rc 2, "could not list PRs")
        # for a surface that had answered perfectly. The record read is pinned for exactly this
        # reason a few functions above — a guard on one of two sibling readers is a coin flip.
        out = subprocess.run(["gh", *args], capture_output=True, text=True, encoding="utf-8")
    except OSError as exc:  # gh not installed / not executable
        raise RuntimeError(f"could not run gh ({' '.join(args[:2])}…): {exc}") from exc
    except ValueError as exc:  # a non-UTF-8 stdout body — UnicodeDecodeError is a ValueError
        raise RuntimeError(f"gh {' '.join(args[:2])}… returned undecodable output: {exc}") from exc
    if out.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args[:3])}… failed rc={out.returncode}: {out.stderr.strip()[:300]}")
    try:
        return json.loads(out.stdout)
    except ValueError as exc:
        raise RuntimeError(f"gh {' '.join(args[:3])}… returned non-JSON ({exc}): {out.stdout[:200]!r}") from exc


def read_gate(repo: str, head: str) -> tuple[str, str]:
    """Newest `ai-review-gate` check-run on the exact head, by id (never the rollup)."""
    payload = gh_json(
        [
            "api",
            "-X",
            "GET",
            f"repos/{repo}/commits/{head}/check-runs",
            "-f",
            f"check_name={GATE_CHECK}",
            "-f",
            "per_page=100",
            "-f",
            "filter=all",
        ]
    )
    # Shape-validate: a valid-JSON-but-wrong-payload must be a TRANSPORT verdict (RuntimeError →
    # UNREADABLE → rc 2), never an AttributeError escaping as rc 1 (cycle 3, measured five shapes).
    runs_raw = payload.get("check_runs", []) if isinstance(payload, dict) else None
    if (
        not isinstance(payload, dict)
        or not isinstance(runs_raw, list)
        or not all(isinstance(r, dict) for r in runs_raw)
    ):
        raise RuntimeError(f"check-runs payload for {head[:12]}… has an unexpected shape: {type(payload).__name__}")
    runs = [r for r in runs_raw if r.get("name") == GATE_CHECK]
    if not runs:
        return "", ""
    # ⛔ NEWEST BY `id`, never by list position: the endpoint answers newest-first, so
    # `runs[-1]` would read the OLDEST attempt — the rollup error requirement (c) forbids
    # (a re-run ADDS an attempt; the oldest may be a stale `cancelled`/`failure`).
    # ⛔ ANY unorderable attempt is a TRANSPORT verdict, not just an all-unorderable payload
    # (review cycle 22). Dropping a non-integer id and taking `max` of the remainder silently reads
    # an OLDER attempt — measured: a payload whose NEWEST attempt had `id: null` and whose older one
    # was green reported `('completed', 'success')`, so the row read "gate green; no re-post owed"
    # on a head whose newest gate attempt was `failure`. That is the rollup error §622 forbids,
    # reached from the other side, and the previous test only covered a single-element payload.
    if any(not isinstance(r.get("id"), int) for r in runs):
        raise RuntimeError(f"check-runs payload for {head[:12]}… carries a non-integer id — cannot order attempts")
    # ⛔ GROUP BEFORE ORDERING, or one workflow's attempt masks another's. `docs/plans/2026-09-26-5215-
    # merge-throughput.md` §622 and AGENTS.md "Reading verification state" require grouping by
    # `(app.slug, workflow, job name)` precisely because "two workflows can publish the same job name —
    # grouping on the name alone merges them, so the newest of one masks the other". The workflow is
    # not in this payload, but the RUN ID inside `details_url` pins it (one run = one workflow), so the
    # key is `(app.slug, run id)`. Measured (review cycle 24): a NAME-ONLY read answered
    # `('completed', 'success')` for a pair whose OTHER group was red — the rollup error this rule
    # forbids, reached from the grouping side. Today only one job emits this name, so that was latent
    # rather than a wrong row; the rule is implemented anyway, because a latent false GREEN on the
    # attestation gate is the exact failure this tool exists to refuse.
    def _group(run: dict) -> tuple[str, str]:
        app = run.get("app")
        slug = app.get("slug") if isinstance(app, dict) else None
        # ⛔ The canonical pattern, not a narrower one of our own (review cycle 26): the run id may end
        # the URL, or be followed by `/job/<id>`, `/`, `?` or `#`. `tools/ci_verdict.py`'s `RUN_ID_RE`
        # and `tools/merge_throughput.py` both accept those, so a narrower one here would classify a
        # resolvable workflow as unresolvable and (after the fallback below) turn a real GREEN into
        # UNKNOWN — an over-block of exactly the rows the census exists to report.
        match = re.search(r"/actions/runs/(\d+)(?=[/?#]|$)", str(run.get("details_url") or ""))
        return str(slug or ""), (match.group(1) if match else "")

    newest_per_group: dict[tuple[str, str], dict] = {}
    for run in runs:
        key = _group(run)
        if key not in newest_per_group or run["id"] > newest_per_group[key]["id"]:
            newest_per_group[key] = run
    contenders = list(newest_per_group.values())
    # ⛔ POLARITY ACROSS GROUPS, exactly as the recorded rule states it: a completed RED in ANY group
    # is final and nothing in flight can un-fail it; otherwise an un-concluded group means the answer
    # is not yet known; otherwise the groups agree the head is non-red. Red is decided by an ALLOW-LIST
    # of non-red conclusions, so a conclusion nobody has named yet cannot fall through a gap and read
    # as green.
    red = [
        r for r in contenders
        if r.get("status") == "completed" and str(r.get("conclusion") or "") not in _NON_RED_CONCLUSIONS
    ]
    if red:
        newest = max(red, key=lambda r: r["id"])
    else:
        in_flight = [r for r in contenders if r.get("status") != "completed"]
        newest = max(in_flight or contenders, key=lambda r: r["id"])
    # ⛔ §622's FALLBACK, which this function was only half-implementing (review cycle 26). The rule
    # reads: *"an unresolvable `details_url` makes that group `UNKNOWN` (exit 2), **never GREEN**"*,
    # and the repo's two canonical readings of it say the same in code — `tools/merge_throughput.py`
    # (*"a GREEN is unprovable and is UNKNOWN, never GREEN"*) and `tools/ci_verdict.py`. Measured with
    # two attempts sharing `details_url: null`, an older red and a newer green: this function answered
    # `('completed', 'success')` where `merge_throughput` answered `UNKNOWN`, and `classify` turned
    # that into `OK` — "gate green; no re-post owed" — on the attestation gate itself.
    #
    # ⛔ Only a GREEN is refused. A red or an un-concluded answer is NOT a positive claim about the
    # workflow, so it stays as it was: turning a real red into UNREADABLE would over-block in the
    # direction that hides a refusal a lane needs to see.
    if (
        _group(newest)[1] == ""
        and str(newest.get("status") or "") == "completed"
        and str(newest.get("conclusion") or "") in _NON_RED_CONCLUSIONS
    ):
        raise RuntimeError(
            f"check-run {GATE_CHECK} on {head[:12]}… has no resolvable workflow identity "
            "(details_url) — a group this census cannot attribute is UNKNOWN, never GREEN"
        )
    return str(newest.get("status") or ""), str(newest.get("conclusion") or "")


# ── render ───────────────────────────────────────────────────────────────────


def _cell(value: object) -> str:
    """Escape a value bound for a markdown TABLE cell (or for one bullet line).

    Registry and `gh` text reaches `render` unescaped: `evidence`, `verdict` and `mint`
    are read straight out of a record JSON on disk, and `why` embeds `gh`'s **multi-line**
    stderr (`gh_json` keeps `out.stderr.strip()[:300]` of it). A `|` therefore emitted
    extra table cells and a newline broke out of the bullet it was written into.

    MEASURED (#10687): a record whose `evidence` is `"comment:1 | injected"` rendered an
    11-cell row (`Expected: 10; Actual: 11`) and the PINNED linter fired
    MD056/table-column-count. That is the SAME class this tool had just been fixed for —
    a regenerated census reddening the REQUIRED `docs` check on a finding the lane did
    not write — so the escape belongs here rather than in a later repair.

    ⛔ The BACKSLASH is replaced FIRST, and that order is the whole point: escaping `|`
    alone leaves data that already contains a literal `\\|` rendering as `\\\\|`, which GFM
    reads as an ESCAPED BACKSLASH followed by a live delimiter. Measured with the pinned
    linter: `evidence` = `"comment:1\\|x"` produced `MD056 [Expected: 10; Actual: 11]`.
    A corrupt or hand-written record is exactly the input this census must survive, so
    "a `|` would add a cell" was still false until the backslash was escaped too.

    ⛔ The ENCODE is the third half of the same promise, and it is here rather than at the write
    for the same reason the other two are: an unencodable code point must not outlive this
    boundary. MEASURED (review cycle 15): RFC 8259 permits a lone surrogate, `json.loads` accepts
    one, and a hand-written record carrying `"evidence": "comment:1 \\ud800"` reached the table —
    where `.encode("utf-8")` raised `UnicodeEncodeError` inside `_content_digest` (a 'surrogates
    not allowed' escape) BEFORE the `except (OSError, ValueError)` around the write could see it,
    so a census that had read every surface correctly died with a traceback and **rc 1**, breaking
    this file's published `0`/`2`-never-`1` contract. `print(text)` afterwards raised on the same
    code point. `backslashreplace` keeps the evidence (`\\ud800`) readable instead of dropping it.

    ⛔ The escape is DOUBLED here on purpose, and it is not cosmetic (review cycle 23). CPython
    converts a docstring to UTF-8 at COMPILE time (CPython #142411, 3.13+), and a docstring holding a
    LONE SURROGATE raises `UnicodeEncodeError: surrogates not allowed` from `compile()` — before any
    bytecode exists, so no `try`/`except`, no `raise SystemExit(2)` guard and no `main()` can see it.
    Measured: a single backslash here made `tools/attest_census.py` refuse to import on CPython 3.14
    (`python tools/attest_census.py --help` → rc **1**, the code this file publishes as impossible),
    while `origin/main`'s copy compiled fine. `pyproject.toml` declares `requires-python >= 3.12`, so
    3.14 is a supported interpreter. `tests/test_attest_census.py` pins this class: it walks every
    docstring and asserts it is UTF-8 encodable.
    """
    text = (
        str(value)
        .replace("\\", "\\\\")
        .replace("|", "\\|")
        .replace("\r", " ")
        .replace("\n", " ")
        # ⛔ A HARD TAB is MD010, measured (#10687 cycle 22) with the pinned linter on
        # `evidence` = `"comment:1\tTAB"` → `MD010/no-hard-tabs [Column: 84]`. A record on disk is
        # untrusted input, so a tab is not exotic — and the finding would land on
        # `operations/attest-census.md`, for which `config/docs-lint-baseline.json` has NO key, so
        # the regenerated census would red the REQUIRED `docs` check on a defect the lane did not
        # write. Same class as the `|` and backslash escapes above.
        .replace("\t", " ")
        .strip()
        # LAST of the replacements, so the escapes it introduces are not themselves doubled, and so
        # it cannot reintroduce a live `|`. Every dynamic cell goes through here, which is why one
        # sanitise point is enough for the table, the digest, the file write and the stdout.
        .encode("utf-8", "backslashreplace")
        .decode("utf-8")
    )
    # ⛔ A BARE URL is MD034, measured (#10687 cycle 22) with the same pinned linter on the OTHER
    # documented evidence shape: `_EVIDENCE_REF` (`:122-129`) accepts a `github.com` URL, and one of
    # the live registry's 3,703 records uses exactly that form, so a row for such a PR rendered
    # `MD034/no-bare-urls` and reddened `docs` the same way the tab did. An AUTOLINK (`<url>`) is not
    # a bare URL, so wrapping it here keeps the value readable AND keeps the row linter-clean. The
    # lookbehind/lookahead mean a value that already carries an autolink is not wrapped twice, and
    # trailing sentence punctuation is left OUTSIDE the link (`<https://x.y>.` reads better than
    # `<https://x.y.>` and links the same target).
    return _autolink(text)


# ── the document's own ACCIDENTAL-EDIT detector ──────────────────────────────

# The rendered document carries a digest of its OWN BODY, so the edit class that actually
# happened here is caught offline instead of trusted against. Measured (#10687): the committed
# snapshot's header read `generated 2026-11-03T05:22:14Z` while its RESTORE/RE-REVIEW prose had
# been reflowed BY HAND eight hours later, so a lane filtering rows by that stamp read a
# population older than the document it was in — AGENTS.md's "stale flag that records a past
# state and is never revalidated". A timestamp can never notice that; a digest of the body can.
#
# ⛔ IT IS NOT A PROVENANCE PROOF, and the wording below says so (#10687 review cycle 15). The
# digest is UNKEYED and its algorithm is in this repo, so it only distinguishes an edit that did
# not recompute it from one that did. MEASURED at the cycle-14 head: flipping a row from
# `RE-REVIEW` to `RESTORE` and re-deriving the header hex with these same three lines made
# `verify_census_document` return "" — a forged RESTORE row, indistinguishable from machine
# output, in the artifact a lane consults to decide whether to re-post a review proof. The claim
# "it proves the file came out of the renderer unedited" was therefore false, and it is the reason
# this comment and the emitted prose are qualified rather than merely shortened. A construct that
# CANNOT deliver its stated guarantee is worse than no construct: see AGENTS.md "Fix Broken
# Infrastructure" — a guarantee that reads as a guard but cannot fail is the class the #10687
# cycle-5/cycle-6 findings were about.
_DIGEST_PLACEHOLDER = "__CENSUS_CONTENT_SHA256__"
# The header's digest is the LAST thing on its line (`… content sha256 `<hex>`.`), and the anchor
# is the END of that line on purpose: `repo` is emitted on the same line AHEAD of it, so a plain
# first-match search can be satisfied by a repo whose text contains a digest-shaped span. Measured
# (#10687 review cycle 16) with `--repo` carrying the placeholder token; the `$` anchor is what
# makes the header's own slot the one that is read, not whichever one appears first.
_DIGEST_RE = re.compile(r"content sha256 `([0-9a-f]{64})`\.\s*$", re.MULTILINE)


def _content_digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def verify_census_document(text: str) -> str:
    """`""` when `text` matches the digest printed in its own header, else the reason it does not.

    ⛔ An ABSENT digest is not a pass. A document with no header digest cannot prove it came out
    of `render`, so it is refused with a reason — the same fail-closed rule the classifier applies
    to a record with no usable `head_sha`, and for the same reason: absence is never evidence.
    """
    m = _DIGEST_RE.search(text)
    if m is None:
        return "no `content sha256` in the header — this file cannot prove it was emitted by render()"
    # Rebuild the pre-digest text: the header's hex is the ONE span the digest cannot cover
    # (it is computed over the body, then substituted in).
    canonical = text[: m.start(1)] + _DIGEST_PLACEHOLDER + text[m.end(1) :]
    actual = _content_digest(canonical)
    if actual != m.group(1):
        return (
            f"content sha256 mismatch: header says {m.group(1)}, body hashes to {actual} — "
            "this file was edited after generation"
        )
    return ""


def render(rows: list[Row], repo: str, generated_at: str, truncated: bool = False, limit: int = 100) -> str:
    counts: dict[str, int] = {}
    for r in rows:
        counts[r.outcome] = counts.get(r.outcome, 0) + 1
    order = [RESTORE, RE_REVIEW, NOTHING, IN_FLIGHT, OK, UNREADABLE]
    totals = " · ".join(f"{k}={counts[k]}" for k in order if counts.get(k))

    lines = [
        "# Attestation census — open PRs",
        "",
    ]
    if truncated:
        lines += [
            f"> ⛔ **TRUNCATED: the first {limit} of MORE than {limit} open PRs.** This report does NOT",
            "> cover the whole population — PRs outside this window have no row here, and their absence",
            "> is not a statement about them. Re-run with a larger `--limit`.",
            "",
        ]
    # ⛔ The header is built as a SEPARATE string and spliced in by INDEX below, never found by
    # searching for the placeholder. `repo` is emitted on this same line AHEAD of the digest, so a
    # first-match `replace(..., 1)` puts the digest into the repo FIELD and leaves the header's
    # slot literal — measured (#10687 review cycle 16): `--repo '__CENSUS_CONTENT_SHA256__'` exited
    # 0 and wrote a census that `verify_census_document` refuses ("no `content sha256` in the
    # header"), i.e. the invariant stated right below held for every repo except one.
    # ⛔ `_cell` on `repo`, because the header must be ONE LINE by construction. The digest is read
    # back by an end-of-line-anchored pattern, so a value that can inject a newline can put an
    # earlier digest-shaped span in front of the real one and make a genuine document refuse itself
    # (measured, review cycle 17: a repo of `a\ncontent sha256 `<64 zeroes>`.\nrest` gave "header says
    # 000…, body hashes to 0d78aa…"). Unreachable through `main` — `gh pr list --repo` refuses a
    # slash-less/multi-line slug first — so this closes the LIBRARY boundary, which is where the
    # suite and any future caller reach `render` directly. `_cell` is IDENTITY for a real slug
    # (`daniel-ospina/tortoise`), so the committed artifact stays byte-identical.
    repo = _cell(repo)
    header_prefix = (
        f"Repo `{repo}` · generated {generated_at} by `tools/attest_census.py` ·"
        " content sha256 `"
    )
    header_suffix = "`."
    header_index = len(lines)
    lines.append(header_prefix + _DIGEST_PLACEHOLDER + header_suffix)
    lines += [
        "",
        "> ⚠️ **SNAPSHOT, NOT A LIVE VERDICT.** This file is machine-generated from *one box's*",
        "> registry at the timestamp above, and every push, review and gate re-run invalidates it.",
        "> Re-run `uv run python tools/attest_census.py` before acting on any row — in particular,",
        "> an `OK` or `NOTHING-TO-RESTORE` row from this snapshot is **not** evidence about the",
        "> PR's current head, and `NOTHING-TO-RESTORE` never means \"nobody reviewed this\"",
        "> fleet-wide: `~/.pi/agent/reviews/` is per-machine.",
        ">",
        "> The header's `content sha256` is a digest of THIS FILE'S OWN BODY, so an edit that does not",
        "> also recompute it cannot pass — the ACCIDENTAL edit (the hand reflow that produced this",
        "> check, a sentence left stale by a later change) is caught offline, without `gh`. ⛔ It is",
        "> **not a provenance proof**: the digest is unkeyed and its algorithm is in this repo, so an",
        "> editor who recomputes it after editing produces a file that verifies. Read a matching digest",
        "> as \"nobody edited this by accident\", never as \"the tool emitted these rows\". The row for",
        "> the PR that regenerated this file names the head that PR had AT generation time. That is NOT",
        "> necessarily the parent of the commit adding the file (measured #10687 cycle 18: a worktree one",
        "> merge behind the remote tip records the REMOTE head, so the row named an ancestor's sibling).",
        "> What holds by construction is narrower and is what matters: a document cannot contain the sha",
        "> of the commit that contains it.",
        "",
        "One row per open PR: its head, whether a review record exists on **this box**, whether that",
        "record's head equals the PR's head, what the record claims, and what the `ai-review-gate`",
        "check says. `RESTORE` is the only outcome that licenses re-posting a proof — and even then the",
        "verdict is quoted from the record, never synthesised. A record at any other head is not",
        "evidence of the live code: it is `RE-REVIEW` when the gate has no green verdict, and `OK`",
        "only because the gate itself already accepted the proof (so no re-post is owed).",
        "",
        # ⛔ LABELLED, never a bare emphasis-only paragraph (#10687). The line used to
        # be `f"**{totals}**"`, which renders as a paragraph consisting ENTIRELY of
        # emphasis — markdownlint's MD036/no-emphasis-as-heading (the `docs` job's
        # PINNED markdownlint-cli2@0.23.3 fires it), so every PR that committed a
        # regenerated census reddened the REQUIRED `docs` check on a finding it did
        # not write. The label earns its place twice: it removes the emphasis-only
        # paragraph, and it says what the numbers ARE (they were previously
        # unlabelled totals a reader had to infer).
        # `or 'no rows'` because an empty census yields an empty `totals` string, and
        # a `**Totals:**` with nothing after it is a malformed report — the run read
        # no rows and must say so rather than render a blank label.
        f"**Totals:** {totals or 'no rows'}",
        "",
        "| PR | head | record | record head | match | mint | verdict | evidence | gate | outcome |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        rec = r.record
        if rec is None:
            rec_cell = "—"
        elif rec.error == "":
            rec_cell = rec.path.name
        elif "foreign" in rec.error:
            rec_cell = f"{rec.path.name} (foreign)"
        elif "legacy" in rec.error:
            rec_cell = f"{rec.path.name} (unattributable)"
        else:
            # Only a record that could NOT be read is corrupt; the label's remedy is destructive,
            # so it must never cover an intact record that merely names another PR/repo (cycle 9).
            rec_cell = f"{rec.path.name} (corrupt)"
        rhead = "—"
        match = "—"
        if rec is not None:
            rhead = (rec.head_sha[:12] + "…") if rec.head_sha else "—"
            match = "yes" if rec.head_sha == r.head else ("no" if rec.head_sha else "—")
        mint = "—" if rec is None else (rec.mint or "—")
        verdict = "—" if rec is None else (rec.verdict or "—")
        ev = "—" if rec is None else ("null" if rec.evidence in (None, "", False) else str(rec.evidence))
        gate = "not-read" if not r.gate_checked else (r.gate_conclusion or r.gate_status or "absent")
        # Every DYNAMIC cell is escaped — the record-derived ones (`rec_cell`, `verdict`,
        # `mint`, `ev`), the `gh`-derived ones (`rhead`, `head`, `match`, `gate`), and the
        # record FILENAME inside `rec_cell` — because a `|` in any of them adds a cell and
        # breaks the table (#10687). `head` is escaped INSIDE its code span: a GFM table is
        # split into cells BEFORE inline parsing, so an unescaped `|` there severs the row
        # even though the value is wrapped in backticks (measured: a 12-cell row, MD056).
        # The two literal columns (`#<pr>` is an int, `outcome` is one of our own constants)
        # carry no external text.
        lines.append(
            "| #" + " | ".join(
                [
                    str(r.pr),
                    f"`{_cell(r.head[:12])}…`",
                    _cell(rec_cell),
                    _cell(rhead),
                    _cell(match),
                    _cell(mint),
                    _cell(verdict),
                    _cell(ev),
                    _cell(gate),
                    f"**{r.outcome}**",
                ]
            ) + " |"
        )

    flagged = [r for r in rows if r.outcome in (RESTORE, RE_REVIEW, NOTHING, UNREADABLE, IN_FLIGHT)]
    if flagged:
        lines += ["", "## What each flagged row needs", ""]
        for r in flagged:
            # `why` can be `gh`'s multi-line stderr and `notes` are free text: a raw newline
            # would break out of the bullet and render the rest as a new paragraph (#10687).
            lines.append(f"- **#{r.pr}** `{_cell(r.branch)}` — {r.outcome}: {_cell(r.why)}")
            for n in r.notes:
                lines.append(f"  - {_cell(n)}")
    lines += [
        "",
        "## Reading rules",
        "",
        "- A `RE-REVIEW` row is **not** restorable, however green the old verdict was: the reviewed artifact is gone.",
        "- A `NOTHING-TO-RESTORE` row means exactly that. Never infer a review from a green test run.",
        "- `mint:\"carried\"` is never evidence of a review of this head (#10177: a carry re-bound an older `clean`",
        "  onto a head whose own review had found a P1).",
        "- A record minted on **another box** is invisible here: `~/.pi/agent/reviews/` is per-machine. Rows are",
        "  this box's view, and the census says so rather than implying a fleet-wide clean bill.",
        "",
    ]
    body = "\n".join(lines)
    # ⛔ The substitution is by INDEX, not by search. A record whose own text contains the token must
    # survive VERBATIM, or `verify_census_document` (which restores the same single span) could never
    # re-derive this digest — and `repo` is the second untrusted field that can contain it, one that
    # sits ahead of the digest on the very same line. Writing to `header_index` makes both true by
    # construction: nothing is searched, so nothing else can be hit.
    digest = _content_digest(body)
    lines[header_index] = header_prefix + digest + header_suffix
    return "\n".join(lines)


def _neutralise_stdout() -> None:
    """Point fd 1 at /dev/null so the interpreter's EXIT-TIME flush cannot decide our exit code.

    ⛔ This is not defensive tidiness, it is the difference between rc 2 and rc **120**. When the
    report fits the ~8 KiB stdout buffer and the reader has closed the pipe, `print()` succeeds and
    the failure happens in CPython's `flush_std_files()` at shutdown — which returns -1, makes
    `_Py_Finalize` set status -1, and `Py_Exit` turns that into `exit(120)`. No `try`/`except` in
    this file can see it. MEASURED (#10687 review cycle 18): the same census exited 2 with a 48-row
    payload and 120 with a 1-row one, i.e. the contract's exit code depended on the report SIZE.
    Only `stdout.flush()` inside the handler fixes that, and this makes the final flush a no-op
    afterwards so it cannot overwrite the 2 we are about to return.
    """
    with contextlib.suppress(OSError, ValueError, AttributeError):
        devnull = os.open(os.devnull, os.O_WRONLY)
        try:
            os.dup2(devnull, sys.stdout.fileno())
        finally:
            os.close(devnull)


def _neutralise_stderr() -> None:
    """Point fd 2 at /dev/null — `flush_std_files()` flushes **stderr too**, not just stdout.

    ⛔ `_neutralise_stdout` covers one of the two streams CPython flushes at shutdown.
    `flush_std_files()` flushes `sys.stdout` AND `sys.stderr`, and a failure of EITHER sets
    `status = -1`, which `Py_Exit` maps to **120** (`Python/pylifecycle.c` 3.12: `:1673-1708`,
    `:1896`, `:3097-3104` — read for this review rather than assumed). So a guarded stdout plus an
    UNGUARDED stderr print leaves exactly the hole the stdout guard was added to close.
    """
    with contextlib.suppress(OSError, ValueError, AttributeError):
        devnull = os.open(os.devnull, os.O_WRONLY)
        try:
            os.dup2(devnull, sys.stderr.fileno())
        finally:
            os.close(devnull)


def _say(message: str) -> str:
    """Write one courtesy line to STDERR; `""` on success, else the failure reason.

    ⛔ Measured (#10687 cycle 22): `attest_census.py --out /tmp/x.md 2>/dev/full` printed a complete,
    correct report, wrote the artifact, and exited **120** — because the unguarded
    `_say(...)` here raised `OSError(ENOSPC)` out of `main`, and the interpreter's
    shutdown flush then decided the code. The same shape with a closed stderr pipe raises
    `BrokenPipeError`. So a census that had read every surface correctly published a code the file
    says is impossible, and on the `--out` failure path the documented **2** became 120 —
    indistinguishable from a crash, which is the one confusion the 0/2 contract exists to prevent.

    The `flush()` inside the `try` is load-bearing for the same reason it is in `_emit_report`:
    stderr is line-buffered, so a write can succeed while the real failure waits for shutdown.
    `_neutralise_stderr()` afterwards makes that final flush a no-op, so it cannot overwrite the
    code the caller is about to return.

    ⛔ The exit code must be decided BEFORE this is called, never derived from it: a courtesy
    message must not be able to change what the census says about its own read.
    """
    try:
        print(message, file=sys.stderr)
        sys.stderr.flush()
        return ""
    except (OSError, ValueError, AttributeError) as exc:
        _neutralise_stderr()
        return str(exc)


def _emit_report(text: str) -> str:
    """Write the report to stdout; `""` on success, else the failure reason (caller returns 2).

    ⛔ ONE helper for BOTH output surfaces (`--json` and the markdown report). The two branches
    drifted once already: the markdown `print` was guarded against `UnicodeEncodeError` and the
    `--json` `print` was not, so `attest_census.py --json | jq` exited **1** on a closed pipe while
    the markdown path exited 2 (measured, review cycle 18). A guard that exists on one of two
    sibling paths is not a contract, it is a coin flip.

    The `flush()` inside the `try` is load-bearing: without it a report smaller than the stdout
    buffer reports success here and then dies at interpreter shutdown with rc 120.
    """
    try:
        print(text)
        sys.stdout.flush()
        return ""
    # ⛔ `AttributeError` is NOT defensive padding here: CPython sets `sys.stdout = None` when fd 1 is
    # closed at startup, and `print()` SILENTLY NO-OPS in that state — so the only thing that raises
    # is the `flush()` above, and without this class it escaped as a traceback and **rc 1** on a run
    # that had already written its report (measured #10687 cycle 19: `… --out o.md 1>&-` went
    # rc 0 → rc 1 versus both the parent commit and `origin/main`). That is the same contract break
    # this helper exists to close, reintroduced by the call that closes the size-dependent one.
    # `_neutralise_stdout` above already suppresses it, and the precedent this file cites
    # (`validation/check-license-surface.py`) suppresses the same triple.
    except (OSError, ValueError, AttributeError) as exc:  # closed pipe / unencodable char / no stdout
        _neutralise_stdout()
        return str(exc)


def main(argv: list[str] | None = None) -> int:
    # ⛔ The report carries non-ASCII (`—`, `…`, the ⛔/⚠️ markers) in its own PROSE, so a C/POSIX
    # stdout cannot encode it: without this, `print(text)` after a perfectly good run raises
    # `UnicodeEncodeError` and the tool exits 1 — breaking its published `0`/`2`-never-`1` contract
    # (measured: review cycle 14). Pinned the same way `validation/check-license-surface.py` pins a
    # C-locale stream, and suppressed because `sys.stdout` may be a replaced stream without it.
    # ⛔ `OSError` belongs in this tuple for the same reason it is in the three sibling helpers
    # (`_neutralise_stdout`, `_neutralise_stderr`, `_say`): `reconfigure` FLUSHES whatever is already
    # buffered, so an in-process caller that wrote to stdout before calling `main` can get `ENOSPC`
    # out of it (measured, review cycle 24: "ESCAPED: OSError [Errno 28] No space left on device").
    # Unreachable from the CLI — the buffer is empty at interpreter start — which is why this is a
    # library-boundary fix, but a guard on one of four sibling paths is the coin flip this file's
    # own rule forbids.
    with contextlib.suppress(OSError, ValueError, AttributeError):
        # ⛔ `errors="backslashreplace"` keeps a courtesy message RENDERABLE when fd 2 is closed:
        # `sys.stderr` is None there, so `print(..., file=None)` falls back to STDOUT, and with a
        # C-locale argv (a non-ASCII `--out` decoded to lone surrogates) the message raised
        # `UnicodeEncodeError` — measured #10687 cycle 20, rc 1 while each ingredient alone was rc 0.
        # It is also exactly what CPython's own `create_stdio` gives a real stderr, which is why the
        # same message is harmless when fd 2 is open.
        # ⚠️ NOT load-bearing for the EXIT CODE any more, and saying otherwise would be the
        # claim/artifact mismatch this file keeps fixing: since cycle 22 every courtesy write goes
        # through `_say`, which catches `UnicodeEncodeError` itself, so deleting this argument leaves
        # the suite green (measured, review cycle 24). It is message FIDELITY plus a guard for any
        # future direct write, not "the last piece of the `0`/`2` contract".
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", default="daniel-ospina/tortoise")
    ap.add_argument("--reviews-dir", default=str(Path.home() / ".pi/agent/reviews"))
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--no-gate", action="store_true", help="skip the per-PR check-run read (gh pr list is still required)")
    ap.add_argument("--json", action="store_true", help="machine-readable rows on stdout instead of markdown")
    ap.add_argument("--out", default="operations/attest-census.md")
    # ⛔ `parse_args` is the ONE stderr writer the cycle-22 `_say` sweep could not reach: argparse
    # writes `usage: … error: …` to STDERR and then raises `SystemExit(2)`, outside every handler in
    # this file. Measured (review cycle 23): `attest_census.py --bogus … 2>/dev/full` exited **120** —
    # the code the stderr guard exists to eliminate, published by a census that had not even started.
    #
    # ⛔ The naive fix (catch `OSError` around `parse_args`) does NOT work, and that is the whole
    # subtlety: `sys.stderr` is a `TextIOWrapper`, and a FAILED FLUSH is deferred — the ENOSPC is
    # remembered and re-raised by the INTERPRETER'S shutdown flush, long after `parse_args` has
    # raised its perfectly clean `SystemExit(2)`. So the stream must be neutralised on the
    # `SystemExit` path, before that deferred flush can decide the status. `SystemExit(0)`
    # (`--help`) is left alone: it is not a failure and must still exit 0.
    # ⛔ `SystemExit(0)` (`--help`) is NOT a failure, but it still needs the stdout surface handled:
    # argparse writes the help text to `sys.stdout`, which is BLOCK-buffered, so the write succeeds
    # and a broken stdout only fails in CPython's shutdown flush — measured (review cycle 24):
    # `--help >/dev/full` and `--help | true` under `pipefail` both exited **120**, the code this
    # file publishes as impossible, from the one path the comment above used to claim was safe.
    # The help text is already unrecoverable at that point, so discarding it is the only honest
    # outcome; `_neutralise_stdout` makes the shutdown flush a no-op and the code stays 0.
    try:
        args = ap.parse_args(argv)
    except (OSError, ValueError):
        _neutralise_stderr()
        return 2
    except SystemExit as exc:
        if exc.code not in (0, None):
            _neutralise_stderr()
        else:
            # ⛔ Neutralise ONLY when the flush actually FAILED (review cycle 25, P3).
            # `_neutralise_stdout` dup2s /dev/null onto fd 1 for the life of the PROCESS, and this
            # branch is also reached by an IN-PROCESS caller — which this suite does in dozens of
            # places — so an unconditional call silently discarded every later write in the HOST: a
            # `print()` after `main(["--help"])` produced nothing, and so would pytest's own summary.
            # The `--help >/dev/full` case still exits 0 because the failing flush is what triggers
            # the neutralise, and a clean flush leaves the stream alone.
            try:
                sys.stdout.flush()
            except (OSError, ValueError, AttributeError):
                _neutralise_stdout()
        raise

    owner, _, name = args.repo.partition("/")
    try:
        reviews_dir = Path(args.reviews_dir).expanduser()
    except RuntimeError as exc:  # `~nosuchuser` — expanduser raises, and that must not be rc 1
        _say(f"attest_census: cannot resolve --reviews-dir {args.reviews_dir!r}: {exc}")
        return 2
    generated_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    try:
        prs = gh_json(
            ["pr", "list", "--repo", args.repo, "--state", "open", "--limit", str(args.limit + 1),
             "--json", "number,headRefOid,headRefName,title"]
        )
    except RuntimeError as exc:
        _say(f"attest_census: could not list PRs: {exc}")
        return 2
    if not isinstance(prs, list) or not all(isinstance(p, dict) for p in prs):
        _say("attest_census: `gh pr list` returned an unexpected payload — refusing to census it")
        return 2

    # ⛔ Assert the REGISTRY is readable BEFORE classifying anything: an unlistable
    # `--reviews-dir` would otherwise render every PR as "no record", i.e. report a
    # whole-registry read failure as "nothing to restore" for the entire fleet.
    try:
        load_record(reviews_dir, owner, name, 0)
    except RegistryUnreadable as exc:
        _say(f"attest_census: {exc} — refusing to report absence from an unreadable registry")
        return 2

    truncated = len(prs) > args.limit
    if truncated:
        prs = prs[: args.limit]

    rows: list[Row] = []
    unreadable = 0
    for pr in prs:
        if not isinstance(pr.get("number"), int):
            _say(f"attest_census: `gh pr list` row has no integer number ({pr!r}) — refusing")
            return 2
        head = str(pr.get("headRefOid") or "")
        try:
            rec = load_record(reviews_dir, owner, name, int(pr["number"]))
        except RegistryUnreadable as exc:  # registry became unlistable mid-run (race)
            _say(f"attest_census: {exc} — refusing to report absence")
            return 2
        row = Row(
            pr=int(pr["number"]),
            head=head,
            branch=str(pr.get("headRefName") or ""),
            title=str(pr.get("title") or ""),
            record=rec,
        )
        if not SHA_RE.match(head):
            row.outcome, row.why = UNREADABLE, f"PR head {head!r} is not a 40-char sha"
        elif args.no_gate:
            row.outcome, row.why = classify(head, rec, "", "", gate_known=False)
            # ⛔ Only the GITHUB half of the sentence may be replaced, and only when there is no
            # record at all: a record-present NOTHING row has its own reason (evidence-less /
            # non-clean verdict) and overwriting it would contradict the same row's own table
            # columns (measured by review cycle 3).
            if row.outcome == NOTHING and rec is None:
                row.why = (
                    "no record on this box at any head; the published-marker surface was NOT "
                    "read (--no-gate), so this is not a statement about GitHub"
                )
            elif row.outcome != NOTHING:
                row.why += " (gate NOT read: --no-gate)"
        else:
            try:
                row.gate_status, row.gate_conclusion = read_gate(args.repo, head)
                row.gate_checked = True
            except RuntimeError as exc:
                row.outcome, row.why = UNREADABLE, f"check-run read failed: {exc}"
        if row.gate_checked:
            row.outcome, row.why = classify(head, rec, row.gate_status, row.gate_conclusion)
            # ⛔ The marker clause is a claim about a surface the GATE reads. Only a gate that
            # actually RAN AND REPORTED can support it: an absent check-run, or a non-red,
            # non-green attempt (`cancelled`/`stale`), is not a read of the marker surface.
            if row.outcome == NOTHING and rec is None:
                # ⛔ A RED CONCLUSION DOES NOT PROVE THE MARKER SURFACE WAS READ. The gate fails
                # before reading any marker when its key is unset, when every candidate marker
                # fails HMAC, when the comment fetch 403s, and when the diff hash cannot be
                # computed (each says so in its own annotation). So state what the run knows:
                # the gate is red, and its own annotation is the thing to read next (cycle 6).
                gate_red = row.gate_conclusion in ("failure", "timed_out", "action_required")
                row.why += (
                    f"; the gate concluded {row.gate_conclusion} — whether a published marker exists is "
                    "answered by the gate's own annotation, not by this census"
                    if gate_red
                    else f"; the gate reported nothing readable on this head ({row.gate_conclusion or 'no run'}), "
                    "so the published-marker surface was not judged"
                )
        # ⛔ ONE increment site. A row the tool itself calls UNREADABLE is a surface it could
        # not read, so the run must not report 0 ("complete census") while displaying one.
        # (A second increment in the bad-head branch made the rc-2 test pass even when this
        # line was deleted — measured by review cycle 2.)
        if row.outcome == UNREADABLE:
            unreadable += 1
        if rec is not None and rec.head_sha and len(rec.head_sha) != 40:
            row.notes.append(f"record head_sha {rec.head_sha!r} is not a 40-char sha — not bindable")
        rows.append(row)

    rows.sort(key=lambda r: r.pr, reverse=True)

    if args.json:
        reason = _emit_report(json.dumps({
            "repo": args.repo,
            "generated_at": generated_at,
            "reviews_dir": str(reviews_dir),
            "truncated": truncated,
            "limit": args.limit,
            "rows": [
                {
                    "pr": r.pr, "head": r.head, "branch": r.branch, "outcome": r.outcome, "why": r.why,
                    "gate_status": r.gate_status, "gate_conclusion": r.gate_conclusion, "gate_checked": r.gate_checked,
                    "record": None if r.record is None else {
                        "path": str(r.record.path), "head_sha": r.record.head_sha, "mint": r.record.mint,
                        "verdict": r.record.verdict, "evidence": r.record.evidence,
                        # ⛔ Attribution is part of the CLAIM, not a column note. `real_evidence`
                        # alone answers "does this record attest a review of SOME PR"; a foreign
                        # record answers that about another PR (measured, review cycle 24: a record
                        # declaring `evil/evil` emitted `real_evidence: true` under an `UNREADABLE`
                        # row, so `jq 'select(.record.real_evidence)'` selected it). The markdown
                        # table labels the row `(foreign)`; this boolean must not disagree with the
                        # string a consumer keys on, which is the sibling-path rule of this file.
                        "real_evidence": r.record.real_evidence and not r.record.error,
                    },
                }
                for r in rows
            ],
        }, indent=2))
        if reason:  # a closed pipe / unencodable char on the --json surface is rc 2, never 1
            _say(f"attest_census: could not write the report to stdout: {reason}")
            return 2
        return 0 if unreadable == 0 else 2

    # Belt to `_cell`'s braces. `_cell` sanitises every RECORD- and `gh`-derived cell, but this
    # string is not the only source of one: `--repo` is ARGV, so `--repo $'\ud800'` reaches the
    # header line unescaped. Without this the digest encoded it, `str.encode` raised
    # `UnicodeEncodeError` (a `ValueError`) OUTSIDE any handler, and the tool exited 1 on a run
    # that read everything correctly — the one exit code this file publishes as impossible.
    try:
        text = render(rows, args.repo, generated_at, truncated=truncated, limit=args.limit)
    except ValueError as exc:  # an unrenderable character is rc 2, never a traceback
        _say(f"attest_census: could not render the report: {exc}")
        return 2
    try:
        out = Path(args.out).expanduser()
    except RuntimeError as exc:  # symmetric with --reviews-dir: an unresolvable ~ is rc 2, not rc 1
        _say(f"attest_census: cannot resolve --out {args.out!r}: {exc}")
        return 2
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        # ⛔ `encoding="utf-8"` is part of the CONTRACT, not tidiness (review cycle 14): this file is
        # read by the pinned markdownlint, by `verify_census_document` (which re-derives its sha256)
        # and by lanes grepping rows, so its bytes cannot be whatever locale the invoking shell
        # exported. `ValueError` stays in the `except` because `UnicodeEncodeError` is a
        # `ValueError`, NOT an `OSError` — but cycle 15 measured that the lone-surrogate shape it was
        # added for could never reach THIS line: `_content_digest` encodes the whole document first,
        # so it died at `render` before this `try` was entered, and the claim that this `except`
        # handled it was false. `render` and `print` are now guarded in their own right and `_cell`
        # sanitises the payload, so this clause is a belt over an already-closed door rather than the
        # fix — do not delete it, and do not describe it as the fix.
        out.write_text(text, encoding="utf-8")
    except (OSError, ValueError) as exc:  # unwritable/unencodable --out is not a finding
        _say(f"attest_census: could not write {out}: {exc}")
        return 2
    reason = _emit_report(text)
    if reason:
        _say(f"attest_census: could not write the report to stdout: {reason}")
        return 2
    # ⛔ The code is DECIDED before any courtesy message is attempted (review cycle 22). `_say`
    # cannot raise, so this ordering is not about the handler — it is about the rule the handler
    # exists for: a message about the run must never be able to change what the run says. Keep the
    # assignment above the call for that reason, not by accident of layout.
    rc = 0 if unreadable == 0 else 2
    _say(f"attest_census: wrote {out}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
