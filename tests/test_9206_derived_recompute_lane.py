"""#9206 — the DERIVED-RECOMPUTE lane: a value RE-DERIVED, never remembered.

§17.1 authorizes four classes of write at the projection's write handle: the
live doors (record appended first), the replay engines (the record came FROM
the journal), and the two restore lanes for state captured from this graph
before a wipe. Arming the guard for #9206's acceptance battery surfaced a fifth
shape the SDK reaches through its own code, and the honest name for it is not
"live" and not "replay":

    `tortoise/source_identity.py::resolve_source_key` adopts a PRE-canonical
    ``:Source`` on touch — ``SET s.canonicalUrl, s.urlAliases`` — with NO record
    of any kind. The value is ``normalize_source_url(url)``, a pure function of
    the ``url`` that is the node's own journaled MERGE key, and the REPLAY lane
    re-derives both fields from it when it mints or folds the Source
    (`_mint_source_stub` / `_upsert_source`). Pinned where that class already
    lives: ``tests/test_source_rematerialisation_5024.py`` asserts the rebuilt
    Point's ``canonicalUrl`` equals the live node's.

So the write needs a lane of its own, and the lane must be exactly as narrow as
that property. This suite is its acceptance gate:

1. the resolver's write reaches the handle under the declared reason;
2. NOTHING is appended for it — the "never remembered" half, measured on the
   journal itself rather than asserted;
3. it DEFERS to an outer lane instead of rebranding it (the nested-no-op rule
   that keeps the fallback from describing a replay as a recompute);
4. it is not a bypass: an undeclared write through the same handle is STILL
   refused, and the lane is not reachable from anywhere but the resolver.

⛔ WHAT IS NOT CLAIMED. ``urlAliases`` accumulates raw spellings the resolver
has SEEN, so a variant seen only live is not restored by replay. That is
disclosed at the lane's definition (the alias list is a dedup aid re-derived
from each ref the journal carries, not a fact ``canonicalUrl`` depends on), and
``tools/source_dedup_report.py --backfill`` is the path that repairs a corpus
whose aliases matter (#5012 S0a).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tortoise.projection import (
    _CACHE_WRITE_AUTHORIZED,
    DirectCacheWriteRefused,
    _GuardedGraph,
    _live_write_scope,
    _test_fixture_write_scope,
)
from tortoise.source_identity import normalize_source_url, resolve_source_key

#: The reason prefix the lane pins (a literal in the projection module, read
#: back here so a silent rename is a failure rather than a new spelling).
_LANE = "derived-recompute"

#: The one statement the resolver issues to adopt a pre-canonical node.
_ADOPT_STATEMENT = "MATCH (s:Source {url: $u}) WHERE s.canonicalUrl IS NULL"


# ── harness ─────────────────────────────────────────────────────────────────


@pytest.fixture
def enforced(monkeypatch):
    """Arm §17.1's third door for one test (part 2's state, deliberately).

    The landed constant stays `False` (#9124 part 2 flips it); a test that
    asserts authorization or refusal flips it on the module the handle reads at
    call time — the same shape `tests/test_9079_handle_write_authority.py` uses,
    so there is one way to arm it in a test.
    """
    import tortoise.projection as projection

    monkeypatch.setattr(projection, "_HANDLE_GUARD_ENFORCES", True)
    return projection


@pytest.fixture
def armed_sdk(tmp_path):
    """A real SDK with a JSONL journal, so the projection is journal-backed.

    `event_log_path` is what arms §17.1 at the handle (`_get_proj` ->
    `_bind_journal`); an unarmed projection is outside §17.1 and every refusal
    assertion here would be vacuous.
    """
    from tortoise.sdk import TortoiseSDK

    journal = tmp_path / "journal.jsonl"
    sdk = TortoiseSDK(str(tmp_path / "recompute.db"),
                      event_log_path=str(journal))
    sdk._journal_path_for_test = journal
    try:
        yield sdk
    finally:
        sdk.close()


@pytest.fixture
def authorizations(monkeypatch):
    """Record `(statement, §17.1 authorization IN FORCE)` at the write handle.

    Patched on the CLASS: `_GuardedGraph` is `__slots__`-restricted and is the
    single handle every projection write passes through, so the observation is
    taken where the guard itself reads the authorization — not around a caller,
    where a scope the write never runs inside would pass.
    """
    real = _GuardedGraph.query
    seen: list[tuple[str, str | None]] = []

    def query(self, cypher: str, params=None, timeout=None):
        seen.append((" ".join(str(cypher).split()), _CACHE_WRITE_AUTHORIZED.get()))
        return real(self, cypher, params=params, timeout=timeout)

    monkeypatch.setattr(_GuardedGraph, "query", query)
    return seen


def _seed_pre_canonical_source(sdk, url: str) -> None:
    """Mint a Source the way the pre-#5012 corpus stores one — `canonicalUrl`
    NULL — so the resolver's adopt-on-touch actually fires.

    The write is a FIXTURE, declared on the TEST-FIXTURE lane: it appends no
    record and states nothing about the product. (`test_source_dedup_
    registration.py` issues the same statement bare, which is fine only while
    the handle is measuring.) ⚠️ It used to be spelled `_live_write_scope`,
    whose whole claim is "the record is appended FIRST" — the one lane this
    suite exists to say is NOT the lane for an unjournalled write, so a fixture
    taking that name was the copyable template #9206 review P2 rules out.
    """
    with _test_fixture_write_scope("9206-pre-canonical-source"):
        sdk._get_proj().g.query(
            "MERGE (s:Source {url:$url}) ON CREATE SET s.id=$url, "
            "s.sourceKind='document', s.version=1",
            params={"url": url},
        )


def _source_rows(sdk) -> list[list]:
    return sdk._get_proj().g.query(
        "MATCH (s:Source) RETURN s.url, s.canonicalUrl, s.urlAliases"
    ).result_set


# ── 1. the resolver's write is DECLARED ─────────────────────────────────────


def test_the_resolver_declares_its_recompute_lane_at_the_handle(
        armed_sdk, enforced, authorizations):
    """The adopt-on-touch statement reaches the handle under the declared lane.

    Without the declaration this is a `DirectCacheWriteRefused` on an armed
    projection — measured while closing #9206's acceptance battery: it was the
    4th of the 5 remaining refusals, and it refused a plain
    `create_point(extractedFrom=…)` of a variant URL.
    """
    raw = "https://legacy.example.com/x/?utm_source=old"
    canonical = normalize_source_url(raw)
    _seed_pre_canonical_source(armed_sdk, raw)
    assert _source_rows(armed_sdk)[0][1] is None, (
        "fixture precondition: the node must be PRE-canonical, or the adoption "
        "never fires and this test proves nothing")

    authorizations.clear()
    adopted = resolve_source_key(armed_sdk._get_proj().g, raw)

    assert adopted == raw, (
        "the resolver returns the stored url key it adopted")
    rows = _source_rows(armed_sdk)
    assert len(rows) == 1, rows
    assert rows[0][1] == canonical, (
        "the adoption must have LANDED: `canonicalUrl` is the value the lane "
        f"authorizes; rows={rows!r}")

    seen = [(stmt, reason) for stmt, reason in authorizations
            if stmt.startswith(_ADOPT_STATEMENT)]
    assert seen, (
        "the adopt-on-touch statement never reached the handle — either the "
        "resolver stopped adopting or this observation moved; both make the "
        "test vacuous rather than green")
    for stmt, reason in seen:
        assert reason is not None, (
            "an armed handle saw the adoption with NO lane — the #9206 defect, "
            f"one door over: {stmt}")
        assert reason.startswith(f"{_LANE}: "), (
            f"the adoption must declare the recompute lane, got {reason!r}")
        assert "source_identity.resolve_source_key" in reason, (
            "the lane must name the writer that took it, so the next reader "
            f"finds the site from the reason alone; got {reason!r}")


# ── 2. the lane covers a write NOTHING is appended for ──────────────────────


def test_the_adoption_appends_no_record_because_the_value_is_rederived(
        armed_sdk, enforced, tmp_path):
    """The "never remembered" half, measured on the journal, not asserted.

    A lane that sanctioned an unjournalled write of RECOVERABLE state is the
    #9068 defect. The distinguishing property here is that the value is a pure
    function of journal-carried input, so this test pins BOTH halves: the
    adoption writes the node AND appends nothing — which is only honest because
    the replay lane re-derives the same fields (the parity
    `tests/test_source_rematerialisation_5024.py` already asserts).
    """
    raw = "https://legacy.example.com/y/?utm_source=old"
    _seed_pre_canonical_source(armed_sdk, raw)
    journal = Path(armed_sdk._journal_path_for_test)
    before = journal.read_text() if journal.exists() else ""

    resolve_source_key(armed_sdk._get_proj().g, raw)

    after = journal.read_text() if journal.exists() else ""
    assert _source_rows(armed_sdk)[0][1] == normalize_source_url(raw), (
        "precondition: the adoption must have written, or 'appends nothing' "
        "is trivially true")
    assert after == before, (
        "the adoption appended a record: "
        f"{after[len(before):]!r} — if this write acquires a record then the "
        "derived-recompute lane is the wrong lane for it (a recorded write "
        "belongs on `_live_write_scope`, append first), and the lane's "
        "docstring says so")


# ── 3. the lane DEFERS, it never rebrands an outer claim ────────────────────


def test_the_lane_defers_to_an_outer_lane_instead_of_rebranding_it(
        armed_sdk, enforced, authorizations):
    """Nested-no-op: an outer declaration keeps its own reason.

    The pre-wipe lane SHADOWS on purpose; this one must not, because the
    statement is already authorized by the outer claim and a recompute label
    would be the weaker description of the SAME write. If this ever shadows, a
    replay's adoption would report itself as a recompute and the "which writes
    are authorized by replay" question would be answered wrongly.
    """
    raw = "https://legacy.example.com/z/?utm_source=old"
    _seed_pre_canonical_source(armed_sdk, raw)
    authorizations.clear()

    with _live_write_scope("outer-claim-under-test"):
        resolve_source_key(armed_sdk._get_proj().g, raw)

    seen = [reason for stmt, reason in authorizations
            if stmt.startswith(_ADOPT_STATEMENT)]
    assert seen, "the adoption never reached the handle"
    # The resolver probes BOTH spellings (the inbound raw url and the canonical
    # one), so one adoption can be two statements — the reason is the property
    # under test, not the count.
    assert set(seen) == {"outer-claim-under-test"}, (
        "the recompute lane must DEFER to a lane already in flight, never "
        f"replace its reason; got {seen!r}")


# ── 4. the lane is not a bypass ─────────────────────────────────────────────


def test_an_undeclared_write_through_the_same_handle_is_still_refused(
        armed_sdk, enforced):
    """The declaration lives at the resolver, not on the statements it resembles.

    A guard that stopped refusing because a lane exists somewhere would be the
    regression this whole programme exists to prevent.
    """
    raw = "https://legacy.example.com/w/?utm_source=old"
    _seed_pre_canonical_source(armed_sdk, raw)

    with pytest.raises(DirectCacheWriteRefused):
        armed_sdk._get_proj().g.query(
            "MATCH (s:Source {url:$u}) SET s.canonicalUrl='bypass'",
            params={"u": raw},
        )

    assert _source_rows(armed_sdk)[0][1] is None, (
        "the refused write must not have landed")


def test_the_recompute_lane_has_exactly_one_writer_in_the_package():
    """The lane's blast radius is pinned by a source scan, not by intent.

    §17.1's lanes are a small closed set and each one is a claim about a
    specific write class; a fallback lane is exactly the kind that grows if
    nobody counts. This is a census, so it is written against the SOURCE (a
    grep the next reader can repeat), and it names the writers it expects.

    ⛔ `tools/` IS IN SCOPE (#9206 review P2): the lane's own disclosure points
    operators at `tools/source_dedup_report.py --backfill`, and that tool issues
    the SAME statement — so it is a writer of this class, and a census bounded
    to `tortoise/` could not see it while the arming this PR prepares would
    refuse the repair path it recommends.
    """
    root = Path(__file__).resolve().parent.parent
    writers: dict[str, int] = {}
    for sub in ("tortoise", "tools"):
        for path in sorted((root / sub).rglob("*.py")):
            text = path.read_text(encoding="utf-8", errors="replace")
            n = text.count("_derived_recompute_write_scope(")
            if n:
                writers[str(path.relative_to(root))] = n
    assert writers == {
        "tortoise/projection/__init__.py": 1,      # the definition
        "tortoise/source_identity.py": 1,          # the resolver's adoption
        "tools/source_dedup_report.py": 1,         # the named repair path
    }, (
        "the derived-recompute lane is used somewhere new — check that the "
        "write is a PURE FUNCTION of journal-carried input and appends "
        f"nothing, then extend this census; got {writers!r}")
