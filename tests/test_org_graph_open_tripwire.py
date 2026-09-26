"""#3645: tripwire for the bare-verdict-then-open silent-empty-graph bug.

Incident. #3543/#3622 renamed the tenant vocabulary ``team`` → ``org`` and,
with it, the SDK's namespace rule ``team_{ns}`` → ``org_{ns}``. No data
migration renamed existing graphs: an org that existed before the rename is
physically ``team_{id}`` forever. ``hosted_api._graph_has_org_namespace``
(line ~17977) probes BOTH prefixes on purpose, so it returns a bare
"listed / not listed" that is True when EITHER name exists — it does NOT say
WHICH name is real. The pre-#3622 caller then did::

    if not _graph_has_org_namespace(org_id): ...
    proj = _make_sdk(namespace=org_id)          # re-derives org_{id}

For a legacy ``team_{id}`` org the verdict is True, yet ``namespace=org_id``
re-derives ``org_{id}`` — a DIFFERENT, ABSENT graph. Constructing it MINTED
the absent graph (a read-path WRITE, banned by "pin 4" in this codebase) and
then read it empty, returning **HTTP 200 with no data**. That is the #873
failure shape: an empty body with a success code reads as "you have no data"
instead of "wrong graph". Because ``team_{id}`` is the physical name of every
pre-rename org's graph, the bug would have silently emptied every org with
pre-rename data. The fix: graph-OPENING callers go through
``_open_org_graph_sdk`` (line ~18043), which addresses the literally LISTED
name (the legacy full name, or the canonical namespace form). It returns None
when the registry probe FAILS **or** when neither name is listed — the ``None``
overload is load-bearing, not just "not listed": the registry probe
(``_registry_existing_graphs``) returns ``None`` on ANY exception, and callers
treat that ``None`` as FAIL-OPEN, keeping ``_make_sdk(namespace=org_id)`` as the
fallback so a graph-up-unknown read proceeds and raises into the 'unavailable'
path instead of taking the node-absent branch.

Regression risk this file closes. Nothing fails today if a FUTURE caller
writes ``if _graph_has_org_namespace(org_id): ... _make_sdk(namespace=org_id)``
— the wrong graph just reads empty. Two layers close that:

  Layer 1 — ``TestOrgGraphOpenTripwireStatic``
    Two ``ast``-based source checks (NOT regex: indentation, decorators and
    comments/docstrings cannot fool them).

    (a) The allowance list. EVERY function in the ``tortoise`` package that
        references the bare verdict ``_graph_has_org_namespace`` — bare name
        OR the cross-module ``hosted_api._graph_has_org_namespace(...)``
        attribute form — must be enumerated in ``VERDICT_CONSUMERS`` with a
        one-line justification for why it consults the verdict — for an
        ORG-graph open, that it routes the open through
        ``_open_org_graph_sdk``; a non-org open is justified differently and
        additionally needs a ``SHAPE_EXEMPT`` entry. This is the real guard
        against a NEW consumer. The live set is EXACTLY two functions, both in
        ``hosted_api.py``: ``_get_onboarding_projection`` (~18096) and
        ``patch_onboarding_state`` (~18327). Adding a third is a deliberate,
        reviewed decision — the ``KEY_WRITE_HANDLERS`` convention from
        ``tests/test_key_write_pins_tripwire.py`` (#2299). ``VERDICT_CONSUMERS``
        is NOT an exemption from the shape predicate below — it answers only
        WHO may consult the verdict. A legitimate NON-org open (e.g. a
        telemetry gate that consults the verdict and opens the registry graph)
        is exempted through the SEPARATE ``SHAPE_EXEMPT`` list, empty by
        design today — never by adding an entry here. ``ast`` ignores comments,
        so ``tortoise/mcp_server.py:1879``'s comment mention is correctly not a
        consumer.

    (b) The offender predicate. Within ONE function's body, an offender
        references ``_graph_has_org_namespace`` AND ``_make_sdk`` AND does NOT
        reference ``_open_org_graph_sdk`` — UNLESS the function's name is in
        ``SHAPE_EXEMPT``, which skips it entirely. This is a NAME-PRESENCE
        heuristic, not a call-graph analysis. Its honest limits: extracting a
        module-level ``_make_sdk`` helper and calling it from an allowlisted
        caller passes clean, and a bare mention of the opener
        (``_ = _open_org_graph_sdk``) clears a function; attribute/aliased/
        ``getattr`` call forms are blind spots (acceptable because
        ``_make_sdk`` is module-local to ``hosted_api.py``). It is therefore
        deliberately scoped to ``hosted_api.py``; check (a) — package-wide —
        is the cross-module guard. The real-code check passes
        ``exempt=set(SHAPE_EXEMPT)`` — NOT ``VERDICT_CONSUMERS`` — so the
        verdict allowlist never weakens the shape check, and
        ``test_live_verdict_callers_route_through_the_opener`` pins the two
        live callers independently of any exemption. The predicate's failure
        message is conditional on the open's target: ``_open_org_graph_sdk``
        for the org graph, a reviewed ``SHAPE_EXEMPT`` entry for a non-org
        open. The scanner is a pure function, so this file carries its own
        positive control: it MUST flag a faithful excerpt of the REAL
        pre-#3622 body, not only a paraphrase.

  Layer 2 — ``TestOrgGraphOpenTripwireBehavior``
    The real contract over a REAL embedded store, using the shared
    ``tests._http_fixtures.patched_tortoise_sdk`` fixture (the #1950/#2090
    keepalive-anchor pattern; ``tests/test_hosted_api.py:133`` is the
    canonical ``TortoiseSDK.__init__`` patch it centralizes). For an org whose
    ONLY listed graph is a legacy ``team_{id}``:
      1. reading through the fixed path returns the legacy graph's REAL
         content (not the node-absent FLOW defaults and not empty); and
      2. the read does NOT mint ``org_{id}`` — the server-wide graph list is
         UNCHANGED after the read. This is the pin-4 read-path-write assertion
         and is the more important of the two; a positive control proves the
         assertion is not vacuous.

The docker-lane test redirect (epic #1647) would flip this file's explicit
temp-path constructions onto ``TORTOISE_DB_URI`` and hash the graph names, so
the behavioral layer adds its own module stem to ``TORTOISE_TEST_NO_REDIRECT``
at runtime — the documented carve-out seam. The carve-out itself is
env-only, but the FILE is registered in ``config/ci-surfaces.yml`` so the
required ``manifest-integrity`` job sees it; an earlier draft of this docstring
claimed "no tracked-file change", which was false (#3645).

Scope note: this is a PATTERN tripwire, not a second copy of
``tests/test_hosted_api.py::TestOrgGraphNameRoundTrip`` (line ~7477), which
pins the stored-name round-trip and the both-prefix verdict with tuple stubs.
This file extends that coverage with the function-level source pattern and a
real-store behavioral contract; the opener-case assertions here use real
embedded SDKs (inspectable ``_namespace`` / ``_graph_name``) rather than the
existing stubs.
"""

