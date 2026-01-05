"""Server-Sent Events (SSE) utilities."""

from __future__ import annotations

import json
import queue
import time
from typing import Any, Generator


def sse_stream(
    data_queue: queue.Queue, timeout: float = 1.0, keepalive_interval: float = 30.0, stop_check: callable = None
) -> Generator[str, None, None]:
    """
    Generate SSE stream from a queue.

    Args:
        data_queue: Queue to read messages from
        timeout: Queue get timeout in seconds
        keepalive_interval: Seconds between keepalive messages
        stop_check: Optional callable that returns True to stop the stream

    Yields:
        SSE formatted strings
    """
    last_keepalive = time.time()

    while True:
        # Check if we should stop
        if stop_check and stop_check():
            break

        try:
            msg = data_queue.get(timeout=timeout)
            last_keepalive = time.time()
            yield format_sse(msg)
        except queue.Empty:
            # Send keepalive if enough time has passed
            now = time.time()
            if now - last_keepalive >= keepalive_interval:
                yield format_sse({"type": "keepalive"})
                last_keepalive = now


def format_sse(data: dict[str, Any] | str, event: str | None = None) -> str:
    """
    Format data as SSE message.

    Args:
        data: Data to send (will be JSON encoded if dict)
        event: Optional event name

    Returns:
        SSE formatted string
    """
    if isinstance(data, dict):
        data = json.dumps(data)

    lines = []
    if event:
        lines.append(f"event: {event}")
    lines.append(f"data: {data}")
    lines.append("")
    lines.append("")

    return "\n".join(lines)


def clear_queue(q: queue.Queue) -> int:
    """
    Clear all items from a queue.

    Args:
        q: Queue to clear

    Returns:
        Number of items cleared
    """
    count = 0
    while True:
        try:
            q.get_nowait()
            count += 1
        except queue.Empty:
            break
    return count
