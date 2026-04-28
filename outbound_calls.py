import json
import os
import random

from dotenv import load_dotenv
from livekit import api
from backend_config import get_outbound_sip_trunk_id, read_config as read_backend_config

DEFAULT_AGENT_NAME = "outbound-caller"

load_dotenv(".env")


def read_config() -> dict:
    return dict(read_backend_config())


def get_setting(config: dict, key: str, env_key: str, default: str = "") -> str:
    value = config.get(key)
    if value not in (None, ""):
        return str(value)
    return os.getenv(env_key, default)


def get_livekit_settings(config: dict | None = None) -> dict[str, str]:
    config = config or read_config()
    return {
        "url": get_setting(config, "livekit_url", "LIVEKIT_URL"),
        "api_key": get_setting(config, "livekit_api_key", "LIVEKIT_API_KEY"),
        "api_secret": get_setting(config, "livekit_api_secret", "LIVEKIT_API_SECRET"),
        "sip_trunk_id": get_outbound_sip_trunk_id(config),
    }


def normalize_phone_number(phone_number: str) -> str:
    import db as _db_module
    result = _db_module.normalize_phone_number(phone_number)
    if not result:
        raise ValueError("Phone number must start with + and country code")
    return result


def validate_livekit_settings(settings: dict[str, str]) -> None:
    if not (settings.get("url") and settings.get("api_key") and settings.get("api_secret")):
        raise ValueError("LiveKit URL, API key, and API secret must be configured first")
    if not settings.get("sip_trunk_id"):
        raise ValueError("SIP trunk ID is missing. Add it in API Credentials before placing outbound calls.")


async def dispatch_outbound_call(
    phone_number: str,
    *,
    config: dict | None = None,
    livekit_settings: dict[str, str] | None = None,
    caller_name: str = "",
    extra_metadata: dict | None = None,
    agent_name: str = DEFAULT_AGENT_NAME,
) -> dict:
    phone = normalize_phone_number(phone_number)
    settings = livekit_settings or get_livekit_settings(config)
    validate_livekit_settings(settings)

    room_name = f"call-{phone.replace('+', '')}-{random.randint(1000, 9999)}"
    metadata = {
        "phone_number": phone,
        "sip_trunk_id": settings["sip_trunk_id"],
    }
    if caller_name:
        metadata["caller_name"] = caller_name
    if extra_metadata:
        metadata.update(extra_metadata)

    lk = api.LiveKitAPI(
        url=settings["url"],
        api_key=settings["api_key"],
        api_secret=settings["api_secret"],
    )
    try:
        dispatch = await lk.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(
                agent_name=agent_name,
                room=room_name,
                metadata=json.dumps(metadata),
            )
        )
        return {
            "status": "ok",
            "dispatch_id": dispatch.id,
            "room": room_name,
            "phone": phone,
            "sip_trunk_id": settings["sip_trunk_id"],
        }
    finally:
        await lk.aclose()
