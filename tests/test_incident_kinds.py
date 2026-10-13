"""#4780 — the incident-kind registry: one declaration site, asserted against
the code and the runbook in BOTH directions.

WHAT THIS REPLACES
------------------
The vocabulary used to be re-derived in five places (per-module constants, ~50
bare literals, ``AlertStore.KIND_OWNERS``, the bash driver's mirror, and the
runbook's hand-maintained parallel table), kept in step by hand and pinned by
three narrow, hand-listed tests:

* ``test_operator_alert.py::test_kind_constants_match_the_runbook`` — a tuple of
  8 kinds, asserting each has a row and a close-then-delete sentence;
* ``test_alert_store.py::test_documented_kinds_have_a_writer`` — a regex scan of
  the emitters, asserting every documented kind has one and exactly two dead
  kinds are declared;
* ``test_alert_store.py::test_dead_kinds_are_documented_as_dead`` — the same two
  dead kinds, from the other side.

Each was written for the kinds that happened to exist when it was added, so a NEW
kind was only covered if someone remembered to extend the list. These tests are
generated from ``tortoise/incident_kinds.py`` instead: a kind that is not
declared cannot be filed quietly, and a declaration that has drifted from the
runbook or from the emitters fails here.

⛔ WHY THE KIND STRING IS A FROZEN KEY
--------------------------------------
``ops/alerts/{kind}/{subject}.json`` is the create-once dedup linearization
point, and the kind is also the ``[DR] {kind}`` token the R2-unreachable
adoption path searches on. Renaming a kind as a "refactor" orphans the dedup
object (so the stale object keeps arming the long attempt window while Telegram —
which pushes only on FILED — never fires again) and makes a recurrence file a
SECOND issue (the adoption search matches the old title). The frozen set below
is the enforcement; the migration playbook is in the
``tortoise/incident_kinds.py`` module docstring.
"""

from __future__ import annotations

import ast
import re
import warnings
from pathlib import Path

import pytest

from tortoise import incident_kinds as ik

ROOT = Path(__file__).resolve().parent.parent
RUNBOOK = ROOT / "docs/ops/registry-backup-dr.md"
DRIVER = ROOT / ".github/scripts/registry-cron.sh"
REGISTRY_MODULE = "tortoise/incident_kinds.py"

#: Python roots scanned for (a) a hardcoded kind string and (b) the emitters of
#: each kind. ``tests/`` is excluded: a test asserting on a literal kind is
#: asserting on the SHAPE of an incident, not emitting one.
_SCAN_ROOTS = ("tortoise", "apps", "integrations", "graph-scripts", "battery",
               "tools", "capability", "engineering")

#: The files any kind's writer can live in — the alert plane. Deliberately a
#: list, not "every file under tortoise/": the emitted-flag check is only
#: meaningful if the scan covers the surfaces that can actually file.
_EMITTER_SOURCES = (
    "tortoise/alert_store.py",
    "tortoise/backup_watcher.py",
    "tortoise/backup_sweep.py",
    "tortoise/hosted_api.py",
    "tortoise/cohort_cost.py",
    "tortoise/operator_alert.py",
    "tortoise/notify.py",
    "tortoise/email_notify.py",
    "tortoise/abuse.py",
)

#: ⛔ The frozen kind STRINGS (#4780). These are durable storage keys, not
#: symbols — see the module docstring's migration playbook before touching one.
#: A failure here is not a lint: it means an open incident's dedup object was
#: just orphaned and its next recurrence will file a second issue.
FROZEN_KIND_STRINGS = frozenset({
    "STALE", "POOL_STALE", "NEVER_BACKED_UP", "METADATA_LOST",
    "BACKUP_SET_MISSING", "DRIVER_DOWN", "R2_DOWN", "ALERTER_DOWN",
    "APP_DOWN", "WATCHER_DOWN", "SWEEP_CONFIG_ERROR", "SWEEP_OFF_STALE",
    "SWEEP_NO_COVERAGE", "PURGE_FAILED", "RECONCILE_FAILED",
    "NO_ELIGIBLE_TEAMS", "ENUM_DELTA", "GRAPH_NAME_RESOLUTION_FAIL",
    "LIVENESS_NO_WORK", "SIZE_GUARD_ABORT", "DATA_LOSS_CANDIDATE",
    "P0_GUARD_FAIL", "RESTORE_DRILL_FAILED", "ANALYTICS_SINK_DEGRADED",
    "UNMETERED_INCREMENT", "COHORT_CAP_UNENFORCEABLE", "COHORT_COST_CAP",
    "ABUSE_DECISION_FAULT", "ABUSE_ENFORCEMENT_FAULT", "abuse_review_needed",
    "abuse_suspended", "BILLING_NOTIFY_REFUSED", "PROVIDER_BILLING_EXHAUSTED",
    "BILLING_SEND_FAILED", "INVITE_SEND_FAILED",
})

