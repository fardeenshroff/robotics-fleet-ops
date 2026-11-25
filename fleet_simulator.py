"""Deterministic fleet operations for the offline staging demo."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


SITE = "RI-01"
TARGET = "AMR-205"
START = datetime(2026, 11, 25, 14, tzinfo=timezone.utc)
END = START + timedelta(hours=1)


@dataclass
class Device:
    kind: str
    site: str
    online: bool = True
    version: str = "2.8.0"
    monitoring: bool = False
    telemetry: bool = False


class SimulatedFleet:
    def __init__(self, scenario):
        self.regression = scenario == "regression"
        self.devices = {}
        self.assignments = {}
        self.events = []
        self.observations = []

    def register(self, device_id, kind, site):
        if not site:
            result = "rejected_missing_site"
        elif device_id in self.devices:
            result = "rejected_duplicate"
        else:
            self.devices[device_id] = Device(kind, site)
            result = "accepted"
        return {"device_id": device_id, "site": site, "device_type": kind, "result": result}

    def update_firmware(self, device_id, target_version):
        device = self.devices[device_id]
        device.online = False
        if not self.regression:
            device.version = target_version
        device.online = True
        return {"device_id": device_id, "version": device.version,
                "online_after_restart": device.online, "smoke_status": self.smoke(device)}

    def dispatch(self, job_id, candidates, site):
        # The regression simulates a scheduler that uses eligibility but ignores live status.
        assigned = next((
            device_id for device_id in candidates
            if self.devices[device_id].kind == "robot"
            and self.devices[device_id].site == site
            and (self.devices[device_id].online or self.regression)
        ), None)
        self.assignments[job_id] = assigned
        return {"job_id": job_id, "state": "assigned" if assigned else "queued",
                "assigned_device": self.assignments[job_id]}

    def reassign_interrupted(self, job_id, candidates, site):
        # An interrupted robot is excluded from its own retry.
        interrupted = self.assignments[job_id]
        return self.dispatch(job_id, [device for device in candidates if device != interrupted], site)

    @staticmethod
    def smoke(device):
        if not device.online:
            return "blocked"
        return {"robot": "completed", "sensor": "sample_received",
                "workstation": "job_completed"}[device.kind]

    def onboard(self, device_id):
        device = self.devices[device_id]
        device.monitoring = device.online
        device.telemetry = device.monitoring and not (
            self.regression and device.kind == "workstation"
        )
        return {"device_id": device_id, "device_type": device.kind,
                "visible_in_monitoring": device.monitoring,
                "telemetry_received": device.telemetry, "smoke_status": self.smoke(device)}

    def log(self, minute, second, device_id, zone, severity, code, job_id, message):
        instant = START + timedelta(minutes=minute, seconds=second)
        self.events.append({
            "timestamp": instant.isoformat(), "device_id": device_id,
            "site": self.devices[device_id].site, "zone": zone, "severity": severity,
            "code": code, "job_id": job_id, "message": message,
        })

    def record(self, case_id, kind, observed):
        instant = END + timedelta(seconds=len(self.observations))
        self.observations.append({
            "id": case_id, "kind": kind, "observed": observed,
            "observed_at": instant.isoformat(), "source": "fleet-simulator",
        })

    def run(self):
        for device_id, site in ((TARGET, SITE), ("AMR-206", SITE),
                                ("AMR-207", SITE), ("AMR-208", "RI-02")):
            self.register(device_id, "robot", site)
        self.log(0, 0, TARGET, "Zone C", "INFO", "JOB_STARTED", "JOB-801", "Transfer started")
        self.log(4, 18, "AMR-206", "Zone C", "INFO", "JOB_COMPLETED", "JOB-802",
                 "Transfer completed")
        for minute, second, device, job in (
            (3, 12, TARGET, "JOB-801"), (12, 0, TARGET, "JOB-803"),
            (20, 9, "AMR-207", "JOB-804"), (33, 0, TARGET, "JOB-805"),
            (29, 0, "AMR-208", "JOB-990"),
        ):
            self.log(minute, second, device, "Zone C", "ERROR", "FRONT_SCANNER_FIELD_OCCUPIED",
                     job, "Simulated scanner fault during transfer")
        self.log(40, 50, "AMR-206", "Zone A", "ERROR", "LOCALIZATION_LOW", "JOB-809",
                 "Simulated localization fault")
        self.events.sort(key=lambda row: row["timestamp"])

        self.record("firmware-amr-205", "firmware", self.update_firmware(TARGET, "2.8.1"))
        self.record("register-sensor", "registration", self.register("SENSOR-40", "sensor", SITE))
        self.record("reject-duplicate-id", "registration", self.register("SENSOR-40", "sensor", SITE))
        self.record("reject-missing-site", "registration", self.register("WS-22", "workstation", None))
        self.register("WS-22", "workstation", SITE)

        self.record("assign-available", "assignment", self.dispatch("JOB-901", [TARGET], SITE))
        self.devices[TARGET].online = False
        self.record("queue-offline", "assignment", self.dispatch("JOB-902", [TARGET], SITE))
        self.devices[TARGET].online = True
        self.dispatch("JOB-903", [TARGET, "AMR-206"], SITE)
        interrupted = self.assignments["JOB-903"]
        self.devices[interrupted].online = False
        self.record("reassign-interrupted", "assignment",
                    self.reassign_interrupted("JOB-903", [TARGET, "AMR-206"], SITE))
        self.devices[interrupted].online = True

        self.record("onboard-robot", "onboarding", self.onboard(TARGET))
        self.record("onboard-sensor", "onboarding", self.onboard("SENSOR-40"))
        self.record("onboard-workstation", "onboarding", self.onboard("WS-22"))
