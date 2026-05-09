from __future__ import annotations

import logging
import os
import platform
import shutil
import subprocess
from typing import Any

logger = logging.getLogger("intercept.dependencies")

# Additional paths to search for tools (e.g., /usr/sbin on Debian, /usr/local/bin for source builds)
EXTRA_TOOL_PATHS = ["/usr/local/bin", "/usr/sbin", "/sbin"]

# Tools installed to non-standard locations (not on PATH)
KNOWN_TOOL_PATHS: dict[str, list[str]] = {
    "auto_rx.py": [
        "/opt/radiosonde_auto_rx/auto_rx/auto_rx.py",
        "/opt/auto_rx/auto_rx.py",
    ],
}


def check_tool(name: str) -> bool:
    """Check if a tool is installed."""
    return get_tool_path(name) is not None


def get_tool_path(name: str) -> str | None:
    """Get the full path to a tool, checking standard PATH and extra locations."""
    # Optional explicit override, e.g. INTERCEPT_RTL_FM_PATH=/opt/homebrew/bin/rtl_fm
    env_key = f"INTERCEPT_{name.upper().replace('-', '_')}_PATH"
    env_path = os.environ.get(env_key)
    if env_path and os.path.isfile(env_path) and os.access(env_path, os.X_OK):
        return env_path

    # Prefer native Homebrew binaries on Apple Silicon to avoid mixing Rosetta
    # /usr/local tools with arm64 Python/runtime.
    if platform.system() == "Darwin":
        machine = platform.machine().lower()
        preferred_paths: list[str] = []
        if machine in {"arm64", "aarch64"}:
            preferred_paths.append("/opt/homebrew/bin")
        preferred_paths.append("/usr/local/bin")

        for base in preferred_paths:
            full_path = os.path.join(base, name)
            if os.path.isfile(full_path) and os.access(full_path, os.X_OK):
                return full_path

    # First check standard PATH
    path = shutil.which(name)
    if path:
        return path

    # Check additional paths (e.g., /usr/sbin for aircrack-ng on Debian)
    for extra_path in EXTRA_TOOL_PATHS:
        full_path = os.path.join(extra_path, name)
        if os.path.isfile(full_path) and os.access(full_path, os.X_OK):
            return full_path

    # Check known non-standard install locations
    for known_path in KNOWN_TOOL_PATHS.get(name, []):
        if os.path.isfile(known_path):
            return known_path

    return None


def _get_soapy_env() -> dict[str, str]:
    """Get environment variables needed for SoapySDR on macOS.

    On macOS with Homebrew, SoapySDR modules are installed in paths that
    require SOAPY_SDR_ROOT or DYLD_LIBRARY_PATH to be set. This fixes
    detection issues where modules like SoapyHackRF are installed but
    not found by SoapySDRUtil.

    See: https://github.com/smittix/intercept/issues/77
    """
    import platform

    env = os.environ.copy()

    if platform.system() == "Darwin":
        # Homebrew paths for Apple Silicon and Intel Macs
        homebrew_paths = ["/opt/homebrew", "/usr/local"]
        lib_paths = []
        module_paths = []

        for base in homebrew_paths:
            lib_path = f"{base}/lib"
            if os.path.isdir(lib_path):
                lib_paths.append(lib_path)
            # SoapySDR modules are in lib/SoapySDR/modules<version>
            soapy_mod_base = f"{base}/lib/SoapySDR"
            if os.path.isdir(soapy_mod_base):
                module_paths.append(soapy_mod_base)

        if lib_paths:
            current_dyld = env.get("DYLD_LIBRARY_PATH", "")
            env["DYLD_LIBRARY_PATH"] = ":".join(lib_paths + ([current_dyld] if current_dyld else []))

        # Set SOAPY_SDR_ROOT if we found Homebrew installation
        for base in homebrew_paths:
            if os.path.isdir(f"{base}/lib/SoapySDR"):
                env["SOAPY_SDR_ROOT"] = base
                break

    return env


def check_soapy_factory(factory_name: str) -> bool:
    """Check if a SoapySDR factory/module is available using SoapySDRUtil."""
    try:
        # Run SoapySDRUtil --info and look for the factory in 'Available factories'
        # Use macOS-aware environment to find Homebrew-installed modules
        env = _get_soapy_env()
        result = subprocess.run(["SoapySDRUtil", "--info"], capture_output=True, text=True, env=env)
        if result.returncode != 0:
            return False

        # Parse output for available factories
        # Format usually: "Available factories... hackrf, lime, rtlsdr"
        for line in result.stdout.splitlines():
            if "Available factories" in line:
                factories = line.split("...")[-1].strip().split(",")
                factories = [f.strip() for f in factories]
                if factory_name in factories:
                    return True
        return False
    except Exception as e:
        logger.debug(f"Failed to check SoapySDR factory {factory_name}: {e}")
        return False


