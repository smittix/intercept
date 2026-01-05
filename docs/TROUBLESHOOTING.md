# Troubleshooting

Solutions for common issues.

## Python / Installation Issues

### "ModuleNotFoundError: No module named 'flask'"

Install Python dependencies first:
```bash
pip install -r requirements.txt

# Or with python3 explicitly
python3 -m pip install -r requirements.txt
```

### "TypeError: 'type' object is not subscriptable"

This error occurs on Python 3.7 or 3.8. **INTERCEPT requires Python 3.9 or later.**

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
sudo venv/bin/python intercept.py
```

### "externally-managed-environment" error (Ubuntu 23.04+, Debian 12+)

Modern systems use PEP 668 to protect system Python. Use a virtual environment:

```bash
# Option 1: Virtual environment (recommended)
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
sudo venv/bin/python intercept.py

# Option 2: Use the setup script (auto-creates venv if needed)
./setup.sh
```

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

```bash
sudo bash -c 'cat > /etc/udev/rules.d/20-rtlsdr.rules << EOF
SUBSYSTEM=="usb", ATTRS{idVendor}=="0bda", ATTRS{idProduct}=="2838", MODE="0666"
SUBSYSTEM=="usb", ATTRS{idVendor}=="0bda", ATTRS{idProduct}=="2832", MODE="0666"
EOF'

sudo udevadm control --reload-rules
sudo udevadm trigger
```

Then unplug and replug your RTL-SDR.

### Device busy error

1. Click "Kill All Processes" in the UI
2. Unplug and replug the SDR device
3. Check for other applications: `lsof | grep rtl`

### LimeSDR/HackRF not detected

1. Verify SoapySDR is installed: `SoapySDRUtil --info`
2. Check driver is loaded: `SoapySDRUtil --find`
3. May need udev rules or run as root

## WiFi Issues

### Monitor mode fails

1. Ensure running as root/sudo
2. Check adapter supports monitor mode: `iw list | grep monitor`
3. Kill interfering processes: `airmon-ng check kill`

### Permission denied when scanning

Run INTERCEPT with sudo:
```bash
sudo python3 intercept.py
# Or with venv:
sudo venv/bin/python intercept.py
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

## GPS Issues

### GPS dongle not detected

1. Install pyserial: `pip install pyserial`
2. Check device is connected:
   - Linux: `ls /dev/ttyUSB* /dev/ttyACM*`
   - macOS: `ls /dev/tty.usb*`
3. Add user to dialout group (Linux):
   ```bash
   sudo usermod -a -G dialout $USER
   ```
4. Most GPS dongles use 9600 baud (default in INTERCEPT)
5. GPS needs clear sky view to get a fix

## Decoding Issues

### No messages appearing (Pager mode)

1. Verify frequency is correct for your area
2. Adjust gain (try 30-40 dB)
3. Check pager services are active in your area
4. Ensure antenna is connected

### No aircraft appearing (ADS-B mode)

1. Verify dump1090 or readsb is installed
2. Check antenna is connected (1090 MHz antenna recommended)
3. Ensure clear view of sky
4. Set correct observer location for range calculations

### Satellite passes not calculating

1. Ensure skyfield is installed: `pip install skyfield`
2. Check TLE data is valid and recent
3. Verify observer location is set correctly
