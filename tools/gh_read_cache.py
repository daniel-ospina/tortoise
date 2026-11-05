#!/usr/bin/env python3
"""gh_read_cache — a TTL cache for DECLARED-INVARIANT `gh` reads ONLY.

WHY THIS EXISTS (#1910 class)
-----------------------------
The account's 5,000-call/hour pool emptied FOUR times in one night. The gated shim (`gh-gate`)
already paces calls (250 ms spacing, 2 in flight), serialises them, honours `retry-after`, shares a
one-budget cooldown across the box, and re-derives a stale window against the exempt `/rate_limit`
read. It is a good gate — and it has **no cache**, so every poll is a live call.

GitHub's own best practice says, under *Avoid polling*:

    "Make authenticated conditional requests, so that unchanged data does not count against your
     primary rate limit." … "Request only the data that you need, and keep responses stable, so
     that more of your polls return 304 Not Modified."
    — docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api

The pool did not empty because the gate refused. It emptied because the FLEET ASKED 5,000 TIMES an
hour for answers that had not changed.

WHAT THIS IS NOT
----------------
* **It is not a general `gh` cache.** A key not in :data:`INVARIANTS` is REFUSED
  (:class:`MutableReadRefused`); there is no pass-through.
* **It is not `gh-gate`.** The gate decides WHEN a call may happen; this decides WHETHER it needs
  to. A hit never reaches the gate at all.
* **It is not a stale-serving cache.** See FAIL-CLOSED.

THE THREE STRUCTURAL GUARDS
---------------------------
Each of these is enforced in code AND pinned by a test, because each one, when it was absent, was a
real false-PASS in a review of the first draft:

1. **SCOPE IS PART OF THE CACHE IDENTITY.** ``read()`` requires a ``scope`` (e.g. ``owner/repo@main``)
   and it is folded into the entry's filename digest *and* re-verified inside the envelope. Without
   this, one repo's branch-protection answer was served verbatim as another repo's — a valid
   envelope, a valid digest, a fresh age, and the wrong subject. A caller cannot forget it: it is a
   required keyword.
2. **NO KEY CACHES A WHOLE RESPONSE BODY.** Every registry entry carries a ``--jq`` PROJECTION
   selecting only invariant fields, and the module refuses to start if one does not (an
   import-time assertion, plus a test). `repos/{repo}/actions/runners` returns ``status`` and
   ``busy`` — second-to-second — beside the invariant ``name``/``labels``; caching the body would
   replay a 30-minute-old "idle" pool. A key-name check can never see that, because the mutability
   is in the PAYLOAD, not the name.
3. **FRESHNESS ARITHMETIC CANNOT BE BYPASSED BY A NON-FINITE CLOCK.** ``now`` and ``stored_at`` must
   both be finite, and the test is a single ``0 <= age < ttl``. The first draft wrote ``age < 0`` and
   ``age >= ttl`` as two separate returns, so a NaN ``now`` made BOTH false and the entry was
   returned as provably fresh.

FAIL-CLOSED — the rule the whole design hangs on
------------------------------------------------
A cached value is served ONLY when its freshness is *provable*, and freshness requires ALL of:

1. the entry parses as an envelope with the expected fields;
2. the envelope's ``key`` and ``scope`` equal the ones asked for (no cross-key, no cross-scope poaching);
3. ``sha256(payload)`` equals the recorded digest (the bytes are the bytes that were stored);
4. ``stored_at`` and ``now`` are finite and ``0 <= now - stored_at < ttl`` — so a future stamp is a
   MISS, not infinite freshness, and ``age == ttl`` is EXPIRED, not "approximately fresh";
5. the entry was stamped under the SAME ttl being asked for.

Anything else is a MISS. And on a MISS the reader is called: **a stale entry is NEVER a fallback for
a failed read.** If the re-read fails, this raises :class:`Unreadable` rather than answering from the
expired entry — because "I could not read" must stay distinguishable from "I read X", and collapsing
those two is exactly how a drained pool produced artifacts that looked like measurements.

USAGE
    import gh_read_cache as C

    body = C.read("repo.required_set", scope="owner/name@main",
                  cache_dir=PATH, now=time.time(), reader=my_reader)

    # CLI — the real `gh`, one key
    python3 tools/gh_read_cache.py --list
    python3 tools/gh_read_cache.py --key repo.required_set --repo owner/name --branch main
    python3 tools/gh_read_cache.py --key repo.labels --repo owner/name --ttl 900   # tighten only
    python3 tools/gh_read_cache.py --purge

Stdlib only. No network beyond the reader the caller supplies.
"""
from __future__ import annotations

