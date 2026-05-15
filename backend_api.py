from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import os
import re
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, Request, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse

from backend_config import (
    apply_config_env,
    parse_int,
    read_config,
    redact_config,
    write_config,
)
from backend_events import (
    handle_appointment_cancelled,
    handle_appointment_updated,
    handle_booking_confirmed,
)
from outbound_calls import dispatch_outbound_call

load_dotenv()

logging.basicConfig(level=logging.INFO)
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

logger = logging.getLogger("backend-api")

MAX_KB_UPLOAD_BYTES = 25 * 1024 * 1024

app = FastAPI(
    title="SPXAgent Backend API",
    version="1.0.0",
    description="Headless backend API for the backend-only Gemini 3.1 Live branch.",
)


@app.exception_handler(Exception)
async def api_exception_handler(request: Request, exc: Exception):
    logger.exception(f"[API] Unhandled error on {request.method} {request.url.path}: {exc}")
    return JSONResponse({"status": "error", "message": "Internal server error."}, status_code=500)


def _load_runtime_config(phone_number: str | None = None) -> dict:
    config = read_config(phone_number)
    apply_config_env(config)
    return config


def parse_calendar_datetime(value: str) -> datetime:
    clean = value.strip()
    if clean.endswith("Z"):
        clean = clean[:-1] + "+00:00"
    dt = datetime.fromisoformat(clean)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone(timedelta(hours=5, minutes=30)))
    return dt


def appointment_error_response(message: str, status_code: int = 400) -> JSONResponse:
    return JSONResponse({"status": "error", "message": message}, status_code=status_code)


def internal_error_response(
    public_message: str = "Unable to complete the request right now.",
    *,
    status_code: int = 500,
) -> JSONResponse:
    return JSONResponse({"status": "error", "message": public_message}, status_code=status_code)


def validate_appointment_payload(data: dict, current: dict | None = None) -> None:
    from calendar_tools import validate_appointment_window

    merged = dict(current or {})
    merged.update({key: value for key, value in data.items() if value is not None})
    status = (merged.get("status") or "scheduled").strip().lower()
    start_value = merged.get("scheduled_start")
    end_value = merged.get("scheduled_end")
    if status == "scheduled" and start_value and end_value:
        validate_appointment_window(
            parse_calendar_datetime(start_value),
            parse_calendar_datetime(end_value),
        )


def _safe_number(value):
    try:
        if value in (None, ""):
            return None
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def _summarize_turn_metrics(rows: list[dict]) -> dict | None:
    if not rows:
        return None
    numeric_fields = [
        "stt_endpoint_ms",
        "kb_ms",
        "llm_first_token_ms",
        "tts_first_audio_ms",
        "tool_ms",
        "total_turn_ms",
    ]
    summary: dict[str, object] = {
        "turns": len(rows),
        "kb_used_turns": sum(1 for row in rows if row.get("kb_used")),
    }
    for field in numeric_fields:
        values = [float(row[field]) for row in rows if row.get(field) not in (None, "")]
        if values:
            summary[field] = round(sum(values) / len(values), 2)
    slowest = None
    for row in rows:
        if row.get("total_turn_ms") in (None, ""):
            continue
        if slowest is None or float(row.get("total_turn_ms") or 0) > float(slowest.get("total_turn_ms") or 0):
            slowest = row
    if slowest:
        summary["slowest_turn"] = {
            "turn_index": slowest.get("turn_index"),
            "total_turn_ms": _safe_number(slowest.get("total_turn_ms")),
            "kb_used": bool(slowest.get("kb_used")),
            "kb_skipped_reason": slowest.get("kb_skipped_reason"),
        }
    return summary


def _attach_latency_summary(db_module, row: dict) -> dict:
    enriched = dict(row)
    room_id = str(row.get("call_room_id") or "").strip()
    if not room_id:
        return enriched
    metrics = db_module.list_call_turn_metrics(call_room_id=room_id, limit=200)
    enriched["latency_summary"] = _summarize_turn_metrics(metrics)
    return enriched


