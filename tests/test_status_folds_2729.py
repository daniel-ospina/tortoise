"""#2729 — pack-declared per-kind Object status folds.

The mechanism replaces the hand-typed ``if/elif`` producer→status map in
``projection/entities.py`` with a per-kind declaration in the pack manifest
(``kindDefs[<kind>].statuses`` + ``.folds``). This file pins four contracts:

1. **Validation.** An unknown fold status, a fold without ``statuses``, an
   orphan fold event, or a malformed list isolates the pack (a hard error). A
   declared status that reuses a shared/connector vocabulary word
   (``open``/``closed``/``active``/``pending``) is an explicit, non-blocking
   warning on ``PackRegistry.lifecycle_warnings``.
2. **Built-in parity.** The connector compatibility folds are retained
   verbatim and reached through the same dispatcher, so a graph with no
   lifecycle-declaring pack behaves exactly as before.
3. **Ownership.** A kind that declares ``statuses`` owns its Object state: a
   connector lifecycle event no longer re-labels it.
4. **Declaration is honoured end-to-end.** A declared fold event flips
   ``Object.status`` to the declared value — proven on the shipped venture pack.

Run (embedded carve-out)::

    TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest \\
        tests/test_status_folds_2729.py -q -p no:cacheprovider
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: I001
from tortoise.pack_registry import PackRegistry, default_packs_dir
from tortoise.pack_manifest_store import validate_manifest
from tortoise.status_folds import (
    BUILTIN_CONNECTOR_FOLDS,
    SHARED_CONNECTOR_STATUS_WORDS,
    StatusFoldIndex,
    bare_lifecycle_kind_collisions,
    build_status_fold_index,
    builtin_fold_status,
    validate_status_decl,
)
from tortoise.sdk import TortoiseSDK


def _fresh_sdk() -> TortoiseSDK:
    return TortoiseSDK(os.path.join(tempfile.mkdtemp(), "t.db"))


def _object_status(sdk: TortoiseSDK, name: str):
    rows = sdk._get_proj().g.query(
        "MATCH (o:Object {name:$n}) RETURN o.status",
        params={"n": name}).result_set
    return rows[0][0] if rows else None


def _decl(event: str, status) -> dict:
    return {"event": event, "status": status}


# ── 1. Declaration validation (DB-free) ──────────────────────────────────


class TestStatusDeclValidation:
    def _validate(self, spec: dict, *, declared=("trancheReleased",)):
        return validate_status_decl(
            "tranche", spec, namespace="demo",
            declared_event_kinds=set(declared),
            declared_object_kinds={"tranche"})

    def test_declaration_on_a_non_object_kind_is_rejected(self):
        """A lifecycle on a core kind would validate but never fold (the
        dispatch key is ``ns:kind``), so it must be a hard error."""
        errors, _ = validate_status_decl(
            "task", {"statuses": ["todo", "done"]}, namespace="demo",
            declared_event_kinds=set(), declared_object_kinds={"tranche"})
        assert any("objectKinds" in e for e in errors)

    def test_well_formed_declaration_has_no_errors_or_warnings(self):
        errors, warnings = self._validate(
            {"statuses": ["queued", "released"],
             "folds": [_decl("trancheReleased", "released")]})
        assert errors == []
        assert warnings == []

    def test_unknown_fold_status_is_rejected(self):
        """The verification checklist's 'unknown status rejected' row."""
        errors, _ = self._validate(
            {"statuses": ["pending", "released"],
             "folds": [_decl("trancheReleased", "shipped")]})
        assert any("not in the kind's declared statuses" in e for e in errors)

    def test_folds_require_statuses(self):
        errors, _ = self._validate(
            {"folds": [_decl("trancheReleased", "released")]})
        assert any("'folds' requires 'statuses'" in e for e in errors)

    def test_statuses_must_be_non_empty_unique_list(self):
        errors, _ = self._validate({"statuses": []})
        assert any("non-empty list" in e for e in errors)
        errors, _ = self._validate({"statuses": ["a", "a"]})
        assert any("duplicates" in e for e in errors)
        errors, _ = self._validate({"statuses": ["a", ""]})
        assert any("non-empty strings" in e for e in errors)

    def test_unhashable_statuses_entry_is_a_diagnostic_not_a_crash(self):
        """A payload-controlled non-string entry (`statuses: [{a: 1}]`) must
        return the validator's error list, not raise `TypeError: unhashable
        type` out of a function whose contract is `-> (errors, warnings)`.
        (Pre-fix the duplicate check ran `set(statuses)` over the raw list.)"""
        for bad in ({"a": 1}, [1]):
            errors, _ = self._validate({"statuses": [bad]})
            assert any("non-empty strings" in e for e in errors)
        errors, _ = self._validate({"statuses": [[1], "ok"]})
        assert any("non-empty strings" in e for e in errors)

    def test_duplicate_fold_event_on_one_kind_is_rejected(self):
        """Two folds naming the same event on one kind would silently
        last-win in the index (and in `_fold_object_status`'s declaration
        order); #2729's precedence question is open, so this is an error."""
        errors, _ = self._validate({
            "statuses": ["a", "b"],
            "folds": [_decl("trancheReleased", "a"),
                      _decl("trancheReleased", "b")]})
        assert any("more than once" in e for e in errors)

    def test_duplicate_fold_event_across_admitted_spellings_is_rejected(self):
        """#2729 review P2: the index resolves the bare and the
        self-namespaced spelling (and any case variant) to ONE key, so the
        duplicate guard must compare the RESOLVED name — not the raw string,
        which let `trancheReleased` + `demo:trancheReleased` both index and
        the second silently win. (Pre-fix the errors list is empty.)"""
        for second in ("demo:trancheReleased", "TRANCHeReleased"):
            errors, _ = self._validate({
                "statuses": ["a", "b"],
                "folds": [_decl("trancheReleased", "a"),
                          _decl(second, "b")]})
            assert any("more than once" in e for e in errors), (second, errors)
        # Parity control: two genuinely distinct events are still accepted.
        errors, _ = self._validate({
            "statuses": ["a", "b"],
            "folds": [_decl("trancheReleased", "a"),
                      _decl("otherEvent", "b")]},
            declared=("trancheReleased", "otherEvent"))
        assert errors == []

    def test_two_distinct_fold_events_are_still_accepted(self):
        """Parity control: only the SAME event twice is rejected."""
        errors, _ = self._validate({
            "statuses": ["a", "b"],
            "folds": [_decl("trancheReleased", "a"),
                      _decl("demo:otherEvent", "b")]},
            declared=("trancheReleased", "otherEvent"))
        assert errors == []

    def test_orphan_fold_event_is_rejected(self):
        errors, _ = self._validate(
            {"statuses": ["released"],
             "folds": [_decl("neverDeclared", "released")]})
        assert any("is not an event kind declared by this pack" in e
                   for e in errors)

    def test_self_namespaced_event_resolves_but_foreign_namespace_is_orphan(self):
        errors, _ = self._validate(
            {"statuses": ["released"],
             "folds": [_decl("demo:trancheReleased", "released")]})
        assert errors == []
        errors, _ = self._validate(
            {"statuses": ["released"],
             "folds": [_decl("other:trancheReleased", "released")]})
        assert any("orphan" in e or "not an event kind declared by this pack" in e
                   for e in errors)

    def test_fold_unknown_key_rejected(self):
        errors, _ = self._validate(
            {"statuses": ["released"],
             "folds": [{"event": "trancheReleased", "status": "released",
                        "when": "always"}]})
        assert any("unknown key" in e for e in errors)

    def test_non_string_fold_key_is_a_diagnostic_not_a_crash(self):
        """A raw YAML fold map can carry a non-string key (``1: x``). The
        unknown-key path used to ``sorted()`` a mixed key set and ``join()``
        it, raising ``TypeError`` out of a function whose contract is
        ``-> (errors, warnings)``. It must return the diagnostic instead.
        (Pre-fix this raises: `'<' not supported between 'str' and 'int'`.)"""
        errors, _ = self._validate(
            {"statuses": ["released"],
             "folds": [{1: "x", "zzz": "y",
                        "event": "trancheReleased",
                        "status": "released"}]})
        assert any("non-string key" in e for e in errors), errors
        assert any("unknown key" in e for e in errors), errors
        # The message must render, not blow up, with the mixed key set.
        assert any("zzz" in e for e in errors), errors

    def test_non_string_declared_event_kind_is_a_diagnostic_not_a_crash(self):
        """``ontology.eventKinds: [1]`` reaches the orphan-fold message, which
        used to ``join()`` the raw declared set. It must render the diagnostic
        rather than raise ``TypeError: sequence item 0: expected str``.
        (Pre-fix this raises on the orphan-fold f-string.)"""
        errors, _ = validate_status_decl(
            "tranche",
            {"statuses": ["released"],
             "folds": [_decl("nope", "released")]},
            namespace="demo",
            declared_event_kinds={1},
            declared_object_kinds={"tranche"})
        assert any("is not an event kind declared by this pack" in e
                   for e in errors), errors

    def test_shared_vocabulary_overlap_is_a_warning_not_an_error(self):
        errors, warnings = self._validate(
            {"statuses": ["open", "done"],
             "folds": [_decl("trancheReleased", "done")]})
        assert errors == []
        assert any("open" in w and "shared connector vocabulary" in w
                   for w in warnings)

    def test_reserved_words_set_is_the_documented_one(self):
        assert frozenset({
            "open", "closed", "active", "pending", "in_progress", "completed"}) == SHARED_CONNECTOR_STATUS_WORDS


