# Hardware & Installation

## Supported SDR Hardware

| Hardware | Frequency Range | Gain Range | TX | Price | Notes |
|----------|-----------------|------------|-----|-------|-------|
| **RTL-SDR** | 24 - 1766 MHz | 0 - 50 dB | No | ~$25 | Most common, budget-friendly |
| **LimeSDR** | 0.1 - 3800 MHz | 0 - 73 dB | Yes | ~$300 | Wide range, requires SoapySDR |
| **HackRF** | 1 - 6000 MHz | 0 - 62 dB | Yes | ~$300 | Ultra-wide range, requires SoapySDR |

INTERCEPT automatically detects connected devices and shows hardware-specific capabilities in the UI.

## Requirements

### Hardware
- **SDR Device** - RTL-SDR, LimeSDR, or HackRF
- **WiFi adapter** capable of monitor mode (for WiFi features)
- **Bluetooth adapter** (for Bluetooth features)
- **GPS dongle** (optional, for precise location)

### Software
- **Python 3.9+** required
- External tools (see installation below)

## Tool Installation

### Core SDR Tools

| Tool | macOS | Ubuntu/Debian | Purpose |
|------|-------|---------------|---------|
| rtl-sdr | `brew install librtlsdr` | `sudo apt install rtl-sdr` | RTL-SDR support |
| multimon-ng | `brew install multimon-ng` | `sudo apt install multimon-ng` | Pager decoding |
| rtl_433 | `brew install rtl_433` | `sudo apt install rtl-433` | 433MHz sensors |
| dump1090 | `brew install dump1090-mutability` | `sudo apt install dump1090-mutability` | ADS-B aircraft |
| aircrack-ng | `brew install aircrack-ng` | `sudo apt install aircrack-ng` | WiFi reconnaissance |
| bluez | Built-in (limited) | `sudo apt install bluez bluetooth` | Bluetooth scanning |

### LimeSDR / HackRF Support (Optional)

| Tool | macOS | Ubuntu/Debian | Purpose |
|------|-------|---------------|---------|
| SoapySDR | `brew install soapysdr` | `sudo apt install soapysdr-tools` | Universal SDR abstraction |
| LimeSDR | `brew install limesuite soapylms7` | `sudo apt install limesuite soapysdr-module-lms7` | LimeSDR support |
| HackRF | `brew install hackrf soapyhackrf` | `sudo apt install hackrf soapysdr-module-hackrf` | HackRF support |
| readsb | Build from source | Build from source | ADS-B with SoapySDR |

> **Note:** RTL-SDR works out of the box. LimeSDR and HackRF require SoapySDR plus the hardware-specific driver.

## Quick Install Commands

### Ubuntu/Debian
```bash
# Core tools
sudo apt update
sudo apt install rtl-sdr multimon-ng rtl-433 dump1090-mutability aircrack-ng bluez bluetooth

# LimeSDR (optional)
sudo apt install soapysdr-tools limesuite soapysdr-module-lms7

# HackRF (optional)
sudo apt install hackrf soapysdr-module-hackrf
```

### macOS (Homebrew)
```bash
# Core tools
brew install librtlsdr multimon-ng rtl_433 dump1090-mutability aircrack-ng

# LimeSDR (optional)
brew install soapysdr limesuite soapylms7

# HackRF (optional)
brew install hackrf soapyhackrf
```

### Arch Linux
```bash
# Core tools
sudo pacman -S rtl-sdr multimon-ng
yay -S rtl_433 dump1090

# LimeSDR/HackRF (optional)
sudo pacman -S soapysdr limesuite hackrf
```

## Linux udev Rules

If your SDR isn't detected, add udev rules:

```bash
sudo bash -c 'cat > /etc/udev/rules.d/20-rtlsdr.rules << EOF
SUBSYSTEM=="usb", ATTRS{idVendor}=="0bda", ATTRS{idProduct}=="2838", MODE="0666"
SUBSYSTEM=="usb", ATTRS{idVendor}=="0bda", ATTRS{idProduct}=="2832", MODE="0666"
EOF'

sudo udevadm control --reload-rules
sudo udevadm trigger
```

Then unplug and replug your device.

## Blacklist DVB-T Driver (Linux)

The default DVB-T driver conflicts with rtl-sdr:

```bash
echo "blacklist dvb_usb_rtl28xxu" | sudo tee /etc/modprobe.d/blacklist-rtl.conf
sudo modprobe -r dvb_usb_rtl28xxu
```

## Verify Installation

Check what's installed:
```bash
python3 intercept.py --check-deps
```

Test SDR detection:
```bash
# RTL-SDR
rtl_test

# LimeSDR/HackRF
SoapySDRUtil --find
```
