#!/usr/bin/env python3
"""Tiered test selection (#1021) — the single parameterized selection function.

Consumed by python-ci.yml's `changes` job (PRs) and the nightly audit.
Emits JSON: {surfaces: [...], full: bool, test_files: [...] | "ALL",
slow_files: [...], slow_run: bool, slow_selected: [...],
carve_out_run: bool}.

#2147/#2148: the test-slow + test-carve-out jobs were the two diff-unaware
python-ci legs (2026-09-02-ci-audit F1/F2 — a docs-only PR paid ~59 runner-
min slow + 23.5 min carve-out). select() now emits the diff-gate contract on
every return path: slow_run (test-slow runs), slow_selected (the slow files
test-slow should run — surface-matched on tier-2 PRs, the whole committed
leg set = slow_files - carve_out on full selections), carve_out_run (the
carve-out job runs — full selections or a matched surface owns carve-out
files). The workflow's changes job echoes these; docs/website-only PRs
(surfaces == []) skip both legs, trunk (push) + nightly (schedule) +
unknown/shared-module PRs keep full coverage. NOTE: config/* is NOT a
docs-only path — it falls through to the core surface, so config-only PRs
run both legs (conservative: manifest edits are exactly when the slow split
should be exercised; the workflow header comment says the same).

Selection rules (fail-closed, conservative):
- push to main / schedule  -> full (tier 3 — the trunk backstop)
- any changed file UNKNOWN to the surface map -> full (new dirs/subsystems)
- any changed SHARED/core module -> full (cross-cutting code wants max coverage)
- otherwise -> tier 2 = core ∪ union(matched surfaces' test files)
  (docs-only PRs -> core set only — the always-on smoke)

Also:
- --integrity: fail if any tests/*.py is absent from the manifest
  (unlisted test files would silently drop out of selection — the drift trap)
- audit artifact: writes the selection record (pr_sha, changed_files, surfaces,
  selected tests, fn version) for the nightly recall audit (scope v5).
"""

from __future__ import annotations

import argparse
import ast
import json
import math
import os
import re
import sys
from pathlib import Path

SELECTION_FN_VERSION = "1.3.0"

REPO = Path(__file__).resolve().parent.parent
MANIFEST = REPO / "config" / "ci-surfaces.yml"
TESTS_DIR = REPO / "tests"
WORKFLOW = REPO / ".github" / "workflows" / "python-ci.yml"

# #1266: the test (a)/(b) halves must stay count-balanced within this delta.
# A tilt beyond it means someone added files to one half without rebalancing
# (the exact drift that pushed half (a) over the watchdog cap).
# #3400: this is now the FALLBACK invariant, used only when the manifest
# carries no `durations` map at all. Once measured durations exist the
# balance invariant is DURATION (below) — LPT packs by weight, and a correct
# pack can legitimately carry very different file counts: a few multi-minute
# files on one side against the long tail of sub-second ones on the other.
HALF_IMBALANCE_TOLERANCE = 3

# #3400: with measured durations, the halves must stay DURATION-balanced
# within this ratio. Index parity on the same pool leaves a tilt far above it —
# which files land on even vs odd indices has nothing to do with what they cost
# — while the LPT pack balances the same pool. 1.25 is loose enough for noise
# and tight enough that a reversion to parity reds. (Do not restate either
# figure here: both move whenever the pool does.)
HALF_DURATION_IMBALANCE_RATIO = 1.25

# #1473: weight for a fast file with no measured duration. The pack can only
# be as good as its weights, hence the coverage guard (#3400).
DEFAULT_FAST_WEIGHT = 2.0

# #3400: `durations` rotted to 15 entries for 500 fast files (97% packed at
# the flat default), which silently degenerated the duration-aware pack into
# a count-based one. Floor the coverage so it cannot rot back. The check is
# skipped entirely for an ABSENT/EMPTY map (a repo that has not adopted
# durations is not failed) and bites once the map is populated. Do not restate
# the pool size or the current percentage here — the durations map's own header
# carries the sweep that measures them, and a figure copied into this comment is
# what went stale before (it read "520 files / 96.5%" while the pool had grown).
DURATION_COVERAGE_MIN = 0.90

# bash/heredoc-safe newline (the pi bash wrapper mangles raw \n in heredocs)
NL = chr(10)

# Shared/cross-cutting modules -> full matrix (conservative per scope v5).
SHARED_MODULES = (
    "tortoise/sdk.py",
    "tortoise/ep.py",
    # cross-cutting leaf: exception classes consumed by sdk, api, core, AND ep
    # surfaces (test_divergence_conformance, test_epic903_modes,
    # test_ingest_*, test_calibration) — a change here runs the full matrix.
    "tortoise/exceptions.py",
    # #4097: cross-cutting leaf — the declared env-truthiness contract. Consumed by
    # `core` (why, rerank, monitoring, model_adapters, frontmatter_validator,
    # extractor_v2, backup_config, embedded_lifecycle, cimd, projection), `api`
    # (hosted_api), `sdk` (sdk, retrieval) and `eval` (embeddings) — the `ep` and
    # `onboarding` surfaces list the guard for coverage reasons, not because they
    # import the leaf. A change to `env_flag`'s blank/garbage handling changes
    # consumer behaviour on every surface, so it runs the full matrix (same
    # rationale as exceptions.py above).
    "tortoise/env_truthy.py",
    "tortoise/tool_registry.py",
    "tortoise/mcp_server.py",
    # ⚠️ #4713: naming the package's entry point rather than the whole
    # `tortoise/projection/` directory is a deliberate coverage REDUCTION. A
    # submodule edit now runs only the surfaces registered for it below, plus the
    # `core` pins that exercise it — the sdk/api suites wait for the next main
    # push. Take the `edges.py` treatment below as the model if that is wrong.
    "tortoise/projection/__init__.py",
    "tests/conftest.py",
    "tests/fake_control_plane.py",
    # #4069: suite-wide test helpers re-exported by `tests/conftest.py`. Both are
    # imported at conftest MODULE level and hand their fixtures to every surface's
    # tests, and neither is a `test_*.py` file, so the manifest never classifies
    # them: without these entries a change to one selected `core` only, and a break
    # it induced in an api/eval/onboarding/ep/battery test never ran on the PR that
    # made it (#1349/#3332/#3910). The `tests.*` half of that rule is enforced by
    # `tests/test_ci_selection.py::test_every_conftest_module_level_tests_import_is_shared`.
    "tests/_tmpdir_hygiene.py",
    "tests/_embedded.py",
    "pyproject.toml",
    "requirements.txt",
    ".github/workflows/python-ci.yml",
    ".github/actions/",
)

