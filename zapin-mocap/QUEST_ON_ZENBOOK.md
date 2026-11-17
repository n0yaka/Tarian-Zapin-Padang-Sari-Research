# Quest 3S in SteamVR on the Zenbook (ARM): setup and first controller test

Session notes from 11–12 Nov 2026 (Claude Code on the Zenbook A14). Companion to `CLAUDE.md`, which
covers the VIVE tracker pipeline. Written to be pasted into a Claude chat for further analysis.

## Summary

- **Goal:** get the Meta Quest 3S working as a SteamVR headset on the Zenbook A14 (Snapdragon X,
  Windows 11 on ARM), so the Quest controllers can be recorded like the VIVE Ultimate Trackers.
- **Outcome:** working, through **ALVR** over a USB cable. Headset, both controllers and hand tracking
  all appear in SteamVR, with a usable picture at 72 fps.
- **Meta Horizon Link and Steam Link do not work on this laptop.** ALVR with software encoding does.
- **First controller recording:** tracked cleanly for 51.6 s, but the take cannot give a
  return-to-start accuracy figure because the still periods were not still.

## Hardware and software

| Item | Details |
|---|---|
| Laptop | ASUS Zenbook A14 UX3407QA, Snapdragon X (X1-26-100, 8 cores), Adreno X1-45 GPU, 32 GB RAM |
| Graphics driver | 31.0.137.0, dated 1 Aug 2026 |
| Headset | Meta Quest 3S, connected by USB, developer mode on |
| SteamVR | 2.18.4 (runs as Intel/AMD code, translated by Windows) |
| ALVR | v20.14.1, streamer on the laptop and the matching app on the Quest |
| Python | x64 Python via the `py` launcher, with the `openvr` package |

## What was tried

| Route | Result | Reason |
|---|---|---|
| Meta Horizon Link | ❌ Not attempted beyond the installer | Built for Intel/AMD PCs with NVIDIA or AMD graphics; no Snapdragon support found in Meta's requirements |
| Steam Link (Quest app) | ❌ Refused to connect | Message: "Streaming VR requires HEVC hardware encoding and updated video drivers". The laptop does have a Qualcomm hardware HEVC encoder, but Steam Link does not accept it |
| ALVR, default settings | ❌ Dark screen | ALVR looked for an NVIDIA or AMD encoder, found none, and sent no video |
| ALVR, software encoding on | ❌ Dark screen | "Force software decoder" had also been switched on; the Quest has no such decoder (`no such codec: OMX.google.h264.decoder`) |
| ALVR, software **encoding** on, software **decoder** off | ✅ Works | The laptop's processor encodes the video; the Quest decodes it with its normal hardware decoder |
| Virtual Desktop | Not tried | Paid; no confirmation it supports Snapdragon |

## Working setup

### Where things are

