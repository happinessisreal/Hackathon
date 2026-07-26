"""One narrated run covering TC6 through TC11 — built for a single video take.

sim/driver.py already implements tc6, tc7 and tc11a. It has no tc8, tc9 or
tc10, so those are implemented here:

  tc8   every documented endpoint answers with its documented shape/status
  tc9b  node buffers readings while offline, resyncs with ORIGINAL seq numbers
  tc10a unregistered zone key -> 401
  tc10b dashboard call with no/!bad token -> 401

tc9a (restart recovery) cannot be automated from inside this process — it
needs the backend killed and restarted. The script pauses and walks you
through it, then verifies the rebuilt state, so the pause is usable footage
rather than dead air. Pass --skip-restart to run straight through.

Usage:
    python sim/record_tc6_tc11.py                 # full TC6-TC11
    python sim/record_tc6_tc11.py --skip-restart  # no tc9a pause
    python sim/record_tc6_tc11.py --phantom 30    # heavier tc11a
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from sim.common import get_token, load_zones, narrate, result  # noqa: E402
from sim.node import ZoneNode  # noqa: E402
from sim.scenarios import (  # noqa: E402
    DriverContext,
    tc6_protocol,
    tc7_incidents,
    tc11_phantom_load,
    settle_node,
)

BANNER = "=" * 64


def section(tc: str, title: str) -> None:
    print(f"\n{BANNER}\n  {tc.upper()}  —  {title}\n{BANNER}")


# --------------------------------------------------------------- tc8


async def tc8_api_contract(ctx: DriverContext) -> bool:
    """TC8/TC28: the documented API table is the contract. Rather than reading
    it aloud, hit every documented endpoint live and show each answers with the
    documented status - documentation that is demonstrably not stale."""
    narrate("tc8", "Every documented endpoint answers as documented (API table = real contract)")
    ok = True
    zone = ctx.zone("IoT Lab")
    hz = {"X-Zone-Key": zone["api_key"]}

    # /api/auth/login is part of the contract, but the backend keeps a single
    # active token per user, so probing it ROTATES the token this run is holding.
    # Probe it first, then adopt the token it returns - otherwise every later
    # bearer call in this scenario (and ctx.reset() in tc9b) 401s.
    r = await ctx.client.post(
        f"{ctx.base_url}/api/auth/login", json={"username": "admin1", "password": "admin123"}
    )
    ok &= result("tc8", r.status_code == 200, f"POST /api/auth/login -> {r.status_code} (documented 200)")
    if r.status_code == 200:
        ctx.admin_token = r.json()["token"]

    bearer = {"Authorization": f"Bearer {ctx.admin_token}"}
    staff = {"Authorization": f"Bearer {ctx.staff_token}"}

    checks = [
        ("GET", "/api/ping", None, {}, 200),
        ("GET", "/api/zones/status", None, bearer, 200),
        ("GET", f"/api/zones/{zone['id']}/trend", None, bearer, 200),
        ("GET", "/api/incidents", None, bearer, 200),
        ("GET", f"/api/commands/{zone['id']}", None, hz, 200),
        ("GET", "/api/admin/health", None, bearer, 200),
        # RBAC is part of the contract: staff must be refused admin routes.
        ("GET", "/api/admin/health", None, staff, 403),
    ]

    for method, path, body, headers, want in checks:
        r = await ctx.client.request(method, f"{ctx.base_url}{path}", json=body, headers=headers)
        good = r.status_code == want
        ok &= result("tc8", good, f"{method} {path} -> {r.status_code} (documented {want})")
    return ok


# --------------------------------------------------------------- tc9b


async def tc9b_offline_cache(ctx: DriverContext) -> bool:
    """TC9b: readings taken while the link is down must survive in RAM and
    resync later under their ORIGINAL seq numbers - not renumbered, or the
    backend's (zone_id, seq) dedup could double-count them on a retry."""
    narrate("tc9b", "Node caches readings while offline, resyncs with original seq numbers")
    node = ctx.node("Server Room")
    await ctx.reset("Server Room")
    await settle_node(node, seconds=2.0)

    before = await ctx.zone_view("Server Room")
    first_seq = node.seq

    node.go_offline()
    narrate("tc9b", "link down - 5 readings taken with nowhere to go")
    for _ in range(5):
        await node.send_reading()
        await asyncio.sleep(0.15)
    buffered = node.buffered_count
    ok = result("tc9b", buffered == 5, f"buffered in RAM while offline = {buffered} (expected 5)")

    narrate("tc9b", "link restored - buffer flushes oldest-first")
    node.go_online()
    await node.send_reading()
    await asyncio.sleep(0.4)
    ok &= result("tc9b", node.buffered_count == 0, f"buffer drained, {node.buffered_count} left")

    # Prove the original seqs actually landed rather than being reassigned.
    from sqlalchemy import select

    from backend.database import async_session_maker
    from backend.models import Reading

    async with async_session_maker() as db:
        rows = (
            await db.execute(
                select(Reading.seq)
                .where(Reading.zone_id == ctx.zone("Server Room")["id"], Reading.seq >= first_seq)
                .order_by(Reading.seq)
            )
        ).scalars().all()
    expected = list(range(first_seq, first_seq + 6))
    landed = [s for s in rows if s in expected]
    ok &= result(
        "tc9b",
        len(landed) == 6,
        f"original seqs {first_seq}..{first_seq + 5} all present ({len(landed)}/6) - no renumbering",
    )
    after = await ctx.zone_view("Server Room")
    print(f"       zone recovered: offline={before['offline']} -> offline={after['offline']}")
    await node.close()
    return ok


