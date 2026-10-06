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

IMPLEMENTED = "implemented"
NOT_IMPLEMENTED = "not-implemented"

#: The two states this bounded slice supports. ⚠️ #5064's own row model also
#: names a third, `descriptive` (its open question (c) asks whether it survives).
#: It is deliberately NOT accepted here, so a `descriptive` row FAILS CLOSED
#: until that question is settled — the safe direction, and an allow-list.
STATES = (IMPLEMENTED, NOT_IMPLEMENTED)


def load_registry(path: Path = REGISTRY) -> dict:
    """Parse the declared-mechanism registry."""
    with open(path, encoding="utf-8") as fh:
        doc = yaml.safe_load(fh)
    assert isinstance(doc, dict), f"{path} must parse to a mapping"
    return doc


def gate_errors(mechanisms: list[dict], root: Path = ROOT) -> list[str]:
    """The gate. Returns every violation — an EMPTY list is the only pass.

    FAIL CLOSED: a row whose ``state`` is missing, misspelled, or anything
    outside :data:`STATES` is reported as NEITHER implemented nor marked. A new
    state word cannot silently become a pass — an unrecognised state is a
    violation, not a default (the same allow-list polarity the checks rule uses).
    """
    errors: list[str] = []
    seen: set[str] = set()

    # A non-iterable registry is a violation, not a crash (cycle-4 P2): the
    # `mechanisms` value of `yaml.safe_load("mechanisms:\n")` is `None`.
    if not isinstance(mechanisms, list):
        return [f"mechanisms must be a list, got {type(mechanisms).__name__}"]

    #: Field -> the KIND of artifact it must name. A declared path that EXISTS
    #: but is the wrong kind (a README satisfying `code:`, a source file
    #: satisfying `tests:`) is the same fail-open class as a missing one: the
    #: row claims an artifact it does not name. Cycle 4 closed it for `tests:`,
    #: cycle 5 for `code:`/`declared_in:` — closed here as one rule.
    def _is_test_name(name: str) -> bool:
        return (
            (name.startswith("test_") and name.endswith(".py"))
            or name.endswith("_test.py")
        )

    #: The KIND rule for ONE artifact name, or ``None`` when it is the right
    #: kind. Applied to BOTH the declared name and the RESOLVED name (cycle-7
    #: P1): a committed symlink named `fake_code.py` -> `docs/design.md`
    #: satisfied a name-only check while no `.py` was ever named.
    def _kind_violation(field: str, kind: str, name: str) -> str | None:
        if kind == "test" and not _is_test_name(name):
            return f"{field} entry is not a test file (expected `test_*.py` or `*_test.py`)"
        if kind == "code" and (Path(name).suffix != ".py" or _is_test_name(name)):
            return f"{field} entry is not production source (expected a non-test `.py`)"
        if kind == "doc" and Path(name).suffix not in (".md", ".yaml", ".yml"):
            return f"{field} entry is not a declaring document (expected `.md`/`.yaml`/`.yml`)"
        return None

    def _path_violations(mid: str, field: str, entries: object, root: Path,
                         kind: str) -> list[str]:
        """A declared path must be the RIGHT KIND of artifact, on disk, IN the repo.

        A bare `root / rel` is fail-OPEN (cycle-2 review P1): `""` resolves to
        `root` itself, an absolute `rel` replaces `root` entirely, `..` escapes
        the repo, and a non-string raises `TypeError` — a crash, not a verdict.
        A mapping whose KEYS are existing paths also passed by key iteration.
        So the entry is validated before it is joined.

        `kind` (cycles 4-5) pins WHAT the path must be, because existence alone
        does not prove the claim: `tests:` must match the repo's own naming
        convention (`test_*.py` / `*_test.py`, wherever tests live — `tests/`,
        `graph-scripts/`, `integrations/tests/` — so no prefix is required);
        `code:` must be a non-test `.py`; `declared_in:` must be a document
        (`.md`/`.yaml`/`.yml`).

        Containment (cycle-5 P2) is by RESOLVED path: the repo carries committed
        symlinks (`scripts/`, `skills/` → agent-infra), so `is_file()` alone
        followed a declared path out of the repo.

        PRECONDITION: `entries` is a non-empty list. Every caller checks that
        first and emits its own message, so a container-shape guard here would
        be unreachable (cycle-8 P2).
        """
        out: list[str] = []
        root_resolved = root.resolve()
        for rel in entries:
            if not isinstance(rel, str) or not rel:
                out.append(f"{mid}: {field} entry is not a non-empty string: {rel!r}")
                continue
            p = Path(rel)
            # ⚠️ `not p.parts` (and the `not rel` above) are BELT-AND-BRACES, the
            # one documented exception to this file's "a guard must be seen to
            # fail" rule (cycle-10 P2-4): an empty or dot basename is also
            # rejected by the per-field kind rule (`Path("").suffix == ""` is no
            # valid doc/code/test name), so neither operand can be the DECIDING
            # check in any test. They are kept because the kind rules are the
            # part most likely to change, and a future kind that accepted a bare
            # name must still not admit `""` or `"."` as an artifact.
            if p.is_absolute() or ".." in p.parts or not p.parts:
                out.append(
                    f"{mid}: {field} path must be a repo-relative path inside "
                    f"the repo: {rel!r}"
                )
                continue
            bad = _kind_violation(field, kind, p.name)
            if bad is not None:
                out.append(f"{mid}: {bad}: {rel!r}")
                continue
            # `is_file()`, not `exists()`: a DIRECTORY satisfied an exists-only
            # check (cycle-3 P2). The probe is wrapped because a declared path
            # the OS cannot probe must be a VERDICT, not a crash: NAME_MAX
            # overflow raises `OSError`, an embedded NUL or a lone surrogate
            # raises `ValueError`/`UnicodeEncodeError`, and a symlink loop
            # raises `RuntimeError` (cycles 3 and 6).
            try:
                resolved = (root / p).resolve()
                present = resolved.is_file() and resolved.is_relative_to(root_resolved)
            except (OSError, ValueError, RuntimeError):
                present = False
            if not present:
                out.append(f"{mid}: declared path does not exist: {rel}")
                continue
            # The KIND must hold for the RESOLVED target too — otherwise the
            # kind is a property of the NAME, and a symlink launders it.
            bad = _kind_violation(field, kind, resolved.name)
            if bad is not None:
                out.append(
                    f"{mid}: {bad}, but {rel!r} resolves to {resolved.name!r}"
                )
        return out

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

        if not isinstance(row.get("name"), str) or not row.get("name").strip():
            errors.append(f"{mid}: declares no name")

        # "Declared" must itself be checkable: a row that names no declaring
        # document, or names one that is not on disk, is not declared anywhere.
        declared_in = row.get("declared_in")
        if not isinstance(declared_in, list) or not declared_in:
            errors.append(f"{mid}: declares no declaring document (declared_in)")
        else:
            errors.extend(_path_violations(mid, "declared_in", declared_in, root, "doc"))

        # The polarity is read FROM `STATES`, so the accepted set and the
        # message naming it cannot drift (cycle-2 review P2).
        state = row.get("state")
        if state not in STATES:
            errors.append(
                f"{mid}: NEITHER implemented (a code path with a test) NOR "
                f"marked not-implemented (state={state!r}, expected one of "
                f"{STATES}) — a declared-and-absent mechanism must be DECLARED, "
                f"not implied"
            )
            continue

        if state == IMPLEMENTED:
            if not isinstance(row.get("code"), list) or not row.get("code"):
                errors.append(f"{mid}: implemented but declares no code path")
            else:
                errors.extend(_path_violations(mid, "code", row["code"], root, "code"))
            if not isinstance(row.get("tests"), list) or not row.get("tests"):
                errors.append(
                    f"{mid}: implemented but declares no test — a code path "
                    f"without a test is exactly the declared-but-absent shape "
                    f"this gate exists to catch"
                )
            else:
                errors.extend(
                    _path_violations(mid, "tests", row["tests"], root, "test")
                )

        else:  # NOT_IMPLEMENTED — the only other member of STATES
            # A tracking issue must be a REAL issue number — a positive,
            # non-bool int. `row.get(...) in (None, "", 0)` used `==` and passed
            # `[]`, `" "`, `"0"`, `"TBD"` and any negative int — a placeholder
            # on the one row that claims to be marked. The check is an allow-list
            # on the type and the sign (cycle-1 review P1). ⚠️ EXISTENCE is NOT
            # verified — `tracking_issue: 424242` passes; link-checking is a
            # different job, not this gate's.
            issue = row.get("tracking_issue")
            if isinstance(issue, bool) or not isinstance(issue, int) or issue <= 0:
                errors.append(
                    f"{mid}: not-implemented with no real tracking_issue "
                    f"(got {issue!r}) — an unbuilt mechanism needs a home"
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
# A guard that cannot fail is worse than none. These cases are the ones that
# must NEVER become a pass, and they are permanent so the gate cannot regress
# into a no-op while the registry still passes.


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
                "code": ["tortoise/fanout.py"],
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
    assert any("no real tracking_issue" in e for e in errors), errors


def test_gate_fails_closed_on_a_placeholder_tracking_issue() -> None:
    """A tracking issue must be a REAL issue number, not a truthy placeholder.

    Cycle-1 review P1: `in (None, "", 0)` passed `[]`, `" "`, `"0"`, `"TBD"`
    and negative ints, so a `not-implemented` row could be "marked" with a
    placeholder on the one field whose whole job is to be a marker.
    """
    for bad in ([], " ", "TBD", "0", -3, True, 0.0):
        errors = gate_errors(
            [
                {
                    "id": "x",
                    "name": "x",
                    "declared_in": [],
                    "state": "not-implemented",
                    "tracking_issue": bad,
                }
            ]
        )
        assert any("tracking_issue" in e for e in errors), (bad, errors)


def test_gate_fails_closed_on_a_fake_declared_path() -> None:
    """A declared path must be a NON-EMPTY, REPO-RELATIVE string that exists.

    Cycle-2 review P1: a bare `root / rel` was fail-OPEN. `""` resolves to
    `root` itself, an absolute path replaces `root`, `..` escapes the repo, and
    a non-string raises `TypeError` instead of reporting. So an `implemented`
    row could carry zero real code and zero real tests and still pass — the
    `declared => present` inference this gate exists to kill.
    """
    base = {
        "id": "x",
        "name": "x",
        "state": "implemented",
        "declared_in": ["config/v4-mechanisms.yml"],
        "code": ["tortoise/fanout.py"],
        "tests": ["tests/test_fanout_cap.py"],
    }
    for field in ("declared_in", "code", "tests"):
        # `"."` is included because `Path(".").parts == ()` and `root / "."` is
        # `root` — it EXISTS, so an exists-only check passed it silently.
        for bad in ([""], ["."], [".."], ["/etc/hosts"], ["../../etc/hosts"],
                    [123], [None], [{}], ["a/../b"], ["tortoise"], ["tests"]):
            row = dict(base)
            row[field] = bad
            errors = gate_errors([row])
            assert errors, (field, bad, "the gate PASSED a fake path")

    # ⚠️ The cases above are all KIND-INVALID, so `_kind_violation` rejects them
    # before the shape guard runs — and `assert errors` cannot tell the two
    # apart. Dropping `or ".." in p.parts` (and `p.is_absolute()`) survived the
    # whole suite (cycle-9 P2). These are KIND-VALID, so ONLY the repo-relative
    # guard can reject them, and the assertion names the message that must fire.
    for field, val in (
        ("declared_in", "config/../config/v4-mechanisms.yml"),
        ("code", "tortoise/../tortoise/fanout.py"),
        ("tests", "tests/../tests/test_fanout_cap.py"),
        ("code", str(ROOT / "tortoise" / "fanout.py")),
    ):
        row = dict(base)
        row[field] = [val]
        errors = gate_errors([row])
        assert any("repo-relative path inside the repo" in e for e in errors), (field, val, errors)


def test_gate_accepts_the_star_test_suffix(tmp_path) -> None:
    """`*_test.py` is a legitimate test name, not only `test_*.py` (cycle-9 P2).

    The second operand of `_is_test_name` was never the DECIDING check in any
    test, so mutating it to `or False` survived the suite — even though the repo
    ships `*_test.py` tests (`graph-scripts/smoke_test.py`, `benchmarks/load_test.py`).
    """
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "impl.py").write_text("x = 1\n")
    (repo / "docs").mkdir()
    (repo / "docs" / "design.md").write_text("x\n")
    (repo / "tests").mkdir()
    (repo / "tests" / "smoke_test.py").write_text("x\n")
    row = {
        "id": "x",
        "name": "x",
        "state": "implemented",
        "declared_in": ["docs/design.md"],
        "code": ["src/impl.py"],
        "tests": ["tests/smoke_test.py"],
    }
    assert gate_errors([row], root=repo) == []


