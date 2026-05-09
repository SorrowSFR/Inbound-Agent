from __future__ import annotations

import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from supabase import Client, create_client

logger = logging.getLogger("db")

_SUPABASE_CLIENT: Client | None = None
_SUPABASE_CLIENT_KEY: tuple[str, str] | None = None

_ANALYTICS_COLUMNS = {
    "sentiment",
    "was_booked",
    "interrupt_count",
    "estimated_cost_usd",
    "call_date",
    "call_hour",
    "call_day_of_week",
}
_APPOINTMENT_COLUMNS = (
    "id, created_at, updated_at, title, contact_name, contact_phone, "
    "scheduled_start, scheduled_end, timezone, status, notes, source"
)
_APPOINTMENT_STATUSES = {"scheduled", "cancelled", "completed"}
_APPOINTMENT_SOURCES = {"voice_agent", "manual_ui"}
_IST = timezone(timedelta(hours=5, minutes=30))

_MAX_RETRIES = 3
_RETRY_DELAYS = [1.0, 2.0, 4.0]
_REQUIRED_SUPABASE_TABLES = (
    "call_logs",
    "call_transcripts",
    "active_calls",
    "appointments",
    "call_turn_metrics",
    "kb_sources",
    "kb_documents",
    "kb_chunks",
    "kb_ingest_jobs",
)


class AppointmentError(Exception):
    """Base error for appointments data operations."""


class AppointmentConflictError(AppointmentError):
    """Raised when an appointment overlaps an active appointment."""


class AppointmentNotFoundError(AppointmentError):
    """Raised when an appointment row does not exist."""


class AppointmentValidationError(AppointmentError):
    """Raised when appointment input is invalid."""


def _is_retryable(err_str: str) -> bool:
    transient = ("525", "ssl", "timeout", "connection", "network", "502", "503", "504")
    el = err_str.lower()
    return any(item in el for item in transient)


def _is_schema_error(err_str: str) -> bool:
    return "PGRST204" in err_str or "schema cache" in err_str.lower()


def _extract_missing_column(err_str: str) -> str | None:
    match = re.search(r"Could not find the '([^']+)' column", err_str, re.IGNORECASE)
    return match.group(1) if match else None


def _missing_appointments_table_message() -> str:
    return "Appointments table is missing. Run sql/supabase/setup.sql in Supabase."


