from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from runtime_env import ensure_primary_config_parent, get_config_read_paths


PROJECT_ROOT = Path(__file__).resolve().parent
CONFIGS_DIR = PROJECT_ROOT / "configs"

DEFAULT_GEMINI_LIVE_MODEL = "gemini-3.1-flash-live-preview"
DEFAULT_GEMINI_LIVE_VOICE = "Puck"
DEFAULT_GEMINI_LIVE_TEMPERATURE = 0.8
DEFAULT_GEMINI_LIVE_LANGUAGE = ""
DEFAULT_GEMINI_LIVE_PREFLIGHT_TIMEOUT = 6.0
DEFAULT_GEMINI_LIVE_CONNECT_TIMEOUT = 20.0
DEFAULT_GEMINI_LIVE_CONNECT_RETRIES = 2
DEFAULT_GEMINI_TTS_MODEL = "gemini-3.1-flash-tts-preview"
DEFAULT_FIRST_LINE = (
    "Namaste! This is Aryan from SPX AI - we help businesses automate with AI. "
    "Hmm, may I ask what kind of business you run?"
)

DEFAULT_CONFIG: dict[str, Any] = {
    "first_line": DEFAULT_FIRST_LINE,
    "agent_instructions": "",
    "gemini_live_model": DEFAULT_GEMINI_LIVE_MODEL,
    "gemini_live_voice": DEFAULT_GEMINI_LIVE_VOICE,
    "gemini_live_temperature": DEFAULT_GEMINI_LIVE_TEMPERATURE,
    "gemini_live_language": DEFAULT_GEMINI_LIVE_LANGUAGE,
    "gemini_live_preflight_enabled": False,
    "gemini_live_preflight_timeout": DEFAULT_GEMINI_LIVE_PREFLIGHT_TIMEOUT,
    "gemini_live_connect_timeout": DEFAULT_GEMINI_LIVE_CONNECT_TIMEOUT,
    "gemini_live_connect_retries": DEFAULT_GEMINI_LIVE_CONNECT_RETRIES,
    "gemini_tts_model": DEFAULT_GEMINI_TTS_MODEL,
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
    "kb_enabled": True,
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
    "kb_embedding_fallback_model": "gemini-embedding-001",
    "kb_index_kind": "flat_ip",
    "kb_rerank_enabled": False,
}

ALLOWED_CONFIG_KEYS = tuple(DEFAULT_CONFIG.keys())

ENV_KEY_MAP = {
    "first_line": "FIRST_LINE",
    "agent_instructions": "AGENT_INSTRUCTIONS",
    "gemini_live_model": "GEMINI_LIVE_MODEL",
    "gemini_live_voice": "GEMINI_LIVE_VOICE",
    "gemini_live_temperature": "GEMINI_LIVE_TEMPERATURE",
    "gemini_live_language": "GEMINI_LIVE_LANGUAGE",
    "gemini_live_preflight_enabled": "GEMINI_LIVE_PREFLIGHT_ENABLED",
    "gemini_live_preflight_timeout": "GEMINI_LIVE_PREFLIGHT_TIMEOUT",
    "gemini_live_connect_timeout": "GEMINI_LIVE_CONNECT_TIMEOUT",
    "gemini_live_connect_retries": "GEMINI_LIVE_CONNECT_RETRIES",
    "gemini_tts_model": "GEMINI_TTS_MODEL",
    "lang_preset": "LANG_PRESET",
    "max_turns": "MAX_TURNS",
    "user_away_timeout": "USER_AWAY_TIMEOUT",
    "session_close_transcript_timeout": "SESSION_CLOSE_TRANSCRIPT_TIMEOUT",
    "livekit_url": "LIVEKIT_URL",
    "livekit_api_key": "LIVEKIT_API_KEY",
    "livekit_api_secret": "LIVEKIT_API_SECRET",
    "sip_trunk_id": "SIP_TRUNK_ID",
    "google_api_key": "GOOGLE_API_KEY",
    "telegram_bot_token": "TELEGRAM_BOT_TOKEN",
    "telegram_chat_id": "TELEGRAM_CHAT_ID",
    "supabase_url": "SUPABASE_URL",
    "supabase_key": "SUPABASE_KEY",
    "kb_enabled": "KB_ENABLED",
    "kb_backend": "KB_BACKEND",
    "kb_data_dir": "KB_DATA_DIR",
    "kb_top_k": "KB_TOP_K",
    "kb_similarity_threshold": "KB_SIMILARITY_THRESHOLD",
    "kb_context_char_budget": "KB_CONTEXT_CHAR_BUDGET",
    "kb_live_timeout_ms": "KB_LIVE_TIMEOUT_MS",
    "kb_live_context_char_budget": "KB_LIVE_CONTEXT_CHAR_BUDGET",
    "kb_cache_ttl_seconds": "KB_CACHE_TTL_SECONDS",
    "kb_chunk_size": "KB_CHUNK_SIZE",
    "kb_chunk_overlap": "KB_CHUNK_OVERLAP",
    "kb_worker_poll_seconds": "KB_WORKER_POLL_SECONDS",
    "kb_embedding_provider": "KB_EMBEDDING_PROVIDER",
    "kb_embedding_model": "KB_EMBEDDING_MODEL",
    "kb_embedding_fallback_provider": "KB_EMBEDDING_FALLBACK_PROVIDER",
    "kb_embedding_fallback_model": "KB_EMBEDDING_FALLBACK_MODEL",
    "kb_index_kind": "KB_INDEX_KIND",
    "kb_rerank_enabled": "KB_RERANK_ENABLED",
}


