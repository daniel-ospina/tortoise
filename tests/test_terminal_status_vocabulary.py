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
from tortoise.commit_ops import OBJECT_TERMINAL_STATUSES
from tortoise.indexer import github_indexer
from tortoise.live import (
    CURRENT_POINT_STATUS_VALUES,
    LEGACY_NON_CURRENT_STATUS_VALUES,
    TERMINAL_EXCLUDED_STATUSES,
    TERMINAL_STATUS_VALUES,
    is_terminal_status,
)
from tortoise.sdk import POINT_STATUS_VALUES, TortoiseSDK

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
    # #2500: the pre-#2490 terminal-EP backfill is a new predicate consumer —
    # registering it here keeps the #2901 no-literal guard on it (it composes
    # its MATCH from live._terminal_expression, so it has no status literal).
    "graph-scripts/2500_backfill_terminal_ep_vacuity.py":
        _GRAPH_SCRIPTS / "2500_backfill_terminal_ep_vacuity.py",
}


def _executable_string_constants_from_source(
        source: str) -> list[tuple[int, str]]:
    """String constants a module can put into a query — docstrings excluded.

    A re-declared status subset is a *code* literal; a prose mention in a
    docstring is not. Distinguishing them by AST (rather than by line-splitting
    on ``#``) makes the guard immune to formatting: a collection rendered one
    literal per line is still caught.

    Takes SOURCE (not a path) so a guard can be exercised against a synthetic
    snippet — the detector's own coverage is then pinned, not just its run
    over one file.
    """
    tree = ast.parse(source)
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


def _executable_string_constants(path: Path) -> list[tuple[int, str]]:
    """``_executable_string_constants_from_source`` over a file on disk."""
    return _executable_string_constants_from_source(
        path.read_text(encoding="utf-8"))


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


# ── 5. the OBJECT vocabulary is BOUND to the Point one (#3308) ───────────────

#: ``archived`` is excluded from the state view's exclude-set on purpose: an
#: archived Object stays VISIBLE unless ``include_superseded`` asks for the
#: rest (#1350 decision 2a). Named here so the derivation below is readable.
_ARCHIVED_VISIBLE_ON_READ = frozenset({"archived"})


def test_object_vocabulary_is_a_subset_of_the_point_vocabulary() -> None:
    """#3308: a reader filtering OBJECT hits with the POINT set is safe IFF the
    Object vocabulary stays inside it.

    The defect this pins: such a reader is *correct today only because*
    ``OBJECT_TERMINAL_STATUSES`` happens to be a subset of
    ``TERMINAL_EXCLUDED_STATUSES``. That is an accident of current membership,
    not a stated contract. Add a status to the Object family without adding it
    to the Point family and that reader silently serves a non-current Object as
    current. The two declarations live in different modules with different
    consumers, and nothing else binds them — this assertion IS the binding.
    """
    leaked = OBJECT_TERMINAL_STATUSES - TERMINAL_EXCLUDED_STATUSES
    assert not leaked, (
        "the OBJECT vocabulary has a member the POINT set does not exclude, so "
        "a reader parameterised by the Point set would serve it as current: "
        f"{sorted(leaked)}"
    )


def test_read_surface_exclude_set_is_derived_from_the_object_vocabulary() -> None:
    """#3308: ``TortoiseSDK.STATE_EXCLUDED_STATUS`` must not drift from the
    canonical OBJECT declaration.

    It is DERIVED in ``sdk.py`` (#4058: ``TERMINAL_EXCLUDED_STATUSES -
    {"outdated", "archived"}``), not a hand-written literal, and the read
    surface's own comment forbids re-literalising a vocabulary ("a copy is how
    the read surface drifts from search"). The derived set is deliberately the
    Object set MINUS ``archived``, so the expectation is *computed* from
    ``OBJECT_TERMINAL_STATUSES`` — never restated, so re-typing it cannot pass.
    """
    expected = OBJECT_TERMINAL_STATUSES - _ARCHIVED_VISIBLE_ON_READ
    assert expected == TortoiseSDK.STATE_EXCLUDED_STATUS, (
        "STATE_EXCLUDED_STATUS drifted from the canonical OBJECT vocabulary, so "
        "the read surface now excludes a different set than search: "
        f"only-in-SDK={sorted(TortoiseSDK.STATE_EXCLUDED_STATUS - expected)}, "
        f"only-in-canonical={sorted(expected - TortoiseSDK.STATE_EXCLUDED_STATUS)}"
    )


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


