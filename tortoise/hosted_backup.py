"""Hosted backup/restore pipeline (#305) — product-ization of the RDB-first restore (#171).

Why a logical dump, not an RDB file:
- Production is FalkorDB Cloud (managed). We cannot BGSAVE-and-copy the RDB over the
  wire (no shell on the server). A logical Cypher export (nodes + edges + props) captures
  the complete graph and restores through the same client API — works against FalkorDB
  Cloud, self-hosted Docker, and embedded FalkorDBLite alike.
- The self-hosted RDB path (BGSAVE file copy) remains `scripts/daily-backup.sh` +
  `tortoise/backup.py` (#101/#171). This module is the hosted/multi-tenant equivalent:
  AES-256-GCM encrypted archives in Cloudflare R2 (S3-compatible), registry metadata,
  verified restore into a temp graph followed by an atomic swap (delete live → copy temp).

Pipeline (per team graph):
  create_backup:  dump_graph → encrypt (AES-256-GCM) → upload dump.enc + manifest.json
                  → stamp Team.backup_latest_at in the registry graph.
  restore_backup: download → sha256 verify vs manifest → decrypt → load into temp graph
                  → verify node count → delete live graph → GRAPH.COPY temp → live
                  → stamp Team.backup_restored_at.
  prune_backups:  keep N daily + M weekly (newest-first).

Env:
- TORTOISE_BACKUP_KEY — base64 32-byte key for AES-256-GCM (required for encrypt/decrypt).
- R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET — R2 storage config.
  (R2Storage lazy-imports boto3 — install with `pip install "tortoise[backups]"`.)
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import secrets
from datetime import datetime, timezone
from typing import Protocol

logger = logging.getLogger(__name__)

DUMP_FORMAT = "tortoise-logical-dump-v1"
_MAGIC = b"TB1"
_NONCE_LEN = 12
_AES_KEY_SIZE = 32  # AES-256
_LABEL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_DUMP_ID_PROP = "__dump_id"  # temp internal-id bridge during restore; removed after edges link


class RestoreVerificationError(RuntimeError):
    """Restore was rejected by a verification guard (corrupt/forged/unsafe
    backup, or an empty backup over live data). Distinct from transient
    storage/DB failures so the API can map it to 4xx, not 503."""


# ── key + encryption (AES-256-GCM) ───────────────────────────────────────────


def _get_backup_key() -> bytes:
    """Return the 32-byte AES-256-GCM key from TORTOISE_BACKUP_KEY (base64).

    Fail loudly — never encrypt with a default.
    """
    raw = os.environ.get("TORTOISE_BACKUP_KEY", "")
    if not raw:
        raise RuntimeError(
            "TORTOISE_BACKUP_KEY not set — required for hosted backups. Generate with: "
            "python -c \"import base64,secrets; print(base64.b64encode(secrets.token_bytes(32)).decode())\""
        )
    try:
        key = base64.b64decode(raw, validate=True)
    except Exception as e:
        raise RuntimeError(
            f"TORTOISE_BACKUP_KEY must be base64-encoded (got {raw[:8]!r}...): {e}"
        ) from e
    if len(key) != _AES_KEY_SIZE:
        raise RuntimeError(
            f"TORTOISE_BACKUP_KEY must decode to {_AES_KEY_SIZE} bytes (got {len(key)})"
        )
    return key


def encrypt_backup(data: bytes, key: bytes | None = None) -> bytes:
    """Encrypt backup payload. Returns magic || nonce(12) || ciphertext."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    key = key or _get_backup_key()
    nonce = os.urandom(_NONCE_LEN)
    ct = AESGCM(key).encrypt(nonce, data, None)
    return _MAGIC + nonce + ct


