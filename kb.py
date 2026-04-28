import hashlib
import io
import json
import logging
import math
import mimetypes
import os
import ipaddress
import re
import sqlite3
import threading
import time
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")

import httpx
import numpy as np
import tiktoken
import trafilatura
from pypdf import PdfReader

logger = logging.getLogger("kb")

KB_SOURCE_TYPES = {"pdf_upload", "web_url", "text_note", "leadrat_crm"}
KB_JOB_STATUSES = {"pending", "processing", "completed", "failed", "cancelled"}
KB_JOB_TYPES = {"ingest", "sync", "reindex"}
KB_CACHE_TTL_SECONDS = 45
KB_DEFAULT_EMBED_DIMENSIONS = 384
KB_DEFAULT_EMBED_MODEL = "BAAI/bge-small-en-v1.5"
LEADRAT_BASE_URL = "https://connect.leadrat.com"

_TOKENIZER = tiktoken.get_encoding("cl100k_base")
_FASTEMBED_MODEL: Any = None
_FASTEMBED_MODEL_NAME = ""
_FASTEMBED_UNAVAILABLE_LOGGED = False
_FASTEMBED_LOAD_FAILURES: set[str] = set()
_FASTEMBED_LOCK = threading.Lock()
_GEMINI_EMBED_UNAVAILABLE_LOGGED = False
_GEMINI_EMBED_FAILURES: set[str] = set()
_CACHE: dict[str, dict[str, Any]] = {
    "sources": {"at": 0.0, "items": []},
    "jobs": {"at": 0.0, "items": []},
    "chunks": {"at": 0.0, "items": []},
    "entities": {"at": 0.0, "items": []},
    "chunk_index": {"at": 0.0, "items": None},
}

KB_QUERY_HINTS = {
    "project", "property", "flat", "apartment", "villa", "plot", "bhk", "price",
    "pricing", "budget", "availability", "available", "status", "possession",
    "amenities", "brochure", "location", "locality", "tower", "community", "rera",
    "sqft", "area", "carpet", "saleable", "furnished", "rent", "buy", "purchase",
}
KB_TEXT_HINTS = {
    "about", "details", "overview", "features", "describe", "document", "pdf",
    "website", "link", "explain", "guide", "note", "brochure",
}
KB_STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "and", "or", "of", "for", "to",
    "in", "on", "at", "me", "you", "i", "we", "our", "your", "their", "it", "this",
    "that", "with", "from", "please", "can", "could", "would", "should", "want",
    "need", "know", "tell", "show", "give", "about", "some", "any", "there", "have",
    "has", "had", "into", "than", "then", "what", "which", "when", "where", "who", "how",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _utcnow_iso() -> str:
    return _utcnow().isoformat()


def parse_bool(value: Any, default: bool) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def parse_int(value: Any, default: int) -> int:
    if value in (None, ""):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def parse_float(value: Any, default: float) -> float:
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def get_runtime_config(config: dict | None = None) -> dict[str, Any]:
    config = config or {}

    def get_value(key: str, env_key: str, default: Any) -> Any:
        value = config.get(key)
        if value not in (None, ""):
            return value
        return os.getenv(env_key, default)

    data_dir = str(get_value("kb_data_dir", "KB_DATA_DIR", "data/kb") or "data/kb").strip()
    return {
        "kb_enabled": parse_bool(get_value("kb_enabled", "KB_ENABLED", True), True),
        "kb_backend": str(get_value("kb_backend", "KB_BACKEND", "local_faiss") or "local_faiss").strip(),
        "kb_data_dir": data_dir,
        "kb_top_k": max(1, parse_int(get_value("kb_top_k", "KB_TOP_K", 4), 4)),
        "kb_inventory_top_k": max(1, parse_int(get_value("kb_inventory_top_k", "KB_INVENTORY_TOP_K", 3), 3)),
        "kb_similarity_threshold": parse_float(get_value("kb_similarity_threshold", "KB_SIMILARITY_THRESHOLD", 0.18), 0.18),
        "kb_context_char_budget": max(400, parse_int(get_value("kb_context_char_budget", "KB_CONTEXT_CHAR_BUDGET", 2800), 2800)),
        "kb_live_timeout_ms": max(50, parse_int(get_value("kb_live_timeout_ms", "KB_LIVE_TIMEOUT_MS", 150), 150)),
        "kb_live_context_char_budget": max(280, parse_int(get_value("kb_live_context_char_budget", "KB_LIVE_CONTEXT_CHAR_BUDGET", 900), 900)),
        "kb_cache_ttl_seconds": max(5, parse_int(get_value("kb_cache_ttl_seconds", "KB_CACHE_TTL_SECONDS", KB_CACHE_TTL_SECONDS), KB_CACHE_TTL_SECONDS)),
        "kb_chunk_size": max(120, parse_int(get_value("kb_chunk_size", "KB_CHUNK_SIZE", 400), 400)),
        "kb_chunk_overlap": max(20, parse_int(get_value("kb_chunk_overlap", "KB_CHUNK_OVERLAP", 60), 60)),
        "kb_worker_poll_seconds": max(5, parse_int(get_value("kb_worker_poll_seconds", "KB_WORKER_POLL_SECONDS", 20), 20)),
        "kb_embedding_provider": str(get_value("kb_embedding_provider", "KB_EMBEDDING_PROVIDER", "local") or "local").strip().lower(),
        "kb_embedding_model": str(get_value("kb_embedding_model", "KB_EMBEDDING_MODEL", KB_DEFAULT_EMBED_MODEL) or KB_DEFAULT_EMBED_MODEL).strip(),
        "kb_embedding_fallback_provider": str(get_value("kb_embedding_fallback_provider", "KB_EMBEDDING_FALLBACK_PROVIDER", "gemini") or "gemini").strip().lower(),
        "kb_embedding_fallback_model": str(get_value("kb_embedding_fallback_model", "KB_EMBEDDING_FALLBACK_MODEL", "gemini-embedding-001") or "gemini-embedding-001").strip(),
        "kb_embedding_dimensions": max(16, parse_int(get_value("kb_embedding_dimensions", "KB_EMBEDDING_DIMENSIONS", KB_DEFAULT_EMBED_DIMENSIONS), KB_DEFAULT_EMBED_DIMENSIONS)),
        "google_api_key": str(get_value("google_api_key", "GOOGLE_API_KEY", "") or "").strip(),
        "kb_index_kind": str(get_value("kb_index_kind", "KB_INDEX_KIND", "flat_ip") or "flat_ip").strip().lower(),
        "kb_rerank_enabled": parse_bool(get_value("kb_rerank_enabled", "KB_RERANK_ENABLED", False), False),
        "leadrat_enabled": parse_bool(get_value("leadrat_enabled", "LEADRAT_ENABLED", False), False),
        "leadrat_tenant": str(get_value("leadrat_tenant", "LEADRAT_TENANT", "") or "").strip(),
        "leadrat_api_key": str(get_value("leadrat_api_key", "LEADRAT_API_KEY", "") or "").strip(),
        "leadrat_secret_key": str(get_value("leadrat_secret_key", "LEADRAT_SECRET_KEY", "") or "").strip(),
        "leadrat_sync_interval_minutes": max(5, parse_int(get_value("leadrat_sync_interval_minutes", "LEADRAT_SYNC_INTERVAL_MINUTES", 5), 5)),
        "leadrat_base_url": str(get_value("leadrat_base_url", "LEADRAT_BASE_URL", LEADRAT_BASE_URL) or LEADRAT_BASE_URL).strip() or LEADRAT_BASE_URL,
    }


def _data_dir(config: dict | None = None) -> Path:
    return Path(get_runtime_config(config)["kb_data_dir"]).expanduser()


def _db_path(config: dict | None = None) -> Path:
    return _data_dir(config) / "kb.sqlite3"


def _files_dir(config: dict | None = None) -> Path:
    return _data_dir(config) / "files"


def _indexes_dir(config: dict | None = None) -> Path:
    return _data_dir(config) / "indexes"


def _ensure_dirs(config: dict | None = None) -> None:
    _files_dir(config).mkdir(parents=True, exist_ok=True)
    _indexes_dir(config).mkdir(parents=True, exist_ok=True)


def _resolve_managed_storage_path(storage_path: str, *, config: dict | None = None) -> Path:
    base = _managed_files_root(config)
    raw_path = Path(str(storage_path or "").strip()).expanduser()
    resolved = raw_path.resolve() if raw_path.is_absolute() else (base / raw_path).resolve()
    if not _is_relative_to(resolved, base):
        raise ValueError("KB file path must stay inside the managed kb files directory.")
    return resolved


def _validate_source_payload(
    source_type: str,
    *,
    source_url: str | None = None,
    raw_text: str | None = None,
    storage_path: str | None = None,
    config: dict | None = None,
) -> None:
    url_text = str(source_url or "").strip()
    text_body = str(raw_text or "").strip()
    path_text = str(storage_path or "").strip()

    if source_type == "text_note":
        if not text_body:
            raise ValueError("Text-note KB sources require raw_text.")
        return
    if source_type == "web_url":
        if not url_text:
            raise ValueError("Web KB sources require source_url.")
        _validate_public_http_url(url_text)
        return
    if source_type == "pdf_upload":
        if path_text:
            _resolve_managed_storage_path(path_text, config=config)
            return
        if not url_text:
            raise ValueError("PDF KB sources require storage_path or a downloadable source_url.")
        if urlparse(url_text).scheme in {"http", "https"}:
            _validate_public_http_url(url_text)
            return
        _resolve_managed_storage_path(url_text, config=config)
        return


