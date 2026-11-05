"""Tests for tools/gh_read_cache.py — the invariant-read cache for `gh` (#1910 class).

WHY THESE TESTS EXIST
---------------------
The 5,000-call/hour pool emptied FOUR times in one night. The gated shim (`gh-gate`) paces,
serialises, honours `retry-after` and shares a cooldown — and it has NO CACHE, so every poll is a
live call. GitHub's own best practice is explicit: *"Make authenticated conditional requests, so
that unchanged data does not count against your primary rate limit"*.

A cache that can serve a STALE (or WRONG-SUBJECT) answer as if it were fresh is worse than no cache:
it turns "I could not read" into "I read X", which is this repo's false-PASS class. These tests are
therefore about the FAILURE directions, not the happy path.

⛔ MUTATION-VERIFIED, AND HONEST ABOUT WHAT IS NOT. Two earlier rounds found fences that could not
fail for the bug they named — a green suite proving nothing. 38 mutations of the module have now
been run; **37 are KILLED** and one survives ALONE, on purpose:

    M1  reader returns the unprojected body ............ KILLED   M12 decode_pages first doc only .... KILLED
    M2  _load ignores freshness ........................ KILLED   M13 envelope scope check removed ... KILLED
    M3  generic reader handler fails open .............. KILLED   M14 TTL dropped from FILENAME ..... KILLED
    M4  global-scope check removed from read() ......... KILLED   M15 import-time call unbound ...... KILLED
    M5  forbidden argv flags no longer refused ......... KILLED   M16 CLI live fallback dead-ends ... KILLED
    M6  declare_invariant skips spec validation ........ KILLED   M17 read() skips spec validation .. KILLED
    M7  import-time registry guard is a no-op .......... KILLED   M18 OverflowError escapes _load ... KILLED
    M8  _store is not atomic ........................... KILLED   M19 digest ignores projection SRC . KILLED
    M9  cache-write failure fails the read ............. KILLED   M20 shape check is a no-op ........ KILLED
    M10 projection dropped from the FILENAME only ...... SURVIVES  M21 non-dict page accepted ....... KILLED
    M11 projection dropped from the ENVELOPE only ...... KILLED   M22 error-document marker gone .... KILLED

Two further rounds added M23-M38 after review (all KILLED):

  cycle-6: M23 required_set ABSENT as empty | M24 _pick_last required | M25 workflow entry key
           M26 runner `name` | M27 all-null answer | M28 non-dict envelope | M29 reader non-bytes
           M30 _load denylist only
  cycle-7: M31 read() stores bytes verbatim (the guard-2 hole) | M32 reader projects instead
           M33 source digest dropped from the filename (cache never hits) | M34 kind_of mutable
           M35 _IMPORT_CHECK constant (needed the checked-key SET — a count was spoofable by `6`)
           M36 ttl_s ValueError | M37 required_set inner absence | M38 _project doesn't refuse

M10 surviving is the documented layering, not a hole: for the PROJECTION the envelope stamp is
authoritative and the filename digest only stops two projections from thrashing one file. (For the
TTL it is the other way round — the FILENAME is authoritative and the envelope's `ttl_s` check is
redundant.) Dropping both projection layers IS killed. Do not delete either layer believing the
other covers the same direction.

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
#: A RAW (unprojected) response body per key, as a `reader` must supply it. Each carries volatile
#: fields beside the invariant ones, so a `read()` that forgot to project is caught immediately.
RAW = {
    "repo.metadata": lambda n: json.dumps(
        {
            "id": n,
            "node_id": "N1",
            "full_name": "o/r",
            "default_branch": "main",
            "visibility": "private",
            "pushed_at": "2020-01-01",
            "open_issues_count": 412,
        }
    ).encode(),
    "repo.required_set": lambda n: json.dumps(
        {
            "required_status_checks": {"contexts": ["docs"], "checks": [{"context": "docs"}]},
            "enforce_admins": {"enabled": False},
        }
    ).encode(),
    "user.identity": lambda n: json.dumps({"login": "someone", "id": n}).encode(),
}


class Counter:
    """A reader that answers with a monotonic RAW body and counts its calls.

    ⛔ The reader returns RAW response bytes: the PROJECTION is `read()`'s job, so a test reader
    that returned a projected document would hide exactly the hole that let a library caller cache
    a whole body.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.fail: Exception | None = None

    def __call__(self, key: str) -> bytes:
        self.calls.append(key)
        if self.fail is not None:
            raise self.fail
        shape = RAW.get(key, RAW["repo.metadata"])
        return shape(len(self.calls))

    @property
    def n(self) -> int:
        return len(self.calls)


def _read(
    key: str, *, now: float, reader, ttl_s: int | None = None, cache_dir: Path, scope: str = SCOPE
):
    return C.read(key, scope=scope, cache_dir=cache_dir, now=now, reader=reader, ttl_s=ttl_s)


def _entry(cache_dir: Path, key: str = "repo.metadata", scope: str = SCOPE) -> Path:
    return cache_dir / C._cache_path(key, scope).name


# ══════════════════════════════════════════════════════════════════════════════════════════════
# 1. THE CACHE ACTUALLY SAVES A CALL
# ══════════════════════════════════════════════════════════════════════════════════════════════
def test_cached_hit_does_not_call_reader(tmp_path: Path) -> None:
    r = Counter()
    first = _read("repo.metadata", now=1000.0, reader=r, cache_dir=tmp_path)
    second = _read("repo.metadata", now=1000.0 + 1.0, reader=r, cache_dir=tmp_path)

    assert json.loads(first)["id"] == 1
    assert second == first, "a fresh cached value must be served verbatim"
    assert r.n == 1, (
        "the pool emptied because every poll was a live call — a hit inside the TTL MUST NOT "
        "call the reader, or the cache is decoration"
    )


def test_ttl_expiry_calls_reader_again(tmp_path: Path) -> None:
    r = Counter()
    _read("repo.metadata", now=1000.0, reader=r, cache_dir=tmp_path)
    out = _read(
        "repo.metadata", now=1000.0 + C.ttl_for("repo.metadata") + 1.0, reader=r, cache_dir=tmp_path
    )
    assert r.n == 2
    assert json.loads(out)["id"] == 2


def test_exact_ttl_boundary_is_a_miss(tmp_path: Path) -> None:
    """`age == ttl` is EXPIRED. A `<=` silently makes every TTL `ttl + one poll`."""
    r = Counter()
    _read("repo.metadata", now=1000.0, reader=r, cache_dir=tmp_path)
    _read("repo.metadata", now=1000.0 + C.ttl_for("repo.metadata"), reader=r, cache_dir=tmp_path)
    assert r.n == 2


def test_different_ttls_do_not_evict_each_others_entries(tmp_path: Path) -> None:
    """Two callers at different TTLs must each keep their entry.

    The filename had no TTL component while `_load` rejects a mismatched stamped ttl, so alternating
    TTLs produced 12 live reads where a working cache does 2 — silently disabling the cache for that
    key, i.e. exactly the burn this module exists to stop.
    """
    tight = 900
    r = Counter()
    for i in range(6):
        _read("repo.metadata", now=1000.0 + i, reader=r, cache_dir=tmp_path)
        _read("repo.metadata", now=1000.0 + i, reader=r, cache_dir=tmp_path, ttl_s=tight)
    assert r.n == 2, f"alternating TTLs thrashed one file: {r.n} live reads, expected 2"
    assert len(list(tmp_path.iterdir())) == 2, "each TTL must own its own entry"


