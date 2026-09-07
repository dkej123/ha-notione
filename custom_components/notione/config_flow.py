"""Config and options flow for notiOne."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import NotiOneApi, NotiOneApiError, NotiOneAuthError
from .const import (
    CONF_EMAIL,
    CONF_DEVICE_AUTOMATIONS,
    CONF_GARAGE_ENTITY,
    CONF_IDLE_INTERVAL,
    CONF_LOCATION_LOG_ENABLED,
    CONF_LOCATION_LOG_RETENTION_DAYS,
    CONF_PASSWORD,
    CONF_ZONE_ENTITY,
    DEFAULT_IDLE_INTERVAL,
    DEFAULT_LOCATION_LOG_ENABLED,
    DEFAULT_LOCATION_LOG_RETENTION_DAYS,
    DOMAIN,
    MAX_INTERVAL,
    MIN_INTERVAL,
)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Optional(CONF_NAME, default=""): str,
    }
)


class NotiOneConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the initial credential setup."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            email = user_input[CONF_EMAIL]
            await self.async_set_unique_id(email.lower())
            self._abort_if_unique_id_configured()

            session = async_get_clientsession(self.hass)
            api = NotiOneApi(session, email, user_input[CONF_PASSWORD])
            try:
                await api.login()
            except NotiOneAuthError:
                errors["base"] = "invalid_auth"
            except NotiOneApiError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_create_entry(title=email, data=user_input)

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return NotiOneOptionsFlow()


class NotiOneOptionsFlow(OptionsFlow):
    """Configure polling and per-device LIVE automation entities."""

    def __init__(self) -> None:
        super().__init__()
        self._new_options: dict[str, Any] = {}
        self._devices: list[tuple[int, str]] = []
        self._device_index = 0

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            self._new_options = dict(self.config_entry.options)
            self._new_options.update(user_input)
            coordinator = self.config_entry.runtime_data
            self._devices = [
                (device_id, device.get("name") or str(device_id))
                for device_id, device in coordinator.data.items()
                if (device.get("gpsDetails") or {}).get("imei") is not None
            ]
            self._device_index = 0
            if self._devices:
                return await self.async_step_device()
            return self.async_create_entry(title="", data=self._new_options)

        options = self.config_entry.options
        current_name = options.get(
            CONF_NAME, self.config_entry.data.get(CONF_NAME, "")
        )
        interval = vol.All(
            vol.Coerce(int), vol.Range(min=MIN_INTERVAL, max=MAX_INTERVAL)
        )
        retention_days = vol.All(vol.Coerce(int), vol.Range(min=1, max=365))
        schema = vol.Schema(
            {
                vol.Optional(CONF_NAME, default=current_name): str,
                vol.Required(
                    CONF_IDLE_INTERVAL,
                    default=options.get(CONF_IDLE_INTERVAL, DEFAULT_IDLE_INTERVAL),
                ): interval,
                vol.Optional(
                    CONF_LOCATION_LOG_ENABLED,
                    default=options.get(
                        CONF_LOCATION_LOG_ENABLED, DEFAULT_LOCATION_LOG_ENABLED
                    ),
                ): bool,
                vol.Optional(
                    CONF_LOCATION_LOG_RETENTION_DAYS,
                    default=options.get(
                        CONF_LOCATION_LOG_RETENTION_DAYS,
                        DEFAULT_LOCATION_LOG_RETENTION_DAYS,
                    ),
                ): retention_days,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)

    async def async_step_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Configure zone and garage entities for each GPS device."""
        device_id, device_name = self._devices[self._device_index]
        automations = dict(
            self._new_options.get(CONF_DEVICE_AUTOMATIONS, {})
        )
        current = dict(automations.get(str(device_id), {}))
        if user_input is not None:
            automations[str(device_id)] = {
                key: value
                for key, value in user_input.items()
                if value not in (None, "")
            }
            self._new_options[CONF_DEVICE_AUTOMATIONS] = automations
            self._device_index += 1
            if self._device_index >= len(self._devices):
                return self.async_create_entry(title="", data=self._new_options)
            return await self.async_step_device()

        zone_selector = selector.EntitySelector(
            selector.EntitySelectorConfig(domain="zone")
        )
        garage_selector = selector.EntitySelector(
            selector.EntitySelectorConfig(domain="binary_sensor")
        )
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_ZONE_ENTITY,
                    description={"suggested_value": current.get(CONF_ZONE_ENTITY)},
                ): zone_selector,
                vol.Optional(
                    CONF_GARAGE_ENTITY,
                    description={"suggested_value": current.get(CONF_GARAGE_ENTITY)},
                ): garage_selector,
            }
        )
        return self.async_show_form(
            step_id="device",
            data_schema=schema,
            description_placeholders={"device_name": device_name},
            last_step=self._device_index == len(self._devices) - 1,
        )