from __future__ import annotations

import ast
import os
import sys
from collections.abc import Collection, Iterator

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: I001

import tortoise.hosted_api as ha_mod
from tortoise.onboarding import state as _os
from tests._http_fixtures import patched_tortoise_sdk

# ── the two symbols the bug is made of ───────────────────────────────────────
_VERDICT = "_graph_has_org_namespace"   # bare listed/not-listed probe
_CONSTRUCT = "_make_sdk"                # namespace= re-derives org_{id}
_OPENER = "_open_org_graph_sdk"         # addresses the LISTED name

_MODULE_STEM = "test_org_graph_open_tripwire"

# Spelled through a NAME so this file's synthetic controls do not themselves
# trip the namespace-literal guard in ``tests/test_markers.py`` (which scans
# test-file text for `namespace="..."`). ``_scan_offenders`` is
# argument-blind, so the namespace value never matters to these controls.
_NON_ORG_NS = "registry"

# ── the allowance list (the real guard) ──────────────────────────────────────
# EVERY function permitted to reference ``_graph_has_org_namespace``, anywhere
# in the ``tortoise`` package — bare name (``_graph_has_org_namespace(...)``)
# or cross-module attribute (``hosted_api._graph_has_org_namespace(...)``).
# ``test_verdict_consumers_are_exhaustively_enumerated`` fails on any function
# NOT listed here, so a new verdict call site cannot land silently. The live
# set is EXACTLY these two, both in ``tortoise/hosted_api.py``; each routes the
# open through ``_open_org_graph_sdk`` (the LISTED name).
#
# This list is NOT an exemption from the shape predicate. It answers only WHO
# may consult the verdict, and every live entry routes its open through
# _open_org_graph_sdk. A function that legitimately consults the verdict AND
# opens a NON-org graph (registry, telemetry, …) is exempted through the
# SEPARATE SHAPE_EXEMPT list below — never by adding it here. Same convention
# as KEY_WRITE_HANDLERS in tests/test_key_write_pins_tripwire.py (#2299).
VERDICT_CONSUMERS: dict[str, str] = {
    # GET/PATCH echo/MCP gate projection (hosted_api.py ~18096): gates on the
    # bare verdict, then opens the LISTED name (with _make_sdk(namespace=…) as
    # the fail-open fallback only).
    "_get_onboarding_projection": (
        "onboarding projection read — opens through _open_org_graph_sdk(org_id)"
    ),
    # PATCH accept-and-drop node probe (hosted_api.py ~18327): consults the
    # verdict for the drop decision, then opens the LISTED name.
    "patch_onboarding_state": (
        "PATCH node probe — opens through _open_org_graph_sdk(org['org_id'])"
    ),
}


