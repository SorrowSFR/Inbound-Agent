from __future__ import annotations

import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
LEGACY_CONFIG_FILE = PROJECT_ROOT / "config.json"

_PUBLIC_URL_ENV_KEYS = (
    "APP_BASE_URL",
    "PUBLIC_BASE_URL",
    "COOLIFY_URL",
    "COOLIFY_FQDN",
    "SERVICE_URL_APP",
    "SERVICE_URL_APP_8000",
    "SERVICE_FQDN_APP",
    "SERVICE_FQDN_APP_8000",
    "SERVICE_URL_SPXAGENT",
    "SERVICE_URL_SPXAGENT_8000",
    "SERVICE_FQDN_SPXAGENT",
    "SERVICE_FQDN_SPXAGENT_8000",
)


def get_app_data_dir() -> Path:
    raw = str(os.getenv("APP_DATA_DIR", "") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return PROJECT_ROOT / "data"


def get_primary_config_path() -> Path:
    for key in ("APP_CONFIG_FILE", "SPX_CONFIG_FILE", "CONFIG_FILE"):
        raw = str(os.getenv(key, "") or "").strip()
        if raw:
            return Path(raw).expanduser()
    return get_app_data_dir() / "config.json"


def get_config_read_paths() -> list[Path]:
    paths: list[Path] = []
    seen: set[str] = set()
    for path in (get_primary_config_path(), LEGACY_CONFIG_FILE):
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        paths.append(path)
    return paths


def ensure_primary_config_parent() -> Path:
    path = get_primary_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def normalize_public_base_url(raw_value: str | None) -> str:
    raw = str(raw_value or "").strip()
    if not raw:
        return ""
    value = raw.split(",", 1)[0].strip().rstrip("/")
    if not value:
        return ""
    if not value.startswith(("http://", "https://")):
        value = f"https://{value}"
    return value.rstrip("/")


def get_public_base_url(*candidates: str | None) -> str:
    for candidate in candidates:
        value = normalize_public_base_url(candidate)
        if value:
            return value
    for key in _PUBLIC_URL_ENV_KEYS:
        value = normalize_public_base_url(os.getenv(key, ""))
        if value:
            return value
    return ""
