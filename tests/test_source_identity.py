"""Unit tests for S0a source identity (``tortoise/source_identity.py``).

Issue #5012.  These assert the *normalisation contract*: the differences that
cannot change which resource is addressed are collapsed, and the ambiguous
ones (scheme, ``www.``) are deliberately preserved.  No graph, no model.
"""

from __future__ import annotations

import pytest

from tortoise.source_identity import (
    SourceIdentity,
    compute_content_hash,
    normalize_source_url,
    source_identity,
)


class TestNormalizeCollapsesProvenVariants:
    """Each case is a real spelling difference that must not mint a 2nd Source."""

    @pytest.mark.parametrize(
        "raw",
        [
            "https://example.com/a/b/",           # trailing slash
            "https://example.com/a/b#section-3",  # fragment
            "https://example.com/a/b?utm_source=news&utm_medium=email",
            "HTTPS://EXAMPLE.COM/a/b",            # scheme + host case
            "https://Example.Com:443/a/b",        # default port
            "https://example.com//a/b",           # repeated slash
        ],
    )
    def test_collapses_to_one_form(self, raw: str) -> None:
        assert normalize_source_url(raw) == "https://example.com/a/b"

    def test_param_order_collapses(self) -> None:
        assert normalize_source_url(
            "https://example.com/a/b?b=2&a=1"
        ) == normalize_source_url("https://example.com/a/b?a=1&b=2")

    def test_root_slash_is_preserved(self) -> None:
        assert normalize_source_url("https://example.com") == "https://example.com/"
        assert normalize_source_url("https://example.com/") == "https://example.com/"

    def test_non_default_port_preserved(self) -> None:
        assert (
            normalize_source_url("https://example.com:8443/a")
            == "https://example.com:8443/a"
        )

    def test_ipv6_literal_keeps_its_brackets(self) -> None:
        assert normalize_source_url("https://[::1]:443/a") == "https://[::1]/a"
        assert normalize_source_url("https://[2001:db8::1]/a") == "https://[2001:db8::1]/a"

    def test_query_values_sorted_deterministically(self) -> None:
        assert (
            normalize_source_url("https://e.com/p?z=1&a=2&a=1")
            == "https://e.com/p?a=1&a=2&z=1"
        )

    def test_blank_query_values_kept(self) -> None:
        assert normalize_source_url("https://e.com/p?flag") == "https://e.com/p?flag="


class TestNormalizePreservesAmbiguousDifferences:
    """A false merge costs more than a kept duplicate (§16.4)."""

    def test_http_and_https_are_not_merged(self) -> None:
        assert normalize_source_url("http://e.com/a") != normalize_source_url(
            "https://e.com/a"
        )

    def test_www_is_not_stripped(self) -> None:
        assert normalize_source_url("https://www.e.com/a") != normalize_source_url(
            "https://e.com/a"
        )

    def test_semantic_looking_params_are_kept(self) -> None:
        # `ref`/`source`/`si` can be semantic on real APIs — never stripped.
        assert (
            normalize_source_url("https://e.com/p?ref=abc&source=docs")
            == "https://e.com/p?ref=abc&source=docs"
        )

    def test_distinct_paths_stay_distinct(self) -> None:
        assert normalize_source_url("https://e.com/a") != normalize_source_url(
            "https://e.com/b"
        )


class TestNonNetworkIdentitiesAreOpaque:
    """`session:` / `corpus://` are canonical already — never rewrite them."""

    @pytest.mark.parametrize(
        "ident",
        [
            "session:abc-123",
            "session:file_notes.md",
            "corpus://notes/session.md",
            "doc_readme.md",
            "github_pr:1234",
            "",
        ],
    )
    def test_returned_verbatim(self, ident: str) -> None:
        assert normalize_source_url(ident) == ident

    def test_colon_in_name_is_not_a_scheme(self) -> None:
        assert normalize_source_url(":leading") == ":leading"


class TestIdempotence:
    @pytest.mark.parametrize(
        "raw",
        [
            "HTTPS://Example.COM/a/b/?utm_source=x&b=2&a=1#frag",
            "https://example.com:8443/a/",
            "session:abc",
            "corpus://notes/x.md",
        ],
    )
    def test_normalize_twice_is_stable(self, raw: str) -> None:
        once = normalize_source_url(raw)
        assert normalize_source_url(once) == once


class TestComputeContentHash:
    def test_str_and_bytes_agree(self) -> None:
        assert compute_content_hash("hello") == compute_content_hash(b"hello")

    def test_known_value_and_length(self) -> None:
        h = compute_content_hash("hello")
        assert h == (
            "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
        )

    def test_differs_for_different_content(self) -> None:
        assert compute_content_hash("a") != compute_content_hash("b")


class TestSourceIdentity:
    def test_carries_raw_canonical_and_hash(self) -> None:
        ident = source_identity("HTTPS://E.com/a/?utm_source=x#f", "deadbeef")
        assert isinstance(ident, SourceIdentity)
        assert ident.raw_url == "HTTPS://E.com/a/?utm_source=x#f"
        assert ident.canonical_url == "https://e.com/a"
        assert ident.content_hash == "deadbeef"
        assert ident.is_network is True

    def test_absent_hash_stays_none_never_empty_string(self) -> None:
        assert source_identity("session:x").content_hash is None

    def test_non_network_identity_is_not_network(self) -> None:
        assert source_identity("session:x").is_network is False
