"""Config flow for the Ondilo ICO integration."""

from __future__ import annotations

from collections.abc import Mapping
from ipaddress import IPv4Address
from typing import Any, override

import probatio

from homeassistant.config_entries import (
    SOURCE_RECONFIGURE,
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_HOST, CONF_PORT, CONF_TOKEN, UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    BooleanSelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    SelectOptionDict,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    CONF_FORWARD,
    CONF_RESOLVER,
    CONF_SCAN_INTERVAL,
    CONF_SOURCE,
    CONF_STALE_AFTER,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MAX_STALE_AFTER,
    MIN_SCAN_INTERVAL,
    MIN_STALE_AFTER,
    SOURCE_BRIDGE,
    SOURCE_DIRECT,
)
from .coordinator import (
    OndiloIcoAuthError,
    OndiloIcoBridge,
    OndiloIcoError,
    OndiloIcoNotFoundError,
    OndiloIcoVersionError,
    scan_interval,
    stale_after_minutes,
)

SCAN_INTERVAL_SELECTOR = NumberSelector(
    NumberSelectorConfig(
        min=MIN_SCAN_INTERVAL,
        max=MAX_SCAN_INTERVAL,
        step=5,
        unit_of_measurement=UnitOfTime.SECONDS,
        mode=NumberSelectorMode.BOX,
    )
)

FORWARD_SELECTOR = BooleanSelector(BooleanSelectorConfig())

# The probe reports once an hour, so this is the one number a user may want to move:
# how long to wait before calling a silent probe unavailable.
STALE_SELECTOR = NumberSelector(
    NumberSelectorConfig(
        min=MIN_STALE_AFTER,
        max=MAX_STALE_AFTER,
        step=5,
        unit_of_measurement=UnitOfTime.MINUTES,
        mode=NumberSelectorMode.BOX,
    )
)

RESOLVER_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT))

# Only one entry can own the ICO routes of a Home Assistant instance: they are
# mounted once on the shared web server, whichever entry asked for them.
DIRECT_UNIQUE_ID = "direct"

SOURCE_SCHEMA = probatio.Schema(
    {
        probatio.Required(CONF_SOURCE): SelectSelector(
            SelectSelectorConfig(
                options=[
                    SelectOptionDict(
                        value=SOURCE_DIRECT,
                        label="The probe talks to Home Assistant itself",
                    ),
                    SelectOptionDict(
                        value=SOURCE_BRIDGE,
                        label="A separate bridge program on another machine",
                    ),
                ],
                mode=SelectSelectorMode.DROPDOWN,
            )
        )
    }
)

BRIDGE_SCHEMA = probatio.Schema(
    {
        probatio.Required(CONF_HOST): TextSelector(
            TextSelectorConfig(type=TextSelectorType.TEXT)
        ),
        probatio.Required(CONF_PORT): NumberSelector(
            NumberSelectorConfig(
                min=1, max=65535, step=1, mode=NumberSelectorMode.BOX
            )
        ),
        probatio.Optional(CONF_TOKEN): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
        probatio.Optional(CONF_SCAN_INTERVAL): SCAN_INTERVAL_SELECTOR,
    }
)

DIRECT_SCHEMA = probatio.Schema(
    {
        probatio.Optional(CONF_FORWARD, default=True): FORWARD_SELECTOR,
        probatio.Optional(CONF_RESOLVER): RESOLVER_SELECTOR,
        probatio.Optional(CONF_SCAN_INTERVAL): SCAN_INTERVAL_SELECTOR,
    }
)

DIRECT_RECONFIGURE_SCHEMA = probatio.Schema(
    {
        probatio.Required(CONF_FORWARD): FORWARD_SELECTOR,
        probatio.Optional(CONF_RESOLVER): RESOLVER_SELECTOR,
    }
)

OPTIONS_SCHEMA = probatio.Schema(
    {
        probatio.Required(CONF_SCAN_INTERVAL): SCAN_INTERVAL_SELECTOR,
        probatio.Required(CONF_STALE_AFTER): STALE_SELECTOR,
    }
)

DIRECT_OPTIONS_SCHEMA = probatio.Schema(
    {
        probatio.Required(CONF_SCAN_INTERVAL): SCAN_INTERVAL_SELECTOR,
        probatio.Required(CONF_STALE_AFTER): STALE_SELECTOR,
        probatio.Required(CONF_FORWARD): FORWARD_SELECTOR,
        probatio.Optional(CONF_RESOLVER): RESOLVER_SELECTOR,
    }
)

