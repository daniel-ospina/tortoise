"""#2350 (E-prec) — per-end validity precision + the 3-state window end.

The additive precision layer on #1538's ``validFrom``/``validTo``:

  * ``tortoise/validity.py`` — the vocabulary + validator + the ONE read
    projection home;
  * the write boundary (``create_point`` / ``update_point`` /
    ``supersede_point`` / ``invalidate_point``);
  * the 3-state end on every window-consuming read path — most importantly
    that an ended-unknown window is NOT read as still-true;
  * rebuild parity (precision survives a JSONL wipe+replay verbatim) and the
    no-re-stamp guarantee for pre-existing windows.

Runnable with:
  TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest \
      tests/test_2350_validity_precision.py -q
"""
from __future__ import annotations

import datetime
import inspect
import os

import pytest

from tortoise.sdk import TortoiseSDK

A_DATE = "2024-06-01"


@pytest.fixture
def lane(tmp_path):
    """(sdk, events_dir) with the journal wired so rebuild parity is testable."""
    events = tmp_path / "events"
    events.mkdir()
    sdk = TortoiseSDK(str(tmp_path / "t.db"),
                      event_log_path=str(events / "events.jsonl"))
    yield sdk, events
    sdk.close()


def _props(sdk: TortoiseSDK, pid: str) -> dict:
    row = sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) RETURN properties(n)",
        params={"id": pid}).result_set
    assert row, f"point {pid} missing"
    return dict(row[0][0])


def _window_props(sdk: TortoiseSDK, pid: str) -> dict:
    p = _props(sdk, pid)
    return {k: p.get(k) for k in
            ("validFrom", "validTo", "validFromPrecision", "validToPrecision")}


# ── the vocabulary + validator (pure — no graph) ─────────────────────────

def test_normalize_precision_accepts_vocabulary_and_normalizes_empty():
    from tortoise.validity import normalize_precision

    for token in ("year", "month", "day", "unknown", "YEAR", " day "):
        assert normalize_precision(token) == token.strip().lower()
    assert normalize_precision(None) is None
    assert normalize_precision("") is None
    assert normalize_precision("   ") is None


def test_normalize_precision_refuses_an_unknown_word_and_non_string():
    from tortoise.validity import normalize_precision

    with pytest.raises(ValueError, match="not a validity precision"):
        normalize_precision("week")
    with pytest.raises(ValueError, match="must be a string"):
        normalize_precision(2024)


def test_effective_precision_legacy_default_is_day():
    from tortoise.validity import effective_precision

    # A bare-ISO bound (no declared precision) reads as DAY — no migration.
    assert effective_precision("2024-01-01", None) == "day"
    # A declared token wins over the legacy default.
    assert effective_precision("2024-01-01", "year") == "year"
    # Absent bound + no declaration ⇒ no precision.
    assert effective_precision(None, None) is None
    # The closed-unknown marker on an absent bound.
    assert effective_precision(None, "unknown") == "unknown"


def test_end_state_is_three_valued():
    from tortoise.validity import END_KNOWN, END_OPEN, END_UNKNOWN, end_state

    assert end_state(None, None) == END_OPEN
    assert end_state(None, "") == END_OPEN
    assert end_state("2024-01-01", None) == END_KNOWN
    assert end_state("2024-01-01", "month") == END_KNOWN
    assert end_state(None, "unknown") == END_UNKNOWN


def test_validate_refuses_unknown_start():
    from tortoise.validity import validate_validity_precision

    with pytest.raises(ValueError, match="END only"):
        validate_validity_precision("2024-01-01", None, "unknown", None)


def test_validate_refuses_unknown_end_alongside_a_fabricated_date():
    from tortoise.validity import validate_validity_precision

    with pytest.raises(ValueError, match="contradiction"):
        validate_validity_precision(None, "2024-01-01", None, "unknown")


def test_validate_refuses_a_precision_without_its_bound():
    from tortoise.validity import validate_validity_precision

    with pytest.raises(ValueError, match="requires a validTo"):
        validate_validity_precision(None, None, None, "year")
    with pytest.raises(ValueError, match="requires a validFrom"):
        validate_validity_precision(None, None, "month", None)