import argparse
import base64
import contextlib
import hashlib
import json
import math
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "GLOBAL_KEYS",
    "INVARIANTS",
    "MutableReadRefused",
    "TtlNotPermitted",
    "Unreadable",
    "declare_invariant",
    "kind_of",
    "read",
    "ttl_for",
]


@dataclass(frozen=True)
class Invariant:
    """One cacheable read: how long, and an argv that projects to invariant fields only."""

    ttl_s: int
    argv: tuple[str, ...]


# ═══════════════════════════════════════════════════════════════════════════════════════════════
# THE REGISTRY — an EXPLICIT allow-list.
#
# ⛔ EVERY ENTRY CARRIES A `--jq` PROJECTION. That is not a style choice; it is guard (2) above.
#    The endpoint is usually a MIXED response (invariant fields beside mutable ones), so the only
#    safe unit to cache is the projection. `_assert_every_key_projects()` enforces it at import.
#
# ⛔ PAGINATED READS USE `--slurp`. `gh api --paginate` without `--slurp` emits one JSON document
#    PER PAGE (`[..][..]`), which is not parseable by `json.loads` — so a repo with >30 labels would
#    cache a payload no consumer could read, for a full hour, on exactly the large repos where the
#    quota burn is highest.
# ═══════════════════════════════════════════════════════════════════════════════════════════════
INVARIANTS: dict[str, Invariant] = {
    # `gh api repos/<repo>` — ONLY the identity/branch fields. ⛔ `pushed_at`, `updated_at`,
    # `open_issues_count` and `stargazers_count` are in this body and change every push; a 6-hour
    # TTL over them would let a lane answer "has anything landed since my base?" from six hours ago.
    "repo.metadata": Invariant(
        ttl_s=6 * 3600,
        argv=(
            "api",
            "repos/{repo}",
            "--jq",
            "{id, node_id, full_name, default_branch, visibility, owner: .owner.login}",
        ),
    ),
    # `gh api repos/<repo>/branches/<branch>/protection` — the REQUIRED SET only. Changes when a
    # protection rule is edited: a PR in the repo's settings surface, human-paced.
    "repo.required_set": Invariant(
        ttl_s=30 * 60,
        argv=(
            "api",
            "repos/{repo}/branches/{branch}/protection",
            "--jq",
            "{contexts: (.required_status_checks.contexts // []), "
            "checks: [(.required_status_checks.checks // [])[].context]}",
        ),
    ),
    # `gh api repos/<repo>/labels` — the label NAMES. Human-paced.
    "repo.labels": Invariant(
        ttl_s=60 * 60,
        argv=("api", "repos/{repo}/labels", "--paginate", "--slurp", "--jq", "[.[][] | .name]"),
    ),
    # `gh api repos/<repo>/actions/workflows` — workflow definitions (id/path/state). A new workflow
    # file on the default branch changes it; a merge-paced event.
    "repo.workflows": Invariant(
        ttl_s=60 * 60,
        argv=(
            "api",
            "repos/{repo}/actions/workflows",
            "--paginate",
            "--slurp",
            "--jq",
            "[.[].workflows[] | {id, path, state}]",
        ),
    ),
    # `gh api repos/<repo>/actions/runners` — the runner INVENTORY: name + LABEL NAMES only.
    # ⛔ The body also carries `status` and `busy`, which move second-to-second; the projection is
    #    what makes this cacheable at all. A caller needing liveness must read live — `runner.status`
    #    is mutable vocabulary below and cannot be registered.
    "runners.inventory": Invariant(
        ttl_s=30 * 60,
        argv=(
            "api",
            "repos/{repo}/actions/runners",
            "--paginate",
            "--slurp",
            "--jq",
            "[.[].runners[] | {name, labels: [.labels[].name]}]",
        ),
    ),
    # `gh api user` — the authenticated identity. Changes on token rotation.
    "user.identity": Invariant(
        ttl_s=12 * 3600,
        argv=("api", "user", "--jq", "{login, id}"),
    ),
}

