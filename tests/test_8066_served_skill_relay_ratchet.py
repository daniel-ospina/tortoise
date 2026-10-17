"""#8066: a SERVED skill's test subject is the tracked copy, never the relay.

`skills` and `scripts` are tracked symlinks (mode 120000) whose committed blobs
are ABSOLUTE paths in one developer's home
(`/Users/danielospina/Documents/GitHub/agent-infra/{skills,scripts}`). They
dangle everywhere else, so `read skills/commit-workflow/SKILL.md` is ENOENT
and `node scripts/check-doc-affiliation.cjs` cannot run.

The generalisable defect the issue's comment thread registers is NOT "a
symlink is dead" — it is that a gate whose SUBJECT is resolved through that
path has NO RESULT on the CI path while its green reads as a pass. The
measured instance was `tests/test_ingest_conformance.py`, retargeted off the
relay onto the served copy in #8499; the second was
`tests/test_website_static.py::test_tortoise_decide_skill_ships_the_workflow`,
retargeted by #8066 itself.

For a SERVED skill the relay is never the right subject even where it
resolves: the product bytes are tracked in THIS repo under `SERVED`, and an
agent-infra checkout is an unrelated tree — measured 2026-10-16,
`/Users/home/agent-infra/skills` carries `how-to-use-tortoise`,
`tortoise-file-finding` and `tortoise-verify-chain` and does NOT carry
`tortoise-decide` at all, so the old gate skipped on a live agent-infra too.
This ratchet keeps the idiom from returning.

It is deliberately NOT a general relay lint. `tests/test_pytest_attribution.py`
and `tests/test_merge_throughput.py` read the relay ON PURPOSE: they try
`$AGENT_INFRA_PATH` first and ANNOUNCE the skip when the rail is absent
(the repo does not own that tree — python-ci.yml is self-contained BY DECISION,
fix #555). A blanket rule would red those legitimate designs, which is why
this one is scoped to the served names, where the tracked copy exists by
construction and the relay can never be correct.

It lives in its OWN static file because its SUBJECT is every `tests/**/*.py`:
the ratchet originally sat in `tests/test_served_skills.py`, which is
registered under `onboarding` alone, and a change to a `core`-owned test file
(the sharp case being `tests/test_ingest_conformance.py`, the very file #8499
retargeted) selects `core` and never ran it — a guard whose subject is
`tests/**` but whose registration is one arm is asleep on exactly the diff
class it exists to catch (the #1349/#2938 silent-drop class). A change to a
`tests/**.py` file selects `core`, EXCEPT the two under `tests/` that are exact
non-core SOURCE_PATTERNS entries (`tests/_html_links.py`,
`tests/test_embedded_concurrency.py`), where a named-surface match REPLACES the
`core` fallback in `select()` (#2938) and selects `onboarding`. This file is
registered on BOTH surfaces, and
`test_8066_relay_ratchet_is_registered_on_every_surface_it_covers` enumerates
every in-scope file and pins the property rather than the declaration.

DETECTOR BOUND (deliberate): the rule reaches LITERAL path-segment chains
only. `ROOT / "skills" / name / "SKILL.md"` (a loop variable),
`Path(f"skills/{name}/SKILL.md")` and `Path("skills/" + name + "/SKILL.md")`
contribute nothing, because a non-literal operand has no segment to resolve.
Both spellings the repo actually uses — the `Path("skills/<name>/…")` literal
and the `ROOT / "skills" / "<name>" / …` chain — are caught. A dynamic name is
outside the reach of a static rule that must stay free of false positives;
naming the bound here keeps the claim and the mechanism aligned.
"""
from __future__ import annotations

import ast
import os
import sys
import warnings
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

SERVED = _REPO_ROOT / "website" / "apps" / "dashboard" / "public" / "skills"


def _served_skill_dirs() -> list[str]:
    return sorted(
        e.name for e in SERVED.iterdir()
        if (e / "SKILL.md").is_file())


def _read(path: str | Path) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _relay_segments(node: ast.AST) -> list[str]:
    """The literal path segments of a `/`-chain or a `join`/`Path` argument.

    `ROOT / "skills" / "tortoise-decide" / "SKILL.md"` and
    `"skills/tortoise-decide/SKILL.md"` both normalise to
    `["skills", "tortoise-decide", "SKILL.md"]`, so the two spellings the repo
    actually uses are detected by one rule. A non-literal operand contributes
    nothing, which is what keeps `REPO / ".github" / "scripts"` out of the way.
    """
    segs: list[str] = []
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        segs = _relay_segments(node.left) + _relay_segments(node.right)
    elif isinstance(node, ast.Constant) and isinstance(node.value, str):
        segs = [node.value]
    elif isinstance(node, ast.Call):
        callee = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if callee in ("Path", "join", "open"):
            for arg in node.args:
                segs.extend(_relay_segments(arg))
    return [p for seg in segs for p in seg.split("/") if p]


