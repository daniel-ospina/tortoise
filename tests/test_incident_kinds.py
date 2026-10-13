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
import json
import os
import re
import subprocess
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
    "BACKUP_SET_MISSING", "DRIVER_DOWN", "R2_DOWN", "R2_UNLISTABLE",
    "ALERTER_DOWN", "APP_DOWN", "WATCHER_DOWN", "SWEEP_CONFIG_ERROR",
    "SWEEP_OFF_STALE",
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


# ── #3146: the recovery-window declaration + its bash mirror ────────────────

#: ⛔ The kinds that carry a recovery window (#3146). Explicit and pinned, like
#: ``FROZEN_KIND_STRINGS``: the boundary between "a threshold-driven measurement
#: that can flap" and "everything else" is a DECISION, and a new kind landing on
#: the wrong side of it is exactly what this set is here to catch. A failure
#: means either the boundary moved (argue it here) or a kind was mis-declared.
DR_RECOVERY_KINDS_3146 = frozenset({
    # #3147 split R2 into two tiers; BOTH are bounded measurements that can
    # hover at their boundary, so both carry the window (R2_DOWN is the
    # watcher-owned reachability tier, R2_UNLISTABLE the driver-owned listing
    # tier).
    "R2_DOWN", "R2_UNLISTABLE", "APP_DOWN", "WATCHER_DOWN",
    "SWEEP_CONFIG_ERROR", "SWEEP_OFF_STALE", "SWEEP_NO_COVERAGE", "POOL_STALE",
    "PURGE_FAILED", "RECONCILE_FAILED", "STALE", "NEVER_BACKED_UP",
    "METADATA_LOST", "BACKUP_SET_MISSING", "DRIVER_DOWN",
})


def test_dr_recovery_plane_is_pinned():
    """The recovery window is per kind, and the plane is a reviewed decision.

    Two directions, both load-bearing: a kind that SHOULD carry a window but
    does not is cleared by one lucky poll (the defect #3146 fixes), and a kind
    that should NOT carry one (a human-triggered re-baseline, an app-owned
    trigger with its own threshold) would silently need its operator to act
    twice. ``resolve_streak`` for every non-plane kind is therefore pinned to 1.
    """
    assert ik.DR_RECOVERY_KINDS == DR_RECOVERY_KINDS_3146, (
        "the recovery-window plane changed. If deliberate, update BOTH this "
        "pin and the runbook narrative; the boundary is a decision, not an "
        "accident of a field default.")
    for kind, spec_ in ik.SPECS.items():
        if kind in DR_RECOVERY_KINDS_3146:
            assert spec_.resolve_streak == ik.DR_RECOVERY_STREAK, kind
        else:
            assert spec_.resolve_streak == 1, (
                f"{kind} is outside the threshold-driven DR plane, so it must "
                "keep the field default (1)")


