# Archived sdk-method `sweep_invite_ghost_memberships` — REMOVED from the product surface (#11609).
#
# batch:   12901-wave-2
# reason:  Scheduled repair of membership rows whose invite never completed. Body survives PRIVATE as `_sweep_invite_ghost_memberships`; beta rules it OUR MAINTENANCE; no destination.
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
ARCHIVED_SYMBOL = 'sweep_invite_ghost_memberships'
ARCHIVED_BATCH = '12901-wave-2'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = 'd741bd4a5a8d45198ec9c8fd2324c93ca746af5e'

# ── verbatim from tortoise/sdk.py ──

    def sweep_invite_ghost_memberships(self, *, dry_run: bool = False) -> dict:
        """#1908: one-time backfill sweep for pre-#1880 invite-{iid} ghosts.

        Deletes Membership rows whose user_id matches 'invite-*' when the
        backing Invitation node is TERMINAL — consumed (accepted_at set),
        expired (expires_at past or status='expired'), revoked
        (status='revoked' / 'accepted'), or MISSING (orphaned fake row with
        no Invitation node). Rows backing a still-pending, unexpired invite
        are kept (they are the legit 'invited' placeholder in list_members).

        Idempotent — re-running after a sweep finds nothing. With
        ``dry_run=True`` reports the ghost count without writing (the
        graph-script's --dry-run mode).

        Returns {"found", "ghosts", "deleted"} — found = invite-* rows
        examined, ghosts = rows qualifying for deletion, deleted = rows
        actually removed (0 under dry_run).
        """
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()  # noqa: UP017
        reg = self._get_registry()
        rows = reg.query(
            "MATCH (m:Membership) WHERE m.user_id STARTS WITH 'invite-' "
            "OPTIONAL MATCH (i:Invitation {id: substring(m.user_id, 7)}) "
            "RETURN m.user_id, m.org_id, properties(i)",
        ).result_set
        ghosts: list[tuple[str, str]] = []
        for fake_uid, org_id, inv_props in rows:
            if inv_props is None:
                ghosts.append((fake_uid, org_id))  # orphaned fake row
                continue
            node = dict(inv_props)
            consumed = node.get("accepted_at") is not None \
                or node.get("status") in ("accepted", "revoked", "expired")
            expired = node.get("expires_at") is not None \
                and node["expires_at"] < now
            if consumed or expired:
                ghosts.append((fake_uid, org_id))
        failed = 0
        if not dry_run:
            for fake_uid, org_id in ghosts:
                try:
                    reg.query(
                        "MATCH (m:Membership {org_id:$tid, user_id:$uid}) DELETE m",
                        params={"tid": org_id, "uid": fake_uid},
                    )
                except Exception as _e:
                    # best-effort, mirroring _delete_fake_invite_membership
                    # (#1902 P2) — a transient failure is retried by a re-run.
                    failed += 1
                    _logger.warning(
                        "invite ghost-cleanup failed for %s on %s (%s)",
                        fake_uid, org_id, _e)
        return {"found": len(rows), "ghosts": len(ghosts),
                "deleted": 0 if dry_run else len(ghosts) - failed}
