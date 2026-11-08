# Ondilo ICO for Home Assistant — local, no cloud

**Unofficial** Home Assistant integration for **Ondilo ICO** pool probes (V1: the model with a
physical on/off button). It shows temperature, pH, ORP, conductivity, battery and a few
diagnostic sensors, **without depending on the Ondilo cloud at read time**.

It reads the probe through one of **two shapes**, chosen in the config flow and switchable later:

| Shape | Who answers the probe | What you run |
| --- | --- | --- |
| **Direct** (no extra machine) | Home Assistant itself, on its own web server | a plain port forward `:80 → :8123` on the Home Assistant host |
| **Bridge** | a separate program that also exposes a read-only API on `8099` | that machine, kept running |

Both are `local_polling` on the Home Assistant side: when the source stops answering, entities go
`unavailable` — no fake data. The integration never talks to the Ondilo cloud to *read* anything.

**How the measurements get here at all.** The ICO V1 only speaks cleartext HTTP on port 80 to
`app.ondilo.com`, and its firmware updates only travel over Bluetooth. The probe cannot be told about
another name or port — the port is a literal `80` compiled into the firmware — so whoever wants the
data takes over the **name**: a DNS rewrite on the network points `app.ondilo.com` at that machine.
In proxy/forwarding mode the received frame is recorded **and re-sent to the real Ondilo server**, so
the mobile app and the Ondilo history keep working. On this hardware that is the only available path
without opening the probe: no BLE, no SWD, no UART.

**This integration needs control of that DNS name.** If you cannot edit the DNS answers of the
network your probe lives on, stop here: nothing else can feed it.

---

## Relationship with the official Ondilo integration

Home Assistant Core ships an official Ondilo integration, under the domain `ondilo_ico` (verified in
2026.11.0). This one is `ondilo_ico_local`, displayed as **Ondilo ICO (local)**, and both can be installed
at the same time:

|  | official `ondilo_ico` | this one, `ondilo_ico_local` |
| --- | --- | --- |
| where a reading comes from | the Ondilo cloud, over OAuth | the probe itself, delivered to your machine |
| when Ondilo is unreachable | nothing updates | readings keep arriving |
| probes | one configuration entry | several, one entry each |
| what it needs from the network | nothing | the DNS rewrite below |

They are two Home Assistant devices with two sets of entities: the official one names its device after the
pool in the Ondilo app, this one after the probe serial with a `(local)` marker. That suffix is the whole
point of the name — Home Assistant prefers `custom_components` over bundled integrations, so an integration
published here as `ondilo_ico` would have switched the official one off without ever mentioning it.

Keeping forwarding on (the default) also keeps the official integration current: every report is resent to
the real Ondilo server byte for byte, with its original `Host` header, so the cloud still sees the same
readings the probe gave you.

## Requirements

| Condition | Why |
| --- | --- |
| You can add a DNS rewrite on the LAN resolver (see next section) | without it the probe never talks to you |
| **Direct**: a forward from host port `80` to Home Assistant's `8123` (add-on, Nginx Proxy Manager, or your own `socat`) | the probe cannot be told about `:8123` |
| **Bridge**: that program runs on a machine reachable from Home Assistant (port `8099`) | it is the data source |
| That machine keeps a fixed address (DHCP reservation) | the rewrite points at that address |
| Firewall: inbound **TCP 80** on the machine the probe is sent to, and **TCP 8099** on a bridge | Windows treats the LAN as Public |
| Home Assistant **2026.9** or newer | the core validates config flows with `probatio` from that release (2026.9.0 pins `probatio==0.11.4` and drops `voluptuous`); a manifest cannot state a minimum version, so it is written here |

In direct mode nothing else is installed: `custom_components/ondilo_ico_local` registers the probe's routes
the probe calls. In bridge mode the bridge itself is a separate program; this repository ships only
`custom_components/ondilo_ico_local`, which is what the Marketplace (and HACS on older versions) installs.

