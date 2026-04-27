"""
persistence.py
--------------
SQLite-backed persistence for device registry and active alerts.

Design philosophy — SD card friendly:
  - PRIMARY store is in-memory (fast reads, zero disk I/O per request)
  - Background thread flushes to SQLite every DB_FLUSH_INTERVAL_SEC seconds
  - Manual flush_now() triggered on significant changes (new device, new alert)
  - SQLite configured with WAL journal mode → reduces write amplification
  - On startup: restores state from SQLite automatically

Tables:
  devices   — registered ESP32 devices
  alerts    — currently active fire alerts

Usage:
  Call init_db() once at startup (done automatically on import).
  Call flush_now() from device_registry/state_manager on important writes.
  Background flusher runs automatically.
"""

import json
import os
import sqlite3
import threading
import time
from typing import Any, Dict, List

from config.settings import DATA_DIR, DB_FLUSH_INTERVAL_SEC
from logger import get_logger

log = get_logger(__name__)

_DB_PATH = os.path.join(DATA_DIR, "evacuation.db")
_lock = threading.Lock()
_flush_thread: threading.Thread | None = None
_stop_event = threading.Event()

# Dirty flag — set by mark_dirty(), cleared after each successful flush.
# Prevents SD card writes when nothing has changed.
_dirty = threading.Event()

# Callbacks registered by device_registry and state_manager
# so persistence can pull current in-memory state at flush time
_snapshot_callbacks: Dict[str, Any] = {}


# ─────────────────────────────────────────────────────────────────────
# Init
# ─────────────────────────────────────────────────────────────────────

def init_db() -> None:
    """Create DB file, tables, and start the background flush thread."""
    os.makedirs(DATA_DIR, exist_ok=True)
    with _get_conn() as conn:
        _create_tables(conn)
    log.info("💾 SQLite persistence initialised at %s", _DB_PATH)
    _start_flush_thread()


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")   # WAL → minimal write amplification
    conn.execute("PRAGMA synchronous=NORMAL") # Safe + fast (not FULL)
    conn.row_factory = sqlite3.Row
    return conn