def test_gate_fails_closed_on_an_unprobeable_path_without_raising() -> None:
    """A path the OS cannot probe is a violation, not a crash.

    Cycle-3 P2: a component longer than NAME_MAX made the probe raise
    `OSError`. Cycle-6 P2: an embedded NUL or a lone surrogate raises
    `ValueError`/`UnicodeEncodeError`. ⚠️ The hostile names are KIND-VALID, so
    they actually REACH the probe — an over-long name with the wrong extension
    is rejected by the kind check first, which left this test vacuous and the
    `except` uncovered (cycle-6 P2).
    """
    base = {
        "id": "x",
        "name": "x",
        "state": "implemented",
        "declared_in": ["config/v4-mechanisms.yml"],
        "code": ["tortoise/fanout.py"],
        "tests": ["tests/test_fanout_cap.py"],
    }
    hostile = {
        "declared_in": ["x" * 4996 + ".md", "a\x00b.md", "\ud800.md"],
        "code": ["x" * 4997 + ".py", "a\x00b.py", "\ud800.py"],
        "tests": ["test_" + "x" * 4994 + ".py", "test_\x00x.py", "test_\ud800.py"],
    }
    for field, bads in hostile.items():
        for bad in bads:
            row = dict(base)
            row[field] = [bad]
            errors = gate_errors([row])  # must NOT raise
            assert errors, (field, bad[:12])


