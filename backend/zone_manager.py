"""In-memory per-zone runtime state: debounce/decay/warm-up trackers plus the
last-applied-reading bookkeeping needed for offline and out-of-order detection.

This is a derived cache, never the source of truth - on startup `ZoneManager.
restore_from_db` rebuilds it from `zone_transitions` / `readings` before the
server accepts connections (CLAUDE.md rule 6: never assume SAFE on restart).
"""

import asyncio
import datetime as dt

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import (
    FLAME_DEBOUNCE_COUNT,
    FLAME_DECAY_SECONDS,
    GAS_WARMUP_SECONDS,
    OCCUPANCY_MIN_HOLD_SECONDS,
)
from backend.models import Reading, Zone, ZoneTransition

SAFE = "SAFE"
WARNING = "WARNING"
CRITICAL = "CRITICAL"


class FireTracker:
    """Debounces the raw digital flame reading and computes the decaying
    0..1 contribution level used by the fusion formula."""

    def __init__(self) -> None:
        self.consec_high = 0
        self.debounced = False
        self.level_at_decay_start = 0.0
        self.decay_start: dt.datetime | None = None

    def update(self, raw: int | None, now: dt.datetime) -> float:
        if raw is None:
            return self.current_level(now)
        if raw:
            self.consec_high += 1
            if self.consec_high >= FLAME_DEBOUNCE_COUNT:
                self.debounced = True
                self.decay_start = None
        else:
            self.consec_high = 0
            if self.debounced:
                # Debounced-on level is always 1.0; capture that before
                # flipping debounced off (current_level() would otherwise
                # recurse into the not-yet-initialized decay branch).
                self.level_at_decay_start = 1.0
                self.debounced = False
                self.decay_start = now
        return self.current_level(now)

    def current_level(self, now: dt.datetime) -> float:
        if self.debounced:
            return 1.0
        if self.decay_start is not None:
            elapsed = (now - self.decay_start).total_seconds()
            if elapsed >= FLAME_DECAY_SECONDS:
                self.decay_start = None
                self.level_at_decay_start = 0.0
                return 0.0
            return max(0.0, self.level_at_decay_start * (1 - elapsed / FLAME_DECAY_SECONDS))
        return 0.0


class OccupancyTracker:
    """PIR state changes only take effect once the new raw state has held
    continuously for OCCUPANCY_MIN_HOLD_SECONDS (no log/score spam on flicker)."""

    def __init__(self) -> None:
        self.stable_value = 0
        self.pending_value: int | None = None
        self.pending_since: dt.datetime | None = None

    def update(self, raw: int | None, now: dt.datetime) -> int:
        if raw is None:
            return self.stable_value
        if raw == self.stable_value:
            self.pending_value = None
            self.pending_since = None
            return self.stable_value
        if self.pending_value != raw:
            self.pending_value = raw
            self.pending_since = now
            return self.stable_value
        assert self.pending_since is not None
        if (now - self.pending_since).total_seconds() >= OCCUPANCY_MIN_HOLD_SECONDS:
            self.stable_value = raw
            self.pending_value = None
            self.pending_since = None
        return self.stable_value


