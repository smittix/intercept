"""Data cleanup utilities for stale entries."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

logger = logging.getLogger("intercept.cleanup")


class DataStore:
    """Thread-safe data store with automatic cleanup of stale entries."""

    def __init__(self, max_age_seconds: float = 300.0, name: str = "data"):
        """
        Initialize data store.

        Args:
            max_age_seconds: Maximum age of entries before cleanup (default 5 minutes)
            name: Name for logging purposes
        """
        self.data: dict[str, Any] = {}
        self.timestamps: dict[str, float] = {}
        self.max_age = max_age_seconds
        self.name = name
        self._lock = threading.Lock()

    def set(self, key: str, value: Any) -> None:
        """Add or update an entry."""
        with self._lock:
            self.data[key] = value
            self.timestamps[key] = time.time()

    def get(self, key: str, default: Any = None) -> Any:
        """Get an entry."""
        with self._lock:
            return self.data.get(key, default)

    def update(self, key: str, updates: dict) -> None:
        """Update an existing entry with new values."""
        with self._lock:
            if key in self.data:
                if isinstance(self.data[key], dict):
                    self.data[key].update(updates)
                else:
                    self.data[key] = updates
            else:
                self.data[key] = updates
            self.timestamps[key] = time.time()

    def touch(self, key: str) -> None:
        """Update timestamp for an entry without changing data."""
        with self._lock:
            if key in self.data:
                self.timestamps[key] = time.time()

    def delete(self, key: str) -> bool:
        """Delete an entry."""
        with self._lock:
            if key in self.data:
                del self.data[key]
                del self.timestamps[key]
                return True
            return False

    def clear(self) -> None:
        """Clear all entries."""
        with self._lock:
            self.data.clear()
            self.timestamps.clear()

    def all(self) -> dict[str, Any]:
        """Get a copy of all data."""
        with self._lock:
            return dict(self.data)

    def keys(self) -> list[str]:
        """Get all keys."""
        with self._lock:
            return list(self.data.keys())

    def values(self) -> list[Any]:
        """Get all values."""
        with self._lock:
            return list(self.data.values())

    def items(self) -> list[tuple[str, Any]]:
        """Get all items."""
        with self._lock:
            return list(self.data.items())

    def __len__(self) -> int:
        with self._lock:
            return len(self.data)

    def __contains__(self, key: str) -> bool:
        with self._lock:
            return key in self.data

    def cleanup(self) -> int:
        """
        Remove entries older than max_age.

        Returns:
            Number of entries removed
        """
        now = time.time()
        expired = []

        with self._lock:
            for key, timestamp in self.timestamps.items():
                if now - timestamp > self.max_age:
                    expired.append(key)

            for key in expired:
                del self.data[key]
                del self.timestamps[key]

        if expired:
            logger.debug(f"{self.name}: Cleaned up {len(expired)} stale entries")

        return len(expired)


class CleanupManager:
    """Manages periodic cleanup of multiple data stores."""

    def __init__(self, interval: float = 60.0):
        """
        Initialize cleanup manager.

        Args:
            interval: Cleanup interval in seconds
        """
        self.stores: list[DataStore] = []
        self.interval = interval
        self._timer: threading.Timer | None = None
        self._running = False
        self._lock = threading.Lock()

    def register(self, store: DataStore) -> None:
        """Register a data store for cleanup."""
        with self._lock:
            if store not in self.stores:
                self.stores.append(store)

    def unregister(self, store: DataStore) -> None:
        """Unregister a data store."""
        with self._lock:
            if store in self.stores:
                self.stores.remove(store)

    def start(self) -> None:
        """Start the cleanup timer."""
        with self._lock:
            if self._running:
                return
            self._running = True
            self._schedule_cleanup()

    def stop(self) -> None:
        """Stop the cleanup timer."""
        with self._lock:
            self._running = False
            if self._timer:
                self._timer.cancel()
                self._timer = None

    def _schedule_cleanup(self) -> None:
        """Schedule the next cleanup."""
        if not self._running:
            return
        self._timer = threading.Timer(self.interval, self._run_cleanup)
        self._timer.daemon = True
        self._timer.start()

    def _run_cleanup(self) -> None:
        """Run cleanup on all registered stores."""
        total_cleaned = 0

        with self._lock:
            stores = list(self.stores)

        for store in stores:
            try:
                total_cleaned += store.cleanup()
            except Exception as e:
                logger.error(f"Error cleaning up {store.name}: {e}")

        if total_cleaned > 0:
            logger.info(f"Cleanup complete: removed {total_cleaned} stale entries")

        self._schedule_cleanup()

    def cleanup_now(self) -> int:
        """Run cleanup immediately."""
        total = 0
        with self._lock:
            stores = list(self.stores)
        for store in stores:
            try:
                total += store.cleanup()
            except Exception as e:
                logger.error(f"Error cleaning up {store.name}: {e}")
        return total


# Global cleanup manager
cleanup_manager = CleanupManager(interval=60.0)


def cleanup_dict(data: dict[str, Any], timestamps: dict[str, float], max_age_seconds: float = 300.0) -> list[str]:
    """
    Clean up stale entries from a dictionary.

    Args:
        data: Dictionary to clean
        timestamps: Dictionary of key -> last_seen timestamp
        max_age_seconds: Maximum age in seconds

    Returns:
        List of removed keys
    """
    now = time.time()
    expired = []

    for key, timestamp in list(timestamps.items()):
        if now - timestamp > max_age_seconds:
            expired.append(key)

    for key in expired:
        data.pop(key, None)
        timestamps.pop(key, None)

    return expired
