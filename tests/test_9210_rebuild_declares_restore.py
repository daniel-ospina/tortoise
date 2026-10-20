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
is claimed here anyway: `rebuild_all` hands `event_store.reestablish_watermark`
its own reason (`restore_reason=…`), and the helper opens the lane around its
own `MERGE`. That is deliberate — an inner declaration SHADOWS an outer one, so
`#9224`'s landed `_replay_write_scope()` inside that helper made a `with` at
this call site inert, and the lane has to be stated at the write. The helper's
other caller (`rebuild()`) passes nothing and keeps `#9224`'s `"replay"`.
That is a DEFERRED instance of the same defect, disclosed rather than claimed
truthful: `rebuild()` also puts back a value it CAPTURED from the graph before
its own wipe (`capture_watermark`), so the replay lane's contract is false for
it too — and a blanket restore reason would be false in the OTHER direction,
because `reestablish_watermark` stores `max(carried, max(:GraphEvent.seq))`, so
the stored `last_seq` can be the replayed seq when that is higher. The two
sources have to be split before one lane can describe that write, which is why
`#9213` owns it and why
`test_the_journal_engines_watermark_stays_disclosed_as_deferred` measures the
deferral instead of trusting a sentence about it.

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

`#9210` has TWO over-authorized legs, and this file pins BOTH. The second is
the `#548` SYNTHETIC fold: `rebuild_all` carries graph-only Points back by
building `PointAdded`/`OperatorAdded` events from the same pre-wipe capture and
FOLDING them, so their writes reached the handle under the replay permit too —
the same false contract, a different mechanism. That leg declares
`pre-wipe-synthetic:` (selected from the fold's own provenance marker, in BOTH
passes that write for a creation), and
`test_the_synthetic_fold_declares_its_own_lane` /
`test_an_outer_replay_permit_cannot_cover_the_synthetic_fold` hold it to the
same three properties above.

MUTATION for every test here: replace a `self._restore_write(..., reason=...)`
call in `rebuild_all` with the bare `self.g.query(...)` it wraps — tests 1 and
2 both go red on the observed reason (and 2 goes red with `"replay"`, the
false contract, rather than with `None`). The watermark leg's mutation is the
same shape one level down: drop the `restore_reason=` argument at its call
site, and the helper falls back to `#9224`'s `"replay"` — red on the observed
reason, which is how the trampoline this PR removes was caught. The synthetic
leg mutates per PASS: delete either `with _fold_lane_for_event(seq, event_source):` wrapper and the statements of that pass fall back to
`"replay"` (measured separately for pass 1a and pass 2 — one unwrapped pass
does not hide behind the other).

WHAT THIS DOES NOT COVER — the disclosed residual, so it is not read as
covered by the properties above. TWO things, and the second is not bounded:

1. writes an edit adds INSIDE `event_store.reestablish_watermark` inherit the
   lane their caller stated, rather than the funnel's required-`reason`
   contract. (The watermark, which HAD been left outside the pin, is claimed
   again: its lane is now handed to the helper and opened inside it, which is
   what makes the leg truthful rather than deferred.)
2. **the funnel is VOLUNTARY** — nothing makes `_rebuild_all_replay` route a
   restore statement through `_restore_write`. A twelfth restore added as a
   bare `self.g.query(...)` inherits the engine's method-wide `"replay"`, and
   `_LEGS` cannot see it (a table of the eleven statements that exist says
   nothing about a twelfth). What the pin DOES have, one pin below, is a
   TABLE-INDEPENDENT half: a bare restore written in the whole-property-map
   shape every restore leg uses (`… SET <alias> += $props`) is caught by
   SHAPE, and `test_a_bare_restore_write_in_the_map_shape_is_caught` proves
   that half bites by running the engine with the funnel bypassed. The other
   restore shapes — a `SET <alias>.<field> = $param` link, a bare edge
   `MERGE` — are caught only while they stay in the table. Closing the gap
   for all shapes is `#9124`'s handle guard armed, not a test that claims to
   have done it.

