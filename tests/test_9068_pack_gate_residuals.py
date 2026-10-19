"""#9068 — the write paths that never meet a #5475 gated door, MEASURED.

`#5475`'s closure property is **a write may not widen the graph's approved
pack set**. At this branch's base that property is enforced at these THIRTEEN
doors and no others — stated as doors rather than as a cardinality, because a
count is the part that goes stale:

* the SDK point door (`sdk.create_point`) and the SDK entity funnel
  (`sdk._create_entity`, which `create_object`/`create_subject`/`create_event`/
  `create_document`/`create_source`/`create_entity` all relay through);
* the two UPDATE doors (`sdk.update_point`, `sdk._update_entity`, #9070);
* the second writers of an already-mined property — `sdk.link_source_to_entity`
  (its `sourceKind` keyword) and `sdk.ingest_corpus` (its `eventKind` keyword);
* the record-building doors that journal-then-fold — `sdk._apply_and_journal`
  and `sdk._doc_write`;
* the journal-first seam every pipeline writes through — `api.EventAPI._emit`
  (`#8982`) and `mining.ConversationMiner._emit_event`;
* the hosted commit door's pre-validation (`hosted_api.commit_session`), and the
  two `POST /v1/{objects,subjects}` doors (`hosted_api.create_object` /
  `hosted_api.create_subject`, via `_require_approved_kind`, #8380).

"And no others" is not prose either: `test_the_door_inventory_is_the_declared_doors_and_no_others`
re-derives the inventory from the #5475 gate CALL SITES by AST, so a FOURTEENTH
door reds this suite instead of silently falsifying this paragraph — and
`test_the_gate_helper_vocabulary_is_complete` reds it when a new gate HELPER
appears, which is the failure this file only missed once: `#9070` introduced
`_refuse_unapproved_kinds_in_record` and the doors behind it were invisible to
the call-site scan until the helper was registered in `_GATE_HELPERS`. It is an
inventory of the BASE, not of the future.
The decision is expressed more than once — the SDK's `_unapproved_kind_refusal`,
`pack_state.kind_namespace_is_approved`, and the approval-gated vocabulary
membership (`compile_vocab(installed_namespaces=…)`, which `validate_layer1`
tests for a namespaced `pointKind`/`eventKind`/`sourceKind` at the commit
door's Layer-1) — so a door inventory has to read the CALL SITES, not one
helper. A reader who takes "the write path is closed" literally will not go
looking, and several writers still widen the set today. `#9068` records that;
this module makes it a *measurement* instead of prose, so the claim's true
scope is executable and cannot drift back:

* six tests PASS. Four pin the SCOPE and the pin MECHANIC — the door inventory
  (below), the completeness of the gate vocabulary that inventory is derived
  from, the fail-closed mechanics the two pins rest on, and the gated door
  refusing (and moving nothing); one pins the deliberate data-mining leg the
  gate's back-compat rests on; and one is the **declared** departure (offline
  re-typing);
* two tests are `xfail(strict=True, raises=AssertionError)` on the **fixed**
  behaviour of the two residuals nothing is currently fixing, so closing either
  one XPASSes its pin and FAILS the suite on purpose rather than letting the pin
  rot — the idiom `tests/test_url_keyed_source_write_4649.py` uses for `#5048`.

Both pins are ``xfail(strict=True, raises=AssertionError)``, and each half of
that marker is load-bearing: ``strict`` alone reacts to the ASSERTION, so a pin
body that raised for ANY other reason would be recorded as an expected failure
forever — pytest swallows such a raise even when it comes from a FIXTURE, not
just from the body. So the pins

* tolerate exactly the two refusal shapes a fix can take at their own seam
  (``_REFUSAL``) and report every other exception as a FAILURE
  (``_attempt_write``), and
* keep their precondition in the fixture as an RAISED non-``AssertionError``
  (``_PreconditionNotMet``), which the report shows as an ERROR rather than an
  xfail — an ``assert`` there would be swallowed by the marker.

So a refusal-shaped fix XPASSes its pin and FAILS the suite (the signal to
delete the pin), and a pin whose precondition or seam silently changed shape
reds the suite instead of staying green and stale.

The approved set is `:PackInstall` records **∪** the namespaces mined out of
the graph's own data (`pack_state.graph_kind_namespaces`, 8 node properties,
label-agnostic). So "a write widened the set" is a one-line, observable fact:
read the set before, perform ONE write, read it after.

## What is measured here, and what is only NAMED

**Widening at HEAD, pinned here** (nothing is fixing these):

1. **`:Pipeline.kind` is a mined property** — `pipeline._write_pipeline` writes
   ``SET p.kind = $kind`` with the pipeline KEY as the value, and
   `graph_kind_namespaces` scans `kind` label-agnostically, so a procedural
   config row grants its namespace. Gating the writer alone is not available
   (the key IS a real ``ns:kind`` spelling, so a gate there would refuse
   legitimate pipeline edits), and dropping `kind` from the scanned keys
   narrows the back-compat union — so this is a **decision about the pipeline
   model**, recorded on `#9068`, not a mechanical patch. **XFAIL.**

2. **A bare fold on a journal-less projection** — `connectors/github.py` folds
   records with ``proj.apply(...)`` (kind ``pm:issue`` from `github_map`), and
   `FalkorProjection.apply` refuses a direct fold only when ``_journal_backed``
   — correctly, since with no journal there is no truth store for the cache to
   be ahead of. So on a journal-less projection the kind is persisted ungated.
   The fix must reuse `pack_state`'s ONE read core rather than re-deriving
   "records ∪ data" a second time (that re-derivation is the drift
   `graph_installed_namespaces` exists to prevent). **XFAIL.**

**Closed upstream at this base, and NOT re-pinned here** — `#9070` landed and
gates the eight seams this module previously only NAMED. This module does not
re-pin them: their pins live in `tests/test_vocab_gating_by_graph.py`
(`TestUpdateDoorsRefuseUnapprovedPacks5475`,
`TestJournalSeamRefusesUnapprovedPacks5475`,
`TestRecordBuildingSeamsRefuseUnapprovedPacks5475`,
`TestSecondWritersOfAKindProperty5475`, `TestIngestCorpusEventKind5475`) and in
`#9070`'s own before/after measurements. They are listed here because they are
DOORS in the inventory below, not because this module measures them:

* the UPDATE doors — `update_point` / `_update_entity` (`#8968`);
* the journal-first seam — `api.EventAPI._emit` → `projection._upsert_*`
  (`#8982`) — and `mining.ConversationMiner._emit_event`;
* the SECOND writers of an already-mined property — `link_source_to_entity`
  (its `sourceKind` keyword) and `ingest_corpus` (its `eventKind` keyword, on a
  raw-Cypher writer);
* the record-building doors — `sdk._apply_and_journal`, `sdk._doc_write`.

**Deliberately ungated, DECLARED** (3) — `migrate_kinds` re-types with raw
Cypher, and `retype` / `tools/retype_snapshot` work on a dump. `#9068` residual
3 asks for exactly this and nothing more: *"a migration is a deliberate
re-typing, so this may be acceptable by design — but it must be STATED, because
the PR that closes #5475 says the write path is closed and a reader will take
that literally."* The last test is that statement, made executable.

Lane: **both legs, and NOT the same shape on each** — stated precisely, because
the construction idiom invites the wrong assumption. The fixture builds its own
``TortoiseSDK`` on ``tmp_path`` (the `tests/test_pipeline_2792.py` construction
idiom), but only the URI-UNSET leg (tier-2 PR / ``test-carve-out``) honours that
path and gets an embedded store. On the URI-SET leg (``TORTOISE_DB_URI`` +
``TORTOISE_TEST_MODE=1``, seeded by ``tests/conftest.py``) the Epic #1647
class-level test redirect (`projection/__init__.py`, gated on
``_is_supported_uri_scheme`` + caller stem ∉ ``TORTOISE_TEST_NO_REDIRECT``)
discards the explicit path and binds a per-test derived server graph
(``test_<sha1>``). The stem is deliberately NOT in
`tests/_embedded.TEST_NO_REDIRECT_STEMS` or the ``carve_out`` surface, because
NOTHING asserted here is engine-specific: journal-less-ness comes from the SDK
being built without ``event_log_path`` (``_bind_journal(None)``), not from the
engine, and every assertion reads the approval set. Registering the file
embedded-only (`test_pipeline_2792`'s option) would run the measurement on ONE
lane instead of both; the honest cost of running it on both is that a URI-set
run mints five per-test throwaway graphs on the shared server — the `#5084`
accumulation surface every ``tmp_path``-SDK test already sits on.

Registered in ``config/ci-surfaces.yml`` (``core``, DUAL-REGISTERED with
``api`` and ``onboarding``), beside the pack tests it reads. The extra arms are
not decoration: the inventory walks ALL of ``tortoise/**``, and three of the
thirteen doors live in ``tortoise/hosted_api.py``, an EXACT ``api``
SOURCE_PATTERN match that REPLACES the ``core`` fallback in
``tools/ci_selection.py::select`` — so a ``hosted_api.py``-only diff would
otherwise run every guard EXCEPT this one, and a stale inventory would merge
green (the #1349/#3332/#3616 silent-drop class).
``test_the_inventory_runs_on_a_diff_to_every_module_it_scans`` pins the
selection, derived from ``_DOORS``, so this cannot rot back.
"""

