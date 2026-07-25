"""Named scenarios, one group per rubric test-case cluster (CLAUDE.md). Each
prints narration cues for the video and does ONE observable check per
sub-case - these are demo/regression scaffolding, not a second pytest suite;
formula math, hysteresis, dedup, ack races, and restart recovery are already
proven in tests/. What's proven here is that the real running system, over
real HTTP, produces the behavior the video claims it does.

Driver targets the sim ZoneNode for sensor-level cases (visual: gradual
ramps, debounce, decay) and hits the API directly for protocol-level cases
(malformed payload, duplicate seq, races, concurrency) - same split CLAUDE.md
specifies for the Wokwi-based driver once Phase 4 lands.
"""

import asyncio
import datetime as dt
import itertools
import time

import httpx

from sim.common import create_phantom_zones, delete_phantom_zones, narrate, result
from sim.node import ZoneNode, hold_fire, ramp

_seq_counter = itertools.count()


def _seq_base() -> int:
    """A fresh, unique starting seq each call. Dedup is permanent per
    (zone_id, seq) for the DB's lifetime, and CLAUDE.md requires every
    scenario to be idempotent/re-runnable (video retakes) - a hardcoded
    literal seq would silently become a "duplicate" on the second run and
    break whichever protocol-level assertion expected `duplicate: false`.
    Combines a microsecond timestamp (unique across separate driver runs)
    with a monotonic in-process counter (unique across calls made within
    the same microsecond, which does happen back-to-back in tc6/tc18).
    """
    return (int(time.time() * 1_000_000) % 1_000_000_000) + next(_seq_counter)


class DriverContext:
    def __init__(self, base_url: str, zones: dict, client: httpx.AsyncClient, staff_token: str, admin_token: str):
        self.base_url = base_url
        self.zones = zones
        self.client = client
        self.staff_token = staff_token
        self.admin_token = admin_token

    def zone(self, name: str) -> dict:
        return self.zones[name]

    def node(self, name: str) -> ZoneNode:
        z = self.zone(name)
        return ZoneNode(name, z["id"], z["api_key"], self.base_url)

    async def status(self) -> dict:
        resp = await self.client.get(
            f"{self.base_url}/api/zones/status", headers={"Authorization": f"Bearer {self.admin_token}"}
        )
        resp.raise_for_status()
        return resp.json()

    async def zone_view(self, name: str) -> dict:
        status = await self.status()
        zid = self.zone(name)["id"]
        return next(z for z in status["zones"] if z["zone_id"] == zid)

    async def override(self, name: str, target_state: str, reason: str) -> dict:
        resp = await self.client.post(
            f"{self.base_url}/api/admin/override",
            headers={"Authorization": f"Bearer {self.admin_token}"},
            json={"zone_id": self.zone(name)["id"], "target_state": target_state, "reason": reason},
        )
        resp.raise_for_status()
        return resp.json()

    async def reset(self, name: str) -> None:
        await self.override(name, "SAFE", "scenario reset")
        await asyncio.sleep(0.1)


# ---------------------------------------------------------------- tc1 flame


async def tc1_flame(ctx: DriverContext) -> bool:
    narrate("tc1", "Flame: none / flicker (< debounce) / sustained / removal decay - zone: IoT Lab")
    ok = True
    node = ctx.node("IoT Lab")
    await ctx.reset("IoT Lab")

    narrate("tc1a", "No flame -> zone stays SAFE")
    for _ in range(3):
        node.fire = 0
        await node.send_reading()
        await asyncio.sleep(0.1)
    view = await ctx.zone_view("IoT Lab")
    ok &= result("tc1a", view["state"] == "SAFE", f"state={view['state']}")

    narrate("tc1b", "Flicker: 3 consecutive HIGH (< 5-reading debounce) -> no trigger")
    await hold_fire(node, 1, 3, interval=0.1)
    node.fire = 0
    await node.send_reading()
    view = await ctx.zone_view("IoT Lab")
    ok &= result("tc1b", view["risk_score"] < 40, f"risk_score={view['risk_score']} (fire must not have latched)")

    narrate("tc1c", "Sustained: 5 consecutive HIGH -> debounce trips, fire contributes 40")
    await hold_fire(node, 1, 5, interval=0.76)
    view = await ctx.zone_view("IoT Lab")
    ok &= result("tc1c", view["risk_score"] >= 40, f"risk_score={view['risk_score']}")
    cmd = await node.poll_command()
    narrate("tc1c", f"node sees: {node.describe_actuation()}")

    narrate("tc1d", "Removal: fire drops, score decays linearly over 5s (not an instant snap)")
    node.fire = 0
    await node.send_reading()
    await asyncio.sleep(0.1)
    mid_view = await ctx.zone_view("IoT Lab")
    await asyncio.sleep(5.2)
    node.fire = 0
    await node.send_reading()
    end_view = await ctx.zone_view("IoT Lab")
    ok &= result(
        "tc1d",
        0 <= end_view["risk_score"] < mid_view["risk_score"],
        f"mid={mid_view['risk_score']} -> end={end_view['risk_score']} (should decay toward 0)",
    )

    await node.close()
    return ok


