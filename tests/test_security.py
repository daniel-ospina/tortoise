"""Tests for tortoise.security shared security primitives (#329)."""
from __future__ import annotations  # noqa: I001

import re
import glob
import os  # noqa: F401
from pathlib import Path  # noqa: F401

import pytest

from tortoise.security import (
    KNOWN_REL_TYPES,
    resolve_under_base,
    ingest_dir_is_safe,
    redact_error,
    validate_document_id,
    validate_entity_type,
    validate_filter_key,
    validate_rel_type,
)


# ── validate_filter_key ────────────────────────────────────────────────────

class TestValidateFilterKey:
    def test_legit_keys_accepted(self):
        for key in ("createdAt", "pointKind", "wing", "status", "content_hash",
                    "_private", "topic_x"):
            assert validate_filter_key(key) == key

    def test_reserved_keys_rejected(self):
        for key in ("kind", "skip", "limit", "kind_0", "kind_1", "kind_99"):
            with pytest.raises(ValueError, match="Reserved filter key"):
                validate_filter_key(key)

    def test_punctuation_and_injection_payloads_rejected(self):
        payloads = [
            "x`} DETACH DELETE (n) //",       # backtick breakout
            "x' OR 1=1 //",                    # quote injection
            "a; MATCH (n) DETACH DELETE n",    # statement injection
            "x} = 1",                          # brace breakout
            "col = $1",                        # param injection
        ]
        for key in payloads:
            with pytest.raises(ValueError, match="Invalid filter key"):
                validate_filter_key(key)

    def test_unicode_keys_rejected(self):
        for key in ("émoji", "中", "clave_ü", "١"):
            with pytest.raises(ValueError, match="Invalid filter key"):
                validate_filter_key(key)

    def test_empty_and_non_string_rejected(self):
        with pytest.raises(ValueError):
            validate_filter_key("")
        with pytest.raises(ValueError):
            validate_filter_key(None)  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            validate_filter_key(123)  # type: ignore[arg-type]


# ── validate_rel_type ──────────────────────────────────────────────────────

class TestValidateRelType:
    def test_known_types_accepted(self):
        for rel in ("IMPL", "NAND", "hasPart", "CORRECTS", "TAGGED",
                    "aboutPoint", "mitigated_by", "extractedFrom", "INPUT"):
            assert validate_rel_type(rel) == rel

    def test_unknown_and_hostile_rejected(self):
        for rel in ("NONE_SUCH", "IMPL]->(x:Point {id:'p2'}) DETACH DELETE x //",
                    "X}", "IMPL ", "impl", ""):
            with pytest.raises(ValueError, match="Invalid relationship type"):
                validate_rel_type(rel)

    def test_known_rel_types_superset_of_inventory(self):
        """Drift test: KNOWN_REL_TYPES must cover every edge type used in code."""
        txt = ""
        for f in glob.glob("tortoise/**/*.py", recursive=True):
            if "__pycache__" in f or f.endswith(("security.py", "sdk.py")):
                continue  # docstrings document the concept with examples
            txt += open(f).read() + "\n"  # noqa: SIM115
        inventory = set(re.findall(r"\-\[:([A-Za-z_]+)", txt))
        missing = inventory - KNOWN_REL_TYPES
        assert not missing, f"Edge types used in code but missing from KNOWN_REL_TYPES: {missing}"


# ── validate_entity_type ───────────────────────────────────────────────────

class TestValidateEntityType:
    def test_valid_accepted(self):
        for et in ("point", "event", "subject", "document", "object", "operator", "source"):
            assert validate_entity_type(et) == et

    def test_invalid_and_hostile_rejected(self):
        for et in ("Point", "POINT", "point ", "Point; DETACH", "x", ""):
            with pytest.raises(ValueError, match="Invalid entity_type"):
                validate_entity_type(et)


# ── validate_document_id ───────────────────────────────────────────────────

