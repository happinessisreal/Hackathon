"""Inter-zone priority ranking for all currently-CRITICAL zones. Locked
formula (CLAUDE.md):

    priority = risk_score
             + 15 * occupancy_factor          (people present -> jump the queue)
             + min(10, unacked_seconds / 15)  (escalates while ignored, cap +10)
    Tie-break: earlier CRITICAL entry first.

Occupancy is deliberately absent from a zone's own risk_score (life safety
belongs in cross-zone triage, not the per-zone hazard score) but dominates
here, alongside "how long has this been ignored" - both signals a purely
sensor-driven score can't express on its own.

Additive on top of the locked formula (Bonus 4): a decaying advisory term
from validated NL incident reports, `runtime.advisory_boost()`. It only
ever touches zones this loop already restricted to CRITICAL (via sensor
data) - it can re-order the queue, never add a zone the sensors didn't
already flag, and it never reaches risk_score/state/actuation.
"""

import datetime as dt

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import PRIORITY_OCCUPANCY_BONUS, PRIORITY_UNACKED_CAP, PRIORITY_UNACKED_DIVISOR
from backend.models import Acknowledgment, Incident, Zone
from backend.zone_manager import CRITICAL, ZoneManager


async def compute_priority_queue(db: AsyncSession, manager: ZoneManager, now: dt.datetime | None = None) -> list[dict]:
    now = now or dt.datetime.now(dt.timezone.utc)
    entries: list[dict] = []

    for zone_id, runtime in manager.all_runtimes().items():
        if runtime.current_state != CRITICAL:
            continue

        zone = await db.get(Zone, zone_id)
        if zone is None:
            continue

        # Bonus 1: PIR backed up by the camera cross-check - a fresh camera
        # detection can rescue a false "zone is empty" (dead/blocked PIR)
        # so the ranking doesn't demote an occupied zone. Ranking only;
        # the zone's own risk score still uses PIR alone (locked formula).
        occupied, occupancy_source = runtime.effective_occupied(now)
        unacked_seconds = 0.0
        if runtime.open_incident_id is not None:
            incident = await db.get(Incident, runtime.open_incident_id)
            if incident is not None:
                if incident.status == "open":
                    unacked_seconds = (now - incident.opened_at).total_seconds()
                elif incident.status == "acked":
                    ack = (
                        await db.execute(
                            select(Acknowledgment).where(Acknowledgment.incident_id == incident.id)
                        )
                    ).scalar_one_or_none()
                    # Escalation stops once acked: freeze at the ack timestamp.
                    if ack is not None:
                        unacked_seconds = (ack.ts - incident.opened_at).total_seconds()

        occ_bonus = PRIORITY_OCCUPANCY_BONUS if occupied else 0
        unacked_bonus = min(PRIORITY_UNACKED_CAP, unacked_seconds / PRIORITY_UNACKED_DIVISOR)
        advisory_bonus = runtime.advisory_boost(now)
        priority = runtime.current_risk_score + occ_bonus + unacked_bonus + advisory_bonus

        parts = [f"Risk {runtime.current_risk_score:.0f}"]
        if occupied:
            source_note = " (camera)" if occupancy_source == "camera" else ""
            parts.append(f"Occupied{source_note} +{occ_bonus}")
        if unacked_bonus > 0:
            parts.append(f"unacked {unacked_seconds:.0f}s +{unacked_bonus:.0f}")
        if advisory_bonus > 0:
            parts.append(f"NL report +{advisory_bonus:.0f}")
        justification = " + ".join(parts) + f" = {priority:.0f}"

        entries.append(
            {
                "zone_id": zone_id,
                "zone_name": zone.name,
                "risk_score": runtime.current_risk_score,
                "occupied": occupied,
                "unacked_seconds": unacked_seconds,
                "priority": priority,
                "justification": justification,
                "critical_entered_at": runtime.critical_entered_at or now,
            }
        )

    entries.sort(key=lambda e: (-e["priority"], e["critical_entered_at"]))
    for e in entries:
        e.pop("critical_entered_at", None)
    return entries