def _timestamp_rank(value: str | None) -> float:
    raw = str(value or "").strip()
    if not raw:
        return 0.0
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


@app.get("/api/config")
async def api_get_config():
    return redact_config(read_config())


@app.post("/api/config")
async def api_post_config(request: Request):
    data = await request.json()
    updated = write_config(data)
    apply_config_env(updated)
    logger.info("Configuration updated via backend API.")
    return {"status": "ok", "config": redact_config(updated)}


@app.get("/api/setup/status")
async def api_setup_status():
    _load_runtime_config()
    import db

    return db.check_supabase_setup()


@app.get("/api/logs")
async def api_get_logs():
    _load_runtime_config()
    import db

    try:
        logs = db.fetch_call_logs(limit=50)
        return [_attach_latency_summary(db, row) for row in logs]
    except Exception as exc:
        logger.error(f"Error fetching logs: {exc}")
        return []


@app.get("/api/logs/{log_id}/transcript")
async def api_get_transcript(log_id: str):
    _load_runtime_config()
    import db

    row = db.get_call_log(log_id)
    if not row:
        return PlainTextResponse(content="Error: Transcript not found.", status_code=404)

    text = f"Call Log - {row.get('created_at', '')}\n"
    text += f"Phone: {row.get('phone_number', 'Unknown')}\n"
    text += f"Duration: {row.get('duration_seconds', 0)}s\n"
    text += f"Summary: {row.get('summary', '')}\n\n"
    latency_summary = _summarize_turn_metrics(
        db.list_call_turn_metrics(call_room_id=row.get("call_room_id"), limit=200)
    )
    if latency_summary:
        text += "--- LATENCY SUMMARY ---\n"
        text += f"Turns: {latency_summary.get('turns', 0)}\n"
        text += f"KB turns: {latency_summary.get('kb_used_turns', 0)}\n"
        for field, label in [
            ("kb_ms", "KB"),
            ("llm_first_token_ms", "LLM first token"),
            ("tts_first_audio_ms", "TTS first audio"),
            ("tool_ms", "Tool"),
            ("total_turn_ms", "Total turn"),
        ]:
            if latency_summary.get(field) not in (None, ""):
                text += f"{label}: {latency_summary[field]} ms avg\n"
        text += "\n"

    transcript_text = str(row.get("transcript") or "").strip()
    if not transcript_text and row.get("call_room_id"):
        transcript_rows = db.list_call_transcripts(call_room_id=row.get("call_room_id"), limit=500)
        if transcript_rows:
            transcript_text = "\n".join(
                f"[{str(item.get('role') or '').upper()}] {str(item.get('content') or '').strip()}"
                for item in transcript_rows
                if str(item.get("content") or "").strip()
            )
    text += "--- TRANSCRIPT ---\n"
    text += transcript_text or "No transcript available."
    return PlainTextResponse(
        content=text,
        media_type="text/plain",
        headers={"Content-Disposition": f"attachment; filename=transcript_{log_id}.txt"},
    )


@app.get("/api/appointments")
async def api_get_appointments(start: str | None = None, end: str | None = None):
    _load_runtime_config()
    import db

    try:
        return db.fetch_appointments(start_iso=start, end_iso=end, limit=500)
    except db.AppointmentValidationError as exc:
        logger.error(f"Appointments validation error: {exc}")
        return appointment_error_response(str(exc), status_code=500)
    except db.AppointmentError as exc:
        logger.error(f"Error fetching appointments: {exc}")
        return appointment_error_response(str(exc), status_code=500)
    except Exception as exc:
        logger.error(f"Error fetching appointments: {exc}")
        return appointment_error_response("Unable to fetch appointments right now.", status_code=500)


