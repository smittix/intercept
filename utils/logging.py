"""Logging utilities for intercept application."""

from __future__ import annotations

import logging
import sys

from config import LOG_LEVEL, LOG_FORMAT


def get_logger(name: str) -> logging.Logger:
    """Get a configured logger for a module."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter(LOG_FORMAT))
        logger.addHandler(handler)
        logger.setLevel(LOG_LEVEL)
    return logger


# Pre-configured loggers for each module
app_logger = get_logger("intercept")
pager_logger = get_logger("intercept.pager")
sensor_logger = get_logger("intercept.sensor")
wifi_logger = get_logger("intercept.wifi")
bluetooth_logger = get_logger("intercept.bluetooth")
adsb_logger = get_logger("intercept.adsb")
satellite_logger = get_logger("intercept.satellite")
iridium_logger = get_logger("intercept.iridium")