from __future__ import annotations

import ast
import contextlib
import re
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise import pack_state  # noqa: E402, RUF100
from tortoise.migrate_kinds import migrate  # noqa: E402, RUF100
from tortoise.pipeline import seed_pipeline  # noqa: E402, RUF100
from tortoise.projection import DirectCacheWriteRefused  # noqa: E402, RUF100
from tortoise.sdk import (  # noqa: E402, RUF100
    TortoiseSDK,
    UnapprovedKindNamespace,
)

#: A minimal declared pipeline — the shape ``seed_pipeline`` materialises from a
#: pack default (``pipeline.py::_pipeline_from_decl``).
_PIPELINE_DECL: dict = {
    "defaultStage": "stg_contact",
    "stages": [
        {"id": "stg_contact", "label": "Contact", "order": 1, "slot": "entry"},
        {"id": "stg_won", "label": "Won", "order": 2, "slot": "terminal",
         "outcome": "won"},
    ],
}

#: The one namespace this fixture graph has approved. Every residual test asks
#: the same question — can ONE write add a namespace this graph never approved?
_DEV_ONLY: tuple[dict, ...] = ({"namespace": "dev", "status": "active"},)

#: The refusals a fix can raise AT THE TWO SEAMS PINNED BELOW, and the ONLY
#: ones a pin body tolerates: the gate's own exception
#: (``pack_state.unapproved_kind_refusal`` → ``UnapprovedKindNamespace``) or the
#: fold path's own guard (``DirectCacheWriteRefused``). Tolerating them is what
#: makes a refusal-shaped fix XPASS the pin instead of leaving it an expected
#: failure forever.
#:
#: The tolerance is deliberately NARROW, and that is safe in the fail-closed
#: direction because it is not the only guard: ``_attempt_write`` reports every
#: OTHER exception as a FAILURE (the pins carry ``raises=AssertionError``, so an
#: exception that is not the widening assertion cannot be laundered into an
#: xfail). A projection-seam guard that invents its own type — or reuses the
#: plain ``ValueError`` that ``UnapprovedKindNamespace`` subclasses, which is the
#: shape a fixer reaches for — therefore REDS this suite: the intended signal
#: that the seam changed and the pin must be re-evaluated, not a silent expiry.
_REFUSAL = (UnapprovedKindNamespace, DirectCacheWriteRefused)