DEFAULT_INPUT: Mapping[str, Any] = {
    CONF_PORT: DEFAULT_PORT,
    CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
}

DIRECT_DEFAULT_INPUT: Mapping[str, Any] = {
    CONF_FORWARD: True,
    CONF_RESOLVER: "",
    CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
}


def _is_ipv4(value: str) -> bool:
    """Return whether we could ask this address a DNS question over UDP."""
    try:
        IPv4Address(value)
    except ValueError:
        return False
    return True


def _is_direct_entry_data(data: Mapping[str, Any]) -> bool:
    """Return whether a set of entry data describes the direct mode."""
    return data.get(CONF_SOURCE) == SOURCE_DIRECT


def _bridge_input(user_input: Mapping[str, Any]) -> dict[str, Any]:
    """Keep the numbers a selector gives us as plain numbers."""
    return {
        CONF_HOST: user_input[CONF_HOST].strip(),
        CONF_PORT: int(user_input[CONF_PORT]),
        CONF_TOKEN: user_input.get(CONF_TOKEN, "").strip(),
        CONF_SCAN_INTERVAL: int(
            user_input.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        ),
    }


def _direct_input(
    user_input: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Split what the direct form collects into entry data and options."""
    return (
        {
            CONF_SOURCE: SOURCE_DIRECT,
            CONF_SCAN_INTERVAL: int(
                user_input.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
            ),
        },
        {
            CONF_FORWARD: bool(user_input.get(CONF_FORWARD, True)),
            CONF_RESOLVER: user_input.get(CONF_RESOLVER, "").strip(),
        },
    )


def _bridge_title(data: Mapping[str, Any]) -> str:
    """Return the name the entry shows with in Settings."""
    return f"Ondilo ICO bridge ({data[CONF_HOST]}:{data[CONF_PORT]})"


def _direct_title(options: Mapping[str, Any]) -> str:
    """Return the name the entry shows with, which tells forwarding apart."""
    if options[CONF_FORWARD]:
        return "Ondilo ICO (direct, forwarded to Ondilo)"
    return "Ondilo ICO (direct, local only)"


async def _async_check_bridge(
    hass: HomeAssistant, data: Mapping[str, Any]
) -> str | None:
    """Return the error the user should see, or None when the bridge answers well."""
    bridge = OndiloIcoBridge(
        async_get_clientsession(hass),
        data[CONF_HOST],
        data[CONF_PORT],
        data[CONF_TOKEN] or None,
    )
    try:
        await bridge.async_health()
    except OndiloIcoAuthError:
        return "invalid_auth"
    except (OndiloIcoNotFoundError, OndiloIcoVersionError):
        return "bad_version"
    except OndiloIcoError:
        return "cannot_connect"
    return None


class OndiloIcoOptionsFlow(OptionsFlowWithReload):
    """Change how the ICO reports reach Home Assistant, and how often they are read."""

    @override
    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options of whichever mode this entry uses."""
        direct = _is_direct_entry_data(self.config_entry.data)
        schema = DIRECT_OPTIONS_SCHEMA if direct else OPTIONS_SCHEMA
        suggested: Mapping[str, Any] = {
            CONF_SCAN_INTERVAL: scan_interval(self.config_entry),
            CONF_STALE_AFTER: stale_after_minutes(self.config_entry),
        }
        if direct:
            entry = self.config_entry
            suggested[CONF_FORWARD] = bool(
                entry.options.get(CONF_FORWARD, entry.data.get(CONF_FORWARD, True))
            )
            suggested[CONF_RESOLVER] = str(
                entry.options.get(CONF_RESOLVER, entry.data.get(CONF_RESOLVER, ""))
            )

        if user_input is not None:
            data: dict[str, Any] = {
                CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL]),
                CONF_STALE_AFTER: int(user_input[CONF_STALE_AFTER]),
            }
            if direct:
                resolver = user_input.get(CONF_RESOLVER, "").strip()
                if resolver and not _is_ipv4(resolver):
                    return self.async_show_form(
                        step_id="init",
                        data_schema=self.add_suggested_values_to_schema(
                            schema, {**suggested, **user_input}
                        ),
                        errors={CONF_RESOLVER: "bad_resolver"},
                    )
                data[CONF_FORWARD] = bool(user_input[CONF_FORWARD])
                data[CONF_RESOLVER] = resolver
            return self.async_create_entry(data=data)

        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(schema, suggested),
        )


class OndiloIcoConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Ondilo ICO."""

    VERSION = 1

    @staticmethod
    @callback
    @override
    def async_get_options_flow(config_entry: ConfigEntry) -> OndiloIcoOptionsFlow:
        """Get the options flow for this handler."""
        return OndiloIcoOptionsFlow()

    @override
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask how the ICO reports reach Home Assistant."""
        return self.async_show_form(step_id="source", data_schema=SOURCE_SCHEMA)

    async def async_step_source(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Send the user to the form that matches the source they chose."""
        if user_input is not None and user_input[CONF_SOURCE] == SOURCE_DIRECT:
            return await self._async_step_direct("direct", None)
        return await self._async_step_bridge("user", None)

    async def async_step_direct(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the direct mode, where the probe talks to Home Assistant."""
        return await self._async_step_direct("direct", user_input)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle a change of address, port, token, forwarding or interval."""
        if _is_direct_entry_data(self._get_reconfigure_entry().data):
            return await self._async_step_direct("reconfigure_direct", user_input)
        return await self._async_step_bridge("reconfigure", user_input)

    async def _async_step_direct(
        self, step_id: str, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        """Ask what to do with the reports this instance receives itself."""
        errors: dict[str, str] = {}
        suggested: Mapping[str, Any] = DIRECT_DEFAULT_INPUT

        if user_input is not None:
            data, options = _direct_input(user_input)
            resolver = options[CONF_RESOLVER]
            if resolver and not _is_ipv4(resolver):
                # A hostname would have to be resolved by the very DNS we are
                # trying to step around, so this field only takes an address.
                errors[CONF_RESOLVER] = "bad_resolver"
                suggested = {**DIRECT_DEFAULT_INPUT, **user_input}
            elif self.source == SOURCE_RECONFIGURE:
                return self.async_update_reload_and_abort(
                    self._get_reconfigure_entry(),
                    options_updates=options,
                    title=_direct_title(options),
                )
            else:
                if any(
                    _is_direct_entry_data(entry.data)
                    for entry in self._async_current_entries()
                ):
                    return self.async_abort(reason="direct_already_configured")
                await self.async_set_unique_id(DIRECT_UNIQUE_ID)
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title=_direct_title(options),
                    data=data,
                    options=options,
                )
        elif self.source == SOURCE_RECONFIGURE:
            entry = self._get_reconfigure_entry()
            suggested = {
                CONF_FORWARD: bool(
                    entry.options.get(
                        CONF_FORWARD, entry.data.get(CONF_FORWARD, True)
                    )
                ),
                CONF_RESOLVER: str(
                    entry.options.get(CONF_RESOLVER, entry.data.get(CONF_RESOLVER, ""))
                ),
            }

        schema = (
            DIRECT_RECONFIGURE_SCHEMA
            if step_id == "reconfigure_direct"
            else DIRECT_SCHEMA
        )
        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(schema, suggested),
            errors=errors,
        )

    async def _async_step_bridge(
        self, step_id: str, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        """Ask for the bridge, check it answers, then create or update the entry."""
        errors: dict[str, str] = {}
        suggested: Mapping[str, Any] = DEFAULT_INPUT

        if user_input is not None:
            data = _bridge_input(user_input)
            unique_id = f"{data[CONF_HOST]}:{data[CONF_PORT]}"
            await self.async_set_unique_id(unique_id)
            if self.source == SOURCE_RECONFIGURE:
                # The address is the identity of a bridge, so it may move with the
                # entry; only another entry already watching it is a problem.
                self._async_abort_entries_match(
                    {CONF_HOST: data[CONF_HOST], CONF_PORT: data[CONF_PORT]}
                )
            else:
                self._abort_if_unique_id_configured()

            if (reason := await _async_check_bridge(self.hass, data)) is None:
                if self.source == SOURCE_RECONFIGURE:
                    return self.async_update_reload_and_abort(
                        self._get_reconfigure_entry(),
                        data=data,
                        title=_bridge_title(data),
                        unique_id=unique_id,
                    )
                return self.async_create_entry(
                    title=_bridge_title(data),
                    data={**data, CONF_SOURCE: SOURCE_BRIDGE},
                )
            errors["base"] = reason
        elif self.source == SOURCE_RECONFIGURE:
            suggested = self._get_reconfigure_entry().data

        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(BRIDGE_SCHEMA, suggested),
            errors=errors,
        )
