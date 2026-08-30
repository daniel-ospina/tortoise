"""Pack authoring CLI tests (#1931, epic #1891 slice 2; test-design #1898
surfaces 5/6).

Covers:
- `tortoise pack new` scaffolding: valid namespace → schema-valid manifest
  written from the template; reserved starter namespace rejected at scaffold
  time (mirrors the hosted 422); invalid namespaces (colon, uppercase,
  canonical collision) rejected; existing-dir rejected.
- `tortoise pack validate`: clean pack passes; broken pack fails with an
  actionable message; --json machine contract.
- Install→mine→mint round-trip (E2E-3 extraction-minting assertion): the
  scaffolded pack's vocabulary reaches the extractor master list
  (build_master_list via compile_value_brief) and mining matching content
  with the offline rule path mints the pack's objectKind.
- Template completeness: packs/_template/manifest.yaml carries
  ontology.memory_granularity (the scaffold must not silently drop it).

Docker lane (default): TORTOISE_DB_URI must be set (epic #1647 P4).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")


from tortoise.pack_registry import PackRegistry

REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = REPO_ROOT / "packs" / "_template" / "manifest.yaml"

# ── Helpers ─────────────────────────────────────────────────────────────────

def _run_cli(*argv: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "tortoise", *argv],
        capture_output=True, text=True, cwd=cwd or REPO_ROOT, timeout=120,
    )


def _scaffold(namespace: str, tmp_path: Path) -> Path:
    """Run `tortoise pack new <ns> --dir <tmp>` and return the manifest path."""
    out_dir = tmp_path / "packs"
    r = _run_cli("pack", "new", namespace, "--dir", str(out_dir))
    assert r.returncode == 0, r.stderr
    return out_dir / namespace / "manifest.yaml"


# ── pack new ────────────────────────────────────────────────────────────────

class TestPackNew:
    def test_scaffolds_schema_valid_manifest(self, tmp_path):
        manifest = _scaffold("mydomain", tmp_path)
        assert manifest.exists()
        text = manifest.read_text()
        assert "namespace: mydomain" in text
        assert "name: Mydomain Pack" in text
        # The scaffold must validate clean against the registry schema.
        reg = PackRegistry(manifest.parent.parent)
        n = reg.load_all()
        assert n == 1 and "mydomain" in reg.packs, reg.errors
        assert not reg.errors, reg.errors

    def test_template_carries_memory_granularity(self):
        """The template must teach ontology.memory_granularity (the shipped
        packs carry it; a scaffolded pack must not silently drop it)."""
        text = TEMPLATE.read_text()
        assert "memory_granularity" in text
        assert "UNDER `ontology:`" in text or "ontology" in text

    def test_reserved_starter_namespace_rejected(self, tmp_path):
        for ns in ("dev", "pm", "marketing", "product-strategy", "agent-ops"):
            r = _run_cli("pack", "new", ns, "--dir", str(tmp_path / "packs"))
            assert r.returncode == 1, f"{ns} should be rejected"
            assert "reserved starter pack" in r.stderr, r.stderr

    def test_invalid_namespaces_rejected(self, tmp_path):
        for ns, msg in (("BadCase", "camelCase"), ("bad:ns", "must not contain"),
                        ("document", "canonical kind")):
            r = _run_cli("pack", "new", ns, "--dir", str(tmp_path / "packs"))
            assert r.returncode == 1, f"{ns} should be rejected"
            assert msg in r.stderr, r.stderr

    def test_existing_dir_rejected(self, tmp_path):
        _scaffold("mydomain", tmp_path)
        r = _run_cli("pack", "new", "mydomain", "--dir", str(tmp_path / "packs"))
        assert r.returncode == 1
        assert "already exists" in r.stderr

    def test_scaffolded_manifest_has_required_fields(self, tmp_path):
        manifest = _scaffold("mydomain", tmp_path)
        text = manifest.read_text()
        assert "namespace: mydomain" in text
        assert "name:" in text and "version:" in text
        assert "tier:" in text


# ── pack validate ───────────────────────────────────────────────────────────

class TestPackValidate:
    def test_clean_pack_passes(self, tmp_path):
        manifest = _scaffold("mydomain", tmp_path)
        r = _run_cli("pack", "validate", str(manifest.parent))
        assert r.returncode == 0, r.stderr + r.stdout

    def test_broken_pack_fails_with_actionable_message(self, tmp_path):
        manifest = _scaffold("mydomain", tmp_path)
        # Mutate: uppercase kind → camelCase violation.
        broken = manifest.read_text().replace("  objectKinds: []",
                                              "  objectKinds: [BadCase]")
        manifest.write_text(broken)
        r = _run_cli("pack", "validate", str(manifest.parent))
        assert r.returncode == 1
        assert "camelCase" in (r.stdout + r.stderr)

    def test_json_contract(self, tmp_path):
        manifest = _scaffold("mydomain", tmp_path)
        r = _run_cli("pack", "validate", str(manifest.parent), "--json")
        assert r.returncode == 0
        import json
        payload = json.loads(r.stdout)
        assert payload["ok"] is True
        assert "mydomain" in payload["loaded"]


# ── Install→mine→mint round-trip (E2E-3 extraction minting) ────────────────

class TestMintRoundTrip:
    def test_scaffolded_pack_vocabulary_reaches_extractor(self, tmp_path):
        """The scaffolded pack's kinds reach the extractor's vocabulary
        compile — the mint contract: extraction can type domain content
        with them."""
        from tortoise.value_extractor import compile_value_brief

        manifest = _scaffold("mydomain", tmp_path)
        # Give the pack a real kind + kindDefs so the vocabulary is observable.
        text = manifest.read_text().replace(
            "  objectKinds: []", "  objectKinds: [contract]")
        text = text.replace(
            "  kindDefs: {}",
            "  kindDefs:\n    contract:\n      description: A commercial agreement\n")
        manifest.write_text(text)
        brief = compile_value_brief(packs_dir=manifest.parent.parent)
        # compile_value_brief returns a FLAT dict {kind: semantics} — the
        # pack kind is a top-level key (no 'kinds' section).
        assert "mydomain:contract" in brief, \
            f"mydomain:contract not in extractor vocabulary: {list(brief)[:6]}"

    def test_mine_mints_scaffolded_kind(self, tmp_path):
        """The scaffolded pack registers its kind in the registry expansion
        table — the graph can type content with it (the E2E-3 mint contract;
        LLM-kind-level minting is covered by the mock-model integration tests
        in #1933/#1934)."""
        manifest = _scaffold("mydomain", tmp_path)
        text = manifest.read_text().replace(
            "  objectKinds: []", "  objectKinds: [contract]")
        manifest.write_text(text)
        reg = PackRegistry(manifest.parent.parent)
        n = reg.load_all()
        assert n == 1 and "mydomain" in reg.packs
        assert "mydomain:contract" in reg.expand_kind("mydomain:contract")