# ── the shape exemption list (EMPTY BY DESIGN) ───────────────────────────────
# Functions the offender predicate below SKIPS ENTIRELY: permitted to reference
# ``_graph_has_org_namespace`` AND construct an SDK with ``_make_sdk`` WITHOUT
# routing the open through ``_open_org_graph_sdk``. EMPTY BY DESIGN — no live
# non-org opener exists today.
#
# This list exists so that a genuine non-org open (a telemetry gate that
# consults the verdict and opens the registry graph) has a documented,
# reviewable path instead of inviting the decorative-opener hack the predicate
# otherwise rewards. Adding an entry is a DELIBERATE, reviewed act: give it the
# one-line justification, add the function to VERDICT_CONSUMERS as well, and
# confirm it is live. ``test_shape_exemptions_are_live_verdict_consumers``
# enforces both invariants (every key is a VERDICT_CONSUMERS key AND a live
# verdict consumer), so an exemption cannot rot after a rename or a dropped
# consult. The exemption is NOT self-service: it is a reviewed change to this
# artifact, exactly like a VERDICT_CONSUMERS entry.
SHAPE_EXEMPT: dict[str, str] = {}


# ── layer 1: static source scan ──────────────────────────────────────────────


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _hosted_api_source() -> str:
    with open(os.path.join(_repo_root(), "tortoise", "hosted_api.py"),
              encoding="utf-8") as fh:
        return fh.read()


def _iter_package_sources() -> Iterator[tuple[str, str]]:
    """(repo-relative path, source) for every ``tortoise/**/*.py`` module,
    ``__pycache__`` skipped — the package-wide scope of the consumer
    allowance check (#3645 review P2)."""
    root = os.path.join(_repo_root(), "tortoise")
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d != "__pycache__")
        for fname in sorted(filenames):
            if not fname.endswith(".py"):
                continue
            path = os.path.join(dirpath, fname)
            with open(path, encoding="utf-8") as fh:
                yield os.path.relpath(path, _repo_root()), fh.read()


def _symbol_mentions(node: ast.AST, symbol: str) -> bool:
    """True when ``symbol`` is referenced inside ``node`` as a bare
    ``ast.Name`` (``_graph_has_org_namespace(...)``) or as an
    ``ast.Attribute`` (``hosted_api._graph_has_org_namespace(...)`` — the
    cross-module reintroduction form, #3645 review P2). Comments and string
    literals are not AST nodes, so a mention that survives only in a comment
    or docstring does NOT count (tortoise/mcp_server.py:1879 is a comment)."""
    for n in ast.walk(node):
        if isinstance(n, ast.Name) and n.id == symbol:
            return True
        if isinstance(n, ast.Attribute) and n.attr == symbol:
            return True
    return False


def _verdict_consumers(source: str) -> list[tuple[str, int]]:
    """(function name, line) for every function in ``source`` that references
    the bare verdict — bare-name or cross-module attribute form; comments and
    docstrings are invisible to ``ast``."""
    consumers: list[tuple[str, int]] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and _symbol_mentions(node, _VERDICT):
            consumers.append((node.name, node.lineno))
    return consumers


