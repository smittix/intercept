from __future__ import annotations

import logging
import shutil
from typing import Any

logger = logging.getLogger("intercept.dependencies")


def check_tool(name: str) -> bool:
    """Check if a tool is installed."""
    return shutil.which(name) is not None


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
                    "manual": "https://github.com/EliasOewornal/multimon-ng",
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
    "iridium": {
        "name": "Iridium Monitoring",
        "tools": {
            "iridium-extractor": {
                "required": False,
                "description": "Iridium burst extractor",
                "install": {"manual": "https://github.com/muccc/gr-iridium"},
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
