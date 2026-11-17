"""Tests for the Object status projection (#1350, decisions 1a/2a/3a):

- CommitPayload supersessions field (optional, additive — old payloads valid)
- SDK _commit_session_v2 surfaces + passes supersessions through
- ObjectSuperseded event → projection fold → Object.status='superseded'
  (+ rebuild replay, clobber guard, chain idempotence)
- Work-item fold: GitHub/Linear lifecycle events → in_progress/completed
- Read side: object results carry status/superseded_by; recall_state filters
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise.sdk import TortoiseSDK  # noqa: E402, RUF100


def _fresh_sdk() -> TortoiseSDK:
    return TortoiseSDK(os.path.join(tempfile.mkdtemp(), "t.db"))


def _oid(sdk, name: str, label: str = "Object") -> str:
    """The MINTED id ``name`` carries — #3590 made the supersede fold id-keyed
    only, so every fold below addresses its target by id, never by name."""
    rows = sdk._get_proj().g.query(
        f"MATCH (n:{label} {{name:$n}}) RETURN n.id",
        params={"n": name}).result_set
    assert rows, f"fixture did not create {label} {name!r}"
    return rows[0][0]


# ── Payload contract (Step 1) ─────────────────────────────────────────

class TestPayloadContract:
    def test_supersessions_optional_and_additive(self):
        from tortoise.commit_schema import (  # noqa: I001
            validate_payload_dict, compute_client_commit_id)
        base = {
            "schema_version": "1", "session_id": "s1",
            "client_commit_id": "", "captured_at": "2026-08-18T00:00:00+00:00",
            "extractor": {"version": "v", "mode": "byok", "calibration_version": "v2"},
            "points": [],
            "telemetry": {
                "extractor": {"version": "v", "mode": "byok"},
                "model": {"provider": "byok", "id": "user-model"},
                "counts": {},
            },
        }
        base["client_commit_id"] = compute_client_commit_id(
            base["session_id"], [], [], [], "", "", [], [])
        l1, _ = validate_payload_dict(base)
        assert l1.ok, "old-shape payload (no supersessions) must still validate"
        base["supersessions"] = [
            {"superseded": "obj-a", "supersedes_by": "strategy-B",
             "evidence": "entity lifecycle supersedes"}]
        base["client_commit_id"] = compute_client_commit_id(
            base["session_id"], [], [], [], "", "", [],
            base["supersessions"])
        l1b, _ = validate_payload_dict(base)
        assert l1b.ok, "new-shape payload with supersessions must validate"

    def test_supersession_record_requires_fields(self):
        from tortoise.commit_schema import SupersessionRecord  # noqa: I001
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            SupersessionRecord(superseded="", supersedes_by="b")
        with pytest.raises(ValidationError):
            SupersessionRecord(superseded="a", supersedes_by="")
        r = SupersessionRecord(superseded="a", supersedes_by="b")
        assert r.evidence == ""

    def test_canonical_includes_supersessions(self):
        from tortoise.commit_schema import canonical_payload
        c1 = canonical_payload("s", [], [], [], "sum", "arc", [],
                               [{"superseded": "a", "supersedes_by": "b"}])
        c2 = canonical_payload("s", [], [], [], "sum", "arc", [], [])
        assert c1 != c2, "a supersession change must change the commit id"

    def test_old_client_commit_id_still_validates(self):
        """#1350 compat guard: a pre-#1350 client computed its id over a
        canonical WITHOUT the supersessions key — the new recompute must
        omit the key when empty so old commits still validate (no 422)."""
        import hashlib  # noqa: I001
        import json
        from tortoise.commit_schema import validate_payload_dict, canonical_payload
        # the pre-#1350 canonical (keys before supersessions existed)
        pre_change = json.dumps({
            "session_id": "s1", "summary": "sum", "story_arc": "arc",
            "points": [], "entities": [], "operators": [], "events": [],
        }, sort_keys=True, separators=(",", ":"))
        old_id = hashlib.sha256(pre_change.encode()).hexdigest()
        payload = {
            "schema_version": "1", "session_id": "s1",
            "client_commit_id": old_id,
            "captured_at": "2026-08-18T00:00:00+00:00",
            "extractor": {"version": "v", "mode": "byok",
                           "calibration_version": "v2"},
            "summary": "sum", "story_arc": "arc", "points": [],
            "telemetry": {"extractor": {"version": "v", "mode": "byok"},
                           "model": {"provider": "byok", "id": "user-model"},
                           "counts": {}},
        }
        l1, _ = validate_payload_dict(payload)
        assert l1.ok, "pre-#1350 commit id must still validate"
        # and the empty-canonical truly omits the key (byte-identical)
        c = canonical_payload("s1", [], [], [], "sum", "arc", [], [])
        assert '"supersessions"' not in c


# ── Projection fold (Steps 4-6) ──────────────────────────────────────

class TestProjectionFold:
    def test_object_superseded_fold(self):
        """The projection-owned fold (what the commit path invokes after
        emitting ObjectSuperseded) sets status + supersededBy."""
        sdk = _fresh_sdk()
        try:
            sdk.create_entity("object", "strategy-A", objectKind="core:strategy")
            # #3590: the fold is ID-KEYED ONLY — an id-less record addresses
            # nothing (the name branch is retired; see
            # `test_fold_returns_match_count`'s (3b) case).
            sdk._get_proj()._fold_object_superseded({
                "id": _oid(sdk, "strategy-A"), "name": "strategy-A",
                "supersedes_by": "strategy-B"})
            proj = sdk._get_proj()
            rows = proj.g.query(
                "MATCH (o:Object {name:$n}) RETURN o.status, o.supersededBy",
                params={"n": "strategy-A"}).result_set
            assert rows and rows[0][0] == "superseded"
            assert rows[0][1] == "strategy-B"
        finally:
            sdk.close()

    def test_rebuild_replays_object_superseded(self):
        """FalkorProjection.apply folds an ObjectSuperseded event — the
        rebuild replay path reproduces status='superseded'."""
        sdk = _fresh_sdk()
        try:
            sdk.create_entity("object", "strategy-A", objectKind="core:strategy")
            # the journaled event shape (id + name + supersedes_by) — the id is
            # the address; #3590 retired the name-keyed fallback.
            sdk._get_proj().apply({
                "type": "ObjectSuperseded",
                "id": _oid(sdk, "strategy-A"), "name": "strategy-A",
                "supersedes_by": "strategy-B"})
            rows = sdk._get_proj().g.query(
                "MATCH (o:Object {name:$n}) RETURN o.status",
                params={"n": "strategy-A"}).result_set
            assert rows and rows[0][0] == "superseded"
        finally:
            sdk.close()

    def test_rebuild_all_restores_object_superseded_fold(self, tmp_path):
        """#2164 pass-1b rebuild parity: a journaled ObjectSuperseded (C2
        kwargs shape — id + name + supersedes_by riding the JSONL envelope) is
        REPLAYED by rebuild_all, not just folded live by apply().

        Pre-fix the rebuild pass-1b elif chain (projection/__init__.py ~:1351)
        dispatched ObjectSuperseded in apply() but had NO rebuild branch — the
        journaled event silently fell through and the Object reverted to
        status='live' on JSONL wipe+rebuild. (The older
        test_rebuild_replays_object_superseded is MISLABELED: it calls
        proj.apply() directly and never exercises rebuild_all — this test
        journals real events and replays them.)

        Rebuild-only shape note (M1): the fold target must EXIST at replay, so
        ObjectRegistered must be journaled BEFORE ObjectSuperseded. #2194
        closed the capture journaling gap: create_entity auto-journals the
        ObjectRegistered on first canonical registration (probe-gated,
        sdk.py _create_entity) — the replay's fold target comes from the real
        production line, exactly as a capture session journals it.
        """
        events = tmp_path / "events"
        events.mkdir()
        sdk = TortoiseSDK(str(tmp_path / "t2164a.db"),
                          event_log_path=str(events / "events.jsonl"))
        try:
            sdk.create_entity("object", "strategy-A",
                              objectKind="core:strategy")
            # Resolve the canonical id for the fold emission (the shared
            # apply_supersessions discipline: id-style, name carried too).
            rows = sdk._get_proj().g.query(
                "MATCH (o:Object {name:$n}) RETURN o.id",
                params={"n": "strategy-A"}).result_set
            oid = rows[0][0]
            # The registration is AUTO-journaled by create_entity (#2194) —
            # no manual ObjectRegistered needed; only the fold is emitted
            # here. C2 kwargs shape: extra kwargs ride the JSONL envelope
            # (event.update(extra)) so supersededBy survives the replay.
            sdk._emit_event("ObjectSuperseded", id=oid, name="strategy-A",
                            supersedes_by="strategy-B")
            # #2164 final-review P4: capture the journaled emission ts — the
            # fold must replay supersededAt from the event (its original ts),
            # not drift it to rebuild time.
            from tortoise.log import EventLog
            journaled = [ev for ev in
                         EventLog(str(events / "events.jsonl")).read_all()
                         if ev.get("type") == "ObjectSuperseded"]
            sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
            rows = sdk._get_proj().g.query(
                "MATCH (o:Object {name:$n}) RETURN o.status, "
                "o.supersededBy, o.supersededAt",
                params={"n": "strategy-A"}).result_set
            assert rows, "journaled ObjectRegistered must recreate the target"
            assert rows[0][0] == "superseded", "status reverted to live on replay"
            assert rows[0][1] == "strategy-B", "supersededBy lost on replay"
            assert journaled and rows[0][2] == journaled[-1]["ts"], (
                "supersededAt must replay the journaled event ts, not drift "
                "to rebuild time")
        finally:
            sdk.close()

    def test_rebuild_all_long_successor_replays_verbatim(self, tmp_path):
        """#5370 (indicator 4): a >200-char successor survives the
        journal→rebuild replay BYTE-FOR-BYTE. The fold stores the FULL name
        live and on replay (the journaled ObjectSuperseded carries it
        verbatim), so the replay lane gains no divergence and no 200-char
        prefix. Pre-fix both lanes truncated identically to a prefix that
        named no Object — self-consistent but wrong."""
        from tortoise.commit_ops import apply_supersessions

        long_name = "gh-issue-title-" + ("y" * 240)
        assert len(long_name) > 200
        events = tmp_path / "events"
        events.mkdir()
        sdk = TortoiseSDK(str(tmp_path / "t5370r.db"),
                          event_log_path=str(events / "events.jsonl"))
        try:
            sdk.create_entity("object", "replay-src")
            sdk.create_entity("object", long_name)
            warns: list[str] = []
            applied = apply_supersessions(
                sdk._get_proj(), sdk,
                [{"superseded": "replay-src", "supersedes_by": long_name,
                  "evidence": "#5370 replay"}],
                session_id="s5370_r", warn=warns.append)
            assert applied == 1, warns
            live = sdk._get_proj().g.query(
                "MATCH (o:Object {name:'replay-src'}) "
                "RETURN o.supersededBy").result_set[0][0]
            assert live == long_name, len(live)

            sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
            status, replayed = sdk._get_proj().g.query(
                "MATCH (o:Object {name:'replay-src'}) "
                "RETURN o.status, o.supersededBy").result_set[0]
            assert status == "superseded", (status, replayed)
            assert replayed == live == long_name, (len(replayed), len(live))
        finally:
            sdk.close()

    def test_rebuild_all_rewrites_a_legacy_prefix_to_the_journaled_full_name(
            self, tmp_path):
        """#5370 review round 1 (P2): a row folded BEFORE the fix holds the
        old 200-char prefix, while the journaled ObjectSuperseded always
        carried the FULL name. So `rebuild_all` REWRITES the prefix to the
        full name — a real live↔replay difference on pre-fix rows, and a
        benign one-way healing (§11: the event log is the reconstruction
        source, so its value is the correct one).

        This is pinned rather than assumed: the sibling test above covers
        only rows folded AFTER the fix (where live and replay already
        agree), which is not the population a pre-fix graph actually has.
        The legacy prefix is simulated by writing exactly the value the old
        fold would have written — the pre-fix assertion the reproduced probe
        used (`stored == long_name[:200]`).
        """
        from tortoise.commit_ops import apply_supersessions

        long_name = "gh-issue-title-" + ("z" * 240)
        legacy_prefix = long_name[:200]
        assert len(long_name) > 200 and len(legacy_prefix) == 200
        events = tmp_path / "events"
        events.mkdir()
        sdk = TortoiseSDK(str(tmp_path / "t5370legacy.db"),
                          event_log_path=str(events / "events.jsonl"))
        try:
            sdk.create_entity("object", "legacy-src")
            sdk.create_entity("object", long_name)
            warns: list[str] = []
            applied = apply_supersessions(
                sdk._get_proj(), sdk,
                [{"superseded": "legacy-src", "supersedes_by": long_name,
                  "evidence": "#5370 legacy replay"}],
                session_id="s5370_l", warn=warns.append)
            assert applied == 1, warns

            # Simulate the pre-fix fold's stored value on the live row.
            sdk._get_proj().g.query(
                "MATCH (o:Object {name:'legacy-src'}) "
                "SET o.supersededBy = $p", params={"p": legacy_prefix})
            live = sdk._get_proj().g.query(
                "MATCH (o:Object {name:'legacy-src'}) "
                "RETURN o.supersededBy").result_set[0][0]
            assert live == legacy_prefix, len(live)

            sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
            _status, replayed = sdk._get_proj().g.query(
                "MATCH (o:Object {name:'legacy-src'}) "
                "RETURN o.status, o.supersededBy").result_set[0]

            # The journal carries the FULL name, so replay heals the
            # prefix. If this ever flips to `replayed == legacy_prefix`,
            # the replay lane has stopped being the reconstruction source
            # and the legacy rows would stay permanently unverifiable.
            assert replayed == long_name, (len(replayed), len(long_name))
            assert replayed != legacy_prefix
        finally:
            sdk.close()

    def test_rebuild_all_fold_before_registration_still_folds(self, tmp_path):
        """#2164 round-2 review (second-model ISSUE 1): the rebuild fold
        sweep must run AFTER all object-creation events, not chronologically.
        A journaled producer can legitimately place the ObjectSuperseded
        event BEFORE the ObjectRegistered/object-creation that recreates the
        Object (connector EventRecorded produces-edge MERGE ON CREATE, or a
        later registration): chronological folding would no-op (0 rows — the
        Object does not exist yet) and the later re-creation would then
        resurrect it status='live' — a dead Object reappearing in
        recall_state's default view. Two-sweep: registration first, fold
        after → superseded survives regardless of journal order."""
        events = tmp_path / "events"
        events.mkdir()
        sdk = TortoiseSDK(str(tmp_path / "t2164f.db"),
                          event_log_path=str(events / "events.jsonl"))
        try:
            # #2194 restructure: a live create_entity auto-journals its
            # registration FIRST (probe-gated) — a create-first preamble
            # would yield [OR, OS] and unpin the two-sweep regression. The
            # adversarial [OS, OR] order comes from the connector/journaled-
            # producer lane (a fold emitted for a name not yet registered in
            # THIS log).
            #
            # ⛔ #3590: the id is MINTED at creation and opaque (R1), so it
            # cannot be synthesized BEFORE the create — an id the registration
            # does not carry would make the deferred sweep address nothing and
            # the two-sweep property unreachable (measured: `folded == 0`, the
            # node stayed `live`). So: create through the SDK (which journals
            # the REAL registration and returns the REAL minted id), then
            # REORDER the journal to the adversarial [ObjectSuperseded,
            # ObjectRegistered] shape, carrying that same id. The property is
            # unchanged — the fold must match the node the LATER registration
            # recreates — only the id's provenance moved from derivation to the
            # graph.
            oid = sdk.create_entity("object", "strategy-A",
                                    objectKind="core:strategy")["node"]["id"]
            # the journal order is genuinely [OS, OR] AFTER the rewrite below
            from tortoise.log import EventLog
            _log = events / "events.jsonl"
            _records = EventLog(str(_log)).read_all()
            _reg = [ev for ev in _records
                    if ev.get("type") == "ObjectRegistered"]
            assert len(_reg) == 1 and _reg[0].get("id") == oid, (
                f"the registration does not carry the minted id: {_reg}")
            _sup = {"type": "ObjectSuperseded", "id": oid,
                    "name": "strategy-A", "supersedes_by": "strategy-B"}
            _other = [ev for ev in _records
                      if ev.get("type") != "ObjectRegistered"]
            _log.write_text("".join(json.dumps(ev) + "\n" for ev in
                                    [_sup, *_other, *_reg]))
            _jtypes = [ev.get("type") for ev in
                       EventLog(str(_log)).read_all()
                       if ev.get("type") in ("ObjectRegistered",
                                             "ObjectSuperseded")]
            assert _jtypes.index("ObjectSuperseded") < _jtypes.index(
                "ObjectRegistered"), _jtypes
            sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
            rows = sdk._get_proj().g.query(
                "MATCH (o:Object {name:$n}) RETURN o.status, o.supersededBy",
                params={"n": "strategy-A"}).result_set
            assert rows, "journaled ObjectRegistered must recreate the target"
            assert rows[0][0] == "superseded", (
                "chronological replay resurrected the superseded Object to "
                f"live: {rows[0]!r} — the fold sweep must run after creation")
            assert rows[0][1] == "strategy-B", rows[0]
        finally:
            sdk.close()

    def test_rebuild_all_legacy_6b_id_only_shape_supersedes(self, tmp_path):
        """LEGACY-FORMAT replay compatibility (#2164/#2193): historical
        id-only ObjectSuperseded lines produced pre-#2193 (hosted §6b passed
        the payload dict POSITIONALLY to _emit_event — GraphEvent-store only
        — with just id= as the JSONL kwarg; name/supersedes_by never reached
        the journal) replay UNCHANGED: the fold flips status but restores
        supersededBy=''. The hosted producer moved to the id-style kwargs
        shape in #2193; old lines must keep replaying."""
        events = tmp_path / "events"
        events.mkdir()
        sdk = TortoiseSDK(str(tmp_path / "t2164b.db"),
                          event_log_path=str(events / "events.jsonl"))
        try:
            sdk.create_entity("object", "strategy-C",
                              objectKind="core:strategy")
            rows = sdk._get_proj().g.query(
                "MATCH (o:Object {name:$n}) RETURN o.id",
                params={"n": "strategy-C"}).result_set
            oid = rows[0][0]
            # The registration is AUTO-journaled by create_entity (#2194) —
            # no manual ObjectRegistered needed; only the legacy fold shape
            # below is manual. Verbatim legacy §6b emission shape
            # (pre-#2193): positional payload (GraphEvent store) + id kwarg
            # (JSONL). The JSONL line carries type+id only.
            sdk._emit_event(
                "ObjectSuperseded",
                {"id": oid, "name": "strategy-C",
                 "supersedes_by": "strategy-D", "evidence": ""},
                id=oid,
            )
            sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
            rows = sdk._get_proj().g.query(
                "MATCH (o:Object {name:$n}) RETURN o.status, o.supersededBy",
                params={"n": "strategy-C"}).result_set
            assert rows and rows[0][0] == "superseded", \
                "id-only ObjectSuperseded must still flip status on replay"
            assert rows[0][1] == "", "legacy shape must fold supersededBy=''"
        finally:
            sdk.close()

    def test_rebuild_all_legacy_idless_object_is_refused_not_synthesized(
            self, tmp_path):
        """#3590: an id-less legacy Object is UNADDRESSABLE — the live path
        refuses it, so nothing is journaled and nothing folds on replay.

        ⛔ This test's predecessor asserted the OPPOSITE (`applied == 1`, an
        `ObjectSuperseded` in the JSONL carrying a SYNTHESIZED
        `obj-<sha26(name)>` id, and a replay that fell back to the name branch).
        Both halves of that pin are RETIRED by this change: R1 ("an id is never
        derived from the name") deletes the synthesized id, and the fold is
        id-keyed only. Keeping the old pin green would require re-growing the
        exact name-derived path the change exists to remove, so it is rewritten
        to pin the REFUSAL — which was previously pinned NOWHERE:

          * `apply_supersessions` records the non-fold reason
            (`shape="unaddressable-name"` — #9876 review round 14: the target
            IS registered, it just carries no id, and `entity_identity`'s own
            record vocabulary owns that spelling; `unregistered-reference`
            belongs to the FOLD's shapes, for a target nothing registered) and
            warns,
          * `applied == 0` — the id-less node is NOT silently "superseded"
            while the journal holds nothing that would replay it, and
          * the rebuild leaves it `live` (no phantom identity in the log).

        RED-capability: pre-#3590 this fails at `applied == 0` (the old code
        applied and journaled the synthesized id)."""
        from tortoise.commit_ops import apply_supersessions

        events = tmp_path / "events"
        events.mkdir()
        sdk = TortoiseSDK(str(tmp_path / "tlegacy-rebuild.db"),
                          event_log_path=str(events / "events.jsonl"))
        try:
            proj = sdk._get_proj()
            name = "legacy-R"
            successor = "successor-S"
            # successor must be visible (payload-entities equivalent). Its
            # create auto-journals an ObjectRegistered (#2194) — harmless.
            sdk.create_entity("object", successor, objectKind="core:strategy")
            # the legacy registration line — journaled in the pre-canonical
            # era's id shape (github-style, NOT _entity_name_id), so the
            # replayed node exists but carries no canonical id. KEPT MANUAL:
            # create_entity would mint the SYNTHESIZED canonical id, which is
            # exactly the id the fold emits — this test's point is a
            # registration id that DIFFERS from the synthesized one. The node
            # must exist at replay for the fold to have a target.
            legacy_reg_id = f"github-issue-{name}-7"
            sdk._emit_event("ObjectRegistered", id=legacy_reg_id, name=name,
                            objectKind="core:strategy")
            # raw legacy Object — NO id property at all
            proj.g.query("CREATE (o:Object {name:$n, status:'live'})",
                         params={"n": name})
            warns: list[str] = []
            applied = apply_supersessions(
                proj, sdk,
                [{"superseded": name, "supersedes_by": successor,
                  "evidence": "legacy lifecycle"}],
                session_id="sess_legacy_r", warn=warns.append)
            assert applied == 0, (
                "an id-less target was superseded anyway — the pre-#3590 "
                f"name-derived synthesis is back: {warns}")
            assert any("id-less" in w or "no id to address" in w
                       for w in warns), (
                f"the refusal was silent — no operator-visible reason: {warns}")
            from tortoise.entity_identity import non_folded_entries
            assert any(e.shape == "unaddressable-name"
                       and e.name == name for e in non_folded_entries()), (
                "the refused coordinate was not recorded as a non-folded "
                f"entry: {non_folded_entries()}")
            # crux: nothing was journaled — a synthesized id would have been a
            # phantom identity the rebuild could never match.
            from tortoise.log import EventLog
            journaled = [ev for ev in
                         EventLog(str(events / "events.jsonl")).read_all()
                         if ev.get("type") == "ObjectSuperseded"]
            assert not journaled, (
                f"an unaddressable supersession reached the journal: {journaled}")
            sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
            rows = proj.g.query(
                "MATCH (o:Object {name:$n}) RETURN o.status, o.supersededBy",
                params={"n": name}).result_set
            assert rows, "the replayed registration must recreate the Object"
            # #9974 R1: the birth status is DERIVED-OR-ABSENT — never the
            # fabricated `live` the fold used to seed and the door used to write
            # into the creation record. This leg's claim is unchanged and is what
            # it must keep saying: an Object the journal never superseded is NOT
            # `superseded` on replay. An absent status reads LIVE
            # (`live._terminal_excluded`), so the assertion is on the CONTRACT
            # (not terminal, no carrier) rather than on a literal the projection
            # has no derivation for.
            assert rows[0][0] is None, (
                "an Object the journal never superseded came back "
                f"`superseded` from replay: {rows!r}")
            assert rows[0][1] is None, rows
        finally:
            sdk.close()

    def test_fold_id_miss_does_not_fall_back_to_name(self):
        """#3590: an id that matches NO Object is a MISS — it is never
        re-resolved by the name the record also carries.

        ⛔ This test's predecessor (`test_fold_id_miss_falls_back_to_name`)
        pinned the #2164 ISSUE B fallback: id-miss + name-present must fold the
        node found BY NAME. That fallback is RETIRED (#3590): under same-name
        coexistence a name-keyed fold supersedes a DIFFERENT incarnation —
        #3573's burial class, and the reason `_update_entity` had to withhold
        rename records. `_fold_object_superseded` now issues
        ``MATCH (o:Object {id:$id})`` and nothing else, so the honest pin is the
        inverse: `(0, 0)` with the node left `live`, on both `cas` modes.

        RED-capability: re-growing a name branch (or any "id missed, try the
        name" fallback) returns `(1, 1)` and reds this."""
        sdk = _fresh_sdk()
        try:
            proj = sdk._get_proj()
            fold = proj._fold_object_superseded
            # #2242: a FRESH legacy node per cas mode — the cas=False pass
            # folds (blind) and would leave the cas=True pass a terminal
            # re-fold (0,1); each arm must assert a first-fold fallback.
            for cas in (False, True):
                name = f"legacy-F-{int(cas)}"
                # raw legacy Object — NO id property at all
                proj.g.query("CREATE (o:Object {name:$n, status:'live'})",
                             params={"n": name})
                matched = fold({"id": f"fixture-{name}",
                                "name": name,
                                "supersedes_by": "successor-F"}, cas=cas)
                assert matched == (0, 0), (
                    "an id-miss was re-resolved by the record's NAME — the "
                    "name branch is back, and it supersedes a different "
                    f"incarnation under coexistence (cas={cas}): {matched}")
                rows = proj.g.query(
                    "MATCH (o:Object {name:$n}) RETURN o.status, o.supersededBy",
                    params={"n": name}).result_set
                assert rows and rows[0][0] == "live", (
                    f"a name-keyed fold MOVED the node's state (cas={cas}): "
                    f"{rows}")
            # id-miss + NO name stays a (0,0) no-op (id-only/§6b legacy shapes)
            assert fold({"id": "no-such-id", "name": "",
                         "supersedes_by": "x"}, cas=False) == (0, 0)
            assert fold({"id": "no-such-id", "name": "",
                         "supersedes_by": "x"}, cas=True) == (0, 0)
        finally:
            sdk.close()

    def test_clobber_guard_second_mention_keeps_superseded(self):
        sdk = _fresh_sdk()
        try:
            sdk.create_entity("object", "strategy-A", objectKind="core:strategy")
            sdk._get_proj()._fold_object_superseded({
                "id": _oid(sdk, "strategy-A"), "name": "strategy-A",
                "supersedes_by": "B"})
            # a re-mention (ObjectRegistered) must NOT reset status
            sdk.create_entity("object", "strategy-A", objectKind="core:strategy")
            rows = sdk._get_proj().g.query(
                "MATCH (o:Object {name:$n}) RETURN o.status",
                params={"n": "strategy-A"}).result_set
            assert rows[0][0] == "superseded", "clobber guard failed"
        finally:
            sdk.close()

    def test_fold_returns_match_count(self):
        """#2164 Task 2 + #2242: _fold_object_superseded returns the
        CLASSIFIED tuple (folded, matched) — 0/1 folded replaced the bare
        matched-row count.

        Pre-#2164 the fold returned None and its Cypher was a bare MATCH…SET
        with no RETURN — a fold that matched 0 rows (missing Object, stale
        id) was indistinguishable from a successful fold. #2164 added the
        matched-row signal; #2242 split it into (folded, matched): folded =
        rows actually flipped by THIS fold (the CAS keep-first loser flips
        0), matched = rows bound (a terminal re-fold still binds the node —
        (0, 1) — which is how a present-but-terminal target is distinguished
        from genuine absence (0, 0)). The CAS-contract asserts pass cas=True
        EXPLICITLY (the default is False/blind — replay parity).
        """
        sdk = _fresh_sdk()
        try:
            fold = sdk._get_proj()._fold_object_superseded
            # #2242: the classified contract — (folded, matched). The CAS
            # contract asserts pass cas=True EXPLICITLY (the default is
            # False/blind — replay parity).
            # (1) fold on a MISSING Object id → (0,0) (fold-miss signal)
            assert fold({"id": "no-such-object", "name": "",
                          "supersedes_by": "strategy-B"}, cas=True) == (0, 0)
            # (1b) empty id AND empty name → (0,0) (early return)
            assert fold({"id": "", "name": "",
                          "supersedes_by": "strategy-B"}, cas=True) == (0, 0)
            # (2) fold on an EXISTING Object (create + resolve canonical id)
            sdk.create_entity("object", "strategy-A",
                              objectKind="core:strategy")
            oid = sdk._get_proj().g.query(
                "MATCH (o:Object {name:$n}) RETURN o.id",
                params={"n": "strategy-A"}).result_set[0][0]
            assert fold({"id": oid, "name": "strategy-A",
                          "supersedes_by": "strategy-B"}, cas=True) == (1, 1)
            # (3) re-fold (already superseded, same successor) → (0,1): the
            #     CAS keeps first-fold stamps (no re-SET) and reports the
            #     terminal target; stored values unchanged
            assert fold({"id": oid, "name": "strategy-A",
                          "supersedes_by": "strategy-B"}, cas=True) == (0, 1)
            # (3b) #3590 RETIRED the name branch: an id-less record is
            #      UNADDRESSABLE whatever name it carries, so it folds nothing
            #      even when a live carrier holds that name (the name-keyed
            #      fold could supersede a DIFFERENT incarnation under
            #      same-name coexistence — #3573's burial class). Its miss is
            #      classified one level up (rebuild_all's sweep / the
            #      reference fold's classifier), not here.
            assert fold({"id": "", "name": "strategy-A",
                          "supersedes_by": "strategy-B"}, cas=True) == (0, 0)
            assert fold({"id": "", "name": "no-such-object",
                          "supersedes_by": "strategy-B"}, cas=True) == (0, 0)
            rows = sdk._get_proj().g.query(
                "MATCH (o:Object {id:$id}) RETURN o.status, o.supersededBy",
                params={"id": oid}).result_set
            assert rows and rows[0][0] == "superseded"
            assert rows[0][1] == "strategy-B", \
                "re-fold must not clobber the successor"
        finally:
            sdk.close()


    def test_fold_cas_terminal_target_not_reflipped(self):
        """#2242: the LIVE-path fold (cas=True) against an already-terminal
        Object must NOT re-flip it — returns (0, 1) and leaves status,
        supersededBy, supersededAt BYTE-IDENTICAL (first-fold stamps kept).
        Pre-fix the fold was an unconditional SET — a divergent re-fold
        clobbered the successor."""
        sdk = _fresh_sdk()
        try:
            proj = sdk._get_proj()
            sdk.create_entity("object", "cas-A", objectKind="core:strategy")
            sdk.create_entity("object", "cas-B", objectKind="core:strategy")
            sdk.create_entity("object", "cas-C", objectKind="core:strategy")
            fold = proj._fold_object_superseded
            ts1 = "2026-09-07T00:00:00Z"
            cas_a = _oid(sdk, "cas-A")
            # cas=True EXPLICIT (the default is False/blind — replay parity)
            assert fold({"id": cas_a, "name": "cas-A",
                         "supersedes_by": "cas-B", "ts": ts1},
                        cas=True) == (1, 1)
            rows = proj.g.query(
                "MATCH (o:Object {name:$n}) "
                "RETURN o.status, o.supersededBy, o.supersededAt",
                params={"n": "cas-A"}).result_set
            assert rows and rows[0] == ["superseded", "cas-B", ts1], rows
            # divergent re-fold → CAS keeps first stamps, reports the loss
            ts2 = "2026-09-07T01:00:00Z"
            assert fold({"id": cas_a, "name": "cas-A",
                         "supersedes_by": "cas-C", "ts": ts2},
                        cas=True) == (0, 1)
            rows = proj.g.query(
                "MATCH (o:Object {name:$n}) "
                "RETURN o.status, o.supersededBy, o.supersededAt",
                params={"n": "cas-A"}).result_set
            assert rows and rows[0] == ["superseded", "cas-B", ts1], \
                "CAS must keep the FIRST fold's stamps on a terminal re-fold"
        finally:
            sdk.close()

    def test_fold_cas_terminal_id_no_name_fallback_to_other_live_node(self):
        """#2242 review round-1 P2 pin, kept across #3590's retirement of the
        fallback it was written against: a present-but-TERMINAL id node
        ((0,1)) must never reach a DIFFERENT live node, even when the event
        name resolves to one (a dup-name carrier). A buggy folded==0-only
        guard would fold the wrong live node under a name mismatch yet pass
        every terminal-refold test (which name the SAME terminal node).

        ⚠️ The mechanism this pin originally described is GONE: ``#3590``
        RETIRED the ``#2164 ISSUE B`` id→name fallback outright
        (``_fold_object_superseded`` is id-keyed only — see
        ``test_fold_id_miss_does_not_fall_back_to_name``), so there is no
        fallback left for a ``(0,1)`` result to wrongly take. The INVARIANT
        still holds and is still worth pinning: restoring any "id missed, try
        the name" branch reds this, because that branch folds the impostor."""
        sdk = _fresh_sdk()
        try:
            proj = sdk._get_proj()
            # terminal node T — superseded via a first fold
            sdk.create_entity("object", "carrier", objectKind="core:strategy")
            sdk.create_entity("object", "succ", objectKind="core:strategy")
            oid = proj.g.query(
                "MATCH (o:Object {name:'carrier'}) RETURN o.id",
            ).result_set[0][0]
            fold = proj._fold_object_superseded
            assert fold({"id": oid, "name": "carrier",
                          "supersedes_by": "succ"}, cas=True) == (1, 1)
            # a SECOND live node with the SAME name (raw CREATE — dup-name
            # carrier; create_entity dedupes by name, only raw writes reach
            # this shape)
            proj.g.query(
                "CREATE (o:Object {name:'carrier', status:'live', id:$id})",
                params={"id": "carrier-live-raw"})
            # id branch: oid node present but terminal -> (0,1); if the guard
            # were folded==0-only the fallback would fold the live carrier
            assert fold({"id": oid, "name": "carrier",
                          "supersedes_by": "succ"}, cas=True) == (0, 1)
            rows = proj.g.query(
                "MATCH (o:Object {id:'carrier-live-raw'}) "
                "RETURN o.status, o.supersededBy",
            ).result_set
            assert rows and rows[0] == ["live", None],                 f"fallback must NOT fold the dup-name LIVE carrier: {rows}"
        finally:
            sdk.close()

    def test_fold_cas_false_replay_keeps_blind_last_wins(self):
        """#2242: the REPLAY-path fold (default cas=False) keeps the legacy
        blind SET — a re-fold OVERWRITES status/supersededBy/supersededAt
        (last-wins — the pre-CAS rebuild contract; incarnation-reuse shapes
        delete→recreate→re-supersede depend on it)."""
        sdk = _fresh_sdk()
        try:
            proj = sdk._get_proj()
            sdk.create_entity("object", "bl-A", objectKind="core:strategy")
            sdk.create_entity("object", "bl-B", objectKind="core:strategy")
            sdk.create_entity("object", "bl-C", objectKind="core:strategy")
            fold = proj._fold_object_superseded
            bl_a = _oid(sdk, "bl-A")
            ts1 = "2026-09-07T00:00:00Z"
            # default cas=False (no cas= arg) — blind, pre-CAS parity
            assert fold({"id": bl_a, "name": "bl-A", "supersedes_by": "bl-B",
                         "ts": ts1}) == (1, 1)
            ts2 = "2026-09-07T01:00:00Z"
            assert fold({"id": bl_a, "name": "bl-A", "supersedes_by": "bl-C",
                         "ts": ts2}) == (1, 1)
            rows = proj.g.query(
                "MATCH (o:Object {name:$n}) "
                "RETURN o.status, o.supersededBy, o.supersededAt",
                params={"n": "bl-A"}).result_set
            assert rows and rows[0] == ["superseded", "bl-C", ts2], \
                "replay (cas=False) must be blind last-wins — pre-CAS parity"
        finally:
            sdk.close()