AND ONE WRITE THAT IS NOT A RESTORE AT ALL, which is disclosed here because
it is the mirror of the residual above rather than a part of it: the same
engine also writes the sticky `config_reset` marker
(`set_config_reset_marker()`, `#2814`) on the post-restore config-verification
FAILURE path, and that write carries the engine's `"replay"` — correctly,
since its props are AUTHORED at write time, not captured before the wipe. It
is therefore NOT a `pre-wipe-restore:` leg and must not be judged as an
undeclared one, which is why `_NOT_A_RESTORE_STATEMENTS` exempts it
statement-exactly instead of the rule reddening on correct product code.

Lane: either. The fixtures build the SDK the way the embedded carve-out suite
does, and the bulk-wipe guard passes on the redirected `test_*_tortoise` graph
too, so this file needs no `carve_out` classification.
"""
from __future__ import annotations

import os
import re

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
#: on it. `test_the_lane_constants_match_the_pinned_literals` reconciles the two
#: so the literal cannot drift off the module's constant either.
RESTORE = "pre-wipe-restore"

#: The SYNTHETIC fold's lane (`#9210`'s SECOND over-authorized leg): the `#548`
#: graph-only `:Point`/operator rows are FOLDED back from the pre-wipe capture,
#: so their writes carry the same claim the restore legs do (the content came
#: from THIS graph's capture, not from a record) under their own name —
#: otherwise they inherit the replay permit, whose contract is false for them.
SYNTHETIC = "pre-wipe-synthetic"

#: The graph-only ids `_seed_prewipe_state` plants: no journal record describes
#: any of them, so every write their event drives in a rebuild is a SYNTHETIC
#: fold. `p-graphonly` carries only declared props (pass 1a's node MERGE), the
#: `-extra` one an open-set prop (pass 1a's `SET n += $extra`), and `-op` an
#: `IMPL` edge (PASS 2's `_upsert_point_edges`) — the three writes the pin needs
#: to see, one per pass and one per write shape.
_GRAPH_ONLY_IDS = frozenset({
    "p-graphonly", "p-graphonly-extra", "p-graphonly-op"})


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


def _record_writes_with_params(monkeypatch):
    """Record `(statement, lane, params)` for every statement at the handle.

    The synthetic pin needs the PARAMS, not just the statement: the fold writes
    one statement template per class and the only way to say "this write was
    driven by a graph-only payload" is the id it binds. Same observation point
    as `_record_authorizations` — the CLASS (`_GuardedGraph`), where the guard
    will read it — kept separate so the existing tests' 2-tuples stay as they
    are rather than re-shaping six call sites for one new assertion.
    """
    from tortoise.projection import _GuardedGraph

    real = _GuardedGraph.query
    seen: list[tuple[str, str | None, dict | None]] = []

    def query(self, cypher: str, params=None, timeout=None):
        seen.append((cypher, _CACHE_WRITE_AUTHORIZED.get(), params))
        return real(self, cypher, params=params, timeout=timeout)

    monkeypatch.setattr(_GuardedGraph, "query", query)
    return seen


def _bound_ids(params) -> frozenset[str]:
    """Every scalar a statement's params bind, stringified (ids land here)."""
    out: set[str] = set()

    def _walk(value):
        if isinstance(value, dict):
            for item in value.values():
                _walk(item)
        elif isinstance(value, (list, tuple, set, frozenset)):
            for item in value:
                _walk(item)
        else:
            out.add(str(value))

    for value in (params or {}).values():
        _walk(value)
    return frozenset(out)


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


#: The whole-property-map ASSIGNMENT shape every restore leg uses to put a
#: captured property set back (`MERGE (b:Batch {id:$id}) SET b += $props`,
#: `MERGE (n:PackInstall {namespace:$identity}) SET n += $props`,
#: `MERGE (m:TeamMeta) ON CREATE SET m += $props`). Matched WITHOUT the leg
#: table on purpose: `#9210`'s residual is that the funnel is VOLUNTARY, so a
#: pin that only knows the eleven classified statements cannot see a twelfth.
#: MEASURED over a full `rebuild_all` run on a graph whose config restore
#: SUCCEEDS: this shape matches the restore statements and nothing else — the
#: journal fold's own map writes bind `$attrs` (`projection/__init__.py`'s two
#: `SET r += $attrs` folds), not `$props` — which is what lets it be a RULE
#: here instead of one more table. It is not TRUE of the whole module, and the
#: difference is named rather than tolerated: see
#: `_NOT_A_RESTORE_STATEMENTS` below, which is the shape-matching write
#: `_rebuild_all_replay` CAN emit on a path this fixture never enters.
_RESTORE_MAP_SHAPE = re.compile(r"\bSET \w+ \+= \$props\b")

