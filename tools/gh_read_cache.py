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

Cache identity is (key, host, account, repo, base, effective argv). `host` is which SERVER
answered: it participates for EVERY route and is threaded into the request itself (`gh api`'s
`--hostname`, or the `host/owner/repo` argument for a porcelain verb), so a host-prefixed
`--repo` can neither fetch the default instance nor file the answer under another. `account`
is which CREDENTIAL answered: it participates only for an ACCOUNT-SCOPED route
(`/rate_limit`, whose buckets are per instance and per token), because keying the `repo`
projection by credential would cost every lane in the fleet a call for one unchanged answer.
`repo`/`base` participate ONLY
when the declared route actually references `{repo}`/`{base}`: keying them unconditionally
fragmented the cache per branch/per repo for routes that do not vary by either, spending
two calls for one answer; and omitting `base` when it IS used once served `main`'s policy
as `release/1.0`'s. `host` is canonicalised (`GH_HOST`, else `github.com` — case, scheme and
a trailing slash folded) rather than left blank, which would have made `o/n` and
`github.com/o/n` two entries for one repository.

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
             GH_CONFIG_DIR       read for `hosts.yml` to resolve the CREDENTIAL for an
                                 account-scoped route when no token is in the environment.
                                 No token is ever written to the cache; only its digest.
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

SCHEMA = 4

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
    # ⛔ TRUE ONLY WHERE THE ANSWER DEPENDS ON WHO ASKED. `/rate_limit` is per CREDENTIAL,
    # so a second token must not be served the first's counters. The `repo` projection is
    # a property of the REPOSITORY (`full_name`, `default_branch`, `visibility`, ...), so
    # keying it by credential would cost one call PER LANE for one unchanged answer — the
    # quota regression this tool exists to remove, reintroduced by an over-broad fix.
    account_scoped: bool = False


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
        account_scoped=True,
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
    # ⛔ THE SPELLED HOST IS CANONICALISED TOO. `_default_host` folded case/scheme/slash but
    # a host from the `HOST/OWNER/REPO` prefix was used verbatim, so `ghe.example.com/o/n` and
    # `GHE.example.com/o/n` were two entries and two calls for one answer - the fragmentation
    # the canonicalisation exists to prevent, one spelling over.
    return _canonical_host(host), "/".join(parts)


def _canonical_host(raw: str) -> str:
    """Lower-case, scheme-stripped, slash-trimmed host; blank stays blank."""
    raw = (raw or "").strip().lower()
    for scheme in ("https://", "http://"):
        if raw.startswith(scheme):
            raw = raw[len(scheme):]
    return raw.rstrip("/")


def _default_host() -> str:
    """The instance gh would talk to with no `HOST/` prefix and no `--hostname`.

    ⛔ CANONICALISED, NOT BLANK. Leaving it `""` made `--repo o/n` and `--repo
    github.com/o/n` two cache entries for ONE repository: `get` wrote one, `status` looked
    for the other and reported `present: false`, and the next `get` spent a call to fetch
    what was already on disk. That is the same fragmentation the `base` gate was fixed for.
    """
    return _canonical_host(os.environ.get("GH_HOST", "")) or "github.com"


def _resolve_repo(repo: str) -> tuple[str, str]:
    """(host, slug), with `GH_HOST` — else github.com — supplying an unspelled instance."""
    host, slug = _repo_slug(repo)
    return (host or _default_host()), slug


def _cache_dir(override: str | None = None) -> Path:
    chosen = override or os.environ.get("GH_READ_CACHE_DIR")
    base = Path(chosen) if chosen else (Path.home() / ".pi" / "agent" / "state" / "gh-read-cache")
    base.mkdir(parents=True, exist_ok=True)
    return base


def _names_host(token: str, host: str) -> bool:
    """Does this template token already name `host`?

    ⛔ CASE-FOLDED, AND `-R`/`-r` PREFIXES STRIPPED. gh resolves
    `repo view GHE.example.com/o/n` and `-Rghe.example.com/o/n` to the same server, so
    comparing the raw token refused a working invocation while serving its lowercase spelling
    - the same false refusal, one case over. The `-R`/`-r` forms are the flag spellings gh
    documents for the repo argument.
    """
    t = token.lower()
    for prefix in ("--repo=", "-r=", "-r"):
        if t.startswith(prefix):
            t = t[len(prefix):]
            break
    return t == host or t.startswith(f"{host}/")


def _gh_bin(override: str | None = None) -> str:
    return override or os.environ.get("GH_READ_CACHE_GH", "gh")