class TestRegistryWiring:
    """The validator above must be REACHED by ``PackRegistry._validate``."""

    def _manifest(self, kind_defs: dict) -> dict:
        return {
            "namespace": "demo", "name": "Demo", "tier": "free",
            "ontology": {
                "extends": "core",
                "objectKinds": ["tranche"],
                "eventKinds": ["trancheReleased"],
                "pointKinds": [], "documentKinds": [], "actionKinds": [],
                "kindDefs": kind_defs,
            },
        }

    def test_unknown_fold_status_isolates_the_pack(self):
        raw = self._manifest({"tranche": {
            "statuses": ["pending", "released"],
            "folds": [_decl("trancheReleased", "shipped")]}})
        errors = PackRegistry("/tmp/nonexistent")._validate(raw)
        assert any("not in the kind's declared statuses" in e for e in errors)

    def test_lifecycle_keys_are_admitted_by_the_kinddef_schema(self):
        raw = self._manifest({"tranche": {
            "statuses": ["pending", "released"],
            "folds": [_decl("trancheReleased", "released")]}})
        errors = PackRegistry("/tmp/nonexistent")._validate(raw)
        assert not any("unknown key" in e for e in errors), errors

    def test_overlap_warning_is_recorded_not_an_error(self):
        raw = self._manifest({"tranche": {"statuses": ["open", "done"]}})
        reg = PackRegistry("/tmp/nonexistent")
        errors = reg._validate(raw)
        assert not any("statuses" in e for e in errors), errors
        assert "demo" in reg._pending_lifecycle_warnings

    def test_non_string_event_kind_entry_is_an_error_not_a_crash(self):
        """``ontology.eventKinds: [1]`` with a declared fold: the ontology loop
        already records the invalid value, but the declaration check used to
        ``set()``/``sorted()`` the raw list and raise out of ``_validate``.
        (Pre-fix this raises ``TypeError`` in the orphan-fold message.)"""
        raw = self._manifest({"tranche": {
            "statuses": ["pending", "released"],
            "folds": [_decl("trancheReleased", "released")]}})
        raw["ontology"]["eventKinds"] = [1]
        errors = PackRegistry("/tmp/nonexistent")._validate(raw)
        assert any("invalid kind value" in e for e in errors), errors
        # With the numeric entry rejected, the fold's event genuinely has no
        # valid declaration to resolve against — an orphan diagnostic, not a
        # crash or a silent pass.
        assert any("not an event kind declared by this pack" in e
                   for e in errors), errors