# Keys whose answer does not depend on a repo/branch. Everything else must be scoped.
GLOBAL_KEYS: frozenset[str] = frozenset({"user.identity"})

# ⛔ THE MUTABLE VOCABULARY. Token-matched after a camelCase split, NEVER substring-matched.
#    A substring test on `"run"` matches `"runners"`; on `"check"` matches `"checked"`. A guard that
#    does both would refuse the reads it exists to allow AND admit a novel spelling — failing in
#    both directions, which is why this is spelled the way it is (and why the first green run of the
#    suite was 23 passed / 2 failed).
_MUTABLE_TOKENS: frozenset[str] = frozenset(
    {
        "state",
        "status",
        "busy",
        "conclusion",
        "conclusions",
        "check",
        "checks",
        "mergeable",
        "mergeablestatus",
        "merged",
        "merge",
        "draft",
        "decision",
        "reviewdecision",
        "run",
        "runs",
        "job",
        "jobs",
        "commit",
        "commits",
        "sha",
        "diff",
        "body",
        "comment",
        "comments",
        "review",
        "reviews",
        "queue",
        "position",
        "updated",
        "created",
        "pushed",
        "modified",
        "count",
        "counts",
        "open",
        "prs",
    }
)

# Glued phrases that carry mutable meaning ACROSS a token boundary.
_MUTABLE_PHRASES: tuple[str, ...] = (
    "ratelimit",
    "mergestatestatus",
    "statuscheckrollup",
)

_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_TOKEN_SPLIT = re.compile(r"[^A-Za-z0-9]+")


class MutableReadRefused(Exception):
    """The key is mutable, or undeclared — which is not the same as invariant.

    Raised BEFORE any reader call, so a refusal never becomes a labelled pass-through.
    """


class TtlNotPermitted(Exception):
    """A caller asked to hold an entry longer than the key's declared ceiling, or to widen it."""


class Unreadable(Exception):
    """The re-read failed and no provably-fresh value exists. Never answered from a stale entry."""


# ── the registry readers ───────────────────────────────────────────────────────────────────────
def _tokens(key: str) -> list[str]:
    """camelCase-aware tokens: `pr.reviewDecision` → ['pr', 'review', 'decision']."""
    spaced = _CAMEL.sub("-", key)
    return [t.lower() for t in _TOKEN_SPLIT.split(spaced) if t]


def _is_mutable_name(key: str) -> bool:
    """Token-exact match, plus a small set of glued phrases. Never a bare substring test."""
    tokens = _tokens(key)
    if any(t in _MUTABLE_TOKENS for t in tokens):
        return True
    glued = "".join(tokens)
    return any(p in glued for p in _MUTABLE_PHRASES)


def _mutable_words_in(key: str) -> list[str]:
    """The words that made a name mutable — for the refusal message, never for the decision."""
    hits = [t for t in _tokens(key) if t in _MUTABLE_TOKENS]
    glued = "".join(_tokens(key))
    hits += [p for p in _MUTABLE_PHRASES if p in glued]
    return sorted(set(hits))


def kind_of(key: str) -> str:
    """``"invariant"`` | ``"mutable"`` — and an UNDECLARED key raises rather than being guessed.

    Undeclared is deliberately NOT a third return value: a caller has no business deciding what to
    do about an unproven key, and returning a string invites one to default it to invariant.
    """
    if key in INVARIANTS:
        if _is_mutable_name(key):
            return "mutable"  # defence in depth — a registry edit cannot smuggle a mutable read in
        return "invariant"
    if _is_mutable_name(key):
        return "mutable"
    raise MutableReadRefused(
        f"{key!r} is not a declared invariant. An undeclared key is not provably invariant, so it "
        f"is refused rather than cached. Declare it with declare_invariant() if it truly cannot "
        f"change, or read it live."
    )


