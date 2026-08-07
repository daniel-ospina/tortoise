# Deploy + Verification Runbook — hosted embeddings (#160, #566)

The #160 embedding pipeline is **already merged and auto-deployed**: 
`.github/workflows/deploy-hosted.yml` deploys on every push to main touching
`tortoise/**`, `Dockerfile.hosted`, `entrypoint.sh`, `requirements.txt`,
`pyproject.toml`, or `fly.toml` (uses `FLY_API_TOKEN` + `FALKORDB_CLOUD_URI`
secrets). This runbook covers the **manual verification + backfill** steps that
automation cannot do.

## 1. Confirm the deploy is live

```bash
curl -s https://api.premiselabs.co/health        # expect HTTP 200 + app info
curl -s -H "Authorization: Bearer $FASTAPI_INTERNAL_KEY" \
  https://api.premiselabs.co/health              # internal probe
```

(From the dev machine the Fly origin may be firewalled — use
`https://api.premiselabs.co` as the canonical origin.)

## 2. Verify embeddings are being written

New Points must get embeddings at creation (model pre-warmed at boot — the
`#516` cache-path fix made build and runtime agree on `/app/model`).

```bash
# On the Fly machine / a host with FALKORDB_CLOUD_URI access:
falkordb-cli -h "$FALKORDB_HOST" -p "$FALKORDB_PORT" --tls \
  -a "$FALKORDB_PASSWORD" "$FALKORDB_GRAPH"
> MATCH (n:Point) WHERE n.embedding IS NOT NULL RETURN count(n);
# expect >= 90% of Points after backfill (below)
```

## 3. Backfill existing Points (one-time)

Requires the embedding model locally (`pip install 'tortoise[embeddings]'`):

```bash
TORTOISE_DB_URI="$FALKORDB_CLOUD_URI" \
  python3 graph-scripts/backfill_embeddings.py --dry-run        # counts first
TORTOISE_DB_URI="$FALKORDB_CLOUD_URI" \
  python3 graph-scripts/backfill_embeddings.py --batch-size 500 # then backfill
```

Repair pre-#244 plain-list Event embeddings if present:

```bash
TORTOISE_DB_URI="$FALKORDB_CLOUD_URI" \
  python3 graph-scripts/backfill_embeddings.py --repair-embeddings
```

## 4. Verify search actually uses the vector strategy

```bash
curl -s -H "Authorization: Bearer $TT_KEY" \
  "https://api.premiselabs.co/v1/search?q=test" | python3 -m json.tool
# expect: "match_source": "rrf" (FTS + vector fused, not fts-only)
# spot-check a semantic query: "port migration" should hit a session about
# changing the FalkorDB default port even with zero keyword overlap (#244).
```

## 5. Post-deploy verification (per #559 discipline)

Every deploy should end with these checks recorded on the PR/issue:
- [ ] `/health` 200
- [ ] `count(n.embedding IS NOT NULL)` ≥ 90% after backfill
- [ ] `/v1/search` returns `match_source: "rrf"` with vector participation
- [ ] `tortoise_search(entity_type="event")` returns session results (#244)