#: Writes that MATCH the restore MAP SHAPE and are NOT snapshot restores, so the
#: shape rule must not judge them. Keyed on the WHOLE normalized statement
#: (never a fragment): a fragment here would exempt a real restore leg that
#: happened to share a prefix — a silent hole pointing the other way, which is
#: the one direction this rule exists to avoid.
#:
#: ONE statement, two writers, both `:Meta` known-hot/state markers a rebuild
#: AUTHORS at write time — `set_config_reset_marker()` (`#2814`, the sticky
#: `config_reset` marker) and `residency.declare_residency` (reached through
#: `cache_projection.write_declaration`, `#7952`). Neither puts back a captured
#: property set: `set_config_reset_marker` builds its props from
#: `_config_reset_props(read_config_reset(g), reason)` when it is called, so
#: declaring it `pre-wipe-restore:` would make the lane's contract false in the
#: other direction, and it is NOT the restore of a captured `config_reset` node
#: (that one rides the config leg, whose statement binds `$identity`).
#: REACHABILITY: the `config_reset` write is issued from INSIDE
#: `_rebuild_all_replay` (`projection/__init__.py`, the post-restore
#: config-verification failure branch) under the engine's method-wide
#: `"replay"`, and
#: `tests/test_rebuild_config_preservation.py::test_post_restore_mismatch_sets_config_reset_and_logs_error`
#: drives exactly that path — so without this exemption the shape rule would
#: red on CORRECT product code and blame the `#9210` lane for a marker write
#: that is not a restore at all (`test_the_config_reset_marker_is_not_read_as_a_restore_leg`
#: pins that, and pins that this exemption is statement-exact).
_NOT_A_RESTORE_STATEMENTS: frozenset[str] = frozenset({
    "MERGE (n:Meta {key:$key}) SET n += $props",
})


def _shape_violations(seen):
    """Writes in the restore MAP SHAPE that carry no restore reason.

    Table-independent by construction: the statement is classified by its
    SHAPE, so a restore statement that is NOT in `_LEGS` is judged too — which
    is the half `_leg_of` cannot reach. The reason it must not carry is the
    engine's own `"replay"`, i.e. the false contract inherited by a write that
    never declared itself.

    The rule has exactly one exemption, `_NOT_A_RESTORE_STATEMENTS` — writes in
    the shape that are NOT restores — matched on the whole statement so it
    cannot swallow a leg.
    """
    out: list[tuple[str, str | None]] = []
    for cypher, reason in seen:
        if not _RESTORE_MAP_SHAPE.search(_norm(cypher)):
            continue
        if isinstance(reason, str) and reason.startswith(f"{RESTORE}:"):
            continue
        if _norm(cypher) in _NOT_A_RESTORE_STATEMENTS:
            continue
        out.append((_norm(cypher)[:120], reason))
    return out


def _seed_prewipe_state(sdk, events):
    """Plant every class whose ONLY post-wipe carrier is the snapshot.

    Each write is the product's own: a bare `CREATE`/`MERGE` on the write
    handle, riding no journal record — which is the whole reason the class has
    to be captured and restored instead of replayed. The journaled Point is
    what makes the run a REAL `rebuild_all` (the wipe happens and the replay
    re-creates the node the batch link and the CONTAINS edge point at).
    """
    created = sdk.create_point("statement", "journaled, so the replay is real",
                              tags=["journaled-tag"])
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
    # #9210 (synthetic leg): a graph-only Point carrying an EXTRA property, so
    # the synthetic fold's OPEN-SET passthrough (`SET n += $extra`) is exercised
    # too, and a graph-only OPERATOR with an `IMPL` edge, so PASS 2 — the other
    # write a synthetic event drives (`_upsert_point_edges`) — is exercised.
    # Without both, the synthetic pin would observe only pass 1a's node MERGE
    # and claim a two-pass property from one pass.
    g.query(
        "CREATE (p:Point {id:'p-graphonly-extra', content:'graph only extra', "
        "content_hash:'beef', status:'live', mitigation_strength:0.7})")
    g.query(
        "MATCH (i:Point {id:'p-graphonly-extra'}) "
        "CREATE (o:Point {id:'p-graphonly-op', content:'IMPL(p-graphonly-extra)', "
        "is_operator:true, op_type:'IMPL', status:'live'}) "
        "CREATE (o)-[:IMPL {idx:0}]->(i)")
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
    # The TABLE-INDEPENDENT half: a capture-restore statement is recognized by
    # its SHAPE, so a leg that is not in `_LEGS` — the residual `#9210` names,
    # since the funnel is voluntary and a twelfth restore can be written as a
    # bare `self.g.query(...)` — is still judged instead of silently absent from
    # `observed`. Without this, `set(observed) == set(_LEGS)` above is satisfied
    # by every KNOWN leg running while an unknown one rides `"replay"`.
    shape_bad = _shape_violations(seen)
    assert not shape_bad, (
        f"{context}: a write in the snapshot-restore MAP SHAPE "
        f"(`SET <alias> += $props`) ran WITHOUT a restore declaration, so it "
        f"inherited the engine's replay permit — the false contract #9210 "
        f"removes, on a statement the leg table does not classify "
        f"(observed={shape_bad!r})")