def test_gate_fails_closed_on_a_symlink_loop(tmp_path) -> None:
    """A symlink loop raises `RuntimeError` from `resolve()` — a verdict, not a crash."""
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    (repo / "docs" / "design.md").write_text("x\n")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_x.py").write_text("x\n")
    (repo / "loop").symlink_to(repo / "loop")
    row = {
        "id": "x",
        "name": "x",
        "state": "implemented",
        "declared_in": ["docs/design.md"],
        "code": ["loop/a.py"],
        "tests": ["tests/test_x.py"],
    }
    errors = gate_errors([row], root=repo)  # must NOT raise
    assert errors, errors


def test_gate_fails_closed_on_a_mapping_code_or_declared_in() -> None:
    """A dict `code`/`declared_in` iterates its KEYS — it must be rejected.

    `{config/v4-mechanisms.yml: 1}` passed silently before cycle 2 (P1).

    ⚠️ The mapping key is KIND-VALID per field (cycle-10 P2-1): a kind-invalid
    key is rejected by `_kind_violation` before the container type is ever the
    deciding guard, and `assert errors` cannot tell the two apart — dropping the
    `isinstance(..., list)` check survived the suite while
    `code: {"tortoise/fanout.py": 1}` passed.
    """
    for field, key in (
        ("declared_in", "config/v4-mechanisms.yml"),
        ("code", "tortoise/fanout.py"),
        ("tests", "tests/test_fanout_cap.py"),
    ):
        row = {
            "id": "x",
            "name": "x",
            "declared_in": ["config/v4-mechanisms.yml"],
            "state": "implemented",
            "code": ["tortoise/fanout.py"],
            "tests": ["tests/test_fanout_cap.py"],
        }
        row[field] = {key: 1}
        errors = gate_errors([row])
        assert errors, (field, "the gate PASSED a mapping as a path list")