# ══════════════════════════════════════════════════════════════════════════════════════════════
# 2. SCOPE IS CACHE IDENTITY (guard 1)
# ══════════════════════════════════════════════════════════════════════════════════════════════
def test_a_different_scope_is_a_different_entry(tmp_path: Path) -> None:
    """⛔ THE P0 of the first review. One repo's answer must never be served as another's."""
    calls: list[str] = []

    def reader_a(key: str) -> bytes:
        calls.append("A")
        return json.dumps({"required_status_checks": {"contexts": ["tortoise-only"]}}).encode()

    def reader_b(key: str) -> bytes:
        calls.append("B")
        return json.dumps({"required_status_checks": {"contexts": ["agent-infra-only"]}}).encode()

    a = _read(
        "repo.required_set",
        now=1000.0,
        reader=reader_a,
        cache_dir=tmp_path,
        scope="daniel-ospina/tortoise@main",
    )
    b = _read(
        "repo.required_set",
        now=1000.0,
        reader=reader_b,
        cache_dir=tmp_path,
        scope="daniel-ospina/agent-infra@main",
    )

    assert a != b, "a cross-scope hit served repo A's required set as repo B's"
    assert json.loads(b)["contexts"] == ["agent-infra-only"]
    assert calls == ["A", "B"], "reader B must actually be called"


def test_a_different_branch_is_a_different_entry(tmp_path: Path) -> None:
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
    """Defence in depth: a file placed at the right NAME is still rejected if its scope disagrees."""
    r = Counter()
    _read("repo.metadata", now=1000.0, reader=r, cache_dir=tmp_path)
    p = _entry(tmp_path)
    env = json.loads(p.read_text())
    env["scope"] = "someone/else@main"
    p.write_text(json.dumps(env))
    _read("repo.metadata", now=1000.0, reader=r, cache_dir=tmp_path)
    assert r.n == 2


# ── global keys are scoped by CREDENTIAL ──────────────────────────────────────────────────────
def test_read_refuses_a_non_credential_scope_for_a_global_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GH_TOKEN", "tok-a")
    r = Counter()
    with pytest.raises(C.TtlNotPermitted):
        _read("user.identity", now=1000.0, reader=r, cache_dir=tmp_path, scope=SCOPE)
    assert r.n == 0


def test_read_recomputes_the_credential_scope_rather_than_trusting_a_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """⛔ A PREFIX TEST WAS NOT ENOUGH (cycle-4 finding 3).

    Any literal `"global@…"` satisfied `scope.startswith("global@")`, so two lanes with different
    tokens could both pass `"global@"` and share one entry. `read()` now recomputes the expected
    scope and requires exact equality.
    """
    monkeypatch.setenv("GH_TOKEN", "tok-a")
    r = Counter()
    # The literal `"global@"` is exactly what the OLD prefix test accepted as a valid scope.
    with pytest.raises(C.TtlNotPermitted):
        _read("user.identity", now=1000.0, reader=r, cache_dir=tmp_path, scope="global@")
    assert r.n == 0, "a bare 'global@' prefix was accepted as a credential scope"

    # The computed scope is accepted, and a DIFFERENT credential's computed scope is not the same
    # entry even though both are `global@…`.
    real = C.scope_for("user.identity", "o/r", "main")
    _read("user.identity", now=1000.0, reader=r, cache_dir=tmp_path, scope=real)
    assert r.n == 1
    monkeypatch.setenv("GH_TOKEN", "tok-b")
    other = C.scope_for("user.identity", "o/r", "main")
    assert other != real, "two credentials derived the same scope"
    _read("user.identity", now=1000.0 + 1.0, reader=r, cache_dir=tmp_path, scope=other)
    assert r.n == 2


def test_two_credentials_never_share_an_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def reader(key: str) -> bytes:
        calls.append("x")
        return json.dumps(
            {"login": "alice" if len(calls) == 1 else "bob", "id": len(calls)}
        ).encode()

    monkeypatch.setenv("GH_TOKEN", "tok-alice")
    a = _read(
        "user.identity",
        now=1000.0,
        reader=reader,
        cache_dir=tmp_path,
        scope=C.scope_for("user.identity", "o/r", "main"),
    )
    monkeypatch.setenv("GH_TOKEN", "tok-bob")
    b = _read(
        "user.identity",
        now=1000.0,
        reader=reader,
        cache_dir=tmp_path,
        scope=C.scope_for("user.identity", "o/r", "main"),
    )

    assert a != b, "two credentials shared one identity entry — wrong subject, valid envelope"
    assert len(calls) == 2


def test_global_scope_never_contains_the_raw_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GH_TOKEN", "super-secret-token-value")
    scope = C.scope_for("user.identity", "o/r", "main")
    assert "super-secret-token-value" not in scope


