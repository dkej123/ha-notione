# notiOne for Home Assistant

A custom [Home Assistant](https://www.home-assistant.io/) integration for
[notiOne](https://notione.com/) GPS locators. It logs in with your notiOne
account and exposes each GPS device in Home Assistant, including on-demand LIVE
tracking, zone automation, telemetry, and supported device settings.

notiOne has no official API — this integration uses the same private endpoints
as the notiOne web panel. See the [disclaimer](#disclaimer).

## Features

- **Live location** — each GPS locator becomes a `device_tracker` entity
  (latitude/longitude), shown on the HA map with history via the recorder.
- **LIVE tracking** — a manual switch opens the notiOne binary WebSocket and
  updates position from incoming GPS samples while REST polling is paused.
- **Zone automation** — optionally start LIVE when a device enters a selected
  HA zone and stop it when it leaves.
- **Garage/offline stop** — stop any LIVE session when the device becomes
  offline or a selected garage `binary_sensor` turns on.
- **Motion sensor** — a `binary_sensor` (device class `moving`) per device.
- **Telemetry sensors** — battery, speed, temperature, humidity, last-seen time,
  device state, and reverse-geocoded city/place as individual sensor entities
  (with history and proper units).
- **Adaptive polling** — polls slowly while parked and speeds up automatically
  while the device reports motion.
- **Device settings** — movement and stationary intervals, speed alarm, and
  battery-saving alarm controls.
- **UI configuration** — set up and configure entirely from the HA UI; no YAML.

## Entities

For each notiOne GPS device:

| Entity | Type | Notes |
| --- | --- | --- |
| `device_tracker.<name>` | Device tracker | GPS position (lat/lon), `battery_level`, accuracy |
| `switch.<name>_live_tracking` | Switch | Manual LIVE session; attributes show source, status, limit, close code, and reason |
| `switch.<name>_location_history` | Switch | Enables the independent CSV position history for this tracker |
| `button.<name>_download_location_history` | Button | Creates a private, five-minute CSV download link in a HA notification |
| `binary_sensor.<name>_moving` | Binary sensor (`moving`) | `on` while the device reports motion |
| `sensor.<name>_battery` | Sensor (battery, %) | Locator battery level |
| `sensor.<name>_speed` | Sensor (speed, km/h) | Current speed |
| `sensor.<name>_temperature` | Sensor (temperature, °C) | Reported temperature |
| `sensor.<name>_humidity` | Sensor (humidity, %) | Reported humidity |
| `sensor.<name>_last_seen` | Sensor (timestamp) | Time of the last GPS fix |
| `sensor.<name>_city` | Sensor (text) | Reverse-geocoded city |
| `sensor.<name>_place` | Sensor (text) | Reverse-geocoded place/street |
| `sensor.<name>_device_state` | Sensor (text) | e.g. `ONLINE` / `OFFLINE` |

Each GPS device also gets configuration entities for moving/stationary
intervals, speed and battery alarms, their thresholds, the battery alarm
interval, and a button that refreshes configuration from notiOne.

Phones and Bluetooth beacons in the account are ignored — only GPS locators are
added.

## Installation

### HACS (recommended)

1. In HACS, open **⋮ → Custom repositories**.
2. Add `https://github.com/dkwasniak/ha-notione`, category **Integration**.
3. Find **notiOne** in HACS and click **Download**.
4. Restart Home Assistant.

### Manual

1. Copy `custom_components/notione/` into your Home Assistant config directory:
   `/config/custom_components/notione/`.
2. Restart Home Assistant.

## Configuration

After installing, add the integration from the UI:

**Settings → Devices & Services → Add Integration → notiOne**, then enter your
notiOne **email** and **password**. Credentials are stored encrypted in the
Home Assistant config entry.

Click **Configure** on the integration at any time to adjust:

| Option | Default | Description |
| --- | --- | --- |
| Tracker name | notiOne name | Overrides the device name; the tracker and its sensors inherit it as their name prefix. Leave blank to keep the name from notiOne. |
| Idle polling interval | 30 s | How often to poll while no device is moving. |
| Moving polling interval | 10 s | How often to poll while a device reports motion. |
| Connection entity | — | Optional `binary_sensor`/`input_boolean`/presence entity that is `on`/`home` when the device is connected (e.g. an ESPHome BLE presence sensor). Turning on forces the fast interval immediately and refreshes — no waiting for API motion. |
| Keep fast polling after disconnect | 300 s | Grace window keeping the fast interval after the connection entity goes off, bridging the device's LTE warm-up until the API reports motion. `0` disables it. |
| Location log retention (days) | 30 | Rows older than this are trimmed once a day. |

The options flow then asks separately for each GPS device:

| Option | Description |
| --- | --- |
| Log location history to a file | Per tracker, on by default. Appends every real position change from REST or LIVE to that tracker's CSV file. It can also be changed with the tracker-level switch. |
| LIVE start zone | Optional `zone.*`. Entering its configured radius starts one automatic LIVE session; leaving stops a zone-started session. |
| Garage connection sensor | Optional `binary_sensor.*`. State `on` stops every LIVE session for that device. |

After a manual stop, server limit/error, offline stop, or garage stop, automatic
LIVE remains disarmed until the device leaves the zone and enters it again.
Manual LIVE remains available independently of the zone configuration.

### Fast-polling logic

Fast polling is active when **API motion OR connection entity on OR within the
grace window**. So when you power on the bike at home its BLE connection triggers
fast polling instantly; when you ride out of BLE range the grace window keeps it
fast until the device's LTE comes online and the API reports motion — a seamless
handover. It returns to the idle interval once there's no motion, no connection,
and the grace window has elapsed. The connection entity only affects the polling
cadence — the **Moving** sensor stays pure API motion.

## How it works

The integration authenticates with your notiOne account, periodically fetches
device positions and telemetry, and supports real-time position updates and
device settings — all through the same backend as the notiOne web panel.
The access token is cached and refreshed automatically.

LIVE increases locator power usage. The integration does not reconnect a closed
or expired session automatically.

### Location log

When enabled (default), every real position change for a GPS device — from
REST polling or LIVE — is appended to its own CSV file at
`/config/notione/location_log_<deviceId>.csv`. This is independent of Home
Assistant's recorder and its purge policy, so it keeps working even if
recorder history is configured to expire quickly. Rows older than the
configured retention (default 30 days) are trimmed once a day. The file is a
plain CSV. Press the tracker's **Download location history** button to receive
a private, five-minute download link in Home Assistant, or copy the file off
the host directly (e.g. with `scp` over SSH).

## Development

Check the API responds for your account without Home Assistant:

```bash
./custom_components/notione/tools/test_login.sh you@example.com
```

It prints whether login and the device list succeed, with each device's
coordinates.

## Disclaimer

This project is not affiliated with or endorsed by notiOne. It relies on a
private API that may change or break at any time. Use at your own risk.

## License

[MIT](LICENSE)
