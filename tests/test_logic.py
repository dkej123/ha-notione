"""Tests for pure coordinator logic."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys

_MODULE_PATH = Path(__file__).parents[1] / "custom_components" / "notione" / "logic.py"
_SPEC = spec_from_file_location("notione_logic", _MODULE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_LOGIC = module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _LOGIC
_SPEC.loader.exec_module(_LOGIC)
build_device_config_payload = _LOGIC.build_device_config_payload
coordinates_in_zone = _LOGIC.coordinates_in_zone
evaluate_live_automation = _LOGIC.evaluate_live_automation
build_location_log_row = _LOGIC.build_location_log_row
is_row_within_retention = _LOGIC.is_row_within_retention
LOCATION_LOG_HEADER = _LOGIC.LOCATION_LOG_HEADER


class ZoneLogicTest(unittest.TestCase):
    def test_inside_and_outside_radius(self) -> None:
        self.assertTrue(coordinates_in_zone(51.1, 17.0, 51.1, 17.0, 500))
        self.assertFalse(coordinates_in_zone(51.11, 17.0, 51.1, 17.0, 500))

    def test_invalid_zone_disables_evaluation(self) -> None:
        self.assertIsNone(coordinates_in_zone(None, 17.0, 51.1, 17.0, 500))
        self.assertIsNone(coordinates_in_zone(51.1, 17.0, 51.1, 17.0, -1))


class DeviceConfigLogicTest(unittest.TestCase):
    def test_preserves_other_writable_fields(self) -> None:
        current = {"enabled": False, "threshold": 10, "readOnly": "ignored"}
        payload = build_device_config_payload(
            current, "enabled", True, {"enabled", "threshold"}
        )
        self.assertEqual({"enabled": True, "threshold": 10}, payload)

    def test_rejects_unsupported_field(self) -> None:
        with self.assertRaises(ValueError):
            build_device_config_payload({}, "readOnly", 1, {"enabled"})


class AutomationLogicTest(unittest.TestCase):
    def test_no_start_when_already_inside_at_startup(self) -> None:
        # armed=False at startup — device already in zone must exit first
        decision = evaluate_live_automation(
            live_on=False,
            source=None,
            offline=False,
            garage_on=False,
            inside=True,
            armed=False,
        )
        self.assertIsNone(decision.action)
        self.assertFalse(decision.armed)

    def test_start_on_zone_entry_after_exit(self) -> None:
        # armed=True after exit — entering zone now triggers LIVE
        decision = evaluate_live_automation(
            live_on=False,
            source=None,
            offline=False,
            garage_on=False,
            inside=True,
            armed=True,
        )
        self.assertEqual(("start", "zone", False), (
            decision.action, decision.reason, decision.armed
        ))

    def test_outside_at_startup_arms_for_entry(self) -> None:
        # armed=False at startup, device outside — first evaluation re-arms
        decision = evaluate_live_automation(
            live_on=False,
            source=None,
            offline=False,
            garage_on=False,
            inside=False,
            armed=False,
        )
        self.assertIsNone(decision.action)
        self.assertTrue(decision.armed)

    def test_zone_exit_stops_and_rearms(self) -> None:
        decision = evaluate_live_automation(
            live_on=True,
            source="zone",
            offline=False,
            garage_on=False,
            inside=False,
            armed=False,
        )
        self.assertEqual(("stop", "left_zone", True), (
            decision.action, decision.reason, decision.armed
        ))

    def test_manual_session_is_not_stopped_by_zone(self) -> None:
        for inside in (False, None):
            decision = evaluate_live_automation(
                live_on=True,
                source="manual",
                offline=False,
                garage_on=False,
                inside=inside,
                armed=False,
            )
            self.assertIsNone(decision.action)

    def test_offline_and_garage_stop_every_source(self) -> None:
        for source in ("manual", "zone"):
            offline = evaluate_live_automation(
                live_on=True,
                source=source,
                offline=True,
                garage_on=False,
                inside=True,
                armed=False,
            )
            garage = evaluate_live_automation(
                live_on=True,
                source=source,
                offline=False,
                garage_on=True,
                inside=True,
                armed=False,
            )
            self.assertEqual("offline", offline.reason)
            self.assertEqual("garage_connected", garage.reason)
            self.assertFalse(offline.armed)
            self.assertFalse(garage.armed)

    def test_blocked_session_requires_exit_before_restart(self) -> None:
        blocked = evaluate_live_automation(
            live_on=False,
            source="zone",
            offline=False,
            garage_on=False,
            inside=True,
            armed=False,
        )
        self.assertIsNone(blocked.action)
        rearmed = evaluate_live_automation(
            live_on=False,
            source="zone",
            offline=False,
            garage_on=False,
            inside=False,
            armed=blocked.armed,
        )
        self.assertTrue(rearmed.armed)


class LocationLogRowTest(unittest.TestCase):
    def test_builds_full_row(self) -> None:
        timestamp = datetime(2026, 9, 7, 8, 0, 0, tzinfo=timezone.utc)
        position = {"latitude": 51.1, "longitude": 17.0, "accuracy": 5, "speed": 12}
        row = build_location_log_row(timestamp, position, 80, True, "ONLINE")
        self.assertEqual(len(row), len(LOCATION_LOG_HEADER))
        self.assertEqual(
            row,
            (timestamp.isoformat(), 51.1, 17.0, 5, 12, 80, True, "ONLINE"),
        )

    def test_missing_position_fields_are_none(self) -> None:
        timestamp = datetime(2026, 9, 7, 8, 0, 0, tzinfo=timezone.utc)
        row = build_location_log_row(timestamp, {}, None, False, None)
        self.assertEqual(row[1:5], (None, None, None, None))


class LocationLogRetentionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.cutoff = datetime(2026, 9, 1, tzinfo=timezone.utc)

    def test_row_after_cutoff_is_kept(self) -> None:
        row = [(self.cutoff + timedelta(days=1)).isoformat(), "51.1", "17.0"]
        self.assertTrue(is_row_within_retention(row, self.cutoff))

    def test_row_before_cutoff_is_dropped(self) -> None:
        row = [(self.cutoff - timedelta(days=1)).isoformat(), "51.1", "17.0"]
        self.assertFalse(is_row_within_retention(row, self.cutoff))

    def test_malformed_timestamp_is_kept(self) -> None:
        self.assertTrue(is_row_within_retention(["not-a-timestamp"], self.cutoff))

    def test_empty_row_is_kept(self) -> None:
        self.assertTrue(is_row_within_retention([], self.cutoff))


if __name__ == "__main__":
    unittest.main()
