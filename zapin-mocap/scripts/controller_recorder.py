"""
controller_recorder.py
Records the Quest 3S headset, controllers and tracked hands from SteamVR (OpenVR)
to a CSV file. Sister script of tracker_recorder.py, with the same CSV layout so
the same analysis works on both.

What it saves, for every device, many times a second:
  - time since the recording started (seconds)
  - position (x, y, z in metres) and rotation (quaternion qw, qx, qy, qz)
  - velocity (m/s) and speed, as reported by SteamVR
  - connected / valid flags and the tracking status
  - a segment number you can change with the M key, to mark parts of a take
  - role: hmd, left_controller, right_controller, left_hand, right_hand

SETUP
  - x64 Python (check: py -c "import sysconfig; print(sysconfig.get_platform())"
    must print win-amd64)
  - py -m pip install openvr
  - Quest connected through ALVR, SteamVR running, headset ON YOUR HEAD
    (the Quest stops tracking the controllers when it goes to sleep)
  - Hold the controllers to record controllers; put them down to record bare hands.
    The Quest tracks one or the other, not both at once.

HOW TO USE
  py controller_recorder.py                    -> recordings/controllers_<date>_<time>.csv
  py controller_recorder.py hand_test_1        -> recordings/hand_test_1_<date>_<time>.csv
  py controller_recorder.py hand_test_1 --hz 72        (change how often it samples)
  py controller_recorder.py hand_test_1 --seconds 60   (stop by itself after 60 s)
  py controller_recorder.py hand_test_1 --no-hmd       (leave the headset out)

  While recording:
    M       = start a new segment (e.g. 0 = standing still, 1 = moving, 2 = still again)
    Ctrl+C  = stop and save

  When you stop, it prints a summary per device: how much of the time it was
  tracked, how many dropouts there were, the return-to-start error and the top
  speed (worked out from the positions, not from SteamVR's velocity).

DIFFERENCES FROM THE VIVE TRACKERS (read before comparing numbers)
  - The data comes over the ALVR stream, so it updates at the Quest's rate
    (about 72 times a second), not 90. Sampling faster only adds repeated rows;
    the summary tells you how many rows were repeats.
  - Controllers and hands drop out when they leave the headset cameras' view.
    The "tracked" percentage and dropout count are the numbers to watch.
  - Positions are in the Quest's own space, not the VIVE tracking map, so they
    do not line up with tracker recordings until the two are calibrated together.
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


# Human-readable names for SteamVR's tracking status codes
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
FREEZE_S = 0.25          # no change at all for this long = the stream has stalled


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


def device_role(vr_system, index, device_class, serial):
    """Work out a plain-language role for a device."""
    if device_class == openvr.TrackedDeviceClass_HMD:
        return "hmd"
    name = serial.lower()
    side = "left" if "left" in name else "right" if "right" in name else None
    if side is None:
        role = vr_system.getControllerRoleForTrackedDeviceIndex(index)
        if role == openvr.TrackedControllerRole_LeftHand:
            side = "left"
        elif role == openvr.TrackedControllerRole_RightHand:
            side = "right"
        else:
            side = "unknown"
    kind = "hand" if "hand" in name else "controller"
    return f"{side}_{kind}"


def find_devices(vr_system, include_hmd):
    """Return {device_index: (serial, role)} for the headset and every controller/hand.

    Devices that exist but are switched off right now are included too, so a
    controller that is picked up part-way through a take still gets recorded.
    """
    devices = {}
    for i in range(openvr.k_unMaxTrackedDeviceCount):
        device_class = vr_system.getTrackedDeviceClass(i)
        if device_class == openvr.TrackedDeviceClass_Controller or (
            include_hmd and device_class == openvr.TrackedDeviceClass_HMD
        ):
            serial = vr_system.getStringTrackedDeviceProperty(i, openvr.Prop_SerialNumber_String)
            devices[i] = (serial, device_role(vr_system, i, device_class, serial))
    return devices


def return_to_start_error(samples):
    """Distance between the average position at the end of the first still period
    and at the end of the last still period.

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


