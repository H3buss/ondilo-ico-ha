"""Answer the ICO probe protocol from inside Home Assistant.

The ICO V1 only ever speaks cleartext HTTP to ``app.ondilo.com`` on port 80, and it
never learns about a port number. Home Assistant listens on 8123, so on Home Assistant
OS something has to forward host port 80 to the Home Assistant web server. That
forward carries bytes and nothing else: every decision about the protocol lives here.

Four endpoints, in the shapes the firmware actually uses (``0x014c5f``, ``0x015bae``)
and observed on live traffic (see ``sinkhole/capturing/ico-capture.jsonl`` and
notes/PLAN-direct-ha.md):

``POST /proxy/prod/wifi/message``      a report, ``{"ico": {...}, "meas": [...]}``
``POST /api/v2/devices/ping/<uuid>``   the keep-alive the firmware dials
``POST /proxy/prod/wifi/ping``         the same keep-alive under the message prefix
``GET  /configuration/<version>``      the configuration the probe may fetch

When forwarding is enabled the report is replayed to the real Ondilo address
byte for byte and the cloud answer is handed back to the probe unchanged, which
keeps the mobile app working and makes the cutover invisible to the device. When it
is disabled, or when the cloud cannot be reached, the probe gets a local ``ok``: a
probe that thinks its report was refused keeps retrying and fills the log.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import socket
import struct
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, override

import aiohttp
from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN, LOGGER, RECEIVER_MODEL
from .coordinator import IcoDevice, IcoSource

PATH_MESSAGE = "/proxy/prod/wifi/message"
PATH_PING = "/proxy/prod/wifi/ping"
PATH_PING_DEVICE = "/api/v2/devices/ping/{uuid}"
# The report and the configuration fetch are built from templates that carry a prefix
# field (``AT+HTTPCLIENT=2,1,"http://%s%s/configuration/%s",,,1`` at ``0x015db5``, base
# ``/proxy/prod/wifi`` at ``0x14c5f``): this is the path the probe dials, and the one
# Ondilo's own server answers. The prefix-less path stays served as an alias because
# nothing in the firmware guarantees that prefix is always that string.
PATH_CONFIGURATION = "/proxy/prod/wifi/configuration/{version}"
PATH_CONFIGURATION_ALIAS = "/configuration/{version}"

UPSTREAM_HOST = "app.ondilo.com"
# Nothing is pinned here on purpose: a shipped address goes stale and would quietly
# send someone's measurements to whoever owns that address later. The resolver
# option, or a working DNS, is the supported way.
UPSTREAM_FALLBACK_IP = ""
RESOLVER_MEMO_SECONDS = 600


def _is_local(address: str) -> bool:
    """Return whether an address would send the report back into this network."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return True
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved

# The probe marks "not measured" with every bit set: -1 on signed fields, 65535 on
# unsigned ones. Seen for real on pH, ORP and both conductivity electrodes while the
# probes hung out of the water in winter mode, with temperature and battery still
# valid. Publishing those as numbers would plot a pH of -1 and an ORP of 65535 mV.
NOT_MEASURED = (-1, 0xFFFF)

FORWARD_TIMEOUT = 10.0


def measured(value: Any) -> bool:
    """Return whether one raw field is an actual measurement."""
    return value is not None and value not in NOT_MEASURED


def firmware_to_string(firmware: Any) -> str | None:
    """Render the packed firmware number the way Ondilo's own API does.

    The real device sent ``"30100"``; Ondilo calls that build 3.1.0.
    """
    try:
        number = int(str(firmware), 10)
    except (TypeError, ValueError):
        return None
    return f"{number // 10000}.{number // 100 % 100}.{number % 100}"