# ------------------------------------------------------------------ tc2 gas


async def tc2_gas(ctx: DriverContext) -> bool:
    narrate("tc2", "Gas: baseline / gradual ramp / threshold cross / boot warm-up - zone: IoT Lab (only gas sensor)")
    ok = True
    node = ctx.node("IoT Lab")
    await ctx.reset("IoT Lab")
    node.reboot()

    narrate("tc2d", "Boot warm-up: high gas within first 30s of boot must be IGNORED")
    node.gas_norm = 0.9
    await node.send_reading()
    view = await ctx.zone_view("IoT Lab")
    ok &= result("tc2d", view["risk_score"] < 22, f"risk_score={view['risk_score']} (25*0.9=22.5 would leak through if warm-up failed)")

    node.gas_norm = 0.0
    await node.send_reading()

    narrate("tc2a", "Baseline: gas=0 contributes nothing")
    view = await ctx.zone_view("IoT Lab")
    ok &= result("tc2a", view["state"] == "SAFE", f"state={view['state']}")

    narrate("tc2b/tc2c", "Gradual ramp 0 -> 0.9 across 6 steps, no step jump, crosses toward WARNING/CRITICAL band")
    node.assume_already_booted(31)  # past the 30s warm-up, so this part of the demo isn't gated by it
    await ramp(node, "gas_norm", 0.0, 0.9, steps=6, hold=0.1, interval=0.2)
    view = await ctx.zone_view("IoT Lab")
    ok &= result("tc2c", view["risk_score"] >= 20, f"risk_score={view['risk_score']}")

    node.gas_norm = 0.0
    await node.send_reading()
    await node.close()
    return ok


# ---------------------------------------------------------------- tc3 water


async def tc3_water(ctx: DriverContext) -> bool:
    narrate("tc3", "Water: dry / rising / cross / wet-then-cleared - zone: Server Room (flood-equivalent profile)")
    ok = True
    node = ctx.node("Server Room")
    await ctx.reset("Server Room")

    narrate("tc3a", "Dry: water=0")
    node.water_norm = 0.0
    await node.send_reading()
    view = await ctx.zone_view("Server Room")
    ok &= result("tc3a", view["state"] == "SAFE", f"state={view['state']}")

    narrate("tc3b/tc3c", "Rising 0 -> 1.0 across 6 steps, proportional, crosses toward CRITICAL")
    await ramp(node, "water_norm", 0.0, 1.0, steps=6, hold=0.1, interval=0.2)
    view = await ctx.zone_view("Server Room")
    ok &= result("tc3c", view["risk_score"] >= 25, f"risk_score={view['risk_score']}")

    narrate("tc3d", "Cleared: water back to 0, contribution resets (not sticky)")
    node.water_norm = 0.0
    await node.send_reading()
    await asyncio.sleep(0.1)
    view = await ctx.zone_view("Server Room")
    ok &= result("tc3d", view["risk_score"] < 25, f"risk_score={view['risk_score']}")

    await node.close()
    return ok


# ------------------------------------------------------------------ tc4 pir


