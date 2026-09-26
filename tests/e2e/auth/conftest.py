"""Auth-suite conftest — serve the dashboard's BUILT upload root.

WHY
---
The #4054 BFF lives in the `tortoise-dashboard` Pages project
(`website/apps/dashboard/`). Production uploads `dist/`, which vite builds by
copying `public/` to the root — that is where the auth pages end up
(`signup.html` = `/auth`, `welcome.html`, `invite-accept.html`).
`wrangler pages dev .` instead serves the SOURCE tree, so those pages are not at
their production paths and Pages answers with the SPA shell. The suites still
"pass" against the wrong document, which is the worst outcome.

`ensure_dashboard_dist()` builds once per session (mtime-cached) so every suite
here can spawn `wrangler pages dev dist` and get production's asset layout.
Wrangler resolves `functions/` against the CWD, not the directory argument, so
the dashboard's Functions are still discovered.
"""

from __future__ import annotations

import pytest
from bff_test_helpers import ensure_dashboard_dist


@pytest.fixture(scope="session", autouse=True)
def _dashboard_dist_built() -> None:
    """Build the dashboard's `dist/` before any suite spawns `pages dev dist`."""
    ensure_dashboard_dist()