def build_state(
    last: Mapping[str, Any],
    count: int,
    ico: Mapping[str, Any],
    uuid: str,
    arrival: str,
    mode: str,
) -> dict[str, Any]:
    """Build the state mapping the entity descriptions expect.

    Field for field identical to what the standalone bridge published, so entities,
    their units and their "not measured" behaviour do not change at the cutover.
    """
    state: dict[str, Any] = {}

    if measured(last.get("temp")):
        state["temperature"] = last["temp"]
    if measured(last.get("ph")):
        state["ph"] = last["ph"]
    if measured(last.get("orp")):
        state["orp"] = last["orp"]
    if measured(last.get("cond+")) and measured(last.get("cond-")):
        state["conductivity"] = last["cond+"] - last["cond-"]
    if measured(last.get("bat_level")):
        state["battery"] = last["bat_level"]
    if measured(last.get("bat_volt")):
        state["battery_voltage"] = last["bat_volt"]
    if last.get("bat_temp") is not None:
        state["battery_temperature"] = last["bat_temp"]
    if measured(last.get("wifi_rssi")):
        # The unit of this field was never confirmed by Ondilo: the device sent 59
        # then 45 while walking closer to the access point. Plausible as |dBm|, not
        # proven, so it stays a plain number with no unit.
        state["wifi_rssi"] = last["wifi_rssi"]
    state["usb_power"] = "plugged" if last.get("usb_plug") else "unplugged"
    state["measure_count"] = count

    state["measure_seq"] = last.get("num")
    # The device's own time fields are counters, not epoch seconds: the real device
    # sent time=470953801 and sendtime=470953809, which decoded as 1985 dates. The
    # only honest timestamp is when the report arrived here.
    state["measure_time"] = arrival
    state["device_clock"] = last.get("time")
    state["report_clock"] = ico.get("sendtime")
    state["retry"] = last.get("retry")
    state["ico_uuid"] = uuid
    state["fw_version"] = ico.get("fw")
    firmware = firmware_to_string(ico.get("fw"))
    if firmware is not None:
        state["firmware"] = firmware
    state["mode"] = mode
    return state


def looks_like_report(payload: Any) -> bool:
    """Return whether a body carries a measurement report."""
    return (
        isinstance(payload, Mapping)
        and isinstance(payload.get("ico"), Mapping)
        and bool(payload["ico"].get("uuid"))
        and isinstance(payload.get("meas"), list)
    )


@dataclass(slots=True)
class ReceivedReport:
    """One accepted report, ready to be handed to the cloud."""

    uuid: str
    arrival: datetime
    body: bytes
    path: str
    measures: int


class IcoReceiver:
    """Keep the last report of every probe that talked to Home Assistant."""

    def __init__(self, hass: HomeAssistant, mode: str = "direct") -> None:
        """Initialize the receiver."""
        self._hass = hass
        self._mode = mode
        self._reports: dict[str, ReceivedReport] = {}
        self._states: dict[str, dict[str, Any]] = {}
        self._firmware: dict[str, str | None] = {}
        self._listeners: set[Callable[[], None]] = set()

    @property
    def known_uuids(self) -> set[str]:
        """Return the uuid of every probe that has reported since startup."""
        return set(self._states)

    def set_mode(self, mode: str) -> None:
        """Change what the `mode` state key says, on reload of an entry."""
        self._mode = mode

    def add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Register a callback run after each accepted report."""
        self._listeners.add(listener)
        return lambda: self._listeners.discard(listener)

    @callback
    def ingest(self, body: bytes, *, uuid: str | None = None) -> ReceivedReport | None:
        """Record one report and tell the coordinators about it."""
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            LOGGER.warning("ondilo_ico_local: report body is not JSON, ignored")
            return None
        if not looks_like_report(payload):
            return None

        ico = payload["ico"]
        measures = payload["meas"]
        probe_uuid = str(uuid or ico["uuid"])
        last = measures[-1] if measures else {}
        arrival = datetime.now(UTC)
        state = build_state(
            last, len(measures), ico, probe_uuid, arrival.isoformat(), self._mode
        )

        self._reports[probe_uuid] = ReceivedReport(
            uuid=probe_uuid,
            arrival=arrival,
            body=body,
            path=PATH_MESSAGE,
            measures=len(measures),
        )
        self._states[probe_uuid] = state
        self._firmware[probe_uuid] = firmware_to_string(ico.get("fw"))
        LOGGER.debug(
            "ondilo_ico_local: report from %s, %s measure(s), %s state key(s)",
            probe_uuid,
            len(measures),
            len(state),
        )
        for listener in list(self._listeners):
            listener()
        return self._reports[probe_uuid]

    def devices(self) -> dict[str, IcoDevice]:
        """Return the devices in the shape the entity layer already understands."""
        return {
            uuid: IcoDevice(
                uuid=uuid,
                firmware=self._firmware.get(uuid),
                state=state,
            )
            for uuid, state in self._states.items()
        }

    def last_report(self, uuid: str) -> ReceivedReport | None:
        """Return the raw report to replay upstream, if any."""
        return self._reports.get(uuid)


class CloudForwarder:
    """Re-send a report to the real Ondilo server, which is who the probe believes
    it is talking to.

    Under the DNS rewrite every resolver on the LAN answers ``app.ondilo.com`` with
    the address of the rewrite target, including Home Assistant's own resolver, so
    the address is asked from a resolver that is deliberately not rewritten.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        resolver: str,
        host: str = UPSTREAM_HOST,
        fallback_ip: str = UPSTREAM_FALLBACK_IP,
    ) -> None:
        """Initialize the forwarder."""
        self._session = session
        self._resolver = resolver
        self._host = host
        self._fallback_ip = fallback_ip
        self._memo: tuple[str, float] | None = None

    async def async_address(self) -> str | None:
        """Return the real upstream address, memoised for ten minutes.

        An address that turns out to be local is refused: under a DNS rewrite that
        is this very server, and POSTing the probe's report to ourselves would look
        like a successful forward while nothing reaches Ondilo.
        """
        now = asyncio.get_running_loop().time()
        if self._memo and now - self._memo[1] < RESOLVER_MEMO_SECONDS:
            return self._memo[0]

        if self._resolver:
            address = await _async_lookup_a(self._resolver, self._host)
        else:
            address = await self._async_system_address()
        if address is not None and _is_local(address):
            LOGGER.warning(
                "ondilo_ico_local: %s answers %s, which is a local address: the forward"
                " would loop here instead of reaching Ondilo. Set the resolver option"
                " to a DNS that does not rewrite %s",
                self._resolver or "the system resolver",
                address,
                self._host,
            )
            address = None
        if address is None:
            address = self._fallback_ip or None
        if address is not None and _is_local(address):
            LOGGER.warning(
                "ondilo_ico_local: the pinned address %s of %s is a local address, so"
                " nothing is forwarded",
                address,
                self._host,
            )
            return None
        if address:
            self._memo = (address, now)
        return address

    async def _async_system_address(self) -> str | None:
        """Ask the resolver Home Assistant itself uses."""
        try:
            infos = await asyncio.get_running_loop().getaddrinfo(
                self._host, None, type=socket.SOCK_STREAM
            )
        except OSError as err:
            LOGGER.warning("ondilo_ico_local: %s did not resolve (%s)", self._host, err)
            return None
        for info in infos:
            if info[0] == socket.AF_INET:
                return info[4][0]
        return None

    async def async_forward(
        self, path: str, method: str, body: bytes
    ) -> tuple[int, bytes] | None:
        """Replay one request upstream; None when the cloud is unreachable."""
        address = await self.async_address()
        if address is None:
            LOGGER.warning("ondilo_ico_local: no upstream address, serving locally")
            return None
        headers = {"Host": self._host, "Content-Type": "application/json"}
        try:
            async with self._session.request(
                method,
                f"http://{address}{path}",
                data=body,
                headers=headers,
                timeout=FORWARD_TIMEOUT,
            ) as response:
                payload = await response.read()
                return response.status, payload
        except Exception as err:  # noqa: BLE001 - any failure means "serve locally"
            LOGGER.warning("ondilo_ico_local: forward to %s failed (%s)", address, err)
            return None


