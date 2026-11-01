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
    def test_linear_card_completed(self):
        sdk = _fresh_sdk()
        try:
            sdk.create_entity("object", "TASK-42", objectKind="dev:issue")
            sdk.create_event("card done", "pm:cardCompleted",
                             object="TASK-42", endedAt="2026-08-18")
            rows = sdk._get_proj().g.query(
                "MATCH (o:Object {name:$n}) RETURN o.status",
                params={"n": "TASK-42"}).result_set
            assert rows and rows[0][0] == "completed"
        finally:
            sdk.close()

    def test_github_issue_open_and_close(self):
        sdk = _fresh_sdk()
        try:
            sdk.create_entity("object", "the bug", objectKind="dev:issue")
            sdk.create_event("opened", "github.issue.open", object="the bug")
            rows = sdk._get_proj().g.query(
                "MATCH (o:Object {name:$n}) RETURN o.status",
                params={"n": "the bug"}).result_set
            assert rows[0][0] == "in_progress"
            sdk.create_event("closed", "github.issue.closed", object="the bug")
            rows = sdk._get_proj().g.query(
                "MATCH (o:Object {name:$n}) RETURN o.status",
                params={"n": "the bug"}).result_set
            assert rows[0][0] == "completed"
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
                "MATCH (o:Object {name:$n}) RETURN o.status",
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
                        "MATCH (o:Object {name:$n}) RETURN o.status",
                        params={"n": name}).result_set
                    assert rows and rows[0][0] == status, (
                        f"{ev_kind} resurrected a terminal Object: "
                        f"{status} -> {rows[0][0] if rows else 'MISSING'}")
            finally:
                sdk.close()


# ── Read side (Steps 8-9) ────────────────────────────────────────────

class TestReadSide:
    def test_object_result_carries_the_status_the_graph_holds(self):
        """#9974 R1: the read carries the status the GRAPH holds.

        For a status-less birth that is now ABSENT (the graph stores no fabricated
        `live`), and the result omits the key — the graph's own reading of an
        absent status is LIVE (`live._terminal_excluded`). The anti-vacuity half
        is the second leg below: a status the caller DID supply is still carried,
        so this cannot pass by the read dropping `status` altogether.
        """
        sdk = _fresh_sdk()
        try:
            sdk.create_entity("object", "the strategy", objectKind="core:strategy")
            hits = sdk.tortoise_fts_query("the strategy", kind="core:strategy",
                                          entity_type="object", limit=5)
            assert hits, "seeded object must be retrievable (kind-scoped)"
            assert hits[0].get("status") is None, hits[0]

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
                sdk.update_entity(oid, **carriers)
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
            sdk.update_entity(oid, status="superseded", supersededBy="alpha-v2",
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

    def test_clearing_a_supersession_is_still_writable(self, tmp_path):
        """`supersededBy=None` is a CLEAR, not a carrier — never refused.

        This is the record-carried un-supersede (`cache_projection`'s
        NOT MONOTONE bullet): the mutation writes both carrier columns, so a
        rebuild derives nothing and agrees with the graph.
        """
        sdk = TortoiseSDK(str(tmp_path / "t10192x.db"))
        try:
            oid = _make_carrier_entity(sdk, "Object", "beta")
            sdk.update_entity(oid, status="superseded", supersededBy="beta-v2",
                              supersededAt="2026-01-01T00:00:00Z")
            sdk.update_entity(oid, status="live", supersededBy=None,
                              supersededAt=None)
            assert _carrier_row(sdk, "Object", "beta")[1:] == ("live", None,
                                                               None)
            # …and the SNAKE spelling of a clear is a clear too: `None` is not a
            # carrier under either spelling, so the guard must not over-block it
            # (review P1 extended the guard to that spelling, not the CLEAR).
            sdk.update_entity(oid, superseded_by=None, superseded_at=None)
            assert _carrier_row(sdk, "Object", "beta")[1:] == ("live", None,
                                                               None)
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
            sdk.update_entity(oid, status="superseded", supersededBy="gamma-v2")
            assert _carrier_row(sdk, "Object", "gamma")[1:] == ("superseded",
                                                                "gamma-v2",
                                                                None)
            sdk.update_entity(oid, supersededAt="2026-02-02T00:00:00Z")
            assert _carrier_row(sdk, "Object", "gamma")[1:] == (
                "superseded", "gamma-v2", "2026-02-02T00:00:00Z")
            # Same under the snake spelling: the guard reads the RESULT status,
            # so it is a coherence guard and not a ban on a property name.
            sdk.update_entity(oid, superseded_by="gamma-v3")
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

    def test_the_mirror_shape_is_not_refused(self, tmp_path):
        """`status='superseded'` with NO carrier is OUT of scope, by decision.

        It makes a rebuild REVIVE a node rather than bury a live one, it is a
        shape in use, and it is already named as #9974's window. Refusing it
        here would be the over-block the change's scope contract forbids.
        """
        sdk = TortoiseSDK(str(tmp_path / "t10192m.db"))
        try:
            oid = _make_carrier_entity(sdk, "Subject", "delta")
            sdk.update_entity(oid, status="superseded")
            assert _carrier_row(sdk, "Subject", "delta")[1:] == (
                "superseded", None, None)
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
                sdk.update_entity(oid, supersededBy="v2")
            assert _carrier_row(sdk, "Object", "epsilon")[1] is None, (
                "the graph's reading of the refused node must stay LIVE "
                "(#9974 R1: an absent status IS live, and a stored 'live' "
                "would mean something DID fabricate one)")
            with pytest.raises(ValueError, match="lifecycle CARRIER"):
                sdk.update_entity(oid, superseded_by="v2")
            assert _carrier_row(sdk, "Object", "epsilon")[1] is None, (
                "a snake-spelled carrier was ACCEPTED on a live node — the "
                "guard is inert against it (review P1)")
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

    def test_a_caller_supplied_birth_status_still_lands_on_both_routes(
            self, tmp_path):
        """Anti-vacuity: DERIVED means the record's value is carried, not lost.

        Without this leg the test above would pass if the birth status were
        simply dropped — the same fabrication failure in the other direction.
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

            assert status_of("theta") == "archived", status_of("theta")
            proj.rebuild_all(str(events), confirm_destructive=True)
            assert status_of("theta") == "archived", (
                "a caller-supplied birth status was dropped by replay: "
                f"{status_of('theta')!r}")
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