def test_window_read_fields_legacy_shape_is_the_pre_2350_reading():
    from tortoise.validity import window_read_fields

    legacy = window_read_fields("2024-01-01", "2024-06-01", None, None)
    assert legacy == {"valid_from_precision": "day",
                      "valid_to_precision": "day", "end_state": "known"}
    open_ended = window_read_fields("2024-01-01", None, None, None)
    assert open_ended["end_state"] == "open"
    assert open_ended["valid_to_precision"] is None


# ── the E2E the issue names: year precision → open read → unknown end ─────

def test_precision_roundtrip_three_state_e2e(lane):
    """write year-precision open → read → supersede with an unknown end →
    read 3-state (the #2350 E2E)."""
    sdk, _events = lane
    old = sdk.create_point("statement", "old claim",
                           validFrom="2024-01-01",
                           validFromPrecision="year")

    first = sdk.get_historical_knowledge(old["id"], A_DATE)
    vp = first["valid_point"]
    assert vp["valid_from_precision"] == "year"
    assert vp["end_state"] == "open"
    assert "end_state_unknown" not in first

    new = sdk.create_point("statement", "new claim", validFrom="2025-03-01")
    sdk.supersede_point(old["id"], new["id"], valid_to_precision="unknown")

    # The predecessor's end is CLOSED but un-datable: no validTo is fabricated.
    assert _window_props(sdk, old["id"]) == {
        "validFrom": "2024-01-01", "validTo": None,
        "validFromPrecision": "year", "validToPrecision": "unknown"}

    after = sdk.get_historical_knowledge(old["id"], A_DATE)
    assert after["valid_point"]["end_state"] == "unknown"
    assert after["valid_point"]["valid_from_precision"] == "year"
    # The reply says plainly that coverage past the start is not attested.
    assert after["end_state_unknown"] is True
    assert after["indeterminate_end_ids"] == [old["id"]]


def test_legacy_bare_iso_window_reads_as_day_precision_no_migration(lane):
    sdk, _events = lane
    p = sdk.create_point("statement", "legacy window",
                         validFrom="2024-01-01", validTo="2024-12-31")
    assert _window_props(sdk, p["id"]) == {
        "validFrom": "2024-01-01", "validTo": "2024-12-31",
        "validFromPrecision": None, "validToPrecision": None}
    out = sdk.get_historical_knowledge(p["id"], "2024-06-01")
    assert out["valid_point"]["valid_from_precision"] == "day"
    assert out["valid_point"]["valid_to_precision"] == "day"
    assert out["valid_point"]["end_state"] == "known"


def test_open_and_closed_unknown_are_distinguishable_on_the_same_read(lane):
    sdk, _events = lane
    still = sdk.create_point("statement", "still true", validFrom="2024-01-01")
    ended = sdk.create_point("statement", "ended unknown",
                             validFrom="2024-01-01",
                             validToPrecision="unknown")
    open_out = sdk.get_historical_knowledge(still["id"], A_DATE)
    unknown_out = sdk.get_historical_knowledge(ended["id"], A_DATE)
    assert open_out["valid_point"]["end_state"] == "open"
    assert unknown_out["valid_point"]["end_state"] == "unknown"
    assert "end_state_unknown" not in open_out
    assert unknown_out["end_state_unknown"] is True


# ── the write boundary ───────────────────────────────────────────────────

def test_create_point_refuses_inconsistent_precision(lane):
    sdk, _events = lane
    with pytest.raises(ValueError, match="not a validity precision"):
        sdk.create_point("statement", "x", validToPrecision="week")
    with pytest.raises(ValueError, match="END only"):
        sdk.create_point("statement", "x", validFromPrecision="unknown")
    with pytest.raises(ValueError, match="contradiction"):
        sdk.create_point("statement", "x", validTo="2024-01-01",
                         validToPrecision="unknown")
    with pytest.raises(ValueError, match="requires a validTo"):
        sdk.create_point("statement", "x", validToPrecision="year")