# Source-path patterns that trigger each surface (surface -> test files come
# from the manifest).
SOURCE_PATTERNS = {
    "battery": ("battery/",),
    "onboarding": ("tortoise/onboarding/",
                   # #4054: the auth surface moved to the app project — the BFF's
                   # pages now live in the dashboard's `public/` tree (vite copies
                   # them to `dist/`, the deployed root). `website/signin.html` was
                   # deleted outright (it 301'd to /auth and was dead).
                   "website/apps/dashboard/public/welcome.html",
                   "website/apps/dashboard/public/signup.html",
                   "website/self-hosted.html", "website/product.html",
                   # #2409: the public contact form's page. Registered here for the
                   # same reason as the rest of this tuple — a PR touching only
                   # this page must still select the guard that holds it.
                   "website/contact.html",
                   "website/index.html",
                   "website/privacy.html",
                   # #3485: the shared cross-subdomain session bridge is a
                   # website asset whose guard test
                   # (test_cross_subdomain_cookie_sync.py) reads it directly.
                   # Without this entry a bridge-only PR matched no pattern,
                   # fell into NON_PYTHON_PREFIXES -> changed == [] -> tier-1
                   # smoke, and the guard for the file under review never ran
                   # (the #1349/#3332/#3616 silent-drop class). The file is
                   # dual-registered: this surface owns the website guards,
                   # `api` keeps its existing membership.
                   "website/assets/supabase-session.js",
                   # #3332: the public pages that own a guard test in this surface.
                   # docs.html + faq.html -> test_website_docs_consistency.py;
                   # product.html + welcome.html -> test_website_static.py;
                   # index.html + privacy.html -> test_waitlist_form.py;
                   # signup.html + signin.html -> test_signup_form_safety.py;
                   # self-hosted.html -> test_harness_mcp_config.py.
                   # Listing a path is what makes a change to it select this
                   # surface at all — otherwise its guard test never runs.
                   "website/docs.html", "website/faq.html",
                   # #3673: the served skill documents and the installer belong to
                   # this surface. The parity contract between
                   # `tortoise/onboarding/SKILL.md` and its served mirror is
                   # asserted by test_onboarding_variants.py, and the installer's
                   # `SKILLS=(...)` is what the dashboard's shipped-set claim is
                   # pinned against (test_installer_preserves_foreign_skill_
                   # content.py, dual-registered onto this surface below).
                   #
                   # Before these entries a change to the SERVED copy alone matched
                   # no pattern: `surfaces=[]` -> the parity gate ran only via the
                   # tier-1 fallback (coverage by accident, not by design, and it
                   # would vanish the moment the test left `tier1`). Worse, the
                   # installer ran NO guard at all — its guard is on `core` and is
                   # not in `tier1` — the #1349/#3332/#3616 silent-drop class.
                   "website/apps/dashboard/public/skills/",
                   "website/apps/dashboard/public/install-tortoise-skills.sh",
                   # #3952: the blog-admin console SPA's build config and its
                   # committed build snapshot own the guard tests added in
                   # tests/test_admin_return_to.py (the build base, and
                   # document-independent resolution of the shell's asset refs).
                   # Neither path is under a Python package prefix, so without
                   # these entries a PR that reverts `base: '/admin/'` to the
                   # relative form selects NO surface (surfaces=[], full=False)
                   # and the guard never runs on the PR that owns it — the same
                   # #3616 pattern these entries sit next to, one level up.
                   "website/apps/blog-admin/vite.config.ts",
                   "website/apps/blog-admin/dist/index.html",
                   # The guards read the moved Functions themselves — and not only
                   # the gate: `test_admin_return_to.py` reads the gate by exact
                   # path and derives the console's mount path from its directory,
                   # `test_pages_bindings.py` rglob-scans the ENTIRE tree for env
                   # reads (`_env_names_read_by_the_bff`), and
                   # `test_website_docs_consistency.py` resolves routes from both
                   # function roots. The directory is therefore the correct
                   # granularity — not the two files that happened to break.
                   #
                   # #4171: this block previously named
                   # `website/functions/admin/[[path]].ts`, which the admin-origin
                   # move DELETED. An entry is matched by `startswith`, never
                   # against the filesystem, so the dead path stayed "alive": a PR
                   # touching the moved gate selected NO surface and
                   # `test_admin_return_to.py` silently stopped guarding the file it
                   # was written for (#1349/#3332 class, one level up — the ratchet
                   # caught the reverse direction only). Naming single files also
                   # left every OTHER moved Function unselectable: `auth/signup.ts`,
                   # `_shared/auth/csrf.ts`, `api/session.ts` and the rest all
                   # selected surfaces=[] — so a change adding an env read shipped
                   # green with the binding guard never running. The directory
                   # closes both holes. `test_source_patterns_all_name_something_real`
                   # (tests/test_ci_selection.py) now fails on a dead entry.
                   "website/apps/dashboard/functions/",
                   # The SPA files the migrated-surface invariant reads by exact
                   # path (`test_no_legacy_token_path.py` -> MIGRATED_SURFACES)
                   # and that `test_admin_return_to.py` opens by name. Each of
                   # these selected surfaces=[] before this commit, so the
                   # (`website/apps/dashboard/src/main.jsx` is covered by the
                   # `website/apps/dashboard/src/` directory entry below.)
                   # legacy-token invariant could not fail on the very files it
                   # exists to guard — including `main.jsx`, which THIS PR
                   # rewrites (logout teardown, CSRF content-type). Same
                   # #1349/#3332 class as the entry above; both are now covered by
                   # `test_source_patterns_all_name_something_real`.
                   "website/apps/blog-admin/src/lib/blog-api.ts",
                   "website/apps/blog-admin/src/hooks/useAuth.ts",
                   # #4171: two more guarded files this branch MODIFIED while leaving
                   # them unselectable, found by review after the directory entry
                   # landed. `supabase.ts` is read by exact constant in
                   # `test_cross_subdomain_cookie_sync.py` (four STORAGE_KEY/cookie
                   # scope assertions) and by
                   # `test_session_bridge_fragment_retention.py`;
                   # `blog/_shared/admin-auth.ts` by
                   # `test_no_legacy_token_path.py`'s store-fault-vs-signed-out
                   # semantics guard. Both are non-tier-1 `onboarding` guards, so
                   # editing these files shipped green with their guard never
                   # running — the same #1349/#3332 class, and inconsistent with
                   # the sibling entries directly above.
                   "website/apps/blog-admin/src/lib/supabase.ts",
                   "website/functions/blog/_shared/admin-auth.ts",
                   # #3523: the dashboard's unknown-address guard
                   # (tests/test_dashboard_unknown_address.py) reads the Pages
                   # routing inputs for app.premiselabs.co plus the app's
                   # location.pathname branches. `website/` sits in
                   # NON_PYTHON_PREFIXES and neither path is under a Python
                   # package prefix, so without these entries a PR that deletes
                   # 404.html, adds a `/* / 200` catch-all, or adds an unrouted
                   # pathname branch selects NO surface (surfaces=[], full=False)
                   # and the guard never runs on the PR that owns it — the
                   # #1349/#3332/#3616 silent-drop class, which registration
                   # alone does not fix (registration only makes the file
                   # CLASSIFIED; selection is what makes it RUN). `src/` is a
                   # directory because the anti-drift check scans every non-test
                   # source module for pathname branches, not just main.jsx.
                   "website/apps/dashboard/public/_redirects",
                   "website/apps/dashboard/public/404.html",
                   "website/apps/dashboard/src/",
                   # #4006 review: the guard's SERVER_BUILT_ROUTES (/team carrying
                   # the Stripe ?session_id= return) are BUILT here, so a change to
                   # the server-side return path must run the guard too — otherwise
                   # public/_redirects goes stale against it and the guard stays
                   # green: the same silent-drop class this entry exists to close.
                   "tortoise/hosted_api.py",
                   # #3950: the blog-discoverability guard
                   # (test_website_docs_consistency.py
                   # ::test_every_in_scope_page_links_to_the_blog) covers all 12
                   # public+indexable+served pages, not just docs/faq. An unlisted
                   # page means a PR touching ONLY that page selects no surface
                   # (surfaces=[], full=False) and the guard never runs — verified
                   # before listing: `--changed-files website/tos.html` yielded
                   # surfaces=[] while website/product.html yielded ['onboarding'].
                   # That is the #1349/#3332 silent-drop class one more time: the
                   # guard silently stops covering the page it was written for.
                   # The ratchet is now two-directional, so adding a guarded page
                   # without listing it here FAILS a test instead of silently
                   # shrinking coverage: `test_every_source_pattern_is_selectable`
                   # (tests/test_ci_selection.py) checks entry -> runs, and
                   # `test_every_in_scope_page_is_selectable_by_ci`
                   # (tests/test_website_docs_consistency.py) checks the reverse —
                   # that every page in the blog guard's derived set reaches a
                   # surface through this tuple.
                   "website/security.html", "website/tos.html",
                   "website/license.html", "website/dpa.html",
                   "website/aviso-privacidad.html",
                   # #3436: the remaining top-level page, listed for the
                   # same reason as every other page entry in this tuple — they
                   # are covered by the site-wide element-id uniqueness guard
                   # (tests/test_website_docs_consistency.py), whose scope is
                   # DERIVED as `website/*.html`. Their absence was verified
                   # before listing: `select(["website/invite-accept.html"])`
                   # returned surfaces=[] with the guard absent from
                   # test_files, so a duplicate id could land on the invite
                   # landing page without the guard running on the PR that
                   # added it. Both are `noindex` pages, which keeps them out of
                   # the blog guard's scope, not out of this one: a noindex page
                   # is still a served document, and duplicate ids are invalid in
                   # it.
                   # #4054: `invite-accept.html` moved to the app project with the
                   # rest of the auth pages, so its entry follows it —
                   # `website/invite-accept.html` no longer exists, and the stale
                   # entry was caught by `test_source_patterns_all_name_something_real`
                   # (tests/test_ci_selection.py) the moment this branch merged main.
                   "website/404.html",
                   "website/apps/dashboard/public/invite-accept.html",
                   # The shared href extractor both blog-guard layers call
                   # (tests/test_website_docs_consistency.py here, and
                   # tests/e2e/test_legal_pages.py in the separate `legal-e2e`
                   # job, which CI runs on every PR regardless of selection).
                   # Without this entry, editing ONLY the extractor selects core
                   # and does NOT run the static guard — so the file implementing
                   # the guard's rule could be changed without running
                   # `test_rendered_hrefs_ignores_non_rendered_markup`, the test
                   # that pins that rule. Same #1349/#3332/#3616 class as the
                   # pages above, one level up: the helper needs the same
                   # reachability guarantee as the pages it serves.
                   # Deliberately NARROW rather than promoted to SHARED_MODULES:
                   # this file has a single matrix consumer, and the full matrix
                   # would buy nothing the E2E consumer is not already given.
                   "tests/_html_links.py",
                   # #3950 review: two more inputs the guard DERIVES its scope from,
                   # so each changes guard coverage without changing a page.
                   # `website/_redirects` is what `_canonical_redirect_targets()`
                   # reads to drop redirected pages from scope, and
                   # `website/functions/blog/[[path]].ts` is the Function
                   # `_function_serves()` resolves the blog link against. A
                   # routing-only PR could therefore silently shrink or break the
                   # guard while selecting NO surface (surfaces=[], full=False) —
                   # the same #1349/#3332/#3616 silent-drop class, applied to the
                   # derivation's own inputs rather than to its output.
                   "website/_redirects",
                   "website/functions/blog/[[path]].ts",
                   # #4316: the agent API Function (create / edit / DELETE) owns
                   # the guard in tests/test_blog_agent_delete_guard.py, which
                   # executes it under Node. Without this entry a PR touching
                   # ONLY the DELETE guard (posts/[[path]].ts) selects NO surface
                   # (surfaces=[], full=False) and the guard never runs on the
                   # very PR that owns it — the #1349/#3332/#3616 silent-drop
                   # class the entries above exist to close, one level down.
                   # Directory granularity, deliberately: the sibling Functions
                   # (generate-seo/-cover, purge) share the API's auth + env
                   # reads, and naming single files is what left every other
                   # moved Function unselectable in #4171.
                   "website/functions/blog/api/",
                   # #3616: the deploy-binding gate is a PAIR — the checker and
                   # the manifest it reads. Neither path is under a Python
                   # package prefix, so without these two entries a PR that
                   # edits the gate's logic or downgrades a binding to
                   # `recommended` selects NO surface (surfaces=[], full=False)
                   # and test_pages_bindings.py never runs on the PR that owns
                   # it. That is the #3616 pattern one level up: the thing that
                   # decides whether the gate works would not itself be gated.
                   "tools/check_pages_bindings.py",
                   "config/required-bindings.yml",
                   # #3806: the ship-test instrument and its guard. The guard test
                   # (tests/test_ship_test_onboarding.py) is registered in BOTH
                   # `core` (its generic probe helpers) and `onboarding` (the
                   # onboarding surface it measures). Without this entry a change
                   # to the instrument alone selects NO surface (`tools/` is a
                   # flat NON_PYTHON_PREFIXES entry, and the docs-only return
                   # bypasses the `core` fallback) so its guard never runs on the
                   # PR that edits it — the #3261/#3616/#3910 silent-drop class.
                   "tools/ship_test_onboarding.py",
                   # #3620: the Pages UPLOAD-ROOT gate is a pair too — the
                   # preflight checker and the reviewed classification it reads.
                   # A PR that adds a top-level entry under website/ (or edits
                   # the checker) must select this surface, or the ratchet that
                   # classifies the new entry never runs on the PR that owns it.
                   "tools/check_pages_upload_root.py",
                   "config/pages-upload-classification.txt"),
    # NOTE: .github/workflows/deploy-pages.yml is deliberately NOT listed above.
    # A review pointed out that adding it would be a coverage DOWNGRADE: an
    # unlisted path falls into the unknown-path branch -> FULL matrix (fail
    # closed), whereas listing it selects only `onboarding`. Today the two tests
    # that read that workflow both live in onboarding, so nothing is lost — but
    # a future core-registered test reading it would silently stop running on
    # the PR that edits it. Fail-closed is the right default for the file that
    # owns the deploy.
    "ep": ("tortoise/dream.py", "tortoise/analyze.py",
           "tortoise/ranking.py"),
    "sdk": ("tortoise/ids.py", "tortoise/models.py", "tortoise/crypto.py",
            "tortoise/reader.py", "tortoise/retrieval.py",
            # #3849: the ask PIPELINE now lives here (moved out of
            # tortoise/sdk.py, which is a shared module -> FULL matrix). It
            # is the only home of run_ask_lane/run_ask_assembled, so an
            # ask_lane-only change must select sdk — otherwise the lane's
            # own suites (test_ask_sdk / test_assembly_sdk /
            # test_ask_regression_llm / test_d3_session_identity) silently
            # stop running (fallback to core ran NO ask tests).
            "tortoise/ask_lane.py",
            # ask-lane shared vocabulary/gating: a PR touching ONLY these
            # must select sdk so test_ask_sdk.py (+ ask reader/calibration
            # pins) run — the old fallback to core ran NO ask tests.
            # exceptions.py is SHARED (cross-cutting leaf -> full matrix);
            # transport.py is dual-wired with api: its only direct unit test
            # is test_metering.py::test_selfhost_transport_exemption.
            "tortoise/schemas.py", "tortoise/transport.py",
            # #2071: the spot-check tools are the eval-lane ask QA — a
            # spot-check-only PR selects the sdk surface (its tests live
            # there: test_ask_spotcheck_judge.py).
            "tools/ask_spotcheck.py", "tools/ask_spotcheck_consistency.py",
            "tools/ask_spotcheck_probe.py",
            # #3910: the ask-lane recall bench is the same QA family —
            # tests/test_ask_retrieval_levers.py pins the `_retrieve_pipeline`
            # it mirrors, so a bench-only PR must select `sdk` rather than
            # drop to tier-1 smoke with that guard test never running.
            # Refs #2089, whose criterion 1 this entry satisfies.
            "tools/ask_recall_bench.py",
            # #3914: gen_ask_transcripts.py OWNS the seeder whose shape the
            # committed transcript goldens and tests/test_ask_seed_shape.py
            # pin. Before this entry the flat "tools/" prefix swallowed the
            # path, so a seeder-only PR selected NO surface (surfaces=[],
            # tier-1 smoke only) and both guards ran nowhere — the same
            # #1349/#3332/#3910 silent-drop class, on the file that
            # manufactures the graph those guards read.
            "tools/gen_ask_transcripts.py",
            # B6 objective 4: the answer-shape instrument is the ask-lane's
            # shape measurement (it drives sdk.ask, build_reader_model and
            # the shipping tortoise_search/tortoise_recall handlers to
            # compute shape_rate). Same family and same silent-drop trap as
            # the entries above: the flat "tools/" prefix would swallow a
            # shape-rate-only PR into tier-1 smoke and the ask-lane tests
            # would not run where the measurement changed.
            "tools/ask_shape_rate.py",
            "tortoise/projection/edges.py"),
    "api": ("tortoise/hosted_api.py", "tortoise/hosted_backup.py",
            "tortoise/acl_graph_users.py", "tortoise/__main__.py", "tortoise/mcp_auth.py",
            # #3154: hosted_api.py imports hosted_backup.py at module level (the
            # backup/restore/import endpoints), and the boolean-index audit lives
            # there — without this entry a hosted_backup.py-only change matched
            # no pattern and fell through to `core`, skipping the api-registered
            # tests that pin it (test_graphcopy_boolean_index_3154.py,
            # test_hosted_backup.py, test_dr_endpoints.py). Paired with
            # CORE_ALSO: many core-registered tests (test_backup_sweep.py,
            # test_backup_multigraph_e2e.py, test_backup_watcher.py,
            # test_alert_store.py) also pin it.
            # #4367: email_notify.py OWNS `_build_invite_link` (and the invite
            # sender itself). `tests/test_email_integration_resend.py` —
            # registered in `api` — is the guard that pins the invite-link
            # contract and replays the recorded accept-page cassette, and
            # `test_email_notify.py` (also `api`) pins the sender. Without this
            # entry an email_notify.py-only change matched no pattern, fell
            # through to `core`, and NEITHER guard ran on the PR that can break
            # them — the regression was caught only post-merge, on push to
            # main. Same silent-drop shape as the #2938/#3154 entries above.
            "tortoise/email_notify.py",
            "tortoise/quota.py", "tortoise/supabase_control.py",
            "tortoise/selfhost_api.py", "tortoise/session_auth.py",
            # ask-lane server surfaces: test_metering.py + test_selfhost_rest.py
            # live in the api surface — a metering.py/selfhost.py-only PR must
            # select api (core runs no ask tests). transport.py is dual-wired
            # here (in addition to sdk): its only direct unit test is
            # test_metering.py::test_selfhost_transport_exemption.
            "tortoise/metering.py", "tortoise/selfhost.py",
            "tortoise/transport.py",
            # #2938: EventAPI — the append surface `ingest.py`, `mining.py`,
            # `extractor.py`, `m0.py` and `__main__.py` all write through.
            # Without this entry a `tortoise/api.py`-only change matched no
            # pattern, fell through to `core`, and skipped the api-registered
            # tests that pin it (test_api.py, test_attribution_actor.py,
            # test_1162_add_operator_local_svbp.py). Paired with CORE_ALSO:
            # its pinning tests are registered across api, core AND ep, so the
            # named-surface match must not drop `core` (see CORE_ALSO).
            "tortoise/api.py",
            # #3036: tortoise/oauth.py is the hosted OAuth implementation, and
            # its pinning tests are `api`-registered (test_oauth_mcp.py,
            # test_oauth_token_fault.py, test_3036_oauth_retention.py,
            # test_attribution_actor.py, test_user_identity_authority.py) plus
            # `api`+`core` (test_control_plane_offload_3498.py). Without this
            # entry an oauth.py-only change selected no named surface and fell
            # through to `core`, silently skipping ALL of those — the
            # #2938/#3154/#4367 silent-drop class, on the file a retention- or
            # token-flow fix must change. Paired with CORE_ALSO so the
            # core-registered half is not dropped by the named-surface match.
            "tortoise/oauth.py",
            # #4282: `tools/bridge_table.py` GENERATES `docs/product/bridge-table.md`
            # and `test_bridge_table.py` (registered in `api`) is the drift gate
            # that keeps them honest. `tools/` is in NON_PYTHON_PREFIXES, so a
            # generator-only edit selected NO surface (`surfaces: []`, `full:
            # false`) and the gate never ran on precisely the PR that can break
            # it. Named here because a SOURCE_PATTERNS match beats the
            # non-python skip. A docs-only hand-edit of the generated file still
            # skips the matrix by the repo's deliberate docs-PR policy — see
            # tortoise #4454.
            "tools/bridge_table.py",
            # #4282 Phase 0.3: `tools/mcp_rename_table.py` GENERATES
            # `docs/product/mcp-rename-table.md` and `test_mcp_rename_table.py`
            # (registered in `api` + `core`) is the drift gate. Same shape as the
            # 0.1 entry directly above and the same reason: a generator-only edit
            # is swallowed by the flat `tools/` prefix and the gate never runs on
            # the PR that can break it (#4454 covers a docs-only hand-edit).
            "tools/mcp_rename_table.py",
            # #4282 Phase 0.3b: `tools/sdk_rename_table.py` GENERATES
            # `docs/product/sdk-rename-table.md`, and `test_sdk_rename_table.py`
            # (registered in `api` AND `core`) is the drift gate. Same gap as the
            # bridge table above: `tools/` is in NON_PYTHON_PREFIXES, so a
            # generator-only edit selected NO surface and the gate never ran on
            # the PR that can break it. A docs-only hand-edit of the generated
            # file still skips the matrix by the docs-PR policy (tortoise #4454).
            "tools/sdk_rename_table.py",
            # #4282 Phase 0.4 + 1.1: `tools/sdk_surface.py` derives the declared
            # `TortoiseSDK` public surface and GENERATES `config/sdk-surface.json` +
            # `docs/product/sdk-surface-declaration.md`; `test_sdk_surface.py`
            # (registered in `api` AND `core`) is the drift gate. Same gap as the bridge
            # table above: `tools/` is in NON_PYTHON_PREFIXES, so a generator-only edit
            # selected NO surface and the gate never ran on the PR that can break it.
            # A docs-only hand-edit of the generated doc still skips the matrix by the
            # repo's deliberate docs-PR policy (tortoise #4454).
            "tools/sdk_surface.py"),
    # eval (#1349): the probe, LongMemEval/mini-BEIR harnesses, threshold
    # tools, benchmark infra, and the backfill script all produce gate
    # evidence — their tests live in the eval surface (config/ci-surfaces.yml).
    # W2-b (#2098) BPRE trigger (plan §2.1) is wired at the TEST-FILE level
    # instead of here: the write-path benchmark gate file is registered on
    # the api (hosted capture/provenance), core (session_import fallback),
    # ep (dream EP pass), and eval surfaces, so a PR touching any of those
    # source paths runs the replay gate via the matched surface's files
    # without displacing the surface's existing selection.
    "eval": ("tools/longmem_eval/", "tools/mini_beir/",
             "tools/embedder_probe.py", "tools/calibrate_thresholds.py",
             "tools/pair_label_runner.py", "benchmarks/",
             "graph-scripts/backfill_embeddings.py",
             # #3359: the per-session cost report CLI consumes the eval-owned
             # versioned PRICING_MAP (tools/longmem_eval/costing.py) and is
             # exercised by tests/test_capture_cost_measurement.py — without
             # this entry a report-CLI-only change selects NO surface
             # (surfaces=[], full=False) and that test never runs on the PR
             # that owns the launch-gate number (the #3616 pattern).
             "tools/capture_cost_report.py",
             # P2-1 (code review): an embeddings.py/cross_lens.py-only PR must
             # select eval so probe/vector-arm/threshold tests run (they assert
             # the EMBEDDING_MODEL + threshold constants — drift class #1260).
             "tortoise/embeddings.py", "tortoise/cross_lens.py"),
    # core is the fallback for any other python-relevant path
}