def _account_fingerprint(host: str = "") -> tuple[str, bool]:
    """A short digest of WHICH CREDENTIAL will answer, and whether it is KNOWABLE.

    ⛔ `/rate_limit` IS ACCOUNT-SCOPED, NOT JUST INSTANCE-SCOPED. The host dimension was
    added in one cycle and the account was then the same bug one axis over: two tokens on
    one box shared one entry, so `GH_TOKEN=B` was handed `GH_TOKEN=A`'s remaining quota with
    rc=0 and `fresh: true`. That number is an INPUT TO A SPENDING DECISION, and the
    docstring's own `--ttl 1` advice cannot help — a one-second-old other account's number
    is still the wrong question's answer.

    ⛔ THERE ARE TWO CREDENTIAL SOURCES, AND THE ENV ONE IS THE RARER. Reading only the env
    vars was itself the same defect once more: a box authenticated with `gh auth login`
    (or `gh auth switch`, or a second `GH_CONFIG_DIR`) has NO token in the environment, so
    every such account collapsed into one digest. Both documented spellings of each variable
    are read (`GH_ENTERPRISE_TOKEN` **and** `GITHUB_ENTERPRISE_TOKEN`), and when neither is
    set the `oauth_token` is read from gh's own `hosts.yml` — the file gh reads — for the
    resolved host.

    ⛔ READ THE FILE, DO NOT SHELL OUT TO `gh auth token`. A subprocess buys nothing here (the
    token is in a plain file) and it made the tool's own call-counting tests unusable, since
    every read of a cached value would have invoked `gh` — the exact cost this tool exists
    to remove.

    Returns `(digest, resolvable)`. `resolvable=False` means NOTHING could be learned about
    the credential, and the caller must then refuse to cache an account-scoped route rather
    than pool every unknown account together (see `cmd_get`) — serving an unknown account
    another's quota is worse than spending an exempt call.

    Only a DIGEST is stored or logged: the token never reaches the cache file, the filename,
    or stderr. The `gh` binary path is deliberately NOT in the digest — quota is held per
    token, so folding `--gh` in would make `get --gh /p/gh` and a later `status` (default
    `gh`) two different accounts and break the write-then-read pair.
    """
    token = _env_token(host) or _config_token(host)
    if not token:
        return "", False
    # ⛔ HASH THE TOKEN, NOT THE RECORD. Hashing the four-slot record made ONE secret up to
    # four identities (`GH_TOKEN=X`, `GITHUB_TOKEN=X`, `"X "`, `hosts.yml: X`), splitting a
    # single account into separate entries. The token is the account.
    return hashlib.sha256(token.encode()).hexdigest()[:16], True


def _env_token(host: str) -> str:
    """The token gh would take from the ENVIRONMENT for `host`, or "".

    ⛔ gh SELECTS BY HOST, SO THIS MUST TOO. Joining all four variables into one record made
    *any* exported enterprise token label *every* host — so a box exporting
    `GH_ENTERPRISE_TOKEN` for its GHES instance keyed its `github.com` reads by that token,
    and two real `github.com` accounts in one `hosts.yml` pooled. gh's own rule (its help
    text, and `gh auth token` empirically): `GH_TOKEN`/`GITHUB_TOKEN` apply to `github.com`,
    `github.localhost`, and subdomains of `github.com`, `github.localhost` and `ghe.com`;
    `GH_ENTERPRISE_TOKEN`/`GITHUB_ENTERPRISE_TOKEN` apply when
    the command targets a GH Enterprise SERVER host — and when the variable for the OTHER
    family is the only one set, gh ignores it and falls back to `hosts.yml`.
    """
    # ⛔ MIRROR gh'S NORMALISATION, DO NOT HAND-LIST. go-gh's rule is not "github.com plus
    # `*.ghe.com`": it folds `*.github.com` -> `github.com` and `*.github.localhost` ->
    # `github.localhost`, and `github.localhost` (gh's local-API / Codespaces host) belongs to
    # the GH_TOKEN family too. Omitting it made gh send `GH_TOKEN` while the digest fell through
    # to the enterprise family — two genuinely different GH_TOKENs hashed the same value and
    # `rate_limit` was served across them as fresh.
    h = host.lower()
    dotcom = (
        h in ("", "github.com", "github.localhost")
        or h.endswith(".ghe.com")
        or h.endswith(".github.com")
        or h.endswith(".github.localhost")
    )
    keys = ("GH_TOKEN", "GITHUB_TOKEN") if dotcom else ("GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN")
    for key in keys:
        value = os.environ.get(key, "").strip()
        if value:
            return value
    return ""


