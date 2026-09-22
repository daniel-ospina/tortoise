"""#2901 — ONE canonical terminal-status declaration; no reader re-declares a subset.

The defect: three readers hand-wrote a subset of the terminal Point statuses and
EACH subset omitted ``outdated``, so a Point whose status was ``outdated``
passed the "exclude non-current" filter and was served as the CURRENT
statement / missed by the audit / used as the dedup twin. That is the
supersession leg of objective 4: a superseded claim rendering as current.

The canonical declaration lives in ``tortoise/live.py`` (the leaf status
module — ``tortoise/sdk.py`` imports it). Nothing in this file re-declares the
vocabulary.

This file pins four things:

1. **The partition** (``TERMINAL_STATUS_VALUES == POINT_STATUS_VALUES -
   CURRENT_POINT_STATUS_VALUES``). A status added to the canonical vocabulary
   cannot be silently omitted from the terminal set — the parity test below is
   the trap, and it is a *derivation from* ``POINT_STATUS_VALUES``, not a
   second hand-written expectation. Consumers that call the canonical
   *predicate* inherit the derivation by construction.
2. **No reader re-declares a status literal** — an AST scan over the fixed
   consumers' executable string constants, so an inline ``IN [...]`` subset
   cannot reappear in a query body (the exact shape of the defect).
3. **Consumer parity** — each consumer either exposes the canonical set or
   calls the canonical predicate (``live._terminal_excluded`` /
   ``live._terminal_expression``); the one consumer that still holds a set
   (``volunteer``) must equal the canonical set.
4. **Behaviour, on both axes** — status ``outdated`` AND the legacy
   ``outdated=true`` flag (which ``invalidate_point`` writes without touching
   ``status``) are handled by every graph-backed consumer. A set that merely
   contains the right strings proves nothing.
"""
from __future__ import annotations

import ast
import importlib.util
import re
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise import live, volunteer
from tortoise.indexer import github_indexer
from tortoise.live import (
    CURRENT_POINT_STATUS_VALUES,
    LEGACY_NON_CURRENT_STATUS_VALUES,
    TERMINAL_EXCLUDED_STATUSES,
    TERMINAL_STATUS_VALUES,
    is_terminal_status,
)
from tortoise.sdk import POINT_STATUS_VALUES

_REPO_ROOT = Path(__file__).resolve().parent.parent
_GRAPH_SCRIPTS = _REPO_ROOT / "graph-scripts"


# ── graph-script loading (graph-scripts is not a package) ─────────────────

def _load_script(module_name: str, filename: str):
    spec = importlib.util.spec_from_file_location(
        module_name, _GRAPH_SCRIPTS / filename)
    assert spec is not None and spec.loader is not None, filename
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


AUDIT_BETA_GATE = _load_script("audit_beta_gate_2901", "audit_beta_gate.py")
DEDUP_OBSERVATION = _load_script(
    "dedup_observation_2901", "1714_dedup_observation.py")


# ── 1. the canonical partition ────────────────────────────────────────────

def test_terminal_set_is_the_vocabularys_terminal_half() -> None:
    """#2901 derivation: terminal == vocabulary minus current.

    ``live.py`` and ``sdk.py`` cannot import each other (sdk imports live), so
    the equality is *asserted here* rather than enforced by import-time
    coercion. This is the derivation — the expected value is computed from
    ``POINT_STATUS_VALUES``, never restated.
    """
    assert TERMINAL_STATUS_VALUES == POINT_STATUS_VALUES - CURRENT_POINT_STATUS_VALUES
    assert TERMINAL_STATUS_VALUES | CURRENT_POINT_STATUS_VALUES == POINT_STATUS_VALUES
    assert not (TERMINAL_STATUS_VALUES & CURRENT_POINT_STATUS_VALUES)


def test_every_vocabulary_member_is_classified() -> None:
    """A new status added to POINT_STATUS_VALUES without classification REDs here.

    This is the answer to "what prevents the NEXT status from being added to
    the vocabulary and forgotten": the partition must stay total, so a new
    member lands in exactly one of current/terminal — it can never be
    unclassified and silently treated as current. Consumers that call the
    canonical predicate then inherit the new member automatically.
    """
    classified = CURRENT_POINT_STATUS_VALUES | TERMINAL_STATUS_VALUES
    assert classified == POINT_STATUS_VALUES, (
        "vocabulary members not classified as current-or-terminal: "
        f"{sorted(POINT_STATUS_VALUES - classified)}"
    )


def test_outdated_is_terminal() -> None:
    assert "outdated" in TERMINAL_STATUS_VALUES
    assert "outdated" in TERMINAL_EXCLUDED_STATUSES
    assert is_terminal_status("outdated") is True