# ── Work-item fold (Step 7) ──────────────────────────────────────────

class TestWorkItemFold:
    # ⚠️ #11824 S0 MOVED these pins from `o.status` to the PIPELINE STAGE, and
    # that is the slice's deliverable rather than a test detail: the connector
    # folds used to write `in_progress` / `completed` as `Object.status`, which
    # the four-word lifecycle axis can no longer hold (they are POSITION words —
    # design §3.2). They now advance the CORE default work-item pipeline
    # (`pipeline.CORE_WORK_ITEM_PIPELINE`), so the words are preserved on the axis
    # they belong to and `status` stays a lifecycle word. The `isClosed` half is
    # asserted with each one because it is what reads a terminal stage as ended.
    def test_linear_card_completed(self):
        sdk = _fresh_sdk()
        try:
            sdk.create_entity("object", "TASK-42", objectKind="dev:issue")
            sdk.create_event("card done", "pm:cardCompleted",
                             object="TASK-42", endedAt="2026-08-18")
            rows = sdk._get_proj().g.query(
                "MATCH (o:Object {name:$n}) "
                "RETURN o.status, o.pipelineStageId, o.pipelineSlot, "
                "       o.pipelineOutcome, o.isClosed",
                params={"n": "TASK-42"}).result_set
            assert rows, "the fold did not reach the Object"
            status, stage, slot, outcome, closed = rows[0]
            assert stage == "done", rows
            assert (slot, outcome, closed) == ("terminal", "completed", True), \
                rows
            assert status is None, (
                "the connector fold wrote a LIFECYCLE word — a position word on "
                f"the status axis is the defect S0 removes: {status!r}")
        finally:
            sdk.close()

    def test_a_folded_close_ends_the_work_item_for_READERS(self):
        """The READER-side consequence of putting the folds on the POSITION axis.

        #13034's recorded decision (`live._terminal_excluded`, design §6) reads
        BOTH axes: a stage in the `terminal` slot is an ending exactly like a
        terminal lifecycle word. Moving the closing folds onto `done` therefore
        makes a closed card ENDED for every reader — where pre-S0
        `status='completed'` was never in `commit_ops.OBJECT_TERMINAL_STATUSES`
        and the card stayed CURRENT in `recall_state` and on the four search
        legs.

        That IS the intent (`status_folds` §4.1: the SLOT carries the meaning,
        a terminal stage is the ending, and unlike a lifecycle word it stays
        reopenable), and this row is what makes it a decision rather than an
        undisclosed side effect: the ending must be a FILTER over a reachable
        row, never a deletion, which is why the `include_superseded` leg is
        asserted too.
        """
        sdk = _fresh_sdk()
        try:
            sdk.create_entity("object", "the card", objectKind="dev:issue")

            def _served(include_superseded: bool = False) -> set[str]:
                return {
                    o["content"] for o in sdk.recall_state(
                        kind="dev:issue", limit=10, object_centric=True,
                        include_superseded=include_superseded)
                    if o.get("entity_type") == "object"}

            assert "the card" in _served(), (
                "the fixture must be served as CURRENT before the fold, or this "
                "row would pass on an unrelated exclusion")

            sdk.create_event("done", "pm:cardCompleted", object="the card")

            assert "the card" not in _served(), (
                "a card folded to the TERMINAL stage `done` is still served as "
                "current state — either the fold did not apply, or the reader "
                "rule stopped reading the position axis (#13034)")
            assert "the card" in _served(include_superseded=True), (
                "the ending must be a FILTER, not a deletion: "
                "`include_superseded` still reaches the closed card")
        finally:
            sdk.close()

    def test_github_issue_open_and_close(self):
        sdk = _fresh_sdk()
        try:
            sdk.create_entity("object", "the bug", objectKind="dev:issue")
            sdk.create_event("opened", "github.issue.open", object="the bug")
            rows = sdk._get_proj().g.query(
                "MATCH (o:Object {name:$n}) "
                "RETURN o.status, o.pipelineStageId, o.isClosed",
                params={"n": "the bug"}).result_set
            assert rows and tuple(rows[0][1:]) == ("in_progress", False), rows
            assert rows[0][0] is None, rows
            sdk.create_event("closed", "github.issue.closed", object="the bug")
            rows = sdk._get_proj().g.query(
                "MATCH (o:Object {name:$n}) "
                "RETURN o.status, o.pipelineStageId, o.isClosed",
                params={"n": "the bug"}).result_set
            assert rows and tuple(rows[0][1:]) == ("done", True), rows
            assert rows[0][0] is None, rows
            # …and the REOPEN fold clears the durable close, which is the reason
            # the closing fold targets a terminal STAGE and not the lifecycle
            # word `deprecated` (that ending is final; a stage is reopenable).
            sdk.create_event("reopened", "github.issue.reopened",
                             object="the bug")
            rows = sdk._get_proj().g.query(
                "MATCH (o:Object {name:$n}) "
                "RETURN o.status, o.pipelineStageId, o.isClosed",
                params={"n": "the bug"}).result_set
            assert rows and tuple(rows[0][1:]) == ("in_progress", False), (
                "github.issue.reopened did not reopen a closed work item: "
                f"{rows}")
        finally:
            sdk.close()

    def test_connector_fold_does_not_clobber_superseded(self):
        """M3-P1 (#2164 Task 11): the connector work-item fold must NOT
        reset a superseded Object back to in_progress/completed.

        A dual-tracked Object (connector work item AND conversationally
        superseded via the capture fold) must keep status='superseded' when
        a LATER connector lifecycle event lands — recall_state's default view
        excludes superseded/deprecated/archived/retracted, so an unguarded
        SET would silently resurrect the Object into the default view.
        BOTH families are driven here against the same superseded Object:
        github.issue.closed (completed family) then
        github.issue.reopened (in_progress family) — each would have
        clobbered status='superseded' pre-fix.
        """
        sdk = _fresh_sdk()
        try:
            sdk.create_entity("object", "TASK-9", objectKind="dev:issue")
            # #2164 capture fold: supersede the work item (repo convention —
            # the projection fold invoked by the commit path).
            sdk._get_proj()._fold_object_superseded({
                "id": _oid(sdk, "TASK-9"), "name": "TASK-9",
                "supersedes_by": "TASK-9-v2"})
            # completed family first — pre-fix this SET clobbers → completed.
            sdk.create_event("closed", "github.issue.closed", object="TASK-9")
            rows = sdk._get_proj().g.query(
                "MATCH (o:Object {name:$n}) RETURN o.status",
                params={"n": "TASK-9"}).result_set
            assert rows and rows[0][0] == "superseded", (
                "github.issue.closed clobbered superseded status -> "
                f"{rows[0][0] if rows else 'MISSING'}")
            # in_progress family — pre-fix this SET clobbers → in_progress.
            sdk.create_event("reopened", "github.issue.reopened",
                             object="TASK-9")
            rows = sdk._get_proj().g.query(
                "MATCH (o:Object {name:$n}) RETURN o.status",
                params={"n": "TASK-9"}).result_set
            assert rows and rows[0][0] == "superseded", (
                "github.issue.reopened clobbered superseded status -> "
                f"{rows[0][0] if rows else 'MISSING'}")
        finally:
            sdk.close()

    def test_connector_fold_still_works_on_live_object(self):
        """Regression for the M3-P1 guard: the WHERE (status IS NULL OR
        status <> 'superseded') must only skip SUPERSEDED Objects — a live
        Object's connector fold keeps working (#1725 reopen → in_progress
        family, the one kind not explicitly covered by the pre-existing
        live-Object tests above)."""
        sdk = _fresh_sdk()
        try:
            sdk.create_entity("object", "the live bug", objectKind="dev:issue")
            sdk.create_event("reopened", "github.issue.reopened",
                             object="the live bug")
            rows = sdk._get_proj().g.query(
                "MATCH (o:Object {name:$n}) RETURN o.pipelineStageId",
                params={"n": "the live bug"}).result_set
            assert rows and rows[0][0] == "in_progress", \
                "live-Object reopen fold regressed under the guard"
        finally:
            sdk.close()


    def test_connector_fold_does_not_resurrect_any_terminal_status(self):
        """#3309: the guard must exclude the WHOLE canonical Object terminal
        vocabulary, not the single hand-typed ``'superseded'`` literal it
        carried.

        The guard's own comment states its purpose — the Object "must NOT be
        silently resurrected into recall_state's default view" — and that view
        excludes all of ``commit_ops.OBJECT_TERMINAL_STATUSES`` (see this
        class's `..._does_not_clobber_superseded` docstring). Pre-fix only
        ``superseded`` was skipped, so a ``retracted`` (a REMOVED Object, per
        #2977) / ``deprecated`` / ``archived`` Object was set back to
        ``in_progress`` or ``completed`` by a connector lifecycle event: a
        removed Object made visible again.

        Both families are driven per status — ``github.issue.closed``
        (completed) then ``github.issue.reopened`` (in_progress); each would
        have clobbered the terminal status pre-fix.
        """
        from tortoise.commit_ops import OBJECT_TERMINAL_STATUSES

        for status in sorted(OBJECT_TERMINAL_STATUSES):
            sdk = _fresh_sdk()
            try:
                name = f"T-{status}"
                sdk.create_entity("object", name, objectKind="dev:issue")
                sdk._get_proj().g.query(
                    "MATCH (o:Object {name:$n}) SET o.status = $s",
                    params={"n": name, "s": status})
                for ev_kind in ("github.issue.closed", "github.issue.reopened"):
                    sdk.create_event("e", ev_kind, object=name)
                    rows = sdk._get_proj().g.query(
                        "MATCH (o:Object {name:$n}) "
                        "RETURN o.status, o.pipelineStageId",
                        params={"n": name}).result_set
                    assert rows and rows[0][0] == status, (
                        f"{ev_kind} resurrected a terminal Object: "
                        f"{status} -> {rows[0][0] if rows else 'MISSING'}")
                    # #11824 S0: the same latch must hold on the POSITION axis —
                    # the fold is a stage move now, so a dead Object must not
                    # acquire a stage either.
                    assert rows[0][1] is None, (
                        f"{ev_kind} wrote a pipeline stage onto a terminal "
                        f"Object: {rows[0][1]!r}")
            finally:
                sdk.close()


