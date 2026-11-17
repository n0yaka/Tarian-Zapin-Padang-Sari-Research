# Zapin Padang Sari VR: motion capture pipeline

Context file for Claude Code. Summarises a Claude chat from 19 Oct to 2 Nov 2026 about setting up
VIVE Ultimate Trackers, testing them, and getting the motion into Blender. Last updated 2 Nov 2026.

## How to work with me

- Be critical. Don't just agree with my suggestions; point out problems directly.
- If a request is unclear, ask before building.
- When you suggest something, give the reasons as bullet points.
- Use plain language someone outside the field can follow.
- Prefer tables and short point-form explanations over long paragraphs.
- Cite papers in APA 7th style.
- For research claims, stick to my sources and say when something isn't covered.

## Project

- Final year project (Computer Science, Multimedia Computing, Universiti Malaya): a VR app for
  learning Zapin Padang Sari, a Malay folk dance from Batu Pahat, Johor.
- Target: a standalone app on **Meta Quest 3S** (older notes say Quest 3; this project uses the 3S).
- Motion capture plan: record an expert dancer with 3 VIVE Ultimate Trackers, put the motion on a 3D
  avatar in Blender, then use it in the VR app. **The trackers are for recording only**; app users won't
  wear them.
- From the dance expert (Dr Khairi):
  - Zapin is taught feet first, then hands, then the full ragam. Padang Sari has four ragam.
  - Hands are the hardest part to capture; even his professional motion capture made the hands look stiff.
  - He photographs each movement as a manual reference, because fast gestures get missed on video.
  - Costume: baju Melayu cekak musang with samping and songkok (men), baju kurung (women).

## Hardware

| Item | Details |
|---|---|
| Desktop | Windows 11 (main OS), Ryzen 7 7800X3D, RX 9070 XT 16 GB, 32 GB RAM. The RX 9070 XT is on Meta's supported list for Link. |
| Laptop | ASUS Zenbook A14, Snapdragon X1 (ARM64), Windows 11 on ARM |
| Headset | Meta Quest 3S |
| Trackers | 3 × VIVE Ultimate Tracker + 1 VIVE Wireless Dongle (one dongle takes up to 5 trackers) |

| Tracker serial | Worn on | Notes |
|---|---|---|
| 67-BH3N01032 | Waist (hip) | Tilts a lot while dancing; see test results |
| 67-BH3N00213 | Ankle | Left/right **not confirmed yet** |
| 67-BH3N00986 | Ankle | Left/right **not confirmed yet** |

Waist plus both ankles is HTC's recommended placement for three trackers.

## What runs where

| Task | Zenbook (ARM) | Desktop |
|---|---|---|
| VIVE Hub, pairing, room map | ✅ Works | ✅ |
| SteamVR with trackers, no headset (null driver) | ✅ Works | Should work (not tried) |
| Recording trackers with Python (x64 Python) | ✅ 90 Hz, no gaps over 30 ms | Not tried |
| Quest 3S as a PC VR headset (Meta Horizon Link) | ❌ Link doesn't support Snapdragon | ✅ |
| Quest controllers in SteamVR | ❌ Needs Link; Steam Link / Virtual Desktop on Snapdragon unconfirmed | ✅ Via Link + VIVE Space Calibrator |
| Mixed Reality Link (Windows desktop inside the headset) | ✅ Virtual monitors only, no PC VR | |
| Sending Unity builds to the Quest over USB (ADB) | Untested; the Oculus ADB driver on ARM is the unknown | ✅ |

Main advantage of the Zenbook: it can record the trackers **without the headset**, so the setup can travel
to the dance studio.

## Zenbook setup (done)

### SteamVR without a headset (null driver)

Back up both files first. Paths are inside the SteamVR folder (Steam > Library > right-click SteamVR >
Manage > Browse local files).

- `drivers\null\resources\settings\default.vrsettings`: `"enable": true,`
- `resources\settings\default.vrsettings`, inside the `"steamvr"` section:
  `"requireHmd": false,` `"forcedDriver": "null",` `"activateMultipleDrivers": true,`
- Recommended: SteamVR Settings > Video > Show Advanced Settings > "Pause VR when headset is idle" = Off.
- When it works, the status window shows "Standing by" plus the tracker icons.
- SteamVR updates can overwrite these edits. Restore the backups before using a real headset with SteamVR
  on this laptop.
- "Game already running" when launching means a stuck SteamVR process. Press Stop in Steam, or end every
  `vr*.exe` in Task Manager > Details.

### Python on Windows on ARM

- Use **x64 Python** (python.org "Windows installer (64-bit)"), not ARM64. SteamVR's `openvr_api.dll` is
  x64, and ARM64 Python can't load it.