def test_no_identifiable_credential_refuses_to_cache_a_global_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unknown identity FAILS CLOSED — a global key is simply not cacheable."""
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert C._credential_fingerprint() is None
    with pytest.raises(C.TtlNotPermitted):
        C.scope_for("user.identity", "o/r", "main")


# ══════════════════════════════════════════════════════════════════════════════════════════════
# 3. A MUTABLE KEY IS REFUSED, NEVER PASSED THROUGH (guard R4)
# ══════════════════════════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize(
    "key",
    [
        "pr.state",
        "pr.mergeStateStatus",
        "pr.checks",
        "run.status",
        "runner.status",
        "rate_limit.buckets",
        "issue.state",
        "pr.mergeable",
        "pr.reviewDecision",
        "repo.branches",
        "repo.tags",
        "repo.assignees",
        "repo.releases",
        "pr.approved",
        "pr.approvals",
        "repo.headsha",
        "repo.headoid",
        "repo.forks",
        "repo.stargazers",
        "repo.size",
        "repo.archived",
        "pr.files",
        "pr.additions",
        "pr.rebaseable",
    ],
)
def test_mutable_key_is_refused(key: str, tmp_path: Path) -> None:
    r = Counter()
    with pytest.raises(C.MutableReadRefused):
        _read(key, now=1000.0, reader=r, cache_dir=tmp_path)
    assert r.n == 0, (
        "a refused key must not reach the network — a refusal that still issues the call is a "
        "labelled pass-through, not a guard"
    )


def test_a_mutable_key_has_no_addressable_cache_entry(tmp_path: Path) -> None:
    """There is no path at which a mutable entry could even be PLANTED.

    `_cache_path` resolves the key through the registry, so a key outside it has no filename, no
    directory and no lookup: the refusal is structural rather than a check that could be reordered
    behind an `if path.exists()`.
    """
    with pytest.raises(KeyError):
        C._cache_path("pr.state", SCOPE)
    r = Counter()
    with pytest.raises(C.MutableReadRefused):
        _read("pr.state", now=1000.0, reader=r, cache_dir=tmp_path)
    assert r.n == 0
    assert not list(tmp_path.iterdir()), "a refused key must not have created an entry"


def test_undeclared_key_is_refused(tmp_path: Path) -> None:
    r = Counter()
    with pytest.raises(C.MutableReadRefused):
        _read("something.nobody.declared", now=1000.0, reader=r, cache_dir=tmp_path)
    assert r.n == 0, "an UNDECLARED key is not provably invariant — refuse, do not assume"


def test_registry_cannot_admit_a_mutable_name() -> None:
    with pytest.raises(C.MutableReadRefused):
        C.declare_invariant(
            "pr.mergeStateStatus", ttl_s=600, argv=("api", "x"), projection="p", project=lambda d: d
        )


def test_runner_inventory_is_cached_but_runner_status_is_not() -> None:
    assert C.kind_of("runners.inventory") == "invariant"
    assert C.kind_of("runner.status") == "mutable"


# ── vocabulary shape ─────────────────────────────────────────────────────────────────────────
def test_tokenizer_splits_camel_case() -> None:
    assert C._tokens("pr.reviewDecision") == ["pr", "review", "decision"]
    assert C._tokens("mergeStateStatus") == ["merge", "state", "status"]


def test_tokenizer_does_not_over_match() -> None:
    """The false-POSITIVE side: `run` must not match `runners`."""
    assert C._tokens("runners.inventory") == ["runners", "inventory"]
    assert not C._is_mutable_name("runners.inventory")


@pytest.mark.parametrize("key", ["x.accurateLimit", "repo.generateLimit", "corporate.limits"])
def test_glued_phrase_does_not_match_across_a_word_boundary(key: str) -> None:
    """Scanning the WHOLE glued key refused `accurate`+`limit` — the `"run" in "runners"` class,
    in the other direction. The phrase table matches ADJACENT TOKEN PAIRS only."""
    assert not C._is_mutable_name(key), f"{key!r} is legitimate but was refused as mutable"


def test_rate_limit_still_refused_after_pair_matching() -> None:
    assert C._is_mutable_name("rate_limit.buckets")
    assert C._is_mutable_name("ratelimit")


# ══════════════════════════════════════════════════════════════════════════════════════════════
# 4. FAIL CLOSED WHEN FRESHNESS CANNOT BE PROVEN (guards R3, R5)
# ══════════════════════════════════════════════════════════════════════════════════════════════
def test_corrupt_envelope_is_a_miss(tmp_path: Path) -> None:
    r = Counter()
    _read("repo.metadata", now=1000.0, reader=r, cache_dir=tmp_path)
    _entry(tmp_path).write_bytes(b"{not json")
    out = _read("repo.metadata", now=1000.0, reader=r, cache_dir=tmp_path)
    assert r.n == 2
    assert json.loads(out)["id"] == 2


def test_tampered_payload_is_a_miss(tmp_path: Path) -> None:
    r = Counter()
    _read("repo.metadata", now=1000.0, reader=r, cache_dir=tmp_path)
    p = _entry(tmp_path)
    env = json.loads(p.read_text())
    env["payload"] = base64.b64encode(b"payload-99").decode()
    p.write_text(json.dumps(env))
    _read("repo.metadata", now=1000.0, reader=r, cache_dir=tmp_path)
    assert r.n == 2


def test_clock_ahead_of_stored_at_is_a_miss(tmp_path: Path) -> None:
    r = Counter()
    _read("repo.metadata", now=1000.0, reader=r, cache_dir=tmp_path)
    p = _entry(tmp_path)
    env = json.loads(p.read_text())
    env["stored_at"] = 9999.0
    p.write_text(json.dumps(env))
    _read("repo.metadata", now=1000.0, reader=r, cache_dir=tmp_path)
    assert r.n == 2, "a future stamp is unprovable freshness, not infinite freshness"


def test_stale_entry_is_never_a_fallback_for_a_failed_read(tmp_path: Path) -> None:
    """⛔ THE LOAD-BEARING TEST. A reader that fails on a MISS must RAISE."""
    r = Counter()
    _read("repo.metadata", now=1000.0, reader=r, cache_dir=tmp_path)
    r.fail = C.Unreadable("gh api failed: HTTP 403")
    with pytest.raises(C.Unreadable):
        _read(
            "repo.metadata",
            now=1000.0 + C.ttl_for("repo.metadata") + 1.0,
            reader=r,
            cache_dir=tmp_path,
        )


@pytest.mark.parametrize(
    "boom",
    [
        RuntimeError("gh api failed"),
        OSError("disk/network fault"),
        TimeoutError("reader timed out"),
        ValueError("unparseable body"),
    ],
)
def test_ANY_reader_failure_is_fail_closed_not_just_unreadable(
    boom: Exception, tmp_path: Path
) -> None:
    """⛔ THE GENERIC HANDLER NEEDS ITS OWN FENCE (cycle-3 finding 1).

    The `Unreadable` case above is re-raised by `except Unreadable: raise` and NEVER reaches the
    generic `except Exception -> raise Unreadable`. Replacing that generic branch with `return b""`,
    or with a real stale fallback, kept the earlier suite fully green.
    """
    r = Counter()
    _read("repo.metadata", now=1000.0, reader=r, cache_dir=tmp_path)
    r.fail = boom
    with pytest.raises(C.Unreadable):
        _read(
            "repo.metadata",
            now=1000.0 + C.ttl_for("repo.metadata") + 1.0,
            reader=r,
            cache_dir=tmp_path,
        )


def test_missing_cache_dir_is_a_miss_not_a_crash(tmp_path: Path) -> None:
    r = Counter()
    out = _read("repo.metadata", now=1000.0, reader=r, cache_dir=tmp_path / "nope" / "deeper")
    assert json.loads(out)["id"] == 1
    assert r.n == 1


@pytest.mark.parametrize("bad_now", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_now_never_serves_a_hit(bad_now: float, tmp_path: Path) -> None:
    """A NaN `now` made `age < 0` AND `age >= ttl` both false, serving an arbitrary old entry."""
    r = Counter()
    _read("repo.metadata", now=1000.0, reader=r, cache_dir=tmp_path)
    _read("repo.metadata", now=bad_now, reader=r, cache_dir=tmp_path)
    assert r.n == 2, "a non-finite clock cannot prove freshness"


# ══════════════════════════════════════════════════════════════════════════════════════════════
# 5. NO WIDENING (guard R6)
# ══════════════════════════════════════════════════════════════════════════════════════════════
def test_ttl_may_not_exceed_the_key_ceiling(tmp_path: Path) -> None:
    r = Counter()
    with pytest.raises(C.TtlNotPermitted):
        _read(
            "repo.metadata",
            now=1000.0,
            reader=r,
            ttl_s=C.ttl_for("repo.metadata") + 1,
            cache_dir=tmp_path,
        )
    assert r.n == 0


def test_requested_ttl_mismatch_invalidates_the_entry(tmp_path: Path) -> None:
    r = Counter()
    ceiling = C.ttl_for("repo.metadata")
    _read("repo.metadata", now=1000.0, reader=r, ttl_s=ceiling, cache_dir=tmp_path)
    _read(
        "repo.metadata", now=1000.0 + 1.0, reader=r, ttl_s=max(60, ceiling // 2), cache_dir=tmp_path
    )
    assert r.n == 2


def test_shorter_ttl_is_allowed(tmp_path: Path) -> None:
    r = Counter()
    _read("repo.metadata", now=1000.0, reader=r, ttl_s=60, cache_dir=tmp_path)
    _read("repo.metadata", now=1000.0 + 1.0, reader=r, ttl_s=60, cache_dir=tmp_path)
    assert r.n == 1


def test_declare_invariant_cannot_widen_an_existing_ceiling() -> None:
    """⛔ The refusal must come from the CEILING guard, not from a different one.

    The earlier version passed `--jq "."`, so the projection guard fired first and
    `pytest.raises(TtlNotPermitted)` was satisfied by the wrong guard — deleting the ceiling check
    outright left the suite green.
    """
    before = C.ttl_for("repo.metadata")
    with pytest.raises(C.TtlNotPermitted) as excinfo:
        C.declare_invariant(
            "repo.metadata",
            ttl_s=before * 4,
            argv=("api", "x"),
            projection="p",
            project=lambda d: d,
        )
    assert "ceiling" in str(excinfo.value), f"refused by a different guard: {excinfo.value}"
    assert C.ttl_for("repo.metadata") == before


def test_declare_invariant_may_tighten() -> None:
    key = "repo.tightenable"
    C.declare_invariant(key, ttl_s=600, argv=("api", "x"), projection="p1", project=lambda d: d)
    C.declare_invariant(key, ttl_s=60, argv=("api", "x"), projection="p1", project=lambda d: d)
    assert C.ttl_for(key) == 60
    C.INVARIANTS.pop(key, None)


# ══════════════════════════════════════════════════════════════════════════════════════════════
# 6. GUARD 2 — NO KEY CACHES A WHOLE BODY. BEHAVIOURAL, not flag-string inspection.
# ══════════════════════════════════════════════════════════════════════════════════════════════
def test_registry_keys_all_declare_a_projection_and_a_callable() -> None:
    for key, spec in C.INVARIANTS.items():
        assert callable(spec.project), f"{key!r} has no projection callable"
        assert spec.projection and spec.projection.strip() != ".", f"{key!r} has no projection id"


VOLATILE = {
    "status": "online",
    "busy": True,
    "pushed_at": "2020-01-01T00:00:00Z",
    "updated_at": "2020-01-01T00:00:00Z",
    "created_at": "2020-01-01T00:00:00Z",
    "open_issues_count": 7,
    "stargazers_count": 9,
    "forks_count": 3,
    "size": 12345,
    "archived": False,
    "ephemeral": True,
    "total_count": 99,
    "enforcement_level": "everyone",
    "topics": ["a"],
    "plan": {"name": "free"},
}

#: A REALISTIC body per endpoint, carrying volatile fields beside the invariant ones.
SHAPED = {
    "repo.metadata": [
        {
            **VOLATILE,
            "id": 1,
            "node_id": "N1",
            "full_name": "o/r",
            "default_branch": "main",
            "visibility": "private",
        }
    ],
    "repo.required_set": [
        {
            **VOLATILE,
            "required_status_checks": {"contexts": ["docs"], "checks": [{"context": "docs"}]},
        }
    ],
    "repo.labels": [[{**VOLATILE, "name": "bug", "color": "fff"}]],
    "repo.workflows": [
        {
            **VOLATILE,
            "workflows": [
                {**VOLATILE, "id": 3, "path": ".github/workflows/ci.yml", "state": "active"}
            ],
        }
    ],
    "runners.inventory": [
        {
            **VOLATILE,
            "runners": [
                {**VOLATILE, "name": "cmux", "labels": [{"name": "self-hosted", "color": "fff"}]}
            ],
        }
    ],
    "user.identity": [{**VOLATILE, "login": "someone", "id": 42}],
}


@pytest.mark.parametrize("key", sorted(C.INVARIANTS))
def test_shipped_projections_drop_volatile_fields(key: str) -> None:
    """⛔ BEHAVIOURAL GUARD 2. Feed a REAL body stuffed with volatile fields; none may survive.

    Strictly stronger than inspecting a `--jq` string: it tests what the code DOES to a realistic
    payload, so it keeps working if the projection is rewritten, and it cannot be satisfied by an
    argv that merely mentions a flag.
    """
    out = C.INVARIANTS[key].project(SHAPED[key])
    dumped = json.dumps(out)
    for field in (
        "pushed_at",
        "updated_at",
        "created_at",
        "open_issues_count",
        "stargazers_count",
        "forks_count",
        "archived",
        "ephemeral",
        "total_count",
        "enforcement_level",
        "topics",
        "plan",
        "busy",
        "status",
        "color",
    ):
        assert field not in dumped, f"{key!r} leaked volatile field {field!r}: {dumped}"


@pytest.mark.parametrize("key", sorted(C.INVARIANTS))
def test_shipped_projections_keep_their_invariant_fields(key: str) -> None:
    """The other half: a projection that dropped EVERYTHING would pass the test above."""
    out = C.INVARIANTS[key].project(SHAPED[key])
    assert out, f"{key!r} projected to an empty answer from a well-formed body"
    dumped = json.dumps(out)
    for expected in {
        "repo.metadata": ("o/r", "main"),
        "repo.required_set": ("docs",),
        "repo.labels": ("bug",),
        "repo.workflows": (".github/workflows/ci.yml",),
        "runners.inventory": ("cmux", "self-hosted"),
        "user.identity": ("someone",),
    }[key]:
        assert expected in dumped, f"{key!r} DROPPED invariant value {expected!r}: {dumped}"


# ── D3: a body the projection does not recognise must RAISE, never project nothing ────────────
@pytest.mark.parametrize(
    ("key", "body"),
    [
        # An error document projected to a complete-looking all-None identity, cached 6 h fresh.
        ("repo.metadata", [{"message": "Server Error"}]),
        ("repo.metadata", [{"whatever": 1}]),
        ("user.identity", [{"message": "Bad credentials"}]),
        # A wrong-shaped PAGE was silently dropped, so page 1 looked like the whole answer.
        ("repo.labels", [[{"name": "a"}], {"message": "oops"}]),
        ("repo.labels", [{"message": "oops"}]),
        ("repo.workflows", [[{"id": 1}]]),
        ("repo.workflows", [{"message": "oops"}]),
        ("runners.inventory", [[{"name": "r"}]]),
        ("runners.inventory", [{"message": "oops"}]),
        # A missing collection is not an empty collection.
        ("runners.inventory", [{"total_count": 0}]),
        ("repo.workflows", [{"total_count": 3}]),
        # An error body that ALSO carries the collection key: only the `message` marker catches
        # this one, so without it an error projects to a cached "empty answer".
        ("repo.workflows", [{"message": "oops", "workflows": []}]),
        ("runners.inventory", [{"message": "oops", "runners": []}]),
        # GitHub's REAL error shape (message + documentation_url + status).
        (
            "repo.metadata",
            [{"message": "Not Found", "documentation_url": "https://x", "status": "404"}],
        ),
        # No page at all.
        ("repo.metadata", []),
    ],
)
def test_a_wrong_shaped_body_is_Unreadable_not_an_empty_answer(key: str, body) -> None:
    """⛔ D3. `{"message": "Server Error"}` used to cache `{"id": None, …}` as fresh for six hours,
    and a wrong-shaped page 2 used to be dropped so page 1 looked like the whole answer.

    A fresh entry built from a body the code did not understand is a LIE, not a saving.
    """
    with pytest.raises(C.Unreadable):
        C.INVARIANTS[key].project(body)


def test_an_explicitly_empty_collection_is_a_valid_answer() -> None:
    """An EMPTY list is a real answer — `{"total_count": 0, "runners": []}`. A MISSING list is not."""
    assert C.INVARIANTS["runners.inventory"].project([{"total_count": 0, "runners": []}]) == []
    assert C.INVARIANTS["repo.workflows"].project([{"total_count": 0, "workflows": []}]) == []


def test_an_empty_label_PAGE_is_a_valid_answer() -> None:
    """A page that is an empty array is a real answer; a MISSING page is not (`no page to project`)."""
    assert C.INVARIANTS["repo.labels"].project([[]]) == []


def test_runner_inventory_projection_drops_status_and_busy() -> None:
    spec = C.INVARIANTS["runners.inventory"]
    page = {
        "runners": [
            {
                "name": "r1",
                "status": "online",
                "busy": True,
                "labels": [{"name": "self-hosted", "type": "read-only"}],
            },
        ]
    }
    out = spec.project([page])
    assert out == [{"name": "r1", "labels": ["self-hosted"]}], out
    assert "status" not in json.dumps(out) and "busy" not in json.dumps(out)


def test_repo_metadata_projection_keeps_only_identity_fields() -> None:
    spec = C.INVARIANTS["repo.metadata"]
    out = spec.project(
        [
            {
                "id": 1,
                "full_name": "o/r",
                "default_branch": "main",
                "visibility": "private",
                "node_id": "N",
                "pushed_at": "x",
                "open_issues_count": 5,
            }
        ]
    )
    assert set(out) == {"id", "node_id", "full_name", "default_branch", "visibility"}


def test_declare_invariant_requires_a_projection_callable() -> None:
    with pytest.raises(C.TtlNotPermitted):
        C.declare_invariant(
            "repo.noproj", ttl_s=600, argv=("api", "x"), projection="p", project=None
        )  # type: ignore[arg-type]


@pytest.mark.parametrize("proj", ["", "   ", "."])
def test_declare_invariant_requires_a_real_projection_id(proj: str) -> None:
    with pytest.raises(C.TtlNotPermitted):
        C.declare_invariant(
            "repo.badproj", ttl_s=600, argv=("api", "x"), projection=proj, project=lambda d: d
        )


# ══════════════════════════════════════════════════════════════════════════════════════════════
# 7. THE `--slurp`/`--jq` STRUCTURAL FIX (cycle-4 blockers 1 and 2)
# ══════════════════════════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize(
    "flag", ["--jq", "-q", "--slurp", "--template", "-t", "--jq=.", "--template=x"]
)
def test_argv_may_not_carry_gh_projection_flags(flag: str) -> None:
    """⛔ MEASURED: `gh api --paginate --slurp --jq '…'` → exit 1, 'the `--slurp` option is not
    supported with `--jq` or `--template'`.

    So requiring BOTH made every paginated key dead — and pflag takes the LAST `--jq` while a
    validator sees the FIRST, so `--jq '{id}' --jq '.'` passed validation and cached the whole
    body. Projection is this module's job; the argv may not carry these at all.
    """
    with pytest.raises(C.TtlNotPermitted):
        C.declare_invariant(
            "repo.flagged", ttl_s=600, argv=("api", "x", flag), projection="p", project=lambda d: d
        )


def test_no_shipped_key_carries_a_projection_flag() -> None:
    for key, spec in C.INVARIANTS.items():
        for a in spec.argv:
            assert a not in C._FORBIDDEN_ARGV_FLAGS, f"{key!r} carries {a!r}"


@pytest.mark.parametrize("key", ["repo.labels", "repo.workflows", "runners.inventory"])
def test_paginated_keys_still_paginate(key: str) -> None:
    assert "--paginate" in C.INVARIANTS[key].argv, f"{key!r} must still fetch every page"


# ── decode_pages: what replaces `--slurp` ────────────────────────────────────────────────────
def test_decode_pages_handles_several_concatenated_documents() -> None:
    """`gh api --paginate` without `--slurp` emits one document PER PAGE."""
    raw = b'[{"name":"a"}]\n[{"name":"b"}]'
    assert C.decode_pages(raw) == [[{"name": "a"}], [{"name": "b"}]]


def test_decode_pages_handles_a_single_document() -> None:
    assert C.decode_pages(b'{"id": 1}') == [{"id": 1}]


def test_decode_pages_handles_object_pages() -> None:
    raw = b'{"workflows":[{"id":1}]}{"workflows":[{"id":2}]}'
    assert C.decode_pages(raw) == [{"workflows": [{"id": 1}]}, {"workflows": [{"id": 2}]}]


@pytest.mark.parametrize("raw", [b"", b"   \n\t ", b"not json at all", b"<html>504</html>"])
def test_decode_pages_refuses_a_non_json_body(raw: bytes) -> None:
    with pytest.raises(C.Unreadable):
        C.decode_pages(raw)


def test_projection_over_multiple_pages_concatenates() -> None:
    spec = C.INVARIANTS["repo.labels"]
    assert spec.project([[{"name": "a"}], [{"name": "b"}, {"name": "c"}]]) == ["a", "b", "c"]


def test_gh_reader_APPLIES_the_projection(monkeypatch: pytest.MonkeyPatch) -> None:
    """⛔ GUARD 2'S HOLE, CLOSED. Every other projection test calls `spec.project` DIRECTLY, so a
    `_gh_reader` that returned `proc.stdout` unprojected would cache the whole body and stay green.

    This drives the real reader with a stubbed `subprocess.run` (no network) and asserts the stored
    bytes are the PROJECTION, and that the argv `gh` would receive carries no `--jq`/`--slurp`.
    """
    seen: list[list[str]] = []

    class Proc:
        returncode = 0
        stderr = b""
        # gh's real shape for this endpoint is one OBJECT per page, not an array of objects.
        stdout = json.dumps(
            {
                "total_count": 1,
                "runners": [
                    {
                        "name": "r1",
                        "status": "online",
                        "busy": True,
                        "labels": [{"name": "self-hosted"}],
                    }
                ],
            }
        ).encode()

    def fake_run(cmd, **kw):
        seen.append(list(cmd))
        return Proc()

    monkeypatch.setattr(C.subprocess, "run", fake_run)
    raw = C._gh_reader("o/r", "main")("runners.inventory")

    # ⛔ The reader returns RAW bytes on purpose: the projection belongs to `read()`, so that a
    # caller supplying its own reader is protected too. Projecting here would leave the library
    # path caching whole bodies (measured: `pushed_at` served for a 6-hour TTL).
    stored = json.loads(C._project("runners.inventory", raw))
    assert stored == [{"name": "r1", "labels": ["self-hosted"]}], (
        f"the projection let volatile fields through: {stored!r}"
    )
    assert "online" not in json.dumps(stored) and "busy" not in json.dumps(stored)
    assert seen and "--paginate" in seen[0]
    for flag in C._FORBIDDEN_ARGV_FLAGS:
        assert flag not in seen[0], f"the argv handed to gh carries {flag!r}"


def test_gh_reader_surfaces_a_nonzero_exit_as_unreadable(monkeypatch: pytest.MonkeyPatch) -> None:
    class Proc:
        returncode = 1
        stdout = b""
        stderr = b"gh: HTTP 403"

    monkeypatch.setattr(C.subprocess, "run", lambda cmd, **kw: Proc())
    with pytest.raises(C.Unreadable) as excinfo:
        C._gh_reader("o/r", "main")("repo.metadata")
    assert "403" in str(excinfo.value)


def test_gh_reader_surfaces_a_projection_failure_as_unreadable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A projection that cannot run must not yield a cached answer."""
    key = "repo.boom_projection"

    def boom(docs):
        raise KeyError("missing field")

    C.declare_invariant(key, ttl_s=600, argv=("api", "x"), projection="boom", project=boom)
    try:

        class Proc:
            returncode = 0
            stderr = b""
            stdout = b'{"ok": true}'

        monkeypatch.setattr(C.subprocess, "run", lambda cmd, **kw: Proc())
        with pytest.raises(C.Unreadable):
            C._project(key, C._gh_reader("o/r", "main")(key))
    finally:
        C.INVARIANTS.pop(key, None)