def _encode_name(hostname: str) -> bytes:
    """Encode a hostname as a DNS QNAME."""
    out = bytearray()
    for label in hostname.split("."):
        out += bytes((len(label),)) + label.encode("ascii")
    return bytes(out) + b"\x00"


def _skip_name(data: bytes, offset: int) -> int:
    """Return the offset just past a DNS name, following one compression pointer."""
    while offset < len(data):
        length = data[offset]
        if length == 0:
            return offset + 1
        if length & 0xC0 == 0xC0:
            return offset + 2
        offset += 1 + length
    return offset


def _parse_answers(data: bytes) -> list[str]:
    """Pull the A records out of a DNS answer, in the order they arrive."""
    if len(data) < 12:
        return []
    # Header: id, flags, then QDCOUNT, ANCOUNT, NSCOUNT, ARCOUNT.
    _, _, questions, answers, _, _ = struct.unpack(">HHHHHH", data[:12])
    offset = 12
    for _ in range(questions):
        offset = _skip_name(data, offset) + 4
    found: list[str] = []
    for _ in range(answers):
        offset = _skip_name(data, offset)
        if offset + 10 > len(data):
            break
        rtype, _, _, rdlength = struct.unpack(">HHIH", data[offset : offset + 10])
        offset += 10
        rdata = data[offset : offset + rdlength]
        offset += rdlength
        if rtype == 1 and rdlength == 4:
            found.append(socket.inet_ntoa(rdata))
    return found


class _AnswerWaiter(asyncio.DatagramProtocol):
    """Resolve a future with the first A record of an answer."""

    def __init__(self, future: asyncio.Future, transaction: bytes) -> None:
        """Initialize the protocol."""
        self._future = future
        self._transaction = transaction

    @override
    def datagram_received(self, data: bytes, _info: Any) -> None:
        """Collect answers until one carries an A record."""
        if len(data) < 2 or data[:2] != self._transaction or self._future.done():
            return
        found = _parse_answers(data)
        if found:
            self._future.set_result(found[0])


