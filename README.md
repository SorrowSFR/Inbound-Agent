# SPXAgent Backend-Only Gemini Branch

Backend-only distribution of the SPX voice stack. This branch ships:

- a LiveKit voice agent
- a headless FastAPI backend
- appointments, call logs, transcripts, stats, and outbound dispatch
- the local KB worker for PDF and website ingestion

This branch does not ship a bundled dashboard, WhatsApp surfaces, demo links, or follow-up automation.

If you need a frontend, build it separately from the prompt in [docs/ui-agent-prompt.md](docs/ui-agent-prompt.md). That prompt is not just documentation. Paste it into a coding agent and tell the agent to create the actual frontend files, install dependencies, and make the app runnable.

## Runtime

- Conversation runtime: `gemini-3.1-flash-live-preview`
- Scripted greeting and wrap-up fallback: `gemini-3.1-flash-tts-preview`
- Google auth: AI Studio API key mode or Vertex AI mode via `GOOGLE_GENAI_USE_VERTEXAI=true`
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

Run one SQL file in Supabase SQL Editor:

1. `sql/supabase/setup.sql`

That file is safe to re-run and covers the call tables, appointment planner, voice metrics, KB tables, storage buckets, and legacy cleanup.

## API and UI handoff

- API contract: [docs/backend-contract.md](docs/backend-contract.md)
- UI generation prompt: [docs/ui-agent-prompt.md](docs/ui-agent-prompt.md)

The expected public handoff is backend plus prompt, not backend plus bundled frontend.

To build the frontend:

1. Start this backend.
2. Verify `http://127.0.0.1:8000/health` returns `ok`.
3. Verify `http://127.0.0.1:8000/api/setup/status` returns `status: "ok"`.
4. Open [docs/ui-agent-prompt.md](docs/ui-agent-prompt.md).
5. Copy the entire prompt.
6. Paste it into a coding agent.
7. Add: `Use this prompt to build the actual frontend application now. Do not just explain the instructions. Create the files, install the packages, and make it runnable.`
8. Tell it to build Vite + React + TypeScript + Tailwind CSS on port `5173`.
9. Set `VITE_API_BASE_URL=http://127.0.0.1:8000` for local development.

## Coolify

Use the repo `Dockerfile`, set the public port to `8000`, add persistent storage at `/app/data`, and use `/health` as the health check path.

Full copy-paste steps are in [docs/deployment/coolify.md](docs/deployment/coolify.md).
