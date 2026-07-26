"""Every script-demonstrable test case, in ascending order, in one take.

Each case prints a banner with its explicit TC id before it runs and a
PASS/FAIL line with its TC id after, so the terminal itself is the on-screen
test-case labelling TC31 asks for.

WHAT THIS COVERS (script-demonstrable)
    tc1   flame: none / flicker / sustained / decay
    tc2   gas: warm-up / baseline / ramp
    tc3   water: dry / rising / cross / cleared
    tc4   pir: empty / enter / flicker / disconnected -> OFFLINE
    tc5   actuation: critical / warning-only / reset / two zones at once
    tc6   malformed 422 / dual-hazard / duplicate seq / concurrent zones
    tc7   3 zones critical / double-ack race / flip-flood / re-trigger
    tc8   every documented endpoint answers as documented        [added here]
    tc9b  offline caching + resync under original seq numbers    [added here]
    tc10  unregistered zone key / missing + bad token -> 401     [added here]
    tc11a 30 phantom zones under concurrent load
    tc13  RBAC enforced server-side: staff token -> 403          [added here]
    tc18a 10 simultaneous writes to one zone
    tc18b zone delete with dependent rows blocked by the FK      [added here]
    tc18c out-of-order ts_device stored + flagged, never applied [added here]
    tc19  indexed query stays fast at volume, index confirmed    [added here]
    tc22  finale: continuous two-zone incident end to end
    tc23  edge cases: offline mid-incident / triple critical /
          override collision / reconnect catch-up / impossible value
    tc24  combined load with consistency + responsiveness checks
    tc9a  restart recovery - guided pause, needs a manual restart

WHAT THIS CANNOT COVER (must be filmed in the UI or the docs)
    tc12  priority queue + justification line + MOST URGENT banner
    tc14  incident timeline drill-down
    tc15  stacking toasts + audio cue, ack silences it
    tc16  colour + icon + text label (never colour alone)
    tc25  dashboard renders backend state, no client-side drift
    tc26  circuit diagrams per zone
    tc28/tc30/tc31  documentation and video requirements

Usage:
    python sim/record_all.py                  # everything, incl. tc9a pause
    python sim/record_all.py --skip-restart   # uninterrupted
    python sim/record_all.py --only tc1 tc5   # subset, still in TC order
    python sim/record_all.py --list           # show ids and exit
"""

import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402
from sqlalchemy import select, text  # noqa: E402

from backend.database import async_session_maker  # noqa: E402
from backend.models import Reading  # noqa: E402
from sim.common import get_token, load_zones, narrate, result  # noqa: E402
from sim.record_tc6_tc11 import (  # noqa: E402
    tc8_api_contract,
    tc9a_restart,
    tc9b_offline_cache,
    tc10_auth,
)
from sim.scenarios import (  # noqa: E402
    DriverContext,
    settle_node,
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
    tc24_combined_load,
    _now_iso,
    _seq_base,
)

BAR = "=" * 68


def section(tc: str, title: str) -> None:
    print(f"\n{BAR}\n  {tc.upper():<8} {title}\n{BAR}")


# ------------------------------------------------------------------ tc13