# ── 5. #7075 — builder is the keep-filter's exact negation, on every value ─

#: Values a hand-edited / pre-#2491 graph can actually hold on ``outdated``.
#: The dual invariant must hold for every one of them, including the values
#: where it does not coincide with the Python mirror's ``bool()`` — the
#: divergence is stated by mechanism on ``_terminal_expression`` and exercised
#: by ``_FALSY_NON_BOOLEAN_RESIDUALS`` below.
_OUTDATED_PROBE_VALUES = [True, False, None, 1, 0, 2, "true", "false", ""]


def _terminal_and_keep(proj, pid: str) -> tuple:
    """``(terminal?, keep-filter-kept?)`` for one node id, from the engine."""
    rows = proj.g.query(
        "MATCH (n:Point {id:$id}) RETURN "
        f"{live._terminal_expression('n')} AS terminal, "
        f"{live._terminal_excluded('n.status')} AS kept",
        params={"id": pid},
    ).result_set
    assert rows, pid
    return rows[0][0], rows[0][1]


def test_terminal_expression_is_the_keep_filters_exact_negation(sdk) -> None:
    """#7075 indicator 2: ``_terminal_expression == NOT _terminal_excluded``.

    They are the positive/negative halves of ONE terminal vocabulary, and the
    builder's docstring has always claimed its partner's bare negation is safe
    to use. Before #7075 the flag half was ``= true``, which is not the
    negation of ``= false`` under FalkorDB's strict type comparison, so a
    non-boolean ``outdated`` made a node BOTH not-terminal and not-live — an
    excluded middle. Asserted against the live engine, on a live-status
    Point, for every probe value (including numeric ``0`` and the empty string
    ``""``, the examples of the falsy non-boolean divergence).
    """
    proj = sdk._get_proj()
    for value in _OUTDATED_PROBE_VALUES:
        pid = _mk_point(sdk, f"dual {value!r}", status="live", outdated=value)
        terminal, kept = _terminal_and_keep(proj, pid)
        assert terminal is not kept, (
            f"excluded middle for outdated={value!r}: builder={terminal}, "
            f"keep-filter={kept} — a value cannot be both non-terminal and "
            "non-live"
        )


def test_boolean_and_null_flag_semantics_are_unchanged(sdk) -> None:
    """``= true`` -> ``<> false`` is a NO-OP on the contract domain.

    Every in-repo writer emits a boolean (the fold enforces the type), so the
    #7075 change must not move any in-contract graph: booleans and NULL read
    exactly as the old expression read them.
    """
    proj = sdk._get_proj()
    for value, expected in ((True, True), (False, False), (None, False)):
        pid = _mk_point(sdk, f"bool {value!r}", status="live", outdated=value)
        rows = proj.g.query(
            "MATCH (n:Point {id:$id}) RETURN "
            f"{live._terminal_expression('n')}, "
            "coalesce(n.outdated, false) = true, "
            f"{live._terminal_excluded('n.status')}",
            params={"id": pid},
        ).result_set
        terminal, legacy_form, kept = rows[0]
        assert terminal is expected, value
        assert terminal == legacy_form, (
            f"in-contract boolean {value!r} changed meaning: "
            f"new={terminal}, pre-#7075 form={legacy_form}"
        )
        assert kept is (not expected), value