@app.post("/api/appointments")
async def api_create_appointment(request: Request):
    config = _load_runtime_config()
    import db

    try:
        data = await request.json()
        payload = {
            "title": data.get("title"),
            "contact_name": data.get("contact_name"),
            "contact_phone": data.get("contact_phone"),
            "scheduled_start": data.get("scheduled_start"),
            "scheduled_end": data.get("scheduled_end"),
            "timezone": data.get("timezone") or "Asia/Kolkata",
            "status": data.get("status") or "scheduled",
            "notes": data.get("notes") or "",
            "source": "backend_api",
        }
        validate_appointment_payload(payload)
        appointment = db.create_appointment(payload)
        if (appointment.get("status") or "scheduled").lower() == "scheduled":
            handle_booking_confirmed(
                appointment=appointment,
                caller_name=appointment.get("contact_name") or "",
                phone_number=appointment.get("contact_phone") or "",
                ai_summary="Appointment created via backend API.",
                config=config,
            )
        return {"status": "ok", "appointment": appointment}
    except ValueError as exc:
        return appointment_error_response(str(exc), status_code=400)
    except db.AppointmentConflictError as exc:
        return appointment_error_response(str(exc), status_code=409)
    except db.AppointmentValidationError as exc:
        return appointment_error_response(str(exc), status_code=400)
    except db.AppointmentError as exc:
        return appointment_error_response(str(exc), status_code=500)
    except Exception as exc:
        logger.error(f"Error creating appointment: {exc}")
        return appointment_error_response("Unable to create appointment right now.", status_code=500)


@app.patch("/api/appointments/{appointment_id}")
async def api_update_appointment(appointment_id: str, request: Request):
    config = _load_runtime_config()
    import db

    try:
        data = await request.json()
        current = db.get_appointment(appointment_id)
        payload = {
            "title": data.get("title"),
            "contact_name": data.get("contact_name"),
            "contact_phone": data.get("contact_phone"),
            "scheduled_start": data.get("scheduled_start"),
            "scheduled_end": data.get("scheduled_end"),
            "timezone": data.get("timezone"),
            "status": data.get("status"),
            "notes": data.get("notes"),
        }
        validate_appointment_payload(payload, current=current)
        appointment = db.update_appointment(appointment_id, payload)
        if (appointment.get("status") or "").lower() == "cancelled":
            handle_appointment_cancelled(
                appointment,
                reason="Cancelled from backend API update.",
                config=config,
            )
        else:
            handle_appointment_updated(appointment, config=config)
        return {"status": "ok", "appointment": appointment}
    except ValueError as exc:
        return appointment_error_response(str(exc), status_code=400)
    except db.AppointmentNotFoundError as exc:
        return appointment_error_response(str(exc), status_code=404)
    except db.AppointmentConflictError as exc:
        return appointment_error_response(str(exc), status_code=409)
    except db.AppointmentValidationError as exc:
        return appointment_error_response(str(exc), status_code=400)
    except db.AppointmentError as exc:
        return appointment_error_response(str(exc), status_code=500)
    except Exception as exc:
        logger.error(f"Error updating appointment: {exc}")
        return appointment_error_response("Unable to update appointment right now.", status_code=500)


@app.post("/api/appointments/{appointment_id}/cancel")
async def api_cancel_appointment(appointment_id: str, request: Request):
    config = _load_runtime_config()
    import db

    try:
        data = await request.json()
        reason = str(data.get("reason") or "").strip()
        appointment = db.cancel_appointment(appointment_id, reason=reason)
        handle_appointment_cancelled(appointment, reason=reason, config=config)
        return {"status": "ok", "appointment": appointment}
    except db.AppointmentNotFoundError as exc:
        return appointment_error_response(str(exc), status_code=404)
    except db.AppointmentValidationError as exc:
        return appointment_error_response(str(exc), status_code=400)
    except db.AppointmentError as exc:
        return appointment_error_response(str(exc), status_code=500)
    except Exception as exc:
        logger.error(f"Error cancelling appointment: {exc}")
        return appointment_error_response("Unable to cancel appointment right now.", status_code=500)