## DNS setup (the part you have to configure)

### Why a rewrite and not a port or a host file

The probe has no local API, no TLS, and no setting screen: it looks up `app.ondilo.com`, then sends
`GET`/`POST` requests in cleartext HTTP to port 80. It takes its resolver from DHCP — there is no
per-device DNS field in the app. So the rewrite has to live in **whatever resolver DHCP hands to
the LAN**; a host file on a PC changes nothing for the probe.

One line does the job in dnsmasq:

```
address=/app.ondilo.com/192.168.1.14      # <- direct mode: the Home Assistant machine
address=/app.ondilo.com/192.168.1.130     # <- bridge mode: the bridge machine
```

Keep one of the two, the one that matches the shape you picked.

Rewrite the four host names the firmware can use, so a firmware that switches environment still
lands on your machine (`<TARGET>` is the address you chose above):

```
address=/app.ondilo.com/<TARGET>
address=/staging.ondilo.com/<TARGET>
address=/dev.ondilo.com/<TARGET>
address=/demo.ondilo.com/<TARGET>
```

Do **not** rewrite `interop.ondilo.com`: that is the client API used by apps and third-party
bridges, not the probe's upload path.

### Where to put that line, depending on your setup

The examples below use `192.168.1.130` for the bridge machine; substitute your own addresses
throughout.

| Your resolver | What to do |
| --- | --- |
| dnsmasq add-on / container under Home Assistant | add the four `address=` lines in its extra config (`dnsmasq.conf` / “Extra Settings”), restart the add-on |
| dnsmasq installed on the system (Raspberry Pi) | `echo 'address=/app.ondilo.com/192.168.1.130' \| sudo tee /etc/dnsmasq.d/ico-local.conf` then `sudo systemctl restart dnsmasq` |
| dnsmasq reading `/etc/hosts` (default build) | `echo '192.168.1.130  app.ondilo.com' \| sudo tee -a /etc/hosts` then restart dnsmasq |
| AdGuard Home | Filters → DNS rewrites: domain `app.ondilo.com`, answer `192.168.1.130` |
| Pi-hole | Local DNS → DNS Records: `app.ondilo.com` → `192.168.1.130` |
| ISP box doing DNS only | most consumer boxes cannot do a local override — put dnsmasq (or AdGuard Home) on a machine of your own, then hand **that** machine out as resolver over DHCP |

Worked example from the network this integration was developed on: DHCP and DNS are served by the
Home Assistant box at `192.168.1.14`, the bridge runs on a Windows PC at `192.168.1.130` (reserved
lease), the router is `192.168.1.254`.

### Reaching port 8123 from port 80 (direct mode only)

Home Assistant listens on `8123`. The probe only ever connects to `80`, and on Home Assistant OS you
cannot move the HTTP port of Home Assistant itself. So one thing has to forward `80 → 8123` on the
Home Assistant host, and nothing else: the integration registers the probe's routes on Home
Assistant's own web server, so the forward carries plain HTTP, path for path.