# ══════════════════════════════════════════════════════════════════════════════════════════════
# 8. PROJECTION IS PART OF THE CACHE IDENTITY (guard 4)
# ══════════════════════════════════════════════════════════════════════════════════════════════
def test_changing_a_projection_invalidates_the_cached_entry(tmp_path: Path) -> None:
    """A maintenance edit to a shipped projection must not serve OLD-schema entries as fresh.

    `repo.metadata` holds 6 h, so otherwise a newly-added field would be missing for six hours.
    """
    key = "repo.projection_probe"
    C.declare_invariant(
        key, ttl_s=600, argv=("api", "x"), projection="v1", project=lambda d: {"id": 1}
    )
    try:
        r1 = Counter()
        _read(key, now=1000.0, reader=r1, cache_dir=tmp_path)
        assert r1.n == 1

        C.INVARIANTS[key] = C.Invariant(
            ttl_s=600,
            argv=("api", "x"),
            projection="v2",
            project=lambda d: {"id": 1, "default_branch": "main"},
        )
        r2 = Counter()
        _read(key, now=1000.0 + 1.0, reader=r2, cache_dir=tmp_path)
        assert r2.n == 1, "an entry selected by the OLD projection was served as a fresh hit"
    finally:
        C.INVARIANTS.pop(key, None)