def _referenced_names(node: ast.AST) -> set[str]:
    """Bare NAME references inside ``node`` (comments and string literals are
    not ``ast.Name``, so a helper that survives only in a docstring does not
    count — the pre-#3622 shape where the call was dropped but the mention
    stayed behind)."""
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def _scan_offenders(
    source: str, exempt: Collection[str] = frozenset()
) -> list[tuple[str, int]]:
    """Functions that consult the bare verdict AND construct an SDK without
    routing the open through ``_open_org_graph_sdk``.

    GUILTY iff ``_graph_has_org_namespace`` and ``_make_sdk`` both appear in
    the function, ``_open_org_graph_sdk`` does not, AND the function's name is
    not in ``exempt``. The fixed caller shape
    (``_open_org_graph_sdk(org_id) or _make_sdk(namespace=org_id)``) keeps
    ``_make_sdk`` only as the fail-open fallback and is therefore clean; the
    pre-fix shape (a bare ``_make_sdk(namespace=org_id)`` with no opener) is
    flagged.

    ``exempt`` is the caller-supplied, DELIBERATE allowance for a legitimate
    non-org open — the real-code check passes ``exempt=set(SHAPE_EXEMPT)``,
    never ``VERDICT_CONSUMERS``, so the verdict allowlist cannot weaken the
    shape check.

    HONEST LIMITS (see the module docstring): this is a name-presence
    heuristic over ONE function's body, not a call-graph analysis. A
    module-level helper that performs the ``_make_sdk`` and is called from a
    verdict-consulting caller passes clean, and a bare mention of the opener
    (``_ = _open_org_graph_sdk``) clears a function, as does an ``exempt``
    entry. ``VERDICT_CONSUMERS`` is the guard against a new consumer;
    ``SHAPE_EXEMPT`` is the reviewed exemption for a non-org opener; this
    predicate is the in-function shape check for everything else.
    Attribute/aliased/``getattr`` call forms are blind spots, acceptable
    because ``_make_sdk`` is module-local to ``hosted_api.py``. Deliberately
    scoped to ``hosted_api.py``.
    """
    tree = ast.parse(source)
    offenders: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name in exempt:
            continue
        names = _referenced_names(node)
        if _VERDICT in names and _CONSTRUCT in names and _OPENER not in names:
            offenders.append((node.name, node.lineno))
    return offenders


# Faithful excerpt of the REAL pre-#3622 bug body. Provenance: the pre-fix
# revision of ``tortoise/hosted_api.py`` — the parent blob of ``ec79951bd``
# (the #3622 commit) — is the REACHABLE object
# ``7a1904408de53c6e8d2b7f905c8aa7e22f94507e``. It is reachable from
# ``origin/main`` @ ``ec79951bd`` (``git rev-list origin/main --objects``
# lists it), so it exists on a fresh clone and survives ``git gc``. That
# revision spells the shape with the pre-rename ``team_*`` vocabulary —
# ``_graph_has_team_namespace(team_id)`` then
# ``_make_sdk(namespace=team_id)._get_proj()``. This excerpt applies the #3622
# rename itself (``team`` → ``org``) so the control pins the CURRENT
# vocabulary, trims the non-bug-bearing branches, and shortens the
# ``patch_onboarding_state`` signature: the bug-bearing lines — the bare
# verdict gate and the bare ``_make_sdk(namespace=…)`` construction — are
# verbatim modulo that rename, and "faithful" claims nothing beyond them. Both
# functions keep the shape that matters: gate on the bare verdict, then
# construct ``_make_sdk(namespace=…)`` directly, with no
# ``_open_org_graph_sdk`` anywhere in the function.
#
# The object id previously cited here (03f22e6d…) was DANGLING (reachable from
# no ref → pruned by ``git gc`` on a fresh clone) and is not cited again.
_PRE_FIX_BLOB = "7a1904408de53c6e8d2b7f905c8aa7e22f94507e"
_PRE_FIX_EXCERPT = (
    "def _get_onboarding_projection(org_id: str) -> dict:\n"
    "    raw = _get_onboarding_state(org_id)\n"
    "    if not _graph_has_org_namespace(org_id):\n"
    "        state = dict(raw)\n"
    "        state.update(_os.flow_defaults())\n"
    "        return state\n"
    "    try:\n"
    "        proj = _make_sdk(namespace=org_id)._get_proj()\n"
    "        node = _os.read_onboarding_node(proj, org_id)\n"
    "        steps = _os.completed_steps(proj, org_id) if node is not None else []\n"
    "    except Exception:\n"
    "        return dict(raw)\n"
    "    return state\n"
    "\n"
    "\n"
    "async def patch_onboarding_state(body, org=None):\n"
    "    updates = {k: v for k, v in body.model_dump().items() if v is not None}\n"
    "    if (_ACCEPT_AND_DROP and \"onboarding_complete\" in updates\n"
    "            and _graph_has_org_namespace(org[\"org_id\"])):\n"
    "        try:\n"
    "            _node_sdk = _make_sdk(namespace=org[\"org_id\"])\n"
    "            try:\n"
    "                _node = _os.read_onboarding_node(\n"
    "                    _node_sdk._get_proj(), org[\"org_id\"])\n"
    "            finally:\n"
    "                _node_sdk.close()\n"
    "        except Exception:\n"
    "            _node = None\n"
    "        if _node is not None:\n"
    "            updates.pop(\"onboarding_complete\")\n"
)


