# Archived sdk-method `org_create` — REMOVED from the product surface (#11609).
#
# batch:   12901-final
# reason:  #12901 FINAL batch: org_create left the public SDK surface — privatised (body survives as `_org_create`), no single owner-approved replacement. Frozen here so a mistake is inspected against the code that shipped it (#11609 ruling).
# source:  tortoise/sdk.py @ 424368c05d8ee9b26aa2d77e4c5a93a4b207b58f
#
# This file IS the source of record. To restore: copy each block below back into the
# file named in ITS OWN `verbatim from <path>` banner (one symbol can span more than
# one file), then run `uv run python tools/archive_surface.py check` (which fails
# while `archive/` and the live surface disagree).
#
# ⛔ NOT part of the product: not importable from `tortoise`, not served over MCP, and
#    not pointed at by any retired-forward name. Do not import this file.

ARCHIVED_KIND = 'sdk-method'
ARCHIVED_SYMBOL = 'org_create'
ARCHIVED_BATCH = '12901-final'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = '424368c05d8ee9b26aa2d77e4c5a93a4b207b58f'

# ── verbatim from tortoise/sdk.py ──

    def org_create(self, name: str, *, idempotency_key: str | None = None,
                    mint_key: bool = True, owner_user_id: str | None = None) -> dict:
        """Create an org with its own graph namespace.

        Writes to the control_plane registry graph. Creates a tenant
        graph (org_{name}) for Point/Operator storage.

        Returns {name, graph_name, api_key, id} on first creation; on an
        idempotent re-call (same idempotency_key) returns
        {name, graph_name, id, existing: True} with NO api_key — the caller
        already holds the plaintext from the original creation (#1710).
        mint_key=False (#1716, onboarding sub-org parity) provisions a
        KEYLESS org: no tt_ mint and no api_key hash on the Org node — the
        return dict omits api_key entirely. The org stays keyless until a
        session-key mint (apikey_create / POST /v1/session/key). A minted
        key whose plaintext is never returned is an unrecoverable dead
        credential.

        owner_user_id (#1748, onboarding sub-org parity): when set, the
        user becomes an OWNER member (Membership role=owner/status=active,
        the registry twin of provision_org's membership upsert) so the
        keyless org is reachable by session-key mint / org list / owner
        delete. Without it a keyless org has NO membership — an unmintable,
        undeletable orphan. Default None = no membership (back-compat for
        CLI/MCP/embedded callers with no user context).

        #765 (plan Task 8 — SDK control-plane backend env-gated): the SDK
        control-plane backend stays REGISTRY-BACKED — the
        TORTOISE_CONTROL_PLANE env gate lives at the hosted layer
        (hosted_api.py), and the hosted create-org writers (POST /v1/organizations,
        /v1/agent/signup, /v1/register, onboarding sub-org) route their
        Supabase writes through the atomic provision_org RPC instead of
        this method. Selfhost + embedded (where this SDK runs) have no
        Supabase control plane — the registry IS the control plane there.
        """
        import re, uuid  # noqa: E401, I001
        from datetime import datetime, timezone
        from tortoise.auth import hash_api_key
        from .exceptions import ControlPlaneError

        # Input validation
        if not name or not name.strip():
            raise ControlPlaneError("Team name must not be empty")
        if len(name) > 64:
            raise ControlPlaneError("Team name must be 64 characters or fewer")
        if not re.match(r'^[a-zA-Z0-9][a-zA-Z0-9_ -]*$', name):
            raise ControlPlaneError(
                f"Invalid team name: {name!r}. Use alphanumeric, hyphens, underscores, spaces."
            )

        graph_name = f"org_{name}".replace(' ', '_')
        proj = self._get_proj()
        reg = self._get_registry()
        now = datetime.now(timezone.utc).isoformat()  # noqa: UP017

        # Idempotency — check registry graph for existing org
        if idempotency_key:
            existing = reg.query(
                "MATCH (t:Team {idempotency_key:$ik}) RETURN t.id, t.name",
                params={"ik": idempotency_key},
            ).result_set
            if existing:
                row = existing[0]
                # #1710: return NO api_key here — a key minted at the top of
                # the function is never hashed/persisted on this branch, so
                # returning it handed callers a dead key that fails auth on
                # first use. The plaintext belongs to the original creation.
                return {"name": name, "graph_name": graph_name,
                        "id": row[0], "existing": True}

        # Mint the key only on the CREATE path (after the idempotency check)
        # so the existing branch never mints or persists anything (#1710).
        # #1716: mint_key=False provisions a KEYLESS org — no tt_ mint and
        # no api_key hash on the Org node (the onboarding sub-org path: a
        # minted key whose plaintext is never returned is an unrecoverable
        # dead credential). The org stays keyless until a session-key mint.
        api_key = key_hash = None
        if mint_key:
            api_key = f"tt_{uuid.uuid4().hex}"
            key_hash = hash_api_key(api_key)

        # Duplicate name check
        dup = reg.query(
            "MATCH (t:Team {name:$name}) RETURN count(t) > 0",
            params={"name": name},
        ).result_set[0][0]
        if dup:
            raise ControlPlaneError(f"Team {name!r} already exists")

        tid = ulid()
        # Tier-driven limits from product/pricing.json (decision 1d) — no max_orgs
        # field: multi-org is a user-level capability, NOT a tier limit.
        from tortoise.pricing import tier_limits
        lim = tier_limits("free")  # provision defaults to Free; upgrades = billing epic
        # #1716 keyless: the api_key property is omitted from the Org node
        # (a NULL property is a delete in redisgraph semantics; omitting the
        # attribute + param is the unambiguous keyless shape).
        key_attr = "api_key:$key, " if mint_key else ""
        org_params = {"id": tid, "name": name, "gn": graph_name,
                       "now": now,
                       "max_graphs": lim["max_graphs_per_team"],
                       "max_users": lim["max_users_per_team"],
                       "max_keys": lim["max_api_keys"],
                       "ops": lim["included_write_ops_per_month"],
                       "nodes": lim["max_graph_nodes"]}
        if mint_key:
            org_params["key"] = key_hash
        reg.query(
            "CREATE (t:Team {id:$id, name:$name, " + key_attr
            + "graph_name:$gn, createdAt:$now, tier:'free', "
            + "max_graphs:$max_graphs, max_users:$max_users, "
            + "max_api_keys:$max_keys, ops_allowance:$ops, "
            + "graph_size_cap:$nodes})",
            params=org_params,
        )
        if idempotency_key:
            reg.query(
                "MATCH (t:Team {id:$id}) SET t.idempotency_key = $ik",
                params={"id": tid, "ik": idempotency_key},
            )
        try:
            # #9157: the eager-init CREATE is issued on a §17.1 GUARDED handle
            # bound to org_{name} (the projection itself is bound to another
            # graph), through `_os.run_eager_init` — the declared route. Before
            # this, `proj.db.select_graph(graph_name).query(...)` was §17.1's
            # escape (3): one hop below every door, so the write was
            # unjournalled AND undeclared and the onboarding lane read as if it
            # covered its own class.
            from tortoise.projection import guarded_write_handle
            _init_graph = guarded_write_handle(proj, graph_name)
            # #2001 (W5): eager OnboardingState init in the same statement as
            # TeamMeta (graph-side atomicity). compact = creator's prior
            # memberships > 0 (registry Membership nodes); fork inherited from
            # the creator's EARLIEST prior org's OnboardingState.fork with
            # 'self' fallback (never re-asks the fork card); None when the
            # creator has no prior orgs (fork card asked exactly once) or no
            # user context (CLI/embedded mint).
            from tortoise.onboarding import state as _os
            init_fork: str | None = None
            init_compact = False
            prior_ids: list[str] = []
            if owner_user_id:
                rows = reg.query(
                    "MATCH (m:Membership {user_id:$uid, status:'active'}) "
                    "WHERE m.org_id <> $org "
                    "RETURN m.org_id, m.created_at ORDER BY m.created_at",
                    params={"uid": owner_user_id, "org": tid},
                ).result_set
                prior_ids = [r[0] for r in rows]
                prior_fork = None
                if prior_ids:
                    try:
                        # registry graphs are org_{name} — resolve the
                        # earliest prior org's graph before reading its fork.
                        _prior = reg.query(
                            "MATCH (t:Team {id:$id}) RETURN t.graph_name",
                            params={"id": prior_ids[0]},
                        ).result_set
                        if _prior and _prior[0][0]:
                            prior_fork = _os.read_prior_org_fork(
                                proj.db.select_graph(_prior[0][0]),
                                prior_ids[0])
                    except Exception:
                        prior_fork = None
                init_fork, init_compact = _os.resolve_init_fork_compact(
                    bool(prior_ids), prior_fork)
            _init_q, _init_p = _os.eager_init_query(
                "CREATE (:TeamMeta {name:$name, created:$now})",
                {"name": name, "now": now},
                org_id=tid, fork=init_fork, compact=init_compact)
            # #3390 WRITE-AHEAD: journal the intended org_{name} graph
            # BEFORE the TeamMeta CREATE that materializes it. The old order
            # (CREATE then journal) left an UNOWNED graph whenever the
            # process died in the gap — no live peer could attribute it, so a
            # scope=None sweep could DETACH it and a journal rebuild diverged
            # from live (live != rebuild). Journal-first makes an unjournaled
            # graph impossible by construction; a journaled-but-never-created
            # name is harmless (the sweep's GRAPH.DELETE on an absent graph is
            # the is_missing_graph_error success family). No rollback removes
            # the line — it is the ownership tombstone that guarantees cleanup.
            # No-op outside test sessions (journal env absent).
            from tortoise.projection import journal_mint_write_ahead
            journal_mint_write_ahead(graph_name)
            _os.run_eager_init(_init_graph, _init_q, _init_p)
            # Graph node (org→graph 1:N, product ontology): the default graph
            self._graph_create(tid, "default", kind="default", namespace=graph_name)
            # #1748: the owner Membership for the session user — INSIDE the
            # rollback-protected try so a membership failure tears the Org
            # node down (a keyless org with no membership is an unmintable,
            # undeletable orphan). Mirrors the Supabase provision_org
            # membership upsert (role=owner, status=active, user_id=session
            # user) and membership_create (BELONGS_TO edge).
            if owner_user_id:
                self._membership_create(tid, owner_user_id, "owner")
        except Exception:
            try:  # noqa: SIM105
                reg.query("MATCH (t:Team {id:$id}) DETACH DELETE t",
                          params={"id": tid})
            except Exception:
                pass
            raise

        self._audit(tid, None, "team_create", resource_type="team", resource_id=tid)
        result = {"name": name, "graph_name": graph_name, "id": tid}
        if mint_key:
            result["api_key"] = api_key  # plaintext delivered exactly once
        return result
