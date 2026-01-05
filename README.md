# INTERCEPT

<p align="center">
  <img src="https://img.shields.io/badge/python-3.9+-blue.svg" alt="Python 3.9+">
  <img src="https://img.shields.io/badge/license-MIT-green.svg" alt="MIT License">
  <img src="https://img.shields.io/badge/platform-macOS%20%7C%20Linux-lightgrey.svg" alt="Platform">
</p>

<p align="center">
  <strong>Signal Intelligence Platform</strong><br>
  A web-based front-end for signal intelligence tools.
</p>

<p align="center">
  <img src="static/images/screenshots/screenshot2.png" alt="Screenshot">
</p>

---

## What is INTERCEPT?

INTERCEPT provides a unified web interface for signal intelligence tools:

- **Pager Decoding** - POCSAG/FLEX via rtl_fm + multimon-ng
- **433MHz Sensors** - Weather stations, TPMS, IoT via rtl_433
- **Aircraft Tracking** - ADS-B via dump1090 with real-time map
- **Satellite Tracking** - Pass prediction using TLE data
- **WiFi Recon** - Monitor mode scanning via aircrack-ng
- **Bluetooth Scanning** - Device discovery and tracker detection

---

## Community

<p align="center">
  <a href="https://discord.gg/z3g3NJMe">Join our Discord</a>
</p>

---

## Quick Start

```bash
git clone https://github.com/smittix/intercept.git
cd intercept
./setup.sh
sudo python3 intercept.py
```

Open http://localhost:5050 in your browser.

> **Note:** Requires Python 3.9+ and external tools. See [Hardware & Installation](docs/HARDWARE.md).

---

## Requirements

- **Python 3.9+**
- **SDR Hardware** - RTL-SDR (~$25), LimeSDR, or HackRF
- **External Tools** - rtl-sdr, multimon-ng, rtl_433, dump1090, aircrack-ng

Quick install (Ubuntu/Debian):
```bash
sudo apt install rtl-sdr multimon-ng rtl-433 dump1090-mutability aircrack-ng bluez
```

See [Hardware & Installation](docs/HARDWARE.md) for full details.

---

## Documentation

| Document | Description |
|----------|-------------|
| [Features](docs/FEATURES.md) | Complete feature list for all modules |
| [Usage Guide](docs/USAGE.md) | Detailed instructions for each mode |
| [Troubleshooting](docs/TROUBLESHOOTING.md) | Solutions for common issues |
| [Hardware & Installation](docs/HARDWARE.md) | SDR hardware and tool installation |

---

## Development

This project was developed using AI as a coding partner, combining human direction with AI-assisted implementation. The goal: make Software Defined Radio more accessible by providing a clean, unified interface for common SDR tools.

Contributions and improvements welcome.

---

## Disclaimer

**This software is for educational purposes only.**

- Only use with proper authorization
- Intercepting communications without consent may be illegal
- WiFi/Bluetooth attacks require explicit permission
- You are responsible for compliance with applicable laws

---

## License

MIT License - see [LICENSE](LICENSE)

## Author

Created by **smittix** - [GitHub](https://github.com/smittix)

## Acknowledgments

[rtl-sdr](https://osmocom.org/projects/rtl-sdr/wiki) |
[multimon-ng](https://github.com/EliasOenal/multimon-ng) |
[rtl_433](https://github.com/merbanan/rtl_433) |
[dump1090](https://github.com/flightaware/dump1090) |
[aircrack-ng](https://www.aircrack-ng.org/) |
[Leaflet.js](https://leafletjs.com/) |
[Celestrak](https://celestrak.org/)
