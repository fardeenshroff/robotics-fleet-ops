"""Deterministic dispatch exercises based on documented fleet operating cases."""

from dataclasses import dataclass, field
from datetime import timedelta

from fleet_simulator import SITE, START


MIN_DISPATCH_BATTERY = 30
MIN_CHARGE_RELEASE = 60
STATION_MAX_AGE = 30
ROBOTS = ("AMR-205", "AMR-206", "AMR-207")
A, B, C = ROBOTS
KIVA = "https://doi.org/10.1609/aimag.v29i1.2082"
VDA = "https://github.com/VDA5050/VDA5050/blob/3.0.0/VDA5050_EN.md"
RMF = "https://osrf.github.io/ros2multirobotbook/integration_nav-maps-strategies.html#lane-closure"
MRTA = "https://doi.org/10.1177/0278364904045564"
MIR = "https://d18hjk6wpn1fl5.cloudfront.net/public/187/documents/Current-mir_fleet_pc_getting_started_1_5_en_pdf-187-50860-1.pdf"


@dataclass(frozen=True)
class JobRequest:
    id: str
    arrival: int
    service: int
    candidates: tuple[str, ...]
    routes: tuple[str, ...]
    station: str | None = None


@dataclass
class Job:
    request: JobRequest
    state: str = "queued"
    robot: str | None = None
    route: str | None = None
    finish_at: int | None = None
    first_start: int | None = None
    interrupted_at: int | None = None
    resumed_at: int | None = None
    completed_at: int | None = None
    active_wait_reason: str | None = None
    wait_reasons: list[str] = field(default_factory=list)


@dataclass
class Robot:
    online: bool = True
    charging: bool = False
    battery: int = 80


@dataclass(frozen=True)
class Checkpoint:
    at: int
    id: str
    job: str
    state: str
    robot: str | None
    route: str | None = None


@dataclass(frozen=True)
class Exercise:
    title: str
    description: str
    sources: tuple[tuple[str, str], ...]
    jobs: tuple[JobRequest, ...]
    changes: tuple[tuple[int, str, str, int], ...]
    checks: tuple[Checkpoint, ...]


