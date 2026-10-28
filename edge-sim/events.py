"""Fake safety detections.

A real edge server runs computer-vision models on camera feeds. We only
simulate their *output*: small, anonymised events. No video, images or
personal identifiers ever leave the site.
"""
import random
import uuid
from datetime import datetime, timezone

ZONES = ["dock-1", "dock-2", "aisle-a", "aisle-b", "forklift-lane", "gate-north"]

# Relative frequencies: routine operational events are far more common
# than safety incidents.
WEIGHTS = {
    "label_read": 30,
    "gate_triggered": 20,
    "loading_session_completed": 10,
    "ppe_violation": 15,
    "speed_violation": 15,
    "near_miss": 10,
}


def make_event(software_version: str, config_version: int, speed_limit_kmh: float) -> dict:
    event_type = random.choices(list(WEIGHTS), weights=list(WEIGHTS.values()))[0]
    severity, details = _details(event_type, speed_limit_kmh)
    return {
        "event_id": str(uuid.uuid4()),
        "type": event_type,
        "zone": random.choice(ZONES),
        "severity": severity,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        # Audit requirement: record exactly which software and config made
        # this decision, so the event can later serve as evidence.
        "software_version": software_version,
        "config_version": config_version,
        "details": details,
    }


def _details(event_type: str, speed_limit_kmh: float) -> tuple[str, dict]:
    match event_type:
        case "near_miss":
            distance = round(random.uniform(0.3, 1.5), 2)
            return ("critical" if distance < 0.6 else "high"), {
                "min_distance_m": distance,
                "objects": ["forklift", "pedestrian"],
            }
        case "ppe_violation":
            return "medium", {"missing": random.choice(["helmet", "hi_vis_vest", "helmet+hi_vis_vest"])}
        case "speed_violation":
            speed = round(speed_limit_kmh + random.uniform(0.5, 10), 1)
            return ("high" if speed > speed_limit_kmh + 5 else "medium"), {
                "speed_kmh": speed,
                "limit_kmh": speed_limit_kmh,
            }
        case "gate_triggered":
            # The gate camera checks everyone walking through for PPE. These
            # checks are the denominator for the PPE compliance KPI.
            return "info", {"direction": random.choice(["in", "out"]), "ppe_ok": random.random() < 0.9}
        case "label_read":
            return "info", {"label": f"PAL-{random.randint(0, 999999):06d}"}
        case "loading_session_completed":
            return "info", {"duration_s": random.randint(300, 2400), "pallets": random.randint(4, 33)}
    raise ValueError(event_type)