def _parse_iso_datetime(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        clean = value.strip()
        if not clean:
            raise AppointmentValidationError("Appointment datetime is required.")
        if clean.endswith("Z"):
            clean = clean[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(clean)
        except ValueError as exc:
            raise AppointmentValidationError(f"Invalid appointment datetime: {value}") from exc
    else:
        raise AppointmentValidationError("Appointment datetime must be a string or datetime.")

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_IST)
    return dt


def _normalize_appointment_error(exc: Exception) -> AppointmentError:
    err = str(exc)
    if _is_schema_error(err):
        return AppointmentValidationError(_missing_appointments_table_message())
    if "appointments_no_overlap" in err or "23P01" in err or "overlap" in err.lower():
        return AppointmentConflictError("That time overlaps an existing scheduled appointment.")
    if "appointments_valid_status" in err:
        return AppointmentValidationError("Appointment status is invalid.")
    if "appointments_valid_source" in err:
        return AppointmentValidationError("Appointment source is invalid.")
    if "appointments_valid_window" in err or "scheduled_end" in err:
        return AppointmentValidationError("Appointment end time must be after start time.")
    return AppointmentError(err)


def _normalize_appointment_payload(
    payload: dict[str, Any],
    *,
    current: dict[str, Any] | None = None,
) -> dict[str, Any]:
    title = (payload.get("title") or "").strip() or (current or {}).get("title") or "Appointment"
    contact_name = (payload.get("contact_name") or (current or {}).get("contact_name") or "").strip()
    contact_phone = (payload.get("contact_phone") or (current or {}).get("contact_phone") or "").strip()
    notes = payload.get("notes")
    if notes is None:
        notes = (current or {}).get("notes") or ""
    notes = str(notes).strip()
    timezone_name = (
        (payload.get("timezone") or "").strip()
        or (current or {}).get("timezone")
        or "Asia/Kolkata"
    )
    status = (payload.get("status") or (current or {}).get("status") or "scheduled").strip().lower()
    source = (payload.get("source") or (current or {}).get("source") or "manual_ui").strip().lower()

    if status not in _APPOINTMENT_STATUSES:
        raise AppointmentValidationError(f"Unsupported appointment status: {status}")
    if source not in _APPOINTMENT_SOURCES:
        raise AppointmentValidationError(f"Unsupported appointment source: {source}")

    start_value = payload.get("scheduled_start", (current or {}).get("scheduled_start"))
    if not start_value:
        raise AppointmentValidationError("scheduled_start is required.")
    start_dt = _parse_iso_datetime(start_value)

    end_value = payload.get("scheduled_end")
    if end_value:
        end_dt = _parse_iso_datetime(end_value)
    elif current and "scheduled_start" in payload and "scheduled_end" not in payload:
        current_start = _parse_iso_datetime(current["scheduled_start"])
        current_end = _parse_iso_datetime(current["scheduled_end"])
        end_dt = start_dt + (current_end - current_start)
    else:
        fallback_end = (current or {}).get("scheduled_end")
        end_dt = _parse_iso_datetime(fallback_end) if fallback_end else start_dt + timedelta(minutes=30)

    if end_dt <= start_dt:
        raise AppointmentValidationError("scheduled_end must be after scheduled_start.")

    return {
        "title": title,
        "contact_name": contact_name,
        "contact_phone": contact_phone,
        "scheduled_start": start_dt.isoformat(),
        "scheduled_end": end_dt.isoformat(),
        "timezone": timezone_name,
        "status": status,
        "notes": notes,
        "source": source,
    }


def get_supabase() -> Client | None:
    url = str(os.environ.get("SUPABASE_URL", "") or "").strip()
    key = str(os.environ.get("SUPABASE_KEY", "") or "").strip()
    if not url or not key:
        return None

    global _SUPABASE_CLIENT, _SUPABASE_CLIENT_KEY
    client_key = (url, key)
    if _SUPABASE_CLIENT is not None and _SUPABASE_CLIENT_KEY == client_key:
        return _SUPABASE_CLIENT
    try:
        _SUPABASE_CLIENT = create_client(url, key)
        _SUPABASE_CLIENT_KEY = client_key
        return _SUPABASE_CLIENT
    except Exception as exc:
        logger.error(f"Failed to init Supabase client: {exc}")
        return None


def check_supabase_setup() -> dict[str, Any]:
    url = str(os.environ.get("SUPABASE_URL", "") or "").strip()
    key = str(os.environ.get("SUPABASE_KEY", "") or "").strip()
    missing_env = [name for name, value in (("SUPABASE_URL", url), ("SUPABASE_KEY", key)) if not value]
    if missing_env:
        return {
            "status": "not_configured",
            "message": "Set SUPABASE_URL and SUPABASE_KEY, then run sql/supabase/setup.sql once.",
            "missing_env": missing_env,
            "missing_tables": [],
            "schema_file": "sql/supabase/setup.sql",
            "tables": {},
        }

    supabase = get_supabase()
    if not supabase:
        return {
            "status": "error",
            "message": "Supabase client could not be initialized. Check the URL and key.",
            "missing_env": [],
            "missing_tables": [],
            "schema_file": "sql/supabase/setup.sql",
            "tables": {},
        }

    tables: dict[str, dict[str, Any]] = {}
    for table_name in _REQUIRED_SUPABASE_TABLES:
        try:
            supabase.table(table_name).select("*").limit(1).execute()
            tables[table_name] = {"ok": True}
        except Exception as exc:
            tables[table_name] = {"ok": False, "message": str(exc)}

    missing_tables = [name for name, result in tables.items() if not result.get("ok")]
    if missing_tables:
        return {
            "status": "setup_required",
            "message": "Run sql/supabase/setup.sql in the Supabase SQL Editor.",
            "missing_env": [],
            "missing_tables": missing_tables,
            "schema_file": "sql/supabase/setup.sql",
            "tables": tables,
        }

    return {
        "status": "ok",
        "message": "Supabase is configured and the required tables are reachable.",
        "missing_env": [],
        "missing_tables": [],
        "schema_file": "sql/supabase/setup.sql",
        "tables": tables,
    }


def normalize_phone_number(phone_number: str | None) -> str:
    raw = str(phone_number or "").strip()
    if raw.startswith("whatsapp:"):
        raw = raw.split(":", 1)[1].strip()
    if not raw:
        return ""
    if raw.startswith("+"):
        return raw
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return ""
    if len(digits) == 12 and digits.startswith("91") and digits[2] in "6789":
        return f"+{digits}"
    if len(digits) == 10 and digits[0] in "6789":
        return f"+91{digits}"
    return f"+{digits}"


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def save_call_log(
    phone: str,
    duration: int,
    transcript: str,
    summary: str = "",
    recording_url: str = "",
    caller_name: str = "",
    sentiment: str = "unknown",
    estimated_cost_usd: float | None = None,
    call_date: str | None = None,
    call_hour: int | None = None,
    call_day_of_week: str | None = None,
    was_booked: bool = False,
    interrupt_count: int = 0,
    call_room_id: str = "",
) -> dict:
    supabase = get_supabase()
    if not supabase:
        logger.warning("Supabase not configured; call log was not saved -> %s %ss", phone, duration)
        return {"success": False, "message": "Supabase not configured"}

    full_data: dict[str, Any] = {
        "phone_number": phone,
        "duration_seconds": duration,
        "transcript": transcript,
        "summary": summary,
        "sentiment": sentiment,
        "was_booked": was_booked,
        "interrupt_count": interrupt_count,
    }
    if recording_url:
        full_data["recording_url"] = recording_url
    if caller_name:
        full_data["caller_name"] = caller_name
    if estimated_cost_usd is not None:
        full_data["estimated_cost_usd"] = estimated_cost_usd
    if call_date:
        full_data["call_date"] = call_date
    if call_hour is not None:
        full_data["call_hour"] = call_hour
    if call_day_of_week:
        full_data["call_day_of_week"] = call_day_of_week
    if call_room_id:
        full_data["call_room_id"] = call_room_id

    base_data: dict[str, Any] = {
        key: value for key, value in full_data.items() if key not in _ANALYTICS_COLUMNS
    }

    def _try_insert(data: dict[str, Any], label: str) -> dict:
        payload = dict(data)
        transient_attempt = 0
        stripped_columns: list[str] = []

        while payload:
            try:
                res = supabase.table("call_logs").insert(payload).execute()
                return {
                    "success": True,
                    "data": res.data,
                    "dropped_columns": stripped_columns,
                }
            except Exception as exc:
                err = str(exc)
                if _is_schema_error(err):
                    missing_col = _extract_missing_column(err)
                    if missing_col and missing_col in payload:
                        payload.pop(missing_col, None)
                        stripped_columns.append(missing_col)
                        continue
                    logger.error(f"Failed to save call log ({label}) due to schema mismatch: {exc}")
                    return {"success": False, "message": err, "dropped_columns": stripped_columns}
                if _is_retryable(err) and transient_attempt < _MAX_RETRIES:
                    delay = _RETRY_DELAYS[min(transient_attempt, len(_RETRY_DELAYS) - 1)]
                    transient_attempt += 1
                    time.sleep(delay)
                    continue
                logger.error(f"Failed to save call log ({label}): {exc}")
                return {"success": False, "message": err, "dropped_columns": stripped_columns}

        return {"success": False, "message": "No compatible columns left to insert"}

    result = _try_insert(full_data, "full")
    if result.get("success"):
        return result
    if _is_schema_error(str(result.get("message", ""))):
        return _try_insert(base_data, "base-fallback")
    return result


def fetch_call_logs(
    limit: int = 50,
    *,
    phone_number: str | None = None,
    call_room_id: str | None = None,
) -> list[dict[str, Any]]:
    supabase = get_supabase()
    if not supabase:
        return []
    for attempt in range(_MAX_RETRIES):
        try:
            query = supabase.table("call_logs").select("*").order("created_at", desc=True)
            if phone_number:
                query = query.eq("phone_number", normalize_phone_number(phone_number))
            if call_room_id:
                query = query.eq("call_room_id", str(call_room_id))
            if limit:
                query = query.limit(limit)
            res = query.execute()
            return res.data or []
        except Exception as exc:
            if _is_retryable(str(exc)) and attempt < _MAX_RETRIES - 1:
                time.sleep(_RETRY_DELAYS[attempt])
                continue
            logger.error(f"Failed to fetch call logs: {exc}")
            return []
    return []


def get_call_log(log_id: str | int) -> dict[str, Any] | None:
    supabase = get_supabase()
    if not supabase:
        return None
    try:
        res = supabase.table("call_logs").select("*").eq("id", str(log_id)).single().execute()
        return res.data or None
    except Exception as exc:
        logger.error(f"Failed to fetch call log {log_id}: {exc}")
        return None


def save_call_transcript(call_room_id: str, phone: str, role: str, content: str) -> dict[str, Any] | None:
    supabase = get_supabase()
    if not supabase:
        return None
    row = {
        "call_room_id": str(call_room_id or "").strip(),
        "phone": normalize_phone_number(phone) or None,
        "role": str(role or "").strip().lower(),
        "content": str(content or "").strip(),
    }
    if row["role"] not in {"user", "assistant"} or not row["content"] or not row["call_room_id"]:
        return None
    try:
        res = supabase.table("call_transcripts").insert(row).execute()
        rows = res.data or []
        return rows[0] if rows else row
    except Exception as exc:
        if _is_schema_error(str(exc)):
            return None
        logger.debug(f"Failed to save call transcript: {exc}")
        return None


def list_call_transcripts(
    *,
    call_room_id: str | None = None,
    phone: str | None = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    supabase = get_supabase()
    if not supabase:
        return []
    try:
        query = supabase.table("call_transcripts").select("*").order("created_at")
        if call_room_id:
            query = query.eq("call_room_id", str(call_room_id))
        if phone:
            query = query.eq("phone", normalize_phone_number(phone))
        if limit:
            query = query.limit(limit)
        res = query.execute()
        return res.data or []
    except Exception as exc:
        if _is_schema_error(str(exc)):
            return []
        logger.debug(f"Failed to fetch call transcripts: {exc}")
        return []


def upsert_active_call(room_id: str, phone: str, caller_name: str, status: str) -> dict[str, Any] | None:
    supabase = get_supabase()
    if not supabase:
        return None
    payload = {
        "room_id": str(room_id or "").strip(),
        "phone": normalize_phone_number(phone) or None,
        "caller_name": str(caller_name or "").strip() or None,
        "status": str(status or "").strip() or "active",
        "last_updated": _utcnow_iso(),
    }
    if not payload["room_id"]:
        return None
    try:
        res = supabase.table("active_calls").upsert(payload).execute()
        rows = res.data or []
        return rows[0] if rows else payload
    except Exception as exc:
        if _is_schema_error(str(exc)):
            return None
        logger.debug(f"Failed to upsert active call: {exc}")
        return None


def fetch_appointments(
    *,
    start_iso: str | None = None,
    end_iso: str | None = None,
    statuses: list[str] | None = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    supabase = get_supabase()
    if not supabase:
        return []
    try:
        query = supabase.table("appointments").select(_APPOINTMENT_COLUMNS).order("scheduled_start")
        if start_iso:
            query = query.gt("scheduled_end", start_iso)
        if end_iso:
            query = query.lt("scheduled_start", end_iso)
        if statuses:
            cleaned = [status.strip().lower() for status in statuses if status.strip()]
            if cleaned:
                if len(cleaned) == 1:
                    query = query.eq("status", cleaned[0])
                else:
                    query = query.in_("status", cleaned)
        if limit:
            query = query.limit(limit)
        res = query.execute()
        return res.data or []
    except Exception as exc:
        normalized = _normalize_appointment_error(exc)
        logger.error(f"Failed to fetch appointments: {normalized}")
        raise normalized


def get_appointment(appointment_id: str | int) -> dict[str, Any]:
    supabase = get_supabase()
    if not supabase:
        raise AppointmentValidationError("Supabase not configured.")
    try:
        res = (
            supabase.table("appointments")
            .select(_APPOINTMENT_COLUMNS)
            .eq("id", str(appointment_id))
            .single()
            .execute()
        )
        if not res.data:
            raise AppointmentNotFoundError(f"Appointment {appointment_id} not found.")
        return res.data
    except AppointmentNotFoundError:
        raise
    except Exception as exc:
        normalized = _normalize_appointment_error(exc)
        logger.error(f"Failed to fetch appointment {appointment_id}: {normalized}")
        raise normalized


def create_appointment(payload: dict[str, Any]) -> dict[str, Any]:
    supabase = get_supabase()
    if not supabase:
        raise AppointmentValidationError("Supabase not configured.")
    data = _normalize_appointment_payload(payload)
    try:
        res = supabase.table("appointments").insert(data).execute()
        if not res.data:
            raise AppointmentError("Appointment insert returned no data.")
        return res.data[0]
    except Exception as exc:
        normalized = _normalize_appointment_error(exc)
        logger.error(f"Failed to create appointment: {normalized}")
        raise normalized


def update_appointment(appointment_id: str | int, payload: dict[str, Any]) -> dict[str, Any]:
    supabase = get_supabase()
    if not supabase:
        raise AppointmentValidationError("Supabase not configured.")
    current = get_appointment(appointment_id)
    data = _normalize_appointment_payload(payload, current=current)
    try:
        res = (
            supabase.table("appointments")
            .update(data)
            .eq("id", str(appointment_id))
            .execute()
        )
        if not res.data:
            raise AppointmentNotFoundError(f"Appointment {appointment_id} not found.")
        return res.data[0]
    except AppointmentNotFoundError:
        raise
    except Exception as exc:
        normalized = _normalize_appointment_error(exc)
        logger.error(f"Failed to update appointment {appointment_id}: {normalized}")
        raise normalized


def cancel_appointment(appointment_id: str | int, reason: str = "") -> dict[str, Any]:
    current = get_appointment(appointment_id)
    notes = (current.get("notes") or "").strip()
    reason = reason.strip()
    if reason:
        notes = f"{notes}\n\nCancellation reason: {reason}".strip()
    return update_appointment(appointment_id, {"status": "cancelled", "notes": notes})


def fetch_stats() -> dict[str, int]:
    empty = {"total_calls": 0, "total_bookings": 0, "avg_duration": 0, "booking_rate": 0}
    supabase = get_supabase()
    if not supabase:
        return empty
    try:
        rows = supabase.table("call_logs").select("duration_seconds, summary, was_booked").execute().data or []
        total = len(rows)
        bookings = sum(
            1 for row in rows if row.get("was_booked") or "confirmed" in (row.get("summary") or "").lower()
        )
        durations = [row["duration_seconds"] for row in rows if row.get("duration_seconds")]
        avg_duration = round(sum(durations) / len(durations)) if durations else 0
        booking_rate = round((bookings / total) * 100) if total else 0
        return {
            "total_calls": total,
            "total_bookings": bookings,
            "avg_duration": avg_duration,
            "booking_rate": booking_rate,
        }
    except Exception as exc:
        logger.error(f"Failed to fetch stats: {exc}")
        return empty


def save_call_turn_metric(payload: dict[str, Any]) -> dict[str, Any] | None:
    supabase = get_supabase()
    if not supabase:
        return None
    row = {
        "call_room_id": str(payload.get("call_room_id") or "").strip() or None,
        "phone_number": normalize_phone_number(payload.get("phone_number", "")) or None,
        "turn_index": int(payload.get("turn_index") or 0),
        "speaker": str(payload.get("speaker") or "assistant").strip().lower() or "assistant",
        "stt_endpoint_ms": payload.get("stt_endpoint_ms"),
        "kb_ms": payload.get("kb_ms"),
        "llm_first_token_ms": payload.get("llm_first_token_ms"),
        "tts_first_audio_ms": payload.get("tts_first_audio_ms"),
        "tool_ms": payload.get("tool_ms"),
        "total_turn_ms": payload.get("total_turn_ms"),
        "kb_used": bool(payload.get("kb_used", False)),
        "kb_skipped_reason": str(payload.get("kb_skipped_reason") or "").strip() or None,
        "metadata": payload.get("metadata") or {},
        "created_at": str(payload.get("created_at") or _utcnow_iso()),
    }
    try:
        res = supabase.table("call_turn_metrics").insert(row).execute()
        rows = res.data or []
        return rows[0] if rows else row
    except Exception as exc:
        if _is_schema_error(str(exc)):
            logger.warning("call_turn_metrics table is missing. Run sql/supabase/setup.sql.")
            return None
        logger.error(f"Failed to save call turn metric: {exc}")
        return None


def list_call_turn_metrics(
    *,
    call_room_id: str | None = None,
    phone_number: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    supabase = get_supabase()
    if not supabase:
        return []
    try:
        query = supabase.table("call_turn_metrics").select("*").order("created_at")
        if call_room_id:
            query = query.eq("call_room_id", str(call_room_id))
        if phone_number:
            query = query.eq("phone_number", normalize_phone_number(phone_number))
        if limit:
            query = query.limit(limit)
        res = query.execute()
        return res.data or []
    except Exception as exc:
        if _is_schema_error(str(exc)):
            return []
        logger.error(f"Failed to fetch call turn metrics: {exc}")
        return []