EXERCISES = {
    "mid-job-offline": Exercise(
        "Robot disconnects before pickup",
        "A robot loses its connection during an empty approach. The affected job is held "
        "until its pre-pickup state is confirmed. An independent transfer continues.",
        (("VDA 5050 connection status", VDA), ("Kiva fleet continuity case study", KIVA)),
        (JobRequest("JOB-701", 0, 90, (A, B), ("Zone C",)),
         JobRequest("JOB-702", 0, 40, (C,), ("Zone A",))),
        ((20, "offline", A, 0), (65, "confirm_retry", "JOB-701", 0)),
        (Checkpoint(25, "hold-unconfirmed-job", "JOB-701", "held", A),
         Checkpoint(45, "unrelated-job-completes", "JOB-702", "completed", C),
         Checkpoint(65, "retry-with-spare-robot", "JOB-701", "running", B),
         Checkpoint(155, "interrupted-job-completes", "JOB-701", "completed", B)),
    ),
    "blocked-aisle": Exercise(
        "Shared aisle closes during a shift",
        "The primary aisle closes. A transfer with an approved alternate route proceeds. "
        "A transfer with only the closed route waits for reopening.",
        (("Open-RMF lane closure", RMF),),
        (JobRequest("JOB-711", 0, 45, (A,), ("Zone C",)),
         JobRequest("JOB-712", 0, 55, (B,), ("Zone C", "Zone D"))),
        ((0, "close", "Zone C", 0), (75, "open", "Zone C", 0)),
        (Checkpoint(10, "hold-without-open-route", "JOB-711", "queued", None),
         Checkpoint(10, "use-approved-detour", "JOB-712", "running", B, "Zone D"),
         Checkpoint(55, "detour-job-completes", "JOB-712", "completed", B),
         Checkpoint(75, "reopened-aisle-used", "JOB-711", "running", A, "Zone C"),
         Checkpoint(120, "queued-job-completes", "JOB-711", "completed", A)),
    ),
    "low-battery": Exercise(
        "Low battery reduces dispatch capacity",
        "A low-charge robot enters charging. Reaching the mission threshold alone leaves "
        "it unavailable until the separate charger release threshold is reached.",
        (("MiR Fleet charging and mission thresholds", MIR),),
        (JobRequest("JOB-721", 0, 35, (A,), ("Zone B",)),
         JobRequest("JOB-722", 0, 50, (B,), ("Zone A",))),
        ((0, "battery", A, 22), (180, "battery", A, 38), (420, "battery", A, 61)),
        (Checkpoint(10, "low-battery-job-queues", "JOB-721", "queued", None),
         Checkpoint(50, "healthy-robot-keeps-working", "JOB-722", "completed", B),
         Checkpoint(180, "mission-threshold-alone-keeps-robot-charging", "JOB-721", "queued", None),
         Checkpoint(420, "dispatch-after-charger-release", "JOB-721", "running", A),
         Checkpoint(455, "charged-robot-job-completes", "JOB-721", "completed", A)),
    ),
    "stale-workstation": Exercise(
        "Destination telemetry becomes stale",
        "The workstation stops sending status. Its inbound transfer waits for fresh "
        "telemetry while work that does not depend on the station continues.",
        (("VDA 5050 periodic robot state as a fleet telemetry reference", VDA),),
        (JobRequest("JOB-731", 35, 50, (A,), ("Zone C",), "WS-22"),
         JobRequest("JOB-732", 35, 25, (B,), ("Zone A",))),
        ((0, "heartbeat", "WS-22", 0), (80, "heartbeat", "WS-22", 0),
         (100, "heartbeat", "WS-22", 0), (120, "heartbeat", "WS-22", 0)),
        (Checkpoint(36, "stale-station-job-queues", "JOB-731", "queued", None),
         Checkpoint(60, "independent-transfer-completes", "JOB-732", "completed", B),
         Checkpoint(80, "fresh-status-releases-job", "JOB-731", "running", A),
         Checkpoint(130, "station-job-completes", "JOB-731", "completed", A)),
    ),
    "dispatch-contention": Exercise(
        "Two transfers compete for one eligible robot",
        "Two same-time transfers need the only eligible robot. The second waits while the "
        "first runs. A third transfer assigned to another robot proceeds independently.",
        (("Gerkey and Matarić, multi-robot task allocation", MRTA),),
        (JobRequest("JOB-741", 0, 50, (A,), ("Zone B",)),
         JobRequest("JOB-742", 0, 60, (A,), ("Zone C",)),
         JobRequest("JOB-743", 0, 25, (B,), ("Zone A",))),
        (),
        (Checkpoint(5, "first-transfer-gets-eligible-robot", "JOB-741", "running", A),
         Checkpoint(5, "no-double-assignment", "JOB-742", "queued", None),
         Checkpoint(25, "independent-transfer-completes", "JOB-743", "completed", B),
         Checkpoint(50, "waiting-job-dispatched-after-completion", "JOB-742", "running", A),
         Checkpoint(110, "waiting-job-completes", "JOB-742", "completed", A)),
    ),
}


