"""#5064 — the declared-mechanism gate (the bounded slice).

THE ROOT THIS PINS, IN ONE SENTENCE
-----------------------------------
*"The design documents a substrate the code does not have"* — extractor v4
declares selection, dedup, merge, span-linkage, an embedding gate, a sufficiency
router and a fan-out cap as adopted decisions, and **nothing compared the
declared set against the code set, so a declared mechanism read exactly like a
shipped one.**

This gate makes the declaration checkable. `config/v4-mechanisms.yml` is the ONE
place the declared set lives; this file is the gate over it. Every declared
mechanism must be either

  * **implemented** — a code path AND a test, every declared path present; or
  * **not-implemented** — an explicit marker (a tracking issue);

and a mechanism that is **neither** FAILS the gate. A mechanism with code but no
test is not "implemented with a test" — that is the `declared ⇒ present`
inference this issue exists to kill, one level down.

WHAT THIS GATE DELIBERATELY DOES NOT DO (#5064, bounded slice)
-------------------------------------------------------------
* It does not implement any mechanism. Three of the seven are genuinely absent
  and are *meant* to be marked `not-implemented` — recording an absence is the
  output, not a failure.
* It does not generate a design document from the registry (indicator (2) /
  #5064's open question (a): registry-as-source vs registry-as-check). The
  registry is a CHECK over the two design documents here, not their source.
* It does not touch the extraction/write-path stages.
* It does not check that a cited test file is REGISTERED in the CI manifest —
  that surface is owned by `tools/ci_selection.py --integrity`. The gate checks
  that the path exists, not that CI runs it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "config" / "v4-mechanisms.yml"

#: The declared set is CLOSED — exactly these seven, in this order (#5064 O/I/T
#: indicator (1): "VET, source dedup, near-dup MERGE, SPAN link, EMBED gate,
#: sufficiency router, fan-out cap"). Pinning the tuple means a mechanism cannot
#: be dropped from the registry without the gate going red, which is the whole
#: point: the set is declared, not implied.
DECLARED_IDS = (
    "vet",
    "source_dedup",
    "near_dup_merge",
    "span_link",
    "embed_gate",
    "sufficiency_router",
    "fanout_cap",
)

#: The only two admissible states. There is no third state and no unmarked row.
STATES = ("implemented", "not-implemented")


def load_registry(path: Path = REGISTRY) -> dict:
    """Parse the declared-mechanism registry."""
    with open(path, encoding="utf-8") as fh:
        doc = yaml.safe_load(fh)
    assert isinstance(doc, dict), f"{path} must parse to a mapping"
    return doc


def gate_errors(mechanisms: list[dict], root: Path = ROOT) -> list[str]:
    """The gate. Returns every violation — an EMPTY list is the only pass.

    FAIL CLOSED: the trailing ``else`` catches a row whose ``state`` is missing,
    misspelled, or anything outside :data:`STATES`, and reports it as NEITHER
    implemented nor marked. A new state word cannot silently become a pass —
    an unrecognised state is a violation, not a default (the same allow-list
    polarity the checks rule uses).
    """
    errors: list[str] = []
    seen: set[str] = set()

    for row in mechanisms:
        if not isinstance(row, dict):
            errors.append(f"mechanism row is not a mapping: {row!r}")
            continue

        mid = row.get("id")
        if not mid or not isinstance(mid, str):
            errors.append(f"a mechanism row has no string id: {row!r}")
            continue
        if mid in seen:
            errors.append(f"{mid}: duplicate id — one row per mechanism")
        seen.add(mid)

        if not row.get("name"):
            errors.append(f"{mid}: declares no name")

        # "Declared" must itself be checkable: a row that names no declaring
        # document, or names one that is not on disk, is not declared anywhere.
        declared_in = row.get("declared_in") or []
        if not declared_in:
            errors.append(f"{mid}: declares no declaring document (declared_in)")
        for rel in declared_in:
            if not (root / rel).exists():
                errors.append(f"{mid}: declared_in path does not exist: {rel}")

        state = row.get("state")

        if state == "implemented":
            code = list(row.get("code") or [])
            tests = list(row.get("tests") or [])
            if not code:
                errors.append(f"{mid}: implemented but declares no code path")
            if not tests:
                errors.append(
                    f"{mid}: implemented but declares no test — a code path "
                    f"without a test is exactly the declared-but-absent shape "
                    f"this gate exists to catch"
                )
            for rel in code + tests:
                if not (root / rel).exists():
                    errors.append(f"{mid}: declared path does not exist: {rel}")

        elif state == "not-implemented":
            issue = row.get("tracking_issue")
            if issue in (None, "", 0):
                errors.append(
                    f"{mid}: not-implemented with no tracking_issue — an "
                    f"unbuilt mechanism needs a home"
                )

        else:
            errors.append(
                f"{mid}: NEITHER implemented (a code path with a test) NOR "
                f"marked not-implemented (state={state!r}) — a declared-and-absent "
                f"mechanism must be DECLARED, not implied"
            )

    return errors


# ── the gate over the real registry ─────────────────────────────────────────


def test_declared_set_is_exactly_the_seven_named_mechanisms() -> None:
    """The registry declares the closed set from #5064 indicator (1)."""
    mechanisms = load_registry()["mechanisms"]
    assert tuple(r["id"] for r in mechanisms) == DECLARED_IDS
    assert len(mechanisms) == len(DECLARED_IDS)