def _config_token(host: str) -> str:
    """gh's `hosts.yml` credential for `host` — the token gh itself would send.

    `hosts.yml` is small, flat YAML (`<host>:\n    oauth_token: …\n    user: …`), so the
    host's block is found by its own line and the token by an indented key. When no block
    matches exactly, a SINGLE `oauth_token` in the file is taken (the common one-account
    case); two or more is ambiguous and reported as unresolvable rather than guessed.

    ⛔ THE DIRECTORY FOLLOWS gh'S DOCUMENTED PRECEDENCE — `GH_CONFIG_DIR`, then
    `$XDG_CONFIG_HOME/gh`, then `~/.config/gh`. Skipping the middle step made the tool read a
    DIFFERENT `hosts.yml` from the one gh uses: on a box selecting its account with
    `XDG_CONFIG_HOME`, the stale default file's token became the digest for every lane, so two
    live accounts pooled onto it — and when no stale file existed the symptom was merely a
    permanently uncached `rate_limit`, which is why it hid.
    """
    config_dir = os.environ.get("GH_CONFIG_DIR") or (
        os.path.join(os.environ["XDG_CONFIG_HOME"], "gh")
        if os.environ.get("XDG_CONFIG_HOME")
        else str(Path.home() / ".config" / "gh")
    )
    try:
        text = Path(config_dir, "hosts.yml").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""

    wanted = (host or "github.com").lower()
    block: str | None = None
    found: dict[str, str] = {}
    for line in text.splitlines():
        if line and not line[0].isspace():
            block = line.rstrip().rstrip(":").strip().strip("'\"").lower()
            continue
        stripped = line.strip()
        if stripped.startswith("oauth_token:") and block:
            value = stripped.split(":", 1)[1].strip().strip("'\"")
            if value:
                found[block] = value
    if wanted in found:
        return found[wanted]
    if len(found) == 1:
        return next(iter(found.values()))
    return ""


