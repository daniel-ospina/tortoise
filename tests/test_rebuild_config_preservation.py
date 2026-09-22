"""#2814 — authoritative-configuration durability across `rebuild_all`.

The wipe in `rebuild_all` (`tortoise/projection/__init__.py`) is an
unconditional `MATCH (n) DETACH DELETE n`, and only the journal is replayed.
`:PackInstall`, `:PackManifest` and the keyed `:Meta` markers
(`calibration_milestone`, `config_reset`) are graph-resident, ride **no**
journal record, and are not re-derivable — so before this change a rebuild
silently reverted a configured graph to defaults (masked by the self-healing
starter-pack read path, which makes the graph look freshly provisioned rather
than empty).

This file pins the fix: a *declared* config registry captured into the durable
pre-wipe sidecar before the wipe and restored after replay, with a sticky
third state for "the config did not come back".

Lane: **embedded** (`TORTOISE_TEST_CARVE_OUT=1`) — registered in
`config/ci-surfaces.yml` (`core` + `api` + `slow_files` + `carve_out`),
`tests/_embedded.py::TEST_NO_REDIRECT_STEMS`, and the literal in
`tests/test_markers.py`.
"""
from __future__ import annotations

import ast
import inspect
import json
import logging
import os
from pathlib import Path
from unittest import mock

import pytest

from tortoise.sdk import TortoiseSDK

# ── harness ──────────────────────────────────────────────────────────────────


def _mk_sdk(tmp_path, name="config.db", namespace=None):
    events = tmp_path / "events"
    events.mkdir(parents=True, exist_ok=True)
    sdk = TortoiseSDK(
        db_path=str(tmp_path / name),
        namespace=namespace or f"test_cfg_{os.urandom(4).hex()}",
        event_log_path=str(events / "events.jsonl"),
    )
    return sdk, events


@pytest.fixture
def graph(tmp_path):
    """(events_dir, sdk) — embedded, isolated DB, journal wired."""
    sdk, events = _mk_sdk(tmp_path)
    yield events, sdk
    sdk.close()


