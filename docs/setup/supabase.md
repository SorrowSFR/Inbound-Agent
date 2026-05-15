# Supabase Setup

This document only sets up the database used by the backend. It does not create the frontend dashboard.

After Supabase and the backend are working, build the frontend separately with `docs/ui-agent-prompt.md`. Paste the full prompt into a coding agent and tell it to create the actual frontend files, not just explain the steps.

## Install Or Upgrade

Run one file in Supabase SQL Editor:

1. `sql/supabase/setup.sql`

The file is idempotent. You can run it on a fresh project or rerun it on an existing deployment after pulling updates.

## Tables retained in this branch

- `call_logs`
- `call_transcripts`
- `active_calls`
- `appointments`
- `call_turn_metrics`
- KB tables: `kb_sources`, `kb_documents`, `kb_chunks`, `kb_ingest_jobs`

## Tables removed by cleanup

- `demo_links`
- `message_assets`
- `automation_jobs`
- `wa_conversations`
- `wa_templates`
- `wa_messages`
- `wa_events`

## Verify

After the backend is running, open:

```text
http://127.0.0.1:8000/api/setup/status
```

Expected:

```json
{"status":"ok","...":"..."}
```