def ttl_for(key: str) -> int:
    """The declared ceiling, in seconds, for an invariant key."""
    if kind_of(key) != "invariant":
        raise MutableReadRefused(f"{key!r} is a mutable read and has no TTL")
    return INVARIANTS[key].ttl_s


def declare_invariant(key: str, *, ttl_s: int, argv: tuple[str, ...]) -> None:
    """Add a key to the registry.

    Refuses: a mutable name, a non-positive TTL, an argv with no ``--jq`` projection, and any
    attempt to WIDEN an existing key's ceiling. That last one matters because this function is the
    documented way to add a key — so without the check, the ceiling that ``read()`` defends could be
    raised by a one-line call, making the whole constraint advisory against its own owner.
    """
    if _is_mutable_name(key):
        raise MutableReadRefused(
            f"{key!r} carries mutable-state vocabulary "
            f"({', '.join(_mutable_words_in(key))}). A mutable read cached at any TTL reports a past "
            f"state as the current one — that is a false PASS, not a saving."
        )
    if not isinstance(ttl_s, int) or isinstance(ttl_s, bool) or ttl_s <= 0:
        raise TtlNotPermitted(f"ttl_s must be a positive int, got {ttl_s!r}")
    if "--jq" not in argv:
        raise TtlNotPermitted(
            f"{key!r} declares no `--jq` projection. Caching a WHOLE response body replays whatever "
            f"mutable fields sit beside the invariant ones (guard 2) — project the invariant fields."
        )
    existing = INVARIANTS.get(key)
    if existing is not None and ttl_s > existing.ttl_s:
        raise TtlNotPermitted(
            f"{key!r} already declares a {existing.ttl_s}s ceiling; refusing to widen it to "
            f"{ttl_s}s. Tightening is allowed; widening is how a TTL quietly becomes a stale read."
        )
    INVARIANTS[key] = Invariant(ttl_s=ttl_s, argv=tuple(argv))


def _assert_every_key_projects() -> None:
    """Import-time guard (2): no key may cache a whole body."""
    for key, spec in INVARIANTS.items():
        if "--jq" not in spec.argv:
            raise TtlNotPermitted(
                f"registry invariant {key!r} has no `--jq` projection, so it would cache a whole "
                f"response body — including any mutable fields beside the invariant ones."
            )


_assert_every_key_projects()


# ── the on-disk format ────────────────────────────────────────────────────────────────────────
_CACHE_PREFIX = "gh-read-"


def _slug(text: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in text)


def _filename(key: str, scope: str) -> str:
    """Deterministic, collision-resistant, readable — and SCOPED (guard 1)."""
    digest = hashlib.sha1(f"{scope}\x00{key}".encode()).hexdigest()[:10]
    return f"{_CACHE_PREFIX}{_slug(key)}-{_slug(scope)[:24]}-{digest}.json"


def _cache_path(key: str, scope: str) -> Path:
    """The entry's NAME (not joined to a directory — the caller supplies cache_dir)."""
    return Path(_filename(key, scope))


def _envelope(key: str, scope: str, payload: bytes, *, now: float, ttl_s: int) -> bytes:
    """The stored bytes: digest over the PAYLOAD ONLY, freshness in the metadata.

    The split is deliberate. The digest answers *"are these the bytes that were stored?"*;
    ``stored_at`` + ``ttl_s`` answer *"are they still fresh?"*. A digest over the metadata too would
    make a rewritten ``stored_at`` look intact, which is the failure :func:`_load` exists to catch.
    """
    return json.dumps(
        {
            "key": key,
            "scope": scope,
            "stored_at": float(now),
            "ttl_s": int(ttl_s),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "payload": base64.b64encode(payload).decode("ascii"),
        },
        sort_keys=True,
    ).encode("utf-8")