def test_resolve_streak_config_contract(caplog):
    """#3146 config precedence, and the two failure directions of a bad value.

    Precedence: global ``ALERT_RECOVERY_STREAK`` > per-kind
    ``ALERT_RECOVERY_STREAKS`` > the legacy ``BACKUP_POOL_RESOLVE_STREAK`` (the
    POOL_STALE-only #4994 knob) > the declared value. A non-integer OR a
    NEGATIVE value is IGNORED with a warning (the declared value stands); ``0``
    is read as 1 with a warning, because a window of zero would mean "never
    resolve". A negative is a typo, not the ``1`` "clear now" spelling, so it
    must not defeat the hysteresis — the bash twin agrees (#3146 review P2).
    """
    import logging

    assert ik.resolve_streak_for("R2_DOWN", {}) == 2
    assert ik.resolve_streak_for("DATA_LOSS_CANDIDATE", {}) == 1
    assert ik.resolve_streak_for("POOL_STALE", {"BACKUP_POOL_RESOLVE_STREAK": "3"}) == 3
    assert ik.resolve_streak_for(
        "R2_DOWN", {"ALERT_RECOVERY_STREAKS": "R2_DOWN=4,STALE=7"}) == 4
    assert ik.resolve_streak_for(
        "STALE", {"ALERT_RECOVERY_STREAKS": "R2_DOWN=4,STALE=7"}) == 7
    # global wins over per-kind
    assert ik.resolve_streak_for(
        "R2_DOWN", {"ALERT_RECOVERY_STREAKS": "R2_DOWN=4",
                    "ALERT_RECOVERY_STREAK": "5"}) == 5
    # the per-kind contract wins over the legacy POOL_STALE alias
    assert ik.resolve_streak_for(
        "POOL_STALE", {"BACKUP_POOL_RESOLVE_STREAK": "3",
                       "ALERT_RECOVERY_STREAKS": "POOL_STALE=4"}) == 4
    with caplog.at_level(logging.WARNING, logger="tortoise.incident_kinds"):
        assert ik.resolve_streak_for(
            "R2_DOWN", {"ALERT_RECOVERY_STREAKS": "R2_DOWN=x"}) == 2
        assert ik.resolve_streak_for("R2_DOWN", {"ALERT_RECOVERY_STREAK": "0"}) == 1
        assert ik.resolve_streak_for("R2_DOWN", {"ALERT_RECOVERY_STREAK": "-4"}) == 2
        # ``-0`` IS zero, so it is not the negative branch: below-1 → 1.
        assert ik.resolve_streak_for("R2_DOWN", {"ALERT_RECOVERY_STREAK": "-0"}) == 1
    assert caplog.text.count("is not an integer") == 1, caplog.text
    assert caplog.text.count("below 1") == 2, caplog.text
    assert caplog.text.count("is negative") == 1, caplog.text


def test_recovery_streak_contract_with_driver():
    """The bash ``recovery_streak_declared`` must agree with the registry.

    Two writers, two languages, one window policy. If the maps drift, one side
    starts clearing an incident a window early (or late) — the #3146 defect from
    the other direction — so the DECLARED per-kind window is pinned across the
    language boundary, exactly like ``kind_owner``/``KIND_OWNERS``.
    """
    sh = DRIVER.read_text(encoding="utf-8")
    body = sh.split("recovery_streak_declared() {", 1)[1].split("esac", 1)[0]
    parsed: dict[str, int] = {}
    pending: list[str] = []
    for raw in body.splitlines():
        line = raw.strip()
        if re.fullmatch(r"[A-Z0-9_|]+\)", line):
            pending = line[:-1].split("|")
            continue
        m = re.fullmatch(r"echo (\d+) ;;", line)
        if m and pending:
            parsed.update(dict.fromkeys(pending, int(m.group(1))))
            pending = []
    assert parsed, "recovery_streak_declared() did not parse — the shape changed"
    declared_windows = {k: v for k, v in parsed.items() if v > 1}
    assert declared_windows == {k: 2 for k in DR_RECOVERY_KINDS_3146}, (
        f"the driver's declared windows disagree with the registry: "
        f"{declared_windows} vs {sorted(DR_RECOVERY_KINDS_3146)}")
    # Every kind the driver names in its DECLARED case is a real kind, and the
    # declared windows match the Python declaration exactly.
    assert set(parsed) <= ik.KIND_NAMES
    for kind, window in parsed.items():
        assert ik.SPECS[kind].resolve_streak == window, kind


def _extract_function(sh: str, name: str) -> str:
    """Extract a top-level bash function (its closing brace at column 0)."""
    match = re.search(rf"^{re.escape(name)}\(\) \{{.*?^\}}$", sh, re.S | re.M)
    assert match, f"{name}() did not extract — the driver shape changed"
    return match.group(0)