class _PreconditionNotMet(RuntimeError):
    """A fixture precondition, RAISED rather than asserted — on purpose.

    An ``assert`` in a fixture is not enough here. ``xfail`` swallows
    SETUP-phase ``AssertionError``s exactly as it swallows call-phase ones
    (verified on the pytest this repo pins, 9.1.1: a fixture ``assert`` under an
    ``xfail``-marked test is reported XFAIL, not ERROR), so an invited
    ``assert`` would let the marker mask precisely the precondition these pins
    depend on. A NON-``AssertionError`` reaches the report as an ERROR under the
    pins' ``raises=AssertionError``, so the precondition cannot be swallowed
    when ``set_graph_packs`` / ``graph_installed_namespaces`` change shape.
    """


def _attempt_write(write: Callable[[], object]) -> None:
    """Run ONE write at a pinned seam, tolerating a refusal and nothing else.

    A pin must XPASS when the seam starts REFUSING (a fix landed) and must FAIL
    when the seam breaks in any other way. ``xfail(..., raises=AssertionError)``
    gives the second half: an exception that is not the widening assertion is
    reported FAILED, not xfailed — and ``pytest.fail`` below raises ``Failed``,
    which is an ``OutcomeException`` and NOT an ``AssertionError``, so this
    catch-all cannot itself be swallowed by the marker.
    """
    try:
        write()
    except _REFUSAL:
        # A refusal-shaped fix landed: the widening assertion below now passes,
        # which XPASSes the strict pin and reds the suite on purpose.
        pass
    except Exception as exc:
        pytest.fail(
            f"unexpected failure at the seam, and NOT one of the tolerated "
            f"refusals {tuple(t.__name__ for t in _REFUSAL)}: {exc!r}")