# ── 2. Built-in parity ───────────────────────────────────────────────────


class TestBuiltinParity:
    def test_builtin_map_is_the_historical_producer_to_status_map(self):
        """The exact map the hand-typed ``if/elif`` carried (#1350/#1725)."""
        assert BUILTIN_CONNECTOR_FOLDS == {
            "pm:cardCreated": "in_progress",
            "github.issue.open": "in_progress",
            "github.issue.reopened": "in_progress",
            "pm:cardCompleted": "completed",
            "github.issue.closed": "completed",
        }

    def test_builtin_lookup_helper_agrees(self):
        for event, status in BUILTIN_CONNECTOR_FOLDS.items():
            assert builtin_fold_status(event) == status
        assert builtin_fold_status("venture:disbursement") is None

    def test_empty_index_declares_nothing(self):
        idx = StatusFoldIndex()
        assert idx.lifecycle_kinds == frozenset()
        assert idx.folds_for_event("venture:disbursement") == []
        assert not idx.owns_state("venture:tranche")


class TestIndexCompilation:
    class _Pack:
        def __init__(self, object_kinds, kind_defs):
            self.object_kinds = object_kinds
            self.kind_defs = kind_defs

    def test_namespaced_kind_is_the_dispatch_key(self):
        """Two packs declaring the same bare kind name do NOT collide."""
        packs = {
            "a": self._Pack(["thing"], {"thing": {
                "statuses": ["a-done"],
                "folds": [_decl("thingDone", "a-done")]}}),
            "b": self._Pack(["thing"], {"thing": {
                "statuses": ["b-done"],
                "folds": [_decl("thingDone", "b-done")]}}),
        }
        idx = build_status_fold_index(packs)
        assert idx.lifecycle_kinds == frozenset({"a:thing", "b:thing"})
        assert idx.folds_for_event("a:thingDone") == [("a:thing", "a-done")]
        assert idx.folds_for_event("b:thingDone") == [("b:thing", "b-done")]

    def test_a_kind_with_statuses_but_no_folds_still_owns_its_state(self):
        packs = {"a": self._Pack(["thing"], {"thing": {"statuses": ["x"]}})}
        idx = build_status_fold_index(packs)
        assert idx.owns_state("a:thing")
        assert idx.statuses_for("a:thing") == ("x",)


