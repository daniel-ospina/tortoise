#!/usr/bin/env bash
# setup.sh — Deploy Twenty CRM via Docker Compose
# Idempotent: safe to re-run
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== Twenty CRM Setup ==="

# Check Docker
if ! command -v docker &>/dev/null; then
    echo "❌ Docker not found. Install Docker Desktop: https://docker.com"
    exit 1
fi

if ! docker info &>/dev/null; then
    echo "❌ Docker daemon not running. Start Docker Desktop."
    exit 1
fi

# Detect docker compose variant
if docker compose version &>/dev/null; then
    COMPOSE_CMD="docker compose"
elif command -v docker-compose &>/dev/null; then
    COMPOSE_CMD="docker-compose"
else
    echo "❌ Neither 'docker compose' nor 'docker-compose' found."
    exit 1
fi

# Create .env if not exists
if [ ! -f .env ]; then
    echo "📝 Creating .env from .env.example..."
    cp .env.example .env

    # Generate random secrets
    PG_PASSWORD=$(openssl rand -hex 16)
    REDIS_PASSWORD=$(openssl rand -hex 16)
    APP_SECRET=$(openssl rand -hex 32)

    # Replace placeholders
    if [[ "$OSTYPE" == "darwin"* ]]; then
        sed -i '' "s/^PG_PASSWORD=$/PG_PASSWORD=$PG_PASSWORD/" .env
        sed -i '' "s/^REDIS_PASSWORD=$/REDIS_PASSWORD=$REDIS_PASSWORD/" .env
        sed -i '' "s/^APP_SECRET=$/APP_SECRET=$APP_SECRET/" .env
    else
        sed -i "s/^PG_PASSWORD=$/PG_PASSWORD=$PG_PASSWORD/" .env
        sed -i "s/^REDIS_PASSWORD=$/REDIS_PASSWORD=$REDIS_PASSWORD/" .env
        sed -i "s/^APP_SECRET=$/APP_SECRET=$APP_SECRET/" .env
    fi

    echo "✅ Generated random secrets for PG_PASSWORD, REDIS_PASSWORD, APP_SECRET"
    echo ""
    echo "⚠️  Edit .env and set TWENTY_API_KEY (get from Twenty UI after first deploy)"
fi

# Check for required env vars
if ! grep -q "^TWENTY_API_KEY=." .env 2>/dev/null; then
    echo "⚠️  TWENTY_API_KEY not set. Bridge won't work until configured."
    echo "   Get it from Twenty UI: Settings → Developers → API Keys"
fi

# Deploy
echo "🚀 Starting Twenty CRM..."
$COMPOSE_CMD up -d

# Wait for healthy
echo "⏳ Waiting for Twenty to be ready..."
for i in $(seq 1 30); do
    if curl -s -o /dev/null http://localhost:3001/api/health 2>/dev/null; then
        echo "✅ Twenty CRM is ready at http://localhost:3001"
        break
    fi
    sleep 2
done

if ! curl -s -o /dev/null http://localhost:3001/api/health 2>/dev/null; then
    echo "⚠️  Health check timed out. Check: $COMPOSE_CMD logs twenty-server"
fi

echo ""
echo "=== Setup Complete ==="
echo "Twenty CRM: http://localhost:3001 (bound to localhost only)"
echo "Get API key: Settings → Developers → API Keys"
echo "Add key to .env: TWENTY_API_KEY=your-key"