def _connect(config: dict | None = None) -> sqlite3.Connection:
    _ensure_dirs(config)
    conn = sqlite3.connect(_db_path(config), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    _ensure_schema(conn)
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS kb_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            source_type TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            source_url TEXT,
            raw_text TEXT,
            storage_bucket TEXT,
            storage_path TEXT,
            mime_type TEXT,
            checksum TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            enabled INTEGER NOT NULL DEFAULT 1,
            last_synced_at TEXT,
            sync_error TEXT,
            metadata TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_kb_sources_type_status ON kb_sources(source_type, status);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_kb_sources_leadrat_unique ON kb_sources(source_type) WHERE source_type = 'leadrat_crm';

        CREATE TABLE IF NOT EXISTS kb_documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            source_id INTEGER NOT NULL REFERENCES kb_sources(id) ON DELETE CASCADE,
            external_id TEXT NOT NULL,
            document_type TEXT NOT NULL DEFAULT 'generic',
            title TEXT NOT NULL DEFAULT '',
            body_text TEXT NOT NULL DEFAULT '',
            checksum TEXT,
            metadata TEXT NOT NULL DEFAULT '{}',
            UNIQUE(source_id, external_id)
        );
        CREATE INDEX IF NOT EXISTS idx_kb_documents_source ON kb_documents(source_id);

        CREATE TABLE IF NOT EXISTS kb_chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            source_id INTEGER NOT NULL REFERENCES kb_sources(id) ON DELETE CASCADE,
            document_id INTEGER NOT NULL REFERENCES kb_documents(id) ON DELETE CASCADE,
            chunk_index INTEGER NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            content TEXT NOT NULL DEFAULT '',
            checksum TEXT,
            token_count INTEGER NOT NULL DEFAULT 0,
            embedding TEXT NOT NULL DEFAULT '[]',
            metadata TEXT NOT NULL DEFAULT '{}',
            UNIQUE(document_id, chunk_index)
        );
        CREATE INDEX IF NOT EXISTS idx_kb_chunks_source ON kb_chunks(source_id);

        CREATE TABLE IF NOT EXISTS kb_structured_entities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            source_id INTEGER NOT NULL REFERENCES kb_sources(id) ON DELETE CASCADE,
            entity_type TEXT NOT NULL,
            external_id TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            serial_no TEXT,
            project_name TEXT,
            status TEXT,
            location_text TEXT,
            bhk_text TEXT,
            price_text TEXT,
            possession_text TEXT,
            attributes TEXT NOT NULL DEFAULT '{}',
            narrative_text TEXT NOT NULL DEFAULT '',
            checksum TEXT,
            embedding TEXT NOT NULL DEFAULT '[]',
            raw_payload TEXT NOT NULL DEFAULT '{}',
            last_synced_at TEXT,
            is_active INTEGER NOT NULL DEFAULT 1,
            UNIQUE(entity_type, external_id)
        );
        CREATE INDEX IF NOT EXISTS idx_kb_structured_title ON kb_structured_entities(title);
        CREATE INDEX IF NOT EXISTS idx_kb_structured_project_name ON kb_structured_entities(project_name);

        CREATE TABLE IF NOT EXISTS kb_ingest_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            source_id INTEGER REFERENCES kb_sources(id) ON DELETE CASCADE,
            source_type TEXT NOT NULL DEFAULT 'generic',
            job_type TEXT NOT NULL DEFAULT 'ingest',
            status TEXT NOT NULL DEFAULT 'pending',
            payload TEXT NOT NULL DEFAULT '{}',
            error_text TEXT,
            started_at TEXT,
            finished_at TEXT,
            attempts INTEGER NOT NULL DEFAULT 0,
            last_result TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_kb_ingest_jobs_status_created ON kb_ingest_jobs(status, created_at);

        CREATE TABLE IF NOT EXISTS kb_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """
    )
    conn.commit()


def _json_dumps(value: Any) -> str:
    return json.dumps(_coerce_jsonable(value), ensure_ascii=False, sort_keys=True)


def _json_loads(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except Exception:
        return default


def _row_to_dict(row: sqlite3.Row | dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    data = dict(row)
    for key in ["metadata", "attributes", "raw_payload", "payload", "last_result"]:
        if key in data:
            data[key] = _json_loads(data.get(key), {} if key != "last_result" else {})
    if "embedding" in data:
        data["embedding"] = _json_loads(data.get("embedding"), [])
    if "enabled" in data:
        data["enabled"] = bool(data["enabled"])
    if "is_active" in data:
        data["is_active"] = bool(data["is_active"])
    return data


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _managed_files_root(config: dict | None = None) -> Path:
    return _files_dir(config).expanduser().resolve()


def _validate_public_http_url(url: str) -> str:
    parsed = urlparse(str(url or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Only http:// and https:// URLs are allowed.")
    host = (parsed.hostname or "").strip().lower()
    if not host:
        raise ValueError("URL host is required.")
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
        raise ValueError("Localhost URLs are not allowed.")
    try:
        ip_addr = ipaddress.ip_address(host)
    except ValueError:
        return parsed.geturl()
    if (
        ip_addr.is_private
        or ip_addr.is_loopback
        or ip_addr.is_link_local
        or ip_addr.is_reserved
        or ip_addr.is_multicast
        or ip_addr.is_unspecified
    ):
        raise ValueError("Private or local network URLs are not allowed.")
    return parsed.geturl()


def _coerce_jsonable(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except Exception:
        return json.loads(json.dumps(value, default=str))


def _sha256_text(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def _safe_title(value: str | None, fallback: str = "Untitled source") -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    return text[:220] if text else fallback


def _normalize_text(value: str | None) -> str:
    text = str(value or "").casefold()
    text = re.sub(r"[^\w\s#+.-]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _tokenize_keywords(value: str | None) -> list[str]:
    tokens: list[str] = []
    for token in re.findall(r"[a-z0-9+.-]{2,}", _normalize_text(value)):
        if token in KB_STOPWORDS:
            continue
        tokens.append(token)
    return tokens


def _preview(value: str, limit: int = 280) -> str:
    clean = re.sub(r"\s+", " ", str(value or "").strip())
    return clean[:limit]


def _round_list(values: list[float], digits: int = 6) -> list[float]:
    return [round(float(v), digits) for v in values]


def _embedding_from_tokens(tokens: list[str], *, dimensions: int = KB_DEFAULT_EMBED_DIMENSIONS) -> list[float]:
    if not tokens:
        return [0.0] * dimensions
    vector = [0.0] * dimensions
    counts = Counter(tokens)
    for token, count in counts.items():
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=12).digest()
        idx = int.from_bytes(digest[:4], "big") % dimensions
        sign = -1.0 if digest[4] & 1 else 1.0
        weight = (1.0 + min(len(token), 12) / 12.0) * (1.0 + math.log1p(count))
        vector[idx] += sign * weight
    arr = np.asarray(vector, dtype=np.float32)
    norm = float(np.linalg.norm(arr))
    if norm <= 0:
        return [0.0] * dimensions
    return _round_list((arr / norm).tolist())


def _get_fastembed_model(model_name: str, *, config: dict | None = None):
    global _FASTEMBED_MODEL, _FASTEMBED_MODEL_NAME, _FASTEMBED_UNAVAILABLE_LOGGED
    if _FASTEMBED_MODEL is not None and _FASTEMBED_MODEL_NAME == model_name:
        return _FASTEMBED_MODEL
    try:
        from fastembed import TextEmbedding
    except Exception as exc:
        if not _FASTEMBED_UNAVAILABLE_LOGGED:
            logger.warning(f"[KB] fastembed unavailable, using hashed fallback embeddings: {exc}")
            _FASTEMBED_UNAVAILABLE_LOGGED = True
        return None
    with _FASTEMBED_LOCK:
        if _FASTEMBED_MODEL is not None and _FASTEMBED_MODEL_NAME == model_name:
            return _FASTEMBED_MODEL
        runtime = get_runtime_config(config)
        cache_dir = _data_dir(runtime) / "model_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
        os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
        try:
            _FASTEMBED_MODEL = TextEmbedding(model_name=model_name, cache_dir=str(cache_dir))
            _FASTEMBED_MODEL_NAME = model_name
            return _FASTEMBED_MODEL
        except Exception as exc:
            if model_name not in _FASTEMBED_LOAD_FAILURES:
                logger.warning(f"[KB] Could not load embedding model {model_name}; using hashed fallback: {exc}")
                _FASTEMBED_LOAD_FAILURES.add(model_name)
            return None


def _normalize_vector(values: list[float], *, dimensions: int) -> list[float]:
    if len(values) > dimensions:
        values = values[:dimensions]
    elif len(values) < dimensions:
        values = [*values, *([0.0] * (dimensions - len(values)))]
    arr = np.asarray(values, dtype=np.float32)
    norm = float(np.linalg.norm(arr))
    if norm > 0:
        arr = arr / norm
    return _round_list(arr.tolist())


def _embed_texts_gemini(texts: list[str], *, config: dict | None = None, is_query: bool = False) -> list[list[float]] | None:
    global _GEMINI_EMBED_UNAVAILABLE_LOGGED
    runtime = get_runtime_config(config)
    api_key = runtime.get("google_api_key") or os.environ.get("GOOGLE_API_KEY", "")
    if not str(api_key or "").strip():
        if not _GEMINI_EMBED_UNAVAILABLE_LOGGED:
            logger.warning("[KB] Gemini embedding fallback is enabled, but GOOGLE_API_KEY is missing; using hashed fallback.")
            _GEMINI_EMBED_UNAVAILABLE_LOGGED = True
        return None
    try:
        from google import genai
        from google.genai import types
    except Exception as exc:
        if not _GEMINI_EMBED_UNAVAILABLE_LOGGED:
            logger.warning(f"[KB] google-genai unavailable for Gemini embeddings; using hashed fallback: {exc}")
            _GEMINI_EMBED_UNAVAILABLE_LOGGED = True
        return None

    model_name = (
        runtime["kb_embedding_model"]
        if runtime["kb_embedding_provider"] == "gemini"
        else runtime["kb_embedding_fallback_model"]
    )
    dimensions = runtime["kb_embedding_dimensions"]
    task_type = "RETRIEVAL_QUERY" if is_query else "RETRIEVAL_DOCUMENT"
    try:
        client = genai.Client(api_key=str(api_key).strip())

        def request_embeddings(*, include_auto_truncate: bool):
            config_kwargs = {
                "task_type": task_type,
                "output_dimensionality": dimensions,
            }
            if include_auto_truncate:
                config_kwargs["auto_truncate"] = True
            return client.models.embed_content(
                model=model_name,
                contents=[str(text or "") for text in texts],
                config=types.EmbedContentConfig(**config_kwargs),
            )

        try:
            response = request_embeddings(include_auto_truncate=True)
        except Exception as exc:
            if "auto_truncate" not in str(exc).lower():
                raise
            response = request_embeddings(include_auto_truncate=False)
        embeddings = response.embeddings or []
        if len(embeddings) != len(texts):
            raise RuntimeError(f"Gemini returned {len(embeddings)} embeddings for {len(texts)} inputs")
        return [
            _normalize_vector([float(value) for value in (embedding.values or [])], dimensions=dimensions)
            for embedding in embeddings
        ]
    except Exception as exc:
        failure_key = f"{model_name}:{type(exc).__name__}:{str(exc)[:160]}"
        if failure_key not in _GEMINI_EMBED_FAILURES:
            logger.warning(f"[KB] Gemini embedding fallback failed with {model_name}; using hashed fallback: {exc}")
            _GEMINI_EMBED_FAILURES.add(failure_key)
        return None


def embed_texts(texts: list[str], *, config: dict | None = None, is_query: bool = False) -> list[list[float]]:
    runtime = get_runtime_config(config)
    dimensions = runtime["kb_embedding_dimensions"]
    provider = runtime["kb_embedding_provider"]
    fallback_provider = runtime["kb_embedding_fallback_provider"]
    if provider == "local":
        model = _get_fastembed_model(runtime["kb_embedding_model"], config=config)
        if model is not None:
            prefix = "query: " if is_query else "passage: "
            try:
                vectors = list(model.embed([prefix + str(text or "") for text in texts]))
                result: list[list[float]] = []
                for vector in vectors:
                    arr = np.asarray(vector, dtype=np.float32)
                    norm = float(np.linalg.norm(arr))
                    if norm > 0:
                        arr = arr / norm
                    result.append(_round_list(arr.tolist()))
                return result
            except Exception as exc:
                logger.warning(f"[KB] Local embedding failed; trying configured fallback: {exc}")
        if fallback_provider == "gemini":
            gemini_vectors = _embed_texts_gemini(texts, config=config, is_query=is_query)
            if gemini_vectors is not None:
                return gemini_vectors
    if provider == "gemini" or (provider == "api" and fallback_provider == "gemini"):
        gemini_vectors = _embed_texts_gemini(texts, config=config, is_query=is_query)
        if gemini_vectors is not None:
            return gemini_vectors
    elif provider == "api":
        logger.warning("[KB] API embedding provider is configured, but no API adapter is installed; using fallback embeddings.")
    return [_embedding_from_tokens(_tokenize_keywords(text), dimensions=dimensions) for text in texts]


def build_embedding(text: str, *, dimensions: int | None = None, config: dict | None = None) -> list[float]:
    if dimensions is not None and not config:
        return _embedding_from_tokens(_tokenize_keywords(text), dimensions=dimensions)
    return embed_texts([text], config=config, is_query=False)[0]


def _coerce_embedding(value: Any) -> list[float]:
    raw = _json_loads(value, []) if isinstance(value, str) else value
    if isinstance(raw, list):
        cleaned: list[float] = []
        for item in raw:
            try:
                cleaned.append(float(item))
            except (TypeError, ValueError):
                cleaned.append(0.0)
        return cleaned
    return []


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    return float(np.dot(np.asarray(left, dtype=np.float32), np.asarray(right, dtype=np.float32)))


def _keyword_overlap_score(query_tokens: list[str], candidate_tokens: list[str]) -> float:
    if not query_tokens or not candidate_tokens:
        return 0.0
    query_counts = Counter(query_tokens)
    candidate_counts = Counter(candidate_tokens)
    overlap = 0.0
    total = 0.0
    for token, count in query_counts.items():
        total += count
        overlap += min(count, candidate_counts.get(token, 0))
    return overlap / total if total else 0.0


def _reset_cache(key: str | None = None) -> None:
    if key:
        _CACHE[key] = {"at": 0.0, "items": None if key == "chunk_index" else []}
        return
    for cache_key in list(_CACHE.keys()):
        _CACHE[cache_key] = {"at": 0.0, "items": None if cache_key == "chunk_index" else []}


def _fetch_cached_rows(cache_key: str, fetcher, *, ttl: int | None = None) -> list[dict[str, Any]]:
    ttl = ttl or KB_CACHE_TTL_SECONDS
    now = time.monotonic()
    entry = _CACHE[cache_key]
    if entry["items"] and (now - entry["at"]) < ttl:
        return entry["items"]
    rows = fetcher()
    _CACHE[cache_key] = {"at": now, "items": rows}
    return rows


def _set_meta(key: str, value: Any, *, config: dict | None = None) -> None:
    with _connect(config) as conn:
        conn.execute(
            "INSERT INTO kb_meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, _json_dumps(value)),
        )
        conn.commit()


def _get_meta(key: str, default: Any = None, *, config: dict | None = None) -> Any:
    with _connect(config) as conn:
        row = conn.execute("SELECT value FROM kb_meta WHERE key = ?", (key,)).fetchone()
    if not row:
        return default
    return _json_loads(row["value"], default)


def is_kb_setup_required_error(exc: Exception | str) -> bool:
    return "kb local data directory" in str(exc or "").lower()


def is_kb_not_configured_error(exc: Exception | str) -> bool:
    return False


def kb_runtime_issue_payload(exc: Exception | str, *, config: dict | None = None) -> dict[str, Any]:
    runtime = get_runtime_config(config)
    return {
        "status": "error",
        "message": str(exc),
        "kb_enabled": runtime["kb_enabled"],
        "backend": runtime["kb_backend"],
        "data_dir": str(_data_dir(config)),
        "counts": {"sources": 0, "jobs": 0, "entities": 0, "chunks": 0},
        "leadrat": {
            "enabled": runtime["leadrat_enabled"],
            "tenant": runtime["leadrat_tenant"],
            "sync_interval_minutes": runtime["leadrat_sync_interval_minutes"],
            "source": None,
        },
    }


def chunk_text(text: str, *, chunk_size: int = 400, overlap: int = 60, config: dict | None = None) -> list[dict[str, Any]]:
    clean = re.sub(r"\s+", " ", str(text or "").strip())
    if not clean:
        return []
    tokens = _TOKENIZER.encode(clean)
    if not tokens:
        return []

    chunks: list[dict[str, Any]] = []
    text_slices: list[str] = []
    starts: list[tuple[int, int, int]] = []
    start = 0
    index = 0
    while start < len(tokens):
        end = min(len(tokens), start + chunk_size)
        token_slice = tokens[start:end]
        text_slice = _TOKENIZER.decode(token_slice).strip()
        if text_slice:
            text_slices.append(text_slice)
            starts.append((index, len(token_slice), start))
        if end >= len(tokens):
            break
        start = max(end - overlap, start + 1)
        index += 1

    embeddings = embed_texts(text_slices, config=config, is_query=False) if text_slices else []
    for (index, token_count, _), text_slice, embedding in zip(starts, text_slices, embeddings):
        chunks.append(
            {
                "chunk_index": index,
                "content": text_slice,
                "token_count": token_count,
                "checksum": _sha256_text(text_slice),
                "embedding": embedding,
            }
        )
    return chunks


def _source_type_from_payload(payload: dict[str, Any]) -> str:
    source_type = str(payload.get("source_type") or payload.get("type") or "").strip().lower()
    aliases = {"url": "web_url", "web": "web_url", "pdf": "pdf_upload", "text": "text_note", "note": "text_note"}
    source_type = aliases.get(source_type, source_type)
    if source_type not in KB_SOURCE_TYPES:
        raise ValueError(f"Unsupported KB source_type: {source_type}")
    return source_type


def list_sources(limit: int = 200, *, config: dict | None = None) -> list[dict[str, Any]]:
    with _connect(config) as conn:
        rows = conn.execute("SELECT * FROM kb_sources ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
    return [_row_to_dict(row) for row in rows if row is not None]


def get_source(source_id: str | int, *, config: dict | None = None) -> dict[str, Any] | None:
    with _connect(config) as conn:
        row = conn.execute("SELECT * FROM kb_sources WHERE id = ? LIMIT 1", (str(source_id),)).fetchone()
    return _row_to_dict(row)


def get_leadrat_source(*, config: dict | None = None) -> dict[str, Any] | None:
    with _connect(config) as conn:
        row = conn.execute("SELECT * FROM kb_sources WHERE source_type = 'leadrat_crm' LIMIT 1").fetchone()
    return _row_to_dict(row)


def create_source(payload: dict[str, Any], *, queue_sync: bool = True, config: dict | None = None) -> dict[str, Any]:
    source_type = _source_type_from_payload(payload)
    title = _safe_title(payload.get("title"), f"{source_type.replace('_', ' ').title()} Source")
    source_url = str(payload.get("source_url") or payload.get("url") or "").strip() or None
    raw_text = str(payload.get("raw_text") or payload.get("text") or payload.get("content") or "").strip() or None
    storage_path = str(payload.get("storage_path") or "").strip() or None
    mime_type = str(payload.get("mime_type") or "").strip() or None
    _validate_source_payload(
        source_type,
        source_url=source_url,
        raw_text=raw_text,
        storage_path=storage_path,
        config=config,
    )
    metadata = _coerce_jsonable(payload.get("metadata") or {})
    now = _utcnow_iso()
    checksum = _sha256_text((source_url or "") + "\n" + (raw_text or "") + "\n" + (storage_path or "") + "\n" + title)
    with _connect(config) as conn:
        cur = conn.execute(
            """
            INSERT INTO kb_sources(
                created_at, updated_at, source_type, title, source_url, raw_text, storage_bucket,
                storage_path, mime_type, checksum, status, enabled, metadata
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now, now, source_type, title, source_url, raw_text,
                str(payload.get("storage_bucket") or "").strip() or None,
                storage_path, mime_type, checksum, "pending",
                1 if parse_bool(payload.get("enabled", True), True) else 0,
                _json_dumps(metadata),
            ),
        )
        source_id = cur.lastrowid
        conn.commit()
    if queue_sync:
        queue_job(
            source_id=source_id,
            source_type=source_type,
            job_type="sync" if source_type == "leadrat_crm" else "ingest",
            payload={},
            config=config,
        )
    _reset_cache("sources")
    return get_source(source_id, config=config) or {}