class ExerciseRun:
    def __init__(self, spec):
        self.spec = spec
        self.robots = {robot: Robot() for robot in ROBOTS}
        self.jobs = {}
        self.closed = set()
        self.last_seen = {}
        self.events = []
        self.evidence = []
        self.plan = []

    def log(self, now, device, zone, severity, code, job="", detail=""):
        self.events.append({
            "timestamp": (START + timedelta(seconds=now)).isoformat(), "device_id": device,
            "site": SITE, "zone": zone, "severity": severity, "code": code,
            "job_id": job, "message": detail,
        })

    def apply_change(self, now, action, subject, value):
        if action == "offline":
            self.robots[subject].online = False
            self.log(now, subject, "Fleet", "ERROR", "CONNECTION_LOST")
            for job in self.jobs.values():
                if job.robot == subject and job.state == "running":
                    job.state = "held"
                    job.finish_at = None
                    job.interrupted_at = now
                    self.log(now, subject, job.route, "WARN", "JOB_HELD", job.request.id,
                             "Waiting for independent confirmation that the load was not picked")
        elif action == "confirm_retry":
            job = self.jobs[subject]
            if job.state != "held":
                raise ValueError(f"Retry confirmation requires a held job: {subject}")
            self.log(now, job.robot, job.route, "INFO", "PRE_PICKUP_CONFIRMED", subject,
                     "Simulated recovery confirmation authorized a retry")
            job.state, job.robot, job.route = "queued", None, None
        elif action == "close":
            self.closed.add(subject)
            self.log(now, "FLEET-CONTROL", subject, "WARN", "AISLE_CLOSED")
        elif action == "open":
            self.closed.remove(subject)
            self.log(now, "FLEET-CONTROL", subject, "INFO", "AISLE_OPEN")
        elif action == "battery":
            robot = self.robots[subject]
            was_charging = robot.charging
            robot.battery = value
            robot.charging = value < (MIN_CHARGE_RELEASE if was_charging else MIN_DISPATCH_BATTERY)
            code = "CHARGE_RELEASED" if was_charging and not robot.charging else "BATTERY_UPDATED"
            if robot.charging and not was_charging:
                code = "CHARGING_STARTED"
            self.log(now, subject, "Charging", "INFO", code, detail=f"Battery at {value}%")
        elif action == "heartbeat":
            self.last_seen[subject] = now
            self.log(now, subject, "Zone C", "INFO", "STATION_STATUS_RECEIVED")

    def wait(self, now, job, reason):
        if job.active_wait_reason == reason:
            return
        job.active_wait_reason = reason
        if reason not in job.wait_reasons:
            job.wait_reasons.append(reason)
        self.log(now, "FLEET-CONTROL", job.request.routes[0], "INFO", "JOB_WAITING",
                 job.request.id, reason.replace("_", " ").capitalize())

    def dispatch(self, now, job, busy):
        request = job.request
        if request.station is not None:
            last_seen = self.last_seen.get(request.station)
            if last_seen is None or now - last_seen > STATION_MAX_AGE:
                self.wait(now, job, "destination_status_stale")
                return
        route = next((zone for zone in request.routes if zone not in self.closed), None)
        if route is None:
            self.wait(now, job, "no_open_approved_route")
            return
        eligible = [
            candidate for candidate in request.candidates
            if self.robots[candidate].online and not self.robots[candidate].charging
            and self.robots[candidate].battery >= MIN_DISPATCH_BATTERY
        ]
        robot = next((candidate for candidate in eligible if candidate not in busy), None)
        if robot is None:
            reason = "no_ready_robot"
            if eligible:
                reason = "eligible_robots_busy"
            elif any(self.robots[candidate].online and self.robots[candidate].charging
                     for candidate in request.candidates):
                reason = "waiting_for_charger_release"
            self.wait(now, job, reason)
            return
        job.active_wait_reason = None
        job.state, job.robot, job.route = "running", robot, route
        job.finish_at = now + request.service
        if job.first_start is None:
            job.first_start = now
            code = "JOB_DISPATCHED"
        else:
            job.resumed_at = now
            code = "JOB_REASSIGNED"
        busy.add(robot)
        self.log(now, robot, route, "INFO", code, request.id,
                 "Route " + route + (" selected as alternate" if route != request.routes[0] else ""))

    def observe(self, now, check):
        job = self.jobs[check.job]
        kind = "route" if check.route is not None else "assignment"
        expected = {"job_id": check.job, "state": check.state, "assigned_device": check.robot}
        observed = {"job_id": job.request.id, "state": job.state, "assigned_device": job.robot}
        if check.route is not None:
            expected["route"] = check.route
            observed["route"] = job.route
        self.plan.append({"id": check.id, "kind": kind, "expected": expected})
        self.evidence.append({
            "id": check.id, "kind": kind, "observed": observed,
            "observed_at": (START + timedelta(seconds=now)).isoformat(),
            "source": "fleet-exercise",
        })

    def run(self):
        end = max(check.at for check in self.spec.checks)
        for now in range(end + 1):
            for at, action, subject, value in self.spec.changes:
                if at == now:
                    self.apply_change(now, action, subject, value)
            for station, last_seen in self.last_seen.items():
                if now - last_seen == STATION_MAX_AGE + 1:
                    self.log(now, station, "Zone C", "WARN", "STATION_STATUS_STALE")
            for request in self.spec.jobs:
                if request.arrival == now:
                    self.jobs[request.id] = Job(request)
                    self.log(now, "FLEET-CONTROL", request.routes[0], "INFO", "JOB_QUEUED", request.id)
            for job in self.jobs.values():
                if job.finish_at == now:
                    job.state, job.completed_at, job.finish_at = "completed", now, None
                    self.log(now, job.robot, job.route, "INFO", "JOB_COMPLETED", job.request.id)
            busy = {job.robot for job in self.jobs.values() if job.state == "running"}
            for job in self.jobs.values():
                if job.state == "queued":
                    self.dispatch(now, job, busy)
            for check in self.spec.checks:
                if check.at == now:
                    self.observe(now, check)

    def metrics(self):
        return [{
            "job_id": job.request.id, "final_state": job.state,
            "assigned_device": job.robot, "route": job.route, "wait_reasons": job.wait_reasons,
            "queue_seconds": (job.first_start - job.request.arrival
                              if job.first_start is not None else None),
            "reassignment_seconds": (job.resumed_at - job.interrupted_at
                                     if job.resumed_at is not None else None),
            "elapsed_seconds": (job.completed_at - job.request.arrival
                                if job.completed_at is not None else None),
        } for job in self.jobs.values()]
