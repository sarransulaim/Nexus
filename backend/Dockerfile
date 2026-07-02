# Nexus Command backend — production image.
# Build context = backend/ (Railway/Render: set root directory to "backend").
#
# Runtime env vars (set on the host platform, never baked into the image):
#   DATABASE_URL, JWT_SECRET, SETUP_SECRET, NEXUS_TOKEN_KEY,
#   CLAUDE_API_KEY, GEMINI_API_KEY, EMBED_PROVIDER=gemini,
#   ALLOWED_ORIGINS=https://<frontend-domain>, BACKEND_URL=https://<this-api-domain>,
#   GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET / GOOGLE_REDIRECT_URI,
#   SLACK_ENABLED + SLACK_* (optional), TWILIO_* (optional)
FROM python:3.12-slim

# libmagic → python-magic (real MIME detection); curl → container healthcheck
RUN apt-get update -q \
    && apt-get install -y -q --no-install-recommends libmagic1 curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS "http://localhost:${PORT:-8000}/health" || exit 1

# Migrations first (fresh DB → full schema; existing → pending only), then the
# API. ONE worker only — rate limits, OAuth flows, WS registry, and schedulers
# are in-process state (see PROJECT_MANUAL §2). Inline sh -c: no entrypoint
# script file, so Windows CRLF line endings can never break the container.
CMD ["sh", "-c", "python create_tables.py && exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
