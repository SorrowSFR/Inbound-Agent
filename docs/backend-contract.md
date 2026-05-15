# Backend Contract

Use this file together with `/openapi.json`.

The OpenAPI document is the route-level source of truth, but several responses are intentionally loose JSON objects. This document gives frontend builders the practical shapes and behavior they need to create a real UI without reverse-engineering the backend.

This document is not the frontend prompt by itself. To build the frontend, copy the full prompt from `docs/ui-agent-prompt.md` into a coding agent and tell it to implement the actual frontend project. The frontend builder should use this contract as the API source of truth.

## Global Behavior

- No auth layer is bundled in this branch.
- The backend is intended to be consumed by a separately deployed frontend.
- Most endpoints return JSON.
- `GET /api/logs/{log_id}/transcript` returns `text/plain`, not JSON.
- Most write endpoints return a wrapper like `{ "status": "ok", ... }`.
- Most error payloads look like `{ "status": "error", "message": "..." }`.
- Setup and KB endpoints may return `status: "setup_required"` or `status: "not_configured"` when local or Supabase prerequisites are missing.

## Retained HTTP Surface

### Core

- `GET /health`
- `GET /openapi.json`
- `GET /api/config`
- `POST /api/config`
- `GET /api/setup/status`

### Calls and reporting

- `GET /api/logs`
- `GET /api/logs/{log_id}/transcript`
- `GET /api/stats`
- `GET /api/contacts`
- `POST /api/call/single`
- `POST /api/call/bulk`

### Appointments

- `GET /api/appointments`
- `POST /api/appointments`
- `PATCH /api/appointments/{appointment_id}`
- `POST /api/appointments/{appointment_id}/cancel`

### Knowledge base

- `GET /api/kb/status`
- `GET /api/kb/sources`
- `POST /api/kb/sources`
- `PATCH /api/kb/sources/{source_id}`
- `DELETE /api/kb/sources/{source_id}`
- `POST /api/kb/sources/{source_id}/sync`
- `POST /api/kb/upload`
- `GET /api/kb/jobs`
- `POST /api/kb/search`

## Removed From This Branch

- `/`
- all HTML routes
- dashboard asset routes
- demo-link routes
- WhatsApp routes
- message asset routes
- automation routes

## Core Data Shapes

### Config object

`GET /api/config` returns a flat JSON object. `POST /api/config` accepts the same shape and returns:

```json
{
  "status": "ok",
  "config": {
    "first_line": "Namaste! ...",
    "agent_instructions": "",
    "gemini_live_model": "gemini-3.1-flash-live-preview",
    "gemini_live_voice": "Puck",
    "gemini_live_temperature": 0.8,
    "gemini_live_language": "",
    "gemini_live_preflight_enabled": false,
    "gemini_live_preflight_timeout": 6.0,
    "gemini_live_connect_timeout": 20.0,
    "gemini_live_connect_retries": 2,
    "gemini_live_input_transcription_enabled": true,
    "gemini_live_output_transcription_enabled": false,
    "gemini_tts_model": "gemini-3.1-flash-tts-preview",
    "lang_preset": "multilingual",
    "max_turns": 25,
    "user_away_timeout": 15.0,
    "session_close_transcript_timeout": 2.0,
    "livekit_url": "",
    "livekit_api_key": "",
    "livekit_api_secret": "",
    "sip_trunk_id": "",
    "google_api_key": "",
    "telegram_bot_token": "",
    "telegram_chat_id": "",
    "supabase_url": "",
    "supabase_key": "",
    "kb_enabled": true,
    "kb_backend": "local_faiss",
    "kb_data_dir": "data/kb",
    "kb_top_k": 4,
    "kb_similarity_threshold": 0.18,
    "kb_context_char_budget": 2800,
    "kb_live_timeout_ms": 150,
    "kb_live_context_char_budget": 900,
    "kb_cache_ttl_seconds": 45,
    "kb_chunk_size": 400,
    "kb_chunk_overlap": 60,
    "kb_worker_poll_seconds": 20,
    "kb_embedding_provider": "local",
    "kb_embedding_model": "BAAI/bge-small-en-v1.5",
    "kb_embedding_fallback_provider": "gemini",
    "kb_paid_embedding_fallback_enabled": false,
    "kb_embedding_fallback_model": "gemini-embedding-001",
    "kb_index_kind": "flat_ip",
    "kb_rerank_enabled": false
  }
}
```

For the canonical config example, also inspect `config.example.json`.

Secret-like fields are redacted in API responses as `********` when configured. Posting a redacted or blank secret value preserves the existing stored value; send `_clear_secrets` with an array of secret field names to explicitly clear them.

Cost estimates use the Gemini 3.1 Flash Live native-audio rates published by Google: USD 0.005 per input-audio minute plus USD 0.018 per output-audio minute. The stored estimate uses call duration as a conservative blended approximation because the backend does not yet persist exact caller-vs-agent audio seconds.