def update_source(source_id: str | int, payload: dict[str, Any], *, config: dict | None = None) -> dict[str, Any]:
    current = get_source(source_id, config=config)
    if not current:
        raise ValueError(f"KB source {source_id} was not found.")
    allowed = [
        "title", "source_url", "raw_text", "storage_bucket", "storage_path", "mime_type",
        "sync_error", "status", "last_synced_at",
    ]
    update_row: dict[str, Any] = {}
    for key in allowed:
        if key in payload:
            value = payload.get(key)
            update_row[key] = str(value).strip() if isinstance(value, str) else value
    if "url" in payload and "source_url" not in update_row:
        update_row["source_url"] = str(payload.get("url") or "").strip() or None
    if "enabled" in payload:
        update_row["enabled"] = 1 if parse_bool(payload.get("enabled"), bool(current.get("enabled", True))) else 0
        if not update_row["enabled"]:
            update_row["status"] = "disabled"
        elif current.get("status") == "disabled":
            update_row["status"] = "pending"
    if "metadata" in payload:
        update_row["metadata"] = _json_dumps(payload.get("metadata") or {})
    if update_row:
        _validate_source_payload(
            str(current.get("source_type") or "").strip().lower(),
            source_url=update_row.get("source_url", current.get("source_url")),
            raw_text=update_row.get("raw_text", current.get("raw_text")),
            storage_path=update_row.get("storage_path", current.get("storage_path")),
            config=config,
        )
        update_row["updated_at"] = _utcnow_iso()
        update_row["checksum"] = _sha256_text(
            str(update_row.get("source_url") or current.get("source_url") or "") + "\n"
            + str(update_row.get("raw_text") or current.get("raw_text") or "") + "\n"
            + str(update_row.get("storage_path") or current.get("storage_path") or "") + "\n"
            + str(update_row.get("title") or current.get("title") or "")
        )
        assignments = ", ".join(f"{key} = ?" for key in update_row)
        values = list(update_row.values()) + [str(source_id)]
        with _connect(config) as conn:
            conn.execute(f"UPDATE kb_sources SET {assignments} WHERE id = ?", values)
            conn.commit()
    _reset_cache("sources")
    return get_source(source_id, config=config) or {}


