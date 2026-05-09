# Hardware & Advanced Setup

## Supported SDR Hardware

| Hardware | Frequency Range | Gain Max | Price | Notes |
|----------|-----------------|----------|-------|-------|
| **RTL-SDR** (e.g. NooElec NESDR v5) | 24 – 1766 MHz | 50 dB | ~$25–35 | Recommended for beginners; supports PPM correction and Bias-T |
| **HackRF HM4 / One** | 1 – 6000 MHz | 102 dB | ~$300 | Ultra-wide range; no PPM correction; requires SoapySDR |
| **Airspy** | 24 – 1800 MHz | 45 dB | ~$150 | High dynamic range; requires SoapySDR |
| **LimeSDR** | 0.1 – 3800 MHz | 73 dB | ~$300 | Wide range, TCXO (low PPM drift); requires SoapySDR |
| **SDRplay RSP** | 0.001 – 2000 MHz | 59 dB | ~$200 | Very wide range; requires SoapySDR |

INTERCEPT automatically detects connected devices and adjusts gain limits and PPM field visibility per device type.

---

## RTL-SDR v5 (NooElec NESDR) — Bias-T & PPM

The **NooElec NESDR RTL-SDR v5** (and RTL-SDR Blog V4/V5) include a switchable bias-T power output on the SMA port, used to power active antennas and low-noise amplifiers (LNAs) without a separate power supply.

### Enabling Bias-T

Use the **global Bias-T toggle** in the INTERCEPT sidebar. It activates bias-T power for:
- **Pager** — passed to `rtl_fm` via device string
- **433 MHz Sensors** — passed to `rtl_433` via device string  
- **ADS-B** — passed to dump1090 (`--enable-biast`) or via `rtl_biast` fallback
- **AIS** — passed to AIS-catcher (`-gr BIASTEE on`)
- **ACARS** — `rtl_biast -b 1` called before acarsdec starts; `-b 0` on stop

Requires `rtl_biast` from RTL-SDR Blog drivers for ACARS/dump1090 fallback:
```bash
sudo apt install rtl-sdr   # includes rtl_biast on RTL-SDR Blog packages
```

### PPM Correction

RTL-SDR dongles have a crystal oscillator with a small frequency error (typically ±5–20 PPM). Enter your dongle's PPM offset in any mode's **PPM Correction** field. To measure it:
```bash
kalibrate-rtl -s 1800000 -g 40   # scan GSM towers and derive PPM error
# or
rtl_test -p                        # measure PPM drift over ~60 seconds
```

---

## HackRF HM4 / One — Gain & PPM

The **HackRF** has a combined LNA + VGA gain stage up to **102 dB**. INTERCEPT sets all gain sliders to `max=102` automatically when HackRF is the selected device type.

**PPM correction is not supported** on HackRF via dump1090/AIS-catcher/acarsdec — the PPM input fields are automatically hidden when HackRF is selected.

Typical gain settings:
| Mode | Recommended Gain |
|------|-----------------|
| ADS-B (1090 MHz) | 40–60 dB |
| AIS (162 MHz) | 40–50 dB |
| ACARS (130 MHz) | 40–50 dB |
| Pager (150–930 MHz) | 30–50 dB |

---

## Quick Install

### Recommended: Use the Setup Script

The setup script provides an interactive menu with install profiles for selective installation:

```bash
git clone https://github.com/smittix/intercept.git
cd intercept
./setup.sh
```

On first run, a guided wizard walks you through profile selection:

| Profile | What it installs |
|---------|-----------------|
| Core SIGINT | rtl_sdr, multimon-ng, rtl_433, dump1090, acarsdec, dumpvdl2, ffmpeg, gpsd |
| Maritime & Radio | AIS-catcher, direwolf |
| Weather & Space | SatDump, radiosonde_auto_rx |
| RF Security | aircrack-ng, HackRF, BlueZ, hcxtools, Ubertooth, SoapySDR |
| Full SIGINT | All of the above |