@pytest.mark.parametrize("value", [1, "true", "false"])
def test_builder_and_python_mirror_agree_on_non_boolean_flags(sdk, value) -> None:
    """#7075 indicator 1: on a live-status Point, the Cypher builder and its
    Python mirror return the same answer for a non-boolean ``outdated``.

    All three values are truthy in Python and now terminal in Cypher, so the
    Phase-1 dedup guard and its two Phase-2 counterparts agree on them.
    """
    pid = _mk_point(sdk, f"mirror {value!r}", status="live", outdated=value)
    terminal, _kept = _terminal_and_keep(sdk._get_proj(), pid)
    mirror = is_terminal_status("live", value)
    assert terminal is True and mirror is True, (
        f"Cypher={terminal} but Python mirror={mirror} for outdated={value!r}"
    )


#: Falsy non-boolean values where the two predicates diverge (the residual the
#: docstrings state by mechanism, these being the practical examples): the
#: builder (and ``_terminal_excluded``) read them as the ``<> false`` half
#: (terminal), while the Python mirror's ``bool()`` reads them live.
_FALSY_NON_BOOLEAN_RESIDUALS = [0, 0.0, ""]


@pytest.mark.parametrize("value", _FALSY_NON_BOOLEAN_RESIDUALS)
def test_documented_residual_class_is_falsy_non_boolean(sdk, value) -> None:
    """The docstrings' residual, made executable: for each falsy non-boolean
    value probed here the Cypher builder calls the node terminal while the
    Python mirror calls it live.

    It pins the example values ``0``/``0.0``/``""`` — the pre-fix prose named
    only numeric ``0`` and omitted the empty string. Fails on the pre-fix
    ``= true`` builder, where such a node read NOT terminal and was separately
    non-live (the excluded middle this PR removes).
    """
    pid = _mk_point(sdk, f"residual {value!r}", status="live", outdated=value)
    terminal, kept = _terminal_and_keep(sdk._get_proj(), pid)
    mirror = is_terminal_status("live", value)
    assert terminal is True, (
        f"Cypher builder should read falsy non-boolean {value!r} as terminal "
        f"(keep-filter dual); got {terminal}"
    )
    assert kept is False, value  # the keep-filter agrees it is terminal
    assert mirror is False, (
        f"Python mirror should still read {value!r} live (the documented "
        f"residual); got {mirror}"
    )


def test_python_mirror_truthiness_of_non_boolean_flags() -> None:
    """The Python half of indicator 1, without a graph: the mirror's own
    answers on the probe set — the contract the Cypher half must meet wherever
    Python truthiness is expressible in Cypher."""
    assert is_terminal_status("live", 1) is True
    assert is_terminal_status("live", "true") is True
    assert is_terminal_status("live", "false") is True  # non-empty string
    assert is_terminal_status("live", 0) is False  # falsy residual: numeric
    assert is_terminal_status("live", "") is False  # falsy residual: empty str
    assert is_terminal_status("live", None) is False


# ── 6. #4058 — the remaining sdk.py re-declarations are bound to the ────────
#          canonical sets (the #2901 mirror: a reader re-deriving its own
#          subset, and the allow-list omitting a future current status).

def test_stale_terminal_statuses_is_the_canonical_point_exclusion() -> None:
    """#4058 residual 1 — ``review_connections(mode=prune)``'s staleness set.

    It used to be a by-value copy of ``TERMINAL_STATUS_VALUES``, so it omitted
    the legacy ``deprecated``: a connection to a deprecated Point was not
    flagged stale although every read surface excluded that Point. It must be
    the canonical exclusion itself (not merely equal to it) so the
    ``deprecated`` member cannot drift out again.
    """
    from tortoise.sdk import STALE_TERMINAL_STATUSES

    assert STALE_TERMINAL_STATUSES is TERMINAL_EXCLUDED_STATUSES
    assert "deprecated" in STALE_TERMINAL_STATUSES
    assert is_terminal_status("deprecated") is True


