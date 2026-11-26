"""Measure one camera's exposure and white balance ON THE DISC, and propose
the numbers for its camera_cfg — the camera's own auto meters the whole
frame, so the white disc on a darker background is pushed to full white.

    cd ~/Downloads/projects/apc && sudo python3 dev/camera/exposure.py bottom
    cd ~/Downloads/projects/apc && sudo python3 dev/camera/exposure.py top

Before it: a disc where the run inspects it, and nothing else moving —
  bottom  a disc held in the gripper at the station camera (the run paused
          at "inspecting", or dev/camera/camera.ipynb's present cell);
  top     a disc on the anode, the robot at its viewing stand (the run
          paused at "inspecting on the anode").

What it does, through the vision server (bench.j2: vision_ip / vision_port
and the camera's serial), and nothing else:
  1. reads the camera's exposure, gain and white balance as they are now;
  2. finds the disc with apc's own detector (vision/disc_od.yaml) — its
     FACE is the circle FACE_SHARE of the box, inside the band;
  3. pins gain at --gain (dB), then takes one picture at each exposure of
     --sweep (µs) and measures the face: its median brightness and the
     share of it clipped (any colour at or over CLIP);
  4. proposes the exposure whose face median is nearest TARGET with under
     MAX_CLIPPED clipped; at it, lets white balance settle ONCE on the
     disc (WB_FRAMES pictures) and reads the ratios back;
  5. PUTS THE CAMERA BACK exactly as step 1 found it, whatever happened.

It writes nothing anywhere but the pictures of the sweep
(captures/exposure/<time>_<camera>/, git-ignored). The proposal is
printed; copying it into scene/bench.j2 (this unit's numbers) and
scene/layout.j2's camera_cfg is yours.
"""

import argparse
import sys
import time
from pathlib import Path

import cv2
import numpy as np
from jinja2 import Template
from dorna_vision_client import VisionClient

PROJ = Path(__file__).resolve().parents[2]

CAMERAS = {"bottom": "camera_serial_station", "top": "camera_serial_robot"}
SWEEP = [60000, 45000, 35000, 27000, 20000, 15000, 11000, 8000, 6000]   # µs
TARGET = 195          # face median to aim for, 0..255 — room above for the band's glare
MAX_CLIPPED = 1.0     # % of the face allowed at CLIP or over
CLIP = 250            # a channel at or over this is clipped (JPEG rounds 255 down a little)
FACE_SHARE = 0.80     # the face: this share of the detector box's half-side, inside the band
WB_FRAMES = 6         # pictures for a "once" white balance to settle on the disc
QUALITY = 95          # JPEG quality of the measured pictures


def bench():
    """This unit's addresses and serials — scene/bench.j2, the one place."""
    return Template((PROJ / "scene" / "bench.j2").read_text()).module


def grab(vc, sn):
    jpeg, _meta = vc.camera_get_img(sn, quality=QUALITY)
    return cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)


def face_mask(shape, box):
    xs = [c[0] for c in box]
    ys = [c[1] for c in box]
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    r = FACE_SHARE * min(max(xs) - min(xs), max(ys) - min(ys)) / 2
    mask = np.zeros(shape[:2], np.uint8)
    cv2.circle(mask, (round(cx), round(cy)), round(r), 255, -1)
    return mask > 0


def measure(img, mask):
    """The face's median brightness (its brightest channel) and the % of it
    clipped in any channel."""
    top = img.max(axis=2)[mask]
    return float(np.median(top)), 100.0 * float((top >= CLIP).mean())


