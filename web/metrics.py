"""
Metrics collection module for Discord bot monitoring.
Collects CPU, memory, latency, uptime, and error counts.
"""
import psutil
import os
import time
import math
import threading
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta
from collections import deque

from web.persistence import MetricsDB


class MetricsCollector:
    """Collects and provides bot performance metrics."""
    
    def __init__(self, bot_instance=None, log_directory: str = "logs"):
        self.bot = bot_instance
        self.log_directory = log_directory
        self.start_time = time.time()
        self.process = psutil.Process(os.getpid())

        # In-memory history (store ~24h @ 2s interval)
        self.cpu_history = deque(maxlen=43200)
        self.memory_history = deque(maxlen=43200)
        self.latency_history = deque(maxlen=43200)

        # Synchronization
        self._lock = threading.Lock()

        # Cached latest values (updated by background collector)
        self._latest_cpu: float = 0.0
        self._latest_memory_mb: float = 0.0
        self._latest_memory_percent: float = 0.0
        self._latest_latency: Optional[float] = None

        # Background collection state
        self.collection_interval = float(os.getenv("METRICS_COLLECTION_INTERVAL", "2"))
        self._collector_thread: Optional[threading.Thread] = None
        self._collector_running = False

        # Persistence & compression
        self._db = MetricsDB()
        self._pending_rows: List[tuple] = []
        self._last_flush_time = time.time()
        self._flush_interval = 10.0  # seconds
        self._compression_enabled = os.getenv("METRICS_COMPRESSION_ENABLED", "false").lower() == "true"
        self._last_compress_time = time.time()

        # Load recent persisted data
        self._load_recent_from_db()

        # Start background collector
        self._start_background_collection()
    
    def set_bot(self, bot_instance):
        """Set the bot instance for metrics collection."""
        self.bot = bot_instance

    def _load_recent_from_db(self) -> None:
        """Load last 24h of metrics from persistence layer."""
        rows = self._db.load_recent(24 * 3600)
        if not rows:
            return
        with self._lock:
            for ts, cpu, mem_mb, mem_pct, latency in rows:
                self.cpu_history.append({"time": ts, "value": cpu})
                self.memory_history.append({"time": ts, "value": mem_mb})
                if latency is not None:
                    self.latency_history.append({"time": ts, "value": latency})
            # bootstrap cached values
            _, cpu, mem_mb, mem_pct, latency = rows[-1]
            self._latest_cpu = cpu
            self._latest_memory_mb = mem_mb
            self._latest_memory_percent = mem_pct
            self._latest_latency = latency
    
    @staticmethod
    def _sanitize_float(value: Optional[float]) -> Optional[float]:
        """Sanitize float values to ensure JSON compliance."""
        if value is None:
            return None
        if math.isnan(value) or math.isinf(value):
            return 0.0
        return value
    
    def get_cpu_usage(self) -> float:
        """Collect CPU usage percentage (blocking call)."""
        try:
            cpu = self.process.cpu_percent(interval=self.collection_interval)
            cpu = self._sanitize_float(cpu) or 0.0
            return cpu
        except Exception:
            return 0.0
    
    def get_memory_usage(self) -> Dict[str, float]:
        """Collect current memory usage."""
        try:
            mem_info = self.process.memory_info()
            mem_mb = mem_info.rss / (1024 * 1024)  # Convert to MB
            mem_percent = self.process.memory_percent()
            
            mem_mb = self._sanitize_float(mem_mb) or 0.0
            mem_percent = self._sanitize_float(mem_percent) or 0.0
            
            return {
                "mb": mem_mb,
                "percent": mem_percent
            }
        except Exception:
            return {"mb": 0.0, "percent": 0.0}
    
    def get_bot_latency(self) -> Optional[float]:
        """Collect bot latency in milliseconds."""
        try:
            if self.bot and hasattr(self.bot, 'latency'):
                latency_ms = self.bot.latency * 1000
                latency_ms = self._sanitize_float(latency_ms)
                if latency_ms is not None:
                    latency_ms = round(latency_ms, 2)
                    return latency_ms
            return None
        except Exception:
            return None
    
    def _store_metrics(
        self,
        timestamp: float,
        cpu: float,
        memory_mb: float,
        memory_percent: float,
        latency: Optional[float],
    ):
        """Store metrics in-memory and queue for persistence."""
        cpu = self._sanitize_float(cpu) or 0.0
        memory_mb = self._sanitize_float(memory_mb) or 0.0
        memory_percent = self._sanitize_float(memory_percent) or 0.0
        if latency is not None:
            latency = self._sanitize_float(latency) or 0.0
        
        with self._lock:
            self.cpu_history.append({"time": timestamp, "value": cpu})
            self.memory_history.append({"time": timestamp, "value": memory_mb})
            if latency is not None:
                self.latency_history.append({"time": timestamp, "value": latency})
            
            self._latest_cpu = cpu
            self._latest_memory_mb = memory_mb
            self._latest_memory_percent = memory_percent
            self._latest_latency = latency
            
            if self._db.enabled:
                self._pending_rows.append(
                    (timestamp, cpu, memory_mb, memory_percent, latency)
                )

    def _maybe_flush_to_db(self):
        if not self._db.enabled:
            self._pending_rows.clear()
            return
        now = time.time()
        if now - self._last_flush_time < self._flush_interval:
            return
        rows = self._pending_rows[:]
        self._pending_rows.clear()
        self._last_flush_time = now
        if rows:
            self._db.insert_batch(rows)

    @staticmethod
    def _compress_segment(data: List[Dict[str, float]], group_size: int) -> List[Dict[str, float]]:
        if not data or group_size <= 1:
            return data
        compressed = []
        for i in range(0, len(data), group_size):
            chunk = data[i:i + group_size]
            if not chunk:
                continue
            avg_time = sum(item["time"] for item in chunk) / len(chunk)
            avg_value = sum(item["value"] for item in chunk) / len(chunk)
            compressed.append({"time": avg_time, "value": avg_value})
        return compressed

    def _maybe_compress_history(self):
        if not self._compression_enabled:
            return
        now = time.time()
        if now - self._last_compress_time < 120:
            return
        self._last_compress_time = now
        one_hour = now - 3600
        six_hours = now - 6 * 3600

        def compress_deque(history: deque):
            recent = [d for d in history if d["time"] >= one_hour]
            medium = [d for d in history if six_hours <= d["time"] < one_hour]
            older = [d for d in history if d["time"] < six_hours]
            medium = self._compress_segment(medium, 10)
            older = self._compress_segment(older, 60)
            history.clear()
            for item in older + medium + recent:
                history.append(item)

        with self._lock:
            compress_deque(self.cpu_history)
            compress_deque(self.memory_history)
            compress_deque(self.latency_history)

    def _collection_loop(self):
        while self._collector_running:
            try:
                mem = self.get_memory_usage()
                cpu = self.get_cpu_usage()
                latency = self.get_bot_latency()
                timestamp = time.time()
                self._store_metrics(timestamp, cpu, mem["mb"], mem["percent"], latency)
                self._maybe_flush_to_db()
                self._db.cleanup_old()
                self._maybe_compress_history()
            except Exception:
                time.sleep(self.collection_interval)
        self._maybe_flush_to_db()

    def _start_background_collection(self):
        if self._collector_thread and self._collector_thread.is_alive():
            return
        self._collector_running = True
        self._collector_thread = threading.Thread(
            target=self._collection_loop,
            name="MetricsCollector",
            daemon=True,
        )
        self._collector_thread.start()

    def stop(self):
        """Stop background collector and flush any pending data."""
        self._collector_running = False
        if self._collector_thread and self._collector_thread.is_alive():
            self._collector_thread.join(timeout=2.0)
        self._maybe_flush_to_db()
    
    def get_uptime(self) -> Dict[str, Any]:
        """Get bot uptime."""
        uptime_seconds = time.time() - self.start_time
        uptime_delta = timedelta(seconds=int(uptime_seconds))
        
        days = uptime_delta.days
        hours, remainder = divmod(uptime_delta.seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        
        return {
            "seconds": int(uptime_seconds),
            "formatted": f"{days}d {hours}h {minutes}m {seconds}s",
            "days": days,
            "hours": hours,
            "minutes": minutes
        }
    
    def get_error_count(self) -> int:
        """Get error count from Errors.log limited to last 48 hours."""
        try:
            error_log_path = os.path.join(self.log_directory, "Errors.log")
            if not os.path.exists(error_log_path):
                return 0
            
            cutoff = time.time() - (48 * 3600)
            count = 0
            with open(error_log_path, 'r', encoding='utf-8') as f:
                for line in f:
                    if not line.startswith("["):
                        continue
                    try:
                        ts_str = line[1:20]  # e.g. 2025-11-12 10:15:30
                        ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S").timestamp()
                        if ts >= cutoff:
                            count += 1
                    except Exception:
                        continue
            return count
        except Exception:
            return 0
    
    def get_guild_count(self) -> int:
        """Get number of guilds bot is in."""
        try:
            if self.bot and hasattr(self.bot, 'guilds'):
                return len(self.bot.guilds)
            return 0
        except Exception:
            return 0
    
    def get_all_metrics(self) -> Dict[str, Any]:
        """Return cached metrics snapshot (non-blocking)."""
        with self._lock:
            cpu = self._latest_cpu
            memory_mb = self._latest_memory_mb
            memory_percent = self._latest_memory_percent
            latency = self._latest_latency
        
        return {
            "cpu": cpu,
            "memory": {
                "mb": round(memory_mb, 2),
                "percent": round(memory_percent, 2),
            },
            "latency": latency,
            "uptime": self.get_uptime(),
            "errors": self.get_error_count(),
            "guilds": self.get_guild_count(),
            "timestamp": time.time()
        }
    
    def get_history(self, minutes: Optional[int] = None) -> Dict[str, list]:
        """
        Get historical data for graphs.
        
        Args:
            minutes: Number of minutes of history to return (None = all data up to 24h)
        """
        if minutes is None:
            # Return all data (up to 24 hours)
            return {
                "cpu": list(self.cpu_history),
                "memory": list(self.memory_history),
                "latency": list(self.latency_history)
            }
        else:
            # Return only last N minutes
            cutoff_time = time.time() - (minutes * 60)
            return {
                "cpu": [d for d in self.cpu_history if d["time"] >= cutoff_time],
                "memory": [d for d in self.memory_history if d["time"] >= cutoff_time],
                "latency": [d for d in self.latency_history if d["time"] >= cutoff_time]
            }


# Global metrics collector instance
_metrics_instance: Optional[MetricsCollector] = None


def init_metrics(bot_instance=None, log_directory: str = "logs") -> MetricsCollector:
    """Initialize the global metrics collector."""
    global _metrics_instance
    _metrics_instance = MetricsCollector(bot_instance, log_directory)
    return _metrics_instance


def get_metrics() -> MetricsCollector:
    """Get the global metrics collector instance."""
    global _metrics_instance
    if _metrics_instance is None:
        _metrics_instance = MetricsCollector()
    return _metrics_instance


def stop_metrics() -> None:
    """Stop metrics collector if it exists."""
    global _metrics_instance
    if _metrics_instance is not None:
        _metrics_instance.stop()

