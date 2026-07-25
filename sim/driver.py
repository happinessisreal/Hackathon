"""SCS-RG scenario driver - the video engine. Runs named scenario groups
against a live backend (default http://127.0.0.1:8000) over real HTTP/WS,
same as CLAUDE.md's driver spec. Every scenario is idempotent/re-runnable -
safe to re-run for retakes.

Usage:
    python sim/driver.py                      # run every scenario group
    python sim/driver.py tc1 tc22             # run just these groups
    python sim/driver.py --phantom 30         # TC11a load test only
    python sim/driver.py --base-url http://127.0.0.1:8001 tc7

Requires: scripts/init_db.py already run against the target DB (zones +
staff1/admin1 users must exist), and the backend already running at
--base-url.
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from sim.common import get_token, load_zones  # noqa: E402
from sim.scenarios import (  # noqa: E402
    DriverContext,
    tc1_flame,
    tc2_gas,
    tc3_water,
    tc4_pir,
    tc5_actuation,
    tc6_protocol,
    tc7_incidents,
    tc11_phantom_load,
    tc18_concurrent_writes,
    tc22_finale,
    tc23_edge_cases,
)

SCENARIOS = {
    "tc1": tc1_flame,
    "tc2": tc2_gas,
    "tc3": tc3_water,
    "tc4": tc4_pir,
    "tc5": tc5_actuation,
    "tc6": tc6_protocol,
    "tc7": tc7_incidents,
    "tc18": tc18_concurrent_writes,
    "tc22": tc22_finale,
    "tc23": tc23_edge_cases,
}


async def build_context(base_url: str) -> DriverContext:
    zones = await load_zones()
    if not zones:
        raise SystemExit(f"No zones found via DB at this env's DATABASE_URL - run scripts/init_db.py first.")

    client = httpx.AsyncClient(timeout=10.0)
    try:
        staff = await get_token(client, base_url, "staff1", "staff123")
        admin = await get_token(client, base_url, "admin1", "admin123")
    except httpx.HTTPError as e:
        raise SystemExit(f"Could not log in to {base_url} - is the backend running? ({e})")

    return DriverContext(base_url, zones, client, staff["token"], admin["token"])


async def main_async(args: argparse.Namespace) -> int:
    ctx = await build_context(args.base_url)
    results: dict[str, bool] = {}

    try:
        if args.phantom:
            results["tc11a"] = await tc11_phantom_load(ctx, n=args.phantom, duration=args.phantom_duration)

        names = args.scenarios or list(SCENARIOS.keys())
        for name in names:
            fn = SCENARIOS.get(name)
            if fn is None:
                print(f"Unknown scenario group: {name} (known: {', '.join(SCENARIOS)})")
                continue
            try:
                results[name] = await fn(ctx)
            except Exception as e:  # noqa: BLE001 - one bad scenario shouldn't kill the run
                print(f"  -> ERROR in {name}: {e!r}")
                results[name] = False
    finally:
        await ctx.client.aclose()

    print("\n" + "=" * 60)
    print("SUMMARY")
    for name, ok in results.items():
        print(f"  {name}: {'PASS' if ok else 'FAIL'}")
    all_pass = bool(results) and all(results.values())
    print(f"\n{'ALL GREEN' if all_pass else 'SOME SCENARIOS FAILED'}")
    return 0 if all_pass else 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("scenarios", nargs="*", help="scenario group(s) to run (default: all except --phantom)")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--phantom", type=int, default=0, help="also run TC11a with N phantom zones")
    parser.add_argument("--phantom-duration", type=float, default=12.0, help="seconds to hold phantom load")
    args = parser.parse_args()
    sys.exit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