For headless/CI installs:
```bash
./setup.sh --non-interactive                # Install everything
./setup.sh --profile=core,maritime          # Install specific profiles
```

After installation, use the menu to manage your setup:
```bash
./setup.sh              # Opens interactive menu
./setup.sh --health-check   # Verify installation
```

### Manual Install: macOS (Homebrew)

```bash
# Install Homebrew if needed
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# Core tools (required)
brew install python@3.11 librtlsdr multimon-ng rtl_433 ffmpeg

# ADS-B aircraft tracking
brew install dump1090-mutability

# WiFi tools (optional)
brew install aircrack-ng

# LimeSDR support (optional)
brew install soapysdr limesuite soapylms7

# HackRF support (optional)
brew install hackrf soapyhackrf
```

### Manual Install: Debian / Ubuntu / Raspberry Pi OS

```bash
# Update package lists
sudo apt update

# Core tools (required)
sudo apt install -y python3 python3-pip python3-venv python3-skyfield
sudo apt install -y rtl-sdr multimon-ng rtl-433 ffmpeg

# ADS-B aircraft tracking
sudo apt install -y dump1090-mutability
# Alternative: dump1090-fa (FlightAware version)

# WiFi tools (optional)
sudo apt install -y aircrack-ng

# Bluetooth tools (optional)
sudo apt install -y bluez bluetooth

# LimeSDR support (optional)
sudo apt install -y soapysdr-tools limesuite soapysdr-module-lms7

# HackRF support (optional)
sudo apt install -y hackrf soapysdr-module-hackrf
```

---

## RTL-SDR Setup (Linux)

### Add udev rules

If your RTL-SDR isn't detected, create udev rules:

```bash
sudo bash -c 'cat > /etc/udev/rules.d/20-rtlsdr.rules << EOF
SUBSYSTEM=="usb", ATTRS{idVendor}=="0bda", ATTRS{idProduct}=="2838", MODE="0666"
SUBSYSTEM=="usb", ATTRS{idVendor}=="0bda", ATTRS{idProduct}=="2832", MODE="0666"
EOF'

sudo udevadm control --reload-rules
sudo udevadm trigger
```

Then unplug and replug your RTL-SDR.

### Blacklist DVB-T driver

The default DVB-T driver conflicts with rtl-sdr:

```bash
echo "blacklist dvb_usb_rtl28xxu" | sudo tee /etc/modprobe.d/blacklist-rtl.conf
sudo modprobe -r dvb_usb_rtl28xxu
```

---

## Multiple RTL-SDR Dongles

If you're running two (or more) RTL-SDR dongles on the same machine, they ship with the same default serial number so Linux can't tell them apart reliably. Follow these steps to give each a unique identity.

### Step 1: Blacklist the DVB-T driver

Already covered above, but make sure this is done first — the kernel's DVB driver will grab the dongles before librtlsdr can:

```bash
echo "blacklist dvb_usb_rtl28xxu" | sudo tee /etc/modprobe.d/blacklist-rtl.conf
sudo modprobe -r dvb_usb_rtl28xxu
```

### Step 2: Burn unique serial numbers

Each dongle has an EEPROM that stores a serial number. By default they're all `00000001`. You need to give each one a unique serial.

**Plug in only the first dongle**, then:

```bash
rtl_eeprom -d 0 -s 00000001
```

**Unplug it, plug in the second dongle**, then:

```bash
rtl_eeprom -d 0 -s 00000002
```

> Pick any 8-digit hex serials you like. The `-d 0` means "device index 0" (the only one plugged in).

Unplug and replug both dongles after writing.

### Step 3: Verify

With both plugged in:

```bash
rtl_test -t
```

You should see:

```
0:  Realtek, RTL2838UHIDIR, SN: 00000001
1:  Realtek, RTL2838UHIDIR, SN: 00000002
```

**Tip:** If you don't know which physical dongle has which serial, unplug one and run `rtl_test -t` — the one still detected is the one still plugged in.

### Step 4: Udev rules with stable symlinks