# #2938: a SOURCE_PATTERNS match REPLACES the `core` fallback in select() —
# the named surface's file list is more specific than the always-on engine
# set. That is wrong for a source whose pinning tests are registered across
# surfaces: `tortoise/api.py` (EventAPI) is imported at module level by 16
# `core`-registered tests (test_projection, test_extractor, test_m1/m2, the
# de2e* suite, …) plus the api-registered trio, and a named-surface match
# would run only the selected surface's half of them. A path listed here adds
# `core` alongside its matched surface(s) — narrower than promoting the whole
# module to SHARED_MODULES (which forces the full matrix).
# Paths listed here ADD `core` alongside whatever surface they matched.
#
# #4207/#4351: `tools/skip-guard.py` is the file the frozen-nodeid manifest is
# enforced by, and BOTH of its pinning tests (`tests/test_skip_guard.py`,
# `tests/test_ci_expected_manifests.py`) are `core`-registered. `tools/` IS in
# NON_PYTHON_PREFIXES (a tools-only change is treated as non-python-relevant), and
# the file matches no SOURCE_PATTERN either, so a follow-up change to
# `--manifest-only` alone selected only tier-1 smoke — the pin for the code being
# changed would not have run. That is the #1349/#3332/#3616 silent-drop class, on
# the file this PR modifies.
#
# #4069: the temp-dir sweep is test-infra whose guard tests
# (tests/test_tmpdir_sweep.py, tests/test_tmpdir_hygiene.py) are
# core-registered. `_selection_relevant()` consults CORE_ALSO, so
# this entry has a DUAL role: it admits a `tools/` path past the
# flat NON_PYTHON_PREFIXES filter AND, in the match loop below,
# adds `core` and marks the path found — so a tool-only change
# selects `core` instead of the unknown-path fail-closed full
# matrix, and never drops to tier-1 smoke (the #1349/#3332/#3910
# silent-drop class). No TOOL_CARVEOUTS entry is needed: that
# tuple is redundant for any path already listed here.
CORE_ALSO = ("tortoise/api.py", "tortoise/hosted_backup.py", "tools/skip-guard.py",
             "tortoise/projection/edges.py",
             "tools/tmpdir_sweep.py",
             # #3036: oauth.py is pinned by BOTH api-registered tests
             # (test_oauth_mcp.py, test_oauth_token_fault.py, ...) and core
             # (test_control_plane_offload_3498.py), so the SOURCE_PATTERNS
             # `api` match must not drop the core half.
             "tortoise/oauth.py")

# Paths that are NOT python-relevant (docs/config PRs skip the matrix).
NON_PYTHON_PREFIXES = (
    "docs/", "website/", "product/", "legal/", "growth/", "engineering/",
    "finance-accounting/", "menu-bar/", "ux/", "data/", "operations/",
    "capability/", "services/", "integrations/", "apps/", "spike/", "tools/",
    ".ci-checks/", "supabase/",
)

# website/ paths that ARE selection-relevant (#3332).
#
# Superseded by the generic rule in select() (`_selection_relevant`): a path that
# SOURCE_PATTERNS already matches is selection-relevant whatever prefix it sits
# under, so it no longer needs a second hand-maintained tuple. Kept empty as a
# documented tombstone rather than deleted, so the next reader finds the reason
# instead of re-inventing the same broken mirror.
#
# Why the mirror was the wrong shape (twice: #1349 for tools/, #3332 for
# website/): the mirror is only as complete as whoever last edited it, and a
# missing entry fails SILENTLY — the path is filtered to `changed == []`, the PR
# drops to tier-1 smoke, and the guard test written for that exact file never
# runs. SOURCE_PATTERNS is the single source of truth; the ratchet that keeps
# this true is tests/test_ci_selection.py::test_every_source_pattern_is_selectable.
SITE_CARVEOUTS: tuple[str, ...] = ()

# tools/ paths that ARE python-relevant for selection (#1349). The flat
# NON_PYTHON_PREFIXES tuple above includes "tools/", which would swallow
# every tools change before SOURCE_PATTERNS matching (a tools-only PR would
# yield empty changed -> tier-1 smoke). These carve-out paths are re-included
# by the filter expression in select() so tools/longmem_eval/run.py etc. can
# select the eval surface. NOT wholesale tools/ removal: unrelated tools
# changes (e.g. tools/kappa.py) must keep failing closed to the old behavior.
TOOL_CARVEOUTS = (
    "tools/longmem_eval/",
    "tools/mini_beir/",
    "tools/embedder_probe.py",
    "tools/calibrate_thresholds.py",
    "tools/pair_label_runner.py",
    # #2071: the eval-lane ask QA spot-check tools (ask_spotcheck + the
    # consistency/probe harnesses) use the eval judge and own the
    # test_ask_spotcheck_judge.py suite — a spot-check-only change must
    # select the sdk ask-lane surface, not drop to tier-1 smoke.
    "tools/ask_spotcheck.py",
    "tools/ask_spotcheck_consistency.py",
    "tools/ask_spotcheck_probe.py",
    # #3914: the transcript-golden generator's seeder is what
    # tests/test_ask_seed_shape.py + tests/test_ask_regression_llm.py pin —
    # same carve-out as the spot-check harnesses above.
    "tools/gen_ask_transcripts.py",
    # B6 objective 4: the answer-shape instrument — same carve-out reason
    # as gen_ask_transcripts.py above (without it the flat "tools/" prefix
    # silently drops the path to tier-1 smoke).
    "tools/ask_shape_rate.py",
    # #2159 review P2-3: the diff-gate selector itself must never classify
    # as docs-only (the two gated legs would skip AND the wiring pins in
    # tests/test_ci_selection.py would never run on the PR that owns them).
    # No source pattern matches tools/ci_selection.py, so a selector-only PR
    # lands in the unknown-path fail-closed branch -> FULL matrix + both
    # legs — the heaviest but safest gate for the file that owns gating.
    "tools/ci_selection.py",
    # #B7 (#3674): the activation-scorecard cohort roll-up. Its `roll_up`
    # summing logic owns part of tests/test_activation_scorecard.py; without
    # this carve-out a cohort-script-only change selects NO surface and the
    # suite that pins it never runs. No SOURCE_PATTERN matches this path, so it
    # lands in the unknown-path fail-closed branch -> FULL matrix + both legs,
    # which is the safe outcome for a file that a metrics number depends on.
    "tools/activation_cohort.py",
    # #3261: the pre-dispatch collision check (#3061) owns
    # tests/test_collision_preflight.py. Without this carve-out a
    # preflight-only change is swallowed by the flat "tools/" prefix,
    # `changed` comes back empty, and select() takes the docs-only path
    # (surfaces=[] -> tier-1 smoke) — so the file's own guard test never
    # runs on the PR that changes it. The assumption that such a change
    # already "falls back to core" was never true: the early docs-only
    # return bypasses the `if not matched: matched.add("core")` fallback
    # entirely.
    # No SOURCE_PATTERNS entry matches it, so like tools/ci_selection.py
    # it lands in the unknown-path branch -> FULL matrix (fail closed).
    # A narrower core-only mapping is possible but not needed: a
    # collision-check change is rare and fail-closed is the safe default.
    "tools/collision_preflight.py",
    # #4408: the local-branch reaper (tools/branch_reaper.py) owns
    # tests/test_branch_reaper.py. Same silent-drop class as the preflight
    # carve-out above: the flat "tools/" prefix would swallow a reaper-only
    # change, `changed` comes back empty, select() takes the docs-only path and
    # the reaper's mutation tests never run on the PR that changes it. No
    # SOURCE_PATTERNS entry matches it, so it lands in the unknown-path branch
    # -> FULL matrix (fail closed) — the safe default for a destructive-ref tool.
    "tools/branch_reaper.py",
    # #2573: the CI embedder gate (tools/embedder_provision.py) owns
    # tests/test_embedder_provision.py. Same silent-drop class as the
    # preflight carve-out above: no SOURCE_PATTERNS entry matches it, so a
    # change to the gate alone would classify as docs-only (surfaces=[] ->
    # tier-1 smoke) and its own wiring/behaviour tests would never run on the
    # PR that edits it.
    "tools/embedder_provision.py",
    # #4290: the finding-provenance gate (tools/finding_provenance.py) owns
    # tests/test_finding_provenance.py. Same silent-drop class as the
    # collision-preflight carve-out above: the flat "tools/" prefix in
    # NON_PYTHON_PREFIXES swallows the path, `changed` comes back empty, and
    # select() takes the docs-only return — so the gate's own falsification
    # suite would never run on the PR that changes the gate. No SOURCE_PATTERNS
    # entry matches it, so it lands in the unknown-path branch -> FULL matrix
    # (fail closed). Pinned by
    # test_ci_selection.test_finding_provenance_tool_change_fails_closed_to_full.
    "tools/finding_provenance.py",
    # #3827: the embedded-lane evidence harness owns
    # tests/test_embedded_evidence.py. Same silent-drop class as the preflight
    # carve-out above: no SOURCE_PATTERNS entry matches the path, so a
    # harness-only change is swallowed by the flat "tools/" prefix, `changed`
    # comes back empty, and select() takes the docs-only return — the harness's
    # own guard test never runs on the PR that edits the harness. That is the
    # exact "proxy silent in the case it exists to cover" class this harness is
    # written to detect, so it must not apply to the harness itself.
    "tools/embedded_evidence.py",
    # #2718/#4860: the eval-key isolation launcher owns
    # tests/test_run_with_eval_keys.py. Same silent-drop class as the
    # collision-preflight carve-out above: the flat "tools/" prefix in
    # NON_PYTHON_PREFIXES swallows `tools/run-with-eval-keys.sh`, so a
    # wrapper-only change (e.g. a fingerprint-format edit, or a change to the
    # managed-key set) would come back as `changed=[]`, select() would take the
    # docs-only return, and the wrapper's own guard suite would never run on
    # the PR that changed the wrapper. No SOURCE_PATTERNS entry matches a `.sh`
    # path, so it lands in the unknown-path branch -> FULL matrix (fail closed)
    # — the safe default for the file that owns provider-key isolation.
    # Pinned by
    # test_ci_selection.test_run_with_eval_keys_tool_change_fails_closed_to_full.
    "tools/run-with-eval-keys.sh",
)


def _surface_members(value) -> list:
    """A manifest surface value as a member list (#3073).

    ``None`` means "empty surface" (a plausible hand-edit or bad-merge
    artifact) and a non-list scalar is malformed. Both coerce to ``[]``
    rather than crashing every consumer with
    ``TypeError: argument of type 'NoneType' is not iterable`` or, worse,
    iterating a string character-by-character. The drift gate still reports
    the orphans, so an empty surface fails loudly later rather than silently
    at selection time.
    """
    if isinstance(value, (list, tuple)):
        return list(value)
    return []


def _normalize_surfaces(manifest: dict) -> dict:
    """Coerce every surface block to a member list, in place (#3073)."""
    surfaces = manifest.get("surfaces")
    if isinstance(surfaces, dict):
        manifest["surfaces"] = {
            s: _surface_members(v) for s, v in surfaces.items()}
    return manifest