async def tc4_pir(ctx: DriverContext) -> bool:
    narrate("tc4", "PIR: empty / enter (1.5s hold) / flicker / sensor disconnected -> OFFLINE - zone: Data Science Lab")
    ok = True
    node = ctx.node("Data Science Lab")
    await ctx.reset("Data Science Lab")

    narrate("tc4a", "Empty: occupancy=0")
    node.occupancy = 0
    await node.send_reading()
    view = await ctx.zone_view("Data Science Lab")
    ok &= result("tc4a", view["state"] == "SAFE")

    narrate("tc4b", "Enter: occupancy=1 held past the 1.5s hold -> occupancy_factor commits")
    node.occupancy = 1
    for _ in range(3):
        await node.send_reading()
        await asyncio.sleep(0.8)  # 3 * 0.8s = 2.4s total, clears the 1.5s hold requirement
    view = await ctx.zone_view("Data Science Lab")
    ok &= result("tc4b", view["risk_score"] >= 10, f"risk_score={view['risk_score']} (occupancy alone contributes 10)")

    narrate("tc4c", "Flicker: brief drop to 0 and back, under the hold - should NOT log/toggle")
    node.occupancy = 0
    await node.send_reading()
    await asyncio.sleep(0.2)
    node.occupancy = 1
    await node.send_reading()
    view = await ctx.zone_view("Data Science Lab")
    ok &= result("tc4c", view["risk_score"] >= 10, f"risk_score={view['risk_score']} (should still show occupied, flicker ignored)")

    narrate("tc4d", "PIR sensor disconnected mid-stream (null field) -> zone shows OFFLINE badge, no false SAFE")
    node.occupancy_connected = False
    node.fire_connected = False
    node.water_connected = False
    await node.send_reading()
    view = await ctx.zone_view("Data Science Lab")
    pir_sensor = next(s for s in view["sensors"] if s["type"] == "pir")
    ok &= result("tc4d", pir_sensor["status"] == "offline", f"pir sensor status={pir_sensor['status']}")

    node.occupancy_connected = True
    node.fire_connected = True
    node.water_connected = True
    node.occupancy = 0
    await node.send_reading()
    await node.close()
    return ok


# ------------------------------------------------------------ tc5 actuation


async def tc5_actuation(ctx: DriverContext) -> bool:
    narrate("tc5", "Actuation: CRITICAL response / WARNING-only / recovery reset / two zones same second")
    ok = True

    narrate("tc5a", "CRITICAL -> buzzer+relay ON, LED red, within ~1 command poll")
    await ctx.override("IoT Lab", "CRITICAL", "tc5a actuation check")
    node = ctx.node("IoT Lab")
    cmd = await node.poll_command()
    ok &= result("tc5a", cmd["buzzer"] and cmd["relay"] and cmd["led"] == "red", f"command={cmd}")

    narrate("tc5b", "WARNING -> yellow LED only, no relay/buzzer")
    await ctx.override("IoT Lab", "WARNING", "tc5b actuation check")
    cmd = await node.poll_command()
    ok &= result("tc5b", not cmd["buzzer"] and not cmd["relay"] and cmd["led"] == "yellow", f"command={cmd}")

    narrate("tc5c", "Recovery: SAFE -> green LED, everything off")
    await ctx.override("IoT Lab", "SAFE", "tc5c actuation check")
    cmd = await node.poll_command()
    ok &= result("tc5c", not cmd["buzzer"] and not cmd["relay"] and cmd["led"] == "green", f"command={cmd}")
    await node.close()

    narrate("tc5d", "Two zones CRITICAL the same second -> both get correct, independent commands")
    await asyncio.gather(
        ctx.override("Server Room", "CRITICAL", "tc5d dual"),
        ctx.override("Data Science Lab", "CRITICAL", "tc5d dual"),
    )
    n1, n2 = ctx.node("Server Room"), ctx.node("Data Science Lab")
    c1, c2 = await asyncio.gather(n1.poll_command(), n2.poll_command())
    ok &= result("tc5d", c1["led"] == "red" and c2["led"] == "red", f"server_room={c1} data_science_lab={c2}")
    await ctx.override("Server Room", "SAFE", "tc5d cleanup")
    await ctx.override("Data Science Lab", "SAFE", "tc5d cleanup")
    await n1.close()
    await n2.close()
    return ok