@app.get("/api/stats")
async def api_get_stats():
    _load_runtime_config()
    import db

    try:
        return db.fetch_stats()
    except Exception as exc:
        logger.error(f"Error fetching stats: {exc}")
        return {"total_calls": 0, "total_bookings": 0, "avg_duration": 0, "booking_rate": 0}


@app.get("/api/contacts")
async def api_get_contacts():
    _load_runtime_config()
    import db

    try:
        rows = db.fetch_call_logs(limit=500)
        appointments = db.fetch_appointments(limit=500)
        contacts: dict[str, dict] = {}

        for row in rows:
            phone = row.get("phone_number") or "unknown"
            item = contacts.setdefault(
                phone,
                {
                    "phone_number": phone,
                    "caller_name": row.get("caller_name") or "",
                    "total_calls": 0,
                    "last_seen": row.get("created_at"),
                    "is_booked": False,
                    "appointment_count": 0,
                },
            )
            item["total_calls"] += 1
            if not item["caller_name"] and row.get("caller_name"):
                item["caller_name"] = row["caller_name"]
            if _timestamp_rank(row.get("created_at")) > _timestamp_rank(item.get("last_seen")):
                item["last_seen"] = row.get("created_at")
            if row.get("was_booked") or "confirmed" in str(row.get("summary") or "").lower():
                item["is_booked"] = True

        for appointment in appointments:
            phone = db.normalize_phone_number(appointment.get("contact_phone") or "") or "unknown"
            item = contacts.setdefault(
                phone,
                {
                    "phone_number": phone,
                    "caller_name": appointment.get("contact_name") or "",
                    "total_calls": 0,
                    "last_seen": appointment.get("scheduled_start") or appointment.get("created_at"),
                    "is_booked": False,
                    "appointment_count": 0,
                },
            )
            item["appointment_count"] += 1
            if not item["caller_name"] and appointment.get("contact_name"):
                item["caller_name"] = appointment["contact_name"]
            if _timestamp_rank(appointment.get("scheduled_start")) > _timestamp_rank(item.get("last_seen")):
                item["last_seen"] = appointment.get("scheduled_start")
            if str(appointment.get("status") or "").lower() == "scheduled":
                item["is_booked"] = True

        return sorted(contacts.values(), key=lambda item: _timestamp_rank(item.get("last_seen")), reverse=True)
    except Exception as exc:
        logger.error(f"Error fetching contacts: {exc}")
        return []


@app.get("/api/kb/status")
async def api_kb_status():
    config = _load_runtime_config()
    import kb

    status = kb.get_status(config)
    status_code = 200 if status.get("status") in {"ok", "setup_required", "not_configured"} else 500
    return JSONResponse(status, status_code=status_code)


@app.get("/api/kb/sources")
async def api_kb_sources():
    config = _load_runtime_config()
    import kb

    try:
        return {"status": "ok", "items": kb.list_sources(limit=200, config=config)}
    except Exception as exc:
        issue = kb.kb_runtime_issue_payload(exc, config=config)
        if issue.get("status") in {"setup_required", "not_configured"}:
            return {"status": issue["status"], "items": [], "message": issue["message"]}
        logger.error(f"Failed to fetch KB sources: {exc}")
        return internal_error_response("Unable to fetch KB sources right now.")


@app.post("/api/kb/sources")
async def api_kb_create_source(request: Request):
    config = _load_runtime_config()
    import kb

    try:
        data = await request.json()
        source = kb.create_source(data, queue_sync=True, config=config)
        await asyncio.to_thread(kb.process_pending_jobs, config, limit=1)
        return {"status": "ok", "source": kb.get_source(source["id"], config=config)}
    except Exception as exc:
        issue = kb.kb_runtime_issue_payload(exc, config=config)
        if issue.get("status") in {"setup_required", "not_configured"}:
            return JSONResponse({"status": issue["status"], "message": issue["message"]}, status_code=400)
        if isinstance(exc, ValueError):
            return internal_error_response(str(exc), status_code=400)
        logger.error(f"Failed to create KB source: {exc}")
        return internal_error_response("Unable to create the KB source right now.")