- Check: `py -c "import sysconfig; print(sysconfig.get_platform())"` must print `win-amd64`.
  Don't use `platform.machine()`; it reports the chip, which is always ARM64 here.
- `python` isn't on PATH on this laptop, so use the `py` launcher: `py -m pip install openvr`,
  `py tracker_recorder.py`.

## Tracker setup notes (VIVE Hub)

- **Pairing:** Settings > VIVE Ultimate Tracker > Trackers > Pair new. Turn the tracker on, wait for solid
  blue, then hold power for 2 s (flashing blue = pairing mode). Pair one at a time and label each tracker.
- **Restart:** hold power 5 s to turn off, 1 s to turn on (hold 10 s to force off).
- **Firmware:** Settings > About > VIVE Ultimate Trackers > Update. One tracker at a time, over USB-C.
- **Room map:** turn on only one tracker (the ★ one), then Tracker setup > Create tracking map. Turn the
  others on afterwards and the map copies over. Redo the map whenever the room changes. The map is tied to
  the room; pairing is tied to the dongle.
- **Power frequency:** 50 Hz (Malaysia), under Advanced.
- **LED:** breathing green = syncing map, flashing blue = pairing, blue = connecting, green = connected,
  flashing red = low battery.
- **Environment (HTC's guidance):**
  - bright enough to read a book, with no direct glare
  - no reflective objects (cover studio mirrors)
  - walls and floor not completely blank
  - keep at least 1.5 m from obstacles
  - stay near the spot where the map was scanned
  - clothes must not cover the cameras (watch the baju Melayu sleeves and the samping at the waist)
- **Lost ankle tracker:** hold it at chest height with the cameras facing out until it tracks, then
  lower it slowly back onto the ankle.
- The waist tracker can go on the lower back; HTC says this helps when the dancer bends forward.

## Folder layout

```
CLAUDE.md
scripts/
  tracker_positions.py           live position readout (quick checks)
  tracker_recorder.py            records trackers to CSV
  import_trackers_to_blender.py  CSV -> animated markers in Blender
  attach_trackers_to_avatar.py   markers -> avatar skeleton, optional bake
results/
  fig1_return_to_start_error.png
  fig2_height_and_jolts.png
  tracker_test_summary_2026-10-26.csv
  take2_stick_preview.gif
```

The raw takes are on the Zenbook in `recordings/` next to the recorder:
`take_20261026_133335.csv`, `take_20261026_133453.csv`, `take_20261026_133624.csv`.

## Scripts

### tracker_positions.py

Prints each tracker's position about 10 times a second, plus how far it has moved since the script
started. Use it for quick checks, like lifting the left foot to find out which ankle tracker is left.

### tracker_recorder.py

- Run: `py tracker_recorder.py <take_name> [--hz 90]`. Saves `recordings/<take_name>_<date>_<time>.csv`.
- Keys: **M** starts a new segment (0 = standing still, 1 = dancing, 2 = standing still again).
  **Ctrl+C** stops and prints a summary.
- CSV columns: `time_s, frame, segment, serial, connected, valid, status, x_m, y_m, z_m, qw, qx, qy, qz,
  vx_mps, vy_mps, vz_mps, speed_mps`. One row per tracker per frame. Positions are in metres in SteamVR
  axes (Y up); the quaternion is in w, x, y, z order.
- **Known issues (to fix):**
  - `status` was "OK" 100% of the time, even during visible glitches, so it's useless for spotting
    problems. Detect glitches from the positions instead.
  - The velocity columns lag the positions by about 55–65 ms and under-read peaks. The summary's
    "top speed" therefore reads too low: 2.7–4.8 m/s, versus 4.7–7.3 m/s from positions. Compute speed
    from positions.
  - The summary's "start-to-end" uses single samples. Better: the average of the last 2 s of the first
    and last segments.
  - About 1% of frames repeat the previous position (polling timing vs tracker updates), and the next
    frame catches up. Harmless, but drop repeats before analysis.
  - Timestamps are poll times, not tracker sample times.

### import_trackers_to_blender.py

Works in Blender 4.2+. Tested headless in Blender 4.2 LTS and 5.2 LTS on all three takes.

- **Settings:**
  - `CSV_PATH`
  - `FPS` (60)
  - `HIP_SERIAL`, `ANKLE_SERIALS`
  - `LEFT_ANKLE_SERIAL` / `RIGHT_ANKLE_SERIAL` (`None` until confirmed)
  - `ANKLE_TRACKER_HEIGHT` (0.10 m)
  - `SMOOTH` (5-sample median filter, removes the landing jolts)
- **What it does:**
  - Drops repeated frames, optionally smooths, then resamples to `FPS` (linear for position, slerp for
    rotation).
  - Converts the axes and creates empties: `Tracker_Hip`, plus `Tracker_Ankle_L` / `Tracker_Ankle_R`
    (or `Tracker_Ankle_<serial>` if left/right isn't set).
  - The empties sit under `Mocap_Align`: hidden and fixed. It puts the floor at z = 0, the start spot at
    the origin, and the dancer facing −Y once left/right is known.
  - `Mocap_Align` sits under `Mocap_Root`, which is at the origin; move, rotate or scale it to fit the
    avatar.
  - Adds timeline markers: `calibrate`, `dance start`, `dance end`.
  - Importing a new take replaces the previous take's markers.
- **Verified:** markers match the CSV to about 1 mm, and body distances are preserved.

### attach_trackers_to_avatar.py

- **Settings:**
  - `ARMATURE_NAME`
  - `BONES`: hips, plus thigh, shin and foot for each leg. Defaults are Mixamo names.
  - `FEET`: `"flat"` or `"follow"`
  - `HIP_TILT` (1.0)
  - `HIP_DROP` (0.05 m)
  - `BAKE`
- **Needs:**
  - the import already run, with left/right set
  - the avatar in its rest pose at the origin, on the floor, facing −Y
  - its scale applied, and no other animation on it
- **Helper empties it creates:**
  - `Target_Hips`: follows the hip tracker, lowered by `HIP_DROP`
  - `Target_HipsUpright`: position and heading only
  - `Target_Foot_L/R`: start at the foot bones and follow the ankle trackers
  - `Target_FootFlat_L/R`: the rest tilt plus the tracker's heading
  - `Pole_L/R`: 0.5 m in front of each knee, parented to the upright hips
- **Bone constraints:**
  - Hips: Copy Transforms from `Target_HipsUpright`, plus Copy Rotation from `Target_Hips` with
    influence = `HIP_TILT`.
  - Shins: IK with chain length 2 and an automatic pole angle, targeting `Target_Foot`.
  - Feet: Copy Rotation from `Target_FootFlat` (flat) or `Target_Foot` (follow).
- **Output:**
  - Prints the leg over-stretch percentage and the hip-tracker tilt, with advice.
  - `BAKE` uses `nla.bake` with visual keying and clears the constraints. It handles the Blender 4.x vs
    5.x difference in bone selection.
- Works for plain skeletons (built by hand, or from Mixamo). **Not for Rigify control rigs.**
- **Results on a stand-in skeleton:**
  - Nothing jumps at the calibrate frame (when `HIP_DROP` is 0).
  - Feet sit on their targets: median 0 mm.
  - Knees bend forward 99.4–99.9% of the time.
  - Flat feet: 0° tilt change. Follow feet: median 10.6° and 95th percentile 46° of tilt.
  - Over-stretched legs: 22–39% of frames without `HIP_DROP`. With 0.05 m: about 4% in takes 1–2 and 11%
    in take 3. With 0.08 m: 1–2% and 5%.

## Coordinates and conventions

- SteamVR/OpenVR: right-handed, +Y up, −Z forward. Blender: +Z up, and characters face −Y.
- Conversion: rotate +90° about X, so (x, y, z) becomes (x, −z, y). For rotations,
  q_blender = qc · q · qc⁻¹, where qc is 90° about X.
- Without a headset, the tracking map's origin and facing direction are arbitrary. In the tests, the
  map's zero height was about 30–31 cm below the real floor.

## Test results (26 Oct 2026, Zenbook, 3 takes of about 60 s of semi-fast dancing)

| Take | Ankle …00213 return error | Ankle …00986 return error | Hip return error | Notes |
|---|---|---|---|---|
| 1 (13:33) | 1.4 cm | 1.2 cm | 3.4 cm | No segment marks; first/last 3 s used as the still periods |
| 2 (13:34) | 0.5 cm | 0.8 cm | 3.3 cm | |
| 3 (13:36) | 7.5 cm | 4.7 cm | 4.8 cm | All three shifted the same way. Cause unknown: stood off the mark (not remembered) or the whole map shifted |

- **Return error** = distance between the average position in the last 2 s of the starting still period
  and the last 2 s of the ending still period. The hip also shifts with posture, so the ankles are the
  cleaner measure. Ankle values were confirmed within about 0.2 cm using medians instead of means.
- Recording rate held at about 89.9 samples/s, with no gaps over 30 ms.
- **Jolts:** 3–7 cm jumps lasting 1–3 frames, mostly right after a foot lands. Ankle trackers only; the hip
  had none. Detection method: drop repeated frames, then flag points more than 3 cm off a 9-sample median
  path. Counts depend on the method (3–27 per ankle per take with this one, 0–12 with a 5-sample moving
  average), so treat counts as rough.
- High alternating leg lifts (up to about 0.9 m above standing height) tracked smoothly.
- **Hip tracker tilt vs standing:** median 7–11°, above 17–27° 10% of the time, peaks 38–51°. Probably the
  tracker rocking on its strap rather than the pelvis. Strap it tighter, or try the lower back.
- **Earlier tests (21–23 Oct):**
  - Trackers left still wobbled about 1 mm.
  - A tracker walked about 2 m out and back by hand returned within 0.6–0.7 cm.
  - In one fast-circles test, a tracker lost tracking and didn't return to zero.

## Recording protocol (for the real capture)

- Mark segments with M: standing still, dancing, standing still again.
- Tape outlines for **both** feet, and film the feet with a phone at the start and end of every take.
- Repeat each test 3–5 times. Report all takes, including bad ones like take 3; dropping them is
  cherry-picking.
- Strap the trackers firmly, label them L/R, and use the same ankle every session.
- Record near the map's scan spot, cover mirrors, and check the lighting.
- **Floor-reference test (not done yet):** keep one tracker fixed on the floor during a take. If it
  "moves", the map shifted; if it doesn't, the dancer stood in a different spot. This answers the take 3
  question.
- Check the "actual rate" in the recorder's summary (it should be about 90/s).

## Arms and hands (deferred: "skip the hands for now")

| Option | Captures | Zenbook? | Main problem |
|---|---|---|---|
| A. Quest controllers | Hand position and rotation, plus the head from the headset | No, desktop only | Dancer must wear the headset (it tracks the controllers); dropouts when hands leave its view (e.g. kopak squats); no fingers; two tracking systems to align |
| B. 2 more Ultimate Trackers on the wrists | Forearm position and rotation | Yes | Cost; no wrist bend or fingers; cameras must stay clear of the sleeves |
| C. Quest hand tracking | Fingers too | No, desktop only | Steam Link hand tracking is basic and per-app; fast moves and out-of-view hands drop out |
| D. Photos and video, fingers animated by hand in Blender | Hand shapes | n/a | Manual work, but it matches Dr Khairi's photo-per-movement practice |

Suggested so far: **B for the arms plus D for the hands**. Not decided yet.

- Ask Dr Khairi whether hand shapes and finger positions matter in Padang Sari.
- Ask the supervisor about budget for 2 more trackers.

## Limitations of 3-tracker capture

- Only the pelvis and ankles are measured. The knees come from IK (estimated). The spine, head, arms and
  hands aren't captured.
- Foot tilt (heel lifts, pointed toes) isn't measured, because the tracker sits above the ankle joint.
- Precision reference: about 5 mm on average (Kulozik & Jarrassé, 2024).
- The avatar animation needs an expert check. Rizhan et al. (2025) found experts rated raw motion
  templates 92–100%, but the same motion on a 3D avatar only 68.6% (Rodat) and 74.5% (Zapin Terengganu).

## Open items / next steps

1. Confirm which ankle tracker is left (foot-lift test with `tracker_positions.py`). Data cross-check: if
   the hip tracker was worn on the front of the waist, …00213 is the left ankle.
2. Confirm the avatar's rig type and bone names, then run the attach script on the real avatar.
3. Do the floor-reference test to settle the take 3 question.
4. Do the ankle test properly: outlines for both feet, phone video, 3–5 repeats.
5. Fix the `tracker_recorder.py` summary: speed from positions, segment-averaged start-to-end, drop
   repeats, glitch detection from positions.
6. Decide where the real recording happens: at the studio in Batu Pahat with the Zenbook, or with the
   dancer at the desktop.
7. Make the arms/hands decision (see above).
8. Export the baked animation as FBX for Unity (the Quest app).

## References

Hamza, H. Z. (2023). Exploring the elements of main zapin: A preliminary reports and studies from fieldwork
in Johor. *Jurai Sembah, 4*(1), 9–20. https://doi.org/10.37134/juraisembah.vol4.1.2.2023

Kulozik, J., & Jarrassé, N. (2024). *Evaluating the precision of the HTC VIVE Ultimate Tracker with robotic
and human movements under varied environmental conditions* (arXiv:2409.01947). arXiv.
https://arxiv.org/abs/2409.01947

Md Nor, M. A. (2009). The spiritual essence of tawhid (oneness-peerlessness) in zapin dance performance by
the beholders of the Tariqat Naqsabandiah in Southeast Asia. *Jati, 14*, 33–40.

Rizhan, W., Mazian, A. I., Rahim, N., Jamal, A. A., Ismail, I., & Fadzli, S. A. (2025). Folk dance motion
templates based on motion capture technique for guaranteeing authenticity of folk dance: Case studies of
Malay folk dances. *Digital Applications in Archaeology and Cultural Heritage, 38*, Article e00449.

Setup facts come from HTC's VIVE Ultimate Tracker support pages and Meta's "Windows PC requirements for
Meta Horizon Link" page.