def load_manifest() -> dict:
    import yaml  # local import (uv provides pyyaml via the dev group)
    return _normalize_surfaces(yaml.safe_load(MANIFEST.read_text()))


def classify_test_file(name: str, manifest: dict) -> str | None:
    """Return the surface owning a test file.

    ``name`` is the ``tests/``-relative path (e.g. ``longmem_eval/test_vector_arm.py``)
    or a bare basename (backward-compat: the manifest was basename-keyed
    before subdir registration). Both forms are checked against each
    surface's entries, so subdir files classify correctly and legacy
    top-level basename entries keep working.
    """
    base = name.rsplit("/", 1)[-1]
    for surface, files in manifest["surfaces"].items():
        members = _surface_members(files)
        if name in members or base in members:
            return surface
    return None


# #2147/#2148 (2026-09-02-ci-audit F1/F2): surface -> slow-leg / carve-out
# file ownership maps. The test-slow and test-carve-out jobs were the two
# diff-unaware python-ci legs (docs-only PR #2132 paid 29m43+29m08 slow +
# 23m31 carve-out); select() now emits slow_run / slow_selected /
# carve_out_run on every return path so the workflow can diff-gate them.
def slow_leg_by_surface(manifest: dict) -> dict[str, set[str]]:
    """Surface -> slow files that run in test-slow (slow minus carve-out).

    The 6 slow carve-out files (test_reaper et al.) run in the dedicated
    URI-unset carve-out job (epic #1647 E2E-4), never the docker slow legs
    — the test-slow committed leg set is exactly slow_files - carve_out
    (pinned by the workflow's drift-guard step, #1471).

    First-match semantics (#2159 review P2-2): classify_test_file returns
    the FIRST surface whose patterns match (dict order api, battery, core,
    ep, onboarding, sdk, classify, eval). A slow/carve file registered
    under two surfaces therefore owns to the first — a tier-2 PR touching
    ONLY the second surface will not run it (full runs stay the backstop).
    test_diff_gate_keys_emitted_on_every_return_path pins every
    slow_selected subset to slow_files - carve_out; the tier-2 intersection
    tests pin the per-surface splits."""
    slow = set(manifest.get("slow_files", []))
    carve = set(manifest.get("carve_out", []))
    by_surface: dict[str, set[str]] = {}
    for f in sorted(slow - carve):
        by_surface.setdefault(classify_test_file(f, manifest), set()).add(f)
    return by_surface


def carve_out_by_surface(manifest: dict) -> dict[str, set[str]]:
    """Surface -> carve-out files it owns (the E2E-4 embedded set)."""
    by_surface: dict[str, set[str]] = {}
    for f in sorted(manifest.get("carve_out", [])):
        by_surface.setdefault(classify_test_file(f, manifest), set()).add(f)
    return by_surface


def _full_selection(manifest: dict, slow: set[str]) -> dict:
    """push/schedule/shared-module/unknown-path selection (tier 3).

    The trunk + nightly backstop: full matrix AND both diff-gated legs run
    (test-slow + test-carve-out) — main can never lose slow/carve-out
    coverage to a PR-shape gate."""
    carve = set(manifest.get("carve_out", []))
    return {"surfaces": list(manifest["surfaces"]), "full": True,
            "test_files": "ALL", "slow_files": sorted(slow),
            # #2147/#2148: full selection always runs both legs; slow_selected
            # carries the whole committed leg set (slow - carve-out).
            "slow_run": True, "carve_out_run": True,
            "slow_selected": sorted(slow - carve)}


def select(changed_files: list[str], event: str, manifest: dict) -> dict:
    slow = set(manifest.get("slow_files", []))
    # #1371: slow files run ONLY in the test-slow job — never in the fast
    # gate's tier-1/tier-2 selections (they are already covered there).
    # Every return path carries slow_files so the workflow's changes job can
    # always emit it (a missing key would KeyError the nightly/schedule run).
    # #2147/#2148: every return path ALSO carries slow_run / slow_selected /
    # carve_out_run (the test-slow + test-carve-out diff-gate contract).
    if event in ("push", "schedule"):
        return _full_selection(manifest, slow)

    tier1 = set(manifest.get("tier1", [])) - slow
    # Filter out non-python-relevant paths — but a path that a SOURCE_PATTERNS
    # entry already matches is selection-relevant BY DEFINITION, whatever prefix
    # it sits under (#3332). Mirroring those paths into a second hand-maintained
    # tuple (the #1349 TOOL_CARVEOUTS shape, and this PR's own first attempt at
    # SITE_CARVEOUTS) drifts: a missing entry is silent, and the guard test
    # written for that exact file never runs. SOURCE_PATTERNS is the single
    # source of truth; test_every_source_pattern_is_selectable is the ratchet.
    def _selection_relevant(path: str) -> bool:
        return any(
            path.startswith(p)
            for surface, pats in SOURCE_PATTERNS.items()
            if surface != "core"
            for p in pats
        ) or path.startswith(CORE_ALSO)

    changed = [c for c in changed_files
               if c and (not c.startswith(NON_PYTHON_PREFIXES)
                         or _selection_relevant(c)
                         or c.startswith(TOOL_CARVEOUTS)
                         or c.startswith(SITE_CARVEOUTS))]
    if not changed:
        # docs-only PR -> tier 1 (curated smoke) only; no slow/carve surface
        # is touched, so both diff-gated legs skip (#2147/#2148).
        return {"surfaces": [], "full": False, "test_files": sorted(tier1),
                "slow_files": sorted(slow),
                "slow_run": False, "carve_out_run": False,
                "slow_selected": []}

    # Shared module -> full
    if any(c.startswith(SHARED_MODULES) for c in changed):
        return _full_selection(manifest, slow)

    matched: set[str] = set()
    unknown: list[str] = []
    for c in changed:
        found = False
        for surface, pats in SOURCE_PATTERNS.items():
            if surface == "core":
                continue
            if any(c.startswith(p) for p in pats):
                matched.add(surface)
                found = True
        # #2938: a CORE_ALSO path keeps `core` even though a named surface
        # matched above (see CORE_ALSO) — otherwise its core-registered
        # pinners silently drop out of the selection.
        if any(c.startswith(p) for p in CORE_ALSO):
            matched.add("core")
            found = True
        if not found:  # noqa: SIM102
            if c.startswith("tortoise/") or c.startswith("tests/") or \
               c.startswith("graph-scripts/") or c.startswith("config/") or \
               c.startswith("validation/") or c.startswith("packs/"):
                matched.add("core")  # engine/registry code -> core surface
                found = True
        if not found:
            unknown.append(c)
    if unknown:
        # New/unknown path -> fail-closed full matrix (scope v5 decision 1)
        return _full_selection(manifest, slow)

    # Test-file changes select their owning surface
    for c in changed:
        if c.startswith("tests/") and c.endswith(".py"):
            s = classify_test_file(c[len("tests/"):], manifest)
            if s:
                matched.add(s)

    if not matched:
        matched.add("core")

    surfaces = sorted(matched)
    files = set(tier1)  # tier 2 = tier 1 ∪ surface-matched (scope v5 dec 5)
    for s in surfaces:
        files.update(_surface_members(manifest["surfaces"].get(s)))
    files -= slow  # #1371: slow files never run in the fast gate
    # #1988: carve-out (embedded-only) files run in the dedicated carve-out
    # job — on tier-2 PR legs the fast-matrix process runs everything embedded
    # (URI unset) and exhausts its redislite spawn budget before the late
    # embedded suites (RedisLiteServerStartError); the carve-out job gives
    # them a fresh process. The carve-out job now runs on PRs too.
    carve = set(manifest.get("carve_out", []))
    files -= carve
    # #2147/#2148 tier-2: test-slow runs the slow files owned by the matched
    # surfaces (slow_selected — carve-out files excluded: they run in the
    # carve-out job when it triggers, never the docker slow legs); test-carve-
    # out runs when a matched surface owns any carve-out file (embedded /
    # daemon / registry / guard coverage). Docs/website/config-only PRs
    # (surfaces == []) already returned above with both legs skipped.
    slow_by_surface = slow_leg_by_surface(manifest)
    carve_by_surface = carve_out_by_surface(manifest)
    slow_selected: set[str] = set()
    carve_out_run = False
    for s in surfaces:
        slow_selected.update(slow_by_surface.get(s, set()))
        if s in carve_by_surface:
            carve_out_run = True
    return {"surfaces": surfaces, "full": False, "test_files": sorted(files),
            "slow_files": sorted(slow),
            "slow_run": bool(slow_selected), "carve_out_run": carve_out_run,
            "slow_selected": sorted(slow_selected)}


def integrity(manifest: dict) -> list[str]:
    """All tests/**/test_*.py must be in the manifest — the drift trap.

    Recursive (rglob) so subdir test files are drift-checked too; the
    tests/e2e/ prefix is deliberately SKIPPED (#1349 — 4 direct + 13 hosted
    files, covered by welcome-e2e-monitor + legal-e2e jobs + ENV_BROKEN_FILES).
    Files are classified by their tests/-relative path so subdir files match
    the manifest's subdir keys.
    """
    missing = []
    for f in sorted(TESTS_DIR.rglob("test_*.py")):
        rel = f.relative_to(TESTS_DIR)
        if rel.parts[0] == "e2e":
            continue
        if classify_test_file(str(rel), manifest) is None:
            missing.append(str(rel))
    return missing


def duplicate_entries(manifest: dict) -> list[str]:
    """#2913: names listed more than once WITHIN one surface.

    `select()` unions surfaces, so a same-surface duplicate is invisible to
    selection and to :func:`integrity` (which only asks "is it classified?").
    Surfaced as a non-fatal `--integrity` note rather than a gate failure — the
    duplicate is a manifest edit to clean up, and the gate must stay green while
    it is. Cross-surface (dual) registration is deliberate; only same-surface
    repeats are reported.

    Each offending name is reported ONCE however many times it repeats, so the
    note's count is a count of distinct problems.
    """
    dupes: list[str] = []
    for surface, files in manifest.get("surfaces", {}).items():
        seen: set[str] = set()
        reported: set[str] = set()
        for f in files or ():
            if f in seen and f not in reported:
                dupes.append(f"{surface}: {f}")
                reported.add(f)
            seen.add(f)
    return dupes


def unlisted_tests(tests_dir: Path, manifest: dict) -> list[str]:
    """Top-level tests/test_*.py absent from every surface in the manifest.

    Kept from origin/main's refactor — callers (workflow matrix checks)
    use the top-level glob; :func:`integrity` uses the recursive rglob form.
    """
    missing = []
    for f in sorted(tests_dir.glob("test_*.py")):
        if classify_test_file(f.name, manifest) is None:
            missing.append(f.name)
    return missing


def register_tests(manifest_path: Path, tests_dir: Path, surface: str,
                   manifest: dict) -> list[str]:
    """Auto-register unlisted test files under `surface` (#1429).

    Text-preserving: appends `  - name.py` in alphabetical position inside the
    surface's list block, keeping the hand-curated manifest format. Idempotent.
    Returns the files that were registered (empty when already clean). The
    default surface is `core` — the selection logic's own fallback; the shared
    / unknown-path / push-to-main rules still expand to the full matrix, so a
    misclassified new test keeps running on every broad change.

    Insertion lands *between real entry lines* (#2913): `pos` indexes the entry
    list (comment lines excluded), so it is converted to a physical line via the
    entry immediately before it — never used as a raw line offset, which would
    drop the new entry inside a comment run and detach the run from the entries
    it documents.
    """
    missing = unlisted_tests(tests_dir, manifest)
    if not missing:
        return []
    text = manifest_path.read_text()
    # #3073: a manifest whose final line lacks a newline would concatenate the
    # appended entry onto it (`  - test_b.py  - test_c.py`, malformed YAML).
    # Normalise the terminator before splitting so every insertion point is a
    # line boundary.
    if text and not text.endswith(NL):
        text += NL
    lines = text.splitlines(keepends=True)
    # locate the surface block: "  <surface>:" then "  - name" lines (strip
    # any trailing inline comment from the key)
    block_start = None
    for i, ln in enumerate(lines):
        key = ln.split("#", 1)[0].rstrip()
        if key == f"  {surface}:":
            block_start = i
            break
    if block_start is None:
        # append the new surface block at the end of the surfaces: mapping.
        # The surfaces are the only 2-space-indented mapping keys, followed by
        # the top-level tier1: key (column 0) — that is the reliable boundary.
        anchor = next((i for i, ln in enumerate(lines) if ln.startswith("tier1:")), len(lines))
        new_block = [f"  {surface}:{NL}"] + [f"  - {n}{NL}" for n in missing]
        lines[anchor:anchor] = new_block
        manifest_path.write_text("".join(lines))
        return missing

    # Scan the block (until the next "  x:" / non-list line), tracking each
    # real entry's PHYSICAL line index. Comments are prose about the group that
    # follows and must never be split by an insertion (#2913).
    end = block_start + 1
    entry_lines: list[int] = []
    while end < len(lines) and (lines[end].startswith("  - ") or lines[end].lstrip().startswith("#")):
        if lines[end].startswith("  - "):
            entry_lines.append(end)
        end += 1
    names = [lines[i].strip()[2:].split("#", 1)[0].rstrip() for i in entry_lines]
    # A file already present in THIS surface is a no-op, reported: writing it
    # again would duplicate the entry, and select() unions surfaces so a
    # same-surface duplicate is invisible downstream (#2913).
    already = [n for n in missing if n in set(names)]
    to_add = [n for n in missing if n not in set(names)]
    if already:
        print(f"ℹ️  already registered under {surface}: {already}")
    # INSERT-ONLY: place each new name at its alphabetical position among
    # the existing entries; never re-order pre-existing lines (keeps the
    # diff surgical — normalizing the whole block would churn the manifest).
    for n in sorted(to_add):
        pos = 0
        while pos < len(names) and names[pos] < n:
            pos += 1
        # `pos` indexes the entry list; convert to a PHYSICAL line by inserting
        # right after the preceding entry (or at the top of the block when the
        # name sorts first). This keeps comment runs contiguous and attached to
        # the entries they describe — the bug was `block_start + 1 + pos`, which
        # counted comment lines and landed inside a run.
        insert_at = block_start + 1 if pos == 0 else entry_lines[pos - 1] + 1
        lines[insert_at:insert_at] = [f"  - {n}{NL}"]
        names.insert(pos, n)
        entry_lines.insert(pos, insert_at)
        for j in range(pos + 1, len(entry_lines)):
            entry_lines[j] += 1
    if to_add:
        manifest_path.write_text("".join(lines))
    return to_add


