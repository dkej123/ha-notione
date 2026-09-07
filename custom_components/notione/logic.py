"""Pure helpers shared by notiOne coordinator behavior."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import asin, cos, radians, sin, sqrt
from typing import Any


@dataclass(frozen=True)
class AutomationDecision:
    """One pure zone/garage/offline automation transition."""

    action: str | None = None
    reason: str | None = None
    armed: bool = False


def evaluate_live_automation(
    *,
    live_on: bool,
    source: str | None,
    offline: bool,
    garage_on: bool,
    inside: bool | None,
    armed: bool,
) -> AutomationDecision:
    """Choose the next LIVE automation action and re-arm state."""
    if live_on and offline:
        return AutomationDecision("stop", "offline", False)
    if live_on and garage_on:
        return AutomationDecision("stop", "garage_connected", False)
    if inside is None:
        if live_on and source == "zone":
            return AutomationDecision("stop", "zone_unavailable", False)
        return AutomationDecision(armed=armed)
    if not inside:
        if live_on and source == "zone":
            return AutomationDecision("stop", "left_zone", True)
        return AutomationDecision(armed=True)
    if armed and not live_on and not offline and not garage_on:
        return AutomationDecision("start", "zone", False)
    return AutomationDecision(armed=armed)


def coordinates_in_zone(
    latitude: Any,
    longitude: Any,
    zone_latitude: Any,
    zone_longitude: Any,
    radius: Any,
) -> bool | None:
    """Return zone membership, or None for invalid/missing coordinates."""
    try:
        lat1 = radians(float(latitude))
        lon1 = radians(float(longitude))
        lat2 = radians(float(zone_latitude))
        lon2 = radians(float(zone_longitude))
        radius_m = float(radius)
    except (TypeError, ValueError):
        return None
    if radius_m < 0:
        return None
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    distance = 2 * 6_371_000 * asin(sqrt(a))
    return distance <= radius_m


def build_device_config_payload(
    current: dict[str, Any], field: str, value: Any, writable_fields: set[str]
) -> dict[str, Any]:
    """Preserve the complete known writable model while changing one field."""
    if field not in writable_fields:
        raise ValueError(f"Unsupported device config field: {field}")
    payload = {key: current[key] for key in writable_fields if key in current}
    payload[field] = value
    return payload


LOCATION_LOG_HEADER = (
    "timestamp",
    "latitude",
    "longitude",
    "accuracy",
    "speed",
    "battery",
    "moving",
    "device_state",
)


def build_location_log_row(
    timestamp: datetime,
    position: dict[str, Any],
    battery: Any,
    moving: bool,
    device_state: str | None,
) -> tuple[Any, ...]:
    """Return one location-log CSV row's fields for a position sample."""
    return (
        timestamp.isoformat(),
        position.get("latitude"),
        position.get("longitude"),
        position.get("accuracy"),
        position.get("speed"),
        battery,
        moving,
        device_state,
    )


def is_row_within_retention(row: list[Any], cutoff: datetime) -> bool:
    """Return whether a location-log data row is at/after the retention cutoff.

    Fails open (keeps the row) when the timestamp is missing or unparsable,
    so a malformed line is never silently dropped — only real age does that.
    """
    try:
        return datetime.fromisoformat(row[0]) >= cutoff
    except (IndexError, TypeError, ValueError):
        return True