@pytest.fixture()
def dev_only_sdk(tmp_path: Path) -> Iterator[TortoiseSDK]:
    """An embedded-shaped graph whose approval set is EXACTLY ``{'dev'}``.

    Deliberately constructed WITHOUT ``event_log_path``: that is what makes the
    projection journal-less (``_journal_backed is False``), i.e. the shape the
    connectors run on and the shape residual 2 is about. The recorded ``dev``
    ``:PackInstall`` is what makes the set a real gate rather than the ``None``
    "no records ⇒ catalog union" back-compat answer (#2714 indicator 3) —
    under ``None`` NOTHING can widen the set and every test here would pass for
    the wrong reason, so both preconditions are checked on EVERY construction,
    in a fixture, as raises (``_PreconditionNotMet``) rather than asserts.
    """
    sdk = TortoiseSDK(str(tmp_path / "residuals.db"))
    try:
        pack_state.set_graph_packs(sdk, packs=[dict(p) for p in _DEV_ONLY])
        approved = pack_state.graph_installed_namespaces(sdk)
        if approved != frozenset({"dev"}):
            raise _PreconditionNotMet(
                "fixture precondition: the graph must start approved for 'dev' "
                f"only, got {sorted(approved or ())}")
        if sdk._get_proj()._journal_backed is not False:
            raise _PreconditionNotMet(
                "fixture precondition: this must be the JOURNAL-LESS shape "
                "residual 2 is about (SDK built without event_log_path) — a "
                "journal-backed projection already refuses a direct fold")
        yield sdk
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


def _approved(sdk: TortoiseSDK) -> frozenset[str] | None:
    return pack_state.graph_installed_namespaces(sdk)


# ── The scope claim: the door inventory, RE-DERIVED from the source ───────

#: The `#5475` gate helpers. A DOOR is a function that CALLS one of these; the
#: helpers are not doors themselves, so a call inside another helper (the SDK's
#: `_refuse_*` / `_unapproved_kind_refusal` wrappers,
#: `EventAPI._refuse_unapproved_kinds`, `hosted_api._require_approved_kind`,
#: `pack_state.refuse_unapproved_kinds_in_payload`) is a composition step, not a
#: door. `unapproved_kind_pack` is here too: it is the pure decision helper the
#: refusal composes, so nothing may treat it as a door.
#:
#: Kept in step with the source by
#: `test_the_gate_helper_vocabulary_is_complete`, which re-derives every DEF
#: whose NAME is in the gate vocabulary and reds when one is unregistered. That
#: guard exists because the call-site scan is only as complete as this list:
#: `#9070` added `_refuse_unapproved_kinds_in_record`, and every door behind it
#: was invisible to this scan until the name was registered here.
_GATE_HELPERS: frozenset[str] = frozenset({
    "_refuse_unapproved_kind",
    "_refuse_unapproved_kinds",
    "_refuse_unapproved_kinds_in_props",
    "_refuse_unapproved_kinds_in_record",
    "_unapproved_kind_refusal",
    "_require_approved_kind",
    "refuse_unapproved_kinds_in_payload",
    "unapproved_kind_pack",
    "unapproved_kind_refusal",
})

#: The naming convention the #5475 gate is written in — derived from the source,
#: so a new helper cannot arrive unannounced. Anything matching this that is not
#: in `_GATE_HELPERS` reds `test_the_gate_helper_vocabulary_is_complete`.
_GATE_VOCABULARY = re.compile(
    r"^(?:.*_)?(?:refuse_unapproved\w*|unapproved_kind\w*|require_approved_kind)$")

