"""Meshtastic device management and message handling.

This module provides integration with Meshtastic mesh networking devices,
allowing INTERCEPT to receive and decode messages from LoRa mesh networks.

Supports multiple connection types:
- USB/Serial: Physical device connected via USB
- TCP: WiFi-enabled devices (T-Beam, Heltec WiFi LoRa, etc.)

Install SDK with: pip install meshtastic
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import secrets
import threading
import urllib.request
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

from utils.logging import get_logger

logger = get_logger("intercept.meshtastic")

# Meshtastic SDK import (optional dependency)
try:
    import meshtastic
    import meshtastic.serial_interface
    import meshtastic.tcp_interface
    from meshtastic import BROADCAST_ADDR
    from pubsub import pub

    HAS_MESHTASTIC = True
except ImportError:
    HAS_MESHTASTIC = False
    BROADCAST_ADDR = 0xFFFFFFFF  # Fallback if SDK not installed
    logger.warning("Meshtastic SDK not installed. Install with: pip install meshtastic")


@dataclass
class MeshtasticMessage:
    """Decoded Meshtastic message."""

    from_id: str
    to_id: str
    message: str | None
    portnum: str
    channel: int
    rssi: int | None
    snr: float | None
    hop_limit: int | None
    timestamp: datetime
    from_name: str | None = None
    to_name: str | None = None
    raw_packet: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "type": "meshtastic",
            "from": self.from_id,
            "from_name": self.from_name,
            "to": self.to_id,
            "to_name": self.to_name,
            "message": self.message,
            "text": self.message,  # Alias for frontend compatibility
            "portnum": self.portnum,
            "channel": self.channel,
            "rssi": self.rssi,
            "snr": self.snr,
            "hop_limit": self.hop_limit,
            "timestamp": self.timestamp.timestamp(),  # Unix seconds for frontend
        }


@dataclass
class ChannelConfig:
    """Meshtastic channel configuration."""

    index: int
    name: str
    psk: bytes
    role: int  # 0=DISABLED, 1=PRIMARY, 2=SECONDARY

    def to_dict(self) -> dict:
        """Convert to dict for API response (hides raw PSK)."""
        role_names = ["DISABLED", "PRIMARY", "SECONDARY"]
        # Default key is 1 byte (0x01) or the well-known AQ== base64
        is_default = self.psk in (b"\x01", b"")
        return {
            "index": self.index,
            "name": self.name,
            "role": role_names[self.role] if self.role < len(role_names) else "UNKNOWN",
            "encrypted": len(self.psk) > 1,
            "key_type": self._get_key_type(),
            "is_default_key": is_default,
        }

    def _get_key_type(self) -> str:
        """Determine encryption type from key length."""
        if len(self.psk) == 0:
            return "none"
        elif len(self.psk) == 1:
            return "default"
        elif len(self.psk) == 16:
            return "AES-128"
        elif len(self.psk) == 32:
            return "AES-256"
        else:
            return "unknown"


@dataclass
class MeshNode:
    """Tracked Meshtastic node with position and metadata."""

    num: int
    user_id: str
    long_name: str
    short_name: str
    hw_model: str
    latitude: float | None = None
    longitude: float | None = None
    altitude: int | None = None
    sats_in_view: int | None = None
    battery_level: int | None = None
    snr: float | None = None
    last_heard: datetime | None = None
    # Device telemetry
    voltage: float | None = None
    channel_utilization: float | None = None
    air_util_tx: float | None = None
    # Environment telemetry
    temperature: float | None = None
    humidity: float | None = None
    barometric_pressure: float | None = None

    def to_dict(self) -> dict:
        return {
            "num": self.num,
            "id": self.user_id or f"!{self.num:08x}",
            "long_name": self.long_name,
            "short_name": self.short_name,
            "hw_model": self.hw_model,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "altitude": self.altitude,
            "sats_in_view": self.sats_in_view,
            "battery_level": self.battery_level,
            "snr": self.snr,
            "last_heard": self.last_heard.isoformat() if self.last_heard else None,
            "has_position": self.latitude is not None and self.longitude is not None,
            # Device telemetry
            "voltage": self.voltage,
            "channel_utilization": self.channel_utilization,
            "air_util_tx": self.air_util_tx,
            # Environment telemetry
            "temperature": self.temperature,
            "humidity": self.humidity,
            "barometric_pressure": self.barometric_pressure,
        }


@dataclass
class NodeInfo:
    """Meshtastic node information."""

    num: int
    user_id: str
    long_name: str
    short_name: str
    hw_model: str
    latitude: float | None
    longitude: float | None
    altitude: int | None
    sats_in_view: int | None = None

    def to_dict(self) -> dict:
        return {
            "num": self.num,
            "user_id": self.user_id,
            "long_name": self.long_name,
            "short_name": self.short_name,
            "hw_model": self.hw_model,
            "position": {
                "latitude": self.latitude,
                "longitude": self.longitude,
                "altitude": self.altitude,
                "sats_in_view": self.sats_in_view,
            }
            if self.latitude is not None
            else None,
        }


@dataclass
class TracerouteResult:
    """Result of a traceroute to a mesh node."""

    destination_id: str
    route: list[str]  # Node IDs in forward path
    route_back: list[str]  # Return path
    snr_towards: list[float]  # SNR per hop (forward)
    snr_back: list[float]  # SNR per hop (return)
    timestamp: datetime
    success: bool

    def to_dict(self) -> dict:
        return {
            "destination_id": self.destination_id,
            "route": self.route,
            "route_back": self.route_back,
            "snr_towards": self.snr_towards,
            "snr_back": self.snr_back,
            "timestamp": self.timestamp.isoformat(),
            "success": self.success,
        }


@dataclass
class TelemetryPoint:
    """Single telemetry data point for graphing."""

    timestamp: datetime
    battery_level: int | None = None
    voltage: float | None = None
    temperature: float | None = None
    humidity: float | None = None
    pressure: float | None = None
    channel_utilization: float | None = None
    air_util_tx: float | None = None

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp.isoformat(),
            "battery_level": self.battery_level,
            "voltage": self.voltage,
            "temperature": self.temperature,
            "humidity": self.humidity,
            "pressure": self.pressure,
            "channel_utilization": self.channel_utilization,
            "air_util_tx": self.air_util_tx,
        }


@dataclass
class PendingMessage:
    """Message waiting for ACK/NAK."""

    packet_id: int
    destination: int
    text: str
    channel: int
    timestamp: datetime
    status: str = "pending"  # pending, acked, failed

    def to_dict(self) -> dict:
        return {
            "packet_id": self.packet_id,
            "destination": self.destination,
            "text": self.text,
            "channel": self.channel,
            "timestamp": self.timestamp.isoformat(),
            "status": self.status,
        }


@dataclass
class NeighborInfo:
    """Neighbor information from NEIGHBOR_INFO_APP."""

    neighbor_num: int
    neighbor_id: str
    snr: float
    timestamp: datetime

    def to_dict(self) -> dict:
        return {
            "neighbor_num": self.neighbor_num,
            "neighbor_id": self.neighbor_id,
            "snr": self.snr,
            "timestamp": self.timestamp.isoformat(),
        }


class MeshtasticClient:
    """Client for connecting to Meshtastic devices."""

    def __init__(self):
        self._interface = None
        self._running = False
        self._callback: Callable[[MeshtasticMessage], None] | None = None
        self._lock = threading.Lock()
        self._nodes: dict[int, MeshNode] = {}  # num -> MeshNode
        self._device_path: str | None = None
        self._connection_type: str | None = None  # 'serial' or 'tcp'
        self._error: str | None = None
        self._traceroute_results: list[TracerouteResult] = []
        self._max_traceroute_results = 50

        # Telemetry history for graphing (node_num -> deque of TelemetryPoints)
        self._telemetry_history: dict[int, deque] = {}
        self._max_telemetry_points = 1000

        # Pending messages for ACK tracking (packet_id -> PendingMessage)
        self._pending_messages: dict[int, PendingMessage] = {}

        # Neighbor info (node_num -> list of NeighborInfo)
        self._neighbors: dict[int, list[NeighborInfo]] = {}

        # Firmware version cache
        self._firmware_version: str | None = None
        self._latest_firmware: dict | None = None
        self._firmware_check_time: datetime | None = None

        # Range test state
        self._range_test_running: bool = False
        self._range_test_results: list[dict] = []

        # Topology tracking: node_id -> {neighbors, hop_count, msg_count, last_seen}
        self._topology: dict[str, dict] = {}

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def device_path(self) -> str | None:
        return self._device_path

    @property
    def connection_type(self) -> str | None:
        return self._connection_type

    @property
    def error(self) -> str | None:
        return self._error

    def set_callback(self, callback: Callable[[MeshtasticMessage], None]) -> None:
        """Set callback for received messages."""
        self._callback = callback

    def record_message_route(self, from_node: str, to_node: str, hops: int | None = None) -> None:
        """Record a message route for topology tracking."""
        now = datetime.now(timezone.utc).isoformat()
        for node_id in (from_node, to_node):
            if node_id not in self._topology:
                self._topology[node_id] = {
                    "neighbors": set(),
                    "hop_count": hops,
                    "msg_count": 0,
                    "last_seen": now,
                }
            entry = self._topology[node_id]
            entry["msg_count"] += 1
            entry["last_seen"] = now
        self._topology[from_node]["neighbors"].add(to_node)
        self._topology[to_node]["neighbors"].add(from_node)

    def get_topology(self) -> dict:
        """Return topology dict with serializable sets."""
        result = {}
        for node_id, data in self._topology.items():
            result[node_id] = {
                "neighbors": list(data.get("neighbors", set())),
                "hop_count": data.get("hop_count"),
                "msg_count": data.get("msg_count", 0),
                "last_seen": data.get("last_seen"),
            }
        return result

    def connect(self, device: str | None = None, connection_type: str = "serial", hostname: str | None = None) -> bool:
        """
        Connect to a Meshtastic device.

        Args:
            device: Serial port path (e.g., /dev/ttyUSB0, /dev/ttyACM0).
                    Only used for serial connections. If None, auto-discovers.
            connection_type: Connection type - 'serial' or 'tcp' (default: 'serial')
            hostname: Hostname or IP address for TCP connections (e.g., '192.168.1.100')

        Returns:
            True if connected successfully.
        """
        if not HAS_MESHTASTIC:
            self._error = "Meshtastic SDK not installed. Install with: pip install meshtastic"
            return False

        # Quick check under lock — bail if already running
        with self._lock:
            if self._running:
                return True

        # Create interface outside lock (blocking I/O: serial/TCP connect)
        new_interface = None
        new_device_path = None
        new_connection_type = None
        try:
            # Subscribe to message events before connecting
            pub.subscribe(self._on_receive, "meshtastic.receive")
            pub.subscribe(self._on_connection, "meshtastic.connection.established")
            pub.subscribe(self._on_disconnect, "meshtastic.connection.lost")

            if connection_type == "tcp":
                if not hostname:
                    self._error = "Hostname is required for TCP connections"
                    self._cleanup_subscriptions()
                    return False
                new_interface = meshtastic.tcp_interface.TCPInterface(hostname=hostname)
                new_device_path = hostname
                new_connection_type = "tcp"
                logger.info(f"Connected to Meshtastic device via TCP: {hostname}")
            else:
                if device:
                    new_interface = meshtastic.serial_interface.SerialInterface(device)
                    new_device_path = device
                else:
                    new_interface = meshtastic.serial_interface.SerialInterface()
                    new_device_path = "auto"
                new_connection_type = "serial"
                logger.info(f"Connected to Meshtastic device via serial: {new_device_path}")
        except Exception as e:
            self._error = str(e)
            logger.error(f"Failed to connect to Meshtastic: {e}")
            self._cleanup_subscriptions()
            return False

        # Install interface under lock
        with self._lock:
            if self._running:
                # Another thread connected while we were connecting — discard ours
                if new_interface:
                    with contextlib.suppress(Exception):
                        new_interface.close()
                return True

            self._interface = new_interface
            self._device_path = new_device_path
            self._connection_type = new_connection_type
            self._running = True
            self._error = None
            return True

    def disconnect(self) -> None:
        """Disconnect from the Meshtastic device."""
        iface_to_close = None
        with self._lock:
            iface_to_close = self._interface
            self._interface = None
            self._cleanup_subscriptions()
            self._running = False
            self._device_path = None
            self._connection_type = None

        # Close interface outside lock (blocking I/O)
        if iface_to_close:
            try:
                iface_to_close.close()
            except Exception as e:
                logger.warning(f"Error closing Meshtastic interface: {e}")

        logger.info("Disconnected from Meshtastic device")

    def _cleanup_subscriptions(self) -> None:
        """Unsubscribe from pubsub topics."""
        if HAS_MESHTASTIC:
            with contextlib.suppress(Exception):
                pub.unsubscribe(self._on_receive, "meshtastic.receive")
            with contextlib.suppress(Exception):
                pub.unsubscribe(self._on_connection, "meshtastic.connection.established")
            with contextlib.suppress(Exception):
                pub.unsubscribe(self._on_disconnect, "meshtastic.connection.lost")

    def _on_connection(self, interface, topic=None) -> None:
        """Handle connection established event."""
        logger.info("Meshtastic connection established")
        # Sync nodes from device's nodeDB so names are available for messages
        self._sync_nodes_from_interface()
        # Try to set device time from host computer
        self._sync_device_time()

    def _on_disconnect(self, interface, topic=None) -> None:
        """Handle connection lost event."""
        logger.warning("Meshtastic connection lost")
        self._running = False

    def _sync_device_time(self) -> None:
        """Sync device time from host computer."""
        if not self._interface:
            return
        try:
            # Try to set the device's time using the SDK
            import time

            current_time = int(time.time())
            if hasattr(self._interface, "localNode") and self._interface.localNode:
                local_node = self._interface.localNode
                if hasattr(local_node, "setTime"):
                    local_node.setTime(current_time)
                    logger.info(f"Set device time to {current_time}")
                elif hasattr(self._interface, "sendAdmin"):
                    # Alternative: send admin message with time
                    logger.debug("setTime not available, device time not synced")
            else:
                logger.debug("localNode not available, device time not synced")
        except Exception as e:
            logger.warning(f"Failed to sync device time: {e}")

    def _on_receive(self, packet: dict, interface) -> None:
        """Handle received packet from Meshtastic device."""
        try:
            decoded = packet.get("decoded", {})
            from_num = packet.get("from", 0)
            to_num = packet.get("to", 0)
            portnum = decoded.get("portnum", "UNKNOWN")

            # Track node from packet (always, even for filtered messages)
            self._track_node_from_packet(packet, decoded, portnum)

            # Record topology route
            if from_num and to_num:
                self.record_message_route(
                    self._format_node_id(from_num),
                    self._format_node_id(to_num),
                    packet.get("hopLimit"),
                )

            # Parse traceroute responses
            if portnum == "TRACEROUTE_APP":
                self._handle_traceroute_response(packet, decoded)

            # Handle ACK/NAK for message delivery tracking
            if portnum == "ROUTING_APP":
                self._handle_routing_packet(packet, decoded)

            # Handle neighbor info for mesh topology
            if portnum == "NEIGHBOR_INFO_APP":
                self._handle_neighbor_info(packet, decoded)

            # Skip callback if none set
            if not self._callback:
                return

            # Filter out internal protocol messages that aren't useful to users
            ignored_portnums = {
                "ROUTING_APP",  # Mesh routing/acknowledgments - handled above
                "ADMIN_APP",  # Admin commands
                "REPLY_APP",  # Internal replies
                "STORE_FORWARD_APP",  # Store and forward protocol
                "RANGE_TEST_APP",  # Range testing
                "PAXCOUNTER_APP",  # People counter
                "REMOTE_HARDWARE_APP",  # Remote hardware control
                "SIMULATOR_APP",  # Simulator
                "MAP_REPORT_APP",  # Map reporting
                "TELEMETRY_APP",  # Device telemetry (battery, etc.) - too noisy
                "POSITION_APP",  # Position updates - used for map, not messages
                "NODEINFO_APP",  # Node info - used for tracking, not messages
                "NEIGHBOR_INFO_APP",  # Neighbor info - handled above
            }
            if portnum in ignored_portnums:
                logger.debug(f"Ignoring {portnum} message from {from_num}")
                return

            # Extract text message if present
            message = None
            if portnum == "TEXT_MESSAGE_APP":
                message = decoded.get("text")
            elif portnum in ("WAYPOINT_APP", "TRACEROUTE_APP"):
                # Show these as informational messages
                message = f"[{portnum}]"
            elif "payload" in decoded:
                # For other message types, include payload info
                message = f"[{portnum}]"

            # Look up node names - try cache first, then SDK's nodeDB
            from_name = self._lookup_node_name(from_num)
            to_name = self._lookup_node_name(to_num) if to_num != BROADCAST_ADDR else None

            msg = MeshtasticMessage(
                from_id=self._format_node_id(from_num),
                to_id=self._format_node_id(to_num),
                message=message,
                portnum=portnum,
                channel=packet.get("channel", 0),
                rssi=packet.get("rxRssi"),
                snr=packet.get("rxSnr"),
                hop_limit=packet.get("hopLimit"),
                timestamp=datetime.now(timezone.utc),
                from_name=from_name,
                to_name=to_name,
                raw_packet=packet,
            )

            self._callback(msg)
            logger.debug(f"Received: {msg.from_id} -> {msg.to_id}: {msg.portnum}")

        except Exception as e:
            logger.error(f"Error processing Meshtastic packet: {e}")

    def _is_local_node(self, node_num: int) -> bool:
        my_info = getattr(self._interface, "myInfo", None)
        my_num = getattr(my_info, "my_node_num", None)
        return my_num is not None and node_num == my_num

    def _track_node_from_packet(self, packet: dict, decoded: dict, portnum: str) -> None:
        """Update node tracking from received packet."""
        from_num = packet.get("from", 0)
        if from_num == 0 or from_num == 0xFFFFFFFF:
            return

        now = datetime.now(timezone.utc)

        # Get or create node entry
        if from_num not in self._nodes:
            self._nodes[from_num] = MeshNode(
                num=from_num,
                user_id=f"!{from_num:08x}",
                long_name="",
                short_name="",
                hw_model="UNKNOWN",
            )

        node = self._nodes[from_num]
        node.last_heard = now
        node.snr = packet.get("rxSnr", node.snr)

        # Parse NODEINFO_APP for user details
        if portnum == "NODEINFO_APP":
            user = decoded.get("user", {})
            if user:
                node.long_name = user.get("longName", node.long_name)
                node.short_name = user.get("shortName", node.short_name)
                node.hw_model = user.get("hwModel", node.hw_model)
                if user.get("id"):
                    node.user_id = user.get("id")

        # Parse POSITION_APP for location
        elif portnum == "POSITION_APP":
            position = decoded.get("position", {})
            if position:
                lat = position.get("latitude") or position.get("latitudeI")
                lon = position.get("longitude") or position.get("longitudeI")

                # Handle integer format (latitudeI/longitudeI are in 1e-7 degrees)
                if isinstance(lat, int) and abs(lat) > 1000:
                    lat = lat / 1e7
                if isinstance(lon, int) and abs(lon) > 1000:
                    lon = lon / 1e7

                if lat is not None and lon is not None:
                    node.latitude = lat
                    node.longitude = lon
                    node.altitude = position.get("altitude", node.altitude)
                    node.sats_in_view = position.get("satsInView", node.sats_in_view)

                    try:
                        from utils.event_pipeline import process_event

                        process_event(
                            "meshtastic",
                            {
                                "id": node.user_id,
                                "callsign": node.long_name or node.short_name,
                                "lat": node.latitude,
                                "lon": node.longitude,
                                "altitude": node.altitude,
                            },
                            "position",
                        )
                    except Exception:
                        # Event pipeline failures should never break mesh packet handling
                        pass

                    if self._is_local_node(from_num):
                        try:
                            from utils.gps import GPSPosition, set_external_position

                            set_external_position(
                                GPSPosition(
                                    latitude=node.latitude,
                                    longitude=node.longitude,
                                    altitude=node.altitude,
                                    fix_quality=3,
                                    timestamp=now,
                                    device=f"meshtastic:{node.user_id}",
                                )
                            )
                        except Exception:
                            # GPS forwarding failures should never break mesh packet handling
                            pass

        # Parse TELEMETRY_APP for battery and other metrics
        elif portnum == "TELEMETRY_APP":
            telemetry = decoded.get("telemetry", {})

            # Device metrics
            device_metrics = telemetry.get("deviceMetrics", {})
            if device_metrics:
                battery = device_metrics.get("batteryLevel")
                if battery is not None:
                    node.battery_level = battery
                voltage = device_metrics.get("voltage")
                if voltage is not None:
                    node.voltage = voltage
                channel_util = device_metrics.get("channelUtilization")
                if channel_util is not None:
                    node.channel_utilization = channel_util
                air_util = device_metrics.get("airUtilTx")
                if air_util is not None:
                    node.air_util_tx = air_util

            # Environment metrics
            env_metrics = telemetry.get("environmentMetrics", {})
            if env_metrics:
                temp = env_metrics.get("temperature")
                if temp is not None:
                    node.temperature = temp
                humidity = env_metrics.get("relativeHumidity")
                if humidity is not None:
                    node.humidity = humidity
                pressure = env_metrics.get("barometricPressure")
                if pressure is not None:
                    node.barometric_pressure = pressure

            # Store telemetry point for historical graphing
            self._store_telemetry_point(from_num, device_metrics, env_metrics)

    def _store_telemetry_point(self, node_num: int, device_metrics: dict, env_metrics: dict) -> None:
        """Store a telemetry data point for historical graphing."""
        # Skip if no actual data
        if not device_metrics and not env_metrics:
            return

        point = TelemetryPoint(
            timestamp=datetime.now(timezone.utc),
            battery_level=device_metrics.get("batteryLevel"),
            voltage=device_metrics.get("voltage"),
            temperature=env_metrics.get("temperature"),
            humidity=env_metrics.get("relativeHumidity"),
            pressure=env_metrics.get("barometricPressure"),
            channel_utilization=device_metrics.get("channelUtilization"),
            air_util_tx=device_metrics.get("airUtilTx"),
        )

        # Initialize deque for this node if needed
        if node_num not in self._telemetry_history:
            self._telemetry_history[node_num] = deque(maxlen=self._max_telemetry_points)

        self._telemetry_history[node_num].append(point)

    def _lookup_node_name(self, node_num: int) -> str | None:
        """Look up a node's name by its number."""
        if node_num == 0 or node_num == BROADCAST_ADDR:
            return None

        # Try our cache first
        if node_num in self._nodes:
            node = self._nodes[node_num]
            name = node.short_name or node.long_name
            if name:
                return name

        # Try SDK's nodeDB with various key formats
        if self._interface and hasattr(self._interface, "nodes") and self._interface.nodes:
            nodes = self._interface.nodes

            # Try direct lookup with different key formats
            for key in [node_num, f"!{node_num:08x}", f"!{node_num:x}", str(node_num)]:
                if key in nodes:
                    user = nodes[key].get("user", {})
                    name = user.get("shortName") or user.get("longName")
                    if name:
                        logger.debug(f"Found name '{name}' for node {node_num} with key {key}")
                        return name

            # Search through all nodes by num field
            for key, node_data in nodes.items():
                if node_data.get("num") == node_num:
                    user = node_data.get("user", {})
                    name = user.get("shortName") or user.get("longName")
                    if name:
                        logger.debug(f"Found name '{name}' for node {node_num} by search")
                        return name

        return None

    @staticmethod
    def _format_node_id(node_num: int) -> str:
        """Format node number as hex string."""
        if node_num == 0xFFFFFFFF:
            return "^all"
        return f"!{node_num:08x}"

    def get_node_info(self) -> NodeInfo | None:
        """Get local node information."""
        if not self._interface:
            return None
        try:
            node = self._interface.getMyNodeInfo()
            user = node.get("user", {})
            position = node.get("position", {})

            return NodeInfo(
                num=node.get("num", 0),
                user_id=user.get("id", ""),
                long_name=user.get("longName", ""),
                short_name=user.get("shortName", ""),
                hw_model=user.get("hwModel", "UNKNOWN"),
                latitude=position.get("latitude"),
                longitude=position.get("longitude"),
                altitude=position.get("altitude"),
                sats_in_view=position.get("satsInView"),
            )
        except Exception as e:
            logger.error(f"Error getting node info: {e}")
            return None

    def get_nodes(self) -> list[MeshNode]:
        """Get all tracked nodes."""
        # Also pull nodes from the SDK's nodeDB if available
        self._sync_nodes_from_interface()
        return list(self._nodes.values())

    def _sync_nodes_from_interface(self) -> None:
        """Sync nodes from the Meshtastic SDK's nodeDB."""
        if not self._interface:
            return

        try:
            nodes = self._interface.nodes
            if not nodes:
                return

            for node_id, node_data in nodes.items():
                # Skip if it's a string key like '!abcd1234'
                if isinstance(node_id, str):
                    try:
                        num = int(node_id[1:], 16) if node_id.startswith("!") else int(node_id)
                    except ValueError:
                        continue
                else:
                    num = node_id

                user = node_data.get("user", {})
                position = node_data.get("position", {})

                # Get or create node
                if num not in self._nodes:
                    self._nodes[num] = MeshNode(
                        num=num,
                        user_id=user.get("id", f"!{num:08x}"),
                        long_name=user.get("longName", ""),
                        short_name=user.get("shortName", ""),
                        hw_model=user.get("hwModel", "UNKNOWN"),
                    )

                node = self._nodes[num]

                # Update from SDK data
                if user:
                    node.long_name = user.get("longName", node.long_name) or node.long_name
                    node.short_name = user.get("shortName", node.short_name) or node.short_name
                    node.hw_model = user.get("hwModel", node.hw_model) or node.hw_model
                    if user.get("id"):
                        node.user_id = user.get("id")

                if position:
                    lat = position.get("latitude")
                    lon = position.get("longitude")
                    if lat is not None and lon is not None:
                        node.latitude = lat
                        node.longitude = lon
                        node.altitude = position.get("altitude", node.altitude)
                        node.sats_in_view = position.get("satsInView", node.sats_in_view)

                # Update last heard from SDK
                last_heard = node_data.get("lastHeard")
                if last_heard:
                    node.last_heard = datetime.fromtimestamp(last_heard, tz=timezone.utc)

                # Update SNR
                node.snr = node_data.get("snr", node.snr)

        except Exception as e:
            logger.error(f"Error syncing nodes from interface: {e}")

    def get_channels(self) -> list[ChannelConfig]:
        """Get all configured channels."""
        if not self._interface:
            return []

        channels = []
        try:
            for i, ch in enumerate(self._interface.localNode.channels):
                if ch.role != 0:  # 0 = DISABLED
                    channels.append(
                        ChannelConfig(
                            index=i,
                            name=ch.settings.name or f"Channel {i}",
                            psk=bytes(ch.settings.psk) if ch.settings.psk else b"",
                            role=ch.role,
                        )
                    )
        except Exception as e:
            logger.error(f"Error getting channels: {e}")
        return channels

    def send_text(self, text: str, channel: int = 0, destination: str | int | None = None) -> tuple[bool, str]:
        """
        Send a text message to the mesh network.

        Args:
            text: Message text (max 237 characters)
            channel: Channel index to send on (0-7)
            destination: Target node ID (string like "!a1b2c3d4" or int).
                        None or "^all" for broadcast.

        Returns:
            Tuple of (success, error_message)
        """
        if not self._interface:
            return False, "Not connected to device"

        if not text or len(text) > 237:
            return False, "Message must be 1-237 characters"

        try:
            # Parse destination - use broadcast address for None/^all
            dest_id = BROADCAST_ADDR  # Default to broadcast

            if destination:
                if isinstance(destination, int):
                    dest_id = destination
                elif destination == "^all":
                    dest_id = BROADCAST_ADDR
                elif destination.startswith("!"):
                    dest_id = int(destination[1:], 16)
                else:
                    # Try parsing as integer
                    try:
                        dest_id = int(destination)
                    except ValueError:
                        return False, f"Invalid destination: {destination}"

            # Send the message using sendData for more control
            logger.debug(f"Calling sendData: text='{text[:30]}', dest={dest_id}, channel={channel}")

            # Use sendData with TEXT_MESSAGE_APP portnum
            # This gives us more control over the packet
            from meshtastic import portnums_pb2

            self._interface.sendData(
                text.encode("utf-8"),
                destinationId=dest_id,
                portNum=portnums_pb2.PortNum.TEXT_MESSAGE_APP,
                channelIndex=channel,
            )
            logger.debug("sendData completed")

            dest_str = "^all" if dest_id == BROADCAST_ADDR else f"!{dest_id:08x}"
            logger.info(f"Sent message to {dest_str} on channel {channel}: {text[:50]}...")
            return True, None

        except Exception as e:
            logger.error(f"Error sending message: {e}")
            return False, str(e)

    def set_channel(self, index: int, name: str | None = None, psk: str | None = None) -> tuple[bool, str]:
        """
        Configure a channel with encryption key.

        Args:
            index: Channel index (0-7)
            name: Channel name (optional)
            psk: Pre-shared key in one of these formats:
                 - "none" - disable encryption
                 - "default" - use default (public) key
                 - "random" - generate new AES-256 key
                 - "base64:..." - base64-encoded key (16 or 32 bytes)
                 - "0x..." - hex-encoded key (16 or 32 bytes)
                 - "simple:passphrase" - derive key from passphrase (AES-256)

        Returns:
            Tuple of (success, message)
        """
        if not self._interface:
            return False, "Not connected to device"

        if not 0 <= index <= 7:
            return False, f"Invalid channel index: {index}. Must be 0-7."

        try:
            ch = self._interface.localNode.channels[index]

            if name is not None:
                ch.settings.name = name

            if psk is not None:
                psk_bytes = self._parse_psk(psk)
                if psk_bytes is None:
                    return False, f"Invalid PSK format: {psk}"
                ch.settings.psk = psk_bytes

            # Enable channel if it was disabled
            if ch.role == 0:
                ch.role = 2  # SECONDARY (1 = PRIMARY, only one allowed)

            # Write config to device
            self._interface.localNode.writeChannel(index)
            logger.info(f"Channel {index} configured: {name or ch.settings.name}")
            return True, f"Channel {index} configured successfully"

        except Exception as e:
            logger.error(f"Error setting channel: {e}")
            return False, str(e)

    def _parse_psk(self, psk: str) -> bytes | None:
        """
        Parse PSK string into bytes.

        Supported formats:
            - "none" - no encryption (empty key)
            - "default" - use default public key (1 byte)
            - "random" - generate random 32-byte AES-256 key
            - "base64:..." - base64-encoded key
            - "0x..." - hex-encoded key
            - "simple:passphrase" - SHA-256 hash of passphrase
        """
        psk = psk.strip()

        if psk.lower() == "none":
            return b""

        if psk.lower() == "default":
            # Default key (1 byte = use default)
            return b"\x01"

        if psk.lower() == "random":
            # Generate random 32-byte key
            return secrets.token_bytes(32)

        if psk.startswith("base64:"):
            try:
                decoded = base64.b64decode(psk[7:])
                if len(decoded) not in (0, 1, 16, 32):
                    logger.warning(f"PSK length {len(decoded)} is non-standard")
                return decoded
            except Exception:
                return None

        if psk.startswith("0x"):
            try:
                decoded = bytes.fromhex(psk[2:])
                if len(decoded) not in (0, 1, 16, 32):
                    logger.warning(f"PSK length {len(decoded)} is non-standard")
                return decoded
            except Exception:
                return None

        if psk.startswith("simple:"):
            # Hash passphrase to create 32-byte AES-256 key
            passphrase = psk[7:].encode("utf-8")
            return hashlib.sha256(passphrase).digest()

        # Try as raw base64 (for compatibility)
        try:
            decoded = base64.b64decode(psk)
            if len(decoded) in (0, 1, 16, 32):
                return decoded
        except Exception:
            pass

        return None

    def send_traceroute(self, destination: str | int, hop_limit: int = 7) -> tuple[bool, str]:
        """
        Send a traceroute request to a destination node.

        Args:
            destination: Target node ID (string like "!a1b2c3d4" or int)
            hop_limit: Maximum number of hops (1-7, default 7)

        Returns:
            Tuple of (success, error_message)
        """
        if not self._interface:
            return False, "Not connected to device"

        if not HAS_MESHTASTIC:
            return False, "Meshtastic SDK not installed"

        # Validate hop limit
        hop_limit = max(1, min(7, hop_limit))

        try:
            # Parse destination
            if isinstance(destination, int):
                dest_id = destination
            elif destination.startswith("!"):
                dest_id = int(destination[1:], 16)
            else:
                try:
                    dest_id = int(destination)
                except ValueError:
                    return False, f"Invalid destination: {destination}"

            if dest_id == BROADCAST_ADDR:
                return False, "Cannot traceroute to broadcast address"

            # Use the SDK's sendTraceRoute method
            logger.info(f"Sending traceroute to {self._format_node_id(dest_id)} with hop_limit={hop_limit}")
            self._interface.sendTraceRoute(dest_id, hopLimit=hop_limit)

            return True, None

        except Exception as e:
            logger.error(f"Error sending traceroute: {e}")
            return False, str(e)

    def _handle_traceroute_response(self, packet: dict, decoded: dict) -> None:
        """Handle incoming traceroute response."""
        try:
            from_num = packet.get("from", 0)
            route_discovery = decoded.get("routeDiscovery", {})

            # Extract route information
            route = route_discovery.get("route", [])
            route_back = route_discovery.get("routeBack", [])
            snr_towards = route_discovery.get("snrTowards", [])
            snr_back = route_discovery.get("snrBack", [])

            # Convert node numbers to IDs
            route_ids = [self._format_node_id(n) for n in route]
            route_back_ids = [self._format_node_id(n) for n in route_back]

            # Convert SNR values (stored as int8, need to convert)
            snr_towards_float = [float(s) / 4.0 if isinstance(s, int) else float(s) for s in snr_towards]
            snr_back_float = [float(s) / 4.0 if isinstance(s, int) else float(s) for s in snr_back]

            result = TracerouteResult(
                destination_id=self._format_node_id(from_num),
                route=route_ids,
                route_back=route_back_ids,
                snr_towards=snr_towards_float,
                snr_back=snr_back_float,
                timestamp=datetime.now(timezone.utc),
                success=len(route) > 0 or len(route_back) > 0,
            )

            # Store result
            self._traceroute_results.append(result)
            if len(self._traceroute_results) > self._max_traceroute_results:
                self._traceroute_results.pop(0)

            logger.info(
                f"Traceroute response from {result.destination_id}: route={route_ids}, route_back={route_back_ids}"
            )

        except Exception as e:
            logger.error(f"Error handling traceroute response: {e}")

    def get_traceroute_results(self, limit: int | None = None) -> list[TracerouteResult]:
        """
        Get recent traceroute results.

        Args:
            limit: Maximum number of results to return (None for all)

        Returns:
            List of TracerouteResult objects, most recent first
        """
        results = list(reversed(self._traceroute_results))
        if limit:
            results = results[:limit]
        return results

    def _handle_routing_packet(self, packet: dict, decoded: dict) -> None:
        """Handle ROUTING_APP packets for ACK/NAK tracking."""
        try:
            routing = decoded.get("routing", {})
            error_reason = routing.get("errorReason")
            request_id = packet.get("requestId", 0)

            if request_id and request_id in self._pending_messages:
                msg = self._pending_messages[request_id]
                if error_reason and error_reason != "NONE":
                    msg.status = "failed"
                    logger.debug(f"Message {request_id} failed: {error_reason}")
                else:
                    msg.status = "acked"
                    logger.debug(f"Message {request_id} acknowledged")
        except Exception as e:
            logger.error(f"Error handling routing packet: {e}")

    def _handle_neighbor_info(self, packet: dict, decoded: dict) -> None:
        """Handle NEIGHBOR_INFO_APP packets for mesh topology."""
        try:
            from_num = packet.get("from", 0)
            if from_num == 0:
                return

            neighbor_info = decoded.get("neighborinfo", {})
            neighbors = neighbor_info.get("neighbors", [])

            now = datetime.now(timezone.utc)
            neighbor_list = []

            for neighbor in neighbors:
                neighbor_num = neighbor.get("nodeId", 0)
                if neighbor_num:
                    neighbor_list.append(
                        NeighborInfo(
                            neighbor_num=neighbor_num,
                            neighbor_id=self._format_node_id(neighbor_num),
                            snr=neighbor.get("snr", 0.0),
                            timestamp=now,
                        )
                    )

            if neighbor_list:
                self._neighbors[from_num] = neighbor_list
                logger.debug(f"Updated neighbors for {self._format_node_id(from_num)}: {len(neighbor_list)} neighbors")

        except Exception as e:
            logger.error(f"Error handling neighbor info: {e}")

    def get_neighbors(self, node_num: int | None = None) -> dict[int, list[NeighborInfo]]:
        """
        Get neighbor information for mesh topology visualization.

        Args:
            node_num: Specific node number, or None for all nodes

        Returns:
            Dict mapping node_num to list of NeighborInfo
        """
        if node_num is not None:
            return {node_num: self._neighbors.get(node_num, [])}
        return dict(self._neighbors)

    def get_telemetry_history(self, node_num: int, hours: int = 24) -> list[TelemetryPoint]:
        """
        Get telemetry history for a node.

        Args:
            node_num: Node number to get history for
            hours: Number of hours of history to return

        Returns:
            List of TelemetryPoint objects
        """
        if node_num not in self._telemetry_history:
            return []

        cutoff = datetime.now(timezone.utc).timestamp() - (hours * 3600)
        return [p for p in self._telemetry_history[node_num] if p.timestamp.timestamp() > cutoff]

    def get_pending_messages(self) -> dict[int, PendingMessage]:
        """Get all pending messages waiting for ACK."""
        return dict(self._pending_messages)

    def request_position(self, destination: str | int) -> tuple[bool, str]:
        """
        Request position from a specific node.

        Args:
            destination: Target node ID (string like "!a1b2c3d4" or int)

        Returns:
            Tuple of (success, error_message)
        """
        if not self._interface:
            return False, "Not connected to device"

        if not HAS_MESHTASTIC:
            return False, "Meshtastic SDK not installed"

        try:
            # Parse destination
            if isinstance(destination, int):
                dest_id = destination
            elif destination.startswith("!"):
                dest_id = int(destination[1:], 16)
            else:
                try:
                    dest_id = int(destination)
                except ValueError:
                    return False, f"Invalid destination: {destination}"

            if dest_id == BROADCAST_ADDR:
                return False, "Cannot request position from broadcast address"

            # Send position request using admin message
            # The Meshtastic SDK's localNode.requestPosition works for the local node
            # For remote nodes, we send a POSITION_APP request
            from meshtastic import portnums_pb2

            # Request position by sending an empty position request packet
            self._interface.sendData(
                b"",  # Empty payload triggers position response
                destinationId=dest_id,
                portNum=portnums_pb2.PortNum.POSITION_APP,
                wantAck=True,
                wantResponse=True,
            )

            logger.info(f"Sent position request to {self._format_node_id(dest_id)}")
            return True, None

        except Exception as e:
            logger.error(f"Error requesting position: {e}")
            return False, str(e)

    def check_firmware(self) -> dict:
        """
        Check current firmware version and compare to latest release.

        Returns:
            Dict with current_version, latest_version, update_available, release_url
        """
        result = {
            "current_version": None,
            "latest_version": None,
            "update_available": False,
            "release_url": None,
            "error": None,
        }

        # Get current firmware version from device
        if self._interface:
            try:
                my_info = self._interface.getMyNodeInfo()
                if my_info:
                    my_info.get("deviceMetrics", {})
                    # Firmware version is in the user section or metadata
                    if "firmware_version" in my_info:
                        self._firmware_version = my_info["firmware_version"]
                    elif hasattr(self._interface, "myInfo") and self._interface.myInfo:
                        self._firmware_version = getattr(self._interface.myInfo, "firmware_version", None)
                    result["current_version"] = self._firmware_version
            except Exception as e:
                logger.warning(f"Could not get device firmware version: {e}")

        # Check GitHub for latest release (cache for 15 minutes)
        now = datetime.now(timezone.utc)
        cache_valid = (
            self._firmware_check_time
            and self._latest_firmware
            and (now - self._firmware_check_time).total_seconds() < 900
        )

        if not cache_valid:
            try:
                url = "https://api.github.com/repos/meshtastic/firmware/releases/latest"
                req = urllib.request.Request(url, headers={"User-Agent": "INTERCEPT"})
                with urllib.request.urlopen(req, timeout=10) as response:
                    data = json.loads(response.read().decode("utf-8"))
                    self._latest_firmware = {
                        "version": data.get("tag_name", "").lstrip("v"),
                        "url": data.get("html_url"),
                        "name": data.get("name"),
                    }
                    self._firmware_check_time = now
            except Exception as e:
                logger.warning(f"Could not check latest firmware: {e}")
                result["error"] = str(e)

        if self._latest_firmware:
            result["latest_version"] = self._latest_firmware.get("version")
            result["release_url"] = self._latest_firmware.get("url")

            # Compare versions
            if result["current_version"] and result["latest_version"]:
                result["update_available"] = self._compare_versions(result["current_version"], result["latest_version"])

        return result

    def _compare_versions(self, current: str, latest: str) -> bool:
        """Compare semver versions, return True if update available."""
        try:

            def parse_version(v: str) -> tuple:
                # Strip any leading 'v' and split by dots
                v = v.lstrip("v").split("-")[0]  # Remove pre-release suffix
                parts = v.split(".")
                return tuple(int(p) for p in parts[:3])

            current_parts = parse_version(current)
            latest_parts = parse_version(latest)
            return latest_parts > current_parts
        except Exception:
            return False

    def generate_channel_qr(self, channel_index: int) -> bytes | None:
        """
        Generate QR code for a channel configuration.

        Args:
            channel_index: Channel index (0-7)

        Returns:
            PNG image bytes, or None on error
        """
        try:
            from io import BytesIO

            import qrcode
        except ImportError:
            logger.error("qrcode library not installed. Install with: pip install qrcode[pil]")
            return None

        if not self._interface:
            return None

        try:
            channels = self.get_channels()
            channel = None
            for ch in channels:
                if ch.index == channel_index:
                    channel = ch
                    break

            if not channel:
                logger.error(f"Channel {channel_index} not found")
                return None

            # Build Meshtastic URL
            # Format: https://meshtastic.org/e/#CgMSAQ... (base64 channel config)
            # The URL encodes the channel settings protobuf

            # For simplicity, we'll create a URL with the channel name and key info
            # The official format requires protobuf serialization
            channel_data = {
                "name": channel.name,
                "index": channel.index,
                "psk": base64.b64encode(channel.psk).decode("utf-8") if channel.psk else "",
            }

            # Encode as base64 JSON (simplified format)
            encoded = base64.urlsafe_b64encode(json.dumps(channel_data).encode("utf-8")).decode("utf-8")

            url = f"https://meshtastic.org/e/#{encoded}"

            # Generate QR code
            qr = qrcode.QRCode(
                version=1,
                error_correction=qrcode.constants.ERROR_CORRECT_L,
                box_size=10,
                border=4,
            )
            qr.add_data(url)
            qr.make(fit=True)

            img = qr.make_image(fill_color="black", back_color="white")

            # Convert to PNG bytes
            buffer = BytesIO()
            img.save(buffer, format="PNG")
            return buffer.getvalue()

        except Exception as e:
            logger.error(f"Error generating QR code: {e}")
            return None

    def start_range_test(self, count: int = 10, interval: int = 5) -> tuple[bool, str]:
        """
        Start a range test by sending test packets.

        Args:
            count: Number of test packets to send
            interval: Seconds between packets

        Returns:
            Tuple of (success, error_message)
        """
        if not self._interface:
            return False, "Not connected to device"

        if not HAS_MESHTASTIC:
            return False, "Meshtastic SDK not installed"

        if self._range_test_running:
            return False, "Range test already running"

        try:
            from meshtastic import portnums_pb2

            self._range_test_running = True
            self._range_test_results = []

            # Send range test packets in a background thread
            import threading

            def send_packets():
                import time

                for i in range(count):
                    if not self._range_test_running:
                        break

                    try:
                        # Send range test packet with sequence number
                        payload = f"RangeTest #{i + 1}".encode()
                        self._interface.sendData(
                            payload,
                            destinationId=BROADCAST_ADDR,
                            portNum=portnums_pb2.PortNum.RANGE_TEST_APP,
                        )
                        logger.info(f"Range test packet {i + 1}/{count} sent")
                    except Exception as e:
                        logger.error(f"Error sending range test packet: {e}")

                    if i < count - 1 and self._range_test_running:
                        time.sleep(interval)

                self._range_test_running = False
                logger.info("Range test complete")

            thread = threading.Thread(target=send_packets, daemon=True)
            thread.start()

            return True, None

        except Exception as e:
            self._range_test_running = False
            logger.error(f"Error starting range test: {e}")
            return False, str(e)

    def stop_range_test(self) -> None:
        """Stop an ongoing range test."""
        self._range_test_running = False

    def get_range_test_status(self) -> dict:
        """Get range test status."""
        return {
            "running": self._range_test_running,
            "results": self._range_test_results,
        }

    def request_store_forward(self, window_minutes: int = 60) -> tuple[bool, str]:
        """
        Request missed messages from a Store & Forward router.

        Args:
            window_minutes: Minutes of history to request

        Returns:
            Tuple of (success, error_message)
        """
        if not self._interface:
            return False, "Not connected to device"

        if not HAS_MESHTASTIC:
            return False, "Meshtastic SDK not installed"

        try:
            from meshtastic import portnums_pb2, storeforward_pb2

            # Find S&F router (look for nodes with router role)
            router_num = None
            if self._interface.nodes:
                for node_id, node_data in self._interface.nodes.items():
                    # Check for router role
                    role = node_data.get("user", {}).get("role")
                    if role in ("ROUTER", "ROUTER_CLIENT"):
                        if isinstance(node_id, str) and node_id.startswith("!"):
                            router_num = int(node_id[1:], 16)
                        elif isinstance(node_id, int):
                            router_num = node_id
                        break

            if not router_num:
                return False, "No Store & Forward router found on mesh"

            # Build S&F history request
            sf_request = storeforward_pb2.StoreAndForward()
            sf_request.rr = storeforward_pb2.StoreAndForward.RequestResponse.CLIENT_HISTORY
            sf_request.history.window = window_minutes * 60  # Convert to seconds

            self._interface.sendData(
                sf_request.SerializeToString(),
                destinationId=router_num,
                portNum=portnums_pb2.PortNum.STORE_FORWARD_APP,
            )

            logger.info(f"Requested S&F history from {self._format_node_id(router_num)} for {window_minutes} minutes")
            return True, None

        except ImportError:
            return False, "Store & Forward protobuf not available"
        except Exception as e:
            logger.error(f"Error requesting S&F history: {e}")
            return False, str(e)

    def check_store_forward_available(self) -> dict:
        """
        Check if a Store & Forward router is available.

        Returns:
            Dict with available status and router info
        """
        result = {
            "available": False,
            "router_id": None,
            "router_name": None,
        }

        if not self._interface or not self._interface.nodes:
            return result

        for node_id, node_data in self._interface.nodes.items():
            role = node_data.get("user", {}).get("role")
            if role in ("ROUTER", "ROUTER_CLIENT"):
                result["available"] = True
                if isinstance(node_id, str):
                    result["router_id"] = node_id
                else:
                    result["router_id"] = self._format_node_id(node_id)
                result["router_name"] = node_data.get("user", {}).get("shortName")
                break

        return result


# Global client instance
_client: MeshtasticClient | None = None


def get_meshtastic_client() -> MeshtasticClient | None:
    """Get the global Meshtastic client instance."""
    return _client


def start_meshtastic(
    device: str | None = None,
    callback: Callable[[MeshtasticMessage], None] | None = None,
    connection_type: str = "serial",
    hostname: str | None = None,
) -> bool:
    """
    Start the Meshtastic client.

    Args:
        device: Serial port path (optional, auto-discovers if not provided)
        callback: Function to call when messages are received
        connection_type: Connection type - 'serial' or 'tcp' (default: 'serial')
        hostname: Hostname or IP address for TCP connections

    Returns:
        True if started successfully
    """
    global _client

    if _client and _client.is_running:
        return True

    _client = MeshtasticClient()
    if callback:
        _client.set_callback(callback)

    return _client.connect(device, connection_type=connection_type, hostname=hostname)


def stop_meshtastic() -> None:
    """Stop the Meshtastic client."""
    global _client
    if _client:
        _client.disconnect()
        _client = None


def is_meshtastic_available() -> bool:
    """Check if Meshtastic SDK is installed."""
    return HAS_MESHTASTIC
