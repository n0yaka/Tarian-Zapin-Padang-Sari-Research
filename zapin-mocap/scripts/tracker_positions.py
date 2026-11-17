"""
tracker_positions.py
Live position readout for VIVE Ultimate Trackers through SteamVR (OpenVR).

Prints every tracker's position about 10 times a second, plus how far it has
moved since the script started. Press Ctrl+C to stop.

SETUP ON A SNAPDRAGON (WINDOWS ON ARM) LAPTOP
1. Install the x64 version of Python from python.org
   ("Windows installer (64-bit)"), NOT the ARM64 one.
   Why: SteamVR's openvr_api.dll is built for Intel/AMD (x64).
   ARM64 Python cannot load it; x64 Python runs through Windows' built-in
   translation and can.
2. Check you got the right one:
       py -c "import sysconfig; print(sysconfig.get_platform())"
   It must print win-amd64. If it prints win-arm64, you're using the wrong Python.
   (Don't use platform.machine() for this check: on newer Python it reports the
   laptop's chip, which is always ARM64 here, not which Python you installed.)
3. Install the OpenVR library:
       py -m pip install openvr
4. Start VIVE Hub and SteamVR first (trackers on and green), then run:
       py tracker_positions.py

READING THE NUMBERS
- x / y / z are in centimetres. y is height. Without a headset the starting
  point (0,0,0) is arbitrary, so the raw x/y/z values don't mean much on their own.
- "moved" is the useful number: straight-line distance from where the tracker
  was when the script started.
- Simple accuracy test: lay a tape measure on the floor, start the script with
  the tracker at 0 cm, slide it to 100 cm, and check "moved" reads close to 100.
"""

import time
import openvr

UPDATE_SECONDS = 0.1  # how often to print (0.1 = 10 times a second)


def main():
    vr_system = openvr.init(openvr.VRApplication_Other)
    print("Connected to SteamVR. Move a tracker. Press Ctrl+C to stop.\n")

    start_positions = {}  # serial number -> (x, y, z) when first seen

    try:
        while True:
            poses = vr_system.getDeviceToAbsoluteTrackingPose(
                openvr.TrackingUniverseStanding, 0, openvr.k_unMaxTrackedDeviceCount
            )

            lines = []
            for i in range(openvr.k_unMaxTrackedDeviceCount):
                if vr_system.getTrackedDeviceClass(i) != openvr.TrackedDeviceClass_GenericTracker:
                    continue

                serial = vr_system.getStringTrackedDeviceProperty(
                    i, openvr.Prop_SerialNumber_String
                )
                pose = poses[i]

                if not pose.bPoseIsValid:
                    lines.append(f"{serial}: NOT TRACKING")
                    continue

                m = pose.mDeviceToAbsoluteTracking
                x, y, z = m[0][3], m[1][3], m[2][3]  # metres

                if serial not in start_positions:
                    start_positions[serial] = (x, y, z)
                sx, sy, sz = start_positions[serial]
                moved = ((x - sx) ** 2 + (y - sy) ** 2 + (z - sz) ** 2) ** 0.5

                lines.append(
                    f"{serial}: x={x * 100:7.1f}  y={y * 100:7.1f}  z={z * 100:7.1f} cm"
                    f"   | moved {moved * 100:6.1f} cm"
                )

            print("\n".join(lines) if lines else "No trackers found - are they green in SteamVR?")
            print("-" * 70)
            time.sleep(UPDATE_SECONDS)

    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        openvr.shutdown()


if __name__ == "__main__":
    main()