### Setup status

`GET /api/setup/status` checks Supabase env and required table reachability.

```json
{
  "status": "ok",
  "message": "Supabase is configured and the required tables are reachable.",
  "missing_env": [],
  "missing_tables": [],
  "schema_file": "sql/supabase/setup.sql",
  "tables": {
    "call_logs": { "ok": true }
  }
}
```

`status` can be `ok`, `not_configured`, `setup_required`, or `error`.

### Call log rows

`GET /api/logs` returns an array. Typical fields:

```json
{
  "id": "123",
  "created_at": "2026-04-26T10:30:00+00:00",
  "phone_number": "+919999999999",
  "caller_name": "Asha",
  "duration_seconds": 184,
  "summary": "Booking Confirmed: 45",
  "transcript": "[USER] ...",
  "recording_url": "https://...",
  "sentiment": "unknown",
  "was_booked": true,
  "interrupt_count": 1,
  "estimated_cost_usd": 0.03,
  "call_date": "2026-04-26",
  "call_hour": 16,
  "call_day_of_week": "Saturday",
  "call_room_id": "call-919999999999-1234",
  "latency_summary": {
    "turns": 7,
    "kb_used_turns": 3,
    "kb_ms": 102.4,
    "llm_first_token_ms": 318.7,
    "tts_first_audio_ms": 221.1,
    "tool_ms": 88.0,
    "total_turn_ms": 901.2,
    "slowest_turn": {
      "turn_index": 4,
      "total_turn_ms": 1432.7,
      "kb_used": true,
      "kb_skipped_reason": null
    }
  }
}
```

Notes:

- `latency_summary` may be missing when no voice metrics exist.
- `recording_url` may be absent if call recording is not configured.
- `transcript` may be empty even when a downloadable transcript exists.

### Transcript response

`GET /api/logs/{log_id}/transcript` returns `text/plain`.

It includes:

- call header metadata
- optional latency summary block
- transcript body

Frontend implication:

- fetch as text, not JSON
- support preview, copy, and download

### Stats

`GET /api/stats` returns:

```json
{
  "total_calls": 42,
  "total_bookings": 9,
  "avg_duration": 173,
  "booking_rate": 21
}
```

### Contacts

`GET /api/contacts` returns an array like:

```json
{
  "phone_number": "+919999999999",
  "caller_name": "Asha",
  "total_calls": 3,
  "last_seen": "2026-04-26T10:30:00+00:00",
  "is_booked": true,
  "appointment_count": 1
}
```

## Appointment Contract

### Appointment row

`GET /api/appointments` returns an array of objects like:

```json
{
  "id": "45",
  "created_at": "2026-04-26T10:30:00+00:00",
  "updated_at": "2026-04-26T10:35:00+00:00",
  "title": "Appointment",
  "contact_name": "Asha",
  "contact_phone": "+919999999999",
  "scheduled_start": "2026-04-27T11:00:00+05:30",
  "scheduled_end": "2026-04-27T11:30:00+05:30",
  "timezone": "Asia/Kolkata",
  "status": "scheduled",
  "notes": "Customer wants a morning slot.",
  "source": "voice_agent"
}
```

Status values in this branch:

- `scheduled`
- `cancelled`
- `completed`

### Create appointment

`POST /api/appointments` expects:

```json
{
  "title": "Appointment",
  "contact_name": "Asha",
  "contact_phone": "+919999999999",
  "scheduled_start": "2026-04-27T11:00:00+05:30",
  "scheduled_end": "2026-04-27T11:30:00+05:30",
  "timezone": "Asia/Kolkata",
  "status": "scheduled",
  "notes": "Customer wants a morning slot."
}
```

Returns:

```json
{
  "status": "ok",
  "appointment": { "...appointment row..." }
}
```

### Update appointment

`PATCH /api/appointments/{appointment_id}` accepts partial fields and returns:

```json
{
  "status": "ok",
  "appointment": { "...updated appointment row..." }
}
```

### Cancel appointment

`POST /api/appointments/{appointment_id}/cancel` expects:

```json
{
  "reason": "Customer rescheduled."
}
```

Returns:

```json
{
  "status": "ok",
  "appointment": { "...cancelled appointment row..." }
}
```

Common appointment errors:

- `400` validation error
- `404` not found
- `409` overlap conflict

## Knowledge Base Contract

### KB status

`GET /api/kb/status` returns a status object like:

```json
{
  "status": "ok",
  "kb_enabled": true,
  "backend": "local_faiss",
  "runtime": "Local FAISS + SQLite",
  "embedding_provider": "local",
  "embedding_model": "BAAI/bge-small-en-v1.5",
  "index_kind": "flat_ip",
  "data_dir": "data/kb",
  "index_status": {
    "vector_count": 18,
    "rebuilt_at": "2026-04-26T10:00:00+00:00"
  },
  "vector_count": 18,
  "last_rebuild_at": "2026-04-26T10:00:00+00:00",
  "counts": {
    "sources": 4,
    "jobs": 12,
    "chunks": 18
  }
}
```