async def _async_lookup_a(
    resolver: str, hostname: str, timeout: float = 3.0
) -> str | None:
    """Ask one resolver for an A record, without going through the system resolver.

    The system resolver is the rewritten one: under the sinkhole it hands back our
    own address, which would make the forwarder talk to itself.
    """
    transaction = struct.pack(">H", 0x1C0F)
    query = (
        transaction
        + struct.pack(">HHHHH", 0x0100, 1, 0, 0, 0)
        + _encode_name(hostname)
        + struct.pack(">HH", 1, 1)
    )
    loop = asyncio.get_running_loop()
    future: asyncio.Future = loop.create_future()
    try:
        transport, _ = await loop.create_datagram_endpoint(
            lambda: _AnswerWaiter(future, transaction), remote_addr=(resolver, 53)
        )
    except OSError as err:
        LOGGER.warning(
            "ondilo_ico_local: cannot ask %s about %s (%s)", resolver, hostname, err
        )
        return None
    try:
        transport.sendto(query)
        return await asyncio.wait_for(future, timeout)
    except (TimeoutError, OSError) as err:
        LOGGER.warning(
            "ondilo_ico_local: %s did not answer for %s (%s)", resolver, hostname, err
        )
        return None
    finally:
        transport.close()


class IcoEndpoint:
    """The three ICO routes, shared by every config entry.

    Home Assistant gives an integration no way to unmount a view, so the routes stay
    mounted for the whole run and this holder decides what they answer. Reloading or
    unloading an entry flips `enabled` and swaps the forwarder here instead of
    touching the router, which keeps a reload from leaving the probe unanswered.
    """

    def __init__(self, receiver: IcoReceiver) -> None:
        """Initialize the holder."""
        self.receiver = receiver
        self.forwarder: CloudForwarder | None = None
        self.replay: Mapping[str, Any] = {}
        self.enabled = False


class IcoMessageView(HomeAssistantView):
    """Take the report the probe sends instead of the Ondilo cloud."""

    url = PATH_MESSAGE
    name = "ondilo_ico_local:message"
    requires_auth = False
    auth_required = False

    def __init__(self, endpoint: IcoEndpoint) -> None:
        """Initialize the view."""
        self._endpoint = endpoint

    @override
    async def post(self, request: web.Request) -> web.Response:
        """Handle one report."""
        return await handle_report(request, self._endpoint)


class IcoPingView(HomeAssistantView):
    """Answer the keep-alive so the probe keeps its connection strategy."""

    url = PATH_PING
    name = "ondilo_ico_local:ping"
    requires_auth = False
    auth_required = False

    @override
    async def post(self, request: web.Request) -> web.Response:
        """Handle one keep-alive."""
        return web.json_response({"success": True})


class IcoDevicePingView(HomeAssistantView):
    """Answer the keep-alive the firmware actually dials.

    The report travels under ``/proxy/prod/wifi``, the ping does not: the firmware
    carries its own ``AT+HTTPCLIENT`` template for
    ``http://%s/api/v2/devices/ping/%s`` with a ``{"ping": n}`` body, and Ondilo's
    server answers that route with ``{"value": 0}``. A 404 here does not break
    anything the probe does, it just makes it log an error on every keep-alive.
    """

    url = PATH_PING_DEVICE
    name = "ondilo_ico_local:device_ping"
    requires_auth = False
    auth_required = False

    @override
    async def post(self, request: web.Request, uuid: str) -> web.Response:
        """Answer one keep-alive."""
        return web.json_response({"value": 0})

    @override
    async def get(self, request: web.Request, uuid: str) -> web.Response:
        """Answer one keep-alive asked as a read."""
        return web.json_response({"value": 0})


class IcoConfigurationView(HomeAssistantView):
    """Answer the configuration fetch with the version the probe asked about.

    Under ``/proxy/prod/wifi``, which is where the firmware's own template puts it —
    the same prefix as the report, unlike the keep-alive.
    """

    url = PATH_CONFIGURATION
    name = "ondilo_ico_local:configuration"
    requires_auth = False
    auth_required = False

    def __init__(self, endpoint: IcoEndpoint) -> None:
        """Initialize the view."""
        self._endpoint = endpoint

    @override
    async def get(self, request: web.Request, version: str) -> web.Response:
        """Handle one configuration fetch."""
        # The exact body the cloud sends here has not been captured yet: it takes a
        # change in the Ondilo app to make the probe fetch it. Until then the key
        # names found in the firmware are echoed back at the version asked about,
        # which is what kept the probe quiet while running as a bridge.
        if self._endpoint.replay:
            return web.json_response(self._endpoint.replay)
        return web.json_response(
            {
                "configurationValueVersion": int(version) if version.isdigit() else 0,
                "confValueVersion": int(version) if version.isdigit() else 0,
                "fast_orp_heat": 0,
                "fast_ph_heat": 0,
                "fast_delay_measure_sec": 3600,
                "fast_upload_delay_mes": 3600,
                "fast_upload_retry_delay_mes": 300,
            }
        )


