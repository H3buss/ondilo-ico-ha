# Ondilo ICO for Home Assistant

Custom integration for the Ondilo ICO pool probe (V1), with **two ways to get the data**, picked in
the config flow:

- **Direct** — Home Assistant answers the probe itself, on the routes the Ondilo cloud owns
  (`POST /proxy/prod/wifi/message`, `POST /api/v2/devices/ping/<uuid>`, `POST /proxy/prod/wifi/ping`,
  `GET /proxy/prod/wifi/configuration/<v>`, plus the same configuration path without its prefix),
  after a DNS rewrite and a `:80 → :8123` forward. Nothing else to run.
- **Bridge** — Home Assistant reads the local bridge API only: `GET /health`, `GET /api/devices` and
  `GET /api/devices/<uuid>`.

No MQTT, no cloud read path.

**Target version: Home Assistant Core 2026.11.0** (released 4 November 2026, requires Python
3.14.2); **minimum 2026.9**, the release that replaced `voluptuous` with `probatio` in the core:
2026.8.0 still pins three `voluptuous` packages and no `probatio`, 2026.9.0 pins
`probatio==0.11.4` and none, 2026.11.0 pins `probatio==0.13.0`. The config flow is written with
the `probatio` schema API, so an older core cannot load this integration.

The manifest cannot carry that floor. The 2026.11 core reads it into the `Manifest` TypedDict of
`homeassistant/loader.py`, which has 30 fields (`name`, `disabled`, `domain`,
`integration_type`, `dependencies`, `after_dependencies`, `requirements`, `config_flow`,
`documentation`, `issue_tracker`, `quality_scale`, `iot_class`, `categories`, `connectivity`,
`bluetooth`, `mqtt`, `lorawan`, `ssdp`, `zeroconf`, `dhcp`, `usb`, `homekit`, `is_built_in`,
`overwrites_built_in`, `version`, `codeowners`, `loggers`, `import_executor`,
`single_config_entry`, `preview_features`) and neither a `homeassistant` nor an `after_version`
key: a floor declared there would be dropped without a warning. It is documented here instead.
Every core symbol this integration touches was read back from the 2026.11.0 release: 66 imports
across 15 modules, plus the enum members and selector fields the config flow and the entities
pass around.

## Requirements

Both shapes need the DNS rewrite that sends `app.ondilo.com` to the machine named in it (see the
repository README for the exact dnsmasq / AdGuard / Pi-hole lines and for the trap where the receiver
resolves the name to itself). Without it the probe never arrives and every entity stays empty.

**Direct mode**, in addition:

- Home Assistant must be reachable on **host port 80** — a forward to `8123` (add-on, Nginx Proxy
  Manager, or your own redirect). Home Assistant's own HTTP port cannot be moved.
- Optionally, the IP of a DNS server that does **not** know about the rewrite (usually your router),
  so a forwarded report reaches Ondilo instead of looping back here.

**Bridge mode**, in addition:

- The bridge runs and answers, e.g. `http://192.168.1.20:8099/health` returns
  `{"ok": true, "mode": …, "version": …}`.
- Bridge listening port: **8099** by default.
- If the bridge was started with `--api-token` (or `ICO_API_TOKEN`), have the token.
- Home Assistant must be able to reach that port on the LAN.

## Installation

On Home Assistant **2026.11 and newer**, use the built-in **Marketplace** (HACS is integrated there
and the standalone HACS integration is removed): **Settings → Marketplace → Add from link** →
`H3buss/ondilo-ico-ha`, connect GitHub when asked, add it as an **Integration**, then **Install**.
The setup page opens as soon as the install finishes because the integration has a config flow.

On Home Assistant **2026.10 or older**, add the repository through HACS
(*Custom repositories* → category *Integration*), download it, then **restart Home Assistant** —
older versions do not load freshly copied integration code while running.

To install by hand instead:

1. Copy the `custom_components/ondilo_ico` folder (with its whole content, including
   `translations/`) into the Home Assistant configuration folder, next to `configuration.yaml`:

   ```
   config/custom_components/ondilo_ico/
   ```

2. Restart Home Assistant (not just "reload integrations": freshly copied integration code is
   only loaded at startup).
3. **Settings → Devices & Services → Add Integration**, search for **“Ondilo ICO”**.
4. Pick **where the reports come from**:
   - *The probe talks to Home Assistant itself* — direct mode. Then:
     - **Forward to Ondilo**: on by default, re-sends each report byte for byte to the real server
       with its original `Host` header, so the Ondilo app and its history keep working;
     - **Trusted DNS**: IP of a resolver that does not know about the rewrite (e.g. your router);
       empty uses Home Assistant's own resolver, and a private answer is refused rather than looped;
     - **Refresh interval**: 60 s by default (a report wakes the integration up on its own, this is
       only the backstop).
   - *A separate bridge program on another machine* — bridge mode. Then:
     - **Host**: IP or hostname of the bridge machine, without `http://`;
     - **Port**: 8099 by default;
     - **API token**: optional, leave empty if the bridge does not ask for one;
     - **Refresh interval**: 60 s by default.

In bridge mode the address is verified through `/health` before the entry is created. One entry = one
source: the same bridge cannot be added twice, and only one entry can own the direct-mode routes of a
Home Assistant instance (they are mounted once, on the shared web server).

## How it works

**Bridge mode.** Every cycle the integration calls `/health`, then `/api/devices`, then
`/api/devices/<uuid>` for each known probe. Any error raises `UpdateFailed`: entities go
`unavailable` and the message "Could not get data from the Ondilo ICO bridge: …" appears.

