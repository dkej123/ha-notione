# ha-notione project guide

Custom Home Assistant integration for **notiOne** GPS locators. It uses private,
reverse-engineered REST and WebSocket APIs. The integration exposes location and
telemetry, on-demand LIVE tracking, per-device automation, and supported device
settings.

## Repository layout

The repository root is the HACS integration root. Keep the integration under
`custom_components/notione/`.

```text
ha-notione/
├── .github/workflows/release.yml
├── hacs.json
├── README.md
├── CLAUDE.md
├── tests/
└── custom_components/notione/
    ├── __init__.py              # setup, unload, platform forwarding
    ├── manifest.json            # integration version and metadata
    ├── const.py                 # endpoints, defaults, option keys
    ├── api.py                   # login, devicelist, deviceconfig, re-auth
    ├── live_protocol.py         # dependency-free LIVE protobuf codec
    ├── location_log.py          # opt-in per-device location history CSV log
    ├── coordinator.py           # polling, LIVE, automation, config writes
    ├── config_flow.py           # setup and options flows
    ├── entity.py                # shared per-device entity bases
    ├── device_tracker.py
    ├── binary_sensor.py
    ├── sensor.py
    ├── switch.py
    ├── select.py
    ├── number.py
    ├── button.py
    ├── strings.json
    ├── icons.json
    ├── translations/{en,pl}.json
    └── tools/                   # manual API research tools
```

This is a standalone git repository. Run git commands from this root. Its SSH
remote is `git@github.com:dkwasniak/ha-notione.git` and the default branch is
`main`.

## Private API contracts

Constants and public client headers live in `const.py`.

1. Login: `POST https://auth.notinote.me/public/user/authorize/login`.
2. Devices: `GET https://api.notinote.me/secured/internal/devicelist`.
3. Configuration: `GET/POST /secured/internal/deviceconfig?deviceId=<id>`.
4. LIVE: `wss://api.notinote.me:444/ws/secured/internal/live` using binary
   protobuf frames.

`NotiOneApi` caches the access token and re-logs in near expiry or after HTTP
401. Do not use the uncaptured refresh-token contract. Never write
credentials, tokens, full IMEIs, raw account payloads, or coordinates to
`_LOGGER` debug/diagnostic output. This does not apply to the dedicated
location-log CSV files, whose entire purpose is to persist coordinates to
disk for the user — do not add coordinates to any other logging path.

Do not use `devicesamples` in the integration. Home Assistant recorder history
is the source of entity *state* history (graphs/statistics). The opt-in
location log (`location_log.py`) is a separate, deliberate mechanism: it
persists raw coordinates for GPS devices to local CSV files under
`/config/notione/`, purely so a stolen device can be traced after the fact.
It exists independently of, and does not replace, recorder history.

### Device configuration writes

Configuration changes must be serialized per device and follow:

```text
GET -> mutate one supported field -> POST full writable model -> GET
```

Preserve unrelated settings. Moving and battery-alarm intervals come from the
server's `allowedMoveIntervals`. Stationary choices are 1 h, 6 h, and 24 h.
Theft alarm, eTOLL, Strava, and device shutdown are out of scope.

### LIVE protocol

Expose LIVE for every GPS device with numeric `gpsDetails.imei`, regardless of
`gpsFeatures.allowLiveMode`. Send the IMEI enable request, parse server config
and GPS samples, and close manually with code 3000. Codes 4000-4005 are terminal.
Never automatically reconnect after a close, error, or server session limit.

While any LIVE socket is connecting or active, pause `devicelist` polling by
setting `update_interval = None`. LIVE samples update `lastPosition` in the
coordinator. When the final socket closes, restore polling and immediately
refresh REST data. Close sockets and tasks during integration unload.

## Architecture

`__init__.py` creates the shared API and coordinator, performs the first REST
refresh, loads device configurations, installs automation listeners, stores the
coordinator in `entry.runtime_data`, and forwards all platforms. Options changes
reload the entry.

`coordinator.py` owns `{deviceId: device}`, polling cadence, LIVE session state,
zone/garage automation, and device configuration locks.

- Adaptive REST polling remains fast while API motion, the legacy connection
  trigger, or its grace period is active.
- Zone membership uses `lastPosition` and the selected `zone.*` entity's
  latitude, longitude, and radius.
- REST data detects initial zone entry. LIVE samples maintain membership.
- A zone-started session stops on exit. A manual session does not.
- After manual stop, server close/error, OFFLINE, or garage stop, automatic LIVE
  requires leaving and re-entering the zone. Being inside at HA startup may start
  one automatic session.
- Every active session stops when notiOne reports `OFFLINE` or the configured
  `binary_sensor.*` garage entity is `on`. Garage states `off`, `unknown`, and
  `unavailable` do nothing.
- An unavailable/malformed zone disables zone automation without stopping a
  manual session.

All entities use `_attr_has_entity_name = True`. Device names come from user
override, API name, then `notiOne <id>`. Unique IDs use
`notione_<deviceId>[_<key>]`. Configuration entities read cached
`device_configs` and call coordinator methods for writes; they never construct
partial POST payloads directly.

## UI strings

Keep `strings.json`, `translations/en.json`, and `translations/pl.json` in sync.
Add icons only where a device class does not already provide an appropriate one.

## Validation

The host Python is 3.11. Avoid Python 3.12-only syntax.

```bash
python3 -m compileall -q custom_components/notione tests
for f in custom_components/notione/manifest.json \
  custom_components/notione/strings.json \
  custom_components/notione/icons.json \
  custom_components/notione/translations/*.json; do
  python3 -c "import json; json.load(open('$f'))"
done
python3 -m unittest discover -s tests -v
git diff --check
find . -name __pycache__ -type d -exec rm -rf {} +
```

LIVE protocol tests must stay independent of Home Assistant imports.
Coordinator/entity tests may stub HA or use an installed HA test environment.
Manual API tools must request passwords interactively and must not write secrets
or identifiers into committed output.

## Release process

For every user-facing release:

1. Bump `custom_components/notione/manifest.json` using semantic versioning.
2. Commit and push `main`.
3. Create and push an annotated matching tag:
   `git tag -a vX.Y.Z -m vX.Y.Z && git push origin vX.Y.Z`.
4. `.github/workflows/release.yml` publishes the GitHub Release for pushed `v*`
   tags. Verify the workflow/release after push.

Keep commits focused, use imperative subjects, and do not add model-specific
co-author trailers. Never commit HAR captures, credentials, tokens, full IMEIs,
or raw API responses.
