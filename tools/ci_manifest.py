#!/usr/bin/env python3
"""Validate the MEASURED CI durations map (#5050).

``config/ci-surfaces.yml:durations`` is the weight vector ``split_fast_gate``
packs the push halves by, and the failure this module exists to prevent is
observed and concrete: the committed map claimed a perfect shard balance
(1.000x) while the measured legs ran 1.60x apart — 41.08 vs 25.65 minutes,
15.43 minutes of cycle time wasted per run. A weight that was never measured is
INDISTINGUISHABLE from one that was, which is why the fix is a measured writer
plus a validator, and not a better hand-edit.

``tools/ci_timing.py --refresh-durations`` (#6003) is the SOLE WRITER: the
collector feeds the map directly, floors every weight at
``DURATIONS_VALUE_FLOOR_S`` (0.1 s) and stamps ``durations_captured_at``.
**This module writes nothing.** It validates that map, and it fails closed::

    exit 0   observed, plausible, fresh, complete, disjoint
    exit 1   an OBSERVED defect
    exit 2   UNKNOWN — the map's fidelity could not be observed

Exit 2 is not a soft failure. The state where a stale or invented weight cannot
be told from a measured one is exactly the state where the map must not be
reported as valid, and "we could not look" is not "it is fine".

Each of the four checks the ruling names is implemented at ONE level:

    coverage       ``ci_selection.duration_coverage_issues`` (the 0.90 floor)
    disjoint keys  ``ci_selection.leg_coverage_issues`` — every classified file
                   in exactly one leg. The leg arithmetic BELONGS to the
                   selector; a validator-local copy of it would be a second
                   gate, free to disagree with the one that decides the legs.
    plausibility   any weight the writer could not have rendered   (here)
                   — sub-floor, finer precision, or negative — and a
                   non-empty map whose every weight is the `0.0` sentinel
    staleness/age  ``durations_captured_at`` vs ``MAX_AGE_DAYS``   (here)

``tools/ci_selection.py --integrity`` and the refresh's own gate in
``tools/ci_timing.py`` both call :func:`check`, so a check cannot be
half-wired into one entry point and missing from the other.

CLI::

    python3 tools/ci_manifest.py [--manifest config/ci-surfaces.yml]

The exit code is the verdict, so this is runnable as a bare command: there is
one thing to validate and one verdict, and a `check` verb for it would be
surface with no capability behind it.
"""

from __future__ import annotations

import argparse
import datetime as dt
import math
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MANIFEST = REPO / "config" / "ci-surfaces.yml"

# The collector's floor (``ci_timing.DURATIONS_VALUE_FLOOR_S``). Every weight the
# bridge writes is ``max(measured, 0.1)`` rendered at one decimal place, so a
# committed value BELOW it cannot have been produced by a measurement. That is
# the invented-weight signature this validator exists to catch, which is why the
# floor is a plausibility bound and not a formatting preference.
VALUE_FLOOR = 0.1

# The refresh is weekly (``ci-timing.yml``, cron ``30 4 * * 1``). Three missed
# refreshes means the writer stopped and the map has been drifting against
# reality with nothing measuring the drift.
MAX_AGE_DAYS = 21

# The bridge renders every sampled weight ``f"{max(seconds, FLOOR):.1f}"`` — ONE
# decimal place. So a value carrying a finer fraction (a hand-typed `0.15`) is
# as unreachable for the writer as the sub-floor window is, and is decidable for
# the same reason. Pinned to the writer's own output by
# ``test_the_writers_precision_is_the_one_this_check_assumes``.
VALUE_DECIMALS = 1

# A capture date slightly ahead of now is clock skew between the runner and the
# validator; far ahead of it is not a measurement.
FUTURE_TOLERANCE = dt.timedelta(days=1)