def test_envelope_projection_must_match(tmp_path: Path) -> None:
    """The second layer: a file at the right NAME is still rejected if its projection disagrees."""
    r = Counter()
    _read("repo.metadata", now=1000.0, reader=r, cache_dir=tmp_path)
    p = _entry(tmp_path)
    env = json.loads(p.read_text())
    env["projection"] = C._projection_digest("a-different-projection")
    p.write_text(json.dumps(env))
    _read("repo.metadata", now=1000.0, reader=r, cache_dir=tmp_path)
    assert r.n == 2


def test_envelope_digest_is_over_the_payload_only() -> None:
    env = json.loads(C._envelope("repo.metadata", SCOPE, b"abc", now=1.0, ttl_s=2).decode())
    assert env["sha256"] == hashlib.sha256(b"abc").hexdigest()


# ══════════════════════════════════════════════════════════════════════════════════════════════
# 9. ATOMICITY (guard R8)
# ══════════════════════════════════════════════════════════════════════════════════════════════
def test_partial_entry_from_a_killed_writer_is_a_miss(tmp_path: Path) -> None:
    r = Counter()
    _read("repo.metadata", now=1000.0, reader=r, cache_dir=tmp_path)
    p = _entry(tmp_path)
    p.write_bytes(p.read_bytes()[:10])
    _read("repo.metadata", now=1000.0, reader=r, cache_dir=tmp_path)
    assert r.n == 2


