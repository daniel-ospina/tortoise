#!/usr/bin/env python3
"""Measure, CHECK, and re-state the two notes' `hosted_api.py:<line>` citation census.

`tortoise/activation_scorecard.py` and `tortoise/hosted_api.py` each carry a note
stating how many places cite `hosted_api.py` by absolute line number, because that
number is what justifies interpolating a query instead of binding parameters (the
interpolation buys LINE COST: only cites ABOVE the pivot shift when `hosted_api.py`
grows). A hand count of it has been wrong in BOTH directions at one head, so the
census has one command:

    uv run python tools/hosted_api_cite_census.py            # human-readable
    uv run python tools/hosted_api_cite_census.py --json     # for a script
    uv run python tools/hosted_api_cite_census.py --pivot 24565
    uv run python tools/hosted_api_cite_census.py --check    # exit 1 when a note is stale
    uv run python tools/hosted_api_cite_census.py --refresh  # re-state every figure in place

⛔ `--refresh`, never a hand-paste: copying four figures out of a printout into two
prose notes by hand is the operation that produced the wrong count twice. One
command rewrites all of them, through the SAME narrow sentence patterns `--check`
reads, and the run REFUSES (exit 2, writing nothing) when a note's sentence was
re-worded — so a re-pointed note is reported, never silently skipped. Both verbs
converge as a FIXPOINT: re-writing a note's own pivot cite retires one cite target
and adds another, which changes the population the figures describe, so the command
re-measures until the tree and the notes agree.

⛔ EVERY figure a note states must have a capture group here, including the PIVOT
wherever it is spelled. The scorecard note spells the pivot THREE times (the
`hosted_api.py:<pivot>` cite, the `LARGER than <pivot>` bound of the ABOVE range,
and `the remainder is <pivot> itself`); a digit run matched but NOT captured is a
figure no verb can compare or rewrite, so `--check` would report the note clean
while `--refresh` left it self-contradictory — measured, and the reason the ABOVE
range row captures its pivot (#11576 review, P2).

⛔ A figure the census cannot measure (an EMPTY side of the pivot, where `census`
returns `None`) is a REFUSAL, never a write: stringifying `None` into the prose
would corrupt the note AND break the sentence the next pass needs to match, so
the tool would report "nothing was written" after writing (#11576 review, P3).

Do NOT assert the population in a fast-suite test: it belongs to EVERY tracked file,
so a test that compared it to prose would red unrelated PRs (measured: ~1 commit in
30 adds a `hosted_api.py:<N>` citation). The test in
`tests/test_demo_session_read_exclusion_8290.py` pins only what this PR owns: that
the two notes agree with each other and with the arithmetic, and that the pivot cite
really is the `LIFETIME_MEMORY_QUERY` execution site. `--check` is therefore wired
into the NIGHTLY main-health report and not into a per-PR check —
`.github/workflows/main-health-nightly.yml` says why in its own comment.

⛔ The pattern requires DIGITS (`[0-9]+`, not `[0-9]*`). The `*` form also matches a
bare `hosted_api.py:` and `sort -u` then counts that empty run as one more target —
that is exactly how the count was briefly "re-measured" from 449 to 450.

⛔ A cite target is a TOKEN that IS ``hosted_api.py:<n>``. ``tests/test_hosted_api.py:22``
is a cite of the TEST file, not of `hosted_api.py` line 22, and counting those
overstated the population in BOTH dimensions the two notes state — distinct
targets AND citing files — while quoting a floor (``22``) that no bare cite
targets (#11850). ⛔ Neither size is frozen in this paragraph: both move as
lookalikes are written, so `census()` is the only thing that answers "how big is it
right now". The boundary is applied with ``fullmatch`` in `census`, and the tokens
it declines are counted into ``non_cite_tokens`` so the definition's cost is
reported rather than silent.
"""
from __future__ import annotations

import sys