# ── 3. The shipped venture pack declares the surface ─────────────────────


@pytest.fixture(scope="module")
def venture_registry():
    reg = PackRegistry(default_packs_dir())
    reg.load_all()
    return reg


class TestVentureDeclaration:
    def test_all_seven_object_kinds_declare_statuses(self, venture_registry):
        venture = venture_registry.get_pack("venture")
        for kind in venture.object_kinds:
            assert venture.kind_defs[kind].get("statuses"), kind

    def test_the_three_lifecycle_events_carry_a_fold(self, venture_registry):
        idx = venture_registry.status_fold_index
        assert idx.folds_for_event("venture:disbursement") == [
            ("venture:tranche", "released")]
        assert idx.folds_for_event("venture:conditionMet") == [
            ("venture:condition", "met")]
        assert idx.folds_for_event("venture:actionItemCompleted") == [
            ("venture:actionItem", "done")]

    def test_every_fold_status_is_in_its_kinds_vocabulary(self, venture_registry):
        venture = venture_registry.get_pack("venture")
        for kind in venture.object_kinds:
            spec = venture.kind_defs[kind]
            statuses = set(spec.get("statuses", []))
            for fold in spec.get("folds", []):
                assert fold["status"] in statuses, (kind, fold)

    def test_venture_loads_without_errors(self, venture_registry):
        assert not venture_registry.errors

    def test_no_declared_fold_is_orphaned_in_the_shipped_catalog(
            self, venture_registry):
        """The orphan lint (amendment (d)) over EVERY shipped pack: each
        declared fold's event resolves to an event kind its pack declares.

        The built-in compatibility map is exempt by design — its ``pm:`` keys
        are the pre-namespacing legacy spellings replayed from old journals
        (``status_folds.BUILTIN_CONNECTOR_FOLDS`` documents why), which is why
        this test reads the PACK declarations, not that map.
        """
        reg = venture_registry
        declared_events = {
            f"{ns}:{kind}"
            for ns, pack in reg.packs.items() for kind in pack.event_kinds}
        for ns, pack in reg.packs.items():
            for kind in pack.object_kinds:
                for fold in pack.kind_defs.get(kind, {}).get("folds", []):
                    local = fold["event"].partition(":")[2] or fold["event"]
                    assert f"{ns}:{local}" in declared_events, (ns, kind, fold)

    def test_shared_vocabulary_overlap_is_recorded_for_the_shipped_pack(
            self, venture_registry):
        """The overlap is DETECTED (amendment (c)) — ``active``/``closed``/
        ``pending``/``open`` are reused by the owner-approved lifecycles, and
        the registry says so instead of silently inheriting connector meaning.
        """
        msgs = venture_registry.lifecycle_warnings.get("venture", [])
        joined = " ".join(msgs)
        for word in ("active", "closed", "pending", "open"):
            assert word in joined, (word, msgs)


# ── 4. End-to-end behaviour (embedded) ───────────────────────────────────