def test_gate_fails_closed_on_a_non_py_test_prefixed_name(tmp_path) -> None:
    """`test_*.py` requires the `.py` SUFFIX too (cycle-10 P2-2).

    Every non-test case lacked the `test_` prefix, so the `and name.endswith(
    ".py")` operand was never the deciding check — dropping it let an existing
    `tests/test_notes.txt` satisfy "has a test".
    """
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "impl.py").write_text("x = 1\n")
    (repo / "docs").mkdir()
    (repo / "docs" / "design.md").write_text("x\n")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_notes.txt").write_text("x\n")
    row = {
        "id": "x",
        "name": "x",
        "state": "implemented",
        "declared_in": ["docs/design.md"],
        "code": ["src/impl.py"],
        "tests": ["tests/test_notes.txt"],
    }
    errors = gate_errors([row], root=repo)
    assert any("not a test file" in e for e in errors), errors


def test_gate_accepts_the_yaml_document_suffix() -> None:
    """`.yml` is a legitimate declaring document, not only `.md` (cycle-10 P2-3).

    No test accepted a `.yml` `declared_in`, so removing that tuple member
    survived while the registry itself declares `packs/dev/manifest.yaml`.
    """
    row = {
        "id": "x",
        "name": "x",
        "state": "implemented",
        "declared_in": ["config/v4-mechanisms.yml"],
        "code": ["tortoise/fanout.py"],
        "tests": ["tests/test_fanout_cap.py"],
    }
    assert gate_errors([row]) == []


