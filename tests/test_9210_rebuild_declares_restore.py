"""#9210 — `rebuild_all`'s SNAPSHOT RESTORE declares itself, not the replay lane.

`rebuild_all` does two unlike things in one body. Its FOLD pass projects
records that came FROM the journal — `_replay_write_scope()` states the truth
for it. But the same engine also puts back, verbatim, state it CAPTURED FROM
THE GRAPH THAT IS ABOUT TO BE DESTROYED: `:Batch` markers and
`Point.batch_id`, `:Session` containers and their `CONTAINS` edges, the
onboarding state machine (node, `onboards` anchor, `COMPLETED_STEP` links), the
`:TeamMeta` first-org marker, the `#2814` config registry, the `#4305` derived
Point properties, and the `:GraphEventMeta` watermark. **None of those writes
is a projection of a journal record** (`#990`, `#4641`, `#5353`, `#2814`,
`#4653` each say so at their writer), so the replay lane's contract — "this
content came from the record" — is FALSE for them. Two legs are exceptions in
MECHANISM only, and the `#3947` one is why it is NOT cited as a writer that
says so: `#4305`'s journal owns `content_hash` and `embedding` when it wrote
them (those are re-applied only where it did not, and the non-derived
`_REPLAY_GAP_PROPS` apply to graph-only ids only), and the `#3947` `:Session`
NODE **is** journal-carried for a post-`#3664` journal as a `SessionRecorded`
record (folded by `_fold_session_recorded`) — only its `CONTAINS` edge and the
re-application of its properties ride no record, and the restore is idempotent
over the journal-carried half. The edge's value still exists only in the
pre-wipe capture, which is the property this lane is about.

The watermark leg is the one whose STATEMENT lives in another module, and it
is claimed here anyway: BOTH rebuild engines hand
`event_store.reestablish_watermark` this lane's reason (`restore_reason=…`),
and the helper opens the lane around its own `MERGE`. That is deliberate — an
inner declaration SHADOWS an outer one, so `#9224`'s landed
`_replay_write_scope()` inside that helper made a `with` at the call site
inert, and the lane has to be stated at the write. `rebuild()` states the SAME
leg (#9210): it too captures the mark off this graph immediately before its own
wipe, and `:GraphEvent` rows are not replayed (`#4664`), so its value is the
carry — the earlier claim that it "re-derives the value from the replayed log"
was false, and is pinned the other way by
`test_the_journal_engine_also_declares_the_watermark_restore` below.

Handing them the replay permit is therefore an over-authorization: the
declaration claims a wider write scope than the restore performs. `#9124`'s
guard makes that legibility defect load-bearing, because a method-wide permit
means every FUTURE addition to that 4,000-line body inherits a claim nobody
made about it — the next lane reads "authorized by replay" and trusts it.

What is pinned here is the DECLARATION, observed **at the write handle** (the
`#9123` precedent): the scope is read at the moment the statement is issued,
so a `with` block that wraps a block the write never runs inside cannot pass.
Three properties, and each one names the wrong behaviour it makes impossible:

1. every DIRECT snapshot-restore write observes its OWN named lane, so a
   restore can no longer be authorized by a claim that is false for it;
2. an OUTER `_replay_write_scope()` cannot cover a restore write — the inner
   declaration shadows it — so the over-broad permit `#9213`/`#9124` introduces
   around the whole engine cannot re-acquire these statements (and the same
   test proves the permit really was in force over the fold, so it cannot pass
   vacuously);
3. the lane does not over-claim in the other direction: nothing observes a
   restore reason unless it IS one of the snapshot-restore statements.

MUTATION for every test here: replace a `self._restore_write(..., reason=...)`
call in `rebuild_all` with the bare `self.g.query(...)` it wraps — tests 1 and
2 both go red on the observed reason (and 2 goes red with `"replay"`, the
false contract, rather than with `None`). The watermark leg's mutation is the
same shape one level down: drop the `restore_reason=` argument at its call
site, and the helper falls back to `#9224`'s `"replay"` — red on the observed
reason, which is how the trampoline this PR removes was caught.

WHAT THIS DOES NOT COVER — the disclosed residual, so it is not read as
covered by the properties above: `rebuild_all` also carries graph-only Points
back by building `synthetic_events` from the same pre-wipe capture and FOLDING
them, so that half is authorized by whatever permit the caller opened over the
engine — `"replay"` under `#9124`/`#9213` — and not by this lane. (The
watermark, which HAD been left outside the pin, is claimed again: its lane is
now handed to the helper and opened inside it, which is what makes the leg
truthful rather than deferred.)

Lane: either. The fixtures build the SDK the way the embedded carve-out suite
does, and the bulk-wipe guard passes on the redirected `test_*_tortoise` graph
too, so this file needs no `carve_out` classification.
"""
from __future__ import annotations