def _load(path: Path, key: str, scope: str, *, now: float, ttl_s: int) -> bytes | None:
    """Return the payload IFF freshness is provable. Any doubt → ``None`` (a MISS)."""
    # ⛔ GUARD 3. A non-finite clock makes every comparison below meaningless, and the first draft
    # let a NaN `now` satisfy `age < ttl` AND `age >= 0` by making both false. Refuse up front.
    if not math.isfinite(now):
        return None
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    try:
        env = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return None  # a killed writer's half entry, or corruption. It proves nothing.
    if not isinstance(env, dict):
        return None
    if env.get("key") != key or env.get("scope") != scope:
        return None  # guard 1: no cross-key, no cross-scope poaching
    try:
        payload = base64.b64decode(env["payload"], validate=True)
        digest = env["sha256"]
        stored_at = float(env["stored_at"])
        stored_ttl = int(env["ttl_s"])
    except (KeyError, TypeError, ValueError):
        return None
    if not isinstance(digest, str) or hashlib.sha256(payload).hexdigest() != digest:
        return None
    if stored_ttl != int(ttl_s):
        return None  # a longer-stamped entry must not satisfy a tighter ask
    if not math.isfinite(stored_at):
        return None
    age = now - stored_at
    # One comparison, not two: `0 <= age < ttl`. Two separate returns is how NaN slipped through.
    if not (0 <= age < ttl_s):
        return None
    return payload