async def tc13_rbac(ctx: DriverContext) -> bool:
    """RBAC must hold at the API, not only in the UI. A hidden button is not
    access control - the staff token is sent directly to the admin routes."""
    narrate("tc13", "RBAC server-side: staff token hits admin routes directly -> 403, admin -> 200")
    ok = True
    zid = ctx.zone("IoT Lab")["id"]
    staff = {"Authorization": f"Bearer {ctx.staff_token}"}
    admin = {"Authorization": f"Bearer {ctx.admin_token}"}

    r = await ctx.client.post(
        f"{ctx.base_url}/api/admin/override",
        headers=staff,
        json={"zone_id": zid, "target_state": "CRITICAL", "reason": "staff should not be able to do this"},
    )
    ok &= result("tc13", r.status_code == 403, f"staff -> POST /api/admin/override = {r.status_code} (want 403)")

    r = await ctx.client.get(f"{ctx.base_url}/api/admin/health", headers=staff)
    ok &= result("tc13", r.status_code == 403, f"staff -> GET /api/admin/health = {r.status_code} (want 403)")

    r = await ctx.client.get(f"{ctx.base_url}/api/admin/health", headers=admin)
    ok &= result("tc13", r.status_code == 200, f"admin -> GET /api/admin/health = {r.status_code} (want 200)")

    # Staff must still be able to do its own job, or "403 everything" would pass.
    r = await ctx.client.get(f"{ctx.base_url}/api/zones/status", headers=staff)
    ok &= result("tc13", r.status_code == 200, f"staff -> GET /api/zones/status = {r.status_code} (want 200)")
    return ok


# ----------------------------------------------------------------- tc18b


async def tc18b_delete_blocked(ctx: DriverContext) -> bool:
    """Referential integrity is enforced by the schema, not by app code:
    ON DELETE RESTRICT means a zone with dependent rows cannot be deleted even
    by raw SQL that bypasses every application check."""
    narrate("tc18b", "Zone delete with dependent rows -> blocked by the FK itself, not by app logic")
    zid = ctx.zone("IoT Lab")["id"]
    async with async_session_maker() as db:
        await db.execute(text("PRAGMA foreign_keys=ON"))
        deps = (
            await db.execute(text("select count(*) from readings where zone_id=:z"), {"z": zid})
        ).scalar_one()
        try:
            await db.execute(text("delete from zones where id=:z"), {"z": zid})
            await db.commit()
            return result("tc18b", False, "DELETE SUCCEEDED - referential integrity is NOT enforced")
        except Exception as exc:
            await db.rollback()
            kind = type(exc).__name__
            msg = str(exc).splitlines()[0][:90]
            return result("tc18b", True, f"blocked with {deps} dependent readings: {kind} - {msg}")


# ----------------------------------------------------------------- tc18c


async def tc18c_out_of_order(ctx: DriverContext) -> bool:
    """A reading whose ts_device predates the zone's last applied reading is
    stored for audit and flagged, but must never rewrite current state -
    otherwise a delayed packet could silently downgrade a live CRITICAL."""
    narrate("tc18c", "Out-of-order ts_device -> stored + flagged anomaly, current state untouched")
    key = ctx.zone("IoT Lab")["api_key"]
    zid = ctx.zone("IoT Lab")["id"]
    ok = True

    # Establish a current reading, then send one dated well before it.
    fresh = {
        "seq": _seq_base(), "fire": 0, "gas_norm": 0.2, "water_norm": 0.2,
        "occupancy": 0, "ts_device": _now_iso(), "uptime_ms": 60_000,
    }
    r = await ctx.client.post(f"{ctx.base_url}/api/ingest", headers={"X-Zone-Key": key}, json=fresh)
    baseline = r.json()["risk_score"]

    stale_seq = _seq_base()
    stale = {
        "seq": stale_seq, "fire": 1, "gas_norm": 0.9, "water_norm": 0.9,
        "occupancy": 1, "ts_device": "2020-01-01T00:00:00Z", "uptime_ms": 60_000,
    }
    r = await ctx.client.post(f"{ctx.base_url}/api/ingest", headers={"X-Zone-Key": key}, json=stale)
    body = r.json()
    ok &= result("tc18c", body.get("anomaly") is True, f"flagged anomaly=True in response: {body}")
    ok &= result(
        "tc18c",
        abs(body["risk_score"] - baseline) < 0.01,
        f"score unchanged {baseline:.1f} -> {body['risk_score']:.1f} despite a maxed-out stale payload",
    )

    async with async_session_maker() as db:
        row = (
            await db.execute(select(Reading).where(Reading.zone_id == zid, Reading.seq == stale_seq))
        ).scalar_one_or_none()
    ok &= result("tc18c", row is not None and row.anomaly is True, "row persisted with anomaly=1 (audit trail kept)")
    return ok


