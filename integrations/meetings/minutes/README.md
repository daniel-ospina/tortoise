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

## Data flow and disclosure

This integration captures conversations and moves personal data about the people in them.
Read this before installing it (and before enabling the two LaunchAgents below).

**What is captured.** System audio and microphone input, transcribed locally with whisper.cpp and diarized with pyannote-rs. The structured markdown — including speaker-attributed transcript text — is written to `~/meetings/`. The calendar event's attendee names and emails are stored separately in `~/.minutes/cal-trigger-state.json`.

**What triggers it.** `setup.sh` installs the recorder itself, but the automatic start is a separate step: installing `com.minutes.cal-trigger.plist` runs `cal-trigger.py`, which polls macOS Calendar every 60 s and launches `minutes record` when an upcoming event's start time is within 60 seconds (`cal-trigger.py:122-146`). There is **no per-meeting confirmation step**: the only user-facing signal is a macOS notification emitted automatically as the recording starts (`cal-trigger.py:126-133`) — it is not a prompt and the recording proceeds whether or not the operator answers. The trigger does **not** require the event to have guests — `get_upcoming_events.applescript` returns every event in the next 15 minutes, and the start condition checks start time only; it also does not check for a meeting link.

**Where the data is transmitted.** Installing `com.minutes.bridge.plist` (`RunAtLoad`) runs `watchdog.sh`, which watches `~/meetings/` (via `fswatch`, or polling every 5 s without it) and runs `bridge.py` on each new file (`watchdog.sh:3,49`). The bridge sends:

| Destination | Content | Default address |
|---|---|---|
| Self-hosted Twenty | A note on the first matched contact containing the **first 50 lines of the transcript** plus decisions and commitments (`../crm/twenty/bridge.py:431-449`); and one person/opportunity record per calendar attendee (names + emails) | `TWENTY_BASE_URL`, default `http://localhost:3001` |
| Tortoise MCP daemon | Meeting title, decisions, and commitments — **no transcript excerpt** | `TORTOISE_MCP_URL`, default `http://localhost:8000/mcp` |

Both destinations are self-hosted by default, and no hosted Premise Labs server is contacted by this pipeline. The recorded **audio** stays on the machine unless you opt into cloud summarization, but the **transcript text** is copied out of the recorder into the self-hosted Twenty instance above (on the same machine by default; point `TWENTY_BASE_URL` at a remote host and it is sent there instead).

**Counterparties are not notified.** The other people on the call are not asked to consent and are not told that the call is being recorded — not by this software, and not by anything downstream of it. Nothing in this repository adds a participant notice or a pre-recording prompt. The only party who consents by installing the LaunchAgents is the operator.

## Tortoise Integration

After a meeting, the bridge script (`../crm/twenty/bridge.py`) processes the markdown and creates Tortoise Points:

- Meeting summary → `tortoise_create_point(kind="event", context="meeting-intelligence")`
- Decisions → `tortoise_create_point(kind="decision")`
- Commitments → `tortoise_create_point(kind="commitment")`

All points live under context `meeting-intelligence` for unified querying.
