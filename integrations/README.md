# Tortoise Integrations

Ready-to-use integrations that connect popular tools to the Tortoise epistemic graph. Each integration is self-contained: setup script, configuration, bridge code, and README.

## Available Integrations

### Meetings
- **[Minutes](meetings/minutes/)** — Open-source local meeting recorder with speaker diarization. Transcribes on-device, writes structured markdown, exposes 37 MCP tools.

### CRM
- **[Twenty CRM](crm/twenty/)** — Self-hosted open-source CRM. Bridge script pushes meeting notes to contacts and meeting data to Tortoise.

## Architecture

```
Meeting (Zoom/Meet/cal.com)
    │
    ▼
Minutes ──→ ~/meetings/*.md (structured markdown)
    │
    ▼
bridge.py ──→ Twenty CRM (contact notes)
    │
    ▼
Tortoise/FalkorDB (meeting summaries, decisions, commitments)
    │
    ▼
Pi / Agents (MCP query: "what did I promise Sarah?")
```

## Adding New Integrations

Each integration lives in its own subfolder:

```
integrations/
  meetings/
    minutes/       # Pilot
    granola/       # Future
  crm/
    twenty/        # Pilot
    hubspot/       # Future
```

Follow the existing README + setup.sh + bridge.py pattern.