def test_state_excluded_status_cannot_name_a_current_point_status() -> None:
    """#4058 residual 2 — the state view's additional filter.

    It is a NARROWING of the canonical Point exclusion, so it may never name a
    status the vocabulary calls CURRENT (that would hide a live claim), and
    every member it omits must be a Point-vocabulary TERMINAL status — i.e.
    one the canonical retrieval layer excludes before this narrower filter is
    ever applied. Anything else would be a status the state view silently
    leaks.
    """
    excluded = TortoiseSDK.STATE_EXCLUDED_STATUS
    assert excluded <= TERMINAL_EXCLUDED_STATUSES, (
        "the state view excludes a status the canonical Point exclusion does "
        f"not: {sorted(excluded - TERMINAL_EXCLUDED_STATUSES)}"
    )
    assert not (excluded & CURRENT_POINT_STATUS_VALUES), (
        "the state view would hide a CURRENT status: "
        f"{sorted(excluded & CURRENT_POINT_STATUS_VALUES)}"
    )
    assert (TERMINAL_EXCLUDED_STATUSES - excluded) <= TERMINAL_STATUS_VALUES
    # The legacy member #2901 ruled must be excluded is still excluded.
    assert "deprecated" in excluded


def test_sdk_admits_every_current_status_through_the_canonical_builder() -> None:
    """#4058 residual 3 — the allow-list half of the partition.

    Three Point pools in ``sdk.py`` hand-wrote ``IN ['draft', 'live']``, so a
    status added to ``POINT_STATUS_VALUES`` and classified current by the
    parity test above would have been silently DROPPED by them. The canonical
    builder must admit exactly the current vocabulary (and, being an
    allow-list, drop an unknown status).
    """
    from tortoise.live import _current_status_clause

    clause = _current_status_clause("x.status")
    admitted = set(re.findall(r"'([^']+)'", clause))
    assert admitted == set(CURRENT_POINT_STATUS_VALUES)
    assert "outdated" not in admitted
    assert is_terminal_status("outdated") is True
    # An absent status is current (legacy nodes) — the allow-list keeps the
    # NULL branch the hand-written literal carried.
    assert "x.status IS NULL" in clause


def test_current_status_clause_derives_from_the_canonical_vocabulary() -> None:
    """#8662 P3 — pin the builder's DERIVATION, not only its rendered value.

    ``_current_status_clause`` renders the identical string whether it iterates
    ``CURRENT_POINT_STATUS_VALUES`` or hardcodes the two members, so the value
    assertion above cannot see a re-literalisation of ``live.py`` — the one file
    that is supposed to be the single source of truth. Mirror the
    ``STATE_EXCLUDED_STATUS`` derivation pin: the body must ITERATE the
    canonical vocabulary, so a hand-written allow-list REDs even when its value
    happens to match today.
    """
    source = (_REPO_ROOT / "tortoise" / "live.py").read_text(encoding="utf-8")
    fn = next((n for n in ast.walk(ast.parse(source))
               if isinstance(n, ast.FunctionDef)
               and n.name == "_current_status_clause"), None)
    assert fn is not None, "_current_status_clause not found in live.py"
    derives = any(
        any(isinstance(n, ast.Name) and n.id == "CURRENT_POINT_STATUS_VALUES"
            for n in ast.walk(comp.iter))
        for comp in ast.walk(fn) if isinstance(comp, ast.comprehension)
    )
    assert derives, (
        "_current_status_clause must DERIVE from CURRENT_POINT_STATUS_VALUES "
        "(iterate it), not re-literalise the allow-list — a hand-written list "
        "drops a status added to the vocabulary: "
        f"{ast.unparse(fn)[:200]}"
    )


# An inline `IN [...]` list — its member tokens are extracted from the span, so
# order, quote style, whitespace and a wrapping `(...)` cannot evade the guard
# (the review of #8662 measured the old single-spelling regex being bypassed by
# `IN ['live', 'draft']`, `IN ["draft", "live"]` and `IN ['draft','live']`).
_INLINE_LIST = re.compile(r"\bIN\s*\(?\s*\[([^\]]*)\]", re.IGNORECASE)
_QUOTED_TOKEN = re.compile(r"""['\"]([^'\"]+)['\"]""")