### KB source rows

`GET /api/kb/sources` returns:

```json
{
  "status": "ok",
  "items": [
    {
      "id": 1,
      "created_at": "2026-04-26T09:00:00+00:00",
      "updated_at": "2026-04-26T09:05:00+00:00",
      "source_type": "web_url",
      "title": "Company FAQ",
      "source_url": "https://example.com/faq",
      "raw_text": null,
      "storage_bucket": null,
      "storage_path": null,
      "mime_type": null,
      "checksum": "sha256...",
      "status": "ready",
      "enabled": true,
      "sync_error": "",
      "last_synced_at": "2026-04-26T09:05:00+00:00",
      "metadata": {}
    }
  ]
}
```

Supported source types used by the backend:

- `web_url`
- `pdf_upload`

`web_url` can point at either a normal public page or a public sitemap URL. When a sitemap is provided, the backend crawls the listed same-site pages and indexes each extracted page as part of the source.

### Create KB source

`POST /api/kb/sources` accepts payloads such as:

```json
{
  "source_type": "web_url",
  "title": "Company FAQ",
  "source_url": "https://example.com/faq",
  "enabled": true,
  "metadata": {}
}
```

or:

```json
{
  "source_type": "web_url",
  "title": "Company Sitemap",
  "source_url": "https://example.com/sitemap.xml",
  "enabled": true,
  "metadata": {}
}
```

Returns:

```json
{
  "status": "ok",
  "source": { "...source row..." }
}
```

### Update KB source

`PATCH /api/kb/sources/{source_id}` accepts partial fields such as:

- `title`
- `source_url`
- `storage_bucket`
- `storage_path`
- `mime_type`
- `status`
- `last_synced_at`
- `enabled`
- `metadata`

Returns:

```json
{
  "status": "ok",
  "source": { "...updated source row..." }
}
```

### Delete KB source

`DELETE /api/kb/sources/{source_id}` returns:

```json
{
  "status": "ok",
  "deleted": true
}
```

### Upload file

`POST /api/kb/upload` is multipart form-data with one file field named `file`.

Returns:

```json
{
  "status": "ok",
  "source": { "...created pdf_upload source row..." }
}
```

### Sync source

`POST /api/kb/sources/{source_id}/sync` returns:

```json
{
  "status": "ok",
  "job": { "...kb job row..." }
}
```

### KB jobs

`GET /api/kb/jobs` returns:

```json
{
  "status": "ok",
  "items": [
    {
      "id": 21,
      "created_at": "2026-04-26T09:06:00+00:00",
      "updated_at": "2026-04-26T09:06:10+00:00",
      "source_id": 1,
      "source_type": "web_url",
      "job_type": "ingest",
      "status": "completed",
      "payload": {},
      "last_result": {}
    }
  ]
}
```

### KB search

`POST /api/kb/search` expects:

```json
{
  "query": "What is the booking policy?"
}
```

Returns:

```json
{
  "status": "ok",
  "result": {
    "query": "What is the booking policy?",
    "chunk_hits": [
      {
        "score": 2.71,
        "title": "Company FAQ",
        "content": "Full chunk text...",
        "preview": "Short preview...",
        "source_type": "web_url",
        "source_url": "https://example.com/faq"
      }
    ]
  },
  "grounding": {
    "query": "What is the booking policy?",
    "chunk_hits": [],
    "grounding_text": "Knowledge base grounding rules: ..."
  }
}
```

## Outbound Calling Contract

### Single call

`POST /api/call/single` accepts:

```json
{
  "phone": "+919999999999",
  "caller_name": "Asha"
}
```

Returns on success:

```json
{
  "status": "ok",
  "dispatch_id": "dispatch-id",
  "room": "call-919999999999-1234",
  "phone": "+919999999999",
  "sip_trunk_id": "ST_xxxxxxxxx"
}
```

Returns on failure:

```json
{
  "status": "error",
  "message": "Unable to dispatch the outbound call right now."
}
```

### Bulk call

`POST /api/call/bulk` accepts either:

```json
{
  "numbers": ["+919999999999", "+918888888888"]
}
```

or:

```json
{
  "phone_numbers": "+919999999999\n+918888888888"
}
```

Returns:

```json
{
  "results": [
    {
      "phone": "+919999999999",
      "status": "ok",
      "dispatch_id": "dispatch-1",
      "room": "call-919999999999-1234"
    },
    {
      "phone": "+918888888888",
      "status": "error",
      "message": "Unable to dispatch this call right now."
    }
  ],
  "total": 2
}
```

## Frontend Notes

- Build transcript handling around plain-text fetches.
- Build config forms from the flat config contract, not nested objects.
- Handle optional or partially configured integrations without breaking the UI.
- The backend already owns all booking side effects, KB ingestion logic, Gemini runtime behavior, and outbound dispatch behavior.
