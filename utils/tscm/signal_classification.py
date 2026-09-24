"""
Signal Classification Module

Translates technical RF measurements (RSSI, duration) into confidence-safe,
client-facing language suitable for reports and dashboards.

All outputs use hedged language that avoids absolute claims.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

# =============================================================================
# Signal Strength Classification
# =============================================================================


class SignalStrength(Enum):
    """Qualitative signal strength labels."""

    MINIMAL = "minimal"
    WEAK = "weak"
    MODERATE = "moderate"
    STRONG = "strong"
    VERY_STRONG = "very_strong"


# RSSI thresholds (dBm) - upper bounds for each category
RSSI_THRESHOLDS = {
    SignalStrength.MINIMAL: -85,  # -100 to -85
    SignalStrength.WEAK: -70,  # -84 to -70
    SignalStrength.MODERATE: -55,  # -69 to -55
    SignalStrength.STRONG: -40,  # -54 to -40
    SignalStrength.VERY_STRONG: 0,  # > -40
}

SIGNAL_STRENGTH_DESCRIPTIONS = {
    SignalStrength.MINIMAL: {
        "label": "Minimal",
        "description": "At detection threshold",
        "interpretation": "ambient noise or a distant source",
        "confidence": "low",
    },
    SignalStrength.WEAK: {
        "label": "Weak",
        "description": "Detectable signal",
        "interpretation": "a distant or obstructed source",
        "confidence": "low",
    },
    SignalStrength.MODERATE: {
        "label": "Moderate",
        "description": "Consistent presence",
        "interpretation": "a source in proximity",
        "confidence": "medium",
    },
    SignalStrength.STRONG: {
        "label": "Strong",
        "description": "Clear signal",
        "interpretation": "a source in close proximity",
        "confidence": "medium",
    },
    SignalStrength.VERY_STRONG: {
        "label": "Very Strong",
        "description": "High signal level",
        "interpretation": "a nearby source",
        "confidence": "high",
    },
}


def classify_signal_strength(rssi: float | int | None) -> SignalStrength:
    """
    Classify RSSI value into qualitative signal strength.

    Args:
        rssi: Signal strength in dBm (typically -100 to 0)

    Returns:
        SignalStrength enum value
    """
    if rssi is None:
        return SignalStrength.MINIMAL

    try:
        rssi_val = float(rssi)
    except (ValueError, TypeError):
        return SignalStrength.MINIMAL

    if rssi_val <= -85:
        return SignalStrength.MINIMAL
    elif rssi_val <= -70:
        return SignalStrength.WEAK
    elif rssi_val <= -55:
        return SignalStrength.MODERATE
    elif rssi_val <= -40:
        return SignalStrength.STRONG
    else:
        return SignalStrength.VERY_STRONG


def get_signal_strength_info(rssi: float | int | None) -> dict:
    """
    Get full signal strength classification with metadata.

    Returns dict with: strength, label, description, interpretation, confidence
    """
    strength = classify_signal_strength(rssi)
    info = SIGNAL_STRENGTH_DESCRIPTIONS[strength].copy()
    info["strength"] = strength.value
    info["rssi"] = rssi
    return info


# =============================================================================
# Detection Duration / Confidence Modifiers
# =============================================================================


class DetectionDuration(Enum):
    """Qualitative duration labels."""

    TRANSIENT = "transient"
    SHORT = "short"
    SUSTAINED = "sustained"
    PERSISTENT = "persistent"


# Duration thresholds (seconds)
DURATION_THRESHOLDS = {
    DetectionDuration.TRANSIENT: 5,  # < 5 seconds
    DetectionDuration.SHORT: 30,  # 5-30 seconds
    DetectionDuration.SUSTAINED: 120,  # 30s - 2 min
    DetectionDuration.PERSISTENT: float("inf"),  # > 2 min
}

DURATION_DESCRIPTIONS = {
    DetectionDuration.TRANSIENT: {
        "label": "Transient",
        "modifier": "briefly observed",
        "confidence_impact": "reduces confidence",
    },
    DetectionDuration.SHORT: {
        "label": "Short-duration",
        "modifier": "observed for a short period",
        "confidence_impact": "limited confidence",
    },
    DetectionDuration.SUSTAINED: {
        "label": "Sustained",
        "modifier": "observed over a sustained period",
        "confidence_impact": "supports confidence",
    },
    DetectionDuration.PERSISTENT: {
        "label": "Persistent",
        "modifier": "continuously observed",
        "confidence_impact": "increases confidence",
    },
}


def classify_duration(seconds: float | int | None) -> DetectionDuration:
    """
    Classify detection duration into qualitative category.

    Args:
        seconds: Duration of detection in seconds

    Returns:
        DetectionDuration enum value
    """
    if seconds is None or seconds < 0:
        return DetectionDuration.TRANSIENT

    try:
        duration = float(seconds)
    except (ValueError, TypeError):
        return DetectionDuration.TRANSIENT

    if duration < 5:
        return DetectionDuration.TRANSIENT
    elif duration < 30:
        return DetectionDuration.SHORT
    elif duration < 120:
        return DetectionDuration.SUSTAINED
    else:
        return DetectionDuration.PERSISTENT


def get_duration_info(seconds: float | int | None) -> dict:
    """Get full duration classification with metadata."""
    duration = classify_duration(seconds)
    info = DURATION_DESCRIPTIONS[duration].copy()
    info["duration"] = duration.value
    info["seconds"] = seconds
    return info


# =============================================================================
# Combined Confidence Assessment
# =============================================================================


class ConfidenceLevel(Enum):
    """Overall detection confidence."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass
class SignalAssessment:
    """Complete signal assessment with confidence-safe language."""

    rssi: float | None
    duration_seconds: float | None
    observation_count: int

    signal_strength: SignalStrength
    detection_duration: DetectionDuration
    confidence: ConfidenceLevel

    # Client-safe descriptions
    strength_label: str
    duration_label: str
    summary: str
    interpretation: str
    caveats: list[str]


def assess_signal(
    rssi: float | int | None = None,
    duration_seconds: float | int | None = None,
    observation_count: int = 1,
    has_corroborating_data: bool = False,
) -> SignalAssessment:
    """
    Produce a complete signal assessment with confidence-safe language.

    Args:
        rssi: Signal strength in dBm
        duration_seconds: How long signal was detected
        observation_count: Number of separate observations
        has_corroborating_data: Whether other data supports this detection

    Returns:
        SignalAssessment with hedged, client-safe language
    """
    strength = classify_signal_strength(rssi)
    duration = classify_duration(duration_seconds)

    # Calculate confidence based on multiple factors
    confidence = _calculate_confidence(strength, duration, observation_count, has_corroborating_data)

    strength_info = SIGNAL_STRENGTH_DESCRIPTIONS[strength]
    duration_info = DURATION_DESCRIPTIONS[duration]

    # Build client-safe summary
    summary = _build_summary(strength, duration, confidence)
    interpretation = _build_interpretation(strength, duration, confidence)
    caveats = _build_caveats(strength, duration, confidence)

    return SignalAssessment(
        rssi=rssi,
        duration_seconds=duration_seconds,
        observation_count=observation_count,
        signal_strength=strength,
        detection_duration=duration,
        confidence=confidence,
        strength_label=strength_info["label"],
        duration_label=duration_info["label"],
        summary=summary,
        interpretation=interpretation,
        caveats=caveats,
    )


def _calculate_confidence(
    strength: SignalStrength,
    duration: DetectionDuration,
    observation_count: int,
    has_corroborating_data: bool,
) -> ConfidenceLevel:
    """Calculate overall confidence from contributing factors."""
    score = 0

    # Signal strength contribution
    if strength in (SignalStrength.STRONG, SignalStrength.VERY_STRONG):
        score += 2
    elif strength == SignalStrength.MODERATE:
        score += 1

    # Duration contribution
    if duration == DetectionDuration.PERSISTENT:
        score += 2
    elif duration == DetectionDuration.SUSTAINED:
        score += 1

    # Observation count contribution
    if observation_count >= 5:
        score += 2
    elif observation_count >= 3:
        score += 1

    # Corroborating data bonus
    if has_corroborating_data:
        score += 1

    # Map score to confidence level
    if score >= 5:
        return ConfidenceLevel.HIGH
    elif score >= 3:
        return ConfidenceLevel.MEDIUM
    else:
        return ConfidenceLevel.LOW


def _build_summary(
    strength: SignalStrength,
    duration: DetectionDuration,
    confidence: ConfidenceLevel,
) -> str:
    """Build a confidence-safe summary statement."""
    strength_info = SIGNAL_STRENGTH_DESCRIPTIONS[strength]
    duration_info = DURATION_DESCRIPTIONS[duration]

    # Labels are title case ("Very Strong"); mid-sentence they are not.
    strength_label = strength_info["label"].capitalize()
    if confidence == ConfidenceLevel.HIGH:
        return (
            f"{strength_label}, {duration_info['label'].lower()} signal "
            f"with characteristics that suggest device presence in proximity"
        )
    elif confidence == ConfidenceLevel.MEDIUM:
        return f"{strength_label}, {duration_info['label'].lower()} signal that may indicate device activity"
    else:
        return f"{strength_label} signal, {duration_info['modifier']}, consistent with possible device presence"