def test_store_is_atomic_a_failed_replace_leaves_the_old_entry_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """⛔ Discriminating: a regression to `path.write_bytes(blob)` would pass every other test."""
    r = Counter()
    _read("repo.metadata", now=1000.0, reader=r, cache_dir=tmp_path)
    good = _entry(tmp_path).read_bytes()

    def boom(src, dst):
        raise OSError("simulated crash during replace")

    monkeypatch.setattr(C.os, "replace", boom)

    def reader_then_crash(key: str) -> bytes:
        return json.dumps({"id": 999, "full_name": "o/r", "default_branch": "main"}).encode()

    got = _read(
        "repo.metadata",
        now=1000.0 + C.ttl_for("repo.metadata") + 1,
        reader=reader_then_crash,
        cache_dir=tmp_path,
    )
    assert json.loads(got)["id"] == 999, (
        "a failed cache WRITE must not fail a read that just succeeded"
    )
    assert _entry(tmp_path).read_bytes() == good, (
        "a crashed swap must not have touched the existing entry — the write was not staged"
    )
    assert not [p for p in tmp_path.iterdir() if ".tmp." in p.name], "staged temp file leaked"


def test_import_time_guard_rejects_a_hand_registered_whole_body_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """⛔ MUTATION 9's FENCE. `INVARIANTS[k] = …` bypasses `declare_invariant`, so the import-time
    guard has to be re-run — and its behaviour must be pinned, not just its presence."""
    key = "repo.hand_registered"
    C.INVARIANTS[key] = C.Invariant(ttl_s=600, argv=("api", "x"), projection=".", project=None)  # type: ignore[arg-type]
    try:
        with pytest.raises(C.TtlNotPermitted):
            C._assert_every_key_projects()
    finally:
        C.INVARIANTS.pop(key, None)


# ══════════════════════════════════════════════════════════════════════════════════════════════
# 10. CYCLE-5 FINDINGS — the paths that were described but NOT pinned
# ══════════════════════════════════════════════════════════════════════════════════════════════
def test_the_import_time_registry_guard_actually_RAN() -> None:
    """⛔ The call was bare, so it could be deleted with all 107 earlier tests still green
    (every test called `_assert_every_key_projects` DIRECTLY). It is now bound to `_IMPORT_CHECK`,
    so deleting the call site raises AttributeError here."""
    # The set the loop actually VISITED — not a boolean and not a count. Measured: a hardcoded
    # `_IMPORT_CHECK = 6` satisfied `== len(INVARIANTS)` and survived the whole suite.
    assert frozenset(C.INVARIANTS) == C._IMPORT_CHECK


def test_read_validates_the_spec_on_every_call(tmp_path: Path) -> None:
    """Defence in depth for guard 2: even a key inserted straight into INVARIANTS (bypassing
    `declare_invariant`) cannot be read if it declares no projection."""
    key = "repo.injected"
    C.INVARIANTS[key] = C.Invariant(ttl_s=600, argv=("api", "x"), projection=".", project=None)  # type: ignore[arg-type]
    try:
        r = Counter()
        with pytest.raises(C.TtlNotPermitted):
            _read(key, now=1000.0, reader=r, cache_dir=tmp_path)
        assert r.n == 0
    finally:
        C.INVARIANTS.pop(key, None)


def test_a_non_numeric_clock_is_refused_not_a_crash(tmp_path: Path) -> None:
    """`read(now="1000")` reached `math.isfinite` and raised TypeError — unhandled, where the
    contract promises "any doubt → MISS"."""
    r = Counter()
    # A NUMERIC STRING is accepted (it converts cleanly); a non-number is REFUSED, where the old
    # code crashed with an unhandled TypeError from inside the freshness test.
    for bad in ("not-a-number", None, object()):
        with pytest.raises(C.TtlNotPermitted):
            _read("repo.metadata", now=bad, reader=r, cache_dir=tmp_path)  # type: ignore[arg-type]
    assert r.n == 0
    assert json.loads(_read("repo.metadata", now="1000", reader=r, cache_dir=tmp_path))["id"] == 1


def test_an_Infinity_stamped_envelope_is_a_MISS_not_a_latched_crash(tmp_path: Path) -> None:
    """⛔ D2. `json.loads` ACCEPTS the bare `Infinity` literal and `int(inf)` raises OverflowError,
    which is NOT a ValueError — so it escaped `_load`, escaped `read()`'s contract, escaped
    `main()`'s handlers, and LATCHED: the read never reached the reader, so the bad entry was never
    rewritten and every later read of that key raised too, until `--purge`."""
    r = Counter()
    _read("repo.metadata", now=1000.0, reader=r, cache_dir=tmp_path)
    p = _entry(tmp_path)
    env = json.loads(p.read_text())
    env["ttl_s"] = float("inf")
    p.write_text(json.dumps(env))  # written as the bare Infinity literal, which json accepts

    out = _read("repo.metadata", now=1000.0, reader=r, cache_dir=tmp_path)  # must NOT raise
    assert r.n == 2, "the malformed entry must be a MISS, and the MISS must rewrite it"
    assert json.loads(out)["id"] == 2
    # ...and it must be HEALED: the next read is a normal hit, not another failure.
    _read("repo.metadata", now=1000.0 + 1.0, reader=r, cache_dir=tmp_path)
    assert r.n == 2, "the bad entry was not healed by the re-read"


@pytest.mark.parametrize("field", ["stored_at", "ttl_s"])
def test_a_non_finite_envelope_field_is_a_miss(field: str, tmp_path: Path) -> None:
    r = Counter()
    _read("repo.metadata", now=1000.0, reader=r, cache_dir=tmp_path)
    p = _entry(tmp_path)
    env = json.loads(p.read_text())
    env[field] = float("nan") if field == "stored_at" else float("-inf")
    p.write_text(json.dumps(env))
    _read("repo.metadata", now=1000.0, reader=r, cache_dir=tmp_path)
    assert r.n == 2


