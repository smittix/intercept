"""readsb ADS-B commands for SoapySDR devices (#387)."""

import pytest

from utils.sdr.airspy import AirspyCommandBuilder
from utils.sdr.base import SDRDevice, SDRType
from utils.sdr.bladerf import BladeRFCommandBuilder
from utils.sdr.hydrasdr import HydraSDRCommandBuilder
from utils.sdr.limesdr import LimeSDRCommandBuilder
from utils.sdr.sdrplay import SDRPlayCommandBuilder
from utils.sdr.usrp import USRPCommandBuilder

BUILDERS = [
    (AirspyCommandBuilder, SDRType.AIRSPY),
    (BladeRFCommandBuilder, SDRType.BLADE_RF),
    (HydraSDRCommandBuilder, SDRType.HYDRA_SDR),
    (LimeSDRCommandBuilder, SDRType.LIME_SDR),
    (SDRPlayCommandBuilder, SDRType.SDRPLAY),
    (USRPCommandBuilder, SDRType.USRP),
]


@pytest.mark.parametrize("builder, sdr_type", BUILDERS, ids=lambda v: getattr(v, "__name__", None))
def test_readsb_command_matches_current_readsb(builder, sdr_type):
    device = SDRDevice(
        sdr_type=sdr_type,
        index=0,
        name="test",
        serial="abc123",
        driver=sdr_type.value,
        capabilities=builder.CAPABILITIES,
    )
    cmd = builder().build_adsb_command(device, gain=30)

    # wiedehopf readsb rejects "soapy"; a SoapySDR-only build has no --device
    assert cmd[cmd.index("--device-type") + 1] == "soapysdr"
    assert "--device" not in cmd
    assert "--soapy-device" in cmd
    # readsb 3.16 defaults --net-sbs-port to 0; INTERCEPT reads SBS on 30003
    assert cmd[cmd.index("--net-sbs-port") + 1] == "30003"


@pytest.mark.parametrize("builder, sdr_type", BUILDERS, ids=lambda v: getattr(v, "__name__", None))
def test_readsb_bias_tee_option(builder, sdr_type):
    device = SDRDevice(
        sdr_type=sdr_type,
        index=0,
        name="test",
        serial="abc123",
        driver=sdr_type.value,
        capabilities=builder.CAPABILITIES,
    )
    cmd = builder().build_adsb_command(device, gain=30, bias_t=True)
    # readsb rejects --enable-bias-t; its option is --enable-biastee
    assert "--enable-bias-t" not in cmd
