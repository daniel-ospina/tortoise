"""Unit tests for ``tortoise.file_indexer`` — the pure file-identity module
(epic #900, task T1).

Covers the S1 surface (plan §8.4): canonical hash + identity derivation —
percent-encoding (incl. ``corpus_name`` single-encode), realpath dedup,
escape rejection (outside-root realpath → hard error), derived-id collision,
date normalization, CRLF-immunity; the S6 sourceKind registry surface
(import-time registration, NEUTRAL tier, ``document`` pre-registered); and
the S3 import-flip alias regression net (``_FM_RE`` / ``compute_file_hash``
back-compat aliases in session_indexer/ingest — zero behavior change).
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from tortoise.file_indexer import (
    CLASSIFIER_TO_SOURCE_KIND,
    _FM_RE,
    classify_file,
    compute_file_hash,
    derive_document_id,
    derive_meeting_event_id,
    derive_session_id,
    derive_source_url,
    hash_text,
    normalize_source_date,
    parse_frontmatter,
    slugify,
    source_kind_for_classifier,
)
from tortoise.source_credibility import SOURCE_KIND_DEFAULTS, resolve_source_tier

# ── S6: sourceKind registry (import-time registration, §4.4) ──────────────


class TestSourceKindRegistry:
    def test_agentSession_registered_neutral(self):
        # ONTOLOGY v3.6 #6 value — snake ``agent_session`` RETIRED as a value.
        # Membership asserted explicitly: dict.get would return None for an
        # ABSENT key, so the tier check alone cannot distinguish registered-
        # NEUTRAL from never-registered.
        assert "agentSession" in SOURCE_KIND_DEFAULTS
        assert SOURCE_KIND_DEFAULTS["agentSession"] is None
        assert resolve_source_tier("agentSession") is None  # NEUTRAL

    def test_meeting_summary_registered_neutral(self):
        assert "meeting_summary" in SOURCE_KIND_DEFAULTS
        assert SOURCE_KIND_DEFAULTS["meeting_summary"] is None
        assert resolve_source_tier("meeting_summary") is None  # NEUTRAL

    def test_document_registered_neutral(self):
        # T2-merge note: ``document`` was already registered in
        # SOURCE_KIND_DEFAULTS before file_indexer existed — file_indexer must
        # NOT re-register it (re-registration is state-identical, so this test
        # asserts the observable precondition; the no-re-registration
        # discipline is enforced by keeping §4.4's registration block to the
        # two new kinds only).
        assert "document" in SOURCE_KIND_DEFAULTS
        assert SOURCE_KIND_DEFAULTS["document"] is None

    def test_registration_is_idempotent(self):
        # Re-calling the registrations never raises and never changes the
        # import-time tier (snapshot-then-assert — order-independent).
        from tortoise.source_credibility import register_source_kind_default

        before_a = SOURCE_KIND_DEFAULTS["agentSession"]
        before_m = SOURCE_KIND_DEFAULTS["meeting_summary"]
        register_source_kind_default("agentSession", None)
        register_source_kind_default("meeting_summary", None)
        assert SOURCE_KIND_DEFAULTS["agentSession"] is before_a
        assert SOURCE_KIND_DEFAULTS["meeting_summary"] is before_m

    def test_all_mapped_kinds_registered(self):
        # No unregistered kind can reach the Source write path (§6.2 I26).
        for kind in CLASSIFIER_TO_SOURCE_KIND.values():
            assert kind in SOURCE_KIND_DEFAULTS, f"{kind} must be registered"


# ── Frontmatter parsing (tolerant, degraded={}) ────────────────────────────


class TestParseFrontmatter:
    def test_no_frontmatter(self):
        assert parse_frontmatter("plain body text") == {}

    def test_basic_frontmatter(self):
        fm = parse_frontmatter("---\ntitle: Hello\ndate: 2026-08-05\n---\nbody")
        # Raw YAML values (yaml.safe_load semantics — date becomes a
        # datetime.date object; consumers normalize via normalize_source_date).
        assert fm["title"] == "Hello"
        assert normalize_source_date(fm["date"]) == "2026-08-05T00:00:00+00:00"

    def test_no_newline_after_opening_dashes_is_not_frontmatter(self):
        # ``---sessionId: foo\n---`` must parse as NO frontmatter (canonical
        # boundary pin — health and ingest must agree).
        assert parse_frontmatter("---sessionId: foo\n---\nbody") == {}

    def test_crlf_frontmatter_parses(self):
        # CRLF form of the canonical boundary: text-mode reads normalize line
        # endings before parse in the ingest path, but the boundary regex must
        # also match raw CRLF text (the ``\s*\n`` accepts the CR).
        assert parse_frontmatter("---\r\ntitle: a\r\n---\r\nbody") == {"title": "a"}

    def test_crlf_no_newline_after_dashes_is_not_frontmatter(self):
        assert parse_frontmatter("---sessionId: foo\r\n---\r\nbody") == {}

    def test_malformed_yaml_degrades_to_empty(self):
        assert parse_frontmatter("---\n\tbad: [unclosed\n---\nbody") == {}

    def test_non_dict_yaml_root_degrades_to_empty(self):
        assert parse_frontmatter("---\n- a\n- b\n---\nbody") == {}

    def test_scalar_yaml_root_degrades_to_empty(self):
        assert parse_frontmatter("---\njust a string\n---\nbody") == {}

    def test_eof_closing_dashes(self):
        # Closing ``---`` at EOF with no trailing body is valid frontmatter.
        assert parse_frontmatter("---\ntitle: a\n---") == {"title": "a"}


# ── Hash primitives (§4.5 / OQ-4; §5.1 pin (c)) ────────────────────────────


class TestHash:
    def test_hash_text_known_vector(self):
        # Independent oracle — hardcoded constant (SHA-256 of "hello").
        assert hash_text("hello") == (
            "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
        )

    def test_hash_text_empty(self):
        # Hardcoded constant — SHA-256 of the empty string (the encoding-agnostic
        # input, so only an independent oracle discriminates).
        assert hash_text("") == (
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        )

    def test_crlf_immunity_via_text_mode_read(self, tmp_path):
        # Universal-newlines text mode normalizes line endings BEFORE the
        # hash — LF and CRLF copies of the same content hash equal (the
        # #330 non-convergence class). The primitive itself hashes the buffer
        # AS GIVEN; normalization is the text-mode read's job.
        lf = tmp_path / "lf.md"
        crlf = tmp_path / "crlf.md"
        lf.write_text("line one\nline two\nline three\n", encoding="utf-8")
        crlf.write_bytes(b"line one\r\nline two\r\nline three\r\n")
        assert compute_file_hash(str(lf)) == compute_file_hash(str(crlf))
        # single-read semantics: hash of the text-mode buffer == file hash
        assert compute_file_hash(str(lf)) == hash_text(
            lf.read_text(encoding="utf-8")
        )

    def test_compute_file_hash_matches_ingest_style(self, tmp_path):
        # Compose from the already-pinned primitive: the file hash must equal
        # hash_text over the text-mode buffer (the ingest read semantics —
        # read_text(encoding="utf-8").encode()); hash_text itself is pinned by
        # an independent hardcoded vector.
        p = tmp_path / "a.md"
        p.write_text("---\ntitle: x\n---\nbody\n", encoding="utf-8")
        text = p.read_text(encoding="utf-8")
        assert compute_file_hash(str(p)) == hash_text(text)
        assert compute_file_hash(str(p)) == hash_text("---\ntitle: x\n---\nbody\n")

    def test_compute_file_hash_crlf_immune_non_ascii(self, tmp_path):
        # INDEPENDENT oracle — hardcoded digest for a CRLF + non-ASCII fixture.
        # An encoding regression (e.g. latin-1) or a raw-bytes read would fail
        # this even though pure-ASCII fixtures can't discriminate (the #330
        # hash-stale divergence class).
        p = tmp_path / "non-ascii.md"
        p.write_bytes("t\xedtulo\r\nl\xednea 2\r\n".encode("utf-8"))
        assert compute_file_hash(str(p)) == (
            "61810ec213a9f72b18744b65b917a5f3ca2d3a175192cefb1a307c91a32d4a10"
        )

    def test_compute_file_hash_none_on_error(self, tmp_path):
        assert compute_file_hash(str(tmp_path / "missing.md")) is None
        # Non-UTF-8 bytes → None (never a hash).
        bad = tmp_path / "bad.md"
        bad.write_bytes(b"\xff\xfe\x00binary")
        assert compute_file_hash(str(bad)) is None
        # A directory path (IsADirectoryError) → None, never raises.
        assert compute_file_hash(str(tmp_path)) is None

    def test_compute_file_hash_single_read_semantics(self, tmp_path):
        # (Consolidated: test_crlf_immunity_via_text_mode_read pins the
        # read-then-hash single-read contract; matches_ingest_style pins the
        # hash_text composition. The index path hashes the SAME buffer it
        # parses — compute_file_hash is read + hash_text.)
        p = tmp_path / "x.md"
        p.write_text("---\ntitle: t\n---\nbody\n", encoding="utf-8")
        text = p.read_text(encoding="utf-8")
        assert compute_file_hash(str(p)) == hash_text(text)


# ── Date normalization (§4.1) ──────────────────────────────────────────────


class TestNormalizeSourceDate:
    def test_iso_date_only(self):
        assert normalize_source_date("2026-08-05") == "2026-08-05T00:00:00+00:00"

    def test_z_suffix(self):
        assert normalize_source_date("2026-08-05T10:00:00Z") == "2026-08-05T10:00:00+00:00"

    def test_naive_datetime_implies_utc(self):
        assert normalize_source_date("2026-08-05T10:00:00") == "2026-08-05T10:00:00+00:00"

    def test_non_zero_padded(self):
        assert normalize_source_date("2026-8-5") == "2026-08-05T00:00:00+00:00"

    def test_slash_separated(self):
        assert normalize_source_date("2026/08/05") == "2026-08-05T00:00:00+00:00"

    def test_offset_preserved(self):
        assert (
            normalize_source_date("2026-08-05T10:00:00-05:00")
            == "2026-08-05T10:00:00-05:00"
        )

    def test_yaml_coerced_date_object(self):
        import yaml

        fm = yaml.safe_load("date: 2026-08-05")
        assert normalize_source_date(fm["date"]) == "2026-08-05T00:00:00+00:00"

    def test_yaml_coerced_datetime_object(self):
        import yaml

        fm = yaml.safe_load("startedAt: 2026-08-05T10:00:00Z")
        assert normalize_source_date(fm["startedAt"]) == "2026-08-05T10:00:00+00:00"

    def test_format_variants_converge(self):
        # E2E-2 date-variant probe — all accepted spellings converge on ONE
        # canonical value (stable eventId tiers across runs).
        variants = ["2026-08-05", "2026-8-5", "2026/08/05"]
        canonical = normalize_source_date(variants[0])
        for v in variants[1:]:
            assert normalize_source_date(v) == canonical

    def test_garbage_returns_none(self):
        assert normalize_source_date("garbage") is None
        assert normalize_source_date("not a date") is None
        assert normalize_source_date("2026-13-99") is None

    def test_none_and_empty_return_none(self):
        assert normalize_source_date(None) is None
        assert normalize_source_date("") is None
        assert normalize_source_date("   ") is None


# ── Source url derivation (§4.1/§4.2; #909-shared contract) ────────────────


class TestDeriveSourceUrl:
    def _corpus(self, tmp_path):
        root = tmp_path / "corpus"
        root.mkdir()
        return root

    def test_basic_url(self, tmp_path):
        root = self._corpus(tmp_path)
        f = root / "notes" / "hello.md"
        f.parent.mkdir()
        f.write_text("body")
        assert derive_source_url(f, root) == "corpus://corpus/notes/hello.md"

    def test_per_segment_percent_encoding(self, tmp_path):
        root = self._corpus(tmp_path)
        f = root / "my notes" / "a#b?c&d reuni\u00f3n.md"
        f.parent.mkdir()
        f.write_text("body")
        url = derive_source_url(f, root)
        assert url == "corpus://corpus/my%20notes/a%23b%3Fc%26d%20reuni%C3%B3n.md"
        # segments joined with '/' — the separator itself is NOT encoded.
        assert "/" in url and "%2F" not in url

    def test_corpus_name_percent_encoded_same_rule(self, tmp_path):
        root = self._corpus(tmp_path)
        f = root / "x.md"
        f.write_text("body")
        url = derive_source_url(f, root, corpus_name="my docs")
        assert url == "corpus://my%20docs/x.md"

    def test_corpus_name_single_encode_pin(self, tmp_path):
        # A RAW input encoded EXACTLY ONCE — never sniffed for pre-encoding.
        root = self._corpus(tmp_path)
        f = root / "x.md"
        f.write_text("body")
        # pre-encoded name → the '%' itself is encoded (distinct merge key)
        assert derive_source_url(f, root, corpus_name="my%20docs") == (
            "corpus://my%2520docs/x.md"
        )
        # slash-containing name → '/' encoded (never forks the authority path)
        assert derive_source_url(f, root, corpus_name="a/b") == "corpus://a%2Fb/x.md"
        # space-containing name → single encode, never double
        assert derive_source_url(f, root, corpus_name="my docs") == (
            "corpus://my%20docs/x.md"
        )

    def test_corpus_name_default_is_resolved_root_basename(self, tmp_path):
        root = self._corpus(tmp_path)
        f = root / "x.md"
        f.write_text("body")
        assert derive_source_url(f, root) == "corpus://corpus/x.md"

    def test_default_name_with_space_gets_encoded_authority(self, tmp_path):
        # A root whose basename contains a space yields an ENCODED authority
        # (never a raw space in the url) via the default-name branch.
        root = tmp_path / "my corpus"
        root.mkdir()
        f = root / "x.md"
        f.write_text("body")
        assert derive_source_url(f, root) == "corpus://my%20corpus/x.md"

    def test_realpath_dedup_symlink_file(self, tmp_path):
        root = self._corpus(tmp_path)
        real = root / "real.md"
        real.write_text("body")
        alias = root / "alias.md"
        alias.symlink_to(real)
        # symlink alias and real path derive the SAME url (MERGE converges).
        assert derive_source_url(alias, root) == derive_source_url(real, root)

    def test_realpath_dedup_symlinked_dir(self, tmp_path):
        root = self._corpus(tmp_path)
        sub = root / "sub"
        sub.mkdir()
        f = sub / "x.md"
        f.write_text("body")
        link = root / "linked"
        link.symlink_to(sub, target_is_directory=True)
        assert derive_source_url(f, root) == derive_source_url(link / "x.md", root)

    def test_escape_rejection_outside_root(self, tmp_path):
        root = self._corpus(tmp_path)
        outside = tmp_path / "outside.md"
        outside.write_text("body")
        with pytest.raises(ValueError, match="escape rejected"):
            derive_source_url(outside, root)

    def test_escape_rejection_symlink_outside_root(self, tmp_path):
        # A symlink INSIDE the root whose target resolves OUTSIDE → hard error
        # (never silent follow, never an absolute url).
        root = self._corpus(tmp_path)
        outside = tmp_path / "target-outside.md"
        outside.write_text("body")
        alias = root / "leak.md"
        alias.symlink_to(outside)
        with pytest.raises(ValueError, match="escape rejected"):
            derive_source_url(alias, root)

    def test_realpath_dedup_symlinked_root(self, tmp_path):
        # The corpus ROOT itself symlinked: realpath(root) resolves both, so
        # walking through the link derives the same rel-path/url (MERGE dedup).
        real = tmp_path / "real-corpus"
        real.mkdir()
        f = real / "x.md"
        f.write_text("body")
        link = tmp_path / "corpus-link"
        link.symlink_to(real, target_is_directory=True)
        assert derive_source_url(link / "x.md", link) == derive_source_url(f, real)

    def test_encoding_round_trip_via_unquote(self, tmp_path):
        from urllib.parse import unquote

        root = self._corpus(tmp_path)
        f = root / "my notes" / "a#b.md"
        f.parent.mkdir()
        f.write_text("body")
        url = derive_source_url(f, root)
        assert unquote(url.removeprefix("corpus://")) == "corpus/my notes/a#b.md"


# ── Event / Document identity (§4.2) ───────────────────────────────────────


class TestDeriveSessionId:
    def test_sessionId_primary(self):
        assert derive_session_id({"sessionId": "abc"}, "stem") == "abc"

    def test_session_id_alternate_key(self):
        assert derive_session_id({"session_id": "xyz"}, "stem") == "xyz"

    def test_str_coerced_or_collapse(self):
        # falsy-but-coercible scalars are NOT collapsed (review round 2 P2).
        assert derive_session_id({"sessionId": 0}, "stem") == "0"
        assert derive_session_id({"sessionId": 0.0}, "stem") == "0.0"
        assert derive_session_id({"sessionId": False}, "stem") == "False"

    def test_empty_sessionId_falls_through(self):
        # Empty-string sessionId is falsy → alternate key, then file stem
        # (review round 4 P2 — mirrors ingest exactly).
        assert derive_session_id({"sessionId": ""}, "stem") == "file_stem"
        assert derive_session_id({"sessionId": "", "session_id": "b"}, "stem") == "b"
        # empty ALTERNATE key also falls through to the stem (or-collapse).
        assert derive_session_id({"session_id": ""}, "stem") == "file_stem"

    def test_missing_keys_file_fallback(self):
        assert derive_session_id({}, "my-file") == "file_my-file"

    def test_none_value_falls_through(self):
        # None is the one input where the fall-through is not a coercion
        # artifact — _coerce_str keeps None as None.
        assert derive_session_id({"sessionId": None}, "stem") == "file_stem"
        assert derive_session_id({"sessionId": None, "session_id": "b"}, "stem") == "b"


class TestSlugify:
    def test_lowercase(self):
        assert slugify("Hello World") == "hello-world"

    def test_non_alnum_to_dash_collapse_strip(self):
        assert slugify("  hello!!  world  ") == "hello-world"
        assert slugify("a--b___c") == "a-b-c"

    def test_cap_60(self):
        long = "x" * 100
        assert len(slugify(long)) == 60
        assert len(slugify(long, cap=10)) == 10
        # Truncation never leaves a trailing '-' (slice then strip).
        assert slugify("hello-world", cap=5) == "hello"
        assert slugify("a-b-c", cap=1) == "a"
        # slice that ENDS on a dash → strip removes it (the dead-weight guard).
        assert slugify("ab-cd", cap=3) == "ab"
        assert slugify("hello-world", cap=6) == "hello"
        # Non-positive/None caps disable truncation by design.
        assert slugify("x" * 100, cap=None) == "x" * 100
        assert slugify("x" * 100, cap=0) == "x" * 100
        # cap == len boundary is a no-op.
        assert slugify("hello", cap=5) == "hello"

    def test_empty(self):
        assert slugify("") == ""


class TestDeriveMeetingEventId:
    def _rel(self, root, name):
        f = root / name
        f.write_text("body")
        return f

    def test_frontmatter_date_and_title(self, tmp_path):
        root = tmp_path / "corpus"
        root.mkdir()
        f = self._rel(root, "m1.md")
        fm = {"date": "2026-08-05", "title": "Quarterly Review"}
        assert (
            derive_meeting_event_id(fm, f)
            == "meeting_2026-08-05-quarterly-review"
        )

    def test_date_variant_stability(self, tmp_path):
        # The date tier uses the NORMALIZED date — format variants converge.
        root = tmp_path / "corpus"
        root.mkdir()
        f = self._rel(root, "m1.md")
        base = derive_meeting_event_id(
            {"date": "2026-08-05", "title": "Q"}, f
        )
        for variant in ("2026-8-5", "2026/08/05", "2026-08-05T10:00:00Z"):
            assert derive_meeting_event_id(
                {"date": variant, "title": "Q"}, f
            ) == base

    def test_filename_date_tier(self, tmp_path):
        root = tmp_path / "corpus"
        root.mkdir()
        f = self._rel(root, "2026-08-05-standup.md")
        assert (
            derive_meeting_event_id({"title": "Standup"}, f)
            == "meeting_2026-08-05-standup"
        )

    def test_title_only_tier(self, tmp_path):
        root = tmp_path / "corpus"
        root.mkdir()
        f = self._rel(root, "notes.md")
        assert derive_meeting_event_id({"title": "Design Notes"}, f) == (
            "meeting_design-notes"
        )

    def test_degraded_date_falls_to_title_tier(self, tmp_path):
        # Garbage date → normalize_source_date None → the date tier degrades;
        # never a slug of the raw garbage string (E2E-2 stability class).
        root = tmp_path / "corpus"
        root.mkdir()
        f = self._rel(root, "notes.md")
        assert derive_meeting_event_id({"date": "garbage", "title": "X"}, f) == "meeting_x"

    def test_degraded_date_with_dated_filename(self, tmp_path):
        # Garbage frontmatter date + dated filename → filename-date tier wins.
        root = tmp_path / "corpus"
        root.mkdir()
        f = self._rel(root, "2026-08-05-x.md")
        assert (
            derive_meeting_event_id({"date": "garbage", "title": "X"}, f)
            == "meeting_2026-08-05-x"
        )

    def test_startedAt_fallback(self, tmp_path):
        # §4.2 date tier consumes startedAt when date is absent.
        root = tmp_path / "corpus"
        root.mkdir()
        f = self._rel(root, "m.md")
        assert (
            derive_meeting_event_id({"startedAt": "2026-08-05T10:00:00Z", "title": "Q"}, f)
            == "meeting_2026-08-05-q"
        )

    def test_date_without_title_uses_stem(self, tmp_path):
        # No title → neither date tier applies; degrade to the stem tier.
        # Empty-string title is falsy (str-coerced) — same degrade.
        root = tmp_path / "corpus"
        root.mkdir()
        f = self._rel(root, "m1.md")
        assert derive_meeting_event_id({"date": "2026-08-05"}, f) == "meeting_m1"
        assert derive_meeting_event_id({"date": "2026-08-05", "title": ""}, f) == "meeting_m1"
        assert derive_meeting_event_id({"date": "2026-08-05", "title": " "}, f) == "meeting_m1"

    def test_stem_fallback(self, tmp_path):
        root = tmp_path / "corpus"
        root.mkdir()
        f = self._rel(root, "mystery.md")
        assert derive_meeting_event_id({}, f) == "meeting_mystery"

    def test_empty_string_date_falls_through_to_startedAt(self, tmp_path):
        # Empty-string date is treated as ABSENT (consistent with _has_value
        # semantics) — the startedAt fallback applies, not a degrade.
        root = tmp_path / "corpus"
        root.mkdir()
        f = self._rel(root, "m.md")
        assert (
            derive_meeting_event_id(
                {"date": "", "startedAt": "2026-08-05T10:00:00Z", "title": "Q"}, f
            )
            == "meeting_2026-08-05-q"
        )

    def test_collision_suffix_different_source_file(self, tmp_path):
        # Behavior contract, not derivation internals: a candidate already
        # claimed by a DIFFERENT source_file gets a deterministic suffixed id
        # that differs from the base candidate.
        root = tmp_path / "corpus"
        root.mkdir()
        f = self._rel(root, "m.md")
        fm = {"date": "2026-08-05", "title": "Same Title"}
        base = "meeting_2026-08-05-same-title"
        lookup = lambda candidate: "/other/source_file.md"  # noqa: E731
        collided = derive_meeting_event_id(
            fm, f, existing_source_file_lookup=lookup, source_file="rel/m.md"
        )
        assert collided != base
        assert collided.startswith(f"{base}-")
        # deterministic + idempotent
        again = derive_meeting_event_id(
            fm, f, existing_source_file_lookup=lookup, source_file="rel/m.md"
        )
        assert collided == again
        # two DIFFERENT colliding source_files → different suffixes
        other = derive_meeting_event_id(
            fm, f, existing_source_file_lookup=lookup, source_file="rel/other.md"
        )
        assert other != collided

    def test_collision_reuse_own_event_id(self, tmp_path):
        root = tmp_path / "corpus"
        root.mkdir()
        f = self._rel(root, "m.md")
        fm = {"date": "2026-08-05", "title": "Same Title"}
        own = "rel/2026-08-05-same-title.md"
        lookup = lambda candidate: own  # noqa: E731 — the eventId is OURS
        assert (
            derive_meeting_event_id(fm, f, existing_source_file_lookup=lookup, source_file=own)
            == "meeting_2026-08-05-same-title"
        )

    def test_no_collision_when_lookup_empty(self, tmp_path):
        root = tmp_path / "corpus"
        root.mkdir()
        f = self._rel(root, "m.md")
        fm = {"date": "2026-08-05", "title": "Same Title"}
        lookup = lambda candidate: None  # noqa: E731 — eventId is free
        assert (
            derive_meeting_event_id(
                fm, f, existing_source_file_lookup=lookup, source_file="m.md"
            )
            == "meeting_2026-08-05-same-title"
        )

    def test_suffix_deterministic_and_idempotent(self, tmp_path):
        root = tmp_path / "corpus"
        root.mkdir()
        f = self._rel(root, "m.md")
        fm = {"date": "2026-08-05", "title": "Same Title"}
        lookup = lambda candidate: "/other.md"  # noqa: E731
        first = derive_meeting_event_id(
            fm, f, existing_source_file_lookup=lookup, source_file="rel/m.md"
        )
        second = derive_meeting_event_id(
            fm, f, existing_source_file_lookup=lookup, source_file="rel/m.md"
        )
        assert first == second  # deterministic, idempotent

    def test_symlink_alias_derives_same_suffix(self, tmp_path):
        # CYCLE-26 REVIEW FIX (P2): the suffix-hash input is the caller-passed
        # realpath-RELATIVIZED source_file (plan §4.2 cycle-15/16/17/18) — alias
        # and real path for ONE physical file converge on the same suffixed id;
        # omitting source_file under a collision lookup now RAISES (the
        # absolute-realpath default is retired).
        root = tmp_path / "corpus"
        root.mkdir()
        real = self._rel(root, "m.md")
        alias = root / "alias-m.md"
        alias.symlink_to(real)
        fm = {"date": "2026-08-05", "title": "Same Title"}
        lookup = lambda candidate: "/other.md"  # noqa: E731
        a = derive_meeting_event_id(
            fm, alias, existing_source_file_lookup=lookup, source_file="m.md"
        )
        b = derive_meeting_event_id(
            fm, real, existing_source_file_lookup=lookup, source_file="m.md"
        )
        assert a == b
        assert a.startswith("meeting_2026-08-05-same-title-")
        with pytest.raises(ValueError):
            derive_meeting_event_id(fm, real, existing_source_file_lookup=lookup)


class TestDeriveDocumentId:
    def test_doc_rel_path(self, tmp_path):
        root = tmp_path / "corpus"
        root.mkdir()
        f = root / "docs" / "intro.md"
        f.parent.mkdir()
        f.write_text("body")
        assert derive_document_id(f, root) == "doc_docs/intro.md"

    def test_doc_id_escape_rejection(self, tmp_path):
        root = tmp_path / "corpus"
        root.mkdir()
        outside = tmp_path / "out.md"
        outside.write_text("body")
        with pytest.raises(ValueError, match="escape rejected"):
            derive_document_id(outside, root)

    def test_doc_id_symlink_dedup(self, tmp_path):
        # Shares _resolve_rel_path with derive_source_url — symlinked subdir
        # and real path derive the same doc_ id.
        root = tmp_path / "corpus"
        root.mkdir()
        sub = root / "docs"
        sub.mkdir()
        f = sub / "a.md"
        f.write_text("body")
        link = root / "linked"
        link.symlink_to(sub, target_is_directory=True)
        assert derive_document_id(f, root) == derive_document_id(link / "a.md", root)


# ── Classification (§6.2) — precedence + mapping ───────────────────────────


class TestClassifyFile:
    def test_declared_param_overrides_all(self):
        assert classify_file({}, "x.md", declared="agentSession") == "agent_session"
        assert classify_file({}, "x.md", declared="meeting") == "meeting"
        assert classify_file({}, "x.md", declared="doc") == "doc"
        # classifier spellings accepted too
        assert classify_file({}, "x.md", declared="agent_session") == "agent_session"
        assert classify_file({}, "x.md", declared="meeting_summary") == "meeting"
        assert classify_file({}, "x.md", declared="document") == "doc"
        # rule (1) wins over frontmatter signals (rule 1 > rules 2-4)
        assert (
            classify_file({"fileType": "doc", "sessionId": "x"}, "x.md", declared="meeting")
            == "meeting"
        )
        assert classify_file({"sessionId": "x"}, "x.md", declared="doc") == "doc"

    def test_classification_precedence_under_conflict(self):
        # Deterministic precedence (§6.2) — ordering pinned under competing
        # signals: rule 2 > 3 > 4.
        assert classify_file({"fileType": "meeting", "sessionId": "abc"}, "x.md") == "meeting"
        assert (
            classify_file({"fileType": "meeting", "date": "2026-08-05", "title": "T"}, "x.md")
            == "meeting"
        )
        assert (
            classify_file({"sessionId": "x", "participants": ["a"]}, "x.md")
            == "agent_session"
        )

    def test_declared_param_unknown_raises(self):
        with pytest.raises(ValueError, match="unknown file_type"):
            classify_file({}, "x.md", declared="banana")

    def test_declared_param_empty_is_absent(self):
        # Empty/whitespace declared is treated as ABSENT (tolerance discipline
        # — an unset config default must never hard-error the index path),
        # while a genuinely unknown value still raises (test above).
        assert classify_file({"sessionId": "x"}, "x.md", declared="") == "agent_session"
        assert classify_file({"sessionId": "x"}, "x.md", declared="   ") == "agent_session"

    def test_frontmatter_fileType_declaration(self):
        assert classify_file({"fileType": "agentSession"}, "x.md") == "agent_session"
        assert classify_file({"fileType": "AgentSession"}, "x.md") == "agent_session"
        assert classify_file({"eventKind": "meeting"}, "x.md") == "meeting"

    def test_declared_param_normalization(self):
        # declared is normalized: strip + lowercase before alias mapping.
        assert classify_file({}, "x.md", declared="DOC") == "doc"
        assert classify_file({}, "x.md", declared=" AgentSession ") == "agent_session"

    def test_sessionId_present_triggers_session(self):
        assert classify_file({"sessionId": "abc"}, "x.md") == "agent_session"
        assert classify_file({"session_id": 7}, "x.md") == "agent_session"
        # falsy-but-coercible scalars are truthy-after-coercion (matches
        # derive_session_id's str-coercion).
        assert classify_file({"sessionId": 0}, "x.md") == "agent_session"

    def test_present_but_empty_inputs_do_not_trigger(self):
        # _has_value: empty containers/scalars are ABSENT — rules 2-4 do not
        # fire and classification degrades to doc (A6: index, never skip).
        assert classify_file({"participants": []}, "x.md") == "doc"
        assert classify_file({"sessionId": ""}, "x.md") == "doc"
        assert classify_file({"date": " ", "title": ""}, "x.md") == "doc"

    def test_frontmatter_fileType_miss_falls_through(self):
        # rule (2) only fires for the declared agentSession/meeting values — a
        # bogus fileType falls through to rule (3) (sessionId wins).
        assert classify_file({"fileType": "bogus", "sessionId": "x"}, "x.md") == "agent_session"

    def test_participants_triggers_meeting(self):
        assert classify_file({"participants": ["a", "b"]}, "x.md") == "meeting"
        assert classify_file({"participants": "solo"}, "x.md") == "meeting"

    def test_date_and_title_triggers_meeting(self):
        assert classify_file({"date": "2026-08-05", "title": "T"}, "x.md") == "meeting"

    def test_date_without_title_is_not_meeting(self):
        # §6.2 rule (4) requires BOTH date AND title.
        assert classify_file({"date": "2026-08-05"}, "x.md") == "doc"

    def test_default_doc(self):
        assert classify_file({}, "x.md") == "doc"
        assert classify_file({"title": "just a doc"}, "x.md") == "doc"

    def test_never_raises_on_ambiguity(self):
        # Ambiguous/empty inputs degrade to doc, never raise.
        assert classify_file({"fileType": "bogus"}, "x.md") == "doc"

    def test_mapping_table_exhaustive(self):
        # §6.2 I26 — the pinned classify→sourceKind mapping.
        assert CLASSIFIER_TO_SOURCE_KIND == {
            "agent_session": "agentSession",
            "meeting": "meeting_summary",
            "doc": "document",
        }
        for classifier, kind in CLASSIFIER_TO_SOURCE_KIND.items():
            assert source_kind_for_classifier(classifier) == kind
            assert kind in SOURCE_KIND_DEFAULTS  # every written kind registered

    def test_source_kind_for_unknown_classifier_raises(self):
        with pytest.raises(ValueError):
            source_kind_for_classifier("nonsense")


# ── S3 import-flip regression net (back-compat aliases) ────────────────────


class TestImportFlipAliases:
    def test_session_indexer_aliases_canonical_objects(self):
        from tortoise import session_indexer as si

        assert si._FM_RE is _FM_RE
        assert si.compute_file_hash is compute_file_hash
        assert si._parse_frontmatter("x") == parse_frontmatter("x")
        assert si._parse_frontmatter("---\ntitle: a\n---\nb") == {"title": "a"}

    def test_ingest_aliases_canonical_regex(self):
        from tortoise import ingest

        assert ingest._FM_RE is _FM_RE

    def test_extract_session_id_parity(self, tmp_path):
        # session_indexer.extract_session_id (file read + derive) must equal
        # derive_session_id over the parsed frontmatter (health convergence).
        from tortoise import session_indexer as si

        p = tmp_path / "s.md"
        p.write_text("---\nsessionId: abc\n---\nbody")
        assert si.extract_session_id(str(p)) == derive_session_id(
            {"sessionId": "abc"}, "s"
        )
        p2 = tmp_path / "fallback.md"
        p2.write_text("no frontmatter here")
        assert si.extract_session_id(str(p2)) == "file_fallback"

    def test_sdk_imports_file_indexer_module(self):
        # sdk.py's module-level import guarantees import-time registration
        # whenever the SDK is the entry point: the module is bound in sdk's
        # namespace (checked via identity, not registry state — the test
        # module's own imports already executed registration).
        import tortoise.sdk

        assert tortoise.sdk.file_indexer is tortoise.file_indexer


def test_hash_text_does_not_normalize_boundary():
    """P2-2 review fix: pin that hash_text hashes the buffer AS GIVEN — the
    CRLF-immunity guarantee belongs to compute_file_hash's text-mode read."""
    from tortoise.file_indexer import hash_text
    assert hash_text("a\r\nb") != hash_text("a\nb"), (
        "hash_text must NOT normalize line endings (caller precondition)"
    )