#: The driver functions the recovery-window harness executes. These are the
#: PURE ones — the gate and its count — with every side effect (aws, r2, gh)
#: stubbed, so the test measures the decision rather than the network.
_DRIVER_WINDOW_FUNCS = (
    "kind_owner", "alert_key", "alert_keys_all", "recovery_streak_declared",
    "_coerce_streak", "recovery_streak_for", "alert_streak_get",
    "alert_streak_set", "resolve_global",
)


def _driver_window_harness(tmp_path, *, extra_functions=(), body="",
                           seed=None, env_extra=None):
    """Run real driver functions over an in-memory object store.

    Returns ``(proc, store_dir, close_log_path)``. The store maps a sentinel key
    to a file (``/`` -> ``_``), exactly as ``r2_get``/``aws`` are stubbed to.
    ``seed`` maps a sentinel key to its JSON body, written before the run.
    """
    sh = DRIVER.read_text(encoding="utf-8")
    names = _DRIVER_WINDOW_FUNCS + tuple(extra_functions)
    funcs = "\n".join(_extract_function(sh, n) for n in names)
    store = tmp_path / "store"
    store.mkdir(exist_ok=True)
    close_log = tmp_path / "closes.txt"
    for key, sentinel_body in (seed or {}).items():
        _seed_sentinel(store, key, sentinel_body)
    script = (
        "set -uo pipefail\n"
        "R2_ENDPOINT=http://x\nR2_BUCKET=b\n"
        "GH_SEARCH_FAILED_SENTINEL=__gh_search_failed__\nLOUD=0\n"
        "R2_STORE=__STORE__\nCLOSE_LOG=__CLOSES__\n"
        "log() { printf 'LOG %s\\n' \"$*\" >&2; }\n"
        "fail() { printf 'FAIL %s\\n' \"$*\" >&2; }\n"
        "gh_find_open() { printf '%s' \"${GH_FIND_NUM:-42}\"; }\n"
        "gh_close() { printf 'close:%s\\n' \"$1\" >> \"$CLOSE_LOG\"; return 0; }\n"
        "gh_issue_open() { return \"${GH_ISSUE_OPEN_CODE:-0}\"; }\n"
        "gh_record_occurrence() { :; }\n"
        "r2_put_once() { return \"${R2_PUT_ONCE_RC:-1}\"; }\n"
        "telegram() { :; }\n"
        "r2_get() { local f=\"$R2_STORE/${1//\\//_}\"; "
        "[ -f \"$f\" ] && cat \"$f\" || true; }\n"
        "aws() { local key=\"\" body=\"\"; while [ $# -gt 0 ]; do "
        "case \"$1\" in --key) key=\"$2\"; shift 2;; --body) body=\"$2\"; shift 2;; "
        "*) shift;; esac; done; "
        "if [ -n \"$key\" ] && [ -n \"$body\" ]; then "
        "cp \"$body\" \"$R2_STORE/${key//\\//_}\"; fi; return 0; }\n"
    ).replace("__STORE__", str(store)).replace("__CLOSES__", str(close_log))
    script += funcs + "\n" + body
    env = {k: v for k, v in os.environ.items()
           if k not in ("ALERT_RECOVERY_STREAK", "ALERT_RECOVERY_STREAKS",
                        "BACKUP_POOL_RESOLVE_STREAK")}
    env["GH_FIND_NUM"] = "42"
    env.update(env_extra or {})
    proc = subprocess.run(["bash", "-c", script], capture_output=True,
                          text=True, env=env)
    return proc, store, close_log


def _seed_sentinel(store: Path, key: str, body: dict) -> Path:
    path = store / key.replace("/", "_")
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


def _closes(close_log: Path) -> list[str]:
    if not close_log.exists():
        return []
    return close_log.read_text(encoding="utf-8").split()


