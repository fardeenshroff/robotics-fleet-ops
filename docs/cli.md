# Robotics Fleet Ops CLI reference

Run these commands from the project root with Python 3.10 or newer. Use a fresh directory for generated runs and a fresh filename for a diagnostic CSV.

## `exercise`

```bash
python fleet_ops.py exercise --output reports/fleet-exercises
python fleet_ops.py exercise --scenario blocked-aisle --output reports/aisle-only
```

The default runs all five exercises. The [scenario guide](fleet-exercises.md) lists each case, its source, and the simulated policy. Open the suite's `index.html` to compare results and navigate to individual reports.

| File | Contents |
| --- | --- |
| `summary.csv`, `summary.json`, `summary.md` | Case counts, completed and submitted jobs, and longest queue and reassignment times |
| `SCENARIO/events.csv` | Timestamped events, including changes in why a queued job is waiting |
| `SCENARIO/plan.json`, `SCENARIO/evidence.json` | Expected checkpoint state and independently recorded observations |
| `SCENARIO/validation.json` | Per-case results and field differences |
| `SCENARIO/job-metrics.json` | Per-job queue, reassignment, and completion times plus queue reasons |
| `SCENARIO/report.html` | Timing charts, checkpoint results, and event timeline |

For a job that does not start or finish, unavailable times are represented as `null` in JSON. A reassignment time applies only when the job was interrupted and dispatched again.

## `simulate`

```bash
python fleet_ops.py simulate --scenario baseline --output reports/baseline
python fleet_ops.py simulate --scenario regression --output reports/regression
python fleet_ops.py simulate --inject firmware_not_applied --tz America/Chicago --output reports/firmware-only
```

The default baseline validates all ten cases. The regression scenario enables all three faults. Repeat `--inject` to select more than one of `firmware_not_applied`, `dispatch_ignores_status`, and `workstation_no_telemetry`. These flags add to the selected scenario.

The report time zone defaults to UTC. Named IANA zones require system time zone data or the optional `tzdata` package. Timeline spacing uses elapsed time through daylight-saving transitions.

The run writes `events.csv`, `evidence.json`, `diagnostics.json`, `incident.csv`, `fleet-window.json`, `validation.json`, and `report.html`. The regression command writes the files before returning `1`.

## `diagnose`

```bash
python fleet_ops.py diagnose --events demo/events.csv --device AMR-205 --site RI-01 --start 2026-11-25T14:00:00Z --end 2026-11-25T15:00:00Z --peer-window 15m --csv incident.csv
```

Use the columns and order in the [sample CSV](../demo/events.csv). `job_id` and `message` may be blank. Use ISO 8601 timestamps with an offset. The start is inclusive and the end is exclusive. `ERROR` and `CRITICAL` rows count as faults. A peer match uses the same site, zone, and fault code and occurs within the configured gap of a target fault. The default gap is 15 minutes.

The JSON output includes the matching peer events. The optional CSV contains the target device’s timeline.

## `validate`

```bash
python fleet_ops.py validate --plan demo/release-plan.json --evidence demo/release-pass.json
python fleet_ops.py validate --plan reports/fleet-exercises/blocked-aisle/plan.json --evidence reports/fleet-exercises/blocked-aisle/evidence.json
python fleet_ops.py validate --plan demo/release-plan.json --evidence reports/baseline/evidence.json --max-age 10m --now 2026-11-25T15:05:00Z
```

Use the [sample release plan](../demo/release-plan.json) for expected fields and the [sample evidence file](../demo/release-pass.json) for observations. Cases match by unique ID and kind. Use JSON booleans for connection or telemetry state and `null` for an unassigned device or omitted site. The exercise suite also emits route checks. The comparison uses exact values. Missing cases, missing fields, unexpected observations, and mismatched fields fail validation.

When enabled, `--max-age` rejects stale evidence, missing or invalid `observed_at` timestamps, and timestamps more than one minute ahead of the reference time. `--now` lets you replay a deterministic historical run. Otherwise the validator uses the current time. Freshness is optional.

| Exit code | Meaning |
| --- | --- |
| `0` | The command completed and all applicable checks passed |
| `1` | One or more checks failed |
| `2` | Invalid arguments or input, or a file operation failed |
