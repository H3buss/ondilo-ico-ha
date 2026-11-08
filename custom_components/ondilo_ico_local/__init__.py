"""The Ondilo ICO integration."""

from __future__ import annotations

from collections.abc import Callable

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PORT, CONF_TOKEN, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_FORWARD,
    CONF_RESOLVER,
    CONF_SOURCE,
    DEFAULT_PORT,
    DEFAULT_RESOLVER,
    DOMAIN,
    SOURCE_BRIDGE,
    SOURCE_DIRECT,
)
from .coordinator import (
    IcoSource,
    OndiloIcoBridge,
    OndiloIcoConfigEntry,
    OndiloIcoCoordinator,
)
from .receiver import (
    ReceiverSource,
    async_setup_endpoint,
    async_teardown_endpoint,
)

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR]

# Ondilo keeps its own history, so a report is worth re-sending unless the user
# asked for a fully local setup.
DEFAULT_FORWARD: bool = True


def is_direct(entry: OndiloIcoConfigEntry) -> bool:
    """Return whether the probes of this entry talk to Home Assistant themselves."""
    return entry.data.get(CONF_SOURCE, SOURCE_BRIDGE) == SOURCE_DIRECT


def _stored_option(entry: OndiloIcoConfigEntry, key: str) -> object:
    """Read an option, falling back to what was stored when the entry was made."""
    return entry.options.get(key, entry.data.get(key))


def forward_enabled(entry: OndiloIcoConfigEntry) -> bool:
    """Return whether reports of this entry are also re-sent to Ondilo."""
    value = _stored_option(entry, CONF_FORWARD)
    return DEFAULT_FORWARD if value is None else bool(value)


def resolver_address(entry: OndiloIcoConfigEntry) -> str:
    """Return the resolver to ask the real Ondilo address from, empty if unset."""
    return str(_stored_option(entry, CONF_RESOLVER) or DEFAULT_RESOLVER).strip()


@callback
def _async_wakeup(
    hass: HomeAssistant, coordinator: OndiloIcoCoordinator
) -> Callable[[], None]:
    """Return the callback a report runs so the entities read it right away."""

    def woke_up() -> None:
        hass.async_create_task(coordinator.async_request_refresh())

    return woke_up


async def _async_build_source(
    hass: HomeAssistant, entry: OndiloIcoConfigEntry
) -> IcoSource:
    """Return whatever this entry reads its probes from."""
    if is_direct(entry):
        forward = forward_enabled(entry)
        endpoint = await async_setup_endpoint(
            hass,
            mode="direct + cloud" if forward else "direct",
            forward=forward,
            resolver=resolver_address(entry),
        )
        return ReceiverSource(endpoint)

    return OndiloIcoBridge(
        async_get_clientsession(hass),
        entry.data[CONF_HOST],
        entry.data.get(CONF_PORT, DEFAULT_PORT),
        entry.data.get(CONF_TOKEN),
    )


async def async_setup_entry(hass: HomeAssistant, entry: OndiloIcoConfigEntry) -> None:
    """Set up one ICO source, whether a bridge or the probes themselves."""
    source = await _async_build_source(hass, entry)
    coordinator = OndiloIcoCoordinator(hass, entry, source)

    if isinstance(source, ReceiverSource):
        # A report coming in is an event worth acting on: the refresh interval then
        # stays a backstop, and not the way news reaches the dashboard.
        entry.async_on_unload(
            source.endpoint.receiver.add_listener(_async_wakeup(hass, coordinator))
        )

    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)


async def async_unload_entry(hass: HomeAssistant, entry: OndiloIcoConfigEntry) -> None:
    """Unload one ICO source."""
    await hass.config_entries.async_unload_platforms_or_raise(entry, PLATFORMS)

    if not is_direct(entry):
        return

    # The ICO routes are shared between entries, so they only go quiet once the last
    # entry that wanted them is gone.
    remaining = [
        other
        for other in hass.config_entries.async_entries(DOMAIN)
        if other.entry_id != entry.entry_id
        and other.state is ConfigEntryState.LOADED
        and is_direct(other)
    ]
    if not remaining:
        async_teardown_endpoint(hass)
