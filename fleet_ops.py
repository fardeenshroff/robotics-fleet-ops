"""Analyze exported fleet logs and compare observed results with a release plan."""

import argparse
from bisect import bisect_left
import csv
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import sys
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fleet_exercises import EXERCISES, ExerciseRun
from fleet_report import render, render_exercise, render_exercise_index
from fleet_simulator import END, FAULTS, SCENARIOS, SITE, START, TARGET, SimulatedFleet


EVENT_FIELDS = ("timestamp", "device_id", "site", "zone", "severity",
                "code", "job_id", "message")
FAULT_LEVELS = {"ERROR", "CRITICAL"}
PEER_WINDOW = timedelta(minutes=15)
CLOCK_SKEW = timedelta(minutes=1)


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        raise argparse.ArgumentTypeError(
            f"Expected an ISO 8601 timestamp with a UTC offset, got {value!r}"
        ) from None


def duration(value):
    match = re.fullmatch(r"([0-9]+)([smhd])", value)
    if match:
        amount, unit = match.groups()
        try:
            seconds = int(amount) * {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]
            if seconds > 0:
                return timedelta(seconds=seconds)
        except (ValueError, OverflowError):
            pass
    raise argparse.ArgumentTypeError(
        f"Expected a positive duration like 90s, 15m, 2h or 1d, got {value!r}"
    )


def reporting_zone(value):
    if value == "UTC":
        return timezone.utc
    try:
        return ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        raise argparse.ArgumentTypeError(
            f"Unknown time zone {value!r}. Use an IANA zone with time-zone data installed, or UTC."
        ) from None


def load_events(path):
    events = []
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream, strict=True)
        if tuple(reader.fieldnames or ()) != EVENT_FIELDS:
            raise ValueError(f"{path}: expected columns {', '.join(EVENT_FIELDS)} in order")
        for row in reader:
            required = ("timestamp", "device_id", "site", "zone", "severity", "code")
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"{path}:{reader.line_num}: incorrect number of columns")
            if any(not row[field].strip() for field in required):
                raise ValueError(f"{path}:{reader.line_num}: missing required event field")
            try:
                instant = timestamp(row["timestamp"])
            except argparse.ArgumentTypeError as error:
                raise ValueError(f"{path}:{reader.line_num}: {error}") from None
            events.append((instant, row))
    return sorted(events, key=lambda event: event[0])


def spreadsheet_cell(value):
    # Treat untrusted log strings as text when a CSV is opened in a spreadsheet.
    if value.startswith(("\t", "\r", "\n")) or value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def site_events(path, site, start, end):
    if start >= end:
        raise ValueError("--start must precede --end")
    return [row for instant, row in load_events(path)
            if start <= instant < end and row["site"] == site]


def find_peer_faults(events, faults, device, window):
    target_times = defaultdict(list)
    for row in faults:
        target_times[row["zone"], row["code"]].append(timestamp(row["timestamp"]))
    nearby = []
    for row in events:
        if row["device_id"] == device or row["severity"] not in FAULT_LEVELS:
            continue
        times = target_times.get((row["zone"], row["code"]))
        if not times:
            continue
        instant = timestamp(row["timestamp"])
        index = bisect_left(times, instant)
        # The nearest target fault must be the immediate predecessor or successor.
        if any(abs(times[i] - instant) <= window
               for i in (index - 1, index) if 0 <= i < len(times)):
            nearby.append(row)
    return nearby