# The map's declared "not a measurement" sentinel. A row no source leg ever
# measured — or a deliberate minimum-weight packing pin — is written `0.0`, and
# in the committed map each such row carries an `# unmeasured` / pin comment
# (e.g. `test_time_aware_2520.py: 0.0  # unmeasured — arrived via main #5297`).
# It has to be named here, because the writer cannot produce it: the floor check
# would otherwise red a documented state, and the rows that legitimately
# hold it would have no legal spelling.
UNMEASURED_SENTINEL = 0.0

CAPTURED_AT_KEY = "durations_captured_at"

# `dt.UTC` is 3.11+ only; the 3.9 system interpreter has no such attribute, so
# every map (red or not) reported UNKNOWN instead of a verdict. Alias it once.
UTC = getattr(dt, "UTC", dt.timezone.utc)


def _module_is(path: Path, mod: object) -> bool:
    """True when ``mod`` was loaded from ``path`` (the same file on disk)."""
    file = getattr(mod, "__file__", None)
    if not file:
        return False
    try:
        return Path(file).resolve() == path.resolve()
    except OSError:  # pragma: no cover - defensive
        return False


def _ci_selection():
    """The loaded ``ci_selection`` module, whichever name it was imported under.

    ``ci_selection`` imports this module back, so the import is lazy and must
    not create a second copy under a different name — two copies would split
    module state under pytest and let ``check`` validate against a different
    manifest constant than the caller's. The RUNNING module is probed FIRST:
    ``python3 tools/ci_selection.py --integrity`` executes that file as
    ``__main__``, which is under neither ``tools.ci_selection`` nor
    ``ci_selection``, so a name-only lookup imported a SECOND copy of the same
    file and the "must not be a second copy" invariant was not true on the
    production invocation.
    """
    main = sys.modules.get("__main__")
    if main is not None and hasattr(main, "fast_pool") \
            and _module_is(REPO / "tools" / "ci_selection.py", main):
        return main
    for name in ("tools.ci_selection", "ci_selection"):
        mod = sys.modules.get(name)
        if mod is not None and hasattr(mod, "fast_pool"):
            return mod
    for path in (str(REPO), str(REPO / "tools")):
        if path not in sys.path:
            sys.path.insert(0, path)
    try:
        from tools import ci_selection as mod
    except ImportError:  # pragma: no cover - direct `tools/` runtime
        import ci_selection as mod
    return mod


def _durations(manifest: dict) -> dict:
    """`durations` as a mapping, or `{}` — delegated, never re-implemented.

    The map-shape rule has ONE definition (`ci_selection._durations_map`), and a
    validator-local copy would be a second gate free to disagree with the
    selector about what counts as a map.
    """
    return _ci_selection()._durations_map(manifest)


def _load(path: Path) -> dict:
    """A manifest file in the normalized shape every accessor assumes.

    ``_normalize_surfaces`` is the selector's own normalization (a surface may
    be declared as either a list or a mapping); using it, rather than reading
    ``surfaces`` directly, is what makes this validator see the same manifest
    the selector gates — a hand-reading of the raw YAML could disagree with the
    gate about which files a leg holds.
    """
    import yaml

    return _ci_selection()._normalize_surfaces(yaml.safe_load(path.read_text()))


# ── staleness / age ──────────────────────────────────────────────────────


def _parse_captured_at(raw: object) -> dt.datetime | None:
    """A stamp from the value as the FILE spells it, not as we wish it did.

    The writer (``ci_timing._set_captured_at``) QUOTES the stamp, so the
    canonical spelling arrives here as a ``str``. A HAND-EDITED manifest may
    leave it UNQUOTED, and PyYAML then resolves an ISO-8601 scalar to
    ``datetime.datetime`` (and a date-only scalar to ``datetime.date``).
    Accepting only ``str`` reported that hand-edited file as "not a parseable
    timestamp" — UNKNOWN, the staleness check skipped, ``--integrity`` green —
    so the map's freshness flipped fail→pass on quoting alone.
    """
    if isinstance(raw, dt.datetime):
        return (raw if raw.tzinfo is not None
                else raw.replace(tzinfo=UTC)).astimezone(UTC)
    if isinstance(raw, dt.date):  # date-only YAML scalar → midnight UTC
        return dt.datetime(raw.year, raw.month, raw.day, tzinfo=UTC)
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        stamp = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    return stamp.astimezone(UTC)


