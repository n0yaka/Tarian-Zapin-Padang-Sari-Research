"""
mocap_recorder.py
Records EVERYTHING SteamVR is tracking into one CSV, with shared timestamps:
  - Quest 3S headset
  - Quest controllers (and Quest hand tracking, when the controllers are put down)
  - VIVE Ultimate Trackers (waist + ankles)

Combines tracker_recorder.py and controller_recorder.py, so a take from both systems can be
compared frame by frame. Same CSV columns as those two, plus `role`, `system` and `glitch`.

SETUP
  - x64 Python (check: py -c "import sysconfig; print(sysconfig.get_platform())"
    must print win-amd64)
  - py -m pip install openvr
  - VIVE Hub running, trackers solid green
  - Quest connected through ALVR, SteamVR running, headset ON YOUR HEAD
    (the Quest stops tracking the controllers when it goes to sleep)

HOW TO USE
  py mocap_recorder.py                      -> recordings/mocap_<date>_<time>.csv
  py mocap_recorder.py combo_test_1         -> recordings/combo_test_1_<date>_<time>.csv
  py mocap_recorder.py combo_test_1 --seconds 60   (stop by itself after 60 s)
  py mocap_recorder.py combo_test_1 --hz 72        (change how often it samples)
  py mocap_recorder.py combo_test_1 --no-hands     (leave out Quest hand tracking)

  While recording:
    M       = start a new segment (e.g. 0 = standing still, 1 = moving, 2 = still again)
    Ctrl+C  = stop and save

WHAT THE SUMMARY TELLS YOU (per device)
  - tracked %: time with a usable position. Counts as LOST: SteamVR not OK, a frozen stream
    (position not changing at all for 0.25 s - Quest devices only), or an impossible jump.
  - dropouts: how many times tracking was lost, and the longest loss.
  - glitches: jumps between samples implying more than 6 m/s (no hand or foot moves that fast).
    SteamVR still calls these "OK", so they are flagged in the `glitch` column instead.
  - return-to-start error: average of the last 2 s of the first and last segments.
  - top speed: worked out from positions, ignoring glitches.

IMPORTANT: THE TWO SYSTEMS ARE NOT ALIGNED
  The VIVE trackers use their own tracking map; the Quest uses its own space. Their positions do
  not line up (different floor height and origin) until they are calibrated together, or aligned
  afterwards in Blender. Compare each system with itself, not tracker position vs controller position.
"""

import argparse
import csv
import math
import os
import time
from collections import deque
from datetime import datetime

import openvr

try:
    import msvcrt  # Windows only: lets us read the M key without pressing Enter
except ImportError:
    msvcrt = None


# Body position of each VIVE tracker, from CLAUDE.md. Left/right ankle not confirmed yet.
TRACKER_PLACES = {
    "67-BH3N01032": "waist",
    "67-BH3N00213": "ankle_00213",
    "67-BH3N00986": "ankle_00986",
}

TRACKING_RESULT_NAMES = {
    1: "UNINITIALIZED",
    100: "CALIBRATING",
    101: "CALIBRATING_OUT_OF_RANGE",
    200: "OK",
    201: "OUT_OF_RANGE",
    300: "ROTATION_ONLY",
}

STILL_WINDOW_S = 2.0     # seconds averaged for the return-to-start error
SPEED_WINDOW_S = 0.05    # speed is measured across this much time, to tame jitter
REPEAT_EPS_M = 1e-6      # a position within this of the previous one counts as a repeat
FREEZE_S = 0.25          # Quest only: no change at all for this long = the stream has stalled
GLITCH_MPS = 6.0         # a jump implying more than this speed is not real movement


def matrix_to_quaternion(m):
    """Convert OpenVR's 3x4 pose matrix rotation part to a quaternion (w, x, y, z)."""
    r00, r01, r02 = m[0][0], m[0][1], m[0][2]
    r10, r11, r12 = m[1][0], m[1][1], m[1][2]
    r20, r21, r22 = m[2][0], m[2][1], m[2][2]
    trace = r00 + r11 + r22
    if trace > 0:
        s = 0.5 / math.sqrt(trace + 1.0)
        w = 0.25 / s
        x = (r21 - r12) * s
        y = (r02 - r20) * s
        z = (r10 - r01) * s
    elif r00 > r11 and r00 > r22:
        s = 2.0 * math.sqrt(1.0 + r00 - r11 - r22)
        w = (r21 - r12) / s
        x = 0.25 * s
        y = (r01 + r10) / s
        z = (r02 + r20) / s
    elif r11 > r22:
        s = 2.0 * math.sqrt(1.0 + r11 - r00 - r22)
        w = (r02 - r20) / s
        x = (r01 + r10) / s
        y = 0.25 * s
        z = (r12 + r21) / s
    else:
        s = 2.0 * math.sqrt(1.0 + r22 - r00 - r11)
        w = (r10 - r01) / s
        x = (r02 + r20) / s
        y = (r12 + r21) / s
        z = 0.25 * s
    return w, x, y, z