def main():
    parser = argparse.ArgumentParser(
        description="Record Quest headset, controller and hand data from SteamVR to CSV."
    )
    parser.add_argument("take", nargs="?", default="controllers", help="name for this recording")
    parser.add_argument("--hz", type=float, default=90.0, help="samples per second (default 90)")
    parser.add_argument("--seconds", type=float, default=None, help="stop by itself after this long")
    parser.add_argument("--no-hmd", action="store_true", help="do not record the headset")
    args = parser.parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    out_dir = os.path.join(script_dir, "recordings")
    os.makedirs(out_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(out_dir, f"{args.take}_{stamp}.csv")

    vr_system = openvr.init(openvr.VRApplication_Other)
    devices = find_devices(vr_system, include_hmd=not args.no_hmd)
    if not devices:
        print("No headset or controllers found. Is the Quest connected through ALVR?")
        openvr.shutdown()
        return

    print(f"Recording {len(devices)} device(s):")
    for serial, role in devices.values():
        print(f"  {role:<17} {serial}")
    print(f"Saving to: {out_path}")
    if msvcrt:
        print("Press M to start a new segment, Ctrl+C to stop.\n")
    else:
        print("Ctrl+C to stop. (M key segments only work on Windows.)\n")

    # Per-device stats for the end-of-take summary
    stats = {
        idx: {
            "rows": 0, "tracked": 0, "repeats": 0, "frozen": 0, "last_fresh_t": 0.0,
            "dropouts": 0, "in_dropout": False, "dropout_start": 0.0, "longest_dropout": 0.0,
            "samples": [],                 # (t, segment, pos) for tracked frames
            "recent": deque(),             # fresh (t, pos) samples for the speed window
            "prev_pos": None, "max_speed": 0.0,
        }
        for idx in devices
    }

    columns = [
        "time_s", "frame", "segment", "serial", "connected", "valid", "status",
        "x_m", "y_m", "z_m", "qw", "qx", "qy", "qz", "vx_mps", "vy_mps", "vz_mps", "speed_mps",
        "role",
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

            for idx, (serial, role) in devices.items():
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

                writer.writerow([
                    f"{t:.4f}", frame, segment, serial, int(connected), int(valid), status,
                    f"{pos[0]:.5f}", f"{pos[1]:.5f}", f"{pos[2]:.5f}",
                    f"{qw:.6f}", f"{qx:.6f}", f"{qy:.6f}", f"{qz:.6f}",
                    f"{vx:.4f}", f"{vy:.4f}", f"{vz:.4f}", f"{speed:.4f}",
                    role,
                ])

                s = stats[idx]
                s["rows"] += 1
                tracked = connected and valid and status == "OK"

                # A real hand or head is never perfectly still. If the position has not
                # changed at all for FREEZE_S, the stream has stalled (headset asleep or
                # taken off) even though SteamVR still says "OK". Count that as lost.
                is_repeat = (
                    tracked and s["prev_pos"] is not None
                    and distance(pos, s["prev_pos"]) < REPEAT_EPS_M
                )
                if tracked and not is_repeat:
                    s["last_fresh_t"] = t
                frozen = tracked and is_repeat and t - s["last_fresh_t"] > FREEZE_S
                if frozen:
                    s["frozen"] += 1
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
                        # speed from positions, measured across SPEED_WINDOW_S
                        recent = s["recent"]
                        recent.append((t, pos))
                        while len(recent) > 2 and recent[1][0] <= t - SPEED_WINDOW_S:
                            recent.popleft()
                        t_old, pos_old = recent[0]
                        if t - t_old >= SPEED_WINDOW_S * 0.5:
                            s["max_speed"] = max(s["max_speed"], distance(pos, pos_old) / (t - t_old))
                    s["prev_pos"] = pos
                else:
                    if not s["in_dropout"] and s["tracked"] > 0:
                        s["in_dropout"] = True      # only count dropouts after it has tracked once
                        s["dropouts"] += 1
                        s["dropout_start"] = t
                    s["recent"].clear()
                    # keep the position while frozen, so we notice when it moves again
                    s["prev_pos"] = pos if frozen else None

            frame += 1

            # --- short live status once per second ---
            if now - last_status_print >= 1.0:
                f.flush()
                parts = []
                for idx, (serial, role) in devices.items():
                    s = stats[idx]
                    if s["frozen"] and s["in_dropout"] and s["prev_pos"] is not None:
                        parts.append(f"{role}: FROZEN")
                    elif s["in_dropout"] or not s["samples"]:
                        parts.append(f"{role}: LOST")
                    else:
                        moved = distance(s["samples"][-1][2], s["samples"][0][2]) * 100
                        parts.append(f"{role}: {moved:6.1f} cm")
                print(f"[{t:6.1f} s | seg {segment}] " + "  ".join(parts))
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
    for idx, (serial, role) in devices.items():
        s = stats[idx]
        frozen_pct = 100.0 * s["frozen"] / s["rows"]
        if frozen_pct > 50.0:
            print(f"  {role:<17} FROZEN {frozen_pct:.0f}% of the time: positions were not updating."
                  " Is the headset on your head and ALVR streaming?")
            continue
        if not s["samples"]:
            print(f"  {role:<17} never tracked (off, asleep, or out of the cameras' view)")
            continue
        if s["in_dropout"]:
            s["longest_dropout"] = max(s["longest_dropout"], elapsed - s["dropout_start"])
        tracked_pct = 100.0 * s["tracked"] / s["rows"]
        repeat_pct = 100.0 * s["repeats"] / s["tracked"]
        if frozen_pct > 0:
            print(f"  {role:<17} note: frozen (stream stalled) {frozen_pct:.1f}% of the time,"
                  " counted as lost below")
        fresh_rate = (s["tracked"] - s["repeats"]) / elapsed
        error, label = return_to_start_error(s["samples"])
        error_text = f"{error * 100:5.1f} cm ({label})" if error is not None else f"n/a ({label})"
        print(f"  {role:<17} {serial}")
        print(f"      tracked {tracked_pct:5.1f}% of the time | dropouts {s['dropouts']}"
              f" | longest {s['longest_dropout']:.2f} s")
        print(f"      new positions {fresh_rate:5.1f} per second ({repeat_pct:4.1f}% of rows were repeats)")
        print(f"      return-to-start error {error_text}")
        print(f"      top speed {s['max_speed']:4.2f} m/s (from positions)")


if __name__ == "__main__":
    main()
