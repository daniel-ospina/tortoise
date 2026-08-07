"""Fixture: MUST be rejected by redis-guard (relative FalkorProjection)."""
from tortoise.projection import FalkorProjection
proj = FalkorProjection('tortoise.db')
