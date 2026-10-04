"""Switch entities for notiOne LIVE mode and alarms."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import NotiOneConfigEntry
from .const import CONF_DEVICE_LOCATION_LOG
from .coordinator import LiveState
from .device_tracker import name_override
from .entity import NotiOneConfigEntity, NotiOneDeviceEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NotiOneConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create LIVE and alarm switches for GPS devices."""
    coordinator = entry.runtime_data
    override = name_override(entry)
    gps_devices = [
        device_id
        for device_id, device in coordinator.data.items()
        if (device.get("gpsDetails") or {}).get("imei") is not None
    ]
    async_add_entities(
        entity
        for device_id in gps_devices
        for entity in (
            NotiOneLiveSwitch(coordinator, device_id, override),
            NotiOneLocationLogSwitch(entry, coordinator, device_id, override),
            NotiOneAlarmSwitch(
                coordinator,
                device_id,
                "speed_alarm",
                "speedExceedAlarmEnabled",
                override,
            ),
            NotiOneAlarmSwitch(
                coordinator,
                device_id,
                "battery_alarm",
                "batterySavingAlarmEnabled",
                override,
            ),
        )
    )


class NotiOneLiveSwitch(NotiOneDeviceEntity, SwitchEntity):
    """Manually control a device LIVE session."""

    _attr_translation_key = "live"
    _attr_icon = "mdi:crosshairs-gps"

    def __init__(self, coordinator, device_id: int, override: str | None) -> None:
        super().__init__(coordinator, device_id, "live", override)

    @property
    def live_state(self) -> LiveState:
        return self.coordinator.live_states.setdefault(self._device_id, LiveState())

    @property
    def is_on(self) -> bool:
        return self.live_state.is_on

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        state = self.live_state
        status = "connecting" if state.connecting else "active"
        if not state.is_on:
            status = "off"
        return {
            "status": status,
            "source": state.source,
            "max_session_time": state.max_session_time,
            "close_code": state.close_code,
            "reason": state.reason,
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_start_live(self._device_id, "manual")

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_stop_live(self._device_id, "manual")


class NotiOneLocationLogSwitch(NotiOneDeviceEntity, SwitchEntity):
    """Control independent CSV history logging for one tracker."""

    _attr_translation_key = "location_history"
    _attr_icon = "mdi:map-marker-path"

    def __init__(
        self,
        entry: NotiOneConfigEntry,
        coordinator,
        device_id: int,
        override: str | None,
    ) -> None:
        super().__init__(coordinator, device_id, "location_history", override)
        self._entry = entry

    @property
    def is_on(self) -> bool:
        return self.coordinator.location_logger.is_enabled(self._device_id)

    async def _async_set_enabled(self, enabled: bool) -> None:
        if self.is_on == enabled:
            return
        self.coordinator.location_logger.set_enabled(self._device_id, enabled)
        options = dict(self._entry.options)
        devices = dict(options.get(CONF_DEVICE_LOCATION_LOG, {}))
        devices[str(self._device_id)] = enabled
        options[CONF_DEVICE_LOCATION_LOG] = devices
        self.async_write_ha_state()
        self.hass.config_entries.async_update_entry(self._entry, options=options)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_set_enabled(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_set_enabled(False)


class NotiOneAlarmSwitch(NotiOneConfigEntity, SwitchEntity):
    """Toggle a boolean alarm setting."""

    def __init__(
        self,
        coordinator,
        device_id: int,
        key: str,
        field: str,
        override: str | None,
    ) -> None:
        super().__init__(coordinator, device_id, key, override)
        self._attr_translation_key = key
        self._field = field

    @property
    def is_on(self) -> bool:
        return bool(self.device_config.get(self._field))

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_update_device_config(
            self._device_id, self._field, True
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_update_device_config(
            self._device_id, self._field, False
        )
