from __future__ import annotations

import hashlib
import sqlite3
import time
from pathlib import Path

CACHE_DIR = Path.home() / ".cache" / "trawl"
CACHE_DB = CACHE_DIR / "cache.db"


def _ensure_db():
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(CACHE_DB))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS cache ("
        "  key TEXT PRIMARY KEY,"
        "  value BLOB,"
        "  created_at REAL,"
        "  ttl REAL"
        ")"
    )
    conn.commit()
    return conn


def _make_key(url: str, params: dict | None = None) -> str:
    raw = url
    if params:
        raw += str(sorted(params.items()))
    return hashlib.sha256(raw.encode()).hexdigest()


class Cache:
    enabled: bool = True
    default_ttl: float = 3600.0

    def __init__(self, enabled: bool = True, ttl: float = 3600.0):
        self.enabled = enabled
        self.default_ttl = ttl

    def get(self, url: str, params: dict | None = None) -> bytes | None:
        if not self.enabled:
            return None
        try:
            conn = _ensure_db()
            key = _make_key(url, params)
            row = conn.execute(
                "SELECT value, created_at, ttl FROM cache WHERE key = ?", (key,)
            ).fetchone()
            if row:
                val, created_at, ttl = row
                if time.time() - created_at < ttl:
                    return val
                conn.execute("DELETE FROM cache WHERE key = ?", (key,))
                conn.commit()
        except Exception:
            pass
        return None

    def set(self, url: str, value: bytes, params: dict | None = None, ttl: float | None = None):
        if not self.enabled:
            return
        try:
            conn = _ensure_db()
            key = _make_key(url, params)
            conn.execute(
                "INSERT OR REPLACE INTO cache (key, value, created_at, ttl) VALUES (?, ?, ?, ?)",
                (key, value, time.time(), ttl or self.default_ttl),
            )
            conn.commit()
        except Exception:
            pass

    def clear(self):
        try:
            conn = _ensure_db()
            conn.execute("DELETE FROM cache")
            conn.commit()
        except Exception:
            pass

    def invalidate(self, url_pattern: str):
        try:
            conn = _ensure_db()
            key_prefix = hashlib.sha256(url_pattern.encode()).hexdigest()[:16]
            conn.execute("DELETE FROM cache WHERE key LIKE ?", (f"{key_prefix}%",))
            conn.commit()
        except Exception:
            pass
