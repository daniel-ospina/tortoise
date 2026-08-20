"""CLI export tests (#1388, epic #1230 Task 1).

Covers the ``tortoise export`` contract: ``tortoise-export-v1`` envelope shape
(zero graph content in the clear header), encrypt-by-default (AES-256-GCM via
the backup engine), canonical-sha256 byte stability, tamper/wrong-key
fail-closed, --no-encrypt loud warning, ephemeral-key-printed-once, exit
codes, and the export → restore round-trip into a fresh DB.
"""
from __future__ import annotations  # noqa: I001

import base64
import hashlib  # noqa: F401
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tortoise.export import (
    ARTIFACT_VERSION,
    EXPORT_FORMAT,
    ExportError,
    HEADER_KEYS,
    artifact_bytes,
    build_artifact,
    canonical_json_bytes,
    key_fingerprint,
    open_payload,
    parse_artifact,
    resolve_export_key,
    verify_blob,
)
from tortoise.hosted_backup import DUMP_FORMAT, dump_graph, restore_graph
from tortoise.projection import FalkorProjection

REPO_ROOT = Path(__file__).resolve().parent.parent


def _make_proj(tmpdir: str, name: str = "t.db") -> FalkorProjection:
    return FalkorProjection(os.path.join(tmpdir, name))


def _seed(g, n: int = 4) -> None:
    """Seed a graph with Points + IMPL edges (matches engine test style)."""
    for i in range(n):
        g.query(
            "CREATE (p:Point {id:$id, content:$c, pointKind:$k})",
            params={"id": f"pt-{i}", "c": f"content {i}", "k": "claim"},
        )
    for i in range(n - 1):
        g.query(
            "MATCH (a:Point {id:$a}), (b:Point {id:$b}) CREATE (a)-[:IMPL {weight:0.8}]->(b)",
            params={"a": f"pt-{i}", "b": f"pt-{i+1}"},
        )


def _make_key() -> bytes:
    return os.urandom(32)


def _run_cli(*argv: str, cwd: Path | None = None, env: dict | None = None):
    return subprocess.run(
        [sys.executable, "-m", "tortoise", *argv],
        capture_output=True,
        text=True,
        timeout=180,
        cwd=cwd or REPO_ROOT,
        env=env or os.environ.copy(),
    )


# ── envelope schema + canonical sha256 (S2) ──────────────────────────────────


def test_envelope_shape_clear_header_zero_graph_content():
    dump = {
        "format": DUMP_FORMAT,
        "dumped_at": "2026-08-17T12:00:00+00:00",
        "graph_name": "tortoise",
        "node_count": 2,
        "edge_count": 1,
        "nodes": [{"dump_id": 1, "labels": ["Point"], "props": {"id": "pt-1", "content": "secret"}}],
        "edges": [{"src": 1, "dst": 2, "type": "IMPL", "props": {}}],
    }
    artifact = build_artifact(dump, key=_make_key(), source_surface="embedded")
    assert artifact["format"] == EXPORT_FORMAT
    assert artifact["artifact_version"] == ARTIFACT_VERSION
    assert artifact["encrypted"] is True
    assert artifact["algorithm"] == "AES-256-GCM"
    assert artifact["key_fingerprint"]
    assert artifact["source_surface"] == "embedded"
    assert len(artifact["blob_sha256"]) == 64
    # Clear header carries zero graph content — the "nodes" key must not exist
    # anywhere in the header portion of the artifact.
    header = {k: artifact[k] for k in HEADER_KEYS}
    assert set(header) == set(HEADER_KEYS)
    header_json = json.dumps(header)
    for leaked in ("nodes", "edges", "pt-1", "secret", "IMPL", "Point"):
        assert leaked not in header_json, f"clear header leaks graph content: {leaked!r}"
    assert set(artifact) == set(HEADER_KEYS) | {"blob_b64"}


def test_parse_artifact_rejects_bad_format_and_version():
    with pytest.raises(ExportError):
        parse_artifact(b'{"format": "nope", "artifact_version": 1}')
    with pytest.raises(ExportError):
        parse_artifact(b'{"format": "tortoise-export-v1", "artifact_version": 99}')
    with pytest.raises(ExportError):
        parse_artifact(b"not json")
    with pytest.raises(ExportError):
        parse_artifact(b'{"format": "tortoise-export-v1", "artifact_version": 1}')