class IcoConfigurationAliasView(IcoConfigurationView):
    """Same answer one level up, for a firmware that carries no prefix.

    Cheap insurance: one more route on a server that already answers four, no behaviour
    of its own, and it costs nothing when the probe never asks for it.
    """

    url = PATH_CONFIGURATION_ALIAS
    name = "ondilo_ico_local:configuration_alias"


async def handle_report(
    request: web.Request,
    endpoint: IcoEndpoint,
) -> web.Response:
    """Record a report, forward it when asked to, answer the probe."""
    if not endpoint.enabled:
        # No entry is watching right now, between an unload and the next setup.
        # Answer the plain 404 a server without these routes would give, rather than
        # a half-open endpoint that stores reports nobody reads.
        return web.json_response({"success": False}, status=404)

    body = await request.read()
    report = endpoint.receiver.ingest(body)
    if report is None:
        # Not a report we understand: answer like the probe expects an error, and keep
        # the body out of the log so a curious payload cannot spray the log file.
        return web.json_response({"success": False}, status=404)

    if endpoint.forwarder is not None:
        forwarded = await endpoint.forwarder.async_forward(
            PATH_MESSAGE, "POST", report.body
        )
        if forwarded is not None:
            status, payload = forwarded
            try:
                return web.Response(
                    status=status,
                    body=payload,
                    content_type="application/json",
                )
            except ValueError:
                return web.Response(status=status, body=payload)

    return web.json_response(
        {
            "success": True,
            "data": {"message": "ok", "insertedMeasures": report.measures},
        }
    )


class ReceiverSource(IcoSource):
    """Feed the entities from the probes that reached Home Assistant directly."""

    model = RECEIVER_MODEL
    translation_key = "receiver"

    def __init__(self, endpoint: IcoEndpoint) -> None:
        """Initialize the source."""
        self.endpoint = endpoint

    @override
    async def async_prepare(self) -> None:
        """Nothing to reach: the probes are the ones knocking."""

    @override
    async def async_devices(self) -> dict[str, IcoDevice]:
        """Return one device per probe seen since startup."""
        return self.endpoint.receiver.devices()


async def async_setup_endpoint(
    hass: HomeAssistant,
    *,
    mode: str,
    forward: bool,
    resolver: str,
    replay: Mapping[str, Any] | None = None,
) -> IcoEndpoint:
    """Mount the ICO routes once, then apply the settings of this entry.

    The routes are mounted on the Home Assistant web server, which means whatever
    can reach Home Assistant's HTTP port can POST to them: they answer only probes
    whose reports parse, they keep no token, and they should not be exposed beyond
    the local network.
    """
    if getattr(hass, "http", None) is None:
        raise ConfigEntryError(
            "the direct mode needs the Home Assistant HTTP integration to be enabled"
        )

    data = hass.data.setdefault(DOMAIN, {})
    endpoint: IcoEndpoint | None = data.get("endpoint")
    if endpoint is None:
        endpoint = IcoEndpoint(IcoReceiver(hass, mode=mode))
        data["endpoint"] = endpoint
    else:
        endpoint.receiver.set_mode(mode)

    endpoint.forwarder = (
        CloudForwarder(async_get_clientsession(hass), resolver) if forward else None
    )
    endpoint.replay = dict(replay or {})
    endpoint.enabled = True

    if not data.get("views"):
        for view in (
            IcoMessageView(endpoint),
            IcoPingView(),
            IcoDevicePingView(),
            IcoConfigurationView(endpoint),
            IcoConfigurationAliasView(endpoint),
        ):
            await hass.http.register_view(view)
        data["views"] = True
        LOGGER.info("ondilo_ico_local: probes are received on %s", PATH_MESSAGE)
    return endpoint


@callback
def async_teardown_endpoint(hass: HomeAssistant) -> None:
    """Stop answering probes: the routes stay mounted, they just refuse."""
    if (endpoint := hass.data.get(DOMAIN, {}).get("endpoint")) is not None:
        endpoint.enabled = False
        endpoint.forwarder = None
