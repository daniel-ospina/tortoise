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

Pipeline (per org graph):
  create_backup:  dump_graph → encrypt (AES-256-GCM) → upload dump.enc + manifest.json
                  → stamp Org.backup_latest_at in the registry graph.
  restore_backup: download → sha256 verify vs manifest → decrypt → load into temp
                  graph → verify node+edge counts against the AUTHENTICATED payload
                  → empty-backup-over-live guard → pre-restore safety copy of the
                  live graph → delete live → GRAPH.COPY temp → live → boolean-index
                  audit (#3154 — GRAPH.COPY can drop the `false` postings of a
                  copied boolean index)
                  → cleanup.
                  Both GRAPH.COPY copies run over the restore's OWN read bound
                  (#3813) — an ordinary request's socket_timeout forbids a copy
                  longer than 60s at any legal configuration.
                  Any verification failure leaves the live graph untouched; a swap
                  failure leaves the verified temp + pre-restore copies recoverable.
  prune_backups:  keep N daily + M weekly (newest-first).

Env (restore):
- TORTOISE_RESTORE_SWAP_TIMEOUT_S — explicit read bound (seconds) for the
  restore's GRAPH.COPY copies. Default 120, clamped to [60, 3600]. See
  _restore_swap_timeout_s.

Env:
- TORTOISE_BACKUP_KEY — base64 32-byte ACTIVE key for AES-256-GCM (encrypt
  + decrypt; required). Managed by the secret store (#2318): a RETAINED
  previous key (TORTOISE_BACKUP_KEY_PREVIOUS, or an older file-store version)
  is kept as a DECRYPT candidate during a rotation overlap window — see
  _decrypt_candidate_keys. Sweep archives additionally carry
  REGISTRY_STREAM_KEY(_PREVIOUS) (#661) — Fly-only, never in GitHub.
- R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET — R2 storage config.
  (R2Storage lazy-imports boto3 — install with `pip install "tortoise[backups]"`.)
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import math
import os
import re
import secrets
from datetime import datetime, timezone
from typing import Callable, Protocol  # noqa: UP035

from tortoise.fork_slot import (
    ForkSlotRecovery,
    ForkSlotWedgedError,
    fork_slot_is_wedged,
    is_fork_refusal,
    recover_fork_slot,
)

logger = logging.getLogger(__name__)

DUMP_FORMAT = "tortoise-logical-dump-v1"
_MAGIC = b"TB1"
_NONCE_LEN = 12
_AES_KEY_SIZE = 32  # AES-256
_LABEL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_DUMP_ID_PROP = "__dump_id"  # temp internal-id bridge during restore; removed after edges link
_EMBED_BATCH = 400  # rows per UNWIND vecf32 re-encode query (falkordb inlines params)


class RestoreVerificationError(RuntimeError):
    """Restore was rejected by a verification guard (corrupt/forged/unsafe
    backup, or an empty backup over live data). Distinct from transient
    storage/DB failures so the API can map it to 4xx, not 503."""


# ── key + encryption (AES-256-GCM) ───────────────────────────────────────────


def _get_backup_key() -> bytes:
    """Return the ACTIVE 32-byte AES-256-GCM key from TORTOISE_BACKUP_KEY (base64).

    #2318: this is the ENCRYPT key (the rotation overlap window keeps a
    retained previous key only for DECRYPT — see ``_decrypt_candidate_keys``).

    Fail loudly — never encrypt with a default.
    """
    raw = os.environ.get("TORTOISE_BACKUP_KEY", "")
    if not raw:
        raise RuntimeError(
            "TORTOISE_BACKUP_KEY not set — required for hosted backups. Generate with: "
            "python -c \"import base64,secrets; print(base64.b64encode(secrets.token_bytes(32)).decode())\""
        )
    try:
        key = base64.b64decode(raw.strip(), validate=True)  # tolerate trailing newline
    except Exception as e:
        # #2796 review (R2/R4, third site): never echo the raw value — a
        # malformed key is still secret material, and this message can reach
        # logs / /status.graph_failures. Fingerprint only (secret_store contract).
        got = hashlib.sha256(raw.strip().encode()).hexdigest()[:8]
        raise RuntimeError(
            f"TORTOISE_BACKUP_KEY must be base64-encoded (got <{got}>...): {e}"
        ) from e
    if len(key) != _AES_KEY_SIZE:
        raise RuntimeError(
            f"TORTOISE_BACKUP_KEY must decode to {_AES_KEY_SIZE} bytes (got {len(key)})"
        )
    return key

def _decrypt_candidate_keys(explicit: bytes | None = None) -> tuple[bytes, ...]:
    """Active-first, fingerprint-deduped decrypt candidate chain (#2318).

    Rotation retention (#2318): during the overlap window a role holds TWO
    keys — the active (encrypt) key and the retained previous key (the
    ``*_PREVIOUS`` env var or an older file-store version). Decrypt must try
    both for BOTH roles: the #661 cross-role seam is preserved (a sweep
    archive restores through the user-backup path and vice versa) and an
    archive encrypted under a rotated-out key stays decryptable in-app for as
    long as the old key is retained.

    Order: the explicit key first (the caller's chosen role), then the config
    chains (backup role, then registry_stream role — when the sweep config is
    enabled). When NO explicit key is given (auto mode: the hosted
    user-facing restore endpoint passes key=None) and the config is disabled,
    the raw env-level chains apply (sweep-disabled / selfhost restores, so a
    manually-rotated TORTOISE_BACKUP_KEY + ``_PREVIOUS`` still decrypts). An
    explicit key stays authoritative when the config is disabled — pre-#2318
    semantics: decrypt fails if the supplied key is wrong (an env key never
    silently substitutes for an explicitly-chosen key). No key material is
    ever logged — candidates are identified by fingerprint only.
    """
    from . import secret_store as ss
    from .backup_config import load_config as _load_cfg

    out: list[bytes] = []
    seen: set[str] = set()

    def _add(key: bytes) -> None:
        fp = ss.key_fingerprint(key)
        if fp not in seen:
            seen.add(fp)
            out.append(key)

    if explicit is not None:
        _add(explicit)
    try:
        cfg = _load_cfg()
    except Exception:
        cfg = None
    if cfg is not None and cfg.enabled:
        for chain in (cfg.backup_key_chain, cfg.registry_stream_key_chain):
            for key in chain:
                _add(key)
    elif explicit is None:
        # Auto mode (no explicit key): env-level chains cover sweep-disabled /
        # selfhost restores — including a manually-rotated *_PREVIOUS key.
        env_store = ss.EnvKeyStore()
        for role_name in ss.ROLES:
            for key in env_store.candidates(role_name):
                _add(key)
    return tuple(out)


def _try_decrypt_with_chain(blob: bytes, keys: tuple[bytes, ...]) -> bytes:
    """Decrypt with the first candidate that authenticates. Raises ValueError
    (tamper/wrong key) when none match."""
    last: ValueError | None = None
    for candidate in keys:
        try:
            return decrypt_backup(blob, key=candidate)
        except ValueError as e:
            last = e
    if last is not None:
        raise last
    raise ValueError("Backup decryption failed — no decrypt key available")


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

    #1625: internal bookkeeping (EpMeta/GraphEventMeta/TeamMeta label-wide,
    # plus Meta nodes with key in {point_fts_v2, event_fts_v2} — the R2/R3
    # FTS-migration markers) is EXCLUDED — runtime markers, not content;
    # exporting them inflates node_count and restore recreates them
    # spuriously. Meta {key:'calibration_milestone'} is DATA (Gate B state)
    # and is NOT excluded (key-scoped).
    """
    from tortoise.hosted_api import _is_export_skip_node
    nodes = []
    rows = g.query("MATCH (n) RETURN id(n), labels(n), properties(n)").result_set
    for internal_id, labels, props in rows:
        labels_list = [str(l) for l in (labels or [])]  # noqa: E741
        if _is_export_skip_node(labels_list, dict(props or {})):
            continue
        nodes.append({
            "dump_id": int(internal_id),
            "labels": labels_list,
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
        "dumped_at": datetime.now(timezone.utc).isoformat(),  # noqa: UP017
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
        safe_labels = ":".join(_sanitize_label(l) for l in labels)  # noqa: E741
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

    # Re-encode vector props as vecf32 in CHUNKED batches (the falkordb client
    # inlines params into the query header — a single unbounded UNWIND for a
    # large embedding-heavy graph would exceed socket_timeout/redis bulk
    # limits; a plain-list embedding would poison vector search — search_engine
    # documents this).
    embed_rows = [
        {"id": n["dump_id"], "v": n["props"]["embedding"]}
        for n in nodes
        if isinstance(n.get("props", {}).get("embedding"), list)
    ]
    for i in range(0, len(embed_rows), _EMBED_BATCH):
        chunk = embed_rows[i:i + _EMBED_BATCH]
        g.query(
            f"UNWIND $rows AS r MATCH (n {{{_DUMP_ID_PROP}:r.id}}) "
            "SET n.embedding = vecf32(r.v)",
            params={"rows": chunk},
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
    # #1625: count non-skip nodes by applying the SAME predicate as the dump
    # (_is_export_skip_node) so the two sides can never drift again (the
    # earlier label/cypher query missed TeamMeta — a pre-fix org backup's
    # TeamMeta node made actual = expected + 1 → RestoreVerificationError).
    # The dst projection's open re-creates the FTS Meta markers, which the
    # dump excludes; calibration_milestone is data and IS counted.
    from tortoise.hosted_api import _is_export_skip_node
    actual_nodes = 0
    for row in g.query("MATCH (n) RETURN labels(n), properties(n)").result_set:
        labels = [str(l) for l in (row[0] or [])]  # noqa: E741
        if not _is_export_skip_node(labels, dict(row[1] or {})):
            actual_nodes += 1
    return {"nodes": int(actual_nodes), "edges": int(actual_edges)}


# ── storage (S3-compatible / in-memory) ──────────────────────────────────────


class BackupStorage(Protocol):
    def upload(self, key: str, data: bytes, content_type: str | None = None) -> None: ...
    def download(self, key: str) -> bytes: ...
    def list(self, prefix: str) -> list[str]: ...
    def delete(self, key: str) -> None: ...
    def create_if_not_exists(self, key: str, data: bytes) -> bool: ...


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
        try:
            self._s3().put_object(Bucket=self._bucket, Key=key, Body=data, **kwargs)
        except Exception as e:
            raise RuntimeError(f"R2 upload failed for {key}: {e}") from e

    def create_if_not_exists(self, key: str, data: bytes) -> bool:
        """Create ``key`` ONLY if it does not exist (S3 ``IfNoneMatch='*'``).

        Returns True on create, False if the object already exists. This is the
        dedup linearization point for the alert store — the R2 object is the
        authority, so a simultaneous creator/adopter race resolves here.

        Fallback (HEAD-check) applies if the store rejects conditional writes:
        the dedup authority must never silently degrade to unconditional puts.
        """
        try:
            self._s3().put_object(
                Bucket=self._bucket, Key=key, Body=data, IfNoneMatch="*"
            )
            return True
        except Exception as e:
            # 412 PreconditionFailed — the object already exists (the expected
            # race outcome). boto3 surfaces it as a ClientError.
            try:
                from botocore.exceptions import ClientError

                if isinstance(e, ClientError) and str(e.response.get("Error", {}).get("Code", "")) in (
                    "PreconditionFailed",
                    "ConditionalRequestConflict",
                    "412",
                ):
                    return False
            except ImportError:
                pass
            # Fallback (HEAD-check) for any client that rejected the
            # conditional write — boto3 412s are handled above; other clients
            # (and the review P3 guard) fall through to a HEAD that confirms
            # existence. An ambiguous HEAD (object missing, read failed) must
            # RAISE — a blind-put would weaken the dedup linearization point.
            try:
                self._s3().head_object(Bucket=self._bucket, Key=key)
                return False  # exists → not created
            except Exception:
                pass
            raise RuntimeError(
                f"R2 create_if_not_exists could not confirm absence for {key}: {e}"
            ) from e

    def download(self, key: str) -> bytes:
        try:
            resp = self._s3().get_object(Bucket=self._bucket, Key=key)
            return resp["Body"].read()
        except Exception as e:
            # Normalize missing-object to KeyError so the pipeline's clean
            # "Backup object not found" ValueError is uniform across stores.
            if _is_no_such_key_error(e):
                raise KeyError(key) from e
            raise RuntimeError(f"R2 download failed for {key}: {e}") from e

    def list(self, prefix: str) -> list[str]:
        try:
            keys: list[str] = []
            paginator = self._s3().get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
                for obj in page.get("Contents", []):
                    keys.append(obj["Key"])
            return keys
        except Exception as e:
            raise RuntimeError(f"R2 list failed for {prefix}: {e}") from e

    def delete(self, key: str) -> None:
        try:
            self._s3().delete_object(Bucket=self._bucket, Key=key)
        except Exception as e:
            raise RuntimeError(f"R2 delete failed for {key}: {e}") from e


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

    def create_if_not_exists(self, key: str, data: bytes) -> bool:
        if key in self._objects:
            return False
        self._objects[key] = data
        return True


# ── #2319 bucket-lock immutability + second-region mirror ────────────────────
# R2 bucket locks are prefix-scoped retention RULES (Cloudflare dashboard /
# Wrangler / REST API) — NOT S3 Object Lock: R2's S3 layer does not implement
# GetBucketVersioning / GetObjectLockConfiguration / PutObjectLockConfiguration,
# so lock state can only be READ via the Cloudflare REST API with an
# account-scoped API token (CF_API_TOKEN), never via boto3. When no token is
# configured the verification reports "unverifiable" and the ops runbook
# (docs/ops/registry-backup-dr.md §#2319) is the verification path.
#
# Prune compatibility: R2 locks block DELETE (and overwrite) inside the
# window, so the prune (which deletes day-bucket losers from ~25h old) must
# tolerate locked objects — skip + log + retry on a later run — never abort
# the pool (#2319; the #2304 purge path was already best-effort per object).
_LOCK_PREFIX = "backups/"  # the ONLY prefix ever locked — never ops/*
_LOCK_CF_API = "https://api.cloudflare.com/client/v4"
_LOCK_READ_TIMEOUT_S = 6.0


class LockVerificationError(RuntimeError):
    """The live bucket-lock configuration could not be read (HTTP/transport
    failure, missing CF_API_TOKEN, or an unexpected payload). Callers surface
    it as "unverifiable" + point at the runbook — a lock read failure must
    never block the backup pipeline itself."""


def _is_locked_delete_error(e: Exception) -> bool:
    """Best-effort classification of a delete failure caused by an R2
    bucket-lock retention rule. R2 reports code 10069
    (ObjectLockedByBucketPolicy) and a 403 AccessDenied over the S3 seam.
    Loose string matching is intentional — it only decorates the prune log;
    prune tolerates ALL delete failures identically."""
    msg = str(e).lower()
    markers = (
        "accessdenied", "access denied", "10069",
        "objectlockedbybucketpolicy", "bucket lock", "locked by",
    )
    return any(m in msg for m in markers)


def _delete_backup_objects(storage, backup_id: str) -> bool:
    """Best-effort delete of a backup's dump.enc + manifest.json pair.

    Returns True when BOTH objects were deleted. A locked (or otherwise
    failed) delete is logged and returns False — never raises — so the prune
    keeps pruning the rest of the pool (#2319: bucket locks block deletion
    inside the window; the object is retried on a later run once the
    retention expires). A partial delete (dump gone, manifest delete failed)
    converges on the next run: the manifest is re-listed and the missing dump
    delete is a no-op success.
    """
    ok = True
    for suffix in ("dump.enc", "manifest.json"):
        key = f"backups/{backup_id}/{suffix}"
        try:
            storage.delete(key)
        except Exception as e:
            ok = False
            if _is_locked_delete_error(e):
                logger.warning(
                    "prune: %s is bucket-locked (retention window) — "
                    "skipping; retried once the lock expires: %s", key, e,
                )
            else:
                logger.warning("prune delete failed for %s: %s", key, e)
    return ok


def _lock_rules_url(account_id: str, bucket: str) -> str:
    return f"{_LOCK_CF_API}/accounts/{account_id}/r2/buckets/{bucket}/lock"


def _lock_rules_http_get(url: str, headers: dict, timeout: float):
    """GET ``url`` and return the parsed JSON payload. Module-level so tests
    can monkeypatch it (urllib is imported lazily — the lock verification path
    is optional and must not weigh on the core import)."""
    import json as _json
    import urllib.error as _urlerr
    import urllib.request as _urlreq

    req = _urlreq.Request(url, headers=headers)
    try:
        with _urlreq.urlopen(req, timeout=timeout) as resp:
            return _json.loads(resp.read().decode("utf-8"))
    except (_urlerr.HTTPError, _urlerr.URLError, TimeoutError,
            OSError, ValueError) as e:
        raise LockVerificationError(
            f"cannot read R2 bucket-lock rules: {e}") from e


def fetch_r2_bucket_lock_rules(
    account_id: str,
    token: str,
    bucket: str,
    *,
    timeout: float = _LOCK_READ_TIMEOUT_S,
) -> list[dict]:
    """Read the R2 bucket-lock rule configuration via the Cloudflare REST
    API (GET /accounts/{id}/r2/buckets/{bucket}/lock — the ONLY supported
    lock read; R2's S3 layer has no Object-Lock/versioning reads).

    Returns ``result.rules``. Raises LockVerificationError when
    unconfigured, on transport/HTTP failure, or on an unexpected payload.
    """
    if not (account_id and token and bucket):
        raise LockVerificationError(
            "lock verification unconfigured — set R2_ACCOUNT_ID + R2_BUCKET "
            "and an R2-scoped CF_API_TOKEN"
        )
    payload = _lock_rules_http_get(
        _lock_rules_url(account_id, bucket),
        {"Authorization": f"Bearer {token}",
         "Content-Type": "application/json"},
        timeout,
    )
    if not isinstance(payload, dict) or not payload.get("success"):
        raise LockVerificationError(
            f"Cloudflare API error: {payload.get('errors') if isinstance(payload, dict) else payload}"
        )
    result = payload.get("result") or {}
    rules = result.get("rules") if isinstance(result, dict) else None
    if not isinstance(rules, list):
        raise LockVerificationError("unexpected bucket-lock rules payload shape")
    return rules


def _rule_retention_seconds(rule: dict) -> float | None:
    """Retention of ONE enabled rule in seconds: Age → maxAgeSeconds;
    Indefinite / Date (absolute deadline) → infinity (can only extend
    coverage). None when disabled or malformed."""
    if not isinstance(rule, dict) or rule.get("enabled") is False:
        return None
    cond = rule.get("condition")
    if not isinstance(cond, dict):
        return None
    ctype = str(cond.get("type") or "")
    if ctype == "Age":
        try:
            secs = float(cond["maxAgeSeconds"])
        except (KeyError, TypeError, ValueError):
            return None
        if secs != secs:  # NaN — a malformed rule must never lock/compare
            return None
        return secs
    if ctype in ("Indefinite", "Date"):
        return float("inf")
    return None


def effective_lock_seconds(rules, prefix: str = _LOCK_PREFIX) -> float | None:
    """Strictest retention (seconds) among enabled rules COVERING ``prefix``.
    A rule covers the prefix when the rule's ``prefix`` is a key-prefix of it
    (the empty rule prefix covers everything — the #2319 rule is scoped to
    ``backups/`` exactly so the frequently-rewritten ops/* objects are never
    locked). None when no enabled rule covers the prefix."""
    best: float | None = None
    for rule in rules or []:
        if not isinstance(rule, dict):
            continue
        rp = str(rule.get("prefix") or "")
        if not prefix.startswith(rp):
            continue
        secs = _rule_retention_seconds(rule)
        if secs is None:
            continue
        best = secs if best is None else max(best, secs)
    return best


def verify_bucket_lock(
    rules,
    *,
    expected_days: int,
    prefix: str = _LOCK_PREFIX,
) -> dict:
    """Pure drift check: is the strictest retention covering ``prefix`` >=
    ``expected_days``? Returns the #2319 verification block with
    ``status``: "verified" | "drift" | "absent" (never raises — the caller
    maps LockVerificationError from the transport layer to "unverifiable").
    """
    eff = effective_lock_seconds(rules, prefix=prefix)
    base = {"prefix": prefix, "expected_days": expected_days}
    if eff is None:
        return {
            "status": "absent", **base,
            "detail": "no enabled bucket-lock rule covers the prefix",
        }
    if eff == float("inf"):
        return {
            "status": "verified", **base, "retention_days": None,
            "detail": "an indefinite/date bucket-lock rule covers the prefix",
        }
    retention_days = eff / 86400.0
    if retention_days + 1e-9 >= expected_days:
        return {
            "status": "verified", **base,
            "retention_days": round(retention_days, 3),
            "detail": "strictest retention meets the expected window",
        }
    return {
        "status": "drift", **base,
        "retention_days": round(retention_days, 3),
        "detail": (
            f"strictest retention {retention_days:.2f}d is below the "
            f"expected {expected_days}d window — backups under this prefix "
            "are NOT protected by the configured lock window"
        ),
    }


def mirror_backup(storage, mirror, backup_id: str) -> dict:
    """Copy one ACCEPTED backup (dump.enc + manifest.json) from ``storage``
    to the second-region ``mirror`` store and read-back verify the ciphertext
    against the manifest sha256 (#2319 geo decision c). Keys are preserved
    byte-for-byte so the mirror holds the same restore paths.

    Returns {"backup_id", "mirrored": [keys], "verified": true}. Raises
    RuntimeError on copy or verification failure — callers surface it as a
    per-graph mirror error; the PRIMARY backup is already durable and is
    never affected by a mirror failure (a later run mirrors the next archive).
    """
    dump_key = f"backups/{backup_id}/dump.enc"
    manifest_key = f"backups/{backup_id}/manifest.json"
    blob = storage.download(dump_key)
    manifest_raw = storage.download(manifest_key)
    try:
        sha = str((json.loads(manifest_raw) or {}).get("sha256") or "")
    except ValueError:
        sha = ""
    mirror.upload(dump_key, blob)
    mirror.upload(manifest_key, manifest_raw, content_type="application/json")
    if not sha:
        raise RuntimeError(
            f"mirror verify impossible for {backup_id}: manifest sha256 missing"
        )
    if hashlib.sha256(mirror.download(dump_key)).hexdigest() != sha:
        raise RuntimeError(
            f"mirror verification failed for {backup_id}: mirrored dump.enc "
            "sha256 mismatch"
        )
    return {
        "backup_id": backup_id,
        "mirrored": [dump_key, manifest_key],
        "verified": True,
    }



# ── pipeline ─────────────────────────────────────────────────────────────────


def _validate_org_id(org_id: str) -> None:
    """Org ids flow into object keys + prefix isolation — reject path-injection."""
    if not re.match(r"^[A-Za-z0-9_-]{1,64}$", org_id):
        raise ValueError(f"Invalid org_id {org_id!r} — must be [A-Za-z0-9_-]")


def _validate_graph_id(graph_id: str) -> None:
    """#2313: graph ids flow into object keys (the graph key segment) —
    reject path-injection with the same charset as org ids."""
    if not re.match(r"^[A-Za-z0-9_-]{1,64}$", graph_id):
        raise ValueError(f"Invalid graph_id {graph_id!r} — must be [A-Za-z0-9_-]")


# ── #669 backup seam (plan Task 5, P1-3) ────────────────────────────────────
# The backup pipeline talks to the control plane through ONE seam (an adapter
# exposing ``query()``): pre-#669 the FalkorDB registry graph handle (Cypher
# dialect), post-#669 the Supabase control plane (PostgREST dialect —
# ``SupabaseControlPlane`` or a fake mirroring its interface). The dialect is
# auto-detected so the registry path keeps working for selfhost while hosted
# passes the Supabase source through the same seam.


def _is_supabase_source(source) -> bool:
    """Dialect check for the backup seam.

    The registry source is a FalkorDB graph handle whose ``query(cypher)``
    returns a result-set object; the Supabase source exposes
    ``query(table, ...)`` returning row dicts. Both name their method
    ``query`` — the first positional parameter name is the discriminator
    (``q`` for FalkorDB, ``table`` for PostgREST). An un-inspectable query
    (or a ``*args`` stub) is treated as the registry dialect — the failure
    path is unchanged for every existing test stub.
    """
    try:
        import inspect
        first = next(iter(inspect.signature(source.query).parameters.values()))
    except Exception:  # noqa: BLE001, RUF100
        return False
    return first.name == "table"


def source_dialect(source) -> str:
    """#2823: the seam dialect actually resolved for this run.

    ``"supabase"`` (PostgREST control plane) or ``"registry"`` (FalkorDB
    ``registry_control_plane`` handle). Recorded on every sweep roll-up so
    ``/v1/internal/backups/status`` answers *"which control plane did the sweep
    enumerate?"* — the question that took 31 days to answer for #2823, because
    a wrong-dialect read is indistinguishable from an empty deployment in the
    run result.
    """
    return "supabase" if _is_supabase_source(source) else "registry"


def _compose_backup_id(org_id: str, backup_id_ts: str,
                        graph_id: str | None = None) -> str:
    """#2313: the UNPREFIXED composite backup id stored in manifests.

    Legacy shape (graph_id None — org-era callers, pre-#2313 artifacts):
    ``{org_id}/{ts}_{rnd}`` — byte-identical to the historical manifest
    contract. Per-graph shape: ``{org_id}/{graph_id}/{ts}_{rnd}``. Object
    keys are ``backups/`` + this id (every consumer prefixes it itself).
    """
    seg = f"{graph_id}/" if graph_id is not None else ""
    return f"{org_id}/{seg}{backup_id_ts}"


def _parse_backup_key(key: str) -> tuple[str, str | None, str]:
    """#2313: parse a backup object key into (org_id, graph_id, id_ts).

    Accepts both shapes: ``backups/{org}/{ts}_{rnd}/{file}`` (legacy,
    graph_id None) and ``backups/{org}/{graph}/{ts}_{rnd}/{file}``
    (per-graph). ``id_ts`` is the ``{ts}_{rnd}`` token — feed it to
    ``_compose_backup_id`` to rebuild the manifest ``backup_id``. Raises
    ValueError on a malformed key.
    """
    parts = key.split("/")
    if len(parts) == 4 and parts[0] == "backups":
        return parts[1], None, parts[2]
    if len(parts) == 5 and parts[0] == "backups":
        return parts[1], parts[2], parts[3]
    raise ValueError(f"malformed backup key {key!r}")


def _stamp_backup_latest(source, org_id: str, ts: str) -> None:
    """Seam: stamp ``backup_latest_at`` on the org's control-plane row.

    Registry mode: SET on the Org graph node. Supabase mode: PATCH the
    ``teams`` row (the ``id`` filter pins the exact org). Raises on failure
    — the callers (create_backup/restore_backup) keep the best-effort
    contract (#669 P3: a control-plane blip must not fail an
    otherwise-durable backup).
    """
    if _is_supabase_source(source):
        source.query(
            "organizations", method="PATCH", filters=[("id", "eq", org_id)],
            json_body={"backup_latest_at": ts},
        )
    else:
        source.query(
            "MATCH (t:Team {id:$id}) SET t.backup_latest_at = $ts",
            params={"id": org_id, "ts": ts},
        )


def _stamp_backup_restored(source, org_id: str, ts: str | None = None) -> None:
    """Seam: stamp ``backup_restored_at`` on the org's control-plane row.

    Same dialect split as ``_stamp_backup_latest``. Drills never reach it
    (restore_backup's ``drill`` flag skips the end-stamp entirely).
    """
    ts = ts or datetime.now(timezone.utc).isoformat()  # noqa: UP017
    if _is_supabase_source(source):
        source.query(
            "organizations", method="PATCH", filters=[("id", "eq", org_id)],
            json_body={"backup_restored_at": ts},
        )
    else:
        source.query(
            "MATCH (t:Team {id:$id}) SET t.backup_restored_at = $ts",
            params={"id": org_id, "ts": ts},
        )


def create_backup(
    proj,
    registry,
    storage: BackupStorage,
    *,
    org_id: str,
    graph_name: str | None = None,
    graph_id: str | None = None,
    key: bytes | None = None,
) -> dict:
    """Dump → encrypt → upload an org graph backup; stamp registry metadata.

    ``proj``: FalkorProjection bound to the org graph.
    ``registry``: Graph handle of the control_plane registry (Org node lives there).
    ``graph_id`` (#2313): when set, the artifact is keyed under the graph
    segment (``backups/{org}/{graph}/{ts}/…``) and the manifest records it;
    None keeps the legacy org-level key shape byte-for-byte (pre-#2313
    callers and artifacts unchanged).
    Returns the plaintext manifest (also stored in R2 for listing).
    """
    _validate_org_id(org_id)
    if graph_id is not None:
        _validate_graph_id(graph_id)
    graph_name = graph_name or getattr(getattr(proj, "g", None), "name", f"org_{org_id}")
    dump = dump_graph(proj.g, graph_name=graph_name)
    payload = json.dumps(dump).encode("utf-8")
    blob = encrypt_backup(payload, key=key)
    ts = datetime.now(timezone.utc)  # noqa: UP017
    # Millisecond + random suffix — two backups for the same org within one
    # millisecond must not collide on the same object key (silent overwrite).
    backup_id_ts = (
        f"{ts.strftime('%Y%m%dT%H%M%S')}{ts.microsecond // 1000:03d}Z_{secrets.token_hex(4)}"
    )
    backup_id = _compose_backup_id(org_id, backup_id_ts, graph_id=graph_id)
    manifest = {
        "backup_id": backup_id,
        "org_id": org_id,
        "graph_name": graph_name,
        "created_at": dump["dumped_at"],
        "node_count": dump["node_count"],
        "edge_count": dump["edge_count"],
        "sha256": hashlib.sha256(blob).hexdigest(),
        "format": DUMP_FORMAT,
    }
    if graph_id is not None:
        manifest["graph_id"] = graph_id
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
        try:  # noqa: SIM105
            storage.delete(f"backups/{backup_id}/dump.enc")
        except Exception:
            pass
        raise
    try:
        _stamp_backup_latest(registry, org_id, dump["dumped_at"])
    except Exception as e:  # backup already durable; the stamp is best-effort (#669 P3)
        logger.warning("backup uploaded but stamp failed for %s: %s", org_id, e)
    return manifest


def list_backups(storage: BackupStorage, org_id: str,
                 graph_id: str | None = None) -> list[dict]:
    """Return manifests for an org's backups, newest first.

    ``graph_id`` (#2313): when set, lists only that graph's backups (the
    graph key-segment prefix). When None, lists the whole org pool — both
    legacy flat artifacts and per-graph nested ones (manifest-based listing
    carries graph_name/graph_id, so org-wide consumers keep working).
    """
    _validate_org_id(org_id)
    if graph_id is not None:
        _validate_graph_id(graph_id)
    prefix = f"backups/{org_id}/{graph_id}/" if graph_id is not None \
        else f"backups/{org_id}/"
    out: list[dict] = []
    for key in storage.list(prefix):
        if key.endswith("/manifest.json"):
            try:
                out.append(json.loads(storage.download(key)))
            except (json.JSONDecodeError, ValueError, UnicodeDecodeError, KeyError) as e:
                logger.warning("unreadable/vanished backup manifest %s: %s", key, e)
            # Storage/network failures (AccessDenied, timeouts) propagate — a
            # partial outage must not masquerade as "no backups exist" during
            # disaster recovery. KeyError (concurrently pruned object) is the
            # same unreadable-manifest class and is skipped.
    out.sort(key=lambda m: str(m.get("created_at", "")), reverse=True)
    return out


def _audit_copied_boolean_indexes(
    g,
    *,
    graph_name: str,
    stage: str,
    raise_on_failure: bool = True,
) -> bool:
    """#3154: verify + repair boolean predicates after a ``GRAPH.COPY``.

    FalkorDB's ``GRAPH.COPY`` can copy a boolean RANGE index on
    ``Point.is_operator`` without its ``false`` posting entries — observed on
    docker FalkorDB 4.20.4 when the source's index set carries the boolean
    index in its SDK-created position (a composite-ONLY source copies
    healthy). The destination's index then has no entry for ``false``, so
    ``n.is_operator = false`` matches ZERO rows on such a copy while
    ``NOT n.is_operator`` and ``n.is_operator = true`` stay correct and
    ``typeof(n.is_operator)`` still reports Boolean (the DATA is intact — the
    index is corrupt). The restore/import swap (``temp → live``) and the
    pre-restore safety copy are both GRAPH.COPYs, so an un-audited copy
    can silently degrade EP anchor selection, the calibration gate and dedup on
    the restored graph.

    The repair is PRESENCE-based, never count-based: a copy destination can
    hold a poisoned boolean index while the counts happen to agree — a graph
    with no ``false`` rows at copy time hides the lost postings, and the
    NEXT non-operator write then reads 0 (verified: 5 ``true`` nodes + a
    boolean index copied, then one ``false`` node created → `= false` 0 vs
    `NOT` 1). Any boolean index found is therefore dropped, and the DROP
    succeeding is itself the presence signal (an absent index raises "no
    such index" in O(1)).

    A boolean index is DETECTED BY PRESENCE (`CALL db.indexes()`), not by
    comparing two predicate counts: those counts come from separate
    non-atomic queries, so a concurrent non-operator write landing between
    them is indistinguishable from corruption (measured: 35/40 false
    "corruption" raises on a graph with NO index while a writer ran). The
    count probe is retained for diagnostics only and gates nothing.

    Dropping is the only repair. A copy destination that carries an index set
    rebuilds the boolean index CORRUPT: after ``DROP INDEX``, a fresh
    ``CREATE INDEX`` on ``is_operator`` still reads 0 for `= false` (measured
    on docker FalkorDB 4.20.4 — the single index, the ``(is_operator,
    lastDreamedAt)`` composite, and a copy of an already-sanitized source all
    reproduce it). A graph built from scratch with no indexes is healthy,
    which is why the logical-dump restore path is safe today. This mirrors
    #522's embedded policy: the full label scan for `= false` is correct, and
    ``_ensure_indexes`` no longer creates the index on any backend.

    Returns ``True`` when a boolean index was present and dropped. Raises
    ``RuntimeError`` when the predicates are still inconsistent after the
    drop — a caller must not report a successful copy that silently degrades
    belief state. Never raises when ``raise_on_failure`` is False.
    """
    def _index_has_is_operator() -> bool:
        """Is there a ``:Point`` index whose field list includes
        ``is_operator``?  PRESENCE, read from the index catalogue — not a
        predicate count.

        Matched by EXACT field name, never by substring: ``row[1]`` is the
        list of indexed field names, and ``"is_operator" in str(row[1])``
        would also match an unrelated property such as ``is_operator_flag``.
        That false positive is never cleared by the drop (which only targets
        the two known boolean-index forms), so the re-check would report
        "still present" and raise — the same post-swap user-visible failure
        as the count race this function replaced (#3154 review P2).
        """
        rows = g.query("CALL db.indexes()").result_set
        for row in rows:
            if not row or row[0] != "Point":
                continue
            fields = row[1]
            if isinstance(fields, str):
                # Defensive: a stringified field list is tokenised, not
                # substring-matched, so `is_operator_flag` cannot match.
                fields = [
                    f.strip().strip("[]'\"")
                    for f in fields.strip("[]").split(",")
                ]
            if any(str(f) == "is_operator" for f in (fields or ())):
                return True
        return False

    def _probe() -> tuple[int, int]:
        """Diagnostic only — NEVER gates the repair. Two separate count()
        queries are not atomic, so a concurrent non-operator write landing
        between them desynchronises them indistinguishably from corruption
        (measured: 35/40 false "corruption" raises on a graph with NO index
        while a writer ran). Hence presence-based detection above/below, and
        this is used solely to describe what was found."""
        indexed = int(g.query(
            "MATCH (n:Point) WHERE n.is_operator = false RETURN count(n)"
        ).result_set[0][0])
        truth = int(g.query(
            "MATCH (n:Point) WHERE NOT n.is_operator RETURN count(n)"
        ).result_set[0][0])
        return indexed, truth

    def _drop_boolean_indexes() -> bool:
        """Unconditionally drop every known boolean-index form. Returns True
        when a DROP actually removed something.

        Only "no such index" is swallowed: treating EVERY failure as
        "absent" silently skips the repair on a genuine drop error, which is
        the silent-failure class #3154 exists to close.
        """
        dropped = False
        for stmt in ("DROP INDEX ON :Point(is_operator)",
                     "DROP INDEX ON :Point(is_operator, lastDreamedAt)"):
            try:
                g.query(stmt)
                dropped = True
            except Exception as e:
                if "no such index" not in str(e).lower():
                    logger.warning(
                        "#3154: DROP INDEX failed on %s after %s (%s): %s",
                        graph_name, stage, stmt, e,
                    )
                # else: absent — the healthy case (O(1), no startup penalty)
        return dropped

    # Presence FIRST, so nothing below depends on a racy count comparison.
    try:
        if not _index_has_is_operator():
            return False
    except Exception as e:
        logger.warning(
            "#3154: could not enumerate indexes on %s after %s: %s",
            graph_name, stage, e,
        )
        return False

    indexed = truth = None
    try:
        indexed, truth = _probe()
    except Exception as e:
        # A probe failure must not suppress the drop (it gates nothing now).
        logger.warning(
            "#3154: could not probe boolean predicates on %s after %s: %s",
            graph_name, stage, e,
        )

    present = _drop_boolean_indexes()

    # PRESENCE-based verification: the repair is "the index is GONE", not
    # "two counts agree". A surviving index is the failure, whatever the
    # counts say.
    try:
        still_present = _index_has_is_operator()
    except Exception as e:
        logger.warning(
            "#3154: could not re-check indexes on %s after %s: %s",
            graph_name, stage, e,
        )
        return present

    if still_present:
        msg = (
            f"#3154: a boolean is_operator index on {graph_name} survived the "
            f"{stage} copy's repair and could not be dropped — refusing to "
            "report a successful copy that silently degrades EP/calibration "
            "state (a surviving corrupt index returns ZERO rows for every "
            "`is_operator = false` predicate)"
        )
        if raise_on_failure:
            raise RuntimeError(msg)
        logger.error("%s (raise_on_failure=False)", msg)
        return present

    if indexed is not None and indexed != truth:
        logger.error(
            "#3154: GRAPH.COPY dropped the `false` entries of the boolean "
            "is_operator index on %s (%s) — `= false` read %d rows instead of "
            "%d. Dropped the corrupt index: `= false` predicates now use the "
            "correct label scan (perf cost, not a correctness cost).",
            graph_name, stage, indexed, truth,
        )
    elif present:
        logger.warning(
            "#3154: dropped a boolean is_operator index on %s (%s) after the "
            "copy. The counts agreed, so the index may have been healthy — "
            "but a copied boolean index is not trustworthy (a graph with no "
            "`false` rows hides the lost postings). No engine indexes the "
            "property; `= false` now uses the correct label scan.",
            graph_name, stage,
        )
    return present


def _graph_copy_or_diagnose(
    graph,
    dst_name: str,
    *,
    db,
    site: str,
    copy: Callable[[], None] | None = None,
) -> ForkSlotRecovery | None:
    """``graph.copy(dst_name)``, recovering a wedged module-fork slot (#3845).

    A plain ``GRAPH.COPY`` is the only operation that forks a Redis MODULE
    child. On the embedded lane that child can deadlock in the server's log
    path (see :mod:`tortoise.fork_slot`), permanently occupying the single
    module-fork slot — after which every later copy is refused and the whole
    DR family stays red until a restart.

    Detection is EVIDENCE-based, never a guess:

    * a copy that raises the engine's fork-refusal response (``could not
      fork``) is a wedge by construction; and
    * a copy that failed for ANY other reason (the restore's OWN bound
      excepted — see below) is *also* a wedge when our own daemon is still
      carrying a module-fork child that has outlived
      :data:`_FORK_CHILD_HUNG_AGE_S` — a child that old for a graph this
      small is parked, not working.

    On either signal the hung child(ren) of OUR socket are reaped (which is
    what actually releases the slot — Redis's ``checkChildrenDone`` calls
    ``resetChildState``), and the copy is retried on the freed slot. The retry
    is inherently racy (a fresh fork can lose the same race), so it is BOUNDED:
    when it keeps hitting the wedge the caller gets a
    :class:`ForkSlotWedgedError` rather than an endless loop. Returns the
    recovery when a wedge was handled, ``None`` on the clean path. Any other
    copy failure is re-raised unchanged.

    ``copy`` (#3813): the copy STEP to retry. Defaults to ``graph.copy(dst_name)``
    — the wedge recovery here is otherwise orthogonal to *how* the copy is
    issued. The restore sites pass ``_graph_copy_with_restore_bound`` so the
    copy runs over the restore's own generous read bound while STILL getting
    this detect→reap→retry recovery. On the restore path a CLIENT READ TIMEOUT
    is never treated as a wedge: the server may still be copying, and reaping a
    fork child to retry would issue a SECOND server-side fork while the first
    may still be running (#3813) — as well as misreporting a timeout as a wedge
    (#3845). Only the restore's own bound raises ``RestoreCopyTimeoutError``,
    so the documented default ``copy is None`` path is untouched and keeps
    #3924's ``is_fork_refusal(...) or fork_slot_is_wedged(...)``
    classification (a plain read timeout there is still a wedge candidate).
    """
    last_recovery: ForkSlotRecovery | None = None
    last_exc: BaseException | None = None
    for attempt in range(1, _FORK_COPY_ATTEMPTS + 1):
        try:
            if copy is None:
                graph.copy(dst_name)
            else:
                copy()
            return last_recovery
        except Exception as exc:
            last_exc = exc
            # #3813 x #3845: on the RESTORE path the copy runs over its own
            # generous bound, and only that bound raises
            # ``RestoreCopyTimeoutError`` (chaining the real client timeout as
            # ``__cause__``). A client read timeout there is NEVER a wedge: the
            # server may still be copying, so reaping the fork child and
            # re-issuing would start a SECOND server-side fork while the first
            # may still be running, and would misreport a timeout as a wedge.
            # The check is deliberately the restore's OWN exception TYPE, not
            # ``_is_client_read_timeout``: the documented ``copy is None``
            # default path (#3924) must keep its ``is_fork_refusal(...) or
            # fork_slot_is_wedged(...)`` classification unchanged.
            if isinstance(exc, RestoreCopyTimeoutError):
                raise
            wedged = is_fork_refusal(exc) or fork_slot_is_wedged(
                db, min_age_s=_FORK_CHILD_HUNG_AGE_S)
            if not wedged:
                raise  # an ordinary copy failure — not ours to reinterpret

        # Wedge confirmed. Recover the SLOT (not just this call): until the
        # hung child is reaped, every later GRAPH.COPY on this server is
        # refused, so freeing it is the whole point.
        recovery = recover_fork_slot(db)
        if not recovery.recovered:
            raise ForkSlotWedgedError(
                site=site, dst_name=dst_name, recovery=recovery) from last_exc
        last_recovery = recovery
        logger.warning(
            "#3845: %s fork slot was wedged — released it (%s); retrying "
            "GRAPH.COPY -> %s (attempt %d/%d)",
            site, recovery.detail, dst_name, attempt, _FORK_COPY_ATTEMPTS,
        )

    raise ForkSlotWedgedError(
        site=site, dst_name=dst_name,
        recovery=last_recovery or ForkSlotRecovery(
            wedged=True, detail="every retry re-wedged the slot"),
    ) from last_exc


def _promote_payload_fork_free(
    live_g,
    payload: dict,
    *,
    live_name: str,
    temp_name: str,
    expected_nodes: int,
    expected_edges: int,
) -> dict:
    """Install a VERIFIED payload into ``live_g`` with NO ``GRAPH.COPY``.

    The last-resort path when the module-fork slot cannot be released: rebuild
    the live graph from the authenticated payload through the SAME fork-free
    logical path that built the verified temp graph. Correctness is not
    weakened — this never reports success it did not achieve:

    * the live graph must be EMPTY first (the logical restore APPENDS; the
      pre-swap delete is best-effort, so emptiness is re-checked here);
    * the restored counts must equal the counts the temp graph was verified
      against, else it raises; and
    * a failure names the verified temp graph as intact and the live graph as
      NOT restored — the temp graph is never presented as the live one.
    """
    # Fail closed on a non-empty destination: restoring on top of existing
    # nodes would duplicate data while still satisfying the count check only
    # by luck. Re-delete, then confirm emptiness by reading the graph.
    try:
        live_g.delete()
    except Exception as e:
        logger.warning(
            "#3845: live graph delete before fork-free promotion reported: %s", e)
    try:
        rows = live_g.query("MATCH (n) RETURN count(n)").result_set
        live_now = int(rows[0][0]) if rows else 0
    except Exception as e:
        raise RuntimeError(
            f"Restore swap failed (fork slot wedged; fork-free promotion could "
            f"not verify {live_name} is empty) — verified temp graph "
            f"{temp_name} intact"
        ) from e
    if live_now:
        raise RuntimeError(
            f"Restore swap failed (fork slot wedged; {live_name} still holds "
            f"{live_now} nodes — refusing to append the restore) — verified "
            f"temp graph {temp_name} intact"
        )
    promoted = restore_graph(live_g, payload)
    if (promoted.get("nodes") != expected_nodes
            or promoted.get("edges") != expected_edges):
        raise RestoreVerificationError(
            f"Restore swap failed (fork slot wedged; fork-free promotion "
            f"restored {promoted.get('nodes')}/{expected_nodes} nodes, "
            f"{promoted.get('edges')}/{expected_edges} edges) — verified temp "
            f"graph {temp_name} intact"
        )
    return promoted


#: A module-fork child older than this is parked, not working: a healthy
#: ``GRAPH.COPY`` of a DR-sized graph completes in milliseconds. Used only to
#: classify a FAILED copy as a wedge when the engine's refusal text is absent.
_FORK_CHILD_HUNG_AGE_S = 2.0

#: Bounded detect → reap → retry rounds for one copy. The retry forks again and
#: can lose the very race it is recovering from, so an unbounded loop would be
#: the wedge in a different costume; on exhaustion the caller gets a
#: :class:`ForkSlotWedgedError`.
_FORK_COPY_ATTEMPTS = 3


# ── #3813: the restore's GRAPH.COPY must not inherit an ordinary request's
#    read bound ────────────────────────────────────────────────────────────
#
# ``GRAPH.COPY`` is a LONG-RUNNING, SERVER-SIDE operation. FalkorDB forks a
# child that encodes the source graph to a temp file, the parent decodes it
# and installs the destination key, and the CLIENT is blocked for the whole
# copy (FalkorDB ``cmd_copy.c``: ``_Graph_Copy`` → ``RedisModule_Fork``, then
# ``LoadGraphFromFile`` → ``RedisModule_ReplyWithCString(ctx, "OK")``).
#
# On the ordinary client that read is governed by ``socket_timeout``, which
# #2850 deliberately bounds at ``_DB_TIMEOUT_MAX_S`` = 60s: right for an
# ordinary request (a stalled call must not park a thread — or, on the paths
# that call the client synchronously from the event loop, the loop), WRONG for
# a copy that legitimately outlives it. A restore of a large enough graph
# therefore fails at ANY legal configuration — and it fails *misleadingly*:
# when the read bound expires, redis-py tears the connection down mid-parse and
# the originating ``redis.exceptions.TimeoutError: Timeout reading from
# socket`` surfaces as ``ValueError: I/O operation on closed file``. That is a
# CLIENT timeout reported as a dead connection, which the swap then reports as
# a failed copy.
#
# The swap (and the pre-restore safety copy — the same class of operation) run
# on a client whose read bound belongs to the operation: EXPLICIT, generous,
# and still FINITE — ``None``/infinite is redis-py's "block forever", the
# #2850 failure mode this module must not reintroduce.
_RESTORE_SWAP_TIMEOUT_DEFAULT_S = 120.0
#: Floor: must NOT be BELOW ``projection._DB_TIMEOUT_MAX_S`` (60s) — the
#: largest read bound a *legal* ordinary configuration can reach. Equality at
#: 60 is deliberate: an operator who sets exactly the ordinary ceiling gets no
#: restore headroom, by choice. The DEFAULT (120s) is what supplies the
#: headroom the swap exists for.
_RESTORE_SWAP_TIMEOUT_MIN_S = 60.0
#: Ceiling: keeps a wedged copy bounded. It is NOT inside the scheduled
#: drill's committed RTO — the copy bounded here can itself outlive the
#: 900s (``hosted_api._DRILL_RTO_S``) drill RTO.
_RESTORE_SWAP_TIMEOUT_MAX_S = 3600.0


def _restore_swap_timeout_s() -> float:
    """Resolve the restore swap's own GRAPH.COPY read bound, in seconds.

    ``TORTOISE_RESTORE_SWAP_TIMEOUT_S`` (default
    ``_RESTORE_SWAP_TIMEOUT_DEFAULT_S``), clamped to
    ``[_RESTORE_SWAP_TIMEOUT_MIN_S, _RESTORE_SWAP_TIMEOUT_MAX_S]``.

    A non-numeric or non-finite value falls back to the default rather than
    disabling the bound: ``float("inf")`` reaching redis-py's
    ``sock.settimeout`` raises ``OverflowError`` (NOT caught by its
    ``except OSError``), so an unbounded swap would brick the restore instead
    of merely slowing it.
    """
    raw = os.environ.get("TORTOISE_RESTORE_SWAP_TIMEOUT_S")
    if raw is None or not str(raw).strip():
        return _RESTORE_SWAP_TIMEOUT_DEFAULT_S
    try:
        v = float(raw)
    except (TypeError, ValueError):
        logger.warning("TORTOISE_RESTORE_SWAP_TIMEOUT_S=%r is not a number — "
                       "using %ss", raw, _RESTORE_SWAP_TIMEOUT_DEFAULT_S)
        return _RESTORE_SWAP_TIMEOUT_DEFAULT_S
    if not math.isfinite(v) or v <= 0:
        logger.warning("TORTOISE_RESTORE_SWAP_TIMEOUT_S=%r is not a finite "
                       "positive bound — using %ss (a non-finite socket "
                       "timeout means 'block forever')",
                       raw, _RESTORE_SWAP_TIMEOUT_DEFAULT_S)
        return _RESTORE_SWAP_TIMEOUT_DEFAULT_S
    if v < _RESTORE_SWAP_TIMEOUT_MIN_S:
        logger.warning("TORTOISE_RESTORE_SWAP_TIMEOUT_S=%r is below the %.0fs "
                       "floor — a legal ordinary bound already reaches that "
                       "(projection._DB_TIMEOUT_MAX_S); clamping",
                       raw, _RESTORE_SWAP_TIMEOUT_MIN_S)
        return _RESTORE_SWAP_TIMEOUT_MIN_S
    if v > _RESTORE_SWAP_TIMEOUT_MAX_S:
        logger.warning("TORTOISE_RESTORE_SWAP_TIMEOUT_S=%r exceeds the %.0fs "
                       "ceiling — clamping", raw, _RESTORE_SWAP_TIMEOUT_MAX_S)
        return _RESTORE_SWAP_TIMEOUT_MAX_S
    return v


class RestoreCopyTimeoutError(RuntimeError):
    """A restore GRAPH.COPY outlived its own (generous) read bound (#3813).

    Deliberately its OWN type, with its own wording. A CLIENT read timeout is
    neither a dead connection nor a failed copy — the server may still be
    copying. The message states a TIMEOUT, names the graph that is left intact
    and identifiable, and states that the destination was NOT restored, so a
    timeout can never be read as a successful (or merely failed) swap.
    """

    def __init__(self, *, role: str, timeout_s: float, intact_name: str,
                 dst_name: str) -> None:
        self.role = role
        self.timeout_s = timeout_s
        self.intact_name = intact_name
        self.dst_name = dst_name
        super().__init__(
            f"{role} timed out after {timeout_s:.0f}s — the server-side "
            f"GRAPH.COPY may still be running; {intact_name} intact, "
            f"{dst_name} NOT restored"
        )


def _restore_copy_client(db):
    """A DB client whose READ BOUND belongs to the restore, not a request.

    Derived from ``db``'s OWN connection — same server, same unix socket /
    host+port, same credentials, same db index (the pool's
    ``connection_class`` is preserved, so a unix-domain embedded client stays
    one) — with an explicit generous ``socket_timeout`` (#3813) and NO retry.

    No retry is deliberate on both counts: retrying multiplies the wait, and
    it re-issues the copy (a second server-side fork) while the first may
    still be running.
    """
    import redis as _redis
    from redis.backoff import NoBackoff
    from redis.retry import Retry as _Retry

    base = getattr(db, "connection", None) or getattr(db, "client", None)
    pool = getattr(base, "connection_pool", None)
    if pool is None:
        raise RuntimeError(
            "restore copy needs a redis connection pool to derive its own "
            f"read bound from; {type(db).__name__} exposes none"
        )
    kwargs = dict(pool.connection_kwargs)
    # #3813: the one line that decouples the restore's copy from an ordinary
    # request's read bound. Dropping it puts the copy back on the ordinary
    # client (see tests/test_dr_endpoints.py::TestRestoreSwapReadBound).
    kwargs["socket_timeout"] = _restore_swap_timeout_s()
    kwargs["retry"] = _Retry(NoBackoff(), 0)
    return _redis.Redis(connection_pool=_redis.ConnectionPool(
        connection_class=pool.connection_class, **kwargs))


def _issue_graph_copy(client, src_name: str, dst_name: str) -> None:
    """``GRAPH.COPY src → dst`` over ``client`` (the swap's single seam).

    Split out so where the bound applies is testable: the bound belongs to the
    CLIENT the command is issued over, not to the command string.
    """
    client.execute_command("GRAPH.COPY", src_name, dst_name)


def _is_client_read_timeout(exc: BaseException) -> bool:
    """True when ``exc`` IS — or MASKS — a redis CLIENT read timeout (#3813).

    redis-py's RESP parser closes the socket when a read times out and then
    seeks in the now-closed buffer, so the originating
    ``redis.exceptions.TimeoutError: Timeout reading from socket`` arrives
    chained behind ``ValueError: I/O operation on closed file`` — the exact
    shape the embedded lane produced. Walk ``__cause__``/``__context__`` so
    the classification never depends on which layer's exception surfaced, and
    never on message text.
    """
    import redis as _redis
    seen: set[int] = set()
    e: BaseException | None = exc
    while e is not None and id(e) not in seen:
        seen.add(id(e))
        if isinstance(e, (TimeoutError, _redis.exceptions.TimeoutError)):
            return True
        e = e.__cause__ or e.__context__
    return False


def _graph_copy_with_restore_bound(db, src_name: str, dst_name: str, *,
                                   role: str, intact_name: str) -> None:
    """Run a long GRAPH.COPY for the restore over its own read bound (#3813).

    Raises ``RestoreCopyTimeoutError`` when that bound expires, keeping the
    originating timeout as ``__cause__``, so callers report a TIMEOUT rather
    than a dead connection or a failed copy.
    """
    client = _restore_copy_client(db)
    try:
        _issue_graph_copy(client, src_name, dst_name)
    except Exception as e:
        if _is_client_read_timeout(e):
            raise RestoreCopyTimeoutError(
                role=role, timeout_s=_restore_swap_timeout_s(),
                intact_name=intact_name, dst_name=dst_name,
            ) from e
        raise
    finally:
        try:
            # redis-py's ``Redis.close()`` is a NO-OP for the socket when a
            # pool was supplied to the constructor (it returns early on
            # ``auto_close_connection_pool=False``), releasing only a cached
            # ``self.connection`` — which ``close()`` here never set. The
            # derived pool owns the socket, so disconnect it deterministically;
            # otherwise each derived client (one per copy attempt — the
            # pre-restore and swap sites together can build up to 2 ×
            # ``_FORK_COPY_ATTEMPTS`` per restore) leaks its socket until GC.
            client.close()
            client.connection_pool.disconnect()
        except Exception:
            pass


def _restore_into_temp_verify_swap(
    db,
    payload: dict,
    *,
    live_name: str,
    expected_nodes: int | None = None,
    expected_edges: int | None = None,
    stamp: Callable[[], None] | None = None,
) -> dict:
    """Temp-graph restore → verify → atomic swap — the shared stage behind
    ``restore_backup`` and the hosted ``POST /v1/organizations/{org_id}/import``
    endpoint (epic #1230 Task 2).

    ``db``: falkordb Connection handle (e.g. ``sdk._get_proj().db``) — the temp
        graph and the live graph live on the same server.
    ``live_name``: the graph the verified temp graph is swapped INTO.
    ``expected_nodes/expected_edges``: counts to verify against. Default: the
        payload's own ``node_count``/``edge_count`` — callers pass the
        AUTHENTICATED payload (decrypted under a verified key / sha256 chain),
        so verification can never be disabled by editing bookkeeping.
    ``stamp``: optional post-swap callback (control-plane metadata, best-effort
        at the caller): ``restore_backup`` stamps ``backup_restored_at`` via the
        registry; the import endpoint stamps its ``last_import_sha256`` ledger.

    Flow: restore into ``{live_name}_restore_{ts}_{rnd}`` → verify node+edge
    counts → empty-backup-over-live guard → pre-restore safety copy of the
    live graph → delete live → GRAPH.COPY temp → live → cleanup staging and
    pre-restore copies. Any failure before the swap leaves the live graph
    untouched; a swap failure leaves the verified temp + pre-restore copies
    recoverable. Both GRAPH.COPY copies run over the restore's OWN generous,
    explicit read bound (#3813) — never an ordinary request's, which forbids a
    copy longer than 60s at any legal configuration.
    """
    if expected_nodes is None:
        # #1625: derive the expected count from the AUTHENTICATED dump content
        # (non-skip nodes), not the manifest node_count — a pre-fix backup's
        # node_count includes the R2/R3 Meta markers (old dump_graph filtered
        # nothing), while restore_graph's actual count excludes them. The
        # payload is sha256-authenticated, so this is equally secure and
        # correct for both old and new dumps.
        from tortoise.hosted_api import _is_export_skip_node
        nodes_list = payload.get("nodes")
        if not isinstance(nodes_list, list):
            raise ValueError(
                "Backup payload missing nodes list — refusing to restore "
                "with count verification disabled"
            )
        expected_nodes = sum(
            1 for n in nodes_list
            if not _is_export_skip_node(list(n.get("labels") or []),
                                        dict(n.get("props") or {}))
        )
    if expected_edges is None:
        expected_edges = payload.get("edge_count")
    if expected_nodes is None or expected_edges is None:
        raise ValueError(
            "Backup payload missing node_count/edge_count — refusing to restore "
            "with count verification disabled"
        )

    ts = datetime.now(timezone.utc)  # noqa: UP017
    # Millisecond + random suffix — two restores of the same graph within one
    # millisecond must not share a staging graph (a retry racing the original
    # would contaminate counts).
    ts_str = f"{ts.strftime('%Y%m%dT%H%M%S')}{ts.microsecond // 1000:03d}Z_{secrets.token_hex(4)}"
    temp_name = f"{live_name}_restore_{ts_str}"
    pre_name = f"{live_name}_pre_restore_{ts_str}"
    temp_g = db.select_graph(temp_name)
    try:
        counts = restore_graph(temp_g, payload)
    except Exception:
        # validation failure (unsafe label, dangling edge, malformed edge) —
        # drop the staging graph; the live graph was never touched
        try:  # noqa: SIM105
            temp_g.delete()
        except Exception:
            pass
        raise

    # Verify against the DECRYPTED/AUTHENTICATED payload — a tampered manifest
    # can never disable verification (the caller's sha256 chain gates this).
    if counts["nodes"] != expected_nodes:
        # Partial restore — drop the staging graph (unlike the copy-failure
        # path below, this temp graph is a failed partial restore, not a
        # verified recovery copy). Live graph untouched.
        try:  # noqa: SIM105
            temp_g.delete()
        except Exception:
            pass
        raise RestoreVerificationError(
            f"Restore verification failed: {counts['nodes']} nodes restored, "
            f"expected {expected_nodes} — live graph untouched"
        )
    if counts["edges"] != expected_edges:
        try:  # noqa: SIM105
            temp_g.delete()
        except Exception:
            pass
        raise RestoreVerificationError(
            f"Restore verification failed: {counts['edges']} edges restored, "
            f"expected {expected_edges} — live graph untouched"
        )

    # Empty-backup guard (issue #101 class): a backup taken after a wipe (0
    # nodes) must not silently REPLACE live data on restore — the operator
    # would see "verified ✓" while the live graph is destroyed again. The live
    # count read FAILS CLOSED: a query failure is NOT treated as "graph
    # missing" — only a confirmed-absent graph (via list_graphs) is safe to
    # proceed on. A read failure must never authorize a destructive delete.
    #
    # #1625: count NON-SKIP nodes (same predicate as dump_graph/expected_nodes)
    # — a live graph holding only internal bookkeeping (the R2/R3 FTS-migration
    # Meta marker) is effectively EMPTY of user data, so an empty content
    # backup must not be rejected for it (the guard exists to protect real
    # data, not runtime markers).
    from tortoise.hosted_api import _is_export_skip_node
    live_g = db.select_graph(live_name)
    try:
        _live_rows = live_g.query(
            "MATCH (n) RETURN labels(n), properties(n)").result_set
        live_nodes = sum(
            1 for row in _live_rows
            if not _is_export_skip_node(
                [str(l) for l in (row[0] or [])], dict(row[1] or {}))  # noqa: E741
        )
    except Exception:
        # Fail closed: only a CONFIRMED-absent graph (via GRAPH.LIST) is safe to
        # proceed on. A query failure OR a list_graphs failure (dead connection —
        # exactly the incident-time scenario) aborts with the temp cleaned up;
        # a read failure must never authorize a destructive delete.
        try:
            graph_present = live_name in set(db.list_graphs())
        except Exception:
            graph_present = True  # cannot confirm absence → treat as present
        if graph_present:
            try:  # noqa: SIM105
                temp_g.delete()
            except Exception:
                pass
            raise RestoreVerificationError(  # noqa: B904
                "Cannot verify live graph state before restore — aborting (fail closed)"
            )
        live_nodes = 0  # confirmed absent (dropped graph) — nothing to protect
    if live_nodes > 0 and expected_nodes == 0:
        try:  # noqa: SIM105
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
    fork_slot: ForkSlotRecovery | None = None
    pre_g = None
    if live_nodes > 0:
        try:
            # #3845: a wedged module-fork slot refuses this copy too. The copy
            # is best-effort, but the RECOVERY is not — releasing the slot here
            # is what lets the swap below use a fork at all instead of the
            # fork-free fallback. #3813: a long server-side copy, so the copy
            # STEP runs over the restore's own read bound rather than an
            # ordinary request's socket_timeout.
            recovery = _graph_copy_or_diagnose(
                live_g, pre_name, db=db, site="pre-restore safety copy",
                copy=lambda: _graph_copy_with_restore_bound(
                    db, live_name, pre_name,
                    role="Pre-restore safety copy", intact_name=live_name,
                ),
            )
            if recovery is not None:
                fork_slot = recovery
            pre_g = db.select_graph(pre_name)
            # #3154: a boolean index corrupted by this copy would silently
            # break `= false` predicates on the DR fallback copy. Repair and
            # log loudly, but never abort an otherwise-healthy restore over
            # a best-effort safety copy.
            _audit_copied_boolean_indexes(
                pre_g, graph_name=pre_name,
                stage="pre-restore safety copy", raise_on_failure=False,
            )
        except ForkSlotWedgedError as e:
            # Best-effort copy; the swap below still gets its chance (and its
            # own fork-free fallback). Report the wedge distinctly — never as
            # a generic "copy failed".
            fork_slot = e.recovery
            logger.warning("pre-restore safety copy skipped — %s", e)
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
        # #3845: the swap's copy gets the detect→reap→retry wedge recovery;
        # #3813: and it runs over the restore's OWN generous read bound, so a
        # copy that outlives an ordinary request's socket_timeout still
        # completes instead of being torn down.
        recovery = _graph_copy_or_diagnose(
            temp_g, live_name, db=db, site="restore swap",
            copy=lambda: _graph_copy_with_restore_bound(
                db, temp_name, live_name,
                role="Restore swap",
                intact_name=f"verified temp graph {temp_name}",
            ),
        )
        if recovery is not None:
            fork_slot = recovery
    except RestoreCopyTimeoutError as e:
        # A CLIENT read timeout is NOT a dead connection and NOT a failed copy:
        # the server may still be copying. Say *timeout*, leave the verified
        # temp graph intact and identifiable, and never imply the live graph
        # was restored. Handled BEFORE ForkSlotWedgedError so a timeout is
        # never reinterpreted as a wedge (#3813).
        logger.error(
            "%s — the server-side copy may still be running; verified temp "
            "graph %s intact, live graph %s NOT restored",
            e, temp_name, live_name,
        )
        raise
    except ForkSlotWedgedError as e:
        # The module-fork slot is wedged and could not be released. Do NOT fail
        # the restore on a fork primitive: promote the already-VERIFIED,
        # authenticated payload into the live graph through the fork-free
        # logical path (the one that built the temp graph). The result is
        # re-verified against the same counts before success is reported.
        fork_slot = e.recovery
        logger.warning(
            "#3845: restore swap: fork slot wedged and not recoverable — "
            "falling back to a FORK-FREE promotion of the verified temp "
            "graph %s into %s (%s)", temp_name, live_name, e.recovery.detail,
        )
        try:
            _promote_payload_fork_free(
                live_g, payload,
                live_name=live_name, temp_name=temp_name,
                expected_nodes=expected_nodes, expected_edges=expected_edges,
            )
        except Exception as promo_exc:
            logger.exception(
                "fork-free promotion failed for %s — temp graph %s intact",
                live_name, temp_name,
            )
            raise RuntimeError(
                f"Restore swap failed (fork slot wedged; fork-free promotion "
                f"failed) — verified temp graph {temp_name} intact: {promo_exc}"
            ) from promo_exc
    except Exception as e:
        logger.exception(
            "GRAPH.COPY temp→live failed for %s — temp graph %s intact",
            live_name, temp_name,
        )
        raise RuntimeError(
            f"Restore swap failed — verified temp graph {temp_name} intact: {e}"
        ) from e
    # #3154: audit the swapped graph BEFORE declaring success — GRAPH.COPY is
    # the corrupting step, and a silently dead `n.is_operator = false` on the
    # live graph disables EP/calibration/dedup without any error. A failure
    # here leaves the verified temp and pre-restore copies intact.
    try:
        _audit_copied_boolean_indexes(
            db.select_graph(live_name), graph_name=live_name, stage="restore swap"
        )
    except RuntimeError:
        logger.exception(
            "#3154: boolean-index audit failed after the swap for %s — "
            "verified temp graph %s and pre-restore copy %s left intact",
            live_name, temp_name, pre_name,
        )
        raise
    # Success: remove the transient staging + pre-restore copies
    for g in (temp_g, pre_g):
        if g is not None:
            try:
                g.delete()
            except Exception as e:
                logger.warning("cleanup of %s failed: %s", getattr(g, "name", "?"), e)

    if stamp is not None:
        try:
            stamp()
        except Exception as e:  # best-effort metadata (#669 P3)
            logger.warning("restore stamp failed for %s: %s", live_name, e)

    result = {
        "restored": counts,
        "restored_at": datetime.now(timezone.utc).isoformat(),  # noqa: UP017
    }
    if fork_slot is not None:
        # #3845: report the wedge DISTINCTLY from a slow copy, and what was
        # done about it, so an operator sees "fork slot wedged" rather than a
        # misleading "copy failed".
        result["fork_slot"] = fork_slot.as_dict()
    return result


def restore_backup(
    db,
    registry,
    storage: BackupStorage,
    backup_key: str,
    *,
    org_id: str,
    graph_name: str,
    key: bytes | None = None,
    target_graph: str | None = None,
    drill: bool = False,
) -> dict:
    """Restore an org graph from a stored backup: verify → temp graph → swap.

    ``db``: falkordb Connection handle (e.g. ``sdk._get_proj().db``) — temp graph and
    the live graph live on the same server.

    ``target_graph`` (drill mode): when set, ALL live-phase operations (empty-guard
    read, pre-restore safety copy, live delete, swap copy) bind to ``target_graph``
    while the fail-closed graph-ISOLATION checks (manifest + decrypted payload) bind
    to the canonical ``graph_name``. This is what makes a drill scratch-only: it
    restores a real org archive into ``_drill_*`` and can never touch the live org
    graph. Staging/pre-restore names derive from ``target_graph``.

    ``drill``: skips the registry end-stamp (``Org.backup_restored_at``) so a drill
    performs ZERO production writes.

    Flow: restore into ``{live}_restore_{ts}_{rnd}`` → verify node+edge counts
    against the decrypted payload → empty-backup-over-live guard → pre-restore safety
    copy of the live graph → delete live → GRAPH.COPY temp → live → cleanup staging
    and pre-restore copies. Any failure before the swap leaves the live graph
    untouched; a swap failure leaves the verified temp + pre-restore copies intact.
    """
    _validate_org_id(org_id)
    live_name = target_graph or graph_name
    if not backup_key.endswith("dump.enc"):
        raise ValueError("backup_key must reference a dump.enc object")
    # Tenant isolation: the backup must belong to the requesting org.
    # Defense in depth — the API already derives org_id from auth, but the
    # pipeline must not accept a cross-org key (a leaked/guessed key would
    # otherwise restore another tenant's graph into this org's live graph).
    if not backup_key.startswith(f"backups/{org_id}/"):
        raise ValueError(
            f"backup_key does not belong to team {org_id} — cross-team restore rejected"
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
    # Fail-closed tenant isolation: a missing OR mismatched org_id is rejected.
    if manifest.get("org_id") != org_id:
        raise ValueError(
            f"Backup manifest does not belong to team {org_id} — cross-team restore rejected"
        )
    # Fail-closed graph isolation within the org: the backup belongs to one
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
        # #661: sweep archives encrypt with REGISTRY_STREAM_KEY, user-facing
        # backups with TORTOISE_BACKUP_KEY — try the FULL candidate chain
        # (#2318: both roles' active + retained-previous keys, deduped) before
        # failing, so both restore paths accept both archive types AND archives
        # encrypted under a rotated-out key stay decryptable during the overlap
        # window.
        candidates = _decrypt_candidate_keys(key)
        if not candidates:
            raise ValueError(f"Cannot restore: {e}") from e
        try:
            payload = json.loads(_try_decrypt_with_chain(blob, candidates))
        except ValueError:
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

    result = _restore_into_temp_verify_swap(
        db, payload,
        live_name=live_name,
        stamp=(None if drill else lambda: _stamp_backup_restored(registry, org_id)),
    )
    result["backup_key"] = backup_key
    return result


def prune_backups(
    storage: BackupStorage,
    org_id: str,
    keep_daily: int = 7,
    keep_weekly: int = 4,
    *,
    keep_hourly: int = 0,
    graph_id: str | None = None,
) -> list[str]:
    """Delete old backups: keep ``keep_daily`` newest, plus ``keep_weekly`` weekly
    anchors (one per ISO week) beyond the daily window. Returns deleted backup_ids.

    ``keep_hourly=0`` (default) preserves the legacy behavior byte-for-byte:
    keep ALL backups younger than ``keep_daily`` days.

    ``keep_hourly>0`` (sub-daily cadence) REPLACES the daily keep-all rule:
    keep ALL backups younger than ``keep_hourly`` hours, then one anchor per
    UTC DAY-bucket for ages between ``keep_hourly`` and ``keep_daily`` days
    (bounded by the daily horizon), then the ``keep_weekly`` weekly anchors.
    This bounds an org at hourly cadence to ~24 hourly + ~7 daily-anchors + 4
    weekly (≈35 objects/pool) — #2373: the anchor granularity was hour-
    buckets (retaining ~172/pool over 7 days), contradicting this docstring,
    the DR runbook, and #2319's lock-window premise; day anchors restore the
    documented intent. Newest-first iteration keeps the newest backup of each
    bucket.

    Delete-path trust: objects are keyed by the STORAGE KEY they were found
    under, never by a manifest's self-declared ``backup_id`` — a forged or
    stale manifest cannot trigger deletion of another (newer) backup's objects.

    ``graph_id`` (#2313): when set, retention is computed over ONLY that
    graph's pool (``backups/{org}/{graph}/`` — independent per-graph
    retention, Option A). When None, the legacy org-wide pool is pruned
    (pre-#2313 flat artifacts); per-graph nested keys are NEVER deleted by a
    org-wide prune (their retention is owned by the per-graph prune). This
    split keeps the pre-#2313 contract byte-identical for existing callers.
    """
    _validate_org_id(org_id)
    if graph_id is not None:
        _validate_graph_id(graph_id)
    now = datetime.now(timezone.utc)  # noqa: UP017
    deleted: list[str] = []
    kept_weekly: set[tuple[int, int]] = set()
    kept_day_buckets: set[tuple[int, int, int]] = set()
    hourly_mode = keep_hourly > 0

    prefix = f"backups/{org_id}/{graph_id}/" if graph_id is not None \
        else f"backups/{org_id}/"
    # Newest first by the key-derived backup_id (from the listing prefix).
    manifest_keys = sorted(
        (k for k in storage.list(prefix) if k.endswith("/manifest.json")),
        key=lambda k: k,
        reverse=True,
    )
    for key in manifest_keys:
        try:
            _org, _graph, ts = _parse_backup_key(key)
        except ValueError:
            logger.warning("skipping malformed manifest key %s", key)
            continue
        if graph_id is not None and _graph != graph_id:
            continue  # org-prefix listing under a graph filter — not ours
        if graph_id is None and _graph is not None:
            continue  # per-graph nested keys belong to the per-graph prune
        backup_id = _compose_backup_id(_org, ts, graph_id=_graph)
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
                created = created.replace(tzinfo=timezone.utc)  # naive → assume UTC  # noqa: UP017
        except (ValueError, TypeError):
            # corrupt/naive-mismatch timestamps are deleted — a bad date must
            # never abort pruning of the org's other backups (#2319: a
            # bucket-locked object is skipped + logged, never a pool abort)
            if _delete_backup_objects(storage, backup_id):
                deleted.append(backup_id)
            continue

        # Hourly window (sub-daily mode): keep everything younger than
        # keep_hourly hours.
        if hourly_mode:
            age_hours = (now - created).total_seconds() / 3600.0
            if age_hours < keep_hourly:
                continue

        if (now - created).days < keep_daily:
            if hourly_mode:
                # Day anchor (#2373): keep the newest per UTC DAY-bucket
                # within the daily horizon (bounded — no anchors beyond
                # keep_daily days). Iteration is newest-first, so the first
                # backup seen per bucket is the newest and the rest are
                # deleted. Hour-bucket granularity over-retained (~172/pool
                # vs the documented ~35); day anchors implement the
                # docstring/runbook intent.
                bucket = (created.year, created.month, created.day)
                if bucket not in kept_day_buckets:
                    kept_day_buckets.add(bucket)
                    continue
            else:
                continue  # inside daily window — keep (legacy keep-all)
        else:
            iso = created.isocalendar()
            week = (iso.year, iso.week)
            if week not in kept_weekly and len(kept_weekly) < keep_weekly:
                kept_weekly.add(week)
                continue
        # #2319: bucket-lock-tolerant delete — a locked object is skipped +
        # logged (retried once the retention expires); only actually-deleted
        # backups are returned, so over-retention is bounded and the pool
        # never wedges on a locked object.
        if _delete_backup_objects(storage, backup_id):
            deleted.append(backup_id)
    return deleted
