"""Bonus 4: free-text incident reporting.

Pipeline: text -> {LLM call, or regex/keyword fallback if no LLM_API_KEY or
the call fails/times out} -> deterministic validation gate -> soft advisory
priority term.

Hard safety rule (CLAUDE.md #4 - predicted/ML/NL-derived values never
trigger actuation): nothing in this module writes to a zone's risk_score,
state, or `/api/commands` output. The only place its output is consumed is
`ZoneRuntime.advisory_boost()`, which `priority.py` adds to the *ranking*
of zones the sensor pipeline has *already* put in CRITICAL - it can shuffle
the queue, never populate it.
"""

import datetime as dt
import json

import httpx

from backend.config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL

ALLOWED_HAZARDS = ("fire", "gas", "water")

HAZARD_KEYWORDS: dict[str, list[str]] = {
    "fire": ["fire", "flame", "smoke", "burning", "burn"],
    "gas": ["gas", "fumes", "smell", "odor", "odour"],
    "water": ["water", "flood", "leak", "wet", "spill", "dripping"],
}

SEVERITY_KEYWORDS: list[tuple[list[str], float]] = [
    (["huge", "massive", "severe", "raging", "out of control"], 0.9),
    (["large", "big", "serious", "major"], 0.7),
    (["moderate"], 0.5),
    (["small", "minor", "slight", "little", "faint"], 0.3),
]
DEFAULT_SEVERITY = 0.5

SYSTEM_PROMPT = (
    "You are a hazard-report parser for a campus safety system. Extract "
    "exactly one JSON object from the user's free-text report, in this "
    "exact shape and nothing else:\n"
    '{"zone": "<zone name mentioned, or your best guess>", '
    '"hazard_type": "fire|gas|water", "severity": <float 0.0-1.0>}\n'
    "If the text doesn't clearly state a zone, hazard, or severity, make "
    "your best reasonable guess from context. Output ONLY the JSON object."
)

LLM_TIMEOUT_SECONDS = 8.0


class ReportRejected(Exception):
    """Raised by the validation gate; carries a human-readable reason."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


async def call_llm(text: str) -> dict | None:
    """Returns a parsed dict from the LLM, or None on any failure/timeout/
    missing key - callers must treat None as "fall through to the regex
    parser", never as an error to propagate."""
    if not LLM_API_KEY:
        return None
    try:
        async with httpx.AsyncClient(timeout=LLM_TIMEOUT_SECONDS) as client:
            resp = await client.post(
                f"{LLM_BASE_URL}/chat/completions",
                headers={"Authorization": f"Bearer {LLM_API_KEY}"},
                json={
                    "model": LLM_MODEL,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": text},
                    ],
                    "temperature": 0,
                    "response_format": {"type": "json_object"},
                },
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            parsed = json.loads(content)
            return parsed if isinstance(parsed, dict) else None
    except Exception:
        # Network error, timeout, non-2xx, malformed JSON - all treated the
        # same: the demo must keep working offline, so we degrade instead
        # of failing the request.
        return None


def fallback_parse(text: str, known_zones: list[str]) -> dict:
    """Deterministic offline path - no network, no model. Always returns a
    dict shaped like the LLM's output (still runs through the same
    validation gate afterward)."""
    lower = text.lower()

    zone = next((name for name in known_zones if name.lower() in lower), None)
    if zone is None and known_zones:
        zone = known_zones[0]

    hazard = next(
        (h for h, keywords in HAZARD_KEYWORDS.items() if any(kw in lower for kw in keywords)),
        ALLOWED_HAZARDS[0],
    )

    severity = next(
        (value for keywords, value in SEVERITY_KEYWORDS if any(kw in lower for kw in keywords)),
        DEFAULT_SEVERITY,
    )

    return {"zone": zone, "hazard_type": hazard, "severity": severity}


def validate_and_clean(raw: dict, zones_by_name: dict[str, int]) -> dict:
    """The deterministic gate: zone must exist, hazard must be in the enum,
    severity is clamped (not rejected) to [0, 1]. Applies identically to
    LLM output and fallback output - a hallucinated zone name is rejected
    exactly like a fallback miss would be."""
    zone_name = str(raw.get("zone") or "").strip()
    match = next((name for name in zones_by_name if name.lower() == zone_name.lower()), None)
    if match is None:
        raise ReportRejected(f"unknown zone '{zone_name}' - must be one of {sorted(zones_by_name)}")

    hazard = str(raw.get("hazard_type") or "").strip().lower()
    if hazard not in ALLOWED_HAZARDS:
        raise ReportRejected(f"hazard_type '{hazard}' not in {list(ALLOWED_HAZARDS)}")

    try:
        severity = float(raw.get("severity", DEFAULT_SEVERITY))
    except (TypeError, ValueError):
        raise ReportRejected("severity must be a number")
    severity = max(0.0, min(1.0, severity))

    return {"zone_id": zones_by_name[match], "zone_name": match, "hazard_type": hazard, "severity": severity}


async def parse_report(text: str, zones_by_name: dict[str, int]) -> dict:
    llm_result = await call_llm(text)
    source = "llm"
    if llm_result is None:
        llm_result = fallback_parse(text, list(zones_by_name))
        source = "fallback"
    cleaned = validate_and_clean(llm_result, zones_by_name)
    cleaned["source"] = source
    cleaned["received_at"] = dt.datetime.now(dt.timezone.utc)
    return cleaned
