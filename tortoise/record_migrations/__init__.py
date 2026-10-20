"""Discovery marker for the record's schema, mapped from `supabase/migrations`.

This directory holds no code. `pyproject.toml` maps it to the repo's
`supabase/migrations` (`[tool.setuptools] package-dir = {"tortoise.
record_migrations" = "supabase/migrations"}`) and ships the record's SQL via
`[tool.setuptools.package-data]`, so an INSTALLED `tortoise` carries the same
files `record_projection.table_columns()` parses at a repo checkout.

WHY THE MAPPING. `record_projection` reads the schema rather than restating it
(a hand-copied column list is a second declaration, and the second one goes
stale), so the reader needs the SQL on disk. A wheel ships no `supabase/` tree,
which meant `table_columns()` raised before the first statement and EVERY armed
live write in a wheel install failed as `write_failed` — the record empty while
the static wiring gate read green (#9231 review P2).

The `__init__.py` exists ONLY so `packages.find` discovers the mapped package
(the finder cannot see a `package_dir`-mapped directory), exactly like
`tortoise/packs/__init__.py` (#1929). It is a discovery marker, not importable
content.
"""
