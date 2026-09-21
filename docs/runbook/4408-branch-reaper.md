# branch-reaper dry-run report — #4408

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
| `fix/545-pricing-image` | `200abf34bdc6` | 43 | pr-merged-tip | merged-pr-record | 642 |
| `fix/545-deploy-secrets` | `e10bc4bd40ea` | 43 | pr-merged-tip | merged-pr-record | 643 |
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
| `feat/1177-307-invite-accept-email` | `b642261cd72e` | 37 | ancestry | reachable-from-main | — |
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
| `feat/1245-epic903-observability` | `48735a51d8dc` | 36 | ancestry | reachable-from-main | — |
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
| `fix/signup-layout` | `6b1742aafef4` | 35 | pr-merged-tip | merged-pr-record | 1344 |
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
| `feat/1933-agent-ops-pack` | `bbbeaeb6f432` | 21 | pr-merged-tip | merged-pr-record | 2015 |
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
| `fix/2052-redislite-orphan-sweep` | `142c65f28da8` | 20 | ancestry | reachable-from-main | — |
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
| `fix/org-create-spacing-and-edge-function` | `28edcc183c31` | 12 | pr-merged-tip | merged-pr-record | 2551 |
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
| `pr-3391` | `13a49e939ed1` | 7 |
| `docs/scoping-3055` | `6677611089b5` | 7 |
| `pr-3426` | `6b395bc485d1` | 7 |
| `tmp-3511-orphan-threshold` | `344b01dc58c6` | 5 |
| `feat/3543-tenancy-rename` | `1ed87510d269` | 5 |
| `pr-3576-review` | `3479195932cb` | 5 |
| `pr-2948` | `9642057418e4` | 5 |
| `2813-revive-passthrough` | `258d0049ac95` | 4 |
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