def test_driver_recovery_window_needs_two_consecutive_healthy_runs(tmp_path):
    """#3146 acceptance, driver side: ONE healthy run must not clear R2_UNLISTABLE.

    Two consecutive healthy runs clear it; a single one only counts and leaves
    the incident open with its sentinel intact. Measured on the shipping script
    (the functions are extracted and executed, not re-described), because the
    driver is the writer for the kind whose measurement flaps. R2_UNLISTABLE is
    the driver-owned LISTING tier (#3147); the reachability tier, R2_DOWN, is
    watcher-owned and the driver's ``resolve_global`` deliberately refuses it.
    """
    seed = {"ops/alerts/R2_UNLISTABLE/_.json": {
        "kind": "R2_UNLISTABLE", "issue_number": 42,
        "filed_at": "2026-01-01T00:00:00Z"}}
    proc, store, close_log = _driver_window_harness(
        tmp_path, seed=seed,
        body=("resolve_global R2_UNLISTABLE 'first healthy run'\n"
              "resolve_global R2_UNLISTABLE 'second healthy run'\n"),
    )
    assert proc.returncode == 0, proc.stderr
    assert _closes(close_log) == ["close:42"], (
        "exactly ONE close — the SECOND healthy run; the first only counts: "
        f"{_closes(close_log)}")
    assert "healthy reading 1/2" in proc.stderr, proc.stderr
    body = json.loads((store / "ops_alerts_R2_UNLISTABLE__.json").read_text())
    assert body["healthy_streak"] == 1, body


def test_driver_recovery_window_reads_the_legacy_fresh_streak(tmp_path):
    """One deploy cycle: an object written by the still-deployed driver
    (``fresh_streak``, the #4994 spelling) must not be read as "no count".

    Seeded at the old spelling with a count of 1, one healthy run reaches the
    declared window of 2 and closes — the count carried over rather than being
    discarded.
    """
    seed = {"ops/alerts/R2_UNLISTABLE/_.json": {
        "kind": "R2_UNLISTABLE", "issue_number": 42,
        "filed_at": "2026-01-01T00:00:00Z", "fresh_streak": 1}}
    proc, _store, close_log = _driver_window_harness(
        tmp_path, seed=seed,
        body="resolve_global R2_UNLISTABLE 'healthy run'\n",
    )
    assert proc.returncode == 0, proc.stderr
    assert _closes(close_log) == ["close:42"], _closes(close_log)


def test_driver_recovery_window_is_the_global_clear_now_lever(tmp_path):
    """ALERT_RECOVERY_STREAK=1 restores the pre-#3146 "clear on one poll"."""
    seed = {"ops/alerts/R2_UNLISTABLE/_.json": {
        "kind": "R2_UNLISTABLE", "issue_number": 42,
        "filed_at": "2026-01-01T00:00:00Z"}}
    proc, _store, close_log = _driver_window_harness(
        tmp_path, seed=seed,
        body="resolve_global R2_UNLISTABLE 'healthy run'\n",
        env_extra={"ALERT_RECOVERY_STREAK": "1"},
    )
    assert proc.returncode == 0, proc.stderr
    assert _closes(close_log) == ["close:42"], _closes(close_log)


def test_driver_recovery_window_uses_the_legacy_pool_override(tmp_path):
    """BACKUP_POOL_RESOLVE_STREAK still governs POOL_STALE (#4994's knob)."""
    seed = {"ops/alerts/POOL_STALE/_.json": {
        "kind": "POOL_STALE", "issue_number": 7,
        "filed_at": "2026-01-01T00:00:00Z"}}
    proc, _store, close_log = _driver_window_harness(
        tmp_path, seed=seed,
        body=("resolve_global POOL_STALE 'run 1'\n"
              "resolve_global POOL_STALE 'run 2'\n"
              "resolve_global POOL_STALE 'run 3'\n"),
        env_extra={"BACKUP_POOL_RESOLVE_STREAK": "3", "GH_FIND_NUM": "7"},
    )
    assert proc.returncode == 0, proc.stderr
    assert _closes(close_log) == ["close:7"], _closes(close_log)
    assert "healthy reading 1/3" in proc.stderr, proc.stderr
    assert "healthy reading 2/3" in proc.stderr, proc.stderr