def parse_bool(value: Any, default: bool) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def parse_float(value: Any, default: float) -> float:
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_int(value: Any, default: int) -> int:
    if value in (None, ""):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _load_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return {}
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _normalized_phone_suffix(phone_number: str | None) -> str:
    raw = str(phone_number or "").strip()
    if not raw or raw == "unknown":
        return ""
    return "".join(ch for ch in raw if ch.isdigit())


def _config_layers(phone_number: str | None = None) -> list[dict[str, Any]]:
    layers: list[dict[str, Any]] = []

    shared_config = CONFIGS_DIR / "default.json"
    if shared_config.exists():
        layers.append(_load_json(shared_config))

    for path in reversed(get_config_read_paths()):
        if path.exists():
            layers.append(_load_json(path))

    phone_suffix = _normalized_phone_suffix(phone_number)
    if phone_suffix:
        per_phone = CONFIGS_DIR / f"{phone_suffix}.json"
        if per_phone.exists():
            layers.append(_load_json(per_phone))

    return layers


def _normalize_config(values: dict[str, Any] | None) -> dict[str, Any]:
    raw = dict(DEFAULT_CONFIG)
    for key in ALLOWED_CONFIG_KEYS:
        if values and key in values and values[key] not in (None, ""):
            raw[key] = values[key]
        else:
            env_key = ENV_KEY_MAP.get(key)
            if env_key:
                env_value = os.getenv(env_key, "")
                if env_value not in (None, ""):
                    raw[key] = env_value

    normalized = {
        "first_line": str(raw.get("first_line") or DEFAULT_CONFIG["first_line"]).strip() or DEFAULT_CONFIG["first_line"],
        "agent_instructions": str(raw.get("agent_instructions") or "").strip(),
        "gemini_live_model": str(raw.get("gemini_live_model") or DEFAULT_GEMINI_LIVE_MODEL).strip() or DEFAULT_GEMINI_LIVE_MODEL,
        "gemini_live_voice": str(raw.get("gemini_live_voice") or DEFAULT_GEMINI_LIVE_VOICE).strip() or DEFAULT_GEMINI_LIVE_VOICE,
        "gemini_live_temperature": max(0.0, min(2.0, parse_float(raw.get("gemini_live_temperature"), DEFAULT_GEMINI_LIVE_TEMPERATURE))),
        "gemini_live_language": str(raw.get("gemini_live_language") or "").strip(),
        "gemini_live_preflight_enabled": parse_bool(raw.get("gemini_live_preflight_enabled"), False),
        "gemini_live_preflight_timeout": max(1.0, min(20.0, parse_float(raw.get("gemini_live_preflight_timeout"), DEFAULT_GEMINI_LIVE_PREFLIGHT_TIMEOUT))),
        "gemini_live_connect_timeout": max(5.0, min(60.0, parse_float(raw.get("gemini_live_connect_timeout"), DEFAULT_GEMINI_LIVE_CONNECT_TIMEOUT))),
        "gemini_live_connect_retries": max(0, min(10, parse_int(raw.get("gemini_live_connect_retries"), DEFAULT_GEMINI_LIVE_CONNECT_RETRIES))),
        "gemini_tts_model": str(raw.get("gemini_tts_model") or DEFAULT_GEMINI_TTS_MODEL).strip() or DEFAULT_GEMINI_TTS_MODEL,
        "lang_preset": str(raw.get("lang_preset") or "multilingual").strip() or "multilingual",
        "max_turns": max(1, parse_int(raw.get("max_turns"), 25)),
        "user_away_timeout": max(1.0, parse_float(raw.get("user_away_timeout"), 15.0)),
        "session_close_transcript_timeout": max(0.5, parse_float(raw.get("session_close_transcript_timeout"), 2.0)),
        "livekit_url": str(raw.get("livekit_url") or "").strip(),
        "livekit_api_key": str(raw.get("livekit_api_key") or "").strip(),
        "livekit_api_secret": str(raw.get("livekit_api_secret") or "").strip(),
        "sip_trunk_id": str(raw.get("sip_trunk_id") or "").strip(),
        "google_api_key": str(raw.get("google_api_key") or "").strip(),
        "telegram_bot_token": str(raw.get("telegram_bot_token") or "").strip(),
        "telegram_chat_id": str(raw.get("telegram_chat_id") or "").strip(),
        "supabase_url": str(raw.get("supabase_url") or "").strip(),
        "supabase_key": str(raw.get("supabase_key") or "").strip(),
        "kb_enabled": parse_bool(raw.get("kb_enabled"), True),
        "kb_backend": str(raw.get("kb_backend") or "local_faiss").strip() or "local_faiss",
        "kb_data_dir": str(raw.get("kb_data_dir") or "data/kb").strip() or "data/kb",
        "kb_top_k": max(1, parse_int(raw.get("kb_top_k"), 4)),
        "kb_similarity_threshold": parse_float(raw.get("kb_similarity_threshold"), 0.18),
        "kb_context_char_budget": max(400, parse_int(raw.get("kb_context_char_budget"), 2800)),
        "kb_live_timeout_ms": max(50, parse_int(raw.get("kb_live_timeout_ms"), 150)),
        "kb_live_context_char_budget": max(280, parse_int(raw.get("kb_live_context_char_budget"), 900)),
        "kb_cache_ttl_seconds": max(10, parse_int(raw.get("kb_cache_ttl_seconds"), 45)),
        "kb_chunk_size": max(120, parse_int(raw.get("kb_chunk_size"), 400)),
        "kb_chunk_overlap": max(20, parse_int(raw.get("kb_chunk_overlap"), 60)),
        "kb_worker_poll_seconds": max(5, parse_int(raw.get("kb_worker_poll_seconds"), 20)),
        "kb_embedding_provider": str(raw.get("kb_embedding_provider") or "local").strip().lower() or "local",
        "kb_embedding_model": str(raw.get("kb_embedding_model") or "BAAI/bge-small-en-v1.5").strip() or "BAAI/bge-small-en-v1.5",
        "kb_embedding_fallback_provider": str(raw.get("kb_embedding_fallback_provider") or "gemini").strip().lower() or "gemini",
        "kb_embedding_fallback_model": str(raw.get("kb_embedding_fallback_model") or "gemini-embedding-001").strip() or "gemini-embedding-001",
        "kb_index_kind": str(raw.get("kb_index_kind") or "flat_ip").strip().lower() or "flat_ip",
        "kb_rerank_enabled": parse_bool(raw.get("kb_rerank_enabled"), False),
    }
    return normalized