# ── Read side (Steps 8-9) ────────────────────────────────────────────

class TestReadSide:
    def test_object_result_carries_the_status_the_graph_holds(self):
        """#9974 R1: the read RENDERS the stored status.

        For a status-less birth the graph now holds NOTHING (the fabricated
        `live` is gone), and the hit renders that absence as ``"live"`` through
        ``live.status_as_reported`` — so a consumer comparing ``== "live"``
        keeps matching a live Object. (The raw node echo — ``create_object`` /
        ``get_entity`` — reports the record honestly: absent stays absent.)

        The anti-vacuity half is the second leg below: a status the caller DID
        supply (``draft``) is still carried, so this cannot pass by the read
        defaulting everything to live.
        """
        sdk = _fresh_sdk()
        try:
            sdk.create_entity("object", "the strategy", objectKind="core:strategy")
            hits = sdk.tortoise_fts_query("the strategy", kind="core:strategy",
                                          entity_type="object", limit=5)
            assert hits, "seeded object must be retrievable (kind-scoped)"
            # #9974 R1: the hit's `status` is a LIVENESS decoration, rendered
            # through `live.status_as_reported` — an absent stored status
            # reports live, so a consumer comparing `== "live"` keeps matching.
            assert hits[0]["status"] == "live", hits[0]

            sdk.create_entity("object", "the tactic", objectKind="core:strategy",
                              status="draft")
            hits = sdk.tortoise_fts_query("the tactic", kind="core:strategy",
                                          entity_type="object", limit=5)
            assert hits, "the draft object must be retrievable"
            assert hits[0]["status"] == "draft", hits[0]
        finally:
            sdk.close()

    def test_recall_state_filters_superseded_object(self):
        sdk = _fresh_sdk()
        try:
            sdk.create_entity("object", "old-strategy", objectKind="core:strategy")
            sdk.create_entity("object", "new-strategy", objectKind="core:strategy")
            sdk._get_proj()._fold_object_superseded({
                "id": _oid(sdk, "old-strategy"), "name": "old-strategy",
                "supersedes_by": "new-strategy"})
            default = [o["content"] for o in sdk.recall_state(
                kind="core:strategy", limit=10, object_centric=True)
                if o.get("entity_type") == "object"]
            assert "old-strategy" not in default, "superseded object must be filtered"
            with_all = [o["content"] for o in sdk.recall_state(
                kind="core:strategy", limit=10, object_centric=True,
                include_superseded=True)
                if o.get("entity_type") == "object"]
            assert "old-strategy" in with_all, "include_superseded must bring it back"
        finally:
            sdk.close()

    def test_recall_state_filter_covers_the_whole_object_vocabulary(self):
        """#3301: recall_state round-trips the ENTIRE canonical OBJECT
        vocabulary, not just 'superseded'. The default half is enforced
        end-to-end (the four search legs exclude the canonical set at the query
        layer and the read-surface filter consumes the same constant); the
        include_superseded half pins the terminal-inclusive wiring that the
        widening alone regressed."""
        from tortoise.commit_ops import OBJECT_TERMINAL_STATUSES
        sdk = _fresh_sdk()
        try:
            sdk.create_entity("object", "obj-live", objectKind="core:strategy")
            for st in sorted(OBJECT_TERMINAL_STATUSES):
                name = f"obj-{st}"
                sdk.create_entity("object", name, objectKind="core:strategy")
                sdk._get_proj().g.query(
                    "MATCH (o:Object {name:$n}) SET o.status=$st",
                    params={"n": name, "st": st})
            terminal_names = {f"obj-{s}" for s in OBJECT_TERMINAL_STATUSES}
            default = {o["content"] for o in sdk.recall_state(
                kind="core:strategy", limit=20, object_centric=True)
                if o.get("entity_type") == "object"}
            assert "obj-live" in default
            assert not (default & terminal_names), (
                "terminal Objects leaked into the default view: "
                f"{default & terminal_names}")
            with_all = {o["content"] for o in sdk.recall_state(
                kind="core:strategy", limit=20, object_centric=True,
                include_superseded=True)
                if o.get("entity_type") == "object"}
            assert terminal_names <= with_all, (
                "include_superseded must re-admit every canonical status; "
                f"missing {terminal_names - with_all}")
        finally:
            sdk.close()


# ── #10192: a lifecycle CARRIER requires the STATUS it implies ─────────────
#
# The record never stores `status` (`record_projection`'s `never_store` for both
# layer tables; the schema refuses the name in `props`), so the record's
# procedural fold (`public.object_current_status` / `subject_current_status`,
# `20261012000004_record_procedural.sql`) derives it from the CARRIERS alone:
# `superseded_by IS NOT NULL OR superseded_at IS NOT NULL -> 'superseded'`.
# A carrier written WITHOUT the status was therefore persisted, the record
# answered `'superseded'` for a node the graph holds `'live'`, and a
# record-sourced rebuild materialized it TERMINAL (`DA#9974`'s
# `cache_projection.DERIVED_FROM_RECORD` derives from the same carriers;
# `live._terminal_excluded` then drops it from recall and
# `entity_identity.resolve_entity_id` refuses its name).
#
# These tests pin BOTH halves of the fix: the incoherent write is now REFUSED at
# both entity doors (and leaves the graph and the journal untouched), and every
# coherent shape stays writable — the record's fold and the graph then agree.


def _carrier_row(sdk, label: str, name: str):
    """`(id, status, supersededBy, supersededAt)` for a fixture node (tuple)."""
    rows = sdk._get_proj().g.query(
        f"MATCH (n:{label} {{name:$n}}) "
        "RETURN n.id, n.status, n.supersededBy, n.supersededAt",
        params={"n": name}).result_set
    return tuple(rows[0]) if rows else None


def _records(journal) -> list[dict]:
    from tortoise.log import EventLog
    return list(EventLog(str(journal)).read_all())


def _make_carrier_entity(sdk, label: str, name: str) -> str:
    """Create the Object/Subject a carrier write will target; return its id."""
    if label == "Subject":
        sdk.create_subject(name, "core:organization")
    else:
        sdk.create_object(name, "core:strategy")
    row = _carrier_row(sdk, label, name)
    assert row, f"fixture did not create {label} {name!r}"
    return row[0]


class _LyingEq:
    """A non-`str` status whose `__eq__` answers `True` for ANYTHING (#12896).

    The guard must answer "may this value leave the dead state?" from the TYPE,
    never from a comparison the caller controls. `str.__eq__(stored, non_str)`
    returns `NotImplemented`, so a natural `stored == target` falls back to THIS
    reflected `__eq__` — measured: `status=_LyingEq()` on an `archived` row
    returned normally, the `status` property was REMOVED, and the row read
    `live`. It is unhashable on purpose (defining `__eq__` nulls `__hash__`),
    which is a shape a store can hold — but it is NOT the `in _np_terminal`
    membership door: on the PRE-fix tree the ENTAILMENT shortcut `continue`d
    first for a non-`str` target, so this fixture never reached the membership
    test (at HEAD the shortcut is gated on an exact `str`, so it is not live for
    a non-`str` at all). That door is pinned by the str-SUBCLASS arm of
    `test_a_str_subclass_cannot_forge_a_dead_word_or_crash_the_guard`
    (`_UnhashHonest`), not here.
    """

    def __eq__(self, other):
        return True

    __hash__ = None

    def __repr__(self):
        return "_LyingEq()"


class _RenderingLie(str):
    """A `str` whose CONTENT is honest and whose RENDERING lies (#12906).

    The guard normalises the target with `str.__str__`, but the DRIVER renders
    what it is given: `falkordb.helpers.quote_string` calls `len(v)` and
    `v.replace(...)` on the CALLER'S object. So a subclass could agree with the
    stored word by content (the entailed shortcut rightly reads "no transition
    asked for") and still store a different word. Measured at `70b37dcfc5` on an
    `archived` Object: `update_knowledge(oid, status=_RenderingLie('archived'))`
    returned normally and the row read `live` — a resurrection the guard never saw.
    """

    def replace(self, *args, **kwargs):
        return "live"


class _ZeroLen(str):
    """Content honest, `__len__` lying — `quote_string` renders it as `""`.

    Measured at `70b37dcfc5`: `status=_ZeroLen('archived')` on an `archived`
    Object returned normally and stored `''` (the same rendering hole as
    `_RenderingLie`, through `quote_string`'s `len(v) == 0` branch).
    """

    def __len__(self):
        return 0


#: Every carrier shape measured as ACCEPTED (and record-divergent) before the
#: #10192 guard: a pointer/stamp alone, a BLANK pointer (the record view's
#: `IS NOT NULL` reads `''` as a carrier), a carrier with an explicit LIVE status,
#: and a carrier with an explicit null status.
#:
#: ⚠️ The SNAKE spellings are in this list because the camel-only guard was
#: INERT against them (review P1): `record_projection._split_properties` sends a
#: props key to a record COLUMN through `snake`, so `superseded_by` reaches the
#: very `public.object.superseded_by` / `public.subject.superseded_by` column
#: the cached camel spelling reaches — and the record's `IS NOT NULL` fold then
#: reports a LIVE node as `'superseded'` exactly as it did for `supersededBy`.
#: A guard that only knew one spelling of the column it protects is not a guard.
_BARE_CARRIER_SHAPES = [
    pytest.param({"supersededBy": "v2"}, id="pointer-only"),
    pytest.param({"supersededAt": "2026-01-01T00:00:00Z"}, id="stamp-only"),
    pytest.param({"supersededBy": ""}, id="empty-pointer"),
    pytest.param({"status": "live", "supersededBy": "v2"},
                 id="live-status-with-pointer"),
    pytest.param({"status": None, "supersededBy": "v2"},
                 id="null-status-with-pointer"),
    pytest.param({"superseded_by": "v2"}, id="snake-pointer-only"),
    pytest.param({"superseded_at": "2026-01-01T00:00:00Z"},
                 id="snake-stamp-only"),
    pytest.param({"superseded_by": ""}, id="snake-empty-pointer"),
    pytest.param({"status": "live", "superseded_by": "v2"},
                 id="snake-live-status-with-pointer"),
    pytest.param({"status": None, "superseded_at": "2026-01-01T00:00:00Z"},
                 id="snake-null-status-with-stamp"),
]


