"""The notiOne integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import NotiOneApi
from .const import (
    CONF_EMAIL,
    CONF_DEVICE_AUTOMATIONS,
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
        entry.options.get(CONF_LOCATION_LOG_ENABLED, DEFAULT_LOCATION_LOG_ENABLED),
        entry.options.get(
            CONF_LOCATION_LOG_RETENTION_DAYS, DEFAULT_LOCATION_LOG_RETENTION_DAYS
        ),
    )
    coordinator = NotiOneCoordinator(hass, api, poll_interval, location_logger)

    await coordinator.async_config_entry_first_refresh()
    await coordinator.async_load_device_configs()
    coordinator.configure_automations(
        entry.options.get(CONF_DEVICE_AUTOMATIONS, {})
    )

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