class TestOrgGraphOpenTripwireStatic:
    """Layer 1 — the function-level source pattern over the real module."""

    def test_no_function_consults_the_bare_verdict_and_constructs_an_sdk(self):
        """No function may both consult the bare ``_graph_has_org_namespace``
        verdict AND construct with ``_make_sdk`` unless it opens through
        ``_open_org_graph_sdk``.

        The verdict is True for EITHER tenant prefix, so it does not say which
        name is real. A caller that consults it and then constructs
        ``_make_sdk(namespace=org_id)`` opens — and so MINTS — ``org_{id}`` for
        a legacy ``team_{id}`` org, reading it empty at HTTP 200 (the #873
        failure shape; pin 4 bans the write).

        Run with ``exempt=set(SHAPE_EXEMPT)`` (empty by design), so the two
        live consumers remain shape-checked. A function legitimately opening a
        NON-org graph is exempted through ``SHAPE_EXEMPT``; adding it to
        ``VERDICT_CONSUMERS`` alone does NOT exempt it.
        """
        offenders = _scan_offenders(
            _hosted_api_source(), exempt=set(SHAPE_EXEMPT))
        assert offenders == [], (
            "org-graph open tripwire (#3645): "
            + "; ".join(f"{name}() at hosted_api.py:{lineno}"
                        for name, lineno in offenders)
            + " consults the bare _graph_has_org_namespace(...) verdict AND "
            "constructs an SDK with _make_sdk(...) without routing through "
            "_open_org_graph_sdk(...).\n"
            "WHY THIS IS A BUG: the bare verdict is True for EITHER tenant "
            "prefix (`org_<id>` or the pre-rename `team_<id>`), so it does not "
            "say WHICH name is real; a bare _make_sdk(namespace=org_id) "
            "re-derives org_<id>, minting an absent graph on a read (banned by "
            "pin 4) and returning HTTP 200 with no data.\n"
            "HOW TO RESOLVE — it depends on what the open targets:\n"
            "  * if the open IS the org graph: open the LISTED name via "
            "_open_org_graph_sdk(org_id), keeping _make_sdk(namespace=org_id) "
            "only as its fail-open fallback.\n"
            "  * if the open targets something OTHER than the org graph (the "
            "registry, a telemetry graph, …): this name-presence predicate "
            "cannot tell — add the function to SHAPE_EXEMPT with a one-line "
            "justification (and to VERDICT_CONSUMERS if it is not already "
            "there) and get the exemption reviewed. Do NOT add a decorative "
            "_open_org_graph_sdk mention to clear this test."
        )

    def test_verdict_consumers_are_exhaustively_enumerated(self):
        """The allowance list IS the guard (#3645 review P1-3): every function
        in the ``tortoise`` package that references the bare
        ``_graph_has_org_namespace`` verdict must be enumerated in
        ``VERDICT_CONSUMERS`` — the cross-module
        ``hosted_api._graph_has_org_namespace(...)`` form included. A NEW
        consumer fails here until someone adds an entry, states in one line
        why it consults the verdict, and gets it reviewed.

        This list does NOT exempt a function from the shape predicate; that is
        ``SHAPE_EXEMPT`` (empty by design). A non-org open needs BOTH: a
        ``VERDICT_CONSUMERS`` entry (it consults the verdict) and a
        ``SHAPE_EXEMPT`` entry (its open does not route through the opener)."""
        unenumerated: list[str] = []
        seen: set[str] = set()
        for path, source in _iter_package_sources():
            for name, lineno in _verdict_consumers(source):
                seen.add(name)
                if name not in VERDICT_CONSUMERS:
                    unenumerated.append(f"{path}:{lineno} {name}()")
        assert unenumerated == [], (
            "un-enumerated _graph_has_org_namespace(...) consumer(s) — a new "
            "verdict call site must be a DELIBERATE, reviewed decision. Add "
            "to VERDICT_CONSUMERS with a one-line justification for why it "
            "consults the verdict. If it then OPENS a graph, the shape "
            "predicate applies next: route the open through "
            "_open_org_graph_sdk(...) (the org graph) or add the function to "
            "SHAPE_EXEMPT (a non-org open):\n"
            + "\n".join(unenumerated)
        )
        stale = sorted(set(VERDICT_CONSUMERS) - seen)
        assert stale == [], (
            f"VERDICT_CONSUMERS entr(ies) with no verdict reference: {stale} "
            "— delete the stale entry (or restore the consult)"
        )

    def test_shape_exemptions_are_live_verdict_consumers(self):
        """Every ``SHAPE_EXEMPT`` key must also be a ``VERDICT_CONSUMERS`` key
        AND must still be a LIVE verdict consumer (the function exists and
        still references the verdict). Otherwise an exemption rots into a
        blanket name allowance after a rename or a dropped consult."""
        not_consumers = sorted(set(SHAPE_EXEMPT) - set(VERDICT_CONSUMERS))
        assert not_consumers == [], (
            f"SHAPE_EXEMPT entr(ies) absent from VERDICT_CONSUMERS: "
            f"{not_consumers} — a shape exemption is only meaningful for a "
            "function that consults the verdict; add the VERDICT_CONSUMERS "
            "entry too (and justify it)"
        )
        live = {
            name
            for _path, source in _iter_package_sources()
            for name, _lineno in _verdict_consumers(source)
        }
        stale = sorted(set(SHAPE_EXEMPT) - live)
        assert stale == [], (
            f"stale SHAPE_EXEMPT entr(ies) — no longer a live verdict "
            f"consumer: {stale} — delete the exemption (a renamed or "
            "verdict-dropping function must not stay shape-exempt)"
        )

    def test_shape_exempt_skips_exactly_the_named_function(self):
        """Positive control for the exemption seam: a non-org opener (verdict
        + ``_make_sdk`` with a non-org namespace, no ``_open_org_graph_sdk``)
        is RED when unexempted and GREEN once its name is passed as
        ``exempt`` — the documented path works, and it skips ONLY the named
        function."""
        telemetry = (
            "def _telemetry_gate(org_id):\n"
            "    if not _graph_has_org_namespace(org_id):\n"
            "        return None\n"
            "    return _make_sdk(namespace=_NON_ORG_NS)\n"
            "\n"
            "\n"
            "def _still_guilty(org_id):\n"
            "    if not _graph_has_org_namespace(org_id):\n"
            "        return None\n"
            "    return _make_sdk(namespace=org_id)\n"
        )
        assert _scan_offenders(telemetry) == [
            ("_telemetry_gate", 1), ("_still_guilty", 7),
        ]
        assert _scan_offenders(telemetry, exempt={"_telemetry_gate"}) == [
            ("_still_guilty", 7),
        ]

    def test_consumer_scan_sees_the_cross_module_attribute_form(self):
        """The package-wide consumer check must catch the cross-module form
        (``ha._graph_has_org_namespace(...)``) — the reintroduction path the
        package walk exists for — and must NOT be fooled by a comment mention
        (``ast`` ignores comments; ``tortoise/mcp_server.py:1879`` mentions
        the symbol only in a comment, so it is correctly not a consumer)."""
        cross = (
            "def _sneaky(org_id):\n"
            "    return ha._graph_has_org_namespace(org_id)\n"
        )
        assert _verdict_consumers(cross) == [("_sneaky", 1)]
        comment_only = (
            "def _comment_only(org_id):\n"
            "    # ha._graph_has_org_namespace(org_id)\n"
            "    return org_id\n"
        )
        assert _verdict_consumers(comment_only) == []

    def test_scanner_flags_the_verdict_then_construct_pattern(self):
        """Positive control — the scanner MUST flag the pre-fix shape. A
        tripwire that cannot fail is worthless; this proves the scan fires on
        a synthetic function that consults the verdict then constructs
        directly (no opener)."""
        bad = (
            "def _pre_fix_caller(org_id):\n"
            "    if not _graph_has_org_namespace(org_id):\n"
            "        return None\n"
            "    return _make_sdk(namespace=org_id)\n"
        )
        offenders = _scan_offenders(bad)
        assert offenders == [("_pre_fix_caller", 1)], offenders

    def test_scanner_flags_the_real_pre_fix_body(self):
        """Positive control on the REAL historical shape — a faithful excerpt
        of the pre-#3622 body: the REACHABLE pre-fix ``tortoise/hosted_api.py``
        blob ``7a1904408de53c6e8d2b7f905c8aa7e22f94507e`` (see the excerpt
        comment above for exactly what is verbatim vs. reformatted). Both
        consult the bare verdict, then construct ``_make_sdk(namespace=…)``
        directly, with no ``_open_org_graph_sdk`` — so both are offenders.
        This is the shape that actually shipped, so the scanner's positive
        control cannot drift from the bug it guards."""
        offenders = _scan_offenders(_PRE_FIX_EXCERPT)
        names = [name for name, _ in offenders]
        assert names == ["_get_onboarding_projection", "patch_onboarding_state"], (
            f"scanner missed the real pre-#3622 shape (blob {_PRE_FIX_BLOB}): "
            f"{offenders}"
        )

    def test_scanner_passes_verdict_only_and_opener_functions(self):
        """The two legitimate shapes must NOT be flagged:
          * a verdict-ONLY function (never calls _make_sdk) — the
            accept-and-drop verdict the bare predicate exists for; and
          * a verdict-consulting opener caller using the fixed
            ``_open_org_graph_sdk(...) or _make_sdk(...)`` fallback form.
        """
        verdict_only = (
            "def _gate(org_id):\n"
            "    return _graph_has_org_namespace(org_id)\n"
        )
        opener_fallback = (
            "def _fixed(org_id):\n"
            "    if not _graph_has_org_namespace(org_id):\n"
            "        return None\n"
            "    return (_open_org_graph_sdk(org_id)\n"
            "            or _make_sdk(namespace=org_id))\n"
        )
        assert _scan_offenders(verdict_only) == []
        assert _scan_offenders(opener_fallback) == []

    def test_live_verdict_callers_route_through_the_opener(self):
        """Pin the two live verdict call sites so a rename/move that silently
        drops the opener is caught here, not only above.

        The two live verdict call sites at ``hosted_api.py:18096``
        (``_get_onboarding_projection`` — the FLOW-defaults read branch) and
        ``:18327`` (``patch_onboarding_state`` — the accept-and-drop gate)
        both consult the bare verdict; each opens the listed name through
        ``_open_org_graph_sdk``. They are the live proof that the scan's
        predicate passes real, correct code.

        Deliberately exemption-BLIND: this check never passes ``exempt``, so
        adding a live caller to ``SHAPE_EXEMPT`` could not silence it — the
        opener reference is asserted directly on the function node.
        """
        source = _hosted_api_source()
        offenders = {name for name, _ in _scan_offenders(source)}
        for fn in ("_get_onboarding_projection", "patch_onboarding_state"):
            assert fn not in offenders, f"{fn} regressed into a bare open"
            assert _VERDICT in _referenced_names(
                _function_node(source, fn)), f"{fn} no longer consults verdict?"
            assert _OPENER in _referenced_names(
                _function_node(source, fn)), (
                f"{fn} consults the bare verdict but no longer routes "
                "through _open_org_graph_sdk — the #3645 silent-empty-graph "
                "regression"
            )