class TestLifecycleCarrierCoherence:
    """#10192 — a POINTER without a STATUS is unwritable at both entity doors."""

    @pytest.mark.parametrize("label", ["Object", "Subject"])
    @pytest.mark.parametrize("carriers", _BARE_CARRIER_SHAPES)
    def test_a_bare_carrier_is_refused_and_writes_nothing(
            self, tmp_path, label, carriers):
        """The refused write is ATOMIC: no graph change, no journal record.

        The journal matters as much as the graph — it is the record's only
        producer, so "nothing journaled" is the row-level half of proof that no
        carrier can reach `public.object`/`public.subject` (no Postgres in this
        lane; the census query is handed over on the issue).
        """
        events = tmp_path / "events"
        events.mkdir()
        journal = events / "events.jsonl"
        sdk = TortoiseSDK(str(tmp_path / "t10192u.db"),
                          event_log_path=str(journal))
        try:
            oid = _make_carrier_entity(sdk, label, "zeta")
            before_row = _carrier_row(sdk, label, "zeta")
            before = len(_records(journal))
            with pytest.raises(ValueError) as exc:
                sdk.update_knowledge(oid, **carriers)
            msg = str(exc.value)
            carrier_keys = sorted(k for k in carriers if k.startswith("superseded"))
            assert carrier_keys[0] in msg, (
                f"the refusal must NAME the carrier it refused: {msg!r}")
            assert "status='superseded'" in msg, (
                f"the refusal must name the coherent triple: {msg!r}")
            assert "#10192" in msg, msg
            assert _carrier_row(sdk, label, "zeta") == before_row, (
                f"a REFUSED carrier write mutated the node: "
                f"{before_row} -> {_carrier_row(sdk, label, 'zeta')}")
            after = _records(journal)
            assert len(after) == before, (
                "a REFUSED carrier write appended journal records "
                f"{[r.get('type') for r in after[before:]]} — the record would "
                "receive the carrier it just refused")
            assert not [r for r in after
                        if r.get("type") == "EntityMutated"
                        and r.get("op") == "supersede"], (
                "a REFUSED write journaled an EntityMutated op=supersede")
        finally:
            sdk.close()

    def test_the_create_door_refuses_a_bare_carrier(self, tmp_path):
        """The CREATE door is the second way in — and it reaches EXISTING nodes.

        `_upsert_object`/`_upsert_subject` MERGE, so an ON-MATCH re-create lands
        on a live node too; a guard on `update_entity` alone was bypassable in
        one call. The refusal precedes the write, so the node is never minted.
        """
        events = tmp_path / "events"
        events.mkdir()
        journal = events / "events.jsonl"
        sdk = TortoiseSDK(str(tmp_path / "t10192c.db"),
                          event_log_path=str(journal))
        try:
            before = len(_records(journal))
            with pytest.raises(ValueError, match="lifecycle CARRIER"):
                sdk.create_object("eta", "core:strategy", supersededBy="eta-v2")
            assert _carrier_row(sdk, "Object", "eta") is None, (
                "a refused create_object(props={'supersededBy': ...}) minted "
                "the node anyway")
            with pytest.raises(ValueError, match="lifecycle CARRIER"):
                sdk.create_subject("eta-subj", "core:organization",
                                   supersededBy="eta-v3")
            assert _carrier_row(sdk, "Subject", "eta-subj") is None
            assert len(_records(journal)) == before, (
                "a refused create appended journal records")

            # …and the EXISTING-node route the MERGE opens is closed too: a
            # re-create by NAME of a live node cannot smuggle the carrier in.
            sdk.create_object("eta", "core:strategy")
            live_row = _carrier_row(sdk, "Object", "eta")
            with pytest.raises(ValueError, match="lifecycle CARRIER"):
                sdk.create_object("eta", "core:strategy", supersededBy="eta-v2")
            assert _carrier_row(sdk, "Object", "eta") == live_row, (
                "the ON-MATCH re-create wrote the carrier onto the live node")

            # …and the SNAKE spelling is the same channel (review P1): the
            # record column is `superseded_by`, so `_split_properties` maps
            # BOTH spellings onto it and the create door must refuse both.
            snake_before = len(_records(journal))
            with pytest.raises(ValueError, match="lifecycle CARRIER"):
                sdk.create_object("eta", "core:strategy",
                                  superseded_by="eta-v2")
            assert _carrier_row(sdk, "Object", "eta") == live_row, (
                "the ON-MATCH re-create smuggled the carrier in under the "
                "snake spelling")
            with pytest.raises(ValueError, match="lifecycle CARRIER"):
                sdk.create_subject("eta-subj", "core:organization",
                                   superseded_at="2026-01-01T00:00:00Z")
            assert _carrier_row(sdk, "Subject", "eta-subj") is None, (
                "a refused create_subject(superseded_at=...) minted the node")
            assert len(_records(journal)) == snake_before, (
                "a refused snake-spelling create appended journal records")

            # A BORN-superseded create names the status and stays writable.
            sdk.create_object("eta2", "core:strategy", status="superseded",
                              supersededBy="eta2-v2",
                              supersededAt="2026-01-01T00:00:00Z")
            row = _carrier_row(sdk, "Object", "eta2")
            assert row[1:] == ("superseded", "eta2-v2",
                               "2026-01-01T00:00:00Z"), row
        finally:
            sdk.close()

    @pytest.mark.parametrize("label", ["Object", "Subject"])
    def test_the_coherent_triple_is_accepted_and_journals_it(self, tmp_path,
                                                             label):
        """The shipped-producer shape (status + both carriers, ONE state map)."""
        events = tmp_path / "events"
        events.mkdir()
        journal = events / "events.jsonl"
        sdk = TortoiseSDK(str(tmp_path / "t10192t.db"),
                          event_log_path=str(journal))
        try:
            oid = _make_carrier_entity(sdk, label, "alpha")
            sdk.update_knowledge(oid, status="superseded", supersededBy="alpha-v2",
                              supersededAt="2026-01-01T00:00:00Z")
            row = _carrier_row(sdk, label, "alpha")
            assert row[1:] == ("superseded", "alpha-v2",
                               "2026-01-01T00:00:00Z"), row
            records = [r for r in _records(journal)
                       if r.get("type") == "EntityMutated"
                       and r.get("label") == label
                       and r.get("op") == "supersede"]
            assert records, "the coherent triple journaled no supersede record"
            state = records[-1]["state"]
            assert state.get("status") == "superseded", state
            assert state.get("supersededBy") == "alpha-v2", state
            assert state.get("supersededAt") == "2026-01-01T00:00:00Z", state
        finally:
            sdk.close()

    def test_a_coherent_un_supersede_lifts_the_row_and_a_status_only_write_cannot(
            self, tmp_path):
        """#12825's decided contract: the CLEAR lifts the row; the STATUS-ONLY cannot.

        ⛔ This test has now been restated TWICE, and the reason is worth keeping:
        the row is `superseded`, the one dead word with a DESIGNED WITHDRAWAL, so
        the guard must ask which WRITE the call performs rather than which WORD it
        names.

        * `status='live'` **plus both carriers cleared** — the record-carried
          un-supersede (`cache_projection`'s NOT MONOTONE bullet): the graph goes
          live and a rebuild derives nothing, so graph and record AGREE. Allowed.
        * `status='live'` **alone** — refused: the carriers stay set, so the record
          still reads 'superseded' against a live graph. That divergence is the
          whole reason the guard exists.

        #12745 refused both. #12790 restated this test to match. #12825 then
        corrected the refusal for the designed withdrawal (#12745 had taken its
        dead set from the RECALL set, which answers "which stored states are dead"
        and not "which states a writer may mint") and did not restate this test
        back — which is what made `test (a)` red on `main` at `14be041b8f`.
        """
        events = tmp_path / "events"
        events.mkdir()
        journal = events / "events.jsonl"
        sdk = TortoiseSDK(str(tmp_path / "t10192x.db"),
                          event_log_path=str(journal))
        try:
            oid = _make_carrier_entity(sdk, "Object", "beta")
            sdk.update_knowledge(oid, status="superseded", supersededBy="beta-v2",
                              supersededAt="2026-01-01T00:00:00Z")
            superseded_row = _carrier_row(sdk, "Object", "beta")
            assert superseded_row[1:] == ("superseded", "beta-v2",
                                          "2026-01-01T00:00:00Z"), (
                superseded_row)

            # ── the STATUS-ONLY shape is the incoherent one, and it is refused ──
            journal_before = len(_records(journal))
            with pytest.raises(ValueError, match="is TERMINAL for a :Object"):
                sdk.update_knowledge(oid, status="live")
            assert _carrier_row(sdk, "Object", "beta") == superseded_row, (
                "a REFUSED status-only lift still moved the row — the refusal "
                "must leave it exactly as it stood")
            assert len(_records(journal)) == journal_before, (
                "a refused status-only lift appended a journal record — the "
                "refusal must land before any write reaches the stream")

            # ── the COHERENT shape lifts it: both carriers go NULL ──
            sdk.update_knowledge(oid, status="live", supersededBy=None,
                              supersededAt=None)
            lifted = _carrier_row(sdk, "Object", "beta")
            assert lifted[1:] == ("live", None, None), (
                "the record-carried un-supersede must make the row CURRENT with "
                f"both carriers cleared, so a rebuild derives nothing: {lifted}")

            # ── and the SNAKE spelling of a clear is accepted too (`None` is not
            # a carrier under either spelling). Its EFFECT is NOT pinned here: a
            # snake carrier SET is what writes the shadow property (#12782).
            sdk.update_knowledge(oid, superseded_by=None, superseded_at=None)

            # ── a BARE carrier CLEAR (no `status` key at all) is still ACCEPTED
            # on a superseded row and leaves the row TERMINAL: with no status
            # named there is no transition to ask about, so the guard above never
            # runs — the carriers clear while the row stays dead. Restated from
            # the assertion #12790 dropped when it rewrote this test: the
            # BEHAVIOUR never changed, only its pin did (#12882 review F2).
            bare = _make_carrier_entity(sdk, "Object", "gamma")
            sdk.update_knowledge(bare, status="superseded",
                              supersededBy="gamma-v2",
                              supersededAt="2026-01-01T00:00:00Z")
            sdk.update_knowledge(bare, supersededBy=None, supersededAt=None)
            cleared = _carrier_row(sdk, "Object", "gamma")
            assert cleared[1:] == ("superseded", None, None), (
                "the bare carrier CLEAR did not clear both columns while "
                f"leaving the row dead: {cleared}")
        finally:
            sdk.close()

    @pytest.mark.parametrize("target,expected", [
        (["live"], "was refused|refused a non-string status"),
        ({"a": 1}, "was refused|refused a non-string status"),
        (123, "was refused|refused a non-string status"),
        (True, "was refused|refused a non-string status"),
        (None, "is TERMINAL for a :Object"),
        (_LyingEq(), "was refused|refused a non-string status"),
    ], ids=["list", "dict", "int", "bool", "none", "lying-eq"])
    def test_a_non_string_or_absent_status_cannot_lift_a_dead_row(
            self, tmp_path, target, expected):
        """⛔ The question is "is this in the DEAD set", never "is this a string".

        An earlier cut skipped the whole check when the target was not a `str`,
        so a dead Object went CURRENT without naming a dead word. Measured, per
        target, on the pre-fix tree:

            status=['live']   STORED as-is   -> in no dead set -> reads current
            status=123        STORED as-is   -> same
            status=True       STORED as-is   -> same
            status={'a': 1}   DEGRADED to null (#7369) -> property ABSENT
            status=None       property REMOVED

        ⛔ NOT "all five store a wrong word": two of the five end with NO status at
        all — the dict silently, through #7369's null degradation, and `None` by
        construction. §4.3 settles those two ("an Object created without a status
        stores none and reads as `live`"), so all five make a DEAD row current
        again, by two different mechanisms. That difference is why the assertion
        below is about the ROW, not about the stored value.

        ⛔ NOT "a row whose carriers still describe a supersession": this fixture
        is a plain create force-set to `archived`, so it carries no supersession at
        all. The harm is the resurrection; a superseded row adds the graph/record
        divergence, which is a different test.

        The bare carrier clear (no `status` key at all) remains accepted above;
        only naming a status that leaves the dead state is refused.

        ⛔ WHICH REFUSAL ANSWERS, and why it is per-parameter (#12869). This pin
        asks one question — "did the dead row move?" — and two doors now answer
        it, in a fixed order. The Object write door refuses a non-string that
        would LAND (`['live']`, `123`, `True`) BEFORE this guard runs, so those
        three get the TYPE message; the three that are DROPPED by the `#7369`
        gate (`{'a': 1}`, `None`, and the non-persistable `_LyingEq()`) reach
        this guard, which refuses them as a resurrection. The expected message
        is therefore per-parameter: both arms are an atomic `ValueError`, so the
        claim this pin exists for is unchanged, and a mutant that lets any one of
        them through still reddens the row assertion.
        """
        sdk = TortoiseSDK(str(tmp_path / "t10192n.db"))
        try:
            oid = _make_carrier_entity(sdk, "Object", "delta")
            sdk._get_proj().g.query(
                "MATCH (o:Object {id:$id}) SET o.status='archived'",
                params={"id": oid})
            before = _carrier_row(sdk, "Object", "delta")
            assert before[1] == "archived", before
            # ⚠️ #11824 S0: the refusal comes from ONE of two doors, and which
            # one depends on the value. A non-string (list/dict/int/bool) is
            # refused by the VOCABULARY door ("was refused: a status must be a
            # plain string"). An explicit ``None`` is the DELETE sentinel, so it
            # passes the vocabulary door and is refused HERE — by the dead-set
            # transition guard, because deleting the status of a DEAD row is
            # itself the resurrection. The ROW assertion below is the invariant
            # both doors must keep.
            with pytest.raises(ValueError,
                               match=r"was refused|refused the status transition"):
                sdk.update_knowledge(oid, status=target)
            assert _carrier_row(sdk, "Object", "delta") == before, (
                "a refused resurrection moved the row: " +
                repr(_carrier_row(sdk, "Object", "delta")))
        finally:
            sdk.close()

    @pytest.mark.parametrize("target", [_RenderingLie("archived"),
                                        _ZeroLen("archived")],
                             ids=["replace-lie", "len-lie"])
    def test_a_rendering_lie_cannot_move_a_dead_row(self, tmp_path, target):
        """The guard's word and the WRITTEN word must be the same object (#12906).

        Normalising only the guard's copy left `props` carrying the caller's
        subclass, and the driver renders that object with its own `replace`/
        `len` — so a target that AGREES with the stored word by content, and is
        therefore the no-op re-assert the entailed shortcut knowingly lets
        through, could still store whatever its rendering produced. Measured on
        the pre-fix tree (`70b37dcfc5`), both params below were ACCEPTED and
        MOVED the row: `replace-lie` -> `'live'`, `len-lie` -> `''`.

        The invariant pinned is the ROW, not the outcome: a same-content
        re-assert is legitimately accepted (it asks for no transition), so this
        asserts that whatever the door answers, the dead row does not move and
        never comes back CURRENT.

        ⚠️ **UPDATED BY #11824 S0 — the row now CANONICALISES, and that is the
        point of the update, not a relaxation.** `archived` is a declared
        SYNONYM of `deprecated` (design §3.1: *ended, and nothing replaced it*),
        and the write door stores the canonical word — silently (AX §1). So a
        same-content re-assert of `archived` rewrites the stored word to
        `deprecated`. What the pin still demands, and now demands more sharply,
        is the thing it was written for: the store writes the word the DOOR read,
        never the caller's RENDERING — so `live`/`""` (what the two lies below
        produce) must never appear on the row.
        """
        sdk = TortoiseSDK(str(tmp_path / "t12906.db"))
        try:
            oid = _make_carrier_entity(sdk, "Object", "epsilon")
            sdk._get_proj().g.query(
                "MATCH (o:Object {id:$id}) SET o.status='archived'",
                params={"id": oid})
            before = _carrier_row(sdk, "Object", "epsilon")
            assert before[1] == "archived", before
            try:
                sdk.update_knowledge(oid, status=target)
            except ValueError as exc:
                assert "was refused" in str(exc), exc
            after = _carrier_row(sdk, "Object", "epsilon")
            assert after[0] == before[0] and after[2:] == before[2:], (
                "a rendering lie moved a DEAD row's carriers: "
                f"{before} -> {after}")
            assert after[1] == "deprecated", (
                "the store did not write the CANONICAL word the door read — the "
                f"guard approved the CONTENT and the store wrote the RENDERING: "
                f"{before} -> {after}")
            assert after[1] != "live" and after[1] != "", (
                f"a rendering lie RESURRECTED the row: {after}")
        finally:
            sdk.close()

    def test_a_hostile_repr_cannot_replace_the_refusal(self, tmp_path):
        """A caller-controlled `__repr__` must not escape as its own exception.

        The refusal interpolates the refused target with `!r`. For a non-`str`
        target that is the CALLER'S `__repr__`, so a target that raises there
        turned the promised `ValueError` into its own class (measured at
        `70b37dcfc5`: `RuntimeError` — review cycle 8 P3). Not a fail-open (the
        refusal still precedes the write and the row was unchanged), but the
        contract this door advertises is "refuse with ValueError".
        """

        class _BoomRepr:
            def __eq__(self, other):
                return True

            __hash__ = None

            def __repr__(self):
                raise RuntimeError("repr-boom")

        sdk = TortoiseSDK(str(tmp_path / "t12906r.db"))
        try:
            oid = _make_carrier_entity(sdk, "Object", "zeta")
            sdk._get_proj().g.query(
                "MATCH (o:Object {id:$id}) SET o.status='archived'",
                params={"id": oid})
            before = _carrier_row(sdk, "Object", "zeta")
            # #11824 S0: a non-string is refused by the VOCABULARY door, whose
            # message renders the value — so this still pins #12906 P3 exactly:
            # a caller-controlled `__repr__` must not escape as its own class.
            with pytest.raises(ValueError, match="was refused"):
                sdk.update_knowledge(oid, status=_BoomRepr())
            assert _carrier_row(sdk, "Object", "zeta") == before, before

            # ⛔ AND THE ID OPERAND (review cycle 9 P3): the same message
            # interpolates `id_val`, so an id whose `__repr__` raises escaped as
            # its own class too (measured: `RuntimeError: id-repr-boom`).
            class _IdLie(str):
                def __repr__(self):
                    raise RuntimeError("id-repr-boom")

            with pytest.raises(ValueError, match="is TERMINAL for a :Object"):
                sdk.update_knowledge(_IdLie(oid), status="live")
            assert _carrier_row(sdk, "Object", "zeta") == before, before
            # ⛔ AND THE CONFLICTING-WRITE door, ONE BRANCH ABOVE it (review cycle
            # 10 P3): that refusal renders `id_val` too, so the same id object
            # escaped as `RuntimeError: id-repr-boom` there. One helper for both
            # doors, so this pins the sibling the earlier fix walked past.
            class _StatusKey(str):
                def __str__(self):
                    return "status"

            with pytest.raises(ValueError, match="CONFLICTING status write"):
                sdk.update_knowledge(
                    _IdLie(oid),
                    **{_StatusKey("superseded"): "superseded", "status": "live"})
            assert _carrier_row(sdk, "Object", "zeta") == before, before
        finally:
            sdk.close()

    def test_a_str_subclass_cannot_forge_a_dead_word_or_crash_the_guard(
            self, tmp_path):
        """`isinstance(x, str)` is not the boundary — a SUBCLASS keeps the
        caller's `__eq__`/`__hash__`, and the guard consulted both (#12896,
        review cycles 6 and 7). Measured on a dead `archived` Object:

            class HashCollide(str):          # content 'banana', lying __eq__
                __hash__ = lambda self: hash("archived")
                __eq__ = lambda self, other: True

            status=HashCollide('banana')  ACCEPTED -> 'archived' -> 'banana'

        ⛔ THROUGH THE **ENTAILMENT** SHORTCUT, not the dead→dead membership.
        The comparison `'archived' == HashCollide('banana')` consults the
        SUBCLASS's own `__eq__` FIRST — CPython gives a proper subclass priority
        for the reflected comparison, BEFORE any content comparison (measured: a
        logging `__eq__` is called with `'archived'` and `str`'s comparison never
        runs; `str.__eq__('archived', HashCollide('banana'))` on its own is
        `False`). Note this is a DIFFERENT mechanism from the non-`str` case: there
        `str.__eq__(s, non_str)` returns `NotImplemented` and Python falls back to
        the reflected call. Either way the write looked like "no transition asked
        for". That shortcut runs FIRST, so it shadows the membership line for any
        shape that lies about equality —
        which is why a colliding `__hash__` ALONE does not lift a row (`in` also
        compares, so a hash-only subclass whose content is `'banana'` is
        REFUSED, measured).

        The `TypeError` is a THIRD shape, and it needs an unhashable subclass
        whose `__eq__` does NOT answer `True` — that one passes the entailment
        check, reaches the membership test, and hashes:

            class UnhashHonest(str):          # __eq__ never returns True
                def __eq__(self, other): return other == "zzz"
            status=UnhashHonest('banana')  TypeError: unhashable type

        All three are closed by normalising the target with `str.__str__` (which
        cannot consult an override) before ANY decision, so the guard reads the
        value the store holds. The gate is `issubclass(type(x), str)`, not
        `isinstance`: an object can claim `__class__ = str`, `isinstance`
        believes it, and `str.__str__` on such an impostor RAISES (the impostor
        arm below) — it is not a `str` word, so it takes the non-`str` path.

        ⛔ The last arm pins the OTHER side: normalising must not over-refuse. A
        plain subclass whose content IS the dead word is the same no-op it was —
        `S('archived')` on an `archived` row is ACCEPTED and the row is unchanged.
        """

        class _HashCollide(str):
            def __hash__(self):
                return hash("archived")

            def __eq__(self, other):
                return True

        class _UnhashHonest(str):
            def __eq__(self, other):
                return other == "zzz"

        class _Impostor:
            __class__ = str

            def __eq__(self, other):
                return True

        class _PlainSub(str):
            pass

        sdk = TortoiseSDK(str(tmp_path / "t12882s.db"))
        try:
            oid = _make_carrier_entity(sdk, "Object", "eta")
            sdk._get_proj().g.query(
                "MATCH (o:Object {id:$id}) SET o.status='archived'",
                params={"id": oid})
            before = _carrier_row(sdk, "Object", "eta")
            assert before[1] == "archived", before

            for target, label in ((_HashCollide("banana"), "entailed-door"),
                                  (_UnhashHonest("banana"),
                                   "unhashable membership"),
                                  (_Impostor(), "__class__ impostor")):
                # #11824 S0: the VOCABULARY door refuses first, and it reads the
                # content with `str.__str__` behind an `issubclass(type(x), str)`
                # gate — the two devices this test exists to require. The
                # dead-row assertion is unchanged.
                with pytest.raises(ValueError, match="was refused"):
                    sdk.update_knowledge(oid, status=target)
                assert _carrier_row(sdk, "Object", "eta") == before, (
                    f"a {label} target moved the dead row: "
                    + repr(_carrier_row(sdk, "Object", "eta")))

            # normalising must not over-refuse: same CONTENT as the dead word.
            # ⚠️ #11824 S0: `archived` is a declared SYNONYM of `deprecated`, so
            # the re-assert is still ACCEPTED — and the store now writes the
            # canonical word (that IS the door's contract, and it is silent per
            # AX §1). What must not happen is the row going CURRENT.
            sdk.update_knowledge(oid, status=_PlainSub("archived"))
            after = _carrier_row(sdk, "Object", "eta")
            assert after == (before[0], "deprecated", *tuple(before[2:])), (
                "a plain `str` subclass re-asserting the dead word did not land "
                "as the canonical synonym: " + repr(after))
        finally:
            sdk.close()

    @pytest.mark.parametrize("target,expected", [
        (["live"], "was refused|refused a non-string status"),
        ({"a": 1}, "was refused|refused a non-string status"),
        (123, "was refused|refused a non-string status"),
        (True, "was refused|refused a non-string status"),
        (None, "is TERMINAL for a :Object"),
        (_LyingEq(), "was refused|refused a non-string status"),
    ], ids=["list", "dict", "int", "bool", "none", "lying-eq"])
    def test_a_composed_non_string_status_is_refused_too(self, tmp_path, target,
                                                        expected):
        """The allowance is a COHERENCE test, not a licence for any value.

        The three conjuncts that admit the un-supersede read the KEY supplied
        (`_status_key_leaves_the_dead_state`) and the carriers the call CLEARS —
        never the value written. So a non-string target that also clears both
        carriers rode the coherent shape into the row, measured at the head
        before this pin existed:

            status=123     + both clears -> (123, None, None)
            status=['live'] + both clears -> (['live'], None, None)
            status=None    + both clears -> (None, None, None)
            status={'a':1} + both clears -> (None, None, None)   (#7369)

        — i.e. the row went CURRENT on a word nobody can read, and for the two
        null shapes the record kept folding `superseded` against a current graph
        (#9974's damage direction, one call away). The settled decision on
        #12827 licenses exactly ONE shape (`status='live'` with both carriers
        cleared), so the allowance now requires a `str` target.

        ⛔ This is NOT a vocabulary check: an undeclared STRING word is still
        accepted (#10371 / #8166 — an `:Object`'s status is unvalidated). Only the
        non-string half is pinned here.

        ⛔ AND ON `#12869`'s HEAD THE NON-STRING HITS THE TYPE DOOR FIRST. A
        non-string that would LAND is refused by the Object write door before
        this allowance is ever reached, so `list`/`int`/`bool` carry the TYPE
        message and the three DROPPED shapes (`dict`, `None`, `_LyingEq()`) carry
        the TERMINAL one. Both are atomic refusals and the row assertion is what
        the pin is for, so the expectation is per-parameter rather than widened
        to a substring that would also match a message this guard no longer
        emits.
        """
        sdk = TortoiseSDK(str(tmp_path / "t12882c.db"))
        try:
            oid = _make_carrier_entity(sdk, "Object", "zeta")
            sdk.update_knowledge(oid, status="superseded",
                              supersededBy="zeta-v2")
            assert _carrier_row(sdk, "Object", "zeta")[1:] == (
                "superseded", "zeta-v2", None)
            before = _carrier_row(sdk, "Object", "zeta")
            # #11824 S0: a non-string target is refused before it can ride into
            # the row — by the VOCABULARY door for a list/dict/int/bool, and by
            # the dead-set transition guard for an explicit ``None`` (the DELETE
            # sentinel, which the vocabulary door passes through by design).
            # Either way the composed shape can no longer reach the row, which is
            # a STRONGER result than the allowance's `str` requirement.
            with pytest.raises(ValueError,
                               match=r"was refused|refused the status transition"):
                sdk.update_knowledge(oid, status=target,
                                     supersededBy=None, supersededAt=None)
            assert _carrier_row(sdk, "Object", "zeta") == before, (
                "a refused composed write moved the row: "
                + repr(_carrier_row(sdk, "Object", "zeta")))
        finally:
            sdk.close()

    def test_a_non_string_status_on_a_LIVE_row_is_REFUSED_now_the_door_closed(
            self, tmp_path):
        """⛔ **INVERTED BY #11824 S0** — this pin used to keep the hole OPEN.

        It was written to prove the #10371 guard was a TRANSITION guard and not a
        vocabulary: a live row accepted `status=['live']`, and the docstring said
        so plainly ("a live row accepts these exactly as it did before — the
        vocabulary hole for Objects is #10371 and needs per-kind resolution").
        That reading is now retired by the model: there IS no per-kind *status*
        resolution to wait for, because a pack's own words are its pipeline
        STAGES (design §4.2, and the `OVERRIDES` (2) reversal recorded on
        #11824), so ONE four-word set is correct for every knowledge kind.

        So the hole this test protected is CLOSED, and the assertion is inverted:
        a non-string is refused on a live row exactly as on a dead one, and the
        row is untouched. ⚠️ The seed is kept, and kept for the same reason it was
        added (#12882 review R1) — the stored word must be a real one so the
        refusal cannot pass for the `if not _np_stored: continue` exit.
        """
        sdk = TortoiseSDK(str(tmp_path / "t10192v.db"))
        try:
            oid = _make_carrier_entity(sdk, "Object", "epsilon")
            sdk.update_knowledge(oid, status="live")
            assert _carrier_row(sdk, "Object", "epsilon")[1] == "live"
            before = _carrier_row(sdk, "Object", "epsilon")
            with pytest.raises(ValueError, match="was refused"):
                sdk.update_knowledge(oid, status=["live"])
            assert _carrier_row(sdk, "Object", "epsilon") == before, (
                "a REFUSED non-string status moved a live row: "
                + repr(_carrier_row(sdk, "Object", "epsilon")))
        finally:
            sdk.close()

    def test_an_explicit_None_is_still_the_documented_status_DELETE(
            self, tmp_path):
        """⛔ #11824 S0 must not turn a LEGAL write into a refusal (#1934).

        `_sanitize_props` states the contract: *"`None` REMOVES the property
        outright (`SET n += $props` with a None deletes it)"*, and
        `record_reconcile` / `cache_projection` both model
        `update_entity(obj, status=None)` as a real CLEAR on a non-terminal row.
        The new vocabulary door ran on that same bag, so `None` hit its
        non-`str` shape arm first and a documented delete started raising —
        a silent behaviour change in a slice that did not intend one.

        Both knowledge doors are pinned: `None` is the delete sentinel and
        passes through untouched, while a `None`-shaped SPELLING of a bad word
        (a non-string that is not `None`) is still refused.
        """
        sdk = TortoiseSDK(str(tmp_path / "t11824none.db"))
        try:
            oid = _make_carrier_entity(sdk, "Object", "omicron")
            sdk.update_knowledge(oid, status="live")
            assert _carrier_row(sdk, "Object", "omicron")[1] == "live"

            sdk.update_knowledge(oid, status=None)
            assert _carrier_row(sdk, "Object", "omicron")[1] is None, (
                "an explicit None must DELETE the status property, not raise")

            # …and the creation door agrees (the Object's status is absent).
            sdk.create_entity("object", "pi-none", status=None)
            assert _carrier_row(sdk, "Object", "pi-none")[1] is None

            # CONTROL: the delete sentinel is `None` and nothing else — a
            # non-string non-None value is still refused as a status.
            with pytest.raises(ValueError, match="was refused"):
                sdk.update_knowledge(oid, status=["live"])

            # ⛔ ONE FIELD, ONE ANSWER (#11824 S0 review, P3): the leftover
            # #1423 `:Subject` block BELOW the family door kept refusing `None`
            # with the pre-S0 text, so `create_entity('subject', status=None)`
            # succeeded (the family door exempts it) while
            # `update_entity(<subject>, status=None)` raised — and the Object
            # arm accepted the same value. All three must agree, which is what
            # this leg pins; the mutant is deleting the `_status is not None`
            # conjunct in that block.
            sid = _make_carrier_entity(sdk, "Subject", "tau")
            sdk.update_knowledge(sid, status="live")
            assert _carrier_row(sdk, "Subject", "tau")[1] == "live"
            sdk.update_knowledge(sid, status=None)
            assert _carrier_row(sdk, "Subject", "tau")[1] is None, (
                "the :Subject door must answer `None` the way the :Object door "
                "and the create door do — as a property DELETE")
            # …and a non-`str` is still REFUSED on this door (whichever of the
            # two arms answers — since S0 the rendered word reaches the shared
            # lifecycle door first, so the refusal is that door's).
            with pytest.raises(ValueError, match=r"was refused"):
                sdk.update_knowledge(sid, status=["live"])
        finally:
            sdk.close()

    @pytest.mark.parametrize("target,expected", [
        (["live"], "was refused|refused a non-string status"),
        ({"a": 1}, "was refused|refused a non-string status"),
        (123, "was refused|refused a non-string status"),
    ], ids=["list", "dict", "int"])
    def test_a_non_string_stored_status_does_not_crash_the_dead_set(
            self, tmp_path, target, expected):
        """A non-`str` STORED value is not a dead WORD — and is REFUSED, not raised.

        `_np_dead` used to run `s in _np_terminal` over EVERY stored value, so an
        unhashable one (`['live']`) raised `TypeError: unhashable type: 'list'`
        out of `update_entity` (#12869). The entailed shortcut had been hiding it
        for the IDEMPOTENT case only — `['live'] == ['live']` was True, so the
        write `continue`d — which means tightening that shortcut's type check
        REVEALED the traceback as a REGRESSION on an idempotent rewrite (measured
        on `d2872f0967`).

        ⛔ The assertion is "did not raise `TypeError`, row is intact" — NOT a
        claim about which word lands. ⚠️ #11824 S0 moved the PRODUCER of such a
        row: a non-string `status` can no longer be written through the door at
        all, so the stored non-string is seeded OUT OF BAND here — which is
        exactly the legacy population the guard must still not crash on.
        """
        sdk = TortoiseSDK(str(tmp_path / "t12869dead.db"))
        try:
            oid = _make_carrier_entity(sdk, "Object", "kappa")
            sdk._get_proj().g.query(
                "MATCH (o:Object {id:$id}) SET o.status=$s",
                params={"id": oid, "s": target})
            before = _carrier_row(sdk, "Object", "kappa")
            assert before[0] == oid, before
            # ⚠️ NOT `before[1] == target`: the engine DEGRADES an unstorable
            # value (a map) to NULL before this reader ever sees it — the #7369
            # degradation the original docstring already recorded. What matters
            # here is that the STORED shape does not crash the door.
            # The write under test must complete: `TypeError` out of a door that
            # promises a refusal is the defect this pins shut. A non-string
            # TARGET is refused by the vocabulary door, cleanly.
            with pytest.raises(ValueError, match="was refused"):
                sdk.update_knowledge(oid, status=target)
            assert _carrier_row(sdk, "Object", "kappa") == before
        finally:
            sdk.close()

    def test_a_stamp_on_an_already_superseded_node_is_still_writable(
            self, tmp_path):
        """The guard tests the RESULT status, not the presence of a `status` arg.

        Adding a stamp to a node that is ALREADY `'superseded'` leaves the graph
        and the record's carrier-derived fold agreeing, so it is not a
        divergence and must not be an over-block.
        """
        sdk = TortoiseSDK(str(tmp_path / "t10192s.db"))
        try:
            oid = _make_carrier_entity(sdk, "Object", "gamma")
            sdk.update_knowledge(oid, status="superseded", supersededBy="gamma-v2")
            assert _carrier_row(sdk, "Object", "gamma")[1:] == ("superseded",
                                                                "gamma-v2",
                                                                None)
            sdk.update_knowledge(oid, supersededAt="2026-02-02T00:00:00Z")
            assert _carrier_row(sdk, "Object", "gamma")[1:] == (
                "superseded", "gamma-v2", "2026-02-02T00:00:00Z")
            # Same under the snake spelling: the guard reads the RESULT status,
            # so it is a coherence guard and not a ban on a property name.
            sdk.update_knowledge(oid, superseded_by="gamma-v3")
            assert _carrier_row(sdk, "Object", "gamma")[1:] == (
                "superseded", "gamma-v2", "2026-02-02T00:00:00Z")
        finally:
            sdk.close()

    @pytest.mark.parametrize("spelling", ["supersededBy", "superseded_by",
                                          "SupersededBy"])
    def test_every_spelling_of_a_carrier_is_the_same_record_column(
            self, spelling):
        """The guard's surface IS the record's surface (review P1).

        This is the property the finding turned on: the door's carrier set must
        be derived from the COLUMNS `_split_properties` writes, not from a
        hand-written pair of prop names — `snake` maps every camel/mixed/snake
        spelling onto the one `superseded_by` column, so one column has many
        spellings and a guard that lists one of them protects none of them.
        """
        from tortoise.record_projection import snake

        assert snake(spelling) == "superseded_by", (
            f"{spelling!r} does not reach the record's carrier column")
        # The door must recognise it for that reason — through the SAME rule.
        from tortoise.sdk import _lifecycle_carrier_keys

        assert _lifecycle_carrier_keys({spelling: "v2"}) == {
            spelling: "superseded_by"}, (
            f"the guard does not treat {spelling!r} as a carrier; it would "
            "write public.object.superseded_by without a status (#10192)")

    def test_the_mirror_shape_is_not_refused_by_the_carrier_guard(self, tmp_path):
        """`status='superseded'` with NO carrier: OUT of the CARRIER guard's
        scope, and REFUSED by the #12592 dual.

        ⛔ Two different rules, and this case exists to keep them apart.
        #10192's guard asks "does a CARRIER have the status it implies" — it kept
        this mirror shape out of scope by decision, and it STILL does (the refusal
        below is not its message). What refuses it now is the dual rule
        ONTOLOGY §4.3 states for the word itself: *"`superseded` means replaced:
        it names the successor"*, so a row that says the word and names nothing is
        a state the model does not have — #12592. The write is refused ATOMICALLY,
        so the shape cannot be minted through this door at all; a graph that
        already holds it (an older SDK, a raw producer, a pre-guard journal) is
        untouched, and the REPLAY half of §4.3 still restores it (pinned in
        `tests/test_12592_supersede_door.py`).
        """
        sdk = TortoiseSDK(str(tmp_path / "t10192m.db"))
        try:
            oid = _make_carrier_entity(sdk, "Subject", "delta")
            before = _carrier_row(sdk, "Subject", "delta")
            with pytest.raises(ValueError) as exc:
                sdk.update_knowledge(oid, status="superseded")
            # ⛔ NOT the carrier guard's message: that rule is untouched here.
            assert "lifecycle CARRIER" not in str(exc.value), str(exc.value)
            assert "no successor" in str(exc.value), str(exc.value)
            assert _carrier_row(sdk, "Subject", "delta") == before, (
                "the refused write moved the row")
        finally:
            sdk.close()

    def test_the_record_fold_and_the_graph_agree_on_the_coherent_write(
            self, tmp_path):
        """The RECORD half: on the accepted shape the two stores both say
        `'superseded'`, and on the refused shape the record WOULD have said it
        against a `'live'` graph — the divergence this guard removes.

        No Postgres in this lane, so the record row is DERIVED the way the
        projection derives it: the `EntityMutated op=supersede` mutation's own
        columns into `cache_projection.node_from_row`, which is the same
        derivation path `DERIVED_FROM_RECORD` feeds (and the same predicate
        `public.object_current_status` spells in SQL).
        """
        from tortoise import cache_projection as cp
        from tortoise import record_projection as rp

        def record_row(state: dict, table: str = "object") -> dict:
            stmts = rp.mutations_for(
                {"type": "EntityMutated", "label": "Object", "op": "supersede",
                 "state": state, "id": "obj-1"},
                org_id="o", graph_id="g")
            row = {c: None for c in rp.table_columns(
                Path(__file__).resolve().parent.parent
                / "supabase" / "migrations")[table]}
            row[rp.snake(cp.KEY_PROPERTY["Object"])] = "obj-1"
            row.setdefault("props", {})
            joined = " ".join(s.sql for s in stmts)
            assert "superseded_by" in joined, joined
            assert "status" not in joined, (
                "the record stored a `status` column it does not have: "
                f"{joined}")
            for stmt in stmts:
                if "superseded_by" not in stmt.sql:
                    continue
                cols = [c.strip().split("=")[0].strip()
                        for c in stmt.sql.split("SET", 1)[1].split("WHERE")[0]
                        .split(",")]
                for col, val in zip(cols, stmt.params, strict=False):
                    if col in row:
                        row[col] = val
            return row

        # (a) the COHERENT write: record carriers + record-derived status.
        coherent = record_row({"status": "superseded", "supersededBy": "v2",
                               "supersededAt": "2026-01-01T00:00:00Z"})
        node = cp.node_from_row("Object", coherent)
        assert node.props.get("status") == "superseded", (
            "the record's own fold must derive 'superseded' for the coherent "
            f"write; got {node.props.get('status')!r}")

        # (b) the REFUSED write: had the door allowed it, this row is what the
        # record would hold — a carrier with no status, i.e. the record fold
        # saying 'superseded' for the node the live graph still calls 'live'.
        bare = record_row({"supersededBy": "v2"})
        bare_node = cp.node_from_row("Object", bare)
        assert bare_node.props.get("status") == "superseded", (
            "the record fold is supposed to derive a status from the carrier "
            "alone — that is exactly why the door must refuse a bare carrier")

        # (c) the SAME row under the SNAKE spelling (review P1). This is why a
        # camel-only guard closes nothing: `_split_properties` maps the key
        # through `snake`, so `superseded_by` lands in the identical column and
        # the identical fold reports the LIVE graph's node as terminal.
        snake_bare = record_row({"superseded_by": "v2"})
        assert snake_bare == bare, (
            "the snake spelling must produce the SAME record row as the camel "
            f"one — otherwise this lane is guarding the wrong surface: "
            f"{snake_bare} != {bare}")
        assert cp.node_from_row("Object", snake_bare).props.get(
            "status") == "superseded", (
            "the snake-spelled bare carrier must derive 'superseded' in the "
            "record too — that is the divergence the guard must refuse")

        # …and the door now does refuse it, on a node the graph holds LIVE.
        sdk = TortoiseSDK(str(tmp_path / "t10192d.db"))
        try:
            oid = _make_carrier_entity(sdk, "Object", "epsilon")
            with pytest.raises(ValueError, match="lifecycle CARRIER"):
                sdk.update_knowledge(oid, supersededBy="v2")
            assert _carrier_row(sdk, "Object", "epsilon")[1] is None, (
                "the graph's reading of the refused node must stay LIVE "
                "(#9974 R1: an absent status IS live, and a stored 'live' "
                "would mean something DID fabricate one)")
            with pytest.raises(ValueError, match="lifecycle CARRIER"):
                sdk.update_knowledge(oid, superseded_by="v2")
            assert _carrier_row(sdk, "Object", "epsilon")[1] is None, (
                "a snake-spelled carrier was ACCEPTED on a live node — the "
                "guard is inert against it (review P1)")
        finally:
            sdk.close()