def decrypt_backup(blob: bytes, key: bytes | None = None) -> bytes:
    """Decrypt a backup blob. Raises ValueError on tamper / wrong key / bad magic."""
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    key = key or _get_backup_key()
    if not blob.startswith(_MAGIC) or len(blob) < len(_MAGIC) + _NONCE_LEN + 1:
        raise ValueError("Not a tortoise backup blob (bad magic or truncated payload)")
    nonce = blob[len(_MAGIC): len(_MAGIC) + _NONCE_LEN]
    ct = blob[len(_MAGIC) + _NONCE_LEN:]
    try:
        return AESGCM(key).decrypt(nonce, ct, None)
    except InvalidTag as e:
        raise ValueError(
            "Backup decryption failed — file tampered or wrong TORTOISE_BACKUP_KEY"
        ) from e


# ── logical graph dump / restore ─────────────────────────────────────────────


def _sanitize_label(lbl: str) -> str:
    """Allow only safe identifier labels/edge types (blocks Cypher injection)."""
    if not isinstance(lbl, str) or not _LABEL_RE.match(lbl):
        raise ValueError(f"Unsafe graph label/type: {lbl!r}")
    return lbl


def dump_graph(g, graph_name: str | None = None) -> dict:
    """Export the complete graph (nodes + edges + props) as a JSON-safe dict.

    Uses internal ids only as a temporary bridge (``__dump_id``) — restore rewires
    edges before removing the bridge, so the export is fully portable.
    """
    nodes = []
    rows = g.query("MATCH (n) RETURN id(n), labels(n), properties(n)").result_set
    for internal_id, labels, props in rows:
        nodes.append({
            "dump_id": int(internal_id),
            "labels": [str(l) for l in (labels or [])],
            "props": dict(props or {}),
        })
    edges = []
    rows = g.query("MATCH (a)-[r]->(b) RETURN id(a), id(b), type(r), properties(r)").result_set
    for src, dst, rtype, props in rows:
        edges.append({
            "src": int(src),
            "dst": int(dst),
            "type": str(rtype),
            "props": dict(props or {}),
        })
    return {
        "format": DUMP_FORMAT,
        "dumped_at": datetime.now(timezone.utc).isoformat(),
        "graph_name": graph_name,
        "node_count": len(nodes),
        "edge_count": len(edges),
        "nodes": nodes,
        "edges": edges,
    }


def restore_graph(g, dump: dict) -> dict:
    """Rebuild the graph from a logical dump into graph handle ``g``.

    Returns {"nodes": N, "edges": M}. Raises ValueError on unsafe labels/types or
    unsupported dump format.
    """
    if not isinstance(dump, dict) or dump.get("format") != DUMP_FORMAT:
        raise ValueError(
            "Unsupported dump format: "
            f"{dump.get('format') if isinstance(dump, dict) else type(dump)!r}"
        )
    nodes = dump.get("nodes", [])
    edges = dump.get("edges", [])

    for n in nodes:
        if "dump_id" not in n:
            raise ValueError("Dump node missing dump_id")
        labels = n.get("labels") or []
        safe_labels = ":".join(_sanitize_label(l) for l in labels)
        props = dict(n.get("props") or {})
        if _DUMP_ID_PROP in props:
            raise ValueError(
                f"Node props contain reserved property {_DUMP_ID_PROP!r} — "
                "refusing to clobber it during restore"
            )
        props[_DUMP_ID_PROP] = n["dump_id"]
        if safe_labels:
            g.query(f"CREATE (n:{safe_labels}) SET n = $p", params={"p": props})
        else:
            g.query("CREATE (n) SET n = $p", params={"p": props})  # unlabeled node
        # Re-encode vector props as vecf32 (the falkordb client decodes them to
        # plain lists on read; a plain-list embedding would poison vector
        # search for the whole graph — search_engine.py documents this).
        if isinstance(props.get("embedding"), list):
            g.query(
                f"MATCH (n {{{_DUMP_ID_PROP}:$id}}) "
                "SET n.embedding = CASE WHEN $v IS NOT NULL THEN vecf32($v) END",
                params={"id": n["dump_id"], "v": props["embedding"]},
            )

    for e in edges:
        if not all(k in e for k in ("src", "dst", "type")):
            raise ValueError(f"Malformed dump edge (missing src/dst/type): {e!r}")
        safe_type = _sanitize_label(e["type"])
        g.query(
            f"MATCH (a {{{_DUMP_ID_PROP}:$s}}), (b {{{_DUMP_ID_PROP}:$d}}) "
            f"CREATE (a)-[r:{safe_type}]->(b) SET r = $p",
            params={"s": e["src"], "d": e["dst"], "p": dict(e.get("props") or {})},
        )

    g.query(f"MATCH (n) WHERE n.{_DUMP_ID_PROP} IS NOT NULL REMOVE n.{_DUMP_ID_PROP}")
    # Edge integrity: every dumped edge must have been linkable. A dangling
    # src/dst (missing node) would otherwise be silently dropped while the
    # caller reports len(edges) — partial data loss invisible to verification.
    actual_edges = g.query("MATCH ()-[r]->() RETURN count(r)").result_set[0][0]
    if int(actual_edges) != len(edges):
        raise ValueError(
            f"Edge restore incomplete: {actual_edges}/{len(edges)} linked — "
            "dump references missing nodes"
        )
    # ACTUAL node count from the graph (not the dump bookkeeping) — the
    # verification gate must compare real graph state, mirroring the edge check.
    actual_nodes = g.query("MATCH (n) RETURN count(n)").result_set[0][0]
    return {"nodes": int(actual_nodes), "edges": int(actual_edges)}