def _identity(
    key: str,
    repo: str,
    base: str,
    argv: list[str],
    template: tuple[str, ...],
    host: str = "",
    account: str = "",
    account_scoped: bool = False,
) -> dict:
    """Everything that makes two cached answers DIFFERENT questions.

    `repo`/`base` participate ONLY when the declared route references them. Keying
    them unconditionally fragmented the cache for routes that do not vary by either (the
    account-scoped `/rate_limit` was split per repo, and a `{base}`-free route per branch)
    — two calls for one answer. Omitting `base` when it IS used once served `main`'s
    protection policy as `release/1.0`'s, so the rule is "when used".

    `host` and `account` are NOT gated on "when used": the host is which SERVER answered and
    the account is which CREDENTIAL answered, so both are true of every route. `account` is
    gated on the ROUTE BEING ACCOUNT-SCOPED instead — per-credential for `/rate_limit`, out
    of `repo` (whose projection does not vary by who asked, and keying it per token would
    cost every lane in the fleet one call for one unchanged answer).
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
        # ⛔ THE ACCOUNT IS GATED ON THE ROUTE, NOT UNCONDITIONAL. `/rate_limit` is per
        # credential, so a second token must MISS. The `repo` projection is a property of the
        # REPOSITORY, so keying it per credential cost one call PER LANE for one unchanged
        # answer - reintroducing the exact quota regression this tool exists to remove.
        "account": account if account_scoped else "",
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
def _accepts_hostname(argv: list[str]) -> bool:
    """`--hostname` is a flag of `gh api` ONLY — appending it elsewhere is a parse error.

    The two declared routes are both `api`, so the caching path was safe; an `--argv`
    pass-through of a porcelain verb (`repo view {repo}`) got the flag appended and died at
    flag parsing (`unknown flag: --hostname`) instead of being served — a call that used to
    run now failed, without ever asking the right server.
    """
    return bool(argv) and argv[0] == "api"


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
    supplied_host = any(t == "--hostname" or t.startswith("--hostname=") for t in argv)
    if host and host != _default_host() and not supplied_host and _accepts_hostname(argv):
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
        if uses_repo and not args.repo:
            raise Usage("--repo is required for this key")
        # ⛔ THE HOST COMES FROM `--repo` EVEN WHEN THE TEMPLATE HAS NO `{repo}`. Taking
        # `_default_host()` there made `--argv repo list --repo ghe.example.com/o/n` ask the
        # DEFAULT instance and never express the spelled one — a live fetch of the wrong
        # server. `uses_repo` decides whether the SLUG is formatted in, not which host is
        # meant.
        host, repo = _resolve_repo(args.repo) if args.repo else (_default_host(), "")
        if not uses_repo:
            repo = ""
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
        # ⛔ A PORCELAIN VERB HAS NO `--hostname`. `--hostname` is a flag of `gh api` alone, so
        # appending it to `repo view {repo}` died at flag parsing (`unknown flag: --hostname`)
        # — a call that used to run now failed, and the host was still not expressed. For those
        # verbs the host is spelled INSIDE the local-repo argument, which `{repo}` already
        # occupies, so it is re-formatted as `host/owner/name` (the form gh's own `-R` and
        # positional parsing accept).
        pass_argv = argv
        named_host = bool(host) and host != _default_host()
        # ⛔ THE CALLER MAY HAVE ALREADY EXPRESSED THE HOST THEMSELVES — as a literal
        # `host/owner/name` token, or via `-R host/owner/name`. Refusing on "no `{repo}`" alone
        # rejected working invocations (`--argv repo view ghe.example.com/o/n`), and it fired
        # for any lane exporting `GH_REPO=<host>/<owner>/<repo>` without typing `--repo`.
        host_expressed = any(_names_host(t, host) for t in template)
        if named_host and not _accepts_hostname(template) and repo:
            pass_argv = _format_argv(template, f"{host}/{repo}", args.base)
        elif named_host and not _accepts_hostname(template) and not host_expressed:
            # ⛔ NOWHERE TO PUT IT. A porcelain verb has no `--hostname`, and a template with
            # no `{repo}` has no argument to carry `host/owner/repo` — so the pass-through used
            # to ask the DEFAULT instance while the caller had named another, silently. Refuse
            # instead of fetching the wrong server: an actuator that cannot deliver is a defect,
            # not a status.
            sys.stderr.write(
                f"gh_read_cache: --repo names the host {host!r} but this command has no "
                "`--hostname` flag (only `gh api` has one) and no `{repo}` to carry "
                "host/owner/name — refusing rather than querying the default instance\n"
            )
            return 2
        body, ok = fetch(pass_argv, args.gh, host)
        if not ok:
            sys.stderr.write("gh_read_cache: --argv pass-through fetch failed — no value emitted\n")
            return 4
        sys.stdout.write(body)
        return 0

    account, account_known = _account_fingerprint(host)
    if decl.account_scoped and not account_known:
        # AN UNKNOWABLE CREDENTIAL MUST NOT BE POOLED WITH EVERY OTHER UNKNOWABLE ONE.
        # `gh auth token` failing (no login, broken config, stripped PATH) used to collapse
        # into one shared digest, so any two unlogged-in accounts shared an entry - the very
        # defect the digest exists to close. Serving an unknown account another's remaining
        # quota is worse than spending one call, and `/rate_limit` is quota-EXEMPT, so this
        # costs latency, never budget. Fetch live and store NOTHING.
        sys.stderr.write(
            "gh_read_cache: the credential behind this account-scoped read could not be "
            "resolved (no usable token in the environment, and no `oauth_token` for "
            f"{host or 'github.com'} in the gh config's hosts.yml) — reading live without a "
            "cache rather than pooling unknown accounts\n"
        )
        body, ok = fetch(argv, args.gh, host)
        if not ok:
            sys.stderr.write("gh_read_cache: FAIL-CLOSED — the refresh failed and no value will be emitted\n")
            return 4
        if not body.strip():
            # The caching path already refuses this; the degraded path must agree, or a
            # caller gets rc=0 with nothing on stdout — "success" and no value.
            sys.stderr.write(
                "gh_read_cache: FAIL-CLOSED — gh succeeded but returned an empty body; "
                "an empty answer is not a value\n"
            )
            return 4
        sys.stdout.write(body)
        return 0

    ident = _identity(
        decl.key, repo, args.base, argv, decl.argv, host, account, decl.account_scoped
    )
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
        # ⛔ THE SAME RESOLUTION AS `cmd_get`, DELIBERATELY. `status` still tied the host to
        # `{repo}`, so on a route without one it reported `host: github.com` and `present:
        # false` for an entry the very next `get --repo ghe.example.com/...` served: a hit
        # could not be attributed to a server, which is exactly what this report exists for.
        if uses_repo and not args.repo:
            # Mirror `cmd_get`: a `{repo}` route without a repo is a usage error, not a
            # truthful "nothing cached" for a question that cannot be asked.
            raise Usage("--repo is required for this key")
        host, repo = _resolve_repo(args.repo) if args.repo else (_default_host(), "")
        if not uses_repo:
            repo = ""
    except Refused as exc:
        sys.stderr.write(f"gh_read_cache: REFUSED — {exc}\n")
        return 3
    except Usage as exc:
        sys.stderr.write(f"gh_read_cache: {exc}\n")
        return 2

    argv = _format_argv(decl.argv, repo, args.base)
    account, account_known = _account_fingerprint(host)
    ident = _identity(
        decl.key, repo, args.base, argv, decl.argv, host, account, decl.account_scoped
    )
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
