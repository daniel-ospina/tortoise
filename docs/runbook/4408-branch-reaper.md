---
title: "4408 — Worktree/branch reaper dry-run report"
type: operations
domain: operations
doc_status: live
created: 2026-09-20
ownedBy: organisation-design-team
aboutSubjects: organisation-design-team
aboutObjects: tortoise
---

# branch-reaper dry-run report — #4408

> **Provenance.** Generated at commit `afc0c9a76` with the tool as of that
> revision: the dry-run classification below precedes the actual reaping pass
> whose results appear in the Post-apply section. Round 2 of code review later
> changed the tool's report format (a `dirty` column on the held table, HEAD-age
> ranking for detached worktrees, and the Recovery record promoted to its own
> H2 before the results), so a fresh `--report` run renders slightly differently.
> The classification numbers and the deleted count are the historical evidence
> of the pass and are unchanged.
>
> ⚠️ **The recovery rows below carry a claim that is WRONG.** Their recovery
> column reads `reflog ~30d`, which `git update-ref -d` does not provide — it
> deletes the deleted ref's reflog, so a tip survives only until the objects are
> pruned (`gc.pruneExpire`, 2 weeks by default; immediately under
> `gc --prune=now`). The tool's report text is corrected, and `--apply` now
> writes a backup bundle by default. The rows are left as generated so the
> historical pass stays readable: read `reflog ~30d` as evidence of the defect,
> not as guidance.

Generated: 2026-09-21 · repo `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/4408-branch-reaper` · slug `daniel-ospina/tortoise` · main ref `origin/main`

Tool: `tools/branch_reaper.py`. Dry-run by default; this report is the evidence a reviewer reads before any `--apply`.

`--include-closed-unmerged`: **False**

## Summary

| metric | count |
|---|---|
| local branches | 1475 |
| **SAFE_BY_HISTORY** | **1160** |
| of which checked out in a worktree | 237 |
| **SAFE_DELETABLE_now** | **923** |
| PRESERVE | 208 |
| JUDGEMENT (report only) | 107 |
| worktrees | 412 (detached: 51) |

### Verdicts by reason

| reason | count |
|---|---|
| advanced-beyond-pr-tip | 63 |
| ancestry | 650 |
| no-pr-non-ancestor | 107 |
| open-pr | 77 |
| pr-closed-tip | 66 |
| pr-merged-tip | 510 |
| trunk | 2 |

## Safe — deletable now

Branch tip provably survives and no worktree holds it.

