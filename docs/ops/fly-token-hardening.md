---
title: "Ops: Fly Token Hardening + Main Branch Protection (#660)"
type: engineering
domain: platform
doc_status: live
subjects.team: epistemic-team
created: 2026-08-09
aboutSubjects: tortoise
---

# Fly Token Hardening + Main Branch Protection (#660)

Follow-up to #596 (registry backup DR). Closes the largest residual under the
cycle-5 P0 threat model: an unconstrained `FLY_API_TOKEN` in GitHub Actions
secrets + unprotected main branch gave any main-push collaborator full app
takeover (deploy + `flyctl secrets set`).

## Part 1: deploy-scoped Fly token

The deploy-hosted workflow now uses TWO Fly tokens:

| Step | Secret | Scope | Why |
|---|---|---|---|
| Set all app secrets on Fly.io | `FLY_API_TOKEN` | Full org access | `flyctl secrets set` requires it |
| Deploy | `FLY_API_TOKEN_DEPLOY` | Deploy-only | Least privilege — cannot mutate secrets |

### Operator setup (one-time)

```bash
# 1. Create a deploy-scoped token (requires Fly.io auth + org access).
fly tokens create deploy --app tortoise-y4mjjq
# Output: a token string starting with "fm1" or "fm2".

# 2. Set it as a GitHub Actions secret.
gh secret set FLY_API_TOKEN_DEPLOY --body "fm1..." --repo daniel-ospina/tortoise

# 3. Verify the new secret is registered.
gh secret list --repo daniel-ospina/tortoise | grep FLY_API_TOKEN
```

### Migration / rotation

After confirming deploys succeed with `FLY_API_TOKEN_DEPLOY`:

1. **Rotate the full-scope token** (optional but good practice after scoping):
   ```bash
   fly tokens create org --org premiselabs   # creates a new full-scope token
   gh secret set FLY_API_TOKEN --body "<new-token>" --repo daniel-ospina/tortoise
   # Then revoke the old token in Fly dashboard → Access Tokens.
   ```

2. **Remove the old full-scope secret name** when confident the new one works
   (the old secret value stays in GH until explicitly removed).

### E2E verification

**Scope reality (verified 2026-08-09):** an app-scoped deploy token (`fly tokens
create deploy --app <app>`) is restricted to a SINGLE app — it cannot touch other
apps or the org — but it CAN manage that app's own secrets (`flyctl secrets set`).
The security win over the old org-wide personal token is the app-scoping
(org-wide blast radius → one app), NOT secrets isolation. Keep the full-scope
`FLY_API_TOKEN` secret for the secrets-set step and never share it beyond GH.

```bash
# App management (deploy, status, secrets) on THE ONE app — works:
FLY_API_TOKEN="$FLY_API_TOKEN_DEPLOY_VALUE" \
  flyctl status --app tortoise-y4mjjq
FLY_API_TOKEN="$FLY_API_TOKEN_DEPLOY_VALUE" \
  flyctl deploy --remote-only --app tortoise-y4mjjq

# Org-wide / other apps — fails (token is app-scoped):
FLY_API_TOKEN="$FLY_API_TOKEN_DEPLOY_VALUE" \
  flyctl apps list
```

## Part 2: main branch protection

Applied via GitHub API (token has admin:true on the repo):

```bash
gh api repos/daniel-ospina/tortoise/branches/main/protection -X PUT \
  --input - <<'EOF'
{
  "required_status_checks": {
    "strict": true,
    "contexts": [
      "pricing-artifact",
      "docs",
      "test-isolation",
      "license-surface",
      "legal-e2e"
    ]
  },
  "enforce_admins": false,
  "required_pull_request_reviews": {
    "dismiss_stale_reviews": true,
    "require_code_owner_reviews": false,
    "required_approving_review_count": 1
  },
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false
}
EOF
```

Settings applied:
- **Require PR reviews:** min 1 approval, dismiss stale reviews on new commits
- **Prevent force pushes:** yes
- **Prevent branch deletion:** yes
- **Require status checks (strict):** all 5 CI jobs must pass before merge
  (`pricing-artifact`, `docs`, `test-isolation`, `license-surface`,
  `legal-e2e`; `welcome-e2e` moved off the required list in #1008 — the
  live signup smoke runs in the welcome-e2e-monitor workflow instead)
- **Enforce admins:** no (owner can still bypass in emergencies)