if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/hosted_api_cite_census.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/hosted_api_cite_census.py`"
    )

import argparse
import json
import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

#: The DEFINITION of a cite target: a token that IS ``hosted_api.py:<n>``.
#:
#: ⛔ A token that merely ENDS with it — ``tests/test_hosted_api.py:22`` — is a cite
#: of a DIFFERENT file, and counting those overstated the "measured" population in
#: BOTH dimensions the notes state: distinct targets, and citing files (a file can
#: carry only lookalikes and no bare cite at all — this change's own two files do
#: exactly that). It also quoted a floor naming a line no bare cite targets (#11850).
#: The boundary is enforced by ``fullmatch`` in `census`, never by suffix-matching.
#:
#: ⛔ NO COUNT IS FROZEN HERE ON PURPOSE, and that is a correction, not a style
#: choice: an earlier draft of this comment quoted the overstatement as a bracket of
#: measured figures and asserted the delta could not drift — and both halves were
#: falsified before the change landed, twice, at three different sites. Measured at
#: #11850's filing head the pair was 456 → 440; re-measured at ``8300c102c8`` it was
#: 458 → 442; and the citing-file delta grew on the very commit that introduced
#: lookalike-bearing files. `census()` is the only thing that answers "how big is it
#: right now" — every figure written here is a measurement of a PAST tree, which is
#: precisely the hand-paste class this module exists to end.
#:
#: ⚠️ TWO patterns, and the split is FORCED: ``git grep -E`` is POSIX ERE, which
#: has NO lookbehind, so the left boundary cannot be expressed on the grep side.
#: ``_CITE_CANDIDATE_RE`` is therefore deliberately PERMISSIVE on the left — it
#: captures any run of filename characters before the basename — and the Python
#: side decides which candidates are actually cites. DIGITS REQUIRED in both (see
#: the module docstring for the ``[0-9]*`` footgun).
#:
#: ⚠️ A PATH-QUALIFIED cite IS counted: the candidate class excludes ``/``, so
#: ``git grep -o`` starts its match after the slash and ``tortoise/hosted_api.py:<n>``
#: arrives as ``hosted_api.py:<n>`` — same file, same absolute line, so it belongs in
#: the population. This is stated because the obvious later "tighten the boundary"
#: edit (admitting ``/`` into the class) would silently drop those targets and force
#: a note refresh with nothing to explain it. No count is frozen here on purpose: it
#: moves with every new qualified cite, and `census()` is the verb that measures it.
CITE_RE = r"hosted_api\.py:[0-9]+"
_CITE_CANDIDATE_RE = r"[[:alnum:]_.-]*hosted_api\.py:[0-9]+"

#: The line the two notes are written around: the ONE production execution site of
#: `LIFETIME_MEMORY_QUERY`. ⚠️ A FALLBACK, not the source: every shipped path
#: derives the site from the tree (`lifetime_query_sites()[0]`) and the test
#: passes its own `_PIVOT`, so this constant only ever has to agree with them —
#: which is exactly why it must be re-derived whenever an edit above the site
#: moves it (#10226 forward-merge moved it 23128 -> 23131; #11795 moved it
#: 23131 -> 23466, where it had sat stale while the notes it is a fallback for
#: had already been re-derived by hand; #11803 moved it 23466 -> 23477; #11977 moved
#: it 23477 -> 23502 and re-derived neither this constant nor the notes, which is
#: why the #11850 lane found the census red on main at `7e6baffc5d`). Two
#: distinct wrongnesses, with distinct start points — do not merge them:
#:   • `pivot_is_the_lifetime_site(<old pivot>)` goes FALSE the moment the CALL
#:     moves, because the note still cites the old line (#11803, #11977);
#:   • the COUNT SPLIT beside it (`above N`, `at the pivot 0`) only goes wrong
#:     once a re-derivation moves the notes and leaves this constant behind —
#:     measured at #11950's first commit `5d35cd58f6`, over a correct tree, where the
#:     whole triple read `at the pivot 0` / `above 13` / `site: False` and nothing
#:     reddened.
#: Because no shipped path reads it, nothing could see that, so it is now PINNED:
#: `tests/test_demo_session_read_exclusion_8290.py` asserts it equals the site —
#: and that pin is what reds when a forward merge grows the module, which is the
#: remedy this constant relies on rather than a hand sweep.
DEFAULT_PIVOT = 24565

_SCORECARD = "tortoise/activation_scorecard.py"
_HOSTED = "tortoise/hosted_api.py"
_TEST = "tests/test_demo_session_read_exclusion_8290.py"

#: `(label, path, pattern, figure keys)` — the sentence each figure lives in, and
#: the figure it must state. Both `--check` and `--refresh` read exactly these: a
#: re-worded note is a REFUSAL, and a new figure means a new row here.
_NOTES: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    ("scorecard PIVOT cite", _SCORECARD,
     r"PRODUCTION execution is ``hosted_api\.py:(\d+)``", ("pivot",)),
    ("scorecard ABOVE count", _SCORECARD,
     r"\*\*(\d+)\*\* distinct cite", ("above_pivot",)),
    # ⛔ The pivot is CAPTURED here, not matched as a bare digit run: this sentence
    # is the note's second of three spellings of it, and an uncaptured one is a
    # figure neither verb can see (#11576 review, P2 — `--check` greened a note
    # that said 23131 and 24500 at once after a pivot move).
    ("scorecard ABOVE range", _SCORECARD,
     r"LARGER than (\d+) \((\d+) … (\d+)\)", ("pivot", "above_min", "max_above")),
    ("scorecard BELOW count", _SCORECARD,
     r"\*\*(\d+)\*\* have a number smaller", ("below_pivot",)),
    ("scorecard BELOW range", _SCORECARD,
     r"number smaller than it \((\d+) … (\d+)\)", ("min_target", "max_below")),
    ("scorecard TOTAL count", _SCORECARD,
     r"\((\d+) distinct targets in all", ("distinct_targets",)),
    ("scorecard pivot remainder", _SCORECARD,
     r"the remainder is (\d+) itself", ("pivot",)),
    ("hosted_api CITING-FILE count", _HOSTED,
     r"from (\d+) tracked files", ("citing_files",)),
    ("hosted_api TOTAL count", _HOSTED,
     r"tracked files \((\d+) distinct", ("distinct_targets",)),
    ("test contract pin _PIVOT", _TEST,
     r"(?m)^_PIVOT = (\d+)$", ("pivot",)),
)


def lifetime_query_sites(repo: Path = REPO) -> list[int]:
    """Every line of ``hosted_api.py`` that EXECUTES ``LIFETIME_MEMORY_QUERY``."""
    lines = (repo / "tortoise" / "hosted_api.py").read_text(
        encoding="utf-8").splitlines()
    return [n for n, line in enumerate(lines, 1)
            if "query(LIFETIME_MEMORY_QUERY)" in line]


def census(pivot: int = DEFAULT_PIVOT, repo: Path = REPO) -> dict:
    """The four figures the notes state, plus the pivot split.

    ``git grep`` exits **1** when nothing matches — that is an EMPTY census, not a
    failure, and the fast test's collapse floor is what decides whether an empty
    one is acceptable. Only rc >= 2 is a real error.
    """
    # ONE `git grep` answers both questions: `-o` without `-h` prints
    # `path:matched-text`, and git puts the path first, so the FIRST colon
    # separates it from the token (no tracked path contains a colon — measured, and
    # a path that did would yield a token failing `fullmatch` below, i.e. an
    # undercount this function reports as `non_cite_tokens`, never a miscount).
    #
    # ⛔ `-a` IS REQUIRED, not decoration: without it a BINARY carrier makes git
    # print `Binary file <path> matches` — a colon-less line, which this parse would
    # drop before either counter, so the file would vanish from `citing_files` with
    # nothing reported (measured on a throwaway repo: old `-l` listed the file, the
    # colon-less line yields no token). With `-a` the line is `path:token` and the
    # parse stays total. Today no tracked file is a binary carrier, so the figures
    # do not move; the flag is what keeps that from becoming a silent undercount, and
    # `test_the_census_counts_a_binary_carrier_without_a_line_number` pins it.
    #
    # ⛔ `-c grep.lineNumber=false -c grep.column=false` IS REQUIRED TOO: git's own
    # documented config can prefix the match with a line or column number, after
    # which this parse reads that number as part of the token and every genuine cite
    # lands in `non_cite_tokens` (a false red, never a false green — but a phantom
    # hunt). `-c` is used rather than `--no-line-number` so an older git that lacks
    # the flag still runs; an unknown config key is inert.
    # ⛔ And do NOT spell a concrete cite in this comment (or in any test's prose):
    # the census greps EVERY tracked file, so a literal cite here JOINS the
    # population it measures and immediately reds `--check`. Write `hosted_api.py:<n>`.
    proc = subprocess.run(
        ["git", "-c", "grep.lineNumber=false", "-c", "grep.column=false",
         "grep", "-a", "-o", "-E", _CITE_CANDIDATE_RE],
        cwd=str(repo), capture_output=True, text=True, check=False,
    )
    if proc.returncode > 1:
        raise SystemExit(
            f"hosted_api_cite_census: `git grep` failed in {repo}: {proc.stderr!r}")

    targets_seen: set[int] = set()
    citing_files: set[str] = set()
    non_cite = 0
    for line in proc.stdout.splitlines():
        path, _, token = line.partition(":")
        if not token:
            continue
        if not re.fullmatch(CITE_RE, token):
            # e.g. `tests/test_hosted_api.py:22` — a cite of a different file.
            non_cite += 1
            continue
        citing_files.add(path)
        targets_seen.add(int(token.rsplit(":", 1)[1]))
    targets = sorted(targets_seen)

    below = [n for n in targets if n < pivot]
    above = [n for n in targets if n > pivot]
    return {
        "pivot": pivot,
        "distinct_targets": len(targets),
        "below_pivot": len(below),
        "above_pivot": len(above),
        "at_pivot": sum(1 for n in targets if n == pivot),
        "citing_files": len(citing_files),
        #: tokens the permissive grep found that are NOT cites of this file. Report
        #: them so the definition's cost is visible instead of silent (#11850).
        "non_cite_tokens": non_cite,
        "min_target": below[0] if below else None,
        "max_below": below[-1] if below else None,
        "above_min": above[0] if above else None,
        "max_above": above[-1] if above else None,
        "lifetime_sites": lifetime_query_sites(repo),
    }


def pivot_is_the_lifetime_site(pivot: int = DEFAULT_PIVOT, repo: Path = REPO) -> bool:
    """True when ``hosted_api.py:<pivot>`` EXECUTES ``LIFETIME_MEMORY_QUERY``.

    Exact line, and it must be a CALL. A ±N window is not good enough: a window
    that spans the ``from … import LIFETIME_MEMORY_QUERY`` line a few lines above
    accepts the IMPORT as the execution site, so ``_PIVOT`` could be set to the
    wrong line and the pin would stay green — measured, which is why the window is
    gone (mutation attribution, agent-infra#3442: an assertion the mutant survives
    is dead weight that reads as coverage).
    """
    path = repo / "tortoise" / "hosted_api.py"
    lines = path.read_text(encoding="utf-8").splitlines()
    if not 1 <= pivot <= len(lines):
        return False
    return "query(LIFETIME_MEMORY_QUERY)" in lines[pivot - 1]


def _stated(src: str, pattern: str) -> list[int] | None:
    """The numbers a note states in that sentence, or `None` when it is gone."""
    found = re.search(pattern, src)
    return None if not found else [int(g) for g in found.groups()]


def _restate(src: str, pattern: str, want: list[int]) -> tuple[str, int]:
    """Rewrite one sentence's figures in place; returns `(text, substitutions)`."""
    def _repl(found: re.Match[str]) -> str:
        out, cursor = found.group(0), 0
        for i, value in enumerate(want, start=1):
            token = found.group(i)
            at = out.find(token, cursor)
            if at < 0:  # pragma: no cover — a group always sits inside the match
                return found.group(0)
            out = out[:at] + str(value) + out[at + len(token):]
            cursor = at + len(str(value))
        return out

    return re.subn(pattern, _repl, src, count=1)


def _sweep(measured: dict, refresh: bool) -> int:
    """Compare (and optionally rewrite) every figure the notes state.

    Returns 0 = the notes agree with `measured` (and nothing was written),
    1 = stale rows (reported, or just rewritten — the caller re-measures),
    2 = a note's sentence is gone, so nothing was written at all.
    """
    sources = {_SCORECARD: (REPO / _SCORECARD).read_text(encoding="utf-8"),
               _HOSTED: (REPO / _HOSTED).read_text(encoding="utf-8"),
               _TEST: (REPO / _TEST).read_text(encoding="utf-8")}
    rewritten = dict(sources)
    stale: list[str] = []
    refused: list[str] = []

    for label, path, pattern, keys in _NOTES:
        stated = _stated(rewritten[path], pattern)
        if stated is None:
            refused.append(
                f"{label}: its sentence is gone from {path} — re-point the pattern "
                f"in tools/hosted_api_cite_census.py at the new wording")
            continue
        want = [measured[k] for k in keys]
        if any(value is None for value in want):
            # `census()` returns None for the min/max of an EMPTY side. Writing it
            # would put the word `None` into prose and, worse, destroy the sentence
            # the NEXT pass must match to repair it — after which the tool prints
            # "nothing was written" over a file it has already modified. Refusing
            # here keeps that report true: `rewritten` is never persisted.
            refused.append(
                f"{label}: the census has no figure for {keys} — that side of the "
                f"pivot is EMPTY, so there is no measured number to state")
            continue
        if stated == want:
            if not refresh:
                print(f"  ok    {label}: {stated} == measured")
            continue
        stale.append(f"{label}: {path} states {stated}, the tree measures {want}")
        if refresh:
            rewritten[path], n = _restate(rewritten[path], pattern, want)
            if n != 1:  # pragma: no cover — re.subn after a successful search
                refused.append(f"{label}: could not rewrite ({n} substitutions)")

    if refused:
        for line in refused:
            print(f"REFUSED  {line}", file=sys.stderr)
        print("hosted_api_cite_census: nothing was written", file=sys.stderr)
        return 2
    if not stale:
        print("hosted_api_cite_census: every stated figure is the measured one")
        return 0
    if not refresh:
        for line in stale:
            print(f"STALE  {line}", file=sys.stderr)
        print("hosted_api_cite_census: re-state them with "
              "`uv run python tools/hosted_api_cite_census.py --refresh`",
              file=sys.stderr)
        return 1

    changed = 0
    for line in stale:
        print(f"  REFRESHED  {line}")
    for path, text in rewritten.items():
        if text != sources[path]:
            (REPO / path).write_text(text, encoding="utf-8")
            changed += 1
    # 1, not 0: the rewrite CHANGED THE POPULATION it just measured (see the module
    # docstring), so a correct pass can still leave figures describing the old tree.
    print(f"hosted_api_cite_census: rewrote {changed} file(s); re-measuring")
    return 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pivot", type=int, default=None,
                    help="pivot line for the printed split (default: the derived "
                         "LIFETIME_MEMORY_QUERY execution site)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true",
                      help="exit 1 when a note's stated figure is not the measured one")
    mode.add_argument("--refresh", action="store_true",
                      help="rewrite every stale figure in place (one pass, narrow)")
    args = ap.parse_args(argv)

    sites = lifetime_query_sites()
    if args.pivot is None and len(sites) != 1:
        raise SystemExit(
            f"hosted_api_cite_census: {len(sites)} "
            f"`query(LIFETIME_MEMORY_QUERY)` sites in {_HOSTED} ({sites}) — the "
            f"notes are written around exactly ONE; pass --pivot explicitly")
    pivot = args.pivot if args.pivot is not None else sites[0]

    if args.check or args.refresh:
        # The notes' figures describe the DERIVED site, so re-derive on every pass
        # and iterate to a fixpoint (bounded — 3 passes converge after a pivot move).
        for _ in range(4):
            sites = lifetime_query_sites()
            if len(sites) != 1:
                raise SystemExit(
                    f"hosted_api_cite_census: {len(sites)} "
                    f"`query(LIFETIME_MEMORY_QUERY)` sites in {_HOSTED} ({sites})")
            rc = _sweep(census(sites[0]), refresh=args.refresh)
            if rc != 1 or not args.refresh:
                return rc
            print("  ... re-measuring: a rewritten pivot cite is itself a cite target")
        print("hosted_api_cite_census: the notes did not converge in 4 passes — a "
              "rewrite is feeding a pattern it re-measures", file=sys.stderr)
        return 1

    data = census(pivot)
    data["pivot"] = pivot
    data["pivot_is_lifetime_site"] = pivot_is_the_lifetime_site(pivot)

    if args.json:
        print(json.dumps(data, indent=2, sort_keys=True))
        return 0

    print(f"hosted_api.py cite census (pivot {data['pivot']}):")
    print(f"  distinct targets   {data['distinct_targets']}")
    print(f"  below the pivot    {data['below_pivot']}  "
          f"({data['min_target']} … {data['max_below']})")
    print(f"  above the pivot    {data['above_pivot']}  "
          f"({data['above_min']} … {data['max_above']})")
    print(f"  at the pivot       {data['at_pivot']}")
    print(f"  citing files       {data['citing_files']}")
    print(f"  not a cite         {data['non_cite_tokens']} token(s) name a "
          f"different file (e.g. `tests/test_hosted_api.py:<n>`)")
    print(f"  pivot is the LIFETIME_MEMORY_QUERY site: "
          f"{data['pivot_is_lifetime_site']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