| branch | tip | age (days) | verdict | survives | PR |
|---|---|---|---|---|---|
| `feat/235-hosted-onboarding-journey` | `d93359c5b728` | 44 | ancestry | reachable-from-main | — |
| `feat/236-mcp-streamable-http` | `f86a068bc213` | 44 | pr-merged-tip | merged-pr-record | 487 |
| `feat/160-hosted-search-fts-vector` | `7393582b9b69` | 44 | ancestry | reachable-from-main | — |
| `feat/485-sync-tagged-edges` | `ef138b954336` | 44 | pr-merged-tip | merged-pr-record | 488 |
| `feat/420-ep-validation` | `7521227df57a` | 44 | ancestry | reachable-from-main | — |
| `chore/187-untrack-pycache` | `02504b5f7358` | 44 | pr-merged-tip | merged-pr-record | 503 |
| `fix/191-audit-scripts-env` | `798cab091d77` | 44 | pr-merged-tip | merged-pr-record | 504 |
| `fix/22-sep-confidence-scale` | `c67ef79b5614` | 44 | pr-merged-tip | merged-pr-record | 506 |
| `chore/206-rename-website` | `b3bc416e2b2d` | 44 | pr-merged-tip | merged-pr-record | 507 |
| `feat/25-graph-informed-ranking` | `d85d5c2006c2` | 44 | pr-merged-tip | merged-pr-record | 513 |
| `feat/324-connector-secrets-encryption` | `dbb1572aa61d` | 44 | pr-merged-tip | merged-pr-record | 512 |
| `feat/160-hosted-embeddings` | `03b634081bae` | 44 | pr-merged-tip | merged-pr-record | 508 |
| `feat/454-canonical-tool-registry` | `860cc67ced39` | 44 | pr-merged-tip | merged-pr-record | 510 |
| `fix/160-embeddings-cache-path` | `fe820a9ca3d3` | 44 | pr-merged-tip | merged-pr-record | 516 |
| `docs/28-adr-008` | `8aee29e4e66b` | 44 | pr-merged-tip | merged-pr-record | 514 |
| `chore/486-register-about-meta-keys` | `a094090c1282` | 44 | pr-merged-tip | merged-pr-record | 520 |
| `feat/243-search-sessions-temporal` | `616412543e57` | 44 | pr-merged-tip | merged-pr-record | 505 |
| `feat/244-session-semantic-search` | `de2f42740e81` | 44 | pr-merged-tip | merged-pr-record | 511 |
| `feat/497-onboarding-welcome-impl` | `1a929f6e6221` | 44 | pr-merged-tip | merged-pr-record | 534 |
| `feat/498-onboarding-api-impl` | `0ca9508f8774` | 44 | pr-merged-tip | merged-pr-record | 533 |
| `fix/420-ep-validation-gaps` | `99e3aff35629` | 44 | pr-merged-tip | merged-pr-record | 536 |
| `feat/428-ops-safety-residual` | `8b0a77adcfcb` | 44 | pr-merged-tip | merged-pr-record | 538 |
| `feat/327-db-indexes` | `b7e5741ca788` | 44 | ancestry | reachable-from-main | — |
| `fix/545-deploy-pipeline` | `8dd2fd483d50` | 44 | pr-merged-tip | merged-pr-record | 546 |
| `feat/330-data-divergence` | `18a673d80056` | 44 | pr-merged-tip | merged-pr-record | 539 |
| `fix/420-quadrature-test-followup` | `14156468e413` | 44 | pr-merged-tip | merged-pr-record | 550 |
| `feat/338-service-model-v2` | `5d32820b99cd` | 44 | pr-merged-tip | merged-pr-record | 554 |
| `fix/338-contact-email` | `9627c4c41277` | 44 | pr-merged-tip | merged-pr-record | 556 |
| `fix/stale-search-tests` | `a895ad8bc913` | 44 | ancestry | reachable-from-main | — |
| `feat/398-source-credibility` | `5db19cd38230` | 44 | ancestry | reachable-from-main | — |
| `fix/555-ci` | `a9d6f4e01031` | 44 | pr-merged-tip | merged-pr-record | 567 |
| `feat/contestation-signal` | `95fb223cad4e` | 44 | ancestry | reachable-from-main | — |
| `fix/contestation-surface-only` | `1e41ce9de1f8` | 44 | ancestry | reachable-from-main | — |
| `fix/561-timeout-nodiscard` | `cefa2a911692` | 44 | pr-merged-tip | merged-pr-record | 584 |
| `test/562-revise-embed-test` | `860a4271a146` | 44 | pr-merged-tip | merged-pr-record | 585 |
| `feat/564-session-end-hook` | `218c02df6bdb` | 44 | pr-merged-tip | merged-pr-record | 587 |
| `docs/565-ontology-cascade` | `4c988746078b` | 44 | pr-merged-tip | merged-pr-record | 588 |
| `docs/566-deploy-runbook` | `be47daac5e87` | 44 | pr-merged-tip | merged-pr-record | 590 |
| `feat/560-mcp-graph-ranking` | `1dc963dc4dc7` | 44 | pr-merged-tip | merged-pr-record | 589 |
| `test/563-rebuild-order-test` | `1f672b932d64` | 44 | pr-merged-tip | merged-pr-record | 586 |
| `ci/559-postmerge-validation` | `d6f82591ba1d` | 44 | ancestry | reachable-from-main | — |
| `feat/525-rest` | `149979fe63fe` | 44 | pr-merged-tip | merged-pr-record | 581 |
| `feat/329-security-hardening` | `1aa92b79e97e` | 44 | ancestry | reachable-from-main | — |
| `feat/575-pricing` | `ff4b8d4dcc8b` | 44 | pr-merged-tip | merged-pr-record | 594 |
| `fix/338-contact-url` | `9a7a36af510b` | 44 | pr-merged-tip | merged-pr-record | 597 |
| `fix/545-prewarm-nonblocking` | `d53888e9cf83` | 44 | pr-merged-tip | merged-pr-record | 600 |
| `fix/hosted-coldstart` | `aac53f5ceb25` | 44 | pr-merged-tip | merged-pr-record | 601 |
| `fix/545-deploy-serialize` | `146e1a63cd08` | 44 | pr-merged-tip | merged-pr-record | 603 |
| `fix/hosted-deploy-stage` | `0796c5ad4e4d` | 44 | pr-merged-tip | merged-pr-record | 605 |
| `fix/hosted-mcp-origins` | `753b989d17c1` | 44 | pr-merged-tip | merged-pr-record | 606 |
| `fix/545-memory-bump` | `768dfdc41f66` | 44 | pr-merged-tip | merged-pr-record | 607 |
| `fix/hosted-mcp-hosts` | `eb59e613f08d` | 44 | pr-merged-tip | merged-pr-record | 609 |
| `fix/hosted-mcp-hosts-v2` | `665a5aa64814` | 44 | pr-merged-tip | merged-pr-record | 610 |
| `fix/dogfood-signup-fixes` | `e861ac140e11` | 44 | pr-merged-tip | merged-pr-record | 611 |
| `chore/website-redeploy` | `a3abd9d21ecf` | 44 | ancestry | reachable-from-main | — |
| `feat/569-provisioning` | `175b4cd88630` | 44 | pr-merged-tip | merged-pr-record | 612 |
| `feat/568-decoupling-v2` | `3c5cce1ca4f9` | 44 | pr-merged-tip | merged-pr-record | 615 |
| `feat/570-session-key-v3` | `2df8188e9b47` | 44 | pr-merged-tip | merged-pr-record | 618 |
| `feat/571-reveal` | `a782b24a3093` | 44 | pr-merged-tip | merged-pr-record | 620 |
| `feat/573-onboarding` | `3701675af7b4` | 44 | ancestry | reachable-from-main | — |
| `feat/572-dashboard-auth` | `5fb4fe419d21` | 44 | pr-merged-tip | merged-pr-record | 623 |
| `feat/573-onboarding-v2` | `1e23c30cf9e7` | 44 | pr-merged-tip | merged-pr-record | 627 |
| `feat/574-invites` | `f87dc3d6fdcf` | 44 | pr-merged-tip | merged-pr-record | 629 |
| `feat/576-email-v2` | `1760362e0578` | 44 | pr-merged-tip | merged-pr-record | 634 |
| `feat/577-analytics` | `0c4b9e69bbe1` | 44 | pr-merged-tip | merged-pr-record | 635 |
| `fix/540-prompt-url` | `d5d0b4ff9740` | 44 | pr-merged-tip | merged-pr-record | 640 |
| `feat/578-e2e` | `df7288a352a3` | 44 | pr-merged-tip | merged-pr-record | 641 |
| `fix/545-pricing-image` | `200abf34bdc6` | 44 | pr-merged-tip | merged-pr-record | 642 |
| `fix/545-deploy-secrets` | `e10bc4bd40ea` | 44 | pr-merged-tip | merged-pr-record | 643 |
| `fix/542-oauth-secrets` | `a3917cb358c2` | 43 | pr-merged-tip | merged-pr-record | 644 |
| `fix/post-merge-drift` | `93acac020eb3` | 43 | ancestry | reachable-from-main | — |
| `fix/543-analytics` | `3d329a2607fb` | 43 | pr-merged-tip | merged-pr-record | 646 |
| `fix/test-drift` | `bcdfc8c1a47c` | 43 | pr-merged-tip | merged-pr-record | 649 |
| `fix/541-e2e` | `2368cb19c9d6` | 43 | pr-merged-tip | merged-pr-record | 648 |
| `fix/544-selfhost` | `0bc417987aa0` | 43 | pr-merged-tip | merged-pr-record | 653 |
| `feat/399-embedding-matching` | `2a6fe4540e78` | 43 | pr-merged-tip | merged-pr-record | 650 |
| `fix/landing-layout` | `83e91d90819c` | 43 | pr-merged-tip | merged-pr-record | 659 |
| `feat/free-tier-headroom` | `dec6c1e7728b` | 43 | pr-merged-tip | merged-pr-record | 662 |
| `fix/hero-cta` | `36d08a526c6d` | 43 | pr-merged-tip | merged-pr-record | 664 |
| `feat/beat-narrative` | `4e88d3517494` | 43 | pr-merged-tip | merged-pr-record | 665 |
| `fix/accent-colors` | `4247814e4b33` | 43 | pr-merged-tip | merged-pr-record | 666 |
| `fix/beat-spacing` | `aca7544b3a0d` | 43 | pr-merged-tip | merged-pr-record | 667 |
| `fix/beat-background` | `4389afd7c180` | 43 | pr-merged-tip | merged-pr-record | 668 |
| `fix/canvas-zoom-3x` | `fe71e6a3aeb3` | 43 | pr-merged-tip | merged-pr-record | 670 |
| `fix/canvas-zoom-2.2` | `e1a4330fe057` | 43 | pr-merged-tip | merged-pr-record | 672 |
| `fix/selfhost-copy` | `dfd20571b23e` | 43 | pr-merged-tip | merged-pr-record | 674 |
| `feat/341-ep-source-validation` | `941f5ab71801` | 43 | pr-merged-tip | merged-pr-record | 671 |
| `feat/596-backup-cron-alerting` | `287f5341d2d9` | 43 | pr-merged-tip | merged-pr-record | 680 |
| `fix/dr-issues-pat-name` | `02717b077307` | 43 | pr-merged-tip | merged-pr-record | 701 |
| `fix/pricing-cleanup` | `06a0d29663e0` | 43 | pr-merged-tip | merged-pr-record | 704 |
| `fix/mobile-responsive` | `62cce195e01f` | 43 | ancestry | reachable-from-main | — |
| `feat/welcome-segments` | `783aea1b5d3b` | 43 | pr-merged-tip | merged-pr-record | 708 |
| `chore/682-pricing-json-guard` | `b2fe9772265c` | 43 | ancestry | reachable-from-main | — |
| `chore/690-status-vocabulary` | `b2fe9772265c` | 43 | ancestry | reachable-from-main | — |
| `docs/703-quickstart-docs` | `b2fe9772265c` | 43 | ancestry | reachable-from-main | — |
| `feat/427-tortoise-planning-skill` | `b2fe9772265c` | 43 | ancestry | reachable-from-main | — |
| `feat/663-zero-email-signup` | `b2fe9772265c` | 43 | ancestry | reachable-from-main | — |
| `feat/683-max-limits-enforcement` | `b2fe9772265c` | 43 | ancestry | reachable-from-main | — |
| `feat/688-eventlog-read-after` | `b2fe9772265c` | 43 | ancestry | reachable-from-main | — |
| `fix/343-client-graceful` | `b2fe9772265c` | 43 | ancestry | reachable-from-main | — |
| `fix/345-domain-vocabulary` | `b2fe9772265c` | 43 | ancestry | reachable-from-main | — |
| `fix/356-ontology-endpoints` | `b2fe9772265c` | 43 | ancestry | reachable-from-main | — |
| `fix/509-stale-search-tests` | `b2fe9772265c` | 43 | ancestry | reachable-from-main | — |
| `fix/527-signup-form` | `b2fe9772265c` | 43 | ancestry | reachable-from-main | — |
| `fix/652-source-inheritance-revert` | `b2fe9772265c` | 43 | ancestry | reachable-from-main | — |
| `fix/656-backup-tier-solo` | `b2fe9772265c` | 43 | ancestry | reachable-from-main | — |
| `fix/686-team-limit-failclosed` | `b2fe9772265c` | 43 | ancestry | reachable-from-main | — |
| `fix/689-retract-tombstone` | `b2fe9772265c` | 43 | ancestry | reachable-from-main | — |
| `fix/702-selfhosted-mcp-deadend` | `b2fe9772265c` | 43 | ancestry | reachable-from-main | — |
| `fix/705-onboard-embedded` | `b2fe9772265c` | 43 | ancestry | reachable-from-main | — |
| `fix/706-redislite-message` | `b2fe9772265c` | 43 | ancestry | reachable-from-main | — |
| `fix/707-init-api-key-validation` | `b2fe9772265c` | 43 | ancestry | reachable-from-main | — |
| `fix/team-500` | `49a7cbcd11a7` | 43 | pr-merged-tip | merged-pr-record | 712 |
| `feat/373-waitlist-form` | `cb12210d8c90` | 43 | pr-merged-tip | merged-pr-record | 723 |
| `fix/hero-cta-signup` | `a5382233ff84` | 42 | pr-merged-tip | merged-pr-record | 726 |
| `fix/547-supersede-validation` | `4c17616ffe08` | 42 | ancestry | reachable-from-main | — |
| `fix/651-nand-phi-docs` | `68063e690a7f` | 42 | ancestry | reachable-from-main | — |
| `fix/legal-e2e-obfuscated-mailto` | `c4eed197010d` | 42 | ancestry | reachable-from-main | — |
| `feat/688-eventlog-tail` | `64b9b4d65620` | 42 | ancestry | reachable-from-main | — |
| `fix/686-limit-failclosed` | `413da68d88f7` | 42 | ancestry | reachable-from-main | — |
| `fix/687-auth-key-scan` | `fe07ba11c0c7` | 42 | ancestry | reachable-from-main | — |
| `fix/400-ep-nplus1` | `157e5073f875` | 42 | ancestry | reachable-from-main | — |
| `chore/677-postdeploy-legal-e2e` | `8fbc3cf7c316` | 42 | ancestry | reachable-from-main | — |
| `feat/673-telegram-alerts` | `e379b2ab782f` | 42 | ancestry | reachable-from-main | — |
| `fix/478-mcp-db-target` | `2333f1fdd84a` | 42 | ancestry | reachable-from-main | — |
| `fix/web-audit` | `1c22edb83889` | 42 | pr-merged-tip | merged-pr-record | 738 |
| `fix/welcome-e2e` | `55544b1b088e` | 42 | pr-merged-tip | merged-pr-record | 739 |
| `feat/736-x-signup-event` | `85805106c6ca` | 42 | ancestry | reachable-from-main | — |
| `fix/647-test-suite` | `d3452cff570c` | 42 | ancestry | reachable-from-main | — |
| `fix/backend-batch` | `ab9f0f84d993` | 42 | pr-merged-tip | merged-pr-record | 754 |
| `fix/website-1` | `836440fd1b65` | 42 | pr-merged-tip | merged-pr-record | 755 |
| `fix/website-2` | `13b95e539a02` | 42 | pr-merged-tip | merged-pr-record | 756 |
| `fix/test-infra` | `dad7d5a26e12` | 42 | pr-merged-tip | merged-pr-record | 757 |
| `feat/312-capture-sdk` | `376ab29d1cc6` | 42 | pr-merged-tip | merged-pr-record | 721 |
| `fix/tos-dollar-guard` | `f37eedee1561` | 42 | pr-merged-tip | merged-pr-record | 760 |
| `fix/713-index-github` | `3e19ff0ebb95` | 42 | ancestry | reachable-from-main | — |
| `feat/705-onboard-embedded-db` | `e86a744efefd` | 42 | ancestry | reachable-from-main | — |
| `fix/689-retraction-tombstone` | `10f354b92d3e` | 42 | ancestry | reachable-from-main | — |
| `feat/683-enforce-limits` | `3deb6bff6597` | 42 | ancestry | reachable-from-main | — |
| `fix/652-evidence-revert-prior` | `86074649b7bf` | 42 | ancestry | reachable-from-main | — |
| `fix/685-last-used-at` | `2c593c0f166a` | 42 | ancestry | reachable-from-main | — |
| `fix/548-sdk-jsonl-events` | `b97ba7c910a9` | 42 | ancestry | reachable-from-main | — |
| `feat/681-usage-metering` | `f057c1f725fd` | 42 | ancestry | reachable-from-main | — |
| `feat/692-event-replay` | `63264e7461e1` | 42 | ancestry | reachable-from-main | — |
| `feat/684-mcp-tier-limits` | `817ba31f7604` | 42 | ancestry | reachable-from-main | — |
| `fix/656-backup-tier-gate` | `70126c7438be` | 42 | ancestry | reachable-from-main | — |
| `chore/682-pricing-guard-test-hygiene` | `2b3f4cac1be4` | 42 | ancestry | reachable-from-main | — |
| `feat/278-ollama-local-mode` | `c0856b6b0189` | 42 | ancestry | reachable-from-main | — |
| `feat/706-redislite-message` | `bec2264a08a0` | 42 | pr-merged-tip | merged-pr-record | 716 |
| `fix/657-welcome-footer-layout` | `6c3842945871` | 42 | ancestry | reachable-from-main | — |
| `fix/flaky-signup-test` | `5a9e588db3e0` | 42 | pr-merged-tip | merged-pr-record | 774 |
| `docs/549-epic-docs-merge` | `bcdb45f95f13` | 42 | pr-merged-tip | merged-pr-record | 791 |
| `test/283-e2e-gaps` | `5f287a3f064e` | 42 | pr-merged-tip | merged-pr-record | 790 |
| `epic/264-insight-mining` | `87fef08670ea` | 42 | pr-merged-tip | merged-pr-record | 777 |
| `feat/issue-284-entity-resolution-closeout` | `f8dd5dba0d84` | 42 | ancestry | reachable-from-main | — |
| `fix/797-postmerge-validation` | `73ca177dbeba` | 42 | ancestry | reachable-from-main | — |
| `fix/selfhost-smoke-redirect` | `49d33460b764` | 42 | ancestry | reachable-from-main | — |
| `feat/753-directed-nand` | `88a9ed5989f0` | 42 | pr-merged-tip | merged-pr-record | 795 |
| `chore/654-reconcile-schedule` | `8a16048eacdc` | 42 | ancestry | reachable-from-main | — |
| `feat/655-team-backup-sweep` | `aa5829f37d1f` | 42 | ancestry | reachable-from-main | — |
| `feat/669-child-769-schema-migrations` | `93069732425f` | 42 | pr-merged-tip | merged-pr-record | 792 |
| `chore/690-status-vocab` | `de69e367e6b0` | 42 | ancestry | reachable-from-main | — |
| `chore/661-label-data-loss` | `ad38eb94562c` | 42 | ancestry | reachable-from-main | — |
| `fix/797-postmerge-validation-full` | `a687ec8d90db` | 42 | ancestry | reachable-from-main | — |
| `fix/suite-red` | `a05a3dcbcf67` | 42 | pr-merged-tip | merged-pr-record | 808 |
| `fix/802-provision-auth` | `751b0c34ad8d` | 42 | ancestry | reachable-from-main | — |
| `fix/798-python-ci` | `e45d4941d0d5` | 42 | ancestry | reachable-from-main | — |
| `feat/doctor-path` | `fe8c8ce779ba` | 42 | pr-merged-tip | merged-pr-record | 720 |
| `chore/660-fly-token-hardening` | `3fc6702e60be` | 42 | ancestry | reachable-from-main | — |
| `fix/ops-followups-673-692-686-713` | `53bb0a2aa72b` | 42 | ancestry | reachable-from-main | — |
| `fix/ep-followups-651-400` | `95aabcb75ee1` | 42 | ancestry | reachable-from-main | — |
| `feat/592-topic-summarization` | `ea9cb64bc7a4` | 42 | ancestry | reachable-from-main | — |
| `feat/714-dashboard-session-detail` | `80304b8fcff8` | 42 | ancestry | reachable-from-main | — |
| `fix/ingest-redis` | `37a1f7dde57f` | 42 | pr-merged-tip | merged-pr-record | 813 |
| `fix/assert-cluster` | `e3c5a191fd71` | 42 | pr-merged-tip | merged-pr-record | 815 |
| `fix/mcp-asyncio` | `d64f5d7d2d08` | 42 | pr-merged-tip | merged-pr-record | 814 |
| `fix/ci-validation-bugs` | `16e3ddcc5f45` | 42 | pr-merged-tip | merged-pr-record | 789 |
| `fix/repair-432-clobber` | `f353f95af015` | 42 | ancestry | reachable-from-main | — |
| `fix/ci-dedup2` | `5dbca167ee0d` | 42 | pr-merged-tip | merged-pr-record | 828 |
| `docs/fix-deploy-token-scope` | `792965b7ad67` | 42 | ancestry | reachable-from-main | — |
| `feat/281-instantiates-aboutobject` | `651d8f849435` | 42 | pr-merged-tip | merged-pr-record | 796 |
| `feat/300-dashboard` | `7112c1df7ff9` | 42 | pr-merged-tip | merged-pr-record | 788 |
| `feat/280-index-concurrency` | `9d11ce69ccaf` | 42 | pr-merged-tip | merged-pr-record | 793 |
| `chore/version-bump-deploytest` | `5a2b4eb495d6` | 42 | ancestry | reachable-from-main | — |
| `feat/833-mcp-mount` | `8f1cbca2d9cb` | 41 | ancestry | reachable-from-main | — |
| `docs/598-prior-art-screening` | `4bae889d1595` | 41 | pr-merged-tip | merged-pr-record | 846 |
| `fix/527-provision-jwt` | `f2c674902bec` | 41 | pr-merged-tip | merged-pr-record | 839 |
| `fix/647-suite-sweep` | `d7c175fd75c4` | 41 | pr-merged-tip | merged-pr-record | 845 |
| `chore/375-cross-repo-readmes` | `7fe4d4b714d9` | 41 | ancestry | reachable-from-main | — |
| `fix/suite-red2` | `6221cd323989` | 41 | pr-merged-tip | merged-pr-record | 848 |
| `fix/ep-cascade` | `4a6ec96b09bd` | 41 | ancestry | reachable-from-main | — |
| `fix/billing-surface` | `209d51f485bc` | 41 | pr-merged-tip | merged-pr-record | 850 |
| `fix/843-billing-routes` | `eecef0a6034b` | 41 | ancestry | reachable-from-main | — |
| `feat/669-child-767-auth-flip` | `5e009572d8ba` | 41 | pr-merged-tip | merged-pr-record | 851 |
| `feat/326-ep-propagation` | `d7976cc04cf4` | 41 | pr-merged-tip | merged-pr-record | 853 |
| `scratch/801-baseline` | `23942d2186e1` | 41 | ancestry | reachable-from-main | — |
| `fix/390-391-edge-guards` | `ff664f79b3f2` | 41 | pr-merged-tip | merged-pr-record | 862 |
| `fix/844-ep-directional` | `12ad42b63b6f` | 41 | pr-merged-tip | merged-pr-record | 852 |
| `fix/343-client-graceful-degradation` | `d52b8551e641` | 41 | pr-merged-tip | merged-pr-record | 866 |
| `feat/669-child-764-onboarding-health` | `16728287ded6` | 41 | pr-merged-tip | merged-pr-record | 861 |
| `test/748-749-revenue-surfaces` | `1e38b39843f8` | 41 | pr-merged-tip | merged-pr-record | 869 |
| `feat/669-child-768-backup-seam` | `de975962bf2c` | 41 | pr-merged-tip | merged-pr-record | 858 |
| `feat/669-child-763-invitations` | `87cc0bdfb69c` | 41 | pr-merged-tip | merged-pr-record | 864 |
| `fix/855-nand-propagation` | `0c976702f5fa` | 41 | pr-merged-tip | merged-pr-record | 871 |
| `feat/669-child-765-writer-inventory` | `6a169e070889` | 41 | pr-merged-tip | merged-pr-record | 874 |
| `fix/801-signup-email-ratelimit` | `c9b4334e9628` | 41 | pr-merged-tip | merged-pr-record | 860 |
| `feat/304-hosted-cli` | `cb714e1b4603` | 41 | pr-merged-tip | merged-pr-record | 875 |
| `feat/669-child-766-postflip-verify` | `ea9eae9b4bc1` | 41 | pr-merged-tip | merged-pr-record | 887 |
| `feat/889-mcp-telemetry` | `369e98efdece` | 41 | pr-merged-tip | merged-pr-record | 890 |
| `fix/880-semantic-dedup-degrade` | `22f37a6a0c24` | 41 | pr-merged-tip | merged-pr-record | 891 |
| `fix/880-ci-green-rebalance` | `f48ac582e7cf` | 41 | pr-merged-tip | merged-pr-record | 892 |
| `fix/881-hero-cta-pe-on` | `6d5146f72903` | 41 | pr-merged-tip | merged-pr-record | 893 |
| `chore/669-plan-docs` | `1d83d85fe5b5` | 41 | pr-merged-tip | merged-pr-record | 895 |
| `fix/914-required-checks` | `e587e5dbce1f` | 40 | ancestry | reachable-from-main | — |
| `fix/669-audit-dsn-live` | `90e112b65227` | 40 | pr-merged-tip | merged-pr-record | 919 |
| `fix/879-crash-test-rescope` | `2b319f1c1b8f` | 40 | pr-merged-tip | merged-pr-record | 921 |
| `fix/925-metering-readback` | `5b9435fea1e7` | 40 | pr-merged-tip | merged-pr-record | 929 |
| `fix/923-metering-degrade` | `a7e42e7a081e` | 40 | pr-merged-tip | merged-pr-record | 934 |
| `fix/924-backups-graph-name` | `c9202330b923` | 40 | pr-merged-tip | merged-pr-record | 935 |
| `fix/915-rebase3-tmp` | `f5e39bf7c2a7` | 40 | pr-merged-tip | merged-pr-record | 941 |
| `chore/323-search-capstone` | `ea9be5465d67` | 40 | ancestry | reachable-from-main | — |
| `docs/epic909-plan` | `ea9be5465d67` | 40 | ancestry | reachable-from-main | — |
| `feat/901-connect-workflow` | `ea9be5465d67` | 40 | ancestry | reachable-from-main | — |
| `feat/epic909-impl` | `661222d43d1d` | 39 | ancestry | reachable-from-main | — |
| `test/303-hosted-e2e` | `661222d43d1d` | 39 | ancestry | reachable-from-main | — |
| `fix/942-selfhost-trust` | `873dac023ad6` | 39 | pr-merged-tip | merged-pr-record | 974 |
| `feat/epic909-945-harness` | `2b87dbd96b1c` | 39 | pr-merged-tip | merged-pr-record | 975 |
| `feat/epic909-948-ontology` | `9d09f7386079` | 39 | pr-merged-tip | merged-pr-record | 973 |
| `feat/529-onboarding-variants` | `26f67ed2feb7` | 39 | pr-merged-tip | merged-pr-record | 977 |
| `feat/epic909-947-quota` | `2e45bb6b308d` | 39 | pr-merged-tip | merged-pr-record | 976 |
| `feat/epic909-952-commit-schema` | `d0fbca4276c4` | 39 | pr-merged-tip | merged-pr-record | 987 |
| `feat/epic909-950-pack-content` | `8e8284c18dc8` | 39 | pr-merged-tip | merged-pr-record | 988 |
| `fix/529-mcp-url-trailing-slash` | `9006fbfd7f5e` | 39 | pr-merged-tip | merged-pr-record | 984 |
| `feat/epic909-951-domain-loader` | `116b7b005004` | 39 | pr-merged-tip | merged-pr-record | 989 |
| `chore/verify-onboarding` | `c837e89338d7` | 39 | ancestry | reachable-from-main | — |
| `ci-check-local` | `2c280ab97936` | 39 | ancestry | reachable-from-main | — |
| `ciinv` | `2c280ab97936` | 39 | ancestry | reachable-from-main | — |
| `feat/308-abuse-prevention` | `708afce81b39` | 39 | pr-merged-tip | merged-pr-record | 983 |
| `feat/416-mining-pilot` | `4e2b9f4bb7f8` | 39 | ancestry | reachable-from-main | — |
| `fix/993-mcp-entrypoint` | `09ef9e501990` | 39 | pr-merged-tip | merged-pr-record | 997 |
| `feat/epic909-960-metrics` | `027e56db7499` | 39 | pr-merged-tip | merged-pr-record | 996 |
| `fix/331-crash-batch` | `650082b89119` | 39 | pr-merged-tip | merged-pr-record | 971 |
| `feat/303-e2e-suite` | `7b45ab2c4d3b` | 39 | pr-merged-tip | merged-pr-record | 980 |
| `feat/529-harness-onboarding` | `8cf753cf98cd` | 39 | pr-merged-tip | merged-pr-record | 970 |
| `fix/992-998-ep-confidence` | `a25e44b92c01` | 39 | ancestry | reachable-from-main | — |
| `landing-run` | `a25e44b92c01` | 39 | ancestry | reachable-from-main | — |
| `docs/epic909-docs` | `1b5f0d53a006` | 39 | pr-merged-tip | merged-pr-record | 999 |
| `chore/291-capstone` | `98cc1eb1d6db` | 39 | ancestry | reachable-from-main | — |
| `verify/291-capstone` | `98cc1eb1d6db` | 39 | ancestry | reachable-from-main | — |
| `fix/992-ep-draft-tests` | `c59c891fb7dd` | 39 | pr-merged-tip | merged-pr-record | 1004 |
| `feat/epic909-953-commit-endpoint` | `94affcdd2812` | 39 | pr-merged-tip | merged-pr-record | 1006 |
| `verify/epic909-main-check` | `f71e13718f53` | 39 | ancestry | reachable-from-main | — |
| `pr-1015` | `6ccac2466db8` | 39 | ancestry | reachable-from-main | — |
| `docs/state-centric-memory` | `c5320e7d0eae` | 39 | pr-merged-tip | merged-pr-record | 1016 |
| `docs/state-centric-ontology` | `078651e1bf5d` | 39 | pr-merged-tip | merged-pr-record | 1017 |
| `docs/extraction-state-centric` | `0b11d8402560` | 39 | pr-merged-tip | merged-pr-record | 1018 |
| `docs/pointkinds-statement` | `a7a4b0e77488` | 39 | pr-merged-tip | merged-pr-record | 1022 |
| `fix/1005-redislite-leak` | `af4fc8b5340e` | 39 | pr-merged-tip | merged-pr-record | 1020 |
| `fix/1005-end-sweep` | `5c8bf8913388` | 39 | pr-merged-tip | merged-pr-record | 1027 |
| `fix/1028-metering-seam` | `abf0918772c3` | 38 | pr-merged-tip | merged-pr-record | 1062 |
| `feat/900-index-workflow` | `3c7418533af6` | 38 | ancestry | reachable-from-main | — |
| `feat/902-ingest-workflow` | `3c7418533af6` | 38 | ancestry | reachable-from-main | — |
| `feat/900-t10-ont-note` | `c57283599bee` | 38 | pr-merged-tip | merged-pr-record | 1063 |
| `pr-1069` | `0f5d918194f9` | 38 | ancestry | reachable-from-main | — |
| `fix/1001-migration-renumber` | `4eb24ae588bf` | 38 | ancestry | reachable-from-main | — |
| `fix/migration-0012b-unique` | `b4fe182a7945` | 38 | pr-merged-tip | merged-pr-record | 1074 |
| `fix/0015-abuse-policy-idempotent` | `eefdfd89a59d` | 38 | pr-merged-tip | merged-pr-record | 1075 |
| `fix/migration-0015b-unique` | `d5b2a0d43bbb` | 38 | pr-merged-tip | merged-pr-record | 1076 |
| `fix/migration-timestamp-names` | `2417cca16542` | 38 | pr-merged-tip | merged-pr-record | 1077 |
| `feat/900-t1-file-indexer` | `187e31d8e4f9` | 38 | pr-merged-tip | merged-pr-record | 1070 |
| `cleanup-tmp-1083` | `5a62181f1ad6` | 38 | ancestry | reachable-from-main | — |
| `feat/1083-login-routing` | `5a62181f1ad6` | 38 | ancestry | reachable-from-main | — |
| `tmp-main-check` | `5f665c114c54` | 38 | ancestry | reachable-from-main | — |
| `fix/orphan-sweep-deferral` | `c1f06ef906af` | 38 | pr-merged-tip | merged-pr-record | 1072 |
| `fix/522-embedded-is-operator-index` | `af147b178c41` | 38 | ancestry | reachable-from-main | — |
| `verify/1001-main-check` | `2eec487e5d51` | 38 | ancestry | reachable-from-main | — |
| `fix/index-test-embedded` | `f2b5cf662e8d` | 38 | ancestry | reachable-from-main | — |
| `fix/1001-migration-consolidate` | `c46510dd8be0` | 38 | ancestry | reachable-from-main | — |
| `feat/900-t3-index-sdk` | `ae0a27ac15a4` | 38 | pr-merged-tip | merged-pr-record | 1093 |
| `feat/902-a1-validation` | `1215e3c4b8ac` | 38 | pr-merged-tip | merged-pr-record | 1091 |
| `feat/902-a4-batch-id` | `2ecf6c461792` | 38 | pr-merged-tip | merged-pr-record | 1092 |
| `chore/1097-migration-docfix` | `9a1a57dd9148` | 38 | ancestry | reachable-from-main | — |
| `fix/1005-ci-orphan-gate` | `bb6a1f40a786` | 38 | ancestry | reachable-from-main | — |
| `cleanup-tmp-1786630876` | `da0fcc11d75a` | 38 | ancestry | reachable-from-main | — |
| `feat/1081-abuse-protection` | `da0fcc11d75a` | 38 | ancestry | reachable-from-main | — |
| `cleanup-tmp-1786630879` | `914be0b28ae1` | 38 | ancestry | reachable-from-main | — |
| `feat/1082-claim-path` | `914be0b28ae1` | 38 | ancestry | reachable-from-main | — |
| `feat/902-s8-direct-edge` | `e1940c22d536` | 38 | pr-merged-tip | merged-pr-record | 1106 |
| `fix/1005-orphan-instrument` | `9992a7cac83a` | 38 | ancestry | reachable-from-main | — |
| `feat/902-a2-failure-contract` | `b684f4991e35` | 38 | pr-merged-tip | merged-pr-record | 1117 |
| `cleanup-tmp-1112` | `88fb90d5e22e` | 38 | ancestry | reachable-from-main | — |
| `feat/1112-dashboard-ci` | `88fb90d5e22e` | 38 | ancestry | reachable-from-main | — |
| `cleanup-tmp-1119` | `fb59df903357` | 38 | ancestry | reachable-from-main | — |
| `fix/1118-wf-trigger` | `fb59df903357` | 38 | ancestry | reachable-from-main | — |
| `fix/backfill-embedded-gate` | `38a5329467a6` | 38 | ancestry | reachable-from-main | — |
| `cleanup-tmp-1124` | `292a965ad3b8` | 38 | ancestry | reachable-from-main | — |
| `fix/1118-preflight-jq` | `292a965ad3b8` | 38 | ancestry | reachable-from-main | — |
| `cleanup-tmp-1125` | `422b69ddd98c` | 38 | ancestry | reachable-from-main | — |
| `fix/1124-pages-perpage` | `422b69ddd98c` | 38 | ancestry | reachable-from-main | — |
| `cleanup-tmp-1126` | `51671e35dc8c` | 38 | ancestry | reachable-from-main | — |
| `fix/1125-perpage-clean` | `51671e35dc8c` | 38 | ancestry | reachable-from-main | — |
| `fix/website-tiers-test` | `782e7ee3a8a7` | 38 | ancestry | reachable-from-main | — |
| `cleanup-tmp-1148` | `14c4c496cdf5` | 38 | ancestry | reachable-from-main | — |
| `fix/1008-queue-health` | `104fda0da060` | 38 | pr-merged-tip | merged-pr-record | 1011 |
| `fix/1166-cli-serve-conflict` | `c705558569c8` | 38 | ancestry | reachable-from-main | — |
| `feat/900-t4-e2e-suite` | `e989217f6444` | 38 | pr-merged-tip | merged-pr-record | 1153 |
| `fix/1095-migration-drift-gate` | `2001e101b71b` | 38 | ancestry | reachable-from-main | — |
| `fix/1145-research-append-relative` | `8e40a5464b8b` | 38 | ancestry | reachable-from-main | — |
| `fix/1160-mining-dedup-docs` | `8e40a5464b8b` | 38 | ancestry | reachable-from-main | — |
| `fix/1168-verify-chain-skill-sync` | `8e40a5464b8b` | 38 | ancestry | reachable-from-main | — |
| `chore/1146-pin-falkordb` | `e070967b615a` | 38 | pr-merged-tip | merged-pr-record | 1169 |
| `feat/900-t5-surfacing` | `2fb026d2365e` | 38 | pr-merged-tip | merged-pr-record | 1172 |
| `fix/981-mcp-config-parity` | `5c1f066e1f49` | 38 | pr-merged-tip | merged-pr-record | 1007 |
| `fix/1002-cors-allowlist` | `8824f4af6c41` | 38 | pr-merged-tip | merged-pr-record | 1175 |
| `chore/1067-welcome-e2e-secrets` | `fb07a602f3b9` | 38 | pr-merged-tip | merged-pr-record | 1176 |
| `fix/p2-github-error-text` | `bfe2557a97ea` | 38 | pr-merged-tip | merged-pr-record | 1010 |
| `fix/perf-note-indexing` | `31cdd209e508` | 38 | pr-merged-tip | merged-pr-record | 1030 |
| `feat/welcome-path-chooser` | `21116dd665f7` | 38 | ancestry | reachable-from-main | — |
| `feat/1148-protect-account` | `a59ac9b23c7d` | 38 | ancestry | reachable-from-main | — |
| `feat/1021-tiered-selection` | `486ad04188f8` | 38 | pr-merged-tip | merged-pr-record | 1184 |
| `fix/885-server-auth-abuse` | `f01cbc6f77c2` | 38 | ancestry | reachable-from-main | — |
| `fix/welcome-e2e-monitor` | `b39ab72b52b5` | 38 | pr-merged-tip | merged-pr-record | 1181 |
| `fix/969-negative-leg-masking` | `70807249eadc` | 38 | pr-merged-tip | merged-pr-record | 1195 |
| `feat/900-t9-sc4-markers` | `480d38075848` | 38 | pr-merged-tip | merged-pr-record | 1202 |
| `fix/1189-welcome-e2e-tests` | `f5cc7f6b5bfe` | 38 | pr-merged-tip | merged-pr-record | 1205 |
| `feat/1177-307-invite-accept-email` | `b642261cd72e` | 38 | ancestry | reachable-from-main | — |
| `pr-1208` | `ad4809f6f53f` | 37 | ancestry | reachable-from-main | — |
| `feat/epic909-calibration-tooling` | `54fd134bbb41` | 37 | pr-merged-tip | merged-pr-record | 1213 |
| `docs/calibration-specs` | `9dd6fd4280a6` | 37 | pr-merged-tip | merged-pr-record | 1214 |
| `pr-1215` | `2cb1e727f0f5` | 37 | ancestry | reachable-from-main | — |
| `feat/1221-email-integration-test` | `f3d7615d0799` | 37 | ancestry | reachable-from-main | — |
| `docs/2026-08-15-scoping-plans` | `640dab22a024` | 37 | ancestry | reachable-from-main | — |
| `fix/1224-oauth-state-expiry` | `2bf2fd1d7092` | 37 | ancestry | reachable-from-main | — |
| `fix/1189-welcome-e2e` | `1bdb8650d79f` | 37 | ancestry | reachable-from-main | — |
| `feat/900-tci-registration` | `8e3fe8e8cd07` | 37 | pr-merged-tip | merged-pr-record | 1207 |
| `feat/900-tdocs` | `1b41de1e0343` | 37 | pr-merged-tip | merged-pr-record | 1217 |
| `fix/20260813000005-migration-prefix` | `5c6e30928ac8` | 37 | ancestry | reachable-from-main | — |
| `feat/900-t12-restore` | `f6a833ac5410` | 37 | pr-merged-tip | merged-pr-record | 1216 |
| `fix/1235-migration-guards` | `760e48c1e2e5` | 37 | ancestry | reachable-from-main | — |
| `feat/309-security-page` | `c3b1016a7654` | 37 | ancestry | reachable-from-main | — |
| `chore/1221-email-env` | `89eeeda502db` | 37 | ancestry | reachable-from-main | — |
| `feat/334-wiring-phase01` | `d321348af817` | 37 | ancestry | reachable-from-main | — |
| `fix/822-llm-extraction-default` | `d192c3bff991` | 37 | ancestry | reachable-from-main | — |
| `pr-1194` | `d192c3bff991` | 37 | ancestry | reachable-from-main | — |
| `fix/ci-fast-leg-watchdog` | `bc6d79a38768` | 37 | ancestry | reachable-from-main | — |
| `feat/344-calibration-default` | `e4f246b3203d` | 37 | ancestry | reachable-from-main | — |
| `verify/epic909-prod` | `089c894ff6bb` | 37 | ancestry | reachable-from-main | — |
| `verify/epic909-prod2` | `089c894ff6bb` | 37 | ancestry | reachable-from-main | — |
| `chore/492-uv-adoption` | `4e9df9bb7494` | 37 | ancestry | reachable-from-main | — |
| `feat/388-connector-source-nodes` | `65e7b1bb863d` | 37 | ancestry | reachable-from-main | — |
| `feat/902-a3-idempotency` | `1f62c46005e1` | 37 | pr-merged-tip | merged-pr-record | 1256 |
| `fix/ci-selection-manifest` | `0fb985cb655e` | 37 | pr-merged-tip | merged-pr-record | 1260 |
| `feat/946-gate-window2-rubric-validation` | `db9136d07656` | 37 | pr-merged-tip | merged-pr-record | 1259 |
| `feat/902-a10-rebuild-durability` | `70471b245a55` | 37 | pr-merged-tip | merged-pr-record | 1265 |
| `feat/903-dreaming-ep` | `c1b0bca2f9b5` | 37 | ancestry | reachable-from-main | — |
| `fix/1149-monitor-false-positive` | `da34856560ff` | 37 | ancestry | reachable-from-main | — |
| `feat/316-vector-benchmark` | `d9648ed82a6a` | 37 | ancestry | reachable-from-main | — |
| `feat/316-vector-benchmark-rebased` | `d9648ed82a6a` | 37 | ancestry | reachable-from-main | — |
| `feat/946-validate-extractor` | `6a469206c19d` | 37 | ancestry | reachable-from-main | — |
| `fix/mcp-ingest-promotion-keyerror` | `d83dc28c96b1` | 37 | pr-merged-tip | merged-pr-record | 1274 |
| `feat/epic909-graph-construct` | `407cd862ba6e` | 37 | pr-merged-tip | merged-pr-record | 1278 |
| `feat/1272-calibrate-prompts` | `a32ab597a822` | 37 | ancestry | reachable-from-main | — |
| `feat/405-domain-constraints` | `ff1e83650659` | 37 | ancestry | reachable-from-main | — |
| `feat/1250-epic903-fixtures` | `7c7a77a9ff70` | 37 | ancestry | reachable-from-main | — |
| `fix/ci-manifest-drift2` | `789754b7c374` | 37 | pr-merged-tip | merged-pr-record | 1283 |
| `fix/1231-redislite-lifecycle` | `1c234e7dd790` | 37 | ancestry | reachable-from-main | — |
| `feat/524-oauth-mcp` | `b28aae404a9d` | 37 | ancestry | reachable-from-main | — |
| `feat/395-local-ep` | `0b856004c128` | 37 | ancestry | reachable-from-main | — |
| `feat/1239-epic903-diagnostics` | `6edb11ef04f8` | 37 | ancestry | reachable-from-main | — |
| `fix/1155-gh-event-collision` | `e592f8a147bc` | 37 | ancestry | reachable-from-main | — |
| `feat/438-candidate-exposure` | `2911919fadae` | 37 | ancestry | reachable-from-main | — |
| `feat/348-audit-tool` | `9537f59053eb` | 37 | ancestry | reachable-from-main | — |
| `fix/ci-manifest-drift3` | `89b7ae29b914` | 37 | pr-merged-tip | merged-pr-record | 1300 |
| `feat/1240-epic903-freshness` | `8203d038bb79` | 37 | ancestry | reachable-from-main | — |
| `fix/1227-final` | `4674ba9591a1` | 37 | ancestry | reachable-from-main | — |
| `feat/318-pack-isolation` | `1b6d16d829b8` | 37 | ancestry | reachable-from-main | — |
| `fix/ci-surfaces-drift-3c` | `d1199b84f362` | 37 | ancestry | reachable-from-main | — |
| `feat/1241-epic903-scheduler` | `54377e30aadf` | 37 | ancestry | reachable-from-main | — |
| `feat/1272-exec` | `044400c95c5d` | 37 | pr-merged-tip | merged-pr-record | 1310 |
| `fix/email-scheduler` | `58a6631b9d80` | 37 | ancestry | reachable-from-main | — |
| `fix/1302-rebuild` | `3637765530d8` | 37 | pr-merged-tip | merged-pr-record | 1315 |
| `fix/1272-review-fixes` | `69989611542a` | 37 | pr-merged-tip | merged-pr-record | 1316 |
| `feat/1272-present` | `47bf4aae7a6d` | 37 | ancestry | reachable-from-main | — |
| `feat/1244-epic903-moderouter` | `6de10266e377` | 37 | ancestry | reachable-from-main | — |
| `feat/1243-epic903-retention` | `de63580ec9b4` | 37 | ancestry | reachable-from-main | — |
| `fix/email-notify-reborn` | `d45486e234f6` | 37 | ancestry | reachable-from-main | — |
| `feat/1242-epic903-warmstart` | `d49af6d9326d` | 37 | ancestry | reachable-from-main | — |
| `feat/1247-epic903-lifecycle` | `6c1d8e9add71` | 37 | ancestry | reachable-from-main | — |
| `feat/1248-epic903-staleness-eval` | `0244f41fbeff` | 37 | ancestry | reachable-from-main | — |
| `feat/1245-epic903-observability` | `48735a51d8dc` | 37 | ancestry | reachable-from-main | — |
| `feat/1246-epic903-hosted` | `5ae94f364e85` | 36 | ancestry | reachable-from-main | — |
| `feat/1249-epic903-mcp` | `6ac469040f3c` | 36 | ancestry | reachable-from-main | — |
| `feat/1254-epic903-capstone` | `b69e6a5cf73a` | 36 | ancestry | reachable-from-main | — |
| `tmp/main-check` | `b69e6a5cf73a` | 36 | ancestry | reachable-from-main | — |
| `tmp/main-check2` | `b69e6a5cf73a` | 36 | ancestry | reachable-from-main | — |
| `tmp/main-clean` | `b69e6a5cf73a` | 36 | ancestry | reachable-from-main | — |
| `feat/903-diagnostics-prodscale` | `6cb7290ead5c` | 36 | ancestry | reachable-from-main | — |
| `fix/calibration-7080` | `5a2a4d6738d8` | 36 | ancestry | reachable-from-main | — |
| `fix/ci-v3` | `8bb1607704bc` | 36 | pr-merged-tip | merged-pr-record | 1341 |
| `feat/1287-auth-v4` | `4946ca1f7ad9` | 36 | ancestry | reachable-from-main | — |
| `fix/1280-dashboard-v2b` | `f63d37176dea` | 36 | pr-merged-tip | merged-pr-record | 1342 |
| `fix/signup-layout` | `6b1742aafef4` | 36 | pr-merged-tip | merged-pr-record | 1344 |
| `feat/1287-auth-v5` | `be05757e6da8` | 35 | pr-merged-tip | merged-pr-record | 1345 |
| `fix/deploy-secret-gate` | `7380a31aaa2d` | 35 | pr-merged-tip | merged-pr-record | 1347 |
| `fix/session-source-agentkind` | `cc60a653fc6b` | 34 | pr-merged-tip | merged-pr-record | 1354 |
| `feat/1144-retrieval-eval` | `938762a883d1` | 34 | ancestry | reachable-from-main | — |
| `feat/1144-longmemevl-runner` | `03fa1b40d5cc` | 34 | ancestry | reachable-from-main | — |
| `fix/1359-falkordb-compat` | `d0866251f966` | 34 | ancestry | reachable-from-main | — |
| `tmp/main-reaper` | `d6dee91fb6ab` | 34 | ancestry | reachable-from-main | — |
| `fix/bench-degradation-vector` | `404179c565ba` | 34 | pr-merged-tip | merged-pr-record | 1364 |
| `feat/1272-parity` | `ca34761f73ab` | 34 | ancestry | reachable-from-main | — |
| `feat/seo-sitemap-consolidation` | `47fe3941458c` | 34 | ancestry | reachable-from-main | — |
| `feat/1350-parity-fix` | `9668a549c674` | 34 | ancestry | reachable-from-main | — |
| `feat/1353-relationships-decoration` | `f15cd62c6240` | 34 | ancestry | reachable-from-main | — |
| `fix/seo-301-immutable-headers` | `9e8c4e689d7a` | 34 | ancestry | reachable-from-main | — |
| `feat/1386-supersession` | `7cbd503d899e` | 34 | ancestry | reachable-from-main | — |
| `fix/legal-e2e-skip-external-crawl` | `b60095ade6b8` | 34 | ancestry | reachable-from-main | — |
| `feat/1348-deeper-candidate-pool` | `9b2279dedf4a` | 34 | ancestry | reachable-from-main | — |
| `feat/1391-read-filter` | `faadb379087f` | 34 | ancestry | reachable-from-main | — |
| `feat/1369-lme-v2-ingest` | `40481c3e9b70` | 34 | ancestry | reachable-from-main | — |
| `fix/1400-tool-registry-count` | `887567e70715` | 34 | ancestry | reachable-from-main | — |
| `feat/1395-routing-config` | `51f219822dea` | 33 | ancestry | reachable-from-main | — |
| `docs/557-scoping-research` | `ee676e3e0eda` | 33 | ancestry | reachable-from-main | — |
| `feat/1350-status-projection` | `40ee81513440` | 33 | ancestry | reachable-from-main | — |
| `feat/1375-fallback-perf` | `f5f469eec668` | 33 | ancestry | reachable-from-main | — |
| `fix/1422-bench-kwarg` | `e0f366a9deae` | 33 | ancestry | reachable-from-main | — |
| `fix/gitignore-tortoise-file` | `9230be3bd349` | 33 | ancestry | reachable-from-main | — |
| `fix/1417-aboutEvent-untangle` | `09e4608a1f43` | 33 | ancestry | reachable-from-main | — |
| `fix/ci-test-drift` | `2f8bc94b134c` | 33 | ancestry | reachable-from-main | — |
| `fix/ci-battery-manifest` | `754522936714` | 33 | ancestry | reachable-from-main | — |
| `fix/1162-add-operator-ep` | `007914d3d695` | 33 | ancestry | reachable-from-main | — |
| `fix/1135-welcome-url` | `89b2f0dbc7d9` | 33 | ancestry | reachable-from-main | — |
| `fix/1438-postmerge-verdict` | `562a11b57378` | 33 | ancestry | reachable-from-main | — |
| `fix/1158-audit-sourcekind` | `3bb9177a404c` | 33 | ancestry | reachable-from-main | — |
| `feat/1163-ep-dirty-persist` | `f5fc0fd0728c` | 33 | ancestry | reachable-from-main | — |
| `chore/1436-loud-skips` | `16ea165593d6` | 33 | ancestry | reachable-from-main | — |
| `fix/1427-orphan-reap` | `ebdab467f85d` | 33 | ancestry | reachable-from-main | — |
| `tmp/lme-v2-run` | `2aed4a3d7e8d` | 33 | ancestry | reachable-from-main | — |
| `tmp/lme-full-run` | `2f7c3df83bb1` | 33 | ancestry | reachable-from-main | — |
| `feat/1418-object-event-slots` | `5027a2331e68` | 33 | ancestry | reachable-from-main | — |
| `fix/1382-ep-local-env` | `8e4cc71c4947` | 33 | pr-merged-tip | merged-pr-record | 1440 |
| `pr1467` | `4463e6b9992a` | 33 | ancestry | reachable-from-main | — |
| `feat/1350-s3-chunk-fixes` | `039aae8e4e14` | 33 | ancestry | reachable-from-main | — |
| `fix/1383-reaper-semantics` | `241562799a11` | 33 | pr-merged-tip | merged-pr-record | 1454 |
| `fix/1266-suite-cap` | `2f72de11f7b2` | 32 | ancestry | reachable-from-main | — |
| `fix/1439-postmerge-budget` | `3491a3723dd9` | 32 | pr-merged-tip | merged-pr-record | 1465 |
| `base-check` | `b367d69fc969` | 32 | ancestry | reachable-from-main | — |
| `fix/1474-postmerge-dedup` | `bc4ecc9734cd` | 32 | pr-merged-tip | merged-pr-record | 1480 |
| `fix/1475-lifecycle-finalize` | `e168eb420dda` | 32 | pr-merged-tip | merged-pr-record | 1482 |
| `fix/1477-measure` | `c4c417b92791` | 32 | pr-merged-tip | merged-pr-record | 1483 |
| `fix/1472-single-manifest` | `03ec3925555d` | 32 | pr-merged-tip | merged-pr-record | 1485 |
| `fix/1471-split-test-slow` | `a20c0f35bc0a` | 32 | pr-merged-tip | merged-pr-record | 1481 |
| `fix/1473-tier2-duration` | `17a2dbcacb42` | 32 | pr-merged-tip | merged-pr-record | 1487 |
| `docs/ci-process-research` | `8748991decb2` | 32 | pr-merged-tip | merged-pr-record | 1489 |
| `fix/1490-signup-login-page` | `aa8cb64f3379` | 32 | ancestry | reachable-from-main | — |
| `feat/303-ci-central` | `e8d1d69b8426` | 32 | ancestry | reachable-from-main | — |
| `fix/create-source-is-episodic` | `ecb9f5b87796` | 32 | ancestry | reachable-from-main | — |
| `fix/ci-email-flood` | `a373308880c0` | 32 | ancestry | reachable-from-main | — |
| `fix/1498-auth-gating` | `f86bad27e415` | 32 | ancestry | reachable-from-main | — |
| `fix/keepalive-test-pollution` | `89572d1aa5f5` | 32 | ancestry | reachable-from-main | — |
| `fix/1498-apikey-label` | `422208847163` | 32 | ancestry | reachable-from-main | — |
| `fix/1506-auth-gating` | `1ae193e71d5c` | 31 | ancestry | reachable-from-main | — |
| `fix/ci-remaining-red` | `f37c2f285622` | 31 | ancestry | reachable-from-main | — |
| `fix/1502-ci-red` | `9de006a1a2be` | 31 | ancestry | reachable-from-main | — |
| `feat/1503-lint-config` | `a269b589b470` | 31 | ancestry | reachable-from-main | — |
| `feat-1503-m` | `23c26746d33a` | 31 | ancestry | reachable-from-main | — |
| `tmp/lme-rerun` | `f060b3180987` | 31 | ancestry | reachable-from-main | — |
| `fix/1559-session-mint-429` | `1dd014934b03` | 30 | ancestry | reachable-from-main | — |
| `feat/1549-run-protocol` | `c87907d6f37b` | 30 | ancestry | reachable-from-main | — |
| `feat/1567-dashboard-latency` | `e23aea94a621` | 30 | ancestry | reachable-from-main | — |
| `feat/1566-welcome-in-app` | `5f220dc127f6` | 30 | ancestry | reachable-from-main | — |
| `feat/1529-failclosed-capture` | `0868358ee5d4` | 30 | ancestry | reachable-from-main | — |
| `fix/1566-e2e-redirect` | `a60b55e5e92d` | 30 | ancestry | reachable-from-main | — |
| `fix/1566-recovery-cap` | `a10aaddf94dc` | 30 | ancestry | reachable-from-main | — |
| `feat/1536-s4-merge` | `a75a3a51a2ad` | 29 | ancestry | reachable-from-main | — |
| `feat/1528-stats` | `8824bc50349e` | 29 | ancestry | reachable-from-main | — |
| `feat/1541-or-sparse` | `2b70893e330d` | 29 | ancestry | reachable-from-main | — |
| `fix/1591-team-500` | `b0fdc8d44446` | 29 | ancestry | reachable-from-main | — |
| `fix/1591-graph-ux` | `50ceba43fa3d` | 29 | ancestry | reachable-from-main | — |
| `feat/1623-billing` | `1e8413fd232c` | 29 | ancestry | reachable-from-main | — |
| `verify-t` | `9d226d89431c` | 29 | ancestry | reachable-from-main | — |
| `fix/1591-cors-500` | `d334a5097bf0` | 28 | ancestry | reachable-from-main | — |
| `fix/1591-graph-design` | `c0210b8ddaa4` | 28 | ancestry | reachable-from-main | — |
| `feat/1591-onboarding` | `bc4a960ec96c` | 28 | ancestry | reachable-from-main | — |
| `fix/1643-reentry` | `a59c39e29fe5` | 27 | ancestry | reachable-from-main | — |
| `fix/1643-card-design` | `d1c64451bc8b` | 27 | ancestry | reachable-from-main | — |
| `fix/1643-wizard-nav` | `3c390a6f0916` | 27 | ancestry | reachable-from-main | — |
| `fix/1643-skills-copy` | `f79559942fc3` | 27 | ancestry | reachable-from-main | — |
| `feat/1643-skills-install` | `f0244b7fe563` | 27 | ancestry | reachable-from-main | — |
| `fix/1643-skills-primer` | `bf0075cbc3a3` | 27 | ancestry | reachable-from-main | — |
| `feat/1349-embedder-swap` | `b63b17f1774e` | 27 | pr-merged-tip | merged-pr-record | 1619 |
| `feat/1643-skills-repo` | `aa82e25b177c` | 27 | ancestry | reachable-from-main | — |
| `feat/1549-session-parallel` | `07c68dc88ae8` | 27 | ancestry | reachable-from-main | — |
| `feat/1656-load-test` | `b61dddf0fc23` | 27 | ancestry | reachable-from-main | — |
| `feat/1657-fusion-fix` | `b61dddf0fc23` | 27 | ancestry | reachable-from-main | — |
| `feat/1660-onboarding-redesign` | `82a1ce28b641` | 27 | ancestry | reachable-from-main | — |
| `feat/1656-load-test-v2` | `22dccaa91fda` | 27 | pr-merged-tip | merged-pr-record | 1682 |
| `feat/1657-fusion-fix-v2` | `2ed1dd54bc88` | 27 | pr-merged-tip | merged-pr-record | 1683 |
| `feat/1657-fusion-on` | `e65b1d9f83d8` | 26 | pr-merged-tip | merged-pr-record | 1687 |
| `feat/1680-setup-back` | `67e6732c7000` | 26 | ancestry | reachable-from-main | — |
| `feat/1680-onboarding-polish` | `fa34f63e379c` | 26 | ancestry | reachable-from-main | — |
| `feat/1680-setup-back2` | `4d281a16ca7c` | 26 | ancestry | reachable-from-main | — |
| `fix/1689-setup-visible` | `86d04ae20c8e` | 26 | ancestry | reachable-from-main | — |
| `epic/test-db-migration` | `a78bcac00081` | 26 | ancestry | reachable-from-main | — |
| `fix/1691-onboarding-copy` | `292c22630399` | 26 | ancestry | reachable-from-main | — |
| `feat/1549-prompt-efficiency` | `cf42490ad0ef` | 26 | ancestry | reachable-from-main | — |
| `fix/1692-orient-order` | `938dd14896cc` | 26 | ancestry | reachable-from-main | — |
| `fix/canary-gh-token` | `ca0c2ee01bad` | 26 | ancestry | reachable-from-main | — |
| `fix/1694-web-steps` | `0d2a058a8952` | 26 | ancestry | reachable-from-main | — |
| `fix/1699-web-copy` | `9ebc30f92c5c` | 26 | ancestry | reachable-from-main | — |
| `fix/1701-consent-page` | `658aea18a433` | 26 | ancestry | reachable-from-main | — |
| `fix/1549-pilot-extractor-reasoning` | `02127dc65700` | 26 | ancestry | reachable-from-main | — |
| `fix/1704-cookie-session` | `2081be0aa93f` | 26 | ancestry | reachable-from-main | — |
| `feat/api-key-labels` | `91777e9ac755` | 26 | ancestry | reachable-from-main | — |
| `fix/1710-team-create-phantom-key` | `1bcf7be57799` | 26 | ancestry | reachable-from-main | — |
| `feat/1708-key-mint-idempotency` | `b5983bd4f5f8` | 26 | ancestry | reachable-from-main | — |
| `feat/1709-signup-idempotency-recovery` | `8eb31d44bef8` | 26 | ancestry | reachable-from-main | — |
| `feat/1714-memory-capture-onboarding` | `9e7c8c514090` | 26 | ancestry | reachable-from-main | — |
| `feat/1715-token-revoke` | `d622c8cc5e9f` | 26 | ancestry | reachable-from-main | — |
| `fix/1716-onboarding-orphan-key` | `1549456231d8` | 25 | ancestry | reachable-from-main | — |
| `fix/1721-asyncio-cascade` | `ba38fd8200c1` | 25 | ancestry | reachable-from-main | — |
| `feat/1698-welcome-e2e-email-visibility` | `2a421b633ea7` | 25 | ancestry | reachable-from-main | — |
| `fix/1749-recover-api-url` | `68ceb9cf044d` | 25 | ancestry | reachable-from-main | — |
| `fix/1753-1754-registry-parity` | `0cb000dbf423` | 25 | ancestry | reachable-from-main | — |
| `pr-1761` | `e4ec6e708ad9` | 25 | ancestry | reachable-from-main | — |
| `fix/1750-1751-signup-messaging` | `ee2b945a1272` | 25 | ancestry | reachable-from-main | — |
| `fix/1752-token-source-divergence` | `07319f621dc3` | 25 | ancestry | reachable-from-main | — |
| `feat/1748-onboarding-user-path` | `68762261dbd9` | 25 | ancestry | reachable-from-main | — |
| `feat/1763-answer-string-mark` | `5bda081e154b` | 25 | ancestry | reachable-from-main | — |
| `feat/1686-carveout-team-create-leak` | `dccc5584bb30` | 25 | ancestry | reachable-from-main | — |
| `fix/1755-token-revoke-confirm` | `3760bb8fa4ee` | 25 | ancestry | reachable-from-main | — |
| `fix/1756-recover-guidance` | `af2c62775d04` | 25 | ancestry | reachable-from-main | — |
| `feat/1685-ruff-baseline-drift` | `13612966b468` | 25 | ancestry | reachable-from-main | — |
| `fix/harness-copy-instructions` | `d99e21f9de1c` | 25 | ancestry | reachable-from-main | — |
| `fix/welcome-bridge` | `ebfcbf9ec3ad` | 25 | ancestry | reachable-from-main | — |
| `feat/1725-slice0` | `404f99bf1794` | 24 | ancestry | reachable-from-main | — |
| `docs/tortoise-blog-cms-planning` | `bdbca67f985e` | 24 | ancestry | reachable-from-main | — |
| `fix/1719-session-login-mint-guard` | `055a08bd8bca` | 24 | ancestry | reachable-from-main | — |
| `feat/1793-blog-data` | `da3109cbc75d` | 24 | ancestry | reachable-from-main | — |
| `feat/1795-agent-api` | `2caed1d37802` | 24 | ancestry | reachable-from-main | — |
| `feat/1794-blog-render` | `9bb06a8202bc` | 24 | ancestry | reachable-from-main | — |
| `feat/1795-blog-agent-api` | `b03ae1f1dab7` | 24 | ancestry | reachable-from-main | — |
| `feat/1796-blog-seo` | `4caadbd66aa2` | 24 | ancestry | reachable-from-main | — |
| `feat/1797-admin-gate` | `abc8b3ea2abe` | 24 | ancestry | reachable-from-main | — |
| `feat/1799-blog-events` | `86df5f8eef34` | 24 | ancestry | reachable-from-main | — |
| `feat/1798-admin-app` | `0d5c1dc67729` | 24 | ancestry | reachable-from-main | — |
| `fix/1738-uuid-burst-copy` | `3488adbc8ceb` | 24 | ancestry | reachable-from-main | — |
| `feat/1800-blog-deploy` | `e056e21e344b` | 24 | ancestry | reachable-from-main | — |
| `docs/blog-epic-status` | `7d6e60c128ec` | 24 | ancestry | reachable-from-main | — |
| `fix/1737-uniform-503` | `5b113d998ceb` | 24 | ancestry | reachable-from-main | — |
| `fix/1822-admin-gate-supabase-auth` | `7f5fd0ee55c6` | 24 | ancestry | reachable-from-main | — |
| `fix/1826-oauth-redirect` | `260dfd4f4b6e` | 24 | ancestry | reachable-from-main | — |
| `fix/1828-session-key-deadlock` | `359c9057a346` | 24 | ancestry | reachable-from-main | — |
| `feat/1726-docs` | `949a2b780073` | 24 | ancestry | reachable-from-main | — |
| `fix/1830-recovery-rotation` | `d8a84dd32edb` | 24 | ancestry | reachable-from-main | — |
| `fix/1832-session-failsoft` | `8d248b8a251a` | 24 | ancestry | reachable-from-main | — |
| `fix/1835-google-cookie` | `c6624be91d4f` | 23 | ancestry | reachable-from-main | — |
| `fix/1838-onboarding-race` | `31d1574c206b` | 23 | ancestry | reachable-from-main | — |
| `feat/1841-overview-skeleton` | `d755ce65925d` | 23 | ancestry | reachable-from-main | — |
| `fix/1847-memsources-refresh` | `c50ae9918ecd` | 23 | ancestry | reachable-from-main | — |
| `fix/1844-object-only` | `5f53f8cc00df` | 23 | ancestry | reachable-from-main | — |
| `fix/1834-import-columns` | `0392f9c3839e` | 23 | ancestry | reachable-from-main | — |
| `pr1868` | `d7e6e69d2936` | 23 | ancestry | reachable-from-main | — |
| `fix/1856-deadshell` | `e059c8038f27` | 23 | ancestry | reachable-from-main | — |
| `fix/1845-source-scope-multiselect` | `7d6352742eaa` | 23 | ancestry | reachable-from-main | — |
| `feat/1874-account-menu-restructure` | `af799110cc8a` | 23 | pr-merged-tip | merged-pr-record | 1887 |
| `fix/1781-lint-clean` | `79bcdf77a614` | 23 | ancestry | reachable-from-main | — |
| `fix/1892-ci` | `241260903436` | 23 | ancestry | reachable-from-main | — |
| `feat/1876-billing-team-dropdown` | `86a78a298fc0` | 23 | pr-merged-tip | merged-pr-record | 1899 |
| `fix/1880-ghost-members` | `54928a2b57a7` | 23 | pr-merged-tip | merged-pr-record | 1902 |
| `fix/1885-gate-test-bootstrap` | `fe02ba793bf6` | 23 | pr-merged-tip | merged-pr-record | 1939 |
| `feat/1877-create-team-entitlement` | `31b74d485062` | 23 | pr-merged-tip | merged-pr-record | 1952 |
| `feat/1875-invite-pending` | `d778a7d13a13` | 23 | pr-merged-tip | merged-pr-record | 1964 |
| `fix/1927-consent` | `7349274cbfea` | 22 | ancestry | reachable-from-main | — |
| `feat/1929-pack-shipping` | `408c1a49c123` | 22 | pr-merged-tip | merged-pr-record | 1953 |
| `fix/1941-proxy-text` | `36de6520ca20` | 22 | pr-merged-tip | merged-pr-record | 1967 |
| `fix/1908-ghost-expiry` | `2f677a4a3579` | 22 | pr-merged-tip | merged-pr-record | 1969 |
| `fix/1904-content-hash` | `3cc089a1d629` | 22 | ancestry | reachable-from-main | — |
| `fix/1965-invite-capacity-toctou` | `7fd4aa926d9f` | 22 | pr-merged-tip | merged-pr-record | 1978 |
| `fix/1940-tracing-drift` | `07baa77ee908` | 22 | pr-merged-tip | merged-pr-record | 1968 |
| `fix/1954-entitlement-toctou` | `30f3117117ac` | 22 | pr-merged-tip | merged-pr-record | 1979 |
| `fix/1912-teams-suspension` | `0c2391a32f68` | 22 | ancestry | reachable-from-main | — |
| `fix/1917-input-edge` | `9a0702bfc37d` | 22 | ancestry | reachable-from-main | — |
| `feat/1896-fly-orphan-machine-guard` | `a754bf284bb5` | 22 | ancestry | reachable-from-main | — |
| `fix/1918-subject-canonical-id` | `b7a79aa3c7c9` | 22 | ancestry | reachable-from-main | — |
| `feat/1970-main-hygiene` | `c841516f0c90` | 22 | pr-merged-tip | merged-pr-record | 1986 |
| `fix/1913-abuse-session-lane` | `6f0e4e4675ae` | 22 | ancestry | reachable-from-main | — |
| `fix/1906-welcome-dashboard` | `82e489bc29aa` | 22 | ancestry | reachable-from-main | — |
| `chore/1976-epic-planning` | `e503bb626fa8` | 22 | pr-merged-tip | merged-pr-record | 2012 |
| `feat/1930-packs-dir` | `a00f5acd038e` | 22 | pr-merged-tip | merged-pr-record | 2014 |
| `feat/1933-agent-ops-pack` | `bbbeaeb6f432` | 22 | pr-merged-tip | merged-pr-record | 2015 |
| `feat/1935-hosted-packs` | `ef706f75e433` | 21 | pr-merged-tip | merged-pr-record | 2017 |
| `fix/1914-pagination-params` | `12a0ff13b152` | 21 | ancestry | reachable-from-main | — |
| `feat/1932-pack-docs` | `b534e22821c4` | 21 | pr-merged-tip | merged-pr-record | 2019 |
| `feat/1936-pack-export` | `3bc74220864b` | 21 | pr-merged-tip | merged-pr-record | 2021 |
| `feat/1934-enforcement` | `32c830ee1402` | 21 | pr-merged-tip | merged-pr-record | 2022 |
| `feat/1895-repoll-cursor-advance` | `0dc1c1e15bff` | 21 | ancestry | reachable-from-main | — |
| `fix/1903-team-graph-name` | `b771b3bbf213` | 21 | ancestry | reachable-from-main | — |
| `feat/1970-ci-debug` | `0b78df1b56c3` | 21 | pr-merged-tip | merged-pr-record | 2026 |
| `review-bugs` | `9108a0096ead` | 21 | ancestry | reachable-from-main | — |
| `fix/1915-confidence-freshness` | `9005423f0dfa` | 21 | ancestry | reachable-from-main | — |
| `fix/1909-oauth-fragment` | `b8ce5e5eabef` | 21 | ancestry | reachable-from-main | — |
| `feat/2029-body-cap` | `5c8651a344e1` | 21 | pr-merged-tip | merged-pr-record | 2033 |
| `feat/2031-tenant-view` | `e476eeda7909` | 21 | pr-merged-tip | merged-pr-record | 2036 |
| `feat/2028-foreign-kinds` | `376f6e050a2a` | 21 | pr-merged-tip | merged-pr-record | 2041 |
| `feat/1893-source-scope-persist` | `9f886e3b779e` | 21 | ancestry | reachable-from-main | — |
| `feat/2030-namespaced-enforcement` | `1f7400cb7e2a` | 21 | pr-merged-tip | merged-pr-record | 2042 |
| `feat/2030b-pack-count-test` | `afb95481d5bb` | 21 | pr-merged-tip | merged-pr-record | 2043 |
| `feat/1894-docs-memory-source-switch` | `e96504978121` | 21 | ancestry | reachable-from-main | — |
| `pr-2013` | `4607fbb8d754` | 21 | ancestry | reachable-from-main | — |
| `feat/2039-backup-restore-guard` | `46fac62c76b4` | 21 | ancestry | reachable-from-main | — |
| `tmp/1922-cleanup` | `d21c89645867` | 21 | ancestry | reachable-from-main | — |
| `pr-2049` | `096c60512714` | 21 | ancestry | reachable-from-main | — |
| `fix/1928-hosted-e2e-consent` | `c215d0b66aea` | 21 | ancestry | reachable-from-main | — |
| `pr-2054` | `3a4c76e9b403` | 21 | ancestry | reachable-from-main | — |
| `feat/ai-review-gate` | `cc4944d39ef4` | 21 | ancestry | reachable-from-main | — |
| `feat/2005-W9-onboarding` | `e2959dbdcde1` | 21 | ancestry | reachable-from-main | — |
| `feat/2006-W11-onboarding` | `e2959dbdcde1` | 21 | ancestry | reachable-from-main | — |
| `feat/2007-W12-onboarding` | `e2959dbdcde1` | 21 | ancestry | reachable-from-main | — |
| `docs/ai-review-gate-e2e` | `d5401e50568d` | 21 | ancestry | reachable-from-main | — |
| `fix/1919-operator-dedup` | `283a6cee7cbc` | 21 | ancestry | reachable-from-main | — |
| `feat/2038-pack-rate-limit` | `2085c753ba0e` | 21 | ancestry | reachable-from-main | — |
| `feat/2040-ledger-order` | `841f2e1d5690` | 21 | ancestry | reachable-from-main | — |
| `feat/2032-body-sweep` | `0c88b73b6080` | 21 | ancestry | reachable-from-main | — |
| `docs/500q-strong-reader-config` | `eda20ecf262a` | 21 | ancestry | reachable-from-main | — |
| `fix/2052-redislite-orphan-sweep` | `142c65f28da8` | 21 | ancestry | reachable-from-main | — |
| `fix/2065-flaky-pack-upload` | `cbb1e783959d` | 20 | ancestry | reachable-from-main | — |
| `fix/2061-event-journaling` | `5667e5d96b04` | 20 | ancestry | reachable-from-main | — |
| `feat/2001-W5-onboarding` | `0f2365a79ea9` | 20 | ancestry | reachable-from-main | — |
| `fix/1901-vacuity` | `b14898083421` | 19 | ancestry | reachable-from-main | — |
| `feat/2193-hosted-supersession-migration` | `684687467043` | 19 | ancestry | reachable-from-main | — |
| `feat/2052-reaper-sweep` | `8596791661ef` | 19 | ancestry | reachable-from-main | — |
| `feat/2069-reader-routing` | `0dde912a9ed6` | 19 | ancestry | reachable-from-main | — |
| `fix/2071-spotcheck-judge` | `8cb4b8762704` | 19 | ancestry | reachable-from-main | — |
| `fix/407-skill-links` | `458ef8c45546` | 19 | ancestry | reachable-from-main | — |
| `fix/2062-ingest-event` | `47662051eb6c` | 19 | ancestry | reachable-from-main | — |
| `fix/1900-dataset-join` | `55ba3a415f9a` | 19 | ancestry | reachable-from-main | — |
| `fix/2070-ask-retrieval` | `bdef11902409` | 19 | ancestry | reachable-from-main | — |
| `feat/2080-gbrain-scope` | `bf01b77377f9` | 19 | pr-merged-tip | merged-pr-record | 2086 |
| `feat/2080-gbrain-plan` | `f2e8d95455f0` | 19 | pr-merged-tip | merged-pr-record | 2095 |
| `feat/2080-gbrain-verify` | `2cff245f4551` | 19 | pr-merged-tip | merged-pr-record | 2109 |
| `fix/2085-decide-legacy-skip` | `1a7795a47161` | 19 | ancestry | reachable-from-main | — |
| `fix/2084-uri-restore-guard` | `c422cd37e710` | 19 | ancestry | reachable-from-main | — |
| `feat/2110-c1-dual-mode-graph-key-model` | `9911b0915f0b` | 19 | pr-merged-tip | merged-pr-record | 2130 |
| `feat/2080-w1-learnings-map` | `a28fa8114f4e` | 18 | ancestry | reachable-from-main | — |
| `feat/1997-W1-onboarding` | `4cb7e6711ebe` | 18 | ancestry | reachable-from-main | — |
| `fix/2090-ci-lane-pollution` | `96e435213cf8` | 18 | ancestry | reachable-from-main | — |
| `feat/2000-W4-onboarding` | `06e34c0cd5db` | 18 | ancestry | reachable-from-main | — |
| `feat/2127-shared-fixture-helper` | `0d1565cdd029` | 18 | ancestry | reachable-from-main | — |
| `feat/2080-w4a-why-enrichment` | `19c87ae05826` | 18 | ancestry | reachable-from-main | — |
| `fix/2151-mcp-lazy` | `11ad1baefa5c` | 18 | pr-merged-tip | merged-pr-record | 2152 |
| `fix/2138-ruf100-cleanup` | `d5210c22e731` | 18 | ancestry | reachable-from-main | — |
| `feat/2127-wave1b-migration` | `65bb7f186f91` | 17 | ancestry | reachable-from-main | — |
| `fix/2147-2148-diff-gate-pythonci` | `799a4d1c2194` | 17 | ancestry | reachable-from-main | — |
| `fix/2149-ciyml-path-gates` | `4254448279d3` | 17 | ancestry | reachable-from-main | — |
| `ops/2146-orphan-cleanup` | `5b7a6aba8905` | 17 | ancestry | reachable-from-main | — |
| `feat/2127-wave2-migration` | `560ba26956fe` | 17 | ancestry | reachable-from-main | — |
| `feat/2080-w2a-planted-gold` | `c255926d3292` | 17 | ancestry | reachable-from-main | — |
| `feat/1998-W2-onboarding` | `9c63a9bf2989` | 17 | ancestry | reachable-from-main | — |
| `fix/2155-w2a-lint-cleanup` | `78b2f820d088` | 17 | ancestry | reachable-from-main | — |
| `feat/2127-wave3-tripwire` | `29826e4a255b` | 17 | ancestry | reachable-from-main | — |
| `feat/2111-c2-provisioning-service` | `2e2c7275cb7f` | 17 | pr-merged-tip | merged-pr-record | 2145 |
| `feat/2004-W8-onboarding` | `9c34f2cd32ee` | 17 | ancestry | reachable-from-main | — |
| `chore/agent-infra-v0.1.2` | `5fb3b8d8ccdf` | 17 | ancestry | reachable-from-main | — |
| `ops/2146-graph-drop` | `5d1a80c115a2` | 17 | ancestry | reachable-from-main | — |
| `pr2181` | `5d1a80c115a2` | 17 | ancestry | reachable-from-main | — |
| `feat/2002-W6-onboarding` | `4947b188eaf2` | 17 | pr-merged-tip | merged-pr-record | 2180 |
| `fix/2163-graph-drop` | `55639ae488c1` | 17 | ancestry | reachable-from-main | — |
| `fix/2172-anchor-race` | `3abe56e62dde` | 17 | ancestry | reachable-from-main | — |
| `fix/2173-collect-strays` | `bf46d2bb45b4` | 17 | ancestry | reachable-from-main | — |
| `fix/2174-postmerge-lint` | `df1e6d561390` | 17 | ancestry | reachable-from-main | — |
| `chore/419-exa-lazy` | `d285b117b6bb` | 17 | ancestry | reachable-from-main | — |
| `fix/2189-reconcile` | `2391b02aa2cd` | 17 | ancestry | reachable-from-main | — |
| `feat/2178-keys-table-e2e` | `c71f4380af82` | 17 | pr-merged-tip | merged-pr-record | 2213 |
| `feat/2112-c3-key-lifecycle` | `9030ca848f9f` | 17 | pr-merged-tip | merged-pr-record | 2196 |
| `feat/2080-w2b-benchmark-runner` | `cf4b964a500b` | 16 | ancestry | reachable-from-main | — |
| `hotfix/lint-eval-write-path` | `2f5979fcd9d6` | 16 | ancestry | reachable-from-main | — |
| `fix/1998-connect-durable-key` | `82c659205760` | 16 | pr-merged-tip | merged-pr-record | 2211 |
| `chore/2174-lint-drift` | `f362a07129a1` | 16 | pr-merged-tip | merged-pr-record | 2222 |
| `feat/2113-c4-acl-layer` | `182baf95d6fa` | 16 | pr-merged-tip | merged-pr-record | 2220 |
| `feat/2167-browser-session-auth` | `4f602c49dcdf` | 16 | pr-merged-tip | merged-pr-record | 2232 |
| `feat/2080-w3a-cat34-harness` | `5cb319866e66` | 16 | ancestry | reachable-from-main | — |
| `feat/2164-capture-supersession-fold` | `b26c07a9b174` | 16 | ancestry | reachable-from-main | — |
| `feat/2229-rotate-held-key` | `996780887442` | 16 | pr-merged-tip | merged-pr-record | 2234 |
| `fix/2235-dedup-syntax` | `81d580886242` | 16 | ancestry | reachable-from-main | — |
| `fix/2208-version-endpoint` | `2314b417219e` | 16 | pr-merged-tip | merged-pr-record | 2239 |
| `feat/1999-W3-onboarding` | `58e840e5219a` | 16 | pr-merged-tip | merged-pr-record | 2156 |
| `feat/2080-w4b-contested-score` | `8194cb4f523c` | 16 | ancestry | reachable-from-main | — |
| `fix/2188-d14-gate` | `0e1e495632e8` | 16 | ancestry | reachable-from-main | — |
| `feat/2080-w5-ingestion-quality` | `afa086183975` | 16 | ancestry | reachable-from-main | — |
| `feat/2080-w7b-comparison-docs` | `931c5c822b78` | 16 | ancestry | reachable-from-main | — |
| `feat/2114-c5-tenancy-spine` | `adcb54317ec7` | 16 | pr-merged-tip | merged-pr-record | 2241 |
| `tmp/2252-d24probe` | `8ec4afc283bf` | 16 | ancestry | reachable-from-main | — |
| `tmp/2252-mainprobe` | `8ec4afc283bf` | 16 | ancestry | reachable-from-main | — |
| `fix/2260-legacy-scope` | `6e320eed8297` | 16 | pr-merged-tip | merged-pr-record | 2261 |
| `fix/2260-scope-followup` | `c2d16022ef29` | 16 | pr-merged-tip | merged-pr-record | 2264 |
| `fix/2104-audit-headers` | `3135806e8e2c` | 16 | pr-merged-tip | merged-pr-record | 2265 |
| `2179-keepalive-siblings` | `ac24f6b68cf5` | 16 | ancestry | reachable-from-main | — |
| `fix/2260-scope-extraction` | `ac721bd984d5` | 16 | pr-merged-tip | merged-pr-record | 2267 |
| `fix/2260-extraction-clean` | `bf6eb9db874c` | 16 | ancestry | reachable-from-main | — |
| `tmp/2269-clean` | `bf6eb9db874c` | 16 | ancestry | reachable-from-main | — |
| `feat/2115-c6-delivery-shape-tenancy` | `2605983f979f` | 16 | pr-merged-tip | merged-pr-record | 2266 |
| `chore/restore-1987-ask-gates` | `3f33b7da25de` | 16 | ancestry | reachable-from-main | — |
| `tmp/maincheck` | `64fb2e67786e` | 16 | ancestry | reachable-from-main | — |
| `feat/2116-c7-dashboard-graphs` | `61f1d2e423bb` | 16 | pr-merged-tip | merged-pr-record | 2274 |
| `feat/2080-w5c-ep-ingest` | `6c32878a447f` | 16 | ancestry | reachable-from-main | — |
| `feat/2080-w3b-why-suite` | `e3e15bc3053d` | 16 | ancestry | reachable-from-main | — |
| `feat/2117-c8-migration-docs` | `04305e8926d3` | 16 | pr-merged-tip | merged-pr-record | 2277 |
| `feat/2083-multi-graph` | `deaf4835886d` | 16 | pr-merged-tip | merged-pr-record | 2088 |
| `feat/2080-w4c-volunteer-context` | `40fcb34d4dd5` | 16 | ancestry | reachable-from-main | — |
| `feat/2118-capstone` | `107474199db9` | 16 | pr-merged-tip | merged-pr-record | 2279 |
| `feat/2080-w5-phase-d` | `6c7f4fe6748c` | 15 | ancestry | reachable-from-main | — |
| `fix/2179-keepalive-siblings` | `1955c740d228` | 15 | ancestry | reachable-from-main | — |
| `feat/2080-w5-phase-e` | `900022383ec3` | 15 | ancestry | reachable-from-main | — |
| `fix/2287-ci-volunteer-guards` | `cf546dfac5b9` | 15 | ancestry | reachable-from-main | — |
| `feat/2193-supersession-migration` | `2357b7d29458` | 15 | ancestry | reachable-from-main | — |
| `fix/2280-ask-reader-collapse` | `35676f9efdce` | 15 | pr-merged-tip | merged-pr-record | 2285 |
| `fix/2251-embedded-path-divergence` | `56555b6d1169` | 15 | ancestry | reachable-from-main | — |
| `fix/2134-reader-none-guard` | `e7395f6ff63c` | 15 | pr-merged-tip | merged-pr-record | 2136 |
| `fix/2210-cli-mcp-polish` | `ebf136a6b90d` | 15 | pr-merged-tip | merged-pr-record | 2219 |
| `fix/2204-doctor-preinit-noise` | `28a29d731537` | 15 | pr-merged-tip | merged-pr-record | 2218 |
| `fix/2203-orphan-redis-sigint` | `d6b54ea4000f` | 15 | pr-merged-tip | merged-pr-record | 2228 |
| `fix/2218-guard-os-import` | `4fd1cb2922d0` | 15 | ancestry | reachable-from-main | — |
| `feat/2080-w5-phase-f` | `174ad63074f8` | 15 | ancestry | reachable-from-main | — |
| `fix/2200-selfhost-docker` | `02be6ec02cc2` | 15 | pr-merged-tip | merged-pr-record | 2272 |
| `fix/2199-decide-calibration` | `10368f132eaa` | 15 | pr-merged-tip | merged-pr-record | 2262 |
| `fix/2205-summarize-counts` | `ad76da2fc561` | 15 | pr-merged-tip | merged-pr-record | 2226 |
| `fix/2202-health-truthful` | `ebd1caebe931` | 15 | pr-merged-tip | merged-pr-record | 2227 |
| `fix/2206-confidence-surfaces` | `65e6d0767f15` | 15 | pr-merged-tip | merged-pr-record | 2286 |
| `fix/2207-extraction-digest` | `0102da8a8b15` | 15 | pr-merged-tip | merged-pr-record | 2225 |
| `fix/2201-indexer-unreadable` | `a331daa14e4f` | 15 | pr-merged-tip | merged-pr-record | 2215 |
| `feat/2194-journal-objectregistered` | `6b0d12fc7092` | 15 | ancestry | reachable-from-main | — |
| `feat/2080-seams-wave1` | `f22097920de9` | 15 | ancestry | reachable-from-main | — |
| `fix/2339-transport-stall` | `54a1c6f206e0` | 15 | ancestry | reachable-from-main | — |
| `feat/2313-per-graph-backups` | `c60e1609eccf` | 14 | ancestry | reachable-from-main | — |
| `pr-2366` | `36a7ca6b6655` | 14 | ancestry | reachable-from-main | — |
| `feat/2284-battery-measurement-path` | `16c290a04732` | 14 | ancestry | reachable-from-main | — |
| `fix/2367-watcher-state-teams` | `c5b6630ec57e` | 14 | ancestry | reachable-from-main | — |
| `fix/2374-pergraph-taxonomy` | `e2ba13b3c7dd` | 14 | ancestry | reachable-from-main | — |
| `fix/2371-acl-reconcile-to-thread` | `adce257966f6` | 14 | ancestry | reachable-from-main | — |
| `fix/2377-rebaseline-validate` | `7972a6479996` | 14 | ancestry | reachable-from-main | — |
| `fix/2376-create-graph-kind` | `e617fe707051` | 14 | ancestry | reachable-from-main | — |
| `fix/2372-sweep-rollup` | `67cecad9ee65` | 14 | ancestry | reachable-from-main | — |
| `fix/2373-retention-day-anchors` | `cfed5dacc8ef` | 14 | ancestry | reachable-from-main | — |
| `fix/2375-driver-subject-scope` | `4856746413cc` | 14 | ancestry | reachable-from-main | — |
| `fix/2378-docs-residuals` | `030bde286269` | 14 | ancestry | reachable-from-main | — |
| `fix/2370-legacy-flat-index` | `1abd91858798` | 14 | ancestry | reachable-from-main | — |
| `feat/2295-subjectadded-journaling` | `362022cb458a` | 14 | ancestry | reachable-from-main | — |
| `fix/2411-selfheal` | `8cd3318a788d` | 14 | ancestry | reachable-from-main | — |
| `fix/2412-opstate` | `85c2d9166c62` | 14 | ancestry | reachable-from-main | — |
| `fix/2414-keyderived` | `a41fe5e69b0e` | 14 | ancestry | reachable-from-main | — |
| `fix/2415-residual` | `56b9813779ea` | 14 | ancestry | reachable-from-main | — |
| `fix/2413-subjmatch` | `a20c545d7591` | 14 | ancestry | reachable-from-main | — |
| `fix/confidence-graphranker-prior-coalesce` | `a6ad6fd9671d` | 14 | pr-merged-tip | merged-pr-record | 2433 |
| `opt/2080-retention` | `656935c2654f` | 14 | ancestry | reachable-from-main | — |
| `fix/digest-noise-preserve-decision-shapes` | `79117bd06794` | 14 | pr-merged-tip | merged-pr-record | 2434 |
| `feat/2380-session-key-recovery` | `c200cf3a3daa` | 14 | ancestry | reachable-from-main | — |
| `feat/2408-s4-reemit-census` | `8b170976835c` | 14 | ancestry | reachable-from-main | — |
| `fix/2209-session-hook-notice` | `c2b74c1d857c` | 14 | pr-merged-tip | merged-pr-record | 2223 |
| `feat/2437-contribution-policy` | `a87fadc62703` | 14 | ancestry | reachable-from-main | — |
| `fix/2426-wizard-tdz` | `92a4c265b864` | 14 | ancestry | reachable-from-main | — |
| `feat/2426-key-expiry` | `1fbdbed97cde` | 14 | ancestry | reachable-from-main | — |
| `opt/2080-qa-loop` | `84d35029fada` | 13 | ancestry | reachable-from-main | — |
| `fix/indexer-exit-code-mixed-failure` | `f20d8fcb9473` | 13 | pr-merged-tip | merged-pr-record | 2435 |
| `docs/batch-bug-hunt-report` | `f36b337da4a1` | 13 | pr-merged-tip | merged-pr-record | 2457 |
| `feat/2304-delete-trash` | `76ddfdba334e` | 13 | ancestry | reachable-from-main | — |
| `docs/scoping-2304` | `c992583b3f05` | 13 | ancestry | reachable-from-main | — |
| `fix/2422-ep-terminal-ghost` | `764325302cb2` | 13 | pr-merged-tip | merged-pr-record | 2432 |
| `opt/2080-d2-failover` | `f7455cc59b5d` | 13 | ancestry | reachable-from-main | — |
| `opt/2080-trust-posture` | `a6250ee70532` | 13 | ancestry | reachable-from-main | — |
| `opt/2080-ex-quality` | `49e21c7e0a0a` | 13 | ancestry | reachable-from-main | — |
| `feat/2479-re-auth-ux-investigation` | `ce6ccae83ae7` | 13 | ancestry | reachable-from-main | — |
| `docs/2421-supersede-restatement` | `9a0a829ee256` | 13 | pr-merged-tip | merged-pr-record | 2492 |
| `fix/2463-restore0row` | `66a6a3252e67` | 13 | ancestry | reachable-from-main | — |
| `seo-fixes` | `f1705b4147cb` | 13 | pr-merged-tip | merged-pr-record | 2497 |
| `feat/2439-inbound-intake-store` | `5f3be4e1334b` | 13 | ancestry | reachable-from-main | — |
| `seo-docs-link` | `ac3dbb336c21` | 13 | pr-merged-tip | merged-pr-record | 2499 |
| `fix/2462-p1-flatpurge` | `dffd2862229f` | 13 | ancestry | reachable-from-main | — |
| `fix/2466-indexrmw` | `5a86689f5f17` | 13 | ancestry | reachable-from-main | — |
| `feat/2494-account-menu-sections` | `1c7e8dda0a8b` | 13 | pr-merged-tip | merged-pr-record | 2502 |
| `fix/2494-account-menu-section-order` | `0f0ce32d75e1` | 13 | pr-merged-tip | merged-pr-record | 2505 |
| `fix/2494-logout-in-personal-section` | `4d41489f60ff` | 13 | pr-merged-tip | merged-pr-record | 2508 |
| `fix/2464-stampcond` | `9433af10a28a` | 13 | ancestry | reachable-from-main | — |
| `fix/2494-overview-layout` | `958d3a38b001` | 13 | ancestry | reachable-from-main | — |
| `fix/2467-quota` | `eaaec6e44957` | 13 | ancestry | reachable-from-main | — |
| `feat/2406-signup-onboarding-email` | `e8ebe22a5b42` | 13 | pr-merged-tip | merged-pr-record | 2459 |
| `fix/2471-ghoststreak` | `e7d2d5e8d6e8` | 13 | ancestry | reachable-from-main | — |
| `feat/2479-re-auth-fix` | `dcc4c0d36ee4` | 13 | pr-merged-tip | merged-pr-record | 2527 |
| `merge-tmp` | `3fe0f45e3737` | 13 | ancestry | reachable-from-main | — |
| `2529-oauth-fragment-fix` | `be3b4e0ee116` | 13 | pr-merged-tip | merged-pr-record | 2531 |
| `fix/2469-inspect` | `e446467f8658` | 13 | ancestry | reachable-from-main | — |
| `feat/2249-supersession-order` | `b377dda11e3b` | 13 | ancestry | reachable-from-main | — |
| `opt/2424-clause-fix` | `59aac0401c87` | 13 | ancestry | reachable-from-main | — |
| `fix/2470-locktimeout` | `2b93542f18b2` | 13 | ancestry | reachable-from-main | — |
| `2529-onboarding-invite-flow` | `d87f6893c926` | 13 | pr-merged-tip | merged-pr-record | 2545 |
| `opt/2315-mitigation` | `a4daec65d938` | 13 | ancestry | reachable-from-main | — |
| `fix/org-create-copy-and-name-validation` | `356d74c82c60` | 13 | pr-merged-tip | merged-pr-record | 2547 |
| `fix/org-create-spacing-and-edge-function` | `28edcc183c31` | 13 | pr-merged-tip | merged-pr-record | 2551 |
| `fix/fork-card-step-index` | `da3808ec7d83` | 12 | pr-merged-tip | merged-pr-record | 2553 |
| `fix/connect-step-clean-layout` | `aff5a35ac1aa` | 12 | ancestry | reachable-from-main | — |
| `feat/2242-cas-fold` | `884405328e57` | 12 | ancestry | reachable-from-main | — |
| `feat/2291-a4-ep-semantics` | `abb9644ad8fb` | 12 | ancestry | reachable-from-main | — |
| `fix/2465-graceorigin` | `7d6132555128` | 12 | ancestry | reachable-from-main | — |
| `fix/2468-namerace` | `61f6d52a1451` | 12 | ancestry | reachable-from-main | — |
| `fix/2392-dialog-focus-a11y` | `a9d2c7774a26` | 12 | pr-merged-tip | merged-pr-record | 2533 |
| `opt/2552-operators` | `4796ad47b4eb` | 12 | ancestry | reachable-from-main | — |
| `opt/2518-entity-keys` | `621fa6403891` | 12 | ancestry | reachable-from-main | — |
| `fix/connect-step-fork-aware-layout` | `09057abede5d` | 12 | pr-merged-tip | merged-pr-record | 2572 |
| `feat/2292-rubric-model-budget` | `1bc31d3101b9` | 12 | pr-merged-tip | merged-pr-record | 2575 |
| `fix/2559-reaud` | `97fcac586be6` | 12 | ancestry | reachable-from-main | — |
| `fix/2561-reaud` | `38c0b19efe88` | 12 | ancestry | reachable-from-main | — |
| `fix/2562-reaud` | `625b1d0c0515` | 12 | ancestry | reachable-from-main | — |
| `fix/2563-reaud` | `4f4330e13c27` | 12 | ancestry | reachable-from-main | — |
| `fix/2564-reaud` | `23391d13def4` | 12 | ancestry | reachable-from-main | — |
| `fix/2566-reaud` | `8c2102ff00ab` | 12 | ancestry | reachable-from-main | — |
| `fix/2560-reaud` | `dfac9c3a997b` | 12 | ancestry | reachable-from-main | — |
| `fix/2565-reaud` | `e1fbf8a40065` | 12 | ancestry | reachable-from-main | — |
| `fix/raud2558` | `5176b81ecfb8` | 12 | ancestry | reachable-from-main | — |
| `fix/2601-judge-pair-distinct` | `b7c186ed28e8` | 12 | pr-merged-tip | merged-pr-record | 2624 |
| `feat/2523-diff-profile-contract` | `05388c91c03d` | 12 | ancestry | reachable-from-main | — |
| `feat/2284-executor-exposure` | `1db99413c807` | 12 | ancestry | reachable-from-main | — |
| `fix/2633-real-vendor-key-preflight` | `342cda7645b5` | 12 | ancestry | reachable-from-main | — |
| `feat/1416-cli-executor-real` | `69188dab99a2` | 12 | ancestry | reachable-from-main | — |
| `fix/1416-real-episode-deadline` | `31e2fd3825ee` | 12 | ancestry | reachable-from-main | — |
| `feat/add-email-ui-fixes` | `29e1bb7b7685` | 12 | pr-merged-tip | merged-pr-record | 2645 |
| `fix/2644-l4-flake` | `3bf46ef38c93` | 12 | ancestry | reachable-from-main | — |
| `feat/identity-email-column` | `6d51e82aa40d` | 12 | pr-merged-tip | merged-pr-record | 2662 |
| `feat/identity-better-fallback` | `a58ffb3e389f` | 12 | pr-merged-tip | merged-pr-record | 2663 |
| `pr-2664` | `76dd3039fda6` | 12 | ancestry | reachable-from-main | — |
| `pr2664` | `76dd3039fda6` | 12 | ancestry | reachable-from-main | — |
| `fix/1416-envelope-typeerror` | `a801cc51d944` | 11 | ancestry | reachable-from-main | — |
| `feat/clean-profile-tab` | `87d8b86e82a4` | 11 | pr-merged-tip | merged-pr-record | 2666 |
| `feat/api-keys-ux-fixes` | `e2234e9d1277` | 11 | pr-merged-tip | merged-pr-record | 2667 |
| `feat/dashboard-login-default-off` | `1c05905b2c60` | 11 | pr-merged-tip | merged-pr-record | 2670 |
| `feat/2688-dashboard-deploy-verification` | `1354a493074c` | 11 | pr-merged-tip | merged-pr-record | 2692 |
| `feat/key-modal-expiry-label` | `2840d48d6f07` | 11 | pr-merged-tip | merged-pr-record | 2694 |
| `fix/1416-envelope-extract` | `e237a47d2ae7` | 11 | ancestry | reachable-from-main | — |
| `feat/1416-run-main` | `1cefbf2ce2f1` | 11 | ancestry | reachable-from-main | — |
| `feat/1416-run-v2` | `be07ce3382b4` | 11 | ancestry | reachable-from-main | — |
| `feat/1416-run-v3` | `3b6ee571add1` | 11 | ancestry | reachable-from-main | — |
| `fix/1416-episode-deadline` | `a0b09cfadb6d` | 10 | ancestry | reachable-from-main | — |
| `feat/1416-run-v4` | `49314307046f` | 10 | ancestry | reachable-from-main | — |
| `tmp/main-gate-check` | `4270f17159a9` | 10 | ancestry | reachable-from-main | — |
| `docs/1416-verdict-report` | `9ed905892419` | 10 | ancestry | reachable-from-main | — |
| `fix/2712-pin-preflight-test` | `c357886314f7` | 10 | ancestry | reachable-from-main | — |
| `fix/2735-e2e-residuals` | `d93d6e49ab2a` | 10 | ancestry | reachable-from-main | — |
| `feat/2740-r1-derive` | `5d6baea425ac` | 10 | ancestry | reachable-from-main | — |
| `fix/2764-lint-i001` | `690ace738294` | 10 | ancestry | reachable-from-main | — |
| `chore/2238-ignore-agent-evidence-dirs` | `d37d5d053a13` | 10 | ancestry | reachable-from-main | — |
| `fix/2773-props-coercion` | `a71dc4756a5a` | 10 | ancestry | reachable-from-main | — |
| `fix/2759-authored-turn-refused` | `68791f365a5b` | 10 | ancestry | reachable-from-main | — |
| `docs/standard-harness-reuse-audit` | `8618ffa85e80` | 10 | ancestry | reachable-from-main | — |
| `fix/2450-judge-nonstring-gold` | `d25f1c63fb92` | 10 | ancestry | reachable-from-main | — |
| `docs/2784-per-graph-backups-brief` | `ed27f553c8ec` | 10 | ancestry | reachable-from-main | — |
| `fix/2797-parity-not-measured` | `17f0e2c3313e` | 10 | ancestry | reachable-from-main | — |
| `fix/2802-conflict-markers` | `9d08bbbbbf74` | 10 | ancestry | reachable-from-main | — |
| `feat/2800-parity-executors` | `183aeb8e78f8` | 10 | ancestry | reachable-from-main | — |
| `fix/2827-wizard-connect-polish` | `b3f083631bae` | 10 | pr-merged-tip | merged-pr-record | 2832 |
| `feat/2800-mabench-data-metric` | `26065873fcee` | 10 | ancestry | reachable-from-main | — |
| `fix/2824-embed-state-tests` | `060176a30ee7` | 10 | ancestry | reachable-from-main | — |
| `fix/ci-manifest-drift-2800` | `eb578392a55f` | 10 | ancestry | reachable-from-main | — |
| `feat/2800-mabench-cr-runloop` | `b6bd51a96b18` | 10 | ancestry | reachable-from-main | — |
| `fix/2874-price-basis` | `a230291db175` | 10 | pr-merged-tip | merged-pr-record | 2890 |
| `fix/2216-bgsave-fork-slot` | `0f97fb667f64` | 10 | ancestry | reachable-from-main | — |
| `fix/2906-spend-note` | `cea2783d03f4` | 10 | pr-merged-tip | merged-pr-record | 2908 |
| `docs/2854-scope-semantics` | `0df8625ef18f` | 10 | ancestry | reachable-from-main | — |
| `fix/ci-manifest-drift-price-basis` | `41e7670ee046` | 10 | ancestry | reachable-from-main | — |
| `fix/2906-provider-cost` | `8569204692e0` | 10 | pr-merged-tip | merged-pr-record | 2915 |
| `fix/2656-drift-gate-decouple` | `e2e468db1e0f` | 9 | pr-merged-tip | merged-pr-record | 2932 |
| `finish/2796-killswitch-loud` | `ceec08ad613f` | 9 | ancestry | reachable-from-main | — |
| `fix/2916-battery-surface` | `f5821f44a9e3` | 9 | pr-merged-tip | merged-pr-record | 2934 |
| `feat/2800-cr-tortoise-lane` | `4193388e5b46` | 9 | pr-merged-tip | merged-pr-record | 2945 |
| `fix/ci-empty-uri-falkordb-probe` | `c7ab0661e134` | 9 | pr-merged-tip | merged-pr-record | 2930 |
| `fix/2391-team-to-org-sweep` | `31f114ae9516` | 9 | pr-merged-tip | merged-pr-record | 2460 |
| `fix/2364-resume-round1` | `095d2387f648` | 9 | pr-merged-tip | merged-pr-record | 2487 |
| `feat/2407-fork-unsure-option` | `2f89314781e3` | 9 | pr-merged-tip | merged-pr-record | 2493 |
| `feat/2360-real-starter-data` | `0355b79fa76f` | 9 | pr-merged-tip | merged-pr-record | 2461 |
| `fix/2947-orphan-investigation` | `e8ed2460f2ed` | 9 | ancestry | reachable-from-main | — |
| `fix/2985-degrade-gate` | `e8ed2460f2ed` | 9 | ancestry | reachable-from-main | — |
| `fix/2952-time-dependent-ranking` | `29eb3ad27824` | 9 | ancestry | reachable-from-main | — |
| `fix/2361-vocab-anchor` | `aef112674d1f` | 9 | pr-merged-tip | merged-pr-record | 2994 |
| `docs/2779-org-name-scoping` | `48a22bada527` | 9 | ancestry | reachable-from-main | — |
| `fix/2947-embedded-ingest-cache` | `18b521002a04` | 9 | ancestry | reachable-from-main | — |
| `fix/2913-register-insert` | `9d05b01f8f18` | 9 | ancestry | reachable-from-main | — |
| `fix/2823-sweep-no-teams` | `f70bd439b19b` | 9 | ancestry | reachable-from-main | — |
| `chore/2938-surface-audit` | `2d084e7a42f8` | 9 | ancestry | reachable-from-main | — |
| `feat/2779-opaque-id-display-name` | `40a606c8f2bd` | 9 | ancestry | reachable-from-main | — |
| `chore/2938-audit-fixes` | `4f88d91f61a3` | 9 | ancestry | reachable-from-main | — |
| `fix/2919-parity-detail` | `1d3c3f4887a1` | 9 | pr-merged-tip | merged-pr-record | 3099 |
| `chore/2938-audit-fixes2` | `15642fcabf1b` | 9 | ancestry | reachable-from-main | — |
| `fix/3074-flaky-pointsmerged` | `6b23a798bab3` | 8 | pr-merged-tip | merged-pr-record | 3149 |
| `fix/3076-review-gate-diagnosis` | `2bd1831cb24f` | 8 | ancestry | reachable-from-main | — |
| `fix/2938-curate-surfaces` | `cd06d652a4c0` | 8 | ancestry | reachable-from-main | — |
| `fix/3221-manifest-drift-both-files` | `d3ac78685a4d` | 8 | ancestry | reachable-from-main | — |
| `fix/3218-onboarding-wizard-copy` | `16cb039a2a23` | 8 | ancestry | reachable-from-main | — |
| `chore/2525-matched-recall-reconcile` | `1117aaa46c6b` | 8 | ancestry | reachable-from-main | — |
| `research/matched-recall-controls` | `969381038947` | 8 | pr-merged-tip | merged-pr-record | 3337 |
| `docs/3327-record-trigger-population` | `3f5c449bc57f` | 8 | pr-merged-tip | merged-pr-record | 3342 |
| `docs/3327-sweep-trigger-population` | `7c3d28447210` | 8 | pr-merged-tip | merged-pr-record | 3345 |
| `feat/2784-graphs-last-backup` | `a587badc8fd6` | 8 | ancestry | reachable-from-main | — |
| `fix/3325-keyword-false-positive` | `7ac54318fcc7` | 8 | pr-merged-tip | merged-pr-record | 3378 |
| `fix/3261-3381-selection-manifest` | `6e5cafcc9b78` | 7 | pr-merged-tip | merged-pr-record | 3399 |
| `verify/2833-e2e-connect` | `e45bfb29cd7c` | 4 | ancestry | reachable-from-main | — |
| `fix/3599-embedded-orphan-leak` | `8e43a5b2aa59` | 4 | ancestry | reachable-from-main | — |
| `pr3780` | `fcd01388e7ac` | 3 | ancestry | reachable-from-main | — |
| `fix/3910-ask-spotcheck-capture-shape` | `3341d9a7cbdb` | 3 | pr-merged-tip | merged-pr-record | 3911 |
| `fix/3795-upgrade-path` | `8a2074578b8c` | 2 | pr-merged-tip | merged-pr-record | 3866 |
| `fix/3863-mcp-sdk-surface-curation` | `eac93755771f` | 2 | pr-merged-tip | merged-pr-record | 3966 |
| `feat/3806-ship-test-instrument` | `c25999714b35` | 2 | pr-merged-tip | merged-pr-record | 4004 |
| `fix/3503-session-fragment` | `fbab303fb230` | 2 | pr-merged-tip | merged-pr-record | 3640 |
| `fix/3628-auth-watchdog` | `170367017c22` | 2 | pr-merged-tip | merged-pr-record | 3648 |
| `fix/3620-upload-root` | `d72a70001859` | 2 | pr-merged-tip | merged-pr-record | 3652 |
| `perf/4068-reaper-census` | `db570edb4a70` | 1 | pr-merged-tip | merged-pr-record | 4099 |
| `fix/3474-api-domain` | `c7a669761a2a` | 1 | pr-merged-tip | merged-pr-record | 4112 |
| `fix/4113-capability-not-name-guards` | `007a9b3a02e9` | 1 | pr-merged-tip | merged-pr-record | 4142 |
| `fix/3436-signup-duplicate-id` | `a94b272a7713` | 1 | ancestry | reachable-from-main | — |
| `fix/4163-stale-carveout-counts` | `0f9dcf8fc5c5` | 1 | pr-merged-tip | merged-pr-record | 4184 |
| `feat/4170-stable-tool-id` | `e7071f720408` | 1 | pr-merged-tip | merged-pr-record | 4183 |
| `fix/4164-embedded-marker-selection` | `029d27cdf05c` | 1 | pr-merged-tip | merged-pr-record | 4198 |
| `fix/4098-tmpdir-hardening` | `75e27a39d880` | 1 | pr-merged-tip | merged-pr-record | 4137 |
| `docs/4293-runbook-probe-host` | `e6a4b9408b1a` | 0 | pr-merged-tip | merged-pr-record | 4297 |
| `feat/4282-bridge-table` | `c9de1be02638` | 0 | ancestry | reachable-from-main | — |
| `fix/3498-control-plane-offloop` | `7e4dbc30758d` | 0 | pr-merged-tip | merged-pr-record | 4352 |