# ── the declaration ──────────────────────────────────────────────────────────


def test_every_snapshot_restore_leg_declares_itself(tmp_path, monkeypatch):
    """RED before #9210: every restore leg observed the REPLAY lane.

    The pre-fix observation is `"replay"`, NOT `None`. `#9213` landed a
    method-wide `_replay_write_scope()` in `rebuild_all`, and the restore legs
    run inside `_rebuild_all_replay` underneath it, so on the merge base these
    writes were already AUTHORIZED — by a claim that is false for them, which
    is the defect this lane removes. `"replay"` is in no leg's reason set, so
    this test reds on the base tree. The revert-mutation agrees: replace a
    `_restore_write(...)` with the bare `self.g.query(...)` it wraps and the
    observation falls back to the engine's own `"replay"`, never to `None`.

    What is pinned is therefore the SHADOWING, not the survival of a
    permit-free tree: `rebuild_all` is called plainly, with no permit opened by
    the caller, and every restore write must still name its own leg over the
    engine's replay permit — which is what stops the answer to "which writes
    are authorized by replay" from being "whatever this method happens to
    contain".

    MEASURED on this PR's base (`bed945fd3` = `origin/main`) with the lane
    removed (`_restore_write` → the bare `self.g.query(...)` it wraps, the
    watermark called without `restore_reason`): all ELEVEN legs observe
    `"replay"`. `None` is the pre-`#9213` figure, not this base's.
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

    WHY IT CANNOT PASS VACUOUSLY: the same run must observe a replay permit at
    work on the fold (`reason == "replay"` on a statement that is NOT a
    restore leg). Without that half, a `_replay_write_scope()` that never
    reached the handle would let the test pass while proving nothing.

    ⚠️ That half is satisfied by `rebuild_all`'s OWN method-wide permit
    (`#9213`), not necessarily by the one this test opens: the engine opens one
    either way, so the observation cannot tell the two apart. The property the
    test does establish is the one that matters — a replay permit IS in force
    at the handle during this run, and the restore legs do not inherit it.

    RED before #9210 with `reason == "replay"` on every restore leg — the false
    contract in the observable — rather than with `None`.
    """
    sdk, events = _mk_sdk(tmp_path)
    try:
        _, proj, _ = _seed_prewipe_state(sdk, events)
        seen = _record_authorizations(monkeypatch)

        with _replay_write_scope():
            proj.rebuild_all(str(events), confirm_destructive=True)

        # A replay permit was REAL here: it reached the handle on the fold.
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