def test_gate_fails_closed_on_a_non_test_file_in_the_tests_field() -> None:
    """`tests:` must name a TEST, not merely a file that exists (cycle-4 P2).

    Before this, `tests: ["README.md"]` satisfied "implemented WITH A TEST" —
    the gate's whole claim, unverifiable. The check is the repo's own naming
    convention, so it holds wherever tests live and needs no `tests/` prefix.
    """
    for bad in (
        ["tortoise/vet_gate.py"],
        ["config/v4-mechanisms.yml"],
        ["README.md"],
        ["tests/__init__.py"],
    ):
        row = {
            "id": "x",
            "name": "x",
            "state": "implemented",
            "declared_in": ["config/v4-mechanisms.yml"],
            "code": ["tortoise/vet_gate.py"],
            "tests": bad,
        }
        errors = gate_errors([row])
        assert any("test file" in e for e in errors), (bad, errors)


def test_gate_fails_closed_on_a_non_list_registry() -> None:
    """A non-list `mechanisms` is a VERDICT, not a crash (cycle-4 P2).

    `yaml.safe_load("mechanisms:\n")` is `None`; it used to raise `TypeError`
    instead of reporting. Every other malformed shape was already reported.
    """
    for bad in (None, {}, "mechanisms", 7):
        errors = gate_errors(bad)
        assert any("must be a list" in e for e in errors), (bad, errors)


def test_gate_fails_closed_on_a_non_source_code_file() -> None:
    """`code:` must name production SOURCE, not merely a file that exists.

    Cycle-5 P2: existence-only let `code: ["README.md"]`, the registry YAML, or
    a TEST file satisfy "implemented (a code path with a test)" — the same
    fail-open class cycle 4 closed for `tests:`, left unfixed on `code:`.
    """
    for bad in (
        ["README.md"],
        ["config/v4-mechanisms.yml"],
        ["tests/test_fanout_cap.py"],
        ["tortoise/fanout.pyc"],
    ):
        row = {
            "id": "x",
            "name": "x",
            "state": "implemented",
            "declared_in": ["config/v4-mechanisms.yml"],
            "code": bad,
            "tests": ["tests/test_fanout_cap.py"],
        }
        errors = gate_errors([row])
        assert any("production source" in e for e in errors), (bad, errors)


