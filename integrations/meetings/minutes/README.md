# Minutes — Meeting Intelligence Integration for Tortoise

[Minutes](https://github.com/silverstein/minutes) is an open-source (MIT), local-first meeting recorder, transcriber, and conversation memory layer. It captures system audio + microphone on macOS, transcribes locally via whisper.cpp, diarizes speakers with pyannote-rs, and writes structured markdown to `~/meetings/`.

## Why Minutes + Tortoise

- **37 MCP tools** — agents (Pi, Claude, Cursor) can query meeting memory directly
- **Relationship graph** — tracks people, commitments, and topics across all meetings
- **Plain markdown** — `~/meetings/*.md` is grep-able, vendor-independent, survives for 10+ years
- **Local-first** — audio never leaves your machine unless you opt into cloud LLM summarization
- **Tortoise bridge** — meeting summaries, decisions, and commitments flow into the epistemic graph automatically

## Quick Start

```bash
# Install
bash setup.sh

# Record a meeting
minutes record

# Query via MCP (from any agent)
npx minutes-mcp
```

## Manual Install

```bash
# CLI
brew tap silverstein/tap && brew install minutes

# Desktop app (menu bar, call detection, UI)
brew install --cask silverstein/tap/minutes

# Download whisper model (466MB)
minutes setup --model small

# Grant permissions
# System Settings → Privacy & Security → Screen Recording → enable Minutes
# System Settings → Privacy & Security → Microphone → enable Minutes

# Verify
minutes health
```

## MCP Configuration

Add to `.mcp.json`:

```json
{
  "mcpServers": {
    "minutes": {
      "command": "npx",
      "args": ["minutes-mcp"]
    }
  }
}
```

## How Recording Works

| Platform | Detection | Trigger |
|---|---|---|
| Google Meet | "Call detected" banner | One click |
| Zoom | "Call detected" banner | One click |
| Teams/Webex | "Call detected" banner | One click |
| cal.com embedded | System audio capture | `minutes record` |
| In-person | Manual | `minutes record` |
| Any browser call | System audio capture | `minutes record` |

Minutes captures **system audio** (ScreenCaptureKit, macOS 15+) — it records whatever plays through your Mac, regardless of the app.

## Tortoise Integration

After a meeting, the bridge script (`../crm/twenty/bridge.py`) processes the markdown and creates Tortoise Points:

- Meeting summary → `tortoise_create_point(kind="event", context="meeting-intelligence")`
- Decisions → `tortoise_create_point(kind="decision")`
- Commitments → `tortoise_create_point(kind="commitment")`

All points live under context `meeting-intelligence` for unified querying.
