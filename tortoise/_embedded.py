"""Embedded-mode boundary text, shared by every entrypoint (#942).

Zero-import leaf module: __main__, mcp_server, and selfhost all import
EMBEDDED_EVAL_BANNER from here. NOT in selfhost.py: selfhost imports
create_http_app from mcp_server at module load, so mcp_server importing
back from selfhost would be an import cycle.
"""

EMBEDDED_EVAL_BANNER = (
    "⚠️  EMBEDDED FalkorDBLite — SINGLE-WRITER, EVAL ONLY. "
    "Concurrent writers (multiple agents) LOSE DATA on this engine. "
    "Durable multi-writer: `docker compose up -d` (repo root) or set "
    "TORTOISE_DB_URI (managed Cloud). --auth tenant on embedded is "
    "single-agent eval only — NOT a supported team deployment."
)
