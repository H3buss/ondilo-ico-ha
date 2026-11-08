"""Constants for the Ondilo ICO integration."""

from __future__ import annotations

import logging
from typing import Final

DOMAIN: Final = "ondilo_ico_local"
LOGGER: Final = logging.getLogger(__package__)

CONF_SCAN_INTERVAL: Final = "scan_interval"

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

API_PATH_HEALTH: Final = "/health"
API_PATH_DEVICES: Final = "/api/devices"
API_TOKEN_HEADER: Final = "X-API-Token"

MANUFACTURER: Final = "Ondilo"
BRIDGE_MODEL: Final = "ICO bridge"
RECEIVER_MODEL: Final = "ICO receiver"
ICO_MODEL: Final = "ICO V1"
SHORT_ID_LENGTH: Final = 8
