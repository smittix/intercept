# Troubleshooting

Solutions for common issues.

## Login and Access Issues

### I don't know the admin password

There is no default password. On first start INTERCEPT generates one, logs it, and writes it to `instance/.initial_password`:

```bash
cat instance/.initial_password
```

Log in with that, then set your own when prompted. Delete the file afterwards.

### I am stuck on the "set new password" page

That is intended. An account seeded with a password you did not choose must set one before the rest of the interface is reachable. It also appears if you are upgrading from a version that used the old `admin`/`admin` default.

Enter the current password (the generated one, or `admin` if you are upgrading), then a new one of at least 12 characters. See [Security](SECURITY.md#authentication) for why.

### I have forgotten the password entirely

Set `INTERCEPT_ADMIN_PASSWORD` in your environment or `.env` and restart. On startup this overwrites the stored password for the admin account:

```bash
INTERCEPT_ADMIN_PASSWORD='a-new-password' sudo ./start.sh
```

### My remote agent stopped sending data after upgrading

From v2.33.6 an agent must have an API key to push. An agent registered without one is refused by `/controller/api/ingest`. Set a key on the agent in the interface, and the matching `controller_api_key` in the agent's own config file, then restart the agent.

## Python / Installation Issues

### "ModuleNotFoundError: No module named 'flask'"

Install Python dependencies first:
```bash
pip install -r requirements.txt

# Or with python3 explicitly
python3 -m pip install -r requirements.txt
```

### pip install fails for flask or skyfield

On newer Debian/Ubuntu systems, pip may fail with permission errors or dependency conflicts. **Use apt instead:**

```bash
# Install Python packages via apt (recommended for Debian/Ubuntu)
sudo apt install python3-flask python3-requests python3-serial python3-skyfield

# Then create venv with system packages
python3 -m venv --system-site-packages venv
source venv/bin/activate
sudo ./start.sh
```

### "error: externally-managed-environment" (pip blocked)

This is PEP 668 protection on Ubuntu 23.04+, Debian 12+, and similar systems. Solutions:

```bash
# Option 1: Use apt packages (recommended)
sudo apt install python3-flask python3-requests python3-serial python3-skyfield
python3 -m venv --system-site-packages venv
source venv/bin/activate

# Option 2: Use pipx for isolated install
pipx install flask

# Option 3: Force pip (not recommended)
pip install --break-system-packages flask
```

### "TypeError: 'type' object is not subscriptable"

This error occurs on Python 3.7 or 3.8. **INTERCEPT requires Python 3.10 or later.**

```bash
# Check your Python version
python3 --version

# Ubuntu/Debian - install newer Python
sudo apt update
sudo apt install python3.11 python3.11-venv python3-pip

# Run with newer Python
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
sudo ./start.sh
```

### Alternative: Use the setup script

The setup script handles all installation automatically, including apt packages and source builds:

```bash
./setup.sh                    # Interactive wizard (first run) or menu
./setup.sh --non-interactive  # Headless full install
./setup.sh --health-check     # Diagnose installation issues
```

The setup menu also includes a **System Health Check** (option 2) that verifies all tools, SDR devices, ports, permissions, and Python packages — useful for diagnosing installation problems.

### "pip: command not found"

```bash
# Ubuntu/Debian
sudo apt install python3-pip

# macOS
python3 -m ensurepip --upgrade
```

### Permission denied during pip install

```bash
# Install to user directory
pip install --user -r requirements.txt
```

## SDR Hardware Issues

### No SDR devices found

1. Ensure your SDR device is plugged in
2. Check detection:
   - RTL-SDR: `rtl_test`
   - LimeSDR/HackRF: `SoapySDRUtil --find`
3. On Linux, add udev rules (see below)
4. Blacklist conflicting drivers:
   ```bash
   echo "blacklist dvb_usb_rtl28xxu" | sudo tee /etc/modprobe.d/blacklist-rtl.conf
   sudo modprobe -r dvb_usb_rtl28xxu
   ```

### Linux udev rules for RTL-SDR

See [HARDWARE.md — RTL-SDR Setup](HARDWARE.md#rtl-sdr-setup-linux) for udev rules and driver blacklisting.

### Device busy error

1. Click "Stop all running processes" (the ⊗ button in the toolbar)
2. Unplug and replug the SDR device
3. Check for other applications: `lsof | grep rtl`

### LimeSDR/HackRF not detected
Ensure the correct SoapySDR module for your hardware is installed first

1. Verify SoapySDR is installed: `SoapySDRUtil --info`
2. Check driver is loaded: `SoapySDRUtil --find`
3. May need udev rules or run as root

### Using HackRF/Airspy/LimeSDR with ADS-B

For non-RTL-SDR devices, ADS-B requires `readsb` compiled with SoapySDR support (standard dump1090 won't work).

**Option 1: Run readsb separately and connect via Remote mode**

1. Start readsb with your device:
   ```bash
   # HackRF
   readsb --device-type soapysdr --device driver=hackrf --net --quiet

   # Airspy
   readsb --device-type soapysdr --device driver=airspy --net --quiet

   # LimeSDR
   readsb --device-type soapysdr --device driver=lime --net --quiet
   ```

2. In Intercept's ADS-B dashboard:
   - Check the **"Remote"** checkbox
   - Enter Host: `localhost` and Port: `30003`
   - Click **START**

3. Intercept will connect to readsb's SBS output on port 30003

**Option 2: Install readsb with SoapySDR support**

On Debian/Ubuntu:
```bash
# Install dependencies
sudo apt install build-essential debhelper librtlsdr-dev pkg-config \
    libncurses5-dev libbladerf-dev libhackrf-dev liblimesuite-dev libsoapysdr-dev

# Clone and build
git clone https://github.com/wiedehopf/readsb.git
cd readsb
dpkg-buildpackage -b --no-sign
sudo dpkg -i ../readsb_*.deb
```

### Waterfall Audio with HackRF, Airspy and Other SoapySDR Devices

RTL-SDRs use `rtl_fm` for audio. Other devices need `rx_fm` from [rx_tools](https://github.com/rxseger/rx_tools), which is built on SoapySDR. HackRF FM uses `rx_sdr` with `csdr` instead when both are installed.

```bash
# SoapySDR development files, then build rx_tools
sudo apt install libsoapysdr-dev cmake build-essential
git clone https://github.com/rxseger/rx_tools.git
cd rx_tools && mkdir build && cd build && cmake .. && make && sudo make install

# Verify
which rx_fm rx_sdr
```

HackRF, Airspy, LimeSDR and SDRplay then appear in the Waterfall's device dropdown.

### Waterfall Audio: No Sound

Audio streams straight from INTERCEPT to the browser; no separate audio server is needed. If nothing plays:

**1. Check the decoder's own errors**

Open `/receiver/audio/debug` in the browser while audio is running. It shows the frequency, device and gain in use, whether the audio process is alive, and the last error output from `rtl_fm` (or `rx_fm`) and `ffmpeg`.

**2. Verify the SDR works**
```bash
rtl_test -t        # should report "Found 1 device(s)"
```

**3. Check ffmpeg is installed**
```bash
which ffmpeg       # macOS: brew install ffmpeg  /  Linux: sudo apt install ffmpeg
```

**4. Test the demodulator by hand**
```bash
# Should print raw audio bytes
rtl_fm -M am -f 118000000 -s 24000 -r 24000 -g 40 2>&1 | head -c 1000 | xxd | head
```

**5. Common errors**

| Error | Cause | Solution |
|-------|-------|----------|
| `No supported devices found` | SDR not connected | Plug in the SDR and check USB |
| `Device or resource busy` | Another mode or process is using the SDR | Stop the other mode, or use **Kill All** in the navigation bar |
| `ffmpeg not found` | ffmpeg missing | Install ffmpeg |
| Audio plays but is silent | Squelch too high | Lower the squelch |

## WiFi Issues

### Monitor mode fails

1. Ensure running as root/sudo
2. Check adapter supports monitor mode: `iw list | grep monitor`
3. Kill interfering processes: `airmon-ng check kill`

### Permission denied when scanning

Run INTERCEPT with sudo:
```bash
sudo ./start.sh
```

### Interface not found after enabling monitor mode

Some adapters rename when entering monitor mode (e.g., wlan0 → wlan0mon). The interface should auto-select, but if not, manually select the monitor interface from the dropdown.

## Bluetooth Issues

### No Bluetooth adapter found

```bash
# Check if adapter is detected
hciconfig

# Ubuntu/Debian - install BlueZ
sudo apt install bluez bluetooth
```

### Permission denied

Run with sudo or add your user to the bluetooth group:
```bash
sudo usermod -a -G bluetooth $USER
```

## Decoding Issues

### No messages appearing (Pager mode)

1. Verify frequency is correct for your area
2. Adjust gain (try 30-40 dB)
3. Check pager services are active in your area
4. Ensure antenna is connected

### Cannot install dump1090 in Debian (ADS-B mode)

On newer Debian versions, dump1090 may not be in repositories. Use the setup script which builds it from source automatically:

```bash
./setup.sh                        # Select Core SIGINT profile, or
./setup.sh --profile=core         # Install core tools including dump1090
```

The setup menu's **Install / Add Modules** option also lets you install dump1090 individually via the Custom tool checklist.

### No aircraft appearing (ADS-B mode)

1. Verify dump1090 is installed
2. Check antenna is connected (1090 MHz antenna recommended)
3. Ensure clear view of sky
4. Set correct observer location for range calculations or use gpsd

### Satellite passes not calculating

1. Ensure skyfield is installed: `apt install python3-skyfield`
2. Check TLE data is valid and recent
3. Verify observer location is set correctly

