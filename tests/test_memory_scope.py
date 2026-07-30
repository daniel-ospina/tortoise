"""Tests for memory_scope — Protocol definition and mock implementations."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tortoise.memory_scope import MemoryScope


class TestMemoryScopeProtocol:
    def test_protocol_is_runtime_checkable(self):
        """Verify MemoryScope can be used with isinstance checks."""
        # The @runtime_checkable decorator enables isinstance
        # We can't directly create MemoryScope, but we can verify
        # that a conforming class passes isinstance
        class MyScope:
            managed_by = "team-a"
            owned_by = "org"

            def filter(self, team_id: str, memory_types: list[str]):
                return {"team": team_id, "types": memory_types}

        scope = MyScope()
        assert isinstance(scope, MemoryScope)

    def test_non_conforming_class_fails(self):
        """Class without required attributes should fail isinstance."""
        class NotAScope:
            pass

        assert not isinstance(NotAScope(), MemoryScope)

    def test_missing_filter_method_fails(self):
        """Class with attributes but no filter method."""
        class PartialScope:
            managed_by = "team"
            owned_by = "org"

        assert not isinstance(PartialScope(), MemoryScope)

    def test_missing_managed_by_fails(self):
        """Class with filter but no managed_by."""
        class PartialScope:
            owned_by = "org"

            def filter(self, team_id, memory_types):
                return {}

        assert not isinstance(PartialScope(), MemoryScope)

    def test_concrete_implementation(self):
        """Test a full concrete implementation of MemoryScope."""

        class FalkorDBScope:
            managed_by = "epistemic-team"
            owned_by = "org-design"

            def __init__(self):
                self._calls = []

            def filter(self, team_id: str, memory_types: list[str]):
                self._calls.append((team_id, memory_types))
                return {
                    "team": team_id,
                    "types": memory_types,
                    "points": [],
                    "events": [],
                }

        scope = FalkorDBScope()
        assert isinstance(scope, MemoryScope)

        result = scope.filter("team-1", ["episodic", "epistemic"])
        assert result["team"] == "team-1"
        assert "episodic" in result["types"]
        assert len(scope._calls) == 1

    def test_protocol_structural_subtyping(self):
        """Verify it's structural, not nominal — no inheritance needed."""
        # A plain dict-like class can conform without importing MemoryScope
        class ExternalScope:
            managed_by = "external"
            owned_by = "system"

            def filter(self, team_id: str, memory_types: list[str]):
                return {"team_id": team_id, "memory_types": memory_types}

        # This works because MemoryScope is @runtime_checkable
        assert isinstance(ExternalScope(), MemoryScope)
