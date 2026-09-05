"""W5 memory_write_v1 write-verb unit tests (issue #2104, epic #2080, S12).

Pure-shape contract tests — no graph.  Pins:
* protocol_version REQUIRED in every envelope;
* provenance REQUIRED (three keys) — missing ⇒ PROVENANCE_MISSING block;
* status branch (ok/partial/error) + error:null on clean writes;
* canonical enumerated error codes + non-empty suggestions (S12 / §6.4);
* extra-wins merge (legacy surface keys ride the envelope — D8 additive);
* point_entry shape + honest defaults.
"""
from __future__ import annotations

from tortoise.write_verb import (
    DEDUP_NEW,
    ERROR_EP_UPDATE_FAILED,
    ERROR_PROVENANCE_MISSING,
    ERROR_SUGGESTIONS,
    MEMORY_WRITE_V1,
    STATUS_PARTIAL,
    assert_provenance,
    build_write_verb,
    error_block,
    point_entry,
    surfaced_marker,
)


class TestEnvelope:
    def test_protocol_version_required_every_envelope(self):
        verb = build_write_verb(source_session="s1", source_harness="codex",
                                ingested_at="2026-09-01T00:00:00Z")
        assert verb["protocol_version"] == MEMORY_WRITE_V1

    def test_provenance_block_present(self):
        verb = build_write_verb(source_session="s1", source_harness="codex",
                                ingested_at="2026-09-01T00:00:00Z")
        assert verb["provenance"] == {
            "source_session": "s1",
            "source_harness": "codex",
            "ingested_at": "2026-09-01T00:00:00Z",
        }

    def test_clean_status_ok_error_null(self):
        verb = build_write_verb(source_session="s1", source_harness="pi",
                                ingested_at="2026-09-01T00:00:00Z")
        assert verb["status"] == "ok"
        assert verb["error"] is None

    def test_partial_status_flows_through(self):
        verb = build_write_verb(source_session="s1", source_harness="pi",
                                ingested_at="2026-09-01T00:00:00Z",
                                status=STATUS_PARTIAL)
        assert verb["status"] == STATUS_PARTIAL

    def test_error_block_rides_clean_error(self):
        verb = build_write_verb(source_session="s1", source_harness="pi",
                                ingested_at="2026-09-01T00:00:00Z",
                                error=error_block(ERROR_PROVENANCE_MISSING))
        assert verb["error"]["code"] == ERROR_PROVENANCE_MISSING
        assert verb["error"]["suggestion"]

    def test_extra_wins_merge_for_legacy_points(self):
        """D8: a surface whose legacy response already carries a ``points``
        list keeps it as the verb's per-point array (points prefers extra)."""
        legacy_points = [{"id": "pt_1", "content": "x", "status": "live",
                          "ep_updated": False, "dedup": DEDUP_NEW}]
        verb = build_write_verb(source_session="s1", source_harness="pi",
                                ingested_at="2026-09-01T00:00:00Z",
                                extra={"session_id": "s1",
                                       "points": legacy_points,
                                       "extraction_mode": "llm:mock"})
        assert verb["points"] == legacy_points
        assert verb["session_id"] == "s1"
        assert verb["protocol_version"] == MEMORY_WRITE_V1  # not clobbered

    def test_protocol_keys_never_clobbered_by_extra(self):
        """P3-1 (review): a surface's legacy response can never corrupt the
        envelope's protocol-owned keys — even a same-named ``status`` /
        ``error`` / ``protocol_version`` in extra is ignored."""
        verb = build_write_verb(source_session="s1", source_harness="pi",
                                ingested_at="2026-09-01T00:00:00Z",
                                status=STATUS_PARTIAL,
                                error=error_block(ERROR_EP_UPDATE_FAILED),
                                extra={"status": "error",
                                       "protocol_version": "bogus-v2",
                                       "error": {"code": "X"},
                                       "session_id": "s1"})
        assert verb["status"] == STATUS_PARTIAL
        assert verb["protocol_version"] == MEMORY_WRITE_V1
        assert verb["error"]["code"] == ERROR_EP_UPDATE_FAILED


