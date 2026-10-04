"""Constants for the notiOne integration."""

from __future__ import annotations

DOMAIN = "notione"

# Endpoints (unofficial notiOne API, confirmed from captured traffic).
AUTH_BASE = "https://auth.notinote.me"
API_BASE = "https://api.notinote.me"
LOGIN_URL = f"{AUTH_BASE}/public/user/authorize/login"
DEVICELIST_URL = f"{API_BASE}/secured/internal/devicelist"
DEVICECONFIG_URL = f"{API_BASE}/secured/internal/deviceconfig"
LIVE_WS_URL = "wss://api.notinote.me:444/ws/secured/internal/live"
API_ACCEPT = "application/notinote.me-5+json"
USER_AGENT = "notiOne/2.2.3 (Android; Home Assistant)"

# Static public OAuth client used by the official panel/app. Not a user secret —
# it is the same value shipped in the web panel and the legacy n4ts/ha-notione.
CLIENT_BASIC_AUTH = (
    "Basic dGVzdC1vYXV0aC1jbGllbnQtaWQ6JDJ5JDEyJHZYT1V0RWVuVkZDTzFaZ3ky"
    "WWllUHVGM1dGL3NEZ05PM1luaFJqbDQ5TklEbEViR2VTZU91"
)
SCOPE = "NOTI"

CONF_EMAIL = "email"
CONF_PASSWORD = "password"
CONF_IDLE_INTERVAL = "idle_interval"
CONF_DEVICE_AUTOMATIONS = "device_automations"
CONF_DEVICE_LOCATION_LOG = "device_location_log"
CONF_ZONE_ENTITY = "zone_entity"
CONF_GARAGE_ENTITY = "garage_entity"
CONF_LOCATION_LOG_ENABLED = "location_log_enabled"
CONF_LOCATION_LOG_RETENTION_DAYS = "location_log_retention_days"

DEFAULT_LOCATION_LOG_ENABLED = True
DEFAULT_LOCATION_LOG_RETENTION_DAYS = 30
# Directory name under hass.config.path() where per-device location logs live.
LOCATION_LOG_DIR = "notione"

STATIONARY_INTERVALS = {
    "1 h": 3600,
    "6 h": 21600,
    "24 h": 86400,
}

DEFAULT_IDLE_INTERVAL = 60  # seconds between REST polls when LIVE is not active
MIN_INTERVAL = 5
MAX_INTERVAL = 3600

# Seconds added to the device's last gpstime + reporting interval to account for
# LTE upload latency and API indexing before fresh data is queryable.
PROPAGATION_BUFFER_S = 10

# WS close codes where a zone-triggered session should be re-armed for automatic
# restart once the device is confirmed still inside the zone via REST.
LIVE_RETRIABLE_CLOSE_CODES = {
    4000,  # server_error — transient server fault
    4002,  # idle_timeout — server ended idle session; worth restarting
}