# --------------------------------------------------------------- tc10


async def tc10_auth(ctx: DriverContext) -> bool:
    narrate("tc10a", "Unregistered zone key -> 401 (a rogue node cannot inject readings)")
    ok = True
    async with httpx.AsyncClient(timeout=10) as raw:
        r = await raw.post(
            f"{ctx.base_url}/api/ingest",
            headers={"X-Zone-Key": "zk_TOTALLY_NOT_A_REAL_KEY"},
            json={
                "seq": 999999001, "fire": 1, "gas_norm": 0.9, "water_norm": 0.9,
                "occupancy": 1, "ts_device": "2026-07-26T12:00:00Z",
            },
        )
        ok &= result("tc10a", r.status_code == 401, f"POST /api/ingest with bogus key -> {r.status_code}")

        narrate("tc10b", "Dashboard data with no token, then a bad token -> 401 both times")
        r = await raw.get(f"{ctx.base_url}/api/zones/status")
        ok &= result("tc10b", r.status_code == 401, f"no Authorization header -> {r.status_code}")
        r = await raw.get(
            f"{ctx.base_url}/api/zones/status", headers={"Authorization": "Bearer not-a-real-token"}
        )
        ok &= result("tc10b", r.status_code == 401, f"invalid bearer token -> {r.status_code}")
    return ok


# --------------------------------------------------------------- tc9a