class TestValidateDocumentId:
    def test_ulid_and_basename_accepted(self):
        for doc_id in (
            "01HZ1234567890ABCDEFGHIJKLMNOPQRS",   # ULID-like
            "foo.md", ".hidden.md", "-dash.md", "résumé.md",
            "session-2026-08-07.md",
        ):
            assert validate_document_id(doc_id) == doc_id

    def test_path_traversal_rejected(self):
        for doc_id in ("/etc/passwd", "../x", "..", ".", "a/../b", "a\\..\\b", "..\\x"):
            with pytest.raises(ValueError, match="Invalid document id"):
                validate_document_id(doc_id)

    def test_control_and_oversize_rejected(self):
        with pytest.raises(ValueError):
            validate_document_id("a\x00b")
        with pytest.raises(ValueError):
            validate_document_id("x" * 300)
        with pytest.raises(ValueError):
            validate_document_id("")


# ── resolve_under_base ─────────────────────────────────────────────────────

class TestResolveUnderBase:
    def test_positive_under_base(self, tmp_path):
        base = tmp_path / "base"
        base.mkdir()
        (base / "docs").mkdir()
        target = base / "docs" / "file.md"
        target.write_text("x")
        assert resolve_under_base(str(target), str(base)) == target.resolve()

    def test_base_none_fails_closed(self, tmp_path):
        f = tmp_path / "x.md"
        f.write_text("x")
        assert resolve_under_base(str(f), None) is None

    def test_absolute_outside_base_rejected(self, tmp_path):
        base = tmp_path / "base"
        base.mkdir()
        outside = tmp_path / "outside.md"
        outside.write_text("x")
        assert resolve_under_base(str(outside), str(base)) is None

    def test_dotdot_rejected(self, tmp_path):
        base = tmp_path / "base"
        base.mkdir()
        assert resolve_under_base(str(base / ".." / "evil.md"), str(base)) is None

    def test_sibling_prefix_rejected(self, tmp_path):
        """/tmp/data vs /tmp/data_evil — the classic prefix-match bug."""
        base = tmp_path / "data"
        base.mkdir()
        evil = tmp_path / "data_evil"
        evil.mkdir()
        assert resolve_under_base(str(evil / "x.md"), str(base)) is None

    def test_symlink_escape_rejected(self, tmp_path):
        base = tmp_path / "base"
        base.mkdir()
        secret = tmp_path / "secret.md"
        secret.write_text("topsecret")
        link = base / "link.md"
        link.symlink_to(secret)
        assert resolve_under_base(str(link), str(base)) is None

    def test_relative_escape_rejected(self, tmp_path, monkeypatch):
        base = tmp_path / "base"
        base.mkdir()
        # relative candidate that escapes base when resolved against base's CWD
        assert resolve_under_base("../evil.md", str(base)) is None


# ── ingest_dir_is_safe ─────────────────────────────────────────────────────

class TestIngestDirIsSafe:
    def test_absolute_no_dotdot_accepted(self, tmp_path):
        d = tmp_path / "corpus"
        d.mkdir()
        assert ingest_dir_is_safe(str(d), None) is True

    def test_relative_rejected(self):
        assert ingest_dir_is_safe("corpus", None) is False
        assert ingest_dir_is_safe("./corpus", None) is False

    def test_dotdot_rejected(self, tmp_path):
        assert ingest_dir_is_safe(str(tmp_path / ".."), None) is False

    def test_base_enforced(self, tmp_path):
        base = tmp_path / "base"
        base.mkdir()
        inside = base / "docs"
        inside.mkdir()
        outside = tmp_path / "other"
        outside.mkdir()
        assert ingest_dir_is_safe(str(inside), str(base)) is True
        assert ingest_dir_is_safe(str(outside), str(base)) is False

    def test_empty_rejected(self):
        assert ingest_dir_is_safe("", None) is False


# ── redact_error ───────────────────────────────────────────────────────────

class TestRedactError:
    def test_redacts_credentials_and_paths(self):
        err = ValueError("connection to postgres://user:secret@db:5432/tortoise failed, file /etc/passwd.db")
        out = redact_error(err)
        assert "secret" not in out
        assert "ValueError" in out

    def test_generic_message(self):
        out = redact_error(RuntimeError("boom"))
        assert out == "RuntimeError: boom"
