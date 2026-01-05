"""
Hardware-specific parameter validation for SDR devices.

Validates frequency, gain, sample rate, and other parameters against
the capabilities of specific SDR hardware.
"""

from typing import Optional

from .base import SDRCapabilities, SDRDevice, SDRType


class SDRValidationError(ValueError):
    """Raised when SDR parameter validation fails."""

    pass


def validate_frequency(
    freq_mhz: float, device: Optional[SDRDevice] = None, capabilities: Optional[SDRCapabilities] = None
) -> float:
    """
    Validate frequency against device capabilities.

    Args:
        freq_mhz: Frequency in MHz
        device: SDR device (optional, takes precedence)
        capabilities: SDR capabilities (used if device not provided)

    Returns:
        Validated frequency in MHz

    Raises:
        SDRValidationError: If frequency is out of range
    """
    if device:
        caps = device.capabilities
    elif capabilities:
        caps = capabilities
    else:
        # Default RTL-SDR range for backwards compatibility
        caps = SDRCapabilities(
            sdr_type=SDRType.RTL_SDR, freq_min_mhz=24.0, freq_max_mhz=1766.0, gain_min=0.0, gain_max=50.0
        )

    if not caps.freq_min_mhz <= freq_mhz <= caps.freq_max_mhz:
        raise SDRValidationError(
            f"Frequency {freq_mhz} MHz out of range for {caps.sdr_type.value}. "
            f"Valid range: {caps.freq_min_mhz}-{caps.freq_max_mhz} MHz"
        )

    return freq_mhz


def validate_gain(
    gain: float, device: Optional[SDRDevice] = None, capabilities: Optional[SDRCapabilities] = None
) -> float:
    """
    Validate gain against device capabilities.

    Args:
        gain: Gain in dB
        device: SDR device (optional, takes precedence)
        capabilities: SDR capabilities (used if device not provided)

    Returns:
        Validated gain in dB

    Raises:
        SDRValidationError: If gain is out of range
    """
    if device:
        caps = device.capabilities
    elif capabilities:
        caps = capabilities
    else:
        # Default range for backwards compatibility
        caps = SDRCapabilities(
            sdr_type=SDRType.RTL_SDR, freq_min_mhz=24.0, freq_max_mhz=1766.0, gain_min=0.0, gain_max=50.0
        )

    # Allow 0 for auto gain
    if gain == 0:
        return gain

    if not caps.gain_min <= gain <= caps.gain_max:
        raise SDRValidationError(
            f"Gain {gain} dB out of range for {caps.sdr_type.value}. "
            f"Valid range: {caps.gain_min}-{caps.gain_max} dB"
        )

    return gain


def validate_sample_rate(
    rate: int,
    device: Optional[SDRDevice] = None,
    capabilities: Optional[SDRCapabilities] = None,
    snap_to_nearest: bool = True,
) -> int:
    """
    Validate sample rate against device capabilities.

    Args:
        rate: Sample rate in Hz
        device: SDR device (optional, takes precedence)
        capabilities: SDR capabilities (used if device not provided)
        snap_to_nearest: If True, return nearest valid rate instead of raising

    Returns:
        Validated sample rate in Hz

    Raises:
        SDRValidationError: If rate is invalid and snap_to_nearest is False
    """
    if device:
        caps = device.capabilities
    elif capabilities:
        caps = capabilities
    else:
        return rate  # No validation without capabilities

    if not caps.sample_rates:
        return rate  # No restrictions

    if rate in caps.sample_rates:
        return rate

    if snap_to_nearest:
        # Find closest valid rate
        closest = min(caps.sample_rates, key=lambda x: abs(x - rate))
        return closest

    raise SDRValidationError(
        f"Sample rate {rate} Hz not supported by {caps.sdr_type.value}. " f"Valid rates: {caps.sample_rates}"
    )


def validate_ppm(ppm: int, device: Optional[SDRDevice] = None, capabilities: Optional[SDRCapabilities] = None) -> int:
    """
    Validate PPM frequency correction.

    Args:
        ppm: PPM correction value
        device: SDR device (optional, takes precedence)
        capabilities: SDR capabilities (used if device not provided)

    Returns:
        Validated PPM value

    Raises:
        SDRValidationError: If PPM is out of range or not supported
    """
    if device:
        caps = device.capabilities
    elif capabilities:
        caps = capabilities
    else:
        caps = None

    # Check if device supports PPM
    if caps and not caps.supports_ppm:
        if ppm != 0:
            # Warn but don't fail - some hardware just ignores PPM
            pass
        return 0  # Return 0 to indicate no correction

    # Standard PPM range
    if not -1000 <= ppm <= 1000:
        raise SDRValidationError(f"PPM correction {ppm} out of range. Valid range: -1000 to 1000")

    return ppm


def validate_device_index(index: int) -> int:
    """
    Validate device index.

    Args:
        index: Device index (0-255)

    Returns:
        Validated device index

    Raises:
        SDRValidationError: If index is out of range
    """
    if not 0 <= index <= 255:
        raise SDRValidationError(f"Device index {index} out of range. Valid range: 0-255")
    return index


def validate_squelch(squelch: int) -> int:
    """
    Validate squelch level.

    Args:
        squelch: Squelch level (0-1000, 0 = off)

    Returns:
        Validated squelch level

    Raises:
        SDRValidationError: If squelch is out of range
    """
    if not 0 <= squelch <= 1000:
        raise SDRValidationError(f"Squelch {squelch} out of range. Valid range: 0-1000")
    return squelch


def get_capabilities_for_type(sdr_type: SDRType) -> SDRCapabilities:
    """
    Get default capabilities for an SDR type.

    Args:
        sdr_type: The SDR type

    Returns:
        SDRCapabilities for the specified type
    """
    from .rtlsdr import RTLSDRCommandBuilder
    from .limesdr import LimeSDRCommandBuilder
    from .hackrf import HackRFCommandBuilder

    builders = {
        SDRType.RTL_SDR: RTLSDRCommandBuilder,
        SDRType.LIME_SDR: LimeSDRCommandBuilder,
        SDRType.HACKRF: HackRFCommandBuilder,
    }

    builder_class = builders.get(sdr_type)
    if builder_class:
        return builder_class.CAPABILITIES

    raise SDRValidationError(f"Unknown SDR type: {sdr_type}")
