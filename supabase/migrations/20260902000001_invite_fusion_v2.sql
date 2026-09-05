-- Migration 20260902000001: invite fusion v2 — OTP proof-of-control + mismatch
-- override records (#2003, W7 of epic #1976)
--
-- W7 turns the POST /v1/invites/accept email-mismatch hard-403 into a 3-path
-- fusion choice UNDER AN EXPLICIT v2 Accept header opt-in
-- (application/vnd.tortoise.onboarding+json;version=2 — legacy default 403 is
-- byte-unchanged). Both mismatch-override paths (fuse + accept-with-mismatch)
-- require proof-of-control of the INVITEE email via a 6-digit OTP.
--
-- This migration ONLY adds state columns to public.invitations — the flow,
-- checks, single-use semantics and rate caps live in
-- tortoise/supabase_control.py (invitation_otp_mint / invitation_otp_verify /
-- invitation_accept mismatch_override) and tortoise/hosted_api.py. The code
-- reads the otp_* columns ONLY on the v2 mismatch path, so a schema one
-- migration behind never breaks the legacy accept lane (same fail-soft
-- posture as the #1096 additive-column ladder).
--
-- Columns:
--   otp_hash          text       PBKDF2-HMAC-SHA256(pepper + code) with a
--                                per-code random salt (tortoise.auth
--                                hash_api_key; verified via verify_api_key —
--                                never a deterministic digest). Code never
--                                stored plaintext.
--   otp_expires_at    timestamptz 10-minute proof window (minted fresh on
--                                each send).
--   otp_attempts      int        failed-verify budget (cleared at 5).
--   otp_sent_at       timestamptz last send time (send-cap bookkeeping).
--   otp_verified_at   timestamptz SINGLE-USE consume marker — written at the
--                                ACCEPT COMMIT (the code is consumed
--                                atomically with the token single-use write,
--                                never on a pre-commit 402). The accept seam
--                                verifies the submitted code before the
--                                commit and clears otp_hash here.
--   otp_verified_by   text       user id that proved control.
--   accepted_via      text       'fuse' | 'accept-mismatch' (NULL on the
--                                legacy email-match accept).
--   accepted_mismatch boolean    TRUE when the accept ran on a mismatched
--                                email (never silent — recorded).
--   fused_from_email  text       the original invited email when the accept
--                                overrode the mismatch under another account.
--   expired_by        text       admin user id that force-expired the invite.

ALTER TABLE public.invitations
    ADD COLUMN IF NOT EXISTS otp_hash text,
    ADD COLUMN IF NOT EXISTS otp_expires_at timestamptz,
    ADD COLUMN IF NOT EXISTS otp_attempts integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS otp_sent_at timestamptz,
    ADD COLUMN IF NOT EXISTS otp_verified_at timestamptz,
    ADD COLUMN IF NOT EXISTS otp_verified_by text,
    ADD COLUMN IF NOT EXISTS accepted_via text,
    ADD COLUMN IF NOT EXISTS accepted_mismatch boolean NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS fused_from_email text,
    ADD COLUMN IF NOT EXISTS expired_by text;

-- accepted_via is a closed enum (contract: fuse | accept-mismatch).
ALTER TABLE public.invitations
    DROP CONSTRAINT IF EXISTS chk_invitations_accepted_via;
ALTER TABLE public.invitations
    ADD CONSTRAINT chk_invitations_accepted_via
    CHECK (accepted_via IS NULL OR accepted_via IN ('fuse', 'accept-mismatch'));

-- RLS stays as 0008 (service_role ALL; authenticated reads only the
-- display-safe columns) — the OTP + mismatch state is service-role-only via
-- the same seam that mints/accepts invitations, so no policy change is
-- needed. Column protection is unnecessary: the columns never carry a
-- plaintext capability (otp_hash is PBKDF2-hashed with a per-code salt and
-- the code is never SELECTable by authenticated — the grant from 0008 lists
-- the safe columns explicitly).