# ── D4: the digest tracks the SOURCE, not the hand-written id ──────────────────────────────
def test_projection_digest_changes_when_the_source_changes() -> None:
    """⛔ An id is a RENAME-detector. Editing the projection BODY while leaving `…-v1` in place used
    to serve the old-schema entry as a fresh hit for the whole TTL."""

    def v_one(docs):
        return {"id": 1}

    def v_two(docs):
        return {"id": 1, "full_name": "o/r"}

    assert C._projection_digest("same-id", v_one) != C._projection_digest("same-id", v_two)
    assert C._projection_digest("same-id", v_one) == C._projection_digest("same-id", v_one)
    assert C._projection_digest("a", v_one) != C._projection_digest("b", v_one)


def test_shipped_projections_are_identified_by_source_not_only_by_id() -> None:
    for key, spec in C.INVARIANTS.items():
        with_source = C._projection_digest(spec.projection, spec.project)
        id_only = C._projection_digest(spec.projection)
        assert with_source != id_only, (
            f"{key!r}: the source contributes nothing to the identity, so an edit to the "
            f"projection body would not invalidate the cached entry"
        )


def test_a_cached_entry_stops_matching_when_the_shipped_projection_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = "repo.metadata"
    r = Counter()
    _read(key, now=1000.0, reader=r, cache_dir=tmp_path)
    assert r.n == 1

    base = C.INVARIANTS[key].project  # capture BEFORE patching, or it recurses into itself

    def widened(docs):
        return {**base(docs), "new_field": True}

    import dataclasses

    monkeypatch.setitem(  # same declared id — only the BODY changed
        C.INVARIANTS, key, dataclasses.replace(C.INVARIANTS[key], project=widened)
    )
    r2 = Counter()
    _read(key, now=1000.0 + 1.0, reader=r2, cache_dir=tmp_path)
    assert r2.n == 1, "an entry built from the OLD projection was served after the body changed"


# ── the CLI, which was entirely untested ──────────────────────────────────────────────────
class _Out:
    """Stands in for sys.stdout so `main()`'s `sys.stdout.buffer.write` works under pytest."""

    def __init__(self) -> None:
        self.buffer = __import__("io").BytesIO()
        self.text: list[str] = []

    def write(self, s: str) -> int:
        self.text.append(s)
        return len(s)

    def flush(self) -> None:
        return None


def test_cli_list_prints_the_registry() -> None:
    out = _Out()
    import io
    import sys as _sys

    buf = io.StringIO()
    old = _sys.stdout
    _sys.stdout = buf
    try:
        rc = C.main(["--list"])
    finally:
        _sys.stdout = old
    assert rc == 0
    for key in C.INVARIANTS:
        assert key in buf.getvalue()
    assert out is not None


def test_cli_purge_removes_entries(tmp_path: Path) -> None:
    r = Counter()
    _read("repo.metadata", now=1000.0, reader=r, cache_dir=tmp_path)
    assert list(tmp_path.iterdir())
    rc = C.main(["--purge", "--cache-dir", str(tmp_path)])
    assert rc == 0
    assert not list(tmp_path.iterdir()), "--purge left entries behind"