# ── #12869: a NON-SCALAR `status` — the STORED side and the write DOORS ────
#
# The defect (measured on main at `14be041b8f`): a LIVE never-statused Object
# accepted `status=['live']` and stored the list, because the Object family is
# the ONE entity family with no `status` check at either of its write doors
# (`create_entity(type='object')` and `update_entity`). The list is unhashable,
# so the NEXT lifecycle write on that row raised
# `TypeError: unhashable type: 'list'` out of the guard that reads the dead set —
# a bare traceback from a door that promises an actionable refusal, and a row no
# status write could address.
#
# ⛔ WHAT THIS IS *NOT*: a vocabulary. §4.3 declares `status`'s TYPE as `string`
# and leaves the WORD SET open per kind ("pack-declared kinds validate against
# their own declared words"), which `#10371` records as a constraint rather than
# a to-do — `packs/venture` stores `active`/`invested`/`paused`. So the doors
# refuse a NON-STRING and NOTHING ELSE; `status='banana'` must keep storing.
# `:Subject` (`#1423`) and `:Point` (`POINT_STATUS_VALUES`) keep their own closed
# vocabularies and are pinned here as unchanged neighbours.
class TestNonScalarStatus12869:
    """#12869 — a non-string `status` is refused at the door, and a row that
    already HOLDS one is refused rather than raised at."""

    #: The PERSISTABLE non-string shapes — measured ACCEPTED-and-STORED before
    #: this change, so each poisoned the row. `bool` and `int` are here because
    #: they are HASHABLE: they never raised, so a test written only against the
    #: `TypeError` would have left them storing silently.
    _REFUSED = (
        pytest.param(["live"], id="list"),
        pytest.param(["retracted"], id="list-terminal-word"),
        pytest.param(123, id="int"),
        pytest.param(True, id="bool"),
    )

    #: The NON-persistable shape. `#7369` degrades it to null before dispatch, so
    #: the node never carries it and the row is never poisoned — which is the
    #: recorded `#7925` round-4 reason it must stay ACCEPTED rather than refused.
    _DROPPED = (
        pytest.param({"a": 1}, id="dict"),
        pytest.param(b"live", id="bytes"),
        pytest.param(bytearray(b"x"), id="bytearray"),
        pytest.param(frozenset({"a"}), id="frozenset"),
    )

    @pytest.mark.parametrize("bad", _REFUSED)
    def test_the_OBJECT_update_door_refuses_a_non_string_status(
            self, tmp_path, bad):
        """Refused with the guard's own `ValueError` — never a `TypeError`.

        FALSIFIABLE BY: deleting the Object arm of `update_entity`'s family loop
        reds all four arms — each as `DID NOT RAISE ValueError`, because this
        fixture mints a row with NO stored status, so nothing is hashed and no
        `TypeError` can occur (MEASURED). The `TypeError` half of the original
        defect is pinned instead by
        `test_a_row_that_ALREADY_holds_a_non_scalar_is_REFUSED_not_crashed`, which
        is the only place a stored non-scalar exists to hash.
        """
        sdk = TortoiseSDK(str(tmp_path / "t12869u.db"))
        try:
            oid = _make_carrier_entity(sdk, "Object", "upsilon")
            before = _carrier_row(sdk, "Object", "upsilon")
            with pytest.raises(ValueError, match=r"was refused|refused a non-string status"):
                sdk.update_knowledge(oid, status=bad)
            assert _carrier_row(sdk, "Object", "upsilon") == before, (
                "the refused write was not atomic — the row changed: "
                f"{_carrier_row(sdk, 'Object', 'upsilon')} != {before}")
            # …and the row is still ADDRESSABLE by the guard, which is the whole
            # point: a declared word can still be written afterwards.
            sdk.update_knowledge(oid, status="live")
            assert _carrier_row(sdk, "Object", "upsilon")[1] == "live"
        finally:
            sdk.close()

    @pytest.mark.parametrize("bad", _REFUSED)
    def test_the_OBJECT_CREATE_door_refuses_a_non_string_status(
            self, tmp_path, bad):
        """The minting door too, or the defect is one call away.

        FALSIFIABLE BY: reverting the `elif t == "object"` guard reds every arm
        — measured pre-fix, `create_object('probe', status=['live'])` returned a
        node whose stored status WAS the list.
        """
        sdk = TortoiseSDK(str(tmp_path / "t12869c.db"))
        try:
            with pytest.raises(ValueError, match=r"was refused|refused a non-string"):
                sdk.create_object("phi", "core:strategy", status=bad)
            assert _carrier_row(sdk, "Object", "phi") is None, (
                "a refused create left a node behind — a refusal must not cost "
                "a write (#9287/#10192)")
        finally:
            sdk.close()

    def test_a_non_string_target_is_refused_before_it_can_trip_the_dead_set(
            self, tmp_path):
        """The exact #12869 repro, end to end, from the issue body.

        `status=['live']` was ACCEPTED on a live Object (storing the list) and
        the follow-up `status='live'` raised `TypeError: unhashable type:
        'list'`. Both halves must now be refusals.

        FALSIFIABLE BY: reverting the UPDATE-door Object check reds this arm
        (MEASURED — it is the first assertion in `M2`'s red list). ⚠️ Reverting the
        CREATE-door guard does NOT red it, and that is measured too: this arm
        mints its row with NO status and then exercises `update_entity` alone, so
        the two doors are pinned independently by
        `test_the_OBJECT_CREATE_door_refuses_a_non_string_status`.
        """
        sdk = TortoiseSDK(str(tmp_path / "t12869r.db"))
        try:
            sdk.create_object("rho", "core:strategy")
            oid = _carrier_row(sdk, "Object", "rho")[0]
            with pytest.raises(ValueError, match=r"was refused|refused a non-string status"):
                sdk.update_knowledge(oid, status=["live"])
            assert _carrier_row(sdk, "Object", "rho")[1] is None, (
                "the list reached the row — the stored side is unprotected")
            # The follow-up write that used to raise is now an ordinary success.
            sdk.update_knowledge(oid, status="live")
            assert _carrier_row(sdk, "Object", "rho")[1] == "live"
        finally:
            sdk.close()

    #: `(target, the refusal that must win)`. The TYPE refusal is asked before the
    #: STORED one, deliberately: a caller that supplied `123` gets the caller's
    #: error, and the corrupt row is the SECOND complaint, not the first. `None`
    #: and `'live'` are strings-or-clear, so they reach the stored check.
    _STORED_TARGETS = (
        pytest.param("live", "STORED status is", id="string-target"),
        pytest.param(None, "STORED status is", id="null-clear-target"),
        pytest.param(123, "was refused|refused a non-string status", id="non-string-target"),
    )

    @pytest.mark.parametrize("bad", _DROPPED)
    def test_a_NON_PERSISTABLE_status_is_REFUSED_not_silently_dropped(
            self, tmp_path, bad):
        """`#7925` round 4 — the over-rejection this change must not ship.

        A dict cannot be stored by FalkorDB, so `#7369` degrades it to null
        before dispatch: the node never carries it and the lifecycle guard never
        chokes on it. Refusing it would reject a write both writers perform, and
        that exact over-rejection was already fixed once in `#7925` (pinned at
        the bundle level by
        `test_direct_route_object_and_event_status_values_not_over_rejected`).
        The refusal is therefore scoped by "would this LAND?", the same question
        `sdk._status_is_stored_unhashable` asks.

        FALSIFIABLE BY: replacing the `_value_ok(word)` scope with an
        unconditional refusal reds every arm here (mutation M9/M10).

        ⛔ The first cut of this docstring said "dropping the
        `_is_persistable_prop_value` conjunct reds this arm". MEASURED FALSE: the
        predicate was that conjunct OR the transport gate, and reducing it to the
        transport gate alone reds NOTHING (34 passed) — because the conjunct only
        ever ADDED refusals, for values the transport drops anyway (`['a\x00b']`:
        fold True, gate False, node stores nothing). So the REVIEW was right that
        the claim was false, and the conjunct was the over-block: the predicate is
        now the transport gate alone. `_DROPPED` also gained `bytes`,
        `bytearray`, `frozenset` — all four are measured ACCEPTED-and-absent on
        `main`, at the doors that do not journal.
        """
        sdk = TortoiseSDK(str(tmp_path / "t12869drop.db"))
        try:
            # ⛔ RE-DERIVED at #12910's merge of #11824 S0 — the DOCTRINE IS
            # INVERTED, deliberately and with its cause named. This arm asserted
            # `#7925` round 4's "drop, do not refuse": a `#7369`-degraded value
            # (`dict`, `bytes`, `bytearray`, `frozenset`) reached the node as a
            # property DELETE, so refusing it would have rejected a write both
            # writers perform. `#11824 S0` replaces that question with a plain
            # one — `status` is a STRING, and the four-word vocabulary is the
            # only thing it may hold — so these four are now refused at the door,
            # BEFORE any transport degradation, and nothing lands. The property
            # this arm keeps is the one that survives the doctrine change: the
            # refusal is a DEFINED `ValueError` on a shape no word covers, not a
            # silent store and not a `TypeError`.
            with pytest.raises(ValueError, match=r"was refused"):
                sdk.create_object("drop-create", "core:strategy", status=bad)
            assert _carrier_row(sdk, "Object", "drop-create") is None, (
                "a refused non-persistable status minted a row anyway")
            # …and at the UPDATE door, on a row that exists and is live.
            sdk.create_object("drop-upd", "core:strategy", status="live")
            oid = _carrier_row(sdk, "Object", "drop-upd")[0]
            with pytest.raises(ValueError, match=r"was refused"):
                sdk.update_knowledge(oid, status=bad)
            assert _carrier_row(sdk, "Object", "drop-upd")[1] == "live", (
                "a refused non-persistable status moved the row")
        finally:
            sdk.close()

    def test_the_CREATE_guard_is_a_LANDING_test_not_a_presence_test(
            self, tmp_path):
        """A create that lands on an EXISTING node drops the status — so it is
        NOT refused, and that is the recorded `#7925` round-4 decision.

        TWO shapes land on an existing node, and they are pinned separately
        because `resolve_or_mint`'s `created` flag is true for BOTH the mint and
        the ADOPT (an id-less legacy holder that gets a stamped id). Testing the
        flag instead of the node's existence would have over-blocked adopt AND
        refused AFTER `_adopt_idless_holder` had already written the stamp — a
        refusal that costs a write.

        FALSIFIABLE BY: making the `lands=` thunk unconditional reds BOTH arms
        (mutation M7, MEASURED: "1 failed, 33 passed" for the whole class before
        the review round widened it); the adopt arm also then leaves the id
        stamped, which is the atomicity half. (The first cut of this docstring
        named an `_obj_exists` gate that does not exist anywhere in the tree —
        the mechanism is the thunk passed to the helper. Corrected.)
        """
        sdk = TortoiseSDK(str(tmp_path / "t12869merge.db"))
        try:
            # (a) a live id-carrying holder: the ordinary MERGE
            sdk.create_object("held", "core:strategy")
            before = _carrier_row(sdk, "Object", "held")
            # ⛔ RE-DERIVED at #12910's merge of #11824 S0: the DROP this half
            # pinned is now a REFUSAL issued before the MERGE — the shared
            # lifecycle door answers a non-string as its FIRST arm, so the write
            # never reaches `_persist_extra_props`. The INVARIANT both
            # mechanisms serve is unchanged and still asserted: the merged row
            # does not move.
            with pytest.raises(ValueError, match=r"was refused"):
                sdk.create_object("held", "core:strategy", status=["retracted"])
            assert _carrier_row(sdk, "Object", "held") == before, (
                "the re-create changed the merged row — the status was supposed "
                f"to be refused/dropped: {_carrier_row(sdk, 'Object', 'held')} != {before}")

            # (b) an id-LESS live holder: the ADOPT path. `resolve_or_mint`
            # STAMPS an id here, so a refusal after that point would be a
            # refusal that already wrote.
            sdk._get_proj().g.query(
                "CREATE (n:Object {name:'noid', objectKind:'core:strategy'})")
            assert _carrier_row(sdk, "Object", "noid")[0] is None, (
                "the fixture is supposed to be an id-LESS holder")
            # ⛔ SAME RE-DERIVATION as (a): S0 refuses the array at the door, so
            # the ADOPT never runs — and THAT is the atomicity claim this arm
            # exists for: the refusal precedes the id stamp, so no write is
            # left behind by a refusal (#9287/#10192).
            with pytest.raises(ValueError, match=r"was refused"):
                sdk.create_object("noid", "core:strategy", status=["retracted"])
            adopted = _carrier_row(sdk, "Object", "noid")
            assert adopted[1] is None, (
                f"the adopted row took the array status: {adopted}")
            assert adopted[0] is None, (
                "the refusal ran AFTER `_adopt_idless_holder` stamped an id — a "
                f"refusal that already wrote: {adopted}")
        finally:
            sdk.close()

    @pytest.mark.parametrize("bad", _REFUSED)
    def test_a_REFUSED_create_writes_NOTHING(self, tmp_path, bad):
        """Atomicity: the refusal precedes every write, including the id stamp.

        FALSIFIABLE BY: moving the raise AFTER `_create_entity` reds this (the
        node count becomes 1) — and moving it before `resolve_or_mint`'s
        `_obj_created` gate while keying on `created` reds the adopt arm above.
        """
        sdk = TortoiseSDK(str(tmp_path / "t12869atomic.db"))
        try:
            def count():
                return sdk._get_proj().g.query(
                    "MATCH (n:Object) RETURN count(n)").result_set[0][0]
            before = count()
            with pytest.raises(ValueError, match=r"was refused|refused a non-string"):
                sdk.create_object("never-made", "core:strategy", status=bad)
            assert count() == before, (
                "a REFUSED create left a node behind — a refusal must not cost "
                "a write (#9287/#10192)")
            assert _carrier_row(sdk, "Object", "never-made") is None
        finally:
            sdk.close()

    def test_no_SDK_status_write_can_repair_a_non_scalar_row(self, tmp_path):
        """The refusal's prescription must be PERFORMABLE (AX rule 2 / #10183).

        The guard declines to guess whether an unreadable row is dead, so it
        refuses EVERY status write on it — `'live'`, the stored word, and `None`
        alike (`None` alone would let a dead row read current). That means no SDK
        door repairs the row, and the message says so and names the route that
        does. This pin exists because the first draft of that message said
        "repair the row by writing a declared word onto it" — a call this very
        guard refuses, i.e. an unperformable prescription.

        FALSIFIABLE BY: allowing a status write through on a foreign stored value
        (drop the `_np_foreign` raise, or exempt a `str` target from it) reds the
        `'live'` arm here, and would mean the message is wrong again.
        """
        sdk = TortoiseSDK(str(tmp_path / "t12869repair.db"))
        try:
            sdk.create_object("wrecked", "core:strategy")
            oid = _carrier_row(sdk, "Object", "wrecked")[0]
            sdk._get_proj().g.query(
                "MATCH (n:Object {id:$i}) SET n.status = $s",
                params={"i": oid, "s": ["archived"]})
            for target in ("live", "archived", None):
                with pytest.raises(ValueError, match="STORED status is"):
                    sdk.update_knowledge(oid, status=target)
            # NON-status edits still land (the refusal is the `status` field)…
            sdk.update_knowledge(oid, description="a note")
            assert _carrier_row(sdk, "Object", "wrecked")[1] == ["archived"], (
                "a non-status edit disturbed the stored value")
            # …and the documented repair route actually works: after a raw write
            # of a DECLARED word the row is ADDRESSABLE again, so the ordinary
            # guard answers it instead of refusing to read it. It answers with a
            # TRANSITION verdict (`archived` -> `live` stays refused, #12745),
            # which is the proof it read the word rather than choking on it.
            sdk._get_proj().g.query(
                "MATCH (n:Object {id:$i}) SET n.status = 'archived'",
                params={"i": oid})
            sdk.update_knowledge(oid, status="deprecated")
            assert _carrier_row(sdk, "Object", "wrecked")[1] == "deprecated", (
                "the row is still unaddressable after a raw repair")
            with pytest.raises(ValueError, match="is TERMINAL for a :Object"):
                sdk.update_knowledge(oid, status="live", supersededBy=None,
                                  supersededAt=None)
        finally:
            sdk.close()

    def test_a_hashable_non_string_that_STILL_LANDS_is_refused(self, tmp_path):
        """The fold's filter under-approximates what the engine stores.

        `np.int64` is NOT an `int` subclass, so `_is_persistable_prop_value`
        (the FOLD's filter) answers False for it — while the engine really does
        store it. Scoping on that predicate alone let
        `create_object(status=np.int64(5))` through and the node **stored 5**: the
        exact poisoning #12869 exists to stop, one library type over. The
        transport's predicate (`projection._value_ok`, the `#7369` gate) is the
        half that catches it, so the refusal asks THAT one — `_value_ok` ALONE.
        The OR of the two was measured wrong in BOTH directions and deleted.

        FALSIFIABLE BY: returning to `_is_persistable_prop_value` alone reds this
        arm (the value is accepted and 5 lands on the node).
        """
        np = pytest.importorskip("numpy")
        sdk = TortoiseSDK(str(tmp_path / "t12869np.db"))
        try:
            with pytest.raises(ValueError, match=r"was refused|refused a non-string"):
                sdk.create_object("np-obj", "core:strategy",
                                  status=np.int64(5))
            assert _carrier_row(sdk, "Object", "np-obj") is None
            sdk.create_object("np-obj2", "core:strategy")
            oid = _carrier_row(sdk, "Object", "np-obj2")[0]
            with pytest.raises(ValueError, match=r"was refused|refused a non-string"):
                sdk.update_knowledge(oid, status=np.int64(7))
            assert _carrier_row(sdk, "Object", "np-obj2")[1] is None
        finally:
            sdk.close()

    @pytest.mark.parametrize("bad", _REFUSED)
    def test_the_THIRD_object_door_add_object_refuses_too(self, tmp_path, bad):
        """`EventAPI.add_object` splats caller props onto `ObjectRegistered`.

        It is the THIRD Object write door, and `_upsert_object` writes `status`
        ON CREATE — so without the shared helper `add_object(..., status=[...])`
        mints exactly the unaddressable row this issue is about, after the other
        two doors started refusing it. A rule stated at two doors and silently
        absent at the third is the defect `#11835` round 4 and `#11948` each
        measured — the latter in this method's own docstring.

        FALSIFIABLE BY: removing the helper call from `add_object` reds every arm
        (the row stores the list/int/bool, and the next status write then refuses
        or raises `TypeError`).
        """
        from tortoise.api import EventAPI
        from tortoise.log import EventLog
        sdk = TortoiseSDK(str(tmp_path / "t12869ao.db"))
        try:
            api = EventAPI(EventLog(str(tmp_path / "ao.jsonl")),
                           initiated_by="user", projection=sdk._get_proj())
            with pytest.raises(ValueError, match="add_object refused"):
                api.add_object("ao-obj", "core:strategy", status=bad)
            assert _carrier_row(sdk, "Object", "ao-obj") is None, (
                "a refused add_object left a node behind")
            # ...and a STRING is still accepted (the vocabulary stays open).
            oid = api.add_object("ao-ok", "core:strategy", status="banana")
            assert _carrier_row(sdk, "Object", "ao-ok")[1] == "banana"
            assert isinstance(oid, str)
        finally:
            sdk.close()

    def test_an_ingest_bundle_is_refused_at_PHASE_1_not_mid_write(self, tmp_path):
        """The door's refusal must be mirrored by the bundle validator.

        `_validate_bundle` is a SEPARATE authority from `create_entity`, and a
        Phase-1 gap means step 3 raises with the earlier items already committed
        — the #2422/#7925 partial-mutation class that sweep exists to close.

        FALSIFIABLE BY: removing the Object arm from the Phase-1 sweep reds this
        (the bundle validates clean, then `ingest` raises after `good-first` is
        written).
        """
        sdk = TortoiseSDK(str(tmp_path / "t12869bundle.db"))
        try:
            bundle = {"entities": [
                {"ref": "g", "type": "object", "name": "good-first",
                 "status": "live"},
                {"ref": "p", "type": "object", "name": "poison-second",
                 "status": 123}]}
            violations = sdk._validate_bundle(bundle)
            assert violations, (
                "a non-string Object status passed Phase 1 — ingest would then "
                "abort mid-write with good-first already committed")
            assert violations[0]["section"] == "entities"
            assert violations[0]["index"] == 1
            # Nothing was written, because nothing was attempted.
            assert sdk._get_proj().g.query(
                "MATCH (n:Object {name:'good-first'}) RETURN count(n)"
            ).result_set[0][0] == 0
            # ⛔ RE-DERIVED at #12910's merge of #11824 S0: a dict status is no
            # longer "dropped by the writer", so it is no longer accepted by the
            # validator either — both answer the same question, which is the
            # property this arm exists for (the two authorities AGREE).
            _dict_v = sdk._validate_bundle({"entities": [
                {"ref": "o", "type": "object", "name": "dict-ok",
                 "status": {"a": 1}}]})
            assert _dict_v, "the validator accepted a non-string the door refuses"
            with pytest.raises(ValueError, match=r"was refused"):
                sdk.create_object("dict-ok", "core:strategy", status={"a": 1})
            # ⛔ AND THE VALIDATOR'S PREDICATE IS THE DOOR'S, NOT THE OR (#12869
            # review round 4, P3). This arm asked
            # `_is_persistable_prop_value(_mint) or _value_ok(_mint)`, so a value
            # the FOLD filter keeps and the TRANSPORT gate drops was refused here
            # while `create_object` ACCEPTED it — the validator refusing a bundle
            # on a value the door stores nothing of, and saying so in a message
            # that asserted a refusal which does not happen. `('a\x00b',)` is
            # exactly that shape (measured: fold True, gate False).
            # ⛔ ALSO RE-DERIVED: the `#7369` "kept by the fold filter, dropped by
            # the transport" carve-out is gone with S0 — a plain string is the
            # contract now, so the tuple is REFUSED by both authorities and the
            # agreement this arm exists for is asserted as agreement, not as a
            # stored-nothing outcome.
            _tuple_v = sdk._validate_bundle({"entities": [
                {"ref": "t", "type": "object", "name": "tuple-ok",
                 "status": ("a\x00b",)}]})
            assert _tuple_v, "the validator accepted a tuple the door refuses"
            with pytest.raises(ValueError, match=r"was refused"):
                sdk.create_object("tuple-ok", "core:strategy",
                                  status=("a\x00b",))
            assert _carrier_row(sdk, "Object", "tuple-ok") is None, (
                "the refused create wrote a row")
        finally:
            sdk.close()

    def test_TWO_keys_rendering_as_status_are_refused_at_EVERY_door(
            self, tmp_path):
        """A first-wins READ over a last-wins WRITE is a bypass, not an edge case.

        `lifecycle_props_by_rendering` is FIRST-wins over the renderer union
        (its own docstring) while the live map write applies `str(key)`. So a
        call carrying TWO keys that render as `status` is READ as the first and
        LANDED as the second. MEASURED through the create door before this
        check, with a `str` subclass whose `__hash__` sends it to a different
        bucket so it survives alongside the literal key:

            create_object(name, kind,
                **{"status": None, KHash("status"): ["live"]})
            -> ACCEPTED, node stored ['live']

        i.e. a `None` key HIDING a non-scalar produced the exact unaddressable
        row #12869 exists to remove — through the guard added to prevent it. The
        update door already refused this (`_status_write_is_ambiguous`, `#12786`
        rounds 2-3); the other doors did not. It now lives in the shared helper,
        so a fourth door cannot miss it.

        FALSIFIABLE BY — PER ARM, because "reds every arm" was FALSE (review
        round 3, P2; measured, one arm):
          (a) create  — reds on removing the helper's check (`DID NOT RAISE`,
              then `['live']` lands). This is the arm that pins the helper.
          (b) update  — caught by `update_entity`'s OWN `#12786` check, which
              runs before the helper is ever reached; it pins the DOOR, not the
              helper.
          (c) add_object — `_refuse_caller_named_funnel_keys` refuses a
              renderer-only KEY before the guard sees it, and two EXACT `str`
              keys cannot both render as `status`, so ambiguity is UNREACHABLE
              at that door: the helper's check there is defence in depth, not the
              mechanism.
        """
        class KHash(str):
            def __hash__(self):          # a different bucket, same rendering
                return 424242

        sdk = TortoiseSDK(str(tmp_path / "t12869amb.db"))
        try:
            # (a) create door
            with pytest.raises(ValueError, match="CONFLICTING status write"):
                sdk.create_object("pair-obj", "core:strategy",
                                  **{"status": None, KHash("status"): ["live"]})
            assert _carrier_row(sdk, "Object", "pair-obj") is None, (
                "a refused conflicting create left a node behind")
            # (b) update door — sends the pair at an EXISTING row
            sdk.create_object("pair-obj2", "core:strategy", status="live")
            oid = _carrier_row(sdk, "Object", "pair-obj2")[0]
            with pytest.raises(ValueError, match="CONFLICTING status write"):
                sdk.update_knowledge(oid, **{"status": "live",
                                          KHash("status"): ["live"]})
            assert _carrier_row(sdk, "Object", "pair-obj2")[1] == "live", (
                "a refused conflicting update changed the row")
            # (c) the third door
            from tortoise.api import EventAPI
            from tortoise.log import EventLog
            api = EventAPI(EventLog(str(tmp_path / "amb.jsonl")),
                           initiated_by="user", projection=sdk._get_proj())
            with pytest.raises(ValueError):
                api.add_object("pair-obj3", "core:strategy",
                               **{"status": None, KHash("status"): ["live"]})
            # (d) ONE key is still fine: the helper is not a blanket refusal.
            sdk.update_knowledge(oid, status="archived")
            assert _carrier_row(sdk, "Object", "pair-obj2")[1] == "deprecated"
        finally:
            sdk.close()

    def test_a_PROJECTION_LESS_lane_still_accepts(self, tmp_path):
        """`EventAPI` without a projection is a documented, supported lane.

        It journals only — `_registration_id` and `_emit` both branch on it
        (`getattr(self.projection, "g", None)`), and `api.py` documents the
        `__main__`/`m0.py` demo mode. NOTHING can land there, so the door's own
        "would this land?" question answers NO and the write must be accepted.

        FALSIFIABLE BY: dropping the `_lands is not None` guard from
        `add_object` reds this (MEASURED: `AttributeError: 'NoneType' object has
        no attribute 'g'`, where `origin/main` accepted and journaled — a
        regression the first cut of this guard shipped, caught by review).
        """
        from tortoise.api import EventAPI
        from tortoise.log import EventLog
        log_path = tmp_path / "journalonly.jsonl"
        api = EventAPI(EventLog(str(log_path)), initiated_by="user")
        assert getattr(api, "projection", None) is None, (
            "the fixture is supposed to be projection-less")
        oid = api.add_object("logonly", "core:strategy", status=["live"])
        assert isinstance(oid, str) and oid, "the journal-only lane refused"
        assert log_path.exists() and log_path.stat().st_size > 0, (
            "nothing was journaled — the write did not happen at all")

    def test_a_str_subclass_that_cannot_be_HASHED_is_refused_not_crashed(
            self, tmp_path):
        """The residual `TypeError` of the same shape, one input class over.

        The guard tests `_np_status in _np_terminal` — a `frozenset` membership,
        which HASHES. A `str` subclass with `__hash__ = None` passes the
        `isinstance(word, str)` check and would raise `TypeError: unhashable
        type` out of `_update_entity` (MEASURED on a dead row, and on
        `origin/main` too) — the identical failure the issue exists to replace
        with a refusal.

        ⛔ TWO DIFFERENT FIXES WERE ON THE TABLE, and this pin now records which
        one is in the tree (#12869). An earlier round refused such a word at the
        shared helper with a `hash(word)` probe, and the review was right that it
        OVER-BLOCKED: only this dead-row path hashes the caller's word, and a
        CREATE stores a `str` subclass by content which round-trips from
        FalkorDB as a plain `str`. What removes the crash is the NORMALISATION
        above (`str.__str__` on any `str` subclass, which yields exact `str`
        content), so the word reaches the membership test already hashable and is
        refused as the RESURRECTION it is — the guard's own `TERMINAL` message,
        not a type message.

        So the pin asserts the whole path: an unhashable `str` subclass is still
        a WORD by content, and it is REFUSED (a `ValueError`, never a
        `TypeError`).

        FALSIFIABLE BY — measured, see the PR body's mutation matrix:
          * deleting the terminal guard reds this as `DID NOT RAISE`;
          * dropping the normalisation AND the exact-`str` conjunct together
            reds it with `TypeError: unhashable type` (either alone is already
            covered by the other, which is why both are named).
        """
        class UnhashableStr(str):
            __hash__ = None

        sdk = TortoiseSDK(str(tmp_path / "t12869hash.db"))
        try:
            sdk.create_object("dead-h", "core:strategy", status="superseded")
            oid = _carrier_row(sdk, "Object", "dead-h")[0]
            # ⛔ RE-DERIVED at #12910's merge of #11824 S0: `UnhashableStr("live")`
            # is refused by the shared VOCABULARY door before the dead-set guard
            # builds anything, so the guarantee this pin exists for — a
            # `ValueError`, never a `TypeError: unhashable type` — is asserted on
            # the class, which is the contract both mechanisms keep.
            with pytest.raises(ValueError):
                sdk.update_knowledge(oid, status=UnhashableStr("live"))
            assert _carrier_row(sdk, "Object", "dead-h")[1] == "superseded", (
                "the refused unhashable word changed the dead row")
            # …and a HASHABLE subclass is still a word (no over-refusal).
            class Fine(str):
                pass
            sdk.create_object("live-h", "core:strategy")
            oid2 = _carrier_row(sdk, "Object", "live-h")[0]
            sdk.update_knowledge(oid2, status=Fine("archived"))
            assert _carrier_row(sdk, "Object", "live-h")[1] == "deprecated"
        finally:
            sdk.close()

    @pytest.mark.parametrize("bad", [b"live", bytearray(b"x"), frozenset({"a"})])
    def test_the_JOURNAL_door_refuses_what_the_ENCODER_cannot_carry(
            self, tmp_path, bad):
        """`dropped` is a claim about the NODE, and it is not true of the RECORD.

        At `EventAPI.add_object` every write is appended as JSON. A value the
        `#7369` gate drops from the node (`bytes`, `bytearray`, `frozenset`) has
        no JSON form, so `EventLog.append` raises
        `TypeError: Object of type bytes is not JSON serializable` — MEASURED,
        AFTER `_registration_id` had stamped an id onto an adopted holder, with
        zero journal lines: a write with no record behind it, the `#3590`
        live-without-durable class. The caller got the bare traceback #12869
        exists to replace. `dict` DOES have a JSON form and stays accepted.

        FALSIFIABLE BY: deleting the `_refuse_unencodable_object_status(…)` call
        at `tortoise/api.py` (the `from .sdk import` line and its call, which sit
        BEFORE `entity_create_lock`), or moving that call below
        `with entity_create_lock("Object", name):` — either one lets the raw
        `TypeError: Object of type bytes is not JSON serializable` escape from
        `EventLog.append` again (MEASURED; the ADOPT lane additionally leaves the
        row id-bearing with zero journal lines).
        """
        from tortoise.api import EventAPI
        from tortoise.log import EventLog
        sdk = TortoiseSDK(str(tmp_path / "t12869json.db"))
        try:
            log_path = tmp_path / "enc.jsonl"
            api = EventAPI(EventLog(str(log_path)), initiated_by="user",
                           projection=sdk._get_proj())
            with pytest.raises(ValueError, match=r"no JSON form|non-string"):
                api.add_object("enc-obj", "core:strategy", status=bad)
            assert _carrier_row(sdk, "Object", "enc-obj") is None
            # …a dict IS encodable, so it is still dropped-and-accepted.
            api.add_object("enc-dict", "core:strategy", status={"a": 1})
            assert _carrier_row(sdk, "Object", "enc-dict")[1] is None
            assert log_path.exists()
        finally:
            sdk.close()

    @pytest.mark.parametrize("lane", ["merge", "adopt", "no_projection"])
    def test_the_JOURNAL_door_refuses_on_EVERY_landing_lane(self, tmp_path, lane):
        """The encoder refusal is a RECORD question and must not be landing-gated.

        The pin above exercises ONLY the MINT path — a fresh name, where
        `lands()` answers True and the refusal fires. On every OTHER path the
        landing test answers False and the pre-fix helper returned before the
        encoder check, so the raw traceback escaped the very method #12869 is
        about:

          * MERGE — the name already carries an id;
          * ADOPT — a raw id-less holder that `resolve_or_mint` stamps an id
            onto, so `lands()` then answers False for a row it has just
            MUTATED (the refusal cost the write it was meant to prevent — the
            `#3590` live-without-durable class: id-bearing, zero journal lines);
          * NO PROJECTION — the documented journal-only `EventAPI` lane, where
            there is no graph to land on at all and `lands` is `lambda: False`.

        MEASURED at the pre-fix head, all three: `TypeError: Object of type
        bytes is not JSON serializable` out of `EventLog.append`. The fix asks
        the encoder BEFORE `_registration_id`, which is this method's own
        `#11948` rule, so ADOPT is no longer stamped either.

        FALSIFIABLE BY: moving the encoder check back below `lands` reds MERGE
        and ADOPT (raw `TypeError`, not `ValueError`); moving it back below
        `_registration_id` reds the ADOPT arm's id-absence assert.
        """
        from tortoise.api import EventAPI
        from tortoise.log import EventLog
        sdk = TortoiseSDK(str(tmp_path / f"t12869lane-{lane}.db"))
        try:
            target = {"merge": "enc-merge",
                      "adopt": "enc-adopt",
                      "no_projection": "enc-noproj"}[lane]
            log_path = tmp_path / f"enc-{lane}.jsonl"
            projection = None if lane == "no_projection" else sdk._get_proj()
            api = EventAPI(EventLog(str(log_path)), initiated_by="user",
                           projection=projection)
            if lane == "merge":
                api.add_object(target, "core:strategy")   # mints + journals
            elif lane == "adopt":
                sdk._get_proj().g.query(
                    "CREATE (n:Object {name:$n, objectKind:'core:strategy'})",
                    params={"n": target})
                assert _carrier_row(sdk, "Object", target)[0] is None, (
                    "the ADOPT fixture must start id-less")
            before = (log_path.read_text().splitlines()
                      if log_path.exists() else [])
            with pytest.raises(ValueError, match=r"no JSON form"):
                api.add_object(target, "core:strategy", status=b"live")
            after = (log_path.read_text().splitlines()
                     if log_path.exists() else [])
            assert after == before, (
                "a REFUSED write appended a journal record — the refusal must "
                "land before anything reaches the stream")
            if lane == "adopt":
                assert _carrier_row(sdk, "Object", target)[0] is None, (
                    "the refusal ran AFTER `_adopt_idless_holder` stamped an "
                    "id: a refusal that costs a write (#12869 review P1)")
            if lane != "no_projection":
                assert _carrier_row(sdk, "Object", target)[1] is None, (
                    "the unencodable status reached the node")
        finally:
            sdk.close()

    @pytest.mark.parametrize("payload_kind", ["subclass", "pair", "stronly"])
    def test_a_renderer_only_status_does_NOT_land_on_the_MERGE_path(
            self, tmp_path, payload_kind):
        """The MERGE/ADOPT branch drops a declared `status` — but only one KEY.

        The guard skips the merge path because `_persist_extra_props` drops a
        declared status there (`#7925` r4). That drop is a LITERAL-name
        `frozenset` membership, which HASHES the raw key, while the live write
        inlines `str(key)` — so a `str` SUBCLASS key that only RENDERS as
        `status` missed the skip set and was written by `SET n += $extra` on the
        ON MATCH lane, after the guard had declined to look at it. MEASURED
        before the fix, all three shapes:

            create_object("held", kind)                    # exists
            create_object("held", kind, **{KHash("status"): ['live']})
            -> stored ['live'], row then unaddressable by every SDK status write
            (same for `**{"status": None, KHash("status"): ['live']}` and for a
             str subclass whose `__str__` renders `status`)

        FALSIFIABLE BY: reverting `_persist_extra_props`' skip test to
        `k in skip` alone reds every arm here (the value lands again).
        """
        class KHash(str):
            def __hash__(self):
                return 424242

        class StrOnly(str):
            def __str__(self):
                return "status"

        key = {"subclass": KHash("status"),
               "pair": None,                       # built below
               "stronly": StrOnly("x")}[payload_kind]
        sdk = TortoiseSDK(str(tmp_path / f"t12869merge-{payload_kind}.db"))
        try:
            sdk.create_object("held", "core:strategy")
            before = _carrier_row(sdk, "Object", "held")
            extra = ({"status": None, KHash("status"): ["live"]}
                     if key is None else {key: ["live"]})
            # ⛔ RE-DERIVED at #12910's merge of #11824 S0: the create door now
            # REFUSES the bag (ambiguity / non-string) before it can reach
            # `_persist_extra_props`, so the drop this arm pinned is a refusal.
            # The invariant is unchanged and still asserted: nothing lands.
            with pytest.raises(ValueError):
                sdk.create_object("held", "core:strategy", **extra)
            assert _carrier_row(sdk, "Object", "held") == before, (
                "a renderer-only status LANDED on the merge path — the row is "
                f"now unaddressable: {_carrier_row(sdk, 'Object', 'held')}")
            # …and the row is still addressable: the whole point.
            sdk.update_knowledge(before[0], status="archived")
            assert _carrier_row(sdk, "Object", "held")[1] == "deprecated"
        finally:
            sdk.close()

    def test_a_renderer_only_status_does_NOT_land_on_the_ADOPT_path(
            self, tmp_path):
        """Same branch, the id-LESS holder shape (the ADOPT path).

        `resolve_or_mint` stamps an id here, so this is also the shape where a
        late failure would cost a write.

        FALSIFIABLE BY: as above — reverting the render-aware skip reds it.
        """
        class KHash(str):
            def __hash__(self):
                return 424242

        sdk = TortoiseSDK(str(tmp_path / "t12869adopt.db"))
        try:
            sdk._get_proj().g.query(
                "CREATE (n:Object {name:'adoptme', objectKind:'core:strategy'})")
            # ⛔ RE-DERIVED at #12910's merge of #11824 S0: refused at the door,
            # BEFORE `resolve_or_mint` stamps anything — so the atomicity half
            # (no write survives a refusal) is the assertion, and it is a
            # STRICTER claim than the drop this arm used to pin.
            with pytest.raises(ValueError):
                sdk.create_object("adoptme", "core:strategy",
                                  **{KHash("status"): ["live"]})
            adopted = _carrier_row(sdk, "Object", "adoptme")
            assert adopted[1] is None, (
                f"the adopted row took the renderer-only status: {adopted}")
            assert adopted[0] is None, (
                "the refusal ran AFTER the id stamp — a refusal that wrote: "
                f"{adopted}")
        finally:
            sdk.close()

    def test_an_OBJECT_declared_create_cannot_poison_a_SUBJECT_row(
            self, tmp_path):
        """The kind-redirect makes the Subject arm a door for `type='object'`.

        `_apply_kind_guard` routes `objectKind="core:organization"` onto the
        `:Subject` layer, whose own `#1423` presence test was
        `props.get("status")` — a LITERAL-name lookup. A renderer-only key
        passed validation unread and was then written under `status`, so an
        object-declared create stored `['live']` on a `:Subject` row and every
        Subject status write on it was refused afterwards. MEASURED. The
        identical payload at a genuinely-Object door was already refused, which
        is the two-spellings divergence `#1423` exists to prevent.

        FALSIFIABLE BY: reverting the arm to `props.get("status", "live")` reds
        this (the array lands on the Subject row).
        """
        class RStr(str):
            def __str__(self):
                return "status"

        sdk = TortoiseSDK(str(tmp_path / "t12869subj.db"))
        try:
            # #11824 S0 replaced `#1423`'s Subject vocabulary arm with the shared
            # four-word door, so the message is that door's ("was refused") — the
            # claim is the refusal plus the empty row, not the wording.
            with pytest.raises(ValueError, match=r"was refused|Invalid status"):
                sdk.create_object("acme2", "core:organization",
                                  **{RStr("x"): ["live"]})
            assert sdk._get_proj().g.query(
                "MATCH (n:Subject) RETURN count(n)").result_set[0][0] == 0, (
                "a refused create left a Subject row behind")
            # The plain-name shape is refused EARLIER, by the Object guard — it
            # reads the literal key, so it gets there first (measured). Either
            # message is a refusal that writes nothing; what must not happen is
            # an Object-or-Subject row holding the list.
            with pytest.raises(ValueError,
                               match=r"was refused|Invalid status"):
                sdk.create_object("acme3", "core:organization",
                                  status=["live"])
            # …and a legal word still goes to the Subject layer.
            sdk.create_object("acme4", "core:organization", status="live")
            assert sdk._get_proj().g.query(
                "MATCH (n:Subject) RETURN count(n)").result_set[0][0] == 1
        finally:
            sdk.close()

    def test_a_refusal_cannot_run_AFTER_the_ADOPT_stamp(self, tmp_path):
        """A refusal that lands after `resolve_or_mint` COSTS A WRITE (#12869 P2).

        `create_entity(type='object')` RESOLVES before it guards, and
        `_adopt_idless_holder` has already stamped an id onto an id-less live
        holder by the time the helper is reachable. So any refusal the helper
        raises ABOVE its `lands` test fires after that write — the atomicity
        rule `#9287`/`#10192` states, and the exact thing
        `test_a_REFUSED_create_writes_NOTHING` claims is impossible. That test
        cannot see it: every value in its `_REFUSED` set answers `lands()` False
        on adopt, so none of them ever reached these two checks.

        MEASURED before the fix, on this fixture: BOTH calls raised, the row was
        left id-bearing and the journal held 0 lines — a live-without-durable
        row (`#3590`) produced by a refusal that was supposed to write nothing.

        FALSIFIABLE BY: moving the `lands` thunk back BELOW the ambiguity check
        reds the second arm with the `ValueError` it must not raise (MEASURED);
        re-adding `journals=bool(self._event_log_path)` to the create door reds
        the third arm the same way.
        """
        class KHash(str):
            def __hash__(self):          # a different bucket, same rendering
                return 424242

        sdk = TortoiseSDK(str(tmp_path / "t12869adopt2.db"),
                          event_log_path=str(tmp_path / "t12869adopt2.jsonl"))
        try:
            for name in ("noid-a", "noid-b"):
                sdk._get_proj().g.query(
                    "CREATE (n:Object {name:$n, objectKind:'core:strategy'})",
                    params={"n": name})
            # (1) The ambiguity refusal must not fire after the ADOPT stamp. The
            #     node EXISTS, so this call's declared status is DROPPED by the
            #     merge lane — there is nothing here to refuse.
            # ⛔ RE-DERIVED at #12910's merge of #11824 S0, and the claim is now
            # STRONGER: the shared lifecycle door refuses this bag before
            # `resolve_or_mint` can stamp anything, so "the refusal does not cost
            # a write" is asserted as "no id was stamped", not as "the dropped
            # status left no trace".
            with pytest.raises(ValueError):
                sdk.create_object("noid-b", "core:strategy",
                                  **{"status": None, KHash("status"): ["live"]})
            adopted = _carrier_row(sdk, "Object", "noid-b")
            assert adopted is not None, "the fixture row vanished"
            assert adopted[0] is None, (
                "the refusal ran AFTER the adopt stamp — a refusal that wrote: "
                f"{adopted}")
            # (2) …and an unencodable status is NOT this door's refusal either:
            #     `_journal_entity_event` swallows the encoder failure and logs
            #     LIVE-ONLY (#4240), exactly as it does for `tags=frozenset(...)`,
            #     so narrowing `status` alone here narrowed on a mechanism that
            #     is not `status`-specific. The refusal lives at the ONE door
            #     that RAISES (`EventAPI.add_object`).
            with pytest.raises(ValueError, match=r"was refused"):
                sdk.create_object("noid-a", "core:strategy", status=b"live")
            enc = _carrier_row(sdk, "Object", "noid-a")
            assert enc is not None, "the fixture row vanished"
            assert enc[0] is None, (
                f"the refusal stamped an id — a refusal that wrote: {enc}")
            # (3) …and on a node this call MINTS, where the status really would
            #     be written, an unencodable value is still ACCEPTED — #4240's
            #     LIVE-ONLY lane, uniformly for every unencodable prop. This is
            #     the arm that pins the create door's decision NOT to pass
            #     `journals`: re-adding it reds here (MEASURED).
            with pytest.raises(ValueError, match=r"was refused"):
                sdk.create_object("enc-fresh", "core:strategy", status=b"live")
            fresh = _carrier_row(sdk, "Object", "enc-fresh")
            assert fresh is None, (
                f"an unencodable status minted a node anyway: {fresh}")
        finally:
            sdk.close()

    @pytest.mark.parametrize("target,expected", _STORED_TARGETS)
    def test_a_row_that_ALREADY_holds_a_non_scalar_is_REFUSED_not_crashed(
            self, tmp_path, target, expected):
        """DECIDED #2 — the read is hardened, and it fails CLOSED.

        Once the doors refuse, such a row can only be produced by a raw Cypher
        producer, a JSONL replay, or a pre-fix SDK — so the fixture writes it
        with raw Cypher, which the docstring records as the only remaining route.
        All three targets are pinned because `#12882` widened the affected input
        set: `None`/`123` short-circuited at the (then-present) target-side skip
        and returned, and `'live'` was the only case in the issue text.

        FALSIFIABLE BY: reverting the stored-side `_np_foreign` block reds the
        first two arms — `'live'` as `TypeError: unhashable type: 'list'`, and
        `None` as ACCEPTED (the write would land, which is the fail-open reading
        this test refuses). The third arm stays green on that revert, because the
        TYPE check catches it first — which is what proves the two refusals are
        independent and ordered.
        """
        sdk = TortoiseSDK(str(tmp_path / "t12869s.db"))
        try:
            sdk.create_object("sigma", "core:strategy")
            oid = _carrier_row(sdk, "Object", "sigma")[0]
            sdk._get_proj().g.query(
                "MATCH (n:Object {id:$i}) SET n.status = $s",
                params={"i": oid, "s": ["archived"]})
            assert _carrier_row(sdk, "Object", "sigma")[1] == ["archived"]
            with pytest.raises(ValueError, match=expected):
                sdk.update_knowledge(oid, status=target)
            assert _carrier_row(sdk, "Object", "sigma")[1] == ["archived"], (
                "the refusal was not atomic — the unreadable stored value was "
                "overwritten")
        finally:
            sdk.close()

    def test_a_non_scalar_stored_STATUS_is_refused_for_a_SUBJECT_too(
            self, tmp_path):
        """The guard is shared, so the Subject arm must not become the way round.

        FALSIFIABLE BY: restricting `_np_foreign` to `_np_label == "Object"`
        reds this arm with `TypeError: unhashable type: 'list'`.
        """
        sdk = TortoiseSDK(str(tmp_path / "t12869sub.db"))
        try:
            sid = _make_carrier_entity(sdk, "Subject", "tau")
            sdk._get_proj().g.query(
                "MATCH (n:Subject {id:$i}) SET n.status = $s",
                params={"i": sid, "s": ["archived"]})
            with pytest.raises(ValueError, match="STORED status is"):
                sdk.update_knowledge(sid, status="live")
        finally:
            sdk.close()

    # ── NO OVER-BLOCK: the half of #10371 this change must not touch ──────

    def test_an_arbitrary_STRING_status_is_REFUSED_by_the_one_lifecycle_door(
            self, tmp_path):
        """⛔ INVERTED BY `#11824 S0` — this pin used to keep the vocabulary OPEN.

        It asserted the opposite, and said why: *"§4.3's open vocabulary is NOT
        narrowed by the type guard … FALSIFIABLE BY: adding `or _obj_word not in
        <some closed set>` reds this — the `#10371` regression this test exists
        to catch."* `#11824 S0` (`2f1d5a5ca0`) IS that closed set, adopted
        deliberately: a pack's own words move to the **position** axis (its
        pipeline stages) and the **lifecycle** axis is four words for every
        knowledge kind (`tortoise/lifecycle_vocabulary.py`).

        ⛔ A recorded decision, not a merge accident: `#11824`'s design of record
        (`docs/plans/2026-11-15-state-lifecycle-and-pipelines.md` §3/§3.2) is the
        reason `#10371`'s per-kind vocabulary is no longer the model. This PR's
        own claim was about the TYPE axis (`§4.3 declares status a STRING`); the
        vocabulary axis above it was settled by that design, so the arm is
        restated rather than deleted — a deleted pin is how a later lane
        re-opens a decision it never sees.

        What this arm now guarantees, and why it still belongs to this PR: the
        refusal is a DEFINED `ValueError` naming the vocabulary, never a
        `TypeError` and never a silent store, and nothing lands.
        """
        sdk = TortoiseSDK(str(tmp_path / "t12869v.db"))
        try:
            with pytest.raises(ValueError, match=r"not a lifecycle word"):
                sdk.create_object("upsilon-word", "core:strategy",
                                  status="banana")
            assert sdk._get_proj().g.query(
                "MATCH (n:Object {name:'upsilon-word'}) RETURN count(n)"
            ).result_set[0][0] == 0, "a refused word minted a row"
            # …and the same on the UPDATE door, on a row that exists.
            sdk.create_object("upsilon-live", "core:strategy", status="live")
            oid = _carrier_row(sdk, "Object", "upsilon-live")[0]
            with pytest.raises(ValueError, match=r"not a lifecycle word"):
                sdk.update_knowledge(oid, status="plantain")
            assert _carrier_row(sdk, "Object", "upsilon-live")[1] == "live", (
                "a refused word moved the row")
        finally:
            sdk.close()

    def test_a_NULL_status_still_CLEARS_an_Object(self, tmp_path):
        """`None` is the documented clear, not a non-string to refuse.

        ⛔ This is the over-block a naive `not isinstance(x, str)` would ship:
        it would answer the bare-carrier guard's null arms
        (`_BARE_CARRIER_SHAPES`' `null-status-with-pointer`) with a TYPE message
        and turn two correct `"lifecycle CARRIER"` refusals into false positives.

        FALSIFIABLE BY: removing the `word is None` disjunct ALONE reds this arm
        and `[null-clear-target]` and the repair pin (MEASURED: "3 failed, 31
        passed"). The disjunct IS the mechanism, not a restatement of the rule:
        the scope is the transport gate, and `_value_ok(None)` is **True**
        (a live write of null clears the property), so without the disjunct
        `status=None` would be REFUSED.

        ⛔ The first cut of this docstring claimed the opposite — "removing
        `is not None` ALONE reds nothing, `None` is not persistable". That was
        true of the predicate it was written against (the fold filter answers
        False for `None`) and became FALSE when the scope was widened to the
        transport gate. The review caught it; the claim was measured and
        corrected, not reworded.
        """
        sdk = TortoiseSDK(str(tmp_path / "t12869n.db"))
        try:
            sdk.create_object("chi", "core:strategy")
            oid = _carrier_row(sdk, "Object", "chi")[0]
            sdk.update_knowledge(oid, status="live")
            assert _carrier_row(sdk, "Object", "chi")[1] == "live"
            sdk.update_knowledge(oid, status=None)
            assert _carrier_row(sdk, "Object", "chi")[1] is None, (
                "`status=None` must still CLEAR — an absent status reads live "
                "(#9974 R1)")
        finally:
            sdk.close()

    def test_a_str_SUBCLASS_is_a_str_word_and_still_stores(self, tmp_path):
        """A `str` subclass carries a word by CONTENT — no over-refusal.

        FALSIFIABLE BY: switching either guard to an exact-type test
        (`type(x) is str`) reds this arm.
        """

        class Word(str):
            pass

        sdk = TortoiseSDK(str(tmp_path / "t12869w.db"))
        try:
            sdk.create_object("psi", "core:strategy", status=Word("live"))
            assert _carrier_row(sdk, "Object", "psi")[1] == "live"
            oid = _carrier_row(sdk, "Object", "psi")[0]
            sdk.update_knowledge(oid, status=Word("archived"))
            assert _carrier_row(sdk, "Object", "psi")[1] == "deprecated"
        finally:
            sdk.close()

    # ── THE NEIGHBOURS are unchanged (the brief's "and Point, if not already") ──

    def test_the_POINT_and_SUBJECT_doors_already_refuse_and_still_do(
            self, tmp_path):
        """`#1423` and `POINT_STATUS_VALUES` were already closed; this pins it.

        Both families keep their OWN refusal text, which is why the new guard is
        placed on the Object arm alone rather than in front of the shared loop.

        FALSIFIABLE BY: adding the same refusal ABOVE the family loop reds the
        Subject arm here, because that arm's own `#1423` message is what the
        assertion matches (MEASURED).
        """
        sdk = TortoiseSDK(str(tmp_path / "t12869nb.db"))
        try:
            sid = _make_carrier_entity(sdk, "Subject", "omega")
            # ⛔ #11824 S0 ANSWERS FIRST on both families now: the shared
            # four-word door refuses a non-string with its own text before
            # `#1423`'s per-family arm is reached, so the contract this pin
            # keeps is the REFUSAL (a `ValueError`, nothing written), and the
            # per-family wording is asserted only where it still answers.
            with pytest.raises(ValueError, match=r"was refused|Invalid status"):
                sdk.update_knowledge(sid, status=["live"])
            pid = sdk.create_point(content="a probe point", kind="statement")
            pid = pid["id"] if isinstance(pid, dict) else pid
            with pytest.raises(ValueError, match=r"was refused|Invalid status"):
                sdk.update_knowledge(pid, status=["live"])
        finally:
            sdk.close()

    def test_the_coherent_un_supersede_is_still_admitted(self, tmp_path):
        """#12825's allowance must survive a guard placed in the same loop.

        FALSIFIABLE BY: removing the coherence `continue` (the
        `_np_dead == [_SUPERSEDED_STATUS]` / `_status_key_leaves_the_dead_state` /
        `_write_clears_every_lifecycle_carrier` block) reds this arm — MEASURED
        (`M11`).

        ⛔ THE CONTRADICTION THIS NOTE USED TO RECORD IS SETTLED — by the PR it
        named. It said this arm and
        `TestLifecycleCarrierCoherence::test_clearing_a_supersession_cannot_lift_a_dead_row`
        asserted opposite contracts for the same call, with the older pin red
        here AND on `origin/main` at `58f904ccf793`, owned by then-OPEN PR
        #12882. #12882 has since LANDED (`2dfbb5d455`) and restated that pin to
        `#12825`'s contract — the coherent un-supersede is ACCEPTED, a
        status-only `live` is REFUSED — so the two arms now agree and both are
        green. Nothing about this arm changed; only its description of a
        neighbour that no longer contradicts it. (The placeholder that stood in
        for the other pin's name here named nothing greppable, which is why the
        name is now spelled out.)
        """
        sdk = TortoiseSDK(str(tmp_path / "t12869u2.db"))
        try:
            sdk.create_object("alpha", "core:strategy")
            oid = _carrier_row(sdk, "Object", "alpha")[0]
            sdk.update_knowledge(oid, status="superseded", supersededBy="alpha2",
                              supersededAt="2026-01-01T00:00:00Z")
            assert _carrier_row(sdk, "Object", "alpha")[1] == "superseded"
            sdk.update_knowledge(oid, status="live", supersededBy=None,
                              supersededAt=None)
            assert _carrier_row(sdk, "Object", "alpha")[1] == "live", (
                "the coherent un-supersede (#12825) was refused — the new guard "
                "narrowed an existing allowance")
        finally:
            sdk.close()

    # ── THE CONTRACT OF THE REFUSAL ITSELF: the exception CLASS ──────────────

    def test_every_new_refusal_survives_a_hostile_repr(self, tmp_path):
        """P2-1 (#12910 review): every refusal this PR adds names its
        operands through `_refusal_repr` / `_np_safe_repr`, never the raw `!r`.

        The rule is already stated in this tree — `_refusal_repr`'s docstring:
        *"A refusal's contract is the exception CLASS — `ValueError` — so a
        `__repr__` that raises would replace it with its own class … ONE
        definition for EVERY refusal that names a caller operand"*. The first
        four sites were added with the raw `!r` anyway, and **nothing in the
        tree red on it** (`grep -r '_refusal_repr' tests/` was empty), so the
        contract was prose-only until this pin.

        ⛔ AND IT TOOK A FIFTH. The Subject door's ambiguity refusal
        (`sdk.py:52895`) shipped with the raw `{name!r}` after these four were
        pinned, because the pin's coverage stopped at the sites the first
        review round listed (`820c0ac48e`, P2). Arm (e) below is that site —
        and it must enter through `create_subject`; see its own note for why
        this arm's payload cannot reach the raise through an object-declared
        create.

        ⛔ AND A SIXTH AND SEVENTH. The bundle validator's two status refusals
        (`sdk.py:31271` non-string, `sdk.py:31283` array) were still
        interpolating the bundle's `status` value with the raw `!r`; their
        messages sit in a violations list that `ingest` raises on, so a hostile
        `__repr__` replaced that exception. Arms (f) and (i).

        ⛔ EACH OPERAND IS ITS OWN ARM, because they are evaluated in SOURCE
        ORDER inside one f-string — `{target!r}` before `{word!r}` — so an arm
        that makes only the VALUE hostile cannot see a broken TARGET, and an
        arm that makes only the TARGET hostile cannot see a broken VALUE.

        FALSIFIABLE BY: reverting any ONE site to the raw `!r` reds its own arm
        with the caller's exception (`RuntimeError: repr-boom` /
        `RuntimeError: id-repr-boom`) instead of `ValueError` — MEASURED at the
        pre-fix heads, per arm. One arm per site, so no single revert can mask
        another.
        """

        class RaisingRepr:
            """A caller-controlled operand whose `__repr__` raises.

            `__str__` is a literal `str` so ONLY `repr` raises: the defect this
            pins is the `!r` interpolation, not a broken `str()`.
            """

            def __str__(self):
                return "raising-repr"

            def __repr__(self):
                raise RuntimeError("repr-boom")

        class RaisingReprStr(str):
            """Same, on the `id` operand — which must be a `str` to reach a door."""

            def __repr__(self):
                raise RuntimeError("id-repr-boom")

        class RaisingReprInt(int):
            """Same, on a value the #7369 gate KEEPS, so the TYPE arm sees it.

            `_PERSISTABLE_SCALAR_TYPES` is `(str, bool, int, float)` tested with
            `isinstance`, so an `int` subclass is a persistable non-`str` — the
            only shape that reaches the type refusal's `{word!r}` at all.
            """

            def __repr__(self):
                raise RuntimeError("repr-boom")

        class KHash(str):
            def __hash__(self):          # a different bucket, same rendering
                return 424242

        from tortoise.api import EventAPI
        from tortoise.log import EventLog

        sdk = TortoiseSDK(str(tmp_path / "t12869rr.db"))
        try:
            api = EventAPI(EventLog(str(tmp_path / "rr.jsonl")),
                           initiated_by="user", projection=sdk._get_proj())

            # (a) `_refuse_unencodable_object_status` — the VALUE operand
            #     (a `dict`/`bytes` arm cannot see this: their `repr` is safe).
            with pytest.raises(ValueError) as ei:
                api.add_object("rr-enc", "core:strategy", status=RaisingRepr())
            assert "RaisingRepr" in str(ei.value), (
                "the refusal lost the operand instead of escaping it: "
                + str(ei.value))
            assert "repr-boom" not in str(ei.value)
            assert _carrier_row(sdk, "Object", "rr-enc") is None

            # (b) `_refuse_non_string_object_status`, the AMBIGUITY arm — the
            #     TARGET operand (its `{target!r}` is the whole message).
            #     ⛔ THIS ARM MUST ENTER THROUGH THE **CREATE** DOOR. The update
            #     door never reaches this raise: `update_entity` asks the same
            #     ambiguity question EARLIER and raises with an
            #     already-safe `_np_safe_repr(id_val)`, so an update-door arm
            #     here is VACUOUS — it passes with this site reverted to the raw
            #     `!r` (MEASURED, and that is how a first cut of this test was
            #     caught passing for the wrong reason).
            with pytest.raises(ValueError) as ei:
                sdk.create_object(RaisingReprStr("rr-amb"), "core:strategy",
                                  **{"status": None,
                                     KHash("status"): ["live"]})
            assert "RaisingReprStr" in str(ei.value), (
                "the ambiguity refusal did not use `_refusal_repr` on its "
                "target: " + str(ei.value))
            assert "id-repr-boom" not in str(ei.value)
            assert _carrier_row(sdk, "Object", "rr-amb") is None, (
                "a refused conflicting create left a node behind")

            # …and the same raise's message is unchanged for an ordinary `str`
            # target (no over-correction: the helper still renders real ids).
            sdk.create_object("rr-amb2", "core:strategy", status="live")
            amb_id = _carrier_row(sdk, "Object", "rr-amb2")[0]

            # (c) `_refuse_non_string_object_status`, the TYPE arm — the SAME
            #     target operand, reached with a value that is a plain `list`
            #     (so the target is the only hostile one) …
            with pytest.raises(ValueError) as ei:
                sdk.update_knowledge(RaisingReprStr(amb_id), status=["live"])
            # ⛔ RE-DERIVED at #12910's merge of #11824 S0: the shared VOCABULARY
            # door answers this call and interpolates the VALUE (`['live']`), not
            # the target — so the target's `__repr__` is never called at all.
            # The security property this arm exists for is unchanged and still
            # asserted: the promised `ValueError` survives, and no caller
            # exception (`id-repr-boom`) escapes in its place.
            assert "id-repr-boom" not in str(ei.value), (
                "a hostile `__repr__` on the target replaced the refusal: "
                + str(ei.value))
            assert _carrier_row(sdk, "Object", "rr-amb2")[1] == "live", (
                "a refused status write changed the row")

            # …and the VALUE operand of that same raise, which needs a target
            # that renders safely and a value the #7369 gate KEEPS (a
            # non-persistable one is dropped, not refused, so it never reaches
            # this raise).
            with pytest.raises(ValueError) as ei:
                sdk.update_knowledge(amb_id, status=RaisingReprInt(123))
            assert "RaisingReprInt" in str(ei.value), (
                "the type refusal did not use `_refusal_repr` on its value: "
                + str(ei.value))
            assert "repr-boom" not in str(ei.value)

            # (d) `_np_foreign` — the STORED-side refusal, which sits ~170 lines
            #     ABOVE the two in the same block that already use
            #     `_np_safe_repr`. Reachable only from the row shape it names:
            #     a non-scalar already on the row, which only a raw Cypher
            #     producer / a replay / a pre-fix SDK can create.
            sdk.create_object("rr-stored", "core:strategy", status="live")
            stored_id = _carrier_row(sdk, "Object", "rr-stored")[0]
            sdk._get_proj().g.query(
                "MATCH (n:Object {id:$i}) SET n.status = $s",
                params={"i": stored_id, "s": ["archived"]})
            with pytest.raises(ValueError) as ei:
                sdk.update_knowledge(stored_id, status=RaisingRepr())
            assert "RaisingRepr" in str(ei.value), (
                "`_np_foreign` did not use `_np_safe_repr` on its value: "
                + str(ei.value))
            assert "repr-boom" not in str(ei.value)
            # …and its TARGET operand.
            with pytest.raises(ValueError) as ei:
                sdk.update_knowledge(RaisingReprStr(stored_id),
                                     status=RaisingRepr())
            # ⛔ RE-DERIVED (same reason as arm (c)): the vocabulary door answers
            # on the VALUE and interpolates it through its own contained
            # `_render`, so the TARGET is never rendered at all. Asserted as the
            # security property — no caller exception replaces the `ValueError`.
            assert "id-repr-boom" not in str(ei.value), (
                "a hostile `__repr__` on the id replaced the refusal: "
                + str(ei.value))
            assert _carrier_row(sdk, "Object", "rr-stored")[1] == ["archived"], (
                "a refused stored-side write changed the row")

            # (e) the SUBJECT door's ambiguity refusal (`sdk.py:52895`) — the
            #     FIFTH site, found by the `820c0ac48e` review AFTER the other
            #     four were pinned. Its operand is the caller's `name`.
            #     ⛔ THIS ARM'S PAYLOAD, NOT ITS DOOR, is why it enters through
            #     `create_subject`. The Subject arm IS a door for a
            #     `type='object'` create (`test_an_OBJECT_declared_create_
            #     cannot_poison_a_SUBJECT_row` depends on exactly that), but
            #     `**{"status": None, <KHash>"status": …}` makes the KIND GUARD
            #     abstain (`subject_only_prop_abstention`), so `t` stays
            #     `"object"` and the Object create door's own guard
            #     (`_refuse_non_string_object_status`, which renders its target
            #     safely) raises FIRST — an object-declared arm with THIS
            #     payload would pin that site and pass with this one reverted
            #     (MEASURED: its message is the object door's "which value
            #     lands"). A subject-legal payload (`status='live'` + a second
            #     rendering key) DOES route and reach this raise — that claim is
            #     the SECOND half of this arm, executed rather than asserted.
            with pytest.raises(ValueError) as ei:
                sdk.create_subject(RaisingReprStr("rr-subj-amb"),
                                   "core:organization",
                                   **{"status": None,
                                      KHash("status"): "live"})
            assert "RaisingReprStr" in str(ei.value), (
                "the Subject door's ambiguity refusal did not use "
                "`_refusal_repr` on its name: " + str(ei.value))
            # ⛔ THE MECHANISM, NOT ONLY THE OPERAND (#12910 fix lane). Deleting
            # the ambiguity guard leaves the NEXT check raising `ValueError`
            # (`Invalid status None for a :Subject`) — MEASURED — so the name
            # assertion above covers that mutation only by accident: it reds
            # because that message happens not to name the operand. Naming the
            # raise makes the discriminator explicit and independent of the
            # fallback message's wording.
            assert "CONFLICTING status write" in str(ei.value), (
                "the Subject door's ambiguity GUARD did not fire: "
                + str(ei.value))
            assert "repr-boom" not in str(ei.value)
            assert _carrier_row(sdk, "Subject", "rr-subj-amb") is None, (
                "a refused Subject create left a node behind")

            #     …and the ROUTED entry reaches the SAME raise, which is the
            #     sentence above as an executable pin. `_kind_layer_note` sends
            #     an object-declared create to the Subject layer when the
            #     first-wins status is subject-legal, so this arm proves the
            #     entry a `create_object(type='object')` caller actually takes —
            #     not one the `create_subject` arm could stand in for. Reverting
            #     the site to `!r` reds it (`RuntimeError: id-repr-boom`,
            #     MEASURED) and DELETING the `raise` reds it `DID NOT RAISE`
            #     (MEASURED), so the routed entry cannot silently drift back to
            #     the Object door's guard — which is the vacuous shape this note
            #     warns about, arriving from the other side.
            with pytest.raises(ValueError) as ei:
                sdk.create_object(RaisingReprStr("rr-subj-route"),
                                  "core:organization",
                                  **{"status": "live",
                                     KHash("status"): "archived"})
            assert "RaisingReprStr" in str(ei.value), (
                "the ROUTED Subject-door refusal did not use `_refusal_repr` "
                "on its name: " + str(ei.value))
            assert "id-repr-boom" not in str(ei.value)
            # ⛔ WHICH raise, not just SOME raise (#12910 review, P3). Every
            # assertion above also holds on the OBJECT door's refusal — it
            # renders the same operand through `_refusal_repr` and writes
            # nothing — so on its own this arm cannot tell a routing
            # regression from the routed entry. The two messages differ by one
            # word and one door label: the Subject arm says "which WORD lands",
            # the Object door says "which VALUE lands" behind
            # `create_entity(type='object') ...`. MEASURED on a non-routed
            # payload (`core:strategy`): the object door's message satisfies
            # every assertion above, which is how the vacuous version was
            # caught.
            assert "which word lands" in str(ei.value), (
                "the routed create did not reach the SUBJECT arm's raise "
                "(the object door says 'which value lands'): " + str(ei.value))
            assert "create_entity(type" not in str(ei.value), (
                "the routed create raised the OBJECT door's refusal: "
                + str(ei.value))
            for _lbl in ("Subject", "Object"):
                assert _carrier_row(sdk, _lbl, "rr-subj-route") is None, (
                    "a refused routed create left a " + _lbl + " row")

            # (f) the BUNDLE validator's non-string refusal (`sdk.py:31271`) —
            #     the SIXTH site, found by the review of THIS delta. The message
            #     is built into a violations list that `ingest` raises on, so a
            #     hostile `__repr__` replaced THAT exception with its own
            #     (MEASURED pre-fix: `RuntimeError: repr-boom` out of
            #     `_validate_bundle`). The validator RETURNS violations, so the
            #     arm asserts the call survives AND names the operand's type.
            violations = sdk._validate_bundle({"entities": [
                {"ref": "b", "type": "object", "name": "boom-ent",
                 "status": RaisingReprInt(123)}]})
            assert violations, (
                "the validator did not refuse a non-string bundle status")
            _msg = violations[0]["message"]
            assert "RaisingReprInt" in _msg, (
                "the validator's refusal did not use `_refusal_repr` on the "
                "bundle value: " + _msg)
            assert "repr-boom" not in _msg
            assert sdk._get_proj().g.query(
                "MATCH (n:Object {name:'boom-ent'}) RETURN count(n)"
            ).result_set[0][0] == 0

            # (g) the CLASS NAME, not just the value (#12910 review round 8, P2).
            #     Both refusals above name the operand's type as well as its
            #     repr, and `type(word).__name__` is caller-controlled too: a
            #     METACLASS may define `__name__` as a raising property, so the
            #     f-string escaped as that exception before `_refusal_type_name`
            #     was used. ⛔ AN `int` SUBCLASS is the shape that reaches it —
            #     `_json.dumps` can serialise it (so the encoder probe passes)
            #     and `_value_ok` is False (so the TYPE arm is entered), while a
            #     plain object is DEGRADED to null by the #7369 gate instead and
            #     never reaches the message at all (MEASURED). A raising
            #     `__class__` property is NOT this axis: `isinstance` consults it
            #     and raises inside the guard's first line, before any message is
            #     built (MEASURED) — so this arm pins the `__name__` axis, which
            #     is the one a refusal can fix.
            class _RaisingNameMeta(type):
                @property
                def __name__(cls):
                    raise RuntimeError("name-boom")

            class _BoomInt(int, metaclass=_RaisingNameMeta):
                pass

            with pytest.raises(ValueError) as ei:
                sdk.create_object("rr-name", "core:strategy",
                                  status=_BoomInt(123))
            assert "unreadable (its metaclass raises)" in str(ei.value), (
                "the refusal did not use `_refusal_type_name` on its class "
                "name: " + str(ei.value))
            assert "name-boom" not in str(ei.value)
            assert _carrier_row(sdk, "Object", "rr-name") is None, (
                "a refused non-string create left a node behind")

            # (h) the RECORD-side probe, not just the message (#12910 review
            #     round 9, P3). `_refuse_unencodable_object_status` asks
            #     "does this value have a JSON form?" by CALLING `json.dumps`,
            #     and `dumps` runs the operand's own `__iter__`/`items()` — so a
            #     hostile one raised THROUGH the `except (TypeError, ValueError)`
            #     and replaced the promised `ValueError` (MEASURED:
            #     `RuntimeError: iter-boom`). Any failure to serialise is "no
            #     JSON form", whatever its class, which is why the probe now
            #     catches every exception and refuses. ⛔ ENTERED THROUGH
            #     `add_object`: the encoder is the journal door's, not the
            #     create door's, so a `create_object` arm would never reach it.
            class _BoomIter(list):
                def __iter__(self):
                    raise RuntimeError("iter-boom")

            with pytest.raises(ValueError) as ei:
                api.add_object("rr-iter", "core:strategy",
                               status=_BoomIter([1]))
            assert "iter-boom" not in str(ei.value), (
                "the encoder refusal let the operand's own exception out: "
                + str(ei.value))
            assert "which has no" in str(ei.value), (
                "the encoder refusal did not answer as a refusal: "
                + str(ei.value))
            assert _carrier_row(sdk, "Object", "rr-iter") is None, (
                "a refused encoder write left a node behind")

            # (i) the SIBLING bundle refusal — the ARRAY arm (`sdk.py:31283`).
            #     Arm (f) pins the non-string arm; that one and this one are two
            #     separate messages, so a revert of either must red something.
            #     ⛔ A hostile `__repr__` here replaced the violations list with
            #     the caller's exception (MEASURED: `RuntimeError: repr-boom` out
            #     of `_validate_bundle`), which is the same class as (f).
            class _HostileList(list):
                def __repr__(self):
                    raise RuntimeError("repr-boom")

            _arr = sdk._validate_bundle({"entities": [
                {"ref": "a", "type": "object", "name": "arr-ent",
                 "status": _HostileList(["x"])}]})
            assert _arr, "the validator did not refuse an array bundle status"
            # ⛔ RE-DERIVED at #12910's merge of #11824 S0: the array arm is no
            # longer the one that answers — `#11824 S0` made the Object writer
            # (and therefore Phase 1) validate the same four-word door, so the
            # NON-STRING arm answers first and the array arm is unreachable for
            # this payload. The security property this pair exists for is what is
            # asserted, and it is a real one here: BOTH hostile operands are
            # rendered through the contained helper (`<_HostileList>` /
            # `<… whose repr() raised>`), and no caller exception escapes.
            assert "repr-boom" not in _arr[0]["message"], (
                "the bundle refusal let a hostile `__repr__` out: "
                + _arr[0]["message"])
            assert "<_HostileList" in _arr[0]["message"], (
                "neither bundle arm rendered the refusing value safely: "
                + _arr[0]["message"])
        finally:
            sdk.close()