| Item | Location |
|---|---|
| ALVR streamer | `C:\Users\ASUS\ALVR\ALVR Dashboard.exe` |
| ALVR settings file | `C:\Users\ASUS\ALVR\session.json` (do not edit while ALVR or SteamVR is running) |
| ALVR headset app installer | `C:\Users\ASUS\ALVR\download\alvr_client_android.apk` |
| ADB (for installing apps on the Quest) | `%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe` (from Android Studio) |
| Controller recorder | `zapin-mocap\scripts\controller_recorder.py` |
| New recordings | `zapin-mocap\scripts\recordings\` |

### ALVR settings that matter (Settings > Video)

| Setting | Value | Why |
|---|---|---|
| Force software encoding | **On** | The only encoder ALVR can use on this laptop |
| Force software decoder | **Off** | Turning it on breaks the picture |
| Preferred codec | H264 | The only codec software encoding supports |
| Transcoding view resolution | Scale 0.5 (832 × 864 per eye) when measured | Picture is a bit blurry at this value |
| Bitrate | Constant 30 Mbps when measured | |
| Foveated encoding | On | Keeps the centre sharp, saves processor time |
| Refresh rate | 72 Hz | |

### Starting a session

1. Plug the Quest into the laptop by USB.
2. Start `ALVR Dashboard.exe`. It should show the headset as a wired connection.
3. Put the headset on and open ALVR (Library > Unknown Sources).
4. Press Launch SteamVR in the dashboard.
5. SteamVR should show the headset, two controllers and two hands.

### Measured stream performance (12 s sample, SteamVR's empty environment)

| Measure | Value |
|---|---|
| Frame rate, laptop / headset | 73 / 71–72 fps |
| Encoding time per frame | about 6–7 ms (13.9 ms available at 72 Hz) |
| Total delay | about 38 ms |
| Lost packets | 0 |

- **Suggested to reduce blur, not yet confirmed as applied:** resolution scale 0.65 and bitrate 50 Mbps,
  then 0.75 if it stays smooth. Re-measure after changing.
- **Headroom will be smaller in a real app**, because the laptop's graphics chip also has to draw it.

## Things confirmed along the way

- **ADB works on the ARM laptop.** The Quest 3S is seen and authorised over USB. This was listed as
  untested in `CLAUDE.md`.
- **SteamVR's no-headset (null driver) edits had been reset**, probably by a SteamVR update:
  `requireHmd` was `true` and `forcedDriver` was empty. That is correct for using the Quest. The edits
  must be re-applied for tracker-only recording, and removed again to use the Quest.
- **SteamVR lists the Quest as a Quest 2 ("Miramar").** That is ALVR's default, not a fault.
- **The Quest tracks controllers or bare hands, not both.** Holding the controllers switches hand
  tracking off, and the other pair shows as disconnected.
- **Frozen data is reported as "OK".** When the headset is idle or off the head, SteamVR keeps
  reporting the last position as valid. The recorder detects this and labels it FROZEN.

## controller_recorder.py

Sister script of `tracker_recorder.py`.

- **Run:** `py controller_recorder.py <take_name> [--hz 90] [--seconds N] [--no-hmd]`
- **Keys:** M starts a new segment; Ctrl+C stops and saves.
- **Records:** headset, left and right controller, left and right hand.
- **CSV columns:** the same 18 as the tracker recorder, in the same order, plus `role` at the end
  (`hmd`, `left_controller`, `right_controller`, `left_hand`, `right_hand`).
- **Summary improvements over the tracker recorder:**
  - return-to-start error uses the average of the last 2 s of the first and last segments
  - top speed is worked out from positions
  - reports tracked percentage, dropout count and longest dropout
  - reports how many rows were repeats, and flags frozen streams
- **Needs:** the headset on the head with ALVR streaming, otherwise everything reads FROZEN.

## First controller take: hand_test_1 (12 Nov 2026, 13:39)

File: `zapin-mocap\scripts\recordings\hand_test_1_20261112_133923.csv`

| Item | Value |
|---|---|
| Length | 51.6 s, 4648 samples, 90.0 per second, no gap over 17 ms |
| Segments | 0 = 5.4 s, 1 = 31.9 s, 2 = 14.3 s |

| Measure | Headset | Left controller | Right controller |
|---|---|---|---|
| Tracked | 100% | 98.8% | 98.0% |
| Dropouts mid-take | 0 | 0 | 0 |
| Longest gap between new positions | 22 ms | 66 ms | 55 ms |
| Jumps over 3 cm off a 9-sample median path | 0 | 0 | 0 |
| Top speed, from positions | 0.57 m/s | 2.18 m/s | 2.35 m/s |
| Top speed, SteamVR's velocity | 0 (not reported) | 2.17 m/s | 2.36 m/s |
| Height range | 1.12–1.32 m | 0.48–1.66 m | 0.48–1.71 m |
| Furthest from headset | – | 1.03 m | 0.98 m |
| Hand travel in segments 0 / 1 / 2 | 0.5 / 3.6 / 1.7 m | 0.5 / 20.5 / 10.7 m | 0.8 / 21.2 / 12.5 m |

- **Lost time is only at the edges:** the first 0.6 s (switch from hands to controllers) and the last
  0.1–0.4 s.
- **Controller velocity agrees with position-derived speed**, unlike the VIVE trackers, where the
  velocity columns under-read.

### Limits of this take

- **Return-to-start error is not usable.** The script gives 14.7 cm (left) and 8.0 cm (right), but the
  headset ended 23.5 cm from its start, and the right controller moved up to 51 cm inside the final
  "still" window. The hands were still moving in segment 2.
- **Out-of-view behaviour was not tested**, as far as the data shows. The controllers stayed within
  about 1 m of the headset and never went lost.
- **"Tracked" may overstate it.** The Quest is believed to keep estimating a controller's position from
  its motion sensors for a short while after the cameras lose it, still labelled OK. Not verified here.
- **The true measurement rate is unknown.** A new position arrives on every 90 Hz poll even though the
  stream runs at 72 Hz, which points to positions being predicted between real updates.
- **Headset height of 1.12–1.32 m** suggests the wearer was seated, or the floor level is set too high.
- **Not comparable with tracker takes yet.** Controller positions are in the Quest's own space, not
  the VIVE tracking map.

## Second controller take: headset on a stand (13 Nov 2026, 12:20)

File: `zapin-mocap\scripts\recordings\hand_test_1_20261113_122053.csv`

Setup: headset not worn, plugged in, cameras facing the person, who moved up to about 1 m away holding
both controllers. 66.0 s at 90.0 samples per second; segments 0 = 5.0 s, 1 = 32.6 s, 2 = 26.9 s,
3 = 1.4 s.

| Period | Controllers, as seen from the headset | Impossible jumps (> 6 m/s) |
|---|---|---|
| 0–10 s | 0.4–1.6 m in front, within about 40° of the camera centre | 0 |
| 10–54.8 s | Much of the time reported 0–0.6 m *behind* or beside the headset | 321 (left), 221 (right) |
| 54.8 s to end | Both controllers disconnected at the same moment | – |

- **Headset stayed still:** it moved under 1 cm in total, so it was a fixed reference.
- **Tracking was clean for the first 10 s,** then broke when the controllers reached about 1.5–1.8 m
  in front, and never recovered.
- **After that the positions are not usable:** jumps of up to 109 cm in 11 ms, implied speeds up to
  29 m/s, and positions behind the headset while the person was in front of it (unless they walked
  around it, which was not confirmed). SteamVR still reported OK the whole time.
- **SteamVR's velocity did not show the jumps** (max 4.6–4.9 m/s), so it is a poor glitch detector here.
- **Likely explanation (not verified):** once the cameras lose the controllers, the Quest falls back to
  a body/arm model that places them near where a wearer's hands would be, close to the headset.
- **Return-to-start error (88 cm) is meaningless** for this take.
- **Conclusion:** a headset on a stand tracks controllers only close in front of it, roughly within
  1.2 m and 40°. Not suitable for dance capture. Wrist-mounted Ultimate Trackers remain the better
  option for the arms.

## Suggested next controller test

1. Rest both controllers on taped marks on a table. Press M, pick them up and move, put them back on
   the marks, press M, wait 5 s.
2. Stand still on a floor mark, so head movement does not mix into the hand numbers.
3. Break line of sight on purpose: hands behind the back, above the head, and down by the hips as in a
   kopak squat, each for a few seconds in its own segment.
4. Repeat 3–5 times and report every take.

## Open items

1. Raise the ALVR resolution scale and bitrate, then re-measure frame rate and encoding time.
2. Run the improved controller test above.
3. Test the VIVE trackers and the Quest together in one SteamVR session. They are two tracking systems
   and need aligning (VIVE Space Calibrator was the plan on the desktop). Not tried on the Zenbook.
4. Decide how to switch between tracker-only recording (null driver) and Quest recording, since both
   need different SteamVR settings.
5. Update the "What runs where" table in `CLAUDE.md`: Quest in SteamVR and ADB now work on the Zenbook.
6. `Setup.exe` in the `VSCode` folder is the Oculus installer and can be deleted.
