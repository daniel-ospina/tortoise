"""#2779 slice 1 — org display-name vs identifier naming rules.

The shared vectors in ``tests/fixtures/org_naming_vectors.json`` are the
contract between this Python implementation and BOTH JS mirrors
(``website/apps/dashboard/src/orgNaming.js`` and
``supabase/functions/_shared/orgNaming.ts``). If a vector fails here, do not
edit the expectation without editing all three implementations.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tortoise.org_naming import (
    DISPLAY_NAME_MAX,
    ID_PATTERN,
    RESERVED_IDENTIFIERS,
    identifier_error,
    slugify_id,
    validate_display_name,
)

_VECTORS = json.loads(
    (Path(__file__).resolve().parent / "fixtures" / "org_naming_vectors.json").read_text()
)


class TestValidateDisplayName:
    def test_accepts_the_reported_name_verbatim(self):
        """The #2779 symptom: 'test org for multi-organisation' was rejected
        with a message that never named the space. It must survive verbatim."""
        assert validate_display_name("test org for multi-organisation") == \
            "test org for multi-organisation"

    def test_collapses_whitespace_runs_and_trims(self):
        assert validate_display_name("Café  Ltd") == "Café Ltd"
        assert validate_display_name("  Acme  ") == "Acme"
        assert validate_display_name("Acme\nCorp") == "Acme Corp"

    def test_shared_vectors(self):
        for v in _VECTORS["display_names"]:
            if v["valid"]:
                assert validate_display_name(v["input"]) == v["normalized"], v["input"]
            else:
                with pytest.raises(ValueError) as exc:
                    validate_display_name(v["input"])
                for needle in v["error_contains"]:
                    assert needle in str(exc.value), (v["input"], str(exc.value))

    @pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
    def test_blank_rejected_with_required(self, blank):
        with pytest.raises(ValueError, match="required"):
            validate_display_name(blank)

    def test_too_long_names_the_length(self):
        with pytest.raises(ValueError, match="65"):
            validate_display_name("a" * (DISPLAY_NAME_MAX + 1))

    def test_control_char_names_the_code_point(self):
        with pytest.raises(ValueError, match="U\\+0000"):
            validate_display_name("Acme\x00Corp")

    def test_none_is_blank(self):
        with pytest.raises(ValueError, match="required"):
            validate_display_name(None)


class TestSlugifyId:
    def test_shared_vectors(self):
        for v in _VECTORS["slugify"]:
            assert slugify_id(v["input"]) == v["expected"], v["input"]

    def test_reported_name_derives_the_expected_identifier(self):
        assert slugify_id("test org for multi-organisation") == \
            "test-org-for-multi-organisation"

    def test_strips_and_prefixes(self):
        assert slugify_id("!!!") == "org-"
        assert slugify_id("") == "org-"
        assert slugify_id("  Acme  ") == "Acme"

    def test_reserved_words_are_never_emitted(self):
        for word in RESERVED_IDENTIFIERS:
            assert slugify_id(word) == f"org-{word}"

    def test_truncates_to_64(self):
        assert len(slugify_id("x" * 200)) == DISPLAY_NAME_MAX

    def test_every_derived_id_satisfies_the_pattern(self):
        """The identifier contract: a free-text display name must NEVER reach
        an identifier or graph namespace unslugged."""
        samples = [v["input"] for v in _VECTORS["slugify"]]
        samples += [v["input"] for v in _VECTORS["display_names"]]
        samples += ["a/b", "a b", "../../etc/passwd", "Café Ltd", "x" * 300,
                    "team_{injection}", "a\nb", "\x00", "  "]
        for raw in samples:
            derived = slugify_id(raw)
            assert derived, raw
            assert len(derived) <= DISPLAY_NAME_MAX, raw
            assert ID_PATTERN.match(derived), (raw, derived)
            assert derived not in RESERVED_IDENTIFIERS, (raw, derived)


class TestIdentifierError:
    def test_shared_vectors(self):
        for v in _VECTORS["identifier_errors"]:
            err = identifier_error(v["input"])
            if not v["message_contains"]:
                assert err is None, (v["input"], err)
            else:
                assert err is not None, v["input"]
                for needle in v["message_contains"]:
                    assert needle in err, (v["input"], err)

    def test_names_the_space_and_suggests_the_slug(self):
        err = identifier_error("test org for multi-organisation")
        assert err is not None
        assert '" "' in err
        assert "test-org-for-multi-organisation" in err

    def test_names_the_at_sign(self):
        assert '"@"' in (identifier_error("bad@name") or "")

    def test_names_a_leading_separator(self):
        err = identifier_error("-acme")
        assert err is not None and '"-"' in err and "acme" in err

    def test_names_the_reserved_word(self):
        err = identifier_error("registry")
        assert err is not None and "registry" in err and "org-registry" in err

    def test_legal_ids_return_none(self):
        assert identifier_error("acme") is None
        assert identifier_error("Acme-Corp_2") is None
        assert identifier_error("a" * 64) is None

    def test_exactly_65_is_rejected(self):
        assert identifier_error("a" * 65) is not None