# Comprehensive tool dependency definitions
TOOL_DEPENDENCIES = {
    "pager": {
        "name": "Pager Decoding",
        "tools": {
            "rtl_fm": {
                "required": True,
                "description": "RTL-SDR FM demodulator",
                "install": {
                    "apt": "sudo apt install rtl-sdr",
                    "brew": "brew install librtlsdr",
                    "manual": "https://osmocom.org/projects/rtl-sdr/wiki",
                },
            },
            "multimon-ng": {
                "required": True,
                "description": "Digital transmission decoder",
                "install": {
                    "apt": "sudo apt install multimon-ng",
                    "brew": "brew install multimon-ng",
                    "manual": "https://github.com/EliasOenal/multimon-ng",
                },
            },
            "rtl_test": {
                "required": False,
                "description": "RTL-SDR device detection",
                "install": {
                    "apt": "sudo apt install rtl-sdr",
                    "brew": "brew install librtlsdr",
                    "manual": "https://osmocom.org/projects/rtl-sdr/wiki",
                },
            },
        },
    },
    "sensor": {
        "name": "433MHz Sensors",
        "tools": {
            "rtl_433": {
                "required": True,
                "description": "ISM band decoder for sensors, weather stations, TPMS",
                "install": {
                    "apt": "sudo apt install rtl-433",
                    "brew": "brew install rtl_433",
                    "manual": "https://github.com/merbanan/rtl_433",
                },
            }
        },
    },
    "wifi": {
        "name": "WiFi Reconnaissance",
        "tools": {
            "airmon-ng": {
                "required": True,
                "description": "Monitor mode controller",
                "install": {
                    "apt": "sudo apt install aircrack-ng",
                    "brew": "Not available on macOS",
                    "manual": "https://aircrack-ng.org",
                },
            },
            "airodump-ng": {
                "required": True,
                "description": "WiFi network scanner",
                "install": {
                    "apt": "sudo apt install aircrack-ng",
                    "brew": "Not available on macOS",
                    "manual": "https://aircrack-ng.org",
                },
            },
            "aireplay-ng": {
                "required": False,
                "description": "Deauthentication / packet injection",
                "install": {
                    "apt": "sudo apt install aircrack-ng",
                    "brew": "Not available on macOS",
                    "manual": "https://aircrack-ng.org",
                },
            },
            "aircrack-ng": {
                "required": False,
                "description": "Handshake verification",
                "install": {
                    "apt": "sudo apt install aircrack-ng",
                    "brew": "brew install aircrack-ng",
                    "manual": "https://aircrack-ng.org",
                },
            },
            "hcxdumptool": {
                "required": False,
                "description": "PMKID capture tool",
                "install": {
                    "apt": "sudo apt install hcxdumptool",
                    "brew": "brew install hcxtools",
                    "manual": "https://github.com/ZerBea/hcxdumptool",
                },
            },
            "hcxpcapngtool": {
                "required": False,
                "description": "PMKID hash extractor",
                "install": {
                    "apt": "sudo apt install hcxtools",
                    "brew": "brew install hcxtools",
                    "manual": "https://github.com/ZerBea/hcxtools",
                },
            },
        },
    },
    "bluetooth": {
        "name": "Bluetooth Scanning",
        "tools": {
            "hcitool": {
                "required": False,
                "description": "Bluetooth HCI tool (legacy)",
                "install": {
                    "apt": "sudo apt install bluez",
                    "brew": "Not available on macOS (use native)",
                    "manual": "http://www.bluez.org",
                },
            },
            "bluetoothctl": {
                "required": True,
                "description": "Modern Bluetooth controller",
                "install": {
                    "apt": "sudo apt install bluez",
                    "brew": "Not available on macOS (use native)",
                    "manual": "http://www.bluez.org",
                },
            },
            "hciconfig": {
                "required": False,
                "description": "Bluetooth adapter configuration",
                "install": {
                    "apt": "sudo apt install bluez",
                    "brew": "Not available on macOS",
                    "manual": "http://www.bluez.org",
                },
            },
        },
    },
    "aircraft": {
        "name": "Aircraft Tracking (ADS-B)",
        "tools": {
            "dump1090": {
                "required": False,
                "description": "Mode S / ADS-B decoder (preferred)",
                "install": {
                    "apt": "sudo apt install dump1090-mutability (or build dump1090-fa from source)",
                    "brew": "brew install dump1090-mutability",
                    "manual": "https://github.com/flightaware/dump1090",
                },
                "alternatives": ["dump1090-mutability", "dump1090-fa"],
            },
            "rtl_adsb": {
                "required": False,
                "description": "Simple ADS-B decoder",
                "install": {
                    "apt": "sudo apt install rtl-sdr",
                    "brew": "brew install librtlsdr",
                    "manual": "https://osmocom.org/projects/rtl-sdr/wiki",
                },
            },
        },
    },
    "acars": {
        "name": "Aircraft Messaging (ACARS)",
        "tools": {
            "acarsdec": {
                "required": True,
                "description": "ACARS VHF decoder",
                "install": {
                    "apt": "Run ./setup.sh (builds from source)",
                    "brew": "Run ./setup.sh (builds from source)",
                    "manual": "https://github.com/TLeconte/acarsdec",
                },
            }
        },
    },
    "ais": {
        "name": "Vessel Tracking (AIS)",
        "tools": {
            "AIS-catcher": {
                "required": True,
                "description": "AIS receiver and decoder",
                "install": {
                    "apt": "Download .deb from https://github.com/jvde-github/AIS-catcher/releases",
                    "brew": "brew install aiscatcher",
                    "manual": "https://github.com/jvde-github/AIS-catcher/releases",
                },
            }
        },
    },
    "aprs": {
        "name": "APRS Tracking",
        "tools": {
            "direwolf": {
                "required": False,
                "description": "APRS/packet radio decoder (preferred)",
                "install": {
                    "apt": "sudo apt install direwolf",
                    "brew": "brew install direwolf",
                    "manual": "https://github.com/wb2osz/direwolf",
                },
            },
            "multimon-ng": {
                "required": False,
                "description": "Alternative AFSK1200 decoder",
                "install": {
                    "apt": "sudo apt install multimon-ng",
                    "brew": "brew install multimon-ng",
                    "manual": "https://github.com/EliasOenal/multimon-ng",
                },
            },
        },
    },
    "satellite": {
        "name": "Satellite Tracking",
        "tools": {
            "skyfield": {
                "required": True,
                "description": "Python orbital mechanics library",
                "install": {"pip": "pip install skyfield", "manual": "https://rhodesmill.org/skyfield/"},
                "python_module": True,
            }
        },
    },
    "sdr_hardware": {
        "name": "SDR Hardware Support",
        "tools": {
            "SoapySDRUtil": {
                "required": False,
                "description": "Universal SDR abstraction (required for LimeSDR, HackRF)",
                "install": {
                    "apt": "sudo apt install soapysdr-tools",
                    "brew": "brew install soapysdr",
                    "manual": "https://github.com/pothosware/SoapySDR",
                },
            },
            "rx_fm": {
                "required": False,
                "description": "SoapySDR FM receiver (for non-RTL hardware)",
                "install": {"manual": "Part of SoapySDR utilities or build from source"},
            },
            "LimeUtil": {
                "required": False,
                "description": "LimeSDR native utilities",
                "install": {
                    "apt": "sudo apt install limesuite",
                    "brew": "brew install limesuite",
                    "manual": "https://github.com/myriadrf/LimeSuite",
                },
            },
            "SoapyLMS7": {
                "required": False,
                "description": "SoapySDR plugin for LimeSDR",
                "soapy_factory": "lime",
                "install": {
                    "apt": "sudo apt install soapysdr-module-lms7",
                    "brew": "brew install soapylms7",
                    "manual": "https://github.com/myriadrf/LimeSuite",
                },
            },
            "hackrf_info": {
                "required": False,
                "description": "HackRF native utilities",
                "install": {
                    "apt": "sudo apt install hackrf",
                    "brew": "brew install hackrf",
                    "manual": "https://github.com/greatscottgadgets/hackrf",
                },
            },
            "SoapyHackRF": {
                "required": False,
                "description": "SoapySDR plugin for HackRF",
                "soapy_factory": "hackrf",
                "install": {
                    "apt": "sudo apt install soapysdr-module-hackrf",
                    "brew": "brew install soapyhackrf",
                    "manual": "https://github.com/pothosware/SoapyHackRF",
                },
            },
            "readsb": {
                "required": False,
                "description": "ADS-B decoder with SoapySDR support",
                "install": {
                    "apt": "Build from source with SoapySDR support",
                    "brew": "Build from source with SoapySDR support",
                    "manual": "https://github.com/wiedehopf/readsb",
                },
            },
        },
    },
    "subghz": {
        "name": "SubGHz Transceiver",
        "tools": {
            "hackrf_transfer": {
                "required": True,
                "description": "HackRF IQ capture and replay",
                "install": {
                    "apt": "sudo apt install hackrf",
                    "brew": "brew install hackrf",
                    "manual": "https://github.com/greatscottgadgets/hackrf",
                },
            },
            "hackrf_sweep": {
                "required": False,
                "description": "HackRF wideband spectrum sweep",
                "install": {
                    "apt": "sudo apt install hackrf",
                    "brew": "brew install hackrf",
                    "manual": "https://github.com/greatscottgadgets/hackrf",
                },
            },
            "rtl_433": {
                "required": False,
                "description": "Protocol decoder for SubGHz signals",
                "install": {
                    "apt": "sudo apt install rtl-433",
                    "brew": "brew install rtl_433",
                    "manual": "https://github.com/merbanan/rtl_433",
                },
            },
        },
    },
    "radiosonde": {
        "name": "Radiosonde Tracking",
        "tools": {
            "auto_rx.py": {
                "required": True,
                "description": "Radiosonde weather balloon decoder",
                "install": {
                    "apt": "Run ./setup.sh (clones from GitHub)",
                    "brew": "Run ./setup.sh (clones from GitHub)",
                    "manual": "https://github.com/projecthorus/radiosonde_auto_rx",
                },
            }
        },
    },
    "tscm": {
        "name": "TSCM Counter-Surveillance",
        "tools": {
            "rtl_power": {
                "required": False,
                "description": "Wideband spectrum sweep for RF analysis",
                "install": {
                    "apt": "sudo apt install rtl-sdr",
                    "brew": "brew install librtlsdr",
                    "manual": "https://osmocom.org/projects/rtl-sdr/wiki",
                },
            },
            "rtl_fm": {
                "required": True,
                "description": "RF signal demodulation",
                "install": {
                    "apt": "sudo apt install rtl-sdr",
                    "brew": "brew install librtlsdr",
                    "manual": "https://osmocom.org/projects/rtl-sdr/wiki",
                },
            },
            "rtl_433": {
                "required": False,
                "description": "ISM band device decoding",
                "install": {
                    "apt": "sudo apt install rtl-433",
                    "brew": "brew install rtl_433",
                    "manual": "https://github.com/merbanan/rtl_433",
                },
            },
            "airmon-ng": {
                "required": False,
                "description": "WiFi monitor mode for network scanning",
                "install": {
                    "apt": "sudo apt install aircrack-ng",
                    "brew": "Not available on macOS",
                    "manual": "https://aircrack-ng.org",
                },
            },
            "bluetoothctl": {
                "required": False,
                "description": "Bluetooth device scanning",
                "install": {
                    "apt": "sudo apt install bluez",
                    "brew": "Not available on macOS (use native)",
                    "manual": "http://www.bluez.org",
                },
            },
        },
    },
}


def check_all_dependencies() -> dict[str, dict[str, Any]]:
    """Check all tool dependencies and return status."""
    results: dict[str, dict[str, Any]] = {}

    for mode, config in TOOL_DEPENDENCIES.items():
        mode_result = {"name": config["name"], "tools": {}, "ready": True, "missing_required": []}

        for tool, tool_config in config["tools"].items():
            # Check if it's a Python module
            if tool_config.get("python_module"):
                try:
                    __import__(tool)
                    installed = True
                except Exception as e:
                    logger.debug(f"Failed to import {tool}: {type(e).__name__}: {e}")
                    installed = False
            # Check using SoapySDRUtil if specified
            elif tool_config.get("soapy_factory"):
                installed = check_soapy_factory(tool_config["soapy_factory"])
            else:
                # Check for alternatives
                alternatives = tool_config.get("alternatives", [])
                installed = check_tool(tool) or any(check_tool(alt) for alt in alternatives)

            mode_result["tools"][tool] = {
                "installed": installed,
                "required": tool_config["required"],
                "description": tool_config["description"],
                "install": tool_config["install"],
            }

            if tool_config["required"] and not installed:
                mode_result["ready"] = False
                mode_result["missing_required"].append(tool)

        results[mode] = mode_result

    return results