def distance(a, b):
    return math.sqrt(sum((a[k] - b[k]) ** 2 for k in range(3)))


def mean_position(points):
    n = len(points)
    return tuple(sum(p[k] for p in points) / n for k in range(3))


def describe_device(vr_system, index, device_class, serial):
    """Return (role, system) in plain language, or None to skip the device."""
    if device_class == openvr.TrackedDeviceClass_GenericTracker:
        return f"tracker_{TRACKER_PLACES.get(serial, serial)}", "vive"
    if device_class == openvr.TrackedDeviceClass_HMD:
        return "hmd", "quest"
    if device_class == openvr.TrackedDeviceClass_Controller:
        name = serial.lower()
        side = "left" if "left" in name else "right" if "right" in name else None
        if side is None:
            role = vr_system.getControllerRoleForTrackedDeviceIndex(index)
            side = {openvr.TrackedControllerRole_LeftHand: "left",
                    openvr.TrackedControllerRole_RightHand: "right"}.get(role, "unknown")
        kind = "hand" if "hand" in name else "controller"
        return f"{side}_{kind}", "quest"
    return None


def find_devices(vr_system, include_hands):
    """Return {device_index: (serial, role, system)} for everything worth recording.

    Devices that exist but are off right now are included too, so a controller that is
    picked up part-way through a take still gets recorded.
    """
    devices = {}
    for i in range(openvr.k_unMaxTrackedDeviceCount):
        device_class = vr_system.getTrackedDeviceClass(i)
        if device_class == openvr.TrackedDeviceClass_Invalid:
            continue
        serial = vr_system.getStringTrackedDeviceProperty(i, openvr.Prop_SerialNumber_String)
        described = describe_device(vr_system, i, device_class, serial)
        if described is None:
            continue
        role, system = described
        if role.endswith("_hand") and not include_hands:
            continue
        devices[i] = (serial, role, system)
    # Quest first, then trackers, so the printout is easy to read
    return dict(sorted(devices.items(), key=lambda kv: (kv[1][2] != "quest", kv[1][1])))


def return_to_start_error(samples):
    """Distance between the average position at the end of the first still period and
    at the end of the last still period.

    samples: list of (time_s, segment, (x, y, z)) for tracked frames only.
    With M-key segments: last 2 s of the first segment vs last 2 s of the last segment.
    Without segments: first 2 s of the take vs last 2 s of the take.
    Returns (error_m, description) or (None, reason).
    """
    if not samples:
        return None, "never tracked"
    segments = sorted({s[1] for s in samples})
    if len(segments) >= 2:
        first = [s for s in samples if s[1] == segments[0]]
        last = [s for s in samples if s[1] == segments[-1]]
        a = [s[2] for s in first if s[0] >= first[-1][0] - STILL_WINDOW_S]
        b = [s[2] for s in last if s[0] >= last[-1][0] - STILL_WINDOW_S]
        label = f"segment {segments[0]} vs segment {segments[-1]}"
    else:
        t0, t1 = samples[0][0], samples[-1][0]
        if t1 - t0 < 2 * STILL_WINDOW_S:
            return None, "take too short"
        a = [s[2] for s in samples if s[0] <= t0 + STILL_WINDOW_S]
        b = [s[2] for s in samples if s[0] >= t1 - STILL_WINDOW_S]
        label = "first 2 s vs last 2 s (no segments marked)"
    if not a or not b:
        return None, "not tracked during a still period"
    return distance(mean_position(a), mean_position(b)), label


