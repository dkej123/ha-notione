"""Buttons for notiOne devices."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.components import persistent_notification
from homeassistant.components.button import ButtonEntity
from homeassistant.components.http.auth import async_sign_path
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import NotiOneConfigEntry
from .device_tracker import name_override
from .entity import NotiOneDeviceEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NotiOneConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    override = name_override(entry)
    async_add_entities(
        entity
        for device_id, device in coordinator.data.items()
        if (device.get("gpsDetails") or {}).get("imei") is not None
        for entity in (
            NotiOneRefreshConfigButton(coordinator, device_id, override),
            NotiOneDownloadLocationLogButton(entry, coordinator, device_id, override),
        )
    )


class NotiOneRefreshConfigButton(NotiOneDeviceEntity, ButtonEntity):
    _attr_translation_key = "refresh_config"
    _attr_icon = "mdi:refresh"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, device_id: int, override: str | None) -> None:
        super().__init__(coordinator, device_id, "refresh_config", override)

    async def async_press(self) -> None:
        await self.coordinator.async_refresh_device_config(self._device_id)


class NotiOneDownloadLocationLogButton(NotiOneDeviceEntity, ButtonEntity):
    """Create a short-lived authenticated link to one tracker's CSV log."""

    _attr_translation_key = "download_location_history"
    _attr_icon = "mdi:download"

    def __init__(
        self,
        entry: NotiOneConfigEntry,
        coordinator,
        device_id: int,
        override: str | None,
    ) -> None:
        super().__init__(coordinator, device_id, "download_location_history", override)
        self._entry = entry

    async def async_press(self) -> None:
        polish = self.hass.config.language == "pl"
        notification_id = (
            f"notione_location_log_{self._entry.entry_id}_{self._device_id}"
        )
        if not await self.hass.async_add_executor_job(
            self.coordinator.location_logger.log_exists, self._device_id
        ):
            persistent_notification.async_create(
                self.hass,
                (
                    "Dla tego trackera nie zapisano jeszcze żadnej historii lokalizacji."
                    if polish
                    else "No location history has been recorded for this tracker yet."
                ),
                "Historia lokalizacji notiOne"
                if polish
                else "notiOne location history",
                notification_id,
            )
            return
        path = f"/api/notione/location-log/{self._entry.entry_id}/{self._device_id}"
        signed_path = async_sign_path(self.hass, path, timedelta(minutes=5))
        persistent_notification.async_create(
            self.hass,
            (
                f"[Pobierz historię lokalizacji CSV]({signed_path})  \n"
                "Prywatny link wygaśnie za 5 minut. Naciśnij przycisk ponownie, "
                "aby utworzyć nowy."
                if polish
                else f"[Download CSV location history]({signed_path})  \n"
                "This private link expires in 5 minutes. Press the button again "
                "to create a new one."
            ),
            "Historia lokalizacji notiOne" if polish else "notiOne location history",
            notification_id,
        )