def test_every_declared_mechanism_is_implemented_with_a_test_or_marked_absent() -> None:
    """#5064 indicator (1), checkable: 0 mechanisms with neither code nor a marker."""
    errors = gate_errors(load_registry()["mechanisms"])
    assert errors == [], "declared-mechanism gate violations:\n  " + "\n  ".join(errors)


# ── the gate's own fail-closed proof (mutation) ─────────────────────────────
#
# A guard that cannot fail is worse than none. These four cases are the ones
# that must NEVER become a pass, and they are permanent so the gate cannot
# regress into a no-op while the registry still passes.


def test_gate_fails_closed_on_a_mechanism_that_is_neither() -> None:
    errors = gate_errors([{"id": "x", "name": "x", "declared_in": []}])
    assert any("NEITHER" in e for e in errors), errors


def test_gate_fails_closed_on_an_unknown_state_word() -> None:
    """An unrecognised state is a violation, not a default (allow-list polarity)."""
    errors = gate_errors([{"id": "x", "name": "x", "declared_in": [], "state": "maybe"}])
    assert any("NEITHER" in e for e in errors), errors


def test_gate_fails_closed_on_implemented_without_a_test() -> None:
    errors = gate_errors(
        [
            {
                "id": "x",
                "name": "x",
                "declared_in": ["config/v4-mechanisms.yml"],
                "state": "implemented",
                "code": ["config/v4-mechanisms.yml"],
            }
        ]
    )
    assert any("declares no test" in e for e in errors), errors


def test_gate_fails_closed_on_a_missing_declared_path() -> None:
    errors = gate_errors(
        [
            {
                "id": "x",
                "name": "x",
                "declared_in": ["config/v4-mechanisms.yml"],
                "state": "implemented",
                "code": ["tortoise/does_not_exist_5064.py"],
                "tests": ["tests/does_not_exist_5064.py"],
            }
        ]
    )
    assert any("does not exist" in e for e in errors), errors


def test_gate_fails_closed_on_not_implemented_without_a_tracking_issue() -> None:
    errors = gate_errors(
        [{"id": "x", "name": "x", "declared_in": [], "state": "not-implemented"}]
    )
    assert any("no tracking_issue" in e for e in errors), errors


if __name__ == "__main__":  # pragma: no cover - manual mutation aid
    raise SystemExit(pytest.main([__file__, "-q"]))
