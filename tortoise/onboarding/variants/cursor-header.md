---
alwaysApply: true
---

# Tortoise Onboarding — Cursor setup

> Harness variant of the canonical Tortoise onboarding prompt (epic #529).
> The question flow below is the canonical `AGENT_ONBOARDING.md` body —
> single source of truth; never fork it.

## How to use

1. Save this ENTIRE document as `.cursor/rules/tortoise-onboarding.mdc` in
   your project (keep the frontmatter above intact).
2. Open any Cursor chat — this rule is injected automatically (`alwaysApply:
   true`), so onboarding starts automatically with no paste into chat. Then
   answer the yes/no questions one at a time.

Warnings: the file MUST have the `.mdc` extension — a plain `.md` file in
`.cursor/rules/` is ignored by Cursor. This rule pairs with the Block A config
(`.cursor/mcp.json`, which uses `${env:TORTOISE_API_KEY}` — export that
environment variable before starting Cursor).
