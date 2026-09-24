"""Server-Sent Events (SSE) utilities."""

from __future__ import annotations

import contextlib
import json
import queue
import threading
import time
from collections.abc import Generator
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class _QueueFanoutChannel:
    """Internal fanout state for a source queue."""

    source_queue: queue.Queue
    source_timeout: float
    subscribers: set[queue.Queue] = field(default_factory=set)
    lock: threading.Lock = field(default_factory=threading.Lock)
    distributor: threading.Thread | None = None
    # Called once for every message taken from the source queue, with or
    # without subscribers: the place for work that must happen once per event.
    on_ingest: Callable[[Any], None] | None = None


def _ingest(channel: _QueueFanoutChannel, msg: Any) -> None:
    if channel.on_ingest is not None:
        with contextlib.suppress(Exception):
            channel.on_ingest(msg)


_fanout_channels: dict[str, _QueueFanoutChannel] = {}
_fanout_channels_lock = threading.Lock()


def _run_fanout(channel: _QueueFanoutChannel) -> None:
    """Drain source queue and fan out each message to all subscribers."""
    idle_drain_batch = 512

    while True:
        src = channel.source_queue
        if src is None:
            # Source queue was cleared (e.g. during interpreter shutdown).
            time.sleep(0.5)
            continue

        with channel.lock:
            subscribers = tuple(channel.subscribers)

        if not subscribers:
            # Keep ingest pipelines responsive even if UI clients disconnect:
            # drain and drop stale backlog while idle so producer threads do
            # not block on full source queues.
            drained = 0
            for _ in range(idle_drain_batch):
                try:
                    _ingest(channel, src.get_nowait())
                    drained += 1
                except queue.Empty:
                    break

            if drained == 0:
                time.sleep(channel.source_timeout)
            continue

        try:
            msg = src.get(timeout=channel.source_timeout)
        except queue.Empty:
            continue
        _ingest(channel, msg)

        for subscriber in subscribers:
            try:
                subscriber.put_nowait(msg)
            except queue.Full:
                # Drop oldest frame for this subscriber and retry once.
                try:
                    subscriber.get_nowait()
                    subscriber.put_nowait(msg)
                except (queue.Empty, queue.Full):
                    continue


def _ensure_fanout_channel(
    channel_key: str,
    source_queue: queue.Queue,
    source_timeout: float,
) -> _QueueFanoutChannel:
    """Get/create a fanout channel."""
    with _fanout_channels_lock:
        channel = _fanout_channels.get(channel_key)
        if channel is None:
            channel = _QueueFanoutChannel(source_queue=source_queue, source_timeout=source_timeout)
            _fanout_channels[channel_key] = channel

        if channel.source_queue is not source_queue:
            # Keep channel in sync if source queue object is replaced.
            channel.source_queue = source_queue
        channel.source_timeout = source_timeout

    return channel


def _ensure_distributor_running(channel: _QueueFanoutChannel, channel_key: str) -> None:
    """Ensure fanout distributor thread is running for a channel."""
    with _fanout_channels_lock:
        if channel.distributor is None or not channel.distributor.is_alive():
            channel.distributor = threading.Thread(
                target=_run_fanout,
                args=(channel,),
                daemon=True,
                name=f"sse-fanout-{channel_key}",
            )
            channel.distributor.start()


def register_ingest(
    source_queue: queue.Queue,
    channel_key: str,
    on_ingest: Callable[[Any], None],
    source_timeout: float = 1.0,
) -> None:
    """Run on_ingest once for every message of a fan-out channel's source
    queue, and start draining it now rather than when a browser first
    subscribes, so messages are handled with no page open."""
    channel = _ensure_fanout_channel(channel_key, source_queue, source_timeout)
    channel.on_ingest = on_ingest
    _ensure_distributor_running(channel, channel_key)


def subscribe_fanout_queue(
    source_queue: queue.Queue,
    channel_key: str,
    source_timeout: float = 1.0,
    subscriber_queue_size: int = 500,
) -> tuple[queue.Queue, Callable[[], None]]:
    """
    Subscribe a client queue to a shared source queue fanout channel.

    Returns:
        tuple: (subscriber_queue, unsubscribe_fn)
    """
    channel = _ensure_fanout_channel(channel_key, source_queue, source_timeout)
    subscriber = queue.Queue(maxsize=subscriber_queue_size)

    with channel.lock:
        channel.subscribers.add(subscriber)

    # Start distributor only after subscriber is registered to avoid initial-loss race.
    _ensure_distributor_running(channel, channel_key)

    def _unsubscribe() -> None:
        with channel.lock:
            channel.subscribers.discard(subscriber)

    return subscriber, _unsubscribe


def sse_stream_fanout(
    source_queue: queue.Queue,
    channel_key: str,
    timeout: float = 1.0,
    keepalive_interval: float = 30.0,
    stop_check: Callable[[], bool] | None = None,
    on_message: Callable[[dict[str, Any]], None] | None = None,
) -> Generator[str, None, None]:
    """
    Generate an SSE stream from a fanout channel backed by source_queue.
    """
    subscriber, unsubscribe = subscribe_fanout_queue(
        source_queue=source_queue,
        channel_key=channel_key,
        source_timeout=timeout,
    )
    last_keepalive = time.time()

    # Send an immediate keepalive so the browser receives response headers
    # right away (Werkzeug dev server buffers headers until first body byte).
    yield format_sse({"type": "keepalive"})

    try:
        while True:
            if stop_check and stop_check():
                break

            try:
                msg = subscriber.get(timeout=timeout)
                last_keepalive = time.time()
                if on_message and isinstance(msg, dict):
                    with contextlib.suppress(Exception):
                        on_message(msg)
                yield format_sse(msg)
            except queue.Empty:
                now = time.time()
                if now - last_keepalive >= keepalive_interval:
                    yield format_sse({"type": "keepalive"})
                    last_keepalive = now
    finally:
        unsubscribe()


def sse_stream(
    data_queue: queue.Queue,
    timeout: float = 1.0,
    keepalive_interval: float = 30.0,
    stop_check: Callable[[], bool] | None = None,
    channel_key: str | None = None,
) -> Generator[str, None, None]:
    """
    Generate SSE stream from a queue.

    Args:
        data_queue: Queue to read messages from
        timeout: Queue get timeout in seconds
        keepalive_interval: Seconds between keepalive messages
        stop_check: Optional callable that returns True to stop the stream
        channel_key: Optional fanout key; defaults to stable queue id

    Yields:
        SSE formatted strings
    """
    key = channel_key or f"queue:{id(data_queue)}"
    yield from sse_stream_fanout(
        source_queue=data_queue,
        channel_key=key,
        timeout=timeout,
        keepalive_interval=keepalive_interval,
        stop_check=stop_check,
    )


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