# ── storage (S3-compatible / in-memory) ──────────────────────────────────────


class BackupStorage(Protocol):
    def upload(self, key: str, data: bytes, content_type: str | None = None) -> None: ...
    def download(self, key: str) -> bytes: ...
    def list(self, prefix: str) -> list[str]: ...
    def delete(self, key: str) -> None: ...


def _r2_endpoint_from_env() -> str:
    account = os.environ.get("R2_ACCOUNT_ID", "").strip()
    if not account:
        return ""
    return f"https://{account}.r2.cloudflarestorage.com"


def _is_no_such_key_error(e: Exception) -> bool:
    """True when ``e`` is a botocore ClientError for a missing S3/R2 key."""
    try:
        from botocore.exceptions import ClientError
    except ImportError:
        return False
    if not isinstance(e, ClientError):
        return False
    return str(e.response.get("Error", {}).get("Code", "")) == "NoSuchKey"


class R2Storage:
    """Cloudflare R2 (S3-compatible) backup store. boto3 lazy-imported."""

    def __init__(
        self,
        *,
        endpoint_url: str | None = None,
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
        bucket: str | None = None,
    ):
        self._endpoint = endpoint_url or _r2_endpoint_from_env()
        self._ak = access_key_id or os.environ.get("R2_ACCESS_KEY_ID", "")
        self._sk = secret_access_key or os.environ.get("R2_SECRET_ACCESS_KEY", "")
        self._bucket = bucket or os.environ.get("R2_BUCKET", "")
        if not all([self._endpoint, self._ak, self._sk, self._bucket]):
            raise RuntimeError(
                "R2 not configured — set R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, "
                "R2_SECRET_ACCESS_KEY, R2_BUCKET"
            )
        self._client = None

    def _s3(self):
        if self._client is None:
            try:
                import boto3
            except ImportError as e:
                raise RuntimeError(
                    "boto3 not installed — install with: pip install 'tortoise[backups]'"
                ) from e
            self._client = boto3.client(
                "s3",
                endpoint_url=self._endpoint,
                aws_access_key_id=self._ak,
                aws_secret_access_key=self._sk,
                region_name="auto",
            )
        return self._client

    def upload(self, key: str, data: bytes, content_type: str | None = None) -> None:
        kwargs = {"ContentType": content_type} if content_type else {}
        self._s3().put_object(Bucket=self._bucket, Key=key, Body=data, **kwargs)

    def download(self, key: str) -> bytes:
        try:
            resp = self._s3().get_object(Bucket=self._bucket, Key=key)
            return resp["Body"].read()
        except Exception as e:
            # Normalize missing-object to KeyError so the pipeline's clean
            # "Backup object not found" ValueError is uniform across stores.
            if _is_no_such_key_error(e):
                raise KeyError(key) from e
            raise

    def list(self, prefix: str) -> list[str]:
        keys: list[str] = []
        paginator = self._s3().get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                keys.append(obj["Key"])
        return keys

    def delete(self, key: str) -> None:
        self._s3().delete_object(Bucket=self._bucket, Key=key)


