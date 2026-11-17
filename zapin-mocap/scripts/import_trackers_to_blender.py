"""
import_trackers_to_blender.py
Loads a CSV from tracker_recorder.py into Blender as three animated markers
("empties"): one for the hip tracker and one for each ankle tracker.
Your avatar's skeleton can then be attached to these markers.

HOW TO USE (Blender 4.2 or newer)
1. Open your avatar's .blend file.
2. Go to the Scripting tab, then Text > Open, and pick this file.
3. Change the SETTINGS below (at least CSV_PATH).
4. Press the Run Script button (the triangle), or Alt+P with the mouse over the text.
5. Press Space in the 3D view to play. Three markers should move like your trackers did.

WHAT IT DOES
- Skips repeated frames (the ~1% timing quirk in the recordings).
- Optionally smooths out the short 1–3 frame jolts that happen when a foot lands.
- Re-samples the 90 Hz recording to your chosen frame rate (default 60 fps).
- Converts from SteamVR's axes (Y is up) to Blender's axes (Z is up).
- Lines the recording up with your scene (inside a helper marker called Mocap_Align):
    - moves the floor to height 0 (the tracking map's zero is about 30 cm below the real floor)
    - centres the dancer's starting spot at the world origin
    - turns the dancer to face Blender's front view, if you've told it which ankle is left
- Puts it all under Mocap_Root, which sits on the floor at the dancer's starting spot.
  Move, rotate or scale Mocap_Root to fit your avatar; the floor stays at height 0.
  Your recorded data itself stays untouched in the tracker markers.
- Takes from mocap_recorder.py: also imports the Quest headset and controllers (Quest_Head,
  Quest_Hand_L, Quest_Hand_R) and lines them up with the trackers automatically (Quest_Align).
- Adds timeline markers: "calibrate" (standing still), and "segment 1", "segment 2", ... where M was pressed.
- Importing a new take replaces the previous take's markers, so bake your avatar's
  animation (see attach_trackers_to_avatar.py) before loading the next take.
"""

import bpy
import csv
import math
import os
from mathutils import Vector, Quaternion, Matrix

# ============================== SETTINGS ==============================
CSV_PATH = r"C:\Users\radin\OneDrive\Desktop\Research Zapin\zapin-mocap\scripts\recordings\mocap_20261117_210455.csv"

FPS = 60                      # frame rate for the animation

# Which tracker was worn where. CHECK THIS FOR EVERY TAKE: the trackers get swapped around.
#   Oct 2026 takes:            waist 01032, left ankle 00986, right ankle 00213
#   mocap_20261117_210455:     waist 00986, left ankle 01032, right ankle 00213
# (worked out from the data: tracker heights, and which ankle is on the left controller's side)
HIP_SERIAL = "67-BH3N00986"
ANKLE_SERIALS = ["67-BH3N01032", "67-BH3N00213"]

# Once you've checked which ankle tracker is which (lift your left foot while
# tracker_positions.py runs), fill these in. Then the markers get L/R names and
# the dancer is turned to face Blender's front view automatically.
LEFT_ANKLE_SERIAL = "67-BH3N01032"
RIGHT_ANKLE_SERIAL = "67-BH3N00213"

# Seconds (start, end) where the dancer stands still, upright, looking ahead.
# None = the last 2 s of segment 0. Set it when segment 0 isn't a standing pose
# (in mocap_20261117_210455 the dancer is still sitting down then).
CALIBRATE_WINDOW_S = (33.5, 35.5)

# Quest devices in takes from mocap_recorder.py: CSV role -> marker name. Takes without them still work.
QUEST_ROLES = {"hmd": "Quest_Head", "left_controller": "Quest_Hand_L", "right_controller": "Quest_Hand_R"}

ANKLE_TRACKER_HEIGHT = 0.10   # metres from the floor to the middle of an ankle tracker when standing
SMOOTH = True                 # remove the short jolts at foot landings
SMOOTH_WINDOW = 5             # samples (5 = about 55 ms); must be an odd number
# =====================================================================

QC = Quaternion((1.0, 0.0, 0.0), math.radians(90.0))   # SteamVR (Y up) -> Blender (Z up)
QC_INV = QC.inverted()