**Direct mode.** The probe is the one knocking. Each accepted report updates the store and wakes the
coordinator up, so the dashboard is current without waiting for the next cycle; the store holds the
last report per probe uuid, in the same state shape the bridge API uses — which is why every entity,
unit and sentinel behaves identically in both modes. A body that is not an ICO report is answered
`404 {"success": false}` and never logged.

In both modes, probes seen **after** the integration was added (the ICO only reports once an hour, so
there may be nothing at install time) are discovered automatically, without reloading the entry.

Two Home Assistant devices per installation: a **service** device — “Ondilo ICO receiver” in direct
mode, “Ondilo ICO bridge” in bridge mode (with configuration URL and version when there is a bridge
to read them from) — and one **“Ondilo ICO <uuid>”** device per probe (`manufacturer: Ondilo`,
`model: ICO V1`, `sw_version` = the `firmware` state key, attached to the service device).

## Data freshness and availability

The probe sleeps between reports (roughly one hour in normal operation). That is not a fault:
entities stay **available** and keep showing the last known value. The **Last report** entity
(key `measure_time`, the arrival time of the report on this machine) is the honesty signal —
automate on it if you want to know how long nothing has been heard. Going `unavailable` is
reserved for what we actually know is broken: bridge unreachable, token refused, malformed reply.

## Winter mode: missing pH, ORP and conductivity is normal

In winter mode the probe only reports temperature, battery and RSSI. The state then omits the `ph`,
`orp` and `conductivity` keys (the same happens for other values when they equal `-1` or `65535`,
i.e. "not measured"). A missing key yields an entity state of
**`unknown`** — never `0`, never an invented value. Do not look for a bug: the entities exist,
they are simply empty until the probe measures again.

## Entities created per probe

| State key | Entity | Unit / class | Default |
| --- | --- | --- | --- |
| `temperature` | Temperature | °C, `temperature`, measurement | enabled |
| `ph` | pH | no class, no unit, 2 decimals | enabled |
| `orp` | ORP | mV | enabled |
| `conductivity` | Conductivity | µS/cm | enabled |
| `battery` | Battery | %, `battery` | enabled |
| `battery_voltage` | Battery voltage | mV, `voltage` | enabled |
| `battery_temperature` | Battery temperature | °C, `temperature` | enabled |
| `wifi_rssi` | Wi-Fi RSSI | **no unit, no class** | enabled (diagnostic) |
| `usb_power` | USB power | binary, `plug` class | enabled |
| `measure_time` | Last report | `timestamp` | enabled |
| `mode` | Collection mode | text | enabled (diagnostic) |
| `retry` | Retries | number | enabled (diagnostic) |
| `measure_count` | Measures in report | number | disabled (diagnostic) |
| `measure_seq` | Measure number | number | disabled (diagnostic) |
| `device_clock` | Device clock | number | disabled (diagnostic) |
| `report_clock` | Report clock | number | disabled (diagnostic) |
| `ico_uuid` | ICO UUID | text | disabled (diagnostic) |
| `fw_version` | Firmware build | text | disabled (diagnostic) |
| `firmware` | Firmware | text | disabled (diagnostic) |

Three choices deserve an explanation:

- **`wifi_rssi` is published raw, without a unit.** The captured value arrives positive
  (for example `59`) and nobody has verified that it is dBm. Rather than inventing a unit, the
  entity has neither `device_class` nor unit.
- **`device_clock` and `report_clock` stay numbers.** These are the probe's internal counters
  (`470953801` in the captured report), not epochs: reading them as dates would display 1985.
- **pH without `device_class`**: requested in the specification, even though
  `SensorDeviceClass.PH` exists and would allow unitless display. Change it in one line if you
  prefer the official class.

`usb_power` is a **binary sensor** of class `plug` (plugged = `on`): it is the cleanest rendering
for a yes/no, and it keeps state `unknown` when the key is absent from the report.

## Configuration errors

| Message | Cause |
| --- | --- |
| `cannot_connect` | Bridge off, wrong port, Home Assistant not routable to the host, timeout, non-JSON reply. |
| `invalid_auth` | The bridge answered 401/403: token missing or wrong. |
| `bad_version` | Something answers, but `ok` is not true, or `version` is empty, or `/health` does not exist (wrong service on that port). |
| `bad_resolver` | Direct mode: the Trusted DNS field holds something other than an IPv4 address. A hostname would have to be resolved by the very DNS you are stepping around — leave it empty to use Home Assistant's own resolver. |

The token is corrected through **Reconfigure** on the config entry (the address may change too:
entry identity follows `host:port`). There is no re-authentication flow: the token is static, a
refused value makes entities unavailable until you reconfigure. In direct mode there is nothing to
reach at setup time, so the flow creates the entry without a reachability test — the `curl` check in
the repository README is what tells you the forward is in place.

## Known limitations

- The bridge version shown on the “Ondilo ICO bridge” device comes from the `/health` read when
  the entry loads; after a bridge upgrade, reload the entry to refresh it.
- In direct mode the ICO routes live on **Home Assistant's own HTTP port** and carry no token,
  because the probe would not send one. They accept bodies that parse as ICO reports and nothing
  else — but keep Home Assistant's HTTP interface inside the LAN, behind the port 80 forward.
- Home Assistant gives an integration no way to unmount a view: in direct mode the routes stay
  mounted while it runs and answer a plain `404` while no entry is loaded.
- No actions, no buttons, no services: the integration is read-only, like the bridge.

## Uninstall

Delete the entry (devices and entities go with it), then delete the
`custom_components/ondilo_ico` folder and restart.

## Going further

The reverse-engineering work on the protocol (frames, fields, sentinel values) is documented in
the analysis workspace this integration was built in.
