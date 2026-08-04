"""
DEPRECATED — This file is no longer in use.

Authentication for the Tortoise Hosted API has been consolidated into
hosted_api.py's inline `get_current_team` FastAPI dependency.

The AuthMiddleware and RequestIDMiddleware defined here were never wired
into hosted_api.py and are dead code. They have been removed as part of
issue #7837.

TODO: The inline `get_current_team` dependency in hosted_api.py is
missing the `last_used_at` tracking that was present in the old
AuthMiddleware here. Add the following after team resolution in
hosted_api.py's get_current_team:

    sdk._get_proj().g.query(
        "MATCH (k:APIKey {id: $id}) SET k.last_used_at = datetime()",
        params={"id": key_id},
    )

Without this, API key last-used timestamps are not being updated.
"""