def test_gate_fails_closed_on_a_non_document_declared_in() -> None:
    """`declared_in:` must name a DOCUMENT, not any file that exists.

    ⚠️ `notes.txt` is included because the tuple's PERMISSIVE direction was
    unpinned (cycle-10 P2-3): every rejection case had no suffix or a `.py` one,
    so adding `.txt`/`.rst` to the accepted tuple survived the suite.
    """
    for bad in (["tortoise/fanout.py"], ["tests/test_fanout_cap.py"], ["README"], ["notes.txt"], ["NOTES.rst"]):
        row = {
            "id": "x",
            "name": "x",
            "state": "implemented",
            "declared_in": bad,
            "code": ["tortoise/fanout.py"],
            "tests": ["tests/test_fanout_cap.py"],
        }
        errors = gate_errors([row])
        assert any("declaring document" in e for e in errors), (bad, errors)


def test_gate_fails_closed_on_a_path_outside_the_repo_via_symlink(tmp_path) -> None:
    """Containment is by RESOLVED path (cycle-5 P2).

    The repo carries committed symlinks (`scripts/`, `skills/` → agent-infra),
    so `is_file()` alone followed a declared path OUT of the repo.
    """
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    (repo / "docs" / "design.md").write_text("x\n")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_x.py").write_text("x\n")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "escaped.py").write_text("x = 1\n")
    (repo / "link").symlink_to(outside, target_is_directory=True)

    row = {
        "id": "x",
        "name": "x",
        "state": "implemented",
        "declared_in": ["docs/design.md"],
        "code": ["link/escaped.py"],
        "tests": ["tests/test_x.py"],
    }
    errors = gate_errors([row], root=repo)
    assert any("does not exist" in e for e in errors), errors


def test_gate_fails_closed_on_a_symlink_that_launders_the_kind(tmp_path) -> None:
    """A symlink's KIND is the TARGET's, not its name (cycle-7 P1).

    `fake_code.py -> docs/design.md` passed a name-only kind check, so a row
    could be `implemented` with no `.py` ever named — the existence probe
    resolved while the kind check did not.
    """
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    (repo / "docs" / "design.md").write_text("x\n")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_x.py").write_text("x\n")
    (repo / "src").mkdir()
    (repo / "src" / "impl.py").write_text("x = 1\n")
    (repo / "fake_code.py").symlink_to(repo / "docs" / "design.md")
    (repo / "test_fake.py").symlink_to(repo / "docs" / "design.md")
    (repo / "fake_doc.md").symlink_to(repo / "src" / "impl.py")

    base = {
        "id": "x",
        "name": "x",
        "state": "implemented",
        "declared_in": ["docs/design.md"],
        "code": ["src/impl.py"],
        "tests": ["tests/test_x.py"],
    }
    for field, bad in (
        ("code", ["fake_code.py"]),
        ("tests", ["test_fake.py"]),
        ("declared_in", ["fake_doc.md"]),
    ):
        row = dict(base)
        row[field] = bad
        errors = gate_errors([row], root=repo)
        assert errors, (field, bad, "the gate PASSED a kind-laundering symlink")


