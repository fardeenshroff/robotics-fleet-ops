"""Render the latest simulated diagnostics and release results as standalone HTML."""

from collections import Counter
from datetime import datetime, timezone
from html import escape


STYLES = """
:root{color-scheme:light;--ink:#142641;--muted:#485d77;--line:#d9e3ee;
      --teal:#126c61;--red:#a33134;--blue:#2e5b9a}
*{box-sizing:border-box}body{margin:0;background:#f1f5fa;color:var(--ink);
  font:16px/1.55 system-ui,sans-serif}main{max-width:1060px;margin:auto;padding:46px 22px 55px}
header{margin-bottom:22px}h1{font-size:clamp(29px,5vw,45px);line-height:1.1;margin:0 0 10px}
h2{font-size:21px;margin:0 0 8px}p{margin:7px 0 12px}.meta{color:var(--muted)}
section{margin:16px 0;padding:20px;background:#fff;border:1px solid var(--line);border-radius:12px}
.summary{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px}
.summary div{padding:11px 14px;background:#edf3f9;border-radius:8px}.summary strong{display:block;font-size:25px}
.chart-row{display:grid;grid-template-columns:125px minmax(150px,1fr) minmax(90px,auto);
  gap:10px;align-items:center;margin:8px 0}
.track{display:flex;background:#edf3f9;border-radius:4px;height:22px;overflow:hidden}
.pass,.fail,.count{display:block;height:100%}.pass{background:var(--teal)}
.fail{background:var(--red)}.count{background:var(--blue)}
.timeline-row,.axis-row{display:grid;grid-template-columns:86px minmax(0,1fr);
  gap:10px;align-items:center;margin:9px 0}
.timeline-track{position:relative;height:24px;border-bottom:1px solid #b5c4d5;
  background:repeating-linear-gradient(to right, #e5edf5 0 1px, transparent 1px 16.6667%);
  margin:0 8px}
.marker{position:absolute;top:5px;width:13px;height:13px;border:2px solid #fff;
  border-radius:50%;background:var(--blue);transform:translateX(-50%);box-shadow:0 0 0 1px #aebed0}
.marker.error,.marker.critical{background:var(--red);border-radius:2px;transform:translateX(-50%) rotate(45deg)}
.axis-row div{display:flex;justify-content:space-between;font-size:.78rem;color:var(--muted)}
table{border-collapse:collapse;width:100%;font-size:.91rem}
td,th{text-align:left;vertical-align:top;border-bottom:1px solid var(--line);padding:8px 10px}
th{background:#edf3f9}.table{overflow:auto}code{font-family:ui-monospace,monospace}
@media(max-width:560px){.chart-row{grid-template-columns:1fr}.chart-row{gap:3px;margin-bottom:12px}}
"""


def table(headers, rows):
    headings = "".join(f'<th scope="col">{escape(label)}</th>' for label in headers)
    body = "".join("<tr>" + "".join(f"<td>{escape(str(value))}</td>" for value in row) + "</tr>"
                   for row in rows)
    return f'<div class="table"><table><thead><tr>{headings}</tr></thead><tbody>{body}</tbody></table></div>'


def bar(label, segments, scale):
    summary = " / ".join(f"{count} {name}" for name, count, _ in segments)
    widths = "".join(f'<span class="{style}" style="width:{100 * count / scale:.3f}%"></span>'
                     for _, count, style in segments)
    return (f'<div class="chart-row"><strong>{escape(label)}</strong>'
            f'<div class="track" aria-hidden="true">{widths}</div><span>{summary}</span></div>')


def incident_chart(timeline, target, start, end, zone):
    start = start.astimezone(timezone.utc)
    end = end.astimezone(timezone.utc)
    lanes = sorted({event["device_id"] for event in timeline} - {target})
    duration = (end - start).total_seconds()
    rows = []
    for device in [target, *lanes]:
        markers = []
        for event in timeline:
            if event["device_id"] == device:
                instant = datetime.fromisoformat(event["timestamp"])
                position = 100 * (instant - start).total_seconds() / duration
                severity = event["severity"].lower()
                detail = f"{event['code']} · {event['job_id']}"
                markers.append(
                    f'<span class="marker {escape(severity, quote=True)}" '
                    f'style="left:{position:.4f}%" title="{escape(detail, quote=True)}"></span>'
                )
        rows.append(f'<div class="timeline-row"><strong>{escape(device)}</strong>'
                    f'<div class="timeline-track">{"".join(markers)}</div></div>')
    tick_times = [(start + (end - start) * (step / 6)).astimezone(zone) for step in range(7)]
    tick_format = "%H:%M %Z" if len({tick.utcoffset() for tick in tick_times}) > 1 else "%H:%M"
    ticks = "".join(f"<span>{escape(tick.strftime(tick_format))}</span>" for tick in tick_times)
    axis = f'<div class="axis-row"><span></span><div>{ticks}</div></div>'
    return f'<div aria-hidden="true">{"".join(rows)}{axis}</div>'


def describe_difference(diff):
    if "reason" in diff:
        return " ".join(part for part in (
            diff.get("field"), diff["reason"].replace("_", " "),
        ) if part)
    return f"{diff['field']}: {diff['expected']!r} → {diff['observed']!r}"