def _current_status_allow_list_redeclarations(
        source: str) -> list[tuple[int, str, list[str]]]:
    """Inline ``IN [...]`` enumerations of the FULL CURRENT Point vocabulary.

    The #2901-mirror shape is an inline allow-list that names EVERY current
    status; the canonical ``live._current_status_clause`` owns it now.
    Detection is formatting-INSENSITIVE by construction: the list is located as
    a span and its quoted tokens extracted, so member order, quote style,
    whitespace and a wrapping ``(...)`` cannot hide a re-declaration.

    The polarity is SUPERSET-of-the-canonical-vocabulary, deliberately: the
    defect is a literal that enumerates ALL current statuses, so a status later
    added to the vocabulary is silently dropped. A list naming a PROPER SUBSET
    (``IN ['live']`` — published-only; ``IN ['draft']`` — drafts-only) is a
    different, legitimate question the canonical builder does NOT answer, so it
    must not be flagged — and neither is a list whose members are not statuses
    (``IN ['Point', 'Object']``).

    Only executable (non-docstring) string constants are scanned, and only a
    list literal contained in ONE constant — a list split across an f-string
    interpolation is outside this guard's scope (named, not silently claimed).
    """
    canonical = set(CURRENT_POINT_STATUS_VALUES)
    offenders: list[tuple[int, str, list[str]]] = []
    for lineno, value in _executable_string_constants_from_source(source):
        for match in _INLINE_LIST.finditer(value):
            members = _QUOTED_TOKEN.findall(match.group(1))
            if canonical <= set(members):
                offenders.append((lineno, value, members))
    return offenders


def test_no_sdk_query_re_declares_the_current_status_allow_list() -> None:
    """The #2901-mirror shape cannot reappear in ``sdk.py`` in any spelling.

    An inline ``IN [...]`` that enumerates EVERY current status is the
    allow-list re-declaration #4058 removes — it drops a future current status
    with no edit anywhere. Only the canonical builder (which renders the
    literal) may contain it. The scan is formatting-insensitive (order, quote
    style, whitespace, a wrapping ``(...)``), not a single-spelling line regex:
    the review of #8662 measured the old regex missing ``IN ['live', 'draft']``,
    ``IN ["draft", "live"]`` and ``IN ['draft','live']``. A list naming a
    PROPER SUBSET of the vocabulary (``IN ['live']``) is a different, legitimate
    question and is not flagged.
    """
    source = (_REPO_ROOT / "tortoise" / "sdk.py").read_text(encoding="utf-8")
    offenders = _current_status_allow_list_redeclarations(source)
    assert not offenders, (
        "sdk.py re-declares the current-status allow-list: call "
        f"live._current_status_clause instead — {offenders}"
    )


@pytest.mark.parametrize("literal", [
    "IN ['draft', 'live']",          # the pre-fix spelling
    "IN ['live', 'draft']",          # order differs
    'IN ["draft", "live"]',          # double quotes
    "IN ['draft','live']",           # no space after the comma
    "IN [ 'draft' , 'live' ]",       # extra whitespace
    "IN [\n                    'draft',\n                    'live',\n                ]",
    "IN (['draft', 'live'])",        # parenthesised list expression
    "IN ['draft', 'live', 'retracted']",  # SUPERSET — still names every current
    "IN ['archived', 'live', 'draft']",   # superset, unordered
])
def test_inline_current_allow_list_is_caught_in_every_spelling(literal: str) -> None:
    """The guard is formatting-insensitive, not one exact spelling.

    Every variant is a real evasion of the pre-#8662 line regex; the two
    superset cases are the ones the FIRST cut of the AST guard missed (its
    subset polarity flagged only an exact-or-smaller list). This pins the
    detector's coverage, so a revert to the single-spelling regex REDs here.
    """
    source = (
        "def q():\n"
        "    return " + repr(f"MATCH (n:Point) WHERE {literal} RETURN n") + "\n"
    )
    assert _current_status_allow_list_redeclarations(source), literal