def test_a_bare_restore_write_in_the_map_shape_is_caught(tmp_path,
                                                       monkeypatch):
    """The pin's TABLE-INDEPENDENT half, and the proof that it bites.

    This is the residual `#9210` discloses, made observable. The funnel is
    VOLUNTARY: nothing makes `_rebuild_all_replay` route a restore statement
    through `_restore_write`, so "the next restore has to declare itself" is a
    convention while `_LEGS` — a table of the eleven statements that EXIST —
    reads green on a twelfth that does not. The surviving half of the pin is
    the SHAPE rule in `_shape_violations`.

    What it does: runs the engine with the funnel BYPASSED — `_restore_write`
    replaced by the bare `self.g.query(...)` it wraps, which is exactly what a
    restore leg added tomorrow as a bare write looks like — and requires the
    shape rule to red on the result, with the reason the bare write actually
    inherits (`"replay"`, the false contract), not with `None`. Without this
    half the rule could be a no-op and every other test here would stay green.

    NOT RED on the pre-fix tree, and it cannot be: the bypass is SIMULATED
    (the ablation), so the test pins a property that holds before and after the
    fix. Its non-vacuity is the ablation assertion below, not a base-tree
    failure. Coverage is honestly partial — the map shape catches
    `SET <alias> += $props`, i.e. the shape every one of the eleven restore legs
    uses; a restore written as a `SET <alias>.<field> = $param` link or a bare
    edge `MERGE` is still caught only while it stays in `_LEGS`, until
    `#9124`'s handle guard is armed.
    """
    from tortoise.projection import FalkorProjection

    sdk, events = _mk_sdk(tmp_path)
    try:
        _, proj, _ = _seed_prewipe_state(sdk, events)
        seen = _record_authorizations(monkeypatch)

        def bare_restore(self, cypher, *, params=None, reason):
            # The mutation `#9210`'s docstring names: a caller that reaches for
            # `self.g.query` declares nothing, so the write falls back to the
            # engine's method-wide `"replay"`.
            return self.g.query(cypher, params=params)

        monkeypatch.setattr(FalkorProjection, "_restore_write", bare_restore)

        proj.rebuild_all(str(events), confirm_destructive=True)

        violations = _shape_violations(seen)
        assert violations, (
            "the map-shape pin is VACUOUS: a full rebuild ran with the restore "
            "funnel bypassed and NOT ONE write was flagged, so the rule that "
            "is supposed to see an undeclared restore leg sees nothing")
        reasons = {reason for _cypher, reason in violations}
        assert reasons == {"replay"}, (
            "a bypassed restore write must be observed under the engine's own "
            "replay permit — that inherited `\"replay\"` IS the defect, so a "
            f"`None` here means the ablation did not run where it claims: "
            f"reasons={sorted(map(str, reasons))}")
    finally:
        sdk.close()


def test_the_config_reset_marker_is_not_read_as_a_restore_leg(tmp_path,
                                                             monkeypatch):
    """The shape rule's ONE exemption, and why it is an exemption.

    `_rebuild_all_replay` emits one write in the restore MAP SHAPE that is not a
    snapshot restore: `set_config_reset_marker()`'s sticky `config_reset`
    marker, on the post-restore config-verification FAILURE branch, under the
    engine's method-wide `"replay"`. Its props are AUTHORED at write time, so
    the restore lane would be FALSE for it — but without an explicit exemption
    the shape rule flags it, and the flag lands on the `#9210` lane while the
    suite that drives the path is green (`test_rebuild_config_preservation.py`).

    NON-VACUITY, three ways:
      * the statement really does match `_RESTORE_MAP_SHAPE` (asserted), so the
        exemption is load-bearing — delete `_NOT_A_RESTORE_STATEMENTS` and the
        second assertion reds;
      * the reason observed at the handle is NOT a restore reason, so this is
        the genuine un-declared case and not one already covered by the
        reason branch above it (if a future edit routes the marker through the
        funnel, this arm tells the next reader the exemption is now dead
        rather than silently keeping it);
      * EXACTNESS: the same shape one alias away is still flagged, so the
        exemption is a statement, not a pattern that could swallow a leg.
    """
    from tortoise.projection import set_config_reset_marker

    sdk, _events = _mk_sdk(tmp_path)
    try:
        proj = sdk._get_proj()
        seen = _record_authorizations(monkeypatch)

        # The engine's own method-wide permit (`rebuild_all` → `#9213`), which
        # is the authorization this write inherits on the failure path.
        with _replay_write_scope():
            set_config_reset_marker(proj.g, "restore_incomplete")

        marker = [(cypher, reason) for cypher, reason in seen
                  if _RESTORE_MAP_SHAPE.search(_norm(cypher))]
        assert marker, (
            "the config_reset marker did not reach the handle in the restore "
            "MAP SHAPE, so this test proves nothing about the exemption; "
            f"observed={[(_norm(c)[:60], r) for c, r in seen]!r}")
        _cypher, reason = marker[0]
        assert not (isinstance(reason, str)
                    and reason.startswith(f"{RESTORE}:")), (
            "the config_reset marker is riding a `pre-wipe-restore:` "
            "declaration. Its props are AUTHORED at write time, not captured "
            "before the wipe, so that contract would be false: either route "
            "it back to the engine's replay permit, or delete the now-dead "
            f"exemption (reason={reason!r})")
        assert _shape_violations(seen) == [], (
            "the shape rule blamed the #9210 restore lane for a marker write "
            "that is not a restore — the false RED the exemption exists to "
            f"prevent: {_shape_violations(seen)!r}")

        # The exemption is the WHOLE statement, not the shape.
        near_miss = [("MERGE (m:Meta {key:$key}) SET m += $props",
                      _CACHE_WRITE_AUTHORIZED.get())]
        assert _shape_violations(near_miss), (
            "the exemption matches a PATTERN, not a statement: a near-miss "
            "restore-shaped write with no declaration must still be flagged, "
            "or the rule has a hole it cannot see")
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