def test_canonical_sha256_byte_stable():
    """Same content → byte-identical canonical bytes + identical payload sha."""
    dump_a = {
        "format": DUMP_FORMAT,
        "dumped_at": "2026-08-17T12:00:00+00:00",
        "graph_name": "tortoise",
        "node_count": 1,
        "edge_count": 0,
        "nodes": [{"dump_id": 7, "labels": ["Point"], "props": {"id": "p", "b": 2, "a": 1}}],
        "edges": [],
    }
    dump_b = {
        "edges": [],
        "dumped_at": "2026-08-17T12:00:00+00:00",
        "format": DUMP_FORMAT,
        "graph_name": "tortoise",
        "node_count": 1,
        "edge_count": 0,
        "nodes": [{"dump_id": 7, "labels": ["Point"], "props": {"a": 1, "b": 2, "id": "p"}}],
    }
    assert canonical_json_bytes(dump_a) == canonical_json_bytes(dump_b)
    art_a = build_artifact(dump_a, key=_make_key())
    art_b = build_artifact(dump_b, key=_make_key())  # noqa: F841
    # payload_sha256 lives in the (encrypted) inner envelope — decrypt to check
    from tortoise.export import build_inner_envelope
    assert build_inner_envelope(dump_a)["payload_sha256"] == \
        build_inner_envelope(dump_b)["payload_sha256"]
    # blob_sha256 differs (fresh nonce per encryption) but serialization is stable
    assert artifact_bytes(art_a).startswith(b'{"algorithm"')


def test_tamper_detected_pre_decrypt():
    artifact = build_artifact(
        {"format": DUMP_FORMAT, "dumped_at": "x", "graph_name": "t",
         "node_count": 0, "edge_count": 0, "nodes": [], "edges": []},
        key=_make_key(),
    )
    # Flip one base64 char in the blob → blob_sha256 must not match.
    b64 = bytearray(artifact["blob_b64"].encode())
    b64[10] = ord("0") if b64[10] != ord("0") else ord("1")
    tampered = dict(artifact, blob_b64=bytes(b64).decode())
    with pytest.raises(ExportError, match="blob_sha256 mismatch"):
        verify_blob(tampered)


# ── encryption + round-trip (S3) ─────────────────────────────────────────────


def test_roundtrip_export_restore_into_fresh_db(tmp_path):
    key = _make_key()
    src = _make_proj(str(tmp_path), "src.db")
    _seed(src.g)
    dump = dump_graph(src.g, graph_name="tortoise")
    src.close()

    artifact = build_artifact(dump, key=key, source_surface="embedded")
    parsed = parse_artifact(artifact_bytes(artifact))
    restored_dump = open_payload(parsed, key=key)

    assert restored_dump["format"] == DUMP_FORMAT
    assert restored_dump["node_count"] == 4
    assert restored_dump["edge_count"] == 3

    dst = _make_proj(str(tmp_path), "dst.db")
    try:
        counts = restore_graph(dst.g, restored_dump)
        assert counts == {"nodes": 4, "edges": 3}
        re_dump = dump_graph(dst.g, graph_name="tortoise")
        assert re_dump["node_count"] == dump["node_count"]
        assert re_dump["edge_count"] == dump["edge_count"]
        # Content survives: every source Point id present with same props.
        src_ids = {n["props"]["id"] for n in dump["nodes"]}
        dst_ids = {n["props"]["id"] for n in re_dump["nodes"]}
        assert src_ids == dst_ids
    finally:
        dst.close()


def test_wrong_key_fails_closed():
    artifact = build_artifact(
        {"format": DUMP_FORMAT, "dumped_at": "x", "graph_name": "t",
         "node_count": 0, "edge_count": 0, "nodes": [], "edges": []},
        key=_make_key(),
    )
    with pytest.raises(ValueError, match="decryption failed|wrong"):  # noqa: RUF043
        open_payload(parse_artifact(artifact_bytes(artifact)), key=_make_key())


def test_no_encrypt_artifact_plaintext_openable():
    dump = {"format": DUMP_FORMAT, "dumped_at": "x", "graph_name": "t",
            "node_count": 0, "edge_count": 0, "nodes": [], "edges": []}
    artifact = build_artifact(dump, encrypted=False, source_surface="selfhost")
    assert artifact["encrypted"] is False
    assert artifact["algorithm"] is None
    assert artifact["key_fingerprint"] is None
    # Openable without any key; blob is the plaintext inner envelope.
    opened = open_payload(parse_artifact(artifact_bytes(artifact)), key=None)
    assert opened["node_count"] == 0


