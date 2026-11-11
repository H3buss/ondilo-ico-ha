# Changelog

All notable changes to this Home Assistant integration. Dates are the dates the bridge and the
integration were validated together against a real ICO V1.

## 1.3.2

The repository has an icon: the probe floating on its water, with the link going to a Home Assistant
hexagon instead of up into the sky. `images/logo.png` heads the README; `images/avatar-512.png` and
`images/social-preview.png` sit next to it for the repository settings, which GitHub only lets you fill
by hand. `tools/make-logo.py` draws all three from the same coordinates, and `docs-check.py` now checks
the `src` of an `<img>` like any other relative link - a banner pointing at nothing fails a gate rather
than showing a broken frame.

## 1.3.1

Home Assistant refused to load the calibration fields. `native_precision` is not a field of
`NumberEntityDescription`, and a description built with an invented keyword raises `TypeError` while the
platform is still being imported, which takes the whole `number` platform down with it. The display
precision comes from the step, which was already right - 0.01 mV per pH unit, 0.0001 ppm per count - so
the two keywords are simply gone.

The gate that should have caught it did not exist: nothing confronted the keywords of our description
constructors with the fields the core actually declares. `tools/desc-check.py` does now. It reads the
dataclasses in the core wheel - the `entity.py` of each platform, and the bases they inherit in that same
file - and reports any keyword no constructor accepts. Run against 1.3.0 it names exactly the two lines
Home Assistant named, and finds nothing else among the 28 descriptions.

## 1.3.0

The readings Ondilo computes in its cloud are computed here now, and calibrated by you.

`Estimated pH` and `Estimated TDS` appear on the probe. They are computed out of the raw electrode
potential and the raw cell counts, which means they need the two constants Ondilo keeps to itself: the
potential your electrode gives at pH 7, and what your cell counts are worth in ppm. Neither is in the
firmware - the 3.1.0 image names the pH field a millivolt measure, and names no unit at all for the two
conductivity electrodes - and neither is in a report, because an offset belongs to one probe and to
Ondilo's database.

So the integration asks you for them, with a bottle of buffer solution:

- set **pH buffer** to the value the bottle claims, dip the probe, press **Capture pH calibration
  point**;
- dip in a second buffer, set the field to it, capture again: the slope of your electrode is fitted on
  the two points and replaces the theoretical one;
- set **Known ppm** to a standard - or to the TDS the Ondilo app shows for the same water - and press
  **Capture conductivity calibration point**.

The five fields are `number` entities and the three captures are `button` entities, all under
Configuration on the device page, so they can be typed in, scripted, or undone with **Clear
calibration**. Everything is anchored at pH 7, the isopotential point of an electrode, and the slope is
compensated with the temperature the probe measures, so a calibration taken in 8 C pool water holds in a
heated spa. Until something has been captured the two estimated sensors stay unavailable, rather than
showing a guess with a unit on it.

## 1.2.4

The conductivity difference is published as a magnitude now.

Two reports twelve hours apart moved `cond+` by +1005 and `cond-` by +1006 while their difference stayed
at 1266 and 1267, and the Ondilo app's TDS sat at 253 then 257 ppm the whole time. The two electrodes are
driven with opposite polarity so that whatever they pick up in common cancels: the difference is the
measurement, the common mode is drift. The sign of that difference is not stable, though - the same pool
gave +1233 and -1266 - so the entity publishes its magnitude. It still carries no unit: the firmware
names both readings and names no unit for them, and the proportionality to the app's TDS, about 0.204 ppm
per count on these reports, is a fit on two field points rather than a unit Ondilo ever published.

## 1.2.3

Two diagnostic sensors now show the conductivity measurement the way the probe actually sends it.

- **Conductivity electrode positive** and **Conductivity electrode negative** publish `cond+` and
  `cond-` untouched, with no unit. The firmware names both readings (`COND_POS = %ld` and
  `COND_NEG = %ld`, 0x01568a and 0x0156a1 in fw 3.1.0) and names no unit for either; the number
  `Conductivity (raw)` shows is their difference, which is our arithmetic rather than Ondilo's. Until
  the scale is settled from real reports, the two halves are the honest place to look, and they are
  what any scaling will have to be fitted to.