## Safe by history — held by a worktree (branch preserved)

The delegate preserves these checkouts (dirty / ignored-artifact / too-recent), or they were not torn down; the branch is kept. Reported, not deleted.

| branch | worktree | verdict | PR |
|---|---|---|---|
| `cleanup/1509-merged` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/1509-RUN` | ancestry | — |
| `feat/1931-authoring-cli` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/feat/1931-authoring-cli` | pr-merged-tip | 2016 |
| `feat/2080-gbrain-scope-v2` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/feat/2080-gbrain-scope-v2` | pr-merged-tip | 2091 |
| `feat/2185-longmem-usage-cost` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/feat/2185-longmem-usage-cost` | ancestry | — |
| `feat/2080-w7a-longmem` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/feat/2080-w7a-longmem` | ancestry | — |
| `feat/2080-w5-phase-g` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/feat/2080-w5-phase-g` | ancestry | — |
| `fix/2423-pointsuperseded-rebuild` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix/2423-pointsuperseded-rebuild` | pr-merged-tip | 2436 |
| `opt/2514-ops-corpus` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/opt/2514-ops-corpus` | ancestry | — |
| `opt/2521-intent` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/opt/2521-intent` | ancestry | — |
| `opt/2552-emission` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/opt/2552-emission` | ancestry | — |
| `opt/2567-loop` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/opt/2567-loop` | ancestry | — |
| `fix/sc2300` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/sc2300` | ancestry | — |
| `fix/sc2301` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/sc2301` | ancestry | — |
| `fix/sc2302` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/sc2302` | ancestry | — |
| `fix/sc2303` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/sc2303` | ancestry | — |
| `fix/sc2305` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/sc2305` | ancestry | — |
| `fix/sc2306` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/sc2306` | ancestry | — |
| `fix/sc2308` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/sc2308` | ancestry | — |
| `fix/sc2317` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/sc2317` | ancestry | — |
| `fix/sc2318` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/sc2318` | ancestry | — |
| `feat/2587-quiet-tier-routing` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/feat/2587-quiet-tier-routing` | ancestry | — |
| `opt/eval-ingest-cache` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/opt/eval-ingest-cache` | ancestry | — |
| `fix/2558-main-ci-unblock` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/raud2558` | ancestry | — |
| `fix/2558-battery-uri-guard` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix/2558-battery-uri-guard` | ancestry | — |
| `feat/onboarding-auto-complete` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/feat/onboarding-auto-complete` | pr-merged-tip | 2631 |
| `fix/sc2307` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/sc2307` | ancestry | — |
| `fix/sc2311` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/sc2311` | ancestry | — |
| `fix/tdz-connected-once-ref` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix/tdz-connected-once-ref` | pr-merged-tip | 2646 |
| `fix/sc2319` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/sc2319` | ancestry | — |
| `feat/600-agents-materialization` | `/Users/danielospina/Documents/GitHub/wt-tortoise-agents` | pr-merged-tip | 2658 |
| `feat/modal-create-invoke-fix` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/feat/modal-create-invoke-fix` | ancestry | — |
| `fix/reapply-auto-complete` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix/reapply-auto-complete` | pr-merged-tip | 2669 |
| `fix/stale-minted-key-test` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix/stale-minted-key-test` | pr-merged-tip | 2672 |
| `feat/1701-oauth-routing` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/feat/1701-oauth-routing` | ancestry | — |
| `feat/2600-attribution-plumbing` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/feat/2600-attribution-plumbing` | ancestry | — |
| `fix/2673-claim-e2e-patch` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix/2673-claim-e2e-patch` | ancestry | — |
| `fix/2676-drift-tests` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix/2676-drift-tests` | ancestry | — |
| `feat/2599-machine-model-attribution` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/feat/2599-machine-model-attribution` | ancestry | — |
| `feat/2681-producer-machine-id` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/feat/2681-producer-machine-id` | ancestry | — |
| `opt/2080-measure` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/opt/2080-measure` | ancestry | — |
| `feat/wizard-connect-step-cleanup` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/wizard-connect-step-cleanup` | pr-merged-tip | 2698 |
| `fix/2710-2711-wizard-connect` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix-2710-2711-wizard-connect` | ancestry | — |
| `fix/2703-battery-report-writers` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix/2703-battery-report-writers` | ancestry | — |
| `feat/2701-graphs-rename-delete` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/feat-2701` | ancestry | — |
| `feat/2475-toggle-persist` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/feat/2475-toggle-persist` | ancestry | — |
| `feat/2481-revoked-cap` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/feat/2481-revoked-cap` | ancestry | — |
| `feat/2476-last-used` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/feat/2476-last-used` | ancestry | — |
| `fix/2709-tdz-harness-key-deps` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix/2709-tdz-harness-key-deps` | pr-merged-tip | 2737 |
| `docs/evidence-assembly-wave` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/docs/evidence-assembly-wave` | pr-merged-tip | 2752 |
| `chore/2724-hygiene-sweep` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/chore/2724-hygiene-sweep` | ancestry | — |
| `fix/main-green-2772-2773-2774` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix/main-green` | ancestry | — |
| `fix/2851-backup-watcher-os-shadow` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix/2851-backup-watcher-os-shadow` | ancestry | — |
| `fix/2796-killswitch-loud` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix/2796-killswitch-loud` | ancestry | — |
| `fix/700-review-cap` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix-700-review-cap` | ancestry | — |
| `fix/2965-ci-manifest` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix/2965-ci-manifest` | ancestry | — |
| `feat/2578-temporal-measurement` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/feat/2578-temporal-measurement` | pr-merged-tip | 2693 |
| `fix/2850-fly-routing-checks` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix/2850-fly-routing-checks` | ancestry | — |
| `fix/2850-health-signal-decouple` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix/2850-health-signal-decouple` | ancestry | — |
| `fix/2993-restore-always-restarts` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix/2993-restore-always-restarts` | ancestry | — |
| `fix/2922-2923-boot-credential-and-watcher` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix/2922-2923-boot-credential-and-watcher` | pr-merged-tip | 2984 |
| `feat/2833-oauth-claude-connector` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/feat/2833-oauth-claude-connector` | pr-merged-tip | 2910 |
| `fix/2976-temporal-retrieval` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix/2976-temporal-retrieval` | pr-merged-tip | 3004 |
| `fix-2993-restore-restart` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix-2993-restore-restart` | pr-merged-tip | 2995 |
| `fix/2844-r2-down-dedup-sentinel` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/2844-r2-down-dedup-sentinel` | ancestry | — |
| `fix/2983-mask-uri-userinfo` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix/2983-mask-uri-userinfo` | pr-merged-tip | 3040 |
| `fix/2974-bgsave-endpoint` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix/2974-bgsave-endpoint` | pr-merged-tip | 3056 |
| `research/2976-subgraph-competitors` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/research/2976-subgraph-competitors` | pr-merged-tip | 3017 |
| `fix/2552-operator-edges` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix/2552-operator-edges` | pr-merged-tip | 3071 |
| `docs/2952-2976-decisions` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/docs/2952-2976-decisions` | pr-merged-tip | 3079 |
| `fix/2952-degraded-read` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix/2952-degraded-read` | pr-merged-tip | 3096 |
| `fix/2988-health-ready-blocks-loop` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix/2988-health-ready-blocks-loop` | pr-merged-tip | 3009 |
| `opt/2976-rerank` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/opt/2976-rerank` | pr-merged-tip | 3123 |
| `feat/3011-context-assembly-impl` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/feat/3011-context-assembly-impl` | pr-merged-tip | 3108 |
| `fix/2978-empty-slots` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix/2978-empty-slots` | pr-merged-tip | 3000 |
| `fix/2976-temporal-leg` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix/2976-temporal-leg` | pr-merged-tip | 3220 |
| `fix/2977-object-retracted` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix/2977-object-retracted` | ancestry | — |
| `fix/3232-connect-verify-restart-order` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix/3232-connect-verify-restart-order` | ancestry | — |
| `fix/main-red-3140-3141` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix/main-red-3140-3141` | pr-merged-tip | 3257 |
| `feat/2863-oauth-code-atomicity` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/feat/2863-oauth-code-atomicity` | pr-merged-tip | 3097 |
| `feat/2866-dcr-capacity-policy` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/feat/2866-dcr-capacity-policy` | pr-merged-tip | 3130 |
| `fix/blog-admin-auth-gate` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix/blog-admin-auth-gate` | pr-merged-tip | 3279 |
| `feat/2865-wizard-oauth-path` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/feat/2865-wizard-oauth-path` | pr-merged-tip | 3256 |
| `fix/2850-fly-topology-redundancy` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix/2850-fly-topology-redundancy` | pr-merged-tip | 3063 |
| `fix/1744-parallel-ingest` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix/1744-parallel-ingest` | pr-merged-tip | 3357 |
| `fix/onboarding-skill-pi-row-collision` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix/onboarding-skill-pi-row-collision` | pr-merged-tip | 3249 |
| `fix/2850-health-liveness-decouple` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix/2850-health-liveness-decouple` | pr-merged-tip | 3062 |
| `fix/3263-extractedfrom` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix/3263-extractedfrom` | pr-merged-tip | 3295 |
| `feat/2850-watchdog-alerting` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/feat/2850-watchdog-alerting` | pr-merged-tip | 3064 |
| `fix/3129-capture-replay-race` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix/3129-capture-replay-race` | ancestry | — |
| `fix/3154-graphcopy-bool` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix/3154-graphcopy-bool` | pr-merged-tip | 3343 |
| `fix/3276-has-ep-measured` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix/3276-has-ep-measured` | pr-merged-tip | 3414 |
| `fix/3277-insight-ranking` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix/3277-insight-ranking` | pr-merged-tip | 3417 |
| `fix/3416-uptime-coupled-test` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix/3416-uptime-coupled-test` | ancestry | — |
| `fix/3350-health-thread-leak` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix/3350-health-thread-leak` | pr-merged-tip | 3420 |
| `fix/3139-ep-noop` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix/3139-ep-noop` | pr-merged-tip | 3351 |
| `fix/2846-loopback-redirect-port-agnostic` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix/2846-loopback-redirect-port-agnostic` | pr-merged-tip | 3408 |
| `fix/main-manifest-drift` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix/main-manifest-drift` | pr-merged-tip | 3449 |
| `fix/2971-terminal-dedup-nullhash` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix/2971-terminal-dedup-nullhash` | pr-merged-tip | 3028 |
| `fix/2498-lifecycle-guards` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix/2498-lifecycle-guards` | pr-merged-tip | 3391 |
| `fix/2975-oauth-escape` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix/2975-oauth-escape` | pr-merged-tip | 2999 |
| `docs/2835-prior-art-scan` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/docs/2835-prior-art-scan` | pr-merged-tip | 3457 |
| `chore/group-d-main-red` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/group-d-main-red` | ancestry | — |
| `fix/3465-backup-e2e-bgsave-mock` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/3465-backup-e2e-bgsave-mock` | pr-merged-tip | 3466 |
| `fix/harness-cell-points-arity` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/harness-cell-arity` | pr-merged-tip | 3477 |
| `fix/3459-pin-redis-below-8` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix/3459-redislite-cleanup-guard` | pr-merged-tip | 3476 |
| `feat/3359-graph-ops` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/3359-graph-ops` | ancestry | — |
| `measure/graphops` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/measure-graphops` | ancestry | — |
| `fix/3458-group-e-census` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix/3458-group-e-census` | pr-merged-tip | 3511 |
| `docs/2835-identity-decision` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/docs/2835-identity-decision` | pr-merged-tip | 3584 |
| `feat/3501-auth-session` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/3501-auth-session` | pr-merged-tip | 3572 |
| `fix/3587-closed-pr-rest` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix-3587-closed-pr-rest` | ancestry | — |
| `fix/3501-binding-gate` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix/3501-binding-gate` | pr-merged-tip | 3618 |
| `docs/3642-ontology-temporal-model` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/docs/3642-ontology-temporal-model` | pr-merged-tip | 3643 |
| `feat/2552-operator-edges-investigate` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/feat/2552-operator-edges-investigate` | ancestry | — |
| `fix/3654-mining-validfrom-fallback` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix/3654-mining-validfrom-fallback` | ancestry | — |
| `fix/mcp-json-hosted-transport` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix/mcp-json-hosted-transport` | ancestry | — |
| `fix/2847-client-identity` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix/2847-client-identity` | pr-merged-tip | 3672 |
| `feat/3359-capture-cost` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/impl/3359-capture-cost` | ancestry | — |
| `fix/3546-central-embedded-lock` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix-3546-central-lock` | pr-merged-tip | 3706 |
| `fix/3657-cursor-exit-evidence` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix/3657-cursor-exit-evidence` | pr-merged-tip | 3668 |
| `fix/3653-redis-tempdir-clobber` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/3653-redis` | pr-merged-tip | 3710 |
| `fix/3428-2937-connect-verification` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix/3428-2937-connect-verification` | pr-merged-tip | 3704 |
| `chore/3694-dashboard-eslint` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/chore/3694-dashboard-eslint` | pr-merged-tip | 3723 |
| `chore/1128-bump-agent-infra-v0.1.3` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/1128-v013-pins` | pr-merged-tip | 3751 |
| `fix/2981-maxmemory-not-corruption` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix/2981-maxmemory-message` | pr-merged-tip | 3758 |
| `fix/backup-durability-b5` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/backup-durability-b5` | pr-merged-tip | 3624 |
| `fix/2573-embedder-skip-reason` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix/2573-embedder-skip-reason` | pr-merged-tip | 3760 |
| `test/3359-capture-cost-behavioural` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/b7-cost-test-audit` | ancestry | — |
| `fix/3718-rest-offload` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix/3718-rest-offload` | pr-merged-tip | 3772 |
| `fix/3299-journaled-delete` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/3299-journal-delete` | pr-merged-tip | 3696 |
| `fix/3769-embedded-seam` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/3769-embedded-seam` | pr-merged-tip | 3774 |
| `fix/3782-capture-claim-install-pending` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix/3782-capture-claim-install-pending` | pr-merged-tip | 3791 |
| `fix/3781-remove-beta-gate` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix/3781-remove-beta-gate` | pr-merged-tip | 3790 |
| `fix/3755-install-probe-gate` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix/3755-install-probe-gate` | pr-merged-tip | 3799 |
| `fix/2795-replay-open-set` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix/2795-replay-open-set` | pr-merged-tip | 2958 |
| `fix/3754-claude-hook-timeout` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix/3754-claude-hook-timeout` | pr-merged-tip | 3793 |
| `chore/3775-untrack-dashboard-dist` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/3775-untrack-dashboard-dist` | pr-merged-tip | 3794 |
| `fix/3783-onboarding-key-allowance` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix/3783-onboarding-key-allowance` | pr-merged-tip | 3807 |
| `fix/2878-dr-flake` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix-2878-dr-flake` | pr-merged-tip | 3788 |
| `docs/embedded-is-not-an-offering` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/docs/embedded-is-not-an-offering` | pr-merged-tip | 3861 |
| `fix/3575-pi-capture-seam` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix/3575-pi-capture-seam` | pr-merged-tip | 3721 |
| `fix/3400-duration-balanced-halves` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix/3400-duration-balanced-halves` | pr-merged-tip | 3407 |
| `fix/3832-not-configured` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix/3832-not-configured` | pr-merged-tip | 3893 |
| `feat/d3-session-identity` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/d3-session-identity` | pr-merged-tip | 3888 |
| `test/3810-availability-record-baseline` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/3810-availability-record` | pr-merged-tip | 3897 |
| `fix/3447-reinstate-loop-liveness` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix-3447-loop-liveness` | pr-merged-tip | 3891 |
| `fix/3952-admin-bundle-base` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/3952-admin-base` | pr-merged-tip | 3958 |
| `fix/3845-producer` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/3845-producer` | pr-merged-tip | 3964 |
| `fix/3677-analytics-event-loss` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/b7-analytics-loss` | pr-merged-tip | 3749 |
| `fix/deploy-parity-requirements-drift` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix-deploy-parity` | pr-merged-tip | 3974 |
| `fix/3845-fork-wedge` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/3845-fork-wedge` | pr-merged-tip | 3924 |
| `fix/3950-blog-discoverability` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix/3950-blog-discoverability` | pr-merged-tip | 3962 |
| `fix/2943-prewipe-snapshot` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/3010-merge` | pr-merged-tip | 3010 |
| `fix/3890-empty-state-destination` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix/3890-empty-state-destination` | pr-merged-tip | 3989 |
| `feat/3808-install-seam` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/feat/3808-install-seam` | pr-merged-tip | 3917 |
| `fix/3892-keyless-capture-stores-turns` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/3892-write-turns` | pr-merged-tip | 4014 |
| `feat/3805-read-path-contract` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/feat-3805-read-path-contract` | ancestry | — |
| `fix/3914-ask-seeders-capture-shape` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/3914-ask-seeder-capture-shape` | pr-merged-tip | 4011 |
| `fix/3947-rebuild-turn-points` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/3947-rebuild-turn-points` | pr-merged-tip | 3957 |
| `fix/supersede-validfrom-contiguity` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/supersede-vf` | pr-merged-tip | 3984 |
| `research/narrow-vs-broad-mcp-surface` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/research/narrow-vs-broad-mcp-surface` | ancestry | — |
| `fix/3485-auth-loop` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/3485-auth-loop` | pr-merged-tip | 4003 |
| `fix/3813-restore-swap-timeout` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/3813-restore-swap-timeout` | pr-merged-tip | 3859 |
| `fix/3485-blog-admin-session` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/3485-blog-admin-session` | pr-merged-tip | 4016 |
| `feat/3820-analytics-fallback-alert` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/b7-analytics-alert` | ancestry | — |
| `fix/3625-pglite-identity-data-shim` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix/3625-pglite-identity-data-shim` | ancestry | — |
| `fix/2901-terminal-status` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix/2901-terminal-status` | pr-merged-tip | 4066 |
| `feat/3805-client-status-vocabulary` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix/3805-client-status-vocabulary` | pr-merged-tip | 4044 |
| `fix/4028-precision-leak` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/4028-precision-leak` | pr-merged-tip | 4075 |
| `fix/4056-fork-slot-title-wait` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/4056-fork-slot-title-wait` | pr-merged-tip | 4067 |
| `fix/3523-unknown-address` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/3523-unknown-address` | pr-merged-tip | 4006 |
| `fix/3849-ask-eval-only` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/3849-ask-eval-only` | pr-merged-tip | 3929 |
| `fix/3895-dump-asymmetry` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/3895-dump-asymmetry` | pr-merged-tip | 3921 |
| `fix/3689-annotator-props-journal` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/3689-annotator` | pr-merged-tip | 4074 |
| `chore/3818-codex-live-leg` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/codex-live-leg` | pr-merged-tip | 4024 |
| `fix/3821-silent-drop-telemetry` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix/3821-silent-drop-telemetry` | ancestry | — |
| `fix/4010-remove-session-cap` | `/private/tmp/wt-4010` | pr-merged-tip | 4048 |
| `fix/2573-ci-real-embedder` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix/2573-ci-real-embedder` | pr-merged-tip | 4123 |
| `feat/b7-activation-scorecard` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/b7-activation` | ancestry | — |
| `feat/3824-billed-calls-rollup` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/b7-3824-billed-calls` | ancestry | — |
| `fix/4010-provenance-record` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix-4010-provenance` | pr-merged-tip | 4138 |
| `fix/ask-shape-rate-post-3929` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/ask-shape-rate-post-3929` | pr-merged-tip | 4139 |
| `fix/3874-key-allowance-v2` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix/3874-key-allowance-v2` | pr-merged-tip | 4141 |
| `chore/3863-canonical-mcp-list` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/3863-canonical-list` | pr-merged-tip | 4120 |
| `test/cross-tenant-read-isolation` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/xtenant-isolation` | pr-merged-tip | 3697 |
| `fix/4106-undatable-turns` | `/private/tmp/wt-4106` | pr-merged-tip | 4154 |
| `fix/3912-repair-false-decide-completed` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix/3912-repair-false-decide-completed` | pr-merged-tip | 4152 |
| `fix/4096-validity-tmp-leak` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/4096-validity-tmp-leak` | pr-merged-tip | 4149 |
| `feat/3819-cursor-capture-seam` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/feat/3819-cursor-capture-seam` | pr-merged-tip | 4110 |
| `fix/3889-untrack-dashboard-dist-chunk` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix-3889` | pr-merged-tip | 4172 |
| `fix/4009-surface-recut` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/4009-surface-recut` | ancestry | — |
| `fix/4047-fork-embedded-carveout` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/4047-fork-carveout` | pr-merged-tip | 4169 |
| `docs/m1-read-path-latency-profile` | `/private/tmp/wt-m1-latency` | pr-merged-tip | 4196 |
| `chore/4097-env-truthy-vocabulary` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/4097-env-truthy` | pr-merged-tip | 4151 |
| `feat/3665-cohort-cost-cap` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/b7-cost-cap` | ancestry | — |
| `fix/4179-retention-deletion-source-of-truth` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix/4179-retention-deletion-source-of-truth` | pr-merged-tip | 4191 |
| `feat/3825-metering-window` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/b7-3825-window` | ancestry | — |
| `fix/4162-fork-producer-ci` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/4162-fork-producer` | pr-merged-tip | 4209 |
| `docs/w6c-dense-leg-baseline` | `/private/tmp/w6c-receipt` | pr-merged-tip | 4217 |
| `feat/3809-capture-verify` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/feat/3809-capture-verify` | pr-merged-tip | 4182 |
| `fix/4136-reaper-provenance` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/4136-reaper-provenance` | pr-merged-tip | 4236 |
| `ete-w1-g2` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/ete-w1-g2` | ancestry | — |
| `ete-w1-g3` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/ete-w1-g3` | ancestry | — |
| `fix/4221-never-executes` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/4221-never-executes` | pr-merged-tip | 4229 |
| `fix/4223-ci-waste` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/4223-ci-waste` | pr-merged-tip | 4245 |
| `fix/3892-hosted-keyless-capture-store` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix/3892-hosted-keyless-capture-store` | pr-merged-tip | 4195 |
| `fix/4027-collision-preflight-repo` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/4027-collision-preflight` | pr-merged-tip | 4204 |
| `fix/4214-redislite-atexit-hang` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix-4214-atexit-hang` | ancestry | — |
| `fix/3860-anchor-label` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/3860-anchor-label` | pr-merged-tip | 4228 |
| `fix/3902-restore-seq` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/3902-restore-seq` | pr-merged-tip | 4227 |
| `fix/4156-merge-capture-session-time` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/4156-merge-capture-session-time` | pr-merged-tip | 4274 |
| `fix/4005-source-index-identity` | `/Users/danielospina/Documents/GitHub/tortoise-wt-4005` | pr-merged-tip | 4119 |
| `fix/4222-cannot-fail` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/4222-cannot-fail` | pr-merged-tip | 4261 |
| `fix/4216-metering-period-anchor` | `/Users/danielospina/Documents/GitHub/tortoise-wt-4216` | ancestry | — |
| `fix/4221-never-invoked` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/4221b-never-invoked` | pr-merged-tip | 4296 |
| `feat/3664-subject-object-capture` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/feat/3664-subject-object-capture` | pr-merged-tip | 3722 |
| `fix/4298-lint` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/4298-lint` | pr-merged-tip | 4306 |
| `fix/4291-session-seam` | `/private/tmp/4291-session-seam` | pr-merged-tip | 4318 |
| `fix/4220-blog-residue` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/4220-blog-residue` | pr-merged-tip | 4316 |
| `fix/4246-pricing` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/4246-pricing` | pr-merged-tip | 4322 |
| `fix/4194-turn-embedding-v2` | `/private/tmp/w5a-4194` | pr-merged-tip | 4304 |
| `fix/4281-auth-probe-origin` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/4281-auth-probe-origin` | pr-merged-tip | 4286 |
| `fix/3841-surface-resolution-test` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/3841-surface-resolution` | pr-merged-tip | 3898 |
| `fix/3658-3659-backup-signal` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/b5-3658-3659-backup-signal` | pr-merged-tip | 4321 |
| `fix/4305-graph-only-derived` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/4305-graph-only-derived` | ancestry | — |
| `fix/3784-decide-completed` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix/3784-decide-completed` | pr-merged-tip | 3949 |
| `fix/4367-cut-nightly` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/4367-cut-nightly` | pr-merged-tip | 4376 |
| `fix/4341-mitigation-advisory` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix/4341-mitigation-advisory` | ancestry | — |
| `fix/4256-strand-rescue` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/4256-strand-rescue` | ancestry | — |
| `fix/4387-hermetic-suite` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/4387-hermetic-suite` | ancestry | — |
| `fix/4385-tortoise-decide-advisory` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix/4385-tortoise-decide-advisory` | ancestry | — |
| `chore/w0-substrate` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/w0-substrate` | pr-merged-tip | 3978 |
| `fix/4327-installer-overwrite` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix/4327-installer-overwrite` | pr-merged-tip | 4400 |
| `fix/4126-managed-env` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/4126-managed-env` | pr-merged-tip | 4259 |
| `fix/w7a-seeder-embeds-turns` | `/private/tmp/w7a-seeder` | pr-merged-tip | 4395 |
| `fix/4366-mit-licence-home` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix/4366-mit-licence-home` | ancestry | — |
| `fix/4356-rotate-check-before-revoke` | `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix/4356-rotate-check-before-revoke` | ancestry | — |

