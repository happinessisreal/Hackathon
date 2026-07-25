"""ZoneNode: a Python stand-in for the ESP32 firmware (firmware/zone_node.ino,
Phase 4), implementing the exact same wire protocol described in CLAUDE.md so
scenarios written against it transfer directly to the Wokwi sketch later:

  - POSTs a reading to /api/ingest every ~750ms with X-Zone-Key auth
  - fire (0/1) and occupancy (0/1) are raw digital reads; gas_norm/water_norm
    are already normalized 0.0-1.0 (the node's job is unit conversion from
    its ADC range, never scoring/state - see ASSUMPTIONS.md)
  - sends uptime_ms (millis() since boot) so the backend can gate gas during
    its 30s warm-up window
  - a sensor field is omitted (null) when that specific sensor is marked
    disconnected, never fabricated
  - polls /api/commands/{zone_id} for actuation state (buzzer/relay/LED)
  - buffers readings in RAM when a POST fails and resyncs them - in original
    seq order - once connectivity returns (TC9b)

This class is deliberately the single source of "what a node does"; the
scenario driver only flips its public setters, it never talks to /api/ingest
directly for anything that a real node would do.
"""

import asyncio
import datetime as dt
import time

import httpx


class ZoneNode:
    def __init__(self, name: str, zone_id: int, api_key: str, base_url: str = "http://127.0.0.1:8000"):
        self.name = name
        self.zone_id = zone_id
        self.api_key = api_key
        self.base_url = base_url

        self.fire = 0
        self.gas_norm = 0.0
        self.water_norm = 0.0
        self.occupancy = 0
        self.fire_connected = True
        self.gas_connected = True
        self.water_connected = True
        self.occupancy_connected = True

        self.online = True  # simulated network connectivity
        # Dedup is permanent per (zone_id, seq) for the DB's lifetime. A real
        # device's counter never resets to 0 just because you're holding a
        # new reference to it; seeding from wall-clock time means two
        # ZoneNode instances for the same zone (e.g. two scenarios reusing
        # "IoT Lab" against the same live server) never collide and get
        # silently treated as duplicates of each other's readings.
        self.seq = int(time.time() * 1000) % 100_000_000
        self._boot_monotonic = time.monotonic()
        self._buffer: list[dict] = []
        self._last_command: dict | None = None

        self._client = httpx.AsyncClient(timeout=5.0)
        self._loop_task: asyncio.Task | None = None

    def assume_already_booted(self, seconds: float = 60.0) -> None:
        """A freshly-constructed ZoneNode reports uptime_ms starting near 0,
        which is correct for a device that just powered on but wrong for a
        scenario that's just reusing a zone mid-demo (e.g. the finale) where
        the real device would already be well past its 30s gas warm-up
        window. Backdates boot time so the very first reading already looks
        like a long-running device."""
        self._boot_monotonic -= seconds

    def reboot(self) -> None:
        """Simulates a power cycle: uptime resets, so the backend's gas
        warm-up window restarts too. seq keeps counting (a real device
        would also reset seq, but the backend dedups per zone regardless -
        kept monotonic here to avoid colliding with earlier readings still
        in this run)."""
        self._boot_monotonic = time.monotonic()

    def uptime_ms(self) -> int:
        return int((time.monotonic() - self._boot_monotonic) * 1000)

    def _headers(self) -> dict:
        return {"X-Zone-Key": self.api_key}

    def _build_payload(self) -> dict:
        payload = {
            "seq": self.seq,
            "fire": self.fire if self.fire_connected else None,
            "gas_norm": round(self.gas_norm, 4) if self.gas_connected else None,
            "water_norm": round(self.water_norm, 4) if self.water_connected else None,
            "occupancy": self.occupancy if self.occupancy_connected else None,
            "ts_device": dt.datetime.now(dt.timezone.utc).isoformat(),
            "uptime_ms": self.uptime_ms(),
        }
        self.seq += 1
        return payload

    async def _post(self, payload: dict) -> httpx.Response:
        return await self._client.post(f"{self.base_url}/api/ingest", headers=self._headers(), json=payload)

    async def send_reading(self) -> httpx.Response | None:
        """Builds and sends one reading. If the node is "offline" or the
        POST fails, the reading is buffered instead - exactly like the
        firmware would when it can't reach the AP/backend."""
        payload = self._build_payload()

        if not self.online:
            self._buffer.append(payload)
            return None

        await self._flush_buffer()

        try:
            resp = await self._post(payload)
            return resp
        except httpx.HTTPError:
            self._buffer.append(payload)
            self.online = False
            return None

    async def _flush_buffer(self) -> None:
        while self._buffer:
            oldest = self._buffer[0]
            try:
                resp = await self._post(oldest)
            except httpx.HTTPError:
                return  # still can't reach the backend; stop and try again next tick
            if resp.status_code not in (200,):
                return
            self._buffer.pop(0)

    def go_offline(self) -> None:
        self.online = False

    def go_online(self) -> None:
        self.online = True

    @property
    def buffered_count(self) -> int:
        return len(self._buffer)

    async def poll_command(self) -> dict | None:
        try:
            resp = await self._client.get(f"{self.base_url}/api/commands/{self.zone_id}", headers=self._headers())
            if resp.status_code == 200:
                self._last_command = resp.json()
        except httpx.HTTPError:
            pass
        return self._last_command

    def describe_actuation(self) -> str:
        if not self._last_command:
            return "no command yet"
        c = self._last_command
        return f"LED={c['led'].upper()} buzzer={'ON' if c['buzzer'] else 'off'} relay={'ON' if c['relay'] else 'off'}"

    async def run_forever(self, interval: float = 0.75) -> None:
        while True:
            await self.send_reading()
            await self.poll_command()
            await asyncio.sleep(interval)

    def start_background(self, interval: float = 0.75) -> None:
        if self._loop_task is None:
            self._loop_task = asyncio.create_task(self.run_forever(interval))

    def stop_background(self) -> None:
        if self._loop_task is not None:
            self._loop_task.cancel()
            self._loop_task = None

    async def close(self) -> None:
        self.stop_background()
        await self._client.aclose()


async def ramp(node: ZoneNode, attr: str, start: float, end: float, steps: int, hold: float, interval: float = 0.75):
    """Linearly ramps a normalized sensor attribute (gas_norm/water_norm)
    from start to end over `steps` readings, sending + polling each step -
    the "gradual, no step jump" cases (tc2b/tc3b)."""
    for i in range(steps + 1):
        value = start + (end - start) * (i / steps)
        setattr(node, attr, value)
        await node.send_reading()
        await node.poll_command()
        await asyncio.sleep(interval if i < steps else hold)


async def hold_fire(node: ZoneNode, raw: int, readings: int, interval: float = 0.75):
    """Sends `readings` consecutive fire readings at the given raw value,
    polling commands after each - the debounce/decay cases (tc1)."""
    for _ in range(readings):
        node.fire = raw
        await node.send_reading()
        await node.poll_command()
        await asyncio.sleep(interval)