@pytest.mark.parametrize("literal", [
    "IN ['Point', 'Object']",          # node labels — a different question
    "IN ['retracted', 'superseded']",  # terminal deny-list, not the allow-list
    "IN [$ids]",                        # bound parameter, no literal members
    "IN ['live']",                      # PROPER SUBSET — published-only filter
    "IN ['draft']",                     # PROPER SUBSET — drafts-only filter
])
def test_inline_list_without_current_vocabulary_is_not_flagged(literal: str) -> None:
    """The guard does not fire on lists that are not the FULL current allow-list.

    A proper subset is a deliberate narrower question (``IN ['live']`` /
    ``IN ['draft']``) the canonical builder does NOT answer — it also admits the
    other current status — so flagging it would demand a fix that silently
    WIDENS the predicate.
    """
    source = (
        "def q():\n"
        "    return " + repr(f"MATCH (n) WHERE n.x {literal} RETURN n") + "\n"
    )
    assert not _current_status_allow_list_redeclarations(source), literal


def test_docstring_mention_of_the_allow_list_is_not_an_offender() -> None:
    """A prose mention is not a re-declaration — the scan is over code only."""
    source = (
        "def q():\n"
        '    """Legacy code once used IN [\'draft\', \'live\']."""\n'
        "    return 1\n"
    )
    assert not _current_status_allow_list_redeclarations(source)



# ── 7. #8662 review — the contract text and the DERIVATION, not just ────────
#          values (a value-only pin cannot see a re-literalisation, because
#          the pre-fix literal has the same value as the derived set).

def _function_docstring(source: str, name: str) -> str:
    """The docstring of ``name`` in ``source``, or an explicit assertion."""
    for node in ast.walk(ast.parse(source)):
        if (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == name):
            return ast.get_docstring(node, clean=True) or ""
    raise AssertionError(f"{name} not found in the scanned source")


#: Reader-facing prose that documents the canonical terminal predicate. The
#: #8662 review found these still enumerating the PRE-#4058 four-member set
#: (``deprecated`` omitted) while the code path they describe excludes it.
#: Registering them turns "the next instance is found by review" into "found by
#: CI". ``review_connections`` is pinned separately — its bullet-scoped check is
#: stricter than this whole-docstring one — so it is not duplicated here.
_TERMINAL_CONTRACT_DOCSTRINGS = {
    # The canonical module's own docstring — the first place a reader meets
    # the predicate.
    "live module docstring": (_REPO_ROOT / "tortoise" / "live.py", None),
    "TortoiseSDK.query": (_REPO_ROOT / "tortoise" / "sdk.py", "query"),
    "TortoiseSDK.paginated_query": (
        _REPO_ROOT / "tortoise" / "sdk.py", "paginated_query"),
    "TortoiseSDK._raise_update_point_status_error": (
        _REPO_ROOT / "tortoise" / "sdk.py",
        "_raise_update_point_status_error"),
    "analyze._derived_live_operators": (
        _REPO_ROOT / "tortoise" / "analyze.py", "_derived_live_operators"),
    "FalkorProjection.extract_svbp_factors": (
        _REPO_ROOT / "tortoise" / "projection" / "__init__.py",
        "extract_svbp_factors"),
}


def _contract_docstring(source: str, name: str | None) -> str:
    """The named function's docstring, or the MODULE docstring when ``name``
    is ``None`` (``live.py``'s module docstring is itself contract text)."""
    if name is None:
        return ast.get_docstring(ast.parse(source), clean=True) or ""
    return _function_docstring(source, name)


def _terminal_contract_statuses_missing(doc: str) -> list[str]:
    """Canonical terminal statuses ``doc`` neither names nor cites.

    Empty when the prose cites ``TERMINAL_EXCLUDED_STATUSES`` (the reader can
    follow the constant) or names every member. The statuses are matched
    against ``live.TERMINAL_EXCLUDED_STATUSES`` itself — never against the set
    the prose happens to describe — so an enumeration that silently drops a
    member (the #8662 defect: the code excluded five, the prose named four)
    REDs. Whitespace is normalised, so a re-wrapped enumeration is still seen.
    """
    normalized = re.sub(r"\s+", " ", doc)
    if "TERMINAL_EXCLUDED_STATUSES" in normalized:
        return []
    return sorted(s for s in TERMINAL_EXCLUDED_STATUSES if s not in normalized)