def test_cli_requires_a_key(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        C.main(["--cache-dir", str(tmp_path)])


def test_cli_refuses_a_mutable_key(tmp_path: Path) -> None:
    assert C.main(["--key", "pr.state", "--repo", "o/r", "--cache-dir", str(tmp_path)]) == 1


def test_cli_no_cache_reads_live(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_reader(repo: str, branch: str):
        def _r(key: str) -> bytes:
            calls.append(key)
            return json.dumps({"id": 5, "full_name": "o/r", "default_branch": "main"}).encode()

        return _r

    monkeypatch.setattr(C, "_gh_reader", fake_reader)
    out = _Out()
    monkeypatch.setattr(C.sys, "stdout", out)
    rc = C.main(
        ["--key", "repo.metadata", "--repo", "o/r", "--cache-dir", str(tmp_path), "--no-cache"]
    )
    assert rc == 0
    assert json.loads(out.buffer.getvalue())["id"] == 5
    assert calls == ["repo.metadata"]
    assert not list(tmp_path.iterdir()), "--no-cache must not write an entry"


def test_cli_global_key_without_a_token_still_reads_LIVE(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """⛔ D5. `scope_for` correctly refuses to guess an account, but the CLI must NOT dead-end
    there: the promise is a live read, and that branch had ZERO coverage — deleting it silently
    turned a live read into `REFUSED exit 1`."""
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    calls: list[str] = []

    def fake_reader(repo: str, branch: str):
        def _r(key: str) -> bytes:
            calls.append(key)
            return json.dumps({"login": "someone", "id": 9}).encode()

        return _r

    monkeypatch.setattr(C, "_gh_reader", fake_reader)
    out = _Out()
    monkeypatch.setattr(C.sys, "stdout", out)
    rc = C.main(["--key", "user.identity", "--cache-dir", str(tmp_path)])
    assert rc == 0, "a global key with no identifiable credential must be read LIVE, not refused"
    assert json.loads(out.buffer.getvalue())["login"] == "someone"
    assert calls == ["user.identity"]
    assert not list(tmp_path.iterdir()), "an unidentifiable-credential read must not be cached"


def test_cli_honours_a_tightened_ttl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_reader(repo: str, branch: str):
        def _r(key: str) -> bytes:
            calls.append(key)
            return json.dumps({"id": 1, "full_name": "o/r", "default_branch": "main"}).encode()

        return _r

    monkeypatch.setattr(C, "_gh_reader", fake_reader)
    out = _Out()
    monkeypatch.setattr(C.sys, "stdout", out)
    assert (
        C.main(
            ["--key", "repo.metadata", "--repo", "o/r", "--cache-dir", str(tmp_path), "--ttl", "60"]
        )
        == 0
    )
    assert (
        C.main(
            ["--key", "repo.metadata", "--repo", "o/r", "--cache-dir", str(tmp_path), "--ttl", "60"]
        )
        == 0
    )
    assert len(calls) == 1, "--ttl 60 should have produced one live read and one hit"


# ══════════════════════════════════════════════════════════════════════════════════════════════
# 11. CYCLE-6 FINDINGS — "an identity with nulls" is not an answer
# ══════════════════════════════════════════════════════════════════════════════════════════════
def test_required_set_ABSENT_is_not_the_same_as_NULL() -> None:
    """⛔ THE MERGE-GATING KEY. An empty required set reads as "this branch requires no checks".

    Measured before this check: `[{}]` — and a BRANCH OBJECT (`{"id":1,"name":"main",
    "protection":{}}`) — both projected to `{"contexts": [], "checks": []}` and were cached for 30
    minutes. `required_status_checks: null` is the legitimate answer; an ABSENT key is not.
    """
    for body in ([{}], [{"id": 1, "name": "main", "protection": {}}]):
        with pytest.raises(C.Unreadable):
            C.INVARIANTS["repo.required_set"].project(body)


def test_required_set_null_is_the_legitimate_no_checks_answer() -> None:
    assert C.INVARIANTS["repo.required_set"].project([{"required_status_checks": None}]) == {
        "contexts": [],
        "checks": [],
    }


@pytest.mark.parametrize(
    ("key", "body"),
    [
        # A body that names no identity: `login` absent used to yield {"login": None, "id": 1}.
        ("user.identity", [{"id": 1}]),
        ("repo.metadata", [{"id": 1}]),
        # An inventory/workflow entry that names nothing.
        ("repo.workflows", [{"workflows": [{}]}]),
        ("repo.workflows", [{"workflows": [{"id": 1, "path": "a"}]}]),
        ("runners.inventory", [{"runners": [{}]}]),
        # Everything projected is null.
        ("user.identity", [{"login": None, "id": None}]),
    ],
)
def test_an_identity_with_nulls_is_not_an_answer(key: str, body) -> None:
    """⛔ `[{"id": 1}]` used to project `user.identity` to `{"login": None, "id": 1}`: an identity
    naming no account, cached for 12 hours. Same for `[{}]` inside a collections response."""
    with pytest.raises(C.Unreadable):
        C.INVARIANTS[key].project(body)


def test_a_well_formed_identity_still_projects() -> None:
    """The other direction — the added strictness must not refuse a real body."""
    assert C.INVARIANTS["user.identity"].project([{"login": "a", "id": 7}]) == {
        "login": "a",
        "id": 7,
    }


def test_a_non_dict_envelope_is_a_miss_not_an_AttributeError(tmp_path: Path) -> None:
    """A JSON ARRAY envelope raises AttributeError out of `_load` on the `env.get(...)` path — the
    same uncaught-exception/latch class as the OverflowError defect."""
    r = Counter()
    _read("repo.metadata", now=1000.0, reader=r, cache_dir=tmp_path)
    _entry(tmp_path).write_text("[1, 2, 3]")
    _read("repo.metadata", now=1000.0, reader=r, cache_dir=tmp_path)  # must not raise
    assert r.n == 2


@pytest.mark.parametrize("raw", [b"[1,2,3]", b'"a string"', b"null", b"0", b"{}"])
def test_odd_envelopes_never_escape_load(raw: bytes, tmp_path: Path) -> None:
    r = Counter()
    _read("repo.metadata", now=1000.0, reader=r, cache_dir=tmp_path)
    _entry(tmp_path).write_bytes(raw)
    _read("repo.metadata", now=1000.0, reader=r, cache_dir=tmp_path)
    assert r.n == 2


@pytest.mark.parametrize("payload", ["a string", None, 42, {"a": 1}])
def test_a_reader_returning_non_bytes_is_Unreadable(payload, tmp_path: Path) -> None:
    """`bytes("str")` raises TypeError out of `read()` — uncaught, where the contract promises
    `Unreadable`."""
    r = Counter()
    r.fail = None

    def bad_reader(key: str):
        return payload

    with pytest.raises(C.Unreadable):
        _read("repo.metadata", now=1000.0, reader=bad_reader, cache_dir=tmp_path)
    assert r.n == 0


# ══════════════════════════════════════════════════════════════════════════════════════════════
# 12. CYCLE-7 FINDINGS — guard 2 must hold for EVERY caller, and the cache must actually HIT
# ══════════════════════════════════════════════════════════════════════════════════════════════
def test_read_PROJECTS_a_raw_body_from_an_injected_reader(tmp_path: Path) -> None:
    """⛔ THE HOLE. `read()` stored whatever the reader returned, VERBATIM, so guard 2 held only
    inside the private `_gh_reader`. A caller following the documented `reader=my_reader` usage
    cached a WHOLE body — measured: `pushed_at` and `open_issues_count` served for a 6-hour TTL,
    which is exactly what the `repo.metadata` registry comment says must never happen.

    The reader contract is therefore: supply RAW response bytes. `read()` decodes and projects,
    before the store, on every path.
    """
    raw = json.dumps(
        {
            "id": 1,
            "full_name": "o/r",
            "default_branch": "main",
            "visibility": "private",
            "node_id": "N1",
            "pushed_at": "1999-01-01",
            "open_issues_count": 412,
            "stargazers_count": 7,
        }
    ).encode()
    calls: list[str] = []

    def reader(key: str) -> bytes:
        calls.append(key)
        return raw

    out = _read("repo.metadata", now=1000.0, reader=reader, cache_dir=tmp_path)
    assert b"pushed_at" not in out, f"read() cached a whole body: {out!r}"
    assert b"open_issues_count" not in out
    assert json.loads(out)["full_name"] == "o/r"

    # ...and the HIT is the projection too, not the raw body.
    hit = _read("repo.metadata", now=1001.0, reader=reader, cache_dir=tmp_path)
    assert hit == out
    assert b"pushed_at" not in hit
    assert len(calls) == 1


def test_read_REFUSES_an_error_document_from_an_injected_reader(tmp_path: Path) -> None:
    """Claim 6 has to hold on the library path too, not only inside `_gh_reader`."""

    def reader(key: str) -> bytes:
        return b'{"message": "Server Error"}'

    with pytest.raises(C.Unreadable):
        _read("repo.metadata", now=1000.0, reader=reader, cache_dir=tmp_path)
    assert not list(tmp_path.iterdir()), "an unrecognised body must not be stored"


def test_two_projection_revisions_do_not_thrash_one_cache_file(tmp_path: Path) -> None:
    """⛔ A cache that never HITS is as bad as none.

    The source-bearing digest was in the envelope only, so two checkouts of this file at different
    projection revisions overwrote each other's entry on every access (measured: 8 alternating
    accesses -> 8 live reads, never converging — and `main()` defaults to a box-wide cache dir).
    """
    key = "repo.proj_rev_probe"

    def v1(docs):
        return {"id": 1}

    def v2(docs):
        return {"id": 1, "extra": True}

    C.declare_invariant(key, ttl_s=600, argv=("api", "x"), projection="same-id", project=v1)
    try:
        p1 = C._cache_path(key, SCOPE, ttl_s=600).name
        C.INVARIANTS[key] = C.Invariant(
            ttl_s=600, argv=("api", "x"), projection="same-id", project=v2
        )
        p2 = C._cache_path(key, SCOPE, ttl_s=600).name
        assert p1 != p2, (
            "two revisions of the same projection share one filename, so each overwrites the "
            "other's entry and the cache never hits"
        )
    finally:
        C.INVARIANTS.pop(key, None)


def test_a_hand_registered_mutable_key_is_still_refused() -> None:
    """⛔ `_assert_every_key_projects` checks only the SPEC, never the NAME, so `kind_of`'s
    mutable-name branch is the ONLY gate against a key inserted straight into INVARIANTS. It was
    unpinned: deleting those two lines left the suite green."""
    C.INVARIANTS["pr.state"] = C.Invariant(
        ttl_s=600, argv=("api", "x"), projection="p", project=lambda d: d
    )
    try:
        assert C.kind_of("pr.state") == "mutable"
        r = Counter()
        with pytest.raises(C.MutableReadRefused):
            _read("pr.state", now=1000.0, reader=r, cache_dir=Path("/tmp/nope-unused"))
        assert r.n == 0
    finally:
        C.INVARIANTS.pop("pr.state", None)


def test_a_non_int_ttl_is_refused_not_a_ValueError(tmp_path: Path) -> None:
    """The same conversion hole that was fixed for `now`: a caller mistake must be a
    `TtlNotPermitted` refusal, not a traceback out of `int()`."""
    r = Counter()
    for bad in ("abc", None if False else "1.5", object()):
        with pytest.raises(C.TtlNotPermitted):
            _read("repo.metadata", now=1000.0, reader=r, cache_dir=tmp_path, ttl_s=bad)
    assert r.n == 0


def test_required_set_without_contexts_or_checks_is_refused() -> None:
    """Absent one level DEEPER: `{"strict": false}` names no check list, so reading it as an EMPTY
    required set infers "no required checks" from an absence — on the merge-gating key."""
    for rsc in ({"strict": False}, {}):
        with pytest.raises(C.Unreadable):
            C.INVARIANTS["repo.required_set"].project([{"required_status_checks": rsc}])


def test_required_set_with_only_checks_projects() -> None:
    """The other direction: a real App-based protection sends `checks` and no `contexts`."""
    out = C.INVARIANTS["repo.required_set"].project(
        [{"required_status_checks": {"checks": [{"context": "docs"}]}}]
    )
    assert out == {"contexts": [], "checks": ["docs"]}
