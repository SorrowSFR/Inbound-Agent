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
7. `sql/supabase/migration_v7_kb_demo_sources.sql`

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

## 6. Coolify

Use the repo `Dockerfile`.

Set:

- public port: `8000`
- health check path: `/health`
- persistent storage: `/app/data`

Full steps: [docs/deployment/coolify.md](docs/deployment/coolify.md)

## 7. Build a UI separately

This repo does not include a finished frontend. The frontend must be built separately.

Use [docs/ui-agent-prompt.md](docs/ui-agent-prompt.md) with a coding agent to generate the real frontend against this backend.

Do not just read the prompt. Do this:

1. Open [docs/ui-agent-prompt.md](docs/ui-agent-prompt.md).
2. Copy the whole prompt.
3. Paste it into a coding agent.
4. Add this line before the prompt:

```text
Use this prompt to build the actual frontend application now. Do not just explain the instructions. Create the files, install the packages, and make it runnable.
```

5. Tell the agent the backend is running at:

```text
http://127.0.0.1:8000
```

6. Tell the agent to create a frontend `.env` value like:

```env
VITE_API_BASE_URL=http://127.0.0.1:8000
```

7. Tell the agent to use Vite on port `5173`.

8. In the generated frontend folder, run:

```bash
npm install
npm run dev
```
