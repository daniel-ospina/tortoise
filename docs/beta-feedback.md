---
title: "Beta Feedback & Bug Reporting — Channel Guide"
type: operations
domain: operations
doc_status: live
created: 2026-08-14
ownedBy: organisation-design-team
aboutSubjects: tortoise
aboutObjects: tortoise-beta
---

# Beta Feedback & Bug Reporting (#1199)

This is the single entry point for the beta cohort (~10–50 technical agent
developers) to report bugs and give feedback. If you're a beta tester, start
here.

**Two channels, one repo:**

| What you have | Where it goes |
|---|---|
| A bug / unexpected behavior | [File a bug report](https://github.com/daniel-ospina/tortoise/issues/new?template=bug_report.yml) — structured form, feeds the tracker |
| A question, idea, or general feedback | [GitHub Discussions](https://github.com/daniel-ospina/tortoise/discussions) — async, no form needed |

## Channel decision (research, 2026-08-14)

**Decision: GitHub Discussions (conversation) + GitHub Issues with a
structured bug-report template (structured bug path).** Both live in this repo.

Options considered for a small technical cohort (cost, triage ergonomics, graph
JSON attachment):

- **GitHub Discussions — chosen for conversation.** $0 (part of GitHub). Every
  beta tester already has a GitHub account (it's how they signed up), so there
  is no new account surface. Triage is async and threaded — good for
  deliberation, no real-time support expectations. Graph JSON and screenshots
  attach natively as files (a tester can drop a `graph export.json` straight
  into a post). Discussions convert to Issues in one click when a thread turns
  into a real bug — which is exactly the "feeds the tracker" requirement.
- **GitHub Issues + bug template — chosen for structured bug reports.** Bug
  reports *are* tracker items, so filing them as issues (auto-labeled
  `beta-feedback`) skips a hop: no discussion → issue copy step. The YAML form
  enforces the fields that make graph-engine bugs triageable (surface,
  expectation vs actual, graph JSON).
- **Discord — rejected.** $0 for a small server, but: new account surface,
  real-time chat creates support-load expectations the maintainer can't meet
  during a beta, no structured forms, graph JSON pasting is awkward, threads
  are ephemeral, and nothing feeds the issue tracker without manual copying.
- **Typeform — rejected.** Structured and pretty, but paid tiers beyond the
  free plan (free tier is capped well below a 10–50 person cohort), testers
  must leave their dev context, and responses land in a CSV — they don't feed
  the issue tracker at all.

GitHub also documents Discussions explicitly for private-repo beta feedback —
this is a known-good pattern, not a workaround.

## What to include in a bug report

The issue form enforces these fields. If you're pasting into Discussions
instead, use the same skeleton:

```markdown
**Surface:** SDK / MCP server / CLI / REST API / hosted / self-hosted / embedded
**Expected:** what you expected to happen
**Actual:** what happened instead
**Graph JSON:** the point/operator payload you sent and the response you got
  (or `tortoise_summarize_structure` output / a `tortoise backup` export).
  Attach long JSON as a .json file rather than pasting.
**Steps:** minimal numbered repro, including the exact MCP tool call / CLI command
**Version:** `pip show tortoise` version or the git SHA
**Mode:** hosted (streamable-http) / local HTTP / stdio / Docker / embedded
```

**Graph JSON matters most.** Tortoise bugs are almost always about what got
written to or read from the graph — the point kind, edges, and EP weights.
Include the payload when the bug involves graph data; screenshots help for UI
surfaces, JSON is what makes a graph-engine bug fixable in one pass.

## Triage path

1. **Channel watcher (owner):** `@daniel-ospina` watches both channels
   (Discussions + issues labeled `beta-feedback`).
2. **Bug reports** land as issues auto-labeled `bug` + `beta-feedback`. The
   `beta-feedback` label is the tracker's beta filter — triage
   = reviewing `github.com/daniel-ospina/tortoise/labels/beta-feedback`.
3. **Triage cadence:** reports are acknowledged within **2 business days**.
   Each report is either (a) fixed directly, (b) converted into a scoped
   tracker issue (with `complexity:` + `team:` labels per repo convention),
   or (c) answered in-place if it's a usage question, not a bug.
4. **Discussions:** threads that turn into confirmed bugs are converted to
   issues (one click) and pick up the same `beta-feedback` label; the rest
   stay as conversation.
5. **Feedback loop:** every report gets a response in its thread — no silent
   triage. Beta-priority fixes ship in the next release; the reporter is
   pinged on the issue when it closes.

## Repo state after this lands

- ✅ GitHub Discussions **enabled** on the repo (default categories:
  Announcements, General, Ideas, Polls, Q&A, Show and tell).
- ✅ Structured bug-report template at `.github/ISSUE_TEMPLATE/bug_report.yml`
  (surface, expected vs actual, graph JSON, repro, version, mode).
- ✅ Template chooser at `.github/ISSUE_TEMPLATE/config.yml` links
  Discussions from the "New issue" page.
- ✅ `beta-feedback` label created — the tracker's triage filter.

**Optional owner step (UI, cannot be automated — GitHub removed the
`enableDiscussions` mutation):** to add a dedicated "Beta feedback" discussion
category, open **Discussions → Categories (bottom of the page) → Add category**,
name it `Beta feedback`, and set it as the default for new threads. Not
required — General/Q&A already cover the cohort.