#: The doors the module docstring states — that header's inventory, executable.
#: THIRTEEN at this base: the two SDK create funnels, the two UPDATE doors, the
#: two second-writers of a mined property, the two record-building doors, the two
#: journal-first emitters, the commit-door pre-check and the two REST doors
#: (`#9070` landed the update/journal seams; `#8380` the REST pair). "And no
#: others" is a claim about the BASE: the day a new gate CALL SITE — or a new
#: gate HELPER, see `_GATE_VOCABULARY` — lands, this reds and the header has to
#: be updated in the same PR, which is the point. A paragraph asserting a scope
#: nobody checks back is the drift that #6139/#7551 hit from the manifest side
#: and that this module exists to close.
_DOORS: frozenset[str] = frozenset({
    "tortoise/api.py:EventAPI._emit",
    "tortoise/hosted_api.py:commit_session",
    "tortoise/hosted_api.py:create_object",
    "tortoise/hosted_api.py:create_subject",
    "tortoise/mining.py:ConversationMiner._emit_event",
    "tortoise/sdk.py:TortoiseSDK._apply_and_journal",
    "tortoise/sdk.py:TortoiseSDK._create_entity",
    "tortoise/sdk.py:TortoiseSDK._doc_write",
    "tortoise/sdk.py:TortoiseSDK._update_entity",
    "tortoise/sdk.py:TortoiseSDK.create_point",
    "tortoise/sdk.py:TortoiseSDK.ingest_corpus",
    "tortoise/sdk.py:TortoiseSDK.link_source_to_entity",
    "tortoise/sdk.py:TortoiseSDK.update_point",
})


class _GateCallScan(ast.NodeVisitor):
    """Collect #5475 gate calls, keyed by the ENCLOSING function.

    AST rather than grep on purpose: a text scan cannot tell a helper's own
    internals from a door, cannot see a call through an alias, and reports
    comment mentions as call sites (the docstrings here name every helper).
    """

    def __init__(self, prefix: str,
                 out: dict[str, list[tuple[str, int]]]) -> None:
        self._prefix = prefix
        self._out = out
        self._scope: list[str] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._scope.append(node.name)
        self.generic_visit(node)
        self._scope.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._scope.append(node.name)
        self.generic_visit(node)
        self._scope.pop()

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        name = getattr(func, "id", None) or getattr(func, "attr", None)
        if name in _GATE_HELPERS:
            key = f"{self._prefix}:{'.'.join(self._scope)}"
            self._out.setdefault(key, []).append((name, node.lineno))
        self.generic_visit(node)


def _gate_call_sites() -> dict[str, list[tuple[str, int]]]:
    """Every ``#5475`` gate call under ``tortoise/``, keyed ``<file>:<qualname>``.

    The pre-filter (a file has to SPELL a helper to call it) only keeps the
    parse set to the modules that gate; the AST pass decides.
    """
    root = Path(__file__).resolve().parent.parent
    sites: dict[str, list[tuple[str, int]]] = {}
    for path in sorted((root / "tortoise").rglob("*.py")):
        source = path.read_text(encoding="utf-8", errors="replace")
        if not any(helper in source for helper in _GATE_HELPERS):
            continue
        scan = _GateCallScan(path.relative_to(root).as_posix(), sites)
        scan.visit(ast.parse(source))
    return sites


def _gate_vocabulary_defs() -> set[str]:
    """Every DEF under ``tortoise/`` whose NAME is in the ``#5475`` vocabulary.

    Deliberately NOT the same filter as `_gate_call_sites`: a new helper may be
    DEFINED in a module that spells none of the registered names (that is
    exactly how `_refuse_unapproved_kinds_in_record` would have been missed had
    it been the only new name), so the pre-filter here keys on the vocabulary
    stem rather than on the registered set.
    """
    root = Path(__file__).resolve().parent.parent
    defs: set[str] = set()
    for path in sorted((root / "tortoise").rglob("*.py")):
        source = path.read_text(encoding="utf-8", errors="replace")
        if "unapproved" not in source and "approved_kind" not in source:
            continue
        for node in ast.walk(ast.parse(source)):
            if (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and _GATE_VOCABULARY.match(node.name)):
                defs.add(node.name)
    return defs


