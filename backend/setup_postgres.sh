#!/bin/bash
# =============================================================================
# Nexus Core — PostgreSQL Setup Script
# Run this once from your project root: bash setup_postgres.sh
# =============================================================================

set -e  # Stop on any error

echo ""
echo "🚀 Nexus Core — PostgreSQL Migration Setup"
echo "==========================================="
echo ""

# ---------------------------------------------------------------------------
# Step 1: Install Python dependencies
# ---------------------------------------------------------------------------
echo "📦 Installing Python dependencies..."
pip install psycopg2-binary alembic python-dotenv sqlalchemy fastapi uvicorn

echo "✅ Dependencies installed."
echo ""

# ---------------------------------------------------------------------------
# Step 2: Check PostgreSQL is running
# ---------------------------------------------------------------------------
echo "🔍 Checking PostgreSQL connection..."
if ! pg_isready -q; then
  echo "❌ PostgreSQL is not running."
  echo "   Start it with: sudo service postgresql start (Linux)"
  echo "   Or open pgAdmin / Postgres.app (Mac/Windows)"
  exit 1
fi
echo "✅ PostgreSQL is running."
echo ""

# ---------------------------------------------------------------------------
# Step 3: Create the database
# ---------------------------------------------------------------------------
echo "🗄️  Creating database 'nexus_core'..."
psql -U postgres -c "CREATE DATABASE nexus_core;" 2>/dev/null && echo "✅ Database created." || echo "⚠️  Database may already exist — continuing."
echo ""

# ---------------------------------------------------------------------------
# Step 4: Create .env if it doesn't exist
# ---------------------------------------------------------------------------
if [ ! -f .env ]; then
  echo "📝 Creating .env file..."
  # Secrets are GENERATED, never templated. This file used to ship a literal
  # JWT_SECRET, so every install that didn't change it shared one signing key
  # published in the repo — anyone could mint a valid manager token for it.
  # (security.py refuses to start on that value now, but a placeholder people
  # are told to replace is a placeholder people forget to replace.)
  GEN_JWT_SECRET=$(python -c "import secrets; print(secrets.token_urlsafe(48))")
  GEN_SETUP_SECRET=$(python -c "import secrets; print(secrets.token_urlsafe(24))")
  GEN_TOKEN_KEY=$(python -c "import secrets; print(secrets.token_urlsafe(48))")
  cat > .env << EOF
# PostgreSQL connection string
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/nexus_core

# Gemini API Key
GEMINI_API_KEY=your_gemini_api_key_here

# Claude API Key (for orchestrator — Step 4 of our build)
CLAUDE_API_KEY=your_claude_api_key_here

# Signing key for auth tokens. Generated per-install — never share or commit.
JWT_SECRET=${GEN_JWT_SECRET}

# One-time secret required by /auth/setup to create the first manager.
SETUP_SECRET=${GEN_SETUP_SECRET}

# Credential-encryption key (MCP + Google tokens at rest). Kept separate from
# JWT_SECRET so rotating the signing key never bricks stored credentials.
# If you change this, run:  python -m api.token_crypto rotate
NEXUS_TOKEN_KEY=${GEN_TOKEN_KEY}
EOF
  echo "✅ .env created with freshly generated secrets — add your API keys inside it."
else
  echo "⚠️  .env already exists — skipping. Make sure DATABASE_URL is set correctly."
fi
echo ""

# ---------------------------------------------------------------------------
# Step 5: Initialize Alembic (if not already done)
# ---------------------------------------------------------------------------
if [ ! -d "alembic" ]; then
  echo "🔧 Initializing Alembic migration system..."
  alembic init alembic
  # Replace the auto-generated env.py with our custom one
  cp env.py alembic/env.py
  echo "✅ Alembic initialized."
else
  echo "⚠️  Alembic directory already exists — copying env.py..."
  cp env.py alembic/env.py
fi
echo ""

# ---------------------------------------------------------------------------
# Step 6: Generate and apply the first migration
# ---------------------------------------------------------------------------
echo "🔄 Generating initial migration from models..."
alembic revision --autogenerate -m "initial_schema"

echo ""
echo "🚀 Applying migration to PostgreSQL..."
alembic upgrade head

echo ""
echo "============================================"
echo "✅ ALL DONE — PostgreSQL is ready!"
echo ""
echo "Your database 'nexus_core' is live with:"
echo "  • employees table"
echo "  • tasks + subtasks tables"
echo "  • meetings + meeting_attendees junction table"
echo "  • peer_requests table"
echo "  • manager_profile + manager_drafts tables"
echo "  • agent_memory table (digital twin persistence)"
echo ""
echo "Next: Update your GEMINI_API_KEY in .env and boot the server:"
echo "  uvicorn main:app --reload"
echo "============================================"
echo ""