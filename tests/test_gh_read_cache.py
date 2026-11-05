"""Tests for tools/gh_read_cache.py — the invariant-read cache for `gh` (#1910 class).

WHY THESE TESTS EXIST, AND WHAT THEY PIN
----------------------------------------
The 5,000-call/hour pool emptied FOUR times in one night. The gated shim (`gh-gate`) paces,
serialises, honours `retry-after` and shares a cooldown — and it has NO CACHE, so every poll is a
live call. GitHub's own best practice is explicit: *"Make authenticated conditional requests, so
that unchanged data does not count against your primary rate limit"* and *"keep responses stable, so
that more of your polls return 304 Not Modified"*
(docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api).

So a cache is the fix — but a cache that can serve a STALE (or WRONG-SUBJECT) answer as if it were
fresh is worse than no cache at all: it turns "I could not read" into "I read X", which is the
false-PASS class this repo treats as a defect. These tests are therefore about the FAILURE
directions, not the happy path.

⛔ EVERY TEST IN SECTION 8 IS HERE BECAUSE A REVIEW OF THE FIRST DRAFT FOUND THE BUG. They are the
regression fence for that review, and each one fails against the draft's code:

  8a scope        — no scope ⇒ repo A's branch-protection answer served as repo B's   (was P0)
  8b projection   — runners.inventory cached `status`/`busy` beside name/labels         (was P1)
  8c camelCase    — `pr.mergeable` was ACCEPTED by declare_invariant                    (was P1)
  8d whole body   — repo.metadata cached `pushed_at`/`open_issues_count` for 6h          (was P2)
  8e NaN clock    — a NaN `now` made `age < 0` AND `age >= ttl` both false ⇒ served      (was P2)
  8f widening     — declare_invariant could RAISE an existing ceiling                    (was P2)
  8g --slurp      — `--paginate` alone emits one document per page, unparseable          (was P2)
  8h atomicity    — no test failed if `_store` became non-atomic                         (was P2)

Hermetic: no network, no `gh`, no DB. The reader and the clock are injected.
"""
from __future__ import annotations

import base64
import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools import gh_read_cache as C  # noqa: E402

SCOPE = "daniel-ospina/tortoise@main"


