# Archived sdk-method `apikey_verify` — REMOVED from the product surface (#11609).
#
# batch:   12901-wave-2
# reason:  Verify a presented key against stored hashes. Body survives PRIVATE as `_apikey_verify`; beta files it under CONSOLE PLUMBING; no destination.
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
ARCHIVED_SYMBOL = 'apikey_verify'
ARCHIVED_BATCH = '12901-wave-2'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = 'd741bd4a5a8d45198ec9c8fd2324c93ca746af5e'

# ── verbatim from tortoise/sdk.py ──

    def apikey_verify(self, key_plaintext: str) -> dict | None:
        """Verify an API key against stored hashes.

        Returns {org_id, key_id} if valid, None if not found or revoked.
        C2 (#2111): also returns delegation_depth + scopes when present on
        the node (MCP's OrgResolutionMiddleware rejects deleg=0 minted
        keys — the REST surface is gated; MCP must not be the fail-open
        lane for handed-out per-graph keys). Uses salted-hash verification
        (per-key salt means exact-hash lookup never matches — see #130,
        #139). #1709: expires_at filtering with NULL-as-never-expires
        semantics (mirrors the REST path #742 + the agent_signup mint,
        which now writes expires_at:null) — a legacy selfhost key without
        the prop must keep authenticating.

        #2300 (C5 registry-lane parity): a C1/C2 graph-bound key (graph_id
        set on the node) also resolves graph_id + graph_namespace (the
        Graph node's namespace) — the SAME C1 tenancy fields REST's
        registry lane and the Supabase lane (resolve_api_key) carry, so
        MCP's OrgResolutionMiddleware routes a per-graph key to ITS graph
        and the org-surface tool gates can reject it. Missing/drifted
        Graph node → namespace None (graph-delete revokes the graph's keys,
        C3, so a real deleted graph never reaches here) — mirrors REST's
        registry lane, which also resolves None without failing the hash
        auth.
        """
        from datetime import datetime, timezone as _tz  # noqa: I001
        now_iso = datetime.now(_tz.utc).isoformat()  # noqa: UP017
        matches = [
            p for p in self._verify_hashed_lookup("APIKey", "key_hash", key_plaintext)
            if p.get("revoked_at") is None
            and (p.get("expires_at") is None or p.get("expires_at") > now_iso)
        ]
        if matches:
            m = matches[0]
            delegation_depth = m.get("delegation_depth")
            scopes = m.get("scopes") or []
            # C5 #2114 (parity): REST's registry-lane resolution derives the
            # D2 owner class (deleg NULL + scopes [] → legacy_full_access)
            # at hosted_api.py:5414 — apikey_verify is the MCP registry lane
            # and MUST carry the same field or the C5 scope gate 403s every
            # tt_/legacy owner key on the selfhost MCP surface.
            # #2300 (C5 registry-lane parity): a C1/C2 graph-bound key
            # (graph_id on the node) ALSO resolves graph_id/graph_namespace
            # (the Graph node's namespace) — the same C1 tenancy fields REST's
            # registry lane and the Supabase lane carry, so MCP's
            # OrgResolutionMiddleware routes the key to ITS graph and the
            # org-surface tool gates can reject it. A missing Graph node →
            # namespace None (graph-delete revokes the graph's keys — C3 — so
            # a real deleted graph never reaches here; mirrors REST's registry
            # lane, which resolves None without failing the hash auth).
            graph_namespace = None
            graph_id = m.get("graph_id")
            if graph_id:
                g_rows = self._get_registry().query(
                    "MATCH (g:Graph {id:$gid, org_id:$tid}) "
                    "RETURN g.namespace",
                    params={"gid": graph_id, "tid": m["org_id"]},
                ).result_set
                graph_namespace = (
                    g_rows[0][0] if (g_rows and g_rows[0][0]) else None)
            return {"org_id": m["org_id"], "key_id": m["id"],
                    "graph_id": graph_id,
                    "graph_namespace": graph_namespace,
                    "delegation_depth": delegation_depth,
                    "scopes": scopes,
                    "legacy_full_access": (delegation_depth is None)
                    and (scopes == []),
                    # #2600: RAW key creator rides the resolved dict (the MCP
                    # middleware's UUID-gated `actor_user_id` alias reads it —
                    # sdk cannot import supabase_control, so the gate lives in
                    # the consuming seam). Additive; None for legacy keys that
                    # predate created_by.
                    "created_by": m.get("created_by")}
        return None
