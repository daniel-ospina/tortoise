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

import pytest

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
        """The scaffolder must refuse what the validator refuses (#2786).

        Every case here would otherwise produce a manifest its OWN validator
        rejects — `my domain` did exactly that before the whitespace leg landed on
        the writer side.
        """
        for ns, msg in (
            ("BadCase", "camelCase"),
            ("bad:ns", "must not contain"),
            ("my domain", "whitespace"),
            ("document", "canonical kind"),
            # #2786: an ACTION-axis word. The pre-#2786 hand-rolled union here
            # omitted CANONICAL_ACTION_KINDS, so this is the case that
            # discriminates `canonical_kind_words()` from it.
            ("implement", "canonical kind"),
        ):
            r = _run_cli("pack", "new", ns, "--dir", str(tmp_path / "packs"))
            assert r.returncode == 1, f"{ns} should be rejected"
            assert msg in r.stderr, r.stderr

    def test_reserved_words_are_derived_from_the_owner_map(self):
        """One definition (#2786): the reserved word set IS `canonical_kind_words()`.

        This pins the DERIVATION — the word set is computed from the owner map, so
        `KIND_OWNER_AXES` cannot gain an axis that the reservation ignores. It is
        deliberately a self-comparison: it never runs the CLI, so it says nothing
        about what the scaffolder does with the set. Whether `pack new <word>`
        actually REFUSES a word from every axis (the drift this file used to have —
        the action axis was missing from a hand-rolled union here) is pinned
        end-to-end by `test_every_owned_axis_word_is_rejected_by_the_cli`.
        """
        from tortoise.pack_registry import KIND_OWNER_AXES, canonical_kind_words

        words = canonical_kind_words()
        for axis, kinds in KIND_OWNER_AXES.items():
            missing = {k for k in kinds if k.lower() not in words}
            assert not missing, f"{axis} missing from the reserved words: {missing}"

    @pytest.mark.parametrize("word,axis", [
        # ONE word per owned axis, each driving the real CLI. `implement`
        # (actionKinds) is the decisive case: the pre-#2786 hand-rolled union in
        # `_cmd_pack_new` omitted the action axis, so it scaffolded a namespace
        # colliding with a core word. A unit check over `canonical_kind_words()`
        # cannot see that — reverting the CLI to the hand-rolled union leaves it
        # green — so the pin has to go through `_run_cli`.
        ("tag", "objectKinds"),
        ("deployment", "eventKinds"),
        ("hypothesis", "pointKinds"),
        ("transcript", "documentKinds"),
        ("implement", "actionKinds"),
    ])
    def test_every_owned_axis_word_is_rejected_by_the_cli(self, tmp_path, word, axis):
        """A namespace must not collide with ANY word core owns, on any axis."""
        out_dir = tmp_path / "packs"
        r = _run_cli("pack", "new", word, "--dir", str(out_dir))
        assert r.returncode == 1, f"{word} ({axis}) should be rejected: {r.stdout}"
        assert "canonical kind" in r.stderr, r.stderr
        # …and nothing was written: the scaffold is refused BEFORE the manifest
        # exists, so there is no pack a validator would then refuse.
        assert not (out_dir / word / "manifest.yaml").exists()

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

    def test_prints_the_relation_verdict_table(self, tmp_path):
        """#2766: `pack validate` reports each declared relation's write-path
        verdict. A relation whose declaration binds no path is REPORTED, never a
        failure (warn-not-block, W2) — the pack still validates clean."""
        manifest = _scaffold("mydomain", tmp_path)
        text = manifest.read_text().replace(
            "  relations: []",
            "  relations:\n"
            "  - predicate: blocks\n"
            "    from: Object\n"
            "    to: Object\n"
            "  - predicate: hasPart\n"
            "    from: Object\n"
            "    to: Object\n",
        )
        assert "predicate: blocks" in text, "template shape changed"
        manifest.write_text(text)
        r = _run_cli("pack", "validate", str(manifest.parent))
        out = r.stdout + r.stderr
        assert r.returncode == 0, out  # warn-not-block: not an error
        assert "relation write verdicts" in out
        assert "predicate-not-in-allowlist" in out
        assert "mydomain.blocks" in out
        assert "mydomain.hasPart" in out

    def test_json_carries_relation_verdicts(self, tmp_path):
        manifest = _scaffold("mydomain", tmp_path)
        text = manifest.read_text().replace(
            "  relations: []",
            "  relations:\n"
            "  - predicate: blocks\n"
            "    from: Object\n"
            "    to: Object\n"
            "    declarationOnly: true\n",
        )
        manifest.write_text(text)
        r = _run_cli("pack", "validate", str(manifest.parent), "--json")
        assert r.returncode == 0, r.stdout + r.stderr
        import json
        payload = json.loads(r.stdout)
        rels = payload["relation_verdicts"]["relations"]
        assert [x["write_verdict"] for x in rels] == ["declaration-only"]
        assert rels[0]["write_paths"] == []
        assert payload["relation_verdicts"]["counts"] == {"declaration-only": 1}

    def test_a_broken_pack_whose_dir_is_not_its_namespace_is_not_a_false_green(
            self, tmp_path):
        """The verdict filter keyed on the DIRECTORY name, and the registry keys
        a diagnostic on the MANIFEST's namespace — two shipped packs differ
        (`packs/project-management` → `pm`, `packs/investment-portfolio-manager`
        → `portfolio`), so a broken manifest in such a pack printed
        `✓ Clean — pack(s) validate` and exited 0 while the registry had
        already ISOLATED it.

        FAIL-ON: `ns == target.name` / `(packs_dir / ns / "manifest.yaml")`
        against a directory spelled differently from the namespace. REACHABLE:
        the same pack under its OWN directory name exits 1, so the difference
        is the spelling and not the manifest.
        """
        manifest = _scaffold("mydomain", tmp_path)
        # Same manifest, a directory name that is NOT the namespace.
        renamed = manifest.parent.parent / "my-domain-overlay"
        manifest.parent.rename(renamed)
        manifest = renamed / "manifest.yaml"
        # A CROSS-PACK typo, not a schema error: the registry keys a cross-pack
        # diagnostic by the MANIFEST's namespace, which is what makes the
        # directory-name filter lose it. (A schema error is keyed by directory,
        # so it would be found either way.)
        manifest.write_text(
            manifest.read_text()
            .replace("  objectKinds: []", "  objectKinds: [contract]")
            .replace("  kindDefs: {}",
                     "  kindDefs:\n    contract:\n      nearMisses: [gadget]"))

        r = _run_cli("pack", "validate", str(renamed))
        out = r.stdout + r.stderr
        assert r.returncode == 1, out
        assert "Clean" not in out
        assert "nearMisses target 'gadget'" in out, out

        # The `--json` machine contract must not read clean either.
        rj = _run_cli("pack", "validate", str(renamed), "--json")
        assert rj.returncode == 1, rj.stdout + rj.stderr
        import json
        payload = json.loads(rj.stdout)
        assert payload["ok"] is False
        assert "mydomain" in payload["errors"]

        # CONTROL: a clean pack beside it exits 0, so exit 1 above is the
        # broken manifest and not the command.
        clean = _scaffold("otherdomain", tmp_path)
        ok = _run_cli("pack", "validate", str(clean.parent))
        assert ok.returncode == 0, ok.stdout + ok.stderr

    def test_a_broken_pack_is_reported_when_the_PACKS_DIR_is_the_target(
            self, tmp_path):
        """The same false green one level up: passing the `packs/` DIRECTORY
        ran the identical per-directory-name filter over every namespace, so a
        broken `portfolio`-style pack was silently absent from a
        whole-catalog run — the one mode a CI gate would use."""
        manifest = _scaffold("mydomain", tmp_path)
        renamed = manifest.parent.parent / "my-domain-overlay"
        manifest.parent.rename(renamed)
        broken = renamed / "manifest.yaml"
        broken.write_text(
            broken.read_text()
            .replace("  objectKinds: []", "  objectKinds: [contract]")
            .replace("  kindDefs: {}",
                     "  kindDefs:\n    contract:\n      nearMisses: [gadget]"))

        r = _run_cli("pack", "validate", str(tmp_path / "packs"))
        out = r.stdout + r.stderr
        assert r.returncode == 1, out
        assert "Clean" not in out
        assert "nearMisses target 'gadget'" in out, out

    def test_a_missing_declared_dependency_is_not_a_false_green(self, tmp_path):
        """The SECOND fail-open path in the same verdict: it read `reg.errors`
        only, and a declared-but-absent `depends_on` lands in the other bucket,
        `reg.unresolved_refs` (#2781). A pack whose base pack is not installed
        printed `✓ Clean — pack(s) validate` and exited 0 while the dependency
        AND every reference into it did not resolve — the same tree the hosted
        install path refuses, so the local and hosted gates disagreed in the
        fail-open direction.

        FAIL-ON: `ok = not errors`. REACHABLE: the same tree now prints the
        `⚠ unresolved references` section, names the missing pack and exits 1;
        the CONTROL run without the declared dependency exits 0.
        """
        manifest = _scaffold("mydomain", tmp_path)
        text = manifest.read_text()
        assert "depends_on: []" in text, "template shape changed"
        manifest.write_text(text.replace("depends_on: []",
                                         "depends_on: [ghost]"))

        r = _run_cli("pack", "validate", str(manifest.parent))
        out = r.stdout + r.stderr
        assert r.returncode == 1, out
        assert "Clean" not in out
        assert "unresolved references" in out, out
        assert "declared dependency 'ghost'" in out, out
        assert "unresolved reference(s)" in out, out

        # `--json` carries the same bucket, so a machine caller cannot read
        # `ok: true` off a tree whose dependency is absent.
        rj = _run_cli("pack", "validate", str(manifest.parent), "--json")
        assert rj.returncode == 1, rj.stdout + rj.stderr
        import json
        payload = json.loads(rj.stdout)
        assert payload["ok"] is False
        assert payload["errors"] == {}
        assert "mydomain" in payload["unresolved_refs"]

        # CONTROL: the same pack without the unmet declaration is clean again,
        # so the failure is the absent dependency and not a broken command.
        manifest.write_text(text)
        ok = _run_cli("pack", "validate", str(manifest.parent))
        assert ok.returncode == 0, ok.stdout + ok.stderr
        assert "Clean" in ok.stdout

    def test_a_SIBLING_s_unresolved_gap_is_not_this_pack_s_verdict(self, tmp_path):
        """A single-pack target is scoped to THAT pack.

        The scoping filter admitted every sibling whose directory name equals
        its namespace (`(packs_dir / ns / "manifest.yaml").exists()`), so
        `pack validate packs/alpha` reported — and once the missing-peer bucket
        reached the exit code, FAILED on — a sibling's missing dependency, while
        a sibling keyed by a different namespace was silently dropped: two
        identical gaps, two answers.

        FAIL-ON: the directory-existence clause. REACHABLE: `alpha` is clean and
        `beta` is not, in the SAME tree — so `0` vs `1` is the scoping, not the
        catalog.
        """
        alpha_manifest = _scaffold("alpha", tmp_path)
        beta_dir = tmp_path / "packs" / "beta"
        beta_dir.mkdir()
        (beta_dir / "manifest.yaml").write_text(
            alpha_manifest.read_text()
            .replace("namespace: alpha", "namespace: beta")
            .replace("name: Alpha Pack", "name: Beta Pack")
            .replace("depends_on: []", "depends_on: [ghost]"))
        # A third sibling whose DIRECTORY is not its namespace: the filter must
        # match on the namespace too, never admit it by path.
        gamma_dir = tmp_path / "packs" / "gamma-dir"
        gamma_dir.mkdir()
        (gamma_dir / "manifest.yaml").write_text(
            alpha_manifest.read_text()
            .replace("namespace: alpha", "namespace: gam")
            .replace("name: Alpha Pack", "name: Gam Pack")
            .replace("depends_on: []", "depends_on: [ghost]"))

        clean = _run_cli("pack", "validate", str(alpha_manifest.parent))
        assert clean.returncode == 0, clean.stdout + clean.stderr
        assert "Clean" in clean.stdout

        for broken_dir, ns in ((beta_dir, "beta"), (gamma_dir, "gam")):
            r = _run_cli("pack", "validate", str(broken_dir))
            out = r.stdout + r.stderr
            assert r.returncode == 1, out
            assert "Clean" not in out
            assert "unresolved references" in out, out
            assert ns in out, out


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
