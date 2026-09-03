---
title: "W7 — Invite-accept fusion (3-path + OTP both paths + atomic new-user accept + pending affordance) — implementation plan"
type: engineering
domain: capability
doc_status: draft
created: 2026-09-02
ownedBy: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: tortoise
---

# Implementation Plan — #2003 (W7): invite-accept fusion (3-path + OTP both paths + atomic new-user accept + pending affordance)

> **Scope anchors:** issue #2003 body (O/I/T + verification checklist) · epic #1976 `06-plan.md` §1 J3/J4, §2 WF-3, §4 DM-4, §6 I-6, §7 DE2E-7/DE2E-8 · test-design #1992 surface 12 (`04-test-design.md`).
> **W7/W10 split (pinned):** W7 ships the invitee accept mechanics + member_progress WRITE SURFACE usage + inline-skippable affordance MECHANICS (armed per-member slot; skip writes nothing). The invitee post-accept mini-onboarding UX + RBAC tiers are W10 — OUT OF SCOPE.
> **M6 (never re-create):** all seven `/v1/invites*` endpoints exist and are PRESERVED byte-compatible on the legacy lane. W7 only ADDS (v2 contract + OTP + resend/expire + accept-side arming).
> **Research intake:** epic 02-research-brief.md §M10 (fusion UNION + OTP proof-of-control), §M6 (invite infra inventory); plan/DE2E sections above; hosted_api.py invite + W1/W5 code read (accept auth = invite token + session-JWT email match; NO pre-existing OTP/verification-code infra anywhere in the repo — OTP is genuinely new).
> **Repository reality:** HTTP integration tests run the REGISTRY lane (selfhost; `is_supabase_enabled()` false — no Supabase creds in CI). Supabase-lane seams are implemented over the PostgREST cp abstraction and unit-tested against `tests/fake_control_plane.py` (generic row store — new columns/tables work without schema). No SQL migration is executed by CI (pglite list is stale at 20260827000001); migrations are deploy-time artifacts, written conservatively.

---

## 1. Problem restatement (what W7 actually changes on the wire)

Today `POST /v1/invites/accept` hard-403s on ANY email mismatch between the session JWT and the invite. W7 (v2 opt-in only):

1. **3-path fusion choice, fuse default, never silent** — opted-in client on mismatch gets a discovery payload describing the three choices; the client must pick. Never an automatic merge.
2. **OTP proof-of-control on BOTH mismatch-override paths** (fuse AND accept-with-mismatch) — closes the invite-hijack + privilege-accumulation vectors (risk register High).
3. **Atomic new-user accept** — new user's accept = one request: token consumed + membership created + onboarding member-slot armed. No durable "created but not accepted" state; consumed-token re-click idempotent.
4. **Legacy-403 preserved byte-unchanged** without the `Accept: application/vnd.tortoise.onboarding+json;version=2` opt-in.
5. **Pending-invites + admin resend/expire** — new owner/admin endpoints; existing pending list/accept/decline untouched.
6. **member_progress writes without faking org completion** — accept arms the invitee's per-member slot in the org's OnboardingState node (`member_progress {user_id: []}`); W5's checkpoint stays the write surface; member entries NEVER advance org-level steps (W5 `write_member_progress` map-merge is separate from COMPLETED_STEP edges — by construction).

