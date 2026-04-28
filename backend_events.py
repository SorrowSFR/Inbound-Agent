from __future__ import annotations

from datetime import datetime

import db
from notify import (
    notify_booking_cancelled,
    notify_booking_confirmed,
    notify_call_no_booking,
)


def _format_booking_time(booking_time_iso: str) -> str:
    try:
        dt = datetime.fromisoformat(booking_time_iso.replace("Z", "+00:00"))
        return dt.isoformat()
    except Exception:
        return booking_time_iso


def handle_booking_confirmed(
    *,
    appointment: dict,
    caller_name: str = "",
    phone_number: str = "",
    notes: str = "",
    tts_voice: str = "",
    ai_summary: str = "",
    config: dict | None = None,
) -> list[dict]:
    notify_booking_confirmed(
        caller_name=caller_name or appointment.get("contact_name") or "",
        caller_phone=phone_number or appointment.get("contact_phone") or "",
        booking_time_iso=_format_booking_time(
            str(appointment.get("scheduled_start") or appointment.get("start_time") or "")
        ),
        booking_id=str(appointment.get("id") or ""),
        notes=notes,
        tts_voice=tts_voice,
        ai_summary=ai_summary,
        config=config,
    )
    return []


def handle_appointment_updated(appointment: dict, *, config: dict | None = None) -> list[dict]:
    return []


def handle_appointment_cancelled(
    appointment: dict,
    *,
    reason: str = "",
    config: dict | None = None,
) -> list[dict]:
    notify_booking_cancelled(
        caller_name=appointment.get("contact_name") or "",
        caller_phone=appointment.get("contact_phone") or "",
        booking_id=str(appointment.get("id") or ""),
        reason=reason,
        config=config,
    )
    return []


def handle_call_no_booking(
    *,
    caller_name: str,
    phone_number: str,
    call_summary: str,
    related_call_room_id: str,
    config: dict | None = None,
) -> list[dict]:
    notify_call_no_booking(
        caller_name=caller_name,
        caller_phone=db.normalize_phone_number(phone_number),
        call_summary=call_summary,
        ai_summary=call_summary,
        config=config,
    )
    return []