def delete_source(source_id: str | int, *, config: dict | None = None) -> bool:
    source = get_source(source_id, config=config)
    with _connect(config) as conn:
        conn.execute("DELETE FROM kb_sources WHERE id = ?", (str(source_id),))
        conn.commit()
    if source and source.get("storage_path"):
        path = _resolve_storage_path(str(source.get("storage_path")), config=config)
        try:
            if path.is_file() and _files_dir(config).resolve() in path.resolve().parents:
                path.unlink()
        except Exception:
            pass
    _reset_cache()
    rebuild_index(config=config)
    return True


def queue_job(
    *,
    source_id: str | int | None,
    source_type: str,
    job_type: str = "ingest",
    payload: dict[str, Any] | None = None,
    config: dict | None = None,
) -> dict[str, Any]:
    if job_type not in KB_JOB_TYPES:
        raise ValueError(f"Unsupported KB job_type: {job_type}")
    now = _utcnow_iso()
    with _connect(config) as conn:
        cur = conn.execute(
            """
            INSERT INTO kb_ingest_jobs(
                created_at, updated_at, source_id, source_type, job_type, status, payload, last_result
            )
            VALUES(?, ?, ?, ?, ?, 'pending', ?, '{}')
            """,
            (now, now, source_id, source_type, job_type, _json_dumps(payload or {})),
        )
        job_id = cur.lastrowid
        conn.commit()
    _reset_cache("jobs")
    return _get_job(job_id, config=config) or {}


def _get_job(job_id: str | int, *, config: dict | None = None) -> dict[str, Any] | None:
    with _connect(config) as conn:
        row = conn.execute("SELECT * FROM kb_ingest_jobs WHERE id = ? LIMIT 1", (str(job_id),)).fetchone()
    return _row_to_dict(row)


