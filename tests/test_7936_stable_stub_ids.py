"""#7936 — the entity view must be a pure function of the journal.

⚠️ The premise below is the PRE-#3590 one, kept as the record of what this
file was written to catch; the ``_STUB_TARGETS`` matrix further down states
what replaced it (the projection mints NOTHING — D9 — so replay is stable
because there is no fold-time id to be random).

`_event_plain_merge` (`tortoise/projection/entities.py`) minted the `:Object`
/ `:Subject` name stubs an `EventRecorded` implies with a **random** `ulid()`.
The graph fold is MERGE-by-name, so the *node* is stable — but its `id`
property is not: replaying ONE journal twice mints a different stub id each
time.

That is the measured blocker on this issue's remaining `(label, id)` re-key.
The re-key widens the reference fold to one `(label, id)` index deriving
`log_points` / `db_points` / `_compare_views` / `_graph_fingerprint`
(`tortoise/consistency.py`). `_graph_fingerprint` feeds `db_hash`, which is
compared against the STORED baseline (`graph_moved = stored["content_hash"] !=
db_hash`). A random stub id makes that digest a function of WHEN the graph was
built rather than of the JOURNAL — so every legitimate `rebuild_all` would read
`journal_moved=False, graph_moved=True` → a false `unrecorded-mutation` whose
`action` sends the operator hunting an unjournalled writer that does not
exist. The issue's own last comment records this measurement.

The fix #7936 landed was that the stub id was made the SAME identity the
canonical entity path already minted — `tortoise.ids.entity_name_id(label,
name)`, deterministic, so the entity view was a pure function of the journal.
⛔ #3590 then RETIRED that id entirely (an id derived from a name is not an
identity, R1) and the mint with it (D9): the property this file pins survives
— the view must not move across replays of one journal — while the mechanism
that produced it is gone, which is why the matrix below asserts the INVERSE.

#1155-P1 is preserved unchanged and is NOT about the randomness: the ruling is
that the stub MERGE writes `id` **ON CREATE only**, so a late stub cannot
clobber a canonical id (e.g. a connector's `github-issue-{repo}-{n}`). The
regression guards below pin both orderings.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from _node_ids import node_id

from tortoise.consistency import _graph_entities
from tortoise.log import EventLog
from tortoise.sdk import TortoiseSDK

#: A journal whose ONLY record is the event — no `ObjectRegistered` /
#: `SubjectAdded` ever registers the names. It is the shape the issue's last
#: comment measures, and it is the shape the fold used to MINT stubs for.
_STUB_EVENT = {
    "type": "EventRecorded",
    "id": "evt-7936-stub",
    "name": "stub matrix event",
    "eventKind": "test",
    "startedAt": "2026-01-01T00:00:00Z",
    "subject": "stub-subject",
    "object": "stub-object",
    "uses": [{"name": "stub-use", "kind": "tool"}],
}

#: The three names `_event_plain_merge` USED to mint a stub for: the Subject of
#: `EventRecorded.subject`, the Object of `EventRecorded.object`, and one
#: Object per `EventRecorded.uses[]`. ⛔ #3590 retired all three mints (the
#: projection is not the minter, D9), so this matrix now asserts the INVERSE
#: property: replaying the journal creates NO node for any of them.
_STUB_TARGETS = (
    ("Subject", "stub-subject"),
    ("Object", "stub-object"),
    ("Object", "stub-use"),
)


def _journal(tmp_path: Path, *records: dict) -> tuple[TortoiseSDK, Path]:
    """An SDK plus a hand-written journal dir holding exactly ``records``."""
    events = tmp_path / "events"
    events.mkdir(parents=True, exist_ok=True)
    log_path = events / "events.jsonl"
    log_path.write_text("".join(json.dumps(r) + "\n" for r in records))
    sdk = TortoiseSDK(str(tmp_path / "t.db"), event_log_path=str(log_path))
    return sdk, events


def _stub_ids(sdk: TortoiseSDK) -> dict:
    """``{(label, name): id}`` for every Object/Subject node in the graph."""
    rows = sdk._get_proj().g.query(
        "MATCH (n) WHERE n:Object OR n:Subject "
        "RETURN labels(n)[0], n.name, n.id"
    ).result_set or []
    return {(r[0], r[1]): r[2] for r in rows}


def _legacy_stub(sdk: TortoiseSDK, label: str, name: str) -> str:
    """Build the PRE-#3590 name-stub shape a PERSISTED graph still carries.

    ``_event_plain_merge`` minted it (``MERGE (n:<label> {name:$name}) ON CREATE
    SET n.id=$name, n._stub=true``) and #3590 retired the mint (D9: the
    projection is not the minter; D1: a bare mention creates nothing), so
    ``create_event`` no longer produces one — the fixture this class was written
    against is gone, which is why it raised ``KeyError`` here.

    The DISCRIMINATOR GUARD is NOT retired with the mint: a graph built before
    the change still holds these nodes, so a caller able to CLEAR ``_stub`` on
    one still re-opens the measured hole (a forged exclusion stops the later
    ``ObjectRegistered``/``SubjectAdded`` being journaled, the node vanishes on
    rebuild, live != replay). This rebuilds that shape directly, exactly as the
    file's other legacy pins build a pre-change graph out of a raw record.
    """
    kind = "subjectKind" if label == "Subject" else "objectKind"
    # `title`/`status` are part of the shape, not decoration: the RELEASED
    # projection's MERGE chain wrote both on CREATE (`… o.title=coalesce($title,''),
    # o.status=coalesce($st,'live')`), so a stub a PERSISTED pre-#9974-R1 graph
    # carries them — which is the legacy shape this fixture exists to reproduce.
    # (#9974 R1 removed the `status` half: a status-less birth now stores
    # nothing. A legacy stub already holds `'live'`, and no replay can erase it,
    # because the new registration record states no status — see the adoption
    # leg below, which names that asymmetry instead of comparing it away.)
    sdk._get_proj().g.query(
        f"MERGE (n:{label} {{name:$n}}) "
        f"ON CREATE SET n.id=$n, n._stub=true, n.{kind}='other', "
        f"n.title='', n.status='live'",
        params={"n": name})
    return name


def _rebuild(sdk: TortoiseSDK, events: Path) -> None:
    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)


@pytest.mark.parametrize("label,name", _STUB_TARGETS)
def test_the_entity_view_is_stable_across_replays_of_one_journal(
        tmp_path: Path, label: str, name: str):
    """#7936, re-based on #3590: replay is STABLE *because* it mints nothing.

    ⛔ This test's predecessor (`test_a_name_stub_id_is_a_pure_function_of_the
    _journal`) asserted `(label, name) in ids` three times over — i.e. that the
    fold MINTED a node for an unregistered mention, which #3590 retired
    (the projection is not the minter, D9). Kept as a RED-before-fix pin it
    would only pin the absence of the change.

    The property the file exists for survives, in the form the change leaves it:
    three consecutive `rebuild_all`s of ONE journal must produce the SAME entity
    view. Randomness in that view is what made a digest over it unstable (the
    measured `(label, id)` re-key blocker) — and the only fold-time source of it
    was the name-derived stub mint, so the first assertion below is the guard:
    restoring any mint (the retired `entity_name_id` or a `ulid()` stub) puts a
    node here and reds this test, while the drift assertion catches a mint whose
    id is not deterministic.

    The recorded half of the same property — that the dropped mention is NAMED
    at the run boundary rather than swallowed — is
    `test_a_mention_stub_is_not_minted_by_the_fold`.
    """
    sdk, events = _journal(tmp_path, _STUB_EVENT)
    try:
        seen: list = []
        for _ in range(3):
            _rebuild(sdk, events)
            ids = _stub_ids(sdk)
            assert (label, name) not in ids, (
                f"replay MINTED a {label} for the unregistered mention "
                f"{name!r} — the fold is the minter again (#3590 D9), and a "
                "fold-time mint is not replay-deterministic")
            seen.append(_graph_entities(sdk._get_proj())[0])
        assert seen[0] == seen[1] == seen[2], (
            "the graph's entity view MOVED across replays of ONE journal: "
            f"{seen} — a digest over it would report a false "
            "`unrecorded-mutation` on a graph `rebuild_all` itself produced "
            "(#7936)"
        )
    finally:
        sdk.close()


def test_the_graph_entity_view_is_a_pure_function_of_the_journal(
        tmp_path: Path):
    """RED before the fix: the exact index a widened digest would consume.

    `consistency._graph_entities` is what `_graph_fingerprint` would fold into
    the digest under the `(label, id)` re-key. It must be equal for two
    replays of the same journal — otherwise the baseline moves for a reason
    the journal does not contain.
    """
    sdk, events = _journal(tmp_path, _STUB_EVENT)
    try:
        views: list = []
        for _ in range(2):
            _rebuild(sdk, events)
            views.append(_graph_entities(sdk._get_proj())[0])
        assert views[0] == views[1], (
            "the graph's entity view is not a pure function of the journal: "
            f"{views[0]} != {views[1]} (#7936)"
        )
    finally:
        sdk.close()


# ⛔ #3590 DELETED `test_a_stub_carries_the_canonical_entity_name_id` here.
# It asserted `ids[(label, name)] == node_id(sdk, label, name)` — i.e. that a
# replayed mention MINTED a node whose id is the name-derived canonical one.
# The file's own rewritten premise three tests above states the inverse (D9:
# the projection is not the minter), so the two could never both hold: the
# migrated matrix takes the `(label, name) not in ids` branch and this sibling
# raised `KeyError` on all three parameters (`_stub_ids` returns only nodes
# that EXIST). Applying the recorded decision rather than a compromise: a
# property that can only pin a retired mint is deleted, and the inverse matrix
# above is the single statement of what the fold does now.


def test_a_later_canonical_id_still_wins_over_the_stub(tmp_path: Path):
    """#1155-P1, kept green: a foreign canonical id re-keys the stub node.

    Regression guard — this passes before and after the fix. The canonical id
    (a connector's scheme, not `entity_name_id`) must win when it arrives
    AFTER the stub, via `_upsert_object`'s ON MATCH
    ``o.id=coalesce($id, o.id)``.
    """
    canonical = "github-issue-test/repo-42"
    sdk, events = _journal(
        tmp_path, _STUB_EVENT,
        {"type": "ObjectRegistered", "id": canonical, "name": "stub-object"},
    )
    try:
        _rebuild(sdk, events)
        ids = _stub_ids(sdk)
        assert ids[("Object", "stub-object")] == canonical, ids
    finally:
        sdk.close()


def test_a_late_stub_does_not_clobber_a_canonical_id(tmp_path: Path):
    """#1155-P1, kept green: the stub MERGE writes `id` ON CREATE only.

    The canonical registration comes FIRST; the later `EventRecorded` matches
    the node by NAME and must NOT rewrite its id. This is the hazard the
    ON CREATE-only clause exists for — and it is orthogonal to whether the
    stub id is random (it is unchanged by this fix).
    """
    canonical = "github-issue-test/repo-42"
    sdk, events = _journal(
        tmp_path,
        {"type": "ObjectRegistered", "id": canonical, "name": "stub-object"},
        _STUB_EVENT,
    )
    try:
        _rebuild(sdk, events)
        ids = _stub_ids(sdk)
        assert ids[("Object", "stub-object")] == canonical, ids
    finally:
        sdk.close()


def test_a_late_subject_stub_does_not_clobber_a_foreign_id(tmp_path: Path):
    """#1155-P1 (Subject leg): the stub MERGE writes `id` ON CREATE only.

    The Subject companion to the Object guard above, and the guard that keeps
    the clause MEASURABLE now that the stub mints the canonical id: the
    pre-existing Subject carries a FOREIGN id (`subject:existing-agent`, not
    `entity_name_id`), so replacing the Subject stub's `ON CREATE SET` with an
    unconditional `SET` turns this red. Without it,
    `test_event_provenance.py::test_stub_preserves_existing_id_on_merge` —
    whose pre-existing id is now the SAME canonical id the stub would mint —
    could no longer fail on the regression it names.
    """
    foreign = "subject:existing-agent"
    sdk, events = _journal(
        tmp_path,
        {"type": "SubjectAdded", "id": foreign, "name": "stub-subject",
         "subject_kind": "person"},
        _STUB_EVENT,
    )
    try:
        _rebuild(sdk, events)
        ids = _stub_ids(sdk)
        assert ids[("Subject", "stub-subject")] == foreign, ids
    finally:
        sdk.close()


# ── P1 regression: the canonical-id stub must NOT defeat the probe ──────────
#
# `TortoiseSDK._create_entity` (`sdk.py`) decides whether to journal the FIRST
# canonical registration (`ObjectRegistered` / `SubjectAdded`) by asking whether
# a node with the canonical `(id, name)` already exists. Before #7936 the event
# name-stub carried a RANDOM ulid, so that probe always MISSED on a stub and the
# registration was journaled. Once the stub carries the canonical id (#7936)
# the probe HITS and, without a discriminator, the registration stops being
# journaled — silently disabling the durability half of #2194/#2295 (live built
# from a journal that lacks the registration → node loss on rebuild, and a
# live != replay drift the presence/status comparison cannot see).
#
# ⛔ #3590 RETIRED THE FIRST-REGISTRATION PROBE ENTIRELY (#2194/#2295/#3590):
# `_create_entity` now EMITS `ObjectRegistered`/`SubjectAdded` on EVERY
# Object/Subject create — "the probe existed to avoid a duplicate … and it could
# only be honest because the id was a FUNCTION of the name". With a minted id the
# duplicate is the PRICE of the guarantee, is documented at the emit site
# ("the journal grows with CREATE-CALL count, not entity count"), and is
# replay-idempotent (`test_duplicate_registration_lines_replay_idempotently`).
# So the property these tests guard is now stronger and unconditional: NO node
# state can silence a registration — not a legacy `_stub` mark, not an
# already-live holder. The assertions therefore look for the registration that
# CARRIES the create's declared props, not for a single line.
# These guards are RED before that discriminator/emit ever existed.

_EVENT_RECORD = {
    "type": "EventRecorded",
    "id": "evt-7936-probe",
    "eventId": "evt-7936-probe",
    "name": "probe event",
    "eventKind": "test",
    "object": "probe-object",
    "subject": "probe-subject",
    "startedAt": "2026-01-01T00:00:00Z",
}


def _node_props(sdk: TortoiseSDK, label: str, name: str) -> dict:
    """Every persisted property of ``(label, name)`` except the embedding."""
    rows = sdk._get_proj().g.query(
        f"MATCH (n:{label} {{name:$name}}) RETURN properties(n)",
        params={"name": name},
    ).result_set
    assert rows, f"{label} {name!r} is absent from the graph"
    return {k: v for k, v in rows[0][0].items() if k != "embedding"}


def _regs(events: Path, event_type: str, name: str) -> list:
    return [e for e in EventLog(str(events / "events.jsonl")).read_all()
            if e.get("type") == event_type and e.get("name") == name]


def _registration_with(regs: list, **props) -> list:
    """The registration line(s) carrying every one of ``props``.

    With the probe retired a name can hold more than one registration line —
    the event door's D9 registration (minimal props) and the caller's
    `create_entity` (its declared kind) — so a test must name the one whose
    CONTENT it is asserting instead of indexing `regs[0]` (backend row order,
    the silent-pick class this whole change removes).
    """
    return [r for r in regs if all(r.get(k) == v for k, v in props.items())]


class TestStubAdoptionDurability:
    """#7936 P1: a create is ALWAYS journaled, whatever node state precedes it."""

    def test_stub_adoption_journals_object_registration(self, tmp_path: Path):
        """An event-path Object, then `create_entity`, must journal
        `ObjectRegistered` carrying the declared kind, and rebuild
        byte-identically (live == replay)."""
        sdk, events = _journal(tmp_path)
        try:
            sdk.create_event(
                "probe event", "test", object="probe-object",
                subject="probe-subject",
                startedAt="2026-01-01T00:00:00Z")
            sdk.create_entity("object", "probe-object",
                              objectKind="core:task", is_episodic=False)
            regs = _regs(events, "ObjectRegistered", "probe-object")
            adopted = _registration_with(regs, objectKind="core:task")
            assert adopted, (
                "the create's registration was never journaled — nothing but "
                "node state may decide that, so a rebuild drops the node: "
                f"{regs}")
            assert adopted[0]["id"] == node_id(sdk, "Object", "probe-object")
            live = _node_props(sdk, "Object", "probe-object")
            _rebuild(sdk, events)
            rebuilt = _node_props(sdk, "Object", "probe-object")
            assert rebuilt == live, (
                "live != replay after stub adoption: live="
                f"{live} rebuilt={rebuilt}")
            assert rebuilt.get("objectKind") == "core:task", rebuilt
        finally:
            sdk.close()

    def test_stub_adoption_journals_subject_registration(self, tmp_path: Path):
        """The Subject leg of the same probe-gate."""
        sdk, events = _journal(tmp_path)
        try:
            sdk.create_event(
                "probe event", "test", object="probe-object",
                subject="probe-subject",
                startedAt="2026-01-01T00:00:00Z")
            sdk.create_entity("subject", "probe-subject",
                              subjectKind="core:person")
            regs = _regs(events, "SubjectAdded", "probe-subject")
            adopted = _registration_with(regs, subjectKind="core:person")
            assert adopted, (
                "the create's SubjectAdded was never journaled — the Subject "
                f"is live-only and a rebuild drops it: {regs}")
            live = _node_props(sdk, "Subject", "probe-subject")
            _rebuild(sdk, events)
            rebuilt = _node_props(sdk, "Subject", "probe-subject")
            assert rebuilt == live, (
                f"live != replay after Subject stub adoption: {live} != "
                f"{rebuilt}")
            assert rebuilt.get("subjectKind") == "core:person", rebuilt
        finally:
            sdk.close()

    def test_stub_registration_survives_rebuild_without_its_event(
            self, tmp_path: Path):
        """Node loss: the stub's `EventRecorded` is NOT in the rebuilt
        journal (a second producer / journal-less writer sharing the graph).
        The `create_entity` registration must be journaled so the rebuild
        keeps the node instead of deleting it."""
        sdk, events = _journal(tmp_path)
        try:
            sdk.create_event(
                "probe event", "test", object="probe-object",
                subject="probe-subject",
                startedAt="2026-01-01T00:00:00Z")
            sdk.create_entity("object", "probe-object",
                              objectKind="core:task", is_episodic=False)
            regs = _regs(events, "ObjectRegistered", "probe-object")
            assert regs, (
                "no ObjectRegistered line for a name the caller created — "
                "node state silenced the registration")
            # Rebuild from a journal holding ONLY the create's own line (the
            # one carrying the kind), which is what a second producer's
            # journal-less write leaves behind.
            adopted = _registration_with(regs, objectKind="core:task")
            assert adopted, f"the create's registration carries no kind: {regs}"
            other = tmp_path / "events_other"
            other.mkdir()
            EventLog(str(other / "events.jsonl")).append(adopted[0])
            sdk._get_proj().rebuild_all(str(other), confirm_destructive=True)
            props = _node_props(sdk, "Object", "probe-object")
            assert props.get("objectKind") == "core:task", props
        finally:
            sdk.close()

    def test_stub_adoption_journals_uses_object_registration(
            self, tmp_path: Path):
        """The `uses[]` leg of the same probe-gate (#7936 review P3).

        `_event_plain_merge` marks the per-`uses[]` Object stub `_stub=true`
        exactly like the `object`/`subject` stubs, so a later `create_entity`
        for a used name must still journal its `ObjectRegistered` and rebuild
        byte-identically. Without coverage here the `uses[]` mint's
        discriminator could be dropped unnoticed.
        """
        sdk, events = _journal(tmp_path)
        try:
            sdk.create_event(
                "probe event", "test", object="probe-object",
                uses=[{"name": "probe-use", "kind": "core:tool"}],
                startedAt="2026-01-01T00:00:00Z")
            sdk.create_entity("object", "probe-use",
                              objectKind="core:task", is_episodic=False)
            regs = _regs(events, "ObjectRegistered", "probe-use")
            adopted = _registration_with(regs, objectKind="core:task")
            assert adopted, (
                "the `uses[]` create's registration was never journaled — "
                f"nothing but node state may decide that: {regs}")
            assert adopted[0]["id"] == node_id(sdk, "Object", "probe-use")
            live = _node_props(sdk, "Object", "probe-use")
            _rebuild(sdk, events)
            rebuilt = _node_props(sdk, "Object", "probe-use")
            assert rebuilt == live, (
                f"live != replay after uses[] stub adoption: {live} != "
                f"{rebuilt}")
            assert rebuilt.get("objectKind") == "core:task", rebuilt
        finally:
            sdk.close()


# ── P2 regression: the `_stub` discriminator is not caller-writable ─────────
#
# `_stub` gates whether `_create_entity`'s first-registration probe journals a
# registration, so a caller who can WRITE it can forge the exclusion and
# silently stop the journaling (node loss on rebuild + live != replay). The
# write door is `update_entity`'s direct `SET n += $p`; `_sanitize_props`
# (SDK backstop) and `_SERVER_MANAGED_PROPS` (MCP boundary) both refuse it.
#
# These guards are RED before the reject: `update_entity(..., _stub=False)` is
# accepted, no error is raised, and the following `create_entity` skips the
# registration.


class TestStubDiscriminatorIsNotCallerWritable:
    """#7936 P2: `_stub` must not be writable through the generic doors."""

    @pytest.mark.parametrize("label,name", (
        ("Object", "probe-object"),
        ("Subject", "probe-subject"),
    ))
    def test_update_entity_refuses_the_stub_discriminator(
            self, tmp_path: Path, label: str, name: str):
        """The direct-write door (`update_entity`) must refuse `_stub`."""
        sdk, events = _journal(tmp_path)
        try:
            _legacy_stub(sdk, label, name)
            stub_id = _stub_ids(sdk)[(label, name)]
            with pytest.raises(ValueError, match="_stub"):
                sdk.update_entity(stub_id, _stub=False)
            assert "EntityMutated" not in [
                e.get("type")
                for e in EventLog(str(events / "events.jsonl")).read_all()
            ], "a refused `_stub` write still journaled an EntityMutated"
        finally:
            sdk.close()

    def test_a_refused_stub_forge_cannot_defeat_the_registration(
            self, tmp_path: Path):
        """End-to-end: an attempted forge is refused, `ObjectRegistered` is
        still journaled, and live == replay."""
        sdk, events = _journal(tmp_path)
        try:
            _legacy_stub(sdk, "Object", "probe-object")
            stub_id = _stub_ids(sdk)[("Object", "probe-object")]
            with pytest.raises(ValueError, match="_stub"):
                sdk.update_entity(stub_id, _stub=False)
            sdk.create_entity("object", "probe-object",
                              objectKind="core:task", is_episodic=False)
            regs = _regs(events, "ObjectRegistered", "probe-object")
            assert _registration_with(regs, objectKind="core:task"), (
                "an attempted `_stub` forge silenced the registration — a "
                f"rebuild would drop the node: {regs}")
            live = _node_props(sdk, "Object", "probe-object")
            _rebuild(sdk, events)
            rebuilt = _node_props(sdk, "Object", "probe-object")
            # #9974 R1 STATED ASYMMETRY: the LEGACY stub above already stores
            # `status='live'` (the released projection minted it), and the
            # post-fix registration record states NO status, so the live node
            # keeps the stored value while replay leaves the property ABSENT.
            # Both READ live (`live._terminal_excluded` treats an absent status
            # as live), and no fold may CLEAR a stored status on adoption — that
            # would erase a legacy `archived`/`superseded` the record cannot
            # restate. So the comparison is on everything the record decides.
            assert {k: v for k, v in live.items() if k != "status"} == {
                k: v for k, v in rebuilt.items() if k != "status"}, (
                f"live != replay after a refused forge: {live} != {rebuilt}")
            assert live.get("status") in (None, "live"), live
            assert rebuilt.get("status") is None, rebuilt
            assert rebuilt.get("objectKind") == "core:task", rebuilt
        finally:
            sdk.close()

    def test_the_mcp_boundary_refuses_the_stub_discriminator(self):
        """The boundary entry refuses `_stub` in FRONT of the write attempt,
        and does not refuse an ordinary caller prop."""
        from tortoise.mcp_server import _reject_server_managed_props

        msg = _reject_server_managed_props({"_stub": False})
        assert msg is not None and "_stub" in msg, msg
        assert _reject_server_managed_props({"objectKind": "core:task"}) is None