def test_update_point_refuses_a_one_sided_move_off_unknown(lane):
    sdk, _events = lane
    p = sdk.create_point("statement", "x", validFrom="2024-01-01",
                         validToPrecision="unknown")
    # A stored end of 'unknown' plus a newly dated validTo is refused until the
    # caller states the new precision — no stale marker beside a real date.
    with pytest.raises(ValueError, match="contradiction"):
        sdk.update_point(p["id"], validTo="2025-01-01")
    sdk.update_point(p["id"], validTo="2025-01-01", validToPrecision="day")
    assert _window_props(sdk, p["id"])["validToPrecision"] == "day"


def test_supersede_inherits_the_successors_start_precision(lane):
    sdk, _events = lane
    old = sdk.create_point("statement", "old", validFrom="2025-01-01")
    new = sdk.create_point("statement", "new", validFrom="2026-01-01",
                           validFromPrecision="month")
    sdk.supersede_point(old["id"], new["id"])
    assert _window_props(sdk, old["id"])["validToPrecision"] == "month"
    assert _window_props(sdk, old["id"])["validTo"] == "2026-01-01"


def test_supersede_does_not_fabricate_precision_on_a_legacy_chain(lane):
    sdk, _events = lane
    old = sdk.create_point("statement", "old", validFrom="2025-01-01")
    new = sdk.create_point("statement", "new", validFrom="2026-01-01")
    sdk.supersede_point(old["id"], new["id"])
    # No declared precision anywhere ⇒ the property stays ABSENT (legacy day
    # default on read), never a stored guess.
    assert _window_props(sdk, old["id"])["validToPrecision"] is None


def test_invalidate_point_accepts_an_unknown_end(lane):
    sdk, _events = lane
    old = sdk.create_point("statement", "old", validFrom="2024-01-01")
    other = sdk.create_point("statement", "other", validFrom="2024-06-01")
    sdk.invalidate_point(old["id"], other["id"], valid_to_precision="unknown")
    props = _window_props(sdk, old["id"])
    assert props["validTo"] is None
    assert props["validToPrecision"] == "unknown"


# ── no re-stamp of pre-existing windows ──────────────────────────────────

def test_supersession_leaves_the_predecessors_own_start_untouched(lane):
    sdk, _events = lane
    old = sdk.create_point("statement", "old", validFrom="2025-01-01",
                           validFromPrecision="year")
    new = sdk.create_point("statement", "new", validFrom="2026-03-01")
    sdk.supersede_point(old["id"], new["id"])
    assert _window_props(sdk, old["id"])["validFrom"] == "2025-01-01"
    assert _window_props(sdk, old["id"])["validFromPrecision"] == "year"


# ── rebuild parity (full-prop preserved) ─────────────────────────────────

def test_precision_survives_a_jsonl_wipe_and_rebuild(lane):
    sdk, events = lane
    old = sdk.create_point("statement", "old", validFrom="2024-01-01",
                           validFromPrecision="year")
    new = sdk.create_point("statement", "new", validFrom="2025-03-01")
    sdk.supersede_point(old["id"], new["id"], valid_to_precision="unknown")
    live = _window_props(sdk, old["id"])

    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
    assert _window_props(sdk, old["id"]) == live
    rebuilt = sdk.get_historical_knowledge(old["id"], A_DATE)
    assert rebuilt["valid_point"]["end_state"] == "unknown"
    assert rebuilt["end_state_unknown"] is True


def test_invalidated_unknown_precision_survives_rebuild(lane):
    sdk, events = lane
    old = sdk.create_point("statement", "old", validFrom="2024-01-01")
    other = sdk.create_point("statement", "other", validFrom="2024-06-01")
    sdk.invalidate_point(old["id"], other["id"], valid_to_precision="unknown")
    live = _window_props(sdk, old["id"])

    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
    assert _window_props(sdk, old["id"]) == live


# ── search / recall annotation ───────────────────────────────────────────

def _scan(sdk, **kw):
    return sdk.tortoise_fts_query(query=None, kind="statement",
                                  entity_type="point", limit=50,
                                  include_terminal=True, **kw)