def read_recording(path):
    """Return ({serial: list of (time, segment, (x,y,z), (w,x,y,z))}, {serial: role}), usable rows only."""
    data, roles = {}, {}
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            if r.get("valid", "1") == "0" or r.get("glitch", "0") == "1":
                continue
            roles[r["serial"]] = r.get("role", "")
            data.setdefault(r["serial"], []).append((
                float(r["time_s"]), int(r["segment"]),
                (float(r["x_m"]), float(r["y_m"]), float(r["z_m"])),
                (float(r["qw"]), float(r["qx"]), float(r["qy"]), float(r["qz"])),
            ))
    for s in data:
        data[s].sort(key=lambda row: row[0])
    return data, roles


def fit_quest(hmd, hip, root_matrix):
    """Turn + shift that puts the Quest's space onto the trackers' space.

    The two systems have their own origins and facing. While standing, the headset stays roughly
    above the waist tracker, so matching their paths across the floor gives the turn and shift.
    Both floors are already at height 0 (the Quest's own floor level is used as is).
    Returns (4x4 matrix, median leftover distance in metres).
    """
    hip_at = dict(zip(hip[0], hip[2]))
    pairs = [(q, root_matrix @ hip_at[t]) for t, q in zip(hmd[0], hmd[2]) if t in hip_at and q.z > 1.2]
    if len(pairs) < 100:
        raise ValueError("Too few standing frames with both the headset and the waist tracker to line them up.")
    qc = sum((q for q, _ in pairs), Vector()) / len(pairs)
    vc = sum((v for _, v in pairs), Vector()) / len(pairs)
    a = [(q - qc, v - vc) for q, v in pairs]
    yaw = math.atan2(sum(q.x * v.y - q.y * v.x for q, v in a), sum(q.x * v.x + q.y * v.y for q, v in a))
    rot = Matrix.Rotation(yaw, 4, "Z")
    shift = vc - rot @ qc
    m = Matrix.Translation((shift.x, shift.y, 0.0)) @ rot
    err = sorted(((m @ q) - v).to_2d().length for q, v in pairs)
    return m, err[len(err) // 2]


def drop_repeats(rows):
    out = [rows[0]]
    for row in rows[1:]:
        if row[2] != out[-1][2]:
            out.append(row)
    return out


def median_smooth(values, window):
    """Median filter on a list of floats (keeps sharp real moves, removes 1–2 sample spikes)."""
    h = window // 2
    n = len(values)
    out = []
    for i in range(n):
        w = sorted(values[max(0, i - h):min(n, i + h + 1)])
        out.append(w[len(w) // 2])
    return out


def to_blender(rows):
    """Convert positions and rotations to Blender axes; keep quaternion signs continuous."""
    times, segs, locs, quats = [], [], [], []
    prev = None
    for t, seg, p, q in rows:
        loc = QC @ Vector(p)
        quat = QC @ Quaternion(q) @ QC_INV
        quat.normalize()
        if prev is not None and prev.dot(quat) < 0:
            quat.negate()
        prev = quat
        times.append(t); segs.append(seg); locs.append(loc); quats.append(quat)
    return times, segs, locs, quats


def resample(times, locs, quats, t0, n_frames, fps):
    """Linear interpolation for positions, slerp for rotations, onto an even frame grid."""
    out_l, out_q = [], []
    j = 0
    for f in range(n_frames):
        t = t0 + f / fps
        while j < len(times) - 2 and times[j + 1] < t:
            j += 1
        ta, tb = times[j], times[j + 1]
        a = 0.0 if tb == ta else min(max((t - ta) / (tb - ta), 0.0), 1.0)
        out_l.append(locs[j].lerp(locs[j + 1], a))
        out_q.append(quats[j].slerp(quats[j + 1], a))
    return out_l, out_q


def get_fcurves(obj):
    ad = obj.animation_data
    try:
        return ad.action.fcurves                       # Blender 4.2 – 4.5
    except AttributeError:
        from bpy_extras import anim_utils              # Blender 5.x
        return anim_utils.action_get_channelbag_for_slot(ad.action, ad.action_slot).fcurves


def write_keys(obj, frames, locs, quats):
    """Fast keyframing: create the curves with one key, then fill in all keys at once."""
    obj.location = locs[0]
    obj.rotation_quaternion = quats[0]
    obj.keyframe_insert("location", frame=frames[0])
    obj.keyframe_insert("rotation_quaternion", frame=frames[0])
    for fc in get_fcurves(obj):
        if fc.data_path == "location":
            vals = [v[fc.array_index] for v in locs]
        elif fc.data_path == "rotation_quaternion":
            vals = [q[fc.array_index] for q in quats]
        else:
            continue
        kp = fc.keyframe_points
        kp.add(len(frames) - len(kp))
        co = [0.0] * (2 * len(frames))
        co[0::2] = frames
        co[1::2] = vals
        kp.foreach_set("co", co)
        fc.update()
    obj.animation_data.action.name = obj.name + "_Action"


def make_empty(name, collection, display, size, parent=None):
    obj = bpy.data.objects.new(name, None)
    obj.empty_display_type = display
    obj.empty_display_size = size
    obj.rotation_mode = "QUATERNION"
    collection.objects.link(obj)
    if parent is not None:
        obj.parent = parent           # no parent-inverse: child keys are relative to the root
    return obj


def main():
    path = bpy.path.abspath(CSV_PATH)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"CSV not found: {path}  (change CSV_PATH at the top of the script)")
    data, roles = read_recording(path)
    wanted = [HIP_SERIAL] + ANKLE_SERIALS
    missing = [s for s in wanted if s not in data]
    if missing:
        raise ValueError(f"These trackers aren't in the CSV: {missing}. Serials found: {list(data)}")
    quest = {s: QUEST_ROLES[r] for s, r in roles.items() if r in QUEST_ROLES}

    names = {HIP_SERIAL: "Tracker_Hip", **quest}
    for s in ANKLE_SERIALS:
        if s == LEFT_ANKLE_SERIAL:
            names[s] = "Tracker_Ankle_L"
        elif s == RIGHT_ANKLE_SERIAL:
            names[s] = "Tracker_Ankle_R"
        else:
            names[s] = "Tracker_Ankle_" + s[-5:]

    # --- clean and convert each tracker ---
    tracks = {}
    for s in wanted + list(quest):
        rows = drop_repeats(data[s])
        if SMOOTH and s not in quest and len(rows) > SMOOTH_WINDOW:   # the jolts are a VIVE thing
            for axis in range(3):
                col = median_smooth([r[2][axis] for r in rows], SMOOTH_WINDOW)
                rows = [(r[0], r[1], tuple(col[i] if k == axis else r[2][k] for k in range(3)), r[3])
                        for i, r in enumerate(rows)]
        tracks[s] = to_blender(rows)

    t0 = max(tr[0][0] for tr in tracks.values())
    t1 = min(tr[0][-1] for tr in tracks.values())
    n_frames = int((t1 - t0) * FPS) + 1
    frames = [float(f + 1) for f in range(n_frames)]

    # --- calibration window: standing still before dancing ---
    times, segs, _, _ = tracks[HIP_SERIAL]
    seg_starts = {}
    for tr in tracks.values():
        for t, sg in zip(tr[0], tr[1]):
            seg_starts[sg] = min(t, seg_starts.get(sg, t))
    if CALIBRATE_WINDOW_S:
        cal_a, cal_b = CALIBRATE_WINDOW_S
    elif len(seg_starts) >= 3:
        seg0_end = max(t for t, sg in zip(times, segs) if sg == 0)
        cal_a, cal_b = seg0_end - 2.0, seg0_end
    else:
        cal_a, cal_b = t0 + 1.0, t0 + 3.0

    def mean_loc(serial):
        ts, _, ls, _ = tracks[serial]
        pts = [l for t, l in zip(ts, ls) if cal_a <= t <= cal_b]
        return sum(pts, Vector()) / len(pts)

    ankle_cal = [mean_loc(s) for s in ANKLE_SERIALS]
    floor_z = min(p.z for p in ankle_cal) - ANKLE_TRACKER_HEIGHT
    mid = (ankle_cal[0] + ankle_cal[1]) / 2.0

    yaw = 0.0
    facing_done = False
    if LEFT_ANKLE_SERIAL in ANKLE_SERIALS and RIGHT_ANKLE_SERIAL in ANKLE_SERIALS:
        right_vec = mean_loc(RIGHT_ANKLE_SERIAL) - mean_loc(LEFT_ANKLE_SERIAL)
        # A character facing -Y has its LEFT side on +X, so left->right must point along -X.
        # (Was -atan2(...), which put left->right along +X and left the dancer facing +Y.)
        yaw = math.pi - math.atan2(right_vec.y, right_vec.x)
        facing_done = True

    root_matrix = (Matrix.Translation((0.0, 0.0, -floor_z))
                   @ Matrix.Rotation(yaw, 4, "Z")
                   @ Matrix.Translation((-mid.x, -mid.y, 0.0)))
    hmd = [s for s, n in quest.items() if n == "Quest_Head"]
    if quest and not hmd:
        raise ValueError("The take has Quest controllers but no headset, so they can't be lined up with the trackers.")
    if quest:
        quest_matrix, quest_err = fit_quest(tracks[hmd[0]], tracks[HIP_SERIAL], root_matrix)

    # --- remove a previously imported take (one take at a time keeps the names simple) ---
    old_prefixes = ("Mocap_Root", "Mocap_Align", "Quest_", "Tracker_Hip", "Tracker_Ankle_", "Target_", "Pole_")
    for obj in list(bpy.data.objects):
        if obj.name.startswith(old_prefixes) and obj.type == "EMPTY":
            bpy.data.objects.remove(obj, do_unlink=True)
    for c in list(bpy.data.collections):
        if c.name.startswith("Mocap_") and len(c.all_objects) == 0:
            bpy.data.collections.remove(c)

    # --- build the scene objects ---
    take = os.path.splitext(os.path.basename(path))[0]
    coll = bpy.data.collections.new("Mocap_" + take)
    bpy.context.scene.collection.children.link(coll)
    root = make_empty("Mocap_Root", coll, "PLAIN_AXES", 0.4)
    root.rotation_mode = "XYZ"                       # stays at the world origin; yours to adjust
    align = make_empty("Mocap_Align", coll, "PLAIN_AXES", 0.05, parent=root)
    align.rotation_mode = "XYZ"
    align.matrix_basis = root_matrix                 # fixed: floor to 0, start spot to centre, facing
    align.hide_viewport = True
    if quest:
        q_align = make_empty("Quest_Align", coll, "PLAIN_AXES", 0.05, parent=root)
        q_align.rotation_mode = "XYZ"
        q_align.matrix_basis = quest_matrix           # fixed: Quest space onto the trackers' space
        q_align.hide_viewport = True

    for s in wanted + list(quest):
        times, _, locs, quats = tracks[s]
        rl, rq = resample(times, locs, quats, t0, n_frames, FPS)
        size = 0.15 if s == HIP_SERIAL else 0.10
        obj = make_empty(names[s], coll, "ARROWS", size, parent=q_align if s in quest else align)
        write_keys(obj, frames, rl, rq)

    # --- scene settings and timeline markers ---
    scene = bpy.context.scene
    scene.render.fps = FPS
    scene.render.fps_base = 1.0
    scene.frame_start = 1
    scene.frame_end = n_frames
    to_frame = lambda t: int(round((t - t0) * FPS)) + 1
    cal_frame = to_frame((cal_a + cal_b) / 2.0)
    for m in [m for m in scene.timeline_markers
              if m.name in ("calibrate", "dance start", "dance end") or m.name.startswith("segment ")]:
        scene.timeline_markers.remove(m)          # the previous take's markers
    scene.timeline_markers.new("calibrate", frame=cal_frame)
    for sg, t in sorted(seg_starts.items()):
        if sg > 0 and t0 <= t <= t1:
            scene.timeline_markers.new(f"segment {sg}", frame=to_frame(t))
    scene.frame_set(cal_frame)

    print("\n=== Tracker import finished ===")
    print(f"File: {path}")
    print(f"Frames: {n_frames} at {FPS} fps ({n_frames / FPS:.1f} s)")
    print(f"Markers: {', '.join(names[s] for s in wanted + list(quest))} (under Mocap_Root)")
    if quest:
        print(f"Quest lined up with the trackers: headset path vs waist path differ by {quest_err * 100:.1f} cm "
              f"(median). Over about 15 cm, check the line-up.")
    print(f"Moved everything {'down' if floor_z > 0 else 'up'} by {abs(floor_z) * 100:.1f} cm so the floor is at height 0")
    print(f"Calibrate frame: {cal_frame} (you're standing still here)")
    print("Facing: turned to face Blender's front view" if facing_done
          else "Facing: not changed (set LEFT_/RIGHT_ANKLE_SERIAL to fix the direction automatically)")
    if SMOOTH:
        print(f"Smoothing: on ({SMOOTH_WINDOW}-sample median)")


main()
