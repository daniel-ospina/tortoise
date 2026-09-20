"""#4366 — the consumer-skills licence assertion must be non-vacuous and prose-safe.

``validation/check-license-surface.py`` is executed directly by the REQUIRED CI
job (``.github/workflows/ci.yml`` → ``license-surface``), so the assertion runs on
every PR. What could rot silently is its *composition*: a marker matcher that
stops catching a re-imported BSL header, or one that reds on the served skills'
own prose about licensing — which is how a required check gets taught to be
ignored. Both directions are pinned here against ``tmp_path`` fixtures: no repo
file is mutated, and no network/DB/FalkorDB is used.

The load-bearing cases (each verified RED against the pre-fix implementation
before this change landed):

* a missing / non-MIT / composite (MIT + appended BSL) licence file;
* a BSL declaration inside a served file (SPDX header, bare ``BUSL`` token,
  column-aligned canonical name) — including the ``*.license`` sidecar form that
  the first implementation claimed in its comment but did not match;
* prose: the served ``how-to-use-tortoise`` skill discusses BSL, and a skill
  saying "Business Source License" in its body must NOT red the check.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHECK_PATH = ROOT / "validation" / "check-license-surface.py"

MIT_TEXT = """MIT License

Copyright (c) 2026 Premise Labs

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND.
"""

BSL_TEXT = """License text copyright (c) 2020 MariaDB Corporation Ab.

Parameters

Licensor:             Premise Labs (Daniel Ospina)
Business Source License 1.1
"""


def _load():
    spec = importlib.util.spec_from_file_location("check_license_surface_4366", CHECK_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _spec(module, surface: Path) -> dict:
    """A CONSUMER_SURFACES-shaped spec pointing at a tmp surface."""
    return {
        "path": surface,
        "licence": surface / "LICENSE",
        "required": module.CONSUMER_SURFACES["website/apps/dashboard/public/skills"]["required"],
    }


def _make_surface(tmp_path: Path) -> Path:
    surface = tmp_path / "skills"
    (surface / "some-skill").mkdir(parents=True)
    (surface / "some-skill" / "SKILL.md").write_text("---\nname: some-skill\n---\n\n# Body\n")
    return surface


# ── the repo's real surface ───────────────────────────────────────────────────


def test_repo_surface_passes_and_is_registered() -> None:
    """The shipped surface is asserted, and the real check exits clean."""
    module = _load()
    assert "website/apps/dashboard/public/skills" in module.CONSUMER_SURFACES
    assert module.check() == []
    assert (module.CONSUMER_SURFACES["website/apps/dashboard/public/skills"]["path"] / "LICENSE").is_file()


# ── the licence file itself ──────────────────────────────────────────────────


def test_missing_licence_is_an_error(tmp_path: Path) -> None:
    module = _load()
    errors = module.check_consumer_surface("surface", _spec(module, _make_surface(tmp_path)))
    assert any("no per-directory licence" in e for e in errors), errors


def test_non_mit_licence_is_an_error(tmp_path: Path) -> None:
    module = _load()
    surface = _make_surface(tmp_path)
    (surface / "LICENSE").write_text(BSL_TEXT)
    errors = module.check_consumer_surface("surface", _spec(module, surface))
    assert any("not a valid MIT licence" in e for e in errors), errors


def test_composite_licence_is_an_error(tmp_path: Path) -> None:
    """MIT markers kept, BSL terms appended — the copy-paste accident class.

    A presence-only MIT check passes this; the BSL declaration scan is what
    rejects it, and the licence file must not be exempt from that scan.
    """
    module = _load()
    surface = _make_surface(tmp_path)
    (surface / "LICENSE").write_text(MIT_TEXT + "\n" + BSL_TEXT)
    errors = module.check_consumer_surface("surface", _spec(module, surface))
    assert any("declares BSL" in e for e in errors), errors


def test_mit_licence_alone_is_clean(tmp_path: Path) -> None:
    module = _load()
    surface = _make_surface(tmp_path)
    (surface / "LICENSE").write_text(MIT_TEXT)
    assert module.check_consumer_surface("surface", _spec(module, surface)) == []


# ── files under the surface ──────────────────────────────────────────────────


def test_bsl_spdx_header_in_a_served_file_is_an_error(tmp_path: Path) -> None:
    module = _load()
    surface = _make_surface(tmp_path)
    (surface / "LICENSE").write_text(MIT_TEXT)
    skill = surface / "some-skill" / "SKILL.md"
    skill.write_text("<!-- SPDX-License-Identifier: BUSL-1.1 -->\n" + skill.read_text())
    errors = module.check_consumer_surface("surface", _spec(module, surface))
    assert any("declares BSL" in e for e in errors), errors


def test_licence_sidecar_suffix_is_scanned_whole_file(tmp_path: Path) -> None:
    """A `*.license` sidecar is a licence/notice file (the suffix form)."""
    module = _load()
    surface = _make_surface(tmp_path)
    (surface / "LICENSE").write_text(MIT_TEXT)
    (surface / "MIT.license").write_text(MIT_TEXT + "\n" + BSL_TEXT)
    errors = module.check_consumer_surface("surface", _spec(module, surface))
    assert any("MIT.license" in e for e in errors), errors


def test_licenses_directory_is_scanned_whole_file(tmp_path: Path) -> None:
    module = _load()
    surface = _make_surface(tmp_path)
    (surface / "LICENSE").write_text(MIT_TEXT)
    (surface / "licenses").mkdir()
    (surface / "licenses" / "terms.txt").write_text("filler\n" * 30 + BSL_TEXT)
    errors = module.check_consumer_surface("surface", _spec(module, surface))
    assert any("terms.txt" in e for e in errors), errors


# ── prose must NOT be a declaration ──────────────────────────────────────────


def test_canonical_name_in_body_prose_is_not_a_declaration(tmp_path: Path) -> None:
    """The served skills legitimately discuss licensing in their own text."""
    module = _load()
    surface = _make_surface(tmp_path)
    (surface / "LICENSE").write_text(MIT_TEXT)
    (surface / "some-skill" / "SKILL.md").write_text(
        "---\nname: some-skill\n---\n\n# Body\n"
        + "filler\n" * 30
        + "\nThe options compared were AGPLv3-dual, the Business Source License, and SSPL.\n"
    )
    assert module.check_consumer_surface("surface", _spec(module, surface)) == []


def test_marker_matcher_is_case_and_whitespace_insensitive() -> None:
    module = _load()
    assert module.bsl_declaration("<!-- spdx-license-identifier: busl-1.1 -->\n") == r"\bbusl\b"
    assert module.bsl_declaration("license: BUSL\n") == r"\bbusl\b"
    assert module.bsl_declaration("Copyright 2026 - Business   Source   License\n")
    assert module.bsl_declaration("# x\n\nBSL is not OSI-approved\n") is None
    assert module.bsl_declaration("filler\n" * 40 + "Business Source License 1.1\n") is None


def test_licence_file_name_detection() -> None:
    module = _load()
    for name in ("LICENSE", "LICENSE.md", "COPYING", "NOTICE", "MIT.license", "COPYING.txt"):
        assert module.is_licence_file(Path(name)), name
    assert module.is_licence_file(Path("SKILL.md")) is False
    assert module.is_licence_file(Path("licenses") / "terms.txt") is True