def test_the_door_inventory_is_the_declared_doors_and_no_others() -> None:
    """The header's scope claim, made executable — "these doors and no others".

    A door is a function that CALLS a #5475 gate helper; a call inside another
    helper is composition, so it is excluded by leaf name. If a fourteenth gate
    call site lands, this reds and the header must be updated in the same PR
    rather than left asserting a scope the source no longer has.
    """
    doors = {
        key for key in _gate_call_sites()
        if key.rsplit(":", 1)[1].rsplit(".", 1)[-1] not in _GATE_HELPERS
    }

    assert doors == set(_DOORS), (
        "the module docstring's door inventory went stale: a #5475 gate call "
        f"site was added or removed. Live doors: {sorted(doors)}; stated: "
        f"{sorted(_DOORS)}. Update the header — and if the new door WIDENS the "
        "approved set, pin it beside the residuals below")


def test_the_gate_helper_vocabulary_is_complete() -> None:
    """The inventory above is only as complete as `_GATE_HELPERS`.

    A DOOR is found by the helper it CALLS, so a gate helper nobody registered
    is a whole family of doors hidden from `test_the_door_inventory_...` — the
    scan does not crash, it silently reports fewer doors. That is not
    hypothetical: `#9070` introduced `_refuse_unapproved_kinds_in_record`, and
    the two doors behind it (`_apply_and_journal`, `_doc_write`) plus the two
    that carried their own `_refuse_unapproved_kinds_in_props` call were
    invisible until the name was added.

    So the vocabulary is DERIVED from the source, not trusted from the list: any
    DEF whose name matches `_GATE_VOCABULARY` must be registered, and every
    registered name must still exist. A new helper reds this — which is the
    correct signal that the inventory needs re-reading, not an expiry.
    """
    live = _gate_vocabulary_defs()

    assert live == set(_GATE_HELPERS), (
        "the #5475 gate vocabulary changed: a helper was added or renamed "
        f"without being registered in `_GATE_HELPERS`. Live: {sorted(live)}; "
        f"registered: {sorted(_GATE_HELPERS)}. Register the name (its DOORS "
        "then reappear in the inventory test) or, if it is not a gate, rename "
        "it so the vocabulary stays decidable")


def test_the_inventory_runs_on_a_diff_to_every_module_it_scans() -> None:
    """A guard the diff that falsifies it does not RUN is not a guard.

    ``_gate_call_sites`` walks ALL of ``tortoise/**``, so the change that can
    add a fourteenth door is a change to ANY module — not only to the ones that
    happen to hold the thirteen today. Registration is per SURFACE, and an
    exact ``SOURCE_PATTERNS`` match (``tortoise/hosted_api.py`` and
    ``tortoise/__main__.py`` → ``api``+``onboarding``) REPLACES the ``core``
    fallback that was this file's only arm: before those arms, a
    ``hosted_api.py``-only PR — where THREE of the thirteen doors live —
    selected ``['api', 'onboarding']`` and ran every guard except this one, so a
    stale ``_DOORS`` merged green and reddened ``main`` only on the next full
    run (the #1349/#3332/#3616 silent-drop class).

    Derived from ``_DOORS`` rather than from a hand-kept module list, so when a
    door lands in a NEW module this pin asks the same question about it.
    """
    from tools.ci_selection import load_manifest, select

    manifest = load_manifest()
    this_file = Path(__file__).name
    doors_by_module: dict[str, int] = {}
    for door in _DOORS:
        module = door.split(":", 1)[0]
        doors_by_module[module] = doors_by_module.get(module, 0) + 1

    missing: dict[str, tuple[list[str], int]] = {}
    for module, door_count in sorted(doors_by_module.items()):
        sel = select([module], "pull_request", manifest)
        selected = (sel["full"] or sel["test_files"] == "ALL"
                    or this_file in set(sel["test_files"]))
        if not selected:
            missing[module] = (sel["surfaces"], door_count)

    assert not missing, (
        "a diff to a module this inventory scans does not SELECT this file, so "
        "a fourteenth door (or a moved one) there would merge green: "
        + "; ".join(
            f"{module} holds {n} door(s) and selects {surfaces}"
            for module, (surfaces, n) in missing.items())
        + ". Register this file on that surface in config/ci-surfaces.yml (the "
          "`test_7951_write_doors.py` precedent) rather than only in `core`")