def test_the_journal_engines_watermark_stays_disclosed_as_deferred(tmp_path,
                                                                 monkeypatch):
    """`rebuild()`'s watermark is a DEFERRED #9213 item, measured, not asserted.

    `rebuild_all` (this PR) declares its watermark leg; the journal engine
    `rebuild()` puts back a value it CAPTURED before its own wipe
    (`capture_watermark`) and still writes it under the engine's method-wide
    `"replay"`. Five artifacts used to say that was fine because `rebuild()`
    "re-derives the value from the replayed log" — the code refutes it (the
    carry is a pre-wipe read of the live counter), and nothing observed the
    reason at the write, which is how the claim drifted unchallenged. This is
    that observation.

    It is honest about what it is: a pin on the DISCLOSED state, not on a
    defect this PR fixes. It is not vacuous in either direction — it reds if the
    watermark write never reaches the handle (so it cannot pass on a rebuild
    that silently did nothing), and it reds the moment the lane CHANGES, which
    is exactly when the disclosure in `event_store.py`, the
    `_prewipe_restore_write_scope` docstring and
    `docs/architecture/STORAGE-ARCHITECTURE.md` §17.1 must be updated with it.
    The value provenance that makes a blanket `pre-wipe-restore:` reason wrong
    here (the replayed `seq` can win the `max`) is pinned at the helper seam by
    `test_rebuild_event_meta_watermark_4653.py`;
    `test_reestablish_watermark_never_moves_below_the_replayed_log`.
    """
    from tortoise.log import EventLog

    sdk, events = _mk_sdk(tmp_path)
    try:
        sdk.create_point("statement", "the watermark carries across a rebuild")
        proj = sdk._get_proj()
        seen = _record_authorizations(monkeypatch)

        proj.rebuild(EventLog(str(events / "events.jsonl")),
                     confirm_destructive=True)

        meta = [(cypher, reason) for cypher, reason in seen
                if "GraphEventMeta" in _norm(cypher)
                and _norm(cypher).startswith("MERGE")]
        assert meta, (
            "`rebuild()` issued no `:GraphEventMeta` write at the handle, so "
            "this pin proves nothing about the lane it declares; "
            f"observed={[( _norm(c)[:70], r) for c, r in seen]!r}")
        reasons = {reason for _c, reason in meta}
        assert reasons == {"replay"}, (
            "`rebuild()`'s watermark lane changed. It is a DEFERRED #9213 item: "
            "this engine restores a captured counter, so if it now names a "
            "restore leg the split happened and every disclosure that says "
            "`rebuild()` is left on `\"replay\"` must move with it "
            "(event_store.py's module comment, `reestablish_watermark`'s "
            "docstring, `_prewipe_restore_write_scope`'s WHERE-THE-WATERMARK "
            "paragraph, and STORAGE-ARCHITECTURE.md §17.1). If the split did "
            "NOT happen, a blanket `pre-wipe-restore:` reason is false in the "
            "other direction and this test is what caught it: "
            f"observed={reasons!r}")
    finally:
        sdk.close()