def restore(vc, sn, before):
    """The camera back as `before` (camera_info) found it."""
    exp, gain, wb = before["exposure"], before["gain"], before["wb"]
    if exp["auto"]:
        vc.camera_exposure(sn, auto=True)
    else:
        vc.camera_exposure(sn, us=exp["us"])
    if gain["auto"]:
        vc.camera_gain(sn, auto=True)
    else:
        vc.camera_gain(sn, db=gain["db"])
    if wb["cfg"].get("auto"):
        vc.camera_wb(sn, auto=True)
    else:
        vc.camera_wb(sn, ratios=wb["ratios"])


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("camera", choices=sorted(CAMERAS))
    ap.add_argument("--gain", type=float, default=0.0, help="dB, pinned for the sweep (default 0)")
    ap.add_argument("--sweep", type=int, nargs="+", default=SWEEP, help="exposures to try, µs")
    args = ap.parse_args()

    b = bench()
    sn = str(getattr(b, CAMERAS[args.camera]))
    out = PROJ / "captures" / "exposure" / f"{time.strftime('%Y-%m-%d_%H-%M-%S')}_{args.camera}"
    out.mkdir(parents=True, exist_ok=True)

    vc = VisionClient()
    vc.connect(host=b.vision_ip, port=int(b.vision_port))
    before = vc.camera_info(sn)
    for k in ("exposure", "gain", "wb"):
        if k not in before or "error" in before[k]:
            sys.exit(f"{args.camera} camera {sn}: the vision server reports no {k} "
                     f"({before.get(k)}) — upgrade the vision unit first")
    print(f"{args.camera} camera {sn} now: exposure {before['exposure']}, "
          f"gain {before['gain']}, wb {before['wb']}")

    det = f"exposure_{args.camera}"
    vc.detection_add(det, camera_serial_number=sn, config=str(PROJ / "vision" / "disc_od.yaml"))
    try:
        found = vc.detection_run(det)
        if not found:
            sys.exit(f"no disc found by the {args.camera} camera — put one where the run inspects it")
        best = max(found, key=lambda r: r.get("conf", 0))
        print(f"disc found, conf {best.get('conf', 0):.2f}")

        vc.camera_gain(sn, db=args.gain)
        rows = []
        print(f"\n{'exposure µs':>12} {'face median':>12} {'clipped %':>10}")
        for us in args.sweep:
            vc.camera_exposure(sn, us=us)
            grab(vc, sn)                                  # the first at the new setting, discarded
            img = grab(vc, sn)
            mask = face_mask(img.shape, best["corners"])
            med, clipped = measure(img, mask)
            cv2.imwrite(str(out / f"exposure_{us}.jpg"), img)
            rows.append((us, med, clipped))
            print(f"{us:>12} {med:>12.0f} {clipped:>10.1f}")

        usable = [r for r in rows if r[2] < MAX_CLIPPED]
        if not usable:
            sys.exit(f"\nevery exposure clips over {MAX_CLIPPED} % of the face — try a shorter --sweep")
        us, med, clipped = min(usable, key=lambda r: abs(r[1] - TARGET))

        vc.camera_exposure(sn, us=us)
        vc.camera_wb(sn, once=True)
        for _ in range(WB_FRAMES):
            grab(vc, sn)
        ratios = vc.camera_wb(sn)["wb"]["ratios"]
        cv2.imwrite(str(out / f"chosen_{us}.jpg"), grab(vc, sn))
    finally:
        vc.detection_remove(det)
        restore(vc, sn, before)
        vc.close()

    print(f"\npictures: {out}")
    print(f"camera put back as it was: exposure {before['exposure']}, gain {before['gain']}")
    print(f"\nproposed for the {args.camera} camera {sn} — face median {med:.0f} "
          f"(target {TARGET}), {clipped:.1f} % clipped:")
    print(f"  exposure: {us}        # µs")
    print(f"  gain: {args.gain:g}             # dB")
    print(f"  wb: {{ratios: {{red: {ratios['red']}, green: {ratios['green']}, blue: {ratios['blue']}}}}}")
    if abs(med - TARGET) > 10:
        print(f"  (no sweep step landed within 10 of {TARGET} — narrow --sweep around {us} for a closer one)")


if __name__ == "__main__":
    main()