def test_the_pin_mechanic_is_fail_closed() -> None:
    """The two guards that make the two pins above mean anything, exercised.
    (a) The fixture precondition must NOT be an ``AssertionError``. Under
        ``xfail(strict=True, raises=AssertionError)`` a raised assertion — in
        the body OR out of a fixture — is recorded as ``xfailed`` (verified on
        the pytest this repo pins, 9.1.1), so an ``assert`` precondition would
        green the suite with the pin measuring nothing.
    (b) ``_attempt_write`` must TOLERATE a refusal (a refusal-shaped fix is the
        XPASS that reds the suite on purpose) and report every other exception
        as a FAILURE — ``pytest.fail`` raises ``Failed``, which the
        ``raises=AssertionError`` marker cannot swallow.
    """
    assert not issubclass(_PreconditionNotMet, AssertionError), (
        "a fixture precondition that is an AssertionError is swallowed by the "
        "pins' `raises=AssertionError` marker — see the class docstring")

    def _raise(exc: Exception) -> None:
        raise exc

    _attempt_write(lambda: _raise(UnapprovedKindNamespace(
        "probe", prop="pointKind", kind="marketing:probe",
        namespace="marketing", installed=frozenset({"dev"}))))
    _attempt_write(lambda: _raise(DirectCacheWriteRefused("journal-backed")))

    with pytest.raises(pytest.fail.Exception, match="NOT one of the tolerated"):
        _attempt_write(
            lambda: _raise(ValueError("a seam guard nobody predicted")))


# ── The control: the gate holds where the closure claim actually applies ──


def test_a_live_door_refuses_an_unapproved_pack_and_moves_nothing(
        dev_only_sdk: TortoiseSDK) -> None:
    """`#5475`'s gate, at a LIVE door — this is the half the closure claim owns.

    ``create_point`` is one of the gated funnels, so the ``marketing:`` spelling
    that widens the set through the residual seams below must be REFUSED here,
    and the refusal must land BEFORE any write (a refusal that left the widening
    behind would be indistinguishable from no gate at all).
    """
    sdk = dev_only_sdk

    with pytest.raises(UnapprovedKindNamespace):
        sdk.create_point("marketing:contentPerformance", "probe content")

    assert _approved(sdk) == frozenset({"dev"}), (
        "the refusal must land before the write: the approved set is unmoved")


# ── The deliberate back-compat the gate rests on (#2714 indicator 2) ──────


def test_the_data_leg_keeps_existing_vocabulary_writable(
        dev_only_sdk: TortoiseSDK) -> None:
    """The mining is deliberate, so "stop mining" is not a free change.

    A namespace already present in the graph's DATA stays in the approval set —
    otherwise a pack that was uninstalled while its kinds stayed all over the
    graph would make its own rows un-updatable. This test asserts the property
    the mining exists for: after a row carrying an unapproved ``marketing:``
    kind lands, a WRITE of that same kind SUCCEEDS instead of being refused.
    The ``:Pipeline`` residual above is what makes narrowing the scan tempting;
    this is the cost that makes it a decision (the scanned key set is declared
    in ``pack_state._KIND_PROP_KEYS`` and pinned by
    ``tests/test_vocab_gating_by_graph.py``).
    """
    sdk = dev_only_sdk
    sdk._get_proj().g.query(
        "CREATE (n:Object {id:'o-existing', name:'ExistingProbe', "
        "objectKind:'marketing:product'})")

    created = sdk.create_object("WritableProbe", objectKind="marketing:product")

    assert created and created.get("id"), (
        "the data leg must keep the graph able to write the vocabulary its own "
        "data already holds (#2714 indicator 2)")


# ── Residual 1: :Pipeline.kind is a mined property ────────────────────────