def _build_interpretation(
    strength: SignalStrength,
    duration: DetectionDuration,
    confidence: ConfidenceLevel,
) -> str:
    """Build interpretation text with appropriate hedging."""
    strength_info = SIGNAL_STRENGTH_DESCRIPTIONS[strength]

    base = strength_info["interpretation"]  # a noun phrase: "a nearby source"

    if confidence == ConfidenceLevel.HIGH:
        return f"Observed signal characteristics suggest {base}"
    elif confidence == ConfidenceLevel.MEDIUM:
        return f"Signal pattern may indicate {base}"
    else:
        return f"Limited data; the signal could represent {base}, or environmental factors"


def _build_caveats(
    strength: SignalStrength,
    duration: DetectionDuration,
    confidence: ConfidenceLevel,
) -> list[str]:
    """Build list of relevant caveats for the assessment."""
    caveats = []

    # Always include general caveat
    caveats.append(
        "Signal strength is affected by environmental factors including walls, interference, and device orientation"
    )

    # Strength-specific caveats
    if strength in (SignalStrength.MINIMAL, SignalStrength.WEAK):
        caveats.append("Weak signals may represent background noise, distant devices, or heavily obstructed sources")

    # Duration-specific caveats
    if duration == DetectionDuration.TRANSIENT:
        caveats.append(
            "Brief detection may indicate passing device, intermittent transmission, or momentary interference"
        )

    # Confidence-specific caveats
    if confidence == ConfidenceLevel.LOW:
        caveats.append("Insufficient data for reliable assessment; additional observation recommended")

    return caveats


# =============================================================================
# Client-Facing Language Generators
# =============================================================================


def describe_signal_for_report(
    rssi: float | int | None,
    duration_seconds: float | int | None = None,
    observation_count: int = 1,
    protocol: str = "RF",
) -> dict:
    """
    Generate client-facing signal description for reports.

    Returns dict with:
        - headline: Short summary for quick scanning
        - description: Detailed description with hedged language
        - technical: Technical details (RSSI value, duration)
        - confidence: Confidence level
        - caveats: List of applicable caveats
    """
    assessment = assess_signal(rssi, duration_seconds, observation_count)

    # Estimate range (very approximate, with appropriate hedging)
    range_estimate = _estimate_range(rssi)

    return {
        "headline": f"{assessment.strength_label} {protocol} signal, {assessment.duration_label.lower()}",
        "description": assessment.summary,
        "interpretation": assessment.interpretation,
        "technical": {
            "rssi_dbm": rssi,
            "strength_category": assessment.signal_strength.value,
            "duration_seconds": duration_seconds,
            "duration_category": assessment.detection_duration.value,
            "observations": observation_count,
        },
        "range_estimate": range_estimate,
        "confidence": assessment.confidence.value,
        "confidence_factors": {
            "signal_strength": assessment.strength_label,
            "detection_duration": assessment.duration_label,
            "observation_count": observation_count,
        },
        "caveats": assessment.caveats,
    }


def _estimate_range(rssi: float | int | None) -> dict:
    """
    Estimate approximate range from RSSI with heavy caveats.

    Returns range as min/max estimate with disclaimer.
    """
    if rssi is None:
        return {
            "estimate": "Unknown",
            "disclaimer": "Insufficient signal data for range estimation",
        }

    try:
        rssi_val = float(rssi)
    except (ValueError, TypeError):
        return {
            "estimate": "Unknown",
            "disclaimer": "Invalid signal data",
        }

    # Very rough estimates based on free-space path loss
    # These are intentionally wide ranges due to environmental variability
    if rssi_val > -40:
        estimate = "< 3 meters"
        range_min, range_max = 0, 3
    elif rssi_val > -55:
        estimate = "3-10 meters"
        range_min, range_max = 3, 10
    elif rssi_val > -70:
        estimate = "5-20 meters"
        range_min, range_max = 5, 20
    elif rssi_val > -85:
        estimate = "10-50 meters"
        range_min, range_max = 10, 50
    else:
        estimate = "> 30 meters or heavily obstructed"
        range_min, range_max = 30, None

    return {
        "estimate": estimate,
        "range_min_meters": range_min,
        "range_max_meters": range_max,
        "disclaimer": (
            "Range estimates are approximate and significantly affected by "
            "walls, interference, antenna characteristics, and transmit power"
        ),
    }