## Judgement — no PR, not an ancestor (never auto-deleted)

Oldest first. A human decides.

| branch | tip | age (days) |
|---|---|---|
| `feat/495-onboarding-artifact` | `b4406dd153e6` | 44 |
| `feat/498-onboarding-api` | `67d2f08b165e` | 44 |
| `feat/496-onboarding-questions` | `3b1ffd0bd968` | 44 |
| `feat/501-onboarding-analytics` | `3b33fb7a2f60` | 44 |
| `feat/497-onboarding-welcome` | `226f15896c56` | 44 |
| `feat/500-onboarding-demo` | `759cfc3dbd3d` | 44 |
| `feat/499-onboarding-github` | `f74bfa95942d` | 44 |
| `feat/502-onboarding-e2e` | `9f36c9b83450` | 44 |
| `feat/570-session-key-v2` | `7b1ff18929be` | 44 |
| `fix/722-extraction-modes` | `794ec893618f` | 42 |
| `feat/669-supabase-control-plane` | `0374955fe53a` | 42 |
| `fix/717-review` | `a53b4d3c604f` | 42 |
| `pr853` | `8a74bfc93265` | 41 |
| `pr875` | `be8f404e5034` | 41 |
| `m888-907` | `f9b4fa9e52da` | 40 |
| `fix/915-rebase-tmp` | `bad02052eb37` | 40 |
| `fix/915-rebase2-tmp` | `774bf8c37caa` | 40 |
| `pr943` | `cfa7baa3fb21` | 39 |
| `pr944` | `6cacffee631c` | 39 |
| `feat/epic909-949-pack-v3-rebase` | `382fb5defab1` | 39 |
| `pr-994` | `b0fd894054f3` | 39 |
| `pr-995` | `ccffde6834eb` | 39 |
| `pr1006` | `94affcdd2812` | 39 |
| `c15009f` | `c15009f81ee6` | 38 |
| `pr-1073` | `35dd4826b5e6` | 38 |
| `pr-1080` | `bdd3e6c77a30` | 38 |
| `fix/1115-conflict-resolve` | `537e6b2d3197` | 38 |
| `pr-1210` | `25af332c8b6e` | 37 |
| `fix/1252-manifest-entry` | `874ca5b8c1e6` | 37 |
| `fix/email-notify-final` | `167d5e04b195` | 37 |
| `tmp/main-bench` | `614fa638d841` | 34 |
| `wt-1454-cw` | `28d704e78e69` | 33 |
| `pr1482-review` | `90f49f90bc0c` | 32 |
| `feat-1503-final` | `eb19d18d1287` | 31 |
| `pr-1718` | `5786bfa02047` | 26 |
| `pr1792` | `03a0d77b526f` | 24 |
| `feat/1891-expansion-pack-product` | `68db2798ba4e` | 23 |
| `pr1942` | `4d06d7936e5f` | 23 |
| `pr-merge` | `3a7cb3c0c39c` | 22 |
| `pr-2042` | `8c634e184d3a` | 21 |
| `pr-2144` | `e31e4a04253a` | 18 |
| `pr2214` | `7d85c5972f10` | 17 |
| `pr-2221` | `a7867a809946` | 16 |
| `pr-2224` | `6f48533f2362` | 16 |
| `fix/main-red-c5-verb-contract` | `6c065c1c9305` | 16 |
| `salvage/2026-09-04-hub-dirty-wip` | `c9256bd0a111` | 16 |
| `w7-rebase-dryrun` | `d86c87ae8c51` | 15 |
| `pr-2451` | `14c4457e3720` | 14 |
| `opt/2515-battery` | `a44e7958c5ef` | 13 |
| `opt/2513-retrieval-scope` | `9b822b9accdd` | 13 |
| `main-hygiene` | `0a87026fad26` | 13 |
| `pr-2666` | `87d8b86e82a4` | 11 |
| `salvage-20260909-dashboard-wip` | `1c411152a006` | 11 |
| `fix/2774-abuse-velocity` | `d759d65a5fbd` | 10 |
| `fix/2772-longmem-rerank` | `2c38b3580c77` | 10 |
| `fix/2216-overcommit-fork` | `29152b3b3192` | 10 |
| `salvage/20261001-shot-wizard-wip` | `f36afa1feea9` | 10 |
| `finish/2815-skip-guard` | `73edbf0095eb` | 10 |
| `finish/2815-b` | `36adb8803b4a` | 9 |
| `finish/2815-c` | `36adb8803b4a` | 9 |
| `feat/2296-durability-surface` | `7f146adfa6ea` | 9 |
| `pr2949` | `3222472ecf50` | 9 |
| `chore/hub-salvage-2958-cr-logs` | `7162c55273b7` | 9 |
| `pr-2999` | `cfc584f5d132` | 9 |
| `pr-3028` | `42f4eda90410` | 9 |
| `pr-3010` | `7e191d9e971f` | 9 |
| `pr-3068` | `6946a7940c03` | 9 |
| `pr3068` | `6946a7940c03` | 9 |
| `tmp3219` | `11998b0595c9` | 8 |
| `pr3229tmp` | `e39ecda08ebb` | 8 |
| `pr3247` | `d563c95d3bee` | 8 |
| `pr3257` | `7a93e87e6f56` | 8 |
| `pr-3281` | `9d40e62f5218` | 8 |
| `pr-3354` | `ad70893d7e13` | 8 |
| `rescue/3062-rebase` | `3c6d2f7fa59f` | 8 |
| `fix/3247-refit` | `449df813d75a` | 8 |
| `pr-3391` | `13a49e939ed1` | 8 |
| `docs/scoping-3055` | `6677611089b5` | 7 |
| `pr-3426` | `6b395bc485d1` | 7 |
| `tmp-3511-orphan-threshold` | `344b01dc58c6` | 5 |
| `feat/3543-tenancy-rename` | `1ed87510d269` | 5 |
| `pr-3576-review` | `3479195932cb` | 5 |
| `pr-2948` | `9642057418e4` | 5 |
| `2813-revive-passthrough` | `258d0049ac95` | 5 |
| `fix/3284-jwks-cold-start-bound` | `83399e14615e` | 4 |
| `integration/3575-3664-exit-evidence` | `3e985a0175ff` | 4 |
| `fix/3143-rebased` | `bbfe710becf3` | 4 |
| `b6/2813-read-side` | `cb9eee09a35a` | 4 |
| `pr-3722` | `ef64dd5334bf` | 3 |
| `wip/b7-analytics-loss-2026-09-17` | `ddb2f1b1813f` | 3 |
| `rebuild/3281-on-remote` | `61d3fc2c5d08` | 3 |
| `research/b5-raw-storage-ux-byo` | `a0ea5e81fe91` | 3 |
| `b6/c2-ab-prereg` | `f355e1fb489d` | 3 |
| `b6/c2-ab-sealed` | `4824ac3d81c9` | 3 |
| `b6/entity-alt-mechanism` | `4262ed02520b` | 3 |
| `pre-rebase-3950-750103a28` | `750103a28d43` | 2 |
| `pre-reword-backup` | `8afe35447198` | 2 |
| `chore/3863-approval-columns` | `a3b88db6596d` | 2 |
| `pr-4040` | `5db509708d64` | 2 |
| `pr-4044` | `7b452c0c73d4` | 2 |
| `pr4044` | `c00bf1a47f33` | 2 |
| `pr4048` | `15634e4d03ff` | 2 |
| `pr-4064` | `b14f99ac2ab5` | 1 |
| `b6-backup-w4a-4158` | `50785f5631c9` | 1 |
| `backup/w0-substrate-2026-09-20` | `8991259bac79` | 1 |
| `backup/j2-4158-pre-rebase` | `846876f15339` | 0 |
| `docs/j3-4065-crash-recovery-adjudication` | `52f80d9aae40` | 0 |

## Detached-HEAD worktrees (never auto-deleted)

| path | HEAD | age (days) |
|---|---|---|
| `/private/tmp/laneB2c-bb3/origin-main-wt` | `017f4dac41b9` | — |
| `/private/tmp/wt782-pre.sW4BZP` | `03e2b33ff9d8` | — |
| `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/verify-main` | `090fa004c7e0` | — |
| `/private/tmp/scratch-tortoise-6vppO1` | `1054094e62ed` | — |
| `/private/tmp/j3-4065/head` | `1fb9555112da` | — |
| `/private/tmp/4305-base` | `2381d8f88755` | — |
| `/private/tmp/4258-baseline` | `2b38792b7508` | — |
| `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/sweep-2747` | `2d7af8bfcfcb` | — |
| `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/pre-2710-2711` | `3245fcab3697` | — |
| `/private/tmp/3892-live-keyless` | `3be34cdd103e` | — |
| `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix/3805-readpath-contract` | `3be34cdd103e` | — |
| `/private/tmp/lockchk` | `3f883f2a1557` | — |
| `/private/tmp/4305-4263` | `4937ed73ec65` | — |
| `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/feat-3883-retired-name-warning` | `4a7b7202e993` | — |
| `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/sweep-2950` | `4dfb33c158c0` | — |
| `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/1509-REVAL` | `57f439782457` | — |
| `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/b7-3749-control` | `59b78595b2aa` | — |
| `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/sync-3068` | `6946a7940c03` | — |
| `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/sync-3426` | `7797396a6b12` | — |
| `/private/tmp/v-3863-A` | `79c3d9dbe730` | — |
| `/private/tmp/v-3863-B` | `79c3d9dbe730` | — |
| `/private/tmp/v-3863-main` | `79c3d9dbe730` | — |
| `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix/2943-prewipe-snapshot` | `7e191d9e971f` | — |
| `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix/3874-key-allowance` | `7ea8874fbfc3` | — |
| `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/1509-E1` | `7f78cb4848d8` | — |
| `/private/tmp/scratch-tortoise-FXB6go` | `83eef3a2a36c` | — |
| `/private/tmp/4047-pre` | `8a35f8b93304` | — |
| `/private/tmp/4097-base2` | `8a35f8b93304` | — |
| `/private/tmp/b1-main-proof` | `8b02980d60ee` | — |
| `/private/tmp/b5-main-probe-fd23` | `8b02980d60ee` | — |
| `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/sync-2948` | `9642057418e4` | — |
| `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/feat/3834-cold-busy-wait-bound/.worktrees/scratch-w3834base` | `9848157531b9` | — |
| `/private/tmp/w3a-4105-mainbase` | `a4b436572a2a` | — |
| `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/verify-3511` | `a718107d3fd6` | — |
| `/private/tmp/scratch-tortoise-vTm6vJ` | `ab27c2e5ae35` | — |
| `/private/tmp/scratch-tortoise-XdcpgK` | `ab27c2e5ae35` | — |
| `/private/tmp/tortoise-baseline-4096` | `b86f6ef20e09` | — |
| `/private/tmp/j3-4065/rebased` | `c0c1ed4dd9a7` | — |
| `/private/tmp/proof-main` | `c9de1be02638` | — |
| `/private/tmp/w8a-main` | `cb8aa661284a` | — |
| `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/batch-2589-followup` | `ccd636acb320` | — |
| `/private/tmp/wt-4010-base` | `d0b699bd7bf6` | — |
| `/private/tmp/4097-base` | `d26e1e7cda08` | — |
| `/private/tmp/4202-dense` | `d51306c51de7` | — |
| `/private/tmp/j3-4065/base` | `d7c102559d2b` | — |
| `/private/tmp/mainchk` | `e29fb36d0b7f` | — |
| `/private/tmp/laneB2c-z3/origin-main-wt` | `e2f4bdc92cd4` | — |
| `/private/tmp/4305-main` | `f2b3e91b2060` | — |
| `/private/tmp/j3-4065/main` | `f2b3e91b2060` | — |
| `/private/tmp/maincheck-b3` | `f2b3e91b2060` | — |
| `/Users/danielospina/Documents/GitHub/tortoise/.worktrees/sync-3324` | `fc2aa045041d` | — |

