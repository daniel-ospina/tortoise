"""Fixture: MUST pass redis-guard (absolute path)."""
from tortoise.projection import FalkorProjection  # noqa: I001
proj = FalkorProjection('/tmp/canonical.db')
