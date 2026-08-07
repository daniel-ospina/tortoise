"""Fixture: MUST pass redis-guard (absolute path)."""
from tortoise.projection import FalkorProjection
proj = FalkorProjection('/tmp/canonical.db')
