"""
tracker_recorder.py
Records VIVE Ultimate Tracker data from SteamVR (OpenVR) to a CSV file.

What it saves, for every tracker, many times a second:
  - time since the recording started (seconds)
  - position (x, y, z in metres) and rotation (quaternion qw, qx, qy, qz)
  - velocity (m/s) and speed
  - tracking status: OK, OUT_OF_RANGE, ROTATION_ONLY, etc.
    (more detailed than the simple "valid" flag the test script used)
  - a segment number you can change with the M key, to mark parts of a take

SETUP: same as tracker_positions.py
  - x64 Python (check: py -c "import sysconfig; print(sysconfig.get_platform())"
    must print win-amd64)
  - py -m pip install openvr
  - VIVE Hub and SteamVR running, trackers green

HOW TO USE
  py tracker_recorder.py                 -> saves recordings/take_<date>_<time>.csv
  py tracker_recorder.py ankle_test_1    -> saves recordings/ankle_test_1_<date>_<time>.csv
  py tracker_recorder.py ankle_test_1 --hz 60   (change how often it samples)

  While recording:
    M       = start a new segment (e.g. 0 = on the mark, 1 = dancing, 2 = back on the mark)
    Ctrl+C  = stop and save

  When you stop, it prints a summary per tracker, including the
  start-to-end distance (the return-to-start error) and how much of the time
  tracking was fully OK.
"""

import argparse
import csv
import math
import os
import sys
import time
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


def find_trackers(vr_system):
    """Return {device_index: serial} for every connected generic tracker."""
    trackers = {}
    for i in range(openvr.k_unMaxTrackedDeviceCount):
        if vr_system.getTrackedDeviceClass(i) == openvr.TrackedDeviceClass_GenericTracker:
            trackers[i] = vr_system.getStringTrackedDeviceProperty(
                i, openvr.Prop_SerialNumber_String
            )
    return trackers


def distance(a, b):
    return math.sqrt(sum((a[k] - b[k]) ** 2 for k in range(3)))


def main():
    parser = argparse.ArgumentParser(description="Record VIVE tracker data from SteamVR to CSV.")
    parser.add_argument("take", nargs="?", default="take", help="name for this recording")
    parser.add_argument("--hz", type=float, default=90.0, help="samples per second (default 90)")
    args = parser.parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    out_dir = os.path.join(script_dir, "recordings")
    os.makedirs(out_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(out_dir, f"{args.take}_{stamp}.csv")

    vr_system = openvr.init(openvr.VRApplication_Other)
    trackers = find_trackers(vr_system)
    if not trackers:
        print("No trackers found. Are they green in SteamVR?")
        openvr.shutdown()
        return

    print(f"Recording {len(trackers)} tracker(s): {', '.join(trackers.values())}")
    print(f"Saving to: {out_path}")
    if msvcrt:
        print("Press M to start a new segment, Ctrl+C to stop.\n")
    else:
        print("Ctrl+C to stop. (M key segments only work on Windows.)\n")

    # Per-tracker stats for the end-of-take summary
    stats = {
        serial: {"rows": 0, "ok": 0, "first": None, "last": None, "max_moved": 0.0, "max_speed": 0.0}
        for serial in trackers.values()
    }

    columns = [
        "time_s", "frame", "segment", "serial", "connected", "valid", "status",
        "x_m", "y_m", "z_m", "qw", "qx", "qy", "qz", "vx_mps", "vy_mps", "vz_mps", "speed_mps",
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
            poses = vr_system.getDeviceToAbsoluteTrackingPose(
                openvr.TrackingUniverseStanding, 0, openvr.k_unMaxTrackedDeviceCount
            )

            for idx, serial in trackers.items():
                pose = poses[idx]
                status = TRACKING_RESULT_NAMES.get(int(pose.eTrackingResult), str(int(pose.eTrackingResult)))
                valid = bool(pose.bPoseIsValid)
                m = pose.mDeviceToAbsoluteTracking
                x, y, z = m[0][3], m[1][3], m[2][3]
                qw, qx, qy, qz = matrix_to_quaternion(m)
                vel = getattr(pose.vVelocity, "v", pose.vVelocity)  # works across pyopenvr versions
                vx, vy, vz = vel[0], vel[1], vel[2]
                speed = math.sqrt(vx * vx + vy * vy + vz * vz)

                writer.writerow([
                    f"{t:.4f}", frame, segment, serial, int(pose.bDeviceIsConnected), int(valid), status,
                    f"{x:.5f}", f"{y:.5f}", f"{z:.5f}",
                    f"{qw:.6f}", f"{qx:.6f}", f"{qy:.6f}", f"{qz:.6f}",
                    f"{vx:.4f}", f"{vy:.4f}", f"{vz:.4f}", f"{speed:.4f}",
                ])

                s = stats[serial]
                s["rows"] += 1
                if status == "OK":
                    s["ok"] += 1
                if valid:
                    pos = (x, y, z)
                    if s["first"] is None:
                        s["first"] = pos
                    s["last"] = pos
                    s["max_moved"] = max(s["max_moved"], distance(pos, s["first"]))
                    s["max_speed"] = max(s["max_speed"], speed)

            frame += 1

            # --- short live status once per second ---
            if now - last_status_print >= 1.0:
                f.flush()
                parts = []
                for serial in trackers.values():
                    s = stats[serial]
                    moved = distance(s["last"], s["first"]) * 100 if s["first"] else float("nan")
                    parts.append(f"{serial[-5:]}: moved {moved:6.1f} cm")
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
    print(f"Saved {frame} frames in {elapsed:.1f} s (actual rate {frame / elapsed:.1f} per second)")
    print(f"File: {out_path}\n")
    print("SUMMARY PER TRACKER")
    for serial in trackers.values():
        s = stats[serial]
        if s["first"] is None:
            print(f"  {serial}: never had a valid position")
            continue
        ok_pct = 100.0 * s["ok"] / s["rows"] if s["rows"] else 0.0
        end_error = distance(s["last"], s["first"]) * 100
        print(
            f"  {serial}: start-to-end {end_error:5.1f} cm | furthest {s['max_moved'] * 100:6.1f} cm"
            f" | top speed {s['max_speed']:4.2f} m/s | tracking OK {ok_pct:5.1f}% of the time"
        )


if __name__ == "__main__":
    main()