Create rules that give each dongle a persistent name based on its serial:

```bash
sudo bash -c 'cat > /etc/udev/rules.d/20-rtlsdr.rules << EOF
# RTL-SDR dongles - permissions and stable symlinks by serial
SUBSYSTEM=="usb", ATTR{idVendor}=="0bda", ATTR{idProduct}=="2838", MODE="0666"
SUBSYSTEM=="usb", ATTR{idVendor}=="0bda", ATTR{idProduct}=="2832", MODE="0666"

# Symlinks by serial — change names/serials to match your hardware
SUBSYSTEM=="usb", ATTR{idVendor}=="0bda", ATTRS{serial}=="00000001", SYMLINK+="sdr-dongle1"
SUBSYSTEM=="usb", ATTR{idVendor}=="0bda", ATTRS{serial}=="00000002", SYMLINK+="sdr-dongle2"
EOF'

sudo udevadm control --reload-rules
sudo udevadm trigger
```

After replugging, you'll have `/dev/sdr-dongle1` and `/dev/sdr-dongle2`.

### Step 5: USB power (Raspberry Pi)

Two dongles can draw more current than the Pi allows by default:

```bash
# In /boot/firmware/config.txt, add:
usb_max_current_enable=1
```

Disable USB autosuspend so dongles don't get powered off:

```bash
# In /etc/default/grub or kernel cmdline, add:
usbcore.autosuspend=-1
```

Or via udev:

```bash
echo 'ACTION=="add", SUBSYSTEM=="usb", ATTR{power/autosuspend}="-1"' | \
  sudo tee /etc/udev/rules.d/50-usb-autosuspend.rules
```

### Step 6: Docker access

Your `docker-compose.yml` needs privileged mode and USB passthrough:

```yaml
services:
  intercept:
    privileged: true
    volumes:
      - /dev/bus/usb:/dev/bus/usb
```

INTERCEPT auto-detects both dongles inside the container via `rtl_test -t` and addresses them by device index (`-d 0`, `-d 1`).

### Quick reference

| Step | What | Why |
|------|------|-----|
| Blacklist DVB | `/etc/modprobe.d/blacklist-rtl.conf` | Kernel won't steal the dongles |
| Burn serials | `rtl_eeprom -d 0 -s <serial>` | Unique identity per dongle |
| Udev rules | `/etc/udev/rules.d/20-rtlsdr.rules` | Permissions + stable `/dev/sdr-*` names |
| USB power | `config.txt` + autosuspend off | Enough current for two dongles on a Pi |
| Docker | `privileged: true` + USB volume | Container sees both dongles |

---

## Verify Installation

### Check dependencies
```bash
python3 intercept.py --check-deps
```

### Test SDR detection
```bash
# RTL-SDR
rtl_test

# LimeSDR/HackRF (via SoapySDR)
SoapySDRUtil --find
```

---

## Python Environment

### Using setup.sh (Recommended)
```bash
./setup.sh
```

The setup wizard automatically:
- Detects your OS (macOS, Debian/Ubuntu, DragonOS)
- Lets you choose install profiles (Core, Maritime, Weather, Security, Full, Custom)
- Creates a virtual environment with system site-packages
- Installs Python dependencies (core + optional)
- Runs a health check to verify everything works

After initial setup, use the menu to manage your environment:
- **Install / Add Modules** — add tools you didn't install initially
- **System Health Check** — verify all tools and dependencies
- **Environment Configurator** — set `INTERCEPT_*` variables interactively
- **Update Tools** — rebuild source-built tools (dump1090, SatDump, etc.)
- **View Status** — see what's installed at a glance

### Manual setup
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

---

## Running INTERCEPT

After installation:

```bash
sudo ./start.sh

# Custom port
sudo ./start.sh -p 8080

# HTTPS
sudo ./start.sh --https
```

Open **http://localhost:5050** in your browser.

---

## Complete Tool Reference

