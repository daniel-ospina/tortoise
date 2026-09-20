"""#4366 — the consumer-skills licence assertion must be non-vacuous and prose-safe.

``validation/check-license-surface.py`` is executed directly by the REQUIRED CI
job (``.github/workflows/ci.yml`` → ``license-surface``), so the assertion runs on
every PR. What could rot silently is its *composition*: a marker matcher that
stops catching a re-imported BSL header, or one that reds on the served skills'
own prose about licensing — which is how a required check gets taught to be
ignored. Both directions are pinned here against ``tmp_path`` fixtures: no repo
file is mutated, and no network/DB/FalkorDB is used.

The load-bearing cases (each verified RED against the implementation it
replaced):

* a missing / non-MIT / non-UTF-8 / composite (MIT + appended BSL) licence file;
* a BSL declaration inside a served file or a licence/notice file — SPDX header,
  bare ``BUSL`` token, column-aligned canonical name — including the name forms
  a prefix-only pattern missed (``*.license`` sidecars, ``LICENSE-BSL``,
  ``LICENSE-2.0.txt``, ``third_party_licenses.txt``, ``LICENSES/``);
* a skill directory SYMLINKED into the surface (``Path.rglob`` does not descend
  into one, so it used to be served-but-unasserted);
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
    """A `*.license` sidecar is scanned whole-file — the name is the only reason
    the declaration is found, so the fixture pushes it past the 20-line window
    (a fixture that lands inside the window would pass either way)."""
    module = _load()
    surface = _make_surface(tmp_path)
    (surface / "LICENSE").write_text(MIT_TEXT)
    (surface / "MIT.license").write_text("filler\n" * 30 + BSL_TEXT)
    errors = module.check_consumer_surface("surface", _spec(module, surface))
    assert any("MIT.license" in e for e in errors), errors


def test_dashed_and_suffixed_licence_names_are_scanned_whole_file(tmp_path: Path) -> None:
    """`LICENSE-BSL` / `LICENSE-2.0.txt` / `third_party_licenses.txt` — the
    forms a prefix-only pattern missed while the comment claimed `LICENSE*`."""
    module = _load()
    for name in ("LICENSE-BSL", "LICENSE-2.0.txt", "third_party_licenses.txt", "COPYING.txt"):
        assert module.is_licence_file(Path(name)), name
        surface = tmp_path / name.replace(".", "-")
        surface.mkdir()
        (surface / "LICENSE").write_text(MIT_TEXT)
        (surface / name).write_text("filler\n" * 30 + BSL_TEXT)
        errors = module.check_consumer_surface("surface", _spec(module, surface))
        assert any(name in e for e in errors), (name, errors)


def test_non_utf8_licence_is_a_named_error_not_a_traceback(tmp_path: Path) -> None:
    module = _load()
    surface = _make_surface(tmp_path)
    (surface / "LICENSE").write_bytes(b"\xff\xfe\x00M\x00I\x00T")
    errors = module.check_consumer_surface("surface", _spec(module, surface))
    assert any("not UTF-8" in e for e in errors), errors


def test_symlinked_skill_directory_is_scanned(tmp_path: Path) -> None:
    """`Path.rglob` does not descend into a symlinked dir; the surface scan must
    (the vite `public/` copy follows links, so such a dir would be served)."""
    module = _load()
    surface = _make_surface(tmp_path)
    (surface / "LICENSE").write_text(MIT_TEXT)
    outside = tmp_path / "elsewhere" / "linked-skill"
    outside.mkdir(parents=True)
    (outside / "LICENSE-BSL").write_text("filler\n" * 30 + BSL_TEXT)
    (surface / "linked-skill").symlink_to(outside, target_is_directory=True)
    errors = module.check_consumer_surface("surface", _spec(module, surface))
    assert any("linked-skill" in e for e in errors), errors


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
    """Declarations are matched however they are cased/aligned, and the matcher
    returns the EVIDENCE (the matched text), not the regex."""
    module = _load()
    assert module.bsl_declaration("<!-- spdx-license-identifier: busl-1.1 -->\n") == "busl-1.1"
    assert module.bsl_declaration("license: BUSL\n") == "BUSL"
    assert module.bsl_declaration("licensed under the BUSL\n") == "BUSL"
    assert module.bsl_declaration("busling\n") is None
    assert module.bsl_declaration("Copyright 2026 - Business   Source   License\n") == (
        "Business   Source   License"
    )
    # a token ANYWHERE counts (prose included): the artifact is claiming BUSL
    assert module.bsl_declaration("filler\n" * 30 + "This is under BUSL-1.1.\n") == "BUSL-1.1"
    # ... but the canonical NAME past the window of a content file does not
    assert module.bsl_declaration("filler\n" * 40 + "Business Source License 1.1\n") is None
    # and the bare acronym the served skills use in prose is not a token
    assert module.bsl_declaration("# x\n\nBSL is not OSI-approved\n") is None


def test_licence_file_name_detection() -> None:
    module = _load()
    for name in ("LICENSE", "LICENSE.md", "COPYING", "NOTICE", "MIT.license", "COPYING.txt",
                 "LICENSE-BSL", "LICENSE-2.0.txt", "third_party_licenses.txt"):
        assert module.is_licence_file(Path(name)), name
    assert module.is_licence_file(Path("SKILL.md")) is False
    assert module.is_licence_file(Path("licensee-notes.md")) is False
    assert module.is_licence_file(Path("licenses") / "terms.txt") is True
    assert module.is_licence_file(Path("licences") / "terms.txt") is True