def staleness(manifest: dict,
              now: dt.datetime | None = None) -> tuple[list[str], list[str]]:
    """``(red, unknown)`` for the map's capture date.

    ABSENCE IS UNKNOWN, never green. The key's absence is not remedied by the
    file's git date either: any unrelated edit resets that, so a commit date
    would read a hand-edited map as fresh (the same reasoning
    ``ci_timing._set_captured_at`` records for why the key is machine-written
    rather than inferred).
    """
    now = now or dt.datetime.now(UTC)
    raw = manifest.get(CAPTURED_AT_KEY)
    if raw is None:
        return [], [
            f"`{CAPTURED_AT_KEY}` is absent — the map has never been refreshed "
            f"by `tools/ci_timing.py --refresh-durations`, its only writer, so "
            f"its fidelity is UNOBSERVED and a stale or invented weight cannot "
            f"be told from a measured one"
        ]
    stamp = _parse_captured_at(raw)
    if stamp is None:
        return [], [
            f"`{CAPTURED_AT_KEY}` is {raw!r}, which is not a parseable "
            f"timestamp — the map's age is UNKNOWN"
        ]
    if stamp - now > FUTURE_TOLERANCE:
        return [
            f"`{CAPTURED_AT_KEY}` is {stamp.isoformat()}, in the future "
            f"(validator clock {now.isoformat()}) — a capture date from the "
            f"future is not a measurement"
        ], []
    age = now - stamp
    if age > dt.timedelta(days=MAX_AGE_DAYS):
        return [
            f"the durations map was captured {age.days} days ago "
            f"({stamp.date().isoformat()}), past the {MAX_AGE_DAYS}-day bound "
            f"(three missed weekly refreshes) — the weights have been drifting "
            f"against reality with nothing measuring the drift. Re-run "
            f"`tools/ci_timing.py --refresh-durations`"
        ], []
    return [], []


def unparseable_stamp_issue(manifest: dict) -> str | None:
    """The RED issue for a capture stamp that is PRESENT but unparseable.

    ``staleness`` answers "can the map's AGE be observed?" — absence and
    unparseability are both UNKNOWN there, because neither yields an age. This
    answers the narrower question the enforcing gates need: is the stamp's
    VALUE malformed? The distinction is presence, and presence is the KEY'S,
    never the value's:

    * the key is ABSENT → ``None`` — ``staleness``'s UNKNOWN stays the
      non-gating notice (#6091 blocks the refresh from carrying a date, so
      gating on mere absence would red ``manifest-integrity`` repo-wide);
    * the key is PRESENT with any value ``_parse_captured_at`` rejects — ``""``,
      an explicit ``null``, ``"   "``, ``"not-a-date"``, ``0``, ``[]``, ``{}``
      → the issue text, because a present stamp describes a map whose age is
      claimed but not legible, and degrading a stale-but-parseable stamp to
      junk must not turn RED (exit 1) into GREEN (exit 0).

    The decision is the PARSER'S VERDICT plus the key's presence — never a
    hand-rolled value tuple, and never a substring of a reason string (wording
    a downstream rewrite would silently disarm). Both enforcing entry points
    (``ci_selection --integrity`` and ``ci_timing.integrity_problems``) call
    THIS, so their verdicts on the stamp cannot drift apart.
    """
    if CAPTURED_AT_KEY not in manifest:
        return None
    raw = manifest[CAPTURED_AT_KEY]
    if _parse_captured_at(raw) is not None:
        return None
    return (
        f"`{CAPTURED_AT_KEY}` is {raw!r}, which is not a parseable timestamp — "
        f"a malformed stamp is an OBSERVED defect in the manifest, not the "
        f"unobserved 'never refreshed' state, so it is RED (#5050)"
    )


