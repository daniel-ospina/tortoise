"""Embedded-mode boundary text, shared by every entrypoint (#942).

Zero-import leaf module: __main__, mcp_server, and selfhost all import
EMBEDDED_EVAL_BANNER from here. NOT in selfhost.py: selfhost imports
create_http_app from mcp_server at module load, so mcp_server importing
back from selfhost would be an import cycle.
"""

EMBEDDED_EVAL_BANNER = (
    "⚠️  EMBEDDED FalkorDBLite — SINGLE-WRITER, EVAL ONLY. "
    "Concurrent writers (multiple agents) LOSE DATA on this engine. "
    "Durable multi-writer: `docker compose up -d` (repo root) or point "
    "TORTOISE_DB_URI at a FalkorDB sidecar or managed Cloud. --auth tenant "
    "on embedded is "
    "single-agent eval only — NOT a supported team deployment."
)

# #2200: the install/onboard-wizard fallback gate (init / onboard), printed
# when a run lands on the canonical embedded default because TORTOISE_DB_URI
# is unset — the user never chose the eval-only engine. Self-hosted users
# must be routed to the SUPPORTED path (Docker Compose + FalkorDB sidecar),
# never silently defaulted onto embedded. The quoted sentence is pinned by
# tests/test_cli_context.py (regression) — do not drift the wording.
EMBEDDED_FALLBACK_NOTICE = (
    "⚠️  Embedded engine active — eval-only fallback; the supported path is "
    "docker compose up -d, see quickstart-selfhosted Option A. "
    "TORTOISE_DB_URI is unset, so this run defaulted to embedded "
    "FalkorDBLite (SINGLE-WRITER, eval only — concurrent writers lose "
    "data). The supported self-hosted path runs the daemon + FalkorDB "
    "sidecar via docker compose; embedded is for single-agent eval only."
)