def read_config(phone_number: str | None = None) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for layer in _config_layers(phone_number):
        merged.update({key: value for key, value in layer.items() if key in ALLOWED_CONFIG_KEYS})
    return _normalize_config(merged)


def write_config(data: dict[str, Any]) -> dict[str, Any]:
    current = _load_json(ensure_primary_config_parent())
    filtered = {key: value for key, value in current.items() if key in ALLOWED_CONFIG_KEYS}
    for key in ALLOWED_CONFIG_KEYS:
        if key in data:
            filtered[key] = data[key]
    normalized = _normalize_config(filtered)
    config_path = ensure_primary_config_parent()
    with config_path.open("w", encoding="utf-8") as handle:
        json.dump(normalized, handle, indent=4)
    return normalized


def apply_config_env(config: dict[str, Any] | None) -> None:
    for key, env_key in ENV_KEY_MAP.items():
        value = (config or {}).get(key)
        if value not in (None, ""):
            os.environ[env_key] = str(value)


def get_outbound_sip_trunk_id(config: dict[str, Any], metadata: dict[str, Any] | None = None) -> str:
    if metadata:
        trunk_id = (
            metadata.get("sip_trunk_id")
            or metadata.get("outbound_trunk_id")
            or metadata.get("sip_outbound_trunk_id")
        )
        if trunk_id:
            return str(trunk_id).strip()

    return (
        str(config.get("sip_trunk_id") or "").strip()
        or os.environ.get("SIP_TRUNK_ID", "")
        or os.environ.get("OUTBOUND_TRUNK_ID", "")
        or os.environ.get("SIP_OUTBOUND_TRUNK_ID", "")
    )