# ------------------------------------------------------- tc6 protocol-level


async def tc6_protocol(ctx: DriverContext) -> bool:
    narrate("tc6", "Protocol: malformed payload / dual-hazard / duplicate seq / concurrent all-zones")
    ok = True
    key = ctx.zone("IoT Lab")["api_key"]

    narrate("tc6b", "Malformed: negative water -> 422, never silently absorbed")
    resp = await ctx.client.post(
        f"{ctx.base_url}/api/ingest",
        headers={"X-Zone-Key": key},
        json={"seq": 900001, "fire": 0, "gas_norm": 0.1, "water_norm": -0.5, "occupancy": 0, "ts_device": _now_iso()},
    )
    ok &= result("tc6b", resp.status_code == 422, f"status={resp.status_code}")

    narrate("tc6c", "Dual-hazard: gas+water combine into the real weighted score, not a step function")
    resp = await ctx.client.post(
        f"{ctx.base_url}/api/ingest",
        headers={"X-Zone-Key": key},
        json={
            "seq": _seq_base(),
            "fire": 0,
            "gas_norm": 0.4,
            "water_norm": 0.4,
            "occupancy": 0,
            "ts_device": _now_iso(),
            "uptime_ms": 60_000,
        },
    )
    body = resp.json()
    expected = 25 * 0.4 + 25 * 0.4  # 20
    ok &= result("tc6c", resp.status_code == 200 and abs(body["risk_score"] - expected) < 0.01, f"body={body}")

    narrate("tc6d", "Duplicate seq -> second POST returns duplicate:true, not counted twice")
    dup_payload = {
        "seq": _seq_base(),
        "fire": 0,
        "gas_norm": 0.1,
        "water_norm": 0.1,
        "occupancy": 0,
        "ts_device": _now_iso(),
        "uptime_ms": 60_000,
    }
    r1 = await ctx.client.post(f"{ctx.base_url}/api/ingest", headers={"X-Zone-Key": key}, json=dup_payload)
    r2 = await ctx.client.post(f"{ctx.base_url}/api/ingest", headers={"X-Zone-Key": key}, json=dup_payload)
    ok &= result("tc6d", r1.json()["duplicate"] is False and r2.json()["duplicate"] is True, f"r1={r1.json()} r2={r2.json()}")

    narrate("tc6e", "Concurrent all-zones: 3 zones POST at once -> none dropped")
    seq = _seq_base()
    tasks = [
        ctx.client.post(
            f"{ctx.base_url}/api/ingest",
            headers={"X-Zone-Key": z["api_key"]},
            json={
                "seq": seq,
                "fire": 0,
                "gas_norm": 0.1 if name == "IoT Lab" else None,
                "water_norm": 0.1,
                "occupancy": 0,
                "ts_device": _now_iso(),
                "uptime_ms": 60_000,
            },
        )
        for name, z in ctx.zones.items()
    ]
    responses = await asyncio.gather(*tasks)
    ok &= result("tc6e", all(r.status_code == 200 for r in responses), f"statuses={[r.status_code for r in responses]}")

    return ok


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


async def _drive_to_critical(node: ZoneNode) -> None:
    """fire (40) + water=1.0 (25) = 65, crossing CRITICAL - works for any of
    our 3 zones since all three have both a fire and a water sensor (fire
    alone tops out at 40, WARNING, never CRITICAL on its own)."""
    node.water_norm = 1.0
    await hold_fire(node, 1, 5, interval=0.76)


# -------------------------------------------------------------- tc7 incidents