# ------------------------------------------------------------------ tc19


async def tc19_indexed_query(ctx: DriverContext) -> bool:
    """The incident-history query must stay fast at real volume, and be fast
    because of an index rather than by accident on a small table - so the
    query plan is printed, not just the timing."""
    narrate("tc19", "Incident history query at volume: timed, with the query plan shown")
    ok = True
    async with async_session_maker() as db:
        n_read = (await db.execute(text("select count(*) from readings"))).scalar_one()
        n_inc = (await db.execute(text("select count(*) from incidents"))).scalar_one()
        print(f"       table volume: {n_read} readings, {n_inc} incidents")
        if n_read < 10_000:
            print(f"       NOTE: fewer than 10k readings - run `python sim/seed.py` for the full TC19 claim")

        # Equality on the leading column, ordered by the second. That is exactly
        # the shape idx_incidents_status_created(status, opened_at) exists for:
        # the seek narrows to one status and the index already supplies
        # opened_at order, so there is no sort step. A `status != 'resolved'`
        # predicate cannot drive the same index - an inequality has no single
        # seek point - and SQLite correctly falls back to a scan.
        sql = (
            "select id, zone_id, opened_at, peak_risk, status from incidents "
            "where status = 'open' order by opened_at desc limit 50"
        )
        plan = (await db.execute(text("EXPLAIN QUERY PLAN " + sql))).fetchall()
        plan_txt = " | ".join(str(r[-1]) for r in plan)
        print(f"       plan: {plan_txt}")
        used_index = "idx_incidents_status_created" in plan_txt or "USING INDEX" in plan_txt.upper()
        ok &= result("tc19", used_index, f"index used (not a full scan): {plan_txt[:80]}")

        best = None
        for _ in range(5):
            t0 = time.perf_counter()
            (await db.execute(text(sql))).fetchall()
            el = (time.perf_counter() - t0) * 1000
            best = el if best is None else min(best, el)
        ok &= result("tc19", best < 100, f"best of 5 runs = {best:.1f}ms (budget 100ms)")
    return ok


# ------------------------------------------------------------------ plan


async def prep_baseline(ctx: DriverContext) -> None:
    """tc6/tc18c assert exact scores but never reset the zone, so they inherit
    a latched FireTracker from whatever ran before (Wokwi flame testing, a
    previous take) and read up to 40 points high. Flush to a cold SAFE first."""
    print("\n  preparing: flushing IoT Lab tracker state to a cold baseline...")
    node = ctx.node("IoT Lab")
    await ctx.reset("IoT Lab")
    await settle_node(node)
    await node.close()
    v = await ctx.zone_view("IoT Lab")
    print(f"  baseline: state={v['state']} risk={v['risk_score']:.1f}")