- The debug log line for an incoming report now carries the raw measure. With `debug` enabled for
  `custom_components.ondilo_ico_local`, one report prints the whole JSON object the probe sent. It used
  to log only counts, which was useless for exactly the question these two entities answer.

## 1.2.2

Two entities carried names the probe never promised. Both came out of a live reading, and both are
fixed by publishing what the device actually measures rather than what we hoped it meant.

- **The pH entity is now "pH electrode potential", in millivolts.** The firmware writes that field as
  an integer (`"ph":%d`, offset 0x0158aa in fw 3.1.0) and its own console names the quantity twice: a
  guard reading `Measure is out of range [-500; 500]` (0x015996) and a print reading
  `Measure = %d.%d mV` (0x0159c0). A glass electrode gives roughly 59 mV per pH unit around a zero
  point near pH 7, which is precisely that window. Millivolts only become pH with the offset the
  probe's calibration recorded, and that offset is nowhere in the report — the firmware holds no
  calibration constant of any kind — so the conversion lives upstream, in Ondilo's cloud. A probe
  showing -64 here and pH 7.7 in the Ondilo app is the two ends of that offset. The entity keeps its
  identity and its history; the name and the unit change.
- **Conductivity lost its microsiemens-per-centimetre unit and is now "Conductivity (raw)".** The
  probe sends two electrode readings; the firmware bounds them (`Measure is out of range [50; 65535]`,
  0x0156d0) but never names a unit, and the difference we publish is our own construction. A field
  reading of 1233 here against 253 ppm of TDS upstream is not explained by any standard conversion
  factor, so one of the two is wrong, and a unit we cannot defend has no business being printed. The
  number itself is unchanged.

Coming next is a local pH estimate built on a calibration you take at home: dip the probe in a buffer,
record the millivolts as the offset, and let the integration convert with a Nernst slope compensated by
the temperature the probe already reports.

## 1.2.1

Fixes a crash on startup: the entry refused to set up with
`TypeError: 'NoneType' object can't be awaited`, before a single route was mounted.

- `hass.http.register_view()` is a **synchronous** method of Home Assistant Core
  (`homeassistant/components/http/server.py`, `def register_view(...) -> None`), and we awaited it.
  Direct mode therefore never started on any real Home Assistant.
- Our own bench had declared that method `async def`, which is why every local check was green.
  The bench now carries the core signature, and the API gate resolves every `await` the package
  performs against the core sources inside the published wheel: an await on a synchronous method
  is refused, and this exact bug is kept as a replay inside the gate.
- Nothing else moved. Routes, reply bodies, entities and the configuration flow are the ones 1.2.0
  shipped with.

## 1.2.0

Renamed to `ondilo_ico_local`, displayed as **Ondilo ICO (local)**, so it no longer shadows the official
Ondilo integration.

- Home Assistant Core ships an official Ondilo integration under the domain `ondilo_ico` (verified in
  2026.11.0): `cloud_polling`, OAuth against `interop.ondilo.com`, one configuration entry. Home Assistant
  gives `custom_components` priority over bundled integrations, so this package, published under that same
  domain, would have **silently disabled the official one**. The domain, the folder, the HACS name and the
  device names all moved to carry `local`, and the two integrations can now be installed side by side.
- They stay separate in every place that matters: separate configuration entries, separate devices (the
  official one names its device after the pool in the Ondilo app, this one after the probe serial with a
  `(local)` marker), separate entity ids. Nothing is migrated and nothing is replaced.
- The probe protocol did not move by one byte: the report route, the two keep-alives, the configuration
  fetch, the reply bodies and the sentinel values answer exactly as 1.1.0 did. Only the Home Assistant side
  changed its name.
- Leaving forwarding on, which is the default, keeps the Ondilo app *and* the official integration up to
  date: the report is resent to the real server byte for byte, with its original `Host` header.
- If you installed 1.1.0: delete its configuration entry, remove the `custom_components/ondilo_ico` folder,
  copy the new one and restart. Entities are not carried over, because the domain is what namespaces them.

## 1.1.0

Direct mode: the probe can now talk to Home Assistant itself, with no second machine.

- The config flow starts by asking **where the reports come from** — the probe itself (direct), or a
  separate bridge — and both shapes produce the same 19 entities with the same units, sentinels and
  availability rules.
