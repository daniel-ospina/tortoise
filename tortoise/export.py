"""Self-serve graph export — ``tortoise export`` (#1388, epic #1230 Task 1).

Wraps the production-verified logical dump engine (``hosted_backup.dump_graph``,
``tortoise-logical-dump-v1``) in a versioned, encrypted artifact envelope
(``tortoise-export-v1``). Encrypt-by-default: AES-256-GCM via the backup
engine; the artifact key is caller-supplied (``TORTOISE_BACKUP_KEY``) or a
fresh ephemeral key printed once on stdout at export time (never written to
disk — the artifact itself never carries key material, only its fingerprint).

Artifact (single JSON document):
    clear header (ZERO graph content):
        format, artifact_version, encrypted, algorithm, key_fingerprint,
        exporter_version, exported_at, source_surface, blob_sha256
    blob_b64: base64 of AES-256-GCM(canonical JSON of the inner envelope)

Inner envelope (encrypted)::
    {format: "tortoise-export-v1", payload_sha256, payload: {logical dump verbatim}}

Integrity is a two-link chain:
    blob_sha256    (clear header)      = sha256 of the encrypted blob
                                         — verifiable PRE-decrypt (tamper/truncate)
    payload_sha256 (inside, encrypted) = sha256 of the canonical plaintext dump
                                         — verifiable POST-decrypt

Canonical serialization is byte-stable: ``json.dumps(sort_keys=True,
separators=(",", ":"))`` — the frozen contract the hosted import side (#1389)
builds against.
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
from datetime import datetime, timezone

from tortoise.hosted_backup import DUMP_FORMAT, decrypt_backup, encrypt_backup

EXPORT_FORMAT = "tortoise-export-v1"
ARTIFACT_VERSION = 1
EXPORTER_VERSION = "1.0.0"
ALGORITHM = "AES-256-GCM"
KEY_FINGERPRINT_HEX = 8  # sha256 prefix length in hex chars (clear-header identity)
_EPHEMERAL_KEY_BYTES = 32  # AES-256

# Clear-header keys that MUST contain zero graph content (asserted in tests).
HEADER_KEYS = (
    "format",
    "artifact_version",
    "encrypted",
    "algorithm",
    "key_fingerprint",
    "exporter_version",
    "exported_at",
    "source_surface",
    "blob_sha256",
)


class ExportError(RuntimeError):
    """Artifact envelope error — malformed, tampered, or wrong artifact key."""


# ── canonical serialization ──────────────────────────────────────────────────


def _importable_pack_status(status: str | None) -> str:
    """#1936: clamp an exported status to the set the IMPORTER accepts.

    The exporter used to write ``:PackInstall.status`` verbatim while the
    importer's shape gate accepts only ``active`` | ``disabled`` | ``removed``
    — so a graph holding any other value produced an artifact its own importer
    rejected pre-restore, and an artifact is sha-pinned and usually the only
    surviving copy of a graph that is gone. "Export your graph, restore it"
    has to be total.

    No in-product writer can produce an out-of-vocabulary status (``pack_state``
    and ``pack_manifest_store`` both validate), so this is a residual against
    an out-of-band graph write — which is exactly the case a restore exists
    for.

    A non-empty unknown status clamps to ``disabled``: it never WIDENS a
    tenant's vocabulary on restore, and it agrees with the ``activated``
    boolean emitted beside it, which is ``(status or "active") == "active"``
    — False for any such value. A FALSY status (absent, ``None``, ``""``)
    clamps to ``active`` for the same reason: it is what ``activated``
    already says, and it is how ``pack_state`` itself normalises a missing
    status (``status or PACK_STATUS_ACTIVE``).
    """
    if status in ("active", "disabled", "removed"):
        return status
    return "disabled" if status else "active"


def collect_pack_config(proj) -> dict:
    """#1936: the pack-config block carried INSIDE the encrypted payload.

    Reads the graph's ``PackInstall`` activation records (namespace, version,
    source, status) plus the ``:PackManifest`` nodes' YAML (custom packs only —
    starter packs live in the shared catalog, yaml=null). Shape (v1):
    ``{schema_version: 1, packs: [{namespace, version, activated, status,
    yaml}]}`` — ``activated`` mirrors the record's status/active, ``status``
    carries the FULL activation state (``active`` | ``disabled`` |
    ``removed``), ``yaml`` is the custom manifest text or null for starter
    packs.

    ``status`` is what makes restore parity (#1936 indicator 2) expressible:
    ``activated`` alone collapses ``disabled`` and ``removed`` into one
    boolean, so an importer could not tell "the tenant switched this pack
    off" (carry it, write ``disabled``) from "the tenant removed this pack".

    ⛔ A REMOVED PACK STILL CARRIES ITS MANIFEST, and the docs elsewhere say so:
    a live graph removes through ``pack_state.set_graph_packs``, which only sets
    ``:PackInstall.status='removed'`` and KEEPS the ``:PackManifest`` — so a real
    export is ``status: removed`` WITH ``yaml``. The ``yaml: null`` removal shape
    comes from the caller-less ``delete_tenant_manifest``. Either way the entry
    must be SKIPPED on import: never re-installed ``active`` (the tombstone-
    erasing widening this PR fixes, which is what the with-yaml shape did before
    it), and never failed as an unknown starter (all the ``yaml: null`` shape
    could raise). Additive within v1: the shape validator leaves unknown keys
    unconstrained, so a pre-``status`` importer ignores it.
    """
    records = []
    try:
        rows = proj.g.query(
            "MATCH (p:PackInstall) RETURN p.namespace, p.version, "
            "p.source, p.status ORDER BY p.namespace"
        ).result_set
        for ns, version, source, status in rows:
            yaml_text = None
            if source == "custom":
                mrows = proj.g.query(
                    "MATCH (m:PackManifest {namespace: $ns}) RETURN m.yaml",
                    params={"ns": ns},
                ).result_set
                yaml_text = mrows[0][0] if mrows else None
            records.append({
                "namespace": ns,
                "version": version or "0.1.0",
                "activated": (status or "active") == "active",
                "status": _importable_pack_status(status),
                "yaml": yaml_text,
            })
    except Exception:
        # failure must not fail the export (the graph dump is the payload).
        return {"schema_version": 1, "packs": []}
    return {"schema_version": 1, "packs": records}


def canonical_json_bytes(payload: dict) -> bytes:
    """Byte-stable canonical serialization (frozen design decision #4)."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def key_fingerprint(key: bytes) -> str:
    """8-hex-char sha256 prefix of the artifact key (clear-header identity)."""
    return hashlib.sha256(key).hexdigest()[:KEY_FINGERPRINT_HEX]


def resolve_export_key(env_key: str | None) -> tuple[bytes, bool]:
    """Resolve the artifact key.

    Returns (key, ephemeral). ``TORTOISE_BACKUP_KEY`` (base64 32 bytes, same
    validation as the backup engine) wins when set; otherwise a fresh 32-byte
    key is generated. The ephemeral key must be printed to stdout exactly once
    by the caller and never persisted.
    """
    if env_key:
        from tortoise.hosted_backup import _get_backup_key
        return _get_backup_key(), False
    return secrets.token_bytes(_EPHEMERAL_KEY_BYTES), True


# ── envelope build ───────────────────────────────────────────────────────────


def build_inner_envelope(dump: dict) -> dict:
    """Inner (encrypted) envelope: the logical dump + payload_sha256 link."""
    payload_sha256 = hashlib.sha256(canonical_json_bytes(dump)).hexdigest()
    return {
        "format": EXPORT_FORMAT,
        "payload_sha256": payload_sha256,
        "payload": dump,
    }


def build_artifact(
    dump: dict,
    key: bytes | None = None,
    *,
    source_surface: str = "selfhost",
    encrypted: bool = True,
    exported_at: str | None = None,
) -> dict:
    """Build a full ``tortoise-export-v1`` artifact dict from a logical dump.

    ``key`` is required when ``encrypted``. The clear header carries zero
    graph content; only the (encrypted) blob carries nodes/edges/props.
    """
    inner = build_inner_envelope(dump)
    if encrypted:
        blob = encrypt_backup(canonical_json_bytes(inner), key=key)
    else:
        blob = canonical_json_bytes(inner)
    return {
        "format": EXPORT_FORMAT,
        "artifact_version": ARTIFACT_VERSION,
        "encrypted": encrypted,
        "algorithm": ALGORITHM if encrypted else None,
        "key_fingerprint": key_fingerprint(key) if encrypted else None,
        "exporter_version": EXPORTER_VERSION,
        "exported_at": exported_at or datetime.now(timezone.utc).isoformat(),  # noqa: UP017
        "source_surface": source_surface,
        "blob_sha256": hashlib.sha256(blob).hexdigest(),
        "blob_b64": base64.b64encode(blob).decode("ascii"),
    }


def artifact_bytes(artifact: dict) -> bytes:
    """Serialize an artifact dict to its canonical on-disk bytes."""
    return canonical_json_bytes(artifact)


# ── envelope verification (shared with the hosted import side, #1389) ────────


def parse_artifact(data: bytes) -> dict:
    """Parse artifact bytes and validate format/version (fail closed)."""
    try:
        artifact = json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        raise ExportError(f"Artifact is not valid JSON: {e}") from e
    if not isinstance(artifact, dict):
        raise ExportError("Artifact root must be a JSON object")
    fmt = artifact.get("format")
    ver = artifact.get("artifact_version")
    if fmt != EXPORT_FORMAT or ver != ARTIFACT_VERSION:
        raise ExportError(
            f"Unsupported artifact {fmt!r} v{ver!r} — expected "
            f"{EXPORT_FORMAT} v{ARTIFACT_VERSION}"
        )
    for key in ("blob_b64", "blob_sha256", "encrypted"):
        if key not in artifact:
            raise ExportError(f"Artifact missing required header field {key!r}")
    return artifact


def verify_blob(artifact: dict) -> bytes:
    """Pre-decrypt integrity: recompute sha256 of the blob; fail closed."""
    try:
        blob = base64.b64decode(artifact["blob_b64"], validate=True)
    except (ValueError, TypeError) as e:
        raise ExportError(f"blob_b64 is not valid base64: {e}") from e
    actual = hashlib.sha256(blob).hexdigest()
    if actual != artifact["blob_sha256"]:
        raise ExportError(
            f"blob_sha256 mismatch: header {artifact['blob_sha256']} != computed "
            f"{actual} — artifact truncated or tampered"
        )
    return blob


def open_payload(artifact: dict, key: bytes | None = None) -> dict:
    """Decrypt/parse an artifact and return the inner logical dump dict.

    Fail-closed chain: pre-decrypt blob_sha256 → decrypt (wrong key raises
    ``ValueError``) → payload_sha256 vs canonical plaintext.
    """
    blob = verify_blob(artifact)
    if artifact.get("encrypted"):  # noqa: SIM108
        plaintext = decrypt_backup(blob, key=key)
    else:
        plaintext = blob
    try:
        inner = json.loads(plaintext.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        raise ExportError(f"Decrypted payload is not valid JSON: {e}") from e
    if not isinstance(inner, dict) or inner.get("format") != EXPORT_FORMAT:
        raise ExportError("Inner envelope has an unexpected format")
    dump = inner.get("payload")
    if not isinstance(dump, dict) or dump.get("format") != DUMP_FORMAT:
        raise ExportError(f"Payload is not a {DUMP_FORMAT} logical dump")
    payload_sha256 = hashlib.sha256(canonical_json_bytes(dump)).hexdigest()
    if payload_sha256 != inner.get("payload_sha256"):
        raise ExportError(
            f"payload_sha256 mismatch: {inner.get('payload_sha256')} != "
            f"{payload_sha256} — payload tampered"
        )
    return dump