# ── #9974 R1: the BIRTH status is DERIVED-OR-ABSENT, never fabricated ──────

class TestBirthStatusIsDerivedNotFabricated:
    """The birth seed wrote a status nothing derived (#9974 R1).

    Two writers fabricated it, and both are pinned here because either one alone
    reproduces the defect:

    * the DOOR — ``sdk._entity_door_props`` put ``out["status"] = "live"`` into
      every creation record, so the journal itself CLAIMED a birth status;
    * the FOLD — ``_upsert_object``/``_upsert_subject`` seeded
      ``status=coalesce($st, 'live')``, so a raw/connector producer that omitted
      the key was answered with a fabricated ``'live'`` on replay.

    Why it is a defect and not a default: ``status`` is a DERIVED cache (§2.6,
    ``02-identity-and-placement.md``), and the record-sourced route
    (``cache_projection``) derives it from the CARRIERS and leaves it ABSENT
    where they are silent — by deliberate, mutation-verified rule (#10189's
    no-fabrication leg). Two rebuild routes therefore materialised two different
    graphs for the same record, which §2.0 forbids. The reader-visible meaning is
    unchanged: ``live._terminal_excluded`` treats an ABSENT status as LIVE.
    """

    def test_a_status_less_birth_stores_no_status_on_either_route(self,
                                                                 tmp_path):
        """Live write AND a REPLAY of a hand-emitted status-less record.

        The two facts are pinned at their own writers, so neither can mask the
        other:

        * the LIVE write is the door + fold together (``create_entity``);
        * the REPLAY half drives a record that never passed through the door at
          all (``_emit_event("ObjectRegistered", …)`` — the raw/connector
          producer shape), so the FOLD's own clause is isolated: with the door
          silenced, a re-instated ``coalesce($st, 'live')`` still reds here.
        """
        events = tmp_path / "events"
        events.mkdir()
        sdk = TortoiseSDK(str(tmp_path / "t9974r1.db"),
                          event_log_path=str(events / "events.jsonl"))
        try:
            proj = sdk._get_proj()
            sdk.create_entity("object", "eta", objectKind="core:strategy")
            sdk.create_entity("subject", "team-eta", subjectKind="core:team")
            # a producer that never goes through `create_entity`: the record
            # carries an id and a name, and NO status.
            sdk._emit_event("ObjectRegistered", id="obj-9974-raw",
                            name="eta-raw")
            sdk._emit_event("SubjectAdded", id="subj-9974-raw",
                            name="team-eta-raw")

            def status_of(label, name):
                rows = proj.g.query(
                    f"MATCH (n:{label} {{name:$n}}) RETURN n.status",
                    params={"n": name}).result_set
                assert rows, f"the fixture did not create {label} {name!r}"
                return rows[0][0]

            # 1. the LIVE write (door + fold)
            assert status_of("Object", "eta") is None, (
                "a status-less Object was born with a status the record never "
                "derived — #9974 R1")
            assert status_of("Subject", "team-eta") is None, (
                "a status-less Subject was born with a fabricated status")

            # 2. the JSONL REPLAY, including the door-less records — the FOLD's
            #    own clause.
            proj.rebuild_all(str(events), confirm_destructive=True)
            assert status_of("Object", "eta") is None, (
                "rebuild_all materialised a birth status from a record that "
                "never carried one")
            assert status_of("Subject", "team-eta") is None
            assert status_of("Object", "eta-raw") is None, (
                "the fold fabricated a birth status for a record produced "
                "outside the create door")
            assert status_of("Subject", "team-eta-raw") is None

            # 3. …and the record-sourced route derives nothing from a row that
            #    carries no lifecycle CARRIER, which is the agreement R1 asks
            #    for (the §2.0 divergence). It uses the cache's OWN instrument —
            #    `DERIVED_FROM_RECORD` — rather than a second predicate; the row
            #    is carrier-free by construction, which is exactly the shape the
            #    record emits for a status-less birth.
            from tortoise.cache_projection import node_from_row
            oid = proj.g.query("MATCH (o:Object {name:'eta'}) RETURN o.id"
                               ).result_set[0][0]
            cache = node_from_row("Object", {"id": oid, "name": "eta"})
            assert cache.props.get("status") is None, cache.props
            assert cache.props.get("status") == status_of("Object", "eta")
        finally:
            sdk.close()

    def test_a_caller_supplied_birth_status_survives_the_live_write_and_replay(
            self, tmp_path):
        """Anti-vacuity: DERIVED means the record's value is carried, not lost.

        Without this leg the test above would pass if the birth status were
        simply dropped — the same fabrication failure in the other direction.

        ⛔ SCOPE, stated (#9974 review P2-4): this covers the LIVE WRITE and the
        JSONL replay only. It is NOT "both routes" in the sense of live vs
        RECORD-SOURCED: on the record-sourced route (`cache_projection`,
        `node_from_row`) a caller-supplied birth status is still DROPPED, because
        `ObjectRegistered`/`SubjectAdded` are not `EventRecorded` and the layer
        tables never store `status`. That is the PR's named residual — see
        `cache_projection`'s STATED BOUND — and this leg must not be read as
        covering it.
        """
        events = tmp_path / "events"
        events.mkdir()
        sdk = TortoiseSDK(str(tmp_path / "t9974r1b.db"),
                          event_log_path=str(events / "events.jsonl"))
        try:
            proj = sdk._get_proj()
            sdk.create_entity("object", "theta", objectKind="core:strategy",
                              status="archived")

            def status_of(name):
                rows = proj.g.query(
                    "MATCH (n:Object {name:$n}) RETURN n.status",
                    params={"n": name}).result_set
                return rows[0][0] if rows else "<absent>"

            # ⚠️ #11824 S0: `archived` is a declared SYNONYM of `deprecated`, so
            # the live write stores the CANONICAL word (silently, AX §1) — and the
            # affinity this test exists to prove, "the record's value is CARRIED,
            # not lost", holds BETWEEN THE TWO ROUTES because the DOOR puts the
            # canonical word in the RECORD too (measured: `create_object(...,
            # status='archived')` journals `status: "deprecated"`). So the replay
            # stores exactly what the graph holds, with no second spelling rule
            # anywhere on the read side.
            assert status_of("theta") == "deprecated", status_of("theta")
            proj.rebuild_all(str(events), confirm_destructive=True)
            assert status_of("theta") == "deprecated", (
                "a caller-supplied birth status was dropped (or re-spelled) by "
                f"replay: {status_of('theta')!r}")
        finally:
            sdk.close()

    def test_the_door_never_fabricates_a_status(self):
        """The DOOR half, isolated: no status in, no status key out.

        Pinned on the function because it is the only writer of the creation
        record's payload — a re-instated ``out["status"] = "live"`` reds this
        without needing a graph, and a caller-supplied value must survive
        untouched (``None`` included: a caller's explicit clear is not this
        function's to rewrite).
        """
        from tortoise.sdk import _entity_door_props

        assert "status" not in _entity_door_props("objectKind", "x", {}), (
            "the door fabricated a birth status (#9974 R1)")
        assert "status" not in _entity_door_props("subjectKind", "x", {}), (
            "the door fabricated a birth status for a Subject")
        assert _entity_door_props(
            "objectKind", "x", {"status": "archived"})["status"] == "archived"
        assert _entity_door_props(
            "objectKind", "x", {"status": None})["status"] is None
        # the kind rule #9572 landed must be unaffected by this change
        assert "objectKind" not in _entity_door_props("objectKind", "x", {})
        assert _entity_door_props(
            "objectKind", "x", {"objectKind": "core:strategy"}
        )["objectKind"] == "core:strategy"