class MemoryStorage:
    """In-memory BackupStorage — tests / dry-runs."""

    def __init__(self) -> None:
        self._objects: dict[str, bytes] = {}

    def upload(self, key: str, data: bytes, content_type: str | None = None) -> None:
        self._objects[key] = data

    def download(self, key: str) -> bytes:
        if key not in self._objects:
            raise KeyError(f"object not found: {key}")
        return self._objects[key]

    def list(self, prefix: str) -> list[str]:
        return sorted(k for k in self._objects if k.startswith(prefix))

    def delete(self, key: str) -> None:
        self._objects.pop(key, None)


# ── pipeline ─────────────────────────────────────────────────────────────────


def _validate_team_id(team_id: str) -> None:
    """Team ids flow into object keys + prefix isolation — reject path-injection."""
    if not re.match(r"^[A-Za-z0-9_-]{1,64}$", team_id):
        raise ValueError(f"Invalid team_id {team_id!r} — must be [A-Za-z0-9_-]")


def create_backup(
    proj,
    registry,
    storage: BackupStorage,
    *,
    team_id: str,
    graph_name: str | None = None,
    key: bytes | None = None,
) -> dict:
    """Dump → encrypt → upload a team graph backup; stamp registry metadata.

    ``proj``: FalkorProjection bound to the team graph.
    ``registry``: Graph handle of the control_plane registry (Team node lives there).
    Returns the plaintext manifest (also stored in R2 for listing).
    """
    _validate_team_id(team_id)
    graph_name = graph_name or getattr(getattr(proj, "g", None), "name", f"team_{team_id}")
    dump = dump_graph(proj.g, graph_name=graph_name)
    payload = json.dumps(dump).encode("utf-8")
    blob = encrypt_backup(payload, key=key)
    ts = datetime.now(timezone.utc)
    # Millisecond + random suffix — two backups for the same team within one
    # millisecond must not collide on the same object key (silent overwrite).
    backup_id = (
        f"{team_id}/{ts.strftime('%Y%m%dT%H%M%S')}{ts.microsecond // 1000:03d}Z_{secrets.token_hex(4)}"
    )
    manifest = {
        "backup_id": backup_id,
        "team_id": team_id,
        "graph_name": graph_name,
        "created_at": dump["dumped_at"],
        "node_count": dump["node_count"],
        "edge_count": dump["edge_count"],
        "sha256": hashlib.sha256(blob).hexdigest(),
        "format": DUMP_FORMAT,
    }
    storage.upload(f"backups/{backup_id}/dump.enc", blob)
    try:
        storage.upload(
            f"backups/{backup_id}/manifest.json",
            json.dumps(manifest, indent=2).encode("utf-8"),
            content_type="application/json",
        )
    except Exception:
        # Partial-upload orphan: the blob is already durable but has no manifest
        # (never listed, never pruned, unrestorable). Roll it back so a failed
        # backup leaves no garbage.
        try:
            storage.delete(f"backups/{backup_id}/dump.enc")
        except Exception:
            pass
        raise
    try:
        registry.query(
            "MATCH (t:Team {id:$id}) SET t.backup_latest_at = $ts",
            params={"id": team_id, "ts": dump["dumped_at"]},
        )
    except Exception as e:  # backup already durable; registry stamp is best-effort
        logger.warning("backup uploaded but registry stamp failed for %s: %s", team_id, e)
    return manifest