def list_jobs(limit: int = 100, *, config: dict | None = None) -> list[dict[str, Any]]:
    with _connect(config) as conn:
        rows = conn.execute("SELECT * FROM kb_ingest_jobs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    return [_row_to_dict(row) for row in rows if row is not None]


def _update_job(job_id: str | int, fields: dict[str, Any], *, config: dict | None = None) -> dict[str, Any] | None:
    if not fields:
        return _get_job(job_id, config=config)
    prepared: dict[str, Any] = {"updated_at": _utcnow_iso()}
    for key, value in fields.items():
        prepared[key] = _json_dumps(value) if key in {"payload", "last_result"} else value
    assignments = ", ".join(f"{key} = ?" for key in prepared)
    with _connect(config) as conn:
        conn.execute(f"UPDATE kb_ingest_jobs SET {assignments} WHERE id = ?", [*prepared.values(), str(job_id)])
        conn.commit()
    _reset_cache("jobs")
    return _get_job(job_id, config=config)


def _update_source_status(
    source_id: str | int,
    *,
    status: str,
    sync_error: str | None = None,
    metadata_patch: dict[str, Any] | None = None,
    last_synced_at: str | None = None,
    config: dict | None = None,
) -> dict[str, Any] | None:
    current = get_source(source_id, config=config)
    if not current:
        return None
    metadata = dict(current.get("metadata") or {})
    if metadata_patch:
        metadata.update(metadata_patch)
    fields: dict[str, Any] = {"status": status, "metadata": metadata}
    if sync_error is not None:
        fields["sync_error"] = sync_error
    if last_synced_at is not None:
        fields["last_synced_at"] = last_synced_at
    return update_source(source_id, fields, config=config)


def _update_sync_progress(
    source_id: str | int,
    *,
    phase: str,
    label: str,
    processed: int,
    total: int,
    item_type: str = "",
    job_id: str | int | None = None,
    config: dict | None = None,
) -> None:
    total_value = max(0, parse_int(total, 0))
    processed_value = max(0, min(parse_int(processed, 0), total_value if total_value else parse_int(processed, 0)))
    percent = int(round((processed_value / total_value) * 100)) if total_value > 0 else 0
    progress = {
        "phase": phase,
        "label": label,
        "processed": processed_value,
        "total": total_value,
        "percent": max(0, min(percent, 100)),
        "item_type": item_type,
        "updated_at": _utcnow_iso(),
    }
    if job_id is not None:
        progress["job_id"] = str(job_id)
    _update_source_status(source_id, status="syncing", metadata_patch={"sync_progress": progress}, config=config)
    if job_id is not None:
        _update_job(job_id, {"last_result": progress}, config=config)


def _upsert_document(
    *,
    source_id: str | int,
    external_id: str,
    document_type: str,
    title: str,
    body_text: str,
    metadata: dict[str, Any],
    config: dict | None = None,
) -> dict[str, Any]:
    now = _utcnow_iso()
    checksum = _sha256_text(body_text)
    with _connect(config) as conn:
        conn.execute(
            """
            INSERT INTO kb_documents(
                created_at, updated_at, source_id, external_id, document_type, title, body_text, checksum, metadata
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_id, external_id) DO UPDATE SET
                updated_at=excluded.updated_at,
                document_type=excluded.document_type,
                title=excluded.title,
                body_text=excluded.body_text,
                checksum=excluded.checksum,
                metadata=excluded.metadata
            """,
            (now, now, source_id, external_id, document_type, _safe_title(title), body_text, checksum, _json_dumps(metadata)),
        )
        row = conn.execute(
            "SELECT * FROM kb_documents WHERE source_id = ? AND external_id = ? LIMIT 1",
            (str(source_id), external_id),
        ).fetchone()
        conn.commit()
    result = _row_to_dict(row)
    if not result:
        raise RuntimeError(f"Unable to upsert KB document {external_id}.")
    return result


def _replace_document_chunks(
    *,
    source_id: str | int,
    document_id: str | int,
    title: str,
    content: str,
    metadata: dict[str, Any],
    config: dict | None = None,
) -> int:
    runtime = get_runtime_config(config)
    chunks = chunk_text(
        content,
        chunk_size=runtime["kb_chunk_size"],
        overlap=runtime["kb_chunk_overlap"],
        config=config,
    )
    with _connect(config) as conn:
        conn.execute("DELETE FROM kb_chunks WHERE document_id = ?", (str(document_id),))
        now = _utcnow_iso()
        for chunk in chunks:
            conn.execute(
                """
                INSERT INTO kb_chunks(
                    created_at, source_id, document_id, chunk_index, title, content, checksum,
                    token_count, embedding, metadata
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    now, source_id, document_id, chunk["chunk_index"], _safe_title(title),
                    chunk["content"], chunk["checksum"], chunk["token_count"],
                    _json_dumps(chunk["embedding"]), _json_dumps(metadata),
                ),
            )
        conn.commit()
    _reset_cache("chunks")
    _reset_cache("chunk_index")
    return len(chunks)


def _fetch_source_documents(source_id: str | int, *, config: dict | None = None) -> list[dict[str, Any]]:
    with _connect(config) as conn:
        rows = conn.execute("SELECT * FROM kb_documents WHERE source_id = ?", (str(source_id),)).fetchall()
    return [_row_to_dict(row) for row in rows if row is not None]


def _fetch_source_entities(source_id: str | int, *, config: dict | None = None) -> list[dict[str, Any]]:
    with _connect(config) as conn:
        rows = conn.execute("SELECT * FROM kb_structured_entities WHERE source_id = ?", (str(source_id),)).fetchall()
    return [_row_to_dict(row) for row in rows if row is not None]


def _upsert_structured_entity(source_id: str | int, entity: dict[str, Any], *, config: dict | None = None) -> dict[str, Any]:
    checksum = _entity_checksum(entity)
    embedding = embed_texts([entity.get("narrative_text") or entity["title"]], config=config, is_query=False)[0]
    now = _utcnow_iso()
    with _connect(config) as conn:
        conn.execute(
            """
            INSERT INTO kb_structured_entities(
                created_at, updated_at, source_id, entity_type, external_id, title, serial_no, project_name,
                status, location_text, bhk_text, price_text, possession_text, attributes, narrative_text,
                checksum, embedding, raw_payload, last_synced_at, is_active
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT(entity_type, external_id) DO UPDATE SET
                updated_at=excluded.updated_at,
                source_id=excluded.source_id,
                title=excluded.title,
                serial_no=excluded.serial_no,
                project_name=excluded.project_name,
                status=excluded.status,
                location_text=excluded.location_text,
                bhk_text=excluded.bhk_text,
                price_text=excluded.price_text,
                possession_text=excluded.possession_text,
                attributes=excluded.attributes,
                narrative_text=excluded.narrative_text,
                checksum=excluded.checksum,
                embedding=excluded.embedding,
                raw_payload=excluded.raw_payload,
                last_synced_at=excluded.last_synced_at,
                is_active=1
            """,
            (
                now, now, source_id, entity["entity_type"], entity["external_id"], entity["title"],
                entity.get("serial_no") or None, entity.get("project_name") or None,
                entity.get("status") or None, entity.get("location_text") or None,
                entity.get("bhk_text") or None, entity.get("price_text") or None,
                entity.get("possession_text") or None, _json_dumps(entity.get("attributes") or {}),
                entity.get("narrative_text") or "", checksum, _json_dumps(embedding),
                _json_dumps(entity.get("raw_payload") or {}), now,
            ),
        )
        row = conn.execute(
            "SELECT * FROM kb_structured_entities WHERE entity_type = ? AND external_id = ? LIMIT 1",
            (entity["entity_type"], entity["external_id"]),
        ).fetchone()
        conn.commit()
    _reset_cache("entities")
    result = _row_to_dict(row)
    if not result:
        raise RuntimeError(f"Unable to upsert KB structured entity {entity['external_id']}.")
    return result


def save_uploaded_file(filename: str, content: bytes, *, mime_type: str | None = None, config: dict | None = None) -> dict[str, Any]:
    if not filename:
        raise ValueError("File name is required.")
    _ensure_dirs(config)
    safe_name = re.sub(r"[^a-zA-Z0-9._-]", "_", filename)
    rel_dir = Path(datetime.now(timezone.utc).strftime("%Y/%m"))
    rel_path = rel_dir / f"{uuid.uuid4().hex}_{safe_name}"
    full_path = _files_dir(config) / rel_path
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_bytes(content)
    guessed_mime = mime_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
    return {
        "storage_bucket": "local-kb-files",
        "storage_path": rel_path.as_posix(),
        "source_url": rel_path.as_posix(),
        "mime_type": guessed_mime,
        "metadata": {"original_name": filename, "size_bytes": len(content), "local_path": rel_path.as_posix()},
    }


def _resolve_storage_path(storage_path: str, *, config: dict | None = None) -> Path:
    return _resolve_managed_storage_path(storage_path, config=config)


def _download_source_bytes(source: dict[str, Any], *, config: dict | None = None) -> bytes:
    path_text = str(source.get("storage_path") or "").strip()
    if path_text:
        path = _resolve_storage_path(path_text, config=config)
        if path.exists():
            return path.read_bytes()
    url = str(source.get("source_url") or "").strip()
    if url:
        parsed = urlparse(url)
        if parsed.scheme in {"http", "https"}:
            response = httpx.get(_validate_public_http_url(url), follow_redirects=True, timeout=35.0)
            response.raise_for_status()
            return response.content
        local_path = _resolve_storage_path(url, config=config)
        if local_path.exists():
            return local_path.read_bytes()
    raise RuntimeError("KB source is missing a local file path or downloadable URL.")


def _extract_pdf_text_from_bytes(content: bytes) -> str:
    if not content:
        return ""
    try:
        import pymupdf4llm

        text = pymupdf4llm.to_markdown(io.BytesIO(content))
        if str(text or "").strip():
            return str(text).strip()
    except Exception:
        pass
    try:
        import fitz

        doc = fitz.open(stream=content, filetype="pdf")
        parts = [page.get_text("text").strip() for page in doc if page.get_text("text").strip()]
        if parts:
            return "\n\n".join(parts).strip()
    except Exception:
        pass
    reader = PdfReader(io.BytesIO(content))
    parts: list[str] = []
    for page in reader.pages:
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        if text.strip():
            parts.append(text.strip())
    return "\n\n".join(parts).strip()


def _extract_url_text(url: str) -> str:
    response = httpx.get(_validate_public_http_url(url), follow_redirects=True, timeout=30.0)
    response.raise_for_status()
    extracted = trafilatura.extract(
        response.text,
        url=url,
        include_links=True,
        include_tables=True,
        favor_precision=True,
    )
    return str(extracted or "").strip()


def _ingest_single_source(source: dict[str, Any], *, config: dict | None = None) -> dict[str, Any]:
    source_type = str(source.get("source_type") or "").strip().lower()
    source_id = source["id"]
    title = _safe_title(source.get("title"), "Knowledge Source")
    content = ""
    metadata = dict(source.get("metadata") or {})
    document_type = source_type
    if source_type == "text_note":
        content = str(source.get("raw_text") or "").strip()
    elif source_type == "web_url":
        content = _extract_url_text(str(source.get("source_url") or "").strip())
    elif source_type == "pdf_upload":
        content = _extract_pdf_text_from_bytes(_download_source_bytes(source, config=config))
    else:
        raise RuntimeError(f"Unsupported KB ingest source type: {source_type}")

    if not content:
        raise RuntimeError(f"No text could be extracted from source {source_id}.")

    metadata.update(
        {
            "source_url": source.get("source_url"),
            "mime_type": source.get("mime_type"),
            "last_ingested_at": _utcnow_iso(),
            "character_count": len(content),
        }
    )
    document = _upsert_document(
        source_id=source_id,
        external_id=f"source:{source_id}",
        document_type=document_type,
        title=title,
        body_text=content,
        metadata=metadata,
        config=config,
    )
    chunk_count = _replace_document_chunks(
        source_id=source_id,
        document_id=document["id"],
        title=title,
        content=content,
        metadata={"source_type": source_type, "title": title, "source_url": source.get("source_url")},
        config=config,
    )
    finished_at = _utcnow_iso()
    update_source(source_id, {"status": "ready", "sync_error": "", "metadata": metadata, "last_synced_at": finished_at}, config=config)
    rebuild_index(config=config)
    return {"source_id": source_id, "document_id": document["id"], "chunk_count": chunk_count, "character_count": len(content)}


def _flatten_address(address: dict[str, Any] | None) -> str:
    if not isinstance(address, dict):
        return ""
    pieces = [
        address.get("subLocality"), address.get("locality"), address.get("community"),
        address.get("subCommunity"), address.get("district"), address.get("city"),
        address.get("state"), address.get("country"), address.get("postalCode"),
    ]
    return ", ".join(str(piece).strip() for piece in pieces if str(piece or "").strip())


def _format_currency(amount: Any, currency: str | None = None) -> str:
    if amount in (None, ""):
        return ""
    try:
        numeric = float(amount)
    except (TypeError, ValueError):
        return str(amount)
    prefix = f"{currency} " if currency else ""
    if numeric.is_integer():
        return f"{prefix}{int(numeric):,}"
    return f"{prefix}{numeric:,.2f}"


def _format_project_price(raw: dict[str, Any]) -> str:
    minimum = raw.get("minimumPrice")
    maximum = raw.get("maximumPrice")
    if minimum not in (None, "") and maximum not in (None, ""):
        return f"{_format_currency(minimum)} to {_format_currency(maximum)}"
    if minimum not in (None, ""):
        return f"From {_format_currency(minimum)}"
    if maximum not in (None, ""):
        return f"Up to {_format_currency(maximum)}"
    return ""


def _format_property_price(raw: dict[str, Any]) -> str:
    info = raw.get("monetaryInfo") or {}
    if not isinstance(info, dict):
        info = {}
    currency = info.get("currency")
    bits: list[str] = []
    expected = info.get("expectedPrice")
    if expected not in (None, ""):
        bits.append(f"Expected price {_format_currency(expected, currency)}")
    deposit = info.get("depositAmount")
    if deposit not in (None, ""):
        bits.append(f"Deposit {_format_currency(deposit, currency)}")
    maintenance = info.get("maintenanceCost")
    if maintenance not in (None, ""):
        bits.append(f"Maintenance {_format_currency(maintenance, currency)}")
    extra_deposit = raw.get("securityDepositAmount")
    if extra_deposit not in (None, ""):
        bits.append(f"Security deposit {_format_currency(extra_deposit, raw.get('securityDepositUnit'))}")
    return " | ".join(bits)


def _format_dimensions(raw: dict[str, Any]) -> str:
    dimension = raw.get("dimension") or {}
    if not isinstance(dimension, dict):
        return ""
    bits: list[str] = []
    for field, unit_field, label in [
        ("carpetArea", "carpetAreaUnit", "Carpet"),
        ("buildUpArea", "buildUpAreaUnit", "Built-up"),
        ("saleableArea", "saleableAreaUnit", "Saleable"),
        ("propertyArea", "propertyAreaUnit", "Property area"),
        ("area", "areaUnitUnit", "Area"),
    ]:
        amount = dimension.get(field)
        if amount in (None, ""):
            continue
        unit = str(dimension.get(unit_field) or "").strip()
        suffix = f" {unit}" if unit else ""
        bits.append(f"{label}: {amount}{suffix}")
    return " | ".join(bits)


def _format_bhk(raw: dict[str, Any]) -> str:
    bhk = raw.get("noOfBHK")
    if bhk in (None, ""):
        return ""
    bhk_type = str(raw.get("bhkType") or "").strip()
    suffix = f" ({bhk_type})" if bhk_type else ""
    return f"{bhk} BHK{suffix}"


def _collect_amenities(raw: dict[str, Any]) -> list[str]:
    result = []
    for item in raw.get("amenities") or []:
        if isinstance(item, dict):
            name = item.get("amenityDisplayName") or item.get("name")
            if name:
                result.append(str(name))
    return result


def _collect_attributes(raw: dict[str, Any]) -> list[str]:
    result = []
    for item in raw.get("attributes") or []:
        if isinstance(item, dict):
            label = str(item.get("attributeDisplayName") or item.get("name") or "").strip()
            value = str(item.get("value") or "").strip()
            if label and value:
                result.append(f"{label}: {value}")
            elif label:
                result.append(label)
    return result


def _collect_brochures(raw: dict[str, Any]) -> list[str]:
    result = []
    for item in raw.get("brochures") or []:
        if isinstance(item, dict):
            name = str(item.get("name") or "").strip()
            url = str(item.get("url") or "").strip()
            if name and url:
                result.append(f"{name} ({url})")
            elif url:
                result.append(url)
            elif name:
                result.append(name)
    return result


def _build_project_entity(raw: dict[str, Any]) -> dict[str, Any]:
    title = _safe_title(raw.get("name"), "Unnamed project")
    status_parts = [str(raw.get("status") or "").strip(), str(raw.get("currentStatus") or "").strip()]
    status = " / ".join(part for part in status_parts if part)
    rera_numbers = ", ".join(str(item).strip() for item in (raw.get("reraNumbers") or []) if str(item).strip())
    description = str(raw.get("description") or "").strip()
    notes = str(raw.get("notes") or "").strip()
    price_text = _format_project_price(raw)
    possession = str(raw.get("possessionDate") or "").strip()
    narrative_parts = [
        f"Project {title}",
        f"Status: {status}" if status else "",
        f"Price range: {price_text}" if price_text else "",
        f"Possession: {possession}" if possession else "",
        f"RERA: {rera_numbers}" if rera_numbers else "",
        description,
        notes,
    ]
    return {
        "entity_type": "project",
        "external_id": str(raw.get("id") or "").strip(),
        "title": title,
        "serial_no": str(raw.get("serialNo") or "").strip(),
        "project_name": title,
        "status": status,
        "location_text": "",
        "bhk_text": "",
        "price_text": price_text,
        "possession_text": possession,
        "attributes": _coerce_jsonable(
            {
                "certificates": raw.get("certificates"),
                "rera_numbers": raw.get("reraNumbers") or [],
                "total_flats": raw.get("totalFlats"),
                "total_blocks": raw.get("totalBlocks"),
                "total_floor": raw.get("totalFloor"),
                "area": raw.get("area"),
                "area_unit_id": raw.get("areaUnitId"),
                "facings": raw.get("facings") or [],
                "description": description,
                "notes": notes,
            }
        ),
        "narrative_text": "\n".join(part for part in narrative_parts if part).strip(),
        "raw_payload": _coerce_jsonable(raw),
    }


def _build_property_entity(raw: dict[str, Any]) -> dict[str, Any]:
    title = _safe_title(raw.get("title"), "Unnamed property")
    location_text = _flatten_address(raw.get("address"))
    project_name = str(raw.get("project") or "").strip()
    if not project_name:
        projects = raw.get("projects") or []
        if isinstance(projects, list):
            project_name = ", ".join(str(item).strip() for item in projects if str(item).strip())
    price_text = _format_property_price(raw)
    bhk_text = _format_bhk(raw)
    dimension_text = _format_dimensions(raw)
    amenities = _collect_amenities(raw)
    attributes = _collect_attributes(raw)
    brochures = _collect_brochures(raw)
    description = str(raw.get("aboutProperty") or "").strip()
    notes = str(raw.get("notes") or "").strip()
    possession = str(raw.get("possessionDate") or "").strip()
    narrative_parts = [
        f"Property {title}",
        f"Project: {project_name}" if project_name else "",
        f"Status: {raw.get('status')}" if raw.get("status") else "",
        f"BHK: {bhk_text}" if bhk_text else "",
        f"Price: {price_text}" if price_text else "",
        f"Dimensions: {dimension_text}" if dimension_text else "",
        f"Location: {location_text}" if location_text else "",
        f"Possession: {possession}" if possession else "",
        f"Amenities: {', '.join(amenities)}" if amenities else "",
        f"Attributes: {', '.join(attributes)}" if attributes else "",
        f"Brochures: {', '.join(brochures)}" if brochures else "",
        description,
        notes,
    ]
    return {
        "entity_type": "property",
        "external_id": str(raw.get("id") or "").strip(),
        "title": title,
        "serial_no": str(raw.get("serialNo") or "").strip(),
        "project_name": project_name,
        "status": str(raw.get("status") or "").strip(),
        "location_text": location_text,
        "bhk_text": bhk_text,
        "price_text": price_text,
        "possession_text": possession,
        "attributes": _coerce_jsonable(
            {
                "sale_type": raw.get("saleType"),
                "enquired_for": raw.get("enquiredFor"),
                "status": raw.get("status"),
                "property_type_id": raw.get("propertyTypeId"),
                "property_type": raw.get("propertyType"),
                "furnish_status": raw.get("furnishStatus"),
                "facing": raw.get("facing"),
                "amenities": amenities,
                "attributes": attributes,
                "brochures": brochures,
                "dimension_text": dimension_text,
                "about_property": description,
                "notes": notes,
                "links": raw.get("links") or [],
                "microsite_url": raw.get("micrositeURL"),
                "short_url": raw.get("shortUrl"),
            }
        ),
        "narrative_text": "\n".join(part for part in narrative_parts if part).strip(),
        "raw_payload": _coerce_jsonable(raw),
    }


def _entity_checksum(entity: dict[str, Any]) -> str:
    stable = {
        "title": entity.get("title"),
        "serial_no": entity.get("serial_no"),
        "project_name": entity.get("project_name"),
        "status": entity.get("status"),
        "location_text": entity.get("location_text"),
        "bhk_text": entity.get("bhk_text"),
        "price_text": entity.get("price_text"),
        "possession_text": entity.get("possession_text"),
        "narrative_text": entity.get("narrative_text"),
        "attributes": entity.get("attributes"),
    }
    return _sha256_text(json.dumps(stable, sort_keys=True, default=str))


def _format_entity_fact_block(entity: dict[str, Any]) -> str:
    lines = []
    entity_type = str(entity.get("entity_type") or "record").strip().title()
    title = str(entity.get("title") or "").strip()
    if title:
        lines.append(f"{entity_type}: {title}")
    if entity.get("serial_no"):
        lines.append(f"Serial: {entity.get('serial_no')}")
    if entity.get("project_name") and entity.get("project_name") != entity.get("title"):
        lines.append(f"Project: {entity.get('project_name')}")
    if entity.get("status"):
        lines.append(f"Status: {entity.get('status')}")
    if entity.get("location_text"):
        lines.append(f"Location: {entity.get('location_text')}")
    if entity.get("bhk_text"):
        lines.append(f"BHK: {entity.get('bhk_text')}")
    if entity.get("price_text"):
        lines.append(f"Price: {entity.get('price_text')}")
    if entity.get("possession_text"):
        lines.append(f"Possession: {entity.get('possession_text')}")
    attributes = entity.get("attributes") or {}
    if isinstance(attributes, dict):
        dimension_text = str(attributes.get("dimension_text") or "").strip()
        amenities = attributes.get("amenities") or []
        brochures = attributes.get("brochures") or []
        if dimension_text:
            lines.append(f"Dimensions: {dimension_text}")
        if amenities:
            lines.append(f"Amenities: {', '.join(str(item) for item in amenities[:8])}")
        if brochures:
            lines.append(f"Brochures: {', '.join(str(item) for item in brochures[:3])}")
    if entity.get("last_synced_at"):
        lines.append(f"Source: LeadRat CRM synced {entity.get('last_synced_at')}")
    return "\n".join(lines)


class LeadratClient:
    def __init__(self, *, tenant: str, api_key: str, secret_key: str, base_url: str = LEADRAT_BASE_URL) -> None:
        self.tenant = tenant.strip()
        self.api_key = api_key.strip()
        self.secret_key = secret_key.strip()
        self.base_url = base_url.rstrip("/")
        self._access_token = ""
        self._expires_at = datetime.min.replace(tzinfo=timezone.utc)

    def is_configured(self) -> bool:
        return bool(self.tenant and self.api_key and self.secret_key)

    def _auth_headers(self) -> dict[str, str]:
        return {
            "tenant": self.tenant,
            "Authorization": f"Bearer {self._get_access_token()}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _get_access_token(self) -> str:
        if self._access_token and _utcnow() < (self._expires_at - timedelta(minutes=2)):
            return self._access_token
        if not self.is_configured():
            raise RuntimeError("LeadRat credentials are incomplete.")
        with httpx.Client(timeout=20.0) as client:
            response = client.post(
                f"{self.base_url}/api/v1/authentication/token",
                headers={"tenant": self.tenant, "Content-Type": "application/json"},
                json={"apiKey": self.api_key, "secretKey": self.secret_key},
            )
            response.raise_for_status()
            payload = response.json()
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict) or not data.get("accessToken"):
            raise RuntimeError("LeadRat token response was missing accessToken.")
        self._access_token = str(data.get("accessToken"))
        expires_in = parse_int(data.get("expiresIn"), 3600)
        self._expires_at = _utcnow() + timedelta(seconds=max(60, expires_in))
        return self._access_token

    def _request(self, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        with httpx.Client(timeout=35.0) as client:
            response = client.get(f"{self.base_url}{path}", headers=self._auth_headers(), params=params)
            response.raise_for_status()
            payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("LeadRat returned a non-object response.")
        return payload

    def validate_connection(self) -> dict[str, Any]:
        token = self._get_access_token()
        return {"status": "ok", "token_preview": token[:12] + "..." if token else "", "expires_at": self._expires_at.isoformat()}

    def _fetch_all_pages(self, path: str, *, page_size: int = 100) -> list[dict[str, Any]]:
        page = 1
        items: list[dict[str, Any]] = []
        while True:
            payload = self._request(path, params={"PageNumber": page, "PageSize": page_size})
            page_items = payload.get("items") or []
            if not isinstance(page_items, list):
                page_items = []
            items.extend(item for item in page_items if isinstance(item, dict))
            total = parse_int(payload.get("totalCount"), len(items))
            if not page_items or len(items) >= total:
                break
            page += 1
        return items

    def fetch_projects(self) -> list[dict[str, Any]]:
        return self._fetch_all_pages("/api/v1/project")

    def fetch_properties(self) -> list[dict[str, Any]]:
        return self._fetch_all_pages("/api/v1/property")


def ensure_leadrat_source(config: dict | None = None) -> dict[str, Any]:
    runtime = get_runtime_config(config)
    existing = get_leadrat_source(config=runtime)
    metadata = {
        "tenant": runtime["leadrat_tenant"],
        "base_url": runtime["leadrat_base_url"],
        "sync_interval_minutes": runtime["leadrat_sync_interval_minutes"],
    }
    if existing:
        return update_source(existing["id"], {"title": "LeadRat CRM", "enabled": runtime["leadrat_enabled"], "metadata": metadata}, config=runtime)
    return create_source(
        {"source_type": "leadrat_crm", "title": "LeadRat CRM", "enabled": runtime["leadrat_enabled"], "metadata": metadata},
        queue_sync=False,
        config=runtime,
    )


def validate_leadrat_connection(config: dict | None = None) -> dict[str, Any]:
    runtime = get_runtime_config(config)
    client = LeadratClient(
        tenant=runtime["leadrat_tenant"],
        api_key=runtime["leadrat_api_key"],
        secret_key=runtime["leadrat_secret_key"],
        base_url=runtime["leadrat_base_url"],
    )
    if not client.is_configured():
        raise RuntimeError("LeadRat tenant, API key, and secret key are required.")
    result = client.validate_connection()
    ensure_leadrat_source(runtime)
    return result


def sync_leadrat_source(config: dict | None = None, *, job_id: str | int | None = None) -> dict[str, Any]:
    runtime = get_runtime_config(config)
    if not runtime["leadrat_enabled"]:
        raise RuntimeError("LeadRat sync is disabled in configuration.")
    client = LeadratClient(
        tenant=runtime["leadrat_tenant"],
        api_key=runtime["leadrat_api_key"],
        secret_key=runtime["leadrat_secret_key"],
        base_url=runtime["leadrat_base_url"],
    )
    if not client.is_configured():
        raise RuntimeError("LeadRat tenant, API key, and secret key are required.")

    source = ensure_leadrat_source(runtime)
    source_id = source["id"]
    update_source(source_id, {"status": "syncing", "sync_error": ""}, config=config)
    started_at = _utcnow_iso()
    _update_sync_progress(source_id, phase="fetch_projects", label="Fetching projects from LeadRat...", processed=0, total=1, item_type="projects", job_id=job_id, config=config)
    projects = client.fetch_projects()
    _update_sync_progress(source_id, phase="fetch_properties", label="Fetching properties from LeadRat...", processed=0, total=1, item_type="properties", job_id=job_id, config=config)
    properties = client.fetch_properties()

    current_docs = {row["external_id"]: row for row in _fetch_source_documents(source_id, config=config)}
    current_entities = {f"{row.get('entity_type')}:{row.get('external_id')}": row for row in _fetch_source_entities(source_id, config=config)}
    seen_doc_ids: set[str] = set()
    seen_entity_ids: set[str] = set()
    entity_count = 0
    chunk_count = 0

    for records, builder, entity_type in [(projects, _build_project_entity, "project"), (properties, _build_property_entity, "property")]:
        total = len(records)
        processed = 0
        if total:
            _update_sync_progress(source_id, phase=f"{entity_type}s", label=f"Indexing {entity_type} records...", processed=0, total=total, item_type=f"{entity_type}s", job_id=job_id, config=config)
        for raw in records:
            entity = builder(raw)
            if not entity.get("external_id"):
                continue
            entity_row = _upsert_structured_entity(source_id, entity, config=config)
            entity_count += 1
            seen_entity_ids.add(f"{entity_type}:{entity['external_id']}")
            external_doc_id = f"{entity_type}:{entity['external_id']}"
            document = _upsert_document(
                source_id=source_id,
                external_id=external_doc_id,
                document_type=f"leadrat_{entity_type}",
                title=entity["title"],
                body_text=entity["narrative_text"],
                metadata={"source_type": "leadrat_crm", "entity_type": entity_type, "entity_id": entity_row["id"]},
                config=config,
            )
            seen_doc_ids.add(external_doc_id)
            if current_docs.get(external_doc_id, {}).get("checksum") != document.get("checksum"):
                chunk_count += _replace_document_chunks(
                    source_id=source_id,
                    document_id=document["id"],
                    title=entity["title"],
                    content=entity["narrative_text"],
                    metadata={"source_type": "leadrat_crm", "entity_type": entity_type, "external_id": entity["external_id"]},
                    config=config,
                )
            processed += 1
            if total and (processed == total or processed % 25 == 0):
                _update_sync_progress(source_id, phase=f"{entity_type}s", label=f"Indexing {entity_type} records...", processed=processed, total=total, item_type=f"{entity_type}s", job_id=job_id, config=config)

    with _connect(config) as conn:
        for external_id, row in current_docs.items():
            if external_id not in seen_doc_ids:
                conn.execute("DELETE FROM kb_documents WHERE id = ?", (str(row["id"]),))
        for external_key, row in current_entities.items():
            if external_key not in seen_entity_ids:
                conn.execute("UPDATE kb_structured_entities SET is_active = 0, updated_at = ? WHERE id = ?", (_utcnow_iso(), str(row["id"])))
        conn.commit()

    finished_at = _utcnow_iso()
    update_source(
        source_id,
        {
            "status": "ready",
            "sync_error": "",
            "last_synced_at": finished_at,
            "metadata": {
                **(source.get("metadata") or {}),
                "tenant": runtime["leadrat_tenant"],
                "base_url": runtime["leadrat_base_url"],
                "projects_count": len(projects),
                "properties_count": len(properties),
                "last_sync_started_at": started_at,
                "last_sync_finished_at": finished_at,
                "sync_progress": {
                    "phase": "completed",
                    "label": "LeadRat sync completed.",
                    "processed": len(projects) + len(properties),
                    "total": len(projects) + len(properties),
                    "percent": 100,
                    "item_type": "records",
                    "updated_at": finished_at,
                    **({"job_id": str(job_id)} if job_id is not None else {}),
                },
            },
        },
        config=config,
    )
    _reset_cache()
    rebuild_index(config=config)
    return {
        "source_id": source_id,
        "projects_count": len(projects),
        "properties_count": len(properties),
        "entity_count": entity_count,
        "chunk_count": chunk_count,
        "started_at": started_at,
        "finished_at": finished_at,
    }


def process_pending_jobs(config: dict | None = None, *, limit: int = 5) -> list[dict[str, Any]]:
    with _connect(config) as conn:
        rows = conn.execute(
            "SELECT * FROM kb_ingest_jobs WHERE status IN ('pending', 'failed') ORDER BY created_at LIMIT ?",
            (limit,),
        ).fetchall()
    jobs = [_row_to_dict(row) for row in rows if row is not None]
    processed: list[dict[str, Any]] = []
    for job in jobs:
        job_id = job["id"]
        _update_job(job_id, {"status": "processing", "started_at": _utcnow_iso(), "attempts": parse_int(job.get("attempts"), 0) + 1, "error_text": ""}, config=config)
        try:
            if str(job.get("source_type") or "").strip().lower() == "leadrat_crm":
                result = sync_leadrat_source(config, job_id=job_id)
            else:
                source = get_source(job.get("source_id"), config=config)
                if not source:
                    raise RuntimeError(f"KB source {job.get('source_id')} was not found.")
                result = _ingest_single_source(source, config=config)
            _update_job(job_id, {"status": "completed", "finished_at": _utcnow_iso(), "last_result": result}, config=config)
            processed.append({"job_id": job_id, "status": "completed", "result": result})
        except Exception as exc:
            logger.error(f"KB job {job_id} failed: {exc}")
            _update_job(job_id, {"status": "failed", "finished_at": _utcnow_iso(), "error_text": str(exc)}, config=config)
            if job.get("source_id"):
                _update_source_status(job["source_id"], status="error", sync_error=str(exc), config=config)
            processed.append({"job_id": job_id, "status": "failed", "error": str(exc)})
    return processed


def maybe_sync_leadrat(config: dict | None = None) -> dict[str, Any] | None:
    runtime = get_runtime_config(config)
    if not runtime["leadrat_enabled"]:
        return None
    source = ensure_leadrat_source(runtime)
    last_synced_at = str(source.get("last_synced_at") or "").strip()
    if last_synced_at:
        try:
            last_dt = datetime.fromisoformat(last_synced_at.replace("Z", "+00:00"))
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            if _utcnow() - last_dt < timedelta(minutes=runtime["leadrat_sync_interval_minutes"]):
                return None
        except Exception:
            pass
    pending = [
        job for job in list_jobs(limit=50, config=runtime)
        if str(job.get("source_type") or "") == "leadrat_crm" and str(job.get("status") or "") in {"pending", "processing"}
    ]
    if pending:
        return pending[0]
    return queue_job(source_id=source["id"], source_type="leadrat_crm", job_type="sync", payload={}, config=runtime)


def _preprocess_entity_row(row: dict[str, Any]) -> dict[str, Any]:
    narrative = str(row.get("narrative_text") or "").strip()
    search_text = " ".join(
        str(part or "")
        for part in [
            row.get("title"), row.get("serial_no"), row.get("project_name"), row.get("status"),
            row.get("location_text"), row.get("bhk_text"), row.get("price_text"), row.get("possession_text"), narrative,
        ]
    ).strip()
    row["_search_text"] = search_text
    row["_search_norm"] = _normalize_text(search_text)
    row["_tokens"] = _tokenize_keywords(search_text)
    row["_embedding"] = _coerce_embedding(row.get("embedding")) or build_embedding(search_text)
    row["_fact_block"] = _format_entity_fact_block(row)
    return row


def _preprocess_chunk_row(row: dict[str, Any]) -> dict[str, Any]:
    metadata = row.get("metadata") or {}
    search_text = " ".join(str(part or "") for part in [row.get("title"), row.get("content"), metadata.get("source_url")]).strip()
    row["_search_text"] = search_text
    row["_search_norm"] = _normalize_text(search_text)
    row["_tokens"] = _tokenize_keywords(search_text)
    row["_embedding"] = _coerce_embedding(row.get("embedding")) or build_embedding(search_text)
    return row


def _load_entities(config: dict | None = None) -> list[dict[str, Any]]:
    runtime = get_runtime_config(config)

    def fetcher() -> list[dict[str, Any]]:
        with _connect(config) as conn:
            rows = conn.execute("SELECT * FROM kb_structured_entities WHERE is_active = 1 ORDER BY updated_at DESC LIMIT 50000").fetchall()
        return [_preprocess_entity_row(_row_to_dict(row) or {}) for row in rows]

    return _fetch_cached_rows("entities", fetcher, ttl=runtime["kb_cache_ttl_seconds"])


def _load_chunks(config: dict | None = None) -> list[dict[str, Any]]:
    runtime = get_runtime_config(config)

    def fetcher() -> list[dict[str, Any]]:
        with _connect(config) as conn:
            rows = conn.execute("SELECT * FROM kb_chunks ORDER BY created_at DESC LIMIT 150000").fetchall()
        return [_preprocess_chunk_row(_row_to_dict(row) or {}) for row in rows]

    return _fetch_cached_rows("chunks", fetcher, ttl=runtime["kb_cache_ttl_seconds"])


def _build_chunk_index(rows: list[dict[str, Any]], *, config: dict | None = None) -> dict[str, Any]:
    vectors: list[list[float]] = []
    ids: list[int] = []
    for row in rows:
        embedding = row.get("_embedding") or []
        if embedding:
            vectors.append(embedding)
            ids.append(int(row["id"]))
    matrix = np.asarray(vectors, dtype=np.float32) if vectors else np.zeros((0, get_runtime_config(config)["kb_embedding_dimensions"]), dtype=np.float32)
    faiss_index = None
    if len(matrix):
        try:
            import faiss

            faiss_index = faiss.IndexIDMap2(faiss.IndexFlatIP(matrix.shape[1]))
            faiss_index.add_with_ids(matrix, np.asarray(ids, dtype=np.int64))
        except Exception as exc:
            logger.info(f"[KB] FAISS unavailable; using numpy vector search fallback: {exc}")
            faiss_index = None
    avgdl = 0.0
    doc_freq: Counter[str] = Counter()
    tokenized: dict[int, list[str]] = {}
    for row in rows:
        tokens = row.get("_tokens") or []
        tokenized[int(row["id"])] = tokens
        avgdl += len(tokens)
        for token in set(tokens):
            doc_freq[token] += 1
    avgdl = avgdl / len(rows) if rows else 0.0
    return {"rows": rows, "by_id": {int(row["id"]): row for row in rows}, "ids": ids, "matrix": matrix, "faiss": faiss_index, "bm25": {"avgdl": avgdl, "doc_freq": doc_freq, "tokenized": tokenized}}


def _load_chunk_index(config: dict | None = None) -> dict[str, Any]:
    runtime = get_runtime_config(config)
    now = time.monotonic()
    entry = _CACHE["chunk_index"]
    if entry["items"] is not None and (now - entry["at"]) < runtime["kb_cache_ttl_seconds"]:
        return entry["items"]
    rows = _load_chunks(config)
    index = _build_chunk_index(rows, config=config)
    _CACHE["chunk_index"] = {"at": now, "items": index}
    return index


def rebuild_index(config: dict | None = None) -> dict[str, Any]:
    runtime = get_runtime_config(config)
    rows = _load_chunks(config)
    index = _build_chunk_index(rows, config=config)
    vector_count = int(len(index["ids"]))
    snapshot = {
        "rebuilt_at": _utcnow_iso(),
        "backend": runtime["kb_backend"],
        "index_kind": runtime["kb_index_kind"],
        "embedding_provider": runtime["kb_embedding_provider"],
        "embedding_model": runtime["kb_embedding_model"],
        "vector_count": vector_count,
        "faiss_available": index.get("faiss") is not None,
    }
    try:
        if index.get("faiss") is not None:
            import faiss

            path = _indexes_dir(config) / "chunks.faiss"
            faiss.write_index(index["faiss"], str(path))
            snapshot["faiss_path"] = str(path)
        (_indexes_dir(config) / "manifest.json").write_text(_json_dumps(snapshot), encoding="utf-8")
    except Exception as exc:
        logger.warning(f"[KB] Failed to persist index snapshot: {exc}")
    _set_meta("index_status", snapshot, config=config)
    _CACHE["chunk_index"] = {"at": time.monotonic(), "items": index}
    return snapshot


def is_inventory_query(query: str) -> bool:
    normalized = _normalize_text(query)
    if not normalized:
        return False
    tokens = set(_tokenize_keywords(normalized))
    if tokens & KB_QUERY_HINTS:
        return True
    return bool(re.search(r"\b\d+\s*bhk\b", normalized))


def is_kb_query(query: str) -> bool:
    normalized = _normalize_text(query)
    if not normalized:
        return False
    tokens = set(_tokenize_keywords(normalized))
    return bool(tokens & (KB_QUERY_HINTS | KB_TEXT_HINTS))


def search_inventory(query: str, *, limit: int = 3, config: dict | None = None) -> list[dict[str, Any]]:
    query_norm = _normalize_text(query)
    query_tokens = _tokenize_keywords(query)
    query_embedding = embed_texts([query], config=config, is_query=True)[0]
    scored: list[tuple[float, dict[str, Any]]] = []
    for row in _load_entities(config):
        score = 0.0
        title_norm = _normalize_text(row.get("title"))
        serial_norm = _normalize_text(row.get("serial_no"))
        project_norm = _normalize_text(row.get("project_name"))
        if query_norm and title_norm and query_norm == title_norm:
            score += 5.0
        elif query_norm and title_norm and query_norm in title_norm:
            score += 3.2
        if serial_norm and serial_norm in query_norm:
            score += 6.0
        if project_norm and project_norm in query_norm:
            score += 1.8
        score += _keyword_overlap_score(query_tokens, row["_tokens"]) * 3.6
        score += max(0.0, _cosine_similarity(query_embedding, row["_embedding"])) * 2.6
        if score >= 0.85:
            scored.append((score, row))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [
        {
            "score": round(score, 4),
            "entity_type": row.get("entity_type"),
            "title": row.get("title"),
            "serial_no": row.get("serial_no"),
            "project_name": row.get("project_name"),
            "status": row.get("status"),
            "location_text": row.get("location_text"),
            "bhk_text": row.get("bhk_text"),
            "price_text": row.get("price_text"),
            "possession_text": row.get("possession_text"),
            "fact_block": row.get("_fact_block"),
            "source": "leadrat_crm",
            "last_synced_at": row.get("last_synced_at"),
        }
        for score, row in scored[:limit]
    ]


def _bm25_scores(query_tokens: list[str], index: dict[str, Any]) -> dict[int, float]:
    rows = index["rows"]
    if not rows or not query_tokens:
        return {}
    bm25 = index["bm25"]
    doc_freq: Counter[str] = bm25["doc_freq"]
    tokenized: dict[int, list[str]] = bm25["tokenized"]
    avgdl = float(bm25["avgdl"] or 0.0)
    n_docs = len(rows)
    k1 = 1.5
    b = 0.75
    scores: dict[int, float] = {}
    for doc_id, tokens in tokenized.items():
        if not tokens:
            continue
        counts = Counter(tokens)
        dl = len(tokens)
        score = 0.0
        for token in query_tokens:
            tf = counts.get(token, 0)
            if tf <= 0:
                continue
            df = doc_freq.get(token, 0)
            idf = math.log(1 + (n_docs - df + 0.5) / (df + 0.5))
            denom = tf + k1 * (1 - b + b * (dl / avgdl if avgdl else 1.0))
            score += idf * ((tf * (k1 + 1)) / denom)
        if score > 0:
            scores[doc_id] = score
    return scores


def _dense_candidate_scores(query_embedding: list[float], index: dict[str, Any], *, limit: int) -> dict[int, float]:
    if not query_embedding or not index["ids"]:
        return {}
    query_vector = np.asarray([query_embedding], dtype=np.float32)
    scores: dict[int, float] = {}
    faiss_index = index.get("faiss")
    if faiss_index is not None:
        distances, ids = faiss_index.search(query_vector, min(max(limit, 20), len(index["ids"])))
        for score, doc_id in zip(distances[0], ids[0]):
            if int(doc_id) >= 0:
                scores[int(doc_id)] = float(score)
        return scores
    matrix = index["matrix"]
    if not len(matrix):
        return {}
    sims = matrix @ query_vector[0]
    top_n = min(max(limit, 20), len(sims))
    top_idx = np.argpartition(-sims, top_n - 1)[:top_n] if top_n < len(sims) else np.arange(len(sims))
    for idx in top_idx:
        scores[int(index["ids"][idx])] = float(sims[idx])
    return scores


def search_chunks(query: str, *, limit: int = 4, config: dict | None = None) -> list[dict[str, Any]]:
    query_norm = _normalize_text(query)
    query_tokens = _tokenize_keywords(query)
    query_embedding = embed_texts([query], config=config, is_query=True)[0]
    index = _load_chunk_index(config)
    dense_scores = _dense_candidate_scores(query_embedding, index, limit=limit * 8)
    bm25_scores = _bm25_scores(query_tokens, index)
    candidate_ids = set(dense_scores) | set(sorted(bm25_scores, key=bm25_scores.get, reverse=True)[: limit * 12])
    scored: list[tuple[float, dict[str, Any]]] = []
    for doc_id in candidate_ids:
        row = index["by_id"].get(int(doc_id))
        if not row:
            continue
        score = 0.0
        if query_norm and row["_search_norm"] and query_norm in row["_search_norm"]:
            score += 1.8
        score += _keyword_overlap_score(query_tokens, row["_tokens"]) * 2.6
        score += max(0.0, dense_scores.get(doc_id, 0.0)) * 2.2
        score += min(3.0, bm25_scores.get(doc_id, 0.0)) * 0.75
        if score >= 0.65:
            scored.append((score, row))
    scored.sort(key=lambda item: item[0], reverse=True)
    results = []
    for score, row in scored[:limit]:
        metadata = row.get("metadata") or {}
        results.append(
            {
                "score": round(score, 4),
                "title": row.get("title"),
                "content": row.get("content"),
                "preview": _preview(row.get("content"), limit=340),
                "source_type": metadata.get("source_type") or "unknown",
                "source_url": metadata.get("source_url"),
                "entity_type": metadata.get("entity_type"),
            }
        )
    return results


def search_hybrid(query: str, *, config: dict | None = None) -> dict[str, Any]:
    runtime = get_runtime_config(config)
    inventory_hits = search_inventory(query, limit=runtime["kb_inventory_top_k"], config=config)
    descriptive_query = bool(set(_tokenize_keywords(query)) & KB_TEXT_HINTS)
    chunk_hits = search_chunks(query, limit=runtime["kb_top_k"], config=config) if descriptive_query or not inventory_hits else []
    return {"query": query, "inventory_hits": inventory_hits, "chunk_hits": chunk_hits}


def build_grounding_text(query: str, *, config: dict | None = None) -> dict[str, Any] | None:
    runtime = get_runtime_config(config)
    if not runtime["kb_enabled"]:
        return None
    if not is_kb_query(query) and not is_inventory_query(query):
        return None

    results = search_hybrid(query, config=config)
    inventory_hits = results["inventory_hits"]
    chunk_hits = results["chunk_hits"]
    if not inventory_hits and not chunk_hits:
        return {
            "query": query,
            "inventory_hits": [],
            "chunk_hits": [],
            "grounding_text": (
                "Knowledge base rule: this looks like a knowledge/inventory question, but there is no confirmed "
                "match in the CRM or KB excerpts for this turn. Do not guess. Say you do not have confirmed information."
            ),
        }

    parts = [
        "Knowledge base grounding rules:",
        "1. Prefer LeadRat CRM facts over PDFs, URLs, or notes if they differ.",
        "2. Use only the confirmed facts below for this turn.",
        "3. If the exact fact is missing below, say you do not have confirmed information.",
        "",
    ]
    if inventory_hits:
        parts.append("LeadRat CRM results:")
        for index, item in enumerate(inventory_hits, start=1):
            parts.append(f"{index}. {item['fact_block']}")
        parts.append("")
    if chunk_hits:
        parts.append("Knowledge excerpts:")
        for index, item in enumerate(chunk_hits, start=1):
            label = f"[{item.get('source_type')}]"
            title = f"{item.get('title')}: " if item.get("title") else ""
            parts.append(f"{index}. {label} {title}{item.get('preview')}")
    grounding_text = "\n".join(part for part in parts if part is not None).strip()
    if len(grounding_text) > runtime["kb_context_char_budget"]:
        grounding_text = grounding_text[: runtime["kb_context_char_budget"]].rstrip() + "..."
    return {"query": query, "inventory_hits": inventory_hits, "chunk_hits": chunk_hits, "grounding_text": grounding_text}


def search_for_agent(query: str, *, config: dict | None = None) -> dict[str, Any] | None:
    return build_grounding_text(query, config=config)


def get_status(config: dict | None = None) -> dict[str, Any]:
    runtime = get_runtime_config(config)
    try:
        sources = list_sources(limit=200, config=config)
        jobs = list_jobs(limit=200, config=config)
        entities = _load_entities(config)
        chunks = _load_chunks(config)
        index_status = _get_meta("index_status", {}, config=config)
    except Exception as exc:
        return kb_runtime_issue_payload(exc, config=config)

    leadrat_source = next((row for row in sources if row.get("source_type") == "leadrat_crm"), None)
    index_status = index_status or {}
    return {
        "status": "ok",
        "kb_enabled": runtime["kb_enabled"],
        "backend": runtime["kb_backend"],
        "runtime": "Local FAISS + SQLite",
        "embedding_provider": runtime["kb_embedding_provider"],
        "embedding_model": runtime["kb_embedding_model"],
        "index_kind": runtime["kb_index_kind"],
        "data_dir": str(_data_dir(config)),
        "index_status": index_status,
        "vector_count": index_status.get("vector_count", len(chunks)),
        "last_rebuild_at": index_status.get("rebuilt_at"),
        "counts": {"sources": len(sources), "jobs": len(jobs), "entities": len(entities), "chunks": len(chunks)},
        "leadrat": {
            "enabled": runtime["leadrat_enabled"],
            "tenant": runtime["leadrat_tenant"],
            "sync_interval_minutes": runtime["leadrat_sync_interval_minutes"],
            "source": leadrat_source,
            "connected": bool(leadrat_source and leadrat_source.get("status") == "ready"),
            "last_sync": leadrat_source.get("last_synced_at") if leadrat_source else None,
            "records": len(entities),
        },
    }