def test_reader_marker_does_not_relabel_invalidated_unknown_as_still_true(lane):
    """#2350: the reader-facing ``[valid …]`` marker must not render an
    invalidated-un-datable window as still-true, and must keep the
    transaction-time expiry date (``invalidate_point`` stamps ``expiredAt``)."""
    from tortoise.retrieval import _validity_marker

    sdk, _events = lane
    old = sdk.create_point("statement", "old", validFrom="2024-01-01")
    other = sdk.create_point("statement", "other", validFrom="2024-06-01")
    sdk.invalidate_point(old["id"], other["id"], valid_to_precision="unknown")
    hit = next(r for r in _scan(sdk) if r["id"] == old["id"])
    assert hit["end_state"] == "unknown"
    assert hit["valid_to_precision"] == "unknown"
    marker = _validity_marker(hit)
    # the SUPERSEDED BY marker renders first, so match the window half.
    assert "[valid since 2024-01-01; expired " in marker
    assert marker.endswith("; end unknown]")


def test_search_annotation_distinguishes_open_from_unknown(lane):
    sdk, _events = lane
    still = sdk.create_point("statement", "still", validFrom="2024-01-01")
    ended = sdk.create_point("statement", "ended", validFrom="2024-01-01",
                             validToPrecision="unknown")
    hits = {r["id"]: r for r in _scan(sdk)}
    assert hits[still["id"]]["end_state"] == "open"
    assert "valid_to_precision" not in hits[still["id"]]
    assert hits[ended["id"]]["end_state"] == "unknown"
    assert hits[ended["id"]]["valid_to_precision"] == "unknown"


def test_search_annotation_carries_declared_precision(lane):
    sdk, _events = lane
    p = sdk.create_point("statement", "dated", validFrom="2024-01-01",
                         validFromPrecision="year")
    hit = next(r for r in _scan(sdk) if r["id"] == p["id"])
    assert hit["valid_from_precision"] == "year"
    assert hit["end_state"] == "open"


def test_undated_hits_emit_no_precision_keys(lane):
    sdk, _events = lane
    p = sdk.create_point("statement", "timeless")
    hit = next(r for r in _scan(sdk) if r["id"] == p["id"])
    for key in ("valid_from_precision", "valid_to_precision", "end_state"):
        assert key not in hit, (key, hit)


def test_assembly_window_rows_carry_the_precision_block(lane):
    sdk, _events = lane
    from tortoise.assembly import docker_walker_port
    ended = sdk.create_point("statement", "ended", validFrom="2024-01-01",
                             validToPrecision="unknown")
    port = docker_walker_port(sdk)
    today = datetime.date.today()
    rows = port.window_rows(today, today)
    row = next(r for r in rows if r["id"] == ended["id"])
    assert row["end_state"] == "unknown"
    assert row["valid_to_precision"] == "unknown"


def test_falsey_but_present_bound_resolves_identically_on_all_three_read_paths(lane):
    """#2350 review P2: the ONE read-projection home gates on ``is not None``
    (a falsey-but-PRESENT bound — ``validFrom=""`` — is a REAL bound, the
    same predicate ``_covers`` orders with), so search/recall, assembly and
    ``get_historical_knowledge`` must resolve the SAME stored window the same
    way. Pre-fix, ``fetch_point_epistemic_state`` pre-normalized with
    ``or None`` (precision ``None``) and ``SearchResult.to_dict`` dropped the
    block entirely, while the other two surfaces said ``day``/``open`` — the
    exact cross-surface drift the module exists to prevent."""
    sdk, _events = lane
    from tortoise.assembly import docker_walker_port

    p = sdk.create_point("statement", "empty start", validFrom="")
    # non-vacuous: the empty string is STORED (a falsey-but-present bound),
    # not refused and not normalized away at the write boundary.
    assert _props(sdk, p["id"])["validFrom"] == ""

    # 1. get_historical_knowledge (raw bound)
    gk = sdk.get_historical_knowledge(p["id"], A_DATE)
    assert gk["chain"][0]["valid_from_precision"] == "day"
    assert gk["chain"][0]["end_state"] == "open"

    # 2. assembly window rows (raw bound)
    port = docker_walker_port(sdk)
    today = datetime.date.today()
    row = next(r for r in port.window_rows(today, today) if r["id"] == p["id"])
    assert row["valid_from_precision"] == "day"
    assert row["end_state"] == "open"

    # 3. search/recall wire — the outlier pre-fix
    hit = next(r for r in _scan(sdk) if r["id"] == p["id"])
    assert hit["valid_from_precision"] == "day"
    assert hit["end_state"] == "open"


