"""Fixture: MUST be rejected by redis-guard (relative FalkorProjection)."""
from tortoise.projection import FalkorProjection  # noqa: I001
proj = FalkorProjection('tortoise.db')
