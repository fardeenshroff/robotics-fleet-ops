"""Render the latest simulated diagnostics and release results as standalone HTML."""

from collections import Counter
from datetime import datetime, timedelta, timezone
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


def incident_chart(timeline, target, start, end):
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
    ticks = "".join(f"<span>{start + (end - start) * (step / 6):%H:%M}</span>" for step in range(7))
    axis = f'<div class="axis-row"><span></span><div>{ticks}</div></div>'
    return f'<div aria-hidden="true">{"".join(rows)}{axis}</div>'


def describe_difference(diff):
    if "reason" in diff:
        return " ".join(part for part in (
            diff.get("field"), diff["reason"].replace("_", " "),
        ) if part)
    return f"{diff['field']}: {diff['expected']!r} → {diff['observed']!r}"


def render(scenario, diagnostics, release, shift):
    labels = {"firmware": "Firmware", "registration": "Registration",
              "assignment": "Job assignment", "onboarding": "Onboarding"}
    statuses = Counter((case["kind"], case["status"]) for case in release["cases"])
    totals = [statuses[kind, "PASS"] + statuses[kind, "FAIL"] for kind in labels]
    release_chart = "".join(
        bar(label, [("passed", statuses[kind, "PASS"], "pass"),
                    ("failed", statuses[kind, "FAIL"], "fail")], max(1, *totals))
        for kind, label in labels.items()
    )
    fault_chart = "".join(
        bar(zone, [("faults", count, "count")], shift["fault_count"])
        for zone, count in shift["faults_by_zone"].items()
    )
    central = timezone(timedelta(hours=-6))
    start = datetime.fromisoformat(shift["start_inclusive"]).astimezone(central)
    end = datetime.fromisoformat(shift["end_exclusive"]).astimezone(central)
    timeline = sorted(diagnostics["timeline"] + diagnostics["peer_faults"],
                      key=lambda event: datetime.fromisoformat(event["timestamp"]))
    incident = incident_chart(timeline, diagnostics["device_id"], start, end)
    events = table(("CST", "Device", "Zone", "Event", "Job"), (
        (datetime.fromisoformat(row["timestamp"]).astimezone(central).strftime("%H:%M:%S"),
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
<section><h2>Fleet faults by zone</h2><p>{shift['event_count']} events and {shift['fault_count']} faults at this site on {start:%B %d, %Y}, from {start:%H:%M} to {end:%H:%M} CST.</p>{fault_chart}</section>
<section><h2>Diagnostic timeline</h2><p>{escape(diagnostics['device_id'])} events and matching same-zone faults on peer robots. Red diamonds mark faults. Blue circles mark information events.</p>{incident}{events}</section>
<section><h2>Release validation by area</h2>{release_chart}</section>
<section><h2>Case results</h2>{results}</section></main></body></html>
"""