## Delegated worktree engine (`pi-reap-worktrees.sh`)

```
ot classified
preserve /Users/danielospina/Documents/GitHub/tortoise/.worktrees/sc2300
         reason=deferred  pass budget 300s exhausted — not classified
preserve /Users/danielospina/Documents/GitHub/tortoise/.worktrees/sc2301
         reason=deferred  pass budget 300s exhausted — not classified
preserve /Users/danielospina/Documents/GitHub/tortoise/.worktrees/sc2302
         reason=deferred  pass budget 300s exhausted — not classified
preserve /Users/danielospina/Documents/GitHub/tortoise/.worktrees/sc2303
         reason=deferred  pass budget 300s exhausted — not classified
preserve /Users/danielospina/Documents/GitHub/tortoise/.worktrees/sc2305
         reason=deferred  pass budget 300s exhausted — not classified
preserve /Users/danielospina/Documents/GitHub/tortoise/.worktrees/sc2306
         reason=deferred  pass budget 300s exhausted — not classified
preserve /Users/danielospina/Documents/GitHub/tortoise/.worktrees/sc2307
         reason=deferred  pass budget 300s exhausted — not classified
preserve /Users/danielospina/Documents/GitHub/tortoise/.worktrees/sc2308
         reason=deferred  pass budget 300s exhausted — not classified
preserve /Users/danielospina/Documents/GitHub/tortoise/.worktrees/sc2311
         reason=deferred  pass budget 300s exhausted — not classified
preserve /Users/danielospina/Documents/GitHub/tortoise/.worktrees/sc2317
         reason=deferred  pass budget 300s exhausted — not classified
preserve /Users/danielospina/Documents/GitHub/tortoise/.worktrees/sc2318
         reason=deferred  pass budget 300s exhausted — not classified
preserve /Users/danielospina/Documents/GitHub/tortoise/.worktrees/sc2319
         reason=deferred  pass budget 300s exhausted — not classified
preserve /Users/danielospina/Documents/GitHub/tortoise/.worktrees/scoping-3055
         reason=deferred  pass budget 300s exhausted — not classified
preserve /Users/danielospina/Documents/GitHub/tortoise/.worktrees/supersede-vf
         reason=deferred  pass budget 300s exhausted — not classified
preserve /Users/danielospina/Documents/GitHub/tortoise/.worktrees/sweep-2747
         reason=deferred  pass budget 300s exhausted — not classified
preserve /Users/danielospina/Documents/GitHub/tortoise/.worktrees/sweep-2950
         reason=deferred  pass budget 300s exhausted — not classified
preserve /Users/danielospina/Documents/GitHub/tortoise/.worktrees/sync-2948
         reason=deferred  pass budget 300s exhausted — not classified
preserve /Users/danielospina/Documents/GitHub/tortoise/.worktrees/sync-3068
         reason=deferred  pass budget 300s exhausted — not classified
preserve /Users/danielospina/Documents/GitHub/tortoise/.worktrees/sync-3324
         reason=deferred  pass budget 300s exhausted — not classified
preserve /Users/danielospina/Documents/GitHub/tortoise/.worktrees/sync-3426
         reason=deferred  pass budget 300s exhausted — not classified
preserve /Users/danielospina/Documents/GitHub/tortoise/.worktrees/verify-3511
         reason=deferred  pass budget 300s exhausted — not classified
preserve /Users/danielospina/Documents/GitHub/tortoise/.worktrees/verify-main
         reason=deferred  pass budget 300s exhausted — not classified
preserve /Users/danielospina/Documents/GitHub/tortoise/.worktrees/w0-substrate
         reason=deferred  pass budget 300s exhausted — not classified
preserve /Users/danielospina/Documents/GitHub/tortoise/.worktrees/w6b-graph-candidate
         reason=deferred  pass budget 300s exhausted — not classified
preserve /Users/danielospina/Documents/GitHub/tortoise/.worktrees/wizard-connect-step-cleanup
         reason=deferred  pass budget 300s exhausted — not classified
preserve /Users/danielospina/Documents/GitHub/tortoise/.worktrees/xtenant-isolation
         reason=deferred  pass budget 300s exhausted — not classified
preserve /Users/danielospina/Documents/GitHub/wt-tortoise-agents
         reason=deferred  pass budget 300s exhausted — not classified

armed pass complete: REMOVED=0 FAILED=0
```

## Post-apply results

| metric | value |
|---|---|
| deleted | 923 |
| refused | 0 |
| skipped (held / moved) | 237 |
| filesystem free before | 642050188 KiB |
| filesystem free after | 641849908 KiB |
| free-space delta | -200280 KiB |

### Recovery record (written before deletion)

