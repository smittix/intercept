#!/bin/bash
#
# INTERCEPT Setup Script
# Installs Python dependencies and checks for external tools
#

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}"
echo "  ___ _   _ _____ _____ ____   ____ _____ ____ _____ "
echo " |_ _| \\ | |_   _| ____|  _ \\ / ___| ____|  _ \\_   _|"
echo "  | ||  \\| | | | |  _| | |_) | |   |  _| | |_) || |  "
echo "  | || |\\  | | | | |___|  _ <| |___| |___|  __/ | |  "
echo " |___|_| \\_| |_| |_____|_| \\_\\\\____|_____|_|    |_|  "
echo -e "${NC}"
echo "Signal Intelligence Platform - Setup Script"
echo "============================================"
echo ""

# Detect OS
detect_os() {
    if [[ "$OSTYPE" == "darwin"* ]]; then
        OS="macos"
        PKG_MANAGER="brew"
    elif [[ -f /etc/debian_version ]]; then
        OS="debian"
        PKG_MANAGER="apt"
    elif [[ -f /etc/redhat-release ]]; then
        OS="redhat"
        PKG_MANAGER="dnf"
    elif [[ -f /etc/arch-release ]]; then
        OS="arch"
        PKG_MANAGER="pacman"
    else
        OS="unknown"
        PKG_MANAGER="unknown"
    fi
    echo -e "${BLUE}Detected OS:${NC} $OS (package manager: $PKG_MANAGER)"
}

# Check if a command exists
check_cmd() {
    command -v "$1" &> /dev/null
}

# Install Python dependencies
install_python_deps() {
    echo ""
    echo -e "${BLUE}[1/3] Installing Python dependencies...${NC}"

    if ! check_cmd python3; then
        echo -e "${RED}Error: Python 3 is not installed${NC}"
        echo "Please install Python 3.9 or later"
        exit 1
    fi

    # Check Python version (need 3.9+)
    PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    PYTHON_MAJOR=$(python3 -c 'import sys; print(sys.version_info.major)')
    PYTHON_MINOR=$(python3 -c 'import sys; print(sys.version_info.minor)')
    echo "Python version: $PYTHON_VERSION"

    if [ "$PYTHON_MAJOR" -lt 3 ] || ([ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -lt 9 ]); then
        echo -e "${RED}Error: Python 3.9 or later is required${NC}"
        echo "You have Python $PYTHON_VERSION"
        echo ""
        echo "Please upgrade Python:"
        echo "  Ubuntu/Debian: sudo apt install python3.11"
        echo "  macOS: brew install python@3.11"
        exit 1
    fi

    # Check if we're in a virtual environment
    if [ -n "$VIRTUAL_ENV" ]; then
        echo "Using virtual environment: $VIRTUAL_ENV"
        pip install -r requirements.txt
    elif [ -d "venv" ]; then
        echo "Found existing venv, activating..."
        source venv/bin/activate
        pip install -r requirements.txt
    else
        # Try direct pip install first, fall back to venv if it fails (PEP 668)
        echo "Attempting to install dependencies..."
        if python3 -m pip install -r requirements.txt 2>/dev/null; then
            echo -e "${GREEN}Python dependencies installed successfully${NC}"
            return
        fi

        # If pip install failed (likely PEP 668), create a virtual environment
        echo ""
        echo -e "${YELLOW}System Python is externally managed (PEP 668).${NC}"
        echo "Creating virtual environment..."
        python3 -m venv venv
        source venv/bin/activate
        pip install -r requirements.txt
        echo ""
        echo -e "${YELLOW}NOTE: A virtual environment was created.${NC}"
        echo "You must activate it before running INTERCEPT:"
        echo "  source venv/bin/activate"
        echo "  sudo venv/bin/python intercept.py"
    fi

    echo -e "${GREEN}Python dependencies installed successfully${NC}"
}

