"""TC19: seeds 10k+ reading rows (plus a realistic spread of incidents) so
the incidents/readings queries can be checked against real volume, not an
empty dev DB. Exercises exactly the indexes docs will cite:
idx_readings_zone_ts and idx_incidents_status_created.

Bulk-inserts via SQLAlchemy core (not one ORM object per row) since 10k+
individual session.add() calls would itself become the bottleneck being
measured. Bypasses the ingestion pipeline entirely - this is synthetic
history, not live sensor traffic - so no fusion/state-machine side effects.

Usage:
    python sim/seed.py [--readings 10000] [--incidents 300]
"""

import argparse
import asyncio
import datetime as dt
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import insert, select  # noqa: E402

from backend.database import async_session_maker  # noqa: E402
from backend.models import Incident, Reading, Sensor, Zone  # noqa: E402


async def seed(total_readings: int, total_incidents: int) -> None:
    async with async_session_maker() as db:
        zones = (await db.execute(select(Zone))).scalars().all()
        if not zones:
            print("No zones found - run scripts/init_db.py first.")
            return

        zone_gas = {}
        for zone in zones:
            sensors = (await db.execute(select(Sensor).where(Sensor.zone_id == zone.id))).scalars().all()
            zone_gas[zone.id] = any(s.type == "gas" for s in sensors)

        now = dt.datetime.now(dt.timezone.utc)
        # Unique per run so re-running doesn't collide with (zone_id, seq).
        base_seq = int(time.time()) * 1000

        t0 = time.perf_counter()
        per_zone = total_readings // len(zones)
        rows = []
        for zone in zones:
            has_gas = zone_gas[zone.id]
            for i in range(per_zone):
                ts = now - dt.timedelta(seconds=(per_zone - i) * 0.75)
                rows.append(
                    {
                        "zone_id": zone.id,
                        "seq": base_seq + i,
                        "fire": 0,
                        "gas_norm": round(random.uniform(0.0, 0.3), 4) if has_gas else None,
                        "water_norm": round(random.uniform(0.0, 0.3), 4),
                        "occupancy": random.choice([0, 0, 0, 1]),
                        "ts_device": ts,
                        "ts_server": ts,
                        "anomaly": False,
                    }
                )

        for start in range(0, len(rows), 2000):
            await db.execute(insert(Reading), rows[start : start + 2000])
        await db.commit()
        readings_elapsed = time.perf_counter() - t0

        t1 = time.perf_counter()
        incident_rows = []
        for _ in range(total_incidents):
            zone = random.choice(zones)
            opened = now - dt.timedelta(days=random.uniform(0, 90))
            status = random.choices(["resolved", "acked", "open"], weights=[0.8, 0.1, 0.1])[0]
            resolved = opened + dt.timedelta(minutes=random.uniform(1, 30)) if status == "resolved" else None
            incident_rows.append(
                {
                    "zone_id": zone.id,
                    "opened_at": opened,
                    "peak_risk": round(random.uniform(65, 100), 2),
                    "status": status,
                    "resolved_at": resolved,
                }
            )
        for start in range(0, len(incident_rows), 2000):
            await db.execute(insert(Incident), incident_rows[start : start + 2000])
        await db.commit()
        incidents_elapsed = time.perf_counter() - t1

        print(f"Inserted {len(rows)} readings in {readings_elapsed:.2f}s across {len(zones)} zone(s).")
        print(f"Inserted {len(incident_rows)} incidents in {incidents_elapsed:.2f}s.")

        # TC19: the query the (status, opened_at) index exists for.
        t2 = time.perf_counter()
        recent_open = (
            await db.execute(
                select(Incident)
                .where(Incident.status == "open")
                .order_by(Incident.opened_at.desc())
                .limit(50)
            )
        ).scalars().all()
        query_elapsed = time.perf_counter() - t2
        print(f"Indexed query (status='open' ORDER BY opened_at DESC LIMIT 50) took {query_elapsed*1000:.1f}ms, {len(recent_open)} rows.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--readings", type=int, default=10_000)
    parser.add_argument("--incidents", type=int, default=300)
    args = parser.parse_args()
    asyncio.run(seed(args.readings, args.incidents))


if __name__ == "__main__":
    main()