def test_driver_file_alert_resets_the_count_on_adoption(tmp_path):
    """#3146: the driver's still-unhealthy choke point resets the count.

    ``file_alert`` is called only when the condition IS true, and an adoption
    means the incident is still unhealthy — so the accumulated recovery count is
    zeroed. Without it, alternating readings accumulate into a clear that was
    never consecutive. Exercised on the shipping ``file_alert``: the sentinel is
    seeded at a LIVE issue with a count of 1 and must come back at 0.
    """
    seed = {"ops/alerts/R2_DOWN/_.json": {
        "kind": "R2_DOWN", "issue_number": 42,
        "filed_at": "2026-01-01T00:00:00Z", "healthy_streak": 1}}
    proc, store, close_log = _driver_window_harness(
        tmp_path, seed=seed, extra_functions=("file_alert",),
        body="file_alert R2_DOWN '[DR] R2_DOWN' 'body' ''\n",
    )
    assert proc.returncode == 0, proc.stderr
    body = json.loads((store / "ops_alerts_R2_DOWN__.json").read_text())
    assert body["healthy_streak"] == 0, body
    assert _closes(close_log) == [], "an adoption never closes"



def test_driver_recovery_streak_config_precedence(tmp_path):
    """The driver's env contract, executed (the IFS/parse half is bash-only).

    The DECLARED map is pinned above; this exercises the resolution order and the
    two failure directions on the shipping functions, since the comma-splitting
    and the non-integer guard exist only in bash and cannot be verified from
    Python.
    """
    sh = DRIVER.read_text(encoding="utf-8")
    funcs = "\n".join(
        _extract_function(sh, n) for n in
        ("recovery_streak_declared", "_coerce_streak", "recovery_streak_for"))
    script = (
        "log() { printf '%s\\n' \"$*\" >&2; }\n"
        + funcs
        + "\nprintf '%s ' \"$(recovery_streak_for R2_DOWN)\"\n"
        "printf '%s ' \"$(recovery_streak_for STALE)\"\n"
        "printf '%s ' \"$(recovery_streak_for POOL_STALE)\"\n"
        "printf '%s\\n' \"$(recovery_streak_for DATA_LOSS_CANDIDATE)\"\n"
    )
    env = {k: v for k, v in os.environ.items()
           if k not in ("ALERT_RECOVERY_STREAK", "ALERT_RECOVERY_STREAKS",
                        "BACKUP_POOL_RESOLVE_STREAK")}
    env.update({
        "ALERT_RECOVERY_STREAKS": "STALE=5,POOL_STALE=4,BADKIND=9",
        "BACKUP_POOL_RESOLVE_STREAK": "3",
    })
    proc = subprocess.run(["bash", "-c", script], capture_output=True,
                          text=True, env=env)
    assert proc.returncode == 0, proc.stderr
    # R2_DOWN is not in the per-kind map -> declared 2. STALE -> 5. POOL_STALE:
    # the per-kind map (4) beats the legacy alias (3). A non-plane kind -> 1.
    assert proc.stdout.strip() == "2 5 4 1", proc.stdout

    # Global override wins over both.
    env["ALERT_RECOVERY_STREAK"] = "7"
    proc = subprocess.run(["bash", "-c", script], capture_output=True,
                          text=True, env=env)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "7 7 7 7", proc.stdout

    # A non-integer is IGNORED (the declared value stands) with a warning; a
    # value below 1 is read as 1 with a warning — never as "never resolve".
    env["ALERT_RECOVERY_STREAKS"] = "STALE=x,POOL_STALE=0"
    env.pop("ALERT_RECOVERY_STREAK")
    env.pop("BACKUP_POOL_RESOLVE_STREAK")
    proc = subprocess.run(["bash", "-c", script], capture_output=True,
                          text=True, env=env)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "2 2 1 1", proc.stdout
    assert "is not an integer" in proc.stderr, proc.stderr
    assert "below 1" in proc.stderr, proc.stderr

    # A NEGATIVE value is IGNORED too (the declared value stands) — it must not
    # silently defeat the hysteresis, and the Python twin must agree. This used
    # to be the exact divergence #3146's review caught: bash read ``-4`` as a
    # non-integer (declared window stands) while Python clamped it to 1, so one
    # env var selected two different recovery windows across the two writers.
    # ``-0`` is ZERO (Python's ``int("-0") == 0``), so it is NOT the negative
    # branch: both writers read it through the below-1 clamp. The expected window
    # for each kind is taken from the PYTHON twin, so the assertion cannot
    # silently agree with only one side.
    #
    # The shape cases pin the rest of the contract the review called out:
    # surrounding whitespace is trimmed by BOTH (the store's `str.strip()` and
    # the driver's parameter-expansion trim), while spellings ``int()`` happens
    # to tolerate but bash's digit guard does not (``+3``, ``1_0``) must be
    # IGNORED by both rather than accepted by one side only.
    cross_language_cases = {
        "a negative value is ignored":
            ({"ALERT_RECOVERY_STREAKS": "STALE=-4,POOL_STALE=-4"},
             "is negative"),
        "a signed zero is still zero":
            ({"ALERT_RECOVERY_STREAKS": "STALE=-0,POOL_STALE=-0"},
             "below 1"),
        "surrounding whitespace is trimmed, not rejected":
            ({"ALERT_RECOVERY_STREAKS": " STALE = 3 ,POOL_STALE= 4 "},
             None),
        "a plus sign is not an integer":
            ({"ALERT_RECOVERY_STREAKS": "STALE=+3,POOL_STALE=+3"},
             "is not an integer"),
        "an underscore is not a digit":
            ({"ALERT_RECOVERY_STREAKS": "STALE=1_0,POOL_STALE=1_0"},
             "is not an integer"),
    }
    for label, (extra_env, expected_warning) in cross_language_cases.items():
        env.update(extra_env)
        proc = subprocess.run(["bash", "-c", script], capture_output=True,
                              text=True, env=env)
        assert proc.returncode == 0, proc.stderr
        expected = " ".join(
            str(ik.resolve_streak_for(kind, extra_env)) for kind in
            ("R2_DOWN", "STALE", "POOL_STALE", "DATA_LOSS_CANDIDATE"))
        assert proc.stdout.strip() == expected, (label, proc.stdout, expected)
        if expected_warning is None:
            assert "is not an integer" not in proc.stderr, (label, proc.stderr)
            assert "below 1" not in proc.stderr, (label, proc.stderr)
        else:
            assert expected_warning in proc.stderr, (label, proc.stderr)


def test_driver_workflow_binds_the_recovery_window_knobs():
    """The runbook's knobs must reach the driver through its only production caller.

    Actions passes a job ONLY the env it enumerates, so a knob the runbook
    documents but the workflow omits is unreachable no matter what an operator
    sets (#3146 review P2). The driver's sole production invocation is
    ``registry-backup-cron.yml``, so pin the bindings there. The value is
    ``${{ vars.… }}`` deliberately: an unset repository variable expands to the
    empty string, which ``recovery_streak_for`` reads as "no override".
    """
    import yaml

    wf = yaml.safe_load(
        (ROOT / ".github/workflows/registry-backup-cron.yml").read_text())
    env = wf["jobs"]["drive"]["env"]
    for key in ("ALERT_RECOVERY_STREAK", "ALERT_RECOVERY_STREAKS",
                "BACKUP_POOL_RESOLVE_STREAK"):
        assert env.get(key) == f"${{{{ vars.{key} }}}}", (
            f"registry-backup-cron must bind {key} so the documented lever "
            f"reaches the driver; got {env.get(key)!r}")