def _write_journal(events_dir, records: list[dict] | None = None) -> None:
    (events_dir / "events.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in records or []))


def _g(sdk):
    return sdk._get_proj().g


def _write_install(sdk, namespace: str, **props) -> None:
    """A raw `:PackInstall` write — the shape the registry must preserve."""
    _g(sdk).query(
        f"MERGE (p:{_PACK_INSTALL_LABEL} {{namespace:$ns}}) SET p += $props",
        params={"ns": namespace, "props": props})


def _write_manifest(sdk, namespace: str, **props) -> None:
    _g(sdk).query(
        f"MERGE (m:{_PACK_MANIFEST_LABEL} {{namespace:$ns}}) SET m += $props",
        params={"ns": namespace, "props": props})


def _read_node(sdk, label: str, identity_prop: str, value) -> dict | None:
    rows = _g(sdk).query(
        f"MATCH (n:{label} {{{identity_prop}:$v}}) RETURN properties(n)",
        params={"v": value}).result_set
    if not rows:
        return None
    props = rows[0][0]
    return props if isinstance(props, dict) and props else None


def _read_install(sdk, namespace: str) -> dict | None:
    return _read_node(sdk, _PACK_INSTALL_LABEL, "namespace", namespace)


def _read_manifest(sdk, namespace: str) -> dict | None:
    return _read_node(sdk, _PACK_MANIFEST_LABEL, "namespace", namespace)


def _config_state(sdk) -> set:
    """The live `(label, identity)` set, read through the registry query."""
    from tortoise.projection import _capture_config_snapshot, _config_key
    return {_config_key(e) for e in _capture_config_snapshot(_g(sdk))}


def _sidecar_payload(**overrides) -> dict:
    """A valid v2 sidecar payload; `overrides` replaces whole sections."""
    from tortoise.projection import _PREWIPE_SNAPSHOT_VERSION
    payload = {
        "version": _PREWIPE_SNAPSHOT_VERSION,
        "created_at": "2026-01-01T00:00:00Z",
        "synthetic_events": [],
        "batch_snapshot": [],
        "batch_point_links": [],
        "session_snapshot": [],
        "session_point_links": [],
        "config_snapshot": [],
    }
    payload.update(overrides)
    return payload


def _plant(path, payload: dict) -> None:
    from tortoise.projection import _write_prewipe_snapshot
    _write_prewipe_snapshot(str(path), payload)


def _sidecar_path(events_dir) -> str:
    from tortoise.projection import prewipe_snapshot_path
    return prewipe_snapshot_path(str(events_dir))


def _capture_writes(monkeypatch):
    """Spy on `_write_prewipe_snapshot`; returns the list of payloads."""
    from tortoise import projection as pr
    written: list[dict] = []
    real = pr._write_prewipe_snapshot

    def spy(path, payload):
        written.append(payload)
        return real(path, payload)

    monkeypatch.setattr(pr, "_write_prewipe_snapshot", spy)
    return written


def _inject_query_failure(sdk, predicate):
    """Make the INNER graph `query` raise when `predicate(cypher)` is true.

    Patches the inner handle (`_GuardedGraph` is `__slots__`) so the guard
    stays in the call chain. Returns the list of injected cypher strings —
    callers MUST assert it is non-empty, or the injection silently stopped
    firing and the test would pass without exercising anything.
    """
    proj = sdk._get_proj()
    inner = proj.g._g
    real_query = inner.query
    injected: list[str] = []

    def _inject(cypher, params=None, timeout=None):
        if predicate(cypher):
            injected.append(cypher)
            raise RuntimeError("injected engine rejection")
        return real_query(cypher, params=params, timeout=timeout)

    return mock.patch.object(inner, "query", _inject), injected


_PACK_INSTALL_LABEL = None
_PACK_MANIFEST_LABEL = None


def setup_module(module):
    global _PACK_INSTALL_LABEL, _PACK_MANIFEST_LABEL
    from tortoise.pack_manifest_store import PACK_MANIFEST_LABEL
    from tortoise.pack_state import PACK_INSTALL_LABEL
    _PACK_INSTALL_LABEL = PACK_INSTALL_LABEL
    _PACK_MANIFEST_LABEL = PACK_MANIFEST_LABEL


# ── Task 1: registry, validators, cycle-safe accessor ───────────────────────


def _string_constants(module) -> list[str]:
    """Every non-docstring str literal in a module (the Cypher candidates).

    Docstrings are excluded explicitly: they *talk* about the labels, and a
    comment about a label is not a Cypher site.
    """
    tree = ast.parse(Path(inspect.getsourcefile(module)).read_text())
    docstrings = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", [])
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            docstrings.add(id(body[0].value))
    return [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and id(n) not in docstrings]


def test_config_registry_covers_domain_writers(tmp_path):
    """The declared registry, and the labels the writers actually emit.

    NON-TAUTOLOGICAL by construction: a constant-vs-constant comparison reads
    the same constant twice and cannot fail, so the liveness half is a
    STATIC check that the writer modules no longer carry a bare
    `:PackInstall` label literal in a string constant. `PACK_INSTALL_LABEL`
    was DEAD before this change (defined, zero uses) — every writer/reader
    re-typed the label — so without the binding a rename would silently
    de-enrol the issue's headline class with this test still green.
    """
    from tortoise import pack_manifest_store, pack_state
    from tortoise.pack_manifest_store import PACK_MANIFEST_LABEL
    from tortoise.pack_state import PACK_INSTALL_LABEL
    from tortoise.projection import _CONFIG_RESET_KEY, _config_classes
    from tortoise.sdk import TortoiseSDK

    classes = {c.label: c for c in _config_classes()}
    assert set(classes) == {PACK_INSTALL_LABEL, PACK_MANIFEST_LABEL, "Meta"}

    meta = classes["Meta"]
    assert meta.identity_prop == "key"
    assert meta.keys == {TortoiseSDK._CALIBRATION_MARKER_KEY,
                         _CONFIG_RESET_KEY}
    # Derived markers must NOT be enrolled: they are re-created on open, so
    # preserving them would restore a stale index as if it were config.
    assert "point_fts_v2" not in meta.keys
    assert "event_fts_v2" not in meta.keys

    for module in (pack_state, pack_manifest_store):
        offenders = [s for s in _string_constants(module)
                     if ":PackInstall" in s]
        assert not offenders, (
            f"{module.__name__} still carries a bare `:PackInstall` label "
            f"literal in a string constant {offenders!r} — the registry reads "
            f"PACK_INSTALL_LABEL, so renaming the constant (or the literal) "
            f"would silently de-enrol the class")

    # The real writer must agree with the registry, on a scratch graph.
    sdk, _events = _mk_sdk(tmp_path)
    try:
        _write_install(sdk, "reg-probe", version="9.9.9", status="active")
        assert (_PACK_INSTALL_LABEL, "reg-probe") in _config_state(sdk)
    finally:
        sdk.close()


def test_config_registry_identifiers_are_safe_cypher_identifiers(monkeypatch):
    """`_assert_config_registry_safe()` refuses an unsafe declaration.

    Labels and identity-property names are interpolated into Cypher, so each
    must be a safe identifier; an empty `:Meta` key set would turn the capture
    into a LABEL-WIDE read (sweeping the derived FTS markers in); and a
    section without an entry check would KeyError inside pre-wipe validation.
    """
    from tortoise import projection as pr

    # The real declaration passes.
    pr._assert_config_registry_safe()

    good_map = dict(pr._CONFIG_CLASS_BY_LABEL)
    unsafe = [
        pr._ConfigClass("Pack Install", "namespace"),
        pr._ConfigClass("PackInstall", "namespace) DETACH DELETE (n"),
        pr._ConfigClass("Meta", "key", frozenset()),          # empty key set
        pr._ConfigClass("Meta", "key", frozenset({""})),      # empty key
    ]
    for bad in unsafe:
        monkeypatch.setattr(pr, "_CONFIG_CLASS_BY_LABEL", {bad.label: bad})
        with pytest.raises(RuntimeError):
            pr._assert_config_registry_safe()
    monkeypatch.setattr(pr, "_CONFIG_CLASS_BY_LABEL", good_map)

    assert "config_snapshot" in pr._SNAPSHOT_SECTIONS
    good_checks = dict(pr._SNAPSHOT_ENTRY_CHECK)
    monkeypatch.setattr(
        pr, "_SNAPSHOT_ENTRY_CHECK",
        {k: v for k, v in good_checks.items() if k != "config_snapshot"})
    with pytest.raises(RuntimeError):
        pr._assert_config_registry_safe()
    monkeypatch.setattr(pr, "_SNAPSHOT_ENTRY_CHECK", good_checks)
    pr._assert_config_registry_safe()

    # And a section absent from the tuple is refused too.
    monkeypatch.setattr(
        pr, "_SNAPSHOT_SECTIONS",
        tuple(s for s in pr._SNAPSHOT_SECTIONS if s != "config_snapshot"))
    with pytest.raises(RuntimeError):
        pr._assert_config_registry_safe()


def test_config_snapshot_roundtrips_loader_and_union(monkeypatch, tmp_path):
    """No-DB contracts: validator, `_config_key`, the union, and retirement.

    Includes the cross-label same-identity collision
    (`PackInstall{namespace:'x'}` vs `Meta{key:'x'}`), which must stay TWO
    entries — an identity-only key would merge them, keeping the left label
    and writing a foreign property onto the pack node.
    """
    from tortoise.projection import (
        _CONFIG_CLASS_BY_LABEL,
        _SNAPSHOT_SECTIONS,
        _clear_prewipe_snapshot,
        _config_key,
        _union_prewipe_snapshot,
        _validate_config_entry,
        _validate_prewipe_snapshot,
    )

    good = {"label": "PackInstall",
            "props": {"namespace": "x", "version": "0.9.0",
                      "tags": ["a", "b"], "count": 3}}
    assert _validate_config_entry(good) is None

    # Cross-label, same identity value — two distinct keys.
    install = {"label": _PACK_INSTALL_LABEL, "props": {"namespace": "x"}}
    marker = {"label": "Meta",
              "props": {"key": "x", "reason": "restore_incomplete"}}
    assert _config_key(install) != _config_key(marker)
    assert _config_key(install) == (_PACK_INSTALL_LABEL, "x")
    assert _config_key(marker) == ("Meta", "x")

    # The union is PER-KEY on `_config_key`: a collision keeps the leftover
    # verbatim; a fresh-only key is appended; the cross-label pair survives.
    leftover = {"config_snapshot": [
        {"label": _PACK_INSTALL_LABEL,
         "props": {"namespace": "x", "version": "0.9.0", "status": "removed"}},
        marker,
    ]}
    fresh = {"config_snapshot": [
        {"label": _PACK_INSTALL_LABEL,
         "props": {"namespace": "x", "version": "0.3.0", "status": "active"}},
        {"label": _PACK_MANIFEST_LABEL, "props": {"namespace": "fresh-only"}},
    ]}
    empty = {k: [] for k in _SNAPSHOT_SECTIONS}
    merged = _union_prewipe_snapshot(leftover, {**empty, **fresh})
    entries = merged["config_snapshot"]
    by_key = {_config_key(e): e for e in entries}
    assert len(entries) == 3
    assert by_key[(_PACK_INSTALL_LABEL, "x")]["props"]["version"] == "0.9.0"
    assert by_key[(_PACK_INSTALL_LABEL, "x")]["props"]["status"] == "removed"
    assert ("Meta", "x") in by_key
    assert (_PACK_MANIFEST_LABEL, "fresh-only") in by_key
    assert _CONFIG_CLASS_BY_LABEL[_PACK_INSTALL_LABEL].identity_prop == "namespace"

    # A fresh-only key is appended, not dropped (a config
    # provisioned after an interrupted wipe would otherwise go into the
    # retry's OWN wipe with nothing to restore it from).
    loss = _union_prewipe_snapshot(
        {"config_snapshot": []}, {**empty, **fresh})
    assert {_config_key(e) for e in loss["config_snapshot"]} == {
        (_PACK_INSTALL_LABEL, "x"), (_PACK_MANIFEST_LABEL, "fresh-only")}

    # An unkeyable entry is kept (the pre-wipe validator is what refuses it).
    junk = _union_prewipe_snapshot(
        {"config_snapshot": ["not-an-object"]}, {**empty, **fresh})
    assert "not-an-object" in junk["config_snapshot"]

    # The retirement payload is DERIVED from the section tuple: every section
    # present and empty, so a retired sidecar can never carry live config.
    written = _capture_writes(monkeypatch)
    _clear_prewipe_snapshot(str(tmp_path / "retired.json"))
    assert written, "retirement wrote nothing"
    retired = written[0]
    assert retired["completed"] is True
    for section in _SNAPSHOT_SECTIONS:
        assert retired.get(section) == [], section

    # The validator runs over every section, including the new one.
    payload = _sidecar_payload(config_snapshot=[good])
    _validate_prewipe_snapshot(payload, str(tmp_path / "x.json"))


def test_config_snapshot_malformed_sidecar_refused_before_wipe(tmp_path):
    """A non-list `config_snapshot` is refused by the loader (pre-wipe)."""
    from tortoise.projection import _load_prewipe_snapshot
    path = tmp_path / "bad.json"
    _plant(path, _sidecar_payload(config_snapshot="not-a-list"))
    with pytest.raises(RuntimeError) as exc:
        _load_prewipe_snapshot(str(path))
    assert "config_snapshot" in str(exc.value)
    assert path.exists(), "the sidecar must be KEPT for repair, not deleted"


def test_planted_sidecar_unknown_label_refused_before_wipe(tmp_path):
    """Adversarial class 2: a Cypher-bearing / undeclared label is refused."""
    from tortoise.projection import _load_prewipe_snapshot
    path = tmp_path / "planted-label.json"
    _plant(path, _sidecar_payload(config_snapshot=[
        {"label": "Evil) DETACH DELETE (n) //",
         "props": {"namespace": "x"}}]))
    with pytest.raises(RuntimeError) as exc:
        _load_prewipe_snapshot(str(path))
    assert "not a declared config class" in str(exc.value)
    assert path.exists()


def test_planted_sidecar_undeclared_meta_key_refused_before_wipe(tmp_path):
    """Adversarial class 2: a key outside the declared `:Meta` set is refused.

    This is what keeps the capture (and the restore) from becoming a
    label-wide `:Meta` read that would sweep the derived FTS markers in.
    """
    from tortoise.projection import _load_prewipe_snapshot
    path = tmp_path / "planted-key.json"
    _plant(path, _sidecar_payload(config_snapshot=[
        {"label": "Meta", "props": {"key": "point_fts_v2"}}]))
    with pytest.raises(RuntimeError) as exc:
        _load_prewipe_snapshot(str(path))
    assert "not one of the declared keys" in str(exc.value)
    assert path.exists()


def test_planted_sidecar_nonprimitive_config_prop_refused_before_wipe(tmp_path):
    """Adversarial class 2: a non-storable value is refused BEFORE the wipe.

    A nested object passes a shape check but dies inside the driver AFTER
    `DETACH DELETE` (`ResponseError: Property values can only be of primitive
    types`) — exactly the post-wipe failure the validator exists to prevent.
    """
    from tortoise.projection import _load_prewipe_snapshot
    path = tmp_path / "planted-prop.json"
    _plant(path, _sidecar_payload(config_snapshot=[
        {"label": _PACK_INSTALL_LABEL,
         "props": {"namespace": "x", "nested": {"a": 1}}}]))
    with pytest.raises(RuntimeError) as exc:
        _load_prewipe_snapshot(str(path))
    assert "not a primitive" in str(exc.value)
    assert path.exists()


def test_config_registry_export_consistency():
    """`preserved ⊆ exported`, and the two facts that must NOT be inferred.

    The reverse pin (export-skipped ⇒ preserved) is deliberately ABSENT:
    export-skip is not a durability classifier — `:GraphEventMeta` is
    export-skipped AND load-bearing (#4653) — so only the direction that holds
    is asserted.
    """
    from tortoise import export, hosted_api
    from tortoise.projection import (
        _CONFIG_RESET_KEY,
        _NO_PROJECTION_FOLD,
        _config_classes,
    )

    preserved = {c.label for c in _config_classes()}
    # The export path reads exactly these two pack labels.
    assert {_PACK_INSTALL_LABEL, _PACK_MANIFEST_LABEL} <= preserved
    assert callable(export.collect_pack_config)

    # The calibration milestone rides NO projection fold, which is *why* the
    # sidecar is its only carrier across a rebuild.
    assert "CalibrationRecorded" in _NO_PROJECTION_FOLD

    # Export-skip ≠ durability (the domain comment in plan §2): `:GraphEventMeta`
    # is skipped by the export AND load-bearing, so membership there can never
    # be used to decide preservation.
    assert "GraphEventMeta" in hosted_api._EXPORT_SKIP_LABELS
    assert "GraphEventMeta" not in preserved
    assert _CONFIG_RESET_KEY == "config_reset"


# ── Task 2: version bump, write-payload completeness ────────────────────────


def test_prewipe_snapshot_version_bumped_and_stamped(monkeypatch, graph):
    """The version is bumped AND the payload the caller writes carries it.

    Nothing else in the suite pins the constant (`rg -n
    '_PREWIPE_SNAPSHOT_VERSION' tests/` → no matches on main), so without this
    assertion leaving it at 1 keeps every other named test green while an old
    binary still accepts the fresh sidecar and ignorantly wipes — the exact
    fail-open that decision (d) exists to remove.
    """
    from tortoise import projection as pr

    assert pr._PREWIPE_SNAPSHOT_VERSION != 1
    assert 1 in pr._PREWIPE_SNAPSHOT_READABLE_VERSIONS
    assert pr._PREWIPE_SNAPSHOT_VERSION in pr._PREWIPE_SNAPSHOT_READABLE_VERSIONS

    events, sdk = graph
    _write_journal(events, [])
    _write_install(sdk, "stamped", version="1.0.0")
    written = _capture_writes(monkeypatch)
    sdk._get_proj().rebuild_all(str(events))
    assert written, "no sidecar payload was written"
    assert written[0]["version"] == pr._PREWIPE_SNAPSHOT_VERSION


def test_prewipe_snapshot_v1_readable_and_unknown_version_refused(tmp_path):
    """C6: a v1 rescue file stays loadable; an unknown version is refused."""
    from tortoise.projection import _load_prewipe_snapshot
    v1 = tmp_path / "v1.json"
    _plant(v1, _sidecar_payload(version=1,
                                batch_snapshot=[{"id": "b1"}]))
    loaded = _load_prewipe_snapshot(str(v1))
    assert loaded is not None and loaded["version"] == 1

    # No version at all is the pre-#2943 shape and is accepted too.
    noversion = tmp_path / "noversion.json"
    payload = _sidecar_payload(batch_snapshot=[{"id": "b1"}])
    payload.pop("version")
    _plant(noversion, payload)
    assert _load_prewipe_snapshot(str(noversion)) is not None

    unknown = tmp_path / "v99.json"
    _plant(unknown, _sidecar_payload(version=99,
                                     batch_snapshot=[{"id": "b1"}]))
    with pytest.raises(RuntimeError) as exc:
        _load_prewipe_snapshot(str(unknown))
    assert "unsupported version" in str(exc.value)


def test_prewipe_snapshot_oversized_refused_before_wipe(monkeypatch, tmp_path):
    """Both 64 MB legs, with the cap narrowed so the test stays cheap.

    (a) a planted over-cap sidecar is refused by the LOADER, and
    (b) an over-cap CAPTURED payload is refused by the WRITER before the wipe
    (graph untouched, no sidecar written) — the writer's serialize-then-refuse
    order is what guarantees the loader can always read what the writer wrote.
    """
    from tortoise import projection as pr

    monkeypatch.setattr(pr, "_PREWIPE_SNAPSHOT_MAX_BYTES", 200)

    planted = tmp_path / "huge.json"
    # Written RAW (not through `_write_prewipe_snapshot`, which refuses to
    # produce such a file — that is leg (b), below). A planted file does not
    # come from our writer, so the loader's own cap is what must refuse it.
    planted.write_text(json.dumps(_sidecar_payload(
        batch_snapshot=[{"id": "b" * 400}])))
    with pytest.raises(RuntimeError) as exc:
        pr._load_prewipe_snapshot(str(planted))
    assert "absurdly large" in str(exc.value)

    sdk, events = _mk_sdk(tmp_path / "writer")
    try:
        _write_journal(events, [])
        _write_install(sdk, "big", version="1.0.0", blob="x" * 400)
        with pytest.raises(RuntimeError) as exc:
            sdk._get_proj().rebuild_all(str(events))
        assert "aborted BEFORE the graph wipe" in str(exc.value)
        # Pre-wipe: the config is still there and NO sidecar was written.
        assert _read_install(sdk, "big") is not None
        assert not os.path.exists(_sidecar_path(events))
    finally:
        sdk.close()


def test_rebuild_all_write_payload_covers_every_snapshot_section(
        monkeypatch, graph):
    """The write payload's completeness, by VALUE derivation.

    `rebuild_all` iterates `_SNAPSHOT_SECTIONS` and pulls each section's value
    from the merged dict, so a section dropped from the union's return literal
    raises at capture time — PRE-wipe — instead of being written as `[]`
    (which the loader reads identically to an absent key). A key-presence-only
    assertion would be satisfied by a `{k: []}` spread and could not fail, so
    the config section is asserted NON-EMPTY and its content is round-tripped.
    """
    from tortoise.projection import (
        _SNAPSHOT_SECTIONS,
        _load_prewipe_snapshot,
    )

    events, sdk = graph
    _write_journal(events, [])
    _write_install(sdk, "payload", version="2.5.0", status="active")
    _write_manifest(sdk, "payload", name="p", status="active", yaml="x: 1")

    incoming = _capture_writes(monkeypatch)
    # Keep the sidecar on disk so the round-trip can be read back: replace the
    # retirement with a no-op AFTER the write.
    sdk._get_proj().rebuild_all(str(events))
    assert incoming, "no write payload captured"
    payload = incoming[0]
    for section in _SNAPSHOT_SECTIONS:
        assert section in payload, f"write payload is missing {section}"
    assert payload["config_snapshot"], (
        "the config section must be NON-EMPTY — a key-presence assertion "
        "would pass on the {k: []} spread and cannot falsify an omission")
    assert (_PACK_INSTALL_LABEL, "payload") in {
        (e["label"], e["props"]["namespace"]) for e in payload["config_snapshot"]}

    # Round-trips through the loader verbatim.
    _plant(Path(_sidecar_path(events)), payload)
    assert _load_prewipe_snapshot(_sidecar_path(events)) == payload


def test_snapshot_pending_is_derived_from_section_values(graph):
    """A CONFIG-ONLY graph still writes a sidecar.

    `snapshot_pending` is a fourth hand-maintained membership list; an
    omission there is worse than a `[]` section — a config-only graph would
    take the `elif` branch, write NO sidecar at all, and lose the config
    silently. Derived as `any(merged[k] for k in _SNAPSHOT_SECTIONS)`, it
    folds the config section in automatically.
    """
    events, sdk = graph
    _write_journal(events, [])
    _write_install(sdk, "only-config", version="1.2.3")
    assert _read_install(sdk, "only-config") is not None
    sdk._get_proj().rebuild_all(str(events))
    # The config survived AND the graph has no Points (nothing to replay).
    assert _read_install(sdk, "only-config") is not None


# ── Task 4/5: capture, restore, and the survival proofs ─────────────────────


def test_rebuild_all_preserves_pack_install_and_manifest(graph):
    """Config survival across the real wipe, with byte-identical property maps.

    Includes a `status='removed'` install and a custom manifest with
    `yaml`/`sha256` — the shapes the #1936 export path reads — plus a
    synthetic selection-marker property so an omitted field is visible.
    """
    events, sdk = graph
    _write_journal(events, [])
    _write_install(sdk, "dev", version="0.9.0", status="removed",
                   source="custom", installed_at="2026-01-01T00:00:00Z",
                   selection_marker="agent-ops")
    _write_install(sdk, "other", version="1.1.0", status="active",
                   source="starter", installed_at="2026-02-02T00:00:00Z")
    _write_manifest(sdk, "custom-pack", name="Custom", version="2.0.0",
                    yaml="name: custom\n", sha256="deadbeef",
                    status="active", installed_at="2026-03-03T00:00:00Z")
    before = {k: _read_node(sdk, _PACK_INSTALL_LABEL, "namespace", k)
              for k in ("dev", "other")}
    before_manifest = _read_manifest(sdk, "custom-pack")

    sdk._get_proj().rebuild_all(str(events))

    for ns in ("dev", "other"):
        assert _read_install(sdk, ns) == before[ns], (
            f"PackInstall {ns} did not survive the wipe byte-identically")
    assert _read_manifest(sdk, "custom-pack") == before_manifest
    assert _read_install(sdk, "dev")["status"] == "removed"
    assert _read_install(sdk, "dev")["selection_marker"] == "agent-ops"


def test_rebuild_all_preserves_calibration_milestone(graph):
    """The Gate-B marker survives with all its props.

    `CalibrationRecorded` is in `_NO_PROJECTION_FOLD`, so the journal replay
    cannot rebuild this node — the sidecar is its only carrier.
    """
    from tortoise.sdk import TortoiseSDK

    events, sdk = graph
    _write_journal(events, [])
    key = TortoiseSDK._CALIBRATION_MARKER_KEY
    _g(sdk).query(
        "MERGE (m:Meta {key:$key}) SET m += $props",
        params={"key": key, "props": {
            "precision": 0.91, "sample_size": 12,
            "mean_grounding_delta": 0.4,
            "recordedAt": "2026-04-04T00:00:00Z"}})
    before = _read_node(sdk, "Meta", "key", key)

    sdk._get_proj().rebuild_all(str(events))

    assert _read_node(sdk, "Meta", "key", key) == before


def test_rebuild_all_preserves_config_reset_marker(graph):
    """An existing marker survives a rebuild with `count` NOT clobbered.

    The marker is enrolled in the same preservation set as the config it
    describes (C5), so it needs no rebuild-specific handling and no reset.
    """
    from tortoise.projection import read_config_reset, set_config_reset_marker

    events, sdk = graph
    _write_journal(events, [])
    written = set_config_reset_marker(_g(sdk), "restore_incomplete")
    assert written["count"] == 1

    sdk._get_proj().rebuild_all(str(events))

    after = read_config_reset(_g(sdk))
    assert after is not None
    assert after["reason"] == "restore_incomplete"
    assert after["count"] == 1, "a rebuild must not increment/reset the count"
    assert after["at"] == written["at"]


def test_config_reset_marker_is_sticky_across_rebuilds(graph):
    """Sticky across rebuilds, and monotonic on ONE node when re-set."""
    from tortoise.projection import read_config_reset, set_config_reset_marker

    events, sdk = graph
    _write_journal(events, [])
    first = set_config_reset_marker(_g(sdk), "restore_incomplete")
    sdk._get_proj().rebuild_all(str(events))
    sdk._get_proj().rebuild_all(str(events))
    still = read_config_reset(_g(sdk))
    assert still is not None and still["count"] == 1

    second = set_config_reset_marker(_g(sdk), "restore_incomplete")
    assert second["count"] == 2, "re-setting must advance the count"
    assert second["at"] == first["at"], "`at` is the FIRST-set time"
    assert second["last_at"] >= first["last_at"]
    # One node, not an accumulation of them.
    rows = _g(sdk).query(
        "MATCH (n:Meta {key:$key}) RETURN count(n)",
        params={"key": "config_reset"}).result_set
    assert rows[0][0] == 1

    from tortoise.projection import clear_config_reset
    assert clear_config_reset(_g(sdk)) is True
    assert read_config_reset(_g(sdk)) is None
    assert clear_config_reset(_g(sdk)) is False


def test_rebuild_all_wipe_statement_is_byte_identical_and_classified_bulk(
        monkeypatch, graph):
    """THE OWNER'S EXPLICIT PIN (C2).

    The wipe must stay byte-identical `MATCH (n) DETACH DELETE n` so the P0
    production guard keeps seeing a bulk wipe; a filtered DELETE would
    disengage it. Config survives by snapshot+restore AROUND the wipe.
    """
    from tortoise import projection as pr

    events, sdk = graph
    _write_journal(events, [])
    _write_install(sdk, "wipe-pin", version="1.0.0")

    seen: list[str] = []
    real = pr._GuardedGraph.query

    def spy(self, cypher, params=None, timeout=None):
        seen.append(cypher)
        return real(self, cypher, params=params, timeout=timeout)

    monkeypatch.setattr(pr._GuardedGraph, "query", spy)
    sdk._get_proj().rebuild_all(str(events))

    deletes = [c for c in seen if "DETACH DELETE" in c]
    assert deletes == ["MATCH (n) DETACH DELETE n"], deletes
    assert pr._is_bulk_wipe(deletes[0]) is True
    assert _read_install(sdk, "wipe-pin") is not None


def test_rebuild_all_sidecar_recovery_restores_config(graph):
    """The sidecar-RECOVERY path (crash after the wipe, before the replay).

    The live graph is already empty, so the leftover sidecar is the only
    record of the config — and it is reassigned from `merged` before the
    restore leg reads it.
    """
    from tortoise.projection import _load_prewipe_snapshot

    events, sdk = graph
    _write_journal(events, [])
    path = _sidecar_path(events)
    _plant(Path(path), _sidecar_payload(config_snapshot=[
        {"label": _PACK_INSTALL_LABEL,
         "props": {"namespace": "dev", "version": "0.9.0",
                   "status": "removed", "source": "custom"}},
        {"label": _PACK_MANIFEST_LABEL,
         "props": {"namespace": "custom", "yaml": "name: c\n",
                   "sha256": "abc", "status": "active"}},
    ]))
    assert _read_install(sdk, "dev") is None

    sdk._get_proj().rebuild_all(str(events))

    assert _read_install(sdk, "dev")["version"] == "0.9.0"
    assert _read_manifest(sdk, "custom")["sha256"] == "abc"
    assert _load_prewipe_snapshot(path) is None, "sidecar must retire"


def test_retired_sidecar_does_not_resurrect_config_deleted_after_rebuild(graph):
    """The site-5 correctness pin: retirement is entry-less, so merging is a no-op."""
    events, sdk = graph
    _write_journal(events, [])
    _write_install(sdk, "dev", version="0.9.0", status="active")

    sdk._get_proj().rebuild_all(str(events))
    assert _read_install(sdk, "dev") is not None

    _g(sdk).query(
        f"MATCH (p:{_PACK_INSTALL_LABEL} {{namespace:$ns}}) DETACH DELETE p",
        params={"ns": "dev"})
    sdk._get_proj().rebuild_all(str(events))

    assert _read_install(sdk, "dev") is None, (
        "a RETIRED sidecar must not resurrect config deleted after the rebuild")


def test_leftover_config_wins_over_self_healed_defaults(graph):
    """Decision (e) rule 1, BOTH directions in one run.

    The self-heal (`get_tenant_packs` → `ensure_tenant_packs`) fires on the
    post-wipe partial graph and writes present values with a fresh
    `installed_at`. Field-merging would let those overwrite a real captured
    value, so a colliding `_config_key` keeps the LEFTOVER verbatim — while a
    fresh-only key is APPENDED rather than dropped into the retry's own wipe.
    """
    from tortoise.pack_state import ensure_tenant_packs

    events, sdk = graph
    _write_journal(events, [])
    _plant(Path(_sidecar_path(events)), _sidecar_payload(config_snapshot=[
        {"label": _PACK_INSTALL_LABEL,
         "props": {"namespace": "dev", "version": "0.9.0",
                   "status": "removed", "source": "custom",
                   "installed_at": "2026-01-01T00:00:00Z"}},
    ]))
    # The self-heal runs on the partial graph: it writes `dev` with STARTER
    # defaults (a present version / status / source / installed_at), so every
    # field of the colliding entry is a candidate for the wrong direction.
    ensure_tenant_packs(sdk)
    assert _read_install(sdk, "dev")["source"] == "starter"
    # ...and provision a key the leftover does NOT have.
    _write_manifest(sdk, "post-wipe-only", name="New", yaml="name: n\n",
                    sha256="feed", status="active")

    sdk._get_proj().rebuild_all(str(events))

    restored = _read_install(sdk, "dev")
    assert restored["version"] == "0.9.0"
    assert restored["status"] == "removed"
    assert restored["source"] == "custom"
    assert restored["installed_at"] == "2026-01-01T00:00:00Z"
    assert _read_manifest(sdk, "post-wipe-only") is not None, (
        "a fresh-only key must be APPENDED, not discarded with the section")


def test_fresh_only_config_key_survives_pending_leftover(graph):
    """The loss direction of the same rule."""
    events, sdk = graph
    _write_journal(events, [])
    _plant(Path(_sidecar_path(events)), _sidecar_payload(config_snapshot=[
        {"label": _PACK_INSTALL_LABEL,
         "props": {"namespace": "leftover", "version": "1.0.0"}}]))
    _write_install(sdk, "fresh-only", version="2.0.0", source="custom")
    _write_manifest(sdk, "fresh-only", name="F", yaml="a: 1\n", sha256="s1")

    sdk._get_proj().rebuild_all(str(events))

    assert _read_install(sdk, "leftover") is not None
    fresh = _read_install(sdk, "fresh-only")
    assert fresh is not None and fresh["version"] == "2.0.0"
    assert _read_manifest(sdk, "fresh-only")["sha256"] == "s1"


def test_pending_sidecar_restores_leftover_config_over_a_post_wipe_delete(graph):
    """The DOCUMENTED direction, pinned so it is a recorded residual.

    Within the interrupted-rebuild window a colliding key is reverted to the
    leftover — resurrection of a config deleted after the wipe. The remedy is
    the operator deleting the PENDING rescue file; this pins the direction so
    it is a known residual rather than an unknown.
    """
    events, sdk = graph
    _write_journal(events, [])
    _plant(Path(_sidecar_path(events)), _sidecar_payload(config_snapshot=[
        {"label": _PACK_INSTALL_LABEL,
         "props": {"namespace": "dev", "version": "0.9.0", "status": "active"}}]))
    # A deliberate post-wipe delete inside the window.
    _write_install(sdk, "dev", version="9.9.9", status="deleted-elsewhere")
    _g(sdk).query(
        f"MATCH (p:{_PACK_INSTALL_LABEL} {{namespace:$ns}}) DETACH DELETE p",
        params={"ns": "dev"})
    assert _read_install(sdk, "dev") is None

    sdk._get_proj().rebuild_all(str(events))

    assert _read_install(sdk, "dev")["version"] == "0.9.0", (
        "documented residual: the pending leftover reverts a post-wipe delete")


def test_config_capture_failure_aborts_before_wipe(graph):
    """Adversarial class 3's capture leg: refuse BEFORE the wipe.

    A corrupt or merely heavy `properties(n)` read can fail while the light
    DELETE succeeds, so proceeding would wipe with no durable record.
    """
    from tortoise import projection as pr

    events, sdk = graph
    _write_journal(events, [])
    _write_install(sdk, "keep-me", version="1.0.0")
    patcher, injected = _inject_query_failure(
        sdk, lambda c: "(n:PackInstall)" in c and "properties(n)" in c)
    with patcher, pytest.raises(RuntimeError) as exc:
        sdk._get_proj().rebuild_all(str(events))
    assert injected, "the capture failure was never injected"
    assert "aborted BEFORE the graph wipe" in str(exc.value)
    assert _read_install(sdk, "keep-me") is not None, "the graph was touched"
    assert not os.path.exists(_sidecar_path(events))
    assert pr is not None


# ── Task 6: post-restore verification, the third state, operator surface ────


def test_post_restore_mismatch_sets_config_reset_and_logs_error(graph, caplog):
    """T1: a restore that silently drops an identity is NOT a silent success."""
    from tortoise.projection import read_config_reset

    events, sdk = graph
    _write_journal(events, [])
    _write_install(sdk, "dropped", version="3.3.3", status="active")
    _write_manifest(sdk, "dropped", name="D", yaml="d: 1\n", sha256="s2")

    patcher, injected = _inject_query_failure(
        sdk, lambda c: c.startswith("MERGE (n:PackInstall"))
    with caplog.at_level(logging.ERROR, logger="tortoise.projection"), patcher:
        result = sdk._get_proj().rebuild_all(str(events))
    assert injected, "the restore failure was never injected"

    marker = read_config_reset(_g(sdk))
    assert marker is not None, "a dropped identity must set the third state"
    assert marker["reason"] == "restore_incomplete"
    assert result["config_reset"] is True
    assert result["config_restored"] < result["config_expected"]
    assert any("post-restore verification FAILED" in r.message
               for r in caplog.records), (
        "T1 must LOG, not only set the marker")
    # The manifest (not injected) still restored — the failure is per-entry.
    assert _read_manifest(sdk, "dropped") is not None


def test_never_configured_vs_wiped_distinguishable(graph):
    """No marker on a never-configured graph; an UNKNOWN-state marker on the
    v1-leftover graph.

    The v1 leftover must carry a real graph-only entry, because an entry-less
    sidecar is reported absent by the loader (it is the retirement shape).
    """
    from tortoise.projection import read_config_reset

    events, sdk = graph
    _write_journal(events, [])
    result = sdk._get_proj().rebuild_all(str(events))
    assert read_config_reset(_g(sdk)) is None
    assert result["config_reset"] is False
    assert result["config_expected"] == 0

    # Now the legacy shape: a v1 rescue file (no config record at all).
    _plant(Path(_sidecar_path(events)), _sidecar_payload(
        version=1, batch_snapshot=[{"id": "b-legacy"}]))
    result2 = sdk._get_proj().rebuild_all(str(events))
    marker = read_config_reset(_g(sdk))
    assert marker is not None
    assert marker["reason"] == "legacy_sidecar_no_config_record"
    assert "reset" not in marker["reason"], (
        "T2 is a STATE-UNKNOWN signal — it must never claim a reset happened")
    assert result2["config_reset"] is True


def test_v1_leftover_stages_the_marker_into_the_written_payload(graph,
                                                              monkeypatch):
    """T2's marker must reach the SIDECAR, not only the restore leg.

    The payload is derived from `merged`, so staging the marker into the local
    `config_snapshot` alone would write an EMPTY config section while the graph
    still ends up marked. A crash between the sidecar write and the replay then
    leaves a v2 file — for which T2's `version < 2` test is false — so the
    retry would restore nothing and report `config_reset=False` on a graph whose
    config state is UNKNOWN: the third state silently lost in exactly the
    window the sidecar exists for.
    """
    from tortoise.projection import _load_prewipe_snapshot, read_config_reset

    events, sdk = graph
    _write_journal(events, [])
    _plant(Path(_sidecar_path(events)), _sidecar_payload(
        version=1, batch_snapshot=[{"id": "b-legacy"}]))

    written = _capture_writes(monkeypatch)
    sdk._get_proj().rebuild_all(str(events))

    assert written, "no sidecar payload was written"
    staged = [e for e in written[0]["config_snapshot"]
              if e["label"] == "Meta"
              and e["props"].get("key") == "config_reset"]
    assert staged, (
        "the T2 marker did not reach the written payload — a crash before the "
        f"replay would lose it (payload config_snapshot={written[0]['config_snapshot']!r})")
    assert staged[0]["props"]["reason"] == "legacy_sidecar_no_config_record"
    assert read_config_reset(_g(sdk)) is not None

    # And the marker survives the sidecar-RECOVERY path: replay exactly the
    # payload that was written, on a graph whose config is gone.
    path = _sidecar_path(events)
    _plant(Path(path), written[0])
    _g(sdk).query("MATCH (n:Meta {key:'config_reset'}) DETACH DELETE n")
    assert read_config_reset(_g(sdk)) is None
    _plant(Path(path), _load_prewipe_snapshot(path) or written[0])
    sdk._get_proj().rebuild_all(str(events))
    recovered = read_config_reset(_g(sdk))
    assert recovered is not None, (
        "a v2 sidecar carrying the staged marker must restore it on recovery")
    assert recovered["reason"] == "legacy_sidecar_no_config_record"


def test_rebuild_all_returns_config_restored_counts(graph):
    """The additive return keys: counts of `(label, identity)` pairs."""
    events, sdk = graph
    _write_journal(events, [])
    _write_install(sdk, "a", version="1.0.0")
    _write_install(sdk, "b", version="1.0.0")
    _write_manifest(sdk, "c", name="C", yaml="c: 1\n")

    result = sdk._get_proj().rebuild_all(str(events))

    assert result["config_expected"] == 3
    assert result["config_restored"] == 3
    assert result["config_reset"] is False
    for key in ("nodes", "edges", "events"):
        assert key in result, "existing return keys must be preserved"


def test_cmd_rebuild_reports_config_state(tmp_path, capsys):
    """The operator surface is WIRED, not asserted.

    Without it a successful rebuild that staged the unknown-state marker would
    print `Done:` and say nothing — the marker written and never surfaced on
    the one path an operator reads.
    """
    import argparse

    from tortoise.__main__ import _cmd_rebuild

    sdk, events = _mk_sdk(tmp_path)
    sdk.close()
    _write_journal(events, [])
    # The CLI opens its own projection from a path, so seed config through a
    # direct projection the same way.
    from tortoise.projection import FalkorProjection
    proj = FalkorProjection(str(tmp_path / "config.db"), skip_health_check=True)
    proj.g.query(f"MERGE (p:{_PACK_INSTALL_LABEL} {{namespace:$ns}}) "
                 "SET p.version = $v, p.status = 'active'",
                 params={"ns": "cli", "v": "1.0.0"})
    proj.close()

    rc = _cmd_rebuild(argparse.Namespace(dir=str(events),
                                        db=str(tmp_path / "config.db")))
    out = capsys.readouterr()
    assert rc in (None, 0)
    assert "Config:" in out.out
    # NOT `or`-guarded: the static format string would satisfy a disjunct, so an
    # `or` here cannot fail and would not pin the counts the operator reads.
    assert "1 of 1" in out.out, out.out
    assert "1 authoritative entr" in out.out, out.out


def test_recover_from_log_refuses_nonempty_graph_with_config(graph):
    """Decision (g): the sticky marker's availability effect, pinned.

    A graph whose data is gone but whose `config_reset` marker remains reports
    `count(n) > 0`, so `recover_from_log` returns "graph already has nodes — no
    rebuild". Safe (it refuses rather than wipes) but real, so recorded here
    rather than discovered later.
    """
    from tortoise.consistency import recover_from_log
    from tortoise.projection import set_config_reset_marker

    events, sdk = graph
    _write_journal(events, [])
    set_config_reset_marker(_g(sdk), "restore_incomplete")
    rows = _g(sdk).query("MATCH (n) RETURN count(n)").result_set
    assert rows[0][0] > 0, "the marker alone must make the graph non-empty"

    result = recover_from_log(str(events), sdk._get_proj())
    assert result["recovered"] is False
    assert "already has nodes" in result["reason"]


# ── Task 7: the declaration's home is pinned to the code registry ───────────


def _doc_block(name: str) -> str:
    """The text between `<!-- config-registry:<name> -->` and the next `:end`."""
    doc = (Path(__file__).resolve().parent.parent
           / "docs" / "durability-posture.md").read_text()
    open_tag = f"<!-- config-registry:{name} -->"
    assert open_tag in doc, f"docs/durability-posture.md has no {open_tag}"
    body = doc.split(open_tag, 1)[1]
    assert "<!-- config-registry:end -->" in body, (
        f"the {open_tag} block is unterminated")
    return body.split("<!-- config-registry:end -->", 1)[0]


def test_config_registry_doc_consistency():
    """Decision (j): the declaration and `_config_classes()` cannot drift.

    Bidirectional for the preserved/non-preserved sets — the existing doc gate
    (`tests/test_durability_posture.py`) is a text-pattern check over the
    phrase "source of truth" and never reads a node class, so nothing else
    would catch a class silently leaving the operator-facing map. The audit
    query is pinned ONE-directionally (every declared class/key appears), so a
    newly enrolled class cannot leave the operator audit under-reporting.
    """
    import re

    from tortoise.projection import (
        _CONFIG_RESET_KEY,
        _config_classes,
    )
    from tortoise.sdk import TortoiseSDK

    classes = list(_config_classes())
    doc_preserved = _doc_block("preserved")
    label_wide = {c.label for c in classes if c.keys is None}
    scoped = {c.label: c.keys for c in classes if c.keys is not None}

    # `:PackInstall` / `:PackManifest` rows — label-wide preserved classes.
    doc_labels = set(re.findall(r"`:(\w+)`", doc_preserved))
    assert doc_labels == label_wide, (
        f"docs/durability-posture.md preserves {sorted(doc_labels)} but the "
        f"registry declares {sorted(label_wide)}")

    # `:Meta{key:'...'}` rows — the declared key set, both directions.
    doc_pair = set(re.findall(r"`:(\w+)\{key:'([^']+)'\}`", doc_preserved))
    doc_meta_keys = {k for label, k in doc_pair if label == "Meta"}
    assert doc_meta_keys == {TortoiseSDK._CALIBRATION_MARKER_KEY,
                             _CONFIG_RESET_KEY}
    for label, keys in scoped.items():
        assert doc_meta_keys == set(keys), (
            f"the doc's `:{label}{{key:…}}` rows disagree with the registry")

    # Not-preserved: the named derived set, and none of it may be enrolled.
    not_preserved = _doc_block("not-preserved")
    named = set(re.findall(r"`:(\w+)", not_preserved))
    named_meta_keys = {k for _l, k in re.findall(
        r"`:(\w+)\{key:'([^']+)'\}`", not_preserved)}
    assert "EpMeta" in named
    assert named_meta_keys == {"point_fts_v2", "event_fts_v2"}
    assert not (named & label_wide), (
        f"{sorted(named & label_wide)} is listed as NOT preserved but the "
        f"registry enrolls it")
    assert not (named_meta_keys & doc_meta_keys)

    # Unenrolled: the known losses with their filed vehicles.
    unenrolled = _doc_block("unenrolled")
    assert "OnboardingState" in unenrolled and "#4641" in unenrolled
    assert "TeamMeta" in unenrolled
    assert "GraphEventMeta" in unenrolled and "#4653" in unenrolled
    for vehicle in ("OnboardingState", "TeamMeta", "GraphEventMeta"):
        assert vehicle not in label_wide

    # The audit query names every declared class and key (one-directional).
    audit = _doc_block("audit-query")
    for label in label_wide:
        assert f":{label}" in audit, f"audit query omits :{label}"
    for key in doc_meta_keys:
        assert key in audit, f"audit query omits Meta key {key}"


def test_v1_leftover_with_self_healed_config_still_reports_unknown(graph):
    """A pre-preservation rescue file makes the state UNKNOWN even when the LIVE
    graph already carries config.

    The self-heal this issue names (`ensure_tenant_packs`) repopulates starter
    `:PackInstall` rows between the old build's wipe and the retry, so gating
    the state-unknown marker on "the fresh capture is empty" would report a
    clean `N of N restored` with NO marker while the real custom configuration
    the old build destroyed stays unknown — a false "restored" for an unknown
    state. The marker must not inflate the counts either.
    """
    from tortoise.projection import read_config_reset

    events, sdk = graph
    _write_journal(events, [])
    _plant(Path(_sidecar_path(events)), _sidecar_payload(
        version=1, batch_snapshot=[{"id": "b-legacy"}]))
    _write_install(sdk, "dev", version="0.3.0", status="active",
                   source="starter")

    result = sdk._get_proj().rebuild_all(str(events))

    marker = read_config_reset(_g(sdk))
    assert marker is not None, (
        "a v1 rescue file means the state is unknown; the presence of a live "
        "starter row must not turn that into a reported clean restore")
    assert marker["reason"] == "legacy_sidecar_no_config_record"
    assert result["config_reset"] is True
    # The live row survives, and the staged marker is NOT counted as captured
    # configuration (it asserts unprovability, it is not config).
    assert _read_install(sdk, "dev")["source"] == "starter"
    assert result["config_expected"] == 1
    assert result["config_restored"] == 1


def test_unreadable_marker_is_not_reported_as_absent(graph, monkeypatch):
    """A marker READ failure must not read as `config_reset: False`.

    `None` from the read currently means both "never set" and "could not be
    read"; the return field and the CLI warning are built from it, so an
    unreadable marker would tell the operator the configuration is fine on the
    one path that cannot check.
    """
    from tortoise import projection as pr

    events, sdk = graph
    _write_journal(events, [])
    _write_install(sdk, "unreadable", version="1.0.0")

    def _boom(_g):
        raise RuntimeError("injected marker read failure")

    monkeypatch.setattr(pr, "read_config_reset", _boom)
    result = sdk._get_proj().rebuild_all(str(events))

    assert result["config_reset"] is True, (
        "an unreadable marker must fail SAFE (report the incident), never "
        "report `config_reset: False`")
    assert result["config_reset_read_failed"] is True


def test_staged_marker_that_fails_to_restore_stays_in_the_verification(graph,
                                                                     monkeypatch):
    """If T2's staged marker does not come back, the state must NOT read as
    `config_reset: False`.

    The marker is correctly excluded from the reported COUNTS (it asserts
    unprovability, it is not captured configuration), but excluding it from the
    post-restore VERIFICATION as well removes the only identity that can notice
    its own restore failing: the graph is then state-UNKNOWN with no marker on
    it, and the operator is told a clean `0 of 0` — "never configured". That is
    the same false "restored" this state exists to prevent, re-entering through
    the marker itself.

    Injection: filtering the marker out of the verification read is what a
    failed restore write looks like to T1 (the restore leg merges from the
    captured entries; the verification re-reads the graph).
    """
    from tortoise import projection as pr
    from tortoise.projection import read_config_reset

    events, sdk = graph
    _write_journal(events, [])
    _plant(Path(_sidecar_path(events)), _sidecar_payload(
        version=1, batch_snapshot=[{"id": "b-legacy"}]))
    g = _g(sdk)
    real_capture = pr._capture_config_snapshot

    def _capture_without_the_marker(graph_):
        return [
            e for e in real_capture(graph_)
            if not (e.get("label") == "Meta"
                    and (e.get("props") or {}).get("key") == "config_reset")
        ]

    monkeypatch.setattr(pr, "_capture_config_snapshot",
                        _capture_without_the_marker)
    result = sdk._get_proj().rebuild_all(str(events))

    assert result["config_reset"] is True, (
        "a staged marker that did not restore must be re-recorded, not read "
        "as `config_reset: False`")
    marker = read_config_reset(g)
    assert marker is not None and marker["reason"] == "restore_incomplete"
    # ... and it still does not inflate the counts the operator reads.
    assert result["config_expected"] == 0
    assert result["config_restored"] == 0
    assert result["config_reset_read_failed"] is False