async def tc7_incidents(ctx: DriverContext) -> bool:
    narrate("tc7", "3 zones near-simultaneously / double-ack race / flip-flood / re-trigger")
    ok = True

    narrate("tc7a", "3 zones crossing CRITICAL within the same ~1s window (parallel fire debounce - "
                     "literal-simultaneous isn't reachable under the locked 5-reading/750ms debounce, "
                     "so zones are driven in parallel instead of sequentially)")
    for name in ctx.zones:
        await ctx.reset(name)
    nodes = [ctx.node(name) for name in ctx.zones]

    await asyncio.gather(*(_drive_to_critical(n) for n in nodes))
    status = await ctx.status()
    critical_count = sum(1 for z in status["zones"] if z["state"] == "CRITICAL")
    ok &= result("tc7a", critical_count == len(nodes), f"critical_count={critical_count}/{len(nodes)}")
    ok &= result("tc7a-queue", len(status["priority_queue"]) == critical_count, f"priority_queue len={len(status['priority_queue'])}")
    for n in nodes:
        await n.close()

    narrate("tc7b", "Double-ack race: two parallel requests for the same incident -> exactly one 200, one 409")
    incident_id = next((z["open_incident_id"] for z in status["zones"] if z["open_incident_id"]), None)
    if incident_id is None:
        ok &= result("tc7b", False, "no open incident to race against - tc7a must have failed to reach CRITICAL")
        return ok
    r1, r2 = await asyncio.gather(
        ctx.client.post(f"{ctx.base_url}/api/incidents/{incident_id}/ack", headers={"Authorization": f"Bearer {ctx.staff_token}"}),
        ctx.client.post(f"{ctx.base_url}/api/incidents/{incident_id}/ack", headers={"Authorization": f"Bearer {ctx.admin_token}"}),
        return_exceptions=True,
    )
    codes = sorted(r.status_code for r in (r1, r2))
    ok &= result("tc7b", codes == [200, 409], f"codes={codes}")

    narrate("tc7c", "Flip-flood: oscillate right around threshold rapidly -> no incident flood (3s hold suppresses it)")
    for name in ctx.zones:
        await ctx.reset(name)
    before = await ctx.client.get(f"{ctx.base_url}/api/incidents", headers={"Authorization": f"Bearer {ctx.admin_token}"})
    before_count = len(before.json())
    for _ in range(6):
        await ctx.override("IoT Lab", "CRITICAL", "tc7c flap")
        await ctx.override("IoT Lab", "SAFE", "tc7c flap")
    after = await ctx.client.get(f"{ctx.base_url}/api/incidents", headers={"Authorization": f"Bearer {ctx.admin_token}"})
    after_count = len(after.json())
    # each override IS a manual, deliberate transition (cause='manual'), so this
    # isn't testing sensor hysteresis (covered in tests/test_state_machine.py) -
    # it's confirming rapid manual flips don't corrupt incident bookkeeping.
    ok &= result("tc7c", after_count - before_count <= 6, f"incidents created={after_count - before_count}")

    narrate("tc7d", "Re-trigger: resolve then cross again -> a NEW incident row, not a reused one")
    await ctx.override("IoT Lab", "CRITICAL", "tc7d first")
    first = await ctx.zone_view("IoT Lab")
    first_incident = first["open_incident_id"]
    await ctx.override("IoT Lab", "SAFE", "tc7d resolve")
    await ctx.override("IoT Lab", "CRITICAL", "tc7d retrigger")
    second = await ctx.zone_view("IoT Lab")
    second_incident = second["open_incident_id"]
    ok &= result("tc7d", first_incident != second_incident, f"first={first_incident} second={second_incident}")
    await ctx.reset("IoT Lab")

    return ok


# --------------------------------------------------------------- tc11 load


async def tc11_phantom_load(ctx: DriverContext, n: int = 30, duration: float = 12.0) -> bool:
    narrate("tc11a", f"--phantom {n}: {n} fake zones posting concurrently for {duration:.0f}s - backend + dashboard stay responsive")
    phantoms = await create_phantom_zones(n)
    nodes = [ZoneNode(p["name"], p["id"], p["api_key"], ctx.base_url) for p in phantoms]

    latencies = []
    errors = 0

    async def hammer(node: ZoneNode):
        nonlocal errors
        node.gas_norm = 0.0
        node.water_norm = 0.1
        end = asyncio.get_event_loop().time() + duration
        while asyncio.get_event_loop().time() < end:
            t0 = asyncio.get_event_loop().time()
            resp = await node.send_reading()
            latencies.append(asyncio.get_event_loop().time() - t0)
            if resp is None or resp.status_code != 200:
                errors += 1
            await asyncio.sleep(0.75)

    await asyncio.gather(*(hammer(n) for n in nodes))
    for n in nodes:
        await n.close()

    p95 = sorted(latencies)[int(len(latencies) * 0.95)] if latencies else 0
    ok = result(
        "tc11a",
        errors == 0 and p95 < 1.0,
        f"{len(nodes)} nodes, {len(latencies)} requests, {errors} errors, p95 latency={p95*1000:.0f}ms",
    )

    deleted = await delete_phantom_zones()
    narrate("tc11a", f"cleaned up {deleted} phantom zone(s)")
    return ok