#: Exact-kind string literals outside the registry that are NOT incident
#: references — each with the reason it is not a kind. Pinned by equality, so
#: adding an entry is a reviewed act rather than a silent widening.
NON_INCIDENT_LITERALS: dict[tuple[str, int, str], str] = {
    ("tools/finding_provenance.py", 397, "STALE"):
        "a finding-provenance freshness verdict printed to stderr; the DR "
        "STALE incident is never named by this tool",
    ("tools/mergify_config_guard.py", 192, "STALE"):
        "a mergify staleness token for an exit code mapping "
        "(FRESH/STALE/UNKNOWN/DIGEST-DIVERGED), unrelated to DR alerts",
}


def _runbook_rows() -> dict[str, str]:
    """kind -> its triage-table row, from the §"Alert taxonomy + triage" table.

    Parsed to the NEXT ``###`` heading, so a row cannot be picked up from a
    later section (the `/status` diagnostic blocks have their own table).
    """
    lines = RUNBOOK.read_text(encoding="utf-8").splitlines()
    start = next(i for i, ln in enumerate(lines)
                 if ln.startswith("## Alert taxonomy + triage"))
    end = next(i for i in range(start + 1, len(lines))
               if lines[i].startswith("### "))
    rows: dict[str, str] = {}
    for ln in lines[start:end]:
        if not ln.startswith("| ") or ln.count("|") < 3:
            continue
        kind = ln.split("|")[1].strip()
        if not kind or kind == "Kind" or set(kind) <= set("- : "):
            continue
        rows[kind] = ln
    assert rows, "the alert-taxonomy table did not parse — its shape changed"
    return rows


def _registry_constants() -> dict[str, str]:
    """Every ``KIND_*`` string constant, re-derived from the module namespace.

    Re-derived HERE rather than read from ``incident_kinds``: the in-module
    closure check runs before the end of that module, so a constant appended
    after it would escape the import-time check and be caught only here.
    """
    return {name: value for name, value in vars(ik).items()
            if name.startswith("KIND_") and isinstance(value, str)}


def _emitted_kinds() -> set[str]:
    """The kinds any surface can actually file — scanned from the emitters.

    Mechanical on purpose: ``KindSpec.emitted`` is a declaration, and a
    declaration checked only against itself is a comment. Every emitting call
    site goes through a registry constant after #4780, so the scan resolves
    ``KIND_*`` identifiers back to their values; the bash driver is scanned for
    its bare tokens.
    """
    constants = _registry_constants()
    kinds: set[str] = set()
    for rel in _EMITTER_SOURCES:
        text = (ROOT / rel).read_text(encoding="utf-8")
        for name in set(re.findall(r"\bKIND_[A-Z0-9_]+\b", text)):
            if name in constants:
                kinds.add(constants[name])
    driver = DRIVER.read_text(encoding="utf-8")
    for pattern in (r"\bfile_alert\s+([A-Z][A-Z0-9_]+)\s",
                    r"\bresolve_global\s+([A-Z][A-Z0-9_]+)\s"):
        kinds |= {k for k in re.findall(pattern, driver) if k in ik.KIND_NAMES}
    return kinds


# ── the registry is internally complete ──────────────────────────────────────


def test_every_kind_constant_is_declared_and_unique():
    """A constant with no ``SPECS`` entry, or two constants on one value.

    The second half matters more than it looks: two constants sharing a value
    would make two DIFFERENT kinds the same R2 dedup key and the same adoption
    token, while the set comparison that guards the first half still passes.
    """
    constants = _registry_constants()
    assert set(constants.values()) == set(ik.KIND_NAMES), (
        "a KIND_* constant is missing from, or extra to, the registry: "
        f"{set(constants.values()) ^ set(ik.KIND_NAMES)}")
    assert len(constants) == len(set(constants.values())), (
        "two KIND_* constants share one value — the kind string IS the dedup "
        f"key: {sorted(n for n in constants if list(constants.values()).count(constants[n]) > 1)}")
    assert len(ik.SPECS) == len(ik.KIND_NAMES), "SPECS and KIND_NAMES disagree"