# Check external tools
check_tools() {
    echo ""
    echo -e "${BLUE}[2/3] Checking external tools...${NC}"
    echo ""

    MISSING_TOOLS=()

    # Core SDR tools
    echo "Core SDR Tools:"
    check_tool "rtl_fm" "RTL-SDR FM demodulator"
    check_tool "rtl_test" "RTL-SDR device detection"
    check_tool "multimon-ng" "Pager decoder"
    check_tool "rtl_433" "433MHz sensor decoder"
    check_tool "dump1090" "ADS-B decoder"

    echo ""
    echo "Additional SDR Hardware (optional):"
    check_tool "SoapySDRUtil" "SoapySDR (for LimeSDR/HackRF)"
    check_tool "LimeUtil" "LimeSDR tools"
    check_tool "hackrf_info" "HackRF tools"

    echo ""
    echo "WiFi Tools:"
    check_tool "airmon-ng" "WiFi monitor mode"
    check_tool "airodump-ng" "WiFi scanner"

    echo ""
    echo "Bluetooth Tools:"
    check_tool "bluetoothctl" "Bluetooth controller"
    check_tool "hcitool" "Bluetooth HCI tool"

    if [ ${#MISSING_TOOLS[@]} -gt 0 ]; then
        echo ""
        echo -e "${YELLOW}Some tools are missing. See installation instructions below.${NC}"
    fi
}

check_tool() {
    local cmd=$1
    local desc=$2
    if check_cmd "$cmd"; then
        echo -e "  ${GREEN}✓${NC} $cmd - $desc"
    else
        echo -e "  ${RED}✗${NC} $cmd - $desc ${YELLOW}(not found)${NC}"
        MISSING_TOOLS+=("$cmd")
    fi
}

# Show installation instructions
show_install_instructions() {
    echo ""
    echo -e "${BLUE}[3/3] Installation instructions for missing tools${NC}"
    echo ""

    if [ ${#MISSING_TOOLS[@]} -eq 0 ]; then
        echo -e "${GREEN}All tools are installed!${NC}"
        return
    fi

    echo "Run the following commands to install missing tools:"
    echo ""

    if [[ "$OS" == "macos" ]]; then
        echo -e "${YELLOW}macOS (Homebrew):${NC}"
        echo ""

        # Check if Homebrew is installed
        if ! check_cmd brew; then
            echo "First, install Homebrew:"
            echo '  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
            echo ""
        fi

        echo "# Core SDR tools"
        echo "brew install librtlsdr multimon-ng rtl_433 dump1090-mutability"
        echo ""
        echo "# LimeSDR support (optional)"
        echo "brew install soapysdr limesuite soapylms7"
        echo ""
        echo "# HackRF support (optional)"
        echo "brew install hackrf soapyhackrf"
        echo ""
        echo "# WiFi tools"
        echo "brew install aircrack-ng"

    elif [[ "$OS" == "debian" ]]; then
        echo -e "${YELLOW}Ubuntu/Debian:${NC}"
        echo ""
        echo "# Core SDR tools"
        echo "sudo apt update"
        echo "sudo apt install rtl-sdr multimon-ng rtl-433 dump1090-mutability"
        echo ""
        echo "# LimeSDR support (optional)"
        echo "sudo apt install soapysdr-tools limesuite soapysdr-module-lms7"
        echo ""
        echo "# HackRF support (optional)"
        echo "sudo apt install hackrf soapysdr-module-hackrf"
        echo ""
        echo "# WiFi tools"
        echo "sudo apt install aircrack-ng"
        echo ""
        echo "# Bluetooth tools"
        echo "sudo apt install bluez bluetooth"

    elif [[ "$OS" == "arch" ]]; then
        echo -e "${YELLOW}Arch Linux:${NC}"
        echo ""
        echo "# Core SDR tools"
        echo "sudo pacman -S rtl-sdr multimon-ng"
        echo "yay -S rtl_433 dump1090"
        echo ""
        echo "# LimeSDR/HackRF support (optional)"
        echo "sudo pacman -S soapysdr limesuite hackrf"

    elif [[ "$OS" == "redhat" ]]; then
        echo -e "${YELLOW}Fedora/RHEL:${NC}"
        echo ""
        echo "# Core SDR tools"
        echo "sudo dnf install rtl-sdr"
        echo "# multimon-ng, rtl_433, dump1090 may need to be built from source"

    else
        echo "Please install the following tools manually:"
        for tool in "${MISSING_TOOLS[@]}"; do
            echo "  - $tool"
        done
    fi
}

# RTL-SDR udev rules (Linux only)
setup_udev_rules() {
    if [[ "$OS" != "macos" ]] && [[ "$OS" != "unknown" ]]; then
        echo ""
        echo -e "${BLUE}RTL-SDR udev rules (Linux only):${NC}"
        echo ""
        echo "If your RTL-SDR is not detected, you may need to add udev rules:"
        echo ""
        echo "sudo bash -c 'cat > /etc/udev/rules.d/20-rtlsdr.rules << EOF"
        echo 'SUBSYSTEM=="usb", ATTRS{idVendor}=="0bda", ATTRS{idProduct}=="2838", MODE="0666"'
        echo 'SUBSYSTEM=="usb", ATTRS{idVendor}=="0bda", ATTRS{idProduct}=="2832", MODE="0666"'
        echo "EOF'"
        echo ""
        echo "sudo udevadm control --reload-rules"
        echo "sudo udevadm trigger"
        echo ""
        echo "Then unplug and replug your RTL-SDR device."
    fi
}

# Main
main() {
    detect_os
    install_python_deps
    check_tools
    show_install_instructions
    setup_udev_rules

    echo ""
    echo "============================================"
    echo -e "${GREEN}Setup complete!${NC}"
    echo ""
    echo "To start INTERCEPT:"
    if [ -d "venv" ]; then
        echo "  source venv/bin/activate"
        echo "  sudo venv/bin/python intercept.py"
    else
        echo "  sudo python3 intercept.py"
    fi
    echo ""
    echo "Then open http://localhost:5050 in your browser"
    echo ""
}

main "$@"
