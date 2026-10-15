"""#7936 — the name-stub id must be a pure function of the journal.

`_event_plain_merge` (`tortoise/projection/entities.py`) mints the `:Object` /
`:Subject` name stubs an `EventRecorded` implies with a **random** `ulid()`.
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

The fix is that the stub id is the SAME identity the canonical entity path
already mints — `tortoise.ids.entity_name_id(label, name)` (the helper
`tortoise/sdk.py` has carried since #452/#1315, here promoted to the neutral
`tortoise/ids.py` so the fold can read it without importing the SDK). It is
deterministic, so the entity view becomes a pure function of the journal; and
it EQUALS the id a generic `ObjectRegistered`/`SubjectAdded` for that name
carries, so the canonical registration is a no-op rather than a re-key.

#1155-P1 is preserved unchanged and is NOT about the randomness: the ruling is
that the stub MERGE writes `id` **ON CREATE only**, so a late stub cannot
clobber a canonical id (e.g. a connector's `github-issue-{repo}-{n}`). The
regression guards below pin both orderings.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tortoise.consistency import _graph_entities
from tortoise.log import EventLog
from tortoise.sdk import TortoiseSDK, _entity_name_id

#: A journal whose ONLY record is the event — no `ObjectRegistered` /
#: `SubjectAdded` ever registers the names, so the stubs are the whole
#: non-Point node set. This is the shape the issue's last comment measures.
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

#: The three stub mint sites in `_event_plain_merge`: the Subject of
#: `EventRecorded.subject`, the Object of `EventRecorded.object`, and one
#: Object per `EventRecorded.uses[]`.
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


def _rebuild(sdk: TortoiseSDK, events: Path) -> None:
    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)


@pytest.mark.parametrize("label,name", _STUB_TARGETS)
def test_a_name_stub_id_is_a_pure_function_of_the_journal(
        tmp_path: Path, label: str, name: str):
    """RED before the fix: replaying ONE journal re-mints a random stub id.

    Three consecutive `rebuild_all`s of the SAME journal must yield the SAME
    id for the same name. Randomness here is what makes a digest over the
    entity view unstable, which is the measured `(label, id)` re-key blocker.
    """
    sdk, events = _journal(tmp_path, _STUB_EVENT)
    try:
        seen: list = []
        for _ in range(3):
            _rebuild(sdk, events)
            ids = _stub_ids(sdk)
            assert (label, name) in ids, ids
            seen.append(ids[(label, name)])
        assert len(set(seen)) == 1, (
            f"the {label} stub id for {name!r} MOVED across replays of ONE "
            f"journal ({seen}): the mint is not a function of the journal, so "
            f"a digest over the entity view would report a false "
            f"`unrecorded-mutation` on a graph `rebuild_all` itself produced "
            f"(#7936)"
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


@pytest.mark.parametrize("label,name", _STUB_TARGETS)
def test_a_stub_carries_the_canonical_entity_name_id(
        tmp_path: Path, label: str, name: str):
    """The stub id IS the id the canonical entity path mints for that name.

    Owning one identity is what makes the later generic registration a no-op
    (rather than a re-key), and it is the shape `_check_refs` already reserves
    for entity ids.
    """
    sdk, events = _journal(tmp_path, _STUB_EVENT)
    try:
        _rebuild(sdk, events)
        ids = _stub_ids(sdk)
        assert ids[(label, name)] == _entity_name_id(label, name), ids
    finally:
        sdk.close()


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
# `_event_plain_merge` marks its stubs `_stub=true` ON CREATE; the probe excludes
# marked stubs; `_upsert_object`/`_upsert_subject` clear the mark on adoption.
# These guards are RED before that discriminator exists.

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


class TestStubAdoptionDurability:
    """#7936 P1: a canonical-id stub must still journal its registration."""

    def test_stub_adoption_journals_object_registration(self, tmp_path: Path):
        """An event-path Object stub, then `create_entity`, must journal
        `ObjectRegistered` and rebuild byte-identically (live == replay)."""
        sdk, events = _journal(tmp_path)
        try:
            sdk.create_event(
                "probe event", "test", object="probe-object",
                subject="probe-subject",
                startedAt="2026-01-01T00:00:00Z")
            sdk.create_entity("object", "probe-object",
                              objectKind="core:task", is_episodic=False)
            regs = _regs(events, "ObjectRegistered", "probe-object")
            assert len(regs) == 1, (
                "the canonical-id event stub defeated the first-registration "
                "probe — ObjectRegistered was never journaled, so rebuild "
                f"drops the node: {regs}")
            assert regs[0]["id"] == _entity_name_id("Object", "probe-object")
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
            assert len(regs) == 1, (
                "the canonical-id event stub defeated the SubjectAdded "
                f"probe — the Subject is live-only: {regs}")
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
                "no ObjectRegistered line for a stubbed name — the probe "
                "hit the canonical-id stub and skipped the registration")
            # Rebuild from a journal holding ONLY the registration line.
            other = tmp_path / "events_other"
            other.mkdir()
            EventLog(str(other / "events.jsonl")).append(regs[0])
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
            assert len(regs) == 1, (
                "the canonical-id `uses[]` stub defeated the "
                "first-registration probe — ObjectRegistered was never "
                f"journaled, so rebuild drops the node: {regs}")
            assert regs[0]["id"] == _entity_name_id("Object", "probe-use")
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
            sdk.create_event(
                "probe event", "test", object="probe-object",
                subject="probe-subject",
                startedAt="2026-01-01T00:00:00Z")
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
            sdk.create_event(
                "probe event", "test", object="probe-object",
                startedAt="2026-01-01T00:00:00Z")
            stub_id = _stub_ids(sdk)[("Object", "probe-object")]
            with pytest.raises(ValueError, match="_stub"):
                sdk.update_entity(stub_id, _stub=False)
            sdk.create_entity("object", "probe-object",
                              objectKind="core:task", is_episodic=False)
            regs = _regs(events, "ObjectRegistered", "probe-object")
            assert len(regs) == 1, (
                "an attempted `_stub` forge silenced the first-registration "
                f"probe: {regs}")
            live = _node_props(sdk, "Object", "probe-object")
            _rebuild(sdk, events)
            rebuilt = _node_props(sdk, "Object", "probe-object")
            assert rebuilt == live, (
                f"live != replay after a refused forge: {live} != {rebuilt}")
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
