"""Independent, opt-in location history log for GPS devices.

Persists coordinates to a per-device CSV file under
`hass.config.path(LOCATION_LOG_DIR)`, entirely separate from Home Assistant's
recorder history. Its sole purpose is to let a stolen device be traced back
after the fact, regardless of the recorder's own purge policy.
"""

from __future__ import annotations

import asyncio
import csv
from datetime import datetime, timedelta, timezone
import logging
import os
from typing import Any, Mapping

from homeassistant.core import HomeAssistant

from .const import LOCATION_LOG_DIR
from .logic import LOCATION_LOG_HEADER, build_location_log_row, is_row_within_retention

_LOGGER = logging.getLogger(__name__)


class LocationLogger:
    """Append-only per-device CSV location log with periodic retention trim."""

    def __init__(
        self,
        hass: HomeAssistant,
        enabled_devices: Mapping[str, bool],
        retention_days: int,
        default_enabled: bool = True,
    ) -> None:
        self._hass = hass
        self._enabled_devices = {
            str(device_id): bool(enabled)
            for device_id, enabled in enabled_devices.items()
        }
        self._default_enabled = default_enabled
        self._retention_days = retention_days
        self._locks: dict[int, asyncio.Lock] = {}

    def _lock(self, device_id: int) -> asyncio.Lock:
        return self._locks.setdefault(device_id, asyncio.Lock())

    def path(self, device_id: int) -> str:
        """Return the CSV path for one device."""
        return self._hass.config.path(LOCATION_LOG_DIR, f"location_log_{device_id}.csv")

    def is_enabled(self, device_id: int) -> bool:
        """Return whether history logging is enabled for one device."""
        return self._enabled_devices.get(str(device_id), self._default_enabled)

    def set_enabled(self, device_id: int, enabled: bool) -> None:
        """Update history logging for one device at runtime."""
        self._enabled_devices[str(device_id)] = enabled

    def log_exists(self, device_id: int) -> bool:
        """Return whether one device already has a CSV log."""
        return os.path.isfile(self.path(device_id))

    async def async_log_position(
        self,
        device_id: int,
        position: dict[str, Any],
        battery: Any,
        moving: bool,
        device_state: str | None,
    ) -> None:
        """Append one position sample for a device, if logging is enabled."""
        if not self.is_enabled(device_id):
            return
        row = build_location_log_row(
            datetime.now(timezone.utc), position, battery, moving, device_state
        )
        async with self._lock(device_id):
            try:
                await self._hass.async_add_executor_job(self._write_row, device_id, row)
            except OSError as err:
                _LOGGER.warning(
                    "Could not write location log for device %s: %s", device_id, err
                )

    def _write_row(self, device_id: int, row: tuple[Any, ...]) -> None:
        path = self.path(device_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        is_new = not os.path.exists(path)
        with open(path, "a", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            if is_new:
                writer.writerow(LOCATION_LOG_HEADER)
            writer.writerow(row)

    async def async_trim(self, device_id: int) -> None:
        """Drop rows older than the configured retention window."""
        if not self.is_enabled(device_id):
            return
        cutoff = datetime.now(timezone.utc) - timedelta(days=self._retention_days)
        async with self._lock(device_id):
            try:
                await self._hass.async_add_executor_job(
                    self._trim_file, device_id, cutoff
                )
            except OSError as err:
                _LOGGER.warning(
                    "Could not trim location log for device %s: %s", device_id, err
                )

    def _trim_file(self, device_id: int, cutoff: datetime) -> None:
        path = self.path(device_id)
        if not os.path.exists(path):
            return
        with open(path, newline="", encoding="utf-8") as handle:
            rows = list(csv.reader(handle))
        if not rows:
            return
        header, data_rows = rows[0], rows[1:]
        kept = [row for row in data_rows if is_row_within_retention(row, cutoff)]
        if len(kept) == len(data_rows):
            return
        tmp_path = f"{path}.tmp"
        with open(tmp_path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(header)
            writer.writerows(kept)
        os.replace(tmp_path, path)