| branch | tip | how |
|---|---|---|
| `2179-keepalive-siblings` | `ac24f6b68cf5ed1c672796f0bbb602764f5b275e` | reflog ~30d / `--backup-bundle` |
| `2529-oauth-fragment-fix` | `be3b4e0ee1169422ae65b7be665eeef646e2f1f9` | reflog ~30d / `--backup-bundle` |
| `2529-onboarding-invite-flow` | `d87f6893c92606aa6418322eda9a1a0ac4450930` | reflog ~30d / `--backup-bundle` |
| `base-check` | `b367d69fc9697ab709d6274033cea4ea8254c9db` | reflog ~30d / `--backup-bundle` |
| `chore/1067-welcome-e2e-secrets` | `fb07a602f3b9265409f70081eb7bd12e53eceae1` | reflog ~30d / `--backup-bundle` |
| `chore/1097-migration-docfix` | `9a1a57dd9148d948a0845c0e4e7f741a8d182ee7` | reflog ~30d / `--backup-bundle` |
| `chore/1146-pin-falkordb` | `e070967b615ac16f9a0ddfaecc262e2a65db5dfe` | reflog ~30d / `--backup-bundle` |
| `chore/1221-email-env` | `89eeeda502db36c0aeed48608c3ee115b6432b7a` | reflog ~30d / `--backup-bundle` |
| `chore/1436-loud-skips` | `16ea165593d6a398daf765f9f6d9a119d08ed849` | reflog ~30d / `--backup-bundle` |
| `chore/187-untrack-pycache` | `02504b5f73586541c5b21236cdb131e5b616214d` | reflog ~30d / `--backup-bundle` |
| `chore/1976-epic-planning` | `e503bb626fa8680a5a0f926e8603e7836630fd56` | reflog ~30d / `--backup-bundle` |
| `chore/206-rename-website` | `b3bc416e2b2d6a1e5aec886fa2e3c08c41e26bbf` | reflog ~30d / `--backup-bundle` |
| `chore/2174-lint-drift` | `f362a07129a13e1d49f2d8b52fec583ea2deaf45` | reflog ~30d / `--backup-bundle` |
| `chore/2238-ignore-agent-evidence-dirs` | `d37d5d053a13bc9c465464afd9751d1bd8af95a6` | reflog ~30d / `--backup-bundle` |
| `chore/2525-matched-recall-reconcile` | `1117aaa46c6bcb43594c201d64d16d59bb6d4570` | reflog ~30d / `--backup-bundle` |
| `chore/291-capstone` | `98cc1eb1d6dbc363e037e159a47e301c45f7a3f8` | reflog ~30d / `--backup-bundle` |
| `chore/2938-audit-fixes` | `4f88d91f61a3c16a36449b97bccc39c5a515b128` | reflog ~30d / `--backup-bundle` |
| `chore/2938-audit-fixes2` | `15642fcabf1b7232080a7379cb86ec50091a2994` | reflog ~30d / `--backup-bundle` |
| `chore/2938-surface-audit` | `2d084e7a42f8712fe3e266fb00ccb47b2dc2f0d3` | reflog ~30d / `--backup-bundle` |
| `chore/323-search-capstone` | `ea9be5465d67ae9f110e6785fe9f171a634c09e5` | reflog ~30d / `--backup-bundle` |
| `chore/375-cross-repo-readmes` | `7fe4d4b714d9b9b96d2ea63a671b819fd9872f1f` | reflog ~30d / `--backup-bundle` |
| `chore/419-exa-lazy` | `d285b117b6bba66e25b7673d762a6335919f0a70` | reflog ~30d / `--backup-bundle` |
| `chore/486-register-about-meta-keys` | `a094090c1282eb3f0b3f43e439d3a1ccd296e59a` | reflog ~30d / `--backup-bundle` |
| `chore/492-uv-adoption` | `4e9df9bb7494532201081fc890e3b787b9280b5e` | reflog ~30d / `--backup-bundle` |
| `chore/654-reconcile-schedule` | `8a16048eacdca99f7b420a9154113be24cfe36f9` | reflog ~30d / `--backup-bundle` |
| `chore/660-fly-token-hardening` | `3fc6702e60be49732008d802a94c00bc563e431e` | reflog ~30d / `--backup-bundle` |
| `chore/661-label-data-loss` | `ad38eb94562c8ae433c7b04a2a95ce7a5d473ee5` | reflog ~30d / `--backup-bundle` |
| `chore/669-plan-docs` | `1d83d85fe5b58353c817c1e1d38bf209e131e7e5` | reflog ~30d / `--backup-bundle` |
| `chore/677-postdeploy-legal-e2e` | `8fbc3cf7c316909e2a4ba717889afac78d6bfe80` | reflog ~30d / `--backup-bundle` |
| `chore/682-pricing-guard-test-hygiene` | `2b3f4cac1be4c3db3445911346c7233d76c44214` | reflog ~30d / `--backup-bundle` |
| `chore/682-pricing-json-guard` | `b2fe9772265cdc391777568ba3c1236e5aeffa9e` | reflog ~30d / `--backup-bundle` |
| `chore/690-status-vocab` | `de69e367e6b0bb27a9f7b91b22b7fbe65652e8f0` | reflog ~30d / `--backup-bundle` |
| `chore/690-status-vocabulary` | `b2fe9772265cdc391777568ba3c1236e5aeffa9e` | reflog ~30d / `--backup-bundle` |
| `chore/agent-infra-v0.1.2` | `5fb3b8d8ccdf8e1a85fd78e96d83005652c131f0` | reflog ~30d / `--backup-bundle` |
| `chore/restore-1987-ask-gates` | `3f33b7da25de1b866c925bf55c1d41a86dea510a` | reflog ~30d / `--backup-bundle` |
| `chore/verify-onboarding` | `c837e89338d7aa044f014e3ebb1a3f045d1abfa9` | reflog ~30d / `--backup-bundle` |
| `chore/version-bump-deploytest` | `5a2b4eb495d6d55b2433c62768c9a2ba54fd1be6` | reflog ~30d / `--backup-bundle` |
| `chore/website-redeploy` | `a3abd9d21ecf1bb09b5884f0387f96dfc6482cec` | reflog ~30d / `--backup-bundle` |
| `ci-check-local` | `2c280ab9793699b83d8aa3e619516e9cec561d0b` | reflog ~30d / `--backup-bundle` |
| `ci/559-postmerge-validation` | `d6f82591ba1de7ce5e36a08a10dc432118a31b82` | reflog ~30d / `--backup-bundle` |
| `ciinv` | `2c280ab9793699b83d8aa3e619516e9cec561d0b` | reflog ~30d / `--backup-bundle` |
| `cleanup-tmp-1083` | `5a62181f1ad618774730aa86def3df4b0c194245` | reflog ~30d / `--backup-bundle` |
| `cleanup-tmp-1112` | `88fb90d5e22eebba00524c9768a2b87858efd453` | reflog ~30d / `--backup-bundle` |
| `cleanup-tmp-1119` | `fb59df9033574d7713abaae65484bc695745219f` | reflog ~30d / `--backup-bundle` |
| `cleanup-tmp-1124` | `292a965ad3b8241a7efe911c63ec03f477b179f4` | reflog ~30d / `--backup-bundle` |
| `cleanup-tmp-1125` | `422b69ddd98c83bc69b60f36efc198e78b3993af` | reflog ~30d / `--backup-bundle` |
| `cleanup-tmp-1126` | `51671e35dc8ceb0d2d8c8b5e1631da28dba9fc11` | reflog ~30d / `--backup-bundle` |
| `cleanup-tmp-1148` | `14c4c496cdf54e0656e5168079befb2c9f6e7151` | reflog ~30d / `--backup-bundle` |
| `cleanup-tmp-1786630876` | `da0fcc11d75aa83235edd49527e2f46052108aa4` | reflog ~30d / `--backup-bundle` |
| `cleanup-tmp-1786630879` | `914be0b28ae16ffa7d24f660c60abcae8692a905` | reflog ~30d / `--backup-bundle` |
| `docs/1416-verdict-report` | `9ed905892419baa118260a975cfec0a325c28a5d` | reflog ~30d / `--backup-bundle` |
| `docs/2026-08-15-scoping-plans` | `640dab22a0240602ee0df88dbe2a077dcc2d7172` | reflog ~30d / `--backup-bundle` |
| `docs/2421-supersede-restatement` | `9a0a829ee256698ed5e4d8e07eb492e52c6ba158` | reflog ~30d / `--backup-bundle` |
| `docs/2779-org-name-scoping` | `48a22bada527676508675859a7258f138f3cf392` | reflog ~30d / `--backup-bundle` |
| `docs/2784-per-graph-backups-brief` | `ed27f553c8ecf994b39084b18e858cb435a34881` | reflog ~30d / `--backup-bundle` |
| `docs/28-adr-008` | `8aee29e4e66bd7c12168800ecbe352df8abaf620` | reflog ~30d / `--backup-bundle` |
| `docs/2854-scope-semantics` | `0df8625ef18ff9ba04ea74177fc659b2f5f51c20` | reflog ~30d / `--backup-bundle` |
| `docs/3327-record-trigger-population` | `3f5c449bc57f3903e1c20bdc0242da5a5c93c2b6` | reflog ~30d / `--backup-bundle` |
| `docs/3327-sweep-trigger-population` | `7c3d284472103f5383bd4b3145b6ca8c58ecf8ab` | reflog ~30d / `--backup-bundle` |
| `docs/4293-runbook-probe-host` | `e6a4b9408b1a1656687999af1318e276d1d59613` | reflog ~30d / `--backup-bundle` |
| `docs/500q-strong-reader-config` | `eda20ecf262acbd0b6f48025d19ff9e71d5fef31` | reflog ~30d / `--backup-bundle` |
| `docs/549-epic-docs-merge` | `bcdb45f95f132d50b3c213eddf906ef1d8a2d807` | reflog ~30d / `--backup-bundle` |
| `docs/557-scoping-research` | `ee676e3e0eda274a03776485900f1832a7ffb7c5` | reflog ~30d / `--backup-bundle` |
| `docs/565-ontology-cascade` | `4c988746078b09233a1795579ef0bb7aba814850` | reflog ~30d / `--backup-bundle` |
| `docs/566-deploy-runbook` | `be47daac5e874d27ed3724cf7b2d9a73b2746da9` | reflog ~30d / `--backup-bundle` |
| `docs/598-prior-art-screening` | `4bae889d1595be311d9c011b507adbad62383593` | reflog ~30d / `--backup-bundle` |
| `docs/703-quickstart-docs` | `b2fe9772265cdc391777568ba3c1236e5aeffa9e` | reflog ~30d / `--backup-bundle` |
| `docs/ai-review-gate-e2e` | `d5401e50568d09f2e2967125380d82de1c3c0e99` | reflog ~30d / `--backup-bundle` |
| `docs/batch-bug-hunt-report` | `f36b337da4a10598a56c674c2347b92cb33ab1a5` | reflog ~30d / `--backup-bundle` |
| `docs/blog-epic-status` | `7d6e60c128ecd0381776bedea2a9e95e570c1ea9` | reflog ~30d / `--backup-bundle` |
| `docs/calibration-specs` | `9dd6fd4280a60c3d0164d3e1ecfd7a11823ce978` | reflog ~30d / `--backup-bundle` |
| `docs/ci-process-research` | `8748991decb27d7f07866e9f0bb8bce4d6e33e69` | reflog ~30d / `--backup-bundle` |
| `docs/epic909-docs` | `1b5f0d53a006b140cfab6c337c1cd70eee571421` | reflog ~30d / `--backup-bundle` |
| `docs/epic909-plan` | `ea9be5465d67ae9f110e6785fe9f171a634c09e5` | reflog ~30d / `--backup-bundle` |
| `docs/extraction-state-centric` | `0b11d8402560a65bda74c90d5432cf44309c31c6` | reflog ~30d / `--backup-bundle` |
| `docs/fix-deploy-token-scope` | `792965b7ad6708ae0609c47281b8f9e56f6bf847` | reflog ~30d / `--backup-bundle` |
| `docs/pointkinds-statement` | `a7a4b0e77488bd048513ae3017fbeb52e051a540` | reflog ~30d / `--backup-bundle` |
| `docs/scoping-2304` | `c992583b3f056e5dde4e6b8275a5c706897a2fce` | reflog ~30d / `--backup-bundle` |
| `docs/standard-harness-reuse-audit` | `8618ffa85e80d074ad51d56510c15c81bbc1137d` | reflog ~30d / `--backup-bundle` |
| `docs/state-centric-memory` | `c5320e7d0eaef1212128bdd997509e44189636c2` | reflog ~30d / `--backup-bundle` |
| `docs/state-centric-ontology` | `078651e1bf5db63e2ef9897d63fe9688d0dde888` | reflog ~30d / `--backup-bundle` |
| `docs/tortoise-blog-cms-planning` | `bdbca67f985e9ea7c6c74952823d7a1759f74994` | reflog ~30d / `--backup-bundle` |
| `epic/264-insight-mining` | `87fef08670ea58510298f7b3510b67c02c8ff555` | reflog ~30d / `--backup-bundle` |
| `epic/test-db-migration` | `a78bcac000814a89c8aebf16470c432176d4bdeb` | reflog ~30d / `--backup-bundle` |
| `feat-1503-m` | `23c26746d33a61e16a56d567b3aaa6cc17228be2` | reflog ~30d / `--backup-bundle` |
| `feat/1021-tiered-selection` | `486ad04188f854344f8f25ac48dbc4a86f3a36c8` | reflog ~30d / `--backup-bundle` |
| `feat/1081-abuse-protection` | `da0fcc11d75aa83235edd49527e2f46052108aa4` | reflog ~30d / `--backup-bundle` |
| `feat/1082-claim-path` | `914be0b28ae16ffa7d24f660c60abcae8692a905` | reflog ~30d / `--backup-bundle` |
| `feat/1083-login-routing` | `5a62181f1ad618774730aa86def3df4b0c194245` | reflog ~30d / `--backup-bundle` |
| `feat/1112-dashboard-ci` | `88fb90d5e22eebba00524c9768a2b87858efd453` | reflog ~30d / `--backup-bundle` |
| `feat/1144-longmemevl-runner` | `03fa1b40d5cc0affa84bf0866604856d52c5771a` | reflog ~30d / `--backup-bundle` |
| `feat/1144-retrieval-eval` | `938762a883d10293bd1fcaab62d3a26e22402c7a` | reflog ~30d / `--backup-bundle` |
| `feat/1148-protect-account` | `a59ac9b23c7d2de4d1779dae882471dd722e78df` | reflog ~30d / `--backup-bundle` |
| `feat/1163-ep-dirty-persist` | `f5fc0fd0728c84ad1fbd75eaee274974ff1e6813` | reflog ~30d / `--backup-bundle` |
| `feat/1177-307-invite-accept-email` | `b642261cd72e8f6195ee0d903c8b5b36deea0ad3` | reflog ~30d / `--backup-bundle` |
| `feat/1221-email-integration-test` | `f3d7615d0799fc874f58b850a51e151dec6b761f` | reflog ~30d / `--backup-bundle` |
| `feat/1239-epic903-diagnostics` | `6edb11ef04f8153c7ffa2fb8c27162d011a13d79` | reflog ~30d / `--backup-bundle` |
| `feat/1240-epic903-freshness` | `8203d038bb792670faa3106d7bf1119ab4616bee` | reflog ~30d / `--backup-bundle` |
| `feat/1241-epic903-scheduler` | `54377e30aadf9cde541fe437fbd8b66ae9a28211` | reflog ~30d / `--backup-bundle` |
| `feat/1242-epic903-warmstart` | `d49af6d9326d54c17aa15e5c277c6d013dc0f6c3` | reflog ~30d / `--backup-bundle` |
| `feat/1243-epic903-retention` | `de63580ec9b4b810c3e97da30d227b02b68d6fac` | reflog ~30d / `--backup-bundle` |
| `feat/1244-epic903-moderouter` | `6de10266e37711e3e2134ffb13a1e7ecbfb7e26c` | reflog ~30d / `--backup-bundle` |
| `feat/1245-epic903-observability` | `48735a51d8dc1914f84b5efa7070c2ffcd2b2464` | reflog ~30d / `--backup-bundle` |
| `feat/1246-epic903-hosted` | `5ae94f364e85ec9b23f0ee93a893598c66b222c5` | reflog ~30d / `--backup-bundle` |
| `feat/1247-epic903-lifecycle` | `6c1d8e9add71b4e5d0f346641c7525bc59533379` | reflog ~30d / `--backup-bundle` |
| `feat/1248-epic903-staleness-eval` | `0244f41fbeff0d3ffa241b22bde467d3c5638814` | reflog ~30d / `--backup-bundle` |
| `feat/1249-epic903-mcp` | `6ac469040f3c4ed00f6060f862a1616930b97d05` | reflog ~30d / `--backup-bundle` |
| `feat/1250-epic903-fixtures` | `7c7a77a9ff705fd00d9931a346995ca2187a12d5` | reflog ~30d / `--backup-bundle` |
| `feat/1254-epic903-capstone` | `b69e6a5cf73a8c9ecce8f8d99edd24b7f6539068` | reflog ~30d / `--backup-bundle` |
| `feat/1272-calibrate-prompts` | `a32ab597a8223833c64873fe042238d192644cf6` | reflog ~30d / `--backup-bundle` |
| `feat/1272-exec` | `044400c95c5d5ba474c5160731ba46ff2064de9a` | reflog ~30d / `--backup-bundle` |
| `feat/1272-parity` | `ca34761f73abd6ce30f07b575e9d03ebe7fa4273` | reflog ~30d / `--backup-bundle` |
| `feat/1272-present` | `47bf4aae7a6d7561b7accf3759e177a35775f781` | reflog ~30d / `--backup-bundle` |
| `feat/1287-auth-v4` | `4946ca1f7ad9eb50293df25b0f930953723604ea` | reflog ~30d / `--backup-bundle` |
| `feat/1287-auth-v5` | `be05757e6da8f7f9bc2bb3025fa87eaa610611de` | reflog ~30d / `--backup-bundle` |
| `feat/1348-deeper-candidate-pool` | `9b2279dedf4ac04be1acb7ee4e0633002d0190c9` | reflog ~30d / `--backup-bundle` |
| `feat/1349-embedder-swap` | `b63b17f1774ef8a147e2baec13dfc472628c795e` | reflog ~30d / `--backup-bundle` |
| `feat/1350-parity-fix` | `9668a549c67425059aa92b06e86a12d854958cdb` | reflog ~30d / `--backup-bundle` |
| `feat/1350-s3-chunk-fixes` | `039aae8e4e143258b009c49cf4e6baa504ca4ac1` | reflog ~30d / `--backup-bundle` |
| `feat/1350-status-projection` | `40ee815134401892cb691514356eee8d2f344385` | reflog ~30d / `--backup-bundle` |
| `feat/1353-relationships-decoration` | `f15cd62c624089dc88bb7031f4b789b6c9007186` | reflog ~30d / `--backup-bundle` |
| `feat/1369-lme-v2-ingest` | `40481c3e9b70a5ccc93e11f83a2d509b9e92b3d9` | reflog ~30d / `--backup-bundle` |
| `feat/1375-fallback-perf` | `f5f469eec6686708de04c65c90ad6e95dbe7db59` | reflog ~30d / `--backup-bundle` |
| `feat/1386-supersession` | `7cbd503d899eb7199048cedd54e5e40be676e8f6` | reflog ~30d / `--backup-bundle` |
| `feat/1391-read-filter` | `faadb379087fbd2cf35e2cf9def20714393799ac` | reflog ~30d / `--backup-bundle` |
| `feat/1395-routing-config` | `51f219822dea83c514a500c67be4db34c849026f` | reflog ~30d / `--backup-bundle` |
| `feat/1416-cli-executor-real` | `69188dab99a21762520cc010fd16fff9e4f176ef` | reflog ~30d / `--backup-bundle` |
| `feat/1416-run-main` | `1cefbf2ce2f11733a03170259cc8c7c394a3a66b` | reflog ~30d / `--backup-bundle` |
| `feat/1416-run-v2` | `be07ce3382b4ee331491f4007890cd9d888b5675` | reflog ~30d / `--backup-bundle` |
| `feat/1416-run-v3` | `3b6ee571add11cd76994fae8da2bfad7bf936114` | reflog ~30d / `--backup-bundle` |
| `feat/1416-run-v4` | `49314307046f250e087c5baf5cf7d46d015be9a4` | reflog ~30d / `--backup-bundle` |
| `feat/1418-object-event-slots` | `5027a2331e68695317a85abe6ab472f8282fda76` | reflog ~30d / `--backup-bundle` |
| `feat/1503-lint-config` | `a269b589b470910482030f420ce61dd4c381f029` | reflog ~30d / `--backup-bundle` |
| `feat/1528-stats` | `8824bc50349e0293830ed6e6cd85afc3d1ea1a15` | reflog ~30d / `--backup-bundle` |
| `feat/1529-failclosed-capture` | `0868358ee5d4013eac5ef7c322579e9025fc07bb` | reflog ~30d / `--backup-bundle` |
| `feat/1536-s4-merge` | `a75a3a51a2adf8edcd7ba73e880bdc96e23cbc8d` | reflog ~30d / `--backup-bundle` |
| `feat/1541-or-sparse` | `2b70893e330d513ca3e36e294a0aef0a056930b7` | reflog ~30d / `--backup-bundle` |
| `feat/1549-prompt-efficiency` | `cf42490ad0ef5da8c6acdf38093116ffcb9c31fe` | reflog ~30d / `--backup-bundle` |
| `feat/1549-run-protocol` | `c87907d6f37b5d46f4f716249cb8b948f5249c39` | reflog ~30d / `--backup-bundle` |
| `feat/1549-session-parallel` | `07c68dc88ae873d9e8f8eadda985bcb7bd98d0d3` | reflog ~30d / `--backup-bundle` |
| `feat/1566-welcome-in-app` | `5f220dc127f6408d79671e33b1ecddd467054161` | reflog ~30d / `--backup-bundle` |
| `feat/1567-dashboard-latency` | `e23aea94a621693b49eeaff16eee239bb9509eff` | reflog ~30d / `--backup-bundle` |
| `feat/1591-onboarding` | `bc4a960ec96c5720795e205ed8dc0b85859e0964` | reflog ~30d / `--backup-bundle` |
| `feat/160-hosted-embeddings` | `03b634081baefb88927bfc63c4d9e0e453e04566` | reflog ~30d / `--backup-bundle` |
| `feat/160-hosted-search-fts-vector` | `7393582b9b69f30bb17929f0fcb8b62bc4946288` | reflog ~30d / `--backup-bundle` |
| `feat/1623-billing` | `1e8413fd232c867bd890b2c90f0110a4989453f8` | reflog ~30d / `--backup-bundle` |
| `feat/1643-skills-install` | `f0244b7fe563d45944642e7ea36bb75291460bd1` | reflog ~30d / `--backup-bundle` |
| `feat/1643-skills-repo` | `aa82e25b177cd273e68030ec2407ce5899e1a49a` | reflog ~30d / `--backup-bundle` |
| `feat/1656-load-test` | `b61dddf0fc23a81ea5a276f1470f355e7fc0bdd8` | reflog ~30d / `--backup-bundle` |
| `feat/1656-load-test-v2` | `22dccaa91fda3ff91355c3462040d4f84299f70c` | reflog ~30d / `--backup-bundle` |
| `feat/1657-fusion-fix` | `b61dddf0fc23a81ea5a276f1470f355e7fc0bdd8` | reflog ~30d / `--backup-bundle` |
| `feat/1657-fusion-fix-v2` | `2ed1dd54bc88a7f0fcd7e96d6b74fa4c64e39c58` | reflog ~30d / `--backup-bundle` |
| `feat/1657-fusion-on` | `e65b1d9f83d8af50181042ce6f1091b329ae9026` | reflog ~30d / `--backup-bundle` |
| `feat/1660-onboarding-redesign` | `82a1ce28b641bfaea9b6e2c31873206c47a48b97` | reflog ~30d / `--backup-bundle` |
| `feat/1680-onboarding-polish` | `fa34f63e379c32b79ea2c83c5730f92bd879ad3c` | reflog ~30d / `--backup-bundle` |
| `feat/1680-setup-back` | `67e6732c7000f0d32b92c025c14637d328330dcd` | reflog ~30d / `--backup-bundle` |
| `feat/1680-setup-back2` | `4d281a16ca7c0b38ddf7519dd0c76e6e81410e54` | reflog ~30d / `--backup-bundle` |
| `feat/1685-ruff-baseline-drift` | `13612966b46892e913f01fd1a5e96e2dc49ba569` | reflog ~30d / `--backup-bundle` |
| `feat/1686-carveout-team-create-leak` | `dccc5584bb30b154cacadc4e038a4eef118e8214` | reflog ~30d / `--backup-bundle` |
| `feat/1698-welcome-e2e-email-visibility` | `2a421b633ea7e679a766f786e131b194d654c0ac` | reflog ~30d / `--backup-bundle` |
| `feat/1708-key-mint-idempotency` | `b5983bd4f5f875b60bc1c02ba073800d9f858c9f` | reflog ~30d / `--backup-bundle` |
| `feat/1709-signup-idempotency-recovery` | `8eb31d44bef86038bae54fe7a881a03bcd18dee5` | reflog ~30d / `--backup-bundle` |
| `feat/1714-memory-capture-onboarding` | `9e7c8c51409023671abbab61b229b29ad14c95e6` | reflog ~30d / `--backup-bundle` |
| `feat/1715-token-revoke` | `d622c8cc5e9f4889d2174b26d088d70fe656505c` | reflog ~30d / `--backup-bundle` |
| `feat/1725-slice0` | `404f99bf1794b6882e4d0d1325fac0929517c46e` | reflog ~30d / `--backup-bundle` |
| `feat/1726-docs` | `949a2b78007323985ffdb79b2bb5d2c7858f0541` | reflog ~30d / `--backup-bundle` |
| `feat/1748-onboarding-user-path` | `68762261dbd99c721b4f7848894ac9c295c301c3` | reflog ~30d / `--backup-bundle` |
| `feat/1763-answer-string-mark` | `5bda081e154bea52d79e0c43f9c81bc5e54ad277` | reflog ~30d / `--backup-bundle` |
| `feat/1793-blog-data` | `da3109cbc75dba3a5ecdc2590bdef592637a13a8` | reflog ~30d / `--backup-bundle` |
| `feat/1794-blog-render` | `9bb06a8202bc58cab923418ae658a04ef021ea84` | reflog ~30d / `--backup-bundle` |
| `feat/1795-agent-api` | `2caed1d378026622c527d1c03376ace20d2c9e8a` | reflog ~30d / `--backup-bundle` |
| `feat/1795-blog-agent-api` | `b03ae1f1dab7630abb58d4c8ef6e8aa578db0f1e` | reflog ~30d / `--backup-bundle` |
| `feat/1796-blog-seo` | `4caadbd66aa27f8fde9c5258b38e35bf70abe6a3` | reflog ~30d / `--backup-bundle` |
| `feat/1797-admin-gate` | `abc8b3ea2abe4dd33b35d0d45c2ef4408306e95f` | reflog ~30d / `--backup-bundle` |
| `feat/1798-admin-app` | `0d5c1dc67729ff5c9c6e4813be6e4d1f9e371573` | reflog ~30d / `--backup-bundle` |
| `feat/1799-blog-events` | `86df5f8eef347d96db11e4831380e149c5f9c7c7` | reflog ~30d / `--backup-bundle` |
| `feat/1800-blog-deploy` | `e056e21e344bca10accdc35a08667b0be5dd9a33` | reflog ~30d / `--backup-bundle` |
| `feat/1841-overview-skeleton` | `d755ce65925d4c6384aa36b76aea46a60d09b874` | reflog ~30d / `--backup-bundle` |
| `feat/1874-account-menu-restructure` | `af799110cc8a56eb3a02d0f70647ba832d579818` | reflog ~30d / `--backup-bundle` |
| `feat/1875-invite-pending` | `d778a7d13a13d0afa693a807b8b90482ff2e7571` | reflog ~30d / `--backup-bundle` |
| `feat/1876-billing-team-dropdown` | `86a78a298fc07abddac8b88ec7eb9d65a88858ba` | reflog ~30d / `--backup-bundle` |
| `feat/1877-create-team-entitlement` | `31b74d485062b61262321b8d9d4a9c71fa91aa04` | reflog ~30d / `--backup-bundle` |
| `feat/1893-source-scope-persist` | `9f886e3b779e885aef91e1e3229fdcc8a1d738e5` | reflog ~30d / `--backup-bundle` |
| `feat/1894-docs-memory-source-switch` | `e96504978121fae46f91bf2995a600c71108d5e4` | reflog ~30d / `--backup-bundle` |
| `feat/1895-repoll-cursor-advance` | `0dc1c1e15bff6fd7e7475f60d7194c57cfd826fc` | reflog ~30d / `--backup-bundle` |
| `feat/1896-fly-orphan-machine-guard` | `a754bf284bb5fb49a432cd48db00957183d76c78` | reflog ~30d / `--backup-bundle` |
| `feat/1929-pack-shipping` | `408c1a49c12369fe400287bbf2d14f8b78d101fb` | reflog ~30d / `--backup-bundle` |
| `feat/1930-packs-dir` | `a00f5acd038e1825407633691c2d20956a8ce1d7` | reflog ~30d / `--backup-bundle` |
| `feat/1932-pack-docs` | `b534e22821c4cb4a1f8028cdd4b80858c0c85991` | reflog ~30d / `--backup-bundle` |
| `feat/1933-agent-ops-pack` | `bbbeaeb6f4326dde09104cfc19890403315c3540` | reflog ~30d / `--backup-bundle` |
| `feat/1934-enforcement` | `32c830ee14028b8690cbd90614f96948b43f6a6f` | reflog ~30d / `--backup-bundle` |
| `feat/1935-hosted-packs` | `ef706f75e43328163bd5eb5140c9d967339d4b9f` | reflog ~30d / `--backup-bundle` |
| `feat/1936-pack-export` | `3bc74220864bfc0635253aa51e39563b8fb552ae` | reflog ~30d / `--backup-bundle` |
| `feat/1970-ci-debug` | `0b78df1b56c3e5b81d7e8769a3c9ead7baffb61d` | reflog ~30d / `--backup-bundle` |
| `feat/1970-main-hygiene` | `c841516f0c90ef6d41617783b6d7b6243685c31c` | reflog ~30d / `--backup-bundle` |
| `feat/1997-W1-onboarding` | `4cb7e6711ebec05265255f2170ad78640ebf62d4` | reflog ~30d / `--backup-bundle` |
| `feat/1998-W2-onboarding` | `9c63a9bf298964f5d80379b7ce1a951430da29c1` | reflog ~30d / `--backup-bundle` |
| `feat/1999-W3-onboarding` | `58e840e5219a5886f1978551b702d10fdc1954b1` | reflog ~30d / `--backup-bundle` |
| `feat/2000-W4-onboarding` | `06e34c0cd5db2ea7cb7a5489e16837ba39a19098` | reflog ~30d / `--backup-bundle` |
| `feat/2001-W5-onboarding` | `0f2365a79ea99c81f70cbcb7dde53980f5697ae6` | reflog ~30d / `--backup-bundle` |
| `feat/2002-W6-onboarding` | `4947b188eaf27cdbd87eea8fa165dafd75cdb5cb` | reflog ~30d / `--backup-bundle` |
| `feat/2004-W8-onboarding` | `9c34f2cd32eee2f0af6a6463138191a5deeceda3` | reflog ~30d / `--backup-bundle` |
| `feat/2005-W9-onboarding` | `e2959dbdcde15f22cd5ec252a7c2dea6e52d0b47` | reflog ~30d / `--backup-bundle` |
| `feat/2006-W11-onboarding` | `e2959dbdcde15f22cd5ec252a7c2dea6e52d0b47` | reflog ~30d / `--backup-bundle` |
| `feat/2007-W12-onboarding` | `e2959dbdcde15f22cd5ec252a7c2dea6e52d0b47` | reflog ~30d / `--backup-bundle` |
| `feat/2028-foreign-kinds` | `376f6e050a2a5ec4efb241b1eeb87eb3889a07bf` | reflog ~30d / `--backup-bundle` |
| `feat/2029-body-cap` | `5c8651a344e1a51e2480963f1d2acc331fbc8aaa` | reflog ~30d / `--backup-bundle` |
| `feat/2030-namespaced-enforcement` | `1f7400cb7e2ae12a02566bbe5acaa17eceb0a772` | reflog ~30d / `--backup-bundle` |
| `feat/2030b-pack-count-test` | `afb95481d5bbaade45953adbe4d1f2c23ed23c41` | reflog ~30d / `--backup-bundle` |
| `feat/2031-tenant-view` | `e476eeda790924b2664a4912efa04897912e4b24` | reflog ~30d / `--backup-bundle` |
| `feat/2032-body-sweep` | `0c88b73b608059114e48e608c7d46e81caefb999` | reflog ~30d / `--backup-bundle` |
| `feat/2038-pack-rate-limit` | `2085c753ba0eb3546253ff021784f86a08735d4f` | reflog ~30d / `--backup-bundle` |
| `feat/2039-backup-restore-guard` | `46fac62c76b493a5647e412f721d13eef54fdb96` | reflog ~30d / `--backup-bundle` |
| `feat/2040-ledger-order` | `841f2e1d5690e4b4f564cbd11b5622295a9c0bfd` | reflog ~30d / `--backup-bundle` |
| `feat/2052-reaper-sweep` | `8596791661efbed8ad811618b813d7fad5741097` | reflog ~30d / `--backup-bundle` |
| `feat/2069-reader-routing` | `0dde912a9ed6a6c6d9ff6ca055deb0f2bcbfce26` | reflog ~30d / `--backup-bundle` |
| `feat/2080-gbrain-plan` | `f2e8d95455f044b9d9b576f309601c6c500585a0` | reflog ~30d / `--backup-bundle` |
| `feat/2080-gbrain-scope` | `bf01b77377f9d6c3262e3f9fe0954622fea12cf7` | reflog ~30d / `--backup-bundle` |
| `feat/2080-gbrain-verify` | `2cff245f4551852510c4126f3226317d5de8c45a` | reflog ~30d / `--backup-bundle` |
| `feat/2080-seams-wave1` | `f22097920de9ee867d87d41c0cc73ab5c8e0debc` | reflog ~30d / `--backup-bundle` |
| `feat/2080-w1-learnings-map` | `a28fa8114f4e3f5ee53638f6a073fc8e30db1b31` | reflog ~30d / `--backup-bundle` |
| `feat/2080-w2a-planted-gold` | `c255926d3292d199c67878791876dfe184350491` | reflog ~30d / `--backup-bundle` |
| `feat/2080-w2b-benchmark-runner` | `cf4b964a500b6cf862f9064fecf06238734c9b5c` | reflog ~30d / `--backup-bundle` |
| `feat/2080-w3a-cat34-harness` | `5cb319866e668f7a34e2d6b326e06820a0214bfc` | reflog ~30d / `--backup-bundle` |
| `feat/2080-w3b-why-suite` | `e3e15bc3053d888298d4eb969cdce44e41689669` | reflog ~30d / `--backup-bundle` |
| `feat/2080-w4a-why-enrichment` | `19c87ae05826ccde90b90469b8d454593cf81bae` | reflog ~30d / `--backup-bundle` |
| `feat/2080-w4b-contested-score` | `8194cb4f523ce1dfa14da794f2b9b65799fe33a7` | reflog ~30d / `--backup-bundle` |
| `feat/2080-w4c-volunteer-context` | `40fcb34d4dd52a82104cfed0b3f70123bfa0405e` | reflog ~30d / `--backup-bundle` |
| `feat/2080-w5-ingestion-quality` | `afa0861839752dd963d30c81eac74243f4d63b6b` | reflog ~30d / `--backup-bundle` |
| `feat/2080-w5-phase-d` | `6c7f4fe6748ca7d7d78f57cd6bffadffab617d49` | reflog ~30d / `--backup-bundle` |
| `feat/2080-w5-phase-e` | `900022383ec341ec8b69a60daa18989ed7118bd4` | reflog ~30d / `--backup-bundle` |
| `feat/2080-w5-phase-f` | `174ad63074f894d7ec5c7f19bf0c1adb3aee0ec9` | reflog ~30d / `--backup-bundle` |
| `feat/2080-w5c-ep-ingest` | `6c32878a447fb0fa4bd688fc94daec724f06b2bb` | reflog ~30d / `--backup-bundle` |
| `feat/2080-w7b-comparison-docs` | `931c5c822b78834a0e3b6f11bd364fe8f52fb56b` | reflog ~30d / `--backup-bundle` |
| `feat/2083-multi-graph` | `deaf4835886d3783c1c4eb1b8fee15984e330df6` | reflog ~30d / `--backup-bundle` |
| `feat/2110-c1-dual-mode-graph-key-model` | `9911b0915f0b14dcc623b986dd53c3667bcf72e6` | reflog ~30d / `--backup-bundle` |
| `feat/2111-c2-provisioning-service` | `2e2c7275cb7fd8ae9c05c63e7fcd040403d9ec61` | reflog ~30d / `--backup-bundle` |
| `feat/2112-c3-key-lifecycle` | `9030ca848f9f8bff2edbefb15686fbfa525206d8` | reflog ~30d / `--backup-bundle` |
| `feat/2113-c4-acl-layer` | `182baf95d6fafc9804a16042336fbae45f6cb760` | reflog ~30d / `--backup-bundle` |
| `feat/2114-c5-tenancy-spine` | `adcb54317ec7180e9636b5dea159cabdf16af552` | reflog ~30d / `--backup-bundle` |
| `feat/2115-c6-delivery-shape-tenancy` | `2605983f979f039c7312788b1d634ed13ea0db1b` | reflog ~30d / `--backup-bundle` |
| `feat/2116-c7-dashboard-graphs` | `61f1d2e423bb302ff110139ef8d44358fbc0c42e` | reflog ~30d / `--backup-bundle` |
| `feat/2117-c8-migration-docs` | `04305e8926d36ade6c351e4d3a65df1830269018` | reflog ~30d / `--backup-bundle` |
| `feat/2118-capstone` | `107474199db96a761d92f08d46c2027da900b154` | reflog ~30d / `--backup-bundle` |
| `feat/2127-shared-fixture-helper` | `0d1565cdd0296f913c64239d3cf58d6d98070710` | reflog ~30d / `--backup-bundle` |
| `feat/2127-wave1b-migration` | `65bb7f186f918f491d1128f7bf74a2ed9ec1bd6f` | reflog ~30d / `--backup-bundle` |
| `feat/2127-wave2-migration` | `560ba26956fe1946934f25c74ccf25730600049f` | reflog ~30d / `--backup-bundle` |
| `feat/2127-wave3-tripwire` | `29826e4a255b28e17cfb4ff74c13a6efdec34743` | reflog ~30d / `--backup-bundle` |
| `feat/2164-capture-supersession-fold` | `b26c07a9b174eb6e7881dd729205a60501badbe7` | reflog ~30d / `--backup-bundle` |
| `feat/2167-browser-session-auth` | `4f602c49dcdf8df0c50527c96be5547558305c6d` | reflog ~30d / `--backup-bundle` |
| `feat/2178-keys-table-e2e` | `c71f4380af82bc163fb61bcc0f8f6fb32d789284` | reflog ~30d / `--backup-bundle` |
| `feat/2193-hosted-supersession-migration` | `68468746704345b21d760ce660d0680035263063` | reflog ~30d / `--backup-bundle` |
| `feat/2193-supersession-migration` | `2357b7d2945851e8a8bebd87cc514e01051b32ee` | reflog ~30d / `--backup-bundle` |
| `feat/2194-journal-objectregistered` | `6b0d12fc70920af43b757ac2e1969a66367a7160` | reflog ~30d / `--backup-bundle` |
| `feat/2229-rotate-held-key` | `99678088744228189105272fdcc2e861d42d4b25` | reflog ~30d / `--backup-bundle` |
| `feat/2242-cas-fold` | `884405328e57619303656c193678fc2f947e382c` | reflog ~30d / `--backup-bundle` |
| `feat/2249-supersession-order` | `b377dda11e3b04df246bad1601ceb6fa1dd5f960` | reflog ~30d / `--backup-bundle` |
| `feat/2284-battery-measurement-path` | `16c290a04732a68fe357561631c265f8b1dbaa10` | reflog ~30d / `--backup-bundle` |
| `feat/2284-executor-exposure` | `1db99413c80747257bff0ef35708d0db52bfa5c3` | reflog ~30d / `--backup-bundle` |
| `feat/2291-a4-ep-semantics` | `abb9644ad8fb37dc48cf22b3531915c39c76bb56` | reflog ~30d / `--backup-bundle` |
| `feat/2292-rubric-model-budget` | `1bc31d3101b9475778350cfd05fece2d0fe54854` | reflog ~30d / `--backup-bundle` |
| `feat/2295-subjectadded-journaling` | `362022cb458ac6881ce6661da4c34c6b460085a8` | reflog ~30d / `--backup-bundle` |
| `feat/2304-delete-trash` | `76ddfdba334e5a27585e495ea9f44921ce08ca50` | reflog ~30d / `--backup-bundle` |
| `feat/2313-per-graph-backups` | `c60e1609eccfd73b779e41752534b729c721cf2a` | reflog ~30d / `--backup-bundle` |
| `feat/235-hosted-onboarding-journey` | `d93359c5b728491a2e9a01535673d421f369b2af` | reflog ~30d / `--backup-bundle` |
| `feat/236-mcp-streamable-http` | `f86a068bc213d8cf8865042a87a2be5d09cd0c7d` | reflog ~30d / `--backup-bundle` |
| `feat/2360-real-starter-data` | `0355b79fa76f1b71698a9efae95ab137d8e7a5a0` | reflog ~30d / `--backup-bundle` |
| `feat/2380-session-key-recovery` | `c200cf3a3daa4f275ce62f08a7504f3d774369fa` | reflog ~30d / `--backup-bundle` |
| `feat/2406-signup-onboarding-email` | `e8ebe22a5b424b159a95e5b34efb76b7f1c4100a` | reflog ~30d / `--backup-bundle` |
| `feat/2407-fork-unsure-option` | `2f89314781e3b9e8500d5c1e3316aa7cdf244f2f` | reflog ~30d / `--backup-bundle` |
| `feat/2408-s4-reemit-census` | `8b170976835c3afdd471d02c30bfc7114a0ff833` | reflog ~30d / `--backup-bundle` |
| `feat/2426-key-expiry` | `1fbdbed97cde9191317fd2aab523234fce87ead4` | reflog ~30d / `--backup-bundle` |
| `feat/243-search-sessions-temporal` | `616412543e579cf5482ad0c0fb2a7a0a63f90b67` | reflog ~30d / `--backup-bundle` |
| `feat/2437-contribution-policy` | `a87fadc62703278c0e6f337c8d09c6109e5fbab5` | reflog ~30d / `--backup-bundle` |
| `feat/2439-inbound-intake-store` | `5f3be4e1334bb903108d4183a3ee4cb539bbbbf8` | reflog ~30d / `--backup-bundle` |
| `feat/244-session-semantic-search` | `de2f42740e819b12916e83bbb3e371f78632a6ff` | reflog ~30d / `--backup-bundle` |
| `feat/2479-re-auth-fix` | `dcc4c0d36ee4983dbbfd22afea0ea30aa76281a8` | reflog ~30d / `--backup-bundle` |
| `feat/2479-re-auth-ux-investigation` | `ce6ccae83ae74582856a8dcda10df9fa986c1127` | reflog ~30d / `--backup-bundle` |
| `feat/2494-account-menu-sections` | `1c7e8dda0a8be97eceedf486244289f87483a153` | reflog ~30d / `--backup-bundle` |
| `feat/25-graph-informed-ranking` | `d85d5c2006c25deba4de6bae40f7638aa000b49e` | reflog ~30d / `--backup-bundle` |
| `feat/2523-diff-profile-contract` | `05388c91c03d429bd8c81f4b62015d410ae7ced5` | reflog ~30d / `--backup-bundle` |
| `feat/2688-dashboard-deploy-verification` | `1354a493074c7b349fa4ec1b61ff1bd5cefd1a25` | reflog ~30d / `--backup-bundle` |
| `feat/2740-r1-derive` | `5d6baea425ac8448245eb240dfd579cf215e086d` | reflog ~30d / `--backup-bundle` |
| `feat/2779-opaque-id-display-name` | `40a606c8f2bd03c1a317f0091b766ebbc3459d7b` | reflog ~30d / `--backup-bundle` |
| `feat/278-ollama-local-mode` | `c0856b6b0189a812ee69c07171987a76ba9b86b0` | reflog ~30d / `--backup-bundle` |
| `feat/2784-graphs-last-backup` | `a587badc8fd6f5baf090e8377a0514f50bb2d0d0` | reflog ~30d / `--backup-bundle` |
| `feat/280-index-concurrency` | `9d11ce69ccaf284f979d00eccb3a354a7c99996d` | reflog ~30d / `--backup-bundle` |
| `feat/2800-cr-tortoise-lane` | `4193388e5b462cdd9051dc7e2cc5eeeb711546e8` | reflog ~30d / `--backup-bundle` |
| `feat/2800-mabench-cr-runloop` | `b6bd51a96b1818d1ebfd413f820995d005eb5d7c` | reflog ~30d / `--backup-bundle` |
| `feat/2800-mabench-data-metric` | `26065873fcee66935e51dcb4b655dcbd463f8a83` | reflog ~30d / `--backup-bundle` |
| `feat/2800-parity-executors` | `183aeb8e78f8072fefa7795f6186bf231ec57b24` | reflog ~30d / `--backup-bundle` |
| `feat/281-instantiates-aboutobject` | `651d8f8494353524ce287c234f2e98adc46b15b7` | reflog ~30d / `--backup-bundle` |
| `feat/300-dashboard` | `7112c1df7ff9ce97f550aa608a115a39c3d0a68a` | reflog ~30d / `--backup-bundle` |
| `feat/303-ci-central` | `e8d1d69b84264fe7aefd10b4a3e7198d425b55d2` | reflog ~30d / `--backup-bundle` |
| `feat/303-e2e-suite` | `7b45ab2c4d3b0a008656fd405f3d306308f66d41` | reflog ~30d / `--backup-bundle` |
| `feat/304-hosted-cli` | `cb714e1b4603ff60d88c68af3c53afdf3c39cb8c` | reflog ~30d / `--backup-bundle` |
| `feat/308-abuse-prevention` | `708afce81b39568a261187faa5ea8d57df4e354c` | reflog ~30d / `--backup-bundle` |
| `feat/309-security-page` | `c3b1016a76548ca82fa04658d5834fda20753fc5` | reflog ~30d / `--backup-bundle` |
| `feat/312-capture-sdk` | `376ab29d1cc686a03257cc04cec191387632e382` | reflog ~30d / `--backup-bundle` |
| `feat/316-vector-benchmark` | `d9648ed82a6a2b8a6737a9d956aa330566a9adc5` | reflog ~30d / `--backup-bundle` |
| `feat/316-vector-benchmark-rebased` | `d9648ed82a6a2b8a6737a9d956aa330566a9adc5` | reflog ~30d / `--backup-bundle` |
| `feat/318-pack-isolation` | `1b6d16d829b82cba14bc84acc2e924408f4b64bd` | reflog ~30d / `--backup-bundle` |
| `feat/324-connector-secrets-encryption` | `dbb1572aa61db897beae44017579495a89d8a412` | reflog ~30d / `--backup-bundle` |
| `feat/326-ep-propagation` | `d7976cc04cf4ce96f779771273bca8be791c47d9` | reflog ~30d / `--backup-bundle` |
| `feat/327-db-indexes` | `b7e5741ca788a9b650db4be212a05eeac4754d6f` | reflog ~30d / `--backup-bundle` |
| `feat/329-security-hardening` | `1aa92b79e97e5a482129b72760c125df6e3e3818` | reflog ~30d / `--backup-bundle` |
| `feat/330-data-divergence` | `18a673d800566cc7853854109e6671e569c83fae` | reflog ~30d / `--backup-bundle` |
| `feat/334-wiring-phase01` | `d321348af81720db0b676e3b51ebcef6f858a05c` | reflog ~30d / `--backup-bundle` |
| `feat/338-service-model-v2` | `5d32820b99cd790a8ddc536ce543d11eb511a3b2` | reflog ~30d / `--backup-bundle` |
| `feat/341-ep-source-validation` | `941f5ab7180139df8816b6cdc5342c3771a64816` | reflog ~30d / `--backup-bundle` |
| `feat/344-calibration-default` | `e4f246b3203dd72842248a7fcbf1f1a7d74e596e` | reflog ~30d / `--backup-bundle` |
| `feat/348-audit-tool` | `9537f59053ebbd1e2e81b6d5dd3410fc0f0326ae` | reflog ~30d / `--backup-bundle` |
| `feat/373-waitlist-form` | `cb12210d8c90f5cc2e0cabb24e222a028a654b88` | reflog ~30d / `--backup-bundle` |
| `feat/3806-ship-test-instrument` | `c25999714b35ecc3108eb4746a37e1e12f99f0ad` | reflog ~30d / `--backup-bundle` |
| `feat/388-connector-source-nodes` | `65e7b1bb863d12255aa681c3bc1b8168fd0ae455` | reflog ~30d / `--backup-bundle` |
| `feat/395-local-ep` | `0b856004c12871348499a81f332e5aec7f33c2e4` | reflog ~30d / `--backup-bundle` |
| `feat/398-source-credibility` | `5db19cd3823069af3936f5a8b782139579570cbd` | reflog ~30d / `--backup-bundle` |
| `feat/399-embedding-matching` | `2a6fe4540e78997e83b244df05661cef6469d61c` | reflog ~30d / `--backup-bundle` |
| `feat/405-domain-constraints` | `ff1e836506598d1423fea09a7eccb57e9b24b4ee` | reflog ~30d / `--backup-bundle` |
| `feat/416-mining-pilot` | `4e2b9f4bb7f80479aa17a5fc905d43b0b69ef6f8` | reflog ~30d / `--backup-bundle` |
| `feat/4170-stable-tool-id` | `e7071f72040898e017a1f64650a413f873d4993b` | reflog ~30d / `--backup-bundle` |
| `feat/420-ep-validation` | `7521227df57a979bc78eb2816b6c38ca370172dd` | reflog ~30d / `--backup-bundle` |
| `feat/427-tortoise-planning-skill` | `b2fe9772265cdc391777568ba3c1236e5aeffa9e` | reflog ~30d / `--backup-bundle` |
| `feat/428-ops-safety-residual` | `8b0a77adcfcbe7b1c5347199a8d60561b53fe379` | reflog ~30d / `--backup-bundle` |
| `feat/4282-bridge-table` | `c9de1be02638f3b55242aecdbe870a5dc861e1b4` | reflog ~30d / `--backup-bundle` |
| `feat/438-candidate-exposure` | `2911919fadaec1bb0200a2eaa6d7890a4795293b` | reflog ~30d / `--backup-bundle` |
| `feat/454-canonical-tool-registry` | `860cc67ced3941d6e5a5825ceb082c591254bace` | reflog ~30d / `--backup-bundle` |
| `feat/485-sync-tagged-edges` | `ef138b954336c181560501a2cbe25e3e22769773` | reflog ~30d / `--backup-bundle` |
| `feat/497-onboarding-welcome-impl` | `1a929f6e622176213cd05b726174cf149f9c3889` | reflog ~30d / `--backup-bundle` |
| `feat/498-onboarding-api-impl` | `0ca9508f8774376ed0e9fbcc30905d1ecf5c44c0` | reflog ~30d / `--backup-bundle` |
| `feat/524-oauth-mcp` | `b28aae404a9db9727619d21e82952cd6869cc25a` | reflog ~30d / `--backup-bundle` |
| `feat/525-rest` | `149979fe63fe43af17634de61f7ce26b6ad6758c` | reflog ~30d / `--backup-bundle` |
| `feat/529-harness-onboarding` | `8cf753cf98cdd7ab73093d0886647e22ea78ab5b` | reflog ~30d / `--backup-bundle` |
| `feat/529-onboarding-variants` | `26f67ed2feb79f621bf773172683646cddf61311` | reflog ~30d / `--backup-bundle` |
| `feat/560-mcp-graph-ranking` | `1dc963dc4dc77b209f5a494872c00bac87a2a2fe` | reflog ~30d / `--backup-bundle` |
| `feat/564-session-end-hook` | `218c02df6bdbd292660351ee870b1c2fcbfa9b90` | reflog ~30d / `--backup-bundle` |
| `feat/568-decoupling-v2` | `3c5cce1ca4f98ff7464194f9fdef57788da39604` | reflog ~30d / `--backup-bundle` |
| `feat/569-provisioning` | `175b4cd8863050e7c26c78ee8f15d327c41ebf68` | reflog ~30d / `--backup-bundle` |
| `feat/570-session-key-v3` | `2df8188e9b47a7e230b77a47c7dc2b3ce2b102b1` | reflog ~30d / `--backup-bundle` |
| `feat/571-reveal` | `a782b24a3093c09f1d17d8902a15a45616be6e2d` | reflog ~30d / `--backup-bundle` |
| `feat/572-dashboard-auth` | `5fb4fe419d217227c4baf13481ecc1d1b023b32b` | reflog ~30d / `--backup-bundle` |
| `feat/573-onboarding` | `3701675af7b42abc0fe75cfb2eeca1b347196410` | reflog ~30d / `--backup-bundle` |
| `feat/573-onboarding-v2` | `1e23c30cf9e7d1ad2717e79387b8aafd3b25d6fc` | reflog ~30d / `--backup-bundle` |
| `feat/574-invites` | `f87dc3d6fdcfef14bb582fe90e6a0e3e6aaa5908` | reflog ~30d / `--backup-bundle` |
| `feat/575-pricing` | `ff4b8d4dcc8b991c1d426269a00c2cda1fa8a64c` | reflog ~30d / `--backup-bundle` |
| `feat/576-email-v2` | `1760362e0578ceb6f09dcb8aa3b874afd3b3bf36` | reflog ~30d / `--backup-bundle` |
| `feat/577-analytics` | `0c4b9e69bbe1f711d85dadae30bf85e643f837e4` | reflog ~30d / `--backup-bundle` |
| `feat/578-e2e` | `df7288a352a31ea15fd34f31f4550e84886b04a5` | reflog ~30d / `--backup-bundle` |
| `feat/592-topic-summarization` | `ea9cb64bc7a4cdd41848e31356cb590e5d83c3c7` | reflog ~30d / `--backup-bundle` |
| `feat/596-backup-cron-alerting` | `287f5341d2d9a202a162789c094940eb0bcba102` | reflog ~30d / `--backup-bundle` |
| `feat/655-team-backup-sweep` | `aa5829f37d1f091ccca2840edd6cc44acd1065ea` | reflog ~30d / `--backup-bundle` |
| `feat/663-zero-email-signup` | `b2fe9772265cdc391777568ba3c1236e5aeffa9e` | reflog ~30d / `--backup-bundle` |
| `feat/669-child-763-invitations` | `87cc0bdfb69c01d41ff65ebe8c2846134a6015bd` | reflog ~30d / `--backup-bundle` |
| `feat/669-child-764-onboarding-health` | `16728287ded60cff80e88ee1b93586caaaeca4e6` | reflog ~30d / `--backup-bundle` |
| `feat/669-child-765-writer-inventory` | `6a169e0708898115aea315d27ed0a95db8bb0c50` | reflog ~30d / `--backup-bundle` |
| `feat/669-child-766-postflip-verify` | `ea9eae9b4bc157156af58dbdaa74f601877773f4` | reflog ~30d / `--backup-bundle` |
| `feat/669-child-767-auth-flip` | `5e009572d8ba34de54f5f3415104dbc58b8a6f85` | reflog ~30d / `--backup-bundle` |
| `feat/669-child-768-backup-seam` | `de975962bf2c34e9808eeb9f7f4ea4b70a273854` | reflog ~30d / `--backup-bundle` |
| `feat/669-child-769-schema-migrations` | `93069732425fa80210492a3ec5860f39e4f47385` | reflog ~30d / `--backup-bundle` |
| `feat/673-telegram-alerts` | `e379b2ab782fcc1dad5879215a6fef451b9d4256` | reflog ~30d / `--backup-bundle` |
| `feat/681-usage-metering` | `f057c1f725fdf5825352fd424c7a891653dfb835` | reflog ~30d / `--backup-bundle` |
| `feat/683-enforce-limits` | `3deb6bff6597f85d56df2ae6bf3ad06ef0e06f7e` | reflog ~30d / `--backup-bundle` |
| `feat/683-max-limits-enforcement` | `b2fe9772265cdc391777568ba3c1236e5aeffa9e` | reflog ~30d / `--backup-bundle` |
| `feat/684-mcp-tier-limits` | `817ba31f7604e89bd3d0b5433af963a3604eeb0b` | reflog ~30d / `--backup-bundle` |
| `feat/688-eventlog-read-after` | `b2fe9772265cdc391777568ba3c1236e5aeffa9e` | reflog ~30d / `--backup-bundle` |
| `feat/688-eventlog-tail` | `64b9b4d65620639d6ec4bfdf8497e813f312fe7b` | reflog ~30d / `--backup-bundle` |
| `feat/692-event-replay` | `63264e7461e19db2dc4fd3dc798b0712a407172c` | reflog ~30d / `--backup-bundle` |
| `feat/705-onboard-embedded-db` | `e86a744efefd4dbb23ee477772ede90984d9a393` | reflog ~30d / `--backup-bundle` |
| `feat/706-redislite-message` | `bec2264a08a01bdc706207c07933da68356a6315` | reflog ~30d / `--backup-bundle` |
| `feat/714-dashboard-session-detail` | `80304b8fcff844eaa4726e3995513481aa374f95` | reflog ~30d / `--backup-bundle` |
| `feat/736-x-signup-event` | `85805106c6ca3681f30fd3720548c26cfc9d3979` | reflog ~30d / `--backup-bundle` |
| `feat/753-directed-nand` | `88a9ed5989f0636d07193ec3ce71ab4c9cd9aaa1` | reflog ~30d / `--backup-bundle` |
| `feat/833-mcp-mount` | `8f1cbca2d9cbcd6578d88d2eea35e9ed1cc0f0bc` | reflog ~30d / `--backup-bundle` |
| `feat/889-mcp-telemetry` | `369e98efdece81e42d4c96c24542448f4d005794` | reflog ~30d / `--backup-bundle` |
| `feat/900-index-workflow` | `3c7418533af6d7e47498ad4e721cb016bdaedc7c` | reflog ~30d / `--backup-bundle` |
| `feat/900-t1-file-indexer` | `187e31d8e4f9c09ec2c78bf733882b3a8fc921aa` | reflog ~30d / `--backup-bundle` |
| `feat/900-t10-ont-note` | `c57283599bee7b8c4712f40b928e3c367ecc9ea1` | reflog ~30d / `--backup-bundle` |
| `feat/900-t12-restore` | `f6a833ac54109d8bba505249345ed0c2e9ca336c` | reflog ~30d / `--backup-bundle` |
| `feat/900-t3-index-sdk` | `ae0a27ac15a4972a8e9d731e6c065f4c01abfe33` | reflog ~30d / `--backup-bundle` |
| `feat/900-t4-e2e-suite` | `e989217f644427f70ab4cede185fb62ec234eea5` | reflog ~30d / `--backup-bundle` |
| `feat/900-t5-surfacing` | `2fb026d2365e1cd1bca3fe9c5368d5930cf7c54d` | reflog ~30d / `--backup-bundle` |
| `feat/900-t9-sc4-markers` | `480d38075848c88740009177c71230d43254707b` | reflog ~30d / `--backup-bundle` |
| `feat/900-tci-registration` | `8e3fe8e8cd079368d8e5e36aee7986d529a84120` | reflog ~30d / `--backup-bundle` |
| `feat/900-tdocs` | `1b41de1e03438c46b0a037547825356def63b395` | reflog ~30d / `--backup-bundle` |
| `feat/901-connect-workflow` | `ea9be5465d67ae9f110e6785fe9f171a634c09e5` | reflog ~30d / `--backup-bundle` |
| `feat/902-a1-validation` | `1215e3c4b8ac8d6a128356e7180ec22f42fe1b45` | reflog ~30d / `--backup-bundle` |
| `feat/902-a10-rebuild-durability` | `70471b245a55afd90d3bee864e897342e192624b` | reflog ~30d / `--backup-bundle` |
| `feat/902-a2-failure-contract` | `b684f4991e355188ae3166e94f3f5fa4da121bc6` | reflog ~30d / `--backup-bundle` |
| `feat/902-a3-idempotency` | `1f62c46005e1742d334e3518c8b7376679fd54e3` | reflog ~30d / `--backup-bundle` |
| `feat/902-a4-batch-id` | `2ecf6c46179228aeceb4f530ec8d2a97a272032a` | reflog ~30d / `--backup-bundle` |
| `feat/902-ingest-workflow` | `3c7418533af6d7e47498ad4e721cb016bdaedc7c` | reflog ~30d / `--backup-bundle` |
| `feat/902-s8-direct-edge` | `e1940c22d536f7f16f46878c3d7ad97b6a6572f4` | reflog ~30d / `--backup-bundle` |
| `feat/903-diagnostics-prodscale` | `6cb7290ead5cb26b55c318584e0ecd6143d3ce23` | reflog ~30d / `--backup-bundle` |
| `feat/903-dreaming-ep` | `c1b0bca2f9b5055631c2451329032e289474d709` | reflog ~30d / `--backup-bundle` |
| `feat/946-gate-window2-rubric-validation` | `db9136d076561f2d6750cac282b1058390989da2` | reflog ~30d / `--backup-bundle` |
| `feat/946-validate-extractor` | `6a469206c19d957285c45684ac36312d8545ec25` | reflog ~30d / `--backup-bundle` |
| `feat/add-email-ui-fixes` | `29e1bb7b768555d476fdc60a55de3f13d3377fa0` | reflog ~30d / `--backup-bundle` |
| `feat/ai-review-gate` | `cc4944d39ef40ec08afd73e36e8262af31275559` | reflog ~30d / `--backup-bundle` |
| `feat/api-key-labels` | `91777e9ac755f371406ba0fac4e4bf224f08a4e2` | reflog ~30d / `--backup-bundle` |
| `feat/api-keys-ux-fixes` | `e2234e9d127705a5a607c8ec76ef0dc6792252a7` | reflog ~30d / `--backup-bundle` |
| `feat/beat-narrative` | `4e88d351749497dea5eab6644633c9e152661f22` | reflog ~30d / `--backup-bundle` |
| `feat/clean-profile-tab` | `87d8b86e82a4840d7ce9c233019e7be2d982ec75` | reflog ~30d / `--backup-bundle` |
| `feat/contestation-signal` | `95fb223cad4edfcffc1ea8c32161fbd5b2dabb0e` | reflog ~30d / `--backup-bundle` |
| `feat/dashboard-login-default-off` | `1c05905b2c6001a82144ee9ddee2d894f48c7a28` | reflog ~30d / `--backup-bundle` |
| `feat/doctor-path` | `fe8c8ce779bafb42644b00d2b1af656b07017fd7` | reflog ~30d / `--backup-bundle` |
| `feat/epic909-945-harness` | `2b87dbd96b1c6380824db5fb9507cc0c305ffce5` | reflog ~30d / `--backup-bundle` |
| `feat/epic909-947-quota` | `2e45bb6b308d768c591fa454daf4a242edb751bb` | reflog ~30d / `--backup-bundle` |
| `feat/epic909-948-ontology` | `9d09f7386079fab70c9ac57f9e1da79420aace9f` | reflog ~30d / `--backup-bundle` |
| `feat/epic909-950-pack-content` | `8e8284c18dc827b2576352f741c883c64fc2494f` | reflog ~30d / `--backup-bundle` |
| `feat/epic909-951-domain-loader` | `116b7b005004ca18ec47237db959a8326d121438` | reflog ~30d / `--backup-bundle` |
| `feat/epic909-952-commit-schema` | `d0fbca4276c44ce9c562e488999e28dc0a44abf4` | reflog ~30d / `--backup-bundle` |
| `feat/epic909-953-commit-endpoint` | `94affcdd2812d56be2ec6730750bff2c6c839462` | reflog ~30d / `--backup-bundle` |
| `feat/epic909-960-metrics` | `027e56db749935dead445afc8d82cb1381d27861` | reflog ~30d / `--backup-bundle` |
| `feat/epic909-calibration-tooling` | `54fd134bbb418ef17be8815b03a4d7a6f9215470` | reflog ~30d / `--backup-bundle` |
| `feat/epic909-graph-construct` | `407cd862ba6e279ceb85b257eb03352a1a3b2d17` | reflog ~30d / `--backup-bundle` |
| `feat/epic909-impl` | `661222d43d1d90fd5d25a936134ecd1e0338db35` | reflog ~30d / `--backup-bundle` |
| `feat/free-tier-headroom` | `dec6c1e7728bbc56c3b466f57a7df4cfd3ef248c` | reflog ~30d / `--backup-bundle` |
| `feat/identity-better-fallback` | `a58ffb3e389fcc8e3dd1c1927aa11a4aa6e4340f` | reflog ~30d / `--backup-bundle` |
| `feat/identity-email-column` | `6d51e82aa40d357d243a5252b894fe77ab80e5ef` | reflog ~30d / `--backup-bundle` |
| `feat/issue-284-entity-resolution-closeout` | `f8dd5dba0d84e8ef83ddcbc27268baad8d78d961` | reflog ~30d / `--backup-bundle` |
| `feat/key-modal-expiry-label` | `2840d48d6f07d3e488b7129bbc58ff660e56ff93` | reflog ~30d / `--backup-bundle` |
| `feat/seo-sitemap-consolidation` | `47fe3941458c56121365fd8d24becac642425b63` | reflog ~30d / `--backup-bundle` |
| `feat/welcome-path-chooser` | `21116dd665f7a516147364f057877b169b62db72` | reflog ~30d / `--backup-bundle` |
| `feat/welcome-segments` | `783aea1b5d3bb3afa8883f855343d6f04b806ce2` | reflog ~30d / `--backup-bundle` |
| `finish/2796-killswitch-loud` | `ceec08ad613f2fa9a5932ae626bb187d21b510a5` | reflog ~30d / `--backup-bundle` |
| `fix/0015-abuse-policy-idempotent` | `eefdfd89a59de69b54f6e133605c541d76652256` | reflog ~30d / `--backup-bundle` |
| `fix/1001-migration-consolidate` | `c46510dd8be06640de85654fa8d0aa42330bccd7` | reflog ~30d / `--backup-bundle` |
| `fix/1001-migration-renumber` | `4eb24ae588bf77bf6ba94bc3aaba49aa1c5853b6` | reflog ~30d / `--backup-bundle` |
| `fix/1002-cors-allowlist` | `8824f4af6c41f911b7cb1a0f891b7fa5ea0d0268` | reflog ~30d / `--backup-bundle` |
| `fix/1005-ci-orphan-gate` | `bb6a1f40a78614c41bf73d5e85e09806821e4e85` | reflog ~30d / `--backup-bundle` |
| `fix/1005-end-sweep` | `5c8bf89133886eae312be2bafe3e72994628942f` | reflog ~30d / `--backup-bundle` |
| `fix/1005-orphan-instrument` | `9992a7cac83a975d057afbc6bb6eb0aa2d0f8916` | reflog ~30d / `--backup-bundle` |
| `fix/1005-redislite-leak` | `af4fc8b5340e0c821c7b57ae0a5dd75cf80ee098` | reflog ~30d / `--backup-bundle` |
| `fix/1008-queue-health` | `104fda0da0608584f42d96caa204dda01df8d029` | reflog ~30d / `--backup-bundle` |
| `fix/1028-metering-seam` | `abf0918772c3fc9dcd64d632ec1960986802ab88` | reflog ~30d / `--backup-bundle` |
| `fix/1095-migration-drift-gate` | `2001e101b71bd15fae05f31421495062a23a307c` | reflog ~30d / `--backup-bundle` |
| `fix/1118-preflight-jq` | `292a965ad3b8241a7efe911c63ec03f477b179f4` | reflog ~30d / `--backup-bundle` |
| `fix/1118-wf-trigger` | `fb59df9033574d7713abaae65484bc695745219f` | reflog ~30d / `--backup-bundle` |
| `fix/1124-pages-perpage` | `422b69ddd98c83bc69b60f36efc198e78b3993af` | reflog ~30d / `--backup-bundle` |
| `fix/1125-perpage-clean` | `51671e35dc8ceb0d2d8c8b5e1631da28dba9fc11` | reflog ~30d / `--backup-bundle` |
| `fix/1135-welcome-url` | `89b2f0dbc7d9ec734c32cc552f273d6f3d592976` | reflog ~30d / `--backup-bundle` |
| `fix/1145-research-append-relative` | `8e40a5464b8b9a8ee88c9cf51464fac975c305e0` | reflog ~30d / `--backup-bundle` |
| `fix/1149-monitor-false-positive` | `da34856560ffe434eaecb146a74aa0eea7352853` | reflog ~30d / `--backup-bundle` |
| `fix/1155-gh-event-collision` | `e592f8a147bcde9796a36c7af5052670339341ab` | reflog ~30d / `--backup-bundle` |
| `fix/1158-audit-sourcekind` | `3bb9177a404c91b97280f11daf1fb4871c12bfc8` | reflog ~30d / `--backup-bundle` |
| `fix/1160-mining-dedup-docs` | `8e40a5464b8b9a8ee88c9cf51464fac975c305e0` | reflog ~30d / `--backup-bundle` |
| `fix/1162-add-operator-ep` | `007914d3d69534524fc989e742f0e6122f10a2a3` | reflog ~30d / `--backup-bundle` |
| `fix/1166-cli-serve-conflict` | `c705558569c802f8cccbc67e472c28dd8bc897b7` | reflog ~30d / `--backup-bundle` |
| `fix/1168-verify-chain-skill-sync` | `8e40a5464b8b9a8ee88c9cf51464fac975c305e0` | reflog ~30d / `--backup-bundle` |
| `fix/1189-welcome-e2e` | `1bdb8650d79f63bb489a3174984273bc3c608032` | reflog ~30d / `--backup-bundle` |
| `fix/1189-welcome-e2e-tests` | `f5cc7f6b5bfee323df12ddde38cb76bf64289e10` | reflog ~30d / `--backup-bundle` |
| `fix/1224-oauth-state-expiry` | `2bf2fd1d70928349205a64825994e16317c8f67c` | reflog ~30d / `--backup-bundle` |
| `fix/1227-final` | `4674ba9591a1082878c74609b7e6cff576fd358d` | reflog ~30d / `--backup-bundle` |
| `fix/1231-redislite-lifecycle` | `1c234e7dd790790b98ae1e1e0a5964da4cbf4a9d` | reflog ~30d / `--backup-bundle` |
| `fix/1235-migration-guards` | `760e48c1e2e59756ecc42635dab8024600f9b38b` | reflog ~30d / `--backup-bundle` |
| `fix/1266-suite-cap` | `2f72de11f7b2995749c1ae5b5857a4e69404684b` | reflog ~30d / `--backup-bundle` |
| `fix/1272-review-fixes` | `69989611542a1f17996696bd7722e2b748477816` | reflog ~30d / `--backup-bundle` |
| `fix/1280-dashboard-v2b` | `f63d37176dea4df2e662654582fdb72f29fc0737` | reflog ~30d / `--backup-bundle` |
| `fix/1302-rebuild` | `3637765530d81ac5e63e83c51134e3bcf461f6b3` | reflog ~30d / `--backup-bundle` |
| `fix/1359-falkordb-compat` | `d0866251f966aa6cc4138173b1456e5738719553` | reflog ~30d / `--backup-bundle` |
| `fix/1382-ep-local-env` | `8e4cc71c4947c21d6c569822bfccd3f1411a5d04` | reflog ~30d / `--backup-bundle` |
| `fix/1383-reaper-semantics` | `241562799a11cc93d887300875e19bc806cc6244` | reflog ~30d / `--backup-bundle` |
| `fix/1400-tool-registry-count` | `887567e70715e88e8e3ed197798896836ae6c48d` | reflog ~30d / `--backup-bundle` |
| `fix/1416-envelope-extract` | `e237a47d2ae78824860ece46886ea901b41849e9` | reflog ~30d / `--backup-bundle` |
| `fix/1416-envelope-typeerror` | `a801cc51d944900d53d881099bb60a85faf03675` | reflog ~30d / `--backup-bundle` |
| `fix/1416-episode-deadline` | `a0b09cfadb6d2aa4faa0533111e5e37f026d911d` | reflog ~30d / `--backup-bundle` |
| `fix/1416-real-episode-deadline` | `31e2fd3825ee833904592817624ecabb2af23b22` | reflog ~30d / `--backup-bundle` |
| `fix/1417-aboutEvent-untangle` | `09e4608a1f439f5daf46b67508bd4009d6da663f` | reflog ~30d / `--backup-bundle` |
| `fix/1422-bench-kwarg` | `e0f366a9deae58c6d2fa4b1d1dcc437900a2b177` | reflog ~30d / `--backup-bundle` |
| `fix/1427-orphan-reap` | `ebdab467f85d4bbda46e78f7ce7d79f7a3b293ec` | reflog ~30d / `--backup-bundle` |
| `fix/1438-postmerge-verdict` | `562a11b57378110b0e0e49e298af99ad3dcc26eb` | reflog ~30d / `--backup-bundle` |
| `fix/1439-postmerge-budget` | `3491a3723dd9aff984602797fdebe72394c42b7e` | reflog ~30d / `--backup-bundle` |
| `fix/1471-split-test-slow` | `a20c0f35bc0adc1a6fc9e096e6b550b7b674dfd0` | reflog ~30d / `--backup-bundle` |
| `fix/1472-single-manifest` | `03ec3925555dae3b5f688c43d3547685bbf76e48` | reflog ~30d / `--backup-bundle` |
| `fix/1473-tier2-duration` | `17a2dbcacb42b76f87c13c551adc941e14b1ba4d` | reflog ~30d / `--backup-bundle` |
| `fix/1474-postmerge-dedup` | `bc4ecc9734cdb97e555bb03cf9599e50d445fefe` | reflog ~30d / `--backup-bundle` |
| `fix/1475-lifecycle-finalize` | `e168eb420ddad16f06f59f38fc0e2c2dba4831bf` | reflog ~30d / `--backup-bundle` |
| `fix/1477-measure` | `c4c417b92791602c4c97de5881c3715f70b11ca3` | reflog ~30d / `--backup-bundle` |
| `fix/1490-signup-login-page` | `aa8cb64f3379b3fdc62c7d92591ba3788aeae6d2` | reflog ~30d / `--backup-bundle` |
| `fix/1498-apikey-label` | `422208847163a693a3f06eee3acdf35af4050715` | reflog ~30d / `--backup-bundle` |
| `fix/1498-auth-gating` | `f86bad27e4156ab8fadd6beca6d7319b8a460d1d` | reflog ~30d / `--backup-bundle` |
| `fix/1502-ci-red` | `9de006a1a2beb949b859e54fb62f45bc7fd70f6a` | reflog ~30d / `--backup-bundle` |
| `fix/1506-auth-gating` | `1ae193e71d5cd527fa8df8e84af670ff8d6b7067` | reflog ~30d / `--backup-bundle` |
| `fix/1549-pilot-extractor-reasoning` | `02127dc65700d592f1f02faad890c60a10e74770` | reflog ~30d / `--backup-bundle` |
| `fix/1559-session-mint-429` | `1dd014934b03241169715d8433db468e9973809c` | reflog ~30d / `--backup-bundle` |
| `fix/1566-e2e-redirect` | `a60b55e5e92d9fe65eb93f7b6d917d6d4d57c87a` | reflog ~30d / `--backup-bundle` |
| `fix/1566-recovery-cap` | `a10aaddf94dc039d37775da47b1ecce0e25c006d` | reflog ~30d / `--backup-bundle` |
| `fix/1591-cors-500` | `d334a5097bf0fbde0980f4eaca6ab653fbbf7158` | reflog ~30d / `--backup-bundle` |
| `fix/1591-graph-design` | `c0210b8ddaa4bef57ea37df4d1aefd2ae6590e32` | reflog ~30d / `--backup-bundle` |
| `fix/1591-graph-ux` | `50ceba43fa3de96b6a0b0d0dfdb5723b89a52b0b` | reflog ~30d / `--backup-bundle` |
| `fix/1591-team-500` | `b0fdc8d444463699ad4311506f022c85b8631733` | reflog ~30d / `--backup-bundle` |
| `fix/160-embeddings-cache-path` | `fe820a9ca3d37a040da953ada179b8a7c99ed8c8` | reflog ~30d / `--backup-bundle` |
| `fix/1643-card-design` | `d1c64451bc8bc72f6006df1d5b4cf71b45667a18` | reflog ~30d / `--backup-bundle` |
| `fix/1643-reentry` | `a59c39e29fe5b315219e14763f16260c66d21213` | reflog ~30d / `--backup-bundle` |
| `fix/1643-skills-copy` | `f79559942fc39d1e9c3e84de1a00a8f5374cab81` | reflog ~30d / `--backup-bundle` |
| `fix/1643-skills-primer` | `bf0075cbc3a3aebd34339108d22fa6c767f6794e` | reflog ~30d / `--backup-bundle` |
| `fix/1643-wizard-nav` | `3c390a6f0916ccf8f66efdfb5434c9feb15b6475` | reflog ~30d / `--backup-bundle` |
| `fix/1689-setup-visible` | `86d04ae20c8e626803ba76871423e857ad15d611` | reflog ~30d / `--backup-bundle` |
| `fix/1691-onboarding-copy` | `292c2263039940413e06f1aa0e0af3fbc07d3895` | reflog ~30d / `--backup-bundle` |
| `fix/1692-orient-order` | `938dd14896ccb630925109792d09353588c873c4` | reflog ~30d / `--backup-bundle` |
| `fix/1694-web-steps` | `0d2a058a8952eeae5fb55fec905fb7ded602facc` | reflog ~30d / `--backup-bundle` |
| `fix/1699-web-copy` | `9ebc30f92c5c3f3376056b91fb9172e8f2c676e4` | reflog ~30d / `--backup-bundle` |
| `fix/1701-consent-page` | `658aea18a43364dc48f9ca62395fc874beadf75d` | reflog ~30d / `--backup-bundle` |
| `fix/1704-cookie-session` | `2081be0aa93f444a7254348244f33140b3c8cb2a` | reflog ~30d / `--backup-bundle` |
| `fix/1710-team-create-phantom-key` | `1bcf7be577996a76f0f9105de73b1f2a3a7e028b` | reflog ~30d / `--backup-bundle` |
| `fix/1716-onboarding-orphan-key` | `1549456231d89c36d9b4813911da7280fc60d276` | reflog ~30d / `--backup-bundle` |
| `fix/1719-session-login-mint-guard` | `055a08bd8bca0ecb60ee6d1d3fe921e193ed27b2` | reflog ~30d / `--backup-bundle` |
| `fix/1721-asyncio-cascade` | `ba38fd8200c12efc0ac3bc0d63ecb7bcd6ba7059` | reflog ~30d / `--backup-bundle` |
| `fix/1737-uniform-503` | `5b113d998cebc853c7ce587d00dd22b39ed7a985` | reflog ~30d / `--backup-bundle` |
| `fix/1738-uuid-burst-copy` | `3488adbc8ceb7cc170a9043753e771428c6095fe` | reflog ~30d / `--backup-bundle` |
| `fix/1749-recover-api-url` | `68ceb9cf044df55b255978af5c1908f6461df67c` | reflog ~30d / `--backup-bundle` |
| `fix/1750-1751-signup-messaging` | `ee2b945a12724742326f9a82acb081bba8b20858` | reflog ~30d / `--backup-bundle` |
| `fix/1752-token-source-divergence` | `07319f621dc39ecb3f2d243685719106b04f72ad` | reflog ~30d / `--backup-bundle` |
| `fix/1753-1754-registry-parity` | `0cb000dbf423fc6f2aeb3abec790add8c31013c0` | reflog ~30d / `--backup-bundle` |
| `fix/1755-token-revoke-confirm` | `3760bb8fa4ee0c1a0e67941a0acfc883350c67fa` | reflog ~30d / `--backup-bundle` |
| `fix/1756-recover-guidance` | `af2c62775d0453a4286db95260e65241899728a7` | reflog ~30d / `--backup-bundle` |
| `fix/1781-lint-clean` | `79bcdf77a614bb69a9ce4549cf33b4f302a75864` | reflog ~30d / `--backup-bundle` |
| `fix/1822-admin-gate-supabase-auth` | `7f5fd0ee55c68e289f723c314b87b5ab246d4e90` | reflog ~30d / `--backup-bundle` |
| `fix/1826-oauth-redirect` | `260dfd4f4b6ec036af5220f2a122568218c4228a` | reflog ~30d / `--backup-bundle` |
| `fix/1828-session-key-deadlock` | `359c9057a34663850f5143f91fbccb26ea89c4d6` | reflog ~30d / `--backup-bundle` |
| `fix/1830-recovery-rotation` | `d8a84dd32edba3a33b1ef03bd1af7a08b38e77b3` | reflog ~30d / `--backup-bundle` |
| `fix/1832-session-failsoft` | `8d248b8a251ae70e73a5aedb9f7c07cd2e9081b4` | reflog ~30d / `--backup-bundle` |
| `fix/1834-import-columns` | `0392f9c3839ea9f5688890d1e4b77c9a5e60b297` | reflog ~30d / `--backup-bundle` |
| `fix/1835-google-cookie` | `c6624be91d4fcd2dd5e2daab0b2c0e02811c7342` | reflog ~30d / `--backup-bundle` |
| `fix/1838-onboarding-race` | `31d1574c206bfc756c8a0717301c4cf3fbf8d83d` | reflog ~30d / `--backup-bundle` |
| `fix/1844-object-only` | `5f53f8cc00df0a96d8f9705a3a1c9816735ed70b` | reflog ~30d / `--backup-bundle` |
| `fix/1845-source-scope-multiselect` | `7d6352742eaa79b19b7f6281c16b06c294c12e6a` | reflog ~30d / `--backup-bundle` |
| `fix/1847-memsources-refresh` | `c50ae9918ecd14b670d0760955c1925e913cb145` | reflog ~30d / `--backup-bundle` |
| `fix/1856-deadshell` | `e059c8038f271da07206718825c38f57cd55e192` | reflog ~30d / `--backup-bundle` |
| `fix/1880-ghost-members` | `54928a2b57a741cbf21f59a692ecaf94a46e93e4` | reflog ~30d / `--backup-bundle` |
| `fix/1885-gate-test-bootstrap` | `fe02ba793bf63740e35aad50bbdca2a59c419ebb` | reflog ~30d / `--backup-bundle` |
| `fix/1892-ci` | `241260903436e51fb3f2ddef468ed6a60f5dfb87` | reflog ~30d / `--backup-bundle` |
| `fix/1900-dataset-join` | `55ba3a415f9aadde7c8a130451a002e73f151a05` | reflog ~30d / `--backup-bundle` |
| `fix/1901-vacuity` | `b148980834212953e4ced9a5e946c6cfbe58e429` | reflog ~30d / `--backup-bundle` |
| `fix/1903-team-graph-name` | `b771b3bbf213cec540448e8c9836f1948de8bdd5` | reflog ~30d / `--backup-bundle` |
| `fix/1904-content-hash` | `3cc089a1d6298a337e9e62c824908e786724ab8c` | reflog ~30d / `--backup-bundle` |
| `fix/1906-welcome-dashboard` | `82e489bc29aaba05f02dd2b54652202af4230c29` | reflog ~30d / `--backup-bundle` |
| `fix/1908-ghost-expiry` | `2f677a4a35799b3f8a37d6fd444947453df872eb` | reflog ~30d / `--backup-bundle` |
| `fix/1909-oauth-fragment` | `b8ce5e5eabef955d2aff97739134fc547eca00f9` | reflog ~30d / `--backup-bundle` |
| `fix/191-audit-scripts-env` | `798cab091d77516db236ae851b39390b7476a61e` | reflog ~30d / `--backup-bundle` |
| `fix/1912-teams-suspension` | `0c2391a32f68913819e502f78a3895e218b64fbc` | reflog ~30d / `--backup-bundle` |
| `fix/1913-abuse-session-lane` | `6f0e4e4675ae77c94aef3f5476c86e111866c939` | reflog ~30d / `--backup-bundle` |
| `fix/1914-pagination-params` | `12a0ff13b152c9ecb8d02bb801e9f22d16dc365b` | reflog ~30d / `--backup-bundle` |
| `fix/1915-confidence-freshness` | `9005423f0dfa3306599a5b72c5c6b556e7d80f6c` | reflog ~30d / `--backup-bundle` |
| `fix/1917-input-edge` | `9a0702bfc37d9707745d03d990bee3b2cfad85d4` | reflog ~30d / `--backup-bundle` |
| `fix/1918-subject-canonical-id` | `b7a79aa3c7c91f4b119d6d264e07d2b076a81fbe` | reflog ~30d / `--backup-bundle` |
| `fix/1919-operator-dedup` | `283a6cee7cbcd3f50360f8de38509eea722c780d` | reflog ~30d / `--backup-bundle` |
| `fix/1927-consent` | `7349274cbfeae34608fb2a399eab1d07c3facc8e` | reflog ~30d / `--backup-bundle` |
| `fix/1928-hosted-e2e-consent` | `c215d0b66aea0028026115f14b4e673f3ffb0c0b` | reflog ~30d / `--backup-bundle` |
| `fix/1940-tracing-drift` | `07baa77ee908b7e3ac7243996c562787ee6c92a1` | reflog ~30d / `--backup-bundle` |
| `fix/1941-proxy-text` | `36de6520ca20b2c5a1af27c5eb3063676f5a7dbf` | reflog ~30d / `--backup-bundle` |
| `fix/1954-entitlement-toctou` | `30f3117117ac20ae3a5b2c899737968ecea625ee` | reflog ~30d / `--backup-bundle` |
| `fix/1965-invite-capacity-toctou` | `7fd4aa926d9fc1d61fe56d3cf148b6515f75b4b9` | reflog ~30d / `--backup-bundle` |
| `fix/1998-connect-durable-key` | `82c659205760f5dc336b265d63385e7f400cd891` | reflog ~30d / `--backup-bundle` |
| `fix/20260813000005-migration-prefix` | `5c6e30928ac8775e497c7c8f786f2bbca7e5571c` | reflog ~30d / `--backup-bundle` |
| `fix/2052-redislite-orphan-sweep` | `142c65f28da8bfcd2f6fe1c496c489785a2736b3` | reflog ~30d / `--backup-bundle` |
| `fix/2061-event-journaling` | `5667e5d96b046d588ec72b3b2d518fddc7e53141` | reflog ~30d / `--backup-bundle` |
| `fix/2062-ingest-event` | `47662051eb6c1fbaec630ad8dba5e0e20c41af61` | reflog ~30d / `--backup-bundle` |
| `fix/2065-flaky-pack-upload` | `cbb1e783959d4c41d5fa9c19681c11da2ca86f89` | reflog ~30d / `--backup-bundle` |
| `fix/2070-ask-retrieval` | `bdef11902409b64f520ad43b7df36595651da815` | reflog ~30d / `--backup-bundle` |
| `fix/2071-spotcheck-judge` | `8cb4b87627042a4c6029168279b3d04aaccbfe1b` | reflog ~30d / `--backup-bundle` |
| `fix/2084-uri-restore-guard` | `c422cd37e7103eb8a197279ec5c58d7f649625c1` | reflog ~30d / `--backup-bundle` |
| `fix/2085-decide-legacy-skip` | `1a7795a4716181c7e79334a80638aa0b01a6fb49` | reflog ~30d / `--backup-bundle` |
| `fix/2090-ci-lane-pollution` | `96e435213cf8241a289f470b10136acc35d6d947` | reflog ~30d / `--backup-bundle` |
| `fix/2104-audit-headers` | `3135806e8e2c4885131a6bd68886a43b0f9b26d8` | reflog ~30d / `--backup-bundle` |
| `fix/2134-reader-none-guard` | `e7395f6ff63ca03c9aeb4b81bf761ebb7f38943d` | reflog ~30d / `--backup-bundle` |
| `fix/2138-ruf100-cleanup` | `d5210c22e7311d438ed2d9b13a17519ce7dac4f1` | reflog ~30d / `--backup-bundle` |
| `fix/2147-2148-diff-gate-pythonci` | `799a4d1c21946e1b80342492dd5f6ee7b8d46c9e` | reflog ~30d / `--backup-bundle` |
| `fix/2149-ciyml-path-gates` | `4254448279d3a1ef0dba20021ce640c09e5312ae` | reflog ~30d / `--backup-bundle` |
| `fix/2151-mcp-lazy` | `11ad1baefa5c0bf272dc6b74e842d118baa6065b` | reflog ~30d / `--backup-bundle` |
| `fix/2155-w2a-lint-cleanup` | `78b2f820d08814de62d4108034cff0312a70973e` | reflog ~30d / `--backup-bundle` |
| `fix/2163-graph-drop` | `55639ae488c141eebe31ef6fd0527e80ed459c6c` | reflog ~30d / `--backup-bundle` |
| `fix/2172-anchor-race` | `3abe56e62ddef82d8ddba456aaf94d9c7276daf7` | reflog ~30d / `--backup-bundle` |
| `fix/2173-collect-strays` | `bf46d2bb45b44cc9c54005f13fd9d39dc95f1f74` | reflog ~30d / `--backup-bundle` |
| `fix/2174-postmerge-lint` | `df1e6d561390056ab5fa70fed5b1538e9e4bbdd8` | reflog ~30d / `--backup-bundle` |
| `fix/2179-keepalive-siblings` | `1955c740d22844e516b3e0c63e036d33521e89d5` | reflog ~30d / `--backup-bundle` |
| `fix/2188-d14-gate` | `0e1e495632e8c88d318f3103f0b2d47cb258424e` | reflog ~30d / `--backup-bundle` |
| `fix/2189-reconcile` | `2391b02aa2cdf3822817b82c3f3d974337c94b95` | reflog ~30d / `--backup-bundle` |
| `fix/2199-decide-calibration` | `10368f132eaac20a04fb95fdcf3a7c4623e77775` | reflog ~30d / `--backup-bundle` |
| `fix/22-sep-confidence-scale` | `c67ef79b5614b96301214c8ef791c20d47e8db81` | reflog ~30d / `--backup-bundle` |
| `fix/2200-selfhost-docker` | `02be6ec02cc2fc7d6182e2b019aa280fb7b0ca77` | reflog ~30d / `--backup-bundle` |
| `fix/2201-indexer-unreadable` | `a331daa14e4fdd17f0ee8f6d7c09304123930832` | reflog ~30d / `--backup-bundle` |
| `fix/2202-health-truthful` | `ebd1caebe931de3a56177a5917e5a970c2e3e6ab` | reflog ~30d / `--backup-bundle` |
| `fix/2203-orphan-redis-sigint` | `d6b54ea4000f7fdd260d67fb93a54ba2a2f8c86c` | reflog ~30d / `--backup-bundle` |
| `fix/2204-doctor-preinit-noise` | `28a29d7315377fbe6f57b85209e714c828121b42` | reflog ~30d / `--backup-bundle` |
| `fix/2205-summarize-counts` | `ad76da2fc561ea435d859c63f87355c020b826e2` | reflog ~30d / `--backup-bundle` |
| `fix/2206-confidence-surfaces` | `65e6d0767f1546864e2865023c4a4c73b65c9acc` | reflog ~30d / `--backup-bundle` |
| `fix/2207-extraction-digest` | `0102da8a8b155e772563ce3c13e726acbbd8012f` | reflog ~30d / `--backup-bundle` |
| `fix/2208-version-endpoint` | `2314b417219e1656cc23ccea58581f3494fd78de` | reflog ~30d / `--backup-bundle` |
| `fix/2209-session-hook-notice` | `c2b74c1d857c9bd9e6a74ab99c9fcdaa687af638` | reflog ~30d / `--backup-bundle` |
| `fix/2210-cli-mcp-polish` | `ebf136a6b90d3ecda053e1ac89348bffa86dbe8b` | reflog ~30d / `--backup-bundle` |
| `fix/2216-bgsave-fork-slot` | `0f97fb667f64784924bf2fb086f0e7f7345fe925` | reflog ~30d / `--backup-bundle` |
| `fix/2218-guard-os-import` | `4fd1cb2922d093d49773389e93ac93ea09c89f6e` | reflog ~30d / `--backup-bundle` |
| `fix/2235-dedup-syntax` | `81d580886242254124bb430086ca9fce2945c78c` | reflog ~30d / `--backup-bundle` |
| `fix/2251-embedded-path-divergence` | `56555b6d1169a83a34340dc2b881fa946a229a82` | reflog ~30d / `--backup-bundle` |
| `fix/2260-extraction-clean` | `bf6eb9db874c40e76e391b60e2835c0ae3b519f8` | reflog ~30d / `--backup-bundle` |
| `fix/2260-legacy-scope` | `6e320eed82973659bf79d71025790d7112909ce8` | reflog ~30d / `--backup-bundle` |
| `fix/2260-scope-extraction` | `ac721bd984d5cbda7bfaea02876638d305befd36` | reflog ~30d / `--backup-bundle` |
| `fix/2260-scope-followup` | `c2d16022ef29463848d775db7e65a006d73bec4f` | reflog ~30d / `--backup-bundle` |
| `fix/2280-ask-reader-collapse` | `35676f9efdce9df7fda00f228edcefa4acc7bc40` | reflog ~30d / `--backup-bundle` |
| `fix/2287-ci-volunteer-guards` | `cf546dfac5b9278830381b62f5a779156bf9d692` | reflog ~30d / `--backup-bundle` |
| `fix/2339-transport-stall` | `54a1c6f206e04dd501c90ee938cc4ddb38d3644e` | reflog ~30d / `--backup-bundle` |
| `fix/2361-vocab-anchor` | `aef112674d1fd714875e5a7cb2803e2171b3fae5` | reflog ~30d / `--backup-bundle` |
| `fix/2364-resume-round1` | `095d2387f648e9f30549744ff53c9c24ab42cb71` | reflog ~30d / `--backup-bundle` |
| `fix/2367-watcher-state-teams` | `c5b6630ec57ee34c1d1c44b25dcd6f840125c030` | reflog ~30d / `--backup-bundle` |
| `fix/2370-legacy-flat-index` | `1abd9185879848b07e7b6e2aa19f8a2d0fadc141` | reflog ~30d / `--backup-bundle` |
| `fix/2371-acl-reconcile-to-thread` | `adce257966f6e3f6093db512e1662ff556c19d67` | reflog ~30d / `--backup-bundle` |
| `fix/2372-sweep-rollup` | `67cecad9ee653890e8236e5e68c7e534a9f37fc7` | reflog ~30d / `--backup-bundle` |
| `fix/2373-retention-day-anchors` | `cfed5dacc8ef8f874e46fb99839d775d5d19caa6` | reflog ~30d / `--backup-bundle` |
| `fix/2374-pergraph-taxonomy` | `e2ba13b3c7dd575de03c7657b7cd78f28d0ca7d5` | reflog ~30d / `--backup-bundle` |
| `fix/2375-driver-subject-scope` | `4856746413cc4555e334862ad84a34fb9480aebc` | reflog ~30d / `--backup-bundle` |
| `fix/2376-create-graph-kind` | `e617fe7070516ed62500562ad2a3e608130ffaf2` | reflog ~30d / `--backup-bundle` |
| `fix/2377-rebaseline-validate` | `7972a6479996374927c6db219f2d60d13a5c98e8` | reflog ~30d / `--backup-bundle` |
| `fix/2378-docs-residuals` | `030bde2862694e7dbb3b8670b47f34d3cff6c722` | reflog ~30d / `--backup-bundle` |
| `fix/2391-team-to-org-sweep` | `31f114ae951696a8f846928d3f948761c8988014` | reflog ~30d / `--backup-bundle` |
| `fix/2392-dialog-focus-a11y` | `a9d2c7774a26499215f66956afb145ef2ec7860f` | reflog ~30d / `--backup-bundle` |
| `fix/2411-selfheal` | `8cd3318a788db5dc6509954ae5104cc3780f47f4` | reflog ~30d / `--backup-bundle` |
| `fix/2412-opstate` | `85c2d9166c624b3b861553f4047f2fc17ebb5f49` | reflog ~30d / `--backup-bundle` |
| `fix/2413-subjmatch` | `a20c545d7591a0028a0ee188b02b788ba4edd83c` | reflog ~30d / `--backup-bundle` |
| `fix/2414-keyderived` | `a41fe5e69b0e714e1ff07cdb1ac852ee21ec6f65` | reflog ~30d / `--backup-bundle` |
| `fix/2415-residual` | `56b9813779ea8a460e2a4d4b16cb3dbce948764d` | reflog ~30d / `--backup-bundle` |
| `fix/2422-ep-terminal-ghost` | `764325302cb264fac60492a0c0649f090913e85f` | reflog ~30d / `--backup-bundle` |
| `fix/2426-wizard-tdz` | `92a4c265b8642d6d4a3265cf09076fbdbccdea8d` | reflog ~30d / `--backup-bundle` |
| `fix/2450-judge-nonstring-gold` | `d25f1c63fb92948e5d56e32d6370210047351dc7` | reflog ~30d / `--backup-bundle` |
| `fix/2462-p1-flatpurge` | `dffd2862229fa7dfdf7972b39477e43bc71168e8` | reflog ~30d / `--backup-bundle` |
| `fix/2463-restore0row` | `66a6a3252e67effcc9ed4ab3d3b957d1d6997eb2` | reflog ~30d / `--backup-bundle` |
| `fix/2464-stampcond` | `9433af10a28ae699558ea6725e99a7e4448c224a` | reflog ~30d / `--backup-bundle` |
| `fix/2465-graceorigin` | `7d61325551287ed03a3cf2bf0faf48c330d661d5` | reflog ~30d / `--backup-bundle` |
| `fix/2466-indexrmw` | `5a86689f5f17282db0ee61b98965914cdd2c4144` | reflog ~30d / `--backup-bundle` |
| `fix/2467-quota` | `eaaec6e449576439ee76bf503c765b5c9f50f177` | reflog ~30d / `--backup-bundle` |
| `fix/2468-namerace` | `61f6d52a14514e9b563e381526492519c9ce97cc` | reflog ~30d / `--backup-bundle` |
| `fix/2469-inspect` | `e446467f86581c1b0ec9c7800e38a2131b6948a6` | reflog ~30d / `--backup-bundle` |
| `fix/2470-locktimeout` | `2b93542f18b28576784f0fbe6af1c8bd6fae7bdf` | reflog ~30d / `--backup-bundle` |
| `fix/2471-ghoststreak` | `e7d2d5e8d6e829cf3a2408f395151076b428dab1` | reflog ~30d / `--backup-bundle` |
| `fix/2494-account-menu-section-order` | `0f0ce32d75e1d600cf300af7bda14e009dcd38b5` | reflog ~30d / `--backup-bundle` |
| `fix/2494-logout-in-personal-section` | `4d41489f60ffa7e84f137c64ecabf5e7c61e5ae4` | reflog ~30d / `--backup-bundle` |
| `fix/2494-overview-layout` | `958d3a38b001d970b368a70156067f563ee1b8ae` | reflog ~30d / `--backup-bundle` |
| `fix/2559-reaud` | `97fcac586be638858ad96f763bc99a3255d6b2d9` | reflog ~30d / `--backup-bundle` |
| `fix/2560-reaud` | `dfac9c3a997bb765ebf1de847c5dbbbf1e945ee9` | reflog ~30d / `--backup-bundle` |
| `fix/2561-reaud` | `38c0b19efe88688c21b1c15b07447c11bb8bff62` | reflog ~30d / `--backup-bundle` |
| `fix/2562-reaud` | `625b1d0c0515f1da0d6d460bf7a68fe614d36ced` | reflog ~30d / `--backup-bundle` |
| `fix/2563-reaud` | `4f4330e13c27c82728d207110154c612d225a85e` | reflog ~30d / `--backup-bundle` |
| `fix/2564-reaud` | `23391d13def46f42fbbdfb510cd08fdf330d01e3` | reflog ~30d / `--backup-bundle` |
| `fix/2565-reaud` | `e1fbf8a400651ce4a33de7eae4a3e7a5b87b546a` | reflog ~30d / `--backup-bundle` |
| `fix/2566-reaud` | `8c2102ff00ab2e8970dad12fb9d95a15ce024291` | reflog ~30d / `--backup-bundle` |
| `fix/2601-judge-pair-distinct` | `b7c186ed28e860ce4dd61cd24bec60297583004d` | reflog ~30d / `--backup-bundle` |
| `fix/2633-real-vendor-key-preflight` | `342cda7645b5521f9d9cb48a8def2605b1117639` | reflog ~30d / `--backup-bundle` |
| `fix/2644-l4-flake` | `3bf46ef38c934317115e1be05da9c7ab4cabe63c` | reflog ~30d / `--backup-bundle` |
| `fix/2656-drift-gate-decouple` | `e2e468db1e0f34c1002038c63fe7f8367533bf5b` | reflog ~30d / `--backup-bundle` |
| `fix/2712-pin-preflight-test` | `c357886314f778b0d103e80fd490fc1351eee29e` | reflog ~30d / `--backup-bundle` |
| `fix/2735-e2e-residuals` | `d93d6e49ab2a22af72031f851879ccac98205966` | reflog ~30d / `--backup-bundle` |
| `fix/2759-authored-turn-refused` | `68791f365a5bc8f931b77d8146f3dd24a7c81786` | reflog ~30d / `--backup-bundle` |
| `fix/2764-lint-i001` | `690ace738294cb9a62dcd7e9bae17d6ba1ba7821` | reflog ~30d / `--backup-bundle` |
| `fix/2773-props-coercion` | `a71dc4756a5a944a8850b541ff94736f8ed6cb50` | reflog ~30d / `--backup-bundle` |
| `fix/2797-parity-not-measured` | `17f0e2c3313edf032d58cda6b3f6276ee7a03733` | reflog ~30d / `--backup-bundle` |
| `fix/2802-conflict-markers` | `9d08bbbbbf7475da947401e05b9a1e1ab7e78b8b` | reflog ~30d / `--backup-bundle` |
| `fix/2823-sweep-no-teams` | `f70bd439b19bd2b708c7c6b2b179cea6cb8f9c08` | reflog ~30d / `--backup-bundle` |
| `fix/2824-embed-state-tests` | `060176a30ee72f053cdfd144e05f8c8d195da2e4` | reflog ~30d / `--backup-bundle` |
| `fix/2827-wizard-connect-polish` | `b3f083631baeca4287aaaa96b5e7841cd4ead2ab` | reflog ~30d / `--backup-bundle` |
| `fix/2874-price-basis` | `a230291db1753ad1d9369d548c6a973fe531c7e3` | reflog ~30d / `--backup-bundle` |
| `fix/2906-provider-cost` | `8569204692e052698e49940a277320721cefeff7` | reflog ~30d / `--backup-bundle` |
| `fix/2906-spend-note` | `cea2783d03f461a967d77e4bba3e4de8c50b5ee0` | reflog ~30d / `--backup-bundle` |
| `fix/2913-register-insert` | `9d05b01f8f187915fc5d1048dec72604793bbd46` | reflog ~30d / `--backup-bundle` |
| `fix/2916-battery-surface` | `f5821f44a9e3f564020df33b43a69ecb1d8e210a` | reflog ~30d / `--backup-bundle` |
| `fix/2919-parity-detail` | `1d3c3f4887a184f40276b1a412876bd3350cc950` | reflog ~30d / `--backup-bundle` |
| `fix/2938-curate-surfaces` | `cd06d652a4c000ea08c20f30c7535a04556041bb` | reflog ~30d / `--backup-bundle` |
| `fix/2947-embedded-ingest-cache` | `18b521002a049a0b1254390aac3abf1b1054b2e5` | reflog ~30d / `--backup-bundle` |
| `fix/2947-orphan-investigation` | `e8ed2460f2ed24d319a71dd08d27aec8bccc181d` | reflog ~30d / `--backup-bundle` |
| `fix/2952-time-dependent-ranking` | `29eb3ad2782403eee964f70e3590891ee87fb65b` | reflog ~30d / `--backup-bundle` |
| `fix/2985-degrade-gate` | `e8ed2460f2ed24d319a71dd08d27aec8bccc181d` | reflog ~30d / `--backup-bundle` |
| `fix/3074-flaky-pointsmerged` | `6b23a798bab3b67d0be718531185e98226fcebea` | reflog ~30d / `--backup-bundle` |
| `fix/3076-review-gate-diagnosis` | `2bd1831cb24f0963ae89e70bbaa648b0f5028909` | reflog ~30d / `--backup-bundle` |
| `fix/3218-onboarding-wizard-copy` | `16cb039a2a23c3717b3209b69e5a0eaea21dc478` | reflog ~30d / `--backup-bundle` |
| `fix/3221-manifest-drift-both-files` | `d3ac78685a4d62b22375c49012fd46b5ec38dcde` | reflog ~30d / `--backup-bundle` |
| `fix/3261-3381-selection-manifest` | `6e5cafcc9b781d1a96fed4e75eaea67358fcacf4` | reflog ~30d / `--backup-bundle` |
| `fix/331-crash-batch` | `650082b891191032650fe555020b98d1cde60145` | reflog ~30d / `--backup-bundle` |
| `fix/3325-keyword-false-positive` | `7ac54318fcc78673826f5c11b33ccd10020a4c70` | reflog ~30d / `--backup-bundle` |
| `fix/338-contact-email` | `9627c4c41277415eb88ee7c324665cf3be00467a` | reflog ~30d / `--backup-bundle` |
| `fix/338-contact-url` | `9a7a36af510be7df061685f9479d322da3e889ea` | reflog ~30d / `--backup-bundle` |
| `fix/343-client-graceful` | `b2fe9772265cdc391777568ba3c1236e5aeffa9e` | reflog ~30d / `--backup-bundle` |
| `fix/343-client-graceful-degradation` | `d52b8551e641ff3a2c8037bd3c36b58c4be067b6` | reflog ~30d / `--backup-bundle` |
| `fix/3436-signup-duplicate-id` | `a94b272a771396663782dede28e2be647b58bcf1` | reflog ~30d / `--backup-bundle` |
| `fix/345-domain-vocabulary` | `b2fe9772265cdc391777568ba3c1236e5aeffa9e` | reflog ~30d / `--backup-bundle` |
| `fix/3474-api-domain` | `c7a669761a2a2b540743ffc34aabfd661b901215` | reflog ~30d / `--backup-bundle` |
| `fix/3498-control-plane-offloop` | `7e4dbc30758d4679abc96fc345df7c3e830b6dbc` | reflog ~30d / `--backup-bundle` |
| `fix/3503-session-fragment` | `fbab303fb23087853f372a00f77aa5ad8536146b` | reflog ~30d / `--backup-bundle` |
| `fix/356-ontology-endpoints` | `b2fe9772265cdc391777568ba3c1236e5aeffa9e` | reflog ~30d / `--backup-bundle` |
| `fix/3599-embedded-orphan-leak` | `8e43a5b2aa590db0ffd29cb7744c7de00141eafc` | reflog ~30d / `--backup-bundle` |
| `fix/3620-upload-root` | `d72a7000185924f98f2e78e0d049d54c0c29c24d` | reflog ~30d / `--backup-bundle` |
| `fix/3628-auth-watchdog` | `170367017c22f101279510bc715f1079711231f1` | reflog ~30d / `--backup-bundle` |
| `fix/3795-upgrade-path` | `8a2074578b8cb180ce17851bd91d6ca15ddcf952` | reflog ~30d / `--backup-bundle` |
| `fix/3863-mcp-sdk-surface-curation` | `eac93755771f4a3fdf9988acbb4444f1e1e30beb` | reflog ~30d / `--backup-bundle` |
| `fix/390-391-edge-guards` | `ff664f79b3f2c2d3152f374084ae66de3d15c43c` | reflog ~30d / `--backup-bundle` |
| `fix/3910-ask-spotcheck-capture-shape` | `3341d9a7cbdb4180470e3ddef3f7a477eeee9f51` | reflog ~30d / `--backup-bundle` |
| `fix/400-ep-nplus1` | `157e5073f875873d37a69fb2042d8755ddcdafdb` | reflog ~30d / `--backup-bundle` |
| `fix/407-skill-links` | `458ef8c4554647b1f55b681db366439f35a7de8a` | reflog ~30d / `--backup-bundle` |
| `fix/4098-tmpdir-hardening` | `75e27a39d880e8fd007aa20b62a4181abd9c06ac` | reflog ~30d / `--backup-bundle` |
| `fix/4113-capability-not-name-guards` | `007a9b3a02e9b2035db8e2b06b2b8ea0fd52fa38` | reflog ~30d / `--backup-bundle` |
| `fix/4163-stale-carveout-counts` | `0f9dcf8fc5c54305f4533fda3e0850b761817fe5` | reflog ~30d / `--backup-bundle` |
| `fix/4164-embedded-marker-selection` | `029d27cdf05ce395c09308040310e083209b8a79` | reflog ~30d / `--backup-bundle` |
| `fix/420-ep-validation-gaps` | `99e3aff35629ebc95688c5e16a1e8c0487a6c7a7` | reflog ~30d / `--backup-bundle` |
| `fix/420-quadrature-test-followup` | `14156468e413448a42409d4d8ff62cafa92e6bdb` | reflog ~30d / `--backup-bundle` |
| `fix/478-mcp-db-target` | `2333f1fdd84a149f5c751350d10f278474b147a6` | reflog ~30d / `--backup-bundle` |
| `fix/509-stale-search-tests` | `b2fe9772265cdc391777568ba3c1236e5aeffa9e` | reflog ~30d / `--backup-bundle` |
| `fix/522-embedded-is-operator-index` | `af147b178c41eb87db466a0319609a5d65a4a9d6` | reflog ~30d / `--backup-bundle` |
| `fix/527-provision-jwt` | `f2c674902bec3f9471f36c75f549593136c9f9c3` | reflog ~30d / `--backup-bundle` |
| `fix/527-signup-form` | `b2fe9772265cdc391777568ba3c1236e5aeffa9e` | reflog ~30d / `--backup-bundle` |
| `fix/529-mcp-url-trailing-slash` | `9006fbfd7f5eb3db94b78fe748b82f758fc727d0` | reflog ~30d / `--backup-bundle` |
| `fix/540-prompt-url` | `d5d0b4ff97403051de412d290b2520076646b06f` | reflog ~30d / `--backup-bundle` |
| `fix/541-e2e` | `2368cb19c9d60d0f34b1359524997d0e84346124` | reflog ~30d / `--backup-bundle` |
| `fix/542-oauth-secrets` | `a3917cb358c281b0f0d2bbc42dfbdf21ca628dca` | reflog ~30d / `--backup-bundle` |
| `fix/543-analytics` | `3d329a2607fb783efb08a2c53c4f43ffe7f05d47` | reflog ~30d / `--backup-bundle` |
| `fix/544-selfhost` | `0bc417987aa076e7ef8b34b2a6f9b1050b7f81f5` | reflog ~30d / `--backup-bundle` |
| `fix/545-deploy-pipeline` | `8dd2fd483d50594b78bd9eda145c0e048bf902b9` | reflog ~30d / `--backup-bundle` |
| `fix/545-deploy-secrets` | `e10bc4bd40ea66837ae73cbcea15fba633b2faf0` | reflog ~30d / `--backup-bundle` |
| `fix/545-deploy-serialize` | `146e1a63cd08caa79a5186e996634ede94537adb` | reflog ~30d / `--backup-bundle` |
| `fix/545-memory-bump` | `768dfdc41f6635c091dce96a8648d57d05e6703d` | reflog ~30d / `--backup-bundle` |
| `fix/545-prewarm-nonblocking` | `d53888e9cf833049be700c703e2f2e68ad4977c3` | reflog ~30d / `--backup-bundle` |
| `fix/545-pricing-image` | `200abf34bdc6f6f71f9e31ddf40c7db9563d3fb6` | reflog ~30d / `--backup-bundle` |
| `fix/547-supersede-validation` | `4c17616ffe0842b3dddd8d3e64c9a1d0130a8a1c` | reflog ~30d / `--backup-bundle` |
| `fix/548-sdk-jsonl-events` | `b97ba7c910a9949ee0ff2784e9085bd5069cf980` | reflog ~30d / `--backup-bundle` |
| `fix/555-ci` | `a9d6f4e010317a1084e4546eae219753c7d897c6` | reflog ~30d / `--backup-bundle` |
| `fix/561-timeout-nodiscard` | `cefa2a91169251bb1d2bb4e2a8f20b38d7b594f3` | reflog ~30d / `--backup-bundle` |
| `fix/647-suite-sweep` | `d7c175fd75c4fbd2c02d905663046f5283ece5ff` | reflog ~30d / `--backup-bundle` |
| `fix/647-test-suite` | `d3452cff570cb10637d06a3246ca45f40604f272` | reflog ~30d / `--backup-bundle` |
| `fix/651-nand-phi-docs` | `68063e690a7f7d273f03f163cf1abee37192fcfb` | reflog ~30d / `--backup-bundle` |
| `fix/652-evidence-revert-prior` | `86074649b7bff8a7baeb93d101795965fafb70bd` | reflog ~30d / `--backup-bundle` |
| `fix/652-source-inheritance-revert` | `b2fe9772265cdc391777568ba3c1236e5aeffa9e` | reflog ~30d / `--backup-bundle` |
| `fix/656-backup-tier-gate` | `70126c7438be535dfa9ab5626de2b5bf45dc40e4` | reflog ~30d / `--backup-bundle` |
| `fix/656-backup-tier-solo` | `b2fe9772265cdc391777568ba3c1236e5aeffa9e` | reflog ~30d / `--backup-bundle` |
| `fix/657-welcome-footer-layout` | `6c3842945871b5be727a5d39bff6b88d65f23385` | reflog ~30d / `--backup-bundle` |
| `fix/669-audit-dsn-live` | `90e112b652276ee4d3487e9b986bf25b24ab3450` | reflog ~30d / `--backup-bundle` |
| `fix/685-last-used-at` | `2c593c0f166a172901147f09104267970aed3632` | reflog ~30d / `--backup-bundle` |
| `fix/686-limit-failclosed` | `413da68d88f756772e0a3c5776ec01059d717c92` | reflog ~30d / `--backup-bundle` |
| `fix/686-team-limit-failclosed` | `b2fe9772265cdc391777568ba3c1236e5aeffa9e` | reflog ~30d / `--backup-bundle` |
| `fix/687-auth-key-scan` | `fe07ba11c0c7e66774e82de82ad0df9eebdd3053` | reflog ~30d / `--backup-bundle` |
| `fix/689-retract-tombstone` | `b2fe9772265cdc391777568ba3c1236e5aeffa9e` | reflog ~30d / `--backup-bundle` |
| `fix/689-retraction-tombstone` | `10f354b92d3e376692a3d4f83e60d4529d2a263b` | reflog ~30d / `--backup-bundle` |
| `fix/702-selfhosted-mcp-deadend` | `b2fe9772265cdc391777568ba3c1236e5aeffa9e` | reflog ~30d / `--backup-bundle` |
| `fix/705-onboard-embedded` | `b2fe9772265cdc391777568ba3c1236e5aeffa9e` | reflog ~30d / `--backup-bundle` |
| `fix/706-redislite-message` | `b2fe9772265cdc391777568ba3c1236e5aeffa9e` | reflog ~30d / `--backup-bundle` |
| `fix/707-init-api-key-validation` | `b2fe9772265cdc391777568ba3c1236e5aeffa9e` | reflog ~30d / `--backup-bundle` |
| `fix/713-index-github` | `3e19ff0ebb9555e973d6f310bec7a5c2f718e95e` | reflog ~30d / `--backup-bundle` |
| `fix/797-postmerge-validation` | `73ca177dbeba9d3c44c9b4e7138c42092a1b0360` | reflog ~30d / `--backup-bundle` |
| `fix/797-postmerge-validation-full` | `a687ec8d90dbbd7b06cf48ddcb92ceee4c77fb63` | reflog ~30d / `--backup-bundle` |
| `fix/798-python-ci` | `e45d4941d0d5388732af34360e93e7697623a743` | reflog ~30d / `--backup-bundle` |
| `fix/801-signup-email-ratelimit` | `c9b4334e96280b5e312524f6a5b93638f7f058f7` | reflog ~30d / `--backup-bundle` |
| `fix/802-provision-auth` | `751b0c34ad8df0016a4e2b4431dfa04938c524e7` | reflog ~30d / `--backup-bundle` |
| `fix/822-llm-extraction-default` | `d192c3bff9918b200d9cba72939b5548b573e872` | reflog ~30d / `--backup-bundle` |
| `fix/843-billing-routes` | `eecef0a6034ba0fac8498446d6a740decd25a3b3` | reflog ~30d / `--backup-bundle` |
| `fix/844-ep-directional` | `12ad42b63b6f861e956d842a4bef656b3ae2b6ad` | reflog ~30d / `--backup-bundle` |
| `fix/855-nand-propagation` | `0c976702f5fa9aa0f4a028d26c4031702d9701a2` | reflog ~30d / `--backup-bundle` |
| `fix/879-crash-test-rescope` | `2b319f1c1b8f5879316346b0f5f0cfefb8401438` | reflog ~30d / `--backup-bundle` |
| `fix/880-ci-green-rebalance` | `f48ac582e7cfcfeb841a5ace18f4d50436992d56` | reflog ~30d / `--backup-bundle` |
| `fix/880-semantic-dedup-degrade` | `22f37a6a0c2489152607587f3898f79b3b5eeec2` | reflog ~30d / `--backup-bundle` |
| `fix/881-hero-cta-pe-on` | `6d5146f7290386bc64dbad34b84387eef4cdd1c0` | reflog ~30d / `--backup-bundle` |
| `fix/885-server-auth-abuse` | `f01cbc6f77c26d94d2e992b0539f52e486d39c42` | reflog ~30d / `--backup-bundle` |
| `fix/914-required-checks` | `e587e5dbce1f3188a2be7cecf0e8c7146c7839a9` | reflog ~30d / `--backup-bundle` |
| `fix/915-rebase3-tmp` | `f5e39bf7c2a7427eea43d7304d7d45e60d17fcd6` | reflog ~30d / `--backup-bundle` |
| `fix/923-metering-degrade` | `a7e42e7a081e2d487eef273e7f78cc3ae2282c73` | reflog ~30d / `--backup-bundle` |
| `fix/924-backups-graph-name` | `c9202330b923c6ee27e6c65e4933a65d9a8cc274` | reflog ~30d / `--backup-bundle` |
| `fix/925-metering-readback` | `5b9435fea1e7e8bbff35d868e58ee7d7cf1560ae` | reflog ~30d / `--backup-bundle` |
| `fix/942-selfhost-trust` | `873dac023ad6096ddfe605f8ef49149d962ff832` | reflog ~30d / `--backup-bundle` |
| `fix/969-negative-leg-masking` | `70807249eadcec0518bb0726ce54f18c9855197f` | reflog ~30d / `--backup-bundle` |
| `fix/981-mcp-config-parity` | `5c1f066e1f491fa0b10f40350c47a2fabc1a8a53` | reflog ~30d / `--backup-bundle` |
| `fix/992-998-ep-confidence` | `a25e44b92c0145e8e71ca375dc161f33fe90941c` | reflog ~30d / `--backup-bundle` |
| `fix/992-ep-draft-tests` | `c59c891fb7ddd78c8773055d2a0c64db9debb96e` | reflog ~30d / `--backup-bundle` |
| `fix/993-mcp-entrypoint` | `09ef9e501990ed7e9106a8f9b64c3fde5385588a` | reflog ~30d / `--backup-bundle` |
| `fix/accent-colors` | `4247814e4b336577cf67ed331b6005e89c58bffb` | reflog ~30d / `--backup-bundle` |
| `fix/assert-cluster` | `e3c5a191fd71f449243eec421f6a820a9e852be5` | reflog ~30d / `--backup-bundle` |
| `fix/backend-batch` | `ab9f0f84d993931f16adcea4962c8cfddd2c5ba5` | reflog ~30d / `--backup-bundle` |
| `fix/backfill-embedded-gate` | `38a5329467a6c5674d35001ea6ba6dd418090099` | reflog ~30d / `--backup-bundle` |
| `fix/beat-background` | `4389afd7c180eccd7c28630f689d1d23f4a08a7f` | reflog ~30d / `--backup-bundle` |
| `fix/beat-spacing` | `aca7544b3a0d5d95855e76a06e620b602c2613a7` | reflog ~30d / `--backup-bundle` |
| `fix/bench-degradation-vector` | `404179c565ba4a59c2135eb598878dba71c2054d` | reflog ~30d / `--backup-bundle` |
| `fix/billing-surface` | `209d51f485bcb890c04d18f21a3abfb89fbb485e` | reflog ~30d / `--backup-bundle` |
| `fix/calibration-7080` | `5a2a4d6738d8d6371a3d5f872e8e96db415e13b6` | reflog ~30d / `--backup-bundle` |
| `fix/canary-gh-token` | `ca0c2ee01bad8629316f6b6f2300fb5702a455fe` | reflog ~30d / `--backup-bundle` |
| `fix/canvas-zoom-2.2` | `e1a4330fe057ff92fe2b7e728313ad6abe4a748c` | reflog ~30d / `--backup-bundle` |
| `fix/canvas-zoom-3x` | `fe71e6a3aeb32f39fe8764966d77733743c277ce` | reflog ~30d / `--backup-bundle` |
| `fix/ci-battery-manifest` | `7545229367149303acc1337d59de5cab23383d64` | reflog ~30d / `--backup-bundle` |
| `fix/ci-dedup2` | `5dbca167ee0da788ab5eb32def45ee8a48fa918b` | reflog ~30d / `--backup-bundle` |
| `fix/ci-email-flood` | `a373308880c0971b2221fbeec96638e16986b3ff` | reflog ~30d / `--backup-bundle` |
| `fix/ci-empty-uri-falkordb-probe` | `c7ab0661e134f9d220621709ff4b4536bc6eac7f` | reflog ~30d / `--backup-bundle` |
| `fix/ci-fast-leg-watchdog` | `bc6d79a38768b6fca998f8c7575cad93b28519cd` | reflog ~30d / `--backup-bundle` |
| `fix/ci-manifest-drift-2800` | `eb578392a55f3b08b259e7ced589729af0dc7fb4` | reflog ~30d / `--backup-bundle` |
| `fix/ci-manifest-drift-price-basis` | `41e7670ee0467426d212d8967c6edeecf0b3eb32` | reflog ~30d / `--backup-bundle` |
| `fix/ci-manifest-drift2` | `789754b7c37424ccdb2d3ee908b2dbdb510c0b0d` | reflog ~30d / `--backup-bundle` |
| `fix/ci-manifest-drift3` | `89b7ae29b91407cdd42e0f493d9d29290a93420a` | reflog ~30d / `--backup-bundle` |
| `fix/ci-remaining-red` | `f37c2f2856225caab7922e85a65aa27606213826` | reflog ~30d / `--backup-bundle` |
| `fix/ci-selection-manifest` | `0fb985cb655e40f9fb6f1b96c492e2733758900a` | reflog ~30d / `--backup-bundle` |
| `fix/ci-surfaces-drift-3c` | `d1199b84f362e0f3c8c8f2e9dbfc88b802938046` | reflog ~30d / `--backup-bundle` |
| `fix/ci-test-drift` | `2f8bc94b134ce72b3d04afb517c8ab575789cfbd` | reflog ~30d / `--backup-bundle` |
| `fix/ci-v3` | `8bb1607704bce9fab259b013e22c3eb2d783adbc` | reflog ~30d / `--backup-bundle` |
| `fix/ci-validation-bugs` | `16e3ddcc5f45156a551ff2ab506d95ec69d279ee` | reflog ~30d / `--backup-bundle` |
| `fix/confidence-graphranker-prior-coalesce` | `a6ad6fd9671d465be0052aa9a88e1a7053355b45` | reflog ~30d / `--backup-bundle` |
| `fix/connect-step-clean-layout` | `aff5a35ac1aaeba175ba0c4cf1164bfdd75d46fa` | reflog ~30d / `--backup-bundle` |
| `fix/connect-step-fork-aware-layout` | `09057abede5d6c82adbc3acf5daa734fa0aa4735` | reflog ~30d / `--backup-bundle` |
| `fix/contestation-surface-only` | `1e41ce9de1f8ddc27d3ee874477c4b1d820b9014` | reflog ~30d / `--backup-bundle` |
| `fix/create-source-is-episodic` | `ecb9f5b87796a70268000835b88862d12b3f3c35` | reflog ~30d / `--backup-bundle` |
| `fix/deploy-secret-gate` | `7380a31aaa2d1252c130593281d4f0e5ca904c64` | reflog ~30d / `--backup-bundle` |
| `fix/digest-noise-preserve-decision-shapes` | `79117bd06794076eb2dcbd193ac8bd8d8c07a335` | reflog ~30d / `--backup-bundle` |
| `fix/dogfood-signup-fixes` | `e861ac140e1186536ef6548f6668dd268a090673` | reflog ~30d / `--backup-bundle` |
| `fix/dr-issues-pat-name` | `02717b077307e04db2f9115eda2269977690f0db` | reflog ~30d / `--backup-bundle` |
| `fix/email-notify-reborn` | `d45486e234f633a38a52f626c3a74df44b3e723a` | reflog ~30d / `--backup-bundle` |
| `fix/email-scheduler` | `58a6631b9d801c149c17f953254a2ce3676fbcd7` | reflog ~30d / `--backup-bundle` |
| `fix/ep-cascade` | `4a6ec96b09bd2f4f06de7642d7305807cce24433` | reflog ~30d / `--backup-bundle` |
| `fix/ep-followups-651-400` | `95aabcb75ee1fc81456dc12d76ba065cdd9b02b1` | reflog ~30d / `--backup-bundle` |
| `fix/flaky-signup-test` | `5a9e588db3e0dc3cbfdc3d7a414c49d58e005705` | reflog ~30d / `--backup-bundle` |
| `fix/fork-card-step-index` | `da3808ec7d83be28e145f7a23e49dde350728b09` | reflog ~30d / `--backup-bundle` |
| `fix/gitignore-tortoise-file` | `9230be3bd3492e2afe2df1519a01306478563d43` | reflog ~30d / `--backup-bundle` |
| `fix/harness-copy-instructions` | `d99e21f9de1c533ceb1c30165f9e1a8ac656bcfb` | reflog ~30d / `--backup-bundle` |
| `fix/hero-cta` | `36d08a526c6d8c983eb6c3d44c7c39e5b79deac7` | reflog ~30d / `--backup-bundle` |
| `fix/hero-cta-signup` | `a5382233ff84b0ccaecf819770ec0635efcb56ff` | reflog ~30d / `--backup-bundle` |
| `fix/hosted-coldstart` | `aac53f5ceb257043622b26a43136e68107b4bdf6` | reflog ~30d / `--backup-bundle` |
| `fix/hosted-deploy-stage` | `0796c5ad4e4d9e99f5b76c6a53dc2581f62f3d72` | reflog ~30d / `--backup-bundle` |
| `fix/hosted-mcp-hosts` | `eb59e613f08dee863c8a69d8d77ff4873fef094b` | reflog ~30d / `--backup-bundle` |
| `fix/hosted-mcp-hosts-v2` | `665a5aa64814246714dd2334a7a9dac8cece7d66` | reflog ~30d / `--backup-bundle` |
| `fix/hosted-mcp-origins` | `753b989d17c1f261baf1b427c0d42a6538a58066` | reflog ~30d / `--backup-bundle` |
| `fix/index-test-embedded` | `f2b5cf662e8de12cdde06948c0b58b8d01f47d6a` | reflog ~30d / `--backup-bundle` |
| `fix/indexer-exit-code-mixed-failure` | `f20d8fcb94736ba7b92f81a0f4efb2c771ea5a63` | reflog ~30d / `--backup-bundle` |
| `fix/ingest-redis` | `37a1f7dde57fd48fff63bddc82415a337ffa249a` | reflog ~30d / `--backup-bundle` |
| `fix/keepalive-test-pollution` | `89572d1aa5f5d53cafa223b434a1aed1ee0c51ad` | reflog ~30d / `--backup-bundle` |
| `fix/landing-layout` | `83e91d90819cf5d2e0666798544e8da2129c6cfa` | reflog ~30d / `--backup-bundle` |
| `fix/legal-e2e-obfuscated-mailto` | `c4eed197010df8be494ebc58d49055e552103a84` | reflog ~30d / `--backup-bundle` |
| `fix/legal-e2e-skip-external-crawl` | `b60095ade6b8451f3c5949082dab8fcd2c096f65` | reflog ~30d / `--backup-bundle` |
| `fix/mcp-asyncio` | `d64f5d7d2d08673fe3317d9b5ffcba6f909bae02` | reflog ~30d / `--backup-bundle` |
| `fix/mcp-ingest-promotion-keyerror` | `d83dc28c96b191e03b0f1866e2c3653e4a0a79bc` | reflog ~30d / `--backup-bundle` |
| `fix/migration-0012b-unique` | `b4fe182a7945decd1bb2f09282243cc1f75bd02f` | reflog ~30d / `--backup-bundle` |
| `fix/migration-0015b-unique` | `d5b2a0d43bbb6438f1553071941479eb9010dca5` | reflog ~30d / `--backup-bundle` |
| `fix/migration-timestamp-names` | `2417cca165426c588466a3dfc9c2fa3e8b03f2e4` | reflog ~30d / `--backup-bundle` |
| `fix/mobile-responsive` | `62cce195e01f596435858eeabce7ffe9767da4c8` | reflog ~30d / `--backup-bundle` |
| `fix/ops-followups-673-692-686-713` | `53bb0a2aa72b218ce9cd134e5d7972065a299490` | reflog ~30d / `--backup-bundle` |
| `fix/org-create-copy-and-name-validation` | `356d74c82c60ed566909bceabf8f5214cb419258` | reflog ~30d / `--backup-bundle` |
| `fix/org-create-spacing-and-edge-function` | `28edcc183c3158d3fc9862677d778e15c658a1a6` | reflog ~30d / `--backup-bundle` |
| `fix/orphan-sweep-deferral` | `c1f06ef906af210136d4a5f84433f649320e5167` | reflog ~30d / `--backup-bundle` |
| `fix/p2-github-error-text` | `bfe2557a97ea031a7341afe25d3dc723eb4a84d3` | reflog ~30d / `--backup-bundle` |
| `fix/perf-note-indexing` | `31cdd209e508662eaf800ad057682fff0fa6860e` | reflog ~30d / `--backup-bundle` |
| `fix/post-merge-drift` | `93acac020eb3b34a7b1c62f10b4caa346b2ae32d` | reflog ~30d / `--backup-bundle` |
| `fix/pricing-cleanup` | `06a0d29663e00925e51ffef48f4410ca349cb8c1` | reflog ~30d / `--backup-bundle` |
| `fix/raud2558` | `5176b81ecfb87b8094f8a0b39bc15fd6e7341dee` | reflog ~30d / `--backup-bundle` |
| `fix/repair-432-clobber` | `f353f95af0150f1df78b8d346026f56371485cb6` | reflog ~30d / `--backup-bundle` |
| `fix/selfhost-copy` | `dfd20571b23e6e5cc0c05d8d68d097a5f998d775` | reflog ~30d / `--backup-bundle` |
| `fix/selfhost-smoke-redirect` | `49d33460b764a65dd710735788d592fec262e4d8` | reflog ~30d / `--backup-bundle` |
| `fix/seo-301-immutable-headers` | `9e8c4e689d7a3e6e881d9a636d79c8f320390aab` | reflog ~30d / `--backup-bundle` |
| `fix/session-source-agentkind` | `cc60a653fc6b62ed01aa8391d7b64333a46b8357` | reflog ~30d / `--backup-bundle` |
| `fix/signup-layout` | `6b1742aafef406763e721b0cbdbf6a1556575aed` | reflog ~30d / `--backup-bundle` |
| `fix/stale-search-tests` | `a895ad8bc9135d313d42cda1407ad1e83cd65325` | reflog ~30d / `--backup-bundle` |
| `fix/suite-red` | `a05a3dcbcf67698a555609cdd6511c5c44ccafd1` | reflog ~30d / `--backup-bundle` |
| `fix/suite-red2` | `6221cd32398913b6e2af921d79216e8a1be34e8c` | reflog ~30d / `--backup-bundle` |
| `fix/team-500` | `49a7cbcd11a7362de34eb860a127a0f2d8e96c20` | reflog ~30d / `--backup-bundle` |
| `fix/test-drift` | `bcdfc8c1a47ccf9c892a5a5722f854989be379d0` | reflog ~30d / `--backup-bundle` |
| `fix/test-infra` | `dad7d5a26e12693d2c3af7fc6e83bcce34e52b7c` | reflog ~30d / `--backup-bundle` |
| `fix/tos-dollar-guard` | `f37eedee15616407b2740d495de148e895a5715c` | reflog ~30d / `--backup-bundle` |
| `fix/web-audit` | `1c22edb838892109865c8307748d93a93ea96a30` | reflog ~30d / `--backup-bundle` |
| `fix/website-1` | `836440fd1b658f6fbb8325be061cfe73da9da962` | reflog ~30d / `--backup-bundle` |
| `fix/website-2` | `13b95e539a02ce498d4fb75ccdcac59374deac34` | reflog ~30d / `--backup-bundle` |
| `fix/website-tiers-test` | `782e7ee3a8a796b2ae400c84d058eb024ababcbd` | reflog ~30d / `--backup-bundle` |
| `fix/welcome-bridge` | `ebfcbf9ec3ad78567bc3cadfe4e9f4fe3dd8d170` | reflog ~30d / `--backup-bundle` |
| `fix/welcome-e2e` | `55544b1b088ed4f9f38c9766479f568ae60d25a6` | reflog ~30d / `--backup-bundle` |
| `fix/welcome-e2e-monitor` | `b39ab72b52b538c477c0350ebb843fd56d2ae477` | reflog ~30d / `--backup-bundle` |
| `hotfix/lint-eval-write-path` | `2f5979fcd9d6ed969ea8d9bdd80fb4386bb74007` | reflog ~30d / `--backup-bundle` |
| `landing-run` | `a25e44b92c0145e8e71ca375dc161f33fe90941c` | reflog ~30d / `--backup-bundle` |
| `merge-tmp` | `3fe0f45e37373c9e9a01c5be6c36bcb2267a5995` | reflog ~30d / `--backup-bundle` |
| `ops/2146-graph-drop` | `5d1a80c115a2a598710b77cb3c73e7740effbf98` | reflog ~30d / `--backup-bundle` |
| `ops/2146-orphan-cleanup` | `5b7a6aba8905004775f4cb24c43355f95f8b89a9` | reflog ~30d / `--backup-bundle` |
| `opt/2080-d2-failover` | `f7455cc59b5d06041f5668a3a51334393d3048c5` | reflog ~30d / `--backup-bundle` |
| `opt/2080-ex-quality` | `49e21c7e0a0aea7adf339fac3a4a811a4ab4966c` | reflog ~30d / `--backup-bundle` |
| `opt/2080-qa-loop` | `84d35029fadab063b72ef95d995d65523af8267d` | reflog ~30d / `--backup-bundle` |
| `opt/2080-retention` | `656935c2654f6f3d07401eea9c5f3f3a274e0b97` | reflog ~30d / `--backup-bundle` |
| `opt/2080-trust-posture` | `a6250ee705329a3daf2dbc0f1cbc5d499f4d16c1` | reflog ~30d / `--backup-bundle` |
| `opt/2315-mitigation` | `a4daec65d938fcb7e8afcbe2b85853a714873fb5` | reflog ~30d / `--backup-bundle` |
| `opt/2424-clause-fix` | `59aac0401c8703c0c1007149be918a1c2c6fbc6d` | reflog ~30d / `--backup-bundle` |
| `opt/2518-entity-keys` | `621fa6403891ed3b477ab98c13ac736d2b25cb21` | reflog ~30d / `--backup-bundle` |
| `opt/2552-operators` | `4796ad47b4ebdc8adf56711c7154d93b30e2a221` | reflog ~30d / `--backup-bundle` |
| `perf/4068-reaper-census` | `db570edb4a70c9a1f4b8b529933dc3f0316d59f3` | reflog ~30d / `--backup-bundle` |
| `pr-1015` | `6ccac2466db8d616555d1e847c6f6d5e35fabe2f` | reflog ~30d / `--backup-bundle` |
| `pr-1069` | `0f5d918194f994a96fc4b74e91e71ebfe856cbe5` | reflog ~30d / `--backup-bundle` |
| `pr-1194` | `d192c3bff9918b200d9cba72939b5548b573e872` | reflog ~30d / `--backup-bundle` |
| `pr-1208` | `ad4809f6f53f0adb2dd5b8f82d296b46dc7b4859` | reflog ~30d / `--backup-bundle` |
| `pr-1215` | `2cb1e727f0f59fff0c0502f2cb1f49a5f3e35136` | reflog ~30d / `--backup-bundle` |
| `pr-1761` | `e4ec6e708ad9684b2e1832a35d2b37e45b45d6f0` | reflog ~30d / `--backup-bundle` |
| `pr-2013` | `4607fbb8d7541b2cf417804933d2501f133ea923` | reflog ~30d / `--backup-bundle` |
| `pr-2049` | `096c60512714743592412998ac89fdc0329a7be5` | reflog ~30d / `--backup-bundle` |
| `pr-2054` | `3a4c76e9b403f75b5e1c8efb1a28c4c0e4ec4b81` | reflog ~30d / `--backup-bundle` |
| `pr-2366` | `36a7ca6b665522748420355f51868f85aa9a5e33` | reflog ~30d / `--backup-bundle` |
| `pr-2664` | `76dd3039fda6fd31c67e32f6890dabba0f13ffd1` | reflog ~30d / `--backup-bundle` |
| `pr1467` | `4463e6b9992ad927684030e9cf23b7aa8b361a55` | reflog ~30d / `--backup-bundle` |
| `pr1868` | `d7e6e69d2936cf900de49b76f2beffb2694023b2` | reflog ~30d / `--backup-bundle` |
| `pr2181` | `5d1a80c115a2a598710b77cb3c73e7740effbf98` | reflog ~30d / `--backup-bundle` |
| `pr2664` | `76dd3039fda6fd31c67e32f6890dabba0f13ffd1` | reflog ~30d / `--backup-bundle` |
| `pr3780` | `fcd01388e7acd7cc6a322f0f65aa1aaf476a407e` | reflog ~30d / `--backup-bundle` |
| `research/matched-recall-controls` | `96938103894724b5dcc126965ebc791279dabb14` | reflog ~30d / `--backup-bundle` |
| `review-bugs` | `9108a0096eadb0fbd1e243a5fb2aaba0d933c0d5` | reflog ~30d / `--backup-bundle` |
| `scratch/801-baseline` | `23942d2186e14c24f9cd9e5409cbe3292cabdaa8` | reflog ~30d / `--backup-bundle` |
| `seo-docs-link` | `ac3dbb336c212bcc0e966fb4ce34c1e723e69a31` | reflog ~30d / `--backup-bundle` |
| `seo-fixes` | `f1705b4147cb8f122c9dbf482cd867fac6684ffb` | reflog ~30d / `--backup-bundle` |
| `test/283-e2e-gaps` | `5f287a3f064ea1c1b3bd8afd2ab99d4127cba886` | reflog ~30d / `--backup-bundle` |
| `test/303-hosted-e2e` | `661222d43d1d90fd5d25a936134ecd1e0338db35` | reflog ~30d / `--backup-bundle` |
| `test/562-revise-embed-test` | `860a4271a1469a8f7fa98a9c92faed8224672b47` | reflog ~30d / `--backup-bundle` |
| `test/563-rebuild-order-test` | `1f672b932d6429d9a6f4e4706a29e3e48b539d62` | reflog ~30d / `--backup-bundle` |
| `test/748-749-revenue-surfaces` | `1e38b39843f849a1d43dfc306bd2f9a555c7ae73` | reflog ~30d / `--backup-bundle` |
| `tmp-main-check` | `5f665c114c548d72c9813703b8c36703851c257d` | reflog ~30d / `--backup-bundle` |
| `tmp/1922-cleanup` | `d21c89645867891023c9c089b73de40d4fe26dc2` | reflog ~30d / `--backup-bundle` |
| `tmp/2252-d24probe` | `8ec4afc283bf9d6444dc0dd0a8dbe96e82a60db6` | reflog ~30d / `--backup-bundle` |
| `tmp/2252-mainprobe` | `8ec4afc283bf9d6444dc0dd0a8dbe96e82a60db6` | reflog ~30d / `--backup-bundle` |
| `tmp/2269-clean` | `bf6eb9db874c40e76e391b60e2835c0ae3b519f8` | reflog ~30d / `--backup-bundle` |
| `tmp/lme-full-run` | `2f7c3df83bb174d97fa5c3636dda5fd5a2bb2a1c` | reflog ~30d / `--backup-bundle` |
| `tmp/lme-rerun` | `f060b3180987cc00fa5f77de3d32cd5eef6afb0d` | reflog ~30d / `--backup-bundle` |
| `tmp/lme-v2-run` | `2aed4a3d7e8d364561593c2cac6a326d30c41f25` | reflog ~30d / `--backup-bundle` |
| `tmp/main-check` | `b69e6a5cf73a8c9ecce8f8d99edd24b7f6539068` | reflog ~30d / `--backup-bundle` |
| `tmp/main-check2` | `b69e6a5cf73a8c9ecce8f8d99edd24b7f6539068` | reflog ~30d / `--backup-bundle` |
| `tmp/main-clean` | `b69e6a5cf73a8c9ecce8f8d99edd24b7f6539068` | reflog ~30d / `--backup-bundle` |
| `tmp/main-gate-check` | `4270f17159a949aecea6d6aefde392df5b6c93b5` | reflog ~30d / `--backup-bundle` |
| `tmp/main-reaper` | `d6dee91fb6ab0593e7e66500d9ddcd0d7ccd9d6f` | reflog ~30d / `--backup-bundle` |
| `tmp/maincheck` | `64fb2e67786e5bab0c2b8b0fbc2f8ca5daa1f816` | reflog ~30d / `--backup-bundle` |
| `verify-t` | `9d226d89431c7c8eafcf7de687956a14a9a3f575` | reflog ~30d / `--backup-bundle` |
| `verify/1001-main-check` | `2eec487e5d51f74337f70d92fefb1ac156f987ba` | reflog ~30d / `--backup-bundle` |
| `verify/2833-e2e-connect` | `e45bfb29cd7ce360b777a07763cae983bfbb7355` | reflog ~30d / `--backup-bundle` |
| `verify/291-capstone` | `98cc1eb1d6dbc363e037e159a47e301c45f7a3f8` | reflog ~30d / `--backup-bundle` |
| `verify/epic909-main-check` | `f71e13718f53747e616dc3ead5493f9440fb38a4` | reflog ~30d / `--backup-bundle` |
| `verify/epic909-prod` | `089c894ff6bba4c6a5be50e858bba72f083d2ce9` | reflog ~30d / `--backup-bundle` |
| `verify/epic909-prod2` | `089c894ff6bba4c6a5be50e858bba72f083d2ce9` | reflog ~30d / `--backup-bundle` |