@app.patch("/api/kb/sources/{source_id}")
async def api_kb_update_source(source_id: str, request: Request):
    config = _load_runtime_config()
    import kb

    try:
        data = await request.json()
        source = kb.update_source(source_id, data, config=config)
        return {"status": "ok", "source": source}
    except Exception as exc:
        issue = kb.kb_runtime_issue_payload(exc, config=config)
        if issue.get("status") in {"setup_required", "not_configured"}:
            return JSONResponse({"status": issue["status"], "message": issue["message"]}, status_code=400)
        if isinstance(exc, ValueError):
            return internal_error_response(str(exc), status_code=400)
        logger.error(f"Failed to update KB source {source_id}: {exc}")
        return internal_error_response("Unable to update the KB source right now.")


@app.delete("/api/kb/sources/{source_id}")
async def api_kb_delete_source(source_id: str):
    config = _load_runtime_config()
    import kb

    try:
        kb.delete_source(source_id, config=config)
        return {"status": "ok", "deleted": True}
    except Exception as exc:
        issue = kb.kb_runtime_issue_payload(exc, config=config)
        if issue.get("status") in {"setup_required", "not_configured"}:
            return JSONResponse({"status": issue["status"], "message": issue["message"]}, status_code=400)
        logger.error(f"Failed to delete KB source {source_id}: {exc}")
        return internal_error_response("Unable to delete the KB source right now.")


@app.post("/api/kb/sources/{source_id}/sync")
async def api_kb_sync_source(source_id: str):
    config = _load_runtime_config()
    import kb

    try:
        source = kb.get_source(source_id, config=config)
        if not source:
            return JSONResponse({"status": "error", "message": "KB source not found."}, status_code=404)
        job = kb.queue_job(
            source_id=source_id,
            source_type=source.get("source_type") or "generic",
            job_type="ingest",
            payload={},
            config=config,
        )
        await asyncio.to_thread(kb.process_pending_jobs, config, limit=3)
        return {"status": "ok", "job": job}
    except Exception as exc:
        issue = kb.kb_runtime_issue_payload(exc, config=config)
        if issue.get("status") in {"setup_required", "not_configured"}:
            return JSONResponse({"status": issue["status"], "message": issue["message"]}, status_code=400)
        logger.error(f"Failed to sync KB source {source_id}: {exc}")
        return internal_error_response("Unable to sync the KB source right now.")


@app.post("/api/kb/upload")
async def api_kb_upload(file: UploadFile = File(...)):
    config = _load_runtime_config()
    import kb

    if not file.filename:
        return JSONResponse({"status": "error", "message": "File name is required."}, status_code=400)

    try:
        content = await file.read(MAX_KB_UPLOAD_BYTES + 1)
        if len(content) > MAX_KB_UPLOAD_BYTES:
            return JSONResponse(
                {"status": "error", "message": f"File is too large. Max size is {MAX_KB_UPLOAD_BYTES // (1024 * 1024)} MB."},
                status_code=400,
            )
        safe_name = re.sub(r"[^a-zA-Z0-9._-]", "_", file.filename)
        mime_type = file.content_type or mimetypes.guess_type(file.filename)[0] or "application/octet-stream"
        stored_file = kb.save_uploaded_file(safe_name, content, mime_type=mime_type, config=config)
        source = kb.create_source(
            {
                "source_type": "pdf_upload",
                "title": safe_name,
                "source_url": stored_file["source_url"],
                "storage_bucket": stored_file["storage_bucket"],
                "storage_path": stored_file["storage_path"],
                "mime_type": mime_type,
                "metadata": stored_file["metadata"],
            },
            queue_sync=True,
            config=config,
        )
        await asyncio.to_thread(kb.process_pending_jobs, config, limit=1)
        return {"status": "ok", "source": kb.get_source(source["id"], config=config)}
    except Exception as exc:
        issue = kb.kb_runtime_issue_payload(exc, config=config)
        if issue.get("status") in {"setup_required", "not_configured"}:
            return JSONResponse({"status": issue["status"], "message": issue["message"]}, status_code=400)
        if isinstance(exc, ValueError):
            return internal_error_response(str(exc), status_code=400)
        logger.error(f"Failed to upload KB source: {exc}")
        return internal_error_response("Unable to upload the KB source right now.")


