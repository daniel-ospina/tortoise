"""Battery test-support package (#2284 Task 4/10).

Home of the seed_mode contract helpers (``battery.testing.seeds``) — the
SINGLE place Tasks 4 and 10 import (never duplicated into a test file).
Importing this package must never touch the corpus/DB (helpers resolve
their inputs lazily).
"""