### Fusion union semantics (honest scope)
- Registry/selfhost lane: no per-email identity directory exists (users are opaque `user_id`s on Membership/APIKey nodes) — an "existing invitee account" is not representable. `fuse` therefore executes the mismatch-override accept UNDER THE CURRENT ACCOUNT with the invite recording `accepted_via='fuse'`, `fused_from_email=<invite email>`, OTP proof. The membership + node arming are identical to accept-mismatch; the distinction is recorded and surfaced.
- Supabase/hosted lane: same seam over the cp tables (invitations columns from a deploy-time migration); true auth-identity linking (both emails → one login, auth.admin) is auth-schema work owned with W10 RBAC — recorded on the invite, not silently deferred.
- The SECURITY property (OTP-gated, never silent, never un-OTP'd) is identical on both lanes and is what the regression tests pin.

---

## 2. Contract (pinned)

### v2 opt-in
Header on `POST /v1/invites/accept`: `Accept: application/vnd.tortoise.onboarding+json;version=2`.

### New endpoint — `POST /v1/invites/otp` (session-authed)
Body `{"token": str}` → 200 `{"status": "otp_sent", "expires_in_s": 600}`.
- Resolve invite by token (pending + unexpired) — unknown/consumed/expired → 400 `"Invalid or expired invite token"` / `"Invite token expired"` (matches accept's errors).
- Session email == invite email → 400 `{"error_code": "otp_not_required"}` (email-match needs no proof — logging in as the invitee IS the proof).
- Session without email → 422.
- 6-digit code; stored HASHED on the invitation (`otp_hash` via `hash_api_key`), `otp_expires_at = now+600s`, `otp_attempts = 0`, `otp_sent_at`. Re-issue rotates (invalidates prior). Code NEVER returned by the API — emailed best-effort via new `email_notify.send_otp_email` (monkeypatched in tests).
- Rate caps (env-tunable, `RATE_LIMIT_DISABLED=1` opt-out): per-invitation sends (5 / 15 min), per-IP (10 / h), global (200 / h) — sliding-window buckets mirroring the invite-accept limiter (`_check_ip_bucket_rate_limit` helper).

### `POST /v1/invites/accept` (mismatch branch only — match path 100% unchanged)
| Condition | Result |
|---|---|
| No v2 header | **legacy 403 byte-unchanged** (`"Invite email does not match this account"`) |
| v2 + no `path` | 409 `{"error_code":"invite_email_mismatch","detail":...,"choice":{"paths":["fuse","accept-mismatch"],"default_path":"fuse","otp_required":true,"invited_email":y}}` — the 3-path presentation |
| v2 + `path` + no `otp` | 403 `{"error_code":"invite_mismatch_otp_required",...}` (BOTH override paths gated) |
| v2 + `path` + wrong/expired `otp` | 403 `{"error_code":"invite_otp_invalid",...}` (attempts++; 5 failures clears the code) |
| v2 + `path` + valid `otp` | OTP consumed (single-use) → membership under current account; invite records `accepted_at/accepted_by/otp_verified_at/otp_verified_by/accepted_via/fused_from_email`; ghost membership cleanup; 200 `{"team_id","role","accepted_via":"fuse"|"accept-mismatch","mismatch":{"invited_email":y,"recorded":true}}` |

### Admin resend / expire (owner/admin only, mirroring `DELETE /v1/invites/{id}` RBAC)
- `POST /v1/invites/{invitation_id}/resend?team_id=...` → rotates the token (new plaintext returned once + hash updated + email re-sent best-effort), refreshes expiry to +7d; consumed/revoked → 409; rate-capped (max 5 resends/day per invitation, env).
- `POST /v1/invites/{invitation_id}/expire?team_id=...` → pending invite becomes `status='expired'` + `expires_at=now` (link dies, leaves pending lists, frees the Pro capacity seat) + ghost membership cleanup; consumed → 409.

### Accept-side arming (member_progress mechanics)
Every successful accept (match + mismatch-override, registry lane; mirrored seam): ensure the org's OnboardingState node exists (create-on-write seam, `ensure_onboarding_state_node`) and idempotently write the invitee's member slot `member_progress {user_id: []}` (`write_member_progress`). NEVER writes org-level COMPLETED_STEP edges; NEVER evaluates org completion for the acceptor. This is the "inline-skippable affordance mechanics": armed slot + W5 checkpoint writes; skip = no write; org completion unchanged (DE2E-8 And-clause).

---

## 3. Implementation steps

### Step 1 — `tortoise/email_notify.py`: `send_otp_email`
Mirror `send_invite_email` (budget reserve/refund + `_skip_channel` + async `_send_invite_attempt`-style send + `on_sent` callback). Copy: 6-digit code + team name + 10-min expiry. Signature: `send_otp_email(team_name, invitee_email, code, on_sent=None)`.

### Step 2 — `tortoise/supabase_control.py`: seam (mirrors the registry lane; unit-tested on FakeControlPlane)
- `invitation_otp_mint(cp, invitation_id, code_hash, expires_at, sent_at)` — PATCH invitations row (id filter) setting `otp_hash/otp_expires_at/otp_attempts=0/otp_sent_at`; row-must-match guard (returns False when the invitation vanished).
- `invitation_otp_verify(cp, invitation_id, code_hash)` → `("ok"|"invalid"|"expired"|"no_otp")`; on ok clears the hash/expiry + sets `otp_verified_at` (single-use); invalid increments attempts, ≥5 clears the code.
- `invitation_accept_mismatch_v2(cp, token, user_id, user_email, path, otp_verified)` — the mismatch-override accept: reuse `invitation_accept`'s checks (pending/expiry/existing-member/team kill-switches/free-cap/max_users) minus the email-match 403, + requires a verified OTP row, + PATCHes the invite with the mismatch/OTP/fusion record, + resurrect-or-insert the membership with `invited_email`. Returns the standard `{team_id, role}` + record fields.
- `invitation_resend(cp, invitation_id, team_id, actor_user_id)` — owner/admin re-check is done by the caller (hosted_api) like rescind; seam validates pending + not-accepted, rotates `lookup_hash` to a fresh token (returned once) + bumps `expires_at`, returns the token.
- `invitation_expire(cp, invitation_id, team_id)` — pending → `status='expired'`, `expires_at=now`; idempotent-ish guards.

### Step 3 — `supabase/migrations/20260902000001_invite_fusion_v2.sql` (deploy-time; conservative, pglite-styled)
Add to `public.invitations`: `otp_hash text`, `otp_expires_at timestamptz`, `otp_attempts integer NOT NULL DEFAULT 0`, `otp_sent_at timestamptz`, `otp_verified_at timestamptz`, `otp_verified_by text`, `accepted_via text`, `accepted_mismatch boolean NOT NULL DEFAULT false`, `fused_from_email text`. Constraint check `accepted_via IN ('fuse','accept-mismatch')`. (Only if pglite validation passes locally is it added to `supabase/tests/pglite/validate.mjs`; otherwise it stays a normal reviewed deploy artifact like the other 2026xxxx migrations that postdate the pglite list.)

### Step 4 — `tortoise/hosted_api.py`
1. `_onboarding_v2(request)` — Accept-header sniff (mimetype `application/vnd.tortoise.onboarding+json` + `version=2`).
2. OTP send/verify registry helpers over the registry Invitation node (`_registry_invite_by_token` refactor reuse; fields `otp_hash/otp_expires_at/otp_attempts/otp_sent_at/otp_verified_at/by`), plus `_otp_rate_limit(request, token_key or ip)` sliding windows + env knobs + `RATE_LIMIT_DISABLED` opt-out.
3. `POST /v1/invites/otp` endpoint (supabase seam `invitation_otp_mint` OR registry inline).
4. `POST /v1/invites/accept` — split the mismatch branch: keep legacy 403 when not opted-in; add v2 discovery + OTP-gated override via a shared `_accept_mismatch_v2(...)` lane-parameterized executor. Match path untouched. OTP verify BEFORE any write; single-use consume; under the existing `_team_create_lock` + `_invite_team_lock` + capacity/free-cap pre-checks (reuse the by-id accept's structure — non-consuming 402s stay non-consuming).
5. `_arm_invitee_member_progress(org_id, user_id)` after successful accept (registry lane; supabase lane: node arming is graph-side — do it through `_make_sdk(namespace=team_id)` like W5 does from hosted_api; fail-soft on graph error, never masks accept).
6. `POST /v1/invites/{invitation_id}/resend` + `POST /v1/invites/{invitation_id}/expire` (owner/admin; registry + supabase lanes; resend rate cap).

### Step 5 — Tests
- `tests/test_invite_fusion_http.py` (registry lane; patched-embedded SDK pattern of `test_invites_http.py`; runs in BOTH lanes — no docker gate):
  - legacy 403 byte-unchanged golden (opt-in absent → exact `403` + `detail == "Invite email does not match this account"`).
  - 3-path discovery shape (v2 + mismatch + no path → 409, default fuse, otp_required, both paths listed).
  - OTP: send → capture code via monkeypatched `email_notify.send_otp_email`; verify wrong code → 403 blocked; correct → accept proceeds; code single-use (replay blocked); expiry (backdate) → blocked; 5 failed attempts clears.
  - fuse path without OTP → 403 `otp_required`; accept-mismatch without OTP → 403 (BOTH paths gated — DE2E-7).
  - fuse + OTP → membership under current account, invite records `accepted_via='fuse'` + `fused_from_email` + `otp_verified_at`; token single-use (2nd accept → 400/409).
  - accept-mismatch + OTP → membership + `accepted_mismatch`.
  - match path (no mismatch) unchanged one-click accept; already-member 409 preserved.
  - admin resend → new token works on accept + old token dead; rate cap 429; RBAC 403 for member.
  - admin expire → invite leaves pending list + link 400/404 + seat freed for Pro capacity; ghost membership cleanup.
  - member arming: accept → OnboardingState node exists (create-on-write), `member_progress[user_id] == []`, org-level completed steps unchanged (no faked team-named/decide edges for the org), node status not 'complete'; checkpoint member write still works and never advances org steps.
- `tests/test_invite_fusion_docker.py` (docker lane; module-level skip mirroring `test_onboarding_state_split.py`): DE2E-8-style journey on the real graph — register owner (unique email) → seed owner Membership → invite a second email → mismatch accept via OTP → assert node + member_progress + org steps via `onboarding.state` reads; consumed-token replay idempotent ("already in org"/invalid); capacity 402 non-consuming preserved.
- Seam unit tests appended to `tests/test_supabase_control.py` (OTP mint/verify semantics over FakeControlPlane + `invitation_resend`/`invitation_expire` + mismatch-v2 accept row effects).
- `config/ci-surfaces.yml`: add the two new files under `onboarding:`.
- `tests/test_markers.py`: add `ROUTED_NAMESPACES` entries `{"registry": "prod-coupled"}` for the new files (they use the `registry` namespace literal).
- Ruff 0.16.4: RUF059 active — underscore-prefix unused loop vars (`for iid, ...` → `_` where unread).

### Step 6 — Local verification
```bash
TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' uv run pytest \
  tests/test_invite_fusion_http.py tests/test_invite_fusion_docker.py \
  tests/test_invites_http.py tests/test_invites_email_http.py tests/test_onboarding_state_split.py -v
TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_invite_fusion_http.py tests/test_invites_http.py -v
uv run ruff check <changed files>
TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' uv run pytest tests/test_supabase_control.py -v
```

### Step 7 — Commit/PR per commit-workflow (registry path `<worktree>::<file>`), review record at final head, auto-merge.

---

## 4. Risks & mitigations
- **Hosted seam not end-to-end runnable in CI** (no Supabase creds) → seam logic unit-tested on FakeControlPlane with the same interface the real PostgREST client uses; migration is deploy-time, conservative, mirrors existing invitation columns' style.
- **Legacy 403 drift** → golden byte-equality test pins the legacy response; match path untouched (existing 200-byte-equality tests in test_invites_http.py still pass).
- **OTP forgery/brute force** → hashed at rest, 6-digit space + 5-attempt cap + 10-min expiry + single-use + per-IP/send/global rate caps; code never returned by the API.
- **Concurrency** → OTP verify + accept reuse the per-user/per-team locks; capacity pre-checks stay non-consuming.
- **member_progress faking org completion** → by construction member entries are user-scoped map-merge keys; org steps only come from COMPLETED_STEP edges; docker test asserts org steps unchanged after member writes.