def register(manifest_path: Path, tests_dir: Path, surface: str) -> list[str]:
    """Load + register in one call (CLI entry)."""
    import yaml
    manifest = _normalize_surfaces(yaml.safe_load(manifest_path.read_text()))
    return register_tests(manifest_path, tests_dir, surface, manifest)


# #1472: files excluded from the fast push legs by construction (they cannot
# run in THIS job — no live redis on 6379 for test_agent_signup; tests/e2e is
# excluded by construction since unlisted_tests only globs top-level files).
ENV_BROKEN_FILES = {"test_agent_signup.py"}


def carve_out_files(manifest: dict) -> set[str]:
    """Epic #1647 Task 9 (P3): the embedded carve-out set.

    The carve-out tests run embedded BY DESIGN (E2E-4) in the dedicated
    URI-unset job (TORTOISE_TEST_CARVE_OUT=1) — they are excluded from the
    docker fast legs AND the docker test-slow legs, so the docker lanes
    create ~zero redislite servers (E2E-7 orphan assert ≈ 0). The set
    mirrors tests/_embedded.TEST_NO_REDIRECT_STEMS (pinned by
    tests/test_ci_selection.py + tests/test_markers.py)."""
    return set(manifest.get("carve_out", []))


def fast_pool(manifest: dict) -> list[str]:
    """#3400: the full-matrix fast pool — every manifest-classified file that
    is not slow, env-broken, or carve-out. Single source of truth for the
    push halves (push_legs) AND the durations-coverage guard, so the two can
    never disagree about which files need a weight."""
    slow = set(manifest.get("slow_files", []))
    carve_out = carve_out_files(manifest)
    classified = set()
    for s, files in manifest["surfaces"].items():  # noqa: B007
        classified.update(files)
    classified.update(manifest.get("tier1", []))
    classified.update(slow)
    return sorted(f for f in classified
                  if f not in slow and f not in ENV_BROKEN_FILES
                  and f not in carve_out)


def push_legs(manifest: dict) -> dict:
    """#1472: partition every manifest-classified file into exactly one push
    leg (half_a / half_b / slow / env_broken / carve_out), duration-balanced
    across the two fast halves (#3400).

    Single source of truth for the workflow's push matrix: registration in
    the manifest is sufficient — no manual matrix edit. Returns .py-less
    names (the workflow's matrix format). push_extra (bench files, not
    classifiable as top-level surfaces) appends to half b. Epic #1647
    Task 9: carve_out files are excluded from every docker leg and emitted
    as their own leg (the URI-unset carve-out job's file list).
    """
    slow = set(manifest.get("slow_files", []))
    carve_out = carve_out_files(manifest)
    fast = fast_pool(manifest)
    # #3400: pack the push halves by measured duration (#1473 LPT) instead of
    # the duration-blind index-parity split this used to be (`fast[0::2]` /
    # `fast[1::2]`). Parity on the real pool leaves a tilt far above the ratio
    # below — the pool's cost is not index-uniform — and blew the 55m watchdog;
    # LPT is deterministic (ties break on name) and balances the same pool.
    # split_fast_gate returns `tests/`-prefixed names;
    # the workflow's matrix format is bare, so strip the prefix.
    fast_a, fast_b = split_fast_gate(fast,
                                     _durations_map(manifest))
    half_a = [f[len("tests/"):] for f in fast_a]
    half_b = [f[len("tests/"):] for f in fast_b]
    # #1485: distribute push_extra (bench files) evenly across the halves.
    # Durations now drive the balance (#3400), but even spreading keeps this
    # neutral rather than dumping the whole bench set on one half.
    for i, f in enumerate(manifest.get("push_extra", [])):
        (half_a if i % 2 == 0 else half_b).append(f.replace(".py", ""))
    strip = lambda xs: sorted(x.replace(".py", "") for x in xs)  # noqa: E731
    return {"half_a": strip(half_a), "half_b": strip(half_b),
            # Epic #1647 Task 9: slow carve-out files (test_reaper et al.)
            # run in the URI-unset carve-out job, never the docker slow legs.
            "slow": strip(slow - carve_out),
            "env_broken": sorted(ENV_BROKEN_FILES),
            "carve_out": strip(carve_out)}


def leg_coverage_issues(manifest: dict) -> list[str]:
    """#1472 reverse drift: every classified file in exactly one push leg."""
    slow = set(manifest.get("slow_files", []))
    carve_out = carve_out_files(manifest)
    legs = push_legs(manifest)
    half_a = {f + ".py" for f in legs["half_a"]}
    half_b = {f + ".py" for f in legs["half_b"]}
    fast = half_a | half_b
    issues = []
    overlap = fast & slow
    if overlap:
        issues.append(f"fast/slow overlap: {sorted(overlap)}")
    if fast & ENV_BROKEN_FILES:
        issues.append(f"fast/env-broken overlap: {sorted(fast & ENV_BROKEN_FILES)}")
    if slow & ENV_BROKEN_FILES:
        issues.append(f"slow/env-broken overlap: {sorted(slow & ENV_BROKEN_FILES)}")
    # Epic #1647 Task 9: carve-out files must never ride the docker legs
    # (fast OR slow) — they are the E2E-4 embedded surface only. The config
    # sets MAY overlap (test_reaper et al. are slow AND carve-out); the LEGS
    # must not.
    if fast & carve_out:
        issues.append(f"carve-out file leaked into a fast leg: {sorted(fast & carve_out)}")
    slow_leg = {f + ".py" for f in legs["slow"]}
    if slow_leg & carve_out:
        issues.append(f"carve-out file leaked into the slow legs: {sorted(slow_leg & carve_out)}")
    if carve_out & ENV_BROKEN_FILES:
        issues.append(f"carve-out/env-broken overlap: {sorted(carve_out & ENV_BROKEN_FILES)}")
    classified = set()
    for s, files in manifest["surfaces"].items():  # noqa: B007
        classified.update(files)
    classified.update(manifest.get("tier1", []))
    classified.update(slow)
    for f in manifest.get("push_extra", []):
        if f in classified:
            issues.append(f"push_extra {f} is a classified top-level file (move it into a surface)")
    for f in sorted(ENV_BROKEN_FILES):
        if f not in classified:
            issues.append(f"env-broken {f} is not classified in the manifest")
    # carve-out files must be classified (they run in the carve-out job, which
    # keys off the config list) and must exist on disk (dead entries drift).
    for f in sorted(carve_out):
        if f not in classified:
            issues.append(f"carve-out {f} is not classified in any surface")
        if f in slow:
            continue  # slow carve-out files (test_reaper et al.) are legit
        if not (TESTS_DIR / f).exists():
            issues.append(f"carve-out {f} has no tests/{f} (dead entry)")
    return issues


def workflow_matrix_issues(workflow_path: str, manifest: dict) -> list[str]:
    """#1472: the workflow's matrix rows must come from the derivation, never
    re-hardcoded lists; ENV_BROKEN_FILES must not be duplicated in env."""
    import yaml
    issues = []
    try:
        wf = yaml.safe_load(open(workflow_path))  # noqa: SIM115
    except Exception as exc:
        return [f"cannot parse workflow {workflow_path}: {exc}"]
    inc = wf.get("jobs", {}).get("test", {}).get("strategy", {}).get("matrix", {}).get("include", [])
    for row in inc:
        files = str(row.get("files", ""))
        # #1472 regression fix: the matrix rows consume the SPACE-JOINED
        # matrix_a/matrix_b outputs directly (fromJSON(...) yields a JS array
        # that renders as "Array" in the shell `for f in ${{ matrix.files }}`
        # loop — every full-matrix run collected tests/Array.py).
        if not files.startswith("${{ needs.changes.outputs.matrix_"):
            issues.append(f"matrix row {row.get('half')} files is not derived (matrix_* output)")
    if "ENV_BROKEN_FILES" in wf.get("env", {}):
        issues.append("workflow env re-defines ENV_BROKEN_FILES (single source is ci_selection.py)")
    return issues


def slow_file_issues(manifest: dict) -> list[str]:
    """#1371: slow_files must be non-empty, classified, and never in tier1.

    Fail-closed so the fast gate can never silently drag a slow file back in
    (the #1260/#1270 drift class) or drop one from test-slow coverage.
    """
    issues: list[str] = []
    slow = manifest.get("slow_files", [])
    if not slow:
        issues.append("slow_files is empty (must list the test-slow files)")
    tier1 = set(manifest.get("tier1", []))
    for f in slow:
        if classify_test_file(f, manifest) is None:
            issues.append(f"slow file {f} is not in any surface (unclassified)")
        if f in tier1:
            issues.append(f"slow file {f} is also in tier1 (fast gate leak)")
    return issues


def parse_matrix_halves(workflow_text: str) -> dict[str, list[str]]:
    """#1266: extract the test job's (a)/(b) matrix halves from python-ci.yml.

    The halves are folded scalars (`files: >-`) with bare file names
    (no .py) — the run step maps them to `tests/<name>.py` and `bench/*`.
    Returns {"a": [...], "b": [...]} — empty when the parse fails so callers
    can fail closed on "workflow changed shape" instead of silently passing.
    """
    halves: dict[str, list[str]] = {}
    for m in re.finditer(r"- half: ([ab])\n\s+files: >-\n\s+([^\n]+)\n", workflow_text):
        halves[m.group(1)] = m.group(2).split()
    return halves


def workflow_halves_issues(manifest: dict, halves: dict[str, list[str]],
                           tests_dir: Path | None = None) -> list[str]:
    """#1266: fail-closed checks tying the workflow's matrix halves to the
    manifest — the fast gate must never leak a slow file, carry a file that
    left the manifest, list a dead file, run a file twice, or tilt.

    bench/* entries are exempt (they live outside tests/ and the manifest).
    """
    issues: list[str] = []
    if not halves:
        return ["no matrix halves found in python-ci.yml (workflow parse failure)"]
    tests_dir = tests_dir or TESTS_DIR
    slow = set(manifest.get("slow_files", []))
    all_manifest = set()
    for fs in manifest["surfaces"].values():
        all_manifest.update(fs)
    # bare name -> .py name (halves store bare names, manifest stores .py)
    by_bare = {f[:-3]: f for f in all_manifest}
    slow_bare = {f[:-3] for f in slow}
    seen: set[str] = set()
    for half, files in halves.items():
        for f in files:
            if f.startswith("bench/"):
                continue
            if f in slow_bare:
                issues.append(f"slow file {f}.py leaked into half {half} (fast gate leak — test-slow only, #1266)")
            elif f not in by_bare:
                issues.append(f"half {half} entry {f} is not in any manifest surface (unclassified drift, #1266)")
            if not (tests_dir / f"{f}.py").exists():
                issues.append(f"half {half} entry {f} has no tests/{f}.py (dead entry, #1266)")
            if f in seen:
                issues.append(f"half entry {f} is in BOTH halves (double-run, #1266)")
            seen.add(f)
    counts = {h: len(fs) for h, fs in halves.items()}
    # #3400: the balance invariant is DURATION once measured weights exist.
    # LPT packs by weight, so a heavy file dumped entirely on one half is
    # caught even when the counts look even — and a correct duration pack may
    # legitimately carry very different counts.
    # The ±3 count check would red that correct split, so it now applies only
    # to manifests with no durations map at all (e.g. the small test
    # fixtures, or a repo that has not adopted durations).
    durations = _durations_map(manifest)
    if durations:
        weights = {h: sum(_duration_weight(durations.get(
                            f if f.endswith(".py") else f + ".py"))
                          for f in fs)
                   for h, fs in halves.items()}
        lo, hi = min(weights.values()), max(weights.values())
        # P2 (#3407 review): compute the ratio BEFORE the f-string. `lo <= 0`
        # short-circuits the comparison but the message still evaluated
        # `hi / lo`, so the one branch written to CATCH a zero-weight half died
        # with ZeroDivisionError while formatting its own diagnosis. A
        # single-sided pack is reachable (a 1-file pool, or an all-zero
        # measured map) and this is the only check that catches it —
        # `leg_coverage_issues()` and `fast_files_absent_from_halves()` both
        # pass when one half is empty.
        ratio = float("inf") if lo <= 0 else hi / lo
        if lo <= 0 or ratio > HALF_DURATION_IMBALANCE_RATIO:
            issues.append(
                f"matrix halves duration-imbalanced: "
                f"{ {h: round(w / 60, 1) for h, w in weights.items()} } min "
                f"(ratio {ratio:.2f}x, tolerance "
                f"{HALF_DURATION_IMBALANCE_RATIO:.2f}x) — rebalance the "
                f"durations map (#3400)")
    elif abs(counts.get("a", 0) - counts.get("b", 0)) > HALF_IMBALANCE_TOLERANCE:
        issues.append(
            f"matrix halves imbalanced: a={counts.get('a', 0)} vs "
            f"b={counts.get('b', 0)} (tolerance ±{HALF_IMBALANCE_TOLERANCE}) — "
            "rebalance before adding more files (#1266)")
    return issues


