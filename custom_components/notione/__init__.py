"""The notiOne integration."""

from __future__ import annotations

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import NotiOneApi
from .const import (
    CONF_DEVICE_AUTOMATIONS,
    CONF_DEVICE_LOCATION_LOG,
    CONF_EMAIL,
    CONF_IDLE_INTERVAL,
    CONF_LOCATION_LOG_ENABLED,
    CONF_LOCATION_LOG_RETENTION_DAYS,
    CONF_PASSWORD,
    DEFAULT_IDLE_INTERVAL,
    DEFAULT_LOCATION_LOG_ENABLED,
    DEFAULT_LOCATION_LOG_RETENTION_DAYS,
    DOMAIN,
)
from .coordinator import NotiOneCoordinator
from .location_log import LocationLogger

PLATFORMS: list[Platform] = [
    Platform.DEVICE_TRACKER,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]

NotiOneConfigEntry = ConfigEntry[NotiOneCoordinator]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the authenticated location-log download endpoint."""
    hass.http.register_view(NotiOneLocationLogView(hass))
    return True


class NotiOneLocationLogView(HomeAssistantView):
    """Serve a device location log as a CSV attachment."""

    url = "/api/notione/location-log/{entry_id}/{device_id}"
    name = "api:notione:location-log"
    requires_auth = True

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass

    async def get(
        self, request: web.Request, entry_id: str, device_id: str
    ) -> web.StreamResponse:
        """Download the CSV log when the entry and device are loaded."""
        entry = self._hass.config_entries.async_get_entry(entry_id)
        if (
            entry is None
            or entry.domain != DOMAIN
            or entry.state is not ConfigEntryState.LOADED
            or not device_id.isdigit()
        ):
            raise web.HTTPNotFound
        numeric_device_id = int(device_id)
        coordinator = entry.runtime_data
        if numeric_device_id not in coordinator.data:
            raise web.HTTPNotFound
        path = coordinator.location_logger.path(numeric_device_id)
        exists = await self._hass.async_add_executor_job(
            coordinator.location_logger.log_exists, numeric_device_id
        )
        if not exists:
            raise web.HTTPNotFound
        return web.FileResponse(
            path,
            headers={
                "Content-Disposition": (
                    f'attachment; filename="notione_location_log_{numeric_device_id}.csv"'
                )
            },
        )


async def async_setup_entry(hass: HomeAssistant, entry: NotiOneConfigEntry) -> bool:
    """Set up notiOne from a config entry."""
    session = async_get_clientsession(hass)
    api = NotiOneApi(
        session,
        entry.data[CONF_EMAIL],
        entry.data[CONF_PASSWORD],
    )
    poll_interval = entry.options.get(CONF_IDLE_INTERVAL, DEFAULT_IDLE_INTERVAL)
    location_logger = LocationLogger(
        hass,
        entry.options.get(CONF_DEVICE_LOCATION_LOG, {}),
        entry.options.get(
            CONF_LOCATION_LOG_RETENTION_DAYS, DEFAULT_LOCATION_LOG_RETENTION_DAYS
        ),
        entry.options.get(CONF_LOCATION_LOG_ENABLED, DEFAULT_LOCATION_LOG_ENABLED),
    )
    coordinator = NotiOneCoordinator(hass, api, poll_interval, location_logger)

    await coordinator.async_config_entry_first_refresh()
    await coordinator.async_load_device_configs()
    coordinator.configure_automations(entry.options.get(CONF_DEVICE_AUTOMATIONS, {}))

    entry.runtime_data = coordinator
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: NotiOneConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_shutdown()
    return unloaded


async def _async_update_listener(
    hass: HomeAssistant, entry: NotiOneConfigEntry
) -> None:
    """Reload the entry when options (polling intervals) change."""
    await hass.config_entries.async_reload(entry.entry_id)