import os

from tortoise.onboarding.state import (
    COMPLETED_STEP_EDGE,
    ONBOARDING_NODE_LABEL,
    ONBOARDING_STEP_LABEL,
    ONBOARDS_EDGE,
)
from tortoise.pack_state import PACK_INSTALL_LABEL
from tortoise.pipeline import PIPELINE_LABEL
from tortoise.projection import (
    _CACHE_WRITE_AUTHORIZED,
    _TEAM_META_LABEL,
    _replay_write_scope,
)
from tortoise.sdk import TortoiseSDK

#: The declared lane, pinned as a LITERAL (`#9123`'s precedent), not imported:
#: a lane that renames itself silently is a lane the next reader cannot find,
#: and a test that imports the name would follow the rename instead of failing
#: on it. `test_the_lane_constant_matches_the_pinned_literal` reconciles the two
#: so the literal cannot drift off the module's constant either.
RESTORE = "pre-wipe-restore"


def _config_registry_parts() -> tuple[tuple[str, ...], tuple[str, ...]]:
    """The config leg's reasons and statement fragments, DERIVED from the registry.

    The restore is ONE code site templated over EVERY class in
    `projection._config_classes()` — `PackInstall(namespace)`,
    `PackManifest(namespace)`, `Pipeline(pipelineKey)`, `PipelineStage(stageKey)`
    and key-scoped `Meta(key)` — each writing under its own reason
    (`config registry {spec.label} (#2814)`) with its own statement.

    Pinning the `PackInstall` literal alone classified ONE of those five arms:
    a snapshot carrying any other class is a restore write that matches no
    fragment, so `test_the_restore_lane_does_not_claim_a_record_fold` reports
    the lane as over-claiming on CORRECT product code — a false RED, and the
    same defect class round 1 fixed for `point-derived` and `onboards`.
    Deriving both halves from the registry means the table cannot go stale: a
    class added there is classified here on the same commit.
    """
    from tortoise.projection import _config_classes

    reasons: list[str] = []
    fragments: list[str] = []
    for spec in _config_classes():
        reasons.append(f"{RESTORE}: config registry {spec.label} (#2814)")
        fragments.append(
            f"MERGE (n:{spec.label} {{{spec.identity_prop}:$identity}}) "
            "SET n += $props")
    return tuple(reasons), tuple(fragments)


_CONFIG_REASONS, _CONFIG_FRAGMENTS = _config_registry_parts()

