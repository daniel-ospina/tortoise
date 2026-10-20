"""#9256 — the declared-lane probe is ATTRIBUTED to the projection that wrote.

The defect this suite pins is a false RED, measured on `tests/test_9079_onboarding_
declared_lane.py` + `tests/test_webhook_delivery.py`: `_GuardedGraph.query` is
patched on the CLASS (the handle is `__slots__`-restricted and read-only, so an
instance patch cannot be installed), so the recorder sees every projection in the
process. A peer projection opening while it was installed —
`hosted_api._acquire_probe_sdk` does that from `monitoring._loop` — contributes
its own `_ensure_indexes` burst, correctly declared under `schema-ddl` for THAT
projection, and without handle identity those statements were attributed to the
lane under test:

    E onboarding writes reached the cache handle OUTSIDE any §17.1 declaration:
      CREATE INDEX FOR (n:Point) ON (n.id)      ← no onboarding writer issues this
    E a statement declared as `onboarding.metadata` writes a class the
      declaration does not name: ['Event', 'Meta', 'Object', 'Point', …]

`tests/_declared_lane_probe.py` fixes it by recording the projection object and
letting a suite read `probe.own()`. This suite is where THAT rule is pinned, in
both directions — the attribution, and the blindness the attribution must not
buy:

* the two views (`own()` / `foreign()`) really do partition by handle identity,
  and the foreign view is NOT empty (a filter that excluded everything, or
  nothing, would pass an attribution assertion vacuously);
* `refuse` still applies to a FOREIGN journal-backed handle, so an undeclared
  write from a peer projection is still refused loudly — scoping the refusal to
  the owner would turn the instrument into the blindfold #9256 is about;
* a probe constructed with no owner is refused outright, so the defect cannot be
  reintroduced by omitting the argument.
"""
from __future__ import annotations

import pytest

from tests._declared_lane_probe import LaneProbe
from tortoise.projection import DirectCacheWriteRefused
from tortoise.sdk import TortoiseSDK


@pytest.fixture
def two_journal_backed_sdks(tmp_path):
    """An `owner` SDK and a SECOND, unrelated one — both §17.1 journal-backed.

    Two real projections, not fakes: the foreign one's `_ensure_indexes` burst
    (`CREATE INDEX FOR (n:Point) …`) is exactly the traffic that reddened the
    onboarding suite, and it only exists if the projection really opens.
    """
    owner = TortoiseSDK(str(tmp_path / "owner.db"),
                        event_log_path=str(tmp_path / "owner-journal.jsonl"))
    other = TortoiseSDK(str(tmp_path / "other.db"),
                        event_log_path=str(tmp_path / "other-journal.jsonl"))
    try:
        yield owner, other
    finally:
        other.close()
        owner.close()


def test_a_foreign_projections_statements_are_not_attributed_to_the_lane(
        two_journal_backed_sdks, monkeypatch):
    """The #9256 regression: the peer projection's DDL is recorded, never claimed.

    MUTATION: give `LaneProbe.own()` the whole record list (the pre-fix
    behaviour) and this fails — `CREATE INDEX FOR (n:Point) …` appears in the
    lane's own statements, and a class check over them then reds on `Point`.
    """
    owner_sdk, other_sdk = two_journal_backed_sdks
    owner = owner_sdk._get_proj()
    probe = LaneProbe(owner).install(monkeypatch, refuse=False)

    other = other_sdk._get_proj()
    assert other is not owner, (
        "the fixture returned one projection twice, so nothing here would be "
        "foreign — the attribution test would be vacuous"
    )
    owner.g.query("MATCH (n:OnboardingState) RETURN n.org_id")

    own_statements = [statement for statement, _r, _w in probe.own()]
    assert own_statements == ["MATCH (n:OnboardingState) RETURN n.org_id"], (
        "the lane's own view carries statements another projection issued: "
        f"{own_statements}"
    )


def test_the_foreign_view_is_not_empty_so_the_attribution_is_not_a_blindfold(
        two_journal_backed_sdks, monkeypatch):
    """An empty `foreign()` would make the previous test pass for the wrong reason.

    The peer projection's `_ensure_indexes` burst must actually be in the
    record set (the instrument is class-wide, and must stay that way), and it
    must be attributable to the peer's handle — not merely absent from `own()`.
    """
    owner_sdk, other_sdk = two_journal_backed_sdks
    owner = owner_sdk._get_proj()
    probe = LaneProbe(owner).install(monkeypatch, refuse=False)

    other = other_sdk._get_proj()
    other_scale = [statement for statement, _r, _w in probe.foreign()
                   if "CREATE INDEX FOR (n:Point)" in statement]
    assert other_scale, (
        "the peer projection issued no index DDL through the handle, so this "
        "pin proves nothing about a foreign write being seen: "
        f"{[s for s, _r, _w in probe.foreign()][:5]}"
    )
    assert probe.own() == [], (
        "the owner issued nothing, so its view must be empty: "
        f"{probe.own()}"
    )
    for record in probe.foreign_records():
        assert record.owner is other, (
            "a foreign record names a handle that is neither the owner nor the "
            "peer projection — identity, not shape, is what this partition is"
        )


def test_the_guard_still_refuses_a_foreign_undeclared_write(
        two_journal_backed_sdks, monkeypatch):
    """`refuse` is the GUARD's predicate, so it is not scoped to the owner.

    A peer projection issuing a mutating statement with no declaration in flight
    is a real §17.1 violation wherever it comes from: the instrument must raise
    for it exactly as the product guard would. Scoping the refusal to `owner`
    would hide a peer lane's undeclared write behind a suite that looks green.
    """
    owner_sdk, other_sdk = two_journal_backed_sdks
    owner = owner_sdk._get_proj()
    probe = LaneProbe(owner).install(monkeypatch, refuse=True)
    other = other_sdk._get_proj()

    with pytest.raises(DirectCacheWriteRefused):
        other.g.query("CREATE (x:Undeclared9256 {id: 'x'})")

    undeclared = probe.foreign_undeclared()
    assert [r.text for r in undeclared] == ["CREATE (x:Undeclared9256 {id: 'x'})"], (
        "the refusal fired but the record does not name the foreign, undeclared "
        f"statement — a failure message could not tell the two lanes apart: "
        f"{[r.text for r in undeclared]}"
    )
    assert probe.own() == [], (
        "the refused statement was attributed to this lane: "
        f"{probe.own()}"
    )


def test_a_probe_with_no_owner_is_refused():
    """The #9256 defect must not be reachable by OMITTING the argument.

    An owner-less probe is a recorder that cannot answer "whose statement was
    this?" — which is the defect itself, not a convenience default.
    """
    with pytest.raises(ValueError, match="#9256"):
        LaneProbe(None)