def list_backups(storage: BackupStorage, team_id: str) -> list[dict]:
    """Return manifests for a team's backups, newest first."""
    _validate_team_id(team_id)
    out: list[dict] = []
    for key in storage.list(f"backups/{team_id}/"):
        if key.endswith("/manifest.json"):
            try:
                out.append(json.loads(storage.download(key)))
            except (json.JSONDecodeError, ValueError, UnicodeDecodeError) as e:
                logger.warning("unreadable backup manifest %s: %s", key, e)
            # Storage/network failures (AccessDenied, timeouts) propagate — a
            # partial outage must not masquerade as "no backups exist" during
            # disaster recovery.
    out.sort(key=lambda m: str(m.get("created_at", "")), reverse=True)
    return out


def restore_backup(
    db,
    registry,
    storage: BackupStorage,
    backup_key: str,
    *,
    team_id: str,
    graph_name: str,
    key: bytes | None = None,
) -> dict:
    """Restore a team graph from a stored backup: verify → temp graph → swap.

    ``db``: falkordb Connection handle (e.g. ``sdk._get_proj().db``) — temp graph and
    the live graph live on the same server.
    Swap: restore into ``{graph_name}_restore_{ts}`` → verify node count → delete live
    graph → GRAPH.COPY temp → live. If the final copy fails the temp graph remains
    intact (recoverable), never a partial live graph.
    """
    _validate_team_id(team_id)
    if not backup_key.endswith("dump.enc"):
        raise ValueError("backup_key must reference a dump.enc object")
    # Tenant isolation: the backup must belong to the requesting team.
    # Defense in depth — the API already derives team_id from auth, but the
    # pipeline must not accept a cross-team key (a leaked/guessed key would
    # otherwise restore another tenant's graph into this team's live graph).
    if not backup_key.startswith(f"backups/{team_id}/"):
        raise ValueError(
            f"backup_key does not belong to team {team_id} — cross-team restore rejected"
        )
    try:
        blob = storage.download(backup_key)
    except KeyError as e:
        raise ValueError(f"Backup object not found: {backup_key}") from e

    manifest: dict = {}
    manifest_key = backup_key.replace("/dump.enc", "/manifest.json")
    try:
        parsed = json.loads(storage.download(manifest_key))
        if not isinstance(parsed, dict):
            raise ValueError("manifest is not an object")
        manifest = parsed
    except Exception:
        raise ValueError(
            "Backup manifest unreadable — refusing to restore with verification disabled"
        ) from None
    if not manifest.get("sha256"):
        raise ValueError(
            "Backup manifest missing sha256 — refusing to restore with integrity disabled"
        )
    if hashlib.sha256(blob).hexdigest() != manifest["sha256"]:
        raise ValueError("Backup integrity check failed (sha256 mismatch)")
    # Fail-closed tenant isolation: a missing OR mismatched team_id is rejected.
    if manifest.get("team_id") != team_id:
        raise ValueError(
            f"Backup manifest does not belong to team {team_id} — cross-team restore rejected"
        )
    # Fail-closed graph isolation within the team: the backup belongs to one
    # graph; swapping it into a differently-named graph silently replaces that
    # graph's content with another's.
    if manifest.get("graph_name") and manifest["graph_name"] != graph_name:
        raise ValueError(
            f"Backup graph {manifest['graph_name']!r} does not match requested "
            f"graph {graph_name!r} — cross-graph restore rejected"
        )

    try:
        payload = json.loads(decrypt_backup(blob, key=key))
    except ValueError as e:
        raise ValueError(f"Cannot restore: {e}") from e
    if not isinstance(payload, dict) or payload.get("format") != DUMP_FORMAT:
        raise ValueError("Decrypted payload is not a tortoise logical dump")
    # Authenticated graph isolation (defense in depth beyond the plaintext
    # manifest check): the payload itself must agree on the target graph.
    if payload.get("graph_name") != graph_name:
        raise ValueError(
            f"Backup payload graph {payload.get('graph_name')!r} does not match "
            f"requested graph {graph_name!r} — cross-graph restore rejected"
        )

    ts = datetime.now(timezone.utc)
    # Millisecond + random suffix — two restores of the same graph within one
    # millisecond must not share a staging graph (a retry racing the original
    # would contaminate counts).
    ts_str = f"{ts.strftime('%Y%m%dT%H%M%S')}{ts.microsecond // 1000:03d}Z_{secrets.token_hex(4)}"
    temp_name = f"{graph_name}_restore_{ts_str}"
    pre_name = f"{graph_name}_pre_restore_{ts_str}"
    temp_g = db.select_graph(temp_name)
    try:
        counts = restore_graph(temp_g, payload)
    except Exception:
        # validation failure (unsafe label, dangling edge, malformed edge) —
        # drop the staging graph; the live graph was never touched
        try:
            temp_g.delete()
        except Exception:
            pass
        raise

    # Verify against the DECRYPTED payload (authenticated by AES-GCM), not the
    # plaintext manifest — a tampered manifest can never disable verification.
    expected_nodes = payload.get("node_count")
    expected_edges = payload.get("edge_count")
    if expected_nodes is None or expected_edges is None:
        try:
            temp_g.delete()
        except Exception:
            pass
        raise ValueError(
            "Backup payload missing node_count/edge_count — refusing to restore "
            "with count verification disabled"
        )
    if counts["nodes"] != expected_nodes:
        # Partial restore — drop the staging graph (unlike the copy-failure
        # path below, this temp graph is a failed partial restore, not a
        # verified recovery copy). Live graph untouched.
        try:
            temp_g.delete()
        except Exception:
            pass
        raise RestoreVerificationError(
            f"Restore verification failed: {counts['nodes']} nodes restored, "
            f"expected {expected_nodes} — live graph untouched"
        )
    if counts["edges"] != expected_edges:
        try:
            temp_g.delete()
        except Exception:
            pass
        raise RestoreVerificationError(
            f"Restore verification failed: {counts['edges']} edges restored, "
            f"expected {expected_edges} — live graph untouched"
        )

    # Empty-backup guard (issue #101 class): a backup taken after a wipe (0
    # nodes) must not silently REPLACE live data on restore — the operator
    # would see "verified ✓" while the live graph is destroyed again.
    live_g = db.select_graph(graph_name)
    try:
        live_nodes = int(live_g.query("MATCH (n) RETURN count(n)").result_set[0][0])
    except Exception:
        live_nodes = 0  # graph missing/empty — nothing to protect
    if live_nodes > 0 and expected_nodes == 0:
        try:
            temp_g.delete()
        except Exception:
            pass
        raise RestoreVerificationError(
            f"Refusing to restore an empty backup over a live graph with "
            f"{live_nodes} nodes — live graph untouched"
        )

    # Pre-restore safety copy: before the destructive delete, snapshot the live
    # graph so the swap is reversible even if the process dies mid-window
    # (the 2026-08-05 "wipe followed by any write re-saves the empty state"
    # failure chain). Best-effort — skipped when live is empty/missing.
    pre_g = None
    if live_nodes > 0:
        try:
            live_g.copy(pre_name)
            pre_g = db.select_graph(pre_name)
        except Exception as e:
            logger.warning("pre-restore copy failed (continuing): %s", e)

    # Swap: delete live graph then promote the verified temp graph. Delete is
    # best-effort: the disaster-recovery path restores into a graph that was
    # DROPPED/lost — a missing graph raises on delete but the copy below seeds
    # it. A genuine delete failure surfaces as a copy failure ("destination key
    # already exists") and the verified temp graph remains intact.
    try:
        live_g.delete()
    except Exception as e:
        logger.warning("live graph delete failed (proceeding to copy): %s", e)
    try:
        temp_g.copy(graph_name)
    except Exception as e:
        logger.exception(
            "GRAPH.COPY temp→live failed for %s — temp graph %s intact",
            graph_name, temp_name,
        )
        raise RuntimeError(
            f"Restore swap failed — verified temp graph {temp_name} intact: {e}"
        ) from e
    # Success: remove the transient staging + pre-restore copies
    for g in (temp_g, pre_g):
        if g is not None:
            try:
                g.delete()
            except Exception as e:
                logger.warning("cleanup of %s failed: %s", getattr(g, "name", "?"), e)

    try:
        registry.query(
            "MATCH (t:Team {id:$id}) SET t.backup_restored_at = $ts",
            params={"id": team_id, "ts": datetime.now(timezone.utc).isoformat()},
        )
    except Exception as e:  # best-effort metadata
        logger.warning("registry restore stamp failed for %s: %s", team_id, e)

    return {
        "backup_key": backup_key,
        "restored": counts,
        "restored_at": datetime.now(timezone.utc).isoformat(),
    }