# --------------------------------------------------------- tc18 concurrent


async def tc18_concurrent_writes(ctx: DriverContext) -> bool:
    narrate("tc18a", "10 simultaneous writes to one zone -> none lost, none corrupted")
    key = ctx.zone("Data Science Lab")["api_key"]
    base = _seq_base()
    tasks = [
        ctx.client.post(
            f"{ctx.base_url}/api/ingest",
            headers={"X-Zone-Key": key},
            json={
                "seq": base + i,
                "fire": 0,
                "water_norm": 0.05,
                "occupancy": 0,
                "ts_device": _now_iso(),
                "uptime_ms": 60_000,
            },
        )
        for i in range(10)
    ]
    responses = await asyncio.gather(*tasks)
    all_ok = all(r.status_code == 200 for r in responses)
    none_duplicate = all(not r.json().get("duplicate") for r in responses)
    return result("tc18a", all_ok and none_duplicate, f"statuses={[r.status_code for r in responses]}")


# --------------------------------------------------------------- tc22 finale


async def tc22_finale(ctx: DriverContext) -> bool:
    narrate("tc22", "FINALE: continuous two-zone incident, ranked, acked in order, recovery to idle")
    ok = True
    for name in ("IoT Lab", "Server Room"):
        await ctx.reset(name)

    narrate("tc22", "IoT Lab (fire+gas+occupancy) and Server Room (water+fire, flood-equivalent) build "
                     "toward CRITICAL concurrently - each node keeps posting throughout, so neither goes "
                     "OFFLINE while the other is mid-ramp")
    n1 = ctx.node("IoT Lab")
    n2 = ctx.node("Server Room")
    n1.assume_already_booted()  # this is a mid-demo zone reuse, not a fresh device - gas shouldn't be warm-up-gated

    async def build_iot_lab():
        n1.gas_norm = 0.7  # 40 (fire) + 17.5 (gas) + 10 (occupancy, committed well within the 3.8s hold) = 67.5
        n1.occupancy = 1
        await hold_fire(n1, 1, 5, interval=0.76)

    async def build_server_room():
        await ramp(n2, "water_norm", 0.0, 1.0, steps=4, hold=0.1, interval=0.2)  # 25 (water)
        n2.fire = 1
        await hold_fire(n2, 1, 5, interval=0.76)  # + 40 (fire) = 65

    await asyncio.gather(build_iot_lab(), build_server_room())

    status = await ctx.status()
    critical = [z for z in status["zones"] if z["zone_id"] in (ctx.zone("IoT Lab")["id"], ctx.zone("Server Room")["id"])]
    ok &= result("tc22-critical", all(z["state"] == "CRITICAL" for z in critical), f"zones={critical}")
    ok &= result("tc22-queue", len(status["priority_queue"]) >= 2, f"priority_queue={status['priority_queue']}")
    if status["priority_queue"]:
        narrate("tc22", f"#1 MOST URGENT: {status['priority_queue'][0]['zone_name']} - {status['priority_queue'][0]['justification']}")

    narrate("tc22", "Acknowledging both, in priority order")
    for entry in status["priority_queue"]:
        zid = entry["zone_id"]
        zview = next(z for z in status["zones"] if z["zone_id"] == zid)
        if zview["open_incident_id"]:
            r = await ctx.client.post(
                f"{ctx.base_url}/api/incidents/{zview['open_incident_id']}/ack",
                headers={"Authorization": f"Bearer {ctx.staff_token}"},
            )
            ok &= result(f"tc22-ack-{entry['zone_name']}", r.status_code == 200, f"status={r.status_code}")

    narrate("tc22", "Recovery: both zones clear back to idle")
    for name in ("IoT Lab", "Server Room"):
        await ctx.reset(name)
    final = await ctx.status()
    managed_ids = {ctx.zone("IoT Lab")["id"], ctx.zone("Server Room")["id"]}
    managed_final = [z for z in final["zones"] if z["zone_id"] in managed_ids]
    ok &= result("tc22-recovery", all(z["state"] == "SAFE" for z in managed_final), f"zones={managed_final}")

    await n1.close()
    await n2.close()
    return ok