def _relay_relative_subject(source: str, names: tuple[str, ...]) -> list[str]:
    """The served names `source` reads as `skills/<name>/…` — the relay idiom.

    The `"skills" in source` line is a NECESSARY-condition fast path, not a
    separate rule: a hit requires a segment equal to `skills`, so a source that
    does not contain that word cannot hit. It matters because the caller parses
    every `tests/**/*.py` (1097 files at head, 18 carrying the word), and an
    unconditional `ast.parse` per file cost ~16s on the merge gate.
    """
    if "skills" not in source:
        return []
    try:
        # Several test files carry a literal backslash-backtick in a docstring; the
        # SyntaxWarning that raises is about the TESTED file, not about this
        # detector, and printing it once per run would be noise attributed here.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)
            tree = ast.parse(source)
    except SyntaxError:
        return []
    found = set()
    for node in ast.walk(tree):
        segs = _relay_segments(node)
        if len(segs) >= 2 and segs[0] == "skills" and segs[1] in names:
            found.add(segs[1])
    return sorted(found)


def test_8066_no_served_skill_is_a_test_subject_through_the_dead_relay():
    """#8066: no test under `tests/` resolves a SERVED skill through `skills/`.

    ⛔ The detector is itself asserted on a positive control first: a ratchet
    that cannot fire is the same vacuity this issue is about — a guard whose
    subject never resolves reads as green.

    The rule is bounded to LITERAL path segments (see the module docstring);
    the positive control is the measured idiom, not a claim of completeness.
    """
    names = tuple(_served_skill_dirs())
    assert names, "no served skills found — the ratchet would be vacuous"
    control = 'X = ROOT / "skills" / "tortoise-decide" / "SKILL.md"\n'
    assert _relay_relative_subject(
        control, names) == ["tortoise-decide"], (
        "the relay detector does not fire on the measured idiom — it would pass "
        "vacuously")

    offenders: dict[str, list[str]] = {}
    for dirpath, _dirnames, filenames in os.walk(_REPO_ROOT / "tests"):
        for filename in filenames:
            if not filename.endswith(".py"):
                continue
            path = Path(dirpath) / filename
            hit = _relay_relative_subject(_read(path), names)
            if hit:
                offenders[path.relative_to(_REPO_ROOT).as_posix()] = hit
    assert not offenders, (
        "test(s) resolve a SERVED skill through the machine-local `skills` relay "
        "(#8066): the link dangles off the machine that committed it, so the gate "
        "has no result on CI and grades an unrelated checkout where it does "
        f"resolve. Read the tracked served copy under "
        f"{SERVED.relative_to(_REPO_ROOT).as_posix()}/ instead: {offenders}")


def test_8066_relay_ratchet_is_registered_on_every_surface_it_covers():
    """#8066: a ratchet wider than its registration is blind where it matters.

    The ratchet's SUBJECT is every `tests/**/*.py`, and a change to such a file
    selects the surface that file owns. This pins the PROPERTY, not the
    declaration: it enumerates EVERY in-scope file and requires that a PR
    changing that file alone actually puts this guard in the run set (directly,
    or via a `full` selection, which runs everything).

    Measured before the fix: the ratchet lived in `tests/test_served_skills.py`
    (registered under `onboarding` alone), so
    `tests/test_ingest_conformance.py -> surfaces=['core']` did not run it —
    the exact file #8499 retargeted off the relay. An arm on `core` is what the
    enumeration below demanded; `onboarding` covers the two exact non-core
    SOURCE_PATTERNS entries under `tests/`.
    """
    from tools.ci_selection import load_manifest, select

    manifest = load_manifest()
    guard = Path(__file__).name
    covered = [
        p.relative_to(_REPO_ROOT).as_posix()
        for p in sorted((_REPO_ROOT / "tests").rglob("*.py"))
    ]
    # A collapsed enumeration would make the loop below vacuous — the failure
    # mode a green ratchet can hide.
    assert len(covered) > 100, (
        f"the ratchet's subject enumerated only {len(covered)} file(s) — a "
        "green run here would prove nothing")
    blind = []
    for path in covered:
        selection = select([path], "pull_request", manifest)
        if selection.get("full"):
            continue
        if guard not in (selection.get("test_files") or []):
            blind.append(f"{path} -> {selection['surfaces']}")
    assert not blind, (
        f"{len(blind)} file(s) under the ratchet's subject do NOT run {guard} "
        "on a change to them — the ratchet would be blind to a relay read in "
        "them (#8066, the #1349/#2938 silent-drop class):\n  "
        + "\n  ".join(blind))
