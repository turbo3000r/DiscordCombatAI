import os
import sqlite3
import threading
import time
from typing import List, Optional, Tuple


class MetricsDB:
    """
    Lightweight SQLite wrapper to persist metrics.
    Gracefully degrades (disables itself) if database operations fail.
    """

    def __init__(self, db_path: Optional[str] = None, retention_days: int = 7) -> None:
        self.db_path = db_path or os.getenv("METRICS_DB_PATH", "metrics.db")
        self.retention_days = int(os.getenv("METRICS_RETENTION_DAYS", retention_days))
        self._lock = threading.Lock()
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()

    @property
    def enabled(self) -> bool:
        return self._conn is not None

    def _init_db(self) -> None:
        try:
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            cur = self._conn.cursor()
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    cpu REAL NOT NULL,
                    memory_mb REAL NOT NULL,
                    memory_percent REAL,
                    latency REAL
                )
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_metrics_timestamp
                ON metrics (timestamp)
                """
            )
            self._conn.commit()
            self.cleanup_old()
        except Exception:
            self._conn = None

    def insert_batch(
        self, rows: List[Tuple[float, float, float, float, Optional[float]]]
    ) -> None:
        if not self.enabled or not rows:
            return
        try:
            with self._lock:
                self._conn.executemany(
                    """
                    INSERT INTO metrics (timestamp, cpu, memory_mb, memory_percent, latency)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    rows,
                )
                self._conn.commit()
        except Exception:
            self._conn = None

    def load_recent(
        self, seconds: float
    ) -> List[Tuple[float, float, float, float, Optional[float]]]:
        if not self.enabled:
            return []
        cutoff = time.time() - seconds
        try:
            with self._lock:
                cur = self._conn.cursor()
                cur.execute(
                    """
                    SELECT timestamp, cpu, memory_mb, memory_percent, latency
                    FROM metrics
                    WHERE timestamp >= ?
                    ORDER BY timestamp ASC
                    """,
                    (cutoff,),
                )
                return cur.fetchall()
        except Exception:
            return []

    def cleanup_old(self) -> None:
        if not self.enabled:
            return
        cutoff = time.time() - self.retention_days * 24 * 3600
        try:
            with self._lock:
                self._conn.execute("DELETE FROM metrics WHERE timestamp < ?", (cutoff,))
                self._conn.commit()
        except Exception:
            pass