def test_kind_strings_are_frozen():
    """⛔ The rename guard (#4780). Read the docstring before "fixing" this."""
    assert ik.KIND_NAMES == FROZEN_KIND_STRINGS, (
        "the incident-kind vocabulary changed. A kind string is the R2 dedup "
        "key (ops/alerts/{kind}/{subject}.json) AND the [DR] {kind} adoption "
        "token, so a rename is a DATA MIGRATION, not a refactor: it orphans the "
        "open incident's dedup object and makes its next recurrence file a "
        "SECOND issue. If the change is deliberate, follow the migration "
        "playbook in tortoise/incident_kinds.py, then update this set in the "
        "same PR.")


def test_only_the_grandfathered_kinds_are_lowercase():
    """The SCREAMING_CASE convention, enforced for every kind added after #4780.

    ``abuse_review_needed`` and ``abuse_suspended`` predate the convention and
    are frozen as keys, so the convention is applied going forward rather than
    by a rename that would migrate two live dedup namespaces.
    """
    lowercase = {k for k in ik.KIND_NAMES if not k.isupper()}
    assert lowercase == set(ik.LEGACY_LOWERCASE_KINDS), (
        f"lowercase kinds changed: {lowercase} — a new kind must be "
        "SCREAMING_CASE; only the grandfathered spellings may be lowercase")


def test_no_kind_is_a_prefix_of_another():
    """The property the GH-search adoption path actually needs.

    ``github_issue.incident_title_matches`` verifies a hit by the ``[DR] {kind}``
    PREFIX, so a kind that is a prefix of another would let one incident's title
    parse as the other's — cross-adoption, and resolving one closes the other's
    issue. Note this is a PREFIX test, not a substring test: ``STALE`` IS a
    substring of ``POOL_STALE`` / ``SWEEP_OFF_STALE`` (measured 2026-10-13),
    which is benign precisely because the verifier is prefix-anchored and both
    of those cannot start with ``[DR] STALE``. The registry work in #5047 is
    free to add kinds; it is not free to add a prefix-colliding pair.
    """
    collisions = sorted(
        (a, b) for a in ik.KIND_NAMES for b in ik.KIND_NAMES
        if a != b and b.startswith(a))
    assert not collisions, (
        f"kind prefix collisions: {collisions} — the adoption search matches "
        "[DR] {kind} by prefix, so these two would cross-adopt")


def test_kind_owners_is_the_declared_owner_map():
    """``KIND_OWNERS`` is derived, not a second hand-maintained map (#3127)."""
    from tortoise.alert_store import KIND_OWNERS

    expected = {kind: spec.owner for kind, spec in ik.SPECS.items()
                if spec.owner != ik.WRITER_UNSPECIFIED}
    assert expected == KIND_OWNERS
    # Every kind has an explicit owner decision — "unspecified" is a decision,
    # not an omission — and only the four declared writers may appear.
    declared = {ik.WRITER_DRIVER, ik.WRITER_WATCHER, ik.WRITER_APP,
                ik.WRITER_UNSPECIFIED}
    assert {s.owner for s in ik.SPECS.values()} <= declared


# ── the runbook ⇄ code set equality, both directions ─────────────────────────


def test_every_runbook_row_is_declared_and_every_kind_has_a_row():
    """The generalization of the hand-pinned ``test_kind_constants_match_the_runbook``.

    Both directions fail: a row for a kind no code declares (a phantom the
    operator will never see) and a declaration with no row (an incident that
    pages with no triage) are the same drift.
    """
    rows = _runbook_rows()
    assert set(rows) == set(ik.KIND_NAMES), (
        f"runbook rows with no declared kind: {sorted(set(rows) - ik.KIND_NAMES)}; "
        f"declared kinds with no runbook row: {sorted(ik.KIND_NAMES - set(rows))}")


def test_manual_close_kinds_document_the_dedup_close_procedure():
    """``manual_close`` kinds must carry the close-then-delete procedure — and only they.

    An incident whose recovery is not programmatic cannot be cleared without the
    operator knowing that the GitHub issue is closed FIRST and the R2 dedup
    object deleted SECOND: deleting the object first leaves the issue open and
    re-arms a recurrence as a second issue. The converse assertion keeps a row
    from quietly gaining a manual procedure it does not need (or losing one it
    does).
    """
    rows = _runbook_rows()
    documented = {kind for kind, row in rows.items()
                  if "close the GitHub issue" in row and "then delete" in row}
    declared = {kind for kind, spec in ik.SPECS.items() if spec.manual_close}
    assert declared == documented, (
        f"manual-close declarations with no documented procedure: "
        f"{sorted(declared - documented)}; documented procedures that are not "
        f"declared manual_close: {sorted(documented - declared)}")