def prune_backups(
    storage: BackupStorage, team_id: str, keep_daily: int = 7, keep_weekly: int = 4
) -> list[str]:
    """Delete old backups: keep ``keep_daily`` newest, plus ``keep_weekly`` weekly
    anchors (one per ISO week) beyond the daily window. Returns deleted backup_ids.

    Delete-path trust: objects are keyed by the STORAGE KEY they were found
    under, never by a manifest's self-declared ``backup_id`` — a forged or
    stale manifest cannot trigger deletion of another (newer) backup's objects.
    """
    _validate_team_id(team_id)
    now = datetime.now(timezone.utc)
    deleted: list[str] = []
    kept_weekly: set[tuple[int, int]] = set()

    # Newest first by the key-derived backup_id (from the listing prefix).
    manifest_keys = sorted(
        (k for k in storage.list(f"backups/{team_id}/") if k.endswith("/manifest.json")),
        key=lambda k: k,
        reverse=True,
    )
    for key in manifest_keys:
        parts = key.split("/")
        if len(parts) != 4:
            logger.warning("skipping malformed manifest key %s", key)
            continue
        _team, ts = parts[1], parts[2]
        backup_id = f"{_team}/{ts}"
        try:
            parsed = json.loads(storage.download(key))
            manifest = parsed if isinstance(parsed, dict) else {}
        except Exception:
            manifest = {}
        if manifest.get("backup_id") != backup_id:
            logger.warning(
                "manifest backup_id %r does not match key %s — skipping (untrusted)",
                manifest.get("backup_id"), key,
            )
            continue
        created_at = manifest.get("created_at", "")
        try:
            created = datetime.fromisoformat(str(created_at))
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)  # naive → assume UTC
            if (now - created).days < keep_daily:
                continue  # inside daily window — keep
        except (ValueError, TypeError):
            # corrupt/naive-mismatch timestamps are deleted — a bad date must
            # never abort pruning of the team's other backups
            deleted.append(backup_id)
            for suffix in ("dump.enc", "manifest.json"):
                storage.delete(f"backups/{backup_id}/{suffix}")
            continue
        iso = created.isocalendar()
        week = (iso.year, iso.week)
        if week not in kept_weekly and len(kept_weekly) < keep_weekly:
            kept_weekly.add(week)
            continue
        deleted.append(backup_id)
        for suffix in ("dump.enc", "manifest.json"):
            storage.delete(f"backups/{backup_id}/{suffix}")
    return deleted
