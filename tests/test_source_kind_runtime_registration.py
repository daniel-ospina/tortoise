"""#2742 — a source kind registered AFTER import must reach the Layer-1 gate.

**Class B** (the decision is stated as a testable indicator): a kind registered
via ``source_credibility.register_source_kind_default`` is live for pack
validation immediately, so the Layer-1 commit gate must see it too — with no
process restart. Each test names the exact value that makes it fail (FAIL-ON)
and proves the fixture reaches that value (REACHABLE).

The defect was an *import-time snapshot vs runtime mutation* drift:
``commit_schema.CORE_SOURCE_KINDS`` froze ``SOURCE_KIND_DEFAULTS`` at import and
``compile_vocab()`` unioned the frozen set, so a kind registered at runtime was
accepted by ``pack_registry.registered_source_types()`` (a live view) but stayed
rejected by the commit gate. The fix routes both validators through the one live
helper — these tests pin the property, not the removed constant.
"""
from __future__ import annotations

import pytest

from tortoise.commit_schema import (
    compute_client_commit_id,
    refresh_vocab,
    validate_payload_dict,
)
from tortoise.pack_registry import KNOWN_SOURCE_TYPES, registered_source_types
from tortoise.source_credibility import SOURCE_KIND_DEFAULTS, register_source_kind_default

# A kind that exists in NO list before the fixture runs — so if it is present
# afterwards, the only thing that could have put it there is the runtime call.
_RUNTIME_KIND = "runtime_registered_kind_2742"

_TELEMETRY = {
    "extractor": {"version": "value@1.0.0+abc+def", "mode": "byok"},
    "model": {"provider": "anthropic", "id": "claude-3-7", "cfg_hash": "h1"},
    "counts": {"kept": 5, "candidate": 10, "segment": 12, "window": 3,
               "empty_windows": 0},
    "keep_ratio": 0.5,
    "dedup_hits": 0,
    "frontier_calls": 1,
    "llm_cost_usd": 0.02,
    "extraction_ms": 1234,
    "retry_count": 0,
    "last_error_code": None,
    "confidence_histogram": [0, 0, 0, 0, 0, 0, 0, 1, 2, 2],
}


@pytest.fixture()
def runtime_kind():
    """Register a brand-new source kind at runtime, then unregister it.

    REACHABLE: the two asserts prove the kind is absent from BOTH the immutable
    core set and the mutable registry before ``register_source_kind_default``,
    so a later presence assertion is evidence of the runtime mutation and not
    of the fixture importing it from somewhere.
    """
    assert _RUNTIME_KIND not in KNOWN_SOURCE_TYPES
    assert _RUNTIME_KIND not in SOURCE_KIND_DEFAULTS
    register_source_kind_default(_RUNTIME_KIND, None)
    try:
        yield _RUNTIME_KIND
    finally:
        SOURCE_KIND_DEFAULTS.pop(_RUNTIME_KIND, None)
        # Drop the kind from the cached vocab too, so the mutation cannot leak
        # into a later test in the same process.
        refresh_vocab()


def _payload_with_source_kind(source_kind: str) -> dict:
    """A minimal otherwise-valid §6.1 payload emitting one source."""
    raw = {
        "schema_version": "1",
        "session_id": "s-runtime-kind-2742",
        "client_commit_id": "",
        "captured_at": "2026-08-11T10:00:00Z",
        "extractor": {"version": "value@1.0.0+abc+def", "mode": "byok",
                      "calibration_version": "v3"},
        "summary": "summary text",
        "story_arc": "arc text",
        "provenance_refs": [{"path": "session.md", "spans": ["0-10"]}],
        "sources": [{"sourceKind": source_kind, "url": "https://example.invalid/1"}],
        "entities": [{"name": "Alpha", "kind": "Project",
                      "passes_frequency_gate": True}],
        "points": [{
            "id": "pt_" + "0" * 64,
            "content": "point 0",
            "pointKind": "decision",
            "reason": "NEW",
            "confidence": 0.9,
            "c_cal": 0.8,
            "about_entities": ["Alpha"],
            "source_ref": "session.md",
            "quote": "",
            "status": "live",
        }],
        "operators": [],
        "telemetry": _TELEMETRY,
    }
    raw["client_commit_id"] = compute_client_commit_id(
        raw["session_id"], raw["points"], raw["entities"], raw["operators"],
        raw["summary"], raw["story_arc"], [], [],
    )
    return raw


class TestRuntimeSourceKindRegistration:
    def test_runtime_registered_kind_is_visible_to_the_compiled_vocab(
        self, runtime_kind,
    ):
        """FAIL-ON: unioning an import-time ``CORE_SOURCE_KINDS`` snapshot makes
        ``runtime_kind in refresh_vocab().source_kinds`` False. REACHABLE: the
        fixture asserted the kind was absent from every source list first.
        """
        assert runtime_kind in registered_source_types()
        assert runtime_kind in refresh_vocab().source_kinds

    def test_runtime_registered_kind_passes_the_layer1_gate(self, runtime_kind):
        """FAIL-ON (the reported harm, #2742): the gate 422s with
        ``sources[0].sourceKind: sourceKind '<kind>' not in the ontology §5
        source-type vocabulary`` while pack validation accepts the same kind.
        REACHABLE: ``_payload_with_source_kind`` emits a source of exactly that
        kind and the fixture made it a genuinely new one.
        """
        result, _ = validate_payload_dict(
            _payload_with_source_kind(runtime_kind), vocab=refresh_vocab(),
        )
        assert result.ok, result.errors
        assert "sources[0].sourceKind" not in result.errors

    def test_unregistered_kind_is_still_rejected(self):
        """Negative control: the gate must stay CLOSED for an unknown kind, so
        the fix cannot be "accept everything" (which would also green the test
        above). FAIL-ON: a vocab widened beyond the registry returns ok=True
        where this asserts the sourceKind error.
        """
        result, _ = validate_payload_dict(
            _payload_with_source_kind("totally_unregistered_kind_2742"),
            vocab=refresh_vocab(),
        )
        assert not result.ok
        assert "sources[0].sourceKind" in result.errors