def render(scenario, diagnostics, release, shift, zone=timezone.utc):
    labels = {"firmware": "Firmware", "registration": "Registration",
              "assignment": "Job assignment", "onboarding": "Onboarding", "route": "Route"}
    statuses = Counter((case["kind"], case["status"]) for case in release["cases"])
    totals = [statuses[kind, "PASS"] + statuses[kind, "FAIL"] for kind in labels]
    release_chart = "".join(
        bar(label, [("passed", statuses[kind, "PASS"], "pass"),
                    ("failed", statuses[kind, "FAIL"], "fail")], max(1, *totals))
        for kind, label in labels.items()
        if statuses[kind, "PASS"] or statuses[kind, "FAIL"]
    )
    fault_chart = "".join(
        bar(zone, [("faults", count, "count")], shift["fault_count"])
        for zone, count in shift["faults_by_zone"].items()
    )
    start = datetime.fromisoformat(shift["start_inclusive"]).astimezone(zone)
    end = datetime.fromisoformat(shift["end_exclusive"]).astimezone(zone)
    timeline = sorted(diagnostics["timeline"] + diagnostics["peer_faults"],
                      key=lambda event: datetime.fromisoformat(event["timestamp"]))
    incident = incident_chart(timeline, diagnostics["device_id"], start, end, zone)
    events = table(("Local time", "Device", "Zone", "Event", "Job"), (
        (datetime.fromisoformat(row["timestamp"]).astimezone(zone).strftime("%H:%M:%S %Z"),
         row["device_id"], row["zone"], row["code"], row["job_id"] or "—")
        for row in timeline
    ))
    results = table(("Check", "Area", "Result", "Expected → observed"), (
        (case["id"], labels[case["kind"]], case["status"],
         " | ".join(describe_difference(diff) for diff in case["differences"]) or "All fields matched")
        for case in release["cases"]
    ))
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Robotics Fleet Ops | {escape(scenario.title())} run</title><style>{STYLES}</style></head>
<body><main><header><h1>Robotics Fleet Ops</h1>
<p class="meta">{escape(scenario.title())} simulation · {escape(diagnostics['site'])} · Synthetic fleet data</p></header>
<section class="summary" aria-label="Run summary">
<div><strong>{diagnostics['fault_count']}</strong>Target robot faults</div>
<div><strong>{len(diagnostics['peer_faults'])}</strong>Matching peer events</div>
<div><strong>{release['passed']}</strong>Checks passed</div>
<div><strong>{release['failed']}</strong>Checks failed</div></section>
<section><h2>Fleet faults by zone</h2><p>{shift['event_count']} events and {shift['fault_count']} faults at this site from {escape(start.strftime("%b %d, %Y %H:%M %Z"))} to {escape(end.strftime("%b %d, %Y %H:%M %Z"))}.</p>{fault_chart}</section>
<section><h2>Diagnostic timeline</h2><p>{escape(diagnostics['device_id'])} events and same-code faults in the same zone within {diagnostics["peer_window_seconds"] / 60:g} minutes of a target fault. Red diamonds mark faults. Blue circles mark information events.</p>{incident}{events}</section>
<section><h2>Release validation by area</h2>{release_chart}</section>
<section><h2>Case results</h2>{results}</section></main></body></html>
"""


def exercise_page(title, description, sections):
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)} | Robotics Fleet Ops</title><style>{STYLES}</style></head>
<body><main><header><h1>{escape(title)}</h1><p class="meta">{escape(description)}</p></header>
{sections}</main></body></html>
"""


def render_exercise_index(results):
    return exercise_page("Fleet disruption exercises", "Synthetic warehouse operations replay", "".join(
        f'<section><h2><a href="{escape(row["report"], quote=True)}">{escape(row["title"])}</a></h2>'
        f'<p>{row["passed"]} checks passed, {row["failed"]} failed. '
        f'{row["jobs_completed"]} jobs completed.</p></section>'
        for row in results
    ))


def render_exercise(spec, events, validation, metrics, start):
    times = table(("Elapsed seconds", "Device", "Zone", "Event", "Job", "Detail"), (
        (int((datetime.fromisoformat(row["timestamp"]) - start).total_seconds()), row["device_id"],
         row["zone"], row["code"], row["job_id"] or "—", row["message"] or "—")
        for row in events
    ))
    jobs = table(("Job", "Final state", "Robot", "Route", "Queue wait (s)",
                  "Reassignment (s)", "Time to complete (s)"), (
        (job["job_id"], job["final_state"], job["assigned_device"] or "—", job["route"] or "—",
         *[job[key] if job[key] is not None else "—"
           for key in ("queue_seconds", "reassignment_seconds", "elapsed_seconds")])
        for job in metrics
    ))
    completed = [job for job in metrics if job["elapsed_seconds"] is not None]
    scale = max([1, *(job["elapsed_seconds"] for job in completed)])
    chart = "".join(bar(job["job_id"], [("s", job["elapsed_seconds"], "count")], scale)
                    for job in completed)
    checks = table(("Check", "Result", "Expected → observed"), (
        (case["id"], case["status"],
         " | ".join(describe_difference(diff) for diff in case["differences"]) or "All fields matched")
        for case in validation["cases"]
    ))
    sources = "".join(f'<li><a href="{escape(url, quote=True)}">{escape(label)}</a></li>'
                      for label, url in spec.sources)
    return exercise_page(spec.title, spec.description, f"""
<section><h2>Job timing</h2><p>Bars show the time from job arrival to completion.</p>{chart}{jobs}</section>
<section><h2>Release checks</h2><p>{validation["passed"]} passed, {validation["failed"]} failed.</p>{checks}</section>
<section><h2>Event timeline</h2>{times}</section>
<section><h2>Source references</h2><p>These references inform the operating conditions. The event
times, job IDs, policies, and outcomes are synthetic.</p><ul>{sources}</ul></section>
""")
