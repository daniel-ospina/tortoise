# Archived sdk-method `apikey_create` — REMOVED from the product surface (#11609).
#
# batch:   12901-wave-2
# reason:  Mint a control-plane API key. Body survives PRIVATE as `_apikey_create`; recorded destination `create_key` (row 34).
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
ARCHIVED_SYMBOL = 'apikey_create'
ARCHIVED_BATCH = '12901-wave-2'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = 'd741bd4a5a8d45198ec9c8fd2324c93ca746af5e'

# ── verbatim from tortoise/sdk.py ──

    def apikey_create(self, org_id: str, created_by: str,
                      *, graph_id: str | None = None,
                      scopes: list | None = None,
                      created_by_key_id: str | None = None,
                      delegation_depth: int | None = None,
                      prefix: str = "tt_",
                      name: str | None = None,
                      created_via: str | None = None,
                      expires_at: str | None = None) -> dict:
        """Generate an API key for an org.

        Stores SHA-256 hash (never plaintext). Plaintext returned once.

        C1 (#2110) tenancy kwargs (all optional — absent = legacy shape,
        back-compat for existing callers): graph_id (NULL = org-wide key
        → default graph), scopes (FLAT allowlist, default []), mint lineage
        (created_by_key_id + delegation_depth; 0 = minted cannot-escalate,
        NULL = owner-minted). C2 (#2111): ``prefix`` (default "tt_") lets
        the provisioning service mint tk_ per-graph keys (epic vocabulary);
        existing callers unchanged. C3 (#2112): ``name`` (user-facing label,
        20260825000001 parity — the hosted create_api_key lane passes it)
        and ``created_via`` (mint-source classification — "provisioned" /
        "agent_signup" etc.) ride the node as optional props; absent =
        legacy nodes without them. #2426: optional ``expires_at`` (ISO
        timestamp, None = never) rides the same optional-props pattern — the
        registry node is graph-property-additive, and the auth/expiry filter
        (hosted_api ~1558-1570) and cap predicates already read the prop on
        LEGACY nodes (expires_at absent = NULL = never), so an expiring mint
        is a pure CREATE-side addition.

        Registry-side invariant (code-review #2b, mirrors the Supabase DB
        CHECK chk_minted_key_no_escalation): a MINTED key (delegation_depth
        = 0) can never hold escalation scopes (graphs:create/delete,
        keys:manage, team:manage) — only owner-minted keys may. The app
        mint (_mint_graph_key) already ∩ _MINTABLE_SCOPES, but apikey_create
        is a public SDK method (graph-scripts, C3 drift surface): the check
        here keeps selfhost parity with the hosted DB invariant once C5
        flips deleg=0 dormancy off and starts honoring scopes.
        """
        import uuid  # noqa: I001
        from datetime import datetime, timezone
        from tortoise.auth import hash_api_key
        from .exceptions import ControlPlaneError

        _ESCALATION_SCOPES = {"graphs:create", "graphs:delete",
                              "keys:manage", "team:manage"}
        if delegation_depth == 0 and scopes and any(
                s in _ESCALATION_SCOPES for s in scopes):
            raise ControlPlaneError(
                "Minted keys (delegation_depth=0) cannot hold escalation "
                "scopes: " + ",".join(sorted(
                    _ESCALATION_SCOPES & set(scopes))) + ".",
            )

        org = self.org_get(org_id)
        if org is None:
            raise ControlPlaneError(f"Team {org_id!r} not found")

        api_key = f"{prefix}{uuid.uuid4().hex}"
        key_hash = hash_api_key(api_key)
        key_prefix = api_key[:10]
        kid = ulid()
        now = datetime.now(timezone.utc).isoformat()  # noqa: UP017

        reg = self._get_registry()
        # C1 (#2110): include non-None tenancy props in the CREATE (absent =
        # old callers unchanged; old registry nodes without the props resolve
        # with safe defaults). C3 (#2112): name/created_via ride the same
        # optional-props pattern.
        extra = ""
        params = {"id": kid, "tid": org_id, "kh": key_hash,
                  "kp": key_prefix, "cb": created_by, "now": now}
        if graph_id is not None:
            extra += ", graph_id:$gid"; params["gid"] = graph_id  # noqa: E702 (baseline #1503)
        if scopes:
            extra += ", scopes:$sc"; params["sc"] = list(scopes)  # noqa: E702 (baseline #1503)
        if created_by_key_id is not None:
            extra += ", created_by_key_id:$cbk"; params["cbk"] = created_by_key_id  # noqa: E702 (baseline #1503)
        if delegation_depth is not None:
            extra += ", delegation_depth:$dd"; params["dd"] = delegation_depth  # noqa: E702 (baseline #1503)
        if name is not None:
            extra += ", name:$nm"; params["nm"] = name  # noqa: E702 (baseline #1503)
        if created_via is not None:
            extra += ", created_via:$cv"; params["cv"] = created_via  # noqa: E702 (baseline #1503)
        if expires_at is not None:
            extra += ", expires_at:$ea"; params["ea"] = expires_at  # noqa: E702 (baseline #1503)
        reg.query(
            "CREATE (k:APIKey {id:$id, org_id:$tid, key_hash:$kh, "
            "key_prefix:$kp, created_by:$cb, created_at:$now"
            + (extra or "") + "})",
            params=params,
        )
        # BELONGS_TO edge
        reg.query(
            "MATCH (k:APIKey {id:$kid}), (t:Team {id:$tid}) "
            "CREATE (k)-[:BELONGS_TO]->(t)",
            params={"kid": kid, "tid": org_id},
        )

        self._audit(org_id, created_by, "apikey_create",
                     resource_type="apikey", resource_id=kid)
        return {"id": kid, "key_prefix": key_prefix, "api_key": api_key,
                "org_id": org_id, "created_at": now}
