import csv
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


PROJECT = Path(__file__).resolve().parents[1]


class ExerciseTests(unittest.TestCase):
    def run_cli(self, code, *args):
        result = subprocess.run([sys.executable, str(PROJECT / "fleet_ops.py"), *map(str, args)],
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, code, result.stderr or result.stdout)
        if result.stdout:
            return json.loads(result.stdout)

    def test_suite_generates_replayable_evidence_and_expected_recoveries(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "reports" / "suite"
            summary = self.run_cli(0, "exercise", "--output", output)
            self.assertEqual((summary["passed"], summary["failed"]), (23, 0))
            self.assertEqual(sum(row["jobs_completed"] for row in summary["scenarios"]), 11)
            index = (output / "index.html").read_text(encoding="utf-8")
            metrics = {}
            events = {}
            for row in summary["scenarios"]:
                name = row["scenario"]
                path = output / name
                original = json.loads((path / "validation.json").read_text(encoding="utf-8"))
                self.assertEqual(self.run_cli(0, "validate", "--plan", path / "plan.json",
                                              "--evidence", path / "evidence.json"), original)
                self.assertIn(row["report"], index)
                self.assertIn("Source references", (output / row["report"]).read_text(encoding="utf-8"))
                metrics[name] = {
                    job["job_id"]: job for job in json.loads(
                        (path / "job-metrics.json").read_text(encoding="utf-8")
                    )
                }
                with (path / "events.csv").open(newline="", encoding="utf-8") as file:
                    events[name] = list(csv.DictReader(file))

            offline = events["mid-job-offline"]
            job_701 = [event["code"] for event in offline if event["job_id"] == "JOB-701"]
            self.assertEqual(job_701, ["JOB_QUEUED", "JOB_DISPATCHED", "JOB_HELD",
                                       "PRE_PICKUP_CONFIRMED", "JOB_REASSIGNED", "JOB_COMPLETED"])
            self.assertEqual(metrics["mid-job-offline"]["JOB-701"]["reassignment_seconds"], 45)
            self.assertEqual(metrics["mid-job-offline"]["JOB-702"]["elapsed_seconds"], 40)

            self.assertEqual(metrics["blocked-aisle"]["JOB-712"]["route"], "Zone D")
            self.assertEqual(metrics["blocked-aisle"]["JOB-711"]["queue_seconds"], 75)
            self.assertEqual(metrics["low-battery"]["JOB-721"]["queue_seconds"], 420)
            self.assertEqual(metrics["stale-workstation"]["JOB-731"]["queue_seconds"], 45)
            self.assertEqual(metrics["dispatch-contention"]["JOB-742"]["queue_seconds"], 50)
            self.run_cli(2, "exercise", "--output", output)
            self.assertEqual((output / "index.html").read_text(encoding="utf-8"), index)

    def test_validator_flags_evidence_of_dispatch_in_closed_aisle(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "single"
            result = self.run_cli(0, "exercise", "--scenario", "blocked-aisle", "--output", output)
            self.assertEqual(len(result["scenarios"]), 1)
            scenario = output / "blocked-aisle"
            evidence = json.loads((scenario / "evidence.json").read_text(encoding="utf-8"))
            detour = next(case for case in evidence["cases"] if case["id"] == "use-approved-detour")
            detour["observed"]["route"] = "Zone C"
            path = output / "invalid-evidence.json"
            path.write_text(json.dumps(evidence), encoding="utf-8")
            validation = self.run_cli(1, "validate", "--plan", scenario / "plan.json",
                                      "--evidence", path)
            self.assertEqual(validation["failed"], 1)
            case = next(case for case in validation["cases"] if case["status"] == "FAIL")
            self.assertEqual(case["differences"], [
                {"field": "route", "expected": "Zone D", "observed": "Zone C"},
            ])