def test_review_connections_contract_names_every_stale_status() -> None:
    """#8662 P2 — the contract text must name every member of the set.

    ``review_connections(mode=prune)``'s only reader-facing definition of
    ``stale`` is its docstring enumeration. #4058 rebased
    ``STALE_TERMINAL_STATUSES`` onto the canonical exclusion, which added the
    legacy ``deprecated`` — the enumeration named four of the five, so an agent
    reading the SDK was told a deprecated-incident edge would NOT surface as
    stale. Every member must be named.
    """
    doc = re.sub(r"\s+", " ", _function_docstring(
        (_REPO_ROOT / "tortoise" / "sdk.py").read_text(encoding="utf-8"),
        "review_connections"))
    assert "* stale" in doc and "* contested" in doc, (
        "review_connections no longer documents the mode=prune "
        "stale/contested bullets, so this pin cannot bind the stale "
        "contract to STALE_TERMINAL_STATUSES. Update the pin with the new "
        "contract text."
    )
    stale_bullet = doc.split("* stale", 1)[1].split("* contested", 1)[0]
    # Either enumerate every member, or cite the canonical constant: the
    # invariant is that no member is OMITTED from the reader-visible contract
    # (the pre-fix text enumerated four of the five and cited nothing).
    if "STALE_TERMINAL_STATUSES" in stale_bullet:
        return
    # Iterate the CANONICAL exclusion, not ``STALE_TERMINAL_STATUSES``: a pin
    # that iterates the set under test cannot see a member that set is missing
    # (the pre-fix docstring and the pre-fix 4-member set agreed with each
    # other while both understated the canonical five).
    missing = sorted(s for s in TERMINAL_EXCLUDED_STATUSES if s not in stale_bullet)
    assert not missing, (
        "the review_connections(mode=prune) stale enumeration omits "
        f"{missing} — a reader is told those statuses do not surface as "
        "stale. Name every member of STALE_TERMINAL_STATUSES in the docstring, "
        "or cite STALE_TERMINAL_STATUSES itself."
    )


@pytest.mark.parametrize("label", sorted(_TERMINAL_CONTRACT_DOCSTRINGS))
def test_terminal_contract_docstring_names_every_canonical_status(
        label: str) -> None:
    """#8662 P3 — every reader-facing enumeration must name the full set.

    Each registered docstring describes a code path that excludes
    ``live.TERMINAL_EXCLUDED_STATUSES``. The pre-fix prose named four of the
    five (the legacy ``deprecated`` omitted), so an agent reading
    ``TortoiseSDK.query`` was told a ``deprecated`` Point is served while the
    predicate excludes it. The review's suggestion — a
    ``_PREDICATE_CONSUMERS``-style registry — is what this test enforces, so
    the next understated enumeration is caught rather than found by review.
    """
    path, name = _TERMINAL_CONTRACT_DOCSTRINGS[label]
    doc = _contract_docstring(path.read_text(encoding="utf-8"), name)
    missing = _terminal_contract_statuses_missing(doc)
    assert not missing, (
        f"{label}: the reader-facing enumeration omits {missing} — the code "
        "path excludes them, so the contract tells a reader a non-current "
        "status IS served. Name every member of "
        "live.TERMINAL_EXCLUDED_STATUSES, or cite the constant."
    )


