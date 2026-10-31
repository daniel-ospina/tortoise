---
title: "#9664 acceptance run — evidence index (2026-10-26), the run's own index kept verbatim"
type: evidence
domain: platform
doc_status: historical
created: 2026-10-26
subjects.team: epistemic-team
aboutSubjects: Tortoise onboarding, MCP connector, ChatGPT
aboutObjects: "#9664, #8617, #10084, #10088, chatgpt-acceptance browser profile"
---

<!-- Frontmatter added at publication; everything below this line is the run's own index, verbatim,
     including its doubled `# #9664` heading. It is the prior summary `INDEX.md` corrects. -->

# #9664 acceptance run — evidence index (2026-10-26, 12:51–13:21 UTC-05 / 17:51–18:21 UTC)

Instrument: cmux browser surface `surface:1000030017`, real headed webview, driven via `cmux browser`.
Account observed on ChatGPT: **Daniel Ospina — Plus** (not Pro). Org on Tortoise: `daniel-ospinabotero`,
**Solo tier, ⚠ flagged**, graph usage 1/2, graph name `default`.

Chain of custody: every action below was a click/type/Enter in the real UI. `eval` was used to READ
state only (text, selectors, box geometry) — never to perform a described action.

| # | Verdict | Screenshots | Trace / text |
|---|---|---|---|
| 0 | logged out → owner logged in | `step0-logged-out-*`, `step0-logged-in-plus-20261026-125551.png` | — |
| 1 | PASS (state) — `team-named` complete | `step1a-tortoise-auth-page`, `step1b-tortoise-login-tab`, `step1-org-overview`, `step1-settings` | `step7-graphs-and-step5-onboarding-state.txt` (`completed_steps`) |
| 2a | **FAIL** — graph creation refused (FalkorDB ACL) | `step2e-graph-create-FAILED-acl-*`, `step2f-graph-create-retry-acl-*` | `step2e-graph-create-failed-error.txt` |
| 2b | PASS — packs narrow → persists → `configured` pinned | `step2g-*`, `step2h-*`, `step2i-*`, `step2j-*`, `step2l-*`, `step2m-*`, `step2o-*` | `step2g-packs-before.txt`, `step2k-packs-after-save.txt` |
| 3 | PASS as corrected — no ChatGPT card; docs page is the door | `step3a-setup-guide-after-run-*`, `step3c-docs-chatgpt-scrolled-*`, `step3d-docs-chatgpt-section-*` | `step3a-settings-setup-guide.txt` |
| 4 | **BLOCKED** (consent render) / connector + token verified | `step4a-plugins-installed-*`, `step4b-*`, `step4c-plugin-manage-settings-*`, `step4e-connect-consent-dialog-*`, `step4f-oauth-authorize-page-asks-signin-*`, `step4g-*` | — |
| 5 | PASS as corrected — nothing closes at token issue | — | `step6e-full-trace.txt` (step table), state JSON in `step7-…txt` |
| 6 | PASS as corrected — `tortoise_overview(section="health")`, no `load_skill` | `step6a-*`, `step6b-*`, `step6c-*`, `step6d-*` | `step6e-full-trace.txt` |
| 7 | PASS — one entitled namespace; foreign id refused | `step7a-list-graphs-and-onboarding-state-*` | `step7-graphs-and-step5-onboarding-state.txt` |
| 8 | PASS — write landed and read back | `step8a-*`, `step8b-*`, `step8c-*` | `step8c-write-probe-full-trace.txt` |
| 9 | PASS — `decide-completed` Pending → Complete | `step9a-*` | `step9b-decide-full-trace.txt` |
| 10 | PASS — ChatGPT capture disclosure rendered | `step10-capture-disclosure-chatgpt-*`, `step10-…-tall-*` | — |
| probe A (minute-one write) | PASS | `step8c-*` | `step8c-write-probe-full-trace.txt` |
| probe B (four ugly cases) | BLOCKED / host-blocked | `step11-ugly-cases-*` | `step11-ugly-cases-trace.txt` |

Pasted document (step 6): `skill-tortoise-onboarding.md`, 66,457 bytes, fetched from
`https://app.premiselabs.co/skills/tortoise-onboarding/SKILL.md` (HTTP 200).
