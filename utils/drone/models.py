from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

_MAX_HISTORY_IN_DICT = 50
_MAX_RF_IN_DICT = 10


@dataclass
class RFSignal:
    frequency_hz: int
    protocol: str
    rssi: float
    hardware: str  # "RTL433" | "HACKRF"
    timestamp: datetime


@dataclass
class RemoteIDObservation:
    source: str  # "WIFI" | "BLE"
    serial_number: str
    operator_id: str
    lat: float
    lon: float
    altitude_m: float
    speed_ms: float
    heading: float
    timestamp: datetime


@dataclass
class RFObservation:
    frequency_hz: int
    protocol: str
    rssi: float
    hardware: str  # "RTL433" | "HACKRF"
    timestamp: datetime


@dataclass
class DroneContact:
    id: str
    first_seen: datetime
    last_seen: datetime
    serial_number: str | None = None
    operator_id: str | None = None
    position: tuple[float, float] | None = None
    altitude_m: float | None = None
    speed_ms: float | None = None
    heading: float | None = None
    position_history: list[tuple[float, float, datetime]] = field(default_factory=list)
    rf_signals: list[RFSignal] = field(default_factory=list)
    compliant: bool = False
    detection_vectors: set[str] = field(default_factory=set)
    confidence: float = 0.0
    risk_level: str = "low"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "first_seen": self.first_seen.isoformat(),
            "last_seen": self.last_seen.isoformat(),
            "serial_number": self.serial_number,
            "operator_id": self.operator_id,
            "position": list(self.position) if self.position else None,
            "altitude_m": self.altitude_m,
            "speed_ms": self.speed_ms,
            "heading": self.heading,
            "position_history": [
                {"lat": p[0], "lon": p[1], "ts": p[2].isoformat()}
                for p in self.position_history[-_MAX_HISTORY_IN_DICT:]
            ],
            "rf_signals": [
                {
                    "frequency_hz": s.frequency_hz,
                    "protocol": s.protocol,
                    "rssi": s.rssi,
                    "hardware": s.hardware,
                }
                for s in self.rf_signals[-_MAX_RF_IN_DICT:]
            ],
            "compliant": self.compliant,
            "detection_vectors": sorted(self.detection_vectors),
            "confidence": round(self.confidence, 2),
            "risk_level": self.risk_level,
        }