#: leg → (`reasons` it must carry, statement `fragments` that identify it). ONE
#: entry per snapshot-restore leg `#9210` names; a leg whose statement is
#: templated over a registry (the config registry) carries one reason and one
#: fragment PER class, because the product emits one per class.
_LEGS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "batch-marker": (
        (f"{RESTORE}: :Batch markers (#990)",),
        ("MERGE (b:Batch {id:$id}) SET b += $props",),
    ),
    "batch-link": (
        (f"{RESTORE}: Point.batch_id links (#990)",),
        ("MATCH (p:Point {id:$pid}) SET p.batch_id = $bid",),
    ),
    "session": (
        (f"{RESTORE}: :Session containers (#3947)",),
        ("MERGE (s:Session {id:$id}) SET s += $props",),
    ),
    "session-link": (
        (f"{RESTORE}: :Session CONTAINS links (#3947)",),
        ("MATCH (s:Session {id:$sid}), (p:Point {id:$pid}) "
         "MERGE (s)-[:CONTAINS]->(p)",),
    ),
    "onboarding": (
        (f"{RESTORE}: onboarding state machine (#4641)",),
        (f"MERGE (n:{ONBOARDING_NODE_LABEL} {{org_id:$oid}}) SET n += $props",),
    ),
    "onboarding-onboards": (
        (f"{RESTORE}: onboarding onboards edge (#4641)",),
        (f"MATCH (n:{ONBOARDING_NODE_LABEL} {{org_id:$oid}}), "
         f"(s:Subject {{id:$sid}}) MERGE (n)-[:{ONBOARDS_EDGE}]->(s)",),
    ),
    "onboarding-step": (
        (f"{RESTORE}: onboarding COMPLETED_STEP links (#4641)",),
        (f"MATCH (n:{ONBOARDING_NODE_LABEL} {{org_id:$oid}}) "
         f"MERGE (s:{ONBOARDING_STEP_LABEL} {{org_id:$oid, step_id:$step_id}}) "
         f"MERGE (n)-[:{COMPLETED_STEP_EDGE}]->(s)",),
    ),
    "team-meta": (
        (f"{RESTORE}: :TeamMeta first-org marker (#5353)",),
        (f"MERGE (m:{_TEAM_META_LABEL}) ON CREATE SET m += $props",),
    ),
    "watermark": (
        (f"{RESTORE}: :GraphEventMeta watermark (#4653)",),
        # The statement lives in `event_store.reestablish_watermark` and is
        # issued on this run's handle. The lane is stated by the reason the
        # engine hands that helper, and OPENED inside it — `#9224`'s inner
        # `_replay_write_scope()` is why a declaration at this call site could
        # not survive.
        ("MERGE (m:GraphEventMeta) "
         "ON CREATE SET m.last_seq = $last",),
    ),
    "config": (_CONFIG_REASONS, _CONFIG_FRAGMENTS),
    "point-derived": (
        (f"{RESTORE}: Point derived properties (#4305)",),
        # ARM-AGNOSTIC on purpose: the restore prefixes a
        # `clear_then_set_prefix(...)` guard whenever it carries a vector, so a
        # fragment ending in `SET ` matches only the no-embedding arm and would
        # report a MISSING leg (blaming the fixture) on correct product code.
        # No other statement in the run binds alias `n` with `$pid` — the
        # batch/session links use `p`.
        ("MATCH (n:Point {id:$pid}) ",),
    ),
}


# ── harness ──────────────────────────────────────────────────────────────────


def _mk_sdk(tmp_path):
    events = tmp_path / "events"
    events.mkdir(parents=True, exist_ok=True)
    sdk = TortoiseSDK(
        db_path=str(tmp_path / "rs.db"),
        # A `test_`-prefixed namespace keeps the bulk-wipe guard passing under
        # the docker redirect (graph name becomes `test_..._tortoise`).
        namespace=f"test_rs_{os.urandom(4).hex()}",
        event_log_path=str(events / "events.jsonl"),
    )
    return sdk, events


def _record_authorizations(monkeypatch):
    """Record `(statement, §17.1 authorization IN FORCE)` at the write handle.

    Patched on the CLASS, not the instance: `_GuardedGraph` is
    `__slots__`-restricted and every writer in the engine goes through it, so
    the observation lands exactly where the guard will read it when `#9124`
    closes the third door — not around a caller, where a scope the write never
    runs inside would pass.
    """
    from tortoise.projection import _GuardedGraph

    real = _GuardedGraph.query
    seen: list[tuple[str, str | None]] = []

    def query(self, cypher: str, params=None, timeout=None):
        seen.append((cypher, _CACHE_WRITE_AUTHORIZED.get()))
        return real(self, cypher, params=params, timeout=timeout)

    monkeypatch.setattr(_GuardedGraph, "query", query)
    return seen


def _norm(cypher: str) -> str:
    return " ".join(str(cypher).split())


#: Statement verbs that WRITE. The lane authorizes WRITES, so the "does it
#: over-claim" half is asserted over these only: a helper the restore calls
#: also READS under the scope (`reestablish_watermark` probes `:GraphEvent` and
#: the live counter before its MERGE), and a read under a write declaration is
#: not a write authorized by a claim that does not describe it.
_WRITE_VERBS = ("CREATE", "MERGE", "SET ", "DELETE", "REMOVE", "DROP ")


def _writes(cypher: str) -> bool:
    text = f"{_norm(cypher)} "
    return any(verb in text for verb in _WRITE_VERBS)


def _leg_of(cypher: str) -> str | None:
    """The leg whose statement fragment this is, else None."""
    text = _norm(cypher)
    for leg, (_reasons, fragments) in _LEGS.items():
        if any(_norm(fragment) in text for fragment in fragments):
            return leg
    return None