class TestFoldBehaviour:
    def test_declared_fold_flips_status_end_to_end(self):
        """Indicator (1): a declared rule flips Object.status."""
        sdk = _fresh_sdk()
        try:
            sdk.create_entity("object", "AI-1", objectKind="venture:actionItem")
            sdk.create_event("AI-1 done", "venture:actionItemCompleted",
                             object="AI-1")
            assert _object_status(sdk, "AI-1") == "done"
        finally:
            sdk.close()

    def test_tranche_disbursement_fold(self):
        sdk = _fresh_sdk()
        try:
            sdk.create_entity("object", "T1", objectKind="venture:tranche")
            sdk.create_event("T1 released", "venture:disbursement",
                             object="T1")
            assert _object_status(sdk, "T1") == "released"
        finally:
            sdk.close()

    def test_lifecycle_owning_kind_is_not_relabelled_by_the_connector_fold(self):
        """Contract 2: the collision the #2725 check found cannot fire."""
        sdk = _fresh_sdk()
        try:
            sdk.create_entity("object", "T2", objectKind="venture:tranche")
            sdk.create_event("closed", "github.issue.closed", object="T2")
            assert _object_status(sdk, "T2") == "live"
        finally:
            sdk.close()

    def test_undeclared_kind_still_takes_the_builtin_fold(self):
        """Parity control for the test above — the built-in fold is untouched
        for a kind that declares no lifecycle."""
        sdk = _fresh_sdk()
        try:
            sdk.create_entity("object", "the bug", objectKind="dev:issue")
            sdk.create_event("opened", "github.issue.open", object="the bug")
            assert _object_status(sdk, "the bug") == "in_progress"
            sdk.create_event("closed", "github.issue.closed", object="the bug")
            assert _object_status(sdk, "the bug") == "completed"
        finally:
            sdk.close()

    def test_legacy_pm_fold_key_still_folds(self):
        """The legacy ``pm:cardCompleted`` spelling is kept as a built-in so
        pre-namespacing journals replay identically."""
        sdk = _fresh_sdk()
        try:
            sdk.create_entity("object", "TASK-42", objectKind="dev:issue")
            sdk.create_event("done", "pm:cardCompleted", object="TASK-42")
            assert _object_status(sdk, "TASK-42") == "completed"
        finally:
            sdk.close()

    def test_declared_fold_never_resurrects_a_terminal_object(self):
        """#2164/#3309 guard carried through the declared path too."""
        sdk = _fresh_sdk()
        try:
            sdk.create_entity("object", "T3", objectKind="venture:tranche")
            sdk._get_proj()._fold_object_superseded(
                {"name": "T3", "supersedes_by": "T3-v2"})
            sdk.create_event("released", "venture:disbursement", object="T3")
            assert _object_status(sdk, "T3") == "superseded"
        finally:
            sdk.close()

    def test_unapplied_declared_fold_emits_a_violation(self, caplog):
        """A declared fold that finds no Object of its kind is reported at the
        kind's #1934 enforcement level, not swallowed."""
        import logging
        sdk = _fresh_sdk()
        try:
            sdk.create_entity("object", "not-an-action-item",
                              objectKind="dev:issue")
            with caplog.at_level(logging.WARNING):
                sdk.create_event("done", "venture:actionItemCompleted",
                                 object="not-an-action-item")
            assert any("status_fold_unapplied" in r.getMessage()
                       for r in caplog.records)
        finally:
            sdk.close()


# ── 5. Form-widened dispatch (#2729 review P1/P2) ────────────────────────


class TestFormWidenedDispatch:
    """The write gate admits a pack kind BOTH namespaced and bare, case-folded
    (``extractor_v2._object_kind_forms``/``_event_kind_forms`` ``.lower()``
    every form, and no path normalizes the stored value), and the extraction
    commit strips the event namespace. Keying the index on the namespaced
    spelling alone made a declared fold inert — and the built-in connector fold
    re-applied to a bare-spelled lifecycle kind, writing a status outside its
    declared vocabulary."""

    def test_bare_object_and_bare_event_kind_fold(self):
        sdk = _fresh_sdk()
        try:
            sdk.create_entity("object", "B1", objectKind="tranche")
            sdk.create_event("B1 released", "disbursement", object="B1")
            assert _object_status(sdk, "B1") == "released"
        finally:
            sdk.close()

    def test_bare_object_with_namespaced_event_folds(self):
        sdk = _fresh_sdk()
        try:
            sdk.create_entity("object", "B2", objectKind="tranche")
            sdk.create_event("B2 released", "venture:disbursement", object="B2")
            assert _object_status(sdk, "B2") == "released"
        finally:
            sdk.close()

    def test_namespaced_object_with_bare_event_folds(self):
        sdk = _fresh_sdk()
        try:
            sdk.create_entity("object", "B3", objectKind="venture:tranche")
            sdk.create_event("B3 released", "disbursement", object="B3")
            assert _object_status(sdk, "B3") == "released"
        finally:
            sdk.close()

    def test_bare_spelled_lifecycle_kind_is_not_relabelled_by_the_connector_fold(self):
        """The wrong-state write the review reproduced (row B): a bare-spelled
        ``tranche`` took the connector's ``completed``, a value outside the
        kind's declared vocabulary ``[pending, conditions-met, released,
        denied]``."""
        sdk = _fresh_sdk()
        try:
            sdk.create_entity("object", "B4", objectKind="tranche")
            sdk.create_event("closed", "github.issue.closed", object="B4")
            assert _object_status(sdk, "B4") == "live"
        finally:
            sdk.close()

    def test_case_varied_spelling_folds_and_is_owned(self):
        sdk = _fresh_sdk()
        try:
            sdk.create_entity("object", "B5", objectKind="VENTURE:TRANCHE")
            sdk.create_event("B5 released", "Venture:Disbursement", object="B5")
            assert _object_status(sdk, "B5") == "released"
            sdk.create_entity("object", "B6", objectKind="Tranche")
            sdk.create_event("closed", "github.issue.closed", object="B6")
            assert _object_status(sdk, "B6") == "live"
        finally:
            sdk.close()

    def test_index_registers_both_spellings(self, venture_registry):
        idx = venture_registry.status_fold_index
        assert idx.folds_for_event("disbursement") == [
            ("venture:tranche", "released")]
        assert idx.folds_for_event("Venture:Disbursement") == [
            ("venture:tranche", "released")]
        assert "venture:tranche" in idx.kind_forms
        assert "tranche" in idx.kind_forms
        assert idx.owns_state("tranche")


