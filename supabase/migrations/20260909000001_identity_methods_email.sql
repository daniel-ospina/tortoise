-- 20260909000001_identity_methods_email.sql
--
-- Add `email` field to each method returned by user_identity_inventory
-- so the dashboard can show the actual email address for each login method.
-- ============================================================================
-- The email is extracted from identity_data->>'email' (GoTrue identities
-- stores the user's OAuth email there). For email-provider identities,
-- provider_id IS the email; for OAuth (Google/GitHub), the email lives
-- in identity_data. COALESCE handles both cleanly.
-- ============================================================================

CREATE OR REPLACE FUNCTION public.user_identity_inventory(p_user_id uuid)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
    v_email            text;
    v_email_confirmed  timestamptz;
    v_encrypted_pwd    text;
    v_has_password     boolean;
    v_email_method     boolean;
    v_oauth_methods    int;
    v_login_methods    int;
    v_methods          jsonb;
    v_keys_tier        int;
BEGIN
    SELECT email, email_confirmed_at, encrypted_password
      INTO v_email, v_email_confirmed, v_encrypted_pwd
      FROM auth.users WHERE id = p_user_id;

    IF v_email IS NULL AND v_email_confirmed IS NULL AND v_encrypted_pwd IS NULL
       AND NOT EXISTS (SELECT 1 FROM auth.identities i WHERE i.user_id = p_user_id) THEN
        -- Unknown user (or zero-method user) — never an error; 0 methods.
        RETURN jsonb_build_object(
            'methods', '[]'::jsonb, 'has_password', false,
            'email_method', false, 'login_methods', 0,
            'keys_tier', 0, 'banner', jsonb_build_object('show', false));
    END IF;

    -- has_password: OAuth-created users have encrypted_password = '' OR NULL
    -- (hosted observed: NULL) — either must be FALSE.
    v_has_password := v_encrypted_pwd IS NOT NULL AND v_encrypted_pwd <> '';

    -- email_method: recovery capability off auth.users.email + confirmation
    -- (NOT the email identity row — hosted check (b) = NO: OAuth signups have
    -- no email identity rows; the identity-row conjunct would undercount).
    v_email_method := (v_email IS NOT NULL AND v_email_confirmed IS NOT NULL) OR v_has_password;

    -- login_methods: OAuth/phone methods + email_method. NEVER
    -- count(provider <> 'email') — COUNT(expr) counts non-NULL rows and the
    -- comparison is FALSE (not NULL) for email rows → double-count.
    SELECT count(*) INTO v_oauth_methods
      FROM auth.identities i
     WHERE i.user_id = p_user_id AND i.provider NOT IN ('email');

    v_login_methods := v_oauth_methods + CASE WHEN v_email_method THEN 1 ELSE 0 END;

    -- #1765: methods array with email from identity_data.
    -- email-provider identities store the email in provider_id;
    -- OAuth identities store it in identity_data->>'email'.
    -- COALESCE handles both cases cleanly.
    SELECT COALESCE(jsonb_agg(jsonb_build_object(
               'id', i.id,
               'provider', i.provider,
               'provider_id', i.provider_id,
               'email', COALESCE(i.identity_data->>'email', i.provider_id),
               'email_confirmed_at', v_email_confirmed)
             ORDER BY i.created_at), '[]'::jsonb)
      INTO v_methods
      FROM auth.identities i
     WHERE i.user_id = p_user_id;

    -- keys tier: human-minted keys (created_by = this user uuid), non-revoked
    -- and enabled. anon-/st_/reg-/client/NULL attribution is EXCLUDED (agent
    -- principals + bootstrap keys are not a human login method).
    SELECT count(*) INTO v_keys_tier
      FROM public.api_keys k
     WHERE k.created_by = p_user_id::text AND k.revoked_at IS NULL AND k.enabled;

    RETURN jsonb_build_object(
        'methods', v_methods,
        'has_password', v_has_password,
        'email_method', v_email_method,
        'login_methods', v_login_methods,
        'keys_tier', v_keys_tier,
        'banner', jsonb_build_object(
            'show', v_login_methods <= 1
                    AND NOT (v_email IS NOT NULL AND v_email_confirmed IS NOT NULL AND v_has_password)));
END;
$$;

REVOKE ALL ON FUNCTION public.user_identity_inventory(uuid) FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.user_identity_inventory(uuid) TO service_role;
