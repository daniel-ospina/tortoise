# Twenty CRM — Tortoise Integration

[Twenty](https://twenty.com) is an open-source (AGPLv3), self-hosted CRM with REST + GraphQL APIs. It serves as the deal/contact/company tracking layer alongside Minutes' built-in relationship graph.

## Why Twenty + Tortoise

- **Self-hosted** — Docker Compose, your data stays with you
- **REST API** — bridge script pushes meeting notes to contacts
- **MCP server** — agents can read/write CRM data natively
- **Extensible** — custom objects, TypeScript apps, webhooks

## Quick Start

```bash
# Deploy
bash setup.sh

# Verify
curl http://localhost:3001/rest/contacts -H "Authorization: Bearer $TWENTY_API_KEY"
```

## Setup

1. Copy `.env.example` to `.env` and set `TWENTY_API_KEY`
2. Run `docker compose up -d`
3. Wait for health check: `curl http://localhost:3001/api/health`
4. Create your first contact via the UI at `http://localhost:3001`

## API Key

```bash
# After deployment, get an API key from the Twenty UI:
# Settings → Developers → API Keys → Create
# Then add to your .env:
echo "TWENTY_API_KEY=your-key-here" >> .env
```

## Bridge Integration

The bridge script (`bridge.py`) connects Minutes meeting markdown → Twenty CRM:

```
~/meetings/*.md → bridge.py → Twenty REST API → Contact notes
                              → Tortoise → Meeting Points
```

See `bridge.py` for the full integration pipeline.