def diagnose(args):
    events = site_events(args.events, args.site, args.start, args.end)
    timeline = [row for row in events if row["device_id"] == args.device]
    faults = [row for row in timeline if row["severity"] in FAULT_LEVELS]
    nearby = find_peer_faults(events, faults, args.device, args.peer_window)
    counts = Counter(row["code"] for row in faults)
    report = {
        "device_id": args.device,
        "site": args.site,
        "start_inclusive": args.start.isoformat(),
        "end_exclusive": args.end.isoformat(),
        "event_count": len(timeline),
        "fault_count": len(faults),
        "fault_counts": dict(counts),
        "repeated_faults": {code: count for code, count in counts.items() if count > 1},
        "fault_job_ids": sorted({row["job_id"] for row in faults if row["job_id"]}),
        "peer_window_seconds": int(args.peer_window.total_seconds()),
        "peer_faults": nearby,
        "timeline": timeline,
    }
    if args.csv:
        if args.csv.resolve() == args.events.resolve() or (
            args.csv.exists() and args.csv.samefile(args.events)
        ):
            raise ValueError("--csv must point to a different file from the event export")
        with args.csv.open("x", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(EVENT_FIELDS)
            writer.writerows([spreadsheet_cell(row[field]) for field in EVENT_FIELDS]
                             for row in timeline)
    return report, 0


# Every check compares an expected set of fields against a recorded observation.
CHECKS = {
    "firmware": {
        "device_id": str, "version": str, "online_after_restart": bool, "smoke_status": str,
    },
    "registration": {
        "device_id": str, "site": (str, type(None)), "device_type": str, "result": str,
    },
    "assignment": {
        "job_id": str, "state": str, "assigned_device": (str, type(None)),
    },
    "route": {
        "job_id": str, "state": str, "assigned_device": (str, type(None)),
        "route": (str, type(None)),
    },
    "onboarding": {
        "device_id": str, "device_type": str, "visible_in_monitoring": bool,
        "telemetry_received": bool, "smoke_status": str,
    },
}


def load_cases(path, fields_key):
    with path.open(encoding="utf-8") as stream:
        document = json.load(stream)
    if not isinstance(document, dict) or not isinstance(document.get("cases"), list):
        raise ValueError(f"{path}: expected an object with a cases array")
    indexed = {}
    for index, case in enumerate(document["cases"], 1):
        label = f"{path}: case {index}"
        if not isinstance(case, dict):
            raise ValueError(f"{label} must be an object")
        case_id = case.get("id")
        kind = case.get("kind")
        fields = case.get(fields_key)
        if not isinstance(case_id, str) or not case_id.strip() or case_id in indexed:
            raise ValueError(f"{label} needs a unique, nonblank id")
        if not isinstance(kind, str) or kind not in CHECKS:
            raise ValueError(f"{label} has an unknown kind")
        if not isinstance(fields, dict):
            raise ValueError(f"{label} needs a {fields_key} object")
        for name, value in fields.items():
            field_type = CHECKS[kind].get(name)
            if field_type is None or not isinstance(value, field_type):
                raise ValueError(f"{label}: invalid field {name!r} for {kind}")
            if isinstance(value, str) and not value.strip():
                raise ValueError(f"{label}: {name!r} must be nonblank")
        if fields_key == "expected" and fields.keys() != CHECKS[kind].keys():
            missing = sorted(CHECKS[kind].keys() - fields.keys())
            raise ValueError(f"{label}: missing expected fields {', '.join(missing)}")
        indexed[case_id] = case
    return indexed


def check_observation_age(record, now, max_age):
    if "observed_at" not in record:
        return {"reason": "missing_observed_at"}
    raw = record["observed_at"]
    if not isinstance(raw, str):
        return {"reason": "invalid_observed_at"}
    try:
        observed = timestamp(raw)
    except argparse.ArgumentTypeError:
        return {"reason": "invalid_observed_at"}
    age = now - observed
    if age < -CLOCK_SKEW:
        return {"reason": "observed_in_future"}
    if age > max_age:
        return {"reason": "stale_observation", "age_seconds": age.total_seconds()}
    return None


def compare_cases(plan, evidence, max_age=None, now=None):
    if max_age is not None and now is None:
        now = datetime.now(timezone.utc)
    if not plan:
        raise ValueError("Validation plan must contain at least one case")
    results = []
    for case_id, case in plan.items():
        record = evidence.get(case_id)
        differences = []
        if record is None:
            differences.append({"reason": "missing_observation"})
        elif record["kind"] != case["kind"]:
            differences.append({"field": "kind", "expected": case["kind"],
                                "observed": record["kind"]})
        else:
            observed = record["observed"]
            for field, expected in case["expected"].items():
                if field not in observed:
                    differences.append({"field": field, "expected": expected,
                                        "reason": "missing_field"})
                elif observed[field] != expected:
                    differences.append({"field": field, "expected": expected,
                                        "observed": observed[field]})
            if max_age is not None:
                problem = check_observation_age(record, now, max_age)
                if problem is not None:
                    differences.append(problem)
        results.append({"id": case_id, "kind": case["kind"],
                        "status": "FAIL" if differences else "PASS",
                        "differences": differences})
    for case_id in sorted(evidence.keys() - plan.keys()):
        results.append({"id": case_id, "kind": evidence[case_id]["kind"],
                        "status": "FAIL", "differences": [{"reason": "unexpected_observation"}]})
    failed = sum(result["status"] == "FAIL" for result in results)
    return {"passed": len(results) - failed, "failed": failed, "cases": results}


def validate(args):
    report = compare_cases(
        load_cases(args.plan, "expected"), load_cases(args.evidence, "observed"),
        max_age=args.max_age, now=args.now,
    )
    return report, int(report["failed"] > 0)


def write_json(path, data):
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def write_events(path, events):
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=EVENT_FIELDS)
        writer.writeheader()
        writer.writerows(events)


def exercise(args):
    names = tuple(EXERCISES) if args.scenario == "all" else (args.scenario,)
    output = args.output
    output.mkdir(parents=True, exist_ok=False)
    results = []
    for name in names:
        spec = EXERCISES[name]
        run = ExerciseRun(spec)
        run.run()
        path = output / name
        path.mkdir()

        plan_path = path / "plan.json"
        evidence_path = path / "evidence.json"
        write_events(path / "events.csv", run.events)
        write_json(plan_path, {"cases": run.plan})
        write_json(evidence_path, {"cases": run.evidence})
        validation = compare_cases(load_cases(plan_path, "expected"),
                                   load_cases(evidence_path, "observed"))
        metrics = run.metrics()
        write_json(path / "validation.json", validation)
        write_json(path / "job-metrics.json", metrics)
        (path / "report.html").write_text(
            render_exercise(spec, run.events, validation, metrics, START), encoding="utf-8",
        )
        results.append({
            "scenario": name, "title": spec.title,
            "passed": validation["passed"], "failed": validation["failed"],
            "jobs_completed": sum(job["final_state"] == "completed" for job in metrics),
            "report": f"{name}/report.html",
        })
    (output / "index.html").write_text(render_exercise_index(results), encoding="utf-8")
    report = {"scenarios": results, "passed": sum(row["passed"] for row in results),
              "failed": sum(row["failed"] for row in results), "index": str(output / "index.html")}
    write_json(output / "summary.json", report)
    return report, int(report["failed"] > 0)