# ── helpers ────────────────────────────────────────────────────────────────────────────────────
class Counter:
    """A reader that answers with a monotonic payload and counts its calls."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.fail: Exception | None = None

    def __call__(self, key: str) -> bytes:
        self.calls.append(key)
        if self.fail is not None:
            raise self.fail
        return b"payload-%d" % len(self.calls)

    @property
    def n(self) -> int:
        return len(self.calls)


def _read(key: str, *, now: float, reader, ttl_s: int | None = None, cache_dir: Path,
          scope: str = SCOPE):
    return C.read(key, scope=scope, cache_dir=cache_dir, now=now, reader=reader, ttl_s=ttl_s)


def _entry(cache_dir: Path, key: str = "repo.metadata", scope: str = SCOPE) -> Path:
    return cache_dir / C._cache_path(key, scope).name


# ── 1. a cache HIT does not touch the reader ───────────────────────────────────────────────────
def test_cached_hit_does_not_call_reader(tmp_path: Path) -> None:
    r = Counter()
    first = _read("repo.metadata", now=1000.0, reader=r, cache_dir=tmp_path)
    second = _read("repo.metadata", now=1000.0 + 1.0, reader=r, cache_dir=tmp_path)

    assert first == b"payload-1"
    assert second == first, "a fresh cached value must be served verbatim"
    assert r.n == 1, (
        "the pool emptied because every poll was a live call — a hit inside the TTL MUST NOT "
        "call the reader, or the cache is decoration"
    )


# ── 2. TTL expiry re-reads (no silent widening) ────────────────────────────────────────────────
def test_ttl_expiry_calls_reader_again(tmp_path: Path) -> None:
    r = Counter()
    _read("repo.metadata", now=1000.0, reader=r, cache_dir=tmp_path)
    ttl = C.ttl_for("repo.metadata")
    out = _read("repo.metadata", now=1000.0 + ttl + 1.0, reader=r, cache_dir=tmp_path)

    assert r.n == 2, "a value at or past its TTL is not fresh and must be re-read"
    assert out == b"payload-2"


def test_exact_ttl_boundary_is_a_miss(tmp_path: Path) -> None:
    """The boundary is exclusive on fresh: age == ttl is EXPIRED.

    A one-sided comparison (`<=`) is how a TTL silently becomes ttl+one-poll, and each poll is a
    real call at 15 lanes.
    """
    r = Counter()
    _read("repo.metadata", now=1000.0, reader=r, cache_dir=tmp_path)
    ttl = C.ttl_for("repo.metadata")
    _read("repo.metadata", now=1000.0 + ttl, reader=r, cache_dir=tmp_path)
    assert r.n == 2


# ── 3. a MUTABLE key is refused, never silently passed through ─────────────────────────────────
@pytest.mark.parametrize("key", ["pr.state", "pr.mergeStateStatus", "pr.checks", "run.status",
                                 "runner.status", "rate_limit.buckets", "issue.state",
                                 "pr.mergeable", "pr.reviewDecision"])
def test_mutable_key_is_refused(key: str, tmp_path: Path) -> None:
    r = Counter()
    with pytest.raises(C.MutableReadRefused):
        _read(key, now=1000.0, reader=r, cache_dir=tmp_path)
    assert r.n == 0, (
        "a refused key must not reach the network — a refusal that still issues the call is a "
        "labelled pass-through, not a guard"
    )


def test_mutable_key_is_refused_on_cached_lookup_too(tmp_path: Path) -> None:
    """Even with a populated entry under that key, the refusal comes FIRST.

    Otherwise a poached cache file would serve mutable state as invariant.
    """
    blob = C._envelope("pr.state", SCOPE, b"stale-merge-state", now=1000.0, ttl_s=600)
    _entry(tmp_path, "pr.state").write_bytes(blob)
    r = Counter()
    with pytest.raises(C.MutableReadRefused):
        _read("pr.state", now=1000.0, reader=r, cache_dir=tmp_path)
    assert r.n == 0


# ── 4. fail closed when freshness cannot be PROVEN ────────────────────────────────────────────
def test_corrupt_envelope_is_a_miss(tmp_path: Path) -> None:
    r = Counter()
    _read("repo.metadata", now=1000.0, reader=r, cache_dir=tmp_path)
    _entry(tmp_path).write_bytes(b"{not json")
    out = _read("repo.metadata", now=1000.0, reader=r, cache_dir=tmp_path)
    assert r.n == 2, "an unparseable entry proves nothing; it must re-read rather than raise or serve"
    assert out == b"payload-2"


def test_tampered_payload_is_a_miss(tmp_path: Path) -> None:
    """The digest is what makes "fresh" a claim about the BYTES, not the file's mtime."""
    r = Counter()
    _read("repo.metadata", now=1000.0, reader=r, cache_dir=tmp_path)
    p = _entry(tmp_path)
    env = json.loads(p.read_text())
    env["payload"] = base64.b64encode(b"payload-99").decode()
    p.write_text(json.dumps(env))
    out = _read("repo.metadata", now=1000.0, reader=r, cache_dir=tmp_path)
    assert r.n == 2
    assert out == b"payload-2"


def test_clock_ahead_of_stored_at_is_a_miss(tmp_path: Path) -> None:
    """A future `stored_at` would make the age negative and satisfy `age < ttl` forever."""
    r = Counter()
    _read("repo.metadata", now=1000.0, reader=r, cache_dir=tmp_path)
    p = _entry(tmp_path)
    env = json.loads(p.read_text())
    env["stored_at"] = 9999.0
    p.write_text(json.dumps(env))
    _read("repo.metadata", now=1000.0, reader=r, cache_dir=tmp_path)
    assert r.n == 2, "a future stamp is unprovable freshness, not infinite freshness"


