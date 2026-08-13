"""GoTrue Admin API helpers for live e2e cleanup (#801).

Stdlib-only (urllib). (httpx IS a base dependency, but a zero-dep helper is
simpler and keeps the e2e extras unbloated.) All helpers are BEST-EFFORT:
cleanup failures are logged, never raised, so a hygiene failure can never
fail the test that created the resource.

Reference: GET /auth/v1/admin/users?filter=<email> (ILIKE email search),
DELETE /auth/v1/admin/users/{id} — Authorization: Bearer <service_role_key>.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request


def delete_user_by_email(base_url: str, service_key: str, email: str) -> bool:
    """Delete the auth user with the exact given email (no-op when absent).

    Cascades to team_memberships (FK ON DELETE CASCADE, migration 0001).
    Returns True if a user was deleted."""
    headers = {
        "Authorization": f"Bearer {service_key}",
        "apikey": service_key,
        "Accept": "application/json",
    }
    try:
        req = urllib.request.Request(
            f"{base_url}/auth/v1/admin/users?filter={email}", headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        print(f"[supabase_admin] list users failed: HTTP {e.code} {e.reason}")
        return False
    except Exception as e:  # network/parse — best-effort
        print(f"[supabase_admin] list users failed: {e!r}")
        return False

    deleted = False
    for user in data.get("users", []):
        if user.get("email") != email:
            continue
        try:
            req = urllib.request.Request(
                f"{base_url}/auth/v1/admin/users/{user['id']}",
                method="DELETE", headers=headers)
            with urllib.request.urlopen(req, timeout=15):
                deleted = True
            print(f"[supabase_admin] deleted user {user['id']} ({email})")
        except urllib.error.HTTPError as e:
            print(f"[supabase_admin] delete {user['id']} failed: HTTP {e.code} {e.reason}")
        except Exception as e:
            print(f"[supabase_admin] delete {user['id']} failed: {e!r}")
    if not deleted:
        print(f"[supabase_admin] no user found for {email} (already cleaned up?)")
    return deleted
