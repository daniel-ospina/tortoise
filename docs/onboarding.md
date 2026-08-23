---
title: "Onboarding — the in-dashboard wizard (issue #1643)"
type: decisions
domain: operations
doc_status: live
created: 2026-08-23
ownedBy: epistemic-team
---

# Onboarding wizard (#1643)

The first-run experience is a 5-step getting-started wizard **inside the
dashboard** (no dead ends, no external-docs links for the core path):

1. **Connect your tool** — the four-harness chooser (Claude Code / Codex /
   Cursor / Pi) with one-click setup commands + Copy (data in
   `website/apps/dashboard/src/harnesses.js`, ported from welcome.html).
2. **Your agent's toolkit** — the two skills: `how-to-use-tortoise`
   (passive — the agent loads it to know how to use the graph) and
   `tortoise-decide` (invoke — the decision workflow: options → criteria →
   findings → IMPL/NAND/mitigation edges → EP confidence; `skills/
   tortoise-decide/SKILL.md` ships in agent-infra, `graph-scripts/decide.py`
   is the self-host variant).
3. **Connect GitHub** — the existing OAuth+indexer surface
   (`POST /v1/onboarding/github/connect` + status polling; per-team
   encrypted token; issues → Events via `GitHubIndexer`).
4. **Seed your graph** — the STATE sample: `POST /v1/objects`
   (`{workspace}`, `in_progress`) + a statement Point wired `aboutObject`
   (authored by the user) — populating the state-centric model (Objects
   carry lifecycle + confidence; Points argue about them).
5. **You're set** — completion patches `onboarding_complete` on
   `/v1/onboarding/state`.

**Re-entry:** returning users with an empty graph get a "Continue setup"
card opening the wizard (no raw-curl dead end). Completed users never see
it.

**Backend additions (this work):**
- `POST /v1/objects` — wraps `sdk.create_object` (idempotent by name).
- `about_object` on `POST /v1/points` — wires the ID-based
  `(p)-[:aboutObject]->(o)` edge (never a bare prop; never the
  name-resolution stub path).
- `graph-scripts/decide.py` fixed (dropped the removed `context=` kwarg —
  it silently produced stub operators) + a subprocess smoke test.

**Design rules:** free tier is the default (no card); the plan step stays
non-blocking; the raw `tt_` key never leaves the app origin (#1082); the
GitHub token is per-team encrypted (no new token surface); the wizard is
gated on `authed && !mountError` (#1559) and the claim funnel (#1511) runs
first.
