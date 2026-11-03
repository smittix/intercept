"""
Shared csdr demodulation pipeline: CU8 I/Q source | csdr ... | s16le audio.

Used where the vendor's own demodulator cannot be used: HackRF (rx_fm's
SoapySDR streaming is broken) and remote rtl_tcp servers (stock rtl_fm has
no rtl_tcp client).
"""

from __future__ import annotations

import math
import shlex

# Minimum rate the channel is decimated to before demodulation.
# Narrowband FM (pager, APRS, voice) fits easily in 40 kHz; broadcast FM
# needs ~200 kHz for its +/-75 kHz deviation.
_NFM_MIN_CHANNEL_RATE = 40_000
_WFM_MIN_CHANNEL_RATE = 200_000

FM_MODULATIONS = {"fm", "nfm", "wfm"}
CSDR_MODULATIONS = FM_MODULATIONS | {"am", "usb", "lsb"}

# SSB audio passband, Hz
_SSB_HIGH_CUT_HZ = 3000


def decimations(sample_rate: int, modulation: str, min_capture_rate: int) -> tuple[int, int]:
    """(channel_decim, audio_decim) for a capture rate >= min_capture_rate."""
    min_channel_rate = _WFM_MIN_CHANNEL_RATE if modulation == "wfm" else _NFM_MIN_CHANNEL_RATE
    audio_decim = max(1, math.ceil(min_channel_rate / sample_rate))
    channel_decim = max(1, math.ceil(min_capture_rate / (sample_rate * audio_decim)))
    return channel_decim, audio_decim


def capture_plan(frequency_mhz: float, sample_rate: int, modulation: str, min_capture_rate: int) -> tuple[int, int]:
    """(capture_rate, tune_hz) for the I/Q source.

    The capture rate is an exact integer multiple of the output rate so
    only integer decimation is needed: csdr's rational_resampler_ff
    outputs silence for large ratios such as 441/40000, which a fixed
    2 MS/s capture would need for 22050 Hz audio.

    The source tunes capture_rate/8 above the target so the DC spike falls
    outside the channel; shift_addition_cc 0.125 moves the signal back.
    """
    channel_decim, audio_decim = decimations(sample_rate, modulation, min_capture_rate)
    capture_rate = sample_rate * audio_decim * channel_decim
    return capture_rate, round(frequency_mhz * 1e6) + capture_rate // 8


def squelch_level(squelch: int | None) -> float | None:
    """Channel-power threshold for csdr's squelch, or None when off.

    The 0-100 squelch control maps linearly onto -80..-10 dB of channel
    power. Measured on a HackRF at moderate gain: a broadcast station
    -41 dB and an empty channel -57 dB (WFM); -43 / -64.5 dB (NFM), so
    about 40 (-52 dB) mutes noise and passes a station.
    """
    if not squelch or squelch <= 0:
        return None
    return 10 ** ((-80 + 0.7 * min(squelch, 100)) / 10)


def csdr_demod_script(
    source_cmd: list[str],
    csdr_path: str,
    modulation: str,
    sample_rate: int,
    min_capture_rate: int,
    squelch: int | None = None,
) -> str:
    """Script for ``bash -c``: source | csdr stages, writing s16le mono at sample_rate.

    source_cmd must stream CU8 at capture_plan()'s rate and tune frequency.
    With squelch > 0 the channel is gated on its power before demodulation
    (csdr outputs zeros while closed, so audio keeps flowing as silence).
    """
    channel_decim, audio_decim = decimations(sample_rate, modulation, min_capture_rate)
    channel_rate = sample_rate * audio_decim

    stages = [
        "convert_u8_f",
        "shift_addition_cc 0.125",
        # Channel filter; transition band = 25% of the channel rate
        f"fir_decimate_cc {channel_decim} {0.25 / channel_decim:.6f}",
    ]
    level = squelch_level(squelch)
    if level is not None:
        # The level is read from a fifo; the s-meter fifo must be drained.
        # use_every_nth must be 1: csdr divides the sampled power by the full
        # block length, so n > 1 reads n times low and skews the threshold.
        stages.append('squelch_and_smeter_cc --fifo "$SQ/level" --outfifo "$SQ/meter" 1 1000')
    if modulation in FM_MODULATIONS:
        # atan is exact at wide deviation, unlike quadri's sin() approximation
        stages.append("fmdemod_atan_cf")
        if modulation == "wfm":
            stages.append(f"deemphasis_wfm_ff {channel_rate} 75e-6")
    elif modulation == "am":
        # Envelope with its DC (carrier level) kept, like rtl_fm -M am
        stages.append("amdemod_cf")
    else:
        cut = _SSB_HIGH_CUT_HZ / channel_rate
        low, high = (0, cut) if modulation == "usb" else (-cut, 0)
        stages += [f"bandpass_fir_fft_cc {low:.6f} {high:.6f} {cut / 4:.6f}", "realpart_cf"]
    if audio_decim > 1:
        stages.append(f"rational_resampler_ff 1 {audio_decim}")
    stages += ["limit_ff", "convert_f_s16"]

    csdr = shlex.quote(csdr_path)
    pipeline = " | ".join([shlex.join(source_cmd)] + [f"{csdr} {stage} 2>/dev/null" for stage in stages])

    # Callers stop this by signalling bash, which would orphan the
    # pipeline. Run it in the background and, on TERM/INT/HUP, kill its
    # last stage: the SIGPIPE cascade then stops each stage back to the
    # source, which handles SIGPIPE and releases the device cleanly.
    if level is None:
        return f"trap 'kill $! 2>/dev/null; wait' TERM INT HUP; {pipeline} & wait $!"
    # Fifo helpers start before the pipeline so $! stays the pipeline's last
    # stage; they are killed explicitly (a writer whose reader never opened
    # the fifo would otherwise block `wait` forever).
    setup = (
        'SQ=$(mktemp -d) && mkfifo "$SQ/level" "$SQ/meter" || exit 1; '
        f'echo {level:.6e} > "$SQ/level" & W=$!; '
        'cat "$SQ/meter" > /dev/null & M=$!; '
    )
    cleanup = 'kill $W $M 2>/dev/null; rm -rf "$SQ"'
    return f"{setup}trap 'kill $! 2>/dev/null; wait $!; {cleanup}' TERM INT HUP; {pipeline} & wait $!; {cleanup}"