# `raises=AssertionError`: ONLY the widening assertion below may xfail this
# pin. A refusal outside `_REFUSAL`, or any other failure at the seam, is
# reported FAILED by `_attempt_write` — never recorded as an expected failure.
@pytest.mark.xfail(strict=True, raises=AssertionError, reason=(
    "#9068 residual 1 — `pipeline._write_pipeline` persists the pipeline KEY "
    "into the mined `kind` property (`SET p.kind=$kind`) and "
    "`pack_state.graph_kind_namespaces` scans `kind` label-agnostically, so a "
    ":Pipeline row grants its namespace; the fix is a decision about the "
    "pipeline model, recorded on #9068"))
def test_a_pipeline_row_does_not_grant_its_namespace(
        dev_only_sdk: TortoiseSDK) -> None:
    """(1) Failing value is the approval set gaining ``venture``; (2) the
    fixture is a graph holding only a ``dev`` ``:PackInstall`` plus ONE
    pipeline seeded for the key ``venture:deal`` — a real ``ns:kind`` spelling,
    which is why the writer cannot simply be gated as well."""
    sdk = dev_only_sdk

    _attempt_write(
        lambda: seed_pipeline(
            sdk._get_proj().g, "venture:deal", _PIPELINE_DECL))

    assert "venture" not in (_approved(sdk) or frozenset()), (
        "a :Pipeline row is procedural config, not an ontology-kind write: it "
        "must not install the `venture` pack")


# ── Residual 2: a bare fold on a journal-less projection ──────────────────


# `raises=AssertionError`: same mechanic as residual 1's pin — only the
# widening assertion xfails; a refusal outside `_REFUSAL` or any other seam
# failure is FAILED, not silently xfailed. The journal-less precondition lives
# in the fixture (an in-body `assert` would be swallowed by this marker).
@pytest.mark.xfail(strict=True, raises=AssertionError, reason=(
    "#9068 residual 2 — `projection.apply` gates a direct fold only when "
    "`_journal_backed`, so on a journal-less projection (the connectors' "
    "shape) an `object_kind` is persisted ungated and widens the set"))
def test_a_journal_less_fold_does_not_widen_the_approval_set(
        dev_only_sdk: TortoiseSDK) -> None:
    """(1) Failing value is the approval set gaining ``pm``; (2) the fixture is
    a fold through the seam the connectors actually use — ``connectors/github``
    → ``proj.apply(entities["object"])`` — carrying the kind ``github_map``
    stamps on an issue Object (``pm:issue``), on a projection with NO journal.
    """
    sdk = dev_only_sdk
    proj = sdk._get_proj()

    _attempt_write(
        lambda: proj.apply({"type": "ObjectRegistered", "id": "o-fold-1",
                            "name": "FoldProbe", "object_kind": "pm:issue"}))

    assert "pm" not in (_approved(sdk) or frozenset()), (
        "a fold persists a mined kind property, so with no journal to gate it "
        "the fold itself must consult the graph's approval set")


# ── Residual 3: offline re-typing is DELIBERATELY ungated ─────────────────


def test_offline_retype_is_declared_ungated_by_design(
        dev_only_sdk: TortoiseSDK) -> None:
    """The DECLARED departure: a migration DOES widen the set, on purpose.

    ``#9068`` residual 3 asks for exactly this and nothing more — *"a migration
    is a deliberate re-typing, so this may be acceptable by design — but it
    must be STATED, because the PR that closes #5475 says the write path is
    closed and a reader will take that literally."* This test is the statement,
    made executable: the widening is real, and it is an operator's own
    re-typing of vocabulary the graph already stores, not a request-carried
    namespace. Gating it would refuse the repair of data that exists.

    Unlike the two pins above this one PASSES, because the behaviour is the
    declared posture rather than a defect awaiting a fix. If the posture ever
    changes to "gate the migration too", this test will fail — and that is the
    point: the departure cannot become silent again.
    """
    sdk = dev_only_sdk
    sdk._get_proj().g.query(
        "CREATE (n:Object {id:'o-legacy', name:'LegacyProbe', "
        "objectKind:'product'})")

    migrate(sdk, dry_run=False)

    assert "product-strategy" in (_approved(sdk) or frozenset()), (
        "the declared posture: an offline re-typing persists the migrated kind "
        "through raw Cypher, with no door and no gate")
    assert sdk._get_proj().g.query(
        "MATCH (n:Object {id:'o-legacy'}) RETURN n.objectKind"
    ).result_set[0][0] == "product-strategy:product", (
        "and the migration actually did the re-typing it is declared to do")