- Written against **Home Assistant Core 2026.11.0**: the config flow uses the core `probatio` schema API
  (2026.9 and up), `SelectOptionDict` for its dropdown options and `UpdateFailed` from
  `homeassistant.helpers.update_coordinator`. The two names older examples still spell `SelectOption` and
  `homeassistant.exceptions.UpdateFailed` do not exist in that core, and installing this package with them
  would have failed on import. The build now re-reads every core symbol this package touches, plus its enum
  members and selector fields, in the 2026.11.0 release itself.
- Every module now declares `from __future__ import annotations`. Before that, importing the package under
  Python 3.12 or 3.13 raised `NameError: name 'IcoDevice' is not defined` — a method annotated with the name
  of the class being defined, inside that class body. It only ever imported under Python 3.14, where
  annotations are lazy by default. That is also what made the config-flow schemas checkable at all: the
  forms are now built and validated by the same schema library Home Assistant uses.
- Direct mode registers the routes the probe calls on Home Assistant's own web server
  (`POST /proxy/prod/wifi/message`, `POST /api/v2/devices/ping/<uuid>`, `POST /proxy/prod/wifi/ping`,
  `GET /proxy/prod/wifi/configuration/<version>`, and that configuration path again without its
  prefix), answering exactly like the Ondilo service: the reply body, the `success` flag, the 404 for
  a body that is not a report, and `{"value": 0}` for the keep-alive. The two prefixes are not
  symmetric, and that is firmware fact rather than preference: the report and the configuration fetch
  are built from templates that carry `/proxy/prod/wifi`, the keep-alive from a template that does not
  — answering only under the prefix would make the probe log an error on every ping. A check now reads
  the OTA image itself and fails the build if the integration ever stops serving a path the probe
  dials. Reaching these routes needs a DNS rewrite and a `:80 → :8123` forward, documented in the
  repository README with a `curl` check that tells the two failure modes apart.
- That forward ships as its own app repository,
  [`ondilo-ico-addons`](https://github.com/H3buss/ondilo-ico-addons): the Supervisor wants its
  repository file at the root of the store, which this repository cannot also carry. Two options —
  the port to take and where to send it — and Home Assistant's protection mode can stay on.
- Reports are pushed in: an arriving report wakes the coordinator up instead of waiting for the next
  poll, which stays as a backstop.
- **Forward to Ondilo** is on by default: the report is re-sent byte for byte with its original
  `Host` header so the mobile app and the Ondilo history keep working. Turn it off for a fully local
  setup. The `mode` entity is renamed **Collection mode** and now reads `direct`, `direct + cloud`,
  `proxy` or `local`.
- **Trusted DNS**: the real Ondilo address is asked from a resolver you choose, because under a DNS
  rewrite Home Assistant's own resolver answers with your machine. Any private, loopback or
  link-local answer is refused with a warning rather than POSTed back to ourselves.
- Bridge mode is unchanged, and stays the reference: the same state contract, the same `/health`
  check at setup, reconfigure and options as before.

## 1.0.0

First published form of the integration.

- Reads the local bridge API only: `GET /health`, `GET /api/devices`, `GET /api/devices/<uuid>`.
  No MQTT, no cloud, no other data source.
- Config flow with a real reachability test before the entry is created (`cannot_connect`,
  `invalid_auth`, `bad_version`), plus reconfigure to change host, port or token.
- Two devices per installation: the **bridge** (service device, configuration URL, bridge version)
  and one device **per probe** (`Ondilo`, `ICO V1`, firmware from the report).
- 19 entities per probe: 12 visible, 7 disabled by default (the report's internal counters and raw
  identity fields).
- Sentinels `-1` and `65535` from the probe are turned into `unknown`, never into `0`: in winter
  mode the probe genuinely does not measure pH, ORP or conductivity.
- `measure_time` is exposed as the bridge's reception time (the probe's own `time`/`sendtime`
  fields are counters, not epochs) — that entity is the honest freshness signal.
- `usb_power` is a binary sensor of class `plug`; `wifi_rssi` is published raw because its unit has
  not been confirmed by Ondilo.
- Entities go `unavailable` only on a provable failure: bridge unreachable, token refused,
  malformed reply.
- Translations: English and French.
- Requires Home Assistant 2026.9 or newer.