# ── 6. The extraction pipeline's own event shape (#2729 review P1) ───────


_PIPELINE_EMBED = {
    "entities": [{"name": "T1", "kind": "venture:tranche",
                  "lifecycle": "created", "supersedes": None, "note": None}],
    "events": [{"content": "T1 released", "eventKind": "venture:disbursement",
                "about_entities": ["T1"]}],
    "points": [], "operators": [], "chain_notes": [],
    "link_before_create": [],
}


class TestExtractionPipelineFold:
    """The acceptance indicator must hold for the shape the PIPELINE emits, not
    only for the SDK door: `execute_embed` writes a BARE event kind
    (`sdk.py::_extract_session_v2` does `.rsplit(":", 1)[-1]`) and carries
    `about_entities`, not `object`."""

    def test_payload_event_shape_is_the_pipeline_shape(self):
        """Pin the producer: bare event kind + `about_entities`, no `object`."""
        import tortoise.extractor_v2 as v2

        payload = v2.execute_embed(
            _PIPELINE_EMBED, {}, session_id="s1")["payload"]
        event = payload["events"][0]
        assert event["eventKind"] == "disbursement"
        assert "object" not in event
        assert event["about_entities"] == ["T1"]
        assert payload["entities"][0]["kind"] == "venture:tranche"

    def test_declared_fold_fires_through_execute_embed_and_the_commit(
            self, monkeypatch):
        import tortoise.extractor_v2 as v2

        monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
        payload = v2.execute_embed(
            _PIPELINE_EMBED, {}, session_id="s1")["payload"]
        monkeypatch.setattr(
            v2, "extract_session_v2",
            lambda *a, **k: {"payload": payload, "errors": [], "warnings": []})
        sdk = _fresh_sdk()
        try:
            sdk._extract_session_v2(
                [{"role": "user", "content": "T1 released"}], "s1",
                "2026-01-01T00:00:00Z")
            assert _object_status(sdk, "T1") == "released"
        finally:
            sdk.close()

    def test_builtin_fold_still_does_not_fire_via_about_entities(self):
        """Parity control: the widening is for DECLARED folds only — routing
        `about_entities` must not let the built-in connector fold apply through
        a list the old code never read."""
        sdk = _fresh_sdk()
        try:
            sdk.create_entity("object", "ISSUE", objectKind="dev:issue")
            sdk.create_event("opened", "github.issue.open",
                             about_entities=["ISSUE"])
            assert _object_status(sdk, "ISSUE") == "live"
        finally:
            sdk.close()


# ── 7. Registry outage keeps the ownership guard (#2729 review minor) ────


class TestOwnershipSurvivesRegistryOutage:
    def test_outage_keeps_the_last_good_kind_forms(self, monkeypatch):
        from tortoise.projection import entities as ents

        warm = ents._status_fold_index()
        assert "venture:tranche" in warm.kind_forms
        assert "venture:tranche" in ents._LAST_GOOD_KIND_FORMS

        def _boom():
            raise RuntimeError("packs unavailable")

        monkeypatch.setattr("tortoise.domain_loader._get_registry", _boom)
        degraded = ents._status_fold_index()
        # The declared folds are gone (the registry that compiles them is), but
        # the OWNERSHIP half survives.
        assert degraded.folds_for_event("venture:disbursement") == []
        assert degraded.kind_forms == warm.kind_forms
        assert degraded.owns_state("venture:tranche")

    def test_builtin_fold_respects_ownership_when_declared_folds_are_empty(
            self, monkeypatch):
        """End-to-end of the guard: an index with no declared folds but the
        last-good ownership forms must still suppress the connector fold for a
        lifecycle-owning kind (pre-fix, an empty index re-applied it)."""
        from tortoise.projection import entities as ents

        warm = ents._status_fold_index()
        degraded = StatusFoldIndex()
        degraded.kind_forms = warm.kind_forms
        monkeypatch.setattr(ents, "_status_fold_index", lambda: degraded)
        sdk = _fresh_sdk()
        try:
            sdk.create_entity("object", "O1", objectKind="venture:tranche")
            sdk.create_event("closed", "github.issue.closed", object="O1")
            assert _object_status(sdk, "O1") == "live"
        finally:
            sdk.close()


# ── 8. Non-string object kinds and multi-pack key sharing (#2729 review) ──