def format_signal_for_dashboard(
    rssi: float | int | None,
    duration_seconds: float | int | None = None,
) -> dict:
    """
    Generate dashboard-friendly signal display data.

    Returns dict with:
        - label: Short label for display
        - color: Suggested color code
        - icon: Suggested icon name
        - tooltip: Hover text with details
    """
    strength = classify_signal_strength(rssi)
    duration = classify_duration(duration_seconds)

    colors = {
        SignalStrength.MINIMAL: "#888888",  # Gray
        SignalStrength.WEAK: "#6baed6",  # Light blue
        SignalStrength.MODERATE: "#3182bd",  # Blue
        SignalStrength.STRONG: "#fd8d3c",  # Orange
        SignalStrength.VERY_STRONG: "#e6550d",  # Red-orange
    }

    icons = {
        SignalStrength.MINIMAL: "signal-0",
        SignalStrength.WEAK: "signal-1",
        SignalStrength.MODERATE: "signal-2",
        SignalStrength.STRONG: "signal-3",
        SignalStrength.VERY_STRONG: "signal-4",
    }

    strength_info = SIGNAL_STRENGTH_DESCRIPTIONS[strength]
    duration_info = DURATION_DESCRIPTIONS[duration]

    tooltip = f"{strength_info['label']} signal ({rssi} dBm)"
    if duration_seconds is not None:
        tooltip += f", {duration_info['modifier']}"

    return {
        "label": strength_info["label"],
        "color": colors[strength],
        "icon": icons[strength],
        "tooltip": tooltip,
        "strength": strength.value,
        "duration": duration.value,
    }


# =============================================================================
# Hedged Language Patterns
# =============================================================================

# Vocabulary for generating hedged statements
HEDGED_VERBS = {
    "high_confidence": [
        "suggests",
        "indicates",
        "is consistent with",
    ],
    "medium_confidence": [
        "may indicate",
        "could suggest",
        "is potentially consistent with",
    ],
    "low_confidence": [
        "might represent",
        "could possibly indicate",
        "may or may not suggest",
    ],
}

HEDGED_CONCLUSIONS = {
    "device_presence": {
        "high": "likely device presence in proximity",
        "medium": "possible device activity",
        "low": "potential device presence, though environmental factors cannot be ruled out",
    },
    "surveillance_indicator": {
        "high": "characteristics warranting further investigation",
        "medium": "pattern that may warrant review",
        "low": "inconclusive pattern requiring additional data",
    },
    "location": {
        "high": "probable location within estimated range",
        "medium": "possible location in general vicinity",
        "low": "uncertain location; signal could originate from various distances",
    },
}


def generate_hedged_statement(
    subject: str,
    conclusion_type: str,
    confidence: ConfidenceLevel | str,
) -> str:
    """
    Generate a hedged statement for reports.

    Args:
        subject: What we're describing (e.g., "The detected WiFi signal")
        conclusion_type: Type of conclusion (device_presence, surveillance_indicator, location)
        confidence: Confidence level

    Returns:
        Hedged statement string
    """
    if isinstance(confidence, ConfidenceLevel):
        conf_key = confidence.value
    else:
        conf_key = str(confidence).lower()

    verbs = HEDGED_VERBS.get(f"{conf_key}_confidence", HEDGED_VERBS["low_confidence"])
    conclusions = HEDGED_CONCLUSIONS.get(conclusion_type, {})
    conclusion = conclusions.get(conf_key, conclusions.get("low", "an inconclusive pattern"))

    verb = verbs[0]  # Use first verb for consistency

    return f"{subject} {verb} {conclusion}"


# =============================================================================
# Standard Disclaimer Text
# =============================================================================

SIGNAL_ANALYSIS_DISCLAIMER = """
Signal analysis provides indicators for further investigation and should not be
interpreted as definitive identification of devices or their purposes. Environmental
factors such as building materials, electromagnetic interference, multipath
propagation, and device orientation significantly affect signal measurements.
All findings represent patterns observed at a specific point in time and location.
"""

RANGE_ESTIMATION_DISCLAIMER = """
Distance estimates are based on signal strength measurements and standard radio
propagation models. Actual distances may vary significantly due to transmit power
variations, antenna characteristics, physical obstructions, and environmental
conditions. These estimates should be considered approximate guidelines only.
"""