async def tc9a_restart(ctx: DriverContext, skip: bool) -> bool | None:
    narrate("tc9a", "Restart recovery: backend rebuilds zone state from the DB, never assumes SAFE")
    if skip:
        print("  -> SKIPPED [tc9a] - --skip-restart was passed")
        return None

    # Drive a zone to CRITICAL so there is real state worth losing.
    node = ctx.node("IoT Lab")
    await ctx.reset("IoT Lab")
    await settle_node(node, seconds=2.0)
    node.assume_already_booted(60)
    node.fire, node.water_norm, node.occupancy = 1, 1.0, 1
    for _ in range(6):
        await node.send_reading()
        await asyncio.sleep(0.2)
    view = await ctx.zone_view("IoT Lab")
    print(f"       IoT Lab is now state={view['state']} risk={view['risk_score']:.1f}")
    await node.close()

    print("\n  ACTION REQUIRED — good footage, do it on camera:")
    print("    1. Ctrl-C the uvicorn terminal (the dashboard goes disconnected)")
    print("    2. Restart it:  ./.venv/Scripts/python.exe -m uvicorn backend.main:app --port 8000")
    print("    3. Come back here and press ENTER")
    print("\n  The point: after restart the zone must still read CRITICAL/WARNING,")
    print("  rebuilt from zone_transitions - NOT reset to a false SAFE.\n")
    await asyncio.to_thread(input, "  press ENTER once the backend is back up... ")

    for attempt in range(20):
        try:
            after = await ctx.zone_view("IoT Lab")
            break
        except Exception:
            await asyncio.sleep(1)
    else:
        return result("tc9a", False, "backend never came back")

    return result(
        "tc9a",
        after["state"] in ("CRITICAL", "WARNING"),
        f"state after restart = {after['state']} risk={after['risk_score']:.1f} (rebuilt from DB, not SAFE)",
    )


# --------------------------------------------------------------- main


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:8000")
    ap.add_argument("--phantom", type=int, default=30)
    ap.add_argument("--phantom-duration", type=float, default=10.0)
    ap.add_argument("--skip-restart", action="store_true")
    args = ap.parse_args()

    zones = await load_zones()
    if not zones:
        raise SystemExit("No zones - run scripts/init_db.py first.")

    async with httpx.AsyncClient(timeout=30) as client:
        # get_token returns the whole login payload (token + role), not a string.
        staff = await get_token(client, args.base_url, "staff1", "staff123")
        admin = await get_token(client, args.base_url, "admin1", "admin123")
        ctx = DriverContext(args.base_url, zones, client, staff["token"], admin["token"])

        results: dict[str, bool | None] = {}

        # tc6_protocol posts straight to /api/ingest and asserts exact scores,
        # but never resets the zone first - so it silently inherits a latched
        # FireTracker from whatever ran before (flame testing on Wokwi, an
        # earlier take) and tc6c reads 40 points high. Flush IoT Lab to a cold
        # SAFE baseline so this run is reproducible no matter what preceded it.
        print("\n  preparing: flushing IoT Lab tracker state to a cold baseline...")
        prep = ctx.node("IoT Lab")
        await ctx.reset("IoT Lab")
        await settle_node(prep)
        await prep.close()
        base = await ctx.zone_view("IoT Lab")
        print(f"  baseline: state={base['state']} risk={base['risk_score']:.1f}")

        section("tc6", "Protocol hardening: malformed / dual-hazard / duplicate / concurrent")
        results["tc6"] = await tc6_protocol(ctx)

        section("tc7", "Incidents: multi-zone, double-ack race, flip-flood, re-trigger")
        results["tc7"] = await tc7_incidents(ctx)

        section("tc8", "API contract is live, not just documented")
        results["tc8"] = await tc8_api_contract(ctx)

        section("tc9b", "Node offline caching and resync")
        results["tc9b"] = await tc9b_offline_cache(ctx)

        section("tc10", "Authentication boundaries")
        results["tc10"] = await tc10_auth(ctx)

        section("tc11a", f"Load: {args.phantom} phantom zones posting concurrently")
        results["tc11a"] = await tc11_phantom_load(ctx, n=args.phantom, duration=args.phantom_duration)

        section("tc9a", "Restart recovery (needs a manual backend restart)")
        results["tc9a"] = await tc9a_restart(ctx, args.skip_restart)

    print(f"\n{BANNER}\n  SUMMARY — TC6 to TC11\n{BANNER}")
    failed = 0
    for tc in ("tc6", "tc7", "tc8", "tc9a", "tc9b", "tc10", "tc11a"):
        v = results.get(tc)
        label = "SKIPPED" if v is None else ("PASS" if v else "FAIL")
        if v is False:
            failed += 1
        print(f"  {tc:7s} {label}")
    print()
    print("ALL GREEN" if failed == 0 else f"{failed} GROUP(S) FAILED")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
