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
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")

import httpx
import numpy as np
import tiktoken
import trafilatura
from pypdf import PdfReader

logger = logging.getLogger("kb")

KB_SOURCE_TYPES = {"pdf_upload", "web_url"}
KB_JOB_STATUSES = {"pending", "processing", "completed", "failed", "cancelled"}
KB_JOB_TYPES = {"ingest", "reindex"}
KB_CACHE_TTL_SECONDS = 45
KB_DEFAULT_EMBED_DIMENSIONS = 384
KB_DEFAULT_EMBED_MODEL = "BAAI/bge-small-en-v1.5"
KB_MAX_SITEMAP_URLS = 250
KB_MAX_SITEMAPS = 25

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
    "chunk_index": {"at": 0.0, "items": None},
}

KB_QUERY_HINTS = {
    "price", "pricing", "cost", "plan", "plans", "feature", "features", "service",
    "services", "product", "products", "availability", "available", "status",
    "location", "address", "hours", "timing", "policy", "support", "setup",
    "integration", "integrations", "demo", "booking", "appointment", "contact",
    "refund", "shipping", "delivery", "warranty", "terms",
}
KB_TEXT_HINTS = {
    "about", "details", "overview", "features", "describe", "document", "pdf",
    "website", "link", "explain", "guide", "brochure", "page", "sitemap",
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
        "kb_paid_embedding_fallback_enabled": parse_bool(get_value("kb_paid_embedding_fallback_enabled", "KB_PAID_EMBEDDING_FALLBACK_ENABLED", False), False),
        "kb_embedding_fallback_model": str(get_value("kb_embedding_fallback_model", "KB_EMBEDDING_FALLBACK_MODEL", "gemini-embedding-001") or "gemini-embedding-001").strip(),
        "kb_embedding_dimensions": max(16, parse_int(get_value("kb_embedding_dimensions", "KB_EMBEDDING_DIMENSIONS", KB_DEFAULT_EMBED_DIMENSIONS), KB_DEFAULT_EMBED_DIMENSIONS)),
        "google_api_key": str(get_value("google_api_key", "GOOGLE_API_KEY", "") or "").strip(),
        "kb_index_kind": str(get_value("kb_index_kind", "KB_INDEX_KIND", "flat_ip") or "flat_ip").strip().lower(),
        "kb_rerank_enabled": parse_bool(get_value("kb_rerank_enabled", "KB_RERANK_ENABLED", False), False),
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
    path_text = str(storage_path or "").strip()

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

        DELETE FROM kb_ingest_jobs WHERE source_type NOT IN ('pdf_upload', 'web_url');
        DELETE FROM kb_sources WHERE source_type NOT IN ('pdf_upload', 'web_url');
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
    paid_fallback_enabled = bool(runtime["kb_paid_embedding_fallback_enabled"])
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
        if fallback_provider == "gemini" and paid_fallback_enabled:
            gemini_vectors = _embed_texts_gemini(texts, config=config, is_query=is_query)
            if gemini_vectors is not None:
                return gemini_vectors
    if provider == "gemini" or (provider == "api" and fallback_provider == "gemini" and paid_fallback_enabled):
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
        "counts": {"sources": 0, "jobs": 0, "chunks": 0},
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
    aliases = {"url": "web_url", "web": "web_url", "website": "web_url", "sitemap": "web_url", "pdf": "pdf_upload"}
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
            job_type="ingest",
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


def _fetch_url_response(url: str, *, timeout: float = 30.0) -> httpx.Response:
    response = httpx.get(_validate_public_http_url(url), follow_redirects=True, timeout=timeout)
    response.raise_for_status()
    return response


def _extract_text_from_html(html: str, *, url: str) -> str:
    extracted = trafilatura.extract(
        html,
        url=url,
        include_links=True,
        include_tables=True,
        favor_precision=True,
    )
    return str(extracted or "").strip()


def _html_title(html: str, fallback_url: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", html or "", flags=re.IGNORECASE | re.DOTALL)
    if match:
        title = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", match.group(1))).strip()
        if title:
            return _safe_title(title, _title_from_web_url(fallback_url))
    return _title_from_web_url(fallback_url)


def _title_from_web_url(url: str) -> str:
    parsed = urlparse(str(url or ""))
    path = parsed.path.strip("/")
    if not path:
        return parsed.netloc or "Website page"
    leaf = path.rsplit("/", 1)[-1] or path
    leaf = re.sub(r"\.[a-zA-Z0-9]{1,8}$", "", leaf)
    leaf = re.sub(r"[-_]+", " ", leaf).strip()
    return _safe_title(leaf.title() if leaf else parsed.netloc, "Website page")


def _xml_tag_name(tag: str) -> str:
    return str(tag or "").rsplit("}", 1)[-1].lower()


def _same_site_url(candidate: str, root_url: str) -> bool:
    candidate_host = (urlparse(candidate).hostname or "").lower().removeprefix("www.")
    root_host = (urlparse(root_url).hostname or "").lower().removeprefix("www.")
    return bool(candidate_host and root_host and candidate_host == root_host)


def _is_sitemap_response(url: str, response: httpx.Response) -> bool:
    content_type = response.headers.get("content-type", "").lower()
    text = response.text.lstrip()[:500].lower()
    parsed_path = urlparse(str(response.url or url)).path.lower()
    return (
        "xml" in content_type
        or parsed_path.endswith(".xml")
        or parsed_path.endswith(".xml.gz")
        or "<urlset" in text
        or "<sitemapindex" in text
    )


def _parse_sitemap_locs(xml_text: str, *, base_url: str) -> tuple[list[str], list[str]]:
    try:
        root = ET.fromstring(xml_text.encode("utf-8"))
    except Exception as exc:
        raise RuntimeError(f"Unable to parse sitemap XML: {exc}") from exc

    root_tag = _xml_tag_name(root.tag)
    sitemap_urls: list[str] = []
    page_urls: list[str] = []
    if root_tag == "sitemapindex":
        target = sitemap_urls
    elif root_tag == "urlset":
        target = page_urls
    else:
        target = page_urls

    for loc in root.findall(".//{*}loc"):
        value = str(loc.text or "").strip()
        if not value:
            continue
        absolute_url = _validate_public_http_url(urljoin(base_url, value))
        if _same_site_url(absolute_url, base_url):
            target.append(absolute_url)
    return sitemap_urls, page_urls


def _dedupe_urls(urls: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for url in urls:
        clean = str(url or "").strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        result.append(clean)
    return result


def _collect_sitemap_page_urls(sitemap_url: str) -> list[str]:
    root_url = _validate_public_http_url(sitemap_url)
    pending = [root_url]
    seen_sitemaps: set[str] = set()
    page_urls: list[str] = []

    while pending and len(seen_sitemaps) < KB_MAX_SITEMAPS and len(page_urls) < KB_MAX_SITEMAP_URLS:
        current = pending.pop(0)
        if current in seen_sitemaps:
            continue
        seen_sitemaps.add(current)
        response = _fetch_url_response(current, timeout=35.0)
        nested_sitemaps, nested_pages = _parse_sitemap_locs(response.text, base_url=current)
        pending.extend(url for url in _dedupe_urls(nested_sitemaps) if url not in seen_sitemaps)
        for page_url in _dedupe_urls(nested_pages):
            if len(page_urls) >= KB_MAX_SITEMAP_URLS:
                break
            if page_url not in page_urls:
                page_urls.append(page_url)

    return page_urls


def _extract_web_documents(source_url: str) -> list[dict[str, Any]]:
    response = _fetch_url_response(source_url)
    final_url = str(response.url)
    documents: list[dict[str, Any]] = []

    if _is_sitemap_response(source_url, response):
        page_urls = _collect_sitemap_page_urls(final_url)
        for page_url in page_urls:
            try:
                page_response = _fetch_url_response(page_url)
                page_text = _extract_text_from_html(page_response.text, url=str(page_response.url))
            except Exception as exc:
                logger.warning(f"[KB] Failed to crawl sitemap page {page_url}: {exc}")
                continue
            if not page_text:
                continue
            page_final_url = str(page_response.url)
            documents.append(
                {
                    "external_id": f"url:{page_final_url}",
                    "title": _html_title(page_response.text, page_final_url),
                    "body_text": page_text,
                    "metadata": {"source_url": page_final_url, "sitemap_url": final_url},
                }
            )
        return documents

    page_text = _extract_text_from_html(response.text, url=final_url)
    if page_text:
        documents.append(
            {
                "external_id": f"url:{final_url}",
                "title": _html_title(response.text, final_url),
                "body_text": page_text,
                "metadata": {"source_url": final_url},
            }
        )
    return documents


def _ingest_documents_for_source(
    source: dict[str, Any],
    documents: list[dict[str, Any]],
    *,
    source_metadata: dict[str, Any],
    config: dict | None = None,
) -> dict[str, Any]:
    source_id = source["id"]
    source_type = str(source.get("source_type") or "").strip().lower()
    current_docs = {str(row.get("external_id")): row for row in _fetch_source_documents(source_id, config=config)}
    seen_external_ids: set[str] = set()
    document_ids: list[Any] = []
    chunk_count = 0
    character_count = 0

    for item in documents:
        content = str(item.get("body_text") or "").strip()
        if not content:
            continue
        external_id = str(item.get("external_id") or f"source:{source_id}:{len(seen_external_ids)}").strip()
        if external_id in seen_external_ids:
            continue
        seen_external_ids.add(external_id)
        title = _safe_title(item.get("title"), _safe_title(source.get("title"), "Knowledge Source"))
        item_metadata = {**source_metadata, **dict(item.get("metadata") or {})}
        document = _upsert_document(
            source_id=source_id,
            external_id=external_id,
            document_type=source_type,
            title=title,
            body_text=content,
            metadata=item_metadata,
            config=config,
        )
        document_ids.append(document["id"])
        character_count += len(content)
        chunk_count += _replace_document_chunks(
            source_id=source_id,
            document_id=document["id"],
            title=title,
            content=content,
            metadata={
                "source_type": source_type,
                "title": title,
                "source_url": item_metadata.get("source_url") or source.get("source_url"),
            },
            config=config,
        )

    with _connect(config) as conn:
        for external_id, row in current_docs.items():
            if external_id not in seen_external_ids:
                conn.execute("DELETE FROM kb_documents WHERE id = ?", (str(row["id"]),))
        conn.commit()

    if not seen_external_ids:
        raise RuntimeError(f"No text could be extracted from source {source_id}.")

    return {
        "source_id": source_id,
        "document_ids": document_ids,
        "document_count": len(seen_external_ids),
        "chunk_count": chunk_count,
        "character_count": character_count,
    }


def _ingest_single_source(source: dict[str, Any], *, config: dict | None = None) -> dict[str, Any]:
    source_type = str(source.get("source_type") or "").strip().lower()
    source_id = source["id"]
    title = _safe_title(source.get("title"), "Knowledge Source")
    metadata = dict(source.get("metadata") or {})
    documents: list[dict[str, Any]]
    if source_type == "web_url":
        documents = _extract_web_documents(str(source.get("source_url") or "").strip())
    elif source_type == "pdf_upload":
        content = _extract_pdf_text_from_bytes(_download_source_bytes(source, config=config))
        documents = [
            {
                "external_id": f"source:{source_id}",
                "title": title,
                "body_text": content,
                "metadata": {"source_url": source.get("source_url")},
            }
        ]
    else:
        raise RuntimeError(f"Unsupported KB ingest source type: {source_type}")

    metadata.update(
        {
            "source_url": source.get("source_url"),
            "mime_type": source.get("mime_type"),
            "last_ingested_at": _utcnow_iso(),
        }
    )
    result = _ingest_documents_for_source(
        source,
        documents,
        source_metadata=metadata,
        config=config,
    )
    finished_at = _utcnow_iso()
    metadata.update(
        {
            "document_count": result["document_count"],
            "chunk_count": result["chunk_count"],
            "character_count": result["character_count"],
        }
    )
    update_source(source_id, {"status": "ready", "sync_error": "", "metadata": metadata, "last_synced_at": finished_at}, config=config)
    rebuild_index(config=config)
    return {**result, "finished_at": finished_at}


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


def _preprocess_chunk_row(row: dict[str, Any]) -> dict[str, Any]:
    metadata = row.get("metadata") or {}
    search_text = " ".join(str(part or "") for part in [row.get("title"), row.get("content"), metadata.get("source_url")]).strip()
    row["_search_text"] = search_text
    row["_search_norm"] = _normalize_text(search_text)
    row["_tokens"] = _tokenize_keywords(search_text)
    row["_embedding"] = _coerce_embedding(row.get("embedding")) or build_embedding(search_text)
    return row


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


def is_kb_query(query: str) -> bool:
    normalized = _normalize_text(query)
    if not normalized:
        return False
    tokens = set(_tokenize_keywords(normalized))
    return bool(tokens & (KB_QUERY_HINTS | KB_TEXT_HINTS))


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
            }
        )
    return results


def search_hybrid(query: str, *, config: dict | None = None) -> dict[str, Any]:
    runtime = get_runtime_config(config)
    chunk_hits = search_chunks(query, limit=runtime["kb_top_k"], config=config)
    return {"query": query, "chunk_hits": chunk_hits}


def build_grounding_text(query: str, *, config: dict | None = None) -> dict[str, Any] | None:
    runtime = get_runtime_config(config)
    if not runtime["kb_enabled"]:
        return None
    if not is_kb_query(query):
        return None

    results = search_hybrid(query, config=config)
    chunk_hits = results["chunk_hits"]
    if not chunk_hits:
        return {
            "query": query,
            "chunk_hits": [],
            "grounding_text": (
                "Knowledge base rule: this looks like a knowledge-base question, but there is no confirmed "
                "match in the PDF or website excerpts for this turn. Do not guess. Say you do not have confirmed information."
            ),
        }

    parts = [
        "Knowledge base grounding rules:",
        "1. Use only the confirmed PDF and website facts below for this turn.",
        "2. If the exact fact is missing below, say you do not have confirmed information.",
        "",
    ]
    if chunk_hits:
        parts.append("Knowledge excerpts:")
        for index, item in enumerate(chunk_hits, start=1):
            label = f"[{item.get('source_type')}]"
            title = f"{item.get('title')}: " if item.get("title") else ""
            parts.append(f"{index}. {label} {title}{item.get('preview')}")
    grounding_text = "\n".join(part for part in parts if part is not None).strip()
    if len(grounding_text) > runtime["kb_context_char_budget"]:
        grounding_text = grounding_text[: runtime["kb_context_char_budget"]].rstrip() + "..."
    return {"query": query, "chunk_hits": chunk_hits, "grounding_text": grounding_text}


def search_for_agent(query: str, *, config: dict | None = None) -> dict[str, Any] | None:
    return build_grounding_text(query, config=config)


def get_status(config: dict | None = None) -> dict[str, Any]:
    runtime = get_runtime_config(config)
    try:
        sources = list_sources(limit=200, config=config)
        jobs = list_jobs(limit=200, config=config)
        chunks = _load_chunks(config)
        index_status = _get_meta("index_status", {}, config=config)
    except Exception as exc:
        return kb_runtime_issue_payload(exc, config=config)

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
        "counts": {"sources": len(sources), "jobs": len(jobs), "chunks": len(chunks)},
    }