def test_check_consistency_reference_fold_states_the_terminalizer_precision(lane):
    """#2350 review P1: ``check_consistency``'s reference fold must state
    ``validToPrecision`` on every precision-carrying terminalizer.

    The live writers (``invalidate_point`` / ``supersede_point``) SET
    ``n.validToPrecision`` and the graph/replay folds restore it, so a
    reference fold that omits it reports a graph the live path itself wrote
    as diverged — with advice ("replay the journal") that cannot clear it,
    the #5048/#3817 class. Fails on the pre-fix fold, where ``validToPrecision``
    is graph-only and lands in ``fields``."""
    from tortoise.consistency import check_consistency

    sdk, events = lane
    # (a) invalidate with the closed-unknown end
    a = sdk.create_point("statement", "a", validFrom="2024-01-01")
    other = sdk.create_point("statement", "other", validFrom="2024-06-01")
    sdk.invalidate_point(a["id"], other["id"], valid_to_precision="unknown")
    # (b) supersede inheriting a declared precision from the successor's start
    b = sdk.create_point("statement", "b", validFrom="2024-02-01")
    c = sdk.create_point("statement", "c", validFrom="2025-03-01",
                         validFromPrecision="month")
    sdk.supersede_point(b["id"], c["id"])
    # (c) a legacy supersede with NO precision stays symmetric-absent
    d = sdk.create_point("statement", "d", validFrom="2024-03-01")
    e = sdk.create_point("statement", "e", validFrom="2026-01-01")
    sdk.supersede_point(d["id"], e["id"])

    result = check_consistency(str(events / "events.jsonl"), sdk._get_proj())
    assert result["ok"], result.get("divergent_points")


# ── surface discipline (no new MCP tool; params on existing verbs only) ───

def test_no_new_mcp_tool_was_added_for_precision():
    from tortoise.tool_registry import TOOL_REGISTRY

    names = {t.name for t in TOOL_REGISTRY}
    assert not any("precision" in n.lower() for n in names), sorted(names)


def test_precision_is_an_optional_param_on_existing_write_verbs():
    sup = inspect.signature(TortoiseSDK.supersede_point).parameters
    inv = inspect.signature(TortoiseSDK.invalidate_point).parameters
    assert "valid_to_precision" in sup and sup["valid_to_precision"].default is None
    assert "valid_to_precision" in inv and inv["valid_to_precision"].default is None
    # create/update keep their generic props passthrough — no signature change.
    assert any(p.kind is inspect.Parameter.VAR_KEYWORD
               for p in inspect.signature(TortoiseSDK.create_point).parameters.values())
    assert any(p.kind is inspect.Parameter.VAR_KEYWORD
               for p in inspect.signature(TortoiseSDK.update_point).parameters.values())


# ── the ONTOLOGY record: the doc self-identifies as its newest changelog ──

def test_ontology_version_matches_its_newest_changelog():
    """#2350 review P2: the canonical doc must self-identify as the version of
    its NEWEST changelog entry — the convention its own history follows
    (``98e46c089`` bumped title+H1 to v3.27 with Changelog v3.27; ``cd5d7a7aa``
    for v3.26). The #2350 diff added Changelog v3.28 while leaving the
    frontmatter title and H1 at v3.27. Fails on the pre-fix doc."""
    import re
    from pathlib import Path

    doc = (Path(__file__).resolve().parent.parent
           / "docs" / "ONTOLOGY.md").read_text(encoding="utf-8")
    title = re.search(r'^title: "Tortoise — Canonical Ontology v([\d.]+)"',
                      doc, re.M)
    h1 = re.search(r'^# Tortoise — Canonical Ontology v([\d.]+)$', doc, re.M)
    assert title, "ONTOLOGY frontmatter title is missing"
    assert h1, "ONTOLOGY H1 is missing"
    changelog = re.findall(r"Changelog v([\d.]+) \(", doc)
    assert changelog, "ONTOLOGY carries no changelog entries"
    newest = max(changelog, key=lambda v: tuple(int(x) for x in v.split(".")))
    assert title.group(1) == newest, (title.group(1), newest)
    assert h1.group(1) == newest, (h1.group(1), newest)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([os.path.abspath(__file__), "-q"]))
