#!/usr/bin/env python3
"""
INTERCEPT - Signal Intelligence Platform

A comprehensive signal intelligence tool featuring:
- Pager decoding (POCSAG/FLEX)
- 433MHz sensor monitoring
- ADS-B aircraft tracking with WarGames-style display
- Satellite pass prediction and Iridium burst detection
- WiFi reconnaissance and drone detection
- Bluetooth scanning

Requires RTL-SDR hardware for RF modes.
"""

import sys

# Check Python version early, before imports that use 3.9+ syntax
if sys.version_info < (3, 9):
    print(f"Error: Python 3.9 or higher is required.")
    print(f"You are running Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")
    print("\nTo fix this:")
    print("  - On Ubuntu/Debian: sudo apt install python3.9 (or newer)")
    print("  - On macOS: brew install python@3.11")
    print("  - Or use pyenv to install a newer version")
    sys.exit(1)

import site

# Ensure user site-packages is available (may be disabled when running as root/sudo)
if not site.ENABLE_USER_SITE:
    user_site = site.getusersitepackages()
    if user_site and user_site not in sys.path:
        sys.path.insert(0, user_site)

from app import main

if __name__ == '__main__':
    main()