# ── #9974 R1: a read surface RENDERS an absent status as live ───────────────

class TestAbsentStatusRendering:
    """``live.status_as_reported`` — the one spelling of "what a read REPORTS".

    R1 removed the fabricated birth status, so a status-less Object/Subject now
    stores NOTHING and an absent value has to be rendered. Two public read
    surfaces render the value as a STRING — ``expand_relationships``'
    ``related_status`` and ``provenance``'s subject ``status`` — and both
    reported ``''`` for it, which silently changed what a LIVE entity read as
    (#10524 review P2). ``''`` is a MEANINGFUL value on the first of them: it is
    what ``expand_relationships`` reserves for a layer with no ``status`` family
    at all (``:Event``), so reusing it for "absent" conflates the two.

    The unit-level contract is pinned here; the two surfaces are pinned end to
    end in ``test_expand_relationships_6976.py::
    test_related_status_survives_the_widening`` (which exercises the ABSENT case
    rather than writing a status first) and
    ``test_subject_supersession_1423.py::
    test_provenance_chain_surfaces_the_subject_status``.
    """

    def test_absent_reads_live(self):
        from tortoise.live import status_as_reported

        assert status_as_reported(None) == "live"
        # every label whose layer carries a `status` family, explicitly
        for label in ("Point", "Subject", "Object"):
            assert status_as_reported(None, label=label) == "live", label

    def test_the_event_carve_out_is_not_flattened_to_live(self):
        """``:Event``'s lifecycle is ``eventStatus`` — ``''`` is the honest
        answer there, and it is the value ``''`` must KEEP meaning."""
        from tortoise.live import status_as_reported

        assert status_as_reported(None, label="Event") == ""

    def test_a_stored_status_is_never_defaulted(self):
        """The helper fills an ABSENT value only: a terminal status must pass
        through untouched, or every surface would report a dead claim live."""
        from tortoise.live import status_as_reported

        for value in ("superseded", "retracted", "outdated", "archived",
                      "draft", "live", ""):
            assert status_as_reported(value) == value, value
            assert status_as_reported(value, label="Event") == value, value

    def test_recall_subgraph_renders_an_absent_status_as_live(self, tmp_path):
        """The FIFTH read surface (#9974 R1 review P2). ``ranking``'s subgraph
        expansion rendered ``coalesce(n.status, '')`` and only set the key
        ``if status:`` — so a status-less Object/Subject DROPPED the ``status``
        key entirely, where it carried ``"live"`` before the fabrication was
        removed. ``SUBNODE_LABELS`` includes ``Object``/``Subject``, and
        ``recall_subgraph`` is a public MCP tool (``tortoise_recall(mode="subgraph")``):
        a consumer reading ``node["status"]`` raised.

        RED on the pre-fix rendering (the key is absent), so this leg is the
        surface's own guard rather than a restatement of the helper's.
        """
        sdk = TortoiseSDK(str(tmp_path / "g.db"))
        created = sdk.create_object("statusless-object", objectKind="core:strategy")
        oid = (created.get("node") or created)["id"]
        res = sdk.recall_subgraph(oid, depth=1)
        nodes = {n["id"]: n for n in res["nodes"]}
        node = nodes[oid]
        assert node["type"] == "object", node
        assert node["status"] == "live", (
            "an absent stored status must RENDER live on the subgraph surface, "
            f"not vanish from the payload: {node!r}")

    def test_the_status_carrying_set_is_a_partition_of_the_epistemic_labels(self):
        """A new epistemic label forces a decision here instead of silently
        inheriting "absent reads live" (or the Event carve-out)."""
        from tortoise.live import (
            EPISTEMIC_LABELS,
            STATUS_CARRYING_EPISTEMIC_LABELS,
        )

        assert set(EPISTEMIC_LABELS) == (
            STATUS_CARRYING_EPISTEMIC_LABELS | {"Event"}), (
            "a label was added to EPISTEMIC_LABELS without deciding whether its "
            "layer carries a `status` family")