def new_stats():
    return {
        "rows": 0, "tracked": 0, "repeats": 0, "frozen": 0, "glitches": 0,
        "dropouts": 0, "in_dropout": False, "dropout_start": 0.0, "longest_dropout": 0.0,
        "samples": [],                 # (t, segment, pos) for tracked frames
        "recent": deque(),             # fresh (t, pos) samples for the speed window
        "prev_pos": None,              # last position seen (for repeat / freeze checks)
        "last_good": None,             # (t, pos) of the last accepted fresh sample (glitch check)
        "last_fresh_t": 0.0, "max_speed": 0.0,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Record the Quest headset, controllers and VIVE trackers from SteamVR to one CSV."
    )
    parser.add_argument("take", nargs="?", default="mocap", help="name for this recording")
    parser.add_argument("--hz", type=float, default=90.0, help="samples per second (default 90)")
    parser.add_argument("--seconds", type=float, default=None, help="stop by itself after this long")
    parser.add_argument("--no-hands", action="store_true", help="leave out Quest hand tracking")
    args = parser.parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    out_dir = os.path.join(script_dir, "recordings")
    os.makedirs(out_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(out_dir, f"{args.take}_{stamp}.csv")

    vr_system = openvr.init(openvr.VRApplication_Other)
    devices = find_devices(vr_system, include_hands=not args.no_hands)
    if not devices:
        print("Nothing found in SteamVR. Is the Quest connected through ALVR and are the trackers green?")
        openvr.shutdown()
        return

    n_vive = sum(1 for d in devices.values() if d[2] == "vive")
    n_quest = len(devices) - n_vive
    print(f"Recording {n_quest} Quest device(s) and {n_vive} VIVE tracker(s):")
    for serial, role, system in devices.values():
        print(f"  {system:<6} {role:<20} {serial}")
    if n_vive == 0:
        print("  (no VIVE trackers found - is VIVE Hub running?)")
    print(f"Saving to: {out_path}")
    print("Note: Quest and VIVE positions are in different spaces and do not line up.")
    if msvcrt:
        print("Press M to start a new segment, Ctrl+C to stop.\n")
    else:
        print("Ctrl+C to stop. (M key segments only work on Windows.)\n")

    stats = {idx: new_stats() for idx in devices}

    columns = [
        "time_s", "frame", "segment", "serial", "connected", "valid", "status",
        "x_m", "y_m", "z_m", "qw", "qx", "qy", "qz", "vx_mps", "vy_mps", "vz_mps", "speed_mps",
        "role", "system", "glitch",
    ]

    interval = 1.0 / args.hz
    segment = 0
    frame = 0
    start = time.perf_counter()
    next_tick = start
    last_status_print = start

    f = open(out_path, "w", newline="")
    writer = csv.writer(f)
    writer.writerow(columns)

    try:
        while True:
            # --- key handling (M = new segment) ---
            if msvcrt and msvcrt.kbhit():
                key = msvcrt.getwch()
                if key.lower() == "m":
                    segment += 1
                    print(f"  >> Segment {segment} started at {time.perf_counter() - start:.2f} s")

            now = time.perf_counter()
            t = now - start
            if args.seconds is not None and t >= args.seconds:
                break

            poses = vr_system.getDeviceToAbsoluteTrackingPose(
                openvr.TrackingUniverseStanding, 0, openvr.k_unMaxTrackedDeviceCount
            )

            for idx, (serial, role, system) in devices.items():
                pose = poses[idx]
                status = TRACKING_RESULT_NAMES.get(int(pose.eTrackingResult), str(int(pose.eTrackingResult)))
                connected = bool(pose.bDeviceIsConnected)
                valid = bool(pose.bPoseIsValid)
                m = pose.mDeviceToAbsoluteTracking
                pos = (m[0][3], m[1][3], m[2][3])
                qw, qx, qy, qz = matrix_to_quaternion(m)
                vel = getattr(pose.vVelocity, "v", pose.vVelocity)  # works across pyopenvr versions
                vx, vy, vz = vel[0], vel[1], vel[2]
                speed = math.sqrt(vx * vx + vy * vy + vz * vz)

                s = stats[idx]
                s["rows"] += 1
                tracked = connected and valid and status == "OK"
                glitch = False

                is_repeat = (
                    tracked and s["prev_pos"] is not None
                    and distance(pos, s["prev_pos"]) < REPEAT_EPS_M
                )
                if tracked and not is_repeat:
                    s["last_fresh_t"] = t

                # Quest only: a stalled stream keeps reporting the last position as "OK".
                frozen = (system == "quest" and tracked and is_repeat
                          and t - s["last_fresh_t"] > FREEZE_S)
                if frozen:
                    s["frozen"] += 1
                    tracked = False

                # Impossible jump since the last accepted sample = glitch, not movement.
                if tracked and not is_repeat and s["last_good"] is not None:
                    t_prev, pos_prev = s["last_good"]
                    if t > t_prev and distance(pos, pos_prev) / (t - t_prev) > GLITCH_MPS:
                        glitch = True
                        s["glitches"] += 1
                        tracked = False

                if tracked:
                    s["tracked"] += 1
                    if s["in_dropout"]:
                        s["in_dropout"] = False
                        s["longest_dropout"] = max(s["longest_dropout"], t - s["dropout_start"])
                    s["samples"].append((t, segment, pos))
                    if is_repeat:
                        s["repeats"] += 1          # same position as last poll: no new data yet
                    else:
                        s["last_good"] = (t, pos)
                        recent = s["recent"]
                        recent.append((t, pos))
                        while len(recent) > 2 and recent[1][0] <= t - SPEED_WINDOW_S:
                            recent.popleft()
                        t_old, pos_old = recent[0]
                        if t - t_old >= SPEED_WINDOW_S * 0.5:
                            s["max_speed"] = max(s["max_speed"], distance(pos, pos_old) / (t - t_old))
                else:
                    if not s["in_dropout"] and s["tracked"] > 0:
                        s["in_dropout"] = True      # only count dropouts after it has tracked once
                        s["dropouts"] += 1
                        s["dropout_start"] = t
                    s["recent"].clear()
                    if not glitch and not frozen:
                        s["last_good"] = None       # real loss: restart the glitch check afterwards
                s["prev_pos"] = pos if (connected and valid) else None

                writer.writerow([
                    f"{t:.4f}", frame, segment, serial, int(connected), int(valid), status,
                    f"{pos[0]:.5f}", f"{pos[1]:.5f}", f"{pos[2]:.5f}",
                    f"{qw:.6f}", f"{qx:.6f}", f"{qy:.6f}", f"{qz:.6f}",
                    f"{vx:.4f}", f"{vy:.4f}", f"{vz:.4f}", f"{speed:.4f}",
                    role, system, int(glitch),
                ])

            frame += 1

            # --- short live status once per second ---
            if now - last_status_print >= 1.0:
                f.flush()
                parts = []
                for idx, (serial, role, system) in devices.items():
                    s = stats[idx]
                    short = role.replace("tracker_", "").replace("_controller", "_ctrl")
                    if s["in_dropout"] or not s["samples"]:
                        parts.append(f"{short}:LOST")
                    else:
                        moved = distance(s["samples"][-1][2], s["samples"][0][2]) * 100
                        parts.append(f"{short}:{moved:5.1f}")
                print(f"[{t:6.1f} s | seg {segment}] cm moved  " + "  ".join(parts))
                last_status_print = now

            # --- keep a steady sample rate ---
            next_tick += interval
            sleep_for = next_tick - time.perf_counter()
            if sleep_for > 0:
                time.sleep(sleep_for)
            else:
                next_tick = time.perf_counter()  # running behind; don't try to catch up

    except KeyboardInterrupt:
        pass
    finally:
        f.close()
        openvr.shutdown()

    elapsed = time.perf_counter() - start
    print("\nStopped.")
    if frame == 0:
        print("No frames recorded.")
        return
    print(f"Saved {frame} frames in {elapsed:.1f} s (actual rate {frame / elapsed:.1f} per second)")
    print(f"File: {out_path}\n")
    print("SUMMARY PER DEVICE")
    current_system = None
    for idx, (serial, role, system) in devices.items():
        if system != current_system:
            print(f"\n  --- {'QUEST' if system == 'quest' else 'VIVE TRACKERS'} ---")
            current_system = system
        s = stats[idx]
        frozen_pct = 100.0 * s["frozen"] / s["rows"]
        if frozen_pct > 50.0:
            print(f"  {role:<20} FROZEN {frozen_pct:.0f}% of the time: positions were not updating."
                  " Is the headset on your head and ALVR streaming?")
            continue
        if not s["samples"]:
            print(f"  {role:<20} never tracked (off, asleep, or lost)")
            continue
        if s["in_dropout"]:
            s["longest_dropout"] = max(s["longest_dropout"], elapsed - s["dropout_start"])
        tracked_pct = 100.0 * s["tracked"] / s["rows"]
        repeat_pct = 100.0 * s["repeats"] / s["tracked"]
        fresh_rate = (s["tracked"] - s["repeats"]) / elapsed
        error, label = return_to_start_error(s["samples"])
        error_text = f"{error * 100:5.1f} cm ({label})" if error is not None else f"n/a ({label})"
        print(f"  {role:<20} {serial}")
        print(f"      tracked {tracked_pct:5.1f}% | dropouts {s['dropouts']}"
              f" | longest {s['longest_dropout']:.2f} s | glitches (>{GLITCH_MPS:.0f} m/s) {s['glitches']}")
        if frozen_pct > 0:
            print(f"      frozen (stream stalled) {frozen_pct:.1f}% of the time, counted as lost")
        print(f"      new positions {fresh_rate:5.1f} per second ({repeat_pct:4.1f}% of rows were repeats)")
        print(f"      return-to-start error {error_text}")
        print(f"      top speed {s['max_speed']:4.2f} m/s (from positions, glitches excluded)")


if __name__ == "__main__":
    main()
