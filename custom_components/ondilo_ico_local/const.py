"""Constants for the Ondilo ICO integration."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Final

DOMAIN: Final = "ondilo_ico_local"
LOGGER: Final = logging.getLogger(__package__)

CONF_SCAN_INTERVAL: Final = "scan_interval"
CONF_STALE_AFTER: Final = "stale_after"

# Where the reports come from: a separate bridge program over HTTP, or the probe
# itself, talking to Home Assistant through the port 80 forward.
CONF_SOURCE: Final = "source"
CONF_FORWARD: Final = "forward"
CONF_RESOLVER: Final = "resolver"

SOURCE_BRIDGE: Final = "bridge"
SOURCE_DIRECT: Final = "direct"

# Left empty on purpose: the answer of the system resolver is used as is, and a
# private answer is refused rather than POSTed back to ourselves. Point this at a
# resolver that does not rewrite app.ondilo.com when Home Assistant shares the DNS
# of the machine the probe is redirected to.
DEFAULT_RESOLVER: Final = ""

DEFAULT_PORT: Final = 8099
DEFAULT_SCAN_INTERVAL: Final = 60
MIN_SCAN_INTERVAL: Final = 5
MAX_SCAN_INTERVAL: Final = 3600

# How long a probe may stay silent before its entities say "unavailable", in minutes.
# The firmware measures and uploads once an hour (the configuration it asks for carries
# fast_delay_measure_sec and fast_upload_delay_mes, both 3600) and retries a refused
# report after five minutes, so an hour and a half is one cycle plus half of one.
# Measured on our own captures, though, the reports do not arrive on a metronome: they
# come in bursts, several within a minute, and the gaps between bursts were 29 and 118
# minutes. That second number is why this is an option and not a constant: raise it to
# 180 if a quiet evening turns the dashboard grey.
DEFAULT_STALE_AFTER: Final = 90
MIN_STALE_AFTER: Final = 15
MAX_STALE_AFTER: Final = 1440

# Direct mode has no polling of its own to notice that a probe went quiet: this is how
# often the coordinator is woken up to look at the age of the last report.
STALE_TICK_INTERVAL: Final = timedelta(minutes=5)

API_PATH_HEALTH: Final = "/health"
API_PATH_DEVICES: Final = "/api/devices"
API_TOKEN_HEADER: Final = "X-API-Token"

MANUFACTURER: Final = "Ondilo"
BRIDGE_MODEL: Final = "ICO bridge"
RECEIVER_MODEL: Final = "ICO receiver"
ICO_MODEL: Final = "ICO V1"
SHORT_ID_LENGTH: Final = 8