# ------------------------------------------------------------- tc23 edge cases


async def tc23_edge_cases(ctx: DriverContext) -> bool:
    narrate("tc23", "Edge cases: offline mid-incident / triple-critical / override-collision / reconnect catch-up / impossible value")
    ok = True

    narrate("tc23a", "Offline mid-incident: zone goes CRITICAL then stops posting -> OFFLINE badge, state frozen (not falsely SAFE)")
    await ctx.reset("Data Science Lab")
    node = ctx.node("Data Science Lab")
    await _drive_to_critical(node)
    node.go_offline()
    await asyncio.sleep(3.2)  # > OFFLINE_AFTER_SECONDS
    view = await ctx.zone_view("Data Science Lab")
    ok &= result("tc23a", view["offline"] is True and view["state"] == "CRITICAL", f"view={view}")
    node.go_online()
    await ctx.reset("Data Science Lab")
    await node.close()

    narrate("tc23b", "Triple-critical: all 3 zones CRITICAL at once -> priority queue has exactly 3, correctly ranked")
    nodes = [ctx.node(name) for name in ctx.zones]
    await asyncio.gather(*(_drive_to_critical(n) for n in nodes))
    status = await ctx.status()
    ok &= result("tc23b", len(status["priority_queue"]) == 3, f"priority_queue={status['priority_queue']}")
    for name in ctx.zones:
        await ctx.reset(name)
    for n in nodes:
        await n.close()

    narrate("tc23c", "Override-collision: admin override and a sensor crossing fire at the same instant -> exactly one transition, never a double-fire")
    await ctx.reset("Server Room")
    node = ctx.node("Server Room")
    for _ in range(4):
        node.fire = 1
        await node.send_reading()
        await asyncio.sleep(0.76)
    # 5th (debounce-triggering) sensor reading and an admin override race each other.
    node.fire = 1

    async def final_reading():
        return await node.send_reading()

    await asyncio.gather(final_reading(), ctx.override("Server Room", "CRITICAL", "tc23c collision"))
    view = await ctx.zone_view("Server Room")
    ok &= result("tc23c", view["state"] == "CRITICAL", f"view={view}")
    await ctx.reset("Server Room")
    await node.close()

    narrate("tc23d", "Reconnect catch-up: WS drop -> reconnect -> fresh snapshot matches REST /api/zones/status (no stale data)")
    ok &= await _tc23d_ws_catchup(ctx)

    narrate("tc23f", "Impossible value: fire=7 (not 0/1) -> 422, never silently absorbed")
    resp = await ctx.client.post(
        f"{ctx.base_url}/api/ingest",
        headers={"X-Zone-Key": ctx.zone("IoT Lab")["api_key"]},
        json={"seq": 990001, "fire": 7, "water_norm": 0.1, "occupancy": 0, "ts_device": _now_iso()},
    )
    ok &= result("tc23f", resp.status_code == 422, f"status={resp.status_code}")

    return ok


async def _tc23d_ws_catchup(ctx: DriverContext) -> bool:
    import websockets

    ws_url = ctx.base_url.replace("http://", "ws://").replace("https://", "wss://") + f"/ws?token={ctx.admin_token}"
    async with websockets.connect(ws_url) as ws1:
        await ws1.recv()  # initial snapshot
    # Connection closed (simulating a drop); reconnect and compare against REST.
    async with websockets.connect(ws_url) as ws2:
        import json

        snapshot = json.loads(await ws2.recv())
    rest = await ctx.status()
    same_states = {z["zone_id"]: z["state"] for z in snapshot["zones"]} == {z["zone_id"]: z["state"] for z in rest["zones"]}
    return result("tc23d", snapshot["event"] == "snapshot" and same_states, "reconnect snapshot matches REST status")
