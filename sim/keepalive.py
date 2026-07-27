"""Hold one or more zones in a live, quiet SAFE state.

Purpose: the dashboard's idle shot needs every zone online, but a zone with
no node attached correctly reports OFFLINE (never a false SAFE - CLAUDE.md
rule 5). When only some zones run as Wokwi nodes, this feeds the rest with
neutral readings so the grid shows the true steady state of the system
rather than gaps caused by unstarted simulators.

It sends the same protocol as firmware/zone_node.ino and sim/node.py - raw
normalized values only, never a state or score. All fusion, state and
actuation decisions stay on the backend (rule 1).

Usage:
    # keep Server Room + Data Science Lab alive while IoT Lab runs on Wokwi
    python sim/keepalive.py "Server Room" "Data Science Lab"

    # every zone (no Wokwi at all)
    python sim/keepalive.py --all

Ctrl-C to stop. Zones then transition to OFFLINE after
OFFLINE_AFTER_SECONDS, which is correct behaviour, not a fault.
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sim.common import load_zones  # noqa: E402
from sim.node import ZoneNode  # noqa: E402

INTERVAL = 0.75  # matches POST_INTERVAL_MS in the firmware


async def hold(node: ZoneNode, gas: bool, critical: bool = False) -> None:
    """Post the same readings forever so a zone sits in a stable, filmable state.

    critical=False: neutral. Gas idles at a plausible clean-air baseline rather
    than a flat 0.0, so the card reads realistic instead of implausibly perfect.

    critical=True: sustained fire + flood + occupancy, which the backend fuses
    to 40+25+10 = 75 (plus gas where fitted). Driven through real readings
    rather than an admin override on purpose - an override pins the state but
    leaves risk_score at its last sensor value, so the card can end up showing
    "SAFE" beside a risk of 68, contradicting the documented bands on camera.
    Sensor-driven CRITICAL keeps state and score coherent, and the transition
    is logged with cause='sensor'.

    Fire needs 5 consecutive readings to clear debounce (~3.75s at 750ms) and
    PIR needs 1.5s of hold, so give it ~5s to settle before filming.
    """
    node.gas_connected = gas
    if critical:
        node.fire = 1
        node.water_norm = 1.0
        node.occupancy = 1
        if gas:
            node.gas_norm = 0.8
    else:
        node.fire = 0
        node.water_norm = 0.0
        node.occupancy = 0
        if gas:
            node.gas_norm = 0.04

    label = "CRITICAL" if critical else "SAFE"
    sent = 0
    while True:
        await node.send_reading()
        sent += 1
        if sent % 20 == 0:
            print(f"  [{node.name}] {sent} readings sent, holding {label}", flush=True)
        await asyncio.sleep(INTERVAL)


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("zones", nargs="*", help="zone names to hold (exact, quoted)")
    ap.add_argument("--all", action="store_true", help="hold every zone in the DB")
    ap.add_argument(
        "--critical", nargs="*", default=[], metavar="ZONE",
        help="hold these zones in a sensor-driven CRITICAL (stages the priority queue for filming)",
    )
    ap.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = ap.parse_args()

    available = await load_zones()
    if not available:
        raise SystemExit("No zones found - run scripts/init_db.py first.")

    names = list(available) if args.all else args.zones
    # A zone named only under --critical should still be held.
    for extra in args.critical:
        if extra not in names:
            names.append(extra)
    if not names:
        raise SystemExit(f"Name at least one zone, or pass --all. Available: {', '.join(available)}")

    unknown = [n for n in names + args.critical if n not in available]
    if unknown:
        raise SystemExit(f"Unknown zone(s): {unknown}. Available: {', '.join(available)}")

    nodes = []
    tasks = []
    for name in names:
        z = available[name]
        node = ZoneNode(name, z["id"], z["api_key"], args.base_url)
        nodes.append(node)
        # Only IoT Lab has an MQ-2; reporting gas for a zone that has no gas
        # sensor would contradict the seeded sensor rows.
        tasks.append(
            asyncio.create_task(
                hold(node, gas=(name == "IoT Lab"), critical=(name in args.critical))
            )
        )

    crit = [n for n in names if n in args.critical]
    safe = [n for n in names if n not in args.critical]
    print(f"holding {len(names)} zone(s) at {INTERVAL * 1000:.0f}ms")
    if safe:
        print(f"  SAFE     : {', '.join(safe)}")
    if crit:
        print(f"  CRITICAL : {', '.join(crit)}  (allow ~5s for debounce + PIR hold)")
    print("Ctrl-C to stop\n")
    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        pass
    finally:
        for node in nodes:
            await node.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nstopped - zones will go OFFLINE shortly (expected)")
