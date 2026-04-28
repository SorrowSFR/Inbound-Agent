# Quickstart

## 1. Environment

Create `.env` from `.env.example` and fill in at least:

- `LIVEKIT_URL`
- `LIVEKIT_API_KEY`
- `LIVEKIT_API_SECRET`
- `SIP_TRUNK_ID`
- `GOOGLE_API_KEY`
- `SUPABASE_URL`
- `SUPABASE_KEY`

Optional but useful:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `SUPABASE_S3_ACCESS_KEY`
- `SUPABASE_S3_SECRET_KEY`
- `SUPABASE_S3_ENDPOINT`

## 2. Config

Seed a config file if you want explicit defaults:

```bash
cp config.example.json config.json
```

The backend-only config contract is Gemini-first and lives in `backend_config.py`.

## 3. Database

Run these SQL files in Supabase:

1. `sql/supabase/setup.sql`
2. `sql/supabase/migration_v2.sql`
3. `sql/supabase/migration_v3.sql`
4. `sql/supabase/migration_v4_voice_metrics.sql`
5. `sql/supabase/migration_v5_kb.sql`

If you are upgrading from the old WhatsApp/dashboard branch, also run:

6. `sql/supabase/migration_v6_backend_cleanup.sql`

## 4. Start

```bash
python start_stack.py
```

Or start components individually:

```bash
uvicorn backend_api:app --host 0.0.0.0 --port 8000
python agent.py start
python kb_worker.py
```

## 5. Verify

- `GET /health`
- `GET /openapi.json`
- `GET /api/config`

## 6. Build a UI separately

Use [docs/ui-agent-prompt.md](docs/ui-agent-prompt.md) with any coding agent to generate a frontend against this backend.