class TestFoldQueryTypeSafety:
    """#2729 review P1: the ownership guard must not ADD a failure mode to a
    query that previously tolerated any ``objectKind``.

    Pre-#2729 the built-in fold never read ``objectKind``; the guard compares
    it, and a raw ``toLower`` raises ``Type mismatch`` on a non-string value
    (``create_entity(objectKind=…)`` / ``uses[].kind`` are payload-supplied),
    which aborted the WHOLE event write. ``toStringOrNull`` coerces an integer
    and nulls an array, so the fold degrades instead of raising.
    """

    def test_integer_object_kind_does_not_break_the_builtin_fold(self):
        sdk = _fresh_sdk()
        try:
            sdk.create_entity("object", "N1", objectKind=5)
            sdk.create_event("closed", "github.issue.closed", object="N1")
            assert _object_status(sdk, "N1") == "completed"
        finally:
            sdk.close()

    def test_array_object_kind_does_not_break_the_builtin_fold(self):
        sdk = _fresh_sdk()
        try:
            sdk.create_entity("object", "N2", objectKind=["a", "b"])
            sdk.create_event("closed", "github.issue.closed", object="N2")
            assert _object_status(sdk, "N2") == "completed"
        finally:
            sdk.close()

    def test_integer_object_kind_does_not_abort_a_declared_fold_write(self):
        """The declared half reads the kind too; a non-string must be a
        non-match, not an exception that loses the event."""
        sdk = _fresh_sdk()
        try:
            sdk.create_entity("object", "N3", objectKind=5)
            sdk.create_event("released", "venture:disbursement", object="N3")
            assert _object_status(sdk, "N3") == "live"
            # The write itself landed (the Event exists) rather than raising
            # and losing the whole write.
            rows = sdk._get_proj().g.query(
                "MATCH (e:Event) RETURN count(e) AS n",
                params={}).result_set
            assert rows and rows[0][0] == 1
        finally:
            sdk.close()


class TestMultiPackEventKeySharing:
    """#2729 review P2: two packs declaring the same BARE event kind share the
    form-widened key, so the non-matching pack's kind reports a miss for a
    write that folded correctly. A miss is only a real signal when NO declared
    fold for the event folded anything."""

    class _Pack:
        def __init__(self, object_kinds, kind_defs):
            self.object_kinds = object_kinds
            self.kind_defs = kind_defs

    def test_other_packs_kind_miss_is_not_reported_when_one_target_matched(
            self, monkeypatch, caplog):
        import logging

        from tortoise.projection import entities as ents

        idx = build_status_fold_index({
            "a": self._Pack(["thing"], {"thing": {
                "statuses": ["a-done"],
                "folds": [_decl("thingDone", "a-done")]}}),
            "b": self._Pack(["thing"], {"thing": {
                "statuses": ["b-done"],
                "folds": [_decl("thingDone", "b-done")]}}),
        })
        assert len(idx.folds_for_event("thingDone")) == 2
        monkeypatch.setattr(ents, "_status_fold_index", lambda: idx)
        sdk = _fresh_sdk()
        try:
            sdk.create_entity("object", "X1", objectKind="a:thing")
            with caplog.at_level(logging.WARNING):
                sdk.create_event("done", "thingDone", object="X1")
            assert _object_status(sdk, "X1") == "a-done"
            assert not any("status_fold_unapplied" in r.getMessage()
                           for r in caplog.records), [
                r.getMessage() for r in caplog.records]
        finally:
            sdk.close()

    def test_a_lone_declared_fold_miss_is_still_reported(
            self, monkeypatch, caplog):
        """Parity control for the suppression: exactly one declared target
        and no Object of its kind must still report."""
        import logging

        from tortoise.projection import entities as ents

        idx = build_status_fold_index({
            "a": self._Pack(["thing"], {"thing": {
                "statuses": ["a-done"],
                "folds": [_decl("thingDone", "a-done")]}}),
        })
        monkeypatch.setattr(ents, "_status_fold_index", lambda: idx)
        sdk = _fresh_sdk()
        try:
            sdk.create_entity("object", "Y1", objectKind="dev:issue")
            with caplog.at_level(logging.WARNING):
                sdk.create_event("done", "thingDone", object="Y1")
            assert any("status_fold_unapplied" in r.getMessage()
                       for r in caplog.records)
        finally:
            sdk.close()