| Tool | Package (Debian) | Package (macOS) | Required For |
|------|------------------|-----------------|--------------|
| `rtl_fm` | rtl-sdr | librtlsdr | Pager, Listening Post |
| `rtl_test` | rtl-sdr | librtlsdr | SDR detection |
| `multimon-ng` | multimon-ng | multimon-ng | Pager decoding |
| `rtl_433` | rtl-433 | rtl_433 | 433MHz sensors |
| `dump1090` | dump1090-mutability | dump1090-mutability | ADS-B tracking |
| `ffmpeg` | ffmpeg | ffmpeg | Listening Post audio |
| `airmon-ng` | aircrack-ng | aircrack-ng | WiFi monitor mode |
| `airodump-ng` | aircrack-ng | aircrack-ng | WiFi scanning |
| `aireplay-ng` | aircrack-ng | aircrack-ng | WiFi deauth (optional) |
| `hcitool` | bluez | N/A | Bluetooth scanning |
| `bluetoothctl` | bluez | N/A | Bluetooth control |
| `hciconfig` | bluez | N/A | Bluetooth config |

### Optional tools:
| Tool | Package (Debian) | Package (macOS) | Purpose |
|------|------------------|-----------------|---------|
| `ffmpeg` | ffmpeg | ffmpeg | Alternative audio encoder |
| `SoapySDRUtil` | soapysdr-tools | soapysdr | LimeSDR/HackRF support |
| `LimeUtil` | limesuite | limesuite | LimeSDR native tools |
| `hackrf_info` | hackrf | hackrf | HackRF native tools |

### Python dependencies (requirements.txt):
| Package | Purpose |
|---------|---------|
| `flask` | Web server |
| `skyfield` | Satellite tracking |
| `bleak` | BLE scanning with manufacturer data (TSCM) |

---

## dump1090 Notes

### Package names vary by distribution:
- `dump1090-mutability` - Most common
- `dump1090-fa` - FlightAware version (recommended)
- `dump1090` - Generic

### Not in repositories (Debian Trixie)?

Install FlightAware's version:
https://flightaware.com/adsb/piaware/install

Or build from source:
https://github.com/flightaware/dump1090

---

## TSCM Mode Requirements

TSCM (Technical Surveillance Countermeasures) mode requires specific hardware for full functionality:

### BLE Scanning (Tracker Detection)
- Any Bluetooth adapter supported by your OS
- `bleak` Python library for manufacturer data detection
- Detects: AirTags, Tile, SmartTags, ESP32/ESP8266 devices

```bash
# Install bleak
pip install bleak>=0.21.0

# Or via apt (Debian/Ubuntu)
sudo apt install python3-bleak
```

### RF Spectrum Analysis
- **RTL-SDR dongle** (required for RF sweeps)
- `rtl_power` command from `rtl-sdr` package

Frequency bands scanned:
| Band | Frequency | Purpose |
|------|-----------|---------|
| FM Broadcast | 88-108 MHz | FM bugs |
| 315 MHz ISM | 315 MHz | US wireless devices |
| 433 MHz ISM | 433-434 MHz | EU wireless devices |
| 868 MHz ISM | 868-869 MHz | EU IoT devices |
| 915 MHz ISM | 902-928 MHz | US IoT devices |
| 1.2 GHz | 1200-1300 MHz | Video transmitters |
| 2.4 GHz ISM | 2400-2500 MHz | WiFi/BT/Video |

```bash
# Linux
sudo apt install rtl-sdr

# macOS
brew install librtlsdr
```

### WiFi Scanning
- Standard WiFi adapter (managed mode for basic scanning)
- Monitor mode capable adapter for advanced features
- `aircrack-ng` suite for monitor mode management

---

## Notes

- **Bluetooth on macOS**: Uses bleak library (CoreBluetooth backend), bluez tools not needed
- **WiFi on macOS**: Monitor mode has limited support, full functionality on Linux
- **System tools**: `iw`, `iwconfig`, `rfkill`, `ip` are pre-installed on most Linux systems
- **TSCM on macOS**: BLE and WiFi scanning work; RF spectrum requires RTL-SDR