class ZoneRuntime:
    def __init__(self, zone_id: int) -> None:
        self.zone_id = zone_id
        self.fire = FireTracker()
        self.occupancy = OccupancyTracker()
        self.boot_ts: dt.datetime | None = None

        self.last_seq: int | None = None
        self.last_ts_server: dt.datetime | None = None
        self.last_applied_ts_device: dt.datetime | None = None
        self.last_raw: dict = {"fire": None, "gas_norm": None, "water_norm": None, "occupancy": None}

        self.current_state: str = SAFE
        self.current_risk_score: float = 0.0
        self.critical_entered_at: dt.datetime | None = None
        self.open_incident_id: int | None = None

        # Rolling window of the last 8 computed scores, for Bonus 2 (trend).
        self.recent_scores: list[float] = []

        # Bonus 4: NL-report advisory terms, each (severity, received_at).
        # Ephemeral/in-memory by design (same lifetime class as the other
        # runtime trackers above) - these are soft, decaying hints for
        # cross-zone triage, never persisted state and never a trigger for
        # actuation (CLAUDE.md #4). Consumed only by advisory_boost() below,
        # which priority.py adds to the ranking of zones already CRITICAL.
        self.advisory_reports: list[tuple[float, dt.datetime]] = []

    def record_score(self, score: float) -> None:
        self.recent_scores.append(score)
        if len(self.recent_scores) > 8:
            self.recent_scores.pop(0)

    def add_advisory_report(self, severity: float, received_at: dt.datetime) -> None:
        self.advisory_reports.append((severity, received_at))

    def advisory_boost(self, now: dt.datetime, decay_seconds: float = 600.0, cap: float = 10.0) -> float:
        """Sum of each report's `severity * 10` contribution, linearly
        decaying to 0 over `decay_seconds` (10 min, locked), capped in
        total at `cap` (10, same cap shape as the unacked-time term)."""
        total = 0.0
        fresh: list[tuple[float, dt.datetime]] = []
        for severity, received_at in self.advisory_reports:
            elapsed = (now - received_at).total_seconds()
            if elapsed >= decay_seconds:
                continue
            fresh.append((severity, received_at))
            total += severity * 10.0 * (1 - elapsed / decay_seconds)
        self.advisory_reports = fresh
        return min(cap, total)

    def gas_value(self, raw: float | None, now: dt.datetime) -> float | None:
        if raw is None:
            return None
        if self.boot_ts is not None and (now - self.boot_ts).total_seconds() < GAS_WARMUP_SECONDS:
            return None
        return raw

    def is_offline(self, now: dt.datetime, offline_after_seconds: float) -> bool:
        if self.last_ts_server is None:
            return True
        return (now - self.last_ts_server).total_seconds() > offline_after_seconds


class ZoneManager:
    def __init__(self) -> None:
        self._runtimes: dict[int, ZoneRuntime] = {}
        self._locks: dict[int, asyncio.Lock] = {}

    def get_or_create(self, zone_id: int) -> ZoneRuntime:
        if zone_id not in self._runtimes:
            self._runtimes[zone_id] = ZoneRuntime(zone_id)
        return self._runtimes[zone_id]

    def lock_for(self, zone_id: int) -> asyncio.Lock:
        if zone_id not in self._locks:
            self._locks[zone_id] = asyncio.Lock()
        return self._locks[zone_id]

    def all_runtimes(self) -> dict[int, ZoneRuntime]:
        return self._runtimes

    async def restore_from_db(self, db: AsyncSession) -> None:
        """CLAUDE.md rule 6: rebuild zone states from DB before serving traffic."""
        zones = (await db.execute(select(Zone))).scalars().all()
        for zone in zones:
            runtime = self.get_or_create(zone.id)

            last_transition = (
                await db.execute(
                    select(ZoneTransition)
                    .where(ZoneTransition.zone_id == zone.id)
                    .order_by(ZoneTransition.ts.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if last_transition is not None:
                runtime.current_state = last_transition.to_state
                runtime.current_risk_score = last_transition.risk_score
                if last_transition.to_state == CRITICAL:
                    runtime.critical_entered_at = last_transition.ts

            last_reading = (
                await db.execute(
                    select(Reading)
                    .where(Reading.zone_id == zone.id, Reading.anomaly.is_(False))
                    .order_by(Reading.ts_server.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if last_reading is not None:
                runtime.last_seq = last_reading.seq
                runtime.last_ts_server = last_reading.ts_server
                runtime.last_applied_ts_device = last_reading.ts_device
                runtime.last_raw = {
                    "fire": last_reading.fire,
                    "gas_norm": last_reading.gas_norm,
                    "water_norm": last_reading.water_norm,
                    "occupancy": last_reading.occupancy,
                }
                # Boot time cannot be recovered across a restart without a
                # persisted uptime sample; treat restart as a fresh warm-up
                # window so gas is never trusted prematurely (fail-safe).
                runtime.boot_ts = None