def _seed_prewipe_state(sdk, events):
    """Plant every class whose ONLY post-wipe carrier is the snapshot.

    Each write is the product's own: a bare `CREATE`/`MERGE` on the write
    handle, riding no journal record — which is the whole reason the class has
    to be captured and restored instead of replayed. The journaled Point is
    what makes the run a REAL `rebuild_all` (the wipe happens and the replay
    re-creates the node the batch link and the CONTAINS edge point at).
    """
    created = sdk.create_point("statement", "journaled, so the replay is real")
    pid = created["id"] if isinstance(created, dict) else created
    # A journaled `:Subject` is the `onboards` edge's endpoint: the replay
    # re-creates it (`SubjectAdded`), so the edge's `MATCH` has something to
    # bind — the restore of a snapshot edge against a minted endpoint would be
    # a different write than the one this test pins.
    subject = sdk.create_subject("org owner")
    sid = subject["id"] if isinstance(subject, dict) else subject
    proj = sdk._get_proj()
    g = proj.g
    g.query("MERGE (b:Batch {id:'batch-1'}) SET b.reason = 'quarantine'")
    # A SECOND marker on purpose: a restore leg is N-per-class (a second
    # `:Batch`, a second step link, a second graph-only Point), so the
    # declaration assert must compare each observation to the reason rather
    # than require exactly one. Without this seed that distinction is
    # untested and an "observed exactly once" assertion would pass here and
    # fire on correct product code in the field.
    g.query("MERGE (b:Batch {id:'batch-2'}) SET b.reason = 'second'")
    g.query("MATCH (p:Point {id:$pid}) SET p.batch_id = 'batch-1'",
            params={"pid": pid})
    g.query("MERGE (s:Session {id:'sess-1'}) SET s.capture_ok = true")
    g.query(
        "MATCH (s:Session {id:'sess-1'}), (p:Point {id:$pid}) "
        "MERGE (s)-[:CONTAINS]->(p)", params={"pid": pid})
    # The onboarding state machine's THREE snapshot-restore writes: the state
    # node, its `onboards` org anchor (needs `org_subject_id`), and the
    # `COMPLETED_STEP` step links (#4641) — the leg whose loss forges a
    # grandfathered completion, so leaving it un-declared is not cosmetic.
    g.query(
        f"MERGE (n:{ONBOARDING_NODE_LABEL} {{org_id:'org-1'}}) "
        "SET n.completed = false, n.org_subject_id = $sid", params={"sid": sid})
    g.query(
        f"MATCH (n:{ONBOARDING_NODE_LABEL} {{org_id:'org-1'}}) "
        f"MERGE (s:{ONBOARDING_STEP_LABEL} "
        "{org_id:'org-1', step_id:'welcome'}) "
        f"MERGE (n)-[:{COMPLETED_STEP_EDGE}]->(s)")
    g.query(f"CREATE (m:{_TEAM_META_LABEL} {{name:'Acme', created:'2026-01-01'}})")
    g.query(
        f"MERGE (n:{PACK_INSTALL_LABEL} {{namespace:'ns-1'}}) SET n.version = 1")
    # A SECOND config class on purpose. The config restore is ONE statement
    # TEMPLATE over every `_config_classes()` label (five today), each with its
    # own reason, so a fixture seeding `:PackInstall` alone exercises one arm
    # of five and lets a label-blind classification pass — which is exactly the
    # false RED (`:Pipeline` restore reported as the lane over-claiming) this
    # table now derives its way out of.
    g.query(
        f"MERGE (n:{PIPELINE_LABEL} {{pipelineKey:'pipe-1'}}) SET n.version = 1")
    g.query("MERGE (m:GraphEventMeta) SET m.last_seq = 7, m.first_seq = 1")
    # The #4305 derived restore: a GRAPH-ONLY `:Point` (no journal record), so
    # the replay cannot re-derive its `content_hash` — that value exists only
    # in the pre-wipe capture, which is what makes it a restore and not a fold.
    g.query(
        "CREATE (p:Point {id:'p-graphonly', content:'graph only', "
        "content_hash:'c0ffee', status:'live'})")
    return pid, proj, events


