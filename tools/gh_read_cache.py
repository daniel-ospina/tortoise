#!/usr/bin/env python3
"""gh_read_cache — serve a DECLARED-INVARIANT GitHub read from cache, and refuse
to serve anything else.

Why this exists
---------------
The fleet shares ONE 5,000-call/hour REST budget across every lane on every box.
It emptied four times in one night (2026-11-05, last measured `core 0/5000`,
`reset_in 784s`). The deployed `gh` shim already bounds *rate* and *concurrency*
(2 in flight, 250 ms between starts, 60->900 s backoff, one 120 s wait budget per
call, single-flight refusals, rc=75 `gh-contended`). It bounds how FAST a caller
polls; nothing bounds HOW MANY calls it makes, and nothing remembers that an
answer already fetched cannot have changed. So 15 lanes polling an unchanged
value spend a call each, every time, which is arithmetic that cannot fit a
5,000/hour budget.

This tool is LOCAL TTL MEMOIZATION of the reads whose value only changes when a
human changes repository configuration.

⚠️ ONE DECLARED KEY IS NOT AN INVARIANT, AND IS LABELLED AS SUCH: `rate_limit`.
Its counters move on every other lane's call. It is cached 30 s purely as a
LATENCY cache on a route GitHub does not charge for, so for it `fresh` means
"fetched ≤30 s ago", NEVER "the budget is this value". A lane making a spending
decision off it should pass `--ttl 1` (or read it uncached) if that second
matters. Every other declared key is a true invariant. It saves the call outright: a fresh entry
is served with no request at all.

⛔ It does NOT make conditional requests. No ETag is read or sent and there is no
`If-None-Match`/304 handling here. That is a different lever (GitHub's REST
guidance: an authenticated conditional GET answering 304 "does not count against
your primary rate limit"), and it is the right fix for the reads this tool must
not cache — the MUTABLE ones, where a 304 is the server certifying "unchanged"
rather than us assuming it. Treating this file as that lever would be wrong, so
it is named as the recommended next step and implemented nowhere here.

The rule that makes it safe — and why it is not a general cache
--------------------------------------------------------------
**Stale bytes are never emitted as fresh.** A cache miss may be slow; it is never
allowed to be wrong. Concretely:

  - Only DECLARED INVARIANT keys are stored (see `INVARIANTS`). An undeclared key
    is refused (rc=2), not cached with a default TTL.
  - Mutable state reads are refused OUTRIGHT (rc=3), by key AND by command:
    `pr view`, `pr checks`, `pr list`, `run view`, `run list`, the
    `repos/{repo}/actions` family, and the fields `state`/`mergeStateStatus`/`status`/`conclusion`/
    `merged`/`mergeable` — in every spelling, including `--json`, `--jq`/`-q`,
    `--template`/`-t`, the `=`-attached forms, and jq's bracket index `.["state"]`.
    The denylist is a REFUSAL CONTRACT, not the safety property: `--argv` stores
    nothing, so a route it has never heard of cannot poison the cache — it only
    spends the call it would have spent anyway.
  - `--argv` is a **pass-through**: it fetches and emits, and **stores nothing**.
    An override is not the declared command, so it has no declared invariant to
    cache — and a denylist of routes is not an identity, which is exactly how a
    first cut cached a live PR body under the key `repo`.
  - Freshness must be PROVABLE. A missing, non-numeric, NaN or infinite
    `fetched_at`, a stamp more than `CLOCK_SKEW_TOLERANCE_S` (60 s) in the future, a
    corrupt entry, a nonsensical TTL, or an entry whose recorded identity (instance
    host + key + repo + base + argv) does not match the request are all MISSES — never
    "probably fine". A stamp *within* that tolerance is read as age 0: the clock of the
    writing process is not this process's clock, and refusing its own peers' writes
    would make the cache useless on a fleet whose boxes do not share a time source.
  - A failed re-fetch FAILS (rc=4) and emits NOTHING. It never falls back to the
    stale body sitting in the cache dir. That fallback is the tempting behaviour
    and the forbidden one.
  - A TTL may be SHRUNK by a caller and never extended.

What this is NOT (#1178 — one instrument, no second predicate)
--------------------------------------------------------------
This is not a second rate-limit predicate. It does not decide when the account
may spend a call — `gh-shim` owns that entirely and this tool never consults or
duplicates it. This is memoization, so the shim is never asked to pace a call
that did not need to happen.

Declared invariants (edit this table, not a call site)
-----------------------------------------------------
  repo          GET /repos/{repo}  projected with --jq to admin-only fields   3600 s
  rate_limit    GET /rate_limit (quota-EXEMPT, account-scoped)                 30 s

Deliberately NOT declared:
  - `protection` (`GET /repos/{repo}/branches/{base}/protection`). It answers **404 when
    the branch has no protection rules** — the common case. Caching it would mean storing
    a negative we cannot represent honestly; not caching it means every poll spends a call
    AND returns a retryable rc=4, i.e. a retry storm aimed at the budget this tool exists
    to protect. Representing the negative (a distinct non-retryable verdict + a negative
    sentinel on its own short TTL) is a follow-up, not a half-build.
  - the Actions runner list (autoscale changes it), every PR/run/check read (all mutable),
    and `.mergify.yml` (a file read, not an API read — no quota to save).

Cache identity is (key, host, account, repo, base, effective argv). `host` and `account`
are which SERVER and which CREDENTIAL answered — load-bearing for `/rate_limit` (whose
buckets are per instance and per token) and threaded into the request itself as
`--hostname`, so a host-prefixed `--repo` cannot fetch the default instance and file the
answer under another. `repo`/`base` participate ONLY
when the declared route actually references `{repo}`/`{base}`: keying them unconditionally
fragmented the cache per branch/per repo for routes that do not vary by either, spending
two calls for one answer; and omitting `base` when it IS used once served `main`'s policy
as `release/1.0`'s. `host` participates ALWAYS — it is which server answered, and the
`/rate_limit` buckets are per instance — so it is canonicalised (`GH_HOST`, else
`github.com`) rather than left blank, which would have made `o/n` and `github.com/o/n` two
entries for one repository.

Usage
-----
  gh_read_cache.py get <key> [--repo owner/name] [--base main] [--ttl N] [--explain] [--argv …]
  gh_read_cache.py status <key> [--repo owner/name] [--base main] [--ttl N]
  gh_read_cache.py keys
  gh_read_cache.py check

`--repo` is required ONLY for a route that references `{repo}` (so `get rate_limit` works
bare). `--argv` is a PASS-THROUGH: it fetches and emits and caches nothing.

Exit codes
----------
  0  a value was emitted — from cache (fresh) or from a successful fetch
  2  usage / undeclared key (nothing fetched, nothing written)
  3  REFUSED: a mutable state read (nothing fetched, nothing written)
  4  FAIL-CLOSED: the fetch failed, or freshness could not be proven and the
     fetch could not recover it. No value is emitted — never the stale one.

Seams
-----
  GH_READ_CACHE_DIR   cache directory (default ~/.pi/agent/state/gh-read-cache)
             GH_READ_CACHE_GH    path to the `gh` binary (default `gh` on PATH)
             GH_REPO             default for `--repo` (gh's own variable; both `OWNER/REPO`
                                 and gh's documented `HOST/OWNER/REPO` are accepted)
             GH_HOST             the instance, when `--repo` carries no `HOST/` prefix.
                                 ⛔ THIS SEAM SELECTS THE CACHE ENTRY, so a lane that sets
                                 it reads a different cache than one that does not —
                                 undocumented it was invisible (a reviewer greped for the
                                 env vars this tool reads and found only two).
             --gh / --cache-dir  the same as the first two, per invocation
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

SCHEMA = 3

# Provenance tolerance. A stamp further into the future than this cannot have its
# age computed, so it cannot be proven fresh.
CLOCK_SKEW_TOLERANCE_S = 60.0

# ── the declared registry ────────────────────────────────────────────────────
@dataclass(frozen=True)
class Invariant:
    key: str
    argv: tuple[str, ...]
    ttl: int
    why: str


INVARIANTS: dict[str, Invariant] = {
    "repo": Invariant(
        key="repo",
        # ⛔ --jq IS NOT COSMETIC. `GET /repos/{repo}` also carries
        # `open_issues_count`, `pushed_at`, `updated_at`, `stargazers_count`,
        # `forks_count`, `size` — live counters that move with NO admin action. Caching
        # the whole body served an hour-old live number as fresh, which is the exact
        # stale-verdict failure this tool forbids, reached through an invariant. The
        # projection is the invariant: only fields that change by admin action survive.
        argv=(
            "api",
            "repos/{repo}",
            "--jq",
            "{full_name,name,default_branch,visibility,private,archived,fork}",
        ),
        ttl=3600,
        why="repo identity / default_branch / visibility / private / archived / fork — admin action only. "
            "Projected with --jq so live counters (stars, open_issues_count, pushed_at) cannot be stored",
    ),
    "rate_limit": Invariant(
        key="rate_limit",
        argv=("api", "rate_limit"),
        ttl=30,
        why="⛔ NOT AN INVARIANT — a declared LATENCY cache. The counters move on every other "
            "lane's call, and the route is EXEMPT from the quota, so this saves round trips, "
            "never budget. `fresh` means 'fetched ≤30 s ago', never 'the budget is this'",
    ),
}

# ⛔ `protection` is DELIBERATELY NOT DECLARED, and the reason is a quota hazard rather
# than taste: `GET /repos/{repo}/branches/{base}/protection` answers **404 when the branch
# has no protection rules** — the common case. Caching it would require inventing a
# response shape for the negative; not caching it means every poll spends a call AND gets
# a retryable rc=4, so ~15 lanes retry a permanently-absent answer against the very
# 5,000/h budget this tool exists to protect. Representing the negative honestly is its
# own change (a distinct non-retryable verdict + a negative sentinel with its own short
# TTL), recorded as a follow-up rather than half-built here.
#
# Also NOT declared: the Actions runner list (autoscale changes it), every PR/run/check
# read (all mutable), and `.mergify.yml` (a file read, not an API read — no quota to save).

# ── mutable reads: refused, by name and by command ───────────────────────────
MUTABLE_KEYS: dict[str, str] = {
    "pr_state": "a PR's state/open-ness is exactly what a lane must not learn late",
    "merge_state_status": "mergeStateStatus gates a merge decision",
    "run_status": "a run's status/conclusion is the poll target itself",
    "pr_checks": "check results ARE the verdict",
    "issue_state": "issue state moves under every lane",
    "runners": "the Actions runner list is changed by autoscale",
}

# (leading argv tokens, why) — placeholders are resolved before comparison.
MUTABLE_ARGV: tuple[tuple[tuple[str, ...], str], ...] = (
    (("pr", "view"), "PR state/mergeStateStatus"),
    (("pr", "checks"), "check results are the verdict"),
    (("pr", "list"), "open-PR listing is live state"),
    (("pr", "status"), "PR state"),
    (("run", "view"), "run status/conclusion"),
    (("run", "list"), "run status"),
    (("run", "watch"), "run status"),
    (("api", "repos/{repo}/actions/runs"), "Actions run state"),
    (("api", "repos/{repo}/actions/workflows"), "Actions run state"),
    # The rest of the Actions family (`artifacts`, `caches`, `secrets`, `variables`) is
    # live too, and the docstring tells callers the Actions REST routes are refused — so
    # the prefix is the FAMILY, not two members of it. (Nothing is cached either way;
    # this keeps the documented refusal honest rather than narrowing the claim.)
    (("api", "repos/{repo}/actions"), "the Actions family is live state"),
    (("api", "repos/{repo}/commits"), "branch head moves constantly"),
    (("api", "repos/{repo}/pulls"), "PR state"),
    (("api", "repos/{repo}/issues"), "issue state"),
    (("api", "repos/{repo}/check-runs"), "check results are the verdict"),
    (("api", "repos/{repo}/check-suites"), "check results are the verdict"),
    # A graphql query is a live read and its content cannot be classified from argv, so it
    # is refused rather than guessed at (an under-match here spends a real call).
    (("api", "graphql"), "a graphql query is a live read with unclassifiable content"),
)

# Fields that are verdicts no matter which endpoint carries them.
MUTABLE_FIELDS = frozenset(
    {"state", "mergestatestatus", "status", "conclusion", "merged", "mergeable"}
)


class Refused(Exception):
    """A mutable read. rc=3 — nothing fetched, nothing written."""


class FailClosed(Exception):
    """Freshness unprovable and/or the fetch failed. rc=4 — no value emitted."""


class Usage(Exception):
    """A malformed request. rc=2 — nothing fetched, nothing written."""


def _repo_slug(repo: str) -> tuple[str, str]:
    """Split `OWNER/REPO` or gh's documented `HOST/OWNER/REPO` into (host, slug).

    ⛔ THE HOST IS RETURNED, NOT THROWN AWAY. An earlier cut accepted the host-prefixed
    form and then `parts = parts[1:]`-ed it into oblivion, so `github.com/acme/one` and
    `ghe.example.com/acme/one` shared ONE cache entry — one instance's `default_branch` /
    `visibility` served for another and reported fresh. The host is part of the question.

    ⛔ THREE SEGMENTS ARE ALWAYS `host/owner/repo`. Detecting a host by the presence of a
    dot refused `ghe/o/n` and `localhost:8080/o/n`, i.e. it rejected documented forms.
    """
    parts = repo.split("/")
    host = ""
    if len(parts) == 3:
        host, parts = parts[0], parts[1:]
    if len(parts) != 2 or not all(parts):
        raise Usage(f"--repo must be owner/name (or host/owner/name), got {repo!r}")
    for piece in ([host] if host else []) + parts:
        # A slug with whitespace or a query/fragment would reach gh as a different route.
        # ⛔ BRACES TOO: `--repo 'o/{repo}'` used to be substituted a SECOND time by
        # `_format_argv`, so the placeholder survived into `api repos/o/{repo}` and the
        # identity named a question the fetch never asked (falsifying the premise of
        # `test_no_unresolved_placeholder_ever_reaches_gh`). No real slug carries braces.
        if not piece or piece in (".", "..") or any(ch in piece for ch in " \t\n?#{},"):
            raise Usage(f"--repo segment {piece!r} is not a valid repository component")
    return host, "/".join(parts)


def _default_host() -> str:
    """The instance gh would talk to with no `HOST/` prefix and no `--hostname`.

    ⛔ CANONICALISED, NOT BLANK. Leaving it `""` made `--repo o/n` and `--repo
    github.com/o/n` two cache entries for ONE repository: `get` wrote one, `status` looked
    for the other and reported `present: false`, and the next `get` spent a call to fetch
    what was already on disk. That is the same fragmentation the `base` gate was fixed for.
    """
    raw = (os.environ.get("GH_HOST", "") or "").strip().lower()
    for scheme in ("https://", "http://"):
        if raw.startswith(scheme):
            raw = raw[len(scheme):]
    return (raw.rstrip("/") or "github.com")


def _resolve_repo(repo: str) -> tuple[str, str]:
    """(host, slug), with `GH_HOST` — else github.com — supplying an unspelled instance."""
    host, slug = _repo_slug(repo)
    return (host or _default_host()), slug


def _cache_dir(override: str | None = None) -> Path:
    chosen = override or os.environ.get("GH_READ_CACHE_DIR")
    base = Path(chosen) if chosen else (Path.home() / ".pi" / "agent" / "state" / "gh-read-cache")
    base.mkdir(parents=True, exist_ok=True)
    return base


def _gh_bin(override: str | None = None) -> str:
    return override or os.environ.get("GH_READ_CACHE_GH", "gh")


def _account_fingerprint() -> str:
    """A short digest of WHICH CREDENTIAL will answer.

    ⛔ `/rate_limit` IS ACCOUNT-SCOPED, NOT JUST INSTANCE-SCOPED. The host dimension was
    added in one cycle and the account was then the same bug one axis over: two tokens on
    one box shared one entry, so `GH_TOKEN=B` was handed `GH_TOKEN=A`'s remaining quota with
    rc=0 and `fresh: true`. That number is an INPUT TO A SPENDING DECISION, and the
    docstring's own `--ttl 1` advice cannot help — a one-second-old other account's number
    is still the wrong question's answer.

    Only a DIGEST is stored and logged: the token never reaches the cache file, the
    filename, or stderr. An absent token is a legal, distinct value (it is the anonymous
    bucket).

    ⛔ THE CREDENTIAL ONLY — NOT the `gh` binary path. Quota is held per TOKEN, so two
    binaries reading one token share one bucket; folding `--gh` in would make
    `get --gh /p/gh` and a later `status` (which defaults to `gh`) two different accounts
    and break the ordinary "write with get, read with status" pair.
    """
    tokens = "\x00".join(os.environ.get(k, "") for k in ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN"))
    return hashlib.sha256(tokens.encode()).hexdigest()[:16]


def _identity(
    key: str,
    repo: str,
    base: str,
    argv: list[str],
    template: tuple[str, ...],
    host: str = "",
    account: str = "",
) -> dict:
    """Everything that makes two cached answers DIFFERENT questions.

    `repo`/`host`/`base` participate ONLY when the declared route references them. Keying
    them unconditionally fragmented the cache for routes that do not vary by either (the
    account-scoped `/rate_limit` was split per repo, and a `{base}`-free route per branch)
    — two calls for one answer. Omitting `base` when it IS used once served `main`'s
    protection policy as `release/1.0`'s, so the rule is "when used".

    `host` accompanies `repo` because the same `owner/name` on two GitHub instances is two
    different repositories; dropping it served one instance's metadata for another.
    """
    uses_repo = any("{repo}" in a for a in template)
    uses_base = any("{base}" in a for a in template)
    return {
        "key": key,
        "repo": repo if uses_repo else "",
        # ⛔ THE HOST IS UNCONDITIONAL. `repo`/`base` are gated on "when used" because a
        # route that does not reference them cannot vary by them; the HOST is different —
        # it is WHICH SERVER ANSWERED, for every route including the account-scoped
        # `/rate_limit`, whose buckets are per instance. Gating it on `uses_repo` blanked
        # it for `rate_limit` and served one GHES instance's counters as another's, fresh.
        "host": host,
        # ⛔ THE CREDENTIAL DECIDES WHOSE QUOTA `/rate_limit` REPORTS (see
        # `_account_fingerprint`). Unconditional for the same reason as `host`: the account
        # is which ANSWERING AUTHORITY replied, not a property of the route.
        "account": account,
        "base": base if uses_base else "",
        "argv": list(argv),
    }


def _entry_name(ident: dict) -> str:
    blob = json.dumps(ident, sort_keys=True).encode()
    return f"{ident['key']}.{hashlib.sha256(blob).hexdigest()[:16]}.json"


# ── the freshness predicate ──────────────────────────────────────────────────
def entry_freshness(entry: object, now: float, ttl: int, expect: dict | None = None) -> tuple[bool, str]:
    """Is this entry PROVABLY fresh? Returns (fresh, reason).

    Every ambiguous case is a MISS. An unprovable age is not a small risk to be
    traded against a saved call; it is the thing this tool must never do.
    """
    if not isinstance(entry, dict):
        return False, "entry_unreadable"
    if entry.get("schema") != SCHEMA:
        return False, "schema_mismatch"
    if isinstance(ttl, bool) or not isinstance(ttl, int) or ttl <= 0:
        return False, "bad_ttl"
    if expect is not None:
        # An entry that answers a DIFFERENT question is not a hit, however fresh.
        for field in ("key", "repo", "host", "account", "base", "argv"):
            if entry.get(field) != expect.get(field):
                return False, f"identity_mismatch:{field}"
    fetched_at = entry.get("fetched_at")
    if isinstance(fetched_at, bool) or not isinstance(fetched_at, (int, float)):
        return False, "no_timestamp"
    # `json.loads` accepts the bare `NaN`/`Infinity` literals, and NaN fails every
    # comparison below — so a corrupt stamp would read as fresh FOREVER, which is
    # the worst possible answer for a cache that must never serve stale. A JSON integer
    # too large for a float (10**400-scale) makes `isfinite` raise, which used to escape
    # as an undocumented rc=1 traceback: a corrupt entry must be a MISS, never a crash.
    try:
        finite = math.isfinite(float(fetched_at))
    except (OverflowError, ValueError, TypeError):
        return False, "no_timestamp"
    if not finite:
        return False, "no_timestamp"
    # Freshness is proven for the PAYLOAD too, not just the envelope: an entry with no
    # usable `body` used to be served as a HIT with rc=0 and empty stdout (or raise
    # TypeError on `body: null`), i.e. a caller got "success" and no value.
    if not isinstance(entry.get("body"), str) or not entry["body"].strip():
        return False, "no_body"
    if fetched_at > now + CLOCK_SKEW_TOLERANCE_S:
        return False, "clock_skew_future"
    # The tolerance above is what decides the MISS; there is no age clamp here because a
    # negative age PASSES `age > ttl` BY DESIGN — a stamp inside the skew tolerance is
    # fresh. A clamp here would therefore change a VERDICT rather than a display, so the
    # display floor lives in `_reported_age`, where `age -5s` stops reading as corruption.
    age = now - float(fetched_at)
    if age > ttl:
        return False, "expired"
    return True, "fresh"


def _reported_age(entry: dict, now: float) -> float:
    """Age for DISPLAY only, floored at 0.

    A stamp inside the skew tolerance is fresh, so reporting `age -5s` reads as corruption
    to an operator. The clamp belongs here, where it changes what is printed.
    """
    return max(0.0, now - float(entry["fetched_at"]))


def _read_entry(path: Path) -> object | None:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        # Unreadable bytes are not evidence of anything — treat as absent.
        return None


def _write_entry(path: Path, entry: dict) -> None:
    """Atomic write. A SHORT write must not be installed as a truncated entry, and a
    failed install must not leave its temp file behind in the shared cache dir."""
    payload = json.dumps(entry, sort_keys=True).encode()
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.stem}.", suffix=".tmp")
    try:
        os.fchmod(fd, 0o600)
        written = 0
        while written < len(payload):
            written += os.write(fd, payload[written:])
        os.fsync(fd)
        os.close(fd)
        fd = -1
        os.replace(tmp, path)
    except BaseException:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ── the mutable guard ────────────────────────────────────────────────────────
def _prefix_pattern(tokens: tuple[str, ...]) -> re.Pattern[str]:
    """Compile a mutable prefix into a matcher where placeholders are wildcards.

    ⛔ WHY A PATTERN AND NOT A RESOLVED TOKEN LIST. Comparing resolved tokens needs a
    concrete `repo`, so it matches only when the caller happened to spell the slug the same
    way the tool resolved it. A route typed with the repo already spelled out
    (`--argv api repos/o/n/actions/runs`) — or typed while `--repo` is absent because the
    template has no `{repo}` — matched nothing, which is exactly how three Actions prefixes
    became dead code and an Actions body got cached under the key `repo`. A wildcard cannot
    be defeated by which form the caller used.
    """
    parts = []
    for tok in tokens:
        p = re.escape(tok)
        # ⛔ `{repo}` IS TWO SLUG SEGMENTS, not `\S*`. A wildcard that can swallow a `/`
        # re-split the path, so a repo legitimately named `foo/pulls` matched the
        # `.../pulls` route and the `repo` invariant was unservable for it. `{base}` keeps
        # `\S*` because a branch name may itself contain `/`.
        p = p.replace(re.escape("{repo}"), r"[^/\s]+/[^/\s]+").replace(re.escape("{base}"), r"\S*")
        parts.append(p)
    # ⛔ THE TERMINATOR MUST ACCEPT A SUB-PATH, A QUERY AND A FRAGMENT. `(?: |$)` alone
    # under-matched every route carrying a segment after the prefix
    # (`actions/runs/<id>/jobs`, `actions/workflows/x.yml`, `pulls/42`) AND every query
    # spelling — including `actions/runs?per_page=100`, which is the fleet's REAL polling
    # form. A following `/`, `?` or `#` is the same mutable family.
    return re.compile(" ".join(parts) + r"(?:[ /?#]|$)", re.IGNORECASE)


def _route_candidates(argv: tuple[str, ...]) -> list[str]:
    """The strings a mutable-route pattern is allowed to match.

    ⛔ NOT just `" ".join(argv)`, because global `gh` flags sit BETWEEN `api` and the
    route (`api --paginate repos/…`, `api -X GET repos/…`), so a position-0 prefix match
    is defeated by an ordinary flag. Each `repos/...` token is also offered with the `api`
    verb in front, which is the shape the patterns are written in.

    ⛔ AND A LEADING SLASH IS A THIRD SPELLING. `gh api /repos/o/n/pulls/42` is a real
    invocation (gh normalises it), so the route passed through — falsifying this file's own
    "however the caller spelled it" claim. A **PERCENT-ENCODED** segment is a fourth
    (`p%75lls` decodes to `pulls` inside gh): both are normalised before matching, because
    an unquoted spelling that gh resolves must not slip past the guard.
    """
    raw = list(argv)
    norm = [tok[1:] if tok.startswith("/") else tok for tok in raw]
    unq = [urllib.parse.unquote(tok) for tok in norm]
    candidates = [" ".join(raw), " ".join(norm), " ".join(unq)]
    candidates.extend(f"api {tok}" for tok in unq if tok.lower().startswith("repos/"))
    return candidates


def _verdict_field_names(tokens: tuple[str, ...]) -> set[str]:
    """The FIELD NAMES a caller projects — compared as whole names, never substrings.

    A verdict arrives via a projection flag (`--json state`, `--jq .conclusion`), never via
    a path segment. Scanning the whole command made a spelled-out slug look like a field
    request — `--argv api repos/o/state/labels` was refused as "names the verdict field
    'state'" — which is the same false refusal this guard already fixed for `--repo`.

    ⛔ BOTH SPELLINGS COUNT. `--json=state`, `--jq=.state`, `--template=.state` and the
    attached shorthands `-q.state` / `-t.state` are real gh invocations; matching only the
    separate-token form left four ways to ask for a verdict unscanned.

    ⛔ WHOLE NAMES, NOT SUBSTRINGS. A `field in text` test refused `--jq .merged_at`
    ("names the verdict field 'merged'") and `mergeable_state`, `state_reason` — legitimate
    reads rejected by a guard whose job is to reject verdicts. Names are extracted, then
    compared exactly.
    """
    picked: list[str] = []
    long_flags = ("--json", "--jq", "--template")
    short_flags = ("-q", "-t")
    for i, tok in enumerate(tokens):
        matched = False
        for f in long_flags:
            if tok == f and i + 1 < len(tokens):
                picked.append(tokens[i + 1])
                matched = True
            elif tok.startswith(f + "="):
                picked.append(tok[len(f) + 1:])
                matched = True
        if matched:
            continue
        for f in short_flags:
            if tok == f and i + 1 < len(tokens):
                picked.append(tokens[i + 1])
                break
            if tok.startswith(f) and len(tok) > len(f):
                picked.append(tok[len(f):])
                break
    names: set[str] = set()
    for val in picked:
        # `--json a,b` is a name list; `--jq .a.b` / `{a: .b}` are expressions.
        for piece in re.split(r"[,\s{}\[\]()|]+", val):
            piece = piece.strip().lstrip(".")
            if piece:
                names.add(piece.split(".")[-1].lower())
        for expr in re.findall(r"\.([A-Za-z_][A-Za-z0-9_]*)", val):
            names.add(expr.lower())
        # ⛔ JQ'S BRACKET-INDEX FORM IS A SEPARATE SPELLING. `.["state"]`,
        # `$x["mergeStateStatus"]`, `-q '.["conclusion"]'` and `.["state"] // empty` were
        # all SERVED rc=0: the quote characters defeated both the name splitter and the
        # `.field` regex, so five real verdict reads slipped past a guard that refused
        # `.state`. Only QUOTED bracket keys are read — a quoted STRING COMPARISON
        # (`map(select(.name=="state"))`) is a filter, not a projection, and stays allowed.
        for bracket in re.findall(r"\[\s*[\s\"'()]*([A-Za-z_][A-Za-z0-9_]*)[\s\"'()]*\]", val):
            names.add(bracket.lower())
    names.discard("")
    return names


_MUTABLE_PREFIXES: tuple[tuple[re.Pattern[str], tuple[str, ...], str], ...] = tuple(
    (_prefix_pattern(toks), toks, why) for toks, why in MUTABLE_ARGV
)


def refuse_if_mutable(
    key: str,
    formatted_argv: tuple[str, ...],
    caller_tokens: tuple[str, ...],
    repo: str,
    base: str,
) -> None:
    """Raise Refused for anything that is a live verdict.

    Two halves on purpose, inspecting DIFFERENT things:

      - the PREFIX test runs on the formatted command against placeholder-agnostic
        patterns, so `api repos/o/n/actions/runs` is recognised whatever the repo is called
        and however the caller spelled it;
      - the FIELD test runs on the CALLER's own tokens (the declared template or an
        `--argv` override), never on the resolved slug — otherwise a repo legitimately
        named `status/status` makes an invariant read unservable.

    This is defence in depth, not the identity: `--argv` writes nothing at all,
    so a route this denylist has never heard of cannot poison the cache.
    """
    if key in MUTABLE_KEYS:
        raise Refused(f"{key!r} is a MUTABLE state read ({MUTABLE_KEYS[key]}) — refusing to cache it")
    candidates = _route_candidates(formatted_argv)
    for pattern, toks, why in _MUTABLE_PREFIXES:
        if formatted_argv and any(pattern.match(c) for c in candidates):
            raise Refused(
                f"command {' '.join(toks)!r} (as {' '.join(formatted_argv)!r}) is a MUTABLE "
                f"state read ({why}) — refusing to cache it"
            )
    lowered = _verdict_field_names(caller_tokens)
    for field in sorted(MUTABLE_FIELDS & lowered):
        raise Refused(f"the command projects the verdict field {field!r} — refusing to cache it")


def _format_argv(argv: tuple[str, ...], repo: str, base: str) -> list[str]:
    return [a.replace("{repo}", repo).replace("{base}", base) for a in argv]


# ── fetch ────────────────────────────────────────────────────────────────────
def fetch(argv: list[str], gh: str | None = None, host: str = "") -> tuple[str, bool]:
    """Run gh; return (stdout, ok). Stderr is captured, never merged into stdout.

    A successful call's stderr is deliberately NOT replayed — the harness layers
    above parse stdout as JSON, and a banner on a good answer turns `--json` into
    a decode error (the rule `gh-shim` states and enforces).

    ⛔ THE RESOLVED HOST IS PART OF THE REQUEST, NOT ONLY OF THE KEY. `_format_argv`
    substitutes the SLUG into `repos/owner/name`, so `--repo ghe.example.com/o/n` asked
    github.com for `o/n` and then filed the answer under `host: ghe.example.com` — minting
    an entry that certifies the wrong server for a full hour, and spending two calls for one
    answer because both hosts send byte-identical argv. `--hostname` is gh's documented
    spelling, and it sits on the `api` command where a global flag is already accepted.
    """
    child_env = dict(os.environ)
    run_argv = [*argv]
    if host and host != _default_host() and "--hostname" not in argv:
        # `api <route> --hostname <host>` — the flag is global, so appending is safe, and
        # the argv stored in the identity already carries `host` separately.
        run_argv = [*argv, "--hostname", host]
    try:
        proc = subprocess.run([_gh_bin(gh), *run_argv], capture_output=True, text=True, timeout=120, env=child_env)
    except (OSError, subprocess.TimeoutExpired, UnicodeDecodeError) as exc:
        # ⛔ UnicodeDecodeError TOO: `text=True` decodes inside `subprocess.run`, and a
        # binary route (`.../actions/runs/<id>/logs`, an octet-stream asset) used to escape
        # as an undocumented rc=1 traceback — unclassifiable by any caller's retry logic.
        # `errors="replace"` would be worse: it caches a CORRUPTED body as a body.
        sys.stderr.write(f"gh_read_cache: could not run gh: {exc}\n")
        return "", False
    if proc.returncode != 0:
        sys.stderr.write(
            f"gh_read_cache: gh exited {proc.returncode}: {proc.stderr.strip()[:400]}\n"
        )
        return "", False
    return proc.stdout, True


def _effective_ttl(requested: int | None, declared: int) -> int:
    """A caller may SHRINK a TTL, never extend one.

    The design puts TTLs in the `INVARIANTS` table; an unbounded `--ttl` override
    would let one caller pin repo metadata as fresh for a year for every lane
    sharing the cache directory.
    """
    if requested is None:
        return declared
    if isinstance(requested, bool) or requested <= 0:
        raise Usage(f"--ttl must be a positive integer, got {requested!r}")
    return min(requested, declared)


def cmd_get(args: argparse.Namespace) -> int:
    now = time.time()
    try:
        # (1) KEY-level refusal FIRST, so a mutable key reports rc=3 (REFUSED)
        # rather than rc=2 ("undeclared").
        refuse_if_mutable(args.key, (), (args.key,), "", "")
        decl = INVARIANTS.get(args.key)
        if decl is None:
            raise Usage(
                f"{args.key!r} is not a declared invariant "
                f"(declared: {', '.join(sorted(INVARIANTS))}) — refusing to invent a TTL"
            )
        ttl = _effective_ttl(args.ttl, decl.ttl)
        template = tuple(args.argv) if args.argv else decl.argv
        # `--repo` is REQUIRED only where the route actually uses `{repo}`. Demanding it
        # for the account-scoped `/rate_limit` both made the key unusable bare and split
        # the cache per repo — two calls for one answer.
        uses_repo = any("{repo}" in a for a in template)
        host, repo = _resolve_repo(args.repo) if uses_repo else (_default_host(), "")
        argv = _format_argv(template, repo, args.base)
        # (2) COMMAND-level refusal, as defence in depth (see refuse_if_mutable).
        refuse_if_mutable(args.key, tuple(argv), tuple(template), repo, args.base)
    except Refused as exc:
        sys.stderr.write(f"gh_read_cache: REFUSED — {exc}\n")
        return 3
    except Usage as exc:
        sys.stderr.write(f"gh_read_cache: {exc}\n")
        return 2

    # ── `--argv` is a PASS-THROUGH: fetch, emit, store NOTHING. ──────────────
    # An override is not the declared command, so it has no declared invariant to
    # cache. Without this a route the denylist had never heard of
    # (`--argv api repos/{repo}/pulls/42`) cached a LIVE PR body under the key
    # `repo`, and a later plain `get repo` served it as the repo answer.
    # `args.argv is not None` (not truthiness): an EMPTY remainder parses to `[]`, and a
    # flag-present-with-no-tokens must not silently fall through to the caching path.
    if args.argv is not None:
        if not args.argv:
            sys.stderr.write("gh_read_cache: --argv given with no command tokens\n")
            return 2
        body, ok = fetch(argv, args.gh, host)
        if not ok:
            sys.stderr.write("gh_read_cache: --argv pass-through fetch failed — no value emitted\n")
            return 4
        sys.stdout.write(body)
        return 0

    ident = _identity(decl.key, repo, args.base, argv, decl.argv, host, _account_fingerprint())
    try:
        cache_dir = _cache_dir(args.cache_dir)
    except OSError as exc:
        # An uncreatable cache dir must DEGRADE, not crash: it used to raise
        # FileExistsError/PermissionError out of cmd_get as an undocumented rc=1 that no
        # caller's retry classifier could place.
        sys.stderr.write(f"gh_read_cache: cache dir unavailable ({exc}) — reading without a cache\n")
        cache_dir = None
    path = (cache_dir / _entry_name(ident)) if cache_dir is not None else None

    entry = _read_entry(path) if path is not None else None
    fresh, reason = entry_freshness(entry, now, ttl, expect=ident)
    if fresh:
        assert isinstance(entry, dict)
        if args.explain:
            age = _reported_age(entry, now)
            sys.stderr.write(f"gh_read_cache: cache HIT {decl.key} (age {age:.0f}s of {ttl}s)\n")
        sys.stdout.write(entry["body"])
        return 0

    body, ok = fetch(argv, args.gh, host)
    if not ok:
        # ⛔ NO FALLBACK. The stale body may be sitting in `entry`; emitting it
        # here is precisely the "stale as fresh" defect this tool forbids.
        sys.stderr.write(
            f"gh_read_cache: FAIL-CLOSED on {decl.key} ({reason}) — the refresh failed and no "
            "value will be emitted (a stale value is never served as fresh)\n"
        )
        return 4
    if not body.strip():
        # A declared invariant cannot legitimately answer empty. Storing "" would mint a
        # HIT that emits nothing with rc=0 and never self-heals for a whole TTL.
        sys.stderr.write(
            f"gh_read_cache: FAIL-CLOSED on {decl.key} — gh succeeded but returned an empty body; "
            "an empty answer is not a cacheable one\n"
        )
        return 4

    # Emit FIRST: the call is already spent, and an unwritable cache directory must
    # degrade to "slower next time", never to "the answer you paid for is thrown
    # away and you get an undocumented rc=1".
    sys.stdout.write(body)
    if path is None:
        return 0
    try:
        # No `ttl` field: it was written and never read, and a caller's shorter TTL is
        # already enforced by `_effective_ttl` on the reading side.
        _write_entry(path, {**ident, "schema": SCHEMA, "body": body, "fetched_at": now})
    except OSError as exc:
        sys.stderr.write(f"gh_read_cache: could not write the cache entry ({exc}) — value emitted, not cached\n")
        return 0
    if args.explain:
        sys.stderr.write(f"gh_read_cache: cache MISS {decl.key} ({reason}) — fetched and stored\n")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    now = time.time()
    try:
        # The mutable guard applies to `status` too, so the documented exit-code
        # contract holds on both paths (a review finding: `status pr_state` used
        # to answer rc=2 while `get pr_state` answered rc=3).
        refuse_if_mutable(args.key, (), (args.key,), "", "")
        decl = INVARIANTS.get(args.key)
        if decl is None:
            raise Usage(f"{args.key!r} is not a declared invariant")
        ttl = _effective_ttl(args.ttl, decl.ttl)
        uses_repo = any("{repo}" in a for a in decl.argv)
        host, repo = _resolve_repo(args.repo) if uses_repo else (_default_host(), "")
    except Refused as exc:
        sys.stderr.write(f"gh_read_cache: REFUSED — {exc}\n")
        return 3
    except Usage as exc:
        sys.stderr.write(f"gh_read_cache: {exc}\n")
        return 2

    argv = _format_argv(decl.argv, repo, args.base)
    ident = _identity(decl.key, repo, args.base, argv, decl.argv, host, _account_fingerprint())
    try:
        cache_dir: Path | None = _cache_dir(args.cache_dir)
    except OSError as exc:
        sys.stderr.write(f"gh_read_cache: cache dir unavailable ({exc})\n")
        cache_dir = None
    path = (cache_dir / _entry_name(ident)) if cache_dir is not None else None
    entry = _read_entry(path) if path is not None else None
    fresh, reason = entry_freshness(entry, now, ttl, expect=ident)
    age = None
    if (
        isinstance(entry, dict)
        and isinstance(entry.get("fetched_at"), (int, float))
        and not isinstance(entry.get("fetched_at"), bool)
    ):
        try:
            age = _reported_age(entry, now) if math.isfinite(float(entry["fetched_at"])) else None
        except (OverflowError, ValueError, TypeError):
            age = None
    print(
        json.dumps(
            {
                "key": args.key,
                "repo": repo,
                "host": host,
                "base": args.base,
                "present": entry is not None,
                "fresh": fresh,
                "reason": reason,
                "age_s": None if age is None else round(age, 1),
                "ttl_s": ttl,
                "path": None if path is None else str(path),
            },
            indent=2,
        )
    )
    return 0


def cmd_keys(_args: argparse.Namespace) -> int:
    for key in sorted(INVARIANTS):
        d = INVARIANTS[key]
        print(f"{key:<14} ttl={d.ttl:<6} {' '.join(d.argv)}\n{'':<14} {d.why}")
    if MUTABLE_KEYS:
        print("\nREFUSED (mutable — never cached):")
        for key in sorted(MUTABLE_KEYS):
            print(f"  {key:<22} {MUTABLE_KEYS[key]}")
    return 0


def cmd_check(_args: argparse.Namespace) -> int:
    """Exercise the predicate on the cases that must never be called fresh."""
    now = 1_000_000.0
    ident = {"key": "repo", "repo": "o/n", "host": "", "base": "", "argv": ["api", "repos/o/n"]}
    good = {"schema": SCHEMA, "fetched_at": now - 10, "body": '{"id":1}', **ident}
    other = {**good, "repo": "o/m"}
    cases = [
        (good, 60, True),
        ({**good, "fetched_at": now - 10}, 5, False),
        ({**good, "fetched_at": now}, 0, False),  # age 0 PASSES `age > ttl` -> only the ttl guard can refuse it
        ({**good, "fetched_at": now}, -1, False),
        ({**good, "fetched_at": now}, True, False),
        ({**good, "fetched_at": now + 4_000}, 60, False),
        ({**good, "fetched_at": now + 5}, 60, True),  # inside skew tolerance -> age 0
        # ⛔ THE TOLERANCE BOUNDARY IS THE ONLY THING BOUNDING SERVED UNPROVEN FRESHNESS,
        # so it is pinned from both sides here AND in the suite. With `--ttl 1` (the
        # docstring's own advice for `rate_limit`) a stamp 59 s in the future is served
        # with the age DISPLAYED as 0 — the tolerance, not the requested TTL, decides.
        # Mutants 60→10 and 60→600 both left a 40-test suite green without these.
        ({**good, "fetched_at": now + 59}, 1, True),
        ({**good, "fetched_at": now + 61}, 1, False),
        ({**good, "fetched_at": now + 61}, 600, False),
        ({"schema": SCHEMA, "fetched_at": now - 10, **ident}, 0, False),        ({**good, "fetched_at": float("nan")}, 60, False),
        ({**good, "fetched_at": float("inf")}, 60, False),
        ({k: v for k, v in good.items() if k != "fetched_at"}, 60, False),
        ({k: v for k, v in good.items() if k != "body"}, 60, False),
        ({**good, "body": None}, 60, False),
        ({**good, "body": "   "}, 60, False),
        ({**good, "fetched_at": "recent"}, 60, False),
        ({**good, "fetched_at": True}, 60, False),
        (other, 60, False),
        ({"schema": SCHEMA}, 60, False),
        ({}, 60, False),
        (None, 60, False),
    ]
    bad = []
    for entry, ttl, want in cases:
        got, reason = entry_freshness(entry, now, ttl, expect=ident)
        if got is not want:
            bad.append((entry, ttl, want, got, reason))
    if bad:
        for b in bad:
            sys.stderr.write(f"gh_read_cache: check FAILED {b}\n")
        return 4
    print(f"ok — {len(cases)} freshness cases behave; {len(INVARIANTS)} invariants declared")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="gh_read_cache.py",
        description="Serve a declared-invariant GitHub read from cache; refuse anything mutable.",
    )
    # Seams accepted AFTER the subcommand so one invocation can point at a stub.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--gh", default=None, help="path to the gh binary (default: gh on PATH)")
    common.add_argument("--cache-dir", default=None, help="cache directory override")

    sub = p.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("get", parents=[common], help="emit the value (cache hit, or a fresh fetch)")
    g.add_argument("key")
    g.add_argument("--repo", default=os.environ.get("GH_REPO", ""))
    g.add_argument("--base", default="main")
    g.add_argument("--ttl", type=int, default=None, help="shrink the declared TTL (never extend it)")
    # REMAINDER, not "+": the override is a raw gh argv and legitimately contains
    # flags of its own (`--json state`). "+" stops at the first dash-token, which
    # would let a verdict field slip past the classifier. Must be the LAST arg.
    g.add_argument(
        "--argv",
        nargs=argparse.REMAINDER,
        default=None,
        help="override the declared command — PASS-THROUGH: fetched and emitted, never cached "
             "(must be last; the mutable guard still applies)",
    )
    g.add_argument("--explain", action="store_true", help="note HIT/MISS on stderr")
    g.set_defaults(func=cmd_get)

    s = sub.add_parser("status", parents=[common], help="is there an entry for this key, and is it fresh?")
    s.add_argument("key")
    s.add_argument("--repo", default=os.environ.get("GH_REPO", ""))
    s.add_argument("--base", default="main")
    s.add_argument("--ttl", type=int, default=None)
    s.set_defaults(func=cmd_status)

    k = sub.add_parser("keys", parents=[common], help="the declared registry")
    k.set_defaults(func=cmd_keys)

    c = sub.add_parser("check", parents=[common], help="self-check the freshness predicate")
    c.set_defaults(func=cmd_check)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
