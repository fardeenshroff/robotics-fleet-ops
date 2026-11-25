"""Analyze exported fleet logs and compare observed results with a release plan."""

import argparse
import csv
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

from fleet_report import render
from fleet_simulator import END, SITE, START, TARGET, SimulatedFleet


EVENT_FIELDS = ("timestamp", "device_id", "site", "zone", "severity",
                "code", "job_id", "message")
FAULT_LEVELS = {"ERROR", "CRITICAL"}


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError
        return parsed.astimezone(timezone.utc)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"Expected an ISO 8601 timestamp with a UTC offset, got {value!r}"
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


def diagnose(args):
    events = site_events(args.events, args.site, args.start, args.end)
    timeline = [row for row in events if row["device_id"] == args.device]
    faults = [row for row in timeline if row["severity"] in FAULT_LEVELS]
    fault_locations = {(row["zone"], row["code"]) for row in faults}
    nearby = [row for row in events if row["device_id"] != args.device
              and row["severity"] in FAULT_LEVELS
              and (row["zone"], row["code"]) in fault_locations]
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


def validate(args):
    plan = load_cases(args.plan, "expected")
    evidence = load_cases(args.evidence, "observed")
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
        results.append({"id": case_id, "kind": case["kind"],
                        "status": "FAIL" if differences else "PASS",
                        "differences": differences})
    for case_id in sorted(evidence.keys() - plan.keys()):
        results.append({"id": case_id, "kind": evidence[case_id]["kind"],
                        "status": "FAIL", "differences": [{"reason": "unexpected_observation"}]})
    failed = sum(result["status"] == "FAIL" for result in results)
    return {"passed": len(results) - failed, "failed": failed, "cases": results}, int(failed > 0)


def simulate(args):
    """Run the simulator, then process its exports using the usual CLI logic."""
    plan = Path(__file__).resolve().parent / "demo" / "release-plan.json"
    fleet = SimulatedFleet(args.scenario)
    fleet.run()
    output = args.output
    output.mkdir(exist_ok=False)
    events = output / "events.csv"
    evidence = output / "evidence.json"

    def write_json(path, data):
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    with events.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=EVENT_FIELDS)
        writer.writeheader()
        writer.writerows(fleet.events)
    write_json(evidence, {"scenario": args.scenario, "cases": fleet.observations})
    diagnostics, _ = diagnose(argparse.Namespace(
        events=events, device=TARGET, site=SITE, start=START, end=END,
        csv=output / "incident.csv",
    ))
    release, status = validate(argparse.Namespace(plan=plan, evidence=evidence))
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
    report.write_text(render(args.scenario, diagnostics, release, window), encoding="utf-8")
    return {
        "scenario": args.scenario, "output_directory": str(output), "report": str(report),
        "target_faults": diagnostics["fault_count"], "peer_faults": len(diagnostics["peer_faults"]),
        "site_faults": window["fault_count"], "passed": release["passed"], "failed": release["failed"],
    }, status


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
    diagnostics.set_defaults(run=diagnose)
    validation = commands.add_parser("validate", help="Compare a release plan and evidence")
    validation.add_argument("--plan", required=True, type=Path)
    validation.add_argument("--evidence", required=True, type=Path)
    validation.set_defaults(run=validate)
    simulation = commands.add_parser("simulate", help="Run the offline fleet staging demo")
    simulation.add_argument("--scenario", choices=("baseline", "regression"), default="baseline")
    simulation.add_argument("--output", required=True, type=Path, help="New directory for run outputs")
    simulation.set_defaults(run=simulate)
    args = parser.parse_args()
    try:
        report, status = args.run(args)
    except (OSError, ValueError, csv.Error) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, ensure_ascii=True))
    return status


if __name__ == "__main__":
    sys.exit(main())
