---
title: "Email-send abuse posture — GoTrue captcha + email bucket (#885)"
type: engineering
domain: platform
doc_status: live
subjects.team: epistemic-team
aboutSubjects: tortoise
aboutObjects: hosted-api, supabase-auth
created: 2026-08-13
---

# Email-Send Abuse Posture — GoTrue Captcha + Email Bucket (#885)

> Ops/config companion to the #863 client-side lockouts. #863 closes
> browser-originated retry loops on signup + recovery; this doc covers the
> server-side (GoTrue) and dashboard surfaces for the **project-wide email-send
> bucket** (custom SMTP 30/hr for ALL users, shared by `signup` / `recover` /
> `magiclink` / `otp` / `resend` / email-change). Direct-API spam is not
> client-mitigable — the posture below is the defense.

## What's in place (this repo, #885)

- **`[auth.captcha]` enabled in `supabase/config.toml`** — `provider =
  "turnstile"`, `secret = env(TURNSTILE_SECRET_KEY)`. Gates
  `POST /auth/v1/signup | recover | resend | magiclink | otp` (and `/token` on
  recent GoTrue). Requests without a valid `gotrue_meta_security.captcha_token`
  → 400.
- **Fail-closed by design:** GoTrue refuses to boot while enabled with an empty
  secret (`CaptchaConfiguration.Validate` → "captcha provider secret is empty"),
  so there is no silent fail-open. This is the opposite of the #308 app-level
  `_check_turnstile` check (hosted API), which fails OPEN while the secret is
  unset.
- **`email_sent = 2`** in `[auth.rate_limit]` stays dev-only-intentional (local
  inbucket testing keeps the bucket branch exercisable — see #801/#863).
- `TURNSTILE_SECRET_KEY` / `TURNSTILE_SITE_KEY` documented in `.env.example`
  (shared with the #308 app-level check).

## Remaining human / ops steps (NOT doable from this repo)

1. **Provision Cloudflare Turnstile keys** (one-time, human):
   Cloudflare dashboard → Turnstile → create a site. `TURNSTILE_SITE_KEY`
   (public) goes in client widgets; `TURNSTILE_SECRET_KEY` goes in server
   config/secrets. The hosted API is wired to consume the same key pair for
   the #308 app-level check (deploy-hosted.yml passes `TURNSTILE_SECRET_KEY`
   when set); the client-side widget provisioning (site key) is still pending
   — tracked in #1003 item 2.
2. **Hosted project: enable GoTrue captcha in the dashboard** —
   Dashboard → Auth → Security → CAPTCHA → provider Turnstile + paste
   `TURNSTILE_SECRET_KEY` (project `ybetwichurajbfswfeqa`). Config.toml does
   NOT govern the hosted project; this dashboard flip is the actual production
   enforcement for the public email-sending endpoints.
   ⚠️ **Flip gating:** on current GoTrue, `/token` with `grant_type=password`
   is ALSO captcha-gated, and the hosted dashboard's sign-in page has no
   Turnstile widget yet (site key empty, #1003). Flipping before login ships a
   widget breaks hosted password login (400 `captcha_failed`). Existing
   sessions survive (refresh_token / pkce / service-role are exempt) —
   coordinate with #1003 and do NOT flip until login flows pass tokens or an
   exemption is confirmed.
3. **Email-bucket headroom + monitoring** — Dashboard → Auth → Rate Limits
   (project-wide email, 30/hr). Consider raising headroom for legit flows
   (invites, password resets at scale) and/or alerting on 429
   (`over_email_send_rate_limit`) frequency. Supabase has no built-in 429 alert;
   alerting would need a scheduled check of auth logs or a watcher edge function
   (not yet filed).
4. **CI/deploy secrets** — `TURNSTILE_SECRET_KEY` (+ site key) for selfhost
   deploys. Hosted API secrets are already tracked under #308.

## Relationship to #1003

- **#1003 item 2** tracks Turnstile provisioning for the **signup page widget +
  hosted API `_check_turnstile`** (app layer, currently failing open).
- **#885 item 1** is the **GoTrue gateway captcha** on `/auth/v1/*` (auth layer).
  Different enforcement layers, same key pair; both need the dashboard steps
  above to become live in production.

## Known gaps

- Email-change (`PUT /auth/v1/user`) has no captcha hook — authenticated route;
  `double_confirm_changes` caps at 2 emails/change; no client guard in repo
  (#863 plan acceptance (a)).
- Local dev: `supabase start` requires `TURNSTILE_SECRET_KEY` in `.env`
  (a Turnstile TEST secret is sufficient ONLY to boot the stack —
  `supabase/tests/run_schema_tests.sh`) or `[auth.captcha]
  enabled = false` — GoTrue fails closed at boot. The TEST secret does NOT
  exempt requests from captcha: `website/signin.html` (local mode) performs
  password sign-in + recovery with no Turnstile widget (site key empty, #1003),
  so those flows 400 `captcha_failed` regardless of the secret. Use
  `enabled = false` for local form testing until #1003 ships the widget +
  captchaToken pass-through.
