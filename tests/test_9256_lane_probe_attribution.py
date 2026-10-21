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
  reintroduced by omitting the argument;
* the CENSUS half survives the attribution: `assert_no_foreign_undeclared()`
  stays green on the peer's DECLARED `_ensure_indexes` burst (the traffic that
  produced the false red) and still reds on a peer's UNDECLARED write, so
  scoping `own()` cannot filter a real §17.1 violation into silence;
* and that census applies §17.1's THIRD clause too — an UNARMED (not
  journal-backed) peer is outside the rule, so its undeclared write must stay
  GREEN rather than red the whole-process census (the same arming clause the
  refusal path already applies);
* the write CLASSIFIER reads the RAW statement, as the product guard does — a
  `//` line comment must not swallow the write after it once the text is
  whitespace-normalized for the record (fail-open in the armed door).
"""
from __future__ import annotations

import pytest

from tests._declared_lane_probe import (
    LaneProbe,
    assert_no_foreign_undeclared,
)
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


def test_the_census_helper_reds_on_a_foreign_undeclared_write_not_on_its_ddl(
        two_journal_backed_sdks, monkeypatch):
    """Attribution must not buy silence: the census half of #9256.

    Scoping an assertion to `own()` answers "whose statement is this?" — never
    "was it declared?". A peer projection's UNDECLARED write has no declaration
    to attribute to anyone, so it is a §17.1 violation however the census is
    scoped, and a suite that filters it away goes green on exactly the class
    #9256 asked it to keep loud (the wild peer is product code).

    MUTATION: delete `assert_no_foreign_undeclared()` from an adopting census
    test and the peer's undeclared write below is recorded, excluded from
    `own()`, and the suite passes.
    """
    owner_sdk, other_sdk = two_journal_backed_sdks
    owner = owner_sdk._get_proj()
    probe = LaneProbe(owner).install(monkeypatch, refuse=False)
    other = other_sdk._get_proj()

    # The peer's own `_ensure_indexes` burst is DECLARED (`schema-ddl`) — the
    # traffic that produced the #9256 false red — so the census stays GREEN on
    # it. A helper that reddened here would be the false red all over again.
    assert probe.foreign(), (
        "the peer contributed no statement, so the green half of this pin is "
        "vacuous"
    )
    assert_no_foreign_undeclared(probe)

    # ... and an undeclared write on the peer's handle must RED, not be
    # filtered into silence by the attribution.
    other.g.query("CREATE (x:PeerUndeclared9256 {id: 'x'})")
    with pytest.raises(AssertionError, match=r"NO §17\.1 declaration"):
        assert_no_foreign_undeclared(probe)


def test_the_census_does_not_red_on_an_unarmed_peer_because_17_1_does_not_apply(
        two_journal_backed_sdks, tmp_path, monkeypatch):
    """§17.1 has THREE clauses, and the census must apply all three.

    The product guard MEASURES and refuses only on a journal-backed projection
    — `_GuardedGraph._refuse_unauthorized_cache_write` returns early on
    `not getattr(self._proj, "_journal_backed", False)` because an unbound
    projection has no journal to be the single writer OF. A census predicate of
    `writes and scope is None` alone therefore reports a "§17.1 violation" on a
    handle the rule explicitly exempts, and arming is a property of the
    ENVIRONMENT (`hosted_api._make_sdk` passes `event_log_path=None` unless
    `TORTOISE_EVENT_LOG_BASE_DIR` is set), not of the test.

    This is the mirror of `test_the_guard_still_refuses_a_foreign_undeclared_
    write`: the refusal is already unarmed-correct, so the census must agree
    with it rather than contradict it.

    MUTATION: drop the arming clause from `foreign_undeclared()` and the
    assertion below reds on a peer §17.1 does not reach.
    """
    owner_sdk, _other = two_journal_backed_sdks
    owner = owner_sdk._get_proj()
    probe = LaneProbe(owner).install(monkeypatch, refuse=False)

    unarmed = TortoiseSDK(str(tmp_path / "unarmed.db"))  # no event_log_path
    try:
        peer = unarmed._get_proj()
        assert peer is not owner
        assert peer._journal_backed is False, (
            "the peer is journal-backed, so this pin proves nothing about an "
            "unarmed handle — §17.1 would apply to it after all"
        )
        peer.g.query("CREATE (x:Unarmed9256 {id: 'x'})")

        # NON-VACUITY: the write really was recorded, undeclared, and attributed
        # to the unarmed peer — the census is not green because it saw nothing.
        recorded = [r for r in probe.foreign_records()
                    if "Unarmed9256" in r.text]
        assert recorded, (
            "the unarmed peer's write never reached the class-wide instrument, "
            "so the green assertion below is vacuous: "
            f"{[r.text[:60] for r in probe.foreign_records()]}"
        )
        assert recorded[0].writes is True and recorded[0].scope is None, (
            f"the record is not the undeclared write this pin needs: "
            f"{recorded[0].as_tuple()}"
        )

        assert probe.foreign_undeclared() == [], (
            "§17.1 does not apply to an unarmed projection, so the census must "
            "not report its write as a violation: "
            f"{[(r.text[:60], r.scope) for r in probe.foreign_undeclared()]}"
        )
        assert_no_foreign_undeclared(probe)
    finally:
        unarmed.close()


def test_the_classifier_reads_the_raw_statement_not_the_normalized_one(
        two_journal_backed_sdks, monkeypatch):
    """A `//` comment must not swallow the write that follows it.

    `_statement_writes` strips `//[^\\n]*`, and the product guard classifies the
    RAW statement. Normalizing whitespace FIRST turns the newline into a space,
    so the comment eats the rest of the statement: `SET` disappears, the record
    says `writes=False`, the statement drops out of every `if w` census filter
    AND is never refused by the armed door — fail-open in the one direction this
    instrument exists for.

    MUTATION: classify `text` (the normalized string) instead of `cypher` and
    nothing is raised here.
    """
    owner_sdk, _other = two_journal_backed_sdks
    owner = owner_sdk._get_proj()
    probe = LaneProbe(owner).install(monkeypatch, refuse=True)

    statement = "MATCH (n:Point) // a note\nSET n.commented9256 = true"
    with pytest.raises(DirectCacheWriteRefused):
        owner.g.query(statement)

    record = probe.own_records()[-1]
    assert record.writes is True, (
        "the write after the `//` comment was classified as a READ, so it is "
        "invisible to every census filter and to the armed door: "
        f"{record.as_tuple()}"
    )
    assert "\n" not in record.text, (
        f"the RECORD must stay whitespace-normalized: {record.text!r}"
    )