| Option | What it is |
| --- | --- |
| **Ondilo ICO port forward** app | a companion repository whose whole app folder is six files ([`ondilo-ico-addons`](https://github.com/H3buss/ondilo-ico-addons)) whose whole job is `socat` from `:80` to `homeassistant:8123`, in the host's network namespace so Home Assistant still sees the probe's real IP. Add it under **Settings → Apps → Install app → ⋮ → Repositories** (older releases: **Settings → Add-ons → Add-on store → ⋮ → Repositories**). Home Assistant's protection mode can stay on: `host_network` is not one of the capabilities it hides behind that switch |
| **Nginx Proxy Manager** add-on | nothing to build: add a proxy host listening on `80` forwarding to `homeassistant:8123`, keep the path untouched. It does log request lines, which is your measurements' URLs in the add-on log |
| Your own container or systemd unit | the same two-line redirect on the host, if you already run something there |

Whichever you pick, the check is the same, and it is the check that tells you the whole chain works
apart from the probe:

```bash
curl -i -X POST http://<home-assistant-ip>/proxy/prod/wifi/message -H 'Content-Type: application/json' -d '{}'
curl -s http://<home-assistant-ip>/api/v2/devices/ping/<uuid>
curl -s http://<home-assistant-ip>/proxy/prod/wifi/configuration/0
```

`404 {"success":false}` is **good**: it means port 80 reached Home Assistant and the ICO route
answered, refusing a body that is not a report. `{"value": 0}` from the second one is the keep-alive
route answering — `<uuid>` there is the 24-character id of your probe, but any id gets the same
answer. The third answers with the settings the probe would apply, all at their default; that path
carries the `/proxy/prod/wifi` prefix because the firmware builds it from a template that adds the
prefix, unlike the keep-alive. A connection refused, or Home Assistant's own login page, means the
forward is not in place.

### The trap worth knowing about: the receiver must still see the real server

Once the rewrite is live, **every** machine on the LAN — Home Assistant included — gets your own IP
when it asks for `app.ondilo.com`. Whoever forwards reports to the cloud therefore cannot simply
resolve the name: it would send your measurements to itself and call that a success.

In direct mode the integration asks the **Trusted DNS** you give it in the config flow — normally
your router, e.g. `192.168.1.254` — over UDP port 53, and caches the answer for ten minutes. Leave
the field empty when your resolver is *not* sinkholed for Home Assistant (the rewrite lives on
another machine). Whatever the source, an answer that turns out to be a private, loopback or
link-local address is **refused with a warning in the log** and nothing is forwarded: the report
still lands in Home Assistant, it simply does not pretend to have reached Ondilo.

A bridge avoids the same trap with `-UpstreamDns 192.168.1.254` (or `ICO_DNS_UPSTREAM`), and logs:

```
[upstream] app.ondilo.com = 54.194.148.222 (resolved via 192.168.1.254, which is not sinkholed)
```

If your router is itself behind the rewrite, pin the address instead (`-UpstreamIp 54.194.148.222`,
or `ICO_UPSTREAM_IP`). Refusing to guess is deliberate in both shapes: a wrong forward is invisible,
a loud refusal is not.

### Verify the rewrite

```powershell
Resolve-DnsName app.ondilo.com -Server 192.168.1.14 -DnsOnly   # must return your chosen target
```

Then wake the probe (its on/off button, or wait for the hourly report).

- **Direct mode**: the *Last report* entity of the probe fills in, and *Collection mode* reads
  `direct + cloud` (or `direct` when forwarding is off). Nothing to poll: a report wakes the
  integration up instead of waiting for the next cycle.
- **Bridge mode**: the `capture_lines` counter of `http://<bridge>:8099/health` increases, and
  `http://<bridge>:8099/api/devices` lists your probe with a fresh `age_s`.

Two things this does **not** break: the Ondilo cloud keeps receiving everything (proxy mode), so
the mobile app and its history stay correct; and the rewrite is trivially reversible — delete the
lines, restart the resolver, the probe is talking to Ondilo again within its next report.


## Install from Home Assistant 2026.11 and newer (Marketplace)

Since Home Assistant **2026.11**, HACS is built into Home Assistant as the **Marketplace**: there is
nothing to install, and the standalone HACS integration is *removed* — Home Assistant blocks it. This
repository is a regular GitHub repository, so it is added as a **custom repository**.

Requirements for a custom repository, all met here: a **public** GitHub repository, a `hacs.json`
file at the root, and the folder structure for its content type (`custom_components/ondilo_ico_local/`).

1. **Settings → Marketplace** (administrators only). Accept the community-content warning the
   first time you open it.
2. Upper-right corner → **Add from link**.
3. Under **GitHub link**, enter `H3buss/ondilo-ico-ha` (or the full URL).
4. Home Assistant asks you to **connect GitHub** — device sign-in: it shows a code and a link, you
   enter the code on GitHub and approve. The connection is read-only for public information and is
   only used to fetch custom repositories and check them for updates (every 48 h).
5. Select **Add**. If the type is not detected automatically, pick **Integration** under **Type**
   and add again.
6. Open the repository in the list → **Install**.

Because the integration has a config flow, the **setup page opens right away** when the install
finishes; a restart is only requested through a repair if it turns out to be needed. It first asks
**where the reports come from**: pick *The probe talks to Home Assistant itself* for the direct mode,
or *A separate bridge program on another machine* and fill in the address, for example
`192.168.1.130` port `8099`, leaving the token empty if the bridge does not use one.

Updates arrive as an **update entity** under Settings → Updates. After updating an integration,
restart Home Assistant (a repair reminds you and can do it for you). To pick another version, use
**⋮ → Install another version** on the repository page — useful when your Home Assistant is older
than the version this integration needs.

## Install on Home Assistant 2026.10 or older (HACS)

1. HACS → *Integrations* → **⋮** menu → **Custom repositories**.
2. Repository: `H3buss/ondilo-ico-ha` — category: **Integration** → **Add**.
3. The “Ondilo ICO” card appears → **Download**.
4. **Restart Home Assistant** (older versions do not load freshly copied integration code while running).
5. Settings → **Devices & Services** → **Add Integration** → *Ondilo ICO*.

When you later update Home Assistant to 2026.11, HACS is migrated automatically: this repository
carries over as a custom repository, and HACS itself is removed.

## Install by copying files

Copy the `custom_components/ondilo_ico_local` folder into `<config>/custom_components/`, then restart
Home Assistant. On Home Assistant OS the simplest route is the **Samba share** or **Advanced SSH &
Web Terminal** add-on: without one of those there is no file access to `/config` at all (ports 22,
2222 and 445 stay closed). The Marketplace path above needs none of that, which is why it is the
recommended one.


## What you get

One **Ondilo ICO** device per probe, plus a service device for the source: **Ondilo ICO receiver** in
direct mode, **Ondilo ICO bridge** in bridge mode.

Visible right after adding the integration (12):

| Entity | Unit | Notes |
| --- | --- | --- |
| Temperature | °C | |
| pH | — | no unit: the probe reports a raw value |
| ORP | mV | |
| Conductivity | µS/cm | |
| Battery | % | |
| Battery voltage | mV | |
| Battery temperature | °C | internal probe sensor, not the water |
| Wi-Fi RSSI | — | **unit unverified**: value exactly as the ICO reports it |
| Last report | timestamp | when your machine received the measurement, not the ICO's clock |
| Collection mode | text | `direct`, `direct + cloud`, `proxy` or `local` (diagnostic) |
| Retries | — | the ICO's own counter (diagnostic) |
| USB power | on/off | plugged / unplugged |

Disabled by default (7 — enable them if useful): *Measures in report*, *Measure number*,
*Device clock*, *Report clock*, *ICO UUID*, *Firmware build*, *Firmware*. The first four are
internal ICO counters: they are **not** wall-clock times.

**`unknown` is not a failure.** In winter mode the ICO reports `-1` (pH) and `65535` (ORP,
conductivity) meaning “not measured”. The integration turns those into `unknown`: a sensor showing
`-1` pH would be a misreading, not a measurement.

## Data freshness

The ICO reports once an hour (more often while it is working). The integration never fabricates a
value: *Last report* is the reference, and in bridge mode the bridge device also exposes the age of
the last reception. If your entities turn `unavailable`, first check the source: in bridge mode
`http://<bridge-ip>:8099/api/devices`; in direct mode the `curl` check of the port-80 section above.

## Troubleshooting

| Symptom | Likely cause |
| --- | --- |
| The integration is not in the add list | Home Assistant not restarted after a manual copy (the Marketplace opens the setup page by itself), or folder in the wrong place (it must be `custom_components/ondilo_ico_local/manifest.json`) — check **Settings → System → Repairs** for a restart request |
| “Add from link” refuses the repository | the repository is private, has no `hacs.json`, or GitHub was not connected (**Settings → Marketplace**, device sign-in) |
| “Bridge unreachable” while adding | wrong address/port, firewall, or bridge stopped |
| Entities stay empty in direct mode | the `80 → 8123` forward is missing or wrong: run the `curl` check above, `404 {"success":false}` is the answer you want |
| Log says an address “is a local address” | Ondilo resolves to your own network: put your router's IP in **Trusted DNS**, or stop sinkholing Home Assistant's resolver |
| `bad_resolver` while adding | that field takes an IPv4 address (a hostname would need the very DNS you are stepping around); leave it empty to use Home Assistant's own resolver |
| All entities `unavailable` | the source is hearing nothing: check the DNS rewrite, the forward, and that the ICO reported since |
| pH / ORP / conductivity `unknown` while temperature works | winter mode: the probe is not measuring, “not measured” is translated faithfully |
| *Last report* stuck | the ICO is asleep or off Wi-Fi; its on/off button wakes it |
| Two “Ondilo ICO” devices | the bridge was reached under two different addresses: delete the entry and re-add with one address |

## Deliberate limitations

- Read only, on purpose: nothing written in Home Assistant ever goes back to the ICO. The forwarding
  option re-sends what the probe already sent; it never sends anything the probe did not send.
- Direct mode answers the probe on **Home Assistant's own HTTP port**, behind the `80 → 8123`
  forward. Those routes cannot carry a token — the probe would not send one — so they accept
  any body that parses as an ICO report and nothing more. **Do not expose Home Assistant's HTTP
  interface to the internet** for this: the forward is a LAN thing.
- Home Assistant gives an integration no way to unmount a view, so in direct mode the routes stay
  mounted while it runs; they answer a plain `404` while no entry is loaded, and the option changes
  take effect on reload without touching the router.
- No ICO firmware update through this path (the ICO's OTA is Bluetooth only), and no change of the
  port the probe dials: `80` is compiled into the firmware (`AT+CIPSTART="TCP","%s",80`), which is
  why the forward exists in the first place.
- Some units are not documented by Ondilo; they are displayed as-is rather than guessed at.

## Licence and development

GPLv3 — see [LICENSE](LICENSE). Not affiliated with Ondilo.

The full protocol analysis (real frames, firmware, service replies) is documented separately; this
repository contains only the integration and the contracts it speaks.

Bridge mode reads that API:

```
GET /health                 → { ok, mode, version, bridge_id, devices, … }
GET /api/devices            → { devices: [ { uuid, ip, firmware, last_seen, age_s, … } ] }
GET /api/devices/<uuid>     → { … , state: { temperature, ph, orp, … } }
```

Direct mode instead **answers** what the probe asks, on Home Assistant's web server:

```
POST /proxy/prod/wifi/message                  → { "success": true, "data": { "message": "ok", "insertedMeasures": 1 } }
POST /api/v2/devices/ping/<uuid>               → { "value": 0 }
POST /proxy/prod/wifi/ping                     → { "success": true }
GET  /proxy/prod/wifi/configuration/<version>   → configuration body echoed at that version
GET  /configuration/<version>                  → the same answer, as a prefix-less alias
```

The report and the configuration fetch are built by the firmware from templates that carry the
`/proxy/prod/wifi` prefix; the keep-alive is built from a template that does not, which is why
`/api/v2/devices/ping/<uuid>` is served too. Anything else keeps Home Assistant's own `404`, and a
body that is not an ICO report gets `404 {"success": false}` without being stored.

A key missing from `state` means “not measured”. The entities hidden by default correspond to the
report's internal counters.
