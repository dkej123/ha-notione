"""Data, LIVE-session and device-configuration coordinator for notiOne."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
import time
from typing import Any

from aiohttp import ClientError, ClientWebSocketResponse, WSMsgType
from homeassistant.const import STATE_ON
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import NotiOneApi, NotiOneApiError, NotiOneAuthError
from .const import (
    CONF_GARAGE_ENTITY,
    CONF_ZONE_ENTITY,
    DOMAIN,
    LIVE_RETRIABLE_CLOSE_CODES,
    LIVE_WS_URL,
    MIN_INTERVAL,
    PROPAGATION_BUFFER_S,
)
from .live_protocol import (
    build_enable_request,
    parse_server_message,
    reason_for_close_code,
)
from .location_log import LocationLogger
from .logic import (
    build_device_config_payload,
    coordinates_in_zone,
    evaluate_live_automation,
)

_LOGGER = logging.getLogger(__name__)

_LOCATION_LOG_TRIM_INTERVAL = timedelta(hours=24)


def device_has_gps(device: dict) -> bool:
    """Return whether a device is a real GPS locator (has a numeric IMEI)."""
    return isinstance((device.get("gpsDetails") or {}).get("imei"), int)


_WRITABLE_CONFIG_FIELDS = {
    "movePositionInterval",
    "stationaryPositionInterval",
    "theftAlarmStatus",
    "etollEnabled",
    "allowCollectGpsData",
    "theftAlarmMode",
    "theftAlarmWholeDayNotification",
    "theftAlarmBeginTime",
    "theftAlarmEndTime",
    "allowDisableDevice",
    "theftAlarmSoundEnabled",
    "speedExceedAlarmEnabled",
    "speedExceedAlarmThreshold",
    "batterySavingAlarmEnabled",
    "batterySavingAlarmThreshold",
    "batterySavingAlarmMoveInterval",
}


def device_display_name(device: dict, device_id: int, override: str | None) -> str:
    """Resolve the device name: user override, API name, or fallback."""
    return (override or "").strip() or device.get("name") or f"notiOne {device_id}"


def device_is_offline(device: dict) -> bool:
    """Return whether notiOne reports stale/offline device data."""
    return device.get("deviceState") == "OFFLINE"


_STALE_GPS_MS = 300_000  # 5 minutes — beyond this, lastPosition speed is unreliable


def device_is_moving(device: dict) -> bool:
    """Return whether a device currently reports motion."""
    if device.get("deviceState") != "ONLINE":
        return False
    pos = device.get("lastPosition") or {}
    gpstime = pos.get("gpstime")
    if not gpstime or (time.time() * 1000 - gpstime) > _STALE_GPS_MS:
        return False
    accel = pos.get("accelerometerStatusEnum")
    if accel is not None:
        return accel == "MOVE"
    return bool(pos.get("speed"))


def point_in_zone(position: dict, zone_state: Any) -> bool | None:
    """Return zone membership, or None when coordinates are unavailable."""
    if zone_state is None or zone_state.state in ("unknown", "unavailable"):
        return None
    try:
        latitude = position["latitude"]
        longitude = position["longitude"]
        zone_latitude = zone_state.attributes["latitude"]
        zone_longitude = zone_state.attributes["longitude"]
        radius = zone_state.attributes["radius"]
    except (KeyError, TypeError):
        return None
    return coordinates_in_zone(
        latitude, longitude, zone_latitude, zone_longitude, radius
    )


@dataclass
class LiveState:
    """Observable state for one LIVE session."""

    source: str | None = None
    connecting: bool = False
    active: bool = False
    max_session_time: int | None = None
    close_code: int | None = None
    reason: str | None = None
    task: asyncio.Task[None] | None = None
    websocket: ClientWebSocketResponse | None = None
    retriable_error: bool = False

    @property
    def is_on(self) -> bool:
        return self.connecting or self.active


class NotiOneCoordinator(DataUpdateCoordinator[dict[int, dict]]):
    """Coordinate REST polling, LIVE sockets and per-device settings."""

    def __init__(
        self,
        hass: HomeAssistant,
        api: NotiOneApi,
        idle_interval: int,
        location_logger: LocationLogger,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=idle_interval),
        )
        self.api = api
        self._idle_interval = idle_interval
        self.location_logger = location_logger
        self.live_states: dict[int, LiveState] = {}
        self.device_configs: dict[int, dict[str, Any]] = {}
        self._config_locks: dict[int, asyncio.Lock] = {}
        self._automations: dict[int, dict[str, str]] = {}
        self._zone_armed: dict[int, bool] = {}
        self._automation_unsub: list[Callable[[], None]] = []
        self._evaluation_lock = asyncio.Lock()
        self._shutting_down = False
        self._automation_unsub.append(
            async_track_time_interval(
                hass, self._async_trim_location_logs, _LOCATION_LOG_TRIM_INTERVAL
            )
        )

    @property
    def live_active(self) -> bool:
        """Return whether any LIVE session is connecting or active."""
        return any(state.is_on for state in self.live_states.values())

    def _set_poll_interval(self, seconds: int | None) -> None:
        self.update_interval = None if seconds is None else timedelta(seconds=seconds)

    async def _async_update_data(self) -> dict[int, dict]:
        if self.live_active:
            _LOGGER.debug("REST poll skipped — LIVE session active")
            return self.data
        try:
            devices = await self.api.async_get_devices()
        except NotiOneAuthError as err:
            raise UpdateFailed(f"Authentication failed: {err}") from err
        except NotiOneApiError as err:
            raise UpdateFailed(str(err)) from err

        data = {dev["deviceId"]: dev for dev in devices if "deviceId" in dev}
        _LOGGER.debug("REST poll fetched %d device(s)", len(data))
        prev = self.data or {}
        now = datetime.now(timezone.utc)
        for device_id, device in data.items():
            old = prev.get(device_id, {})
            old_pos = old.get("lastPosition") or {}
            new_pos = device.get("lastPosition") or {}
            if "_last_position_updated" in old:
                device["_last_position_updated"] = old["_last_position_updated"]
            if (old_pos.get("latitude"), old_pos.get("longitude")) != (
                new_pos.get("latitude"),
                new_pos.get("longitude"),
            ) and new_pos.get("latitude") is not None:
                device["_last_position_updated"] = now
                if device_has_gps(device):
                    self.hass.async_create_task(
                        self.location_logger.async_log_position(
                            device_id,
                            new_pos,
                            (device.get("gpsDetails") or {}).get("battery"),
                            device_is_moving(device),
                            device.get("deviceState"),
                        )
                    )
        self._set_poll_interval(self._compute_next_poll_delay(data))
        return data

    async def _async_trim_location_logs(self, now: datetime | None = None) -> None:
        """Trim every GPS device's location log to the configured retention."""
        await asyncio.gather(
            *(
                self.location_logger.async_trim(device_id)
                for device_id, device in self.data.items()
                if device_has_gps(device)
            ),
            return_exceptions=True,
        )

    def _compute_next_poll_delay(self, data: dict[int, dict]) -> int:
        """Compute seconds until next REST poll aligned with device reporting cadence."""
        now = time.time()
        candidates = []
        for device_id, device in data.items():
            pos = device.get("lastPosition") or {}
            gpstime_ms = pos.get("gpstime")
            if not gpstime_ms:
                continue
            config = self.device_configs.get(device_id) or {}
            if device_is_moving(device):
                device_interval = config.get("movePositionInterval", self._idle_interval)
            else:
                device_interval = min(
                    config.get("stationaryPositionInterval", self._idle_interval),
                    self._idle_interval,
                )
            next_expected = gpstime_ms / 1000 + device_interval + PROPAGATION_BUFFER_S
            candidates.append(next_expected - now)
        if not candidates:
            return self._idle_interval
        best = min(candidates)
        delay = max(MIN_INTERVAL, min(int(best), self._idle_interval))
        _LOGGER.debug("Next REST poll in %d s (best candidate %.1f s)", delay, best)
        return delay

    async def async_load_device_configs(self) -> None:
        """Fetch configuration for every GPS device without failing setup."""
        device_ids = [
            device_id
            for device_id, device in self.data.items()
            if device_has_gps(device)
        ]
        results = await asyncio.gather(
            *(self.api.async_get_device_config(device_id) for device_id in device_ids),
            return_exceptions=True,
        )
        for device_id, result in zip(device_ids, results):
            if isinstance(result, dict):
                self.device_configs[device_id] = result
            else:
                _LOGGER.warning(
                    "Could not load config for device %s: %s", device_id, result
                )

    async def async_refresh_device_config(self, device_id: int) -> None:
        """Refresh one device configuration and notify entities."""
        lock = self._config_locks.setdefault(device_id, asyncio.Lock())
        async with lock:
            self.device_configs[device_id] = await self.api.async_get_device_config(
                device_id
            )
        self.async_update_listeners()

    async def async_update_device_config(
        self, device_id: int, field: str, value: Any
    ) -> None:
        """Perform serialized GET, mutate, full POST, GET for one field."""
        lock = self._config_locks.setdefault(device_id, asyncio.Lock())
        async with lock:
            current = await self.api.async_get_device_config(device_id)
            payload = build_device_config_payload(
                current, field, value, _WRITABLE_CONFIG_FIELDS
            )
            await self.api.async_set_device_config(device_id, payload)
            self.device_configs[device_id] = await self.api.async_get_device_config(
                device_id
            )
        self.async_update_listeners()

    def configure_automations(self, automations: dict[str, Any]) -> None:
        """Install state listeners for configured zones and garage sensors."""
        self._automations = {
            int(device_id): config
            for device_id, config in automations.items()
            if str(device_id).isdigit() and isinstance(config, dict)
        }
        for device_id in self.data:
            self._zone_armed[device_id] = False
        entities = {
            entity_id
            for config in self._automations.values()
            for key in (CONF_ZONE_ENTITY, CONF_GARAGE_ENTITY)
            if (entity_id := config.get(key))
        }
        if entities:
            self._automation_unsub.append(
                async_track_state_change_event(
                    self.hass, list(entities), self._state_changed
                )
            )
        self._automation_unsub.append(self.async_add_listener(self._data_changed))
        self.hass.async_create_task(self.async_evaluate_automations())

    @callback
    def _state_changed(self, event: Event) -> None:
        self.hass.async_create_task(self.async_evaluate_automations())

    @callback
    def _data_changed(self) -> None:
        self.hass.async_create_task(self.async_evaluate_automations())

    async def async_evaluate_automations(self) -> None:
        """Apply offline, garage and zone rules to every GPS device."""
        if self._shutting_down:
            return
        async with self._evaluation_lock:
            for device_id, device in self.data.items():
                state = self.live_states.setdefault(device_id, LiveState())
                config = self._automations.get(device_id, {})
                garage_entity = config.get(CONF_GARAGE_ENTITY)
                garage_on = bool(
                    garage_entity
                    and (garage := self.hass.states.get(garage_entity)) is not None
                    and garage.state == STATE_ON
                )
                zone_entity = config.get(CONF_ZONE_ENTITY)
                inside = point_in_zone(
                    device.get("lastPosition") or {},
                    self.hass.states.get(zone_entity) if zone_entity else None,
                )
                decision = evaluate_live_automation(
                    live_on=state.is_on,
                    source=state.source,
                    offline=device_is_offline(device),
                    garage_on=garage_on,
                    inside=inside,
                    armed=self._zone_armed.get(device_id, True),
                )
                self._zone_armed[device_id] = decision.armed
                if decision.action:
                    _LOGGER.debug(
                        "Zone automation device=%s action=%s reason=%s armed=%s",
                        device_id,
                        decision.action,
                        decision.reason,
                        decision.armed,
                    )
                if decision.action == "start":
                    await self.async_start_live(device_id, "zone")
                elif decision.action == "stop" and decision.reason:
                    await self.async_stop_live(device_id, decision.reason)

    async def async_start_live(self, device_id: int, source: str = "manual") -> None:
        """Start one LIVE WebSocket session."""
        device = self.data.get(device_id, {})
        imei = (device.get("gpsDetails") or {}).get("imei")
        state = self.live_states.setdefault(device_id, LiveState())
        if self._shutting_down or state.is_on or not isinstance(imei, int):
            return
        _LOGGER.info("Starting LIVE session for device %s (source=%s)", device_id, source)
        self._zone_armed[device_id] = False
        state.source = source
        state.connecting = True
        state.active = False
        state.max_session_time = None
        state.close_code = None
        state.reason = None
        state.retriable_error = False
        self._set_poll_interval(None)
        state.task = self.hass.async_create_task(
            self._async_live_loop(device_id, imei)
        )
        self.async_update_listeners()

    async def _async_live_loop(self, device_id: int, imei: int) -> None:
        state = self.live_states[device_id]
        close_code: int | None = None
        close_reason: str | None = None
        try:
            headers = await self.api.async_auth_headers()
            async with self.api.session.ws_connect(
                LIVE_WS_URL, headers=headers, heartbeat=30
            ) as websocket:
                state.websocket = websocket
                await websocket.send_bytes(build_enable_request(imei))
                async for message in websocket:
                    if message.type == WSMsgType.BINARY:
                        kind, payload = parse_server_message(message.data)
                        if kind == "config":
                            state.connecting = False
                            state.active = True
                            state.max_session_time = payload["max_session_time"]
                            _LOGGER.info(
                                "LIVE session active for device %s (max=%s s)",
                                device_id,
                                payload["max_session_time"],
                            )
                            self.async_update_listeners()
                        elif kind == "sample":
                            self._apply_live_sample(device_id, payload)
                    elif message.type == WSMsgType.ERROR:
                        raise websocket.exception() or ClientError("LIVE socket error")
                    elif message.type in (WSMsgType.CLOSE, WSMsgType.CLOSED):
                        break
                close_code = websocket.close_code
                close_reason = reason_for_close_code(close_code)
                if close_code in LIVE_RETRIABLE_CLOSE_CODES:
                    state.retriable_error = True
        except asyncio.CancelledError:
            close_code = state.close_code or 3000
            close_reason = state.reason or "integration_unload"
        except (ClientError, TimeoutError, ValueError) as err:
            _LOGGER.warning("LIVE session failed for device %s: %s", device_id, err)
            close_reason = str(err)
            state.retriable_error = True
        finally:
            state.websocket = None
            state.task = None
            state.connecting = False
            state.active = False
            state.close_code = state.close_code or close_code
            state.reason = (
                state.reason or close_reason or reason_for_close_code(close_code)
            )
            _LOGGER.info(
                "LIVE session ended for device %s — code=%s reason=%s retriable=%s",
                device_id,
                state.close_code,
                state.reason,
                state.retriable_error,
            )
            if state.retriable_error and state.source == "zone":
                _LOGGER.debug(
                    "LIVE device %s re-arming zone for automatic restart",
                    device_id,
                )
                self._zone_armed[device_id] = True
            self.async_update_listeners()
            if not self.live_active and not self._shutting_down:
                self._restore_polling_and_refresh()

    def _apply_live_sample(self, device_id: int, sample: dict[str, Any]) -> None:
        data = dict(self.data)
        device = dict(data.get(device_id, {}))
        position = dict(device.get("lastPosition") or {})
        old_coords = (position.get("latitude"), position.get("longitude"))
        position.update(sample)
        position.pop("imei", None)
        new_coords = (position.get("latitude"), position.get("longitude"))
        device["lastPosition"] = position
        device["deviceState"] = "ONLINE"
        if old_coords != new_coords and new_coords[0] is not None:
            device["_last_position_updated"] = datetime.now(timezone.utc)
        data[device_id] = device
        _LOGGER.debug(
            "LIVE sample device=%s lat=%.5f lon=%.5f gpstime=%s",
            device_id,
            new_coords[0] or 0.0,
            new_coords[1] or 0.0,
            sample.get("gpstime"),
        )
        self.async_set_updated_data(data)
        if old_coords != new_coords and new_coords[0] is not None:
            self.hass.async_create_task(
                self.location_logger.async_log_position(
                    device_id,
                    position,
                    (device.get("gpsDetails") or {}).get("battery"),
                    device_is_moving(device),
                    device.get("deviceState"),
                )
            )

    async def async_stop_live(self, device_id: int, reason: str = "manual") -> None:
        """Stop LIVE with the official client close code."""
        state = self.live_states.setdefault(device_id, LiveState())
        if not state.is_on:
            return
        _LOGGER.info("Stopping LIVE for device %s (reason=%s)", device_id, reason)
        if reason in ("manual", "offline", "garage_connected"):
            self._zone_armed[device_id] = False
        state.close_code = 3000
        state.reason = reason
        websocket = state.websocket
        task = state.task
        if websocket is not None and not websocket.closed:
            await websocket.close(code=3000, message=reason.encode("utf-8"))
        elif task is not None:
            task.cancel()
        if task is not None and task is not asyncio.current_task():
            try:
                await task
            except asyncio.CancelledError:
                pass
        if state.task is task:
            state.websocket = None
            state.task = None
            state.connecting = False
            state.active = False
            self.async_update_listeners()
            if not self.live_active and not self._shutting_down:
                self._restore_polling_and_refresh()

    def _restore_polling_and_refresh(self) -> None:
        self._set_poll_interval(self._idle_interval)
        self.hass.async_create_task(self.async_request_refresh())

    async def async_shutdown(self) -> None:
        """Close sockets, listeners and tasks during integration unload."""
        self._shutting_down = True
        for unsubscribe in self._automation_unsub:
            unsubscribe()
        self._automation_unsub.clear()
        await asyncio.gather(
            *(
                self.async_stop_live(device_id, "integration_unload")
                for device_id, state in self.live_states.items()
                if state.is_on
            ),
            return_exceptions=True,
        )
        tasks = [state.task for state in self.live_states.values() if state.task]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