def _synthetic_fold_writes(seen):
    """WRITES at the handle whose params bind a graph-only (no-record) id.

    ⚠️ A restore LEG is excluded, and the exclusion is precise rather than
    convenient: `#4305`'s derived-properties restore also binds a graph-only
    `:Point` id (`MATCH (n:Point {id:$pid}) SET n += $props`), and it is the
    OTHER lane's write — `_leg_of` classifies it, and the restore pin already
    holds it to `pre-wipe-restore:`. What is left after that exclusion is
    exactly the population this pin is about: the fold writes the synthetic
    `PointAdded`/`OperatorAdded` event drives through the ordinary handle.
    """
    return [(_norm(cypher), reason) for cypher, reason, params in seen
            if _writes(cypher) and (_bound_ids(params) & _GRAPH_ONLY_IDS)
            and _leg_of(cypher) is None]


def test_the_synthetic_fold_declares_its_own_lane(tmp_path, monkeypatch):
    """`#9210`'s SECOND leg: the `#548` synthetic fold is NAMED, not inherited.

    The graph-only rows `rebuild_all` carries back are turned into
    `PointAdded`/`OperatorAdded` events built from the pre-wipe CAPTURE, then
    prepended to the replay stream and folded through the ordinary handle. No
    journal record describes them, so the writes they drive are not a
    projection of a record — and until this lane existed they were authorized
    by the engine's method-wide `_replay_write_scope()`, i.e. by a contract
    that is FALSE for them. `rebuild_all`'s docstring disclosed that as a
    residual; this pin is what makes the disclosure unnecessary.

    MEASURED on the pre-fix tree (this fixture: pass 1a's node MERGE, its
    open-set `SET n += $extra`, and pass 2's `MERGE (o)-[:IMPL …]->(s)`, all
    three binding a graph-only id): every one of them observed `"replay"`.
    Not `None` — `#9213`'s permit is real, which is exactly why the wrong
    answer was being served. The mutation is the `with _fold_lane_for_event(…):`
    wrapper at either call site: remove one and that pass's writes fall back to
    `"replay"` and this test reds.

    BOTH passes are required. Pass 1a writes the node and pass 2 re-derives the
    operator's edges from the captured payload, so a single-pass check would go
    green on the first pass while half the synthetic writes stayed on the
    replay permit.
    """
    sdk, events = _mk_sdk(tmp_path)
    try:
        pid, proj, _ = _seed_prewipe_state(sdk, events)
        journal_ids = frozenset({pid})
        seen = _record_writes_with_params(monkeypatch)

        proj.rebuild_all(str(events), confirm_destructive=True)

        synth = _synthetic_fold_writes(seen)
        assert synth, (
            "no write bound a graph-only id, so the synthetic fold never "
            "reached the handle and this pin would be vacuous: "
            f"observed statements={[c[:70] for c, _r, _p in seen]!r}")
        # PASS COVERAGE, asserted rather than assumed: the node MERGE and the
        # open-set `$extra` write are pass 1a; the `IMPL` edge is pass 2.
        assert any(c.startswith("MERGE (n:Point {id:$id})") for c, _r in synth), (
            "pass 1a's synthetic node write was not observed: "
            f"{[c[:80] for c, _r in synth]!r}")
        assert any(c.startswith("MATCH (n:Point {id:$id}) SET n += $extra")
                   for c, _r in synth), (
            "pass 1a's synthetic OPEN-SET passthrough was not observed, so the "
            "lane would only be pinned for the fixed SET list: "
            f"{[c[:80] for c, _r in synth]!r}")
        assert any("MERGE (o)-[:IMPL" in c for c, _r in synth), (
            "pass 2's synthetic edge write was not observed, so this test "
            "would claim a two-pass property from one pass: "
            f"{[c[:80] for c, _r in synth]!r}")

        wrong = {
            (c[:110], str(reason)) for c, reason in synth
            if not (isinstance(reason, str)
                    and reason.startswith(f"{SYNTHETIC}:"))
        }
        assert not wrong, (
            "the `#548` synthetic fold ran under a declaration whose contract "
            "is FALSE for it — its payload came from the pre-wipe graph "
            "CAPTURE, not from a journal record — so it must name "
            f"`{SYNTHETIC}:` and never the replay lane: observed={wrong!r}")

        # THE OTHER DIRECTION, in the same run and in BOTH passes: a JOURNAL
        # record's fold must still observe `"replay"`. Without it the new lane
        # could be a blanket that swallows the record fold too and every
        # assertion above would still pass — and without the PASS-2 half below,
        # the very same blanket at pass 2's call site would go unnoticed (a
        # journaled Point's pass-1a creation already satisfies a pass-1a-only
        # check, so the `TAGGED` write is what makes pass 2 judgeable).
        journal = [(_norm(c), reason) for c, reason, params in seen
                   if _writes(c) and (_bound_ids(params) & journal_ids)
                   and not (_bound_ids(params) & _GRAPH_ONLY_IDS)]
        assert any("MERGE (p)-[:TAGGED]->(t)" in c for c, _r in journal), (
            "no PASS-2 write bound a journaled id, so the over-claim half of "
            "this pin only reaches pass 1a and a blanket lane at pass 2's "
            f"call site would pass: observed={[c[:80] for c, _r in journal]!r}")
        misattributed = [(c[:110], str(r)) for c, r in journal
                         if isinstance(r, str)
                         and r.startswith(f"{SYNTHETIC}:")]
        assert not misattributed, (
            "a JOURNAL record's fold was authorized by the pre-wipe SYNTHETIC "
            "lane — the mirror over-claim, and false in the other direction "
            f"(the payload DID come from the record): {misattributed!r}")
        assert any(reason == "replay" for _c, reason in journal), (
            "the journaled Point's own fold did not observe the replay "
            "permit, so this run cannot show the lane is chosen by "
            f"PROVENANCE rather than applied blanket: observed={journal!r}")
    finally:
        sdk.close()