class TestProvenanceRejection:
    def test_missing_provenance_block(self):
        block = assert_provenance(None)
        assert block == error_block(ERROR_PROVENANCE_MISSING)
        assert block["suggestion"]

    def test_partial_provenance_rejected(self):
        assert assert_provenance({"source_session": "s1"}) is not None
        assert assert_provenance(
            {"source_session": "s1", "source_harness": "pi",
             "ingested_at": ""}) is not None

    def test_complete_provenance_passes(self):
        assert assert_provenance(
            {"source_session": "s1", "source_harness": "pi",
             "ingested_at": "2026-09-01T00:00:00Z"}) is None


class TestErrorCodes:
    def test_canonical_codes_have_suggestions(self):
        from tortoise.write_verb import (
            ERROR_DEDUP_CONFLICT,
            ERROR_INVALID_KIND,
            ERROR_WRITE_CONFLICT,
        )
        for code in (ERROR_PROVENANCE_MISSING, ERROR_INVALID_KIND,
                     ERROR_WRITE_CONFLICT, ERROR_DEDUP_CONFLICT,
                     ERROR_EP_UPDATE_FAILED):
            assert code in ERROR_SUGGESTIONS
            assert ERROR_SUGGESTIONS[code]

    def test_error_block_shape(self):
        block = error_block(ERROR_EP_UPDATE_FAILED)
        assert set(block) == {"code", "suggestion"}


class TestPointEntry:
    def test_honest_defaults(self):
        entry = point_entry("pt_1")
        assert entry["point_id"] == "pt_1"
        assert entry["status"] == "live"
        assert entry["ep_updated"] is False  # no EP pass has run
        assert entry["dedup"] == DEDUP_NEW

    def test_kind_optional(self):
        assert "kind" not in point_entry("pt_1")
        assert point_entry("pt_1", kind="statement")["kind"] == "statement"


class TestSurfacedMarker:
    """W5 Phase E (#2104, S11) — capture-receipt disclosure marker data
    (pure-shape tests; graph-truth integration pinned in test_capture_session.py
    test_phase_e_*)."""

    def test_minted_verified_only(self):
        pts = [{"id": "pt_a", "dedup": DEDUP_NEW, "text": "the schema needs normalization"}]
        out = surfaced_marker(pts, verified_ids={"pt_a"})
        assert out == [{"point_id": "pt_a", "label": "the schema needs normalization"}]

    def test_verified_ids_required_fail_closed_empty(self):
        # A caller that cannot (or does not) verify graph presence gets an
        # EMPTY marker — never a fabricated count.  The kwarg is required,
        # and an empty verification set is the fail-closed fallback.
        pts = [{"id": "pt_a", "dedup": DEDUP_NEW, "text": "claim"}]
        assert surfaced_marker(pts, verified_ids=set()) == []

    def test_unverified_point_never_counted(self):
        pts = [{"id": "pt_ghost", "dedup": DEDUP_NEW, "text": "never landed"},
               {"id": "pt_real", "dedup": DEDUP_NEW, "text": "landed"}]
        out = surfaced_marker(pts, verified_ids={"pt_real"})
        assert [e["point_id"] for e in out] == ["pt_real"]

    def test_folded_claims_excluded(self):
        from tortoise.write_verb import DEDUP_CONTENT_HASH_HIT, DEDUP_REPHRASE_LINKED
        pts = [
            {"id": "pt_mint", "dedup": DEDUP_NEW, "text": "minted"},
            {"id": "pt_old", "dedup": DEDUP_CONTENT_HASH_HIT, "text": "already in memory"},
            {"id": "pt_par", "dedup": DEDUP_REPHRASE_LINKED, "text": "paraphrase"},
        ]
        out = surfaced_marker(pts, verified_ids={"pt_mint", "pt_old", "pt_par"})
        assert [e["point_id"] for e in out] == ["pt_mint"], \
            "content_hash_hit/rephrase_linked folds add no item — never counted"

    def test_empty_and_content_fallback(self):
        assert surfaced_marker([], verified_ids=set()) == []
        # label falls back to the point id when the entry carries no content
        pts = [{"id": "pt_a", "dedup": DEDUP_NEW, "props": {}}]
        out = surfaced_marker(pts, verified_ids={"pt_a"})
        assert out[0]["label"] == "pt_a"
        # label is deterministic and bounded (the volunteer label grammar)
        long = {"id": "pt_b", "dedup": DEDUP_NEW,
                "text": " ".join(f"word{i}" for i in range(20))}
        out2 = surfaced_marker([long], verified_ids={"pt_b"})
        assert len(out2[0]["label"]) <= 48