def test_exclusion_set_adds_only_the_legacy_deprecated_status() -> None:
    """#2901 inverse-shape ruling (see live.py).

    ``deprecated`` is NOT a legal create-time status: no SDK/API write path
    emits it (asserted in tests/test_lifecycle_guards.py), so the vocabulary is
    right to omit it. It IS present in legacy graphs and must never be served
    as current, so it joins the EXCLUSION set only.
    """
    assert TERMINAL_EXCLUDED_STATUSES == (
        TERMINAL_STATUS_VALUES | LEGACY_NON_CURRENT_STATUS_VALUES)
    assert {"deprecated"} == LEGACY_NON_CURRENT_STATUS_VALUES
    assert "deprecated" not in POINT_STATUS_VALUES
    assert is_terminal_status("deprecated") is True


# ── 2. no reader re-declares a status literal ─────────────────────────────

#: The consumers fixed by #2901. Each must call the canonical predicate (or,
#: for ``volunteer``, expose the canonical set) — never an inline subset.
_PREDICATE_CONSUMERS = {
    "tortoise/indexer/github_indexer.py": (
        _REPO_ROOT / "tortoise" / "indexer" / "github_indexer.py"),
    "graph-scripts/audit_beta_gate.py":
        _GRAPH_SCRIPTS / "audit_beta_gate.py",
    "graph-scripts/1714_dedup_observation.py":
        _GRAPH_SCRIPTS / "1714_dedup_observation.py",
}


def _executable_string_constants(path: Path) -> list[tuple[int, str]]:
    """String constants a module can put into a query — docstrings excluded.

    A re-declared status subset is a *code* literal; a prose mention in a
    docstring is not. Distinguishing them by AST (rather than by line-splitting
    on ``#``) makes the guard immune to formatting: a collection rendered one
    literal per line is still caught.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            body = getattr(node, "body", [])
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                docstrings.add(id(body[0].value))
    return [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


@pytest.mark.parametrize("label", sorted(_PREDICATE_CONSUMERS))
def test_consumer_has_no_terminal_status_string_literal(label: str) -> None:
    """The defect shape — an inline status list in a query — cannot reappear.

    The literal is matched *inside* a larger constant too: the original defect
    was a status list embedded in a Cypher string
    (``"... IN ['retracted', 'superseded']"``), which an equality check against
    the bare status would miss. Both shapes are covered — a BARE status
    constant (a module-level tuple) and a status quoted inside a larger
    constant — because either can be fed to a query.
    """
    statuses = "|".join(sorted(TERMINAL_EXCLUDED_STATUSES))
    literal = re.compile(rf"""['"](?:{statuses})['"]""")
    offenders = [
        (lineno, value)
        for lineno, value in _executable_string_constants(_PREDICATE_CONSUMERS[label])
        # A bare status constant (``TERMINAL_STATUSES = ("retracted", ...)``)
        # decodes to the status itself; a status inside a larger Cypher
        # constant (``"... IN ['retracted', ...]"``) keeps its quotes.
        if value in TERMINAL_EXCLUDED_STATUSES or literal.search(value)
    ]
    assert not offenders, (
        f"{label}: terminal-status literal(s) in executable code {offenders} — "
        "call live._terminal_excluded / live._terminal_expression instead"
    )


@pytest.mark.parametrize("label", sorted(_PREDICATE_CONSUMERS))
def test_consumer_calls_the_canonical_predicate(label: str) -> None:
    """The consumer must be bound to live.py, not to a private copy."""
    source = _PREDICATE_CONSUMERS[label].read_text(encoding="utf-8")
    assert "from tortoise.live import" in source, label
    assert ("_terminal_excluded" in source
            or "_terminal_expression" in source), label


# ── 3. consumer parity ────────────────────────────────────────────────────

def test_volunteer_current_view_set_is_the_canonical_set() -> None:
    """The one fixed consumer that still holds a set must equal the canonical one.

    ``volunteer.CURRENT_VIEW_EXCLUDED_STATUS`` gates which candidates may be
    served as the current belief; the pre-fix subset omitted ``outdated`` and
    ``archived``.
    """
    assert set(volunteer.CURRENT_VIEW_EXCLUDED_STATUS) == set(TERMINAL_EXCLUDED_STATUSES)
    assert "outdated" in volunteer.CURRENT_VIEW_EXCLUDED_STATUS


# ── 4. behaviour (graph-backed; runs on the configured lane) ───────────────

@pytest.fixture
def sdk(sdk_factory):
    """A fresh SDK on the configured lane (docker when TORTOISE_DB_URI is set,
    embedded on the carve-out lane). Never embeds while a URI is present."""
    s = sdk_factory()
    yield s
    s.close()


def _mk_point(sdk, content: str, *, status: str | None = None, **props) -> str:
    pid = sdk.create_point("statement", content)["id"]
    sets, params = [], {"id": pid}
    if status is not None:
        sets.append("n.status=$st")
        params["st"] = status
    for key, value in props.items():
        sets.append(f"n.{key}=${key}")
        params[key] = value
    if sets:
        sdk._get_proj().g.query(
            f"MATCH (n:Point {{id:$id}}) SET {', '.join(sets)}", params=params)
    return pid