def test_writer_less_kinds_are_declared_and_carried_by_no_emitter():
    """``emitted=False`` must agree with a scan of the emitters (#3033), both ways.

    A declared-but-unwired kind is corrosive (the operator believes a condition
    will page when it cannot); an emitted kind that still declares itself dead
    is the same lie from the other side.
    """
    scanned = _emitted_kinds()
    declared = {kind for kind, spec in ik.SPECS.items() if spec.emitted}
    assert scanned == declared, (
        f"declared emitted but no emitter references it: "
        f"{sorted(declared - scanned)}; emitted by code but declared "
        f"writer-less: {sorted(scanned - declared)}")


def test_writer_less_kinds_are_marked_not_emitted_in_their_triage_cell():
    """The runbook's own marker, so the table cannot read as live (#3033)."""
    rows = _runbook_rows()
    for kind, spec in ik.SPECS.items():
        if spec.emitted:
            continue
        assert "NOT EMITTED" in rows[kind], (
            f"{kind} has no writer, so its triage cell must say NOT EMITTED")
        assert f"`{kind}`" in RUNBOOK.read_text(encoding="utf-8").split(
            "### Kinds with no writer", 1)[1], (
            f"{kind} must be listed under 'Kinds with no writer'")


# ── no bare kind literal, and the driver's tokens ────────────────────────────


def test_no_bare_kind_literal_outside_the_registry():
    """A kind string written by hand anywhere else is the drift this removes.

    Scans the Python source roots for a string literal EQUAL to a declared kind
    (an AST walk, so a dict value, a list entry and a call argument are all
    covered) and fails unless the occurrence is the declaration itself or a
    documented non-incident use. Docstrings are not flagged: they contain prose
    about a kind, not a literal equal to it.
    """
    offenders: set[tuple[str, int, str]] = set()
    for root in _SCAN_ROOTS:
        base = ROOT / root
        if not base.exists():
            continue
        for path in sorted(base.rglob("*.py")):
            if "test" in path.name:
                continue
            rel = str(path.relative_to(ROOT))
            if rel == REGISTRY_MODULE:
                continue
            try:
                with warnings.catch_warnings():
                    # A pre-existing invalid escape sequence in an unrelated
                    # module must not decorate this test's output.
                    warnings.simplefilter("ignore", SyntaxWarning)
                    tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:  # not our file to police
                continue
            for node in ast.walk(tree):
                if (isinstance(node, ast.Constant)
                        and isinstance(node.value, str)
                        and node.value in ik.KIND_NAMES):
                    offenders.add((rel, node.lineno, node.value))
    undocumented = sorted(offenders - set(NON_INCIDENT_LITERALS))
    assert not undocumented, (
        "a kind string is hardcoded outside the registry — import the "
        "tortoise.incident_kinds constant instead (the string IS the R2 dedup "
        f"key, so a typo files a phantom incident): {undocumented}")
    assert offenders <= set(NON_INCIDENT_LITERALS), (
        "an allowlisted non-incident literal disappeared — drop the entry")


def test_driver_kind_tokens_are_declared():
    """The bash driver files and resolves kinds too; its tokens are pinned.

    The driver cannot import Python, so its kind literals are the one place a
    string still has to be written by hand. This asserts every kind it names is
    declared, which is the half the cross-language ``kind_owner`` contract test
    (``tests/test_alert_store.py``) does not cover.
    """
    driver = DRIVER.read_text(encoding="utf-8")
    tokens: set[str] = set()
    for pattern in (r"\bfile_alert\s+([A-Z][A-Z0-9_]+)\s",
                    r"\bresolve_global\s+([A-Z][A-Z0-9_]+)\s",
                    r"for kind in ([A-Z_ ]+); do"):
        for match in re.findall(pattern, driver):
            tokens.update(match.split())
    assert tokens, "the driver's kind call sites did not parse — shape changed"
    assert tokens <= ik.KIND_NAMES, (
        f"the driver names undeclared kinds: {sorted(tokens - ik.KIND_NAMES)}")


def test_spec_lookup_is_strict_about_unknown_kinds():
    """``spec()``/``is_declared`` must not invent a declaration for a typo."""
    assert ik.is_declared(ik.KIND_STALE)
    assert not ik.is_declared("STALE_TYPO")
    assert ik.spec(ik.KIND_STALE).owner == ik.WRITER_WATCHER
    with pytest.raises(KeyError):
        ik.spec("STALE_TYPO")
