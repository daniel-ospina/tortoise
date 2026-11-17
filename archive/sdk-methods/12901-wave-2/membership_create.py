# Archived sdk-method `membership_create` — REMOVED from the product surface (#11609).
#
# batch:   12901-wave-2
# reason:  Add a user to an org with a role. Body survives PRIVATE as `_membership_create`; recorded destination `add_member` (row 37).
# source:  tortoise/sdk.py @ d741bd4a5a8d45198ec9c8fd2324c93ca746af5e
#
# This file IS the source of record. To restore: copy each block below back into the
# file named in ITS OWN `verbatim from <path>` banner (one symbol can span more than
# one file), then run `uv run python tools/archive_surface.py check` (which fails
# while `archive/` and the live surface disagree).
#
# ⛔ NOT part of the product: not importable from `tortoise`, not served over MCP, and
#    not pointed at by any retired-forward name. Do not import this file.

ARCHIVED_KIND = 'sdk-method'
ARCHIVED_SYMBOL = 'membership_create'
ARCHIVED_BATCH = '12901-wave-2'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = 'd741bd4a5a8d45198ec9c8fd2324c93ca746af5e'

# ── verbatim from tortoise/sdk.py ──

    def membership_create(self, org_id: str, user_id: str, role: str) -> dict:
        """Add a user to an org with a given role.

        Validates role, org existence, and max_users constraint.
        Creates BELONGS_TO edge to Org.
        """
        from datetime import datetime, timezone  # noqa: I001
        from .exceptions import ControlPlaneError

        if role not in ("owner", "admin", "member"):
            raise ControlPlaneError(
                f"Invalid role {role!r}. Must be 'owner', 'admin', or 'member'."
            )

        org = self.org_get(org_id)
        if org is None:
            raise ControlPlaneError(f"Team {org_id!r} not found")

        # Check max_users constraint
        max_users = org.get("max_users")
        if max_users is not None:
            reg = self._get_registry()
            count = reg.query(
                "MATCH (m:Membership {org_id:$tid}) "
                "WHERE m.status = 'active' RETURN count(m)",
                params={"tid": org_id},
            ).result_set[0][0]
            if count >= max_users:
                from tortoise.quota import with_limit_contact  # #5425
                raise ControlPlaneError(with_limit_contact(
                    f"Team at max users ({max_users}). Upgrade to add more."))

        mid = ulid()
        now = datetime.now(timezone.utc).isoformat()  # noqa: UP017
        reg = self._get_registry()
        reg.query(
            "CREATE (m:Membership {id:$id, user_id:$uid, org_id:$tid, "
            "role:$role, status:'active', joinedAt:$now, created_at:$now})",
            params={"id": mid, "uid": user_id, "tid": org_id,
                    "role": role, "now": now},
        )
        # Create BELONGS_TO edge
        reg.query(
            "MATCH (m:Membership {id:$mid}), (t:Team {id:$tid}) "
            "CREATE (m)-[:BELONGS_TO]->(t)",
            params={"mid": mid, "tid": org_id},
        )

        self._audit(org_id, user_id, "membership_create",
                     resource_type="membership", resource_id=mid)
        return {"id": mid, "org_id": org_id, "user_id": user_id, "role": role}