# ── value plausibility ───────────────────────────────────────────────────


def plausibility_issues(manifest: dict) -> list[str]:
    """A weight the collector could not have written is not a measurement.

    This is the check the ruling asks for by name, and it is only decidable
    BECAUSE the writer has a floor and a fixed render: every SAMPLED weight is
    rewritten as ``max(measured, 0.1)`` at one decimal place, so the values that
    occur are exactly ``0.0`` (the declared unmeasured/pin sentinel the merge
    preserves) and one-decimal values ``>= 0.1``. Anything else — the sub-floor
    window, or a finer fraction such as a hand-typed `0.15` — is unreachable by
    construction, and that is what this validator exists to make visible rather
    than indistinguishable.

    Values that are the wrong TYPE or non-finite are ``duration_issues``' job
    and are left to it rather than reported twice. A NEGATIVE weight is not
    skipped: it is unreachable for the writer for exactly the same reason the
    ``(0, 0.1)`` window is, so ``duration_issues`` and this check both name it —
    a duplicated diagnostic on a list that is read as a whole, not two gates.

    Note the honest limit: an INDIVIDUAL ``0.0`` is ALLOWED because whether a
    given zero is an unmeasured carry-forward, a deliberate pack pin, or a lazy
    stand-in for a number nobody took cannot be decided from the value at all.
    The map-LEVEL rule below is the exception: a non-empty map in which EVERY
    value is the ``0.0`` sentinel carries no measurement, and the writer — which
    refuses a zero measured key and floors every resolved key to
    ``max(seconds, VALUE_FLOOR)`` — cannot produce one. That
    indistinguishability is exactly why the map needed a writer-side capture
    date, so the freshness half of this module — not a guess about a zero — is
    what guards the individual sentinel rows.
    """
    issues: list[str] = []
    durations = _durations(manifest)
    # `str` sort keys: a hand-edited map can carry a NON-STRING key (`123: 0.9`,
    # `true: …`, `null: …` — YAML yields int/bool/None), and an unsorted
    # `sorted(items)` raises `TypeError` on the mixed types. That propagated out
    # of `check()` and `main()` reported exit 2 (UNKNOWN), DISCARDING the red that
    # `duration_issues` had already produced — a named defect reported as the
    # softer verdict, against this module's own "red outranks unknown". The
    # selector still names the key; this loop must not crash on the way there.
    for name, value in sorted(durations.items(), key=lambda kv: str(kv[0])):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        try:
            if not math.isfinite(value):
                continue
            if value == UNMEASURED_SENTINEL:
                continue
            if value >= VALUE_FLOOR \
                    and round(float(value), VALUE_DECIMALS) == float(value):
                continue
        except OverflowError:  # an int beyond float range; named elsewhere
            continue
        unreachable = (
            f"below the collector's {VALUE_FLOOR}s floor"
            if value < VALUE_FLOOR else
            f"carried finer precision than the collector renders "
            f"({VALUE_DECIMALS} decimal place)"
        )
        issues.append(
            f"durations value for {name} is {value!r} — {unreachable}. The "
            f"bridge rewrites every sampled weight as ``max(measured, "
            f"{VALUE_FLOOR})`` rendered at {VALUE_DECIMALS} decimal place, and "
            f"writes {UNMEASURED_SENTINEL} only as the declared unmeasured/pin "
            f"sentinel the merge preserves. Anything else is unreachable for it "
            f"— a weight is a measurement or it is an invention (#5050)"
        )
    # A non-empty map whose EVERY value is the unmeasured sentinel carries no
    # measurement at all. The per-row tolerance above is for an individual `0.0`
    # (an honest carry-forward or a pack pin), but the writer can never produce
    # an all-zero map: it refuses a zero measured key and floors every resolved
    # key to `max(seconds, VALUE_FLOOR)`. So this state is unreachable-and-
    # unobserved, and leaving it green would be the fail-open the module exists
    # to close.
    if durations and all(
        not isinstance(v, bool) and isinstance(v, (int, float))
        and v == UNMEASURED_SENTINEL
        for v in durations.values()
    ):
        issues.append(
            f"every durations weight is the {UNMEASURED_SENTINEL} unmeasured "
            f"sentinel — a map with no non-zero weight carries no measurement, "
            f"and the writer (which floors every resolved key and refuses a zero "
            f"measured key) cannot produce it. Unreachable for the writer, so "
            f"unobserved for this check (#5050)"
        )
    return issues


