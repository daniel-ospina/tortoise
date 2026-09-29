"""Unit tests for the measured-durations-map validator (#5050).

The validator exists to make one distinction checkable: a weight the collector
MEASURED versus one somebody typed. The writer's floor is the only arithmetic
fingerprint available, and the capture date is the only provenance, so these
tests pin those two boundaries plus the polarity of the verdict itself —
"could not look" must never read as "looked and it was fine".

Deliberately NOT re-encoded here: the map's schema, its row set, and the leg/
coverage arithmetic. Those belong to `ci_selection` (which `check` composes),
and a second copy of them in a test would be a second gate.
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import ci_manifest
from tools import ci_selection as cs

# A fixed clock, so `MAX_AGE_DAYS` and the future-skew tolerance are exercised
# against a stated instant rather than against test-run duration.
NOW = dt.datetime(2026, 9, 28, 12, 0, tzinfo=dt.UTC)

_ABSENT = object()


def _manifest(captured_at=_ABSENT, durations: dict | None = None) -> dict:
    """The real manifest, with the capture date held out of it.

    The committed map has no capture date yet (the weekly refresh has not run
    since the bridge landed), so every case here sets the state explicitly
    instead of inheriting whatever the tree happens to carry.
    """
    manifest = dict(cs.load_manifest())
    manifest["durations"] = (dict(manifest["durations"])
                             if durations is None else durations)
    manifest.pop(ci_manifest.CAPTURED_AT_KEY, None)
    if captured_at is not _ABSENT:
        manifest[ci_manifest.CAPTURED_AT_KEY] = captured_at
    return manifest


# ── provenance: the verdict's polarity ───────────────────────────────────


def test_the_committed_map_carries_no_red_defect() -> None:
    """The validator is behaviour-neutral on the tree it lands on.

    `red` is asserted; the map's freshness is NOT. The map is meant to gain a
    `durations_captured_at` the moment a refresh lands, so asserting that its
    freshness is UNKNOWN would fail on the first refresh that worked — the
    automation this change exists to enable — and evaluating its AGE against a
    FIXED test clock would red the moment that stamp is later than the clock.
    The clock here is therefore the map's OWN capture time when it has one, so
    the question stays stable at any date: does the committed map carry a red
    defect other than age?

    The `0.0` rows are the declared unmeasured/pin sentinel. If the floor check
    reddened them it would be redding a documented state, and the refresh (which
    CARRIES those rows forward, since no leg measures them) could never write.
    """
    manifest = cs.load_manifest()
    captured = ci_manifest._parse_captured_at(
        manifest.get(ci_manifest.CAPTURED_AT_KEY))
    red, _ = ci_manifest.check(manifest, captured or NOW)
    assert red == [], red


def test_the_unmeasured_sentinel_stays_representable() -> None:
    """A row no source leg has measured still needs a legal spelling.

    Built on a synthetic row rather than read off the committed map: WHICH rows
    hold `0.0` is data the refresh owns, and pinning a committed key here would
    red the moment a run finally measures it — the same stale-able-literal class
    the 688 count was (#6143).
    """
    manifest = _manifest((NOW - dt.timedelta(days=1)).isoformat())
    manifest["durations"][cs.fast_pool(manifest)[0]] = ci_manifest.UNMEASURED_SENTINEL
    red, _ = ci_manifest.check(manifest, NOW)
    assert red == [], red


def test_an_absent_capture_date_is_unknown_and_never_green() -> None:
    red, unknown = ci_manifest.check(_manifest(), NOW)
    assert red == []
    assert any(ci_manifest.CAPTURED_AT_KEY in reason for reason in unknown), unknown


def test_an_unparseable_capture_date_is_unknown() -> None:
    red, unknown = ci_manifest.check(_manifest("sometime last week"), NOW)
    assert red == []
    assert any("not a parseable timestamp" in reason for reason in unknown), unknown


def test_the_writers_own_stamp_format_is_accepted() -> None:
    """`ci_timing` stamps `datetime.now(utc).strftime("%Y-%m-%dT%H:%M:%SZ")`.

    The `Z` spelling is not the spelling `isoformat()` emits, and a validator
    that only understood the latter would report the one stamp the writer
    actually produces as UNKNOWN — a false alarm on every refreshed map.
    """
    red, unknown = ci_manifest.check(_manifest("2026-09-28T04:30:00Z"), NOW)
    assert (red, unknown) == ([], [])


def test_an_unquoted_iso_stamp_is_a_timestamp_not_unknown() -> None:
    """The artifact under validation is the FILE, so the parser is fed YAML text.

    PyYAML resolves an UNQUOTED ISO-8601 scalar to ``datetime.datetime``. A
    parser that accepted only ``str`` reported that UNQUOTED spelling as
    "not a parseable timestamp" — UNKNOWN, staleness skipped, `--integrity`
    green — so whether a stale map was caught depended on quoting alone.
    """
    import yaml

    loaded = yaml.safe_load(
        f"{ci_manifest.CAPTURED_AT_KEY}: 2026-09-28T04:30:00Z\n")
    raw = loaded[ci_manifest.CAPTURED_AT_KEY]
    assert isinstance(raw, dt.datetime) and raw.tzinfo is not None, (
        "precondition: an unquoted ISO scalar resolves to a datetime")
    assert ci_manifest._parse_captured_at(raw) == dt.datetime(
        2026, 9, 28, 4, 30, tzinfo=dt.UTC)
    assert ci_manifest.check(_manifest(raw), NOW) == ([], [])


def test_a_date_only_stamp_is_midnight_utc() -> None:
    """YAML's date-only scalar is a legal spelling of a capture day."""
    import yaml

    loaded = yaml.safe_load(f"{ci_manifest.CAPTURED_AT_KEY}: 2026-09-28\n")
    raw = loaded[ci_manifest.CAPTURED_AT_KEY]
    assert isinstance(raw, dt.date) and not isinstance(raw, dt.datetime)
    assert ci_manifest._parse_captured_at(raw) == dt.datetime(
        2026, 9, 28, tzinfo=dt.UTC)


def test_an_unquoted_stale_stamp_is_red_through_the_cli(tmp_path) -> None:
    """The CLI verdict must not flip to UNKNOWN on quoting alone.

    The writer emits a QUOTED stamp; the UNQUOTED spelling here is the
    HAND-EDITED case this parser also accepts, and `yaml.safe_load` then hands
    the validator a ``datetime``. Before the fix that value was "not a
    parseable timestamp" → exit 2, and the staleness check that is this PR's
    headline capability never ran. It must be the OBSERVED defect: exit 1, not
    2.
    """
    import yaml

    stale = (dt.datetime.now(dt.UTC)
             - dt.timedelta(days=ci_manifest.MAX_AGE_DAYS + 1))
    text = yaml.safe_dump(_manifest("PLACEHOLDER")).replace(
        "PLACEHOLDER", stale.strftime("%Y-%m-%dT%H:%M:%SZ"))
    assert isinstance(yaml.safe_load(text)[ci_manifest.CAPTURED_AT_KEY],
                      dt.datetime), "precondition: the stamp is unquoted"
    path = tmp_path / "unquoted-ci-surfaces.yml"
    path.write_text(text)
    assert ci_manifest.main(["--manifest", str(path)]) == 1


def test_a_fresh_capture_date_is_green() -> None:
    stamp = (NOW - dt.timedelta(days=1)).isoformat()
    assert ci_manifest.check(_manifest(stamp), NOW) == ([], [])


def test_a_stale_capture_date_is_red() -> None:
    stamp = (NOW - dt.timedelta(days=ci_manifest.MAX_AGE_DAYS + 1)).isoformat()
    red, unknown = ci_manifest.check(_manifest(stamp), NOW)
    assert unknown == []
    assert any("days ago" in reason for reason in red), red


def test_a_capture_date_from_the_future_is_red() -> None:
    stamp = (NOW + dt.timedelta(days=3)).isoformat()
    red, unknown = ci_manifest.check(_manifest(stamp), NOW)
    assert unknown == []
    assert any("future" in reason for reason in red), red


# ── plausibility: what the writer could have produced ────────────────────


@pytest.mark.parametrize("value", [0.05, 0.09, 0.1 - 1e-9])
def test_a_weight_between_the_sentinel_and_the_floor_is_red(value: float) -> None:
    """The writer renders `max(measured, 0.1)`, so this range is unreachable.

    A value inside it did not come from a measurement and is not the declared
    sentinel either — it is a number somebody typed.
    """
    manifest = _manifest((NOW - dt.timedelta(days=1)).isoformat())
    key = cs.fast_pool(manifest)[0]
    manifest["durations"][key] = value
    red, unknown = ci_manifest.check(manifest, NOW)
    assert unknown == []
    assert any(key in reason and "invention" in reason for reason in red), red


def test_the_floor_is_the_writers_own() -> None:
    """A validator floor that drifted from the writer's would red weights the
    bridge legitimately writes, or bless ones it cannot produce."""
    from tools import ci_timing

    assert ci_manifest.VALUE_FLOOR == ci_timing.DURATIONS_VALUE_FLOOR_S


def test_the_writers_precision_is_the_one_this_check_assumes() -> None:
    """A precision the writer does not render is decidable, so it is checked.

    `render_refreshed_manifest` emits `f"{max(seconds, 0.1):.1f}"`, so a value
    like a hand-typed `0.15` cannot have come from it. If the writer ever emitted
    more places, this check would red weights it legitimately wrote — the same
    drift `test_the_floor_is_the_writers_own` guards for the floor.
    """
    from tools import ci_timing

    rendered, _ = ci_timing.render_refreshed_manifest(
        cs.MANIFEST.read_text(), {"test_bridge_table.py": 1.23456}, "T")
    assert ci_manifest.VALUE_DECIMALS == 1, (
        "the validator's precision pin drifted from the writer's one-decimal "
        "render")
    assert "  test_bridge_table.py: 1.2\n" in rendered, (
        "the writer no longer renders one decimal place — VALUE_DECIMALS and "
        "the plausibility check must move with it")


def test_a_weight_with_finer_precision_than_the_writer_is_red() -> None:
    """`0.15` is a weight the writer cannot produce, so it is not a measurement.

    Decidable by value for the same reason the sub-floor window is: the bridge
    renders one decimal place. Without this the check passed a value it declares
    a"weight the collector could not have written" — its own contract.
    """
    manifest = _manifest((NOW - dt.timedelta(days=1)).isoformat())
    key = cs.fast_pool(manifest)[0]
    manifest["durations"][key] = 0.15
    red, _ = ci_manifest.check(manifest, NOW)
    assert any(key in reason and "precision" in reason for reason in red), red


def test_the_map_checks_are_composed_not_re_implemented() -> None:
    """`check` reports what the selector's own checks report.

    The leg/value arithmetic decides the gate, so a validator-local copy of it
    would be a second gate free to disagree with the first.
    """
    manifest = _manifest((NOW - dt.timedelta(days=1)).isoformat())
    key = cs.fast_pool(manifest)[0]
    manifest["durations"][key] = "not a number"
    red, _ = ci_manifest.check(manifest, NOW)
    assert cs.duration_issues(manifest), "precondition: the selector names it"
    assert any(key in reason for reason in red), red


def test_a_malformed_map_is_red_where_an_absent_one_is_unknown() -> None:
    """`durations: 0` is a BROKEN map; a missing `durations:` is an ABSENT one.

    Those are the two different exit codes, and the selector already owns the
    diagnosis (`durations is not a mapping: int`). A validator that consulted
    `_durations()` before asking the selector routes every non-mapping down the
    empty path, reporting a hand-broken key as UNKNOWN — exit 2, a state nobody
    owns — instead of exit 1 on the defect that is right there. Regression
    caught by the selector's own
    `test_null_or_non_mapping_durations_reports_instead_of_tracebacking`.
    """
    stamp = (NOW - dt.timedelta(days=1)).isoformat()

    red, _ = ci_manifest.check(_manifest(stamp, durations=0), NOW)
    assert any("not a mapping" in reason for reason in red), red

    absent = _manifest(stamp)
    absent.pop("durations")
    red_absent, unknown_absent = ci_manifest.check(absent, NOW)
    assert red_absent == [], red_absent
    assert unknown_absent, "an absent map is UNKNOWN, not a defect"


def test_a_non_string_key_is_red_not_a_crash() -> None:
    """A hand-edited `123: 0.9` row is a defect the selector NAMES.

    YAML yields non-string keys (int/bool/None), and an unsorted
    `sorted(items)` raises `TypeError` on the mixed types. That escaped `check()`
    and `main()` reported exit 2 (UNKNOWN) — DISCARDING the red `duration_issues`
    had already produced, i.e. a named defect reported as the softer verdict.
    """
    manifest = _manifest((NOW - dt.timedelta(days=1)).isoformat())
    manifest["durations"][123] = 0.9
    red, _ = ci_manifest.check(manifest, NOW)
    assert any("123" in reason for reason in red), red


def test_a_map_with_no_non_zero_weight_is_red(tmp_path) -> None:
    """An all-sentinel map is a state the writer can NEVER produce.

    The per-row tolerance for an individual `0.0` is for an honest
    carry-forward or a pack pin; a map where EVERY weight is `0.0` carries no
    measurement at all. The writer refuses a zero measured key and floors every
    resolved key to `max(seconds, 0.1)`, so this map is unreachable for it —
    leaving it green was the fail-open this module exists to close.
    """
    fresh = dt.datetime.now(dt.UTC).isoformat()
    manifest = _manifest(fresh)
    manifest["durations"] = {k: 0.0 for k in manifest["durations"]}
    red, _ = ci_manifest.check(manifest, NOW)
    assert any("no non-zero weight" in reason for reason in red), red
    assert _cli(manifest, tmp_path) == 1, "the CLI verdict must be exit 1"


def test_a_red_defect_outranks_an_unknown(tmp_path) -> None:
    """A map that is observed wrong is red even when its age is also unknown,
    so a caller gating on the exit code cannot pick the softer of the two."""
    manifest = _manifest()  # no capture date → UNKNOWN
    manifest["durations"][cs.fast_pool(manifest)[0]] = 0.05
    red, unknown = ci_manifest.check(manifest, NOW)
    assert red and unknown
    assert _cli(manifest, tmp_path) == 1


# ── the CLI's exit codes ─────────────────────────────────────────────────


def _cli(manifest: dict, tmp_path: Path) -> int:
    import yaml

    path = tmp_path / "ci-surfaces.yml"
    path.write_text(yaml.safe_dump(manifest))
    return ci_manifest.main(["--manifest", str(path)])


def test_cli_exit_codes_are_zero_one_and_two(tmp_path) -> None:
    fresh = dt.datetime.now(dt.UTC).isoformat()
    assert _cli(_manifest(fresh), tmp_path) == 0

    green = _manifest(fresh)
    green["durations"] = dict(green["durations"])
    green["durations"][cs.fast_pool(green)[0]] = 0.05
    assert _cli(green, tmp_path) == 1

    assert _cli(_manifest(), tmp_path) == 2


def test_the_cli_still_distinguishes_red_from_unknown(tmp_path) -> None:
    """#6243 review cycle 2 (d): the selector-side K1 fix must NOT move the
    validator's own contract.

    Through `ci_manifest.py` itself, a stale but PARSEABLE stamp stays RED
    (exit 1), while an unparseable one and an absent one both stay UNKNOWN
    (exit 2). The red-with-junk decision lives in the enforcing selector entry
    point; it does not change exit-2-for-unknown here.
    """
    stale = (dt.datetime.now(dt.UTC)
             - dt.timedelta(days=ci_manifest.MAX_AGE_DAYS + 1)).isoformat()
    assert _cli(_manifest(stale), tmp_path) == 1
    assert _cli(_manifest("not-a-date"), tmp_path) == 2
    assert _cli(_manifest(), tmp_path) == 2


def test_an_unreadable_manifest_is_unknown_not_a_traceback(tmp_path) -> None:
    """Exit 2 has to carry the same meaning as the report: a manifest we cannot
    read — or cannot WALK — is a state the validator could not observe, not a
    crash and not an observed defect."""
    missing = tmp_path / "absent.yml"
    assert ci_manifest.main(["--manifest", str(missing)]) == 2

    malformed = tmp_path / "malformed.yml"
    malformed.write_text("surfaces: [unclosed\n")
    assert ci_manifest.main(["--manifest", str(malformed)]) == 2

    # Parses, but has no `surfaces:` key — the selector's own accessors raise
    # `KeyError` inside `check`. A bare traceback would exit 1, the code for an
    # observed defect.
    structureless = tmp_path / "no-surfaces.yml"
    structureless.write_text("durations: {}\n")
    assert ci_manifest.main(["--manifest", str(structureless)]) == 2


# ── the gate's polarity ──────────────────────────────────────────────────


def test_integrity_notices_unknown_and_still_fails_red(
    tmp_path, monkeypatch, capsys
) -> None:
    """UNKNOWN must be visible in the gate but must not gate it.

    Failing on it would red `manifest-integrity` repo-wide until a weekly data
    refresh landed, refusing honest merges for a state no lane owns — while
    silence would let an unobserved map read as a validated one. So: a notice,
    plus `ci_manifest.py` exiting 2. A RED defect is a defect either way.
    """
    import yaml

    # The UNKNOWN half runs against a manifest that DEFINITIVELY has no capture
    # date, built here, rather than against the committed map. Pointing it at the
    # real map would red the moment a refresh stamped it — i.e. on the first
    # working run of the automation this change exists to enable. Same
    # stale-able-coupling class as the committed-map test.
    real_text = cs.MANIFEST.read_text()
    unstamped = tmp_path / "unstamped-ci-surfaces.yml"
    unstamped.write_text("\n".join(
        line for line in real_text.split("\n")
        if not line.startswith(f"{ci_manifest.CAPTURED_AT_KEY}:")))
    monkeypatch.setattr(cs, "MANIFEST", unstamped)
    monkeypatch.setattr(sys, "argv", ["ci_selection.py", "--integrity"])
    assert cs.main() == 0
    assert "UNKNOWN" in capsys.readouterr().out

    poisoned = _manifest()
    poisoned["durations"][cs.fast_pool(poisoned)[0]] = 0.05
    path = tmp_path / "poisoned-ci-surfaces.yml"
    path.write_text(yaml.safe_dump(poisoned))
    monkeypatch.setattr(cs, "MANIFEST", path)
    monkeypatch.setattr(sys, "argv", ["ci_selection.py", "--integrity"])
    assert cs.main() == 1