def _observed_restore_legs(seen):
    """`{leg: [reason, …]}` for every restore leg that actually ran."""
    out: dict[str, list[str | None]] = {}
    for cypher, reason in seen:
        leg = _leg_of(cypher)
        if leg is not None:
            out.setdefault(leg, []).append(reason)
    return out


def _assert_every_leg_declares_itself(seen, *, context: str):
    observed = _observed_restore_legs(seen)
    # VACUITY GUARD: a leg that never ran cannot be evidence either way. The
    # fixture seeds all eleven, so a missing one means the seed or the capture
    # changed, not that the declaration is fine.
    assert set(observed) == set(_LEGS), (
        f"{context}: these snapshot-restore legs did not reach the write "
        f"handle, so the pin would be vacuous: missing="
        f"{sorted(set(_LEGS) - set(observed))}; observed={sorted(observed)}")
    # `any`, not `reasons != [expected]`: a restore loop is N-per-class (a
    # second `:Batch`, a second step link, a second graph-only Point), so
    # "observed exactly once" would fire on correct code and then blame the
    # declaration for it.
    wrong = {
        leg: reasons for leg, reasons in observed.items()
        if any(reason not in _LEGS[leg][0] for reason in reasons)
    }
    assert not wrong, (
        f"{context}: every snapshot-restore write must run inside its OWN "
        "named declaration — the record-replay lane's contract ('this content "
        "came FROM the record') is false for all of them (#9210). "
        f"observed={wrong!r}")
    # The config registry is ONE templated site over FIVE declared labels, so a
    # fixture that reaches a single arm would leave the label-blindness this
    # table removes untested. Two labels observed is what the seed guarantees.
    config_labels = set(observed.get("config") or ())
    assert len(config_labels) >= 2, (
        f"{context}: the config-registry leg must be exercised for MORE than "
        "one declared label, or its label-agnostic classification is "
        f"unproven: observed={sorted(config_labels)}")
    # ONE reason, ONE leg. Two legs sharing a reason would each be acceptable
    # under the other's declaration, so "which writes does the restore "
    # authorize" would stop being answerable per leg — the property the
    # deleted table-only test claimed in its name and could not check.
    by_reason: dict[str, set[str]] = {}
    for leg, reasons in observed.items():
        for reason in reasons:
            if reason is not None:
                by_reason.setdefault(reason, set()).add(leg)
    shared = {r: sorted(legs) for r, legs in by_reason.items() if len(legs) > 1}
    assert not shared, (
        f"{context}: a reason must name ONE restore leg, not several — a "
        f"shared reason makes two legs interchangeable: {shared!r}")


# ── the declaration ──────────────────────────────────────────────────────────


def test_every_snapshot_restore_leg_declares_itself(tmp_path, monkeypatch):
    """RED before #9210: the restore writes ran under NO declaration at all.

    `_CACHE_WRITE_AUTHORIZED` is `None` at these statements on the pre-fix
    tree, which is precisely what `#9124`'s closed door reads as "refuse" — so
    the leg that says "these writes are not record replays" is what keeps the
    restore alive when the door closes, and what stops the answer to "which
    writes are authorized by replay" from being "whatever this method happens
    to contain".
    """
    sdk, events = _mk_sdk(tmp_path)
    try:
        _, proj, _ = _seed_prewipe_state(sdk, events)
        seen = _record_authorizations(monkeypatch)

        proj.rebuild_all(str(events), confirm_destructive=True)

        _assert_every_leg_declares_itself(seen, context="rebuild_all")
    finally:
        sdk.close()