def test_an_outer_replay_permit_cannot_cover_the_synthetic_fold(tmp_path,
                                                               monkeypatch):
    """An over-broad permit cannot re-acquire the synthetic fold either.

    Same shadowing property the restore legs are pinned for, stated for the
    other leg of `rebuild_all`'s width: a caller (or `#9124`'s engine wrapper)
    that opens `_replay_write_scope()` around the whole rebuild must not be
    able to authorize a synthetic fold with it. The reason is a `ContextVar`,
    so the per-event declaration SHADOWS the outer permit.

    NON-VACUITY: the same run must observe `"replay"` on a JOURNAL fold, so the
    test cannot pass by the outer permit never reaching the handle. (As in the
    restore pin, the observation cannot tell this test's permit from
    `rebuild_all`'s own — both spell `"replay"`; what is established is that a
    replay permit IS in force at the handle and the synthetic writes do not
    inherit it.)
    """
    sdk, events = _mk_sdk(tmp_path)
    try:
        pid, proj, _ = _seed_prewipe_state(sdk, events)
        seen = _record_writes_with_params(monkeypatch)

        with _replay_write_scope():
            proj.rebuild_all(str(events), confirm_destructive=True)

        synth = _synthetic_fold_writes(seen)
        assert synth, (
            "no write bound a graph-only id, so the pin is vacuous: "
            f"{[c[:70] for c, _r, _p in seen]!r}")
        wrong = {
            (c[:110], str(reason)) for c, reason in synth
            if not (isinstance(reason, str)
                    and reason.startswith(f"{SYNTHETIC}:"))
        }
        assert not wrong, (
            "an outer `_replay_write_scope()` covered the synthetic fold, so "
            "the per-event declaration did not shadow it: "
            f"observed={wrong!r}")
        assert any(reason == "replay" for c, reason, params in seen
                   if _writes(c) and pid in _bound_ids(params)), (
            "the replay permit never reached a journal fold, so this test "
            "proves nothing about shadowing: "
            f"{[str(r) for _c, r, _p in seen]!r}")
    finally:
        sdk.close()


def test_the_lane_constants_match_the_pinned_literals():
    """The other half of the pin: the module's constants must spell the lanes
    the tests above pinned, so neither can be renamed past the other.

    These are the tests here that reach for the symbols the fix adds; every
    behavioural test above is written against the literals, which is why they
    go red on the OBSERVED AUTHORIZATION rather than on an ImportError — a
    `"replay"` on the current base (whose `#9213` permit is the false
    contract), a `None` on a pre-`#9213` tree.
    """
    from tortoise.projection import _PREWIPE_RESTORE_LANE, _PREWIPE_SYNTHETIC_LANE

    assert _PREWIPE_RESTORE_LANE == RESTORE
    assert _PREWIPE_SYNTHETIC_LANE == SYNTHETIC
