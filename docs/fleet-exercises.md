# Warehouse fleet disruption exercises

These offline exercises reproduce operating conditions described in fleet documentation and research. The simulator computes dispatch and completion events from robot availability, route availability, and destination telemetry. The job IDs, timings, warehouse layout, and outcomes are synthetic.

```bash
python fleet_ops.py exercise --output reports/fleet-exercises
python fleet_ops.py exercise --scenario mid-job-offline --output reports/disconnect-only
```

Open `reports/fleet-exercises/index.html` for the full suite, or open an individual scenario's `report.html`. The suite exports the same results in CSV, JSON, and Markdown. See the [recorded example](example-exercise-summary.md) for the expected durations and check counts. Each scenario exports `events.csv`, `plan.json`, `evidence.json`, `validation.json`, and `job-metrics.json`. The CLI uses all scenarios by default and returns `1` if any checkpoint fails. The output directory must be new.

| Scenario | Operating reference | Behavior checked |
| --- | --- | --- |
| `mid-job-offline` | [VDA 5050 connection reporting](https://github.com/VDA5050/VDA5050/blob/3.0.0/VDA5050_EN.md) and the [Kiva fleet case study](https://doi.org/10.1609/aimag.v29i1.2082) | Hold the disconnected robot's job until independent evidence confirms it had yet to pick up a load. Reassign it while a separate transfer continues. |
| `blocked-aisle` | [Open-RMF lane closure](https://osrf.github.io/ros2multirobotbook/integration_nav-maps-strategies.html#lane-closure) | Use an approved alternate route when available. Hold other work until its route opens. |
| `low-battery` | [MiR Fleet PC Getting Started, automatic charging settings](https://d18hjk6wpn1fl5.cloudfront.net/public/187/documents/Current-mir_fleet_pc_getting_started_1_5_en_pdf-187-50860-1.pdf) | Keep a charging robot unavailable until it crosses a separate release threshold. An eligible robot continues its assigned work. |
| `stale-workstation` | [VDA 5050 periodic robot state](https://github.com/VDA5050/VDA5050/blob/3.0.0/VDA5050_EN.md) | Apply timestamped status to a simulated workstation. Hold inbound work until its status is fresh while independent work continues. |
| `dispatch-contention` | [Gerkey and Matarić, multi-robot task allocation](https://doi.org/10.1177/0278364904045564) | Keep a robot on one job at a time. Dispatch the next eligible queued job after the robot finishes its current one. |

## How to interpret the results

- **Queue wait** is the time from job arrival to its first dispatch.
- **Reassignment time** is the time from interruption to the next dispatch of that job.
- **Time to complete** is the total elapsed time from arrival to completion.
- **Queue reasons** explain which constraint prevented dispatch, such as a closed route, stale destination status, or a busy eligible robot. The timeline records each change in reason.
- **Checkpoint outcomes** compare fixed expectations with the simulated state at specified times. To rerun validation independently, use `python fleet_ops.py validate --plan PATH/plan.json --evidence PATH/evidence.json`.

The simulator treats routes as approved zone choices and uses fixed service durations. It does not model vehicle movement, physical obstacles, stopping distances, battery chemistry, or live fleet integration. The demo station freshness limit is a local simulation policy. VDA 5050 is referenced for robot state and connection reporting. The pre-pickup retry assumes independent confirmation of the load state. Loaded or ambiguous jobs would require a different recovery exercise.