def _function_node(source: str, name: str) -> ast.AST:
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == name:
            return node
    raise AssertionError(f"function {name} not found in hosted_api.py")


# ── layer 2: behavioral contract (real embedded store) ───────────────────────


def _exempt_from_uri_redirect(monkeypatch) -> None:
    """Add this module's stem to TORTOISE_TEST_NO_REDIRECT.

    Epic #1647's docker-lane redirect flips explicit-path constructions onto
    TORTOISE_DB_URI (and hash-renames legacy graph names), which would make
    the temp embedded store — and the literal ``team_{id}`` graph — a fiction.
    The stem-carve-out seam is the documented exemption; it is read at
    construction time, so this must run before any SDK is built.
    """
    stems = {s.strip() for s in
             os.environ.get("TORTOISE_TEST_NO_REDIRECT", "").split(",")
             if s.strip()}
    stems.add(_MODULE_STEM)
    monkeypatch.setenv("TORTOISE_TEST_NO_REDIRECT", ",".join(sorted(stems)))


@pytest.fixture
def embedded_store(tmp_path, monkeypatch):
    """A real, isolated embedded store via the shared canonical fixture.

    Registry mode + no Supabase so ``_get_onboarding_state`` reads/writes the
    registry graph (not a control plane); the temp DB path means the
    server-wide graph list is per-test.
    """
    _exempt_from_uri_redirect(monkeypatch)
    monkeypatch.setenv("TORTOISE_CONTROL_PLANE", "registry")
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    with patched_tortoise_sdk(os.path.join(str(tmp_path), "tripwire.db")):
        yield


