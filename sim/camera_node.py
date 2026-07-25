"""Bonus 1: camera occupancy node - the Track B stand-in for an ESP32-CAM.

Runs the exact algorithm the case suggests ("simple motion or
frame-difference detection is enough"): consecutive grayscale frames are
differenced, the changed-pixel ratio is smoothed over a short window, and
the zone is called "occupied" when that ratio clears a threshold. The
verdict + confidence is POSTed to `/api/camera` (zone-key auth) once a
second, where the backend cross-checks it against the PIR for the priority
ranking.

Three frame sources, because Track B has no physical ESP32-CAM:
    --webcam            laptop webcam via OpenCV (the honest live demo)
    --video FILE        any video file via OpenCV (re-runnable demo takes)
    --synthetic         no OpenCV needed: emits a scripted occupied/empty
                        pattern so the *integration* (cross-check, priority
                        effect, dashboard chip) can be demonstrated on any
                        machine. The narration/docs must say which source
                        was used - synthetic mode demonstrates integration,
                        not detection.

Usage:
    python sim/camera_node.py --zone "IoT Lab" --webcam
    python sim/camera_node.py --zone "IoT Lab" --video footage.mp4
    python sim/camera_node.py --zone "IoT Lab" --synthetic
    (--base-url http://127.0.0.1:8000 to target another server)
"""

import argparse
import asyncio
import datetime as dt
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from sim.common import load_zones  # noqa: E402

POST_INTERVAL_SECONDS = 1.0
MOTION_PIXEL_THRESHOLD = 25  # grayscale delta for a pixel to count as "changed"
MOTION_RATIO_THRESHOLD = 0.02  # >=2% of pixels changed -> motion
SMOOTH_WINDOW = 5  # verdicts smoothed over the last N frames


class FrameDiffDetector:
    """Consecutive-frame differencing with a smoothed verdict."""

    def __init__(self) -> None:
        self.prev_gray = None
        self.recent_ratios: list[float] = []

    def update(self, gray_frame) -> tuple[bool, float]:
        import numpy as np

        ratio = 0.0
        if self.prev_gray is not None:
            delta = np.abs(gray_frame.astype(np.int16) - self.prev_gray.astype(np.int16))
            ratio = float((delta > MOTION_PIXEL_THRESHOLD).mean())
        self.prev_gray = gray_frame

        self.recent_ratios.append(ratio)
        if len(self.recent_ratios) > SMOOTH_WINDOW:
            self.recent_ratios.pop(0)

        smoothed = sum(self.recent_ratios) / len(self.recent_ratios)
        occupied = smoothed >= MOTION_RATIO_THRESHOLD
        # Confidence: how decisively the smoothed ratio clears (or fails)
        # the threshold, clamped to [0.5, 1.0] so "barely" reads as 0.5.
        distance = abs(smoothed - MOTION_RATIO_THRESHOLD) / MOTION_RATIO_THRESHOLD
        confidence = max(0.5, min(1.0, 0.5 + distance / 2))
        return occupied, confidence


def _open_capture(args):
    try:
        import cv2
    except ImportError:
        print("OpenCV not installed. `pip install opencv-python`, or use --synthetic.")
        raise SystemExit(1)
    source = 0 if args.webcam else args.video
    cap = cv2.VideoCapture(source)
    if not cap.isOpened():
        print(f"Could not open video source: {source}")
        raise SystemExit(1)
    return cv2, cap


async def run(args) -> None:
    zones = await load_zones()
    if args.zone not in zones:
        print(f"Unknown zone '{args.zone}' - known: {sorted(zones)}")
        raise SystemExit(1)
    zone = zones[args.zone]
    headers = {"X-Zone-Key": zone["api_key"]}
    print(f"[camera:{args.zone}] reporting to {args.base_url} every {POST_INTERVAL_SECONDS}s")

    detector = FrameDiffDetector()
    cv2 = cap = None
    if not args.synthetic:
        cv2, cap = _open_capture(args)

    synthetic_t0 = time.monotonic()
    async with httpx.AsyncClient(timeout=5.0) as client:
        while True:
            if args.synthetic:
                # Scripted pattern: 15s occupied / 15s empty, forever.
                elapsed = time.monotonic() - synthetic_t0
                occupied = (int(elapsed) // 15) % 2 == 0
                confidence = 0.9
            else:
                ok, frame = cap.read()
                if not ok:
                    if args.video:  # loop demo footage
                        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        continue
                    print("Camera read failed; exiting.")
                    return
                gray = cv2.cvtColor(cv2.resize(frame, (320, 240)), cv2.COLOR_BGR2GRAY)
                occupied, confidence = detector.update(gray)

            try:
                resp = await client.post(
                    f"{args.base_url}/api/camera",
                    headers=headers,
                    json={
                        "zone_id": zone["id"],
                        "occupied": occupied,
                        "confidence": round(confidence, 2),
                        "ts_device": dt.datetime.now(dt.timezone.utc).isoformat(),
                    },
                )
                body = resp.json()
                marker = "==" if body.get("agrees_with_pir") else "!="
                print(
                    f"[camera:{args.zone}] occupied={occupied} conf={confidence:.2f} "
                    f"| PIR {marker} camera (pir_occupied={body.get('pir_occupied')})"
                )
            except httpx.HTTPError as exc:
                print(f"[camera:{args.zone}] POST failed: {exc}")

            await asyncio.sleep(POST_INTERVAL_SECONDS)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--zone", required=True, help='zone name, e.g. "IoT Lab"')
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--webcam", action="store_true", help="use the default webcam (needs opencv-python)")
    source.add_argument("--video", help="use a video file (needs opencv-python)")
    source.add_argument("--synthetic", action="store_true", help="no camera/OpenCV: scripted occupancy pattern")
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