def _store(path: Path, blob: bytes) -> None:
    """Atomic: a killed writer leaves the old entry or nothing, never a half one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    try:
        tmp.write_bytes(blob)
        os.replace(tmp, path)
    finally:
        with contextlib.suppress(OSError):
            tmp.unlink()


# ── the read ─────────────────────────────────────────────────────────────────────────────────
def read(
    key: str,
    *,
    scope: str,
    cache_dir: str | os.PathLike[str],
    now: float,
    reader,
    ttl_s: int | None = None,
) -> bytes:
    """Serve a provably-fresh cached value, else call ``reader(key)`` once and store it.

    ``scope`` is REQUIRED and is part of the cache identity (guard 1): the same key asked for a
    different repo or branch is a different entry, so one repo's answer can never be served as
    another's.

    ``reader`` is injected on purpose: correctness is testable without a network, and the CLI is
    the only place that knows about `gh`.

    Raises:
        MutableReadRefused: the key is mutable or undeclared — before the reader is touched.
        TtlNotPermitted: ``ttl_s`` exceeds the key's declared ceiling, or no ``scope`` was given.
        Unreadable: propagated from ``reader`` on a MISS. A stale entry is NEVER served instead.
    """
    ceiling = ttl_for(key)  # raises MutableReadRefused for a mutable/undeclared key
    if not isinstance(scope, str) or not scope.strip():
        raise TtlNotPermitted(
            f"{key!r} requires a non-empty scope (e.g. 'owner/repo@main'); without one, one repo's "
            f"answer is served as another's."
        )
    effective = ceiling if ttl_s is None else int(ttl_s)
    if effective <= 0:
        raise TtlNotPermitted(f"ttl_s must be positive, got {ttl_s!r}")
    if effective > ceiling:
        raise TtlNotPermitted(
            f"{key!r} declares a {ceiling}s ceiling; {effective}s was asked for. Widening a TTL to "
            f"make something pass is how a cache starts reporting a past state as the current one "
            f"(#1910)."
        )

    path = Path(cache_dir) / _cache_path(key, scope).name
    hit = _load(path, key, scope, now=now, ttl_s=effective)
    if hit is not None:
        return hit

    # MISS. ⛔ THE STALE ENTRY IS NOT CONSULTED AGAIN. If the reader fails we raise; serving the
    # expired payload here would answer a question that was never asked of GitHub.
    try:
        payload = reader(key)
    except Unreadable:
        raise
    except Exception as exc:  # every reader failure is "could not read"
        raise Unreadable(f"re-read of {key!r} failed: {exc}") from exc
    if not isinstance(payload, (bytes, bytearray)):
        raise Unreadable(f"reader for {key!r} returned {type(payload).__name__}, not bytes")
    payload = bytes(payload)
    # ⛔ A CACHE-WRITE FAILURE MUST NOT FAIL A SUCCESSFUL READ. The value in hand was just read
    # from GitHub and is as fresh as anything can be; failing to STORE it is a lost optimisation,
    # not a lost answer. Fail-closed governs what may be SERVED from disk, never what may be
    # RETURNED after a live read — conflating them would turn a full disk into a total gh outage.
    with contextlib.suppress(OSError):
        _store(path, _envelope(key, scope, payload, now=now, ttl_s=effective))
    return payload


# ═══════════════════════════════════════════════════════════════════════════════════════════════
# CLI — the only part that knows `gh` exists.
# ═══════════════════════════════════════════════════════════════════════════════════════════════
def default_cache_dir() -> Path:
    base = os.environ.get("GH_READ_CACHE_DIR")
    if base:
        return Path(base)
    return Path(os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")) / "gh-read-cache"


def scope_for(key: str, repo: str, branch: str) -> str:
    """Global keys are not fragmented per branch; everything else is scoped by repo AND branch."""
    if key in GLOBAL_KEYS:
        return "global"
    return f"{repo}@{branch}"


def _gh_reader(repo: str, branch: str):
    def _read(key: str) -> bytes:
        spec = INVARIANTS.get(key)
        if spec is None:
            raise Unreadable(f"no argv is declared for {key!r}")
        cmd = ["gh", *[a.replace("{repo}", repo).replace("{branch}", branch) for a in spec.argv]]
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=120, check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            raise Unreadable(f"{' '.join(cmd)} could not run: {exc}") from exc
        if proc.returncode != 0:
            raise Unreadable(
                f"{' '.join(cmd)} exited {proc.returncode}: "
                f"{(proc.stderr or b'').decode('utf-8', 'replace').strip()[:300]}"
            )
        return proc.stdout

    return _read


def main(argv: list[str] | None = None) -> int:
    import time

    ap = argparse.ArgumentParser(description="TTL cache for declared-invariant `gh` reads.")
    ap.add_argument("--key", help="an invariant key (see --list)")
    ap.add_argument("--ttl", type=int, default=None, help="tighten the TTL (never widen)")
    ap.add_argument("--repo", default=os.environ.get("GH_REPO", ""), help="owner/name for {repo}")
    ap.add_argument("--branch", default=os.environ.get("GH_BRANCH", "main"))
    ap.add_argument("--cache-dir", default=None)
    ap.add_argument("--list", action="store_true", help="print the registry and exit")
    ap.add_argument("--purge", action="store_true", help="remove every entry and exit")
    a = ap.parse_args(argv)

    cache_dir = Path(a.cache_dir) if a.cache_dir else default_cache_dir()

    if a.list:
        for key in sorted(INVARIANTS):
            print(f"{key}\t{INVARIANTS[key].ttl_s}s")
        return 0

    if a.purge:
        n = 0
        if cache_dir.is_dir():
            for p in cache_dir.glob(_CACHE_PREFIX + "*"):
                try:
                    p.unlink()
                    n += 1
                except OSError:
                    pass
        print(f"purged {n} entr{'y' if n == 1 else 'ies'} from {cache_dir}", file=sys.stderr)
        return 0

    if not a.key:
        ap.error("--key is required (or --list / --purge)")
    try:
        body = read(
            a.key,
            scope=scope_for(a.key, a.repo, a.branch),
            cache_dir=cache_dir,
            now=time.time(),
            reader=_gh_reader(a.repo, a.branch),
            ttl_s=a.ttl,
        )
    except (MutableReadRefused, TtlNotPermitted, Unreadable) as exc:
        print(f"gh_read_cache: REFUSED — {exc}", file=sys.stderr)
        return 1
    sys.stdout.buffer.write(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