def test_resolve_export_key_env_vs_ephemeral(monkeypatch):
    raw = base64.b64encode(os.urandom(32)).decode()
    monkeypatch.setenv("TORTOISE_BACKUP_KEY", raw)
    key, ephemeral = resolve_export_key(raw)
    assert len(key) == 32
    assert ephemeral is False
    monkeypatch.delenv("TORTOISE_BACKUP_KEY")
    key2, ephemeral2 = resolve_export_key("")
    assert len(key2) == 32
    assert ephemeral2 is True
    assert key2 != key
    assert len(key_fingerprint(key2)) == 8


# ── CLI (S1) — subprocess, embedded (FalkorDBLite) source ────────────────────


def _seed_db_file(db_path: str) -> None:
    proj = _make_proj(os.path.dirname(db_path), os.path.basename(db_path))
    _seed(proj.g)
    proj.close()


def test_cli_export_encrypt_by_default_one_json_line(tmp_path):
    db_path = os.path.join(str(tmp_path), "seed.db")
    _seed_db_file(db_path)
    out = os.path.join(str(tmp_path), "graph.tortoise")

    res = _run_cli("export", "--db", db_path, "--output", out)
    assert res.returncode == 0, f"stderr: {res.stderr}"
    assert os.path.exists(out)

    # stdout is exactly ONE JSON line (machine contract).
    line = res.stdout.strip()
    assert len(line.splitlines()) == 1
    summary = json.loads(line)
    assert summary["status"] == "ok"
    assert summary["format"] == EXPORT_FORMAT
    assert summary["artifact_version"] == ARTIFACT_VERSION
    assert summary["encrypted"] is True
    assert summary["algorithm"] == "AES-256-GCM"
    assert summary["node_count"] == 4
    assert summary["edge_count"] == 3
    # Ephemeral key printed exactly once (never persisted to disk).
    assert line.count("key_b64") == 1
    key = base64.b64decode(summary["key_b64"])
    assert len(key) == 32
    assert summary["key_fingerprint"] == key_fingerprint(key)
    assert summary["source_surface"] == "embedded"

    # Artifact on disk: clear header has zero graph content; blob decrypts.
    artifact = json.loads(Path(out).read_text())
    header = {k: artifact[k] for k in HEADER_KEYS}
    assert "nodes" not in json.dumps(header)
    opened = open_payload(parse_artifact(Path(out).read_bytes()), key=key)
    assert opened["node_count"] == 4


def test_cli_export_env_key_used_not_printed(tmp_path):
    db_path = os.path.join(str(tmp_path), "seed.db")
    _seed_db_file(db_path)
    env_key = base64.b64encode(os.urandom(32)).decode()
    env = dict(os.environ.copy(), TORTOISE_BACKUP_KEY=env_key)

    res = _run_cli(
        "export", "--db", db_path, "--output", os.path.join(str(tmp_path), "g.tortoise"),
        env=env,
    )
    assert res.returncode == 0, f"stderr: {res.stderr}"
    summary = json.loads(res.stdout.strip())
    assert "key_b64" not in summary  # caller supplied the key — nothing to print
    assert summary["key_fingerprint"] == key_fingerprint(base64.b64decode(env_key))
    assert "fresh export key" not in res.stderr.lower()


def test_cli_export_no_encrypt_warns_loudly(tmp_path):
    db_path = os.path.join(str(tmp_path), "seed.db")
    _seed_db_file(db_path)
    out = os.path.join(str(tmp_path), "plain.tortoise")

    res = _run_cli("export", "--db", db_path, "--output", out, "--no-encrypt")
    assert res.returncode == 0, f"stderr: {res.stderr}"
    assert "WARNING" in res.stderr and "no-encrypt" in res.stderr.lower()
    summary = json.loads(res.stdout.strip())
    assert summary["encrypted"] is False
    artifact = json.loads(Path(out).read_text())
    assert artifact["encrypted"] is False
    # Blob is plaintext inner-envelope JSON (no decryption needed).
    blob = base64.b64decode(artifact["blob_b64"])
    assert blob.decode().startswith('{"format":"tortoise-export-v1"')


def test_cli_export_exit_codes(tmp_path):
    db_path = os.path.join(str(tmp_path), "seed.db")
    _seed_db_file(db_path)
    good = os.path.join(str(tmp_path), "ok.tortoise")
    # 0 on success
    assert _run_cli("export", "--db", db_path, "--output", good).returncode == 0
    # 1 on unwritable output path
    res = _run_cli(
        "export", "--db", db_path,
        "--output", os.path.join(str(tmp_path), "missing-dir", "x.tortoise"),
    )
    assert res.returncode == 1
    # 1 on unreachable graph
    res = _run_cli("export", "--db", "docker://localhost:1/", "--output", good)
    assert res.returncode == 1