def test_stale_entry_is_never_a_fallback_for_a_failed_read(tmp_path: Path) -> None:
    """⛔ THE LOAD-BEARING TEST. Fail closed, never stale-as-fresh.

    A reader that fails on a MISS must RAISE. Serving the expired entry would report a
    measurement that was never taken — the exact false-PASS the pool running dry produces.
    """
    r = Counter()
    _read("repo.metadata", now=1000.0, reader=r, cache_dir=tmp_path)
    ttl = C.ttl_for("repo.metadata")
    r.fail = C.Unreadable("gh api failed: HTTP 403")
    with pytest.raises(C.Unreadable):
        _read("repo.metadata", now=1000.0 + ttl + 1.0, reader=r, cache_dir=tmp_path)


def test_missing_cache_dir_is_a_miss_not_a_crash(tmp_path: Path) -> None:
    r = Counter()
    out = _read("repo.metadata", now=1000.0, reader=r, cache_dir=tmp_path / "nope" / "deeper")
    assert out == b"payload-1"
    assert r.n == 1


# ── 5. a TTL may not be widened ───────────────────────────────────────────────────────────────
def test_ttl_may_not_exceed_the_key_ceiling(tmp_path: Path) -> None:
    r = Counter()
    ceiling = C.ttl_for("repo.metadata")
    with pytest.raises(C.TtlNotPermitted):
        _read("repo.metadata", now=1000.0, reader=r, ttl_s=ceiling + 1, cache_dir=tmp_path)
    assert r.n == 0