def fast_files_absent_from_halves(manifest: dict, halves: dict[str, list[str]]) -> list[str]:
    """#1266 (informational): manifest fast files that are in NO half — the
    full-matrix coverage hole. Slow files, bench/*, and the epic #1647
    carve-out set (their leg is `carve_out`) are excluded. Kept as
    a warning (not fail-closed): closing it would push 100+ files into the
    fast gate and blow the watchdog budget (see the scoping doc).
    """
    slow = set(manifest.get("slow_files", []))
    carve_out = carve_out_files(manifest)
    fast = set()
    for fs in manifest["surfaces"].values():
        fast.update(fs)
    fast -= slow
    fast -= carve_out
    halfset = {f for fs in halves.values() for f in fs}
    return sorted(f for f in fast if f[:-3] not in halfset)


def _duration_weight(value, default: float = DEFAULT_FAST_WEIGHT) -> float:
    """#3407 review: a malformed `durations` value must never crash a consumer.

    `split_fast_gate`'s sort key negates the weight, so a `None`/string value
    raised `TypeError: bad operand type for unary -` deep inside the sort —
    which killed `--integrity` in `leg_coverage_issues()` *before*
    `duration_issues()` was ever called, so the gate that exists to NAME the bad
    entry tracebacked instead. A `NaN` was worse: every comparison is False, so
    it passed both the value check and the imbalance check and silently
    produced a maximally single-sided pack with a green exit.

    Coercing to the default here means every consumer degrades safely, while
    `duration_issues()` still names the offending entry and fails the gate.
    `bool` is excluded explicitly (`isinstance(True, int)` is True).

    #3407 review cycle 3: the finiteness probe must be TOTAL. `math.isfinite`
    converts to float, so an int beyond float range (>=309 digits) raised
    `OverflowError` — i.e. the probe introduced to stop a crash could itself
    crash. A negative duration is impossible data and is likewise coerced.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    try:
        finite = math.isfinite(value)
    except OverflowError:  # an int beyond float range
        finite = False
    if not finite or value < 0:
        return default
    return value


def split_fast_gate(files, durations: dict, default_weight: float = DEFAULT_FAST_WEIGHT):
    """#1473: LPT greedy pack of the selected fast-gate files across halves
    a/b by measured duration — deterministic (ties -> a; assignment order).
    Raises ValueError on non-list input (guards the 'ALL' full-mode string).
    """
    if not isinstance(files, list):
        raise ValueError(f"split_fast_gate expects a list, got {type(files).__name__}")
    weighted = []
    for f in files:
        name = f[len("tests/"):] if f.startswith("tests/") else f
        weighted.append((name, _duration_weight(
            durations.get(name, default_weight), default_weight)))
    a, b = [], []
    ta = tb = 0.0
    for name, w in sorted(weighted, key=lambda x: (-x[1], x[0])):
        if ta <= tb:
            a.append("tests/" + name)
            ta += w
        else:
            b.append("tests/" + name)
            tb += w
    return a, b


def _durations_map(manifest: dict) -> dict:
    """`durations` as a mapping, or `{}` — never a non-mapping. (#3407 c4)

    `None`/absent is the documented "this repo has not adopted the duration
    gate" state and collapses to `{}` (a PASS, per `duration_coverage_issues`).
    A non-mapping is malformed and is NAMED by `duration_issues` before it gets
    here; this exists so a PRODUCER path (`--split`) can never crash either.
    """
    raw = manifest.get("durations")
    return raw if isinstance(raw, dict) else {}


def duration_issues(manifest: dict) -> list[str]:
    """#1473/#4712: every durations key must be a CLASSIFIED test file.

    Slow and carve-out lane keys carry their measured cost here too, so a cost
    regression in a lane that exists *because* it is expensive is visible.
    Values must additionally be numeric and finite.
    """
    issues = []
    # #3407 review cycle 4 (pre-existing): this site and `--split` below used
    # `.get("durations", {})`, which returns a present-but-NULL `durations:` key
    # as `None` — the empty-map state `duration_coverage_issues` documents as
    # "NOT a failure" — and crashed with a raw TypeError instead.
    #
    # The precise predicate: `None`/absent means "this repo has not adopted the
    # duration gate" and is a PASS. ANY other non-mapping (`0`, a string, a
    # list) is a malformed declaration and must be NAMED — collapsing it into
    # the empty case with `or {}` would have turned a wrong crash into a silent
    # wrong pass.
    raw = manifest.get("durations")
    if raw is not None and not isinstance(raw, dict):
        return [f"durations is not a mapping: {type(raw).__name__}"]
    durations = _durations_map(manifest)
    classified = set()
    for s, files in manifest["surfaces"].items():  # noqa: B007
        classified.update(files)
    classified.update(manifest.get("tier1", []))
    for name in durations:
        if name not in classified:
            issues.append(f"durations key {name} is not classified in the manifest")
        # P2 (#3407 review): validate the VALUE, not just the key. Both guards
        # iterated keys only, so a hand-edit typo in a now-505-line map passed
        # `--integrity` silently and then crashed `push_legs` with a TypeError
        # inside `split_fast_gate`'s sort key — the gate's whole job is to name
        # the bad entry instead of tracebacking on it. `bool` is excluded
        # explicitly: it is an `int` subclass and would slip through.
        v = durations[name]
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            issues.append(f"durations value for {name} is not numeric: {v!r}")
        else:
            # #3407 review P2: a NaN passed the type check AND was invisible to
            # the imbalance guard (every comparison is False), so it produced a
            # maximally single-sided pack with a green exit. Type-checking is
            # necessary but not sufficient — finiteness is the real predicate.
            # Cycle 3: the probe must be TOTAL (`math.isfinite` raises
            # OverflowError on an int beyond float range), and a negative
            # duration is impossible data that otherwise exited 0.
            try:
                finite = math.isfinite(v)
            except OverflowError:
                finite = False
            if not finite:
                issues.append(f"durations value for {name} is not finite: {v!r}")
            elif v < 0:
                issues.append(f"durations value for {name} is negative: {v!r}")
    return issues


def duration_coverage_issues(manifest: dict,
                             threshold: float = DURATION_COVERAGE_MIN) -> list[str]:
    """#3400: the `durations` map must cover (almost) the whole fast pool.

    A fast file with no measured duration is packed at DEFAULT_FAST_WEIGHT,
    so a mostly-empty map silently turns `split_fast_gate` back into a
    count-based pack — the exact rot that left 15 weights for 500 fast files
    (#1266/#1473) and left the push halves duration-blind. Fail-closed once
    the map is populated; an ABSENT or EMPTY map is NOT a failure, so a repo
    that has not adopted durations is never hard-failed by this guard.
    """
    durations = _durations_map(manifest)
    if not durations:
        return []
    fast = fast_pool(manifest)
    if not fast:
        return []
    missing = sorted(f for f in fast if f not in durations)
    coverage = (len(fast) - len(missing)) / len(fast)
    if coverage < threshold:
        return [
            f"durations coverage {coverage:.1%} "
            f"({len(fast) - len(missing)}/{len(fast)} fast files) is below the "
            f"{threshold:.0%} floor — {len(missing)} file(s) pack at the flat "
            f"{DEFAULT_FAST_WEIGHT}s default, so the push split is effectively "
            f"count-based (#3400). Refresh config/ci-surfaces.yml `durations` "
            f"from tools/ci_timing.py / the CI junit artifacts. Example: "
            f"{missing[:5]}"
        ]
    return []


# ── #2938: surface audit (report-only, non-blocking) ─────────────────────
#
# `config/ci-surfaces.yml` membership is hand-curated: a surface's members are
# the test files that should RUN when that surface's source changes, which is
# not the same thing as "the files that reference that surface's source".
# Deriving membership mechanically was rejected (#2938: it proposed 326/562
# file moves, 28 out of `onboarding`, and emptied `classify`) because a naive
# import scan cannot resolve `from tortoise import X`, a source path no
# SOURCE_PATTERNS entry maps, fixture/HTTP indirection, or human intent. This
# mode derives nothing: it prints the mismatches, with evidence, for a human.
#
# Pin-resolution rules — each gap the mechanical attempt hit is handled (or
# explicitly surfaced):
#   (a) `from tortoise import X` / `import tortoise.X` -> `tortoise/X.py` or
#       `tortoise/X/__init__.py`, accepted only when the file exists on disk
#       (a class re-exported from the package root is not a submodule). The
#       bare package roots `tortoise` / `tests` are never pins themselves:
#       the root re-exports many surfaces and every test imports it.
#   (b) a pinned source path that no SOURCE_PATTERNS entry maps (the #2938
#       `tortoise/api.py` case, since fixed by mapping it) is reported under
#       "uncovered source paths" rather than silently binned as `core` (the
#       selection fallback), naming the pinning files and the surfaces they
#       are registered under. A path named after a surface
#       (`tortoise/<surface>.py`) is additionally called out under
#       "SOURCE_PATTERNS coverage gaps".
#   (c) string references: every non-docstring string literal is scanned for
#       path-like tokens (a subprocess argv, a Path(...) literal, a path read
#       from disk); evidence is rendered quoted (`file <- "path/string"`).
#   (d) fixture/helper indirection: a test that imports another module under
#       tests/ inherits that module's pins transitively (cycle-safe), EXCEPT
#       helpers in SHARED_MODULES (conftest.py, fake_control_plane.py) —
#       those already force the full matrix, so following them would make
#       every test "pin" everything. Helper-derived evidence is tagged
#       `(via tests/helper.py)` so direct and indirect evidence are distinct.
# SHARED_MODULES are excluded from pins entirely (a change to them forces the
# full matrix, so a file importing sdk.py is not a mismatch).
#
# Still undecided by construction (kept visible, never hidden):
#   - `classify` and `core` have no SOURCE_PATTERNS entry, so "pins nothing
#     from this surface" is undefined for them; the report says so and skips
#     removal analysis instead of proposing to empty `classify`.
#   - intent ("this suite should run when this area changes") is not derivable
#     from references; a removal candidate whose only pins are uncovered or
#     helper-derived is labelled rather than asserted.
#   - seams the path scan cannot resolve at all (a pytest marker, a fixture
#     name, an HTTP route string with no source path) stay invisible.

# Bare package roots that are not pins: `from tortoise import X` / `import
# tortoise` mean "the package", and the root re-exports many surfaces.
_AUDIT_NAMESPACE_ROOTS = frozenset({"tortoise", "tests"})

# Mirror of select()'s per-file fallback branch: engine/config paths with no
# SOURCE_PATTERNS entry select the `core` surface.
_AUDIT_CORE_FALLBACK = ("tortoise/", "graph-scripts/", "config/",
                        "validation/", "packs/")

# Source trees worth naming in the uncovered report (tests/ is test-internal
# scaffolding, not a selectable source surface).
_AUDIT_SOURCE_TREES = ("tortoise/", "tools/", "battery/", "graph-scripts/",
                       "benchmarks/", "config/", "packs/", "validation/",
                       "website/", "services/", "integrations/")

# Source trees whose modules are followed TRANSITIVELY when a test imports
# them (rule (d) extension, #2938 review P2). A test reaching a surface only
# through a repo-root helper (tools/longmem_eval/retrieve.py ->
# tortoise/retrieval.py) must not read as "pins nothing". `tortoise/` is
# deliberately excluded: it IS the surface source, and its internals are not
# helper indirection.
_AUDIT_HELPER_TREES = ("tests/", "tools/", "graph-scripts/", "battery/",
                       "benchmarks/", "services/", "integrations/")

_AUDIT_PATH_TOKEN = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.\\/-]*")
_AUDIT_EVIDENCE_CAP = 6


def _audit_is_shared(path: str) -> bool:
    return any(path == s or (s.endswith("/") and path.startswith(s))
               for s in SHARED_MODULES)


def _audit_module_files(module: str, repo: Path, tests_dir: Path) -> set[str]:
    """Existing repo files a dotted module may resolve to.

    Checked under BOTH import roots: the repo root and tests/ (pytest puts
    tests/ on sys.path, so `eval.harness.schema` lives at
    tests/eval/harness/schema.py while `tortoise.embeddings` lives at
    tortoise/embeddings.py).
    """
    rel = module.replace(".", "/")
    out: set[str] = set()
    for root in (repo, tests_dir):
        for cand in (root / f"{rel}.py", root / rel / "__init__.py"):
            if cand.is_file():
                out.add(cand.relative_to(repo).as_posix())
    return out


def _audit_classify(path: str) -> tuple[frozenset[str], bool, str]:
    """(surfaces, covered, reason) for a repo-relative source path.

    ``reason`` is ``pattern`` (matched SOURCE_PATTERNS), ``shared``
    (SHARED_MODULES -> full matrix), ``core-fallback`` (select()'s core
    branch) or ``outside`` (not selection-relevant).
    """
    if _audit_is_shared(path):
        return frozenset(), False, "shared"
    surfaces = {s for s, pats in SOURCE_PATTERNS.items()
                if any(path == p or path.startswith(p) for p in pats)}
    if surfaces:
        return frozenset(surfaces), True, "pattern"
    if any(path.startswith(p) for p in _AUDIT_CORE_FALLBACK):
        return frozenset({"core"}), False, "core-fallback"
    return frozenset(), False, "outside"


def _audit_parse(abs_path: Path) -> ast.AST | None:
    try:
        return ast.parse(abs_path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return None


def _audit_relative_base(node: ast.ImportFrom, pkg: list[str]) -> str:
    """Resolve a (possibly relative) ImportFrom base module name."""
    if node.level == 0:
        return node.module or ""
    base = list(pkg)
    drop = node.level - 1
    if drop:
        base = base[:-drop] if drop <= len(base) else []
    if node.module:
        base = base + node.module.split(".")
    return ".".join(base)


def _audit_import_nodes(tree: ast.AST, module_level_only: bool):
    """Import nodes to honor, optionally skipping deferred (call-time) ones.

    #2938 review P1: `ast.walk` visits every nested import, so an import
    inside a function body reads exactly like a module-level one. A helper
    module that is only IMPORTED (never called) never executes its
    function-local imports, so those must not become strong pins. Descend
    through statements that execute at import time (if/try/with/class
    bodies); stop at function/lambda scopes.
    """
    if not module_level_only:
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                yield node
        return
    stack = [tree]
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.Lambda)):
            continue
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            yield node
            continue
        stack.extend(ast.iter_child_nodes(node))


def _audit_import_paths(tree: ast.AST, module_name: str, repo: Path,
                        tests_dir: Path,
                        module_level_only: bool = False) -> list[str]:
    """Repo-relative files the file's import statements resolve to.

    `from A import n` descends into `A.n` as well as `A`, so
    `from tortoise import onboarding` pins tortoise/onboarding/__init__.py
    and `from tortoise.api import EventAPI` pins tortoise/api.py (api is a
    module, not a package, so the name descent finds nothing and the base
    does).

    ``module_level_only`` skips imports nested in function/lambda bodies
    (see :func:`_audit_import_nodes`): true for a transitively followed
    helper, false for the root test file (whose function bodies DO run).
    """
    pkg = module_name.split(".")[:-1]
    out: set[str] = set()
    for node in _audit_import_nodes(tree, module_level_only):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in _AUDIT_NAMESPACE_ROOTS:
                    continue
                out.update(_audit_module_files(alias.name, repo, tests_dir))
        elif isinstance(node, ast.ImportFrom):
            base = _audit_relative_base(node, pkg)
            if base and base not in _AUDIT_NAMESPACE_ROOTS:
                out.update(_audit_module_files(base, repo, tests_dir))
            for alias in node.names:
                if alias.name == "*":
                    continue
                sub = f"{base}.{alias.name}" if base else alias.name
                out.update(_audit_module_files(sub, repo, tests_dir))
    return sorted(out)


def _audit_string_paths(tree: ast.AST) -> list[str]:
    """Path-like tokens from non-docstring string literals (rule (c))."""
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) \
                    and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                docstrings.add(id(body[0].value))
    out: set[str] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        if id(node) in docstrings:
            continue
        for raw in _AUDIT_PATH_TOKEN.findall(node.value):
            token = raw.replace("\\", "/")
            while token.startswith("./"):
                token = token[2:]
            out.add(token)
    return sorted(out)


def _audit_own_refs(abs_path: Path, module_name: str, repo: Path,
                    tests_dir: Path,
                    cache: dict[tuple[str, bool], tuple] | None = None,
                    module_level_only: bool = False):
    """Direct reference scan of one file.

    Returns ``(surface_pins, uncovered, shared, helpers)`` where
    surface_pins maps a SOURCE_PATTERNS surface to evidence strings,
    uncovered maps a pinned-but-unmapped source path to evidence, shared is
    the referenced SHARED_MODULES set, and helpers is the set of repo-relative
    modules imported and worth following (rule (d): tests/ helpers plus any
    module in ``_AUDIT_HELPER_TREES``). ``cache`` memoizes the scan (a helper
    reached by many test roots is parsed once); the cache key carries
    ``module_level_only`` because the same file can be scanned both as a
    test root (all imports) and as a helper (module-level only).
    """
    key = (abs_path.as_posix(), module_level_only)
    if cache is not None and key in cache:
        return cache[key]
    surface_pins: dict[str, set[str]] = {}
    uncovered: dict[str, set[str]] = {}
    shared: set[str] = set()
    helpers: set[str] = set()
    tree = _audit_parse(abs_path)
    if tree is None:
        result = (surface_pins, uncovered, shared, helpers)
        if cache is not None:
            cache[key] = result
        return result

    def record(path: str, evidence: str) -> None:
        surfaces, covered, reason = _audit_classify(path)
        if reason == "shared":
            shared.add(path)
        elif covered:
            for s in surfaces:
                surface_pins.setdefault(s, set()).add(evidence)
        elif reason == "core-fallback" or any(
                path.startswith(p) for p in _AUDIT_SOURCE_TREES):
            uncovered.setdefault(path, set()).add(evidence)

    for path in _audit_import_paths(tree, module_name, repo, tests_dir,
                                    module_level_only):
        if path.startswith("tests/") and path.endswith(".py"):
            # test-internal module: a helper to follow, not a source pin.
            if _audit_is_shared(path):
                shared.add(path)
            else:
                helpers.add(path)
            continue
        record(path, path)
        # #2938 review P2: a repo-root helper (tools/, graph-scripts/, …) is
        # both a pin of its own surface AND indirection — follow it so a test
        # reaching a surface only through it is not read as pinning nothing.
        if path.endswith(".py") and path.startswith(_AUDIT_HELPER_TREES) \
                and not _audit_is_shared(path):
            helpers.add(path)
    for token in _audit_string_paths(tree):
        # rule (c) precision: a string naming no file/dir on disk is data,
        # not a source reference (e.g. an MCP method name "tools/call").
        if not (repo / token).exists():
            continue
        record(token, f'"{token}"')
    result = (surface_pins, uncovered, shared, helpers)
    if cache is not None:
        cache[key] = result
    return result


def _audit_file_refs(abs_path: Path, rel: str, repo: Path, tests_dir: Path,
                     cache: dict[tuple[str, bool], tuple] | None = None):
    """Pins for one test file, following its imported helper modules.

    Cycle-safe BFS over repo-relative paths: each helper contributes its own
    pins tagged ``(via tests/helper.py)`` (or ``(via tools/…)`` for a
    repo-root helper); SHARED_MODULES helpers are never followed. Helpers are
    scanned module-level only (#2938 review P1): a helper is imported, not
    called, so its function-local imports never execute — and a helper's own
    deferred import of another helper is not followed either.
    """
    surface_pins: dict[str, set[str]] = {}
    uncovered: dict[str, set[str]] = {}
    shared: set[str] = set()
    seen: set[str] = set()
    root = rel if rel.startswith("tests/") else f"tests/{rel}"
    queue = [root]
    while queue:
        cur = queue.pop(0)
        if cur in seen:
            continue
        seen.add(cur)
        cur_abs = repo / cur
        if not cur_abs.is_file():
            continue
        module_name = cur[:-3].replace("/", ".")
        sp, unc, sh, helpers = _audit_own_refs(
            cur_abs, module_name, repo, tests_dir, cache,
            module_level_only=(cur != root))
        shared |= sh
        tag = "" if cur == root else f" (via {cur})"
        for s, evs in sp.items():
            for ev in evs:
                surface_pins.setdefault(s, set()).add(ev + tag)
        for p, evs in unc.items():
            for ev in evs:
                uncovered.setdefault(p, set()).add(ev + tag)
        for helper in sorted(helpers):
            if helper not in seen:
                queue.append(helper)
    return surface_pins, uncovered, shared


def _audit_coverage_gaps(repo: Path) -> dict[str, str]:
    """Surface-named source paths absent from that surface's SOURCE_PATTERNS.

    The #2938 case (b): a source named after a surface (`tortoise/<s>.py`,
    e.g. `tortoise/api.py` before this PR mapped it) exists, but that
    surface's `SOURCE_PATTERNS` entry does not list it — so a change to it
    selects `core` (or nothing), and the surface-registered tests pinning it
    never run. Reporting this explicitly is the whole point of the audit:
    silently binning the path as `core` would make the numbers lie.
    """
    gaps: dict[str, str] = {}
    for surface in sorted(SOURCE_PATTERNS):
        pats = SOURCE_PATTERNS[surface]
        for cand in (f"tortoise/{surface}.py", f"tortoise/{surface}",
                     surface):
            if not (repo / cand).exists() or _audit_is_shared(cand):
                continue
            covered = any(cand == p or cand.startswith(p) or p == cand + "/"
                          for p in pats)
            if not covered:
                gaps[cand] = surface
                break
    return gaps


def _audit_is_string_evidence(evidence: str) -> bool:
    """True when the evidence came from a path *string*, not an import."""
    return evidence.startswith('"')


def _audit_entries(entries) -> list:
    """Manifest surface value as a list.

    #2938 review P3: a curated manifest can carry `null` for a surface, and a
    hard-edited one a scalar. `None` means empty (like integrity()'s
    `files or ()`); a non-list scalar is malformed and is treated as empty
    rather than silently iterated (`api: "test_x.py"` used to become a list
    of characters).
    """
    if entries is None:
        return []
    return _surface_members(entries)


def _audit_gap_selection(rec: dict, manifest: dict) -> tuple[list[str], list[str], list[str]]:
    """(selected_surfaces, victims, runners) for one coverage-gap record.

    #2938 review P2: the previous renderer asserted the non-gap pinners "run
    via {others}", but `others` is just the union of non-gap registrations —
    `select([unmapped_path], ...)` selects only the fallback `core` surface
    and does NOT select `ep`, so a pinner registered solely under `ep` does
    not run. A pinner RUNS when it is in the change's fast-gate `test_files`,
    in the selected surfaces' slow leg, or in a triggered carve-out job;
    every other pinner is a victim. Callers pass a manifest whose surface
    values are already normalised via :func:`_audit_entries`.
    """
    selection = select([rec["path"]], "pull_request", manifest)
    if selection["full"] or selection["test_files"] == "ALL":
        return sorted(manifest["surfaces"]), [], sorted(rec["files"])
    runs: set[str] = set(selection["test_files"])
    runs |= set(selection.get("slow_selected") or [])
    if selection.get("carve_out_run"):
        runs |= set(manifest.get("carve_out") or [])
    runners, victims = [], []
    for f in sorted(rec["files"]):
        base = f.rsplit("/", 1)[-1]
        (runners if (f in runs or base in runs) else victims).append(f)
    return sorted(selection["surfaces"]), victims, runners


def surface_audit(manifest: dict, repo: Path | None = None,
                  tests_dir: Path | None = None) -> dict:
    """#2938: report-only manifest/source mismatch audit.

    Never mutates the manifest and never feeds selection: it returns a
    structured report consumed by :func:`render_surface_audit`. ``repo`` /
    ``tests_dir`` are injectable for synthetic-manifest tests. Surface values
    are normalised with :func:`_audit_entries` (``null``/scalars -> empty),
    so a curated manifest with a malformed value still audits without raising.
    """
    repo = Path(repo) if repo is not None else REPO
    tests_dir = Path(tests_dir) if tests_dir is not None else TESTS_DIR
    owners: dict[str, list[str]] = {
        s: _audit_entries(entries)
        for s, entries in manifest["surfaces"].items()
    }
    # select() gets the normalised surfaces (a scalar value would crash its
    # `files.update(...)`); the original manifest is left untouched.
    audit_manifest = {**manifest, "surfaces": owners}
    source_mapped = set(SOURCE_PATTERNS)
    gaps = _audit_coverage_gaps(repo)
    cache: dict[tuple[str, bool], tuple] = {}

    disk: list[str] = []
    for f in sorted(tests_dir.rglob("test_*.py")):
        rel_path = f.relative_to(tests_dir)
        if rel_path.parts and rel_path.parts[0] == "e2e":
            continue
        disk.append(rel_path.as_posix())

    def registered_in(rel: str) -> list[str]:
        base = rel.rsplit("/", 1)[-1]
        return sorted(s for s, entries in owners.items()
                      if rel in entries or base in entries)

    by_surface = {
        s: {"members": list(entries),
            "source_mapped": s in source_mapped,
            "removal": [], "addition": []}
        for s, entries in owners.items()
    }
    uncovered_index: dict[str, dict] = {}
    for rel in disk:
        surface_pins, uncovered, shared = _audit_file_refs(
            tests_dir / rel, rel, repo, tests_dir, cache)
        registered = registered_in(rel)
        for s in sorted(surface_pins):
            if s in by_surface and s not in registered:
                evs = sorted(surface_pins[s])
                # #2938 review P1-2: a path STRING is weak evidence — it may
                # be fixture data (test_ci_selection.py's own corpus names six
                # surfaces). Keep it visible (the renderer labels it), but only
                # an import-derived reference makes the file an addition
                # candidate a human should act on.
                by_surface[s]["addition"].append({
                    "file": rel, "evidence": evs,
                    "strong": any(not _audit_is_string_evidence(e)
                                   for e in evs),
                    "registered": registered})
        for s in registered:
            if s not in source_mapped or surface_pins.get(s):
                continue
            by_surface[s]["removal"].append({
                "file": rel, "surface": s,
                "pins": {t: sorted(v)
                         for t, v in sorted(surface_pins.items())},
                "uncovered": sorted(uncovered), "shared": sorted(shared),
                "gap": sorted(p for p in uncovered if p in gaps)})
        for path, evs in uncovered.items():
            rec = uncovered_index.setdefault(
                path, {"path": path, "files": {}, "weak_files": {},
                       "evidence": set()})
            evs = sorted(evs)
            # #2938 review P1-2: only a file with an import-derived reference
            # to this path counts as a STRONG pinnner. A string-only pinnner
            # (test_ci_selection.py names tortoise/api.py as fixture data) is
            # kept in weak_files so the "never run" claim is not inflated.
            bucket = (rec["files"]
                      if any(not _audit_is_string_evidence(e) for e in evs)
                      else rec["weak_files"])
            bucket[rel] = sorted(registered)
            rec["evidence"].update(evs)

    uncovered = []
    for rec in uncovered_index.values():
        rec["files"] = {f: rec["files"][f] for f in sorted(rec["files"])}
        rec["weak_files"] = {f: rec["weak_files"][f]
                             for f in sorted(rec["weak_files"])}
        rec["registered"] = sorted(
            {s for regs in rec["files"].values() for s in regs}
            | {s for regs in rec["weak_files"].values() for s in regs})
        rec["evidence"] = sorted(rec["evidence"])
        rec["surface"] = gaps.get(rec["path"])
        uncovered.append(rec)
    uncovered.sort(key=lambda r: (r["surface"] is None, r["path"]))

    coverage_gaps = [r for r in uncovered if r["surface"] is not None]
    for rec in coverage_gaps:
        sel, victims, runners = _audit_gap_selection(rec, audit_manifest)
        rec["selected_surfaces"] = sel
        rec["victims"] = victims
        rec["runs"] = runners

    # #2938 review P3: a curated manifest can carry `null` for a surface;
    # a bare `entries.count()` would traceback. `owners` is normalised above.
    duplicates: dict[str, list[str]] = {}
    for s, entries in owners.items():
        dupes = sorted({e for e in entries if entries.count(e) > 1})
        if dupes:
            duplicates[s] = dupes

    return {
        "surfaces": by_surface,
        "no_surface": sorted(r for r in disk if not registered_in(r)),
        "duplicates": {s: d for s, d in duplicates.items() if d},
        "coverage_gaps": coverage_gaps,
        "uncovered": [r for r in uncovered if r["surface"] is None],
        "disk_files": len(disk),
    }


def _audit_fmt_paths(paths: list[str], cap: int = _AUDIT_EVIDENCE_CAP) -> str:
    shown = ", ".join(paths[:cap])
    if len(paths) > cap:
        shown += f", … (+{len(paths) - cap} more)"
    return shown


def _audit_label_evidence(evidence: str) -> str:
    """Mark string-derived evidence so a human sees it is not an import."""
    if not _audit_is_string_evidence(evidence):
        return evidence
    core, sep, via = evidence.partition(" (via ")
    if sep:
        return f"{core} (string, not an import; via {via}"
    return f"{core} (string, not an import)"


def _audit_fmt_evidence(evs: list[str],
                        cap: int = _AUDIT_EVIDENCE_CAP) -> str:
    return _audit_fmt_paths([_audit_label_evidence(e) for e in evs], cap)


def _audit_removal_line(entry: dict) -> str:
    parts = [f"{s}: {_audit_fmt_evidence(v)}"
             for s, v in sorted(entry["pins"].items())]
    if entry["shared"]:
        parts.append(f"shared: {_audit_fmt_paths(entry['shared'])}")
    if entry["uncovered"]:
        parts.append(f"uncovered: {_audit_fmt_paths(entry['uncovered'])}")
    if entry["gap"]:
        parts.append(f"⚑ coverage gap: {_audit_fmt_paths(entry['gap'])}")
        warn = ("  ⚑ surface-named source is unmapped — fix SOURCE_PATTERNS "
                "before removing")
    else:
        # #2938 review P3: every entry in this list has NO pin for its own
        # surface BY DEFINITION, so the "don't act" cue belongs on all of
        # them — shared-only members (`shared: tortoise/sdk.py`) used to
        # render as bare removal candidates.
        warn = (f"  ⚠ no `{entry['surface']}` pin resolved — helper/dynamic "
                "indirection unresolved; confirm before removing")
    body = "; ".join(parts) if parts else "no source references at all"
    return f"  {entry['file']} <- ({body}){warn}"


def render_surface_audit(report: dict) -> str:
    """Human-readable rendering of :func:`surface_audit` (deterministic)."""
    lines = [
        "CI surface audit (#2938) — report only; select() / --integrity are "
        "unchanged.",
        f"scanned tests/**/test_*.py (tests/e2e/ exempt): {report['disk_files']} files",
        "",
        "Summary (surface: members / no-pin removal candidates / addition "
        "candidates):",
    ]
    for s in sorted(report["surfaces"]):
        v = report["surfaces"][s]
        if v["source_mapped"]:
            n_add = sum(1 for e in v["addition"] if e["strong"])
            lines.append(f"  {s:<11} {len(v['members']):>4} members   "
                         f"{len(v['removal']):>3} no-pin   "
                         f"{n_add:>3} additions")
        else:
            lines.append(f"  {s:<11} {len(v['members']):>4} members   "
                         "  n/a (no SOURCE_PATTERNS entry)")
    unmapped = sorted(s for s, v in report["surfaces"].items()
                      if not v["source_mapped"])
    if unmapped:
        lines += [
            "",
            f"⚠ {', '.join(unmapped)}: no SOURCE_PATTERNS entry — nothing maps a "
            "source change to this surface, so \"pins",
            "  nothing from it\" is undefined and removal analysis is skipped "
            "(a naive scan would propose",
            "  emptying `classify`; the rejected mechanical rule did exactly "
            "that).",
        ]
    lines.append("")
    for s in sorted(report["surfaces"]):
        v = report["surfaces"][s]
        if not v["source_mapped"]:
            continue
        lines.append(f"── {s} " + "─" * max(0, 58 - len(s)))
        if v["removal"]:
            lines.append(f"removal candidates — members pinning nothing from "
                         f"`{s}` ({len(v['removal'])}):")
            lines += [_audit_removal_line(e) for e in v["removal"]]
        else:
            lines.append(f"removal candidates — none (every member pins `{s}`)")
        strong_add = [e for e in v["addition"] if e["strong"]]
        weak_add = [e for e in v["addition"] if not e["strong"]]
        if strong_add:
            lines.append(f"addition candidates — files pinning `{s}` but not "
                         f"registered ({len(strong_add)}):")
            for e in strong_add:
                reg = ", ".join(e["registered"]) or "none"
                lines.append(f"  {e['file']} <- "
                             f"{_audit_fmt_evidence(e['evidence'])}"
                             f"  (registered: {reg})")
        else:
            lines.append("addition candidates — none")
        if weak_add:
            lines.append(f"string-only references — weak evidence, not counted "
                         f"as pins ({len(weak_add)}):")
            for e in weak_add:
                reg = ", ".join(e["registered"]) or "none"
                lines.append(f"  {e['file']} <- "
                             f"{_audit_fmt_evidence(e['evidence'])}"
                             f"  (registered: {reg})")
        lines.append("")

    gaps = report["coverage_gaps"]
    if gaps:
        lines.append(f"⚑ SOURCE_PATTERNS coverage gaps ({len(gaps)}) — surface-named "
                     "source that no pattern maps:")
        for rec in gaps:
            # #2938 review P2: do NOT assert which files run from the union of
            # non-gap registrations — `select()` decides. Victims are the
            # pinners this change does not select (e.g. a pinner registered
            # only under a surface the change does not select).
            victims = rec["victims"]
            selected = "/".join(rec["selected_surfaces"]) or "none"
            weak_victims = sorted(
                f for f, regs in rec["weak_files"].items()
                if rec["surface"] in regs)
            total = len(rec["files"]) + len(rec["weak_files"])
            weak_note = (f" (+{len(weak_victims)} string-only pinnner(s), "
                         "weak)" if weak_victims else "")
            lines.append(f"  {rec['path']} is not in "
                         f"SOURCE_PATTERNS[{rec['surface']!r}] — a change to "
                         f"it selects `{selected}`,")
            if victims:
                lines.append(f"    never run on a change to {rec['path']}: "
                             f"{_audit_fmt_paths(victims)}")
            else:
                lines.append(f"    every pinning file still runs on a change "
                             f"to {rec['path']} (via `{selected}`)")
            lines.append(f"    (pinned by {total} file(s) in total; "
                         f"{len(victims)} never run, {len(rec['runs'])} run via "
                         f"`{selected}`{weak_note}; evidence: "
                         f"{_audit_fmt_evidence(rec['evidence'])})")
        lines.append("")

    nosurf = report["no_surface"]
    lines.append(f"files in NO surface ({len(nosurf)})"
                 + (":" if nosurf else " — none"))
    for f in nosurf:
        lines.append(f"  {f} <- (unregistered; a tests/ change cannot select it)")
    lines.append("")

    dupes = report["duplicates"]
    total_dupes = sum(len(v) for v in dupes.values())
    lines.append(f"same-surface duplicate entries ({total_dupes})"
                 + (":" if total_dupes else " — none"))
    for s in sorted(dupes):
        for e in dupes[s]:
            lines.append(f"  {s}: {e} (listed "
                         f"{report['surfaces'][s]['members'].count(e)}x)")
    lines.append("")

    unc = report["uncovered"]
    lines.append(f"other pinned paths with no SOURCE_PATTERNS entry ({len(unc)}) "
                 "— these select `core` (the fallback):")
    for rec in unc:
        reg = ", ".join(rec["registered"]) or "none"
        weak = (f" (+{len(rec['weak_files'])} string-only)"
                if rec["weak_files"] else "")
        lines.append(f"  {rec['path']}  (pinned by {len(rec['files'])}{weak}, "
                     f"registered: {reg})")
    return NL.join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--changed-files", default="", help="newline-separated changed files")
    ap.add_argument("--event", default="pull_request", choices=["push", "pull_request", "schedule"])
    ap.add_argument("--integrity", action="store_true", help="verify manifest coverage")
    ap.add_argument("--surface-audit", action="store_true",
                    help="#2938: report manifest/source membership mismatches "
                         "(non-blocking, never changes selection)")
    ap.add_argument("--register", action="store_true",
                    help="auto-register unlisted test files in the manifest (#1429)")
    ap.add_argument("--surface", default="core",
                    choices=["api", "battery", "classify", "core", "ep",
                             "eval", "onboarding", "sdk"],
                    help="surface for --register (default: core — the selection fallback)")
    ap.add_argument("--dry-run", action="store_true", help="preview --register without writing")
    ap.add_argument("--emit-push-matrix", action="store_true",
                    help="#1472: print the derived push halves {half_a, half_b}")
    ap.add_argument("--split", action="store_true",
                    help="#1473: LPT-pack the stdin JSON test-file list into {a, b}")
    args = ap.parse_args()

    manifest = load_manifest()

    if args.integrity:
        missing = integrity(manifest)
        # #3407 review P1: `duration_issues` must run BEFORE `leg_coverage_issues`.
        # The latter calls `push_legs()` -> `split_fast_gate()`, so a malformed
        # durations value used to raise inside the packer before the check that
        # names it had run — fail-closed, but with no diagnosis. (Belt and
        # braces: `_duration_weight` also coerces, so the packer can no longer
        # raise at all.)
        problems = missing + slow_file_issues(manifest) \
            + duration_issues(manifest) + leg_coverage_issues(manifest) \
            + duration_coverage_issues(manifest)
        # #1472: the matrix rows must come from the selector derivation
        # (space-joined matrix_* outputs) — when they do, the #1266
        # halves-parse tie check is
        # subsumed (the derivation guarantees no slow leaks / dupes / dead
        # files by construction). Legacy hardcoded halves keep the #1266 check.
        wf_issues = workflow_matrix_issues(
            REPO / ".github" / "workflows" / "python-ci.yml", manifest)
        problems += wf_issues
        if not wf_issues:
            # derived rows: feed the derived halves so the #1266 tie checks
            # still run against the ACTUAL execution split.
            legs = push_legs(manifest)
            halves = {"a": set(legs["half_a"]), "b": set(legs["half_b"])}
            problems += workflow_halves_issues(manifest, halves)
        else:
            halves = parse_matrix_halves(WORKFLOW.read_text())
            problems += workflow_halves_issues(manifest, halves)
        if problems:
            print(f"❌ manifest drift: {problems}")
            return 1
        absent = fast_files_absent_from_halves(manifest, halves)
        if absent:
            sample = ", ".join(absent[:8])
            print(f"⚠️  {len(absent)} manifest fast files are in NO half "
                  f"(full-matrix coverage hole, #1266): {sample} …")
        dupes = duplicate_entries(manifest)
        if dupes:
            print(f"⚠️  {len(dupes)} duplicate manifest entr(y/ies) — invisible "
                  f"to select(), #2913: {', '.join(dupes)}")
        print("✅ integrity: all test files classified; slow_files consistent; halves consistent")
        return 0

    if args.surface_audit:
        # #2938: report-only. Always exits 0 — a mismatch is a curation
        # decision, never a gate; `--integrity` keeps its own exit code.
        print(render_surface_audit(surface_audit(manifest)))
        return 0

    if args.register:
        if args.dry_run:
            missing = unlisted_tests(TESTS_DIR, manifest)
            if missing:
                print(f"ℹ️  would register under {args.surface}: {missing}")
            else:
                print("✅ nothing to register")
            return 0
        added = register(MANIFEST, TESTS_DIR, args.surface)
        if added:
            print(f"✅ registered {len(added)} test file(s) under {args.surface}: {added}")
            print("   review: config/ci-surfaces.yml — move a file to another surface if the default is wrong.")
        else:
            print("✅ manifest already covers all test files")
        return 0

    if args.emit_push_matrix:
        legs = push_legs(manifest)
        print(json.dumps({"half_a": legs["half_a"], "half_b": legs["half_b"],
                          "carve_out": legs["carve_out"]}))
        return 0

    if args.split:
        # #1492: an empty stdin (the split step's output expression resolving
        # to "") must degrade to an empty selection — NOT crash --split for
        # every tier-2 PR (json.loads('') raises).
        raw = sys.stdin.read().strip()
        files = json.loads(raw) if raw else []
        a, b = split_fast_gate(files, _durations_map(manifest))
        result = {"a": a, "b": b}
        out_dir = Path(os.environ.get("CI_SELECTION_ARTIFACT_DIR", REPO / ".ci-selection"))
        out_dir.mkdir(exist_ok=True)
        (out_dir / "split.json").write_text(json.dumps(result, indent=2))
        print(json.dumps(result))
        return 0

    changed = [l.strip() for l in args.changed_files.splitlines() if l.strip()]  # noqa: E741
    if args.changed_files == "-":
        changed = [l.strip() for l in sys.stdin.read().splitlines() if l.strip()]  # noqa: E741
    result = select(changed, args.event, manifest)

    # Audit artifact (scope v5 decision 1): replayable selection record.
    artifact = {
        "pr_sha": os.environ.get("GITHUB_SHA", ""),
        "selection_fn_version": SELECTION_FN_VERSION,
        "changed_files": changed,
        "surfaces": result["surfaces"],
        "full": result["full"],
        "selected_tests": result["test_files"],
        # #2147/#2148: the slow/carve-out diff-gate decisions ride the
        # artifact so the nightly recall audit can replay what ran.
        "slow_run": result["slow_run"],
        "slow_selected": result["slow_selected"],
        "carve_out_run": result["carve_out_run"],
    }
    out_dir = Path(os.environ.get("CI_SELECTION_ARTIFACT_DIR", REPO / ".ci-selection"))
    out_dir.mkdir(exist_ok=True)
    (out_dir / "selection.json").write_text(json.dumps(artifact, indent=2))

    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