PLAN: list[tuple[str, str, object]] = [
    ("tc1",   "Flame: none / flicker under debounce / sustained / decay on removal", tc1_flame),
    ("tc2",   "Gas: boot warm-up ignored / baseline / gradual ramp",                 tc2_gas),
    ("tc3",   "Water: dry / rising / threshold / cleared",                           tc3_water),
    ("tc4",   "PIR: empty / enter / flicker / disconnected -> OFFLINE",              tc4_pir),
    ("tc5",   "Actuation: CRITICAL fires / WARNING is LED-only / reset / 2 zones",   tc5_actuation),
    ("tc6",   "Malformed 422 / dual-hazard weighting / duplicate seq / concurrent",  tc6_protocol),
    ("tc7",   "3 zones CRITICAL / double-ack race / flip-flood / re-trigger",        tc7_incidents),
    ("tc8",   "Documented API is the real contract",                                 tc8_api_contract),
    ("tc9b",  "Offline caching, resync under ORIGINAL seq numbers",                  tc9b_offline_cache),
    ("tc10",  "Auth boundaries: bad zone key / missing token / bad token -> 401",    tc10_auth),
    ("tc11a", "Load: 30 phantom zones posting concurrently",                         "phantom"),
    ("tc13",  "RBAC enforced server-side, not just hidden in the UI",                tc13_rbac),
    ("tc18a", "10 simultaneous writes to one zone",                                  tc18_concurrent_writes),
    ("tc18b", "Zone delete blocked by the foreign key itself",                       tc18b_delete_blocked),
    ("tc18c", "Out-of-order timestamp flagged, never applied",                       tc18c_out_of_order),
    ("tc19",  "Indexed incident query at volume, plan shown",                        tc19_indexed_query),
    ("tc22",  "FINALE: continuous two-zone incident, ranked, acked, recovered",      tc22_finale),
    ("tc23",  "Edge cases: offline mid-incident / triple / collision / catch-up",    tc23_edge_cases),
    ("tc24",  "Combined load with consistency + responsiveness checks",              tc24_combined_load),
]

UI_ONLY = [
    ("tc12", "priority queue, justification line, MOST URGENT banner"),
    ("tc14", "incident timeline drill-down"),
    ("tc15", "stacking toasts + audio cue, ack silences"),
    ("tc16", "colour + icon + text label, never colour alone"),
    ("tc25", "dashboard renders backend state, no client drift"),
    ("tc26", "circuit diagrams per zone"),
    ("tc28/30/31", "documentation and video requirements"),
]


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:8000")
    ap.add_argument("--phantom", type=int, default=30)
    ap.add_argument("--phantom-duration", type=float, default=10.0)
    ap.add_argument("--skip-restart", action="store_true")
    ap.add_argument("--only", nargs="*", default=None, help="run only these ids")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    if args.list:
        print("script-demonstrable:")
        for tc, title, _ in PLAN:
            print(f"  {tc:7s} {title}")
        print("  tc9a    restart recovery (guided pause)")
        print("\nUI / docs only - film these separately:")
        for tc, title in UI_ONLY:
            print(f"  {tc:11s} {title}")
        return

    zones = await load_zones()
    if not zones:
        raise SystemExit("No zones - run scripts/init_db.py first.")

    selected = [p for p in PLAN if args.only is None or p[0] in args.only]
    results: dict[str, bool | None] = {}
    t_start = time.time()

    async with httpx.AsyncClient(timeout=60) as client:
        staff = await get_token(client, args.base_url, "staff1", "staff123")
        admin = await get_token(client, args.base_url, "admin1", "admin123")
        ctx = DriverContext(args.base_url, zones, client, staff["token"], admin["token"])

        await prep_baseline(ctx)

        for tc, title, fn in selected:
            section(tc, title)
            if fn == "phantom":
                results[tc] = await tc11_phantom_load(
                    ctx, n=args.phantom, duration=args.phantom_duration
                )
            else:
                results[tc] = await fn(ctx)

        if args.only is None:
            section("tc9a", "Restart recovery - rebuilt from the DB, never assumed SAFE")
            results["tc9a"] = await tc9a_restart(ctx, args.skip_restart)

    elapsed = time.time() - t_start
    print(f"\n{BAR}\n  SUMMARY — script-demonstrable test cases\n{BAR}")
    failed = 0
    for tc in [p[0] for p in selected] + (["tc9a"] if args.only is None else []):
        v = results.get(tc)
        label = "SKIPPED" if v is None else ("PASS" if v else "FAIL")
        if v is False:
            failed += 1
        print(f"  [{tc:6s}] {label}")
    print(f"\n  runtime {elapsed:.0f}s")
    print("\n  NOT covered here — film in the UI/docs:")
    for tc, title in UI_ONLY:
        print(f"    [{tc}] {title}")
    print()
    print("ALL GREEN" if failed == 0 else f"{failed} GROUP(S) FAILED")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