def test_requested_ttl_mismatch_invalidates_the_entry(tmp_path: Path) -> None:
    """An entry stamped under a LONGER ttl must not satisfy a SHORTER ask.

    Without this, one caller's generous TTL silently becomes every caller's.
    """
    r = Counter()
    ceiling = C.ttl_for("repo.metadata")
    _read("repo.metadata", now=1000.0, reader=r, ttl_s=ceiling, cache_dir=tmp_path)
    _read("repo.metadata", now=1000.0 + 1.0, reader=r, ttl_s=max(60, ceiling // 2), cache_dir=tmp_path)
    assert r.n == 2


def test_shorter_ttl_is_allowed(tmp_path: Path) -> None:
    r = Counter()
    _read("repo.metadata", now=1000.0, reader=r, ttl_s=60, cache_dir=tmp_path)
    _read("repo.metadata", now=1000.0 + 1.0, reader=r, ttl_s=60, cache_dir=tmp_path)
    assert r.n == 1


# ── 6. the invariant registry is an explicit allow-list ───────────────────────────────────────
def test_registry_is_an_allowlist_of_declared_invariants() -> None:
    assert C.INVARIANTS, "an empty registry must not silently accept everything"
    for key in C.INVARIANTS:
        assert C.kind_of(key) == "invariant"


def test_undeclared_key_is_refused(tmp_path: Path) -> None:
    r = Counter()
    with pytest.raises(C.MutableReadRefused):
        _read("something.nobody.declared", now=1000.0, reader=r, cache_dir=tmp_path)
    assert r.n == 0, "an UNDECLARED key is not provably invariant — refuse, do not assume"


def test_registry_cannot_admit_a_mutable_name() -> None:
    """The registry is the gate; a mutable name must not be addable to it."""
    with pytest.raises(C.MutableReadRefused):
        C.declare_invariant("pr.mergeStateStatus", ttl_s=600, argv=("api", "x", "--jq", "."))


def test_runner_inventory_is_cached_but_runner_status_is_not() -> None:
    """The runner pool read is the measured burn; only its INVARIANT half is cacheable."""
    assert C.kind_of("runners.inventory") == "invariant"
    assert C.kind_of("runner.status") == "mutable"


# ── 7. atomic write ───────────────────────────────────────────────────────────────────────────
def test_partial_entry_from_a_killed_writer_is_a_miss(tmp_path: Path) -> None:
    r = Counter()
    _read("repo.metadata", now=1000.0, reader=r, cache_dir=tmp_path)
    p = _entry(tmp_path)
    p.write_bytes(p.read_bytes()[:10])
    _read("repo.metadata", now=1000.0, reader=r, cache_dir=tmp_path)
    assert r.n == 2


def test_envelope_digest_is_over_the_payload_only(tmp_path: Path) -> None:
    """Documenting the digest's scope: it proves the BYTES, not the metadata around them.

    Metadata freshness is decided by `stored_at` + `ttl_s` (tested above); the digest decides
    "are these the bytes that were stored". Conflating them is how a rewritten `stored_at`
    would look intact.
    """
    env = json.loads(C._envelope("repo.metadata", SCOPE, b"abc", now=1.0, ttl_s=2).decode())
    assert env["sha256"] == hashlib.sha256(b"abc").hexdigest()


# ══════════════════════════════════════════════════════════════════════════════════════════════
# 8. REGRESSION FENCE FOR THE FIRST-DRAFT REVIEW. Each test fails against the draft.
# ══════════════════════════════════════════════════════════════════════════════════════════════

# ── 8a (was P0): the cache had no repo/branch identity ────────────────────────────────────────
def test_a_different_scope_is_a_different_entry(tmp_path: Path) -> None:
    """⛔ THE P0. One repo's branch-protection answer must never be served as another's.

    Against the draft, `read()` had no scope at all, so the second call returned repo A's payload
    with reader B never invoked — a valid envelope, a valid digest, a fresh age, and the wrong
    SUBJECT. Nothing in the freshness checks could see it.
    """
    calls: list[tuple[str, bytes]] = []

    def reader_a(key: str) -> bytes:
        calls.append(("A", key))
        return b'{"checks":["tortoise-only"]}'

    def reader_b(key: str) -> bytes:
        calls.append(("B", key))
        return b'{"checks":["agent-infra-only"]}'

    a = _read("repo.required_set", now=1000.0, reader=reader_a, cache_dir=tmp_path,
              scope="daniel-ospina/tortoise@main")
    b = _read("repo.required_set", now=1000.0, reader=reader_b, cache_dir=tmp_path,
              scope="daniel-ospina/agent-infra@main")

    assert a != b, "a cross-scope hit served repo A's required set as repo B's"
    assert b == b'{"checks":["agent-infra-only"]}'
    assert [c[0] for c in calls] == ["A", "B"], "reader B must actually be called"


def test_a_different_branch_is_a_different_entry(tmp_path: Path) -> None:
    """`repo.required_set` interpolates {branch}; two branches are two answers."""
    r = Counter()
    _read("repo.required_set", now=1000.0, reader=r, cache_dir=tmp_path, scope="o/r@main")
    _read("repo.required_set", now=1000.0, reader=r, cache_dir=tmp_path, scope="o/r@release")
    assert r.n == 2


def test_scope_is_required(tmp_path: Path) -> None:
    """A caller cannot FORGET the scope — it is a required keyword, not a defaulted one."""
    r = Counter()
    with pytest.raises(TypeError):
        C.read("repo.metadata", cache_dir=tmp_path, now=1000.0, reader=r)  # type: ignore[call-arg]
    assert r.n == 0


@pytest.mark.parametrize("bad", ["", "   "])
def test_empty_scope_is_refused(bad: str, tmp_path: Path) -> None:
    r = Counter()
    with pytest.raises(C.TtlNotPermitted):
        _read("repo.metadata", now=1000.0, reader=r, cache_dir=tmp_path, scope=bad)
    assert r.n == 0


def test_envelope_scope_must_match(tmp_path: Path) -> None:
    """Defence in depth: even a file placed at the right NAME is rejected if its scope disagrees."""
    r = Counter()
    _read("repo.metadata", now=1000.0, reader=r, cache_dir=tmp_path)
    p = _entry(tmp_path)
    env = json.loads(p.read_text())
    env["scope"] = "someone/else@main"
    p.write_text(json.dumps(env))
    _read("repo.metadata", now=1000.0, reader=r, cache_dir=tmp_path)
    assert r.n == 2


# ── 8b/8d (was P1/P2): no key may cache a WHOLE response body ─────────────────────────────────
def test_every_registry_key_declares_a_jq_projection() -> None:
    """⛔ Guard 2. `runners` returns status/busy beside name/labels; `repos/<r>` returns
    pushed_at/open_issues_count. Caching the whole body replays the mutable fields — and no key-NAME
    check can see that, because the mutability is in the PAYLOAD."""
    for key, spec in C.INVARIANTS.items():
        assert "--jq" in spec.argv, (
            f"{key!r} caches a whole response body; it must project the invariant fields"
        )


def test_runner_inventory_projects_away_status_and_busy() -> None:
    """The specific P1: the projection must select name+labels and NOT status/busy."""
    jq = C.INVARIANTS["runners.inventory"].argv[C.INVARIANTS["runners.inventory"].argv.index("--jq") + 1]
    assert "name" in jq and "labels" in jq
    assert "status" not in jq, "caching `status` replays a 30-minute-old 'online' as current"
    assert "busy" not in jq, "caching `busy` replays a 30-minute-old 'idle' and dispatches onto a busy runner"


def test_repo_metadata_projects_away_volatile_counts() -> None:
    """The specific P2: a 6h TTL must not sit over pushed_at / open_issues_count."""
    argv = C.INVARIANTS["repo.metadata"].argv
    jq = argv[argv.index("--jq") + 1]
    for volatile in ("pushed_at", "updated_at", "open_issues_count", "stargazers_count"):
        assert volatile not in jq, f"{volatile} changes every push; a 6h TTL over it is a stale read"


def test_declare_invariant_requires_a_projection() -> None:
    r = Counter()
    with pytest.raises(C.TtlNotPermitted):
        C.declare_invariant("repo.note", ttl_s=600, argv=("api", "repos/{repo}"))
    assert r.n == 0


# ── 8c (was P1): camelCase-aware mutable vocabulary ───────────────────────────────────────────
@pytest.mark.parametrize("key", ["pr.mergeable", "pr.reviewDecision", "pr.draft", "branch.head_sha",
                                 "repo.open_prs", "pr.merged_at"])
def test_camel_case_mutable_names_are_refused(key: str) -> None:
    """The draft's tokenizer only split on non-alphanumerics, so `mergeable` (not
    `mergeStateStatus`) slipped through — the most obvious spelling of the name the guard names."""
    with pytest.raises(C.MutableReadRefused):
        C.declare_invariant(key, ttl_s=600, argv=("api", "x", "--jq", "."))
    assert C.kind_of(key) == "mutable"


def test_tokenizer_splits_camel_case() -> None:
    assert C._tokens("pr.reviewDecision") == ["pr", "review", "decision"]
    assert C._tokens("mergeStateStatus") == ["merge", "state", "status"]


def test_tokenizer_does_not_over_match() -> None:
    """The false-POSITIVE side: `run` must not match `runners`."""
    assert C._tokens("runners.inventory") == ["runners", "inventory"]
    assert not C._is_mutable_name("runners.inventory")


# ── 8e (was P2): a non-finite clock must not prove freshness ──────────────────────────────────
@pytest.mark.parametrize("bad_now", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_now_never_serves_a_hit(bad_now: float, tmp_path: Path) -> None:
    """⛔ The draft wrote `age < 0` and `age >= ttl` as two returns, so a NaN `now` made BOTH
    false and an arbitrarily old entry was returned as provably fresh."""
    r = Counter()
    _read("repo.metadata", now=1000.0, reader=r, cache_dir=tmp_path)
    _read("repo.metadata", now=bad_now, reader=r, cache_dir=tmp_path)
    assert r.n == 2, "a non-finite clock cannot prove freshness"


def test_non_finite_stored_at_is_a_miss(tmp_path: Path) -> None:
    r = Counter()
    _read("repo.metadata", now=1000.0, reader=r, cache_dir=tmp_path)
    p = _entry(tmp_path)
    p.write_text(p.read_text().replace('"stored_at": 1000.0', '"stored_at": Infinity'))
    _read("repo.metadata", now=1000.0, reader=r, cache_dir=tmp_path)
    assert r.n == 2


# ── 8f (was P2): declare_invariant must not WIDEN an existing ceiling ────────────────────────
def test_declare_invariant_cannot_widen_an_existing_ceiling() -> None:
    """That function is the documented way to add a key — so without this check the ceiling that
    `read()` defends could be raised by a one-line call, making the constraint advisory."""
    before = C.ttl_for("repo.metadata")
    with pytest.raises(C.TtlNotPermitted):
        C.declare_invariant("repo.metadata", ttl_s=before * 4, argv=("api", "x", "--jq", "."))
    assert C.ttl_for("repo.metadata") == before, "the ceiling was widened by a rejected call"


def test_declare_invariant_may_tighten() -> None:
    key = "repo.tightenable"
    C.declare_invariant(key, ttl_s=600, argv=("api", "x", "--jq", "."))
    C.declare_invariant(key, ttl_s=60, argv=("api", "x", "--jq", "."))
    assert C.ttl_for(key) == 60
    C.INVARIANTS.pop(key, None)


# ── 8g (was P2): paginated reads must emit ONE JSON document ─────────────────────────────────
@pytest.mark.parametrize("key", ["repo.labels", "repo.workflows", "runners.inventory"])
def test_paginated_keys_use_slurp(key: str) -> None:
    """`gh api --paginate` without `--slurp` emits one document PER PAGE (`[..][..]`), which
    `json.loads` cannot parse — cached, unreadable, for an hour, on the large repos."""
    argv = C.INVARIANTS[key].argv
    assert "--paginate" in argv
    assert "--slurp" in argv, f"{key!r} paginates without --slurp, so its payload is not one document"


# ── 8h (was P2): the atomicity claim must be PINNED, not asserted ────────────────────────────
def test_store_is_atomic_a_failed_replace_leaves_the_old_entry_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """⛔ Discriminating, unlike a truncate-the-final-file test.
    A regression to `path.write_bytes(blob)` (no temp file, no os.replace) would still pass every
    other test in this file. This one fails if the write is not staged.
    """
    r = Counter()
    _read("repo.metadata", now=1000.0, reader=r, cache_dir=tmp_path)
    good = _entry(tmp_path).read_bytes()

    # Make the atomic swap fail: a staged writer leaves the previous entry untouched.
    def boom(src, dst):
        raise OSError("simulated crash during replace")

    monkeypatch.setattr(C.os, "replace", boom)
    # Force a MISS (past TTL) so _store runs; the reader succeeds, so the CALLER must still get
    # its fresh answer — a failed cache WRITE is a lost optimisation, never a failed read.
    def reader_then_crash(key: str) -> bytes:
        return b"payload-NEW"

    got = _read("repo.metadata", now=1000.0 + C.ttl_for("repo.metadata") + 1,
                reader=reader_then_crash, cache_dir=tmp_path)

    assert got == b"payload-NEW", "a failed cache WRITE must not fail a read that just succeeded"
    assert _entry(tmp_path).read_bytes() == good, (
        "a crashed swap must not have touched the existing entry — the write was not staged"
    )
    assert not [p for p in tmp_path.iterdir() if ".tmp." in p.name], "staged temp file leaked"


def test_store_leaves_no_temp_file_behind(tmp_path: Path) -> None:
    r = Counter()
    _read("repo.metadata", now=1000.0, reader=r, cache_dir=tmp_path)
    leftovers = [p.name for p in tmp_path.iterdir() if ".tmp." in p.name]
    assert leftovers == [], f"staged temp files were not cleaned up: {leftovers}"
    assert len(list(tmp_path.iterdir())) == 1
