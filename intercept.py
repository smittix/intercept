#!/usr/bin/env python3
"""
INTERCEPT - Signal Intelligence Platform

A comprehensive signal intelligence tool featuring:
- Pager decoding (POCSAG/FLEX)
- 433MHz sensor monitoring
- ADS-B aircraft tracking with WarGames-style display
- Satellite pass prediction
- WiFi reconnaissance and drone detection
- Bluetooth scanning

Requires RTL-SDR hardware for RF modes.
"""

import sys

# Check Python version early, before imports that use 3.9+ syntax

# Handle --version early before other imports
if '--version' in sys.argv or '-V' in sys.argv:
    from config import VERSION
    print(f"INTERCEPT v{VERSION}")
    sys.exit(0)

import site
from pathlib import Path

# Ensure user site-packages is available (may be disabled when running as root/sudo)
if not site.ENABLE_USER_SITE:
    user_site = site.getusersitepackages()
    if user_site and user_site not in sys.path:
        sys.path.insert(0, user_site)

# Load .env before importing app so env vars are available to config.py
try:
    from dotenv import load_dotenv
    _env_path = Path(__file__).parent / '.env'
    if _env_path.exists():
        load_dotenv(_env_path, override=False)
except ImportError:
    pass

from app import main

if __name__ == '__main__':
    main()
