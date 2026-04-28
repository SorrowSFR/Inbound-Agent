import logging
import os
from datetime import datetime

import requests

logger = logging.getLogger("notify")


def _env_or_config(config: dict | None, key: str, env_key: str, default: str = ""):
    config = config or {}
    value = config.get(key)
    if value not in (None, ""):
        return value
    return os.environ.get(env_key, default)


def normalize_phone_number(phone_number: str) -> str:
    import db as _db_module

    result = _db_module.normalize_phone_number(phone_number)
    if not result:
        raise ValueError("Phone number must start with + and country code")
    return result


def _get_telegram_config(config: dict | None = None) -> tuple[str, str, str]:
    token = str(_env_or_config(config, "telegram_bot_token", "TELEGRAM_BOT_TOKEN", "")).strip()
    chat_id = str(_env_or_config(config, "telegram_chat_id", "TELEGRAM_CHAT_ID", "")).strip()
    url = f"https://api.telegram.org/bot{token}/sendMessage" if token else ""
    return token, chat_id, url


def send_telegram(message: str, *, config: dict | None = None) -> bool:
    token, chat_id, telegram_url = _get_telegram_config(config)
    if not token or not chat_id:
        logger.warning("[TELEGRAM] Token or chat ID is not configured")
        return False
    try:
        response = requests.post(
            telegram_url,
            json={"chat_id": chat_id, "text": message, "parse_mode": "Markdown"},
            timeout=5,
        )
        response.raise_for_status()
        return True
    except Exception as exc:
        logger.error("[TELEGRAM] Failed: %s", exc)
        return False


def notify_booking_confirmed(
    caller_name: str,
    caller_phone: str,
    booking_time_iso: str,
    booking_id: str,
    notes: str = "",
    tts_voice: str = "",
    ai_summary: str = "",
    *,
    config: dict | None = None,
) -> bool:
    try:
        dt = datetime.fromisoformat(booking_time_iso)
        readable = dt.strftime("%A, %d %B %Y at %I:%M %p IST")
    except Exception:
        readable = booking_time_iso

    message = (
        f"*New Booking Confirmed*\n"
        f"Name: {caller_name or 'Unknown'}\n"
        f"Phone: `{caller_phone}`\n"
        f"Time: {readable}\n"
        f"Booking ID: `{booking_id}`\n"
        f"Notes: {notes or '-'}\n"
        f"Voice: {tts_voice or '-'}\n"
        + (f"\nAI Summary:\n_{ai_summary}_" if ai_summary else "")
    )
    return send_telegram(message, config=config)


def notify_booking_cancelled(
    caller_name: str,
    caller_phone: str,
    booking_id: str,
    reason: str = "",
    *,
    config: dict | None = None,
) -> bool:
    message = (
        f"*Booking Cancelled*\n"
        f"Name: {caller_name or 'Unknown'}\n"
        f"Phone: `{caller_phone}`\n"
        f"Booking ID: `{booking_id}`\n"
        f"Reason: {reason or 'Cancelled'}"
    )
    return send_telegram(message, config=config)


def notify_call_no_booking(
    caller_name: str,
    caller_phone: str,
    call_summary: str = "",
    tts_voice: str = "",
    ai_summary: str = "",
    duration_seconds: int = 0,
    *,
    config: dict | None = None,
) -> bool:
    message = (
        f"*Call Ended - No Booking*\n"
        f"Name: {caller_name or 'Unknown'}\n"
        f"Phone: `{caller_phone}`\n"
        f"Duration: {duration_seconds}s\n"
        f"Voice: {tts_voice or '-'}\n"
        f"Summary: _{ai_summary or call_summary or 'Caller did not schedule.'}_"
    )
    return send_telegram(message, config=config)


def notify_agent_error(caller_phone: str, error: str, *, config: dict | None = None) -> bool:
    message = (
        f"*Agent Error During Call*\n"
        f"Phone: `{caller_phone}`\n"
        f"Error: `{error}`"
    )
    return send_telegram(message, config=config)
