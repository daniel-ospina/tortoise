from typing import Protocol, runtime_checkable, Any


@runtime_checkable
class MemoryScope(Protocol):
    """Storage-agnostic memory scoping interface. ARCH-002, PROV-F-009."""
    managed_by: str
    owned_by: str

    def filter(self, team_id: str, memory_types: list[str]) -> dict[str, Any]:
        """Return filtered context scoped to team and memory types.

        Backend adapters implement this: FalkorDB today, replaceable tomorrow.
        """
        ...
