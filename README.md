# SPXAgent Backend-Only Gemini Branch

Backend-only distribution of the SPX voice stack. This branch ships:

- a LiveKit voice agent
- a headless FastAPI backend
- appointments, call logs, transcripts, stats, and outbound dispatch
- the local KB worker and LeadRat KB sync

This branch does not ship a bundled dashboard, WhatsApp surfaces, demo links, or follow-up automation.

## Runtime

- Conversation runtime: `gemini-3.1-flash-live-preview`
- Scripted greeting and wrap-up fallback: `gemini-3.1-flash-tts-preview`
- Public HTTP entrypoint: `backend_api.py`
- Agent worker entrypoint: `agent.py`

## Local start

1. Create and activate the project virtualenv.
2. Install `requirements.txt`.
3. Copy `.env.example` to `.env` and fill in the required values.
4. Optionally seed `config.json` from `config.example.json`.
5. Start the stack:

```bash
python start_stack.py
```

Services started by this branch:

- backend API on `http://127.0.0.1:8000`
- LiveKit worker health port on `http://127.0.0.1:8081`
- KB worker in the background

## Supabase

Fresh installs should run these SQL files in order:

1. `sql/supabase/setup.sql`
2. `sql/supabase/migration_v2.sql`
3. `sql/supabase/migration_v3.sql`
4. `sql/supabase/migration_v4_voice_metrics.sql`
5. `sql/supabase/migration_v5_kb.sql`

Existing legacy deployments should also run:

6. `sql/supabase/migration_v6_backend_cleanup.sql`

## API and UI handoff

- API contract: [docs/backend-contract.md](docs/backend-contract.md)
- UI generation prompt: [docs/ui-agent-prompt.md](docs/ui-agent-prompt.md)

The expected public handoff is backend plus prompt, not backend plus bundled frontend.
