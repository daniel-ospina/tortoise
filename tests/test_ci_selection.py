"""Unit tests for the tiered test selector (#1021).

Covers the fail-closed selection rules: docs-only → tier 1; surface changes →
tier 1 ∪ surface; shared modules → full; unknown paths → full; test-file
changes select their owning surface; push/schedule → full; manifest integrity
(recursive rglob since #1349 — subdir test files, tests/e2e/ exempt; tool-path
carve-out so tools/longmem_eval/ etc. select the eval surface).
"""
from __future__ import annotations

import ast
import copy
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.ci_selection import (  # noqa: I001
    SOURCE_PATTERNS, SHARED_MODULES, load_manifest, select, integrity, slow_file_issues,
    unlisted_tests, register_tests, register, classify_test_file,  # noqa: F401
    surface_audit, render_surface_audit, duplicate_entries,
    on_demand_files, leg_coverage_issues, push_legs, fast_pool,
    duration_issues, TESTS_DIR, WATCHDOG_HEADROOM, WATCHDOG_CEILING_MIN,
    uri_requiring_files, slow_leg_by_surface,
    carve_matrix_include, carve_shard_issues, carve_shard_count,
    MAX_CARVE_SHARDS, DEFAULT_CARVE_SHARDS,
    slow_matrix_include, slow_shard_issues, slow_shard_count,
    slow_shard_entries, slow_leg_files, slow_wall_projection,
    MAX_SLOW_SHARDS, DEFAULT_SLOW_SHARDS,
    SLOW_LEG_WALL_TARGET_MIN, SLOW_LEG_SETUP_ALLOWANCE_MIN,
)
from tools import mergify_config_guard as mcg
from tools import ci_selection as cs


REPO = Path(__file__).resolve().parents[1]
FROZEN_NODEIDS_DIR = REPO / "config" / "ci-expected-nodeids"


def _frozen_nodeids(name: str) -> list[str]:
    """The nodeids a frozen manifest declares (comments/directives ignored)."""
    return [
        line.strip()
        for line in (FROZEN_NODEIDS_DIR / name).read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def _sel(changed, event="pull_request"):
    return select(changed, event, load_manifest())


def _expand(legs: dict) -> dict:
    """Compare leg membership without caring about the .py suffix.

    #6135: the fast legs are the N shards under `shards`, so flatten them into
    one set per shard LABEL — that keeps every per-leg assertion below a
    per-leg assertion, which is the property that catches "one leg forgot".
    """
    out = {}
    for k, v in legs.items():
        if k in ("shards", "carve_shards", "slow_shards"):
            # W37: `carve_shards` is the same shape as `shards` (a list of
            # {name, files, …} dicts), so it must be flattened the same way —
            # `set(v)` over dicts raises `unhashable type: 'dict'`, which is
            # how this surface announced itself rather than silently passing.
            # #9539: `slow_shards` is the third member of that family.
            prefix = {"shards": "", "carve_shards": "carve_",
                      "slow_shards": "slow_"}[k]
            for shard in v:
                out[prefix + shard["name"]] = set(shard["files"])
        else:
            out[k] = set(v)
    return out


def _tier1() -> set:
    return set(load_manifest()["tier1"])


def test_docs_only_runs_tier1():
    # A markdown-only docs change stays the pure tier-1 smoke path.
    r = _sel(["docs/README.md"])
    assert r["full"] is False
    assert r["surfaces"] == []
    assert set(r["test_files"]) == _tier1()


def test_docs_scanning_gates_stay_in_tier1():
    # #4309: a docs-only PR runs *only* tier1.
    #
    # A gate whose whole job is to stop a claim being re-scattered across the
    # doc tree is silent on the exact change it exists to catch unless it is
    # registered in `tier1` — every other surface needs a Python path to be
    # selected. #4283 registered the durability gate in `tier1` + `core` for
    # exactly this reason; the older #4179 retention gate stayed out of `tier1`
    # and so ran no part of its scan on a docs-only edit (e.g. to
    # docs/retention-and-deletion.md), letting a new unlinked retention claim
    # merge green. Pin both: dropping either from `tier1` re-opens that hole.
    tier1 = _tier1()
    assert "test_retention_promise.py" in tier1, (
        "#4179 retention/deletion anti-scatter gate must stay in tier1 — a "
        "docs-only PR runs only tier1, so without it a new unlinked retention "
        "claim in docs/ merges green (#4309)"
    )
    assert "test_durability_posture.py" in tier1, (
        "#2881 durability gate must stay in tier1 — same docs-only silent-drop "
        "class (#4309)"
    )
    assert "test_7818_ontology_record.py" in tier1, (
        "#7818 ONTOLOGY §4.7 doc↔code pin must be in tier1 — the other half of "
        "the #7818 guard (`test_7818_boundary_ambiguity_contract.py`) is "
        "`sdk`-registered and docker-lane, so a docs-only `docs/ONTOLOGY.md` "
        "edit never runs it; without this hermetic pin the §4.7 coverage-rule "
        "drift lands green (#4309 class, same as #5142/#2881)"
    )
    assert "test_8474_ontology_record.py" in tier1, (
        "#8474 §4.6 rawState/rawStateAt availability doc↔code pin must be in "
        "tier1 — `docs/` is in NON_PYTHON_PREFIXES, so a docs-only "
        "`docs/ONTOLOGY.md` edit selects no surface and runs only tier1; "
        "without this hermetic pin the §4.6 row's ruling/contract drift lands "
        "green (#4309 class, same as #5142/#7818/#2881)"
    )
    assert "test_7818_plan_record_5362.py" in tier1, (
        "#7818/#5362 plan-record pin must be in tier1 — the #5362 plan's "
        "RES-1 residual and out-of-scope list are docs the drift arrives on, "
        "and a docs-only edit runs only tier1; without this pin a re-staled "
        "`found`-omission claim merges green (#4309 class)"
    )
    assert "test_10191_record_move_doc_pin.py" in tier1, (
        "#10244 review (P3): the #10191 record-side MOVE pin must be in tier1 — "
        "it guards `docs/INGEST_CONTRACT.md`, which is in NON_PYTHON_PREFIXES "
        "and matches no SOURCE_PATTERNS entry, so an INGEST-only diff selects "
        "no surface and runs tier1 ONLY. The `core` row and the `durations` row "
        "both survive a dropped `tier1` row, so nothing would red: the manifest "
        "stays internally consistent while the pin sleeps on the exact edit it "
        "exists to catch (#4309/#8474 class, the hole #10244 closed)"
    )
    assert "test_fly_secret_drift.py" in tier1, (
        "#8614: the runbook's secret-drift guards read docs/infra-runbook.md "
        "(`docs/` is in NON_PYTHON_PREFIXES and the runbook matches no "
        "SOURCE_PATTERNS entry), so a runbook-only diff runs tier1 only — "
        "without this registration a re-introduced `—` in a `gh-secret:` row "
        "or a `fly secrets set NAME=$(openssl rand …)` recipe merges green "
        "(#4309 class)"
    )
    assert "test_cross_subdomain_cookie_sync.py" in tier1, (
        "#8701 review (P1): the key-routing adapter registry-completeness gate "
        "must be in tier1 — it scans for a new adapter copy, but a new file "
        "under a NON_PYTHON_PREFIXES root (e.g. website/assets/) selects no "
        "surface, so a diff that ONLY adds the copy runs tier1 only; without "
        "this registration the fourth copy merges green (#4309 class)"
    )
    assert "test_b7_runbook_ok_semantics_8135.py" in tier1, (
        "#8135 review (P1): the B7 operator runbook's `status == \"ok\"` pin "
        "reads docs/runbook/b7-activation-scorecard.md — `docs/` is in "
        "NON_PYTHON_PREFIXES and the runbook matches no SOURCE_PATTERNS "
        "entry, so a runbook-only diff runs tier1 only; without this "
        "registration the pre-#8135 sentence (or a dropped #9084 CLOSED "
        "claim, or a re-asserted plain-dict producer) merges green (#4309 "
        "class, same as #8614)"
    )
    assert "test_definite_description_backfill_5059.py" in tier1, (
        "#5333 review (P1): the *G5's destructive half* doc guards in this file "
        "read ONLY docs/architecture/EXTRACTOR-V4-ARCHITECTURE.md — `docs/` is "
        "in NON_PYTHON_PREFIXES and that document matches no SOURCE_PATTERNS "
        "entry, so a docs-only edit of the passage runs tier1 only; without "
        "this registration a re-inverted 52%/48% share, a re-asserted "
        "'nothing writes `archived`' or a new reader of "
        "`_ALLOWED_TRANSITIONS` merges green (#4309 class, same as #8495's "
        "sibling row)"
    )
    assert "test_v4_declared_mechanisms.py" in tier1, (
        "#9778 review (P1): the declared-mechanism MEMBERSHIP gate derives the "
        "declared set from the `<!-- v4-mechanism: <id> -->` markers in "
        "docs/architecture/**/*.md — `docs/` is in NON_PYTHON_PREFIXES, so a "
        "docs-only diff runs tier1 only (EXTRACTOR-V4-ARCHITECTURE.md takes "
        "the early return, STORAGE-ARCHITECTURE.md selects `onboarding`, and "
        "`core` is added only when NOTHING matched); registered in `core` "
        "alone, deleting or renaming a marker merged green while the gate was "
        "red on main (#4309/#3616 class, same dual registration as the six "
        "sibling test_extractor_v4_* doc guards)"
    )
    assert "test_7949_e5_section10_writeback.py" in tier1, (
        "#10108 review (P1): the E5 write-back pin binds "
        "docs/architecture/EXTRACTOR-V4-ARCHITECTURE.md §10's replicated-run "
        "table to a fresh run of the registered scorer — `docs/` is in "
        "NON_PYTHON_PREFIXES and that document matches no SOURCE_PATTERNS "
        "entry, so a docs-only edit of §10 runs tier1 only; registered in "
        "`core` alone, retyping one digit or deleting the subsection merged "
        "green while #7949/#7947 went on citing it (#4309 class, same dual "
        "registration as the sibling capstone rows above)"
    )


    assert "test_substrate_map.py" in tier1, (
        "#5086 review (P1-2): the substrate map's drift guard must be in tier1 — "
        "the map IS a document (`docs/architecture/SUBSTRATE-REBUILD-MAP.md`), "
        "and `docs/` is in NON_PYTHON_PREFIXES and the map matches no "
        "SOURCE_PATTERNS entry, so the docs-only edit that let the map go stale "
        "three times selects no surface and runs tier1 ONLY. Registered in "
        "`core` alone it ran on `tortoise/` diffs, i.e. on the one half of the "
        "map that is not the artifact that went stale (#4309 class, same dual "
        "registration as the sibling doc guards above). ⚠️ This row is the MAP's "
        "arm only: `docs/00_index.md`, the other file the guard reads, is in "
        "`CORE_ALSO`, so an index-only edit measures `surfaces=['core']` and "
        "needs no tier1 row (#5086 review P3, cycle 6)"
    )


def test_v4_membership_gate_runs_on_both_architecture_docs():
    """#9778 review (P2): pin the SELECTION, not just the manifest string.

    The `tier1` assertion above catches a deleted row, but the load-bearing half
    of the reasoning had no test: `STORAGE-ARCHITECTURE.md` matches the
    `onboarding` SOURCE_PATTERNS entry, so its plan is `surfaces=['onboarding']`
    — a surface that does NOT contain the gate and whose `tier1` union is the
    only thing that runs it. That is why `core` alone is insufficient, and this
    file's idiom for such a registration is a behavioural assertion (as at
    :func:`test_g5_destructive_half_doc_guards_run_on_a_docs_only_diff`).

    Measured: 131 files for the STORAGE diff (`onboarding`), 60 for the
    EXTRACTOR diff (empty surfaces, the early return); the gate is in the
    selected set in both, and dropping the `tier1` row removes it from both.
    """
    storage = _sel(["docs/architecture/STORAGE-ARCHITECTURE.md"])
    assert storage["surfaces"] == ["onboarding"], storage["surfaces"]
    assert storage["full"] is False
    assert "test_v4_declared_mechanisms.py" in storage["test_files"]

    extractor = _sel(["docs/architecture/EXTRACTOR-V4-ARCHITECTURE.md"])
    assert extractor["surfaces"] == [], extractor["surfaces"]
    assert "test_v4_declared_mechanisms.py" in extractor["test_files"]


def test_7949_e5_section10_writeback_runs_on_a_docs_only_diff():
    """#10108 review (P1): pin the SELECTION, not just the manifest string.

    `tests/test_7949_e5_section10_writeback.py` compares §10's published
    replicated-run figures against a fresh run of the registered scorer. The one
    change that can red it is a docs-only edit of
    `docs/architecture/EXTRACTOR-V4-ARCHITECTURE.md` — the diff class on which
    `docs/` is filtered out by NON_PYTHON_PREFIXES, the document matches no
    SOURCE_PATTERNS entry, `changed` empties and `select()` takes the docs-only
    early return, so tier1 is the ONLY surface that runs.

    Measured before the fix: the document's plan was `surfaces=[]`, the tier-1
    set (64 files) did not contain this file, and the pin was registered in
    `core` alone — asleep on the exact change it exists to catch. The manifest
    assertion in :func:`test_docs_scanning_gates_stay_in_tier1` catches a deleted
    row; this catches a row that is present but no longer reaches the file
    (#9778 review P2 made the same point for the declared-mechanism gate).
    """
    r = _sel(["docs/architecture/EXTRACTOR-V4-ARCHITECTURE.md"])
    assert r["full"] is False
    assert r["surfaces"] == [], r["surfaces"]
    assert "test_7949_e5_section10_writeback.py" in r["test_files"], (
        "a docs-only edit of §10's replicated-run passage must select the pin "
        "that binds it to the scorer — otherwise the table can be retyped, "
        "edited one digit, or deleted and merge green (#10108 review P1, "
        "#4309 class)"
    )


def test_substrate_map_guard_runs_on_the_diffs_it_guards():
    """#5086 review (P1-2): pin the SELECTION on all four arms, not the string.

    `tests/test_substrate_map.py` re-measures
    `docs/architecture/SUBSTRATE-REBUILD-MAP.md` against the tree. Four distinct
    diffs can break it, and before this fix NONE of them selected it — the row had
    been appended to the `ep:` list rather than `core:`, and neither `core` nor
    the early return reaches the file anyway:

    - a **docs-only** edit of the map → measured `surfaces=[]`, tier-1 set WITHOUT
      the pin; `tier1` is the only surface that runs, so the row above is the
      whole fix. (This is the arm that went stale three times, and the ONLY one
      that needs `tier1`.)
    - a **tool-only** edit of `tools/substrate_map_check.py` → measured
      `surfaces=[]` (a flat `NON_PYTHON_PREFIXES` entry, no `SOURCE_PATTERNS`
      match), so the tool's own test never ran on the PR editing the tool. Closed
      by `CORE_ALSO` in `tools/ci_selection.py` (#1349/#3332/#3616 class).
    - a **`docs/00_index.md`** edit — the OTHER file the guard reads for the
      registration claim — is `CORE_ALSO` too, so it measures
      `surfaces=['core']` and reaches the pin through the `core` row. Pinned here
      because the fix-pass notes and the `tier1` comment both claimed this arm was
      a docs-only early return; it is not (#5086 review P3, cycle 6).
    - a **`tortoise/`** symbol move (what the interface map places) → `core`,
      through the `tortoise/` prefix fallback.

    Measured after the fix: docs `surfaces=[]` + pin present, tool
    `surfaces=['core']` + pin present, index `surfaces=['core']` + pin present,
    `tortoise/projection/edges.py` `surfaces=['core', ...]` + pin present.
    """
    docs = _sel(["docs/architecture/SUBSTRATE-REBUILD-MAP.md"])
    assert docs["full"] is False
    assert docs["surfaces"] == [], docs["surfaces"]
    assert "test_substrate_map.py" in docs["test_files"], (
        "a docs-only edit of the map must run the guard that re-measures it — "
        "otherwise it goes stale a fourth time with every gate green"
    )

    tool = _sel(["tools/substrate_map_check.py"])
    assert tool["full"] is False
    assert "test_substrate_map.py" in tool["test_files"], (
        "a tool-only edit must run the tool's own negative controls — `tools/` "
        "is swallowed by NON_PYTHON_PREFIXES, so CORE_ALSO is the only thing "
        "that keeps this out of the docs-only early return"
    )

    index = _sel(["docs/00_index.md"])
    assert index["surfaces"] == ["core"], index["surfaces"]
    assert "test_substrate_map.py" in index["test_files"], (
        "the registration claim reads `docs/00_index.md`, and that path is in "
        "CORE_ALSO — so an index-only edit is `core`, NOT the docs-only early "
        "return the `tier1` comment used to describe it (#5086 review P3, "
        "cycle 6: measured, not reasoned)"
    )

    symbol_move = _sel(["tortoise/projection/edges.py"])
    assert "core" in symbol_move["surfaces"], symbol_move["surfaces"]
    assert "test_substrate_map.py" in symbol_move["test_files"]


def test_g5_destructive_half_doc_guards_run_on_a_docs_only_diff():
    """#5333 review (P1): those guards must run on the DOCUMENT they pin.

    Four guards in `tests/test_definite_description_backfill_5059.py` bind
    `docs/architecture/EXTRACTOR-V4-ARCHITECTURE.md` §4.3's *G5's destructive
    half*, and **three** of them read the document: the load-bearing share
    (which also reads its own module for the measurement), the
    `archived`-has-a-writer claim (which rejects the set of no-writer spellings
    it enumerates, rather than one literal — the literal form was evaded
    twice), and the recorded design decision itself. The fourth, the
    `_ALLOWED_TRANSITIONS` production-reader scan, reads **no line of the
    document**: it AST-scans `tortoise/` for a consumer of the map the passage
    cites, so a docs-only edit cannot red it (#10069 review, P3 — this
    docstring said all four read only the document, which was false for it).
    The file was
    registered in `core` alone — which is right for its module-level guards, and
    useless for these: `docs/` is in NON_PYTHON_PREFIXES and the document
    matches no SOURCE_PATTERNS entry, so a docs-only diff filters to
    `changed == []` and takes the docs-only early return (tier1 ONLY).

    Measured before the fix: `select(["docs/architecture/EXTRACTOR-V4-"
    "ARCHITECTURE.md"], "pull_request", …)["test_files"]` did not contain this
    file, while the sibling `test_extractor_v4_failure_policy.py` (dual-
    registered for exactly this reason) was selected. Dropping the tier1 entry
    re-opens that hole (#4309 silent-drop class; #8614/#8495 shape).
    """
    r = _sel(["docs/architecture/EXTRACTOR-V4-ARCHITECTURE.md"])
    assert r["full"] is False
    assert r["surfaces"] == []
    assert "test_definite_description_backfill_5059.py" in r["test_files"], (
        "a docs-only edit of the §4.3 relocation passage must select the guards "
        "that bind its prose (test_definite_description_backfill_5059.py) — "
        "otherwise the passage can be re-inverted and merge green (#5333 "
        "review P1, #4309 class)"
    )


def test_fly_secret_drift_gate_runs_on_a_runbook_only_diff():
    """#8614 review (P2): the runbook's own guards must run on the runbook.

    `tests/test_fly_secret_drift.py` binds prose claims in
    `docs/infra-runbook.md` (the Secrets Matrix GitHub column, §1/§2 never
    giving a `gh-secret:` name a value minted on Fly, and §2's account of which
    `workflow` names the deploy re-stages — the set grows with the file, so this
    names the binding rather than counting it). `docs/` is in
    NON_PYTHON_PREFIXES and `docs/infra-runbook.md` matches no SOURCE_PATTERNS
    entry, so a runbook-only diff filtered to `changed == []` and took the
    docs-only early return — tier1 ONLY. Registered in `core` but not in
    `tier1`, the guards were therefore asleep on the only change that can break
    them: the pre-#8614 state (`—` in the FASTAPI_INTERNAL_KEY /
    TORTOISE_SECRET_PEPPER rows) would have merged green and only reddened
    `main` post-merge. Dropping the tier1 entry re-opens that hole (#4309
    silent-drop class).
    """
    r = _sel(["docs/infra-runbook.md"])
    assert r["full"] is False
    assert r["surfaces"] == []
    assert "test_fly_secret_drift.py" in r["test_files"], (
        "a docs/infra-runbook.md-only diff must select the guards that bind its "
        "prose (`test_fly_secret_drift.py`) — otherwise a docs-only edit that "
        "re-breaks a claim it pins merges green (#8614 review, #4309 class)"
    )


def test_the_record_arming_runbook_pins_run_on_a_runbook_only_diff():
    """#9548 review (P1): the arming section's pins must run on the runbook.

    `tests/test_deploy_workflow.py` binds `docs/infra-runbook.md` prose for the
    first time in #9548 (§"Arming the Postgres record"):
    `_record_arming_section()`, `_matrix_rows()` and
    `test_the_record_arming_verify_recipe_reads_a_surface_that_exists`. `docs/`
    is in NON_PYTHON_PREFIXES and the runbook matches no SOURCE_PATTERNS entry,
    so a runbook-only diff filters to `changed == []` and takes the docs-only
    early return — tier1 ONLY. Registered in `core` but not in `tier1`, those
    pins were therefore asleep on the only realistic diff that can re-break
    them: a docs-only PR re-introducing
    `curl -s localhost:9090/metrics | grep record_write_failures` — which the
    hosted app does not serve, so it prints nothing in EVERY state and the
    empty grep reads as "0 failures" — or restoring the absolute
    `record_write_failures == 0` bar, would merge green and only red `main`
    post-merge (#4309 silent-drop class; the #8614 shape one runbook section
    over). Dropping the tier1 entry re-opens that hole.
    """
    r = _sel(["docs/infra-runbook.md"])
    assert r["full"] is False
    assert r["surfaces"] == []
    assert "test_deploy_workflow.py" in r["test_files"], (
        "a docs/infra-runbook.md-only diff must select the pins that bind its "
        "arming-section prose (`test_deploy_workflow.py`) — otherwise a "
        "docs-only edit that restores the 9090/metrics recipe or the absolute "
        "`record_write_failures == 0` bar merges green (#9548 review, #4309 "
        "class)"
    )
    # ... and the pre-existing runbook owner must stay selected alongside it:
    # the two files pin DIFFERENT halves of the same doc (Secrets Matrix +
    # rotation recipes vs. the arming section), so neither substitutes.
    assert "test_fly_secret_drift.py" in r["test_files"], (
        "the #8614 runbook owner must stay in tier1 — #9548 adds a second "
        "runbook reader, it does not replace the first"
    )


def test_the_8503_docs_pins_run_on_a_docs_only_diff():
    """#8503 round 9 (P1): the bundle's own pins must run on the bundle.

    `tests/test_evidence_8503_claims.py` binds two DOC claims: the single
    `docs/00_index.md` row that routes a reader into
    `docs/evidence/8503/README.md`, and that README's verdict banner (the zero
    operator-drop count `does NOT make every edge_missing a selection miss`;
    the pre-fix banner said the opposite). `docs/` is in
    NON_PYTHON_PREFIXES and `docs/evidence/8503/` matches no CORE_ALSO entry,
    so a bundle-README-only diff filters to `changed == []` and takes the
    docs-only early return — tier1 ONLY, measured: `surfaces == []`,
    `slow_run is False`, 63 test files.

    Both pins used to live in `tests/test_extractor_v2.py`, registered `core`
    AND a `slow_files` member — and slow files are SUBTRACTED from tier1, so
    on that diff they ran in NO leg (adding them to tier1 there would have
    been a no-op). Deleting the index row, or restoring the banner's
    "so no edge_missing is an extractor-side drop" over-claim, therefore
    merged GREEN and only reddened `main` post-merge — the #1349/#3332/#3616/
    #4309 silent-drop class, the same shape as `test_fly_secret_drift.py`
    above.

    Non-vacuity: dropping the `tier1` entry below re-opens exactly that hole,
    and the second half asserts it by driving `select()` on a manifest copy
    with the entry removed.
    """
    r = _sel(["docs/evidence/8503/README.md"])
    assert r["full"] is False
    assert r["surfaces"] == []
    assert "test_evidence_8503_claims.py" in r["test_files"], (
        "a bundle-README-only diff must select the pins that bind its prose "
        "(`test_evidence_8503_claims.py`) — otherwise a docs-only edit that "
        "deletes the index row or restores the banner's over-claim merges "
        "green (#8503 round 9, #4309 class)"
    )
    # the `docs/00_index.md` half rides the same lane
    idx = _sel(["docs/00_index.md"])
    assert "test_evidence_8503_claims.py" in idx["test_files"], (
        "a docs/00_index.md-only diff must select the row pin too"
    )
    # ... and the pre-fix home must NOT be relied on: it is a slow file, which
    # tier1 subtracts, so it cannot cover the docs-only lane.
    assert "test_extractor_v2.py" not in r["test_files"]

    # non-vacuity: without the `tier1` entry the pin is asleep again.
    m = copy.deepcopy(load_manifest())
    m["tier1"] = [f for f in m["tier1"] if f != "test_evidence_8503_claims.py"]
    blind = select(["docs/evidence/8503/README.md"], "pull_request", m)
    assert "test_evidence_8503_claims.py" not in blind["test_files"], (
        "the tier1 registration is what makes this pin run on a docs-only "
        "diff; `core` alone does not reach that lane"
    )


def test_b7_runbook_ok_semantics_gate_runs_on_a_runbook_only_diff():
    """#8135 review (P1, two reviews): the runbook's `ok` pin must run on the runbook.

    `tests/test_b7_runbook_ok_semantics_8135.py` binds the claim
    `docs/runbook/b7-activation-scorecard.md` makes about
    `mcp_tool_call.status == "ok"` — the claim #8135 corrected after it had
    already drifted once (#3675). `docs/` is in NON_PYTHON_PREFIXES and the
    runbook matches no SOURCE_PATTERNS entry, so a runbook-only diff filters to
    `changed == []` and takes the docs-only early return — tier1 ONLY.

    The pin used to live in `tests/test_activation_scorecard.py`, registered on
    `api` and `core` but NOT in `tier1`, so it was asleep on the one change that
    can break it: re-introducing "means only that the tool did not raise", or
    dropping the #9084 CLOSED claim (and the two residuals it names), in a
    docs-only PR merged green.
    Dropping the tier1 entry moves the pin back into that hole (#4309 silent-drop
    class; the #8614 shape one runbook over).
    """
    r = _sel(["docs/runbook/b7-activation-scorecard.md"])
    assert r["full"] is False
    assert r["surfaces"] == []
    assert "test_b7_runbook_ok_semantics_8135.py" in r["test_files"], (
        "a docs/runbook/b7-activation-scorecard.md-only diff must select the "
        "pin that binds its prose (test_b7_runbook_ok_semantics_8135.py) — "
        "otherwise a docs-only edit that re-teaches the pre-#8135 meaning of "
        "`ok`, or drops the #9084 CLOSED claim, merges green (#8135 review P1, "
        "#4309 class)"
    )


def test_cross_subdomain_cookie_sync_gate_runs_on_a_new_file_diff():
    """#8701 review (P1): the adapter registry-completeness gate must run on the
    diff that ADDS a fourth copy, even when that copy lands in a new file under
    a NON_PYTHON_PREFIXES root.

    `test_every_storage_adapter_copy_is_registered` scans product sources for a
    storage adapter and fails on any copy not in `_ROUTER_CASES`. But a NEW file
    under `website/assets/` matches no SOURCE_PATTERNS entry, so
    `_keep_changed` drops it, `changed` comes back empty, and `select()` takes
    the docs-only early return — tier1 ONLY. Registered in `api`/`onboarding`
    but not `tier1`, the gate was asleep on the exact change it exists to catch:
    a fourth copy would merge green and only red `main` post-merge (the
    #1349/#3332/#3616/#4309 silent-drop class). Dropping the tier1 entry
    re-opens that hole.
    """
    r = _sel(["website/assets/__probe_router_copy.js"])
    assert r["full"] is False
    assert r["surfaces"] == []
    assert "test_cross_subdomain_cookie_sync.py" in r["test_files"], (
        "a diff that only ADDS an adapter copy in a new website/assets/ file must "
        "select the registry-completeness gate — otherwise the fourth copy merges "
        "green (#8701 review P1, #4309 class)"
    )


def test_the_end_customer_credential_design_guard_runs_on_its_doc_only_diff():
    """#8693 review (P1, finding 1): the design doc must select its own guard.

    `test_8693_end_customer_credential_design.py` pins the end-customer credential
    DESIGN DOC — half code pins against `tortoise/hosted_api.py` (`rotate`/
    `DELETE` org pin, owner-class `graphs:create`, the third audit action name),
    half the corrected prose of
    `docs/scoping/2026-10-19-8693-end-customer-credential-design.md`. The prose
    half only breaks on a docs-only edit to that doc, and `docs/` is in
    NON_PYTHON_PREFIXES with the doc matching no SOURCE_PATTERNS entry, so a
    doc-only diff filters to `changed == []` and takes the docs-only early return
    — tier1 ONLY. Registered on `api` (which covers `hosted_api.py` edits) but not
    in `tier1`, the guard was asleep on the one change that can re-stale it: the
    doc reverted to the three refuted claims in a one-file PR would merge green
    and only red `main` post-merge (the #1349/#3332/#3616/#4309 silent-drop
    class; the #8614/#8135 shape one doc over). Dropping the tier1 entry re-opens
    that hole.

    Asserted as `surfaces == []` on purpose, not just "the guard is present": the
    docs-only early return is the whole reason this registration is needed, and a
    Python path creeping into the selection would mask the loss of the tier1
    entry (`test_fly_secret_drift_gate_runs_on_a_runbook_only_diff` shape above).
    """
    root = Path(__file__).resolve().parents[1]
    changed = "docs/scoping/2026-10-19-8693-end-customer-credential-design.md"
    assert (root / changed).exists(), f"guarded design doc is gone: {changed}"
    r = _sel([changed])
    assert r["full"] is False
    assert r["surfaces"] == []
    assert "test_8693_end_customer_credential_design.py" in r["test_files"], (
        f"a {changed}-only diff does not select "
        "tests/test_8693_end_customer_credential_design.py, so its prose pins "
        "cannot fire on the only change that can break them — a one-file revert "
        "to the three refuted claims would merge green (#8693 review P1, #4309 "
        "silent-drop class)"
    )


def test_public_site_surface_change_selects_onboarding_and_skips_slow():
    """#3332: a public *site surface* change selects `onboarding`.

    Each path listed in SOURCE_PATTERNS is a file some guard test in the
    onboarding surface reads — most importantly test_website_docs_consistency.py
    (checks each public page against its canonical source: POINT_STATUS_VALUES in
    tortoise/sdk.py, the EP mechanism, the ontology link) and test_website_static.py
    (pins product.html's pricing surface). Before #3332 every `website/` path was
    filtered out by NON_PYTHON_PREFIXES *before* SOURCE_PATTERNS was consulted, so
    changed == [] -> tier-1 smoke: those guard tests never ran for the files
    they guard.

    The #2147/#2148 cost gates are unchanged — only the fast surface tests are
    added; no slow leg, no carve-out leg. That is a property of the SURFACE, not
    of these nine paths: `onboarding` owns none of the slow-leg files
    (`slow_leg_by_surface()` returns no `onboarding` key), which is why the
    docs/drafts deferral in `test_every_guard_input_is_selectable_by_ci` cannot
    cite the slow leg as the cost of pinning them (PR #9623 review P3). The
    assertion below keeps that docstring claim honest: if a slow file ever lands
    in `onboarding`, this REDS and the paragraph must be re-measured.
    """
    assert not slow_leg_by_surface(load_manifest()).get("onboarding"), (
        "`onboarding` now owns slow-leg files, so an onboarding-pinned docs/drafts "
        "path DOES flip slow_run. Re-measure and update the 'Known remaining "
        "hole' paragraph in test_website_docs_consistency.py's "
        "test_every_guard_input_is_selectable_by_ci, which currently records the "
        "opposite."
    )
    for changed in (["website/docs.html"], ["website/faq.html"],
                    ["website/apps/dashboard/public/welcome.html"],
                    ["website/self-hosted.html"],
                    ["website/product.html"], ["website/index.html"],
                    ["website/apps/dashboard/public/signup.html"],
                    ["website/privacy.html"],
                    ["docs/README.md", "website/self-hosted.html"]):
        r = _sel(changed)
        assert r["surfaces"] == ["onboarding"], changed
        assert r["full"] is False
        assert r["slow_run"] is False
        assert r["carve_out_run"] is False
        assert r["slow_selected"] == []
        assert "test_website_docs_consistency.py" in r["test_files"], changed


def test_product_root_marker_guard_runs_on_a_legal_pages_only_diff():
    """PR #9652 review (P2): the marker guard must run on the FILE it parses.

    `tests/test_website_static.py::TestProductRootMarkerContract` pins three
    surfaces together: `website/product.html`, the `PRODUCT_ROOT_MARKER`
    literal in `tests/e2e/test_legal_pages.py`, and the DNS preflight grep in
    `.github/workflows/deploy-pages.yml`. Only the first selected the guard —
    `tests/e2e/test_legal_pages.py` matched no SOURCE_PATTERNS row and fell
    back to `core`, where `test_website_static.py` (registered `onboarding`
    only) does not live.

    So re-cutting the marker by editing ONLY the e2e constant — the file the
    contract is named after, and the edit PR #8597 and PR #9652 each had to
    make — ran no guard, and the workflow kept grepping the old string: the
    preflight's `::warning::` branch then skipped the tortoise-host half green.
    MEASURED before the entries: `_sel(["tests/e2e/test_legal_pages.py"])` ->
    `surfaces == ["core"]` with `test_website_static.py` ABSENT.

    Both halves are asserted because the fix has both: the `onboarding`
    SOURCE_PATTERNS row selects the guard, and the `CORE_ALSO` entry keeps
    `core` (a named-surface match REPLACES the `tests/` -> `core` fallback).

    Mutation check: dropping the SOURCE_PATTERNS row fails the `onboarding`
    assert (surfaces become `["core"]`); dropping the CORE_ALSO entry fails
    the `core` assert (surfaces become `["onboarding"]`).
    """
    changed = "tests/e2e/test_legal_pages.py"
    assert (REPO / changed).exists(), f"the marker contract's subject is gone: {changed}"
    r = _sel([changed])
    assert r["full"] is False, (changed, r["full"])
    assert "onboarding" in r["surfaces"], (
        f"{changed} selects {r['surfaces']} — the guard that parses its "
        "PRODUCT_ROOT_MARKER (test_website_static.py) never runs, so a marker "
        "move in this file merges with the deploy preflight grepping a string "
        "the page no longer serves (#8596/#8597 class, #9652 review P2)"
    )
    assert "test_website_static.py" in r["test_files"], (
        f"{changed} does not select test_website_static.py — the served-root "
        "marker contract has no guard on the one file that defines the marker"
    )
    assert "core" in r["surfaces"], (
        f"{changed} selects {r['surfaces']} — the onboarding SOURCE_PATTERNS "
        "match replaced the `tests/` -> core fallback, so every core-registered "
        "test silently drops off the diff (CORE_ALSO entry removed?)"
    )


def test_the_capacity_guards_derived_source_selects_the_guard():
    """#3329/#8492: a change to the guard's DERIVED SOURCE must select the guard.

    `tests/test_capacity_page.py` derives the published write-concurrency figure
    from `tests/test_embedded_concurrency.py`'s own loop bound. The two docs it
    reads (`docs/capacity.md`, `docs/quickstart-selfhosted.md`) were registered
    as `onboarding` `SOURCE_PATTERNS`, but the TEST FILE supplying the derived
    value was not — so a diff that bumped the live test's coverage to six
    writers and left the prose stale selected only `core`, excluded the guard,
    and merged green. The drift then surfaced only on the `push`-event full run,
    reddening `main` AFTER the merge — the failure mode the `#3442` guard in the
    same workflow refuses to certify.

    Asserted on the SURFACE and on the guard file, not merely "surfaces is
    non-empty": `tests/` falls through to `core` on its own, so a bare
    non-emptiness check passes with the wiring absent — a gate that can only
    ever pass. Watched RED before the `SOURCE_PATTERNS` entry existed, GREEN
    after.
    """
    changed = "tests/test_embedded_concurrency.py"
    assert (REPO / changed).exists(), f"guarded derived source is gone: {changed}"
    r = _sel([changed])
    assert "onboarding" in r["surfaces"], (
        f"{changed} selects {r['surfaces']} — its guard (test_capacity_page.py) "
        "is not selected, so a coverage bump there merges with stale prose and "
        "reds main after the merge"
    )
    assert "test_capacity_page.py" in r["test_files"], (
        f"{changed} does not select test_capacity_page.py — the derived "
        "write-concurrency figure has no guard on the change that invalidates it"
    )


def test_the_declaration_gate_selects_its_own_proofs():
    """#10044 review F1: a change to the APPROVED-surface gate must select its proofs.

    `tools/sdk_surface_declaration.py` is executed by the REQUIRED `surface-guard`
    job (from the PR's own tree) and its proofs live in
    `tests/test_sdk_surface_declaration.py`, registered `api` + `core`. Both
    registrations were unreachable for the diff that can neuter the gate: `tools/`
    is in `NON_PYTHON_PREFIXES` and no `SOURCE_PATTERNS` row claimed this module, so
    the changed set emptied (`surfaces: []`, `full: false`) and the required job ran
    an edited gate whose 17 proofs never executed — the #1349/#3332/#3616
    silent-drop class, and the one bypass the "residual bypass, stated" paragraph in
    `tools/surface_manifest.py` does not cover.

    Asserted on the SURFACE and on the PROOF FILE, not on non-emptiness: a bare
    "surfaces is non-empty" check passes for a `tests/`-relative path on its own.
    Watched RED before the `SOURCE_PATTERNS["api"]` entry (`surfaces == []`),
    GREEN after.
    """
    changed = "tools/sdk_surface_declaration.py"
    assert (REPO / changed).exists(), f"the declaration gate is gone: {changed}"
    r = _sel([changed])
    assert "api" in r["surfaces"], (
        f"{changed} selects {r['surfaces']} — the gate's own proofs are not "
        "selected, so a gate-only PR can make `contract_problems()` return [] and "
        "merge with every required check green and none of its tests run (#10044 F1)"
    )
    assert "test_sdk_surface_declaration.py" in r["test_files"], (
        f"{changed} does not select test_sdk_surface_declaration.py — the gate "
        "that holds the owner's 40 has no proof on the diff that edits it"
    )


def test_the_engine_pin_guards_are_selected_on_the_files_they_pin():
    """#9085 review P1: a revert of the engine pin must run the guard that pins it.

    `tests/test_runner_ports_6673.py` is registered `core` and carries BOTH #8124
    guards that read these two files — the compose sweep (whose `COMPOSE_GLOBS`
    includes `apps/*/docker-compose*.yml`) and the quickstart hand-out
    (`test_the_selfhost_quickstart_hands_out_the_compose_pin_not_latest`).

    Neither path was kept by the selector, so a one-line revert of either pin
    merged green and reddened `main` on the post-merge push (the
    #1349/#3332/#3616 silent-drop class):

    * `apps/` is in `NON_PYTHON_PREFIXES` and matches no `SOURCE_PATTERNS` row,
      so the diff filtered to `changed == []` and took the docs-only early
      return — tier-1 smoke only (measured before the `CORE_ALSO` entries:
      `surfaces=[]`, the guard absent).
    * `docs/quickstart-selfhosted.md` matched the `#3329` `docs/` pattern that
      carries `test_capacity_page.py` (`onboarding`), not `core` (measured:
      `surfaces=['onboarding']`, the guard absent).

    Asserted on the SURFACE and on the guard FILE, not on non-emptiness: a bare
    "surfaces is non-empty" check passes on the pre-entry tree for the quickstart
    (`onboarding` is selected without the guard), which is a gate that can only
    ever pass. Watched RED before the two `CORE_ALSO` entries, GREEN after.
    """
    guard = "test_runner_ports_6673.py"
    for changed in ("apps/graph-viz/docker-compose.yml",
                    "docs/quickstart-selfhosted.md"):
        assert (REPO / changed).exists(), f"pinned file is gone: {changed}"
        r = _sel([changed])
        assert "core" in r["surfaces"], (
            f"{changed} selects {r['surfaces']} — the #8124 engine pin it carries "
            f"is guarded by a `core`-registered test, so a revert of that pin "
            f"merges green and reds main after the merge"
        )
        assert guard in r["test_files"], (
            f"{changed} does not select {guard} — nothing checks the engine pin "
            f"on the one diff that can revert it (selected: {r['test_files']})"
        )


def test_the_2684_census_is_selected_on_every_surface_that_moves_it():
    """#2684 review P2: the census must run on EVERY diff that can move it.

    `tests/eval/write_path/test_value_span_census_2684.py` re-derives numbers
    from THREE inputs, and each selects a different surface:

      * `tortoise/extractor_v2.py` -> `core` (the span mechanism),
      * `tortoise/retrieval.py` -> `sdk` (the dedupe half: `_pkg_overlap`,
        `_pkg_differ_value_critical`, `_pkg_turn_key` and both
        `DEFAULT_PACKAGE_*` thresholds) — an EXACT match that REPLACES the
        `core` fallback in `select()` (#2938),
      * the committed #7947 receipts + fixtures -> `eval` (the population).

    With only `core` + `eval` registered, a `tortoise/retrieval.py`-only diff
    selected `sdk` and 158 test files with the census ABSENT (measured), so
    §2.3's four published readings could go stale with the guard never running
    — the #1349/#2938 silent-drop class. The row is needed because the census
    would not run AT ALL on that diff, not because every predicate it uses is
    otherwise unpinned: `_pkg_turn_key`'s `0`-vs-`""` normalisation is pinned
    elsewhere, hermetically, by
    `tests/test_8658_product_hit_provenance.py::test_the_first_source_turn_is_a_turn`.

    Asserted on the SURFACE and the guard FILE, not on non-emptiness: every
    one of these diffs selects its surface without the census entry, so a bare
    `surfaces` check can only pass.

    ⛔ #10194 REVERSED THE SECOND HALF, DELIBERATELY (owner decision A,
    2026-10-27): `tests/eval/**` is now the NON-GATING lane, so the census no
    longer runs on any PR — a diff to `tortoise/retrieval.py` selects 158 files
    and NONE of them is the census. The surface mapping this test was built to
    prove is still correct and still asserted; what it must now also assert is
    the CONSEQUENCE, so nobody reads a green PR as "the census ran". The
    published §2.3 readings can go stale until evals reopen; that is the cost
    the owner accepted, and it is recorded here rather than left to be
    rediscovered. Reachable from `eval-suite-manual.yml`, never from a merge.
    """
    from tools.ci_selection import on_demand_files
    guard = "eval/write_path/test_value_span_census_2684.py"
    assert guard in on_demand_files(load_manifest()), (
        f"{guard} must be in the non-gating lane — #10194 took the eval subtree "
        "out of the merge gate; if it left the lane it is gating again")
    for changed, surface in (
            ("tortoise/extractor_v2.py", "core"),
            ("tortoise/retrieval.py", "sdk"),
            ("tests/eval/write_path/receipts/7947-arms-2026-10-18/"
             "wp01_quarry_debug.arms.json", "eval"),
            ("tests/eval/write_path/fixtures/wp01_quarry_debug.json",
             "eval")):
        assert (REPO / changed).exists(), f"pinned file is gone: {changed}"
        r = _sel([changed])
        assert surface in r["surfaces"], (
            f"{changed} selects {r['surfaces']}, not {surface} — the premise "
            "of this pin changed; re-derive it"
        )
        selected = set(r["test_files"]) | set(r["slow_selected"])
        assert guard not in selected, (
            f"{changed} selects {guard} again — a non-gating lane file is back "
            "on a pre-merge leg, which is the regression #10194 exists to "
            f"prevent (selected: {sorted(selected)[:8]} ...)"
        )


def test_the_9572_seed_surface_pins_are_selected_on_their_own_modules():
    """#9572 review P2-2: a seed-surface pin must run on the module it pins.

    `tests/test_9572_discriminator_not_clobbered.py` was registered `core`
    ONLY, and `core` is the FALLBACK that a `SOURCE_PATTERNS` match REPLACES in
    `select()`. Two of its cases pin the onboarding seed surfaces —
    `_OrgSeedSurface` in `tortoise/hosted_api.py` (an EXACT `api` match) and
    `_SdkSeedSurface` in `tortoise/onboarding/selfhost.py` (an `onboarding`
    prefix match) — so on a diff touching either module the file was not
    selected at all (measured before the entries: `surfaces=['api']` /
    `['onboarding']` with the guard ABSENT from `test_files`).

    That is the #1349/#2938 silent-drop class in its harmful direction: a PR
    that reintroduces `def create_subject(self, name, subjectKind="other",
    **props)` — literally the code #9572 removes — would run `api`/`onboarding`
    and never the guard. `git grep -n "_OrgSeedSurface\|_SdkSeedSurface"` over
    the tree finds no other test exercising either surface.

    Asserted on the FILE and the SURFACE, not on non-emptiness: BOTH surfaces
    are selected without the entry, so a `surfaces` check alone can only pass.
    """
    guard = "test_9572_discriminator_not_clobbered.py"
    for changed, surface in (
            ("tortoise/hosted_api.py", "api"),
            ("tortoise/onboarding/selfhost.py", "onboarding")):
        assert (REPO / changed).exists(), f"pinned file is gone: {changed}"
        r = _sel([changed])
        assert surface in r["surfaces"], (
            f"{changed} selects {r['surfaces']}, not {surface} — the premise "
            "of this pin changed; re-derive it"
        )
        assert guard in r["test_files"], (
            f"{changed} does not select {guard} — the seed-surface pin sleeps "
            "on the one diff that can re-fabricate the kind it forbids "
            f"(selected: {r['test_files']})"
        )


def test_the_4282_durability_suite_is_selected_on_the_modules_it_guards():
    """#10053 review P2: a durability invariant must run on the modules it pins.

    `tests/test_4282_dated_retraction_durability.py` was registered on `sdk`
    ONLY, and it pins three replay/reference arms that live in modules NO
    `SOURCE_PATTERNS` row claims — `tortoise/consistency.py`'s reference fold,
    `tortoise/record_projection.py`'s carrier, and
    `tortoise/projection/entities.py`'s `_retract`. None of the three is in
    `CORE_ALSO` either, so each falls through to the plain `tortoise/` arm and
    selects `core` ALONE; a file registered only on `sdk` is then in neither
    `tier1` nor `surface_members(matched)`. Measured before the `core` row:
    all three answered `surfaces=['core']` with the suite ABSENT from
    `test_files`, while `test_5048_retract_instant_recorded.py` — the sibling
    invariant, registered `core` — was present on the same diffs.

    That is the #1349/#2938 silent-drop class in its harmful direction: a
    follow-up that reverts one arm of the invariant (e.g. dropping
    `_storable_bound`'s END_UNKNOWN half) would run `core` and never the suite
    that pins it.

    Asserted on the FILE, not on non-emptiness: all three diffs select `core`
    with or without this row, so a `surfaces` check alone can only pass. The
    SIBLING assertion is the control — it proves the `core` block itself still
    works, so a red here is this row and not a moved manifest.
    """
    guard = "test_4282_dated_retraction_durability.py"
    sibling = "test_5048_retract_instant_recorded.py"
    for changed in (
            "tortoise/consistency.py",
            "tortoise/record_projection.py",
            "tortoise/projection/entities.py"):
        assert (REPO / changed).exists(), f"pinned file is gone: {changed}"
        r = _sel([changed])
        assert r["surfaces"] == ["core"], (
            f"{changed} selects {r['surfaces']}, not ['core'] — the premise of "
            "this pin changed; re-derive it")
        assert sibling in r["test_files"], (
            f"{changed} does not select {sibling} — the `core` block itself "
            "moved; re-derive this pin")
        assert guard in r["test_files"], (
            f"{changed} does not select {guard} — the durability suite sleeps "
            "on the one diff that can break the invariant it pins "
            f"(selected: {r['test_files']})")


def test_the_spa_override_guard_is_selected_on_every_manifest_it_reads():
    """#9524 review I1/I3: a dependency-manifest diff must run the override guard.

    `tests/test_spa_dependency_overrides.py` reads six files — the
    `package.json` + `package-lock.json` of the three SPAs — and two of those
    apps' manifests match NO `SOURCE_PATTERNS` row while `apps/` and `website/`
    are both in `NON_PYTHON_PREFIXES`. Before the `CORE_ALSO` entries, such a
    diff filtered to `changed == []` and took the docs-only early return (tier-1
    smoke only) — measured on the branch that fixed I1/I3:
    `select(['apps/graph-viz/package.json'])` -> `surfaces=[]` with the guard
    absent. That is the #1349/#3332/#3616 silent-drop class on exactly the diff
    the guard exists for: the graph-viz arm is the one that reds when the
    manifest loses its `nanoid`/`source-map-js` floors again.

    blog-admin reaches the guard through the OTHER arm: `website/apps/blog-admin/`
    is a `onboarding` SOURCE_PATTERNS row, where the guard is dual-registered.

    Asserted per FILE, on the guard's presence, not on non-emptiness: the
    pre-entry tree selects tier-1 smoke for these paths, which is non-empty and
    contains no guard.
    """
    guard = "test_spa_dependency_overrides.py"
    for app, surface in (("apps/graph-viz", "core"),
                         ("website/apps/dashboard", "core"),
                         ("website/apps/blog-admin", "onboarding")):
        for name in ("package.json", "package-lock.json"):
            changed = f"{app}/{name}"
            assert (REPO / changed).exists(), f"pinned file is gone: {changed}"
            r = _sel([changed])
            assert surface in r["surfaces"], (
                f"{changed} selects {r['surfaces']} — the override guard is "
                f"registered on `{surface}` and a manifest-only diff must reach it"
            )
            assert guard in r["test_files"], (
                f"{changed} does not select {guard} — a dependency override could "
                f"lose its floor (or gain one that clears nothing) with every "
                f"check green"
            )


def test_the_monospace_guard_is_selected_on_every_root_it_scans():
    """#6870 review P1: the mono-stack guard must RUN on `apps/` and `tortoise/oauth.py`.

    `tests/test_website_static.py::TestMonospaceStackRendersAsAMonoFace` widened
    its scan to `ROOTS = ("website", "apps")` plus `MODULES = ("tortoise/oauth.py",
    "tortoise/render.py")`, but is registered on the `onboarding` surface ONLY —
    and neither widened root selected it (measured at the pre-entry head):

    * `apps/` is in `NON_PYTHON_PREFIXES` and matched no `SOURCE_PATTERNS` row,
      so a graph-viz-only diff filtered to `changed == []` and took the docs-only
      early return — `surfaces=[]`, tier-1 smoke, the guard absent (the 8
      canvas/JSX declarations it was widened to reach).
    * `tortoise/oauth.py` matches the `api` row, so an oauth-only diff selected
      `['api', 'core']` and NOT `onboarding` — leaving the served MCP consent
      page, the one place the Courier defect was customer-facing, unguarded.

    Same #1349/#3332/#3616 silent-drop class the sibling pins above document.
    Asserted on the SURFACE and on the guard FILE: a bare "surfaces is not
    empty" check passes for the graph-viz path the moment anything else claims
    it, which is a gate that can only ever pass. Watched RED before the two
    `SOURCE_PATTERNS["onboarding"]` entries, GREEN after.
    """
    guard = "test_website_static.py"
    for changed, keeps in (("apps/graph-viz/src/index.css", ()),
                           ("apps/graph-viz/src/components/ForceGraphView.jsx", ()),
                           ("tortoise/oauth.py", ("api", "core")),
                           # Declared in the guard's `MODULES`; it carries no
                           # declaration today, which is why nothing but this
                           # pin forces its selection wiring.
                           ("tortoise/render.py", ("core",))):
        assert (REPO / changed).exists(), f"guarded path is gone: {changed}"
        r = _sel([changed])
        assert guard in r["test_files"], (
            f"{changed} does not select {guard} — the mono declaration it carries "
            f"(or may carry) is unguarded on the one diff that can revert it "
            f"(selected surfaces: {r['surfaces'] or 'none'})")
        # A `SOURCE_PATTERNS` match REPLACES the `core` fallback, so a path whose
        # own guard lives on `core` must also be in `CORE_ALSO`.
        assert set(keeps) <= set(r["surfaces"]), (
            f"{changed} selects {r['surfaces']} — wiring it into `onboarding` "
            f"dropped {sorted(set(keeps) - set(r['surfaces']))}, whose guards "
            f"now never run on its own diff")


def test_the_onboarding_copy_gate_is_wired_not_left_to_the_tier1_fallback():
    """#3673 indicator (2): the parity gate runs on every PR touching either copy.

    The gate is `test_onboarding_variants.py::test_m8_deploy_mirror_matches_canonical`.
    Before this entry existed, `website/apps/dashboard/public/skills/` matched NO
    SOURCE_PATTERNS entry, so an edit to the SERVED copy alone produced
    `surfaces=[]` and the parity test ran only through the tier-1 fallback —
    coverage that held by accident and that would vanish the moment the file left
    `tier1`. For the installer the consequence was worse: its guard,
    `test_installer_preserves_foreign_skill_content.py`, is on `core` and NOT in
    `tier1`, so an installer-only PR ran no guard for the installer at all — the
    #1349/#3332/#3616 silent-drop class this file exists to prevent.

    Asserted on the SURFACE, not merely on the test-file list: the tier-1
    fallback also puts `test_onboarding_variants.py` in `test_files`, so a
    test-file-only assertion passes with the wiring absent — a gate that can only
    ever pass. Watched RED before the SOURCE_PATTERNS entries existed, GREEN
    after, which is the only evidence that distinguishes the two.
    """
    cases = (
        # the two tracked copies whose byte-identity IS the parity contract
        ("tortoise/onboarding/SKILL.md", "test_onboarding_variants.py"),
        ("website/apps/dashboard/public/skills/tortoise-onboarding/SKILL.md",
         "test_onboarding_variants.py"),
        # a served sibling — the same directory, the same gate
        ("website/apps/dashboard/public/skills/how-to-use-tortoise/SKILL.md",
         "test_onboarding_variants.py"),
        # the installer whose SKILLS=(...) the dashboard's claim is pinned against
        ("website/apps/dashboard/public/install-tortoise-skills.sh",
         "test_installer_preserves_foreign_skill_content.py"),
    )
    root = Path(__file__).resolve().parents[1]
    for changed, guard in cases:
        assert (root / changed).exists(), f"guarded path is gone: {changed}"
        r = _sel([changed])
        assert r["surfaces"] == ["onboarding"], (
            f"{changed} selects {r['surfaces']} — its guard runs only via the "
            f"tier-1 fallback, which is not a wiring")
        assert guard in r["test_files"], f"{changed} does not select {guard}"


def test_every_source_pattern_is_selectable():
    """The ratchet: every SOURCE_PATTERNS entry must reach `select()`.

    This is the invariant whose absence let the same bug ship twice — #1349 for
    `tools/` (patched with the TOOL_CARVEOUTS mirror) and #3332 for `website/`
    (where the SOURCE_PATTERNS entries for welcome.html and self-hosted.html were
    dead on arrival, and the hand-maintained mirror that replaced them caught only
    4 of the 10 guarded paths). A pattern that a NON_PYTHON_PREFIXES prefix would
    filter out must be re-included by `select()`'s `_selection_relevant` rule.
    Derived from SOURCE_PATTERNS rather than enumerated, so a future entry cannot
    be added and silently not run — prior review (#2994) rejected an enumerated
    guard for exactly this reason.

    Direction: entry -> runs. This does NOT catch the reverse (a guarded page with
    no SOURCE_PATTERNS entry at all) — that direction is tracked separately.
    """
    assert "onboarding" in load_manifest()["surfaces"]
    dead = []
    for surface, pats in SOURCE_PATTERNS.items():
        if surface == "core":
            continue
        for pat in pats:
            if not _sel([pat])["surfaces"]:
                dead.append((surface, pat))
    assert not dead, (
        f"SOURCE_PATTERNS entries that select NO surface (dead on arrival — the "
        f"path is filtered by NON_PYTHON_PREFIXES before SOURCE_PATTERNS is "
        f"consulted, so no guard test for that file ever runs): {dead}"
    )


def _tracked_files(root: Path) -> list[str]:
    """The TRACKED file set — what `select()` reasons about, and what CI sees.

    `git ls-files`, deliberately not `os.walk`. A walk is both slower and WRONG:
    it counts untracked local build artifacts, and this repo carries ~148 sibling
    checkouts under `.worktrees/` (measured: ~720k files / ~23s walked, vs ~2.4k
    files / ~0.9s tracked). A dead entry could then look alive locally while
    failing in CI — a false negative in exactly the environment a developer runs
    in. `node_modules` is NOT excluded either. (#3768: nothing under `node_modules`
    is tracked any more, but the exclusion predicate is about the PATH PATTERN,
    not about what the tree currently contains — excluding by path would still
    diverge from git whenever any node_modules did appear.)
    """
    try:
        proc = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=root,
            check=False,
            capture_output=True,
        )
    except FileNotFoundError as exc:  # no git binary at all
        raise AssertionError(
            "git is not on PATH, so the tracked set is unknown — this test "
            f"cannot decide liveness: {exc}"
        ) from exc
    # check=False + an explicit assert, rather than check=True: check=True would
    # raise a bare CalledProcessError with no context, so an sdist export with no
    # `.git`, or `fatal: detected dubious ownership`, would look like every entry
    # being dead rather than like a broken environment.
    assert proc.returncode == 0, (
        "git ls-files failed, so the tracked set is unknown — this test cannot "
        "decide liveness and must not report every entry as dead: "
        + proc.stderr.decode("utf-8", "replace").strip()
    )
    return [p for p in proc.stdout.decode("utf-8").split("\0") if p]


def test_source_patterns_all_name_something_real():
    """Every SOURCE_PATTERNS entry must name a file (or directory) that EXISTS.

    Why this exists — #4171. The admin-origin move DELETED
    `website/functions/admin/[[path]].ts`, and its SOURCE_PATTERNS entry stayed.
    That is not cosmetic staleness: entries are matched with `startswith`, never
    against the filesystem, so a deleted path keeps "selecting" its surface for a
    file nobody can edit. The PR that moves a guarded file to a new path
    therefore selects NO surface for the new location, and the guard written for
    that exact file silently stops running — the #1349/#3332 silent-drop class,
    reached through a door the existing ratchet does not cover.

    `test_every_source_pattern_is_selectable` cannot see this failure: a dead
    path still matches its OWN pattern, so it "runs" its surface fine — there is
    simply nothing left that can edit it. This is a third FORWARD check
    (entry -> exists), NOT the reverse direction (guarded path -> has an entry),
    which is still hand-pinned per guard wherever an author remembered to (see
    `test_website_docs_consistency.py::test_every_guard_input_is_selectable_by_ci`).
    The reverse direction remains the open half of this class.

    Existence is checked against the tracked set, in the two shapes
    SOURCE_PATTERNS actually uses. No glob branch: no entry is a glob, and
    `select()` itself has no glob support, so a glob-shaped entry satisfied here
    would still select nothing — the test would bless a dead entry.
    """
    root = Path(__file__).resolve().parents[1]
    tracked = set(_tracked_files(root))

    def names_something_real(pattern: str) -> bool:
        # A real tracked path is always live.
        if pattern in tracked:
            return True
        # Otherwise it must name a SUBTREE, anchored on the separator. `select()`
        # matches with `startswith`, so a directory entry is live with OR without
        # its trailing slash (`tortoise/onboarding` and `tortoise/onboarding/`
        # both match). Reporting the slash-less form as "names NOTHING" would be
        # false — it IS live — and would disagree with
        # `test_every_source_pattern_is_selectable`, which accepts it.
        return any(f.startswith(pattern.rstrip("/") + "/") for f in tracked)

    dead = sorted(
        f"[{surface}] {pat}"
        for surface, pats in SOURCE_PATTERNS.items()
        for pat in pats
        if not names_something_real(pat)
    )

    assert not dead, (
        "SOURCE_PATTERNS entries naming NOTHING tracked — a deleted or moved "
        "guarded path keeps its entry, so a change to the file's NEW location "
        "selects no surface and its guard silently stops running (#4171). "
        "Repoint the entry at the new path, or delete it:\n  "
        + "\n  ".join(dead)
    )


# ── #7076: a guard that its own source's diff never SELECTS ───────────────────
# `test_every_source_pattern_is_selectable` is the FORWARD check (entry -> runs);
# `test_source_patterns_all_name_something_real` is entry -> exists, and its
# docstring names the REVERSE direction (guarded path -> has an entry) as "the
# open half of this class". These two tests are that half, for the subset where
# the repository's own naming convention makes the pairing decidable:
# `tests/**/test_<stem>.py` guarding `tortoise/**/<stem>.py`.
#
# WHY THE NAME CONVENTION IS ENOUGH. It is not a guess about intent: a
# `test_<stem>.py` whose stem also names a real `tortoise/<stem>.py` is a
# declaration the repository already makes, and the failure it detects is the one
# #7076 measured — a guard registered on surface A while its source falls through
# to surface B (#2159 first-match), so a source-only diff runs A's pool and NOT
# the file's own test. #7076 is the instance in hand; the same shape was reached
# before through #1349, #3332, #3616, #3950, #4171 and #5545.
#
# NOT CLAIMED, and stated so a reader does not over-read an entry:
#   * pairings the convention cannot express (a guard named after the BEHAVIOUR
#     rather than the module — most of the suite, which is why this covers 104
#     of ~795 guards);
#   * the slow / carve-out / on-demand legs as a *classification* question. The
#     predicate honours those legs — a test that runs in `slow_selected`, or in
#     the carve-out pool when that leg runs, counts as running — and a test the
#     on-demand lane CLASSIFIES is skipped outright, because "does not gate the
#     merge" is that lane's deliberate meaning rather than drift (the same
#     reason `integrity()` skips it);
#   * MARKER-selected whole-tree jobs. `test-d14-hosted-api` runs
#     `pytest tests/ -m embedded_only` on every PR, so an individually marked
#     param executes regardless of which surface owns its file: `test_audit.py`
#     below is the live example (its `embedded_busy` param runs there, while
#     none of the file is selected for a `tortoise/audit.py` diff). Membership
#     here therefore means "NO diff-selected leg runs it", which is the property
#     this check can decide — NOT "no line of it ever executes". Marker coverage
#     is a whole-tree mechanism with its own pin (`tests/test_markers.py::
#     test_ci_runs_the_embedded_only_marker_selection`).
#
# WHY A FROZEN DEBT LIST AND NOT 21 FIXES: each remaining pair is a per-file
# registration decision about which surface's pool should carry that guard, and
# every one of them edits `config/ci-surfaces.yml` (the `merge=union` file). The
# list stops the class GROWING while it is paid down one PR at a time. It is
# asserted in BOTH directions, so a fixed pair must be deleted from it and a new
# pair must be added deliberately — it cannot rot into fiction.
_SELF_GUARD_DEBT: dict[str, str] = {
    "tests/test_abuse.py": "tortoise/abuse.py",
    "tests/test_audit.py": "tortoise/audit.py",
    "tests/test_audit_events.py": "tortoise/audit_events.py",
    "tests/test_auth.py": "tortoise/auth.py",
    "tests/test_billing.py": "tortoise/billing.py",
    "tests/test_capture_consent.py": "tortoise/capture_consent.py",
    "tests/test_chain_enforcer.py": "tortoise/chain_enforcer.py",
    "tests/test_commit_schema.py": "tortoise/commit_schema.py",
    "tests/test_github_indexer.py": "tortoise/indexer/github_indexer.py",
    "tests/test_github_issue.py": "tortoise/github_issue.py",
    "tests/test_github_map.py": "tortoise/github_map.py",
    "tests/test_kind_classifier.py": "tortoise/kind_classifier.py",
    "tests/test_kind_index.py": "tortoise/kind_index.py",
    "tests/test_mcp_client.py": "tortoise/mcp_client.py",
    "tests/test_sentry.py": "tortoise/sentry.py",
    "tests/test_source_credibility.py": "tortoise/source_credibility.py",
    "tests/test_subgraph_render.py": "tortoise/subgraph_render.py",
    "tests/test_telegram_push.py": "tortoise/telegram_push.py",
    "tests/test_tortoise_client.py": "tortoise/tortoise_client.py",
    "tests/test_version_vector.py": "tortoise/version_vector.py",
}


def _self_guard_violations(manifest: dict, tracked: list[str]) -> dict[str, str]:
    """{test path: its source path(s)} for every named guard its diff never SELECTS.

    "Never selected" is decided as a UNION over the legs a pull_request diff can
    schedule — the fast selection, the slow leg's diff-gate selection, and the
    carve-out pool when that leg runs — because a guard counts as scheduled
    wherever it is selected, and reporting a slow-lane guard as missing would be
    a false alarm about the wrong contract.

    SCOPE. A marker-selected whole-tree job (`test-d14-hosted-api`) can execute
    an individual marked param whatever this returns, so an entry here means
    "no diff-selected leg runs the file", not "nothing in the file executes".
    The narrower claim is the one that is decidable from `select()`, and it is
    the one #7076 is about.
    """
    sources: dict[str, list[str]] = {}
    for path in tracked:
        if path.startswith("tortoise/") and path.endswith(".py"):
            sources.setdefault(Path(path).stem, []).append(path)
    on_demand = on_demand_files(manifest)
    carve = cs.carve_out_files(manifest)
    violations: dict[str, str] = {}
    for path in sorted(tracked):
        if not path.startswith("tests/") or path.startswith("tests/e2e/"):
            continue
        name = Path(path).name
        if not (name.startswith("test_") and name.endswith(".py")):
            continue
        if name in on_demand or path[len("tests/"):] in on_demand:
            continue
        srcs = sources.get(name[len("test_"):-3])
        if not srcs:
            continue
        sel = select(srcs, "pull_request", manifest)
        if sel["full"] or sel["test_files"] == "ALL":
            continue
        ran = set(sel["test_files"]) | set(sel.get("slow_selected") or ())
        if sel.get("carve_out_run"):
            ran |= carve
        if name in ran or path in ran or path[len("tests/"):] in ran:
            continue
        violations[path] = ", ".join(srcs)
    return violations


def test_the_degraded_fallback_guard_runs_for_its_own_source():
    """#7076, pinned as BEHAVIOUR so the fix cannot be undone by a debt entry.

    tests/test_fallback_snapshot.py was registered only under `api` while
    tortoise/fallback_snapshot.py falls through to `core`, so a
    fallback_snapshot.py-only diff ran the `core` pool and NOT the file's own
    test. The ratchet below would accept the pair reappearing in
    `_SELF_GUARD_DEBT`; this asserts the selection instead of the registration.
    """
    sel = _sel(["tortoise/fallback_snapshot.py"])
    assert sel["surfaces"], (
        "tortoise/fallback_snapshot.py now selects NO surface, so its guard "
        "would run only through the tier-1 fallback — coverage by accident, "
        "which vanishes the moment the test leaves tier1 (#3673)"
    )
    assert "test_fallback_snapshot.py" in sel["test_files"], (
        "the file's own guard is not selected by its own diff (#7076): "
        f"{len(sel['test_files'])} files ran, test_fallback_snapshot.py was not "
        "one of them"
    )


def test_the_cross_surface_grid_guard_runs_for_each_side_of_its_declaration():
    """#3513 review P1: the grid inventory test asserts BOTH directions of ONE
    declaration — the JS grid's tile keys ↔ ``tortoise/demand.py`` ↔
    ``config/connector_manifest.yaml``. It was registered only under
    ``onboarding``, so the two BACKEND sources (which match no named surface and
    select the ``core`` fallback) never ran it: a connector added to the manifest
    with no grid tile and no mapping was green on its own PR and surfaced as an
    unrelated red in the next dashboard PR (#1349/#7076 class).

    Pinned as BEHAVIOUR, not as a YAML string: every side of the declaration
    must select the file that asserts it.
    """
    for source in (
        "website/apps/dashboard/src/integrationsGrid.js",
        "tortoise/demand.py",
        "config/connector_manifest.yaml",
    ):
        sel = _sel([source])
        assert "test_integrations_grid_inventory.py" in sel["test_files"], (
            f"{source} selects {sel['surfaces']} but does NOT run the guard that "
            "asserts the tile declaration it moves (#3513 review P1) — a drift "
            "introduced on this side of the cross-surface pair would be silent"
        )


def test_every_named_guard_runs_for_its_own_source():
    """The reverse of `test_every_source_pattern_is_selectable`: path -> selected.

    Every `tests/**/test_<stem>.py` whose stem also names a tracked
    `tortoise/**/<stem>.py` must be SELECTED for a diff of that source alone.
    A pair that is not is the #7076 class: the file's guard does not run on its
    own change's diff-selected legs, and the green check reads as coverage.

    Scope: "selected", not "not one byte of it executes" — see
    `_self_guard_violations` for the marker-selected whole-tree job that this
    predicate cannot (and does not claim to) model.

    Both directions are asserted. New pairs fail (the class must not grow); debt
    entries that now pass also fail (the frozen list must stay true, so fixing a
    pair is completed by deleting its entry).
    """
    root = Path(__file__).resolve().parents[1]
    violations = _self_guard_violations(load_manifest(), _tracked_files(root))
    new = sorted(set(violations) - set(_SELF_GUARD_DEBT))
    fixed = sorted(set(_SELF_GUARD_DEBT) - set(violations))
    assert not new, (
        "guards that their own source's diff does not run (#7076 class — the "
        "same shape as #1349/#3332/#3616/#3950/#4171/#5545). Register the test "
        "on the surface its source selects, or the source on the test's surface, "
        "rather than adding it to _SELF_GUARD_DEBT:\n  "
        + "\n  ".join(f"{p} <- {violations[p]}" for p in new)
    )
    assert not fixed, (
        "pairs listed in _SELF_GUARD_DEBT that now PASS, so the debt list has "
        "gone stale — delete them (the list is asserted in both directions so "
        "it cannot rot into fiction):\n  "
        + "\n  ".join(f"{p} <- {_SELF_GUARD_DEBT[p]}" for p in fixed)
    )


def test_unrelated_website_change_stays_tier1():
    """SITE_CARVEOUTS is not a wholesale `website/` removal.

    A website path that owns no guard test keeps the old docs-only behavior
    (empty changed -> tier-1 smoke), so unrelated website edits do not drag the
    onboarding surface in.
    """
    r = _sel(["website/robots.txt"])
    assert r["surfaces"] == []
    assert r["full"] is False
    assert set(r["test_files"]) == _tier1()


def test_onboarding_change_selects_onboarding():
    r = _sel(["tortoise/onboarding/SKILL.md"])
    assert r["full"] is False
    assert "onboarding" in r["surfaces"]
    # #6884: the tier-2 set subtracts `uri_requiring` as well as `carve_out` —
    # those modules module-skip at import in a URI-less leg, collect zero tests
    # and red the leg unattributably. The shipped defect and its arm are in
    # tests/test_uri_requiring_selection.py.
    # #6884: read the lane through the accessor, NOT `manifest.get(key, [])` —
    # the `.get` default only applies when the key is ABSENT, while an empty
    # `uri_requiring:` in the YAML is present-and-None, so `set(None)` would
    # TypeError here on the lane's terminal state (the last URI-gated module
    # fixed) even though `select()` itself is fixed.
    assert set(r["test_files"]) == ((_tier1() | set(load_manifest()["surfaces"]["onboarding"]))
                                     - set(load_manifest().get("carve_out", []))
                                     - uri_requiring_files(load_manifest()))


def test_ep_change_selects_ep():
    r = _sel(["tortoise/ranking.py"])
    assert r["full"] is False
    assert r["surfaces"] == ["ep"]
    assert "test_decide.py" in r["test_files"]


def test_shared_module_goes_full():
    r = _sel(["tortoise/sdk.py"])
    assert r["full"] is True
    assert r["test_files"] == "ALL"
    r2 = _sel(["tests/conftest.py"])
    assert r2["full"] is True


def test_every_conftest_module_level_tests_import_is_shared():
    """#4069: a `tests/` helper conftest imports at MODULE level is suite-wide.

    `tests/conftest.py` re-exports suite-wide fixtures, so a `tests/` helper it imports at
    module level runs for EVERY surface's tests; the manifest never classifies it (it is
    not a `test_*.py` file), so unless it is in `SHARED_MODULES` a change to it selects
    `core` only and an api/onboarding/battery break it induces never runs on the PR that
    made it (the #1349/#3332/#3910 silent-under-selection class).

    Scope is deliberately `tests.*` only: this criterion justifies a *test helper* being
    suite-wide, not a product module (whose classification is its own path pattern plus
    the cross-cutting judgment list `SHARED_MODULES` carries). The product modules conftest
    imports at module level are therefore NOT covered here; that residual is measured on
    #4486, not asserted away.

    The import set is read from the AST and the walk is GENERIC: it recurses into every
    nested statement container (class bodies, `match` cases, `except*` blocks, with/for
    bodies, ...) and stops only at function-like nodes, whose bodies are not module level.
    Enumerating the containers to descend into is what let an earlier version of this ratchet
    be narrower than the rule it documents, so there is no such list here. Relative imports
    (`from . import _x`) are deliberately unmatched: `tests/` has no `__init__.py`, so they
    cannot appear at conftest module level today, and this test states that rather than
    pretending they are covered.
    """
    conftest_path = Path(__file__).resolve().parent / "conftest.py"
    module = ast.parse(conftest_path.read_text())
    imported: set[str] = set()

    def walk(node) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue  # a function-local import is not module level
            if isinstance(child, ast.Import):
                for alias in child.names:
                    if alias.name == "tests" or alias.name.startswith("tests."):
                        imported.add(alias.name)
                continue
            if isinstance(child, ast.ImportFrom):
                if child.level == 0 and child.module == "tests":
                    for alias in child.names:
                        imported.add(f"tests.{alias.name}")
                elif child.level == 0 and child.module and child.module.startswith("tests."):
                    imported.add(child.module)
                continue
            walk(child)

    walk(module)
    assert imported, "expected tests/conftest.py to import a tests.* module at module level"
    for module in sorted(imported):
        rel = module.replace(".", "/") + ".py"
        assert rel in SHARED_MODULES, (
            f"{rel} is imported at conftest MODULE level (so it runs for every "
            f"surface's tests) but is not in SHARED_MODULES — a change to it would "
            f"select core only")
        result = _sel([rel])
        assert result["full"] is True, result
        assert result["test_files"] == "ALL", result


def test_the_docker_lane_seam_forces_the_full_matrix():
    """#6673: `tests/_live_utils.py` carries the docker lane's port/host
    resolution and is imported by test modules registered on every docker-lane
    surface (core, ep, sdk, api, eval) — `git grep -l _live_utils -- tests/`
    lists every file that touches it (importers plus mention-only references). The conftest-derived ratchet cannot cover it (it is not a
    conftest import), so without the explicit entry a seam-only edit selected
    `core` and the other surfaces' consumers never ran on the PR that made it.
    """
    result = _sel(["tests/_live_utils.py"])
    assert result["full"] is True, result
    assert result["test_files"] == "ALL", result


def test_the_minted_identity_id_helper_forces_the_full_matrix():
    """#3590 round-5 review F4: `tests/_node_ids.py` is a CROSS-SURFACE helper.

    It replaced the retired `entity_name_id` derivation in every test that needs
    "the id of this name", and it is imported from modules registered on several
    surfaces — `test_sdk_group3.py` (sdk), `test_capture_session.py` /
    `test_ingest_event_endpoint.py` / `test_event_provenance.py` /
    `test_status_folds_2729.py` (core), `test_7936_stable_stub_ids.py`
    (core + sdk) and others. It is NOT a `test_*.py` file, so the manifest never
    classifies it: without its `SHARED_MODULES` entry a helper-only change
    selected `core` only and a break it induced in a sdk/api test never ran on
    the PR that made it (the #1349/#3332/#3910 under-selection class). Same
    reason — and same shape — as the `_live_utils.py` pin above; the generic
    `test_every_shared_module_entry_selects_the_full_matrix` cannot catch the
    entry going MISSING, which is the failure this names.
    """
    result = _sel(["tests/_node_ids.py"])
    assert result["full"] is True, result
    assert result["test_files"] == "ALL", result


def test_every_shared_module_entry_selects_the_full_matrix():
    """#4097: `SHARED_MODULES` is a hand-maintained list, so derive its invariant here.

    `test_shared_module_goes_full` pins two literal examples; a future entry that is
    added (or a cross-cutting leaf like `tortoise/env_truthy.py` that is REMOVED) would
    otherwise silently downgrade to `core`-only and stop running the consumer suites.
    """
    py_modules = [m for m in SHARED_MODULES if m.endswith(".py")]
    assert py_modules, "SHARED_MODULES should list python modules"
    for module in py_modules:
        result = _sel([module])
        assert result["full"] is True, f"{module} is in SHARED_MODULES but selects {result}"
        assert result["test_files"] == "ALL", module


def test_unknown_path_goes_full():
    # fail-closed: a path outside any known subtree must never silently
    # under-select (new top-level dir/subsystem → full matrix)
    r = _sel(["mystery-dir/x.py"])
    assert r["full"] is True


def test_root_level_non_python_files_skip_the_matrix():
    """#6784: an EXPLICIT root-level allowlist skips the matrix.

    Before this, a ROOT path matched no NON_PYTHON_PREFIXES entry, fell to the
    unknown-path branch, and reserved the FULL matrix (every shard slot) for a
    prose edit. The names are hard-coded here, NOT read from the module
    constant: emptying the allowlist must RED this test, which it cannot do if
    the assertion derives its expectation from the same set it is checking.

    ⛔ This set is the ZERO-READER census only, and it is much smaller than the
    change's first two revisions: every root file that a test reads KEEPS the
    fail-closed full matrix (see `test_root_file_read_by_a_test_is_never_allowlisted`).
    """
    allowlisted = (".scope-1894.md", ".scope-comment-2578.md", "pr-body.md")
    for name in allowlisted:
        r = _sel([name])
        assert r["full"] is False, f"{name} must skip the matrix, got {r}"
        assert r["surfaces"] == [], name
        assert set(r["test_files"]) == _tier1(), name


def test_every_allowlisted_root_file_actually_exists():
    """#6784 review cycle 2, P3: a liveness ratchet on the allowlist.

    `SOURCE_PATTERNS` has an equivalent ratchet (`test_source_patterns_all_name_
    something_real`). Without one, a name that does not exist in the tree is a
    dead entry that skips the matrix for nothing — which is exactly how the
    previous revision passed its own tests while changing NO existing file's
    behaviour (its four names were all absent on disk, so #6784's headline case
    was still unfixed and the review had to catch it by hand).
    """
    for name in sorted(cs.ROOT_NON_PYTHON_FILES):
        assert (REPO / name).exists(), (
            f"{name} is allowlisted but does not exist in the tree — a dead "
            "entry that skips the matrix for nothing")


# Files that only NAME the census/members rather than reading them: the census
# module quotes it, and this test file hard-codes the members.
_SELF_NAMERS = frozenset({"tools/ci_selection.py", "tests/test_ci_selection.py"})

# The five roots the mention census greps. `scripts/` carries a TRAILING SLASH
# deliberately (see `_readers_of`).
_CENSUS_ROOTS = ("tests", "tools", "scripts/", ".github", "tortoise")

# The materialiser for the `scripts/` root (#9475) — named in the announcement so
# the reader gets the REMEDY, not only the fact.
_SCRIPTS_MATERIALISER = "tools/link_agent_infra_scripts.py"

# Census losses already announced, keyed by the absent-root tuple (#9541 review round 6,
# minor 3). `test_no_allowlisted_root_file_has_a_reader` calls `_readers_of` TWICE per
# allowlisted name (~30 calls on a fresh clone), so an unconditional print emitted ~30
# identical `::warning::` annotations for ONE fact. Announcing once per distinct loss keeps
# the loss audible without burying the log; the register is per-process because a pytest
# session is the unit that reads it.
_CENSUS_ANNOUNCED: set[tuple[tuple[str, ...], int | None]] = set()


def _readers_of(name: str) -> list[str]:
    """Non-self files under tests/tools/scripts/.github/tortoise naming `name`.

    ⛔ `scripts/` carries a TRAILING SLASH deliberately: `scripts` is a SYMLINK
    (machine-local since #9475 — gitignored and materialised from
    `$AGENT_INFRA_PATH`), and BSD grep does not descend a symlinked directory
    named without one — so `grep -rlF <n> tests tools scripts .github tortoise`
    silently searches NOTHING under scripts/ on macOS (0 hits vs 72 for the same
    dir with the slash). The census numbers are only reproducible with the slash.

    ⛔ AND THE ROOT CAN BE ABSENT — which is ANNOUNCED, never silent (#9541
    review ISSUE 1). `scripts` is no longer tracked, so a fresh clone, a CI
    runner, and a lane before its first materialisation have no such path at
    all: grep prints `scripts/: No such file or directory` to stderr, exits 2,
    and uses only `.stdout`. The four present roots are still censused, but the
    `scripts/` one is MISSING, and both ratchets this feeds can only be as good
    as what the census saw — so the loss is printed with the command that
    restores it. Before this, the root vanished from every fresh checkout
    without a word while the ratchets stayed green.

    ⛔ ANNOUNCED ONCE per distinct loss, not once per call (#9541 review round 6,
    minor 3): the ratchets call this twice per allowlisted name, so an
    unconditional print emitted ~30 identical `::warning::` annotations for one
    fact and buried it. See `_CENSUS_ANNOUNCED`.

    SCOPE: a LITERAL-NAME grep. It does not see a reader that finds `name` by
    GLOB or directory walk, nor one outside these five roots.
    """
    result = subprocess.run(
        ["grep", "-rlF", name, *_CENSUS_ROOTS],
        cwd=REPO, capture_output=True, text=True)
    absent = [root for root in _CENSUS_ROOTS if not (REPO / root).is_dir()]
    # grep exits 1 for "no match" (normal here) and 2 for "a root could not be
    # read". Any other non-zero, with every root present, is a fault that must
    # not read as "no readers" — the same silent-narrowing class as an absent root.
    # Announced ONCE per distinct loss: the ratchets above call this twice per
    # allowlisted name, and ~30 identical annotations for one fact is noise that buries
    # it (#9541 review round 6, minor 3).
    loss = (tuple(absent), result.returncode if not absent else None)
    if (absent or result.returncode not in (0, 1)) and loss not in _CENSUS_ANNOUNCED:
        _CENSUS_ANNOUNCED.add(loss)
        missing = (
            f"{', '.join(absent)} absent from this checkout — a machine-local pointer that has "
            f"not been materialised (#9475: `scripts` is gitignored, not tracked; run `uv run "
            f"python {_SCRIPTS_MATERIALISER}`)"
            if absent else "every root is present"
        )
        print(
            f"::warning::MENTION CENSUS INCOMPLETE for {name!r}: {missing}; "
            f"grep exited {result.returncode}."
            + (f" {result.stderr.strip()}" if result.stderr.strip() else "")
            + " A reader living under a root that was not searched cannot be seen here.",
            file=sys.stderr,
        )
    out = result.stdout.split("\n")
    return sorted(h for h in (x.strip() for x in out)
                  if h and h not in _SELF_NAMERS and not h.endswith(".pyc"))


def test_the_mention_census_announces_an_unmaterialised_root(tmp_path, monkeypatch, capsys):
    """#9541 review ISSUE 1 (P3): the narrowing must be AUDIBLE, not silent.

    `scripts/` is untracked since #9475, so on a fresh clone, a runner, or a lane
    before its first materialisation the path is ABSENT: grep prints
    `scripts/: No such file or directory`, exits 2, and `_readers_of` kept only
    `.stdout` — so the root vanished from the census without a word and both
    ratchets it feeds stayed green while a reader under `scripts/` was
    unseeable. RED before the fix: nothing was printed at all, so the assertion
    below failed on empty stderr.

    The second half is the discriminator: with every root present the same call
    says nothing, so the message tracks the ABSENCE rather than being emitted
    unconditionally.

    The register is cleared first: the announcement is once-per-process, and an
    earlier test may already have announced the same absent root.
    """
    for root in ("tests", "tools", ".github", "tortoise"):
        (tmp_path / root).mkdir()
    # `_readers_of` greps at `REPO`, the module global in THIS file — patch that one.
    monkeypatch.setattr(sys.modules[__name__], "REPO", tmp_path)
    monkeypatch.setattr(sys.modules[__name__], "_CENSUS_ANNOUNCED", set())
    assert tmp_path == REPO, "the census must run against the throwaway tree"

    # `scripts/` deliberately ABSENT — the fresh-clone / runner / new-lane state.
    assert _readers_of("AGENTS.md") == []
    absent_message = capsys.readouterr().err
    assert "MENTION CENSUS INCOMPLETE" in absent_message, (
        "the `scripts/` root is absent and the census said nothing: the two ratchets this "
        "feeds (test_no_allowlisted_root_file_has_a_reader, test_no_content_reader) then "
        "report a complete census over four of five roots"
    )
    assert "scripts/" in absent_message, absent_message
    # The reader gets the REMEDY, not just the fact (the materialiser this PR ships).
    assert "tools/link_agent_infra_scripts.py" in absent_message, absent_message

    # Control: the pointer materialised — nothing to announce.
    (tmp_path / "scripts").mkdir()
    _readers_of("AGENTS.md")
    assert capsys.readouterr().err == ""


def test_the_mention_census_announces_an_absent_root_ONCE(tmp_path, monkeypatch, capsys):
    """#9541 review round 6, minor 3: the announcement must not repeat per call.

    `test_no_allowlisted_root_file_has_a_reader` calls `_readers_of` twice for each
    allowlisted root file, so on a fresh clone / runner the unconditional print emitted
    ~30 identical `::warning::MENTION CENSUS INCOMPLETE` annotations for ONE absent root
    — the signal buried in its own repetitions, which is how an announcement stops being
    read. RED before the fix: the second call printed the message again.
    """
    for root in ("tests", "tools", ".github", "tortoise"):
        (tmp_path / root).mkdir()
    monkeypatch.setattr(sys.modules[__name__], "REPO", tmp_path)
    monkeypatch.setattr(sys.modules[__name__], "_CENSUS_ANNOUNCED", set())

    _readers_of("AGENTS.md")
    first = capsys.readouterr().err
    assert "MENTION CENSUS INCOMPLETE" in first, first

    _readers_of("AGENTS.md")
    assert capsys.readouterr().err == "", (
        "the absent-root announcement repeated on a second call — the ratchets call this "
        "twice per allowlisted name (~30 identical ::warning:: lines for one fact)"
    )
    # A DIFFERENT loss is still announced: dedupe must not mute a new fact.
    (tmp_path / "tests").rmdir()
    _readers_of("AGENTS.md")
    assert "MENTION CENSUS INCOMPLETE" in capsys.readouterr().err, (
        "a newly-absent root was not announced — the dedupe keyed on the wrong thing"
    )


def test_no_allowlisted_root_file_has_a_reader():
    """#6784 review cycle 3 P2: derive the readers, do not list the names.

    The earlier guard was a hard-coded 11-name list, so it only defended names
    someone had thought of. Measured counterexamples: adding `.env.example` or
    `fly.toml` left the whole suite GREEN while silently skipping guards — the
    exact class this allowlist exists to prevent.

    ⛔ SCOPE — do not read this as "any future member whose file something reads
    will fail here" (review cycle 4, P2 falsified exactly that phrasing). This
    catches a LITERAL-NAME reader under `tests tools scripts/ .github tortoise`.
    A reader that GLOBS for the file (`REPO.glob("*.md")`), or that lives
    outside those roots (e.g. `docs/`), is NOT caught — both were demonstrated
    GREEN against this test. Before adding a member, grep the WHOLE tree.
    """
    offenders = {name: _readers_of(name) for name in sorted(cs.ROOT_NON_PYTHON_FILES)
                 if _readers_of(name)}
    assert not offenders, (
        "allowlisted root file(s) are READ by the listed files — allowlisting them "
        f"makes those guards skip on exactly the PR that edits the file: {offenders}")


def test_allowlist_members_are_real_root_level_files():
    """Companion to the invariant above: every member must be a ROOT-LEVEL
    tracked file, so the census is about real paths, not invented ones."""
    roots = subprocess.run(["git", "ls-files"], cwd=REPO,
                           capture_output=True, text=True).stdout.split("\n")
    root_set = {f for f in roots if f and "/" not in f}
    for name in sorted(cs.ROOT_NON_PYTHON_FILES):
        assert name in root_set, (
            f"{name} is allowlisted but is not a root-level tracked file in this tree")


def test_root_file_read_by_a_test_is_never_allowlisted():
    """#6784 review cycle 1 P1 + cycle 2 P1: a file a test READS is not "not
    python-relevant".

    `tests/test_embedded_durability_claim.py` reads `ROOT/README.md` and asserts
    its durability claim, and it is `core`+carve-out (NOT tier1) — so allowlisting
    README.md made that guard skip on exactly the PR that edits it. The census is
    published on issue #6784 (method: `grep -rlF "<name>" tests tools scripts/
    .github tortoise`, excluding both ci_selection files, which only name or quote
    the census) rather than duplicated here, because a duplicated number is a
    claim that re-stales: an earlier revision of this docstring cited
    `.env.example` as 16 readers when the method yields 15.

    This is the explicit spot-check for the files that matter most; the rule
    itself is pinned by `test_no_allowlisted_root_file_has_a_reader`. It REDs if
    the removed blanket `*.md`-at-root clause is reintroduced (it admitted
    README.md).

    #8785: CHANGELOG.md, CONTRIBUTING.md and CLAUDE.md are deliberately NOT in
    this list. They are prose whose CONTENT no test or tool reads (their only
    mentions are docstrings, a generated-doc link, or `record-review.sh`'s
    basename case), so they are reprieved to the tier-1 smoke by
    `NON_CODE_DOC_PATHS` — pinned by
    `test_non_code_doc_paths_have_no_content_reader`. Every name below IS read
    (README.md by `test_embedded_durability_claim.py:62`; AGENTS.md by the
    base-subset pin; `.gitignore` by `test_kind_index.py:290`; …) and keeps FULL.
    """
    for name in ("README.md", "AGENTS.md",
                 "LICENSE", "MEMORY.md", "index.md",
                 ".gitignore", ".env.example", "fly.toml", "pyproject.toml",
                 ".python-version"):
        r = _sel([name])
        assert r["full"] is True, (
            f"{name} is read by a test — it must keep the fail-closed FULL "
            f"matrix, got {r}")
        assert r["test_files"] == "ALL", name
    # `*.md` at the root is NOT blanket-safe: only explicitly named files are.
    r_md = _sel(["NOTES.md"])
    assert r_md["full"] is True, "root *.md must fail closed, not blanket-skip"
    assert r_md["test_files"] == "ALL"


def test_a_claimed_root_file_beats_the_allowlist(monkeypatch):
    """#6784 review P2: the allowlist is a FALLBACK, never a short-circuit.

    An earlier revision tested `_is_safe_root_file` FIRST, so listing a file in
    SOURCE_PATTERNS or CORE_ALSO — the repo's own documented remedy for "the
    guard for file X never runs when X changes" (#6138 review P1) — could no
    longer rescue it. Here a root-allowlisted name is CLAIMED via CORE_ALSO and
    must therefore stay selected.
    """
    claimed = ".scope-1894.md"
    assert _sel([claimed])["full"] is False, "precondition: unclaimed → skip"
    monkeypatch.setattr(cs, "CORE_ALSO", (*cs.CORE_ALSO, claimed))
    r = _sel([claimed])
    assert r["full"] is False, f"a claimed path must not become full: {r}"
    assert r["surfaces"] == ["core"], (
        f"a claimed root file must be KEPT and select its surface, got {r}")


def test_root_build_files_still_select_the_full_matrix():
    """Fail-closed must be PRESERVED: the root allowlist is NOT "root-level
    and no extension ⇒ safe" (that would admit Makefile/Dockerfile/build.sh)
    and NOT "any non-python extension ⇒ safe". Each of these remains
    code-relevant and must keep the FULL matrix."""
    for name in ("Makefile", "Dockerfile", "pyproject.toml", "uv.lock",
                 ".python-version", "requirements.txt", "conftest.py",
                 "setup.py"):
        r = _sel([name])
        assert r["full"] is True, f"{name} must fail closed to full, got {r}"
        assert r["test_files"] == "ALL", name


def test_unknown_root_file_still_selects_the_full_matrix():
    """An unknown ROOT-level path must still hit the fail-closed default:
    the allowlist is additive, it does not soften the unknown-path branch.
    (Reds if that fall-through defaults to skip instead of full.)"""
    for name in ("build.sh", "some_new_thing.xyz", "mystery.conf"):
        r = _sel([name])
        assert r["full"] is True, f"{name} must fail closed to full, got {r}"
        assert r["test_files"] == "ALL", name


def test_subdirectory_markdown_is_unaffected():
    """The root rule must not become "any *.md anywhere": `docs/x.md` keeps
    its NON_PYTHON_PREFIXES skip, and `tortoise/NOTES.md` keeps the `tortoise/`
    core fallback — it must NOT become a matrix skip by accident."""
    r_docs = _sel(["docs/x.md"])
    assert r_docs["full"] is False
    assert r_docs["surfaces"] == []
    r_tortoise = _sel(["tortoise/NOTES.md"])
    assert r_tortoise["full"] is False
    assert r_tortoise["surfaces"] == ["core"]


# ---------------------------------------------------------------------------
# #8785: a non-Python prose diff must not reserve the FULL test surface.
#
# The selector classified a root-level or `.github/` prose file as an UNKNOWN
# PATH (no NON_PYTHON_PREFIXES match, no SOURCE_PATTERNS claim) and failed
# closed to the whole matrix. These tests pin the distinction the fix adds —
# "no surface matched because the file is not code" (drop -> tier-1 smoke)
# versus "no surface matched because the path is unclassified and might carry
# code" (still FULL).
# ---------------------------------------------------------------------------

_READ_CALLS = frozenset({"open", "read_text", "read_bytes", "read",
                         "readlines", "load", "loads", "loadtxt"})

# The CI roots the selector's surface map is built on. `scripts/` is a SYMLINK to
# an agent-infra checkout outside this repo — gitignored and materialised per
# machine since #9475, not tracked — so it must be walked on disk, not read from
# `git ls-files`.
_NON_CODE_DOC_ROOTS = ("tests", "tools", "scripts", ".github", "tortoise")

# A shell read operation. `read\b` deliberately does NOT match "reads" (word
# boundary), so prose like "pi's loader reads AGENTS.md" is not a read op.
_SHELL_READ_RE = re.compile(r"(?:^|[^\w])(?:cat|grep|egrep|rg|source|read)\b|<")


def _content_read_in_shell(source: str, path: str) -> bool:
    """Line-level read-op detector for `.sh` files (never `ast.parse`)."""
    name = path.rsplit("/", 1)[-1]
    for line in source.splitlines():
        if name not in line:
            continue
        if _SHELL_READ_RE.search(line):
            return True
    return False


def test_non_code_doc_paths_are_prose():
    """#8785 review (B, P2): the tuple reprieves NON-CODE DOCUMENTATION.

    Every member must be a prose file. Without this, a code-bearing path can be
    added and satisfies BOTH the liveness and no-content-reader ratchets while
    silently dropping its own guard to the tier-1 smoke — the
    #1349/#3332/#3616 silent-drop class this tuple exists to avoid. (Measured:
    `entrypoint.sh` passes every other new test and skips
    `tests/test_boot_regressions.py`, which is not in tier1.)
    """
    for name in sorted(cs.NON_CODE_DOC_PATHS):
        assert name.endswith(".md"), (
            f"{name} is not a prose doc — NON_CODE_DOC_PATHS must not reprieve "
            "a code-bearing path")


def _content_read_in_py(source: str, path: str) -> bool:
    """Whether `source` READS `path`'s content (not merely names it).

    A literal equal to `path` (or ending in `/<path>`) anywhere inside a
    read-call expression — including the callee's OWN receiver, which is how
    `(ROOT / "README.md").read_text()` receives the path.

    ⛔ SCOPE, stated rather than overclaimed (same honest-scope limit as the
    #6787 mention census): this is a LITERAL-PATH read detector. It does NOT see
    an indirect read through a parameter/parametrize value, a glob/walk, or a
    shell `grep -r`. Before adding a member, grep the whole tree yourself.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        callee = fn.attr if isinstance(fn, ast.Attribute) else (
            fn.id if isinstance(fn, ast.Name) else "")
        if callee not in _READ_CALLS:
            continue
        for lit in ast.walk(node):
            if (isinstance(lit, ast.Constant) and isinstance(lit.value, str)
                    and (lit.value == path or lit.value.endswith("/" + path))):
                return True
    return False


def _content_readers_of(path: str) -> list[str]:
    """Files under the five CI roots that READ `path`'s content.

    ⛔ The roots are WALKED on disk, not read from `git ls-files`: `scripts/` is
    a SYMLINK to a checkout OUTSIDE this repository, so a listing returns the
    link itself (before #9475 it returned the tracked 120000 blob; now the path
    is gitignored and untracked, so a listing returns nothing at all) and the
    ~70 files behind it are never scanned — the same gap #6787 cycle 4 P3 found
    in the sibling mention census. Shell files use a line-level read-op pattern,
    never `ast.parse` (which returns False on every `.sh`).

    ⚠️ SCOPE, stated rather than overclaimed (cycle-2 review P2): `scripts/` is
    a symlink to an ABSOLUTE machine-local target materialised from
    `$AGENT_INFRA_PATH` (#9475), so on a fresh clone — the Ubuntu CI runner, a
    lane before its first commit — the path is simply ABSENT and this walk
    skips that root there; the root is reached only where the pointer has been
    materialised (`uv run python tools/link_agent_infra_scripts.py`). That is
    the SAME limitation the sibling `_readers_of` grep has (which now ANNOUNCES
    the absent root), and it is not closable from inside the repo. A future
    member whose ONLY reader lives under `scripts/` cannot rely on this ratchet
    on CI: grep the whole tree before adding one.
    """
    name = path.rsplit("/", 1)[-1]
    out: list[str] = []
    for root in _NON_CODE_DOC_ROOTS:
        base = REPO / root
        if not base.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(base, followlinks=True):
            dirnames[:] = [d for d in dirnames if d != "__pycache__"]
            for fn in filenames:
                if not fn.endswith((".py", ".sh", ".bash")):
                    continue
                f = Path(dirpath) / fn
                rel = str(f.relative_to(REPO))
                if rel in _SELF_NAMERS:
                    continue
                try:
                    source = f.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                if name not in source:
                    continue
                if f.suffix == ".py":
                    if _content_read_in_py(source, path):
                        out.append(rel)
                elif _content_read_in_shell(source, path):
                    out.append(rel)
    return sorted(out)


def test_non_code_doc_paths_exist():
    """Liveness ratchet (#6787 review cycle 2 P3): a member absent from the
    tree skips the matrix for nothing, so membership must name real files."""
    for name in sorted(cs.NON_CODE_DOC_PATHS):
        assert (REPO / name).exists(), (
            f"{name} is a NON_CODE_DOC_PATHS member but does not exist in the "
            "tree — a dead entry that skips the matrix for nothing")


def test_non_code_doc_paths_have_no_content_reader():
    """#8785: no member may be READ by a test/tool.

    Reprieving a file a guard reads is the silent-hole class (#1349/#4309),
    strictly worse than the provisioning cost the reprieve removes.
    """
    offenders = {name: _content_readers_of(name)
                 for name in sorted(cs.NON_CODE_DOC_PATHS)
                 if _content_readers_of(name)}
    assert not offenders, (
        "NON_CODE_DOC_PATHS member(s) are READ — dropping them to the tier-1 "
        f"smoke would skip those guards on the PR that edits the file: {offenders}")


def test_non_code_docs_select_tier1_not_full(monkeypatch):
    """#8785: a prose doc no guard reads selects the tier-1 smoke, not FULL."""
    members = sorted(cs.NON_CODE_DOC_PATHS)
    assert members, "NON_CODE_DOC_PATHS is empty — the reprieve is inert"
    for path in members:
        r = _sel([path])
        assert r["full"] is False, f"{path} must not select the full matrix: {r}"
        assert r["surfaces"] == [], path
        assert set(r["test_files"]) == _tier1(), path
        assert r["slow_run"] is False and r["carve_out_run"] is False, path

    # NON-VACUITY: against the pre-fix selector (no reprieve) the SAME paths are
    # full/ALL. Disabling the tuple must therefore RED this test — that is what
    # proves it exercises the fix rather than restating the docs-only default.
    monkeypatch.setattr(cs, "NON_CODE_DOC_PATHS", frozenset())
    for path in members:
        pre = _sel([path])
        assert pre["full"] is True and pre["test_files"] == "ALL", (
            f"non-vacuity: {path} must be FULL without the reprieve, got {pre}")


def test_unclassified_paths_still_fail_closed_after_the_doc_reprieve():
    """CONTROL for the non-vacuity above: the reprieve must NOT soften the
    unknown-path branch. A `.md` not in the census, a code-like extension, a
    build file and a new subsystem all still reserve the FULL matrix."""
    for name in ("NOTES.md", "some_new_thing.xyz", "mystery.conf",
                 "mystery-dir/x.py", "Makefile", "Dockerfile", "uv.lock",
                 "build.sh"):
        r = _sel([name])
        assert r["full"] is True, f"{name} must fail closed to full, got {r}"
        assert r["test_files"] == "ALL", name


def test_doc_reprieve_is_per_path_not_per_diff():
    """Mixed diffs: dropping the prose must not drop the code, and a
    docs+unknown diff must still escalate."""
    r = _sel(["CONTRIBUTING.md", "tortoise/api.py"])
    assert r["full"] is False
    assert "api" in r["surfaces"] and "core" in r["surfaces"]

    # The measured #8726 shape: two prose files, no Python -> tier-1 smoke.
    r2 = _sel(["CONTRIBUTING.md", ".github/pull_request_template.md"])
    assert r2["full"] is False and r2["surfaces"] == []
    assert r2["slow_run"] is False and r2["carve_out_run"] is False

    r3 = _sel(["CONTRIBUTING.md", "mystery-dir/thing.xyz"])
    assert r3["full"] is True


def test_new_engine_module_maps_to_core():
    # a new file under tortoise/ is engine code → core surface (conservative,
    # 108 files — not silent under-selection)
    r = _sel(["tortoise/brand_new_module.py"])
    assert r["full"] is False
    assert "core" in r["surfaces"]


def test_test_file_change_selects_owning_surface():
    r = _sel(["tests/test_decide.py"])
    assert "ep" in r["surfaces"]
    r2 = _sel(["tests/test_harness_mcp_config.py"])
    assert "onboarding" in r2["surfaces"]


def test_pi_hooks_change_selects_the_capture_guard():
    """#3575 P1-B: `tortoise/pi-hooks/` matches no SOURCE_PATTERNS entry, so a
    change to the extension selects `core` via the `tortoise/` fallback. The
    guard that pins the extension must be IN that selection — registered only
    under `onboarding`, a PR fixing the extension ran neither the Python guard
    nor the extension's `node --test` suite. `--integrity` cannot see this
    (the file is classified); only a selection assertion can."""
    r = _sel(["tortoise/pi-hooks/tortoise-capture.ts"])
    assert r["full"] is False
    assert "core" in r["surfaces"]
    assert "test_pi_capture_hooks.py" in r["test_files"]


def test_token_ts_change_selects_the_consent_bridge_guard():
    """#3524: the guard's `token.ts` half must be selected by a `token.ts` diff.

    `test_oauth_consent_bridge.py`'s task-3 assertion reads
    `website/apps/dashboard/functions/_shared/auth/token.ts`. That path matches
    the `onboarding` SOURCE_PATTERN `website/apps/dashboard/functions/`, NOT
    `api` — so an `api`-only registration (which covers the file's
    `tortoise/oauth.py` + `tortoise/hosted_api.py` subjects) left the token.ts
    guard unrun exactly when token.ts moved: a token.ts regression shipped green
    while the guard that exists to catch it stayed asleep. This asserts the
    SELECTION, not the registration, so it reds on the pre-fix manifest.
    """
    r = _sel(["website/apps/dashboard/functions/_shared/auth/token.ts"])
    assert r["full"] is False
    assert r["surfaces"] == ["onboarding"], (
        f"token.ts selects {r['surfaces']} — the consent bridge's token.ts guard "
        "is only reachable through the surface that path matches"
    )
    assert "test_oauth_consent_bridge.py" in r["test_files"], (
        "a token.ts-only diff does not run tests/test_oauth_consent_bridge.py, "
        "so its task-3 guard cannot fire on the change it pins (#3524)"
    )


def test_a_consent_token_only_diff_runs_the_bridge_guard():
    """#3524 review (P1): the verifier's own diff must select the guard that pins it.

    `tests/test_oauth_consent_bridge.py` is registered `api` + `onboarding` and
    NOT `core`; a named-surface match REPLACES the `core` fallback. So before
    `tortoise/consent_token.py` was a `SOURCE_PATTERNS["api"]` entry, a diff that
    touched the verifier and nothing else selected `core`, where that guard does
    not exist — a regression in the signature/`alg`/`bind`/`jti` logic (the
    security half of the bridge) red nowhere and merged green. Asserts the
    SELECTION, not the registration, so it reds on the pre-fix patterns.
    """
    r = _sel(["tortoise/consent_token.py"])
    assert r["full"] is False
    assert "api" in r["surfaces"], (
        f"a consent_token.py-only diff selects {r['surfaces']} — the bridge "
        "guard is registered on `api`, and `core` does not run it"
    )
    assert "test_oauth_consent_bridge.py" in r["test_files"], (
        "a consent_token.py-only diff does not run "
        "tests/test_oauth_consent_bridge.py, so its verifier guards cannot fire "
        "on the change they pin (#3524)"
    )


def test_the_consent_bridge_guard_runs_on_its_docs_records_too():
    """#3524: the guard's two `docs/` subjects must select it on a docs-only diff.

    `test_oauth_consent_bridge.py` also pins `docs/engineering/auth/consent-identity-bridge.md`
    (the spec) and `docs/ops/auth-email-flows.md` (the redirect-allowlist record).
    `docs/` is in NON_PYTHON_PREFIXES and matches (now) the `onboarding`
    SOURCE_PATTERNS entries added with this fix, so a docs-only edit to either
    record selects the surface that runs the guard. Before those entries a
    docs-only edit selected `surfaces=[]` and the guard never saw the record it
    guards — the same silent-drop class the token.ts half (#3524) fixes.
    """
    root = Path(__file__).resolve().parents[1]
    subjects = (
        "docs/engineering/auth/consent-identity-bridge.md",
        "docs/ops/auth-email-flows.md",
    )
    for changed in subjects:
        assert (root / changed).exists(), f"guarded docs record is gone: {changed}"
        r = _sel([changed])
        assert r["full"] is False
        assert "onboarding" in r["surfaces"], (
            f"{changed} selects {r['surfaces']} — the guard is not in tier1, so "
            "a docs-only edit to this record runs it nowhere"
        )
        assert "test_oauth_consent_bridge.py" in r["test_files"], (
            f"a docs-only edit to {changed} does not run "
            "tests/test_oauth_consent_bridge.py, so its record guard cannot fire "
            "on the change it pins (#3524)"
        )


def test_the_csp_plan_doc_guard_runs_on_its_plan_doc_too():
    """#8334 review (P2, Finding 1): the doc guard's own subject must select it.

    `test_csp_plan_doc_consistency.py` guards
    `docs/plans/2026-09-22-3525-csp-no-store.md` — the artifact a lane reads before
    touching the CSP. `docs/` is in NON_PYTHON_PREFIXES and is not itself a
    SOURCE_PATTERNS pattern, so without the concrete `docs/plans/...` entry a
    docs-only edit selects NO surface (`surfaces=[]`) and the guard never sees the
    doc it guards — while the `config/ci-surfaces.yml` registration comment claimed
    the coverage existed. Same silent-drop class as the #3524 docs records above.
    """
    root = Path(__file__).resolve().parents[1]
    changed = "docs/plans/2026-09-22-3525-csp-no-store.md"
    assert (root / changed).exists(), f"guarded plan doc is gone: {changed}"
    r = _sel([changed])
    assert r["full"] is False
    assert "onboarding" in r["surfaces"], (
        f"{changed} selects {r['surfaces']} — a docs-only edit to the plan doc runs "
        "the guard nowhere"
    )
    assert "test_csp_plan_doc_consistency.py" in r["test_files"], (
        f"a docs-only edit to {changed} does not run "
        "tests/test_csp_plan_doc_consistency.py, so its doc guard cannot fire (#8334)"
    )


def test_the_blog_authoring_doc_guard_runs_on_its_doc_too():
    """#8379 (finding 2): the authoring-doc guard's own subject must select it.

    `test_blog_authoring_doc.py` renders `docs/blog-authoring.md` with a real
    CommonMark parser (markdownlint cannot see an unterminated fence). `docs/` is
    in NON_PYTHON_PREFIXES and is not itself a SOURCE_PATTERNS pattern, so without
    the concrete `docs/blog-authoring.md` entry a docs-only edit selects NO surface
    and the guard never sees the page it guards — the same silent-drop class as the
    #3524 docs records and the #8334 plan doc above.
    """
    root = Path(__file__).resolve().parents[1]
    changed = "docs/blog-authoring.md"
    assert (root / changed).exists(), f"guarded docs record is gone: {changed}"
    r = _sel([changed])
    assert r["full"] is False
    assert "onboarding" in r["surfaces"], (
        f"{changed} selects {r['surfaces']} — the guard is not in tier1, so a "
        "docs-only edit to the authoring contract runs it nowhere"
    )
    assert "test_blog_authoring_doc.py" in r["test_files"], (
        f"a docs-only edit to {changed} does not run tests/test_blog_authoring_doc.py, "
        "so its render guard cannot fire on the change it pins (#8379)"
    )


def test_the_session_declaration_guard_runs_on_its_docs_subjects_too():
    """#3554 review (P2, Finding 2): the guard's `docs/` subjects must select it.

    `test_session_schema_3554.py` pins `docs/ONTOLOGY.md` §4.8's owner table
    against the machine declaration and reads the plan's `#3554 amendment`. Both
    are `docs/` paths: `docs/` is in NON_PYTHON_PREFIXES and neither is a
    concrete SOURCE_PATTERNS entry, so a docs-only edit selected NO surface
    (`surfaces=[]`) and fell back to tier-1 smoke — whose set does not contain
    this file. A §4.8 edit could then drop an owner and merge green against the
    declaration it claims to be pinned to. Same silent-drop class as the #3524
    consent-bridge records; the `core` registration alone cannot fix it, because
    a name in a surface's test_files only runs when that surface is ALREADY
    selected.

    #3554 review (P2): the THIRD subject — `docs/ONTOLOGY-CHANGELOG.md`, read by
    `test_section_4_8_changelog_entry_sits_under_its_own_version_block` — was
    omitted from this list and from `CORE_ALSO`, so a docs-only edit that moved
    the `§4.8 (added by #3554` bullet back under the wrong `Changelog vX.Y`
    header (the exact defect that test exists to prevent) selected no surface and
    ran a 45-file smoke set without the guard. The sibling changelog readers are
    covered only because they sit in tier1; this module is not.
    """
    root = Path(__file__).resolve().parents[1]
    for changed in ("docs/ONTOLOGY.md",
                    "docs/ONTOLOGY-CHANGELOG.md",
                    "docs/plans/2026-08-25-1714-memory-capture-onboarding.md"):
        assert (root / changed).exists(), f"guarded doc subject is gone: {changed}"
        r = _sel([changed])
        assert r["full"] is False
        assert "core" in r["surfaces"], (
            f"{changed} selects {r['surfaces']} — the guard is not in tier1, so "
            "a docs-only edit to this record runs it nowhere"
        )
        assert "test_session_schema_3554.py" in r["test_files"], (
            f"a docs-only edit to {changed} does not run "
            "tests/test_session_schema_3554.py, so its declaration mirror cannot "
            "fire on the change it pins (#3554 review)"
        )


def test_kind_vocabulary_guard_runs_on_a_docs_only_ontology_edit():
    """#2786: the §5 vocabulary guard IS selected by a docs-only ONTOLOGY edit.

    `tests/test_kind_vocabulary_consistency.py` reads `docs/ONTOLOGY.md` §5 as its
    canonical source, so a docs-only §5 edit must run it. A review of PR #9146
    raised this as a P1 (`ci-selection-gap`): "the file is not in tier1, so a
    docs-only ONTOLOGY edit never runs it". **Measured false** at `547411b27` with
    this selector — `select(['docs/ONTOLOGY.md'])` → `surfaces=['core']` with the
    guard in `test_files`.

    The premise was wrong: `docs/ONTOLOGY.md` is in `CORE_ALSO` (the #3554 entry,
    the #1349/#3332/#3616 silent-drop remedy), so the docs path survives
    `_keep_changed`, `core` is selected, and the docs-only tier-1 early return is
    never reached. `tier1` registration is therefore NOT the remedy here: it would
    add ~3 s to every PR for coverage the `core` leg already provides.

    The residual risk that WOULD be real — `docs/ONTOLOGY.md` dropping out of
    `CORE_ALSO`, which reverts every doc-anchored guard in this file's family to a
    tier-1-only run — is what this pin holds (and
    `test_the_session_declaration_guard_runs_on_its_docs_subjects_too` pins the
    same mechanism for the #3554 guard).
    """
    r = _sel(["docs/ONTOLOGY.md"])
    assert r["full"] is False
    assert r["surfaces"] == ["core"], (
        f"a docs-only docs/ONTOLOGY.md edit selects {r['surfaces']} — `core` is "
        "lost, so the §5 vocabulary guard would fall back to tier-1 smoke and "
        "never run on the doc it exists to pin (CORE_ALSO entry removed?)"
    )
    assert "test_kind_vocabulary_consistency.py" in r["test_files"], (
        "a docs-only docs/ONTOLOGY.md edit does not run "
        "tests/test_kind_vocabulary_consistency.py, so a §5 drop/addition/rename "
        "merges green (#2786, #4309 class)"
    )

    # Non-vacuous: the same selection loses the guard the moment its `core`
    # registration is dropped, so the assertion above is not satisfied by any file.
    stripped = copy.deepcopy(load_manifest())
    stripped["surfaces"]["core"] = [
        t for t in stripped["surfaces"]["core"]
        if t != "test_kind_vocabulary_consistency.py"
    ]
    assert "test_kind_vocabulary_consistency.py" not in select(
        ["docs/ONTOLOGY.md"], "pull_request", stripped)["test_files"]


def test_session_import_change_selects_the_window_guard():
    """#3575 P1-A: `tortoise/session_import/` maps to no named surface, so a
    parsers.py change selects `core` via the fallback. The window guard must be
    in that selection — registered only under `api`, it did not run for the
    change it guards."""
    r = _sel(["tortoise/session_import/parsers.py"])
    assert r["full"] is False
    assert "core" in r["surfaces"]
    assert "test_session_import_codex.py" in r["test_files"]


def test_two_surfaces_union():
    r = _sel(["tortoise/ranking.py", "tortoise/onboarding/SKILL.md"])
    assert r["full"] is False
    assert set(r["surfaces"]) == {"ep", "onboarding"}


def test_push_and_schedule_are_full():
    assert _sel([], "push")["full"] is True
    assert _sel([], "schedule")["full"] is True


def test_integrity_covers_all_test_files():
    # every tests/**/test_*.py (except tests/e2e/) must be classified — the
    # drift trap (scope v5 dec 2/5; rglob since #1349)
    assert integrity(load_manifest()) == []


def test_integrity_rglob_flags_unregistered_subdir(monkeypatch, tmp_path):
    # a subdir test file absent from the manifest is drift-flagged — the
    # non-recursive glob used to miss subdir files entirely (#1349)
    import tools.ci_selection as cs
    (tmp_path / "test_top.py").write_text("")
    (tmp_path / "bench").mkdir()
    (tmp_path / "bench" / "test_bench_x.py").write_text("")
    monkeypatch.setattr(cs, "TESTS_DIR", tmp_path)
    manifest = load_manifest()
    manifest["surfaces"]["core"].append("test_top.py")
    missing = cs.integrity(manifest)
    assert "bench/test_bench_x.py" in missing
    assert "test_top.py" not in missing


def test_integrity_rglob_registered_subdir_clean(monkeypatch, tmp_path):
    # registering the subdir file (relative-path key) clears the flag
    import tools.ci_selection as cs
    (tmp_path / "test_top.py").write_text("")
    (tmp_path / "bench").mkdir()
    (tmp_path / "bench" / "test_bench_x.py").write_text("")
    monkeypatch.setattr(cs, "TESTS_DIR", tmp_path)
    manifest = load_manifest()
    manifest["surfaces"]["core"].append("test_top.py")
    manifest["surfaces"]["eval"].append("bench/test_bench_x.py")
    assert cs.integrity(manifest) == []


def test_integrity_rglob_skips_e2e(monkeypatch, tmp_path):
    # tests/e2e/ (4 direct + 13 hosted) is deliberately NOT registered —
    # covered by welcome-e2e-monitor + legal-e2e + ENV_BROKEN_FILES
    import tools.ci_selection as cs
    (tmp_path / "e2e").mkdir()
    (tmp_path / "e2e" / "test_browser.py").write_text("")
    (tmp_path / "e2e" / "hosted").mkdir()
    (tmp_path / "e2e" / "hosted" / "test_01_signup.py").write_text("")
    monkeypatch.setattr(cs, "TESTS_DIR", tmp_path)
    assert cs.integrity(load_manifest()) == []


def test_classify_relative_path_and_basename_backcompat():
    m = load_manifest()
    # relative-path form (subdir files, #1349)
    assert classify_test_file("eval/retrieval/test_gate_1349.py", m) == "eval"
    assert classify_test_file("longmem_eval/test_vector_arm.py", m) == "eval"
    assert classify_test_file("bench/test_bench_core.py", m) == "eval"
    # basename backward-compat (legacy top-level keys)
    assert classify_test_file("test_decide.py", m) == "ep"
    assert classify_test_file("test_embedder_probe.py", m) == "eval"
    # unknown files classify to nothing
    assert classify_test_file("nope.py", m) is None
    assert classify_test_file("subdir/nope.py", m) is None


def test_tools_longmem_change_selects_eval_not_tier1():
    # tools/longmem_eval/ is carved out of NON_PYTHON_PREFIXES — a harness
    # change selects the eval surface instead of dropping to tier-1 smoke
    r = _sel(["tools/longmem_eval/run.py"])
    assert r["full"] is False
    assert r["surfaces"] == ["eval"]
    # #6137 moved `eval/retrieval/test_run.py` out of the fast pool into the
    # slow legs, so the witness that "eval was selected" had to be another eval
    # file that stayed.
    # #10194 moved BOTH of them into the non-gating lane, so the witness is now
    # an `eval`-surface file that is NOT under the eval/longmem subtrees —
    # `test_measure_ask_distilled.py` (registered on `eval`, at the top of
    # `tests/`). The property under test is unchanged: an eval-harness diff must
    # select the eval surface's tests and must NOT collapse to tier-1 smoke.
    # What changed is that the subtree's own files no longer ride along, which
    # is asserted here rather than merely allowed.
    assert "test_measure_ask_distilled.py" in r["test_files"], r["test_files"]
    assert "eval/retrieval/test_1348.py" not in r["test_files"], (
        "eval/ files are the non-gating lane (#10194) — they must not reach a "
        "tier-2 fast shard")
    assert "eval/retrieval/test_run.py" not in r["test_files"]
    assert "eval/retrieval/test_run.py" not in r["slow_selected"], (
        "#10194: a lane file must not ride the tier-2 slow leg either")
    assert set(r["test_files"]) != _tier1()


def test_unrelated_tools_change_still_tier1():
    # tools/kappa.py is NOT in TOOL_CARVEOUTS — it keeps the old behavior
    # (filtered as non-python-relevant → tier-1 smoke; never eval/full)
    r = _sel(["tools/kappa.py"])
    assert r["full"] is False
    assert r["surfaces"] == []
    assert set(r["test_files"]) == _tier1()


def test_activation_cohort_change_does_not_drop_to_tier1():
    # #B7 (#3674, restated by #3676): tools/activation_cohort.py's transport
    # guards (https-only, no-redirect, credential hygiene) are pinned by
    # tests/test_activation_scorecard.py. Without the TOOL_CARVEOUTS entry the
    # flat "tools/" prefix swallows it -> tier-1 smoke only, and the suite that
    # pins those controls never runs. (The cohort FOLD moved into
    # tortoise/activation_scorecard.py in #3676 — the `core` surface covers
    # that file, so the carve-out's reason narrowed rather than vanished.)
    r = _sel(["tools/activation_cohort.py"])
    assert r["surfaces"], r
    assert set(r["test_files"]) != _tier1(), r
    # Today it lands in the fail-closed unknown-path branch (FULL matrix — the
    # heaviest but safest gate for a file a reported metric depends on). If
    # that ever becomes a mapped surface, the owning suite must still run.
    if not r["full"]:
        assert "test_activation_scorecard.py" in r["test_files"], r


def test_ask_spotcheck_tools_change_selects_sdk_not_tier1():
    # #2071: tools/ask_spotcheck*.py are carved out of NON_PYTHON_PREFIXES
    # and mapped to the sdk surface — a spot-check-only change selects the
    # ask-lane tests (test_ask_spotcheck_judge.py) instead of tier-1 smoke
    for changed in ("tools/ask_spotcheck.py",
                    "tools/ask_spotcheck_consistency.py",
                    "tools/ask_spotcheck_probe.py"):
        r = _sel([changed])
        assert r["full"] is False, changed
        assert "sdk" in r["surfaces"], changed
        assert "test_ask_spotcheck_judge.py" in r["test_files"], changed
        assert set(r["test_files"]) != _tier1()
    # a test-file change selects its owning surface too
    r = _sel(["tests/test_ask_spotcheck_judge.py"])
    assert "sdk" in r["surfaces"]


def test_ask_recall_bench_change_selects_sdk_not_tier1():
    # #3910: tools/ask_recall_bench.py owns the `_retrieve_pipeline` mirror in
    # tests/test_ask_retrieval_levers.py. Before its SOURCE_PATTERNS entry the
    # flat "tools/" prefix swallowed the path, so `changed` came back empty and
    # select() took the docs-only return — surfaces=[], tier-1 smoke only — and
    # the guard test for that exact file never ran on the PR that changed it
    # (the #1349/#3332 shape the ratchet exists for).
    r = _sel(["tools/ask_recall_bench.py"])
    assert r["full"] is False
    assert "sdk" in r["surfaces"], r
    assert "test_ask_retrieval_levers.py" in r["test_files"], r
    assert set(r["test_files"]) != _tier1()


def test_gen_ask_transcripts_change_selects_sdk_not_tier1():
    # #3914: tools/gen_ask_transcripts.py owns the capture-shaped seeder the
    # committed transcript goldens are generated from, and its shape is pinned
    # by tests/test_ask_seed_shape.py. Before its SOURCE_PATTERNS entry the
    # flat "tools/" prefix swallowed the path, so a seeder-only change came
    # back with surfaces=[] and select() took the docs-only return — tier-1
    # smoke only — leaving BOTH guards unrun on the PR that changed the
    # seeder (the #1349/#3332/#3910 class the ratchet exists for).
    r = _sel(["tools/gen_ask_transcripts.py"])
    assert r["full"] is False, r
    assert "sdk" in r["surfaces"], r
    assert "test_ask_seed_shape.py" in r["test_files"], r
    assert "test_ask_regression_llm.py" in r["test_files"], r
    assert set(r["test_files"]) != _tier1()


def test_tmpdir_sweep_tool_change_selects_core_not_tier1():
    # #4069: tools/tmpdir_sweep.py owns tests/test_tmpdir_sweep.py and
    # tests/test_tmpdir_hygiene.py. The mechanism is the CORE_ALSO entry:
    # `_selection_relevant()` consults it, so the `tools/` path survives the
    # flat NON_PYTHON_PREFIXES filter, and the match loop then adds `core` and
    # marks the path found — so a tool-only change selects `core` instead of
    # tier-1 smoke or the unknown-path full matrix. Mutation check: removing
    # the CORE_ALSO entry filters the path out (docs-only early return → empty
    # surfaces, tier-1 smoke), which fails asserts 2–5 below.
    r = _sel(["tools/tmpdir_sweep.py"])
    assert r["full"] is False, r
    assert "core" in r["surfaces"], r
    assert "test_tmpdir_sweep.py" in r["test_files"], r
    assert "test_tmpdir_hygiene.py" in r["test_files"], r
    assert set(r["test_files"]) != _tier1()


def test_cli_extraction_engine_change_selects_core_and_carve_out_leg():
    # #9929 review #2: the CORE_ALSO entry for
    # integrations/cli-extraction/extract_turns.py exists only to stop the
    # #6138 silent drop — `integrations/` is a flat NON_PYTHON_PREFIXES entry,
    # so without it an extractor-only diff comes back changed=[], select()
    # takes the docs-only early return and NOT ONE of the lane's guards runs on
    # the very diff that can break the extractor.
    #
    # The guard runs in the CARVE-OUT leg, not `test_files`:
    # tests/test_cli_extraction.py is a carve_out member, so it is excluded
    # from the docker legs by construction and `carve_out_run` (a matched
    # surface owns a carve-out file) is what actually schedules it. Asserting
    # on `test_files` here would be the wrong half of the contract.
    #
    # Mutation check (verified by dropping the CORE_ALSO entry in a REPL):
    # select() then returns surfaces=[], full=False and carve_out_run=False on
    # the 64-file tier-1 smoke set, so asserts 2, 3 and 5 below go RED. Assert 1
    # (`full is False`) survives and assert 4 is manifest-driven
    # (`carve_out_files()` reads config/ci-surfaces.yml, not this selection).
    r = _sel(["integrations/cli-extraction/extract_turns.py"])
    assert r["full"] is False, r
    assert r["surfaces"] == ["core"], r
    assert r["carve_out_run"] is True, r
    assert "test_cli_extraction.py" in cs.carve_out_files(load_manifest()), r
    assert set(r["test_files"]) != _tier1(), r


def test_docs_lint_baseline_tool_change_selects_core_not_tier1():
    # #7435 review P1: tools/docs_lint_baseline.py owns
    # tests/test_docs_lint_baseline.py (`core`), whose 58 cases pin the differ's
    # fail-CLOSED behaviour — the unreadable report, the missing `Summary:`, the
    # count mismatch, the multiset semantics, the generated-file fix target.
    #
    # The `core` registration alone is INERT for a differ-only change: `tools/`
    # is a flat NON_PYTHON_PREFIXES entry and no SOURCE_PATTERNS row matches the
    # tool, so `select()` returned NO surface and fell back to the 31-file tier-1
    # smoke set, which does not contain the guard. The PR that can make the
    # differ fail OPEN would therefore never run the tests that catch it. Same
    # #3261/#3332/#3616 silent-drop class as tools/queue_resweep.py above.
    #
    # CORE_ALSO (not TOOL_CARVEOUTS) is the deliberate choice: the guard is
    # hermetic and sub-second, so selecting `core` runs it at the lowest CI cost.
    # Mutation check: removing the CORE_ALSO entry fails the second assert.
    r = _sel(["tools/docs_lint_baseline.py"])
    assert "core" in r["surfaces"], r
    assert "test_docs_lint_baseline.py" in r["test_files"], r
    assert r["full"] is False, r


def test_inbound_relay_sweep_tool_change_selects_core_not_tier1():
    """A change to the relay sweep must run the rails it pins (#9943).

    `tests/test_inbound_relay_sweep.py` is registered on the `core` surface and
    owns the sweep's invariants — relay-before-close, the label applied LAST so a
    failure leaves the item retryable, an unreadable page taking no action, and
    the queue-batch exclusion — which are exactly what a PR would edit to weaken
    the control. That registration is INERT for a tool-only diff: `tools/` is a
    flat NON_PYTHON_PREFIXES entry and no SOURCE_PATTERNS row matches the tool, so
    `select()` returned NO surface, took the docs-only early return and fell back
    to the tier-1 smoke set, which does not contain the file. The 15-minute cron
    then runs the weakened tool with its rails unpinned.

    Mutation check: removing the CORE_ALSO entry fails the FIRST assert
    (`"core" in r["surfaces"]`); the docs-only early return then yields
    `full: False` with no pin, so the later asserts pass unexercised.
    """
    r = _sel(["tools/inbound_relay_sweep.py"])
    assert "core" in r["surfaces"], r
    assert "test_inbound_relay_sweep.py" in r["test_files"], r
    assert r["full"] is False, r


def test_worktree_report_tool_change_selects_core_not_tier1():
    """A change to the lane report must run the rules it pins (#10220).

    `tests/test_worktree_report.py` is registered on the `core` surface and owns the
    report's anti-fabrication guarantees — the counts are derived from a log that is
    re-hashed and re-parsed, a red suite exits 3 and never 0, an unfinished suite is
    refused. That registration is INERT for a tool-only diff: `tools/` is a flat
    NON_PYTHON_PREFIXES entry and no SOURCE_PATTERNS row matches
    `tools/worktree_report.py`, so `select()` returns NO surface, takes the
    docs-only early return and falls back to the tier-1 smoke set, which does not
    contain the file. Weakening a rule would then be the one PR that never ran the
    cases pinning it — and that tool is what the merge path reads as a lane's test
    evidence (`agent-infra#2977`), so the drop would be invisible where it matters most.

    Mutation check: removing the CORE_ALSO entry fails the FIRST assert
    (`"core" in r["surfaces"]`); the docs-only early return then yields
    `full: False` with no pin, so the later asserts pass unexercised.
    """
    r = _sel(["tools/worktree_report.py"])
    assert "core" in r["surfaces"], r
    assert "test_worktree_report.py" in r["test_files"], r
    assert r["full"] is False, r


def test_ax_write_eval_tool_change_selects_core_not_tier1():
    """A change to the eval instrument must run the rules it pins (#9695 review).

    `tests/test_ax_write_eval.py` is registered on the `core` surface and owns
    the instrument's scoring semantics — the P0/P1 rules, the two-homes bar, the
    one-act-one-finding guard — which is what #9675 Step 3 reads its per-input
    delta against. That registration is INERT for a tool-only diff: `tools/` is
    a flat NON_PYTHON_PREFIXES entry and no SOURCE_PATTERNS row matches
    `tools/ax_write_eval.py`, so `select()` returned NO surface and fell back to
    the tier-1 smoke set, which does not contain the file. Weakening a rule would
    therefore be the one PR that never ran the tests pinning it — the same
    #1349/#3332/#3616 silent-drop class as the entries beside it.

    Mutation check: removing the CORE_ALSO entry fails the FIRST assert
    (`"core" in r["surfaces"]`); the docs-only early return then returns
    `full: False` with no pin, so the later asserts pass unexercised.
    """
    r = _sel(["tools/ax_write_eval.py"])
    assert "core" in r["surfaces"], r
    assert "test_ax_write_eval.py" in r["test_files"], r
    assert r["full"] is False, r


def test_the_step1_eval_evidence_doc_selects_core_not_tier1():
    """The evidence doc must not be editable without the pin that re-derives it.

    #9695 review P1 — the OTHER half of the entry above, and the one the doc
    itself exposes: `docs/ax/how-to-use-tortoise-step1-eval.md` publishes every
    measured number of the Step-1 eval — the aggregate headline, all ten
    per-input P0/P1 rows, the class census and every `kind` ×N multiplier — and
    the only file that re-derives them from the committed `runs/*/score.json`
    records is `tests/test_ax_write_eval.py`, registered on `core` only.

    MEASURED before the CORE_ALSO entry (`select()` in-process):
    `select(["docs/ax/how-to-use-tortoise-step1-eval.md"])` → `surfaces=[]`,
    `full=False`, pin ABSENT. `docs/` is a flat NON_PYTHON_PREFIXES
    entry and `_is_non_code_doc` recognises four exact paths, so the changed
    list came back empty and `select()` took the docs-only early return. A
    one-file PR could therefore re-cut the headline to any number and merge with
    the guard that re-measures it never having run — the #1349/#3332/#3616
    silent-drop class, reached through the early return rather than through
    `_selection_relevant`.

    Mutation check: removing the CORE_ALSO entry fails the FIRST assert
    (`surfaces == ["core"]`); dropping the pin from `core` fails the SECOND
    (`test_ax_write_eval.py` in `test_files`). The non-vacuity clause below
    exists for that second one: it re-selects from a manifest copy with the pin
    stripped from `core`, which proves the second assert turns on the `core`
    registration alone (the pin is the ONLY difference; the file counts move
    with every manifest edit, so they are not restated here).
    """
    r = _sel(["docs/ax/how-to-use-tortoise-step1-eval.md"])
    assert r["surfaces"] == ["core"], r
    assert "test_ax_write_eval.py" in r["test_files"], r
    assert r["full"] is False, r

    # Non-vacuous: the pin's presence turns on its `core` registration alone —
    # strip that entry and the same selection loses the pin and NOTHING else.
    # This makes the assert above a claim about `core` being the pin's ONLY
    # home: a `tier1` dual-registration would keep it selected and red this.
    stripped = copy.deepcopy(load_manifest())
    stripped["surfaces"]["core"] = [
        t for t in stripped["surfaces"]["core"] if t != "test_ax_write_eval.py"
    ]
    assert "test_ax_write_eval.py" not in select(
        ["docs/ax/how-to-use-tortoise-step1-eval.md"], "pull_request", stripped
    )["test_files"]


def test_extractor_tooling_change_selects_core_and_ep_not_tier1():
    # #2786 review P2: `tools/probe_extractor.py` (the classification prompt put
    # in front of the model) and `tools/calibration_harness.py` (the harness whose
    # `VOCAB` the `minted` metric reads) are the two TOOLING copies of the core
    # object-kind vocabulary. #2786 makes both DERIVE from
    # `pack_registry.CANONICAL_OBJECT_KINDS`, and the guards that pin them live in
    # TWO surfaces: `tests/test_kind_vocabulary_consistency.py` is `core` (the
    # canonical-derivation legs), and `tests/test_calibration_tools.py` is `ep`
    # (it imports BOTH tools at module level).
    #
    # `tools/` is a flat NON_PYTHON_PREFIXES entry and neither file had a
    # SOURCE_PATTERNS row, so a tooling-only diff selected NO surface and fell
    # back to the 52-file tier-1 smoke set, which contains NEITHER guard.
    # MEASURED on the PR head: `select(["tools/probe_extractor.py",
    # "tools/calibration_harness.py"])` -> surfaces=[], full=False, 52 files,
    # both guards absent. So the exact drift the guard exists to prevent —
    # someone retyping a stale `core:<kind>` into a tool — was green on the very
    # PR that introduces it. Same #1349/#3332/#3616 silent-drop class as
    # tools/docs_lint_baseline.py above.
    #
    # Both halves are asserted because the fix has both: the CORE_ALSO entries
    # add `core` (the vocabulary guard), and the `ep` SOURCE_PATTERNS rows add
    # `ep` (the harness/probe behaviour guard) — and a SOURCE_PATTERNS match
    # REPLACES the `core` fallback, which is exactly why the two must be paired.
    #
    # Mutation check: removing a CORE_ALSO entry fails the `core` assert
    # (`surfaces` becomes `["ep"]`); removing an `ep` SOURCE_PATTERNS entry fails
    # the `ep` assert. Neither guard is in tier-1, so the pre-fix selection ran
    # neither — that is what makes these asserts non-vacuous.
    for path in ("tools/probe_extractor.py", "tools/calibration_harness.py"):
        r = _sel([path])
        assert r["full"] is False, (path, r)
        assert "core" in r["surfaces"], (path, r["surfaces"])
        assert "test_kind_vocabulary_consistency.py" in r["test_files"], (
            path, "the vocabulary guard is not selected by a change to the tool it pins")
        assert "ep" in r["surfaces"], (path, r["surfaces"])
        assert "test_calibration_tools.py" in r["test_files"], (
            path, "the harness/probe behaviour guard is not selected by a change to it")


def test_e1_e2_evidence_bundle_change_selects_core_not_the_docs_only_return():
    # #9076: the E1+E2 evidence bundle (`docs/evidence/7949-capstone/` — the probe,
    # the corpus JSON + builder, the evidence log) and its structural checker
    # (`tools/e1_e2_census_check.py`) own `tests/test_e1_e2_measurement.py`,
    # registered `core`. MEASURED before the CORE_ALSO entries: all three paths
    # returned `surfaces=[]`, `full=False`, 50 files — `docs/` and `tools/` are
    # flat NON_PYTHON_PREFIXES entries, so the changed list came back empty and
    # select() took the docs-only early return (tier-1 smoke). The pin ran there
    # only because this PR dual-registers it in `tier1`; `tier1` is a CURATED
    # list, so the guard's owning surface was nominal and one tier1 edit from
    # dropping it silently. These asserts are on the `core` arm, which is what the
    # entries create: after the change, `surfaces=['core']` and the pin is present.
    # Mutation check: removing either CORE_ALSO entry restores `surfaces=[]` and
    # fails the FIRST assert (`"core" in r["surfaces"]`), which is the one that
    # names the owning surface. The second assert (`full is False`) still PASSES on
    # the docs-only early return, which returns `full: False` — the mutation is
    # caught by the surface assert, not by that one (#9110 review P3).
    for path in ("docs/evidence/7949-capstone/probes/e1_e2_probe.py",
                 "docs/evidence/7949-capstone/corpus/e1e2_corpus.json",
                 "docs/evidence/7949-capstone/e1-e2.log",
                 "tools/e1_e2_census_check.py"):
        r = _sel([path])
        assert "core" in r["surfaces"], (path, r["surfaces"])
        assert r["full"] is False, (path, r)
        assert "test_e1_e2_measurement.py" in r["test_files"], (path, r)


def test_quadrature_change_keeps_the_core_pool_and_runs_the_formula_guard():
    # #9607 review P2: the quadrature fix has TWO halves and only ONE of them was
    # pinned. `SOURCE_PATTERNS["onboarding"]` admits `tortoise/quadrature.py`, so
    # the /docs formula guard (`test_website_docs_consistency.py`) runs; the
    # `CORE_ALSO` entry keeps the `core` pool that a named-surface match REPLACES
    # (#2938). Dropping the `CORE_ALSO` half is SILENT: the guard still runs, so a
    # reachability assert alone stays green while the core pool disappears
    # (measured at `89f780805`: 682 files with the entry, 125 without — the
    # `onboarding` set alone). No generic ratchet covers it — nothing else iterates
    # `CORE_ALSO`, and MOST other entries carry their own explicit arm like this
    # (not all: `tortoise/projection/edges.py` is unpinned and drops 681 -> 147
    # silently — same class as #9621, filed as #9646).
    #
    # Each assert names a file registered ONLY in the surface its half adds
    # (`load_manifest()`: `test_website_docs_consistency.py` is `onboarding`,
    # `test_record_schema_placement.py` is `core`), so a lost half cannot be
    # masked by the other one.
    #
    # The descriptive `test_files` assert comes BEFORE the bare `surfaces` assert
    # in both pairs. That ordering WAS load-bearing and is kept so the more
    # descriptive message fires first: a failing assert short-circuits the rest of
    # the test, so with the surface check leading, the only thing a developer saw
    # was a bare `['core']` with no explanation. Both surface asserts now carry
    # their own message too, so a lost half is named whichever assert fires.
    #
    # Mutation check, both measured: removing "tortoise/quadrature.py" from
    # `CORE_ALSO` fails the `test_record_schema_placement.py` assert (682 files ->
    # 125, the `onboarding` set alone); removing it from
    # `SOURCE_PATTERNS["onboarding"]` fails the `test_website_docs_consistency.py`
    # assert (682 -> 633, the `core` pool alone) — counts are dated anchors.
    #
    # The coverage asserts tolerate the FULL matrix: `test_files` is then the STRING
    # "ALL", so a bare `in` test reads False. Promoting `tortoise/quadrature.py` to
    # `SHARED_MODULES` is a strictly WIDER selection, and reding this pin for it
    # would hand the next developer a red gate for better coverage (#9623 review
    # P3). `full` is therefore treated as "runs everything", mirroring
    # `tests/test_website_docs_consistency.py::_guard_reachable_for`'s treatment of
    # the sentinel; the `surfaces` asserts hold under `full` too, which returns
    # every surface.
    r = _sel(["tortoise/quadrature.py"])

    def runs(name: str) -> bool:
        return r["full"] is True or name in r["test_files"]

    assert runs("test_website_docs_consistency.py"), (
        "the /docs formula guard is not selected by a change to the module it "
        "derives its expected factors from (SOURCE_PATTERNS entry missing?)", r)
    assert "onboarding" in r["surfaces"], (
        "the guard ran from some other surface — a quadrature-only diff must "
        "select `onboarding`, which is where the guard is registered",
        r["surfaces"])
    assert runs("test_record_schema_placement.py"), (
        "the core-registered pool is missing from a quadrature-only selection "
        "(CORE_ALSO entry removed?)", r)
    assert "core" in r["surfaces"], (
        "a quadrature-only diff dropped the core pool (CORE_ALSO entry removed?)",
        r["surfaces"])


def test_commit_schema_change_keeps_the_core_pool_and_runs_the_docs_guard():
    # #9607 review P2 (round 5): the SIBLING of the quadrature arm one test up, and
    # the reason an enumeration claiming to be exhaustive was wrong. The /docs
    # guard imports `Point` / `point_content_id` from `tortoise/commit_schema.py`
    # (`tests/test_website_docs_consistency.py:56`) and derives an asserted
    # expectation from it — `test_commit_schema_requires_a_source_on_every_extracted_
    # claim` is the evidence under the FAQ's PUBLISHED traceability sentence — but
    # a commit_schema-only diff selected `surfaces=['core']`, where that guard is
    # not registered. So relaxing `Point.source_ref` reds the contract tests, gets
    # its author to edit them, and never runs the pin on the page that publishes
    # the claim.
    #
    # Same two halves as quadrature, and for the same reason (#2938): the
    # `SOURCE_PATTERNS["onboarding"]` entry makes the guard run, and the
    # `CORE_ALSO` entry keeps the `core` pool the match would otherwise REPLACE.
    # Each half is named by a file registered ONLY in the surface it adds
    # (`test_website_docs_consistency.py` is `onboarding`-only,
    # `test_record_schema_placement.py` is `core`-only), so neither can mask the
    # other.
    #
    # Mutation check, both measured: removing "tortoise/commit_schema.py" from
    # `CORE_ALSO` fails the `test_record_schema_placement.py` assert (the selection
    # collapses to the `onboarding` set alone); removing it from
    # `SOURCE_PATTERNS["onboarding"]` fails the `test_website_docs_consistency.py`
    # assert (the selection is the `core` pool alone).
    r = _sel(["tortoise/commit_schema.py"])
    files = r["test_files"]
    docs_guard_runs = r["full"] is True or "test_website_docs_consistency.py" in files
    core_pool_runs = r["full"] is True or "test_record_schema_placement.py" in files
    assert docs_guard_runs, (
        "the /docs guard is not selected by a change to `tortoise/commit_schema.py`, "
        "whose `Point` contract it asserts the FAQ sentence against "
        "(SOURCE_PATTERNS entry missing?)", r)
    assert "onboarding" in r["surfaces"], (
        "a commit_schema-only diff must select `onboarding`, where the guard is "
        "registered", r["surfaces"])
    assert core_pool_runs, (
        "the core-registered pool is missing from a commit_schema-only selection "
        "(CORE_ALSO entry removed?)", r)
    assert "core" in r["surfaces"], (
        "a commit_schema-only diff dropped the core pool (CORE_ALSO entry removed?)",
        r["surfaces"])


def test_supabase_control_change_selects_the_org_purge_rpc_guard():
    # #7968 / PR #8179 review P1: `purge_org_control_plane` lives in
    # `tortoise/supabase_control.py`, which IS a SOURCE_PATTERNS["api"] entry —
    # so a supabase_control.py-only change matches `api`, REPLACES the `core`
    # fallback in select(), and the `core`-only registration is never reached.
    # Before the guard was dual-registered under `api`, this diff selected
    # ['api'] and never ran test_7968_org_purge_rpc.py (the
    # #2938/#3154/#4367 silent-drop class). Mutation check: deleting the `api`
    # arm fails the second assert.
    r = _sel(["tortoise/supabase_control.py"])
    assert r["full"] is False, r
    assert "test_7968_org_purge_rpc.py" in r["test_files"], r


def test_socket_bound_2969_guard_runs_on_every_spread_site():
    # PR #8856 review P2: `tests/test_socket_bound_not_multiplied_2969.py`
    # guards the `retry=` key that `_bounded_server_socket_kwargs()` returns and
    # that FOUR other modules spread. Its registration was `core`-only — and
    # `core` is added only when nothing else matched — but `tortoise/__main__.py`
    # (the doctor probe) and `tortoise/acl_graph_users.py` (the ACL admin client)
    # are BOTH exact SOURCE_PATTERNS["api"] entries. A diff touching either one
    # alone therefore selected `api`, REPLACED the `core` fallback, and never
    # collected this file: the one-attempt/wall-clock measurement simply did not
    # run on a change to a site it pins (the #2938/#3154/#4367 silent-drop class
    # `#3261` removed the false claim about). Registration is DUAL for that
    # reason; `session_indexer.py` / `backup.py` match no named pattern, so they
    # really do fall through to `core` and rely on its own entry.
    # Mutation check: deleting the `api` arm fails the first loop; deleting the
    # `core` arm fails the second.
    for api_source in ("tortoise/__main__.py", "tortoise/acl_graph_users.py"):
        r = _sel([api_source])
        assert r["full"] is False, (api_source, r)
        assert r["surfaces"] == ["api"], (api_source, r)
        assert "test_socket_bound_not_multiplied_2969.py" in r["test_files"], (
            api_source, r)
    for core_source in ("tortoise/session_indexer.py", "tortoise/backup.py"):
        r = _sel([core_source])
        assert r["full"] is False, (core_source, r)
        assert r["surfaces"] == ["core"], (core_source, r)
        assert "test_socket_bound_not_multiplied_2969.py" in r["test_files"], (
            core_source, r)


def test_7357_note_guard_runs_on_every_surface_its_subjects_select():
    # PR #9393 review (three cycles, P2): `tests/test_7357_transport_legibility.py`
    # pins the instance-outage NOTE on the two in-package raw clients
    # (`hosted_backup._restore_copy_client`, `acl_graph_users._admin_client`) and
    # the classifier arms behind it (`tortoise/retry.py`). Its registration was
    # `core`-only — but `tortoise/acl_graph_users.py` is an exact
    # SOURCE_PATTERNS["api"] entry and is deliberately NOT in `CORE_ALSO`, so an
    # `acl_graph_users.py`-only diff selected `api`, REPLACED the `core` fallback,
    # and never ran the only test that pins what the seam SAYS on that channel —
    # the #1349/#2938/#9190 silent-drop class. The anti-REVERT half
    # (`test_bounded_raw_clients_3065.py`) is an AST inventory: it pins THAT the
    # client goes through the seam, not WHAT the seam emits.
    # Mutation check: deleting the `api` arm fails the first loop; deleting the
    # `core` row fails the second.
    for api_source in ("tortoise/acl_graph_users.py",):
        r = _sel([api_source])
        assert r["full"] is False, (api_source, r)
        assert r["surfaces"] == ["api"], (api_source, r)
        assert "test_7357_transport_legibility.py" in r["test_files"], (
            api_source, r)
    # `hosted_backup.py` is a `CORE_ALSO` entry, so it selects BOTH; the two
    # modules that match no named pattern fall through to the `core` fallback.
    for name, expected in (
            ("tortoise/hosted_backup.py", ["api", "core"]),
            ("tortoise/retry.py", ["core"]),
            ("tortoise/cypher_guard.py", ["core"])):
        r = _sel([name])
        assert r["full"] is False, (name, r)
        assert r["surfaces"] == expected, (name, r)
        assert "test_7357_transport_legibility.py" in r["test_files"], (name, r)


def test_record_schema_pin_runs_on_migration_or_doc_change():
    # #7968 / PR #8179 review P2: `tests/test_record_schema_placement.py` pins
    # the record migrations against the placement table in
    # `docs/epics/2026-10-10-7879-postgres-record/02-identity-and-placement.md`.
    # Both paths are under NON_PYTHON_PREFIXES (`supabase/`, `docs/`) and match
    # no SOURCE_PATTERNS row, so before the CORE_ALSO entries a change to either
    # side ALONE selected NO surface: the pin's own claim ("editing either side
    # alone turns the test red") was untestable in CI, and the edit landed with
    # the pin never running. Mutation check: removing ANY CORE_ALSO entry for a
    # path in this tuple fails its arm below (surfaces == []).
    #
    # #9638 review P1: the doc arm was DELETED in the same PR that inserted the
    # #9555/R7 migration entry over the doc's `CORE_ALSO` slot — so the comment
    # above kept asserting a doc arm that no longer existed, and a
    # placement-doc-only change (the edit that desynchronises the pin from the
    # table) ran no guard at all. The tuple is a set of INDEPENDENTLY pinned
    # paths: a new entry is added beside the existing ones, never on top of one.
    for changed in (
        "supabase/migrations/20261012000003_record_epistemic.sql",
        # #8263: the FORWARD record migration sorts outside the `2026101200000`
        # CORE_ALSO prefix, so it carries its own entry. Both readers must run on
        # a change to this file alone — `test_record_projection.py` reads its
        # ALTER, `test_record_schema_placement.py` pins the record schema.
        "supabase/migrations/20261014000001_record_subject_props_no_derived.sql",
        # #2782: the FORWARD migration that ADDS the five money COLUMNS to
        # `public.point`. Both readers must run on a change to this file alone —
        # `test_record_projection.py` reads its ALTER for the column set,
        # `test_record_schema_placement.py` pins the record schema against the
        # placement table.
        "supabase/migrations/20261018000001_record_point_money_value_2782.sql",
        # #9264: the FORWARD migration that NARROWS
        # `public.session.capture_redactions` from `jsonb` to `integer`. Same gap
        # as the two entries above: it sorts outside the `2026101200000`
        # CORE_ALSO prefix, so without its own entry a change to this file alone
        # selects NO surface and neither reader runs on it.
        "supabase/migrations/20261022000001_record_session_capture_redactions_integer.sql",
        # #9555/R7: the FORWARD migration that CREATES
        # `public.session_narrative`. Both readers must run on a change to this
        # file alone — `test_record_projection.py` reads its CREATE TABLE for the
        # column set, `test_record_schema_placement.py` pins the record schema
        # against the placement table — plus the narrative suite itself.
        "supabase/migrations/20261023000001_record_session_narrative.sql",
        # #9555/R7 (PR #9862 review round 4): the CARRIER migration — the
        # append-only `COMMENT ON` correction that states the schema's narrative
        # claim, and the file `test_9555_session_narrative.py` READS to pin it.
        # Measured on the pre-fix head: `_sel([<this path>])` -> surfaces=[],
        # full=False, so editing the very file the pin parses ran no guard.
        "supabase/migrations/20261024000001_record_session_narrative_carrier.sql",
        # #10017 R2: the FORWARD migration that re-declares
        # `chk_point_props_no_derived` to refuse `cache_tier` — and the one file
        # that can silently SHRINK `point`'s refusal set, because the SQL CHECK
        # REPLACES while `refused_props_names()` UNIONS (so the parser keeps
        # reporting every name refused after the database stopped refusing it).
        "supabase/migrations/20261029000001_record_point_cache_tier.sql",
        # The DOC side of the #7968 pair, restated after #9638 review P1 deleted
        # it: `test_record_schema_placement.py` reads this file's table, so a
        # docs-only edit must select `core` too. Measured on the pre-fix head:
        # `_sel([<this path>])` -> surfaces=[], full=False, both readers ABSENT.
        "docs/epics/2026-10-10-7879-postgres-record/02-identity-and-placement.md",
        # #9638 review P3: the SECOND document the same pin reads, and the one
        # the P3 finding was about — `08-hosted-prerequisites.md` §A1 states the
        # record's table census ("EXISTS, N tables + M views") and an exhaustive
        # row set, and it is the epic's own "what EXISTS, without re-deriving
        # it" ledger. Measured on the pre-fix head: `_sel([<this path>])` ->
        # surfaces=[], full=False, both readers ABSENT — so the §A1 edit that
        # left the census at nine tables ran no guard at all. Both halves of the
        # pin (the heading and the row set) live in this file, so it must select
        # `core` on its own.
        "docs/epics/2026-10-10-7879-postgres-record/08-hosted-prerequisites.md",
        # #9638 review P1 (second round): the THIRD document a record pin reads,
        # and the one that made this PR's own newest commit (`e92a78099`, "the
        # routing index's census, and the pin that keeps it true") unpinnable.
        # `test_the_routing_index_census_matches_the_record_migrations` reads
        # `docs/00_index.md`'s #7879 row for both its census ("N record tables +
        # M procedural views") and its negative ("no narrative/raw-text table").
        # Without its own CORE_ALSO entry an index-only edit filtered to
        # `changed == []`, took the docs-only early return and selected NO
        # surface — so the census could go stale again with the pin that guards
        # it never selected. A `core` registration alone cannot fix that: a
        # surface's test_files only run when the surface is ALREADY selected.
        # Measured on the pre-fix head: `_sel(["docs/00_index.md"])` ->
        # surfaces=[], full=False, test_record_schema_placement.py ABSENT.
        "docs/00_index.md",
        # #9881 / PR #9948 review P2: the FORWARD migration that RE-DECLARES the
        # Object/Subject key contract (`node_identity.key_property` plus both
        # `chk_*_node_key_is_contract_key` pins) — the one file that can make
        # `record_projection.key_properties()` report a contract the schema no
        # longer holds. Measured on the pre-fix head: `_sel([<this path>])` ->
        # surfaces=[], full=False, BOTH readers absent.
        "supabase/migrations/20261027000001_record_semantic_id_key.sql",
    ):
        r = _sel([changed])
        assert r["full"] is False, (changed, r)
        assert r["surfaces"] == ["core"], (changed, r)
        assert "test_record_schema_placement.py" in r["test_files"], (changed, r)
        assert "test_record_projection.py" in r["test_files"], (changed, r)


def test_the_semantic_id_key_migration_selects_every_reader_of_the_contract():
    # PR #9948 review P2: `20261027000001_record_semantic_id_key.sql` is the one
    # diff that can RE-DECLARE the key contract — `record_projection.
    # key_properties()` takes the LAST declaration in the migration chain, and
    # `cache_projection`/`read_ledger` delegate to it. So the three READERS of
    # that contract plus the schema pin must run on a change to this file ALONE.
    # Mutation check: removing its CORE_ALSO row (or the arm above) fails both
    # this test and the tuple arm above with `surfaces == []`.
    changed = "supabase/migrations/20261027000001_record_semantic_id_key.sql"

    r = _sel([changed])

    assert r["full"] is False, r
    assert r["surfaces"] == ["core"], r
    for reader in (
        "test_record_schema_placement.py",  # the CHECK pins read from disk
        "test_record_projection.py",  # key_properties()
        "test_cache_projection.py",  # delegates to key_properties()
        "test_read_ledger.py",  # KEY_FIELDS is pinned to it
    ):
        assert reader in r["test_files"], (reader, r)


def test_the_tenancy_migration_selects_the_posture_pins():
    # #9554, PR #9600 review cycle 1 (P2): the TENANCY POSTURE migration defines
    # the record's entire boundary — the role, its grants (including the
    # IDENTITY sequence's USAGE) and the `*_app_tenant` policies (one per record
    # table, ten since #9555's `session_narrative`). It sorts
    # outside the `2026101200000` CORE_ALSO prefix and `supabase/` is in
    # NON_PYTHON_PREFIXES, so without its own entry a migration-only edit
    # (dropping a `WITH CHECK`, flipping `is_local`, re-adding `BYPASSRLS`,
    # deleting the sequence grant) selected NO PYTHON surface: the PGlite posture
    # suite already ran (the workflow selects it from its own `supabase/migrations/`
    # prefix output), but `tests/test_record_tenancy_guc.py` — the suite that pins
    # this migration's text — did not. Mutation check: removing the CORE_ALSO entry makes
    # this assertion fail with `surfaces == []`.
    changed = "supabase/migrations/20261025000001_record_tenancy_role_and_guc.sql"

    r = _sel([changed])

    assert r["full"] is False, r
    assert r["surfaces"] == ["core"], r
    assert "test_record_tenancy_guc.py" in r["test_files"], r


def test_the_source_canonical_index_migration_selects_the_resolver_pins():
    # #9967 review cycle 2, P1 follow-through: `20261025000002_record_source_
    # canonical_index.sql` is the index behind the `:Source` resolver's
    # `canonical_url` arm — the arm #9956 makes run on EVERY source write. It
    # sorts outside every `2026101200000` CORE_ALSO prefix and `supabase/` is a
    # NON_PYTHON_PREFIX, so before this entry a diff touching ONLY this file
    # selected NO surface: renaming or dropping the index would have run no pin,
    # silently (the #1349/#3332/#3616 class; `test_record_projection.py`'s
    # registration cannot fix it, because a surface's test_files only run when
    # that surface is ALREADY selected).
    #
    # Measured before the CORE_ALSO entry: `_sel([<this path>])` ->
    # `surfaces: []`, `full: false`. Mutation check: removing the entry makes the
    # assertions below fail with `surfaces == []`.
    changed = "supabase/migrations/20261025000002_record_source_canonical_index.sql"

    r = _sel([changed])

    assert r["full"] is False, r
    assert r["surfaces"] == ["core"], r
    # The resolver itself, and the two suites that pin it to the migration
    # REGISTRY — the closures this file's absence broke (#9967 review 1a/1b).
    for pin in (
        "test_record_projection.py",  # the resolver + row-key/edge parity
        "test_7950_capstone_probe.py",  # probe lists pinned to RECORD_MIGRATIONS
        "test_9078_record_written_by_production_path.py",  # the re-apply registry
    ):
        assert pin in r["test_files"], (pin, r)


def test_postgres_storage_guard_runs_on_migration_or_doc_change():
    # PR #8744 review P2: `tests/test_postgres_storage.py` pins the SQL↔Python
    # contract — the two RPC names and the `p_since`/`p_until` parameter names
    # read from the migration text. A rename on either side makes PostgREST 404
    # the wrapper at runtime, and the PGlite suite cannot catch it (it calls the
    # functions positionally). Both paths are under NON_PYTHON_PREFIXES
    # (`supabase/`, `docs/`) and match no SOURCE_PATTERNS row, so before the
    # CORE_ALSO entries a change to either side ALONE selected NO surface and
    # took the docs-only early return — the guard never ran on the very change
    # it exists to catch (the #1349/#3332/#3616 silent-drop class; a name in a
    # surface's test_files only runs when that surface is ALREADY selected, so
    # the `core` registration could not fix it).
    #
    # Measured before the entries: `_sel([...])` -> surfaces=[], full=False,
    # 43 test files, test_postgres_storage.py absent.
    # Mutation check: removing either CORE_ALSO entry fails its arm below.
    for changed in (
        "supabase/migrations/20261015000001_postgres_storage_footprint.sql",
        "docs/ops/postgres-storage-measurement.md",
    ):
        r = _sel([changed])
        assert r["full"] is False, (changed, r)
        assert r["surfaces"] == ["core"], (changed, r)
        assert "test_postgres_storage.py" in r["test_files"], (changed, r)


def test_a_linter_policy_file_at_any_depth_runs_the_full_matrix():
    # #7435 review P1 (with its round-6 correction): cli2 reads a `.markdownlint*`
    # config from ANY directory on the path to a linted file, and a more specific
    # config OVERRIDES the repo one — so `docs/.markdownlint.json` turns a rule off
    # for every doc beneath it, and `battery/.markdownlint.json` does the same for
    # `battery/`.
    #
    # TWO silent routes out of the gate had to be closed, and the first fix closed
    # only one. (a) `_keep_changed` DROPPED the path — `docs/` is a
    # NON_PYTHON_PREFIXES entry and no SOURCE_PATTERNS row matches the basename —
    # so the PR selected the 31-file tier-1 smoke set. (b) Keeping the path is not
    # enough: the match loop still CLAIMED it under a surface-owned prefix
    # (`battery/.markdownlint.json` → ['battery'], `tools/longmem_eval/...` →
    # ['eval']), and neither of those legs runs
    # `tests/test_docs_lint_baseline.py` — whose committed-policy pin is the only
    # thing that makes a config edit move the snapshot. A config-only PR also skips
    # the `docs` job's differ steps (there is no changed `.md`), so the edit landed
    # with the snapshot unmoved: exactly the invariant
    # `docs/ci/docs-lint-baseline.md` promises.
    #
    # Mutation check: each half fails on its own. Dropping the
    # `_is_linter_policy_path` claim in `_keep_changed` makes the `docs/` and
    # `website/` cases tier-1, and dropping the early `_full_selection` return in
    # `select()` makes every surface-owned case select its surface instead of ALL.
    for path in (
        "docs/.markdownlint.json",                 # dropped by the prefix fallback
        "website/.markdownlintrc",                 # dropped by the prefix fallback
        "battery/.markdownlint.json",              # CLAIMED by a surface
        "battery/.lycheeignore",                   # CLAIMED by a surface
        "battery/lychee.toml",                     # CLAIMED by a surface
        "tools/longmem_eval/.markdownlint.json",   # CLAIMED as `eval`
        ".markdownlint-cli2.jsonc",                # root: already full
        "lychee.toml",                             # root: already full
    ):
        r = _sel([path])
        assert r["full"] is True, (path, r)
        assert r["test_files"] == "ALL", (path, r)
    # An ordinary nested doc is still docs-only (tier 1) — not swept into `full`.
    assert _sel(["docs/notes.md"])["surfaces"] == []
    assert _sel(["docs/notes.md"])["full"] is False


def test_queue_resweep_tool_change_selects_core_not_tier1():
    # tools/queue_resweep.py owns tests/test_queue_resweep.py (82 hermetic cases
    # pinning dry-run-by-default, the never-touch-a-queued-PR rule, and the
    # artifact-not-the-send verification).
    #
    # Before its CORE_ALSO entry this was the #3261 silent-drop class: `tools/`
    # is a flat NON_PYTHON_PREFIXES entry, so a tools-only change came back with
    # `changed == []` and took the docs-only early return — `select()` returned
    # NO surface, and the guard never ran on the file it guards. Measured before
    # the fix: `_sel(["tools/queue_resweep.py"])` -> surfaces=[], full=False,
    # test_queue_resweep.py absent.
    #
    # CORE_ALSO (not TOOL_CARVEOUTS) is the deliberate choice: the guard is
    # hermetic and sub-second, so selecting `core` runs it at the lowest CI cost.
    # Mutation check: removing the CORE_ALSO entry fails assert 2 below.
    r = _sel(["tools/queue_resweep.py"])
    assert "core" in r["surfaces"], r
    assert "test_queue_resweep.py" in r["test_files"], r
    assert r["full"] is False, r


def test_retype_snapshot_tool_change_selects_core_not_tier1():
    # #8882 review P1: tools/retype_snapshot.py is the CLI for the offline
    # re-typing mechanics and owns tests/test_retype_8882.py, registered `core`.
    #
    # `tools/` is a flat NON_PYTHON_PREFIXES entry and no SOURCE_PATTERNS row
    # matches the tool, so a CLI-only diff came back with `changed == []` and
    # took the docs-only early return: `select()` returned NO surface and the
    # file's own CLI-contract cases (`--decisions requires --out`, the propose →
    # apply round trip, the exit codes) never ran on the PR editing the CLI.
    # Registering the test under `core` does not help — a name in a surface's
    # test_files only runs when that surface is already selected.
    #
    # Measured before the CORE_ALSO entry: `_sel(["tools/retype_snapshot.py"])`
    # -> surfaces=[], full=False, test_retype_8882.py absent.
    # CORE_ALSO (not TOOL_CARVEOUTS) is the deliberate choice: the suite is
    # hermetic and DB-free, so selecting `core` runs it at the lowest CI cost.
    # Mutation check: removing the CORE_ALSO entry fails assert 2 below.
    r = _sel(["tools/retype_snapshot.py"])
    assert "core" in r["surfaces"], r
    assert "test_retype_8882.py" in r["test_files"], r
    assert r["full"] is False, r


def test_backfill_graph_turns_tool_change_selects_core_not_tier1():
    # #10017 review P1: tools/backfill_graph_turns.py is the pre-delete turn
    # census — the artifact `verify` compares the record against before an
    # IRREVERSIBLE delete — and it owns tests/test_10017_backfill_census.py,
    # registered `core`.
    #
    # `tools/` is a flat NON_PYTHON_PREFIXES entry and no SOURCE_PATTERNS row
    # matches the tool, so a census-only diff came back with `changed == []` and
    # took the docs-only early return: `select()` returned NO surface, so the
    # file's own suite (and BOTH of its repo guards — the #8118 read bound in
    # tests/test_bounded_raw_clients_3065.py and the #5128 interpreter guard in
    # tests/test_entry_point_python_guard.py) never ran on the PR editing the
    # census. Registering the test under `core` does not help: a name in a
    # surface's test_files only runs when that surface is ALREADY selected.
    #
    # Measured before the CORE_ALSO entry: `--changed-files
    # tools/backfill_graph_turns.py` -> surfaces=[], full=False, and
    # test_10017_backfill_census.py absent from the 68 selected files. Same
    # #1349/#3332/#3616 silent-drop class as tools/graph_census.py and
    # tools/queue_conflict_census.py above; CORE_ALSO (not TOOL_CARVEOUTS)
    # because the suite is hermetic (a fake graph + a fake client, no DB).
    # Mutation check: removing the CORE_ALSO entry fails assert 2 below.
    r = _sel(["tools/backfill_graph_turns.py"])
    assert "core" in r["surfaces"], r
    assert "test_10017_backfill_census.py" in r["test_files"], r
    assert r["full"] is False, r


def test_backfill_copy_turns_tool_change_selects_core_not_tier1():
    # #10017 review P1 (the third time this class was measured on this chain):
    # tools/backfill_copy_turns.py is the COPY phase — the write whose turns the
    # parity gate then certifies before an IRREVERSIBLE delete — and it owns
    # tests/test_10017_backfill_copy.py, registered `core`.
    #
    # `tools/` is a flat NON_PYTHON_PREFIXES entry and no SOURCE_PATTERNS row
    # matches the tool, so a copy-only diff came back with `changed == []` and
    # took the docs-only early return: `select()` returned NO surface, so the
    # file's own suite — and its #5128 interpreter guard, the repo gate that DID
    # red this tool at 4cdad4be7 — never ran on the PR editing the copy.
    # Registering the test under `core` does not help: a name in a
    # surface's test_files only runs when that surface is ALREADY selected.
    #
    # Measured before the CORE_ALSO entry: `--changed-files
    # tools/backfill_copy_turns.py` -> surfaces=[], full=False,
    # test_10017_backfill_copy.py ABSENT. Same #1349/#3332/#3616 silent-drop
    # class as tools/backfill_graph_turns.py above. CORE_ALSO (not
    # TOOL_CARVEOUTS) because the suite is hermetic (a fake graph + a fake
    # journal, no DB). Mutation check: removing the CORE_ALSO entry fails the
    # second assert below.
    r = _sel(["tools/backfill_copy_turns.py"])
    assert "core" in r["surfaces"], r
    assert "test_10017_backfill_copy.py" in r["test_files"], r
    assert r["full"] is False, r


def test_graph_census_tool_change_selects_core_not_tier1():
    # #8882 review P1: tools/graph_census.py is the CLI for the measured census
    # and owns tests/test_graph_census_8882.py, registered `core`.
    #
    # `tools/` is a flat NON_PYTHON_PREFIXES entry and no SOURCE_PATTERNS row
    # matches the tool, so a CLI-only diff came back with `changed == []` and took
    # the docs-only early return: `select()` returned NO surface, so the file's own
    # CLI-contract cases (--out/--samples, the 0/1/2 exit-code split, --diff) never
    # ran on the PR editing the CLI. Registering the test under `core` does not
    # help — a name in a surface's test_files only runs when that surface is
    # already selected.
    #
    # Measured before the CORE_ALSO entry: `_sel(["tools/graph_census.py"])`
    # -> surfaces=[], full=False, test_graph_census_8882.py absent. The same
    # measurement is what tools/queue_conflict_census.py and tools/edge_census.py
    # record: an instrument that makes an uncounted class visible must not be
    # invisible to CI selection itself. CORE_ALSO (not TOOL_CARVEOUTS) is the
    # deliberate choice: the suite is hermetic and DB-free.
    # Mutation check: removing the CORE_ALSO entry fails assert 2 below.
    r = _sel(["tools/graph_census.py"])
    assert "core" in r["surfaces"], r
    assert "test_graph_census_8882.py" in r["test_files"], r
    assert r["full"] is False, r


def test_legacy_id_normalise_tool_change_selects_core_not_tier1():
    # #9523 review P1: tools/legacy_id_normalise.py is the CLI for the legacy-id
    # normaliser and owns tests/test_8607_legacy_id_normaliser.py, registered
    # `core`.
    #
    # `tools/` is a flat NON_PYTHON_PREFIXES entry and no SOURCE_PATTERNS row
    # matches the tool, so a CLI-only diff came back with `changed == []` and
    # took the docs-only early return: `select()` returned NO surface, and the
    # file whose entire content is the dry-run default, the
    # `--apply`/`--revert`-requires-`--out` rule and the 0/1/2 exit contract ran
    # none of its own cases. Registering the test under `core` does not help — a
    # name in a surface's test_files only runs when that surface is already
    # selected.
    #
    # Measured before the CORE_ALSO entry: `_sel(["tools/legacy_id_normalise.py"])`
    # -> surfaces=[], full=False, test_8607_legacy_id_normaliser.py absent.
    # CORE_ALSO (not TOOL_CARVEOUTS) is the deliberate choice: the suite is
    # hermetic (pure functions over a dict — no graph, no Postgres), so selecting
    # `core` runs it at the lowest CI cost.
    # Mutation check: removing the CORE_ALSO entry fails assert 2 below.
    r = _sel(["tools/legacy_id_normalise.py"])
    assert "core" in r["surfaces"], r
    assert "test_8607_legacy_id_normaliser.py" in r["test_files"], r
    assert r["full"] is False, r


def test_write_op_reachability_tool_change_selects_core_not_tier1():
    # #4495 review P2: tools/write_op_reachability.py is the CLI that reports the
    # write-op allowance's stock/flow conversion, and its guard
    # (tests/test_write_op_reachability.py) is `core`+`api` registered.
    #
    # `tools/` is a flat NON_PYTHON_PREFIXES entry and no SOURCE_PATTERNS row
    # matches the tool, so a CLI-only diff came back with `changed == []` and
    # took the docs-only early return: `select()` returned NO surface, so the
    # exit-code contract (0 stated-yield / 1 unreachable / 2 not-measurable /
    # 3 measured-uncertified), the UNCERTIFIED rule and the absence rule would
    # never run on the PR editing the CLI. Registering the test under `core` does
    # not help — a name in a surface's test_files only runs when that surface is
    # already selected. Same #1349/#3332/#3616 silent-drop class as the
    # tools/graph_census.py case above: an instrument that makes an uncounted
    # class visible must not be invisible to CI selection itself.
    #
    # Measured before the CORE_ALSO entry: `_sel(["tools/write_op_reachability.py"])`
    # -> surfaces=[], full=False, test_write_op_reachability.py absent.
    # CORE_ALSO (not TOOL_CARVEOUTS) is the deliberate choice: the guard is
    # hermetic (synthetic books, a fake SDK) and `core` is where its core arm
    # lives.
    # Mutation check: removing the CORE_ALSO entry fails assert 2 below.
    r = _sel(["tools/write_op_reachability.py"])
    assert "core" in r["surfaces"], r
    assert "test_write_op_reachability.py" in r["test_files"], r
    assert r["full"] is False, r


def test_why_layer_instrument_selects_its_guards_on_every_vector():
    # #3990: `tortoise/why.py` selects `core` and NOTHING else on a PR, and
    # `tools/` is a flat NON_PYTHON_PREFIXES entry, so a
    # `tools/why_layer_rate.py`-only diff came back with `changed == []` and took
    # the docs-only early return (`select()` returned NO surface). Either way the
    # two guards added for the why-layer operator leg would never run on the
    # files they pin — the #1349/#2938/#3332 silent-drop class the neighbouring
    # tests record. Registered under `core` (the surface why.py selects) plus a
    # CORE_ALSO row for the tool.
    # Mutation check: dropping the CORE_ALSO row fails the tool assert; moving
    # either test off `core` fails the why.py assert.
    for changed in (["tortoise/why.py"], ["tools/why_layer_rate.py"],
                    ["tests/fixtures/why_layer_eval.json"]):
        r = _sel(changed)
        assert r["full"] is False, (changed, r)
        assert "core" in r["surfaces"], (changed, r)
        assert "test_why_operator_mediation.py" in r["test_files"], (changed, r)
        assert "test_why_layer_rate.py" in r["test_files"], (changed, r)


def test_write_op_reachability_guard_runs_on_every_surface_it_pins():
    # #4495 review P1: tests/test_write_op_reachability.py pins THREE modules and
    # they do not all select the same surface — so a single registration arm is
    # inert for part of the file:
    #
    #   tortoise/pricing.py            -> core  (no SOURCE_PATTERNS row)
    #   tortoise/metering.py           -> api   (exact SOURCE_PATTERNS["api"])
    #   tortoise/supabase_control.py   -> api   (exact SOURCE_PATTERNS["api"])
    #
    # A named-surface match REPLACES the `core` fallback, so with a `core`-only
    # registration a metering.py-only PR (the `get_write_op_yield` attribution
    # rule) or a supabase_control.py-only PR (the counters reader) selected `api`
    # and never ran this guard — the #2938/#3154/#4367 silent-drop class, and the
    # same lesson test_supabase_control_change_selects_the_org_purge_rpc_guard
    # records. Hence DUAL registration.
    # Mutation check: deleting the `api` arm fails the loop below; deleting the
    # `core` arm fails the pricing.py assert.
    for api_source in ("tortoise/metering.py", "tortoise/supabase_control.py"):
        r = _sel([api_source])
        assert r["full"] is False, (api_source, r)
        assert "api" in r["surfaces"], (api_source, r)
        assert "test_write_op_reachability.py" in r["test_files"], (api_source, r)
    r = _sel(["tortoise/pricing.py"])
    assert r["full"] is False, r
    assert "core" in r["surfaces"], r
    assert "test_write_op_reachability.py" in r["test_files"], r


def test_retired_orchestrator_harness_selects_its_retained_guards():
    # RETIRED 2026-10-19. The harness moved to agent-infra/scripts/orchestrator-harness/
    # (single source of truth) and tests/test_orchestrator_harness.py was retired WITH the
    # copy it drove.
    #
    # The directory is NOT a dead path, though: two scripts are retained because their fixes are
    # UNPORTED (`merge-one.sh` — #8484/#8602/#8612; `merged-not-closed.sh` — no landed copy
    # anywhere, agent-infra#2160 is still open), and they carry hermetic guards of their own
    # (tests/test_orchestrator_harness_retained.py), as does the guard that keeps the rest
    # retired (tests/test_harness_pointer_guard.py for tools/check-harness-fork.sh).
    #
    # Both must run on a change to their subject. `operations/` and `tools/` are flat
    # NON_PYTHON_PREFIXES entries and no SOURCE_PATTERNS row matches either path, so without the
    # CORE_ALSO entries an operations-only or tools-only diff came back with `changed == []`, took
    # the docs-only early return and ran NO surface — the #1349/#3332/#3616 silent-drop class (the
    # guard would not run on the PR that weakens the guard). A name in a surface's test_files is
    # NOT sufficient: it only runs when the surface is already selected.
    #
    # Mutation check: removing either CORE_ALSO entry restores `surfaces=[]` and reds these asserts.
    r = _sel(["operations/orchestrator-harness/lane.sh"])
    assert "core" in r["surfaces"], r
    assert "test_orchestrator_harness_retained.py" in r["test_files"], r
    # ⛔ The RETIRED suite must not come back with it: a stale registration schedules a file that
    # no longer exists and hides the retirement from anyone reading the map.
    assert "test_orchestrator_harness.py" not in r["test_files"], r
    # ⛔ `full` MUST STAY FALSE (review finding): a change under this path must not start forcing
    # a full run — that would be a heavy, silent regression from a retirement. Pinning it makes
    # this "deliberately retired" rather than "path dropped".
    assert r["full"] is False, r

    # The guard's own path: same class, same requirement.
    g = _sel(["tools/check-harness-fork.sh"])
    assert "core" in g["surfaces"], g
    assert "test_harness_pointer_guard.py" in g["test_files"], g
    assert g["full"] is False, g

    # The pointer README is a docs change: still no harness suite, still not a full run.
    r2 = _sel(["operations/orchestrator-harness/README.md"])
    assert "test_orchestrator_harness.py" not in r2["test_files"], r2
    assert r2["full"] is False, r2


def test_queue_dispatch_and_fleet_state_change_select_core_not_tier1():
    # PR #8594 review P2: tools/agent-infra-queue-dispatch.py owns
    # tests/test_queue_dispatch_fail_closed.py and tools/fleet_state.py owns
    # tests/test_fleet_state.py — both `core`-registered. `tools/` is a flat
    # NON_PYTHON_PREFIXES entry and neither path matches a SOURCE_PATTERNS row,
    # so each one's own change came back with `changed == []` and took the
    # docs-only early return — `select()` returned NO surface and the guard never
    # ran on the file it guards (the #1349/#3332/#3616 silent-drop class).
    #
    # CORE_ALSO (not TOOL_CARVEOUTS) is the deliberate choice: both guards are
    # hermetic and sub-second, so selecting `core` runs them at the lowest CI cost.
    # Mutation check: removing either CORE_ALSO entry fails its assert below.
    for path, guard in (
        ("tools/agent-infra-queue-dispatch.py", "test_queue_dispatch_fail_closed.py"),
        ("tools/fleet_state.py", "test_fleet_state.py"),
    ):
        r = _sel([path])
        assert r["full"] is False, (path, r)
        assert "core" in r["surfaces"], (path, r)
        assert guard in r["test_files"], (path, r)
        assert set(r["test_files"]) != _tier1(), (path, r)


def test_capstone_probe_change_selects_core_not_tier1():
    # docs/evidence/7950-capstone/probes/e2e_chain_probe.py owns
    # tests/test_7950_capstone_probe.py (`core`): the DB-free pin asserting the
    # probe's two reconciliation readers (`record_key_map` / `cache_key_map`)
    # return the REAL property maps, not `{}` placeholders — two empty mappings
    # always agree, so the pre-fix shape made `field_mismatches` structurally
    # zero and the probe's committed log proved key-set agreement only.
    #
    # PR #8609 review P1: `docs/` is a flat NON_PYTHON_PREFIXES entry and no
    # SOURCE_PATTERNS row matches the probe, so a PROBE-ONLY change came back
    # with `changed == []` and took the docs-only early return — `select()`
    # returned NO surface and the pin never ran on the very file whose readers
    # it pins. Reverting the readers to the pre-fix `{}` shape would land with
    # CI green (the #1349/#3332/#3616 silent-drop class). A SOURCE_PATTERNS
    # entry alone cannot fix it: `_selection_relevant()` deliberately skips the
    # `core` surface, and the guard is `core`-registered.
    #
    # Measured before the CORE_ALSO entry: `_sel(["docs/evidence/7950-capstone/
    # probes/e2e_chain_probe.py"])` -> surfaces=[], full=False, test_files 39,
    # test_7950_capstone_probe.py absent. CORE_ALSO keeps the path and selects
    # `core`; the directory prefix covers the sibling probes too.
    # Mutation check: removing the CORE_ALSO entry fails assert 1 below.
    for path in (
        "docs/evidence/7950-capstone/probes/e2e_chain_probe.py",
        "docs/evidence/7950-capstone/probes/s1_census.py",
    ):
        r = _sel([path])
        assert "core" in r["surfaces"], (path, r)
        assert "test_7950_capstone_probe.py" in r["test_files"], (path, r)
        assert r["full"] is False, (path, r)


def test_the_capstone_gate_change_selects_core_not_tier1():
    # #9089 review P2: the GATE itself (tools/capstone_verdict_check.py) owns
    # tests/test_7949_capstone_verdict_check.py, which is registered `core` —
    # and `config/ci-surfaces.yml` names `core` as its home. Before the
    # CORE_ALSO row a gate-only diff came back with NO surface at all and the
    # pin ran only because it also sits in the `tier1` curated list, so `core`
    # was nominal and one `tier1` edit would have dropped the pin silently
    # (the #1349/#3332/#3616 class, reached through the docs-only early return
    # rather than through `_selection_relevant`).
    #
    # MEASURED before the row (2026-10-20, `select()` in-process):
    # `_sel(["tools/capstone_verdict_check.py"])` -> surfaces=[], full=False.
    # Mutation check: removing the CORE_ALSO row fails assert 1 below.
    r = _sel(["tools/capstone_verdict_check.py"])
    assert "core" in r["surfaces"], r
    assert "test_7949_capstone_verdict_check.py" in r["test_files"], r
    assert r["full"] is False, r


def test_the_capstone_evidence_bundle_selects_core_not_tier1():
    # PR #9196 review P1: the S2 verdict's deciding artifacts are committed TEXT
    # — `s2-wiring-census.log` (the census output), `e2e-chain-probe.log` (the
    # end-to-end transcript) and `README.md` (the quotes and the S2 rows) — and
    # `tests/test_7950_capstone_probe.py` now re-MEASURES two of them
    # (`test_the_s2_census_log_is_a_LIVE_measurement` re-runs the committed
    # probe; `test_the_evidence_readme_quotes_the_log_it_cites_verbatim` pins the
    # README to the log).
    #
    # The CORE_ALSO entry used to be the `probes/` subdirectory only, so a diff
    # that touched just a LOG or the README came back with `changed == []` and
    # took the docs-only early return: a hand-edited number in the artifact the
    # S2 verdict rests on merged with the pin that guards it NEVER RUN — the
    # #1349/#3332/#3616 silent-drop class, and the same "stale flag never
    # revalidated" shape the artifact itself is supposed to catch.
    #
    # Measured before widening the entry (2026-10-19, `select()` in-process):
    # `_sel(["docs/evidence/7950-capstone/s2-wiring-census.log"])` ->
    # surfaces=[], full=False, test_7950_capstone_probe.py ABSENT.
    # Mutation check: narrowing the entry back to `probes/` fails assert 1.
    for path in (
        "docs/evidence/7950-capstone/s2-wiring-census.log",
        "docs/evidence/7950-capstone/e2e-chain-probe.log",
        "docs/evidence/7950-capstone/README.md",
    ):
        r = _sel([path])
        assert "core" in r["surfaces"], (path, r)
        assert "test_7950_capstone_probe.py" in r["test_files"], (path, r)
        assert r["full"] is False, (path, r)


def test_the_capstone_census_pin_runs_on_a_main_py_change():
    # PR #9196 review P1: the OTHER direction of the same hole. The census pin
    # compares each hit's FILE and COUNT (the line ORDINAL is normalised away,
    # #9196 review P2), and the one live hit is
    # `tortoise/__main__.py: view = read_record_view(...)`. That file is an
    # EXACT `api` SOURCE_PATTERNS match and NOT in `CORE_ALSO`, so a named-surface
    # match REPLACES the `core` fallback: before the `api` registration a
    # `__main__.py`-only diff returned surfaces=['api'] with the `core`-registered
    # probe ABSENT, so a diff that ADDED or REMOVED that call site — the wiring
    # change the pin exists to catch — merged green and only reddened `main` on
    # the post-merge full selection. The instrument that makes S2's staleness
    # visible must not be invisible to CI selection on the file its hit lives in
    # (#1349/#2938/#3154 class).
    #
    # Measured before the `api` entry (2026-10-19, `select()` in-process):
    # `_sel(["tortoise/__main__.py"])` -> surfaces=['api'], full=False, 283 test
    # files, test_7950_capstone_probe.py ABSENT.
    # Mutation check: removing the `api` entry fails `test_files` below.
    r = _sel(["tortoise/__main__.py"])
    assert "api" in r["surfaces"], r
    assert "test_7950_capstone_probe.py" in r["test_files"], r
    assert r["full"] is False, r


def test_7949_capstone_probe_change_selects_core_not_tier1():
    # #9080: docs/evidence/7949-capstone/probes/capstone_run.py owns
    # tests/test_7949_capstone_verdict_check.py (`core`), the DB-free pin on the
    # run's refusal chain and the pure observers every row is derived from.
    #
    # ⛔ The entry that keeps this `core` is the BUNDLE prefix
    # (`docs/evidence/7949-capstone/`, added by #9076), NOT a `probes/` row.
    # This PR added the narrower `docs/evidence/7949-capstone/probes/` and its
    # claim was wrong on both halves (#9089 review P1): the broader row was
    # already in place and `path.startswith()` makes a narrower prefix inside
    # it dead, so (a) the entry changed no selection, and (b) the mutation
    # claim this comment used to carry ("removing the entry fails assert 1")
    # was falsified — the test passed with the entry deleted. MEASURED
    # (2026-10-20, `select()` in-process, narrow row deleted):
    # `_sel(["docs/evidence/7949-capstone/probes/capstone_run.py"])` ->
    # surfaces=['core'], full=False, the pin present. The narrow row was
    # removed; the mutation check below now names the row that does the work.
    for path in (
        "docs/evidence/7949-capstone/probes/capstone_run.py",
        "docs/evidence/7949-capstone/probes/README.md",
    ):
        r = _sel([path])
        assert "core" in r["surfaces"], (path, r)
        assert "test_7949_capstone_verdict_check.py" in r["test_files"], (path, r)
        assert r["full"] is False, (path, r)


def test_no_core_also_entry_is_shadowed_by_a_parent_prefix():
    # #9089 review P1: `CORE_ALSO` matching is `path.startswith(entry)`, so an
    # entry that is a DESCENDANT of another entry can never admit a path its
    # parent does not — it is dead config that reads as the reason a path is
    # covered. This bundle added exactly one such row
    # (`docs/evidence/7949-capstone/probes/` under the bundle prefix) and its
    # pin passed with the row deleted, so nothing else could notice.
    #
    # Non-vacuity: with that row restored this test fails (it is the only
    # shadowed pair in the tuple, measured 2026-10-20: 63 entries, 1 pair).
    entries = list(cs.CORE_ALSO)
    shadowed = sorted((a, b) for a in entries for b in entries
                      if a != b and a.startswith(b))
    assert shadowed == [], (
        "CORE_ALSO rows shadowed by a parent prefix (dead config — delete the "
        f"narrow row, keep the broad one): {shadowed}")


def test_the_capture_receipt_key_registry_pin_runs_for_every_surface_that_owns_it():
    # PR #9668 review P1: `tests/test_5051_capture_receipt_key_registry.py` was
    # registered in `core` ALONE, and `core` is a FALLBACK surface in
    # `select()` (`if not matched: matched.add("core")`) — a named-surface match
    # REPLACES it. Every subject the pin guards is a named-surface match:
    #
    #   * `tortoise/hosted_api.py` owns BOTH derivations the pin exists to hold
    #     to the formatter — `_capture_receipt_buckets` (the delete path's orphan
    #     sweep) and `_capture_server_owned_keys` (the client-write-refusal set
    #     #3681 closed) — and is an EXACT `api` source pattern AND an EXACT
    #     `onboarding` one;
    #   * `website/apps/dashboard/src/captureStatus.js` is the JS reader pin
    #     and matches `onboarding`;
    #   * `tortoise/capture_receipts.py` — the ONLY intended change direction (a
    #     FORMAT change) — falls through to `core`, which is what the original
    #     row covers.
    #
    # So the pin fired only for a change that also moved the formatter: BOTH
    # mutations #9668 used as its own evidence are `hosted_api.py`-only edits
    # (a stale reconcile-bucket prefix; `hermes` dropped from the server-owned
    # derivation), and a hosted_api.py-only diff selected `api`+`onboarding`
    # with the pin ABSENT — the drift landed green. Same #1349/#2938/#3154
    # silent-drop class (and same remedy: dual-registration) as
    # `test_the_capstone_census_pin_runs_on_a_main_py_change` above and the
    # #7809 / #8693 / #9068 `api` arms.
    #
    # Measured before the `api`/`onboarding` arms (2026-10-17, `select()`
    # in-process): hosted_api.py -> surfaces=['api','onboarding'], pin ABSENT;
    # captureStatus.js -> surfaces=['onboarding'], pin ABSENT;
    # capture_receipts.py -> surfaces=['core'], pin PRESENT.
    # Mutation check: removing either arm fails the assert below.
    for source, surface in (
        ("tortoise/hosted_api.py", "api"),
        ("tortoise/hosted_api.py", "onboarding"),
        ("website/apps/dashboard/src/captureStatus.js", "onboarding"),
        ("tortoise/capture_receipts.py", "core"),
    ):
        r = _sel([source])
        assert surface in r["surfaces"], (source, surface, r["surfaces"])
        assert r["full"] is False, (source, r)
        assert "test_5051_capture_receipt_key_registry.py" in r["test_files"], (
            f"{source} selects {r['surfaces']} but does NOT run the #5051 "
            "receipt-key pin — a re-spelled key at that site (or a moved "
            "format) would merge green and only surface on the next full "
            "run (#1349/#2938/#3154 silent-drop class)"
        )

    # The `onboarding` arm covers the `website/apps/dashboard/src/` PREFIX, not
    # the dashboard root: its `package.json` / `package-lock.json` match no
    # `onboarding` pattern and fall back to `core` (the #9524 note in
    # `config/ci-surfaces.yml`). Pinned because the #9668 comment on that arm
    # said `website/apps/dashboard/` was the pattern — a claim that would read
    # as "a dashboard MANIFEST dependency is already covered" long after it
    # stopped being true.
    assert _sel(["website/apps/dashboard/package.json"])["surfaces"] == ["core"]


def test_supabase_config_change_selects_core_not_tier1():
    # supabase/config.toml is the committed LOCAL-stack config and resolves its
    # providers by `env(NAME)` substitution, so each of those names is required
    # by a tracked file. tests/test_env_contract_covers_supabase_config.py
    # (`core`) is the guard: every config `env(NAME)` must be declared in
    # .env.example, the contract an operator reads.
    #
    # `supabase/` is a flat NON_PYTHON_PREFIXES entry and the path matches no
    # SOURCE_PATTERNS row, so before the CORE_ALSO entry a CONFIG-ONLY change
    # came back with `changed == []` and took the docs-only early return —
    # `select()` returned NO surface and the guard never ran on the half of the
    # pair it exists to hold (the #1349/#3332/#3616 silent-drop class). A
    # SOURCE_PATTERNS entry cannot fix it: `_selection_relevant()` deliberately
    # skips the `core` surface, and the guard is `core`-registered.
    #
    # Measured before the entry: `_sel(["supabase/config.toml"])` ->
    # surfaces=[], full=False, 40 test files, the guard absent.
    # Mutation check: removing the CORE_ALSO entry fails the assert below.
    r = _sel(["supabase/config.toml"])
    assert "core" in r["surfaces"], r
    assert "test_env_contract_covers_supabase_config.py" in r["test_files"], r
    assert r["full"] is False, r


def test_install_queue_reconcile_schedule_tool_change_selects_core_not_tier1():
    # tools/install-queue-reconcile-schedule.sh owns
    # tests/test_install_queue_reconcile_schedule.py (hermetic cases pinning
    # that the scheduled run is the SAFE run: armed with --apply, the validated
    # queue passed EXPLICITLY, gh's directory on the job's PATH, XML/cron
    # escaping, and the install-only preflight).
    #
    # Before its CORE_ALSO entry this was the #3261 silent-drop class: `tools/`
    # is a flat NON_PYTHON_PREFIXES entry, so an installer-only change came back
    # with `changed == []` and took the docs-only early return — `select()`
    # returned NO surface, and the guard never ran on the file it guards.
    #
    # Measured before the fix: `_sel(["tools/install-queue-reconcile-schedule.sh"])`
    # -> surfaces=[], full=False, test_install_queue_reconcile_schedule.py absent.
    #
    # CORE_ALSO (not TOOL_CARVEOUTS) is the deliberate choice: the guard is
    # hermetic and subprocess-only, so selecting `core` runs it at the lowest CI
    # cost. Mutation check: removing the CORE_ALSO entry selects no surface, so
    # assert 1 below fails.
    r = _sel(["tools/install-queue-reconcile-schedule.sh"])
    assert "core" in r["surfaces"], r
    assert "test_install_queue_reconcile_schedule.py" in r["test_files"], r
    assert r["full"] is False, r


def test_install_ci_runner_x64_tool_change_selects_core_not_tier1():
    # #9639 review P1: tools/install-ci-runner-x64.sh owns
    # tests/test_ci_runner_x64_install.py — the label invariant (this arch's own marker only,
    # never `self-hosted-arm64` or a `ubuntu-<version>` pin, and `--fallback auto` READS the
    # gate's state instead of assuming it), the headroom guards, and the 1/2/3 exit contract.
    #
    # `tools/` is a flat NON_PYTHON_PREFIXES entry and no SOURCE_PATTERNS entry matches an
    # installer path, so before the CORE_ALSO entry an INSTALLER-ONLY change came back with
    # `changed == []`, took the docs-only early return and fell back to tier-1 smoke — the
    # #1349/#3332/#3616 silent-drop class: the guard never ran on the file it guards.
    #
    # The guard is ALSO a `carve_out` member (subprocess-only, no graph, no URI), so it is
    # stripped from `test_files` by design — the assertion is that the carve-out POOL carries it
    # and that `core` is selected, not that it rides in the diff selection.
    #
    # Mutation check: removing the CORE_ALSO entry makes the first assert fail (surfaces == []).
    from tools.ci_selection import carve_out_files

    r = _sel(["tools/install-ci-runner-x64.sh"])
    assert r["surfaces"] == ["core"], r
    assert r["full"] is False, r
    assert "test_ci_runner_x64_install.py" in carve_out_files(load_manifest()), r
    assert r["carve_out_run"] is True, r
    # and it is not merely tier-1 smoke: the selection carries core's own members too
    assert set(r["test_files"]) != _tier1(), r


def test_runner_autoscale_guard_change_selects_core_not_tier1():
    # tools/runner_autoscale.py owns tests/test_runner_autoscale.py (the measured
    # ceiling, the five-way classification, the fail-closed measurement arms and
    # the mutation family that proves each gate load-bearing).
    #
    # `tools/` is a flat NON_PYTHON_PREFIXES entry, so without the CORE_ALSO row a
    # tool-only change came back with `changed == []`, took the docs-only early
    # return and the guard's own pins never ran on the file they guard — the
    # #1349/#3332/#3616 silent-drop class. Mutation check: removing the CORE_ALSO
    # entry selects no surface, so assert 1 below fails.
    r = _sel(["tools/runner_autoscale.py"])
    assert "core" in r["surfaces"], r
    assert "test_runner_autoscale.py" in r["test_files"], r
    assert r["full"] is False, r


def test_runner_autoscale_installer_change_selects_core_not_tier1():
    # tools/install-runner-autoscale-schedule.sh owns
    # tests/test_install_runner_autoscale_schedule.py (the refusals with nothing
    # written, the rendered systemd/cron/launchd artifacts, the install-time
    # measurement probe, and the drift check). It ALARMS the guard, so an
    # installer-only diff that selects nothing is the worst case of this class:
    # the wiring could be broken while every tool test still passes.
    #
    # Measured before the fix (PR #9685 review round 9, P1-2):
    # `_sel(["tools/install-runner-autoscale-schedule.sh"])` -> surfaces=[],
    # full=False, test_install_runner_autoscale_schedule.py absent.
    #
    # CORE_ALSO (not TOOL_CARVEOUTS): the suite is hermetic and `core`-registered,
    # so selecting `core` runs it at the lowest CI cost. Mutation check: removing
    # the CORE_ALSO entry selects no surface, so assert 1 below fails.
    r = _sel(["tools/install-runner-autoscale-schedule.sh"])
    assert "core" in r["surfaces"], r
    assert "test_install_runner_autoscale_schedule.py" in r["test_files"], r
    assert r["full"] is False, r


def test_ci_queue_health_tool_change_selects_core_not_tier1():
    # tools/ci_queue_health.py owns tests/test_ci_queue_health.py (the hermetic
    # cases pinning the FLOOD classification — a deep queue of PR runs is not a
    # flood, a reporting workflow holding the queue is — the governor's bounds
    # (never a pull-request-event run, never a workflow outside TRIM_SAFE), and
    # the truncation labelling).
    #
    # Without its CORE_ALSO entry this is the #1349/#3332/#3616 silent-drop
    # class: `tools/` is a flat NON_PYTHON_PREFIXES entry, so a tool-only change
    # came back with `changed == []` and took the docs-only early return —
    # `select()` returned NO surface and the guard never ran on the file it
    # guards.
    #
    # Measured before the fix: `_sel(["tools/ci_queue_health.py"])` ->
    # surfaces=[], full=False, test_ci_queue_health.py absent.
    #
    # CORE_ALSO (not TOOL_CARVEOUTS) is the deliberate choice: the suite is
    # hermetic and `core`-registered, so selecting `core` runs it at the lowest
    # CI cost. Mutation check: removing the CORE_ALSO entry selects no surface,
    # so assert 1 below fails.
    r = _sel(["tools/ci_queue_health.py"])
    assert "core" in r["surfaces"], r
    assert "test_ci_queue_health.py" in r["test_files"], r
    assert r["full"] is False, r


def test_graph_write_repair_tool_change_selects_core_not_tier1():
    # PR #8427 review Finding 4: tools/graph_write_repair.py and its launchd
    # installer (tools/install-graph-write-repair-schedule.sh) own
    # tests/test_graph_write_repair.py (`core`): the probe classification that
    # authorises a repair, the `--quiet` count contract, and the installer's
    # escaping / interval validation / lint-before-move / load verification.
    #
    # `tools/` is a flat NON_PYTHON_PREFIXES entry and neither path matches a
    # SOURCE_PATTERNS row, so without the CORE_ALSO entries an installer-only
    # change came back with `changed == []` and took the docs-only early return:
    # `select()` returned NO surface and the guard never ran on the file it
    # guards (the #3261/#6138 silent-drop class).
    #
    # Mutation: remove either CORE_ALSO entry -> that path selects no surface, so
    # the assertion below fails.
    for path in ("tools/graph_write_repair.py",
                 "tools/install-graph-write-repair-schedule.sh"):
        r = _sel([path])
        assert r["full"] is False, (path, r)
        assert "core" in r["surfaces"], (path, r)
        assert "test_graph_write_repair.py" in r["test_files"], (path, r)
        assert set(r["test_files"]) != _tier1(), (path, r)


def test_record_wiring_check_tool_change_selects_core_not_tier1():
    # #9231 review P1 (#9188/#9078): tools/record_wiring_check.py is the ONLY
    # executor of the claim "the record has a production write call site" — the
    # defect #9078 measured was precisely that no such caller existed, and no
    # unit test could see it. Its guard is
    # tests/test_9078_record_written_by_production_path.py, registered `core`.
    #
    # Without the CORE_ALSO entry this is the #1349/#3332/#3616/#4309
    # silent-drop class: `tools/` is a flat NON_PYTHON_PREFIXES entry and no
    # SOURCE_PATTERNS row matches the tool, so a wiring-check-only change came
    # back with `changed == []` and took the docs-only early return. The gate is
    # therefore never executed on the one file whose whole purpose is to decide
    # whether the record is wired — the PR that weakens or deletes it merges
    # green.
    #
    # Measured before the fix: `_sel(["tools/record_wiring_check.py"])` ->
    # surfaces=[], full=False, 51 tier-1 files, guard absent.
    #
    # CORE_ALSO (not TOOL_CARVEOUTS) is the deliberate choice: the guard is
    # DB-free on this path (its Postgres legs skip without TEST_RECORD_DB_URI)
    # and `core`-registered, so selecting `core` runs it at the lowest CI cost.
    # Mutation check: removing the CORE_ALSO entry selects no surface, so the
    # asserts below fail.
    r = _sel(["tools/record_wiring_check.py"])
    assert "core" in r["surfaces"], r
    assert "test_9078_record_written_by_production_path.py" in r["test_files"], r
    assert r["full"] is False, r
    assert set(r["test_files"]) != _tier1(), r


def test_9078_guard_runs_on_a_main_only_diff():
    # #9231 review P1: the SAME guard (`test_9078_record_written_by_production_
    # path.py`) also drives `tortoise/__main__.py` — `cli._cmd_demo(None)` — to
    # prove the demo lane files nothing from the journal file it deletes. That
    # path is an EXACT `SOURCE_PATTERNS["api"]` entry, so an `__main__.py`-only
    # diff selects `api`, REPLACES the `core` fallback (`core` is added only in
    # the `if not found:` branch, and `CORE_ALSO` does not list `__main__.py`)
    # and never collected the leg. Since removing `EventLog.scratch(...)` from
    # `_cmd_demo` is NECESSARILY an `__main__.py`-touching diff, the one change
    # that can break that leg merged with the leg never run — the
    # #1349/#2938/#3154/#4367 silent-drop class, the same shape
    # `test_socket_bound_not_multiplied_2969.py` is dual-registered for.
    #
    # Measured before the fix: `_sel(["tortoise/__main__.py"])` ->
    # surfaces=['api'], full=False, guard ABSENT from test_files.
    #
    # Mutation check: deleting the dual `api` arm in config/ci-surfaces.yml
    # fails the first pair of asserts; the `core` arm is checked by
    # `test_record_wiring_check_tool_change_selects_core_not_tier1` above, so
    # neither arm can be dropped believing the other covers it.
    r = _sel(["tortoise/__main__.py"])
    assert r["full"] is False, r
    assert r["surfaces"] == ["api"], r
    assert "test_9078_record_written_by_production_path.py" in r["test_files"], r


def test_9192_guard_runs_on_the_diff_that_can_break_it():
    # #9192 review P1: `tests/test_9192_tenant_armed_by_request.py` is the PR's
    # own proof that a multi-tenant process cannot name its tenant from env. Its
    # REGISTRATION closed `--integrity` (the required `manifest-integrity` job),
    # but a registration is not a SELECTION pin: a guard that is registered and
    # never selected on the diff that can break it certifies nothing (#9196/#9231).
    #
    # It is DUAL-registered for the #9078 reason because the two halves of the
    # fix live under two different selectors. `tortoise/record_writer.py` (the
    # resolver: `record_tenant` / `single_tenant_declared`) matches no
    # SOURCE_PATTERN and falls through to `core`; `tortoise/mcp_auth.py`
    # (`OrgResolutionMiddleware`, the per-request installer) is an EXACT `api`
    # match, and a named-surface match REPLACES the `core` fallback (`core` is
    # added only in the `if not found:` branch; `CORE_ALSO` does not list it).
    #
    # Measured before the registration: `select(["tortoise/record_writer.py"])`
    # -> surfaces=['core'] with the file ABSENT (603 files), and
    # `select(["tortoise/mcp_auth.py"])` -> surfaces=['api'] with it ABSENT (288
    # files).
    #
    # Mutation check: deleting the `core` arm fails the first pair of asserts,
    # deleting the `api` arm fails the second — neither can be dropped believing
    # the other covers it.
    core_sel = _sel(["tortoise/record_writer.py"])
    assert core_sel["full"] is False, core_sel
    assert "core" in core_sel["surfaces"], core_sel
    assert "test_9192_tenant_armed_by_request.py" in core_sel["test_files"], core_sel

    api_sel = _sel(["tortoise/mcp_auth.py"])
    assert api_sel["full"] is False, api_sel
    assert api_sel["surfaces"] == ["api"], api_sel
    assert "test_9192_tenant_armed_by_request.py" in api_sel["test_files"], api_sel


def test_9664_capstone_runs_on_the_middleware_diff_that_can_break_it():
    # #9664 review P2: `tests/test_9664_capstone_connector_to_record.py` is the
    # end-to-end pin of "an authenticated CONNECTOR request reaches the record
    # under the request's OWN tenant". It was registered `core`-only, and
    # `tortoise/mcp_auth.py` — the per-request tenant install it drives, AND the
    # module that produces the 401 body its refusal leg asserts — is an EXACT
    # `SOURCE_PATTERNS["api"]` entry, so a middleware-only diff selects `api` and
    # REPLACES the `core` fallback (`core` is added only in the `if not found:`
    # branch; `CORE_ALSO` lists neither path). Registration alone is not a
    # selection pin: a guard registered and never selected on the diff that can
    # break it certifies nothing (#9196/#9231, the same reasoning as the #9192 pin
    # above).
    #
    # Measured before this entry's `api` arm was added:
    # `select(["tortoise/mcp_auth.py"])` -> surfaces=['api'] with the file ABSENT.
    #
    # Mutation check: deleting the `api` arm fails the second pair of asserts,
    # deleting the `core` arm fails the first — neither can be dropped believing
    # the other covers it.
    core_sel = _sel(["tortoise/record_writer.py"])
    assert core_sel["full"] is False, core_sel
    assert "core" in core_sel["surfaces"], core_sel
    assert "test_9664_capstone_connector_to_record.py" in core_sel["test_files"], core_sel

    for middleware_diff in ("tortoise/mcp_auth.py", "tortoise/supabase_control.py"):
        api_sel = _sel([middleware_diff])
        assert api_sel["full"] is False, api_sel
        assert api_sel["surfaces"] == ["api"], (middleware_diff, api_sel)
        assert "test_9664_capstone_connector_to_record.py" in api_sel["test_files"], (
            middleware_diff,
            "the capstone would not run on the diff class that can break its "
            "tenant install or its refusal text",
            api_sel)


def test_source_currency_tool_change_selects_core_not_tier1():
    # #5581: tools/source_currency.py owns tests/test_source_currency_5581.py
    # (`core`) — the §4.6 aggregate, the sourced disclosure, and the flag-gated
    # reporting of both on the search row. Without the CORE_ALSO entry the flat
    # "tools/" NON_PYTHON_PREFIXES entry swallowed the path, so a helper-only
    # change selected NO surface and fell back to tier-1 smoke: the cases
    # pinning that an EMPTY link set reads `unknown` rather than the vacuously
    # `current` a literal §4.6 reading produces would not have run on the PR
    # that edits the aggregation. Same silent-drop class as #5084/#6138/#4174
    # above.
    r = _sel(["tools/source_currency.py"])
    assert r["full"] is False, r
    assert "core" in r["surfaces"], r
    assert "test_source_currency_5581.py" in r["test_files"], r
    assert set(r["test_files"]) != _tier1()


def test_link_agent_infra_scripts_tool_change_selects_core_not_tier1():
    # PR #9541 review P1: tools/link_agent_infra_scripts.py is the only executor
    # of #9475's fix (it decides whether the repo-root `scripts` pointer exists at
    # all), and its guard tests/test_link_agent_infra_scripts.py is `core`-
    # registered. Without the CORE_ALSO entry the flat "tools/"
    # NON_PYTHON_PREFIXES entry swallowed the path, so a MATERIALISER-only change
    # selected NO surface and took the docs-only early return — the 14 cases
    # pinning that the pointer stays gitignored, that a dangling link is repaired,
    # that a RELATIVE --infra still writes a RESOLVING target (the #9475 symptom
    # reinstated by its own fix) and that an explicit --infra is never silently
    # discarded would not have run on the one PR that can break them. Measured
    # before the entry: `--changed-files tools/link_agent_infra_scripts.py
    # --event pull_request` -> surfaces=[], full=False, guard ABSENT — while
    # `--integrity` stayed green, because the TEST file is classified. Same
    # #1349/#3332/#3616 silent-drop class as #5084/#6138/#4174 above.
    # Mutation check: removing the CORE_ALSO entry fails the asserts below.
    r = _sel(["tools/link_agent_infra_scripts.py"])
    assert r["full"] is False, r
    assert "core" in r["surfaces"], r
    assert "test_link_agent_infra_scripts.py" in r["test_files"], r
    assert set(r["test_files"]) != _tier1()


def test_agent_infra_drift_check_tool_change_selects_core_not_tier1():
    # PR #9541 review (round 6 P1): tools/agent_infra_drift_check.py is the single
    # quote-free command ci.yml's drift-check job passes as `check-command` — it
    # materialises the machine-local `scripts` pointer and then runs agent-infra's
    # checker. If its quoting breaks, the reusable workflow's textual `${{ }}`
    # interpolation makes the step die at PARSE time and the gate reds forever with
    # "produced NO result" while verifying nothing; if its exit contract breaks, the
    # gate can read green over a pointer that was never written. Same silent-drop
    # seam as the materialiser entry above: `tools/` is swallowed by
    # NON_PYTHON_PREFIXES, so a WRAPPER-ONLY change selected NO surface and took the
    # docs-only early return, and the cases pinning it would not run on the one PR
    # that can break them — while `--integrity` stayed green, because the TEST file
    # is classified.
    # Mutation check: removing the CORE_ALSO entry fails the asserts below.
    r = _sel(["tools/agent_infra_drift_check.py"])
    assert r["full"] is False, r
    assert "core" in r["surfaces"], r
    assert "test_link_agent_infra_scripts.py" in r["test_files"], r
    assert set(r["test_files"]) != _tier1()


def test_cost_coverage_audit_tool_change_selects_core_not_tier1():
    # #5045 review P1: tools/cost_coverage_audit.py IS the instrument the
    # cost-dimension registry rests on, and tests/test_cost_dimensions.py is the
    # only thing that pins it (`core` + `api` registered). Without the CORE_ALSO
    # entry the flat "tools/" NON_PYTHON_PREFIXES entry swallowed the path, so a
    # tool-only change selected NO surface and fell back to tier-1 smoke — whose
    # 46-file set does not contain the pin. Measured before the entry:
    # `select(["tools/cost_coverage_audit.py"])` -> surfaces=[], full=False,
    # test_cost_dimensions.py absent, so DELETING a check from `audit()` (or
    # narrowing `_NOT_PRODUCTION`, or restoring a predicate the module argues
    # against) merged green: the registry quietly stopped being verified on the
    # one PR class that can weaken it. Same #1349/#3332/#3616 silent-drop class
    # as tools/drift-guard.py and tools/test_lane.py above.
    # Mutation check: removing the CORE_ALSO entry fails the asserts below.
    r = _sel(["tools/cost_coverage_audit.py"])
    assert r["full"] is False, r
    assert "core" in r["surfaces"], r
    assert "test_cost_dimensions.py" in r["test_files"], r
    assert set(r["test_files"]) != _tier1()


def test_cost_dimension_ledger_migrations_select_core():
    # #5045 review P1 (the ledger half): `check_ledger` verifies the declared
    # columns and SQL functions against the migration TEXT, and `supabase/` is a
    # flat NON_PYTHON_PREFIXES entry — so a migration-only change that renames a
    # metering column or an RPC selected NO surface and the ledger check never ran
    # on the file carrying the DDL it verifies (the #8744 shape). The doc that
    # states what the audit enforces is the same kind of subject: a docs-only edit
    # of it selected nothing, so its claims could drift from the audit's code with
    # every pin green.
    #
    # Mutation check: removing any of these CORE_ALSO entries fails its arm.
    root = Path(__file__).resolve().parents[1]
    for changed in (
        "supabase/migrations/0014_metering_records.sql",
        "supabase/migrations/20260813000002_metering_nodes_written.sql",
        "supabase/migrations/20260829000001_metering_ask_columns.sql",
        "supabase/migrations/20260915000001_tenancy_team_to_org.sql",
        "supabase/migrations/20260917000001_metering_capture_cost.sql",
        "supabase/migrations/20260918000001_metering_period_window.sql",
        "supabase/migrations/20260927000001_metering_unmetered_increments.sql",
        "supabase/migrations/20261001000001_metering_capture_tokens.sql",
        "supabase/migrations/20261001000004_metering_embedding_columns.sql",
        "supabase/migrations/20261001000005_metering_graph_storage.sql",
        "supabase/migrations/20261010000001_metering_period_end_refresh.sql",
        "supabase/migrations/20261013000001_metering_retrieval_columns.sql",
        "docs/ops/cost-dimensions.md",
    ):
        assert (root / changed).exists(), f"guarded ledger subject is gone: {changed}"
        r = _sel([changed])
        assert r["full"] is False, (changed, r)
        assert "core" in r["surfaces"], (changed, r)
        assert "test_cost_dimensions.py" in r["test_files"], (
            f"a change to {changed} does not run tests/test_cost_dimensions.py, so "
            "the ledger gate cannot fire on the DDL it verifies (#5045 review)"
        )


def test_test_lane_tool_change_selects_core_not_tier1():
    # #5084 review P1: tools/test_lane.py owns tests/test_test_lane_tool.py
    # (`core`). Without the CORE_ALSO entry the flat "tools/"
    # NON_PYTHON_PREFIXES entry swallowed the path, so a tool-only change
    # selected NO surface and fell back to tier-1 smoke — the tests that pin
    # the tool's fail-closed ownership guard (never remove a shared instance;
    # never remove a peer lane's container) would not run on the PR editing
    # that guard. Same silent-drop class as #4069/#6138/#4174 above.
    r = _sel(["tools/test_lane.py"])
    assert r["full"] is False, r
    assert "core" in r["surfaces"], r
    assert "test_test_lane_tool.py" in r["test_files"], r
    assert set(r["test_files"]) != _tier1()


def test_queue_conflict_census_tool_change_selects_core_not_tier1():
    # #6138 review P1: tools/queue_conflict_census.py owns
    # tests/test_queue_conflict_census.py (`core`). Without the CORE_ALSO entry
    # the flat "tools/" prefix swallowed the path, so a census-only change
    # selected no surface and fell back to tier-1 smoke — the guard never ran
    # on the PR that edits the census, which is how a measurement instrument
    # regresses silently. Same shape as #4069 above.
    r = _sel(["tools/queue_conflict_census.py"])
    assert r["full"] is False, r
    assert "core" in r["surfaces"], r
    assert "test_queue_conflict_census.py" in r["test_files"], r
    assert set(r["test_files"]) != _tier1()


def test_drift_guard_tool_change_selects_core_not_tier1():
    # #4174 review P1: tools/drift-guard.py owns tests/test_drift_guard.py. The
    # flat "tools/" NON_PYTHON_PREFIXES entry swallowed the path, so a
    # guard-only change selected NO surface and fell back to tier-1 smoke — the
    # suite pinning the guard never ran on the PR that changed the guard. Same
    # silent-drop class as #4069 above, and the same defect #4174 describes a
    # gate having. Mutation check: removing the CORE_ALSO entry filters the path
    # out (docs-only early return → empty surfaces, tier-1 smoke) and fails
    # every assert below.
    r = _sel(["tools/drift-guard.py"])
    assert r["full"] is False, r
    assert "core" in r["surfaces"], r
    assert "test_drift_guard.py" in r["test_files"], r
    assert set(r["test_files"]) != _tier1()


def test_surface_tools_change_selects_core_not_tier1():
    # PR #8986 review (P1/P2, four independent reviews): the lockstep net for
    # the served-identity helpers is
    # tests/test_surface_manifest.py::test_the_two_drivers_agree_on_the_served_identity_helpers
    # — *"a change applied to ONE copy reds there"*. That suite is
    # `core`-registered AND a `slow_files` member, so it only runs on the
    # `test-slow` leg; `tools/` is a flat NON_PYTHON_PREFIXES entry matching no
    # SOURCE_PATTERNS row, so before the CORE_ALSO entries a driver-only diff
    # came back with `changed == []`, took the docs-only early return and
    # returned `surfaces=[]`, `slow_run=False` — the net was ASLEEP on the exact
    # diff class it exists to catch (the #1349/#3332/#3616 silent-drop class).
    # The required `surface-guard` job cannot backstop it: it overwrites
    # `tools/surface-guard.py` with the base ref's copy before running, so a
    # PR-side edit to that copy is never measured.
    # Mutation check: removing either CORE_ALSO entry fails every assert below
    # (measured before the entries: surfaces [], full False, 46 tier-1 files,
    # `test_surface_manifest.py` absent from `slow_selected`).
    for changed in ("tools/surface-guard.py", "tools/surface_manifest.py"):
        r = _sel([changed])
        assert r["full"] is False, (changed, r)
        assert r["surfaces"] == ["core"], (changed, r)
        # The parity suite is a slow_files member, so the assertion that matters
        # is the SLOW leg (`slow_run` is what un-skips the `test-slow` job).
        assert r["slow_run"] is True, (changed, r)
        assert "test_surface_manifest.py" in r["slow_selected"], (changed, r)
        # …and the sibling pin is registered in the fast `core` set.
        assert "test_surface_guard_pin_4606.py" in r["test_files"], (changed, r)
        assert set(r["test_files"]) != _tier1(), (changed, r)


def test_collision_preflight_tool_change_fails_closed_to_full():
    # #3261: tools/collision_preflight.py owns tests/test_collision_preflight.py.
    # Before its TOOL_CARVEOUTS entry the flat "tools/" prefix swallowed the
    # path: `changed` came back empty, so select() took the docs-only return
    # (surfaces=[], tier-1 smoke only) and the tool's own guard test never ran
    # on the PR that changed the tool. The early docs-only return bypasses the
    # `if not matched: matched.add("core")` fallback, so the pre-#3261 note
    # claiming such a change "falls back to core" was never true.
    # No SOURCE_PATTERNS entry matches the path, so it takes the unknown-path
    # branch -> full matrix (fail closed), exactly like tools/ci_selection.py.
    r = _sel(["tools/collision_preflight.py"])
    assert r["full"] is True
    assert r["test_files"] == "ALL"
    assert "core" in r["surfaces"]


def test_run_with_eval_keys_tool_change_fails_closed_to_full():
    # #2718/#4860: tools/run-with-eval-keys.sh owns
    # tests/test_run_with_eval_keys.py. Same silent-drop class as the
    # collision-preflight carve-out above: the flat "tools/"
    # NON_PYTHON_PREFIXES entry swallows a `.sh` path, so without a
    # TOOL_CARVEOUTS entry `changed` is empty and select() takes the docs-only
    # return (surfaces=[], tier-1 smoke only) — a wrapper-only change (a new
    # managed key, a fingerprint-format edit) would ship without its guard
    # suite ever running. No SOURCE_PATTERNS entry matches a `.sh` path, so it
    # lands in the unknown-path fail-closed branch -> FULL matrix + both legs.
    r = _sel(["tools/run-with-eval-keys.sh"])
    assert r["full"] is True
    assert r["test_files"] == "ALL"
    assert "core" in r["surfaces"]


def test_ci_manifest_tool_change_fails_closed_to_full():
    # #5050: tools/ci_manifest.py owns tests/test_ci_manifest.py. Same
    # silent-drop class as the collision-preflight carve-out above — the flat
    # "tools/" NON_PYTHON_PREFIXES entry swallows a tool-only change, so
    # without a TOOL_CARVEOUTS entry `changed` is empty and the docs-only return
    # runs tier-1 smoke only: the validator's own verdict-boundary suite would
    # never run on the PR that changes the validator. No SOURCE_PATTERNS entry
    # matches, so it lands in the unknown-path fail-closed branch -> FULL.
    r = _sel(["tools/ci_manifest.py"])
    assert r["full"] is True
    assert r["test_files"] == "ALL"
    assert "core" in r["surfaces"]


def test_ci_timing_tool_change_fails_closed_to_full():
    # #5050: tools/ci_timing.py is the SOLE writer of the measured `durations`
    # map, and the validator's own suites pin the writer's output (the
    # `VALUE_FLOOR == ci_timing.DURATIONS_VALUE_FLOOR_S` pin and the one-decimal
    # render pin in tests/test_ci_manifest.py). Same silent-drop class as the
    # ci_manifest carve-out above — the flat "tools/" prefix swallows a
    # writer-only change, `changed` is empty and select() takes the docs-only
    # return, so neither tests/test_ci_timing.py nor tests/test_ci_manifest.py
    # would run on the PR that changes the writer. No SOURCE_PATTERNS entry
    # matches, so it lands in the unknown-path branch -> FULL matrix + both legs.
    r = _sel(["tools/ci_timing.py"])
    assert r["full"] is True
    assert r["test_files"] == "ALL"
    assert "core" in r["surfaces"]


def test_ci_check_repair_tool_change_fails_closed_to_full():
    # #8375 item 1: tools/ci_check_repair.py CLOSES and REOPENS pull requests, and
    # re-runs workflow runs, on a schedule and unattended (the workflow and the
    # launchd fallback both call it with `--apply`). Its suite lives in
    # tests/test_ci_check_repair.py and is registered in the `ci-surfaces.yml`
    # `core` surface — which covers edits of the TEST file, NOT of the tool. No
    # SOURCE_PATTERNS entry matches the tool, so without the TOOL_CARVEOUTS entry
    # the flat `tools/` prefix swallows it, `changed` comes back empty and select()
    # takes the docs-only early return (surfaces=[], tier-1 smoke) — an early
    # return that BYPASSES the `if not matched: matched.add("core")` fallback, so
    # NOT ONE of the 74 guards would run on the PR that changes the tool. This was
    # measured on the real path before the carve-out:
    #   --changed-files tools/ci_check_repair.py -> surfaces=[] full=False
    # `tools/ci_selection.py --integrity` cannot catch it: it only asks whether the
    # test file is CLASSIFIED. ALL FOUR paths are asserted because all four decide
    # whether the repair runs and what it does: the entry point (the macOS
    # `bash -s`/TCC handoff), the local fallback, and the installer whose plist
    # decides whether the job is ARMED. Mutation check: removing any entry filters
    # that path out (docs-only early return) and fails the asserts below.
    for path in ("tools/ci_check_repair.py", "tools/ci_check_repair_launchd.py",
                 "tools/ci-check-repair-local.sh",
                 "tools/install-ci-check-repair-schedule.sh"):
        r = _sel([path])
        assert r["full"] is True, (path, r)
        assert r["test_files"] == "ALL", (path, r)
        assert "core" in r["surfaces"], (path, r)


def test_pr_lead_time_tool_change_fails_closed_to_full():
    # #6139: tools/pr_lead_time.py owns tests/test_pr_lead_time.py, whose guards
    # pin the decomposition's residence rule (a zero-length Mergify run is a
    # queue EVALUATION, not residence), its gate clamp, and its exit-code
    # contract (an unobserved read is UNKNOWN, never 0). Same silent-drop class
    # as the carve-outs above: the flat "tools/" NON_PYTHON_PREFIXES entry
    # swallows a tool-only change, so without a TOOL_CARVEOUTS entry `changed`
    # is empty and select() takes the docs-only return (surfaces=[], tier-1
    # smoke only) — and that early return bypasses the
    # `if not matched: matched.add("core")` fallback, so the guard suite would
    # never run on the PR that changes the tool. Registering the TEST under
    # `core` in ci-surfaces.yml does NOT cover this: it applies to edits of the
    # test file, not of the tool. No SOURCE_PATTERNS entry matches the path, so
    # it lands in the unknown-path fail-closed branch -> FULL matrix + both
    # legs. Mutation check: removing the TOOL_CARVEOUTS entry filters the path
    # out (docs-only early return), failing all three asserts below.
    r = _sel(["tools/pr_lead_time.py"])
    assert r["full"] is True
    assert r["test_files"] == "ALL"
    assert "core" in r["surfaces"]


def test_finding_provenance_tool_change_fails_closed_to_full():
    # #4290: tools/finding_provenance.py owns tests/test_finding_provenance.py.
    # Same silent-drop class as the collision-preflight carve-out above — the
    # flat "tools/" NON_PYTHON_PREFIXES entry swallows a tool-only change, so
    # without a TOOL_CARVEOUTS entry `changed` is empty and the docs-only
    # return runs tier-1 smoke only: the gate's own falsification suite would
    # never run on the PR that changes the gate. No SOURCE_PATTERNS entry
    # matches, so it lands in the unknown-path fail-closed branch -> FULL.
    r = _sel(["tools/finding_provenance.py"])
    assert r["full"] is True
    assert r["test_files"] == "ALL"
    assert "core" in r["surfaces"]


def test_edge_census_tool_change_fails_closed_to_full():
    # #4503: tools/edge_census.py owns
    # tests/test_4503_edge_relationship_accounting.py. Same silent-drop class
    # as the carve-outs above — and it was observed live, not predicted: before
    # the TOOL_CARVEOUTS entry, `--changed-files tools/edge_census.py` returned
    # `"surfaces": []`, i.e. the docs-only return, so the census/probe's own
    # guard tests would never have run on the PR that changes the instrument.
    # The instrument exists to make an uncounted class visible; being itself
    # invisible to CI selection would be the same defect one level up. No
    # SOURCE_PATTERNS entry matches the path, so it lands in the unknown-path
    # branch -> FULL matrix (fail closed).
    r = _sel(["tools/edge_census.py"])
    assert r["full"] is True
    assert r["test_files"] == "ALL"
    assert "core" in r["surfaces"]


def test_embedded_evidence_tool_change_fails_closed_to_full():
    # #3827: tools/embedded_evidence.py owns tests/test_embedded_evidence.py.
    # Same silent-drop class as the preflight carve-out above: the flat "tools/"
    # prefix swallowed the path, `changed` came back empty, and select() took the
    # docs-only return (surfaces=[], tier-1 smoke only) — so the harness's own
    # guard test never ran on the PR that changed the harness. That is precisely
    # the "proxy silent in the case it exists to cover" class the harness is
    # written to detect, and it applied to the harness itself.
    # No SOURCE_PATTERNS entry matches the path, so it takes the unknown-path
    # branch -> full matrix (fail closed), like tools/collision_preflight.py.
    r = _sel(["tools/embedded_evidence.py"])
    assert r["full"] is True
    assert r["test_files"] == "ALL"
    assert "core" in r["surfaces"]


def test_who_is_on_tool_change_fails_closed_to_full():
    # #4256: tools/who_is_on.py (+ its tools/who-is-on.sh wrapper) owns
    # tests/test_who_is_on.py. Same #3261 silent-drop class as
    # tools/collision_preflight.py above: without the TOOL_CARVEOUTS entries the
    # flat "tools/" prefix swallows the path, `changed` comes back empty,
    # select() takes the docs-only return (surfaces=[], tier-1 smoke) and the
    # tool's own guard test never runs on the PR that changes it.
    for path in ("tools/who_is_on.py", "tools/who-is-on.sh"):
        r = _sel([path])
        assert r["full"] is True, path
        assert r["test_files"] == "ALL", path
        assert "core" in r["surfaces"], path


def test_session_retrofit_audit_tool_change_fails_closed_to_full():
    # #3554: tools/session_retrofit_audit.py owns
    # tests/test_session_schema_3554.py, whose guards pin the :Session writer
    # inventory predicate (it must be CLEAN on the tree it ships with) and the
    # D9 retrofit CLI. Same silent-drop class as the carve-outs above: the flat
    # "tools/" NON_PYTHON_PREFIXES entry swallows a tool-only change, so
    # without a TOOL_CARVEOUTS entry `changed` is empty and select() takes the
    # docs-only return (surfaces=[], tier-1 smoke only) — and that early return
    # bypasses the `if not matched: matched.add("core")` fallback, so the guard
    # suite would never run on the PR that changes the instrument. Registering
    # the TEST under `core` in ci-surfaces.yml does NOT cover this: it applies
    # to edits of the test file, not of the tool. No SOURCE_PATTERNS entry
    # matches the path, so it lands in the unknown-path fail-closed branch ->
    # FULL matrix + both legs. Mutation check: removing the TOOL_CARVEOUTS
    # entry filters the path out (docs-only early return), failing all three
    # asserts below.
    r = _sel(["tools/session_retrofit_audit.py"])
    assert r["full"] is True
    assert r["test_files"] == "ALL"
    assert "core" in r["surfaces"]


def test_work_claim_tool_change_fails_closed_to_full():
    # #5052/#9487 review P1: tools/work_claim.py owns tests/test_work_claim.py — the
    # dispatch-time claim gate, whose 87 hermetic arms pin every fail-closed refusal the
    # tool exists for. Same #3261/#4256 silent-drop class as the collision_preflight and
    # who_is_on carve-outs above: `tools/` is a flat NON_PYTHON_PREFIXES entry, so without
    # this TOOL_CARVEOUTS entry a claim-tool-only change came back as `changed=[]`,
    # select() took the docs-only early return (surfaces=[], tier-1 smoke only) — which
    # BYPASSES the `if not matched: matched.add("core")` fallback — so none of the arms
    # ran on the file most likely to weaken the gate. Registering
    # tests/test_work_claim.py under `core` in ci-surfaces.yml does NOT cover this: it
    # applies to edits of the TEST file, not of the tool (the ci-surfaces.yml comment said
    # the opposite and is corrected in the same change).
    # Mutation check: removing the TOOL_CARVEOUTS entry filters the path out (docs-only
    # early return), failing all three asserts below.
    r = _sel(["tools/work_claim.py"])
    assert r["full"] is True, r
    assert r["test_files"] == "ALL", r
    assert "core" in r["surfaces"], r
    # ... and the carve-out is a PREFIX, not one exact filename (review of #9487 P2). The
    # exact-path entry failed closed for `tools/work_claim.py` alone: a rename, or the store
    # split into `tools/work_claim_store.py`, re-opened the same silent drop with nothing
    # failing (measured: `tools/work_claim_extra.py` → surfaces=[], full=False, tier-1).
    # The store seam is the half whose breakage must never ship unreviewed.
    for sibling in ("tools/work_claim_store.py", "tools/work_claim_extra.py"):
        r = _sel([sibling])
        assert r["full"] is True, (sibling, r)
        assert r["test_files"] == "ALL", (sibling, r)
        assert "core" in r["surfaces"], (sibling, r)


def test_session_schema_guard_runs_on_every_declared_writer_surface():
    # #3554: the declared :Session writer inventory SPANS surfaces —
    # tools/longmem_eval/ingest.py and ingest_v2.py select `eval`,
    # tools/ask_spotcheck.py selects `sdk`, tortoise/hosted_api.py selects
    # `api`, and tortoise/session_schema.py (the declaration itself) falls to
    # the `core` fallback — so the drift guard
    # (tests/test_session_schema_3554.py) must be registered on each surface
    # its subjects select. A named-surface match REPLACES the `core` fallback
    # (#2938), so a core+api-only registration silently drops the guard on an
    # eval- or sdk-only change. Mutation check: removing ANY ci-surfaces.yml
    # arm fails the corresponding assert below — the `api` and `core` arms were
    # unpinned (the whole suite stayed green with either removed), so they are
    # in this list too.
    for path in ("tools/longmem_eval/ingest.py",
                 "tools/longmem_eval/ingest_v2.py",
                 "tools/ask_spotcheck.py",
                 "tortoise/hosted_api.py",
                 "tortoise/session_schema.py"):
        r = _sel([path])
        assert "test_session_schema_3554.py" in r["test_files"], path


def test_backfill_script_only_change_selects_eval():
    # graph-scripts/backfill_embeddings.py is a SOURCE_PATTERNS["eval"]
    # path — a backfill-only PR selects the eval surface (its test,
    # test_backfill_embeddings_force.py, is registered there by T12/PR2)
    r = _sel(["graph-scripts/backfill_embeddings.py"])
    assert r["full"] is False
    assert "eval" in r["surfaces"]
    assert "test_pair_label_runner.py" in r["test_files"]
def test_slow_files_never_in_fast_gate_selections():
    # #1371: tier-1 and tier-2 selections must never contain a slow file
    # (they run only in the test-slow job).
    m = load_manifest()
    slow = set(m["slow_files"])
    assert slow, "slow_files must be non-empty"
    assert not (set(m["tier1"]) & slow), "tier1 leaks a slow file"

    docs = _sel(["docs/README.md", "website/apps/dashboard/public/welcome.html"])
    assert not (set(docs["test_files"]) & slow), "docs-only tier-1 leaks slow files"

    core = _sel(["tortoise/graph.py", "tortoise/ingest.py"])
    assert not (set(core["test_files"]) & slow), "tier-2 core leaks slow files"

    ep = _sel(["tortoise/ranking.py", "tortoise/analyze.py"])
    assert not (set(ep["test_files"]) & slow), "tier-2 ep leaks slow files"


def test_every_slow_file_a_second_surface_names_runs_for_that_surface():
    """#6234: a `slow_files` entry named by a SECOND surface must still run on
    that second surface's diff.

    `classify_test_file` is FIRST-match, and `select()` keys the tier-2 slow
    leg set on that same first match while subtracting EVERY `slow_files` entry
    from the fast pool (`files -= slow`). So a slow file that a second surface
    also names is dropped from the fast set and never re-added to
    `slow_selected` when the second surface is the matched one — it runs on
    NEITHER leg. The leg still reports green because the second surface usually
    owns other slow files, so `slow_run` stays True: the coverage hole is
    invisible to the drift guard, whose union check is about the committed legs
    and not about which tier-2 diff reaches them.

    The repo already excludes the two known instances
    (`test_capture_install.py` / `test_session_verify.py`) and, after #6234,
    eight more. The property is driven from the manifest — for EVERY slow file
    a second surface names, not a frozen list — so relocating a
    dual-registered file back into `slow_files` reds here with its name.

    A selection that matches the second surface is the MINIMAL case: a diff
    that also matches another surface owning the file would run it, so if the
    minimal one does not, the hole is real. `uri_requiring` files are exempt:
    they are deliberately kept out of every URI-less tier-2 leg (see
    `slow_leg_by_surface`), a separate documented exclusion.
    """
    m = load_manifest()
    slow = set(m["slow_files"])
    carve = set(m["carve_out"])
    uri = uri_requiring_files(m)
    surfaces = m["surfaces"]

    # A path whose diff selects `s` on its own. SOURCE_PATTERNS is the manifest's
    # source->surface map; `core` and `classify` have NO entry (they are only
    # reachable as the fallback / via a test-file change), so for those we use a
    # registered test file that CLASSIFIES as the surface — a test-file change
    # selects its classifying surface, which is the same path the hole is on.
    def _probe(s: str) -> str | None:
        pats = SOURCE_PATTERNS.get(s)
        if pats:
            return pats[0]
        for member in surfaces.get(s) or []:
            if member in slow or member in carve:
                continue
            if classify_test_file(member, m) == s:
                return "tests/" + member
        return None

    offenders: list[str] = []
    for f in sorted(slow - carve - uri):
        owner = classify_test_file(f, m)
        base = f.rsplit("/", 1)[-1]
        offenders_for_f: list[str] = []
        for s, files in surfaces.items():
            if s == owner:
                continue
            if f not in (files or []) and base not in (files or []):
                continue
            probe = _probe(s)
            assert probe is not None, (
                f"no probe diff selects surface {s!r} for slow file {f}; the "
                "probe derivation needs a SOURCE_PATTERNS entry or a "
                "surface-classifying test file")
            sel = select([probe], "pull_request", m)
            assert s in sel["surfaces"], (
                f"probe {probe!r} no longer selects surface {s!r} (got "
                f"{sel['surfaces']}) — update `_probe` in this test")
            ran = set(sel["test_files"]) if sel["test_files"] != "ALL" else set()
            ran |= set(sel["slow_selected"])
            if f not in ran:
                offenders_for_f.append(
                    f"{f} (owner {owner!r}, reached by a {s!r} diff)")
        offenders.extend(offenders_for_f)

    assert not offenders, (
        "slow_files entries a SECOND surface names that the second surface's "
        "diff would run on NEITHER leg: `classify_test_file` is first-match, so "
        "the file is subtracted from the fast pool and never re-added to "
        "`slow_selected`, while the second surface usually keeps `slow_run` "
        "True so the leg still reports green. Keep them OUT of `slow_files` "
        "(the test_capture_install.py / test_session_verify.py precedent):\n  "
        + "\n  ".join(sorted(set(offenders)))
    )


def test_expensive_eval_integration_is_in_the_on_demand_lane():
    """The suite's single most expensive file is OFF the merge gate.

    SUPERSEDED PIN, deliberately repointed rather than deleted. The previous
    version of this test (#4711/#4712) asserted the file must stay in
    `slow_files`. That pin was created for a real reason and its docstring says
    which: the file had been in the FAST lane, cost a MEASURED 1465.9s = 24.4
    min, put half_a at 2956.5s (49.3 min) against a 55-minute watchdog with only
    5.7 minutes of headroom, and moving it to slow_files restored a 1.05:1 split.
    The decision was about WHERE the 24.4 minutes sat so the watchdog was not
    blown — never about whether it had to gate a merge.

    The `on_demand:` lane satisfies that intent strictly better: the 24.4
    minutes leave the pre-merge gate entirely, so no fast or slow leg can be
    blown by it at all. The pin is repointed, not removed, because the reason it
    exists — "this file is enormous and must not sit in a fast lane" — is still
    true and still needs to be loud.

    The old assertion `f in m["slow_files"]` would now be wrong in the opposite
    direction, and its sibling `test_slow_files_never_in_fast_gate_selections`
    cannot catch a revert that ALSO drops the slow_files entry, which is exactly
    what happened here. So this names the file.
    """
    m = load_manifest()
    f = "eval/retrieval/test_integration.py"
    assert f in on_demand_files(m), (
        f"{f} is 24.4 min measured (1.71x its stale declared 855.2s) — it must "
        "stay in the on_demand lane, or it is back on the merge gate (#4711)")
    # Stronger than the pin it replaces: not merely absent from the fast gate
    # for three sample diffs, but absent from EVERY pre-merge leg for ALL of
    # them, because the exclusion is structural (fast_pool filters the lane).
    lanes = push_legs(m)
    bare = f[:-3]
    # #6135: EVERY fast shard is a pre-merge leg, not just a/b.
    for leg in (*(s["files"] for s in lanes["shards"]),
                lanes["slow"], lanes["carve_out"], lanes["env_broken"]):
        assert bare not in leg, (
            f"{f} leaked into {leg!r} — the lane must exclude it from every "
            f"pre-merge leg, not just the fast ones")
    for changed in (["tortoise/graph.py"], ["docs/README.md"],
                    ["tortoise/ranking.py"], ["tortoise/search_engine.py"]):
        assert f not in set(_sel(changed)["test_files"]), (
            f"{f} leaked into the fast gate for {changed}")
    # The cost must stay DECLARED. It no longer re-packs the file anywhere
    # (the lane is subtracted before any packing), but the value is what makes
    # the lane's justification checkable, and dropping it would quietly turn
    # "we measured 24.4 min" into folklore.
    assert f in m["durations"], (
        f"{f} must keep its measured duration — it is the entire justification "
        "for the lane existing")


def test_slow_files_emitted_on_every_return_path():
    # #1371: the changes job reads slow_files from every selection mode — a
    # missing key would KeyError the nightly/schedule run or empty test-slow.
    # #10194: the emitted set is the GATING pool (`slow_gating_files()`: slow
    # minus carve-out minus the non-gating lane), because the changes job feeds
    # `slow_matrix`/the legs straight out of it. Asserting the raw config list
    # would pin the pool that the legs deliberately no longer run.
    from tools.ci_selection import slow_gating_files
    m = load_manifest()
    expected = set(slow_gating_files(m))
    assert expected, "the gating slow pool must not be empty — vacuous otherwise"
    for changed, event in [
        ([], "push"),
        ([], "schedule"),
        (["docs/README.md"], "pull_request"),
        (["tortoise/sdk.py"], "pull_request"),
        (["mystery-dir/x.py"], "pull_request"),
        (["tortoise/ranking.py"], "pull_request"),
    ]:
        r = select(changed, event, m)
        assert "slow_files" in r, f"missing slow_files for {changed}/{event}"
        assert set(r["slow_files"]) == expected, f"bad slow_files for {changed}/{event}"


def test_diff_gate_keys_emitted_on_every_return_path():
    """#2147/#2148 (2026-09-02-ci-audit F1/F2): every selection mode carries
    slow_run / slow_selected / carve_out_run — the changes job echoes them,
    so a missing key would KeyError the echo (or the nightly run) and
    silently mis-gate a leg. slow_selected must always be a subset of
    slow_gating_files() (test-slow's leg set: slow minus carve-out minus the
    non-gating lane, #10194)."""
    from tools.ci_selection import slow_gating_files

    m = load_manifest()
    leg_set = set(slow_gating_files(m))
    for changed, event in [
        ([], "push"),
        ([], "schedule"),
        (["docs/README.md"], "pull_request"),
        (["tortoise/sdk.py"], "pull_request"),
        (["mystery-dir/x.py"], "pull_request"),
        (["tortoise/ranking.py"], "pull_request"),
    ]:
        r = select(changed, event, m)
        for key in ("slow_run", "slow_selected", "carve_out_run"):
            assert key in r, f"missing {key} for {changed}/{event}"
        assert set(r["slow_selected"]) <= leg_set, \
            f"slow_selected must be subset of slow_gating_files() ({changed}/{event})"


def test_docs_only_skips_slow_and_carve_out():
    """#2147/#2148: docs-only PRs touch no slow/carve-out surface —
    both formerly-unconditional legs (the audit's F1/F2 cost drivers) skip;
    the tier-1 smoke still runs in the fast job.

    #3332: a public site surface additionally selects the *fast* onboarding
    surface (see test_public_site_surface_change_selects_onboarding_and_skips_slow),
    but the slow and carve-out legs still skip — which is what this test pins."""
    for changed in (["docs/README.md"],
                    ["docs/README.md", "website/self-hosted.html"]):
        r = _sel(changed)
        assert r["full"] is False
        assert r["slow_run"] is False
        assert r["carve_out_run"] is False
        assert r["slow_selected"] == []


def test_full_selection_runs_both_legs_with_whole_slow_leg_set():
    """#2147/#2148: push/schedule/unknown-path/shared-module -> full:
    test-slow AND test-carve-out run, and slow_selected is the whole
    committed leg set — the trunk + nightly backstop is never weakened by the
    PR-shape gates.

    #10194: the leg set is `slow_gating_files()` (slow - carve-out - the
    non-gating lane). The backstop claim survives the membership change — the
    pool the legs run is still the WHOLE of that pool — but it no longer means
    "every slow_files entry": five eval files stay declared slow and are
    subtracted, exactly the way carve-out files already were."""
    from tools.ci_selection import slow_gating_files
    m = load_manifest()
    leg_set = sorted(slow_gating_files(m))
    assert leg_set, "the full selection's slow leg must not be empty"
    assert len(leg_set) < len(m["slow_files"]), (
        "this test is about the pool the legs actually run; if it equals the "
        "raw config list the subtraction has stopped happening")
    for changed, event in [
        ([], "push"),
        ([], "schedule"),
        (["tortoise/sdk.py"], "pull_request"),
        ([".github/workflows/python-ci.yml"], "pull_request"),
        (["mystery-dir/x.py"], "pull_request"),
    ]:
        r = select(changed, event, m)
        assert r["full"] is True, changed
        assert r["slow_run"] is True and r["carve_out_run"] is True, changed
        assert r["slow_selected"] == leg_set, changed


def test_tier2_slow_run_scoped_to_matched_surfaces():
    """#2148: tier-2 PRs run only their matched surfaces' slow files. ep
    owns test_dream / test_ep_sources / test_source_inheritance_own — a
    ranking.py-only PR selects exactly those (never the full leg set), and
    the carve-out job skips (ep owns no carve-out file). #6137 added
    test_ep_selector to ep's slow set (it moved out of the fast pool), so
    the expected list is pinned here as well."""
    r = _sel(["tortoise/ranking.py"])
    assert r["full"] is False and r["surfaces"] == ["ep"]
    assert r["slow_run"] is True
    assert r["carve_out_run"] is False
    assert r["slow_selected"] == [
        "test_dream.py", "test_ep_selector.py", "test_ep_sources.py",
        "test_source_inheritance_own.py"]


def test_tier2_carve_out_run_when_surface_owns_carve_files():
    """#2147: the carve-out job runs when a matched surface owns carve-out
    files. tortoise/embedded_lifecycle.py maps to core (embedded/daemon/
    registry/guard coverage) -> run; a carve-out TEST-file change selects
    its owning surface -> re-runs here; an onboarding-only change owns no
    carve-out file -> skip."""
    r = _sel(["tortoise/embedded_lifecycle.py"])
    assert r["full"] is False and "core" in r["surfaces"]
    assert r["carve_out_run"] is True
    assert r["slow_run"] is True  # core owns 15 slow-leg files too
    r2 = _sel(["tortoise/onboarding/AGENT_ONBOARDING.md"])
    assert r2["surfaces"] == ["onboarding"]
    assert r2["carve_out_run"] is False and r2["slow_run"] is False
    r3 = _sel(["tests/test_guard.py"])
    assert r3["carve_out_run"] is True and r3["slow_run"] is True


def test_full_mode_selection_unchanged():
    # #1371: push/schedule full mode keeps its exact contract (test_files ALL)
    # and only gains the slow_files key. #10194: the emitted slow pool is the
    # GATING pool, so the count is compared against the same accessor the
    # emitter reads — never the raw config list.
    from tools.ci_selection import slow_gating_files
    r = _sel([], "push")
    assert r["full"] is True
    assert r["test_files"] == "ALL"
    assert len(r["slow_files"]) == len(slow_gating_files(load_manifest()))
    assert len(slow_gating_files(load_manifest())) < len(load_manifest()["slow_files"]), (
        "the gating pool must be a strict subset of the declared slow files, "
        "or #10194's lane subtraction stopped happening")

# ── #1429: auto-registration of unlisted test files ──────────────────────


def _tmp_manifest(tests_dir: Path, extra: str = "") -> Path:
    """Build a minimal manifest YAML with an api surface + tier1."""
    m = tests_dir / "ci-surfaces.yml"
    m.write_text(
        "version: 1" + "\n" +
        "surfaces:" + "\n" +
        "  api:" + "\n" +
        "  - test_existing_api.py" + "\n" +
        "  core:" + "\n" +
        "  - test_existing_core.py" + "\n" +
        extra + "\n" +
        "tier1:" + "\n" +
        "  - test_existing_api.py" + "\n"
    )
    return m


def test_register_adds_unlisted_file_under_surface():
    with tempfile.TemporaryDirectory() as d:
        td = Path(d)
        (td / "test_new_thing.py").write_text("def test_x():\n    pass\n")
        (td / "test_existing_api.py").write_text("def test_x():\n    pass\n")
        (td / "test_existing_core.py").write_text("def test_x():\n    pass\n")
        m = _tmp_manifest(td)
        import yaml
        manifest = yaml.safe_load(m.read_text())
        added = register_tests(m, td, "api", manifest)
        assert added == ["test_new_thing.py"]
        # idempotent
        manifest2 = yaml.safe_load(m.read_text())
        assert register_tests(m, td, "api", manifest2) == []
        # file is registered + manifest still valid
        manifest3 = yaml.safe_load(m.read_text())
        assert "test_new_thing.py" in manifest3["surfaces"]["api"]
        assert classify_test_file("test_new_thing.py", manifest3) == "api"


def test_register_creates_surface_block_if_missing():
    with tempfile.TemporaryDirectory() as d:
        td = Path(d)
        (td / "test_new_sdk.py").write_text("def test_x():\n    pass\n")
        (td / "test_existing_api.py").write_text("def test_x():\n    pass\n")
        m = _tmp_manifest(td)
        import yaml
        manifest = yaml.safe_load(m.read_text())
        added = register_tests(m, td, "sdk", manifest)
        assert added == ["test_new_sdk.py"]
        manifest2 = yaml.safe_load(m.read_text())
        assert "test_new_sdk.py" in manifest2["surfaces"]["sdk"]
        # new surface block landed BEFORE tier1:
        txt = m.read_text()
        assert txt.index("sdk:") < txt.index("tier1:")


def test_register_keeps_alphabetical_order():
    with tempfile.TemporaryDirectory() as d:
        td = Path(d)
        (td / "test_zzz_new.py").write_text("def test_x():\n    pass\n")
        (td / "test_aaa_new.py").write_text("def test_x():\n    pass\n")
        (td / "test_mmm_new.py").write_text("def test_x():\n    pass\n")
        (td / "test_existing_api.py").write_text("def test_x():\n    pass\n")
        m = _tmp_manifest(td)
        import yaml
        manifest = yaml.safe_load(m.read_text())
        register_tests(m, td, "api", manifest)
        manifest2 = yaml.safe_load(m.read_text())
        api = manifest2["surfaces"]["api"]
        assert api == sorted(api), "surface list must stay alphabetized"
        assert api == ["test_aaa_new.py", "test_existing_api.py", "test_mmm_new.py", "test_zzz_new.py"]


def test_register_handles_manifest_without_trailing_newline():
    # #3073 item 1: a manifest whose final entry line lacks a newline
    # concatenated the appended entry onto it (`  - test_b.py  - test_c.py`,
    # malformed YAML). The terminator is normalised before insertion.
    with tempfile.TemporaryDirectory() as d:
        td = Path(d)
        (td / "test_new_thing.py").write_text("def test_x():\n    pass\n")
        (td / "test_existing_api.py").write_text("def test_x():\n    pass\n")
        m = td / "ci-surfaces.yml"
        # the api block is LAST and its final line has NO newline
        m.write_text("surfaces:\n  api:\n  - test_existing_api.py")
        import yaml
        manifest = yaml.safe_load(m.read_text())
        assert register_tests(m, td, "api", manifest) == ["test_new_thing.py"]
        parsed = yaml.safe_load(m.read_text())  # must not raise
        assert parsed["surfaces"]["api"] == ["test_existing_api.py",
                                            "test_new_thing.py"]


def test_classify_and_select_tolerate_null_or_scalar_surface():
    # #3073 item 2: `integrity()`'s duplicate scan tolerated a None surface via
    # `files or ()`, but classification/selection did not — a bare `api:` key
    # (None) or a scalar raised `TypeError: argument of type 'NoneType' is not
    # iterable`, and a string would be iterated character-by-character. A
    # falsy/scalar value means "no members"; the drift gate reports its files.
    for bad in (None, 3, "test_x.py", {"a": 1}):
        m = load_manifest()
        m["surfaces"]["api"] = bad
        assert classify_test_file("test_api.py", m) is None
        r = select(["tortoise/api.py"], "pull_request", m)
        assert r["surfaces"] == ["api", "core"]  # CORE_ALSO still applies


def test_register_default_surface_is_core():
    with tempfile.TemporaryDirectory() as d:
        td = Path(d)
        (td / "test_new_default.py").write_text("def test_x():\n    pass\n")
        (td / "test_existing_api.py").write_text("def test_x():\n    pass\n")
        m = _tmp_manifest(td)
        import yaml
        manifest = yaml.safe_load(m.read_text())
        register_tests(m, td, "core", manifest)
        manifest2 = yaml.safe_load(m.read_text())
        assert "test_new_default.py" in manifest2["surfaces"]["core"]


# ── #2913: comment-safe insertion + duplicate-safe registration ──────────


def test_register_never_splits_a_comment_run():
    """#2913: `pos` indexes entries (comment lines excluded) and must be
    converted to a physical line; treating it as a raw line offset dropped the
    new entry INSIDE a comment run and detached the run from the entries it
    documents."""
    with tempfile.TemporaryDirectory() as d:
        td = Path(d)
        (td / "test_cc.py").write_text("def test_x():\n    pass\n")
        for name in ("test_a.py", "test_b.py", "test_c.py", "test_d.py"):
            (td / name).write_text("def test_x():\n    pass\n")
        (td / "test_existing_core.py").write_text("def test_x():\n    pass\n")
        m = td / "ci-surfaces.yml"
        m.write_text(
            "version: 1" + "\n" +
            "surfaces:" + "\n" +
            "  api:" + "\n" +
            "  - test_a.py" + "\n" +
            "  - test_b.py" + "\n" +
            "  # comment 1 about the group below" + "\n" +
            "  # comment 2 about the group below" + "\n" +
            "  - test_c.py" + "\n" +
            "  - test_d.py" + "\n" +
            "  core:" + "\n" +
            "  - test_existing_core.py" + "\n" +
            "tier1:" + "\n" +
            "  - test_a.py" + "\n"
        )
        import yaml
        manifest = yaml.safe_load(m.read_text())
        assert register_tests(m, td, "api", manifest) == ["test_cc.py"]
        lines = m.read_text().splitlines()
        # the comment run stays contiguous — no entry wedged between its lines
        i = lines.index("  # comment 1 about the group below")
        assert lines[i + 1] == "  # comment 2 about the group below", lines
        # the entry is among real entries and alphabetical order is preserved
        api = yaml.safe_load(m.read_text())["surfaces"]["api"]
        assert api == sorted(api)
        assert api == ["test_a.py", "test_b.py", "test_c.py",
                       "test_cc.py", "test_d.py"]


def test_register_already_present_in_surface_is_reported_noop(capsys):
    """#2913: a file already present in the target surface is a reported no-op.

    The old code did not write a duplicate line either (`to_add` filtered on
    `names`), but it RETURNED `missing`, so `--register` reported an
    already-present file as newly added. This pins the return value and the
    `already registered` report for a caller passing a stale manifest dict —
    the only way to reach that state, since the CLI always loads the manifest
    fresh from disk.
    """
    with tempfile.TemporaryDirectory() as d:
        td = Path(d)
        (td / "test_new_thing.py").write_text("def test_x():\n    pass\n")
        (td / "test_existing_api.py").write_text("def test_x():\n    pass\n")
        (td / "test_existing_core.py").write_text("def test_x():\n    pass\n")
        m = _tmp_manifest(td)
        import yaml
        stale = yaml.safe_load(m.read_text())
        assert register_tests(m, td, "api", stale) == ["test_new_thing.py"]
        assert m.read_text().count("  - test_new_thing.py\n") == 1
        # second call with the pre-registration manifest dict: the entry is
        # already in the surface text, so it must be a reported no-op.
        added = register_tests(m, td, "api", stale)
        assert added == []
        assert m.read_text().count("  - test_new_thing.py\n") == 1, \
            "a second line was written for an already-present entry"
        assert "already registered" in capsys.readouterr().out


def test_duplicate_entries_reports_same_surface_repeats():
    """#2913: a same-surface duplicate is invisible to select() (it unions
    surfaces) and to integrity() (it only asks "classified?") — duplicate_entries()
    surfaces it, and since #5373 it FAILS `--integrity` (a union-merged registry
    would otherwise absorb it silently)."""
    from tools.ci_selection import duplicate_entries
    m = {"surfaces": {"core": ["test_a.py", "test_a.py", "test_b.py"],
                      "api": ["test_a.py"]}}
    # cross-surface (dual) membership is deliberate — only the SAME-surface
    # repeat is reported
    assert duplicate_entries(m) == ["core: test_a.py"]
    assert duplicate_entries({"surfaces": {"core": ["test_a.py", "test_b.py"]}}) == []
    # a name repeated 3x is ONE distinct problem, not two
    assert duplicate_entries(
        {"surfaces": {"core": ["test_a.py", "test_a.py", "test_a.py"]}}
    ) == ["core: test_a.py"]
    # a value that is None (empty surface block) must not raise
    assert duplicate_entries({"surfaces": {"core": None}}) == []


def test_integrity_reddens_on_a_duplicate_entry(monkeypatch, capsys):
    """#5373: a duplicate entry must FAIL `--integrity`, not print a note.

    `config/ci-surfaces.yml` carries `merge=union`, which keeps BOTH sides' lines for
    a conflicting hunk — so a same-surface duplicate is the exact shape union emits
    when two lanes register the same test. It used to be a ⚠️ note; a note on a
    union-merged registry lets the duplicate in silently.
    """
    import tools.ci_selection as cs

    manifest = {"surfaces": {"core": ["test_a.py", "test_a.py"]}}
    monkeypatch.setattr(cs, "load_manifest", lambda: manifest)
    # All the OTHER integrity legs are neutralised so the duplicate is the only
    # possible cause of the non-zero exit; each is a pure function of the manifest.
    for leg in ("integrity", "slow_file_issues", "duration_issues",
                "leg_coverage_issues", "duration_coverage_issues",
                "workflow_matrix_issues"):
        monkeypatch.setattr(cs, leg, lambda *a, **k: [])
    # #6135: `push_legs` now returns `{"shards": [...]}` (N shards), not the old
    # `half_a`/`half_b` pair — the stub below matches the CURRENT contract so the
    # duplicate remains the only possible cause of the non-zero exit.
    monkeypatch.setattr(cs, "push_legs", lambda *a, **k: {"shards": []})
    monkeypatch.setattr(cs, "workflow_halves_issues", lambda *a, **k: [])
    monkeypatch.setattr(cs, "fast_files_absent_from_halves", lambda *a, **k: [])
    monkeypatch.setattr(sys, "argv", ["ci_selection.py", "--integrity"])

    rc = cs.main()
    assert rc == 1, "a duplicate same-surface entry must redden --integrity"
    assert "core: test_a.py" in capsys.readouterr().out


# ── #1266: matrix halves ↔ manifest consistency ──────────────────────────
# The test (a)/(b) halves in python-ci.yml are a second source of truth next
# to config/ci-surfaces.yml. The #1262 integrity gate only covers manifest
# coverage — a slow file can leak into a half (fast-gate leak), a half can
# carry a file that left the manifest (unclassified drift), a half can tilt
# (rebalance drift), and files can silently fall out of BOTH halves (coverage
# hole). These tests pin the fail-closed checks on the actionable classes.

WF_HALVES_FIXTURE = """\
name: Python CI
jobs:
  test:
    strategy:
      matrix:
        half: [a, b]
        include:
          - half: a
            files: >-
              test_api test_auth test_slow_leak test_dead_entry
          - half: b
            files: >-
              test_api test_crypto bench/test_roundrobin
"""


def _halves_manifest(extra_slow: str = "") -> dict:
    return {
        "surfaces": {
            "api": ["test_api.py", "test_auth.py", "test_crypto.py"],
            "core": ["test_slow_leak.py", "test_dead_entry.py"],
        },
        "tier1": ["test_api.py"],
        "slow_files": ["test_slow_leak.py"],
    }


def test_parse_matrix_halves_extracts_both_halves():
    from tools.ci_selection import parse_matrix_halves
    halves = parse_matrix_halves(WF_HALVES_FIXTURE)
    assert set(halves) == {"a", "b"}
    assert halves["a"] == ["test_api", "test_auth", "test_slow_leak", "test_dead_entry"]
    assert halves["b"] == ["test_api", "test_crypto", "bench/test_roundrobin"]


def test_halves_slow_leak_flagged():
    from tools.ci_selection import parse_matrix_halves, workflow_halves_issues
    halves = parse_matrix_halves(WF_HALVES_FIXTURE)
    issues = workflow_halves_issues(_halves_manifest(), halves)
    assert any("test_slow_leak" in i and "leak" in i for i in issues), issues


def test_halves_unclassified_entry_flagged():
    from tools.ci_selection import parse_matrix_halves, workflow_halves_issues
    m = _halves_manifest()
    m["surfaces"]["api"] = ["test_api.py", "test_crypto.py"]  # drop test_auth
    halves = parse_matrix_halves(WF_HALVES_FIXTURE)
    issues = workflow_halves_issues(m, halves)
    assert any("test_auth" in i and "surface" in i for i in issues), issues


def test_halves_duplicate_entry_flagged():
    from tools.ci_selection import workflow_halves_issues
    halves = {"a": ["test_api"], "b": ["test_api", "test_crypto"]}
    issues = workflow_halves_issues(_halves_manifest(), halves)
    assert any("test_api" in i and "MORE THAN ONE shard" in i for i in issues), issues


def test_halves_imbalance_flagged_beyond_tolerance():
    from tools.ci_selection import workflow_halves_issues
    halves = {"a": ["test_api", "test_auth", "test_crypto",
                     "test_slow_leak", "test_dead_entry"],
              "b": ["test_api"]}
    issues = workflow_halves_issues(_halves_manifest(), halves)
    assert any("imbalanced" in i for i in issues), issues


def test_halves_imbalance_within_tolerance_clean():
    from tools.ci_selection import workflow_halves_issues
    halves = {"a": ["test_api", "test_auth"], "b": ["test_crypto", "test_api"]}
    issues = workflow_halves_issues(_halves_manifest(), halves)
    assert not any("imbalanced" in i for i in issues), issues


def test_halves_ratio_decides_at_the_published_precision():
    """#6145, same root as the watchdog gate: this check compared the EXACT
    ratio and then rendered both operands at 2 dp, so a fire inside
    (1.25, 1.255) printed "ratio 1.25x, tolerance 1.25x" — a diagnosis that
    reads as compliant while the check refuses, the very contradiction the
    #6145 change removes next door. The ratio is now decided at the precision it
    publishes, so no message can print a value that reads as within tolerance.
    """
    from tools.ci_selection import HALF_DURATION_IMBALANCE_RATIO, workflow_halves_issues
    halves = {"a": ["test_api"], "b": ["test_auth", "test_crypto"]}
    # 1250.4 / 1000 = 1.2504 -> prints 1.25 -> INSIDE tolerance, so it must pass
    # (under the old exact comparison this fired with a 1.25x-vs-1.25x message).
    inside = _halves_manifest()
    inside["durations"] = {"test_api.py": 1000.0, "test_auth.py": 1000.0,
                           "test_crypto.py": 250.4}
    assert not any("imbalanced" in i
                   for i in workflow_halves_issues(inside, halves))
    # 1256.0 / 1000 = 1.256 -> prints 1.26 -> still refused.
    beyond = _halves_manifest()
    beyond["durations"] = {"test_api.py": 1000.0, "test_auth.py": 1000.0,
                           "test_crypto.py": 256.0}
    assert any("imbalanced" in i
               for i in workflow_halves_issues(beyond, halves))
    assert HALF_DURATION_IMBALANCE_RATIO == 1.25


def test_fast_files_absent_from_halves_reports_coverage_hole():
    from tools.ci_selection import fast_files_absent_from_halves
    halves = {"a": ["test_api"], "b": ["test_crypto"]}
    absent = fast_files_absent_from_halves(_halves_manifest(), halves)
    assert absent == ["test_auth.py", "test_dead_entry.py"], absent  # slow excluded


def test_fast_files_absent_from_halves_excludes_env_broken_files():
    # #1266 false positive: an ENV_BROKEN_FILES member is kept out of EVERY half
    # by the selection filter, so it could never appear in a half and was
    # reported as a coverage hole on every single run. It is not a hole — the
    # file needs a live environment the shard jobs do not have, and it is run by
    # `.github/scripts/verify-cutover` instead. The exclusion must mirror `slow`
    # and `carve_out`, and a REAL hole must still be reported.
    from tools.ci_selection import ENV_BROKEN_FILES, fast_files_absent_from_halves
    m = _halves_manifest()
    m["surfaces"]["api"].extend(ENV_BROKEN_FILES)
    halves = {"a": ["test_api"], "b": ["test_crypto"]}
    absent = fast_files_absent_from_halves(m, halves)
    assert "test_auth.py" in absent, absent  # a genuine hole still reports
    assert not (set(absent) & ENV_BROKEN_FILES), absent


def test_real_manifest_never_reports_env_broken_as_a_coverage_hole():
    # The end-to-end form of the same guard, against the REAL manifest and the
    # REAL derived shards: `--integrity` must never print the
    # "in NO shard (full-matrix coverage hole)" warning about an env-broken
    # file, because that warning is permanent, always false, and was read at
    # face value by two independent readers of a shard-split PR as evidence the
    # split had dropped a test file.
    #
    # Deliberately scoped to ENV_BROKEN_FILES rather than asserting the absent
    # set is empty: the check is documented as informational, so pinning zero
    # would silently promote a warning into a fail-closed gate.
    from tools.ci_selection import (
        ENV_BROKEN_FILES,
        fast_files_absent_from_halves,
        load_manifest,
        push_legs,
    )
    m = load_manifest()
    legs = push_legs(m)
    halves = {s["name"]: set(s["files"]) for s in legs["shards"]}
    absent = set(fast_files_absent_from_halves(m, halves))
    assert not (absent & ENV_BROKEN_FILES), sorted(absent & ENV_BROKEN_FILES)


def test_coverage_hole_report_is_always_a_fast_pool_file():
    # The durable invariant, and the generalisation of the env-broken false
    # positive: ONLY a fast-pool file can legitimately be a hole, because the
    # halves are derived from `fast_pool()`. Anything reported absent that the
    # pool does not contain is a false positive by construction — which is
    # exactly what a member of any group `fast_pool` omits would be
    # (ENV_BROKEN_FILES today; `on_demand` latently, since it is currently in
    # no surface and so cannot reach this check).
    #
    # This subsumes the scoped test above and pins the sync requirement the
    # function's docstring states: the subtraction there must match
    # `fast_pool`'s own filter, or this reports a file the pool deliberately
    # excludes.
    from tools.ci_selection import (
        fast_files_absent_from_halves,
        fast_pool,
        load_manifest,
        push_legs,
    )
    m = load_manifest()
    legs = push_legs(m)
    halves = {s["name"]: set(s["files"]) for s in legs["shards"]}
    absent = set(fast_files_absent_from_halves(m, halves))
    pool = set(fast_pool(m))
    assert absent <= pool, sorted(absent - pool)


def test_real_workflow_halves_are_consistent():
    # #1472: the matrix halves are now DERIVED from the manifest
    # (space-joined matrix_* outputs) —
    # the #1266 discipline runs against the derivation. Verify the derived
    # halves carry every fast file exactly once and tilt is bounded.
    # #3400: the tilt invariant is now DURATION, not count. The full-matrix
    # halves are packed by measured weight (LPT), so a correct split is
    # duration-balanced while carrying very different file counts — the count
    # difference is the design (a few multi-minute files against the long tail
    # of sub-second ones), and the assertion below checks the balance, not the
    # count. The old `abs(count_a - count_b) <= 3` assertion encoded the
    # duration-blind parity split this issue exists to remove.
    from tools.ci_selection import (TESTS_DIR, push_legs,  # noqa: I001
                                    workflow_halves_issues,
                                    HALF_DURATION_IMBALANCE_RATIO)
    m = load_manifest()
    legs = push_legs(m)
    # #6135: the derived split is the SHARD set (N of them), not a/b.
    halves = {s["name"]: set(s["files"]) for s in legs["shards"]}
    issues = workflow_halves_issues(m, halves, TESTS_DIR)
    assert issues == [], f"derived halves drift: {issues}"
    weights = {h: sum(m["durations"].get(f + ".py", 2.0) for f in fs)
               for h, fs in halves.items()}
    ratio = max(weights.values()) / min(weights.values())
    assert ratio <= HALF_DURATION_IMBALANCE_RATIO, (
        f"duration tilt beyond {HALF_DURATION_IMBALANCE_RATIO}x: "
        f"{ {h: round(w / 60, 1) for h, w in weights.items()} } min "
        f"(ratio {ratio:.2f}x)")
    # every fast file rides exactly one shard (no coverage hole, no double-run)
    flat = [f for fs in halves.values() for f in fs]
    assert len(flat) == len(set(flat)), "leg overlap"


def test_push_legs_partitions_every_classified_file():
    """#1472: every classified file lands in exactly one push leg. Epic
    #1647 Task 9: the carve-out set is its OWN leg (E2E-4) — it is
    excluded from fast AND slow docker legs."""
    from tools.ci_selection import (  # noqa: I001
        ENV_BROKEN_FILES, on_demand_files, push_legs,
    )
    m = load_manifest()
    legs = push_legs(m)
    carve = {f.replace(".py", "") for f in m["carve_out"]}
    classified = set()
    for s, files in m["surfaces"].items():  # noqa: B007
        classified.update(files)
    classified.update(m["tier1"])
    classified.update(m["slow_files"])
    fast = {f.replace(".py", "") for f in classified if f not in m["slow_files"]}
    fast |= {f.replace(".py", "") for f in m.get("push_extra", [])}
    broken = {f.replace(".py", "") for f in ENV_BROKEN_FILES}
    # #10194: the non-gating lane is a subtraction from the fast pool just like
    # the carve-out, and it is read from the ONE accessor (a literal list here
    # would be a second definition of the lane that could drift from the one
    # `fast_pool()` subtracts — the partition would then fail for the right
    # reason at the wrong time).
    lane = {f[:-3] if f.endswith(".py") else f for f in on_demand_files(m)}
    assert lane, "the lane must be non-empty, or this subtraction proves nothing"
    assert set().union(*(set(s["files"]) for s in legs["shards"])) & lane == set(), (
        "a non-gating lane file reached a push shard")
    # #6135: the fast pool is partitioned across N shards, not two halves.
    shards = [set(s["files"]) for s in legs["shards"]]
    assert set().union(*shards) == fast - broken - carve - lane
    assert sum(len(s) for s in shards) == len(set().union(*shards)), "leg overlap"
    # carve-out files never ride the docker legs (fast OR slow)
    for shard in shards:
        assert not (shard & carve), "carve-out file on a fast shard"
    # The label sequence must be positional letters from `a`: the merge rail's
    # lane-parity subtracts PR job names from main's, so an S-adding diff is a
    # SUPERSET only while the first two labels stay `a`/`b` (#6135).
    assert [s["name"] for s in legs["shards"]] == list("abcdefghijklmnopqrstuvwxyz")[:len(legs["shards"])]
    assert not (set(legs["slow"]) & carve), \
        "slow carve-out files run in the URI-unset carve-out job, never the slow legs"
    assert set(legs["carve_out"]) == carve, "carve_out leg must be exactly the config set"
    # #1485 / #3400: bench files are NOT pinned to a half. `push_extra` is
    # spread evenly across the halves (#1485) and a bench file registered in
    # `surfaces` is packed by measured duration (#3400 LPT), so which half a
    # given bench file lands in is a packing outcome, not an assignment. The
    # pre-#1485 form of this check required a bench file in half_b SPECIFICALLY
    # and re-staled the moment a pool change moved one (#3811: adding a single
    # classified file flipped all three bench files into half_a, reddening an
    # unrelated PR). Assert the invariant the code actually provides HERE: every
    # bench file reaches a half (the partition assertion above). The push_extra
    # spread rule is not asserted against this manifest — the shipped
    # `push_extra` is empty, so it would be vacuous — it is pinned on a
    # SYNTHETIC manifest by test_push_legs_distributes_push_extra_across_halves
    # below (#2988/#3243).
    all_shard_files = [f for s in legs["shards"] for f in s["files"]]
    assert any(f.startswith("bench/") for f in all_shard_files), \
        "no bench file reached the push legs at all"


def test_push_legs_distributes_push_extra_across_halves():
    """#1485: ``push_extra`` is spread across the halves (even index -> half_a,
    odd -> half_b), never dumped on one.

    Pinned on a SYNTHETIC manifest: the shipped ``push_extra`` is empty (the
    bench files are classified under the `eval` surface and packed by LPT), so
    asserting this against the real manifest is vacuous — and pinning which
    half a *bench* file lands on was a function of the duration estimates, not
    a designed invariant (#2988/#3243 corrected the stale
    test_selfhost_health_probe_executor.py weight, which flipped it).
    """
    from tools.ci_selection import push_legs

    m = dict(load_manifest())
    m["push_extra"] = ["bench/synthetic_a.py", "bench/synthetic_b.py"]
    legs = push_legs(m)
    # #6135: round-robin over the shards — the first two extras land on the
    # first two labels (there is no "half" to pin to any more).
    assert "bench/synthetic_a" in legs["shards"][0]["files"]
    assert "bench/synthetic_b" in legs["shards"][1]["files"]


# ── #6135: the fast pool's shard count ────────────────────────────────────
# The hand-written 2 was the reason the critical path (`max(shard)`) stayed
# ~30 min. These pin the three properties the change turns on: a configured S
# actually shards the pool EXACTLY ONCE (the accounting rule the issue names as
# the real risk), the labels stay a positional letter sequence so a shard-ADD
# is a superset of main's names for the merge rail's lane-parity, and the
# watchdog is PER-LEG rather than the inherited 55m.


def _shard_manifest(count: int, heavy: dict[str, float] | None = None,
                    tiny: int = 60) -> dict:
    m = _duration_manifest(heavy or {}, tiny)
    m["fast_shards"] = count
    return m


def test_every_file_rides_exactly_one_shard_at_s_greater_than_two():
    """#6135 verification: a generated matrix with S>2 covers every selected
    file EXACTLY once (set-equality against the selector's own pool)."""
    from tools.ci_selection import fast_pool, push_legs
    m = _shard_manifest(5)
    legs = push_legs(m)
    assert len(legs["shards"]) == 5
    flat = [f for s in legs["shards"] for f in s["files"]]
    assert len(flat) == len(set(flat)), "double-run: a file on two shards"
    bare_pool = {f[:-3] for f in fast_pool(m)} - {
        f[:-3] for f in m.get("push_extra", [])}
    assert set(flat) == bare_pool, "coverage hole vs the selector's pool"
    # the generator fed the workflow is the same partition (no re-derivation)
    from tools.ci_selection import fast_matrix_include
    inc = fast_matrix_include(legs["shards"])["include"]
    assert len(inc) == 5
    assert len({e["half"] for e in inc}) == 5
    assert [e["half"] for e in inc] == ["a", "b", "c", "d", "e"]
    assert sum(len(e["files"].split()) for e in inc) == len(flat)
    # #6897: every row carries its `runner`, and `fast_matrix_include` must not
    # silently drop it — the whole lane is that key plus the workflow's `runs-on`.
    for e in inc:
        assert "runner" in e, f"the emitted matrix must carry the runner (#6897): {e['half']}"
    # #6145: the emitted headroom is this change's only new OUTPUT — the whole
    # feature is "the margin a leg actually got is visible" — and it had NO
    # assertion: deleting the key from `fast_matrix_include` left all 221 tests
    # green (measured) while silently removing the feature. Pin it per entry
    # against the shard value it reports, so the emitter and the shard dict
    # cannot drift, and require it to be a real margin.
    from tools.ci_selection import WATCHDOG_HEADROOM
    for s, e in zip(legs["shards"], inc, strict=True):
        assert "watchdog_headroom" in e, \
            f"the emitted matrix must carry the headroom (#6145): {e['half']}"
        assert e["watchdog_headroom"] == s["watchdog_headroom"], (e, s)
        assert e["watchdog_headroom"] >= WATCHDOG_HEADROOM, (e, s)


def test_shard_labels_are_a_superset_when_s_changes():
    """#6135/the merge rail: lane-parity subtracts the PR's job names from
    main's, so an S-ADDING diff is comparable only while main's `test (a)`
    and `test (b)` survive. The labels must be a positional letter sequence —
    a rename would make both of main's names MISSING and refuse every merge.
    """
    from tools.ci_selection import shard_labels
    main_names = {f"test ({lbl})" for lbl in shard_labels(2)}
    pr_names = {f"test ({lbl})" for lbl in shard_labels(9)}
    assert main_names == {"test (a)", "test (b)"}
    assert main_names <= pr_names, "a shard-ADD must be a superset (rail-safe)"
    # and the REMOVE direction still refuses (the rail's intended polarity)
    assert not (pr_names <= main_names)


def test_watchdog_is_per_shard_and_scales_with_the_shard():
    """#6135: the watchdog is PER-LEG. Inheriting the old 55m means a hung
    ~6-minute shard is detected ~8× later than it should be."""
    from tools.ci_selection import (
        WATCHDOG_CEILING_MIN,
        WATCHDOG_FLOOR_MIN,
        _duration_weight,
        _durations_map,
        fast_pool,
        load_manifest,
        shard_watchdog_minutes,
    )
    small = shard_watchdog_minutes(6.81 * 60)     # a 6.81-min shard at S=9
    old_two = shard_watchdog_minutes(30.62 * 60)  # a 30.62-min shard at S=2
    # The FLOOR is a DERIVED value, not a taste: 2.9× the largest single fast file
    # (the derivation `tools/ci_selection.py`'s constant block states). It was 15
    # while the largest was 310.7 s; the 2026-10-26 sweep measured
    # `test_calibration.py` at 390.0 s, so 15 no longer clears the 2.5× asserted
    # below and the floor moved to the value its own derivation requires —
    # ceil(390.0 × 2.9 / 60) = 19. The pin stays a pin: it names the number the
    # derivation produced, so the NEXT move has to be argued here too.
    assert small == WATCHDOG_FLOOR_MIN == 19, small
    assert small < old_two, "the smaller shard must not inherit the old budget"
    assert old_two == WATCHDOG_CEILING_MIN == 55
    # the floor must clear the #6133 floor (the largest single fast file) with
    # headroom, or the watchdog would kill a legally-loaded shard. DERIVED from
    # the committed manifest — a frozen literal cannot notice a refresh (or a
    # new file) raising the real floor past the watchdog.
    manifest = dict(load_manifest())
    durations = _durations_map(manifest)
    largest = max(_duration_weight(durations.get(
        f if f.endswith(".py") else f + ".py")) for f in fast_pool(manifest))
    assert largest > 0
    assert small * 60 / largest > 2.5, (
        f"the watchdog floor ({small} min) has <2.5x headroom over the "
        f"largest fast file ({largest:.1f}s) — #6133's floor")
    # a shard big enough to need more than the floor gets its own headroom
    assert shard_watchdog_minutes(20 * 60) == 40
    # malformed/absent estimates fall back to the floor, never to zero —
    # including the non-finite floats that pass float() and then explode
    # inside math.ceil
    for bad in (None, "nonsense", float("inf"), float("nan"), float("-inf"), -5):
        assert shard_watchdog_minutes(bad) == WATCHDOG_FLOOR_MIN, bad


def test_a_shard_merely_slow_under_load_still_fits_its_budget():
    """#6145 direction (a): a shard that runs SLOW under load must still fit.

    The budget is built from `WATCHDOG_HEADROOM`, so the question this test asks
    is whether that factor is still above the slowest width a healthy shard has
    actually been observed to occupy. If it were not, the watchdog would kill a
    shard that observed no defect — the whole defect #6145 names.

    MEASURED — pytest-STEP duration of all nine fast shards, 76 SUCCESSFUL leg
    samples drawn from main runs 2026-10-02T01:19Z-03:22Z, each over that shard's
    declared 7.22-min estimate: min 0.47x, median 0.86x, MAX 1.33x.

    ⛔ THE SAMPLE IS SUCCESS-ONLY, DELIBERATELY, AND ITS LIMIT IS NAMED. A killed
    leg is excluded because a kill is not a "merely slow" observation — it is the
    event this budget exists to bound, and it is not what this assertion is
    about. For the record, the kills in that window and the day after were at
    ~2.11x (legs SIGKILLed at the 15m floor with 0 failures), and the SAME shards
    ran 0.9x-1.2x on another attempt of that run — a nondeterministic wedge
    beyond this distribution, not a tail of it. So this bounds the SUCCESSFUL
    distribution; it does NOT claim the budget survives every loaded leg, and
    #6145 carries that half. The value
    that must survive is the MAX (the median is what a healthy shard costs).
    A job's WALL time is deliberately NOT the measurement: it carries ~6 min of
    setup plus the off-watchdog collect-only pre-phase, so it overstates the
    shard by roughly the setup constant — the pytest step is the only surface
    the watchdog actually bounds, hence the only comparable one.
    """
    from tools.ci_selection import (
        WATCHDOG_HEADROOM,
        load_manifest,
        push_legs,
        watchdog_headroom_issues,
    )
    # The slowest healthy shard measured. A frozen literal is right HERE, unlike
    # the derived floor below: this is an observation about runs that have
    # already happened, not a property of the committed manifest, so there is
    # nothing in-tree to re-derive it from. It is re-measured by hand when the
    # pool changes materially (the durations map's own sweep is the trigger).
    measured_max_healthy_shard_ratio = 1.33
    assert measured_max_healthy_shard_ratio < WATCHDOG_HEADROOM, (
        "the worst HEALTHY shard measured is already outside the factor the "
        "budget is built from — the margin no longer covers even the "
        "successful distribution, and the constant must be raised "
        "DELIBERATELY (#6145)")
    # The shipped gate's OWN verdict on the committed manifest is the primary
    # assertion, so this test cannot disagree with the gate at either end of the
    # reporting quantum (#6145). The loop below is a readable restatement for a
    # non-empty leg, not a second, differently-precise gate.
    committed = load_manifest()
    assert watchdog_headroom_issues(committed) == [], watchdog_headroom_issues(committed)
    for s in push_legs(committed)["shards"]:
        headroom = s["watchdog_minutes"] * 60.0 / s["est_seconds"]
        assert headroom >= measured_max_healthy_shard_ratio, (
            f"shard {s['name']}: {headroom:.2f}x is below the slowest healthy "
            f"shard measured ({measured_max_healthy_shard_ratio}x) — a merely "
            f"slow shard would be killed")
        # The shipped gate's own invariant, asserted on the value the matrix
        # PUBLISHES (2 dp) rather than on a second, differently-precise
        # recomputation: otherwise this test and the gate could disagree in the
        # band where rounding decides — a manifest could pass `--integrity` and
        # then red here (#6145).
        assert s["watchdog_headroom"] >= WATCHDOG_HEADROOM, (
            f"shard {s['name']}: emitted budget retains "
            f"{s['watchdog_headroom']:.2f}x, below the {WATCHDOG_HEADROOM}x it "
            f"is built from (#6145)")


def test_a_shard_that_cannot_fit_its_budget_is_named_not_silently_killed():
    """#6145 direction (b): the sizing contract — "a shard unable to fit is
    flagged rather than silently killed". #4819 option 2 recorded this as the
    thing nothing checked: "the watchdog value and the shard count — nothing
    checks that [they are consistent]".

    The CEILING is what makes a shard unable to fit: it clamps the built factor
    (2.0x) DOWNWARD. Before this check the clamp was silent, so raising an
    estimate produced a TIGHTER margin with no signal anywhere — the state in
    which a shard that fits its own declared estimate is killed.
    """
    from tools.ci_selection import (
        WATCHDOG_CEILING_MIN,
        WATCHDOG_HEADROOM,
        load_manifest,
        push_legs,
        shard_watchdog_minutes,
        watchdog_headroom_issues,
    )
    # (a) the committed manifest is consistent: nothing named, and every leg's
    #     emitted budget retains the factor it was built from.
    committed = load_manifest()
    assert watchdog_headroom_issues(committed) == []
    for s in push_legs(committed)["shards"]:
        assert s["watchdog_headroom"] >= WATCHDOG_HEADROOM, s
    # (b) an estimate past the ceiling's reach is NAMED (fail-closed). 30.62 min
    #     is the ceiling's OWN provenance exemplar ("55m for a ~30m shard",
    #     factor 1.8 — the constants block): ceil(30.62*2)=62 -> clamped to 55 =
    #     1.80x, BELOW the factor the budget was built from. (The real S=2 shape
    #     is heavier still — the probe that lowered fast_shards to 2 measured
    #     est=1951.0s = 32.52 min at 1.69x — so this exemplar is the gentler of
    #     the two, not a best case.)
    assert shard_watchdog_minutes(30.62 * 60) == WATCHDOG_CEILING_MIN == 55
    m = _shard_manifest(2, heavy={"test_huge_a.py": 1837.2,
                                  "test_huge_b.py": 1837.2})
    issues = watchdog_headroom_issues(m)
    assert issues, "a shard the ceiling clamps below the build factor must be NAMED"
    assert any("its emitted budget retains" in i for i in issues), issues
    assert any("ceiling clamped it" in i for i in issues), issues
    # (d) the decision is taken at the PUBLISHED precision (2 dp — the same
    #     rounding the emitters apply), so a manifest is never refused while the
    #     row it would emit reads a compliant `2.0`, and the message never says
    #     "2.00x, below the 2.0x". 1651.0s = 27.5167 min -> 55/27.5167 = 1.9988,
    #     which ROUNDS to 2.0 and must therefore pass; 1660.0s = 27.6667 min ->
    #     1.9880 -> 1.99 and must still be refused. `tiny=0` keeps the shard's
    #     estimate exactly the heavy file's duration.
    edge = _shard_manifest(1, heavy={"test_edge.py": 1651.0}, tiny=0)
    assert watchdog_headroom_issues(edge) == [], watchdog_headroom_issues(edge)
    past = _shard_manifest(1, heavy={"test_past.py": 1660.0}, tiny=0)
    assert watchdog_headroom_issues(past), "1.99x must still be refused"
    # (e) the OTHER end of the same quantum. A ratio below 0.005 rounds to 0.00,
    #     which is also `watchdog_headroom`'s "no usable estimate" sentinel — so
    #     a guard testing the ROUNDED value for truthiness FAILS OPEN here: it
    #     skips the leg and publishes `0.00` on a row it passed. The sentinel is
    #     therefore read from the UNROUNDED ratio, and a leg this far past the
    #     ceiling is still NAMED. 844400.0s = 14073.3 min -> 55/14073.3 = 0.0039
    #     -> rounds to 0.0.
    collapsed = _shard_manifest(1, heavy={"test_collapsed.py": 844400.0},
                               tiny=0)
    assert watchdog_headroom_issues(collapsed), (
        "a ratio that rounds to 0.00 is a mis-sized leg, not an absent estimate")
    # and the sentinel itself is NOT a finding: a leg with no estimate has no
    # ratio to judge (an empty leg is not over-tight).
    empty = _shard_manifest(1, tiny=0)
    assert watchdog_headroom_issues(empty) == [], watchdog_headroom_issues(empty)
    # (c) a genuinely wedged leg is STILL KILLED. The check makes an over-tight
    #     budget visible; it does not widen it and it does not disarm the kill —
    #     the emitted budget is unchanged, and the workflow still applies it with
    #     a SIGINT-then-SIGKILL escalation, so a leg that hangs past its budget
    #     is still terminated (and the outer job cap still bounds the total).
    #     Read the PARSED run step, never the file text: the identical string
    #     also sits in heading COMMENTs (and with literal durations in other
    #     steps), so a whole-file scan stays green if the real execution line is
    #     deleted — an assertion that can pass on a comment does not guard what
    #     its message claims.
    for s in push_legs(m)["shards"]:
        assert s["watchdog_minutes"] == WATCHDOG_CEILING_MIN, s
    wf = _load_python_ci()
    run = next(s for s in wf["jobs"]["test"]["steps"]
               if s.get("name", "").startswith("Run fast test suite"))["run"]
    assert "timeout -s INT -k 10 ${{ matrix.watchdog_minutes }}m" in run, (
        "the per-shard kill must stay wired: without it a wedged leg runs to the "
        "job's outer cap and the budget is advisory")


def test_fast_shard_config_is_validated_not_silently_defaulted():
    """#6135: a malformed `fast_shards` is NAMED by the integrity gate — a
    present-but-unusable declaration must not read as the default 2."""
    from tools.ci_selection import fast_shard_count, fast_shard_issues
    assert fast_shard_issues({}) == []              # absent = not adopted
    assert fast_shard_count({}) == 2
    # AN EXPLICIT null is NOT absence: it would silently drop the lane to 2
    # while main runs the configured S, and the rail would then refuse every
    # merge. Every malformed value is NAMED; the count still degrades safely.
    for bad in (1, 0, -3, 27, 2.5, "9", True, None):
        m = {"fast_shards": bad}
        assert fast_shard_issues(m), f"{bad!r} must be reported"
        assert fast_shard_count(m) == 2, "a producer path must never crash"
    assert fast_shard_issues({"fast_shards": 9}) == []


def test_shard_count_is_stable_regardless_of_the_selection_size():
    """#6135: the tier-2 matrix must emit the SAME shard count as the push
    matrix, even for a tiny selection. The merge rail's lane-parity is a
    job-NAME subtraction, so a PR that ran `test (a)`…`test (c)` while main runs
    `a`…`i` would be refused NOT COMPARABLE. An empty shard exits early with an
    honest report; a shard that does not EXIST cannot be compared at all.
    """
    from tools.ci_selection import build_shard_entries, fast_matrix_include
    three = ["tests/test_a.py", "tests/test_b.py", "tests/test_c.py"]
    entries = build_shard_entries(
        three, {f: 5.0 for f in ("test_a.py", "test_b.py", "test_c.py")}, 9)
    assert len(entries) == 9
    assert [e["name"] for e in entries] == list("abcdefghi")
    inc = fast_matrix_include(entries)["include"]
    assert [e["half"] for e in inc] == list("abcdefghi")
    # #6897: the routing is POSITIONAL and derived, so a tiny selection that leaves
    # most shards EMPTY must still emit a class for every row — an empty shard is
    # still a JOB, and a row without `runner` makes `runs-on` resolve to null.
    assert all(e.get("runner") for e in inc), inc
    # every file still rides exactly one shard; the rest are legitimately empty
    flat = [f for e in entries for f in e["files"]]
    assert sorted(flat) == ["test_a", "test_b", "test_c"]
    assert sum(1 for e in entries if not e["files"]) == 6


def test_the_workflow_matrix_is_derived_and_the_watchdog_is_per_shard():
    """#6135: the `test` job must expand the selector's matrix (no hand-written
    shard count), and the pytest watchdog must be the shard's own budget.

    These are the two workflow-level claims the unit tests above cannot see:
    a literal `half: [a, b]` is exactly the drift the issue exists to end, and
    a literal `55m` watchdog is the per-leg correctness bug it names.
    """
    wf = _load_python_ci()
    job = wf["jobs"]["test"]
    matrix = job["strategy"]["matrix"]
    assert "fromJSON(needs.changes.outputs.fast_matrix)" in str(matrix), matrix
    assert "include" not in str(matrix), \
        "the shard set must come from the selector, not a workflow literal"
    run = next(s for s in job["steps"]
               if s.get("name", "").startswith("Run fast test suite"))["run"]
    assert "timeout -s INT -k 10 ${{ matrix.watchdog_minutes }}m" in run, \
        "the pytest watchdog must be the per-shard budget, not a literal"
    assert "timeout -s INT -k 10 55m" not in run
    # the canary artifact the separate job downloads must be the fixed
    # producer name, never a per-shard one (no shard name survives S changes)
    dl = next(s for s in wf["jobs"]["canary-streak"]["steps"]
              if s.get("name", "").startswith("Download producer-shard"))
    assert dl["with"]["name"] == "pytest-canary-producer"


def test_no_required_check_names_a_fast_leg():
    """#6135 acceptance: a shard-ADDING diff must not leave a required check
    that no longer exists — an unsatisfiable required check is a queue that
    never admits. Every merge surface must key on the AGGREGATE / the job id,
    never on a leg name (`test (a)`), so the required set is satisfiable for
    ANY shard count.

    The live branch-protection set is an API surface (it was read when this
    change was made: `pricing-artifact, docs, test-isolation, license-surface,
    legal-e2e, python-ci-gate` — no leg name); these are the in-repo surfaces
    that must stay consistent with it, and the ones a future edit could break.
    """
    wf = _load_python_ci()
    gate = wf["jobs"]["python-ci-gate"]
    # the aggregate observes the test MATRIX JOB, whose result rolls up every
    # leg — so adding a leg cannot orphan anything it requires
    assert "test" in gate["needs"]
    assert not [n for n in gate["needs"] if str(n).startswith("test (")], gate["needs"]
    # the rollup is job-id keyed, not leg-name keyed. ONE step does the rollup and it
    # is the sole author of the verdict: `python-ci-gate` still has EXACTLY ONE step
    # (#9984 kept it that way on purpose — the two checkouts, this repo and the pinned
    # agent-infra holding the shared predicate, live in the SEPARATE
    # `ci-introduced-failure` job, because a `uses:`/`with:`/`env:` step added to this
    # aggregate would widen the shim defence the capstone pin enforces). So the pin
    # names the property that matters — exactly one step, and exactly one reader of
    # the join — instead of a positional index. (A previous revision of this comment
    # claimed the two checkouts sat ahead of the rollup in THIS job; they never did,
    # and that false premise is what made loosening the positional check look
    # justified.)
    assert len(gate.get("steps") or []) == 1, (
        "python-ci-gate must stay a ONE-step aggregate — an extra step can export "
        f"BASH_ENV or mask the rollup's exit code (#9984 review); got {[s.get('name') for s in gate.get('steps') or []]}")
    rollup_steps = [s for s in gate.get("steps", [])
                    if "join(needs.*.result, ' ')" in (s.get("run") or "")]
    assert len(rollup_steps) == 1, (
        "exactly one step must own the `needs.*.result` rollup — two readers means "
        f"two verdicts; got {len(rollup_steps)}")
    assert rollup_steps[0]["run"] == gate["steps"][0]["run"], (
        "the rollup step must BE the aggregate's only step, not a sibling of one")
    # the merge queue's only merge condition is the aggregate. Comments MAY
    # name a leg as history (several do, deliberately) — the ENFORCED lines
    # must not.
    mergify = (REPO / ".mergify.yml").read_text()
    mergify_code = "\n".join(
        ln for ln in mergify.splitlines() if not ln.lstrip().startswith("#"))
    assert "test (" not in mergify_code, \
        "no enforced mergify line may name a shard leg — the shard set changes with S"
    assert "      - check-success=python-ci-gate" in mergify_code
    # The LIVE required set is a GitHub API surface; its in-repo projection is the
    # guard's record (mcg.RECORD_REL). Read the PROJECTION, never the declaration
    # home: a test or tool reading the declaration home is what makes it silently
    # live, and clause viii(b) of the guard refuses exactly that — reading it here
    # turned this file into a reported divergence.
    record = json.loads((REPO / mcg.RECORD_REL).read_text())
    # Anchor the field before testing it, so an emptied or renamed list cannot
    # satisfy the comprehension below silently. `python-ci-gate` is the
    # aggregate every merge keys on.
    assert "python-ci-gate" in record["required_contexts"], (
        f"{mcg.RECORD_REL} no longer names python-ci-gate — the required-context "
        "list moved or emptied, so the shard-leg check below would read empty and pass")
    assert not [c for c in record["required_contexts"] if c.startswith("test (")], (
        "no required context may be a shard leg — the shard set changes with S, so "
        "keying the required set on a leg name would make the gate unsatisfiable")


# ── #6897: the fast matrix CAN route across both runner classes ──────────
# The capacity decision is #6897's (owner ruling 2026-10-27: use the compute
# capacity wherever it is; THROUGHPUT over runner class) — but it is delivered
# as an OPT-IN, because the pool is **fallback-only** by a RECORDED DECISION
# (`docs/ops/ci-runner-x64.md` -> "Contradiction test",
# `scripts/ci_runner_gate.sh:1-30`), and making the pool a primary class by
# default would be an adoption over that decision.
#
# So these pin FOUR properties: the DEFAULT is main's behaviour exactly
# (`hosted-only`, every row on `ubuntu-latest`) — the one #10186's review found
# violated, with 3 of 4 pool legs failing on its own head — the opt-in actually
# splits, every row HAS a class (a missing key makes `runs-on` resolve to null
# and the file is refused — #6253's zero-jobs case), and the canary producer
# keeps the label it has always used so the streak stays comparable.
#
# ⛔ WHAT THESE DO **NOT** CLAIM: that the hosted half is GitHub-hosted. On this
# repo `ubuntu-latest` is not hosted-only — `scripts/ci_runner_gate.sh` attaches
# it to the self-hosted pool as a fallback, and #6897 measured it attached while
# GitHub-hosted was healthy. The class a `ubuntu-latest` row lands on is main's
# existing ambiguity, carried over unchanged; `SHARD_RUNNER_HOSTED` names the
# LABEL, which is all this lane decides.


def test_the_fast_split_is_off_by_default_so_the_pool_stays_fallback_only(monkeypatch):
    """#6897/#10186: with no opt-in, EVERY row is main's `ubuntu-latest`.

    This is the assertion the earlier cut of this branch failed: it routed odd
    shards to `[self-hosted, self-hosted-x64]` unconditionally, which moved 4 of
    9 gated shards onto a class the recorded `fallback-only` decision says is not
    primary. It also fails SAFE on junk: an unset variable, an emptied one, a
    fork with no `vars`, or a typo must all land on main's behaviour rather than
    discovering the split when a leg dies.
    """
    from tools.ci_selection import (
        SHARD_ROUTING_ENV,
        SHARD_ROUTING_HOSTED_ONLY,
        SHARD_ROUTING_POOL_SPLIT,
        SHARD_RUNNER_HOSTED,
        fast_matrix_include,
        load_manifest,
        push_legs,
        resolve_shard_routing,
    )
    monkeypatch.delenv(SHARD_ROUTING_ENV, raising=False)
    shards = push_legs(load_manifest())["shards"]
    inc = fast_matrix_include(shards)["include"]
    assert inc, "the real config emits no shards — nothing to route"
    assert {e["runner"] for e in inc} == {json.dumps(SHARD_RUNNER_HOSTED)}, (
        "the default must be hosted-only — the pool is fallback-only by recorded "
        f"decision (#6897): {sorted(e['runner'] for e in inc)}")
    # …and it must be the DEFAULT, not an accident of an unset environment: the
    # mode resolver is the single decision point, and every non-opt-in value
    # resolves to hosted-only, `hosted-only` itself included (the documented safe
    # setting must stay legal).
    for value in (None, "", "   ", SHARD_ROUTING_HOSTED_ONLY, "hosted-only ",
                  "HOSTED-ONLY", "true", "1", "pool", "split", "pool-splits",
                  "pool_split"):
        assert resolve_shard_routing(value) == SHARD_ROUTING_HOSTED_ONLY, value
    assert resolve_shard_routing(SHARD_ROUTING_POOL_SPLIT) == SHARD_ROUTING_POOL_SPLIT
    assert resolve_shard_routing(" POOL-SPLIT ") == SHARD_ROUTING_POOL_SPLIT
    # the environment is the operator lever, so it must reach the matrix — and
    # only for the exact opt-in.
    for value in ("", SHARD_ROUTING_HOSTED_ONLY, "nonsense"):
        monkeypatch.setenv(SHARD_ROUTING_ENV, value)
        got = {e["runner"] for e in fast_matrix_include(shards)["include"]}
        assert got == {json.dumps(SHARD_RUNNER_HOSTED)}, (value, sorted(got))
    monkeypatch.setenv(SHARD_ROUTING_ENV, SHARD_ROUTING_POOL_SPLIT)
    got = {e["runner"] for e in fast_matrix_include(shards)["include"]}
    assert len(got) == 2, f"the opt-in must actually split: {sorted(got)}"


def test_every_fast_row_names_a_runner_and_the_split_uses_both_classes():
    """#6897: `runner` is emitted per row, positional, and never degenerates.

    Asserts the OPT-IN mode — the default is pinned by the test above.
    """
    from tools.ci_selection import (
        SHARD_ROUTING_POOL_SPLIT,
        SHARD_RUNNER_HOSTED,
        SHARD_RUNNER_X64,
        fast_matrix_include,
        load_manifest,
        push_legs,
    )
    inc = fast_matrix_include(push_legs(load_manifest())["shards"],
                              routing=SHARD_ROUTING_POOL_SPLIT)["include"]
    assert inc, "the real config emits no shards — nothing to route"
    for e in inc:
        assert "runner" in e, f"missing runner (#6897): {e['half']}"
        # a JSON ARRAY STRING, not a bare label: `runs-on` needs an array, and a
        # matrix value is always a string, so the workflow must `fromJSON` it.
        parsed = json.loads(e["runner"])
        assert isinstance(parsed, list) and parsed, e
        assert all(isinstance(x, str) for x in parsed), e
        assert parsed in (SHARD_RUNNER_HOSTED, SHARD_RUNNER_X64), (
            f"unexpected runner class on {e['half']}: {parsed}")
    # THE CANARY PRODUCER STAYS ON THE HOSTED LABEL — the one it has always used,
    # so the canary streak keeps comparing tests rather than classes. It is the
    # HOSTED LABEL and not "GitHub-hosted": see this block's header for why that
    # label's class is not a guarantee on this repo.
    producer = [e for e in inc if e["canary_producer"]]
    assert len(producer) == 1, producer
    assert json.loads(producer[0]["runner"]) == SHARD_RUNNER_HOSTED, (
        "the canary producer must stay on the hosted label, not the pool (#6897): "
        f"{producer[0]['half']} -> {producer[0]['runner']}")
    # BOTH CLASSES, or the routing is a rename of `ubuntu-latest` with extra
    # failure modes. (The opposite degeneracy — every row on the pool — would
    # ALSO lose the hosted fallback the ci_runner_gate exists for.)
    classes = {e["runner"] for e in inc}
    assert classes == {json.dumps(SHARD_RUNNER_HOSTED), json.dumps(SHARD_RUNNER_X64)}, (
        f"the fast matrix must split across both classes, got {sorted(classes)}")


def test_runner_routing_is_positional_and_the_last_shard_is_always_on_the_hosted_label():
    """#6897: the routing rule, on SYNTHETIC shard counts.

    The real manifest is S=9, where "alternate by index" and "the last shard
    keeps the hosted label" happen to agree (index 8 is even) — so pinning the
    rule only against the real config would let an S=10 change flip the canary
    producer onto the pool without a red. This asserts the rule for both
    parities.
    """
    from tools.ci_selection import SHARD_RUNNER_HOSTED, SHARD_RUNNER_X64, fast_shard_runner
    for count in range(2, 13):
        runners = [fast_shard_runner(i, count) for i in range(count)]
        assert runners[-1] == SHARD_RUNNER_HOSTED, (
            f"S={count}: the last shard is the canary producer and must keep the "
            "hosted label")
        if count == 2:
            # DEGENERATE BY CONSTRUCTION, and safe: with two shards, index 0 is
            # even and index 1 IS the pinned last one, so there is no pool row to
            # give. It falls back to exactly main's pre-#6897 behaviour (both on
            # the hosted label) rather than inventing a split the rule does not
            # describe.
            assert runners == [SHARD_RUNNER_HOSTED, SHARD_RUNNER_HOSTED], runners
        else:
            assert set(map(tuple, runners)) == {
                tuple(SHARD_RUNNER_HOSTED), tuple(SHARD_RUNNER_X64)
            }, f"S={count}: a single class is not a split"
        # positional alternation over every shard that is not the pinned last one
        for i, got in enumerate(runners[:-1]):
            want = SHARD_RUNNER_HOSTED if i % 2 == 0 else SHARD_RUNNER_X64
            assert got == want, (count, i, got, want)
        # the returned list is a COPY: a caller mutating one row's label array
        # (json.dumps in `fast_matrix_include` reads it, so a later edit could
        # plausibly extend it) must not change the class the NEXT row gets. The
        # assertion compares against an INDEPENDENT literal and checks identity,
        # because `==` against the module constant is an aliasing tautology —
        # dropping the `list()` copy survives it (measured).
        runners[0].append("mutated")
        assert fast_shard_runner(0, count) == ["ubuntu-latest"], count
        assert fast_shard_runner(0, count) is not SHARD_RUNNER_HOSTED, (
            "fast_shard_runner must return a copy, never the module constant")
        assert fast_shard_runner(1, count) is not SHARD_RUNNER_X64
        # The hosted LABEL is pinned deliberately, and a change to it must be a
        # deliberate change: `ubuntu-latest` is what main uses today, so moving to
        # the hosted-ONLY `ubuntu-24.04` would also drop the outage fallback and
        # break comparability with the canary streak's history. That is a decision
        # (#6897), not a tidy-up — so it cannot happen behind a green suite.
        assert SHARD_RUNNER_HOSTED == ["ubuntu-latest"], SHARD_RUNNER_HOSTED


def test_the_test_job_takes_its_runner_from_the_matrix_with_a_variable_rollback():
    """#6897: the workflow half of the routing.

    Four claims the unit tests above cannot see: `runs-on` reads the selector's
    per-row class (no literal that would ignore the matrix, and NO second copy of
    the mode logic to drift from the selector's), the opt-in reaches the selector
    as the repo VARIABLE rather than a code edit, the emitted value is parsed
    (the row value is a JSON string; `runs-on` needs the array), and the job is
    not renamed — branch protection and the merge rail key on `test (a)`….
    """
    wf = _load_python_ci()
    job = wf["jobs"]["test"]
    runs_on = str(job["runs-on"])
    assert runs_on == "${{ fromJSON(matrix.runner) }}", (
        "`test` must take its runner class from the selector's matrix row, and "
        f"from nowhere else (got {runs_on!r})")
    assert "self-hosted" not in runs_on and "ubuntu-" not in runs_on, (
        "a literal runner label in `runs-on` would bypass the selector — and, "
        "for a pool label, make the split the DEFAULT again (#10186 review)")
    # THE OPT-IN IS WIRED, in the job that emits the matrix: without this env
    # the selector never sees `CI_SHARD_ROUTING` and the split can never be
    # turned on (the fail-safe direction, but a dead lever all the same).
    emit = [s for s in wf["jobs"]["changes"]["steps"]
            if s.get("id") == "push-matrix"]
    assert len(emit) == 1, emit
    env = _effective_step_env(wf, "changes", emit[0])
    assert "vars.CI_SHARD_ROUTING" in env.get("CI_SHARD_ROUTING", ""), (
        "the matrix emitter must pass the CI_SHARD_ROUTING repo variable to the "
        f"selector (#6897); got env={env!r}")
    # the job id and its `test (<half>)` name are branch-protection surfaces
    assert job["name"] == "test (${{ matrix.half }})", job["name"]
    # ⛔ and the legs that CANNOT run on the pool keep their version pin: a
    # `self-hosted` here would let a 39.6m leg be killed at its watchdog (the
    # measurement in the test-slow header).
    assert wf["jobs"]["test-slow"]["runs-on"] == "ubuntu-24.04", (
        "test-slow must stay version-pinned to GitHub-hosted (#6897 does not touch it)")


def test_the_opt_in_reaches_every_matrix_emitter_in_the_changes_job():
    """#6897/#10186: ONE lever, EVERY emitter — the tier-2 split included.

    `changes` emits `fast_matrix` from TWO steps: `push-matrix`
    (`--emit-push-matrix`, every event) and `split` (`--split`, tier-2 PRs, and
    its output OVERRIDES the push matrix — see the job `outputs:` block). The
    opt-in is read from the ENVIRONMENT (`resolve_shard_routing(
    os.environ.get("CI_SHARD_ROUTING"))`), so an emitter that does not carry
    that variable silently resolves to hosted-only whatever the operator set:
    a dead lever on exactly the event type every PR uses.

    Measured on the pre-fix tree (`3db8f7d78`): the `env:` was attached to the
    `push-matrix` STEP only, so with `CI_SHARD_ROUTING=pool-split` in the repo
    variables a tier-2 PR still emitted nine rows on `["ubuntu-latest"]` and the
    split never happened. Fail-SAFE (that is main's behaviour) but not the
    documented contract — `docs/ops/ci-runner-x64.md` says `pool-split` opts in,
    with no event qualification — and the failure mode is the worst kind: the
    run looks exactly as if nobody had set the variable.

    The env is asserted through `_effective_step_env` (job + step, merged per
    step) and additionally, at STEP scope alone, by
    `test_the_changes_job_carries_no_job_scope_env` —
    job scope is barred by the capstone pin; the emitter set is DISCOVERED from
    the run commands rather than named, so a THIRD emitter added later cannot
    silently miss the lever either.
    """
    wf = _load_python_ci()
    emitters = []
    for step in wf["jobs"]["changes"]["steps"]:
        run = str(step.get("run") or "")
        if "--emit-push-matrix" in run or "--split" in run:
            emitters.append(step)
    names = [s.get("name") for s in emitters]
    assert len(emitters) >= 2, (
        "the `changes` job must still emit the matrix from both the push step "
        f"and the tier-2 split step; discovered {names}")
    missing = [s.get("name") for s in emitters
               if "vars.CI_SHARD_ROUTING"
               not in _effective_step_env(wf, "changes", s).get("CI_SHARD_ROUTING", "")]
    assert not missing, (
        "every step that emits `fast_matrix` must see CI_SHARD_ROUTING, or the "
        f"opt-in is silently ignored on that path (#10186): {missing} of {names}")


def test_the_changes_job_carries_no_job_scope_env():
    """#10186 review ISSUE 1 (blocking): the lever may NOT live at JOB scope.

    `changes` also owns the capstone pre-registration gate step, and
    `tests/test_capstone_preregistration_9075.py` refuses ANY `env:` key in a
    scope above that step — `_SAFE_JOB_ENV` is empty for every job — because a
    key there can hook the gate's interpreter or shell (`BASH_ENV`, a
    `PYTHONPATH` `sitecustomize.py`, `LD_PRELOAD`, `PATH`) so `python3` exits 0
    with the real check never running. A job-scope lever therefore reds a
    required FAST shard: measured at head `3eaf43fda`, that pin reported 44
    failures (`ci_ordering_half_violations` returned one violation, and the
    unmutated-workflow assertion runs once per silencing-idiom parametrization).

    So the rule is DERIVED from the pin instead of restated: whatever
    `_SAFE_JOB_ENV` permits for `changes` is the entire allowance, and the lever
    is carried by each emitter STEP's own `env:` instead. A future pin edit that
    permits a key here loosens this test automatically, which is the point —
    one rule, two readers.

    Non-vacuous: at `3eaf43fda` the job carried
    `env: {CI_SHARD_ROUTING: ...}` and NEITHER emitter step carried it, so both
    assertions below fail on that tree.

    ⛔ THE NAME IS A REFERENCE SURFACE, not decoration. `docs/ops/ci-runner-x64.md:448`
    and the capstone pin's re-cut history (`tests/test_capstone_preregistration_9075.py:1590`)
    both send an operator here by name, and both advertised
    `test_the_changes_job_carries_no_job_scope_env` while the function carried a
    longer, unrelated name — so `pytest -k test_the_changes_job_carries_no_job_scope_env`
    collected ZERO tests and its green "no tests ran" read as confirmation
    (#10186 review, FINDING 1). Renamed to the name the documents already
    advertise; `test_every_test_name_the_runbook_names_exists` in
    `tests/test_ci_runner_x64_install.py` now keeps the two in sync.
    """
    from tests.test_capstone_preregistration_9075 import _SAFE_JOB_ENV

    wf = _load_python_ci()
    job = wf["jobs"]["changes"]
    allowed = set(_SAFE_JOB_ENV.get("changes", frozenset()))
    extra = sorted(set(job.get("env") or {}) - allowed)
    assert not extra, (
        "the `changes` job must not carry `env:` keys above the capstone gate "
        f"step (#10186); only {sorted(allowed)} are permitted, found {extra}")
    emitters = [s for s in job["steps"]
                if "--emit-push-matrix" in str(s.get("run") or "")
                or "--split" in str(s.get("run") or "")]
    assert len(emitters) >= 2, [s.get("name") for s in emitters]
    bare = [s.get("name") for s in emitters
            if "vars.CI_SHARD_ROUTING"
            not in str((s.get("env") or {}).get("CI_SHARD_ROUTING", ""))]
    assert not bare, (
        "each matrix emitter must carry `CI_SHARD_ROUTING` in its OWN `env:` "
        "(step scope) — inheriting it from the job is what reds the capstone "
        f"gate pin (#10186): {bare}")


def test_carve_out_mirrors_test_no_redirect_stems():
    """#4047: `carve_out:` and `TEST_NO_REDIRECT_STEMS` are one set in two homes.

    `config/ci-surfaces.yml`'s `carve_out:` routes a file to the URI-unset
    carve-out job and bars it from every docker leg; `tests/_embedded.py`'s
    `TEST_NO_REDIRECT_STEMS` is the redirect exemption that keeps a module
    embedded if it is executed with a URI set. A stem in only ONE of them is a
    silent hole, in opposite directions (see the failure message).

    Scope, stated honestly: this pin catches ONE-LIST-ONLY drift. It does NOT
    catch #4047's own shape — the fork guards were missing from BOTH lists, so
    the two sets were EQUAL then and this assertion passed on the pre-fix tree.
    That shape is caught by the source scan in
    `tests/test_markers.py::test_module_level_embedded_only_modules_are_carve_out`;
    the two guards cover different holes and neither subsumes the other.
    """
    from tests._embedded import TEST_NO_REDIRECT_STEMS
    m = load_manifest()
    # The one documented asymmetry: the bench smoke file is path-qualified in
    # the manifest (`bench/...`) and bare-stem keyed in the registry. The
    # registry/redirect mechanism is itself stem-keyed, so a collapse can only
    # happen where the stem genuinely collides.
    carve = {f.removesuffix(".py").removeprefix("bench/") for f in m["carve_out"]}
    registry = set(TEST_NO_REDIRECT_STEMS)
    assert carve == registry, (
        "carve_out and TEST_NO_REDIRECT_STEMS have drifted — a stem in only "
        "one of the two registries is a silent hole: "
        f"registry-only={sorted(registry - carve)} — in the redirect registry "
        "but NOT routed to the carve-out job, so a full (URI-set) selection "
        "collects it on a docker leg and skips its embedded_only-marked tests "
        "there; with a module-level mark that is the whole file, reported "
        "green. "
        f"carve-out-only={sorted(carve - registry)} — routed to the carve-out "
        "job but not redirect-exempt, so an out-of-band URI run would flip its "
        "embedded constructions to the server lane instead of the embedded "
        "daemon")


def test_integrity_no_matrix_drift():
    """#1472: integrity must pass with the derived matrix (no hardcoded lists)."""
    from tools.ci_selection import leg_coverage_issues, workflow_matrix_issues, REPO  # noqa: I001
    m = load_manifest()
    assert leg_coverage_issues(m) == []
    wf = REPO / ".github" / "workflows" / "python-ci.yml"
    assert workflow_matrix_issues(str(wf), m) == []


def test_split_balances_heavy_files():
    """#1473: LPT puts the heavy files on alternating halves."""
    from tools.ci_selection import split_fast_gate
    files = ["tests/test_calibration.py", "tests/test_analyze.py",
             "tests/test_main_guards.py", "tests/test_ops_safety.py"]
    durations = {"test_calibration.py": 52.1, "test_analyze.py": 3.9,
                 "test_main_guards.py": 19.5, "test_ops_safety.py": 16.8}
    a, b = split_fast_gate(files, durations)
    # LPT: calibration(52.1)->a, main_guards(19.5)->b, ops_safety(16.8)->b,
    # analyze(3.9)->b (a stays heavier after calibration).
    assert a == ["tests/test_calibration.py"]
    assert sorted(b) == ["tests/test_analyze.py", "tests/test_main_guards.py",
                         "tests/test_ops_safety.py"]
    # imbalance bounded (vs parity which could cluster 52.1+19.5 on one half)
    wa = 52.1
    wb = 19.5 + 16.8 + 3.9
    assert max(wa, wb) / min(wa, wb) < 1.5


def test_split_is_deterministic_and_ties_go_a():
    from tools.ci_selection import split_fast_gate
    files = ["tests/test_a.py", "tests/test_b.py"]
    a1, b1 = split_fast_gate(files, {"test_a.py": 1.0, "test_b.py": 1.0})
    a2, b2 = split_fast_gate(files, {"test_a.py": 1.0, "test_b.py": 1.0})
    assert a1 == a2 and b1 == b2
    assert a1 == ["tests/test_a.py"]  # tie -> a


def test_split_default_weight_for_unmeasured():
    from tools.ci_selection import split_fast_gate
    files = ["tests/test_new_a.py", "tests/test_new_b.py"]
    a, b = split_fast_gate(files, {})  # no durations -> default 2.0
    assert sorted(a + b) == sorted(files)
    assert len(a) == 1 and len(b) == 1


def test_split_rejects_non_list():
    from tools.ci_selection import split_fast_gate  # noqa: I001
    import pytest as _pytest
    with _pytest.raises(ValueError):
        split_fast_gate("ALL", {})


def test_duration_integrity():
    from tools.ci_selection import duration_issues, load_manifest
    m = load_manifest()
    assert duration_issues(m) == []
    slow_now_ok = dict(m)
    slow_now_ok["durations"] = {"test_about_edges.py": 10.0}  # a slow file
    assert duration_issues(slow_now_ok) == []
    carve_ok = dict(m)
    carve_ok["durations"] = {"test_reaper.py": 195.9}
    assert duration_issues(carve_ok) == []
    bad2 = dict(m)
    bad2["durations"] = {"not_a_real_file.py": 10.0}
    assert duration_issues(bad2) != []


# ── #3400: duration-balanced full-matrix halves + durations coverage ──────
# The push halves used to be index-parity (`fast[0::2]` / `fast[1::2]`) —
# duration-blind, so half (b) collected the slow files by luck, tilted the split
# far past the ratio the assertion below allows, and blew the 55m watchdog
# (#3400). These pin the LPT pack (#1473)
# on the full-matrix path and the coverage floor that keeps the `durations`
# map from rotting back to a handful of entries.


def _duration_manifest(heavy: dict[str, float],
                       tiny_count: int) -> dict:
    """A synthetic full-matrix manifest: a few heavy files + many 2s files,
    all in one freshly-named surface so nothing touches the real pool."""
    tiny = [f"test_tiny_{i:04d}.py" for i in range(tiny_count)]
    files = [*heavy.keys(), *tiny]
    durations = {**heavy, **{f: 2.0 for f in tiny}}
    return {"surfaces": {"core": files}, "tier1": [], "slow_files": [],
            "carve_out": [], "push_extra": [], "durations": durations}


def test_full_matrix_split_is_duration_balanced():
    """#3400: the full-matrix (push) halves are packed by measured duration.

    Four heavy files + many 2s files: parity can cluster the heavies on one
    half; LPT must not.  The assertion is the *duration* ratio, not a count
    ratio — a correct pack of the real pool carries unequal counts.
    """
    from tools.ci_selection import HALF_DURATION_IMBALANCE_RATIO, push_legs
    heavy = {"test_h0.py": 850.0, "test_h1.py": 700.0,
             "test_h2.py": 650.0, "test_h3.py": 600.0}
    m = _duration_manifest(heavy, tiny_count=200)
    legs = push_legs(m)
    # the synthetic manifest declares no `fast_shards`, so the default (2)
    # applies and `a`/`b` are the whole set — the LPT balance assertions below
    # are the two-shard case of the same rule.
    by_name = {s["name"]: set(s["files"]) for s in legs["shards"]}
    assert set(by_name) == {"a", "b"}, by_name.keys()
    a, b = by_name["a"], by_name["b"]
    assert not (a & b), "leg overlap"
    assert a | b == {f[:-3] for f in m["surfaces"]["core"]}, "coverage hole"
    weights = {h: sum(m["durations"][f + ".py"] for f in fs)
               for h, fs in (("a", a), ("b", b))}
    ratio = max(weights.values()) / min(weights.values())
    assert ratio <= HALF_DURATION_IMBALANCE_RATIO, (
        f"parity-style tilt survived: { {h: round(w / 60, 1) for h, w in weights.items()} }"
        f" min (ratio {ratio:.2f}x)")
    # the heavy files must be SPREAD — the 850s file must not sit with every
    # other heavy file on one half while the other side carries only 2s files.
    a_heavy = {f for f in heavy if f[:-3] in a}
    b_heavy = {f for f in heavy if f[:-3] in b}
    assert a_heavy and b_heavy, (
        f"heavy files clustered on one half: a={sorted(a_heavy)} b={sorted(b_heavy)}")
    assert len(a_heavy) < len(heavy) and len(b_heavy) < len(heavy)
    # and the OLD parity split of the same pool is the thing being fixed
    order = sorted(m["surfaces"]["core"])
    p_a, p_b = order[0::2], order[1::2]
    p_wa = sum(m["durations"][f] for f in p_a)
    p_wb = sum(m["durations"][f] for f in p_b)
    parity_ratio = max(p_wa, p_wb) / min(p_wa, p_wb)
    assert parity_ratio > ratio, (
        f"fixture does not exercise the defect: parity {parity_ratio:.2f}x "
        f"vs LPT {ratio:.2f}x")


def test_push_legs_is_deterministic():
    """#3400: same manifest -> byte-identical halves, repeated calls."""
    from tools.ci_selection import push_legs
    m = _duration_manifest({"test_h0.py": 850.0, "test_h1.py": 700.0}, 50)
    first = push_legs(m)
    assert push_legs(m) == first
    assert push_legs(m) == first
    real = load_manifest()
    assert push_legs(real) == push_legs(real)


def test_halves_duration_imbalance_flagged():
    """#3400: a heavy file dumped on one half reds even when counts look even."""
    from tools.ci_selection import workflow_halves_issues
    m = _duration_manifest({"test_big.py": 600.0}, tiny_count=10)
    # 5 vs 6 files — a count-balanced split, duration-lopsided
    halves = {"a": ["test_big", "test_tiny_0000", "test_tiny_0002",
                     "test_tiny_0004", "test_tiny_0006"],
              "b": ["test_tiny_0001", "test_tiny_0003", "test_tiny_0005",
                     "test_tiny_0007", "test_tiny_0008", "test_tiny_0009"]}
    issues = workflow_halves_issues(m, halves)
    assert any("duration-imbalanced" in i for i in issues), issues


def test_duration_coverage_guard_fires_when_low():
    """#3400: 15 weights for 100 fast files is the rot this guard forbids."""
    from tools.ci_selection import duration_coverage_issues
    m = _duration_manifest({}, tiny_count=0)
    m["surfaces"]["core"] = [f"test_cov_{i:03d}.py" for i in range(100)]
    m["durations"] = {f: 2.0 for f in m["surfaces"]["core"][:15]}
    issues = duration_coverage_issues(m)
    assert issues, "guard did not bite at 15% coverage"
    assert any("15.0%" in i and "floor" in i for i in issues), issues


def test_duration_coverage_guard_boundary_and_realistic():
    """#3400: the floor is inclusive; realistic coverage is silent."""
    from tools.ci_selection import duration_coverage_issues, load_manifest
    files = [f"test_cov_{i:03d}.py" for i in range(100)]
    below = {"surfaces": {"core": files}, "tier1": [], "slow_files": [],
             "durations": {f: 2.0 for f in files[:89]}}
    at = {"surfaces": {"core": files}, "tier1": [], "slow_files": [],
          "durations": {f: 2.0 for f in files[:90]}}
    above = {"surfaces": {"core": files}, "tier1": [], "slow_files": [],
             "durations": {f: 2.0 for f in files[:95]}}
    assert duration_coverage_issues(below) != [], "89% must fire"
    assert duration_coverage_issues(at) == [], "90% is at the floor, not below"
    assert duration_coverage_issues(above) == [], "95% must be silent"
    assert duration_coverage_issues(load_manifest()) == []


# The refresher (`tools/ci_timing.py::render_refreshed_manifest`) rewrites a row's
# VALUE and preserves its trailing comment verbatim, so a note that states WHERE
# the number came from can outlive the basis it states — the file ships a rule about
# it above the `durations:` block ("a row's comment is kept only while the value AND
# its stated basis are unchanged"). Only the mechanically checkable half is pinned
# here: a row whose value is NOT the flat default must not say it is the default.
# The COUNTERFACTUAL forms are legitimate and must stay silent — "without the row
# the file packs at the flat 2.0s default", "its absence packs it at the flat
# 2.0s default", "a real weight, not the flat 2.0 default" — so the claim is
# recognised by its VERB (`takes`/`carries`/`holds`/`is` … default), not by the
# phrase alone. #8254 review P3 found 3 rows stale this way; the 2026-10-17 refresh
# (#2876) found 5 more, 2 of them of this shape.
_DURATIONS_ROW_RE = re.compile(
    r"^  (?P<key>[^\s:#]+\.py):\s+(?P<value>[0-9.]+)(?P<tail>.*)$")
_FLAT_DEFAULT_CLAIM_RE = re.compile(
    # A claim about THIS row's weight: "it takes/carries/holds the … flat 2.0s
    # default", or "the weight is the … flat 2.0s default". The bare copula form is
    # anchored to a determiner so a note that merely SAYS `is` somewhere and then
    # describes the counterfactual ("the file is in the fast pool, so its absence
    # packs it at the flat 2.0s default") is not read as a claim.
    r"\b(?:takes|carries|holds)\b[^.;]{0,60}?flat\s+2\.0s?\s+default"
    r"|\bis\s+(?:the|only|just|still)\s+(?:\S+\s+){0,2}?flat\s+2\.0s?\s+default",
    re.IGNORECASE)


def _durations_rows_claiming_the_flat_default(text: str, durations: dict):
    """Rows that claim the flat default as their OWN basis for a non-default value."""
    from tools.ci_selection import DEFAULT_FAST_WEIGHT
    out = []
    for line in text.splitlines():
        m = _DURATIONS_ROW_RE.match(line)
        if m is None or m.group("key") not in durations:
            continue
        value = float(m.group("value"))
        if abs(value - DEFAULT_FAST_WEIGHT) < 1e-9:
            continue
        if _FLAT_DEFAULT_CLAIM_RE.search(m.group("tail")):
            out.append((m.group("key"), value))
    return out


def test_no_durations_row_claims_the_flat_default_at_a_measured_value():
    """#2876/#8254 (P3): a re-derived value must not keep its old basis note."""
    from tools.ci_selection import load_manifest
    text = cs.MANIFEST.read_text(encoding="utf-8")
    offenders = _durations_rows_claiming_the_flat_default(
        text, load_manifest()["durations"])
    assert offenders == [], (
        "a durations row states the #3400 flat 2.0s default as its basis while "
        "carrying a different value — the refresh re-derived the row and the note "
        f"went stale: {offenders}. Rewrite the note to the new basis (or drop the "
        "claim), keeping any #4348 do-not-drop instruction.")


def test_flat_default_claim_check_is_not_vacuous():
    """The guard above must bite on the stale shape and stay quiet on its siblings."""
    stale = (
        "  test_a.py: 2.2  # a new file takes the #3400 flat 2.0s default."
        "\n"
        "  test_b.py: 3.7  # so it takes the #3400 flat 2.0s default; required.")
    legitimate = (
        "  test_c.py: 7.2  # unmeasured — without this row the file packs at the"
        " flat 2.0s default.\n"
        "  test_d.py: 18.0  # its absence packs it at the flat 2.0s default.\n"
        "  test_e.py: 140.3  # a real weight, not the flat 2.0 default.\n"
        "  test_f.py: 2.0  # this row IS the flat 2.0s default, and unmeasured.")
    durations = {f"test_{c}.py": 1.0 for c in "abcdef"}
    assert _durations_rows_claiming_the_flat_default(stale, durations) == [
        ("test_a.py", 2.2), ("test_b.py", 3.7)]
    assert _durations_rows_claiming_the_flat_default(legitimate, durations) == []


_DURATIONS_TOP_KEY_RE = re.compile(r"^[a-z_]+:\s*$")


def _durations_row_comments(text: str):
    """`(key, joined comment)` for every row of the `durations` map, wraps joined.

    A row's comment is the tail after its value PLUS its indented `#` continuation
    lines. The map's long notes are wrapped across lines, so reading only the row
    line both misses damage that sits on a continuation line and reads a pair that
    wraps as unbalanced. Continuations are unambiguous here: the block carries no
    standalone comment line (`durations:` is followed by rows and wrap lines only).
    """
    out = []
    lines = text.splitlines()
    try:
        start = next(i for i, ln in enumerate(lines) if ln.strip() == "durations:")
    except StopIteration:  # pragma: no cover — the map always declares the block
        return out
    end = next((i for i in range(start + 1, len(lines))
                if _DURATIONS_TOP_KEY_RE.match(lines[i])), len(lines))
    current = None
    for i in range(start + 1, end):
        m = _DURATIONS_ROW_RE.match(lines[i])
        if m is not None:
            current = [m.group("key"), [m.group("tail")]]
            out.append(current)
            continue
        stripped = lines[i].strip()
        if current is not None and stripped.startswith("#"):
            current[1].append(stripped.lstrip("#"))
        elif stripped:
            current = None
    return [(key, " ".join(chunks)) for key, chunks in out]


def _durations_rows_with_unbalanced_parens(text: str):
    """Rows whose note carries a `(` or `)` whose partner is nowhere in the note."""
    return [(key, note.strip()) for key, note in _durations_row_comments(text)
            if note.count("(") != note.count(")")]


def test_no_durations_row_keeps_half_a_parenthesis():
    """#10055 review (P3): a clause cut may not orphan a parenthesis.

    `tools/ci_timing.py`'s clause cutters drop a stale clause whole; when that
    clause carried one half of a paren pair the survivor kept the other half, e.g.
    `test_url_keyed_source_write_4649.py` -> `#4649: 169.5s wall for the whole file)`
    and `test_7951_enforce_atomic_write.py` -> `#7951: … 7.39s wall (measured at the
    rebase head — ci-timing …` with no closer. Measured on the refresh head: 18 rows
    against 2 on main. Every surviving claim is still TRUE (that is why this is P3
    and not a behaviour defect), but a dangling half reads as a typo and hides that
    a sentence was cut — this map treats a damaged note as a defect, not cosmetics.
    """
    text = cs.MANIFEST.read_text(encoding="utf-8")
    offenders = _durations_rows_with_unbalanced_parens(text)
    assert offenders == [], (
        "a durations row's note keeps half a parenthesis — a clause was cut without "
        f"its partner (#10055 review, P3): {[k for k, _ in offenders]}. Drop the "
        "orphaned half (`tools/ci_timing._balance_cut_parens` does this on every cut).")


def test_the_paren_guard_is_not_vacuous():
    """The guard above must bite on a cut casualty and stay quiet on its siblings."""
    stale = (
        "durations:\n"
        "  test_a.py: 4.3  # #4649: 169.5s wall for the whole file); the floor "
        "fails by construction.\n"
        "  test_b.py: 7.4  # #7951: 19 tests, 7.39s wall (measured at the rebase head "
        "— ci-timing junit 2026-10-26; was 7.4\n"
        "surfaces: []\n")
    legitimate = (
        "durations:\n"
        "  test_c.py: 1.0  # a note with no parens at all.\n"
        "  test_d.py: 2.0  # says (a balanced thing) and nothing else.\n"
        "  test_e.py: 3.0  # a pair that WRAPS across lines (opened here\n"
        "      # and closed on the continuation line).\n"
        "surfaces: []\n")
    assert [k for k, _ in _durations_rows_with_unbalanced_parens(stale)] == [
        "test_a.py", "test_b.py"]
    assert _durations_rows_with_unbalanced_parens(legitimate) == []


#: `WATCHDOG_FLOOR_MIN` as a row NOTE states it: "`WATCHDOG_FLOOR_MIN` (19 min)".
_WATCHDOG_FLOOR_CLAIM_RE = re.compile(r"`WATCHDOG_FLOOR_MIN`\s*\((?P<minutes>\d+) min\)")


def _durations_rows_stating_a_stale_watchdog_floor(text: str, floor: int):
    """Rows whose OWN note attaches a `WATCHDOG_FLOOR_MIN` that is not the current one.

    The row's own tail only, never its continuation lines: a wrapped continuation can
    belong to a comment BLOCK that describes the row BELOW it (the `355.0` cap block
    sits *above* its `test_lane_stash.py` row), so reading continuations here would
    attribute a neighbour's prose to the wrong key.
    """
    out = []
    for line in text.splitlines():
        m = _DURATIONS_ROW_RE.match(line)
        if m is None:
            continue
        for claim in _WATCHDOG_FLOOR_CLAIM_RE.finditer(m.group("tail")):
            if int(claim.group("minutes")) != floor:
                out.append((m.group("key"), int(claim.group("minutes"))))
    return out


def test_no_durations_row_states_a_retired_watchdog_floor():
    """#10044 review F3 (P2): the two cap rows still asserted the 15-min basis.

    `WATCHDOG_FLOOR_MIN` moved 15 -> 19 (#10055, its own 2.9x derivation), which
    falsifies every clause the `test_calibration.py` and `test_write_path_operator_lane.py`
    notes were written at 15 min: at 19 min = 1140 s, 1140/390.0 = 2.92x and
    1140/386.2 = 2.95x CLEAR the 2.5x those rows said they failed, so both readings
    ARE declarable and the arithmetic ceiling is <456 s, not <360 s. The file's own
    header rule ("a row's comment is kept only while the value AND its stated basis
    are unchanged") is what the rows violated; no guard covered prose of this shape.

    Only the mechanically checkable half is pinned — the number a note attaches to the
    CONSTANT, on the row's own line. A row may still NARRATE the retired floor ("at the
    15-min floor this cap was chosen under"), because that sentence is history, not a
    claim about the file today; it must not state the retired number AS the constant's
    value.
    """
    from tools.ci_selection import WATCHDOG_FLOOR_MIN
    text = cs.MANIFEST.read_text(encoding="utf-8")
    offenders = _durations_rows_stating_a_stale_watchdog_floor(text, WATCHDOG_FLOOR_MIN)
    assert offenders == [], (
        "a durations row states a retired `WATCHDOG_FLOOR_MIN` — the floor moved and "
        f"the note's stated basis did not (#10044 review F3): {offenders}. Restate the "
        "row at the current floor, keeping every ⛔ do-not-read-355.0 instruction and "
        "the #9172 pointer.")


def test_the_watchdog_floor_guard_is_not_vacuous():
    """The guard above must bite on the pre-move rows and stay quiet on the rewrite."""
    stale = (
        "durations:\n"
        "  test_a.py: 355.0  # #6133 CEILING, not a reading: `WATCHDOG_FLOOR_MIN` "
        "(15 min) must clear the largest fast file by 2.5x.\n"
        "  test_b.py: 1.0  # a row that names no floor at all.\n"
        "surfaces: []\n")
    legitimate = (
        "durations:\n"
        "  test_c.py: 355.0  # the #6133 rule is that `WATCHDOG_FLOOR_MIN` (19 min) "
        "must clear the largest fast file by 2.5x. At the 15-min\n"
        "      # floor this cap was chosen under, 900/360 = 2.5 exactly.\n"
        "  test_d.py: 0.2\n"
        "surfaces: []\n")
    assert [k for k, _ in _durations_rows_stating_a_stale_watchdog_floor(stale, 19)] == [
        "test_a.py"]
    assert _durations_rows_stating_a_stale_watchdog_floor(legitimate, 19) == []


def test_2818_new_file_row_carries_the_flat_default_until_a_sweep_collects_it():
    """#9003 review (P2): a NEW file's first weight is the flat default, not a local timing.

    The `durations` header above `durations:` prescribes `DEFAULT_FAST_WEIGHT` for a file
    no sweep has collected yet (the #3419 `test_venture_pack.py` precedent) because a
    weight is a SHARD-LAYOUT input: a locally-measured number decides which files share
    a shard, so it must not be hand-picked. The #2818 row shipped a measured-on-this-box
    4.9 first, under a comment that CLAIMED the flat-default convention it violated — and
    the guard above could not see it, because the claim sat on the comment lines PRECEDING
    the row rather than on its tail. Pinned by name here: the row is MARGIN on the
    coverage floor, not the floor itself (`duration_coverage_issues()` is count-based and
    passes either way — 90.01% without the row, 90.12% with it), so its MAGNITUDE must be
    the flat default while its absence is a derivation to re-run, not an automatic red.
    """
    from tools.ci_selection import DEFAULT_FAST_WEIGHT, load_manifest
    row = "test_pack_value_declarations_2818.py"
    durations = load_manifest()["durations"]
    assert row in durations, (
        "the #2818 row is missing from `durations` — its presence is the margin the "
        "#3400 coverage floor has (90.01% without it, still above the 0.90 floor, so "
        "treat its absence as a finding to re-derive, not an automatic red)")
    # Scoped to the UNMEASURED state on purpose: once a sweep collects the file,
    # `tools/ci_timing.py::render_refreshed_manifest` rewrites the value and CLEARS a
    # tail that LEADS with the `# unmeasured` marker (that is how the #3419/#2788
    # precedent rows work), and this pin must not then fail on a legitimate CI
    # measurement. The marker is read exactly the way the refresher reads it — by its
    # leading token — so the two cannot disagree: any OTHER trailing comment is
    # preserved verbatim by the refresh, including one that mentions the word later.
    if not _ROW_LEADS_WITH_UNMEASURED.match(
            _durations_row_tail("test_pack_value_declarations_2818.py")):
        assert durations[row] > 0, (
            f"{row} carries a non-positive weight {durations[row]}s — a sweep has "
            "collected the file, so its value must be a real measurement")
        return
    assert durations[row] == DEFAULT_FAST_WEIGHT, (
        f"{row} carries {durations[row]}s, but a NEW file's first weight is the flat "
        f"{DEFAULT_FAST_WEIGHT}s default until a CI sweep collects it. Do not substitute "
        "a local timing: a weight is a shard-layout input, so a number that does not "
        "model CI decides which files run together (#3419, #9003 review).")


# The refresher clears a `# unmeasured` marker by its LEADING TOKEN (see
# `tools/ci_timing.py::render_refreshed_manifest`), so the pin below reads it the
# same way rather than by substring.
_ROW_LEADS_WITH_UNMEASURED = re.compile(r"[ \t]*#\s*unmeasured\b")


def _durations_row_tail(key: str) -> str:
    """The tail comment of a `durations:` row, or "" when the row is absent."""
    for line in cs.MANIFEST.read_text(encoding="utf-8").splitlines():
        m = _DURATIONS_ROW_RE.match(line)
        if m is not None and m.group("key") == key:
            return m.group("tail")
    return ""


def test_duration_coverage_guard_backwards_compatible():
    """#3400: an absent/empty durations map is never a hard failure."""
    from tools.ci_selection import duration_coverage_issues
    files = [f"test_cov_{i:03d}.py" for i in range(100)]
    base = {"surfaces": {"core": files}, "tier1": [], "slow_files": []}
    assert duration_coverage_issues(base) == []              # key absent
    assert duration_coverage_issues({**base, "durations": {}}) == []   # empty
    assert duration_coverage_issues({**base, "durations": None}) == []  # null
    # a repo with no fast files at all must not divide by zero
    assert duration_coverage_issues(
        {"surfaces": {}, "tier1": [], "slow_files": [],
         "durations": {"x.py": 1.0}}) == []


# ── #1668: the P2 flip's workflow-wiring pins (epic #1647 Task 6) ─────────
# cycle-7 P2-1 (test_expect_uri_gated_iff_uri) + cycle-8 P2-12
# (test_live_required_job_runs_only_declared_live_tests): YAML-parsing
# pins on .github/workflows/python-ci.yml so the flip's invariants cannot
# silently regress — a half-b URI decoupled from the EXPECT_URI tripwire
# signal, or a third live test added to test-concurrency-falkor without the
# audit, reds at PR time.

import re as _re  # noqa: E402


def _load_python_ci() -> dict:
    import yaml
    wf_path = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "python-ci.yml"
    return yaml.safe_load(wf_path.read_text())


def _effective_step_env(wf: dict, job_id: str, step: dict) -> dict:
    """The env a STEP actually sees, as GitHub Actions composes it.

    Workflow -> `jobs.<id>.env` -> `jobs.<id>.steps[*].env`, each scope
    overriding the one above (`docs.github.com` -> "Store information in
    variables": "The scope of a custom variable set by this method is limited to
    the element in which it is defined"). A test that reads only
    `step["env"]` therefore asserts the narrower thing than it claims: it pins
    WHERE the variable is written instead of THAT it reaches the step, which is
    how #10186 shipped a lever wired to one of two emitters.
    """
    merged = {k: str(v) for k, v in (wf["jobs"][job_id].get("env") or {}).items()}
    merged.update({k: str(v) for k, v in (step.get("env") or {}).items()})
    return merged


def test_expect_uri_gated_iff_uri():
    """cycle-7 P2-1 + Task 9 (P3): the docker URI and the E2E-6 tripwire
    signal (TORTOISE_TEST_EXPECT_URI) are set under the SAME gate —
    full==true on BOTH halves (Task 9 flipped half a; the embedded lane is
    retired from the main test job) — and mapped onto the pytest step's env
    together. A future edit that decouples them (URI without the tripwire
    signal, the signal without the URI, or a half-specific gate) reds."""
    wf = _load_python_ci()
    steps = wf["jobs"]["test"]["steps"]
    compute = next(s for s in steps
                   if "Compute docker URI" in s.get("name", ""))
    script = compute["run"]
    # Task 9 (P3): the gate is full==true ONLY — both halves ride the
    # provisioned service; a half-specific gate would resurrect the
    # embedded canary half. Tier-2 PRs (full=false) stay URI-less.
    gate = ('if [ "${{ needs.changes.outputs.full }}" = "true" ]; then')
    assert gate in script, "the docker gate must be full==true (both halves)"
    assert "matrix.half" not in script, \
        "the URI gate must NOT be half-specific (Task 9 flipped BOTH halves)"
    then_block = script.split("then", 1)[1].split("fi", 1)[0]
    # #6673: the host port is assigned at runtime by the provision step, so the
    # URI expands $TORTOISE_TEST_DOCKER_PORT. What this assertion protects is
    # unchanged: the passworded docker:// shape and the test-prefixed path.
    assert ('URI="docker://:falkordb@localhost:'
            '${TORTOISE_TEST_DOCKER_PORT') in then_block
    assert '/tortoise_test_matrix"' in then_block
    assert 'EXPECT_URI="1"' in then_block
    assert 'echo "URI=$URI" >> "$GITHUB_ENV"' in script
    assert 'echo "EXPECT_URI=$EXPECT_URI" >> "$GITHUB_ENV"' in script
    # Iff BOTH directions: the assignments must NOT appear anywhere else in
    # the script (outside the gate's then-block), so a future edit that sets
    # URI or EXPECT_URI on any other shape reds.
    outside_then = script.split("then", 1)[1].split("fi", 1)[1]
    assert 'URI="' not in outside_then and 'EXPECT_URI="' not in outside_then, \
        "URI/EXPECT_URI must be set ONLY inside the full==true gate"
    # The pytest run step maps BOTH onto its env — iff at the YAML level.
    run = next(s for s in steps
               if s.get("name", "").startswith("Run fast test suite"))
    env = run["env"]
    assert env["TORTOISE_DB_URI"] == "${{ env.URI }}"
    assert env["TORTOISE_TEST_EXPECT_URI"] == "${{ env.EXPECT_URI }}"
    # Task 9 / #6135: the coverage manifest is generated on EVERY docker shard
    # (no half-specific `if:` gate on the manifest step), and the skip-guard
    # step's manifest mode is gated on rc==0 AND
    # non-empty $FILES (plan-review P1-7 — the "no selected files" path
    # writes rc=0 with no junitxml; with a manifest that would false-red).
    manifest = next(s for s in steps
                    if s.get("name", "").startswith("Generate coverage manifest"))
    assert "matrix.half" not in manifest.get("if", ""), \
        "the manifest step must run on BOTH halves (P3)"
    guard = next(s for s in steps
                 if s.get("name", "").startswith("Skip-fail guard"))
    assert '-s "${RUNNER_TEMP:-/tmp}/pytest-files"' in guard["run"]
    # ⛔ THE GUARD MUST READ THE MANIFEST THE MANIFEST STEP WROTE — the PATH is not
    # the contract, the PAIRING is (2026-10-25). Both now ride the job's
    # `$PYTEST_MANIFEST` (= `${{ runner.temp }}/expected-nodeids.txt`) instead of the
    # global `/tmp/expected-nodeids.txt`: on a self-hosted runner several jobs share
    # ONE `/tmp`, so a fixed name let this guard compare against ANOTHER job's
    # manifest and red on a mismatch belonging to no test it ran (#7815). Asserting
    # the literal would re-open that clobber; asserting the pairing is what keeps
    # the guard honest once the path is per-runner.
    assert "--manifest \"$PYTEST_MANIFEST\"" in guard["run"]
    assert (
        '--output "$PYTEST_MANIFEST"' in manifest["run"]
    ), "the manifest step and the skip-fail guard must share ONE path variable"
    # The canary producer is gated to half b + post-merge (cycle-5 P1-7
    # option (b): exactly ONE leg writes, no last-writer-wins clobber).
    # #4367: the nightly schedule trigger was removed, so post-merge is
    # `push` alone — the gate no longer names the retired event.
    producer = next(s for s in steps
                    if s.get("name", "").startswith("Canary producer"))
    assert producer["if"] == "github.event_name == 'push'", \
        "producer must be post-merge only"
    # #6135: the one-writer gate is the matrix's ROLE flag, never a shard name
    # — `half == 'b'` stopped naming a producer the moment S rose.
    assert 'if [ "${{ matrix.canary_producer }}" = "true" ]; then' in producer["run"], \
        "the producer must be gated on the canary_producer role (one writer)"


def test_carve_out_env_gated_inverse_of_uri():
    """Epic #1647 Task 10 (P4, P1-9): the URI-required enforcement's CI
    wiring. The compute step sets TORTOISE_TEST_CARVE_OUT=1 iff full==false
    (the tier-2 URI-less embedded shape) — the EXACT inverse of the docker
    URI gate — and the pytest run steps map it onto their env. A future
    edit that drops the tier-2 opt-in reds every tier-2 PR (the enforcement
    would fail the URI-less session); one that sets CARVE_OUT on the docker
    lane would disable the enforcement exactly where it matters."""
    wf = _load_python_ci()
    for job_name in ("test", "test-slow"):
        steps = wf["jobs"][job_name]["steps"]
        compute = next(s for s in steps
                       if "Compute docker URI" in s.get("name", ""))
        script = compute["run"]
        gate = ('if [ "${{ needs.changes.outputs.full }}" = "true" ]; then')
        assert gate in script
        # the docker then-branch runs from the gate keyword to the bash
        # `else` — CARVE_OUT must never be set there (URI satisfies the
        # enforcement on the docker lane)
        then_branch = script.split("then", 1)[1].split("else", 1)[0]
        assert 'CARVE_OUT="1"' not in then_branch, \
            f"{job_name}: CARVE_OUT must NOT be set on the docker lane " \
            "(URI is set there — the enforcement is satisfied by the URI)"
        assert script.count('CARVE_OUT="1"') == 1, \
            f"{job_name}: exactly one CARVE_OUT=\"1\" assignment (the tier-2 " \
            "else branch — the URI-less embedded shape opts in)"
        assert 'CARVE_OUT=""' in script, f"{job_name}: CARVE_OUT declared empty"
        assert 'echo "CARVE_OUT=$CARVE_OUT"' in script
        run = next(s for s in steps
                   if s.get("name", "").startswith("Run fast test suite")
                   or s.get("name", "").startswith("Run slow test suite"))
        assert run["env"]["TORTOISE_TEST_CARVE_OUT"] == "${{ env.CARVE_OUT }}", \
            f"{job_name}: the run step must map TORTOISE_TEST_CARVE_OUT"


def test_carve_out_lane_provisions_the_embedder_offline():
    """#4387: the carve-out lane runs the dense leg, so it must provision the
    embedder the way every other suite-running job does (cache + REQUIRED
    provision) and run the suite OFFLINE.

    Measured failure this pins: with the suite-wide egress guard installed and
    no HF_HUB_OFFLINE on this lane, an uncached `SentenceTransformer()` load
    RETRIES against huggingface.co instead of failing fast, and
    test_longmem_runner.py re-attempts the load once per test — so the shard is
    killed by the 15m watchdog with 0 failures (rc=124), leaving the merge rail
    no failure identity to read (#6798). The request/backoff/test counts
    observed on the pre-`pytest_configure` revision are not restated here: this
    docstring and two comments carried three copies of them, which is what
    drifts. A lane that
    instead SKIPS the dense assertion trips the skip-guard's
    embedder-unavailable family (#2573). Both outcomes are reds, so the lane
    must be provisioned, not merely offline.
    """
    wf = _load_python_ci()
    steps = wf["jobs"]["test-carve-out"]["steps"]
    names = [s.get("name") or "" for s in steps]
    cache_i = next(i for i, n in enumerate(names)
                   if n.startswith("Cache HF embedding model"))
    prov_i = next(i for i, n in enumerate(names)
                  if n.startswith("Embedding model REQUIRED"))
    run_i = next(i for i, n in enumerate(names)
                 if n.startswith("Run carve-out suite"))
    assert cache_i < prov_i < run_i, \
        "the carve-out lane must cache + provision the embedder before the suite"
    env = steps[run_i].get("env") or {}
    assert env.get("HF_HUB_OFFLINE") == "1", \
        "the carve-out suite step must set HF_HUB_OFFLINE=1"
    assert env.get("TRANSFORMERS_OFFLINE") == "1", \
        "the carve-out suite step must set TRANSFORMERS_OFFLINE=1"


def test_pmv_job_carries_uri_manifest_guard():
    """Epic #1647 Task 10 Step 1a (P1-9 + cycle-2 P2-14 + cycle-4 P2-11):
    post-merge-validation is now a docker lane — job-level TORTOISE_DB_URI +
    EXPECT_URI, falkordb services, a manifest-generation step BEFORE the
    pytest step replicating the run's OWN excludes (--ignore tests/e2e + the
    SLOW_IGNORES list + `-m not track_b`), the run emitting junitxml with
    -r fEs (never -rfE), and a skip-guard step gated on rc==0 with the
    junitxml-reconciled manifest. A manifest built without the run's
    excludes expects e2e/slow nodeids the run never produces and every
    merge reds on vanished nodeids."""
    import yaml as _yaml
    wf_path = (Path(__file__).resolve().parents[1] / ".github" / "workflows"
               / "post-merge-validation.yml")
    wf = _yaml.safe_load(wf_path.read_text())
    job = wf["jobs"]["validate"]
    steps = job["steps"]
    # #6673: the URI can no longer be a job-level literal — the host port is
    # assigned at runtime — so the SAME invariants (docker lane, test-prefixed
    # path, BOTH probe families provisioned) are now carried by the provision
    # step. A `services:` block must never come back: its fixed host ports are
    # the mechanism whose collision produced the false reds.
    assert "services" not in job, \
        "pmv must not go back to a `services:` block (fixed host ports, #6673)"
    assert "TORTOISE_DB_URI" not in job.get("env", {}), \
        "the pmv URI is exported at runtime by the provision step, not a literal"
    assert job["env"]["TORTOISE_TEST_EXPECT_URI"] == "1"
    provision = next(s for s in steps
                     if str(s.get("uses", "")).endswith("falkordb-provision"))
    assert provision["with"]["uri_graph"] == "tortoise_test_matrix"
    # #6386: this used to assert `:latest` — i.e. it GUARDED the drift instead
    # of catching it, so the tag was free to float to engine 6.x while every
    # other job ran the v4.20.6 pin. It must name the same immutable engine the
    # gate uses (pinned across all provisioned jobs in
    # tests/test_runner_ports_6673.py).
    assert provision["with"]["image"] == "falkordb/falkordb-server:v4.20.6"
    assert provision.get("if") == "needs.dedup-check.outputs.skip != 'true'"
    assert any(str(s.get("uses", "")).endswith("falkordb-teardown")
               and s.get("if") == "always()" for s in steps), \
        "pmv must tear the services down even when the job fails"
    run = next(s for s in steps
               if s.get("name", "").startswith("Run tests"))
    invocation = run["run"]
    assert "--junitxml=/tmp/pmv-junit.xml" in invocation
    assert "-o junit_family=xunit1" in invocation
    cmdline = next(line for line in invocation.splitlines()
                   if "python -m pytest" in line)
    assert "-r fEs" in cmdline and "-rfE" not in cmdline, \
        "pmv must use -r fEs (the skip-summary superset), never -rfE"
    assert "--ignore=tests/e2e" in cmdline
    assert "$SLOW_IGNORES" in cmdline
    assert "-m 'not track_b'" in cmdline
    assert "pmv-rc" in invocation
    manifest = next(s for s in steps
                    if s.get("name", "").startswith("Generate coverage manifest"))
    assert steps.index(manifest) < steps.index(run), \
        "the pmv manifest collect-only must run BEFORE pytest"
    mrun = manifest["run"]
    assert "--emit-manifest \"tests/\"" in mrun
    assert "--marker \"not track_b\"" in mrun
    assert "--ignore tests/e2e" in mrun
    assert "$SLOW_IGNORES" in mrun
    guard = next(s for s in steps
                 if s.get("name", "").startswith("Skip-fail guard"))
    grun = guard["run"]
    assert "--junitxml=/tmp/pmv-junit.xml" in grun
    assert "--manifest /tmp/pmv-expected-nodeids.txt" in grun
    assert '"$RC" = "0"' in grun, "the guard is gated on pytest rc==0"


def test_live_required_job_runs_only_declared_live_tests():
    """cycle-8 P2-12: test-concurrency-falkor runs EXACTLY the declared
    live-test set — as of #5379 that is THREE tests:
    tests/test_event_store.py::test_seq_is_monotonic_under_concurrency_live_falkor,
    tests/test_event_store.py::test_next_seq_ceiling_is_atomic_across_workers_live_falkor
    and tests/test_embedded_concurrency.py::test_concurrent_writers_live_falkor_no_lost_writes.
    A further live test added to the job without extending this pin reds (the
    audit's executable form — the old audit named two test files but grepped
    only test_embedded_concurrency, so a new live test in test_event_store
    was invisible; #5379 added the third through this pin)."""
    wf = _load_python_ci()
    job = wf["jobs"]["test-concurrency-falkor"]
    steps = job["steps"]
    run = next(s for s in steps
               if s.get("name", "").startswith("Run live concurrency tests"))
    nodeids = _re.findall(r"tests/[A-Za-z0-9_/.]+\.py::[A-Za-z0-9_]+", run["run"])
    declared = {
        "tests/test_event_store.py::test_seq_is_monotonic_under_concurrency_live_falkor",
        "tests/test_event_store.py::test_next_seq_ceiling_is_atomic_across_workers_live_falkor",
        "tests/test_embedded_concurrency.py::test_concurrent_writers_live_falkor_no_lost_writes",
    }
    assert set(nodeids) == declared, (
        f"test-concurrency-falkor must run exactly {sorted(declared)}, got "
        f"{sorted(set(nodeids))} (a new live test needs the audit + the "
        "test-prefixed job URI, epic #1647 cycle-8 P2-12)"
    )
    # The job-level URI stays the NON-test-prefixed path — inert because
    # all three live tests pass explicit test-prefixed graph names and never
    # bulk-wipe the URI default (verified test_embedded_concurrency
    # L111-153 + test_event_store L165-185 and the #5379 ceiling test's
    # `test_seq_ceiling_tortoise`). If a future test adds a
    # path=/URI-default DETACH, the job URI must gain the P1-2 test-prefixed
    # path.
    assert "TORTOISE_DB_URI" not in job.get("env", {}), \
        "#6673: the carrier moved to the provision step (runtime host port)"
    provision = next(s for s in steps
                     if str(s.get("uses", "")).endswith("falkordb-provision"))
    assert provision["with"]["uri_graph"] == "tortoise"
    # The skip-fail guard must name EVERY declared live test, or a new test
    # can skip while the regex stays green (the exact gap #5379 fixed).
    guard_lines = [ln for ln in run["run"].splitlines() if "SKIPPED" in ln]
    assert guard_lines, "the job must carry a skip-fail guard"
    guard_text = "\n".join(guard_lines)
    for nodeid in declared:
        assert nodeid.split("::", 1)[1] in guard_text, (
            f"the skip-fail guard must name {nodeid} — a live test missing from "
            f"the regex can SKIP while the job stays green (vacuity)")


def test_test_slow_job_carries_junitxml_manifest_guard():
    """cycle-7 P2-1 (int-1) + Task 9 Step 5: the test-slow legs flip to
    docker with the fast job's exact wiring — (a) the pytest invocation
    carries --junitxml="$PYTEST_JUNIT" -o junit_family=xunit1 AND -r fEs
    (never the historical -rfE — pytest 9.1.1 replaces the report set on
    repeated -r flags, so a trailing -rfE would suppress the skip summary
    the guard depends on), (b) a manifest-generation step exists BEFORE the
    pytest step (echoing each leg's ${{ matrix.files }} to
    ${RUNNER_TEMP}/pytest-files + --collect-only -m 'not track_b' ->
    $PYTEST_MANIFEST), and (c) a skip-guard step gated on rc==0 AND
    non-empty $FILES runs tools/skip-guard.py with --manifest.

    #10028: the three artifact paths are the PER-JOB variables resolved in
    the job's first step, not the host-global `/tmp/...` literals — see
    `test_test_slow_legs_write_per_job_pytest_artifacts` below for the
    ratchet that keeps them there.

    ⛔ THE PIN MUST NOT RE-STATE THE OLD LITERALS (#10280). This docstring used
    to assert `--junitxml=/tmp/junit.xml` and `--manifest
    /tmp/expected-nodeids.txt`, which RE-PINNED the defect: those three paths
    are shared by every co-tenant job on one host, which is how run
    40921262252 read a co-tenant's manifest and redded `test-slow (a)` for
    `850 expected nodeid(s) absent` while its own pytest exited 0. The
    no-literal half is pinned once, for every migrated leg, by
    `tests/test_tmpdir_hygiene.py::test_migrated_legs_read_their_OWN_pytest_log`.
    """
    wf = _load_python_ci()
    steps = wf["jobs"]["test-slow"]["steps"]
    run = next(s for s in steps
               if s.get("name", "").startswith("Run slow test suite"))
    invocation = run["run"]
    assert '--junitxml="$PYTEST_JUNIT"' in invocation
    assert "-o junit_family=xunit1" in invocation
    # (d) the resolve step comes FIRST (it publishes $GITHUB_ENV, which only
    # reaches SUBSEQUENT steps) and fails CLOSED on a missing RUNNER_TEMP — a
    # silent `/tmp` fallback is indistinguishable from the bug it fixes.
    resolve = steps[0]
    assert resolve.get("name", "").startswith("Resolve per-job pytest artifact paths")
    assert "${RUNNER_TEMP:?" in resolve["run"], \
        "the resolve step must REFUSE to fall back to the host-global /tmp"
    for var, leaf in (("PYTEST_LOG", "pytest.log"), ("PYTEST_JUNIT", "junit.xml"),
                      ("PYTEST_MANIFEST", "expected-nodeids.txt")):
        assert f'echo "{var}=$base/{leaf}"' in resolve["run"]
    # ... and no part of THIS job may name the host-global artifact again. The
    # literals are what the collision needs, so their absence is the property —
    # ACROSS EVERY KEY OF EVERY STEP, not just `run:`. All three literals lived in
    # the `upload-artifact` step's `with: path:` block before #10028, so a
    # `run`-only scan is green on exactly the key the collision rides: reverting
    # that block re-points the upload at the host-global files while the step's
    # own `if-no-files-found: ignore` + `continue-on-error: true` keep the leg
    # green on ANOTHER job's `pytest.log`. `json.dumps` carries every YAML value
    # verbatim (parsed data holds no comments), so the `"# "`-strip still forgives
    # a shell comment that names a RETIRED path.
    def host_global_hits(step_list):
        blob = json.dumps(step_list)
        return [lit for lit in ("/tmp/pytest.log", "/tmp/junit.xml",
                                "/tmp/expected-nodeids.txt")
                if lit in blob.replace("# " + lit, "")]

    assert host_global_hits(steps) == [], (
        f"{host_global_hits(steps)} is host-global: two concurrent jobs on a "
        f"shared runner clobber it and red this leg on the clash (#10028, #7815)")
    # ⛔ NON-VACUITY, asked of a COPY: reverting ONLY the upload's `path:` block to
    # the pre-#10028 literals — the one shape a `run`-only scan cannot see — must
    # red. The real file cannot be both correct and reverted, so the mutation is
    # the only way to show the guard actually discriminates.
    upload = next(s for s in steps
                  if str(s.get("uses", "")).startswith("actions/upload-artifact"))
    reverted = [
        {**s, "with": {**s["with"],
                        "path": "/tmp/pytest.log\n/tmp/junit.xml\n"
                                "/tmp/expected-nodeids.txt"}}
        if s is upload else s
        for s in steps
    ]
    assert host_global_hits(reverted) == ["/tmp/pytest.log", "/tmp/junit.xml",
                                          "/tmp/expected-nodeids.txt"], (
        "the guard must red on a reverted upload `path:` — that block is where "
        "all three host-global literals live, and it is not a `run:` key")
    # ...and this is the fail-open that mutation closes: the retired `run`-only
    # scan sees NOTHING in those same steps, so it certified a reverting leg green.
    assert [lit for lit in ("/tmp/pytest.log", "/tmp/junit.xml",
                            "/tmp/expected-nodeids.txt")
            if lit in "\n".join(s.get("run", "") for s in reverted)] == [], (
        "a `run`-only scan is blind to the upload's `path:` — the blank result is "
        "the defect this scan's broader key set fixes, not a passing check")
    # the pytest command line itself (the comment block may mention the
    # historical flag) must carry -r fEs and never -rfE
    cmdline = next(line for line in invocation.splitlines()
                   if "python -m pytest" in line)
    assert "-r fEs" in cmdline and "-rfE" not in cmdline, \
        "test-slow must use -r fEs (the guard's skip-summary superset), never -rfE"
    assert "pytest-rc" in invocation, "the run step must persist pytest rc"
    assert "pytest-files" in invocation
    # (b) manifest generation step BEFORE the run step (step order)
    manifest = next(s for s in steps
                    if s.get("name", "").startswith("Generate coverage manifest"))
    assert steps.index(manifest) < steps.index(run), \
        "the manifest collect-only must run BEFORE pytest (off the watchdog)"
    mrun = manifest["run"]
    assert "pytest-files" in mrun
    assert "--collect-only" in mrun or "--emit-manifest" in mrun
    assert '--output "$PYTEST_MANIFEST"' in mrun
    # the slow legs' URI gate matches the fast job (full==true only)
    uri = next(s for s in steps
               if s.get("name", "").startswith("Compute docker URI"))
    assert uri["run"].count("if [ \"${{ needs.changes.outputs.full }}\" = \"true\" ]; then") == 1
    # (c) skip-guard step gated on rc==0 AND non-empty $FILES
    guard = next(s for s in steps
                 if s.get("name", "").startswith("Skip-fail guard"))
    grun = guard["run"]
    assert '-s "${RUNNER_TEMP:-/tmp}/pytest-files"' in grun
    assert '--manifest "$PYTEST_MANIFEST"' in grun
    # the slow legs no longer carry carve-out files (E2E-4 owns them)
    # #9539: read the DERIVED legs — the workflow holds no literal rows.
    leg_files = slow_leg_files(load_manifest())
    for carved in ("test_reaper", "test_flip_gate", "test_migrate_db",
                   "test_projection_lifecycle", "test_embedded_concurrency",
                   "test_hosted_backup"):
        assert carved not in leg_files, \
            f"slow carve-out file {carved} must not ride the docker slow legs"


def test_test_slow_legs_write_per_job_pytest_artifacts():
    """#10028 (measured 2026-10-29, run 40808264676): the `test-slow` legs must
    write their pytest artifacts to the PER-JOB path, never the host-global
    `/tmp/...` literals.

    WHY (this is a false verdict on a REQUIRED check, not tidiness). Four
    `test-slow` legs are scheduled concurrently on a self-hosted pool where
    several runner INSTALLS share one `/tmp`. While the three artifacts were
    `/tmp` literals, a sibling leg's `> /tmp/pytest.log` truncated this leg's
    log, so `.github/scripts/pytest-attribution.sh` could not prove ownership
    of the leg's own summary and fell closed — `STALE session summary` /
    `INCOMPLETE` — and the job went red on a run that passed every test.

    MEASURED, `test-slow (a)` on that run: its own manifest declared 367
    expected nodeids, its own run reported `365 passed, 2 skipped` (= 367) in
    2378.86s, and its own `pytest exit code: 0` banner printed — yet the leg
    concluded `failure` via `STALE session summary (NOT this leg's verdict)` /
    `##[error]test leg INCOMPLETE`. The same sharing made legs (b) and (e)
    print FAILED nodeids for `tests/test_epic903_fixtures.py`,
    `tests/test_epic903_scheduler.py` and `tests/test_graph_diagnostics.py`,
    though the only progress line either leg's log carries is
    `tests/test_ci_selection.py::` — tests they never ran. And leg (b)'s #964
    heartbeat DECREASED inside a "single" session (444 passed -> 353 passed).

    Non-vacuous on the pre-fix workflow: each assertion below is false of the
    literals that stood there (`--junitxml=/tmp/junit.xml`, `> /tmp/pytest.log`,
    `--manifest /tmp/expected-nodeids.txt`, and an upload `path:` naming the
    three `/tmp` files), so restoring any one of them reds this test.
    """
    wf = _load_python_ci()
    job = wf["jobs"]["test-slow"]
    steps = job["steps"]

    # (1) the paths are resolved FIRST, from RUNNER_TEMP, and fail closed —
    # `$GITHUB_ENV` only reaches SUBSEQUENT steps, so a later step would leave
    # every `$PYTEST_*` empty (an empty `> "$PYTEST_LOG"` is a shell error, but
    # an empty `--junitxml=` is silently the wrong file).
    resolve = steps[0]
    assert resolve.get("name") == "Resolve per-job pytest artifact paths (#10028)", \
        "the artifact paths must be resolved in the FIRST step of test-slow"
    rrun = resolve["run"]
    assert "${RUNNER_TEMP:?" in rrun, \
        "RUNNER_TEMP must fail closed — a silent /tmp fallback is the bug itself"
    for var, leaf in (("PYTEST_LOG", "pytest.log"),
                      ("PYTEST_JUNIT", "junit.xml"),
                      ("PYTEST_MANIFEST", "expected-nodeids.txt")):
        assert f'{var}=$base/{leaf}' in rrun, f"{var} must resolve under $base"

    # (2) NO executable line of the job may name the host-global literals.
    # Comments are exempt on purpose: the migration's own rationale quotes them.
    globals_ = ("/tmp/pytest.log", "/tmp/junit.xml", "/tmp/expected-nodeids.txt")
    offenders = []
    for step in steps:
        body = step.get("run")
        if not isinstance(body, str):
            continue
        for line in body.splitlines():
            if line.lstrip().startswith("#"):
                continue
            offenders += [f"{step.get('name')}: {line.strip()}"
                          for lit in globals_ if lit in line]
    assert not offenders, (
        "test-slow must not write or read the host-global /tmp pytest artifacts — "
        "concurrent legs share one /tmp and truncate each other's log "
        "(a green leg then concludes INCOMPLETE/failure): " + "; ".join(offenders)
    )

    # (3) the run step routes pytest AND the three readers through the vars.
    run = next(s for s in steps
               if s.get("name", "").startswith("Run slow test suite"))
    cmdline = next(line for line in run["run"].splitlines()
                   if "python -m pytest" in line)
    assert '-o junit_family=xunit1 > "$PYTEST_LOG" 2>&1' in cmdline, \
        "the leg's pytest stdout must land in $PYTEST_LOG"
    for reader in ('tail -n 300 "$PYTEST_LOG"',
                   '--log "$PYTEST_LOG"',
                   "grep -E '^(FAILED|ERROR) ' \"$PYTEST_LOG\""):
        assert reader in run["run"], f"the run step must read the log via {reader!r}"

    # (4) the uploaded artifact is the per-job file. `steps.<id>.with` DOES
    # allow the `runner` context (a job-level `env:` does not — #6253), so the
    # upload names `runner.temp` directly, exactly as the fast job's does.
    upload = next(s for s in steps
                  if s.get("name", "").startswith("Upload pytest log"))
    paths = upload["with"]["path"]
    for leaf in ("pytest.log", "junit.xml", "expected-nodeids.txt"):
        assert f"${{{{ runner.temp }}}}/{leaf}" in paths, \
            f"the upload must name {{{{ runner.temp }}}}/{leaf}, not the /tmp literal"


def test_xdist_is_admitted_only_in_the_d3_shape():
    """#6136 / epic #5215 owner decision D3: xdist is admitted for ONE shard
    only, with `--dist loadscope`, gated to the docker (URI-set) shape.

    Every expectation is DERIVED from the workflow text — the invocation
    sites that pass `-n`, the `--dist` mode they pass, and the guard that
    admits the pinned worker count — never a frozen literal naming a job or
    a half. A literal would re-stale the moment the admitted shard moved;
    D3's properties (one site / loadscope / docker-gated) do not.
    """
    wf = _load_python_ci()
    sites: list[tuple[str, str, str]] = []
    for job_name, job in wf["jobs"].items():
        for step in job.get("steps", []) or []:
            for line in (step.get("run") or "").splitlines():
                if "python -m pytest" in line:
                    sites.append((job_name, step.get("name", ""), line))
    assert sites, "no pytest invocation site found in python-ci.yml"

    xdist_sites = [s for s in sites
                   if _re.search(r'(?:^|\s)-n(?:\s|")', s[2])]
    assert len(xdist_sites) == 1, (
        "D3 admits xdist on ONE shard only: the workflow passes `-n` at "
        f"{len(xdist_sites)} pytest invocation site(s) "
        f"{[(j, n) for j, n, _ in xdist_sites]}")
    assert not any("--dist loadfile" in line for _, _, line in sites), (
        "D3 names `--dist loadscope`; `--dist loadfile` is not the admitted "
        "distribution mode")

    job_name, step_name, cmdline = xdist_sites[0]
    assert "--dist loadscope" in cmdline, (
        f"{job_name}/{step_name}: the admitted xdist site must pass "
        f"`--dist loadscope` (D3), got: {cmdline}")
    assert '-n "$XDIST_WORKERS"' in cmdline, (
        f"{job_name}/{step_name}: the worker count must flow through the "
        "step-local `XDIST_WORKERS`, so the admit/deny decision has ONE home")
    pinned = wf.get("env", {}).get("PYTEST_XDIST_WORKERS", "")
    assert pinned.isdigit() and int(pinned) > 0, (
        "the pinned xdist worker count must be a positive integer (D3: a "
        f"pinned count, not `auto`), got {pinned!r}")
    for jn, job in wf["jobs"].items():
        for step in job.get("steps", []) or []:
            if "PYTEST_XDIST_WORKERS" in (step.get("run") or ""):
                assert (jn, step.get("name", "")) == (job_name, step_name), (
                    f"{jn}/{step.get('name', '')}: the pinned count must be "
                    "consumed at exactly the ONE admitted site")

    # Derive the admitted shard(s) from the step's own guard + the job's
    # matrix, so "one shard" is computed rather than asserted by name.
    script = next(s["run"] for s in wf["jobs"][job_name]["steps"]
                  if s.get("name", "") == step_name)
    assert "TORTOISE_DB_URI" in script, (
        f"{job_name}/{step_name}: the xdist site must be gated on the docker "
        "URI — the URI-less embedded lane is the RC5 leak surface (#2875, "
        "#3653) D3 keeps serial")
    guard = _re.search(
        r'matrix\.half \}\}" = "([^"]+)" \] && '
        r'\[ -n "\$\{TORTOISE_DB_URI:-\}" \]',
        script)
    assert guard, (
        f"{job_name}/{step_name}: the xdist site is not gated on BOTH a "
        "single matrix half and a non-empty TORTOISE_DB_URI — D3 admits ONE "
        "shard on the docker shape only")
    # #6135: the `test` matrix is DERIVED — the workflow carries no literal
    # shard list (`matrix: ${{ fromJSON(needs.changes.outputs.fast_matrix) }}`,
    # and python-ci.yml says so itself: "this job carries no literal shard
    # list"). Reading a literal `matrix["half"]` here re-created exactly the
    # coupling #6135 removed, and against the derived expression it is a
    # STRING, not a list. `ci_selection.push_legs()` is the single source
    # (`parse_matrix_halves`'s own docstring routes callers there), so the
    # halves are read from it rather than re-declared here.
    halves = [s["name"] for s in cs.push_legs(cs.load_manifest())["shards"]]
    admitted = [h for h in halves if h == guard.group(1)]
    assert len(admitted) == 1, (
        f"{job_name}/{step_name}: the guard `matrix.half == "
        f"{guard.group(1)!r}` admits {len(admitted)} of the job's {halves} "
        "half/ies; D3 admits one")
    # Every shape that misses the guard must run serial (xdist's `-n 0`).
    assert _re.search(r"^\s*XDIST_WORKERS=0\s*$", script, _re.M), (
        f"{job_name}/{step_name}: the worker count must DEFAULT to xdist's "
        "serial mode (`0`) and be raised only inside the D3 guard")


# The tool whose lane declaration the CI jobs must make visible to the
# skip-guard STEP (a sibling of the pytest step, so it sees only the job env).
_SKIP_GUARD_PATH = Path(__file__).resolve().parents[1] / "tools" / "skip-guard.py"


def test_skip_guard_can_see_the_lane_signal():
    """#3592: the skip-guard's reason matcher is LANE-AWARE, and the lane signal
    must REACH the tool in every docker lane it gates.

    The signal used to be set in exactly ONE place — the pytest run step's
    `env:` — while the skip-guard is a LATER SIBLING step, which receives only
    the JOB env. The job env carries the "Compute docker URI" step's own
    `$GITHUB_ENV` spelling (`EXPECT_URI`), so the tool reads BOTH names
    (tools/skip-guard.py::_LANE_EXPECT_URI_ENV_ALIASES) — THIS TEST IS THE
    COUPLING: a rename of that `$GITHUB_ENV` line must red here, never silently
    revert the guard to the UNDECLARED arm, which is the #3592 false PASS (a
    module whose live-availability probe regressed skips behind "requires
    TORTOISE_DB_URI" and `python-ci-gate` stays green while the file ran
    nothing).

    Conversely the tier-2 legs need their EMPTY value visible, because that is
    how they declare themselves URI-less — without it a probe race reddens a
    green pytest (measured: run 37350021093, shard f, `EXPECT_URI: ` empty and
    the guard RED on 9 "Live FalkorDB (Docker) not available" skips).

    The carve-out job (test-carve-out) deliberately declares NOTHING: its
    selection is embedded-by-design, so the undeclared arm — which keeps the
    pre-#3592 verdicts — is what reds a live file mis-assigned into that lane.
    """
    import importlib.util

    wf = _load_python_ci()
    spec = importlib.util.spec_from_file_location(
        "_skip_guard_lane_names", str(_SKIP_GUARD_PATH))
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod._LANE_EXPECT_URI_ENV_ALIASES, (
        "the guard must read a name a SIBLING step can see (the job env)")
    for job_name in ("test", "test-slow"):
        steps = wf["jobs"][job_name]["steps"]
        compute = next(s for s in steps
                       if "Compute docker URI" in s.get("name", ""))
        script = compute["run"]
        # The prefixed name is mapped onto the pytest step (the canonical E2E-6
        # signal; the iff is asserted by test_expect_uri_gated_iff_uri), while
        # every alias the TOOL reads must be a name this job actually exports to
        # $GITHUB_ENV — otherwise the guard cannot see the lane at all.
        for name in mod._LANE_EXPECT_URI_ENV_ALIASES:
            export = f'echo "{name}=$EXPECT_URI" >> "$GITHUB_ENV"'
            assert export in script, (
                f"{job_name}: {name!r} is read by tools/skip-guard.py but this "
                "job does not export it to $GITHUB_ENV — the sibling "
                "skip-guard step would read an UNDECLARED lane (#3592)")
            assert export in script.split("fi", 1)[1], (
                f"{job_name}: the {name!r} export must sit AFTER the gate so "
                "BOTH shapes are visible (the empty tier-2 declaration too)")
        guard = next(s for s in steps
                     if s.get("name", "").startswith("Skip-fail guard"))
        assert "--manifest-only" not in guard["run"], (
            f"{job_name}: --manifest-only turns the lane-aware reason matcher "
            "OFF, so the lane export above would assert nothing")
        assert "TORTOISE_TEST_EXPECT_URI" not in guard.get("env", {}), (
            f"{job_name}: the guard step must INHERIT the lane from the job "
            "env, not re-declare it (a second spelling is a second source of "
            "truth)")


def test_canary_classifier_is_pinned_to_the_docker_lane():
    """#3592: the classifier buckets the DOCKER producer shard, so it must pass
    its lane EXPLICITLY rather than inherit the canary-streak job's environment
    (which declares no URI expectation — the undeclared arm would bucket a
    docker-lane availability regression as unattributed instead of guard-red)."""
    tool = (Path(__file__).resolve().parents[1] / "tools"
            / "testdb_canary_classify.py").read_text()
    assert "is_falkor_reason_violation(reason, lane=True)" in tool, (
        "tools/testdb_canary_classify.py must pin the docker lane it classifies")


def test_carve_out_job_uri_unset_with_carve_out_flag():
    """E2E-4 (Task 9 Step 5): the dedicated carve-out job runs the embedded
    set URI-UNSET (no TORTOISE_DB_URI — a URI would redirect the
    carve-out to the server lane) with TORTOISE_TEST_CARVE_OUT=1 (the P4
    enforcement-prep escape), and takes its file list from the selector's
    `carve_matrix` output — the `carve_out` leg packed into per-shard
    `matrix.files`."""
    wf = _load_python_ci()
    job = wf["jobs"]["test-carve-out"]
    assert "TORTOISE_DB_URI" not in job.get("env", {}) or \
        not job["env"].get("TORTOISE_DB_URI"), \
        "the carve-out job must be URI-UNSET (embedded lane)"
    assert job["env"]["TORTOISE_TEST_CARVE_OUT"] == "1"
    assert "TORTOISE_TEST_EXPECT_URI" not in job.get("env", {}), \
        "EXPECT_URI on the carve-out would trip the E2E-6 tripwire (no URI)"
    run = next(s for s in job["steps"]
               if s.get("name", "").startswith("Run carve-out suite"))
    # W37: the file list is still OWNED BY THE SELECTOR — it now arrives as the
    # per-shard `matrix.files` (each shard's slice of the `carve_out` leg)
    # rather than whole. The invariant this pin exists for is "the job never
    # hardcodes its own file list", so assert BOTH halves: the step reads the
    # selector's matrix, and that matrix is the selector's `carve_matrix`
    # output rather than a workflow literal. Asserting the old whole-leg
    # expression would have been a pin on the WIRING, not on the invariant —
    # and it would forbid exactly the change that removes the pole.
    assert "${{ matrix.files }}" in run["run"], \
        "the carve-out job must consume the selector's carve_out leg (its shard)"
    assert (job["strategy"]["matrix"]
            == "${{ fromJSON(needs.changes.outputs.carve_matrix) }}"), \
        "the carve-out matrix must come from the selector's carve_matrix output"
    assert '--junitxml="$PYTEST_JUNIT"' in run["run"], \
        "the shard's junit must be the per-job $PYTEST_JUNIT, not the host-global " \
        "`/tmp/junit.xml` (see test_carve_out_shards_write_per_job_pytest_artifacts)"


def test_carve_out_shards_write_per_job_pytest_artifacts():
    """#10280 (PR #10214 review, P2): the `test-carve-out` SHARDS must write their
    pytest artifacts to the PER-JOB path, never the host-global `/tmp/...` literals.

    WHY THIS JOB SPECIFICALLY. It is a MATRIX (`carve_matrix`, `fail-fast: false`),
    so its shards run CONCURRENTLY — and on the self-hosted pool several runner
    installs share ONE `/tmp`, which run 40985248910 shows directly: in one attempt
    `test (b)`/`test (h)`/`test (c)` and `test-concurrency-falkor` landed on different
    `pi-runner-vm-*`/`pi-runner-x64-*` installs of the same host. A shard writing
    `/tmp/pytest.log` is therefore sharing a file not with a sibling JOB but with its
    own SIBLING SHARDS, a collision no job-name-keyed ownership check can even see
    (every shard is the string `test-carve-out`). The consequence is the measured one:
    a truncated log makes `pytest-attribution.sh` fall closed (`INCOMPLETE`), and the
    `grep '^(FAILED|ERROR) '` the admin rail derives the failure SET from names a
    sibling shard's nodeids. The `junit.xml`/`expected-nodeids.txt` pair collides the
    same way and is worse — a sibling's junit is what `skip-guard.py` compares the
    collected set against, i.e. a false PASS is reachable, not just a false red.

    Non-vacuous on the pre-fix workflow: each assertion below is false of the
    literals that stood there (`--junitxml=/tmp/junit.xml`, `> /tmp/pytest.log`,
    `--output /tmp/expected-nodeids.txt`, and an upload `path:` naming the three
    `/tmp` files), so restoring any one of them reds this test.
    """
    wf = _load_python_ci()
    job = wf["jobs"]["test-carve-out"]
    steps = job["steps"]

    # (1) the paths are resolved FIRST, from RUNNER_TEMP, and fail closed —
    # `$GITHUB_ENV` only reaches SUBSEQUENT steps.
    resolve = steps[0]
    # Prefix, not equality: the issue number in the step's label is attribution, not
    # contract — main landed the same migration for this leg under #10280, so pinning
    # the ticket number would red a correct merge over a comment.
    assert resolve.get("name", "").startswith(
        "Resolve per-job pytest artifact paths"), \
        "the artifact paths must be resolved in the FIRST step of test-carve-out"
    rrun = resolve["run"]
    assert "${RUNNER_TEMP:?" in rrun, \
        "RUNNER_TEMP must fail closed — a silent /tmp fallback is the bug itself"
    for var, leaf in (("PYTEST_LOG", "pytest.log"),
                      ("PYTEST_JUNIT", "junit.xml"),
                      ("PYTEST_MANIFEST", "expected-nodeids.txt")):
        assert f'{var}=$base/{leaf}' in rrun, f"{var} must resolve under $base"

    # (2) NO executable line of the job may name the host-global literals. Comments
    # are exempt on purpose: the migration's own rationale quotes them.
    globals_ = ("/tmp/pytest.log", "/tmp/junit.xml", "/tmp/expected-nodeids.txt")
    offenders = []
    for step in steps:
        body = step.get("run")
        if not isinstance(body, str):
            continue
        for line in body.splitlines():
            if line.lstrip().startswith("#"):
                continue
            offenders += [f"{step.get('name')}: {line.strip()}"
                          for lit in globals_ if lit in line]
    assert not offenders, (
        "test-carve-out shards must not write or read the host-global /tmp pytest "
        "artifacts — concurrent shards share one /tmp (a false verdict on a required "
        "check, and a junit a sibling shard owns): " + "; ".join(offenders)
    )

    # (3) the run step routes pytest AND the readers through the vars.
    run = next(s for s in steps
               if s.get("name", "").startswith("Run carve-out suite"))
    cmdline = next(line for line in run["run"].splitlines()
                   if "python -m pytest" in line)
    assert '-o junit_family=xunit1 > "$PYTEST_LOG" 2>&1' in cmdline, \
        "the shard's pytest stdout must land in $PYTEST_LOG"
    for reader in ('tail -n 200 "$PYTEST_LOG"',
                   '--log "$PYTEST_LOG"',
                   "grep -E '^(FAILED|ERROR) ' \"$PYTEST_LOG\""):
        assert reader in run["run"], f"the run step must read the log via {reader!r}"
    # The guard/enforcing steps are further STEPS, and they read the log too.
    guard = next(s for s in steps
                 if s.get("name", "").startswith("Skip-fail guard"))
    assert 'skip-guard.py "$PYTEST_LOG"' in guard["run"], \
        "the skip-fail guard must read this shard's own log"
    assert '--manifest "$PYTEST_MANIFEST"' in guard["run"], \
        "the skip-fail guard must compare against this shard's own collected set"

    # (4) the uploaded artifact is the per-job file. `steps.<id>.with` DOES allow the
    # `runner` context (a job-level `env:` does not — #6253).
    upload = next(s for s in steps
                  if str(s.get("uses") or "").startswith("actions/upload-artifact"))
    paths = upload["with"]["path"]
    for leaf in ("pytest.log", "junit.xml", "expected-nodeids.txt"):
        assert f"${{{{ runner.temp }}}}/{leaf}" in paths, \
            f"the upload must name {{{{ runner.temp }}}}/{leaf}, not the /tmp literal"

    # #10280: the junitxml is the PER-SHARD path, not the host-global literal it
    # used to be (see the job loop in
    # `test_every_pytest_capture_leg_resolves_its_artifacts_per_job`).
    assert "--junitxml=\"$PYTEST_JUNIT\"" in run["run"]


# ── #10280: no python-ci pytest leg may keep a host-global /tmp artifact ──
#
# The class, stated once so a future leg inherits the reasoning: every runner
# install on this host shares ONE `/tmp`, and one runner runs one job at a time.
# So a per-RUNNER path (`$RUNNER_TEMP`) is per-JOB, while a `/tmp` literal is
# SHARED with whatever else the pool is doing. Measured consequences, in both
# directions:
#
#   * FALSE RED (run 40990582554): `test-track-b` wrote `4 expected nodeids ->
#     /tmp/expected-nodeids.txt`, then a concurrent `test-carve-out (c)` leg
#     overwrote that file with its own `616 expected nodeids` (11s earlier on a
#     sibling runner), and the required check reported `616 expected nodeid(s)
#     absent from the junitxml` with every test in the leg passing.
#   * FALSE RED (run 40808264676, #10028): a sibling's `> /tmp/pytest.log`
#     truncated `test-slow (a)`'s log between its `tail` and the reporter's read,
#     so a green leg concluded `STALE session summary` / `INCOMPLETE`.
#   * FALSE PASS (the same sharing, inverted): a leg whose own manifest was
#     replaced by a LARGER one can just as easily be replaced by a smaller set
#     that its junitxml happens to satisfy — the coverage manifest is the thing
#     that certifies "these tests ran", so it must not be writable by a peer.
#
# The three jobs below were the residue #10028 left (its own comment said the
# `test-slow` migration was the second of four). They are asserted as a TABLE,
# not one bespoke test per job, so a leg added to the selector's matrix inherits
# the check by adding its job id here — and so the assertion cannot drift into
# "whatever the last migrated job happens to look like".
_PER_JOB_ARTIFACT_JOBS = (
    "test",
    "test-slow",
    "test-track-b",
    "test-carve-out",
    "test-concurrency-falkor",
)
#: Every host-global pytest artifact spelling the migration retired. Widened
#: beyond `/tmp/pytest.log` on purpose: filtering on that one literal is what let
#: `trackb.log` and `junit.xml` read as "already fine" (#10280's review).
_HOST_GLOBAL_PYTEST_ARTIFACTS = (
    "/tmp/pytest.log",
    "/tmp/junit.xml",
    "/tmp/expected-nodeids.txt",
    "/tmp/trackb.log",
    "/tmp/pytest-d14.log",
    "/tmp/junit-d14.xml",
)


def test_every_pytest_capture_leg_resolves_its_artifacts_per_job():
    """#10280: every migrated leg resolves its artifacts under `$RUNNER_TEMP`.

    Non-vacuous on the pre-fix workflow, by the reason that actually holds per leg:
    NONE of the three newly-migrated legs (`test-track-b`, `test-carve-out`,
    `test-concurrency-falkor`) had a resolver step — so assertion (1) fails on all
    three — and EACH named at least one of the literals above from an executable
    `run:` line — so assertion (2) fails on all three too. Both halves are what this
    test rests on; how MANY distinct literals a leg had is not (the pre-fix counts
    were 3, 3 and 1 respectively, and a count is the kind of restated measurement
    that goes stale in the commit which changes the code). Restoring any one literal
    (or the `/tmp` upload `path:`) reds it again.
    """
    wf = _load_python_ci()
    for job_id in _PER_JOB_ARTIFACT_JOBS:
        job = wf["jobs"][job_id]
        steps = job["steps"]

        # (1) resolved FIRST — `$GITHUB_ENV` reaches only SUBSEQUENT steps, and an
        # empty `--junitxml=` is silently the wrong (empty) path.
        first = steps[0]
        assert first.get("name", "").startswith(
            "Resolve per-job pytest artifact paths"
        ), f"{job_id}: the artifact paths must be resolved in the FIRST step"
        assert "${RUNNER_TEMP:?" in first["run"], (
            f"{job_id}: RUNNER_TEMP must fail closed — a silent /tmp fallback is "
            "the bug itself"
        )
        assert "$GITHUB_ENV" in first["run"], (
            f"{job_id}: the resolver must export through $GITHUB_ENV"
        )

        # (2) no executable line of the job may name a host-global literal.
        # Comments are exempt on purpose — the migration's rationale quotes them.
        offenders = []
        for step in steps:
            body = step.get("run")
            if not isinstance(body, str):
                continue
            for line in body.splitlines():
                if line.lstrip().startswith("#"):
                    continue
                offenders += [f"{job_id} / {step.get('name')}: {line.strip()}"
                              for lit in _HOST_GLOBAL_PYTEST_ARTIFACTS
                              if lit in line]
        assert not offenders, (
            "a host-global /tmp pytest artifact is SHARED with every other runner "
            "install on this host — a co-tenant then rewrites this leg's log, "
            "manifest or junit (#7815/#10280): " + "; ".join(offenders)
        )

        # (3) the upload artifacts are the runner's own files, not `/tmp`'s.
        # `runner` is a valid context in `steps.<id>.with` (see the #6253 note on
        # test-slow), which is why this is read from the step rather than a
        # job-level `env:` block.
        for step in steps:
            path = (step.get("with") or {}).get("path")
            if path is None or "upload-artifact" not in str(step.get("uses", "")):
                continue
            for line in str(path).splitlines():
                assert "${{ runner.temp }}" in line, (
                    f"{job_id}: the uploaded pytest artifact {line.strip()!r} is not "
                    "under ${{ runner.temp }} — it would upload a co-tenant's file"
                )
                assert "/tmp/" not in line, (
                    f"{job_id}: the uploaded pytest artifact {line.strip()!r} names "
                    "the host-global shared /tmp (#10280)"
                )


# ── #3239: a hung pytest job holds the per-PR concurrency slot ────────────
# The `concurrency:` group is per-PR with `cancel-in-progress: true`, so a
# stale-head run that will not die blocks the CURRENT head's run — and
# `gh run cancel` cannot finish while a job is still executing, so the job's
# OUTER `timeout-minutes` is the exposure window. Two invariants follow, and
# all of them had been violated:
#
#   * ORDER: the in-step watchdog must stay STRICTLY BELOW the outer cap. The
#     watchdog is the bound that PRINTS the pass/fail counts (#798: a
#     runner-level cap that fires first cancels the step and loses the log
#     tail), so a cap beneath it silently restores #798's death mode — and a
#     MISSING cap (test-track-b before #3239) leaves GitHub's 360m default:
#     six hours of held slot instead of the 90m the issue reports.
#   * PROPORTION: the cap clears the measured work with `WATCHDOG_HEADROOM`
#     (#6135's 2.0x factor, reused rather than invented — the constants block
#     owns its provenance) and is not an order of magnitude above it.
#
# The work is MEASURED, not asserted: Actions API on the 60 newest completed
# python-ci.yml runs, `started_at`→`completed_at` on the pytest step
# (2026-09-30T02:17Z..12:52Z, #3239). `_MEASURED_PYTEST_MAX_MIN` is that tap;
# the carve-out test also re-derives a floor from the committed `durations`
# map, so the bound cannot silently rot as the carve-out set grows.
#
# Before the change the caps were 90m (carve-out, 4.9x its 16.02m pytest
# step), 90m (test-slow, 11x its 4.70m) and none at all (test-track-b).
_CAP_CEILING_FACTOR = 3.0
_MEASURED_PYTEST_MAX_MIN = {
    "test-carve-out": 16.02,
    "test-slow": 4.70,
    "test-track-b": 0.08,
}
_PYTEST_WATCHDOG_RE = re.compile(r"timeout -s INT -k \d+ (\d+)m")


def _literal_pytest_watchdog(job: dict) -> int | None:
    """The job's in-step watchdog in minutes, or None when it is not a
    literal. The fast shards pass `${{ matrix.watchdog_minutes }}m`, which is
    derived by `tools.ci_selection.shard_watchdog_minutes` — it has no literal
    here, so it is covered by the ceiling assertion instead."""
    for step in job.get("steps", []):
        hit = _PYTEST_WATCHDOG_RE.search(step.get("run", ""))
        if hit:
            return int(hit.group(1))
    return None


def test_every_bounded_pytest_job_caps_above_its_watchdog():
    """#3239: a pytest job whose in-step watchdog kills it must ALSO carry an
    outer `timeout-minutes` strictly above that watchdog.

    Both halves are load-bearing and both had been broken: test-track-b ran
    pytest under a 20m watchdog with NO job cap (GitHub's 360m default), and
    the fast shards' derived watchdog can reach `WATCHDOG_CEILING_MIN`."""
    wf = _load_python_ci()
    jobs = wf["jobs"]
    bounded = {}
    for name, job in jobs.items():
        watchdog = _literal_pytest_watchdog(job)
        if watchdog is None:
            continue
        cap = job.get("timeout-minutes")
        assert isinstance(cap, int), (
            f"{name}: runs pytest under a {watchdog}m in-step watchdog but has "
            f"no `timeout-minutes` cap — GitHub's 360m default then holds the "
            f"per-PR concurrency slot for a hung job (#3239)")
        assert watchdog < cap, (
            f"{name}: the in-step watchdog ({watchdog}m) must stay BELOW the "
            f"outer cap ({cap}m), or the runner kills the step before it can "
            f"print its pass/fail counts (#798)")
        bounded[name] = (watchdog, cap)
    # W37: `test-carve-out` is now a SHARDED job, so its pytest step carries no
    # literal watchdog — it carries `${{ matrix.watchdog_minutes }}m`, derived
    # per shard by `tools.ci_selection.shard_watchdog_minutes`. The pin is
    # therefore re-derived rather than deleted (its own message asked for
    # this): the ORDER invariant is now asserted against the LARGEST watchdog
    # the selector actually emits, which is the value the literal cap must
    # clear. A static `in bounded` membership check would have gone vacuous the
    # moment the job became a matrix — it would assert nothing about the
    # carve-out at all.
    idx = _carve_matrix()
    carve_cap = jobs["test-carve-out"].get("timeout-minutes")
    assert isinstance(carve_cap, int), (
        "the carve-out job needs a literal `timeout-minutes` — without one it "
        "falls back to GitHub's 360m default and a hung shard holds the per-PR "
        "concurrency slot for six hours (#3239)")
    worst = max(e["watchdog_minutes"] for e in idx["include"])
    assert worst < carve_cap, (
        f"the carve-out's largest per-shard watchdog ({worst}m) must stay "
        f"BELOW its outer cap ({carve_cap}m), or the runner kills the step "
        f"before it can print its pass/fail counts (#798)")
    # The fast shards' watchdog is per-shard, clamped by the selector at
    # WATCHDOG_CEILING_MIN — so the cap must clear that ceiling, not a literal.
    fast_cap = jobs["test"].get("timeout-minutes")
    assert isinstance(fast_cap, int) and fast_cap > WATCHDOG_CEILING_MIN, (
        f"the fast job's cap ({fast_cap}) must clear the selector's "
        f"WATCHDOG_CEILING_MIN ({WATCHDOG_CEILING_MIN}) — a per-shard "
        f"watchdog may legitimately reach it (#6135/#3239)")


def test_slow_leg_bounds_clear_the_committed_work():
    """#6137/#3239: each committed `test-slow` leg must run under a watchdog
    that clears THAT leg's committed work by the house headroom.

    The slow legs are the only bounded pytest job whose budget is a LITERAL
    while its work moves with `slow_files`, so a changed leg set can outgrow
    the budget silently. #6137 moved 43 files in — the legs' committed
    `durations` weight went from 2.84m to 16.21m each — and left the #3239
    literal at 10m, so BOTH legs were killed by the 10m WATCHDOG banner on a
    full-selection run and took the required `python-ci-gate` red with them
    (PR #6234 run 37229875496; 739 and 1257 tests had passed before the kill).
    `test_every_bounded_pytest_job_caps_above_its_watchdog` could not see it:
    a 10m watchdog under a 20m cap is a consistent PAIR whatever the work.

    The floor is re-derived from the committed `durations` map, so the budget
    cannot rot past its work again without reddening here. A leg file with no
    `durations` row FAILS this test rather than weighing 0.0 — see the guard
    below for why the default would re-open the very hole."""
    from tools.ci_selection import WATCHDOG_HEADROOM, _duration_weight
    job = _load_python_ci()["jobs"]["test-slow"]
    durations = load_manifest()["durations"]
    watchdog = _literal_pytest_watchdog(job)
    assert watchdog is not None, (
        "test-slow's pytest step must carry a LITERAL watchdog — the per-leg "
        "budget is what this test derives a floor for")
    cap = job["timeout-minutes"]
    assert watchdog < cap, (
        f"the in-step watchdog ({watchdog}m) must stay BELOW the outer cap "
        f"({cap}m) so a killed leg still prints its counts (#798)")
    # #9539: the legs are DERIVED (slow_shard_entries) — there are no committed
    # rows left to iterate. This keeps the same floor assertion, applied to each
    # leg the selector actually emits at the committed leg count.
    for row in slow_shard_entries(load_manifest()):
        files = row["files"]
        assert files, f"test-slow leg {row['name']!r} is empty"
        # FAIL CLOSED on an unmeasured leg file. `durations.get(f, 0.0)` would
        # weigh it ZERO, so the estimate this guard asserts against could sit
        # arbitrarily below the leg's real work and the budget could rot past
        # it exactly as it did in #6137 — and no other gate closes the gap:
        # `duration_coverage_issues` covers `fast_pool()` only (these files have
        # left it), `duration_issues` validates keys that EXIST, and the
        # in-workflow drift guard pins the union of the two rows without
        # reading the map at all.
        unmeasured = [f for f in files if f + ".py" not in durations]
        assert not unmeasured, (
            f"test-slow leg {row['name']!r} has {len(unmeasured)} file(s) with "
            f"no `durations` row: {unmeasured[:5]} — an unmeasured file weighs "
            f"0.0 here, so the leg's estimate is not a lower bound on its work "
            f"and this guard cannot see the budget rot it exists to catch. "
            f"Register it in config/ci-surfaces.yml `durations`.")
        committed = sum(_duration_weight(durations[f + ".py"]) for f in files) / 60.0
        assert watchdog >= WATCHDOG_HEADROOM * committed, (
            f"test-slow leg {row['name']!r}: the in-step watchdog "
            f"({watchdog}m) no longer clears its committed estimate "
            f"({committed:.2f}m) by WATCHDOG_HEADROOM ({WATCHDOG_HEADROOM}x) — "
            f"the leg is killed mid-suite and the gate reds before pytest can "
            f"report. Re-derive the budget (and the outer cap above it) from "
            f"the committed `durations` map.")


# ── #3239, extended: the REQUIRED surface may not inherit the 360m default ──
# `test_every_bounded_pytest_job_caps_above_its_watchdog` closes the class for
# jobs whose pytest step carries a LITERAL watchdog. It cannot see a job with no
# pytest step at all, and measured 2026-10-27 that gap was open on `packs-compile`
# and on `python-ci-gate` — the REQUIRED check itself — plus FIVE of the seven
# required contexts in `ci.yml` and the `ai-review-gate` job. A wedged job there is
# not a slow CI, it is a merge queue held on a pending check for six hours.
#
# The required-context invariant below is deliberately DERIVED, never a hard-coded
# job list: the names come from `.mergify.yml`'s own conditions (the same source
# `tools/mergify_config_guard.py` reads), so a new required context is covered the
# day it is named rather than the day someone remembers this file. The measured
# bounds themselves live in `docs/ops/ci-wall-clock.md`.

def test_every_python_ci_job_carries_an_outer_cap():
    """Every job in `python-ci.yml` must carry a literal `timeout-minutes`.

    This is the workflow that emits `python-ci-gate`, the REQUIRED merge check, and
    twelve of its jobs are in that job's `needs:` — so any one of them without a
    cap can withhold the required check for GitHub's 360-minute default (#3239).
    The assertion is deliberately workflow-wide rather than `needs:`-scoped: the
    two jobs outside `needs:` (`canary-streak`, and the aggregator itself) are one
    selector output away from joining it, and a 360m default is never the bound
    anyone would choose. A job that needs longer than its measured work is fine:
    pick the number from a measurement and record it, as
    `docs/ops/ci-wall-clock.md` does.
    """
    wf = _load_python_ci()
    uncapped = [
        name for name, job in wf["jobs"].items()
        if not isinstance(job.get("timeout-minutes"), int)
        or job["timeout-minutes"] <= 0
    ]
    assert not uncapped, (
        f"python-ci.yml job(s) {uncapped} carry no literal `timeout-minutes` — "
        f"GitHub's 360m default then holds them, and twelve of this workflow's "
        f"jobs are in `python-ci-gate`'s `needs:`, so a hung job withholds the "
        f"REQUIRED check for six hours (#3239). Set a bound from a measurement "
        f"(docs/ops/ci-wall-clock.md), not from the default.")


def test_every_required_merge_check_carries_an_outer_cap():
    """A job EMITTING a required merge context must carry a literal cap.

    The required set is read from `.mergify.yml` (`queue_conditions` gates ENTRY,
    `merge_conditions` gates the MERGE — both are required, so both are checked),
    and each name is resolved to the job that emits it through the same emitter
    map `tools/mergify_config_guard.py` uses — a job's effective name is its
    `name:` field when set, else its id.

    A required name that resolves to NO job is asserted against rather than
    skipped: today all seven resolve, and a name the workflows do not emit is
    either a new gating workflow that needs a bound or a condition that can never
    be satisfied (the deadlock the mergify guard exists to catch). If a required
    context is ever emitted by a non-workflow app, extend this test's resolution
    — do not weaken the assertion, because a skipped name is a silently unbounded
    merge path.

    Scope note: this covers the SEVEN required contexts. It does not assert
    anything about merge-path workflows that emit no required context (e.g.
    `drift-guard`) — those are bounded by hand, with their measurement recorded
    in `docs/ops/ci-wall-clock.md`.
    """
    docs = mcg._workflow_docs(REPO)
    mergify = mcg._load_mergify(REPO)
    required: list[str] = []
    for rule in mergify["queue_rules"]:
        for key in ("queue_conditions", "merge_conditions"):
            required.extend(mcg._success_names(rule.get(key) or []))
    required = sorted(set(required))
    assert required, (
        ".mergify.yml declares no positive `check-success=` condition — the "
        "required set is empty, so this guard would assert nothing")

    by_name: dict[str, list[dict]] = {}
    for emitter in mcg._emitters(docs):
        by_name.setdefault(emitter["name"], []).append(emitter)

    unemitted = [name for name in required if name not in by_name]
    assert not unemitted, (
        f"required merge context(s) {unemitted} are named in .mergify.yml but "
        f"NO job's effective check name matches — either a gating workflow is "
        f"missing a bound this test cannot see, or the context can never be "
        f"satisfied (see tools/mergify_config_guard.py).")

    uncapped: list[str] = []
    for name in required:
        for emitter in by_name[name]:
            job = docs[emitter["workflow"]]["jobs"][emitter["job"]]
            cap = job.get("timeout-minutes") if isinstance(job, dict) else None
            if not isinstance(cap, int) or cap <= 0:
                uncapped.append(f"{name} ({emitter['workflow']}:{emitter['job']})")
    assert not uncapped, (
        f"required merge check(s) {uncapped} carry no `timeout-minutes`, so a "
        f"wedged job holds the merge queue on GitHub's 360m default instead of "
        f"failing in minutes-to-tens-of-minutes. Set the bound from a "
        f"measurement (docs/ops/ci-wall-clock.md).")


def test_slow_shards_are_duration_packed_and_meet_the_wall_target():
    """#9539: the slow legs must be a complete, duration-balanced partition AND
    the wall their pack projects must stay inside the CI guideline.

    This is the guard the lane did not have. The old committed rows could rot
    without ANY test noticing: #9017 found the halves at 6.4:1 because the eval
    file had grown from its declared 855 s to a measured 24.4 min and nothing
    re-derived the split. Here the properties are recomputed from the committed
    `durations` map on every run, so a slow file that grows reds THIS instead of
    silently restoring the merge ceiling:

      * coverage — the legs partition `slow_gating_files()` exactly, once
        each (no gap, no duplicate);
      * balance — no leg exceeds its fair share by more than the house
        `HALF_DURATION_IMBALANCE_RATIO` band (an LPT pack normally lands
        within 0.1%, so 1.25 is deliberately loose enough for a coarse map);
      * the bound — `max(leg) + SLOW_LEG_SETUP_ALLOWANCE_MIN` <= the 10m
        guideline. A new slow file big enough to break it reds here, and the
        remedy is a reviewed `slow_shards` bump, not a quieter number.

    The setup allowance is a MEASURED constant (1.9–2.1 min from the jobs API
    over 5 main runs), not zero: a bound that ignored the per-leg fixed cost
    would claim a wall the job cannot hit.
    """
    m = load_manifest()
    count = slow_shard_count(m)
    entries = slow_shard_entries(m)
    assert len(entries) == count, (
        f"slow_shards declares {count} legs but the pack emitted {len(entries)} "
        f"— a short pack means the pool cannot be sharded that widely")
    assert count >= 2
    # coverage: exactly once each
    flat = [f for e in entries for f in e["files"]]
    assert len(flat) == len(set(flat)), (
        f"a slow file rides more than one leg: "
        f"{sorted({f for f in flat if flat.count(f) > 1})}")
    assert set(flat) == set(slow_leg_files(m)), (
        "the slow legs must cover slow_gating_files() "
        "(slow_files - carve_out - the non-gating lane, #10194) exactly: "
        f"missing={sorted(set(slow_leg_files(m)) - set(flat))} "
        f"extra={sorted(set(flat) - set(slow_leg_files(m)))}")
    # balance: no leg above its fair share by more than the house band
    proj = slow_wall_projection(m, entries)
    pool = proj["pool_min"]
    fair = pool / count
    assert proj["max_leg_min"] <= fair * cs.HALF_DURATION_IMBALANCE_RATIO, (
        f"slow legs are duration-imbalanced: {proj['legs_min']} min against a "
        f"{fair:.2f}m fair share (band {cs.HALF_DURATION_IMBALANCE_RATIO}x) — "
        f"the LPT pack regressed; re-cut from the `durations` map")
    # the bound itself
    assert proj["projected_wall_min"] <= SLOW_LEG_WALL_TARGET_MIN, (
        f"the slow lane no longer meets its wall target: {proj['legs_min']} min "
        f"per leg against a {pool:.2f}m pool at {count} legs, plus the measured "
        f"{SLOW_LEG_SETUP_ALLOWANCE_MIN}m per-leg setup, projects "
        f"{proj['projected_wall_min']:.2f}m — over the "
        f"{SLOW_LEG_WALL_TARGET_MIN:.0f}m CI guideline. Raise "
        f"`config/ci-surfaces.yml::slow_shards` (reviewed) instead of letting "
        f"this lane become the merge ceiling again")
    # the emitted matrix is what the job expands, and every leg is non-empty
    inc = slow_matrix_include(entries)["include"]
    assert [r["half"] for r in inc] == [e["name"] for e in entries]
    assert all(r["files"].split() for r in inc), "a slow leg must never be empty"


def test_slow_shards_declaration_is_fail_closed():
    """#9539: a malformed `slow_shards` is an ERROR, never a silent default.

    Same polarity as `fast_shard_issues`/`carve_shard_issues`: absence is 'not
    adopted' (the historical 2 legs) and is fine; an explicit null, a string, a
    bool, a float or a count that deletes or over-extends the lane is a typo
    that `--integrity` must NAME — otherwise the workflow runs a leg count
    nobody chose and the merge rail's lane-parity (a job-NAME subtraction) sees
    a set that was never reviewed."""
    ok = load_manifest()
    assert slow_shard_issues(ok) == [], slow_shard_issues(ok)
    assert slow_shard_count(ok) >= 2, (
        "this repo exists to shard the slow lane; a count of 1 means the key "
        "was removed and the wall guard below is what will red")
    for bad, why in ((None, "explicit null"), ("5", "a string"),
                     (True, "a bool"), (5.5, "a float"), (1, "one leg"),
                     (0, "zero"), (-1, "negative"),
                     (MAX_SLOW_SHARDS + 1, "above the a..z ceiling")):
        m = dict(ok, slow_shards=bad)
        assert slow_shard_issues(m), f"{why} ({bad!r}) must be an error"
        assert slow_shard_count(m) == DEFAULT_SLOW_SHARDS == 2, (
            f"{why} must fail SAFE to the historical two legs, not to a number "
            f"nobody chose")
    # Absence is NOT an error — it is 'not adopted', and it must give back the
    # historical two-leg shape.
    m = {k: v for k, v in ok.items() if k != "slow_shards"}
    assert slow_shard_issues(m) == []
    assert slow_shard_count(m) == 2
    assert len(slow_shard_entries(m)) == 2
    assert set(f for e in slow_shard_entries(m) for f in e["files"]) == \
        set(slow_leg_files(m))


def test_removing_slow_shards_reverts_the_leg_count_but_is_not_a_complete_rollback():
    """#9539: absence gives back 2 legs — and the wall guard then REDS.

    `config/ci-surfaces.yml` and `ci_selection` used to say deleting the key
    'is a complete rollback', which is the wording `carve_shards` can use (that
    lane has no wall guard). It is false here: the wall-target guard recomputes
    from the `durations` map, and two legs project ~20.9m against the 10m
    guideline — so an operator taking the documented rollback lands on a FAILING
    suite, not on main's shape. The comments say so now; this pins the FACT they
    describe, so neither comment can drift back to claiming otherwise.
    """
    m = {k: v for k, v in load_manifest().items() if k != "slow_shards"}
    assert len(slow_shard_entries(m)) == 2, (
        "absence must revert the leg count to the historical 2")
    proj = slow_wall_projection(m)
    assert proj["projected_wall_min"] > SLOW_LEG_WALL_TARGET_MIN, (
        f"deleting `slow_shards` is documented as NOT a complete rollback because "
        f"the wall guard reds on 2 legs; it no longer does "
        f"({proj['projected_wall_min']}m <= {SLOW_LEG_WALL_TARGET_MIN}m) — update "
        f"that comment in config/ci-surfaces.yml and tools/ci_selection.py")


def test_the_selector_offers_no_unwired_slow_matrix_flag():
    """#9539 review: `--emit-slow-matrix` was dead surface with a false rationale.

    Its comment claimed the run-time drift guard would call it 'so the guard
    compares the EXPANDED matrix against the config pool without a second
    implementation'. The drift guard imports `ci_selection` and calls
    `slow_leg_files()` itself, and nothing anywhere called the flag — a second,
    unexercised derivation plus a comment asserting it is load-bearing. It was
    removed rather than wired: `--emit-push-matrix` already publishes
    `slow_matrix` for the `changes` job, which is what CI actually consumes.
    """
    selector = Path(__file__).resolve().parents[1] / "tools" / "ci_selection.py"
    proc = subprocess.run([sys.executable, str(selector), "--emit-slow-matrix"],
                          capture_output=True, text=True, cwd=str(selector.parent))
    assert proc.returncode == 2, (
        f"`--emit-slow-matrix` must be gone (argparse rejects unknown flags with "
        f"exit 2), got rc={proc.returncode}: {proc.stdout}{proc.stderr}")
    emit = subprocess.run([sys.executable, str(selector), "--emit-push-matrix"],
                          capture_output=True, text=True, cwd=str(selector.parent),
                          check=True).stdout
    assert json.loads(emit)["slow_matrix"]["include"], (
        "the surviving entry point must still publish the slow matrix")


def test_9017_block_states_the_derived_split_not_the_2_leg_era():
    """#9539 review: the `#9017` block is a DATED 2-leg measurement — say so.

    Its per-file table is still a valid measurement (run 39165691659) but the
    SPLIT narrative wrapped around it is not: #9539 replaced the committed pair
    with legs DERIVED from that same map (`slow_gating_files()` — slow minus
    carve-out minus the non-gating lane since #10194 — LPT-packed
    into `slow_shards` = 5). In the present tense, "the LPT pack of that map into
    two legs of 18.945m / 18.933m" and "2.11x the heavier leg" both contradict
    the lane the reader is about to configure — including the number the per-leg
    `matrix.watchdog_minutes` follow-up would size the 40m literal against (at
    the time of that review the heavier DERIVED leg was 7.58m, 5.3x, not 18.95m
    at the 2.0x floor; #10194 has since taken 5.26m out of the pool, so the live
    figure must be RE-DERIVED, never quoted). This is
    exactly the rotation the durations header asks for, and the same failure this
    block's own history records ("they rot on every re-cut, and this comment has
    now been wrong twice for that reason").
    """
    text = (REPO / "config" / "ci-surfaces.yml").read_text(encoding="utf-8")
    assert "into two legs of 18.945m" not in text, (
        "the `#9017` block still states the 2-leg split as current fact — #9539 "
        "derives 5 legs from the same map")
    assert "2.11x the heavier leg" not in text, (
        "the `#9017` block still quotes the 2-leg-era watchdog headroom as "
        "current (the heavier derived leg is 7.58m, i.e. 5.3x)")
    marker = next(ln for ln in text.splitlines() if "THE SPLIT" in ln)
    assert "HISTORICAL" in marker, (
        "the 2-leg-era split must be marked as history, the way the aggregate's "
        f"pre-#9539 table is; got {marker!r}")
    # …and the CURRENT shape must be stated in that block: the derived leg count,
    # the heavier leg's weight, and the headroom figure the literal is sized from.
    split_block = text[text.index(marker):]
    split_block = split_block[:split_block.index("# #2008 review")]
    for want, why in (("slow_shards", "the derived leg count it now uses"),
                      ("7.58m", "the heavier DERIVED leg's weight"),
                      ("5.3x", "the 40m literal's headroom over that leg")):
        assert want in split_block, (
            f"the `#9017` block must state {why} ({want!r}) — it is the only "
            f"place a reader sizing the 40m watchdog looks")


def test_parse_matrix_halves_matches_no_literal_block_on_the_current_workflow():
    """#9539 review: the legacy regex matches NOTHING now — the docstring said
    the `test-slow` job's block was still there to match.

    #6135 left `test` derived; #9539 left `test-slow` derived too, so no literal
    `- half:` / `files: >-` block remains in python-ci.yml and
    `parse_matrix_halves()` returns {}. That stays fail-CLOSED rather than hollow:
    the normal path never reaches the fallback (`workflow_matrix_issues()` is
    empty), the drift case reports the drifted job by name there, and the `{}`
    arm of `workflow_halves_issues()` reports "workflow parse failure" instead of
    returning `[]` and passing. A RE-INTRODUCED legacy block still parses, so the
    #1266 tie checks against it are still live.
    """
    m = load_manifest()
    assert cs.parse_matrix_halves(cs.WORKFLOW.read_text(encoding="utf-8")) == {}, (
        "python-ci.yml carries a literal legacy matrix block again — the parse "
        "and `parse_matrix_halves`'s docstring disagree (#9539)")
    assert cs.workflow_matrix_issues(cs.WORKFLOW, m) == [], (
        "the empty parse must not be reached on the normal path: a drift here "
        "means `workflow_matrix_issues` should have named it")
    issues = cs.workflow_halves_issues(m, {})
    assert issues and "parse failure" in issues[0], (
        f"an empty parse must fail CLOSED, not pass — got {issues!r}")
    doc = cs.parse_matrix_halves.__doc__ or ""
    assert "the only block this regex can still match" not in doc, (
        "the docstring still claims a literal block is left to match")
    legacy = ("- half: a\n          files: >-\n            test_a test_b\n"
              "- half: b\n          files: >-\n            test_c\n")
    assert cs.parse_matrix_halves(legacy) == {
        "a": ["test_a", "test_b"], "b": ["test_c"]}, (
        "a re-introduced legacy block must still be parsed — that is the only "
        "reason this fallback exists")


CI_RUNNER_GATE = Path(__file__).resolve().parents[1] / "scripts" / "ci_runner_gate.sh"
#: The machine-local `scripts` pointer itself, WITHOUT following it. Since #9475 it is
#: GITIGNORED and NOT tracked (a git symlink blob cannot hold `$AGENT_INFRA_PATH`), so the
#: runner case that may skip is "it was never materialised here" — an absent path, not a
#: dangling committed link.
SCRIPTS_LINK = Path(__file__).resolve().parents[1] / "scripts"
CI_RUNNER_GATE_ABSENT = (
    f"PREMISE CROSS-CHECK NOT RUN: agent-infra's ci_runner_gate.sh is not "
    f"reachable at {CI_RUNNER_GATE} (the machine-local `scripts` pointer is "
    f"missing or does not resolve — #9475: it is gitignored, not tracked, so a "
    f"fresh clone or lane has none until `uv run python "
    f"tools/link_agent_infra_scripts.py` materialises it from $AGENT_INFRA_PATH, "
    f"as on a runner where that variable is unset). The repo-side half of the "
    f"guard — test-slow's label is VERSION-PINNED — ran and passed. The premise "
    f"half is HOST-ONLY AND UNTESTED BY CI: it is a cross-repo fact (the labels "
    f"agent-infra's gate attaches to the self-hosted pool) that no runner checks "
    f"out, so a green CI run says nothing about it. Run this module on a host "
    f"with $AGENT_INFRA_PATH set (and the pointer materialised) to verify that "
    f"the gate still attaches only `ubuntu-latest`. Same shape and same limit as "
    f"RAIL_ABSENT_REASON in tests/test_pytest_attribution.py."
)


def test_slow_legs_are_pinned_off_the_ambiguous_runner_label():
    """2026-10-19: `ubuntu-latest` is not "GitHub-hosted" on this repo.

    `scripts/ci_runner_gate.sh` re-attaches `ubuntu-latest` to the self-hosted
    ARM64 pool as a fallback, so `runs-on: ubuntu-latest` resolves to a ~2x
    slower class run to run — and `test-slow` (the leg that IS the critical
    path) was killed at its 40m watchdog on `pi-runner-vm-2` after executing
    39.6 min of test time (run 39309984832, `rc=137`). The job then read 42.1m
    and concluded `failure`, so this leg's wall became main's critical path
    (`ci_crit_max=42.1m`, up from 22.6m) and the required aggregate red named a
    KILL rather than a test verdict. (That run was ALSO red for independent,
    genuine reasons — three fast shards on hosted runners failed real tests — so
    the pin removes this kill and the critical-path inflation, not the run's
    redness.)

    The budget cannot absorb that class: 2x the leg would be >=79m against a
    40m budget built at the `WATCHDOG_HEADROOM` floor, past both
    `WATCHDOG_CEILING_MIN` and this job's 60m cap. So the leg is pinned to a
    VERSION-PINNED hosted label (the pool claims `ubuntu-latest`, `self-hosted`,
    `Linux`, `ARM64` and `self-hosted-arm64` — never `ubuntu-<version>`), the
    same pin and the same reasoning as `workflow-lint` (#7867).

    A bare `ubuntu-latest` here re-opens exactly that false red, so this reds
    rather than relying on a comment nobody re-reads."""
    job = _load_python_ci()["jobs"]["test-slow"]
    runs_on = job["runs-on"]
    assert runs_on != "ubuntu-latest", (
        "test-slow must NOT use `ubuntu-latest`: the fallback gate re-attaches "
        "that label to the self-hosted ARM64 pool, where this leg needs ~2x its "
        "budget and is killed at the watchdog (measured: 39.6 min of test time, "
        "rc=137, job 42.1m). Pin a version-pinned hosted label (`ubuntu-24.04`).")
    assert isinstance(runs_on, str) and re.fullmatch(r"ubuntu-\d+\.\d+", runs_on), (
        f"test-slow's runner label {runs_on!r} must be a VERSION-PINNED hosted "
        f"label (`ubuntu-<version>`): `ubuntu-latest` is claimed by the "
        f"self-hosted pool whenever the fallback is attached, and a label the "
        f"pool claims puts this leg back on the class that cannot finish it.")

    if not SCRIPTS_LINK.is_dir():
        # The machine-local `scripts` pointer is ABSENT or does not resolve, which is
        # every CI runner and every fresh clone/lane: since #9475 it is gitignored and
        # not tracked, so nothing creates it until
        # `tools/link_agent_infra_scripts.py` runs. It is a host convenience, not a repo
        # dependency (`python-ci.yml` is self-contained by decision, fix #555).
        # Announced, never silent, and the message says out loud that the premise half
        # is untested by CI — a bare `skipped` would let a green run read as "the pool's
        # labels were verified", which is the vacuous pass this guard exists to prevent.
        print(CI_RUNNER_GATE_ABSENT)
        pytest.skip(CI_RUNNER_GATE_ABSENT)
    # The link RESOLVED, so agent-infra IS checked out at $AGENT_INFRA_PATH: a missing
    # gate file now means the premise moved or was renamed out from under this pin,
    # not that there is no premise to check. That is a FAIL, not a skip — the old
    # `.exists()` check folded both cases into the skip and so could never report a
    # premise that had silently stopped existing.
    assert CI_RUNNER_GATE.is_file(), (
        f"the `scripts` symlink resolves ({SCRIPTS_LINK} -> "
        f"{SCRIPTS_LINK.resolve()}) but {CI_RUNNER_GATE.name} is not there: the "
        f"cross-repo premise of this pin has been moved or renamed, so the premise "
        f"half can no longer be verified from this host — fix the path (or the pin) "
        f"rather than letting this guard drift out of reach")
    gate = CI_RUNNER_GATE.read_text()
    # The premise this pin rests on lives in ANOTHER repo's script, reached here
    # through the machine-local `scripts` pointer (#9475 — gitignored, not tracked;
    # materialised per machine from $AGENT_INFRA_PATH, absent until then). Pinning a
    # literal against an external,
    # changeable fact with nothing asserting that fact is the "comment nobody
    # re-reads" failure this test criticises: harden the gate to attach a
    # VERSION-PINNED label instead of `ubuntu-latest` and this test would stay green
    # while `test-slow` silently returned to the ARM64 pool.
    #
    # The assignment shape is NOT part of the premise: agent-infra may quote it, drop
    # the quotes, or `export` it. Only the VALUE is contractual, so the matcher
    # tolerates the spelling instead of asserting it — but it must still FIND both, or
    # the premise is unreadable and this test must not pass by not looking.
    labels = _gate_pool_labels(gate)
    assert "FALLBACK_LABEL" in labels and any(
        name.startswith("POOL_LABEL") for name in labels), (
        f"cannot read FALLBACK_LABEL/POOL_LABEL* from {CI_RUNNER_GATE} (read "
        f"{sorted(labels) or 'nothing'}) — the premise of this pin is unverifiable, "
        f"so this test must not pass by not looking. If agent-infra renamed the "
        f"constants, update the matcher here in the same change that renamed them.")
    for label in labels.values():
        assert not re.fullmatch(r"ubuntu-\d+\.\d+", label), (
            f"ci_runner_gate.sh can attach {label!r} to the self-hosted pool, "
            f"which is the VERSION-PINNED class test-slow is pinned to: the pin "
            f"below no longer keeps this leg off the pool. Either the gate must "
            f"stop attaching version-pinned labels, or test-slow needs a "
            f"different isolation.")


def _gate_pool_labels(gate_text: str) -> dict[str, str]:
    """The labels `ci_runner_gate.sh` can attach to the self-hosted pool.

    Returned by a helper so the PARSING half of the cross-repo premise is testable
    WITHOUT agent-infra checked out — the gate file itself is host-only (see
    `CI_RUNNER_GATE_ABSENT`), but the matcher that reads it is not, and an unreadable
    matcher is how a guard stays green while protecting nothing.

    Only the VALUE is contractual; the assignment SHAPE is not, so the matcher accepts
    `export`, either quoting style, none, and leading whitespace. A commented-out
    assignment is not read — prose is not the value the gate will use.

    ⛔ THE NAME IS A FAMILY, NOT ONE CONSTANT (`POOL_LABEL*`). agent-infra split the
    pool label per architecture — `POOL_LABEL_ARM64="self-hosted-arm64"` and
    `POOL_LABEL_X64="self-hosted-x64"` — when it started measuring capacity per runner
    (agent-infra `4d20edd2`, #2904/#2897). Pinning the exact spelling `POOL_LABEL` made
    the premise UNREADABLE on every host whose agent-infra checkout is current, which
    reds this guard everywhere except CI (where `scripts/` is absent and the cross-repo
    half skips) — a pin that only fails where nobody looks. The family accepts both the
    old single constant and the split pair, and the discriminator below still runs over
    EVERY label it reads, so a split that reintroduces a version-pinned label still
    reds.
    """
    found: dict[str, str] = {}
    for match in re.finditer(
        r"^\s*(?:export\s+)?(FALLBACK_LABEL|POOL_LABEL\w*)=[\"']?([^\"'\s]+)",
        gate_text,
        re.MULTILINE,
    ):
        found[match.group(1)] = match.group(2)
    return found


def test_the_gate_label_matcher_reads_the_spellings_and_rejects_the_unsafe_one():
    """The premise half's PARSER and its DISCRIMINATOR, executed with no agent-infra.

    `test_slow_legs_are_pinned_off_the_ambiguous_runner_label` skips its cross-repo
    half on every runner, so without this test the regexes that read
    `ci_runner_gate.sh` and the `ubuntu-<version>` rejection below them would never
    execute anywhere — and a guard that cannot fail is not a guard. Three claims are
    pinned here, all against crafted gate text:

    1. the shipped spelling is read (double-quoted, line start);
    2. a cosmetic change in agent-infra — `export`, single quotes, no quotes, leading
       whitespace — does not make the labels unreadable (an unreadable premise must
       fail loudly on the host, not silently weaken the pin);
    3. the rejection FIRES: a gate that starts attaching `ubuntu-24.04` to the pool is
       reported, which is the drift this pin exists to notice.
    """
    shipped = 'FALLBACK_LABEL="ubuntu-latest"\nPOOL_LABEL="ubuntu-latest"\n'
    expected = {"FALLBACK_LABEL": "ubuntu-latest", "POOL_LABEL": "ubuntu-latest"}
    assert _gate_pool_labels(shipped) == expected
    # The SPLIT spelling agent-infra ships today (`POOL_LABEL_ARM64`/`_X64` since it
    # measures capacity per runner): the family must read BOTH, or a current
    # agent-infra checkout makes the premise unreadable on the host while CI skips it.
    split = (
        'FALLBACK_LABEL="ubuntu-latest"\n'
        'POOL_LABEL_ARM64="self-hosted-arm64"\n'
        'POOL_LABEL_X64="self-hosted-x64"\n'
    )
    assert _gate_pool_labels(split) == {
        "FALLBACK_LABEL": "ubuntu-latest",
        "POOL_LABEL_ARM64": "self-hosted-arm64",
        "POOL_LABEL_X64": "self-hosted-x64",
    }, "the split pool label was not read (agent-infra 4d20edd2)"
    for spelling in (
        "export FALLBACK_LABEL='ubuntu-latest'\nexport POOL_LABEL='ubuntu-latest'\n",
        "FALLBACK_LABEL=ubuntu-latest\nPOOL_LABEL=ubuntu-latest\n",
        'FALLBACK_LABEL="ubuntu-latest" # inline note\nPOOL_LABEL="ubuntu-latest"\n',
    ):
        assert _gate_pool_labels(spelling) == expected, spelling
    # A commented-out assignment is not an assignment: prose must not stand in for the
    # value the gate will actually use.
    assert _gate_pool_labels('# FALLBACK_LABEL="ubuntu-latest"\n') == {}
    # The discriminator itself, executed: this is the arm that has never run on a
    # runner and decides whether the pin still protects anything.
    drifted = 'FALLBACK_LABEL="ubuntu-latest"\nPOOL_LABEL="ubuntu-24.04"\n'
    labels = _gate_pool_labels(drifted)
    assert labels == {"FALLBACK_LABEL": "ubuntu-latest", "POOL_LABEL": "ubuntu-24.04"}
    assert [lbl for lbl in labels.values() if re.fullmatch(r"ubuntu-\d+\.\d+", lbl)] == [
        "ubuntu-24.04"
    ]


def test_carve_out_shard_bounds_clear_the_committed_work_without_dwarfing_it():
    """#3239: each shard's watchdog must clear THAT SHARD's committed work with
    the house headroom, and the job's cap must not dwarf the work it backstops.

    The watchdog's floor is the committed `durations` map for the shard (a lower
    bound that moves with the code — so a carve-out set that grows past the
    budget reds here instead of surfacing as a 90-minute hang). The frozen API
    measurement is a WHOLE-SET quantity: after W37 it bounds the cap from above
    (the cap may not dwarf the measured step) rather than flooring a watchdog,
    because one shard carries only a fraction of that work.

    W37: with the carve-out SHARDED the watchdog floor and the cap proportion
    are PER SHARD. The invariant that matters is no longer "the one job's
    watchdog clears the whole set" (that is the sum, and the sum is precisely
    the 16.9m wall this change removes) but "EVERY shard's watchdog clears THAT
    SHARD's committed work by the house headroom" — checked for every shard,
    because one under-budgeted shard is a guaranteed red and averaging would
    hide it."""
    job = _load_python_ci()["jobs"]["test-carve-out"]
    cap = job["timeout-minutes"]
    measured = _MEASURED_PYTEST_MAX_MIN["test-carve-out"]
    from tools.ci_selection import _duration_weight
    manifest = load_manifest()
    idx = _carve_matrix()
    durations = manifest["durations"]
    for entry in idx["include"]:
        files = entry["files"].split()
        assert files, f"carve shard {entry['suffix']!r} is empty — a dropped leg"
        # An absent row is weighed at the PACK's default (DEFAULT_FAST_WEIGHT),
        # never 0.0: 0.0 would understate the shard's committed work against the
        # very watchdog that has to clear it. Three carve-out files carry no row
        # today, so the default — not a hard failure — is the honest bound here;
        # the slow legs above fail closed because every one of their files is
        # measured.
        committed = sum(_duration_weight(durations.get(f + ".py"))
                        for f in files) / 60.0
        wd = entry["watchdog_minutes"]
        assert wd >= WATCHDOG_HEADROOM * committed, (
            f"carve shard {entry['suffix']!r} watchdog ({wd}m) no longer clears "
            f"its committed estimate ({committed:.2f}m) by WATCHDOG_HEADROOM "
            f"({WATCHDOG_HEADROOM}x) — re-derive it from a fresh measurement "
            f"rather than widening the hang window (#3239)")
        # PROPORTION, per shard: the cap backs a SHARD now, so the bound it must
        # not dwarf is the shard's own watchdog, not the whole set's work.
        assert cap <= _CAP_CEILING_FACTOR * wd, (
            f"the carve-out cap ({cap}m) dwarfs the per-shard watchdog it "
            f"backstops ({wd}m) by more than {_CAP_CEILING_FACTOR}x — the cap "
            f"IS the stale-head exposure (#3239)")
    # And the whole-set measurement stays an upper bound on the cap, so a
    # future shard count cannot quietly multiply the exposure past the work.
    assert cap <= _CAP_CEILING_FACTOR * measured, (
        f"the carve-out cap ({cap}m) dwarfs the measured pytest step "
        f"({measured}m) — the cap IS the stale-head exposure, and 90m against "
        f"this work is exactly the defect #3239 reports")


def test_the_carve_out_job_restores_the_pip_cache_without_saving_it():
    """#10182/#5219: the carve-out job must be RESTORE-ONLY on the pip cache.

    `actions/cache` saves in a POST step, and (#10182) that post step is charged
    against the job's `timeout-minutes` like any other step. This job cannot
    amortize such a save, and no cap it is allowed to carry can: the cap above
    is pinned at `_CAP_CEILING_FACTOR` x the measured 16.02m pytest step (= 48m
    at the ceiling), while ONE shard already spends its 19m floor-clamped
    watchdog plus ~1.5m of setup/provision — and the SAVE has no timeout of its
    own, so a slow one is unbounded, not merely large. Headroom amortizes a
    bounded cost; it cannot amortize an unbounded one.

    MEASURED (run 41100829071, job `test-carve-out (b)` 124223444008): the
    restore was a PARTIAL hit, so the post step reserved the exact key and began
    a ~3.6 GB save. Every WORK step passed — pytest finished at 18:10:55, the
    orphan gate said 'no orphans' at 18:10:53 — and then `Post Cache pip
    dependency caches` ran 18:12:33 → 18:29:46 against a 17:59:19 start, when
    `The job has exceeded the maximum execution time of 30m0s`. GitHub reported
    `cancelled`, which `python-ci-gate` reads as red BY DESIGN (#5219: a
    cancelled leg certifies nothing) — so a GREEN shard reddened a REQUIRED
    check on a diff that never touched this job.

    Nothing is lost by not saving here: the key is SHARED
    (`pip-<os>-<hash uv.lock>`) and is still written on EVERY run by
    `test-d14-hosted-api` (unconditional, PR + push) and by `test-slow`, so this
    job only ever READ it. The ~90 MB HF embedding cache in the same job is
    deliberately left alone (measured 1m38s to save, and it is the leg's
    provisioning path — an uncached load skip-reds the dense assertion #4387);
    the pip cache is the ~3.6 GB term that does not fit.

    Non-vacuous in both directions: the pre-fix step is a bare `actions/cache@v4`
    on this path, which reds the first assertion; and the presence + shape of
    the restore is asserted too, so DELETING the cache step cannot pass.
    """
    job = _load_python_ci()["jobs"]["test-carve-out"]

    def _path_of(step: dict) -> object:
        return (step.get("with") or {}).get("path")

    pip_caches = [s for s in job.get("steps", []) if _path_of(s) == "~/.cache/pip"]
    assert pip_caches, (
        "the carve-out job no longer restores the shared pip cache at all — every "
        "shard would then download torch (140 wheels, ~3.2 GB) cold")
    saving = [s.get("uses") for s in pip_caches
              if str(s.get("uses") or "").startswith("actions/cache@")]
    assert not saving, (
        f"the carve-out job carries an implicit post-step cache SAVE {saving} on "
        "~/.cache/pip — that save is charged against the job's `timeout-minutes` "
        "(#10182) and this job's cap is pinned at <= 3x its measured pytest step, "
        "so the save can never be amortized. Use `actions/cache/restore@` here "
        "(#5219: the cancel it causes reds a REQUIRED check on a green shard).")
    restores = [s for s in pip_caches
                if s.get("uses") == "actions/cache/restore@v4"]
    assert len(restores) == 1, (
        "the carve-out's pip cache step must be exactly one "
        f"`actions/cache/restore@v4` (found {len(restores)} of {len(pip_caches)} "
        "pip-cache steps in that shape)")
    assert restores[0].get("continue-on-error") is True, (
        "the carve-out's pip-cache restore must be `continue-on-error: true` — a "
        "cache-SERVICE error (transport/503) must never red the leg; a miss is "
        "the fail-open case and install-cpu-torch downloads the pinned CPU "
        "wheel either way")


def _carve_matrix() -> dict:
    """W37: the carve-out matrix the workflow ACTUALLY expands.

    Derived from `push_legs`, not rebuilt from `carve_out`: the point of every
    assertion below is to inspect the object `test-carve-out` receives. A
    helper that re-packed the set independently would happily agree with itself
    while the emitted matrix was wrong.
    """
    return carve_matrix_include(push_legs(load_manifest())["carve_shards"])


def test_carve_out_matrix_partitions_the_leg():
    """W37: sharding must PARTITION the carve-out set — every file in exactly
    one shard.

    This is the coverage pin, and it is the one that matters most: the whole
    justification for sharding the embedded carve-out suite is that a dropped
    file would be a SILENTLY green, permanently unexecuted gate (the exact
    failure #4047 was about)."""
    manifest = load_manifest()
    # The emitted leg, NOT `manifest["carve_out"]`: `push_legs` STRIPS the `.py`
    # suffix (the workflow re-adds it), so comparing against the raw manifest
    # list would compare `test_config` with `test_config.py` and report the whole
    # set as simultaneously missing AND extra. Compare like with like.
    del manifest  # kept for the reader: the raw list is deliberately not used
    expected = set(push_legs(load_manifest())["carve_out"])
    got: list[str] = []
    for entry in _carve_matrix()["include"]:
        got += entry["files"].split()
    assert len(got) == len(set(got)), (
        f"a carve-out file is in MORE THAN ONE shard: "
        f"{sorted(f for f in got if got.count(f) > 1)}")
    assert set(got) == expected, (
        f"the carve shards must partition the carve_out leg: "
        f"missing={sorted(expected - set(got))} extra={sorted(set(got) - expected)}")


def test_the_platform_gated_frozen_files_are_owned_by_the_carve_out_leg():
    """W37: every file the frozen platform-gated manifest names must run in a
    carve-out shard.

    The workflow passes the frozen manifest VERBATIM but narrows it with
    `--scope` to the files THIS shard runs, so a frozen nodeid is required
    EXACTLY by the shard that owns its file. If that file leaves `carve_out` (or
    a nodeid is added for a file that was never in it), no shard's scope matches
    it: every shard requires none of its nodeids and passes, and the
    platform-gated manifest's own runtime enforcement stops silently. The
    workflow comment claims the union over shards still requires the whole
    frozen set; this pins the coverage that claim depends on.

    Derive the leg from `push_legs` and the files from the MANIFEST, never from
    a hardcoded shard name, so the pin follows the emitted matrix and the frozen
    file rather than becoming a second copy of them.
    """
    frozen = {
        Path(nid.split("::", 1)[0]).name.removesuffix(".py")
        for nid in _frozen_nodeids("platform-gated.txt")
    }
    assert frozen, "platform-gated.txt declares no nodeids — nothing to own"
    leg = set(push_legs(load_manifest())["carve_out"])
    unowned = sorted(frozen - leg)
    assert not unowned, (
        f"these files are named by config/ci-expected-nodeids/platform-gated.txt "
        f"but no carve-out shard runs them, so no shard's `--scope` ever requires "
        f"their nodeids and the frozen set's runtime enforcement is silently "
        f"gone: {unowned} — add them to `carve_out` in config/ci-surfaces.yml "
        f"(the frozen manifest must keep an owner in the sharded lane)"
    )


def test_carve_out_matrix_keeps_the_bare_name():
    """W37: shard 0's job name must be EXACTLY `test-carve-out`.

    The merge rail's lane-parity subtracts main's job names from the PR's, so
    an unadopted main holds the bare `test-carve-out`. Naming shard 0
    `test-carve-out (a)` would make this a RENAME — one of main's names would
    be missing from the PR and every merge would refuse until this landed.
    Keeping the bare name makes the shard set a strict SUPERSET, which is the
    same property `fast_shards` relies on when it keeps `a` and `b`."""
    entries = _carve_matrix()["include"]
    assert entries[0]["suffix"] == "", (
        "shard 0 must carry the bare `test-carve-out` name (empty suffix) — a "
        "suffixed shard 0 renames main's job and refuses every merge")
    suffixes = [e["suffix"] for e in entries]
    assert len(set(suffixes)) == len(suffixes), (
        f"two carve shards would produce the SAME check name: {suffixes} — "
        f"GitHub would silently drop one, losing its coverage")
    for s in suffixes[1:]:
        assert s.startswith(" (") and s.endswith(")"), (
            f"a non-first shard suffix must be ` (<label>)`, got {s!r}")


def test_carve_out_artifact_name_consumes_a_distinct_per_leg_label():
    """#6263/W37: the carve-out matrix must carry a `label` the upload step
    actually consumes.

    `test_a_matrix_job_never_uploads_a_fixed_name_artifact` cannot check this
    job: its matrix comes from `fromJSON(needs.changes.outputs.carve_matrix)`,
    an unresolvable string, so the name check degrades to "does it mention
    `matrix.`" and never verifies the key EXISTS. GitHub renders an undefined
    matrix key as the empty string, so renaming the emitted `label` field (or the
    upload name's `matrix.label`) would restore the per-leg 409 collision with
    the whole suite green — the #6263 class. Resolve the emitted matrix and pin
    both halves: the key is present and distinct per shard, and the upload step's
    `name:` is the templated string that consumes it.
    """
    entries = _carve_matrix()["include"]
    labels = [e.get("label") for e in entries]
    assert all(isinstance(lab, str) and lab for lab in labels), (
        f"every carve shard must carry a non-empty `label` (the artifact-name "
        f"tail), got {labels!r} — an undefined `matrix.label` renders as ''"
    )
    assert len(set(labels)) == len(labels), (
        f"two carve shards would upload under the SAME artifact name: {labels} "
        f"— upload-artifact@v4 409s and, because the step is continue-on-error, "
        f"both legs' logs vanish silently (#6263)"
    )
    job = _load_python_ci()["jobs"]["test-carve-out"]
    up = next(s for s in job["steps"]
              if str(s.get("uses") or "").startswith("actions/upload-artifact"))
    name = str((up.get("with") or {}).get("name") or "")
    assert "${{ matrix.label }}" in name, (
        f"the carve-out artifact name ({name!r}) must interpolate the emitted "
        f"per-leg `label` — a fixed name, or any other matrix key, collides "
        f"across shards (#6263)"
    )
    rendered = [
        name.replace("${{ github.job }}", "test-carve-out")
            .replace("${{ matrix.label }}", lab)
        for lab in labels
    ]
    assert len(set(rendered)) == len(rendered), (
        f"two carve shards would upload under the SAME artifact name: {rendered} "
        f"— upload-artifact@v4 409s and the step's continue-on-error makes the "
        f"loss silent (#6263)"
    )


def test_carve_shards_declaration_is_fail_closed():
    """W37: a malformed `carve_shards` is an ERROR, never a silent default.

    Same polarity as `fast_shard_issues`, with one deliberate difference: 1 is
    LEGAL here. It is the unsharded job this repo ran until now, so removing
    the key (or setting 1) must be a complete, safe rollback — 0 would delete
    the lane, and the `a..z` ceiling is the same one the fast band has."""
    ok = load_manifest()
    assert carve_shard_issues(ok) == [], carve_shard_issues(ok)
    assert carve_shard_count(ok) >= 2, (
        "this repo exists to shard the carve-out; a count of 1 means the key "
        "was removed and the pin below should be revisited")
    for bad, why in ((None, "explicit null"), ("3", "a string"),
                     (True, "a bool"), (3.5, "a float"), (0, "zero"),
                     (-1, "negative"), (MAX_CARVE_SHARDS + 1, "above the a..z ceiling")):
        m = dict(ok, carve_shards=bad)
        assert carve_shard_issues(m), f"{why} ({bad!r}) must be an error"
        assert carve_shard_count(m) == DEFAULT_CARVE_SHARDS == 1, (
            f"{why} must fail SAFE to the unsharded shape, not to a number "
            f"nobody chose")
    # Absence is NOT an error — it is 'not adopted', and it must give back the
    # exact unsharded job (one shard, bare name).
    m = {k: v for k, v in ok.items() if k != "carve_shards"}
    assert carve_shard_issues(m) == []
    assert carve_shard_count(m) == 1
    one = carve_matrix_include(push_legs(m)["carve_shards"])["include"]
    assert len(one) == 1 and one[0]["suffix"] == "", (
        "an unadopted manifest must emit ONE shard named exactly "
        "`test-carve-out` — the shape this repo ran before the split")
    assert set(one[0]["files"].split()) == set(push_legs(m)["carve_out"])


def test_diff_gated_jobs_consume_changes_outputs():
    """#2147/#2148 (2026-09-02-ci-audit F1/F2): the workflow wiring pins.
    test-slow + test-carve-out gate their runs on the changes job's
    slow_run / carve_out_run outputs (docs/config-only PRs skip both legs);
    the changes job emits + echoes the diff-gate outputs; test-slow's
    manifest + run steps intersect the DERIVED leg rows with
    slow_selected on the tier-2 shape (full == false) so only the matched
    surfaces' slow files run. test-slow's matrix is the selector's
    `${{ fromJSON(needs.changes.outputs.slow_matrix) }}` — there are NO
    literal rows to keep in sync; the in-workflow drift-guard step pins the
    union of the EXPANDED legs to slow_gating_files() (#9539; slow minus
    carve-out minus the non-gating lane since #10194)."""
    wf = _load_python_ci()
    # job-level gates
    assert wf["jobs"]["test-slow"]["if"] == \
        "needs.changes.outputs.slow_run == 'true'", \
        "test-slow must diff-gate on slow_run (#2148)"
    cj_if = wf["jobs"]["test-carve-out"]["if"]
    assert "carve_out_run == 'true'" in cj_if, \
        "test-carve-out must diff-gate on carve_out_run (#2147)"
    # changes job: outputs + echoes
    outs = wf["jobs"]["changes"]["outputs"]
    for key in ("slow_run", "carve_out_run", "slow_selected"):
        assert key in outs, f"changes job must emit {key}"
    sel_step = next(s for s in wf["jobs"]["changes"]["steps"]
                    if s.get("id") == "select")
    for key in ("slow_run=", "carve_out_run=", "slow_selected="):
        assert key in sel_step["run"], f"select step must echo {key}"
    # test-slow tier-2 intersection (manifest + run steps must share it)
    for want in ("Generate coverage manifest", "Run slow test suite"):
        step = next(s for s in wf["jobs"]["test-slow"]["steps"]
                    if s.get("name", "").startswith(want))
        run = step["run"]
        assert "needs.changes.outputs.slow_selected" in run, \
            f"test-slow '{want}' must consume slow_selected (tier-2 scoping)"
        assert 'case " $SLOW_SEL " in' in run, \
            f"test-slow '{want}' must intersect with slow_selected"
    # #9539: the slow legs are the DERIVED matrix (`fromJSON(...slow_matrix)`),
    # not committed rows — the same shape `test`/`test-carve-out` use. What the
    # pin still checks is what mattered about the old rows: every leg entry
    # exists and is declared in `slow_files` (a dead or unclassified entry is
    # the #1260/#1270 drift class), the union is the whole pool minus the
    # carve-out set, and no file rides two legs.
    matrix = wf["jobs"]["test-slow"]["strategy"]["matrix"]
    assert matrix == "${{ fromJSON(needs.changes.outputs.slow_matrix) }}", (
        "test-slow's matrix must be the selector's slow_matrix (no literal rows, "
        f"#9539), got {matrix!r}")
    assert "slow_matrix" in outs, "changes job must publish slow_matrix (#9539)"
    push_step = next(s for s in wf["jobs"]["changes"]["steps"]
                     if s.get("id") == "push-matrix")
    assert "slow_matrix=" in push_step["run"], (
        "the push-matrix step must echo slow_matrix (#9539)")
    inc = slow_matrix_include(slow_shard_entries(load_manifest()))["include"]
    assert len(inc) == slow_shard_count(load_manifest()) >= 2
    from tools.ci_selection import TESTS_DIR
    _slow = set(load_manifest()["slow_files"])
    seen: set[str] = set()
    for row in inc:
        tokens = row["files"].split()
        assert tokens, "a derived slow leg must never be empty"
        for token in tokens:
            rel = f"{token}.py"
            assert (TESTS_DIR / rel).exists(), \
                f"slow leg entry {rel} does not exist under tests/"
            assert rel in _slow, \
                f"slow leg entry {rel} is not declared in slow_files"
            assert rel not in seen, f"slow leg entry {rel} rides two legs"
            seen.add(rel)
    assert seen == {f + ".py" for f in slow_leg_files(load_manifest())}, (
        "the legs must cover slow_gating_files() exactly (no gap, no dup): slow "
        "minus carve-out minus the non-gating lane")


def test_slow_selected_echo_transform_roundtrips_into_legs():
    """#2159 review P2-1: the changes job's slow_selected echo strips the
    .py suffix and space-joins BARE names; test-slow's tier-2 shape then
    case-matches them against the DERIVED leg rows (matrix.files, #9539). This
    pin reproduces the echo transform for every selection shape and asserts
    the round-trip holds — a strip/join format drift (suffix leak, JSON,
    newline) would silently empty EVERY leg to a green gate, and this
    tier-2 partial shape never runs in CI outside a real tier-2 PR."""
    m = load_manifest()
    # #9539: the legs are the DERIVED matrix — the same set the run step
    # expands from `changes.outputs.slow_matrix` — so the round-trip is checked
    # against what will actually run rather than against committed rows.
    legs = set(slow_leg_files(m))
    shapes = [
        ([], "push"),
        ([], "schedule"),
        (["docs/README.md"], "pull_request"),
        (["tortoise/sdk.py"], "pull_request"),
        (["tortoise/ep.py"], "pull_request"),
        (["tests/test_w4_why_enrichment.py"], "pull_request"),
        (["mystery-dir/x.py"], "pull_request"),
        (["tools/ci_selection.py"], "pull_request"),  # #2159 P2-3: full
    ]
    for changed, event in shapes:
        r = select(changed, event, m)
        # the echo transform, verbatim from python-ci.yml
        transformed = [
            x[:-3] if x.endswith(".py") else x for x in r["slow_selected"]]
        assert all(".py" not in x for x in transformed), \
            f"suffix leak in echo output ({changed}/{event}): {transformed}"
        assert set(transformed) <= legs, \
            f"slow_selected escapes the committed slow legs ({changed}/{event})"
        assert bool(r["slow_run"]) == bool(transformed), \
            f"slow_run must imply a non-empty leg intersection ({changed}/{event})"


def test_canary_streak_job_consumes_producer_artifacts_only():
    """Task 9 Step 6 (cycle-5 P1-7/cycle-6 P1-7): the canary-streak job is
    post-merge only (push), needs [test] (matrix fan-in), consumes
    the PRODUCER-SHARD artifact set (#6135: the fixed-name copy the producer
    shard uploads — never a literal `pytest-log-test-b`, which stopped naming a
    real leg once the fast pool grew past two) plus the previous streak
    artifact via the classifier, and uploads the new streak. It must never read
    a steps-output value (the classifier's own pin lives in
    tests/test_canary_classify.py)."""
    wf = _load_python_ci()
    job = wf["jobs"]["canary-streak"]
    assert job["needs"] == "test"  # single-need YAML collapses to a string
    assert "always()" in job["if"], \
        "canary-streak must run on RED runs too (implicit success() would skip it" \
        " whenever a test leg fails — the streak would freeze instead of reset)"
    assert "github.event_name == 'push'" in job["if"], \
        "the streak population is post-merge full-matrix only"
    steps = job["steps"]
    dl = next(s for s in steps
              if s.get("name", "").startswith("Download producer-shard artifacts"))
    assert dl["with"]["name"] == "pytest-canary-producer"
    classify = next(s for s in steps
                    if s.get("name", "").startswith("Classify run"))
    crun = classify["run"]
    assert "tools/testdb_canary_classify.py" in crun
    assert "--junitxml artifacts/junit.xml" in crun
    assert "--manifest artifacts/expected-nodeids.txt" in crun
    assert "--step-wall artifacts/step_wall.txt" in crun
    assert "--divergence-log" in crun
    assert "--producer-marker artifacts/canary-producer.json" in crun
    assert "prev-streak" in crun and "--out config/testdb-canary-streak.json" in crun
    assert "$GITHUB_OUTPUT" not in crun, \
        "the classifier must never consume a steps-output value (artifacts only)"
    up = next(s for s in steps
              if s.get("name", "").startswith("Upload canary streak"))
    assert up["with"]["name"] == "testdb-canary-streak"
    assert up["with"]["path"] == "config/testdb-canary-streak.json"


def test_a_matrix_job_never_uploads_a_fixed_name_artifact():
    """#6263: in a job with a matrix dimension, an artifact name that does not
    carry the leg makes the legs collide.

    `github.job` is the BASE job id for every matrix leg, so a name built from
    it alone is uploaded once PER LEG under ONE name.
    `ci-timing.yml` downloads by pattern with `merge-multiple` unset (false),
    which places same-named artifacts in ONE directory — so one leg's
    `pytest.log` overwrites the other's, and the durations map measures one
    leg while reporting the whole job. Its guard cannot see this: it compares
    a file count against a positive artifact count only at zero, so a partial
    fetch is indistinguishable from a complete one and the loss is silent.

    observed in this repo: the #3467 plan's defect (i) is the same instance
    this change closes (see the plan's own defect list), and `#6135` is the
    other mechanism — a CONSUMER hardcoding a shard name rather than a
    producer reusing one.

    A name is accepted when it interpolates a matrix key the job actually
    declares, or when the step (or its job) carries an `if:` that mentions the
    matrix — that is why `pytest-canary-producer` is safe, its `if:` requires
    `matrix.canary_producer`. The matrix is resolved from the workflow, not
    kept as a hand-maintained allowlist that the next edit silently outgrows.
    """
    offenders = []
    for job_name, job in _load_python_ci()["jobs"].items():
        matrix = (job.get("strategy") or {}).get("matrix")
        if not matrix:
            continue
        # A `matrix:` built by `fromJSON(...)` is a string here and cannot be
        # resolved statically; only a literal mapping can be checked for the
        # key an interpolated name refers to.
        declared: set[str] = set()
        resolvable = isinstance(matrix, dict)
        if resolvable:
            declared.update(k for k in matrix if k != "include")
            for row in matrix.get("include") or []:
                if isinstance(row, dict):
                    declared.update(row)
        for step in job.get("steps") or []:
            if not str(step.get("uses") or "").startswith("actions/upload-artifact"):
                continue
            name = str((step.get("with") or {}).get("name") or "")
            if "matrix." in str(step.get("if") or "") or "matrix." in str(job.get("if") or ""):
                continue
            if "matrix." in name:
                # GitHub renders an UNDEFINED matrix property as the empty
                # string, so a typo'd key yields the SAME name for every leg
                # and the collision returns with the guard green.
                keys = set(re.findall(r"matrix\.(\w+)", name))
                if not resolvable or keys <= declared:
                    continue
            offenders.append(f"{job_name} uploads {name!r}")
    assert not offenders, (
        "a matrix job uploads a fixed-name artifact, so its legs collide and "
        "one leg's files are overwritten by the other's without any check "
        "failing (#6263; same root as the #3467 plan's defect (i)): "
        + "; ".join(offenders))


def test_workflow_lint_pins_the_actionlint_image_and_its_scope():
    """Pin the `workflow-lint` step's body.

    The plumbing guards (needs/LEGS) prove the leg is wired in; they cannot
    see its body. Without this pin the step can be replaced with `echo ok`,
    the image unpinned to `:latest`, the `*.yaml` glob dropped, or the
    empty-match refusal deleted, and every test stays green while the leg
    stops being a check — the same fail-open shape #6253 is about.
    """
    job = _load_python_ci()["jobs"]["workflow-lint"]
    raw = "\n".join(
        str(step.get("run") or "") for step in job.get("steps") or []
    )
    # Every assertion below targets EXECUTABLE-looking text: `#`-comment lines
    # are stripped, shell line-continuations are joined, and the invocation is
    # pinned STRUCTURALLY, which closes the specific no-op the earlier revision
    # let through (a commented-out body carrying `actionlint:1.7.12
    # -shellcheck= *.yaml` plus the copied empty-match block).
    #
    # WHAT THIS DOES NOT GUARANTEE, stated because the limit is real: a static
    # regex over a `run:` scalar can prove the command is WRITTEN, never that it
    # EXECUTES. A body that prints the script from a quoted heredoc, or that
    # exits 0 before reaching it, still satisfies every assertion below. This is
    # therefore a TRIPWIRE against the realistic no-op replacements (deleting
    # the step's work, commenting it out, unpinning the image, dropping the
    # `*.yaml` glob or the empty-match refusal) — not proof of execution. Real
    # proof needs a runtime positive control in the step itself (lint a
    # deliberately malformed temp workflow and assert actionlint exits
    # non-zero), which is a change to the leg, not to this test.
    runs = "\n".join(
        line for line in raw.splitlines() if not line.lstrip().startswith("#")
    )
    # Join shell line-continuations so a command split across lines (the
    # `docker run … \` + `  rhysd/actionlint:…` form) is matched as ONE command.
    joined = re.sub(r"\\\n\s*", " ", runs)
    assert re.search(
        r"(?m)^\s*docker\s+run\b[^\n]*\brhysd/actionlint"
        r"(?::\d+\.\d+\.\d+(?:@sha256:[0-9a-f]{64})?|@sha256:[0-9a-f]{64})\b"
        r"[^\n]*-shellcheck=",
        joined,
    ), (
        "workflow-lint no longer executes `docker run … rhysd/actionlint:<pinned> "
        "-shellcheck=` on a non-comment line: a body that only MENTIONS those "
        "tokens would satisfy a presence check while linting nothing (#6253)"
    )
    assert "actionlint" in runs, (
        "workflow-lint no longer invokes actionlint — the leg would certify "
        "nothing while still reporting green (#6253)"
    )
    tags = re.findall(r"actionlint:(\S+)", runs)
    digest_only = re.search(r"actionlint@sha256:[0-9a-f]{64}\b", runs)
    assert tags or digest_only, (
        "workflow-lint does not pin the actionlint image by tag or digest"
    )
    for tag in tags:
        # A digest pin (`tag@sha256:…`) is STRONGER than a tag pin and is the
        # supply-chain-correct form, so it must pass this check too. A
        # digest-ONLY reference carries no `tag:` at all and is matched above.
        assert re.fullmatch(r"\d+\.\d+\.\d+(?:@sha256:[0-9a-f]{64})?", tag), (
            f"actionlint image must be pinned to major.minor.patch, got {tag!r} "
            "— a floating tag re-opens the unpinned-dependency class (#5440)"
        )
    assert "-shellcheck=" in runs, (
        "the actionlint invocation must set -shellcheck= explicitly: the pinned "
        "image bundles shellcheck, so defaulting it on would add this tree's "
        "shellcheck findings to the required gate"
    )
    assert "*.yaml" in runs, (
        "workflow-lint globs only *.yml; GitHub loads workflows with either "
        "extension, so a .yaml workflow would be silently unlinted"
    )
    # The empty-match refusal is asserted STRUCTURALLY, not by looking for two
    # loose substrings: a substring pin is satisfied by commenting the body out.
    assert re.search(
        r"if\s+\[\s*\$\{#files\[@\]\}\s*-eq\s+0\s*\]\s*;\s*then\s*\n"
        r"\s*echo\s+\S.*\n"
        r"\s*exit\s+1\s*\n"
        r"\s*fi\b",
        runs,
    ), (
        "workflow-lint no longer refuses an EMPTY match: an unmatched glob "
        "would lint nothing and report success over an empty surface"
    )
    # `$files` unquoted was a real SC2086 this leg introduced into a tree whose
    # other shellcheck findings are pre-existing. The array form keeps it fixed.
    assert '"${files[@]}"' in runs, (
        "workflow-lint must expand the glob array quoted (\"${files[@]}\"); a "
        "bare `$files` is a new SC2086 in a tree pinned to -shellcheck="
    )


def _extract_pytest_marker(run_script: str) -> str:
    """Pull the `-m <marker>` filter from a job's GATING pytest run. The
    docker lanes quote it (`-m 'not track_b and not live'`); the track-b
    job's is bare (`-m track_b`). The launcher's own `python -m pytest`
    module form is never a marker — the unquoted fallback skips 'pytest'.

    #6142: the `test` job now has a SECOND pytest invocation — the last-failed
    pre-phase. Scanning the whole script silently retargets this pin to the
    pre-phase (the same class of bug fixed in tests/test_skip_guard.py), so the
    pre-phase line is dropped and the search stays on the canonical gating
    invocation.

    #10028: the exclusion is spelled on the PRE-PHASE's own junit indirection,
    not on a `/tmp/...` literal — `--junitxml="$PYTEST_JUNIT_LF"`. The retired
    literal appears nowhere any more, so leaving it here silently dropped
    NOTHING and the search fell through to the pre-phase (both markers happened
    to be identical, so the pin stayed green while no longer discriminating).
    Filtering on the LF token drops exactly the pre-phase: `"$PYTEST_JUNIT"` is
    not a substring of `"$PYTEST_JUNIT_LF"`."""
    searchable = "\n".join(
        ln for ln in run_script.splitlines()
        if '"$PYTEST_JUNIT_LF"' not in ln)
    quoted = _re.search(r"-m '([^']+)'", searchable)
    if quoted:
        return quoted.group(1)
    for m in _re.finditer(r"-m\s+([^\s]+)", searchable):
        if m.group(1) != "pytest":
            return m.group(1)
    raise AssertionError(f"no -m marker found in run script:\n{run_script}")


def test_run_marker_matches_manifest_marker():
    """#1787 (PR #1811): the live-probe seam raises the docker lanes' run
    filter to `-m 'not track_b and not live'` while the skip-guard manifest
    steps still carried the old `--marker "not track_b"` — skip-guard's
    contract (tools/skip-guard.py ~L107) requires the manifest's collect-only
    marker to match the run's marker EXACTLY, or the manifest expects nodeids
    the run deselects and every run reds on vanished nodeids. Pin the
    equality for ALL FOUR run jobs (fast/slow/carve-out + track-b) so a
    future edit that changes one side reds at test time instead of CI
    runtime. The track-b job (epic #902 A8/#1058) has the same vanished-
    nodeid class: its manifest collects with `--marker "track_b"` and its
    run filters with the UNQUOTED `-m track_b` — the extraction helper
    handles both quoted and bare forms."""
    wf = _load_python_ci()
    run_prefixes = {
        "test": "Run fast test suite",
        "test-slow": "Run slow test suite",
        "test-carve-out": "Run carve-out suite",
        "test-track-b": "Run Track B tests",
    }
    for job_name, run_prefix in run_prefixes.items():
        steps = wf["jobs"][job_name]["steps"]
        run = next(s for s in steps
                   if s.get("name", "").startswith(run_prefix))
        run_marker = _extract_pytest_marker(run["run"])
        manifest = next(s for s in steps
                        if s.get("name", "").startswith("Generate coverage manifest"))
        manifest_marker = _re.search(r'--marker "([^"]+)"', manifest["run"]).group(1)
        assert run_marker == manifest_marker, (
            f"{job_name}: run filter -m '{run_marker}' must equal the skip-guard "
            f"manifest marker '{manifest_marker}' (the manifest's collect-only "
            "must match the run's deselect set or every run reds on vanished "
            "nodeids — #1787)"
        )


def test_sweep_team_strays_env_gated_iff_uri():
    """#1886 (issue #1884): the journal-blind team_* stray-pass opt-in
    (TORTOISE_TEST_SWEEP_TEAM_STRAYS) is set under the SAME full==true gate
    as the docker URI on BOTH matrix jobs — the pass is safe ONLY on the
    dedicated fresh-per-job container; a future edit that sets it on a
    tier-2 embedded leg (or drops it from a docker leg) reds. Mirrors the
    EXPECT_URI iff-gate pin (cycle-7 P2-1)."""
    wf = _load_python_ci()
    for job in ("test", "test-slow"):
        steps = wf["jobs"][job]["steps"]
        compute = next(s for s in steps
                       if "Compute docker URI" in s.get("name", ""))
        script = compute["run"]
        gate = ('if [ "${{ needs.changes.outputs.full }}" = "true" ]; then')
        assert gate in script, f"{job}: the docker gate must be full==true"
        then_block = script.split("then", 1)[1].split("fi", 1)[0]
        assert 'SWEEP_TEAM_STRAYS="1"' in then_block, \
            f"{job}: opt-in must be set ONLY inside the full==true gate"
        outside_then = script.split("then", 1)[1].split("fi", 1)[1]
        assert 'SWEEP_TEAM_STRAYS="1"' not in outside_then, \
            f"{job}: opt-in must never be set outside the full==true gate"
        assert 'echo "SWEEP_TEAM_STRAYS=$SWEEP_TEAM_STRAYS" >> "$GITHUB_ENV"' \
            in script, f"{job}: opt-in must be exported"
        run = next(s for s in steps
                   if s.get("name", "").startswith(
                       "Run fast test suite" if job == "test"
                       else "Run slow test suite"))
        assert run["env"]["TORTOISE_TEST_SWEEP_TEAM_STRAYS"] \
            == "${{ env.SWEEP_TEAM_STRAYS }}", \
            f"{job}: pytest step must map the opt-in from the job env"


def test_track_b_docker_lane_sets_team_stray_opt_in():
    """#1886: test-track-b is a docker lane on a dedicated fresh-per-job
    container (TORTOISE_DB_URI + EXPECT_URI set) — the team_* stray-pass
    opt-in must be set there too, or the #1686 journal-blind closure is
    silently inert on that lane."""
    wf = _load_python_ci()
    job = wf["jobs"]["test-track-b"]
    # #6673: the URI moved from a job-level literal to the provision step's
    # `uri_graph` (the host port is Docker-assigned at runtime). The invariant
    # this pins is unchanged: test-track-b IS a docker lane on the test-prefixed
    # graph, so the #1886 journal-blind team_* closure must not go inert there.
    provision = next(s for s in job["steps"]
                     if str(s.get("uses", "")).endswith("falkordb-provision"))
    assert provision["with"]["uri_graph"] == "tortoise_test_matrix"
    env = job.get("env", {})
    assert env.get("TORTOISE_TEST_SWEEP_TEAM_STRAYS") == "1", \
        "test-track-b (dedicated docker lane) must set the team_* stray opt-in"


_LEGACY_OPT_IN_VAR = "TORTOISE_TEST_SWEEP_LEGACY"
_TEAM_OPT_IN_VAR = "TORTOISE_TEST_SWEEP_TEAM_STRAYS"


def _env_map(container: object, where: str) -> dict:
    """The ``env`` map of a workflow/job/step, or `{}` when absent.

    A non-mapping container (a malformed workflow shape) is a hard error, not
    a skip: this scanner backs a "the var is set by NO workflow" pin, so an
    unreadable shape must fail closed with a clear message rather than crash
    with an opaque `AttributeError` (or, worse, pass vacuously).
    """
    if not isinstance(container, dict):
        raise AssertionError(
            f"{where}: malformed workflow shape — expected a mapping, got "
            f"{type(container).__name__}"
        )
    env = container.get("env")
    if env is None:
        return {}
    if not isinstance(env, dict):
        raise AssertionError(f"{where}: `env` is not a mapping")
    return env


def _opt_in_sites(wf: dict, label: str, var: str) -> list[str]:
    """Where ``wf`` sets env var ``var`` — structured, never a raw-text scan.

    An ``env`` map key is a real setting at ANY of the three scopes GitHub
    Actions inherits through — workflow-level, job-level, step-level (a
    workflow-level token reaches every job and step, so scanning only the job
    and step maps would leave the pin green while CI armed the opt-in); a
    ``run`` script is inspected only after shell comments are stripped, so a
    YAML or shell comment that merely NAMES the variable is not a hit. Returns
    ``"<label>:<workflow>"`` / ``"<label>:<job>"`` / ``"<label>:<job> step N"``
    labels for assertion messages.
    """
    sites: list[str] = []
    if var in _env_map(wf, f"{label}:<workflow>"):
        sites.append(f"{label}:<workflow>")
    jobs = wf.get("jobs")
    if jobs is None:
        return sites
    if not isinstance(jobs, dict):
        raise AssertionError(f"{label}: `jobs` is not a mapping")
    for job_name, job in jobs.items():
        if var in _env_map(job, f"{label}:{job_name}"):
            sites.append(f"{label}:{job_name}")
        steps = job.get("steps")
        if steps is None:
            continue
        if not isinstance(steps, list):
            raise AssertionError(f"{label}:{job_name}: `steps` is not a list")
        for i, step in enumerate(steps, start=1):
            if var in _env_map(step, f"{label}:{job_name} step {i}"):
                sites.append(f"{label}:{job_name} step {i}")
            script = step.get("run")
            if isinstance(script, str):
                stripped = "\n".join(_strip_shell_comments(line)
                                     for line in script.splitlines())
                if var in stripped:
                    sites.append(f"{label}:{job_name} step {i} (run)")
    return sites


def test_legacy_residue_opt_in_is_never_set_in_ci():
    """#3634 Task 3: TORTOISE_TEST_SWEEP_LEGACY is a MANUAL operator opt-in and
    is set by NO workflow — not just by the one python-ci.yml lane.

    Contrast with the team-stray opt-in pinned just above: that pass is safe on
    a dedicated, fresh-per-job container (nothing accumulates there without it),
    so CI sets it inside the full==true docker gate. The legacy residue cohort
    lives on a LONG-LIVED dev docker whose residue may include a live eval or
    tenant name the next automated session does not own, so CI sets it on no
    lane — a future edit that exports it (any workflow, any job, any gate) reds
    by design.

    SCOPE: EVERY file in `.github/workflows/` (`.yml` and `.yaml`, via
    `_workflow_files`), parsed as YAML. The original python-ci.yml-only text pin
    was too narrow: `post-merge-validation.yml` already sets the SIBLING
    destructive opt-in (`TORTOISE_TEST_SWEEP_TEAM_STRAYS`) and was unscanned, so
    "nowhere in python-ci.yml" was not the claim the docstring made. Analysis is
    structured, not raw text: the variable must not be a workflow/job/step
    `env` key nor appear in a `run` script after comments are stripped, so a
    comment that merely NAMES it does not red.
    """
    wf_dir = Path(__file__).resolve().parents[1] / ".github" / "workflows"
    workflows = _workflow_files(wf_dir)
    assert workflows, "no workflow files found — the scan would pass vacuously"
    import yaml
    parsed = [(p.name, yaml.safe_load(p.read_text()) or {}) for p in workflows]

    # POSITIVE CONTROL — the same scanner must FIND the sibling destructive
    # opt-in that CI deliberately sets; without it, a scanner (or a glob) that
    # found nothing would satisfy this pin vacuously.
    team_sites = [s for label, wf in parsed
                  for s in _opt_in_sites(wf, label, _TEAM_OPT_IN_VAR)]
    assert team_sites, (
        "the scanner did not find TORTOISE_TEST_SWEEP_TEAM_STRAYS in any "
        "workflow, though post-merge-validation.yml sets it — the scan is "
        "vacuous, not clean"
    )

    offenders = [s for label, wf in parsed
                 for s in _opt_in_sites(wf, label, _LEGACY_OPT_IN_VAR)]
    assert not offenders, (
        "the legacy residue opt-in is a manual operator action — never a CI "
        "setting; found in " + ", ".join(offenders)
    )


def test_opt_in_scanner_reads_workflow_level_env():
    """#3634 Task 3 (N1): `_opt_in_sites` must read the WORKFLOW-level `env:`
    map, not only `jobs.<id>.env` / `jobs.<id>.steps[].env`.

    GitHub Actions inherits a workflow-level `env` into every job and step, so
    a top-level `TORTOISE_TEST_SWEEP_LEGACY: "1"` arms the destructive manual
    opt-in on every lane while a jobs-only scan stays green — the exact bypass
    the pin above exists to close. Synthetic, not the real files, so deleting
    the workflow-level branch reds HERE directly.
    """
    wf = {"env": {_LEGACY_OPT_IN_VAR: "1"},
          "jobs": {"test": {"steps": [{"run": "echo hi"}]}}}
    assert _opt_in_sites(wf, "wf", _LEGACY_OPT_IN_VAR) == ["wf:<workflow>"], \
        "the workflow-level `env` map is not scanned"
    # Keyed, not prose: a sibling var is untouched, and a `run` comment that
    # merely names the var is not a setting.
    assert _opt_in_sites(wf, "wf", _TEAM_OPT_IN_VAR) == []
    assert _opt_in_sites(
        {"jobs": {"test": {"steps":
                            [{"run": "true  # " + _LEGACY_OPT_IN_VAR}]}}},
        "wf", _LEGACY_OPT_IN_VAR) == []
    # A malformed shape fails closed and legibly — a clear AssertionError, not
    # an AttributeError from `.get`/`.items` on a non-mapping.
    import pytest as _pytest
    for bad in ({"env": "x"}, {"jobs": []}, {"jobs": {"j": "x"}},
                {"jobs": {"j": {"steps": "x"}}},
                {"jobs": {"j": {"steps": ["x"]}}}):
        with _pytest.raises(AssertionError):
            _opt_in_sites(bad, "wf", _LEGACY_OPT_IN_VAR)


def test_drift_gate_cannot_skip_the_test_matrix():
    """#2656: the manifest drift gate must never be a prerequisite of the test
    matrix.

    It used to be a step inside `changes`, and every other job has
    `needs: changes` — so a one-line manifest drift made GitHub mark the whole
    matrix "skipped" (not failed: never run). Twice in two days (#2868 parity
    files, #2911 price-basis) that hid every test signal behind a bookkeeping
    miss, and the second time it blocked verification of the #2904 fork fix.

    The gate must still BLOCK a merge (python-ci-gate, the required aggregate,
    lists it) — it just must not withhold the tests. This pins the split, and
    pins it against the ways this workflow could silently undo it: its own
    house idioms (`continue-on-error`, `|| true` / `; exit 0`, `if:`) all turn a
    FAILED job into a GREEN required check, which would restore the bug's effect
    without restoring the bug.
    """
    workflow = _load_python_ci()
    jobs = workflow["jobs"]
    _always = ("always()", "${{ always() }}")

    def _needs(name: str) -> list[str]:
        needed = jobs[name].get("needs") or []
        return [needed] if isinstance(needed, str) else list(needed)

    def _code(step: dict) -> str:
        # Comments are not invocations: this change leaves a pointer comment
        # naming the gate, and moving it inside a step must not false-red.
        return "\n".join(line for line in (step.get("run") or "").splitlines()
                         if not line.strip().startswith("#"))

    def runs_integrity(job: str) -> bool:
        # Tool name AND flag, on non-comment lines: an import-form re-nest
        # (`python3 -c "...ci_selection...integrity(m)"`) counts, and a literal
        # `--integrity` re-nest cannot slip past.
        return any("integrity" in _code(s) and "ci_selection" in _code(s)
                   for s in jobs[job].get("steps", []))

    def _defaults_shell(spec: dict) -> str | None:
        return ((spec.get("defaults") or {}).get("run") or {}).get("shell")

    # --- 1. the gate is not inside `changes` ------------------------------
    assert not runs_integrity("changes"), (
        "the drift gate must not run inside `changes`: every other job needs "
        "`changes`, so a drift there SKIPS the whole matrix instead of "
        "failing it (#2656)")

    # --- 2. it is its own unconditional, bounded, unsilenceable job -------
    assert "manifest-integrity" in jobs, \
        "the drift gate must live in its own job (#2656)"
    drift = jobs["manifest-integrity"]
    assert not _needs("manifest-integrity"), (
        "the drift job must have no upstream needs, or an unrelated failure "
        "would skip the check itself")
    assert runs_integrity("manifest-integrity"), \
        "the drift job must actually run the integrity check"
    assert drift.get("if", "always()") in _always, (
        "the drift job must be unconditional (push/PR) — an `if:` "
        "would silently drop drift enforcement on the events it excludes")
    assert not drift.get("continue-on-error"), (
        "the drift job must not be continue-on-error: a failed check would "
        "report success and the required aggregate would go green (#2656)")
    assert not _defaults_shell(workflow) and not _defaults_shell(drift), (
        "a `defaults.run.shell` can swallow the integrity exit code (#2656)")

    # BOTH checks in this job are fail-closed gates and BOTH feed the required
    # aggregate, so BOTH are held to the same unsilenceability rule. The
    # `merge=union` registry validator (#5373) is a SEPARATE step from the drift
    # gate, but a `|| true` / `continue-on-error` / `shell:` override on it is the
    # SAME defect class this test exists to catch: the validator would report
    # success while a duplicate-key union merge passed — the fail-open shape the
    # validator exists to remove, wearing the fix. Matching on "integrity" alone
    # swept both steps into one token check and false-redded; parameterising by
    # each step's OWN exact invocation keeps every contract exact.
    drift_gate_steps = [s for s in drift.get("steps", [])
                        if "ci_selection" in _code(s)]
    assert drift_gate_steps, "the drift job must run the integrity check"
    registry_validator_steps = [s for s in drift.get("steps", [])
                                if "tools/registry_integrity.py" in _code(s)]
    assert registry_validator_steps, (
        "the drift job must also run the union registry validator (#5373): the "
        "unioned registries are only safe while a validator fails closed on them")
    # Each step is paired with the EXACT non-comment invocation it must make.
    checked_gate_steps = [
        *((s, "python3 tools/ci_selection.py --integrity") for s in drift_gate_steps),
        *((s, "python3 tools/registry_integrity.py") for s in registry_validator_steps),
    ]
    for step, expected in checked_gate_steps:
        run = step["run"].replace("\\\n", " ")
        # The exact string, not a `tokens[:2]` prefix and not a membership test:
        # a trailing `|| true`, a `; exit 0`, or any other shell tail is the
        # silencing idiom this pins, and it would satisfy a looser check.
        assert run.strip() == expected, (
            f"a gate step must invoke EXACTLY `{expected}` — no shell operator "
            "and no trailing anything, or a real failure reports green "
            f"(#2656/#5373); got {step.get('run')!r}")
        assert not step.get("continue-on-error") and not step.get("shell"), (
            "a gate STEP must neither be continue-on-error nor override "
            "`shell`: either one lets the job report success while the check "
            "failed (#2656/#5373)")
        assert step.get("if", "always()") in _always, (
            "a gate step must be unconditional: `if: always()` is fine, any "
            "other condition drops enforcement on the events it excludes")
    _timeout = drift.get("timeout-minutes")
    assert isinstance(_timeout, int) and 0 < _timeout <= 15, (
        f"the drift job needs a tight timeout (got {_timeout!r}) — it installs "
        "pyyaml and feeds the required aggregate (#2656)")

    # --- 3. nothing that runs tests may depend on it ----------------------
    # Not just its DIRECT dependents: a helper job that a matrix job needs
    # would re-create the skip transitively (drift fails -> helper skipped ->
    # matrix job skipped).
    assert "manifest-integrity" in (jobs["python-ci-gate"].get("needs") or []), (
        "python-ci-gate is the required status check — it must include the "
        "drift job, or a drift would stop blocking merges (#2656)")
    # What makes a dependent harmful is that it lies on the path to a job that
    # runs tests: skip it and the tests do not run. A job that merely reports
    # the drift result (and that no test job needs) is fine. So intersect the
    # drift job's transitive dependents with the transitive needs-closure of
    # the matrix jobs — which also catches the indirect form (drift job ->
    # helper -> a matrix job), where a direct-only check sees nothing.
    matrix_jobs = set(jobs["python-ci-gate"].get("needs") or []) \
        - {"manifest-integrity"}
    on_the_path, frontier = set(matrix_jobs), list(matrix_jobs)
    while frontier:
        for parent in _needs(frontier.pop()):
            if parent not in on_the_path:
                on_the_path.add(parent)
                frontier.append(parent)
    skipped, frontier = {"manifest-integrity"}, ["manifest-integrity"]
    while frontier:
        current = frontier.pop()
        for name in jobs:
            if current in _needs(name) and name not in skipped:
                skipped.add(name)
                frontier.append(name)
    polluted = sorted((skipped & on_the_path) - {"manifest-integrity"})
    assert not polluted, (
        "no job needed to reach the test matrix may depend on the drift gate, "
        "directly or through a helper: a drift would SKIP it instead of "
        f"failing it (#2656); got {polluted}")

    # --- 4. the aggregate still FAILS, and never swallows it --------------
    gate = jobs["python-ci-gate"]
    assert gate.get("if") in _always, (
        "python-ci-gate must run even when the drift job fails — `if: always()`"
        " is what lets it report the failure (#2656)")
    assert not gate.get("continue-on-error"), (
        "the required aggregate must not be continue-on-error (#2656)")
    gate_steps = gate.get("steps", [])
    assert all(not s.get("continue-on-error") and not s.get("shell")
               for s in gate_steps), (
        "no gate step may be continue-on-error or override `shell` — the "
        "required check would report green on a red drift (#2656)")
    aggregate = next((s for s in gate_steps
                      if "join(needs.*.result" in (s.get("run") or "")), None)
    assert aggregate is not None, (
        "python-ci-gate must aggregate EVERY need's result — narrowing it to "
        "`needs.changes.result` (or `outcome`) silently drops drift "
        "enforcement (#2656)")
    script = aggregate["run"]
    assert "${{ join(needs.*.result, ' ') }}" in script, (
        "python-ci-gate must join `needs.*.result` — reading a subset means a "
        "red drift never reaches the check (#2656)")
    # Defence in depth must not be deletable either: the per-leg rows below cover
    # every declared leg, but the plain sweep is what catches a leg that reaches
    # `needs:` WITHOUT a row (a shape the row-set assertion below forbids, so
    # this is redundancy — deliberately kept and pinned rather than dropped).
    #
    # Pin CODE, not prose (#9240). `script` is the raw `run:` scalar INCLUDING
    # comment lines, and the aggregate's own rationale comment quotes
    # `grep -qE 'failure|cancelled'` verbatim — so a raw-string pin stays green
    # after the functional command is deleted. That is the same
    # comment-satisfiable class the dead-code ordering hid behind for months.
    code = "\n".join(line for line in script.splitlines()
                     if not line.strip().startswith("#"))
    assert re.search(r"grep -qE 'failure\|cancelled'", code), (
        "the aggregate must keep the `failure|cancelled` sweep over "
        "`needs.*.result` as defence in depth (#5219) — as a LINE OF CODE, not "
        "as a mention inside a comment")

    # Render the GitHub expressions into literal results and actually RUN the
    # aggregate's script: this is what turns "the words are present" into "a
    # failed drift really exits non-zero". (Guarded: the assertion is about the
    # shell logic, which is the thing that has to be right on the runner.)
    if shutil.which("bash"):
        job_names = list(jobs["python-ci-gate"].get("needs") or [])

        # Derive each leg's selector from the WORKFLOW itself, not from a literal
        # list here: a leg whose job-level `if:` reads a selector output may
        # legitimately skip when that output is false; a leg with no diff gate
        # (its `if:` names no selector output) must therefore ALWAYS be SUCCESS.
        # Deriving it makes the rows and the jobs' own `if:`s unable to drift.
        def _selector_of(leg: str) -> str:
            outs = re.findall(r"needs\.changes\.outputs\.(\w+)",
                              str(jobs[leg].get("if") or ""))
            return outs[0] if outs else "-"

        SELECTOR = {leg: _selector_of(leg) for leg in job_names}
        DECLINABLE = {leg: sel for leg, sel in SELECTOR.items() if sel != "-"}
        ALWAYS = [leg for leg, sel in SELECTOR.items() if sel == "-"]
        # Default is "selected": an unexpected `skipped` is then a LOST shard,
        # which is the polarity #5219 is about.
        SELECTED = {out: "true" for out in DECLINABLE.values()}

        # Every leg in `needs:` must have exactly ONE row, and that row's
        # selector must be the output the job's own `if:` reads. Without this a
        # shard added to `needs:` with no row — or with the wrong selector —
        # would be certified green while `skipped` (the `lost-shard` shape).
        rows: dict[str, str] = {}
        for line in script.splitlines():
            m = re.match(
                r"^([\w-]+)\|\$\{\{\s*needs\.[\w-]+\.result\s*\}\}\|(.*)$",
                line.strip())
            if m:
                rows[m.group(1)] = m.group(2).strip()
        assert set(rows) == set(job_names), (
            "every leg in `python-ci-gate.needs` must have exactly one row in "
            "the aggregate's per-leg check; "
            f"rows={sorted(rows)} vs needs={sorted(job_names)}")
        for leg, selector in SELECTOR.items():
            if selector == "-":
                assert rows[leg] == "-", (
                    f"`{leg}` has no diff gate, so its row must use `-` (must "
                    f"always be SUCCESS); got {rows[leg]!r}")
            else:
                found = re.search(r"outputs\.(\w+)", rows[leg])
                assert found and found.group(1) == selector, (
                    f"`{leg}`'s row must gate on `{selector}` — the output its "
                    f"own `if:` reads; got {rows[leg]!r}")

        def _render(results: dict, selected: dict, extra=(), src=None) -> str:
            """Render the aggregate's GitHub expressions into literal values.

            `extra` appends result words to the `needs.*.result` join WITHOUT
            adding rows — the only way to reproduce the shape that reaches
            `needs:` with no row in the table (a need the aggregate cannot
            name). `src` renders a modified copy of the script (e.g. with a
            duplicated row) instead of the real one.
            """
            rendered = (src if src is not None else script).replace(
                "${{ join(needs.*.result, ' ') }}",
                " ".join([results.get(name, "skipped") for name in job_names]
                         + list(extra)))
            # `needs.<job>.result` renders as EMPTY when <job> is absent from
            # `needs:` — GitHub's own semantics, and the reason a leg dropped
            # from the required check's list must fail closed here rather than
            # quietly vanish.
            rendered = re.sub(r"\$\{\{\s*needs\.([\w-]+)\.result\s*\}\}",
                              lambda m: results.get(m.group(1), ""), rendered)
            rendered = re.sub(
                r"\$\{\{\s*needs\.changes\.outputs\.(\w+)\s*\}\}",
                lambda m: selected.get(m.group(1), ""), rendered)
            unrendered = re.findall(r"\$\{\{[^}]*\}\}", rendered)
            assert not unrendered, (
                "this harness must render every GitHub expression the aggregate "
                f"uses, or it proves nothing; unrendered: {unrendered}")
            return rendered

        def _verdict(results: dict, selected: dict | None = None,
                     extra=(), src=None) -> tuple[int, str]:
            """Render and RUN the aggregate. Returns `(exit code, output)`.

            The OUTPUT is half the contract: a red that does not NAME the leg
            sends the operator to the job logs, which is exactly the defect
            #9240 fixes. Asserting only the exit code cannot tell that fix from
            the code it replaced — the old bare `grep -qE 'failure|cancelled'`
            bail exits 1 on every red vector below, so exit-code-only coverage
            is satisfied by the pre-fix script (proved in #9240 review).

            The SHELL is `bash -eo pipefail`, deliberately the stricter of the
            two GitHub uses: this step declares no `shell:`, so the runner uses
            its Linux default `bash -e {0}` (the run log's `shell:` line says
            exactly that), while any step declaring `shell: bash` gets
            `bash --noprofile --norc -eo pipefail {0}`. Why it matters HERE:
            the aggregate's LAST-RESORT #5219 backstop is
            `echo "$RESULTS" | grep -qE 'failure|cancelled'` — a `grep -q`
            mid-pipeline, the shape this repo has recorded silently INVERTING a
            branch (the writer takes SIGPIPE, `pipefail` then reports the
            pipeline as failed while the matched line IS present —
            `docs/ci/docs-lint-baseline.md`,
            `tests/test_migration_drift_gate.py`). A verdict that only survives
            `-e` is one this harness must red, because #9240 makes that sweep
            the last reachable layer (#9240 review). Errexit is covered too:
            both shells carry `-e`.
            """
            sel = dict(SELECTED)
            sel.update(selected or {})
            proc = subprocess.run(
                ["bash", "-eo", "pipefail", "-c",
                 _render(results, sel, extra, src)],
                capture_output=True, text=True)
            return proc.returncode, proc.stdout + proc.stderr

        # The bare sentence the pre-fix aggregate printed for every red. Nothing
        # may emit it again: it names no leg, which is the whole defect #9240
        # fixes (agent-infra#2148 sibling).
        LEGACY = "matrix leg failed or was cancelled"

        green = {name: "success" for name in job_names}
        rc, out = _verdict(green)
        assert rc == 0, "an all-green matrix must pass the required check"
        assert "::error::" not in out, (
            f"a green matrix must not emit an error annotation; got {out!r}")

        # EVERY leg, not just the last one: a required check that cannot red on
        # a given leg is a check that does not observe it (#5219). And every red
        # must NAME its leg — that is #9240's deliverable, and the reason these
        # assertions inspect `out` rather than only the exit code.
        #
        # ⛔ SPLIT BY CLASS SINCE #9984, and the split is the contract, not a
        # concession. `cancelled` (below) still reds on the aggregate's own authority:
        # a leg that never ran produced NO failure set, so there is nothing to compare
        # against main and nothing that could make it pre-existing. `failure` does reach
        # the introduced-failure predicate, which asks the RAIL's question about the
        # same tree — so its exit code now comes from the predicate, and the assertion
        # that matters is the FAIL-CLOSED one: with no measurable baseline (the
        # harness's env renders neither `PR_HEAD` nor `EVENT_NAME`) the predicate
        # refuses and the check still exits 1. Deleting the delegation would make this
        # vector exit 0 — which is exactly what the assertion catches.
        for red in ("cancelled",):
            for leg in job_names:
                bad = dict(green)
                bad[leg] = red
                rc, out = _verdict(bad)
                assert rc == 1, (
                    f"a `{red}` for `{leg}` must FAIL python-ci-gate — a leg "
                    "the required check does not observe is a leg it cannot "
                    "block (#5219)")
                assert f"::error::leg '{leg}'" in out, (
                    f"a `{red}` for `{leg}` must be NAMED — an aggregate that "
                    "only says 'merge blocked' sends the operator to the job "
                    f"logs (#9240, agent-infra#2148); got {out!r}")
                assert LEGACY not in out, (
                    "the pre-fix bare sentence names no leg — it must never be "
                    f"the report for a red again (#9240); got {out!r}")
                # The summary line counts LEGS and must not pad its number with
                # `wc`'s spaces (the one operator-facing line in this change).
                assert "❌ 1 leg(s) did not pass" in out, (
                    f"the summary must count exactly one red leg, unpadded; got "
                    f"{out!r}")

        # ── THE INTRODUCED-FAILURE PREDICATE (#9984) ──────────────────────
        # A failed SHARD is now delegated to `ci-introduced-failure` (a separate job,
        # see the workflow): the aggregate asks whether that verdict job SUCCEEDED.
        # Everything else about a red leg is unchanged — it is still named, and the
        # check still cannot go green on a red tree without that verdict.
        PREDICATE = "ci-introduced-failure"
        assert PREDICATE in job_names, (
            "the aggregate must consume the introduced-failure verdict as a need — "
            "otherwise a failed shard has no adjudicator and #9984's predicate is "
            "either missing or asked by nothing")

        #: The arms a failed TEST can be excused on. Declared in the aggregate's own
        #: script (`DEFERRABLE_LEGS`) and pinned against the verdict job's `needs:` by
        #: tests/test_ci_new_failure_gate.py, so this harness reads it from the script
        #: instead of restating it.
        deferrable = re.search(r'^\s*DEFERRABLE_LEGS="([^"]+)"', script, re.M)
        assert deferrable, (
            "the aggregate must declare `DEFERRABLE_LEGS` — the arms a failed test can "
            "be excused on (#9984); without it every failed leg is excusable or none is")
        DEFERRABLE = set(deferrable.group(1).split())
        for leg in job_names:
            if leg not in DEFERRABLE:
                continue
            bad = dict(green)
            bad[leg] = "failure"
            rc, out = _verdict(bad)
            assert rc == 0, (
                f"a `failure` for `{leg}` that `{PREDICATE}` certified must PASS the "
                "required check — an exit 1 here means the delegation was deleted and "
                f"the old \"does anything fail?\" predicate is back (#9984); got {out!r}")
            assert "certified every failing id" in out and leg in out, (
                f"the excused leg must be NAMED, or the operator cannot tell why a red "
                f"shard merged (#9240/#9984); got {out!r}")

        # (b) the SAME red shard with the verdict job RED: the aggregate fails, and the
        #     verdict job is the leg it names. This is the case the old predicate got
        #     right, and it must stay right — a real PR-caused failure still blocks.
        for leg in job_names:
            if leg not in DEFERRABLE:
                continue
            bad = dict(green)
            bad[leg] = "failure"
            bad[PREDICATE] = "failure"
            rc, out = _verdict(bad)
            assert rc == 1, (
                f"a `failure` for `{leg}` that `{PREDICATE}` did NOT certify must FAIL "
                f"the required check; got {out!r}")
            assert f"::error::leg '{PREDICATE}' reported 'failure'" in out, (
                f"the verdict job must be NAMED as the refusing leg; got {out!r}")

        # (b2) `timed_out` is DELEGATED exactly like `failure` (#9984 review). The
        #      delegating case is `failure|timed_out`, and the two halves must agree:
        #      the aggregate delegates a timed-out shard, AND the verdict job's own
        #      step runs for it (pinned structurally by
        #      tests/test_ci_new_failure_gate.py::test_the_verdict_step_is_gated_on_
        #      every_class_the_aggregate_delegates — a `success` from a verdict job
        #      whose step never ran is not a certification).
        for leg in job_names:
            if leg not in DEFERRABLE:
                continue
            timed = dict(green)
            timed[leg] = "timed_out"
            rc, out = _verdict(timed)
            assert rc == 0, (
                f"a `timed_out` for `{leg}` must be delegated exactly like `failure` — "
                "an exit 1 here means the delegating case lost its `timed_out` half; "
                f"got {out!r}")
            assert "certified every failing id" in out and leg in out, out
        #      ... and, like `failure`, it is NEVER delegable on a non-test arm.
        for leg in sorted(set(job_names) - DEFERRABLE - {PREDICATE}):
            timed = dict(green)
            timed[leg] = "timed_out"
            rc, out = _verdict(timed)
            assert rc == 1, (
                f"a `timed_out` for `{leg}` must never be excused by the test-failure "
                f"verdict — it is not a test arm (#9984); got {out!r}")
            assert "not a test arm" in out, out

        # (c) the verdict job is the ONE failure that can never be deferred: deferring
        #     it would make the verdict ask itself (a red verdict, excused by itself).
        bad = dict(green)
        bad[PREDICATE] = "failure"
        rc, out = _verdict(bad)
        assert rc == 1, (
            "a red `ci-introduced-failure` must red the required check even when no "
            f"other leg failed — it is the refusal itself; got {out!r}")
        assert f"::error::leg '{PREDICATE}' reported 'failure'" in out, out
        assert "certified every failing id" not in out, (
            f"a red verdict must never be reported as a certification; got {out!r}")

        # (c2) a NON-test arm is never delegable: `surface-guard` red is a surface
        #      expansion, not a trunk red, and a verdict about test ids says nothing
        #      about it. (This is also what keeps a drift-gate failure a hard block.)
        assert set(job_names) - DEFERRABLE, "there must be a non-deferrable arm to assert on"
        for leg in sorted(set(job_names) - DEFERRABLE - {PREDICATE}):
            bad = dict(green)
            bad[leg] = "failure"
            rc, out = _verdict(bad)
            assert rc == 1, (
                f"a `{leg}` failure must NEVER be excused by the test-failure verdict — "
                f"it is not a test arm (#9984); got {out!r}")
            assert f"::error::leg '{leg}'" in out and "not a test arm" in out, out

        # (d) and it must be fail-closed about its own absence: a verdict job that was
        #     CANCELLED or SKIPPED certifies nothing, so a red shard cannot be excused.
        for verdict_result in ("cancelled", "skipped"):
            bad = dict(green)
            bad["test"] = "failure"
            bad[PREDICATE] = verdict_result
            rc, out = _verdict(bad)
            assert rc == 1, (
                f"a `{verdict_result}` `{PREDICATE}` must not excuse a red shard — an "
                f"absent verdict is not a clean one (#9984 constraint 1); got {out!r}")
            assert f"::error::leg '{PREDICATE}'" in out, out

        # An always-run leg has NO selector that can decline it, so a `skipped`
        # one is a lost shard. This is the half of "`skipped` is not a pass"
        # that covers the legs the gate cannot see skip: a silently skipped
        # `changes`/`manifest-integrity`/`surface-guard` must red the check.
        assert ALWAYS, "the aggregate must have always-run legs to assert on"
        for leg in ALWAYS:
            lost = dict(green)
            lost[leg] = "skipped"
            rc, out = _verdict(lost)
            assert rc == 1, (
                f"`{leg}` has no diff gate, so a `skipped` {leg} is a lost "
                "shard and must red the required check")
            assert f"::error::leg '{leg}'" in out and "SKIPPED" in out, (
                f"a `skipped` {leg} must be named as SKIPPED; got {out!r}")
        # A leg the selector DID select reporting `skipped` is a lost shard...
        for leg, selector in DECLINABLE.items():
            lost = dict(green)
            lost[leg] = "skipped"
            rc, out = _verdict(lost)
            assert rc == 1, (
                f"`{leg}` skipped although the selector SELECTED it is a lost "
                "shard — the required check must not certify it (#5219)")
            assert f"::error::leg '{leg}'" in out, (
                f"a lost shard must be named, not just counted; got {out!r}")
            # ... while one the selector DECLINED may skip: a diff that does not
            # touch the leg must not be blocked by the gate's own shape.
            declined = dict(green)
            declined[leg] = "skipped"
            rc, out = _verdict(declined, {selector: "false"})
            assert rc == 0, (
                f"`{leg}` skipped because `{selector}` declined it must not "
                f"red the required check; got {out!r}")
        # A docs-only diff selects nothing at all.
        declined_all = dict(green)
        for leg in DECLINABLE:
            declined_all[leg] = "skipped"
        rc, out = _verdict(declined_all,
                           {sel: "false" for sel in DECLINABLE.values()})
        assert rc == 0, (
            f"an all-declined (docs-only) diff must stay green; got {out!r}")
        # And the shape #5219 was actually about: a leg dropped from `needs:`
        # must fail closed instead of vanishing — for EVERY leg, not just one.
        # Its row renders EMPTY, so it is reported as the missing-leg shape BY
        # NAME, which is why this class was already named pre-fix.
        for leg in job_names:
            dropped = dict(green)
            dropped.pop(leg)
            rc, out = _verdict(dropped)
            assert rc == 1, (
                f"`{leg}` REMOVED from `needs:` must fail closed, not "
                "disappear — that is the exact #5219 shape")
            assert f"::error::leg '{leg}'" in out, (
                f"a missing `{leg}` must be named — a bare count mismatch "
                f"sends the operator hunting; got {out!r}")

        # Two red legs must yield TWO named lines, not the first one only (an
        # `exit` inside the loop would silently report just the first).
        two = dict(green)
        two["test"] = "failure"
        two["test-slow"] = "cancelled"
        rc, out = _verdict(two)
        assert rc == 1
        assert "test: reported 'failure'" in out, out
        assert "::error::leg 'test-slow' was CANCELLED" in out, out
        assert out.count("::error::leg ") == 1, (
            "each HARD-red leg gets its own error line, and a delegated one gets a "
            f"plain line; got {out!r}")
        # `test` is DELEGATED (#9984) and `test-slow` is a lost shard: the aggregate
        # names both and refuses on its own authority, because a cancelled leg
        # certifies nothing and no verdict could excuse it. The delegation is a
        # plain line, not an `::error::` — an error annotation would claim a refusal
        # the gate is not making.
        assert "❌ 1 leg(s) did not pass" in out, out
        assert "::error::leg 'test-slow'" in out, out
        assert "test: reported 'failure'" in out and "delegated" in out, (
            f"a delegated failure must say so plainly; got {out!r}")

        # A duplicate row inspected twice is ONE red leg and must be counted
        # once — a copy-paste must not inflate the operator's summary.
        order = ("test|${{ needs.test.result }}|"
                 "${{ needs.changes.outputs.python }}")
        row_line = next((ln for ln in script.splitlines()
                         if ln.strip() == order), None)
        assert row_line is not None, (
            "could not find the `test` row to duplicate — the fixture is stale")
        dup_src = script.replace(row_line, f"{row_line}\n{row_line}", 1)
        one_red = dict(green)
        # `cancelled`, not `failure`: a delegated failure is excused by a green
        # verdict (#9984), so a HARD-red class is what proves the count de-duplicates.
        one_red["test"] = "cancelled"
        rc, out = _verdict(one_red, src=dup_src)
        assert rc == 1, (
            "a duplicated row must not hide its own red; got "
            f"rc={rc} {out!r}")
        assert "❌ 1 leg(s) did not pass" in out, (
            "the summary must count LEGS, not rows — a duplicated row inspected "
            f"twice is ONE red leg; got {out!r}")

        # The `failure|cancelled` sweep must stay REACHABLE (#9240 review: it
        # was dead code behind the deleted distinct-count guard). A red result
        # with no row at all is only visible to that sweep — assert it fires and
        # says so, instead of being pre-empted by an earlier, blinder bail.
        rc, out = _verdict(green, extra=("failure",))
        assert rc == 1, (
            "a failing need with no row must red the required check — a "
            f"`skipped`-shaped escape here is a fail-OPEN; got {out!r}")
        assert "no leg row named it" in out, (
            "the unrowed-need red must come from the identity-blind sweep, i.e. "
            f"the sweep must be reachable; got {out!r}")

        # That sweep's `cancelled` half must be reachable even while ANOTHER leg is
        # delegated (#9984 review). Gating the whole sweep on `RED_LEGS` being empty
        # disabled the `cancelled` half too — and `cancelled` can never be delegated
        # (the leg loop reds on it by name), so all the gate did was let a leg that
        # never ran pass the required check once a deferrable leg was adjudicated.
        delegated_plus_lost = dict(green)
        delegated_plus_lost["test"] = "failure"
        rc, out = _verdict(delegated_plus_lost, extra=("cancelled",))
        assert rc == 1, (
            "a `cancelled` need with no leg row must red the required check even while "
            f"a delegable `failure` is being adjudicated; got {out!r}")
        assert "no leg row named it" in out, out

        # ... and so must its `failure` half (#9984 review). The gate on that half is a
        # COUNT (`RESULT_FAILS > ROW_FAILS`), not `RED_LEGS` being empty: emptiness asked
        # the wrong question, because it holds whenever ANY leg was delegated — so an
        # un-rowed `failure` need hid behind an unrelated delegated leg and the check
        # printed "did not introduce it" over a leg the row table never looked at
        # (measured pre-fix: rc=0). `extra=("failure",)` is a need that reached `needs:`
        # with no row; `test=failure` is the delegable leg that used to mask it.
        rc, out = _verdict(delegated_plus_lost, extra=("failure",))
        assert rc == 1, (
            "an un-rowed `failure` need must red the required check even while ANOTHER "
            f"leg is delegated — otherwise the last-resort sweep is blinded in its own "
            f"case; got {out!r}")
        assert "no leg row named it" in out, out


def test_required_gate_covers_the_long_legs():
    """#5219: the required check must OBSERVE every shard it certifies.

    `python-ci-gate` is a REQUIRED status check AND the only `merge_condition`
    of the Mergify merge queue, so its `needs:` list IS its whole claim: a leg
    absent from it is a leg the gate cannot block, however red the run is.

    From 2026-09-24T15:25Z (#5017) until #5219 the list omitted `test`,
    `test-slow` and `test-carve-out`, and the hole was not theoretical: on the
    MERGED heads of #4838 (30c5b45) and #4633 (81401cc), `test (a)`, `test (b)`
    and `test-carve-out` were all `completed/failure` while `python-ci-gate`
    was `completed/success`. Across PRs at the time, the only thing separating
    a gate that reddened on a failing `test` from one that passed was WHICH
    VERSION of the workflow the PR head carried — heads with the pre-#5017 list
    failed the gate, heads with the post-#5017 list passed it.

    #5017 removed those legs DELIBERATELY to cut merge-path latency (`test (a)`
    measured 30.0–31.5m against a ~20m main-merge cadence, so heads went BEHIND
    before the merge could land — 0/41 PRs ever CLEAN). #5219 reverses that
    trade on the owner's call: the integrity of the required check over
    merge-path latency. This test pins the reversal so the shards cannot be
    quietly dropped again — and it pins the DIRECT edge, not only the closure:
    a transitive path is the `canary-streak` → `test` shape that satisfies a
    closure-only assertion while nullifying the intent.
    """
    workflow = _load_python_ci()
    jobs = workflow["jobs"]

    def _needs(name: str) -> list[str]:
        n = jobs[name].get("needs") or []
        return [n] if isinstance(n, str) else list(n)

    direct = list(_needs("python-ci-gate"))
    closure, frontier = set(direct), list(direct)
    while frontier:
        for parent in _needs(frontier.pop()):
            if parent not in closure:
                closure.add(parent)
                frontier.append(parent)

    for leg in ("test", "test-slow", "test-carve-out"):
        assert leg in jobs, (
            f"{leg} must exist — the required check must aggregate it, not "
            "replace it")
        assert leg in direct, (
            f"{leg} must be a DIRECT need of `python-ci-gate`. The required "
            "check — and the merge queue, whose only `merge_condition` it is — "
            "must observe the shard it certifies; a gate that goes green while "
            "this leg is red is the #5219 defect")
        assert leg in closure
    assert "manifest-integrity" in closure, (
        "the required aggregate must still include the manifest drift gate, "
        "or a drift stops blocking merges (#2656)")

    # The OTHER half of the dropped-shard defence (#5219). The drift test pins
    # rows -> needs; this pins needs -> the workflow's own universe of PR-lane
    # jobs. Without it, deleting a leg from `needs:` AND its row is a
    # self-consistent edit that passes every test while the shard sits outside
    # the required check — and that pair is the NATURAL edit, because dropping
    # the entry alone leaves an orphan row, which IS caught, so the author
    # deletes both.
    #
    # A push-only job is exempt by DERIVATION, not by name: on a pull request it
    # reports only `skipped`, so it carries no PR-lane signal.
    push_only = {name for name, spec in jobs.items()
                 if "github.event_name" in str(spec.get("if") or "")}
    must_aggregate = set(jobs) - set(direct) - {"python-ci-gate"} - push_only
    assert not must_aggregate, (
        "every job in this workflow that reports on the PR lane must be a "
        "`python-ci-gate` need — otherwise its failure cannot block a merge, "
        f"which is the #5219 defect. Missing: {sorted(must_aggregate)}")

    # `leg in jobs` alone only proves the leg is DEFINED. The contract leans on
    # the legs still EXECUTING on the PR lane — the `--admin` rail's lane parity
    # requires the PR lane to have run every shard main's lane runs — so pin
    # that too: both triggers must remain, and no leg may be silenced.
    triggers = workflow.get("on", workflow.get(True)) or {}
    assert "push" in triggers and "pull_request" in triggers, (
        "the long legs must still run on push (post-merge detection on main) "
        "AND on pull requests (advisory pre-merge) — dropping either trigger "
        "silently deletes a leg the disclosure depends on")
    for leg in ("test", "test-slow", "test-carve-out"):
        spec = jobs[leg]
        assert spec.get("steps"), (
            f"{leg} must still have steps — an empty job would 'run' nothing")
        assert not spec.get("continue-on-error"), (
            f"{leg} must not be continue-on-error — its failure must stay "
            "visible, or the post-merge detection is silent")
        assert spec.get("if") not in ("false", False), (
            f"{leg} must not be unconditionally disabled")
        # NOT push-only: the workflow's own comment cites the `--admin` rail's
        # lane parity as the reason these legs are not skipped on PRs, so a
        # per-leg `github.event_name` filter is the exact regression to refuse.
        # And job-level `continue-on-error` is not enough — a silenced STEP
        # inside the job produces the same missing signal.
        assert "github.event_name" not in str(spec.get("if") or ""), (
            f"{leg} must not carry an event filter (e.g. push-only): a "
            "`skipped` shard is not coverage, and the `--admin` rail's lane "
            "parity (`ADMIN_MERGE_LANE_PARITY=require`) refuses a merge when "
            "the PR lane did not execute a shard main's lane executes "
            "(#4263/#4457)")
        pytest_steps = [s for s in spec.get("steps", [])
                        if "pytest" in (s.get("run") or "")]
        assert pytest_steps, f"{leg} must still RUN pytest"
        silenced = [s.get("name") for s in pytest_steps
                    if s.get("continue-on-error")]
        assert not silenced, (
            f"{leg}'s pytest step(s) must not be continue-on-error — that "
            "silences the signal the disclosure says is still produced: "
            f"{silenced}")


# ── #2938: surface audit (report-only) ───────────────────────────────────
# The audit must resolve what the rejected mechanical derivation could not:
# package-level imports, `tortoise/api.py` (absent from SOURCE_PATTERNS),
# helper/fixture indirection, string path refs — and it must stay a REPORT
# (never mutate the manifest, never change select()/--integrity).

_AUDIT_SOURCES = {
    "tortoise/hosted_api.py": "",
    "tortoise/api.py": "",
    "tortoise/ranking.py": "",
    "tortoise/sdk.py": "",
    "tortoise/ep.py": "",
    "tortoise/exceptions.py": "",
    "battery/cli.py": "",
}


def _audit_repo(tmp_path: Path, extra: dict[str, str]) -> Path:
    """A synthetic repo: the shared source tree plus `extra` files."""
    for rel, body in {**_AUDIT_SOURCES, **extra}.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    return tmp_path


def _audit(manifest: dict, repo: Path) -> dict:
    return surface_audit(manifest, repo=repo, tests_dir=repo / "tests")


def _audit_manifest(**surfaces: list[str]) -> dict:
    """A manifest carrying every surface the real one has (empty unless
    overridden) so assertions can address any surface."""
    base: dict[str, list[str]] = {
        s: [] for s in ("api", "battery", "classify", "core", "ep",
                        "eval", "onboarding", "sdk")}
    base.update(surfaces)
    return {"surfaces": base, "tier1": []}


def test_surface_audit_reports_unregistered_surface_pin_as_addition(tmp_path):
    # requirement: a file importing a surface's source but not registered
    # under it is an addition candidate, with the import as evidence
    repo = _audit_repo(tmp_path, {
        "tests/test_imports_api.py": "from tortoise.hosted_api import app\n"})
    report = _audit(_audit_manifest(battery=["test_imports_api.py"]), repo)
    api = {e["file"]: e for e in report["surfaces"]["api"]["addition"]}
    assert "test_imports_api.py" in api
    assert api["test_imports_api.py"]["evidence"] == ["tortoise/hosted_api.py"]
    # ... and the same file is a battery removal candidate (pins nothing battery)
    assert [e["file"] for e in report["surfaces"]["battery"]["removal"]] \
        == ["test_imports_api.py"]


def test_surface_audit_reports_member_pinning_nothing_as_removal(tmp_path):
    repo = _audit_repo(tmp_path, {
        "tests/test_stray_api.py": "from battery.cli import main\n"})
    report = _audit(_audit_manifest(api=["test_stray_api.py"]), repo)
    removal = report["surfaces"]["api"]["removal"]
    assert [e["file"] for e in removal] == ["test_stray_api.py"]
    assert removal[0]["pins"] == {"battery": ["battery/cli.py"]}
    # the reverse direction is reported too: not registered under battery
    assert [e["file"] for e in report["surfaces"]["battery"]["addition"]] \
        == ["test_stray_api.py"]


def test_surface_audit_resolves_package_level_import(tmp_path):
    # rule (a): `from tortoise import X` -> tortoise/X.py (or X/__init__.py);
    # this is the gap that made 28 onboarding files read as "pins nothing"
    repo = _audit_repo(tmp_path, {
        "tortoise/onboarding/__init__.py": "",
        "tests/test_pkg_import.py": "from tortoise import onboarding\n"})
    report = _audit(_audit_manifest(api=["test_pkg_import.py"]), repo)
    ep_add = {e["file"]: e for e in report["surfaces"]["ep"]["addition"]}
    assert "test_pkg_import.py" not in ep_add  # sanity: not the wrong surface
    additions = {e["file"]: e for e in report["surfaces"]["onboarding"]["addition"]}
    assert "test_pkg_import.py" in additions
    assert "tortoise/onboarding/__init__.py" in additions["test_pkg_import.py"]["evidence"]
    # `tortoise` alone (the bare package root) is never a pin
    assert report["surfaces"]["api"]["addition"] == []


def test_surface_audit_excludes_shared_modules(tmp_path):
    # rule: SHARED_MODULES force the full matrix, so importing one is NOT a
    # pin and must not surface as an addition/uncovered mismatch
    repo = _audit_repo(tmp_path, {
        "tests/test_shared.py":
            "from tortoise.sdk import TortoiseSDK\n"
            "from tortoise.exceptions import TortoiseError\n"})
    report = _audit(_audit_manifest(sdk=["test_shared.py"]), repo)
    for surface in report["surfaces"].values():
        assert surface["addition"] == []
    assert all(r["path"] not in ("tortoise/sdk.py", "tortoise/exceptions.py")
               for r in report["uncovered"])
    assert all(r["path"] not in ("tortoise/sdk.py", "tortoise/exceptions.py")
               for r in report["coverage_gaps"])
    removal = report["surfaces"]["sdk"]["removal"]
    assert [e["file"] for e in removal] == ["test_shared.py"]
    assert set(removal[0]["shared"]) \
        >= {"tortoise/sdk.py", "tortoise/exceptions.py"}


def test_surface_audit_catches_string_path_reference(tmp_path):
    # rule (c): a test may shell out to / read a path instead of importing it
    repo = _audit_repo(tmp_path, {
        "tests/test_string_ref.py":
            "import subprocess\n"
            "\n"
            "def test_script():\n"
            "    subprocess.run(['python', 'battery/cli.py'])\n"})
    report = _audit(_audit_manifest(core=["test_string_ref.py"]), repo)
    additions = {e["file"]: e for e in report["surfaces"]["battery"]["addition"]}
    assert "test_string_ref.py" in additions
    assert additions["test_string_ref.py"]["evidence"] == ['"battery/cli.py"']


def test_surface_audit_follows_tests_helper(tmp_path):
    # rule (d): a test reaching a surface only through a tests/ helper must
    # still count, with the chain visible in the evidence
    repo = _audit_repo(tmp_path, {
        "tests/_helper.py": "from tortoise.hosted_api import app\n",
        "tests/test_uses_helper.py": "from tests._helper import app\n"})
    report = _audit(_audit_manifest(battery=["test_uses_helper.py"]), repo)
    additions = {e["file"]: e for e in report["surfaces"]["api"]["addition"]}
    assert "test_uses_helper.py" in additions
    assert additions["test_uses_helper.py"]["evidence"] \
        == ["tortoise/hosted_api.py (via tests/_helper.py)"]
    # the helper module itself is not a test file, so it is never audited
    assert all(e["file"] != "_helper.py"
               for s in report["surfaces"].values() for e in s["addition"])


def test_surface_audit_reports_uncovered_surface_named_source(tmp_path):
    # rule (b): a surface-named source absent from that surface's
    # SOURCE_PATTERNS entry is SAID, not silently binned as core. #2938 mapped
    # the real instance (`tortoise/api.py`), so this test uses a synthetic
    # unmapped one (`tortoise/eval.py`) to keep the rule covered.
    repo = _audit_repo(tmp_path, {
        "tortoise/eval.py": "",
        "tests/test_eval_mod.py": "from tortoise.eval import X\n"})
    report = _audit(_audit_manifest(eval=["test_eval_mod.py"]), repo)
    gaps = report["coverage_gaps"]
    assert any(g["path"] == "tortoise/eval.py" and g["surface"] == "eval"
               for g in gaps), gaps
    assert "tortoise/eval.py" not in [r["path"] for r in report["uncovered"]]
    removal = report["surfaces"]["eval"]["removal"][0]
    assert removal["gap"] == ["tortoise/eval.py"]


def test_surface_audit_maps_tortoise_api_source_as_api_pin(tmp_path):
    # #2938 item 1: `tortoise/api.py` is mapped to `api` now — a file importing
    # it is an api pin, not an uncovered path and not a coverage gap.
    repo = _audit_repo(tmp_path, {
        "tests/test_imports_api.py": "from tortoise.api import EventAPI\n"})
    report = _audit(_audit_manifest(api=["test_imports_api.py"]), repo)
    assert "tortoise/api.py" not in [g["path"] for g in report["coverage_gaps"]]
    assert "tortoise/api.py" not in [r["path"] for r in report["uncovered"]]


def test_write_path_measurement_source_edit_selects_eval():
    """#7947 review P1: editing the scorer must run the tests that pin the rule.

    `score_qualifiers.py` (and `raw_vs_narrative.py`, `generate_corpus.py`,
    `schema.py`, and every `gold/*.json`) live under `tests/eval/write_path/`
    and NONE of them is named `test_*.py`, so before the `tests/eval/write_path/`
    SOURCE_PATTERNS entry a change to them matched no pattern and fell to the
    `tests/` -> `core` fallback. `test_score_qualifiers.py` and
    `test_write_path_corpus.py` are registered on `eval` ONLY, so the tests
    that pin the pre-registered decision rule (`MARKERS` / `BAND_PP` /
    `classify()` / `evaluate_branches()`) and the corpus counts §10 publishes a
    verdict on ran on NO diff-selected leg — every published number could move
    with every gate green. Registering the TEST file in
    config/ci-surfaces.yml does not fix this half: selection is driven by
    SOURCE_PATTERNS.

    `core` must stay too (CORE_ALSO): `test_extractor_v2.py` imports
    `tests.eval.write_path.runner` and `test_salience_labels_4917.py` reads
    `tests/eval/write_path/salience_labels.jsonl`, so an `eval`-only match
    would trade one silent drop for another.

    Watched RED before the entry existed (`surfaces == ['core']`, neither pin
    in `test_files`), GREEN after — mutation-real, not a tautology: deleting
    the SOURCE_PATTERNS entry reproduces the red.

    ⛔ #10194 SUPERSEDES THE `eval`-HALF ASSERTIONS (owner decision A,
    2026-10-27). The two `eval`-registered pins are under `tests/eval/**`, i.e. in
    the NON-GATING lane, so they no longer run on any PR — the very thing this
    test was written to guarantee. The SOURCE_PATTERNS mapping is unchanged and
    still asserted (a change to `score_qualifiers.py` still selects `core` +
    `eval`), and the `core` half still gates. What is asserted instead of the
    eval pins is that they are ABSENT, so a green PR is never read as "the rule
    was pinned"; the write-path measurements can therefore drift until evals
    reopen. Reachable from `eval-suite-manual.yml`.
    """
    from tools.ci_selection import on_demand_files
    lane = on_demand_files(load_manifest())
    for pin in ("eval/write_path/test_score_qualifiers.py",
                "eval/write_path/test_write_path_corpus.py"):
        assert pin in lane, (
            f"{pin} must be in the non-gating lane — its subtree is out of the "
            "merge gate (#10194)")
    cases = (
        # the rule itself — the decisive one: `evaluate_branches`/`verdict_label`
        "tests/eval/write_path/score_qualifiers.py",
        # the instrument's calibration inputs
        "tests/eval/write_path/raw_vs_narrative.py",
        # the generator whose output the pinned counts are read off
        "tests/eval/write_path/generate_corpus.py",
        # a hand-edited sealed gold — the scenario `[gold]` in MEMORY.md warns about
        "tests/eval/write_path/gold/wp01_quarry_debug.gold.json",
    )
    for changed in cases:
        r = _sel([changed])
        assert r["full"] is False
        assert r["surfaces"] == ["core", "eval"], (
            f"a write-path measurement source edit ({changed}) must select the "
            f"`eval` surface that owns its pinning tests (and keep `core`, whose "
            f"tests read the same sources); got {r['surfaces']}")
        selected = set(r["test_files"]) | set(r["slow_selected"])
        assert not (selected & lane), (
            f"a non-gating lane file reached a pre-merge leg on {changed}: "
            f"{sorted(selected & lane)[:5]}")
        assert "test_extractor_v2.py" in selected, (
            "the core-registered importer of tests.eval.write_path.runner must "
            f"still run on {changed}; selected {sorted(selected)}")
        # The eval pins no longer run on a PR (see the docstring) — assert the
        # absence so the loss is a pinned fact, not an inference from a count.
        assert "eval/write_path/test_score_qualifiers.py" not in selected
        assert "eval/write_path/test_write_path_corpus.py" not in selected


def test_ask_sealed_corpus_source_edit_selects_eval():
    """#9097: editing the sealed benchmark's sources must run its pinning tests.

    ``corpus.py`` (the hash scope, the manifest verification and the SEALED
    loaders) and ``generate_corpus.py`` (the sealed-gold renderer) live under
    ``tests/eval/ask/`` and are not named ``test_*.py``, so before the
    ``tests/eval/ask/`` SOURCE_PATTERNS entry a change to them matched no
    pattern and fell to the ``tests/`` to ``core`` fallback — where neither
    ``eval/ask/test_ask_corpus.py`` nor ``test_measure_ask_distilled.py`` is
    registered. The corpus hash scope or a gold-only-edit check could then be
    rewritten with every gate green, and the published exit-bar verdict would
    rest on an unpinned instrument.

    Watched RED before the entry existed (surfaces == ['core'], neither pin in
    test_files), GREEN after.

    ⛔ #10194 SUPERSEDES THE `eval/ask/` PIN ASSERTION (owner decision A,
    2026-10-27): `eval/ask/test_ask_corpus.py` is in the NON-GATING lane, so it
    no longer runs on a PR. The `tests/eval/ask/` SOURCE_PATTERNS mapping is
    unchanged and still asserted; the exit-bar pin
    (`test_measure_ask_distilled.py`, registered on `eval` at the top of
    `tests/`, NOT under the lane) still gates, and is asserted to. The sealed
    corpus's own contract is now pinned only by the manual eval lane.
    """
    from tools.ci_selection import on_demand_files
    lane = on_demand_files(load_manifest())
    assert "eval/ask/test_ask_corpus.py" in lane, (
        "the sealed corpus's contract test must be in the non-gating lane "
        "(#10194) — if it left the lane it is gating again")
    cases = (
        # the hash scope / manifest gate / sealed loaders
        "tests/eval/ask/corpus.py",
        # the renderer whose output the pre-registered corpus hash covers
        "tests/eval/ask/generate_corpus.py",
        # a hand-edited sealed gold — the silent re-key scenario
        "tests/eval/ask/gold/ask01_residence.gold.json",
    )
    for changed in cases:
        r = _sel([changed])
        assert r["full"] is False
        assert "eval" in r["surfaces"], (
            "a sealed-ask-corpus source edit (" + changed + ") must select the "
            "eval surface that owns its pinning tests; got "
            + str(r["surfaces"]))
        selected = set(r["test_files"]) | set(r["slow_selected"])
        assert not (selected & lane), (
            f"a non-gating lane file reached a pre-merge leg on {changed}: "
            f"{sorted(selected & lane)[:5]}")
        assert "test_measure_ask_distilled.py" in selected, (
            "the exit-bar tests must run on " + changed + "; selected "
            + str(sorted(selected)))
        assert "eval/ask/test_ask_corpus.py" not in selected, (
            "the sealed-corpus contract test is in the non-gating lane "
            "(#10194) — a green PR must not be read as \"the corpus was pinned\"")


def test_tortoise_api_change_selects_api_and_core():
    # #2938 item 1: mapping `tortoise/api.py` to `api` closes the coverage gap,
    # but its pinning tests are registered across surfaces — CORE_ALSO keeps
    # `core` in the selection so the core-registered importers of EventAPI
    # (test_extractor, test_projection via the slow leg, …) still run. An
    # api-only mapping would silently drop them — the exact regression #2938
    # exists to prevent.
    r = _sel(["tortoise/api.py"])
    assert r["full"] is False
    assert r["surfaces"] == ["api", "core"]
    selected = set(r["test_files"]) | set(r["slow_selected"])
    assert "test_api.py" in selected, "api-registered pinner must run"
    assert "test_extractor.py" in selected, "core-registered pinner must run"
    assert "test_projection.py" in selected, "core slow-leg pinner must run"


def test_tortoise_oauth_change_selects_api_and_core():
    # #3036: `tortoise/oauth.py` is the hosted OAuth implementation. Its pinning
    # tests are `api`-registered (test_oauth_mcp.py, test_oauth_token_fault.py,
    # test_3036_oauth_retention.py, test_attribution_actor.py,
    # test_user_identity_authority.py) and one is api+core
    # (test_control_plane_offload_3498.py). Before #3036 mapped it, an
    # oauth.py-only change fell through to `core` and silently skipped every
    # api pinner — the #2938/#3154/#4367 silent-drop class, on the very file a
    # retention or token-flow fix must change. CORE_ALSO keeps the core half.
    r = _sel(["tortoise/oauth.py"])
    assert r["full"] is False
    # MEMBERSHIP, not equality: `#6870` also wires this file into `onboarding` (it
    # serves the MCP consent page's CSS, whose mono-stack guard must run here),
    # and other surfaces may legitimately read it later. What this pin must keep
    # forbidding is a RE-POINTING that drops either half — equality against a
    # fixed list would red on every added consumer while still passing a
    # re-point that moved `api` to a different file.
    assert {"api", "core"} <= set(r["surfaces"]), (
        f"an oauth.py-only change selects {r['surfaces']} — it must keep reaching "
        "BOTH the api-registered pinners and the core one (#3036)")
    selected = set(r["test_files"]) | set(r["slow_selected"])
    assert "test_3036_oauth_retention.py" in selected, "the sweep suite must run"
    assert "test_oauth_mcp.py" in selected, "api-registered pinner must run"
    assert "test_control_plane_offload_3498.py" in selected, "api+core pinner must run"


def test_vendored_bundle_change_selects_api_and_runs_the_version_pin():
    # #3496: the consent page pins its browser auth client to a CDN specifier
    # whose version must equal the VENDORED bundle the behavioural harness
    # EXECUTES. `website/` is in NON_PYTHON_PREFIXES, so before this entry a
    # vendor-only bump matched no pattern and fell through to tier-1 smoke: the
    # version pin (test_oauth_consent_pkce.py::
    # test_page_specifier_matches_the_vendored_bundle_version) and the harness
    # that executes the very file being bumped would never run on the PR that
    # can break them — the #1349/#3332/#4171 silent-drop class.
    #
    # Asserted on the SURFACE, not only on the test-file list: the two derived
    # ratchets (test_every_source_pattern_is_selectable,
    # test_source_patterns_all_name_something_real) accept ANY non-empty
    # surface, so re-pointing this entry at another surface list (e.g.
    # `onboarding`) keeps them green while the api-registered pin silently
    # stops running. Same hole `test_tortoise_oauth_change_selects_api_and_core`
    # closes for tortoise/oauth.py, and the reason it asserts the surface too.
    #
    # The bundle name is DERIVED from the vendor dir, not written down. The
    # version in the path is irrelevant to what this test pins — `select()`
    # matches SOURCE_PATTERNS by prefix and never touches the filesystem, so a
    # stale or even bogus version still selects the same surface. The derivation
    # is what makes the `assert bundle` below bite: a hardcoded path would keep
    # passing against a vendor dir that no longer holds a bundle.
    vendor = REPO / "website" / "apps" / "dashboard" / "public" / "vendor"
    bundle = sorted(vendor.glob("supabase-*.min.js"))
    assert bundle, f"no vendored bundle under {vendor} — the page executes it"
    changed = str(bundle[0].relative_to(REPO))

    r = _sel([changed])
    assert r["surfaces"] == ["api"], (
        f"a vendored-bundle bump ({changed}) must select `api` — that is the "
        "surface the version pin and the harness that executes the bundle are "
        f"registered on; got {r['surfaces']}"
    )
    assert r["full"] is False
    assert "test_oauth_consent_pkce.py" in r["test_files"], (
        "the behavioural harness that EXECUTES the bumped bundle must run on "
        f"the bump; selected {sorted(r['test_files'])}"
    )


def test_surface_audit_skips_removal_for_unmapped_surfaces(tmp_path):
    # classify/core have no SOURCE_PATTERNS entry: "pins nothing from it" is
    # undefined, so the audit must not propose emptying classify
    repo = _audit_repo(tmp_path, {
        "tests/test_classify_x.py": "import os\n",
        "tests/test_api_nothing.py": "import os\n"})
    report = _audit(_audit_manifest(classify=["test_classify_x.py"],
                                    core=["test_classify_x.py"],
                                    api=["test_api_nothing.py"]), repo)
    for surface in ("classify", "core"):
        assert report["surfaces"][surface]["source_mapped"] is False
        assert report["surfaces"][surface]["removal"] == []
        assert report["surfaces"][surface]["addition"] == []
    # non-vacuity: a source-mapped surface in the SAME repo still reports
    assert [e["file"] for e in report["surfaces"]["api"]["removal"]] \
        == ["test_api_nothing.py"]


def test_surface_audit_reports_no_surface_and_duplicates(tmp_path):
    repo = _audit_repo(tmp_path, {
        "tests/test_dup.py": "import os\n",
        "tests/test_unregistered.py": "import os\n"})
    report = _audit(_audit_manifest(core=["test_dup.py", "test_dup.py"]), repo)
    assert report["no_surface"] == ["test_unregistered.py"]
    assert report["duplicates"] == {"core": ["test_dup.py"]}


def test_surface_audit_is_deterministic_and_non_mutating(tmp_path):
    repo = _audit_repo(tmp_path, {
        "tests/test_a.py": "from tortoise.hosted_api import app\n",
        "tests/test_b.py": "from battery.cli import main\n"})
    manifest = _audit_manifest(api=["test_b.py"], battery=["test_a.py"])
    before = {s: list(f) for s, f in manifest["surfaces"].items()}
    report = _audit(manifest, repo)
    # non-vacuity: the audit actually found the two cross-surface additions
    assert [e["file"] for e in report["surfaces"]["api"]["addition"]] \
        == ["test_a.py"]
    assert [e["file"] for e in report["surfaces"]["battery"]["addition"]] \
        == ["test_b.py"]
    first = render_surface_audit(report)
    second = render_surface_audit(_audit(manifest, repo))
    assert first == second
    assert manifest["surfaces"] == before, "the audit must not mutate the manifest"


def test_surface_audit_coverage_gap_names_only_gap_surface_members(tmp_path):
    # P1-1: only files this change does not select fail to run when the
    # unmapped source changes. A core-registered pinner still runs (via core),
    # so naming it under "never run" overstated the blast radius.
    # P3: assert on the DERIVED data (gap["files"]/["victims"]), not the
    # rendered sentence, so a legitimate wording change cannot break this.
    repo = _audit_repo(tmp_path, {
        "tortoise/eval.py": "",
        "tests/test_eval_registered.py": "from tortoise.eval import X\n",
        "tests/test_core_registered.py": "from tortoise.eval import X\n"})
    manifest = _audit_manifest(eval=["test_eval_registered.py"],
                               core=["test_core_registered.py"])
    report = _audit(manifest, repo)
    gap = next(g for g in report["coverage_gaps"]
               if g["path"] == "tortoise/eval.py")
    # both files are STRONG pinners (they import it); registration differs
    assert set(gap["files"]) == {"test_eval_registered.py",
                                 "test_core_registered.py"}
    assert gap["files"]["test_eval_registered.py"] == ["eval"]
    assert gap["files"]["test_core_registered.py"] == ["core"]
    assert gap["registered"] == ["core", "eval"]
    # `select(['tortoise/eval.py'])` selects `core`, so only the eval-registered
    # pinner is a victim; the core-registered one runs.
    assert gap["selected_surfaces"] == ["core"]
    assert gap["victims"] == ["test_eval_registered.py"]
    assert gap["runs"] == ["test_core_registered.py"]
    # ... and the rendered line agrees with the data
    never_line = render_surface_audit(report).split(
        "never run on a change to tortoise/eval.py:")[1].split("\n")[0]
    assert "test_eval_registered.py" in never_line
    assert "test_core_registered.py" not in never_line, (
        "a core-registered pinner runs via core and must not be in the "
        "\"never run\" list")


def test_surface_audit_coverage_gap_counts_unselected_surface_as_victim(tmp_path):
    # P2: `select(['tortoise/eval.py'], 'pull_request', manifest)` falls back
    # to `core` — it does NOT select `ep`, so an ep-registered pinner never
    # runs. The previous renderer asserted the non-gap pinners "run via
    # core/ep", which was false for the ep one.
    repo = _audit_repo(tmp_path, {
        "tortoise/eval.py": "",
        "tests/test_eval_registered.py": "from tortoise.eval import X\n",
        "tests/test_core_registered.py": "from tortoise.eval import X\n",
        "tests/test_ep_registered.py": "from tortoise.eval import X\n"})
    manifest = _audit_manifest(eval=["test_eval_registered.py"],
                               core=["test_core_registered.py"],
                               ep=["test_ep_registered.py"])
    report = _audit(manifest, repo)
    gap = next(g for g in report["coverage_gaps"]
               if g["path"] == "tortoise/eval.py")
    assert set(gap["files"]) == {"test_eval_registered.py",
                                 "test_core_registered.py",
                                 "test_ep_registered.py"}
    assert gap["files"]["test_ep_registered.py"] == ["ep"]
    assert gap["selected_surfaces"] == ["core"]
    assert gap["victims"] == ["test_ep_registered.py",
                              "test_eval_registered.py"]
    assert gap["runs"] == ["test_core_registered.py"]
    never_line = render_surface_audit(report).split(
        "never run on a change to tortoise/eval.py:")[1].split("\n")[0]
    assert "test_ep_registered.py" in never_line, (
        "an ep-registered pinner is not selected by a change that yields core")
    assert "test_core_registered.py" not in never_line


def test_surface_audit_ignores_function_local_helper_import(tmp_path):
    # P1 (this fix): a helper module is IMPORTED, not called, so an import
    # nested in one of its function bodies never executes and must not become
    # a strong addition. The previous BFS followed `ast.walk`, fabricating 25
    # battery additions from one call-time import in
    # tools/longmem_eval/report.py::build_methodology.
    repo = _audit_repo(tmp_path, {
        "battery/parity/runner.py": "",
        "tests/_deferred.py":
            "def build():\n"
            "    from battery.parity.runner import run\n"
            "    return run\n",
        "tests/test_via_deferred.py": "from tests._deferred import build\n"})
    report = _audit(_audit_manifest(core=["test_via_deferred.py"]), repo)
    assert report["surfaces"]["battery"]["addition"] == [], (
        "a function-local import in a helper never runs on import")
    assert "test_via_deferred.py" not in {
        e["file"] for s in report["surfaces"].values()
        for e in s["addition"] if e["strong"]}


def test_surface_audit_honors_module_level_helper_import(tmp_path):
    # complement of the test above: a MODULE-LEVEL import in a helper DOES
    # execute when the helper is imported, so it stays a strong pin.
    repo = _audit_repo(tmp_path, {
        "tests/_eager.py": "from battery.cli import main\n",
        "tests/test_via_eager.py": "from tests._eager import main\n"})
    report = _audit(_audit_manifest(core=["test_via_eager.py"]), repo)
    additions = {e["file"]: e for e in report["surfaces"]["battery"]["addition"]}
    assert additions["test_via_eager.py"]["evidence"] == [
        "battery/cli.py (via tests/_eager.py)"]
    assert additions["test_via_eager.py"]["strong"] is True


def test_surface_audit_honors_root_function_local_import(tmp_path):
    # the ROOT test file's function bodies DO run when the test runs, so its
    # own call-time imports stay strong pins — only HELPERS are pruned.
    repo = _audit_repo(tmp_path, {
        "tests/test_deferred_own.py":
            "def test_x():\n"
            "    from battery.cli import main\n"
            "    assert main\n"})
    report = _audit(_audit_manifest(core=["test_deferred_own.py"]), repo)
    additions = {e["file"]: e
                 for e in report["surfaces"]["battery"]["addition"]}
    assert additions["test_deferred_own.py"]["evidence"] == ["battery/cli.py"]
    assert additions["test_deferred_own.py"]["strong"] is True


def test_surface_audit_normalises_scalar_surface_values(tmp_path):
    # P3: a hand-edited scalar (`api: 3`, `api: {...}`, `api: "x.py"`) must
    # not raise or silently become a character list; it normalises to empty.
    repo = _audit_repo(tmp_path, {
        "tests/test_scalar.py": "from tortoise.api import EventAPI\n"})
    for bad in (3, {"a": 1}, "test_scalar.py"):
        manifest = _audit_manifest(core=["test_scalar.py"])
        manifest["surfaces"]["api"] = bad
        report = _audit(manifest, repo)  # must not raise
        assert report["surfaces"]["api"]["members"] == []
        assert report["surfaces"]["api"]["removal"] == []
        render_surface_audit(report)  # must not raise


def test_surface_audit_string_only_pin_is_weak_and_labelled(tmp_path):
    # P1-2: a path STRING is fixture data, not an import. It stays visible
    # (labelled) but must not make the file a strong pinnner, or the "never
    # run" claim inflates (test_ci_selection.py's own corpus).
    repo = _audit_repo(tmp_path, {
        "tortoise/eval.py": "",
        "tests/test_fixture_data.py":
            "PATHS = ['tortoise/eval.py']\n"
            "def test_x(): assert PATHS\n",
        "tests/test_fixture_elsewhere.py":
            "PATHS = ['battery/cli.py']\n"
            "def test_y(): assert PATHS\n"})
    report = _audit(_audit_manifest(eval=["test_fixture_data.py"],
                                    core=["test_fixture_elsewhere.py"]), repo)
    gap = next(g for g in report["coverage_gaps"]
               if g["path"] == "tortoise/eval.py")
    assert gap["files"] == {}, "a string-only reference is not a strong pinnner"
    assert list(gap["weak_files"]) == ["test_fixture_data.py"]
    out = render_surface_audit(report)
    assert '"tortoise/eval.py" (string, not an import)' in out, \
        "the human must still see the string pin, labelled"
    assert "1 string-only pinnner(s), weak" in out
    # an UNREGISTERED string-only reference is a weak addition, not candidate
    weak = {e["file"]: e for e in
            report["surfaces"]["battery"]["addition"] if not e["strong"]}
    assert weak["test_fixture_elsewhere.py"]["evidence"] == ['"battery/cli.py"']
    assert report["surfaces"]["battery"]["addition"] and all(
        not e["strong"] for e in report["surfaces"]["battery"]["addition"])
    assert ("string-only references — weak evidence, not counted as pins"
            in out)
    # ... and a strong (import) pin in the SAME repo still claims the gap
    repo2 = _audit_repo(tmp_path / "strong", {
        "tortoise/eval.py": "",
        "tests/test_imports_eval.py": "from tortoise.eval import X\n"})
    report2 = _audit(_audit_manifest(eval=["test_imports_eval.py"]), repo2)
    gap2 = next(g for g in report2["coverage_gaps"]
                if g["path"] == "tortoise/eval.py")
    assert list(gap2["files"]) == ["test_imports_eval.py"]


def test_surface_audit_credits_pin_reached_through_repo_root_helper(tmp_path):
    # P2: the BFS used to follow only tests/ helpers, so a test reaching a
    # surface through tools/ read as "pins nothing" -> false removal candidate
    # (the real report called test_temporal_constraint.py an sdk removal while
    # tools/longmem_eval/retrieve.py imports tortoise.retrieval).
    repo = _audit_repo(tmp_path, {
        "tortoise/retrieval.py": "",
        "tools/reach.py": "from tortoise.retrieval import search\n",
        "tests/test_via_tool.py": "from tools.reach import search\n",
        "tests/test_control.py": "import os\n"})
    # attribution: registered elsewhere, the sdk pin is an addition candidate
    # and the evidence names the helper chain
    additions = {e["file"]: e for e in
                 _audit(_audit_manifest(battery=["test_via_tool.py"]), repo)
                 ["surfaces"]["sdk"]["addition"]}
    assert additions["test_via_tool.py"]["evidence"] == [
        "tortoise/retrieval.py (via tools/reach.py)"]
    # ... and the false removal is gone: only the genuinely pin-less file stays
    report = _audit(_audit_manifest(sdk=["test_via_tool.py",
                                         "test_control.py"]), repo)
    assert [e["file"] for e in report["surfaces"]["sdk"]["removal"]] \
        == ["test_control.py"], "the helper-reached sdk pin must suppress it"


def test_surface_audit_removal_candidate_without_surface_pin_warns(tmp_path):
    # P3: every removal candidate has NO pin for its own surface by definition,
    # so the "don't act" cue belongs on all of them — shared-only members
    # (`shared: tortoise/sdk.py`) used to render as bare removals.
    repo = _audit_repo(tmp_path, {
        "tests/test_shared_only.py": "from tortoise.sdk import TortoiseSDK\n",
        "tests/test_no_refs.py": "import os\n"})
    report = _audit(_audit_manifest(sdk=["test_shared_only.py",
                                         "test_no_refs.py"]), repo)
    assert [e["file"] for e in report["surfaces"]["sdk"]["removal"]] \
        == ["test_no_refs.py", "test_shared_only.py"]
    out = render_surface_audit(report)
    assert ("test_shared_only.py <- (shared: tortoise/sdk.py)  "
            "⚠ no `sdk` pin resolved") in out
    assert ("test_no_refs.py <- (no source references at all)  "
            "⚠ no `sdk` pin resolved") in out


def test_surface_audit_tolerates_null_surface_value(tmp_path):
    # P3: a curated manifest can carry `null` for a surface; the audit must
    # traceback-proof it the way integrity() does with `files or ()`.
    repo = _audit_repo(tmp_path, {
        "tests/test_null_surface.py": "import os\n"})
    manifest = _audit_manifest(api=None, core=["test_null_surface.py"])
    report = _audit(manifest, repo)  # must not raise TypeError
    assert report["surfaces"]["api"]["members"] == []
    assert report["surfaces"]["api"]["removal"] == []
    assert report["surfaces"]["api"]["addition"] == []
    assert report["no_surface"] == []
    assert report["duplicates"] == {}
    # the renderer must survive it too
    assert "api" in render_surface_audit(report)


def test_real_manifest_has_no_duplicate_entries():
    # #3381: two files were each registered twice — one entry added by two
    # different PRs fixing the same drift independently. A same-surface
    # duplicate is invisible to select() (surfaces are unioned) and reported
    # non-fatally by `--integrity` and `--surface-audit` — never as a gate
    # failure — so pin its absence here where CI will actually see it.
    # Call the production detector rather than reimplementing it, so this pin
    # cannot drift from the real semantics (e.g. cross-surface dual
    # registration is deliberate and must stay allowed).
    dupes = duplicate_entries(load_manifest())
    assert dupes == [], f"duplicate manifest entries: {dupes}"


def test_halves_guard_reports_single_sided_pack_without_crashing():
    # #3407 review P2: the branch written to CATCH a zero-weight half died with
    # ZeroDivisionError while formatting its own diagnosis — `hi / lo` was
    # evaluated inside the f-string after `lo <= 0` had short-circuited the
    # comparison. A single-sided pack is reachable (a 1-file pool, or an
    # all-zero measured map), and this is the only check that catches it:
    # `leg_coverage_issues()` and `fast_files_absent_from_halves()` both pass
    # when one half is empty.
    from tools.ci_selection import workflow_halves_issues
    m = {"surfaces": {"core": ["test_only.py"]}, "tier1": [], "slow_files": [],
         "durations": {"test_only.py": 5.0}}
    issues = workflow_halves_issues(m, {"a": {"test_only.py"}, "b": set()})
    assert any("duration-imbalanced" in i for i in issues), issues


def test_halves_guard_reports_all_zero_map_without_crashing():
    from tools.ci_selection import workflow_halves_issues
    files = ["test_a.py", "test_b.py", "test_c.py"]
    m = {"surfaces": {"core": files}, "tier1": [], "slow_files": [],
         "durations": {f: 0.0 for f in files}}
    issues = workflow_halves_issues(m, {"a": set(files), "b": set()})
    assert any("duration-imbalanced" in i for i in issues), issues


def test_duration_issues_flags_non_numeric_value():
    # #3407 review P2: the guards iterated KEYS only, so a hand-edit typo in the
    # now-505-line map passed `--integrity` silently and then crashed
    # `push_legs` with a TypeError inside `split_fast_gate`'s sort key.
    from tools.ci_selection import duration_issues
    m = {"surfaces": {"core": ["test_crypto.py"]}, "tier1": [], "slow_files": [],
         "durations": {"test_crypto.py": "fast"}}
    issues = duration_issues(m)
    assert any("not numeric" in i for i in issues), issues


def test_integrity_chain_names_bad_duration_instead_of_crashing():
    # #3407 review P1 (cycle 2): `duration_issues` alone is NOT the gate. The
    # real `--integrity` chain evaluates `leg_coverage_issues()` -> `push_legs()`
    # -> `split_fast_gate()`, whose sort key negates the weight — so a
    # non-numeric value raised TypeError inside the packer BEFORE the check
    # that names it had run, and the gate tracebacked instead of diagnosing.
    # This test runs the CLI's actual chain, on a real manifest with one real
    # fast-pool key poisoned, which the isolated helper test cannot see.
    from tools.ci_selection import (
        duration_coverage_issues,
        duration_issues,
        fast_pool,
        integrity,
        leg_coverage_issues,
        load_manifest,
    )
    # cycle 3 added an int beyond float range (math.isfinite raises
    # OverflowError) and a negative duration (impossible data, exited 0).
    for bad in (None, "fast", float("nan"), 10 ** 400, -5.0):
        m = load_manifest()
        key = fast_pool(m)[0]
        m = dict(m)
        m["durations"] = dict(m.get("durations") or {})
        m["durations"][key] = bad
        # Must not raise.
        problems = (integrity(m) + slow_file_issues(m) + duration_issues(m)
                    + leg_coverage_issues(m) + duration_coverage_issues(m))
        named = [p for p in problems if "durations value" in p]
        assert named, f"a bad duration value ({bad!r}) was not named: {problems}"


def test_split_fast_gate_cannot_crash_on_a_malformed_duration():
    # The packer must degrade to the default, never raise — belt and braces for
    # the ordering fix above (any consumer, any order).
    from tools.ci_selection import split_fast_gate
    files = ["tests/test_a.py", "tests/test_b.py"]
    for bad in (None, "fast", float("nan"), float("inf"), True):
        a, b = split_fast_gate(files, {"test_a.py": bad})
        assert len(a) + len(b) == 2, (bad, a, b)


def test_integrity_cli_exits_nonzero_for_a_huge_int_and_a_negative(tmp_path, monkeypatch):
    # #3407 review cycle 3: the isolated helper tests could not see an EXIT
    # CODE, and the two residual classes were both silent-green failures. This
    # drives the real CLI entry point (`main()`, which reads sys.argv and the
    # module-level MANIFEST) over a genuinely poisoned manifest.
    import sys as _sys

    import yaml

    from tools import ci_selection as cs
    for bad in (10 ** 400, -5.0, float("nan")):
        m = cs.load_manifest()
        m = dict(m)
        m["durations"] = dict(m.get("durations") or {})
        m["durations"][cs.fast_pool(m)[0]] = bad
        poisoned = tmp_path / "poisoned-ci-surfaces.yml"
        poisoned.write_text(yaml.safe_dump(m))
        monkeypatch.setattr(cs, "MANIFEST", poisoned)
        monkeypatch.setattr(_sys, "argv", ["ci_selection.py", "--integrity"])
        assert cs.main() != 0, f"a bad duration value ({bad!r}) exited 0"


def test_null_or_non_mapping_durations_reports_instead_of_tracebacking(tmp_path, monkeypatch):
    # #3407 review cycle 4 (pre-existing): `duration_issues` and `--split` read
    # `.get("durations", {})`, which returns a present-but-NULL `durations:` key
    # as None — the empty-map state `duration_coverage_issues` documents as
    # "NOT a failure". A raw TypeError traceback is not a diagnosis: it makes
    # the gate look broken rather than making it say what is wrong.
    import sys as _sys

    import yaml

    from tools import ci_selection as cs
    # `None` is NOT in this list: null/absent is the documented empty-map
    # PASS, and is asserted below.
    for bad in (0, "foo", [1.0]):
        m = dict(cs.load_manifest())
        m["durations"] = bad
        poisoned = tmp_path / "bad-durations.yml"
        poisoned.write_text(yaml.safe_dump(m))
        monkeypatch.setattr(cs, "MANIFEST", poisoned)
        monkeypatch.setattr(_sys, "argv", ["ci_selection.py", "--integrity"])
        assert cs.main() != 0, f"durations={bad!r} exited 0"
    # The documented empty-map contracts must still PASS, or the guard above
    # has simply turned one wrong answer into another.
    for empty in ({}, None):
        m = dict(cs.load_manifest())
        if empty is None:
            m.pop("durations", None)
        else:
            m["durations"] = {}
        ok = tmp_path / "empty-durations.yml"
        ok.write_text(yaml.safe_dump(m))
        monkeypatch.setattr(cs, "MANIFEST", ok)
        monkeypatch.setattr(_sys, "argv", ["ci_selection.py", "--integrity"])
        assert cs.main() == 0, f"an empty durations map ({empty!r}) was treated as a failure"


# ── #6243 review cycle 2 (K1): presence-with-junk is RED, absence is a notice ──

# Distinguishes "remove the key" from "the key is present with a `null` value"
# — the two states the whole promotion turns on, and which a bare `None` default
# would collapse (a present explicit `null` is a MALFORMED value, not absence).
_ABSENT = object()


def _integrity_with_stamp(tmp_path, monkeypatch, stamp=_ABSENT):
    """Drive the real `--integrity` CLI over the committed manifest with the
    capture date set to `stamp` (the key is removed entirely when `stamp` is
    left out)."""
    import sys as _sys

    import yaml

    from tools import ci_selection as cs
    # The committed map, read from the REPO path — never through `cs.MANIFEST`,
    # which a previous call in the same test has already monkeypatched.
    base = cs.REPO / "config" / "ci-surfaces.yml"
    m = dict(cs._normalize_surfaces(yaml.safe_load(base.read_text())))
    m.pop("durations_captured_at", None)
    if stamp is not _ABSENT:
        m["durations_captured_at"] = stamp
    path = tmp_path / "stamped-ci-surfaces.yml"
    path.write_text(yaml.safe_dump(m))
    monkeypatch.setattr(cs, "MANIFEST", path)
    monkeypatch.setattr(_sys, "argv", ["ci_selection.py", "--integrity"])
    return cs.main()


def test_integrity_reds_a_stale_but_parseable_stamp(tmp_path, monkeypatch):
    # (a) The baseline the inversion is measured against: an old but parseable
    # capture date is an OBSERVED defect, so the enforcing entry point exits 1.
    assert _integrity_with_stamp(tmp_path, monkeypatch,
                                 "2000-01-01T00:00:00Z") == 1


def test_integrity_reds_a_present_but_unparseable_stamp(tmp_path, monkeypatch):
    # (b) #6243 review cycle 2 (K1): this is the INVERSION. `--integrity`
    # printed the validator's UNKNOWN list as a notice and returned only `red`,
    # so a stale-but-parseable stamp exited 1 while the SAME value degraded to
    # 'not-a-date' exited 0 — strictly worse data, strictly better verdict. A
    # stamp that is present but unparseable is a MALFORMED manifest value, i.e.
    # an observed defect: RED, exit 1. Absence stays the soft notice below.
    assert _integrity_with_stamp(tmp_path, monkeypatch, "not-a-date") == 1, (
        "a present-but-unparseable stamp must be RED (exit 1), not the soft "
        "UNKNOWN notice — otherwise degrading a stale stamp to junk flips "
        "exit 1 to exit 0 (fail-open, K1)")


def test_integrity_reds_every_present_but_unparseable_spelling(
        tmp_path, monkeypatch):
    """#6243 review cycle 3 (L1): pin the CLASS, not one spelling.

    The predicate is the KEY'S PRESENCE plus `_parse_captured_at`'s verdict,
    so no present value the parser rejects can slip through the soft channel —
    in particular `""` and an explicit `null`, which a value-tuple test
    (`raw_stamp not in (None, "")`) exempted as absence. Only a genuinely
    MISSING key is the non-gating notice (pinned by the next test).
    """
    for stamp in ("", None, "   ", "not-a-date", 0, [], {}, False):
        assert _integrity_with_stamp(tmp_path, monkeypatch, stamp) == 1, (
            f"a present-but-unparseable stamp {stamp!r} must be RED (exit 1)"
        )


def test_integrity_never_lets_blanking_a_stale_stamp_exit_zero(
        tmp_path, monkeypatch):
    """The exact inversion pair, in ONE test: the same manifest with a stale
    parseable stamp exits 1, and blanking that stamp to `""` must STILL exit 1
    — never 0. A blanked stamp is a MALFORMED value, not an absent one."""
    assert _integrity_with_stamp(
        tmp_path, monkeypatch, "2000-01-01T00:00:00Z") == 1
    assert _integrity_with_stamp(tmp_path, monkeypatch, "") == 1, (
        "blanking a stale stamp must not turn exit 1 into exit 0")


def test_integrity_still_notices_an_absent_stamp_and_exits_zero(
        tmp_path, monkeypatch, capsys):
    # (c) Absence — the key GENUINELY MISSING, which is the ONLY case that stays
    # soft — is the never-refreshed state no lane owns until #6091 lets a
    # refresh carry a real date, so it must stay a NOTICE: exit 0, with the
    # reason still printed (visible, not silent).
    assert _integrity_with_stamp(tmp_path, monkeypatch) == 0
    out = capsys.readouterr().out
    assert "durations map UNKNOWN (not gating this run)" in out, out
    assert "durations_captured_at" in out, out


def test_integrity_prints_a_promoted_stamp_once_with_red_wording(
        tmp_path, monkeypatch, capsys):
    """#6243 review cycle 3 (L2): an OBSERVED defect must not ALSO be announced
    through the soft channel. `--integrity` used to print the UNKNOWN notice
    for the promoted reason and gate on it at the same time."""
    assert _integrity_with_stamp(tmp_path, monkeypatch, "not-a-date") == 1
    out = capsys.readouterr().out
    assert "durations map UNKNOWN (not gating this run)" not in out, out
    problems = next(line for line in out.splitlines()
                    if line.startswith("❌ manifest drift:"))
    assert "not a parseable timestamp" in problems, problems
    assert problems.count("not a parseable timestamp") == 1, problems


# ── #4378: changed-set diffs must disable rename detection ──────────────
#
# The predicate below is per COMMAND, not per line: a workflow step MAY write two
# `git diff` invocations on ONE line joined by `||` (that was `python-ci.yml`'s
# "Tiered selection" shape until #3442 collapsed it to one canonical merge-base
# diff), and a whole-line substring check is satisfied by the flag on either side
# — removing it from the second command reintroduces #4378 while the check still
# passes. These helpers are module-level so the parser can be exercised directly
# on synthetic shell text.

# The shell joiners that separate one command from another. The DOUBLED forms
# are listed before the single characters so a regex alternation matches `||` as
# one separator rather than two adjacent `|`s, and `&&` likewise. The single
# `|`/`&` are included because a pipeline and a backgrounded command split two
# invocations exactly as `;` does: without them `git diff --name-only A |\
# git diff --name-status B` was ONE parsed command whose ownership accounted for
# the second diff's flag, so a flagless `--name-status` read clean (#4378).
_SHELL_SEPARATORS = re.compile(r"\|\||&&|;|\||&")

# The spellings that make a `git diff` a changed-set computation. `--name-only` is
# the one `changed_set_git_diff_commands` parses; the other two are listed so the
# raw-line cross-check (`unparsed_changed_set_diff_lines`) reports a future site
# that uses them as UNPARSED — a loud failure — instead of letting it pass.
_CHANGED_SET_DIFF_FLAGS = ("--name-only", "--name-status", "--diff-filter")


def _strip_shell_comments_and_quotes(line: str) -> str:
    """Remove shell comments and the CONTENT of ordinary quoted strings.

    `#` starts a comment only OUTSIDE quotes (and, per shell, only at the start
    of a word). The content of an ordinary quoted string is removed so a
    separator, `#`, or flag written inside quotes can neither split a command nor
    satisfy/defeat the pin; a space is left in its place so adjacent tokens do
    not merge. Single-quoted text is a literal in every context and is always
    removed.

    A command substitution is the one exception: `$( … )` runs a real command, so
    its contents are EXTRACTED and returned as commands of their own — including
    when the substitution sits inside double quotes. The substitution becomes a
    space in the surrounding text, so it can neither hide nor lend a flag to the
    command around it. Without this, `CHANGED="$(git diff --name-only … )"` — the
    quoted `="$( … )"` house style this repo writes throughout its workflows —
    was invisible to the pin in BOTH directions (not counted, not flagged), while
    only the unquoted `CHANGED=$( … )` form was seen.

    Only `$( … )` is unwrapped: a backtick substitution's body is left in place
    (though `_shell_tokens` still tokenises it, so a backtick diff IS parsed),
    and this function does not resolve a diff reached through a variable,
    function or `eval`.
    """
    out: list[list[str]] = [[]]
    # Quote context to restore at each `$( … )`'s `)`; `None` = opened unquoted.
    saved_quotes: list[str | None] = []
    extracted: list[str] = []
    quote: str | None = None
    i = 0
    n = len(line)
    while i < n:
        ch = line[i]
        if quote == "'":
            if ch == "'":
                quote = None
                out[-1].append(" ")
            i += 1
            continue
        if quote == '"':
            if ch == "\\" and i + 1 < n:
                i += 2  # an escaped character inside a string is string content
                continue
            if ch == '"':
                quote = None
                out[-1].append(" ")
                i += 1
                continue
            if ch == "$" and line.startswith("$(", i):
                out[-1].append(" ")
                saved_quotes.append(quote)
                quote = None
                out.append([])
                i += 2
                continue
            i += 1
            continue
        # Unquoted, or inside a `$( … )` substitution — both are shell code.
        if ch == "\\" and i + 1 < n:
            out[-1].append(ch)
            out[-1].append(line[i + 1])
            i += 2
            continue
        if ch in ("'", '"'):
            quote = ch
            out[-1].append(" ")
            i += 1
            continue
        if ch == "#" and (i == 0 or line[i - 1].isspace()):
            break
        if ch == "$" and line.startswith("$(", i):
            out[-1].append(" ")
            saved_quotes.append(None)
            quote = None
            out.append([])
            i += 2
            continue
        if ch == ")" and len(out) > 1:
            extracted.append("".join(out.pop()))
            quote = saved_quotes.pop()
            i += 1
            continue
        out[-1].append(ch)
        i += 1
    while len(out) > 1:  # an unterminated substitution still contributes code
        extracted.append("".join(out.pop()))
    stripped = "".join(out[0])
    if extracted:
        stripped += " ; " + " ; ".join(extracted)
    return stripped


def _strip_shell_comments(line: str) -> str:
    """Drop a trailing shell comment, KEEPING quoted text and substitutions.

    `#` starts a comment only OUTSIDE quotes and at the start of a word. Unlike
    `_strip_shell_comments_and_quotes`, everything else survives: the raw-line
    cross-check (`unparsed_changed_set_diff_lines`) must be able to see a
    changed-set spelling the parser's quote stripping would hide (an `eval` or
    `sh -c` whose argument names a `git diff`), so it over-reports rather than
    misses.
    """
    quote: str | None = None
    i = 0
    n = len(line)
    while i < n:
        ch = line[i]
        if quote == "'":
            if ch == "'":
                quote = None
            i += 1
            continue
        if quote == '"':
            if ch == "\\" and i + 1 < n:
                i += 2
                continue
            if ch == '"':
                quote = None
            i += 1
            continue
        if ch in ("'", '"'):
            quote = ch
            i += 1
            continue
        if ch == "#" and (i == 0 or line[i - 1].isspace()):
            return line[:i]
        i += 1
    return line


def _logical_shell_line_spans(text: str) -> list[tuple[int, int, str]]:
    """(start, end, joined) for every backslash-joined logical line.

    Comments/quotes are stripped per physical line first. `start`/`end` are
    1-based physical line numbers (equal for a one-line invocation); the parser
    attributes a command to `start`, so the span is what the raw-line cross-check
    uses to decide whether a physical line was already accounted for.
    """
    spans: list[tuple[int, int, str]] = []
    parts: list[str] = []
    start: int | None = None
    end = 0
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = _strip_shell_comments_and_quotes(raw)
        if start is None:
            start = lineno
        end = lineno
        stripped = line.rstrip()
        if stripped.endswith("\\") and not stripped.endswith("\\\\"):
            parts.append(stripped[:-1])
            continue
        parts.append(line)
        spans.append((start, end, " ".join(parts)))
        parts = []
        start = None
    if start is not None:
        spans.append((start, end, " ".join(parts)))
    return spans


def _logical_shell_lines(text: str) -> list[tuple[int, str]]:
    """Join backslash continuations into one logical line.

    Comments/quotes are stripped per physical line first; the 1-based number of
    the line the logical line STARTED on is preserved for file:line reporting.
    """
    return [(start, joined) for start, _end, joined in _logical_shell_line_spans(text)]


def iter_shell_commands(text: str) -> list[tuple[int, str]]:
    """Split shell text into individual commands on every shell joiner.

    The joiners are `||`, `&&`, `;`, `|` and `&` (`_SHELL_SEPARATORS`), the
    doubled forms matched first so each is one separator; a newline is a joiner
    by construction, since a logical line ends there unless backslash-continued.
    Comments/quotes are stripped and backslash continuations joined first, so a
    multi-line invocation is read as ONE command. Returns (line, command).
    """
    commands: list[tuple[int, str]] = []
    for lineno, logical in _logical_shell_lines(text):
        for part in _SHELL_SEPARATORS.split(logical):
            part = part.strip()
            if part:
                commands.append((lineno, part))
    return commands


def _shell_tokens(command: str) -> list[str]:
    # Shell grouping characters bind adjacent words to each other: `$(git` is one
    # shlex token, and `HEAD)` is another. Treat them as whitespace so `git`,
    # `diff` and the flags are recognised as standalone tokens either way.
    detached = re.sub(r"[()$`]", " ", command)
    try:
        return shlex.split(detached, comments=False, posix=True)
    except ValueError:  # unbalanced quote the stripper did not catch
        return detached.split()


def changed_set_git_diff_commands(text: str) -> list[tuple[int, str]]:
    """The changed-set diff invocations in `text`.

    A command qualifies when its tokens contain `git` AND `diff` AND
    `--name-only`. Token-based, not `"git diff" in command`, so
    `git -c <cfg> diff ...` is recognised, as is a diff living in a command
    substitution — both the unquoted `CHANGED=$(git diff ...)` form and the
    quoted `CHANGED="$(git diff ...)"` house style.
    """
    found: list[tuple[int, str]] = []
    for lineno, command in iter_shell_commands(text):
        tokens = _shell_tokens(command)
        if "git" in tokens and "diff" in tokens and "--name-only" in tokens:
            found.append((lineno, command))
    return found


def _no_renames_is_honoured(tokens: list[str]) -> bool:
    """True iff every `--no-renames` token precedes the FIRST `--` separator.

    `git diff` treats every token after `--` as a PATHSPEC, so a `--no-renames`
    written there is swallowed: the command runs, rc=0, no warning, and rename
    detection stays ON. Token membership alone therefore accepts an argument git
    ignores — position is the whole rule. With no `--` there is no pathspec region
    to fall behind, so the flag is honoured wherever it sits.
    """
    if "--no-renames" not in tokens:
        return False
    if "--" not in tokens:
        return True
    first_separator = tokens.index("--")
    return all(
        index < first_separator
        for index, token in enumerate(tokens)
        if token == "--no-renames"
    )


def changed_set_git_diff_offenders(text: str) -> list[tuple[int, str]]:
    """Changed-set diff commands that do NOT actually disable rename detection.

    An offender is a parsed command where `--no-renames` is ABSENT, or present
    only after the first `--` (where git ignores it).
    """
    return [
        (lineno, command)
        for lineno, command in changed_set_git_diff_commands(text)
        if not _no_renames_is_honoured(_shell_tokens(command))
    ]


def _parsed_changed_set_flag_ownership(text: str) -> dict[int, dict[str, int]]:
    """Per logical-span START line, how many parsed commands own each flag.

    A parsed command is attributed to the START line of the logical span it was
    read from (the same attribution `iter_shell_commands` uses when reporting),
    and OWNS every `_CHANGED_SET_DIFF_FLAGS` spelling its tokens carry. Counting
    occurrences rather than recording a set is what makes the cross-check fail
    closed on co-location: if a span's raw text carries a flag N times while its
    parsed commands account for fewer than N, the surplus is unaccounted for.
    """
    ownership: dict[int, dict[str, int]] = {}
    for lineno, command in changed_set_git_diff_commands(text):
        tokens = _shell_tokens(command)
        owned = ownership.setdefault(
            lineno, {flag: 0 for flag in _CHANGED_SET_DIFF_FLAGS}
        )
        for flag in _CHANGED_SET_DIFF_FLAGS:
            if flag in tokens:
                owned[flag] += 1
    return ownership


def unparsed_changed_set_diff_lines(text: str) -> list[tuple[int, str]]:
    """Comment-stripped raw lines that carry a changed-set spelling the parser MISSED.

    This is the pin's fail-closed net. `changed_set_git_diff_commands` recognises
    exactly one spelling (`git diff … --name-only`); any other way of writing a
    changed-set diff (`--name-status`, `--diff-filter`, an `eval`/`sh -c` whose
    quoted argument names the command, a spelling added after this file was
    written) yields no parsed command, so before this check it passed silently —
    the failure mode every review cycle of #4378 kept re-closing one spelling at a
    time. Here a logical (backslash-joined) line that contains `git` AND `diff`
    AND a `_CHANGED_SET_DIFF_FLAGS` spelling the parsed commands do not account
    for is returned for the caller to fail on.

    FAIL-CLOSED ON CO-LOCATION, not merely on absence. The net used to mark a
    whole line "covered" as soon as ANY parsed command started on it, so a second,
    unparsed changed-set diff sharing that line (`… || git diff --name-status …`
    beside a parsed `--name-only`, or an unparsed `eval "git diff --name-only …"`
    beside one) was suppressed by its neighbour's parse. It now COUNTS
    OCCURRENCES per flag spelling: a span whose raw text carries a flag N times
    while its parsed commands account for fewer than N is reported. The splitter's
    joiners (`||`, `&&`, `;`, `|`, `&`, plus the newline that ends a logical span)
    each make the two invocations separate parsed candidates, so the second's flag
    cannot be absorbed by the first's parse. A same-line
    `--name-only` beside a parsed `--name-only` is still accounted for only when
    both invocations are parsed; a spelling the parser cannot tokenise on any
    logical line is never accounted for and is always reported.

    `git`/`diff`/flag are matched as SUBSTRINGS of the comment-stripped raw line,
    not through the parser's tokeniser, and the presence check spans a backslash
    continuation: it is deliberately coarser than the parser, so a spelling the
    parser mis-splits — or one whose `git`/`diff` and flag land on different
    physical lines of ONE continued command — is still reported. It can only
    SUPPRESS a report for a spelling the parser genuinely accounted for; it can
    never rescue an offender. Stated limits — do not read more into it: a spelling
    whose raw text never carries all three substrings on one logical line (a diff
    reached through a bare variable or alias, a flag assembled from string
    fragments on SEPARATE statements, a command name bound at runtime) is still
    invisible, as is any file outside the caller's scan. No changed-set `git diff`
    in `.github/workflows/` reaches the pin through any such route today.
    """
    ownership = _parsed_changed_set_flag_ownership(text)
    physical = text.splitlines()
    unparsed: list[tuple[int, str]] = []
    for start, end, _joined in _logical_shell_line_spans(text):
        raw_span = " ".join(
            _strip_shell_comments(physical[i - 1]) for i in range(start, end + 1)
        )
        if "git" not in raw_span or "diff" not in raw_span:
            continue
        owned = ownership.get(start, {})
        if any(
            raw_span.count(flag) > owned.get(flag, 0)
            for flag in _CHANGED_SET_DIFF_FLAGS
        ):
            unparsed.append((start, physical[start - 1].strip()))
    return unparsed


def _workflow_files(wf_dir: Path) -> list[Path]:
    """Every workflow file in `wf_dir` — BOTH `.yml` and `.yaml`.

    `.yaml` is a valid GitHub Actions workflow extension, so the scan's claim to
    read EVERY workflow in `.github/workflows/` is only true while this glob
    keeps both. The repo has no `.yaml` workflow today, which is exactly why the
    `.yaml` half needs its own test: without one, deleting the glob left the
    changed-set tests green. Handing every caller (the pin and the synthetic
    test) through this single function is what makes the mutation visible.
    """
    return sorted({*wf_dir.glob("*.yml"), *wf_dir.glob("*.yaml")})


def _compiled_run_scalar_size(run: str) -> int:
    """The length GitHub measures for a `run:` scalar: the script PLUS its spans.

    GitHub compiles a `run:` that contains `${{ }}` and applies its 21000-byte cap
    to the COMPILED scalar, so the expression text is charged ON TOP of the raw
    script. Measuring the raw scalar alone under-reports: on 2026-10-16 it read
    `ci.yml`'s `changes` gate as 20944 — "56 bytes under" — while GitHub measured
    21010 and refused to load the workflow, so the branch ran zero jobs for eight
    commits (#8734).
    """
    spans = re.findall(r"\$\{\{.*?\}\}", run)
    return len(run.encode("utf-8")) + sum(len(s.encode("utf-8")) for s in spans)


def test_compiled_run_scalar_measure_counts_expression_spans():
    """The #8734 fixture: a scalar GitHub refuses must measure OVER, not under.

    The pin below is worth its green only if its measure reproduces the refusal
    it exists to prevent. This freezes the incident's exact shape — 20944 raw
    UTF-8 bytes plus the 66 bytes of `${{ inputs.main_health }}` and
    `${{ github.event.pull_request.base.sha }}` = 21010 — which GitHub refused
    even though the raw count read as 56 bytes of HEADROOM. A revert to the
    raw-only measure reddens here instead of shipping the zero-job outage again.
    """
    span_main = "${{ inputs.main_health }}"
    span_base = "${{ github.event.pull_request.base.sha }}"
    run = "x" * (20944 - len(span_main) - len(span_base)) + span_main + span_base
    raw = len(run.encode("utf-8"))
    compiled = _compiled_run_scalar_size(run)
    assert raw == 20944, "fixture must reproduce the incident's raw size"
    assert compiled == 21010, "the compiled measure must charge the expression spans"
    assert compiled > 21000, "the fixture is a scalar GitHub actually refuses"
    assert raw <= 21000, "the pre-#8734 raw-only measure called this safe"


def test_expression_bearing_run_scalars_stay_under_githubs_21000_byte_cap():
    """#6253: GitHub compiles a `run:` that contains `${{ }}`, capped at 21000 bytes.

    Measured on main 2026-10-07: `ci.yml`'s `changes` gate script reached 21043
    UTF-8 bytes and GitHub refused to LOAD the workflow, so `CI` ran ZERO jobs on
    every branch — none of the five required contexts (`docs`, `legal-e2e`,
    `license-surface`, `pricing-artifact`, `test-isolation`) could appear, and no
    PR could go green. The cap is on the COMPILED scalar, and a `run:` with NO
    `${{ }}` is never compiled (`ai-review-gate.yml` loads a 34 KB one), so this
    pin guards exactly the scalars GitHub compiles. actionlint catches expression
    SYNTAX but not this length (#6253), which is why the invariant is pinned here
    — a comment added to a script this size re-arms a silent full-CI outage.

    Because the cap is on the COMPILED scalar, the measure must be the compiled
    size: the 2026-10-16 incident was `ci.yml`'s `changes` gate at 20944 raw PLUS
    66 bytes of expression spans = 21010, which a raw-only measure read as 56
    bytes of headroom while GitHub was refusing the file and the branch ran zero
    jobs for eight commits (#8734). Expression spans are counted here, so a
    script cannot hide behind the difference between the two measures.
    """
    limit = 21000
    expr = "${{"
    offenders = []
    for wf in _workflow_files(REPO / ".github" / "workflows"):
        doc = yaml.safe_load(wf.read_text(encoding="utf-8")) or {}
        for job_id, job in (doc.get("jobs") or {}).items():
            for step in (job or {}).get("steps") or []:
                if not isinstance(step, dict):
                    continue
                run = step.get("run")
                if not isinstance(run, str) or expr not in run:
                    continue
                # The compiled scalar: the script plus every expression span, as
                # GitHub assembles it before applying the cap.
                size = _compiled_run_scalar_size(run)
                if size > limit:
                    offenders.append((wf.name, job_id, step.get("name", "?"), size))
    assert not offenders, (
        "a `run:` containing `${{ }}` reached GitHub's 21000-byte expression cap, "
        f"so GitHub refuses to LOAD the workflow (zero jobs, no check-runs): {offenders}"
    )


def _scan_changed_set_diffs(workflows: list[Path]) -> tuple[int, list[str], list[str]]:
    """Scan `workflows` for changed-set diffs; return (checked, offenders, unparsed).

    `checked` counts PARSED changed-set commands (the non-vacuity floor's input);
    the two lists hold `file:line: raw` reports ready to embed in an assertion
    message. Kept separate from the pin so `test_every_changed_set_diff_scan_covers_yaml_workflows`
    can run the SAME scan over a tmp dir and freeze the `.yaml` glob.
    """
    offenders: list[str] = []
    unparsed: list[str] = []
    checked = 0
    for wf in workflows:
        text = wf.read_text()
        checked += len(changed_set_git_diff_commands(text))
        raw_lines = text.splitlines()
        for lineno, command in changed_set_git_diff_offenders(text):
            raw = raw_lines[lineno - 1].strip() if lineno <= len(raw_lines) else command
            offenders.append(f"{wf.name}:{lineno}: {raw}")
        for lineno, raw in unparsed_changed_set_diff_lines(text):
            unparsed.append(f"{wf.name}:{lineno}: {raw}")
    return checked, offenders, unparsed


def test_every_changed_set_diff_scan_covers_yaml_workflows(tmp_path):
    """#4378 FIX 2: the pin scans `*.yaml` workflows too, not only `*.yml`.

    The pin reads `.github/workflows/` for BOTH extensions, but this repo has no
    `.yaml` workflow, so removing the `.yaml` glob left every changed-set test
    green — the `.yaml` claim was unfrozen. This writes a synthetic `.yaml`
    workflow (a flagless changed-set diff, and a flagged `.yml` sibling) into a
    tmp dir and runs the SAME scan the pin runs: the `.yaml` offender must be
    reported. No repo fixture is added; nothing here touches `.github/`.
    """
    wf_dir = tmp_path / ".github" / "workflows"
    wf_dir.mkdir(parents=True)
    (wf_dir / "synthetic.yaml").write_text(
        "steps:\n"
        "  - run: |\n"
        '      git diff --name-only "$BASE" HEAD\n'
    )
    (wf_dir / "synthetic.yml").write_text(
        'steps:\n  - run: git diff --no-renames --name-only "$BASE" HEAD\n'
    )
    workflows = _workflow_files(wf_dir)
    assert {p.name for p in workflows} == {"synthetic.yml", "synthetic.yaml"}, workflows
    checked, offenders, unparsed = _scan_changed_set_diffs(workflows)
    assert checked == 2, (checked, offenders, unparsed)
    assert len(offenders) == 1, (offenders, unparsed)
    assert "synthetic.yaml" in offenders[0], offenders
    assert "--no-renames" not in offenders[0], offenders
    assert unparsed == [], unparsed


def test_changed_set_parser_reads_commands_not_lines():
    """#4378 FIX 1: two `git diff`s on one `||` line are TWO commands.

    A changed-set computation can put two `git diff`s on one `||` line (the
    `... || git diff --no-renames --name-only ...` shape). A whole-line
    substring predicate is satisfied by the flag on EITHER side, so removing it
    from the second command would reintroduce #4378 undetected. The synthetic
    inputs below freeze that regression. (#3442 removed this shape from
    `python-ci.yml`'s "Tiered selection" step — it is one canonical merge-base
    diff now — but the parser must keep splitting commands, because any workflow
    may still write it.)
    """
    one_flagged = (
        'CHANGED=$(git diff --no-renames --name-only "$BASE...HEAD" '
        '|| git diff --name-only "$BASE" HEAD)\n'
    )
    commands = changed_set_git_diff_commands(one_flagged)
    assert len(commands) == 2, commands
    offenders = changed_set_git_diff_offenders(one_flagged)
    assert len(offenders) == 1, offenders
    assert offenders[0][0] == 1
    assert "--name-only" in offenders[0][1]
    # both flagged -> no offender
    both_flagged = (
        'CHANGED=$(git diff --no-renames --name-only "$BASE...HEAD" '
        '|| git diff --no-renames --name-only "$BASE" HEAD)\n'
    )
    assert changed_set_git_diff_offenders(both_flagged) == []


def test_changed_set_parser_splits_on_every_documented_separator():
    """#4378: `&&`, `;`, `|` and `&` split commands exactly like `||`.

    The parser's contract is that a line is split on every shell joiner — `||`,
    `&&`, `;`, `|` and `&` (`_SHELL_SEPARATORS`, doubled forms first). Each is
    exercised independently here, so dropping one from that set fails this test:
    narrowing it to `||` alone left every parser test green (proven by runtime
    monkeypatch), and a flagless second command behind any other joiner was
    silently accepted (`commands=1, offenders=0`).
    """
    flagged = 'git diff --no-renames --name-only "$BASE" HEAD'
    flagless = 'git diff --name-only "$BASE" HEAD'
    for sep in ("&&", ";", "|", "&"):
        text = f"{flagged} {sep} {flagless}\n"
        commands = changed_set_git_diff_commands(text)
        assert len(commands) == 2, (sep, commands)
        offenders = changed_set_git_diff_offenders(text)
        assert len(offenders) == 1, (sep, offenders)
        assert offenders[0][0] == 1, (sep, offenders)
        assert "--name-only" in offenders[0][1], (sep, offenders)
        assert "--no-renames" not in offenders[0][1], (sep, offenders)


def test_changed_set_parser_separates_bare_pipe_and_amp_merge():
    """#4378 FIX 1: a bare `|`/`&` keeps two invocations SEPARATE, not merged.

    A pipeline (`|`) and a backgrounded command (`&`) each split two commands
    just as `;` does, but the separator set used to carry only the doubled forms
    (`||`, `&&`) — so this shape was read as ONE command:

        git diff --no-renames --name-only A | git diff --name-status B

    The merged command carried `--no-renames`, its token list contained BOTH
    spellings, and the ownership count therefore matched the raw span exactly:
    `offenders=[]` (the one parsed command is flagged) AND `unparsed=[]` (the
    second diff's `--name-status` was accounted for by the merge) — a flagless
    changed-set diff that read CLEAN. With the bare joiners in the set the two
    halves are separate commands, the second is unparsed, and its flag is a
    surplus occurrence the cross-check reports. Both halves of the fix are
    frozen here: the split itself, and the cross-check catching the merged case.
    """
    for sep in ("|", "&"):
        # The literal bypass: the first half is flagged, the second is not, and
        # the second's spelling is one only the cross-check can see.
        merged = (
            'git diff --no-renames --name-only "$BASE" HEAD '
            f'{sep} git diff --name-status "$BASE" HEAD\n'
        )
        assert len(changed_set_git_diff_commands(merged)) == 1, (sep, merged)
        assert changed_set_git_diff_offenders(merged) == [], (sep, merged)
        unparsed = unparsed_changed_set_diff_lines(merged)
        assert [lineno for lineno, _ in unparsed] == [1], (sep, unparsed)
        assert "--name-status" in unparsed[0][1], (sep, unparsed)

        # A flagless `--name-only` second half becomes its own OFFENDER.
        two_name_only = (
            'git diff --no-renames --name-only "$BASE" HEAD '
            f'{sep} git diff --name-only "$BASE" HEAD\n'
        )
        assert len(changed_set_git_diff_commands(two_name_only)) == 2, (
            sep,
            changed_set_git_diff_commands(two_name_only),
        )
        offenders = changed_set_git_diff_offenders(two_name_only)
        assert len(offenders) == 1, (sep, offenders)
        assert "--no-renames" not in offenders[0][1], (sep, offenders)


def test_changed_set_parser_ignores_comments():
    """A comment can neither satisfy the floor nor rescue an offender."""
    # A comment mentioning the flag is NOT a parsed command.
    assert changed_set_git_diff_commands(
        "# git diff --no-renames --name-only x\n") == []
    # A trailing comment carrying the flag must not rescue the real command.
    rescued = 'git diff --name-only "$BASE" HEAD  # --no-renames is written here\n'
    assert len(changed_set_git_diff_commands(rescued)) == 1
    assert len(changed_set_git_diff_offenders(rescued)) == 1
    # A comment mentioning a bare diff is likewise inert.
    assert changed_set_git_diff_commands("# git diff --name-only x\n") == []


def test_changed_set_parser_joins_backslash_continuations():
    """A multi-line invocation is ONE command, not a silent skip."""
    missing = 'git diff --name-only \\\n  "$BASE" HEAD\n'
    commands = changed_set_git_diff_commands(missing)
    assert len(commands) == 1, commands
    assert commands[0][0] == 1
    assert len(changed_set_git_diff_offenders(missing)) == 1
    ok = 'git diff --no-renames \\\n  --name-only "$BASE" HEAD\n'
    assert len(changed_set_git_diff_commands(ok)) == 1
    assert changed_set_git_diff_offenders(ok) == []


def test_changed_set_parser_accepts_git_config_and_substitution():
    """`git -c <cfg> diff` and a `$( )` prefix still invoke git diff."""
    text = 'git -c diff.renames=true diff --name-only "$BASE" HEAD\n'
    assert len(changed_set_git_diff_commands(text)) == 1
    assert len(changed_set_git_diff_offenders(text)) == 1
    subst = 'CHANGED=$(git diff --name-only "$BASE" HEAD)\n'
    assert len(changed_set_git_diff_commands(subst)) == 1
    assert len(changed_set_git_diff_offenders(subst)) == 1


def test_changed_set_parser_sees_quoted_command_substitution():
    """#4378 FIX 1: `CHANGED="$(git diff … )"` IS a parsed changed-set diff.

    The stripper used to discard a double-quoted span wholesale, so a changed-set
    diff written in the repo's own `="$( … )"` house style (29 uses today:
    `="$(printf …)"`, `="$(gh api …)"`, `="$(unzip …)"` …) was invisible to the
    pin in BOTH directions — never counted, never flagged. These inputs freeze
    both directions, for the one-command and the two-command (`||`) shapes.
    """
    # WITHOUT the flag: one command, and it MUST be an offender.
    flagless = (
        'CHANGED="$(git diff --name-only "$BASE...HEAD" 2>/dev/null || true)"\n'
    )
    commands = changed_set_git_diff_commands(flagless)
    assert len(commands) == 1, commands
    offenders = changed_set_git_diff_offenders(flagless)
    assert len(offenders) == 1, offenders
    assert offenders[0][0] == 1, offenders

    # WITH the flag: still counted, and NOT an offender.
    flagged = (
        'CHANGED="$(git diff --no-renames --name-only "$BASE...HEAD" '
        '2>/dev/null || true)"\n'
    )
    assert len(changed_set_git_diff_commands(flagged)) == 1
    assert changed_set_git_diff_offenders(flagged) == [], changed_set_git_diff_offenders(
        flagged
    )

    # The two-command `||` shape inside the quoted substitution: the flag on the
    # FIRST command must not rescue the flagless SECOND one.
    one_flagged = (
        'CHANGED="$(git diff --no-renames --name-only "$BASE...HEAD" '
        '|| git diff --name-only "$BASE" HEAD)"\n'
    )
    assert len(changed_set_git_diff_commands(one_flagged)) == 2
    offenders = changed_set_git_diff_offenders(one_flagged)
    assert len(offenders) == 1, offenders
    assert "--no-renames" not in offenders[0][1]

    both_flagged = (
        'CHANGED="$(git diff --no-renames --name-only "$BASE...HEAD" '
        '|| git diff --no-renames --name-only "$BASE" HEAD)"\n'
    )
    assert changed_set_git_diff_offenders(both_flagged) == []

    # A `$( … )` inside SINGLE quotes is a literal, not code.
    literal = "CMD='$(git diff --name-only \"$BASE\" HEAD)'\n"
    assert changed_set_git_diff_commands(literal) == []


def test_changed_set_parser_quoted_flag_cannot_rescue_a_site():
    """Unwrapping a quoted `$( … )` must not leak into ordinary quoted text.

    Only the CONTENT of `$( … )` is code; the content of a plain double-quoted
    string is not. A `--no-renames` that a command merely *mentions* — in a
    trailing comment, in a quoted string, or as a single-quoted literal — cannot
    turn a flagless changed-set diff green. Discarding the quoted token is the
    conservative direction: the pin fails closed, never open.
    """
    decoys = (
        'git diff --name-only "$BASE" HEAD  # add --no-renames\n',
        'git diff --name-only "$BASE" HEAD "--no-renames not passed"\n',
        "git diff --name-only \"$BASE\" HEAD '--no-renames'\n",
        # A substitution's inner flag belongs to the substitution, not to the
        # diff command it is an argument of — it must not rescue that diff.
        'git diff --name-only "$BASE" HEAD "$(true --no-renames)"\n',
    )
    for text in decoys:
        commands = changed_set_git_diff_commands(text)
        assert len(commands) == 1, (text, commands)
        offenders = changed_set_git_diff_offenders(text)
        assert len(offenders) == 1, (text, offenders)


def test_changed_set_parser_requires_flag_before_pathspec_separator():
    """#4378 FIX 1: `--no-renames` after the first `--` is ignored by git.

    `git diff` treats everything after `--` as a pathspec, so a flag written
    there is swallowed (rc=0, no warning, renames still detected). The old
    predicate was token membership only, so it accepted the flag wherever it sat.
    These inputs freeze the positional rule: before `--` clean, after `--` an
    offender, and one on each side still an offender (the trailing one governs
    nothing, so git's effective flag is not what the pin claims).
    """
    after = 'git diff --name-only "$BASE" HEAD -- \'*.md\' --no-renames\n'
    commands = changed_set_git_diff_commands(after)
    assert len(commands) == 1, commands
    offenders = changed_set_git_diff_offenders(after)
    assert len(offenders) == 1, offenders
    assert "--no-renames" in offenders[0][1]

    before = 'git diff --no-renames --name-only "$BASE" HEAD -- \'*.md\'\n'
    assert len(changed_set_git_diff_commands(before)) == 1
    assert changed_set_git_diff_offenders(before) == []

    both = 'git diff --no-renames --name-only "$BASE" HEAD -- \'*.md\' --no-renames\n'
    assert len(changed_set_git_diff_offenders(both)) == 1, (
        changed_set_git_diff_offenders(both)
    )


def test_changed_set_parser_cross_check_fails_closed_on_unparsed_spelling():
    """#4378 FIX 2: a changed-set diff the parser does not recognise FAILS.

    Each review cycle closed one spelling at a time; this closes the CLASS. A
    synthetic workflow body carries an unrecognised `--name-status`, an
    unrecognised `--diff-filter`, a comment (inert), and a recognised
    `--name-only` site (covered) — only the first two are reported.
    """
    synthetic = (
        "steps:\n"
        "  - run: |\n"
        "      # a comment mentioning git diff --name-only is inert\n"
        '      git diff --name-status "$BASE" HEAD\n'
        '      CHANGED=$(git diff --diff-filter=ACMR "$BASE" HEAD)\n'
        '      FILES=$(git diff --name-only "$BASE" HEAD)\n'
    )
    unparsed = unparsed_changed_set_diff_lines(synthetic)
    assert [lineno for lineno, _ in unparsed] == [4, 5], unparsed
    # The recognised site is still parsed, so the net suppresses it (no false
    # positive) rather than reporting a line the pin already checked.
    assert changed_set_git_diff_commands(synthetic), (
        "the recognised --name-only site must still parse"
    )
    assert not any(lineno == 6 for lineno, _ in unparsed), unparsed

    # A quoted or `eval`-wrapped spelling the parser cannot unwrap is caught too.
    quoted = 'eval "git diff --name-only \\"$BASE\\" HEAD"\n'
    assert unparsed_changed_set_diff_lines(quoted), quoted
    # `--no-index --quiet` content comparisons carry no changed-set flag: their
    # existence must not trip the net (ci-timing.yml has no --name-only at all).
    no_index = (
        "if git diff --no-index --quiet docs/a generated/a \\\n"
        "   && git diff --no-index --quiet docs/b generated/b; then\n"
    )
    assert unparsed_changed_set_diff_lines(no_index) == []


def test_changed_set_parser_cross_check_fails_closed_on_colocation():
    """#4378 FIX 1: an unparsed changed-set diff cannot hide beside a parsed one.

    `unparsed_changed_set_diff_lines` used to mark a whole line "covered" as soon
    as ONE parsed command started on it, so this shape — two changed-set diffs on
    one line, with the second written in a spelling the
    parser does not recognise (`--name-status`, no flag) — passed the pin clean
    (`offenders=[]`, `unparsed=[]`) while the second command computed a changed set
    with rename detection ON. The cross-check must count the flag spellings, not
    the lines, so the surplus is reported.
    """
    colocated = (
        'CHANGED=$(git diff --no-renames --name-only "$BASE...HEAD" 2>/dev/null '
        '|| git diff --name-status "$BASE" HEAD)\n'
    )
    # The FIRST command is parsed and correctly flagged, so the offender rule is
    # silent — the cross-check is the ONLY thing that can catch the second one.
    assert len(changed_set_git_diff_commands(colocated)) == 1, colocated
    assert changed_set_git_diff_offenders(colocated) == [], colocated
    unparsed = unparsed_changed_set_diff_lines(colocated)
    assert [lineno for lineno, _ in unparsed] == [1], unparsed
    assert "--name-status" in unparsed[0][1], unparsed

    # The SAME spelling hidden by the parser's quote stripping (`eval "…"`) beside
    # a parsed `--name-only` is caught too: the raw span counts two `--name-only`,
    # the parser accounts for one. A set-based rule would suppress this; counting
    # occurrences is what makes it fail closed.
    eval_colocated = (
        'git diff --no-renames --name-only "$BASE" HEAD; '
        'eval "git diff --name-only \\"$BASE\\" HEAD"\n'
    )
    assert len(changed_set_git_diff_commands(eval_colocated)) == 1, eval_colocated
    unparsed = unparsed_changed_set_diff_lines(eval_colocated)
    assert [lineno for lineno, _ in unparsed] == [1], unparsed

    # Neither half recognised: still reported.
    both_unrecognised = (
        'CHANGED=$(git diff --name-status "$BASE...HEAD" '
        '|| git diff --name-status "$BASE" HEAD)\n'
    )
    assert unparsed_changed_set_diff_lines(both_unrecognised), both_unrecognised

    # Control: BOTH halves parsed and flagged is clean — no false positive.
    both_parsed = (
        'CHANGED=$(git diff --no-renames --name-only "$BASE...HEAD" 2>/dev/null '
        '|| git diff --no-renames --name-only "$BASE" HEAD)\n'
    )
    assert unparsed_changed_set_diff_lines(both_parsed) == [], (
        unparsed_changed_set_diff_lines(both_parsed)
    )


def test_changed_set_parser_cross_check_sees_continuation_split_spelling():
    """#4378 FIX 2: `git`/`diff` and the flag on different physical lines of
    ONE backslash-continued command still fail.

    A `--name-status` continuation is invisible to a purely per-physical-line
    check (the first line carries no flag; the flag line carries no `git`), so the
    net joins a backslash continuation before looking for the flag — the same join
    the parser already performs. Freeze it so the join cannot be dropped.
    """
    continued = 'git diff \\\n  --name-status "$BASE" HEAD\n'
    unparsed = unparsed_changed_set_diff_lines(continued)
    assert [lineno for lineno, _ in unparsed] == [1], unparsed
    # The report is attributed to the line the logical span STARTED on, and the
    # flag lives on the continuation line — proving the join happened.
    assert "--name-status" in continued.splitlines()[1], continued
    # The recognised, correctly-flagged continuation is still clean.
    ok = 'git diff --no-renames \\\n  --name-only "$BASE" HEAD\n'
    assert unparsed_changed_set_diff_lines(ok) == [], (
        unparsed_changed_set_diff_lines(ok)
    )


def test_changed_set_parser_cross_check_reports_unrecognised_spelling_in_every_shape():
    """#4378 FIX 3: every declared in-scope spelling of an UNRECOGNISED changed-set
    diff is reported by the cross-check.

    The parser recognises only `--name-only`; `--name-status`/`--diff-filter` are
    caught as unrecognised whatever shell shape they are written in — bare, inside
    `$( … )`, inside a double-quoted `"$( … )"`, joined by `||`/`&&`/`;`/`|`/`&`,
    split across a backslash continuation, or prefixed by `git -c <cfg>`. Each
    entry is a shape whose mutation (e.g. dropping the `$(` unwrap, a per-line
    check, dropping a bare joiner) would let it pass silently, so this is the
    coverage table's executable form.
    """
    shapes = {
        "bare": 'git diff --name-status "$BASE" HEAD',
        "substitution": 'CHANGED=$(git diff --diff-filter=ACMR "$BASE" HEAD)',
        "quoted substitution": (
            'CHANGED="$(git diff --name-status "$BASE" HEAD)"'
        ),
        "or": 'true || git diff --name-status "$BASE" HEAD',
        "and": 'true && git diff --diff-filter=ACMR "$BASE" HEAD',
        "semicolon": 'true ; git diff --name-status "$BASE" HEAD',
        "pipe": 'true | git diff --name-status "$BASE" HEAD',
        "background": 'true & git diff --diff-filter=ACMR "$BASE" HEAD',
        "git -c": 'git -c diff.renames=true diff --name-status "$BASE" HEAD',
        "backslash continuation": 'git diff \\\n  --name-status "$BASE" HEAD',
    }
    for label, text in shapes.items():
        unparsed = unparsed_changed_set_diff_lines(text + "\n")
        assert unparsed, (label, text)
        assert changed_set_git_diff_commands(text) == [], (
            label,
            "an unrecognised spelling must not be a parsed command",
        )


def test_every_changed_set_diff_disables_rename_detection():
    """#4378: every changed-set selection diff must pass `--no-renames`.

    Rename detection is on by default (`diff.renames` is unset -> true), so for a
    rename git emits only the DESTINATION. The selector is handed a path set that
    never mentions the file moved away, and a guard registered against the old
    path silently does not run — the "silent in exactly the case it exists to
    cover" class this lane exists to close.

    Demonstrated on a real rename in this repo's history (371eec29f, which moved
    tests out of tortoise/shared_state/tests/ precisely so they would be collected):

        git diff --name-only 371eec29f^ 371eec29f | grep shared_state
          -> the 6 destinations under tests/ only
        git diff --no-renames --name-only 371eec29f^ 371eec29f | grep shared_state
          -> those 6 AND the 6 sources under tortoise/shared_state/tests/

    Measured cost of the flag: over main's last 200 commits, 2 commits contain any
    rename and 0 are pure moves, so the "a no-op move now selects both surfaces"
    objection does not occur in practice. Adopted on #4378.

    Scope and coverage (read before editing this test):

    * The scan reads EVERY workflow file in `.github/workflows/` — both `.yml` and
      `.yaml` — not a list of the known sites, so a new changed-set computation
      added later cannot reintroduce the hole unnoticed. The `.yaml` half is
      frozen by `test_every_changed_set_diff_scan_covers_yaml_workflows` (the repo has no
      `.yaml` workflow, so without that test the glob was unfrozen). It claims
      NOTHING about files outside `.github/workflows/`.
    * It is per COMMAND, not per line (`iter_shell_commands`): `||`/`&&`/`;`/`|`/`&`
      split a line into commands, a backslash-continued invocation is joined first,
      and comments and ordinary quoted text are dropped. `python-ci.yml`'s "Tiered
      selection" step carried TWO diffs on one line, so a line-level check would
      let the second lose the flag undetected — the regression the parser tests
      above freeze. (#3442 collapsed that step to one merge-base diff; the
      parser tests keep the two-command case frozen synthetically.) The bare `|`/`&` joiners are load-bearing, not decorative: with
      only the doubled forms in the separator set,
      `git diff --no-renames --name-only A | git diff --name-status B` was ONE
      merged command whose token list carried both spellings, so the ownership
      count balanced and the flagless `--name-status` read clean
      (test_changed_set_parser_separates_bare_pipe_and_amp_merge).
    * A `$( … )` command substitution is NOT ordinary quoted text: its contents
      are unwrapped and parsed even inside double quotes, so the repo's own
      `CHANGED="$(git diff … )"` house style is counted and checked exactly like
      the unquoted `CHANGED=$( … )` form. A backtick substitution is parsed too
      (`_shell_tokens` treats the backtick as shell whitespace), so both
      substitution spellings are checked — not merely the `$( … )` one.
    * It FAILS CLOSED on an unrecognised spelling (`unparsed_changed_set_diff_lines`):
      a spelling the parser does not understand must not pass silently. Any
      comment-stripped logical line (backslash continuations joined) that contains
      `git` AND `diff` AND a `--name-only`/`--name-status`/`--diff-filter`
      spelling the parsed commands do not ACCOUNT FOR fails the pin with a message
      to add `--no-renames` or teach the parser the spelling. The net COUNTS flag
      occurrences instead of marking a line "covered", so an unparsed spelling
      CO-LOCATED with a parsed one is still reported — a line carrying two diffs
      is no longer rescued by the parse of its neighbour. The net is deliberately
      COARSER than the parser — it matches substrings on the comment-stripped raw
      line, not the parser's separator/quote output, and it spans a backslash
      continuation — so a spelling the parser mis-splits is still caught. It can
      only SUPPRESS a report for a spelling the parser genuinely accounted for; it
      never rescues an offender. Because it matches raw substrings, a line that
      merely QUOTES a changed-set diff (`echo "git diff --name-only"`) would
      over-report rather than slip through; that is the intended fail-closed
      direction, and no such line exists today.
    * THREAT SURFACE — the declared edge of this guard. This test is a STATIC
      SCANNER over workflow text, NOT a shell interpreter: it reads each command
      as WRITTEN and applies the rules below. The in-scope list IS the declared
      surface. A fresh reviewer that reproduces no in-scope bypass and confirms
      every in-scope class has a test terminates the review — do not chase an
      ever-more-contrived spelling outside the declared surface.

      IN SCOPE — the pin must FAIL for each of these (test named in brackets):

      - `git diff … --name-only` — the parser-recognised changed-set spelling —
        written in ANY of these shapes, when `--no-renames` is MISSING or sits
        after the first `--` (git swallows it as a pathspec; rc=0, no warning):
          · bare (test_changed_set_parser_splits_on_every_documented_separator);
          · inside `$( … )`
            (test_changed_set_parser_accepts_git_config_and_substitution);
          · inside a double-quoted `"$( … )"`
            (test_changed_set_parser_sees_quoted_command_substitution);
          · joined by `||`, `&&`, `;`, `|` or `&`
            (test_changed_set_parser_reads_commands_not_lines,
            test_changed_set_parser_splits_on_every_documented_separator,
            test_changed_set_parser_separates_bare_pipe_and_amp_merge);
          · split across a backslash continuation
            (test_changed_set_parser_joins_backslash_continuations);
          · prefixed by `git -c <cfg>`
            (test_changed_set_parser_accepts_git_config_and_substitution).
      - `--no-renames` after the first `--`
        (test_changed_set_parser_requires_flag_before_pathspec_separator).
      - `--name-status` / `--diff-filter`, and any other spelling the parser
        cannot tokenise, whenever `git` + `diff` + the flag appear on ONE
        comment-stripped logical line — bare, in a substitution, in a quoted
        substitution, `||`/`&&`/`;`/`|`/`&`-joined, `git -c`-prefixed, or split
        across a backslash continuation
        (test_changed_set_parser_cross_check_fails_closed_on_unparsed_spelling,
        test_changed_set_parser_cross_check_reports_unrecognised_spelling_in_every_shape,
        test_changed_set_parser_cross_check_sees_continuation_split_spelling).
      - CO-LOCATION: a logical line whose raw text carries a changed-set flag
        spelling MORE times than its parsed commands account for is reported, not
        covered. This states the mechanism exactly — the net counts flag
        OCCURRENCES per logical span (`raw_span.count(flag) > owned[flag]`, a
        count, not a covered/not-covered set) — so an unparsed changed-set
        spelling sharing a logical line with a parsed one is reported whenever the
        splitter keeps them as separate commands. Every shell joiner does: `||`,
        `&&`, `;`, `|` and `&` split, and a newline ends a logical line by
        construction, so a second diff on the next line is a span of its own (a
        `for`-loop body, a subshell, a `case` arm and a backtick substitution are
        each reported by the count; a newline inside a quoted string is scanned as
        two physical lines and consequently OVER-reports rather than missing).
        (test_changed_set_parser_cross_check_fails_closed_on_colocation,
        test_changed_set_parser_separates_bare_pipe_and_amp_merge).

      OUT OF SCOPE — declared, with the reason; a bypass here is a known,
      accepted gap, not a defect:

      - the command name reached DYNAMICALLY: `$GIT diff`, a shell alias, or any
        other wrapper whose raw text carries no literal `git` substring — a text
        scanner does not follow a name bound at runtime. (`/usr/bin/git diff` IS
        caught, because `/usr/bin/git` carries the `git` substring; that is the
        fail-closed direction, not a gap.)
      - fragments of one invocation split across SEPARATE statements or lines (the
        flag on one line, the command on another), or a flag assembled from
        string pieces — no single logical line then carries all three substrings
        (`git`, `diff`, the flag);
      - two changed-set spellings inside ONE parsed command with NO shell joiner
        between them (e.g. two diffs separated only by whitespace): the command's
        own token list carries both, so the raw occurrence count has no surplus
        and the net reports nothing. That shape is not two invocations — there is
        no joiner — and no site writes it. It is the ONLY same-logical-line
        co-location the count does not catch; every real joiner is handled as
        described in the CO-LOCATION entry above;
      - files outside `.github/workflows/`: the pin scans that directory only. The
        two known changed-set sites elsewhere — the `.husky/pre-commit` hook and
        the eval-drift gate — are filed as follow-ups, not covered here.
    * The non-vacuity floor (`checked >= 3`) is a FLOOR, not a pin of exactly
      three. It is counted from the PARSED commands above — measured today as
      FOUR: ONE on `python-ci.yml`'s "Tiered selection" step (#3442 collapsed its
      two `||`-joined diffs into one canonical merge-base diff), one on `ci.yml`'s
      "Compute per-surface path gates (#2149)" step, and TWO on `ci.yml`'s two
      "Get changed markdown files" steps (the PR path and the main-health path).
      So a comment cannot satisfy it (and a comment mentioning the flag cannot
      inflate it). The floor is 3 so removing one command does not require a
      floor change; it has to come down to 2 only if a SECOND command is removed,
      once just two remain.
    * The PR-path command — the "Get changed markdown files" step in `ci.yml`
      (cited by step name, not line number, because line numbers drift) — is no
      longer inert. #2386: it WAS inert because the `docs` job checked out at
      depth 1, so `github.event.pull_request.base.sha` was absent from the object
      store, the three-dot diff died with `fatal: Invalid symmetric difference
      expression`, the tolerance swallowed it, the changed list came out empty
      and the consuming markdownlint/lychee steps were skipped (job
      100109903325). The job now checks out at full depth (`fetch-depth: 0`) and
      the step carries no tolerance on the diff, so the diff resolves against a
      real base and a bad base fails the step. All FOUR parsed commands are live.
    * The same step's `--no-renames` is still deliberate and the rule applies to
      it uniformly: it IS a changed-set computation, and `--diff-filter ACMR`
      keeps the list to paths that exist on disk, so a deleted or renamed-away
      `.md` source never reaches markdownlint or lychee.
    * Do NOT generalise this rule to `.github/scripts/check-migration-append-only`.
      That script deliberately runs `git diff --find-renames=20% ... --name-status`
      (recorded at `docs/plans/2026-08-13-1095-migration-drift-gate.md:148`).
      Its exempt arm IS keyed on the `R*` status: #2240 scoped the exemption to a
      git-detected forward prefix rename whose destination version sorts strictly
      AFTER the newest applied version, with BOTH endpoints absent from
      `supabase_migrations.schema_migrations`. A bare `M`/`D` path carries no
      destination version, so it has no content-independent ordering bound and is
      NEVER admitted — it falls through to KEEP and is reported as a violation.
      The old justification ("`--no-renames` would break the gate") therefore
      holds again: `--find-renames=20%` is what lets a HIGH-SIMILARITY forward
      renumber report as one `R<sim>` line and reach the exempt arm at all; a
      re-land carrying a real content delta still degrades to `D`+`A` even with
      the flag set, and that form is now reported rather than exempted.
      The rule in this pin is scoped to changed-set
      *selection* diffs; that file is a deliberate exception.
    """
    root = Path(__file__).resolve().parents[1]
    wf_dir = root / ".github" / "workflows"
    workflows = _workflow_files(wf_dir)
    assert workflows, "no workflow files found — the scan would pass vacuously"

    checked, offenders, unparsed = _scan_changed_set_diffs(workflows)

    assert checked >= 3, (
        f"expected the non-vacuity floor of 3 changed-set diff commands across "
        f".github/workflows/ (a floor, not a pin), found {checked} — either the "
        f"parser stopped recognising the sites, or a site was removed without "
        f"lowering the floor"
    )
    assert not offenders, (
        "a changed-set `git diff --name-only` without --no-renames drops the source "
        "path of a rename, so the moved file's guard never runs (#4378):\n  "
        + "\n  ".join(offenders)
    )
    assert not unparsed, (
        "a changed-set `git diff` in .github/workflows/ was not recognised by the "
        "parser, or its flag could not be accounted for beside another diff on "
        "the same line, so the --no-renames rule could not be enforced on it. "
        "Add --no-renames if it is a changed-set selection diff, or teach "
        "changed_set_git_diff_commands the new spelling (#4378):\n  "
        + "\n  ".join(unparsed)
    )


# ── #3442: the changed-set derivation must be ONE canonical question ──────
# The `changes` job is a required, always-runs hub, so its selection must be a
# deterministic function of the diff it claims to measure. Two ways it could
# silently stop being one, both frozen below: (1) the old
# `git diff "$BASE...HEAD" 2>/dev/null || git diff "$BASE" HEAD` chain answered a
# DIFFERENT question (the two-ref form folds in the base branch's own advances,
# and an unrelated history with no merge base) whenever the first form failed,
# and (2) a swallowed failure left `CHANGED` empty, which `select()` maps to the
# tier-1 smoke set — a stripped suite that still reports green.


def _tiered_selection_run_text() -> str:
    """The `run` script of python-ci.yml's `changes` → `Tiered selection` step."""
    wf = _load_python_ci()
    for step in wf["jobs"]["changes"]["steps"]:
        if step.get("id") == "select":
            return step["run"]
    raise AssertionError("python-ci.yml has no `changes` step with id `select`")


def test_tiered_selection_asks_one_canonical_merge_base_question():
    """#3442: the changed set comes from ONE loud merge-base diff.

    The old chain silently answered a different question on failure and rendered
    the fallback reachable only when it would produce a *different* answer, so
    "the same diff" could select different tests. The pin is on the workflow
    text, because the defect is the shell shape, not the selector (whose own
    determinism is frozen by the hashseed test below).
    """
    run_text = _tiered_selection_run_text()
    commands = changed_set_git_diff_commands(run_text)
    assert len(commands) == 1, (
        "the `changes` job must compute the changed set with exactly ONE `git "
        f"diff` (the merge-base question) — #3442. Found {commands}"
    )
    diff_lines = [ln for ln in run_text.splitlines() if "git diff" in ln]
    assert len(diff_lines) == 1, (
        f"exactly ONE `git diff` line may compute the changed set — #3442. {diff_lines}"
    )
    diff_line = diff_lines[0]
    assert '"$BASE...HEAD"' in diff_line, (
        "the single changed-set diff must ask the MERGE-BASE question "
        f"(`\"$BASE...HEAD\"`) — #3442. Got: {diff_line}"
    )
    assert '"$BASE" HEAD' not in diff_line, (
        "the two-ref `git diff \"$BASE\" HEAD` form is a DIFFERENT question "
        "(it folds in the base branch's own advances) and must not be reachable "
        f"— #3442. Got: {diff_line}"
    )
    assert "2>/dev/null" not in diff_line, (
        "a swallowed diff failure is how the empty changed set silently degraded "
        f"to the tier-1 smoke set — #3442. Got: {diff_line}"
    )
    assert "||" not in diff_line, (
        f"no silent fallback to a different diff question — #3442. Got: {diff_line}"
    )


def test_tiered_selection_fails_loudly_on_a_diff_it_cannot_compute():
    """#3442: an absent base, or an empty changed set, REDs instead of degrading.

    `select([])` is `full=false, slow_run=false, carve_out_run=false` — the
    ~31-file tier-1 smoke set. That is a legitimate answer for a genuinely
    docs-only diff and a catastrophic one for a diff that failed to compute, and
    nothing downstream can tell them apart. Both guards must `exit 1`.
    """
    run_text = _tiered_selection_run_text()
    assert 'git rev-parse --verify --quiet "$BASE^{commit}"' in run_text, (
        "the base commit must be proven present before the diff is taken — #3442"
    )
    assert '-z "$CHANGED"' in run_text, (
        "an empty changed set must be rejected, not handed to select() — #3442"
    )
    assert run_text.count("exit 1") >= 2, (
        "both guards (absent base, empty changed set) must fail the job — #3442"
    )
    lowered = run_text.lower()
    assert "::error::" in run_text and "refusing" in lowered, (
        "each guard must name its refusal as an ::error:: so the red is legible "
        "— #3442"
    )
    # The guessable-but-wrong alternative: substituting origin/main for an
    # absent base SHA would change WHICH diff is measured while still exiting 0.
    # `origin/main` must therefore appear ONLY in the documented fallback for an
    # EMPTY base ref, never inside the absent-commit guard.
    assert run_text.count("origin/main") == 1, (
        "an absent base must fail, not be swapped for origin/main (a different "
        "diff) — #3442"
    )


def _run_selector(args: list[str], stdin: str, hashseed: str, artifact_dir: Path):
    import os
    env = dict(os.environ)
    env["PYTHONHASHSEED"] = hashseed
    env["CI_SELECTION_ARTIFACT_DIR"] = str(artifact_dir)
    return subprocess.run(
        [sys.executable, str(Path(__file__).resolve().parents[1] / "tools" / "ci_selection.py"), *args],
        input=stdin, capture_output=True, text=True, env=env, cwd=str(artifact_dir),
        check=True,
    ).stdout


def test_changes_job_selection_is_hashseed_deterministic(tmp_path):
    """#3442 O/ I/T: the same diff selects the same tests on every run.

    `set` iteration order is randomised per interpreter (PYTHONHASHSEED), so a
    selection (or a `--split` half) derived from an unsorted set would differ
    between two runs of the SAME diff. This runs the three commands the `changes`
    job runs — select, the push matrix, and the tier-2 split — under two hash
    seeds and requires byte-identical output.
    """
    changed = "\n".join([
        "tortoise/ep/engine.py",
        "tortoise/connectors/falkor.py",
    ]) + "\n"
    for seed in ("1", "4242"):
        (tmp_path / seed).mkdir()
    first = _run_selector(["--changed-files", "-", "--event", "pull_request"],
                          changed, "1", tmp_path / "1")
    second = _run_selector(["--changed-files", "-", "--event", "pull_request"],
                           changed, "4242", tmp_path / "4242")
    assert first == second, "select() output depends on PYTHONHASHSEED — #3442"

    matrix_a = _run_selector(["--emit-push-matrix"], "", "1", tmp_path / "1")
    matrix_b = _run_selector(["--emit-push-matrix"], "", "4242", tmp_path / "4242")
    assert matrix_a == matrix_b, "--emit-push-matrix depends on PYTHONHASHSEED — #3442"

    selected = json.loads(first)["test_files"]
    assert isinstance(selected, list) and selected, selected
    split_a = _run_selector(["--split"], json.dumps(selected), "1", tmp_path / "1")
    split_b = _run_selector(["--split"], json.dumps(selected), "4242", tmp_path / "4242")
    assert split_a == split_b, "--split depends on PYTHONHASHSEED — #3442"


# ── #4740 review 4: the orphan-assert steps' fail-closed pgrep probe ───────
# Each of the three `Assert no redislite orphans` steps in python-ci.yml is
# the newest fail-closed control on the orphan count, and NO other test can
# see it: `orphan-bound.test.sh` reads only the gate script, and the gate
# receives an already-computed `--count`. A mutation (`-le 1` → `-lt 1`, or a
# revert to `COUNT=$(pgrep … | wc -l)`) would be undetectable. This pin reads
# the workflow text and requires, per step, that pgrep's OWN status is
# captured and the count is never read from a `pgrep | …` pipeline (whose
# status is the last command's — `tr`, always 0 — so a failed probe would
# read as a measured 0 and pass).


def _orphan_assert_steps() -> list[dict]:
    wf = _load_python_ci()
    steps = [
        s
        for job in wf["jobs"].values()
        for s in (job.get("steps") or [])
        if str(s.get("name", "")).startswith("Assert no redislite orphans")
    ]
    return steps


def test_orphan_assert_steps_capture_pgrep_status_fail_closed():
    """#4740 review 4: pgrep's own status must gate the orphan count."""
    steps = _orphan_assert_steps()
    assert len(steps) == 3, (
        f"expected the three 'Assert no redislite orphans' steps, found "
        f"{len(steps)} — this pin must not pass vacuously"
    )
    for s in steps:
        # Drop whole-line comments: they QUOTE the rejected pipeline form
        # (`COUNT=$(pgrep … | wc -l)`) and the `${PIPESTATUS[0]}` rationale, so
        # scanning raw text would flag the documentation rather than the code.
        body = "\n".join(
            line
            for line in s["run"].splitlines()
            if not line.lstrip().startswith("#")
        )
        assert "PIPESTATUS" not in body, (
            "a ${PIPESTATUS[0]} read after `COUNT=$(pgrep … | wc -l)` is the "
            "`tr` status, never pgrep's, so the guard would be inert (#4740)"
        )
        assert re.search(r"\$\(pgrep\b[^)]*\|", body) is None, (
            "COUNT must not be read from a `pgrep | …` pipeline: its status is "
            "the last command's, so a failed probe reads as a measured 0 and "
            "passes (the #4740 fail-open)"
        )
        assert 'PIDS=$(pgrep -f "redislite/bin/redis-server")' in body, (
            "pgrep must run on a bare assignment so `$?` is its own status"
        )
        assert "prc=$?" in body, "pgrep's own status must be captured"
        assert '[ "$prc" -le 1 ]' in body, (
            "rc 0 (matches) and rc 1 (none) are real measurements; anything "
            "else must fail closed (#4740)"
        )
        assert "exit 1" in body, "the failed-probe guard must exit non-zero"
        assert 'COUNT=$(printf \'%s\' "$PIDS" | wc -w | tr -d \' \')' in body, (
            "COUNT must be derived from the pgrep output `$PIDS`, not a "
            "constant — a `COUNT=0` would hand the gate a measured-zero that "
            "no later leak could ever exceed (#4740 review 10)"
        )
        assert '--count "$COUNT"' in body, (
            "the orphan gate must consume the derived COUNT (#4740 review 10)"
        )


def test_orphan_assert_steps_hand_the_gate_the_counted_argv():
    """#1005 (epic #1647 E2E-7): the count is HOST-WIDE; the argv is what makes
    a server attributable to THIS run.

    `--count` alone cannot tell another job's server (or a sibling `pytest -n`
    worker's) from this run's, and redding that difference failed PASSING runs
    (9 counted vs left=8, pytest rc 0). The gate can only make the distinction
    if the workflow hands it the counted servers' argv, so each step must
    produce that file from `$PIDS` and pass it — and a `ps` failure must be a
    FAILED probe, never an empty file that silently reads as "all foreign".
    """
    steps = _orphan_assert_steps()
    assert len(steps) == 3, (
        f"expected the three 'Assert no redislite orphans' steps, found "
        f"{len(steps)} — this pin must not pass vacuously"
    )
    for s in steps:
        body = "\n".join(
            line
            for line in s["run"].splitlines()
            if not line.lstrip().startswith("#")
        )
        assert ': > "$ARGV"' in body, (
            f"step {s.get('name')!r} must truncate the argv file before the "
            f"probe, so a stale file from an earlier attempt cannot attribute "
            f"this run's count (#1005)"
        )
        assert 'ps -ww -o pid=,args= -p "$PIDLIST"' in body, (
            f"step {s.get('name')!r} must read the counted servers' argv with "
            f"`ps -ww -o pid=,args= -p` (#1005)"
        )
        assert "tr -s '[:space:]' ','" in body, (
            f"step {s.get('name')!r} must normalise pgrep's newline-separated "
            f"pids to a comma list: GNU `ps -p` REJECTS a newline list with "
            f"'process ID list syntax error' (measured on the self-hosted "
            f"Linux cells) while BSD `ps` tolerates it, so without this the "
            f"Linux legs write an EMPTY argv file and every server reads as "
            f"unattributable (#1005)"
        )
        assert '[ "$arc" -le 1 ]' in body and "ps failed" in body, (
            f"step {s.get('name')!r} must fail closed on a `ps` error: an "
            f"empty argv would attribute every counted server to another "
            f"process and hide this run's own residue (#1005)"
        )
        # #10255 review 1 (P1): `ps -p <list>` exits 1 when NO listed pid
        # matched (measured on procps-ng 4.0.4: `ps -p 1` → 0, `ps -p 999999`
        # → 1, `ps -p 1,999999` → 0), and the procps man page publishes no
        # exit-status contract. `-eq 0` therefore encoded an assumption the
        # tool never made and failed the job on a benign shutdown race — the
        # very defect class this PR removes, one layer up. Pin the polarity so
        # a re-tightening to `-eq 0` (or to any wider `-le N`) reds here:
        # rc>1 is a probe failure, rc 0/1 are completed measurements.
        assert '[ "$arc" -eq 0 ]' not in body, (
            f"step {s.get('name')!r} must NOT require `ps` to exit 0: rc 1 is a "
            f"COMPLETED measurement of an empty population (every counted "
            f"server exited in the shutdown window), so `-eq 0` fails a green "
            f"run (#10255 review 1)"
        )
        assert re.search(r'\[\s*"\$arc"\s*-le\s+[2-9]', body) is None, (
            f"step {s.get('name')!r} must treat only rc>1 as a probe failure — "
            f"a wider bound would read a real `ps` failure as an empty "
            f"attribution (#10255 review 1)"
        )
        assert '--argv "$ARGV"' in body, (
            f"step {s.get('name')!r} must pass --argv to the gate — the gate "
            f"warns rather than reds when the attribution is unavailable, so a "
            f"dropped flag would silently retire the leak check (#1005)"
        )


def _fake_ps_probe_dir(tmp: Path) -> Path:
    """A PATH dir whose `pgrep`/`ps` answer from the environment.

    The orphan step's verdict on a `ps` exit status cannot be pinned by reading
    its text: what matters is that the JOB does not fail. So the real step body
    is executed against shims — `pgrep` prints ``$FAKE_PIDS``, `ps` prints
    ``$FAKE_PS_OUT`` and exits ``$FAKE_PS_RC`` — which is how the benign
    shutdown race is reproduced deterministically.
    """
    shims = tmp / "shims"
    shims.mkdir(parents=True, exist_ok=True)
    (shims / "pgrep").write_text('#!/bin/sh\nprintf "%s" "$FAKE_PIDS"\n')
    (shims / "ps").write_text(
        '#!/bin/sh\nprintf "%s" "$FAKE_PS_OUT"\nexit "$FAKE_PS_RC"\n')
    for name in ("pgrep", "ps"):
        (shims / name).chmod(0o755)
    return shims


def _run_orphan_step(body: str, tmp: Path, env_extra: dict) -> \
        subprocess.CompletedProcess:
    """Run a real 'Assert no redislite orphans' step body with the shims."""
    env = dict(os.environ)
    env["PATH"] = f"{tmp / 'shims'}:{env.get('PATH', '')}"
    env["RUNNER_TEMP"] = str(tmp)
    env.update(env_extra)
    return subprocess.run(
        ["bash", "-c", body], cwd=str(REPO), env=env,
        capture_output=True, text=True, timeout=120)


def _orphan_step_fixtures(tmp: Path) -> None:
    """The two files the step reads: pytest's rc and the sweep's report."""
    (tmp / "pytest-rc").write_text("0")
    (tmp / "redislite-hygiene-end.json").write_text(json.dumps({
        "own_roots": ["/tmp/tt_fixture"],
        "sweep": {"reaped": 0, "exited": 0, "cleared": True, "left": 0,
                  "before": 0, "own_left": 0},
    }))


def test_orphan_step_survives_ps_exit_1_on_an_empty_match():
    """#10255 review 1 (P1): a benign `ps` race must not fail the job.

    `ps -p <list>` exits **1** when no listed pid matched (measured on
    procps-ng 4.0.4; the procps man page documents no exit-status contract).
    The count and this read sit inside the residue's shutdown window, which the
    gate's own header documents as normal, so at COUNT 1-2 every counted server
    exiting in between is ordinary — the pre-fix `[ "$arc" -eq 0 ]` failed the
    step there, on a run whose pytest passed and which leaked nothing.

    Non-vacuity: with `-eq 0` restored this step exits 1 with
    `::error::ps failed rc=1`.
    """
    steps = _orphan_assert_steps()
    assert len(steps) == 3, f"expected 3 orphan steps, found {len(steps)}"
    for s in steps:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            _fake_ps_probe_dir(tmp)
            _orphan_step_fixtures(tmp)
            done = _run_orphan_step(s["run"], tmp, {
                "FAKE_PIDS": "4242\n", "FAKE_PS_RC": "1", "FAKE_PS_OUT": ""})
        assert "::error::ps failed" not in done.stdout, (
            f"step {s.get('name')!r} failed the job on `ps` rc 1 — a "
            f"COMPLETED measurement of an empty population, not a failed probe "
            f"(#10255 review 1): {done.stdout}{done.stderr}"
        )
        assert done.returncode == 0, (
            f"step {s.get('name')!r} must pass when the counted servers exited "
            f"before the later probe (rc={done.returncode}): "
            f"{done.stdout}{done.stderr}"
        )


def test_orphan_step_still_fails_closed_on_a_real_ps_error():
    """#10255 review 1, the other half: rc>1 is still a FAILED probe.

    Relaxing rc 1 must not relax the fail-closed direction — an unreadable
    attribution would read every counted server as another process's and hide
    this run's own residue (#1005), so rc 2 (and a missing/timed-out `ps`, \
    which is not 0/1 either) must still red.
    """
    steps = _orphan_assert_steps()
    assert len(steps) == 3, f"expected 3 orphan steps, found {len(steps)}"
    for s in steps:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            _fake_ps_probe_dir(tmp)
            _orphan_step_fixtures(tmp)
            done = _run_orphan_step(s["run"], tmp, {
                "FAKE_PIDS": "4242\n", "FAKE_PS_RC": "2", "FAKE_PS_OUT": ""})
        assert done.returncode == 1, (
            f"step {s.get('name')!r} must exit 1 on `ps` rc 2 — a real probe "
            f"failure is not an empty measurement (rc={done.returncode}): "
            f"{done.stdout}{done.stderr}"
        )
        assert "::error::ps failed rc=2" in done.stdout, (
            f"step {s.get('name')!r} must name the failed probe: {done.stdout}"
        )


def test_orphan_assert_no_pytest_producer_writes_the_gated_path():
    """#4740 review 5: each empty-selection block must WRITE the no-pytest
    report to the very path the orphan gate is handed.

    Cases 11-13 of orphan-bound.test.sh pin the gate's READING of that report,
    but nothing pinned the PRODUCER: deleting one `printf` left both the
    harness and the pgrep pin green, so the cycle-1 P0 (the gate parses a
    `missing` report and REDs a legitimately-empty selection) could silently
    return. This reads the real workflow via `_load_python_ci()`.
    """
    report_path = "${RUNNER_TEMP:-/tmp}/redislite-hygiene-end.json"
    producers = [
        s
        for job in _load_python_ci()["jobs"].values()
        for s in (job.get("steps") or [])
        if isinstance(s.get("run"), str) and '"skipped":"no-pytest"' in s["run"]
    ]
    assert len(producers) == 3, (
        f"expected the three empty-selection blocks that write the "
        f"'no-pytest' report, found {len(producers)} — one `printf` deleted "
        f"leaves the gate parsing a missing report and REDs a healthy skip "
        f"(the #4740 cycle-1 P0)"
    )
    for s in producers:
        printf_lines = [
            line
            for line in s["run"].splitlines()
            if '"skipped":"no-pytest"' in line
        ]
        assert len(printf_lines) == 1, (
            f"step {s.get('name')!r} must write the no-pytest report exactly "
            f"once, found {len(printf_lines)} lines carrying it"
        )
        line = printf_lines[0].strip()
        assert line.startswith("printf '"), (
            f"step {s.get('name')!r} must WRITE the report with printf, got "
            f"{line!r}"
        )
        assert line.endswith(f'> "{report_path}"'), (
            f"step {s.get('name')!r} must write the no-pytest report to "
            f"{report_path} — the exact path the orphan gate is handed, not a "
            f"different file (#4740)"
        )
    handed = [
        s
        for s in _orphan_assert_steps()
        if f'--hygiene "{report_path}"' in s["run"]
    ]
    assert len(handed) == 3, (
        f"all three orphan-assert steps must be handed {report_path}, the "
        f"path the empty-selection blocks write; found {len(handed)}"
    )


# ── THE ON-DEMAND LANE ────────────────────────────────────────────────────
# `config/ci-surfaces.yml` `on_demand:` lists files that RUN but do not GATE.
# The lane exists because `fast_pool()` is "everything not slow / env-broken /
# carve-out" — so deleting a file from `slow_files` moves it into the FAST
# matrix and onto the gate. There is no "runs, but does not gate" state without
# a lane, and the lane is only real if a runner actually runs it.

THE_EXPENSIVE_EVAL = "eval/retrieval/test_integration.py"


def test_the_on_demand_lane_excludes_its_file_from_every_pre_merge_leg():
    # The whole point: absent from fast halves, slow, AND carve-out. Asserted
    # per leg, because the failure that matters is "one leg forgot", and a
    # set-level assertion reports that as an opaque union.
    lanes = _expand(push_legs(load_manifest()))
    name = THE_EXPENSIVE_EVAL
    bare = name[:-3] if name.endswith(".py") else name
    assert bare in lanes["on_demand"] or name in lanes["on_demand"], (
        "the lane must own the file, or it is excluded and claimed by nothing")
    # #6135: every fast shard, plus slow/carve-out/env-broken — not just a/b.
    for leg, files in lanes.items():
        if leg == "on_demand":
            continue
        assert bare not in files and name not in files, (
            f"{THE_EXPENSIVE_EVAL} must NOT run in {leg!r}: the lane exists to "
            f"keep it off the pre-merge gate")
    assert bare not in {f[:-3] for f in fast_pool(load_manifest())}, (
        "the lane must be filtered out of fast_pool — that filter is the only "
        "reason the file is off the gate")


def test_the_on_demand_lane_is_not_silently_empty():
    # ABSENT != EMPTY. A repo that has not adopted the lane is fine (no key);
    # a repo that declares the key and leaves it empty has a no-op exclusion —
    # the expensive file is back on the merge path behind something that reads
    # like a saving. Same polarity as the `durations` guard.
    m = load_manifest()
    assert "on_demand" in m, (
        "this repo must declare the lane: the PR that created it removed the "
        "25.4-min file from slow_files, and without the key that file is on "
        "the gate again")
    assert on_demand_files(m), "the declared lane must not be empty"

    adopted = dict(m)
    adopted.pop("on_demand")
    issues = leg_coverage_issues(adopted)
    assert not [i for i in issues if "on_demand lane is present but EMPTY" in i], (
        "an ABSENT lane is 'not adopted', not a broken declaration — making it "
        "red would break every repo that does not use the lane")

    empty = dict(m, on_demand=[])
    assert any("present but EMPTY" in i for i in leg_coverage_issues(empty)), (
        "a declared-but-empty lane excludes nothing and must red")


def test_a_lane_file_still_on_a_gated_leg_is_reported():
    """The silent-and-expensive direction: a lane file that is STILL on a gating
    leg, so the lane saves nothing while reading as if it did.

    #10194 changed what can go wrong here, and the check follows: the lane now
    legitimately OVERLAPS `slow_files` (five eval files keep their `durations`
    weight and their classification; the LEG drops them), exactly as slow
    carve-out files do. So a config-set overlap is no longer a defect — the
    defect is a lane file appearing in the pool a gating leg is built from,
    which `slow_gating_files()` is the single definition of.

    The mutation this pins is therefore the DEFINITION regressing, not the
    config: patch `slow_gating_files` back to `slow_files - carve_out` (drop the
    lane subtraction — the exact edit a later reader makes when the lane
    subtraction looks redundant) and the report must name the file. Without this
    test the check would be vacuous, because with the shipped definition the
    intersection is empty BY CONSTRUCTION.
    """
    m = load_manifest()
    still_slow = dict(m, slow_files=[*list(m["slow_files"]), THE_EXPENSIVE_EVAL])
    # With the shipped definition the lane is subtracted, so no issue is raised.
    assert not [i for i in leg_coverage_issues(still_slow) if "leaked" in i], (
        "a lane file in `slow_files` is now legitimate (the LEG subtracts it) — "
        "reporting it would red a correct manifest and invite deleting the "
        "lane entry")

    import tools.ci_selection as cs
    original = cs.slow_gating_files
    try:
        cs.slow_gating_files = lambda mf: (  # type: ignore[assignment]
            set(mf.get("slow_files") or []) - set(mf.get("carve_out") or []))
        hits = [i for i in leg_coverage_issues(still_slow) if "leaked" in i]
    finally:
        cs.slow_gating_files = original  # type: ignore[assignment]
    assert hits, (
        "with the lane subtraction REMOVED from slow_gating_files() a lane file "
        "is back on a gating slow leg, and this check must say so — if it "
        "passes, the guard cannot fire for the regression it names")
    assert THE_EXPENSIVE_EVAL in hits[0]


def test_the_lane_has_a_real_runner_and_reads_the_lane_from_config():
    # A lane with no runner is strictly worse than no lane: the file is off the
    # gate AND runs nowhere, so a calibration instrument silently stops being
    # run while every guard reports green. Pin that the workflow exists, is
    # manual (it must NOT gate), and does not keep a SECOND copy of the list —
    # a second copy is a second place to forget, and the forgetting is silent.
    wf = (REPO / ".github/workflows/evals-on-demand.yml").read_text()
    import yaml
    doc = yaml.safe_load(wf)
    # `on:` parses as the boolean True key in YAML 1.1 — check both spellings
    # rather than assuming, or this assertion silently tests nothing.
    triggers = doc.get("on", doc.get(True))
    assert list(triggers) == ["workflow_dispatch"], (
        f"the lane's runner must be manual only, got {list(triggers)}")
    assert "pull_request" not in triggers, "an on-demand runner must not gate a PR"
    assert on_demand_files.__name__ in wf, (
        "the workflow must resolve the lane through on_demand_files() — "
        "hardcoding a second list here lets the two drift")


def test_the_perf_lane_runner_is_off_the_gate_and_reads_the_lane_from_config():
    # #3992: `on_demand:` gained a SECOND runner — the dedicated perf lane
    # (`tests/test_perf_lane_volunteer_context.py`, the file the #2080 plan
    # names). The property the first runner is pinned for holds for the second
    # one too, and for the same reason: a wall-clock SLO assertion must never
    # gate a merge (the plan keeps the MECHANISM assertions blocking and the
    # p95 in the dedicated lane), and a runner that hardcodes its file has a
    # second list to forget.
    #
    # The failure this prevents, named: a future edit adding `pull_request:`
    # to perf-lane.yml would silently put a shared-runner wall-clock number on
    # every PR — the flake class the lane exists to separate out.
    perf = "test_perf_lane_volunteer_context.py"
    wf = (REPO / ".github/workflows/perf-lane.yml").read_text()
    import yaml
    doc = yaml.safe_load(wf)
    triggers = doc.get("on", doc.get(True))
    assert "pull_request" not in triggers, (
        "the dedicated perf lane must never gate a PR — a shared-runner "
        "wall-clock assertion on the merge path is the exact flake class "
        "#3992's plan separates out")
    assert "workflow_dispatch" in triggers, (
        "the lane's manual invocation is an acceptance criterion (#3992 AC 4)")
    assert on_demand_files.__name__ in wf, (
        "the perf-lane workflow must resolve the lane through "
        "on_demand_files(); a hardcoded path is a second list to forget")
    assert perf in on_demand_files(load_manifest()), (
        "the perf lane runs from the on_demand lane — without membership it "
        "is excluded from the gate and claimed by nothing")
    # `fast_pool()` returns `.py`-SUFFIXED entries, and the set below strips the
    # suffix, so the comparison must use the bare name. The pre-fix form
    # compared `perf` (WITH `.py`) against bare names — a value that can never
    # be in that set — so it was satisfied by construction and the mutation it
    # names (the file re-entering `fast_pool`) left this test green.
    bare = perf[:-3] if perf.endswith(".py") else perf
    assert bare not in {f[:-3] for f in fast_pool(load_manifest())}, (
        "the lane must be filtered out of fast_pool, or the wall-clock "
        "assertion is back on the merge gate")
    # Non-vacuity control, ASSERTED (the sibling duration test's pattern): move
    # the file back onto a surface and off the lane, and the assertion above
    # must go red. This is the discriminator between the fixed form and the
    # dead one — with `perf` (suffixed) this control passes vacuously too.
    m = load_manifest()
    first_surface = next(iter(m["surfaces"]))
    on_gate = {
        **m,
        "on_demand": [f for f in m.get("on_demand") or [] if f != perf],
        "surfaces": {
            **m["surfaces"],
            first_surface: [*m["surfaces"][first_surface], perf],
        },
    }
    assert bare in {f[:-3] for f in fast_pool(on_gate)}, (
        "control: with the file back on a surface it IS in fast_pool — if this "
        "fails, the assertion above cannot fail for the regression it names")


def test_the_lane_files_duration_key_is_classified_not_drift():
    # `duration_issues()` requires every `durations` key to be a CLASSIFIED
    # test file. The lane file is classified into a LANE rather than a surface,
    # so the lane must be added to that guard's `classified` set — otherwise
    # `--integrity` reds on a manifest that is correct, and the obvious "fix"
    # for the red is to delete the measured cost that justifies the lane.
    #
    # The counterfactual is the load-bearing half: with the lane removed the
    # SAME key IS drift, which proves the assertion above is testing the lane
    # and not merely an empty guard that would pass for any manifest.
    m = load_manifest()
    assert duration_issues(m) == [], (
        "the lane's declared duration must not read as unclassified drift")
    without = dict(m)
    without.pop("on_demand")
    assert any(THE_EXPENSIVE_EVAL in i for i in duration_issues(without)), (
        "control: with the lane absent this key IS drift — if this passes too, "
        "the guard above is vacuous")


def test_the_lane_is_a_classification_for_integrity_too():
    # Same defect, other guard: `integrity()` asks "is this test file
    # classified?". A lane file is deliberately absent from every surface, so
    # without the lane it reads as DRIFT (a test file nobody registered) and
    # `--integrity` reds. This is the guard whose failure would be "fixed" by
    # re-registering the file into a surface — silently putting it back on the
    # gate. Counterfactual pinned the same way.
    assert integrity(load_manifest()) == [], (
        "the lane's file must not read as unregistered drift")
    assert classify_test_file(THE_EXPENSIVE_EVAL, load_manifest()) is None, (
        "the file is deliberately in NO surface — that is what makes the lane "
        "necessary rather than decorative; if it acquires a surface, the "
        "exclusion in fast_pool becomes the only thing keeping it off the gate")
    # The counterfactual, ASSERTED. The docstring above claimed this test pinned
    # one "the same way" as its sibling, and it did not — the sibling pops the
    # lane and asserts drift while this test asserted only the clean direction.
    # With the lane removed the file IS unregistered drift; without that half,
    # a `integrity()` that simply returned [] for everything would pass here.
    without = dict(load_manifest())
    without.pop("on_demand")
    assert THE_EXPENSIVE_EVAL in integrity(without), (
        "control: with the lane absent the file really is unregistered drift — "
        "if this also passes, the assertion above is vacuous")
    assert THE_EXPENSIVE_EVAL not in integrity(load_manifest()), (
        "and with the lane present it is classified, which is the actual claim")


def test_a_null_on_demand_key_is_reported_not_a_traceback():
    # `on_demand:` with NO entries parses to None — not to []. YAML's empty
    # value. `manifest.get("on_demand", [])` returns that None (the key IS
    # present, so the default is never used), and `set(None)` raises TypeError,
    # so EVERY caller crashed before the guard that exists to name an empty
    # lane could report it.
    #
    # This test uses None SPECIFICALLY. The rest of these tests spell it
    # `on_demand=[]`, a Python list — a state a human editing the YAML can
    # never produce. That is why the whole suite passed while the real bug
    # lived: the empty-lane guard was verified against a state that never
    # occurs, which is a false assurance and not coverage.
    m = dict(load_manifest(), on_demand=None)
    assert on_demand_files(m) == set(), "a null lane must read as empty, not raise"
    assert any("present but EMPTY" in i for i in leg_coverage_issues(m)), (
        "the guard must still FIRE for the state a human actually types")
    # Every other consumer must survive the null form too — they each called
    # on_demand_files() and each raised. They must RETURN (fail closed with a
    # report), not merely not-crash: with the lane nulled the file is genuinely
    # unclassified, so drift IS the correct answer here and asserting an empty
    # list would pin the wrong behaviour.
    assert isinstance(integrity(m), list), "must report drift, not raise"
    assert any(THE_EXPENSIVE_EVAL in x for x in integrity(m)), (
        "with the lane nulled the file really is unclassified — the guard must "
        "say so rather than crash")
    assert isinstance(duration_issues(m), list)
    assert isinstance(fast_pool(m), list), "must not raise on a null lane"


def test_the_surface_audit_separates_the_lane_from_drift():
    # The lane file is unregistered ON PURPOSE — that is what the lane means.
    # Listing it under "files in NO surface" reads as drift, and the obvious
    # fix for that reading is to re-register the file into a surface, which
    # silently puts it back on the merge gate. The two states are separated.
    #
    # Pinned against the RENDERED output as well as the report, because the
    # first version of the render fix read `report["manifest"]` — a key the
    # report does not carry — so it silently evaluated to an empty list and
    # the note never appeared. A fix that cannot fail is not a fix.
    r = surface_audit(load_manifest())
    assert THE_EXPENSIVE_EVAL not in r["no_surface"], (
        "a lane file is not drift — it is classified, into a lane")
    assert THE_EXPENSIVE_EVAL in r["on_demand"], (
        "the report must carry the lane, or the renderer cannot show it")
    rendered = render_surface_audit(r)
    assert "in the on_demand lane" in rendered, (
        "the rendered audit must name the lane; the fix that read a missing key "
        "produced no line at all and still passed its own test")
    assert THE_EXPENSIVE_EVAL in rendered

def test_a_dead_on_demand_entry_is_reported():
    # The guard this replaces was VACUOUS: `classified` had just been updated
    # with the lane itself, so `f not in classified` was tautologically False
    # and the branch could never fire. Pinned with an entry that names a file
    # which does not exist — a lane entry like that excludes nothing that runs,
    # while the lane still reads as healthy.
    m = dict(load_manifest(), on_demand=["bogus/nonexistent.py"])
    hits = [i for i in leg_coverage_issues(m) if "on-demand" in i and "dead entry" in i]
    assert hits, "a lane entry naming a missing file must be reported"
    assert "bogus/nonexistent.py" in hits[0]


def test_a_top_level_lane_file_is_not_re_registered_by_unlisted_tests():
    # `--register` fixes "unlisted" files by registering them into a surface.
    # For a lane file that fix is the BUG: it would silently put the file back
    # on the merge gate.
    #
    # THE STATE THAT MATTERS is a file that is BOTH unregistered AND laned —
    # only then does the lane skip change the answer. An earlier version of this
    # test laned `test_billing.py`, which is registered under `api`, so
    # `unlisted_tests()` never listed it either way and the test proved nothing
    # (the reviewer's mutation — deleting the lane skip — left it GREEN).
    # So: take a real registered file, REMOVE it from every surface, and show
    # that the lane alone decides whether it reads as drift.
    base = load_manifest()
    stripped = {**base,
                "surfaces": {s: [e for e in entries if e != "test_billing.py"]
                             for s, entries in base["surfaces"].items()}}
    assert classify_test_file("test_billing.py", stripped) is None, (
        "setup: the file must be in NO surface, or the lane skip is not what "
        "is being tested")
    # Control: unregistered and NOT laned -> drift, so --register would take it.
    assert "test_billing.py" in unlisted_tests(TESTS_DIR, stripped), (
        "control: with no lane, an unregistered file IS unlisted — if this "
        "fails the test below is vacuous")
    # The guard: unregistered BUT laned -> not drift, so --register leaves it be.
    laned = {**stripped, "on_demand": ["test_billing.py"]}
    assert "test_billing.py" not in unlisted_tests(TESTS_DIR, laned), (
        "a lane file must not read as unlisted, or --register would put it "
        "back on the merge gate")
    # And the real manifest must not report the currently-laned file either.
    assert "eval/retrieval/test_integration.py" not in unlisted_tests(TESTS_DIR, base)


def test_mergify_guard_step_is_wired_fail_closed():
    """#5215 Task 4: the protection-invariant guard is REQUIRED and unsilenceable.

    The guard runs the static clauses (i)-(viii) of the merge-throughput plan's
    §3. It lives in `manifest-integrity`, a job `python-ci-gate` (the required
    aggregate) lists in `needs`, so a divergence blocks the merge. This pins the
    invocation SHAPE: a direct call with no shell operator, not
    `continue-on-error`, and unconditional. `|| true` / `; exit 0` /
    `continue-on-error` would each turn a real divergence into a green required
    check (the #2656 class this file already pins for the drift gate).
    """
    workflow = _load_python_ci()
    steps = workflow["jobs"]["manifest-integrity"]["steps"]
    matching = [s for s in steps if "mergify_config_guard.py" in (s.get("run") or "")]
    assert len(matching) == 1, (
        "#5215: the mergify config guard must be invoked exactly once in "
        f"manifest-integrity; found {len(matching)}")
    step = matching[0]
    first_line = (step["run"] or "").splitlines()[0].strip()
    assert first_line.startswith("python3 tools/mergify_config_guard.py --static"), (
        f"the guard must be invoked directly (#5215); got {step['run']!r}")
    assert not any(op in step["run"] for op in ("||", "&&", ";", "`", "$(")), (
        "no shell operator may follow the guard — `|| true` / `; exit 0` makes a "
        f"real divergence report green (#5215); got {step['run']!r}")
    assert not step.get("continue-on-error"), (
        "the guard step must not be continue-on-error: a divergence would report "
        "success and the required aggregate would go green (#5215)")
    assert not step.get("shell"), (
        "the guard step must not override `shell:` — that can swallow the exit "
        "code (#5215)")
    assert step.get("if", "always()") in ("always()", "${{ always() }}"), (
        "the guard step must be unconditional: any other `if:` drops enforcement "
        "on the events it excludes")


def test_lane_stash_guard_runs_on_a_tools_only_change():
    # #8051: `tests/test_lane_stash.py` (registered `core`) guards
    # `tools/lane_stash.py` — the lane-scoped stash that stops a bare
    # `git stash pop` from applying and dropping a peer lane's entry, a
    # repo-global `refs/stash` hazard with three recorded occurrences.
    # `tools/` is a flat NON_PYTHON_PREFIXES entry and the tool matches no
    # SOURCE_PATTERNS row, so before the CORE_ALSO entry a tools-only diff was
    # filtered out BEFORE the "not matched -> core" fallback: the pin never ran
    # on the file it exists to hold (the #1349/#3332/#3616 silent-drop class).
    # Mutation check: removing the CORE_ALSO entry fails the arm below
    # (surfaces == [] and the guard absent from test_files).
    r = _sel(["tools/lane_stash.py"])
    assert r["full"] is False, r
    assert r["surfaces"] == ["core"], r
    assert "test_lane_stash.py" in r["test_files"], r


def test_turn_parity_census_guard_runs_on_a_tools_only_change():
    # #10017: `tests/test_10017_turn_parity_census.py` (registered `core`)
    # pins the refusal arms of `tools/turn_parity_census.py` — the purge gate
    # for the pre-#10000 turn nodes. `tools/` is a flat NON_PYTHON_PREFIXES
    # entry and the tool matches no SOURCE_PATTERNS row, so before the
    # CORE_ALSO entry a census-only diff was filtered out BEFORE the
    # "not matched -> core" fallback: the gate's own refusal pins never ran
    # on the file they hold (the #1349/#3332/#3616 silent-drop class).
    # Mutation check: removing the CORE_ALSO entry fails the arm below
    # (surfaces == [] and the guard absent from test_files).
    r = _sel(["tools/turn_parity_census.py"])
    assert r["full"] is False, r
    assert r["surfaces"] == ["core"], r
    assert "test_10017_turn_parity_census.py" in r["test_files"], r


def test_journal_growth_guard_runs_on_a_tool_or_probe_change():
    # #5612: `tests/test_journal_growth.py` (registered `core`) pins the
    # accounting of `tools/journal_growth.py` — the measurement §5.1 recorded as
    # missing (`bytes + other_bytes == file size` exactly, malformed lines
    # counted not raised, the embedding share found nested in the snapshot).
    # ALL guarded paths are flat NON_PYTHON_PREFIXES entries (`tools/`,
    # `docs/`, `supabase/`) matching no SOURCE_PATTERNS row, so without the
    # CORE_ALSO entries a tool-only, probe-only or FIXTURE-only diff was
    # filtered out BEFORE the "not matched -> core" fallback and the pins never
    # ran on the files they exist to hold (the #1349/#3332/#3616 silent-drop
    # class, the same shape as the `tools/lane_stash.py` arm above).
    for changed in ("tools/journal_growth.py",
                    "docs/evidence/5612-journal-growth/probes/"
                    "corpus_growth_probe.py",
                    # the corpus the pins hard-code (`records == 39`, 29 types)
                    "supabase/tests/fixtures/record_projection_journal.jsonl"):
        r = _sel([changed])
        assert r["full"] is False, (changed, r)
        assert r["surfaces"] == ["core"], (changed, r)
        assert "test_journal_growth.py" in r["test_files"], (changed, r)


def test_dashboard_deploy_script_change_selects_core_not_tier1():
    # PR #8852 review P2: `website/apps/dashboard/deploy.sh` is the documented
    # manual deploy, and the #8816 guard (`tests/test_e2e_wrangler_prewarm.py`,
    # registered `core`) both READS it and EXECUTES it from a scratch tree — the
    # arms that catch a version path which does not resolve. `website/` is a flat
    # NON_PYTHON_PREFIXES entry and the script matches no SOURCE_PATTERNS row, so
    # a deploy.sh-only diff was filtered out BEFORE the "not matched -> core"
    # fallback, took the docs-only early return and fell back to tier-1 smoke,
    # where that guard is NOT selected — the guard never ran on the only PR that
    # can break it (the #1349/#3332/#3616 silent-drop class).
    #
    # Measured before the CORE_ALSO entry: `_sel([...])` -> surfaces=[],
    # full=False, 46 test files, test_e2e_wrangler_prewarm.py absent.
    # Mutation check: removing the CORE_ALSO entry fails every assert below.
    r = _sel(["website/apps/dashboard/deploy.sh"])
    assert r["full"] is False, r
    assert r["surfaces"] == ["core"], r
    assert "test_e2e_wrangler_prewarm.py" in r["test_files"], r


def test_9206_lane_suites_run_on_the_diffs_that_can_break_them():
    # #9206 review P1: `test_9206_apply_first_lane.py` and
    # `test_9206_derived_recompute_lane.py` are the proof that §17.1's write
    # handle COMPLETES the SDK's own doors under an armed guard. A REGISTRATION
    # alone closed `--integrity` (the required `manifest-integrity` job) but is
    # not a SELECTION pin — the same distinction `test_9192_guard_runs_on_the_
    # diff_that_can_break_it` records. Each arm below is the diff that can
    # silently disarm one of the two lanes:
    #
    #   * `tortoise/source_identity.py` — the derived-recompute lane's ONLY
    #     product writer (`resolve_source_key`'s adopt-on-touch). It matches no
    #     SOURCE_PATTERNS row, so it falls through to `core`, which is where the
    #     suite is registered.
    #   * `tools/source_dedup_report.py` — the lane's second DECLARED writer and
    #     the repair path its own disclosure recommends. `tools/` is a flat
    #     NON_PYTHON_PREFIXES entry, so WITHOUT the CORE_ALSO entry this diff
    #     selected NO surface at all (`surfaces: []`, `full: false`) and took
    #     the docs-only early return: the census that pins the tool's lane never
    #     ran on the PR editing the tool (the #1349/#3332/#3616 silent-drop
    #     class).
    #   * `tortoise/sdk.py` — the apply-first lane's doors. It is a
    #     SHARED_MODULE (full matrix), so the assertion here is `full is True`,
    #     not a surface list: it documents that this arm is covered by the trunk
    #     backstop rather than by the `core` row.
    #
    # Mutation: removing either the `core` registration or the CORE_ALSO entry
    # fails the corresponding assert below.
    r = _sel(["tortoise/source_identity.py"])
    assert r["full"] is False, r
    assert r["surfaces"] == ["core"], r
    assert "test_9206_derived_recompute_lane.py" in r["test_files"], r

    r = _sel(["tools/source_dedup_report.py"])
    assert r["full"] is False, r
    assert r["surfaces"] == ["core"], r
    assert "test_9206_derived_recompute_lane.py" in r["test_files"], r
    assert set(r["test_files"]) != _tier1(), r

    r = _sel(["tortoise/sdk.py"])
    assert r["full"] is True, r


def test_batch_review_tool_change_selects_core_not_tier1():
    # #9790 review P1: tools/batch_review.py owns tests/test_batch_review.py
    # (registered `core`, the guard for the fail-closed record path).
    #
    # `tools/` is a flat NON_PYTHON_PREFIXES entry and no SOURCE_PATTERNS row
    # matches the tool, so a tool-only change came back with `changed == []` and
    # took the docs-only early return — tier-1 smoke, where the guard is not
    # registered. Measured before this entry: `select(["tools/batch_review.py"])`
    # -> surfaces=[], full=False, test_batch_review.py absent. That is the
    # #1349/#3332/#3616 silent-drop class on the one diff that can break the
    # guard: deleting `plan_records`'s manifest-head equality would have merged
    # with none of the suite running (no count in the prose: it decays every time
    # a pin is added, and what this test pins is the SELECTION, not the number).
    #
    # CORE_ALSO (not TOOL_CARVEOUTS): the suite is hermetic, so `core` is the
    # cheapest leg that can carry it. Mutation check: removing the CORE_ALSO
    # entry fails assert 2 below.
    r = _sel(["tools/batch_review.py"])
    assert "core" in r["surfaces"], r
    assert "test_batch_review.py" in r["test_files"], r
    assert r["full"] is False, r


def test_seal_session_events_tool_change_selects_core_not_tier1():
    # tools/seal_session_events.py (#6388) owns tests/test_session_events_seal.py,
    # which is `core`-registered in config/ci-surfaces.yml. Without its CORE_ALSO
    # entry this was the same #3261 silent-drop class `queue_resweep` records:
    # `tools/` is swallowed by NON_PYTHON_PREFIXES before the `core` fallback, so
    # a tool-only diff took the docs-only early return and selected NO surface —
    # the guard never ran on the file it guards.
    #
    # Mutation check: removing the CORE_ALSO entry empties `r["surfaces"]`, so
    # assert 1 (below) is the row that reds first — measured: surfaces=[] and
    # the test file is unlisted, same as the pre-entry state.
    r = _sel(["tools/seal_session_events.py"])
    assert "core" in r["surfaces"], r
    assert "test_session_events_seal.py" in r["test_files"], r
    assert r["full"] is False, r