@app.get("/api/kb/jobs")
async def api_kb_jobs():
    config = _load_runtime_config()
    import kb

    try:
        return {"status": "ok", "items": kb.list_jobs(limit=200, config=config)}
    except Exception as exc:
        issue = kb.kb_runtime_issue_payload(exc, config=config)
        if issue.get("status") in {"setup_required", "not_configured"}:
            return {"status": issue["status"], "items": [], "message": issue["message"]}
        logger.error(f"Failed to fetch KB jobs: {exc}")
        return JSONResponse({"status": "error", "message": str(exc)}, status_code=500)


@app.post("/api/kb/search")
async def api_kb_search(request: Request):
    config = _load_runtime_config()
    import kb

    try:
        data = await request.json()
        query = str(data.get("query") or "").strip()
        if not query:
            return JSONResponse({"status": "error", "message": "Query is required."}, status_code=400)
        result = kb.search_hybrid(query, config=config)
        grounding = kb.build_grounding_text(query, config=config)
        return {"status": "ok", "result": result, "grounding": grounding}
    except Exception as exc:
        issue = kb.kb_runtime_issue_payload(exc, config=config)
        if issue.get("status") in {"setup_required", "not_configured"}:
            return JSONResponse({"status": issue["status"], "message": issue["message"]}, status_code=400)
        logger.error(f"KB search failed: {exc}")
        return JSONResponse({"status": "error", "message": str(exc)}, status_code=500)


@app.post("/api/call/single")
async def api_call_single(request: Request):
    data = await request.json()
    phone = str(data.get("phone") or data.get("phone_number") or "").strip()
    config = _load_runtime_config()
    try:
        result = await dispatch_outbound_call(
            phone,
            config=config,
            caller_name=str(data.get("caller_name") or "").strip(),
        )
        logger.info(f"Outbound call dispatched to {phone}: {result['dispatch_id']}")
        return result
    except Exception as exc:
        logger.error(f"Call dispatch error: {exc}")
        return {"status": "error", "message": "Unable to dispatch the outbound call right now."}


@app.post("/api/call/bulk")
async def api_call_bulk(request: Request):
    data = await request.json()
    raw_numbers = data.get("numbers") or data.get("phone_numbers") or ""
    if isinstance(raw_numbers, list):
        numbers = [str(item).strip() for item in raw_numbers if str(item).strip()]
    else:
        numbers = [item.strip() for item in str(raw_numbers).splitlines() if item.strip()]
    results = []
    config = _load_runtime_config()
    for phone in numbers:
        try:
            result = await dispatch_outbound_call(phone, config=config)
            results.append(
                {
                    "phone": phone,
                    "status": "ok",
                    "dispatch_id": result["dispatch_id"],
                    "room": result["room"],
                }
            )
            logger.info(f"Bulk outbound dispatched to {phone}: {result['dispatch_id']}")
        except Exception as exc:
            logger.error(f"Bulk call dispatch error for {phone}: {exc}")
            results.append({"phone": phone, "status": "error", "message": "Unable to dispatch this call right now."})
    return {"results": results, "total": len(results)}


@app.get("/health")
def health_check():
    return {
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "service": "spx-backend-api",
    }