# ── the composed contract ────────────────────────────────────────────────


def map_issues(manifest: dict) -> list[str]:
    """The map checks that already exist, composed in the #3407 order.

    ``duration_issues`` MUST run before ``leg_coverage_issues``: the latter
    calls ``push_legs()`` -> ``split_fast_gate()``, so before the ordering fix a
    malformed weight raised inside the packer, before the check that NAMES it
    had run.
    """
    cs = _ci_selection()
    return (cs.duration_issues(manifest)
            + cs.leg_coverage_issues(manifest)
            + cs.duration_coverage_issues(manifest))


def check(manifest: dict,
          now: dt.datetime | None = None) -> tuple[list[str], list[str]]:
    """The whole verdict: ``(red, unknown)``.

    ``red`` is non-empty when the map is OBSERVED to be wrong; ``unknown`` is
    non-empty when its fidelity could not be observed. A red map is red whatever
    the unknowns are, so a caller that gates on ``red`` alone still fails what
    it can see — but it must not read an empty ``red`` as "valid" while
    ``unknown`` is non-empty, which is why the two are returned separately
    instead of folded into one list.
    """
    cs = _ci_selection()
    stale_red, unknown = staleness(manifest, now)
    # `map_issues` runs UNCONDITIONALLY, including when `durations` is absent or
    # malformed: the selector's own `duration_issues` is what NAMES a map that is
    # not a mapping (`durations is not a mapping: int`), and it returns `[]` for
    # the genuinely-absent case. Deciding "empty" from `_durations` first would
    # route a hand-broken `durations:` key down the empty path and report it as
    # merely UNKNOWN — a broken map must be RED.
    red = map_issues(manifest) + plausibility_issues(manifest) + stale_red
    # Gate on the RAW value: only absent or genuinely empty is the empty state.
    # (See the note above — `_durations` collapses every non-mapping to `{}`.)
    raw_durations = manifest.get("durations")
    if raw_durations is None or raw_durations == {}:
        fast = cs.fast_pool(manifest)
        if fast:
            unknown.append(
                f"the durations map is empty while the manifest classifies "
                f"{len(fast)} fast-pool file(s) — there is no map here to "
                f"validate"
            )
    return red, unknown


# ── CLI ──────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--manifest", default=str(MANIFEST),
                    help="the manifest whose `durations:` map is validated")
    args = ap.parse_args(argv)

    path = Path(args.manifest)
    try:
        manifest = _load(path)
        red, unknown = check(manifest)
    except Exception as exc:
        # Never a traceback and never 0: the exit code has to carry the same
        # meaning as the report does, or a caller reads "could not look" as
        # "looked and it was fine". `check` is INSIDE the guard because a
        # manifest that parses but cannot be walked — `durations: {}` with no
        # `surfaces:` key raises `KeyError` inside the selector's own accessors —
        # is one this validator could not EVALUATE, i.e. UNKNOWN. A bare
        # traceback exits 1, which is the code for an observed defect.
        print(f"2: cannot evaluate {path}: {exc!r}", file=sys.stderr)
        return 2
    for reason in unknown:
        print(f"? UNKNOWN: {reason}", file=sys.stderr)
    for reason in red:
        print(f"x {reason}", file=sys.stderr)
    if red:
        return 1
    if unknown:
        return 2
    print(f"ok {path}: the durations map is complete, disjoint and plausible "
          f"(every weight is one the collector can write), and fresh "
          f"(<= {MAX_AGE_DAYS} days)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
