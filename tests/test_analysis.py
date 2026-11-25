from datetime import datetime, timezone
from pathlib import Path
import unittest
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fleet_ops import compare_cases, load_cases
from fleet_report import incident_chart


DEMO = Path(__file__).resolve().parents[1] / "demo"


class AnalysisTests(unittest.TestCase):
    def test_observation_field_mutations_are_detected_independently(self):
        mutations = {
            "firmware-amr-205": ("online_after_restart", False),
            "assign-available": ("assigned_device", None),
            "onboard-robot": ("telemetry_received", False),
            "register-sensor": ("result", "rejected_duplicate"),
        }
        plan = load_cases(DEMO / "release-plan.json", "expected")
        for case_id, (field, value) in mutations.items():
            with self.subTest(case=case_id):
                evidence = load_cases(DEMO / "release-pass.json", "observed")
                evidence[case_id]["observed"][field] = value
                report = compare_cases(plan, evidence)
                failures = [case for case in report["cases"] if case["status"] == "FAIL"]
                self.assertEqual([case["id"] for case in failures], [case_id])
                self.assertEqual(failures[0]["differences"], [
                    {"field": field, "expected": plan[case_id]["expected"][field], "observed": value},
                ])

    def test_timeline_uses_actual_elapsed_time_across_clock_change(self):
        try:
            zone = ZoneInfo("America/Chicago")
        except ZoneInfoNotFoundError:
            self.skipTest("IANA time-zone data unavailable")
        timeline = [{"device_id": "AMR-205", "timestamp": "2026-11-01T07:00:00+00:00",
                     "severity": "ERROR", "code": "FAULT", "job_id": "JOB-1"}]
        start = datetime(2026, 11, 1, 6, tzinfo=timezone.utc).astimezone(zone)
        end = datetime(2026, 11, 1, 8, tzinfo=timezone.utc).astimezone(zone)
        chart = incident_chart(timeline, "AMR-205", start, end, zone)
        self.assertIn("left:50.0000%", chart)
        self.assertIn("01:00 CDT", chart)
        self.assertIn("01:00 CST", chart)