def test_github_indexer_probe_excludes_outdated(sdk) -> None:
    """The load-bearing consumer: a non-current point must never resolve as the
    current statement for its externalId — on EITHER axis."""
    eid = f"github:issue:acme/repo#{uuid.uuid4().hex[:8]}"
    _mk_point(sdk, "outdated statement", status="outdated", externalId=eid)
    assert github_indexer._current_statement_rows(sdk._get_proj(), eid) == []

    # Axis 2: ``invalidate_point`` sets the legacy flag WITHOUT touching status.
    flag_eid = f"github:issue:acme/repo#{uuid.uuid4().hex[:8]}"
    _mk_point(sdk, "flag-invalidated statement", status="live",
              outdated=True, externalId=flag_eid)
    assert github_indexer._current_statement_rows(sdk._get_proj(), flag_eid) == []

    # Control: a genuinely current statement for the SAME externalId IS returned
    # (the probe is not trivially empty).
    live_id = _mk_point(sdk, "current statement", status="live", externalId=eid)
    rows = github_indexer._current_statement_rows(sdk._get_proj(), eid)
    assert [r[0] for r in rows] == [live_id]


def _nand_onto(sdk, target: str) -> str:
    """Create a NAND operator Point with a NAND edge onto ``target``."""
    proj = sdk._get_proj()
    op = f"nand-op-{uuid.uuid4().hex[:8]}"
    proj.g.query(
        "CREATE (n:Point {id:$op, is_operator:true, op_type:'NAND'})",
        params={"op": op})
    proj.g.query(
        "MATCH (n:Point {id:$op}), (t:Point {id:$tgt}) CREATE (n)-[:NAND]->(t)",
        params={"op": op, "tgt": target})
    return op


def test_audit_beta_gate_dead_target_scan_finds_non_current(sdk) -> None:
    """The audit consumes the canonical predicate in the POSITIVE direction: a
    NAND onto a non-current target IS a dangling attack and must be reported.

    (The pre-fix inline subset omitted ``outdated``, so this attack was
    invisible to the beta gate — the same #2901 omission, caught here rather
    than in the report.)
    """
    proj = sdk._get_proj()
    dead = {}

    tgt = _mk_point(sdk, "outdated attack target", status="outdated")
    _nand_onto(sdk, tgt)
    dead = {r["to"] for r in AUDIT_BETA_GATE._nand_to_dead(proj)}
    assert tgt in dead

    # Axis 2: the legacy flag on an otherwise-live point.
    flag_tgt = _mk_point(sdk, "flag-dead attack target", status="live",
                         outdated=True)
    _nand_onto(sdk, flag_tgt)
    dead = {r["to"] for r in AUDIT_BETA_GATE._nand_to_dead(proj)}
    assert tgt in dead and flag_tgt in dead

    # Control: a LIVE target is not a dead target — never reported.
    live_tgt = _mk_point(sdk, "live target", status="live")
    _nand_onto(sdk, live_tgt)
    assert live_tgt not in {r["to"] for r in AUDIT_BETA_GATE._nand_to_dead(proj)}


def test_dedup_observation_ignores_non_current_statement_twin(sdk) -> None:
    """A non-current keyed statement is NOT a current twin for a legacy
    observation — on either axis."""
    url = f"https://github.com/acme/repo/issues/{uuid.uuid4().hex[:8]}"
    _mk_point(sdk, "legacy observation", pointKind="observation", github_url=url)
    _mk_point(sdk, "outdated keyed statement", status="outdated",
              pointKind="statement", github_url=url)
    assert DEDUP_OBSERVATION.scan_observation_duplicates(sdk._get_proj()) == {}

    # Axis 2: the legacy flag on an otherwise-live statement.
    flag_url = f"https://github.com/acme/repo/issues/{uuid.uuid4().hex[:8]}"
    _mk_point(sdk, "legacy observation", pointKind="observation",
              github_url=flag_url)
    _mk_point(sdk, "flag-invalidated keyed statement", status="live",
              outdated=True, pointKind="statement", github_url=flag_url)
    assert DEDUP_OBSERVATION.scan_observation_duplicates(sdk._get_proj()) == {}

    # Control: a live keyed twin IS found.
    live_twin = _mk_point(sdk, "current keyed statement", status="live",
                          pointKind="statement", github_url=url)
    pairs = DEDUP_OBSERVATION.scan_observation_duplicates(sdk._get_proj())
    assert url in pairs
    assert live_twin in pairs[url]["statements"]


def test_live_predicate_and_set_agree_on_both_axes() -> None:
    """The canonical predicate is the union of the set and the legacy flag.

    Consumers were routed to the PREDICATE (not the bare set) so this is the
    contract they rely on.
    """
    for status in TERMINAL_EXCLUDED_STATUSES:
        assert live.is_terminal_status(status) is True
        assert live.is_terminal_status(status, True) is True
    for status in CURRENT_POINT_STATUS_VALUES:
        assert live.is_terminal_status(status) is False
        assert live.is_terminal_status(status, True) is True  # legacy flag
    assert live.is_terminal_status(None) is False  # legacy/absent == live