def _mint_legacy_graph_with_content(org_id: str) -> str:
    """Create the pre-rename ``team_{org_id}`` graph with REAL content.

    Writes an OnboardingState node (fork='self') and one canonical step edge,
    so a read that lands on the wrong graph is distinguishable from a read
    that lands on this one: the node-absent path serves FLOW defaults
    (fork=None, completed_steps=[]) instead.
    """
    sdk = ha_mod._make_sdk(graph_name=f"team_{org_id}")
    try:
        proj = sdk._get_proj()
        _os.ensure_onboarding_state_node(proj, org_id, fork=_os.FORK_SELF)
        _os.write_completed_step(proj, org_id, "decide-completed")
    finally:
        sdk.close()
    return f"team_{org_id}"


def _listed_graphs() -> set[str]:
    listed = ha_mod._registry_existing_graphs()
    assert listed is not None, "registry probe failed in the embedded lane"
    return set(listed)


class TestOrgGraphOpenTripwireBehavior:
    """Layer 2 — the real contract, over a real embedded graph store."""

    def test_read_returns_legacy_content_and_does_not_mint(self, embedded_store):
        """Legacy-only org: the fixed read returns the legacy graph's REAL
        content and leaves the server-wide graph list UNCHANGED.

        The list assertion is the pin-4 read-path-write check and the more
        important half: the pre-fix open would mint ``org_{id}`` here.
        """
        org_id = "acme-legacy"
        legacy = _mint_legacy_graph_with_content(org_id)
        listed = _listed_graphs()
        assert legacy in listed, listed
        assert f"org_{org_id}" not in listed, listed

        # Warm the registry-side read/write the projection performs first
        # (_get_onboarding_state auto-initializes the jsonb mirror) so the
        # only graph the read path could newly mint is the org graph.
        ha_mod._get_onboarding_state(org_id)
        before = _listed_graphs()

        proj = ha_mod._get_onboarding_projection(org_id)

        after = _listed_graphs()
        # 1. the legacy graph's REAL content, not the node-absent defaults.
        assert proj["fork"] == _os.FORK_SELF, proj
        assert "decide-completed" in proj["completed_steps"], proj
        # 2. pin 4 — the read minted nothing.
        assert f"org_{org_id}" not in after, (
            f"read through the fixed path minted the absent canonical graph "
            f"org_{org_id} — the #3645 pin-4 read-path write"
        )
        assert after == before, (
            f"read changed the server-wide graph list: {before ^ after}"
        )

    def test_pre_fix_open_would_mint_the_absent_graph(self, embedded_store):
        """Positive control for the pin-4 assertion above.

        The pre-fix shape — a bare ``_make_sdk(namespace=org_id)`` for a
        legacy-only org — really does materialize the absent canonical graph,
        so ``after == before`` in the test above is a live discriminator, not
        a vacuous claim.
        """
        org_id = "acme-bugprobe"
        _mint_legacy_graph_with_content(org_id)
        before = _listed_graphs()
        assert f"org_{org_id}" not in before, before

        sdk = ha_mod._make_sdk(namespace=org_id)  # the pre-#3622 bug shape
        try:
            sdk._get_proj()  # opening is what mints
        finally:
            sdk.close()

        after = _listed_graphs()
        assert f"org_{org_id}" in after, (
            "the pre-fix construction no longer mints — the pin-4 "
            "discriminator has gone stale"
        )

    def test_opener_binds_the_listed_name(self, embedded_store):
        """``_open_org_graph_sdk`` addresses the LISTED name.

        legacy-only → the FULL ``team_{id}`` name passed verbatim
        (``graph_name=``); canonical → the namespace form (so the embedded
        keepalive key and downstream ``_namespace`` consumers stay identical);
        neither listed → None.
        """
        legacy_org = "acme-legacy-open"
        canonical_org = "acme-canonical-open"
        _mint_legacy_graph_with_content(legacy_org)
        _canon_sdk = ha_mod._make_sdk(namespace=canonical_org)
        try:
            _canon_sdk._get_proj()  # mint canonical
        finally:
            _canon_sdk.close()

        legacy_sdk = ha_mod._open_org_graph_sdk(legacy_org)
        try:
            assert legacy_sdk is not None
            assert legacy_sdk._graph_name == f"team_{legacy_org}"
            assert legacy_sdk._namespace is None
        finally:
            legacy_sdk.close()

        canonical_sdk = ha_mod._open_org_graph_sdk(canonical_org)
        try:
            assert canonical_sdk is not None
            assert canonical_sdk._namespace == canonical_org
            assert canonical_sdk._graph_name is None
        finally:
            canonical_sdk.close()

        assert ha_mod._open_org_graph_sdk("acme-neither-listed") is None

    def test_verdict_probes_both_prefixes_and_fails_open(self, embedded_store):
        """``_graph_has_org_namespace`` unchanged: True for either prefix,
        False when neither is listed, and fail-OPEN (True) when the probe
        itself fails (graph-up-unknown), the #2251 never-raise contract."""
        legacy_org = "acme-legacy-verdict"
        canonical_org = "acme-canonical-verdict"
        _mint_legacy_graph_with_content(legacy_org)
        _canon_sdk = ha_mod._make_sdk(namespace=canonical_org)
        try:
            _canon_sdk._get_proj()  # mint canonical
        finally:
            _canon_sdk.close()

        assert ha_mod._graph_has_org_namespace(legacy_org) is True
        assert ha_mod._graph_has_org_namespace(canonical_org) is True
        assert ha_mod._graph_has_org_namespace("acme-absent-999") is False

        # Probe failure stays fail-open — never-raise, True.
        real = ha_mod._registry_existing_graphs
        ha_mod._registry_existing_graphs = lambda: None
        try:
            assert ha_mod._graph_has_org_namespace(canonical_org) is True
            assert ha_mod._open_org_graph_sdk(canonical_org) is None
        finally:
            ha_mod._registry_existing_graphs = real
