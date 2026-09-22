"""#4366 — the consumer-skills licence assertion must be non-vacuous and prose-safe.

``validation/check-license-surface.py`` is executed directly by the REQUIRED CI
job (``.github/workflows/ci.yml`` → ``license-surface``), so the assertion runs on
every PR. What could rot silently is its *composition*: a marker matcher that
stops catching a re-imported BSL header, or one that reds on the served skills'
own prose about licensing — which is how a required check gets taught to be
ignored. Both directions are pinned here against ``tmp_path`` fixtures: no repo
file is mutated, and no network/DB/FalkorDB is used.

The load-bearing cases (each pins a bypass that was real at some point in this
change's review history, or a behaviour that must not regress):

* a missing / non-MIT / non-UTF-8 / composite (MIT + appended BSL) licence file;
* a BSL declaration inside a served file or a licence/notice file — `BUSL`,
  `BUSL-1.1`, the versioned short form `BSL 1.1`, SPDX headers, column-aligned
  canonical names — under every name form the guard recognises (`*.license`
  sidecars, `LICENSE-BSL`, `LICENSE-2.0.txt`, `third_party_licenses.txt`,
  `THIRD-PARTY-NOTICES.txt`, `LICENSES/`);
* a skill directory SYMLINKED into the surface (``Path.rglob`` does not descend
  into one, so it used to be served-but-unasserted);
* prose: the served ``how-to-use-tortoise`` skill discusses BSL — unversioned —
  and a skill saying "Business Source License" in its body must NOT red the
  check.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
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
    """The name forms a narrower pattern missed — dashed/suffixed names, the
    space/bracket duplicates Finder and Windows produce, and `COPYRIGHT`."""
    module = _load()
    for i, name in enumerate(("LICENSE-BSL", "LICENSE-2.0.txt", "third_party_licenses.txt",
                              "COPYING.txt", "LICENSE 2.txt", "LICENSE (copy).txt",
                              "license copy.txt", "COPYRIGHT")):
        assert module.is_licence_file(Path(name)), name
        surface = tmp_path / f"surface-{i}"
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


def test_versioned_short_form_is_a_declaration(tmp_path: Path) -> None:
    """`BSL 1.1` (mariadb.com/bsl11's own abbreviation, and what vendored
    notices carry) is a declaration; the UNVERSIONED acronym is not."""
    module = _load()
    assert module.bsl_declaration("This software is also available under the BSL 1.1.\n") == "BSL 1.1"
    assert module.bsl_declaration("released under BSL1.1\n") == "BSL1.1"
    for spelling in ("BSL-1.1", "BSL_1.1", "BSL v1.1", "BSL version 1.1", "BSL.V1.1"):
        assert module.bsl_declaration(f"available under the {spelling}.\n") is not None, spelling
    assert module.bsl_declaration("BSL is OSI-approved\n") is None
    assert module.bsl_declaration("BSL 1.0 is not the same licence\n") is None
    surface = _make_surface(tmp_path)
    (surface / "LICENSE").write_text(MIT_TEXT)
    (surface / "some-skill" / "SKILL.md").write_text(
        MIT_TEXT + "\n\nThis software is also available under the BSL 1.1.\n"
    )
    errors = module.check_consumer_surface("surface", _spec(module, surface))
    assert any("declares BSL" in e for e in errors), errors


def test_plural_notice_names_are_scanned_whole_file(tmp_path: Path) -> None:
    """`NOTICES.txt` / `THIRD-PARTY-NOTICES.txt` — the standard name for the
    composite licence-list file the whole-file rule exists to catch."""
    module = _load()
    for name in ("NOTICES.txt", "THIRD-PARTY-NOTICES.txt", "THIRD_PARTY_NOTICES.md"):
        assert module.is_licence_file(Path(name)), name
        surface = tmp_path / name.replace(".", "-")
        surface.mkdir()
        (surface / "LICENSE").write_text(MIT_TEXT)
        (surface / name).write_text("filler\n" * 30 + BSL_TEXT)
        errors = module.check_consumer_surface("surface", _spec(module, surface))
        assert any(name in e for e in errors), (name, errors)
    assert module.is_licence_file(Path("NOTICES") / "terms.txt") is True


def test_non_ascii_skill_is_still_scanned(tmp_path: Path) -> None:
    """The reads are UTF-8-pinned, so a C/POSIX locale must not silently skip
    every non-ASCII file (all four served skills are heavily non-ASCII).

    This runs the assertion in an `LC_ALL=C` SUBPROCESS: `os.environ` mutations
    after the interpreter has started do not change the locale, so an earlier
    version of this test passed against the unpinned implementation and proved
    nothing (found by the code-review gate). The probe prints the locale it
    actually ran under, and the test refuses to pass unless that locale is
    genuinely non-UTF-8 — a test that cannot fail is not a test.
    """
    surface = _make_surface(tmp_path)
    (surface / "LICENSE").write_text(MIT_TEXT)
    (surface / "some-skill" / "SKILL.md").write_text(
        "# \u2014 em-dash and non-ASCII \u2014\n\nSPDX-License-Identifier: BUSL-1.1\n"
    )
    probe = tmp_path / "locale_probe.py"
    probe.write_text(
        "import importlib.util, locale, pathlib, sys\n"
        f"spec = importlib.util.spec_from_file_location('cls', {str(CHECK_PATH)!r})\n"
        "mod = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(mod)\n"
        "surface = pathlib.Path(sys.argv[1])\n"
        "errors = mod.check_consumer_surface('probe', {'path': surface,\n"
        "    'licence': surface / 'LICENSE',\n"
        "    'required': ['MIT License', 'Copyright (c) 2026 Premise Labs',\n"
        "                 'Permission is hereby granted, free of charge']})\n"
        "print('PREFERRED=' + str(locale.getpreferredencoding(False)))\n"
        "print('VERDICT=' + ('CAUGHT' if errors else 'MISSED'))\n",
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "LC_ALL": "C",
        "PYTHONCOERCECLOCALE": "0",
        "PYTHONUTF8": "0",
        "PYTHONIOENCODING": "utf-8",
    }
    result = subprocess.run(
        [sys.executable, str(probe), str(surface)], env=env, capture_output=True, text=True,
    )
    assert "PREFERRED=" in result.stdout, (result.stdout, result.stderr)
    preferred = result.stdout.split("PREFERRED=", 1)[1].splitlines()[0]
    assert "utf-8" not in preferred.lower(), (
        f"the probe did not actually run under a non-UTF-8 locale ({preferred!r}) — "
        "this test would be vacuous"
    )
    assert "VERDICT=CAUGHT" in result.stdout, (preferred, result.stdout, result.stderr)


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
    for name in ("LICENSE", "LICENSE.md", "COPYING", "NOTICE", "NOTICES.txt", "COPYRIGHT",
                 "MIT.license", "COPYING.txt", "COPYING.LESSER", "LICENSE-BSL",
                 "LICENSE-2.0.txt", "third_party_licenses.txt", "THIRD-PARTY-NOTICES.txt",
                 "LICENSE 2.txt", "LICENSE (copy).txt", "license copy.txt", "LICENSE.txt.bak"):
        assert module.is_licence_file(Path(name)), name
    assert module.is_licence_file(Path("SKILL.md")) is False
    # an alphanumeric continuation means the word is not the token
    assert module.is_licence_file(Path("licensee-notes.md")) is False
    assert module.is_licence_file(Path("licensing-notes.md")) is False
    assert module.is_licence_file(Path("licenses") / "terms.txt") is True
    assert module.is_licence_file(Path("licences") / "terms.txt") is True
    assert module.is_licence_file(Path("NOTICES") / "terms.txt") is True


# ── the served installer surface, in-band MIT notice (#4398) ────────────────

SERVED_SCRIPT_KEY = "website/apps/dashboard/public/install-tortoise-skills.sh"


def _script_spec(module, path: Path) -> dict:
    return {"path": path, "required": module.SERVED_SCRIPTS[SERVED_SCRIPT_KEY]["required"]}


def test_served_installer_notice_is_registered_and_in_band() -> None:
    """The shipped installer is asserted, and its MIT notice sits in the PRELUDE
    — the bytes a `curl … | bash` user receives before the script does anything.
    A served `LICENSE` sidecar would never travel with a piped download, so the
    notice must be in the file itself (#4398)."""
    module = _load()
    assert SERVED_SCRIPT_KEY in module.SERVED_SCRIPTS
    assert module.check() == []
    spec = module.SERVED_SCRIPTS[SERVED_SCRIPT_KEY]
    body = spec["path"].read_text(encoding="utf-8")
    assert module.check_served_script(SERVED_SCRIPT_KEY, spec) == []
    assert body.index("Permission is hereby granted, free of charge") < body.index(
        "set -euo pipefail"), "the notice must precede the first executed command"
    assert "SPDX-License-Identifier: MIT" in body.splitlines()[1], body.splitlines()[:3]


def test_served_script_without_the_notice_is_an_error(tmp_path: Path) -> None:
    """MUTATION: delete the in-band MIT notice block (the script names no
    licence). This is the #4398 acceptance criterion — it must RED."""
    module = _load()
    stub = tmp_path / "install.sh"
    stub.write_text("#!/usr/bin/env bash\nset -euo pipefail\necho hi\n", encoding="utf-8")
    errors = module.check_served_script("script", _script_spec(module, stub))
    assert any("Permission is hereby granted" in e for e in errors), errors
    assert any("SPDX-License-Identifier: MIT" in e for e in errors), errors


def test_served_script_with_only_an_spdx_id_is_an_error(tmp_path: Path) -> None:
    """MUTATION: reduce the notice to a bare `SPDX-License-Identifier: MIT`.
    An SPDX id is machine-readable metadata, not the copyright + permission
    notice MIT's condition requires — so the texted markers must still RED."""
    module = _load()
    stub = tmp_path / "install.sh"
    stub.write_text(
        "#!/usr/bin/env bash\n# SPDX-License-Identifier: MIT\nset -euo pipefail\n",
        encoding="utf-8",
    )
    errors = module.check_served_script("script", _script_spec(module, stub))
    assert any("Copyright (c) 2026 Premise Labs" in e for e in errors), errors
    assert any("Permission is hereby granted" in e for e in errors), errors


def test_served_script_declaring_bsl_is_an_error(tmp_path: Path) -> None:
    """MUTATION: add a `SPDX-License-Identifier: BUSL-1.1` header to the served
    script — a consumer artifact must not carry the engine's licence."""
    module = _load()
    stub = tmp_path / "install.sh"
    stub.write_text(MIT_TEXT.replace("MIT License\n", "MIT License\n# SPDX-License-Identifier: BUSL-1.1\n"),
                    encoding="utf-8")
    errors = module.check_served_script("script", _script_spec(module, stub))
    assert any("declares BSL" in e for e in errors), errors


def test_served_script_missing_is_an_error(tmp_path: Path) -> None:
    module = _load()
    errors = module.check_served_script("script", _script_spec(module, tmp_path / "absent.sh"))
    assert any("file missing" in e for e in errors), errors


def test_served_script_directory_is_a_named_error(tmp_path: Path) -> None:
    """MUTATION: point the served-script surface at a DIRECTORY. It must yield
    the same named error the disclosure surface gives, not an
    `IsADirectoryError` traceback (review finding, round 2)."""
    module = _load()
    d = tmp_path / "install-tortoise-skills.sh"
    d.mkdir()
    errors = module.check_served_script("script", _script_spec(module, d))
    assert any("is not a file" in e for e in errors), errors


# ── the /license disclosure page (#4399) ────────────────────────────────────

LICENCE_PAGE_KEY = "website/license.html"


def _page_spec(module, path: Path) -> dict:
    return {"path": path,
            "required": module.LICENCE_DISCLOSURE_SURFACES[LICENCE_PAGE_KEY]["required"]}


def test_licence_page_discloses_every_permissive_surface() -> None:
    """The shipped page is asserted and names all three surfaces."""
    module = _load()
    assert LICENCE_PAGE_KEY in module.LICENCE_DISCLOSURE_SURFACES
    assert module.check() == []
    spec = module.LICENCE_DISCLOSURE_SURFACES[LICENCE_PAGE_KEY]
    assert module.check_disclosure_surface(LICENCE_PAGE_KEY, spec) == []


def test_licence_page_dropping_a_permissive_surface_is_an_error(tmp_path: Path) -> None:
    """MUTATION: delete the Apache-2.0 / MIT disclosure rows, leaving the page
    in its pre-#4399 BSL-only state — the exact #4399 defect."""
    module = _load()
    page = tmp_path / "license.html"
    page.write_text(
        "<p>Tortoise is licensed under the Business Source License 1.1.</p>",
        encoding="utf-8",
    )
    errors = module.check_disclosure_surface("page", _page_spec(module, page))
    assert any("Apache-2.0" in e for e in errors), errors
    assert any("MIT License" in e for e in errors), errors


def test_licence_page_missing_is_an_error(tmp_path: Path) -> None:
    """MUTATION: delete the page entirely — a missing disclosure surface must
    not pass silently."""
    module = _load()
    errors = module.check_disclosure_surface("page", {"path": tmp_path / "absent.html",
                                                       "required": []})
    assert any("file missing" in e for e in errors), errors


def test_non_utf8_licence_page_is_a_named_error(tmp_path: Path) -> None:
    """MUTATION: re-save the page as non-UTF-8. It must be a NAMED error, not a
    `UnicodeDecodeError` traceback — the same fail-closed contract the other
    surfaces carry (review finding, round 1)."""
    module = _load()
    page = tmp_path / "license.html"
    page.write_bytes(b"\xff\xfe\x00<\x00h\x00t\x00")
    errors = module.check_disclosure_surface("page", _page_spec(module, page))
    assert any("not UTF-8" in e for e in errors), errors


def test_licence_page_directory_is_a_named_error(tmp_path: Path) -> None:
    """MUTATION: point the surface at a DIRECTORY — an `IsADirectoryError`
    traceback would red the required check for an unexplainable reason."""
    module = _load()
    d = tmp_path / "license.html"
    d.mkdir()
    errors = module.check_disclosure_surface("page", _page_spec(module, d))
    assert any("is not a file" in e for e in errors), errors


# ── check() must actually WIRE the new surfaces ─────────────────────────────
# A test that only calls the helpers directly passes even if the two loops in
# `check()` are deleted, so the required CI job could stop enforcing the
# surfaces while every unit test stayed green (review finding, round 1). These
# two pin the wiring by mutating the module-level surface to a non-conforming
# tmp file and asserting `check()` — not the helper — reports it.


def test_check_wires_the_served_script_surface(tmp_path: Path) -> None:
    """MUTATION: delete the `SERVED_SCRIPTS` loop from `check()`."""
    module = _load()
    stub = tmp_path / "install.sh"
    stub.write_text("#!/usr/bin/env bash\nset -euo pipefail\n", encoding="utf-8")
    original = dict(module.SERVED_SCRIPTS[SERVED_SCRIPT_KEY])
    module.SERVED_SCRIPTS[SERVED_SCRIPT_KEY]["path"] = stub
    try:
        errors = module.check()
    finally:
        module.SERVED_SCRIPTS[SERVED_SCRIPT_KEY].clear()
        module.SERVED_SCRIPTS[SERVED_SCRIPT_KEY].update(original)
    assert any("Permission is hereby granted" in e for e in errors), errors


def test_check_wires_the_disclosure_surface(tmp_path: Path) -> None:
    """MUTATION: delete the `LICENCE_DISCLOSURE_SURFACES` loop from `check()`."""
    module = _load()
    page = tmp_path / "license.html"
    page.write_text(
        "<p>Tortoise is licensed under the Business Source License 1.1.</p>",
        encoding="utf-8",
    )
    original = dict(module.LICENCE_DISCLOSURE_SURFACES[LICENCE_PAGE_KEY])
    module.LICENCE_DISCLOSURE_SURFACES[LICENCE_PAGE_KEY]["path"] = page
    try:
        errors = module.check()
    finally:
        module.LICENCE_DISCLOSURE_SURFACES[LICENCE_PAGE_KEY].clear()
        module.LICENCE_DISCLOSURE_SURFACES[LICENCE_PAGE_KEY].update(original)
    assert any("Apache-2.0" in e for e in errors), errors