class TestBareKindCollisionAcrossPacks:
    """#2729 review P2: the namespaced dispatch key cannot disambiguate a
    BARE-spelled ``objectKind`` when two packs declare a lifecycle for the
    same bare kind name — the index registers the bare form for BOTH, so the
    last declaring pack wins. The residual is *detected and recorded*, never
    silently claimed away: dispatching on ``ns:kind`` prevents collision only
    for an Object whose stored ``objectKind`` carries the namespace."""

    class _Pack:
        def __init__(self, object_kinds, kind_defs):
            self.object_kinds = object_kinds
            self.kind_defs = kind_defs

    def _packs(self):
        return {
            "a": self._Pack(["thing"], {"thing": {
                "statuses": ["a-done"],
                "folds": [_decl("thingDone", "a-done")]}}),
            "b": self._Pack(["thing"], {"thing": {
                "statuses": ["b-done"],
                "folds": [_decl("thingDone", "b-done")]}}),
        }

    def test_collision_is_reported(self):
        assert bare_lifecycle_kind_collisions(self._packs()) == {
            "thing": ["a", "b"]}

    def test_single_declaring_pack_is_not_a_collision(self):
        packs = self._packs()
        del packs["b"]
        assert bare_lifecycle_kind_collisions(packs) == {}

    def test_distinct_bare_kind_names_are_not_a_collision(self):
        """Parity control: two packs declaring DIFFERENT bare kind names
        never collide, even for the same event."""
        packs = self._packs()
        packs["b"] = self._Pack(["widget"], {"widget": {
            "statuses": ["b-done"],
            "folds": [_decl("thingDone", "b-done")]}})
        assert bare_lifecycle_kind_collisions(packs) == {}

    def test_bare_object_folds_with_the_last_declaring_pack(self, monkeypatch):
        """The documented residual, pinned: a bare ``objectKind`` matches both
        packs and the last one (catalog order) wins."""
        from tortoise.projection import entities as ents

        idx = build_status_fold_index(self._packs())
        monkeypatch.setattr(ents, "_status_fold_index", lambda: idx)
        sdk = _fresh_sdk()
        try:
            sdk.create_entity("object", "Z1", objectKind="thing")
            sdk.create_event("done", "thingDone", object="Z1")
            assert _object_status(sdk, "Z1") == "b-done"
        finally:
            sdk.close()

    def test_registry_records_the_collision_on_lifecycle_warnings(self, tmp_path):
        for ns in ("alpha", "beta"):
            d = tmp_path / ns
            d.mkdir()
            (d / "manifest.yaml").write_text(
                f"namespace: {ns}\nname: {ns}\nversion: 0.1.0\ntier: free\n"
                "ontology:\n  extends: core\n  objectKinds: [thing]\n"
                "  eventKinds: [thingDone]\n  kindDefs:\n    thing:\n"
                f"      statuses: [{ns}-done]\n      folds:\n"
                f"        - event: thingDone\n          status: {ns}-done\n")
        reg = PackRegistry(tmp_path)
        assert reg.load_all() == 2, reg.errors
        for ns in ("alpha", "beta"):
            assert any("multiple packs" in m
                       for m in reg.lifecycle_warnings.get(ns, [])), \
                reg.lifecycle_warnings


# ── 9. The hosted (graph-native) route refuses the declaration ───────────


_TENANT_MANIFEST = """namespace: tenantx
name: Tenant X
version: 0.1.0
tier: free
ontology:
  extends: core
  objectKinds: [tranche]
  eventKinds: [disbursement]
  kindDefs:
    tranche:
      statuses: [pending, released]
      folds:
        - event: disbursement
          status: released
"""

_TENANT_MANIFEST_NO_FOLDS = """namespace: tenantx
name: Tenant X
version: 0.1.0
tier: free
ontology:
  extends: core
  objectKinds: [tranche]
  eventKinds: [disbursement]
"""


class TestHostedTenantRouteRefusesFolds:
    def test_tenant_manifest_declaring_folds_is_refused_not_silently_inert(self):
        r = validate_manifest(_TENANT_MANIFEST)
        assert r.ok is False, (
            "a graph-native tenant manifest never enters the fold index, so "
            "accepting `statuses`/`folds` admits a declaration that can never "
            "fire")
        assert any("hosted" in e and "inert" in e for e in r.errors), r.errors

    def test_tenant_manifest_without_folds_still_validates(self):
        """Parity control — the refusal is scoped to the fold declaration."""
        r = validate_manifest(_TENANT_MANIFEST_NO_FOLDS)
        assert r.ok is True, r.errors

    def test_mixed_type_kinddefs_keys_return_a_diagnostic_not_a_crash(self):
        """#2729 review P2: the pre-refusal helper must be total. A YAML map
        with a non-string key made ``sorted`` raise ``TypeError`` BEFORE the
        shared validator ran, replacing the diagnostic with an internal
        exception string on the tenant upload route."""
        manifest = """namespace: tenantx
name: Tenant X
version: 0.1.0
tier: free
ontology:
  extends: core
  objectKinds: [tranche]
  eventKinds: [disbursement]
  kindDefs:
    1:
      statuses: [pending, released]
    tranche:
      statuses: [pending, released]
"""
        r = validate_manifest(manifest)  # must not raise
        assert r.ok is False
        assert r.errors, "a malformed declaration must carry a diagnostic"