def _create_tables(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS devices (
            device_id    TEXT PRIMARY KEY,
            building_id  TEXT NOT NULL,
            node_id      TEXT NOT NULL,
            type         TEXT NOT NULL,
            ip           TEXT,
            status       TEXT DEFAULT 'ONLINE',
            last_seen    REAL,
            registered_at TEXT,
            updated_at   REAL
        );

        CREATE TABLE IF NOT EXISTS alerts (
            node_id    TEXT PRIMARY KEY,
            severity   TEXT NOT NULL,
            alert_time REAL NOT NULL,
            updated_at REAL
        );

        CREATE TABLE IF NOT EXISTS events (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ts          REAL NOT NULL,
            event_type  TEXT NOT NULL,
            node_id     TEXT,
            device_id   TEXT,
            severity    TEXT,
            metadata    TEXT,
            created_at  REAL DEFAULT (unixepoch('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_events_ts       ON events(ts DESC);
        CREATE INDEX IF NOT EXISTS idx_events_type     ON events(event_type);
        CREATE INDEX IF NOT EXISTS idx_events_node     ON events(node_id);
    """)
    conn.commit()


# ─────────────────────────────────────────────────────────────────────
# Background flush thread
# ─────────────────────────────────────────────────────────────────────

def _start_flush_thread() -> None:
    global _flush_thread
    _stop_event.clear()
    _flush_thread = threading.Thread(target=_flush_loop, daemon=True, name="db-flusher")
    _flush_thread.start()
    log.info("💾 DB flush thread started (interval=%ds)", DB_FLUSH_INTERVAL_SEC)


def _flush_loop() -> None:
    while not _stop_event.wait(timeout=DB_FLUSH_INTERVAL_SEC):
        if not _dirty.is_set():
            continue  # Nothing changed — skip write entirely (SD-friendly)
        try:
            _do_flush()
            _dirty.clear()
        except Exception as exc:
            log.error("💾 Flush error: %s", exc)


def _do_flush() -> None:
    """Write all registered snapshot callbacks to SQLite."""
    now = time.time()
    with _lock:
        callbacks = dict(_snapshot_callbacks)

    if not callbacks:
        return

    try:
        with _get_conn() as conn:
            for name, fn in callbacks.items():
                try:
                    fn(conn, now)
                except Exception as exc:
                    log.error("💾 Snapshot '%s' flush failed: %s", name, exc)
            conn.commit()
    except Exception as exc:
        log.error("💾 DB connection error during flush: %s", exc)


def mark_dirty() -> None:
    """
    Signal that in-memory state has changed and should be flushed on the
    next background cycle (within DB_FLUSH_INTERVAL_SEC seconds).

    Use this for high-frequency mutations (sensor updates, alert changes).
    The background thread will write to SQLite only once per interval — not
    once per call — protecting the SD card from excess writes.
    """
    _dirty.set()


def flush_now() -> None:
    """
    Trigger an immediate out-of-cycle flush.
    Reserve for critical paths only: shutdown, full reset, device registration.
    Normal alert mutations should use mark_dirty() instead.
    """
    try:
        with _get_conn() as conn:
            now = time.time()
            with _lock:
                callbacks = dict(_snapshot_callbacks)
            for name, fn in callbacks.items():
                try:
                    fn(conn, now)
                except Exception as exc:
                    log.error("💾 Immediate flush '%s' failed: %s", name, exc)
            conn.commit()
        _dirty.clear()  # We just wrote everything — no need for background flush
    except Exception as exc:
        log.error("💾 Immediate flush DB error: %s", exc)


def register_snapshot(name: str, fn) -> None:
    """
    Register a callback function that will be called during each flush.
    fn(conn: sqlite3.Connection, now: float) → None
    """
    with _lock:
        _snapshot_callbacks[name] = fn


def stop() -> None:
    """Stop the background flush thread (call on shutdown)."""
    _stop_event.set()
    if _flush_thread:
        _flush_thread.join(timeout=5)


# ─────────────────────────────────────────────────────────────────────
# Device restore
# ─────────────────────────────────────────────────────────────────────

def load_devices() -> List[Dict[str, Any]]:
    """Load all persisted devices from SQLite. Returns list of device dicts."""
    try:
        with _get_conn() as conn:
            rows = conn.execute("SELECT * FROM devices").fetchall()
            return [dict(r) for r in rows]
    except Exception as exc:
        log.error("💾 Failed to load devices from DB: %s", exc)
        return []


def load_alerts() -> List[Dict[str, Any]]:
    """Load all persisted active alerts from SQLite."""
    try:
        with _get_conn() as conn:
            rows = conn.execute("SELECT * FROM alerts").fetchall()
            return [dict(r) for r in rows]
    except Exception as exc:
        log.error("💾 Failed to load alerts from DB: %s", exc)
        return []


# ─────────────────────────────────────────────────────────────────────
# Event log (direct write — events are append-only, no in-memory buffer)
# ─────────────────────────────────────────────────────────────────────

def write_event(
    event_type: str,
    ts: float,
    node_id: str | None = None,
    device_id: str | None = None,
    severity: str | None = None,
    metadata: Dict | None = None,
) -> None:
    """
    Append a structured event record to the SQLite events table.
    Events are written directly (not buffered) since they're append-only
    and not frequently accessed reads — write amplification is not a concern.
    """
    try:
        with _get_conn() as conn:
            conn.execute(
                """INSERT INTO events (ts, event_type, node_id, device_id, severity, metadata)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (ts, event_type, node_id, device_id, severity,
                 json.dumps(metadata) if metadata else None),
            )
            conn.commit()
    except Exception as exc:
        log.error("💾 Failed to write event '%s': %s", event_type, exc)


def load_events(limit: int = 100, event_type: str | None = None) -> List[Dict]:
    """Load recent events from SQLite, newest first."""
    try:
        with _get_conn() as conn:
            if event_type:
                rows = conn.execute(
                    "SELECT * FROM events WHERE event_type=? ORDER BY ts DESC LIMIT ?",
                    (event_type, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM events ORDER BY ts DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            result = []
            for r in rows:
                d = dict(r)
                if d.get("metadata"):
                    try:
                        d["metadata"] = json.loads(d["metadata"])
                    except ValueError:
                        pass
                result.append(d)
            return result
    except Exception as exc:
        log.error("💾 Failed to load events: %s", exc)
        return []


# Auto-init on import
try:
    init_db()
except Exception as _e:
    log.error("💾 persistence init failed: %s — running without persistence.", _e)
