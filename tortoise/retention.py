"""Single source of truth for the retention/deletion *restore* window (#4179).

Canonical document: ``docs/retention-and-deletion.md``.

The promise is the owner's (2026-09-18): deleting a **user account**, a
**graph**, or a **team account** is reversible for a **7-day restore window**
("simplicity and safety while also allowing users to delete their stuff").

This module is deliberately a **leaf** — no imports — so every deletion path
can derive from it without a cycle:

* ``tortoise/backup_sweep.py``  → ``_GRAPH_PURGE_GRACE_DAYS`` (derived alias)
* ``tortoise/hosted_api.py``    → ``TEAM_DELETE_GRACE_HOURS`` and
  ``USER_ACCOUNT_DELETE_GRACE_HOURS`` (the env defaults)
* ``tortoise/supabase_control.py`` → the ``soft_delete_org`` default

Three separate copies of "7" is how the windows drifted apart (team was 24h,
the user account had none). Do **not** hard-code the window anywhere else:
derive it from here, or link ``docs/retention-and-deletion.md``.

The restore window is **not** a retention window. R1 forbids retaining user
content for any length; the 7 days are an *undo* offer. The separate backup
horizon (~28 days) is the ``retention_hourly/daily/weekly`` triple in
``tortoise/backup_config.py``.
"""

from __future__ import annotations

#: The undo window, in days — the sole authority for the 7-day restore.
RESTORE_WINDOW_DAYS = 7

#: The same window in hours (the unit the team/user soft-delete paths store).
RESTORE_WINDOW_HOURS = RESTORE_WINDOW_DAYS * 24