def test_gate_fails_closed_on_a_kind_valid_directory(tmp_path) -> None:
    """A DIRECTORY with a KIND-VALID name is still not an artifact (cycle-8 P2).

    The cycle-3 directory cases used kind-invalid names (`tortoise`, `tests`),
    so the kind check rejected them BEFORE the probe — leaving `is_file()`
    unpinned: mutating it to `exists()` survived the whole suite.
    """
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "real.py").write_text("x = 1\n")
    (repo / "src" / "mod.py").mkdir()  # a DIRECTORY named `mod.py`
    (repo / "docs").mkdir()
    (repo / "docs" / "real.md").write_text("x\n")
    (repo / "docs" / "policy.md").mkdir()  # a DIRECTORY named `policy.md`
    (repo / "tests").mkdir()
    (repo / "tests" / "test_real.py").write_text("x\n")
    (repo / "tests" / "test_dir.py").mkdir()  # a DIRECTORY named `test_dir.py`

    base = {
        "id": "x",
        "name": "x",
        "state": "implemented",
        "declared_in": ["docs/real.md"],
        "code": ["src/real.py"],
        "tests": ["tests/test_real.py"],
    }
    for field, bad in (
        ("declared_in", ["docs/policy.md"]),
        ("code", ["src/mod.py"]),
        ("tests", ["tests/test_dir.py"]),
    ):
        row = dict(base)
        row[field] = bad
        errors = gate_errors([row], root=repo)
        assert any("does not exist" in e for e in errors), (field, errors)


def test_gate_fails_closed_on_a_non_string_name() -> None:
    """`name` must be a non-blank string, like `id` (cycle-7 P2)."""
    for bad in (True, float("nan"), " ", 7, None):
        row = {
            "id": "x",
            "name": bad,
            "state": "not-implemented",
            "declared_in": ["config/v4-mechanisms.yml"],
            "tracking_issue": 5006,
        }
        errors = gate_errors([row])
        assert any("declares no name" in e for e in errors), (bad, errors)


def test_gate_pins_the_branches_the_ad_hoc_cases_do_not_reach() -> None:
    """Every branch of `gate_errors` is reached by a case (cycle-9 pre-empt).

    A branch no test reaches is a guard that cannot be SEEN to fail — the file's
    own rule. These are the branches the targeted cases above do not cover.
    """
    good = {
        "id": "x",
        "name": "x",
        "state": "implemented",
        "declared_in": ["config/v4-mechanisms.yml"],
        "code": ["tortoise/fanout.py"],
        "tests": ["tests/test_fanout_cap.py"],
    }
    cases = {
        "row is not a mapping": (["not a dict"], "not a mapping"),
        "id is not a string": ([{**good, "id": 1}], "no string id"),
        "id is empty": ([{**good, "id": ""}], "no string id"),
        "duplicate id": ([good, dict(good)], "duplicate id"),
        "declared_in missing": ([{**good, "declared_in": []}], "no declaring document"),
        "declared_in not a list": ([{**good, "declared_in": "x"}], "no declaring document"),
        "code missing": ([{**good, "code": []}], "no code path"),
        "tests missing": ([{**good, "tests": []}], "declares no test"),
        "code not a list": ([{**good, "code": {}}], "no code path"),
        "empty registry": ([], None),
    }
    for label, (mechs, expected) in cases.items():
        errors = gate_errors(mechs)
        if expected is None:
            # An empty registry is empty, not a violation — the CLOSED-SET tuple
            # pin is what refuses it (see the closed-set test), not this gate.
            assert errors == [], (label, errors)
            continue
        assert any(expected in e for e in errors), (label, expected, errors)


def test_gate_fails_closed_on_a_descriptive_state() -> None:
    """`descriptive` is NOT accepted until #5064 open question (c) settles.

    ⚠️ The valid `tracking_issue` is load-bearing: without it the row fails on
    the missing issue instead, so the test would pass for the wrong reason and
    not pin `STATES`. Adding `"descriptive"` to `STATES` survived until this
    case existed (cycle-11 P2).
    """
    errors = gate_errors(
        [
            {
                "id": "x",
                "name": "x",
                "declared_in": ["config/v4-mechanisms.yml"],
                "state": "descriptive",
                "tracking_issue": 5006,
            }
        ]
    )
    assert any("NEITHER" in e for e in errors), errors


if __name__ == "__main__":  # pragma: no cover - manual mutation aid
    raise SystemExit(pytest.main([__file__, "-q"]))
