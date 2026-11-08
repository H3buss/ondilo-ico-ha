"""Talk to the local Ondilo ICO bridge and keep its devices up to date."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from http import HTTPStatus
from typing import Any, override
from urllib.parse import quote

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    API_PATH_DEVICES,
    API_PATH_HEALTH,
    API_TOKEN_HEADER,
    BRIDGE_MODEL,
    CONF_SCAN_INTERVAL,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    LOGGER,
    MANUFACTURER,
)

type OndiloIcoConfigEntry = ConfigEntry[OndiloIcoCoordinator]

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=10)


class OndiloIcoError(HomeAssistantError):
    """Base class for errors reported by the ICO bridge."""


class OndiloIcoConnectionError(OndiloIcoError):
    """Raised when the bridge is unreachable or answers badly."""


class OndiloIcoAuthError(OndiloIcoError):
    """Raised when the bridge refuses the API token."""


class OndiloIcoNotFoundError(OndiloIcoError):
    """Raised when the bridge does not know a device."""


class OndiloIcoVersionError(OndiloIcoError):
    """Raised when an endpoint answers, but not like an ICO bridge."""


def _text(payload: Mapping[str, Any], key: str) -> str | None:
    """Return a text field, or None when it is absent or empty."""
    value = payload.get(key)
    return value if isinstance(value, str) and value else None


def _update_failed(error: BaseException) -> UpdateFailed:
    return UpdateFailed(
        translation_domain=DOMAIN,
        translation_key="update_error",
        translation_placeholders={"error": str(error)},
    )


def scan_interval(config_entry: ConfigEntry) -> int:
    """Return the poll interval, where options override the one set at setup."""
    interval = config_entry.options.get(
        CONF_SCAN_INTERVAL,
        config_entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
    )
    return int(interval)


@dataclass(frozen=True, slots=True)
class IcoDevice:
    """What the bridge last heard from one ICO probe."""

    uuid: str
    firmware: str | None
    state: Mapping[str, Any]

    def value(self, key: str) -> Any:
        """Return a state key, or None when the bridge left it out: not measured."""
        return self.state.get(key)

    @classmethod
    def from_payload(cls, uuid: str, payload: Mapping[str, Any]) -> IcoDevice:
        """Build a device out of a /api/devices/<uuid> answer."""
        state = payload.get("state")
        return cls(
            uuid=uuid,
            firmware=_text(payload, "firmware"),
            state=state if isinstance(state, Mapping) else {},
        )


class IcoSource:
    """Where ICO reports come from: a bridge over HTTP, or this very process.

    The entity layer only ever sees devices, so moving from a standalone bridge to
    the probe talking to Home Assistant directly changes which of these two classes
    the coordinator holds, and nothing else.
    """

    model: str = BRIDGE_MODEL
    translation_key: str = "bridge"
    configuration_url: str | None = None
    sw_version: str | None = None

    async def async_prepare(self) -> None:
        """Check the source is there, before any ICO device attaches to it."""

    async def async_devices(self) -> dict[str, IcoDevice]:
        """Return the last known state of every probe."""
        raise NotImplementedError


class OndiloIcoBridge(IcoSource):
    """Read-only client for the bridge HTTP API."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        port: int = DEFAULT_PORT,
        token: str | None = None,
    ) -> None:
        """Initialize the client."""
        self._session = session
        self._token = token
        self.base_url = f"http://{host}:{port}"
        self.configuration_url = self.base_url

    async def async_health(self) -> Mapping[str, Any]:
        """Return /health, refusing answers that do not come from a healthy bridge."""
        health = await self._async_get_json(API_PATH_HEALTH)
        if not isinstance(health, Mapping) or not health.get("ok"):
            raise OndiloIcoVersionError(
                f"{API_PATH_HEALTH} did not report a healthy ICO bridge"
            )
        if _text(health, "version") is None:
            raise OndiloIcoVersionError(f"{API_PATH_HEALTH} reported no bridge version")
        return health

    async def async_device_uuids(self) -> list[str]:
        """Return the uuid of every ICO probe the bridge has seen."""
        payload = await self._async_get_json(API_PATH_DEVICES)
        devices = payload.get("devices") if isinstance(payload, Mapping) else None
        if not isinstance(devices, list):
            raise OndiloIcoVersionError(f"{API_PATH_DEVICES} returned no device list")
        return [
            uuid
            for device in devices
            if isinstance(device, Mapping) and (uuid := _text(device, "uuid"))
        ]

    async def async_device(self, uuid: str) -> Mapping[str, Any]:
        """Return the last report of one ICO probe."""
        path = f"{API_PATH_DEVICES}/{quote(uuid, safe='')}"
        payload = await self._async_get_json(path)
        if not isinstance(payload, Mapping):
            raise OndiloIcoVersionError(f"{API_PATH_DEVICES} returned no device data")
        return payload

    @override
    async def async_prepare(self) -> None:
        """Remember the bridge version, so the service device shows it."""
        self.sw_version = _text(await self.async_health(), "version")

    @override
    async def async_devices(self) -> dict[str, IcoDevice]:
        """Read the health, the device list, then each device report."""
        try:
            # The health endpoint is polled with the data: entities go unavailable
            # as soon as the bridge itself stops answering, not only when a report
            # is missing.
            await self.async_health()
            uuids = await self.async_device_uuids()
            payloads = await asyncio.gather(
                *(self.async_device(uuid) for uuid in uuids),
                return_exceptions=True,
            )
        except OndiloIcoError as err:
            raise _update_failed(err) from err

        devices: dict[str, IcoDevice] = {}
        for uuid, payload in zip(uuids, payloads, strict=True):
            if isinstance(payload, BaseException):
                raise _update_failed(payload) from payload
            devices[uuid] = IcoDevice.from_payload(uuid, payload)
        return devices

    async def _async_get_json(self, path: str) -> Any:
        headers = {API_TOKEN_HEADER: self._token} if self._token else {}
        url = f"{self.base_url}{path}"
        try:
            async with self._session.get(
                url, headers=headers, timeout=REQUEST_TIMEOUT
            ) as response:
                if response.status in (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN):
                    raise OndiloIcoAuthError(f"{path} refused the API token")
                if response.status == HTTPStatus.NOT_FOUND:
                    raise OndiloIcoNotFoundError(f"{path} is not served by the bridge")
                response.raise_for_status()
                return await response.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            raise OndiloIcoConnectionError(f"could not read {path}: {err}") from err


class OndiloIcoCoordinator(DataUpdateCoordinator[dict[str, IcoDevice]]):
    """Keep every ICO probe of one source up to date."""

    config_entry: OndiloIcoConfigEntry

    source_device_id: str | None = None

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: OndiloIcoConfigEntry,
        source: IcoSource,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=scan_interval(config_entry)),
        )
        self.source = source

    @override
    async def _async_setup(self) -> None:
        """Register the service device before the ICO devices link to it."""
        try:
            await self.source.async_prepare()
        except OndiloIcoError as err:
            raise _update_failed(err) from err

        self.source_device_id = dr.async_get(self.hass).async_get_or_create(
            config_entry_id=self.config_entry.entry_id,
            configuration_url=self.source.configuration_url,
            identifiers={
                (DOMAIN, self.config_entry.unique_id or self.config_entry.entry_id)
            },
            manufacturer=MANUFACTURER,
            model=self.source.model,
            sw_version=self.source.sw_version,
            translation_key=self.source.translation_key,
        ).id

    @override
    async def _async_update_data(self) -> dict[str, IcoDevice]:
        """Ask the source what its probes last reported."""
        return await self.source.async_devices()

