import csv
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


PROJECT = Path(__file__).resolve().parents[1]
DEMO = PROJECT / "demo"


class FleetOpsTests(unittest.TestCase):
    def run_cli(self, exit_code, *arguments):
        result = subprocess.run(
            [sys.executable, str(PROJECT / "fleet_ops.py"), *map(str, arguments)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, exit_code, result.stderr or result.stdout)
        return result

    def test_diagnostics_filters_and_exports_the_selected_timeline(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "incident.csv"
            arguments = (
                "diagnose", "--events", DEMO / "events.csv",
                "--device", "AMR-205", "--site", "RI-01",
                "--start", "2026-11-25T08:00:00-06:00",
                "--end", "2026-11-25T15:00:00Z", "--csv", target,
            )
            report = json.loads(self.run_cli(0, *arguments).stdout)
            self.assertEqual(report["event_count"], 4)
            self.assertEqual(report["fault_count"], 3)
            self.assertEqual(report["repeated_faults"], {"FRONT_SCANNER_FIELD_OCCUPIED": 3})
            self.assertEqual(report["fault_job_ids"], ["JOB-801", "JOB-803", "JOB-805"])
            self.assertEqual([event["device_id"] for event in report["peer_faults"]], ["AMR-207"])
            with target.open(encoding="utf-8", newline="") as file:
                self.assertEqual(list(csv.DictReader(file)), report["timeline"])

            original = target.read_bytes()
            self.run_cli(2, *arguments)
            self.assertEqual(target.read_bytes(), original)

    def validate(self, exit_code, evidence, *options):
        return self.run_cli(
            exit_code, "validate", "--plan", DEMO / "release-plan.json",
            "--evidence", evidence, *options,
        )

    def test_complete_release_passes(self):
        result = json.loads(self.validate(0, DEMO / "release-pass.json").stdout)
        self.assertEqual((result["passed"], result["failed"]), (10, 0))

    def test_release_regressions_fail_with_case_details(self):
        result = json.loads(self.validate(1, DEMO / "release-regression.json").stdout)
        self.assertEqual((result["passed"], result["failed"]), (7, 3))
        failures = {case["id"]: case["differences"] for case in result["cases"]
                    if case["status"] == "FAIL"}
        self.assertEqual(set(failures), {
            "firmware-amr-205", "queue-offline", "onboard-workstation",
        })
        self.assertEqual(failures["firmware-amr-205"], [
            {"field": "version", "expected": "2.8.1", "observed": "2.8.0"}
        ])

    def test_missing_evidence_fails_even_when_the_expected_value_is_null(self):
        evidence = json.loads((DEMO / "release-pass.json").read_text(encoding="utf-8"))
        evidence["cases"].pop()
        queue_case = next(case for case in evidence["cases"] if case["id"] == "queue-offline")
        del queue_case["observed"]["assigned_device"]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "incomplete.json"
            path.write_text(json.dumps(evidence), encoding="utf-8")
            result = json.loads(self.validate(1, path).stdout)
        failures = {case["id"]: case["differences"] for case in result["cases"]
                    if case["status"] == "FAIL"}
        self.assertEqual(failures, {
            "queue-offline": [
                {"field": "assigned_device", "expected": None, "reason": "missing_field"},
            ],
            "onboard-workstation": [{"reason": "missing_observation"}],
        })

    def test_duplicate_case_ids_are_invalid_input(self):
        evidence = json.loads((DEMO / "release-pass.json").read_text(encoding="utf-8"))
        evidence["cases"].append(evidence["cases"][0])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "duplicate.json"
            path.write_text(json.dumps(evidence), encoding="utf-8")
            result = self.validate(2, path)
        self.assertIn("unique, nonblank id", result.stderr)


    def test_simulator_collects_evidence_and_writes_a_release_report(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            for scenario, exit_code, counts in (
                ("baseline", 0, (10, 0)), ("regression", 1, (7, 3))
            ):
                with self.subTest(scenario=scenario):
                    output = base / scenario
                    run = self.run_cli(exit_code, "simulate", "--scenario", scenario, "--output", output)
                    summary = json.loads(run.stdout)
                    self.assertEqual((summary["passed"], summary["failed"]), counts)
                    self.assertEqual((summary["target_faults"], summary["peer_faults"],
                                      summary["site_faults"]), (3, 1, 5))
                    evidence = json.loads((output / "evidence.json").read_text(encoding="utf-8"))
                    self.assertEqual(len(evidence["cases"]), 10)
                    self.assertTrue(all(case["source"] == "fleet-simulator" and case["observed_at"]
                                        for case in evidence["cases"]))
                    replay = self.validate(exit_code, output / "evidence.json")
                    self.assertEqual(json.loads(replay.stdout), json.loads(
                        (output / "validation.json").read_text(encoding="utf-8")
                    ))
                    window = json.loads((output / "fleet-window.json").read_text(encoding="utf-8"))
                    self.assertEqual(window["faults_by_zone"], {"Zone C": 4, "Zone A": 1})
                    self.assertEqual(window["faults_by_code"], {
                        "FRONT_SCANNER_FIELD_OCCUPIED": 4, "LOCALIZATION_LOW": 1,
                    })
                    report = (output / "report.html").read_text(encoding="utf-8")
                    self.assertIn("Release validation by area", report)
                    self.assertIn(f"{counts[1]}</strong>Checks failed", report)
                    with (output / "incident.csv").open(encoding="utf-8", newline="") as stream:
                        self.assertEqual(len(list(csv.DictReader(stream))), 4)
                    self.run_cli(2, "simulate", "--scenario", scenario, "--output", output)
                    self.assertEqual((output / "report.html").read_text(encoding="utf-8"), report)


    def test_each_injected_fault_fails_only_its_own_case(self):
        faults = {
            "firmware_not_applied": "firmware-amr-205",
            "dispatch_ignores_status": "queue-offline",
            "workstation_no_telemetry": "onboard-workstation",
        }
        with tempfile.TemporaryDirectory() as directory:
            for fault, case_id in faults.items():
                with self.subTest(fault=fault):
                    output = Path(directory) / "reports" / fault
                    self.run_cli(1, "simulate", "--inject", fault, "--output", output)
                    data = json.loads((output / "validation.json").read_text(encoding="utf-8"))
                    self.assertEqual([case["id"] for case in data["cases"]
                                      if case["status"] == "FAIL"], [case_id])
                    evidence = json.loads((output / "evidence.json").read_text(encoding="utf-8"))
                    self.assertEqual(evidence["faults_injected"], [fault])

    def test_freshness_detects_stale_future_missing_and_invalid_timestamps(self):
        content = json.loads((DEMO / "release-pass.json").read_text(encoding="utf-8"))
        for case in content["cases"]:
            case["observed_at"] = "2026-11-25T15:00:00Z"
        options = ("--max-age", "10m", "--now", "2026-11-25T15:10:00Z")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "checked.json"
            path.write_text(json.dumps(content), encoding="utf-8")
            self.assertEqual(json.loads(self.validate(0, path, *options).stdout)["passed"], 10)

            content["cases"][0]["observed_at"] = "2026-11-25T14:59:59Z"
            content["cases"][1]["observed_at"] = "2026-11-25T15:11:01Z"
            del content["cases"][2]["observed_at"]
            content["cases"][3]["observed_at"] = "2026-11-25T15:00:00"
            path.write_text(json.dumps(content), encoding="utf-8")
            report = json.loads(self.validate(1, path, *options).stdout)
        self.assertEqual(report["passed"], 6)
        self.assertEqual([case["differences"][0]["reason"] for case in report["cases"][:4]], [
            "stale_observation", "observed_in_future", "missing_observed_at", "invalid_observed_at",
        ])

    def test_peer_window_includes_the_exact_boundary(self):
        arguments = (
            "diagnose", "--events", DEMO / "events.csv", "--device", "AMR-205",
            "--site", "RI-01", "--start", "2026-11-25T14:00:00Z",
            "--end", "2026-11-25T15:00:00Z", "--peer-window",
        )
        for window, count in (("488s", 0), ("489s", 1), ("15m", 1)):
            with self.subTest(window=window):
                report = json.loads(self.run_cli(0, *arguments, window).stdout)
                self.assertEqual(len(report["peer_faults"]), count)


if __name__ == "__main__":
    unittest.main()
