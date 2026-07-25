import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite+aiosqlite:///{(BASE_DIR / 'data' / 'scsrg.db').as_posix()}")
ENV = os.getenv("ENV", "development")

LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "deepseek")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-chat")

# --- Locked risk fusion / state machine parameters (see CLAUDE.md) ---
WEIGHT_FIRE = 40
WEIGHT_GAS = 25
WEIGHT_WATER = 25
WEIGHT_OCCUPANCY = 10

SAFE_MAX = 30          # score < 30 -> SAFE
WARNING_MAX = 65        # 30 <= score < 65 -> WARNING; score >= 65 -> CRITICAL
CRITICAL_EXIT_SCORE = 55   # exit CRITICAL only when score drops below this
CRITICAL_MIN_HOLD_SECONDS = 3.0

FLAME_DEBOUNCE_COUNT = 5          # consecutive HIGH raw readings required to trigger
FLAME_DECAY_SECONDS = 5.0         # linear decay to 0 after removal

GAS_WARMUP_SECONDS = 30.0         # ignore gas readings for this long after node boot

OCCUPANCY_MIN_HOLD_SECONDS = 1.5  # PIR state change must hold this long before it's logged

OFFLINE_AFTER_SECONDS = 3.0       # no reading from a zone for this long -> OFFLINE (4x the 750ms interval)

NODE_POST_INTERVAL_SECONDS = 0.75

PRIORITY_OCCUPANCY_BONUS = 15
PRIORITY_UNACKED_CAP = 10
PRIORITY_UNACKED_DIVISOR = 15  # +1 priority per this many unacked seconds, capped