def test_an_outer_replay_permit_cannot_cover_a_restore_write(tmp_path,
                                                             monkeypatch):
    """The over-authorization `#9210` names: a method-wide replay permit.

    `#9124`'s shape opens `_replay_write_scope()` around the WHOLE engine, so
    every statement inside it — the restore legs included — is authorized by a
    contract that is false for the restore. This test opens exactly that
    permit around the rebuild and requires the restore legs to be unaffected:
    the inner declaration shadows the outer one.

    WHY IT CANNOT PASS VACUOUSLY: the same run must observe the outer permit at
    work on the fold (`reason == "replay"` on a statement that is NOT a
    restore leg). Without that half, a `_replay_write_scope()` that never
    reached the handle would let the test pass while proving nothing.

    RED before #9210 with `reason == "replay"` on every restore leg — the false
    contract in the observable — rather than with `None`.
    """
    sdk, events = _mk_sdk(tmp_path)
    try:
        _, proj, _ = _seed_prewipe_state(sdk, events)
        seen = _record_authorizations(monkeypatch)

        with _replay_write_scope():
            proj.rebuild_all(str(events), confirm_destructive=True)

        # The permit was REAL: it reached the handle on the fold.
        permit_on_non_restore = [
            cypher for cypher, reason in seen
            if reason == "replay" and _leg_of(cypher) is None and _writes(cypher)]
        assert permit_on_non_restore, (
            "the outer replay permit never reached the handle, so this test "
            "would be vacuous — it proves nothing about the restore; "
            f"reasons seen={sorted({r for _c, r in seen if r})}")

        _assert_every_leg_declares_itself(
            seen, context="rebuild_all inside an outer _replay_write_scope()")
    finally:
        sdk.close()


def test_the_restore_lane_does_not_claim_a_record_fold(tmp_path, monkeypatch):
    """The mirror defect: a lane that authorizes MORE than it describes.

    A restore declaration that covered the fold would be the same
    over-authorization pointing the other way — the fold's contract ("the
    record came FROM the journal") is true, and stating the restore reason over
    it would make the restore lane answer a question it was not asked. Only
    the statements classified above may observe it.
    """
    sdk, events = _mk_sdk(tmp_path)
    try:
        _, proj, _ = _seed_prewipe_state(sdk, events)
        seen = _record_authorizations(monkeypatch)

        proj.rebuild_all(str(events), confirm_destructive=True)

        misattributed = [
            _norm(cypher)[:120] for cypher, reason in seen
            if isinstance(reason, str) and reason.startswith(RESTORE)
            and _leg_of(cypher) is None and _writes(cypher)]
        assert not misattributed, (
            "the pre-wipe restore lane was in force over a write that is not a "
            f"snapshot restore (#9210): {misattributed}")
    finally:
        sdk.close()


def test_the_journal_engine_also_declares_the_watermark_restore(tmp_path,
                                                              monkeypatch):
    """`rebuild()`'s watermark is a PRE-WIPE CARRY too, so it declares here.

    The sibling engine was documented as "re-deriving the value from the
    replayed log" and therefore as truthfully keeping `#9224`'s `"replay"`. It
    does the opposite: `rebuild()` calls `capture_watermark(self)` before its
    own `DETACH DELETE`, and `:GraphEvent` rows are not replayed (`#4664`), so
    `reestablish_watermark`'s `max(carried, max(:GraphEvent.seq))` normally
    resolves to the carry — there is no replayed value to derive from. A
    captured restore authorized by a lane whose contract is "this content came
    FROM the record" is the same defect #9210 removes, one engine over.

    FAILING VALUE: with the leg dropped at that call site the `MERGE` observes
    `"replay"` (the false contract) instead of the watermark's own reason.
    """
    from tortoise.log import EventLog

    sdk, events = _mk_sdk(tmp_path)
    try:
        _, proj, _ = _seed_prewipe_state(sdk, events)
        seen = _record_authorizations(monkeypatch)

        proj.rebuild(EventLog(str(events / "events.jsonl")),
                     confirm_destructive=True)

        watermark = [reason for cypher, reason in seen
                     if _leg_of(cypher) == "watermark"]
        assert watermark, (
            "the journal engine never issued its watermark MERGE, so this pin "
            "would be vacuous")
        assert all(reason in _LEGS["watermark"][0] for reason in watermark), (
            "rebuild() restores the :GraphEventMeta mark it CAPTURED before "
            "its own wipe, so its lane is this restore leg — not the replay "
            f"lane whose contract is false for it: observed={watermark!r}")
    finally:
        sdk.close()


def test_the_lane_constant_matches_the_pinned_literal():
    """The other half of the pin: the module's constant must spell the lane the
    tests above pinned, so neither can be renamed past the other.

    This is the ONE test here that reaches for the symbol the fix adds; every
    behavioural test above is written against the literal, which is why they
    go red on the OBSERVED AUTHORIZATION (a `None`, and a `"replay"`) on the
    pre-fix tree rather than on an ImportError.
    """
    from tortoise.projection import _PREWIPE_RESTORE_LANE

    assert _PREWIPE_RESTORE_LANE == RESTORE