@pytest.mark.parametrize("doc,expected", [
    # the #8662 defect shape: the pre-#4058 four-member enumeration
    ("terminal statuses (retracted, superseded, outdated, archived)",
     ["deprecated"]),
    # the fixed shape — every member named, comma-separated
    ("terminal statuses "
     "(retracted, superseded, outdated, archived, deprecated)", []),
    # the fixed shape — every member named, slash-separated
    ("terminal (retracted/superseded/outdated/archived/deprecated)", []),
    # citing the constant is an accepted alternative to enumerating
    ("excluded by live.TERMINAL_EXCLUDED_STATUSES", []),
])
def test_contract_checker_detects_an_understated_enumeration(
        doc: str, expected: list[str]) -> None:
    """The registry checker itself is pinned, so the guard is not vacuous.

    Detection is formatting-insensitive (both separators are covered) and the
    set it iterates is the CANONICAL one, so an omitted member is caught even
    when the prose set was previously accurate — the #8662 defect.
    """
    assert _terminal_contract_statuses_missing(doc) == expected


def test_state_excluded_status_derives_from_the_canonical_exclusion() -> None:
    """#8662 P3 — pin the DERIVATION, not only the value.

    The pre-#4058 literal ``frozenset({"superseded", "deprecated",
    "retracted"})`` is EQUAL to ``TERMINAL_EXCLUDED_STATUSES - {"outdated",
    "archived"}``, so every value/inclusion assertion passes on the pre-fix
    code and a re-literalisation lands green — the exact drift the derivation
    exists to prevent. The claim the definition makes ("DERIVED rather than
    re-literalised") is pinned structurally: the assignment's RHS must subtract
    the canonical exclusion, and may name only the members the state view
    deliberately keeps visible — so a status added to the vocabulary lands in
    the set automatically, and a hand-written ``frozenset`` REDs.
    """
    source = (_REPO_ROOT / "tortoise" / "sdk.py").read_text(encoding="utf-8")
    assignments = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        # AnnAssign carries one target (``t: T = v``) — an annotation on the
        # definition must not read as "the derivation disappeared".
        targets = [node.target] if isinstance(node, ast.AnnAssign) else node.targets
        if any(isinstance(t, ast.Name) and t.id == "STATE_EXCLUDED_STATUS"
               for t in targets):
            assignments.append(node)
    assert len(assignments) == 1, (
        f"expected exactly one STATE_EXCLUDED_STATUS assignment, "
        f"found {len(assignments)}"
    )
    rhs = assignments[0].value
    assert isinstance(rhs, ast.BinOp) and isinstance(rhs.op, ast.Sub), (
        "STATE_EXCLUDED_STATUS must DERIVE from the canonical Point exclusion "
        "(TERMINAL_EXCLUDED_STATUSES - {...}), not re-literalise it — a "
        "hand-written set drifts when the vocabulary grows: "
        f"{ast.unparse(rhs)[:160]}"
    )
    assert (isinstance(rhs.left, ast.Name)
            and rhs.left.id == "TERMINAL_EXCLUDED_STATUSES") \
        or (isinstance(rhs.left, ast.Attribute)
            and rhs.left.attr == "TERMINAL_EXCLUDED_STATUSES"), ast.unparse(rhs)
    rhs_set = rhs.right
    # Accept the set literal or its set(...)/frozenset(...) spelling — the
    # invariant is "subtract the canonical exclusion", not one syntax.
    if (isinstance(rhs_set, ast.Call) and isinstance(rhs_set.func, ast.Name)
            and rhs_set.func.id in {"set", "frozenset"}
            and len(rhs_set.args) == 1):
        rhs_set = rhs_set.args[0]
    assert isinstance(rhs_set, ast.Set), ast.unparse(rhs)
    assert all(isinstance(elt, ast.Constant) for elt in rhs_set.elts), (
        "STATE_EXCLUDED_STATUS may only subtract a set of string literals: "
        f"{ast.unparse(rhs_set)}"
    )
    deliberately_visible = {elt.value for elt in rhs_set.elts}
    assert deliberately_visible == {"outdated", "archived"}, (
        "the state view may only drop the members it deliberately keeps "
        f"visible (the #3308 pin): {sorted(deliberately_visible)}"
    )
    # And the VALUE tracks the LIVE canonical set, not the vocabulary as it was
    # when a hand-written literal was first spelled out.
    expected = TERMINAL_EXCLUDED_STATUSES - deliberately_visible
    assert expected == TortoiseSDK.STATE_EXCLUDED_STATUS