def simulate(args):
    """Run the simulator, then process its exports using the usual CLI logic."""
    plan = Path(__file__).resolve().parent / "demo" / "release-plan.json"
    faults_enabled = SCENARIOS[args.scenario] | frozenset(args.inject)
    fleet = SimulatedFleet(faults_enabled)
    fleet.run()
    output = args.output
    output.mkdir(parents=True, exist_ok=False)
    events = output / "events.csv"
    evidence = output / "evidence.json"

    write_events(events, fleet.events)
    write_json(evidence, {"scenario": args.scenario, "faults_injected": sorted(faults_enabled),
                          "cases": fleet.observations})
    diagnostics, _ = diagnose(argparse.Namespace(
        events=events, device=TARGET, site=SITE, start=START, end=END,
        csv=output / "incident.csv", peer_window=PEER_WINDOW,
    ))
    release = compare_cases(load_cases(plan, "expected"), load_cases(evidence, "observed"))
    window_events = site_events(events, SITE, START, END)
    faults = [event for event in window_events if event["severity"] in FAULT_LEVELS]
    window = {
        "site": SITE, "start_inclusive": START.isoformat(), "end_exclusive": END.isoformat(),
        "event_count": len(window_events), "fault_count": len(faults),
        "faults_by_zone": dict(Counter(event["zone"] for event in faults)),
        "faults_by_device": dict(Counter(event["device_id"] for event in faults)),
        "faults_by_code": dict(Counter(event["code"] for event in faults)),
    }
    write_json(output / "diagnostics.json", diagnostics)
    write_json(output / "validation.json", release)
    write_json(output / "fleet-window.json", window)
    report = output / "report.html"
    report.write_text(render(args.scenario, diagnostics, release, window, args.tz),
                      encoding="utf-8")
    return {
        "scenario": args.scenario, "faults_injected": sorted(faults_enabled),
        "output_directory": str(output), "report": str(report),
        "target_faults": diagnostics["fault_count"], "peer_faults": len(diagnostics["peer_faults"]),
        "site_faults": window["fault_count"], "passed": release["passed"], "failed": release["failed"],
    }, int(release["failed"] > 0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    diagnostics = commands.add_parser("diagnose", help="Summarize fleet events")
    diagnostics.add_argument("--events", required=True, type=Path)
    diagnostics.add_argument("--device", required=True)
    diagnostics.add_argument("--site", required=True)
    diagnostics.add_argument("--start", required=True, type=timestamp)
    diagnostics.add_argument("--end", required=True, type=timestamp)
    diagnostics.add_argument("--csv", type=Path, help="Create a timeline CSV at this path")
    diagnostics.add_argument("--peer-window", type=duration, default=PEER_WINDOW,
                             help="Maximum time gap for a peer fault (default: 15m)")
    diagnostics.set_defaults(run=diagnose)
    validation = commands.add_parser("validate", help="Compare a release plan and evidence")
    validation.add_argument("--plan", required=True, type=Path)
    validation.add_argument("--evidence", required=True, type=Path)
    validation.add_argument("--max-age", type=duration,
                            help="Maximum age of staging evidence, for example 30m")
    validation.add_argument("--now", type=timestamp,
                            help="Reference time for --max-age (default: current time)")
    validation.set_defaults(run=validate)
    simulation = commands.add_parser("simulate", help="Run the offline fleet staging demo")
    simulation.add_argument("--scenario", choices=("baseline", "regression"), default="baseline")
    simulation.add_argument("--output", required=True, type=Path, help="New directory for run outputs")
    simulation.add_argument("--inject", choices=FAULTS, action="append", default=[],
                            help="Add a named fault to the scenario (repeatable)")
    simulation.add_argument("--tz", type=reporting_zone, default=timezone.utc,
                            help="Time zone for the report, for example America/Chicago (default: UTC)")
    simulation.set_defaults(run=simulate)
    exercises = commands.add_parser("exercise", help="Run documented fleet disruption exercises")
    exercises.add_argument("--scenario", choices=("all", *EXERCISES), default="all")
    exercises.add_argument("--output", required=True, type=Path, help="New directory for exercise outputs")
    exercises.set_defaults(run=exercise)
    args = parser.parse_args()
    if args.command == "validate" and args.now is not None and args.max_age is None:
        parser.error("--now requires --max-age")
    try:
        report, status = args.run(args)
    except (OSError, ValueError, csv.Error) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, ensure_ascii=True))
    return status


if __name__ == "__main__":
    sys.exit(main())
